"""Material-change-first Today Brief."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any


ACTION_REVIEW = {"SELL", "REDUCE", "RECONCILE", "ADD", "STRONG_ADD"}
NO_ACTION = {"HOLD", "HOLD_NO_ADD", "WATCH", "CAP"}

ISSUE_GROUPS = {
    "IDENTITY": {
        "what_wrong": "Some securities do not have a trusted canonical identity.",
        "why_wrong": "Without an ISIN or authoritative broker mapping, quotes and advice can attach to the wrong instrument.",
        "required_action": "Resolve the identity from Data Quality before using any buy or sell call.",
        "destination": "/portfolio/data-quality",
    },
    "VALUE_MISMATCH": {
        "what_wrong": "Broker values and independent market marks differ materially.",
        "why_wrong": "Timestamp, quantity, FX, or corporate-action differences can distort allocation and return calculations.",
        "required_action": "Review the largest mismatches, attach source evidence, and save only an audited explanation.",
        "destination": "/portfolio/data-quality",
    },
    "CORPORATE_ACTION": {
        "what_wrong": "A corporate action or cost-basis transition is unresolved.",
        "why_wrong": "Splits, mergers, bonuses, and symbol changes can make quantity and cost basis unreliable.",
        "required_action": "Reconcile the broker or exchange notice before acting on the affected position.",
        "destination": "/portfolio/data-quality",
    },
    "PRICE": {
        "what_wrong": "A current, sourced market price is missing.",
        "why_wrong": "Position value, portfolio weight, and expected-return scenarios cannot be trusted without a valid mark.",
        "required_action": "Refresh market evidence and verify the canonical exchange mapping.",
        "destination": "/portfolio/data-quality",
    },
    "TRADABILITY": {
        "what_wrong": "One or more positions may be suspended or untradeable.",
        "why_wrong": "A recommendation is not executable until the exchange or broker confirms a valid trading path.",
        "required_action": "Confirm tradability, relisting, or the recovery process; do not assume an immediate exit.",
        "destination": "/portfolio/data-quality",
    },
    "GOVERNANCE": {
        "what_wrong": "A governance concern is missing authoritative, dated evidence.",
        "why_wrong": "An unsourced claim must not create a fundamental sell decision.",
        "required_action": "Attach an exchange filing or company disclosure and reassess the thesis.",
        "destination": "/portfolio/research",
    },
    "TAX_PROFILE": {
        "what_wrong": "Residency, account type, or tax-lot evidence is incomplete.",
        "why_wrong": "The same sale can have different settlement, repatriation, withholding, and tax consequences by account.",
        "required_action": "Complete each account profile in Setup and obtain CA review where flagged.",
        "destination": "/portfolio/setup",
    },
    "OTHER_DATA": {
        "what_wrong": "Required portfolio evidence is incomplete.",
        "why_wrong": "The deterministic engine lowers confidence or blocks action instead of filling gaps with guesses.",
        "required_action": "Inspect the affected securities in Data Quality and supply the missing source evidence.",
        "destination": "/portfolio/data-quality",
    },
    "REDUCE": {
        "what_wrong": "Some holdings are below the hold hurdle or above portfolio limits.",
        "why_wrong": "Low expected return, overlap, or concentration reduces portfolio efficiency; momentum only changes timing.",
        "required_action": "Review staged reductions in Action Center after data, tax, and settlement checks pass.",
        "destination": "/portfolio/advisor",
    },
    "ADD": {
        "what_wrong": "Some holdings clear the deterministic add band.",
        "why_wrong": "The base scenario is attractive relative to the configured hurdle, subject to evidence and concentration limits.",
        "required_action": "Validate filings and account constraints, then review a staged allocation—never an automatic order.",
        "destination": "/portfolio/advisor",
    },
    "RESEARCH_REQUIRED": {
        "what_wrong": "A screening call is not yet supported by current authoritative research.",
        "why_wrong": "A target or derived market model can identify a candidate, but it cannot establish the portfolio thesis by itself.",
        "required_action": "Validate current filings and the business thesis in Research before considering a transaction.",
        "destination": "/portfolio/research",
    },
    "EXTERNAL_DISAGREEMENT": {
        "what_wrong": "The internal portfolio decision and external analyst sentiment disagree.",
        "why_wrong": "External consensus is useful challenge evidence, but it has different scope, freshness, and portfolio constraints.",
        "required_action": "Inspect both sources in Action Center; keep the internal decision primary unless authoritative evidence changes it.",
        "destination": "/portfolio/advisor",
    },
    "DEADLINE": {
        "what_wrong": "A dated result, corporate, or ownership event is approaching.",
        "why_wrong": "The event can materially change the thesis or invalidate stale evidence.",
        "required_action": "Review the sourced event and refresh the decision after it occurs.",
        "destination": "/portfolio/research",
    },
}


def _group_for_flag(code: str) -> str:
    code = code.upper()
    if "UNRESOLVED" in code or "IDENTITY" in code:
        return "IDENTITY"
    if "CORPORATE_ACTION" in code or "COST_BASIS" in code:
        return "CORPORATE_ACTION"
    if "RECONCILIATION" in code or "DISPLAYED_COST" in code:
        return "VALUE_MISMATCH"
    if "PRICE" in code or "QUOTE" in code or "FX_" in code:
        return "PRICE"
    if "SUSPENDED" in code or "TRADABLE" in code:
        return "TRADABILITY"
    if "GOVERNANCE" in code:
        return "GOVERNANCE"
    if "TAX" in code or "SETTLEMENT" in code or "RESIDENCY" in code:
        return "TAX_PROFILE"
    return "OTHER_DATA"


def _build_issue_groups(grouped_symbols: dict[str, set[str]]) -> list[dict[str, Any]]:
    priority = {
        "TRADABILITY": 1,
        "GOVERNANCE": 1,
        "IDENTITY": 1,
        "CORPORATE_ACTION": 1,
        "VALUE_MISMATCH": 2,
        "PRICE": 2,
        "TAX_PROFILE": 3,
        "OTHER_DATA": 3,
        "DEADLINE": 4,
        "REDUCE": 5,
        "ADD": 6,
        "RESEARCH_REQUIRED": 4,
        "EXTERNAL_DISAGREEMENT": 4,
    }
    rows = []
    for key, symbols in grouped_symbols.items():
        spec = ISSUE_GROUPS[key]
        ordered = sorted(symbol for symbol in symbols if symbol)
        rows.append(
            {
                "key": key,
                "priority": priority[key],
                "count": len(ordered),
                "affected_symbols": ordered,
                "symbol_preview": ordered[:6],
                **spec,
            }
        )
    return sorted(rows, key=lambda row: (row["priority"], row["key"]))


def build_today_brief(
    *,
    family: dict[str, Any],
    advisory: dict[str, Any],
    sync_status: dict[str, Any] | None = None,
    market_regime: dict[str, Any] | None = None,
    events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    recommendations = advisory.get("recommendations") or []
    issues: list[dict[str, Any]] = []
    grouped_symbols: dict[str, set[str]] = {}
    blocking = 0
    action_review = 0
    no_action = 0
    research_required = 0
    tax_review_required = 0
    external_disagreement = 0
    for item in recommendations:
        action = str(item.get("action") or "WATCH")
        presentation = item.get("decision_presentation") or {}
        conflicts = {str(value) for value in item.get("conflict_categories") or []}
        flags = item.get("data_quality_flags") or []
        blocking_flags = [flag for flag in flags if flag.get("blocking")]
        readiness = str(presentation.get("readiness") or "")
        if not readiness:
            if blocking_flags or action == "RECONCILE":
                readiness = "DATA_BLOCKED"
            elif item.get("requires_ca_review"):
                readiness = "TAX_REVIEW_REQUIRED"
            elif action in ACTION_REVIEW:
                readiness = "READY_TO_REVIEW"
            else:
                readiness = "MONITOR_ONLY"
        if "EXTERNAL_CONTEXT_DIFFERS" in conflicts or "INTERNAL_VS_EXTERNAL" in conflicts:
            external_disagreement += 1
            grouped_symbols.setdefault("EXTERNAL_DISAGREEMENT", set()).add(
                str(item.get("symbol") or "")
            )
        if readiness in {"DATA_BLOCKED", "NOT_EXECUTABLE"}:
            blocking += 1
            groups = {
                _group_for_flag(str(flag.get("code") or "OTHER_DATA"))
                for flag in blocking_flags
            } or {"VALUE_MISMATCH"}
            for group in groups:
                grouped_symbols.setdefault(group, set()).add(str(item.get("symbol") or ""))
            issues.append(
                {
                    "priority": 1,
                    "type": "RECONCILIATION_OR_HARD_RISK",
                    "symbol": item.get("symbol"),
                    "title": "Advice blocked pending data or hard-risk review",
                    "action": action,
                }
            )
            continue
        if readiness == "TAX_REVIEW_REQUIRED":
            tax_review_required += 1
            grouped_symbols.setdefault("TAX_PROFILE", set()).add(str(item.get("symbol") or ""))
            issues.append(
                {
                    "priority": 2,
                    "type": "TAX_REVIEW_REQUIRED",
                    "symbol": item.get("symbol"),
                    "title": presentation.get("label") or "Tax review required",
                    "action": action,
                }
            )
        elif readiness == "RESEARCH_REQUIRED":
            research_required += 1
            grouped_symbols.setdefault("RESEARCH_REQUIRED", set()).add(
                str(item.get("symbol") or "")
            )
            issues.append(
                {
                    "priority": 4,
                    "type": "RESEARCH_REQUIRED",
                    "symbol": item.get("symbol"),
                    "title": presentation.get("label") or "Research required",
                    "action": action,
                }
            )
        elif readiness == "READY_TO_REVIEW" and action in ACTION_REVIEW:
            action_review += 1
            action_group = "ADD" if action in {"ADD", "STRONG_ADD"} else "REDUCE"
            grouped_symbols.setdefault(action_group, set()).add(str(item.get("symbol") or ""))
            issues.append(
                {
                    "priority": 5 if action in {"ADD", "STRONG_ADD"} else 2,
                    "type": "EXECUTION_READY_ACTION",
                    "symbol": item.get("symbol"),
                    "title": f"Review {action}",
                    "action": action,
                }
            )
        elif readiness == "MONITOR_ONLY" or action in NO_ACTION:
            no_action += 1

    today = date.today()
    deadline_count = 0
    for event in events or []:
        try:
            event_day = date.fromisoformat(str(event.get("event_date")))
        except ValueError:
            continue
        if today <= event_day <= today + timedelta(days=30):
            deadline_count += 1
            grouped_symbols.setdefault("DEADLINE", set()).add(str(event.get("instrument_id") or ""))
            issues.append(
                {
                    "priority": 3,
                    "type": "DEADLINE",
                    "symbol": event.get("instrument_id"),
                    "title": event.get("title"),
                    "event_date": event_day.isoformat(),
                }
            )

    reconciliation = family.get("reconciliation") or {}
    reconciled_pct = float((reconciliation.get("summary") or {}).get("family_value_reconciled_pct") or 0)
    sync = sync_status or {}
    latest_sync = (
        (sync.get("last_successful") or {}).get("status")
        or (sync.get("latest_attempt") or {}).get("status")
        or "NOT_RUN"
    )
    status = "CRITICAL" if blocking else "ATTENTION" if issues else "STABLE"
    issues.sort(key=lambda row: (row["priority"], str(row.get("symbol") or "")))
    return {
        "portfolio_status": status,
        "data_reconciled_pct": reconciled_pct,
        "latest_sync": latest_sync,
        "market_regime": market_regime,
        "actions_require_review": action_review,
        "blocking_data_issues": blocking,
        "research_required": research_required,
        "tax_review_required": tax_review_required,
        "external_disagreement": external_disagreement,
        "deadlines_30d": deadline_count,
        "no_action_count": no_action,
        "holdings_count": len(recommendations),
        "review_queue": issues,
        "issue_groups": _build_issue_groups(grouped_symbols),
        "summary_lines": [
            f"Portfolio status: {status}",
            f"Data reconciled: {reconciled_pct:.1f}%",
            f"Latest sync: {latest_sync}",
            f"{action_review} actions require review",
            f"{blocking} data issues block advice",
            f"{research_required} decisions require research",
            f"{tax_review_required} decisions require tax review",
            f"{deadline_count} deadlines in 30 days",
            f"No action required on {no_action} holdings",
        ],
        "execution_enabled": False,
    }
