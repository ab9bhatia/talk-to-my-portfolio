from __future__ import annotations

import threading
import time
from pathlib import Path

from modules.portfolio.services import chart_patterns
from modules.portfolio.services import portfolio_context as context_service
from modules.portfolio.services.groww_portfolio import _normalize_groww_holding
from modules.portfolio.services.quote_reconciliation import apply_family_quote_consensus


ROOT = Path(__file__).resolve().parents[1]


def _holding(code: str, broker: str, quantity: float, price: float, avg: float) -> dict:
    value = quantity * price
    invested = quantity * avg
    return {
        "symbol": "ADANIPORTS",
        "exchange": "NSE",
        "quantity": quantity,
        "avg_price": avg,
        "last_price": price,
        "invested": invested,
        "current_value": value,
        "pnl": value - invested,
        "pnl_pct": (value - invested) / invested * 100,
        "account_id": code.lower(),
        "account_code": code,
        "broker": broker,
    }


def test_groww_cost_basis_is_not_presented_as_market_price(monkeypatch):
    monkeypatch.setattr(
        "modules.portfolio.services.groww_portfolio.get_account_code",
        lambda _account_id: "HB",
    )
    row = _normalize_groww_holding(
        {
            "trading_symbol": "ADANIPORTS",
            "exchange": "NSE",
            "quantity": 410,
            "average_price": 984.74,
        },
        "groww-hb",
        ltp_map={},
    )
    assert row is not None
    assert row["broker_reported_price"] == 984.74
    assert row["broker_price_source"] == "cost_basis_fallback"
    assert row["market_price"] is None
    assert row["market_price_unavailable"] is True


def test_family_quote_consensus_corrects_the_adaniports_weighted_ltp_bug():
    rows = [
        _holding("AB", "zerodha", 225, 1707.5, 749.54),
        _holding("RB", "zerodha", 75, 1707.5, 1202.45),
        _holding("SB", "zerodha", 160, 1707.5, 1051.59),
        _holding("HB", "groww", 410, 984.74, 984.74),
    ]
    rows[-1].update(
        {
            "broker_reported_price": 984.74,
            "broker_price_source": "cost_basis_fallback",
            "market_price": None,
            "market_price_unavailable": True,
        }
    )
    family = {
        "cached_at": 1787994000.0,
        "summary": {"source": "fixture"},
        "portfolios": [
            {
                "account_id": row["account_id"],
                "account_code": row["account_code"],
                "broker": row["broker"],
                "summary": {},
                "holdings": [row],
            }
            for row in rows
        ],
    }

    reconciled = apply_family_quote_consensus(family)
    corrected = [
        row
        for block in reconciled["portfolios"]
        for row in block["holdings"]
    ]
    assert {row["last_price"] for row in corrected} == {1707.5}
    hb = next(row for row in corrected if row["account_code"] == "HB")
    assert hb["broker_reported_price"] == 984.74
    assert hb["market_price_source"] == "family_quote_consensus"
    assert hb["market_price_as_of"] == "2026-08-29T09:00:00Z"
    assert reconciled["summary"]["total_current_value"] == 870 * 1707.5
    assert reconciled["summary"]["source"] == "fixture"


def test_pattern_scan_returns_immediately_and_completes_in_background(monkeypatch):
    release = threading.Event()
    expected = [{"symbol": "ASYNCFIXTURE", "patterns": []}]

    def slow_scan(_holdings, *, max_workers=4):
        release.wait(timeout=2)
        return expected

    chart_patterns._ASYNC_SCAN_STATE.clear()
    monkeypatch.setattr(chart_patterns, "scan_holdings", slow_scan)
    holdings = [{"symbol": "ASYNCFIXTURE", "exchange": "NSE"}]
    started = time.monotonic()
    initial = chart_patterns.scan_holdings_async(holdings)
    assert initial["status"] == "scanning"
    assert time.monotonic() - started < 0.25

    release.set()
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        completed = chart_patterns.scan_holdings_async(holdings)
        if completed["status"] == "complete":
            break
        time.sleep(0.01)
    assert completed["status"] == "complete"
    assert completed["results"] == expected


def test_agent_context_uses_embedded_profiles_without_live_fanout(monkeypatch):
    family = {
        "cached_at": "2026-08-29T09:00:00Z",
        "summary": {"total_current_value": 100, "total_invested": 90},
        "portfolios": [
            {
                "holdings": [
                    {
                        "symbol": "FIXTURE",
                        "exchange": "NSE",
                        "sector": "Infrastructure",
                        "current_value": 100,
                        "invested": 90,
                        "pnl": 10,
                    }
                ]
            }
        ],
    }
    monkeypatch.setattr(context_service, "fetch_family_portfolio", lambda **_kwargs: family)
    monkeypatch.setattr(context_service, "_load_user_goals", lambda: {})
    monkeypatch.setattr(context_service, "get_macro_snapshot", lambda: {})
    monkeypatch.setattr(context_service, "build_advisory_payload", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        context_service,
        "_batch_yahoo_profiles",
        lambda _holdings: (_ for _ in ()).throw(AssertionError("live fan-out called")),
    )

    context = context_service.build_portfolio_context()
    assert context["holdings"][0]["yahoo_sector"] == "Infrastructure"


def test_browser_contracts_surface_agent_and_pattern_failures():
    agent = (ROOT / "shared/web/static/js/portfolio-agent.js").read_text()
    holdings = (ROOT / "shared/web/static/js/holdings.js").read_text()
    radar = (ROOT / "shared/web/static/js/portfolio-patterns.js").read_text()
    assert "onEvent(event, parsed);" in agent
    assert "Agent stream ended before a response was returned" in agent
    assert "Portfolio Agent timed out after 2 minutes" in agent
    assert 'fetch("/api/portfolio/patterns?blocking=false")' in holdings
    assert "Scanning setups…" in holdings
    assert 'fetch(`/api/portfolio/patterns${query}`)' in radar
