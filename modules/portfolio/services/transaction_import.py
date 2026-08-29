"""Deterministic preview-first transaction import framework."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from datetime import date
from typing import Any

from modules.portfolio.db import transaction_ledger
from modules.portfolio.services.instrument_master import resolve_holding


EVENT_TYPES = {
    "BUY", "SELL", "DIVIDEND", "INTEREST", "FEE", "TAX", "DEPOSIT", "WITHDRAWAL",
    "TRANSFER_IN", "TRANSFER_OUT", "BONUS", "SPLIT", "MERGER", "DEMERGER", "RIGHTS",
    "REDEMPTION", "SUBSCRIPTION", "FX_CONVERSION", "CRYPTO_TRANSFER", "OTHER",
}
INSTRUMENT_EVENTS = EVENT_TYPES - {"FEE", "TAX", "DEPOSIT", "WITHDRAWAL", "FX_CONVERSION"}
EXTERNAL_DEFAULTS = {"DEPOSIT", "WITHDRAWAL"}


def preview_import(
    *, source: str, rows: list[dict[str, Any]], source_document: str | None = None
) -> dict[str, Any]:
    normalized: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    source_name = source.strip().lower()
    for index, raw in enumerate(rows, start=1):
        result = _normalize_row(raw, source=source_name, index=index)
        if result.get("transaction"):
            normalized.append(result["transaction"])
        else:
            unresolved.append(result["unresolved"])
    batch_id = f"batch_{int(time.time())}_{uuid.uuid4().hex[:10]}"
    preview = {"transactions": normalized, "unresolved": unresolved}
    transaction_ledger.save_preview(
        {
            "import_batch_id": batch_id,
            "source": source_name,
            "source_document": source_document,
        },
        preview,
    )
    return {
        "import_batch_id": batch_id,
        "status": "PREVIEW",
        "source": source_name,
        "rows_received": len(rows),
        "valid_count": len(normalized),
        "unresolved_count": len(unresolved),
        **preview,
    }


def commit_import(import_batch_id: str) -> dict[str, Any]:
    return transaction_ledger.commit_batch(import_batch_id)


def rollback_import(import_batch_id: str) -> dict[str, Any]:
    return transaction_ledger.rollback_batch(import_batch_id)


def _normalize_row(raw: dict[str, Any], *, source: str, index: int) -> dict[str, Any]:
    row = {str(key).strip(): value for key, value in raw.items()}
    source_record_id = str(row.get("source_record_id") or f"row-{index}").strip()
    source_hash = _row_hash(source, source_record_id, row)
    event_type = str(row.get("event_type") or "").strip().upper()
    if event_type not in EVENT_TYPES:
        return _unresolved(source_record_id, source_hash, "UNKNOWN_EVENT_TYPE", "A valid event_type is required.", row)
    trade_date = str(row.get("trade_date") or "").strip()
    try:
        date.fromisoformat(trade_date)
    except ValueError:
        return _unresolved(source_record_id, source_hash, "INVALID_TRADE_DATE", "trade_date must be YYYY-MM-DD.", row)
    account_id = str(row.get("account_id") or "").strip()
    if not account_id:
        return _unresolved(source_record_id, source_hash, "MISSING_ACCOUNT", "account_id is required.", row)

    instrument_id = str(row.get("instrument_id") or "").strip() or None
    if event_type in INSTRUMENT_EVENTS and not instrument_id:
        identity = resolve_holding(row)
        if not identity.get("resolved"):
            return _unresolved(
                source_record_id, source_hash, "UNRESOLVED_INSTRUMENT",
                str(identity.get("reason") or "Instrument identity could not be resolved."), row,
            )
        instrument_id = identity["instrument_id"]

    quantity = _number(row.get("quantity"))
    price = _number(row.get("price"))
    gross = _number(row.get("gross_amount"), default=abs(quantity * price))
    fees = abs(_number(row.get("fees")))
    taxes = abs(_number(row.get("taxes")))
    explicit_flow = row.get("net_cash_flow")
    flow = _number(explicit_flow) if explicit_flow not in (None, "") else _cash_flow(event_type, gross, fees, taxes)
    source_as_of = str(row.get("source_as_of") or trade_date)
    canonical = {
        "transaction_id": "txn_" + hashlib.sha256(f"{source}:{source_hash}".encode()).hexdigest()[:24],
        "source_record_id": source_record_id,
        "source_row_hash": source_hash,
        "account_id": account_id,
        "instrument_id": instrument_id,
        "event_type": event_type,
        "trade_date": trade_date,
        "settlement_date": row.get("settlement_date") or None,
        "quantity": quantity,
        "price": price,
        "gross_amount": gross,
        "fees": fees,
        "taxes": taxes,
        "net_cash_flow": flow,
        "currency": str(row.get("currency") or "INR").upper(),
        "fx_rate_to_reporting_currency": _number(row.get("fx_rate_to_reporting_currency"), default=1),
        "external_cash_flow": bool(row.get("external_cash_flow", event_type in EXTERNAL_DEFAULTS)),
        "source": source,
        "source_as_of": source_as_of,
        "metadata": dict(row.get("metadata") or {}),
    }
    return {"transaction": canonical}


def _row_hash(source: str, source_record_id: str, row: dict[str, Any]) -> str:
    payload = json.dumps(row, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(f"{source}|{source_record_id}|{payload}".encode()).hexdigest()


def _cash_flow(event: str, gross: float, fees: float, taxes: float) -> float:
    if event in {"BUY", "SUBSCRIPTION", "RIGHTS"}:
        return -(gross + fees + taxes)
    if event in {"SELL", "REDEMPTION", "DIVIDEND", "INTEREST", "TRANSFER_IN", "DEPOSIT"}:
        return gross - fees - taxes
    if event in {"FEE", "TAX"}:
        return -(gross or fees or taxes)
    if event in {"WITHDRAWAL", "TRANSFER_OUT"}:
        return -gross
    return 0.0


def _number(value: Any, *, default: float = 0.0) -> float:
    try:
        return float(value) if value not in (None, "") else float(default)
    except (TypeError, ValueError):
        return float(default)


def _unresolved(
    source_record_id: str, source_hash: str, code: str, reason: str, row: dict[str, Any]
) -> dict[str, Any]:
    return {
        "unresolved": {
            "source_record_id": source_record_id,
            "source_row_hash": source_hash,
            "reason_code": code,
            "reason": reason,
            "row": row,
        }
    }
