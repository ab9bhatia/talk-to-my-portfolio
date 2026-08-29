"""Fund/scheme master and dated constituent observations."""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from modules.portfolio.paths import DATA_DIR


DB_PATH = DATA_DIR / "fund_intelligence.db"
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
            CREATE TABLE IF NOT EXISTS fund_schemes (
                instrument_id TEXT PRIMARY KEY,
                canonical_scheme_name TEXT NOT NULL,
                isin TEXT,
                ticker TEXT,
                amc_issuer TEXT,
                scheme_plan TEXT,
                scheme_option TEXT,
                domicile TEXT,
                currency TEXT NOT NULL,
                underlying_index_category TEXT,
                aum REAL,
                ter_pct REAL,
                tracking_error_pct REAL,
                tracking_difference_pct REAL,
                inception_date TEXT,
                manager TEXT,
                manager_tenure_years REAL,
                exit_load TEXT,
                bid_ask_spread_pct REAL,
                average_traded_value REAL,
                premium_discount_pct REAL,
                rebalance_schedule TEXT,
                factsheet_source TEXT NOT NULL,
                factsheet_as_of TEXT NOT NULL,
                instrument_type TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_fund_scheme_variant
                ON fund_schemes(amc_issuer, canonical_scheme_name, scheme_plan, scheme_option);

            CREATE TABLE IF NOT EXISTS fund_constituents (
                constituent_id INTEGER PRIMARY KEY AUTOINCREMENT,
                fund_instrument_id TEXT NOT NULL,
                underlying_instrument_id TEXT,
                unresolved_label TEXT,
                weight_pct REAL NOT NULL,
                as_of TEXT NOT NULL,
                source TEXT NOT NULL,
                source_type TEXT NOT NULL,
                coverage_type TEXT NOT NULL,
                coverage_pct REAL NOT NULL,
                sector TEXT,
                market_cap TEXT,
                factor_style TEXT,
                promoter_group TEXT,
                created_at REAL NOT NULL,
                UNIQUE(fund_instrument_id, as_of, underlying_instrument_id, unresolved_label),
                FOREIGN KEY (fund_instrument_id) REFERENCES fund_schemes(instrument_id)
            );
            CREATE INDEX IF NOT EXISTS idx_fund_constituents_latest
                ON fund_constituents(fund_instrument_id, as_of DESC);
            """
        )


def upsert_scheme(row: dict[str, Any]) -> dict[str, Any]:
    required = ("instrument_id", "canonical_scheme_name", "currency", "factsheet_source", "factsheet_as_of", "instrument_type")
    missing = [key for key in required if not str(row.get(key) or "").strip()]
    if missing:
        raise ValueError(f"Missing fund scheme fields: {', '.join(missing)}")
    now = time.time()
    columns = (
        "instrument_id", "canonical_scheme_name", "isin", "ticker", "amc_issuer",
        "scheme_plan", "scheme_option", "domicile", "currency", "underlying_index_category",
        "aum", "ter_pct", "tracking_error_pct", "tracking_difference_pct", "inception_date",
        "manager", "manager_tenure_years", "exit_load", "bid_ask_spread_pct",
        "average_traded_value", "premium_discount_pct", "rebalance_schedule",
        "factsheet_source", "factsheet_as_of", "instrument_type",
    )
    values = [row.get(column) for column in columns]
    with connect() as conn:
        conn.execute(
            f"""
            INSERT INTO fund_schemes ({', '.join(columns)}, created_at, updated_at)
            VALUES ({', '.join('?' for _ in columns)}, ?, ?)
            ON CONFLICT(instrument_id) DO UPDATE SET
                canonical_scheme_name = excluded.canonical_scheme_name,
                isin = excluded.isin, ticker = excluded.ticker, amc_issuer = excluded.amc_issuer,
                scheme_plan = excluded.scheme_plan, scheme_option = excluded.scheme_option,
                domicile = excluded.domicile, currency = excluded.currency,
                underlying_index_category = excluded.underlying_index_category,
                aum = excluded.aum, ter_pct = excluded.ter_pct,
                tracking_error_pct = excluded.tracking_error_pct,
                tracking_difference_pct = excluded.tracking_difference_pct,
                inception_date = excluded.inception_date, manager = excluded.manager,
                manager_tenure_years = excluded.manager_tenure_years,
                exit_load = excluded.exit_load, bid_ask_spread_pct = excluded.bid_ask_spread_pct,
                average_traded_value = excluded.average_traded_value,
                premium_discount_pct = excluded.premium_discount_pct,
                rebalance_schedule = excluded.rebalance_schedule,
                factsheet_source = excluded.factsheet_source,
                factsheet_as_of = excluded.factsheet_as_of,
                instrument_type = excluded.instrument_type, updated_at = excluded.updated_at
            """,
            (*values, now, now),
        )
    return get_scheme(row["instrument_id"]) or row


def get_scheme(instrument_id: str) -> dict[str, Any] | None:
    init_db()
    with connect() as conn:
        row = conn.execute("SELECT * FROM fund_schemes WHERE instrument_id = ?", (instrument_id,)).fetchone()
    return dict(row) if row else None


def list_schemes() -> list[dict[str, Any]]:
    init_db()
    with connect() as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM fund_schemes ORDER BY canonical_scheme_name, instrument_id").fetchall()]


def save_constituents(
    *, fund_instrument_id: str, as_of: str, rows: list[dict[str, Any]], source: str,
    source_type: str, coverage_type: str, coverage_pct: float,
) -> dict[str, Any]:
    if get_scheme(fund_instrument_id) is None:
        raise ValueError("Fund scheme must exist before constituent ingestion.")
    normalized_coverage = max(0.0, min(100.0, float(coverage_pct)))
    with connect() as conn:
        for row in rows:
            underlying = str(row.get("underlying_instrument_id") or "").strip() or None
            unresolved = str(row.get("unresolved_label") or "").strip() or None
            if not underlying and not unresolved:
                raise ValueError("Every constituent requires an underlying ID or unresolved label.")
            conn.execute(
                """
                INSERT OR IGNORE INTO fund_constituents (
                    fund_instrument_id, underlying_instrument_id, unresolved_label, weight_pct,
                    as_of, source, source_type, coverage_type, coverage_pct, sector,
                    market_cap, factor_style, promoter_group, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fund_instrument_id, underlying, unresolved, float(row.get("weight_pct") or 0),
                    as_of, source, source_type, coverage_type, normalized_coverage,
                    row.get("sector"), row.get("market_cap"), row.get("factor_style"),
                    row.get("promoter_group"), time.time(),
                ),
            )
    return {"fund_instrument_id": fund_instrument_id, "as_of": as_of, "rows": len(rows), "coverage_type": coverage_type, "coverage_pct": normalized_coverage}


def latest_constituents(fund_instrument_id: str) -> list[dict[str, Any]]:
    init_db()
    with connect() as conn:
        latest = conn.execute(
            "SELECT max(as_of) AS as_of FROM fund_constituents WHERE fund_instrument_id = ?",
            (fund_instrument_id,),
        ).fetchone()["as_of"]
        if not latest:
            return []
        rows = conn.execute(
            "SELECT * FROM fund_constituents WHERE fund_instrument_id = ? AND as_of = ? ORDER BY weight_pct DESC, constituent_id",
            (fund_instrument_id, latest),
        ).fetchall()
    return [dict(row) for row in rows]


def all_constituents() -> list[dict[str, Any]]:
    init_db()
    with connect() as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM fund_constituents ORDER BY fund_instrument_id, as_of, weight_pct DESC").fetchall()]
