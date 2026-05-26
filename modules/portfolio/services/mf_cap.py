"""Infer equity MF cap bucket (Large / Mid / Small / Multi-cap) from scheme name."""

from __future__ import annotations

import re


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip().upper())


def classify_mf_cap(fund_name: str | None) -> str | None:
    """
    Map mutual fund scheme name to cap bucket for portfolio grouping.
    Debt / liquid / arbitrage schemes return None (no equity cap sleeve).
    """
    n = _norm(fund_name)
    if not n:
        return None

    padded = f" {n} "

    if any(
        k in padded
        for k in (
            " LIQUID ",
            " OVERNIGHT ",
            " GILT ",
            " DEBT ",
            " BOND ",
            " MONEY MARKET ",
            " ARBITRAGE ",
            " BANKING AND PSU ",
            " CORPORATE BOND ",
            " CREDIT RISK ",
            " FLOATING RATE ",
            " SHORT DURATION ",
            " LOW DURATION ",
            " ULTRA SHORT ",
        )
    ):
        return None

    if any(
        k in n
        for k in (
            "MULTI CAP",
            "MULTICAP",
            "MULTI-CAP",
            "FLEXI CAP",
            "FLEXICAP",
            "FLEXI-CAP",
            " CONTRA ",
            "LARGE & MID",
            "LARGE AND MID",
            "AGGRESSIVE HYBRID",
            "DYNAMIC ASSET",
            "ASSET ALLOCATION",
            "BALANCED ADVANTAGE",
            "BALANCED HYBRID",
        )
    ):
        return "Multi-cap"

    if "ELSS" in n or "TAX SAVER" in n:
        if "SMALL" in n and "CAP" in n:
            return "Small"
        if "MID" in n and "CAP" in n:
            return "Mid"
        if "LARGE" in n and "CAP" in n:
            return "Large"
        return "Multi-cap"

    if "INDEX" in n or " NIFTY" in padded or n.startswith("NIFTY"):
        if "SMALL" in n and "CAP" in n:
            return "Small"
        if "MID" in n and "CAP" in n:
            return "Mid"
        if any(k in n for k in ("NEXT 50", "NIFTY 50", "NIFTY50", "SENSEX", "NIFTY 100", "NIFTY100")):
            return "Large"
        if any(k in n for k in ("500", "TOTAL MARKET", "ALL CAP", "COMPLETE", "EQUAL WEIGHT")):
            return "Multi-cap"

    if "SMALL" in n and "CAP" in n:
        return "Small"
    if "MID" in n and "CAP" in n:
        return "Mid"
    if "LARGE" in n and "CAP" in n:
        return "Large"

    if any(k in n for k in ("SECTOR", "THEMATIC", "BUSINESS CYCLE")):
        return "Multi-cap"

    return None
