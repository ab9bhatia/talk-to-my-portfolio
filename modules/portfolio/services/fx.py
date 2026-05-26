"""FX helpers for USD → INR (Sarwa and US listings)."""

from __future__ import annotations

import time
from typing import Any

import yfinance as yf

from modules.portfolio.services.market_data import _quiet_yfinance

_CACHE: tuple[float, float] | None = None
_CACHE_TTL_SECONDS = 6 * 60 * 60


def usd_inr_rate(*, refresh: bool = False) -> float:
    """Spot USD/INR from Yahoo (INR=X). Falls back to env or last cache."""
    global _CACHE
    now = time.time()
    if not refresh and _CACHE and (now - _CACHE[0]) < _CACHE_TTL_SECONDS:
        return _CACHE[1]

    rate: float | None = None
    try:
        with _quiet_yfinance():
            info = yf.Ticker("INR=X").info or {}
        rate = float(info.get("regularMarketPrice") or info.get("previousClose") or 0)
    except Exception:
        rate = None

    if not rate or rate <= 0:
        import os

        rate = float(os.getenv("USD_INR_FALLBACK", "83.5"))

    _CACHE = (now, rate)
    return rate


def usd_to_inr(amount_usd: float | None, *, rate: float | None = None) -> float | None:
    if amount_usd is None:
        return None
    fx = rate if rate is not None else usd_inr_rate()
    return round(float(amount_usd) * fx, 2)


def fx_meta() -> dict[str, Any]:
    rate = usd_inr_rate()
    return {"usd_inr": rate, "source": "Yahoo INR=X"}
