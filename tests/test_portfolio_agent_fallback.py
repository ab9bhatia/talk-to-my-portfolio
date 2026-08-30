from modules.portfolio.services.portfolio_agent import (
    _deterministic_provider_fallback,
    _provider_error_message,
)


def test_429_message_distinguishes_provider_from_local_api():
    message = _provider_error_message("openai", 429)
    assert "local portfolio API is healthy" in message
    assert "billing/quota" in message


def test_provider_fallback_preserves_deterministic_actions():
    context = {
        "advisory": {
            "recommendations": [
                {
                    "symbol": "ADDME",
                    "action": "ADD",
                    "sell_type": "NONE",
                    "family_weight_pct": 1.0,
                    "why_now": "Base scenario clears the add band.",
                    "data_quality_flags": [],
                    "decision_presentation": {
                        "label": "Add gradually",
                        "readiness": "READY_TO_REVIEW",
                        "do_now": "Review a staged add.",
                    },
                },
                {
                    "symbol": "TRIMME",
                    "action": "REDUCE",
                    "sell_type": "TACTICAL_REDUCE",
                    "family_weight_pct": 3.0,
                    "why_now": "Expected return is below the hold hurdle.",
                    "data_quality_flags": [],
                    "decision_presentation": {
                        "label": "Trim gradually",
                        "readiness": "READY_TO_REVIEW",
                        "do_now": "Review a staged trim.",
                    },
                },
            ]
        }
    }
    result = _deterministic_provider_fallback(
        context=context,
        question="How can I achieve XIRR above 24%?",
        provider="openai",
        status_code=429,
    )
    assert result["degraded"] is True
    assert "not a guarantee" in result["answer"]
    assert result["buy"][0]["symbol"] == "ADDME"
    assert result["sell_or_trim"][0]["symbol"] == "TRIMME"
    assert {row["deterministic_action"] for row in result["symbols"]} == {"ADD", "REDUCE"}


def test_provider_fallback_never_places_blocked_decision_in_legacy_trade_arrays():
    context = {
        "advisory": {
            "recommendations": [
                {
                    "symbol": "WAITADD",
                    "action": "ADD",
                    "sell_type": "NONE",
                    "family_weight_pct": 1.0,
                    "data_quality_flags": [{"message": "Research required."}],
                    "decision_presentation": {
                        "label": "Research before adding",
                        "readiness": "RESEARCH_REQUIRED",
                        "do_now": "Validate current filings first.",
                    },
                }
            ]
        }
    }
    result = _deterministic_provider_fallback(
        context=context,
        question="What should I add?",
        provider="openai",
        status_code=429,
    )
    assert result["symbols"][0]["decision_label"] == "Research before adding"
    assert result["buy"] == []
    assert result["sell_or_trim"] == []
