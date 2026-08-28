"""Regression tests for Street ratings, target semantics, and actionable setups."""

from __future__ import annotations

from modules.portfolio.services.chart_patterns import is_actionable_pattern
from modules.portfolio.services.market_data import (
    _apply_price_context,
    enforce_rating_semantics,
)
from modules.portfolio.services.stock_insights import _forecast


def test_52w_recovery_never_manufactures_strong_buy_or_target_upside():
    metrics = {
        "rating_label": "Strong buy",
        "rating_slug": "strong-buy",
        "rating_rank": 0,
        "rating_source": "price_52w",
        "upside_pct": 42.87,
        "pct_from_52w_high": -42.87,
        "target_price": None,
        "buy_thesis": "Generated from the invalid price-to-high B+ fallback.",
    }

    enforce_rating_semantics(metrics)

    assert metrics["rating_label"] is None
    assert metrics["rating_source"] == "unavailable"
    assert metrics["upside_pct"] is None
    assert metrics["recovery_to_52w_high_pct"] == 42.87
    assert metrics["buy_thesis"] is None


def test_analyst_consensus_and_target_are_preserved_as_street_evidence():
    metrics = {
        "rating_label": "Strong buy",
        "rating_slug": "strong-buy",
        "rating_rank": 0,
        "rating_source": "analyst",
        "upside_pct": 25,
        "target_price": 125,
    }

    enforce_rating_semantics(metrics)

    assert metrics["rating_label"] == "Strong buy"
    assert metrics["rating_source"] == "analyst"
    assert metrics["upside_pct"] == 25


def test_price_context_exposes_recovery_without_rating():
    metrics = {"pct_from_52w_high": -30.0, "upside_pct": None, "rating_label": None}

    _apply_price_context(metrics, last_price=70, exchange="NSE")

    assert metrics["recovery_to_52w_high_pct"] == 30
    assert metrics["upside_pct"] is None
    assert metrics["rating_label"] is None


def test_trailing_price_trend_is_not_extrapolated_into_forecast_or_rating():
    forecast = _forecast(
        {"regularMarketPrice": 120, "recommendationKey": "none"},
        {"prices": [100, 110, 120]},
        quantity=10,
        last_price=120,
    )

    assert forecast["method"] == "unavailable"
    assert forecast["target_price"] is None
    assert forecast["projected_value_1y"] is None
    assert forecast["rating"]["label"] is None


def test_completed_or_zero_move_pattern_is_not_an_actionable_setup():
    base = {
        "bias": "bullish",
        "lifecycle_state": "CONFIRMED",
        "target_status": "ACTIVE",
        "remaining_upside_pct": 20,
    }
    assert is_actionable_pattern(base) is True
    assert is_actionable_pattern({**base, "remaining_upside_pct": 0}) is False
    assert is_actionable_pattern(
        {
            **base,
            "lifecycle_state": "TARGET_ACHIEVED",
            "target_status": "ACHIEVED",
            "remaining_upside_pct": 0,
        }
    ) is False


def test_active_bearish_pattern_uses_positive_downside_risk_magnitude():
    pattern = {
        "bias": "bearish",
        "lifecycle_state": "CONFIRMED",
        "target_status": "ACTIVE",
        "remaining_downside_pct": 18.5,
        "upside_to_target_pct": -18.5,
    }
    assert is_actionable_pattern(pattern) is True
