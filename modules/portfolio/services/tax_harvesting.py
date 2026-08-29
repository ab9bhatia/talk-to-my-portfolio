"""Conservative FIFO tax-loss planning, separate from investment decisions."""

from __future__ import annotations

from datetime import date
from typing import Any


def evaluate_harvest(
    holding: dict[str, Any], account: dict[str, Any], *, lots: list[dict[str, Any]], as_of: str
) -> dict[str, Any]:
    profile = dict(account.get("account_profile") or account)
    residency = str(profile.get("india_residency_status") or "UNKNOWN").upper()
    result = {
        "symbol": holding.get("symbol"),
        "status": "REVIEW_ONLY",
        "action": "DO_NOT_HARVEST",
        "reason": "No independently supported sale case.",
        "tds_is_final_liability": False,
        "execution_enabled": False,
        "lots": [],
        "uncertainties": ["Re-entry and same-security policy requires current CA review."],
    }
    if residency in {"NRI", "NON_RESIDENT"}:
        result["reason"] = "NRI withholding, final liability, settlement, and repatriation require separate review."
        result["uncertainties"].append("Broker TDS is not final liability.")
        return result
    if residency != "RESIDENT":
        result["status"] = "TAX_REVIEW_REQUIRED"
        result["reason"] = "Residency is incomplete."
        return result
    if not lots or not profile.get("tax_lots_available"):
        result["status"] = "TAX_REVIEW_REQUIRED"
        result["reason"] = "Resident loss planning requires complete FIFO lots."
        return result
    if not holding.get("independent_sell_case"):
        return result
    sale_price = float(holding.get("last_price") or holding.get("ltp") or 0)
    loss_lots = []
    for lot in sorted(lots, key=lambda row: (row.get("acquisition_date") or "", row.get("lot_id") or "")):
        quantity = float(lot.get("remaining_quantity") or lot.get("quantity") or 0)
        cost = float(lot.get("remaining_cost_basis") or lot.get("cost_basis") or 0)
        proceeds = quantity * sale_price
        if quantity <= 0 or proceeds >= cost or not lot.get("acquisition_date"):
            continue
        held_days = (date.fromisoformat(as_of) - date.fromisoformat(lot["acquisition_date"])).days
        loss_lots.append(
            {
                "lot_id": lot.get("lot_id"),
                "quantity": quantity,
                "estimated_loss": round(cost - proceeds, 2),
                "classification": "LTCL" if held_days > 365 else "STCL",
            }
        )
    gross_benefit = sum(row["estimated_loss"] for row in loss_lots) * float(holding.get("evidenced_tax_rate_pct") or 0) / 100
    costs = float(holding.get("exit_load_amount") or 0) + float(holding.get("transaction_cost_amount") or 0)
    if not loss_lots:
        result["reason"] = "FIFO lots do not show a harvestable loss."
    elif costs >= gross_benefit:
        result["reason"] = "Exit load and transaction costs outweigh the evidenced tax benefit."
    elif not holding.get("tax_rule_evidence_current"):
        result["status"] = "TAX_REVIEW_REQUIRED"
        result["reason"] = "STCL/LTCL treatment lacks current effective rule evidence."
    else:
        result["action"] = "REVIEW_HARVEST_WITH_CA"
        result["reason"] = "Independent sell case and FIFO loss lots exist; confirm treatment and re-entry with a CA."
    result["lots"] = loss_lots
    result["estimated_gross_tax_benefit"] = round(gross_benefit, 2)
    result["estimated_exit_and_transaction_cost"] = round(costs, 2)
    return result
