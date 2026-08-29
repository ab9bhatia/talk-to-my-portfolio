"""Durable, local-only cache for portfolio-wide chart-pattern scans."""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from modules.portfolio.paths import DATA_DIR


DB_PATH = DATA_DIR / "pattern_cache.db"
SCHEMA_VERSION = 1


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS pattern_scan_cache (
                universe_key TEXT PRIMARY KEY,
                schema_version INTEGER NOT NULL,
                detector_version TEXT NOT NULL,
                completed_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                results_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_pattern_scan_expiry
                ON pattern_scan_cache(expires_at);
            """
        )


def get_scan(
    universe_key: str,
    *,
    detector_version: str,
    now: float | None = None,
) -> dict[str, Any] | None:
    init_db()
    current = time.time() if now is None else now
    with connect() as conn:
        row = conn.execute(
            """
            SELECT * FROM pattern_scan_cache
            WHERE universe_key = ? AND detector_version = ? AND expires_at > ?
            """,
            (universe_key, detector_version, current),
        ).fetchone()
    if row is None:
        return None
    return {
        "universe_key": row["universe_key"],
        "completed_at": float(row["completed_at"]),
        "expires_at": float(row["expires_at"]),
        "results": json.loads(row["results_json"] or "[]"),
    }


def save_scan(
    universe_key: str,
    *,
    detector_version: str,
    completed_at: float,
    ttl_seconds: int,
    results: list[dict[str, Any]],
) -> None:
    init_db()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO pattern_scan_cache (
                universe_key, schema_version, detector_version,
                completed_at, expires_at, results_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(universe_key) DO UPDATE SET
                schema_version = excluded.schema_version,
                detector_version = excluded.detector_version,
                completed_at = excluded.completed_at,
                expires_at = excluded.expires_at,
                results_json = excluded.results_json
            """,
            (
                universe_key,
                SCHEMA_VERSION,
                detector_version,
                completed_at,
                completed_at + max(1, ttl_seconds),
                json.dumps(results, default=str, separators=(",", ":")),
            ),
        )
