"""Import historical day-level totals from a Google Sheet into daily history."""

from __future__ import annotations

import csv
import io
import re
from datetime import datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from modules.portfolio.config import get_account_code, resolve_account_ref
from modules.portfolio.db import daily_history
from modules.portfolio.db import import_audit

_DATE_PATTERNS = (
    "%Y-%m-%d",
    "%d-%m-%Y",
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%d-%b-%Y",
    "%d %b %Y",
    "%d %B %Y",
    "%b %Y",
    "%B %Y",
)

DEFAULT_ACCOUNT_ALIASES: dict[str, list[str]] = {
    "RB": ["RB", "Zerodha P"],
    "HB": ["HB", "Groww M"],
    "AB": ["AB", "Zerodha A", "AngelOne", "Zerodha A + AngelOne"],
    "SB": ["SB", "Dhan"],
}


def _normalize_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").strip().lower())


def _sheet_urls(sheet_url: str, sheet_name: str) -> list[str]:
    m = re.search(r"/d/([a-zA-Z0-9\-_]+)", sheet_url)
    if m:
        sheet_id = m.group(1)
    else:
        sheet_id = sheet_url.strip()
    name = quote(sheet_name.strip())
    return [
        f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?tqx=out:csv&sheet={name}",
        f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&sheet={name}",
    ]


def _fetch_csv(sheet_url: str, sheet_name: str) -> str:
    headers = {"User-Agent": "Mozilla/5.0 (portfolio-history-import)"}
    last_error: str | None = None
    for url in _sheet_urls(sheet_url, sheet_name):
        try:
            req = Request(url, headers=headers)  # noqa: S310
            with urlopen(req, timeout=20) as resp:  # noqa: S310
                text = resp.read().decode("utf-8", errors="replace")
            if "Sign in" in text and "Google Account" in text:
                last_error = "Google Sheet is private (requires sign-in)."
                continue
            if "<html" in text.lower() and "," not in text:
                last_error = "Sheet did not return CSV data."
                continue
            return text
        except HTTPError as exc:
            if exc.code in (401, 403):
                last_error = "Google Sheet access denied. Share the sheet as 'Anyone with the link (Viewer)' or provide CSV."
            else:
                last_error = f"HTTP Error {exc.code}"
        except URLError as exc:
            last_error = str(exc)
    raise RuntimeError(
        last_error
        or "Could not fetch Google Sheet CSV. Ensure link is shareable to anyone with link (Viewer)."
    )


def _parse_date(value: str) -> str | None:
    raw = (value or "").strip()
    if not raw:
        return None
    compact = raw.replace(".", "-").replace("/", "-")
    try:
        return datetime.fromisoformat(compact).date().isoformat()
    except ValueError:
        pass
    for fmt in _DATE_PATTERNS:
        try:
            dt = datetime.strptime(raw, fmt)
            if fmt in ("%b %Y", "%B %Y"):
                dt = dt.replace(day=1)
            return dt.date().isoformat()
        except ValueError:
            continue
    return None


def _parse_amount(value: str | None) -> float | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    cleaned = (
        raw.replace(",", "")
        .replace("₹", "")
        .replace("$", "")
        .replace("INR", "")
        .replace("USD", "")
        .strip()
    )
    if cleaned in {"-", "—"}:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _pick_date_column(headers: list[str]) -> str | None:
    by_priority = ("date", "day", "asof", "snapshotdate", "month")
    normalized = {_normalize_text(h): h for h in headers}
    for key in by_priority:
        if key in normalized:
            return normalized[key]
    for h in headers:
        if _parse_date(h):
            return h
    return headers[0] if headers else None


def _resolve_account_columns(
    headers: list[str],
    aliases: dict[str, list[str]],
) -> dict[str, str]:
    out: dict[str, str] = {}
    normalized_headers = {h: _normalize_text(h) for h in headers}
    for code, terms in aliases.items():
        code_norm = _normalize_text(code)
        term_norms = [code_norm] + [_normalize_text(t) for t in terms]
        for h, h_norm in normalized_headers.items():
            if any(term and term in h_norm for term in term_norms):
                out[code.upper()] = h
                break
    return out


def _synthetic_position(*, symbol: str, value: float, account_id: str, account_code: str) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "exchange": "HIST",
        "asset_class": "equity",
        "currency": "INR",
        "quantity": 1.0,
        "avg_price": value,
        "last_price": value,
        "invested": value,
        "current_value": value,
        "pnl": 0.0,
        "pnl_pct": 0.0,
        "sector": "Imported",
        "market_cap": "Historical",
        "account_id": account_id,
        "account_code": account_code,
    }


def _snapshot_exists(*, scope: str, account_id: str | None, day_date: str) -> bool:
    with daily_history.connect() as conn:
        row = conn.execute(
            """
            SELECT 1
            FROM daily_snapshots
            WHERE scope = ? AND account_id IS ? AND day_date = ?
            LIMIT 1
            """,
            (scope, account_id, day_date),
        ).fetchone()
    return row is not None


def import_distribution_history(
    *,
    sheet_url: str,
    sheet_name: str = "Distribution",
    account_aliases: dict[str, list[str]] | None = None,
    overwrite_existing: bool = False,
) -> dict[str, Any]:
    """Import date-wise totals from a sheet tab into daily history DB."""
    csv_text = _fetch_csv(sheet_url, sheet_name)
    rows = list(csv.DictReader(io.StringIO(csv_text)))
    if not rows:
        raise ValueError(f"No rows found in sheet tab '{sheet_name}'.")

    headers = list(rows[0].keys())
    date_col = _pick_date_column(headers)
    if not date_col:
        raise ValueError("Could not detect a date column in the sheet.")

    aliases = account_aliases or DEFAULT_ACCOUNT_ALIASES
    account_cols = _resolve_account_columns(headers, aliases)
    if not account_cols:
        raise ValueError("Could not match account columns. Provide account_aliases in request.")

    imported_days = 0
    skipped_existing = 0
    skipped_empty = 0
    unresolved_codes: set[str] = set()
    used_columns = {"date": date_col, "accounts": account_cols}

    for row in rows:
        day = _parse_date(str(row.get(date_col) or ""))
        if not day:
            continue

        per_account: list[tuple[str, str, float]] = []
        for code, col in account_cols.items():
            amt = _parse_amount(row.get(col))
            if amt is None:
                continue
            try:
                account_id = resolve_account_ref(code)
                account_code = get_account_code(account_id)
            except KeyError:
                unresolved_codes.add(code)
                continue
            per_account.append((account_id, account_code, amt))

        if not per_account:
            skipped_empty += 1
            continue

        if not overwrite_existing and _snapshot_exists(scope="family", account_id=None, day_date=day):
            skipped_existing += 1
            continue

        family_positions: list[dict[str, Any]] = []
        for account_id, account_code, amt in per_account:
            if overwrite_existing or not _snapshot_exists(scope="account", account_id=account_id, day_date=day):
                daily_history.save_snapshot(
                    scope="account",
                    account_id=account_id,
                    positions=[
                        _synthetic_position(
                            symbol=f"HIST_{account_code}",
                            value=amt,
                            account_id=account_id,
                            account_code=account_code,
                        )
                    ],
                    source="sheet_distribution",
                    day_date=day,
                    notes=f"Imported from Google Sheet tab '{sheet_name}'",
                )
            family_positions.append(
                _synthetic_position(
                    symbol=f"HIST_{account_code}",
                    value=amt,
                    account_id=account_id,
                    account_code=account_code,
                )
            )

        daily_history.save_snapshot(
            scope="family",
            account_id=None,
            positions=family_positions,
            source="sheet_distribution",
            day_date=day,
            notes=f"Imported from Google Sheet tab '{sheet_name}'",
        )
        imported_days += 1

    result = {
        "sheet_name": sheet_name,
        "rows_seen": len(rows),
        "imported_days": imported_days,
        "skipped_existing_days": skipped_existing,
        "skipped_empty_rows": skipped_empty,
        "unresolved_codes": sorted(unresolved_codes),
        "columns_used": used_columns,
    }
    import_audit.log_event(
        source="sheet_distribution",
        broker="family",
        account_id=None,
        imported_count=imported_days,
        unresolved_codes=result["unresolved_codes"],
        notes=f"sheet={sheet_name},rows={len(rows)}",
    )
    return result
