#!/usr/bin/env python3
"""Record daily portfolio snapshots (family + each account)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.portfolio.db import daily_history
from modules.portfolio.services.daily_recorder import record_today_from_family
from modules.portfolio.services.portfolio import fetch_family_portfolio


def main() -> int:
    parser = argparse.ArgumentParser(description="Record daily portfolio snapshots")
    parser.add_argument("--refresh", action="store_true", help="Fetch live brokers before recording")
    args = parser.parse_args()

    daily_history.init_db()
    family = fetch_family_portfolio(refresh=args.refresh, stale_ok=not args.refresh)
    snaps = record_today_from_family(family, source="cli")
    if not snaps:
        print("No holdings to record")
        return 1

    for snap in snaps:
        print(
            f"{snap['scope']}/{snap.get('account_id') or 'all'} "
            f"day={snap['day_date']} holdings={snap['holdings_count']} "
            f"value=₹{snap['total_current']:,.0f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
