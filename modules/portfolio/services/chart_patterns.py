"""
Heuristic chart-pattern detection from daily OHLC (Yahoo via yfinance).

Patterns: inverse head & shoulders, cup with handle, head & shoulders,
double bottom, ascending triangle.

Lookback policy (trading days, classic TA durations):
- Fetch ~18 months so the longest cup base (up to ~15 months) plus lead-in fits.
- Reversal patterns (H&S, double bottom) are detected within the last ~1 year
  and must be recent (right edge within ~3 months) to count as an actionable setup.
- Cup base may look back up to ~15 months; ascending triangle uses the last ~100 bars.

Each hit preserves the legacy status/confidence fields, while adding an explicit
lifecycle, heuristic-score semantics, target state, currency, and a trading-session
horizon. Exact target dates and probability claims are intentionally not emitted.
"""

from __future__ import annotations

import logging
import math
import os
import time
from dataclasses import dataclass
from typing import Any, Literal

import yfinance as yf

from modules.portfolio.services.market_data import _quiet_yfinance, resolve_yahoo_ticker

logger = logging.getLogger(__name__)

PatternStatus = Literal["confirmed", "forming", "early"]
PatternBias = Literal["bullish", "bearish"]

_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_CACHE_TTL = int(os.getenv("CHART_PATTERNS_CACHE_TTL", str(6 * 60 * 60)))

# Lookback policy (trading days). Fetch ~18 months so the longest cup base
# (up to ~15 months) plus lead-in fits; detect mostly within the last year.
_HISTORY_PERIOD = os.getenv("CHART_PATTERNS_HISTORY", "18mo")
# Max span a reversal pattern (H&S, double bottom) may cover (~1 trading year).
_MAX_REVERSAL_SPAN = int(os.getenv("CHART_PATTERNS_MAX_SPAN", "252"))
# The pattern's right edge must be this recent to count as an actionable setup
# (~3 months); older structures are historical, not current.
_RECENCY_BARS = int(os.getenv("CHART_PATTERNS_RECENCY_BARS", "60"))
# Cup base may look back further than a year (up to ~15 months).
_CUP_WINDOW = int(os.getenv("CHART_PATTERNS_CUP_WINDOW", "315"))
# Only surface setups where measured move to target is at least this % (bullish or bearish).
_MIN_UPSIDE_PCT = float(os.getenv("CHART_PATTERNS_MIN_UPSIDE_PCT", "15"))
_DETECTOR_VERSION = "pattern-detector-6a"
_TARGET_OVERSHOOT_PCT = float(os.getenv("CHART_PATTERNS_TARGET_OVERSHOOT_PCT", "3"))

_US_EXCHANGES = {"US", "NASDAQ", "NYSE", "ARCA", "AMEX", "BATS"}
_INDIA_EXCHANGES = {"NSE", "BSE"}
_LIFECYCLE_BY_LEGACY_STATUS = {
    "early": "BUILDING",
    "forming": "NEAR_BREAKOUT",
    "confirmed": "CONFIRMED",
}
ACTIONABLE_LIFECYCLE_STATES = {
    "BUILDING",
    "NEAR_BREAKOUT",
    "CONFIRMED",
    "RETESTING",
}


@dataclass(frozen=True)
class _Series:
    labels: list[str]
    closes: list[float]
    highs: list[float]
    lows: list[float]


def _load_series(symbol: str, exchange: str | None, *, period: str | None = None) -> _Series | None:
    period = period or _HISTORY_PERIOD
    ticker = resolve_yahoo_ticker(symbol, exchange)
    if not ticker:
        return None
    try:
        with _quiet_yfinance():
            frame = yf.Ticker(ticker).history(period=period, interval="1d", auto_adjust=True)
    except Exception:
        return None
    if frame is None or frame.empty or len(frame) < 60:
        return None
    labels: list[str] = []
    closes: list[float] = []
    highs: list[float] = []
    lows: list[float] = []
    for idx, close, high, low in zip(
        frame.index,
        frame["Close"].tolist(),
        frame["High"].tolist(),
        frame["Low"].tolist(),
    ):
        try:
            c, h, l = float(close), float(high), float(low)
        except (TypeError, ValueError):
            continue
        if math.isnan(c) or math.isnan(h) or math.isnan(l):
            continue  # Yahoo leaves gaps for missing/halted days
        labels.append(idx.strftime("%Y-%m-%d"))
        closes.append(c)
        highs.append(h)
        lows.append(l)
    if len(closes) < 60:
        return None
    return _Series(labels=labels, closes=closes, highs=highs, lows=lows)


def _pivot_lows(closes: list[float], window: int = 5) -> list[tuple[int, float]]:
    out: list[tuple[int, float]] = []
    for i in range(window, len(closes) - window):
        seg = closes[i - window : i + window + 1]
        if closes[i] <= min(seg):
            out.append((i, closes[i]))
    return _dedupe_pivots(out, min_gap=window, prefer="low")


def _pivot_highs(closes: list[float], window: int = 5) -> list[tuple[int, float]]:
    out: list[tuple[int, float]] = []
    for i in range(window, len(closes) - window):
        seg = closes[i - window : i + window + 1]
        if closes[i] >= max(seg):
            out.append((i, closes[i]))
    return _dedupe_pivots(out, min_gap=window, prefer="high")


def _dedupe_pivots(
    pivots: list[tuple[int, float]], *, min_gap: int, prefer: Literal["low", "high"] = "low"
) -> list[tuple[int, float]]:
    if not pivots:
        return []
    pivots = sorted(pivots, key=lambda x: x[0])
    kept = [pivots[0]]
    for idx, price in pivots[1:]:
        if idx - kept[-1][0] >= min_gap:
            kept.append((idx, price))
        elif prefer == "low" and price < kept[-1][1]:
            kept[-1] = (idx, price)
        elif prefer == "high" and price > kept[-1][1]:
            kept[-1] = (idx, price)
    return kept


def _pct_diff(a: float, b: float) -> float:
    mid = (a + b) / 2
    if mid <= 0:
        return 100.0
    return abs(a - b) / mid * 100


def _max_between(highs: list[float], start: int, end: int) -> tuple[float, int]:
    segment = highs[start : end + 1]
    if not segment:
        return 0.0, start
    best = max(segment)
    idx = start + segment.index(best)
    return best, idx


def _point(series: _Series, idx: int, price: float, label: str) -> dict[str, Any]:
    idx = max(0, min(idx, len(series.labels) - 1))
    return {"label": label, "date": series.labels[idx], "price": round(float(price), 2)}


def _instrument_currency(exchange: str | None, explicit: str | None = None) -> str:
    """Return an ISO currency code without guessing from the displayed price."""
    if explicit:
        value = str(explicit).strip().upper()
        if len(value) == 3 and value.isalpha():
            return value
    normalized = str(exchange or "NSE").strip().upper()
    if normalized in _US_EXCHANGES:
        return "USD"
    if normalized in _INDIA_EXCHANGES:
        return "INR"
    return "INR"


def _estimated_horizon(duration_days: Any) -> dict[str, Any]:
    """Return a deliberately broad session range until empirical calibration exists."""
    median = max(1, int(duration_days or 1))
    return {
        "min_trading_days": max(5, round(median * 0.5)),
        "median_trading_days": median,
        "max_trading_days": max(median, round(median * 1.75)),
        "method": "heuristic_until_calibrated",
    }


def _enrich_pattern_semantics(
    hit: dict[str, Any],
    *,
    last_price: float,
    as_of: str,
    currency: str,
    signal_age_trading_days: int = 0,
) -> dict[str, Any]:
    """Add Stage 6A semantics while retaining every legacy response field."""
    enriched = dict(hit)
    target = float(enriched["target_price"])
    bias = str(enriched.get("bias") or "").lower()
    legacy_status = str(enriched.get("status") or "").lower()
    lifecycle = _LIFECYCLE_BY_LEGACY_STATUS.get(legacy_status, "BUILDING")
    horizon = _estimated_horizon(enriched.get("duration_days"))
    target_status = "ACTIVE"
    overshoot = _TARGET_OVERSHOOT_PCT / 100

    target_reached = (
        bias == "bullish" and last_price >= target
    ) or (
        bias == "bearish" and last_price <= target
    )
    target_overshot = (
        bias == "bullish" and last_price >= target * (1 + overshoot)
    ) or (
        bias == "bearish" and last_price <= target * (1 - overshoot)
    )
    if target_overshot:
        lifecycle = "TARGET_OVERSHOT"
        target_status = "OVERSHOT"
    elif target_reached:
        lifecycle = "TARGET_ACHIEVED"
        target_status = "ACHIEVED"
    elif legacy_status == "confirmed" and signal_age_trading_days > horizon["max_trading_days"]:
        lifecycle = "EXPIRED"
        target_status = "EXPIRED"

    active_target = target_status == "ACTIVE"
    remaining_upside = (
        max(0.0, (target - last_price) / last_price * 100)
        if active_target and bias == "bullish" and last_price > 0
        else 0.0
    )
    remaining_downside = (
        max(0.0, (last_price - target) / last_price * 100)
        if active_target and bias == "bearish" and last_price > 0
        else 0.0
    )
    legacy_move = (
        (target - last_price) / last_price * 100
        if last_price > 0 and active_target
        else 0.0
    )
    score = float(enriched.get("heuristic_score", enriched.get("confidence") or 0))

    enriched.update(
        {
            "last_price": round(last_price, 2),
            "current_price": round(last_price, 2),
            "as_of": as_of,
            "currency": currency,
            "lifecycle_state": lifecycle,
            "target_status": target_status,
            "measured_target": round(target, 2),
            "remaining_upside_pct": round(remaining_upside, 1),
            "remaining_downside_pct": round(remaining_downside, 1),
            # Backward compatibility: retain the signed legacy field, but never
            # expose a completed target as negative active upside.
            "upside_to_target_pct": round(legacy_move, 1),
            "heuristic_score": round(score, 2),
            "pattern_quality_score": round(score, 2),
            "confidence_semantics": "heuristic_shape_score",
            "calibrated_target_hit_probability": None,
            "calibration_status": "NOT_CALIBRATED",
            "sample_size": None,
            "estimated_horizon": horizon,
            # Retain the old key without preserving false precision.
            "target_date": None,
            "target_date_note": "Deprecated: use estimated_horizon; no exact target date is asserted.",
            "signal_age_trading_days": max(0, int(signal_age_trading_days)),
            "detector_version": _DETECTOR_VERSION,
        }
    )
    return enriched


def _meets_upside_threshold(last_price: float, target_price: float) -> bool:
    if last_price <= 0 or not math.isfinite(target_price):
        return False
    move_pct = abs(target_price - last_price) / last_price * 100
    return move_pct >= _MIN_UPSIDE_PCT


def is_actionable_pattern(pattern: dict[str, Any] | None) -> bool:
    """True only for a live, directionally coherent setup with move remaining."""
    if not isinstance(pattern, dict):
        return False
    lifecycle = str(
        pattern.get("lifecycle_state")
        or _LIFECYCLE_BY_LEGACY_STATUS.get(str(pattern.get("status") or "").lower(), "")
    ).upper()
    if lifecycle not in ACTIONABLE_LIFECYCLE_STATES:
        return False
    if str(pattern.get("target_status") or "ACTIVE").upper() != "ACTIVE":
        return False
    bias = str(pattern.get("bias") or "").lower()
    if bias == "bearish":
        remaining = pattern.get("remaining_downside_pct")
    elif bias == "bullish":
        remaining = pattern.get("remaining_upside_pct")
    else:
        return False
    try:
        remaining_value = float(remaining)
    except (TypeError, ValueError):
        return False
    return math.isfinite(remaining_value) and remaining_value >= (_MIN_UPSIDE_PCT - 0.1)


def _detect_inverse_head_shoulders(series: _Series) -> dict[str, Any] | None:
    lows = _pivot_lows(series.closes, window=6)
    if len(lows) < 3:
        return None
    last = series.closes[-1]
    best: dict[str, Any] | None = None
    for i in range(len(lows) - 2):
        li, lp = lows[i]
        hi, hp = lows[i + 1]
        ri, rp = lows[i + 2]
        span = ri - li
        if span < 30 or span > _MAX_REVERSAL_SPAN:
            continue
        if (len(series.closes) - 1 - ri) > _RECENCY_BARS:
            continue
        if hp >= lp or hp >= rp:
            continue
        if _pct_diff(lp, rp) > 10:
            continue
        if (lp - hp) / hp < 0.05 or (rp - hp) / hp < 0.05:
            continue
        neckline, neck_idx = _max_between(series.highs, li, ri)
        depth = neckline - hp
        if depth <= 0:
            continue
        target = round(neckline + depth, 2)
        duration_days = max(20, min(120, int(span * 0.7)))
        if last > neckline * 1.01:
            status: PatternStatus = "confirmed"
            confidence = 72
        elif last > neckline * 0.97:
            status = "forming"
            confidence = 58
        elif last > hp * 1.08:
            status = "early"
            confidence = 42
        else:
            continue
        confidence += min(15, int((depth / neckline) * 100))
        candidate = {
            "pattern": "inverse_head_shoulders",
            "label": "Inverse head & shoulders",
            "bias": "bullish",
            "status": status,
            "confidence": min(95, confidence),
            "neckline": round(neckline, 2),
            "target_price": target,
            "duration_days": duration_days,
            "start_date": series.labels[li],
            "end_date": series.labels[ri],
            "points": [
                _point(series, li, lp, "Left shoulder"),
                _point(series, hi, hp, "Head"),
                _point(series, ri, rp, "Right shoulder"),
            ],
            "note": "Bullish reversal — target ≈ neckline + depth of head.",
        }
        if best is None or candidate["confidence"] > best["confidence"]:
            best = candidate
    return best


def _detect_head_shoulders(series: _Series) -> dict[str, Any] | None:
    highs = _pivot_highs(series.closes, window=6)
    if len(highs) < 3:
        return None
    last = series.closes[-1]
    best = None
    for i in range(len(highs) - 2):
        li, lp = highs[i]
        hi, hp = highs[i + 1]
        ri, rp = highs[i + 2]
        span = ri - li
        if span < 30 or span > _MAX_REVERSAL_SPAN:
            continue
        if (len(series.closes) - 1 - ri) > _RECENCY_BARS:
            continue
        if hp <= lp or hp <= rp:
            continue
        if _pct_diff(lp, rp) > 10:
            continue
        neckline = min(series.lows[li : ri + 1])
        depth = hp - neckline
        if depth <= 0:
            continue
        target = round(neckline - depth, 2)
        duration_days = max(20, min(120, int(span * 0.7)))
        if last < neckline * 0.99:
            status: PatternStatus = "confirmed"
            confidence = 70
        elif last < neckline * 1.03:
            status = "forming"
            confidence = 55
        else:
            continue
        confidence += min(12, int((depth / hp) * 100))
        candidate = {
            "pattern": "head_shoulders",
            "label": "Head & shoulders",
            "bias": "bearish",
            "status": status,
            "confidence": min(92, confidence),
            "neckline": round(neckline, 2),
            "target_price": target,
            "duration_days": duration_days,
            "start_date": series.labels[li],
            "end_date": series.labels[ri],
            "points": [
                _point(series, li, lp, "Left shoulder"),
                _point(series, hi, hp, "Head"),
                _point(series, ri, rp, "Right shoulder"),
            ],
            "note": "Bearish reversal — target ≈ neckline minus head height.",
        }
        if best is None or candidate["confidence"] > best["confidence"]:
            best = candidate
    return best


def _detect_double_bottom(series: _Series) -> dict[str, Any] | None:
    lows = _pivot_lows(series.closes, window=5)
    if len(lows) < 2:
        return None
    last = series.closes[-1]
    best = None
    for i in range(len(lows) - 1):
        l1, p1 = lows[i]
        l2, p2 = lows[i + 1]
        gap = l2 - l1
        if gap < 15 or gap > 200:
            continue
        if (len(series.closes) - 1 - l2) > _RECENCY_BARS:
            continue
        if _pct_diff(p1, p2) > 4:
            continue
        peak_idx = l1 + series.closes[l1 : l2 + 1].index(max(series.closes[l1 : l2 + 1]))
        resistance = series.closes[peak_idx]
        depth = resistance - min(p1, p2)
        if depth / resistance < 0.06:
            continue
        target = round(resistance + depth, 2)
        duration_days = max(15, min(90, int(gap * 0.6)))
        if last > resistance * 1.01:
            status: PatternStatus = "confirmed"
            confidence = 68
        elif last > resistance * 0.96:
            status = "forming"
            confidence = 52
        elif last > min(p1, p2) * 1.05:
            status = "early"
            confidence = 38
        else:
            continue
        candidate = {
            "pattern": "double_bottom",
            "label": "Double bottom",
            "bias": "bullish",
            "status": status,
            "confidence": min(90, confidence + 8),
            "neckline": round(resistance, 2),
            "target_price": target,
            "duration_days": duration_days,
            "start_date": series.labels[l1],
            "end_date": series.labels[l2],
            "points": [
                _point(series, l1, p1, "Bottom 1"),
                _point(series, peak_idx, resistance, "Middle peak"),
                _point(series, l2, p2, "Bottom 2"),
            ],
            "note": "Two similar lows with a peak between — breakout above peak confirms.",
        }
        if best is None or candidate["confidence"] > best["confidence"]:
            best = candidate
    return best


def _detect_cup_with_handle(series: _Series) -> dict[str, Any] | None:
    closes = series.closes
    n = len(closes)
    if n < 120:
        return None
    window = min(_CUP_WINDOW, n - 20)
    offset = n - window
    segment = closes[-window:]
    labels = series.labels[-window:]
    left_zone = segment[: int(window * 0.25)]
    left_rim = max(left_zone)
    left_rim_idx = segment.index(left_rim)
    right_zone = segment[int(window * 0.55) : int(window * 0.85)]
    if not right_zone:
        return None
    right_rim = max(right_zone)
    right_rim_idx = int(window * 0.55) + right_zone.index(right_rim)
    if _pct_diff(left_rim, right_rim) > 8:
        return None
    cup_zone = segment[int(window * 0.2) : int(window * 0.8)]
    cup_low = min(cup_zone)
    cup_low_idx = int(window * 0.2) + cup_zone.index(cup_low)
    rim = (left_rim + right_rim) / 2
    cup_depth_pct = (rim - cup_low) / rim * 100
    if cup_depth_pct < 12 or cup_depth_pct > 45:
        return None
    handle_seg = segment[int(window * 0.82) :]
    if len(handle_seg) < 10:
        return None
    handle_low = min(handle_seg)
    handle_low_idx = int(window * 0.82) + handle_seg.index(handle_low)
    handle_retrace = (right_rim - handle_low) / right_rim * 100
    if handle_retrace < 3 or handle_retrace > 18:
        return None
    if handle_low < cup_low * 1.02:
        return None
    last = closes[-1]
    target = round(rim + (rim - cup_low), 2)
    duration_days = max(25, min(150, int(window * 0.35)))
    if last > right_rim * 1.01:
        status: PatternStatus = "confirmed"
        confidence = 74
    elif last > right_rim * 0.97:
        status = "forming"
        confidence = 60
    elif last > handle_low * 1.04:
        status = "early"
        confidence = 45
    else:
        return None
    confidence += min(12, int(cup_depth_pct / 3))
    return {
        "pattern": "cup_with_handle",
        "label": "Cup with handle",
        "bias": "bullish",
        "status": status,
        "confidence": min(94, confidence),
        "neckline": round(right_rim, 2),
        "target_price": target,
        "duration_days": duration_days,
        "start_date": labels[max(0, left_rim_idx)],
        "end_date": labels[-1],
        "points": [
            _point(series, offset + left_rim_idx, left_rim, "Left rim"),
            _point(series, offset + cup_low_idx, cup_low, "Cup low"),
            _point(series, offset + right_rim_idx, right_rim, "Right rim"),
            _point(series, offset + handle_low_idx, handle_low, "Handle low"),
        ],
        "note": "U-shaped base + shallow handle — target ≈ rim + cup depth.",
    }


def _detect_ascending_triangle(series: _Series) -> dict[str, Any] | None:
    n = len(series.closes)
    if n < 80:
        return None
    seg = series.closes[-100:]
    offset = n - len(seg)
    resistance = max(seg[-60:])
    resistance_idx = (len(seg) - 60) + seg[-60:].index(resistance)
    recent_lows = seg[-40:]
    if len(recent_lows) < 15:
        return None
    half = len(recent_lows) // 2
    first_half = sum(recent_lows[:half]) / half
    second_half = sum(recent_lows[half:]) / (len(recent_lows) - half)
    if second_half <= first_half * 1.02:
        return None
    flat_top = _pct_diff(max(seg[-30:]), resistance) < 4
    if not flat_top:
        return None
    early_low = min(recent_lows[:half])
    early_low_idx = (len(seg) - 40) + recent_lows[:half].index(early_low)
    late_low = min(recent_lows[half:])
    late_low_idx = (len(seg) - 40) + half + recent_lows[half:].index(late_low)
    last = series.closes[-1]
    target = round(resistance + (resistance - second_half) * 0.5, 2)
    duration_days = 30
    if last > resistance * 1.01:
        status: PatternStatus = "confirmed"
        confidence = 62
    elif last > resistance * 0.97:
        status = "forming"
        confidence = 50
    else:
        status = "early"
        confidence = 36
    return {
        "pattern": "ascending_triangle",
        "label": "Ascending triangle",
        "bias": "bullish",
        "status": status,
        "confidence": min(88, confidence),
        "neckline": round(resistance, 2),
        "target_price": target,
        "duration_days": duration_days,
        "start_date": series.labels[-100],
        "end_date": series.labels[-1],
        "points": [
            _point(series, offset + early_low_idx, early_low, "Earlier low"),
            _point(series, offset + late_low_idx, late_low, "Higher low"),
            _point(series, offset + resistance_idx, resistance, "Resistance"),
        ],
        "note": "Higher lows under flat resistance — breakout adds ~half the triangle height.",
    }


def analyze_series(series: _Series, *, currency: str = "INR") -> list[dict[str, Any]]:
    """Run all detectors; return matches sorted by confidence (best first)."""
    detectors = (
        _detect_inverse_head_shoulders,
        _detect_cup_with_handle,
        _detect_double_bottom,
        _detect_ascending_triangle,
        _detect_head_shoulders,
    )
    found: list[dict[str, Any]] = []
    for fn in detectors:
        try:
            hit = fn(series)
        except Exception:
            logger.debug("Pattern detector %s failed", fn.__name__, exc_info=True)
            hit = None
        if hit:
            numeric = [hit.get("target_price"), hit.get("neckline")]
            if any(v is None or not math.isfinite(v) for v in numeric):
                continue  # never emit NaN/inf — not JSON serializable
            if float(hit["target_price"]) <= 0:
                continue  # a measured price objective cannot be zero or negative
            last = round(series.closes[-1], 2)
            end_date = str(hit.get("end_date") or "")
            try:
                signal_age = len(series.labels) - 1 - series.labels.index(end_date)
            except ValueError:
                signal_age = 0
            enriched = _enrich_pattern_semantics(
                hit,
                last_price=last,
                as_of=series.labels[-1],
                currency=currency,
                signal_age_trading_days=signal_age,
            )
            if (
                enriched["target_status"] == "ACTIVE"
                and not _meets_upside_threshold(last, enriched["target_price"])
            ):
                continue
            found.append(enriched)
    lifecycle_order = {
        "CONFIRMED": 0,
        "RETESTING": 1,
        "NEAR_BREAKOUT": 2,
        "BUILDING": 3,
        "TARGET_ACHIEVED": 4,
        "TARGET_OVERSHOT": 5,
        "EXPIRED": 6,
        "INVALIDATED": 7,
    }
    found.sort(key=lambda p: (lifecycle_order.get(p["lifecycle_state"], 9), -p["heuristic_score"]))
    return found


def detect_patterns_for_symbol(
    symbol: str,
    exchange: str | None,
    *,
    currency: str | None = None,
    use_cache: bool = True,
) -> dict[str, Any]:
    """Pattern scan for one symbol."""
    currency_code = _instrument_currency(exchange, currency)
    key = f"{_DETECTOR_VERSION}:{symbol}:{exchange or 'NSE'}:{currency_code}"
    now = time.time()
    if use_cache:
        cached = _CACHE.get(key)
        if cached and now - cached[0] < _CACHE_TTL:
            patterns = cached[1]
            actionable = [pattern for pattern in patterns if is_actionable_pattern(pattern)]
            return {
                "symbol": symbol,
                "exchange": exchange,
                "currency": currency_code,
                "patterns": patterns,
                "primary": patterns[0] if patterns else None,
                "actionable_patterns": actionable,
                "actionable_primary": actionable[0] if actionable else None,
                "available": True,
                "cached": True,
            }

    series = _load_series(symbol, exchange)
    if not series:
        payload = {
            "symbol": symbol,
            "exchange": exchange,
            "currency": currency_code,
            "patterns": [],
            "available": False,
            "message": "Not enough price history.",
        }
        return payload

    patterns = analyze_series(series, currency=currency_code)
    actionable = [pattern for pattern in patterns if is_actionable_pattern(pattern)]
    _CACHE[key] = (now, patterns)
    return {
        "symbol": symbol,
        "exchange": exchange,
        "currency": currency_code,
        "patterns": patterns,
        "available": True,
        "primary": patterns[0] if patterns else None,
        "actionable_patterns": actionable,
        "actionable_primary": actionable[0] if actionable else None,
    }


def scan_holdings(
    holdings: list[dict[str, Any]],
    *,
    max_workers: int = 4,
) -> list[dict[str, Any]]:
    """Scan unique equity symbols from holdings list."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    seen: set[tuple[str, str]] = set()
    work: list[tuple[str, str | None, str | None]] = []
    for h in holdings:
        if h.get("asset_class") == "mf":
            continue
        sym = (h.get("symbol") or "").strip().upper()
        exchange = str(h.get("exchange") or "NSE").upper()
        identity = (sym, exchange)
        if not sym or identity in seen:
            continue
        seen.add(identity)
        work.append((sym, h.get("exchange"), h.get("currency") or h.get("base_currency")))

    results: list[dict[str, Any]] = []

    def _one(item: tuple[str, str | None, str | None]) -> dict[str, Any]:
        sym, exch, currency = item
        row = detect_patterns_for_symbol(sym, exch, currency=currency)
        row["holding_count"] = sum(
            1
            for h in holdings
            if (h.get("symbol") or "").upper() == sym
        )
        return row

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_one, item): item for item in work}
        for fut in as_completed(futures):
            try:
                results.append(fut.result())
            except Exception as exc:
                sym, _, _ = futures[fut]
                results.append(
                    {
                        "symbol": sym,
                        "patterns": [],
                        "available": False,
                        "error": str(exc),
                    }
                )

    def sort_key(row: dict[str, Any]) -> tuple:
        primary = row.get("actionable_primary")
        if not primary:
            return (1, 0, 0, row.get("symbol", ""))
        remaining = max(
            primary.get("remaining_upside_pct") or 0,
            primary.get("remaining_downside_pct") or 0,
        )
        score = primary.get("heuristic_score") or primary.get("confidence") or 0
        return (0, -remaining, -score, row.get("symbol", ""))

    results.sort(key=sort_key)
    return results
