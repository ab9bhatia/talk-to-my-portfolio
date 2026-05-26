"""Persistent cache for LLM buy thesis (B+ / Strong buy holdings)."""

from __future__ import annotations

import sqlite3
import time

from modules.portfolio.paths import DATA_DIR

DB_PATH = DATA_DIR / "buy_thesis_cache.db"


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS buy_theses (
                symbol      TEXT NOT NULL,
                exchange    TEXT NOT NULL DEFAULT 'NSE',
                thesis      TEXT NOT NULL,
                updated_at  REAL NOT NULL,
                PRIMARY KEY (symbol, exchange)
            )
            """
        )


def cache_key(symbol: str, exchange: str | None) -> tuple[str, str]:
    ex = (exchange or "NSE").strip().upper()
    if "NSE" in ex:
        ex = "NSE"
    elif "BSE" in ex:
        ex = "BSE"
    return (symbol.strip().upper(), ex)


def get_thesis(symbol: str, exchange: str | None) -> str | None:
    init_db()
    sym, ex = cache_key(symbol, exchange)
    with connect() as conn:
        row = conn.execute(
            "SELECT thesis FROM buy_theses WHERE symbol = ? AND exchange = ?",
            (sym, ex),
        ).fetchone()
    return row["thesis"] if row else None


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
                SELECT symbol, exchange, thesis FROM buy_theses
                WHERE (symbol, exchange) IN ({placeholders})
                """,
                params,
            ).fetchall()
            for row in rows:
                out[(row["symbol"], row["exchange"])] = row["thesis"]
    return out


def put_theses(rows: dict[tuple[str, str], str]) -> None:
    if not rows:
        return
    init_db()
    now = time.time()
    with connect() as conn:
        conn.executemany(
            """
            INSERT INTO buy_theses (symbol, exchange, thesis, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(symbol, exchange) DO UPDATE SET
                thesis = excluded.thesis,
                updated_at = excluded.updated_at
            """,
            [(sym, ex, thesis, now) for (sym, ex), thesis in rows.items()],
        )
