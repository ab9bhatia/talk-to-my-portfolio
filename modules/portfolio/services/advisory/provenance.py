"""Evidence extraction and timestamp normalization for advisory inputs."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from modules.portfolio.services.advisory.models import DataQualityFlag, Evidence


def as_of_text(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=UTC).isoformat().replace("+00:00", "Z")
    return str(value)


def evidence_for_holding(
    holding: dict[str, Any],
    *,
    portfolio_as_of: Any,
) -> tuple[list[Evidence], list[DataQualityFlag]]:
    evidence: list[Evidence] = []
    flags: list[DataQualityFlag] = []
    position_as_of = as_of_text(portfolio_as_of)
    if position_as_of:
        evidence.append(
            Evidence(
                claim=(
                    "Normalized family position value is "
                    f"{holding.get('consolidated_value', 0):.2f} "
                    f"across {len(holding.get('positions') or [])} account position(s)."
                ),
                source="normalized broker/import portfolio snapshot",
                as_of=position_as_of,
                source_type="broker",
            )
        )
    else:
        flags.append(
            DataQualityFlag(
                code="SOURCE_DATE_MISSING",
                severity="warning",
                message="The normalized portfolio snapshot has no source timestamp.",
            )
        )

    return_inputs = holding.get("expected_return_inputs") or {}
    if return_inputs.get("source") and return_inputs.get("as_of"):
        evidence.append(
            Evidence(
                claim="Three-year return scenarios use documented valuation assumptions.",
                source=str(return_inputs["source"]),
                as_of=str(return_inputs["as_of"]),
                source_type=str(return_inputs.get("source_type") or "user_input"),
            )
        )

    if holding.get("governance_event"):
        source = holding.get("governance_event_source")
        event_as_of = holding.get("governance_event_as_of")
        if source and event_as_of:
            evidence.append(
                Evidence(
                    claim=str(holding["governance_event"]),
                    source=str(source),
                    as_of=str(event_as_of),
                    source_type=str(
                        holding.get("governance_event_source_type") or "official_filing"
                    ),
                )
            )
        else:
            flags.append(
                DataQualityFlag(
                    code="GOVERNANCE_PROVENANCE_MISSING",
                    severity="error",
                    message="A governance claim lacks an authoritative source or as-of date.",
                    blocking=True,
                )
            )
    return evidence, flags
