"""Build structured context for the portfolio-level AI agent."""

from __future__ import annotations

import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import yfinance as yf

from modules.portfolio.portfolio_profile import (
    AVOID_FLAGS,
    INVESTOR_PROFILE,
    MAX_DEBT_TO_EQUITY,
    MAX_PCT_PER_SECTOR,
    MAX_PCT_PER_STOCK,
    PREFERRED_GROWTH_THEMES,
    TRADITIONAL_THEMES_TO_DEWEIGHT,
)
from modules.portfolio.services.macro_snapshot import get_macro_snapshot
from modules.portfolio.services.market_data import _quiet_yfinance, normalize_symbol, resolve_yahoo_ticker
from modules.portfolio.services.portfolio import fetch_family_portfolio

def _weight_pct(value: float, total: float) -> float | None:
    if not total:
        return None
    return round((value / total) * 100, 2)


def _short_summary(text: str | None, limit: int = 220) -> str | None:
    if not text:
        return None
    cleaned = re.sub(r"\s+", " ", str(text)).strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"


def _match_growth_themes_from_text(*texts: str | None) -> list[str]:
    """Match multi-word theme phrases against sector/industry/summary — never the ticker."""
    blob = " ".join(t for t in texts if t).lower()
    if not blob:
        return []

    matched: list[str] = []
    for item in PREFERRED_GROWTH_THEMES:
        for phrase in item["keywords"].split(","):
            phrase = phrase.strip().lower()
            if len(phrase) < 4:
                continue
            if phrase in blob:
                matched.append(item["theme"])
                break
    return matched


def _fetch_yahoo_profile(symbol: str, exchange: str | None) -> dict[str, Any]:
    ticker = resolve_yahoo_ticker(symbol, exchange)
    if not ticker:
        return {}

    try:
        with _quiet_yfinance():
            info = yf.Ticker(ticker).info or {}
    except Exception:
        return {}

    de = info.get("debtToEquity")
    debt_to_equity = None
    if de is not None:
        try:
            val = float(de)
            debt_to_equity = round(val / 100, 2) if val > 10 else round(val, 2)
        except (TypeError, ValueError):
            pass

    yahoo_sector = info.get("sector")
    yahoo_industry = info.get("industry")
    summary = _short_summary(info.get("longBusinessSummary") or info.get("description"))

    return {
        "debt_to_equity": debt_to_equity,
        "yahoo_sector": yahoo_sector,
        "yahoo_industry": yahoo_industry,
        "business_summary": summary,
    }


def _batch_yahoo_profiles(holdings: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    symbols = {h.get("symbol") or "" for h in holdings if h.get("symbol")}
    out: dict[str, dict[str, Any]] = {s: {} for s in symbols}
    if not symbols:
        return out

    sym_to_exchange = {h.get("symbol") or "": h.get("exchange") for h in holdings if h.get("symbol")}

    with ThreadPoolExecutor(max_workers=min(8, len(symbols))) as pool:
        futures = {
            pool.submit(_fetch_yahoo_profile, sym, sym_to_exchange.get(sym)): sym for sym in symbols
        }
        for fut in as_completed(futures):
            sym = futures[fut]
            try:
                out[sym] = fut.result()
            except Exception:
                out[sym] = {}
    return out


def _enrich_holding_flags(
    holding: dict[str, Any],
    total_value: float,
    profile: dict[str, Any],
) -> dict[str, Any]:
    weight = _weight_pct(holding.get("current_value") or 0, total_value)
    sector = holding.get("sector")
    symbol = holding.get("symbol") or ""

    yahoo_sector = profile.get("yahoo_sector")
    yahoo_industry = profile.get("yahoo_industry")
    business_summary = profile.get("business_summary")
    de = profile.get("debt_to_equity")

    flags: list[str] = []
    if weight is not None and weight > MAX_PCT_PER_STOCK:
        flags.append(f"Over single-stock limit ({weight}% > {MAX_PCT_PER_STOCK}%)")
    if de is not None and de > MAX_DEBT_TO_EQUITY:
        flags.append(f"High debt/equity ({de})")

    theme_hints = _match_growth_themes_from_text(yahoo_sector, yahoo_industry, business_summary)

    return {
        "symbol": symbol,
        "exchange": holding.get("exchange"),
        "account_label": holding.get("account_label"),
        "sector": sector,
        "yahoo_sector": yahoo_sector,
        "yahoo_industry": yahoo_industry,
        "business_summary": business_summary,
        "market_cap": holding.get("market_cap"),
        "weight_pct": weight,
        "current_value": holding.get("current_value"),
        "invested": holding.get("invested"),
        "pnl": holding.get("pnl"),
        "pnl_pct": holding.get("pnl_pct"),
        "pe_ratio": holding.get("pe_ratio"),
        "pct_from_52w_high": holding.get("pct_from_52w_high"),
        "upside_pct": holding.get("upside_pct"),
        "rating_label": holding.get("rating_label"),
        "rating_source": holding.get("rating_source"),
        "debt_to_equity": de,
        "theme_hints_from_fundamentals": theme_hints,
        "deterministic_flags": flags,
    }


def _sector_allocation(holdings: list[dict[str, Any]], total_value: float) -> list[dict[str, Any]]:
    buckets: dict[str, float] = defaultdict(float)
    for h in holdings:
        label = h.get("sector") or "Unclassified"
        buckets[label] += float(h.get("current_value") or 0)

    rows = []
    for sector, value in sorted(buckets.items(), key=lambda x: -x[1]):
        pct = _weight_pct(value, total_value) or 0
        row = {"sector": sector, "value": round(value, 2), "weight_pct": pct}
        if pct > MAX_PCT_PER_SECTOR:
            row["flag"] = f"Over sector limit ({pct}% > {MAX_PCT_PER_SECTOR}%)"
        rows.append(row)
    return rows


def build_portfolio_context(*, refresh: bool = False) -> dict[str, Any]:
    """Aggregate holdings, sectors, macro, and profile for the agent."""
    family = fetch_family_portfolio(refresh=refresh, stale_ok=not refresh)
    holdings = [h for p in family["portfolios"] for h in p["holdings"]]
    summary = family["summary"]
    total_value = float(summary.get("total_current_value") or 0)

    profiles = _batch_yahoo_profiles(holdings)
    enriched = [
        _enrich_holding_flags(h, total_value, profiles.get(h.get("symbol") or "", {})) for h in holdings
    ]
    enriched.sort(key=lambda x: -(x.get("current_value") or 0))

    return {
        "investor_profile": INVESTOR_PROFILE,
        "constraints": {
            "max_pct_per_stock": MAX_PCT_PER_STOCK,
            "max_pct_per_sector": MAX_PCT_PER_SECTOR,
            "max_debt_to_equity": MAX_DEBT_TO_EQUITY,
            "avoid": AVOID_FLAGS,
            "prefer_growth_themes": [t["theme"] for t in PREFERRED_GROWTH_THEMES],
            "deemphasize": TRADITIONAL_THEMES_TO_DEWEIGHT,
            "classification_rule": (
                "Classify businesses using yahoo_sector, yahoo_industry, and business_summary — "
                "never ticker substrings (e.g. GRINFRA is construction/EPC, not data centers)."
            ),
        },
        "portfolio_summary": summary,
        "sector_allocation": _sector_allocation(holdings, total_value),
        "holdings": enriched,
        "macro": get_macro_snapshot(),
        "accounts_loaded": family.get("accounts_loaded"),
        "errors": family.get("errors", []),
        "cached_at": family.get("cached_at"),
        "from_cache": family.get("from_cache", False),
    }
