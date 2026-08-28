"""Conservative Milestone 1 tax and settlement safety notes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from modules.portfolio.services.advisory.models import Action, DataQualityFlag


@dataclass(frozen=True)
class TaxAssessment:
    tax_note: str
    settlement_note: str
    requires_ca_review: bool
    flags: list[DataQualityFlag] = field(default_factory=list)


def assess_tax_and_settlement(
    holding: dict[str, Any],
    *,
    action: Action,
) -> TaxAssessment:
    profiles = list((holding.get("account_profiles") or {}).values())
    flags: list[DataQualityFlag] = []
    sell_like = action in {Action.REDUCE, Action.SELL}
    statuses = {
        str(profile.get("india_residency_status") or "").upper() for profile in profiles
    }
    account_types = {str(profile.get("account_type") or "").upper() for profile in profiles}

    if any("GIFT" in item for item in account_types):
        verified = all(profile.get("gift_product_tax_verified") is True for profile in profiles)
        if not verified:
            flags.append(
                DataQualityFlag(
                    code="TAX_REVIEW_REQUIRED",
                    severity="warning",
                    message="GIFT tax treatment lacks exact product/share-class evidence.",
                    blocking=sell_like,
                )
            )
            return TaxAssessment(
                tax_note=(
                    "TAX_REVIEW_REQUIRED: do not infer zero tax from GIFT City; verify the exact "
                    "product, share class, offer document, investor eligibility, and current "
                    "tax note."
                ),
                settlement_note="Confirm product redemption and account settlement terms.",
                requires_ca_review=True,
                flags=flags,
            )

    if "NRI" in statuses or "NON_RESIDENT" in statuses:
        note = (
            "Indian-company share gains can remain Indian-tax relevant for an NRI; broker TDS is "
            "not necessarily the final liability."
        )
        settlement = (
            "Confirm NRO Non-PIS/NRE-PIS settlement and repatriation constraints before "
            "reallocating proceeds."
        )
    elif "RESIDENT" in statuses:
        note = "Resident tax planning requires FIFO acquisition dates and tax lots."
        settlement = "Apply normal broker settlement timing before treating proceeds as deployable."
    else:
        note = "Residency, account type, and lot-level tax inputs are unavailable."
        settlement = "Confirm broker/account settlement constraints before redeployment."
        if sell_like:
            flags.append(
                DataQualityFlag(
                    code="TAX_PROFILE_MISSING",
                    severity="warning",
                    message="A sell-like action lacks account residency and tax profile data.",
                )
            )

    requires_ca_review = False
    if sell_like and not holding.get("tax_lots_available"):
        requires_ca_review = True
        flags.append(
            DataQualityFlag(
                code="MISSING_TAX_LOTS",
                severity="warning",
                message="FIFO tax lots and acquisition dates are required for gain/loss treatment.",
            )
        )
        if "RESIDENT" in statuses and float(holding.get("pnl") or 0) < 0:
            note += (
                " The loss may be reviewed for harvesting only if the investment case is "
                "independently weak."
            )

    if holding.get("is_suspended") is True or holding.get("is_tradable") is False:
        settlement = (
            "No market sale is assumed; use broker/exchange recovery, relisting, or "
            "eligible off-market guidance."
        )
    return TaxAssessment(note, settlement, requires_ca_review, flags)
