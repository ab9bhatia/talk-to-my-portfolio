"""Day-over-day portfolio growth breakdowns for the Growth dashboard."""

from __future__ import annotations

from functools import lru_cache
from datetime import datetime
from typing import Any

import yfinance as yf

from modules.portfolio.config import get_account, get_account_code
from modules.portfolio.db import daily_history


def _pct_change(current: float, previous: float) -> float | None:
    if not previous:
        return None
    return round((current - previous) / previous * 100, 2)


def _delta_row(
    *,
    key: str,
    label: str,
    value: float,
    prev_value: float,
) -> dict[str, Any]:
    change = round(value - prev_value, 2)
    return {
        "key": key,
        "label": label,
        "value": round(value, 2),
        "prev_value": round(prev_value, 2),
        "change": change,
        "change_pct": _pct_change(value, prev_value),
    }


def _group_positions(
    positions: list[dict[str, Any]],
    field: str,
    *,
    default_label: str = "Unknown",
) -> dict[str, float]:
    totals: dict[str, float] = {}
    for p in positions:
        raw = p.get(field)
        label = (str(raw).strip() if raw else "") or default_label
        totals[label] = totals.get(label, 0.0) + float(p.get("current_value") or 0)
    return totals


def build_growth_dashboard(*, days: int = 90) -> dict[str, Any]:
    """Family-level series plus day-over-day breakdown by account, cap, asset class."""
    status = daily_history.daily_status()
    series = daily_history.growth_series(scope="family", account_id=None, days=days)

    latest_day = series[-1]["day_date"] if series else None
    previous_day = series[-2]["day_date"] if len(series) >= 2 else None

    day_change: dict[str, Any] | None = None
    if len(series) >= 2:
        cur = series[-1]
        prev = series[-2]
        v_cur = float(cur["total_current"])
        v_prev = float(prev["total_current"])
        day_change = {
            "latest_day": cur["day_date"],
            "previous_day": prev["day_date"],
            "value": v_cur,
            "prev_value": v_prev,
            "change": round(v_cur - v_prev, 2),
            "change_pct": _pct_change(v_cur, v_prev),
            "invested_change": round(float(cur["total_invested"]) - float(prev["total_invested"]), 2),
        }
    elif len(series) == 1:
        cur = series[0]
        day_change = {
            "latest_day": cur["day_date"],
            "previous_day": None,
            "value": float(cur["total_current"]),
            "prev_value": None,
            "change": None,
            "change_pct": None,
            "invested_change": None,
        }

    breakdown: dict[str, list[dict[str, Any]]] = {
        "by_account": [],
        "by_market_cap": [],
        "by_asset_class": [],
        "by_sector": [],
    }
    account_series: list[dict[str, Any]] = []
    timeline_table: list[dict[str, Any]] = []
    benchmark_series: dict[str, list[dict[str, Any]]] = {}

    if latest_day and previous_day:
        cur_family = daily_history.snapshot_for_day(
            scope="family", account_id=None, day_date=latest_day
        )
        prev_family = daily_history.snapshot_for_day(
            scope="family", account_id=None, day_date=previous_day
        )
        cur_pos = (cur_family or {}).get("positions") or []
        prev_pos = (prev_family or {}).get("positions") or []

        for field, default, out_key in (
            ("market_cap", "Unknown", "by_market_cap"),
            ("asset_class", "equity", "by_asset_class"),
            ("sector", "Unknown", "by_sector"),
        ):
            cur_map = _group_positions(cur_pos, field, default_label=default)
            prev_map = _group_positions(prev_pos, field, default_label=default)
            keys = sorted(set(cur_map) | set(prev_map), key=lambda k: cur_map.get(k, 0), reverse=True)
            rows = []
            for key in keys:
                rows.append(
                    _delta_row(
                        key=key,
                        label=key,
                        value=cur_map.get(key, 0.0),
                        prev_value=prev_map.get(key, 0.0),
                    )
                )
            breakdown[out_key] = rows

        account_snaps_cur = _account_totals_for_day(latest_day)
        account_snaps_prev = _account_totals_for_day(previous_day)
        account_ids = sorted(
            set(account_snaps_cur) | set(account_snaps_prev),
            key=lambda aid: account_snaps_cur.get(aid, 0),
            reverse=True,
        )
        for aid in account_ids:
            try:
                acc = get_account(aid)
                code = get_account_code(aid)
                label = (acc.get("label") or code).strip()
            except KeyError:
                label = aid
                code = aid
            breakdown["by_account"].append(
                _delta_row(
                    key=aid,
                    label=f"{code} — {label}" if label != code else code,
                    value=account_snaps_cur.get(aid, 0.0),
                    prev_value=account_snaps_prev.get(aid, 0.0),
                )
            )

    if series:
        account_series, timeline_table = _account_matrix_for_days(series)
        benchmark_series = _benchmark_series_for_days(series)

    return {
        "status": status,
        "days": days,
        "series": series,
        "day_change": day_change,
        "breakdown": breakdown,
        "account_series": account_series,
        "timeline_table": timeline_table,
        "benchmark_series": benchmark_series,
    }


def _account_totals_for_day(day_date: str) -> dict[str, float]:
    from modules.portfolio.db.daily_history import connect

    with connect() as conn:
        rows = conn.execute(
            """
            SELECT account_id, total_current FROM daily_snapshots
            WHERE scope = 'account' AND day_date = ?
            """,
            (day_date,),
        ).fetchall()
    return {r["account_id"]: float(r["total_current"]) for r in rows if r["account_id"]}


def _account_matrix_for_days(
    family_series: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    day_dates = [str(s["day_date"]) for s in family_series if s.get("day_date")]
    if not day_dates:
        return [], []

    placeholders = ",".join("?" for _ in day_dates)
    with daily_history.connect() as conn:
        rows = conn.execute(
            f"""
            SELECT day_date, account_id, total_current, total_invested, source
            FROM daily_snapshots
            WHERE scope = 'account'
              AND day_date IN ({placeholders})
            ORDER BY day_date ASC, account_id ASC
            """,
            day_dates,
        ).fetchall()

    by_day: dict[str, dict[str, dict[str, Any]]] = {d: {} for d in day_dates}
    account_meta: dict[str, dict[str, str]] = {}
    latest_by_account: dict[str, float] = {}

    for r in rows:
        aid = str(r["account_id"] or "").strip()
        day = str(r["day_date"] or "")
        if not aid or day not in by_day:
            continue
        try:
            acc = get_account(aid)
            code = get_account_code(aid)
            label = (acc.get("label") or code).strip()
        except KeyError:
            code = aid
            label = aid
        account_meta[aid] = {"account_id": aid, "code": code, "label": label}
        value = float(r["total_current"] or 0)
        invested = float(r["total_invested"] or 0)
        by_day[day][aid] = {
            "value": round(value, 2),
            "invested": round(invested, 2),
            "source": r["source"],
        }
        latest_by_account[aid] = value

    ordered_accounts = sorted(
        account_meta.keys(),
        key=lambda aid: latest_by_account.get(aid, 0.0),
        reverse=True,
    )
    account_series: list[dict[str, Any]] = []
    for aid in ordered_accounts:
        meta = account_meta[aid]
        account_series.append(
            {
                **meta,
                "series": [
                    {
                        "day_date": day,
                        "total_current": by_day[day].get(aid, {}).get("value"),
                        "total_invested": by_day[day].get(aid, {}).get("invested"),
                        "source": by_day[day].get(aid, {}).get("source"),
                    }
                    for day in day_dates
                ],
            }
        )

    family_map = {str(s["day_date"]): s for s in family_series}
    timeline_table: list[dict[str, Any]] = []
    for day in day_dates:
        fam = family_map.get(day, {})
        timeline_table.append(
            {
                "day_date": day,
                "family_value": float(fam.get("total_current") or 0),
                "family_invested": float(fam.get("total_invested") or 0),
                "family_pnl_pct": float(fam.get("total_pnl_pct") or 0),
                "source": fam.get("source"),
                "accounts": {
                    account_meta[aid]["code"]: by_day[day].get(aid, {"value": None, "invested": None})
                    for aid in ordered_accounts
                },
            }
        )
    return account_series, timeline_table


def _as_date(day_str: str):
    return datetime.strptime(day_str, "%Y-%m-%d").date()


@lru_cache(maxsize=24)
def _benchmark_close_series(symbol: str, start: str, end: str) -> list[tuple[str, float]]:
    df = yf.download(symbol, start=start, end=end, progress=False, auto_adjust=True)
    if df is None or df.empty:
        return []
    out: list[tuple[str, float]] = []
    close_col = "Close"
    for idx, row in df.iterrows():
        val = row.get(close_col)
        if val is None:
            continue
        out.append((idx.date().isoformat(), float(val)))
    return out


def _nearest_price(prices: list[tuple[str, float]], day: str) -> float | None:
    best: float | None = None
    for d, v in prices:
        if d <= day:
            best = v
        else:
            break
    return best


def _benchmark_series_for_days(series: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    if len(series) < 2:
        return {}
    days = [str(s["day_date"]) for s in series if s.get("day_date")]
    if len(days) < 2:
        return {}
    start = min(days)
    end = max(days)
    benchmark_map = {
        "NIFTY50": "^NSEI",
        "SNP500": "^GSPC",
    }
    out: dict[str, list[dict[str, Any]]] = {}
    for label, symbol in benchmark_map.items():
        prices = _benchmark_close_series(symbol, start, end)
        if not prices:
            continue
        base = _nearest_price(prices, days[0])
        if not base:
            continue
        points: list[dict[str, Any]] = []
        for day in days:
            px = _nearest_price(prices, day)
            if px is None:
                points.append({"day_date": day, "index": None})
            else:
                points.append({"day_date": day, "index": round(px / base * 100, 2)})
        out[label] = points
    return out
