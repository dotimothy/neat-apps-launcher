"""Interactive shells for the browser (xterm.js over Socket.IO).

Each browser connection gets its own login shell in a PTY, running as the
launcher's user. The shell ends when the tab disconnects or closes it.
"""
from __future__ import annotations

import fcntl
import os
import pty
import select
import signal
import struct
import termios
import threading
from typing import Callable


class Session:
    def __init__(self, on_output: Callable[[str], None], on_exit: Callable[[int], None],
                 cols: int = 120, rows: int = 32, cwd: str | None = None,
                 shell: str | None = None):
        self.on_output, self.on_exit = on_output, on_exit
        shell = shell or os.environ.get("SHELL") or "/bin/bash"
        pid, fd = pty.fork()
        if pid == 0:                                       # child
            try:
                os.chdir(cwd or os.path.expanduser("~"))
            except OSError:
                pass
            # The launcher runs in the background (nohup, &), so it ignores SIGINT,
            # SIGQUIT and SIGHUP, and Python ignores SIGPIPE. A shell would pass that
            # on to every command, and Ctrl+C would stop nothing: restore the defaults.
            for sig in ("SIGINT", "SIGQUIT", "SIGHUP", "SIGPIPE", "SIGXFSZ", "SIGTERM",
                        "SIGTSTP", "SIGTTIN", "SIGTTOU"):
                if hasattr(signal, sig):
                    signal.signal(getattr(signal, sig), signal.SIG_DFL)
            env = dict(os.environ, TERM="xterm-256color", COLORTERM="truecolor")
            env.pop("NEAT_LAUNCHER_CONFIG", None)
            os.execvpe(shell, [shell, "-l"], env)
        self.pid, self.fd = pid, fd
        self.alive = True
        self._next = 0
        self._held: dict[int, str] = {}
        self._lock = threading.Lock()
        self.resize(cols, rows)
        self._t = threading.Thread(target=self._read, daemon=True, name=f"pty-{pid}")
        self._t.start()

    def _read(self) -> None:
        while self.alive:
            try:
                r, _, _ = select.select([self.fd], [], [], 0.5)
                if not r:
                    continue
                data = os.read(self.fd, 65536)
            except OSError:
                break
            if not data:
                break
            self.on_output(data.decode("utf-8", "replace"))
        self.alive = False
        code = 0
        try:
            _, status = os.waitpid(self.pid, 0)
            code = os.waitstatus_to_exitcode(status)
        except ChildProcessError:
            pass
        try:
            os.close(self.fd)
        except OSError:
            pass
        self.on_exit(code)

    def write(self, data: str) -> None:
        if self.alive:
            try:
                os.write(self.fd, data.encode())
            except OSError:
                pass

    def write_seq(self, seq: int, data: str) -> None:
        """Socket events can be handled on different threads and arrive out of
        order: hold each chunk until the ones before it are written."""
        with self._lock:
            if seq < self._next:
                return
            self._held[seq] = data
            while self._next in self._held:
                self.write(self._held.pop(self._next))
                self._next += 1

    def resize(self, cols: int, rows: int) -> None:
        try:
            fcntl.ioctl(self.fd, termios.TIOCSWINSZ,
                        struct.pack("HHHH", max(rows, 2), max(cols, 10), 0, 0))
        except OSError:
            pass

    def close(self) -> None:
        if not self.alive:
            return
        self.alive = False
        for sig in (signal.SIGHUP, signal.SIGTERM):
            try:
                os.killpg(os.getpgid(self.pid), sig)
            except OSError:
                pass
