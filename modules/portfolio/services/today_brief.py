"""Material-change-first Today Brief."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any


ACTION_REVIEW = {"SELL", "REDUCE", "RECONCILE", "ADD", "STRONG_ADD"}
NO_ACTION = {"HOLD", "HOLD_NO_ADD", "WATCH", "CAP"}


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
    blocking = 0
    action_review = 0
    no_action = 0
    for item in recommendations:
        action = str(item.get("action") or "WATCH")
        flags = item.get("data_quality_flags") or []
        blocking_flags = [flag for flag in flags if flag.get("blocking")]
        if blocking_flags or action == "RECONCILE":
            blocking += 1
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
        if action in ACTION_REVIEW:
            action_review += 1
            issues.append(
                {
                    "priority": 5 if action in {"ADD", "STRONG_ADD"} else 2,
                    "type": "EXECUTION_READY_ACTION",
                    "symbol": item.get("symbol"),
                    "title": f"Review {action}",
                    "action": action,
                }
            )
        elif action in NO_ACTION:
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
        "deadlines_30d": deadline_count,
        "no_action_count": no_action,
        "holdings_count": len(recommendations),
        "review_queue": issues,
        "summary_lines": [
            f"Portfolio status: {status}",
            f"Data reconciled: {reconciled_pct:.1f}%",
            f"Latest sync: {latest_sync}",
            f"{action_review} actions require review",
            f"{blocking} data issues block advice",
            f"{deadline_count} deadlines in 30 days",
            f"No action required on {no_action} holdings",
        ],
        "execution_enabled": False,
    }
