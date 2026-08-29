"""Structured, redacted research context for optional LLM explanations."""

from __future__ import annotations

from typing import Any


ALLOWED_SCORECARD_KEYS = {
    "instrument_id", "symbol", "display_name", "instrument_type", "adapter",
    "total_score", "dimensions", "data_coverage_pct", "missing_evidence",
    "evidence_as_of", "methodology_version",
}


def build_research_llm_context(
    *, scorecards: list[dict[str, Any]], screen_result: dict[str, Any] | None = None
) -> dict[str, Any]:
    redacted = [
        {key: value for key, value in scorecard.items() if key in ALLOWED_SCORECARD_KEYS}
        for scorecard in scorecards
    ]
    result: dict[str, Any] = {
        "schema_version": "research-context-v1",
        "scorecards": redacted,
        "policy": "Explain deterministic structured evidence only; do not invent scores, filings, ownership changes, or targets.",
    }
    if screen_result:
        result["screen_summary"] = {
            "matched_instrument_ids": [row.get("instrument_id") for row in screen_result.get("matches") or []],
            "eliminated": screen_result.get("eliminated") or [],
        }
    return result
