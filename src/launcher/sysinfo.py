"""Board stats for the header chips and the Manager page."""
from __future__ import annotations

import glob
import os
import platform
import socket
import time

import psutil

_boot = psutil.boot_time()
psutil.cpu_percent(None)


def _temp() -> float | None:
    temps = []
    for f in glob.glob("/sys/class/thermal/thermal_zone*/temp"):
        try:
            v = int(open(f).read().strip()) / 1000
            if 0 < v < 150:
                temps.append(v)
        except (OSError, ValueError):
            pass
    return round(max(temps), 1) if temps else None


def _meminfo(key: str) -> int | None:
    try:
        for line in open("/proc/meminfo"):
            if line.startswith(key + ":"):
                return int(line.split()[1]) * 1024
    except OSError:
        pass
    return None


def ips() -> list[str]:
    out = []
    for name, addrs in psutil.net_if_addrs().items():
        if name == "lo":
            continue
        for a in addrs:
            if a.family == socket.AF_INET and not a.address.startswith("127."):
                out.append(a.address)
    return out


def board_name() -> str:
    try:
        model = open("/proc/device-tree/model").read().strip("\x00\n ")
        if model:
            return model
    except OSError:
        pass
    return platform.machine()


def collect(disk_path: str = "/") -> dict:
    vm = psutil.virtual_memory()
    path = disk_path if os.path.exists(disk_path) else "/"
    du = psutil.disk_usage(path)
    la = os.getloadavg()
    return {
        "hostname": socket.gethostname(),
        "board": board_name(),
        "kernel": platform.release(),
        "ips": ips(),
        "uptime": time.time() - _boot,
        "cpu": psutil.cpu_percent(None),
        "cores": psutil.cpu_count(),
        "load": [round(x, 2) for x in la],
        "mem_total": vm.total, "mem_used": vm.total - vm.available,
        "cma_total": _meminfo("CmaTotal"), "cma_free": _meminfo("CmaFree"),
        "disk_path": path, "disk_total": du.total, "disk_used": du.used,
        "temp": _temp(),
        "time": time.time(),
    }
