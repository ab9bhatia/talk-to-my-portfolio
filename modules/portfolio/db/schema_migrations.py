"""Transactional schema metadata, integrity checks, and upgrade backups."""

from __future__ import annotations

import shutil
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path

from modules.portfolio.paths import DATA_DIR


Migration = Callable[[sqlite3.Connection], None]
SUPPORTED_SCHEMA_VERSION = 1


class UnsupportedSchemaError(RuntimeError):
    pass


class DatabaseIntegrityError(RuntimeError):
    pass


def _integrity(conn: sqlite3.Connection) -> str:
    row = conn.execute("PRAGMA integrity_check").fetchone()
    return str(row[0] if row else "unknown")


def _backup_database(path: Path, version: int) -> Path:
    backup = path.with_name(f"{path.name}.pre-migrate-v{version}-{int(time.time())}.bak")
    with sqlite3.connect(path) as source, sqlite3.connect(backup) as target:
        source.backup(target)
    backup.chmod(0o600)
    return backup


def ensure_database(
    path: Path, *, supported_version: int = SUPPORTED_SCHEMA_VERSION,
    migrations: dict[int, Migration] | None = None,
) -> dict:
    """Register a legacy DB or advance it one transactional migration at a time."""
    migrations = migrations or {}
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        if _integrity(conn) != "ok":
            raise DatabaseIntegrityError(f"Integrity check failed for {path.name}")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                version INTEGER NOT NULL,
                applied_at REAL NOT NULL
            )
            """
        )
        row = conn.execute("SELECT version FROM schema_migrations WHERE singleton = 1").fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO schema_migrations (singleton, version, applied_at) VALUES (1, ?, ?)",
                (supported_version, time.time()),
            )
            conn.commit()
            path.chmod(0o600)
            return {"database": path.name, "version": supported_version, "registered_legacy": True, "backup": None}
        current = int(row[0])
        if current > supported_version:
            raise UnsupportedSchemaError(
                f"{path.name} schema v{current} is newer than supported v{supported_version}; refusing startup."
            )
        if current == supported_version:
            path.chmod(0o600)
            return {"database": path.name, "version": current, "registered_legacy": False, "backup": None}
        backup = _backup_database(path, current)
        try:
            for target in range(current + 1, supported_version + 1):
                migration = migrations.get(target)
                if migration is None:
                    raise UnsupportedSchemaError(f"No migration to {path.name} schema v{target}")
                conn.execute("BEGIN IMMEDIATE")
                migration(conn)
                conn.execute(
                    "UPDATE schema_migrations SET version = ?, applied_at = ? WHERE singleton = 1",
                    (target, time.time()),
                )
                if _integrity(conn) != "ok":
                    raise DatabaseIntegrityError(f"Post-migration integrity failed for {path.name}")
                conn.commit()
        except Exception:
            conn.rollback()
            raise
    path.chmod(0o600)
    return {"database": path.name, "version": supported_version, "registered_legacy": False, "backup": str(backup)}


def ensure_all_databases() -> list[dict]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.chmod(0o700)
    return [ensure_database(path) for path in sorted(DATA_DIR.glob("*.db"))]


def database_status() -> list[dict]:
    result = []
    for path in sorted(DATA_DIR.glob("*.db")):
        try:
            with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
                integrity = _integrity(conn)
                table = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
                ).fetchone()
                version = (
                    conn.execute("SELECT version FROM schema_migrations WHERE singleton = 1").fetchone()[0]
                    if table
                    else None
                )
        except (OSError, sqlite3.Error) as exc:
            integrity, version = f"error:{type(exc).__name__}", None
        result.append({"database": path.name, "size_bytes": path.stat().st_size, "integrity": integrity, "schema_version": version})
    return result


def restore_migration_backup(backup: Path, destination: Path) -> None:
    """Documented/manual rollback primitive; caller must stop the app first."""
    if not backup.is_file() or backup.suffix != ".bak":
        raise ValueError("Expected a migration .bak file")
    shutil.copy2(backup, destination)
    destination.chmod(0o600)
