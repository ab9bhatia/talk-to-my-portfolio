"""Milestone 2 tax-rule and account-aware safety tests."""

from __future__ import annotations

from modules.portfolio.services.advisory.models import Action
from modules.portfolio.services.advisory.tax import assess_tax_and_settlement
from modules.portfolio.services.advisory.tax_rules import LAST_REVIEWED, RULES


def _holding(*profiles: dict, **overrides) -> dict:
    row = {
        "symbol": "SYNTH",
        "instrument_type": "equity",
        "exchange": "NSE",
        "pnl": -100,
        "account_profiles": {f"a{index}": profile for index, profile in enumerate(profiles)},
    }
    row.update(overrides)
    return row


def test_tax_rules_have_effective_dates_sources_and_required_inputs():
    assert LAST_REVIEWED == "2026-08-28"
    assert RULES
    for item in RULES.values():
        assert item.effective_from == "2026-08-28"
        assert item.source.startswith(("Income Tax", "Reserve Bank", "International", "Internal"))
        assert item.source_url.startswith("https://")
        assert item.required_inputs


def test_nri_sell_distinguishes_withholding_from_final_tax_and_requires_lots():
    assessment = assess_tax_and_settlement(
        _holding(
            {
                "india_residency_status": "NRI",
                "account_type": "NRO_NON_PIS",
                "tax_lots_available": False,
            }
        ),
        action=Action.REDUCE,
    )
    refs = {item.rule_id for item in assessment.rule_refs}
    flags = {item.code for item in assessment.flags}
    assert "not necessarily the final liability" in assessment.tax_note
    assert "INDIA_NRI_WITHHOLDING" in refs
    assert "RBI_NRI_SETTLEMENT" in refs
    assert "MISSING_TAX_LOTS" in flags
    assert assessment.requires_ca_review is True


def test_verified_gift_profile_still_never_infers_zero_tax():
    assessment = assess_tax_and_settlement(
        _holding(
            {
                "account_type": "GIFT_IBU",
                "gift_product_tax_verified": True,
                "gift_product_tax_source": "Synthetic product tax memo",
                "gift_product_tax_as_of": "2026-08-01",
            }
        ),
        action=Action.WATCH,
    )
    assert "does not infer zero tax" in assessment.tax_note
    assert "IFSCA_PRODUCT_TAX_EVIDENCE" in {
        item.rule_id for item in assessment.rule_refs
    }
    assert "TAX_REVIEW_REQUIRED" not in {item.code for item in assessment.flags}


def test_global_security_tracks_us_situs_as_a_review_not_a_tax_claim():
    assessment = assess_tax_and_settlement(
        _holding(
            {
                "account_type": "GLOBAL_BROKER",
                "country_of_residence": "AE",
            },
            exchange="US",
        ),
        action=Action.WATCH,
    )
    assert "possible U.S.-situs estate exposure" in assessment.tax_note
    assert "US_SITUS_ESTATE_REVIEW" in {item.rule_id for item in assessment.rule_refs}
    assert assessment.requires_ca_review is True


def test_mixed_account_profiles_are_not_net_recommended_as_one_tax_lot():
    assessment = assess_tax_and_settlement(
        _holding(
            {"india_residency_status": "RESIDENT", "account_type": "RESIDENT_DEMAT"},
            {"india_residency_status": "NRI", "account_type": "NRO_NON_PIS"},
        ),
        action=Action.REDUCE,
    )
    assert "MIXED_TAX_PROFILES" in {item.code for item in assessment.flags}
    assert "evaluated separately" in assessment.tax_note
    assert assessment.requires_ca_review is True
