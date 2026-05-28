"""Persist portfolio goals and guardrails."""

from __future__ import annotations

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
            CREATE TABLE IF NOT EXISTS profile_goals (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                updated_at REAL NOT NULL,
                target_return_pct REAL NOT NULL DEFAULT 15,
                max_position_pct REAL NOT NULL DEFAULT 12,
                max_sector_pct REAL NOT NULL DEFAULT 30,
                cash_buffer_pct REAL NOT NULL DEFAULT 5,
                risk_profile TEXT NOT NULL DEFAULT 'moderate'
            );
            INSERT INTO profile_goals (id, updated_at)
            VALUES (1, strftime('%s','now'))
            ON CONFLICT(id) DO NOTHING;
            """
        )


def get_goals() -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM profile_goals WHERE id = 1").fetchone()
    if not row:
        init_db()
        return get_goals()
    return dict(row)


def save_goals(
    *,
    target_return_pct: float,
    max_position_pct: float,
    max_sector_pct: float,
    cash_buffer_pct: float,
    risk_profile: str,
) -> dict[str, Any]:
    updated_at = time.time()
    with connect() as conn:
        conn.execute(
            """
            UPDATE profile_goals
            SET updated_at = ?,
                target_return_pct = ?,
                max_position_pct = ?,
                max_sector_pct = ?,
                cash_buffer_pct = ?,
                risk_profile = ?
            WHERE id = 1
            """,
            (
                updated_at,
                float(target_return_pct),
                float(max_position_pct),
                float(max_sector_pct),
                float(cash_buffer_pct),
                (risk_profile or "moderate").strip().lower(),
            ),
        )
    return get_goals()
