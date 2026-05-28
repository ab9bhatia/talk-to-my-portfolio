"""Account setup wizard — broker catalog, save accounts.json + .env."""

from __future__ import annotations

import re
from typing import Any

from modules.portfolio.accounts_loader import (
    collect_used_codes,
    load_accounts_raw,
    list_all_account_rows,
    save_accounts_raw,
    suggest_account_code,
)
from modules.portfolio.config import HUB_BASE_URL, get_auth_start_url, reload_account_registry
from modules.portfolio.db import custom_holdings as custom_db
from modules.portfolio.db import tokens as token_store
from modules.portfolio.services.env_store import env_var_present, read_env_value, upsert_env_vars
from modules.portfolio.db import import_audit

_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,30}$")

BROKERS: dict[str, dict[str, Any]] = {
    "zerodha": {
        "id": "zerodha",
        "label": "Zerodha",
        "description": "Kite Connect — holdings, OAuth login, optional orders.",
        "docs_url": "/docs/broker-api-keys.md#zerodha-kite-connect",
        "external_url": "https://developers.kite.trade/",
        "available": True,
        "steps": [
            "Log in at developers.kite.trade with the same Zerodha user.",
            "Create an app; set redirect URL to the callback below.",
            "Copy API key and secret into the form.",
            "After saving, open Portfolio and click Connect for this account.",
        ],
        "fields": [
            {"name": "label", "label": "Display name", "type": "text", "required": True},
            {"name": "id", "label": "Account id (lowercase)", "type": "slug", "required": True, "hint": "e.g. ankit → ZERODHA_API_KEY_ANKIT"},
            {"name": "code", "label": "Short code (2 letters)", "type": "code", "required": True},
            {"name": "user_id", "label": "Kite client ID", "type": "text", "required": True},
            {"name": "api_key", "label": "API key", "type": "secret", "required": True, "env": "ZERODHA_API_KEY"},
            {"name": "api_secret", "label": "API secret", "type": "secret", "required": True, "env": "ZERODHA_API_SECRET"},
            {
                "name": "redirect_url",
                "label": "Redirect / callback URL",
                "type": "url",
                "required": True,
                "env": "ZERODHA_REDIRECT_URL",
                "default_from": "callback_url",
            },
        ],
    },
    "groww": {
        "id": "groww",
        "label": "Groww",
        "description": "Groww Trade API — TOTP (recommended) or API key + secret.",
        "docs_url": "/docs/broker-api-keys.md#groww-trade-api",
        "external_url": "https://groww.in/trade-api",
        "available": True,
        "auth_methods": [
            {
                "id": "totp",
                "label": "TOTP (recommended)",
                "fields": [
                    {"name": "totp_token", "label": "TOTP token", "type": "secret", "env": "GROWW_TOTP_TOKEN"},
                    {"name": "totp_secret", "label": "TOTP secret", "type": "secret", "env": "GROWW_TOTP_SECRET"},
                ],
            },
            {
                "id": "api_key",
                "label": "API key + secret",
                "fields": [
                    {"name": "api_key", "label": "API key", "type": "secret", "env": "GROWW_API_KEY"},
                    {"name": "api_secret", "label": "API secret", "type": "secret", "env": "GROWW_API_SECRET"},
                ],
            },
        ],
        "fields": [
            {"name": "label", "label": "Display name", "type": "text", "required": True},
            {"name": "id", "label": "Account id", "type": "slug", "required": True},
            {"name": "code", "label": "Short code", "type": "code", "required": True},
            {"name": "auth_method", "label": "Authentication", "type": "auth_method", "required": True},
        ],
    },
    "dhan": {
        "id": "dhan",
        "label": "Dhan",
        "description": "Dhan API integration is planned — not available in this build yet.",
        "docs_url": "https://dhanhq.co/docs/v2/",
        "external_url": "https://dhanhq.co/",
        "available": False,
        "steps": [
            "Dhan support is coming soon.",
            "For now, use Custom import (CSV/Excel) or Zerodha/Groww.",
        ],
        "fields": [],
    },
    "sarwa": {
        "id": "sarwa",
        "label": "Sarwa",
        "description": "Sarwa Trade (USD) — upload a weekly holdings screenshot; parsed with vision when OpenAI is configured.",
        "docs_url": "/docs/broker-api-keys.md",
        "available": True,
        "accept_upload": ".png,.jpg,.jpeg,.webp",
        "steps": [
            "Export or screenshot your Sarwa Trade positions (USD).",
            "Save the account below, then upload PNG or JPEG.",
            "Re-upload anytime to refresh positions on the dashboard.",
        ],
        "fields": [
            {"name": "label", "label": "Display name", "type": "text", "required": True},
            {"name": "id", "label": "Account id", "type": "slug", "required": True},
            {"name": "code", "label": "Short code (2 letters)", "type": "code", "required": True},
        ],
    },
    "custom": {
        "id": "custom",
        "label": "Custom",
        "description": "Manual portfolio — upload CSV or Excel; appears as its own account on the dashboard.",
        "docs_url": "/docs/broker-api-keys.md",
        "available": True,
        "steps": [
            "Choose a name and short code, then save.",
            "Upload CSV, Excel, or a broker screenshot to populate holdings.",
            "Re-upload anytime from this page to refresh positions.",
        ],
        "accept_upload": ".csv,.xlsx,.xls,.png,.jpg,.jpeg,.webp",
        "fields": [
            {"name": "label", "label": "Portfolio name", "type": "text", "required": True},
            {"name": "id", "label": "Account id", "type": "slug", "required": True},
            {"name": "code", "label": "Short code", "type": "code", "required": True},
        ],
    },
}


def default_callback_url() -> str:
    base = HUB_BASE_URL.rstrip("/")
    return f"{base}/auth/zerodha/callback"


def broker_catalog() -> list[dict[str, Any]]:
    catalog = []
    for broker in BROKERS.values():
        item = {**broker, "default_callback_url": default_callback_url()}
        catalog.append(item)
    return catalog


def _normalize_id(value: str) -> str:
    slug = re.sub(r"[^a-z0-9_]", "", value.strip().lower())
    if not _ID_RE.match(slug):
        raise ValueError("Account id must be lowercase letters, numbers, underscore (2–31 chars, start with letter)")
    return slug


def account_setup_status() -> list[dict[str, Any]]:
    """List configured accounts with credential / connection hints."""
    raw = load_accounts_raw()
    rows = list_all_account_rows(raw)
    out: list[dict[str, Any]] = []
    for row in rows:
        broker = row["broker"]
        aid = row["id"]
        suffix = aid.upper()
        entry: dict[str, Any] = {
            "broker": broker,
            "id": aid,
            "code": row.get("code"),
            "label": row.get("label"),
            "enabled": row.get("enabled", True),
            "user_id": row.get("user_id"),
        }
        if broker == "zerodha":
            entry["credentials_ok"] = env_var_present(f"ZERODHA_API_KEY_{suffix}") and env_var_present(
                f"ZERODHA_API_SECRET_{suffix}"
            )
            st = token_store.get_token_status(aid)
            entry["connected"] = st.get("connected", False)
            entry["connect_url"] = get_auth_start_url(aid) if row.get("enabled") else None
        elif broker == "groww":
            totp = env_var_present(f"GROWW_TOTP_TOKEN_{suffix}") and env_var_present(f"GROWW_TOTP_SECRET_{suffix}")
            keys = env_var_present(f"GROWW_API_KEY_{suffix}") and env_var_present(f"GROWW_API_SECRET_{suffix}")
            entry["credentials_ok"] = totp or keys
            entry["connected"] = entry["credentials_ok"]
        elif broker == "custom":
            entry["credentials_ok"] = True
            entry["connected"] = custom_db.has_holdings(aid)
            entry["holdings_count"] = len(custom_db.list_holdings(aid)) if entry["connected"] else 0
        elif broker == "sarwa":
            from modules.portfolio.db import weekly_history

            snap = weekly_history.latest_snapshot(scope="account", account_id=aid) if row.get("enabled") else None
            entry["credentials_ok"] = True
            entry["connected"] = snap is not None
            entry["holdings_count"] = len(snap.get("positions") or []) if snap else 0
        out.append(entry)
    return out


def add_zerodha_account(payload: dict[str, Any]) -> dict[str, Any]:
    raw = load_accounts_raw()
    aid = _normalize_id(str(payload["id"]))
    if any(r["id"] == aid for r in raw.get("zerodha") or []):
        raise ValueError(f"Zerodha account '{aid}' already exists")

    code = str(payload.get("code") or "").upper().strip() or suggest_account_code(str(payload["label"]), raw)
    if code in collect_used_codes(raw):
        raise ValueError(f"Code '{code}' is already used")

    redirect = str(payload.get("redirect_url") or default_callback_url()).strip()
    row = {
        "id": aid,
        "code": code,
        "label": str(payload["label"]).strip(),
        "user_id": str(payload["user_id"]).strip(),
        "enabled": True,
        "redirect_url": redirect,
        "auth_port": 8000,
    }
    raw.setdefault("zerodha", []).append(row)
    if not raw.get("legacy_zerodha_account_id"):
        raw["legacy_zerodha_account_id"] = aid

    suffix = aid.upper()
    upsert_env_vars({
        f"ZERODHA_API_KEY_{suffix}": str(payload["api_key"]).strip(),
        f"ZERODHA_API_SECRET_{suffix}": str(payload["api_secret"]).strip(),
        f"ZERODHA_REDIRECT_URL_{suffix}": redirect,
        "HUB_BASE_URL": HUB_BASE_URL or "http://127.0.0.1:8000",
    })
    save_accounts_raw(raw)
    reload_account_registry()
    return {"account": row, "connect_url": get_auth_start_url(aid)}


def add_groww_account(payload: dict[str, Any]) -> dict[str, Any]:
    raw = load_accounts_raw()
    aid = _normalize_id(str(payload["id"]))
    if any(r["id"] == aid for r in raw.get("groww") or []):
        raise ValueError(f"Groww account '{aid}' already exists")

    code = str(payload.get("code") or "").upper().strip() or suggest_account_code(str(payload["label"]), raw)
    if code in collect_used_codes(raw):
        raise ValueError(f"Code '{code}' is already used")

    row = {
        "id": aid,
        "code": code,
        "label": str(payload["label"]).strip(),
        "relation": str(payload.get("relation") or ""),
        "user_id": "groww",
        "enabled": True,
    }
    raw.setdefault("groww", []).append(row)

    suffix = aid.upper()
    env_updates: dict[str, str] = {}
    method = str(payload.get("auth_method") or "totp").lower()
    if method == "api_key":
        env_updates[f"GROWW_API_KEY_{suffix}"] = str(payload["api_key"]).strip()
        env_updates[f"GROWW_API_SECRET_{suffix}"] = str(payload["api_secret"]).strip()
    else:
        env_updates[f"GROWW_TOTP_TOKEN_{suffix}"] = str(payload["totp_token"]).strip()
        env_updates[f"GROWW_TOTP_SECRET_{suffix}"] = str(payload["totp_secret"]).strip()

    upsert_env_vars(env_updates)
    save_accounts_raw(raw)
    reload_account_registry()
    return {"account": row}


def add_custom_account(payload: dict[str, Any]) -> dict[str, Any]:
    raw = load_accounts_raw()
    aid = _normalize_id(str(payload["id"]))
    if any(r["id"] == aid for r in raw.get("custom") or []):
        raise ValueError(f"Custom account '{aid}' already exists")

    code = str(payload.get("code") or "").upper().strip() or suggest_account_code(str(payload["label"]), raw)
    if code in collect_used_codes(raw):
        raise ValueError(f"Code '{code}' is already used")

    row = {
        "id": aid,
        "code": code,
        "label": str(payload["label"]).strip(),
        "enabled": True,
        "import_kind": "file",
    }
    raw.setdefault("custom", []).append(row)
    save_accounts_raw(raw)
    reload_account_registry()
    custom_db.init_db()
    return {"account": row, "upload_url": f"/api/portfolio/setup/accounts/{aid}/import"}


def add_account(broker: str, payload: dict[str, Any]) -> dict[str, Any]:
    broker = broker.strip().lower()
    if broker == "zerodha":
        return add_zerodha_account(payload)
    if broker == "groww":
        return add_groww_account(payload)
    if broker == "custom":
        return add_custom_account(payload)
    if broker == "dhan":
        raise ValueError("Dhan is not supported yet — use Custom import or watch for updates.")
    raise ValueError(f"Unknown broker: {broker}")


def _broker_list_key(broker: str) -> str:
    b = broker.strip().lower()
    if b not in ("zerodha", "groww", "sarwa", "custom"):
        raise ValueError(f"Unknown broker: {broker}")
    return b


def _find_row_index(raw: dict[str, Any], broker: str, account_id: str) -> tuple[str, int, dict[str, Any]]:
    key = _broker_list_key(broker)
    aid = account_id.strip().lower()
    rows = raw.get(key) or []
    for idx, row in enumerate(rows):
        if row.get("id") == aid:
            return key, idx, row
    raise ValueError(f"Account '{account_id}' not found under {broker}")


def _codes_available(raw: dict[str, Any], *, exclude_id: str | None = None) -> set[str]:
    used = collect_used_codes(raw)
    if exclude_id:
        for row in list_all_account_rows(raw):
            if row.get("id") == exclude_id and row.get("code"):
                used.discard(str(row["code"]).upper())
    return used


def get_account_for_edit(broker: str, account_id: str) -> dict[str, Any]:
    """Form defaults for edit modal (never returns secret values)."""
    raw = load_accounts_raw()
    _, _, row = _find_row_index(raw, broker, account_id)
    aid = row["id"]
    suffix = aid.upper()
    out: dict[str, Any] = {
        "broker": _broker_list_key(broker),
        "id": aid,
        "code": row.get("code"),
        "label": row.get("label"),
        "enabled": row.get("enabled", True),
        "user_id": row.get("user_id"),
        "redirect_url": row.get("redirect_url") or read_env_value(f"ZERODHA_REDIRECT_URL_{suffix}") or default_callback_url(),
    }
    if out["broker"] == "zerodha":
        out["secrets"] = {
            "api_key_set": env_var_present(f"ZERODHA_API_KEY_{suffix}"),
            "api_secret_set": env_var_present(f"ZERODHA_API_SECRET_{suffix}"),
        }
        out["connect_url"] = get_auth_start_url(aid)
        st = token_store.get_token_status(aid)
        out["connected"] = st.get("connected", False)
    elif out["broker"] == "groww":
        totp = env_var_present(f"GROWW_TOTP_TOKEN_{suffix}")
        keys = env_var_present(f"GROWW_API_KEY_{suffix}")
        out["auth_method"] = "totp" if totp else ("api_key" if keys else "totp")
        out["secrets"] = {
            "totp_token_set": totp,
            "totp_secret_set": env_var_present(f"GROWW_TOTP_SECRET_{suffix}"),
            "api_key_set": keys,
            "api_secret_set": env_var_present(f"GROWW_API_SECRET_{suffix}"),
        }
    elif out["broker"] == "custom":
        out["holdings_count"] = len(custom_db.list_holdings(aid))
        out["has_holdings"] = custom_db.has_holdings(aid)
    elif out["broker"] == "sarwa":
        from modules.portfolio.db import weekly_history

        snap = weekly_history.latest_snapshot(scope="account", account_id=aid)
        out["has_holdings"] = snap is not None
        out["holdings_count"] = len(snap.get("positions") or []) if snap else 0
    return out


def _optional_secret(payload: dict[str, Any], key: str) -> str | None:
    val = payload.get(key)
    if val is None:
        return None
    s = str(val).strip()
    return s if s else None


def update_zerodha_account(account_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    raw = load_accounts_raw()
    key, idx, row = _find_row_index(raw, "zerodha", account_id)
    aid = row["id"]
    suffix = aid.upper()

    new_code = str(payload.get("code") or row["code"]).upper().strip()
    used = _codes_available(raw, exclude_id=aid)
    if new_code in used:
        raise ValueError(f"Code '{new_code}' is already used")

    redirect = str(payload.get("redirect_url") or row.get("redirect_url") or default_callback_url()).strip()
    row.update({
        "label": str(payload.get("label") or row["label"]).strip(),
        "code": new_code,
        "user_id": str(payload.get("user_id") or row.get("user_id") or "").strip(),
        "enabled": bool(payload.get("enabled", row.get("enabled", True))),
        "redirect_url": redirect,
    })
    raw[key][idx] = row

    env_updates: dict[str, str] = {f"ZERODHA_REDIRECT_URL_{suffix}": redirect}
    api_key = _optional_secret(payload, "api_key")
    api_secret = _optional_secret(payload, "api_secret")
    if api_key:
        env_updates[f"ZERODHA_API_KEY_{suffix}"] = api_key
    if api_secret:
        env_updates[f"ZERODHA_API_SECRET_{suffix}"] = api_secret

    upsert_env_vars(env_updates)
    save_accounts_raw(raw)
    reload_account_registry()
    return {"account": row, "connect_url": get_auth_start_url(aid)}


def update_groww_account(account_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    raw = load_accounts_raw()
    key, idx, row = _find_row_index(raw, "groww", account_id)
    aid = row["id"]
    suffix = aid.upper()

    new_code = str(payload.get("code") or row["code"]).upper().strip()
    used = _codes_available(raw, exclude_id=aid)
    if new_code in used:
        raise ValueError(f"Code '{new_code}' is already used")

    row.update({
        "label": str(payload.get("label") or row["label"]).strip(),
        "code": new_code,
        "enabled": bool(payload.get("enabled", row.get("enabled", True))),
        "relation": str(payload.get("relation") if payload.get("relation") is not None else row.get("relation") or ""),
    })
    raw[key][idx] = row

    method = str(payload.get("auth_method") or "totp").lower()
    env_updates: dict[str, str] = {}
    if method == "api_key":
        k = _optional_secret(payload, "api_key")
        s = _optional_secret(payload, "api_secret")
        if k:
            env_updates[f"GROWW_API_KEY_{suffix}"] = k
        if s:
            env_updates[f"GROWW_API_SECRET_{suffix}"] = s
    else:
        t = _optional_secret(payload, "totp_token")
        sec = _optional_secret(payload, "totp_secret")
        if t:
            env_updates[f"GROWW_TOTP_TOKEN_{suffix}"] = t
        if sec:
            env_updates[f"GROWW_TOTP_SECRET_{suffix}"] = sec

    if env_updates:
        upsert_env_vars(env_updates)
    save_accounts_raw(raw)
    reload_account_registry()
    return {"account": row}


def update_custom_account(account_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    raw = load_accounts_raw()
    key, idx, row = _find_row_index(raw, "custom", account_id)
    aid = row["id"]

    new_code = str(payload.get("code") or row["code"]).upper().strip()
    used = _codes_available(raw, exclude_id=aid)
    if new_code in used:
        raise ValueError(f"Code '{new_code}' is already used")

    row.update({
        "label": str(payload.get("label") or row["label"]).strip(),
        "code": new_code,
        "enabled": bool(payload.get("enabled", row.get("enabled", True))),
    })
    raw[key][idx] = row
    save_accounts_raw(raw)
    reload_account_registry()
    return {"account": row}


def update_sarwa_account(account_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    raw = load_accounts_raw()
    key, idx, row = _find_row_index(raw, "sarwa", account_id)
    aid = row["id"]

    new_code = str(payload.get("code") or row["code"]).upper().strip()
    used = _codes_available(raw, exclude_id=aid)
    if new_code in used:
        raise ValueError(f"Code '{new_code}' is already used")

    row.update({
        "label": str(payload.get("label") or row["label"]).strip(),
        "code": new_code,
        "enabled": bool(payload.get("enabled", row.get("enabled", True))),
    })
    raw[key][idx] = row
    save_accounts_raw(raw)
    reload_account_registry()
    return {"account": row}


def update_account(broker: str, account_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    broker = broker.strip().lower()
    if broker == "zerodha":
        return update_zerodha_account(account_id, payload)
    if broker == "groww":
        return update_groww_account(account_id, payload)
    if broker == "custom":
        return update_custom_account(account_id, payload)
    if broker == "sarwa":
        return update_sarwa_account(account_id, payload)
    raise ValueError(f"Cannot edit broker: {broker}")


def import_account_upload(
    broker: str,
    account_id: str,
    content: bytes,
    *,
    filename: str,
) -> dict[str, Any]:
    """Import holdings from file — custom (csv/xlsx/image) or sarwa (image)."""
    broker = _broker_list_key(broker)
    raw = load_accounts_raw()
    _find_row_index(raw, broker, account_id)
    aid = account_id.strip().lower()
    lower = (filename or "").lower()

    if broker == "custom":
        from modules.portfolio.services.custom_portfolio import import_file

        result = import_file(aid, content, filename=filename)
        import_audit.log_event(
            source="custom_upload",
            broker="custom",
            account_id=aid,
            imported_count=int(result.get("imported") or 0),
            notes=f"filename={filename}",
        )
        return result

    if broker == "sarwa":
        if lower.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")):
            from modules.portfolio.services.sarwa_screenshot import parse_sarwa_screenshot
            from modules.portfolio.services.weekly_recorder import import_sarwa_holdings

            media = "image/png"
            if lower.endswith((".jpg", ".jpeg")):
                media = "image/jpeg"
            elif lower.endswith(".webp"):
                media = "image/webp"
            parsed = parse_sarwa_screenshot(content, media_type=media)
            result = import_sarwa_holdings(parsed["rows"], account_id=aid, notes=parsed.get("notes"))
            import_audit.log_event(
                source="sarwa_screenshot",
                broker="sarwa",
                account_id=aid,
                imported_count=len(parsed["rows"]),
                notes=f"filename={filename}",
            )
            return {
                "account_id": aid,
                "imported": len(parsed["rows"]),
                "source": "screenshot",
                "broker": "sarwa",
                **{k: v for k, v in result.items() if k in ("snapshot", "usd_inr")},
            }
        raise ValueError("Sarwa import supports portfolio screenshots (.png, .jpg)")

    raise ValueError(f"Import not supported for broker: {broker}")
