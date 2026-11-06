"""Sample input images for the apps that read pictures.

The bundle ships a small COCO folder that every image app shares. The sets in
apps.yaml (`sample_images:`) give each kind of app photos that suit it: varied
scenes for detection and depth, faces for the face detector, ImageNet
classes for the classifier. They are downloaded once into the launcher's
cache (cache/images/<set>/), by Set Up Everything, Get Them on the Manager
page, or the first run that needs them.
"""
from __future__ import annotations

import shlex
import urllib.request
from pathlib import Path
from typing import Callable

from registry import Settings


class SampleImages:
    def __init__(self, settings: Settings, cache_dir: Path):
        self.sets: dict[str, dict] = settings.sample_images or {}
        self.root = Path(cache_dir) / "images"

    def dir(self, name: str) -> Path:
        return self.root / name

    def files(self, name: str) -> list[str]:
        return list((self.sets.get(name) or {}).get("files") or [])

    @staticmethod
    def local(f: str) -> str:
        """The saved name: a lower-case extension, which every app recognizes."""
        p = Path(f)
        return p.stem + p.suffix.lower()

    def url(self, name: str, f: str) -> str:
        return str(self.sets[name].get("from", "")) + f

    def label(self, name: str) -> str:
        return str((self.sets.get(name) or {}).get("label", name))

    def ready(self, name: str) -> bool:
        files = self.files(name)
        return bool(files) and all((self.dir(name) / self.local(f)).is_file() for f in files)

    def cached(self) -> int:
        return sum(1 for n in self.sets for f in self.files(n) if (self.dir(n) / self.local(f)).is_file())

    def total(self) -> int:
        return sum(len(self.files(n)) for n in self.sets)

    def describe(self, name: str) -> dict | None:
        if name not in self.sets:
            return None
        return {"set": name, "label": self.label(name), "count": len(self.files(name)),
                "source": str(self.sets[name].get("source", "")), "ready": self.ready(name)}

    def fetch_script(self) -> str:
        """Bash that downloads every set (skipping files already there)."""
        if not self.sets:
            return ""
        lines = ['echo "▸ Sample images for the image apps"']
        for name in self.sets:
            d = self.dir(name)
            lines.append(f"mkdir -p {shlex.quote(str(d))}")
            for f in self.files(name):
                dest = shlex.quote(str(d / self.local(f)))
                lines.append(f"[ -s {dest} ] || {{ curl -fsSL {shlex.quote(self.url(name, f))} "
                             f"-o {dest}.part && mv {dest}.part {dest}; }} || echo \"  could not get {f}\"")
        lines.append(f'echo "✔ Sample images in {self.root}"')
        return "\n".join(lines) + "\n"

    def ensure(self, name: str, log: Callable[[str], None] = lambda s: None,
               timeout: float = 30.0) -> Path | None:
        """The set's folder, downloading what is missing; None if none of it can be had."""
        if name not in self.sets:
            return None
        d = self.dir(name)
        missing = [f for f in self.files(name) if not (d / self.local(f)).is_file()]
        if missing:
            log(f"Downloading {len(missing)} sample image(s): {self.label(name)}")
            d.mkdir(parents=True, exist_ok=True)
            for f in missing:
                part = d / (self.local(f) + ".part")
                try:
                    req = urllib.request.Request(self.url(name, f), headers={"User-Agent": "neat-apps-launcher"})
                    with urllib.request.urlopen(req, timeout=timeout) as r:
                        part.write_bytes(r.read())
                    part.replace(d / self.local(f))
                except Exception as e:                       # noqa: BLE001
                    part.unlink(missing_ok=True)
                    log(f"  could not get {f}: {e}")
        have = [f for f in self.files(name) if (d / self.local(f)).is_file()]
        return d if have else None
