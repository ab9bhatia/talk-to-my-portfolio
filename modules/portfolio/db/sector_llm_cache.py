"""Persistent cache for LLM-assigned sector labels."""

from __future__ import annotations

import sqlite3
import time

from modules.portfolio.paths import DATA_DIR

DB_PATH = DATA_DIR / "sector_llm_cache.db"


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sector_labels (
                symbol      TEXT NOT NULL,
                exchange    TEXT NOT NULL DEFAULT 'NSE',
                sector      TEXT NOT NULL,
                source      TEXT NOT NULL DEFAULT 'llm',
                updated_at  REAL NOT NULL,
                PRIMARY KEY (symbol, exchange)
            )
            """
        )


def cache_key(symbol: str, exchange: str | None) -> tuple[str, str]:
    return (symbol.strip().upper(), (exchange or "NSE").strip().upper())


def get_sector(symbol: str, exchange: str | None) -> str | None:
    init_db()
    sym, ex = cache_key(symbol, exchange)
    with connect() as conn:
        row = conn.execute(
            "SELECT sector FROM sector_labels WHERE symbol = ? AND exchange = ?",
            (sym, ex),
        ).fetchone()
    return row["sector"] if row else None


def get_many(keys: list[tuple[str, str | None]]) -> dict[tuple[str, str], str]:
    if not keys:
        return {}
    init_db()
    normalized = [cache_key(s, e) for s, e in keys]
    out: dict[tuple[str, str], str] = {}
    with connect() as conn:
        chunk_size = 400
        for offset in range(0, len(normalized), chunk_size):
            chunk = normalized[offset : offset + chunk_size]
            placeholders = ",".join("(?,?)" for _ in chunk)
            params: list[str] = [part for pair in chunk for part in pair]
            rows = conn.execute(
                f"""
                SELECT symbol, exchange, sector FROM sector_labels
                WHERE (symbol, exchange) IN ({placeholders})
                """,
                params,
            ).fetchall()
            for row in rows:
                out[(row["symbol"], row["exchange"])] = row["sector"]
    return out


def put_sectors(rows: dict[tuple[str, str], str], *, source: str = "llm") -> None:
    if not rows:
        return
    init_db()
    now = time.time()
    with connect() as conn:
        conn.executemany(
            """
            INSERT INTO sector_labels (symbol, exchange, sector, source, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(symbol, exchange) DO UPDATE SET
                sector = excluded.sector,
                source = excluded.source,
                updated_at = excluded.updated_at
            """,
            [(sym, ex, sector, source, now) for (sym, ex), sector in rows.items()],
        )
