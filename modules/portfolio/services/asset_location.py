"""Eligibility and contribution-first asset-location planner."""

from __future__ import annotations

from typing import Any

from modules.portfolio.services.after_tax import estimate_after_tax


DEFAULT_ELIGIBILITY = {
    "RESIDENT_DEMAT": {"equity", "etf", "mutual_fund", "bond", "cash"},
    "NRO_NON_PIS": {"equity", "etf", "mutual_fund", "bond", "cash"},
    "NRE_PIS": {"equity", "etf", "cash"},
    "GIFT_IBU": {"equity", "etf", "mutual_fund", "fund", "bond", "cash"},
    "US_BROKER": {"equity", "etf", "fund", "bond", "cash"},
    "GLOBAL_BROKER": {"equity", "etf", "mutual_fund", "fund", "bond", "cash"},
}


def check_account_eligibility(account: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    profile = dict(account.get("account_profile") or account)
    account_type = str(profile.get("account_type") or "UNKNOWN").upper()
    instrument_type = str(candidate.get("instrument_type") or "").lower()
    permitted = set(profile.get("permitted_instrument_types") or DEFAULT_ELIGIBILITY.get(account_type, set()))
    reasons: list[str] = []
    review = False
    if account_type == "UNKNOWN":
        reasons.append("account type is unknown")
        review = True
    elif instrument_type not in permitted:
        reasons.append(f"{instrument_type or 'instrument'} is not permitted for {account_type}")
    if account_type == "GIFT_IBU" and not (
        profile.get("gift_product_tax_verified") is True
        and candidate.get("exact_product_id")
        and candidate.get("share_class")
    ):
        reasons.append("exact GIFT product/share class requires verification")
        review = True
    if candidate.get("requires_repatriable_account") and str(profile.get("repatriability") or "UNKNOWN") != "REPATRIABLE":
        reasons.append("repatriability requirement is not met")
    return {
        "account_id": account.get("account_id") or account.get("id"),
        "account_type": account_type,
        "eligible": not reasons,
        "review_required": review,
        "reasons": reasons,
    }


def optimize_asset_location(
    candidate: dict[str, Any], accounts: list[dict[str, Any]], *, as_of: str
) -> dict[str, Any]:
    """Rank eligible accounts without proposing artificial family transfers."""
    comparisons = []
    for account in accounts:
        eligibility = check_account_eligibility(account, candidate)
        estimate = estimate_after_tax(candidate, account, as_of=as_of)
        base = estimate.get("scenarios", {}).get("base")
        comparisons.append(
            {
                "account_id": eligibility["account_id"],
                "owner_ref": (account.get("account_profile") or account).get("owner_ref"),
                "eligibility": eligibility,
                "after_tax": estimate,
                "after_tax_base_pct": base.get("after_tax_return_pct") if base else None,
            }
        )
    eligible = [
        row
        for row in comparisons
        if row["eligibility"]["eligible"] and row["after_tax_base_pct"] is not None
    ]
    eligible.sort(key=lambda row: row["after_tax_base_pct"], reverse=True)
    current_account = candidate.get("current_account_id")
    current = next((row for row in comparisons if row["account_id"] == current_account), None)
    best = eligible[0] if eligible else None
    action = "MIGRATE_AFTER_TAX_REVIEW"
    reason = "No account has complete eligibility and current tax evidence."
    selected = best["account_id"] if best else None
    if best and best["account_id"] == current_account:
        action = "KEEP_CURRENT_LOCATION"
        reason = "Current account has the highest evidenced after-tax base outcome."
    elif best:
        current_base = current.get("after_tax_base_pct") if current else None
        annual_benefit = (best["after_tax_base_pct"] - current_base) if current_base is not None else None
        move_cost = float(candidate.get("transfer_tax_cost_pct") or 0) + float(candidate.get("exit_load_pct") or 0)
        current_owner = current.get("owner_ref") if current else candidate.get("current_owner_ref")
        if current_owner and best.get("owner_ref") and current_owner != best["owner_ref"]:
            action = "USE_NEW_CONTRIBUTIONS_ELSEWHERE"
            reason = "Ownership differs; no internal family transfer is assumed tax-free or permissible."
        elif annual_benefit is not None and move_cost >= annual_benefit:
            action = "DO_NOT_MOVE_COST_EXCEEDS_BENEFIT"
            reason = "Estimated transfer/exit cost is at least the one-year after-tax benefit."
        else:
            action = "USE_NEW_CONTRIBUTIONS_ELSEWHERE"
            reason = "Direct new cash to the better evidenced account; do not synthesize a transfer."
    return {
        "instrument_id": candidate.get("instrument_id"),
        "symbol": candidate.get("symbol"),
        "recommended_action": action,
        "selected_account_id": selected,
        "reason": reason,
        "comparisons": comparisons,
        "binding_constraints": sorted(
            {
                reason
                for row in comparisons
                for reason in row["eligibility"].get("reasons") or []
            }
        ),
        "execution_enabled": False,
        "transfer_assumed": False,
    }
