"""What the web app calls: app states and actions, Insight, platform setup.

An app run goes like this (Manager.launch runs it in a thread):
  1. stop other MLA apps (unless "run alongside")
  2. prepare: start Insight if the app streams to it, start the sample (or
     app-specific) streams, pick free Insight channels, write
     runs/<app>/config.yaml with everything filled in
  3. a bash script in the bundle root: download missing models, install the
     Python requirements into pyneat (once), validate the config when the app
     supports it, then exec the app.
"""
from __future__ import annotations

import hashlib
import json
import shlex
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Callable

import yaml

import models
import runconfig
from bundle import Platform
from insight import Insight, InsightError
from procman import Manager, SimpleUnit
from registry import App, Settings
from samples import SampleImages

IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp", ".bmp")
MAX_CHANNELS = 80


class Launcher:
    def __init__(self, settings: Settings, apps: list[App], *, logs_dir: Path, cache_dir: Path,
                 runs_dir: Path, emit: Callable[..., None] | None = None):
        self.s = settings
        self.apps = {a.id: a for a in apps}
        self.order = [a.id for a in apps]
        self.emit = emit or (lambda *a, **k: None)
        self.runs = Path(runs_dir)
        self.runs.mkdir(parents=True, exist_ok=True)
        self.insight = Insight(settings, cache_dir)
        self.images = SampleImages(settings, cache_dir)
        self.platform = Platform(settings, self.insight, self.images)
        self.insight_unit = SimpleUnit("insight", "Neat Insight", [str(self.insight.binary)])
        self.platform_unit = SimpleUnit("platform", "Platform")
        self.mgr = Manager([*apps, self.insight_unit, self.platform_unit], logs_dir, emit=self.emit)
        self.mgr.health["insight"] = self.insight.healthy
        self.mgr.on_push = self.push
        for a in apps:
            self.mgr.on_exit[a.id] = (lambda code, aid=a.id: self._app_exited(aid, code))
        self._pstatus: tuple[float, dict] = (0.0, {})
        self._plock = threading.Lock()

    # -- per-app preferences ----------------------------------------------------
    def run_dir(self, aid: str) -> Path:
        d = self.runs / aid
        d.mkdir(parents=True, exist_ok=True)
        return d

    def prefs(self, aid: str) -> dict:
        try:
            return json.loads((self.run_dir(aid) / "prefs.json").read_text())
        except Exception:                                          # noqa: BLE001
            return {}

    def save_prefs(self, aid: str, upd: dict) -> dict:
        p = {**self.prefs(aid), **{k: v for k, v in upd.items() if v is not None}}
        (self.run_dir(aid) / "prefs.json").write_text(json.dumps(p, indent=1))
        return p

    def custom_config_path(self, aid: str) -> Path:
        return self.run_dir(aid) / "custom.yaml"

    # -- state ---------------------------------------------------------------
    def chosen_models(self, a: App, prefs: dict | None = None) -> list[dict]:
        prefs = prefs if prefs is not None else self.prefs(a.id)
        return models.selected(a, prefs.get("models"))

    def app_state(self, a: App) -> str:
        if a.link:
            return "external"
        base = self.mgr.base_state(a.id)
        if base.startswith("job:"):
            return "downloading" if base == "job:download" else "preparing"
        if base in ("running", "stopping", "starting"):
            return base
        if base == "error":
            return "error"
        if not a.is_installed():
            return "not_installed"
        if models.missing(self.s, self.chosen_models(a)):
            return "needs_models"
        return "finished" if base == "finished" else "ready"

    def app_snapshot(self, aid: str) -> dict:
        a = self.apps[aid]
        snap = self.mgr.snapshot(aid)
        prefs = self.prefs(aid)
        out_dir = self.run_dir(aid) / "output"
        n_out = sum(1 for p in out_dir.rglob("*") if p.is_file()) if out_dir.is_dir() else 0
        return {**snap, "state": self.app_state(a), "installed": a.is_installed(),
                "available_languages": a.available_languages(), "prefs": prefs,
                "models_status": models.status(self.s, a),
                "chosen_models": [m["id"] for m in self.chosen_models(a, prefs)],
                "outputs": n_out, "custom_config": self.custom_config_path(aid).exists(),
                "sample_set": self.images.describe(a.sample_images) if a.sample_images else None}

    def apps_payload(self) -> list[dict]:
        return [{**self.apps[aid].public(), **self.app_snapshot(aid)} for aid in self.order]

    def platform_status(self, max_age: float = 10.0) -> dict:
        with self._plock:
            t, st = self._pstatus
            if time.time() - t > max_age or not st:
                st = self.platform.status()
                self._pstatus = (time.time(), st)
        ins = self.mgr.snapshot("insight")
        plat = self.mgr.snapshot("platform")
        return {**st, "insight": {**st["insight"], "running": self.mgr.is_running("insight"),
                                  "healthy": self.mgr.rt["insight"].healthy, "base": ins["base"],
                                  "procs": ins["procs"], "error": ins["error"]},
                "job": plat["job"], "job_base": plat["base"], "job_error": plat["error"]}

    def push(self) -> None:
        self.emit("apps", [{"id": aid, **self.app_snapshot(aid)} for aid in self.order])
        self.emit("platform", self.platform_status())

    # -- platform ----------------------------------------------------------------
    def setup_all(self, upgrade: bool = False) -> None:
        script = self.platform.setup_script(upgrade=upgrade)

        def after(code: int) -> None:
            self.platform.forget()
            self._pstatus = (0.0, {})
            if code == 0 and self.insight.installed() and not self.mgr.is_running("insight"):
                self.start_insight()
        self.mgr.run_job("platform", "setup", script, self.s.install_root
                         if self.s.install_root.is_dir() else Path.home(), after=after,
                         title="Upgrade apps bundle" if upgrade else "Set up everything")

    def install(self, what: str) -> None:
        """One prerequisite on its own: sima-cli, Neat Core, or the ~/pyneat link."""
        scripts = {"sima-cli": (self.platform.sima_cli_script, "Install sima-cli"),
                   "core": (self.platform.core_script, "Install Neat Core"),
                   "pyneat": (self.platform.pyneat_link_script, "Link the pyneat environment")}
        if what not in scripts:
            raise RuntimeError(f"nothing called {what!r} to install")
        make, title = scripts[what]

        def after(code: int) -> None:
            self.platform.forget()
            self._pstatus = (0.0, {})
        self.mgr.run_job("platform", what, make(), Path.home(), after=after, title=title)

    def fetch_samples(self) -> None:
        self.mgr.run_job("platform", "samples", self.insight.fetch_samples_script() + self.images.fetch_script(),
                         Path.home(), title="Get sample videos and images")

    def start_insight(self) -> None:
        if not self.insight.installed():
            raise RuntimeError("Neat Insight is not installed: run Set Up Everything first")
        if self.mgr.is_running("insight"):
            return
        self.mgr.launch("insight", self.insight.launch_command(), Path.home(),
                        exclusive=False, title="Starting")

    def stop_insight(self) -> None:
        self.mgr.stop("insight")

    def _ensure_insight(self, log) -> None:
        if not self.mgr.is_running("insight") and not self.insight.healthy():
            log("Starting Neat Insight")
            self.start_insight()
            time.sleep(1.0)
        if not self.insight.wait_healthy(90):
            raise RuntimeError(f"Neat Insight did not come up on port {self.s.insight_port}: "
                               "see the Insight log")

    # -- models --------------------------------------------------------------------
    def download_models(self, aid: str, ids: list[str] | None = None) -> None:
        a = self.apps[aid]
        want = models.selected(a, ids) if ids else self.chosen_models(a)
        if not want:
            raise RuntimeError("this app has no models to download")
        self.mgr.run_job(aid, "download", models.download_script(self.s, want), Path.home(),
                         title="Download models")

    def delete_model(self, aid: str, mid: str) -> None:
        a = self.apps[aid]
        m = a.model(mid)
        if not m or not m.get("file"):
            raise RuntimeError("unknown model")
        if self.mgr.is_running(aid):
            raise RuntimeError("stop the app first")
        p = models.path_of(self.s, m)
        if p.parent.resolve() != self.s.models_dir.resolve():
            raise RuntimeError("refusing to remove a file outside the models folder")
        p.unlink(missing_ok=True)
        models.stamp_of(self.s, m).unlink(missing_ok=True)
        self.push()

    # -- run -----------------------------------------------------------------------
    def _used_channels(self, but: str) -> set[int]:
        used: set[int] = set()
        for aid in self.order:
            if aid != but and self.mgr.is_running(aid):
                used.update(self.mgr.rt[aid].info.get("channels", []))
        return used

    def _free_channel_base(self, aid: str, n: int) -> int:
        used = self._used_channels(aid)
        for base in range(0, MAX_CHANNELS - n + 1):
            if not used.intersection(range(base, base + n)):
                return base
        raise RuntimeError("no free Insight channels: stop another app first")

    def preview_config(self, aid: str, opts: dict | None = None) -> str:
        """The config a run would use, with placeholder stream URLs."""
        a = self.apps[aid]
        if self.custom_config_path(aid).exists():
            return self.custom_config_path(aid).read_text()
        prefs = {**self.prefs(aid), **(opts or {})}
        n = int(prefs.get("streams") or a.stream_count())
        urls = self._custom_urls(prefs, n) or [f"rtsp://127.0.0.1:8554/src{i + 1}" for i in range(n)]
        cfg = runconfig.generate(a, model_files=[models.path_of(self.s, m) for m in self.chosen_models(a, prefs)],
                                 stream_urls=urls, output_dir=self.run_dir(aid) / "output")
        return runconfig.dump(cfg)

    @staticmethod
    def _custom_urls(prefs: dict, n: int) -> list[str]:
        if prefs.get("input") != "custom":
            return []
        urls = [u.strip() for u in (prefs.get("urls") or []) if str(u).strip()]
        return urls[:n] if n else urls

    def launch_app(self, aid: str, opts: dict | None = None) -> None:
        a = self.apps[aid]
        if a.link:
            raise RuntimeError(f"{a.name} runs from {a.link.get('label', 'elsewhere')}")
        if not a.is_installed():
            raise RuntimeError(f"{a.name} is not in the installed apps bundle: run Set Up Everything")
        opts = opts or {}
        prefs = self.save_prefs(aid, {k: opts.get(k) for k in
                                      ("lang", "models", "streams", "input", "urls")})
        langs = a.available_languages()
        lang = prefs.get("lang") if prefs.get("lang") in langs else langs[0]
        chosen = self.chosen_models(a, prefs)
        missing = models.missing(self.s, chosen)
        if missing and not self.s.sima_cli_bin():
            raise RuntimeError("models are missing and sima-cli was not found to download them")
        if lang == "python" and not (self.s.pyneat / "bin" / "python").exists():
            raise RuntimeError(f"Python apps need the pyneat environment at {self.s.pyneat} "
                               "(it comes with Neat Core). Try the C++ version.")
        exclusive = opts.get("mode", "exclusive") != "alongside"
        rd = self.run_dir(aid)
        cfg_path = rd / "config.yaml"
        out_dir = rd / "output"
        rt = self.mgr.rt[aid]
        n = int(prefs.get("streams") or a.stream_count()) if a.stream_count() else 0

        def prepare(log) -> None:
            info: dict[str, Any] = {"lang": lang, "config": str(cfg_path), "channels": [], "slots": [],
                                    "models": [m["id"] for m in chosen]}
            urls: list[str] = []
            video = None
            if a.insight or n:
                self._ensure_insight(log)
            if n:
                urls = self._custom_urls(prefs, n)
                if not urls and not self.platform.ffmpeg()["present"]:
                    raise RuntimeError("Insight needs ffmpeg to stream the sample videos: run Set Up "
                                       "Everything (or sudo apt-get install -y ffmpeg), or use My RTSP")
                if not urls:
                    clips = [self.s.bundle / m for m in a.media] if a.media else None
                    if clips and not all(c.exists() for c in clips):
                        raise RuntimeError(f"the clip {a.media} is not in the bundle")
                    try:
                        urls, slots = self.insight.ensure_streams(n, clips, log=log)
                    except InsightError as e:
                        raise RuntimeError(str(e)) from None
                    info["slots"] = slots
                    info["input"] = "samples"
                    video = self.insight.video_info(clips[0] if clips else
                                                    self.insight.cache / self.insight.sample_name(1))
                else:
                    info["input"] = "custom"
                    log(f"Using your stream URL(s): {', '.join(urls)}")
            channel = 0
            if a.insight:
                count = max(1, n or int(a.insight.get("channels", 1)))
                channel = self._free_channel_base(aid, count)
                info["channels"] = list(range(channel, channel + count))
            if out_dir.exists():
                shutil.rmtree(out_dir, ignore_errors=True)
            out_dir.mkdir(parents=True, exist_ok=True)
            image_overrides = {}
            if a.sample_images and not self.custom_config_path(aid).exists():
                key = runconfig.image_input_key(a)
                folder = self.images.ensure(a.sample_images, log=log)
                if key and key not in (a.config or {}) and folder:
                    image_overrides[key] = str(folder)
                    info["input"] = f"images:{a.sample_images}"
                    log(f"Input: {self.images.label(a.sample_images)} ({folder})")
                elif key and not folder:
                    log("Could not download the sample images: using the bundle's own")
            custom = self.custom_config_path(aid)
            if custom.exists():
                log("Using your edited config (runs/" + aid + "/custom.yaml)")
                text = custom.read_text()
                yaml.safe_load(text)                    # fail early on a broken edit
            else:
                cfg = runconfig.generate(a, model_files=[models.path_of(self.s, m) for m in chosen],
                                         stream_urls=urls, channel=channel, output_dir=out_dir,
                                         stream_video=video, overrides=image_overrides)
                left = runconfig.placeholders_left(cfg)
                if left:
                    raise RuntimeError("the config still has placeholders: " + "; ".join(left[:4]))
                text = runconfig.dump(cfg, f"# Generated by Neat Apps Launcher for {a.name} "
                                           f"({time.strftime('%Y-%m-%d %H:%M:%S')})\n")
            cfg_path.write_text(text)
            rt.info = info
            self.push()

        def command() -> str:
            q = shlex.quote
            lines = ["set -eo pipefail", f"cd {q(str(self.s.bundle))}"]
            miss = models.missing(self.s, chosen)
            if miss:
                lines.append(models.download_script(self.s, miss))
            if lang == "python":
                req = a.dir / "src" / "python" / "requirements.txt"
                lines.append(f"source {q(str(self.s.pyneat / 'bin' / 'activate'))}")
                if req.exists():
                    digest = hashlib.sha1(req.read_bytes()).hexdigest()[:12]
                    marker = q(str(rd / f".pydeps-{digest}"))
                    lines.append(f'[ -f {marker} ] || {{ echo "▸ Installing Python requirements"; '
                                 f"python3 -m pip install -q -r {q(str(req))} && touch {marker}; }}")
                run = f"python3 -u {q(str(a.python_main()))} --config {q(str(cfg_path))}"
            else:
                run = f"{q(str(a.binary_path()))} --config {q(str(cfg_path))}"
            if a.validate_flag:
                lines.append(f'echo "▸ Checking the config"; {run} --validate-config-only')
            if a.before_first_run:
                lines.append(self._before_first_run(a, run, cfg_path, chosen))
            lines.append(f'echo "▸ Running {a.name} ({"Python" if lang == "python" else "C++"})"')
            lines.append(f"exec {run}")
            return "\n".join(lines) + "\n"

        self.mgr.launch(aid, command, self.s.bundle, exclusive=exclusive, prepare=prepare,
                        title="Running")

    def _before_first_run(self, a: App, run: str, cfg_path: Path, chosen: list[dict]) -> str:
        """A one-time step (e.g. PatchCore's --calibrate) that runs when the files it
        creates are missing, or older than the model they were made with."""
        step = a.before_first_run
        q = shlex.quote
        cfg = yaml.safe_load(cfg_path.read_text()) or {}
        files = []
        for key in step.get("creates", []):
            v = runconfig.get_dotted(cfg, key, "")
            if v:
                files.append(q(str(v if Path(str(v)).is_absolute() else self.s.bundle / str(v))))
        if not files:
            return ""
        model_files = [q(str(models.path_of(self.s, m))) for m in chosen if m.get("file")]
        tests = [f"[ ! -s {f} ]" for f in files] + [f"[ {m} -nt {files[0]} ]" for m in model_files]
        label = step.get("label", "Preparing")
        return (f"if {' || '.join(tests)}; then echo \"▸ {label}\"; "
                f"{run} {step.get('args', '')}; fi")

    def _app_exited(self, aid: str, code: int) -> None:
        """Stop this run's Insight sources unless another running app uses them."""
        slots = set(self.mgr.rt[aid].info.get("slots", []))
        for other in self.order:
            if other != aid and self.mgr.is_running(other):
                slots -= set(self.mgr.rt[other].info.get("slots", []))
        if slots and self.insight.healthy():
            self.insight.stop_slots(sorted(slots))

    def stop_app(self, aid: str, force: bool = False) -> None:
        self.mgr.stop(aid, force=force)

    def stop_all(self) -> None:
        for aid in self.order:
            if self.mgr.is_running(aid):
                self.mgr.stop(aid)

    # -- results -------------------------------------------------------------------
    def outputs(self, aid: str) -> dict:
        d = self.run_dir(aid) / "output"
        files = sorted((p for p in d.rglob("*") if p.is_file()), key=lambda p: p.name) if d.is_dir() else []
        images = [str(p.relative_to(d)) for p in files if p.suffix.lower() in IMAGE_EXT]
        report = None
        for p in files:
            if p.suffix == ".json" and p.stat().st_size < 2_000_000:
                try:
                    report = json.loads(p.read_text())
                except Exception:                                  # noqa: BLE001
                    pass
                break
        return {"images": images[:400], "count": len(files), "report": report,
                "others": [str(p.relative_to(d)) for p in files if p.suffix.lower() not in IMAGE_EXT][:50]}

    def output_file(self, aid: str, rel: str) -> Path | None:
        d = (self.run_dir(aid) / "output").resolve()
        p = (d / rel).resolve()
        return p if str(p).startswith(str(d) + "/") and p.is_file() else None

    def viewer(self, aid: str) -> dict:
        info = self.mgr.rt[aid].info
        ch = info.get("channels") or [0]
        if not self.insight.healthy():
            return {"url": "", "error": "Neat Insight is not running"}
        try:
            return {"url": self.insight.viewer_url(ch), "channels": ch}
        except InsightError as e:
            return {"url": "", "error": str(e)}
