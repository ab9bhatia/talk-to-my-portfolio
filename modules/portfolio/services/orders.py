"""Place equity orders via Zerodha Kite / Groww Trade API (opt-in)."""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from modules.portfolio.config import (
    ACCOUNTS,
    CODE_TO_ACCOUNT_ID,
    GROWW_ACCOUNTS,
    get_account_code,
    is_known_account,
    resolve_account_ref,
)
from modules.portfolio.services.market_data import is_us_exchange, normalize_symbol

logger = logging.getLogger(__name__)

_TRADABLE_BROKERS = frozenset({"zerodha", "groww"})
_INDIAN_EXCHANGES = frozenset({"NSE", "BSE"})
_IST = ZoneInfo("Asia/Kolkata")


def nse_cash_session_state() -> dict[str, Any]:
    """
    NSE equity session phase in IST (for Zerodha REGULAR vs AMO routing).

    AMO window per Zerodha: ~4:00 PM–8:58 AM; no orders 1:00–5:30 AM maintenance.
    """
    now = datetime.now(_IST)
    clock = now.time()
    weekday = now.weekday()  # Mon=0

    if time(1, 0) <= clock < time(5, 30):
        return {
            "phase": "maintenance",
            "use_amo": False,
            "ist_now": now.strftime("%Y-%m-%d %H:%M IST"),
            "note": "Zerodha blocks API orders 1:00–5:30 AM IST (maintenance).",
        }

    regular_open = weekday < 5 and time(9, 15) <= clock <= time(15, 30)
    if regular_open:
        return {
            "phase": "regular",
            "use_amo": False,
            "ist_now": now.strftime("%Y-%m-%d %H:%M IST"),
            "note": "NSE cash market is open (9:15 AM–3:30 PM IST, Mon–Fri).",
        }

    return {
        "phase": "amo",
        "use_amo": True,
        "ist_now": now.strftime("%Y-%m-%d %H:%M IST"),
        "note": (
            "NSE is closed — Zerodha orders are placed as AMO (After Market Order) "
            "and execute when the market opens. Use Limit price (Market is not supported for AMO)."
        ),
    }


def trading_enabled() -> bool:
    return os.getenv("TRADING_ENABLED", "false").lower() in ("1", "true", "yes")


def trading_status() -> dict[str, Any]:
    zerodha_accounts = [
        {"account_id": aid, "code": meta["code"], "label": meta["label"], "broker": "zerodha"}
        for aid, meta in ACCOUNTS.items()
        if meta.get("enabled")
    ]
    groww_accounts = [
        {"account_id": aid, "code": meta["code"], "label": meta["label"], "broker": "groww"}
        for aid, meta in GROWW_ACCOUNTS.items()
        if meta.get("enabled")
    ]
    session = nse_cash_session_state()
    return {
        "enabled": trading_enabled(),
        "nse_session": session,
        "zerodha_accounts": zerodha_accounts,
        "groww_accounts": groww_accounts,
        "unsupported": ["sarwa (US)", "mutual funds", "US listings"],
        "requirements": (
            "Set TRADING_ENABLED=true in .env. "
            "Zerodha Kite app must include order placement (not read-only). "
            "Groww needs Trade API subscription."
        ),
    }


def _normalize_exchange(exchange: str | None) -> str:
    ex = (exchange or "NSE").upper().strip()
    if ex in _INDIAN_EXCHANGES:
        return ex
    if "NSE" in ex:
        return "NSE"
    if "BSE" in ex:
        return "BSE"
    raise ValueError(f"Unsupported exchange for trading: {exchange}")


def _broker_for_account(account_id: str | None) -> str | None:
    if not account_id:
        return None
    aid = account_id.strip().lower()
    if aid in ACCOUNTS:
        return "zerodha"
    if aid in GROWW_ACCOUNTS:
        return "groww"
    return None


def trade_accounts_for_holding(holding: dict[str, Any]) -> list[dict[str, Any]]:
    """Accounts that can trade this row (Indian equity only)."""
    if not trading_enabled():
        return []
    if holding.get("asset_class") == "mf":
        return []
    if is_us_exchange(holding.get("exchange")) or holding.get("broker") == "sarwa":
        return []

    rows: list[dict[str, Any]] = []
    breakdown = holding.get("account_breakdown")
    if breakdown:
        for part in breakdown:
            aid = part.get("account_id")
            broker = (part.get("broker") or _broker_for_account(aid) or "").lower()
            if not aid or broker not in _TRADABLE_BROKERS:
                continue
            try:
                ex = _normalize_exchange(part.get("exchange") or holding.get("exchange"))
            except ValueError:
                continue
            rows.append(
                {
                    "account_id": aid,
                    "code": part.get("abbrev") or get_account_code(aid),
                    "broker": broker,
                    "exchange": ex,
                    "quantity": float(part.get("quantity") or 0),
                    "symbol": part.get("symbol") or holding.get("symbol"),
                }
            )
    else:
        aid = holding.get("account_id")
        broker = (holding.get("broker") or _broker_for_account(aid) or "zerodha").lower()
        if aid and broker in _TRADABLE_BROKERS:
            try:
                ex = _normalize_exchange(holding.get("exchange"))
            except ValueError:
                return []
            rows.append(
                {
                    "account_id": aid,
                    "code": holding.get("account_code") or get_account_code(aid),
                    "broker": broker,
                    "exchange": ex,
                    "quantity": float(holding.get("quantity") or 0),
                    "symbol": holding.get("symbol"),
                }
            )
    return rows


def _holding_qty_for_account(
    *,
    account_id: str,
    broker: str,
    symbol: str,
    exchange: str,
) -> float:
    sym = normalize_symbol(symbol)
    ex = _normalize_exchange(exchange)
    if broker == "zerodha":
        from modules.portfolio.auth.zerodha import OAuthError, get_kite_client
        from modules.portfolio.services.portfolio import fetch_holdings

        try:
            holdings = fetch_holdings(account_id)
        except OAuthError:
            return 0.0
        for h in holdings:
            if normalize_symbol(h.get("symbol") or "") == sym and _normalize_exchange(h.get("exchange")) == ex:
                return float(h.get("quantity") or 0)
        return 0.0

    if broker == "groww":
        from modules.portfolio.services.groww_portfolio import fetch_groww_holdings

        for h in fetch_groww_holdings(account_id):
            if normalize_symbol(h.get("symbol") or "") == sym and _normalize_exchange(h.get("exchange")) == ex:
                return float(h.get("quantity") or 0)
        return 0.0

    return 0.0


def _zerodha_order_error_message(exc: Exception) -> str:
    text = str(exc)
    if "no ips configured" in text.lower() or "static ip" in text.lower():
        return (
            "Zerodha order failed: no IP whitelisted on your Kite developer account. "
            "At developers.kite.trade open Profile (top-right), not the app page — "
            "add your public IP under IP Whitelist (run: curl -s https://api.ipify.org). "
            "https://support.zerodha.com/category/trading-and-markets/general-kite/kite-api/articles/static-ip"
        )
    if "after market order" in text.lower() or "amo" in text.lower():
        return (
            "Zerodha order failed: outside market hours you need an AMO (after-market) order. "
            "Use Limit (not Market) with a price — the hub should send AMO automatically after refresh. "
            "https://support.zerodha.com/category/trading-and-markets/charts-and-orders/order/articles/auto-amo"
        )
    return f"Zerodha order failed: {text}"


def _place_zerodha_order(
    *,
    account_id: str,
    symbol: str,
    exchange: str,
    transaction_type: str,
    quantity: int,
    order_type: str,
    price: float | None,
) -> dict[str, Any]:
    from kiteconnect import KiteConnect
    from kiteconnect.exceptions import KiteException

    from modules.portfolio.auth.zerodha import OAuthError, get_kite_client

    kite = get_kite_client(account_id)
    session = nse_cash_session_state()
    if session["phase"] == "maintenance":
        raise ValueError(session["note"])

    ot = order_type.upper()
    use_amo = bool(session["use_amo"])
    if use_amo and ot == "MARKET":
        raise ValueError(
            "After-market orders require a Limit price (Market is not supported when NSE is closed)."
        )

    variety = kite.VARIETY_AMO if use_amo else kite.VARIETY_REGULAR
    params: dict[str, Any] = {
        "variety": variety,
        "exchange": _normalize_exchange(exchange),
        "tradingsymbol": symbol.strip().upper(),
        "transaction_type": transaction_type,
        "quantity": quantity,
        "product": kite.PRODUCT_CNC,
        "order_type": kite.ORDER_TYPE_MARKET if ot == "MARKET" else kite.ORDER_TYPE_LIMIT,
        "validity": kite.VALIDITY_DAY,
    }
    if params["order_type"] == kite.ORDER_TYPE_LIMIT:
        if price is None or price <= 0:
            raise ValueError("Limit price is required for LIMIT orders")
        params["price"] = round(float(price), 2)

    try:
        order_id = kite.place_order(**params)
    except KiteException as exc:
        raise RuntimeError(_zerodha_order_error_message(exc)) from exc

    return {
        "broker": "zerodha",
        "order_id": order_id,
        "account_id": account_id,
        "variety": variety,
        "amo": use_amo,
    }


def _place_groww_order(
    *,
    account_id: str,
    symbol: str,
    exchange: str,
    transaction_type: str,
    quantity: int,
    order_type: str,
    price: float | None,
) -> dict[str, Any]:
    from growwapi import GrowwAPI
    from growwapi.groww.exceptions import GrowwAPIException  # noqa: PLC0415

    from modules.portfolio.auth.groww import get_groww_client

    client = get_groww_client(account_id)
    ot = order_type.upper()
    order_type_const = (
        GrowwAPI.ORDER_TYPE_MARKET if ot == "MARKET" else GrowwAPI.ORDER_TYPE_LIMIT
    )
    txn = (
        GrowwAPI.TRANSACTION_TYPE_BUY
        if transaction_type.upper() == "BUY"
        else GrowwAPI.TRANSACTION_TYPE_SELL
    )
    limit_price = 0.0 if ot == "MARKET" else round(float(price or 0), 2)
    if ot == "LIMIT" and limit_price <= 0:
        raise ValueError("Limit price is required for LIMIT orders")

    try:
        resp = client.place_order(
            validity=GrowwAPI.VALIDITY_DAY,
            exchange=_normalize_exchange(exchange),
            order_type=order_type_const,
            price=limit_price,
            product=GrowwAPI.PRODUCT_CNC,
            quantity=quantity,
            segment=GrowwAPI.SEGMENT_CASH,
            trading_symbol=symbol.strip().upper(),
            transaction_type=txn,
        )
    except GrowwAPIException as exc:
        raise RuntimeError(f"Groww order failed: {exc}") from exc

    order_id = None
    if isinstance(resp, dict):
        order_id = resp.get("groww_order_id") or resp.get("order_id") or resp.get("orderReferenceId")
    return {"broker": "groww", "order_id": order_id, "account_id": account_id, "raw": resp}


def place_equity_order(
    *,
    account_ref: str,
    symbol: str,
    exchange: str,
    side: str,
    quantity: int,
    order_type: str = "MARKET",
    price: float | None = None,
    confirmed: bool = False,
) -> dict[str, Any]:
    """
    Place a delivery (CNC) equity order on Zerodha or Groww.
    Requires TRADING_ENABLED=true and confirmed=True from the client.
    """
    if not trading_enabled():
        raise PermissionError("Trading is disabled. Set TRADING_ENABLED=true in .env to enable.")

    if not confirmed:
        raise ValueError("Order not confirmed by the user.")

    if quantity <= 0:
        raise ValueError("Quantity must be positive.")

    side_u = side.strip().upper()
    if side_u not in ("BUY", "SELL"):
        raise ValueError("side must be BUY or SELL")

    account_id = resolve_account_ref(account_ref)
    if not is_known_account(account_id):
        raise ValueError(f"Unknown account: {account_ref}")

    broker: str | None = None
    if account_id in ACCOUNTS:
        broker = "zerodha"
    elif account_id in GROWW_ACCOUNTS:
        broker = "groww"
    else:
        raise ValueError("This account cannot place equity orders from the hub.")

    sym = symbol.strip().upper()
    if not re.match(r"^[A-Z0-9][A-Z0-9&.-]*$", sym):
        raise ValueError(f"Invalid symbol: {symbol}")

    ex = _normalize_exchange(exchange)

    if side_u == "SELL":
        held = _holding_qty_for_account(
            account_id=account_id,
            broker=broker,
            symbol=sym,
            exchange=ex,
        )
        if quantity > held + 1e-6:
            raise ValueError(
                f"Cannot sell {quantity} shares — account holds {int(held)} of {sym} on {ex}."
            )

    if broker == "zerodha":
        from kiteconnect import KiteConnect

        txn = KiteConnect.TRANSACTION_TYPE_BUY if side_u == "BUY" else KiteConnect.TRANSACTION_TYPE_SELL
        result = _place_zerodha_order(
            account_id=account_id,
            symbol=sym,
            exchange=ex,
            transaction_type=txn,
            quantity=quantity,
            order_type=order_type,
            price=price,
        )
    else:
        result = _place_groww_order(
            account_id=account_id,
            symbol=sym,
            exchange=ex,
            transaction_type=side_u,
            quantity=quantity,
            order_type=order_type,
            price=price,
        )

    logger.info(
        "Order placed %s %s %s x%d on %s (%s)",
        side_u,
        sym,
        ex,
        quantity,
        account_id,
        broker,
    )
    return {
        **result,
        "symbol": sym,
        "exchange": ex,
        "side": side_u,
        "quantity": quantity,
        "order_type": order_type.upper(),
        "account_code": get_account_code(account_id),
        "nse_session": nse_cash_session_state().get("phase"),
    }
