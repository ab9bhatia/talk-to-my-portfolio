"""Normalize external analyst context without turning targets into advice."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

from modules.portfolio.services.advisory.models import (
    ExternalAnalystSentiment,
    ExternalAnalystStatus,
    ExternalAnalystView,
)

_RATING_LABELS: dict[str, str] = {
    "strong_buy": "Strong buy",
    "buy": "Buy",
    "hold": "Hold",
    "sell": "Sell",
    "strong_sell": "Strong sell",
    "outperform": "Buy",
    "underperform": "Sell",
    "overweight": "Buy",
    "underweight": "Sell",
    "positive": "Buy",
    "negative": "Sell",
    "neutral": "Hold",
}

_RATING_SLUGS: dict[str, str] = {
    "Strong buy": "strong-buy",
    "Buy": "buy",
    "Hold": "hold",
    "Sell": "sell",
    "Strong sell": "strong-sell",
}

_RATING_RANK = {
    "Strong buy": 0,
    "Buy": 1,
    "Hold": 2,
    "Sell": 3,
    "Strong sell": 4,
}


def _from_mean(mean: float) -> str:
    """Yahoo recommendationMean is typically 1 (best) → 5 (worst)."""
    if mean <= 1.5:
        return "Strong buy"
    if mean <= 2.5:
        return "Buy"
    if mean <= 3.5:
        return "Hold"
    if mean <= 4.5:
        return "Sell"
    return "Strong sell"


def _format_recommendation_key(key: str | None) -> str | None:
    if not key:
        return None
    return key.replace("_", " ").strip().title()


def _build_reasons(
    rating: dict[str, Any],
    *,
    recommendation_key: str | None = None,
    recommendation_mean: float | None = None,
    upside_pct: float | None = None,
    target_price: float | None = None,
    last_price: float | None = None,
    analyst_count: int | None = None,
) -> list[str]:
    source = rating.get("source")
    if source == "analyst":
        label = _format_recommendation_key(recommendation_key) or "Consensus"
        reasons = [f"Yahoo Finance analyst consensus is “{label}”."]
        if analyst_count:
            reasons.append(f"{analyst_count} analysts contributed to this consensus.")
        return reasons

    if source == "analyst_mean" and recommendation_mean is not None:
        return [
            f"Yahoo mean recommendation score is {recommendation_mean:.2f} "
            f"(scale 1 = Strong buy → 5 = Strong sell).",
            f"Mapped to “{rating.get('label')}” using standard score bands.",
        ]

    if not rating.get("label"):
        if upside_pct is not None:
            return [
                f"Published target is {upside_pct:+.1f}% from the current price; this is external context, not a buy/sell rating.",
            ]
        return ["No covered external analyst consensus is available for this symbol."]

    return []


def resolve_analyst_rating(
    *,
    recommendation_key: str | None = None,
    recommendation_mean: float | None = None,
    upside_pct: float | None = None,
) -> dict[str, Any]:
    """Return display label, CSS slug, and how the rating was derived."""
    label: str | None = None
    source = "unavailable"

    if recommendation_key:
        normalized = recommendation_key.strip().lower().replace(" ", "_")
        label = _RATING_LABELS.get(normalized)
        if label:
            source = "analyst"

    if not label and recommendation_mean is not None:
        try:
            mean = float(recommendation_mean)
            if mean == mean:  # not NaN
                label = _from_mean(mean)
                source = "analyst_mean"
        except (TypeError, ValueError):
            pass

    if not label:
        source = "target_only" if upside_pct is not None else source
        return {"label": None, "slug": None, "source": source, "reasons": []}

    return {
        "label": label,
        "slug": _RATING_SLUGS.get(label, "hold"),
        "source": source,
        "rank": _RATING_RANK.get(label, 2),
        "reasons": [],
    }


def compute_rating(
    *,
    recommendation_key: str | None = None,
    recommendation_mean: float | None = None,
    upside_pct: float | None = None,
    target_price: float | None = None,
    last_price: float | None = None,
    analyst_count: int | None = None,
) -> dict[str, Any]:
    """Resolve rating label and human-readable reasons."""
    rating = resolve_analyst_rating(
        recommendation_key=recommendation_key,
        recommendation_mean=recommendation_mean,
        upside_pct=upside_pct,
    )
    rating["reasons"] = _build_reasons(
        rating,
        recommendation_key=recommendation_key,
        recommendation_mean=recommendation_mean,
        upside_pct=upside_pct,
        target_price=target_price,
        last_price=last_price,
        analyst_count=analyst_count,
    )
    return rating


def _safe_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed == parsed else None


def _env_float(name: str, default: float) -> float:
    parsed = _safe_float(os.getenv(name))
    return parsed if parsed is not None else default


def _safe_int(value: Any) -> int | None:
    parsed = _safe_float(value)
    return int(parsed) if parsed is not None else None


def _sentiment(label: str | None) -> ExternalAnalystSentiment:
    if label in {"Strong buy", "Buy"}:
        return ExternalAnalystSentiment.POSITIVE
    if label == "Hold":
        return ExternalAnalystSentiment.NEUTRAL
    if label in {"Sell", "Strong sell"}:
        return ExternalAnalystSentiment.NEGATIVE
    return ExternalAnalystSentiment.UNKNOWN


def _freshness(as_of: str | None) -> str:
    if not as_of:
        return "Publication date unavailable"
    try:
        observed = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=UTC)
        age = (datetime.now(UTC) - observed).days
    except (TypeError, ValueError):
        return "Publication date unavailable"
    stale_days = int(_env_float("EXTERNAL_ANALYST_STALE_DAYS", 120))
    return f"Stale ({age} days old)" if age > stale_days else f"Current ({age} days old)"


def build_external_analyst_view(
    *,
    recommendation_key: str | None = None,
    recommendation_mean: float | None = None,
    analyst_count: int | None = None,
    target_price: float | None = None,
    last_price: float | None = None,
    target_gap_pct: float | None = None,
    as_of: str | None = None,
    fetched_at: str | None = None,
    source: str = "Yahoo Finance",
) -> ExternalAnalystView:
    """Build non-actionable external context with coverage/outlier disclosure."""
    rating = resolve_analyst_rating(
        recommendation_key=recommendation_key,
        recommendation_mean=recommendation_mean,
    )
    label = rating.get("label")
    sentiment = _sentiment(label)
    gap = _safe_float(target_gap_pct)
    if gap is None:
        price = _safe_float(last_price)
        target = _safe_float(target_price)
        gap = round(((target - price) / price) * 100, 2) if price and target else None
    descriptor = (
        "above market"
        if gap is not None and gap > 5
        else "below market"
        if gap is not None and gap < -5
        else "near market"
        if gap is not None
        else "unavailable"
    )
    count = _safe_int(analyst_count)
    min_count = int(_env_float("EXTERNAL_ANALYST_MIN_COVERAGE", 3))
    low_coverage = count is not None and count < min_count
    outlier_high = _env_float("EXTERNAL_ANALYST_TARGET_OUTLIER_HIGH_PCT", 100)
    outlier_low = _env_float("EXTERNAL_ANALYST_TARGET_OUTLIER_LOW_PCT", -50)
    outlier = gap is not None and (gap > outlier_high or gap < outlier_low)
    conflicted = bool(
        gap is not None
        and (
            (sentiment is ExternalAnalystSentiment.POSITIVE and gap < -5)
            or (sentiment is ExternalAnalystSentiment.NEGATIVE and gap > 5)
        )
    )
    available = bool(label or gap is not None)
    status = (
        ExternalAnalystStatus.UNAVAILABLE
        if not available
        else ExternalAnalystStatus.OUTLIER
        if outlier
        else ExternalAnalystStatus.CONFLICTED
        if conflicted
        else ExternalAnalystStatus.LOW_COVERAGE
        if low_coverage
        else ExternalAnalystStatus.AVAILABLE
    )
    coverage = (
        "Coverage unavailable"
        if count is None
        else f"Low coverage ({count} analyst{'s' if count != 1 else ''})"
        if low_coverage
        else f"{count} analysts"
    )
    return ExternalAnalystView(
        status=status,
        sentiment=sentiment,
        consensus_label=label,
        recommendation_key=recommendation_key,
        recommendation_mean=_safe_float(recommendation_mean),
        analyst_count=count,
        target_price=_safe_float(target_price),
        target_gap_pct=round(gap, 2) if gap is not None else None,
        target_descriptor=descriptor,
        coverage_label=coverage,
        freshness_label=_freshness(as_of),
        as_of=as_of,
        fetched_at=fetched_at,
        source=source,
        actionable=False,
    )
