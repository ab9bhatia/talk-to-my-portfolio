"""NAV-based metrics for mutual fund holdings (52W, recovery upside, signal)."""

from __future__ import annotations

import time
from typing import Any

import yfinance as yf

from modules.portfolio.services.analyst_rating import compute_rating
from modules.portfolio.services.market_data import _pct_from_52w_high, _quiet_yfinance, _safe_round

_MF_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_CACHE_TTL_SECONDS = 6 * 60 * 60


def _mf_yahoo_candidates(isin: str) -> list[str]:
    code = (isin or "").strip().upper()
    if not code:
        return []
    return [f"{code}.NS", f"{code}.BO", code]


def _nav_history(ticker_symbol: str) -> list[float]:
    try:
        with _quiet_yfinance():
            frame = yf.Ticker(ticker_symbol).history(period="1y", interval="1d", auto_adjust=True)
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

        high_52w = max(closes) if closes else None
        return_1y = _return_1y(closes)

        metrics = {
            "high_52w": _safe_round(high_52w, 4) if high_52w else None,
            "return_1y_pct": return_1y,
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
    metrics["upside_pct"] = _recovery_upside(metrics.get("pct_from_52w_high"))

    rating = compute_rating(
        upside_pct=metrics.get("upside_pct"),
        target_price=metrics.get("high_52w"),
        last_price=nav,
    )
    if rating.get("label") and metrics.get("nav_history_ok"):
        rating["source"] = "nav_52w"
        reasons = [
            "Mutual fund signal uses NAV vs 52-week high and trailing 1Y NAV return (no analyst targets).",
        ]
        pct = metrics.get("pct_from_52w_high")
        if pct is not None:
            reasons.append(f"Current NAV is {pct:+.1f}% vs 52-week NAV high.")
        ret = metrics.get("return_1y_pct")
        if ret is not None:
            reasons.append(f"Trailing 1Y NAV return: {ret:+.1f}%.")
        rating["reasons"] = reasons

    metrics["rating_label"] = rating.get("label")
    metrics["rating_slug"] = rating.get("slug")
    metrics["rating_source"] = rating.get("source")
    metrics["rating_reasons"] = rating.get("reasons", [])
    metrics["rating_rank"] = rating.get("rank")
    return metrics
