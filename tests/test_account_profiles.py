"""Account profile schema tests; all fixtures are synthetic and local."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from modules.portfolio.account_profile import (
    apply_profile_updates,
    normalize_account_profile,
)
from modules.portfolio.accounts_loader import build_account_registry
from modules.portfolio.router import SetupAccountUpdatePayload


def _legacy_raw() -> dict:
    return {
        "zerodha": [
            {
                "id": "sample",
                "code": "SA",
                "label": "Synthetic",
                "user_id": "SAMPLE",
                "enabled": True,
            }
        ],
        "groww": [],
        "sarwa": [],
        "custom": [],
        "legacy_zerodha_account_id": "sample",
    }


def test_legacy_account_rows_get_safe_backward_compatible_defaults():
    zerodha, _, _, _, _, legacy = build_account_registry(_legacy_raw())
    profile = zerodha["sample"]["account_profile"]
    assert legacy == "sample"
    assert profile["country_of_residence"] == "UNKNOWN"
    assert profile["india_residency_status"] == "UNKNOWN"
    assert profile["account_type"] == "UNKNOWN"
    assert profile["base_currency"] == "INR"
    assert profile["tax_loss_harvesting_mode"] == "off"


def test_sarwa_legacy_currency_defaults_to_usd_without_inferring_tax_status():
    profile = normalize_account_profile({}, broker="sarwa")
    assert profile["base_currency"] == "USD"
    assert profile["account_type"] == "UNKNOWN"
    assert profile["country_of_residence"] == "UNKNOWN"


def test_profile_update_is_validated_and_normalized():
    row = {"id": "sample", "code": "SA"}
    apply_profile_updates(
        row,
        {
            "country_of_residence": "ae",
            "india_residency_status": "nri",
            "tax_profile": "ae_nri_india",
            "account_type": "nro_non_pis",
            "risk_profile": "aggressive",
            "max_position_pct": 12,
            "tax_loss_harvesting_mode": "review_only",
        },
        broker="zerodha",
    )
    assert row["country_of_residence"] == "AE"
    assert row["india_residency_status"] == "NRI"
    assert row["tax_profile"] == "AE_NRI_INDIA"
    assert row["account_type"] == "NRO_NON_PIS"
    assert row["max_position_pct"] == 12


def test_invalid_account_profile_fails_closed():
    with pytest.raises(ValueError, match="account_type"):
        normalize_account_profile({"account_type": "MAGIC_TAX_FREE"}, broker="zerodha")


def test_setup_payload_accepts_nested_profile_and_rejects_out_of_range_limits():
    payload = SetupAccountUpdatePayload(
        account_profile={
            "country_of_residence": "AE",
            "india_residency_status": "NRI",
            "account_type": "NRO_NON_PIS",
            "max_position_pct": 12,
        }
    )
    dumped = payload.model_dump(exclude_none=True)
    assert dumped["account_profile"]["account_type"] == "NRO_NON_PIS"

    with pytest.raises(ValidationError):
        SetupAccountUpdatePayload(max_position_pct=101)
