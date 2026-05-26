#!/usr/bin/env python3
"""List Zerodha mutual fund holdings (Kite /mf/holdings) for AB / RB / SB."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from modules.portfolio.config import get_account_code, resolve_account_ref
from modules.portfolio.services.zerodha_mf import fetch_mf_holdings


def main() -> int:
    ref = sys.argv[1] if len(sys.argv) > 1 else "AB"
    try:
        account_id = resolve_account_ref(ref)
        code = get_account_code(account_id)
        holdings = fetch_mf_holdings(account_id)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"{code} ({account_id}): {len(holdings)} mutual fund holding(s)")
    for h in holdings:
        print(
            f"  {h.get('fund_name', '?')[:60]:<60} "
            f"₹{h.get('current_value', 0):>12,.2f}  "
            f"({h.get('quantity', 0):.3f} units)"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
