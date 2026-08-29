"""Local research workspace: screens, candidates, watchlists, thesis, and events."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from typing import Any

from modules.portfolio.paths import DATA_DIR


DB_PATH = DATA_DIR / "research_workspace.db"
SCHEMA_VERSION = 2


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
            CREATE TABLE IF NOT EXISTS saved_screens (
                screen_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                schema_version INTEGER NOT NULL,
                definition_json TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS saved_screen_revisions (
                revision_id INTEGER PRIMARY KEY AUTOINCREMENT,
                screen_id TEXT NOT NULL,
                schema_version INTEGER NOT NULL,
                definition_json TEXT NOT NULL,
                reason TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS candidate_universe (
                instrument_id TEXT PRIMARY KEY,
                research_status TEXT NOT NULL,
                source_coverage_pct REAL NOT NULL,
                account_eligibility_json TEXT NOT NULL,
                role TEXT NOT NULL,
                max_weight_pct REAL NOT NULL,
                liquidity_threshold REAL NOT NULL,
                overlap_impact TEXT NOT NULL,
                source TEXT NOT NULL,
                source_as_of TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS watchlist_entries (
                watchlist_entry_id TEXT PRIMARY KEY,
                watchlist_name TEXT NOT NULL,
                instrument_id TEXT NOT NULL,
                target_role TEXT NOT NULL,
                entry_condition TEXT NOT NULL,
                desired_weight_pct REAL NOT NULL,
                valuation_range TEXT,
                event_deadline TEXT,
                invalidation_trigger TEXT NOT NULL,
                source_evidence TEXT NOT NULL,
                user_notes TEXT,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS thesis_journal (
                thesis_entry_id TEXT PRIMARY KEY,
                instrument_id TEXT NOT NULL,
                thesis TEXT NOT NULL,
                invalidation_trigger TEXT NOT NULL,
                source TEXT NOT NULL,
                source_as_of TEXT NOT NULL,
                decision TEXT NOT NULL,
                author TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_thesis_history
                ON thesis_journal(instrument_id, created_at, thesis_entry_id);
            CREATE TABLE IF NOT EXISTS research_events (
                event_id TEXT PRIMARY KEY,
                instrument_id TEXT,
                event_type TEXT NOT NULL,
                event_date TEXT NOT NULL,
                title TEXT NOT NULL,
                source TEXT NOT NULL,
                source_as_of TEXT NOT NULL,
                verified INTEGER NOT NULL,
                ownership_change_pct REAL,
                created_at REAL NOT NULL
            );
            """
        )


def save_screen(*, name: str, definition: dict[str, Any], screen_id: str | None = None, reason: str = "saved") -> dict[str, Any]:
    init_db()
    now = time.time()
    screen_id = screen_id or f"screen_{uuid.uuid4().hex[:16]}"
    migrated = migrate_screen(definition)
    payload = json.dumps(migrated, sort_keys=True)
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO saved_screens (screen_id, name, schema_version, definition_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(screen_id) DO UPDATE SET
                name = excluded.name,
                schema_version = excluded.schema_version,
                definition_json = excluded.definition_json,
                updated_at = excluded.updated_at
            """,
            (screen_id, name, SCHEMA_VERSION, payload, now, now),
        )
        conn.execute(
            """
            INSERT INTO saved_screen_revisions (screen_id, schema_version, definition_json, reason, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (screen_id, SCHEMA_VERSION, payload, reason, now),
        )
    return get_screen(screen_id) or {"screen_id": screen_id, "name": name, "definition": migrated}


def get_screen(screen_id: str) -> dict[str, Any] | None:
    init_db()
    with connect() as conn:
        row = conn.execute("SELECT * FROM saved_screens WHERE screen_id = ?", (screen_id,)).fetchone()
    if not row:
        return None
    item = dict(row)
    item["definition"] = migrate_screen(json.loads(item.pop("definition_json") or "{}"))
    item["schema_version"] = SCHEMA_VERSION
    return item


def list_screens() -> list[dict[str, Any]]:
    init_db()
    with connect() as conn:
        rows = conn.execute("SELECT screen_id FROM saved_screens ORDER BY updated_at DESC").fetchall()
    return [item for row in rows if (item := get_screen(row["screen_id"]))]


def screen_revisions(screen_id: str) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM saved_screen_revisions WHERE screen_id = ? ORDER BY revision_id",
            (screen_id,),
        ).fetchall()
    return [{**dict(row), "definition": json.loads(row["definition_json"])} for row in rows]


def migrate_screen(definition: dict[str, Any]) -> dict[str, Any]:
    if definition.get("schema_version") == SCHEMA_VERSION and definition.get("root"):
        return definition
    if isinstance(definition.get("filters"), list):
        return {"schema_version": SCHEMA_VERSION, "root": {"op": "AND", "conditions": definition["filters"]}}
    root = definition.get("root") or definition
    return {"schema_version": SCHEMA_VERSION, "root": root}


def upsert_candidate(row: dict[str, Any]) -> dict[str, Any]:
    required = ("instrument_id", "research_status", "role", "source", "source_as_of")
    missing = [key for key in required if not str(row.get(key) or "").strip()]
    if missing:
        raise ValueError(f"Missing candidate fields: {', '.join(missing)}")
    now = time.time()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO candidate_universe (
                instrument_id, research_status, source_coverage_pct, account_eligibility_json,
                role, max_weight_pct, liquidity_threshold, overlap_impact, source, source_as_of,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(instrument_id) DO UPDATE SET
                research_status = excluded.research_status,
                source_coverage_pct = excluded.source_coverage_pct,
                account_eligibility_json = excluded.account_eligibility_json,
                role = excluded.role,
                max_weight_pct = excluded.max_weight_pct,
                liquidity_threshold = excluded.liquidity_threshold,
                overlap_impact = excluded.overlap_impact,
                source = excluded.source,
                source_as_of = excluded.source_as_of,
                updated_at = excluded.updated_at
            """,
            (
                row["instrument_id"], str(row["research_status"]).upper(),
                float(row.get("source_coverage_pct") or 0), json.dumps(row.get("account_eligibility") or []),
                row["role"], float(row.get("max_weight_pct") or 0),
                float(row.get("liquidity_threshold") or 0), row.get("overlap_impact") or "UNKNOWN",
                row["source"], row["source_as_of"], now, now,
            ),
        )
    return get_candidate(row["instrument_id"]) or row


def get_candidate(instrument_id: str) -> dict[str, Any] | None:
    init_db()
    with connect() as conn:
        row = conn.execute("SELECT * FROM candidate_universe WHERE instrument_id = ?", (instrument_id,)).fetchone()
    if not row:
        return None
    item = dict(row)
    item["account_eligibility"] = json.loads(item.pop("account_eligibility_json") or "[]")
    item["approved_for_recommendation"] = item["research_status"] == "APPROVED"
    return item


def list_candidates() -> list[dict[str, Any]]:
    init_db()
    with connect() as conn:
        rows = conn.execute("SELECT instrument_id FROM candidate_universe ORDER BY role, instrument_id").fetchall()
    return [item for row in rows if (item := get_candidate(row["instrument_id"]))]


def add_watchlist_entry(row: dict[str, Any]) -> dict[str, Any]:
    entry_id = f"watch_{uuid.uuid4().hex[:16]}"
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO watchlist_entries (
                watchlist_entry_id, watchlist_name, instrument_id, target_role, entry_condition,
                desired_weight_pct, valuation_range, event_deadline, invalidation_trigger,
                source_evidence, user_notes, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry_id, row.get("watchlist_name") or "Research", row["instrument_id"],
                row["target_role"], row["entry_condition"], float(row.get("desired_weight_pct") or 0),
                row.get("valuation_range"), row.get("event_deadline"), row["invalidation_trigger"],
                row["source_evidence"], row.get("user_notes"), time.time(),
            ),
        )
    return {"watchlist_entry_id": entry_id, **row}


def list_watchlist() -> list[dict[str, Any]]:
    init_db()
    with connect() as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM watchlist_entries ORDER BY created_at DESC").fetchall()]


def append_thesis(row: dict[str, Any]) -> dict[str, Any]:
    entry_id = f"thesis_{uuid.uuid4().hex[:16]}"
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO thesis_journal (
                thesis_entry_id, instrument_id, thesis, invalidation_trigger,
                source, source_as_of, decision, author, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry_id, row["instrument_id"], row["thesis"], row["invalidation_trigger"],
                row["source"], row["source_as_of"], row.get("decision") or "WATCH",
                row.get("author") or "local-user", time.time(),
            ),
        )
    return {"thesis_entry_id": entry_id, **row}


def thesis_history(instrument_id: str) -> list[dict[str, Any]]:
    init_db()
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM thesis_journal WHERE instrument_id = ? ORDER BY created_at, thesis_entry_id",
            (instrument_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def add_event(row: dict[str, Any]) -> dict[str, Any]:
    event_id = f"event_{uuid.uuid4().hex[:16]}"
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO research_events (
                event_id, instrument_id, event_type, event_date, title, source,
                source_as_of, verified, ownership_change_pct, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id, row.get("instrument_id"), row["event_type"], row["event_date"],
                row["title"], row["source"], row["source_as_of"], int(bool(row.get("verified"))),
                row.get("ownership_change_pct"), time.time(),
            ),
        )
    return {"event_id": event_id, **row}


def list_events(*, instrument_id: str | None = None) -> list[dict[str, Any]]:
    init_db()
    sql = "SELECT * FROM research_events"
    params: tuple[Any, ...] = ()
    if instrument_id:
        sql += " WHERE instrument_id = ?"
        params = (instrument_id,)
    sql += " ORDER BY event_date, event_id"
    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [{**dict(row), "verified": bool(row["verified"])} for row in rows]
