"""Milestone 6A regression tests for chart-pattern response semantics."""

from __future__ import annotations

import threading
import time

import modules.portfolio.services.chart_patterns as chart_patterns
from fastapi.testclient import TestClient

from main import app
from modules.portfolio.services.chart_patterns import (
    _Series,
    _enrich_pattern_semantics,
    _instrument_currency,
    analyze_series,
)
from shared.config import APP_ROOT_PATH


def _hit(*, bias: str = "bullish", status: str = "confirmed", target: float = 125) -> dict:
    return {
        "pattern": "fixture",
        "label": "Synthetic setup",
        "bias": bias,
        "status": status,
        "confidence": 82,
        "neckline": 100,
        "target_price": target,
        "duration_days": 40,
        "start_date": "2026-01-01",
        "end_date": "2026-02-01",
        "points": [],
        "note": "Synthetic deterministic setup.",
    }


def _enrich(*, last: float, bias: str = "bullish", target: float = 125, age: int = 0) -> dict:
    return _enrich_pattern_semantics(
        _hit(bias=bias, target=target),
        last_price=last,
        as_of="2026-08-28",
        currency="INR",
        signal_age_trading_days=age,
    )


def test_confirmed_bullish_target_is_active_above_current_price():
    result = _enrich(last=100)
    assert result["lifecycle_state"] == "CONFIRMED"
    assert result["target_status"] == "ACTIVE"
    assert result["remaining_upside_pct"] == 25
    assert result["remaining_downside_pct"] == 0


def test_completed_bullish_target_has_no_negative_active_upside():
    achieved = _enrich(last=126)
    overshot = _enrich(last=130)
    assert achieved["lifecycle_state"] == "TARGET_ACHIEVED"
    assert achieved["target_status"] == "ACHIEVED"
    assert overshot["lifecycle_state"] == "TARGET_OVERSHOT"
    assert overshot["target_status"] == "OVERSHOT"
    assert achieved["remaining_upside_pct"] == overshot["remaining_upside_pct"] == 0
    assert achieved["upside_to_target_pct"] == overshot["upside_to_target_pct"] == 0


def test_completed_bearish_target_uses_symmetric_logic():
    achieved = _enrich(last=79, bias="bearish", target=80)
    overshot = _enrich(last=75, bias="bearish", target=80)
    assert achieved["target_status"] == "ACHIEVED"
    assert achieved["lifecycle_state"] == "TARGET_ACHIEVED"
    assert overshot["target_status"] == "OVERSHOT"
    assert overshot["lifecycle_state"] == "TARGET_OVERSHOT"
    assert achieved["remaining_downside_pct"] == overshot["remaining_downside_pct"] == 0


def test_confirmed_pattern_expires_after_heuristic_time_stop():
    result = _enrich(last=100, age=100)
    assert result["estimated_horizon"]["max_trading_days"] == 70
    assert result["lifecycle_state"] == "EXPIRED"
    assert result["target_status"] == "EXPIRED"


def test_currency_is_usd_for_pltr_and_inr_for_indian_holding():
    assert _instrument_currency("NASDAQ") == "USD"
    assert _instrument_currency("US") == "USD"
    assert _instrument_currency("NSE") == "INR"
    assert _instrument_currency("BSE") == "INR"


def test_horizon_score_and_legacy_fields_are_backward_compatible():
    result = _enrich(last=100)
    assert result["target_date"] is None
    assert result["estimated_horizon"] == {
        "min_trading_days": 20,
        "median_trading_days": 40,
        "max_trading_days": 70,
        "method": "heuristic_until_calibrated",
    }
    assert result["heuristic_score"] == 82
    assert result["confidence"] == 82
    assert result["confidence_semantics"] == "heuristic_shape_score"
    assert result["calibrated_target_hit_probability"] is None
    assert result["status"] == "confirmed"
    assert "target_price" in result
    assert "upside_to_target_pct" in result


def test_completed_target_is_retained_even_below_active_move_threshold(monkeypatch):
    series = _Series(
        labels=[f"d{i}" for i in range(60)],
        closes=[130.0] * 60,
        highs=[131.0] * 60,
        lows=[129.0] * 60,
    )
    monkeypatch.setattr(chart_patterns, "_detect_inverse_head_shoulders", lambda _series: _hit())
    monkeypatch.setattr(chart_patterns, "_detect_cup_with_handle", lambda _series: None)
    monkeypatch.setattr(chart_patterns, "_detect_double_bottom", lambda _series: None)
    monkeypatch.setattr(chart_patterns, "_detect_ascending_triangle", lambda _series: None)
    monkeypatch.setattr(chart_patterns, "_detect_head_shoulders", lambda _series: None)

    result = analyze_series(series, currency="USD")

    assert len(result) == 1
    assert result[0]["target_status"] == "OVERSHOT"
    assert result[0]["currency"] == "USD"


def test_symbol_pattern_api_keeps_legacy_fields_and_adds_stage_6a(monkeypatch):
    series = _Series(
        labels=[f"d{i}" for i in range(60)],
        closes=[100.0] * 60,
        highs=[101.0] * 60,
        lows=[99.0] * 60,
    )
    monkeypatch.setattr(chart_patterns, "_load_series", lambda *_args, **_kwargs: series)
    monkeypatch.setattr(chart_patterns, "_detect_inverse_head_shoulders", lambda _series: _hit())
    monkeypatch.setattr(chart_patterns, "_detect_cup_with_handle", lambda _series: None)
    monkeypatch.setattr(chart_patterns, "_detect_double_bottom", lambda _series: None)
    monkeypatch.setattr(chart_patterns, "_detect_ascending_triangle", lambda _series: None)
    monkeypatch.setattr(chart_patterns, "_detect_head_shoulders", lambda _series: None)

    response = TestClient(app).get(
        f"{APP_ROOT_PATH}/api/portfolio/patterns/PLTR?exchange=US"
    )

    assert response.status_code == 200
    pattern = response.json()["primary"]
    assert pattern["currency"] == "USD"
    assert pattern["status"] == "confirmed"
    assert pattern["confidence"] == 82
    assert pattern["target_price"] == 125
    assert pattern["upside_to_target_pct"] == 25
    assert pattern["lifecycle_state"] == "CONFIRMED"
    assert pattern["heuristic_score"] == 82
    assert pattern["target_status"] == "ACTIVE"
    assert pattern["target_date"] is None
    assert pattern["estimated_horizon"]["method"] == "heuristic_until_calibrated"
    payload = response.json()
    assert payload["actionable_primary"]["pattern"] == "fixture"
    assert len(payload["actionable_patterns"]) == 1


def test_portfolio_pattern_scan_returns_background_status_immediately(monkeypatch):
    release = threading.Event()
    result_rows = [{"symbol": "ASYNCFIXTURE", "patterns": []}]

    def slow_scan(_holdings, *, max_workers=4):
        release.wait(timeout=2)
        return result_rows

    chart_patterns._ASYNC_SCAN_STATE.clear()
    monkeypatch.setattr(chart_patterns, "scan_holdings", slow_scan)
    holdings = [{"symbol": "ASYNCFIXTURE", "exchange": "NSE"}]

    started = time.monotonic()
    initial = chart_patterns.scan_holdings_async(holdings)
    elapsed = time.monotonic() - started

    assert initial["status"] == "scanning"
    assert elapsed < 0.25
    release.set()
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        completed = chart_patterns.scan_holdings_async(holdings)
        if completed["status"] == "complete":
            break
        time.sleep(0.01)
    assert completed["status"] == "complete"
    assert completed["results"] == result_rows
