#!/usr/bin/env python3
"""Refresh LTPs on current-week snapshots via Yahoo (no broker API)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.portfolio.config import get_enabled_accounts, get_enabled_groww_accounts, get_enabled_sarwa_accounts
from modules.portfolio.db import weekly_history
from modules.portfolio.services.weekly_recorder import refresh_all_current_week_ltps


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh current-week snapshot LTPs")
    parser.add_argument("--scope", choices=("family", "all"), default="all")
    args = parser.parse_args()

    weekly_history.init_db()
    if args.scope == "family":
        from modules.portfolio.services.weekly_recorder import refresh_current_week_ltps_for_scope

        result = refresh_current_week_ltps_for_scope(scope="family", account_id=None)
        print(result)
        return 0

    account_ids = (
        list(get_enabled_accounts())
        + list(get_enabled_groww_accounts())
        + list(get_enabled_sarwa_accounts())
    )
    results = refresh_all_current_week_ltps(account_ids)
    for row in results:
        print(row)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
