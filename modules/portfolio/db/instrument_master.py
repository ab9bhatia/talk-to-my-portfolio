"""Local canonical instrument master, aliases, corporate actions, and overrides."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from modules.portfolio.paths import DATA_DIR


DB_PATH = DATA_DIR / "instrument_master.db"
SCHEMA_VERSION = 1


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS instrument_master (
                instrument_id TEXT PRIMARY KEY,
                version INTEGER NOT NULL DEFAULT 1,
                isin TEXT,
                canonical_symbol TEXT NOT NULL,
                exchange TEXT NOT NULL,
                display_name TEXT NOT NULL,
                legal_name TEXT,
                instrument_type TEXT NOT NULL,
                currency TEXT NOT NULL,
                domicile TEXT,
                issuer_or_amc TEXT,
                scheme_plan TEXT,
                scheme_option TEXT,
                underlying_index TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                tradability_status TEXT NOT NULL DEFAULT 'UNKNOWN',
                source TEXT NOT NULL,
                source_as_of TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_instrument_isin
                ON instrument_master(upper(isin)) WHERE isin IS NOT NULL AND isin != '';
            CREATE INDEX IF NOT EXISTS idx_instrument_symbol_exchange
                ON instrument_master(upper(canonical_symbol), upper(exchange));

            CREATE TABLE IF NOT EXISTS instrument_aliases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                instrument_id TEXT NOT NULL,
                alias_type TEXT NOT NULL,
                alias_value TEXT NOT NULL,
                exchange TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL,
                source_as_of TEXT NOT NULL,
                valid_from TEXT,
                valid_to TEXT,
                created_at REAL NOT NULL,
                UNIQUE(alias_type, alias_value, exchange),
                FOREIGN KEY (instrument_id) REFERENCES instrument_master(instrument_id)
            );
            CREATE INDEX IF NOT EXISTS idx_alias_lookup
                ON instrument_aliases(alias_type, upper(alias_value), upper(exchange));

            CREATE TABLE IF NOT EXISTS corporate_actions (
                action_id INTEGER PRIMARY KEY AUTOINCREMENT,
                instrument_id TEXT NOT NULL,
                successor_instrument_id TEXT,
                action_type TEXT NOT NULL,
                effective_date TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'PENDING_REVIEW',
                ratio_numerator REAL,
                ratio_denominator REAL,
                source_document TEXT NOT NULL,
                source_as_of TEXT NOT NULL,
                notes TEXT,
                created_at REAL NOT NULL,
                FOREIGN KEY (instrument_id) REFERENCES instrument_master(instrument_id),
                FOREIGN KEY (successor_instrument_id) REFERENCES instrument_master(instrument_id)
            );

            CREATE TABLE IF NOT EXISTS reconciliation_overrides (
                override_id INTEGER PRIMARY KEY AUTOINCREMENT,
                instrument_id TEXT NOT NULL,
                account_code TEXT,
                override_type TEXT NOT NULL,
                value_json TEXT NOT NULL,
                reason TEXT NOT NULL,
                source_document TEXT NOT NULL,
                as_of_date TEXT NOT NULL,
                approved_by TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                created_at REAL NOT NULL,
                FOREIGN KEY (instrument_id) REFERENCES instrument_master(instrument_id)
            );
            CREATE TABLE IF NOT EXISTS reconciliation_override_audit (
                audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                override_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                actor TEXT NOT NULL,
                details_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                FOREIGN KEY (override_id) REFERENCES reconciliation_overrides(override_id)
            );
            """
        )


def upsert_instrument(row: dict[str, Any]) -> dict[str, Any]:
    init_db()
    now = time.time()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO instrument_master (
                instrument_id, version, isin, canonical_symbol, exchange,
                display_name, legal_name, instrument_type, currency, domicile,
                issuer_or_amc, scheme_plan, scheme_option, underlying_index,
                active, tradability_status, source, source_as_of, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(instrument_id) DO UPDATE SET
                version = excluded.version,
                isin = COALESCE(excluded.isin, instrument_master.isin),
                canonical_symbol = excluded.canonical_symbol,
                exchange = excluded.exchange,
                display_name = excluded.display_name,
                legal_name = COALESCE(excluded.legal_name, instrument_master.legal_name),
                instrument_type = excluded.instrument_type,
                currency = excluded.currency,
                domicile = COALESCE(excluded.domicile, instrument_master.domicile),
                issuer_or_amc = COALESCE(excluded.issuer_or_amc, instrument_master.issuer_or_amc),
                scheme_plan = COALESCE(excluded.scheme_plan, instrument_master.scheme_plan),
                scheme_option = COALESCE(excluded.scheme_option, instrument_master.scheme_option),
                underlying_index = COALESCE(excluded.underlying_index, instrument_master.underlying_index),
                active = excluded.active,
                tradability_status = excluded.tradability_status,
                source = excluded.source,
                source_as_of = excluded.source_as_of,
                updated_at = excluded.updated_at
            """,
            (
                row["instrument_id"],
                int(row.get("version") or SCHEMA_VERSION),
                row.get("isin"),
                row["canonical_symbol"],
                row["exchange"],
                row.get("display_name") or row["canonical_symbol"],
                row.get("legal_name"),
                row.get("instrument_type") or "equity",
                row.get("currency") or "INR",
                row.get("domicile"),
                row.get("issuer_or_amc"),
                row.get("scheme_plan"),
                row.get("scheme_option"),
                row.get("underlying_index"),
                int(row.get("active", True)),
                row.get("tradability_status") or "UNKNOWN",
                row.get("source") or "portfolio_holding",
                row.get("source_as_of") or time.strftime("%Y-%m-%d"),
                now,
                now,
            ),
        )
    return get_instrument(str(row["instrument_id"])) or dict(row)


def add_alias(
    instrument_id: str,
    *,
    alias_type: str,
    alias_value: str,
    exchange: str | None,
    source: str,
    source_as_of: str,
) -> None:
    value = alias_value.strip().upper()
    if not value:
        return
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO instrument_aliases (
                instrument_id, alias_type, alias_value, exchange,
                source, source_as_of, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(alias_type, alias_value, exchange) DO UPDATE SET
                instrument_id = excluded.instrument_id,
                source = excluded.source,
                source_as_of = excluded.source_as_of
            """,
            (
                instrument_id,
                alias_type.strip().upper(),
                value,
                (exchange or "").strip().upper(),
                source,
                source_as_of,
                time.time(),
            ),
        )


def get_instrument(instrument_id: str) -> dict[str, Any] | None:
    init_db()
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM instrument_master WHERE instrument_id = ?", (instrument_id,)
        ).fetchone()
    return _decode_instrument(row) if row else None


def find_by_isin(isin: str) -> dict[str, Any] | None:
    init_db()
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM instrument_master WHERE upper(isin) = upper(?)", (isin.strip(),)
        ).fetchone()
    return _decode_instrument(row) if row else None


def resolve_alias(
    alias_type: str,
    alias_value: str,
    *,
    exchange: str | None = None,
) -> dict[str, Any] | None:
    init_db()
    with connect() as conn:
        row = conn.execute(
            """
            SELECT m.* FROM instrument_aliases a
            JOIN instrument_master m ON m.instrument_id = a.instrument_id
            WHERE a.alias_type = ? AND upper(a.alias_value) = upper(?)
              AND upper(a.exchange) = upper(?)
              AND (a.valid_to IS NULL OR a.valid_to >= date('now'))
            LIMIT 1
            """,
            (alias_type.strip().upper(), alias_value.strip(), (exchange or "").strip()),
        ).fetchone()
    return _decode_instrument(row) if row else None


def list_instruments(*, query: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    init_db()
    sql = "SELECT * FROM instrument_master"
    params: list[Any] = []
    if query:
        sql += " WHERE upper(canonical_symbol) LIKE ? OR upper(display_name) LIKE ? OR upper(isin) = ?"
        term = f"%{query.strip().upper()}%"
        params.extend([term, term, query.strip().upper()])
    sql += " ORDER BY display_name, instrument_id LIMIT ?"
    params.append(max(1, min(limit, 500)))
    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_decode_instrument(row) for row in rows]


def add_corporate_action(row: dict[str, Any]) -> dict[str, Any]:
    required = ("instrument_id", "action_type", "effective_date", "source_document", "source_as_of")
    missing = [field for field in required if not str(row.get(field) or "").strip()]
    if missing:
        raise ValueError(f"Missing corporate-action fields: {', '.join(missing)}")
    init_db()
    with connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO corporate_actions (
                instrument_id, successor_instrument_id, action_type, effective_date,
                status, ratio_numerator, ratio_denominator, source_document,
                source_as_of, notes, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["instrument_id"], row.get("successor_instrument_id"),
                str(row["action_type"]).upper(), row["effective_date"],
                row.get("status") or "PENDING_REVIEW", row.get("ratio_numerator"),
                row.get("ratio_denominator"), row["source_document"],
                row["source_as_of"], row.get("notes"), time.time(),
            ),
        )
        action_id = int(cursor.lastrowid)
    return get_corporate_action(action_id) or {"action_id": action_id, **row}


def get_corporate_action(action_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM corporate_actions WHERE action_id = ?", (action_id,)
        ).fetchone()
    return dict(row) if row else None


def list_corporate_actions(
    *, instrument_id: str | None = None, pending_only: bool = False
) -> list[dict[str, Any]]:
    init_db()
    clauses: list[str] = []
    params: list[Any] = []
    if instrument_id:
        clauses.append("instrument_id = ?")
        params.append(instrument_id)
    if pending_only:
        clauses.append("status != 'RESOLVED'")
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    with connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM corporate_actions{where} ORDER BY effective_date DESC, action_id DESC",
            params,
        ).fetchall()
    return [dict(row) for row in rows]


def create_override(row: dict[str, Any]) -> dict[str, Any]:
    required = (
        "instrument_id", "override_type", "reason", "source_document",
        "as_of_date", "approved_by",
    )
    missing = [field for field in required if not str(row.get(field) or "").strip()]
    if missing:
        raise ValueError(f"Manual override requires: {', '.join(missing)}")
    init_db()
    now = time.time()
    with connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO reconciliation_overrides (
                instrument_id, account_code, override_type, value_json, reason,
                source_document, as_of_date, approved_by, active, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
            """,
            (
                row["instrument_id"], row.get("account_code"),
                str(row["override_type"]).upper(), json.dumps(row.get("value"), default=str),
                row["reason"], row["source_document"], row["as_of_date"],
                row["approved_by"], now,
            ),
        )
        override_id = int(cursor.lastrowid)
        conn.execute(
            """
            INSERT INTO reconciliation_override_audit (
                override_id, action, actor, details_json, created_at
            ) VALUES (?, 'CREATED', ?, ?, ?)
            """,
            (override_id, row["approved_by"], json.dumps({"reason": row["reason"]}), now),
        )
    return get_override(override_id) or {"override_id": override_id, **row}


def get_override(override_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM reconciliation_overrides WHERE override_id = ?", (override_id,)
        ).fetchone()
    return _decode_override(row) if row else None


def list_overrides(*, instrument_id: str | None = None) -> list[dict[str, Any]]:
    init_db()
    if instrument_id:
        sql = "SELECT * FROM reconciliation_overrides WHERE instrument_id = ? ORDER BY created_at DESC"
        params: tuple[Any, ...] = (instrument_id,)
    else:
        sql = "SELECT * FROM reconciliation_overrides ORDER BY created_at DESC"
        params = ()
    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_decode_override(row) for row in rows]


def override_audit(override_id: int) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM reconciliation_override_audit WHERE override_id = ? ORDER BY created_at",
            (override_id,),
        ).fetchall()
    return [{**dict(row), "details": _json(row["details_json"], {})} for row in rows]


def _decode_instrument(row: sqlite3.Row) -> dict[str, Any]:
    out = dict(row)
    out["active"] = bool(out.get("active"))
    return out


def _decode_override(row: sqlite3.Row) -> dict[str, Any]:
    out = dict(row)
    out["active"] = bool(out.get("active"))
    out["value"] = _json(out.pop("value_json", None), None)
    return out


def _json(raw: str | None, fallback: Any) -> Any:
    try:
        return json.loads(raw) if raw is not None else fallback
    except json.JSONDecodeError:
        return fallback
