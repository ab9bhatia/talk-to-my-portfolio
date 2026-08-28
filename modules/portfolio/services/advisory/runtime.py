"""Local-first runtime for the Action Center and conversational advisor."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from typing import Any, Callable

from modules.portfolio.config import get_account_profile
from modules.portfolio.db import profile_goals
from modules.portfolio.services.advisory.providers import (
    enrich_family_with_cached_evidence,
    refresh_providers,
)
from modules.portfolio.services.advisory.service import build_advisory_payload
from modules.portfolio.services.portfolio import fetch_family_portfolio


PatternScanner = Callable[[list[dict[str, Any]]], list[dict[str, Any]]]


def _attach_local_profiles(family: dict[str, Any]) -> None:
    for block in family.get("portfolios") or []:
        account_id = str(block.get("account_id") or "")
        if not account_id:
            continue
        try:
            block["account_profile"] = get_account_profile(account_id)
        except KeyError:
            block.setdefault("account_profile", {})


def _attach_patterns(
    family: dict[str, Any],
    *,
    scanner: PatternScanner | None = None,
) -> dict[str, Any]:
    if scanner is None:
        from modules.portfolio.services.chart_patterns import scan_holdings

        scanner = scan_holdings
    holdings = [
        holding
        for block in family.get("portfolios") or []
        for holding in block.get("holdings") or []
        if holding.get("symbol")
    ]
    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for holding in holdings:
        key = (
            str(holding.get("symbol") or "").upper(),
            str(holding.get("exchange") or "NSE").upper(),
        )
        unique.setdefault(key, holding)
    try:
        scanned = scanner(list(unique.values()))
    except Exception as exc:
        return {"scanned": 0, "with_patterns": 0, "error": str(exc)}

    by_key = {
        (
            str(row.get("symbol") or "").upper(),
            str(row.get("exchange") or "NSE").upper(),
        ): row
        for row in scanned
    }
    with_patterns = 0
    for holding in holdings:
        key = (
            str(holding.get("symbol") or "").upper(),
            str(holding.get("exchange") or "NSE").upper(),
        )
        row = by_key.get(key)
        if not row:
            continue
        holding["chart_patterns"] = {
            "patterns": row.get("patterns") or [],
            "primary": row.get("primary"),
        }
        if row.get("patterns"):
            with_patterns += 1
    return {
        "scanned": len(scanned),
        "with_patterns": with_patterns,
        "error": None,
    }


def _fingerprint(payload: dict[str, Any]) -> str:
    material = {
        "schema_version": payload.get("schema_version"),
        "source_portfolio_cached_at": payload.get("source_portfolio_cached_at"),
        "recommendations": [
            {
                "symbol": item.get("symbol"),
                "qty": item.get("consolidated_qty"),
                "value": item.get("consolidated_value"),
                "action": item.get("action"),
                "sell_pct": item.get("sell_pct"),
                "pattern_as_of": (item.get("chart_pattern") or {}).get("as_of"),
                "evidence": [row.get("as_of") for row in item.get("evidence") or []],
            }
            for item in payload.get("recommendations") or []
        ],
    }
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


def build_live_advisory(
    *,
    refresh: bool = False,
    include_patterns: bool = True,
    family: dict[str, Any] | None = None,
    pattern_scanner: PatternScanner | None = None,
) -> dict[str, Any]:
    """Build recommendations from the same normalized family payload as the dashboard."""
    source_family = family or fetch_family_portfolio(refresh=refresh, stale_ok=not refresh)
    working = copy.deepcopy(source_family)
    _attach_local_profiles(working)
    provider_refresh = refresh_providers()
    evidence = enrich_family_with_cached_evidence(working)
    patterns = (
        _attach_patterns(working, scanner=pattern_scanner)
        if include_patterns
        else {"scanned": 0, "with_patterns": 0, "error": None}
    )
    goals = profile_goals.get_goals()
    payload = build_advisory_payload(working, goals=goals)
    payload["runtime"] = {
        "accounts_requested": source_family.get("accounts_requested"),
        "accounts_loaded": source_family.get("accounts_loaded"),
        "account_errors": len(source_family.get("errors") or []),
        "patterns": patterns,
        "evidence": evidence,
        "provider_refresh": provider_refresh,
        "execution_enabled": False,
    }
    payload["fingerprint"] = _fingerprint(payload)
    return payload


def advisory_for_llm(payload: dict[str, Any]) -> dict[str, Any]:
    """Default-deny account/tax details before context leaves the local process."""
    safe = copy.deepcopy(payload)
    allow_tax = os.getenv("ADVISOR_ALLOW_LLM_ACCOUNT_TAX_CONTEXT", "").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    for item in safe.get("recommendations") or []:
        for account in item.get("accounts") or []:
            account.pop("account_id", None)
            account.pop("quantity", None)
            account.pop("current_value", None)
        if not allow_tax:
            item["tax_note"] = "Available in the local Action Center only."
            item["settlement_note"] = "Available in the local Action Center only."
            item["tax_rule_refs"] = []
    if not allow_tax:
        safe["proceeds_by_account"] = {}
    safe.setdefault("privacy", {})["account_tax_context_shared_with_llm"] = allow_tax
    return safe
