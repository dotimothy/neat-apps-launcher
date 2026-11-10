"""Make a runnable copy of an app's config.yaml.

The configs in sima-neat/apps ship with placeholders (<model-path>,
<rtsp-url-1>, <insight-host-ip>, ...) and paths relative to the bundle root.
This fills them in for one run and writes runs/<app>/config.yaml; the app's
own config.yaml is never changed.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from registry import App

STREAM_PH = re.compile(r"<[^<>]*rtsp[^<>]*>|rtsp://<rtsp-source-ip>[^\s]*")
INSIGHT_PORT_KEYS = ("video_port", "metadata_port", "video_port_base", "metadata_port_base")
REL_PREFIXES = ("examples/", "assets/", "models/", "sandbox/")


def set_dotted(cfg: dict, key: str, value: Any) -> None:
    node = cfg
    parts = key.split(".")
    for p in parts[:-1]:
        if not isinstance(node.get(p), dict):
            node[p] = {}
        node = node[p]
    node[parts[-1]] = value


def get_dotted(cfg: dict, key: str, default: Any = None) -> Any:
    node: Any = cfg
    for p in key.split("."):
        if not isinstance(node, dict) or p not in node:
            return default
        node = node[p]
    return node


def load_default(app: App, streams: int | None = None) -> dict:
    cfg = yaml.safe_load(app.config_path(streams).read_text()) or {}
    cfg.pop("testing", None)                     # test-harness settings only
    return cfg


def generate(app: App, *, model_files: list[Path], stream_urls: list[str],
             insight_host: str = "127.0.0.1", channel: int = 0,
             output_dir: Path | None = None, overrides: dict | None = None,
             stream_video: dict | None = None, streams: int | None = None) -> dict:
    """The filled-in config (a dict).

    Each <model-path> takes the next chosen model (the first one when there are
    fewer), so an app with one model per stream gets them in order.
    `stream_video` ({width, height, fps} of the videos being streamed) replaces
    the size an entry with a stream URL declares, where it declares one."""
    bundle = app.settings.bundle
    cfg = load_default(app, streams)
    urls = list(stream_urls)
    model_paths = [str(m) for m in model_files]
    used_models = 0

    def fill(v: Any) -> Any:
        nonlocal used_models
        if isinstance(v, dict):
            out = {}
            for k, x in v.items():
                if k in INSIGHT_PORT_KEYS and isinstance(x, int):
                    out[k] = x + channel
                else:
                    out[k] = fill(x)
            has_stream = any(isinstance(x, str) and STREAM_PH.search(x) for x in v.values())
            if has_stream and stream_video:
                for k in ("width", "height", "fps"):
                    if out.get(k) and stream_video.get(k):   # 0 means "detect it": leave it
                        out[k] = stream_video[k]
            return out
        if isinstance(v, list):
            items = [fill(x) for x in v]
            return [x for x in items if x is not _DROP]
        if not isinstance(v, str):
            return v
        if v in (app.placeholders or {}):
            return fill(app.placeholders[v])
        if v == "<model-path>":
            if not model_paths:
                return v
            used_models += 1
            return model_paths[used_models - 1] if used_models <= len(model_paths) else model_paths[0]
        if STREAM_PH.search(v):
            return urls.pop(0) if urls else _DROP
        if "<insight-host-ip>" in v:
            return v.replace("<insight-host-ip>", insight_host)
        if v.startswith(REL_PREFIXES):
            return str(bundle / v)
        return v

    cfg = fill(cfg)
    cfg = _undrop(cfg)
    if isinstance(cfg.get("streams"), list) and urls:
        cfg["streams"].extend(_more_streams(cfg["streams"], urls))   # more streams than placeholders
    if output_dir is not None and app.output.get("key"):
        key = app.output["key"]
        old = get_dotted(cfg, key, "")
        name = Path(str(old)).name if app.output.get("kind") == "report" else None
        set_dotted(cfg, key, str(output_dir / name) if name else str(output_dir))
    for k, v in {**(app.config or {}), **(overrides or {})}.items():
        set_dotted(cfg, k, v)
    return cfg


class _Drop:
    pass


_DROP = _Drop()


def _undrop(v: Any) -> Any:
    """A stream placeholder with no URL left: drop it from lists (with the stream
    entry it belongs to, when that entry is a mapping), blank it elsewhere."""
    if isinstance(v, dict):
        return {k: ("" if x is _DROP else _undrop(x)) for k, x in v.items()}
    if isinstance(v, list):
        return [_undrop(x) for x in v
                if x is not _DROP and not (isinstance(x, dict) and _DROP in x.values())]
    return v


def _more_streams(streams: list, urls: list[str]) -> list:
    """Entries for URLs past the config's own: plain URLs, or copies of the last
    mapping entry with the new URL and the next id and Insight channel."""
    last = streams[-1] if streams else None
    if not isinstance(last, dict):
        return list(urls)
    url_key = next((k for k in ("url", "rtsp_url", "uri") if k in last), "url")
    out = []
    for i, url in enumerate(urls, start=len(streams)):
        e = {**last, url_key: url}
        if isinstance(last.get("insight_channel"), int):
            e["insight_channel"] = i
        if isinstance(last.get("id"), str):
            e["id"] = re.sub(r"\d+$", "", last["id"]) + str(i)
        out.append(e)
    return out


def image_input_key(app: App) -> str | None:
    """The config key that names an image app's input picture or folder."""
    kind = (app.input or {}).get("kind")
    if kind == "folder":
        return "io.input_dir"
    if kind == "image":
        return "io.input"            # a single image or a folder (read one level deep)
    return None


def placeholders_left(cfg: dict) -> list[str]:
    left: list[str] = []

    def walk(v: Any, path: str) -> None:
        if isinstance(v, dict):
            for k, x in v.items():
                walk(x, f"{path}.{k}" if path else str(k))
        elif isinstance(v, list):
            for i, x in enumerate(v):
                walk(x, f"{path}[{i}]")
        elif isinstance(v, str) and re.search(r"<[a-z0-9-]+>", v):
            left.append(f"{path}: {v}")
    walk(cfg, "")
    return left


def dump(cfg: dict, header: str = "") -> str:
    text = yaml.safe_dump(cfg, sort_keys=False, default_flow_style=False, width=100)
    return (header + text) if header else text
