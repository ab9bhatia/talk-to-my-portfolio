"""Safety and determinism tests for lower-confidence return screening."""

from __future__ import annotations

from modules.portfolio.services.advisory.providers import _validate
from modules.portfolio.services.advisory.screening_returns import build_screening_return_inputs
from modules.portfolio.services.advisory.service import build_advisory_payload


def _screen(**overrides):
    holding = {
        "symbol": "SCREEN",
        "instrument_type": "equity",
        "last_price": 100,
        "trailing_eps": 5,
        "trailing_pe": 20,
        "earnings_growth_pct": 15,
        **overrides,
    }
    return build_screening_return_inputs(
        holding,
        source="dated market fixture",
        source_type="derived_market_model",
        as_of="2026-08-28",
        source_url="https://example.test/SCREEN",
    )


def _advisory(expected_return_inputs, *, total=1_000):
    holding = {
        "symbol": "SCREEN",
        "exchange": "NSE",
        "quantity": 1,
        "last_price": 100,
        "current_value": 100,
        "invested": 100,
        "roce": 20,
        "debt_to_equity": 0.5,
        "earnings_growth_pct": 15,
        "account_id": "fixture",
        "account_code": "FX",
        "broker": "custom",
        "expected_return_inputs": expected_return_inputs,
    }
    family = {
        "cached_at": "2026-08-28T08:00:00Z",
        "summary": {"total_current_value": total},
        "portfolios": [
            {
                "account_id": "fixture",
                "account_code": "FX",
                "broker": "custom",
                "summary": {"total_current_value": 100},
                "holdings": [holding],
            }
        ],
    }
    return build_advisory_payload(
        family,
        goals={"max_position_pct": 20},
        generated_at="2026-08-28T09:00:00Z",
    )["recommendations"][0]


def test_equity_screen_is_deterministic_and_source_attributed():
    first = _screen()
    second = _screen()
    assert first == second
    assert first["model_quality"] == "screening_proxy"
    assert first["source"] == "dated market fixture"
    assert first["as_of"] == "2026-08-28"
    assert first["scenarios"]["bear"]["eps_year3"] < first["scenarios"]["base"]["eps_year3"]
    assert first["scenarios"]["base"]["eps_year3"] < first["scenarios"]["bull"]["eps_year3"]


def test_equity_screen_requires_positive_eps_or_pe_and_a_growth_driver():
    assert _screen(trailing_eps=None, trailing_pe=None) is None
    assert _screen(earnings_growth_pct=None, forward_eps=None, revenue_growth_pct=None) is None


def test_fund_screen_uses_only_trailing_three_year_return():
    model = build_screening_return_inputs(
        {
            "instrument_type": "etf",
            "return_3y_cagr_pct": 14,
            "last_price": 100,
        },
        source="adjusted price history fixture",
        as_of="2026-08-28",
    )
    assert model["method"] == "fund_build_up"
    assert model["scenarios"]["base"]["earnings_growth_pct"] == 14
    assert model["drivers"] == ["trailing_3y_total_return_cagr"]


def test_screening_model_caps_confidence_and_downgrades_strong_add():
    recommendation = _advisory(_screen(earnings_growth_pct=30))
    assert recommendation["evidence_state"] == "SCREENING_MODEL"
    assert recommendation["action"] == "ADD"
    assert recommendation["action_confidence"] <= 55
    assert "EXPECTED_RETURN_SCREENING_PROXY" in {
        row["code"] for row in recommendation["data_quality_flags"]
    }


def test_screening_model_cannot_create_full_exit():
    recommendation = _advisory(_screen(earnings_growth_pct=-10), total=50_000)
    assert recommendation["action"] == "REDUCE"
    assert recommendation["sell_type"] == "TACTICAL_REDUCE"
    assert recommendation["sell_pct"] <= 25


def test_missing_model_is_exposed_as_needs_data():
    recommendation = _advisory(None)
    assert recommendation["action"] == "WATCH"
    assert recommendation["evidence_state"] == "NEEDS_DATA"


def test_provider_accepts_only_explicit_screening_tier_for_non_authoritative_return_model():
    row = {
        "symbol": "SCREEN",
        "exchange": "NSE",
        "field": "expected_return_inputs",
        "value": _screen(),
        "source": "dated market fixture",
        "source_type": "derived_market_model",
        "as_of": "2026-08-28",
    }
    validated = _validate(row, provider="fixture", now=1_800_000_000)
    assert validated["authoritative"] is False
