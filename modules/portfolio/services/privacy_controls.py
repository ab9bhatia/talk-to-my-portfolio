"""Explicit local privacy controls for outbound providers and support artifacts."""

from __future__ import annotations

import os


def _enabled(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def privacy_status() -> dict:
    return {
        "market_data_symbol_queries": {
            "enabled": _enabled("PORTFOLIO_ALLOW_MARKET_SYMBOL_SHARING", True),
            "provider": "Yahoo Finance / configured market provider",
            "private_account_data_included": False,
        },
        "external_llm": {
            "enabled": True,
            "account_tax_context_enabled": _enabled("PORTFOLIO_ALLOW_LLM_ACCOUNT_TAX_CONTEXT"),
            "preview_available": True,
        },
        "web_research": {"enabled": _enabled("PORTFOLIO_ALLOW_WEB_RESEARCH"), "default": "deny"},
        "notifications": {"enabled": _enabled("PORTFOLIO_ALLOW_EXTERNAL_NOTIFICATIONS"), "default": "deny"},
        "support_bundle_raw_holdings": {"enabled": False, "requires_explicit_request": True},
    }
