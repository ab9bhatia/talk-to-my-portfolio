"""Instrument-specific, transparent research scorecard adapters."""

from __future__ import annotations

from datetime import date
from typing import Any


DIMENSIONS = (
    "quality", "growth", "valuation", "momentum", "financial_risk",
    "governance_evidence", "ownership_flow", "portfolio_fit",
)


def build_scorecard(instrument: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    adapter = adapter_for(instrument, evidence)
    definitions = _definitions(adapter)
    dimensions: dict[str, Any] = {}
    total_score = 0.0
    total_weight = 0.0
    covered_weight = 0.0
    missing: list[str] = []
    for name in DIMENSIONS:
        specs = definitions.get(name) or []
        score, coverage, inputs = _score_specs(specs, evidence)
        weight = 100 / len(DIMENSIONS)
        if score is not None:
            total_score += score * weight
            total_weight += weight
        covered_weight += coverage * weight
        missing.extend(item["field"] for item in inputs if item["value"] is None)
        dimensions[name] = {
            "score": round(score, 2) if score is not None else None,
            "weight": round(weight, 2),
            "coverage_pct": round(coverage * 100, 2),
            "formula_inputs": inputs,
        }
    data_coverage = covered_weight / 100 * 100
    dimensions["data_coverage"] = {"score": round(data_coverage, 2), "weight": 0, "coverage_pct": 100, "formula_inputs": []}
    return {
        "instrument_id": instrument.get("instrument_id"),
        "symbol": instrument.get("canonical_symbol") or instrument.get("symbol"),
        "display_name": instrument.get("display_name") or instrument.get("symbol"),
        "instrument_type": instrument.get("instrument_type") or evidence.get("instrument_type"),
        "adapter": adapter,
        "total_score": round(total_score / total_weight, 2) if total_weight else None,
        "dimensions": dimensions,
        "data_coverage_pct": round(data_coverage, 2),
        "missing_evidence": sorted(set(missing)),
        "evidence_as_of": evidence.get("evidence_as_of"),
        "methodology_version": "research-scorecard-v1",
        "not_investment_action": True,
    }


def adapter_for(instrument: dict[str, Any], evidence: dict[str, Any]) -> str:
    instrument_type = str(instrument.get("instrument_type") or evidence.get("instrument_type") or "equity").lower()
    sector = str(evidence.get("sector") or "").lower()
    subtype = str(evidence.get("business_type") or "").lower()
    if instrument_type == "etf":
        return "etf"
    if instrument_type == "mutual_fund":
        return "active_mutual_fund"
    if instrument_type in {"reit", "invit"}:
        return "reit_invit"
    if instrument_type in {"gold", "crypto"}:
        return "risk_sleeve"
    if "bank" in sector or subtype == "bank":
        return "bank"
    if any(word in sector for word in ("nbfc", "hfc")) or subtype in {"nbfc", "hfc"}:
        return "nbfc_hfc"
    if "insurance" in sector or subtype == "insurer":
        return "insurer"
    if subtype == "holding_company":
        return "holding_company"
    if evidence.get("pre_profit") is True:
        return "pre_profit_growth"
    if evidence.get("is_cyclical") is True or any(word in sector for word in ("metals", "commodity", "mining")):
        return "commodity_cyclical"
    return "non_financial_equity"


def _definitions(adapter: str) -> dict[str, list[tuple[str, float, float, bool]]]:
    common = {
        "growth": [("revenue_growth_pct", -10, 30, False), ("earnings_growth_pct", -20, 35, False)],
        "valuation": [("expected_return_base_pct", 0, 25, False), ("valuation_percentile", 0, 100, True)],
        "momentum": [("momentum_score_pct", 0, 100, False), ("drawdown_pct", -50, 0, False)],
        "governance_evidence": [("governance_evidence_score", 0, 100, False)],
        "ownership_flow": [("ownership_trend_score", 0, 100, False)],
        "portfolio_fit": [("portfolio_fit_score", 0, 100, False)],
    }
    specialized: dict[str, dict[str, list[tuple[str, float, float, bool]]]] = {
        "bank": {
            "quality": [("roa_pct", 0, 2.2, False), ("net_npa_pct", 0, 5, True), ("capital_adequacy_pct", 10, 22, False)],
            "financial_risk": [("gross_npa_pct", 0, 10, True), ("capital_adequacy_pct", 10, 22, False)],
        },
        "nbfc_hfc": {
            "quality": [("roa_pct", 0, 4, False), ("net_interest_margin_pct", 1, 8, False)],
            "financial_risk": [("capital_adequacy_pct", 12, 30, False), ("gross_stage3_pct", 0, 8, True)],
        },
        "insurer": {
            "quality": [("solvency_ratio_pct", 150, 300, False), ("persistency_pct", 50, 90, False)],
            "financial_risk": [("solvency_ratio_pct", 150, 300, False)],
        },
        "etf": {
            "quality": [("tracking_error_pct", 0, 3, True), ("expense_ratio_pct", 0, 1.5, True)],
            "growth": [("aum_growth_pct", -20, 40, False)],
            "financial_risk": [("bid_ask_spread_pct", 0, 2, True), ("aum", 0, 10000, False)],
        },
        "active_mutual_fund": {
            "quality": [("downside_capture_pct", 60, 130, True), ("expense_ratio_pct", 0, 3, True)],
            "financial_risk": [("max_drawdown_pct", -60, 0, False)],
        },
        "reit_invit": {
            "quality": [("occupancy_pct", 60, 100, False), ("distribution_coverage", 0.5, 1.5, False)],
            "financial_risk": [("loan_to_value_pct", 0, 60, True)],
        },
        "risk_sleeve": {
            "quality": [("liquidity_score", 0, 100, False), ("custody_evidence_score", 0, 100, False)],
            "growth": [("momentum_score_pct", 0, 100, False)],
            "financial_risk": [("volatility_percentile", 0, 100, True), ("max_drawdown_pct", -80, 0, False)],
        },
        "holding_company": {
            "quality": [("holding_discount_pct", 0, 70, True), ("governance_evidence_score", 0, 100, False)],
            "financial_risk": [("debt_to_asset_pct", 0, 50, True)],
        },
        "pre_profit_growth": {
            "quality": [("gross_margin_pct", 0, 80, False), ("cash_runway_months", 0, 36, False)],
            "financial_risk": [("cash_runway_months", 0, 36, False), ("dilution_risk_score", 0, 100, True)],
        },
        "commodity_cyclical": {
            "quality": [("cycle_margin_percentile", 0, 100, False), ("cost_curve_percentile", 0, 100, True)],
            "financial_risk": [("net_debt_to_ebitda", 0, 5, True)],
        },
        "non_financial_equity": {
            "quality": [("roce_pct", 5, 30, False), ("free_cash_flow_margin_pct", -10, 20, False)],
            "financial_risk": [("debt_to_equity", 0, 3, True), ("interest_coverage", 0, 10, False)],
        },
    }
    return {**common, **specialized[adapter]}


def _score_specs(
    specs: list[tuple[str, float, float, bool]], evidence: dict[str, Any]
) -> tuple[float | None, float, list[dict[str, Any]]]:
    total = 0.0
    present = 0
    inputs: list[dict[str, Any]] = []
    for field, lower, upper, invert in specs:
        raw = evidence.get(field)
        score = None
        if raw is not None:
            value = float(raw)
            normalized = max(0.0, min(1.0, (value - lower) / (upper - lower)))
            score = (1 - normalized if invert else normalized) * 100
            total += score
            present += 1
        inputs.append(
            {
                "field": field,
                "value": raw,
                "formula": f"linear clip [{lower}, {upper}]" + (" inverted" if invert else ""),
                "score": round(score, 2) if score is not None else None,
                "source": evidence.get(f"{field}_source"),
                "as_of": evidence.get(f"{field}_as_of") or evidence.get("evidence_as_of"),
            }
        )
    coverage = present / len(specs) if specs else 0.0
    return (total / present if present else None), coverage, inputs
