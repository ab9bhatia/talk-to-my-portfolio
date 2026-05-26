"""Persistent SQLite cache for portfolio snapshots (LTM-style data layer)."""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from modules.portfolio.paths import DATA_DIR

DB_PATH = DATA_DIR / "portfolio_cache.db"


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS portfolio_snapshots (
                cache_key    TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL,
                cached_at    REAL NOT NULL,
                holdings_hash TEXT,
                source       TEXT NOT NULL DEFAULT 'live'
            );

            CREATE TABLE IF NOT EXISTS revalidate_jobs (
                cache_key    TEXT PRIMARY KEY,
                status       TEXT NOT NULL,
                started_at   REAL,
                finished_at  REAL,
                error        TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_snapshots_cached_at
                ON portfolio_snapshots(cached_at);

            CREATE TABLE IF NOT EXISTS agent_threads (
                thread_id    TEXT PRIMARY KEY,
                context_json TEXT NOT NULL,
                created_at   REAL NOT NULL,
                updated_at   REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS agent_messages (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id  TEXT NOT NULL,
                role       TEXT NOT NULL,
                content    TEXT NOT NULL,
                created_at REAL NOT NULL,
                FOREIGN KEY (thread_id) REFERENCES agent_threads(thread_id)
            );

            CREATE INDEX IF NOT EXISTS idx_agent_messages_thread
                ON agent_messages(thread_id, created_at);
            """
        )


def get_snapshot(cache_key: str) -> tuple[float, dict[str, Any]] | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT cached_at, payload_json FROM portfolio_snapshots WHERE cache_key = ?",
            (cache_key,),
        ).fetchone()
    if not row:
        return None
    try:
        payload = json.loads(row["payload_json"])
    except json.JSONDecodeError:
        return None
    return float(row["cached_at"]), payload


def put_snapshot(
    cache_key: str,
    payload: dict[str, Any],
    *,
    cached_at: float | None = None,
    holdings_hash: str | None = None,
    source: str = "live",
) -> float:
    ts = cached_at if cached_at is not None else time.time()
    blob = json.dumps(payload, default=str)
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO portfolio_snapshots (cache_key, payload_json, cached_at, holdings_hash, source)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(cache_key) DO UPDATE SET
                payload_json = excluded.payload_json,
                cached_at = excluded.cached_at,
                holdings_hash = excluded.holdings_hash,
                source = excluded.source
            """,
            (cache_key, blob, ts, holdings_hash, source),
        )
    return ts


def delete_snapshot(cache_key: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM portfolio_snapshots WHERE cache_key = ?", (cache_key,))


def get_revalidate_status(cache_key: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT status, started_at, finished_at, error FROM revalidate_jobs WHERE cache_key = ?",
            (cache_key,),
        ).fetchone()
    return dict(row) if row else None


def set_revalidate_status(
    cache_key: str,
    *,
    status: str,
    started_at: float | None = None,
    finished_at: float | None = None,
    error: str | None = None,
) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO revalidate_jobs (cache_key, status, started_at, finished_at, error)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(cache_key) DO UPDATE SET
                status = excluded.status,
                started_at = COALESCE(excluded.started_at, revalidate_jobs.started_at),
                finished_at = excluded.finished_at,
                error = excluded.error
            """,
            (cache_key, status, started_at, finished_at, error),
        )
