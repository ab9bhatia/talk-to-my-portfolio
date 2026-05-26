"""Map Yahoo analyst data and upside into buy/hold/sell labels."""

from __future__ import annotations

from typing import Any

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


def _from_upside(upside_pct: float) -> str:
    if upside_pct >= 20:
        return "Strong buy"
    if upside_pct >= 10:
        return "Buy"
    if upside_pct >= -5:
        return "Hold"
    if upside_pct >= -15:
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

    if source == "upside" and upside_pct is not None:
        reasons = [
            f"No published analyst consensus; signal derived from target-price upside ({upside_pct:+.1f}%).",
        ]
        if target_price is not None and last_price is not None:
            reasons.append(
                f"Analyst mean target ₹{target_price:,.2f} vs your LTP ₹{last_price:,.2f}."
            )
        reasons.append(
            "Bands: ≥20% Strong buy · ≥10% Buy · ≥−5% Hold · ≥−15% Sell · below Strong sell."
        )
        return reasons

    if not rating.get("label"):
        return [
            "No Yahoo analyst consensus, mean score, or analyst target available for this symbol.",
        ]

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

    if not label and upside_pct is not None:
        try:
            upside = float(upside_pct)
            if upside == upside:
                label = _from_upside(upside)
                source = "upside"
        except (TypeError, ValueError):
            pass

    if not label:
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

