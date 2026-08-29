"""Deterministic canonical instrument resolution over the local master."""

from __future__ import annotations

import hashlib
import re
from datetime import date
from typing import Any

from modules.portfolio.db import instrument_master as store


_INDIA_SERIES = re.compile(r"-(?:BE|BZ|EQ|IV|SM|ST)$", re.IGNORECASE)
_US_EXCHANGES = frozenset({"US", "NYSE", "NASDAQ", "AMEX"})


def canonical_symbol(symbol: str, exchange: str) -> str:
    value = str(symbol or "").strip().upper()
    if exchange in {"NSE", "BSE"}:
        value = _INDIA_SERIES.sub("", value)
    return value


def normalize_exchange(holding: dict[str, Any]) -> str:
    raw = str(holding.get("exchange") or "NSE").strip().upper()
    asset = str(holding.get("asset_class") or holding.get("instrument_type") or "").lower()
    if asset in {"mf", "mutual_fund", "mutual fund"} or raw == "MF":
        return "AMFI"
    if asset == "crypto":
        return "CRYPTO"
    return raw


def classify_instrument(holding: dict[str, Any]) -> str:
    asset = str(holding.get("asset_class") or holding.get("instrument_type") or "equity").lower()
    quote_type = str(holding.get("quote_type") or holding.get("yahoo_quote_type") or "").upper()
    if asset in {"mf", "mutual_fund", "mutual fund"}:
        return "mutual_fund"
    if asset == "etf" or quote_type == "ETF":
        return "etf"
    if asset in {"bond", "debt"}:
        return "bond"
    if asset in {"reit", "invit", "gold", "crypto", "cash"}:
        return asset
    return "equity"


def _stable_id(*, isin: str | None, symbol: str, exchange: str) -> str:
    identity = f"ISIN:{isin}" if isin else f"SYMBOL:{exchange}:{symbol}"
    return "ins_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]


def _source_as_of(holding: dict[str, Any]) -> str:
    value = holding.get("source_as_of") or holding.get("market_session_date")
    return str(value)[:10] if value else date.today().isoformat()


def resolve_holding(holding: dict[str, Any], *, persist: bool = True) -> dict[str, Any]:
    """Resolve authoritative IDs before exact exchange-aware symbol aliases."""
    store.init_db()
    exchange = normalize_exchange(holding)
    raw_symbol = str(holding.get("symbol") or "").strip().upper()
    symbol = canonical_symbol(raw_symbol, exchange)
    isin = str(holding.get("isin") or "").strip().upper() or None

    instrument = None
    method = "UNRESOLVED"
    existing_id = str(holding.get("instrument_id") or "").strip()
    if existing_id:
        instrument = store.get_instrument(existing_id)
        method = "INSTRUMENT_ID" if instrument else method
    if instrument is None and isin:
        instrument = store.find_by_isin(isin)
        method = "ISIN" if instrument else method
    if instrument is None:
        for alias_type, value in (
            ("BROKER_INSTRUMENT_ID", holding.get("broker_instrument_id")),
            ("YAHOO_TICKER", holding.get("yahoo_ticker")),
            ("BROKER_SYMBOL", raw_symbol),
        ):
            if value:
                instrument = store.resolve_alias(alias_type, str(value), exchange=exchange)
                if instrument:
                    method = alias_type
                    break

    if instrument is None and not (symbol or isin):
        return {
            "resolved": False,
            "resolution_method": "UNRESOLVED",
            "instrument": None,
            "reason": "Neither symbol nor authoritative identifier was supplied.",
        }

    if instrument is None:
        instrument_id = _stable_id(isin=isin, symbol=symbol, exchange=exchange)
        status = "SUSPENDED" if holding.get("is_suspended") else (
            "RESTRICTED" if holding.get("is_tradable") is False else "ACTIVE"
        )
        instrument = {
            "instrument_id": instrument_id,
            "version": store.SCHEMA_VERSION,
            "isin": isin,
            "canonical_symbol": symbol or isin or "UNKNOWN",
            "exchange": exchange,
            "display_name": (
                holding.get("fund_name") or holding.get("display_name")
                or holding.get("name") or symbol or isin or "Unknown instrument"
            ),
            "legal_name": holding.get("legal_name"),
            "instrument_type": classify_instrument(holding),
            "currency": str(holding.get("currency") or ("USD" if exchange in _US_EXCHANGES else "INR")).upper(),
            "domicile": holding.get("domicile") or ("US" if exchange in _US_EXCHANGES else "IN"),
            "issuer_or_amc": holding.get("issuer_or_amc") or holding.get("amc"),
            "scheme_plan": holding.get("scheme_plan"),
            "scheme_option": holding.get("scheme_option"),
            "underlying_index": holding.get("underlying_index"),
            "active": status == "ACTIVE",
            "tradability_status": status,
            "source": str(holding.get("identity_source") or holding.get("broker") or "portfolio_holding"),
            "source_as_of": _source_as_of(holding),
        }
        if persist:
            instrument = store.upsert_instrument(instrument)
        method = "CREATED_FROM_ISIN" if isin else "CREATED_FROM_EXACT_SYMBOL"

    if persist:
        as_of = _source_as_of(holding)
        source = str(holding.get("broker") or "portfolio_holding")
        if raw_symbol:
            store.add_alias(
                instrument["instrument_id"], alias_type="BROKER_SYMBOL",
                alias_value=raw_symbol, exchange=exchange, source=source, source_as_of=as_of,
            )
        if holding.get("broker_instrument_id"):
            store.add_alias(
                instrument["instrument_id"], alias_type="BROKER_INSTRUMENT_ID",
                alias_value=str(holding["broker_instrument_id"]), exchange=exchange,
                source=source, source_as_of=as_of,
            )
        if holding.get("yahoo_ticker"):
            store.add_alias(
                instrument["instrument_id"], alias_type="YAHOO_TICKER",
                alias_value=str(holding["yahoo_ticker"]), exchange=exchange,
                source="yahoo", source_as_of=as_of,
            )

    return {
        "resolved": True,
        "resolution_method": method,
        "instrument": instrument,
        "reason": None,
    }


def enrich_holding_identity(holding: dict[str, Any]) -> dict[str, Any]:
    resolution = resolve_holding(holding)
    row = dict(holding)
    if not resolution["resolved"]:
        row.update(
            {
                "symbol_resolved": False,
                "identity_resolution_method": resolution["resolution_method"],
                "identity_resolution_reason": resolution["reason"],
            }
        )
        return row
    instrument = resolution["instrument"]
    row.update(
        {
            "instrument_id": instrument["instrument_id"],
            "isin": instrument.get("isin") or row.get("isin"),
            "canonical_symbol": instrument["canonical_symbol"],
            "canonical_exchange": instrument["exchange"],
            "canonical_display_name": instrument["display_name"],
            "instrument_type": instrument["instrument_type"],
            "currency": instrument["currency"],
            "tradability_status": instrument["tradability_status"],
            "symbol_resolved": True,
            "identity_resolution_method": resolution["resolution_method"],
        }
    )
    return row
