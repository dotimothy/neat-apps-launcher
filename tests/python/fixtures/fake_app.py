#!/usr/bin/env python3
"""Stands in for an example app (C++ binary or src/python/main.py).

Reads --config like the real apps. Folder apps copy their input images to
io.output_dir and exit; report apps write output.report_json; streaming apps
print processed=N until SIGTERM. --validate-config-only checks that no
placeholder is left. FAKE_APP_FAIL=1 makes it exit 3.
"""
import argparse
import json
import os
import shutil
import signal
import sys
import time
from pathlib import Path

import yaml

ap = argparse.ArgumentParser()
ap.add_argument("--config", required=True)
ap.add_argument("--validate-config-only", action="store_true")
a = ap.parse_args()
cfg = yaml.safe_load(Path(a.config).read_text())
text = json.dumps(cfg)
if "<" in text and ">" in text and any(p in text for p in ("<model-path>", "<rtsp", "<insight-host-ip>")):
    print("config has placeholders", flush=True)
    sys.exit(2)
model = (cfg.get("model") or {}).get("path")
if model and not Path(model).exists():
    print(f"model not found: {model}", flush=True)
    sys.exit(4)
if a.validate_config_only:
    print("config ok", flush=True)
    sys.exit(0)
if os.environ.get("FAKE_APP_FAIL") == "1":
    print("failing on purpose", flush=True)
    sys.exit(3)
print(f"fake app running with {a.config}", flush=True)
io = cfg.get("io") or {}
if io.get("output_dir"):
    out = Path(io["output_dir"])
    out.mkdir(parents=True, exist_ok=True)
    for p in sorted(Path(io["input_dir"]).glob("*")):
        shutil.copy(p, out / p.name)
        print(f"wrote {p.name}", flush=True)
    sys.exit(0)
rep = (cfg.get("output") or {}).get("report_json")
if rep:
    Path(rep).parent.mkdir(parents=True, exist_ok=True)
    Path(rep).write_text(json.dumps({"model": model, "fps": 123.4, "latency_ms": {"mean": 8.1}}))
    print("report written", flush=True)
    sys.exit(0)
stop = False


def on_term(*_):
    global stop
    stop = True


signal.signal(signal.SIGTERM, on_term)
streams = cfg.get("streams") or [(cfg.get("source") or {}).get("url") or (cfg.get("source") or {}).get("rtsp_url")]
print(f"streams: {streams}", flush=True)
n = 0
while not stop:
    n += 1
    print(f"processed={n} video_sender=127.0.0.1:9000", flush=True)
    time.sleep(0.2)
print("clean exit", flush=True)
