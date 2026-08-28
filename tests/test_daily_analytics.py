"""Tests for growth dashboard forward-fill behaviour."""

from modules.portfolio.services.daily_analytics import (
    _account_matrix_for_days,
    _carry_forward_amount,
    _forward_fill_growth_series,
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
