"""The platform under the apps: sima-cli, the prebuilt apps bundle, Core,
Insight and the pyneat Python environment. Reports what is there and builds
the shell steps that install what is missing.

The bundle is what `sima-cli neat install apps` installs: prebuilt C++
binaries and Python sources in <install_root>/prebuilt-apps, plus the
matching Core and Insight. Running it again upgrades in place; sima-cli keeps
models/ and backs up the rest to .prebuilt-apps-backup/.
"""
from __future__ import annotations

import json
import re
import shlex
import subprocess
import threading
import time
import urllib.request

from insight import Insight
from registry import Settings

# Where `sima-cli neat install apps` resolves "latest" (sima_cli/vulcan/artifacts.py).
APPS_ARTIFACTS = "https://artifacts.neat.sima.ai/apps"


def _run(cmd: list[str], timeout: float = 20.0) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           env={"SIMA_CLI_CHECK_FOR_UPDATE": "0", "PATH": "/usr/bin:/bin:/usr/local/bin"},
                           stdin=subprocess.DEVNULL)
        return (r.stdout or "") + (r.stderr or "")
    except Exception:                                              # noqa: BLE001
        return ""


class Platform:
    def __init__(self, settings: Settings, insight: Insight, images=None):
        self.s = settings
        self.insight = insight
        self.images = images                     # samples.SampleImages
        self._cache: dict[str, tuple[float, object]] = {}
        self._lock = threading.Lock()

    def _cached(self, key: str, ttl: float, fn):
        with self._lock:
            hit = self._cache.get(key)
            if hit and time.time() - hit[0] < ttl:
                return hit[1]
        val = fn()
        with self._lock:
            self._cache[key] = (time.time(), val)
        return val

    def forget(self) -> None:
        with self._lock:
            self._cache.clear()

    # -- pieces ---------------------------------------------------------------
    def sima_cli(self) -> dict:
        path = self.s.sima_cli_bin()
        if not path:
            return {"present": False, "path": "", "version": ""}

        def ver():
            m = re.search(r"version:?\s*([0-9][\w.+-]*)", _run([path, "--version"]), re.I)
            return m.group(1) if m else ""
        return {"present": True, "path": path, "version": self._cached("cli-ver", 3600, ver)}

    def bundle(self) -> dict:
        b = self.s.bundle
        deps = b / "deps"

        def read(name: str, key: str) -> dict:
            try:
                return json.loads((deps / name).read_text()).get(key) or {}
            except Exception:                                      # noqa: BLE001
                return {}
        apps = read("neat-apps.json", "neat-apps")
        core = read("neat-core.json", "neat-core")
        n = len(list(b.glob("examples/*/*/src"))) if b.is_dir() else 0
        return {"present": (b / "examples").is_dir(), "dir": str(b),
                "ref": apps.get("ref", ""), "spec": apps.get("spec", ""),
                "core_ref": core.get("ref", ""), "apps": n}

    def core(self) -> dict:
        def ver():
            out = _run(["dpkg-query", "-W", "-f=${Version}", "sima-neat"])
            return out.strip() if out and "no packages" not in out.lower() else ""
        v = self._cached("core-ver", 300, ver)
        return {"present": bool(v), "version": v}

    def latest(self) -> dict:
        """The newest published bundle for our branch (cached 15 min)."""
        key = self.s.bundle_branch.replace("/", "-").replace(" ", "-")

        def fetch():
            try:
                req = urllib.request.Request(f"{APPS_ARTIFACTS}/{key}/latest.tag",
                                             headers={"User-Agent": "curl/8 neat-apps-launcher"})
                with urllib.request.urlopen(req, timeout=6) as r:
                    return {"tag": r.read().decode().strip(), "error": ""}
            except Exception as e:                                 # noqa: BLE001
                return {"tag": "", "error": str(e)}
        return self._cached("latest", 900, fetch)                  # type: ignore[return-value]

    def ffmpeg(self) -> dict:
        """Insight's streaming sources run ffmpeg and probe files with ffprobe."""
        import shutil
        return {"present": bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))}

    def pyneat(self) -> dict:
        py = self.s.pyneat / "bin" / "python"
        return {"present": py.exists(), "path": str(self.s.pyneat)}

    def status(self) -> dict:
        b = self.bundle()
        latest = self.latest()
        tag = latest.get("tag", "")
        update = bool(b["present"] and tag and b["spec"] and not (b["spec"].startswith(tag) or tag.startswith(b["spec"])))
        insight = {"present": self.insight.installed(), "version": self.insight.version(),
                   "port": self.s.insight_port, "scheme": self.insight.scheme}
        cli = self.sima_cli()
        samples = {"cached": self.insight.cached_samples(), "total": self.insight.sample_count(),
                   "images_cached": self.images.cached() if self.images else 0,
                   "images_total": self.images.total() if self.images else 0}
        return {
            "sima_cli": cli, "bundle": {**b, "latest": tag, "latest_error": latest.get("error", ""),
                                         "update": update, "branch": self.s.bundle_branch},
            "core": self.core(), "insight": insight, "pyneat": self.pyneat(), "samples": samples,
            "ffmpeg": self.ffmpeg(),
            "ready": bool(cli["present"] and b["present"] and insight["present"] and self.ffmpeg()["present"]),
            "install_root": str(self.s.install_root),
            "catalog": self.s.catalog_source, "modelzoo_version": self.s.modelzoo_version,
        }

    # -- install steps ------------------------------------------------------------
    def _cli(self) -> str:
        cli = self.s.sima_cli_bin()
        if not cli:
            raise RuntimeError("sima-cli was not found. Install it first: "
                               "https://docs.sima.ai (sima-cli install guide)")
        return f"SIMA_CLI_CHECK_FOR_UPDATE=0 {shlex.quote(cli)}"

    def bundle_script(self) -> str:
        """Install or upgrade the apps bundle (with its Core and Insight).

        The bundle's installer asks before it replaces an existing
        prebuilt-apps; NEAT_APPS_OVERWRITE=1 answers yes (the launcher asks
        the user first). It keeps models/ and backs up the rest."""
        root = shlex.quote(str(self.s.install_root))
        branch = self.s.bundle_branch
        target = "apps" if branch == "main" else f"apps@{branch}"
        return (f'echo "▸ Installing the Neat apps bundle ({branch}) into {self.s.bundle}"\n'
                f"mkdir -p {root}\ncd {root}\n"
                f"NEAT_APPS_OVERWRITE=1 {self._cli()} neat install -d {root} {shlex.quote(target)}\n"
                f'[ -d {shlex.quote(str(self.s.bundle / "examples"))} ] || '
                f'{{ echo "✘ The bundle was not found in {self.s.bundle} after installing"; exit 5; }}\n'
                f'echo "✔ Apps bundle installed in {self.s.bundle}"\n')

    def insight_script(self) -> str:
        return ('echo "▸ Installing Neat Insight"\n'
                'tmp=$(mktemp -d)\n'
                f'( cd "$tmp" && {self._cli()} neat install -d . insight )\n'
                'rm -rf "$tmp"\n'
                f'[ -x {shlex.quote(str(self.insight.binary))} ] || '
                f'{{ echo "✘ neat-insight was not found at {self.insight.binary}"; exit 5; }}\n'
                'echo "✔ Neat Insight installed"\n')

    def setup_script(self, upgrade: bool = False) -> str:
        """Everything an out-of-box run needs, skipping what is there."""
        parts = []
        if not self.bundle()["present"] or upgrade:
            parts.append(self.bundle_script())
        else:
            parts.append(f'echo "✔ Apps bundle already in {self.s.bundle}"\n')
        # The bundle install normally brings Insight too; check when the step runs.
        parts.append(f'if [ ! -x {shlex.quote(str(self.insight.binary))} ]; then\n'
                     + self.insight_script()
                     + 'else\necho "✔ Neat Insight already installed"\nfi\n')
        parts.append(self.ffmpeg_script())
        parts.append(self.insight.fetch_samples_script())
        if self.images:
            parts.append(self.images.fetch_script())
        return "".join(parts)

    @staticmethod
    def ffmpeg_script() -> str:
        """Insight streams the sample videos with ffmpeg. The board's apt has it;
        installing needs sudo (passwordless on the DevKit's sima user)."""
        return ('if command -v ffmpeg >/dev/null && command -v ffprobe >/dev/null; then\n'
                '  echo "✔ ffmpeg already installed"\n'
                'else\n'
                '  echo "▸ Installing ffmpeg (Insight streams the sample videos with it)"\n'
                '  if sudo -n true 2>/dev/null; then\n'
                '    sudo -n env DEBIAN_FRONTEND=noninteractive apt-get install -y ffmpeg '
                '|| { sudo -n apt-get update && sudo -n env DEBIAN_FRONTEND=noninteractive apt-get install -y ffmpeg; }\n'
                '    echo "✔ ffmpeg installed"\n'
                '  else\n'
                '    echo "✘ ffmpeg is missing and sudo needs a password: run sudo apt-get install -y ffmpeg in the Shell"; exit 6\n'
                '  fi\n'
                'fi\n')
