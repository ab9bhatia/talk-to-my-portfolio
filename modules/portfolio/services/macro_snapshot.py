"""Lightweight macro / market context via Yahoo (free, best-effort)."""

from __future__ import annotations

import time
from typing import Any

import yfinance as yf

from modules.portfolio.services.market_data import _pct_from_52w_high, _quiet_yfinance

_CACHE: tuple[float, dict[str, Any]] | None = None
_CACHE_TTL = 6 * 60 * 60

_BENCHMARKS = (
    ("Nifty 50", "^NSEI"),
    ("USD/INR", "INR=X"),
    ("Crude oil (WTI)", "CL=F"),
    ("India VIX", "^INDIAVIX"),
)


def _one_year_change(ticker: str) -> dict[str, Any] | None:
    try:
        with _quiet_yfinance():
            hist = yf.Ticker(ticker).history(period="1y", interval="1d", auto_adjust=True)
    except Exception:
        return None

    if hist is None or hist.empty or len(hist) < 2:
        return None

    closes = hist["Close"]
    first = float(closes.iloc[0])
    last = float(closes.iloc[-1])
    if not first:
        return None

    high_52w = float(closes.max())
    return {
        "last": round(last, 4),
        "change_1y_pct": round(((last - first) / first) * 100, 2),
        "pct_from_52w_high": _pct_from_52w_high(last, high_52w),
    }


def get_macro_snapshot() -> dict[str, Any]:
    """Cached macro block for the portfolio agent."""
    global _CACHE
    now = time.time()
    if _CACHE and (now - _CACHE[0]) < _CACHE_TTL:
        return _CACHE[1]

    benchmarks: list[dict[str, Any]] = []
    for label, ticker in _BENCHMARKS:
        stats = _one_year_change(ticker)
        if stats:
            benchmarks.append({"label": label, "ticker": ticker, **stats})

    snapshot = {
        "as_of": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(now)),
        "benchmarks": benchmarks,
        "note": "Macro data from Yahoo Finance; use as context only.",
    }
    _CACHE = (now, snapshot)
    return snapshot
