#!/usr/bin/env python3
"""Capture README screenshots from a running local instance."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "images"
BASE = os.getenv("PORTFOLIO_SCREENSHOT_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
VIEWPORT = {"width": 1440, "height": 900}
WAIT_MS = 1500


def _wait_portfolio_ready(page) -> None:
    page.goto(f"{BASE}/portfolio", wait_until="networkidle", timeout=90_000)
    page.wait_for_timeout(WAIT_MS)
    if page.locator("#holdings-table").count():
        page.wait_for_selector("#holdings-table tbody tr.holding-row", timeout=30_000)
    elif page.locator(".dash-empty").count():
        print("Warning: no holdings on dashboard — some shots may be empty.", file=sys.stderr)


def _capture_page(page, filename: str, path: str, *, full_page: bool = True) -> None:
    url = f"{BASE}{path}"
    print(f"Capturing {filename} <- {url}")
    page.goto(url, wait_until="networkidle", timeout=90_000)
    page.wait_for_timeout(WAIT_MS)
    page.screenshot(path=str(OUT / filename), full_page=full_page)


def _capture_export_modal(page) -> None:
    print("Capturing export-excel-modal.png")
    page.goto(f"{BASE}/portfolio", wait_until="networkidle", timeout=90_000)
    page.wait_for_timeout(WAIT_MS)
    btn = page.locator(".js-export-excel-open").first
    if not btn.count():
        print("Warning: Export Excel button not found.", file=sys.stderr)
        page.screenshot(path=str(OUT / "export-excel-modal.png"), full_page=True)
        return
    btn.click()
    page.wait_for_timeout(600)
    dialog = page.locator("#export-excel-dialog")
    if dialog.count() and dialog.is_visible():
        dialog.screenshot(path=str(OUT / "export-excel-modal.png"))
    else:
        page.screenshot(path=str(OUT / "export-excel-modal.png"), full_page=True)


def _capture_holding_expanded(page) -> None:
    print("Capturing holding-expanded.png")
    page.goto(f"{BASE}/portfolio", wait_until="networkidle", timeout=90_000)
    page.wait_for_timeout(WAIT_MS)
    expander = page.locator("#holdings-table tbody tr.holding-row .row-expander").first
    if not expander.count():
        print("Warning: no holding rows to expand.", file=sys.stderr)
        page.screenshot(path=str(OUT / "holding-expanded.png"), full_page=False)
        return
    expander.click()
    page.wait_for_timeout(400)
    try:
        page.wait_for_selector(
            ".holding-detail-row:not([hidden]) .signal-col-title:has-text('Why this signal?')",
            timeout=45_000,
        )
    except Exception:
        try:
            page.wait_for_selector(
                ".holding-detail-row:not([hidden]) .detail-async:not(.is-loading) canvas",
                timeout=20_000,
            )
        except Exception:
            print("Warning: insights may still be loading.", file=sys.stderr)
    page.wait_for_timeout(800)
    viewport = page.locator("#holdings-viewport")
    if viewport.count():
        viewport.screenshot(path=str(OUT / "holding-expanded.png"))
    else:
        page.screenshot(path=str(OUT / "holding-expanded.png"), full_page=False)


def _capture_trade_modal(page) -> None:
    print("Capturing trade-order-modal.png")
    page.goto(f"{BASE}/portfolio", wait_until="networkidle", timeout=90_000)
    page.wait_for_timeout(WAIT_MS)
    buy_btn = page.locator(".js-trade-open[data-side='BUY']").first
    if not buy_btn.count():
        print(
            "Skipped trade-order-modal.png — enable TRADING_ENABLED=true in .env and reload the app.",
            file=sys.stderr,
        )
        return
    expander = page.locator("#holdings-table tbody tr.holding-row .row-expander").first
    if expander.count():
        if expander.get_attribute("aria-expanded") != "true":
            expander.click()
            page.wait_for_timeout(500)
    buy_btn.click()
    page.wait_for_timeout(600)
    dialog = page.locator("#trade-order-dialog")
    if dialog.count():
        try:
            dialog.evaluate("el => { if (!el.open) el.showModal(); }")
        except Exception:
            pass
    if dialog.count() and dialog.is_visible():
        dialog.screenshot(path=str(OUT / "trade-order-modal.png"))
    else:
        page.screenshot(path=str(OUT / "trade-order-modal.png"), full_page=False)


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Install: pip install playwright pillow && playwright install chromium", file=sys.stderr)
        return 1

    OUT.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport=VIEWPORT)
        _wait_portfolio_ready(page)

        for filename, path in [
            ("dashboard-agent.png", "/portfolio/agent"),
            ("setup-accounts.png", "/portfolio/setup"),
            ("growth-overview.png", "/portfolio/growth"),
        ]:
            _capture_page(page, filename, path)

        _capture_export_modal(page)
        _capture_holding_expanded(page)
        _capture_trade_modal(page)
        browser.close()

    redact = ROOT / "scripts" / "redact_readme_holdings_screenshot.py"
    if redact.exists():
        print("Capturing + redacting dashboard-holdings.png …")
        subprocess.run([sys.executable, str(redact)], check=False)

    print(f"Done. Screenshots in {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
