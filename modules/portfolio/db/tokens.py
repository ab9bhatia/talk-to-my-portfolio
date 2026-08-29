"""SQLite storage for Zerodha access tokens."""

from __future__ import annotations

import sqlite3
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from modules.portfolio.paths import DATA_DIR

IST = ZoneInfo("Asia/Kolkata")
DB_PATH = DATA_DIR / "tokens.db"


def _connect() -> sqlite3.Connection:
    """Open a SQLite connection with row dict access."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create the tokens table if it does not exist."""
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tokens (
                account_id   TEXT PRIMARY KEY,
                user_id      TEXT NOT NULL,
                access_token TEXT NOT NULL,
                api_key      TEXT,
                login_time   TEXT NOT NULL,
                updated_at   TEXT NOT NULL
            )
            """
        )
        columns = {row[1] for row in conn.execute("PRAGMA table_info(tokens)")}
        if "api_key" not in columns:
            conn.execute("ALTER TABLE tokens ADD COLUMN api_key TEXT")
        if "secret_ref" not in columns:
            conn.execute("ALTER TABLE tokens ADD COLUMN secret_ref TEXT")
        if "secret_backend" not in columns:
            conn.execute("ALTER TABLE tokens ADD COLUMN secret_backend TEXT")
        if "migrated_at" not in columns:
            conn.execute("ALTER TABLE tokens ADD COLUMN migrated_at TEXT")
    try:
        DB_PATH.chmod(0o600)
    except OSError:
        pass


def save_token(
    account_id: str,
    user_id: str,
    access_token: str,
    login_time: str,
    api_key: str | None = None,
) -> None:
    """Insert or update the access token for an account."""
    init_db()
    now = datetime.now(IST).isoformat()
    with _connect() as conn:
        existing = conn.execute(
            "SELECT secret_ref, secret_backend FROM tokens WHERE account_id = ?", (account_id,)
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
            INSERT INTO tokens (
                account_id, user_id, access_token, api_key, login_time, updated_at,
                secret_ref, secret_backend, migrated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_id) DO UPDATE SET
                user_id = excluded.user_id,
                access_token = excluded.access_token,
                api_key = excluded.api_key,
                login_time = excluded.login_time,
                updated_at = excluded.updated_at,
                secret_ref = excluded.secret_ref,
                secret_backend = excluded.secret_backend,
                migrated_at = excluded.migrated_at
            """,
            (
                account_id, user_id, stored_token, api_key, login_time, now,
                secret_ref, secret_backend, migrated_at,
            ),
        )


def get_token(account_id: str, *, include_secret: bool = True) -> dict | None:
    """Return stored token metadata for an account."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM tokens WHERE account_id = ?",
            (account_id,),
        ).fetchone()
    if not row:
        return None
    result = dict(row)
    if (
        include_secret
        and result.get("secret_ref")
        and result.get("access_token") == "[MIGRATED_TO_SECRET_STORE]"
    ):
        from modules.portfolio.services.secret_storage import get_secret_backend

        result["access_token"] = get_secret_backend().get(result["secret_ref"])
    return result


def delete_token(account_id: str) -> None:
    """Revoke local token material and its secret-store entry."""
    with _connect() as conn:
        token = conn.execute(
            "SELECT secret_ref FROM tokens WHERE account_id = ?", (account_id,)
        ).fetchone()
    if token and token["secret_ref"]:
        from modules.portfolio.services.secret_storage import get_secret_backend

        get_secret_backend().delete(token["secret_ref"])
    with _connect() as conn:
        conn.execute("DELETE FROM tokens WHERE account_id = ?", (account_id,))


def get_token_status(account_id: str) -> dict:
    """Return human-readable token status for an account."""
    token = get_token(account_id, include_secret=False)
    if token is None:
        return {
            "account_id": account_id,
            "connected": False,
            "needs_login": True,
            "login_time": None,
        }

    needs_login = token_needs_refresh(token["login_time"])
    return {
        "account_id": account_id,
        "connected": not needs_login,
        "needs_login": needs_login,
        "login_time": token["login_time"],
        "user_id": token["user_id"],
    }


def token_needs_refresh(login_time: str, now: datetime | None = None) -> bool:
    """Return True if the token is likely expired (6 AM IST next day)."""
    now = now or datetime.now(IST)

    try:
        parsed_login = datetime.fromisoformat(login_time)
    except ValueError:
        return True

    if parsed_login.tzinfo is None:
        parsed_login = parsed_login.replace(tzinfo=IST)
    else:
        parsed_login = parsed_login.astimezone(IST)

    expiry_date = parsed_login.date() + timedelta(days=1)
    expiry_at = datetime.combine(expiry_date, time(6, 0), tzinfo=IST)
    return now.astimezone(IST) >= expiry_at
