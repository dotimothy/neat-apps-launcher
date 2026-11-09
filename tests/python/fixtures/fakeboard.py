"""Build a fake board in a folder: an apps bundle template (with fake app
binaries), a fake sima-cli, a fake neat-insight and a fake pyneat, plus a
config.local.yaml pointing the launcher at all of it.

    python3 tests/python/fixtures/fakeboard.py /tmp/fakeboard [--apps-repo PATH] [--installed]

With --apps-repo, every app in the real catalog gets its real config.yaml
(the UI then looks like the board); without it, the test catalog is used.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PNG = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082")


def _exe(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def build(root: Path, catalog: dict, apps_repo: Path | None = None, installed: bool = False,
          insight_port: int = 19900) -> Path:
    root = Path(root)
    py = sys.executable
    tpl = root / "bundle-template"
    for a in catalog["apps"]:
        d = tpl / a["rel"]
        (d / "src" / "common").mkdir(parents=True, exist_ok=True)
        cfg = (apps_repo / a["rel"] / "src" / "common" / "config.yaml") if apps_repo else None
        if cfg and cfg.exists():
            shutil.copy(cfg, d / "src" / "common" / "config.yaml")
        elif a.get("_config"):
            (d / "src" / "common" / "config.yaml").write_text(a["_config"])
        else:
            (d / "src" / "common" / "config.yaml").write_text("model:\n  path: <model-path>\n")
        (d / "README.md").write_text(f"# {a['name']}\n")
        if "cpp" in a.get("languages", []) and a.get("binary"):
            _exe(d / "src" / "cpp" / "pre-built" / a["binary"], f"#!{py}\nimport runpy, sys\nsys.argv[0]='{a['binary']}'\nrunpy.run_path({str(HERE / 'fake_app.py')!r}, run_name='__main__')\n")
        if "python" in a.get("languages", []):
            (d / "src" / "python").mkdir(parents=True, exist_ok=True)
            shutil.copy(HERE / "fake_app.py", d / "src" / "python" / "main.py")
            (d / "src" / "python" / "requirements.txt").write_text("")
    coco = tpl / "assets" / "datasets" / "coco"
    coco.mkdir(parents=True, exist_ok=True)
    real = sorted((apps_repo / "assets" / "datasets" / "coco").glob("*.jpg")) if apps_repo else []
    for p in real:
        shutil.copy(p, coco / p.name)
    for i in range(0 if real else 3):
        (coco / f"img{i}.png").write_bytes(PNG)
    for clip in ("blue-discs/discs_demo.mp4", "tum-rgbd/freiburg1-desk.mp4",
                 "yolo26-tiny-drone-tracker/anti_uav_multi_uav_source_demo.mp4"):
        (tpl / "assets" / "datasets" / clip).parent.mkdir(parents=True, exist_ok=True)
        (tpl / "assets" / "datasets" / clip).write_bytes(b"\x00" * 64)
    (tpl / "deps").mkdir(parents=True, exist_ok=True)
    (tpl / "deps" / "neat-apps.json").write_text(json.dumps({"neat-apps": {"ref": "main", "spec": "fakefakefake"}}))
    (tpl / "deps" / "neat-core.json").write_text(json.dumps({"neat-core": {"ref": "v0.5.0", "spec": "fake"}}))
    if installed:
        shutil.copytree(tpl, root / "prebuilt-apps", dirs_exist_ok=True, symlinks=True)
        (root / "core-version").write_text("0.5.0+fake\n")       # an installed board has Neat Core
    _exe(root / "bin" / "core-version", f"#!/bin/sh\ncat {str(root / 'core-version')!r} 2>/dev/null || true\n")
    _exe(root / "bin" / "sima-cli", f"#!/bin/sh\nFAKE_BOARD={str(root)!r} exec {py} {str(HERE / 'fake_sima_cli.py')!r} \"$@\"\n")
    _exe(root / "insight-venv" / "bin" / "neat-insight", f"#!/bin/sh\nexec {py} {str(HERE / 'fake_insight.py')!r} \"$@\"\n")
    (root / "pyneat" / "bin").mkdir(parents=True, exist_ok=True)
    os.symlink(py, root / "pyneat" / "bin" / "python") if not (root / "pyneat" / "bin" / "python").exists() else None
    (root / "pyneat" / "bin" / "activate").write_text(f"export PATH={str(Path(py).parent)!r}:$PATH\n")
    samples = root / "samples"
    samples.mkdir(exist_ok=True)
    for n in range(1, 5):
        (samples / f"video{n:02d}.mp4").write_bytes(b"\x00" * 32)
    pics = root / "sample-images-src"            # stands in for the sample image sites
    pics.mkdir(parents=True, exist_ok=True)
    for name in ("one.jpg", "two.JPEG"):
        (pics / name).write_bytes(b"\xff\xd8fake-jpeg")
    sets = "".join(f"  {s}:\n    label: fake {s}\n    source: the test board\n    from: file://{pics}/\n"
                   f"    files: [one.jpg, two.JPEG]\n" for s in ("scenes", "faces", "imagenet"))
    (root / "config.local.yaml").write_text(
        f"install_root: {root}\nsima_cli: {root / 'bin' / 'sima-cli'}\npyneat: {root / 'pyneat'}\n"
        f"core_query: {root / 'bin' / 'core-version'}\n"
        f"insight:\n  port: {insight_port}\n  scheme: http\n  venv: {root / 'insight-venv'}\n"
        f"  samples: file://{samples}/video{{n:02d}}.mp4\n  sample_count: 4\n"
        f"sample_images:\n{sets}")
    return root


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path)
    ap.add_argument("--apps-repo", type=Path)
    ap.add_argument("--installed", action="store_true")
    ap.add_argument("--catalog", type=Path, default=HERE.parent.parent.parent / "src" / "launcher" / "catalog.json")
    a = ap.parse_args()
    cat = json.loads(a.catalog.read_text())
    build(a.root, cat, a.apps_repo, a.installed)
    print(f"fake board in {a.root}; run:\n  NEAT_LAUNCHER_CONFIG={a.root}/config.local.yaml ./run.sh --no-https")
