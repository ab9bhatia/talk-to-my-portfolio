from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from main import app
from modules.portfolio.db import research
from modules.portfolio.services.research_compare import compare_instruments
from modules.portfolio.services.research_context import build_research_llm_context
from modules.portfolio.services.research_events import assess_event, candidate_is_recommendable
from modules.portfolio.services.research_scorecards import build_scorecard
from modules.portfolio.services.research_screener import run_screen
from shared.config import APP_ROOT_PATH


@pytest.fixture(autouse=True)
def empty_research_workspace():
    with research.connect() as conn:
        for table in (
            "saved_screen_revisions", "saved_screens", "candidate_universe",
            "watchlist_entries", "thesis_journal", "research_events",
        ):
            conn.execute(f"DELETE FROM {table}")
    yield


def _instrument(kind="equity"):
    return {
        "instrument_id": f"ins-{kind}-test",
        "canonical_symbol": kind.upper(),
        "display_name": f"Synthetic {kind}",
        "instrument_type": kind,
    }


def _full_evidence(**extra):
    return {
        "evidence_as_of": "2026-08-29",
        "sector": "Industrials",
        "roce_pct": 20,
        "free_cash_flow_margin_pct": 12,
        "debt_to_equity": 0.5,
        "interest_coverage": 8,
        "revenue_growth_pct": 18,
        "earnings_growth_pct": 20,
        "expected_return_base_pct": 20,
        "valuation_percentile": 40,
        "momentum_score_pct": 65,
        "drawdown_pct": -10,
        "governance_evidence_score": 90,
        "ownership_trend_score": 60,
        "portfolio_fit_score": 80,
        **extra,
    }


def test_bank_scorecard_does_not_use_industrial_debt_equity():
    evidence = _full_evidence(
        sector="Banking", roa_pct=1.8, net_npa_pct=0.8,
        gross_npa_pct=2.2, capital_adequacy_pct=18, debt_to_equity=15,
    )
    scorecard = build_scorecard(_instrument(), evidence)
    assert scorecard["adapter"] == "bank"
    fields = {
        item["field"]
        for dimension in scorecard["dimensions"].values()
        for item in dimension.get("formula_inputs", [])
    }
    assert "debt_to_equity" not in fields
    assert "net_npa_pct" in fields


def test_etf_scorecard_does_not_use_corporate_roce():
    scorecard = build_scorecard(
        _instrument("etf"),
        _full_evidence(tracking_error_pct=0.2, expense_ratio_pct=0.1, aum=5000, bid_ask_spread_pct=0.05),
    )
    fields = {
        item["field"]
        for dimension in scorecard["dimensions"].values()
        for item in dimension.get("formula_inputs", [])
    }
    assert scorecard["adapter"] == "etf"
    assert "roce_pct" not in fields
    assert "tracking_error_pct" in fields


def test_missing_evidence_lowers_coverage():
    complete = build_scorecard(_instrument(), _full_evidence())
    partial = build_scorecard(_instrument(), {"sector": "Industrials", "roce_pct": 20})
    assert partial["data_coverage_pct"] < complete["data_coverage_pct"]
    assert partial["missing_evidence"]


def test_screener_and_or_logic_is_deterministic():
    rows = [
        {"instrument_id": "a", "quality_score": 80, "sector": "Banking", "action_confidence": 75},
        {"instrument_id": "b", "quality_score": 50, "sector": "Technology", "action_confidence": 90},
        {"instrument_id": "c", "quality_score": 85, "sector": "Technology", "action_confidence": 40},
    ]
    definition = {
        "op": "AND",
        "conditions": [
            {"field": "quality_score", "operator": "gte", "value": 70},
            {"op": "OR", "conditions": [
                {"field": "sector", "operator": "eq", "value": "Banking"},
                {"field": "action_confidence", "operator": "gte", "value": 70},
            ]},
        ],
    }
    assert [row["instrument_id"] for row in run_screen(rows, definition)["matches"]] == ["a"]


@pytest.mark.parametrize("field", ["__import__('os').system('id')", "symbol; DROP TABLE holdings"])
def test_unsafe_free_form_expression_cannot_execute(field):
    with pytest.raises(ValueError, match="Unsafe or unsupported"):
        run_screen([], {"op": "AND", "conditions": [{"field": field, "operator": "eq", "value": 1}]})


def test_saved_screen_v1_migrates_and_revisions_remain_auditable():
    saved = research.save_screen(
        name="Quality",
        definition={"filters": [{"field": "quality_score", "operator": "gte", "value": 70}]},
    )
    assert saved["schema_version"] == research.SCHEMA_VERSION
    assert saved["definition"]["root"]["op"] == "AND"
    research.save_screen(
        screen_id=saved["screen_id"], name="Quality 80",
        definition={"root": {"op": "AND", "conditions": [{"field": "quality_score", "operator": "gte", "value": 80}]}},
        reason="raised threshold",
    )
    assert len(research.screen_revisions(saved["screen_id"])) == 2


def test_compare_explains_incompatible_metrics():
    result = compare_instruments([
        {"instrument_id": "equity", "adapter": "non_financial_equity", "metrics": {"roce_pct": 20, "momentum": 70}},
        {"instrument_id": "etf", "adapter": "etf", "metrics": {"expense_ratio_pct": 0.1, "momentum": 65}},
    ])
    assert result["comparison_status"] == "PARTIAL_WITH_EXPLANATIONS"
    assert {row["metric"] for row in result["incompatible_metrics"]} == {"expense_ratio_pct", "roce_pct"}
    assert any(row["metric"] == "momentum" for row in result["comparable_metrics"])


def test_thesis_history_is_append_only():
    base = {
        "instrument_id": "ins-thesis", "invalidation_trigger": "Revenue misses",
        "source": "annual-report", "source_as_of": "2026-03-31", "author": "reviewer",
    }
    first = research.append_thesis({**base, "thesis": "Initial sourced thesis", "decision": "WATCH"})
    second = research.append_thesis({**base, "thesis": "Updated sourced thesis", "decision": "APPROVE"})
    history = research.thesis_history("ins-thesis")
    assert [row["thesis_entry_id"] for row in history] == [first["thesis_entry_id"], second["thesis_entry_id"]]
    assert [row["thesis"] for row in history] == ["Initial sourced thesis", "Updated sourced thesis"]


def test_stale_ownership_is_visible_but_ineligible_for_action_change():
    result = assess_event(
        {
            "event_type": "OWNERSHIP_CHANGE", "source": "official-shareholding",
            "source_as_of": "2026-01-01", "verified": True, "ownership_change_pct": 2,
        },
        as_of="2026-08-29",
    )
    assert result["ownership_change_pct"] == 2
    assert result["stale"] is True
    assert result["eligible_for_action_change"] is False


def test_candidate_outside_approved_universe_is_not_recommendable():
    assert candidate_is_recommendable(None) is False
    assert candidate_is_recommendable({"research_status": "RESEARCH"}) is False
    assert candidate_is_recommendable({"research_status": "APPROVED"}) is True


def test_llm_receives_structured_redacted_context_only():
    scorecard = build_scorecard(_instrument(), _full_evidence())
    scorecard["account_id"] = "private-account"
    scorecard["user_notes"] = "private note"
    context = build_research_llm_context(scorecards=[scorecard])
    serialized = str(context)
    assert "private-account" not in serialized
    assert "private note" not in serialized
    assert context["policy"].startswith("Explain deterministic")


def test_research_ui_is_original_and_not_a_clone():
    template = Path("shared/web/templates/portfolio/research.html").read_text()
    assert "Research deeply" in template
    assert ("Tick" + "ertape") not in template
    assert "Research ≠ recommendation" in template


def test_api_v1_remains_compatible():
    client = TestClient(app)
    api = f"{APP_ROOT_PATH}/api/portfolio"
    version = client.get(f"{api}/version").json()["contract_version"]
    response = client.post(
        f"{api}/research/screens/run",
        json={
            "definition": {"op": "AND", "conditions": [{"field": "quality_score", "operator": "gte", "value": 70}]},
            "rows": [{"instrument_id": "ins-a", "quality_score": 80, "account_id": "private"}],
        },
    )
    assert response.status_code == 200
    assert "account_id" not in response.json()["matches"][0]
    assert client.get(f"{api}/version").json()["contract_version"] == version
