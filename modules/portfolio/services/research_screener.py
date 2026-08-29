"""Typed screening DSL; no eval, Python expressions, or SQL generation."""

from __future__ import annotations

from typing import Any


ALLOWED_FIELDS = {
    "action", "action_confidence", "evidence_coverage", "family_weight_pct",
    "target_weight_pct", "reconciliation_state", "instrument_type", "market_cap",
    "sector", "expected_return_base_pct", "momentum_regime", "pattern_lifecycle",
    "account_eligibility", "liquidity_score", "quality_score", "growth_score",
    "valuation_score", "financial_risk_score", "evidence_age_days", "research_status",
}
ALLOWED_OPERATORS = {"eq", "ne", "gt", "gte", "lt", "lte", "in", "not_in", "contains"}


def run_screen(rows: list[dict[str, Any]], definition: dict[str, Any]) -> dict[str, Any]:
    root = definition.get("root") or definition
    _validate_group(root, depth=0)
    results = []
    eliminated = []
    for row in rows:
        matched, reasons = _evaluate(root, row)
        if matched:
            results.append(row)
        else:
            eliminated.append({"instrument_id": row.get("instrument_id"), "reasons": reasons})
    return {"matches": results, "eliminated": eliminated, "evaluated_count": len(rows)}


def _validate_group(node: dict[str, Any], *, depth: int) -> None:
    if depth > 8:
        raise ValueError("Screen nesting exceeds the safe depth limit.")
    op = str(node.get("op") or "").upper()
    if op not in {"AND", "OR"}:
        raise ValueError("Every screen group must use AND or OR.")
    conditions = node.get("conditions")
    if not isinstance(conditions, list) or not conditions or len(conditions) > 100:
        raise ValueError("A screen group requires 1-100 conditions.")
    for condition in conditions:
        if "conditions" in condition:
            _validate_group(condition, depth=depth + 1)
            continue
        field = str(condition.get("field") or "")
        operator = str(condition.get("operator") or "")
        if field not in ALLOWED_FIELDS:
            raise ValueError(f"Unsafe or unsupported screen field: {field}")
        if operator not in ALLOWED_OPERATORS:
            raise ValueError(f"Unsafe or unsupported screen operator: {operator}")


def _evaluate(node: dict[str, Any], row: dict[str, Any]) -> tuple[bool, list[str]]:
    op = str(node["op"]).upper()
    outcomes: list[tuple[bool, str]] = []
    for condition in node["conditions"]:
        if "conditions" in condition:
            matched, nested_reasons = _evaluate(condition, row)
            outcomes.append((matched, "; ".join(nested_reasons)))
        else:
            matched = _condition(condition, row)
            outcomes.append((matched, f"{condition['field']} {condition['operator']} {condition.get('value')!r}"))
    matched = all(item[0] for item in outcomes) if op == "AND" else any(item[0] for item in outcomes)
    reasons = [text for passed, text in outcomes if not passed] if op == "AND" else [text for passed, text in outcomes if passed]
    return matched, reasons


def _condition(condition: dict[str, Any], row: dict[str, Any]) -> bool:
    actual = row.get(condition["field"])
    expected = condition.get("value")
    operator = condition["operator"]
    if operator == "eq":
        return actual == expected
    if operator == "ne":
        return actual != expected
    if operator in {"gt", "gte", "lt", "lte"}:
        if actual is None or expected is None:
            return False
        left, right = float(actual), float(expected)
        return {"gt": left > right, "gte": left >= right, "lt": left < right, "lte": left <= right}[operator]
    if operator in {"in", "not_in"}:
        if not isinstance(expected, list):
            raise ValueError("in/not_in requires a list value.")
        found = actual in expected
        return found if operator == "in" else not found
    if operator == "contains":
        return str(expected).lower() in str(actual or "").lower()
    return False
