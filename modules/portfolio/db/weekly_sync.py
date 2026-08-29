"""Auditable run state for the unified weekly portfolio sync job."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from modules.portfolio.paths import DATA_DIR

DB_PATH = DATA_DIR / "weekly_sync.db"


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def init_db() -> None:
    """Create the additive Milestone 7A run-history schema."""
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sync_runs (
                run_id              TEXT PRIMARY KEY,
                idempotency_key     TEXT NOT NULL,
                iso_week            TEXT NOT NULL,
                mode                TEXT NOT NULL,
                status              TEXT NOT NULL,
                dry_run             INTEGER NOT NULL DEFAULT 0,
                requested_by        TEXT NOT NULL DEFAULT 'cli',
                account_set_hash    TEXT NOT NULL,
                started_at          REAL NOT NULL,
                finished_at         REAL,
                duplicate_of        TEXT,
                summary_json        TEXT,
                error               TEXT,
                FOREIGN KEY (duplicate_of) REFERENCES sync_runs(run_id)
            );

            CREATE TABLE IF NOT EXISTS sync_run_steps (
                run_id              TEXT NOT NULL,
                step_name           TEXT NOT NULL,
                sequence            INTEGER NOT NULL,
                status              TEXT NOT NULL,
                attempts            INTEGER NOT NULL DEFAULT 0,
                started_at          REAL,
                finished_at         REAL,
                details_json        TEXT,
                error               TEXT,
                PRIMARY KEY (run_id, step_name),
                FOREIGN KEY (run_id) REFERENCES sync_runs(run_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS sync_account_results (
                run_id              TEXT NOT NULL,
                account_id          TEXT NOT NULL,
                account_code        TEXT NOT NULL,
                broker              TEXT NOT NULL,
                status              TEXT NOT NULL,
                position_as_of      TEXT,
                price_as_of         TEXT,
                recovery_action     TEXT,
                warnings_json       TEXT,
                PRIMARY KEY (run_id, account_id),
                FOREIGN KEY (run_id) REFERENCES sync_runs(run_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS sync_artifacts (
                artifact_id         INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id              TEXT NOT NULL,
                kind                TEXT NOT NULL,
                path                TEXT,
                content_hash        TEXT,
                metadata_json       TEXT,
                content_json        TEXT,
                created_at          REAL NOT NULL,
                FOREIGN KEY (run_id) REFERENCES sync_runs(run_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS sync_notifications (
                notification_id     INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id              TEXT NOT NULL,
                channel             TEXT NOT NULL,
                status              TEXT NOT NULL,
                destination         TEXT,
                error               TEXT,
                created_at          REAL NOT NULL,
                FOREIGN KEY (run_id) REFERENCES sync_runs(run_id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_sync_runs_started
                ON sync_runs(started_at DESC);
            CREATE INDEX IF NOT EXISTS idx_sync_runs_idempotency
                ON sync_runs(idempotency_key, started_at DESC);
            CREATE INDEX IF NOT EXISTS idx_sync_artifacts_kind
                ON sync_artifacts(kind, created_at DESC);
            """
        )


def create_run(
    *,
    run_id: str,
    idempotency_key: str,
    iso_week: str,
    mode: str,
    dry_run: bool,
    requested_by: str,
    account_set_hash: str,
    started_at: float,
    status: str = "RUNNING",
    duplicate_of: str | None = None,
) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO sync_runs (
                run_id, idempotency_key, iso_week, mode, status, dry_run,
                requested_by, account_set_hash, started_at, duplicate_of
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                idempotency_key,
                iso_week,
                mode,
                status,
                int(dry_run),
                requested_by,
                account_set_hash,
                started_at,
                duplicate_of,
            ),
        )


def finish_run(
    run_id: str,
    *,
    status: str,
    finished_at: float,
    summary: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    with connect() as conn:
        conn.execute(
            """
            UPDATE sync_runs
            SET status = ?, finished_at = ?, summary_json = ?, error = ?
            WHERE run_id = ?
            """,
            (
                status,
                finished_at,
                json.dumps(summary or {}, default=str),
                error,
                run_id,
            ),
        )


def upsert_step(
    run_id: str,
    *,
    step_name: str,
    sequence: int,
    status: str,
    attempts: int,
    started_at: float | None = None,
    finished_at: float | None = None,
    details: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO sync_run_steps (
                run_id, step_name, sequence, status, attempts, started_at,
                finished_at, details_json, error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, step_name) DO UPDATE SET
                sequence = excluded.sequence,
                status = excluded.status,
                attempts = excluded.attempts,
                started_at = COALESCE(sync_run_steps.started_at, excluded.started_at),
                finished_at = excluded.finished_at,
                details_json = excluded.details_json,
                error = excluded.error
            """,
            (
                run_id,
                step_name,
                sequence,
                status,
                attempts,
                started_at,
                finished_at,
                json.dumps(details or {}, default=str),
                error,
            ),
        )


def upsert_account_result(run_id: str, result: dict[str, Any]) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO sync_account_results (
                run_id, account_id, account_code, broker, status,
                position_as_of, price_as_of, recovery_action, warnings_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, account_id) DO UPDATE SET
                account_code = excluded.account_code,
                broker = excluded.broker,
                status = excluded.status,
                position_as_of = excluded.position_as_of,
                price_as_of = excluded.price_as_of,
                recovery_action = excluded.recovery_action,
                warnings_json = excluded.warnings_json
            """,
            (
                run_id,
                result["account_id"],
                result["account_code"],
                result["broker"],
                result["status"],
                result.get("position_as_of"),
                result.get("price_as_of"),
                result.get("recovery_action"),
                json.dumps(result.get("warnings") or [], default=str),
            ),
        )


def copy_account_results(*, source_run_id: str, target_run_id: str) -> None:
    """Carry prior account truth onto an audited duplicate attempt."""
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO sync_account_results (
                run_id, account_id, account_code, broker, status,
                position_as_of, price_as_of, recovery_action, warnings_json
            )
            SELECT ?, account_id, account_code, broker, status,
                   position_as_of, price_as_of, recovery_action, warnings_json
            FROM sync_account_results
            WHERE run_id = ?
            """,
            (target_run_id, source_run_id),
        )


def add_artifact(
    run_id: str,
    *,
    kind: str,
    created_at: float,
    path: str | None = None,
    content_hash: str | None = None,
    metadata: dict[str, Any] | None = None,
    content: dict[str, Any] | None = None,
) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO sync_artifacts (
                run_id, kind, path, content_hash, metadata_json, content_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                kind,
                path,
                content_hash,
                json.dumps(metadata or {}, default=str),
                json.dumps(content or {}, default=str),
                created_at,
            ),
        )


def add_notification(
    run_id: str,
    *,
    channel: str,
    status: str,
    created_at: float,
    destination: str | None = None,
    error: str | None = None,
) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO sync_notifications (
                run_id, channel, status, destination, error, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (run_id, channel, status, destination, error, created_at),
        )


def find_completed_run(idempotency_key: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT * FROM sync_runs
            WHERE idempotency_key = ?
              AND dry_run = 0
              AND status IN ('COMPLETED', 'COMPLETED_WITH_WARNINGS')
            ORDER BY finished_at DESC
            LIMIT 1
            """,
            (idempotency_key,),
        ).fetchone()
    return _decode_run(row) if row else None


def latest_artifact(kind: str, *, before: float | None = None) -> dict[str, Any] | None:
    clauses = [
        "a.kind = ?",
        "r.status IN ('COMPLETED', 'COMPLETED_WITH_WARNINGS')",
        "r.dry_run = 0",
    ]
    params: list[Any] = [kind]
    if before is not None:
        clauses.append("a.created_at < ?")
        params.append(before)
    with connect() as conn:
        row = conn.execute(
            f"""
            SELECT a.* FROM sync_artifacts a
            JOIN sync_runs r ON r.run_id = a.run_id
            WHERE {' AND '.join(clauses)}
            ORDER BY a.created_at DESC
            LIMIT 1
            """,
            params,
        ).fetchone()
    return _decode_artifact(row) if row else None


def get_run(run_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM sync_runs WHERE run_id = ?", (run_id,)).fetchone()
        if not row:
            return None
        steps = conn.execute(
            "SELECT * FROM sync_run_steps WHERE run_id = ? ORDER BY sequence", (run_id,)
        ).fetchall()
        accounts = conn.execute(
            """
            SELECT account_code, broker, status, position_as_of, price_as_of,
                   recovery_action, warnings_json
            FROM sync_account_results WHERE run_id = ? ORDER BY account_code
            """,
            (run_id,),
        ).fetchall()
        artifacts = conn.execute(
            """
            SELECT artifact_id, kind, path, content_hash, metadata_json, created_at
            FROM sync_artifacts WHERE run_id = ? ORDER BY artifact_id
            """,
            (run_id,),
        ).fetchall()
        notifications = conn.execute(
            """
            SELECT notification_id, channel, status, destination, error, created_at
            FROM sync_notifications WHERE run_id = ? ORDER BY notification_id
            """,
            (run_id,),
        ).fetchall()
    out = _decode_run(row)
    out["steps"] = [_decode_step(item) for item in steps]
    out["accounts"] = [_decode_account(item) for item in accounts]
    out["artifacts"] = [_decode_artifact(item) for item in artifacts]
    out["notifications"] = [dict(item) for item in notifications]
    return out


def list_runs(limit: int = 20) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM sync_runs ORDER BY started_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [_decode_run(row) for row in rows]


def sync_status() -> dict[str, Any]:
    runs = list_runs(limit=100)
    latest = get_run(runs[0]["run_id"]) if runs else None
    successful_summary = next(
        (
            run
            for run in runs
            if run["status"] in {"COMPLETED", "COMPLETED_WITH_WARNINGS"}
            and not run["dry_run"]
        ),
        None,
    )
    successful = get_run(successful_summary["run_id"]) if successful_summary else None
    degraded = []
    if latest:
        degraded = [
            account
            for account in latest.get("accounts") or []
            if account["status"] not in {"LIVE_RECONCILED", "LIVE_WITH_WARNINGS"}
        ]
    return {
        "db_path": str(DB_PATH),
        "latest_attempt": latest,
        "last_successful": successful,
        "degraded_accounts": degraded,
        "configured": True,
    }


def _decode_run(row: sqlite3.Row) -> dict[str, Any]:
    out = dict(row)
    out["dry_run"] = bool(out.get("dry_run"))
    out["summary"] = _json(out.pop("summary_json", None), {})
    return out


def _decode_step(row: sqlite3.Row) -> dict[str, Any]:
    out = dict(row)
    out["details"] = _json(out.pop("details_json", None), {})
    return out


def _decode_account(row: sqlite3.Row) -> dict[str, Any]:
    out = dict(row)
    out["warnings"] = _json(out.pop("warnings_json", None), [])
    return out


def _decode_artifact(row: sqlite3.Row) -> dict[str, Any]:
    out = dict(row)
    out["metadata"] = _json(out.pop("metadata_json", None), {})
    if "content_json" in out:
        out["content"] = _json(out.pop("content_json", None), {})
    return out


def _json(raw: str | None, fallback: Any) -> Any:
    if not raw:
        return fallback
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return fallback
