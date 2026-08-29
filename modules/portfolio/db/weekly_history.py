"""Weekly portfolio snapshots — historical positions for growth tracking."""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any

from modules.portfolio.paths import DATA_DIR

DB_PATH = DATA_DIR / "portfolio_history.db"


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS weekly_snapshots (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                scope           TEXT NOT NULL,
                account_id      TEXT,
                week_start      TEXT NOT NULL,
                captured_at     REAL NOT NULL,
                source          TEXT NOT NULL,
                usd_inr         REAL,
                holdings_count  INTEGER NOT NULL DEFAULT 0,
                total_invested  REAL NOT NULL DEFAULT 0,
                total_current   REAL NOT NULL DEFAULT 0,
                total_pnl       REAL NOT NULL DEFAULT 0,
                total_pnl_pct   REAL NOT NULL DEFAULT 0,
                notes           TEXT,
                sync_run_id     TEXT,
                sync_stage      TEXT,
                snapshot_quality TEXT NOT NULL DEFAULT 'UNKNOWN',
                accounts_expected INTEGER,
                accounts_included INTEGER,
                live_accounts   INTEGER,
                cached_accounts INTEGER,
                manual_current_accounts INTEGER,
                coverage_pct    REAL,
                position_as_of_min TEXT,
                price_as_of_min TEXT,
                market_session_date TEXT,
                comparable_to_previous INTEGER NOT NULL DEFAULT 0,
                comparability_reasons_json TEXT NOT NULL DEFAULT '[]',
                UNIQUE(scope, account_id, week_start)
            );

            CREATE TABLE IF NOT EXISTS weekly_positions (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_id     INTEGER NOT NULL,
                symbol          TEXT NOT NULL,
                exchange        TEXT NOT NULL DEFAULT 'NSE',
                currency        TEXT NOT NULL DEFAULT 'INR',
                asset_class     TEXT NOT NULL DEFAULT 'equity',
                quantity        REAL NOT NULL DEFAULT 0,
                avg_price       REAL NOT NULL DEFAULT 0,
                last_price      REAL NOT NULL DEFAULT 0,
                invested        REAL NOT NULL DEFAULT 0,
                current_value   REAL NOT NULL DEFAULT 0,
                pnl             REAL NOT NULL DEFAULT 0,
                pnl_pct         REAL NOT NULL DEFAULT 0,
                sector          TEXT,
                market_cap      TEXT,
                pe_ratio        REAL,
                rating_label    TEXT,
                extra_json      TEXT,
                FOREIGN KEY (snapshot_id) REFERENCES weekly_snapshots(id) ON DELETE CASCADE,
                UNIQUE(snapshot_id, symbol, exchange)
            );

            CREATE INDEX IF NOT EXISTS idx_weekly_snapshots_week
                ON weekly_snapshots(week_start DESC);
            CREATE INDEX IF NOT EXISTS idx_weekly_positions_snapshot
                ON weekly_positions(snapshot_id);
            CREATE INDEX IF NOT EXISTS idx_weekly_snapshots_scope
                ON weekly_snapshots(scope, account_id, week_start DESC);
            """
        )
        _add_snapshot_metadata_columns(conn)
        _cleanup_duplicate_family_weeks(conn)
        # SQLite UNIQUE(scope, account_id, week_start) does not dedupe NULL account ids.
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_weekly_family_week_unique
            ON weekly_snapshots(scope, week_start)
            WHERE account_id IS NULL
            """
        )


def week_start_for(when: date | None = None) -> str:
    """ISO Monday date YYYY-MM-DD for the week containing `when`."""
    when = when or date.today()
    monday = when - timedelta(days=when.weekday())
    return monday.isoformat()


def _summarize_positions(positions: list[dict[str, Any]]) -> dict[str, float]:
    total_invested = sum(float(p.get("invested") or 0) for p in positions)
    total_current = sum(float(p.get("current_value") or 0) for p in positions)
    total_pnl = sum(float(p.get("pnl") or 0) for p in positions)
    return {
        "holdings_count": len(positions),
        "total_invested": round(total_invested, 2),
        "total_current": round(total_current, 2),
        "total_pnl": round(total_pnl, 2),
        "total_pnl_pct": round((total_pnl / total_invested * 100) if total_invested else 0.0, 2),
    }


def save_snapshot(
    *,
    scope: str,
    account_id: str | None,
    positions: list[dict[str, Any]],
    source: str,
    week_start: str | None = None,
    captured_at: float | None = None,
    usd_inr: float | None = None,
    notes: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Insert or replace one weekly snapshot and its positions."""
    if scope == "family":
        from modules.portfolio.services.holdings_view import (
            aggregate_holdings_across_accounts,
        )

        positions = aggregate_holdings_across_accounts(positions)
    week_start = week_start or week_start_for()
    captured_at = captured_at if captured_at is not None else time.time()
    summary = _summarize_positions(positions)
    meta = dict(metadata or {})

    with connect() as conn:
        existing = conn.execute(
            """
            SELECT * FROM weekly_snapshots
            WHERE scope = ? AND account_id IS ? AND week_start = ?
            ORDER BY captured_at DESC, id DESC
            LIMIT 1
            """,
            (scope, account_id, week_start),
        ).fetchone()
        if existing is not None:
            previous_meta = _metadata_from_mapping(_decode_snapshot_row(existing))
            meta = {**previous_meta, **meta}
        conn.execute(
            """
            INSERT INTO weekly_snapshots (
                scope, account_id, week_start, captured_at, source, usd_inr,
                holdings_count, total_invested, total_current, total_pnl, total_pnl_pct, notes,
                sync_run_id, sync_stage, snapshot_quality, accounts_expected,
                accounts_included, live_accounts, cached_accounts, manual_current_accounts,
                coverage_pct, position_as_of_min, price_as_of_min, market_session_date,
                comparable_to_previous, comparability_reasons_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT DO UPDATE SET
                captured_at = excluded.captured_at,
                source = excluded.source,
                usd_inr = COALESCE(excluded.usd_inr, weekly_snapshots.usd_inr),
                holdings_count = excluded.holdings_count,
                total_invested = excluded.total_invested,
                total_current = excluded.total_current,
                total_pnl = excluded.total_pnl,
                total_pnl_pct = excluded.total_pnl_pct,
                notes = COALESCE(excluded.notes, weekly_snapshots.notes),
                sync_run_id = COALESCE(excluded.sync_run_id, weekly_snapshots.sync_run_id),
                sync_stage = COALESCE(excluded.sync_stage, weekly_snapshots.sync_stage),
                snapshot_quality = excluded.snapshot_quality,
                accounts_expected = excluded.accounts_expected,
                accounts_included = excluded.accounts_included,
                live_accounts = excluded.live_accounts,
                cached_accounts = excluded.cached_accounts,
                manual_current_accounts = excluded.manual_current_accounts,
                coverage_pct = excluded.coverage_pct,
                position_as_of_min = excluded.position_as_of_min,
                price_as_of_min = excluded.price_as_of_min,
                market_session_date = excluded.market_session_date,
                comparable_to_previous = excluded.comparable_to_previous,
                comparability_reasons_json = excluded.comparability_reasons_json
            """,
            (
                scope,
                account_id,
                week_start,
                captured_at,
                source,
                usd_inr,
                summary["holdings_count"],
                summary["total_invested"],
                summary["total_current"],
                summary["total_pnl"],
                summary["total_pnl_pct"],
                notes,
                meta.get("sync_run_id"),
                meta.get("sync_stage"),
                meta.get("snapshot_quality") or "UNKNOWN",
                meta.get("accounts_expected"),
                meta.get("accounts_included"),
                meta.get("live_accounts"),
                meta.get("cached_accounts"),
                meta.get("manual_current_accounts"),
                meta.get("coverage_pct"),
                meta.get("position_as_of_min"),
                meta.get("price_as_of_min"),
                meta.get("market_session_date"),
                int(bool(meta.get("comparable_to_previous"))),
                json.dumps(meta.get("comparability_reasons") or []),
            ),
        )
        row = conn.execute(
            """
            SELECT id FROM weekly_snapshots
            WHERE scope = ? AND account_id IS ? AND week_start = ?
            """,
            (scope, account_id, week_start),
        ).fetchone()
        snapshot_id = int(row["id"])
        conn.execute("DELETE FROM weekly_positions WHERE snapshot_id = ?", (snapshot_id,))

        for pos in positions:
            extra = {
                k: pos[k]
                for k in pos
                if k
                not in {
                    "symbol",
                    "exchange",
                    "quantity",
                    "avg_price",
                    "last_price",
                    "invested",
                    "current_value",
                    "pnl",
                    "pnl_pct",
                    "sector",
                    "market_cap",
                    "pe_ratio",
                    "rating_label",
                    "asset_class",
                    "currency",
                }
                and pos[k] is not None
            }
            conn.execute(
                """
                INSERT INTO weekly_positions (
                    snapshot_id, symbol, exchange, currency, asset_class,
                    quantity, avg_price, last_price, invested, current_value,
                    pnl, pnl_pct, sector, market_cap, pe_ratio, rating_label, extra_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    (pos.get("symbol") or "").upper(),
                    (pos.get("exchange") or "NSE").upper(),
                    pos.get("currency") or "INR",
                    pos.get("asset_class") or "equity",
                    float(pos.get("quantity") or 0),
                    float(pos.get("avg_price") or 0),
                    float(pos.get("last_price") or 0),
                    float(pos.get("invested") or 0),
                    float(pos.get("current_value") or 0),
                    float(pos.get("pnl") or 0),
                    float(pos.get("pnl_pct") or 0),
                    pos.get("sector"),
                    pos.get("market_cap"),
                    pos.get("pe_ratio"),
                    pos.get("rating_label"),
                    json.dumps(extra, default=str) if extra else None,
                ),
            )

    return {
        "snapshot_id": snapshot_id,
        "scope": scope,
        "account_id": account_id,
        "week_start": week_start,
        "captured_at": captured_at,
        "source": source,
        **_metadata_from_mapping({**meta, "snapshot_quality": meta.get("snapshot_quality") or "UNKNOWN"}),
        **summary,
    }


def get_snapshot(snapshot_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        snap = conn.execute(
            "SELECT * FROM weekly_snapshots WHERE id = ?", (snapshot_id,)
        ).fetchone()
        if not snap:
            return None
        positions = conn.execute(
            "SELECT * FROM weekly_positions WHERE snapshot_id = ? ORDER BY current_value DESC",
            (snapshot_id,),
        ).fetchall()
    return _row_to_snapshot(snap, positions)


def list_snapshots(
    *,
    scope: str,
    account_id: str | None = None,
    limit: int = 52,
) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM weekly_snapshots
            WHERE scope = ? AND account_id IS ?
            ORDER BY week_start DESC
            LIMIT ?
            """,
            (scope, account_id, limit),
        ).fetchall()
    return [_decode_snapshot_row(r) for r in rows]


def growth_series(
    *,
    scope: str,
    account_id: str | None = None,
    weeks: int = 52,
) -> list[dict[str, Any]]:
    """Weekly totals for portfolio growth chart (oldest first)."""
    rows = list_snapshots(scope=scope, account_id=account_id, limit=weeks)
    rows.reverse()
    return [
        {
            "week_start": r["week_start"],
            "total_current": r["total_current"],
            "total_invested": r["total_invested"],
            "total_pnl": r["total_pnl"],
            "total_pnl_pct": r["total_pnl_pct"],
            "holdings_count": r["holdings_count"],
            "source": r["source"],
            "captured_at": r["captured_at"],
            "sync_run_id": r.get("sync_run_id"),
            "sync_stage": r.get("sync_stage"),
            "snapshot_quality": r.get("snapshot_quality") or "UNKNOWN",
            "accounts_expected": r.get("accounts_expected"),
            "accounts_included": r.get("accounts_included"),
            "coverage_pct": r.get("coverage_pct"),
            "market_session_date": r.get("market_session_date"),
            "comparable_to_previous": bool(r.get("comparable_to_previous")),
            "comparability_reasons": r.get("comparability_reasons") or [],
        }
        for r in rows
    ]


def compare_weeks(
    *,
    scope: str,
    account_id: str | None,
    week_start: str | None = None,
) -> dict[str, Any]:
    """Diff current week vs previous — qty drops imply sales in the gap."""
    week_start = week_start or week_start_for()
    snaps = list_snapshots(scope=scope, account_id=account_id, limit=2)
    if not snaps:
        return {"week_start": week_start, "previous_week": None, "changes": []}

    current = get_snapshot(snaps[0]["id"]) if snaps else None
    previous = get_snapshot(snaps[1]["id"]) if len(snaps) > 1 else None
    if not current:
        return {"week_start": week_start, "previous_week": None, "changes": []}

    prev_map = {}
    if previous:
        for p in previous["positions"]:
            key = (p["symbol"], p["exchange"])
            prev_map[key] = p

    changes: list[dict[str, Any]] = []
    cur_keys = set()
    for p in current["positions"]:
        key = (p["symbol"], p["exchange"])
        cur_keys.add(key)
        old = prev_map.get(key)
        old_qty = float(old["quantity"]) if old else 0.0
        new_qty = float(p["quantity"])
        if old and new_qty < old_qty - 1e-6:
            changes.append(
                {
                    "symbol": p["symbol"],
                    "exchange": p["exchange"],
                    "change": "sold",
                    "qty_before": old_qty,
                    "qty_after": new_qty,
                    "qty_delta": round(new_qty - old_qty, 4),
                }
            )
        elif not old and new_qty > 0:
            changes.append(
                {
                    "symbol": p["symbol"],
                    "exchange": p["exchange"],
                    "change": "new",
                    "qty_before": 0,
                    "qty_after": new_qty,
                    "qty_delta": new_qty,
                }
            )
        elif old and new_qty > old_qty + 1e-6:
            changes.append(
                {
                    "symbol": p["symbol"],
                    "exchange": p["exchange"],
                    "change": "bought",
                    "qty_before": old_qty,
                    "qty_after": new_qty,
                    "qty_delta": round(new_qty - old_qty, 4),
                }
            )

    for key, old in prev_map.items():
        if key not in cur_keys:
            changes.append(
                {
                    "symbol": old["symbol"],
                    "exchange": old["exchange"],
                    "change": "closed",
                    "qty_before": float(old["quantity"]),
                    "qty_after": 0,
                    "qty_delta": -float(old["quantity"]),
                }
            )

    return {
        "week_start": current["week_start"],
        "previous_week": previous["week_start"] if previous else None,
        "changes": changes,
    }


def refresh_current_week_ltps(
    *,
    scope: str,
    account_id: str | None,
    price_fetcher,
) -> dict[str, Any]:
    """
    Update last_price / value fields for the current week's snapshot only.
    `price_fetcher(symbol, exchange) -> last_price_inr | None`
    """
    week_start = week_start_for()
    with connect() as conn:
        snap = conn.execute(
            """
            SELECT id FROM weekly_snapshots
            WHERE scope = ? AND account_id IS ? AND week_start = ?
            """,
            (scope, account_id, week_start),
        ).fetchone()
        if not snap:
            return {"updated": 0, "week_start": week_start}
        snapshot_id = int(snap["id"])
        rows = conn.execute(
            "SELECT * FROM weekly_positions WHERE snapshot_id = ?", (snapshot_id,)
        ).fetchall()

        updated = 0
        for row in rows:
            symbol = row["symbol"]
            exchange = row["exchange"]
            new_ltp = price_fetcher(symbol, exchange)
            if new_ltp is None or new_ltp <= 0:
                continue
            qty = float(row["quantity"])
            invested = float(row["invested"])
            current_value = round(qty * new_ltp, 2)
            pnl = round(current_value - invested, 2)
            pnl_pct = round((pnl / invested * 100) if invested else 0.0, 2)
            conn.execute(
                """
                UPDATE weekly_positions SET
                    last_price = ?, current_value = ?, pnl = ?, pnl_pct = ?
                WHERE id = ?
                """,
                (round(new_ltp, 4), current_value, pnl, pnl_pct, row["id"]),
            )
            updated += 1

        positions = conn.execute(
            "SELECT invested, current_value, pnl FROM weekly_positions WHERE snapshot_id = ?",
            (snapshot_id,),
        ).fetchall()
        summary = _summarize_positions(
            [
                {
                    "invested": r["invested"],
                    "current_value": r["current_value"],
                    "pnl": r["pnl"],
                }
                for r in positions
            ]
        )
        conn.execute(
            """
            UPDATE weekly_snapshots SET
                holdings_count = ?, total_invested = ?, total_current = ?,
                total_pnl = ?, total_pnl_pct = ?, captured_at = ?
            WHERE id = ?
            """,
            (
                summary["holdings_count"],
                summary["total_invested"],
                summary["total_current"],
                summary["total_pnl"],
                summary["total_pnl_pct"],
                time.time(),
                snapshot_id,
            ),
        )

    return {"updated": updated, "week_start": week_start, **summary}


def _row_to_snapshot(snap: sqlite3.Row, positions: list[sqlite3.Row]) -> dict[str, Any]:
    out = _decode_snapshot_row(snap)
    out["positions"] = []
    for row in positions:
        pos = dict(row)
        if pos.get("extra_json"):
            try:
                pos["extra"] = json.loads(pos["extra_json"])
            except json.JSONDecodeError:
                pos["extra"] = {}
        out["positions"].append(pos)
    return out


def _decode_snapshot_row(row: sqlite3.Row) -> dict[str, Any]:
    out = dict(row)
    out["comparable_to_previous"] = bool(out.get("comparable_to_previous"))
    raw = out.pop("comparability_reasons_json", None)
    try:
        out["comparability_reasons"] = json.loads(raw) if raw else []
    except json.JSONDecodeError:
        out["comparability_reasons"] = []
    return out


def _metadata_from_mapping(meta: dict[str, Any]) -> dict[str, Any]:
    return {
        key: meta.get(key)
        for key in (
            "sync_run_id",
            "sync_stage",
            "snapshot_quality",
            "accounts_expected",
            "accounts_included",
            "live_accounts",
            "cached_accounts",
            "manual_current_accounts",
            "coverage_pct",
            "position_as_of_min",
            "price_as_of_min",
            "market_session_date",
            "comparable_to_previous",
            "comparability_reasons",
        )
    }


def _add_snapshot_metadata_columns(conn: sqlite3.Connection) -> None:
    existing = {
        str(row["name"]) for row in conn.execute("PRAGMA table_info(weekly_snapshots)")
    }
    definitions = {
        "sync_run_id": "TEXT",
        "sync_stage": "TEXT",
        "snapshot_quality": "TEXT NOT NULL DEFAULT 'UNKNOWN'",
        "accounts_expected": "INTEGER",
        "accounts_included": "INTEGER",
        "live_accounts": "INTEGER",
        "cached_accounts": "INTEGER",
        "manual_current_accounts": "INTEGER",
        "coverage_pct": "REAL",
        "position_as_of_min": "TEXT",
        "price_as_of_min": "TEXT",
        "market_session_date": "TEXT",
        "comparable_to_previous": "INTEGER NOT NULL DEFAULT 0",
        "comparability_reasons_json": "TEXT NOT NULL DEFAULT '[]'",
    }
    for name, definition in definitions.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE weekly_snapshots ADD COLUMN {name} {definition}")


def latest_snapshot(
    *,
    scope: str,
    account_id: str | None,
) -> dict[str, Any] | None:
    rows = list_snapshots(scope=scope, account_id=account_id, limit=1)
    if not rows:
        return None
    return get_snapshot(rows[0]["id"])


def weekly_status() -> dict[str, Any]:
    """Summary for UI — confirms SQLite weekly history is populated."""
    family_rows = list_snapshots(scope="family", account_id=None, limit=52)
    sarwa_row = list_snapshots(scope="account", account_id="sarwa", limit=1)
    sarwa_detail = latest_snapshot(scope="account", account_id="sarwa")
    return {
        "db_path": str(DB_PATH),
        "current_week": week_start_for(),
        "family_weeks_stored": len(family_rows),
        "latest_family_week": family_rows[0]["week_start"] if family_rows else None,
        "latest_family_value_inr": family_rows[0]["total_current"] if family_rows else None,
        "sarwa_weeks_stored": len(sarwa_row),
        "latest_sarwa_week": sarwa_row[0]["week_start"] if sarwa_row else None,
        "sarwa_positions": len(sarwa_detail["positions"]) if sarwa_detail else 0,
        "sarwa_value_inr": sarwa_row[0]["total_current"] if sarwa_row else None,
    }


def _cleanup_duplicate_family_weeks(conn: sqlite3.Connection) -> None:
    """Repair historical family/week duplicates before adding the partial unique index."""
    duplicate_weeks = conn.execute(
        """
        SELECT week_start
        FROM weekly_snapshots
        WHERE scope = 'family' AND account_id IS NULL
        GROUP BY week_start
        HAVING COUNT(*) > 1
        """
    ).fetchall()
    for row in duplicate_weeks:
        week_start = row["week_start"]
        snaps = conn.execute(
            """
            SELECT id, captured_at, total_current
            FROM weekly_snapshots
            WHERE scope = 'family' AND account_id IS NULL AND week_start = ?
            ORDER BY captured_at DESC, id DESC
            """,
            (week_start,),
        ).fetchall()
        account_total_row = conn.execute(
            """
            SELECT SUM(total_current) AS total
            FROM weekly_snapshots
            WHERE scope = 'account' AND week_start = ?
            """,
            (week_start,),
        ).fetchone()
        account_total = float(account_total_row["total"] or 0) if account_total_row else 0.0
        best = (
            min(
                snaps,
                key=lambda snap: (
                    abs(float(snap["total_current"] or 0) - account_total),
                    -float(snap["captured_at"] or 0),
                    -int(snap["id"]),
                ),
            )
            if account_total > 0
            else snaps[0]
        )
        drop_ids = [
            int(snap["id"])
            for snap in snaps
            if int(snap["id"]) != int(best["id"])
        ]
        if not drop_ids:
            continue
        placeholders = ",".join("?" for _ in drop_ids)
        conn.execute(
            f"DELETE FROM weekly_positions WHERE snapshot_id IN ({placeholders})", drop_ids
        )
        conn.execute(
            f"DELETE FROM weekly_snapshots WHERE id IN ({placeholders})", drop_ids
        )
