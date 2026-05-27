#!/usr/bin/env python3
"""Capture README screenshots from a running local instance (optional)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "images"
BASE = os.getenv("PORTFOLIO_SCREENSHOT_BASE_URL", "http://127.0.0.1:8000").rstrip("/")

SHOTS: list[tuple[str, str, str | None]] = [
    ("dashboard-holdings.png", "/portfolio", None),
    ("dashboard-agent.png", "/portfolio/agent", None),
    ("setup-accounts.png", "/portfolio/setup", None),
    ("export-excel-modal.png", "/portfolio", "export"),
]


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Install: pip install playwright && playwright install chromium", file=sys.stderr)
        return 1

    OUT.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        for filename, path, action in SHOTS:
            url = f"{BASE}{path}"
            print(f"Capturing {filename} <- {url}")
            if filename == "dashboard-holdings.png":
                continue  # handled by redact_readme_holdings_screenshot.py
            page.goto(url, wait_until="networkidle", timeout=60_000)
            page.wait_for_timeout(1500)
            if action == "export":
                btn = page.locator(".js-export-excel-open").first
                if btn.count():
                    btn.click()
                    page.wait_for_timeout(500)
                dialog = page.locator("#export-excel-dialog")
                if dialog.count() and dialog.is_visible():
                    dialog.screenshot(path=str(OUT / filename))
                else:
                    page.screenshot(path=str(OUT / filename), full_page=True)
            else:
                page.screenshot(path=str(OUT / filename), full_page=True)
        browser.close()

    redact = ROOT / "scripts" / "redact_readme_holdings_screenshot.py"
    if redact.exists():
        import subprocess

        subprocess.run([sys.executable, str(redact)], check=True)
    print(f"Saved screenshots to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
