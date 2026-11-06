"""Neat Insight: the board's browser viewer for the apps' live video and
detections. This module starts it, loads sample videos into its streaming
sources (rtsp://<board>:8554/srcN), and builds viewer links.

Insight API used (neat_insight/app.py):
  GET  /api/health                      readiness
  GET  /api/mediasrc                    source slots: index (1..48), file, state, urls
  GET  /api/mediasrc/videos             media files that can be streamed
  POST /api/upload/media                multipart "file"
  POST /api/mediasrc/assign {index, file}
  POST /api/mediasrc/start  {index}     /stop {index}
  GET  /api/viewer-url?src=0,1          the WebRTC viewer for those channels
  GET  /api/ingest/stats                what reaches Insight's UDP ingest

Insight clears every source assignment when it starts, so streams are set up
again for each app run.
"""
from __future__ import annotations

import json
import mimetypes
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from registry import Settings

APP_SLOT_BASE = 41          # slots for app-specific clips; 1..16 hold the sample videos


def _ctx() -> ssl.SSLContext:
    c = ssl.create_default_context()
    c.check_hostname = False
    c.verify_mode = ssl.CERT_NONE
    return c


class InsightError(RuntimeError):
    pass


class Insight:
    def __init__(self, settings: Settings, cache_dir: Path, host: str = "127.0.0.1"):
        self.settings = settings
        self.host = host
        self.cache = Path(cache_dir) / "media"
        self.lock = threading.Lock()

    # -- basics ---------------------------------------------------------------
    @property
    def port(self) -> int:
        return self.settings.insight_port

    @property
    def scheme(self) -> str:
        return self.settings.insight.get("scheme", "https")

    @property
    def base(self) -> str:
        return f"{self.scheme}://{self.host}:{self.port}"

    @property
    def binary(self) -> Path:
        return self.settings.insight_venv / "bin" / "neat-insight"

    def installed(self) -> bool:
        return self.binary.exists()

    def version(self) -> str:
        for p in sorted((self.settings.insight_venv / "lib").glob("python3*/site-packages/neat_insight-*.dist-info")):
            return p.name[len("neat_insight-"):-len(".dist-info")]
        return ""

    def launch_command(self) -> str:
        return f"exec {self.binary} --port {self.port}"

    def _req(self, method: str, path: str, body: dict | None = None,
             timeout: float = 10.0) -> dict | list:
        data = headers = None
        if body is not None:
            data = json.dumps(body).encode()
            headers = {"Content-Type": "application/json"}
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=_ctx()) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read()).get("error") or str(e)
            except Exception:                                      # noqa: BLE001
                msg = str(e)
            raise InsightError(f"Insight {path}: {msg}") from None
        except Exception as e:                                     # noqa: BLE001
            raise InsightError(f"Insight is not answering on port {self.port} ({e})") from None
        try:
            return json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return {"text": raw.decode("utf-8", "replace")}

    def healthy(self) -> bool:
        try:
            return self._req("GET", "/api/health", timeout=2.0).get("status") == "ok"  # type: ignore[union-attr]
        except InsightError:
            return False

    def wait_healthy(self, timeout: float = 90.0) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            if self.healthy():
                return True
            time.sleep(1.0)
        return False

    def sources(self) -> list[dict]:
        return self._req("GET", "/api/mediasrc")  # type: ignore[return-value]

    def videos(self) -> list[str]:
        return self._req("GET", "/api/mediasrc/videos")  # type: ignore[return-value]

    def ingest(self) -> dict:
        try:
            return self._req("GET", "/api/ingest/stats", timeout=3.0)  # type: ignore[return-value]
        except InsightError as e:
            return {"error": str(e)}

    def viewer_url(self, channels: list[int]) -> str:
        q = urllib.parse.urlencode({"src": ",".join(str(c) for c in channels)})
        return self._req("GET", f"/api/viewer-url?{q}").get("url", "")  # type: ignore[union-attr]

    # -- media ----------------------------------------------------------------
    def sample_name(self, n: int) -> str:
        return f"neat-apps-sample-{n:02d}.mp4"

    def sample_url(self, n: int) -> str:
        tmpl = self.settings.insight.get("samples") or \
            "https://artifacts.sima-neat.com/assets/videos/720p16/video{n:02d}.mp4"
        return tmpl.format(n=n)

    @staticmethod
    def video_info(path: Path) -> dict | None:
        """{width, height, fps} of a video file (ffprobe), or None."""
        import subprocess
        try:
            out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                  "stream=width,height,avg_frame_rate", "-of", "json", str(path)],
                                 capture_output=True, text=True, timeout=20, check=True).stdout
            s = json.loads(out)["streams"][0]
            num, _, den = s.get("avg_frame_rate", "0/1").partition("/")
            fps = round(float(num) / float(den or 1)) if float(den or 1) else 0
            return {"width": int(s["width"]), "height": int(s["height"]), "fps": fps}
        except Exception:                                          # noqa: BLE001
            return None

    def sample_count(self) -> int:
        return int(self.settings.insight.get("sample_count", 16))

    def cached_samples(self) -> int:
        return sum(1 for n in range(1, self.sample_count() + 1)
                   if (self.cache / self.sample_name(n)).exists())

    def fetch_samples_script(self, count: int | None = None) -> str:
        """Bash that downloads the sample videos into the launcher's cache."""
        import shlex
        count = count or self.sample_count()
        d = shlex.quote(str(self.cache))
        lines = [f"mkdir -p {d}", 'echo "▸ Sample videos for the streaming apps"']
        for n in range(1, count + 1):
            dest = shlex.quote(str(self.cache / self.sample_name(n)))
            url = shlex.quote(self.sample_url(n))
            lines.append(f"[ -s {dest} ] || {{ curl -fsSL {url} -o {dest}.part && mv {dest}.part {dest} "
                         f"&& echo \"  {self.sample_name(n)}\"; }}")
        lines.append(f'echo "✔ {count} sample videos in {self.cache}"')
        return "\n".join(lines) + "\n"

    def upload(self, path: Path, name: str | None = None) -> str:
        """Upload a local file into Insight's media library (if not there yet)."""
        name = name or path.name
        if name in self.videos():
            return name
        boundary = uuid.uuid4().hex
        ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
        head = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
                f"filename=\"{name}\"\r\nContent-Type: {ctype}\r\n\r\n").encode()
        body = head + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
        req = urllib.request.Request(self.base + "/api/upload/media", data=body, method="POST",
                                     headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        try:
            with urllib.request.urlopen(req, timeout=300, context=_ctx()) as r:
                text = r.read().decode("utf-8", "replace")
        except Exception as e:                                     # noqa: BLE001
            raise InsightError(f"uploading {name} to Insight failed: {e}") from None
        if "Upload complete" not in text:
            raise InsightError(f"uploading {name} to Insight failed: {text.strip()[-300:]}")
        return name

    # -- streams --------------------------------------------------------------
    def _play(self, index: int, file: str, by_index: dict[int, dict]) -> str:
        src = by_index.get(index)
        if src is None:
            raise InsightError(f"Insight has no streaming source {index}")
        if src.get("file") != file:
            self._req("POST", "/api/mediasrc/assign", {"index": index, "file": file})
            src = {**src, "file": file, "state": "stopped"}
        if src.get("state") != "playing":
            self._req("POST", "/api/mediasrc/start", {"index": index}, timeout=30)
        return f"rtsp://127.0.0.1:8554/src{index}"

    def ensure_streams(self, count: int, clips: list[Path] | None = None,
                       log=lambda s: None) -> tuple[list[str], list[int]]:
        """Start `count` RTSP sources and return (their URLs, slot indexes).

        Without clips: the sample videos in slots 1..count, one video each
        (videos repeat when count > the number of samples). With clips: those
        files (uploaded first) in slots 41.. for this app."""
        with self.lock:
            by_index = {int(s["index"]): s for s in self.sources()}
            urls, slots = [], []
            if clips:
                for i in range(count):
                    clip = clips[i % len(clips)]
                    name = self.upload(clip, f"neat-apps-{clip.name}")
                    slot = APP_SLOT_BASE + i
                    log(f"Stream {i + 1}: {clip.name} -> src{slot}")
                    urls.append(self._play(slot, name, by_index))
                    slots.append(slot)
                return urls, slots
            have = self.cached_samples()
            if have == 0:
                raise InsightError("no sample videos yet: run Set Up Everything (or Get Sample Videos)")
            for i in range(count):
                n = (i % have) + 1
                name = self.upload(self.cache / self.sample_name(n))
                slot = i + 1
                log(f"Stream {i + 1}: {name} -> src{slot}")
                urls.append(self._play(slot, name, by_index))
                slots.append(slot)
            return urls, slots

    def stop_slots(self, slots: list[int]) -> None:
        for s in slots:
            try:
                self._req("POST", "/api/mediasrc/stop", {"index": s})
            except InsightError:
                pass
