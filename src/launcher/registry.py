"""App registry: catalog.json (generated from sima-neat/apps) + apps.yaml
(settings and per-app overrides) + an optional config.local.yaml."""
from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

HERE = Path(__file__).resolve().parent
DEFAULT_CONFIG = HERE / "apps.yaml"
DEFAULT_CATALOG = HERE / "catalog.json"


def _expand(p: str | os.PathLike) -> Path:
    return Path(os.path.expanduser(str(p)))


@dataclass
class Settings:
    install_root: Path
    bundle_dir: str = "prebuilt-apps"
    bundle_branch: str = "main"
    sima_cli: str = ""
    pyneat: Path = field(default_factory=lambda: _expand("~/pyneat"))
    insight: dict[str, Any] = field(default_factory=dict)
    studio_launcher_port: int = 8090
    modelzoo_version: str = ""
    catalog_source: dict[str, Any] = field(default_factory=dict)
    sample_images: dict[str, Any] = field(default_factory=dict)   # set name: {label, from, files}
    platform_version: str = ""        # firmware the apps need; default: the model zoo's major.minor
    sudo_password: str = ""           # only in config.local.yaml: lets setup steps use sudo
    core_query: str = "dpkg-query -W -f='${Version}' sima-neat"   # prints the installed Neat Core version (run by bash too)

    @property
    def bundle(self) -> Path:
        p = _expand(self.bundle_dir)
        return p if p.is_absolute() else self.install_root / p

    @property
    def models_dir(self) -> Path:
        return self.bundle / "models"

    def sima_cli_bin(self) -> str | None:
        if self.sima_cli and os.access(_expand(self.sima_cli), os.X_OK):
            return str(_expand(self.sima_cli))
        return shutil.which("sima-cli")

    @property
    def insight_port(self) -> int:
        return int(self.insight.get("port", 9900))

    @property
    def insight_venv(self) -> Path:
        return _expand(self.insight.get("venv", "~/.simaai/neat-insight/venv"))


@dataclass
class App:
    id: str
    rel: str
    name: str
    settings: Settings
    category: str = ""
    category_dir: str = ""
    difficulty: str = ""
    languages: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    status: str = ""
    binary: str = ""
    summary: str = ""
    model_label: str = ""
    models: list[dict[str, Any]] = field(default_factory=list)
    default_models: list[str] = field(default_factory=list)
    model_placeholder: bool = False
    input: dict[str, Any] = field(default_factory=dict)
    output: dict[str, Any] = field(default_factory=dict)
    insight: dict[str, Any] | None = None
    validate_flag: bool = False
    image: str | None = None
    sections: list[dict[str, str]] = field(default_factory=list)
    # overrides from apps.yaml
    hidden: bool = False
    needs: str = ""
    note: str = ""
    media: list[str] = field(default_factory=list)
    streams: int = 0
    config: dict[str, Any] = field(default_factory=dict)
    link: dict[str, str] = field(default_factory=dict)
    before_first_run: dict[str, Any] = field(default_factory=dict)   # {args, creates, label}
    placeholders: dict[str, str] = field(default_factory=dict)       # "<name>": value
    sample_images: str = ""                                          # a set in settings.sample_images
    mla: bool = True

    # -- locations -----------------------------------------------------------
    @property
    def dir(self) -> Path:
        return self.settings.bundle / self.rel

    def binary_path(self) -> Path:
        return self.dir / "src" / "cpp" / "pre-built" / self.binary

    def python_main(self) -> Path:
        return self.dir / "src" / "python" / "main.py"

    def config_path(self) -> Path:
        return self.dir / "src" / "common" / "config.yaml"

    def available_languages(self) -> list[str]:
        out = []
        if "cpp" in self.languages and self.binary and self.binary_path().exists():
            out.append("cpp")
        if "python" in self.languages and self.python_main().exists():
            out.append("python")
        return out

    def is_installed(self) -> bool:
        return not self.link and self.config_path().exists() and bool(self.available_languages())

    def model(self, mid: str) -> dict | None:
        return next((m for m in self.models if m["id"] == mid), None)

    def stream_count(self) -> int:
        if self.input.get("kind") not in ("rtsp", "streams"):
            return 0
        return int(self.streams or self.input.get("count", 1))

    def process_patterns(self) -> list[str]:
        out = [f"{self.dir}/src/python/main.py"]
        if self.binary:
            out.append(str(self.binary_path()))
        return out

    def log_paths(self) -> list[Path]:
        return []

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "rel": self.rel, "category": self.category,
            "category_dir": self.category_dir, "difficulty": self.difficulty,
            "languages": self.languages, "tags": self.tags, "status": self.status,
            "binary": self.binary, "summary": self.summary, "model_label": self.model_label,
            "models": self.models, "default_models": self.default_models,
            "model_placeholder": self.model_placeholder, "input": self.input,
            "output": self.output, "insight": self.insight, "image": self.image,
            "sections": self.sections, "needs": self.needs, "note": self.note,
            "media": self.media, "streams": self.stream_count(), "link": self.link,
            "sample_images": self.sample_images,
            "dir": str(self.dir),
        }


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load(path: str | os.PathLike | None = None, override: str | os.PathLike | None = None,
         catalog: str | os.PathLike | None = None) -> tuple[Settings, list[App]]:
    """Settings and the visible apps. `override` (or NEAT_LAUNCHER_CONFIG)
    may change any setting, change apps by id, or limit them with `only:`."""
    data = yaml.safe_load(Path(path or DEFAULT_CONFIG).read_text()) or {}
    override = override or os.environ.get("NEAT_LAUNCHER_CONFIG")
    over: dict = {}
    if override and Path(override).is_file():
        over = yaml.safe_load(Path(override).read_text()) or {}
    by_id = {a["id"]: a for a in data.get("apps", []) or []}
    for a in over.get("apps", []) or []:
        by_id[a["id"]] = _merge(by_id.get(a["id"], {}), a)
    data = _merge({k: v for k, v in data.items() if k != "apps"},
                  {k: v for k, v in over.items() if k not in ("apps", "only")})

    cat_path = Path(catalog or os.environ.get("NEAT_APPS_CATALOG") or DEFAULT_CATALOG)
    cat = json.loads(cat_path.read_text())
    root = os.environ.get("NEAT_APPS_ROOT") or data.get("install_root", "/media/nvme")
    settings = Settings(
        install_root=_expand(root),
        bundle_dir=os.environ.get("NEAT_APPS_BUNDLE") or data.get("bundle_dir", "prebuilt-apps"),
        bundle_branch=data.get("bundle_branch", "main"),
        sima_cli=os.environ.get("SIMA_CLI_BIN") or data.get("sima_cli", ""),
        pyneat=_expand(os.environ.get("NEAT_APPS_PYNEAT") or data.get("pyneat", "~/pyneat")),
        insight=data.get("insight", {}) or {},
        studio_launcher_port=int(data.get("studio_launcher_port", 8090)),
        modelzoo_version=cat.get("modelzoo_version", ""),
        catalog_source=cat.get("source", {}),
        sample_images=data.get("sample_images", {}) or {},
        platform_version=str(data.get("platform_version", "") or ""),
        sudo_password=str(data.get("sudo_password", "") or ""),
        core_query=str(data.get("core_query") or "dpkg-query -W -f='${Version}' sima-neat"),
    )
    if os.environ.get("NEAT_APPS_INSIGHT_PORT"):
        settings.insight["port"] = int(os.environ["NEAT_APPS_INSIGHT_PORT"])
    known = set(App.__dataclass_fields__) - {"settings"}
    apps = []
    for entry in cat.get("apps", []):
        merged = _merge(entry, by_id.get(entry["id"], {}))
        fields = {k: v for k, v in merged.items() if k in known}
        app = App(settings=settings, **fields)
        if not app.hidden:
            apps.append(app)
    if over.get("only"):
        apps = [a for a in apps if a.id in over["only"]]
    return settings, apps
