"""Day-over-day portfolio growth breakdowns for the Growth dashboard."""

from __future__ import annotations

from typing import Any

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

    return {
        "status": status,
        "days": days,
        "series": series,
        "day_change": day_change,
        "breakdown": breakdown,
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
