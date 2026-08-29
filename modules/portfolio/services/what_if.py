"""Read-only, constraint-aware portfolio simulation."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def simulate_rebalance(
    holdings: list[dict[str, Any]],
    *,
    operations: list[dict[str, Any]],
    constraints: dict[str, Any],
    approved_candidates: set[str] | None = None,
) -> dict[str, Any]:
    source = deepcopy(holdings)
    rows = deepcopy(holdings)
    approved = approved_candidates or set()
    total = sum(float(row.get("current_value") or 0) for row in rows)
    for row in rows:
        row["target_value"] = float(row.get("current_value") or 0)
        row["binding_constraints"] = []
        row["proposal_reasons"] = []

    for operation in operations:
        op = operation.get("type")
        if op == "sell_below_weight_pct":
            threshold = float(operation.get("threshold_pct") or 0)
            for row in rows:
                weight = float(row["target_value"]) / total * 100 if total else 0
                if 0 < weight < threshold:
                    if row.get("tax_lot_block") or row.get("corporate_action_pending"):
                        row["binding_constraints"].append("TAX_OR_CA_REVIEW_BLOCK")
                    else:
                        row["target_value"] = 0.0
                        row["proposal_reasons"].append(f"Position below {threshold}% meaningful-size floor")
        elif op == "set_position_weight":
            instrument_id = str(operation.get("instrument_id") or "")
            target_pct = float(operation.get("target_weight_pct") or 0)
            row = next((item for item in rows if item.get("instrument_id") == instrument_id), None)
            if row:
                row["target_value"] = total * target_pct / 100
                row["proposal_reasons"].append("User what-if target")
        elif op == "add_candidate":
            instrument_id = str(operation.get("instrument_id") or "")
            if instrument_id not in approved:
                continue
            account_code = str(operation.get("account_code") or "")
            eligible = set(operation.get("eligible_account_codes") or [])
            if eligible and account_code not in eligible:
                continue
            rows.append(
                {
                    "instrument_id": instrument_id,
                    "symbol": operation.get("symbol") or instrument_id,
                    "sector": operation.get("sector") or "Unknown",
                    "account_code": account_code,
                    "current_value": 0.0,
                    "target_value": float(operation.get("amount") or 0),
                    "binding_constraints": [],
                    "proposal_reasons": ["Explicit approved-candidate simulation"],
                }
            )

    _apply_caps(rows, total=total, constraints=constraints)
    turnover = sum(abs(float(row["target_value"]) - float(row.get("current_value") or 0)) for row in rows) / (2 * total) * 100 if total else 0
    budget = float(constraints.get("turnover_budget_pct") or 100)
    if turnover > budget and turnover:
        factor = budget / turnover
        for row in rows:
            current = float(row.get("current_value") or 0)
            row["target_value"] = current + (float(row["target_value"]) - current) * factor
            row["binding_constraints"].append("TURNOVER_BUDGET")
        turnover = budget

    cash_buffer_pct = float(constraints.get("cash_buffer_pct") or 0)
    investable = total * (1 - cash_buffer_pct / 100)
    proposed_total = sum(float(row["target_value"]) for row in rows)
    if proposed_total > investable and proposed_total:
        scale = investable / proposed_total
        for row in rows:
            row["target_value"] *= scale
            row["binding_constraints"].append("CASH_BUFFER")

    proposals = []
    for row in rows:
        current = float(row.get("current_value") or 0)
        target = float(row["target_value"])
        change = target - current
        if abs(change) < 0.01 and not row["binding_constraints"]:
            continue
        blocked = "TAX_OR_CA_REVIEW_BLOCK" in row["binding_constraints"]
        proposals.append(
            {
                "instrument_id": row.get("instrument_id"),
                "symbol": row.get("symbol"),
                "account_code": row.get("account_code"),
                "current_value": round(current, 2),
                "target_value": round(target, 2),
                "change": round(change, 2),
                "execution_ready": not blocked and False,
                "requires_review": blocked or bool(row["binding_constraints"]),
                "binding_constraints": sorted(set(row["binding_constraints"])),
                "reasons": row["proposal_reasons"],
            }
        )
    return {
        "before": _summary(source, total),
        "after": _summary(rows, total, target=True),
        "proposals": proposals,
        "turnover_pct": round(turnover, 2),
        "tax_ca_review_flags": sum(item["requires_review"] for item in proposals),
        "execution_enabled": False,
        "source_portfolio_unchanged": holdings == source,
        "limitations": ["After-tax values appear only when verified tax-lot/rule inputs are supplied."],
    }


def _apply_caps(rows: list[dict[str, Any]], *, total: float, constraints: dict[str, Any]) -> None:
    max_position = float(constraints.get("max_position_pct") or 100)
    for row in rows:
        cap = total * max_position / 100
        if float(row["target_value"]) > cap:
            row["target_value"] = cap
            row["binding_constraints"].append("MAX_POSITION")
    sector_cap = float(constraints.get("sector_cap_pct") or 100)
    sectors = {str(row.get("sector") or "Unknown") for row in rows}
    for sector in sectors:
        members = [row for row in rows if str(row.get("sector") or "Unknown") == sector]
        sector_value = sum(float(row["target_value"]) for row in members)
        cap = total * sector_cap / 100
        if sector_value > cap and sector_value:
            factor = cap / sector_value
            for row in members:
                row["target_value"] *= factor
                row["binding_constraints"].append("SECTOR_CAP")
    _cap_group(
        rows,
        field="promoter_group",
        cap_pct=float(constraints.get("promoter_group_cap_pct") or 100),
        total=total,
        constraint="PROMOTER_GROUP_CAP",
    )
    small_cap = [row for row in rows if str(row.get("market_cap") or "").upper() == "SMALL"]
    small_value = sum(float(row["target_value"]) for row in small_cap)
    small_cap_limit = total * float(constraints.get("small_cap_cap_pct") or 100) / 100
    if small_value > small_cap_limit and small_value:
        factor = small_cap_limit / small_value
        for row in small_cap:
            row["target_value"] *= factor
            row["binding_constraints"].append("SMALL_CAP_CAP")


def _cap_group(
    rows: list[dict[str, Any]], *, field: str, cap_pct: float, total: float, constraint: str
) -> None:
    groups = {str(row.get(field) or "") for row in rows if row.get(field)}
    for group in groups:
        members = [row for row in rows if str(row.get(field) or "") == group]
        value = sum(float(row["target_value"]) for row in members)
        cap = total * cap_pct / 100
        if value > cap and value:
            factor = cap / value
            for row in members:
                row["target_value"] *= factor
                row["binding_constraints"].append(constraint)


def _summary(rows: list[dict[str, Any]], total: float, *, target: bool = False) -> dict[str, Any]:
    key = "target_value" if target else "current_value"
    values = [float(row.get(key) or 0) for row in rows if float(row.get(key) or 0) > 0]
    sectors: dict[str, float] = {}
    for row in rows:
        sector = str(row.get("sector") or "Unknown")
        sectors[sector] = sectors.get(sector, 0) + float(row.get(key) or 0)
    portfolio_value = sum(values)

    def weighted(field: str) -> float | None:
        covered = [
            row
            for row in rows
            if row.get(field) is not None and float(row.get(key) or 0) > 0
        ]
        covered_value = sum(float(row.get(key) or 0) for row in covered)
        if not covered_value:
            return None
        return round(
            sum(float(row[field]) * float(row.get(key) or 0) for row in covered)
            / covered_value,
            2,
        )

    return {
        "portfolio_value": round(portfolio_value, 2),
        "holdings_count": len(values),
        "largest_position_pct": round(max(values) / total * 100, 2) if values and total else 0,
        "sector_weights": {sector: round(value / total * 100, 2) if total else 0 for sector, value in sectors.items()},
        "expected_return_scenarios": {
            "bear_pct": weighted("expected_return_bear_pct"),
            "base_pct": weighted("expected_return_base_pct"),
            "bull_pct": weighted("expected_return_bull_pct"),
        },
        "stress_drawdown_pct": weighted("stress_drawdown_pct"),
        "weighted_ter_pct": weighted("ter_pct"),
        "overlap_score": weighted("overlap_score"),
        "liquidity_warnings": sum(
            bool(row.get("liquidity_warning"))
            for row in rows
            if float(row.get(key) or 0) > 0
        ),
        "cash_buffer_pct": (
            round(max(0.0, total - portfolio_value) / total * 100, 2) if total else 0
        ),
        "after_tax_estimate": weighted("verified_after_tax_return_pct"),
    }
