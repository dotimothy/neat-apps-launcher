#!/usr/bin/env python3
"""Record the README GIFs (docs/media/*.gif) from a running launcher.

    pip install playwright            # uses the installed Google Chrome
    python3 tools/record_gifs.py https://<board>:8091 [--only intro,catalog,...] [--force]

Each GIF is a scripted browser session recorded at 1440x810, then cut to
800x450 at 10 fps with ffmpeg (two-pass palette). The "run" and "results"
scenes start and stop apps on the board: the script refuses to run them
while any app is already running there (someone may be using the board)
unless --force is given.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.async_api import Page, async_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "media"
W, H = 1440, 810
STREAM_APP = "single-stream-object-detector"
FOLDER_APP = "yolo26-object-detector"


def api(base: str, path: str, body: dict | None = None) -> dict:
    ctx = ssl.create_default_context()
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method="POST" if body is not None else "GET",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20, context=ctx) as r:
        return json.loads(r.read())


def running_apps(base: str) -> list[str]:
    return [a["id"] for a in api(base, "/api/apps")["apps"]
            if a["state"] in ("running", "starting", "preparing", "downloading", "stopping")]


async def glide(page: Page, y: int, steps: int = 30, ms: int = 900) -> None:
    """Smooth scroll to y (window)."""
    await page.evaluate("""([y, steps, ms]) => new Promise(r => {
        const y0 = scrollY, d = y - y0; let i = 0;
        const t = setInterval(() => { i++; const k = i / steps; const e = k < .5 ? 2*k*k : 1 - Math.pow(-2*k + 2, 2) / 2;
          scrollTo(0, y0 + d * e); if (i >= steps) { clearInterval(t); r(); } }, ms / steps); })""", [y, steps, ms])


async def slow_type(page: Page, sel: str, text: str) -> None:
    await page.click(sel)
    for ch in text:
        await page.keyboard.type(ch)
        await page.wait_for_timeout(110)


# -- scenes -------------------------------------------------------------------
async def scene_intro(page: Page, base: str) -> None:
    await page.goto(f"{base}/?intro&nowelcome")
    await page.wait_for_timeout(6200)
    btn = page.locator(".intro-explore")
    if await btn.count() and await btn.is_visible():
        await btn.click()
    await page.wait_for_timeout(1500)
    await glide(page, 0, ms=10)
    await page.wait_for_timeout(1800)


async def scene_catalog(page: Page, base: str) -> None:
    await page.goto(f"{base}/?nointro&nowelcome")
    await page.wait_for_timeout(1800)
    y = await page.evaluate("document.querySelector('#apps').getBoundingClientRect().top + scrollY - 70")
    await glide(page, y, ms=1400)
    await page.wait_for_timeout(1200)
    await page.click("[data-act='filter:input:live']")
    await page.wait_for_timeout(1700)
    await page.click("[data-act='filter:input:live']")
    await page.wait_for_timeout(700)
    await slow_type(page, "#search", "segment")
    await page.wait_for_timeout(1800)
    await page.fill("#search", "")
    await page.dispatch_event("#search", "input")
    await page.wait_for_timeout(900)
    await glide(page, y + 420, ms=1400)
    await page.wait_for_timeout(1000)
    await page.click(f".app-card[data-id='{STREAM_APP}'] .card-name")
    await page.wait_for_timeout(1300)
    await page.click("[data-tab='about']")
    await page.wait_for_timeout(1200)
    await glide(page, 420, ms=1600)
    await page.wait_for_timeout(1500)


async def scene_run(page: Page, base: str) -> None:
    await page.goto(f"{base}/?nointro&nowelcome#/app/{STREAM_APP}/live")
    await page.wait_for_timeout(1800)
    await page.click("#appActions [data-act^='run:']")
    await page.wait_for_timeout(1200)
    await page.click("[data-tab='log']")
    await page.wait_for_selector(f"#appState .pill.running", timeout=90_000)
    await page.wait_for_timeout(2500)
    await page.click("[data-tab='live']")
    await page.wait_for_timeout(12_000)


async def scene_results(page: Page, base: str) -> None:
    await page.goto(f"{base}/?nointro&nowelcome#/app/{FOLDER_APP}/results")
    await page.wait_for_timeout(1500)
    await page.click("#appActions [data-act^='run:']")
    await page.wait_for_timeout(800)
    await page.click("[data-tab='log']")
    await page.wait_for_selector("#appState .pill.finished", timeout=120_000)
    await page.wait_for_timeout(1500)
    await page.click("[data-tab='results']")
    await page.wait_for_timeout(2500)
    await glide(page, 300, ms=1200)
    await page.wait_for_timeout(800)
    await page.click(".res >> nth=3")
    await page.wait_for_timeout(1800)
    for _ in range(3):
        await page.keyboard.press("ArrowRight")
        await page.wait_for_timeout(1300)
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(800)


async def scene_manager(page: Page, base: str) -> None:
    await page.goto(f"{base}/?nointro&nowelcome#/manager")
    await page.wait_for_timeout(2500)
    await glide(page, 420, ms=1500)
    await page.wait_for_timeout(1800)
    await glide(page, 820, ms=1500)
    await page.wait_for_timeout(1000)
    await page.click("[data-console='log:platform']")
    await page.wait_for_timeout(2200)
    await page.click("[data-console='shell']")
    await page.wait_for_timeout(1200)
    await page.keyboard.type("ls examples/*/ | head -20", delay=60)
    await page.keyboard.press("Enter")
    await page.wait_for_timeout(2500)


SCENES = {
    "intro": (scene_intro, False),
    "catalog": (scene_catalog, False),
    "run": (scene_run, True),
    "results": (scene_results, True),
    "manager": (scene_manager, False),
}


def to_gif(video: Path, gif: Path, skip: float, max_s: float, fps: int = 10, width: int = 800,
           colors: int = 160) -> None:
    vf = f"fps={fps},scale={width}:-2:flags=lanczos"
    pal = video.with_suffix(".png")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(skip), "-t", str(max_s), "-i", str(video),
                    "-vf", f"{vf},palettegen=max_colors={colors}:stats_mode=diff", str(pal)], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(skip), "-t", str(max_s), "-i", str(video), "-i", str(pal),
                    "-lavfi", f"{vf}[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle",
                    "-loop", "0", str(gif)], check=True)


async def record(base: str, name: str, fn, tmp: Path) -> Path:
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="chrome", headless=True, args=[
            "--autoplay-policy=no-user-gesture-required", "--ignore-certificate-errors"])
        ctx = await browser.new_context(viewport={"width": W, "height": H}, ignore_https_errors=True,
                                        color_scheme="dark", record_video_dir=str(tmp / name),
                                        record_video_size={"width": W, "height": H})
        page = await ctx.new_page()
        try:
            await fn(page, base)
        finally:
            await ctx.close()
            await browser.close()
    return next((tmp / name).glob("*.webm"))


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("base", help="launcher URL, e.g. https://192.168.91.225:8091")
    ap.add_argument("--only", default=",".join(SCENES))
    ap.add_argument("--force", action="store_true", help="run apps even if others are running")
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args()
    base = a.base.rstrip("/")
    a.out.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="neat-gifs-"))
    try:
        for name in a.only.split(","):
            fn, starts_apps = SCENES[name]
            if starts_apps and not a.force:
                busy = running_apps(base)
                if busy:
                    print(f"skip {name}: {', '.join(busy)} running on the board (use --force)", file=sys.stderr)
                    continue
            print(f"recording {name}…", flush=True)
            video = await record(base, name, fn, tmp)
            gif = a.out / f"{name}.gif"
            to_gif(video, gif, skip=1.2 if name != "intro" else 0.6, max_s=40,
                   fps=8 if name == "catalog" else 10, colors=128 if name == "catalog" else 160)
            print(f"  {gif} ({gif.stat().st_size / 1e6:.1f} MB)")
            if starts_apps:
                for app in (STREAM_APP, FOLDER_APP):
                    try:
                        api(base, f"/api/apps/{app}/stop", {})
                    except Exception:                          # noqa: BLE001
                        pass
                end = time.time() + 90                 # apps free the MLA before the next scene
                while running_apps(base) and time.time() < end:
                    time.sleep(1)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
