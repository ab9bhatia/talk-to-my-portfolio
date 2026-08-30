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


def _change_instruction(item: Any, readiness: DecisionReadiness) -> str:
    current = float(item.family_weight_pct or 0)
    target = float(item.target_weight_pct or current)
    delta = target - current
    if readiness is not DecisionReadiness.READY_TO_REVIEW:
        return "0% change until the readiness gate is cleared."
    if item.action in {Action.ADD, Action.STRONG_ADD}:
        return f"Increase from {current:.1f}% to {target:.1f}% ({delta:+.1f} pp), staged."
    if item.action is Action.REDUCE:
        return f"Trim about {item.sell_pct:.0f}%; target {target:.1f}% of the family portfolio."
    if item.action is Action.SELL:
        return "Exit 100%; target 0% of the family portfolio."
    return "0% change; keep the current family weight."


def _execution_instruction(item: Any, readiness: DecisionReadiness) -> tuple[str, str]:
    pattern = item.chart_pattern
    if readiness is not DecisionReadiness.READY_TO_REVIEW:
        return (
            f"Clear the {READINESS_LABELS[readiness].lower()} gate before preparing an order.",
            "Execution gated",
        )
    if not pattern or not pattern.active:
        if item.action in {Action.ADD, Action.STRONG_ADD}:
            return "Use a staged entry within the configured position limit; do not chase.", "No active setup"
        if item.action is Action.REDUCE:
            return "Stage the supported trim across liquid sessions.", "No active setup"
        if item.action is Action.SELL:
            return "Stage the supported exit after account-level checks.", "No active setup"
        return "No order is required; monitor the review trigger.", "No active setup"

    label = pattern.label
    if pattern.bias == "bullish" and item.action in {Action.REDUCE, Action.SELL}:
        qualifier = (
            "the fundamental exit remains unchanged"
            if item.sell_type.value == "FUNDAMENTAL_SELL"
            else "stage the supported trim or exit without reversing it"
        )
        return (
            f"{label} is bullish timing context only; {qualifier}.",
            "Timing differs — decision unchanged",
        )
    if pattern.bias == "bearish" and item.action in {Action.ADD, Action.STRONG_ADD}:
        return (
            f"{label} is bearish timing context: wait for stabilization and use staged entry; the add decision is unchanged.",
            "Timing differs — decision unchanged",
        )
    if pattern.bias == "bullish" and item.action in {Action.ADD, Action.STRONG_ADD}:
        return f"Use the {label} setup for entry timing; stage the add and do not chase.", "Timing supports decision"
    if pattern.bias == "bearish" and item.action in {Action.REDUCE, Action.SELL}:
        return f"{label} supports earlier staged execution of the existing decision.", "Timing supports decision"
    return f"Use {label} as timing context only; it does not change the decision.", "Timing context only"


def conflict_categories(item: Any) -> list[ConflictCategory]:
    categories: list[ConflictCategory] = []
    if any(flag.blocking or flag.severity == "error" for flag in item.data_quality_flags):
        categories.append(ConflictCategory.DATA_BLOCKS_DECISION)
    if item.decision_conflicts:
        categories.append(ConflictCategory.TIMING_VS_DECISION)
    if item.requires_ca_review and item.action in {Action.REDUCE, Action.SELL}:
        categories.append(ConflictCategory.TAX_BLOCKS_EXECUTION)
    external = item.external_analyst_view
    if external and external.sentiment is not ExternalAnalystSentiment.UNKNOWN:
        adding = item.action in {Action.ADD, Action.STRONG_ADD}
        reducing = item.action in {Action.REDUCE, Action.SELL}
        if (adding and external.sentiment is ExternalAnalystSentiment.NEGATIVE) or (
            reducing and external.sentiment is ExternalAnalystSentiment.POSITIVE
        ):
            categories.append(ConflictCategory.EXTERNAL_CONTEXT_DIFFERS)
    return categories


def present_decision(item: Any) -> tuple[DecisionPresentation, SignalStack]:
    readiness = _readiness(item)
    label, short, instruction = _display_label(item, readiness)
    change_instruction = _change_instruction(item, readiness)
    execution_instruction, timing_label = _execution_instruction(item, readiness)
    external = item.external_analyst_view
    external_summary = "No covered external analyst view."
    external_state = "UNAVAILABLE"
    if external:
        external_state = external.status.value
        pieces = [external.sentiment.value.replace("_", " ").title()]
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
        change_instruction=change_instruction,
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
        authority=SignalAuthority.PRIMARY_DECISION,
        action_code=item.action,
        headline=f"{label}: {change_instruction}",
        current_weight_pct=round(float(item.family_weight_pct or 0), 2),
        target_weight_pct=round(float(item.target_weight_pct or 0), 2),
        change_pct_points=round(
            float(item.target_weight_pct or 0) - float(item.family_weight_pct or 0), 2
        ),
        sell_pct=round(float(item.sell_pct or 0), 2),
        execution_instruction=execution_instruction,
        timing_label=timing_label,
    )
    stack = SignalStack(
        primary=SignalAuthority.PRIMARY_DECISION,
        layers=[
            SignalLayer(
                authority=SignalAuthority.PRIMARY_DECISION,
                label="Portfolio decision",
                state=item.action.value,
                summary=item.why_now,
                actionable=True,
            ),
            SignalLayer(
                authority=SignalAuthority.CONTEXT_ONLY,
                label="External analyst view",
                state=external_state,
                summary=external_summary,
                actionable=False,
            ),
            SignalLayer(
                authority=SignalAuthority.EXECUTION_TIMING,
                label="Technical timing",
                state=technical_state,
                summary=technical_summary,
                actionable=False,
            ),
            SignalLayer(
                authority=(
                    SignalAuthority.BLOCKER
                    if readiness is not DecisionReadiness.READY_TO_REVIEW
                    else SignalAuthority.PRIMARY_DECISION
                ),
                label="Readiness",
                state=readiness.value,
                summary=READINESS_LABELS[readiness],
                actionable=False,
            ),
        ],
    )
    return presentation, stack
