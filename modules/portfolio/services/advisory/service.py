"""Orchestrate deterministic Advisor V2 recommendations from the canonical family payload."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from modules.portfolio.services.advisory.expected_return import expected_three_year_irr
from modules.portfolio.services.advisory.features import assess_features
from modules.portfolio.services.advisory.models import (
    AccountPosition,
    Action,
    AdvisoryPortfolio,
    DataQualityFlag,
    HoldingRecommendation,
    InstrumentType,
    SellType,
    to_primitive,
)
from modules.portfolio.services.advisory.momentum import analyze_momentum
from modules.portfolio.services.advisory.overlap import consolidate_family, detect_overlap
from modules.portfolio.services.advisory.provenance import as_of_text, evidence_for_holding
from modules.portfolio.services.advisory.rules import select_action
from modules.portfolio.services.advisory.tax import assess_tax_and_settlement


SCHEMA_VERSION = "advisor-v2-milestone-1"


def _flag(code: str, severity: str, message: str, *, blocking: bool = False) -> DataQualityFlag:
    return DataQualityFlag(code, severity, message, blocking)


def _dedupe_flags(flags: list[DataQualityFlag]) -> list[DataQualityFlag]:
    out: list[DataQualityFlag] = []
    seen: set[str] = set()
    for flag in flags:
        if flag.code not in seen:
            seen.add(flag.code)
            out.append(flag)
    return out


def _operational_flags(
    holding: dict[str, Any],
    *,
    family_stale: bool,
) -> list[DataQualityFlag]:
    flags: list[DataQualityFlag] = []
    if not holding.get("symbol") or holding.get("symbol_resolved") is False:
        flags.append(
            _flag(
                "UNRESOLVED_SYMBOL",
                "error",
                "Security identity is unresolved.",
                blocking=True,
            )
        )
    if holding.get("corporate_action_pending") or holding.get("cost_basis_unreconciled"):
        flags.append(
            _flag(
                "CORPORATE_ACTION_RECONCILIATION",
                "error",
                "Corporate-action quantity or cost allocation requires reconciliation.",
                blocking=True,
            )
        )
    if holding.get("is_suspended") is True or holding.get("is_tradable") is False:
        flags.append(
            _flag(
                "SUSPENDED_OR_UNTRADEABLE",
                "error",
                "The position is suspended or otherwise not market-tradeable.",
                blocking=True,
            )
        )
    if not holding.get("last_price") or float(holding.get("last_price") or 0) <= 0:
        flags.append(
            _flag(
                "MISSING_CURRENT_PRICE",
                "error",
                "Current price is missing.",
                blocking=True,
            )
        )
    if family_stale:
        flags.append(_flag("STALE_PORTFOLIO_SNAPSHOT", "warning", "Portfolio snapshot is stale."))
    if holding.get("governance_risk") in {"high", "broken"} and not holding.get("governance_event"):
        flags.append(
            _flag(
                "GOVERNANCE_EVIDENCE_MISSING",
                "error",
                "High governance risk is asserted without a dated event.",
                blocking=True,
            )
        )
    return flags


def _business_thesis(holding: dict[str, Any]) -> str:
    if holding.get("business_thesis"):
        return str(holding["business_thesis"])
    return "UNKNOWN: the current dataset has no sourced business thesis."


def _conditions(
    holding: dict[str, Any],
    *,
    action: Action,
    max_position_pct: float,
) -> tuple[list[str], list[str]]:
    adds = list(holding.get("add_conditions") or [])
    exits = list(holding.get("exit_triggers") or [])
    if not adds and action in {Action.ADD, Action.STRONG_ADD}:
        adds = [
            "Sourced base-case three-year IRR remains in the applicable add band.",
            f"Family weight remains below the {max_position_pct:.1f}% position limit.",
        ]
    if not exits:
        exits = [
            "Sourced governance, solvency, or structural business impairment.",
            "Base-case expected return falls below the hold hurdle after refreshed assumptions.",
        ]
    return adds, exits


def _account_positions(holding: dict[str, Any]) -> list[AccountPosition]:
    return [
        AccountPosition(
            account_id=str(row["account_id"]),
            account_code=str(row["account_code"]),
            broker=str(row["broker"]),
            quantity=float(row["quantity"]),
            current_value=float(row["current_value"]),
            account_weight_pct=float(row["account_weight_pct"]),
        )
        for row in holding.get("positions") or []
    ]


def _recommendation(
    holding: dict[str, Any],
    *,
    portfolio_as_of: Any,
    family_stale: bool,
    max_position_pct: float,
    cooldown_active: bool,
    overlap_symbols: list[str],
) -> HoldingRecommendation:
    expected, return_flags = expected_three_year_irr(holding)
    momentum, momentum_flags = analyze_momentum(holding)
    holding["momentum_regime"] = momentum.regime
    evidence, provenance_flags = evidence_for_holding(holding, portfolio_as_of=portfolio_as_of)
    flags = _operational_flags(holding, family_stale=family_stale)
    flags.extend(return_flags)
    flags.extend(momentum_flags)
    flags.extend(provenance_flags)
    has_overlap = bool(overlap_symbols)
    if has_overlap:
        flags.append(
            _flag(
                "REDUNDANT_FUND_SLEEVE",
                "warning",
                f"Explicit mandate/index overlap with: {', '.join(overlap_symbols)}.",
            )
        )
    if holding.get("instrument_type") in {InstrumentType.ETF, InstrumentType.MUTUAL_FUND}:
        flags.append(
            _flag(
                "LOOKTHROUGH_UNAVAILABLE",
                "info",
                "Dated constituent-level look-through was not supplied.",
            )
        )

    assessment = assess_features(
        holding,
        expected_return=expected,
        momentum=momentum,
        max_position_pct=max_position_pct,
        has_overlap=has_overlap,
    )
    if assessment.scores.feature_coverage_pct < 50:
        flags.append(
            _flag(
                "LOW_FEATURE_COVERAGE",
                "warning",
                "Only "
                f"{assessment.scores.feature_coverage_pct:.1f}% of weighted features are covered.",
            )
        )
    flags = _dedupe_flags(flags)
    decision = select_action(
        holding,
        expected_return=expected,
        assessment=assessment,
        flags=flags,
        max_position_pct=max_position_pct,
        cooldown_active=cooldown_active,
        has_overlap=has_overlap,
    )
    tax = assess_tax_and_settlement(holding, action=decision.action)
    flags = _dedupe_flags(flags + tax.flags)
    confidence = decision.confidence
    if any(flag.blocking for flag in tax.flags):
        confidence = min(confidence, 60)
    add_conditions, exit_triggers = _conditions(
        holding,
        action=decision.action,
        max_position_pct=max_position_pct,
    )
    positions = _account_positions(holding)
    return HoldingRecommendation(
        symbol=str(holding.get("symbol") or "UNKNOWN"),
        instrument_type=holding.get("instrument_type") or InstrumentType.EQUITY,
        accounts=positions,
        consolidated_qty=float(holding.get("consolidated_qty") or 0),
        consolidated_value=float(holding.get("consolidated_value") or 0),
        family_weight_pct=float(holding.get("family_weight_pct") or 0),
        account_weights={row.account_code: row.account_weight_pct for row in positions},
        action=decision.action,
        sell_type=decision.sell_type,
        action_confidence=confidence,
        sell_pct=decision.sell_pct,
        target_weight_pct=decision.target_weight_pct,
        expected_3y_irr=expected,
        scores=assessment.scores,
        momentum_regime=momentum.regime,
        momentum=momentum,
        business_thesis=_business_thesis(holding),
        why_now=decision.why_now,
        hold_until=decision.hold_until,
        add_conditions=add_conditions,
        exit_triggers=exit_triggers,
        tax_note=tax.tax_note,
        settlement_note=tax.settlement_note,
        replacement_plan=[],
        evidence=evidence,
        data_quality_flags=flags,
        rule_trace=decision.rule_trace,
        feature_coverage_pct=assessment.scores.feature_coverage_pct,
    )


def build_advisory_payload(
    family: dict[str, Any],
    *,
    goals: dict[str, Any] | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Return the versioned, JSON-safe deterministic advisory payload."""
    goals = goals or {}
    max_position_pct = float(goals.get("max_position_pct") or 15)
    turnover = float(family.get("recent_turnover_pct") or 0)
    cooldown_threshold = float(goals.get("turnover_cooldown_threshold_pct") or 15)
    cooldown_active = turnover >= cooldown_threshold
    consolidated = consolidate_family(family)
    overlap_report, overlap_by_security = detect_overlap(consolidated)
    portfolio_as_of = family.get("cached_at")
    recommendations = [
        _recommendation(
            holding,
            portfolio_as_of=portfolio_as_of,
            family_stale=bool(family.get("stale")),
            max_position_pct=max_position_pct,
            cooldown_active=cooldown_active,
            overlap_symbols=overlap_by_security.get(holding["security_key"], []),
        )
        for holding in consolidated
    ]

    proceeds: dict[str, float] = {}
    for recommendation in recommendations:
        if recommendation.sell_pct <= 0:
            continue
        for position in recommendation.accounts:
            proceeds[position.account_code] = round(
                proceeds.get(position.account_code, 0.0)
                + position.current_value * (recommendation.sell_pct / 100),
                2,
            )

    payload = AdvisoryPortfolio(
        schema_version=SCHEMA_VERSION,
        generated_at=generated_at or datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        source_portfolio_cached_at=as_of_text(portfolio_as_of),
        xirr_status=(
            "calculation_deferred_requires_validated_cashflows"
            if family.get("cashflows")
            else "unavailable_without_cashflows"
        ),
        recommendations=recommendations,
        full_exit_queue=[
            item.symbol
            for item in recommendations
            if item.action is Action.SELL and item.sell_pct == 100
        ],
        partial_reduction_queue=[
            item.symbol for item in recommendations if item.action is Action.REDUCE
        ],
        conditional_hold_queue=[
            item.symbol
            for item in recommendations
            if item.action in {Action.WATCH, Action.HOLD_NO_ADD, Action.RECONCILE}
        ],
        add_build_queue=[
            item.symbol
            for item in recommendations
            if item.action in {Action.ADD, Action.STRONG_ADD}
        ],
        target_sleeve_allocation=[],
        proceeds_by_account=proceeds,
        reinvestment_plan=[],
        overlap_report=overlap_report,
        cooldown_warning=(
            f"Recent turnover is {turnover:.1f}%; optional rotations are suppressed "
            "during cooldown."
            if cooldown_active
            else None
        ),
    )
    return to_primitive(payload)
