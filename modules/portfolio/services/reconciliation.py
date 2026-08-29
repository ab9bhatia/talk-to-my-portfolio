"""Canonical price/value provenance and deterministic portfolio reconciliation."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from statistics import median
from typing import Any

from modules.portfolio.db import instrument_master as instrument_store
from modules.portfolio.services.instrument_master import enrich_holding_identity


ABSOLUTE_TOLERANCE_INR = 50.0
TIMING_TOLERANCE_PCT = 1.0
WARNING_TOLERANCE_PCT = 3.0
BLOCKING_FAMILY_VALUE_PCT = 0.5
PRICE_CONSENSUS_TOLERANCE_PCT = 0.75
_AUTHORITATIVE_PRICE_SOURCES = frozenset(
    {
        "nse",
        "nse_quote",
        "yahoo",
        "yahoo_session_quote",
        "groww_ltp_api",
        "family_quote_consensus",
    }
)


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result


def _timestamp(holding: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        if holding.get(key) is not None:
            value = holding[key]
            try:
                epoch = float(value)
            except (TypeError, ValueError):
                return str(value)
            if epoch > 0:
                return datetime.fromtimestamp(epoch, tz=UTC).isoformat().replace("+00:00", "Z")
            return str(value)
    return None


def _security_key(holding: dict[str, Any]) -> str:
    if holding.get("instrument_id"):
        return f"ID:{holding['instrument_id']}"
    if holding.get("isin"):
        return f"ISIN:{str(holding['isin']).upper()}"
    exchange = str(holding.get("canonical_exchange") or holding.get("exchange") or "NSE").upper()
    symbol = str(holding.get("canonical_symbol") or holding.get("symbol") or "").upper()
    return f"SYMBOL:{exchange}:{symbol}"


def _is_cost_basis_fallback(holding: dict[str, Any], price: float) -> bool:
    source = str(
        holding.get("market_price_source")
        or holding.get("broker_price_source")
        or ""
    ).lower()
    if source == "cost_basis_fallback" or holding.get("market_price_unavailable"):
        return True
    avg = _number(holding.get("avg_price"))
    return bool(
        str(holding.get("broker") or "").lower() == "groww"
        and not source
        and avg is not None
        and abs(price - avg) <= 0.01
    )


def _price_candidate(holding: dict[str, Any]) -> dict[str, Any] | None:
    explicit_market = holding.get("market_price")
    price = _number(explicit_market if explicit_market is not None else holding.get("last_price"))
    if price is None or price <= 0 or _is_cost_basis_fallback(holding, price):
        return None
    source = str(
        holding.get("market_price_source")
        or holding.get("broker_price_source")
        or holding.get("broker")
        or "broker_snapshot"
    ).lower()
    return {
        "holding": holding,
        "price": price,
        "source": source,
        "account": str(holding.get("account_code") or holding.get("account_id") or "UNKNOWN"),
        "authoritative": source in _AUTHORITATIVE_PRICE_SOURCES,
    }


def _winning_price_cluster(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    clusters: list[list[dict[str, Any]]] = []
    for candidate in sorted(candidates, key=lambda item: item["price"]):
        target = next(
            (
                cluster
                for cluster in clusters
                if abs(candidate["price"] - median(item["price"] for item in cluster))
                / median(item["price"] for item in cluster)
                * 100
                <= PRICE_CONSENSUS_TOLERANCE_PCT
            ),
            None,
        )
        if target is None:
            clusters.append([candidate])
        else:
            target.append(candidate)
    return max(
        clusters,
        key=lambda cluster: (
            len({item["account"] for item in cluster}),
            sum(bool(item["authoritative"]) for item in cluster),
            len(cluster),
        ),
    )


def _align_family_market_prices(
    blocks: list[dict[str, Any]],
    *,
    family: dict[str, Any],
) -> list[dict[str, Any]]:
    """Apply one market mark per instrument without overwriting broker provenance."""
    aligned_blocks: list[dict[str, Any]] = []
    grouped: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for block in blocks:
        holdings = [enrich_holding_identity(dict(item)) for item in block.get("holdings") or []]
        aligned_blocks.append({**block, "holdings": holdings})
        for holding in holdings:
            grouped[_security_key(holding)].append(holding)

    for rows in grouped.values():
        candidates = [item for row in rows if (item := _price_candidate(row))]
        if not candidates:
            continue
        cluster = _winning_price_cluster(candidates)
        cluster_accounts = {item["account"] for item in cluster}
        can_align = len(cluster_accounts) >= 2 or any(item["authoritative"] for item in cluster)
        if len(rows) > 1 and not can_align:
            continue
        canonical_price = float(median(item["price"] for item in cluster))
        source_accounts = sorted(cluster_accounts)
        source_as_of = next(
            (
                _timestamp(
                    item["holding"],
                    "market_price_as_of",
                    "broker_price_as_of",
                    "position_as_of",
                )
                for item in cluster
                if _timestamp(
                    item["holding"],
                    "market_price_as_of",
                    "broker_price_as_of",
                    "position_as_of",
                )
            ),
            _timestamp(family, "cached_at"),
        )
        for row in rows:
            quantity = _number(row.get("quantity")) or 0.0
            invested = _number(row.get("invested")) or 0.0
            original_value = _number(row.get("current_value"))
            original_price = _number(row.get("last_price"))
            original_pnl = _number(row.get("pnl"))
            row.setdefault("broker_reported_price", original_price)
            row.setdefault("broker_reported_value", original_value)
            row.setdefault("broker_reported_pnl", original_pnl)
            row.setdefault("broker_price_source", row.get("broker") or "broker_snapshot")
            row["market_price"] = round(canonical_price, 4)
            row["market_price_source"] = "family_quote_consensus"
            row["market_price_as_of"] = source_as_of
            row["market_price_unavailable"] = False
            row["price_consensus_accounts"] = source_accounts
            row["display_price"] = round(canonical_price, 4)
            row["display_value"] = round(quantity * canonical_price, 2)
            row["display_pnl"] = round(row["display_value"] - invested, 2)
            row["display_pnl_pct"] = round(
                (row["display_pnl"] / invested * 100) if invested else 0.0,
                2,
            )
            high_52w = _number(row.get("high_52w"))
            target_price = _number(row.get("target_price"))
            if high_52w and high_52w > 0:
                row["pct_from_52w_high"] = round(
                    ((canonical_price - high_52w) / high_52w) * 100,
                    2,
                )
                row["recovery_to_52w_high_pct"] = round(
                    max(0.0, (high_52w - canonical_price) / high_52w * 100),
                    2,
                )
            if target_price and canonical_price > 0:
                row["upside_pct"] = round(
                    ((target_price - canonical_price) / canonical_price) * 100,
                    2,
                )
    return aligned_blocks


def _price_provenance(holding: dict[str, Any], *, family: dict[str, Any]) -> dict[str, Any]:
    currency = str(holding.get("currency") or "INR").upper()
    market_session_date = (
        holding.get("market_session_date")
        or family.get("market_session_date")
        or str(_timestamp(family, "cached_at") or "")[:10]
        or None
    )
    broker_price = _number(
        holding.get("broker_reported_price")
        if holding.get("broker_reported_price") is not None
        else holding.get("last_price")
    )
    market_price = None if holding.get("market_price_unavailable") else _number(
        holding.get("market_price")
        if holding.get("market_price") is not None
        else holding.get("last_price")
    )
    broker_value = _number(
        holding.get("broker_reported_value")
        if holding.get("broker_reported_value") is not None
        else holding.get("current_value")
    )
    fx_rate = _number(holding.get("fx_rate") or holding.get("usd_inr"))
    return {
        "broker_reported_price": broker_price,
        "broker_price_source": holding.get("broker_price_source") or holding.get("broker") or "broker_snapshot",
        "broker_price_as_of": _timestamp(holding, "broker_price_as_of", "position_as_of", "cached_at") or family.get("cached_at"),
        "broker_reported_value": broker_value,
        "broker_value_source": holding.get("broker_value_source") or holding.get("broker") or "broker_snapshot",
        "broker_value_as_of": _timestamp(holding, "broker_value_as_of", "position_as_of", "cached_at") or family.get("cached_at"),
        "market_price": market_price,
        "market_price_source": holding.get("market_price_source") or (
            "yahoo_session_quote" if holding.get("quote_refreshed") else holding.get("broker") or "broker_snapshot"
        ),
        "market_price_as_of": _timestamp(holding, "market_price_as_of", "price_as_of", "cached_at") or family.get("cached_at"),
        "market_session_date": market_session_date,
        "currency": currency,
        "fx_rate": fx_rate,
        "fx_source": holding.get("fx_source"),
        "fx_as_of": holding.get("fx_as_of"),
    }


def _corporate_action_pending(
    holding: dict[str, Any],
    *,
    actions_by_instrument: dict[str, list[dict[str, Any]]] | None = None,
) -> tuple[bool, list[dict[str, Any]]]:
    instrument_id = holding.get("instrument_id")
    if instrument_id and actions_by_instrument is not None:
        actions = actions_by_instrument.get(str(instrument_id), [])
    elif instrument_id:
        actions = instrument_store.list_corporate_actions(
            instrument_id=str(instrument_id), pending_only=True
        )
    else:
        actions = []
    pending = bool(
        holding.get("corporate_action_pending")
        or holding.get("cost_basis_unreconciled")
        or actions
    )
    return pending, actions


def reconcile_holding(
    holding: dict[str, Any],
    *,
    family: dict[str, Any],
    family_value: float,
    corporate_actions_by_instrument: dict[str, list[dict[str, Any]]] | None = None,
    overrides_by_instrument: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    row = dict(holding) if "symbol_resolved" in holding else enrich_holding_identity(holding)
    provenance = _price_provenance(row, family=family)
    quantity = _number(row.get("reconciled_quantity"))
    if quantity is None:
        quantity = _number(row.get("quantity")) or 0.0
    market_price = provenance["market_price"]
    broker_value = provenance["broker_reported_value"]
    invested = _number(row.get("invested")) or 0.0
    broker_pnl = _number(
        row.get("broker_reported_pnl")
        if row.get("broker_reported_pnl") is not None
        else row.get("pnl")
    )
    marked_value = quantity * market_price if market_price is not None else None
    derived_pnl = marked_value - invested if marked_value is not None else None
    delta = broker_value - marked_value if broker_value is not None and marked_value is not None else None
    delta_abs = abs(delta) if delta is not None else None
    delta_pct = (
        (delta_abs / abs(marked_value) * 100)
        if delta_abs is not None and marked_value not in {None, 0}
        else None
    )
    family_impact_pct = (
        (delta_abs / family_value * 100) if delta_abs is not None and family_value > 0 else 0.0
    )

    reasons: list[str] = []
    repair_action = "No action required."
    corporate_action, actions = _corporate_action_pending(
        row,
        actions_by_instrument=corporate_actions_by_instrument,
    )
    unresolved = not bool(row.get("symbol_resolved"))
    suspended = (
        row.get("is_suspended") is True
        or row.get("is_tradable") is False
        or row.get("tradability_status") in {"SUSPENDED", "DELISTED", "RESTRICTED"}
    )

    if unresolved:
        state = "UNRESOLVED_IDENTITY"
        blocking = True
        reasons.append(row.get("identity_resolution_reason") or "Canonical identity is unresolved.")
        repair_action = "Resolve the instrument using ISIN or an authoritative broker identifier."
    elif corporate_action:
        state = "CORPORATE_ACTION_REVIEW"
        blocking = True
        reasons.append("A corporate action or cost-basis transition requires review.")
        repair_action = "Attach the exchange/broker corporate-action notice and approve a lineage override."
    elif market_price is None or broker_value is None:
        state = "WARNING"
        blocking = True
        reasons.append("Broker value or authoritative market price is unavailable.")
        repair_action = "Refresh the quote or import a broker statement with value provenance."
    elif delta_abs is not None and delta_abs <= ABSOLUTE_TOLERANCE_INR:
        state = "RECONCILED"
        blocking = False
    elif delta_pct is not None and delta_pct <= TIMING_TOLERANCE_PCT:
        state = "RECONCILED_WITH_TIMING_DIFFERENCE"
        blocking = False
        reasons.append("Small broker/market timing difference within tolerance.")
        repair_action = "Recheck after the next finalized market session."
    elif (
        delta_pct is not None
        and delta_pct <= WARNING_TOLERANCE_PCT
        and family_impact_pct < BLOCKING_FAMILY_VALUE_PCT
    ):
        state = "WARNING"
        blocking = False
        reasons.append("Value mismatch exceeds timing tolerance but has low family-value impact.")
        repair_action = "Compare broker timestamp with the market-price timestamp."
    else:
        state = "BLOCKING_MISMATCH"
        blocking = True
        reasons.append("Value mismatch is material by percentage or family-value impact.")
        repair_action = "Reconcile quantity, price currency, FX, and corporate actions before advice."

    fx_expected_price = None
    fx_delta = None
    if provenance["currency"] == "USD" and provenance["fx_rate"]:
        price_usd = _number(row.get("last_price_usd") or row.get("market_price_usd"))
        if price_usd is not None:
            fx_expected_price = price_usd * float(provenance["fx_rate"])
            if market_price is not None:
                fx_delta = market_price - fx_expected_price
                if abs(fx_delta) > max(1.0, abs(fx_expected_price) * 0.005):
                    reasons.append("FX_MISMATCH: converted market price differs from the recorded FX rate.")
                    if state in {"RECONCILED", "RECONCILED_WITH_TIMING_DIFFERENCE"}:
                        state = "WARNING"

    instrument_id = str(row.get("instrument_id") or "")
    if instrument_id and overrides_by_instrument is not None:
        override_rows = overrides_by_instrument.get(instrument_id, [])
    elif instrument_id:
        override_rows = instrument_store.list_overrides(instrument_id=instrument_id)
    else:
        override_rows = []
    active_overrides = [item for item in override_rows if item.get("active")]
    if active_overrides:
        reasons.append(f"Manual override present: {active_overrides[0]['override_type']}.")
        if active_overrides[0]["override_type"] in {
            "ACCEPT_TIMING_DIFFERENCE",
            "VALUE_EXPLANATION",
            "CORPORATE_ACTION_CONFIRMED",
        }:
            reasons.append(f"Pre-override state retained for audit: {state}.")
            state = "WARNING"
            blocking = False
            repair_action = "Revalidate the sourced manual override at its next review date."

    reconciliation = {
        "state": state,
        "blocking": blocking,
        "reconciled_quantity": round(quantity, 8),
        **provenance,
        "marked_value": round(marked_value, 2) if marked_value is not None else None,
        "invested_value": round(invested, 2),
        "broker_reported_pnl": round(broker_pnl, 2) if broker_pnl is not None else None,
        "derived_pnl": round(derived_pnl, 2) if derived_pnl is not None else None,
        "reconciliation_delta": round(delta, 2) if delta is not None else None,
        "reconciliation_delta_pct": round(delta_pct, 4) if delta_pct is not None else None,
        "family_value_impact_pct": round(family_impact_pct, 4),
        "fx_expected_price": round(fx_expected_price, 4) if fx_expected_price is not None else None,
        "fx_delta": round(fx_delta, 4) if fx_delta is not None else None,
        "reasons": reasons,
        "likely_cause": reasons[0] if reasons else "Within configured tolerances.",
        "repair_action": repair_action,
        "corporate_actions": actions,
        "manual_overrides": active_overrides,
        "tradability_status": row.get("tradability_status") or ("SUSPENDED" if suspended else "UNKNOWN"),
        "as_of": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    row.update(
        {
            "broker_reported_price": provenance["broker_reported_price"],
            "broker_reported_value": provenance["broker_reported_value"],
            "market_price": provenance["market_price"],
            "marked_value": reconciliation["marked_value"],
            "invested_value": reconciliation["invested_value"],
            "broker_reported_pnl": reconciliation["broker_reported_pnl"],
            "derived_pnl": reconciliation["derived_pnl"],
            "reconciliation_delta": reconciliation["reconciliation_delta"],
            "reconciliation_state": state,
            "reconciliation_blocking": blocking,
            "reconciliation": reconciliation,
            "is_tradable": False if suspended else row.get("is_tradable", True),
            "corporate_action_pending": corporate_action,
            "display_price": provenance["market_price"],
            "display_value": reconciliation["marked_value"],
            "display_pnl": reconciliation["derived_pnl"],
            "display_pnl_pct": round(
                (reconciliation["derived_pnl"] / invested * 100)
                if reconciliation["derived_pnl"] is not None and invested
                else 0.0,
                2,
            ),
        }
    )
    return row


def reconcile_family(family: dict[str, Any]) -> dict[str, Any]:
    """Annotate account rows and return account/security/family reconciliation."""
    family_copy = {**family}
    original_blocks = _align_family_market_prices(
        list(family.get("portfolios") or []),
        family=family,
    )
    pending_actions = instrument_store.list_corporate_actions(pending_only=True)
    actions_by_instrument: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for action in pending_actions:
        actions_by_instrument[str(action.get("instrument_id") or "")].append(action)
    overrides_by_instrument: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for override in instrument_store.list_overrides():
        overrides_by_instrument[str(override.get("instrument_id") or "")].append(override)
    family_value = sum(
        float(item.get("display_value") or item.get("current_value") or 0)
        for block in original_blocks
        for item in block.get("holdings") or []
    )
    blocks: list[dict[str, Any]] = []
    all_rows: list[dict[str, Any]] = []
    by_account: dict[str, dict[str, Any]] = {}
    identities_by_account: defaultdict[tuple[str, str], int] = defaultdict(int)
    for block in original_blocks:
        account_code = str(block.get("account_code") or block.get("account_id") or "UNKNOWN")
        holdings = [
            reconcile_holding(
                item,
                family=family,
                family_value=family_value,
                corporate_actions_by_instrument=actions_by_instrument,
                overrides_by_instrument=overrides_by_instrument,
            )
            for item in block.get("holdings") or []
        ]
        for item in holdings:
            identities_by_account[(account_code, str(item.get("instrument_id") or item.get("symbol")))] += 1
        account_broker = sum(float(item.get("broker_reported_value") or 0) for item in holdings)
        account_marked = sum(float(item.get("marked_value") or 0) for item in holdings)
        account_delta = account_broker - account_marked
        by_account[account_code] = {
            "account_code": account_code,
            "broker_reported_value": round(account_broker, 2),
            "marked_value": round(account_marked, 2),
            "reconciliation_delta": round(account_delta, 2),
            "blocking_positions": sum(bool(item.get("reconciliation_blocking")) for item in holdings),
        }
        account_invested = sum(float(item.get("invested") or 0) for item in holdings)
        account_display = sum(float(item.get("display_value") or 0) for item in holdings)
        account_pnl = account_display - account_invested
        blocks.append(
            {
                **block,
                "holdings": holdings,
                "summary": {
                    **(block.get("summary") or {}),
                    "total_current_value": round(account_display, 2),
                    "total_invested": round(account_invested, 2),
                    "total_pnl": round(account_pnl, 2),
                    "total_pnl_pct": round(
                        (account_pnl / account_invested * 100) if account_invested else 0.0,
                        2,
                    ),
                },
            }
        )
        all_rows.extend(holdings)

    for row in all_rows:
        key = (str(row.get("account_code") or row.get("account_id") or "UNKNOWN"), str(row.get("instrument_id") or row.get("symbol")))
        if identities_by_account[key] > 1:
            row["reconciliation"]["reasons"].append("DUPLICATED_ACCOUNT_POSITION")
            if row["reconciliation_state"] == "RECONCILED":
                row["reconciliation_state"] = "WARNING"
                row["reconciliation"]["state"] = "WARNING"

    by_security: dict[str, dict[str, Any]] = {}
    for row in all_rows:
        key = str(row.get("instrument_id") or f"UNRESOLVED:{row.get('exchange')}:{row.get('symbol')}")
        item = by_security.setdefault(
            key,
            {
                "instrument_id": row.get("instrument_id"),
                "isin": row.get("isin"),
                "symbol": row.get("canonical_symbol") or row.get("symbol"),
                "display_name": row.get("canonical_display_name") or row.get("symbol"),
                "exchange": row.get("canonical_exchange") or row.get("exchange"),
                "broker_reported_value": 0.0,
                "marked_value": 0.0,
                "reconciliation_delta": 0.0,
                "states": [],
                "blocking": False,
                "accounts": [],
                "broker_value_sources": [],
                "market_price_sources": [],
                "broker_value_as_of": [],
                "market_price_as_of": [],
                "reasons": [],
                "repair_actions": [],
            },
        )
        item["broker_reported_value"] += float(row.get("broker_reported_value") or 0)
        item["marked_value"] += float(row.get("marked_value") or 0)
        item["reconciliation_delta"] += float(row.get("reconciliation_delta") or 0)
        item["states"].append(row.get("reconciliation_state"))
        item["blocking"] = bool(item["blocking"] or row.get("reconciliation_blocking"))
        item["accounts"].append(row.get("account_code") or row.get("account_id"))
        detail = row.get("reconciliation") or {}
        item["broker_value_sources"].append(detail.get("broker_value_source"))
        item["market_price_sources"].append(detail.get("market_price_source"))
        item["broker_value_as_of"].append(detail.get("broker_value_as_of"))
        item["market_price_as_of"].append(detail.get("market_price_as_of"))
        item["reasons"].extend(detail.get("reasons") or [])
        item["repair_actions"].append(detail.get("repair_action"))

    security_rows = []
    for item in by_security.values():
        state_order = {
            "UNRESOLVED_IDENTITY": 5,
            "CORPORATE_ACTION_REVIEW": 4,
            "BLOCKING_MISMATCH": 3,
            "WARNING": 2,
            "RECONCILED_WITH_TIMING_DIFFERENCE": 1,
            "RECONCILED": 0,
        }
        item["state"] = max(item.pop("states"), key=lambda value: state_order.get(str(value), 2))
        item["broker_reported_value"] = round(item["broker_reported_value"], 2)
        item["marked_value"] = round(item["marked_value"], 2)
        item["reconciliation_delta"] = round(item["reconciliation_delta"], 2)
        item["accounts"] = sorted({str(code) for code in item["accounts"] if code})
        for field in (
            "broker_value_sources",
            "market_price_sources",
            "broker_value_as_of",
            "market_price_as_of",
            "reasons",
            "repair_actions",
        ):
            item[field] = sorted({str(value) for value in item[field] if value})
        item["likely_cause"] = item["reasons"][0] if item["reasons"] else "Within configured tolerances."
        material_repairs = [
            value for value in item["repair_actions"] if value != "No action required."
        ]
        item["repair_action"] = (
            material_repairs[0]
            if item["blocking"] and material_repairs
            else item["repair_actions"][0]
            if item["repair_actions"]
            else "No action required."
        )
        security_rows.append(item)

    total_marked = sum(float(item.get("marked_value") or 0) for item in security_rows)
    resolved_value = sum(
        float(item.get("marked_value") or 0) for item in security_rows if not item.get("blocking")
    )
    resolved_count = sum(bool(item.get("instrument_id")) for item in security_rows)
    summary = {
        "family_broker_reported_value": round(sum(float(item.get("broker_reported_value") or 0) for item in security_rows), 2),
        "family_marked_value": round(total_marked, 2),
        "family_reconciliation_delta": round(sum(float(item.get("reconciliation_delta") or 0) for item in security_rows), 2),
        "family_value_reconciled_pct": round((resolved_value / total_marked * 100) if total_marked else 100.0, 2),
        "securities_resolved_pct": round((resolved_count / len(security_rows) * 100) if security_rows else 100.0, 2),
        "value_weighted_quote_coverage_pct": round(
            (
                sum(float(row.get("marked_value") or 0) for row in all_rows if row.get("market_price") is not None)
                / total_marked
                * 100
            ) if total_marked else 100.0,
            2,
        ),
        "blocking_securities": sum(bool(item.get("blocking")) for item in security_rows),
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    security_rows.sort(
        key=lambda item: (-abs(float(item.get("reconciliation_delta") or 0)), str(item.get("symbol")))
    )
    family_copy["portfolios"] = blocks
    total_invested = sum(float(row.get("invested") or 0) for row in all_rows)
    display_pnl = total_marked - total_invested
    family_copy["summary"] = {
        **(family.get("summary") or {}),
        "total_current_value": round(total_marked, 2),
        "total_invested": round(total_invested, 2),
        "total_pnl": round(display_pnl, 2),
        "total_pnl_pct": round(
            (display_pnl / total_invested * 100) if total_invested else 0.0,
            2,
        ),
    }
    family_copy["reconciliation"] = {
        "summary": summary,
        "by_account": sorted(by_account.values(), key=lambda item: item["account_code"]),
        "by_security": security_rows,
        "unresolved_instruments": [item for item in security_rows if not item.get("instrument_id")],
        "corporate_action_review": pending_actions,
    }
    return family_copy
