"""Normalize chart-pattern output into bounded advisory timing evidence."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from modules.portfolio.services.advisory.models import (
    ChartPatternEvidence,
    DataQualityFlag,
    Evidence,
)


ACTIVE_LIFECYCLE_STATES = {"CONFIRMED", "RETESTING"}
MIN_HEURISTIC_SCORE = 55
MAX_AGE_DAYS = 10

_LEGACY_LIFECYCLE = {
    "early": "BUILDING",
    "forming": "NEAR_BREAKOUT",
    "confirmed": "CONFIRMED",
}
_US_EXCHANGES = {"US", "NASDAQ", "NYSE", "ARCA", "AMEX", "BATS"}


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
    heuristic_score = float(primary.get("heuristic_score", primary.get("confidence") or 0))
    confidence = float(primary.get("confidence", heuristic_score) or 0)
    lifecycle_state = str(
        primary.get("lifecycle_state") or _LEGACY_LIFECYCLE.get(status, "BUILDING")
    ).upper()
    target_status = str(primary.get("target_status") or "ACTIVE").upper()
    target_price = (
        float(primary["target_price"])
        if primary.get("target_price") is not None
        else None
    )
    current_price = primary.get("current_price", primary.get("last_price", holding.get("last_price")))
    current_price = float(current_price) if current_price is not None else None
    if target_price is not None and current_price is not None and target_status == "ACTIVE":
        if bias == "bullish" and current_price >= target_price:
            target_status = "OVERSHOT" if current_price >= target_price * 1.03 else "ACHIEVED"
            lifecycle_state = f"TARGET_{target_status}"
        elif bias == "bearish" and current_price <= target_price:
            target_status = "OVERSHOT" if current_price <= target_price * 0.97 else "ACHIEVED"
            lifecycle_state = f"TARGET_{target_status}"
    exchange = str(holding.get("exchange") or "NSE").upper()
    currency = str(
        primary.get("currency")
        or holding.get("currency")
        or holding.get("base_currency")
        or ("USD" if exchange in _US_EXCHANGES else "INR")
    ).upper()
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
        active = (
            bias in {"bullish", "bearish"}
            and lifecycle_state in ACTIVE_LIFECYCLE_STATES
            and target_status == "ACTIVE"
            and heuristic_score >= MIN_HEURISTIC_SCORE
            and not stale
        )
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
        lifecycle_state=lifecycle_state,
        target_status=target_status,
        status=status or "unknown",
        confidence=round(confidence, 2),
        heuristic_score=round(heuristic_score, 2),
        confidence_semantics=str(primary.get("confidence_semantics") or "heuristic_shape_score"),
        calibrated_target_hit_probability=(
            float(primary["calibrated_target_hit_probability"])
            if primary.get("calibrated_target_hit_probability") is not None
            else None
        ),
        sample_size=(
            int(primary["sample_size"])
            if primary.get("sample_size") is not None
            else None
        ),
        as_of=str(as_of) if as_of else None,
        currency=currency,
        current_price=current_price,
        target_price=target_price,
        measured_target=(
            float(primary.get("measured_target", target_price))
            if primary.get("measured_target", target_price) is not None
            else None
        ),
        target_date=str(primary["target_date"]) if primary.get("target_date") else None,
        upside_to_target_pct=float(primary["upside_to_target_pct"]) if primary.get("upside_to_target_pct") is not None else None,
        remaining_upside_pct=(
            float(primary["remaining_upside_pct"])
            if primary.get("remaining_upside_pct") is not None
            else (
                max(0.0, (target_price - current_price) / current_price * 100)
                if target_status == "ACTIVE"
                and bias == "bullish"
                and target_price is not None
                and current_price
                else 0.0
            )
        ),
        remaining_downside_pct=(
            float(primary["remaining_downside_pct"])
            if primary.get("remaining_downside_pct") is not None
            else (
                max(0.0, (current_price - target_price) / current_price * 100)
                if target_status == "ACTIVE"
                and bias == "bearish"
                and target_price is not None
                and current_price
                else 0.0
            )
        ),
        estimated_horizon=dict(primary.get("estimated_horizon") or {}),
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
                    f"{normalized.label} is {normalized.lifecycle_state} with a "
                    f"{normalized.heuristic_score:.0f}/100 shape score and target "
                    f"{normalized.target_status}; {decision_use}."
                ),
                source="local deterministic chart-pattern detector using market price history",
                as_of=str(as_of),
                source_type="market_data",
            )
        )
    return normalized, evidence, flags
