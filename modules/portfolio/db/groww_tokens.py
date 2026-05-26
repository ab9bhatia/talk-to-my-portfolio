"""SQLite cache for Groww API access tokens (reset daily ~8 AM IST)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from modules.portfolio.paths import DATA_DIR

IST = ZoneInfo("Asia/Kolkata")
DB_PATH = DATA_DIR / "groww_tokens.db"


def _connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS groww_tokens (
                account_id   TEXT PRIMARY KEY,
                access_token TEXT NOT NULL,
                auth_method  TEXT NOT NULL,
                updated_at   TEXT NOT NULL
            )
            """
        )


def _token_stale_after_ist() -> datetime:
    """Tokens reset around 8 AM IST — invalidate cache from that moment onward."""
    now = datetime.now(IST)
    reset_today = datetime.combine(now.date(), time(8, 0), tzinfo=IST)
    if now >= reset_today:
        return reset_today
    return reset_today - timedelta(days=1)


def get_cached_token(account_id: str) -> str | None:
    """Return cached token if still valid for today's Groww session."""
    init_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT access_token, updated_at FROM groww_tokens WHERE account_id = ?",
            (account_id,),
        ).fetchone()
    if not row:
        return None

    try:
        updated = datetime.fromisoformat(row["updated_at"])
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=IST)
    except ValueError:
        return None

    if updated < _token_stale_after_ist():
        delete_token(account_id)
        return None
    return row["access_token"]


def save_token(account_id: str, access_token: str, *, auth_method: str) -> None:
    init_db()
    now = datetime.now(IST).isoformat()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO groww_tokens (account_id, access_token, auth_method, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(account_id) DO UPDATE SET
                access_token = excluded.access_token,
                auth_method = excluded.auth_method,
                updated_at = excluded.updated_at
            """,
            (account_id, access_token, auth_method, now),
        )


def delete_token(account_id: str) -> None:
    init_db()
    with _connect() as conn:
        conn.execute("DELETE FROM groww_tokens WHERE account_id = ?", (account_id,))
