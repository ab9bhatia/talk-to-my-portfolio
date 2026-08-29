"""Versioned local transaction ledger and reversible import batches."""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from modules.portfolio.paths import DATA_DIR


DB_PATH = DATA_DIR / "transaction_ledger.db"
SCHEMA_VERSION = 1


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS transaction_import_batches (
                import_batch_id TEXT PRIMARY KEY,
                schema_version INTEGER NOT NULL,
                source TEXT NOT NULL,
                source_document TEXT,
                status TEXT NOT NULL,
                preview_json TEXT NOT NULL,
                row_count INTEGER NOT NULL,
                valid_count INTEGER NOT NULL,
                unresolved_count INTEGER NOT NULL,
                committed_count INTEGER NOT NULL DEFAULT 0,
                duplicate_count INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                committed_at REAL,
                rolled_back_at REAL
            );

            CREATE TABLE IF NOT EXISTS ledger_transactions (
                transaction_id TEXT PRIMARY KEY,
                schema_version INTEGER NOT NULL,
                source_record_id TEXT NOT NULL,
                source_row_hash TEXT NOT NULL UNIQUE,
                account_id TEXT NOT NULL,
                instrument_id TEXT,
                event_type TEXT NOT NULL,
                trade_date TEXT NOT NULL,
                settlement_date TEXT,
                quantity REAL NOT NULL DEFAULT 0,
                price REAL NOT NULL DEFAULT 0,
                gross_amount REAL NOT NULL DEFAULT 0,
                fees REAL NOT NULL DEFAULT 0,
                taxes REAL NOT NULL DEFAULT 0,
                net_cash_flow REAL NOT NULL DEFAULT 0,
                currency TEXT NOT NULL,
                fx_rate_to_reporting_currency REAL NOT NULL DEFAULT 1,
                external_cash_flow INTEGER NOT NULL DEFAULT 0,
                source TEXT NOT NULL,
                source_as_of TEXT NOT NULL,
                import_batch_id TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                FOREIGN KEY (import_batch_id) REFERENCES transaction_import_batches(import_batch_id)
            );
            CREATE INDEX IF NOT EXISTS idx_ledger_date ON ledger_transactions(trade_date, transaction_id);
            CREATE INDEX IF NOT EXISTS idx_ledger_account_instrument
                ON ledger_transactions(account_id, instrument_id, trade_date);

            CREATE TABLE IF NOT EXISTS unresolved_transactions (
                unresolved_id INTEGER PRIMARY KEY AUTOINCREMENT,
                import_batch_id TEXT NOT NULL,
                source_record_id TEXT,
                source_row_hash TEXT NOT NULL,
                reason_code TEXT NOT NULL,
                reason TEXT NOT NULL,
                row_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'OPEN',
                created_at REAL NOT NULL,
                FOREIGN KEY (import_batch_id) REFERENCES transaction_import_batches(import_batch_id)
            );
            """
        )


def save_preview(batch: dict[str, Any], preview: dict[str, Any]) -> dict[str, Any]:
    init_db()
    now = time.time()
    valid = preview.get("transactions") or []
    unresolved = preview.get("unresolved") or []
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO transaction_import_batches (
                import_batch_id, schema_version, source, source_document, status,
                preview_json, row_count, valid_count, unresolved_count, created_at
            ) VALUES (?, ?, ?, ?, 'PREVIEW', ?, ?, ?, ?, ?)
            """,
            (
                batch["import_batch_id"],
                SCHEMA_VERSION,
                batch["source"],
                batch.get("source_document"),
                json.dumps(preview, sort_keys=True),
                len(valid) + len(unresolved),
                len(valid),
                len(unresolved),
                now,
            ),
        )
    return get_batch(batch["import_batch_id"]) or batch


def get_batch(import_batch_id: str) -> dict[str, Any] | None:
    init_db()
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM transaction_import_batches WHERE import_batch_id = ?",
            (import_batch_id,),
        ).fetchone()
    if not row:
        return None
    result = dict(row)
    result["preview"] = json.loads(result.pop("preview_json") or "{}")
    return result


def commit_batch(import_batch_id: str) -> dict[str, Any]:
    batch = get_batch(import_batch_id)
    if batch is None:
        raise KeyError(import_batch_id)
    if batch["status"] == "ROLLED_BACK":
        raise ValueError("Rolled-back import batches cannot be recommitted.")
    if batch["status"] == "COMMITTED":
        return batch

    inserted = 0
    duplicates = 0
    preview = batch["preview"]
    with connect() as conn:
        for row in preview.get("transactions") or []:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO ledger_transactions (
                    transaction_id, schema_version, source_record_id, source_row_hash,
                    account_id, instrument_id, event_type, trade_date, settlement_date,
                    quantity, price, gross_amount, fees, taxes, net_cash_flow, currency,
                    fx_rate_to_reporting_currency, external_cash_flow, source, source_as_of,
                    import_batch_id, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["transaction_id"], SCHEMA_VERSION, row["source_record_id"],
                    row["source_row_hash"], row["account_id"], row.get("instrument_id"),
                    row["event_type"], row["trade_date"], row.get("settlement_date"),
                    row.get("quantity", 0), row.get("price", 0), row.get("gross_amount", 0),
                    row.get("fees", 0), row.get("taxes", 0), row.get("net_cash_flow", 0),
                    row.get("currency") or "INR", row.get("fx_rate_to_reporting_currency", 1),
                    int(bool(row.get("external_cash_flow"))), row["source"], row["source_as_of"],
                    import_batch_id, json.dumps(row.get("metadata") or {}, sort_keys=True), time.time(),
                ),
            )
            if cursor.rowcount:
                inserted += 1
            else:
                duplicates += 1
        for row in preview.get("unresolved") or []:
            conn.execute(
                """
                INSERT INTO unresolved_transactions (
                    import_batch_id, source_record_id, source_row_hash, reason_code,
                    reason, row_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    import_batch_id, row.get("source_record_id"), row["source_row_hash"],
                    row["reason_code"], row["reason"], json.dumps(row.get("row") or {}, sort_keys=True),
                    time.time(),
                ),
            )
        conn.execute(
            """
            UPDATE transaction_import_batches
            SET status = 'COMMITTED', committed_count = ?, duplicate_count = ?, committed_at = ?
            WHERE import_batch_id = ?
            """,
            (inserted, duplicates, time.time(), import_batch_id),
        )
    return get_batch(import_batch_id) or batch


def rollback_batch(import_batch_id: str) -> dict[str, Any]:
    batch = get_batch(import_batch_id)
    if batch is None:
        raise KeyError(import_batch_id)
    if batch["status"] == "ROLLED_BACK":
        return batch
    with connect() as conn:
        conn.execute("DELETE FROM ledger_transactions WHERE import_batch_id = ?", (import_batch_id,))
        conn.execute("DELETE FROM unresolved_transactions WHERE import_batch_id = ?", (import_batch_id,))
        conn.execute(
            """
            UPDATE transaction_import_batches
            SET status = 'ROLLED_BACK', rolled_back_at = ? WHERE import_batch_id = ?
            """,
            (time.time(), import_batch_id),
        )
    return get_batch(import_batch_id) or batch


def list_transactions(
    *, account_id: str | None = None, instrument_id: str | None = None, limit: int = 1000
) -> list[dict[str, Any]]:
    init_db()
    clauses: list[str] = []
    params: list[Any] = []
    if account_id:
        clauses.append("account_id = ?")
        params.append(account_id)
    if instrument_id:
        clauses.append("instrument_id = ?")
        params.append(instrument_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(max(1, min(limit, 10000)))
    with connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM ledger_transactions {where} ORDER BY trade_date, transaction_id LIMIT ?",
            params,
        ).fetchall()
    return [_decode_transaction(row) for row in rows]


def list_unresolved(*, status: str = "OPEN") -> list[dict[str, Any]]:
    init_db()
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM unresolved_transactions WHERE status = ? ORDER BY created_at, unresolved_id",
            (status,),
        ).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        item["row"] = json.loads(item.pop("row_json") or "{}")
        out.append(item)
    return out


def _decode_transaction(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["external_cash_flow"] = bool(item["external_cash_flow"])
    item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
    return item
