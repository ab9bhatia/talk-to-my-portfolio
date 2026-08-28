"""Security consolidation and explicit fund/ETF overlap detection."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from modules.portfolio.services.advisory.models import InstrumentType


_PROFILE_FIELDS = (
    "owner_ref",
    "country_of_residence",
    "india_residency_status",
    "tax_profile",
    "base_currency",
    "account_type",
    "tax_loss_harvesting_mode",
    "gift_product_tax_verified",
)

_MERGED_FIELDS = (
    "last_price",
    "pe_ratio",
    "roce",
    "debt_to_equity",
    "free_cash_flow_positive",
    "revenue_growth_pct",
    "earnings_growth_pct",
    "earnings_revision_pct",
    "moat_score",
    "governance_risk",
    "governance_event",
    "governance_event_source",
    "governance_event_as_of",
    "governance_event_source_type",
    "business_thesis",
    "business_thesis_source",
    "business_thesis_as_of",
    "expected_return_inputs",
    "price_history",
    "return_1m_pct",
    "return_3m_pct",
    "return_6m_pct",
    "return_12m_pct",
    "return_1y_pct",
    "relative_strength_6m_pct",
    "pct_vs_dma50",
    "pct_vs_dma200",
    "pct_from_52w_high",
    "max_drawdown_12m_pct",
    "volume_confirmation",
    "momentum_as_of",
    "macro_alignment_score",
    "underlying_index",
    "factor_sleeve",
    "overlap_group",
    "corporate_action_pending",
    "cost_basis_unreconciled",
    "is_suspended",
    "is_tradable",
    "symbol_resolved",
    "tax_lots_available",
    "do_not_sell_before",
    "add_conditions",
    "exit_triggers",
    "replacement_available",
    "is_cyclical",
)


def instrument_type_for(holding: dict[str, Any]) -> InstrumentType:
    asset = str(holding.get("asset_class") or holding.get("instrument_type") or "equity").lower()
    symbol = str(holding.get("symbol") or "").upper()
    if asset in {"mf", "mutual_fund", "mutual fund"}:
        return InstrumentType.MUTUAL_FUND
    if asset == "etf" or symbol.endswith(("BEES", "ETF", "-ETF")):
        return InstrumentType.ETF
    if asset in {"bond", "debt"}:
        return InstrumentType.BOND
    if asset in {"gold", "precious_metal"}:
        return InstrumentType.GOLD
    if asset == "crypto":
        return InstrumentType.CRYPTO
    if asset == "cash":
        return InstrumentType.CASH
    return InstrumentType.EQUITY


def security_key(holding: dict[str, Any]) -> str:
    isin = str(holding.get("isin") or "").strip().upper()
    if isin:
        return f"ISIN:{isin}"
    instrument_id = str(holding.get("instrument_id") or "").strip()
    if instrument_id:
        return f"ID:{instrument_id}"
    exchange = str(holding.get("exchange") or "UNKNOWN").strip().upper()
    symbol = str(holding.get("symbol") or "").strip().upper()
    return f"SYMBOL:{exchange}:{symbol}"


def _profile_from_block(block: dict[str, Any]) -> dict[str, Any]:
    profile = dict(block.get("account_profile") or {})
    for field in _PROFILE_FIELDS:
        if block.get(field) is not None:
            profile[field] = block[field]
    return profile


def consolidate_family(family: dict[str, Any]) -> list[dict[str, Any]]:
    """Consolidate positions by ISIN/canonical identity while retaining accounts."""
    groups: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = defaultdict(list)
    account_totals: dict[str, float] = {}
    for block in family.get("portfolios") or []:
        account_id = str(block.get("account_id") or block.get("account_code") or "unknown")
        account_totals[account_id] = float(
            (block.get("summary") or {}).get("total_current_value")
            or sum(float(item.get("current_value") or 0) for item in block.get("holdings") or [])
        )
        for holding in block.get("holdings") or []:
            groups[security_key(holding)].append((block, holding))

    family_total = float(
        (family.get("summary") or {}).get("total_current_value")
        or sum(account_totals.values())
    )
    consolidated: list[dict[str, Any]] = []
    for key, rows in groups.items():
        first = rows[0][1]
        quantity = sum(float(row.get("quantity") or 0) for _, row in rows)
        current_value = sum(float(row.get("current_value") or 0) for _, row in rows)
        invested = sum(float(row.get("invested") or 0) for _, row in rows)
        positions: list[dict[str, Any]] = []
        account_profiles: dict[str, dict[str, Any]] = {}
        for block, row in rows:
            account_id = str(row.get("account_id") or block.get("account_id") or "unknown")
            account_code = str(
                row.get("account_code") or block.get("account_code") or account_id
            )
            value = float(row.get("current_value") or 0)
            account_total = account_totals.get(account_id) or 0.0
            positions.append(
                {
                    "account_id": account_id,
                    "account_code": account_code,
                    "broker": str(row.get("broker") or block.get("broker") or "unknown"),
                    "quantity": round(float(row.get("quantity") or 0), 6),
                    "current_value": round(value, 2),
                    "account_weight_pct": round((value / account_total) * 100, 2)
                    if account_total
                    else 0.0,
                }
            )
            account_profiles[account_id] = _profile_from_block(block)

        merged: dict[str, Any] = {}
        for field in _MERGED_FIELDS:
            for _, row in rows:
                if row.get(field) is not None:
                    merged[field] = row[field]
                    break
        if quantity and not merged.get("last_price"):
            merged["last_price"] = current_value / quantity

        pnl = current_value - invested
        consolidated.append(
            {
                **merged,
                "security_key": key,
                "symbol": str(first.get("symbol") or "").strip().upper(),
                "exchange": first.get("exchange"),
                "isin": first.get("isin"),
                "instrument_type": instrument_type_for(first),
                "consolidated_qty": round(quantity, 6),
                "consolidated_value": round(current_value, 2),
                "invested": round(invested, 2),
                "pnl": round(pnl, 2),
                "pnl_pct": round((pnl / invested) * 100, 2) if invested else 0.0,
                "family_weight_pct": round((current_value / family_total) * 100, 2)
                if family_total
                else 0.0,
                "positions": positions,
                "account_profiles": account_profiles,
                "source_rows": [row for _, row in rows],
            }
        )
    consolidated.sort(key=lambda item: (-item["consolidated_value"], item["symbol"]))
    return consolidated


def detect_overlap(
    holdings: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    """Detect only explicit index/factor overlap; never infer look-through from a name."""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for holding in holdings:
        if holding.get("instrument_type") not in {
            InstrumentType.ETF,
            InstrumentType.MUTUAL_FUND,
        }:
            continue
        label = (
            holding.get("overlap_group")
            or holding.get("underlying_index")
            or holding.get("factor_sleeve")
        )
        if label:
            groups[str(label).strip().lower()].append(holding)

    report: list[dict[str, Any]] = []
    by_security: dict[str, list[str]] = defaultdict(list)
    for label, rows in sorted(groups.items()):
        symbols = sorted({str(row.get("symbol") or "") for row in rows})
        if len(symbols) < 2:
            continue
        report.append(
            {
                "overlap_key": label,
                "symbols": symbols,
                "basis": "explicit_underlying_or_factor_label",
                "lookthrough_verified": False,
            }
        )
        for row in rows:
            by_security[row["security_key"]].extend(sym for sym in symbols if sym != row["symbol"])
    return report, dict(by_security)
