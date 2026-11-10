"""Neat Apps Launcher: Flask + Socket.IO app."""
from __future__ import annotations

import hashlib
import os
import subprocess
import threading
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request, send_file, url_for
from flask_socketio import SocketIO, join_room, leave_room

import registry
import sysinfo
import updates
from procman import strip_ansi
from service import Launcher
from terminal import Session

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
VERSION = (ROOT / "VERSION").read_text().strip() if (ROOT / "VERSION").exists() else "dev"
LAUNCHER_REPO = os.environ.get("NEAT_LAUNCHER_REPO", "dotimothy/neat-apps-launcher")
LAUNCHER_BRANCH = os.environ.get("NEAT_LAUNCHER_BRANCH", "main")


def create_app(settings=None, apps=None, logs_dir=None, cache_dir=None, runs_dir=None,
               start_monitor=True):
    app = Flask(__name__, static_folder=str(HERE / "static"),
                template_folder=str(HERE / "templates"))
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 300
    sio = SocketIO(app, async_mode="threading", ping_interval=20, ping_timeout=30)

    if settings is None or apps is None:
        settings, apps = registry.load()
    cache = Path(cache_dir or os.environ.get("NEAT_LAUNCHER_CACHE") or (ROOT / ".cache"))
    svc = Launcher(settings, apps, logs_dir=Path(logs_dir or (ROOT / "logs")), cache_dir=cache,
                   runs_dir=Path(runs_dir or (ROOT / "runs")), emit=sio.emit)
    mgr = svc.mgr
    terms: dict[str, Session] = {}
    launcher_job = {"running": False, "log": ""}
    units = set(mgr.units)

    def info():
        return sysinfo.collect(str(settings.install_root) if settings.install_root.exists() else "/")

    # Static links carry a stamp of the file's content, so a browser never runs
    # a cached launcher.js / launcher.css after an update.
    stamps: dict[str, tuple[float, str]] = {}

    def asset(name: str) -> str:
        path = HERE / "static" / name
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return url_for("static", filename=name)
        hit = stamps.get(name)
        if not hit or hit[0] != mtime:
            hit = (mtime, hashlib.sha1(path.read_bytes()).hexdigest()[:10])
            stamps[name] = hit
        return url_for("static", filename=name, v=hit[1])

    app.jinja_env.globals["asset"] = asset

    def err(msg, code=400):
        return jsonify({"ok": False, "error": str(msg)}), code

    def ok(**kw):
        return jsonify({"ok": True, **kw})

    # -- pages ---------------------------------------------------------------
    @app.get("/")
    def index():
        cats: dict[str, int] = {}
        for a in apps:
            cats[a.category] = cats.get(a.category, 0) + 1
        resp = app.make_response(render_template(
            "index.html", version=VERSION, app_count=len(apps), categories=cats,
            catalog=settings.catalog_source))
        resp.headers["Cache-Control"] = "no-cache"      # always pick up new asset stamps
        return resp

    @app.get("/health")
    def health():
        return jsonify({"ok": True, "app": "neat-apps-launcher", "version": VERSION})

    # -- apps ----------------------------------------------------------------
    @app.get("/api/apps")
    def api_apps():
        return jsonify({"apps": svc.apps_payload()})

    @app.get("/api/apps/<aid>")
    def api_app(aid):
        if aid not in svc.apps:
            return err("unknown app", 404)
        return jsonify({**svc.apps[aid].public(), **svc.app_snapshot(aid)})

    @app.post("/api/apps/<aid>/<action>")
    def api_action(aid, action):
        if aid not in svc.apps:
            return err("unknown app", 404)
        body = request.get_json(silent=True) or {}
        try:
            if action == "run":
                svc.launch_app(aid, body)
            elif action == "stop":
                svc.stop_app(aid)
            elif action == "kill":
                svc.stop_app(aid, force=True)
            elif action == "download":
                svc.download_models(aid, body.get("models"))
            elif action == "delete-model":
                svc.delete_model(aid, body.get("model", ""))
            elif action == "prefs":
                svc.save_prefs(aid, {k: body.get(k) for k in ("lang", "models", "streams", "input", "urls")})
                mgr.push()
            elif action == "cancel":
                mgr.cancel_job(aid)
            elif action == "dismiss":
                mgr.clear(aid)
            else:
                return err("unknown action", 404)
        except Exception as e:                                 # noqa: BLE001
            return err(e, 409)
        return ok(app=svc.app_snapshot(aid))

    @app.get("/api/apps/<aid>/config")
    def api_config(aid):
        if aid not in svc.apps:
            return err("unknown app", 404)
        try:
            text = svc.preview_config(aid, {k: request.args.get(k) for k in ("streams", "input")
                                            if request.args.get(k)})
        except Exception as e:                                 # noqa: BLE001
            return err(e, 409)
        return jsonify({"text": text, "custom": svc.custom_config_path(aid).exists(),
                        "default": svc.apps[aid].config_path().read_text()
                        if svc.apps[aid].config_path().exists() else ""})

    @app.post("/api/apps/<aid>/config")
    def api_config_save(aid):
        if aid not in svc.apps:
            return err("unknown app", 404)
        body = request.get_json(silent=True) or {}
        path = svc.custom_config_path(aid)
        if body.get("reset"):
            path.unlink(missing_ok=True)
            mgr.push()
            return ok()
        import yaml
        text = str(body.get("text", ""))
        try:
            if not isinstance(yaml.safe_load(text), dict):
                raise ValueError("the config must be a YAML mapping")
        except Exception as e:                                 # noqa: BLE001
            return err(f"not valid YAML: {e}")
        path.write_text(text)
        mgr.push()
        return ok()

    @app.get("/api/apps/<aid>/outputs")
    def api_outputs(aid):
        if aid not in svc.apps:
            return err("unknown app", 404)
        return jsonify(svc.outputs(aid))

    @app.get("/api/apps/<aid>/output/<path:rel>")
    def api_output_file(aid, rel):
        if aid not in svc.apps:
            return err("unknown app", 404)
        p = svc.output_file(aid, rel)
        if p is None:
            return err("not found", 404)
        return send_file(p, max_age=0)

    @app.get("/api/apps/<aid>/viewer")
    def api_viewer(aid):
        if aid not in svc.apps:
            return err("unknown app", 404)
        return jsonify(svc.viewer(aid))

    @app.get("/api/logs/<uid>")
    def api_log(uid):
        if uid not in units:
            return err("unknown", 404)
        text = strip_ansi(mgr.rt[uid].log.text()).replace("\r\n", "\n")
        return Response(text, mimetype="text/plain", headers={
            "Content-Disposition": f'attachment; filename="{uid}.log"'})

    @app.post("/api/logs/<uid>/clear")
    def api_log_clear(uid):
        if uid not in units:
            return err("unknown", 404)
        mgr.rt[uid].log.clear()
        return ok()

    # -- platform / insight -------------------------------------------------------
    @app.get("/api/platform")
    def api_platform():
        if request.args.get("force"):
            svc.platform.forget()
            return jsonify(svc.platform_status(max_age=0))
        return jsonify(svc.platform_status())

    @app.post("/api/platform/<action>")
    def api_platform_action(action):
        body = request.get_json(silent=True) or {}
        try:
            if action == "setup":
                svc.setup_all(upgrade=bool(body.get("upgrade")), sudo_password=body.get("sudo_password") or None)
            elif action == "samples":
                svc.fetch_samples()
            elif action == "install":
                svc.install(str(body.get("what", "")), sudo_password=body.get("sudo_password") or None)
            elif action == "cancel":
                mgr.cancel_job("platform")
            elif action == "dismiss":
                mgr.clear("platform")
            elif action == "stop-all":
                svc.stop_all()
            else:
                return err("unknown action", 404)
        except Exception as e:                                 # noqa: BLE001
            return err(e, 409)
        return ok()

    @app.post("/api/insight/<action>")
    def api_insight(action):
        try:
            if action == "start":
                svc.start_insight()
            elif action == "stop":
                svc.stop_insight()
            elif action == "restart":
                def work():
                    mgr.stop("insight", wait=True)
                    svc.start_insight()
                threading.Thread(target=work, daemon=True).start()
            elif action == "dismiss":
                mgr.clear("insight")
            else:
                return err("unknown action", 404)
        except Exception as e:                                 # noqa: BLE001
            return err(e, 409)
        return ok()

    @app.get("/api/insight/streams")
    def api_insight_streams():
        try:
            srcs = [s for s in svc.insight.sources() if s.get("file")]
            return jsonify({"sources": srcs, "ingest": svc.insight.ingest()})
        except Exception as e:                                 # noqa: BLE001
            return err(e, 502)

    @app.get("/api/updates")
    def api_updates():
        if request.args.get("force"):
            updates.forget()
            svc.platform.forget()
        return jsonify({"launcher": updates.check(LAUNCHER_REPO, LAUNCHER_BRANCH, ROOT),
                        "bundle": svc.platform_status(max_age=0)["bundle"]})

    @app.post("/api/procs/<int:pid>/kill")
    def api_kill_pid(pid):
        try:
            mgr.kill_pid(pid)
        except Exception as e:                                 # noqa: BLE001
            return err(e, 409)
        return ok()

    # -- system ----------------------------------------------------------------
    @app.get("/api/system")
    def api_system():
        return jsonify({**info(), "version": VERSION})

    @app.get("/api/system/mla")
    def api_mla():
        from procman import legacy_mla_service, mla_users
        return jsonify({"legacy_service": legacy_mla_service(), "users": mla_users()})

    @app.post("/api/system/reset-mla")
    def api_reset_mla():
        try:
            r = mgr.reset_mla()
        except Exception as e:                                 # noqa: BLE001
            return err(f"MLA reset failed: {e}", 500)
        return jsonify({"ok": True, **r})

    @app.post("/api/launcher/update")
    def api_launcher_update():
        if launcher_job["running"]:
            return err("an update is already running", 409)
        launcher_job.update(running=True, log="")

        def work():
            r = subprocess.run(["bash", str(ROOT / "run.sh"), "update"], cwd=str(ROOT),
                               capture_output=True, text=True, stdin=subprocess.DEVNULL)
            launcher_job.update(running=False, log=strip_ansi(r.stdout + r.stderr)[-4000:],
                                code=r.returncode)
            sio.emit("launcher_updated", {"code": r.returncode, "log": launcher_job["log"]})
        threading.Thread(target=work, daemon=True).start()
        return ok()

    # -- sockets ---------------------------------------------------------------
    @sio.on("connect")
    def on_connect():
        # The page compares this with its own script stamp and reloads if it is stale.
        sio.emit("hello", {"asset": asset("launcher.js").rsplit("v=", 1)[-1], "version": VERSION},
                 to=request.sid)
        sio.emit("apps", [{"id": aid, **svc.app_snapshot(aid)} for aid in svc.order], to=request.sid)
        sio.emit("platform", svc.platform_status(), to=request.sid)
        sio.emit("system", info(), to=request.sid)

    @sio.on("subscribe_log")
    def on_sub(data):
        uid = (data or {}).get("id")
        if uid in units:
            join_room(f"log:{uid}")
            sio.emit("log_init", {"id": uid, "data": mgr.rt[uid].log.text()}, to=request.sid)

    @sio.on("unsubscribe_log")
    def on_unsub(data):
        uid = (data or {}).get("id")
        if uid in units:
            leave_room(f"log:{uid}")

    @sio.on("open", namespace="/term")
    def term_open(data):
        sid = request.sid
        old = terms.pop(sid, None)
        if old:
            old.close()
        data = data or {}

        def out(text, sid=sid):
            sio.emit("output", text, to=sid, namespace="/term")

        def done(code, sid=sid):
            terms.pop(sid, None)
            sio.emit("exit", {"code": code}, to=sid, namespace="/term")

        cwd = data.get("cwd")
        if cwd in svc.apps:
            cwd = str(svc.apps[cwd].dir)
        elif cwd == "bundle":
            cwd = str(settings.bundle)
        if not cwd or not os.path.isdir(cwd):
            cwd = None
        terms[sid] = Session(out, done, cols=int(data.get("cols", 120)),
                             rows=int(data.get("rows", 32)), cwd=cwd)

    @sio.on("input", namespace="/term")
    def term_input(data):
        t = terms.get(request.sid)
        if not t:
            return
        if isinstance(data, dict):
            t.write_seq(int(data.get("s", 0)), str(data.get("d", "")))
        elif isinstance(data, str):
            t.write(data)

    @sio.on("resize", namespace="/term")
    def term_resize(data):
        t = terms.get(request.sid)
        if t and data:
            t.resize(int(data.get("cols", 120)), int(data.get("rows", 32)))

    @sio.on("disconnect", namespace="/term")
    def term_disconnect(*_):
        t = terms.pop(request.sid, None)
        if t:
            t.close()

    if start_monitor:
        mgr.start(sysinfo=info)
    app.extensions["launcher"] = {"service": svc, "manager": mgr, "socketio": sio, "terms": terms}
    return app, sio
