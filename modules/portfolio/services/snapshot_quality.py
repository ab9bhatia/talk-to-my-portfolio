"""Pure snapshot-quality and comparability rules shared by daily and weekly history."""

from __future__ import annotations

from typing import Any


LIVE_STATES = frozenset({"LIVE_RECONCILED", "LIVE_WITH_WARNINGS"})
MANUAL_CURRENT_STATES = frozenset({"MANUAL_CURRENT"})
CACHED_STATES = frozenset({"CACHED_POSITIONS_FRESH_PRICES"})
STALE_STATES = frozenset({"STALE_POSITIONS", "MANUAL_STALE"})
EXCLUDED_STATES = frozenset({"AUTH_REQUIRED", "IMPORT_REQUIRED", "FAILED"})


def snapshot_metadata(
    *,
    run_id: str,
    stage: str,
    market_session_date: str,
    accounts: list[dict[str, Any]],
    previous: dict[str, Any] | None,
) -> dict[str, Any]:
    """Describe coverage and whether value movement is safe to compare."""
    expected = len(accounts)
    included = [item for item in accounts if item.get("status") not in EXCLUDED_STATES]
    live = [item for item in included if item.get("status") in LIVE_STATES]
    cached = [item for item in included if item.get("status") in CACHED_STATES]
    manual_current = [
        item for item in included if item.get("status") in MANUAL_CURRENT_STATES
    ]
    stale = [item for item in included if item.get("status") in STALE_STATES]
    coverage = round((len(included) / expected * 100) if expected else 0.0, 2)

    if stale:
        quality = "STALE"
    elif len(included) < expected:
        quality = "PARTIAL"
    elif cached:
        quality = "COMPLETE_MIXED"
    else:
        quality = "COMPLETE_LIVE"

    comparable, reasons = comparability(previous, expected=expected, included=len(included))
    if quality == "STALE":
        comparable = False
        if "CURRENT_SNAPSHOT_STALE" not in reasons:
            reasons.append("CURRENT_SNAPSHOT_STALE")
    position_dates = sorted(
        str(item["position_as_of"])
        for item in included
        if item.get("position_as_of")
    )
    price_dates = sorted(
        str(item["price_as_of"]) for item in included if item.get("price_as_of")
    )
    return {
        "sync_run_id": run_id,
        "sync_stage": stage,
        "snapshot_quality": quality,
        "accounts_expected": expected,
        "accounts_included": len(included),
        "live_accounts": len(live),
        "cached_accounts": len(cached),
        "manual_current_accounts": len(manual_current),
        "coverage_pct": coverage,
        "position_as_of_min": position_dates[0] if position_dates else None,
        "price_as_of_min": price_dates[0] if price_dates else None,
        "market_session_date": market_session_date,
        "comparable_to_previous": comparable,
        "comparability_reasons": reasons,
    }


def comparability(
    previous: dict[str, Any] | None,
    *,
    expected: int,
    included: int,
) -> tuple[bool, list[str]]:
    if previous is None:
        return False, ["NO_PREVIOUS_SNAPSHOT"]
    prior_expected = previous.get("accounts_expected")
    prior_included = previous.get("accounts_included")
    prior_quality = str(previous.get("snapshot_quality") or "UNKNOWN")
    if prior_expected is None or prior_included is None or prior_quality == "UNKNOWN":
        return False, ["PREVIOUS_QUALITY_METADATA_UNAVAILABLE"]

    reasons: list[str] = []
    if int(prior_expected) != expected:
        reasons.append("EXPECTED_ACCOUNT_SET_CHANGED")
    if int(prior_included) != included:
        reasons.append("INCLUDED_ACCOUNT_COVERAGE_CHANGED")
    if prior_quality in {"PARTIAL", "STALE"}:
        reasons.append(f"PREVIOUS_SNAPSHOT_{prior_quality}")
    return not reasons, reasons
