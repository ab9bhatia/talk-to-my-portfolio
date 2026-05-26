"""Zerodha Kite Connect OAuth flow and authenticated client factory."""

from __future__ import annotations

from urllib.parse import quote, urlencode

from kiteconnect import KiteConnect
from kiteconnect.exceptions import KiteException

from modules.portfolio.config import get_account, get_zerodha_credentials, resolve_account_ref
from modules.portfolio.db import tokens as token_store


class OAuthError(Exception):
    """Raised when Zerodha OAuth fails or account validation fails."""


def build_login_url(ref: str) -> str:
    """Build the Kite login URL for a given account (uses that account's API key)."""
    account_id = resolve_account_ref(ref)
    account = get_account(account_id)
    if not account.get("enabled"):
        raise OAuthError(f"Account {account['code']} is not enabled yet.")

    credentials = get_zerodha_credentials(account_id)
    redirect_params = quote(urlencode({"code": account["code"]}))

    return (
        "https://kite.zerodha.com/connect/login"
        f"?v=3&api_key={credentials['api_key']}"
        f"&redirect_params={redirect_params}"
    )


def complete_oauth(request_token: str, ref: str) -> dict:
    """Exchange request_token for access_token and persist it."""
    account_id = resolve_account_ref(ref)
    account = get_account(account_id)
    if not account.get("enabled"):
        raise OAuthError(f"Account {account['code']} is not enabled yet.")

    credentials = get_zerodha_credentials(account_id)
    kite = KiteConnect(api_key=credentials["api_key"])

    try:
        session = kite.generate_session(
            request_token,
            api_secret=credentials["api_secret"],
        )
    except KiteException as exc:
        redirect_url = credentials.get("redirect_url", "")
        raise OAuthError(
            f"Token exchange failed for {account['code']}: {exc}. "
            f"On developers.kite.trade, this app's redirect URL must match exactly: {redirect_url}"
        ) from exc

    user_id = session.get("user_id")
    expected_user_id = account["user_id"]
    if user_id != expected_user_id:
        raise OAuthError(
            f"Logged in as {user_id}, expected {expected_user_id} for account {account['code']}."
        )

    login_time = session.get("login_time")
    access_token = session["access_token"]

    token_store.save_token(
        account_id=account_id,
        user_id=user_id,
        access_token=access_token,
        api_key=credentials["api_key"],
        login_time=str(login_time),
    )

    return {
        "account_id": account_id,
        "account_code": account["code"],
        "user_id": user_id,
        "login_time": login_time,
    }


def get_kite_client(ref: str) -> KiteConnect:
    """Return an authenticated KiteConnect client for an account."""
    account_id = resolve_account_ref(ref)
    credentials = get_zerodha_credentials(account_id)
    token = token_store.get_token(account_id)
    if token is None:
        raise OAuthError(f"No token stored for account '{account_id}'. Please log in.")

    api_key = token.get("api_key") or credentials["api_key"]
    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(token["access_token"])
    return kite
