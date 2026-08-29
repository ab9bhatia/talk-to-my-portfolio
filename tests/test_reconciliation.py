from __future__ import annotations

import pytest

from modules.portfolio.db import instrument_master as store
from modules.portfolio.services.advisory.service import build_advisory_payload
from modules.portfolio.services.instrument_master import resolve_holding
from modules.portfolio.services.holdings_view import aggregate_holdings_across_accounts
from modules.portfolio.services.groww_portfolio import _normalize_groww_holding
from modules.portfolio.services.reconciliation import reconcile_family


def _holding(
    symbol: str,
    *,
    exchange: str = "NSE",
    isin: str | None = None,
    quantity: float = 1,
    broker_value: float = 100,
    market_price: float = 100,
    asset_class: str = "equity",
    **extra,
):
    return {
        "symbol": symbol,
        "exchange": exchange,
        "isin": isin,
        "quantity": quantity,
        "avg_price": 80,
        "last_price": broker_value / quantity if quantity else 0,
        "current_value": broker_value,
        "invested": quantity * 80,
        "pnl": broker_value - quantity * 80,
        "market_price": market_price,
        "asset_class": asset_class,
        "account_id": extra.pop("account_id", "account-a"),
        "account_code": extra.pop("account_code", "AA"),
        "broker": extra.pop("broker", "test"),
        **extra,
    }


def _family(*holdings, account_code: str = "AA"):
    value = sum(float(row.get("current_value") or 0) for row in holdings)
    return {
        "cached_at": "2026-08-28T13:00:00+00:00",
        "summary": {"total_current_value": value},
        "portfolios": [
            {
                "account_id": "account-a",
                "account_code": account_code,
                "broker": "test",
                "summary": {"total_current_value": value},
                "holdings": list(holdings),
            }
        ],
    }


def test_same_isin_across_symbols_consolidates_to_one_identity():
    first = _holding("OLDNAME7B", isin="INE7B0000001")
    second = _holding("NEWNAME7B", exchange="BSE", isin="INE7B0000001")
    resolved = reconcile_family(_family(first, second))
    rows = resolved["reconciliation"]["by_security"]
    assert len(rows) == 1
    assert rows[0]["isin"] == "INE7B0000001"
    assert rows[0]["marked_value"] == 200


def test_same_symbol_on_different_exchanges_stays_distinct_without_isin():
    nse = resolve_holding(_holding("DUAL7B", exchange="NSE"))["instrument"]
    us = resolve_holding(_holding("DUAL7B", exchange="US", currency="USD"))["instrument"]
    assert nse["instrument_id"] != us["instrument_id"]


def test_mutual_fund_isin_resolves_to_human_readable_scheme():
    resolved = resolve_holding(
        _holding(
            "INF7B000001",
            exchange="MF",
            isin="INF7B000001",
            asset_class="mf",
            fund_name="Example Direct Growth Fund",
        )
    )["instrument"]
    assert resolved["exchange"] == "AMFI"
    assert resolved["instrument_type"] == "mutual_fund"
    assert resolved["display_name"] == "Example Direct Growth Fund"


def test_us_etf_classification_uses_quote_type_not_suffix():
    resolved = resolve_holding(
        _holding("QUAL7B", exchange="US", currency="USD", quote_type="ETF")
    )["instrument"]
    assert resolved["instrument_type"] == "etf"
    assert resolved["domicile"] == "US"


def test_broker_and_market_values_remain_separate():
    row = _holding(
        "PROVENANCE7B",
        quantity=10,
        broker_value=1_000,
        market_price=90,
        broker_reported_price=100,
        broker_reported_value=1_000,
        broker_price_as_of="2026-08-28T13:00:00Z",
        market_price_as_of="2026-08-27T13:00:00Z",
        market_price_source="yahoo",
    )
    reconciled = reconcile_family(_family(row))["portfolios"][0]["holdings"][0]
    assert reconciled["broker_reported_price"] == 100
    assert reconciled["market_price"] == 90
    assert reconciled["broker_reported_value"] == 1_000
    assert reconciled["marked_value"] == 900
    assert reconciled["current_value"] == 1_000


def test_groww_missing_ltp_is_explicitly_cost_basis_not_market_price(monkeypatch):
    monkeypatch.setattr(
        "modules.portfolio.services.groww_portfolio.get_account_code",
        lambda _account_id: "HB",
    )
    row = _normalize_groww_holding(
        {
            "trading_symbol": "ADANIPORTS",
            "quantity": 410,
            "average_price": 984.74,
            "exchange": "NSE",
        },
        "groww-hb",
        ltp_map={},
    )
    assert row is not None
    assert row["broker_reported_price"] == 984.74
    assert row["broker_price_source"] == "cost_basis_fallback"
    assert row["market_price"] is None
    assert row["market_price_unavailable"] is True


def test_family_quote_consensus_replaces_groww_cost_basis_fallback():
    zerodha_rows = [
        _holding(
            "ADANIPORTS",
            quantity=quantity,
            broker_value=quantity * 1707.5,
            market_price=None,
            account_id=f"zerodha-{code.lower()}",
            account_code=code,
            broker="zerodha",
        )
        for code, quantity in (("AB", 225), ("RB", 75), ("SB", 160))
    ]
    groww = _holding(
        "ADANIPORTS",
        quantity=410,
        broker_value=410 * 984.74,
        market_price=None,
        avg_price=984.74,
        last_price=984.74,
        broker_reported_price=984.74,
        broker_price_source="cost_basis_fallback",
        market_price_unavailable=True,
        account_id="groww-hb",
        account_code="HB",
        broker="groww",
    )
    groww["avg_price"] = 984.74
    groww["invested"] = groww["current_value"]
    family = {
        "cached_at": "2026-08-29T09:00:00Z",
        "summary": {},
        "portfolios": [
            {
                "account_id": row["account_id"],
                "account_code": row["account_code"],
                "broker": row["broker"],
                "holdings": [row],
            }
            for row in [*zerodha_rows, groww]
        ],
    }

    reconciled = reconcile_family(family)
    rows = [
        row
        for block in reconciled["portfolios"]
        for row in block["holdings"]
    ]
    assert {row["market_price"] for row in rows} == {1707.5}
    assert next(row for row in rows if row["account_code"] == "HB")["broker_reported_price"] == 984.74
    merged = aggregate_holdings_across_accounts(rows)
    assert len(merged) == 1
    assert merged[0]["last_price"] == 1707.5
    assert merged[0]["current_value"] == round(870 * 1707.5, 2)


def test_epoch_cache_timestamp_is_exposed_as_iso_quote_provenance():
    family = _family(_holding("TIMEPROVENANCE7B", market_price=100))
    family["cached_at"] = 1787994000.0
    row = reconcile_family(family)["portfolios"][0]["holdings"][0]
    assert row["reconciliation"]["market_price_as_of"].endswith("Z")
    assert row["reconciliation"]["market_session_date"] == "2026-08-29"


def test_small_timing_delta_is_non_blocking():
    row = _holding("TIMING7B", quantity=100, broker_value=10_080, market_price=100)
    reconciled = reconcile_family(_family(row))["portfolios"][0]["holdings"][0]
    assert reconciled["reconciliation_state"] == "RECONCILED_WITH_TIMING_DIFFERENCE"
    assert reconciled["reconciliation_blocking"] is False


def test_large_value_weighted_delta_is_blocking():
    row = _holding("BLOCK7B", quantity=100, broker_value=13_000, market_price=100)
    reconciled = reconcile_family(_family(row))["portfolios"][0]["holdings"][0]
    assert reconciled["reconciliation_state"] == "BLOCKING_MISMATCH"
    assert reconciled["reconciliation_blocking"] is True


def test_fx_mismatch_is_explained_separately():
    row = _holding(
        "FX7B",
        exchange="US",
        broker_value=900,
        market_price=900,
        currency="USD",
        last_price_usd=10,
        fx_rate=83,
        fx_source="RBI_REFERENCE",
        fx_as_of="2026-08-28",
    )
    reconciled = reconcile_family(_family(row))["portfolios"][0]["holdings"][0]
    assert reconciled["reconciliation_state"] == "WARNING"
    assert any("FX_MISMATCH" in reason for reason in reconciled["reconciliation"]["reasons"])


def test_demerger_creates_corporate_action_review_and_advisory_reconcile():
    row = _holding("DEMERGER7B", isin="INE7B0000002")
    instrument = resolve_holding(row)["instrument"]
    store.add_corporate_action(
        {
            "instrument_id": instrument["instrument_id"],
            "action_type": "DEMERGER",
            "effective_date": "2026-08-20",
            "source_document": "NSE corporate action notice 7B",
            "source_as_of": "2026-08-20",
        }
    )
    family = reconcile_family(_family(row))
    reconciled = family["portfolios"][0]["holdings"][0]
    assert reconciled["reconciliation_state"] == "CORPORATE_ACTION_REVIEW"
    recommendation = build_advisory_payload(family)["recommendations"][0]
    assert recommendation["action"] == "RECONCILE"
    assert recommendation["expected_3y_irr"]["method"] == "unavailable_reconciliation"


def test_suspended_security_remains_operationally_untradeable():
    row = _holding("SUSPEND7B", is_suspended=True, is_tradable=False)
    reconciled = reconcile_family(_family(row))["portfolios"][0]["holdings"][0]
    assert reconciled["is_tradable"] is False
    assert reconciled["reconciliation"]["tradability_status"] == "SUSPENDED"


def test_manual_override_requires_source_and_writes_audit():
    instrument = resolve_holding(_holding("OVERRIDE7B"))["instrument"]
    with pytest.raises(ValueError, match="source_document"):
        store.create_override(
            {
                "instrument_id": instrument["instrument_id"],
                "override_type": "VALUE_EXPLANATION",
                "reason": "Timing difference explained",
                "as_of_date": "2026-08-28",
                "approved_by": "portfolio-owner",
            }
        )
    override = store.create_override(
        {
            "instrument_id": instrument["instrument_id"],
            "override_type": "VALUE_EXPLANATION",
            "value": {"accepted_delta": 25},
            "reason": "Broker closing auction timestamp differs from independent mark.",
            "source_document": "Broker statement dated 2026-08-28",
            "as_of_date": "2026-08-28",
            "approved_by": "portfolio-owner",
        }
    )
    audit = store.override_audit(override["override_id"])
    assert audit[0]["action"] == "CREATED"
    assert audit[0]["actor"] == "portfolio-owner"


def test_unresolved_identity_blocks_advisory_action():
    row = _holding("", exchange="UNKNOWN")
    family = reconcile_family(_family(row))
    recommendation = build_advisory_payload(family)["recommendations"][0]
    assert recommendation["action"] == "RECONCILE"
    assert recommendation["reconciliation_state"] == "UNRESOLVED_IDENTITY"


def test_family_totals_equal_account_totals():
    family = reconcile_family(
        _family(
            _holding("TOTALA7B", broker_value=1_000, market_price=1_000),
            _holding("TOTALB7B", broker_value=2_000, market_price=2_000),
        )
    )
    reconciliation = family["reconciliation"]
    account_total = sum(row["marked_value"] for row in reconciliation["by_account"])
    assert account_total == reconciliation["summary"]["family_marked_value"]
    assert reconciliation["summary"]["family_reconciliation_delta"] == 0
