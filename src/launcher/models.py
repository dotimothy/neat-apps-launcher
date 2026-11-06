"""Model packages for the apps: where they live, which are present, and the
shell steps that download them with sima-cli.

Models go to <bundle>/models/<file> (the folder the app READMEs use), shared
by every app. Next to each one, .<file>.source records where it came from, so a
file left by an older model zoo (same name, different SDK) counts as missing. docs.sima.ai downloads need a SiMa Developer Portal login,
so they go through sima-cli, which holds that login.
"""
from __future__ import annotations

import shlex
from pathlib import Path

from registry import App, Settings

AUTH_HINT = ("If sima-cli asks you to log in, open Manager -> Shell and run "
             "`sima-cli login` once, then try again.")


def path_of(settings: Settings, m: dict) -> Path:
    return settings.models_dir / m["file"]


def source_key(settings: Settings, m: dict) -> str:
    """Where this model comes from, including the model zoo version."""
    src = m.get("source")
    if src == "url":
        return m.get("url", "")
    if src == "modelzoo":
        return f"modelzoo:{settings.modelzoo_version}:{m.get('name', '')}"
    if src == "model-registry":
        return f"registry:{m.get('name', '')}@{m.get('ref', '')}:{m.get('spec', '')}"
    return ""


def stamp_of(settings: Settings, m: dict) -> Path:
    return settings.models_dir / f".{m['file']}.source"


def _current(settings: Settings, m: dict) -> bool:
    """The file on disk came from this model's source (not an older SDK)."""
    if not downloadable(m):
        return True                              # nothing to compare with
    try:
        return stamp_of(settings, m).read_text().strip() == source_key(settings, m)
    except OSError:
        return False


def _size(settings: Settings, m: dict) -> int:
    try:
        return path_of(settings, m).stat().st_size
    except (OSError, KeyError):
        return 0


def present(settings: Settings, m: dict) -> bool:
    return _size(settings, m) > 0 and _current(settings, m)


def downloadable(m: dict) -> bool:
    return m.get("source") in ("url", "modelzoo", "model-registry") and bool(m.get("file"))


def status(settings: Settings, app: App) -> list[dict]:
    out = []
    for m in app.models:
        size = _size(settings, m) if m.get("file") else 0
        ok = size > 0 and _current(settings, m)
        out.append({"id": m["id"], "file": m.get("file", ""), "source": m.get("source"),
                    "present": ok, "size": size if ok else 0, "default": m["id"] in app.default_models,
                    "downloadable": downloadable(m)})
    return out


def selected(app: App, chosen: list[str] | None) -> list[dict]:
    """The models a run needs: the chosen ones, else the app's defaults."""
    ids = [c for c in (chosen or []) if app.model(c)] or app.default_models
    return [m for m in (app.model(i) for i in ids) if m]


def missing(settings: Settings, models: list[dict]) -> list[dict]:
    return [m for m in models if not present(settings, m)]


def download_script(settings: Settings, models: list[dict]) -> str:
    """Bash that downloads each model into a temporary folder and moves the
    finished file into place, so an interrupted download never looks done."""
    cli = settings.sima_cli_bin()
    mdir = settings.models_dir
    lines = [f"mkdir -p {shlex.quote(str(mdir))}"]
    if not models:
        return lines[0] + "\n"
    if not cli:
        return 'echo "✘ sima-cli was not found. Install it: https://docs.sima.ai"; exit 3\n'
    q = shlex.quote
    sc = f"SIMA_CLI_CHECK_FOR_UPDATE=0 {q(cli)}"
    mz = settings.modelzoo_version
    for m in models:
        dest = q(str(path_of(settings, m)))
        tmp = q(str(mdir / f".download-{m['id']}"))
        stamp, key = q(str(stamp_of(settings, m))), q(source_key(settings, m))
        src = m.get("source")
        lines.append(f'if [ -s {dest} ] && [ "$(cat {stamp} 2>/dev/null)" = {key} ]; then '
                     f'echo "✔ {m["file"]} is already here"; else')
        lines.append(f'  [ -s {dest} ] && echo "▸ {m["file"]} is from another model zoo version; replacing it"')
        lines.append(f'  echo "▸ Downloading model {m["id"]}"; rm -rf {tmp}; mkdir -p {tmp}')
        if src == "url":
            lines.append(f"  ( cd {tmp} && {sc} download {q(m['url'])} -d {tmp} )")
        elif src == "modelzoo":
            v = f"-v {q(mz)} " if mz else ""
            lines.append(f"  ( cd {tmp} && {sc} modelzoo {v}--boardtype modalix get {q(m['name'])} )")
        elif src == "model-registry":
            target = f"models/{m['name']}@{m['ref']}:{m['spec']}"
            lines.append(f"  ( cd {tmp} && {sc} neat install -d {tmp} {q(target)} )")
        else:
            lines.append(f'  echo "✘ {m["id"]}: {src} models cannot be downloaded here"; exit 3')
        want = q(m["file"])
        lines.append(f'  f=$(find {tmp} -type f -name {want} | head -1)')
        lines.append(f'  [ -n "$f" ] || f=$(find {tmp} -type f -name "*.tar.gz" | head -1)')
        lines.append(f'  [ -n "$f" ] || {{ echo "✘ No model file after downloading {m["id"]}. '
                     f'{AUTH_HINT}"; exit 4; }}')
        lines.append(f'  mv -f "$f" {dest} && rm -rf {tmp} && printf "%s\\n" {key} > {stamp}')
        lines.append(f'  echo "✔ {m["file"]} ($(du -h {dest} | cut -f1))"')
        lines.append("fi")
    return "\n".join(lines) + "\n"
