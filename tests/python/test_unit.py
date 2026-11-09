"""Neat Apps Launcher tests: run on a PC against a fake board
(tests/python/fixtures: fake bundle, fake sima-cli, fake Insight).

    .venv/bin/python -m pytest tests/python -q
"""
from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "src" / "launcher"))
sys.path.insert(0, str(HERE / "fixtures"))

import fakeboard  # noqa: E402
import models  # noqa: E402
import registry  # noqa: E402
import runconfig  # noqa: E402

CATALOG = {
    "schema": 1, "modelzoo_version": "2.1.3", "source": {"repo": "sima-neat/apps", "commit": "abc", "branch": "main"},
    "apps": [
        {"id": "folder-app", "rel": "examples/object-detection/folder-app", "name": "Folder App",
         "category": "object-detection", "languages": ["cpp", "python"], "binary": "folder-app",
         "models": [{"id": "m1", "source": "url", "url": "https://x/m1.tar.gz", "file": "m1.tar.gz"}],
         "default_models": ["m1"], "model_placeholder": True,
         "input": {"kind": "folder", "path": "assets/datasets/coco"}, "output": {"kind": "images", "key": "io.output_dir"},
         "insight": None, "validate_flag": False,
         "_config": "model:\n  path: <model-path>\nio:\n  input_dir: assets/datasets/coco\n  output_dir: sandbox/folder-app\n"},
        {"id": "stream-app", "rel": "examples/object-detection/stream-app", "name": "Stream App",
         "category": "object-detection", "languages": ["cpp"], "binary": "stream-app",
         "models": [{"id": "m1", "source": "url", "url": "https://x/m1.tar.gz", "file": "m1.tar.gz"},
                    {"id": "m2", "source": "url", "url": "https://x/m2.tar.gz", "file": "m2.tar.gz"}],
         "default_models": ["m1"], "model_placeholder": True,
         "input": {"kind": "rtsp", "count": 1}, "output": {"kind": "log"}, "insight": {"channels": 1}, "validate_flag": True,
         "_config": "model:\n  path: <model-path>\nsource:\n  url: <rtsp-url-1>\n  rtsp_url: ''\n"
                    "output:\n  insight:\n    host: <insight-host-ip>\n    video_port: 9000\n    metadata_port: 9100\n"
                    "testing:\n  e2e: {x: 1}\n"},
        {"id": "multi-app", "rel": "examples/tracking/multi-app", "name": "Multi App",
         "category": "tracking", "languages": ["cpp"], "binary": "multi-app",
         "models": [{"id": "m1", "source": "url", "url": "https://x/m1.tar.gz", "file": "m1.tar.gz"}],
         "default_models": ["m1"], "model_placeholder": True,
         "input": {"kind": "streams", "count": 4}, "output": {"kind": "log"}, "insight": {"channels": 4}, "validate_flag": False,
         "_config": "model:\n  path: <model-path>\nstreams:\n  - <rtsp-url-1>\n  - <rtsp-url-2>\n  - <rtsp-url-3>\n  - <rtsp-url-4>\n"
                    "output:\n  insight:\n    host: <insight-host-ip>\n    video_port_base: 9000\n    metadata_port_base: 9100\n"},
        {"id": "hidden-app", "rel": "examples/x/hidden-app", "name": "Hidden", "category": "x", "languages": ["cpp"],
         "binary": "hidden-app", "models": [], "default_models": [], "input": {"kind": "none"}, "output": {"kind": "log"}},
    ],
}


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Board:
    """A temp fake board, the launcher's config for it, and an app factory."""

    def __init__(self, installed=True):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.port = free_port()
        fakeboard.build(self.root, CATALOG, installed=installed, insight_port=self.port)
        (self.root / "catalog.json").write_text(json.dumps(CATALOG))
        over = (self.root / "config.local.yaml").read_text() + "apps:\n  - id: hidden-app\n    hidden: true\n"
        (self.root / "config.local.yaml").write_text(over)

    def load(self):
        return registry.load(override=self.root / "config.local.yaml", catalog=self.root / "catalog.json")

    def app(self):
        from app import create_app
        settings, apps = self.load()
        a, sio = create_app(settings, apps, logs_dir=self.root / "logs", cache_dir=self.root / "cache",
                            runs_dir=self.root / "runs", start_monitor=False)
        svc = a.extensions["launcher"]["service"]
        return a, a.test_client(), svc

    def close(self, svc=None):
        if svc:
            for uid in list(svc.mgr.units):
                if svc.mgr.is_running(uid):
                    svc.mgr.stop(uid, wait=True, force=True)
        self.tmp.cleanup()


def wait(pred, t=20.0):
    end = time.time() + t
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.1)
    return False


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.b = Board()

    def tearDown(self):
        self.b.close()

    def test_hidden_and_paths(self):
        s, apps = self.b.load()
        self.assertEqual([a.id for a in apps], ["folder-app", "stream-app", "multi-app"])
        self.assertEqual(s.bundle, self.b.root / "prebuilt-apps")
        fa = apps[0]
        self.assertTrue(fa.is_installed())
        self.assertEqual(fa.available_languages(), ["cpp", "python"])
        self.assertEqual(apps[2].stream_count(), 4)

    def test_real_catalog_loads(self):
        s, apps = registry.load()
        self.assertGreater(len(apps), 15)
        ids = {a.id for a in apps}
        self.assertNotIn("pcie-high-density-multi-stream-object-detector", ids)
        self.assertIn("yolo26-object-detector", ids)
        for a in apps:
            for m in a.models:
                if m["source"] == "url":
                    self.assertNotIn("{", m["url"])


class RunConfigTests(unittest.TestCase):
    def setUp(self):
        self.b = Board()
        self.s, apps = self.b.load()
        self.apps = {a.id: a for a in apps}

    def tearDown(self):
        self.b.close()

    def test_single_stream(self):
        cfg = runconfig.generate(self.apps["stream-app"], model_files=[Path("/m/m1.tar.gz")],
                                 stream_urls=["rtsp://127.0.0.1:8554/src1"], channel=3)
        self.assertEqual(cfg["model"]["path"], "/m/m1.tar.gz")
        self.assertEqual(cfg["source"]["url"], "rtsp://127.0.0.1:8554/src1")
        self.assertEqual(cfg["source"]["rtsp_url"], "")
        self.assertEqual(cfg["output"]["insight"], {"host": "127.0.0.1", "video_port": 9003, "metadata_port": 9103})
        self.assertNotIn("testing", cfg)
        self.assertEqual(runconfig.placeholders_left(cfg), [])

    def test_streams_fewer_and_more(self):
        a = self.apps["multi-app"]
        two = runconfig.generate(a, model_files=[Path("/m")], stream_urls=["u1", "u2"])
        self.assertEqual(two["streams"], ["u1", "u2"])
        six = runconfig.generate(a, model_files=[Path("/m")], stream_urls=[f"u{i}" for i in range(6)])
        self.assertEqual(len(six["streams"]), 6)

    def test_paths_and_output(self):
        a = self.apps["folder-app"]
        cfg = runconfig.generate(a, model_files=[Path("/m")], stream_urls=[], output_dir=Path("/out"),
                                 overrides={"io.extra": 1})
        self.assertEqual(cfg["io"]["input_dir"], str(self.s.bundle / "assets/datasets/coco"))
        self.assertEqual(cfg["io"]["output_dir"], "/out")
        self.assertEqual(cfg["io"]["extra"], 1)

    def test_placeholders_left(self):
        cfg = runconfig.generate(self.apps["stream-app"], model_files=[], stream_urls=["u"])
        self.assertTrue(any("<model-path>" in x for x in runconfig.placeholders_left(cfg)))


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.b = Board()
        self.s, apps = self.b.load()
        self.apps = {a.id: a for a in apps}

    def tearDown(self):
        self.b.close()

    def test_selected_and_status(self):
        a = self.apps["stream-app"]
        self.assertEqual([m["id"] for m in models.selected(a, None)], ["m1"])
        self.assertEqual([m["id"] for m in models.selected(a, ["m2", "nope"])], ["m2"])
        st = models.status(self.s, a)
        self.assertFalse(any(m["present"] for m in st))

    def test_download_script_with_fake_cli(self):
        import subprocess
        a = self.apps["stream-app"]
        script = models.download_script(self.s, models.selected(a, ["m2"]))
        r = subprocess.run(["bash", "-c", "set -e\n" + script], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(models.present(self.s, a.model("m2")))
        self.assertFalse(list(self.s.models_dir.glob(".download-*")))

    def test_model_from_older_modelzoo_counts_as_missing(self):
        a = self.apps["stream-app"]
        m = a.model("m2")
        self.s.models_dir.mkdir(parents=True, exist_ok=True)
        models.path_of(self.s, m).write_bytes(b"old")           # no record of its source
        self.assertFalse(models.present(self.s, m))
        models.stamp_of(self.s, m).write_text("https://old/sdk2/m2.tar.gz\n")
        self.assertFalse(models.present(self.s, m))
        models.stamp_of(self.s, m).write_text(models.source_key(self.s, m) + "\n")
        self.assertTrue(models.present(self.s, m))


class FlowTests(unittest.TestCase):
    """The full path through the web API: setup, run, Insight streams, stop."""

    def setUp(self):
        self.b = Board(installed=False)
        self.a, self.c, self.svc = self.b.app()
        self.svc.mgr.start()

    def tearDown(self):
        self.svc.mgr.shutdown()
        self.b.close(self.svc)

    def setup_board(self):
        self.assertTrue(self.c.post("/api/platform/setup", json={}).json["ok"])
        self.assertTrue(wait(lambda: self.svc.mgr.rt["platform"].job.done, 30))
        self.assertEqual(self.svc.mgr.rt["platform"].job.code, 0,
                         (self.b.root / "logs" / "platform-job.log").read_text())

    def test_not_installed_then_setup(self):
        apps = {x["id"]: x for x in self.c.get("/api/apps").json["apps"]}
        self.assertEqual(apps["folder-app"]["state"], "not_installed")
        self.assertEqual(self.c.post("/api/apps/folder-app/run", json={}).status_code, 409)
        self.setup_board()
        st = self.c.get("/api/platform?force=1").json
        self.assertTrue(st["bundle"]["present"])
        self.assertEqual(st["samples"]["cached"], 4)
        self.assertEqual(self.c.get("/api/apps/folder-app").json["state"], "needs_models")

    def test_folder_app_runs_and_writes_results(self):
        self.setup_board()
        self.assertTrue(self.c.post("/api/apps/folder-app/run", json={"lang": "python"}).json["ok"])
        self.assertTrue(wait(lambda: self.c.get("/api/apps/folder-app").json["state"] in ("finished", "error"), 30))
        snap = self.c.get("/api/apps/folder-app").json
        self.assertEqual(snap["state"], "finished", (self.b.root / "logs" / "folder-app.log").read_text())
        out = self.c.get("/api/apps/folder-app/outputs").json
        self.assertEqual(len(out["images"]), 3)
        self.assertEqual(self.c.get(f"/api/apps/folder-app/output/{out['images'][0]}").status_code, 200)
        self.assertEqual(self.c.get("/api/apps/folder-app/output/../prefs.json").status_code, 404)

    def test_stream_apps_with_insight(self):
        self.setup_board()
        self.assertTrue(wait(lambda: self.svc.insight.healthy(), 20))
        self.assertTrue(self.c.post("/api/apps/stream-app/run", json={}).json["ok"])
        self.assertTrue(wait(lambda: self.c.get("/api/apps/stream-app").json["state"] == "running", 30),
                        (self.b.root / "logs" / "stream-app.log").read_text())
        self.assertTrue(self.c.post("/api/apps/multi-app/run", json={"mode": "alongside", "streams": 2}).json["ok"])
        self.assertTrue(wait(lambda: self.c.get("/api/apps/multi-app").json["state"] == "running", 30))
        one = self.c.get("/api/apps/stream-app").json["info"]
        two = self.c.get("/api/apps/multi-app").json["info"]
        self.assertEqual(one["channels"], [0])
        self.assertEqual(two["channels"], [1, 2])            # no overlap with the first app
        cfg = (self.b.root / "runs" / "multi-app" / "config.yaml").read_text()
        self.assertIn("rtsp://127.0.0.1:8554/src2", cfg)
        self.assertIn("video_port_base: 9001", cfg)
        self.assertIn("src=1,2", self.c.get("/api/apps/multi-app/viewer").json["url"])
        playing = {s["index"] for s in self.svc.insight.sources() if s["state"] == "playing"}
        self.assertEqual(playing, {1, 2})
        # Stopping the multi-stream app keeps src1 (still used by the first app).
        self.svc.mgr.stop("multi-app", wait=True)
        self.assertTrue(wait(lambda: {s["index"] for s in self.svc.insight.sources() if s["state"] == "playing"} == {1}))
        self.assertEqual(self.c.get("/api/apps/multi-app").json["state"], "ready")

    def test_exclusive_run_stops_the_other_app(self):
        self.setup_board()
        self.c.post("/api/apps/stream-app/run", json={})
        self.assertTrue(wait(lambda: self.c.get("/api/apps/stream-app").json["state"] == "running", 30))
        self.c.post("/api/apps/multi-app/run", json={"mode": "exclusive"})
        self.assertTrue(wait(lambda: self.c.get("/api/apps/multi-app").json["state"] == "running", 40))
        self.assertNotEqual(self.c.get("/api/apps/stream-app").json["state"], "running")

    def test_custom_config_and_failed_run(self):
        self.setup_board()
        r = self.c.post("/api/apps/folder-app/config", json={"text": "- not a mapping"})
        self.assertEqual(r.status_code, 400)
        text = self.c.get("/api/apps/folder-app/config").json["text"]
        self.assertTrue(self.c.post("/api/apps/folder-app/config", json={"text": text}).json["ok"])
        self.assertTrue(self.c.get("/api/apps/folder-app").json["custom_config"])
        self.c.post("/api/apps/folder-app/config", json={"reset": True})
        self.assertFalse(self.c.get("/api/apps/folder-app").json["custom_config"])

    def test_model_download_failure_is_reported(self):
        self.setup_board()
        import os
        os.environ["FAKE_CLI_FAIL"] = "1"
        try:
            self.c.post("/api/apps/stream-app/download", json={})
            self.assertTrue(wait(lambda: self.svc.mgr.rt["stream-app"].job.done, 20))
        finally:
            del os.environ["FAKE_CLI_FAIL"]
        snap = self.c.get("/api/apps/stream-app").json
        self.assertEqual(snap["state"], "error")
        self.assertIn("download", snap["error"].lower())


class PlaceholderTests(unittest.TestCase):
    def test_app_placeholders_fill_and_resolve(self):
        b = Board()
        try:
            s, apps = b.load()
            a = {x.id: x for x in apps}["stream-app"]
            a.placeholders = {"<rtsp-url-1>": "rtsp://cam/1", "<extra>": "models/two.tar.gz"}
            cfg_text = a.config_path().read_text() + "second: <extra>\n"
            a.config_path().write_text(cfg_text)
            cfg = runconfig.generate(a, model_files=[s.models_dir / "m1.tar.gz"], stream_urls=[])
            self.assertEqual(cfg["source"]["url"], "rtsp://cam/1")
            self.assertEqual(cfg["second"], str(s.bundle / "models" / "two.tar.gz"))
            self.assertEqual(runconfig.placeholders_left(cfg), [])
        finally:
            b.close()


class StreamEntryTests(unittest.TestCase):
    """Apps whose streams are mappings: one model per stream, sizes, more or fewer streams."""
    def setUp(self):
        self.b = Board()
        self.s, apps = self.b.load()
        self.a = {x.id: x for x in apps}["multi-app"]
        self.a.config_path().write_text(
            "streams:\n"
            "  - {id: camera0, url: <rtsp-url-1>, insight_channel: 0, width: 1920, height: 1080, fps: 30, model: <model-path>}\n"
            "  - {id: camera1, url: <rtsp-url-2>, insight_channel: 1, width: 1920, height: 1080, fps: 0, model: <model-path>}\n"
            "output:\n  insight:\n    host: <insight-host-ip>\n    video_port_base: 9000\n")

    def tearDown(self):
        self.b.close()

    def gen(self, urls):
        return runconfig.generate(self.a, model_files=[Path("/m/a"), Path("/m/b")], stream_urls=urls,
                                  stream_video={"width": 1280, "height": 720, "fps": 16})

    def test_models_in_order_and_sizes_from_the_video(self):
        st = self.gen(["rtsp://1", "rtsp://2"])["streams"]
        self.assertEqual([e["model"] for e in st], ["/m/a", "/m/b"])
        self.assertEqual((st[0]["width"], st[0]["height"], st[0]["fps"]), (1280, 720, 16))
        self.assertEqual(st[1]["fps"], 0)                       # 0 = detect: left alone

    def test_fewer_streams_drop_entries(self):
        st = self.gen(["rtsp://1"])["streams"]
        self.assertEqual([e["url"] for e in st], ["rtsp://1"])

    def test_more_streams_copy_the_last_entry(self):
        st = self.gen(["rtsp://1", "rtsp://2", "rtsp://3"])["streams"]
        self.assertEqual([(e["id"], e["insight_channel"], e["url"]) for e in st],
                         [("camera0", 0, "rtsp://1"), ("camera1", 1, "rtsp://2"), ("camera2", 2, "rtsp://3")])


class ShellSignalTests(unittest.TestCase):
    @unittest.skipUnless(Path("/proc/self/status").exists(), "needs Linux /proc")
    def test_ctrl_c_works_although_the_launcher_ignores_sigint(self):
        import signal
        import terminal
        out: list[str] = []
        old = signal.signal(signal.SIGINT, signal.SIG_IGN)      # as under nohup/&
        try:
            s = terminal.Session(out.append, lambda code: None, shell="/bin/sh")
        finally:
            signal.signal(signal.SIGINT, old)
        try:
            s.write("sleep 30; echo NOT-INTERRUPTED\n")
            time.sleep(0.5)
            s.write("\x03")                                     # Ctrl+C
            s.write("echo after-$((1+1))\n")
            self.assertTrue(wait(lambda: "after-2" in "".join(out), 10), "".join(out)[-300:])
            self.assertNotIn("NOT-INTERRUPTED\r\n", "".join(out).replace("echo NOT-INTERRUPTED", ""))
        finally:
            s.close()


class BeforeFirstRunTests(unittest.TestCase):
    """PatchCore-style one-time step: runs when its files are missing or older than the model."""
    def setUp(self):
        self.b = Board()
        self.a, self.c, self.svc = self.b.app()

    def tearDown(self):
        self.b.close(self.svc)

    def test_step_runs_only_when_needed(self):
        import subprocess
        app = self.svc.apps["stream-app"]
        app.before_first_run = {"args": "--calibrate", "creates": ["bank.path"], "label": "Calibrating"}
        m = app.model("m1")
        self.svc.s.models_dir.mkdir(parents=True, exist_ok=True)
        models.path_of(self.svc.s, m).write_bytes(b"model")
        bank = self.b.root / "bank.npy"
        cfg = self.b.root / "cfg.yaml"
        cfg.write_text(f"bank:\n  path: {bank}\n")
        snippet = self.svc._before_first_run(app, "echo RAN", cfg, [m])

        def ran() -> bool:
            return "RAN --calibrate" in subprocess.run(["bash", "-c", snippet], capture_output=True, text=True).stdout
        self.assertTrue(ran())                                  # no bank yet
        bank.write_bytes(b"bank")
        os.utime(models.path_of(self.svc.s, m), (1, 1))
        self.assertFalse(ran())                                 # bank newer than the model
        os.utime(bank, (0, 0))
        self.assertTrue(ran())                                  # model replaced after the bank


class PageTests(unittest.TestCase):
    def test_index_and_health(self):
        b = Board()
        try:
            a, c, svc = b.app()
            r = c.get("/")
            self.assertEqual(r.status_code, 200)
            self.assertIn(b"3 Example Apps", r.data)
            self.assertEqual(c.get("/health").json["app"], "neat-apps-launcher")
            self.assertEqual(c.post("/api/apps/nope/run").status_code, 404)
        finally:
            b.close()


class ResetMlaTests(unittest.TestCase):
    """3.0 firmware has no simaai-appcomplex.service: Reset MLA stops the programs
    holding the MLA instead; older firmware still restarts the service."""

    def _holder(self, dev, ignore_term=False):
        import subprocess
        code = ("import signal,sys,time\n"
                + ("signal.signal(signal.SIGTERM, signal.SIG_IGN)\n" if ignore_term else "")
                + "f=open(sys.argv[1]); print('ready', flush=True); time.sleep(120)")
        p = subprocess.Popen([sys.executable, "-c", code, str(dev)], stdout=subprocess.PIPE, text=True)
        self.addCleanup(lambda: (p.poll() is None and p.kill(), p.wait()))
        p.stdout.readline()
        return p

    def _stub(self):
        from types import SimpleNamespace
        log = []
        return SimpleNamespace(system_log=SimpleNamespace(append=log.append), push=lambda: None), log

    def test_finds_the_programs_holding_the_mla(self):
        import procman
        dev = Path(tempfile.mkdtemp()) / "mla"
        dev.write_text("")
        p = self._holder(dev)
        users = procman.mla_users(devices=(str(dev),))
        self.assertEqual([u["pid"] for u in users], [p.pid])
        self.assertIn(str(dev), users[0]["cmd"])

    def test_3_0_stops_them_and_kills_one_that_ignores_sigterm(self):
        from unittest import mock
        import procman
        dev = Path(tempfile.mkdtemp()) / "mla"
        dev.write_text("")
        polite, stubborn = self._holder(dev), self._holder(dev, ignore_term=True)
        stub, log = self._stub()
        with mock.patch.object(procman, "legacy_mla_service", return_value=False), \
             mock.patch.object(procman, "mla_users", return_value=[{"pid": polite.pid, "cmd": "polite"}, {"pid": stubborn.pid, "cmd": "stubborn"}]):
            r = procman.Manager.reset_mla(stub, grace=1.0)
        self.assertEqual(r["method"], "stop")
        self.assertEqual(sorted(r["stopped"]), sorted([polite.pid, stubborn.pid]))
        import psutil
        for p in (polite, stubborn):              # reset_mla reaps them itself
            self.assertFalse(psutil.pid_exists(p.pid) and psutil.Process(p.pid).status() != psutil.STATUS_ZOMBIE)
        killed = [line for line in log if "SIGKILL" in line]
        self.assertEqual(len(killed), 1)
        self.assertIn(str(stubborn.pid), killed[0])  # only the one that ignored SIGTERM

    def test_older_firmware_restarts_appcomplex(self):
        from unittest import mock
        import subprocess
        import procman
        stub, _ = self._stub()
        done = subprocess.CompletedProcess([], 0, "", "")
        with mock.patch.object(procman, "legacy_mla_service", return_value=True), \
             mock.patch.object(procman.subprocess, "run", return_value=done) as run:
            r = procman.Manager.reset_mla(stub)
        self.assertEqual(r["method"], "service")
        self.assertEqual(run.call_args[0][0], ["sudo", "-n", "systemctl", "restart", "simaai-appcomplex.service"])

if __name__ == "__main__":
    unittest.main()


class SampleImageTests(unittest.TestCase):
    """Image apps read the launcher's sample photo sets (falling back to the bundle's)."""
    def setUp(self):
        self.b = Board()

    def tearDown(self):
        self.b.close(getattr(self, "svc", None))

    def test_set_downloads_with_lower_case_names(self):
        from samples import SampleImages
        s, _ = self.b.load()
        imgs = SampleImages(s, self.b.root / "cache")
        self.assertEqual(imgs.total(), 6)
        self.assertFalse(imgs.ready("scenes"))
        logs = []
        d = imgs.ensure("scenes", log=logs.append)
        self.assertEqual(sorted(p.name for p in d.iterdir()), ["one.jpg", "two.jpeg"])
        self.assertTrue(imgs.ready("scenes"))
        self.assertEqual(imgs.cached(), 2)
        self.assertIn("Downloading 2 sample image(s)", logs[0])
        self.assertIsNone(imgs.ensure("no-such-set"))

    def test_unreachable_set_gives_none(self):
        from samples import SampleImages
        s, _ = self.b.load()
        s.sample_images["scenes"]["from"] = "file:///nonexistent/"
        imgs = SampleImages(s, self.b.root / "cache")
        logs = []
        self.assertIsNone(imgs.ensure("scenes", log=logs.append))
        self.assertTrue(any("could not get one.jpg" in m for m in logs))
        self.assertEqual(list(imgs.dir("scenes").iterdir()), [])      # no .part leftovers

    def test_fetch_script_gets_every_set(self):
        import subprocess
        from samples import SampleImages
        s, _ = self.b.load()
        imgs = SampleImages(s, self.b.root / "cache")
        r = subprocess.run(["bash", "-c", imgs.fetch_script()], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(imgs.cached(), 6)
        self.assertTrue(all(imgs.ready(n) for n in ("scenes", "faces", "imagenet")))

    def test_input_key(self):
        _, apps = self.b.load()
        a = {x.id: x for x in apps}
        self.assertEqual(runconfig.image_input_key(a["folder-app"]), "io.input_dir")
        self.assertIsNone(runconfig.image_input_key(a["stream-app"]))
        a["folder-app"].input = {"kind": "image"}
        self.assertEqual(runconfig.image_input_key(a["folder-app"]), "io.input")

    def test_folder_app_runs_on_its_sample_set(self):
        over = self.b.root / "config.local.yaml"
        over.write_text(over.read_text() + "  - id: folder-app\n    sample_images: scenes\n")
        _, self.c, self.svc = self.b.app()
        self.c.post("/api/platform/setup", json={})
        self.assertTrue(wait(lambda: self.svc.mgr.rt["platform"].job and self.svc.mgr.rt["platform"].job.done, 30))
        self.assertEqual(self.c.get("/api/platform?force=1").json["samples"]["images_cached"], 6)
        snap = self.c.get("/api/apps/folder-app").json
        self.assertEqual(snap["sample_set"]["count"], 2)
        self.assertTrue(snap["sample_set"]["ready"])
        self.assertTrue(self.c.post("/api/apps/folder-app/run", json={"lang": "python"}).json["ok"])
        self.assertTrue(wait(lambda: self.c.get("/api/apps/folder-app").json["state"] in ("finished", "error"), 30))
        log = (self.b.root / "logs" / "folder-app.log").read_text()
        self.assertEqual(self.c.get("/api/apps/folder-app").json["state"], "finished", log)
        cfg = (self.b.root / "runs" / "folder-app" / "config.yaml").read_text()
        self.assertIn(str(self.b.root / "cache" / "images" / "scenes"), cfg)
        self.assertEqual(len(self.c.get("/api/apps/folder-app/outputs").json["images"]), 2)


class PrerequisiteTests(unittest.TestCase):
    """Firmware, Neat Core, pyneat and the Developer Portal sign-in: checked, and installed where
    the launcher can do it."""
    def setUp(self):
        self.b = Board(installed=False)
        self.a, self.c, self.svc = self.b.app()
        (self.b.root / "core-version").unlink(missing_ok=True)

    def tearDown(self):
        self.b.close(self.svc)

    def status(self):
        return self.c.get("/api/platform?force=1").json

    def wait_job(self):
        self.assertTrue(wait(lambda: self.svc.mgr.rt["platform"].job and self.svc.mgr.rt["platform"].job.done, 30))
        return self.svc.mgr.rt["platform"].job.code, (self.b.root / "logs" / "platform-job.log").read_text()

    def test_versions(self):
        import bundle
        self.assertEqual(bundle.version_tuple("3.0.0_master_B2501"), (3, 0, 0))
        self.assertEqual(bundle.version_tuple("v0.6.0+main.88bf"), (0, 6, 0))
        self.assertEqual(bundle.version_tuple("main"), ())
        info = self.b.root / "buildinfo"
        info.write_text("MACHINE = modalix\nSIMA_BUILD_VERSION = 2.1.3_master_B4837\n")
        self.assertEqual(bundle.firmware_version(info), "2.1.3")
        self.assertEqual(bundle.firmware_version(self.b.root / "missing"), "")

    def test_old_firmware_stops_setup_with_the_way_out(self):
        from unittest import mock
        import bundle
        self.svc.s.platform_version = "3.0"
        with mock.patch.object(bundle, "firmware_version", return_value="2.1.3"):
            fw = self.status()["firmware"]
            self.assertEqual((fw["version"], fw["required"], fw["ok"]), ("2.1.3", "3.0", False))
            self.assertFalse(self.status()["ready"])
            self.c.post("/api/platform/setup", json={})
            code, log = self.wait_job()
        self.assertEqual(code, 7, log)
        self.assertIn("sima-cli update", log)
        self.assertFalse((self.b.root / "prebuilt-apps" / "examples").exists())    # nothing installed

    def test_setup_installs_core_when_missing(self):
        st = self.status()
        self.assertEqual((st["core"]["present"], st["core"]["ok"]), (False, False))
        self.c.post("/api/platform/setup", json={})
        code, log = self.wait_job()
        self.assertEqual(code, 0, log)
        self.assertIn("Installing Neat Core", log)
        core = self.status()["core"]
        self.assertEqual((core["version"], core["wanted"], core["ok"]), ("0.5.0+fake", "0.5.0", True))

    def test_core_that_does_not_match_the_bundle(self):
        self.c.post("/api/platform/setup", json={})
        self.wait_job()
        (self.b.root / "core-version").write_text("0.4.0\n")
        self.svc.platform.forget()
        core = self.status()["core"]
        self.assertEqual((core["present"], core["ok"], core["wanted"]), (True, False, "0.5.0"))
        self.assertFalse(self.status()["ready"])
        self.assertTrue(self.c.post("/api/platform/install", json={"what": "core"}).json["ok"])
        code, log = self.wait_job()
        self.assertEqual(code, 0, log)
        self.assertTrue(self.status()["core"]["ok"])
        self.assertEqual(self.c.post("/api/platform/install", json={"what": "rm -rf"}).status_code, 409)

    def test_pyneat_link(self):
        import subprocess
        from unittest import mock
        import bundle
        nvme = self.b.root / "nvme-pyneat"
        (nvme / "bin").mkdir(parents=True)
        os.symlink(sys.executable, nvme / "bin" / "python")
        home = self.svc.s.pyneat
        import shutil
        shutil.rmtree(home)
        with mock.patch.object(bundle, "NVME_PYNEAT", nvme):
            p = self.svc.platform.pyneat()
            self.assertEqual((p["present"], p["linkable"]), (False, True))
            r = subprocess.run(["bash", "-c", self.svc.platform.pyneat_link_script()], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(os.readlink(home), str(nvme))
            self.assertTrue(self.svc.platform.pyneat()["present"])

    def test_signed_in(self):
        from unittest import mock
        import bundle
        with mock.patch.object(bundle.Path, "home", return_value=self.b.root):
            self.assertFalse(bundle.Platform.signed_in()["present"])
            (self.b.root / ".sima-cli").mkdir()
            (self.b.root / ".sima-cli" / ".tokens.json").write_text("{}")
            self.assertTrue(bundle.Platform.signed_in()["present"])
