"""Record daily snapshots from live portfolio fetches."""

from __future__ import annotations

import logging
from typing import Any

from modules.portfolio.db import daily_history

logger = logging.getLogger(__name__)


def record_positions_snapshot(
    *,
    scope: str,
    account_id: str | None,
    positions: list[dict[str, Any]],
    source: str,
    day_date: str | None = None,
    notes: str | None = None,
    usd_inr: float | None = None,
) -> dict[str, Any]:
    return daily_history.save_snapshot(
        scope=scope,
        account_id=account_id,
        positions=positions,
        source=source,
        day_date=day_date,
        usd_inr=usd_inr,
        notes=notes,
    )


def record_family_from_payload(family: dict[str, Any], *, source: str = "live") -> list[dict[str, Any]]:
    """Save family + per-account daily snapshots (upserts today)."""
    from modules.portfolio.services.holdings_view import aggregate_holdings_across_accounts

    results: list[dict[str, Any]] = []
    all_holdings: list[dict[str, Any]] = []
    for portfolio in family.get("portfolios") or []:
        holdings = list(portfolio.get("holdings") or [])
        all_holdings.extend(holdings)
        aid = portfolio.get("account_id")
        if aid:
            results.append(
                record_positions_snapshot(
                    scope="account",
                    account_id=aid,
                    positions=holdings,
                    source=source,
                )
            )
    family_positions = (
        aggregate_holdings_across_accounts(all_holdings) if all_holdings else []
    )
    results.insert(
        0,
        record_positions_snapshot(
            scope="family",
            account_id=None,
            positions=family_positions,
            source=source,
        ),
    )
    return results


def record_today_from_family(family: dict[str, Any], *, source: str = "live") -> list[dict[str, Any]]:
    """Always upsert today's daily snapshot (call after live broker refresh)."""
    if not any(p.get("holdings") for p in family.get("portfolios") or []):
        return []
    return record_family_from_payload(family, source=source)


def seed_today_if_missing(family: dict[str, Any], *, source: str = "bootstrap") -> list[dict[str, Any]] | None:
    """Create today's snapshot when none exists yet (e.g. first visit to Growth tab)."""
    today = daily_history.day_date_for()
    existing = daily_history.list_snapshots(scope="family", account_id=None, limit=1)
    if existing and existing[0]["day_date"] == today:
        return None
    return record_today_from_family(family, source=source)
