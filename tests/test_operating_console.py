from __future__ import annotations

from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from main import app
from modules.portfolio.db import fund_intelligence, operating_console
from modules.portfolio.services.alerts import evaluate_alerts
from modules.portfolio.services.stress_testing import scenario_definition, stress_portfolio
from modules.portfolio.services.today_brief import build_today_brief
from modules.portfolio.services.what_if import simulate_rebalance
from shared.config import APP_ROOT_PATH


@pytest.fixture(autouse=True)
def empty_console_store():
    with operating_console.connect() as conn:
        conn.execute("DELETE FROM alert_history")
        conn.execute("DELETE FROM alert_state")
        conn.execute("DELETE FROM stress_scenarios")
    with fund_intelligence.connect() as conn:
        conn.execute("DELETE FROM fund_constituents")
        conn.execute("DELETE FROM fund_schemes")
    yield


def _recommendation(symbol: str, action: str, *, blocking=False):
    return {
        "symbol": symbol,
        "action": action,
        "data_quality_flags": ([{"code": "BLOCK", "blocking": True}] if blocking else []),
    }


def test_no_action_majority_is_summarized_correctly():
    recommendations = [_recommendation(f"H{index}", "HOLD") for index in range(8)] + [
        _recommendation("ADD", "ADD"), _recommendation("SELL", "SELL")
    ]
    brief = build_today_brief(
        family={"reconciliation": {"summary": {"family_value_reconciled_pct": 99.5}}},
        advisory={"recommendations": recommendations},
    )
    assert brief["no_action_count"] == 8
    assert brief["actions_require_review"] == 2
    assert "No action required on 8 holdings" in brief["summary_lines"]


def test_reconciliation_block_outranks_buy_signal():
    brief = build_today_brief(
        family={},
        advisory={"recommendations": [
            _recommendation("BUYME", "STRONG_ADD"),
            _recommendation("BLOCKED", "RECONCILE", blocking=True),
        ]},
    )
    assert brief["portfolio_status"] == "CRITICAL"
    assert brief["review_queue"][0]["symbol"] == "BLOCKED"
    assert brief["review_queue"][0]["priority"] == 1


def test_small_cap_stress_flows_through_etf_lookthrough():
    fund_intelligence.upsert_scheme({
        "instrument_id": "fund-small", "canonical_scheme_name": "Small ETF", "currency": "INR",
        "factsheet_source": "official", "factsheet_as_of": "2026-08-29", "instrument_type": "etf",
    })
    fund_intelligence.save_constituents(
        fund_instrument_id="fund-small", as_of="2026-08-29",
        rows=[{"underlying_instrument_id": "small-a", "weight_pct": 100, "market_cap": "Small"}],
        source="official", source_type="INDEX_FILE", coverage_type="FULL", coverage_pct=100,
    )
    result = stress_portfolio(
        [{"instrument_id": "fund-small", "current_value": 1000, "account_code": "A"}],
        scenario=scenario_definition("small_cap_correction"),
    )
    assert result["estimated_family_impact"] == -300
    assert result["largest_contributors"][0]["via"] == "fund-small"


def test_fx_shock_affects_only_relevant_holdings():
    scenario = {"name": "fx", "assumptions": {"currency_shocks": {"USD": 0.10}}}
    result = stress_portfolio([
        {"instrument_id": "usd", "current_value": 100, "currency": "USD", "account_code": "US"},
        {"instrument_id": "inr", "current_value": 100, "currency": "INR", "account_code": "IN"},
    ], scenario=scenario)
    impacts = {row["instrument_id"]: row["impact"] for row in result["largest_contributors"]}
    assert impacts["usd"] == 10
    assert impacts["inr"] == 0


def test_internal_cash_transfer_does_not_alter_family_exposure():
    positions = [{"instrument_id": "stock", "current_value": 1000, "account_code": "A"}]
    first = stress_portfolio(positions, scenario=scenario_definition("technology_compression"))
    transfers = [
        {"event_type": "TRANSFER_OUT", "amount": -100},
        {"event_type": "TRANSFER_IN", "amount": 100},
    ]
    assert transfers[0]["amount"] + transfers[1]["amount"] == 0
    second = stress_portfolio(positions, scenario=scenario_definition("technology_compression"))
    assert second["starting_family_value"] == first["starting_family_value"]


def test_what_if_never_mutates_source_portfolio():
    holdings = [{"instrument_id": "tiny", "current_value": 1, "sector": "A"}, {"instrument_id": "core", "current_value": 999, "sector": "B"}]
    original = deepcopy(holdings)
    result = simulate_rebalance(
        holdings,
        operations=[{"type": "sell_below_weight_pct", "threshold_pct": 0.3}],
        constraints={},
    )
    assert holdings == original
    assert result["source_portfolio_unchanged"] is True
    assert result["execution_enabled"] is False


def test_position_and_sector_caps_are_respected():
    holdings = [
        {"instrument_id": "a", "current_value": 800, "sector": "Tech"},
        {"instrument_id": "b", "current_value": 100, "sector": "Tech"},
        {"instrument_id": "c", "current_value": 100, "sector": "Other"},
    ]
    result = simulate_rebalance(
        holdings,
        operations=[{"type": "set_position_weight", "instrument_id": "a", "target_weight_pct": 90}],
        constraints={"max_position_pct": 40, "sector_cap_pct": 50},
    )
    assert result["after"]["largest_position_pct"] <= 40
    assert result["after"]["sector_weights"]["Tech"] <= 50


def test_tax_lot_block_prevents_execution_ready_result():
    result = simulate_rebalance(
        [{"instrument_id": "blocked", "current_value": 1, "sector": "A", "tax_lot_block": True}, {"instrument_id": "core", "current_value": 999, "sector": "B"}],
        operations=[{"type": "sell_below_weight_pct", "threshold_pct": 0.3}],
        constraints={},
    )
    blocked = next(row for row in result["proposals"] if row["instrument_id"] == "blocked")
    assert blocked["execution_ready"] is False
    assert "TAX_OR_CA_REVIEW_BLOCK" in blocked["binding_constraints"]


def test_cooldown_suppresses_repeated_low_value_churn():
    event = {"type": "ACTION_CHANGE", "key": "action:a", "from": "HOLD", "to": "ADD", "priority": "LOW"}
    first = evaluate_alerts([event], now=1000, cooldown_seconds=100)
    second = evaluate_alerts([event], now=1050, cooldown_seconds=100)
    assert len(first["alerts"]) == 1
    assert second["alerts"] == []
    assert second["suppressed"][0]["reason"] == "COOLDOWN_UNCHANGED_STATE"


def test_action_change_alerts_but_one_percent_price_move_does_not():
    result = evaluate_alerts([
        {"type": "ACTION_CHANGE", "key": "action:a", "from": "HOLD", "to": "SELL", "priority": "HIGH"},
        {"type": "PRICE_MOVE", "key": "price:a", "change_pct": 1},
    ], now=1000)
    assert [row["type"] for row in result["alerts"]] == ["ACTION_CHANGE"]
    assert result["suppressed"][0]["reason"] == "NON_MATERIAL_EVENT_TYPE"


def test_custom_scenario_assumptions_are_persisted():
    assumptions = {"sector_shocks": {"TEST": -0.42}, "default_shock": -0.01}
    scenario = scenario_definition("custom", assumptions)
    saved = operating_console.save_scenario(name="My shock", assumptions=scenario["assumptions"])
    loaded = operating_console.get_scenario(saved["scenario_id"])
    assert loaded["assumptions"] == assumptions
    assert loaded["methodology_version"] == "stress-v1"


def test_stress_output_exposes_coverage_and_limitations():
    result = stress_portfolio(
        [{"instrument_id": "unknown", "current_value": 100, "sector": "Unknown"}],
        scenario={"name": "custom", "assumptions": {"sector_shocks": {"TECH": -0.2}}},
    )
    assert result["coverage_pct"] == 0
    assert result["model_limitations"]
    assert result["execution_enabled"] is False


def test_api_v1_remains_compatible():
    client = TestClient(app)
    api = f"{APP_ROOT_PATH}/api/portfolio"
    version = client.get(f"{api}/version").json()["contract_version"]
    response = client.get(f"{api}/stress/scenarios")
    assert response.status_code == 200
    assert "small_cap_correction" in response.json()["library"]
    assert client.get(f"{api}/version").json()["contract_version"] == version
