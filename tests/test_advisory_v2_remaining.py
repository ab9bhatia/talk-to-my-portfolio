"""Milestones 3-5: pattern conflicts, providers, planning API, and agent guardrails."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from main import app
from modules.portfolio.services.advisory.providers import (
    enrich_family_with_cached_evidence,
    refresh_providers,
)
from modules.portfolio.services.advisory.rebalance import evaluate_rebalance
from modules.portfolio.services.advisory.runtime import advisory_for_llm
from modules.portfolio.services.advisory.service import build_advisory_payload
from modules.portfolio.services.portfolio_agent import _validate_agent_response
from shared.config import APP_ROOT_PATH


def _expected_inputs(base_irr_pct: float) -> dict[str, Any]:
    def eps_for(irr: float) -> float:
        return round(100 * ((1 + irr / 100) ** 3) / 20, 4)

    return {
        "method": "eps",
        "source": "official filing fixture",
        "source_type": "official_filing",
        "as_of": "2026-08-27",
        "scenarios": {
            "bear": {"eps_year3": eps_for(base_irr_pct - 8), "exit_multiple": 20},
            "base": {"eps_year3": eps_for(base_irr_pct), "exit_multiple": 20},
            "bull": {"eps_year3": eps_for(base_irr_pct + 8), "exit_multiple": 20},
        },
    }


def _pattern(bias: str = "bullish") -> dict[str, Any]:
    return {
        "patterns": [
            {
                "pattern": "cup_handle" if bias == "bullish" else "head_shoulders",
                "label": "Cup & handle" if bias == "bullish" else "Head & shoulders",
                "bias": bias,
                "status": "confirmed",
                "confidence": 82,
                "as_of": "2026-08-28",
                "target_price": 125 if bias == "bullish" else 80,
                "target_date": "2026-10-15",
                "upside_to_target_pct": 25 if bias == "bullish" else -20,
                "note": "Synthetic deterministic setup.",
            }
        ]
    }


def _holding(symbol: str, **overrides: Any) -> dict[str, Any]:
    row = {
        "symbol": symbol,
        "exchange": "NSE",
        "quantity": 1,
        "last_price": 100,
        "current_value": 100,
        "invested": 100,
        "roce": 22,
        "debt_to_equity": 0.4,
        "free_cash_flow_positive": True,
        "revenue_growth_pct": 18,
        "earnings_growth_pct": 20,
        "earnings_revision_pct": 4,
        "moat_score": 7,
        "governance_risk": "none",
        "return_1m_pct": 4,
        "return_3m_pct": 8,
        "return_6m_pct": 12,
        "return_12m_pct": 20,
        "momentum_as_of": "2026-08-28",
        "account_id": "fixture",
        "account_code": "FX",
        "broker": "custom",
    }
    row.update(overrides)
    return row


def _family(rows: list[dict[str, Any]], *, total: float = 1_000) -> dict[str, Any]:
    return {
        "cached_at": "2026-08-28T08:00:00Z",
        "summary": {"total_current_value": total},
        "portfolios": [
            {
                "account_id": "fixture",
                "account_code": "FX",
                "broker": "custom",
                "summary": {"total_current_value": sum(row["current_value"] for row in rows)},
                "holdings": rows,
            }
        ],
    }


def _advisory(rows: list[dict[str, Any]], *, total: float = 1_000) -> dict[str, Any]:
    return build_advisory_payload(
        _family(rows, total=total),
        goals={"max_position_pct": 20},
        generated_at="2026-08-28T09:00:00Z",
    )


def _rec(payload: dict[str, Any], symbol: str) -> dict[str, Any]:
    return next(row for row in payload["recommendations"] if row["symbol"] == symbol)


def test_bullish_pattern_never_overrides_sourced_fundamental_sell():
    row = _holding(
        "BROKEN",
        chart_patterns=_pattern(),
        governance_risk="high",
        governance_event="Material sourced governance breach.",
        governance_event_source="NSE filing fixture",
        governance_event_as_of="2026-08-28",
        governance_event_source_type="exchange",
    )
    rec = _rec(_advisory([row]), "BROKEN")
    assert rec["action"] == "SELL"
    assert rec["sell_type"] == "FUNDAMENTAL_SELL"
    assert rec["sell_pct"] == 100
    assert "BULLISH_PATTERN_FUNDAMENTAL_SELL_PRESERVED" in rec["decision_conflicts"]


def test_bullish_pattern_changes_timing_only_not_action_or_size():
    tactical = _holding(
        "TACTICAL",
        chart_patterns=_pattern(),
        expected_return_inputs=_expected_inputs(9),
    )
    tactical_rec = _rec(_advisory([tactical]), "TACTICAL")
    assert tactical_rec["action"] == "REDUCE"
    assert tactical_rec["sell_type"] == "TACTICAL_REDUCE"
    assert tactical_rec["sell_pct"] == 25
    assert "CHART_PATTERN_TIMING_ONLY" in {
        row["rule"] for row in tactical_rec["rule_trace"]
    }

    tiny = _holding(
        "TINY",
        chart_patterns=_pattern(),
        expected_return_inputs=_expected_inputs(5),
    )
    tiny_rec = _rec(_advisory([tiny], total=50_000), "TINY")
    assert tiny_rec["action"] == "SELL"
    assert tiny_rec["sell_type"] == "PORTFOLIO_CONSOLIDATION"
    assert tiny_rec["sell_pct"] == 100
    assert "TIMING_VS_DECISION" in tiny_rec["conflict_categories"]


def test_pattern_alone_cannot_create_buy_or_sell():
    bullish = _rec(_advisory([_holding("BULL", chart_patterns=_pattern())]), "BULL")
    bearish = _rec(
        _advisory([_holding("BEAR", chart_patterns=_pattern("bearish"))]),
        "BEAR",
    )
    assert bullish["action"] == "WATCH"
    assert bearish["action"] == "WATCH"
    assert bullish["sell_pct"] == bearish["sell_pct"] == 0


def test_building_or_completed_pattern_does_not_postpone_planned_reduction():
    building = _pattern()
    building["patterns"][0].update(
        {"status": "early", "lifecycle_state": "BUILDING", "target_status": "ACTIVE"}
    )
    completed = _pattern()
    completed["patterns"][0].update(
        {
            "lifecycle_state": "TARGET_ACHIEVED",
            "target_status": "ACHIEVED",
            "current_price": 126,
            "target_price": 125,
            "remaining_upside_pct": 0,
        }
    )

    for symbol, pattern in (("BUILDING", building), ("COMPLETE", completed)):
        recommendation = _rec(
            _advisory(
                [
                    _holding(
                        symbol,
                        chart_patterns=pattern,
                        expected_return_inputs=_expected_inputs(9),
                    )
                ]
            ),
            symbol,
        )
        assert recommendation["action"] == "REDUCE"
        assert recommendation["sell_pct"] == 25
        assert recommendation["decision_conflicts"] == []


class _FixtureProvider:
    name = "fixture"

    def __init__(self, rows: list[dict[str, Any]]):
        self.rows = rows

    def fetch(self) -> list[dict[str, Any]]:
        return self.rows


def test_provider_cache_uses_fresh_authoritative_evidence_and_excludes_stale():
    now = 1_800_000_000.0
    rows = [
        {
            "symbol": "PROVIDERFRESH",
            "exchange": "NSE",
            "field": "business_thesis",
            "value": "Sourced fixture thesis.",
            "source": "Exchange filing fixture",
            "source_url": "https://example.invalid/filing",
            "source_type": "exchange",
            "as_of": "2026-08-28",
            "expires_at": now + 100,
        },
        {
            "symbol": "PROVIDERSTALE",
            "exchange": "NSE",
            "field": "business_thesis",
            "value": "Must not be used.",
            "source": "Old exchange filing fixture",
            "source_type": "exchange",
            "as_of": "2025-01-01",
            "expires_at": now - 1,
        },
    ]
    status = refresh_providers([_FixtureProvider(rows)], now=now)
    assert status["accepted"] == 2
    family = _family([_holding("PROVIDERFRESH"), _holding("PROVIDERSTALE")])
    result = enrich_family_with_cached_evidence(family, now=now)
    holdings = family["portfolios"][0]["holdings"]
    assert holdings[0]["business_thesis"] == "Sourced fixture thesis."
    assert "business_thesis" not in holdings[1]
    assert "STALE_EXTERNAL_EVIDENCE" in holdings[1]["data_quality_flags"]
    assert result["used"] >= 1
    assert result["stale"] >= 1


def test_provider_rejects_unsourced_decision_fields():
    status = refresh_providers(
        [
            _FixtureProvider(
                [
                    {
                        "symbol": "UNSOURCED",
                        "field": "governance_risk",
                        "value": "high",
                        "source": "Anonymous note",
                        "source_type": "market_data",
                        "as_of": "2026-08-28",
                    }
                ]
            )
        ],
        now=1_800_000_000.0,
    )
    assert status["accepted"] == 0
    assert "requires an authoritative source_type" in status["rejected"][0]


def test_agent_validator_replaces_model_trade_decisions_with_deterministic_action():
    advisory = _advisory([_holding("WAIT")])
    parsed = {
        "answer": "Sell WAIT and buy INVENTED.",
        "symbols": [
            {"symbol": "WAIT", "deterministic_action": "SELL", "explanation": "Model idea"},
            {"symbol": "INVENTED", "deterministic_action": "ADD"},
        ],
        "sell_or_trim": [{"symbol": "WAIT", "action": "exit", "rationale": "Model idea"}],
        "rebalance": [{"action": "buy", "detail": "invented"}],
    }
    validated = _validate_agent_response(parsed, context={"advisory": advisory})
    assert validated["schema_version"] == "advisor-conversation-v2"
    assert validated["symbols"][0]["deterministic_action"] == "WATCH"
    assert validated["buy"] == []
    assert validated["sell_or_trim"] == []
    assert validated["rebalance"] == []
    assert any("unknown symbol" in row.lower() for row in validated["warnings"])


def test_rebalance_evaluator_blocks_contradictory_buy_and_never_enables_execution():
    advisory = _advisory([_holding("WAIT")])
    result = evaluate_rebalance(
        advisory,
        [{"symbol": "WAIT", "target_weight_pct": 15}],
        max_position_pct=20,
        cash_buffer_pct=5,
    )
    assert result["accepted"] is False
    assert result["execution_enabled"] is False
    assert any("conflicts with deterministic action WATCH" in row for row in result["violations"])


def test_sale_proceeds_redeploy_only_to_existing_deterministic_add_position():
    sell = _holding("SELLER", expected_return_inputs=_expected_inputs(9))
    add = _holding("BUILDER", expected_return_inputs=_expected_inputs(30))
    advisory = _advisory([sell, add])
    assert advisory["proceeds_by_account"]["FX"] > 0
    plan = advisory["reinvestment_plan"]
    assert plan[0]["destination"] == "BUILDER"
    seller = _rec(advisory, "SELLER")
    assert seller["replacement_plan"][0]["destination"] == "BUILDER"
    assert sum(row["amount"] for row in plan) == advisory["proceeds_by_account"]["FX"]


def test_llm_privacy_redaction_removes_account_values_and_tax_details():
    advisory = _advisory([_holding("PRIVATE")])
    advisory["proceeds_by_account"] = {"FX": 123}
    safe = advisory_for_llm(advisory)
    account = safe["recommendations"][0]["accounts"][0]
    assert "account_id" not in account
    assert "current_value" not in account
    assert safe["proceeds_by_account"] == {}
    assert safe["privacy"]["account_tax_context_shared_with_llm"] is False


def test_advisory_routes_are_additive_and_not_captured_by_account_route(monkeypatch):
    payload = _advisory([_holding("ROUTE")])
    payload["fingerprint"] = "abc123"
    payload["runtime"] = {"execution_enabled": False}

    from modules.portfolio.services.advisory import runtime

    monkeypatch.setattr(runtime, "build_live_advisory", lambda **_kwargs: payload)
    client = TestClient(app)
    response = client.get(f"{APP_ROOT_PATH}/api/portfolio/advisory")
    assert response.status_code == 200
    assert response.headers["etag"] == '"abc123"'
    symbol = client.get(f"{APP_ROOT_PATH}/api/portfolio/advisory/ROUTE")
    assert symbol.status_code == 200
    assert symbol.json()["recommendation"]["symbol"] == "ROUTE"
    deadlines = client.get(f"{APP_ROOT_PATH}/api/portfolio/advisory/deadlines")
    assert deadlines.status_code == 200
    evidence = client.get(f"{APP_ROOT_PATH}/api/portfolio/advisory/evidence/status")
    assert evidence.status_code == 200
    page = client.get(f"{APP_ROOT_PATH}/portfolio/advisor")
    assert page.status_code == 200
    assert "Advisor Action Center" in page.text
