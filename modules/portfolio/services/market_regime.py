"""Transparent India Market Regime & Mood Index (MRMI) methodology."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import sqrt
from statistics import pstdev
from typing import Any, Protocol

from modules.portfolio.db import market_regime


METHODOLOGY_VERSION = "india-mrmi-v1"


class MarketComponentProvider(Protocol):
    def fetch(self, *, as_of: str) -> dict[str, dict[str, Any]]: ...


@dataclass(frozen=True)
class ComponentSpec:
    weight: float
    normalization: str
    lookback: str
    lower: float
    upper: float
    invert: bool = False


COMPONENTS = {
    "market_breadth": ComponentSpec(20, "linear_clipped", "200 sessions", 20, 80),
    "index_momentum": ComponentSpec(20, "linear_clipped", "126 sessions", -20, 20),
    "volatility_regime": ComponentSpec(15, "percentile_inverted", "252 sessions", 0, 100, True),
    "fpi_flow_regime": ComponentSpec(15, "zscore_clipped", "63 sessions", -2, 2),
    "participation_strength": ComponentSpec(10, "linear_clipped", "63 sessions", -10, 10),
    "derivatives_sentiment": ComponentSpec(10, "linear_clipped", "63 sessions", -2, 2),
    "valuation_stretch": ComponentSpec(5, "percentile_inverted", "10 years", 0, 100, True),
    "safe_haven_liquidity": ComponentSpec(5, "stress_percentile_inverted", "252 sessions", 0, 100, True),
}


def calculate_mrmi(
    inputs: dict[str, dict[str, Any]],
    *,
    as_of: str,
    history: list[dict[str, Any]] | None = None,
    observation_state: str = "PROVISIONAL",
) -> dict[str, Any]:
    date.fromisoformat(as_of)
    prior = list(history if history is not None else market_regime.history(limit=365))
    components: list[dict[str, Any]] = []
    flags: list[str] = []
    observed_weight = 0.0
    weighted_score = 0.0
    freshness_weight = 0.0

    for name, spec in COMPONENTS.items():
        raw = inputs.get(name)
        if not raw or raw.get("raw_value") is None:
            flags.append(f"MISSING_{name.upper()}")
            continue
        raw_value = float(raw["raw_value"])
        score = _normalize(raw_value, spec)
        source_as_of = str(raw.get("source_as_of") or as_of)
        age = max(0, (date.fromisoformat(as_of) - date.fromisoformat(source_as_of)).days)
        freshness = _freshness(age)
        if name == "fpi_flow_regime" and age > 3:
            flags.append("STALE_FPI_FLOW")
        if freshness < 0.5:
            flags.append(f"STALE_{name.upper()}")
        observed_weight += spec.weight
        weighted_score += score * spec.weight
        freshness_weight += freshness * spec.weight
        components.append(
            {
                "name": name,
                "raw_value": raw_value,
                "normalization_method": spec.normalization,
                "lookback_window": raw.get("lookback_window") or spec.lookback,
                "percentile_or_zscore": raw.get("percentile_or_zscore", raw_value),
                "component_score": round(score, 2),
                "weight": spec.weight,
                "effective_weight": None,
                "source": str(raw.get("source") or "unspecified_public_source"),
                "source_as_of": source_as_of,
                "freshness": freshness,
            }
        )

    if not observed_weight:
        raise ValueError("At least one sourced MRMI component is required.")
    score = weighted_score / observed_weight
    for component in components:
        component["effective_weight"] = round(component["weight"] / observed_weight * 100, 2)
    coverage = observed_weight
    freshness_score = freshness_weight / observed_weight
    disagreement = pstdev([row["component_score"] for row in components]) if len(components) > 1 else 0
    history_factor = min(1.0, 0.7 + sqrt(min(len(prior), 90) / 90) * 0.3)
    confidence = round(
        max(0, min(100, coverage * freshness_score * history_factor - disagreement * 0.15))
    )
    if coverage < 100:
        flags.append("PARTIAL_COMPONENT_COVERAGE")
    if not prior:
        trend = "STABLE"
        flags.append("INSUFFICIENT_HISTORY_FOR_TREND")
    else:
        change = score - float(prior[-1]["score"])
        trend = "IMPROVING" if change >= 3 else "DETERIORATING" if change <= -3 else "STABLE"
    band, regime = band_and_regime(score)
    return {
        "market": "INDIA",
        "score": round(score, 2),
        "band": band,
        "regime": regime,
        "trend": trend,
        "confidence": confidence,
        "as_of": as_of,
        "methodology_version": METHODOLOGY_VERSION,
        "observation_state": observation_state,
        "components": components,
        "component_coverage_pct": round(coverage, 2),
        "data_quality_flags": sorted(set(flags)),
        "interpretation": interpretation(band, trend),
        "not_a_forecast": True,
    }


def calculate_and_store(
    inputs: dict[str, dict[str, Any]], *, as_of: str, observation_state: str = "PROVISIONAL"
) -> dict[str, Any]:
    observation = calculate_mrmi(inputs, as_of=as_of, observation_state=observation_state)
    return market_regime.save_observation(observation)


def band_and_regime(score: float) -> tuple[str, str]:
    if score < 20:
        return "EXTREME_FEAR", "RISK_OFF"
    if score < 40:
        return "FEAR", "DEFENSIVE"
    if score < 60:
        return "NEUTRAL", "BALANCED"
    if score < 80:
        return "GREED", "RISK_ON"
    return "EXTREME_GREED", "EUPHORIC"


def interpretation(band: str, trend: str) -> str:
    if band == "EXTREME_FEAR" and trend == "IMPROVING":
        return "Risk appetite is depressed but improving; supported additions may use staged deployment."
    if band in {"GREED", "EXTREME_GREED"}:
        return "Risk appetite is elevated; avoid chasing and reduce initial tranche size."
    if band in {"FEAR", "EXTREME_FEAR"}:
        return "Risk appetite is defensive; preserve cash flexibility and prioritize existing risk controls."
    return "Conditions are balanced; retain normal staged execution and portfolio guardrails."


def methodology() -> dict[str, Any]:
    return {
        "methodology_version": METHODOLOGY_VERSION,
        "score_meaning": "Execution and sizing context; not a return forecast.",
        "components": {
            name: {
                "weight": spec.weight,
                "normalization": spec.normalization,
                "lookback_window": spec.lookback,
                "lower_bound": spec.lower,
                "upper_bound": spec.upper,
                "inverted": spec.invert,
            }
            for name, spec in COMPONENTS.items()
        },
        "bands": {"EXTREME_FEAR": "[0,20)", "FEAR": "[20,40)", "NEUTRAL": "[40,60)", "GREED": "[60,80)", "EXTREME_GREED": "[80,100]"},
    }


def _normalize(raw_value: float, spec: ComponentSpec) -> float:
    position = (raw_value - spec.lower) / (spec.upper - spec.lower)
    score = max(0.0, min(1.0, position)) * 100
    return 100 - score if spec.invert else score


def _freshness(age_days: int) -> float:
    if age_days <= 1:
        return 1.0
    if age_days <= 3:
        return 0.85
    if age_days <= 7:
        return 0.55
    return 0.25
