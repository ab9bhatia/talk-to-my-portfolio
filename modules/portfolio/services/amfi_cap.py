"""AMFI / SEBI market-cap list (top 100 Large, 101–250 Mid, 251+ Small)."""

from __future__ import annotations

import io
import logging
import os
import re
import threading
import time
import urllib.request
from typing import Any

from modules.portfolio.db import amfi_cap_cache

logger = logging.getLogger(__name__)

_SERIES_SUFFIX = re.compile(
    r"-(?:BE|BL|BZ|EQ|IV|RR|ST|N1|N2|N3|N4|N5|N6|N7|N8|N9|B1|B2|GS|GB|MF|ETF)$",
    re.IGNORECASE,
)


def normalize_symbol(symbol: str) -> str:
    return _SERIES_SUFFIX.sub("", symbol.strip().upper())

# SEBI/AMFI: rank by 6-month average full market cap across BSE/NSE/MSEI (updated twice yearly).
_DEFAULT_PDF_URL = (
    "https://www.amfiindia.com/Themes/Theme1/downloads/"
    "AverageMarketCapitalization30Jun2025.pdf"
)

_LINE_RE = re.compile(
    r"^\s*\d+\s+.+?(INE[A-Z0-9]{9})\s*([A-Z][A-Z0-9&.-]*)\s*[\d,].*?"
    r"([\d,]+(?:\.\d+)?)\s+(Large|Mid|Small)\s+Cap\s*$",
    re.MULTILINE,
)

_load_lock = threading.Lock()
_loaded = False


def _enabled() -> bool:
    return os.getenv("AMFI_CAP_ENABLED", "true").lower() not in ("0", "false", "no")


def _pdf_url() -> str:
    return os.getenv("AMFI_CAP_PDF_URL", _DEFAULT_PDF_URL).strip()


def _cache_ttl_seconds() -> int:
    try:
        return int(os.getenv("AMFI_CAP_CACHE_TTL_SECONDS", str(90 * 24 * 3600)))
    except ValueError:
        return 90 * 24 * 3600


def _parse_amfi_pdf_text(text: str) -> list[tuple[str, str, str, float | None, int]]:
    """Parse AMFI PDF extract; returns rows in list rank order."""
    rows: list[tuple[str, str, str, float | None, int]] = []
    seen_isin: set[str] = set()
    rank = 0
    for match in _LINE_RE.finditer(text):
        isin = match.group(1).upper()
        if isin in seen_isin:
            continue
        seen_isin.add(isin)
        rank += 1
        symbol = match.group(2).upper()
        avg_cr = float(match.group(3).replace(",", ""))
        bucket = match.group(4)
        if bucket == "Large":
            cap = "Large"
        elif bucket == "Mid":
            cap = "Mid"
        else:
            cap = "Small"
        rows.append((isin, symbol, cap, avg_cr, rank))
    return rows


def _download_pdf_bytes(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (portfolio-hub)"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read()


def _extract_pdf_text(data: bytes) -> str:
    try:
        import pdfplumber
    except ImportError as exc:
        raise RuntimeError(
            "pdfplumber is required for AMFI cap list download (pip install pdfplumber)"
        ) from exc
    parts: list[str] = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages:
            parts.append(page.extract_text() or "")
    return "\n".join(parts)


def refresh_amfi_cap_list(*, url: str | None = None, force: bool = False) -> dict[str, Any]:
    """Download AMFI PDF and refresh SQLite symbol → cap bucket cache."""
    pdf_url = url or _pdf_url()
    if not force and amfi_cap_cache.row_count() > 0:
        return {
            "ok": True,
            "skipped": True,
            "count": amfi_cap_cache.row_count(),
            "source_period": amfi_cap_cache.source_period(),
        }

    data = _download_pdf_bytes(pdf_url)
    text = _extract_pdf_text(data)
    parsed = _parse_amfi_pdf_text(text)
    if len(parsed) < 200:
        raise RuntimeError(f"AMFI PDF parse produced only {len(parsed)} rows; check URL/format")

    period = pdf_url.rsplit("/", 1)[-1].replace(".pdf", "")
    amfi_cap_cache.replace_list(parsed, source_period=period)
    logger.info("AMFI cap list refreshed: %s rows from %s", len(parsed), period)
    return {"ok": True, "count": len(parsed), "source_period": period, "url": pdf_url}


def _ensure_loaded() -> None:
    global _loaded
    if _loaded or not _enabled():
        return
    with _load_lock:
        if _loaded:
            return
        if amfi_cap_cache.row_count() == 0:
            try:
                refresh_amfi_cap_list(force=True)
            except Exception as exc:
                logger.warning("AMFI cap list auto-load failed: %s", exc)
        _loaded = True


def lookup_amfi_cap(symbol: str, isin: str | None = None) -> str | None:
    """Return Large / Mid / Small from latest AMFI list, or None if unknown."""
    if not _enabled():
        return None
    _ensure_loaded()
    if isin:
        bucket = amfi_cap_cache.lookup_isin(isin.strip().upper())
        if bucket:
            return bucket
    sym = normalize_symbol(symbol)
    if not sym:
        return None
    return amfi_cap_cache.lookup_symbol(sym)


def classify_indian_equity_cap(
    symbol: str,
    *,
    isin: str | None = None,
    market_cap_inr: float | None = None,
) -> str | None:
    """
  Classify Indian equity using AMFI rank list (SEBI circular).
  Fallback: compare live mcap (₹) to AMFI rank-100 and rank-250 average caps (₹ crore).
    """
    bucket = lookup_amfi_cap(symbol, isin)
    if bucket:
        return bucket

    if not market_cap_inr or market_cap_inr <= 0:
        return None

    large_floor_cr, mid_floor_cr = amfi_cap_cache.rank_cutoffs()
    if not large_floor_cr or not mid_floor_cr:
        return None

    mcap_cr = market_cap_inr / 1e7
    if mcap_cr >= large_floor_cr:
        return "Large"
    if mcap_cr >= mid_floor_cr:
        return "Mid"
    return "Small"


def cap_bucket_reference() -> dict[str, str]:
    """Human-readable cap definition for UI."""
    period = amfi_cap_cache.source_period() or "AMFI (latest)"
    return {
        "Large": f"AMFI rank 1–100 ({period})",
        "Mid": "AMFI rank 101–250",
        "Small": "AMFI rank 251+",
        "Multi-cap": "Thematic / broad ETFs (manual)",
    }
