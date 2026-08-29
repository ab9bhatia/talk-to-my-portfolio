"""Saved stress scenarios and hysteresis/cooldown alert state."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from typing import Any

from modules.portfolio.paths import DATA_DIR


DB_PATH = DATA_DIR / "operating_console.db"
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
            CREATE TABLE IF NOT EXISTS stress_scenarios (
                scenario_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                methodology_version TEXT NOT NULL,
                assumptions_json TEXT NOT NULL,
                source TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS alert_state (
                alert_key TEXT PRIMARY KEY,
                alert_type TEXT NOT NULL,
                state_hash TEXT NOT NULL,
                last_fired_at REAL NOT NULL,
                cooldown_until REAL NOT NULL,
                fire_count INTEGER NOT NULL,
                payload_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS alert_history (
                alert_id TEXT PRIMARY KEY,
                alert_key TEXT NOT NULL,
                alert_type TEXT NOT NULL,
                priority TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                fired_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS provider_health_events (
                event_id TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                operation TEXT NOT NULL,
                duration_ms REAL NOT NULL,
                status TEXT NOT NULL,
                error_code TEXT,
                recorded_at REAL NOT NULL
            );
            """
        )


def save_scenario(*, name: str, assumptions: dict[str, Any], source: str = "user") -> dict[str, Any]:
    init_db()
    scenario_id = f"scenario_{uuid.uuid4().hex[:16]}"
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO stress_scenarios (
                scenario_id, name, methodology_version, assumptions_json, source, created_at
            ) VALUES (?, ?, 'stress-v1', ?, ?, ?)
            """,
            (scenario_id, name, json.dumps(assumptions, sort_keys=True), source, time.time()),
        )
    return get_scenario(scenario_id) or {"scenario_id": scenario_id, "name": name, "assumptions": assumptions}


def get_scenario(scenario_id: str) -> dict[str, Any] | None:
    init_db()
    with connect() as conn:
        row = conn.execute("SELECT * FROM stress_scenarios WHERE scenario_id = ?", (scenario_id,)).fetchone()
    if not row:
        return None
    item = dict(row)
    item["assumptions"] = json.loads(item.pop("assumptions_json") or "{}")
    return item


def list_scenarios() -> list[dict[str, Any]]:
    init_db()
    with connect() as conn:
        rows = conn.execute("SELECT scenario_id FROM stress_scenarios ORDER BY created_at DESC").fetchall()
    return [item for row in rows if (item := get_scenario(row["scenario_id"]))]


def should_fire_alert(
    *, alert_key: str, alert_type: str, state_hash: str, payload: dict[str, Any],
    fired_at: float, cooldown_seconds: int,
) -> bool:
    init_db()
    with connect() as conn:
        previous = conn.execute("SELECT * FROM alert_state WHERE alert_key = ?", (alert_key,)).fetchone()
        if previous and previous["state_hash"] == state_hash and fired_at < float(previous["cooldown_until"]):
            return False
        fire_count = int(previous["fire_count"]) + 1 if previous else 1
        conn.execute(
            """
            INSERT INTO alert_state (
                alert_key, alert_type, state_hash, last_fired_at, cooldown_until,
                fire_count, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(alert_key) DO UPDATE SET
                alert_type = excluded.alert_type,
                state_hash = excluded.state_hash,
                last_fired_at = excluded.last_fired_at,
                cooldown_until = excluded.cooldown_until,
                fire_count = excluded.fire_count,
                payload_json = excluded.payload_json
            """,
            (
                alert_key, alert_type, state_hash, fired_at, fired_at + cooldown_seconds,
                fire_count, json.dumps(payload, sort_keys=True),
            ),
        )
        conn.execute(
            """
            INSERT INTO alert_history (alert_id, alert_key, alert_type, priority, payload_json, fired_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                f"alert_{uuid.uuid4().hex[:16]}", alert_key, alert_type,
                str(payload.get("priority") or "NORMAL"), json.dumps(payload, sort_keys=True), fired_at,
            ),
        )
    return True


def list_alerts(*, limit: int = 100) -> list[dict[str, Any]]:
    init_db()
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM alert_history ORDER BY fired_at DESC LIMIT ?",
            (max(1, min(limit, 1000)),),
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["payload"] = json.loads(item.pop("payload_json") or "{}")
        result.append(item)
    return result


def record_provider_event(
    *, provider: str, operation: str, duration_ms: float, status: str,
    error_code: str | None = None,
) -> None:
    """Record metadata only; prompts, responses, URLs, and account IDs are forbidden."""
    init_db()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO provider_health_events (
                event_id, provider, operation, duration_ms, status, error_code, recorded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"provider_{uuid.uuid4().hex[:16]}", provider[:40], operation[:80],
                round(max(0, duration_ms), 2), status[:40], error_code[:80] if error_code else None,
                time.time(),
            ),
        )


def provider_health(*, limit: int = 100) -> dict[str, Any]:
    init_db()
    with connect() as conn:
        rows = conn.execute(
            "SELECT provider, operation, duration_ms, status, error_code, recorded_at FROM provider_health_events ORDER BY recorded_at DESC LIMIT ?",
            (max(1, min(limit, 1000)),),
        ).fetchall()
    events = [dict(row) for row in rows]
    return {
        "events": events,
        "sample_count": len(events),
        "failure_count": sum(row["status"] != "OK" for row in events),
        "prompt_or_response_logged": False,
    }
