"""Side-by-side research comparison with compatibility explanations."""

from __future__ import annotations

from typing import Any


TYPE_SPECIFIC = {
    "debt_to_equity": {"equity", "non_financial_equity"},
    "roce_pct": {"equity", "non_financial_equity"},
    "net_npa_pct": {"bank"},
    "tracking_error_pct": {"etf"},
    "expense_ratio_pct": {"etf", "mutual_fund", "active_mutual_fund"},
}


def compare_instruments(items: list[dict[str, Any]]) -> dict[str, Any]:
    if not 2 <= len(items) <= 5:
        raise ValueError("Compare requires between two and five instruments.")
    types = {str(item.get("adapter") or item.get("instrument_type") or "unknown") for item in items}
    all_metrics = sorted({key for item in items for key in (item.get("metrics") or {})})
    metrics: list[dict[str, Any]] = []
    incompatible: list[dict[str, Any]] = []
    for metric in all_metrics:
        allowed = TYPE_SPECIFIC.get(metric)
        if allowed and not types.issubset(allowed):
            incompatible.append(
                {
                    "metric": metric,
                    "reason": f"{metric} is not meaningful across: {', '.join(sorted(types))}.",
                }
            )
            continue
        metrics.append(
            {
                "metric": metric,
                "values": {str(item.get("instrument_id")): (item.get("metrics") or {}).get(metric) for item in items},
            }
        )
    return {
        "instruments": items,
        "comparable_metrics": metrics,
        "incompatible_metrics": incompatible,
        "comparison_status": "PARTIAL_WITH_EXPLANATIONS" if incompatible else "COMPATIBLE",
    }
