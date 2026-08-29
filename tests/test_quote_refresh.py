from __future__ import annotations

from modules.portfolio.services import portfolio, weekly_recorder


def test_quote_refresh_deduplicates_and_reports_value_coverage(monkeypatch):
    calls: list[tuple[str, str | None]] = []

    def quote(symbol: str, exchange: str | None):
        calls.append((symbol, exchange))
        return 120.0 if symbol == "DUP" else None

    monkeypatch.setattr(weekly_recorder, "_yahoo_ltp_inr", quote)
    with portfolio._QUOTE_SESSION_LOCK:
        portfolio._QUOTE_SESSION_CACHE.clear()

    holdings = [
        {
            "symbol": "DUP",
            "exchange": "NSE",
            "quantity": 1,
            "avg_price": 80,
            "last_price": 100,
            "current_value": 100,
            "invested": 80,
        },
        {
            "symbol": "DUP",
            "exchange": "NSE",
            "quantity": 2,
            "avg_price": 90,
            "last_price": 100,
            "current_value": 200,
            "invested": 180,
        },
        {
            "symbol": "STALE",
            "exchange": "NSE",
            "quantity": 1,
            "avg_price": 50,
            "last_price": 50,
            "current_value": 50,
            "invested": 50,
        },
    ]
    updated, report = portfolio._refresh_holdings_ltps_from_yahoo(
        holdings, include_report=True
    )

    assert sorted(calls) == [("DUP", "NSE"), ("STALE", "NSE")]
    assert [row["last_price"] for row in updated[:2]] == [120.0, 120.0]
    assert report["requested_securities"] == 2
    assert report["resolved_securities"] == 1
    assert report["count_coverage_pct"] == 50.0
    assert report["value_weighted_coverage_pct"] == 85.71
    assert report["stale_symbols"] == ["STALE:NSE"]

    calls.clear()
    portfolio._refresh_holdings_ltps_from_yahoo(holdings)
    assert calls == [("STALE", "NSE")]


def test_crypto_quote_uses_usd_conversion_path(monkeypatch):
    calls: list[tuple[str, str | None]] = []

    def quote(symbol: str, exchange: str | None):
        calls.append((symbol, exchange))
        return 7_000_000.0

    monkeypatch.setattr(weekly_recorder, "_yahoo_ltp_inr", quote)
    with portfolio._QUOTE_SESSION_LOCK:
        portfolio._QUOTE_SESSION_CACHE.clear()

    updated = portfolio._refresh_holdings_ltps_from_yahoo(
        [
            {
                "symbol": "BTC",
                "exchange": "CRYPTO",
                "asset_class": "crypto",
                "currency": "USD",
                "quantity": 0.01,
                "last_price": 6_900_000,
                "current_value": 69_000,
                "invested": 60_000,
            }
        ]
    )

    assert calls == [("BTC", "US")]
    assert updated[0]["current_value"] == 70_000.0
