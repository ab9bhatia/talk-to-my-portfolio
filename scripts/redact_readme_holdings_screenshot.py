#!/usr/bin/env python3
"""Capture and redact dashboard-holdings.png for the public README."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "docs" / "images" / "dashboard-holdings.png"
BASE = os.getenv("PORTFOLIO_SCREENSHOT_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
VIEWPORT = {"width": 1440, "height": 1050}
REDACT_FILL = (22, 28, 38)

COLUMN_CLASSES = ("col-weight", "col-value", "col-pnl")


def redact_region(im: Image.Image, box: tuple[int, int, int, int]) -> None:
    x0, y0, x1, y1 = box
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(im.width, x1), min(im.height, y1)
    if x1 <= x0 or y1 <= y0:
        return
    ImageDraw.Draw(im).rectangle((x0, y0, x1, y1), fill=REDACT_FILL)
    patch = im.crop((x0, y0, x1, y1)).filter(ImageFilter.GaussianBlur(radius=8))
    im.paste(patch, (x0, y0))


def capture_and_redact() -> None:
    from playwright.sync_api import sync_playwright

    SRC.parent.mkdir(parents=True, exist_ok=True)
    boxes: list[tuple[int, int, int, int]] = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport=VIEWPORT)
        page.goto(f"{BASE}/portfolio", wait_until="networkidle", timeout=60_000)
        page.wait_for_selector("#holdings-table", timeout=30_000)
        page.wait_for_timeout(1000)
        scroll = page.locator(".holdings-scroll")
        if scroll.count():
            scroll.evaluate("el => { el.scrollLeft = el.scrollWidth; }")
            page.wait_for_timeout(400)

        viewport = page.locator("#holdings-viewport")
        vp_box = viewport.bounding_box() if viewport.count() else None
        table_bottom = int(vp_box["y"] + vp_box["height"]) if vp_box else VIEWPORT["height"]

        for col_class in COLUMN_CLASSES:
            header = page.locator(f"#holdings-table th.{col_class}")
            if header.count() == 0:
                raise RuntimeError(f"Missing column: {col_class}")
            head_box = header.first.bounding_box()
            if not head_box:
                raise RuntimeError(f"Could not measure column: {col_class}")
            boxes.append(
                (
                    int(head_box["x"]) - 2,
                    int(head_box["y"]) - 2,
                    int(head_box["x"] + head_box["width"]) + 2,
                    table_bottom,
                )
            )

        page.screenshot(path=str(SRC), full_page=False)
        browser.close()

    im = Image.open(SRC).convert("RGB")
    for box in boxes:
        redact_region(im, box)
    im.save(SRC, optimize=True)
    print(f"Captured and redacted {SRC} ({im.width}x{im.height})")


def main() -> int:
    try:
        capture_and_redact()
    except Exception as exc:
        print(f"Failed: {exc}", file=sys.stderr)
        print("Ensure the app is running at", BASE, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
