"""Pluggable, cached providers for sourced advisory evidence."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Protocol

from modules.portfolio.db import advisory_evidence as evidence_store
from modules.portfolio.paths import DATA_DIR


DEFAULT_TTL_SECONDS = 7 * 24 * 60 * 60
AUTHORITATIVE_TYPES = {
    "official_filing",
    "exchange",
    "official_amc",
    "official_index",
    "regulator",
    "tax_authority",
}
DECISION_FIELDS = {
    "business_thesis",
    "expected_return_inputs",
    "governance_event",
    "governance_risk",
    "corporate_action_pending",
    "is_suspended",
    "is_tradable",
}
SCREENING_RETURN_SOURCE_TYPES = {"derived_market_model"}


class EvidenceProvider(Protocol):
    name: str

    def fetch(self) -> list[dict[str, Any]]: ...


class LocalEvidenceProvider:
    """Read optional personal evidence from a gitignored local JSON file."""

    name = "local_json"

    def __init__(self, path: Path | None = None):
        self.path = path or DATA_DIR / "advisory-v2" / "evidence.json"

    def fetch(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        rows = payload.get("observations") if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            raise ValueError("Advisory evidence must be a list or an observations list")
        return [dict(row) for row in rows if isinstance(row, dict)]


def _ttl_seconds() -> int:
    raw = os.getenv("ADVISORY_EVIDENCE_TTL_SECONDS")
    try:
        return max(60, int(raw)) if raw else DEFAULT_TTL_SECONDS
    except ValueError:
        return DEFAULT_TTL_SECONDS


def _validate(row: dict[str, Any], *, provider: str, now: float) -> dict[str, Any]:
    missing = [key for key in ("symbol", "field", "value", "source", "source_type", "as_of") if row.get(key) in (None, "")]
    if missing:
        raise ValueError(f"Evidence row is missing: {', '.join(missing)}")
    field = str(row["field"])
    source_type = str(row["source_type"]).lower()
    authoritative = source_type in AUTHORITATIVE_TYPES
    screening_return = (
        field == "expected_return_inputs"
        and source_type in SCREENING_RETURN_SOURCE_TYPES
        and isinstance(row.get("value"), dict)
        and row["value"].get("model_quality") == "screening_proxy"
    )
    if field in DECISION_FIELDS and not authoritative and not screening_return:
        raise ValueError(
            f"Decision field {field} requires an authoritative source_type; got {source_type}"
        )
    fetched_at = float(row.get("fetched_at") or now)
    expires_at = float(row.get("expires_at") or (fetched_at + _ttl_seconds()))
    return {
        **row,
        "symbol": str(row["symbol"]).upper(),
        "exchange": str(row.get("exchange") or "UNKNOWN").upper(),
        "field": field,
        "source_type": source_type,
        "provider": provider,
        "authoritative": authoritative,
        "fetched_at": fetched_at,
        "expires_at": expires_at,
    }


def refresh_providers(
    providers: list[EvidenceProvider] | None = None,
    *,
    now: float | None = None,
) -> dict[str, Any]:
    current = float(now or time.time())
    providers = providers or [LocalEvidenceProvider()]
    accepted = 0
    rejected: list[str] = []
    for provider in providers:
        try:
            rows = provider.fetch()
        except Exception as exc:
            rejected.append(f"{provider.name}: {exc}")
            continue
        for index, row in enumerate(rows):
            try:
                normalized = _validate(row, provider=provider.name, now=current)
                evidence_store.upsert(normalized)
                accepted += 1
            except Exception as exc:
                rejected.append(f"{provider.name}[{index}]: {exc}")
    return {"accepted": accepted, "rejected": rejected, **evidence_store.status(now=current)}


def enrich_family_with_cached_evidence(
    family: dict[str, Any],
    *,
    now: float | None = None,
) -> dict[str, Any]:
    """Attach only fresh cached observations; stale rows remain visible as flags."""
    current = float(now or time.time())
    used = 0
    stale = 0
    for block in family.get("portfolios") or []:
        for holding in block.get("holdings") or []:
            symbol = str(holding.get("symbol") or "").upper()
            if not symbol:
                continue
            rows = evidence_store.list_for_security(symbol, holding.get("exchange"))
            records: list[dict[str, Any]] = []
            applied_fields: set[str] = set()
            for row in rows:
                public = {
                    key: row.get(key)
                    for key in (
                        "field",
                        "source",
                        "source_url",
                        "source_type",
                        "as_of",
                        "expires_at",
                        "provider",
                        "authoritative",
                    )
                }
                public["stale"] = float(row["expires_at"]) < current
                records.append(public)
                if public["stale"]:
                    stale += 1
                    continue
                if row["field"] in applied_fields:
                    continue
                value = row.get("value")
                if row["field"] == "expected_return_inputs" and isinstance(value, dict):
                    value = {
                        **value,
                        "source": row["source"],
                        "source_type": row["source_type"],
                        "as_of": row["as_of"],
                    }
                holding[row["field"]] = value
                applied_fields.add(str(row["field"]))
                used += 1
            if records:
                holding["evidence_records"] = records
                if any(item["stale"] for item in records):
                    holding.setdefault("data_quality_flags", []).append("STALE_EXTERNAL_EVIDENCE")
    return {"used": used, "stale": stale, **evidence_store.status(now=current)}


def evidence_status() -> dict[str, Any]:
    return evidence_store.status()
