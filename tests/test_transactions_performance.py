from __future__ import annotations

import math

import pytest
from fastapi.testclient import TestClient

from modules.portfolio.db import transaction_ledger
from modules.portfolio.services.performance import (
    align_benchmark_attribution,
    build_performance_summary,
    calculate_twrr,
)
from modules.portfolio.services.tax_lots import build_tax_lots
from modules.portfolio.services.transaction_import import (
    commit_import,
    preview_import,
    rollback_import,
)
from main import app
from shared.config import APP_ROOT_PATH


@pytest.fixture(autouse=True)
def empty_ledger():
    with transaction_ledger.connect() as conn:
        conn.execute("DELETE FROM unresolved_transactions")
        conn.execute("DELETE FROM ledger_transactions")
        conn.execute("DELETE FROM transaction_import_batches")
    yield


def _txn(event: str, day: str, amount: float, **extra):
    return {
        "transaction_id": extra.pop("transaction_id", f"{event}-{day}-{amount}-{len(extra)}"),
        "account_id": extra.pop("account_id", "account-a"),
        "instrument_id": extra.pop("instrument_id", "ins-test"),
        "event_type": event,
        "trade_date": day,
        "quantity": extra.pop("quantity", 0),
        "gross_amount": abs(amount),
        "net_cash_flow": amount,
        "fees": extra.pop("fees", 0),
        "taxes": extra.pop("taxes", 0),
        "currency": extra.pop("currency", "INR"),
        "fx_rate_to_reporting_currency": extra.pop("fx_rate_to_reporting_currency", 1),
        "external_cash_flow": extra.pop("external_cash_flow", False),
        "metadata": extra.pop("metadata", {}),
        **extra,
    }


def _snapshot(day: str, value: float, *, quality: str = "COMPLETE_LIVE", comparable=True):
    return {
        "day_date": day,
        "total_current": value,
        "snapshot_quality": quality,
        "comparable_to_previous": comparable,
    }


def test_simple_buy_hold_xirr():
    summary = build_performance_summary(
        ending_value=110,
        ending_date="2026-01-01",
        scope="instrument",
        instrument_id="ins-test",
        transactions=[_txn("BUY", "2025-01-01", -100, quantity=1)],
        snapshots=[],
    )
    assert summary["xirr_status"] == "AVAILABLE"
    assert summary["xirr_pct"] == pytest.approx(10, abs=0.05)


def test_multiple_contributions_and_partial_sale_have_xirr():
    rows = [
        _txn("DEPOSIT", "2025-01-01", 1000, external_cash_flow=True, instrument_id=None),
        _txn("DEPOSIT", "2025-07-01", 500, external_cash_flow=True, instrument_id=None),
        _txn("WITHDRAWAL", "2025-10-01", -300, external_cash_flow=True, instrument_id=None),
    ]
    result = build_performance_summary(
        ending_value=1400, ending_date="2026-01-01", transactions=rows, snapshots=[]
    )
    assert result["xirr_pct"] is not None
    assert len(result["cashflows"]) == 4


def test_dividend_is_an_instrument_cash_flow():
    result = build_performance_summary(
        ending_value=110,
        ending_date="2026-01-01",
        scope="instrument",
        instrument_id="ins-test",
        transactions=[
            _txn("BUY", "2025-01-01", -100, quantity=1),
            _txn("DIVIDEND", "2025-07-01", 5),
        ],
        snapshots=[],
    )
    assert result["income_contribution"] == 5
    assert result["xirr_pct"] > 10


def test_internal_family_transfer_is_excluded_from_xirr():
    rows = [
        _txn("DEPOSIT", "2025-01-01", 1000, external_cash_flow=True, instrument_id=None),
        _txn("TRANSFER_OUT", "2025-06-01", -200, external_cash_flow=True),
        _txn("TRANSFER_IN", "2025-06-01", 200, external_cash_flow=True, account_id="account-b"),
    ]
    result = build_performance_summary(
        ending_value=1100, ending_date="2026-01-01", transactions=rows, snapshots=[]
    )
    assert [row["amount"] for row in result["cashflows"]] == [-1000.0, 1100.0]


def test_usd_investment_uses_fx_conversion():
    result = build_performance_summary(
        ending_value=880,
        ending_date="2026-01-01",
        scope="instrument",
        instrument_id="ins-test",
        transactions=[_txn("BUY", "2025-01-01", -10, quantity=1, currency="USD", fx_rate_to_reporting_currency=80)],
        snapshots=[],
    )
    assert result["xirr_pct"] == pytest.approx(10, abs=0.05)
    assert result["fx_contribution"] == -790


def test_fee_and_tax_drag_are_included_in_lot_cost():
    result = build_tax_lots([_txn("BUY", "2025-01-01", -100, quantity=10, fees=2, taxes=1)])
    assert result["lots"][0]["cost_basis"] == 103


def test_fifo_partial_disposal():
    rows = [
        _txn("BUY", "2025-01-01", -100, quantity=10, transaction_id="buy-1"),
        _txn("BUY", "2025-02-01", -100, quantity=5, transaction_id="buy-2"),
        _txn("SELL", "2025-03-01", 144, quantity=12, transaction_id="sell-1"),
    ]
    result = build_tax_lots(rows)
    assert result["disposals"][0]["matched_quantity"] == 12
    assert result["lots"][0]["remaining_quantity"] == 0
    assert result["lots"][1]["remaining_quantity"] == 3


def test_split_and_bonus_transform_lots_without_false_gain():
    rows = [
        _txn("BUY", "2025-01-01", -100, quantity=10, transaction_id="buy"),
        _txn("SPLIT", "2025-02-01", 0, quantity=2, transaction_id="split", metadata={"ratio_numerator": 2, "ratio_denominator": 1}),
        _txn("BONUS", "2025-03-01", 0, quantity=5, transaction_id="bonus"),
    ]
    result = build_tax_lots(rows)
    assert sum(row["remaining_quantity"] for row in result["lots"]) == 25
    assert sum(row["remaining_cost_basis"] for row in result["lots"]) == 100


def test_demerger_requires_cost_allocation():
    result = build_tax_lots([_txn("DEMERGER", "2025-01-01", 0)])
    assert result["state"] == "LOT_HISTORY_INCOMPLETE"
    assert "CORPORATE_ACTION_COST_ALLOCATION_REQUIRED" in result["data_quality_flags"]


def test_duplicate_statement_import_is_idempotent():
    row = {
        "source_record_id": "contract-1",
        "account_id": "account-a",
        "instrument_id": "ins-test",
        "event_type": "BUY",
        "trade_date": "2025-01-01",
        "quantity": 1,
        "price": 100,
    }
    first = preview_import(source="zerodha_tradebook", rows=[row])
    second = preview_import(source="zerodha_tradebook", rows=[row])
    assert commit_import(first["import_batch_id"])["committed_count"] == 1
    duplicate = commit_import(second["import_batch_id"])
    assert duplicate["committed_count"] == 0
    assert duplicate["duplicate_count"] == 1


def test_import_batch_is_reversible():
    preview = preview_import(
        source="manual",
        rows=[{
            "source_record_id": "manual-1", "account_id": "account-a",
            "instrument_id": "ins-test", "event_type": "BUY",
            "trade_date": "2025-01-01", "quantity": 1, "price": 100,
        }],
    )
    commit_import(preview["import_batch_id"])
    assert len(transaction_ledger.list_transactions()) == 1
    assert rollback_import(preview["import_batch_id"])["status"] == "ROLLED_BACK"
    assert transaction_ledger.list_transactions() == []


def test_missing_cash_flow_keeps_xirr_unavailable():
    result = build_performance_summary(ending_value=100, transactions=[], snapshots=[])
    assert result["xirr_status"] == "UNAVAILABLE_WITHOUT_CASHFLOWS"
    assert result["xirr_pct"] is None


def test_twrr_neutralizes_external_contribution():
    rows = [_txn("DEPOSIT", "2025-01-02", 100, external_cash_flow=True, instrument_id=None)]
    result = calculate_twrr(
        [_snapshot("2025-01-01", 100, comparable=False), _snapshot("2025-01-02", 210)],
        rows,
    )
    assert result["twrr_pct"] == pytest.approx(10)


def test_degraded_snapshot_period_is_excluded():
    result = calculate_twrr(
        [_snapshot("2025-01-01", 100, comparable=False), _snapshot("2025-01-02", 110, quality="DEGRADED")],
        [],
    )
    assert result["twrr_pct"] is None
    assert result["excluded_periods"][0]["reason"] == "DEGRADED_SNAPSHOT"


def test_benchmark_attribution_is_date_aligned():
    result = align_benchmark_attribution(
        [{"date": "2025-01-01", "value": 100}, {"date": "2025-01-03", "value": 110}],
        [{"date": "2025-01-01", "value": 100}, {"date": "2025-01-02", "value": 200}, {"date": "2025-01-03", "value": 105}],
    )
    assert result["aligned_dates"] == ["2025-01-01", "2025-01-03"]
    assert result["active_return_pct"] == pytest.approx(5)


def test_tax_output_never_claims_final_liability():
    result = build_tax_lots([])
    assert "not a final tax liability" in result["disclaimer"].lower()
    assert not math.isnan(result["lot_coverage_pct"])


def test_api_v1_stays_additive_and_hides_private_account_ids():
    client = TestClient(app)
    api = f"{APP_ROOT_PATH}/api/portfolio"
    version_before = client.get(f"{api}/version").json()["contract_version"]
    preview = client.post(
        f"{api}/transactions/import/preview",
        json={
            "source": "manual",
            "rows": [{
                "source_record_id": "api-deposit-1", "account_code": "TEST",
                "event_type": "DEPOSIT", "trade_date": "2025-01-01",
                "gross_amount": 1000,
            }],
        },
    )
    assert preview.status_code == 200
    body = preview.json()
    assert "account_id" not in body["transactions"][0]
    assert body["transactions"][0]["account_code"] == "TEST"
    batch_id = body["import_batch_id"]
    assert client.post(f"{api}/transactions/import/{batch_id}/commit").status_code == 200
    ledger = client.get(f"{api}/transactions").json()["transactions"]
    assert ledger and "account_id" not in ledger[0]
    assert client.post(f"{api}/transactions/import/{batch_id}/rollback").status_code == 200
    assert client.get(f"{api}/version").json()["contract_version"] == version_before
