#!/usr/bin/env python3
"""
Build docs/images/demo.gif — feature walkthrough.

Preferred: Playwright video + ffmpeg (smoothest).
Fallback: stitched PNG frames via Pillow (no ffmpeg required).

  python scripts/capture_readme_demo_gif.py

App must be running at http://127.0.0.1:8000
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "images"
GIF_PATH = OUT / "demo.gif"
BASE = os.getenv("PORTFOLIO_SCREENSHOT_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
VIEWPORT = {"width": 1280, "height": 720}


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _webm_to_gif(webm: Path, gif: Path) -> bool:
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(webm),
        "-vf",
        "fps=8,scale=960:-1:flags=lanczos,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse",
        "-loop",
        "0",
        str(gif),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        if isinstance(exc, subprocess.CalledProcessError) and exc.stderr:
            print(exc.stderr.decode(), file=sys.stderr)
        return False


def _record_video_gif() -> bool:
    from playwright.sync_api import sync_playwright

    video_dir = Path(tempfile.mkdtemp(prefix="portfolio-demo-"))
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            context = browser.new_context(
                viewport=VIEWPORT,
                record_video_dir=str(video_dir),
                record_video_size=VIEWPORT,
            )
            page = context.new_page()
            _run_tour(page)
            context.close()
            browser.close()

        webms = sorted(video_dir.glob("*.webm"), key=lambda p: p.stat().st_mtime)
        if not webms:
            return False
        return _webm_to_gif(webms[-1], GIF_PATH)
    finally:
        shutil.rmtree(video_dir, ignore_errors=True)


def _run_tour(page, *, snap_dir: Path | None = None) -> None:
    def pause(ms: int = 2200) -> None:
        page.wait_for_timeout(ms)

    def snap(name: str) -> None:
        if snap_dir is not None:
            page.screenshot(path=str(snap_dir / name), full_page=False)

    page.goto(f"{BASE}/portfolio", wait_until="networkidle", timeout=90_000)
    pause(2000)
    snap("01-portfolio.png")

    expander = page.locator(".row-expander").first
    if expander.count():
        expander.click()
        pause(4000)
        snap("02-expanded.png")

    page.goto(f"{BASE}/portfolio/growth", wait_until="networkidle", timeout=60_000)
    pause(2500)
    snap("03-growth.png")

    page.goto(f"{BASE}/portfolio/agent", wait_until="networkidle", timeout=60_000)
    pause(2200)
    snap("04-agent.png")

    page.goto(f"{BASE}/portfolio/setup", wait_until="networkidle", timeout=60_000)
    pause(2000)
    snap("05-setup.png")

    page.goto(f"{BASE}/portfolio", wait_until="networkidle", timeout=60_000)
    export_btn = page.locator(".js-export-excel-open").first
    if export_btn.count():
        export_btn.click()
        pause(1500)
        snap("06-export.png")
        page.keyboard.press("Escape")


def _frames_to_gif(frames_dir: Path, gif: Path, duration_ms: int = 400) -> None:
    from PIL import Image

    paths = sorted(frames_dir.glob("*.png"))
    if not paths:
        raise RuntimeError("No frames captured for GIF")
    images = [Image.open(p).convert("RGB") for p in paths]
    w, h = images[0].size
    resized = [im.resize((960, int(h * 960 / w)), Image.Resampling.LANCZOS) for im in images]
    resized[0].save(
        gif,
        save_all=True,
        append_images=resized[1:],
        duration=duration_ms,
        loop=0,
        optimize=True,
    )
    for im in images:
        im.close()


def _record_frames_gif() -> bool:
    from playwright.sync_api import sync_playwright

    frames_dir = Path(tempfile.mkdtemp(prefix="portfolio-frames-"))
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport=VIEWPORT)
            _run_tour(page, snap_dir=frames_dir)
            browser.close()
        _frames_to_gif(frames_dir, GIF_PATH)
        return True
    finally:
        shutil.rmtree(frames_dir, ignore_errors=True)


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError:
        print("Install: pip install playwright pillow && playwright install chromium", file=sys.stderr)
        return 1

    OUT.mkdir(parents=True, exist_ok=True)
    print(f"Recording tour from {BASE} …")

    if _ffmpeg_available():
        print("Using Playwright video + ffmpeg …")
        if _record_video_gif():
            print(f"Saved {GIF_PATH} ({GIF_PATH.stat().st_size // 1024} KB)")
            return 0
        print("Video path failed; trying frame fallback …", file=sys.stderr)

    print("Using frame capture + Pillow (install ffmpeg for smoother GIF) …")
    if _record_frames_gif():
        print(f"Saved {GIF_PATH} ({GIF_PATH.stat().st_size // 1024} KB)")
        return 0

    print("GIF generation failed.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
