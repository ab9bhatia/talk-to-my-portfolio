"""Reconcile duplicate family holdings to one defensible market quote."""

from __future__ import annotations

import copy
from collections import defaultdict
from datetime import UTC, datetime
from statistics import median
from typing import Any


_CLUSTER_TOLERANCE_PCT = 0.75
_INDIAN_EXCHANGES = frozenset({"NSE", "BSE"})


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_iso(value: Any) -> str | None:
    if value is None:
        return None
    try:
        epoch = float(value)
    except (TypeError, ValueError):
        return str(value)
    if epoch <= 0:
        return str(value)
    return datetime.fromtimestamp(epoch, tz=UTC).isoformat().replace("+00:00", "Z")


def _security_key(row: dict[str, Any]) -> str:
    symbol = str(row.get("symbol") or "").strip().upper()
    exchange = str(row.get("exchange") or "NSE").strip().upper()
    exchange_key = "IN" if exchange in _INDIAN_EXCHANGES else exchange
    return f"{exchange_key}:{symbol}"


def _is_cost_basis_fallback(row: dict[str, Any], price: float) -> bool:
    source = str(row.get("broker_price_source") or "").lower()
    if source == "cost_basis_fallback" or row.get("market_price_unavailable"):
        return True
    avg = _number(row.get("avg_price"))
    return bool(
        str(row.get("broker") or "").lower() == "groww"
        and str(row.get("asset_class") or "equity").lower() != "mf"
        and not source
        and avg is not None
        and abs(price - avg) <= 0.01
    )


def _candidate(row: dict[str, Any]) -> dict[str, Any] | None:
    explicit = row.get("market_price")
    price = _number(explicit if explicit is not None else row.get("last_price"))
    if price is None or price <= 0 or _is_cost_basis_fallback(row, price):
        return None
    return {
        "price": price,
        "account": str(row.get("account_code") or row.get("account_id") or "UNKNOWN"),
    }


def _winning_cluster(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    clusters: list[list[dict[str, Any]]] = []
    for candidate in sorted(candidates, key=lambda item: item["price"]):
        target = None
        for cluster in clusters:
            center = median(item["price"] for item in cluster)
            if abs(candidate["price"] - center) / center * 100 <= _CLUSTER_TOLERANCE_PCT:
                target = cluster
                break
        if target is None:
            clusters.append([candidate])
        else:
            target.append(candidate)
    return max(clusters, key=lambda cluster: len({item["account"] for item in cluster}))


def _apply_mark(row: dict[str, Any], price: float, *, accounts: list[str], as_of: str | None) -> None:
    quantity = _number(row.get("quantity")) or 0.0
    invested = _number(row.get("invested")) or 0.0
    original_price = _number(row.get("last_price"))
    original_value = _number(row.get("current_value"))
    original_pnl = _number(row.get("pnl"))
    cost_basis_only = _is_cost_basis_fallback(row, original_price or 0.0)
    if cost_basis_only:
        row["cost_basis_price"] = _number(row.get("avg_price"))
        row["cost_basis_value"] = invested
        row["broker_reported_price"] = None
        row["broker_reported_value"] = None
        row["broker_reported_pnl"] = None
        row["broker_value_source"] = "unavailable"
        row["broker_price_unavailable"] = True
        row["broker_value_unavailable"] = True
    else:
        row.setdefault("broker_reported_price", original_price)
        row.setdefault("broker_reported_value", original_value)
        row.setdefault("broker_reported_pnl", original_pnl)
    row["market_price"] = round(price, 4)
    row["market_price_source"] = "family_quote_consensus"
    row["market_price_as_of"] = as_of
    row["market_price_unavailable"] = False
    row["price_consensus_accounts"] = accounts
    row["last_price"] = round(price, 4)
    row["current_value"] = round(quantity * price, 2)
    row["pnl"] = round(row["current_value"] - invested, 2)
    row["pnl_pct"] = round((row["pnl"] / invested * 100) if invested else 0.0, 2)
    high_52w = _number(row.get("high_52w"))
    target = _number(row.get("target_price"))
    if high_52w and high_52w > 0:
        row["pct_from_52w_high"] = round((price - high_52w) / high_52w * 100, 2)
    if target and price > 0:
        row["upside_pct"] = round((target - price) / price * 100, 2)


def apply_family_quote_consensus(payload: dict[str, Any]) -> dict[str, Any]:
    """Correct false broker LTP fallbacks while retaining their raw values for audit."""
    result = copy.deepcopy(payload)
    groups: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for block in result.get("portfolios") or []:
        for row in block.get("holdings") or []:
            if row.get("symbol"):
                groups[_security_key(row)].append(row)

    as_of = _as_iso(result.get("cached_at"))
    for rows in groups.values():
        candidates = [candidate for row in rows if (candidate := _candidate(row))]
        if not candidates:
            for row in rows:
                price = _number(row.get("last_price")) or 0.0
                if price and _is_cost_basis_fallback(row, price):
                    row["cost_basis_price"] = _number(row.get("avg_price"))
                    row["cost_basis_value"] = _number(row.get("invested"))
                    row["broker_reported_price"] = None
                    row["broker_reported_value"] = None
                    row["broker_reported_pnl"] = None
                    row["broker_value_source"] = "unavailable"
                    row["broker_price_unavailable"] = True
                    row["broker_value_unavailable"] = True
                    row["last_price"] = None
                    row["market_price"] = None
                    row["market_price_source"] = "unavailable"
                    row["market_price_unavailable"] = True
                    row["valuation_state"] = "cost_basis_only"
            continue
        if len(rows) == 1:
            continue
        cluster = _winning_cluster(candidates)
        source_accounts = sorted({item["account"] for item in cluster})
        missing_count = len(rows) - len(candidates)
        if len(rows) > 1 and len(source_accounts) < 2 and missing_count == 0:
            continue
        canonical_price = float(median(item["price"] for item in cluster))
        for row in rows:
            _apply_mark(row, canonical_price, accounts=source_accounts, as_of=as_of)

    all_rows: list[dict[str, Any]] = []
    for block in result.get("portfolios") or []:
        holdings = block.get("holdings") or []
        all_rows.extend(holdings)
        block["summary"] = _summary(holdings, base=block.get("summary"))
    result["summary"] = _summary(all_rows, base=result.get("summary"))
    return result


def _summary(
    rows: list[dict[str, Any]],
    *,
    base: dict[str, Any] | None = None,
) -> dict[str, Any]:
    invested = sum(float(row.get("invested") or 0) for row in rows)
    current = sum(float(row.get("current_value") or 0) for row in rows)
    pnl = current - invested
    return {
        **(base or {}),
        "holdings_count": len(rows),
        "total_invested": round(invested, 2),
        "total_current_value": round(current, 2),
        "total_pnl": round(pnl, 2),
        "total_pnl_pct": round((pnl / invested * 100) if invested else 0.0, 2),
    }
