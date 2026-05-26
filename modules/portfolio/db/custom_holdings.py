"""SQLite storage for custom (manual / imported) portfolio holdings."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from modules.portfolio.paths import DATA_DIR

IST = ZoneInfo("Asia/Kolkata")
DB_PATH = DATA_DIR / "custom_holdings.db"


def _connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS holdings (
                account_id   TEXT NOT NULL,
                symbol       TEXT NOT NULL,
                exchange     TEXT NOT NULL DEFAULT 'NSE',
                quantity     REAL NOT NULL,
                avg_price    REAL,
                last_price   REAL,
                extra_json   TEXT,
                updated_at   TEXT NOT NULL,
                PRIMARY KEY (account_id, symbol, exchange)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS meta (
                account_id TEXT PRIMARY KEY,
                source     TEXT,
                notes      TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )


def replace_holdings(
    account_id: str,
    rows: list[dict[str, Any]],
    *,
    source: str = "import",
    notes: str | None = None,
) -> int:
    """Replace all holdings for an account."""
    now = datetime.now(IST).isoformat()
    init_db()
    with _connect() as conn:
        conn.execute("DELETE FROM holdings WHERE account_id = ?", (account_id,))
        for row in rows:
            extra = {k: v for k, v in row.items() if k not in {
                "symbol", "exchange", "quantity", "avg_price", "last_price",
            }}
            conn.execute(
                """
                INSERT INTO holdings (
                    account_id, symbol, exchange, quantity, avg_price, last_price,
                    extra_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    account_id,
                    str(row["symbol"]).upper().strip(),
                    str(row.get("exchange") or "NSE").upper().strip(),
                    float(row["quantity"]),
                    float(row["avg_price"]) if row.get("avg_price") is not None else None,
                    float(row["last_price"]) if row.get("last_price") is not None else None,
                    json.dumps(extra) if extra else None,
                    now,
                ),
            )
        conn.execute(
            """
            INSERT INTO meta (account_id, source, notes, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(account_id) DO UPDATE SET
                source = excluded.source,
                notes = excluded.notes,
                updated_at = excluded.updated_at
            """,
            (account_id, source, notes, now),
        )
    return len(rows)


def list_holdings(account_id: str) -> list[dict[str, Any]]:
    init_db()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM holdings WHERE account_id = ? ORDER BY symbol",
            (account_id,),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        extra: dict[str, Any] = {}
        if row["extra_json"]:
            try:
                extra = json.loads(row["extra_json"])
            except json.JSONDecodeError:
                extra = {}
        qty = float(row["quantity"])
        avg = float(row["avg_price"] or 0)
        ltp = float(row["last_price"] or avg or 0)
        invested = qty * avg if avg else 0
        current = qty * ltp if ltp else invested
        pnl = current - invested
        pnl_pct = (pnl / invested * 100) if invested else 0.0
        out.append({
            "symbol": row["symbol"],
            "exchange": row["exchange"],
            "quantity": qty,
            "avg_price": avg,
            "last_price": ltp,
            "invested": invested,
            "current_value": current,
            "pnl": pnl,
            "pnl_pct": pnl_pct,
            **extra,
        })
    return out


def has_holdings(account_id: str) -> bool:
    init_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM holdings WHERE account_id = ? LIMIT 1",
            (account_id,),
        ).fetchone()
    return row is not None
