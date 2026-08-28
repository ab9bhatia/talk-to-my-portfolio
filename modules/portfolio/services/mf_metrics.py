"""NAV-based metrics for mutual fund holdings (52W, recovery upside, signal)."""

from __future__ import annotations

import time
from typing import Any

import yfinance as yf
from modules.portfolio.services.market_data import _pct_from_52w_high, _quiet_yfinance, _safe_round

_MF_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_CACHE_TTL_SECONDS = 6 * 60 * 60


def _mf_yahoo_candidates(isin: str) -> list[str]:
    code = (isin or "").strip().upper()
    if not code:
        return []
    return [f"{code}.NS", f"{code}.BO", code]


def _nav_history(ticker_symbol: str, *, period: str = "3y") -> list[float]:
    try:
        with _quiet_yfinance():
            frame = yf.Ticker(ticker_symbol).history(
                period=period,
                interval="1d",
                auto_adjust=True,
            )
    except Exception:
        return []
    if frame is None or frame.empty:
        return []
    return [float(v) for v in frame["Close"].tolist() if v == v]


def _return_1y(closes: list[float]) -> float | None:
    if len(closes) < 2:
        return None
    first, last = closes[0], closes[-1]
    if not first or first <= 0:
        return None
    return round(((last / first) - 1) * 100, 2)


def _return_cagr(closes: list[float]) -> float | None:
    if len(closes) < 500:
        return None
    first, last = closes[0], closes[-1]
    years = len(closes) / 252
    if first <= 0 or last <= 0 or years <= 0:
        return None
    return round((((last / first) ** (1 / years)) - 1) * 100, 2)


def _recovery_upside(pct_from_52w_high: float | None) -> float | None:
    """MF 'upside' = room for NAV to reach 52-week high (not analyst target)."""
    if pct_from_52w_high is None or pct_from_52w_high >= 0:
        return 0.0 if pct_from_52w_high is not None else None
    return round(-pct_from_52w_high, 2)


def get_mf_metrics(isin: str, last_price: float | None) -> dict[str, Any]:
    """52W NAV drawdown, recovery upside, and momentum-based signal for MF rows."""
    cache_key = (isin or "").strip().upper()
    now = time.time()
    cached = _MF_CACHE.get(cache_key)
    if cached and (now - cached[0]) < _CACHE_TTL_SECONDS:
        metrics = cached[1].copy()
    else:
        closes: list[float] = []
        for ticker in _mf_yahoo_candidates(cache_key):
            closes = _nav_history(ticker)
            if len(closes) >= 20:
                break

        closes_1y = closes[-252:]
        high_52w = max(closes_1y) if closes_1y else None
        return_1y = _return_1y(closes_1y)

        metrics = {
            "high_52w": _safe_round(high_52w, 4) if high_52w else None,
            "return_1y_pct": return_1y,
            "return_3y_cagr_pct": _return_cagr(closes),
            "nav_history_ok": bool(closes),
        }
        _MF_CACHE[cache_key] = (now, metrics)

    nav = last_price
    if nav is None and metrics.get("high_52w"):
        nav = metrics["high_52w"]

    if not metrics.get("nav_history_ok"):
        metrics["pct_from_52w_high"] = None
        metrics["upside_pct"] = None
        return metrics

    metrics["pct_from_52w_high"] = _pct_from_52w_high(nav, metrics.get("high_52w"))
    metrics["recovery_to_52w_high_pct"] = _recovery_upside(
        metrics.get("pct_from_52w_high")
    )
    metrics["upside_pct"] = None
    metrics["rating_label"] = None
    metrics["rating_slug"] = None
    metrics["rating_source"] = "unavailable"
    metrics["rating_reasons"] = [
        "Mutual-fund NAV distance and trailing return are context, not analyst recommendations."
    ]
    metrics["rating_rank"] = None
    return metrics
