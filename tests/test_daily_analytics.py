"""Tests for growth dashboard forward-fill behaviour."""

from modules.portfolio.services.daily_analytics import (
    _account_matrix_for_days,
    _carry_forward_amount,
    _forward_fill_growth_series,
    build_growth_dashboard,
)


def test_carry_forward_uses_previous_when_snapshot_missing():
    value, carried = _carry_forward_amount(None, previous=1_000_000.0, has_snapshot=False)
    assert value == 1_000_000.0
    assert carried is True


def test_carry_forward_replaces_placeholder_zero():
    value, carried = _carry_forward_amount(0.0, previous=2_500_000.0, has_snapshot=True)
    assert value == 2_500_000.0
    assert carried is True


def test_forward_fill_growth_series_carries_family_totals():
    series = [
        {"day_date": "2026-01-01", "total_current": 100.0, "total_invested": 90.0, "total_pnl": 10.0, "total_pnl_pct": 11.1},
        {"day_date": "2026-01-02", "total_current": 0.0, "total_invested": 0.0, "total_pnl": 0.0, "total_pnl_pct": 0.0},
    ]
    out = _forward_fill_growth_series(series)
    assert out[1]["total_current"] == 100.0
    assert out[1]["total_invested"] == 90.0
    assert out[1]["carried_forward"] is True


def test_account_matrix_carries_missing_account_day():
    family_series = [
        {"day_date": "2026-01-01", "total_current": 300.0, "total_invested": 250.0, "total_pnl_pct": 20.0},
        {"day_date": "2026-01-02", "total_current": 300.0, "total_invested": 250.0, "total_pnl_pct": 20.0},
    ]

    # Simulate DB rows only for day 1; day 2 account snapshot missing.
    from modules.portfolio.db import daily_history

    with daily_history.connect() as conn:
        conn.execute("DELETE FROM daily_positions")
        conn.execute("DELETE FROM daily_snapshots")

    daily_history.save_snapshot(
        scope="account",
        account_id="test_rb",
        positions=[
            {
                "symbol": "AAA",
                "exchange": "NSE",
                "quantity": 1,
                "avg_price": 100,
                "last_price": 100,
                "invested": 100,
                "current_value": 100,
                "pnl": 0,
                "pnl_pct": 0,
            }
        ],
        source="test",
        day_date="2026-01-01",
    )

    # Patch get_account lookups used inside _account_matrix_for_days.
    import modules.portfolio.services.daily_analytics as mod

    original_get_account = mod.get_account
    original_get_account_code = mod.get_account_code

    def _fake_get_account(aid: str):
        return {"label": "Test", "id": aid}

    def _fake_get_account_code(aid: str):
        return "RB"

    mod.get_account = _fake_get_account
    mod.get_account_code = _fake_get_account_code
    try:
        account_series, timeline = _account_matrix_for_days(family_series)
        assert account_series
        series = account_series[0]["series"]
        assert series[0]["total_current"] == 100.0
        assert series[1]["total_current"] == 100.0
        assert series[1]["carried_forward"] is True
        rb = timeline[1]["accounts"]["RB"]
        assert rb["value"] == 100.0
        assert rb["carried_forward"] is True
    finally:
        mod.get_account = original_get_account
        mod.get_account_code = original_get_account_code
        with daily_history.connect() as conn:
            conn.execute("DELETE FROM daily_positions")
            conn.execute("DELETE FROM daily_snapshots")


def test_growth_suppresses_return_claims_when_account_coverage_changes(monkeypatch):
    from modules.portfolio.db import daily_history
    import modules.portfolio.services.daily_analytics as mod

    monkeypatch.setattr(mod, "_benchmark_series_for_days", lambda _series: {})
    with daily_history.connect() as conn:
        conn.execute("DELETE FROM daily_positions")
        conn.execute("DELETE FROM daily_snapshots")

    position = {
        "symbol": "QUALITY",
        "exchange": "NSE",
        "quantity": 1,
        "avg_price": 100,
        "last_price": 100,
        "invested": 100,
        "current_value": 100,
        "pnl": 0,
        "pnl_pct": 0,
    }
    daily_history.save_snapshot(
        scope="family",
        account_id=None,
        positions=[position],
        source="test",
        day_date="2035-01-04",
        metadata={
            "snapshot_quality": "COMPLETE_LIVE",
            "accounts_expected": 5,
            "accounts_included": 5,
            "coverage_pct": 100,
            "comparable_to_previous": False,
            "comparability_reasons": ["NO_PREVIOUS_SNAPSHOT"],
        },
    )
    daily_history.save_snapshot(
        scope="family",
        account_id=None,
        positions=[{**position, "last_price": 120, "current_value": 120, "pnl": 20}],
        source="test",
        day_date="2035-01-05",
        metadata={
            "snapshot_quality": "PARTIAL",
            "accounts_expected": 5,
            "accounts_included": 4,
            "coverage_pct": 80,
            "comparable_to_previous": False,
            "comparability_reasons": ["INCLUDED_ACCOUNT_COVERAGE_CHANGED"],
        },
    )

    dashboard = build_growth_dashboard(days=30)
    assert dashboard["day_change"]["change"] is None
    assert dashboard["day_change"]["comparable"] is False
    assert dashboard["breakdown"]["by_account"] == []
    assert dashboard["performance_quality"]["claims_allowed"] is False
    assert dashboard["performance_quality"]["non_comparable_points"][0]["day_date"] == "2035-01-05"


def test_benchmark_close_series_handles_yfinance_multiindex(monkeypatch):
    import pandas as pd
    import modules.portfolio.services.daily_analytics as mod

    frame = pd.DataFrame(
        [[24_500.25]],
        index=pd.to_datetime(["2026-08-28"]),
        columns=pd.MultiIndex.from_tuples([("Close", "^NSEI")]),
    )
    monkeypatch.setattr(mod.yf, "download", lambda *args, **kwargs: frame)
    mod._benchmark_close_series.cache_clear()
    try:
        assert mod._benchmark_close_series("^NSEI", "2026-08-28", "2026-08-29") == [
            ("2026-08-28", 24_500.25)
        ]
    finally:
        mod._benchmark_close_series.cache_clear()
