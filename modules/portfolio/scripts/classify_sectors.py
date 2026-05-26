#!/usr/bin/env python3
"""Classify missing / generic-ETF sectors via LLM (uses sector_llm_cache.db)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import modules.portfolio.config  # noqa: F401 — loads .env

from modules.portfolio.db import sector_llm_cache
from modules.portfolio.services.portfolio import fetch_family_portfolio, invalidate_portfolio_cache
from modules.portfolio.services.sector_llm import classify_holdings_llm, llm_available


def main() -> int:
    parser = argparse.ArgumentParser(description="LLM sector classification for portfolio holdings")
    parser.add_argument("--force", action="store_true", help="Re-classify even when cached")
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Re-fetch brokers + Yahoo before classifying (slow)",
    )
    args = parser.parse_args()

    sector_llm_cache.init_db()

    if not llm_available():
        print("OPENAI_API_KEY not set (or SECTOR_LLM_ENABLED=false).", file=sys.stderr)
        return 1

    family = fetch_family_portfolio(refresh=args.refresh, stale_ok=True)
    holdings = [h for p in family.get("portfolios", []) for h in p.get("holdings", [])]
    before = [h for h in holdings if not (h.get("sector") or "").strip()]
    print(f"Holdings: {len(holdings)}, unclassified before: {len(before)}")

    stats = classify_holdings_llm(holdings, force=args.force)
    after = [h for h in holdings if not (h.get("sector") or "").strip()]
    invalidate_portfolio_cache()

    print("Stats:", stats)
    print(f"Unclassified after: {len(after)}")
    if after:
        print("Still missing sector:")
        for h in sorted(after, key=lambda x: (x.get("symbol") or ""))[:60]:
            print(
                f"  {h.get('symbol')} ({h.get('exchange')}) "
                f"broker={h.get('broker')} class={h.get('asset_class')}"
            )
    return 0 if not after else 2


if __name__ == "__main__":
    raise SystemExit(main())
