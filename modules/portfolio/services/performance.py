"""Coverage-aware XIRR, TWRR, return bridge, and attribution."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from math import isfinite
from typing import Any

from modules.portfolio.db import daily_history, transaction_ledger
from modules.portfolio.services.tax_lots import build_tax_lots


GOOD_QUALITY = {"COMPLETE_LIVE", "COMPLETE_MIXED"}


def xirr(cashflows: list[tuple[str, float]]) -> float | None:
    dated = sorted((date.fromisoformat(day), float(value)) for day, value in cashflows if value)
    if len(dated) < 2 or not any(v < 0 for _, v in dated) or not any(v > 0 for _, v in dated):
        return None
    origin = dated[0][0]

    def npv(rate: float) -> float:
        return sum(value / ((1 + rate) ** ((day - origin).days / 365.0)) for day, value in dated)

    low = -0.999999
    high = 1.0
    low_value = npv(low)
    high_value = npv(high)
    while low_value * high_value > 0 and high < 1_000_000:
        high *= 2
        high_value = npv(high)
    if low_value * high_value > 0:
        return None
    for _ in range(200):
        middle = (low + high) / 2
        middle_value = npv(middle)
        if abs(middle_value) < 1e-8:
            return middle
        if low_value * middle_value <= 0:
            high = middle
        else:
            low = middle
            low_value = middle_value
    result = (low + high) / 2
    return result if isfinite(result) else None


def build_performance_summary(
    *,
    ending_value: float,
    ending_date: str | None = None,
    scope: str = "family",
    account_id: str | None = None,
    instrument_id: str | None = None,
    transactions: list[dict[str, Any]] | None = None,
    snapshots: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    txns = transactions if transactions is not None else transaction_ledger.list_transactions(
        account_id=account_id, instrument_id=instrument_id, limit=10000
    )
    unresolved = transaction_ledger.list_unresolved() if transactions is None else []
    lot_result = build_tax_lots(txns)
    end_day = ending_date or date.today().isoformat()
    cashflows = _xirr_cashflows(txns, scope=scope, instrument_id=instrument_id)
    cashflows.append((end_day, float(ending_value)))
    rate = xirr(cashflows)
    audited_rows = len(txns)
    cashflow_coverage = audited_rows / (audited_rows + len(unresolved)) * 100 if audited_rows or unresolved else 0.0
    status = "AVAILABLE" if rate is not None and not unresolved else "PARTIAL" if rate is not None else "UNAVAILABLE_WITHOUT_CASHFLOWS"

    valuation_rows = snapshots
    if valuation_rows is None:
        valuation_rows = daily_history.growth_series(scope=scope, account_id=account_id, days=3650)
    twrr_result = calculate_twrr(valuation_rows, txns, scope=scope)
    bridge = return_bridge(valuation_rows, txns, ending_value=ending_value, scope=scope)
    realized = sum(float(row.get("realized_gain") or 0) for row in lot_result["disposals"])
    fees = sum(abs(float(row.get("fees") or 0)) * _fx(row) for row in txns)
    taxes = sum(abs(float(row.get("taxes") or 0)) * _fx(row) for row in txns)
    income = sum(
        float(row.get("net_cash_flow") or 0) * _fx(row)
        for row in txns
        if row.get("event_type") in {"DIVIDEND", "INTEREST"}
    )
    valuation_good = sum(1 for row in valuation_rows if _snapshot_usable(row))
    valuation_coverage = valuation_good / len(valuation_rows) * 100 if valuation_rows else 0.0
    flags = sorted(
        set(lot_result["data_quality_flags"])
        | ({"UNRESOLVED_TRANSACTIONS"} if unresolved else set())
        | ({"MISSING_DATED_CASHFLOWS"} if rate is None else set())
        | set(twrr_result["data_quality_flags"])
    )
    return {
        "scope": scope,
        "as_of": end_day,
        "ending_value": round(float(ending_value), 2),
        "xirr_pct": round(rate * 100, 4) if rate is not None else None,
        "twrr_pct": twrr_result["twrr_pct"],
        "realized_return": round(realized, 2),
        "unrealized_return": round(float(ending_value) - sum(float(lot["remaining_cost_basis"]) for lot in lot_result["lots"]), 2),
        "income_contribution": round(income, 2),
        "fee_drag": round(fees, 2),
        "tax_drag": round(taxes, 2),
        "fx_contribution": round(sum(_fx_contribution(row) for row in txns), 2),
        "cashflow_coverage_pct": round(cashflow_coverage, 2),
        "lot_coverage_pct": lot_result["lot_coverage_pct"],
        "valuation_coverage_pct": round(valuation_coverage, 2),
        "xirr_status": status,
        "excluded_periods": twrr_result["excluded_periods"],
        "data_quality_flags": flags,
        "return_bridge": bridge,
        "cashflows": [{"date": day, "amount": round(value, 2)} for day, value in cashflows],
        "disclaimer": "Performance and tax-lot outputs are planning analytics, not a final tax filing claim.",
    }


def calculate_twrr(
    snapshots: list[dict[str, Any]], transactions: list[dict[str, Any]], *, scope: str = "family"
) -> dict[str, Any]:
    ordered = sorted(snapshots, key=lambda row: row.get("day_date") or row.get("date") or "")
    if len(ordered) < 2:
        return {"twrr_pct": None, "periods": [], "excluded_periods": [], "data_quality_flags": ["INSUFFICIENT_VALUATIONS"]}
    product = 1.0
    periods: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for previous, current in zip(ordered, ordered[1:]):
        start_day = str(previous.get("day_date") or previous.get("date"))
        end_day = str(current.get("day_date") or current.get("date"))
        if not _snapshot_quality_usable(previous) or not _snapshot_usable(current):
            excluded.append({"start": start_day, "end": end_day, "reason": "DEGRADED_SNAPSHOT"})
            continue
        start = float(previous.get("total_current") or previous.get("value") or 0)
        ending = float(current.get("total_current") or current.get("value") or 0)
        if start <= 0:
            excluded.append({"start": start_day, "end": end_day, "reason": "NON_POSITIVE_START_VALUE"})
            continue
        external_flow = sum(
            _external_flow(row, scope=scope)
            for row in transactions
            if start_day < str(row.get("trade_date") or "") <= end_day
        )
        period_return = (ending - external_flow) / start - 1
        product *= 1 + period_return
        periods.append({"start": start_day, "end": end_day, "external_flow": round(external_flow, 2), "return_pct": round(period_return * 100, 4)})
    return {
        "twrr_pct": round((product - 1) * 100, 4) if periods else None,
        "periods": periods,
        "excluded_periods": excluded,
        "data_quality_flags": ["DEGRADED_VALUATION_PERIODS_EXCLUDED"] if excluded else [],
    }


def return_bridge(
    snapshots: list[dict[str, Any]], transactions: list[dict[str, Any]], *, ending_value: float, scope: str
) -> dict[str, float | None]:
    ordered = sorted(snapshots, key=lambda row: row.get("day_date") or row.get("date") or "")
    starting = float(ordered[0].get("total_current") or ordered[0].get("value") or 0) if ordered else None
    contributions = sum(max(0.0, _external_flow(row, scope=scope)) for row in transactions)
    withdrawals = sum(max(0.0, -_external_flow(row, scope=scope)) for row in transactions)
    fees = sum(abs(float(row.get("fees") or 0)) * _fx(row) for row in transactions)
    taxes = sum(abs(float(row.get("taxes") or 0)) * _fx(row) for row in transactions)
    fx_impact = sum(_fx_contribution(row) for row in transactions)
    investment = None
    if starting is not None:
        investment = float(ending_value) - starting - contributions + withdrawals - fx_impact + fees + taxes
    return {
        "starting_value": round(starting, 2) if starting is not None else None,
        "contributions": round(contributions, 2),
        "withdrawals": round(withdrawals, 2),
        "investment_gain_loss": round(investment, 2) if investment is not None else None,
        "fx_impact": round(fx_impact, 2),
        "fees_taxes": round(fees + taxes, 2),
        "ending_value": round(float(ending_value), 2),
    }


def attribution(transactions: list[dict[str, Any]]) -> dict[str, Any]:
    by_instrument: dict[str, float] = defaultdict(float)
    by_account: dict[str, float] = defaultdict(float)
    for row in transactions:
        contribution = float(row.get("net_cash_flow") or 0) * _fx(row)
        by_instrument[str(row.get("instrument_id") or "CASH")] += contribution
        by_account[str(row.get("account_id") or "UNKNOWN")] += contribution
    return {
        "by_instrument": [{"instrument_id": key, "cash_contribution": round(value, 2)} for key, value in sorted(by_instrument.items())],
        "by_account": [{"account_id": key, "cash_contribution": round(value, 2)} for key, value in sorted(by_account.items())],
    }


def align_benchmark_attribution(
    portfolio_series: list[dict[str, Any]], benchmark_series: list[dict[str, Any]]
) -> dict[str, Any]:
    """Align portfolio and benchmark observations by date; never compare mismatched dates."""
    portfolio = {
        str(row.get("date") or row.get("day_date")): float(
            row.get("indexed_value") or row.get("value") or row.get("total_current") or 0
        )
        for row in portfolio_series
    }
    benchmark = {
        str(row.get("date") or row.get("day_date")): float(
            row.get("indexed_value") or row.get("value") or 0
        )
        for row in benchmark_series
    }
    dates = sorted(set(portfolio) & set(benchmark))
    if len(dates) < 2 or not portfolio[dates[0]] or not benchmark[dates[0]]:
        return {"status": "UNAVAILABLE", "aligned_dates": dates, "active_return_pct": None}
    portfolio_return = portfolio[dates[-1]] / portfolio[dates[0]] - 1
    benchmark_return = benchmark[dates[-1]] / benchmark[dates[0]] - 1
    return {
        "status": "AVAILABLE",
        "aligned_dates": dates,
        "portfolio_return_pct": round(portfolio_return * 100, 4),
        "benchmark_return_pct": round(benchmark_return * 100, 4),
        "active_return_pct": round((portfolio_return - benchmark_return) * 100, 4),
    }


def _xirr_cashflows(
    transactions: list[dict[str, Any]], *, scope: str, instrument_id: str | None
) -> list[tuple[str, float]]:
    flows: list[tuple[str, float]] = []
    for row in transactions:
        event = str(row.get("event_type") or "")
        if instrument_id:
            if row.get("instrument_id") != instrument_id:
                continue
            value = float(row.get("net_cash_flow") or 0) * _fx(row)
            if event in {"BUY", "SUBSCRIPTION", "RIGHTS"}:
                value = -abs(value)
            elif event in {"SELL", "REDEMPTION", "DIVIDEND", "INTEREST"}:
                value = abs(value)
            else:
                continue
        else:
            external = _external_flow(row, scope=scope)
            if not external:
                continue
            value = -external
        flows.append((str(row.get("trade_date")), value))
    return flows


def _external_flow(row: dict[str, Any], *, scope: str) -> float:
    if not row.get("external_cash_flow"):
        return 0.0
    if scope == "family" and row.get("event_type") in {"TRANSFER_IN", "TRANSFER_OUT"}:
        return 0.0
    return float(row.get("net_cash_flow") or 0) * _fx(row)


def _snapshot_usable(row: dict[str, Any]) -> bool:
    return _snapshot_quality_usable(row) and row.get("comparable_to_previous") not in (False, 0)


def _snapshot_quality_usable(row: dict[str, Any]) -> bool:
    quality = str(row.get("snapshot_quality") or "UNKNOWN")
    return quality in GOOD_QUALITY


def _fx(row: dict[str, Any]) -> float:
    return float(row.get("fx_rate_to_reporting_currency") or 1)


def _fx_contribution(row: dict[str, Any]) -> float:
    if str(row.get("currency") or "INR").upper() == "INR":
        return 0.0
    amount = float(row.get("net_cash_flow") or 0)
    return amount * (_fx(row) - 1)
