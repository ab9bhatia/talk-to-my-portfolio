"""Backward-compatible local account/tax profile validation.

Profiles are configuration, not inferred personal facts.  Missing fields remain
explicitly UNKNOWN so the advisory engine can lower confidence safely.
"""

from __future__ import annotations

import re
from datetime import date
from enum import StrEnum
from typing import Any


class IndiaResidencyStatus(StrEnum):
    UNKNOWN = "UNKNOWN"
    RESIDENT = "RESIDENT"
    NRI = "NRI"
    NON_RESIDENT = "NON_RESIDENT"


class AccountType(StrEnum):
    UNKNOWN = "UNKNOWN"
    NRO_NON_PIS = "NRO_NON_PIS"
    NRE_PIS = "NRE_PIS"
    RESIDENT_DEMAT = "RESIDENT_DEMAT"
    GIFT_IBU = "GIFT_IBU"
    US_BROKER = "US_BROKER"
    GLOBAL_BROKER = "GLOBAL_BROKER"


class RiskProfile(StrEnum):
    UNKNOWN = "unknown"
    CONSERVATIVE = "conservative"
    MODERATE = "moderate"
    AGGRESSIVE = "aggressive"


class TaxLossHarvestingMode(StrEnum):
    OFF = "off"
    REVIEW_ONLY = "review_only"
    AGGRESSIVE_IF_WEAK = "aggressive_if_weak"


class Repatriability(StrEnum):
    UNKNOWN = "UNKNOWN"
    NON_REPATRIABLE = "NON_REPATRIABLE"
    REPATRIABLE = "REPATRIABLE"
    CONDITIONAL = "CONDITIONAL"


class EstateTaxReviewStatus(StrEnum):
    UNKNOWN = "UNKNOWN"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    REVIEWED = "REVIEWED"


PROFILE_FIELDS = (
    "owner_ref",
    "country_of_residence",
    "india_residency_status",
    "tax_profile",
    "base_currency",
    "account_type",
    "risk_profile",
    "target_return_pct",
    "max_position_pct",
    "max_sector_pct",
    "max_group_exposure_pct",
    "cash_buffer_pct",
    "tax_loss_harvesting_mode",
    "tax_lots_available",
    "gift_product_tax_verified",
    "gift_product_tax_source",
    "gift_product_tax_as_of",
    "repatriability",
    "estate_tax_review_status",
    "permitted_instrument_types",
    "family_transfers_permitted",
)

_COUNTRY_RE = re.compile(r"^[A-Z]{2}$")
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_TAX_PROFILE_RE = re.compile(r"^[A-Z0-9_-]{1,64}$")


def _default_currency(broker: str) -> str:
    return "USD" if broker.strip().lower() == "sarwa" else "INR"


def _optional_text(value: Any, field: str, *, max_length: int = 200) -> str | None:
    if value is None or str(value).strip() == "":
        return None
    text = str(value).strip()
    if len(text) > max_length:
        raise ValueError(f"{field} must be at most {max_length} characters")
    return text


def _enum_value(value: Any, enum_type: type[StrEnum], field: str, default: StrEnum) -> str:
    text = str(value or default.value).strip()
    allowed = {item.value for item in enum_type}
    normalized = text.lower() if enum_type in {RiskProfile, TaxLossHarvestingMode} else text.upper()
    if normalized not in allowed:
        raise ValueError(f"{field} must be one of: {', '.join(sorted(allowed))}")
    return normalized


def _optional_pct(value: Any, field: str, *, allow_zero: bool = False) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    lower_ok = number >= 0 if allow_zero else number > 0
    if not lower_ok or number > 100:
        lower = "0" if allow_zero else "greater than 0"
        raise ValueError(f"{field} must be {lower} and at most 100")
    return round(number, 4)


def _bool_value(value: Any, field: str) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if value in (0, 1):
        return bool(value)
    raise ValueError(f"{field} must be true or false")


def _instrument_types(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise ValueError("permitted_instrument_types must be a list")
    result = []
    for item in value:
        normalized = str(item).strip().lower()
        if not normalized or len(normalized) > 40:
            raise ValueError("permitted_instrument_types entries must be 1-40 characters")
        result.append(normalized)
    return sorted(set(result))


def _profile_input(row: dict[str, Any]) -> dict[str, Any]:
    nested = row.get("account_profile")
    data = dict(nested) if isinstance(nested, dict) else {}
    for field in PROFILE_FIELDS:
        if row.get(field) is not None:
            data[field] = row[field]
    return data


def normalize_account_profile(row: dict[str, Any], *, broker: str) -> dict[str, Any]:
    """Return a complete, JSON-safe profile; absent legacy fields get safe defaults."""
    data = _profile_input(row)
    country = str(data.get("country_of_residence") or "UNKNOWN").strip().upper()
    if country != "UNKNOWN" and not _COUNTRY_RE.fullmatch(country):
        raise ValueError("country_of_residence must be ISO alpha-2 or UNKNOWN")

    currency = str(data.get("base_currency") or _default_currency(broker)).strip().upper()
    if not _CURRENCY_RE.fullmatch(currency):
        raise ValueError("base_currency must be a three-letter currency code")

    tax_profile = str(data.get("tax_profile") or "UNKNOWN").strip().upper()
    if not _TAX_PROFILE_RE.fullmatch(tax_profile):
        raise ValueError("tax_profile may contain only letters, numbers, underscore, or hyphen")

    gift_as_of = _optional_text(data.get("gift_product_tax_as_of"), "gift_product_tax_as_of", max_length=10)
    if gift_as_of:
        try:
            date.fromisoformat(gift_as_of)
        except ValueError as exc:
            raise ValueError("gift_product_tax_as_of must use YYYY-MM-DD") from exc
    gift_source = _optional_text(
        data.get("gift_product_tax_source"), "gift_product_tax_source", max_length=500
    )
    gift_verified = _bool_value(
        data.get("gift_product_tax_verified"), "gift_product_tax_verified"
    )
    if gift_verified and (not gift_source or not gift_as_of):
        raise ValueError(
            "gift_product_tax_verified requires gift_product_tax_source and "
            "gift_product_tax_as_of"
        )

    return {
        "owner_ref": _optional_text(data.get("owner_ref"), "owner_ref", max_length=64),
        "country_of_residence": country,
        "india_residency_status": _enum_value(
            data.get("india_residency_status"),
            IndiaResidencyStatus,
            "india_residency_status",
            IndiaResidencyStatus.UNKNOWN,
        ),
        "tax_profile": tax_profile,
        "base_currency": currency,
        "account_type": _enum_value(
            data.get("account_type"), AccountType, "account_type", AccountType.UNKNOWN
        ),
        "risk_profile": _enum_value(
            data.get("risk_profile"), RiskProfile, "risk_profile", RiskProfile.UNKNOWN
        ),
        "target_return_pct": _optional_pct(data.get("target_return_pct"), "target_return_pct"),
        "max_position_pct": _optional_pct(data.get("max_position_pct"), "max_position_pct"),
        "max_sector_pct": _optional_pct(data.get("max_sector_pct"), "max_sector_pct"),
        "max_group_exposure_pct": _optional_pct(
            data.get("max_group_exposure_pct"), "max_group_exposure_pct"
        ),
        "cash_buffer_pct": _optional_pct(
            data.get("cash_buffer_pct"), "cash_buffer_pct", allow_zero=True
        ),
        "tax_loss_harvesting_mode": _enum_value(
            data.get("tax_loss_harvesting_mode"),
            TaxLossHarvestingMode,
            "tax_loss_harvesting_mode",
            TaxLossHarvestingMode.OFF,
        ),
        "tax_lots_available": _bool_value(
            data.get("tax_lots_available"), "tax_lots_available"
        ),
        "gift_product_tax_verified": gift_verified,
        "gift_product_tax_source": gift_source,
        "gift_product_tax_as_of": gift_as_of,
        "repatriability": _enum_value(
            data.get("repatriability"),
            Repatriability,
            "repatriability",
            Repatriability.UNKNOWN,
        ),
        "estate_tax_review_status": _enum_value(
            data.get("estate_tax_review_status"),
            EstateTaxReviewStatus,
            "estate_tax_review_status",
            EstateTaxReviewStatus.UNKNOWN,
        ),
        "permitted_instrument_types": _instrument_types(
            data.get("permitted_instrument_types")
        ),
        "family_transfers_permitted": _bool_value(
            data.get("family_transfers_permitted"), "family_transfers_permitted"
        ),
    }


def profile_updates_requested(payload: dict[str, Any]) -> bool:
    nested = payload.get("account_profile")
    return any(field in payload for field in PROFILE_FIELDS) or isinstance(nested, dict)


def apply_profile_updates(
    row: dict[str, Any], payload: dict[str, Any], *, broker: str
) -> dict[str, Any]:
    """Validate and persist profile fields only when the caller supplied profile data."""
    if not profile_updates_requested(payload):
        return row
    candidate = normalize_account_profile(row, broker=broker)
    nested = payload.get("account_profile")
    if isinstance(nested, dict):
        candidate.update({field: nested[field] for field in PROFILE_FIELDS if field in nested})
    candidate.update({field: payload[field] for field in PROFILE_FIELDS if field in payload})
    profile = normalize_account_profile(candidate, broker=broker)
    row.update(profile)
    row.pop("account_profile", None)
    return row
