"""SQLite cache for dated, source-attributed advisory evidence."""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from modules.portfolio.paths import DATA_DIR


DB_PATH = DATA_DIR / "advisory_evidence.db"


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS advisory_evidence (
                symbol TEXT NOT NULL,
                exchange TEXT NOT NULL,
                field TEXT NOT NULL,
                value_json TEXT NOT NULL,
                source TEXT NOT NULL,
                source_url TEXT,
                source_type TEXT NOT NULL,
                as_of TEXT NOT NULL,
                fetched_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                provider TEXT NOT NULL,
                authoritative INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (symbol, exchange, field, provider)
            );
            CREATE INDEX IF NOT EXISTS idx_advisory_evidence_expiry
                ON advisory_evidence(expires_at);
            """
        )


def upsert(record: dict[str, Any]) -> None:
    init_db()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO advisory_evidence (
                symbol, exchange, field, value_json, source, source_url,
                source_type, as_of, fetched_at, expires_at, provider, authoritative
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, exchange, field, provider) DO UPDATE SET
                value_json = excluded.value_json,
                source = excluded.source,
                source_url = excluded.source_url,
                source_type = excluded.source_type,
                as_of = excluded.as_of,
                fetched_at = excluded.fetched_at,
                expires_at = excluded.expires_at,
                authoritative = excluded.authoritative
            """,
            (
                str(record["symbol"]).upper(),
                str(record.get("exchange") or "UNKNOWN").upper(),
                str(record["field"]),
                json.dumps(record.get("value"), default=str),
                str(record["source"]),
                record.get("source_url"),
                str(record["source_type"]),
                str(record["as_of"]),
                float(record.get("fetched_at") or time.time()),
                float(record["expires_at"]),
                str(record.get("provider") or "local"),
                int(bool(record.get("authoritative"))),
            ),
        )


def list_for_security(symbol: str, exchange: str | None = None) -> list[dict[str, Any]]:
    init_db()
    query = "SELECT * FROM advisory_evidence WHERE symbol = ?"
    params: list[Any] = [symbol.upper()]
    if exchange:
        query += " AND exchange IN (?, 'UNKNOWN')"
        params.append(exchange.upper())
    query += " ORDER BY field, authoritative DESC, fetched_at DESC"
    with connect() as conn:
        rows = conn.execute(query, params).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        try:
            item["value"] = json.loads(item.pop("value_json"))
        except json.JSONDecodeError:
            item["value"] = None
            item.pop("value_json", None)
        item["authoritative"] = bool(item["authoritative"])
        out.append(item)
    return out


def status(*, now: float | None = None) -> dict[str, Any]:
    init_db()
    current = float(now or time.time())
    with connect() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN expires_at >= ? THEN 1 ELSE 0 END) AS fresh,
                   SUM(CASE WHEN expires_at < ? THEN 1 ELSE 0 END) AS stale,
                   SUM(CASE WHEN authoritative = 1 THEN 1 ELSE 0 END) AS authoritative,
                   MAX(fetched_at) AS last_fetched_at
            FROM advisory_evidence
            """,
            (current, current),
        ).fetchone()
    return {
        "total": int(row["total"] or 0),
        "fresh": int(row["fresh"] or 0),
        "stale": int(row["stale"] or 0),
        "authoritative": int(row["authoritative"] or 0),
        "last_fetched_at": row["last_fetched_at"],
    }


def delete_provider_source_types(provider: str, source_types: set[str]) -> int:
    """Delete only a provider's explicitly selected source tiers."""
    if not source_types:
        return 0
    init_db()
    placeholders = ",".join("?" for _ in source_types)
    params: list[Any] = [provider, *sorted(source_types)]
    with connect() as conn:
        cursor = conn.execute(
            f"DELETE FROM advisory_evidence WHERE provider = ? "
            f"AND source_type IN ({placeholders})",
            params,
        )
    return int(cursor.rowcount or 0)
