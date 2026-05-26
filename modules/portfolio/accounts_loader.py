"""Load broker account metadata from JSON (keeps names/IDs out of source control)."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

_MODULE_DIR = Path(__file__).resolve().parent
_EXAMPLE_PATH = _MODULE_DIR / "accounts.example.json"
_USER_PATH = _MODULE_DIR / "accounts.json"


def _config_path() -> Path:
    custom = os.getenv("ACCOUNTS_CONFIG", "").strip()
    if custom:
        return Path(custom).expanduser()
    if _USER_PATH.is_file():
        return _USER_PATH
    return _EXAMPLE_PATH


def load_accounts_raw() -> dict[str, Any]:
    path = _config_path()
    if not path.is_file():
        raise FileNotFoundError(
            f"Account config not found: {path}. Copy accounts.example.json to accounts.json and edit."
        )
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def save_accounts_raw(data: dict[str, Any]) -> Path:
    """Persist accounts.json (creates parent dirs if needed)."""
    path = _config_path()
    if path == _EXAMPLE_PATH and not _USER_PATH.is_file():
        path = _USER_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    return path


def accounts_config_path() -> Path:
    return _config_path()


def build_account_registry(raw: dict[str, Any]) -> tuple[
    dict[str, dict],
    dict[str, dict],
    dict[str, dict],
    dict[str, dict],
    dict[str, str],
    str,
]:
    accounts: dict[str, dict] = {}
    groww: dict[str, dict] = {}
    sarwa: dict[str, dict] = {}
    custom: dict[str, dict] = {}
    codes: dict[str, str] = {}

    for row in raw.get("zerodha") or []:
        aid = row["id"]
        accounts[aid] = {
            "code": row["code"],
            "label": row.get("label") or aid,
            "user_id": row.get("user_id") or "",
            "enabled": bool(row.get("enabled", True)),
            "disabled_reason": row.get("disabled_reason"),
            "redirect_url": row.get("redirect_url"),
            "auth_port": row.get("auth_port", 8000),
        }
        codes[aid] = row["code"]

    for row in raw.get("groww") or []:
        aid = row["id"]
        groww[aid] = {
            "code": row["code"],
            "label": row.get("label") or aid,
            "relation": row.get("relation") or "",
            "user_id": row.get("user_id") or "groww",
            "enabled": bool(row.get("enabled", True)),
            "disabled_reason": row.get("disabled_reason"),
        }
        codes[aid] = row["code"]

    for row in raw.get("sarwa") or []:
        aid = row["id"]
        sarwa[aid] = {
            "code": row["code"],
            "label": row.get("label") or aid,
            "enabled": bool(row.get("enabled", True)),
            "disabled_reason": row.get("disabled_reason"),
        }
        codes[aid] = row["code"]

    for row in raw.get("custom") or []:
        aid = row["id"]
        custom[aid] = {
            "code": row["code"],
            "label": row.get("label") or aid,
            "enabled": bool(row.get("enabled", True)),
            "disabled_reason": row.get("disabled_reason"),
            "import_kind": row.get("import_kind"),
        }
        codes[aid] = row["code"]

    legacy = str(raw.get("legacy_zerodha_account_id") or "primary").strip().lower()
    return accounts, groww, sarwa, custom, codes, legacy


def list_all_account_rows(raw: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Flatten all broker rows for setup UI."""
    data = raw if raw is not None else load_accounts_raw()
    out: list[dict[str, Any]] = []
    for broker_key, broker_name in (
        ("zerodha", "zerodha"),
        ("groww", "groww"),
        ("sarwa", "sarwa"),
        ("custom", "custom"),
    ):
        for row in data.get(broker_key) or []:
            out.append({**row, "broker": broker_name})
    return out


def collect_used_codes(raw: dict[str, Any] | None = None) -> set[str]:
    return {str(r.get("code", "")).upper() for r in list_all_account_rows(raw) if r.get("code")}


def suggest_account_code(label: str, raw: dict[str, Any] | None = None) -> str:
    """Pick an unused 2-letter code from label initials or C1, C2, …"""
    used = collect_used_codes(raw)
    parts = [p for p in re.split(r"[^A-Za-z0-9]+", label.strip()) if p]
    candidates: list[str] = []
    if len(parts) >= 2:
        candidates.append((parts[0][0] + parts[1][0]).upper())
    if parts:
        candidates.append(parts[0][:2].upper())
    for code in candidates:
        if len(code) == 2 and code not in used:
            return code
    n = 1
    while True:
        code = f"C{n}"
        if code not in used:
            return code
        n += 1
