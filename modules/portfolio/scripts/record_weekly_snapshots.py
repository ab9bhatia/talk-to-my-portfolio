#!/usr/bin/env python3
"""Record weekly portfolio snapshots (family + each account)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.portfolio.db import weekly_history
from modules.portfolio.services.portfolio import fetch_family_portfolio
from modules.portfolio.services.weekly_recorder import record_family_from_payload, record_if_new_week


def main() -> int:
    parser = argparse.ArgumentParser(description="Record weekly portfolio snapshots")
    parser.add_argument("--force", action="store_true", help="Replace snapshot for current week")
    parser.add_argument("--refresh", action="store_true", help="Fetch live brokers before recording")
    args = parser.parse_args()

    weekly_history.init_db()
    family = fetch_family_portfolio(refresh=args.refresh, stale_ok=not args.refresh)
    if args.force:
        snaps = record_family_from_payload(family, source="cli")
    else:
        snaps = record_if_new_week(family, source="cli", force=False)
        if snaps is None:
            print(f"No new snapshot — week {weekly_history.week_start_for()} already recorded")
            return 0

    for snap in snaps:
        print(
            f"{snap['scope']}/{snap.get('account_id') or 'all'} "
            f"week={snap['week_start']} holdings={snap['holdings_count']} "
            f"value=₹{snap['total_current']:,.0f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
