#!/usr/bin/env python3
"""Build src/launcher/catalog.json from a sima-neat/apps checkout.

    python3 tools/build_catalog.py /path/to/sima-neat/apps [--previews]

For each example it joins:
  * the README Metadata table and sections (parsed by the repo's own
    scripts/generate_catalog.py, so the launcher reads READMEs the way the
    developer portal does),
  * tests/test-scope.yaml: every model the app can use and its default,
  * src/common/config.yaml: what input it expects (image folder, RTSP
    stream(s), a video file, a camera) and whether it sends to Insight.

--previews also writes a small WebP preview per app to
src/launcher/static/previews/<id>.webp (needs Pillow).

The bundle that `sima-cli neat install apps` puts on a board has no tests/ or
portal/ folders, so this runs on a PC and the result is committed.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "src" / "launcher" / "catalog.json"
PREVIEWS = HERE.parent / "src" / "launcher" / "static" / "previews"

STREAM_PH = re.compile(r"<[^<>]*rtsp[^<>]*>|rtsp://<rtsp-source-ip>")
MODEL_REF = re.compile(r"^models/[^\s]+$")
KEEP_SECTIONS = ("concept", "how-it-works", "configuration", "configure", "expected-output",
                 "output", "run", "notes", "limitations", "troubleshooting", "prepare-the-model")


def load_portal_parser(repo: Path):
    spec = importlib.util.spec_from_file_location("generate_catalog", repo / "scripts" / "generate_catalog.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)                                   # type: ignore[union-attr]
    mod.REPO_ROOT = repo
    mod.EXAMPLES_DIR = repo / "examples"
    return mod


def modelzoo_version(repo: Path) -> str:
    try:
        m = json.loads((repo / "deps" / "manifest.json").read_text())
        return m.get("modelzoo-version") or m.get("platform-version") or ""
    except Exception:                                              # noqa: BLE001
        return ""


def walk_strings(node, path=()):
    if isinstance(node, dict):
        for k, v in node.items():
            yield from walk_strings(v, path + (str(k),))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from walk_strings(v, path + (i,))
    elif isinstance(node, str):
        yield path, node


def classify_input(cfg: dict) -> dict:
    """What the app reads, from the shape of its config."""
    strings = list(walk_strings(cfg))
    streams = [p for p, v in strings if STREAM_PH.search(v)]
    io = cfg.get("io") or {}
    if isinstance(cfg.get("streams"), list) and streams:
        return {"kind": "streams", "count": len(cfg["streams"])}
    if streams:
        return {"kind": "rtsp", "count": 1}
    if isinstance(io, dict) and "input_dir" in io:
        return {"kind": "folder", "path": io["input_dir"]}
    if isinstance(io, dict) and "input" in io:                    # null: the app's own sample image
        return {"kind": "video", "path": io["input"]} if io["input"] else {"kind": "image"}
    if isinstance(io, dict) and "image" in io:
        return {"kind": "image"}
    if "camera" in cfg:
        return {"kind": "camera"}
    if "benchmark" in cfg:
        return {"kind": "synthetic"}
    return {"kind": "none"}


def classify_output(cfg: dict) -> dict:
    io = cfg.get("io") or {}
    out = cfg.get("output") or {}
    if isinstance(io, dict) and "output_dir" in io:
        return {"kind": "images", "key": "io.output_dir"}
    if isinstance(out, dict) and "report_json" in out:
        return {"kind": "report", "key": "output.report_json"}
    if isinstance(out, dict) and "directory" in out:
        return {"kind": "images", "key": "output.directory"}
    return {"kind": "log"}


def insight_info(cfg: dict, inp: dict) -> dict | None:
    text = json.dumps(cfg)
    if "<insight-host-ip>" not in text:
        return None
    channels = inp.get("count", 1) if inp["kind"] in ("streams", "rtsp") else 1
    return {"channels": channels}


def models_for(scope: dict, mz: str) -> tuple[list[dict], list[str]]:
    models = []
    for mid, m in (scope.get("models") or {}).items():
        m = dict(m or {})
        src = m.get("source", "url")
        entry = {"id": mid, "source": src}
        if src == "url":
            entry["url"] = str(m.get("url", "")).replace("{modelzoo_version}", mz)
            entry["file"] = m.get("file") or entry["url"].rsplit("/", 1)[-1]
        elif src == "modelzoo":
            entry["name"] = m.get("name", mid)
            entry["file"] = m.get("file") or f"{entry['name']}_mpk.tar.gz"
        elif src == "model-registry":
            entry.update({k: m.get(k) for k in ("name", "ref", "spec", "file")})
        else:                                          # huggingface: not downloadable here (yet)
            entry.update({k: m.get(k) for k in ("repo", "path", "file") if m.get(k)})
        models.append(entry)
    e2e = scope.get("e2e") or {}
    defaults: list[str] = []
    for lang in ("cpp", "python"):
        sec = e2e.get(lang) or {}
        if sec.get("enabled", True) and sec.get("models"):
            defaults = list(sec["models"])
            break
    if not defaults and models:
        defaults = [models[0]["id"]]
    return models, defaults


def langs(raw: str) -> list[str]:
    out = []
    low = raw.lower()
    if "c++" in low or "cpp" in low:
        out.append("cpp")
    if "python" in low:
        out.append("python")
    return out


def git_rev(repo: Path) -> dict:
    def g(*a):
        try:
            return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True,
                                  check=True).stdout.strip()
        except Exception:                                          # noqa: BLE001
            return ""
    branch = g("rev-parse", "--abbrev-ref", "HEAD")
    if branch == "HEAD":                                           # detached: use the remote branch here
        remotes = [b.strip() for b in g("branch", "-r", "--points-at", "HEAD").splitlines() if "->" not in b]
        branch = remotes[0].split("/", 1)[-1] if remotes else ""
    return {"commit": g("rev-parse", "HEAD"), "branch": branch, "date": g("log", "-1", "--format=%cI")}


def write_preview(src: Path, dst: Path) -> bool:
    try:
        from PIL import Image
    except ImportError:
        print("Pillow not installed: skipping previews", file=sys.stderr)
        return False
    im = Image.open(src).convert("RGB")
    im.thumbnail((720, 720))
    dst.parent.mkdir(parents=True, exist_ok=True)
    im.save(dst, "WEBP", quality=78, method=6)
    return True


def build(repo: Path, previews: bool) -> dict:
    gc = load_portal_parser(repo)
    mz = modelzoo_version(repo)
    apps = []
    for readme in sorted((repo / "examples").glob("*/*/README.md")):
        app_dir = readme.parent
        if not (app_dir / "src").is_dir():
            continue                                   # untracked leftovers
        meta = gc.parse_example(readme)
        if meta is None:
            continue
        aid = app_dir.name
        cfg_path = app_dir / "src" / "common" / "config.yaml"
        cfg = yaml.safe_load(cfg_path.read_text()) if cfg_path.exists() else {}
        scope_path = app_dir / "tests" / "test-scope.yaml"
        scope = yaml.safe_load(scope_path.read_text()) if scope_path.exists() else {}
        models, defaults = models_for(scope or {}, mz)
        inp = classify_input(cfg or {})
        src_text = ""
        for f in ("src/cpp/main.cpp", "src/python/main.py"):
            p = app_dir / f
            if p.exists():
                src_text += p.read_text(errors="replace")
        lang_list = [l for l in langs(meta.get("languages", ""))
                     if (app_dir / ("src/cpp" if l == "cpp" else "src/python/main.py")).exists()]
        sections = [s for s in meta["sections"] if s["slug"] not in ("metadata",)]
        image = None
        if previews and meta.get("image_path"):
            dst = PREVIEWS / f"{aid}.webp"
            if write_preview(repo / meta["image_path"], dst):
                image = f"previews/{aid}.webp"
        elif (PREVIEWS / f"{aid}.webp").exists():
            image = f"previews/{aid}.webp"
        apps.append({
            "id": aid,
            "rel": str(app_dir.relative_to(repo)),
            "name": meta["name"],
            "category": meta["category"],
            "category_dir": app_dir.parent.name,
            "difficulty": meta["difficulty"],
            "languages": lang_list,
            "tags": meta["tags"],
            "status": meta["status"],
            "binary": meta["binary_name"],
            "summary": meta["summary"],
            "model_label": meta["model"],
            "models": models,
            "default_models": defaults,
            "model_placeholder": "<model-path>" in json.dumps(cfg),
            "input": inp,
            "output": classify_output(cfg or {}),
            "insight": insight_info(cfg or {}, inp),
            "validate_flag": "--validate-config-only" in src_text,
            "image": image,
            "sections": [{"title": s["title"], "slug": s["slug"], "markdown": s["markdown"]}
                         for s in sections],
        })
    return {"schema": 1, "source": {"repo": "sima-neat/apps", **git_rev(repo)},
            "modelzoo_version": mz, "apps": apps}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("repo", type=Path, help="sima-neat/apps checkout")
    ap.add_argument("--previews", action="store_true", help="also write WebP previews")
    ap.add_argument("-o", "--out", type=Path, default=OUT)
    a = ap.parse_args()
    data = build(a.repo.resolve(), a.previews)
    a.out.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    print(f"{len(data['apps'])} apps -> {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
