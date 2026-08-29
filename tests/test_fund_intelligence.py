from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from main import app
from modules.portfolio.db import fund_intelligence as store
from modules.portfolio.services.fund_intelligence import (
    consolidation_candidates,
    etf_analytics,
    family_lookthrough,
    lookthrough,
    pairwise_overlap,
)
from shared.config import APP_ROOT_PATH


@pytest.fixture(autouse=True)
def empty_fund_store():
    with store.connect() as conn:
        conn.execute("DELETE FROM fund_constituents")
        conn.execute("DELETE FROM fund_schemes")
    yield


def _scheme(instrument_id: str, **extra):
    row = {
        "instrument_id": instrument_id,
        "canonical_scheme_name": extra.pop("canonical_scheme_name", instrument_id),
        "currency": "INR",
        "factsheet_source": "official-factsheet",
        "factsheet_as_of": "2026-08-29",
        "instrument_type": extra.pop("instrument_type", "etf"),
        "ter_pct": extra.pop("ter_pct", 0.2),
        **extra,
    }
    return store.upsert_scheme(row)


def _holdings(fund_id: str, weights: dict[str, float], **extra):
    return store.save_constituents(
        fund_instrument_id=fund_id,
        as_of=extra.pop("as_of", "2026-08-29"),
        rows=[{"underlying_instrument_id": key, "weight_pct": value} for key, value in weights.items()],
        source="official-factsheet",
        source_type="AMC_FACTSHEET",
        coverage_type=extra.pop("coverage_type", "FULL"),
        coverage_pct=extra.pop("coverage_pct", 100),
    )


def test_two_nifty_etfs_have_high_dated_lookthrough_overlap():
    _scheme("fund-nifty-a")
    _scheme("fund-nifty-b")
    weights = {f"stock-{index}": 20 for index in range(5)}
    _holdings("fund-nifty-a", weights)
    _holdings("fund-nifty-b", weights)
    assert pairwise_overlap("fund-nifty-a", "fund-nifty-b")["weighted_overlap_pct"] == 100


def test_momentum_and_broad_index_have_partial_overlap():
    _scheme("fund-broad")
    _scheme("fund-momentum")
    _holdings("fund-broad", {"a": 25, "b": 25, "c": 25, "d": 25})
    _holdings("fund-momentum", {"a": 25, "b": 25, "x": 25, "y": 25})
    assert pairwise_overlap("fund-broad", "fund-momentum")["weighted_overlap_pct"] == 50


def test_direct_stock_duplication_across_funds_preserves_family_value():
    _scheme("fund-one")
    _scheme("fund-two")
    _holdings("fund-one", {"stock-a": 50, "stock-b": 50})
    _holdings("fund-two", {"stock-a": 25, "stock-c": 75})
    result = family_lookthrough([
        {"instrument_id": "stock-a", "current_value": 100},
        {"instrument_id": "fund-one", "current_value": 100},
        {"instrument_id": "fund-two", "current_value": 100},
    ])
    stock = next(row for row in result["underlying_exposures"] if row["underlying_instrument_id"] == "stock-a")
    assert result["family_value"] == 300
    assert stock["value"] == 175
    assert stock["direct_value"] == 100
    assert stock["through_funds_value"] == 75


def test_fund_of_fund_cycle_terminates_safely():
    _scheme("fund-a")
    _scheme("fund-b")
    _holdings("fund-a", {"fund-b": 100})
    _holdings("fund-b", {"fund-a": 100})
    result = lookthrough("fund-a")
    assert result["exposures"] == {}
    assert "FUND_OF_FUND_CYCLE_BLOCKED" in result["data_quality_flags"]


def test_stale_holdings_lower_confidence():
    _scheme("fund-stale")
    _holdings("fund-stale", {"stock-a": 100}, as_of="2026-01-01")
    result = lookthrough("fund-stale", as_of="2026-08-29")
    assert result["confidence"] == 60
    assert "STALE_FUND_HOLDINGS" in result["data_quality_flags"]


def test_top_holdings_only_is_labelled_partial_coverage():
    _scheme("fund-top")
    _holdings("fund-top", {"stock-a": 20, "stock-b": 15}, coverage_type="TOP_HOLDINGS", coverage_pct=35)
    result = lookthrough("fund-top")
    assert result["coverage_type"] == "TOP_HOLDINGS"
    assert result["coverage_pct"] == 35
    assert "PARTIAL_LOOKTHROUGH_COVERAGE" in result["data_quality_flags"]


def test_direct_and_regular_plans_remain_separate_instruments():
    _scheme("fund-direct", canonical_scheme_name="Alpha Fund", scheme_plan="Direct", scheme_option="Growth")
    _scheme("fund-regular", canonical_scheme_name="Alpha Fund", scheme_plan="Regular", scheme_option="Growth")
    schemes = store.list_schemes()
    assert {row["instrument_id"] for row in schemes} == {"fund-direct", "fund-regular"}


def test_etf_market_order_warning_triggers_on_poor_liquidity():
    scheme = _scheme("fund-illiquid", bid_ask_spread_pct=0.8, average_traded_value=500_000)
    result = etf_analytics(scheme)
    assert result["market_order_warning"] is True
    assert "limit order" in result["execution_note"].lower()


def test_tracking_difference_is_not_tracking_error():
    scheme = _scheme("fund-track", tracking_error_pct=0.35, tracking_difference_pct=-0.72)
    result = etf_analytics(scheme)
    assert result["tracking_error_pct"] == 0.35
    assert result["tracking_difference_pct"] == -0.72


def test_consolidation_includes_tax_and_exit_load_review():
    _scheme("fund-expensive", ter_pct=1.1, exit_load="1% within one year")
    _scheme("fund-cheap", ter_pct=0.2)
    weights = {"a": 50, "b": 50}
    _holdings("fund-expensive", weights)
    _holdings("fund-cheap", weights)
    result = consolidation_candidates([
        {"instrument_id": "fund-expensive", "current_value": 100},
        {"instrument_id": "fund-cheap", "current_value": 100},
    ])
    assert result[0]["preferred_destination"] == "fund-cheap"
    assert result[0]["tax_exit_load_review_required"] is True


def test_missing_constituents_never_fabricate_overlap():
    _scheme("fund-missing-a")
    _scheme("fund-missing-b")
    result = pairwise_overlap("fund-missing-a", "fund-missing-b")
    assert result["status"] == "LOOKTHROUGH_UNAVAILABLE"
    assert result["weighted_overlap_pct"] is None
    assert "LOOKTHROUGH_UNAVAILABLE" in result["data_quality_flags"]


def test_api_v1_remains_compatible():
    _scheme("fund-api")
    client = TestClient(app)
    api = f"{APP_ROOT_PATH}/api/portfolio"
    version = client.get(f"{api}/version").json()["contract_version"]
    response = client.get(f"{api}/funds/fund-api/lookthrough")
    assert response.status_code == 200
    assert response.json()["status"] == "LOOKTHROUGH_UNAVAILABLE"
    assert client.get(f"{api}/version").json()["contract_version"] == version
