"""LLM 2–3 line buy thesis for Strong buy (B+) holdings."""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.request
from typing import Any

from modules.portfolio.db import buy_thesis_cache
logger = logging.getLogger(__name__)

_BATCH_SIZE = 25
_MAX_THESIS_CHARS = 420


def _api_key() -> str | None:
    return (
        os.getenv("PORTFOLIO_OPENAI_API_KEY")
        or os.getenv("OPENAI_API_KEY")
        or os.getenv("API_KEY")
        or ""
    ).strip() or None


def _model() -> str:
    return (
        os.getenv("BUY_THESIS_LLM_MODEL")
        or os.getenv("PORTFOLIO_LLM_MODEL")
        or os.getenv("LLM_MODEL")
        or "gpt-4o-mini"
    ).strip()


def llm_available() -> bool:
    if os.getenv("BUY_THESIS_LLM_ENABLED", "false").lower() not in ("1", "true", "yes"):
        return False
    return _api_key() is not None


def is_strong_buy(holding: dict[str, Any]) -> bool:
    if holding.get("rating_slug") == "strong-buy":
        return True
    return (holding.get("rating_label") or "").strip() == "Strong buy"


def _holding_payload(holding: dict[str, Any]) -> dict[str, Any]:
    return {
        "symbol": (holding.get("symbol") or "").upper(),
        "exchange": (holding.get("exchange") or "NSE").upper(),
        "sector": holding.get("sector") or "",
        "market_cap": holding.get("market_cap") or "",
        "pe_ratio": holding.get("pe_ratio"),
        "upside_pct": holding.get("upside_pct"),
        "pct_from_52w_high": holding.get("pct_from_52w_high"),
        "return_1y_pct": holding.get("return_1y_pct"),
        "rating_reasons": holding.get("rating_reasons") or [],
        "industry": holding.get("industry") or "",
    }


def _trim_thesis(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    if len(cleaned) > _MAX_THESIS_CHARS:
        cleaned = cleaned[: _MAX_THESIS_CHARS - 1].rstrip() + "…"
    return cleaned


def _call_openai_batch(rows: list[dict[str, Any]]) -> dict[tuple[str, str], str]:
    api_key = _api_key()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not configured for buy thesis LLM")

    system = (
        "You write concise buy theses for a personal Indian equity portfolio (3+ year horizon, growth tilt). "
        "Respond with valid JSON only. "
        "For each Strong buy (B+) holding, write exactly 2–3 short sentences explaining why to buy or add now. "
        "Use only facts from the holding payload (sector, upside, 52-week position, P/E, rating reasons). "
        "Do not invent analyst names, price targets, or events. "
        "Plain English, no bullet points, no ticker spam."
    )
    user = {
        "holdings": rows,
        "output_schema": {
            "theses": [
                {"symbol": "SYMBOL", "exchange": "NSE", "thesis": "2-3 sentences"}
            ]
        },
    }
    body = json.dumps(
        {
            "model": _model(),
            "temperature": 0.35,
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
        raise RuntimeError(f"Buy thesis LLM API error: {detail[:500]}") from exc

    text = (payload.get("choices") or [{}])[0].get("message", {}).get("content") or "{}"
    parsed = json.loads(text)
    items = parsed.get("theses") or parsed.get("holdings") or parsed.get("classifications") or []
    out: dict[tuple[str, str], str] = {}
    for row in items:
        sym = (row.get("symbol") or "").upper()
        ex = (row.get("exchange") or "NSE").upper()
        if "NSE" in ex:
            ex = "NSE"
        elif "BSE" in ex:
            ex = "BSE"
        thesis = _trim_thesis(row.get("thesis") or row.get("buy_thesis") or "")
        if sym and thesis:
            out[(sym, ex)] = thesis
    return out


def apply_cached_buy_theses(holdings: list[dict[str, Any]]) -> int:
    """Apply SQLite cache to B+ rows (no API)."""
    targets = [h for h in holdings if is_strong_buy(h)]
    if not targets:
        return 0
    keys = [(h.get("symbol") or "", h.get("exchange")) for h in targets]
    cached = buy_thesis_cache.get_many(keys)
    applied = 0
    for h in targets:
        sym, ex = buy_thesis_cache.cache_key(h.get("symbol") or "", h.get("exchange"))
        thesis = cached.get((sym, ex))
        if thesis:
            h["buy_thesis"] = thesis
            applied += 1
    return applied


def generate_buy_theses_llm(holdings: list[dict[str, Any]], *, force: bool = False) -> dict[str, Any]:
    """Generate buy_thesis for all Strong buy holdings; cache in SQLite."""
    if not llm_available():
        return {"applied": 0, "skipped": "llm_disabled"}

    need = [h for h in holdings if is_strong_buy(h) and h.get("asset_class") != "mf"]
    if not need:
        return {"applied": 0, "requested": 0}

    keys = [(h.get("symbol") or "", h.get("exchange")) for h in need]
    cached = {} if force else buy_thesis_cache.get_many(keys)
    still_need: list[dict[str, Any]] = []
    for h in need:
        sym, ex = buy_thesis_cache.cache_key(h.get("symbol") or "", h.get("exchange"))
        if (sym, ex) in cached:
            h["buy_thesis"] = cached[(sym, ex)]
        else:
            still_need.append(h)

    classified = 0
    for offset in range(0, len(still_need), _BATCH_SIZE):
        batch = still_need[offset : offset + _BATCH_SIZE]
        payloads = [_holding_payload(h) for h in batch]
        try:
            result = _call_openai_batch(payloads)
        except Exception as exc:
            logger.warning("Buy thesis LLM batch failed: %s", exc)
            continue
        buy_thesis_cache.put_theses(result)
        for h in batch:
            sym, ex = buy_thesis_cache.cache_key(h.get("symbol") or "", h.get("exchange"))
            thesis = result.get((sym, ex))
            if thesis:
                h["buy_thesis"] = thesis
                classified += 1

    return {
        "applied": classified + len(cached),
        "requested": len(need),
        "from_cache": len(cached),
        "batches": (len(still_need) + _BATCH_SIZE - 1) // _BATCH_SIZE if still_need else 0,
    }
