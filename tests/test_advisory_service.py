"""Deterministic Advisor V2 tests; no live broker, market, tax, or LLM calls."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from main import app
from modules.portfolio.services.advisory.service import build_advisory_payload
from modules.portfolio.services.portfolio_agent import _malformed_json_fallback
from shared.config import APP_ROOT_PATH


def _expected_inputs(base_irr_pct: float) -> dict[str, Any]:
    def eps_for(irr: float) -> float:
        return round(100 * ((1 + irr / 100) ** 3) / 20, 4)

    return {
        "method": "eps",
        "source": "synthetic official filing fixture",
        "source_type": "official_filing",
        "as_of": "2026-06-30",
        "scenarios": {
            "bear": {"eps_year3": eps_for(base_irr_pct - 8), "exit_multiple": 20},
            "base": {"eps_year3": eps_for(base_irr_pct), "exit_multiple": 20},
            "bull": {"eps_year3": eps_for(base_irr_pct + 8), "exit_multiple": 20},
        },
    }


def _holding(symbol: str = "GOOD", **overrides: Any) -> dict[str, Any]:
    quantity = float(overrides.pop("quantity", 1))
    price = float(overrides.pop("last_price", 100))
    invested = float(overrides.pop("invested", quantity * price))
    row = {
        "symbol": symbol,
        "exchange": "NSE",
        "quantity": quantity,
        "last_price": price,
        "current_value": quantity * price,
        "invested": invested,
        "pnl": quantity * price - invested,
        "roce": 22,
        "debt_to_equity": 0.4,
        "free_cash_flow_positive": True,
        "revenue_growth_pct": 18,
        "earnings_growth_pct": 20,
        "earnings_revision_pct": 4,
        "moat_score": 7,
        "governance_risk": "none",
        "business_thesis": "Synthetic sourced business thesis for deterministic tests.",
        "return_1m_pct": 2,
        "return_3m_pct": 6,
        "return_6m_pct": 12,
        "return_12m_pct": 20,
        "relative_strength_6m_pct": 3,
        "pct_vs_dma50": 4,
        "pct_vs_dma200": 8,
        "pct_from_52w_high": -6,
        "momentum_as_of": "2026-08-27",
        **overrides,
    }
    return row


def _family(
    holdings: list[dict[str, Any]],
    *,
    total_value: float = 1_000,
    profile: dict[str, Any] | None = None,
    turnover: float = 0,
    second_account: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    for row in holdings:
        row.setdefault("account_id", "a1")
        row.setdefault("account_code", "A1")
        row.setdefault("broker", "zerodha")
    block = {
        "account_id": "a1",
        "account_code": "A1",
        "broker": "zerodha",
        "summary": {"total_current_value": sum(h["current_value"] for h in holdings)},
        "holdings": holdings,
        "account_profile": profile or {},
    }
    portfolios = [block]
    if second_account:
        for row in second_account:
            row.setdefault("account_id", "a2")
            row.setdefault("account_code", "A2")
            row.setdefault("broker", "groww")
        portfolios.append(
            {
                "account_id": "a2",
                "account_code": "A2",
                "broker": "groww",
                "summary": {
                    "total_current_value": sum(h["current_value"] for h in second_account)
                },
                "holdings": second_account,
                "account_profile": profile or {},
            }
        )
    return {
        "cached_at": "2026-08-28T08:00:00Z",
        "summary": {"total_current_value": total_value},
        "portfolios": portfolios,
        "recent_turnover_pct": turnover,
    }


def _recommendation(payload: dict[str, Any], symbol: str = "GOOD") -> dict[str, Any]:
    return next(item for item in payload["recommendations"] if item["symbol"] == symbol)


def _payload(
    holdings: list[dict[str, Any]],
    *,
    total_value: float = 1_000,
    profile: dict[str, Any] | None = None,
    turnover: float = 0,
    second_account: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return build_advisory_payload(
        _family(
            holdings,
            total_value=total_value,
            profile=profile,
            turnover=turnover,
            second_account=second_account,
        ),
        goals={"max_position_pct": 20},
        generated_at="2026-08-28T09:00:00Z",
    )


def test_missing_inputs_returns_watch_and_does_not_infer_xirr():
    payload = _payload([_holding()])
    rec = _recommendation(payload)
    assert rec["action"] == "WATCH"
    assert rec["expected_3y_irr"]["base_pct"] is None
    assert rec["action_confidence"] <= 45
    assert payload["xirr_status"] == "unavailable_without_cashflows"


def test_cyclical_with_strong_momentum_is_not_fundamental_sell():
    row = _holding(
        "CYCLE",
        is_cyclical=True,
        expected_return_inputs=_expected_inputs(5),
        return_1m_pct=12,
        return_3m_pct=25,
        return_6m_pct=40,
        return_12m_pct=55,
        relative_strength_6m_pct=18,
        pct_vs_dma50=15,
        pct_vs_dma200=25,
        pct_from_52w_high=-2,
    )
    rec = _recommendation(_payload([row]), "CYCLE")
    assert rec["action"] == "REDUCE"
    assert rec["sell_type"] == "TACTICAL_REDUCE"
    assert "staged tactical reduction" in rec["why_now"]


def test_winner_with_improving_earnings_is_not_sold_for_gain():
    row = _holding(
        "WINNER",
        invested=20,
        expected_return_inputs=_expected_inputs(22),
        earnings_growth_pct=28,
    )
    rec = _recommendation(_payload([row]), "WINNER")
    assert rec["action"] == "ADD"
    assert rec["sell_type"] == "NONE"


def test_quality_nri_position_in_loss_has_no_forced_tax_harvest():
    row = _holding(
        "NRILOSS",
        invested=180,
        expected_return_inputs=_expected_inputs(15),
    )
    rec = _recommendation(
        _payload(
            [row],
            profile={"india_residency_status": "NRI", "account_type": "NRO_NON_PIS"},
        ),
        "NRILOSS",
    )
    assert rec["action"] == "HOLD_NO_ADD"
    assert "Indian-tax relevant" in rec["tax_note"]
    assert rec["sell_type"] == "NONE"


def test_weak_resident_loss_is_tax_harvest_review_not_automatic_claim():
    row = _holding(
        "WEAKLOSS",
        invested=180,
        expected_return_inputs=_expected_inputs(5),
        roce=3,
        debt_to_equity=3,
        free_cash_flow_positive=False,
        earnings_growth_pct=-20,
        tax_lots_available=False,
    )
    rec = _recommendation(
        _payload([row], profile={"india_residency_status": "RESIDENT"}),
        "WEAKLOSS",
    )
    assert rec["action"] == "REDUCE"
    assert "may be reviewed for harvesting" in rec["tax_note"]
    assert "MISSING_TAX_LOTS" in {flag["code"] for flag in rec["data_quality_flags"]}


def test_subscale_good_company_builds_to_meaningful_weight():
    row = _holding("TINYGOOD", expected_return_inputs=_expected_inputs(30))
    rec = _recommendation(_payload([row], total_value=50_000), "TINYGOOD")
    assert rec["family_weight_pct"] == 0.2
    assert rec["action"] == "STRONG_ADD"
    assert rec["target_weight_pct"] >= 1


def test_sourced_broken_governance_is_fundamental_exit():
    row = _holding(
        "BROKEN",
        governance_risk="high",
        governance_event="Exchange filing reports a material governance breach.",
        governance_event_source="NSE filing fixture",
        governance_event_as_of="2026-08-01",
        governance_event_source_type="exchange",
    )
    rec = _recommendation(_payload([row]), "BROKEN")
    assert rec["action"] == "SELL"
    assert rec["sell_type"] == "FUNDAMENTAL_SELL"
    assert rec["sell_pct"] == 100
    assert rec["action_confidence"] >= 85


def test_corporate_action_cost_basis_returns_reconcile():
    row = _holding("DEMERGED", cost_basis_unreconciled=True)
    rec = _recommendation(_payload([row]), "DEMERGED")
    assert rec["action"] == "RECONCILE"
    assert rec["sell_type"] == "NONE"
    assert rec["sell_pct"] == 0


def test_suspended_security_does_not_emit_fake_market_sell():
    row = _holding("SUSPEND", is_suspended=True, is_tradable=False)
    rec = _recommendation(_payload([row]), "SUSPEND")
    assert rec["action"] == "WATCH"
    assert rec["sell_pct"] == 0
    assert "No market sale is assumed" in rec["settlement_note"]


def test_explicit_etf_overlap_is_reported_without_fake_lookthrough():
    first = _holding(
        "ETFONE",
        asset_class="etf",
        underlying_index="Nifty Momentum 30",
        expected_return_inputs=_expected_inputs(14),
    )
    second = _holding(
        "ETFTWO",
        asset_class="etf",
        underlying_index="Nifty Momentum 30",
        expected_return_inputs=_expected_inputs(14),
    )
    payload = _payload([first, second])
    assert payload["overlap_report"][0]["symbols"] == ["ETFONE", "ETFTWO"]
    for symbol in ("ETFONE", "ETFTWO"):
        codes = {flag["code"] for flag in _recommendation(payload, symbol)["data_quality_flags"]}
        assert "REDUNDANT_FUND_SLEEVE" in codes
        assert "LOOKTHROUGH_UNAVAILABLE" in codes


def test_gift_product_never_claims_zero_tax_without_product_evidence():
    row = _holding("GIFTETF", asset_class="etf", expected_return_inputs=_expected_inputs(15))
    rec = _recommendation(
        _payload([row], profile={"account_type": "GIFT_IBU"}),
        "GIFTETF",
    )
    assert rec["tax_note"].startswith("TAX_REVIEW_REQUIRED")
    assert "TAX_REVIEW_REQUIRED" in {flag["code"] for flag in rec["data_quality_flags"]}
    assert "is zero tax" not in rec["tax_note"].lower()


def test_recent_turnover_cooldown_suppresses_optional_rotation():
    row = _holding("COOLDOWN", expected_return_inputs=_expected_inputs(5))
    payload = _payload([row], turnover=20)
    rec = _recommendation(payload, "COOLDOWN")
    assert rec["action"] == "HOLD_NO_ADD"
    assert rec["sell_type"] == "NONE"
    assert payload["cooldown_warning"]


def test_same_isin_consolidates_accounts_and_exact_proceeds():
    first = _holding(
        "DUAL",
        isin="INE000000001",
        expected_return_inputs=_expected_inputs(5),
    )
    second = _holding(
        "DUALALT",
        isin="INE000000001",
        quantity=2,
        expected_return_inputs=_expected_inputs(5),
    )
    payload = _payload([first], second_account=[second])
    rec = payload["recommendations"][0]
    assert rec["consolidated_qty"] == 3
    assert {account["account_code"] for account in rec["accounts"]} == {"A1", "A2"}
    assert payload["proceeds_by_account"] == {"A1": 50.0, "A2": 100.0}


def test_any_account_cost_basis_mismatch_forces_consolidated_reconciliation():
    first = _holding("SAME", isin="INE000000002", cost_basis_unreconciled=False)
    second = _holding("SAMEALT", isin="INE000000002", cost_basis_unreconciled=True)
    rec = _payload([first], second_account=[second])["recommendations"][0]
    assert rec["action"] == "RECONCILE"
    assert "CORPORATE_ACTION_RECONCILIATION" in {
        flag["code"] for flag in rec["data_quality_flags"]
    }


def test_imported_screenshot_quality_flags_are_preserved():
    row = _holding(
        "SCREEN",
        data_quality_flags=["SCREENSHOT_COST_BASIS_UNAVAILABLE"],
    )
    rec = _recommendation(_payload([row]), "SCREEN")
    assert "SCREENSHOT_COST_BASIS_UNAVAILABLE" in {
        flag["code"] for flag in rec["data_quality_flags"]
    }


def test_api_v1_contract_version_is_unchanged():
    client = TestClient(app)
    response = client.get(f"{APP_ROOT_PATH}/api/portfolio/version")
    assert response.status_code == 200
    assert response.json()["contract_version"] == "2026-05-mobile-mvp-v1"


def test_malformed_llm_json_keeps_deterministic_output_usable():
    advisory = _payload([_holding()])
    fallback = _malformed_json_fallback("not-json", context={"advisory": advisory})
    assert fallback["answer"] == "not-json"
    assert fallback["deterministic_advisory"]["schema_version"] == "advisor-v2-milestone-2"
    assert fallback["deterministic_advisory"]["recommendations"][0]["action"] == "WATCH"
