"""Seek-free, deterministic momentum feature calculation from supplied price history."""

from __future__ import annotations

from typing import Any

from modules.portfolio.services.advisory.models import (
    DataQualityFlag,
    MomentumRegime,
    MomentumSnapshot,
)


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _history_values(history: list[Any]) -> tuple[list[float], list[float], str | None]:
    closes: list[float] = []
    volumes: list[float] = []
    as_of: str | None = None
    for row in history:
        if isinstance(row, dict):
            close = _number(row.get("adjusted_close", row.get("close")))
            volume = _number(row.get("volume"))
            if row.get("date"):
                as_of = str(row["date"])
        else:
            close = _number(row)
            volume = None
        if close is not None and close > 0:
            closes.append(close)
            if volume is not None:
                volumes.append(volume)
    return closes, volumes, as_of


def _return_for_bars(closes: list[float], bars: int) -> float | None:
    if len(closes) <= bars or closes[-bars - 1] <= 0:
        return None
    return round(((closes[-1] / closes[-bars - 1]) - 1) * 100, 2)


def _history_metrics(holding: dict[str, Any]) -> tuple[dict[str, float | bool | None], str | None]:
    closes, volumes, as_of = _history_values(holding.get("price_history") or [])
    metrics: dict[str, float | bool | None] = {}
    if closes:
        periods = (
            ("return_1m_pct", 21),
            ("return_3m_pct", 63),
            ("return_6m_pct", 126),
            ("return_12m_pct", 252),
        )
        for key, bars in periods:
            metrics[key] = _return_for_bars(closes, bars)
        if len(closes) >= 50:
            dma50 = sum(closes[-50:]) / 50
            metrics["pct_vs_dma50"] = round(((closes[-1] / dma50) - 1) * 100, 2)
        if len(closes) >= 200:
            dma200 = sum(closes[-200:]) / 200
            metrics["pct_vs_dma200"] = round(((closes[-1] / dma200) - 1) * 100, 2)
        if len(closes) >= 2:
            recent = closes[-min(252, len(closes)) :]
            peak = max(recent)
            metrics["pct_from_52w_high"] = round(((closes[-1] / peak) - 1) * 100, 2)
            running_peak = recent[0]
            worst_drawdown = 0.0
            for close in recent:
                running_peak = max(running_peak, close)
                worst_drawdown = min(worst_drawdown, ((close / running_peak) - 1) * 100)
            metrics["max_drawdown_12m_pct"] = round(worst_drawdown, 2)
    if len(volumes) >= 40:
        recent_volume = sum(volumes[-20:]) / 20
        prior_volume = sum(volumes[-40:-20]) / 20
        metrics["volume_confirmation"] = recent_volume > prior_volume * 1.1
    return metrics, as_of


def _bounded_signal(value: float, weak: float, strong: float) -> float:
    if strong <= weak:
        return 0.5
    return max(0.0, min(1.0, (value - weak) / (strong - weak)))


def analyze_momentum(
    holding: dict[str, Any],
) -> tuple[MomentumSnapshot, list[DataQualityFlag]]:
    """Return a transparent regime; `None` means no momentum evidence."""
    metrics, as_of = _history_metrics(holding)
    aliases = {
        "return_1m_pct": ("return_1m_pct",),
        "return_3m_pct": ("return_3m_pct",),
        "return_6m_pct": ("return_6m_pct",),
        "return_12m_pct": ("return_12m_pct", "return_1y_pct"),
        "relative_strength_6m_pct": ("relative_strength_6m_pct",),
        "pct_vs_dma50": ("pct_vs_dma50",),
        "pct_vs_dma200": ("pct_vs_dma200",),
        "pct_from_52w_high": ("pct_from_52w_high",),
        "max_drawdown_12m_pct": ("max_drawdown_12m_pct",),
        "volume_confirmation": ("volume_confirmation",),
    }
    for target, keys in aliases.items():
        for key in keys:
            if holding.get(key) is not None:
                value = holding.get(key)
                metrics[target] = bool(value) if target == "volume_confirmation" else _number(value)
                break
    as_of = str(holding.get("momentum_as_of") or as_of or "") or None

    specs = {
        "return_1m_pct": (-10.0, 10.0, 1.0),
        "return_3m_pct": (-15.0, 20.0, 1.2),
        "return_6m_pct": (-20.0, 30.0, 1.3),
        "return_12m_pct": (-25.0, 40.0, 1.3),
        "relative_strength_6m_pct": (-12.0, 12.0, 1.2),
        "pct_vs_dma50": (-12.0, 12.0, 1.0),
        "pct_vs_dma200": (-20.0, 20.0, 1.5),
        "pct_from_52w_high": (-45.0, 0.0, 0.8),
        "max_drawdown_12m_pct": (-50.0, -5.0, 0.7),
    }
    weighted_score = 0.0
    observed_weight = 0.0
    total_weight = sum(item[2] for item in specs.values()) + 0.5
    for key, (weak, strong, weight) in specs.items():
        value = metrics.get(key)
        if isinstance(value, (int, float)):
            weighted_score += _bounded_signal(float(value), weak, strong) * weight
            observed_weight += weight
    if isinstance(metrics.get("volume_confirmation"), bool):
        weighted_score += (1.0 if metrics["volume_confirmation"] else 0.35) * 0.5
        observed_weight += 0.5

    flags: list[DataQualityFlag] = []
    if observed_weight == 0:
        flags.append(
            DataQualityFlag(
                code="MISSING_MOMENTUM_INPUTS",
                severity="warning",
                message="No dated price-history momentum inputs are available.",
            )
        )
        return MomentumSnapshot(None, 0.0, 0.0, as_of, metrics), flags

    normalized = weighted_score / observed_weight
    broken = (
        (metrics.get("pct_vs_dma200") or 0) < -15
        and (metrics.get("return_6m_pct") or 0) < -20
    )
    if broken or normalized < 0.25:
        regime = MomentumRegime.BROKEN
    elif normalized < 0.4:
        regime = MomentumRegime.WEAK
    elif normalized < 0.6:
        regime = MomentumRegime.NEUTRAL
    elif normalized < 0.75:
        regime = MomentumRegime.POSITIVE
    else:
        regime = MomentumRegime.STRONG

    coverage = round((observed_weight / total_weight) * 100, 1)
    if metrics.get("relative_strength_6m_pct") is None:
        flags.append(
            DataQualityFlag(
                code="MISSING_RELATIVE_STRENGTH",
                severity="info",
                message="Benchmark-relative strength is unavailable.",
            )
        )
    if metrics.get("volume_confirmation") is None:
        flags.append(
            DataQualityFlag(
                code="MISSING_VOLUME_CONFIRMATION",
                severity="info",
                message="Reliable volume confirmation is unavailable.",
            )
        )
    return (
        MomentumSnapshot(
            regime=regime,
            score=round(normalized * 15, 2),
            coverage_pct=coverage,
            as_of=as_of,
            metrics=metrics,
        ),
        flags,
    )
