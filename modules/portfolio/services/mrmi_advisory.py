"""MRMI execution/sizing overlay. It never selects an investment action."""

from __future__ import annotations

from typing import Any


def execution_overlay(recommendation: dict[str, Any], observation: dict[str, Any] | None) -> dict[str, Any]:
    action = str(recommendation.get("action") or "WATCH")
    sell_type = str(recommendation.get("sell_type") or "NONE")
    overlay = {
        "available": bool(observation),
        "action_unchanged": action,
        "tranche_pct": 100,
        "deployment_pace": "NORMAL",
        "cash_buffer_adjustment_pct": 0,
        "execution_note": "No finalized market-regime observation is available.",
        "alert_priority": "NORMAL",
    }
    if not observation:
        return overlay
    band = str(observation.get("band") or "NEUTRAL")
    regime = str(observation.get("regime") or "BALANCED")
    trend = str(observation.get("trend") or "STABLE")
    overlay.update({"band": band, "regime": regime, "trend": trend, "score": observation.get("score")})

    if action in {"ADD", "STRONG_ADD"}:
        if band in {"GREED", "EXTREME_GREED"}:
            overlay.update(
                tranche_pct=25 if band == "EXTREME_GREED" else 40,
                deployment_pace="WAIT_FOR_RETEST_NO_CHASE",
                cash_buffer_adjustment_pct=3,
                execution_note="The supported add remains valid, but elevated risk appetite calls for a smaller first tranche and no chasing.",
            )
        elif band == "EXTREME_FEAR" and trend == "IMPROVING":
            overlay.update(
                tranche_pct=50,
                deployment_pace="STAGED_ACCELERATION",
                execution_note="Depressed but improving conditions allow a larger staged tranche for an independently supported add.",
            )
        elif regime in {"RISK_OFF", "DEFENSIVE"}:
            overlay.update(
                tranche_pct=25,
                deployment_pace="SLOW_STAGED",
                cash_buffer_adjustment_pct=5,
                execution_note="The supported add remains valid; defensive conditions favor slower deployment and more cash flexibility.",
            )
    elif action in {"REDUCE", "SELL"} and regime in {"RISK_OFF", "DEFENSIVE"}:
        overlay.update(
            deployment_pace="ACCELERATE_SUPPORTED_REDUCTION",
            alert_priority="HIGH",
            execution_note="Defensive conditions increase urgency only for the already-supported reduction; the sell decision itself is unchanged.",
        )
    elif sell_type == "FUNDAMENTAL_SELL":
        overlay["execution_note"] = "A fundamental sell remains dominant regardless of market mood."
    else:
        overlay["execution_note"] = "Market mood does not create an action; normal portfolio guardrails remain authoritative."
    return overlay
