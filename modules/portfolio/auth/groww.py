"""Groww Trade API — access token and client (TOTP or API key + secret)."""

from __future__ import annotations

import logging
from typing import Any

from growwapi import GrowwAPI
from growwapi.groww.exceptions import (
    GrowwAPIAuthenticationException,
    GrowwAPIAuthorisationException,
    GrowwAPIException,
)

from modules.portfolio.config import get_groww_credentials
from modules.portfolio.db import groww_tokens as groww_token_store

logger = logging.getLogger(__name__)


class GrowwError(Exception):
    """Groww API or configuration error."""


def _totp_code(secret: str) -> str:
    try:
        import pyotp
    except ImportError as exc:
        raise GrowwError("pyotp is required for Groww TOTP auth. Run: pip install pyotp") from exc
    return pyotp.TOTP(secret.replace(" ", "")).now()


def _exchange_access_token(account_id: str, creds: dict) -> str:
    """Exchange API key (+ secret or TOTP) for a session token."""
    api_key = creds["api_key"]
    try:
        if creds["auth_method"] == "totp":
            secret = creds.get("totp_secret") or creds.get("api_secret")
            if not secret:
                raise GrowwError(
                    f"Groww TOTP secret missing for '{account_id}'. "
                    f"Set GROWW_TOTP_SECRET_{account_id.upper()} in .env"
                )
            return GrowwAPI.get_access_token(
                api_key=api_key,
                totp=_totp_code(secret),
            )
        return GrowwAPI.get_access_token(
            api_key=api_key,
            secret=creds["api_secret"],
        )
    except GrowwAPIException as exc:
        groww_token_store.delete_token(account_id)
        raise GrowwError(
            f"Groww auth failed for '{account_id}': {exc}. "
            "Approve the key on groww.in/trade-api/api-keys (required daily ~6–8 AM IST)."
        ) from exc
    except Exception as exc:
        groww_token_store.delete_token(account_id)
        raise GrowwError(f"Groww auth failed for '{account_id}': {exc}") from exc


def get_access_token(account_id: str, *, force_refresh: bool = False) -> str:
    """
    Exchange credentials for a session access token.

    Approval keys: always exchange fresh (daily approval invalidates prior sessions).
    TOTP keys: cached in SQLite until ~8 AM IST.
    """
    creds = get_groww_credentials(account_id)
    use_cache = creds["auth_method"] == "totp" and not force_refresh

    if use_cache:
        cached = groww_token_store.get_cached_token(account_id)
        if cached:
            return cached

    token = _exchange_access_token(account_id, creds)
    if not token or not isinstance(token, str):
        raise GrowwError(f"Groww returned no access token for '{account_id}'")

    groww_token_store.save_token(
        account_id,
        token,
        auth_method=creds["auth_method"],
    )
    return token


def get_groww_client(account_id: str, *, force_refresh: bool = False) -> GrowwAPI:
    """Authenticated Groww client for one account."""
    creds = get_groww_credentials(account_id)
    if creds["auth_method"] == "approval":
        force_refresh = True
    return GrowwAPI(get_access_token(account_id, force_refresh=force_refresh))


def _is_auth_failure(exc: Exception) -> bool:
    if isinstance(exc, (GrowwAPIAuthorisationException, GrowwAPIAuthenticationException)):
        return True
    code = getattr(exc, "code", None)
    if code in ("401", "403"):
        return True
    text = str(exc).lower()
    return any(
        token in text
        for token in (
            "authorisation",
            "authorization",
            "permission",
            "authentication",
            "expired",
            "invalid",
        )
    )


def verify_groww_session(account_id: str) -> None:
    """Raise GrowwError if credentials cannot fetch holdings (source of truth)."""
    groww_token_store.delete_token(account_id)
    client = get_groww_client(account_id, force_refresh=True)
    try:
        client.get_holdings_for_user(timeout=20)
    except Exception as exc:
        groww_token_store.delete_token(account_id)
        raise GrowwError(
            f"Groww session check failed: {exc}. "
            "Approve today's API key at groww.in/trade-api/api-keys, then refresh the portfolio."
        ) from exc


def get_groww_connection_status(account_id: str) -> dict[str, Any]:
    """Dashboard status — verified by a live holdings request."""
    try:
        creds = get_groww_credentials(account_id)
    except RuntimeError as exc:
        return {
            "connected": False,
            "needs_login": True,
            "broker": "groww",
            "message": str(exc),
        }

    try:
        verify_groww_session(account_id)
        method = creds["auth_method"]
        hint = (
            "TOTP active"
            if method == "totp"
            else "API key active (approve daily on Groww before refresh)"
        )
        return {
            "connected": True,
            "needs_login": False,
            "broker": "groww",
            "message": hint,
        }
    except GrowwError as exc:
        return {
            "connected": False,
            "needs_login": True,
            "broker": "groww",
            "message": str(exc),
        }
