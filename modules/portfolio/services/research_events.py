"""Sourced result, corporate, regulatory, and ownership event contracts."""

from __future__ import annotations

from datetime import date
from typing import Any, Protocol


class ResearchEventProvider(Protocol):
    def fetch_events(self, *, instrument_id: str, as_of: str) -> list[dict[str, Any]]: ...


def assess_event(row: dict[str, Any], *, as_of: str) -> dict[str, Any]:
    source_as_of = str(row.get("source_as_of") or "")
    try:
        age_days = (date.fromisoformat(as_of) - date.fromisoformat(source_as_of)).days
    except ValueError:
        age_days = 9999
    stale = age_days > (35 if row.get("event_type") == "OWNERSHIP_CHANGE" else 7)
    verified = bool(row.get("verified") and row.get("source"))
    return {
        **row,
        "age_days": age_days,
        "stale": stale,
        "eligible_for_action_change": verified and not stale,
        "exclusion_reason": (
            "UNVERIFIED_EVENT" if not verified else "STALE_EVENT_EVIDENCE" if stale else None
        ),
    }


def candidate_is_recommendable(candidate: dict[str, Any] | None) -> bool:
    return bool(candidate and candidate.get("research_status") == "APPROVED")
