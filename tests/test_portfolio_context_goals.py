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
