"""Record weekly snapshots from live portfolio or manual Sarwa imports."""

from __future__ import annotations

import logging
from typing import Any

from modules.portfolio.config import get_account_code, get_sarwa_account
from modules.portfolio.db import weekly_history
from modules.portfolio.services.fx import usd_inr_rate, usd_to_inr
from modules.portfolio.services.market_data import enrich_holdings, is_us_exchange, resolve_yahoo_ticker

logger = logging.getLogger(__name__)


def _yahoo_ltp_usd(symbol: str, exchange: str | None) -> float | None:
    ticker = resolve_yahoo_ticker(symbol, exchange)
    if not ticker:
        return None
    try:
        import yfinance as yf

        from modules.portfolio.services.market_data import _quiet_yfinance

        with _quiet_yfinance():
            info = yf.Ticker(ticker).info or {}
        price = info.get("regularMarketPrice") or info.get("previousClose")
        return float(price) if price else None
    except Exception:
        return None


def _yahoo_ltp_inr(symbol: str, exchange: str | None) -> float | None:
    """Yahoo LTP in INR for NSE/BSE; USD listings converted at spot."""
    price = _yahoo_ltp_usd(symbol, exchange)
    if price is None:
        return None
    if is_us_exchange(exchange):
        return usd_to_inr(price)
    return price


def record_positions_snapshot(
    *,
    scope: str,
    account_id: str | None,
    positions: list[dict[str, Any]],
    source: str,
    notes: str | None = None,
    usd_inr: float | None = None,
) -> dict[str, Any]:
    """Persist one weekly snapshot (replaces same week if exists)."""
    return weekly_history.save_snapshot(
        scope=scope,
        account_id=account_id,
        positions=positions,
        source=source,
        usd_inr=usd_inr,
        notes=notes,
    )


def record_family_from_payload(family: dict[str, Any], *, source: str = "live") -> list[dict[str, Any]]:
    """Save family + per-account weekly snapshots from fetch_family_portfolio result."""
    results: list[dict[str, Any]] = []
    all_holdings: list[dict[str, Any]] = []
    for portfolio in family.get("portfolios") or []:
        holdings = list(portfolio.get("holdings") or [])
        all_holdings.extend(holdings)
        aid = portfolio.get("account_id")
        if aid:
            results.append(
                record_positions_snapshot(
                    scope="account",
                    account_id=aid,
                    positions=holdings,
                    source=source,
                )
            )
    results.insert(
        0,
        record_positions_snapshot(
            scope="family",
            account_id=None,
            positions=all_holdings,
            source=source,
        ),
    )
    return results


def record_if_new_week(
    family: dict[str, Any],
    *,
    source: str = "live",
    force: bool = False,
) -> list[dict[str, Any]] | None:
    """Record snapshots when none exist for current ISO week (or force=True)."""
    week = weekly_history.week_start_for()
    existing = weekly_history.list_snapshots(scope="family", account_id=None, limit=1)
    if not force and existing and existing[0]["week_start"] == week:
        return None
    return record_family_from_payload(family, source=source)


def refresh_current_week_ltps_for_scope(
    *,
    scope: str,
    account_id: str | None,
) -> dict[str, Any]:
    """Refresh LTP on current week snapshot via Yahoo (no broker API needed)."""
    return weekly_history.refresh_current_week_ltps(
        scope=scope,
        account_id=account_id,
        price_fetcher=_yahoo_ltp_inr,
    )


def refresh_all_current_week_ltps(family_account_ids: list[str]) -> list[dict[str, Any]]:
    out = [refresh_current_week_ltps_for_scope(scope="family", account_id=None)]
    for aid in family_account_ids:
        out.append(refresh_current_week_ltps_for_scope(scope="account", account_id=aid))
    return out


def sarwa_positions_from_rows(
    rows: list[dict[str, Any]],
    *,
    account_id: str,
    usd_inr: float | None = None,
    enrich: bool = True,
) -> list[dict[str, Any]]:
    """Convert manual Sarwa rows (USD) to normalized INR holdings."""
    account = get_sarwa_account(account_id)
    fx = usd_inr if usd_inr is not None else usd_inr_rate()
    normalized: list[dict[str, Any]] = []

    for raw in rows:
        symbol = (raw.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        qty = float(raw.get("quantity") or 0)
        avg_usd = float(raw.get("avg_price_usd") or raw.get("avg_price") or 0)
        ltp_usd = float(
            raw.get("last_price_usd")
            or raw.get("last_price")
            or raw.get("ltp_usd")
            or avg_usd
            or 0
        )
        avg_inr = round(avg_usd * fx, 2)
        ltp_inr = round(ltp_usd * fx, 2)
        invested = round(qty * avg_inr, 2)
        current = round(qty * ltp_inr, 2)
        pnl = round(current - invested, 2)
        row = {
            "symbol": symbol,
            "exchange": (raw.get("exchange") or "US").upper(),
            "currency": "USD",
            "quantity": qty,
            "avg_price": avg_inr,
            "last_price": ltp_inr,
            "avg_price_usd": avg_usd,
            "last_price_usd": ltp_usd,
            "invested": invested,
            "current_value": current,
            "pnl": pnl,
            "pnl_pct": round((pnl / invested * 100) if invested else 0.0, 2),
            "account_id": account_id,
            "account_code": account["code"],
            "account_label": account["code"],
            "account_codes": account["code"],
            "broker": "sarwa",
            "asset_class": raw.get("asset_class") or "equity",
        }
        normalized.append(row)

    if enrich and normalized:
        normalized = enrich_holdings(normalized)
    return normalized


def sync_family_weekly_snapshot(*, source: str = "merged") -> dict[str, Any] | None:
    """Update family weekly + daily snapshots from current dashboard holdings (incl. Sarwa)."""
    from modules.portfolio.services.portfolio import fetch_family_portfolio

    family = fetch_family_portfolio(refresh=False, stale_ok=True)
    all_holdings = [h for p in family.get("portfolios") or [] for h in p.get("holdings") or []]
    if not all_holdings:
        return None
    snap = record_positions_snapshot(
        scope="family",
        account_id=None,
        positions=all_holdings,
        source=source,
    )
    try:
        from modules.portfolio.services.daily_recorder import record_today_from_family

        record_today_from_family(family, source=source)
    except Exception as exc:
        logger.warning("Daily snapshot sync skipped: %s", exc)
    return snap


def repair_sarwa_weekly_snapshot(account_id: str = "sarwa") -> dict[str, Any] | None:
    """Rewrite current Sarwa snapshot with correct USD→INR values."""
    snap = weekly_history.latest_snapshot(scope="account", account_id=account_id)
    if not snap:
        return None
    fx = float(snap.get("usd_inr") or 0) or usd_inr_rate()
    repaired: list[dict[str, Any]] = []
    for p in snap.get("positions") or []:
        extra = dict(p.get("extra") or {})
        if not extra and p.get("extra_json"):
            try:
                import json

                extra = json.loads(p["extra_json"])
            except json.JSONDecodeError:
                extra = {}
        inr = normalize_sarwa_holding_inr(p, extra, fx=fx)
        row = {
            "symbol": p["symbol"],
            "exchange": (p.get("exchange") or "US").upper(),
            "currency": "USD",
            "quantity": p["quantity"],
            "asset_class": p.get("asset_class") or "equity",
            **inr,
            **{k: extra[k] for k in extra if k not in inr},
        }
        repaired.append(row)
    return weekly_history.save_snapshot(
        scope="account",
        account_id=account_id,
        positions=repaired,
        source="sarwa_repair_inr",
        usd_inr=fx,
        notes="Repaired USD→INR conversion on snapshot",
        week_start=snap.get("week_start"),
    )


def import_sarwa_holdings(
    rows: list[dict[str, Any]],
    *,
    account_id: str = "sarwa",
    notes: str | None = None,
    enrich: bool = True,
) -> dict[str, Any]:
    """Save Sarwa weekly snapshot and return holdings for dashboard merge."""
    fx = usd_inr_rate()
    positions = sarwa_positions_from_rows(rows, account_id=account_id, usd_inr=fx, enrich=enrich)
    snap = record_positions_snapshot(
        scope="account",
        account_id=account_id,
        positions=positions,
        source="sarwa_manual",
        usd_inr=fx,
        notes=notes or "Sarwa manual import (USD → INR)",
    )
    family_snap = sync_family_weekly_snapshot(source="sarwa_manual")
    return {"snapshot": snap, "family_snapshot": family_snap, "holdings": positions, "usd_inr": fx}


def normalize_sarwa_holding_inr(
    position: dict[str, Any],
    extra: dict[str, Any],
    *,
    fx: float | None = None,
) -> dict[str, float]:
    """
    Build INR price/value fields for Sarwa (USD) holdings.
    Fixes snapshots where USD was stored in INR columns (e.g. 89 instead of ~8500).
    """
    rate = float(fx or 0) or usd_inr_rate()
    qty = float(position.get("quantity") or 0)

    last_usd = extra.get("last_price_usd")
    avg_usd = extra.get("avg_price_usd")
    try:
        last_usd = float(last_usd) if last_usd is not None else None
    except (TypeError, ValueError):
        last_usd = None
    try:
        avg_usd = float(avg_usd) if avg_usd is not None else None
    except (TypeError, ValueError):
        avg_usd = None

    stored_ltp = float(position.get("last_price") or 0)
    stored_avg = float(position.get("avg_price") or 0)

    if not last_usd and stored_ltp > 0:
        if rate > 0 and stored_ltp >= rate * 2:
            last_usd = round(stored_ltp / rate, 4)
        else:
            last_usd = stored_ltp

    if not avg_usd and stored_avg > 0:
        if rate > 0 and stored_avg >= rate * 2:
            avg_usd = round(stored_avg / rate, 4)
        else:
            avg_usd = stored_avg

    if not last_usd:
        last_usd = 0.0
    if not avg_usd:
        avg_usd = last_usd

    ltp_inr = round(last_usd * rate, 2)
    avg_inr = round(avg_usd * rate, 2)
    invested = round(qty * avg_inr, 2)
    current = round(qty * ltp_inr, 2)
    pnl = round(current - invested, 2)
    pnl_pct = round((pnl / invested * 100) if invested else 0.0, 2)
    return {
        "usd_inr": rate,
        "last_price_usd": last_usd,
        "avg_price_usd": avg_usd,
        "last_price": ltp_inr,
        "avg_price": avg_inr,
        "invested": invested,
        "current_value": current,
        "pnl": pnl,
        "pnl_pct": pnl_pct,
    }


_SARWA_METRIC_KEYS = (
    "high_52w",
    "pct_from_52w_high",
    "upside_pct",
    "pe_ratio",
    "roce",
    "debt_to_equity",
    "rsi_14",
    "dma_200",
    "pct_vs_dma200",
    "pct_in_52w_range",
    "rating_label",
    "rating_slug",
    "rating_rank",
    "rating_source",
    "rating_reasons",
    "last_price_usd",
    "avg_price_usd",
    "currency",
    "yahoo_ticker",
)


def sarwa_holdings_for_dashboard(account_id: str = "sarwa") -> list[dict[str, Any]]:
    """Latest Sarwa weekly snapshot as live holdings (for family merge)."""
    snap = weekly_history.latest_snapshot(scope="account", account_id=account_id)
    if not snap:
        return []
    positions = snap.get("positions") or []
    if (
        len(positions) >= 3
        and float(snap.get("total_current") or 0) < 100_000
        and float(snap.get("total_current") or 0) > 0
    ):
        repair_sarwa_weekly_snapshot(account_id)
        snap = weekly_history.latest_snapshot(scope="account", account_id=account_id) or snap
    code = get_account_code(account_id)
    fx = float(snap.get("usd_inr") or 0) or usd_inr_rate()
    out: list[dict[str, Any]] = []
    for p in snap.get("positions") or []:
        extra = p.get("extra") or {}
        if not extra and p.get("extra_json"):
            try:
                import json

                extra = json.loads(p["extra_json"])
            except json.JSONDecodeError:
                extra = {}

        inr = normalize_sarwa_holding_inr(p, extra, fx=fx)

        row = {
            "symbol": p["symbol"],
            "exchange": (p.get("exchange") or "US").upper(),
            "quantity": p["quantity"],
            "avg_price": inr["avg_price"],
            "last_price": inr["last_price"],
            "invested": inr["invested"],
            "current_value": inr["current_value"],
            "pnl": inr["pnl"],
            "pnl_pct": inr["pnl_pct"],
            "sector": p.get("sector") or extra.get("sector"),
            "market_cap": p.get("market_cap") or extra.get("market_cap"),
            "pe_ratio": p.get("pe_ratio") or extra.get("pe_ratio"),
            "rating_label": p.get("rating_label") or extra.get("rating_label"),
            "pct_from_52w_high": extra.get("pct_from_52w_high"),
            "upside_pct": extra.get("upside_pct"),
            "rating_slug": extra.get("rating_slug"),
            "rating_rank": extra.get("rating_rank"),
            "account_id": account_id,
            "account_code": code,
            "account_label": code,
            "account_codes": code,
            "broker": "sarwa",
            "asset_class": p.get("asset_class") or "equity",
            "currency": "USD",
            "last_price_usd": inr["last_price_usd"],
            "avg_price_usd": inr["avg_price_usd"],
            "usd_inr": inr["usd_inr"],
        }
        for key in _SARWA_METRIC_KEYS:
            if key not in row and extra.get(key) is not None:
                row[key] = extra[key]
        out.append(row)
    return out
