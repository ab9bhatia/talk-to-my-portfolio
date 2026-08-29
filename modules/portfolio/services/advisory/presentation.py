"""One deterministic mapping from engine output to user-facing decisions."""

from __future__ import annotations

from typing import Any

from modules.portfolio.services.advisory.models import (
    Action,
    ConfidenceBand,
    ConflictCategory,
    DecisionPresentation,
    DecisionReadiness,
    ExternalAnalystSentiment,
    ExternalAnalystView,
    SignalAuthority,
    SignalLayer,
    SignalStack,
)


ACTION_LABELS: dict[Action, tuple[str, str]] = {
    Action.STRONG_ADD: ("Add more", "Add"),
    Action.ADD: ("Add gradually", "Add"),
    Action.HOLD: ("Hold", "Hold"),
    Action.HOLD_NO_ADD: ("Hold — no new money", "No new money"),
    Action.CAP: ("Hold — position full", "Position full"),
    Action.WATCH: ("Wait for evidence", "Wait"),
    Action.REDUCE: ("Trim gradually", "Trim"),
    Action.SELL: ("Exit", "Exit"),
    Action.RECONCILE: ("Fix data first", "Fix data"),
}

READINESS_LABELS: dict[DecisionReadiness, str] = {
    DecisionReadiness.READY_TO_REVIEW: "Ready to review",
    DecisionReadiness.RESEARCH_REQUIRED: "Research required",
    DecisionReadiness.DATA_BLOCKED: "Data blocked",
    DecisionReadiness.TAX_REVIEW_REQUIRED: "Tax review required",
    DecisionReadiness.NOT_EXECUTABLE: "Not executable",
    DecisionReadiness.MONITOR_ONLY: "Monitor only",
}


def _confidence_band(confidence: int) -> ConfidenceBand:
    if confidence >= 80:
        return ConfidenceBand.HIGH
    if confidence >= 60:
        return ConfidenceBand.MEDIUM
    return ConfidenceBand.LOW


def _review_trigger(item: Any) -> str:
    hold_until = item.hold_until or {}
    value = str(hold_until.get("value") or "").strip()
    if value:
        return value
    if item.exit_triggers:
        return item.exit_triggers[0]
    return "Refresh after the next material result or thesis change."


def _readiness(item: Any) -> DecisionReadiness:
    codes = {flag.code for flag in item.data_quality_flags}
    if "SUSPENDED_OR_UNTRADEABLE" in codes:
        return DecisionReadiness.NOT_EXECUTABLE
    if item.action is Action.RECONCILE or any(flag.blocking for flag in item.data_quality_flags):
        return DecisionReadiness.DATA_BLOCKED
    if item.requires_ca_review and item.action in {Action.REDUCE, Action.SELL}:
        return DecisionReadiness.TAX_REVIEW_REQUIRED
    if item.action in {Action.ADD, Action.STRONG_ADD, Action.REDUCE, Action.SELL}:
        if item.evidence_state != "DOCUMENTED_MODEL":
            return DecisionReadiness.RESEARCH_REQUIRED
        return DecisionReadiness.READY_TO_REVIEW
    return DecisionReadiness.MONITOR_ONLY


def _display_label(item: Any, readiness: DecisionReadiness) -> tuple[str, str, str]:
    label, short = ACTION_LABELS[item.action]
    if readiness is DecisionReadiness.NOT_EXECUTABLE:
        return "Not currently executable", "Not executable", "Confirm tradability before considering any transaction."
    if readiness is DecisionReadiness.DATA_BLOCKED:
        return "Fix data first", "Fix data", "Resolve the blocking data issue before using this decision."
    if readiness is DecisionReadiness.TAX_REVIEW_REQUIRED:
        return "Tax review first", "Tax review", "Complete the account-level tax and settlement review first."
    if readiness is DecisionReadiness.RESEARCH_REQUIRED:
        if item.action in {Action.ADD, Action.STRONG_ADD}:
            return "Research before adding", "Research", "Validate current filings before adding any capital."
        if item.action is Action.REDUCE:
            return "Review for a possible trim", "Review trim", "Validate the thesis and tax constraints before trimming."
        if item.action is Action.SELL:
            return "Research before exiting", "Research", "Validate authoritative exit evidence before acting."
    if item.action in {Action.ADD, Action.STRONG_ADD}:
        instruction = "Review a staged add within the configured position limit."
    elif item.action is Action.REDUCE:
        instruction = f"Review a staged trim of about {item.sell_pct:.0f}%."
    elif item.action is Action.SELL:
        instruction = "Review a full exit after account-level checks."
    else:
        instruction = "No transaction is required now; monitor the review trigger."
    return label, short, instruction


def conflict_categories(item: Any) -> list[ConflictCategory]:
    categories: list[ConflictCategory] = []
    if any(flag.blocking or flag.severity == "error" for flag in item.data_quality_flags):
        categories.append(ConflictCategory.DATA_QUALITY)
    if item.decision_conflicts:
        categories.append(ConflictCategory.FUNDAMENTAL_VS_TECHNICAL)
    if item.requires_ca_review:
        categories.append(ConflictCategory.TAX_OR_SETTLEMENT)
    external = item.external_analyst_view
    if external and external.sentiment is not ExternalAnalystSentiment.UNKNOWN:
        adding = item.action in {Action.ADD, Action.STRONG_ADD}
        reducing = item.action in {Action.REDUCE, Action.SELL}
        if (adding and external.sentiment is ExternalAnalystSentiment.NEGATIVE) or (
            reducing and external.sentiment is ExternalAnalystSentiment.POSITIVE
        ):
            categories.append(ConflictCategory.INTERNAL_VS_EXTERNAL)
    return categories


def present_decision(item: Any) -> tuple[DecisionPresentation, SignalStack]:
    readiness = _readiness(item)
    label, short, instruction = _display_label(item, readiness)
    external = item.external_analyst_view
    external_summary = "No covered external analyst view."
    external_state = "UNAVAILABLE"
    if external:
        external_state = external.status.value
        pieces = [external.consensus_label or external.sentiment.value.title()]
        if external.target_price is not None:
            pieces.append(f"target {external.target_descriptor}")
        external_summary = " · ".join(pieces)
    technical = item.chart_pattern
    technical_summary = "No active chart setup."
    technical_state = "NEUTRAL"
    if technical:
        technical_state = technical.lifecycle_state
        technical_summary = f"{technical.label} · {technical.bias} timing context"
    presentation = DecisionPresentation(
        internal_action=item.action,
        label=label,
        short_label=short,
        readiness=readiness,
        readiness_label=READINESS_LABELS[readiness],
        confidence_band=_confidence_band(item.action_confidence),
        confidence_pct=item.action_confidence,
        do_now=instruction,
        change_instruction=instruction,
        review_trigger=_review_trigger(item),
        why=item.why_now,
        source_label=(
            "Documented deterministic model"
            if item.evidence_state == "DOCUMENTED_MODEL"
            else "Screening model — validation required"
            if item.evidence_state == "SCREENING_MODEL"
            else "Insufficient evidence"
        ),
        execution_enabled=False,
    )
    stack = SignalStack(
        primary=SignalAuthority.INTERNAL_DECISION,
        layers=[
            SignalLayer(
                authority=SignalAuthority.INTERNAL_DECISION,
                label="Portfolio decision",
                state=item.action.value,
                summary=item.why_now,
                actionable=True,
            ),
            SignalLayer(
                authority=SignalAuthority.EXTERNAL_ANALYST_CONTEXT,
                label="External analyst view",
                state=external_state,
                summary=external_summary,
                actionable=False,
            ),
            SignalLayer(
                authority=SignalAuthority.TECHNICAL_TIMING,
                label="Technical timing",
                state=technical_state,
                summary=technical_summary,
                actionable=False,
            ),
            SignalLayer(
                authority=SignalAuthority.EXECUTION_READINESS,
                label="Readiness",
                state=readiness.value,
                summary=READINESS_LABELS[readiness],
                actionable=False,
            ),
        ],
    )
    return presentation, stack
