"""Transparent component scoring with non-neutral missing-data treatment."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from modules.portfolio.services.advisory.models import (
    ExpectedThreeYearIrr,
    MomentumSnapshot,
    Scores,
)


@dataclass(frozen=True)
class FeatureAssessment:
    scores: Scores
    quality_ratio: float | None
    growth_ratio: float | None
    covered_points: dict[str, float]


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result


def _percentage(value: Any) -> float | None:
    result = _number(value)
    if result is not None and abs(result) <= 1 and result != 0:
        return result * 100
    return result


def _scale(value: float, low: float, high: float) -> float:
    return max(0.0, min(1.0, (value - low) / (high - low)))


def _weighted_component(
    values: list[tuple[float | None, float, float, float]],
) -> tuple[float, float, float | None]:
    score = 0.0
    covered = 0.0
    for value, weight, low, high in values:
        if value is None:
            continue
        score += _scale(value, low, high) * weight
        covered += weight
    return score, covered, (score / covered if covered else None)


def assess_features(
    holding: dict[str, Any],
    *,
    expected_return: ExpectedThreeYearIrr,
    momentum: MomentumSnapshot,
    max_position_pct: float,
    has_overlap: bool,
) -> FeatureAssessment:
    roce = _percentage(holding.get("roce"))
    debt = _number(holding.get("debt_to_equity"))
    if debt is not None and debt > 10:
        debt /= 100
    fcf = holding.get("free_cash_flow_positive")
    fcf_value = 100.0 if fcf is True else 0.0 if fcf is False else None
    quality, quality_covered, quality_ratio = _weighted_component(
        [
            (roce, 10.0, 5.0, 25.0),
            ((3.0 - debt) if debt is not None else None, 6.0, 0.0, 3.0),
            (fcf_value, 4.0, 0.0, 100.0),
        ]
    )

    growth, growth_covered, growth_ratio = _weighted_component(
        [
            (_percentage(holding.get("revenue_growth_pct")), 7.0, -10.0, 25.0),
            (_percentage(holding.get("earnings_growth_pct")), 9.0, -15.0, 30.0),
            (_percentage(holding.get("earnings_revision_pct")), 4.0, -10.0, 10.0),
        ]
    )

    valuation = 0.0
    valuation_covered = 0.0
    if expected_return.base_pct is not None:
        valuation = _scale(expected_return.base_pct, 0.0, 25.0) * 20.0
        valuation_covered = 20.0

    momentum_covered = 15.0 * (momentum.coverage_pct / 100)
    momentum_score = momentum.score * (momentum.coverage_pct / 100)

    moat = _number(holding.get("moat_score"))
    governance = str(holding.get("governance_risk") or "").lower()
    moat_governance = 0.0
    moat_covered = 0.0
    if moat is not None:
        moat_governance += _scale(moat, 0.0, 10.0) * 6.0
        moat_covered += 6.0
    if governance:
        governance_score = {
            "none": 1.0,
            "low": 0.8,
            "medium": 0.4,
            "high": 0.0,
            "broken": 0.0,
        }.get(governance, 0.25)
        moat_governance += governance_score * 4.0
        moat_covered += 4.0

    weight = float(holding.get("family_weight_pct") or 0)
    portfolio_fit = 10.0
    if weight > max_position_pct:
        portfolio_fit -= min(8.0, ((weight - max_position_pct) / max_position_pct) * 10)
    if 0 < weight < 0.5:
        portfolio_fit -= 3.0
    if has_overlap:
        portfolio_fit -= 2.0
    portfolio_fit = max(0.0, portfolio_fit)

    macro = _number(holding.get("macro_alignment_score"))
    macro_score = max(0.0, min(5.0, macro)) if macro is not None else 0.0
    macro_covered = 5.0 if macro is not None else 0.0

    covered = {
        "quality": quality_covered,
        "growth": growth_covered,
        "valuation": valuation_covered,
        "momentum": momentum_covered,
        "moat_governance": moat_covered,
        "portfolio_fit": 10.0,
        "macro": macro_covered,
    }
    coverage_pct = sum(covered.values())
    total = (
        quality
        + growth
        + valuation
        + momentum_score
        + moat_governance
        + portfolio_fit
        + macro_score
    )
    missing_penalty = round(max(0.0, (50.0 - coverage_pct) * 0.05), 2)
    total = max(0.0, total - missing_penalty)
    scores = Scores(
        quality=round(quality, 2),
        growth=round(growth, 2),
        valuation=round(valuation, 2),
        momentum=round(momentum_score, 2),
        moat_governance=round(moat_governance, 2),
        portfolio_fit=round(portfolio_fit, 2),
        macro=round(macro_score, 2),
        total=round(total, 2),
        feature_coverage_pct=round(coverage_pct, 1),
        missing_data_penalty=missing_penalty,
    )
    return FeatureAssessment(scores, quality_ratio, growth_ratio, covered)
