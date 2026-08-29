from modules.portfolio.services import portfolio_context as ctx


def test_effective_limits_uses_saved_goals():
    goals = {
        "target_return_pct": 18.0,
        "max_position_pct": 10.0,
        "max_sector_pct": 25.0,
        "cash_buffer_pct": 8.0,
        "risk_profile": "conservative",
    }
    limits = ctx._effective_limits(goals)
    assert limits["target_return_pct"] == 18.0
    assert limits["max_pct_per_stock"] == 10.0
    assert limits["max_pct_per_sector"] == 25.0
    assert limits["risk_profile"] == "conservative"


def test_investor_profile_reflects_goals():
    goals = {"target_return_pct": 20, "risk_profile": "aggressive", "cash_buffer_pct": 3}
    limits = ctx._effective_limits({**goals, "max_position_pct": 15, "max_sector_pct": 35})
    profile = ctx._investor_profile_for_agent(goals, limits)
    assert profile["target_xirr_pct"] == 20
    assert profile["risk"] == "aggressive"
    assert profile["cash_buffer_pct"] == 3
    assert profile["goals_source"] == "setup"


def test_context_includes_deterministic_advisory_payload(monkeypatch):
    family = {
        "cached_at": "2026-08-28T08:00:00Z",
        "from_cache": True,
        "summary": {
            "total_current_value": 1000,
            "total_invested": 900,
            "total_pnl": 100,
            "total_pnl_pct": 11.11,
        },
        "portfolios": [
            {
                "account_id": "fixture",
                "account_code": "FX",
                "broker": "custom",
                "summary": {"total_current_value": 100},
                "holdings": [
                    {
                        "symbol": "FIXTURE",
                        "exchange": "NSE",
                        "quantity": 1,
                        "last_price": 100,
                        "current_value": 100,
                        "invested": 90,
                        "pnl": 10,
                        "account_id": "fixture",
                        "account_code": "FX",
                        "broker": "custom",
                    }
                ],
            }
        ],
        "accounts_loaded": 1,
        "errors": [],
    }
    monkeypatch.setattr(ctx, "_load_user_goals", lambda: {})
    monkeypatch.setattr(ctx, "fetch_family_portfolio", lambda **_kwargs: family)
    monkeypatch.setattr(
        ctx,
        "_batch_yahoo_profiles",
        lambda _holdings: (_ for _ in ()).throw(AssertionError("agent question triggered live quote fan-out")),
    )
    monkeypatch.setattr(ctx, "get_macro_snapshot", lambda: {"as_of": "2026-08-28"})

    context = ctx.build_portfolio_context()

    assert context["advisory"]["schema_version"] == "advisor-v2-v1"
    recommendation = context["advisory"]["recommendations"][0]
    assert recommendation["symbol"] == "FIXTURE"
    assert recommendation["action"] == "WATCH"
