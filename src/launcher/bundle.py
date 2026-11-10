"""The platform under the apps: the board's firmware, sima-cli, the prebuilt
apps bundle, Neat Core, Insight, the pyneat Python environment and the
Developer Portal sign-in. Reports what is there and builds the shell steps
that install what is missing.

The bundle is what `sima-cli neat install apps` installs: prebuilt C++
binaries and Python sources in <install_root>/prebuilt-apps, plus the
matching Core and Insight. Running it again upgrades in place; sima-cli keeps
models/ and backs up the rest to .prebuilt-apps-backup/.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import threading
import time
import urllib.request

from pathlib import Path

from insight import Insight
from registry import Settings

# Where `sima-cli neat install apps` resolves "latest" (sima_cli/vulcan/artifacts.py).
APPS_ARTIFACTS = "https://artifacts.neat.sima.ai/apps"
# The official sima-cli installer (docs.sima.ai); on a DevKit it installs into /data/sima-cli.
SIMA_CLI_INSTALLER = "https://artifacts.neat.sima.ai/sima-cli/linux-mac.sh"
# Neat Studio Launcher, the home of the full studios (GenAI, Decision, Change, DINO).
STUDIO_LAUNCHER_INSTALLER = "https://raw.githubusercontent.com/dotimothy/neat-studio-launcher/main/install.sh"
STUDIO_LAUNCHER_DIR = "neat-studio-launcher"        # in install_root
BUILDINFO = Path("/etc/buildinfo")
NVME_PYNEAT = Path("/media/nvme/pyneat")      # where Neat Core's installer puts the venv on a DevKit


def version_tuple(v: str) -> tuple[int, ...]:
    """'3.0.0_master_B2501' -> (3, 0, 0); 'v0.6.0+main.88bf' -> (0, 6, 0)."""
    m = re.match(r"v?(\d+(?:\.\d+)*)", (v or "").strip())
    return tuple(int(x) for x in m.group(1).split(".")) if m else ()


def firmware_version(path: Path = BUILDINFO) -> str:
    """The board's platform build, e.g. '3.0.0' (from SIMA_BUILD_VERSION = 3.0.0_master_B2501)."""
    try:
        for line in path.read_text().splitlines():
            if line.startswith("SIMA_BUILD_VERSION"):
                return line.split("=", 1)[1].strip().split("_", 1)[0]
    except OSError:
        pass
    return ""


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
                "core_ref": core.get("ref", ""), "core_spec": core.get("spec", ""), "apps": n}

    def core(self) -> dict:
        """Neat Core (the sima-neat package), and whether it is the one the bundle
        was built for (deps/neat-core.json in the bundle names it)."""
        def ver():
            out = _run(shlex.split(self.s.core_query))
            return out.strip() if out and "no packages" not in out.lower() else ""
        v = self._cached("core-ver", 300, ver)
        b = self.bundle()
        ref, spec = b.get("core_ref", ""), b.get("core_spec", "")
        want = version_tuple(ref)
        if want:                                  # a release: 0.6.1 matches 0.6.1+main.f2d49f4ac6b7
            matches, wanted = version_tuple(v)[:len(want)] == want, ref.lstrip("v")
        elif re.fullmatch(r"[0-9a-f]{7,40}", spec or ""):   # a branch build: its commit is in the version
            matches, wanted = spec[:12] in v, f"{ref or 'main'} {spec[:12]}"
        else:
            matches, wanted = True, ""
        return {"present": bool(v), "version": v, "wanted": wanted, "ok": bool(v) and matches}

    def firmware(self) -> dict:
        """The board's platform build against what the apps need: the model zoo's
        major.minor (3.0 for model zoo 3.0.0), or platform_version in apps.yaml."""
        v = firmware_version()
        need = self.required_platform()
        ok = not need or not v or version_tuple(v)[:2] >= version_tuple(need)[:2]
        return {"version": v, "required": need, "ok": ok}

    def required_platform(self) -> str:
        if self.s.platform_version:
            return str(self.s.platform_version)
        parts = version_tuple(self.s.modelzoo_version)
        return ".".join(map(str, parts[:2])) if len(parts) >= 2 else ""

    def studio_launcher(self) -> dict:
        """Neat Studio Launcher: installed next to the bundle, and answering on its port."""
        d = self.s.install_root / STUDIO_LAUNCHER_DIR
        port = self.s.studio_launcher_port

        def probe():
            import ssl
            ctx = ssl.create_default_context()
            ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE   # its certificate is self-signed
            for scheme in ("https", "http"):
                try:
                    with urllib.request.urlopen(f"{scheme}://127.0.0.1:{port}/health", timeout=1.5,
                                                context=ctx if scheme == "https" else None) as r:
                        body = json.loads(r.read() or b"{}")
                    if body.get("app") == "neat-studio-launcher":
                        return {"running": True, "version": str(body.get("version", ""))}
                except Exception:                                  # noqa: BLE001
                    continue
            return {"running": False, "version": ""}
        state = self._cached("studio-launcher", 10, probe)
        return {"installed": (d / "run.sh").is_file(), "dir": str(d), "port": port, **state}

    def studio_launcher_install_script(self) -> str:
        root = shlex.quote(str(self.s.install_root))
        return ('echo "▸ Installing Neat Studio Launcher"\n'
                f"cd {root}\n"
                f"curl -fsSL {STUDIO_LAUNCHER_INSTALLER} | STUDIO_START=1 STUDIO_DIR={STUDIO_LAUNCHER_DIR} bash < /dev/null\n"
                f'echo "✔ Neat Studio Launcher: https://<board>:{self.s.studio_launcher_port}/"\n')

    def studio_launcher_start_script(self) -> str:
        d = shlex.quote(str(self.s.install_root / STUDIO_LAUNCHER_DIR))
        return ('echo "▸ Starting Neat Studio Launcher"\n'
                f"{d}/run.sh --daemon --port {int(self.s.studio_launcher_port)}\n")

    def sudo(self) -> dict:
        """Can the setup steps use sudo: without a password, or with one the launcher was given?"""
        def check():
            try:
                return subprocess.run(["sudo", "-n", "true"], capture_output=True, timeout=5,
                                      stdin=subprocess.DEVNULL).returncode == 0
            except Exception:                                      # noqa: BLE001
                return False
        configured = bool(self.s.sudo_password or os.environ.get("SUDO_PASSWORD") or os.environ.get("DEVKIT_PASSWORD"))
        return {"passwordless": self._cached("sudo", 60, check), "password_set": configured}

    @staticmethod
    def signed_in() -> dict:
        """Signed in to the Developer Portal with sima-cli (models download through it).
        sima-cli keeps its tokens in ~/.sima-cli and refreshes them itself."""
        return {"present": (Path.home() / ".sima-cli" / ".tokens.json").is_file()}

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
        """The Python environment with pyneat. Neat Core's installer makes it at
        /media/nvme/pyneat and links ~/pyneat to it; without the link it can be
        relinked (`linkable`), otherwise installing Core makes it."""
        py = self.s.pyneat / "bin" / "python"
        linkable = (not py.exists() and not os.path.lexists(self.s.pyneat)
                    and (NVME_PYNEAT / "bin" / "python").exists())
        return {"present": py.exists(), "path": str(self.s.pyneat), "linkable": linkable}

    def status(self) -> dict:
        b = self.bundle()
        latest = self.latest()
        tag = latest.get("tag", "")
        update = bool(b["present"] and tag and b["spec"] and not (b["spec"].startswith(tag) or tag.startswith(b["spec"])))
        insight = {"present": self.insight.installed(), "version": self.insight.version(),
                   "port": self.s.insight_port, "scheme": self.insight.scheme}
        cli = self.sima_cli()
        fw = self.firmware()
        core = self.core()
        samples = {"cached": self.insight.cached_samples(), "total": self.insight.sample_count(),
                   "images_cached": self.images.cached() if self.images else 0,
                   "images_total": self.images.total() if self.images else 0}
        return {
            "sima_cli": cli, "bundle": {**b, "latest": tag, "latest_error": latest.get("error", ""),
                                         "update": update, "branch": self.s.bundle_branch},
            "core": core, "insight": insight, "pyneat": self.pyneat(), "samples": samples,
            "ffmpeg": self.ffmpeg(), "firmware": fw, "signin": self.signed_in(),
            "studio_launcher": self.studio_launcher(), "sudo": self.sudo(),
            "ready": bool(fw["ok"] and cli["present"] and b["present"] and core["ok"]
                          and insight["present"] and self.ffmpeg()["present"]),
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

    @staticmethod
    def sima_cli_script() -> str:
        """sima-cli itself, with the official installer (into /data/sima-cli on a DevKit)."""
        return ('echo "▸ Installing sima-cli"\n'
                f"curl -fsSL {SIMA_CLI_INSTALLER} | bash -s < /dev/null\n"
                'echo "✔ sima-cli installed"\n')

    def core_script(self) -> str:
        """Neat Core: its runtime packages and the pyneat venv, as the apps' own installer
        does it (`sima-cli neat install -t minimal core`). The installer uses sudo, with
        SUDO_PASSWORD / DEVKIT_PASSWORD or the DevKit's default password."""
        return ('echo "▸ Installing Neat Core (runtime packages and the pyneat Python environment)"\n'
                'tmp=$(mktemp -d)\n'
                'rc=0\n'
                f'( cd "$tmp" && {self._cli()} neat install -d . -t minimal core ) || rc=$?\n'
                'rm -rf "$tmp"\n'
                '[ $rc -eq 0 ] || { echo "✘ Neat Core did not install (exit $rc)"; exit 5; }\n'
                f'echo "✔ Neat Core $({self._core_query_sh()}) installed"\n'
                + self.pyneat_link_script())

    def _core_query_sh(self) -> str:
        return f"{self.s.core_query} 2>/dev/null"

    def pyneat_link_script(self) -> str:
        home = shlex.quote(str(self.s.pyneat))
        nvme = shlex.quote(str(NVME_PYNEAT))
        return (f'if [ ! -e {home} ] && [ -x {nvme}/bin/python ]; then\n'
                f'  ln -s {nvme} {home} && echo "✔ Linked {self.s.pyneat} to {NVME_PYNEAT}"\n'
                'fi\n')

    def core_step_script(self) -> str:
        """In Set Up Everything, after the bundle: install Core when it is missing or is
        not the one the bundle names (the check runs then, against the new bundle)."""
        deps = shlex.quote(str(self.s.bundle / "deps" / "neat-core.json"))
        read = ("import json,sys; c=json.load(open(sys.argv[1])).get('neat-core') or {}; "
                "print(c.get('ref','').lstrip('v'), c.get('spec',''))")
        return (f'read -r want spec <<< "$(python3 -c "{read}" {deps} 2>/dev/null || true)"\n'
                f'have=$({self._core_query_sh()} || true)\n'
                '# a release (0.6.1) matches by its version; a branch build (main) by its commit\n'
                'case "$want" in [0-9]*) match="$want"; [ "${have#"$want"}" != "$have" ] && ok=1 || ok=0 ;;\n'
                '  *) match="${spec:0:12}"; case "$have" in *"$match"*) ok=1 ;; *) ok=0 ;; esac; [ -n "$match" ] || ok=1 ;;\n'
                'esac\n'
                'if [ -n "$have" ] && [ "$ok" = 1 ]; then\n'
                '  echo "✔ Neat Core $have already installed"\n'
                + "".join("  " + line + "\n" for line in self.pyneat_link_script().splitlines()) +
                'else\n'
                '  [ -n "$have" ] && echo "▸ Neat Core $have is installed; the bundle is built for ${want} ${match}"\n'
                + "".join("  " + line + "\n" for line in self.core_script().splitlines()) +
                'fi\n')

    def firmware_gate_script(self) -> str:
        fw = self.firmware()
        if fw["ok"]:
            return ""
        return (f'echo "✘ The board runs platform {fw["version"]}; these apps need {fw["required"]} or newer."\n'
                'echo "  Update the firmware with: sima-cli update   (in the Shell; the board reboots), then run setup again."\n'
                'exit 7\n')

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
        parts = [self.firmware_gate_script()]
        if not self.bundle()["present"] or upgrade:
            parts.append(self.bundle_script())
        else:
            parts.append(f'echo "✔ Apps bundle already in {self.s.bundle}"\n')
        parts.append(self.core_step_script())
        # The bundle install normally brings Insight too; check when the step runs.
        parts.append(f'if [ ! -x {shlex.quote(str(self.insight.binary))} ]; then\n'
                     + self.insight_script()
                     + 'else\necho "✔ Neat Insight already installed"\nfi\n')
        parts.append(self.ffmpeg_script())
        parts.append(self.insight.fetch_samples_script())
        if self.images:
            parts.append(self.images.fetch_script())
        return "".join(parts)

    # sudo for the setup steps: without a password where the board allows it, else with
    # SUDO_PASSWORD / DEVKIT_PASSWORD (Neat Core's own installer reads them too). The launcher
    # sets SUDO_PASSWORD for one job from what the user typed, or from sudo_password in
    # config.local.yaml; it never stores or logs a password.
    AS_ROOT = (
        'as_root() {\n'
        '  if sudo -n true 2>/dev/null; then sudo -n "$@"; return; fi\n'
        '  local pw\n'
        '  for pw in "${SUDO_PASSWORD:-}" "${DEVKIT_PASSWORD:-}"; do\n'
        '    [ -n "$pw" ] || continue\n'
        '    if printf \'%s\\n\' "$pw" | sudo -S -p \'\' -v 2>/dev/null; then printf \'%s\\n\' "$pw" | sudo -S -p \'\' "$@"; return; fi\n'
        '  done\n'
        '  echo "✘ sudo needs a password: enter it when Set Up Everything asks, or run in the Shell: sudo $*"\n'
        '  return 6\n'
        '}\n')

    @classmethod
    def ffmpeg_script(cls) -> str:
        """Insight streams the sample videos with ffmpeg. The board's apt has it."""
        return (cls.AS_ROOT +
                'if command -v ffmpeg >/dev/null && command -v ffprobe >/dev/null; then\n'
                '  echo "✔ ffmpeg already installed"\n'
                'else\n'
                '  echo "▸ Installing ffmpeg (Insight streams the sample videos with it)"\n'
                '  as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y ffmpeg < /dev/null '
                '|| { as_root apt-get update < /dev/null && as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y ffmpeg < /dev/null; } '
                '|| exit 6\n'
                '  echo "✔ ffmpeg installed"\n'
                'fi\n')
