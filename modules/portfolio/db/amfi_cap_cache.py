"""SQLite cache for AMFI / SEBI market-cap stock lists (symbol → Large/Mid/Small)."""

from __future__ import annotations

import sqlite3
import time

from modules.portfolio.paths import DATA_DIR

DB_PATH = DATA_DIR / "amfi_cap_cache.db"


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS amfi_stocks (
                isin            TEXT PRIMARY KEY,
                symbol          TEXT NOT NULL,
                bucket          TEXT NOT NULL,
                avg_mcap_cr     REAL,
                list_rank       INTEGER NOT NULL,
                source_period   TEXT NOT NULL DEFAULT '',
                updated_at      REAL NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_amfi_stocks_symbol ON amfi_stocks(symbol)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS amfi_meta (
                key         TEXT PRIMARY KEY,
                value       TEXT NOT NULL,
                updated_at  REAL NOT NULL
            )
            """
        )


def replace_list(
    rows: list[tuple[str, str, str, float | None, int]],
    *,
    source_period: str,
) -> None:
    """Replace full AMFI list. Each row: (isin, symbol, bucket, avg_mcap_cr, rank)."""
    init_db()
    now = time.time()
    with connect() as conn:
        conn.execute("DELETE FROM amfi_stocks")
        conn.executemany(
            """
            INSERT INTO amfi_stocks (isin, symbol, bucket, avg_mcap_cr, list_rank, source_period, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (isin, symbol, bucket, avg_cr, rank, source_period, now)
                for isin, symbol, bucket, avg_cr, rank in rows
            ],
        )
        conn.execute(
            """
            INSERT INTO amfi_meta (key, value, updated_at) VALUES ('source_period', ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (source_period, now),
        )


def row_count() -> int:
    init_db()
    with connect() as conn:
        row = conn.execute("SELECT COUNT(*) AS c FROM amfi_stocks").fetchone()
    return int(row["c"]) if row else 0


def lookup_symbol(symbol: str) -> str | None:
    init_db()
    sym = symbol.strip().upper()
    with connect() as conn:
        row = conn.execute(
            "SELECT bucket FROM amfi_stocks WHERE symbol = ? ORDER BY list_rank LIMIT 1",
            (sym,),
        ).fetchone()
    return row["bucket"] if row else None


def lookup_isin(isin: str) -> str | None:
    init_db()
    code = isin.strip().upper()
    with connect() as conn:
        row = conn.execute(
            "SELECT bucket FROM amfi_stocks WHERE isin = ?",
            (code,),
        ).fetchone()
    return row["bucket"] if row else None


def rank_cutoffs() -> tuple[float | None, float | None]:
    """AMFI rank 100 and 250 average market caps (₹ crore) for fallback classification."""
    init_db()
    with connect() as conn:
        r100 = conn.execute(
            "SELECT avg_mcap_cr FROM amfi_stocks WHERE list_rank = 100"
        ).fetchone()
        r250 = conn.execute(
            "SELECT avg_mcap_cr FROM amfi_stocks WHERE list_rank = 250"
        ).fetchone()
    return (
        float(r100["avg_mcap_cr"]) if r100 and r100["avg_mcap_cr"] else None,
        float(r250["avg_mcap_cr"]) if r250 and r250["avg_mcap_cr"] else None,
    )


def source_period() -> str | None:
    init_db()
    with connect() as conn:
        row = conn.execute(
            "SELECT value FROM amfi_meta WHERE key = 'source_period'"
        ).fetchone()
    return row["value"] if row else None
