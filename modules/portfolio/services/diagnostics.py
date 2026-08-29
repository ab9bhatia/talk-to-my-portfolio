"""Redacted local diagnostics and opt-in support bundle generation."""

from __future__ import annotations

import io
import json
import platform
import zipfile
from pathlib import Path
from typing import Any

from modules.portfolio.db.schema_migrations import database_status
from modules.portfolio.paths import DATA_DIR
from shared.security_redaction import account_alias, redact_text


def _scheduler_health() -> dict[str, Any]:
    system = platform.system()
    candidates = {
        "Darwin": Path.home() / "Library/LaunchAgents/com.talktomyportfolio.weekly-sync.plist",
        "Linux": Path.home() / ".config/systemd/user/talktomyportfolio-weekly-sync.timer",
    }
    path = candidates.get(system)
    return {
        "platform": system,
        "configuration_name": path.name if path else None,
        "installed": bool(path and path.exists()),
        "detail": "Check Task Scheduler with Install-WeeklySyncWindows.ps1" if system == "Windows" else None,
    }


def collect_diagnostics() -> dict[str, Any]:
    from modules.portfolio.db import operating_console, portfolio_cache, weekly_sync
    from modules.portfolio.services.portfolio_revalidate import meta_for_family

    try:
        sync = weekly_sync.sync_status()
    except Exception as exc:
        sync = {"status": "UNAVAILABLE", "error": redact_text(exc)}
    try:
        cache = meta_for_family(fresh_ttl=300)
    except Exception as exc:
        cache = {"status": "UNAVAILABLE", "error": redact_text(exc)}
    snapshot = portfolio_cache.get_snapshot("family:metrics=True")
    family = snapshot[1] if snapshot else {}
    reconciliation = family.get("reconciliation") or {}
    quote_coverage = family.get("market_data_coverage") or family.get("quote_coverage") or {}
    return {
        "status": "ok" if all(row["integrity"] == "ok" for row in database_status()) else "degraded",
        "data_directory_permissions": oct(DATA_DIR.stat().st_mode & 0o777) if DATA_DIR.exists() else None,
        "databases": database_status(),
        "portfolio_cache": cache,
        "account_freshness": {
            "accounts_loaded": family.get("accounts_loaded"),
            "error_count": len(family.get("errors") or []),
            "cached_at": family.get("cached_at"),
        },
        "quote_evidence_coverage": {
            "quotes": _redact_structure(quote_coverage),
            "reconciliation": _redact_structure(reconciliation.get("summary") or {}),
        },
        "weekly_sync": _redact_structure(sync),
        "provider_latency": operating_console.provider_health(limit=100),
        "scheduler": _scheduler_health(),
        "trading_enabled": False,
    }


def _redact_structure(value: Any) -> Any:
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            lowered = key.lower()
            if any(token in lowered for token in ("secret", "token", "password", "authorization", "prompt")):
                result[key] = "[REDACTED]"
            elif lowered.endswith("path") and item:
                result[key] = Path(str(item)).name
            elif lowered in {"account_id", "user_id"} and item:
                result[key] = account_alias(str(item))
            else:
                result[key] = _redact_structure(item)
        return result
    if isinstance(value, list):
        return [_redact_structure(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def build_support_bundle(*, family: dict[str, Any] | None = None, include_raw_holdings: bool = False) -> io.BytesIO:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("diagnostics.json", json.dumps(collect_diagnostics(), indent=2, sort_keys=True, default=str))
        archive.writestr("privacy.json", json.dumps({
            "raw_holdings_included": bool(include_raw_holdings and family),
            "secrets_included": False,
            "token_databases_included": False,
        }, indent=2))
        if include_raw_holdings and family:
            archive.writestr("holdings.opt-in.json", json.dumps(_redact_structure(family), indent=2, sort_keys=True, default=str))
    buffer.seek(0)
    return buffer
