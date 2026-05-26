"""Zerodha mutual fund holdings via Kite Connect /mf/holdings."""

from __future__ import annotations

from typing import Any

from kiteconnect.exceptions import KiteException

from modules.portfolio.auth.zerodha import OAuthError, get_kite_client
from modules.portfolio.config import ACCOUNTS
from modules.portfolio.services.mf_cap import classify_mf_cap


def _safe_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def normalize_mf_holding(raw: dict[str, Any], account_id: str) -> dict[str, Any] | None:
    """Convert a Kite MF holding into the hub holding shape."""
    account = ACCOUNTS[account_id]
    quantity = _safe_float(raw.get("quantity"))
    if quantity <= 0:
        return None

    isin = (raw.get("tradingsymbol") or raw.get("isin") or "").strip()
    fund_name = (raw.get("fund") or isin or "MF").strip()
    avg_price = _safe_float(raw.get("average_price"))
    last_price = _safe_float(raw.get("last_price") or avg_price)
    invested = quantity * avg_price
    current_value = quantity * last_price
    pnl = _safe_float(raw.get("pnl") if raw.get("pnl") is not None else current_value - invested)

    return {
        "symbol": isin or fund_name[:12].upper().replace(" ", ""),
        "fund_name": fund_name,
        "exchange": "MF",
        "asset_class": "mf",
        "isin": isin,
        "quantity": quantity,
        "avg_price": round(avg_price, 4),
        "last_price": round(last_price, 4),
        "invested": round(invested, 2),
        "current_value": round(current_value, 2),
        "pnl": round(pnl, 2),
        "pnl_pct": round((pnl / invested * 100) if invested else 0.0, 2),
        "day_change_pct": None,
        "market_cap": classify_mf_cap(fund_name),
        "sector": "Mutual fund",
        "pe_ratio": None,
        "roce": None,
        "debt_to_equity": None,
        "pct_from_52w_high": None,
        "upside_pct": None,
        "rating_label": None,
        "rating_slug": None,
        "rating_rank": None,
        "account_id": account_id,
        "account_code": account["code"],
        "account_label": account["code"],
        "account_codes": account["code"],
        "broker": "zerodha",
    }


def fetch_mf_holdings(account_id: str) -> list[dict[str, Any]]:
    """Fetch mutual fund holdings from Kite (demat MF)."""
    kite = get_kite_client(account_id)
    try:
        raw_holdings = kite.mf_holdings()
    except KiteException as exc:
        raise OAuthError(f"Failed to fetch MF holdings for '{account_id}': {exc}") from exc

    if not isinstance(raw_holdings, list):
        return []

    holdings: list[dict[str, Any]] = []
    for raw in raw_holdings:
        if not isinstance(raw, dict):
            continue
        normalized = normalize_mf_holding(raw, account_id)
        if normalized:
            holdings.append(normalized)
    return holdings
