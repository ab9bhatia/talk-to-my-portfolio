"""Evaluate proposed target weights without placing or preparing broker orders."""

from __future__ import annotations

from typing import Any


BUY_ACTIONS = {"ADD", "STRONG_ADD"}
SELL_ACTIONS = {"REDUCE", "SELL"}


def evaluate_rebalance(
    advisory: dict[str, Any],
    targets: list[dict[str, Any]],
    *,
    max_position_pct: float,
    cash_buffer_pct: float,
) -> dict[str, Any]:
    recommendations = {
        str(item.get("symbol") or "").upper(): item
        for item in advisory.get("recommendations") or []
    }
    target_map: dict[str, float] = {}
    violations: list[str] = []
    for row in targets:
        symbol = str(row.get("symbol") or "").upper()
        if not symbol:
            violations.append("Every target requires a symbol.")
            continue
        if symbol in target_map:
            violations.append(f"Duplicate target for {symbol}.")
            continue
        try:
            target = float(row.get("target_weight_pct"))
        except (TypeError, ValueError):
            violations.append(f"{symbol} target weight is invalid.")
            continue
        if target < 0 or target > 100:
            violations.append(f"{symbol} target must be between 0% and 100%.")
            continue
        if target > max_position_pct:
            violations.append(
                f"{symbol} target {target:.2f}% exceeds the {max_position_pct:.2f}% position limit."
            )
        if symbol not in recommendations:
            violations.append(f"{symbol} is not in the current deterministic advisory universe.")
            continue
        target_map[symbol] = target

    portfolio_value = float(advisory.get("portfolio_value") or 0) or sum(
        float(item.get("consolidated_value") or 0) for item in recommendations.values()
    )
    proposed_invested_pct = sum(
        target_map.get(symbol, float(item.get("family_weight_pct") or 0))
        for symbol, item in recommendations.items()
    )
    investable_limit = 100 - cash_buffer_pct
    if proposed_invested_pct > investable_limit + 0.01:
        violations.append(
            f"Proposed invested weight {proposed_invested_pct:.2f}% exceeds the "
            f"{investable_limit:.2f}% limit after the cash buffer."
        )

    changes: list[dict[str, Any]] = []
    for symbol, target in target_map.items():
        item = recommendations[symbol]
        current = float(item.get("family_weight_pct") or 0)
        delta_pct = target - current
        if abs(delta_pct) < 0.01:
            direction = "HOLD"
        elif delta_pct > 0:
            direction = "BUY"
            if item.get("action") not in BUY_ACTIONS:
                violations.append(
                    f"{symbol} buy conflicts with deterministic action {item.get('action')}."
                )
        else:
            direction = "SELL"
            if item.get("action") not in SELL_ACTIONS:
                violations.append(
                    f"{symbol} sale conflicts with deterministic action {item.get('action')}."
                )

        delta_value = portfolio_value * delta_pct / 100
        account_allocations: list[dict[str, Any]] = []
        current_value = float(item.get("consolidated_value") or 0)
        if direction == "SELL" and current_value > 0:
            sale_value = abs(delta_value)
            for account in item.get("accounts") or []:
                share = float(account.get("current_value") or 0) / current_value
                account_allocations.append(
                    {
                        "account_code": account.get("account_code"),
                        "sell_value": round(sale_value * share, 2),
                        "settlement_note": item.get("settlement_note"),
                    }
                )
        changes.append(
            {
                "symbol": symbol,
                "direction": direction,
                "deterministic_action": item.get("action"),
                "sell_type": item.get("sell_type"),
                "current_weight_pct": round(current, 2),
                "target_weight_pct": round(target, 2),
                "delta_weight_pct": round(delta_pct, 2),
                "estimated_delta_value": round(delta_value, 2),
                "account_allocations": account_allocations,
            }
        )

    return {
        "schema_version": "advisor-rebalance-evaluation-v1",
        "accepted": not violations,
        "execution_enabled": False,
        "portfolio_value": round(portfolio_value, 2),
        "cash_buffer_pct": cash_buffer_pct,
        "proposed_invested_pct": round(proposed_invested_pct, 2),
        "changes": changes,
        "violations": list(dict.fromkeys(violations)),
        "audit": [
            "Proposal evaluated against the current deterministic recommendations.",
            "No order was created, staged, or sent to a broker.",
        ],
    }
