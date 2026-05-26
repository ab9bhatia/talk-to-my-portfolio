"""Fetch and normalize Groww holdings."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

from modules.portfolio.auth.groww import GrowwError, _is_auth_failure, get_groww_client
from modules.portfolio.db import groww_tokens as groww_token_store
from modules.portfolio.config import get_account_code, get_groww_account
from modules.portfolio.services.market_data import enrich_holdings
from modules.portfolio.services.portfolio import summarize_holdings

_LTP_BATCH_SIZE = 40


def _safe_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _preferred_exchange(raw: dict[str, Any]) -> str:
    tradable = raw.get("tradable_exchanges") or []
    if isinstance(tradable, list):
        for preferred in ("NSE", "BSE"):
            if preferred in tradable:
                return preferred
    exchange = raw.get("exchange") or raw.get("exchange_symbol") or "NSE"
    if isinstance(exchange, str):
        exchange = exchange.upper()
        if exchange in ("NSE", "BSE"):
            return exchange
    return "NSE"


def _groww_ltp_key(raw: dict[str, Any], symbol: str) -> str:
    return f"{_preferred_exchange(raw)}_{symbol}"


def _fetch_ltp_map(client: Any, raws: list[dict[str, Any]]) -> dict[str, float]:
    """Batch Groww get_ltp for equity holdings (segment CASH)."""
    keys: list[str] = []
    for raw in raws:
        symbol = (
            raw.get("trading_symbol")
            or raw.get("tradingsymbol")
            or raw.get("symbol")
        )
        if not symbol:
            continue
        qty = _safe_float(raw.get("quantity") or raw.get("qty"))
        if qty <= 0:
            continue
        keys.append(_groww_ltp_key(raw, str(symbol).strip().upper()))

    if not keys:
        return {}

    ltp_map: dict[str, float] = {}
    unique_keys = list(dict.fromkeys(keys))
    for offset in range(0, len(unique_keys), _LTP_BATCH_SIZE):
        batch = tuple(unique_keys[offset : offset + _LTP_BATCH_SIZE])
        try:
            response = client.get_ltp(batch, segment="CASH", timeout=30)
        except Exception:
            continue
        if isinstance(response, dict):
            for key, price in response.items():
                try:
                    ltp_map[key] = float(price)
                except (TypeError, ValueError):
                    pass
    return ltp_map


def _normalize_groww_holding(
    raw: dict[str, Any],
    account_id: str,
    *,
    ltp_map: dict[str, float] | None = None,
) -> dict[str, Any] | None:
    account = get_groww_account(account_id)
    symbol = (
        raw.get("trading_symbol")
        or raw.get("tradingsymbol")
        or raw.get("symbol")
        or raw.get("nse_symbol")
    )
    if not symbol:
        return None

    quantity = _safe_float(raw.get("quantity") or raw.get("qty"))
    if quantity <= 0:
        return None

    symbol = str(symbol).strip().upper()
    exchange = _preferred_exchange(raw)
    ltp_key = f"{exchange}_{symbol}"

    avg_price = _safe_float(
        raw.get("average_price") or raw.get("avg_price") or raw.get("average_buy_price")
    )
    last_price = _safe_float(
        raw.get("last_price")
        or raw.get("ltp")
        or raw.get("close")
        or raw.get("current_price")
    )
    if ltp_map and ltp_key in ltp_map:
        last_price = ltp_map[ltp_key]
    if last_price <= 0:
        last_price = avg_price

    invested = quantity * avg_price
    current_value = quantity * last_price
    pnl = current_value - invested

    return {
        "symbol": symbol,
        "exchange": exchange,
        "isin": raw.get("isin"),
        "quantity": quantity,
        "avg_price": round(avg_price, 2),
        "last_price": round(last_price, 2),
        "invested": round(invested, 2),
        "current_value": round(current_value, 2),
        "pnl": round(pnl, 2),
        "pnl_pct": round((pnl / invested * 100) if invested else 0.0, 2),
        "day_change_pct": raw.get("day_change_percentage") or raw.get("day_change_pct"),
        "account_id": account_id,
        "account_code": get_account_code(account_id),
        "account_label": get_account_code(account_id),
        "account_codes": get_account_code(account_id),
        "broker": "groww",
    }


def _extract_holding_rows(payload: Any) -> list[dict[str, Any]]:
    if payload is None:
        return []
    if isinstance(payload, list):
        return [h for h in payload if isinstance(h, dict)]
    if isinstance(payload, dict):
        for key in ("holdings", "data", "payload", "result"):
            block = payload.get(key)
            if isinstance(block, list):
                return [h for h in block if isinstance(h, dict)]
            if isinstance(block, dict):
                inner = block.get("holdings")
                if isinstance(inner, list):
                    return [h for h in inner if isinstance(h, dict)]
    return []


def fetch_groww_holdings(account_id: str) -> list[dict[str, Any]]:
    """Fetch demat equity holdings from Groww with live LTP."""
    from modules.portfolio.config import get_groww_credentials

    creds = get_groww_credentials(account_id)
    if creds["auth_method"] == "approval":
        groww_token_store.delete_token(account_id)

    last_exc: Exception | None = None
    for attempt in range(3):
        if attempt > 0:
            groww_token_store.delete_token(account_id)
        client = get_groww_client(account_id, force_refresh=True)
        try:
            response = client.get_holdings_for_user(timeout=30)
            break
        except Exception as exc:
            last_exc = exc
            if attempt < 2 and _is_auth_failure(exc):
                logger.warning("Groww holdings auth failed (attempt %s), retrying…", attempt + 1)
                continue
            raise GrowwError(
                f"Failed to fetch Groww holdings: {exc}. "
                "Approve today's key at groww.in/trade-api/api-keys, then open /portfolio?refresh=1"
            ) from exc
    else:
        raise GrowwError(f"Failed to fetch Groww holdings: {last_exc}") from last_exc

    rows = _extract_holding_rows(response)
    ltp_map = _fetch_ltp_map(client, rows)
    holdings: list[dict[str, Any]] = []
    for raw in rows:
        normalized = _normalize_groww_holding(raw, account_id, ltp_map=ltp_map)
        if normalized:
            holdings.append(normalized)
    return holdings


def fetch_groww_portfolio(
    account_id: str,
    *,
    with_metrics: bool = True,
) -> dict[str, Any]:
    """Return Groww portfolio block matching Zerodha shape."""
    account = get_groww_account(account_id)
    holdings = fetch_groww_holdings(account_id)
    if with_metrics:
        holdings = enrich_holdings(holdings)
    summary = summarize_holdings(holdings)
    return {
        "account_id": account_id,
        "account_code": get_account_code(account_id),
        "account_label": get_account_code(account_id),
        "user_id": account.get("user_id", "groww"),
        "broker": "groww",
        "summary": summary,
        "holdings": holdings,
    }
