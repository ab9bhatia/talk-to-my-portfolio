"""Track import quality signals for setup and historical backfills."""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from modules.portfolio.paths import DATA_DIR

DB_PATH = DATA_DIR / "portfolio_profile.db"


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS import_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at REAL NOT NULL,
                source TEXT NOT NULL,
                broker TEXT,
                account_id TEXT,
                imported_count INTEGER NOT NULL DEFAULT 0,
                unresolved_codes_json TEXT,
                notes TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_import_audit_created_at
              ON import_audit(created_at DESC);
            """
        )


def log_event(
    *,
    source: str,
    broker: str | None,
    account_id: str | None,
    imported_count: int,
    unresolved_codes: list[str] | None = None,
    notes: str | None = None,
) -> None:
    unresolved_codes = unresolved_codes or []
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO import_audit (
                created_at, source, broker, account_id, imported_count, unresolved_codes_json, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                time.time(),
                source,
                broker,
                account_id,
                int(imported_count or 0),
                json.dumps(sorted(set(unresolved_codes))),
                notes,
            ),
        )


def latest(limit: int = 20) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM import_audit
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        try:
            d["unresolved_codes"] = json.loads(d.get("unresolved_codes_json") or "[]")
        except json.JSONDecodeError:
            d["unresolved_codes"] = []
        out.append(d)
    return out
