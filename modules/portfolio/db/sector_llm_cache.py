"""Persistent sector reference — LLM, Yahoo, and seed labels (SQLite + JSON file)."""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

from modules.portfolio.paths import DATA_DIR, MODULE_DIR

logger = logging.getLogger(__name__)

DB_PATH = DATA_DIR / "sector_llm_cache.db"
REFERENCE_FILE = DATA_DIR / "sector_reference.json"
EXAMPLE_REFERENCE_FILE = MODULE_DIR / "sector_reference.example.json"

# Higher rank wins on conflict (llm/manual are not overwritten by yahoo).
_SOURCE_RANK: dict[str, int] = {
    "seed": 1,
    "yahoo": 2,
    "file": 2,
    "llm": 4,
    "manual": 5,
}


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def cache_key(symbol: str, exchange: str | None) -> tuple[str, str]:
    return (symbol.strip().upper(), (exchange or "NSE").strip().upper())


def _source_rank(source: str | None) -> int:
    return _SOURCE_RANK.get((source or "").strip().lower(), 0)


def _should_apply_source(new_source: str, existing_source: str | None) -> bool:
    return _source_rank(new_source) >= _source_rank(existing_source)


def init_db() -> None:
    with connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sector_labels (
                symbol      TEXT NOT NULL,
                exchange    TEXT NOT NULL DEFAULT 'NSE',
                sector      TEXT NOT NULL,
                source      TEXT NOT NULL DEFAULT 'llm',
                updated_at  REAL NOT NULL,
                PRIMARY KEY (symbol, exchange)
            )
            """
        )
    import_reference_file(only_if_missing=False)
    if EXAMPLE_REFERENCE_FILE.is_file():
        import_reference_file(path=EXAMPLE_REFERENCE_FILE, only_if_missing=True)


def get_sector(symbol: str, exchange: str | None) -> str | None:
    sym, ex = cache_key(symbol, exchange)
    with connect() as conn:
        row = conn.execute(
            "SELECT sector FROM sector_labels WHERE symbol = ? AND exchange = ?",
            (sym, ex),
        ).fetchone()
    if row:
        return row["sector"]
    if ex != "NSE":
        with connect() as conn:
            row = conn.execute(
                "SELECT sector FROM sector_labels WHERE symbol = ? AND exchange = 'NSE'",
                (sym,),
            ).fetchone()
        return row["sector"] if row else None
    return None


def get_row(symbol: str, exchange: str | None) -> dict[str, Any] | None:
    sym, ex = cache_key(symbol, exchange)
    with connect() as conn:
        row = conn.execute(
            "SELECT symbol, exchange, sector, source, updated_at FROM sector_labels WHERE symbol = ? AND exchange = ?",
            (sym, ex),
        ).fetchone()
    return dict(row) if row else None


def get_many(keys: list[tuple[str, str | None]]) -> dict[tuple[str, str], str]:
    if not keys:
        return {}
    normalized = [cache_key(s, e) for s, e in keys]
    out: dict[tuple[str, str], str] = {}
    with connect() as conn:
        chunk_size = 400
        for offset in range(0, len(normalized), chunk_size):
            chunk = normalized[offset : offset + chunk_size]
            placeholders = ",".join("(?,?)" for _ in chunk)
            params: list[str] = [part for pair in chunk for part in pair]
            rows = conn.execute(
                f"""
                SELECT symbol, exchange, sector FROM sector_labels
                WHERE (symbol, exchange) IN ({placeholders})
                """,
                params,
            ).fetchall()
            for row in rows:
                out[(row["symbol"], row["exchange"])] = row["sector"]
    return out


def get_many_with_source(
    keys: list[tuple[str, str | None]],
) -> dict[tuple[str, str], dict[str, str]]:
    if not keys:
        return {}
    normalized = [cache_key(s, e) for s, e in keys]
    out: dict[tuple[str, str], dict[str, str]] = {}
    with connect() as conn:
        chunk_size = 400
        for offset in range(0, len(normalized), chunk_size):
            chunk = normalized[offset : offset + chunk_size]
            placeholders = ",".join("(?,?)" for _ in chunk)
            params: list[str] = [part for pair in chunk for part in pair]
            rows = conn.execute(
                f"""
                SELECT symbol, exchange, sector, source FROM sector_labels
                WHERE (symbol, exchange) IN ({placeholders})
                """,
                params,
            ).fetchall()
            for row in rows:
                out[(row["symbol"], row["exchange"])] = {
                    "sector": row["sector"],
                    "source": row["source"],
                }
    return out


def put_sectors(
    rows: dict[tuple[str, str], str],
    *,
    source: str = "llm",
    export_file: bool = True,
) -> int:
    """Upsert sectors; respects source priority (llm/manual beat yahoo). Returns rows written."""
    if not rows:
        return 0
    normalized = {cache_key(sym, ex): sector for (sym, ex), sector in rows.items() if sector}
    if not normalized:
        return 0

    existing = get_many_with_source([(k[0], k[1]) for k in normalized])
    to_write: dict[tuple[str, str], str] = {}
    for key, sector in normalized.items():
        prev = existing.get(key)
        if prev and not _should_apply_source(source, prev.get("source")):
            continue
        to_write[key] = sector.strip()

    if not to_write:
        return 0

    now = time.time()
    with connect() as conn:
        conn.executemany(
            """
            INSERT INTO sector_labels (symbol, exchange, sector, source, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(symbol, exchange) DO UPDATE SET
                sector = excluded.sector,
                source = excluded.source,
                updated_at = excluded.updated_at
            """,
            [(sym, ex, sector, source, now) for (sym, ex), sector in to_write.items()],
        )

    if export_file:
        export_reference_file()
    return len(to_write)


def export_reference_file(path: Path | None = None) -> Path:
    """Write all known sectors to JSON for inspection / backup."""
    path = path or REFERENCE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        rows = conn.execute(
            "SELECT symbol, exchange, sector, source, updated_at FROM sector_labels ORDER BY symbol, exchange"
        ).fetchall()
    payload = {
        "version": 1,
        "exported_at": time.time(),
        "entries": [
            {
                "symbol": r["symbol"],
                "exchange": r["exchange"],
                "sector": r["sector"],
                "source": r["source"],
                "updated_at": r["updated_at"],
            }
            for r in rows
        ],
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def import_reference_file(
    *,
    path: Path | None = None,
    only_if_missing: bool = False,
) -> int:
    """Load sectors from JSON into SQLite (file is a static fallback store)."""
    path = path or REFERENCE_FILE
    if not path.is_file():
        return 0

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Sector reference file unreadable (%s): %s", path, exc)
        return 0

    entries = payload.get("entries")
    if not isinstance(entries, list):
        return 0

    rows: dict[tuple[str, str], str] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        sym = (entry.get("symbol") or "").strip().upper()
        sector = (entry.get("sector") or "").strip()
        if not sym or not sector:
            continue
        ex = (entry.get("exchange") or "NSE").strip().upper()
        key = (sym, ex)
        if only_if_missing and get_sector(sym, ex):
            continue
        rows[key] = sector

    if not rows:
        return 0

    source = "file" if path == REFERENCE_FILE else "seed"
    return put_sectors(rows, source=source, export_file=True)


def remember_sector(
    symbol: str,
    exchange: str | None,
    sector: str | None,
    *,
    source: str,
    export_file: bool = False,
) -> None:
    """Store one sector label if we learned it from Yahoo or elsewhere."""
    if not sector or not str(sector).strip():
        return
    sym, ex = cache_key(symbol, exchange)
    put_sectors({(sym, ex): str(sector).strip()}, source=source, export_file=export_file)
