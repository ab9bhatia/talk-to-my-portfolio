"""Portfolio module configuration — Zerodha / Groww / Sarwa accounts and credentials."""

import base64
import json
import os

from dotenv import load_dotenv

from modules.portfolio.accounts_loader import build_account_registry, load_accounts_raw

load_dotenv()


def _env(name: str) -> str:
    """Read .env value and strip optional surrounding quotes."""
    return os.getenv(name, "").strip().strip('"').strip("'")


def _groww_api_key_role(api_key: str) -> str | None:
    """Return Groww JWT role (e.g. auth-totp, auth-approval) or None."""
    try:
        parts = api_key.split(".")
        if len(parts) < 2:
            return None
        payload = parts[1]
        padding = "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload + padding))
        sub = data.get("sub")
        if isinstance(sub, str):
            sub = json.loads(sub)
        if isinstance(sub, dict):
            return sub.get("role")
    except (json.JSONDecodeError, ValueError, KeyError):
        return None
    return None


_raw = load_accounts_raw()
ACCOUNTS, GROWW_ACCOUNTS, SARWA_ACCOUNTS, CUSTOM_ACCOUNTS, _ACCOUNT_CODES, LEGACY_ZERODHA_ACCOUNT_ID = (
    build_account_registry(_raw)
)

HUB_BASE_URL = os.getenv("HUB_BASE_URL", "http://127.0.0.1:8000")
ZERODHA_CALLBACK_URL = os.getenv(
    "ZERODHA_CALLBACK_URL", "http://127.0.0.1:8000/auth/zerodha/callback"
)

# Short codes → internal account_id (AB → primary, etc.)
def _all_account_meta() -> dict[str, dict]:
    return {**ACCOUNTS, **GROWW_ACCOUNTS, **SARWA_ACCOUNTS, **CUSTOM_ACCOUNTS}


ACCOUNT_CODES: dict[str, str] = {aid: meta["code"] for aid, meta in _all_account_meta().items()}
CODE_TO_ACCOUNT_ID: dict[str, str] = {code: account_id for account_id, code in ACCOUNT_CODES.items()}


def reload_account_registry() -> None:
    """Reload accounts.json into module-level registries (after setup saves)."""
    global ACCOUNTS, GROWW_ACCOUNTS, SARWA_ACCOUNTS, CUSTOM_ACCOUNTS, LEGACY_ZERODHA_ACCOUNT_ID
    global ACCOUNT_CODES, CODE_TO_ACCOUNT_ID

    raw = load_accounts_raw()
    accounts, groww, sarwa, custom, _codes, legacy = build_account_registry(raw)
    ACCOUNTS = accounts
    GROWW_ACCOUNTS = groww
    SARWA_ACCOUNTS = sarwa
    CUSTOM_ACCOUNTS = custom
    LEGACY_ZERODHA_ACCOUNT_ID = legacy
    ACCOUNT_CODES = {aid: meta["code"] for aid, meta in _all_account_meta().items()}
    CODE_TO_ACCOUNT_ID = {code: account_id for account_id, code in ACCOUNT_CODES.items()}
    for _aid, _meta in ACCOUNTS.items():
        if not _meta.get("redirect_url"):
            _meta["redirect_url"] = ZERODHA_CALLBACK_URL

# Default redirect on Zerodha rows when not set in JSON
for _aid, _meta in ACCOUNTS.items():
    if not _meta.get("redirect_url"):
        _meta["redirect_url"] = ZERODHA_CALLBACK_URL


def resolve_account_ref(ref: str) -> str:
    """
    Map account code or id to internal account_id.

    Accepts AB, ab, primary, etc.
    """
    if not ref or not str(ref).strip():
        raise KeyError("Empty account reference")

    stripped = str(ref).strip()
    lower = stripped.lower()
    if lower in ACCOUNTS or lower in GROWW_ACCOUNTS or lower in SARWA_ACCOUNTS or lower in CUSTOM_ACCOUNTS:
        return lower

    upper = stripped.upper()
    if upper in CODE_TO_ACCOUNT_ID:
        return CODE_TO_ACCOUNT_ID[upper]
    if upper == "SW":
        for aid, meta in SARWA_ACCOUNTS.items():
            if meta.get("code") == "SW":
                return aid

    raise KeyError(f"Unknown account: {ref}")


def get_account_code(account_id: str) -> str:
    """Return short code (AB, RB, …) for an account_id."""
    account_id = resolve_account_ref(account_id)
    if account_id in GROWW_ACCOUNTS:
        return GROWW_ACCOUNTS[account_id]["code"]
    if account_id in SARWA_ACCOUNTS:
        return SARWA_ACCOUNTS[account_id]["code"]
    if account_id in CUSTOM_ACCOUNTS:
        return CUSTOM_ACCOUNTS[account_id]["code"]
    return ACCOUNTS[account_id]["code"]


def get_zerodha_credentials(account_id: str) -> dict[str, str]:
    """Load Kite Connect credentials for a specific account."""
    account_id = resolve_account_ref(account_id)
    account = get_account(account_id)
    suffix = account_id.upper()

    api_key = os.getenv(f"ZERODHA_API_KEY_{suffix}")
    api_secret = os.getenv(f"ZERODHA_API_SECRET_{suffix}")
    redirect_url = os.getenv(f"ZERODHA_REDIRECT_URL_{suffix}") or account.get("redirect_url")

    # Legacy single-app env vars (see legacy_zerodha_account_id in accounts JSON).
    if account_id == LEGACY_ZERODHA_ACCOUNT_ID:
        api_key = api_key or os.getenv("ZERODHA_API_KEY")
        api_secret = api_secret or os.getenv("ZERODHA_API_SECRET")
        redirect_url = redirect_url or os.getenv("ZERODHA_REDIRECT_URL")

    missing = [
        name
        for name, value in {
            f"ZERODHA_API_KEY_{suffix}": api_key,
            f"ZERODHA_API_SECRET_{suffix}": api_secret,
            "redirect_url": redirect_url,
        }.items()
        if not value
    ]
    if missing:
        code = get_account_code(account_id)
        raise RuntimeError(
            f"Missing credentials for {code} ({account_id}): {', '.join(missing)}. "
            f"Set ZERODHA_API_KEY_{suffix} and ZERODHA_API_SECRET_{suffix} in .env"
        )

    return {
        "api_key": api_key,
        "api_secret": api_secret,
        "redirect_url": redirect_url,
    }


def get_account(account_id: str) -> dict:
    """Return account metadata or raise if unknown."""
    account_id = resolve_account_ref(account_id)
    if account_id not in ACCOUNTS:
        raise KeyError(f"Unknown Zerodha account: {account_id}")
    return ACCOUNTS[account_id]


def get_enabled_accounts() -> dict[str, dict]:
    """Return only Zerodha accounts enabled for OAuth and portfolio fetch."""
    return {
        account_id: account
        for account_id, account in ACCOUNTS.items()
        if account.get("enabled")
    }


def get_groww_account(account_id: str) -> dict:
    """Return Groww account metadata or raise if unknown."""
    account_id = resolve_account_ref(account_id)
    if account_id not in GROWW_ACCOUNTS:
        raise KeyError(f"Unknown Groww account: {account_id}")
    return GROWW_ACCOUNTS[account_id]


def get_enabled_groww_accounts() -> dict[str, dict]:
    """Return Groww accounts enabled for portfolio fetch."""
    return {
        account_id: account
        for account_id, account in GROWW_ACCOUNTS.items()
        if account.get("enabled")
    }


def get_first_enabled_groww_account_id() -> str | None:
    enabled = get_enabled_groww_accounts()
    if not enabled:
        return None
    return next(iter(enabled))


def get_sarwa_account(account_id: str) -> dict:
    """Return Sarwa account metadata or raise if unknown."""
    account_id = resolve_account_ref(account_id)
    if account_id not in SARWA_ACCOUNTS:
        raise KeyError(f"Unknown Sarwa account: {account_id}")
    return SARWA_ACCOUNTS[account_id]


def get_enabled_sarwa_accounts() -> dict[str, dict]:
    """Return Sarwa accounts enabled for dashboard merge."""
    return {
        account_id: account
        for account_id, account in SARWA_ACCOUNTS.items()
        if account.get("enabled")
    }


def get_custom_account(account_id: str) -> dict:
    account_id = resolve_account_ref(account_id)
    if account_id not in CUSTOM_ACCOUNTS:
        raise KeyError(f"Unknown custom account: {account_id}")
    return CUSTOM_ACCOUNTS[account_id]


def get_enabled_custom_accounts() -> dict[str, dict]:
    return {
        account_id: account
        for account_id, account in CUSTOM_ACCOUNTS.items()
        if account.get("enabled")
    }


def get_account_broker(account_id: str) -> str | None:
    """Return broker id for a known account, or None."""
    try:
        account_id = resolve_account_ref(account_id)
    except KeyError:
        return None
    if account_id in GROWW_ACCOUNTS:
        return "groww"
    if account_id in SARWA_ACCOUNTS:
        return "sarwa"
    if account_id in CUSTOM_ACCOUNTS:
        return "custom"
    if account_id in ACCOUNTS:
        return "zerodha"
    return None


def is_known_account(ref: str) -> bool:
    return get_account_broker(ref) is not None


def get_groww_credentials(account_id: str) -> dict[str, str]:
    """
    Load Groww credentials from .env.

    Supports API key + secret or TOTP token + secret (see Groww Trade API docs).
    """
    account_id = resolve_account_ref(account_id)
    if account_id not in GROWW_ACCOUNTS:
        raise KeyError(f"Unknown Groww account: {account_id}")

    suffix = account_id.upper()
    code = get_account_code(account_id)
    api_key = _env(f"GROWW_API_KEY_{suffix}")
    api_secret = _env(f"GROWW_API_SECRET_{suffix}")
    totp_token = _env(f"GROWW_TOTP_TOKEN_{suffix}")
    totp_secret = _env(f"GROWW_TOTP_SECRET_{suffix}")

    if totp_token and totp_secret:
        return {
            "auth_method": "totp",
            "api_key": totp_token,
            "totp_secret": totp_secret,
        }

    if api_key and totp_secret and not api_secret:
        return {
            "auth_method": "totp",
            "api_key": api_key,
            "totp_secret": totp_secret,
        }

    if api_key and api_secret:
        return {
            "auth_method": "approval",
            "api_key": api_key,
            "api_secret": api_secret,
        }

    raise RuntimeError(
        f"Missing Groww credentials for {code} ({account_id}). "
        f"Set GROWW_API_KEY_{suffix} + GROWW_API_SECRET_{suffix}, "
        f"or GROWW_TOTP_TOKEN_{suffix} + GROWW_TOTP_SECRET_{suffix}. "
        "Generate at https://groww.in/trade-api/api-keys"
    )


def get_auth_start_url(ref: str) -> str:
    """OAuth start URL — uses account code in path (e.g. /auth/zerodha/SB)."""
    account_id = resolve_account_ref(ref)
    account = get_account(account_id)
    code = account["code"]
    port = account.get("auth_port") or 8000
    return f"http://127.0.0.1:{port}/auth/zerodha/{code}"


def get_hub_url(path: str = "") -> str:
    """Build absolute URL on the main app."""
    base = HUB_BASE_URL.rstrip("/")
    if not path:
        return base
    return f"{base}/{path.lstrip('/')}"
