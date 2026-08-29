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


def _carry_forward_amount(
    current: float | None,
    *,
    previous: float | None,
    has_snapshot: bool,
) -> tuple[float | None, bool]:
    """
    When a date has no snapshot, reuse the previous amount instead of zero.

    If a snapshot exists but reports 0 while we already had a non-zero balance,
    treat that as missing data as well (common with sparse sheet imports).
    """
    if current is None or not has_snapshot:
        if previous is not None:
            return previous, True
        return None, False
    value = float(current)
    if value == 0 and previous not in (None, 0):
        return previous, True
    return value, False


def _forward_fill_growth_series(series: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Carry family totals forward across days with missing snapshots."""
    out: list[dict[str, Any]] = []
    prev_current: float | None = None
    prev_invested: float | None = None
    prev_pnl: float | None = None
    prev_pnl_pct: float | None = None

    for row in series:
        carried = False
        current, cf_cur = _carry_forward_amount(
            row.get("total_current"), previous=prev_current, has_snapshot=True
        )
        invested, cf_inv = _carry_forward_amount(
            row.get("total_invested"), previous=prev_invested, has_snapshot=True
        )
        carried = cf_cur or cf_inv

        pnl = row.get("total_pnl")
        pnl_pct = row.get("total_pnl_pct")
        if carried and prev_pnl is not None:
            pnl = prev_pnl
            pnl_pct = prev_pnl_pct

        if current is not None:
            prev_current = current
        if invested is not None:
            prev_invested = float(invested)
        if pnl is not None:
            prev_pnl = float(pnl)
        if pnl_pct is not None:
            prev_pnl_pct = float(pnl_pct)

        out.append(
            {
                **row,
                "total_current": current,
                "total_invested": invested,
                "total_pnl": pnl,
                "total_pnl_pct": pnl_pct,
                "carried_forward": carried,
            }
        )
    return out


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
    series = _forward_fill_growth_series(
        daily_history.growth_series(scope="family", account_id=None, days=days)
    )

    latest_day = series[-1]["day_date"] if series else None
    previous_day = series[-2]["day_date"] if len(series) >= 2 else None

    day_change: dict[str, Any] | None = None
    if len(series) >= 2:
        cur = series[-1]
        prev = series[-2]
        v_cur = float(cur["total_current"])
        v_prev = float(prev["total_current"])
        comparable = bool(cur.get("comparable_to_previous"))
        reasons = list(cur.get("comparability_reasons") or [])
        day_change = {
            "latest_day": cur["day_date"],
            "previous_day": prev["day_date"],
            "value": v_cur,
            "prev_value": v_prev,
            "change": round(v_cur - v_prev, 2) if comparable else None,
            "change_pct": _pct_change(v_cur, v_prev) if comparable else None,
            "invested_change": (
                round(float(cur["total_invested"]) - float(prev["total_invested"]), 2)
                if comparable
                else None
            ),
            "comparable": comparable,
            "comparability_reasons": reasons,
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
            "comparable": False,
            "comparability_reasons": ["NO_PREVIOUS_SNAPSHOT"],
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

    if latest_day and previous_day and bool(series[-1].get("comparable_to_previous")):
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

    non_comparable = [
        {
            "day_date": row.get("day_date"),
            "snapshot_quality": row.get("snapshot_quality") or "UNKNOWN",
            "reasons": row.get("comparability_reasons") or [],
        }
        for row in series[1:]
        if not row.get("comparable_to_previous")
    ]
    degraded = [
        {
            "day_date": row.get("day_date"),
            "snapshot_quality": row.get("snapshot_quality") or "UNKNOWN",
            "coverage_pct": row.get("coverage_pct"),
        }
        for row in series
        if (row.get("snapshot_quality") or "UNKNOWN") not in {"COMPLETE_LIVE", "COMPLETE_MIXED"}
    ]
    performance_quality = {
        "claims_allowed": len(series) >= 2 and not non_comparable,
        "non_comparable_points": non_comparable,
        "degraded_points": degraded,
        "explanation": (
            "Best/worst-period and return claims are suppressed because account coverage changed or quality metadata is unavailable."
            if non_comparable
            else None
        ),
    }

    return {
        "status": status,
        "days": days,
        "series": series,
        "day_change": day_change,
        "breakdown": breakdown,
        "account_series": account_series,
        "timeline_table": timeline_table,
        "benchmark_series": benchmark_series,
        "performance_quality": performance_quality,
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
    filled_by_day: dict[str, dict[str, dict[str, Any]]] = {d: {} for d in day_dates}
    account_series: list[dict[str, Any]] = []
    for aid in ordered_accounts:
        meta = account_meta[aid]
        last_value: float | None = None
        last_invested: float | None = None
        points: list[dict[str, Any]] = []
        for day in day_dates:
            raw = by_day[day].get(aid)
            has_snapshot = raw is not None
            raw_value = float(raw["value"]) if raw and raw.get("value") is not None else None
            raw_invested = float(raw["invested"]) if raw and raw.get("invested") is not None else None

            value, cf_value = _carry_forward_amount(
                raw_value, previous=last_value, has_snapshot=has_snapshot
            )
            invested, cf_invested = _carry_forward_amount(
                raw_invested, previous=last_invested, has_snapshot=has_snapshot
            )
            carried = cf_value or cf_invested
            if value is not None:
                last_value = value
            if invested is not None:
                last_invested = invested

            cell = {
                "value": round(value, 2) if value is not None else None,
                "invested": round(invested, 2) if invested is not None else None,
                "source": "carried_forward" if carried else (raw or {}).get("source"),
                "carried_forward": carried,
            }
            filled_by_day[day][aid] = cell
            points.append(
                {
                    "day_date": day,
                    "total_current": cell["value"],
                    "total_invested": cell["invested"],
                    "source": cell["source"],
                    "carried_forward": carried,
                }
            )
        account_series.append({**meta, "series": points})

    family_map = {str(s["day_date"]): s for s in family_series}
    timeline_table: list[dict[str, Any]] = []
    for day in day_dates:
        fam = family_map.get(day, {})
        timeline_table.append(
            {
                "day_date": day,
                "family_value": fam.get("total_current"),
                "family_invested": fam.get("total_invested"),
                "family_pnl_pct": fam.get("total_pnl_pct"),
                "source": fam.get("source"),
                "carried_forward": bool(fam.get("carried_forward")),
                "snapshot_quality": fam.get("snapshot_quality") or "UNKNOWN",
                "coverage_pct": fam.get("coverage_pct"),
                "comparable_to_previous": bool(fam.get("comparable_to_previous")),
                "comparability_reasons": fam.get("comparability_reasons") or [],
                "accounts": {
                    account_meta[aid]["code"]: filled_by_day[day].get(
                        aid, {"value": None, "invested": None}
                    )
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
        # Newer yfinance releases return a one-item Series here when the
        # download has MultiIndex columns, even for a single ticker.
        if hasattr(val, "iloc"):
            if val.empty:
                continue
            val = val.iloc[0]
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
