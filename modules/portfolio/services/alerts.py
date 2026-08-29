"""Material-event alerts with persisted hysteresis and cooldown."""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from modules.portfolio.db import operating_console


ALLOWED_TYPES = {
    "ACTION_CHANGE", "HARD_EXIT_TRIGGER", "PATTERN_CHANGE", "RESULT_DEADLINE",
    "RECONCILIATION_BLOCK", "EVIDENCE_EXPIRY", "CONSTRAINT_BREACH",
    "MRMI_REGIME_CHANGE", "CASHFLOW_IMPORT_FAILURE",
}


def evaluate_alerts(
    events: list[dict[str, Any]], *, now: float | None = None, cooldown_seconds: int = 86400
) -> dict[str, Any]:
    fired_at = now if now is not None else time.time()
    alerts = []
    suppressed = []
    for event in events:
        event_type = str(event.get("type") or "")
        if event_type not in ALLOWED_TYPES:
            suppressed.append({**event, "reason": "NON_MATERIAL_EVENT_TYPE"})
            continue
        if event_type == "MRMI_REGIME_CHANGE" and abs(float(event.get("score_change") or 0)) < 5:
            suppressed.append({**event, "reason": "MRMI_HYSTERESIS"})
            continue
        key = str(event.get("key") or f"{event_type}:{event.get('instrument_id') or 'portfolio'}")
        state = {
            field: event.get(field)
            for field in ("from", "to", "trigger", "deadline", "state", "priority")
            if event.get(field) is not None
        }
        state_hash = hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()[:20]
        if operating_console.should_fire_alert(
            alert_key=key,
            alert_type=event_type,
            state_hash=state_hash,
            payload=event,
            fired_at=fired_at,
            cooldown_seconds=cooldown_seconds,
        ):
            alerts.append(event)
        else:
            suppressed.append({**event, "reason": "COOLDOWN_UNCHANGED_STATE"})
    return {"alerts": alerts, "suppressed": suppressed, "execution_enabled": False}
