"""Deterministic after-tax scenario estimates with explicit evidence gaps."""

from __future__ import annotations

from datetime import date
from typing import Any

from modules.portfolio.services.advisory.tax_rules import rules_as_of


SCENARIOS = ("bear", "base", "bull")


def _profile(account: dict[str, Any]) -> dict[str, Any]:
    return dict(account.get("account_profile") or account)


def _evidence_for(candidate: dict[str, Any], account_type: str) -> dict[str, Any]:
    by_type = candidate.get("tax_evidence_by_account_type") or {}
    return dict(by_type.get(account_type) or candidate.get("tax_evidence") or {})


def _evidence_state(evidence: dict[str, Any], *, as_of: str) -> tuple[bool, list[str]]:
    missing = [
        key
        for key in ("source_url", "effective_from", "last_reviewed")
        if not evidence.get(key)
    ]
    if "capital_gains_rate_pct" not in evidence:
        missing.append("capital_gains_rate_pct")
    if "withholding_rate_pct" not in evidence:
        missing.append("withholding_rate_pct")
    if missing:
        return False, missing
    day = date.fromisoformat(as_of)
    starts = date.fromisoformat(str(evidence["effective_from"]))
    ends = date.fromisoformat(str(evidence["effective_to"])) if evidence.get("effective_to") else None
    if day < starts or (ends is not None and day > ends):
        return False, ["tax evidence is not effective on the scenario date"]
    return True, []


def estimate_after_tax(
    candidate: dict[str, Any], account: dict[str, Any], *, as_of: str
) -> dict[str, Any]:
    """Estimate annual scenario returns; unknown tax facts never become zeroes."""
    date.fromisoformat(as_of)
    profile = _profile(account)
    account_type = str(profile.get("account_type") or "UNKNOWN").upper()
    residency = str(profile.get("india_residency_status") or "UNKNOWN").upper()
    instrument_type = str(candidate.get("instrument_type") or "").lower()
    fund_domicile = str(candidate.get("fund_domicile") or "").upper()
    security_country = str(candidate.get("security_country") or "").upper()
    evidence = _evidence_for(candidate, account_type)
    flags: list[str] = []
    review_reasons: list[str] = []

    if account_type == "UNKNOWN" or residency == "UNKNOWN":
        review_reasons.append("account residency/type is incomplete")
    if instrument_type in {"etf", "mutual_fund", "fund"} and not fund_domicile:
        review_reasons.append("fund domicile is unknown")
    if account_type == "GIFT_IBU":
        if not (
            profile.get("gift_product_tax_verified") is True
            and profile.get("gift_product_tax_source")
            and profile.get("gift_product_tax_as_of")
            and candidate.get("exact_product_id")
            and candidate.get("share_class")
        ):
            review_reasons.append("exact GIFT product/share-class tax evidence is unverified")
    if account_type in {"US_BROKER", "GLOBAL_BROKER"} and evidence.get("treaty_verified") is not True:
        review_reasons.append("source-country/treaty evidence is unverified")
    if residency in {"NRI", "NON_RESIDENT"} and security_country == "IN":
        flags.extend(["INDIA_TAX_RELEVANT", "TDS_NOT_FINAL_LIABILITY"])
    if fund_domicile == "US" or security_country == "US":
        flags.append("US_SITUS_ESTATE_REVIEW")
        if str(profile.get("estate_tax_review_status") or "UNKNOWN").upper() != "REVIEWED":
            review_reasons.append("U.S.-situs estate review is incomplete")

    evidence_ok, evidence_gaps = _evidence_state(evidence, as_of=as_of)
    if not evidence_ok:
        review_reasons.extend(evidence_gaps)
    scenarios = candidate.get("pre_tax_return_pct") or {}
    if any(name not in scenarios for name in SCENARIOS):
        review_reasons.append("bear/base/bull pre-tax scenarios are incomplete")

    status = "AVAILABLE"
    if review_reasons:
        status = (
            "TAX_REVIEW_REQUIRED"
            if any(
                token in " ".join(review_reasons).lower()
                for token in ("gift", "treaty", "estate", "residency", "tax evidence")
            )
            else "UNKNOWN"
        )
    base_payload = {
        "account_id": account.get("account_id") or account.get("id"),
        "account_type": account_type,
        "status": status,
        "as_of": as_of,
        "review_reasons": sorted(set(review_reasons)),
        "flags": sorted(set(flags)),
        "tax_evidence": evidence,
        "rules": [item.public_reference().__dict__ for item in rules_as_of(as_of)],
        "disclaimer": "Planning estimate only; not tax filing, legal advice, or an execution instruction.",
    }
    if status != "AVAILABLE":
        return {**base_payload, "scenarios": {name: None for name in SCENARIOS}}

    capital_rate = float(evidence["capital_gains_rate_pct"]) / 100
    withholding_rate = float(evidence["withholding_rate_pct"]) / 100
    dividend_yield = float(candidate.get("dividend_yield_pct") or 0)
    fund_drag = float(evidence.get("fund_level_tax_drag_pct") or 0)
    ter = float(candidate.get("ter_pct") or 0)
    tracking = float(candidate.get("tracking_difference_pct") or 0)
    fx_cost = float(candidate.get("fx_conversion_cost_pct") or 0)
    brokerage = float(candidate.get("brokerage_settlement_cost_pct") or 0)
    exit_load = float(candidate.get("exit_load_pct") or 0)
    outputs: dict[str, Any] = {}
    for name in SCENARIOS:
        pre_tax = float(scenarios[name])
        taxable_gain = max(0.0, pre_tax - dividend_yield)
        capital_tax = taxable_gain * capital_rate
        withholding = max(0.0, dividend_yield) * withholding_rate
        total_drag = capital_tax + withholding + fund_drag + ter + tracking + fx_cost + brokerage + exit_load
        outputs[name] = {
            "pre_tax_return_pct": round(pre_tax, 4),
            "capital_gains_tax_pct": round(capital_tax, 4),
            "dividend_interest_withholding_pct": round(withholding, 4),
            "fund_level_tax_drag_pct": round(fund_drag, 4),
            "ter_tracking_drag_pct": round(ter + tracking, 4),
            "fx_conversion_cost_pct": round(fx_cost, 4),
            "brokerage_settlement_cost_pct": round(brokerage, 4),
            "exit_load_pct": round(exit_load, 4),
            "after_tax_return_pct": round(pre_tax - total_drag, 4),
        }
    return {**base_payload, "scenarios": outputs}
