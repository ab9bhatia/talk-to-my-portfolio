"""Normalize chart-pattern output into bounded advisory timing evidence."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from modules.portfolio.services.advisory.models import (
    ChartPatternEvidence,
    DataQualityFlag,
    Evidence,
)


ACTIVE_STATUSES = {"confirmed", "forming"}
MIN_CONFIDENCE = 55
MAX_AGE_DAYS = 10


def _as_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = datetime.strptime(text[:10], "%Y-%m-%d").replace(tzinfo=UTC)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def pattern_evidence_for_holding(
    holding: dict[str, Any],
    *,
    now: datetime | None = None,
) -> tuple[ChartPatternEvidence | None, list[Evidence], list[DataQualityFlag]]:
    """Return one primary, dated pattern signal; invalid/stale rows never affect actions."""
    now = now or datetime.now(UTC)
    scan = holding.get("chart_patterns") or {}
    patterns = scan.get("patterns") if isinstance(scan, dict) else scan
    primary = scan.get("primary") if isinstance(scan, dict) else None
    if not primary and isinstance(patterns, list) and patterns:
        primary = patterns[0]
    if not isinstance(primary, dict):
        return None, [], []

    bias = str(primary.get("bias") or "").lower()
    status = str(primary.get("status") or "").lower()
    confidence = float(primary.get("confidence") or 0)
    as_of = primary.get("as_of")
    observed_at = _as_datetime(as_of)
    flags: list[DataQualityFlag] = []
    if not observed_at:
        flags.append(
            DataQualityFlag(
                code="CHART_PATTERN_DATE_MISSING",
                severity="warning",
                message="Chart-pattern evidence has no valid as-of date and cannot affect execution.",
            )
        )
        active = False
        stale = True
    else:
        stale = observed_at < now - timedelta(days=MAX_AGE_DAYS)
        active = bias in {"bullish", "bearish"} and status in ACTIVE_STATUSES and confidence >= MIN_CONFIDENCE and not stale
        if stale:
            flags.append(
                DataQualityFlag(
                    code="STALE_CHART_PATTERN",
                    severity="warning",
                    message=f"Chart-pattern evidence is older than {MAX_AGE_DAYS} days and is excluded from decisions.",
                )
            )

    normalized = ChartPatternEvidence(
        pattern=str(primary.get("pattern") or primary.get("label") or "unknown"),
        label=str(primary.get("label") or primary.get("pattern") or "Unknown pattern"),
        bias=bias or "unknown",
        status=status or "unknown",
        confidence=round(confidence, 2),
        as_of=str(as_of) if as_of else None,
        target_price=float(primary["target_price"]) if primary.get("target_price") is not None else None,
        target_date=str(primary["target_date"]) if primary.get("target_date") else None,
        upside_to_target_pct=float(primary["upside_to_target_pct"]) if primary.get("upside_to_target_pct") is not None else None,
        note=str(primary.get("note") or ""),
        active=active,
        stale=stale,
    )
    evidence: list[Evidence] = []
    if as_of:
        decision_use = (
            "eligible for execution-timing adjustments"
            if active
            else "recorded but excluded from action changes"
        )
        evidence.append(
            Evidence(
                claim=(
                    f"{normalized.label} is {normalized.status} with "
                    f"{normalized.confidence:.0f}% detector confidence; {decision_use}."
                ),
                source="local deterministic chart-pattern detector using market price history",
                as_of=str(as_of),
                source_type="market_data",
            )
        )
    return normalized, evidence, flags
