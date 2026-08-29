"""Append-only Market Regime & Mood Index observations."""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from modules.portfolio.paths import DATA_DIR


DB_PATH = DATA_DIR / "market_regime.db"
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
            CREATE TABLE IF NOT EXISTS market_regime_observations (
                observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                schema_version INTEGER NOT NULL,
                market TEXT NOT NULL,
                score REAL NOT NULL,
                band TEXT NOT NULL,
                regime TEXT NOT NULL,
                trend TEXT NOT NULL,
                confidence INTEGER NOT NULL,
                as_of TEXT NOT NULL,
                methodology_version TEXT NOT NULL,
                observation_state TEXT NOT NULL,
                component_coverage_pct REAL NOT NULL DEFAULT 0,
                interpretation TEXT NOT NULL DEFAULT '',
                not_a_forecast INTEGER NOT NULL DEFAULT 1,
                components_json TEXT NOT NULL,
                data_quality_flags_json TEXT NOT NULL,
                created_at REAL NOT NULL,
                UNIQUE(market, as_of, methodology_version)
            );
            CREATE INDEX IF NOT EXISTS idx_market_regime_history
                ON market_regime_observations(market, as_of DESC, observation_id DESC);
            """
        )
        columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(market_regime_observations)")
        }
        for name, definition in (
            ("component_coverage_pct", "REAL NOT NULL DEFAULT 0"),
            ("interpretation", "TEXT NOT NULL DEFAULT ''"),
            ("not_a_forecast", "INTEGER NOT NULL DEFAULT 1"),
        ):
            if name not in columns:
                conn.execute(f"ALTER TABLE market_regime_observations ADD COLUMN {name} {definition}")


def save_observation(observation: dict[str, Any]) -> dict[str, Any]:
    init_db()
    with connect() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO market_regime_observations (
                schema_version, market, score, band, regime, trend, confidence,
                as_of, methodology_version, observation_state, component_coverage_pct,
                interpretation, not_a_forecast, components_json,
                data_quality_flags_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                SCHEMA_VERSION,
                observation["market"],
                observation["score"],
                observation["band"],
                observation["regime"],
                observation["trend"],
                observation["confidence"],
                observation["as_of"],
                observation["methodology_version"],
                observation.get("observation_state") or "PROVISIONAL",
                observation.get("component_coverage_pct") or 0,
                observation.get("interpretation") or "",
                int(bool(observation.get("not_a_forecast", True))),
                json.dumps(observation.get("components") or [], sort_keys=True),
                json.dumps(observation.get("data_quality_flags") or [], sort_keys=True),
                time.time(),
            ),
        )
    return get_observation(
        market=observation["market"],
        as_of=observation["as_of"],
        methodology_version=observation["methodology_version"],
    ) or observation


def get_observation(*, market: str, as_of: str, methodology_version: str) -> dict[str, Any] | None:
    init_db()
    with connect() as conn:
        row = conn.execute(
            """
            SELECT * FROM market_regime_observations
            WHERE market = ? AND as_of = ? AND methodology_version = ?
            """,
            (market, as_of, methodology_version),
        ).fetchone()
    return _decode(row) if row else None


def latest(*, market: str = "INDIA", finalized_only: bool = False) -> dict[str, Any] | None:
    init_db()
    clause = "AND observation_state = 'FINALIZED'" if finalized_only else ""
    with connect() as conn:
        row = conn.execute(
            f"""
            SELECT * FROM market_regime_observations
            WHERE market = ? {clause}
            ORDER BY as_of DESC, observation_id DESC LIMIT 1
            """,
            (market,),
        ).fetchone()
    return _decode(row) if row else None


def history(*, market: str = "INDIA", limit: int = 365) -> list[dict[str, Any]]:
    init_db()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM market_regime_observations WHERE market = ?
            ORDER BY as_of DESC, observation_id DESC LIMIT ?
            """,
            (market, max(1, min(limit, 5000))),
        ).fetchall()
    return [_decode(row) for row in reversed(rows)]


def _decode(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["not_a_forecast"] = bool(item["not_a_forecast"])
    item["components"] = json.loads(item.pop("components_json") or "[]")
    item["data_quality_flags"] = json.loads(item.pop("data_quality_flags_json") or "[]")
    return item
