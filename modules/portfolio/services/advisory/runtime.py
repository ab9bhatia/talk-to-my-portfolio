"""Local-first runtime for the Action Center and conversational advisor."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import threading
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
DECISION_SUMMARY_SCHEMA_VERSION = "decision-presentation-v1"
_DECISION_SUMMARY_CACHE: dict[str, dict[str, Any]] = {}
_DECISION_SUMMARY_LOCK = threading.Lock()


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


def _decision_summary_key(family: dict[str, Any], goals: dict[str, Any]) -> str:
    from modules.portfolio.services.advisory.providers import evidence_status

    evidence = evidence_status()
    material = {
        "schema": DECISION_SUMMARY_SCHEMA_VERSION,
        "portfolio_cached_at": family.get("cached_at"),
        "goals_updated_at": goals.get("updated_at"),
        "evidence_last_fetched_at": evidence.get("last_fetched_at"),
        "holdings": sorted(
            (
                str(row.get("instrument_id") or row.get("isin") or ""),
                str(row.get("symbol") or ""),
                float(row.get("quantity") or 0),
                float(row.get("current_value") or 0),
                str(row.get("reconciliation_state") or ""),
            )
            for block in family.get("portfolios") or []
            for row in block.get("holdings") or []
        ),
    }
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


def build_decision_summary(
    *,
    family: dict[str, Any] | None = None,
    refresh: bool = False,
) -> dict[str, Any]:
    """Return the dashboard projection of the same advisory engine, without pattern/LLM work."""
    source_family = family or fetch_family_portfolio(refresh=refresh, stale_ok=not refresh)
    goals = profile_goals.get_goals()
    cache_key = _decision_summary_key(source_family, goals)
    with _DECISION_SUMMARY_LOCK:
        cached = _DECISION_SUMMARY_CACHE.get(cache_key)
        if cached is not None and not refresh:
            return copy.deepcopy(cached)

    advisory = build_live_advisory(
        refresh=refresh,
        include_patterns=False,
        family=source_family,
    )
    decisions = []
    for item in advisory.get("recommendations") or []:
        decisions.append(
            {
                "instrument_id": item.get("instrument_id"),
                "isin": item.get("isin"),
                "symbol": item.get("symbol"),
                "action": item.get("action"),
                "decision_presentation": item.get("decision_presentation"),
                "signal_stack": item.get("signal_stack"),
                "external_analyst_view": item.get("external_analyst_view"),
                "conflict_categories": item.get("conflict_categories") or [],
            }
        )
    summary = {
        "schema_version": DECISION_SUMMARY_SCHEMA_VERSION,
        "advisory_schema_version": advisory.get("schema_version"),
        "generated_at": advisory.get("generated_at"),
        "source_portfolio_cached_at": advisory.get("source_portfolio_cached_at"),
        "cache_key": cache_key,
        "patterns_evaluated": False,
        "llm_used": False,
        "decisions": decisions,
    }
    with _DECISION_SUMMARY_LOCK:
        _DECISION_SUMMARY_CACHE.clear()
        _DECISION_SUMMARY_CACHE[cache_key] = copy.deepcopy(summary)
    return summary


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
