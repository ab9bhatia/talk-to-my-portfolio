"""Daily portfolio snapshots — day-level value history and position detail."""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import date
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
            CREATE TABLE IF NOT EXISTS daily_snapshots (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                scope           TEXT NOT NULL,
                account_id      TEXT,
                day_date        TEXT NOT NULL,
                captured_at     REAL NOT NULL,
                source          TEXT NOT NULL,
                usd_inr         REAL,
                holdings_count  INTEGER NOT NULL DEFAULT 0,
                total_invested  REAL NOT NULL DEFAULT 0,
                total_current   REAL NOT NULL DEFAULT 0,
                total_pnl       REAL NOT NULL DEFAULT 0,
                total_pnl_pct   REAL NOT NULL DEFAULT 0,
                notes           TEXT,
                UNIQUE(scope, account_id, day_date)
            );

            CREATE TABLE IF NOT EXISTS daily_positions (
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
                account_id      TEXT,
                account_code    TEXT,
                extra_json      TEXT,
                FOREIGN KEY (snapshot_id) REFERENCES daily_snapshots(id) ON DELETE CASCADE,
                UNIQUE(snapshot_id, symbol, exchange)
            );

            CREATE INDEX IF NOT EXISTS idx_daily_snapshots_day
                ON daily_snapshots(day_date DESC);
            CREATE INDEX IF NOT EXISTS idx_daily_positions_snapshot
                ON daily_positions(snapshot_id);
            CREATE INDEX IF NOT EXISTS idx_daily_snapshots_scope
                ON daily_snapshots(scope, account_id, day_date DESC);
            """
        )
        _cleanup_duplicate_family_days(conn)
        # SQLite UNIQUE(scope, account_id, day_date) does not dedupe NULL account_id rows.
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_daily_family_day_unique
            ON daily_snapshots(scope, day_date)
            WHERE account_id IS NULL
            """
        )


def day_date_for(when: date | None = None) -> str:
    """ISO calendar date YYYY-MM-DD (local date)."""
    return (when or date.today()).isoformat()


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
    day_date: str | None = None,
    captured_at: float | None = None,
    usd_inr: float | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Insert or replace one daily snapshot and its positions."""
    day_date = day_date or day_date_for()
    captured_at = captured_at if captured_at is not None else time.time()
    summary = _summarize_positions(positions)

    with connect() as conn:
        conn.execute(
            """
            INSERT INTO daily_snapshots (
                scope, account_id, day_date, captured_at, source, usd_inr,
                holdings_count, total_invested, total_current, total_pnl, total_pnl_pct, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT DO UPDATE SET
                captured_at = excluded.captured_at,
                source = excluded.source,
                usd_inr = COALESCE(excluded.usd_inr, daily_snapshots.usd_inr),
                holdings_count = excluded.holdings_count,
                total_invested = excluded.total_invested,
                total_current = excluded.total_current,
                total_pnl = excluded.total_pnl,
                total_pnl_pct = excluded.total_pnl_pct,
                notes = COALESCE(excluded.notes, daily_snapshots.notes)
            """,
            (
                scope,
                account_id,
                day_date,
                captured_at,
                source,
                usd_inr,
                summary["holdings_count"],
                summary["total_invested"],
                summary["total_current"],
                summary["total_pnl"],
                summary["total_pnl_pct"],
                notes,
            ),
        )
        row = conn.execute(
            """
            SELECT id FROM daily_snapshots
            WHERE scope = ? AND account_id IS ? AND day_date = ?
            ORDER BY captured_at DESC, id DESC
            """,
            (scope, account_id, day_date),
        ).fetchone()
        snapshot_id = int(row["id"])
        conn.execute("DELETE FROM daily_positions WHERE snapshot_id = ?", (snapshot_id,))

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
                    "account_id",
                    "account_code",
                }
                and pos[k] is not None
            }
            conn.execute(
                """
                INSERT INTO daily_positions (
                    snapshot_id, symbol, exchange, currency, asset_class,
                    quantity, avg_price, last_price, invested, current_value,
                    pnl, pnl_pct, sector, market_cap, pe_ratio, rating_label,
                    account_id, account_code, extra_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    pos.get("account_id"),
                    pos.get("account_code"),
                    json.dumps(extra, default=str) if extra else None,
                ),
            )

    return {
        "snapshot_id": snapshot_id,
        "scope": scope,
        "account_id": account_id,
        "day_date": day_date,
        "captured_at": captured_at,
        "source": source,
        **summary,
    }


def get_snapshot(snapshot_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        snap = conn.execute(
            "SELECT * FROM daily_snapshots WHERE id = ?", (snapshot_id,)
        ).fetchone()
        if not snap:
            return None
        positions = conn.execute(
            "SELECT * FROM daily_positions WHERE snapshot_id = ? ORDER BY current_value DESC",
            (snapshot_id,),
        ).fetchall()
    return _row_to_snapshot(snap, positions)


def list_snapshots(
    *,
    scope: str,
    account_id: str | None = None,
    limit: int = 365,
) -> list[dict[str, Any]]:
    query_limit = limit * 4 if (scope == "family" and account_id is None) else limit
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM daily_snapshots
            WHERE scope = ? AND account_id IS ?
            ORDER BY day_date DESC, captured_at DESC, id DESC
            LIMIT ?
            """,
            (scope, account_id, query_limit),
        ).fetchall()
    out = [dict(r) for r in rows]
    if scope == "family" and account_id is None:
        dedup: list[dict[str, Any]] = []
        seen_days: set[str] = set()
        for row in out:
            day = row["day_date"]
            if day in seen_days:
                continue
            seen_days.add(day)
            dedup.append(row)
            if len(dedup) >= limit:
                break
        return dedup
    return out


def growth_series(
    *,
    scope: str,
    account_id: str | None = None,
    days: int = 90,
) -> list[dict[str, Any]]:
    """Daily totals for charts (oldest first)."""
    rows = list_snapshots(scope=scope, account_id=account_id, limit=days)
    rows.reverse()
    return [
        {
            "day_date": r["day_date"],
            "total_current": r["total_current"],
            "total_invested": r["total_invested"],
            "total_pnl": r["total_pnl"],
            "total_pnl_pct": r["total_pnl_pct"],
            "holdings_count": r["holdings_count"],
            "source": r["source"],
            "captured_at": r["captured_at"],
        }
        for r in rows
    ]


def latest_snapshot(
    *,
    scope: str,
    account_id: str | None,
) -> dict[str, Any] | None:
    rows = list_snapshots(scope=scope, account_id=account_id, limit=1)
    if not rows:
        return None
    return get_snapshot(rows[0]["id"])


def snapshot_for_day(
    *,
    scope: str,
    account_id: str | None,
    day_date: str,
) -> dict[str, Any] | None:
    rows = list_snapshots(scope=scope, account_id=account_id, limit=400)
    match = next((r for r in rows if r["day_date"] == day_date), None)
    if not match:
        return None
    return get_snapshot(match["id"])


def daily_status() -> dict[str, Any]:
    family_rows = list_snapshots(scope="family", account_id=None, limit=365)
    return {
        "db_path": str(DB_PATH),
        "today": day_date_for(),
        "family_days_stored": len(family_rows),
        "latest_day": family_rows[0]["day_date"] if family_rows else None,
        "latest_family_value_inr": family_rows[0]["total_current"] if family_rows else None,
        "earliest_day": family_rows[-1]["day_date"] if family_rows else None,
    }


def _row_to_snapshot(snap: sqlite3.Row, positions: list[sqlite3.Row]) -> dict[str, Any]:
    out = dict(snap)
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


def _cleanup_duplicate_family_days(conn: sqlite3.Connection) -> None:
    """
    Repair historical duplicates for family/day_date.
    Keep the snapshot closest to sum(account totals) for that day; fallback latest captured_at.
    """
    duplicate_days = conn.execute(
        """
        SELECT day_date
        FROM daily_snapshots
        WHERE scope = 'family' AND account_id IS NULL
        GROUP BY day_date
        HAVING COUNT(*) > 1
        """
    ).fetchall()
    for row in duplicate_days:
        day_date = row["day_date"]
        snaps = conn.execute(
            """
            SELECT id, captured_at, total_current
            FROM daily_snapshots
            WHERE scope = 'family' AND account_id IS NULL AND day_date = ?
            ORDER BY captured_at DESC, id DESC
            """,
            (day_date,),
        ).fetchall()
        if len(snaps) <= 1:
            continue
        acct_total_row = conn.execute(
            """
            SELECT SUM(total_current) AS total
            FROM daily_snapshots
            WHERE scope = 'account' AND day_date = ?
            """,
            (day_date,),
        ).fetchone()
        account_total = float(acct_total_row["total"] or 0) if acct_total_row else 0.0
        if account_total > 0:
            best = min(
                snaps,
                key=lambda s: (
                    abs(float(s["total_current"] or 0) - account_total),
                    -float(s["captured_at"] or 0),
                    -int(s["id"]),
                ),
            )
        else:
            best = snaps[0]
        keep_id = int(best["id"])
        drop_ids = [int(s["id"]) for s in snaps if int(s["id"]) != keep_id]
        if not drop_ids:
            continue
        placeholders = ",".join("?" for _ in drop_ids)
        conn.execute(
            f"DELETE FROM daily_positions WHERE snapshot_id IN ({placeholders})",
            drop_ids,
        )
        conn.execute(
            f"DELETE FROM daily_snapshots WHERE id IN ({placeholders})",
            drop_ids,
        )
