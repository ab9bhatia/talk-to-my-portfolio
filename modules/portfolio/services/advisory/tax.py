"""Conservative, account-aware tax and settlement planning notes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from modules.portfolio.services.advisory.models import (
    Action,
    DataQualityFlag,
    TaxRuleReference,
)
from modules.portfolio.services.advisory.tax_rules import rule


@dataclass(frozen=True)
class TaxAssessment:
    tax_note: str
    settlement_note: str
    requires_ca_review: bool
    rule_refs: list[TaxRuleReference] = field(default_factory=list)
    flags: list[DataQualityFlag] = field(default_factory=list)


def _references(*rule_ids: str) -> list[TaxRuleReference]:
    return [rule(rule_id).public_reference() for rule_id in rule_ids]


def _dedupe_references(references: list[TaxRuleReference]) -> list[TaxRuleReference]:
    return list({item.rule_id: item for item in references}.values())


def assess_tax_and_settlement(
    holding: dict[str, Any],
    *,
    action: Action,
) -> TaxAssessment:
    profiles_by_account = holding.get("account_profiles") or {}
    profiles = list(profiles_by_account.values())
    flags: list[DataQualityFlag] = []
    references: list[TaxRuleReference] = []
    tax_notes: list[str] = []
    settlement_notes: list[str] = []
    sell_like = action in {Action.REDUCE, Action.SELL}
    statuses = {
        str(profile.get("india_residency_status") or "UNKNOWN").upper()
        for profile in profiles
    }
    account_types = {
        str(profile.get("account_type") or "UNKNOWN").upper() for profile in profiles
    }
    profile_shapes = {
        (
            str(profile.get("india_residency_status") or "UNKNOWN").upper(),
            str(profile.get("account_type") or "UNKNOWN").upper(),
            str(profile.get("tax_profile") or "UNKNOWN").upper(),
            str(profile.get("base_currency") or "UNKNOWN").upper(),
        )
        for profile in profiles
    }
    requires_ca_review = False

    guardrail_breaches: list[str] = []
    for position in holding.get("positions") or []:
        account_id = str(position.get("account_id") or "")
        profile = profiles_by_account.get(account_id) or {}
        limit = profile.get("max_position_pct")
        weight = float(position.get("account_weight_pct") or 0)
        if limit is not None and weight > float(limit):
            guardrail_breaches.append(
                f"{position.get('account_code') or account_id} {weight:.1f}% > {float(limit):.1f}%"
            )
    if guardrail_breaches:
        flags.append(
            DataQualityFlag(
                code="ACCOUNT_POSITION_LIMIT_EXCEEDED",
                severity="warning",
                message="Account-level position limit exceeded: " + "; ".join(guardrail_breaches),
            )
        )
        settlement_notes.append(
            "Do not add in accounts above their configured position limit; use account-level "
            "placement or reduction review first."
        )

    if any("GIFT" in item for item in account_types):
        gift_profiles = [
            profile
            for profile in profiles
            if "GIFT" in str(profile.get("account_type") or "").upper()
        ]
        verified = bool(gift_profiles) and all(
            profile.get("gift_product_tax_verified") is True
            and profile.get("gift_product_tax_source")
            and profile.get("gift_product_tax_as_of")
            for profile in gift_profiles
        )
        references.extend(_references("IFSCA_PRODUCT_TAX_EVIDENCE"))
        if not verified:
            requires_ca_review = True
            flags.append(
                DataQualityFlag(
                    code="TAX_REVIEW_REQUIRED",
                    severity="warning",
                    message="GIFT tax treatment lacks exact product/share-class evidence.",
                    blocking=sell_like,
                )
            )
            tax_notes.append(
                (
                    "TAX_REVIEW_REQUIRED: do not infer zero tax from GIFT City; verify the exact "
                    "product, share class, offer document, investor eligibility, and current "
                    "tax note."
                )
            )
        else:
            tax_notes.append(
                "Use the dated product-specific GIFT tax evidence; the engine does not infer "
                "zero tax from the account label."
            )
        settlement_notes.append("Confirm product redemption and account settlement terms.")

    if "NRI" in statuses or "NON_RESIDENT" in statuses:
        references.extend(
            _references(
                "INDIA_CAPITAL_GAIN_LOTS",
                "INDIA_NRI_WITHHOLDING",
                "RBI_NRI_SETTLEMENT",
            )
        )
        tax_notes.append(
            "Indian-company share gains can remain Indian-tax relevant for an NRI; broker TDS is "
            "not necessarily the final liability."
        )
        if "NRO_NON_PIS" in account_types:
            settlement_notes.append(
                "Keep non-repatriable sale proceeds in the NRO route until taxes, settlement, "
                "and repatriation eligibility are confirmed."
            )
        if "NRE_PIS" in account_types:
            settlement_notes.append(
                "Confirm designated-bank NRE/PIS settlement and repatriation routing before "
                "reallocating proceeds."
            )
        if not account_types.intersection({"NRO_NON_PIS", "NRE_PIS"}):
            settlement_notes.append(
                "Confirm NRO Non-PIS/NRE-PIS classification and settlement constraints before "
                "reallocating proceeds."
            )
    elif "RESIDENT" in statuses:
        references.extend(_references("INDIA_CAPITAL_GAIN_LOTS"))
        tax_notes.append("Resident tax planning requires FIFO acquisition dates and tax lots.")
        settlement_notes.append(
            "Apply normal broker settlement timing before treating proceeds as deployable."
        )
    else:
        tax_notes.append("Residency, account type, and lot-level tax inputs are unavailable.")
        settlement_notes.append(
            "Confirm broker/account settlement constraints before redeployment."
        )
        if sell_like:
            requires_ca_review = True
            flags.append(
                DataQualityFlag(
                    code="TAX_PROFILE_MISSING",
                    severity="warning",
                    message="A sell-like action lacks account residency and tax profile data.",
                )
            )

    global_account = bool(account_types.intersection({"US_BROKER", "GLOBAL_BROKER"}))
    instrument_type = str(holding.get("instrument_type") or "").lower()
    exchange = str(holding.get("exchange") or "").upper()
    possible_us_security = instrument_type in {"equity", "etf", "mutual_fund"} and (
        global_account or exchange in {"US", "NYSE", "NASDAQ", "ARCA"}
    )
    if possible_us_security:
        references.extend(_references("US_SITUS_ESTATE_REVIEW"))
        tax_notes.append(
            "Capital gains, dividend withholding, and possible U.S.-situs estate exposure are "
            "separate reviews; issuer/fund domicile must be verified."
        )
        requires_ca_review = True
        flags.append(
            DataQualityFlag(
                code="US_SITUS_CLASSIFICATION_REQUIRED",
                severity="info",
                message="Issuer/fund domicile and estate-tax status are not verified.",
            )
        )

    if len(profile_shapes) > 1:
        requires_ca_review = True
        flags.append(
            DataQualityFlag(
                code="MIXED_TAX_PROFILES",
                severity="warning",
                message="The consolidated security spans accounts with different tax profiles.",
                blocking=sell_like,
            )
        )
        tax_notes.append(
            "Account-specific tax treatment must be evaluated separately before allocating a sale."
        )

    profile_lots_available = bool(profiles) and all(
        profile.get("tax_lots_available") is True for profile in profiles
    )
    lots_available = bool(holding.get("tax_lots_available")) or profile_lots_available
    india_tax_profile = bool(statuses.intersection({"RESIDENT", "NRI", "NON_RESIDENT"}))
    if sell_like and india_tax_profile and not lots_available:
        requires_ca_review = True
        flags.append(
            DataQualityFlag(
                code="MISSING_TAX_LOTS",
                severity="warning",
                message="FIFO tax lots and acquisition dates are required for gain/loss treatment.",
            )
        )
        if "RESIDENT" in statuses and float(holding.get("pnl") or 0) < 0:
            tax_notes.append(
                " The loss may be reviewed for harvesting only if the investment case is "
                "independently weak."
            )

    if holding.get("is_suspended") is True or holding.get("is_tradable") is False:
        settlement_notes = [
            "No market sale is assumed; use broker/exchange recovery, relisting, or "
            "eligible off-market guidance."
        ]
    return TaxAssessment(
        tax_note=" ".join(tax_notes),
        settlement_note=" ".join(dict.fromkeys(settlement_notes)),
        requires_ca_review=requires_ca_review,
        rule_refs=_dedupe_references(references),
        flags=flags,
    )
