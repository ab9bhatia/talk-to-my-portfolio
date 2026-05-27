"""Fetch and normalize Zerodha holdings."""

from __future__ import annotations

import copy
import logging
import hashlib
import json
import os
import time

from kiteconnect.exceptions import KiteException

from modules.portfolio.db import portfolio_cache as disk_cache
from modules.portfolio.services.market_data import enrich_holdings
from modules.portfolio.auth.zerodha import OAuthError, get_kite_client
from modules.portfolio.config import (
    ACCOUNTS,
    get_account_broker,
    get_account_code,
    get_enabled_accounts,
    get_enabled_groww_accounts,
    get_enabled_sarwa_accounts,
    get_sarwa_account,
)
from modules.portfolio.db import tokens as token_store
from modules.portfolio.services.portfolio_revalidate import schedule_family_revalidate

logger = logging.getLogger(__name__)

# In-memory hot cache (holdings + metrics). Yahoo metrics also cached separately (6h).
_PORTFOLIO_CACHE: dict[str, tuple[float, dict]] = {}
CACHE_TTL_SECONDS = int(os.getenv("PORTFOLIO_CACHE_TTL_SECONDS", "300"))
_STALE_MAX_SECONDS = int(os.getenv("PORTFOLIO_STALE_MAX_SECONDS", str(7 * 24 * 3600)))


def _cache_key(account_id: str | None, with_metrics: bool) -> str:
    if account_id:
        return f"account:{account_id}:metrics={with_metrics}"
    return f"family:metrics={with_metrics}"


def _holdings_hash(payload: dict) -> str:
    holdings = []
    for p in payload.get("portfolios") or []:
        holdings.extend(p.get("holdings") or [])
    if not holdings and payload.get("holdings"):
        holdings = payload["holdings"]
    slim = sorted(
        (h.get("symbol"), h.get("quantity"), h.get("last_price")) for h in holdings if h.get("symbol")
    )
    return hashlib.sha256(json.dumps(slim, default=str).encode()).hexdigest()[:16]


def _apply_llm_sector_cache(payload: dict) -> dict:
    """Apply SQLite sector cache + symbol overrides to cached holdings (no Yahoo / LLM)."""
    try:
        from modules.portfolio.services.market_data import apply_holdings_metric_overrides

        if payload.get("portfolios"):
            for portfolio in payload["portfolios"]:
                apply_holdings_metric_overrides(portfolio.get("holdings") or [])
        elif payload.get("holdings"):
            apply_holdings_metric_overrides(payload["holdings"])
    except Exception as exc:
        logger.debug("Sector cache patch skipped: %s", exc)
    return payload


def invalidate_portfolio_cache(account_id: str | None = None) -> None:
    """Clear cached portfolio data after login or manual refresh."""
    if account_id is None:
        _PORTFOLIO_CACHE.clear()
        for key in ("family:metrics=True", "family:metrics=False"):
            disk_cache.delete_snapshot(key)
        return

    for key in list(_PORTFOLIO_CACHE):
        if key.startswith(f"account:{account_id}:") or key.startswith("family:"):
            _PORTFOLIO_CACHE.pop(key, None)
    disk_cache.delete_snapshot(_cache_key(account_id, True))
    disk_cache.delete_snapshot(_cache_key(account_id, False))
    disk_cache.delete_snapshot(_cache_key(None, True))
    disk_cache.delete_snapshot(_cache_key(None, False))


def _memory_cached(key: str, refresh: bool) -> dict | None:
    if refresh:
        return None
    entry = _PORTFOLIO_CACHE.get(key)
    if not entry:
        return None
    cached_at, data = entry
    if (time.time() - cached_at) >= CACHE_TTL_SECONDS:
        _PORTFOLIO_CACHE.pop(key, None)
        return None
    return {
        **copy.deepcopy(data),
        "cached_at": cached_at,
        "from_cache": True,
        "stale": False,
    }


def _is_valid_family_payload(data: dict) -> bool:
    summary = data.get("summary")
    if not isinstance(summary, dict):
        return False
    required = ("total_current_value", "total_invested", "total_pnl", "total_pnl_pct")
    return all(k in summary for k in required) and isinstance(data.get("portfolios"), list)


def _is_valid_account_payload(data: dict) -> bool:
    summary = data.get("summary")
    if not isinstance(summary, dict):
        return False
    required = ("total_current_value", "total_invested", "total_pnl", "total_pnl_pct")
    return all(k in summary for k in required) and isinstance(data.get("holdings"), list)


def _payload_without_cache_meta(data: dict) -> dict:
    return {k: v for k, v in data.items() if k not in ("stale", "from_cache", "cached_at")}


def _refresh_holdings_ltps_from_yahoo(holdings: list[dict]) -> list[dict]:
    """Refresh LTP and P&L from Yahoo; keep quantity and avg_price from snapshot."""
    from modules.portfolio.services.weekly_recorder import _yahoo_ltp_inr

    updated: list[dict] = []
    for holding in holdings:
        row = dict(holding)
        symbol = row.get("symbol")
        if not symbol:
            updated.append(row)
            continue
        ltp = _yahoo_ltp_inr(symbol, row.get("exchange"))
        if ltp is None or ltp <= 0:
            updated.append(row)
            continue
        qty = float(row.get("quantity") or 0)
        avg = float(row.get("avg_price") or 0)
        row["last_price"] = round(ltp, 2)
        row["invested"] = round(qty * avg, 2)
        row["current_value"] = round(qty * ltp, 2)
        row["pnl"] = round(row["current_value"] - row["invested"], 2)
        row["pnl_pct"] = round((row["pnl"] / row["invested"] * 100) if row["invested"] else 0.0, 2)
        updated.append(row)
    return updated


def _refresh_stale_payload_prices(payload: dict, *, with_metrics: bool) -> dict:
    """Recompute summary after Yahoo LTP refresh on a cached portfolio payload."""
    if payload.get("portfolios"):
        portfolios = []
        for block in payload["portfolios"]:
            holdings = _refresh_holdings_ltps_from_yahoo(block.get("holdings") or [])
            if with_metrics:
                from modules.portfolio.services.market_data import apply_holdings_metric_overrides

                apply_holdings_metric_overrides(holdings)
            summary = summarize_holdings(holdings)
            portfolios.append({**block, "holdings": holdings, "summary": summary})
        all_holdings = [h for p in portfolios for h in p.get("holdings") or []]
        return {
            **payload,
            "portfolios": portfolios,
            "summary": summarize_holdings(all_holdings),
            "ltp_refreshed_offline": True,
        }

    holdings = _refresh_holdings_ltps_from_yahoo(payload.get("holdings") or [])
    if with_metrics:
        from modules.portfolio.services.market_data import apply_holdings_metric_overrides

        apply_holdings_metric_overrides(holdings)
    return {
        **payload,
        "holdings": holdings,
        "summary": summarize_holdings(holdings),
        "ltp_refreshed_offline": True,
    }


def _load_stale_account_portfolio(account_id: str, *, with_metrics: bool) -> dict | None:
    key = _cache_key(account_id, with_metrics)
    disk = _disk_cached(key, stale_ok=True)
    if disk is None:
        return None
    payload = _payload_without_cache_meta(disk)
    payload = _refresh_stale_payload_prices(payload, with_metrics=with_metrics)
    return {
        **payload,
        "cached_at": disk["cached_at"],
        "from_cache": True,
        "stale": True,
        "auth_degraded": True,
    }


def _load_stale_family_portfolio(*, with_metrics: bool) -> dict | None:
    key = _cache_key(None, with_metrics)
    disk = _disk_cached(key, stale_ok=True)
    if disk is None:
        return None
    payload = _payload_without_cache_meta(disk)
    payload = _refresh_stale_payload_prices(payload, with_metrics=with_metrics)
    return {
        **payload,
        "cached_at": disk["cached_at"],
        "from_cache": True,
        "stale": True,
        "auth_degraded": True,
        "ltp_refreshed_offline": True,
    }


def _disk_cached(key: str, *, stale_ok: bool) -> dict | None:
    if not stale_ok:
        return None
    snap = disk_cache.get_snapshot(key)
    if not snap:
        return None
    cached_at, data = snap
    is_family = key.startswith("family:")
    if is_family and not _is_valid_family_payload(data):
        disk_cache.delete_snapshot(key)
        return None
    if not is_family and not _is_valid_account_payload(data):
        disk_cache.delete_snapshot(key)
        return None
    age = time.time() - cached_at
    if age > _STALE_MAX_SECONDS:
        disk_cache.delete_snapshot(key)
        return None
    is_stale = age >= CACHE_TTL_SECONDS
    return {
        **copy.deepcopy(data),
        "cached_at": cached_at,
        "from_cache": True,
        "stale": is_stale,
    }


def _store_cache(key: str, data: dict) -> dict:
    cached_at = time.time()
    payload = copy.deepcopy(data)
    _PORTFOLIO_CACHE[key] = (cached_at, payload)
    disk_cache.put_snapshot(
        key,
        payload,
        cached_at=cached_at,
        holdings_hash=_holdings_hash(payload),
        source="live",
    )
    return {**payload, "cached_at": cached_at, "from_cache": False, "stale": False}


def normalize_holding(raw: dict, account_id: str) -> dict:
    """Convert a Kite holdings record into a normalized portfolio item."""
    account = ACCOUNTS[account_id]

    quantity = float(raw.get("quantity") or 0)
    avg_price = float(raw.get("average_price") or 0)
    last_price = float(raw.get("last_price") or 0)
    invested = quantity * avg_price
    current_value = quantity * last_price
    pnl = float(raw.get("pnl") or (current_value - invested))
    pnl_pct = (pnl / invested * 100) if invested else 0.0

    return {
        "symbol": raw.get("tradingsymbol"),
        "exchange": raw.get("exchange"),
        "isin": raw.get("isin"),
        "quantity": quantity,
        "avg_price": avg_price,
        "last_price": last_price,
        "invested": round(invested, 2),
        "current_value": round(current_value, 2),
        "pnl": round(pnl, 2),
        "pnl_pct": round(pnl_pct, 2),
        "day_change_pct": raw.get("day_change_percentage"),
        "account_id": account_id,
        "account_code": account["code"],
        "account_label": account["code"],
        "account_codes": account["code"],
        "broker": "zerodha",
    }


def summarize_holdings(holdings: list[dict]) -> dict:
    """Compute portfolio totals from normalized holdings."""
    total_invested = sum(item["invested"] for item in holdings)
    total_current_value = sum(item["current_value"] for item in holdings)
    total_pnl = sum(item["pnl"] for item in holdings)

    return {
        "holdings_count": len(holdings),
        "total_invested": round(total_invested, 2),
        "total_current_value": round(total_current_value, 2),
        "total_pnl": round(total_pnl, 2),
        "total_pnl_pct": round((total_pnl / total_invested * 100) if total_invested else 0.0, 2),
    }


def fetch_holdings(account_id: str) -> list[dict]:
    """Fetch and normalize equity + mutual fund holdings for one Zerodha account."""
    token_status = token_store.get_token_status(account_id)
    if token_status["needs_login"]:
        raise OAuthError(f"Token missing or expired for '{account_id}'. Please log in again.")

    kite = get_kite_client(account_id)

    try:
        raw_holdings = kite.holdings()
    except KiteException as exc:
        raise OAuthError(f"Failed to fetch holdings for '{account_id}': {exc}") from exc

    equity = [normalize_holding(item, account_id) for item in raw_holdings]

    from modules.portfolio.services.zerodha_mf import fetch_mf_holdings

    mf: list[dict] = []
    try:
        mf = fetch_mf_holdings(account_id)
    except OAuthError as exc:
        logger.warning("MF holdings skipped for %s: %s", account_id, exc)

    return equity + mf


def _fetch_portfolio_live(
    account_id: str,
    *,
    with_metrics: bool = True,
) -> dict:
    account = ACCOUNTS[account_id]
    holdings = fetch_holdings(account_id)
    if with_metrics:
        holdings = enrich_holdings(holdings)
    summary = summarize_holdings(holdings)
    return {
        "account_id": account_id,
        "account_code": account["code"],
        "account_label": account["code"],
        "user_id": account["user_id"],
        "broker": "zerodha",
        "summary": summary,
        "holdings": holdings,
    }


def fetch_portfolio(
    account_id: str,
    *,
    with_metrics: bool = True,
    refresh: bool = False,
    stale_ok: bool = True,
) -> dict:
    """Return holdings and summary for one broker account."""
    broker = get_account_broker(account_id)
    if broker == "groww":
        return fetch_groww_portfolio_cached(
            account_id,
            with_metrics=with_metrics,
            refresh=refresh,
            stale_ok=stale_ok,
        )
    if broker == "sarwa":
        return fetch_sarwa_portfolio_cached(
            account_id,
            with_metrics=with_metrics,
            refresh=refresh,
            stale_ok=stale_ok,
        )
    if broker == "custom":
        from modules.portfolio.services.custom_portfolio import fetch_custom_portfolio_live

        return fetch_custom_portfolio_live(account_id, with_metrics=with_metrics)
    return _fetch_cached_account(
        account_id,
        with_metrics=with_metrics,
        refresh=refresh,
        stale_ok=stale_ok,
        live_fetch=_fetch_portfolio_live,
    )


def _fetch_cached_account(
    account_id: str,
    *,
    with_metrics: bool,
    refresh: bool,
    stale_ok: bool,
    live_fetch,
) -> dict:
    key = _cache_key(account_id, with_metrics)
    if not refresh:
        cached = _memory_cached(key, refresh=False)
        if cached is not None:
            return _apply_llm_sector_cache(cached)
        disk = _disk_cached(key, stale_ok=stale_ok)
        if disk is not None:
            payload = _payload_without_cache_meta(disk)
            payload = _apply_llm_sector_cache(payload)
            _PORTFOLIO_CACHE[key] = (disk["cached_at"], payload)
            return {**disk, **payload}

    try:
        return _store_cache(key, live_fetch(account_id, with_metrics=with_metrics))
    except OAuthError:
        if not stale_ok:
            raise
        stale = _load_stale_account_portfolio(account_id, with_metrics=with_metrics)
        if stale is not None:
            return stale
        raise


def fetch_groww_portfolio_cached(
    account_id: str,
    *,
    with_metrics: bool = True,
    refresh: bool = False,
    stale_ok: bool = True,
) -> dict:
    """Return holdings and summary for one Groww account (stale-first cache)."""
    from modules.portfolio.services.groww_portfolio import fetch_groww_portfolio

    return _fetch_cached_account(
        account_id,
        with_metrics=with_metrics,
        refresh=refresh,
        stale_ok=stale_ok,
        live_fetch=fetch_groww_portfolio,
    )


def fetch_account_portfolio(
    account_id: str,
    *,
    with_metrics: bool = True,
    refresh: bool = False,
    stale_ok: bool = True,
) -> dict:
    """Return holdings for any known broker account."""
    broker = get_account_broker(account_id)
    if broker is None:
        raise KeyError(f"Unknown account_id: {account_id}")
    return fetch_portfolio(
        account_id,
        with_metrics=with_metrics,
        refresh=refresh,
        stale_ok=stale_ok,
    )


def _fetch_sarwa_portfolio_live(
    account_id: str,
    *,
    with_metrics: bool = True,
    enrich: bool = False,
) -> dict:
    """Latest Sarwa weekly import as a synthetic portfolio."""
    from modules.portfolio.services.market_data import apply_holdings_metric_overrides, enrich_holdings
    from modules.portfolio.services.weekly_recorder import sarwa_holdings_for_dashboard

    account = get_sarwa_account(account_id)
    holdings = sarwa_holdings_for_dashboard(account_id)
    if with_metrics and holdings:
        if enrich:
            holdings = enrich_holdings(holdings)
        else:
            apply_holdings_metric_overrides(holdings)
    summary = summarize_holdings(holdings)
    return {
        "account_id": account_id,
        "account_code": account["code"],
        "account_label": account["code"],
        "user_id": "sarwa",
        "broker": "sarwa",
        "summary": summary,
        "holdings": holdings,
    }


def fetch_sarwa_portfolio_cached(
    account_id: str,
    *,
    with_metrics: bool = True,
    refresh: bool = False,
    stale_ok: bool = True,
) -> dict:
    """Return Sarwa holdings from the latest weekly snapshot."""
    return _fetch_cached_account(
        account_id,
        with_metrics=with_metrics,
        refresh=refresh,
        stale_ok=stale_ok,
        live_fetch=_fetch_sarwa_portfolio_live,
    )


def _after_family_live_fetch(payload: dict) -> None:
    """Record weekly + daily snapshots when brokers return live data."""
    try:
        from modules.portfolio.config import get_enabled_sarwa_accounts
        from modules.portfolio.services.daily_recorder import record_today_from_family
        from modules.portfolio.services.weekly_recorder import (
            record_if_new_week,
            refresh_all_current_week_ltps,
        )

        account_ids = list(get_enabled_accounts()) + list(get_enabled_groww_accounts())
        account_ids.extend(get_enabled_sarwa_accounts())
        refresh_all_current_week_ltps(account_ids)
        record_if_new_week(payload, source="live")
        record_today_from_family(payload, source="live")
    except Exception as exc:
        logger.warning("History snapshot skipped: %s", exc)


def _fetch_family_live(*, with_metrics: bool = True) -> dict:
    from modules.portfolio.config import get_enabled_custom_accounts
    from modules.portfolio.db import custom_holdings as custom_holdings_store
    from modules.portfolio.services.custom_portfolio import fetch_custom_portfolio_live

    accounts = get_enabled_accounts()
    groww_accounts = get_enabled_groww_accounts()
    sarwa_accounts = get_enabled_sarwa_accounts()
    custom_accounts = get_enabled_custom_accounts()
    portfolios = []
    errors = []

    for account_id in accounts:
        try:
            portfolios.append(_fetch_portfolio_live(account_id, with_metrics=with_metrics))
        except OAuthError as exc:
            stale = _load_stale_account_portfolio(account_id, with_metrics=with_metrics)
            if stale:
                portfolios.append(stale)
            errors.append(
                {
                    "account": get_account_code(account_id),
                    "broker": "zerodha",
                    "error": str(exc),
                    "using_snapshot": bool(stale),
                }
            )

    if groww_accounts:
        from modules.portfolio.auth.groww import GrowwError

        for account_id in groww_accounts:
            try:
                portfolios.append(
                    fetch_groww_portfolio_cached(account_id, with_metrics=with_metrics, refresh=True)
                )
            except GrowwError as exc:
                stale = _load_stale_account_portfolio(account_id, with_metrics=with_metrics)
                if stale:
                    portfolios.append(stale)
                errors.append(
                    {
                        "account": get_account_code(account_id),
                        "broker": "groww",
                        "error": str(exc),
                        "using_snapshot": bool(stale),
                    }
                )

    for account_id in sarwa_accounts:
        try:
            portfolios.append(
                fetch_sarwa_portfolio_cached(
                    account_id, with_metrics=with_metrics, refresh=False, stale_ok=True
                )
            )
        except Exception as exc:
            errors.append(
                {"account": get_account_code(account_id), "broker": "sarwa", "error": str(exc)}
            )

    for account_id in custom_accounts:
        try:
            if custom_holdings_store.has_holdings(account_id):
                portfolios.append(fetch_custom_portfolio_live(account_id, with_metrics=with_metrics))
            else:
                errors.append(
                    {
                        "account": get_account_code(account_id),
                        "broker": "custom",
                        "error": "No holdings imported yet — upload CSV/Excel in Setup.",
                    }
                )
        except Exception as exc:
            errors.append(
                {"account": get_account_code(account_id), "broker": "custom", "error": str(exc)}
            )

    all_holdings = [holding for portfolio in portfolios for holding in portfolio["holdings"]]
    payload = {
        "accounts_requested": len(accounts) + len(groww_accounts) + len(sarwa_accounts) + len(custom_accounts),
        "accounts_loaded": len(portfolios),
        "summary": summarize_holdings(all_holdings),
        "portfolios": portfolios,
        "errors": errors,
    }
    if any(e.get("using_snapshot") for e in errors):
        payload["auth_degraded"] = True
    _after_family_live_fetch(payload)
    return payload


def _merge_sarwa_into_family(
    payload: dict,
    *,
    with_metrics: bool,
    refresh: bool = False,
) -> dict:
    """Always attach latest Sarwa weekly import (even when serving stale broker cache)."""
    sarwa_accounts = get_enabled_sarwa_accounts()
    if not sarwa_accounts:
        return payload

    portfolios = [p for p in (payload.get("portfolios") or []) if p.get("broker") != "sarwa"]
    for account_id in sarwa_accounts:
        try:
            block = _fetch_sarwa_portfolio_live(
                account_id, with_metrics=with_metrics, enrich=refresh
            )
            if block.get("holdings"):
                portfolios.append(block)
        except Exception as exc:
            logger.warning("Sarwa merge skipped for %s: %s", account_id, exc)

    all_holdings = [h for p in portfolios for h in p.get("holdings") or []]
    return {
        **payload,
        "portfolios": portfolios,
        "summary": summarize_holdings(all_holdings),
        "accounts_requested": (
            len(get_enabled_accounts())
            + len(get_enabled_groww_accounts())
            + len(sarwa_accounts)
        ),
        "accounts_loaded": len(portfolios),
    }


def fetch_family_portfolio(
    *,
    with_metrics: bool = True,
    refresh: bool = False,
    stale_ok: bool = True,
) -> dict:
    """
    Return portfolio data for all enabled accounts.

    Stale-first: serves SQLite snapshot when in-memory TTL expired, then
    revalidates Zerodha + Yahoo in the background.
    """
    key = _cache_key(None, with_metrics)

    if refresh:
        live = _fetch_family_live(with_metrics=with_metrics)
        if live.get("portfolios"):
            return _merge_sarwa_into_family(
                _store_cache(key, live),
                with_metrics=with_metrics,
                refresh=True,
            )
        stale = _load_stale_family_portfolio(with_metrics=with_metrics)
        if stale is not None:
            stale["errors"] = live.get("errors") or []
            return _merge_sarwa_into_family(
                stale,
                with_metrics=with_metrics,
                refresh=False,
            )
        return _merge_sarwa_into_family(
            _store_cache(key, live),
            with_metrics=with_metrics,
            refresh=True,
        )

    cached = _memory_cached(key, refresh=False)
    if cached is not None:
        return _merge_sarwa_into_family(
            _apply_llm_sector_cache(cached), with_metrics=with_metrics, refresh=False
        )

    disk = _disk_cached(key, stale_ok=stale_ok)
    if disk is not None:
        payload = _payload_without_cache_meta(disk)
        payload = _apply_llm_sector_cache(payload)
        payload = _merge_sarwa_into_family(
            payload, with_metrics=with_metrics, refresh=False
        )
        _PORTFOLIO_CACHE[key] = (disk["cached_at"], payload)
        merged_disk = {**disk, **payload}
        if disk.get("stale"):
            schedule_family_revalidate(with_metrics=with_metrics)
        return merged_disk

    live = _fetch_family_live(with_metrics=with_metrics)
    if live.get("portfolios"):
        return _merge_sarwa_into_family(
            _store_cache(key, live),
            with_metrics=with_metrics,
            refresh=True,
        )

    stale = _load_stale_family_portfolio(with_metrics=with_metrics)
    if stale is not None:
        stale["errors"] = live.get("errors") or []
        return _merge_sarwa_into_family(stale, with_metrics=with_metrics, refresh=False)

    return _merge_sarwa_into_family(
        _store_cache(key, live),
        with_metrics=with_metrics,
        refresh=True,
    )
