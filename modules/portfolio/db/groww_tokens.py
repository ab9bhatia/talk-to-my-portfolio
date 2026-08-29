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
        columns = {row[1] for row in conn.execute("PRAGMA table_info(groww_tokens)")}
        if "secret_ref" not in columns:
            conn.execute("ALTER TABLE groww_tokens ADD COLUMN secret_ref TEXT")
        if "secret_backend" not in columns:
            conn.execute("ALTER TABLE groww_tokens ADD COLUMN secret_backend TEXT")
        if "migrated_at" not in columns:
            conn.execute("ALTER TABLE groww_tokens ADD COLUMN migrated_at TEXT")
    try:
        DB_PATH.chmod(0o600)
    except OSError:
        pass


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
    if row["access_token"] == "[MIGRATED_TO_SECRET_STORE]":
        with _connect() as conn:
            ref = conn.execute(
                "SELECT secret_ref FROM groww_tokens WHERE account_id = ?", (account_id,)
            ).fetchone()
        if ref and ref["secret_ref"]:
            from modules.portfolio.services.secret_storage import get_secret_backend

            return get_secret_backend().get(ref["secret_ref"])
        return None
    return row["access_token"]


def save_token(account_id: str, access_token: str, *, auth_method: str) -> None:
    init_db()
    now = datetime.now(IST).isoformat()
    with _connect() as conn:
        existing = conn.execute(
            "SELECT secret_ref, secret_backend FROM groww_tokens WHERE account_id = ?",
            (account_id,),
        ).fetchone()
    stored_token = access_token
    secret_ref = existing["secret_ref"] if existing else None
    secret_backend = existing["secret_backend"] if existing else None
    migrated_at = None
    if secret_ref:
        from modules.portfolio.services.secret_storage import MIGRATED_SENTINEL, get_secret_backend

        backend = get_secret_backend()
        backend.set(secret_ref, access_token)
        if backend.get(secret_ref) != access_token:
            raise RuntimeError("Token rotation verification failed; existing token metadata was retained.")
        stored_token = MIGRATED_SENTINEL
        secret_backend = backend.name
        migrated_at = now
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO groww_tokens (
                account_id, access_token, auth_method, updated_at,
                secret_ref, secret_backend, migrated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_id) DO UPDATE SET
                access_token = excluded.access_token,
                auth_method = excluded.auth_method,
                updated_at = excluded.updated_at,
                secret_ref = excluded.secret_ref,
                secret_backend = excluded.secret_backend,
                migrated_at = excluded.migrated_at
            """,
            (
                account_id, stored_token, auth_method, now,
                secret_ref, secret_backend, migrated_at,
            ),
        )


def delete_token(account_id: str) -> None:
    init_db()
    with _connect() as conn:
        row = conn.execute("SELECT secret_ref FROM groww_tokens WHERE account_id = ?", (account_id,)).fetchone()
    if row and row["secret_ref"]:
        from modules.portfolio.services.secret_storage import get_secret_backend

        get_secret_backend().delete(row["secret_ref"])
    with _connect() as conn:
        conn.execute("DELETE FROM groww_tokens WHERE account_id = ?", (account_id,))
