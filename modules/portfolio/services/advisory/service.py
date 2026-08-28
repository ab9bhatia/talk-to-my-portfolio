"""Orchestrate deterministic Advisor V2 recommendations from the canonical family payload."""

from __future__ import annotations

from dataclasses import replace
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
from modules.portfolio.services.advisory.patterns import pattern_evidence_for_holding
from modules.portfolio.services.advisory.provenance import as_of_text, evidence_for_holding
from modules.portfolio.services.advisory.rules import select_action
from modules.portfolio.services.advisory.tax import assess_tax_and_settlement


SCHEMA_VERSION = "advisor-v2-v1"


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
    source_flag_details = {
        "DISPLAYED_COST_BASIS_DOES_NOT_RECONCILE": (
            "error",
            "Displayed average price does not reconcile to broker current value and P&L.",
            True,
        ),
        "SCREENSHOT_COST_BASIS_UNAVAILABLE": (
            "warning",
            "Screenshot supplies position value but not lot-level acquisition cost.",
            False,
        ),
        "SCREENSHOT_QUANTITY_ROUNDED_VALUE_DOES_NOT_RECONCILE": (
            "warning",
            "Displayed screenshot quantity is rounded and does not reproduce displayed value.",
            False,
        ),
        "STALE_EXTERNAL_EVIDENCE": (
            "warning",
            "Cached external evidence is stale and excluded from decisions.",
            False,
        ),
    }
    for code in holding.get("source_data_quality_flags") or []:
        severity, message, blocking = source_flag_details.get(
            str(code),
            ("warning", f"Imported source data-quality flag: {code}.", False),
        )
        flags.append(_flag(str(code), severity, message, blocking=blocking))
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
    chart_pattern, pattern_evidence, pattern_flags = pattern_evidence_for_holding(holding)
    holding["_chart_pattern"] = chart_pattern
    evidence.extend(pattern_evidence)
    flags = _operational_flags(holding, family_stale=family_stale)
    flags.extend(return_flags)
    flags.extend(momentum_flags)
    flags.extend(provenance_flags)
    flags.extend(pattern_flags)
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
    decision_conflicts: list[str] = []
    if chart_pattern and chart_pattern.active:
        if chart_pattern.bias == "bullish" and decision.sell_type is SellType.FUNDAMENTAL_SELL:
            decision_conflicts.append("BULLISH_PATTERN_FUNDAMENTAL_SELL_PRESERVED")
        elif chart_pattern.bias == "bullish" and decision.sell_type in {
            SellType.TACTICAL_REDUCE,
            SellType.PORTFOLIO_CONSOLIDATION,
        }:
            decision_conflicts.append("BULLISH_PATTERN_STAGED_EXIT")
        elif chart_pattern.bias == "bullish" and not expected.available:
            decision_conflicts.append("BULLISH_PATTERN_WITHOUT_RETURN_EVIDENCE")
        elif chart_pattern.bias == "bearish" and decision.action in {
            Action.ADD,
            Action.STRONG_ADD,
        }:
            decision_conflicts.append("BEARISH_PATTERN_VS_FUNDAMENTAL_ADD")
    if decision_conflicts:
        flags.append(
            _flag(
                "SIGNAL_CONFLICT",
                "warning",
                "Chart timing and deterministic business/return evidence disagree; the rule trace records which signal dominates.",
            )
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
        chart_pattern=chart_pattern,
        decision_conflicts=decision_conflicts,
        business_thesis=_business_thesis(holding),
        why_now=decision.why_now,
        hold_until=decision.hold_until,
        add_conditions=add_conditions,
        exit_triggers=exit_triggers,
        tax_note=tax.tax_note,
        settlement_note=tax.settlement_note,
        requires_ca_review=tax.requires_ca_review,
        tax_rule_refs=tax.rule_refs,
        replacement_plan=[],
        evidence=evidence,
        data_quality_flags=flags,
        rule_trace=decision.rule_trace,
        feature_coverage_pct=assessment.scores.feature_coverage_pct,
        recommendation_as_of=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
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

    portfolio_value = float(
        (family.get("summary") or {}).get("total_current_value")
        or sum(item.consolidated_value for item in recommendations)
    )
    add_candidates = sorted(
        (
            item
            for item in recommendations
            if item.action in {Action.ADD, Action.STRONG_ADD}
        ),
        key=lambda item: (
            -(item.expected_3y_irr.base_pct or -999),
            -item.action_confidence,
            item.symbol,
        ),
    )
    remaining_capacity = {
        item.symbol: max(
            0.0,
            portfolio_value * (item.target_weight_pct - item.family_weight_pct) / 100,
        )
        for item in add_candidates
    }
    reinvestment_plan: list[dict[str, Any]] = []
    plan_by_account: dict[str, list[dict[str, Any]]] = {}
    for account_code, account_proceeds in sorted(proceeds.items()):
        remaining = float(account_proceeds)
        rows: list[dict[str, Any]] = []
        for candidate in add_candidates:
            if remaining <= 0:
                break
            if account_code not in {position.account_code for position in candidate.accounts}:
                continue
            capacity = remaining_capacity[candidate.symbol]
            if capacity <= 0:
                continue
            amount = round(min(remaining, capacity), 2)
            if amount <= 0:
                continue
            rows.append(
                {
                    "account_code": account_code,
                    "destination": candidate.symbol,
                    "amount": amount,
                    "basis": "existing_underweight_deterministic_add",
                }
            )
            remaining = round(remaining - amount, 2)
            remaining_capacity[candidate.symbol] = max(0.0, capacity - amount)
        if remaining > 0:
            rows.append(
                {
                    "account_code": account_code,
                    "destination": "CASH_BUFFER",
                    "amount": remaining,
                    "basis": "no_eligible_existing_position_within_target_weight",
                }
            )
        plan_by_account[account_code] = rows
        reinvestment_plan.extend(rows)

    sleeve_totals: dict[str, float] = {}
    for row in reinvestment_plan:
        destination = str(row["destination"])
        sleeve_totals[destination] = sleeve_totals.get(destination, 0.0) + float(row["amount"])
    total_proceeds = sum(proceeds.values())
    target_sleeve_allocation = [
        {
            "destination": destination,
            "amount": round(amount, 2),
            "share_of_proceeds_pct": round(amount / total_proceeds * 100, 2)
            if total_proceeds
            else 0.0,
        }
        for destination, amount in sorted(
            sleeve_totals.items(), key=lambda item: (-item[1], item[0])
        )
    ]

    with_replacements: list[HoldingRecommendation] = []
    for item in recommendations:
        replacement_plan: list[dict[str, Any]] = []
        if item.sell_pct > 0:
            for position in item.accounts:
                source_sale = position.current_value * item.sell_pct / 100
                account_total = proceeds.get(position.account_code, 0.0)
                if account_total <= 0:
                    continue
                for destination in plan_by_account.get(position.account_code, []):
                    replacement_plan.append(
                        {
                            "account_code": position.account_code,
                            "destination": destination["destination"],
                            "amount": round(
                                source_sale * float(destination["amount"]) / account_total,
                                2,
                            ),
                            "basis": destination["basis"],
                        }
                    )
        with_replacements.append(replace(item, replacement_plan=replacement_plan))
    recommendations = with_replacements

    deadlines = [
        {
            "symbol": item.symbol,
            "action": item.action.value,
            "sell_type": item.sell_type.value,
            "hold_until": item.hold_until,
            "priority": "high"
            if item.action in {Action.SELL, Action.RECONCILE}
            else "medium",
        }
        for item in recommendations
        if item.hold_until.get("value")
    ]
    all_flags = [flag for item in recommendations for flag in item.data_quality_flags]
    payload = AdvisoryPortfolio(
        schema_version=SCHEMA_VERSION,
        generated_at=generated_at or datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        source_portfolio_cached_at=as_of_text(portfolio_as_of),
        portfolio_value=round(portfolio_value, 2),
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
        target_sleeve_allocation=target_sleeve_allocation,
        proceeds_by_account=proceeds,
        reinvestment_plan=reinvestment_plan,
        overlap_report=overlap_report,
        cooldown_warning=(
            f"Recent turnover is {turnover:.1f}%; optional rotations are suppressed "
            "during cooldown."
            if cooldown_active
            else None
        ),
        deadlines=deadlines,
        evidence_status={
            "recommendations": len(recommendations),
            "with_dated_evidence": sum(bool(item.evidence) for item in recommendations),
            "stale_items": sum(
                flag.code.startswith("STALE_") for flag in all_flags
            ),
            "blocking_items": sum(flag.blocking for flag in all_flags),
        },
    )
    return to_primitive(payload)
