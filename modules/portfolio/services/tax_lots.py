"""Auditable FIFO lot accounting for planning; never a final tax filing claim."""

from __future__ import annotations

from collections import defaultdict
from typing import Any


BUY_EVENTS = {"BUY", "SUBSCRIPTION", "RIGHTS", "TRANSFER_IN"}
SELL_EVENTS = {"SELL", "REDEMPTION", "TRANSFER_OUT"}


def build_tax_lots(transactions: list[dict[str, Any]]) -> dict[str, Any]:
    books: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    disposals: list[dict[str, Any]] = []
    flags: set[str] = set()
    ordered = sorted(transactions, key=lambda row: (row.get("trade_date") or "", row.get("transaction_id") or ""))

    for row in ordered:
        account = str(row.get("account_id") or "")
        instrument = str(row.get("instrument_id") or "")
        if not account or not instrument:
            continue
        key = (account, instrument)
        event = str(row.get("event_type") or "").upper()
        quantity = abs(float(row.get("quantity") or 0))
        gross = abs(float(row.get("gross_amount") or 0))
        fees = abs(float(row.get("fees") or 0))
        taxes = abs(float(row.get("taxes") or 0))
        metadata = row.get("metadata") or {}

        if event in BUY_EVENTS and quantity > 0:
            history_known = event != "TRANSFER_IN" or bool(metadata.get("original_acquisition_date"))
            if not history_known:
                flags.add("LOT_HISTORY_INCOMPLETE")
            books[key].append(
                {
                    "lot_id": f"lot_{row.get('transaction_id')}",
                    "account_id": account,
                    "instrument_id": instrument,
                    "acquisition_date": metadata.get("original_acquisition_date") or row.get("trade_date"),
                    "original_quantity": quantity,
                    "remaining_quantity": quantity,
                    "cost_basis": gross + fees + taxes,
                    "remaining_cost_basis": gross + fees + taxes,
                    "currency": row.get("currency") or "INR",
                    "history_complete": history_known,
                    "source_transaction_id": row.get("transaction_id"),
                }
            )
            continue

        if event in SELL_EVENTS and quantity > 0:
            remaining = quantity
            cost_released = 0.0
            consumed: list[dict[str, Any]] = []
            for lot in books[key]:
                available = float(lot["remaining_quantity"])
                if available <= 0 or remaining <= 0:
                    continue
                used = min(available, remaining)
                unit_cost = float(lot["remaining_cost_basis"]) / available if available else 0
                released = unit_cost * used
                lot["remaining_quantity"] = round(available - used, 10)
                lot["remaining_cost_basis"] = round(float(lot["remaining_cost_basis"]) - released, 10)
                cost_released += released
                remaining -= used
                consumed.append({"lot_id": lot["lot_id"], "quantity": used, "cost_basis": released})
            if remaining > 1e-8:
                flags.add("LOT_HISTORY_INCOMPLETE")
            proceeds = gross - fees - taxes
            disposals.append(
                {
                    "transaction_id": row.get("transaction_id"),
                    "account_id": account,
                    "instrument_id": instrument,
                    "trade_date": row.get("trade_date"),
                    "quantity": quantity,
                    "matched_quantity": quantity - remaining,
                    "proceeds": proceeds,
                    "cost_basis": round(cost_released, 2),
                    "realized_gain": round(proceeds - cost_released, 2),
                    "consumed_lots": consumed,
                    "history_complete": remaining <= 1e-8,
                }
            )
            continue

        if event == "SPLIT":
            ratio = float(metadata.get("ratio_numerator") or row.get("quantity") or 0)
            denominator = float(metadata.get("ratio_denominator") or 1)
            if ratio <= 0 or denominator <= 0:
                flags.add("LOT_HISTORY_INCOMPLETE")
                continue
            factor = ratio / denominator
            for lot in books[key]:
                lot["original_quantity"] = round(float(lot["original_quantity"]) * factor, 10)
                lot["remaining_quantity"] = round(float(lot["remaining_quantity"]) * factor, 10)
            continue

        if event == "BONUS" and quantity > 0:
            books[key].append(
                {
                    "lot_id": f"lot_{row.get('transaction_id')}",
                    "account_id": account,
                    "instrument_id": instrument,
                    "acquisition_date": row.get("trade_date"),
                    "original_quantity": quantity,
                    "remaining_quantity": quantity,
                    "cost_basis": 0.0,
                    "remaining_cost_basis": 0.0,
                    "currency": row.get("currency") or "INR",
                    "history_complete": True,
                    "source_transaction_id": row.get("transaction_id"),
                }
            )
            continue

        if event == "DEMERGER" and metadata.get("cost_allocation_pct") is None:
            flags.add("CORPORATE_ACTION_COST_ALLOCATION_REQUIRED")
            flags.add("LOT_HISTORY_INCOMPLETE")

    lots = [lot for group in books.values() for lot in group]
    covered_qty = sum(float(lot["remaining_quantity"]) for lot in lots if lot["history_complete"])
    total_qty = sum(float(lot["remaining_quantity"]) for lot in lots)
    coverage = covered_qty / total_qty * 100 if total_qty else 0.0
    return {
        "lots": lots,
        "disposals": disposals,
        "lot_coverage_pct": round(coverage, 2),
        "state": "COMPLETE" if not flags else "LOT_HISTORY_INCOMPLETE",
        "data_quality_flags": sorted(flags),
        "disclaimer": "Planning estimate only; not a final tax liability or filing calculation.",
    }
