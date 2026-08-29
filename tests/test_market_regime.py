from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from main import app
from modules.portfolio.db import market_regime
from modules.portfolio.services.market_regime import (
    COMPONENTS,
    METHODOLOGY_VERSION,
    band_and_regime,
    calculate_and_store,
    calculate_mrmi,
)
from modules.portfolio.services.mrmi_advisory import execution_overlay
from modules.portfolio.services.mrmi_backtest import run_backtest
from shared.config import APP_ROOT_PATH


@pytest.fixture(autouse=True)
def empty_history():
    with market_regime.connect() as conn:
        conn.execute("DELETE FROM market_regime_observations")
    yield


def _inputs(as_of: str = "2026-08-29"):
    return {
        name: {
            "raw_value": (spec.lower + spec.upper) / 2,
            "source": f"official-{name}",
            "source_as_of": as_of,
        }
        for name, spec in COMPONENTS.items()
    }


def test_complete_data_produces_deterministic_score():
    first = calculate_mrmi(_inputs(), as_of="2026-08-29", history=[])
    second = calculate_mrmi(_inputs(), as_of="2026-08-29", history=[])
    assert first == second
    assert first["score"] == 50
    assert first["component_coverage_pct"] == 100


def test_missing_component_reweights_and_lowers_confidence():
    complete = calculate_mrmi(_inputs(), as_of="2026-08-29", history=[])
    partial_inputs = _inputs()
    partial_inputs.pop("market_breadth")
    partial = calculate_mrmi(partial_inputs, as_of="2026-08-29", history=[])
    assert partial["score"] == 50
    assert sum(row["effective_weight"] for row in partial["components"]) == pytest.approx(100, abs=0.05)
    assert partial["confidence"] < complete["confidence"]
    assert "PARTIAL_COMPONENT_COVERAGE" in partial["data_quality_flags"]


def test_stale_fpi_data_is_flagged():
    inputs = _inputs()
    inputs["fpi_flow_regime"]["source_as_of"] = "2026-08-20"
    result = calculate_mrmi(inputs, as_of="2026-08-29", history=[])
    assert "STALE_FPI_FLOW" in result["data_quality_flags"]


def test_extreme_vix_is_bounded_by_configured_weight():
    baseline = calculate_mrmi(_inputs(), as_of="2026-08-29", history=[])
    inputs = _inputs()
    inputs["volatility_regime"]["raw_value"] = 100
    stressed = calculate_mrmi(inputs, as_of="2026-08-29", history=[])
    assert baseline["score"] - stressed["score"] <= 7.5


@pytest.mark.parametrize(
    ("score", "band"),
    [(0, "EXTREME_FEAR"), (19.999, "EXTREME_FEAR"), (20, "FEAR"), (40, "NEUTRAL"), (60, "GREED"), (80, "EXTREME_GREED"), (100, "EXTREME_GREED")],
)
def test_score_band_boundaries_are_stable(score, band):
    assert band_and_regime(score)[0] == band


def test_trend_requires_prior_observation():
    first = calculate_mrmi(_inputs(), as_of="2026-08-29", history=[])
    assert first["trend"] == "STABLE"
    assert "INSUFFICIENT_HISTORY_FOR_TREND" in first["data_quality_flags"]
    stronger = _inputs("2026-08-30")
    stronger["market_breadth"]["raw_value"] = 80
    second = calculate_mrmi(stronger, as_of="2026-08-30", history=[first])
    assert second["trend"] == "IMPROVING"


def test_methodology_version_is_persisted():
    saved = calculate_and_store(_inputs(), as_of="2026-08-29", observation_state="FINALIZED")
    assert saved["methodology_version"] == METHODOLOGY_VERSION
    assert market_regime.latest(finalized_only=True)["methodology_version"] == METHODOLOGY_VERSION


def test_mrmi_cannot_create_buy_or_sell():
    mood = calculate_mrmi(_inputs(), as_of="2026-08-29", history=[])
    overlay = execution_overlay({"action": "WATCH", "sell_type": "NONE"}, mood)
    assert overlay["action_unchanged"] == "WATCH"


def test_fundamental_sell_dominates_euphoric_mood():
    mood = {"score": 90, "band": "EXTREME_GREED", "regime": "EUPHORIC", "trend": "STABLE"}
    overlay = execution_overlay({"action": "SELL", "sell_type": "FUNDAMENTAL_SELL"}, mood)
    assert overlay["action_unchanged"] == "SELL"
    assert overlay["deployment_pace"] == "NORMAL"


def test_greed_causes_no_chase_sizing_only():
    mood = {"score": 75, "band": "GREED", "regime": "RISK_ON", "trend": "IMPROVING"}
    overlay = execution_overlay({"action": "ADD", "sell_type": "NONE"}, mood)
    assert overlay["action_unchanged"] == "ADD"
    assert overlay["tranche_pct"] == 40
    assert overlay["deployment_pace"] == "WAIT_FOR_RETEST_NO_CHASE"


def test_backtest_has_no_lookahead():
    start = date(2025, 1, 1)
    prices = [
        {"date": (start + timedelta(days=index)).isoformat(), "close": 100 + index}
        for index in range(200)
    ]
    result = run_backtest(
        [{"as_of": "2025-01-05", "score": 50, "band": "NEUTRAL"}],
        prices,
        final_test_start="2025-06-01",
    )
    sample = result["samples"][0]
    assert sample["price_date"] <= sample["as_of"]
    assert sample["forward_1m_pct"] > 0
    assert result["lookahead_guard"]


def test_historical_score_is_append_only_and_reproducible():
    first = calculate_and_store(_inputs(), as_of="2026-08-29")
    changed = _inputs()
    changed["market_breadth"]["raw_value"] = 80
    second = calculate_and_store(changed, as_of="2026-08-29")
    assert second["observation_id"] == first["observation_id"]
    assert second["score"] == first["score"]


def test_ui_uses_original_branding_and_language():
    template = Path("shared/web/templates/portfolio/market_regime.html").read_text()
    assert "Market Regime & Mood" in template
    assert ("Tick" + "ertape") not in template
    assert "not a return forecast" in template


def test_api_v1_remains_compatible():
    client = TestClient(app)
    api = f"{APP_ROOT_PATH}/api/portfolio"
    version = client.get(f"{api}/version").json()["contract_version"]
    response = client.post(
        f"{api}/market-regime/observations",
        json={"as_of": "2026-08-29", "observation_state": "FINALIZED", "components": _inputs()},
    )
    assert response.status_code == 200
    assert response.json()["methodology_version"] == METHODOLOGY_VERSION
    assert client.get(f"{api}/market-regime/current?finalized_only=true").status_code == 200
    assert client.get(f"{api}/version").json()["contract_version"] == version
