#!/usr/bin/env python3
"""A stand-in for sima-cli: --version, download, modelzoo get, neat install."""
import os
import shutil
import sys
from pathlib import Path

argv = sys.argv[1:]
board = Path(os.environ.get("FAKE_BOARD", Path(__file__).resolve().parent.parent))
if argv[:1] == ["--version"]:
    print("SiMa CLI version: 9.9.9")
    sys.exit(0)
if os.environ.get("FAKE_CLI_FAIL") == "1":
    print("login required")
    sys.exit(1)
if argv[:1] == ["download"]:
    url = argv[1]
    dest = Path(argv[argv.index("-d") + 1]) if "-d" in argv else Path.cwd()
    dest.mkdir(parents=True, exist_ok=True)
    (dest / url.rsplit("/", 1)[-1]).write_bytes(b"fake model " * 100)
    print(f"downloaded {url}")
    sys.exit(0)
if argv[:1] == ["modelzoo"]:
    name = argv[-1]
    (Path.cwd() / name).mkdir(exist_ok=True)
    (Path.cwd() / name / f"{name}_mpk.tar.gz").write_bytes(b"fake zoo model")
    sys.exit(0)
if argv[:2] == ["neat", "install"]:
    target = argv[-1]
    d = Path(argv[argv.index("-d") + 1]) if "-d" in argv else Path.cwd()
    if target.startswith("apps"):
        src = board / "bundle-template"
        dst = d / "prebuilt-apps"
        if dst.exists():
            shutil.rmtree(dst / "examples", ignore_errors=True)
        shutil.copytree(src, dst, dirs_exist_ok=True, symlinks=True)
        print(f"installed apps into {dst}")
        sys.exit(0)
    if target.startswith("insight"):
        print("insight installed")
        sys.exit(0)
print(f"fake sima-cli: unsupported {argv}")
sys.exit(2)
