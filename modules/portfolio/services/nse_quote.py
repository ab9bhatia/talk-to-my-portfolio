"""NSE India quote-equity API — authoritative market cap for Indian listings."""

from __future__ import annotations

import http.cookiejar
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from modules.portfolio.services.market_data import (
    _INDIAN_YAHOO_SYMBOL_ALIASES,
    is_us_exchange,
    normalize_symbol,
)

logger = logging.getLogger(__name__)

_NSE_HOME = "https://www.nseindia.com/"
_NSE_QUOTE = "https://www.nseindia.com/api/quote-equity"
_CACHE: dict[str, tuple[float, float | None]] = {}
_CACHE_TTL = int(os.getenv("NSE_CAP_CACHE_TTL_SECONDS", str(4 * 60 * 60)))
_TIMEOUT = int(os.getenv("NSE_HTTP_TIMEOUT_SECONDS", "12"))

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _enabled() -> bool:
    return os.getenv("NSE_CAP_ENABLED", "true").lower() not in ("0", "false", "no")


def _nse_symbol_candidates(symbol: str) -> list[str]:
    base = normalize_symbol(symbol)
    if not base:
        return []
    roots = [base]
    for alias in _INDIAN_YAHOO_SYMBOL_ALIASES.get(base, []):
        if alias not in roots:
            roots.append(alias)
    return roots


def _fetch_quote(symbol: str) -> dict[str, Any] | None:
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    headers = {
        "User-Agent": _UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    try:
        warmup = urllib.request.Request(_NSE_HOME, headers=headers)
        opener.open(warmup, timeout=_TIMEOUT).read(512)
    except Exception as exc:
        logger.debug("NSE warmup failed: %s", exc)
        return None

    for candidate in _nse_symbol_candidates(symbol):
        params = urllib.parse.urlencode({"symbol": candidate})
        url = f"{_NSE_QUOTE}?{params}"
        req = urllib.request.Request(
            url,
            headers={
                **headers,
                "Accept": "application/json",
                "Referer": f"https://www.nseindia.com/get-quotes/equity?symbol={candidate}",
            },
        )
        try:
            with opener.open(req, timeout=_TIMEOUT) as resp:
                if resp.status != 200:
                    continue
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                continue
            logger.debug("NSE quote %s HTTP %s", candidate, exc.code)
        except Exception as exc:
            logger.debug("NSE quote %s failed: %s", candidate, exc)
    return None


def market_cap_inr_from_quote(payload: dict[str, Any]) -> float | None:
    """Full market cap in INR from NSE quote-equity (LTP × issued shares)."""
    price_info = payload.get("priceInfo") or {}
    security = payload.get("securityInfo") or {}
    ltp = price_info.get("lastPrice") or price_info.get("close")
    issued = security.get("issuedSize") or security.get("issuedShares")
    try:
        if ltp and issued and float(ltp) > 0 and float(issued) > 0:
            return float(ltp) * float(issued)
    except (TypeError, ValueError):
        pass
    return None


def nse_market_cap_inr(symbol: str, exchange: str | None = None) -> float | None:
    """
    Market cap in INR from https://www.nseindia.com/ (quote-equity).
    Returns None if NSE unavailable or symbol not found.
    """
    if not _enabled() or is_us_exchange(exchange):
        return None

    base = normalize_symbol(symbol)
    if not base:
        return None

    now = time.time()
    cached = _CACHE.get(base)
    if cached and (now - cached[0]) < _CACHE_TTL:
        return cached[1]

    payload = _fetch_quote(base)
    mcap = market_cap_inr_from_quote(payload) if payload else None
    _CACHE[base] = (now, mcap)
    return mcap
