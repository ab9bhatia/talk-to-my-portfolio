"""LLM sector classification for holdings Yahoo cannot map."""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.request
from typing import Any

from modules.portfolio.db import sector_llm_cache
from modules.portfolio.services.market_data import (
    _club_sector_label,
    classify_sector,
    has_symbol_sector_override,
    normalize_symbol,
)

logger = logging.getLogger(__name__)

# Dashboard sector taxonomy (keep in sync with classify_sector / group-by).
ALLOWED_SECTORS: tuple[str, ...] = (
    "IT",
    "Banking",
    "NBFC",
    "Pharma",
    "FMCG",
    "Auto",
    "Oil & Gas",
    "Metals",
    "Real Estate",
    "Power",
    "Telecom",
    "Insurance",
    "Retail",
    "Manufacturing",
    "Chemical",
    "Healthcare",
    "Financial Services",
    "Consumer",
    "Defense",
    "Momentum",
    "Newedge Tech",
    "ETF",
    "Mutual fund",
    "Crypto",
)

_BATCH_SIZE = 45

_MOMENTUM_SYMBOL = re.compile(
    r"(MOMOMENTUM|MOMENTUM|MOMENT|MOM\d|MOMUM|MTUM|ALPHA|SMALLCAP|MIDCAP|LOWVOL|QUALITY|VALUE)",
    re.IGNORECASE,
)


def _api_key() -> str | None:
    return (
        os.getenv("PORTFOLIO_OPENAI_API_KEY")
        or os.getenv("OPENAI_API_KEY")
        or os.getenv("API_KEY")
        or ""
    ).strip() or None


def _model() -> str:
    return (os.getenv("SECTOR_LLM_MODEL") or os.getenv("PORTFOLIO_LLM_MODEL") or "gpt-4o-mini").strip()


def llm_available() -> bool:
    if os.getenv("SECTOR_LLM_ENABLED", "true").lower() in ("0", "false", "no"):
        return False
    return _api_key() is not None


def _needs_llm_sector(holding: dict[str, Any]) -> bool:
    if has_symbol_sector_override(holding.get("symbol") or ""):
        return False
    sector = (holding.get("sector") or "").strip()
    if not sector:
        return True
    if sector != "ETF":
        return False
    symbol = (holding.get("symbol") or "").upper()
    if _MOMENTUM_SYMBOL.search(symbol):
        return True
    if holding.get("broker") == "groww" and (
        symbol.endswith("BEES") or "ETF" in symbol or _MOMENTUM_SYMBOL.search(symbol)
    ):
        return True
    return False


def _holding_row(holding: dict[str, Any]) -> dict[str, str]:
    return {
        "symbol": (holding.get("symbol") or "").upper(),
        "exchange": (holding.get("exchange") or "NSE").upper(),
        "asset_class": holding.get("asset_class") or "equity",
        "broker": holding.get("broker") or "",
        "yahoo_sector": holding.get("yahoo_sector") or "",
        "industry": holding.get("industry") or "",
    }


def _normalize_label(label: str | None, symbol: str, exchange: str) -> str | None:
    if not label:
        return None
    cleaned = label.strip()
    if cleaned not in ALLOWED_SECTORS:
        mapping = {
            "Gold": "Metals",
            "Silver": "Metals",
            "Copper": "Metals",
            "Quantum": "Newedge Tech",
            "Data Centers": "Newedge Tech",
            "AI": "Newedge Tech",
            "Technology": "IT",
            "Financials": "Financial Services",
            "Industrials": "Manufacturing",
            "Energy": "Oil & Gas",
            "Utilities": "Power",
            "Logistics": "Manufacturing",
            "Transportation": "Manufacturing",
            "Infrastructure": "Manufacturing",
        }
        cleaned = mapping.get(cleaned, cleaned)
    if cleaned not in ALLOWED_SECTORS:
        return None
    return _club_sector_label(cleaned, symbol, sector="", industry="")


def _call_openai_batch(rows: list[dict[str, str]]) -> dict[tuple[str, str], str]:
    api_key = _api_key()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not configured for sector LLM")

    allowed = ", ".join(ALLOWED_SECTORS)
    system = (
        "You classify Indian and US equity/ETF holdings into exactly one sector label for a portfolio dashboard. "
        "Respond with valid JSON only. "
        f"Use ONLY these labels: {allowed}. "
        "Rules: "
        "Indian NSE/BSE stocks — pick the company's primary business sector (not ticker words). "
        "Thematic ETFs (BEES, momentum, sector ETFs) — use Momentum, IT, Banking, Metals, Newedge Tech, etc., "
        "not generic ETF when a thematic label fits. "
        "Broad market index ETFs (Nifty 50, Junior Bees, Nifty BeES) may be ETF. "
        "Mutual funds in demat: Mutual fund. "
        "US listings: same taxonomy (Metals for gold/copper ETFs, Newedge Tech for AI/quantum/data-center themes). "
        "Never invent labels outside the list."
    )
    user = {
        "holdings": rows,
        "output_schema": {
            "classifications": [
                {"symbol": "SYMBOL", "exchange": "NSE|BSE|US", "sector": "one allowed label"}
            ]
        },
    }
    body = json.dumps(
        {
            "model": _model(),
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(user)},
            ],
        }
    ).encode()

    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            payload = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise RuntimeError(f"Sector LLM API error: {detail[:500]}") from exc

    text = (payload.get("choices") or [{}])[0].get("message", {}).get("content") or "{}"
    parsed = json.loads(text)
    rows = parsed.get("classifications")
    if not rows and isinstance(parsed.get("holdings"), list):
        rows = parsed["holdings"]
    out: dict[tuple[str, str], str] = {}
    for row in rows or []:
        sym = (row.get("symbol") or "").upper()
        ex = (row.get("exchange") or "NSE").upper()
        label = row.get("sector") or row.get("classification")
        sector = _normalize_label(label, sym, ex)
        if sym and sector:
            out[(sym, ex)] = sector
    return out


def classify_holdings_llm(holdings: list[dict[str, Any]], *, force: bool = False) -> dict[str, Any]:
    """
    Fill missing (and generic ETF) sectors via LLM. Returns stats dict.
    """
    if not llm_available():
        return {"applied": 0, "skipped": "llm_disabled"}

    need: list[dict[str, Any]] = []
    for holding in holdings:
        if force or _needs_llm_sector(holding):
            need.append(holding)

    if not need:
        return {"applied": 0, "requested": 0}

    keys = [(h.get("symbol") or "", h.get("exchange")) for h in need]
    cached = {} if force else sector_llm_cache.get_many(keys)
    still_need = []
    for h in need:
        sym, ex = sector_llm_cache.cache_key(h.get("symbol") or "", h.get("exchange"))
        if (sym, ex) in cached:
            h["sector"] = cached[(sym, ex)]
            h["sector_source"] = "llm_cache"
        else:
            still_need.append(h)

    classified = 0
    for offset in range(0, len(still_need), _BATCH_SIZE):
        batch = still_need[offset : offset + _BATCH_SIZE]
        rows = [_holding_row(h) for h in batch]
        try:
            result = _call_openai_batch(rows)
        except Exception as exc:
            logger.warning("Sector LLM batch failed: %s", exc)
            continue
        sector_llm_cache.put_sectors(result)
        for h in batch:
            sym, ex = sector_llm_cache.cache_key(h.get("symbol") or "", h.get("exchange"))
            sector = result.get((sym, ex))
            if sector:
                h["sector"] = sector
                h["sector_source"] = "llm"
                classified += 1

    return {
        "applied": classified + len(cached),
        "requested": len(need),
        "from_cache": len(cached),
        "llm_batches": (len(still_need) + _BATCH_SIZE - 1) // _BATCH_SIZE if still_need else 0,
    }


def apply_cached_sectors(holdings: list[dict[str, Any]]) -> int:
    """Apply SQLite LLM sector cache only (no API call)."""
    keys = [(h.get("symbol") or "", h.get("exchange")) for h in holdings]
    cached = sector_llm_cache.get_many(keys)
    applied = 0
    for h in holdings:
        sym, ex = sector_llm_cache.cache_key(h.get("symbol") or "", h.get("exchange"))
        if (sym, ex) in cached and _needs_llm_sector(h):
            h["sector"] = cached[(sym, ex)]
            h["sector_source"] = "llm_cache"
            applied += 1
    return applied
