#!/usr/bin/env python3
"""Phase 0 spike — Groww auth + holdings + LTP (run from repo root)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from modules.portfolio.auth.groww import GrowwError, get_access_token, get_groww_client
from modules.portfolio.config import get_groww_credentials
from modules.portfolio.services.groww_portfolio import fetch_groww_holdings, fetch_groww_portfolio


def main() -> int:
    account_id = sys.argv[1] if len(sys.argv) > 1 else "groww1"
    try:
        creds = get_groww_credentials(account_id)
        print(f"Auth method: {creds['auth_method']}")
        token = get_access_token(account_id, force_refresh=True)
        print(f"Access token OK ({len(token)} chars)")

        client = get_groww_client(account_id)
        raw = client.get_holdings_for_user(timeout=30)
        rows = raw.get("holdings") if isinstance(raw, dict) else raw
        print(f"Raw holdings count: {len(rows) if isinstance(rows, list) else '?'}")

        holdings = fetch_groww_holdings(account_id)
        print(f"Normalized holdings: {len(holdings)}")
        if holdings:
            print("Sample:", json.dumps(holdings[0], indent=2))

        portfolio = fetch_groww_portfolio(account_id, with_metrics=False)
        print(
            "Summary:",
            json.dumps(portfolio["summary"], indent=2),
        )
        return 0
    except (GrowwError, RuntimeError, KeyError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
