"""Run jobs, launch, monitor and stop apps (and Insight).

Everything the launcher starts runs in its own session (setsid) with output
in logs/<id>.log, so it outlives a launcher restart and is found again by its
command line. Apps started by hand from a shell are found the same way and
can be stopped from here too.

The manager works on "units": anything with an id, a name, process_patterns()
and log_paths(). Apps, Insight and the platform setup are units.
"""
from __future__ import annotations

import os
import re
import signal
import ssl
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

import psutil


# -- MLA ----------------------------------------------------------------------
LEGACY_MLA_SERVICE = "simaai-appcomplex.service"      # firmware before 3.0
MLA_DEVICES = ("/dev/mla", "/dev/simaai-mlamem")


def legacy_mla_service() -> bool:
    """Is simaai-appcomplex.service installed (firmware before 3.0)?"""
    try:
        r = subprocess.run(["systemctl", "list-unit-files", LEGACY_MLA_SERVICE, "--no-legend"],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return LEGACY_MLA_SERVICE in r.stdout


def mla_users(devices: tuple[str, ...] = MLA_DEVICES, proc: Path = Path("/proc")) -> list[dict]:
    """The programs that have the MLA open (the ones this user can see)."""
    out = []
    for d in proc.iterdir():
        if not d.name.isdigit() or int(d.name) == os.getpid():
            continue
        try:
            fds = os.listdir(d / "fd")
        except OSError:
            continue
        for fd in fds:
            try:
                target = os.readlink(d / "fd" / fd)
            except OSError:
                continue
            if target in devices:
                try:
                    cmd = (d / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace").strip()
                except OSError:
                    cmd = ""
                out.append({"pid": int(d.name), "cmd": cmd})
                break
    return sorted(out, key=lambda u: u["pid"])

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07")
Emit = Callable[..., None]
STOP_GRACE = float(os.environ.get("NEAT_LAUNCHER_STOP_GRACE", "60"))  # MLA apps must free models; SIGKILL leaks MLA memory


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


class LogBuffer:
    """Recent output of one app as raw text chunks (ANSI kept for xterm)."""

    def __init__(self, max_chars: int = 400_000):
        self.max_chars = max_chars
        self.chunks: deque[str] = deque()
        self.size = 0
        self.lock = threading.Lock()

    def append(self, text: str) -> None:
        with self.lock:
            self.chunks.append(text)
            self.size += len(text)
            while self.size > self.max_chars and len(self.chunks) > 1:
                self.size -= len(self.chunks.popleft())

    def text(self) -> str:
        with self.lock:
            return "".join(self.chunks)

    def clear(self) -> None:
        with self.lock:
            self.chunks.clear()
            self.size = 0


@dataclass
class Job:
    kind: str                      # setup | download | launch | ...
    started: float = field(default_factory=time.time)
    proc: subprocess.Popen | None = None
    step: str = ""
    done: bool = False
    code: int | None = None
    error: str = ""




class Unit(Protocol):
    id: str
    name: str
    mla: bool

    def process_patterns(self) -> list: ...
    def log_paths(self) -> list[Path]: ...


@dataclass
class SimpleUnit:
    """A unit that is not an app: Insight, or the platform setup jobs."""
    id: str
    name: str
    patterns: list = field(default_factory=list)
    mla: bool = False

    def process_patterns(self) -> list:
        return list(self.patterns)

    def log_paths(self) -> list[Path]:
        return []


@dataclass
class Runtime:
    job: Job | None = None
    owned: subprocess.Popen | None = None
    launched_at: float = 0.0
    stopping: bool = False
    error: str = ""
    healthy: bool = False
    exit_code: int | None = None       # of the last run the launcher started
    finished_at: float = 0.0
    info: dict[str, Any] = field(default_factory=dict)   # per-run details (channels, slots, ...)
    log: LogBuffer = field(default_factory=LogBuffer)
    offsets: dict[str, int] = field(default_factory=dict)


def _ssl_ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def probe(url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout, context=_ssl_ctx()) as r:
            return r.status < 500
    except urllib.error.HTTPError as e:
        return e.code < 500
    except Exception:                                            # noqa: BLE001
        return False


class Manager:
    def __init__(self, units: list, logs_dir: str | Path, emit: Emit | None = None):
        self.units: dict[str, Any] = {u.id: u for u in units}
        self.order = [u.id for u in units]
        self.logs_dir = Path(logs_dir)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.emit = emit or (lambda *a, **k: None)
        self.rt = {u.id: Runtime() for u in units}
        self.system_log = LogBuffer()
        self.lock = threading.RLock()
        self._procs: dict[str, list[psutil.Process]] = {u: [] for u in self.units}
        self._pcache: dict[int, psutil.Process] = {}
        self._stop = threading.Event()
        self._tail_lock = threading.Lock()
        self.health: dict[str, Callable[[], bool]] = {}      # unit id -> health check
        self.on_exit: dict[str, Callable[[int], None]] = {}  # unit id -> called when its run ends
        self.on_push: Callable[[], None] = lambda: None

    # -- paths ---------------------------------------------------------------
    def run_log(self, uid: str) -> Path:
        return self.logs_dir / f"{uid}.log"

    def job_log(self, uid: str) -> Path:
        return self.logs_dir / f"{uid}-job.log"

    def note(self, uid: str, text: str, path: Path | None = None) -> None:
        """Write a launcher line (shown in the unit's log) to a log file."""
        line = f"\x1b[38;2;154;190;30m▸\x1b[0m \x1b[1m{text}\x1b[0m\n"
        with open(path or self.run_log(uid), "a", encoding="utf-8") as f:
            f.write(line)

    # -- process discovery ---------------------------------------------------
    def scan(self) -> None:
        """Find each unit's processes: the owned process group plus any
        process whose command line names one of the unit's patterns."""
        patterns = {uid: u.process_patterns() for uid, u in self.units.items()}
        owned_groups = {}
        for uid, rt in self.rt.items():
            if rt.owned is not None and rt.owned.poll() is None:
                owned_groups[rt.owned.pid] = uid
        found: dict[str, list[psutil.Process]] = {uid: [] for uid in self.units}
        seen = set()
        me = os.getpid()
        for p in psutil.process_iter(["pid", "cmdline"]):
            try:
                if p.pid == me:
                    continue
                cmd = " ".join(p.info["cmdline"] or [])
                if not cmd:
                    continue
                hit = None
                try:
                    pg = os.getpgid(p.pid)
                except OSError:
                    pg = None
                if pg in owned_groups:
                    hit = owned_groups[pg]
                else:
                    for uid, pats in patterns.items():
                        if any((pat.search(cmd) if hasattr(pat, "search") else pat in cmd) for pat in pats):
                            hit = uid
                            break
                if hit:
                    proc = self._pcache.get(p.pid)
                    if proc is None:
                        proc = p
                        proc.cpu_percent(None)     # prime
                        self._pcache[p.pid] = proc
                    found[hit].append(proc)
                    seen.add(p.pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        for pid in list(self._pcache):
            if pid not in seen:
                del self._pcache[pid]
        self._procs = found

    def procs(self, uid: str) -> list[psutil.Process]:
        return [p for p in self._procs.get(uid, []) if p.is_running()]

    def is_running(self, uid: str) -> bool:
        rt = self.rt[uid]
        return bool(self.procs(uid)) or (rt.owned is not None and rt.owned.poll() is None)

    def busy(self, uid: str) -> bool:
        job = self.rt[uid].job
        return job is not None and not job.done

    # -- state ---------------------------------------------------------------
    def base_state(self, uid: str) -> str:
        """job:<kind> | stopping | running | starting | error | finished | idle"""
        rt = self.rt[uid]
        if rt.job and not rt.job.done:
            return f"job:{rt.job.kind}"
        if self.is_running(uid):
            if rt.stopping:
                return "stopping"
            if uid in self.health and not rt.healthy:
                return "starting"
            return "running"
        if rt.error:
            return "error"
        if rt.exit_code is not None:
            return "finished"
        return "idle"

    def proc_info(self, uid: str) -> list[dict]:
        out = []
        for p in self.procs(uid):
            try:
                with p.oneshot():
                    out.append({
                        "pid": p.pid, "name": p.name(),
                        "cmd": " ".join(p.cmdline())[:240],
                        "cpu": round(p.cpu_percent(None), 1),
                        "rss": p.memory_info().rss,
                        "started": p.create_time(), "status": p.status(),
                    })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return out

    def snapshot(self, uid: str) -> dict:
        rt = self.rt[uid]
        procs = self.proc_info(uid)
        job = rt.job
        running = self.is_running(uid)
        return {
            "id": uid,
            "base": self.base_state(uid),
            "owned": rt.owned is not None and rt.owned.poll() is None,
            "healthy": rt.healthy,
            "error": rt.error,
            "exit_code": rt.exit_code,
            "finished_at": rt.finished_at,
            "launched_at": rt.launched_at if running else 0,
            "info": rt.info,
            "procs": procs,
            "cpu": round(sum(p["cpu"] for p in procs), 1),
            "rss": sum(p["rss"] for p in procs),
            "uptime": (time.time() - min(p["started"] for p in procs)) if procs else 0,
            "job": None if job is None else {
                "kind": job.kind, "step": job.step, "done": job.done,
                "code": job.code, "error": job.error,
                "elapsed": time.time() - job.started,
            },
        }

    # -- log tailing ---------------------------------------------------------
    def _tail_paths(self, uid: str) -> list[Path]:
        return [self.job_log(uid), self.run_log(uid), *self.units[uid].log_paths()]

    def tail_once(self) -> None:
        with self._tail_lock:                  # the monitor and fresh_log both tail
            self._tail_once()

    def _tail_once(self) -> None:
        for uid, rt in self.rt.items():
            for path in self._tail_paths(uid):
                key = str(path)
                try:
                    size = path.stat().st_size
                except OSError:
                    continue
                off = rt.offsets.get(key)
                if off is None:
                    off = max(0, size - 32_000)       # first sight: recent history
                if size < off:
                    off = 0                            # truncated / rotated
                if size == off:
                    rt.offsets[key] = off
                    continue
                with open(path, "rb") as f:
                    f.seek(off)
                    data = f.read(min(size - off, 256_000))
                rt.offsets[key] = off + len(data)
                text = data.decode("utf-8", "replace").replace("\r\n", "\n")
                text = text.replace("\n", "\r\n")
                rt.log.append(text)
                if rt.job and not rt.job.done:
                    steps = [ln for ln in strip_ansi(text).splitlines()
                             if ln.strip().startswith(("▸", "==>", "->", "✔", "✘"))]
                    if steps:
                        rt.job.step = steps[-1].strip(" ▸✔=->")[:140]
                self.emit("log", {"id": uid, "data": text}, to=f"log:{uid}")

    def fresh_log(self, uid: str) -> None:
        """Start the on-screen log afresh (a new run should not show the last
        run's output). The log files on disk keep everything."""
        rt = self.rt[uid]
        self.tail_once()                       # take what is already written...
        rt.log.clear()                         # ...and drop it from the live view
        self.emit("log_reset", {"id": uid}, to=f"log:{uid}")

    # -- jobs ----------------------------------------------------------------
    def env(self, extra: dict | None = None) -> dict:
        env = dict(os.environ)
        env.update({k: str(v) for k, v in (extra or {}).items()})
        env["PYTHONUNBUFFERED"] = "1"
        env["SIMA_CLI_CHECK_FOR_UPDATE"] = "0"
        env.setdefault("TERM", "xterm-256color")
        env.pop("NEAT_LAUNCHER_CONFIG", None)
        return env

    def run_job(self, uid: str, kind: str, script: str, cwd: Path,
                after: Callable[[int], None] | None = None, fresh: bool = True,
                title: str = "") -> Job:
        """Run a bash script as a job of this unit (one at a time). stdin is
        closed, so scripts that would ask a question take their defaults."""
        if self.busy(uid):
            raise RuntimeError("something is already running for this; wait for it to finish")
        u = self.units[uid]
        rt = self.rt[uid]
        job = Job(kind=kind)
        rt.job, rt.error = job, ""
        if fresh:
            self.fresh_log(uid)
        self.note(uid, f"{title or kind} · {u.name} · {time.strftime('%H:%M:%S')}", self.job_log(uid))

        def work():
            log = open(self.job_log(uid), "ab")
            try:
                job.proc = subprocess.Popen(
                    ["bash", "-c", "set -eo pipefail\n" + script], cwd=str(cwd), env=self.env(),
                    stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                    start_new_session=True)
                job.code = job.proc.wait()
            except Exception as e:                         # noqa: BLE001
                job.code, job.error = -1, str(e)
            finally:
                log.close()
            if job.code == 0:
                self.note(uid, f"{title or kind}: done", self.job_log(uid))
            else:
                job.error = job.error or f"{title or kind} failed (exit {job.code}): see the log"
                rt.error = job.error
                self.note(uid, job.error, self.job_log(uid))
            if after:
                try:
                    after(job.code or 0)
                except Exception as e:                     # noqa: BLE001
                    rt.error = job.error = str(e)
                    self.note(uid, str(e), self.job_log(uid))
            self.tail_once()
            job.done = True
            self.push()

        threading.Thread(target=work, daemon=True, name=f"{kind}-{uid}").start()
        self.push()
        return job

    def cancel_job(self, uid: str) -> None:
        job = self.rt[uid].job
        if job and job.proc and job.proc.poll() is None:
            os.killpg(job.proc.pid, signal.SIGTERM)

    # -- launch / stop ---------------------------------------------------------
    def launch(self, uid: str, command: str | Callable[[], str], cwd: Path,
               env: dict | None = None, exclusive: bool = True,
               prepare: Callable[[Callable[[str], None]], None] | None = None,
               title: str = "") -> None:
        """Start a unit. With exclusive=True, every other running MLA unit is
        stopped first. `prepare(log)` runs before the process starts (e.g. to
        set up streams and write the config) and may raise to abort; a
        callable `command` is evaluated after it."""
        u = self.units[uid]
        rt = self.rt[uid]
        if self.busy(uid):
            raise RuntimeError("something is already running for this; wait for it to finish")
        if self.is_running(uid):
            return
        rt.error, rt.stopping, rt.healthy, rt.exit_code = "", False, False, None
        rt.job = Job(kind="launch")
        self.fresh_log(uid)

        def log(text: str) -> None:
            self.note(uid, text)

        def work():
            try:
                if exclusive and getattr(u, "mla", False):
                    for other in self.order:
                        if other != uid and getattr(self.units[other], "mla", False) and self.is_running(other):
                            log(f"Stopping {self.units[other].name} to free the MLA")
                            self.stop(other, wait=True)
                if prepare:
                    rt.job.step = "Preparing"
                    prepare(log)
                cmd = command() if callable(command) else command
                log(f"{title or 'Launching'} {u.name} · {time.strftime('%H:%M:%S')}")
                f = open(self.run_log(uid), "ab")
                rt.owned = subprocess.Popen(
                    ["bash", "-c", cmd], cwd=str(cwd), env=self.env(env),
                    stdin=subprocess.DEVNULL, stdout=f, stderr=subprocess.STDOUT,
                    start_new_session=True)
                f.close()
                rt.launched_at = time.time()
                rt.job.done = True
                threading.Thread(target=self._watch_owned, args=(uid, rt.owned),
                                 daemon=True).start()
            except Exception as e:                           # noqa: BLE001
                rt.error = str(e)
                rt.job.done, rt.job.error = True, str(e)
                log(f"✘ {e}")
                cb = self.on_exit.get(uid)
                if cb:
                    try:
                        cb(-1)
                    except Exception:                          # noqa: BLE001
                        pass
            self.tail_once()
            self.push()

        threading.Thread(target=work, daemon=True, name=f"launch-{uid}").start()
        self.push()

    def _watch_owned(self, uid: str, proc: subprocess.Popen) -> None:
        code = proc.wait()
        rt = self.rt[uid]
        if rt.owned is not proc:
            return
        stopped = rt.stopping or code in (-signal.SIGTERM, -signal.SIGKILL, -signal.SIGINT, 143, 137, 130)
        # A run the user stopped is not a result: the app goes back to "ready".
        rt.exit_code, rt.finished_at = (None if stopped else code), time.time()
        if code != 0 and not stopped:
            rt.error = f"exited with code {code}: see the log"
        self.note(uid, "Stopped" if stopped else f"Finished (exit {code})")
        cb = self.on_exit.get(uid)
        if cb:
            try:
                cb(code)
            except Exception as e:                           # noqa: BLE001
                self.system_log.append(f"on_exit {uid}: {e}\r\n")
        self.tail_once()
        self.push()

    def _signal(self, uid: str, sig: int) -> None:
        """Signal every process of a unit exactly once (a second SIGTERM can
        interrupt an app's own clean shutdown): its owned process group, the
        matched processes (their groups when they lead one), and their children."""
        rt = self.rt[uid]
        done: set[int] = set()

        def group_pids(pg: int) -> set[int]:
            out = set()
            for p in psutil.process_iter(["pid"]):
                try:
                    if os.getpgid(p.pid) == pg:
                        out.add(p.pid)
                except OSError:
                    pass
            return out

        def kill_group(pg: int) -> None:
            members = group_pids(pg)
            if members - done:
                try:
                    os.killpg(pg, sig)
                except OSError:
                    pass
            done.update(members)

        if rt.owned is not None and rt.owned.poll() is None:
            kill_group(rt.owned.pid)
        for p in self.procs(uid):
            try:
                kids = p.children(recursive=True)
            except psutil.Error:
                kids = []
            try:
                pg = os.getpgid(p.pid)
            except OSError:
                pg = None
            if p.pid not in done:
                if pg == p.pid:                             # a group leader: take its group
                    kill_group(pg)
                else:
                    try:
                        p.send_signal(sig)
                    except psutil.Error:
                        pass
                    done.add(p.pid)
            for k in kids:
                if k.pid not in done:
                    try:
                        k.send_signal(sig)
                    except psutil.Error:
                        pass
                    done.add(k.pid)

    def stop(self, uid: str, wait: bool = False, force: bool = False) -> None:
        """Stop gracefully (SIGTERM), then SIGKILL whatever is left after the
        grace period. force=True kills at once. Apps on the MLA need the grace
        period to free their models: SIGKILL leaks MLA memory."""
        rt = self.rt[uid]

        def work():
            rt.stopping = True
            self.push()
            try:
                if force:
                    self.note(uid, "Force kill (SIGKILL)")
                    self._signal(uid, signal.SIGKILL)
                else:
                    self.note(uid, "Stopping")
                    self._signal(uid, signal.SIGTERM)
                    deadline = time.time() + STOP_GRACE
                    while time.time() < deadline:
                        self.scan()
                        if not self.is_running(uid):
                            break
                        time.sleep(0.25)
                    self.scan()
                    if self.is_running(uid):
                        self.note(uid, f"Still running after {STOP_GRACE:.0f}s: SIGKILL")
                        self._signal(uid, signal.SIGKILL)
                time.sleep(0.3)
                self.scan()
            except Exception as e:                               # noqa: BLE001
                rt.error = str(e)
            finally:
                rt.stopping, rt.healthy = False, False
                if not force:
                    rt.error = ""
                self.push()

        if wait:
            work()
        else:
            threading.Thread(target=work, daemon=True, name=f"stop-{uid}").start()

    def kill_pid(self, pid: int) -> None:
        """Kill one process, only if it belongs to a unit."""
        for uid in self.order:
            if any(p.pid == pid for p in self.procs(uid)):
                os.kill(pid, signal.SIGKILL)
                return
        raise RuntimeError(f"pid {pid} does not belong to an app or Insight")

    def clear(self, uid: str) -> None:
        rt = self.rt[uid]
        rt.error, rt.exit_code = "", None
        self.push()

    # -- system --------------------------------------------------------------
    def reset_mla(self, grace: float = 60.0) -> dict:
        """Free every model on the MLA. Before 3.0 the models live in
        simaai-appcomplex.service, which is restarted. From 3.0 that service is
        gone and each program holds its own models, so the programs that have the
        MLA open are stopped (SIGTERM, then SIGKILL after `grace` seconds: a
        clean exit is what frees their MLA memory)."""
        if legacy_mla_service():
            cmd = ["sudo", "-n", "systemctl", "restart", LEGACY_MLA_SERVICE]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            msg = f"$ {' '.join(cmd)}\r\n{(r.stdout + r.stderr).replace(chr(10), chr(13) + chr(10))}"
            self.system_log.append(msg + f"exit {r.returncode}\r\n")
            if r.returncode != 0:
                raise RuntimeError((r.stderr or r.stdout).strip() or f"exit {r.returncode}")
            return {"method": "service", "stopped": []}
        users = mla_users()
        self.system_log.append(f"Reset MLA: {len(users)} program(s) have the MLA open\r\n")
        procs, denied = [], []
        for u in users:
            try:
                p = psutil.Process(u["pid"])
                p.terminate()
                procs.append(p)
                self.system_log.append(f"  SIGTERM {u['pid']} {u['cmd'][:120]}\r\n")
            except psutil.NoSuchProcess:
                pass
            except psutil.AccessDenied:
                denied.append(u)
        _, alive = psutil.wait_procs(procs, timeout=grace)
        for p in alive:
            try:
                p.kill()
                self.system_log.append(f"  SIGKILL {p.pid} (still running after {grace:.0f} s)\r\n")
            except psutil.NoSuchProcess:
                pass
        psutil.wait_procs(alive, timeout=5)
        self.push()
        if denied:
            raise RuntimeError("not allowed to stop " + ", ".join(str(u["pid"]) for u in denied)
                               + " (another user's program): stop it with sudo kill from the Shell tab")
        return {"method": "stop", "stopped": [p.pid for p in procs]}

    # -- loop ----------------------------------------------------------------
    def push(self) -> None:
        try:
            self.on_push()
        except Exception as e:                               # noqa: BLE001
            self.system_log.append(f"push error: {e}\r\n")

    def check_health(self) -> None:
        for uid in self.order:
            check = self.health.get(uid)
            if check is None:
                continue
            self.rt[uid].healthy = bool(self.is_running(uid) and check())

    def loop(self, sysinfo: Callable[[], dict] | None = None, period: float = 2.0) -> None:
        last = 0.0
        while not self._stop.is_set():
            try:
                self.tail_once()
                if time.time() - last >= period:
                    last = time.time()
                    self.scan()
                    self.check_health()
                    self.push()
                    if sysinfo:
                        self.emit("system", sysinfo())
            except Exception as e:                       # noqa: BLE001
                self.system_log.append(f"monitor error: {e}\r\n")
            self._stop.wait(0.4)

    def start(self, sysinfo=None) -> threading.Thread:
        self.scan()
        t = threading.Thread(target=self.loop, args=(sysinfo,), daemon=True, name="monitor")
        t.start()
        return t

    def shutdown(self) -> None:
        self._stop.set()
