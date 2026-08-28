"""Deterministic action selection and portfolio guardrails."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from modules.portfolio.services.advisory.features import FeatureAssessment
from modules.portfolio.services.advisory.models import (
    Action,
    DataQualityFlag,
    ExpectedThreeYearIrr,
    MomentumRegime,
    SellType,
)


@dataclass(frozen=True)
class Decision:
    action: Action
    sell_type: SellType
    sell_pct: float
    target_weight_pct: float
    confidence: int
    why_now: str
    hold_until: dict[str, str]
    rule_trace: list[dict[str, Any]] = field(default_factory=list)


def _has_flag(flags: list[DataQualityFlag], *codes: str) -> bool:
    wanted = set(codes)
    return any(flag.code in wanted for flag in flags)


def _confidence(
    assessment: FeatureAssessment,
    expected_return: ExpectedThreeYearIrr,
    flags: list[DataQualityFlag],
) -> int:
    confidence = round(20 + assessment.scores.feature_coverage_pct * 0.72)
    if expected_return.available:
        confidence += 8
    else:
        confidence = min(confidence, 45)
    if _has_flag(flags, "EXPECTED_RETURN_PROVENANCE_MISSING"):
        confidence = min(confidence, 60)
    if _has_flag(flags, "EXPECTED_RETURN_SCREENING_PROXY"):
        confidence = min(confidence, 55)
    if any(flag.blocking for flag in flags):
        confidence = min(confidence, 45)
    return max(10, min(95, confidence))


def _decision(
    *,
    action: Action,
    sell_type: SellType,
    sell_pct: float,
    target_weight: float,
    confidence: int,
    why: str,
    hold_type: str,
    hold_value: str,
    trace: list[dict[str, Any]],
) -> Decision:
    trace.append(
        {
            "rule": f"ACTION_{action.value}",
            "result": "selected",
            "action": action.value,
            "sell_type": sell_type.value,
        }
    )
    return Decision(
        action=action,
        sell_type=sell_type,
        sell_pct=round(max(0.0, min(100.0, sell_pct)), 2),
        target_weight_pct=round(max(0.0, target_weight), 2),
        confidence=confidence,
        why_now=why,
        hold_until={"type": hold_type, "value": hold_value},
        rule_trace=trace,
    )


def select_action(
    holding: dict[str, Any],
    *,
    expected_return: ExpectedThreeYearIrr,
    assessment: FeatureAssessment,
    flags: list[DataQualityFlag],
    max_position_pct: float,
    cooldown_active: bool,
    has_overlap: bool,
) -> Decision:
    """Select an action without using purchase price or unrealized P&L."""
    trace: list[dict[str, Any]] = []
    weight = float(holding.get("family_weight_pct") or 0)
    confidence = _confidence(assessment, expected_return, flags)
    screening_proxy = expected_return.evidence_tier == "screening_proxy"
    scenario_label = "screening" if screening_proxy else "documented"
    pattern = holding.get("_chart_pattern")
    if pattern and pattern.active:
        trace.append(
            {
                "rule": "CHART_PATTERN_TIMING_ONLY",
                "matched": True,
                "bias": pattern.bias,
                "lifecycle_state": pattern.lifecycle_state,
                "target_status": pattern.target_status,
                "heuristic_score": pattern.heuristic_score,
                "policy": "may_stage_execution_but_cannot_create_or_override_fundamental_action",
            }
        )

    needs_reconcile = _has_flag(
        flags,
        "CORPORATE_ACTION_RECONCILIATION",
        "UNRESOLVED_SYMBOL",
    )
    trace.append({"rule": "IDENTITY_AND_RECONCILIATION", "matched": needs_reconcile})
    if needs_reconcile:
        return _decision(
            action=Action.RECONCILE,
            sell_type=SellType.NONE,
            sell_pct=0,
            target_weight=weight,
            confidence=90,
            why="Identity or cost basis must be reconciled before investment action is reliable.",
            hold_type="event",
            hold_value="Broker/exchange reconciliation completed",
            trace=trace,
        )

    untradeable = _has_flag(flags, "SUSPENDED_OR_UNTRADEABLE")
    trace.append({"rule": "TRADABILITY", "matched": untradeable})
    if untradeable:
        return _decision(
            action=Action.WATCH,
            sell_type=SellType.NONE,
            sell_pct=0,
            target_weight=weight,
            confidence=90,
            why="The security is not currently market-tradeable; no executable sell is assumed.",
            hold_type="event",
            hold_value="Tradability, relisting, or recovery path confirmed",
            trace=trace,
        )

    governance_risk = str(holding.get("governance_risk") or "").lower()
    governance_evidence = bool(
        holding.get("governance_event")
        and holding.get("governance_event_source")
        and holding.get("governance_event_as_of")
    )
    hard_governance = governance_risk in {"high", "broken"} and governance_evidence
    trace.append(
        {
            "rule": "SOURCED_GOVERNANCE_FAILURE",
            "matched": hard_governance,
            "risk": governance_risk or "unknown",
        }
    )
    if hard_governance:
        if pattern and pattern.active and pattern.bias == "bullish":
            trace.append(
                {
                    "rule": "BULLISH_PATTERN_VS_FUNDAMENTAL_SELL",
                    "matched": True,
                    "effect": "fundamental_sell_preserved",
                }
            )
        return _decision(
            action=Action.SELL,
            sell_type=SellType.FUNDAMENTAL_SELL,
            sell_pct=100,
            target_weight=0,
            confidence=90,
            why="A sourced governance event invalidates the business-risk case.",
            hold_type="continuous",
            hold_value="Exit subject to tradability, settlement, and tax review",
            trace=trace,
        )

    if governance_risk in {"high", "broken"} and not governance_evidence:
        trace.append({"rule": "UNSOURCED_GOVERNANCE_CLAIM", "matched": True})
        return _decision(
            action=Action.WATCH,
            sell_type=SellType.NONE,
            sell_pct=0,
            target_weight=weight,
            confidence=min(confidence, 35),
            why=(
                "A governance concern is present but lacks authoritative evidence; "
                "verify it before acting."
            ),
            hold_type="event",
            hold_value="Governance claim verified or rejected from an authoritative source",
            trace=trace,
        )

    base_irr = expected_return.base_pct
    if base_irr is None:
        overweight = weight > max_position_pct
        trace.append({"rule": "EXPECTED_RETURN_UNAVAILABLE", "matched": True})
        if overweight:
            return _decision(
                action=Action.CAP,
                sell_type=SellType.NONE,
                sell_pct=0,
                target_weight=max_position_pct,
                confidence=confidence,
                why=(
                    "The position exceeds its family limit, while sourced return "
                    "evidence is incomplete."
                ),
                hold_type="event",
                hold_value="Return model completed or family weight returns within limit",
                trace=trace,
            )
        if 0 < weight < 0.5 and (assessment.quality_ratio or 0) >= 0.65:
            return _decision(
                action=Action.HOLD_NO_ADD,
                sell_type=SellType.NONE,
                sell_pct=0,
                target_weight=weight,
                confidence=confidence,
                why=(
                    "The company appears sound but the position is subscale and lacks "
                    "a sourced return model."
                ),
                hold_type="result",
                hold_value="Next result plus documented valuation refresh",
                trace=trace,
            )
        return _decision(
            action=Action.WATCH,
            sell_type=SellType.NONE,
            sell_pct=0,
            target_weight=weight,
            confidence=confidence,
            why=(
                "Critical expected-return evidence is missing, so the engine will not "
                "invent a buy or sell case."
            ),
            hold_type="result",
            hold_value="Next result plus sourced three-year valuation model",
            trace=trace,
        )

    trace.append({"rule": "BASE_IRR_BAND", "base_irr_pct": base_irr})
    if base_irr > 25:
        if weight >= max_position_pct:
            candidate = _decision(
                action=Action.CAP,
                sell_type=SellType.NONE,
                sell_pct=0,
                target_weight=max_position_pct,
                confidence=confidence,
                why=(
                    "Expected return is attractive, but the family position is at or "
                    "above its limit."
                ),
                hold_type="continuous",
                hold_value=f"Family weight remains at or below {max_position_pct:.1f}%",
                trace=trace,
            )
        else:
            candidate = _decision(
                action=Action.ADD if screening_proxy else Action.STRONG_ADD,
                sell_type=SellType.NONE,
                sell_pct=0,
                target_weight=min(
                    max_position_pct,
                    max(1.0, weight + (0.5 if screening_proxy else 2.0)),
                ),
                confidence=confidence,
                why=(
                    "The screening base scenario clears the add band; confirm it against "
                    "filings before execution."
                    if screening_proxy
                    else "The documented base scenario exceeds the strong-add return band."
                ),
                hold_type="result",
                hold_value="Next result or material thesis change",
                trace=trace,
            )
        return candidate
    if base_irr >= 20:
        action = Action.CAP if weight >= max_position_pct else Action.ADD
        return _decision(
            action=action,
            sell_type=SellType.NONE,
            sell_pct=0,
            target_weight=max_position_pct
            if action is Action.CAP
            else min(max_position_pct, max(1.0, weight + 1.0)),
            confidence=confidence,
            why=(
                f"The {scenario_label} base scenario is in the add band, subject to "
                "position limits."
            ),
            hold_type="result",
            hold_value="Next result or material thesis change",
            trace=trace,
        )
    if base_irr >= 16:
        return _decision(
            action=Action.HOLD,
            sell_type=SellType.NONE,
            sell_pct=0,
            target_weight=min(weight, max_position_pct),
            confidence=confidence,
            why=f"The {scenario_label} base scenario supports holding, but not an unconditional add.",
            hold_type="result",
            hold_value="Next result and valuation refresh",
            trace=trace,
        )
    if base_irr >= 12:
        return _decision(
            action=Action.HOLD_NO_ADD,
            sell_type=SellType.NONE,
            sell_pct=0,
            target_weight=min(weight, max_position_pct),
            confidence=confidence,
            why=f"The {scenario_label} base scenario is adequate for holding but below the add hurdle.",
            hold_type="result",
            hold_value="Next result and valuation refresh",
            trace=trace,
        )

    do_not_sell_before = holding.get("do_not_sell_before")
    if do_not_sell_before:
        trace.append({"rule": "DO_NOT_SELL_BEFORE", "matched": True, "value": do_not_sell_before})
        return _decision(
            action=Action.HOLD_NO_ADD,
            sell_type=SellType.NONE,
            sell_pct=0,
            target_weight=weight,
            confidence=confidence,
            why="A user-imposed do-not-sell constraint suppresses an otherwise sell-like action.",
            hold_type="date",
            hold_value=str(do_not_sell_before),
            trace=trace,
        )

    consolidation = base_irr < 8 and (
        has_overlap or weight < 0.5 or holding.get("replacement_available") is True
    )
    if consolidation:
        if screening_proxy:
            action = Action.REDUCE
            sell_type = SellType.TACTICAL_REDUCE
            sell_pct = 25.0
            target = weight * 0.75
            why = (
                "Low proxy return plus overlap/subscale fit supports only a staged research "
                "reduction; a full exit requires documented evidence."
            )
            trace.append(
                {
                    "rule": "SCREENING_PROXY_BLOCKS_FULL_EXIT",
                    "matched": True,
                    "selected_sell_pct": sell_pct,
                }
            )
        else:
            action = Action.SELL
            sell_type = SellType.PORTFOLIO_CONSOLIDATION
            sell_pct = 100.0
            target = 0.0
            why = "Low expected return plus overlap/subscale portfolio fit supports consolidation."
    else:
        action = Action.REDUCE
        sell_type = SellType.TACTICAL_REDUCE
        sell_pct = 25.0 if base_irr >= 8 else 50.0
        if weight > max_position_pct:
            sell_pct = max(sell_pct, (1 - (max_position_pct / weight)) * 100)
        if screening_proxy:
            sell_pct = min(sell_pct, 25.0)
        target = weight * (1 - sell_pct / 100)
        why = "Expected return is below the hold hurdle; use a staged tactical reduction."
        if holding.get("is_cyclical") and holding.get("momentum_regime") in {
            MomentumRegime.POSITIVE,
            MomentumRegime.STRONG,
        }:
            why = (
                "Expected return is low, but positive cyclical momentum supports a staged tactical "
                "reduction rather than a fundamental exit."
            )

    if pattern and pattern.active and pattern.bias == "bullish":
        original_action = action
        original_sell_pct = sell_pct
        if sell_type is SellType.PORTFOLIO_CONSOLIDATION:
            action = Action.REDUCE
            sell_pct = min(25.0, sell_pct)
        else:
            sell_pct = max(10.0, sell_pct * 0.5)
        target = weight * (1 - sell_pct / 100)
        why = (
            f"{why} A dated bullish {pattern.label} setup conflicts with immediate execution, "
            "so the exit is staged; the underlying return or portfolio-fit decision is unchanged."
        )
        trace.append(
            {
                "rule": "BULLISH_PATTERN_STAGES_OPTIONAL_EXIT",
                "matched": True,
                "original_action": original_action.value,
                "original_sell_pct": round(original_sell_pct, 2),
                "selected_action": action.value,
                "selected_sell_pct": round(sell_pct, 2),
            }
        )
    elif pattern and pattern.active and pattern.bias == "bearish":
        original_sell_pct = sell_pct
        sell_pct = min(100.0, max(sell_pct, sell_pct * 1.25))
        target = weight * (1 - sell_pct / 100)
        why = f"{why} A dated bearish {pattern.label} setup supports earlier staged execution."
        trace.append(
            {
                "rule": "BEARISH_PATTERN_ACCELERATES_SUPPORTED_EXIT",
                "matched": True,
                "original_sell_pct": round(original_sell_pct, 2),
                "selected_sell_pct": round(sell_pct, 2),
            }
        )

    if screening_proxy and sell_type is not SellType.NONE and sell_pct > 25:
        original_sell_pct = sell_pct
        action = Action.REDUCE
        sell_type = SellType.TACTICAL_REDUCE
        sell_pct = 25.0
        target = weight * 0.75
        trace.append(
            {
                "rule": "SCREENING_PROXY_CAPS_REDUCTION",
                "matched": True,
                "original_sell_pct": round(original_sell_pct, 2),
                "selected_sell_pct": sell_pct,
            }
        )

    if cooldown_active:
        trace.append({"rule": "TURNOVER_COOLDOWN", "matched": True, "suppressed": action.value})
        return _decision(
            action=Action.HOLD_NO_ADD,
            sell_type=SellType.NONE,
            sell_pct=0,
            target_weight=weight,
            confidence=confidence,
            why=(
                "Recent turnover cooldown suppresses this optional rotation; no hard-risk "
                "event is present."
            ),
            hold_type="event",
            hold_value="Cooldown window expires or a hard-risk event occurs",
            trace=trace,
        )

    return _decision(
        action=action,
        sell_type=sell_type,
        sell_pct=sell_pct,
        target_weight=target,
        confidence=confidence,
        why=why,
        hold_type="result",
        hold_value="Next result, catalyst, or material valuation change",
        trace=trace,
    )
