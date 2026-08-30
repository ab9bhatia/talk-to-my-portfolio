"""Milestone 12 semantic signal and shared-presentation contract tests."""

from __future__ import annotations

from typing import Any

from modules.portfolio.services.advisory.service import build_advisory_payload
from modules.portfolio.services.analyst_rating import (
    build_external_analyst_view,
    compute_rating,
)
from modules.portfolio.services.today_brief import build_today_brief


def _expected_inputs(base_irr_pct: float, *, screening: bool = False) -> dict[str, Any]:
    def eps_for(irr: float) -> float:
        return round(100 * ((1 + irr / 100) ** 3) / 20, 4)

    return {
        "method": "eps",
        "source": "dated screening fixture" if screening else "official filing fixture",
        "source_type": "derived_market_model" if screening else "official_filing",
        "as_of": "2026-08-28",
        "model_quality": "screening_proxy" if screening else "documented",
        "scenarios": {
            "bear": {"eps_year3": eps_for(base_irr_pct - 8), "exit_multiple": 20},
            "base": {"eps_year3": eps_for(base_irr_pct), "exit_multiple": 20},
            "bull": {"eps_year3": eps_for(base_irr_pct + 8), "exit_multiple": 20},
        },
    }


def _holding(symbol: str, **overrides: Any) -> dict[str, Any]:
    row = {
        "instrument_id": f"NSE:{symbol}",
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


def _family(rows: list[dict[str, Any]], *, profile: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "cached_at": "2026-08-28T08:00:00Z",
        "summary": {"total_current_value": 10_000},
        "reconciliation": {"summary": {"family_value_reconciled_pct": 100}},
        "portfolios": [
            {
                "account_id": "fixture",
                "account_code": "FX",
                "broker": "custom",
                "summary": {"total_current_value": sum(row["current_value"] for row in rows)},
                "account_profile": profile or {},
                "holdings": rows,
            }
        ],
    }


def _advisory(rows: list[dict[str, Any]], *, profile: dict[str, Any] | None = None) -> dict[str, Any]:
    return build_advisory_payload(
        _family(rows, profile=profile),
        goals={"max_position_pct": 20},
        generated_at="2026-08-28T09:00:00Z",
    )


def test_authoritative_positive_model_is_ready_and_uses_shared_add_label():
    item = _advisory([_holding("READY", expected_return_inputs=_expected_inputs(30))])[
        "recommendations"
    ][0]

    assert item["action"] == "STRONG_ADD"
    assert item["decision_presentation"]["label"] == "Add more"
    assert item["decision_presentation"]["readiness"] == "READY_TO_REVIEW"
    assert item["signal_stack"]["primary"] == "PRIMARY_DECISION"
    assert item["decision_presentation"]["action_code"] == "STRONG_ADD"
    assert item["decision_presentation"]["change_instruction"].startswith("Increase from")


def test_screening_positive_model_requires_research_before_adding():
    item = _advisory(
        [_holding("SCREEN", expected_return_inputs=_expected_inputs(30, screening=True))]
    )["recommendations"][0]

    assert item["action"] in {"ADD", "STRONG_ADD"}
    assert item["decision_presentation"]["label"] == "Research before adding"
    assert item["decision_presentation"]["readiness"] == "RESEARCH_REQUIRED"


def test_target_only_context_never_creates_a_buy_or_sell_label():
    rating = compute_rating(upside_pct=42, target_price=142, last_price=100)
    view = build_external_analyst_view(target_price=142, last_price=100, target_gap_pct=42)

    assert rating["label"] is None
    assert rating["source"] == "target_only"
    assert view.consensus_label is None
    assert view.target_descriptor == "above market"
    assert view.actionable is False


def test_external_view_discloses_missing_low_coverage_stale_and_outlier_states():
    missing = build_external_analyst_view()
    low = build_external_analyst_view(
        recommendation_key="buy", analyst_count=1, as_of="2026-08-28"
    )
    stale = build_external_analyst_view(
        recommendation_key="hold", analyst_count=5, as_of="2020-01-01"
    )
    outlier = build_external_analyst_view(target_gap_pct=150, target_price=250, last_price=100)

    assert missing.status.value == "UNAVAILABLE"
    assert low.status.value == "LOW_COVERAGE"
    assert "Stale" in stale.freshness_label
    assert outlier.status.value == "OUTLIER"


def test_unresolved_identity_and_tax_review_are_explicit_readiness_gates():
    unresolved = _advisory([_holding("UNKNOWN", instrument_id=None, symbol_resolved=False)])[
        "recommendations"
    ][0]
    tax = _advisory(
        [
            _holding(
                "TAX",
                expected_return_inputs=_expected_inputs(5),
                tax_lots_available=False,
            )
        ],
        profile={"india_residency_status": "RESIDENT", "account_type": "RESIDENT_DEMAT"},
    )["recommendations"][0]

    assert unresolved["decision_presentation"]["label"] == "Fix data first"
    assert unresolved["decision_presentation"]["readiness"] == "DATA_BLOCKED"
    assert tax["action"] == "REDUCE"
    assert tax["decision_presentation"]["label"] == "Tax review first"
    assert tax["decision_presentation"]["readiness"] == "TAX_REVIEW_REQUIRED"


def test_today_brief_counts_the_same_ready_decisions_as_action_center():
    family = _family(
        [
            _holding("READY", expected_return_inputs=_expected_inputs(30)),
            _holding("SCREEN", expected_return_inputs=_expected_inputs(30, screening=True)),
            _holding("UNKNOWN", instrument_id=None, symbol_resolved=False),
        ]
    )
    advisory = build_advisory_payload(
        family,
        goals={"max_position_pct": 20},
        generated_at="2026-08-28T09:00:00Z",
    )
    brief = build_today_brief(family=family, advisory=advisory)
    ready = sum(
        item["decision_presentation"]["readiness"] == "READY_TO_REVIEW"
        for item in advisory["recommendations"]
    )

    assert brief["actions_require_review"] == ready == 1
    assert brief["research_required"] == 1
    assert brief["blocking_data_issues"] == 1
    assert advisory["schema_version"] == "advisor-v2-v1"


def test_dashboard_summary_is_cached_and_never_requests_patterns_or_llm(monkeypatch):
    from modules.portfolio.services.advisory import runtime

    runtime._DECISION_SUMMARY_CACHE.clear()
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(runtime.profile_goals, "get_goals", lambda: {"updated_at": 1})
    monkeypatch.setattr(runtime, "_decision_summary_key", lambda *_args: "stable-key")

    def fake_live(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {
            "schema_version": "advisor-v2-v1",
            "generated_at": "2026-08-28T09:00:00Z",
            "source_portfolio_cached_at": "2026-08-28T08:00:00Z",
            "recommendations": [
                {
                    "instrument_id": "NSE:READY",
                    "isin": None,
                    "symbol": "READY",
                    "action": "ADD",
                    "decision_presentation": {
                        "label": "Add gradually",
                        "readiness": "READY_TO_REVIEW",
                    },
                    "signal_stack": {},
                    "external_analyst_view": {},
                    "conflict_categories": [],
                }
            ],
        }

    monkeypatch.setattr(runtime, "build_live_advisory", fake_live)
    family = {"cached_at": "2026-08-28T08:00:00Z", "portfolios": []}

    first = runtime.build_decision_summary(family=family)
    second = runtime.build_decision_summary(family=family)

    assert first == second
    assert len(calls) == 1
    assert calls[0]["include_patterns"] is False
    assert first["patterns_evaluated"] is False
    assert first["llm_used"] is False
