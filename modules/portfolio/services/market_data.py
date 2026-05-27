"""Fetch and cache fundamental metrics for NSE/BSE holdings."""

from __future__ import annotations

import logging
import os
import re
import sys
import time
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import yfinance as yf

from modules.portfolio.services.analyst_rating import compute_rating

# Suppress yfinance 404 spam for illiquid / unmapped Indian tickers.
logging.getLogger("yfinance").setLevel(logging.CRITICAL)
logger = logging.getLogger(__name__)

# In-memory cache: symbol -> (timestamp, metrics dict)
_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_CACHE_TTL_SECONDS = 6 * 60 * 60  # 6 hours

# NSE tradingsymbol → extra Yahoo roots (Zerodha often omits "LTD").
_INDIAN_YAHOO_SYMBOL_ALIASES: dict[str, list[str]] = {
    "GMDC": ["GMDCLTD"],
}

# Zerodha series suffixes appended to tradingsymbol (e.g. BIRLACABLE-BE).
_SERIES_SUFFIX = re.compile(
    r"-(?:BE|BL|BZ|EQ|IV|RR|ST|N1|N2|N3|N4|N5|N6|N7|N8|N9|B1|B2|GS|GB|MF|ETF)$",
    re.IGNORECASE,
)

_ETF_SYMBOL_HINTS = re.compile(r"(?:BEES|SETF|-ETF)$", re.IGNORECASE)

# Known sector/thematic ETFs — override generic "ETF" label from Yahoo.
_SYMBOL_SECTOR_OVERRIDES: dict[str, str] = {
    "ITBEES": "IT",
    "FMCGIETF": "FMCG",
    "PHARMABEES": "ETF",
    "BANKBEES": "Banking",
    "PSUBNKBEES": "Banking",
    "EMULTIMQ": "Momentum",
    "MOMIDMTM": "Momentum",
    "MOMENTUM50": "Momentum",
    "MOMENTUM": "Momentum",
    "MOMOMENTUM": "Momentum",
    "MOM100": "Momentum",
    "MOM30": "Momentum",
    "MOTILALMTUM": "Momentum",
    "ICICIMOM30": "Momentum",
    "MIDMOMENTUM": "Momentum",
    "NIFTYMOMENTUM": "Momentum",
    "MAFANG": "Newedge Tech",
    "MON100": "Newedge Tech",
    "MODEFENSE": "Defense",
    "NIBE": "Defense",
    "ALPHA": "Momentum",
    "SMALLCAP": "ETF",
    "JUNIORBEES": "ETF",
    "NIFTYBEES": "ETF",
    # Rail / logistics — group with IRCTC (Consumer)
    "CONCOR": "Consumer",
    "DELHIVERY": "Consumer",
    "IRCTC": "Consumer",
    # Sarwa / US (symbol-level; see _club_sector_label for metals / newedge tech groups)
    "GLDM": "Metals",
    "BTC": "Crypto",
    "COPP": "Metals",
    "DTCR": "Newedge Tech",
    "ARTY": "Defense",
    "AIQ": "Newedge Tech",
    "QUBT": "Newedge Tech",
}

# Broad / thematic ETFs — hold stocks across cap sizes (not Large/Mid/Small).
_MULTI_CAP_ETF_SYMBOLS = frozenset(
    {
        "ITBEES",
        "FMCGIETF",
        "MOMENTUM50",
        "JUNIORBEES",
        "PHARMABEES",
        "MOMIDMTM",
        "EMULTIMQ",
        "MOMENTUM",
        "MOMOMENTUM",
        "MOM100",
        "MOM30",
        "MOTILALMTUM",
        "ICICIMOM30",
        "MIDMOMENTUM",
        "NIFTYMOMENTUM",
        "BANKBEES",
    }
)

# Single-cap index ETFs (Yahoo AUM or fixed bucket).
_SYMBOL_CAP_OVERRIDES: dict[str, str] = {
    "NIFTYBEES": "Large",
    "PSUBNKBEES": "Mid",
    "SMALLCAP": "Small",
    "ALPHA": "Mid",
    "CONCOR": "Mid",
    "DELHIVERY": "Mid",
    "IRCTC": "Mid",
}

# Thematic groups — applied after Yahoo mapping (and for future Sarwa tickers).
_NEWEDGE_TECH_SYMBOLS = frozenset({"AIQ", "DTCR", "QUBT"})
_METALS_SYMBOLS = frozenset(
    {
        "GLDM",
        "GLD",
        "IAU",
        "SGOL",
        "OUNZ",
        "SLV",
        "SIVR",
        "SIL",
        "PSLV",
        "COPP",
        "CPER",
        "COPX",
        "PPLT",
        "PALL",
        "GDX",
        "GDXJ",
        "SILJ",
    }
)

_US_EXCHANGES = frozenset({"US", "NASDAQ", "NYSE", "AMEX", "ARCA", "BATS"})

# Sarwa / US portfolio symbols → Yahoo tickers (try in order)
_US_YAHOO_TICKERS: dict[str, list[str]] = {
    "BTC": ["BTC-USD"],
    "COPP": ["CPER", "COPX", "COPP"],
    "GLDM": ["GLDM", "IAU"],
    "ARTY": ["ARTY"],
    "DTCR": ["DTCR"],
    "MTUM": ["MTUM"],
    "PLTR": ["PLTR"],
    "AIQ": ["AIQ"],
    "QUBT": ["QUBT"],
    "SVM": ["SVM"],
}


@contextmanager
def _quiet_yfinance():
    """No-op: yfinance logger is CRITICAL; avoid global stderr swap (breaks parallel enrich)."""
    yield


def _technical_on_load() -> bool:
    return os.getenv("PORTFOLIO_TECHNICAL_ON_LOAD", "false").lower() in ("1", "true", "yes")


def _sector_llm_on_enrich() -> bool:
    return os.getenv("SECTOR_LLM_ON_ENRICH", "false").lower() in ("1", "true", "yes")


def _buy_thesis_llm_on_enrich() -> bool:
    return os.getenv("BUY_THESIS_LLM_ON_ENRICH", "false").lower() in ("1", "true", "yes")


def normalize_symbol(symbol: str) -> str:
    """Strip Zerodha series suffixes for Yahoo lookup."""
    return _SERIES_SUFFIX.sub("", symbol.strip().upper())


def is_us_exchange(exchange: str | None) -> bool:
    return (exchange or "").upper() in _US_EXCHANGES


def _yahoo_ticker_candidates(symbol: str, exchange: str | None) -> list[str]:
    """Build Yahoo Finance ticker candidates, most likely first."""
    base = normalize_symbol(symbol)
    if not base:
        return []

    ex = (exchange or "NSE").upper()
    candidates: list[str] = []

    if is_us_exchange(ex):
        candidates.extend(_US_YAHOO_TICKERS.get(base, [base]))
    else:
        roots = [base]
        roots.extend(_INDIAN_YAHOO_SYMBOL_ALIASES.get(base, []))
        for root in roots:
            if ex == "BSE":
                candidates.extend([f"{root}.BO", f"{root}.NS"])
            else:
                candidates.extend([f"{root}.NS", f"{root}.BO"])

    seen: set[str] = set()
    ordered: list[str] = []
    for ticker in candidates:
        if ticker not in seen:
            seen.add(ticker)
            ordered.append(ticker)
    return ordered


def classify_market_cap_usd(market_cap_usd: float | None) -> str | None:
    """US large/mid/small by market cap in USD."""
    if not market_cap_usd or market_cap_usd <= 0:
        return None
    billions = market_cap_usd / 1e9
    if billions >= 10:
        return "Large"
    if billions >= 2:
        return "Mid"
    return "Small"


def metric_last_price(holding: dict[str, Any]) -> float | None:
    """Price for Yahoo % metrics — USD for US listings, INR for India."""
    if is_us_exchange(holding.get("exchange")):
        usd = holding.get("last_price_usd")
        if usd is not None:
            try:
                return float(usd)
            except (TypeError, ValueError):
                pass
    price = holding.get("last_price")
    try:
        return float(price) if price is not None else None
    except (TypeError, ValueError):
        return None


def classify_market_cap(market_cap_inr: float | None) -> str | None:
    """Legacy INR band classifier; prefer classify_indian_equity_cap (AMFI ranks)."""
    if not market_cap_inr or market_cap_inr <= 0:
        return None
    try:
        from modules.portfolio.services.amfi_cap import classify_indian_equity_cap

        return classify_indian_equity_cap("", market_cap_inr=market_cap_inr)
    except Exception:
        return None


def cap_bucket_reference() -> dict[str, str]:
    """Human-readable cap bands for UI / docs."""
    try:
        from modules.portfolio.services.amfi_cap import cap_bucket_reference as amfi_ref

        return amfi_ref()
    except Exception:
        return {
            "Large": "AMFI rank 1–100",
            "Mid": "AMFI rank 101–250",
            "Small": "AMFI rank 251+",
        }


def _infer_market_cap_inr(info: dict[str, Any]) -> float | None:
    """Fallback when Yahoo omits marketCap but has price × shares."""
    mcap = info.get("marketCap")
    if mcap and float(mcap) > 0:
        return float(mcap)
    price = info.get("regularMarketPrice") or info.get("previousClose")
    shares = info.get("sharesOutstanding")
    try:
        if price and shares and float(price) > 0 and float(shares) > 0:
            return float(price) * float(shares)
    except (TypeError, ValueError):
        pass
    return None


def _yahoo_info_usable_for_equity(info: dict[str, Any]) -> bool:
    """Skip wrong Yahoo instruments (e.g. GMDC.NS → mutual fund)."""
    qt = (info.get("quoteType") or "").upper()
    if qt in ("MUTUALFUND", "NONE", ""):
        return bool(info.get("marketCap") or info.get("trailingPE"))
    if qt == "ETF":
        return bool(info.get("marketCap") or info.get("totalAssets"))
    return bool(
        info.get("regularMarketPrice")
        or info.get("marketCap")
        or info.get("trailingPE")
        or info.get("sector")
        or info.get("industry")
    )


def cap_override_for_symbol(symbol: str) -> str | None:
    """Fixed cap bucket for known ETFs / symbols (see overrides and multi-cap set)."""
    base = normalize_symbol(symbol)
    if base in _MULTI_CAP_ETF_SYMBOLS:
        return "Multi-cap"
    return _SYMBOL_CAP_OVERRIDES.get(base)


def _resolve_indian_market_cap_inr(symbol: str, exchange: str | None, yahoo_mcap: float | None) -> float | None:
    """Prefer NSE India when Yahoo is missing or unreliable for Indian listings."""
    if is_us_exchange(exchange):
        return yahoo_mcap
    try:
        from modules.portfolio.services.nse_quote import nse_market_cap_inr

        nse_mcap = nse_market_cap_inr(symbol, exchange)
    except Exception as exc:
        logger.debug("NSE market cap skipped for %s: %s", symbol, exc)
        return yahoo_mcap
    if nse_mcap and nse_mcap > 0:
        return nse_mcap
    return yahoo_mcap


def has_symbol_sector_override(symbol: str) -> bool:
    return normalize_symbol(symbol) in _SYMBOL_SECTOR_OVERRIDES


def sector_override_for_symbol(symbol: str) -> str | None:
    """Thematic sector label for known ETFs."""
    label = _SYMBOL_SECTOR_OVERRIDES.get(normalize_symbol(symbol))
    if not label:
        return None
    return _club_sector_label(label, symbol, sector="", industry="")


def _sector_from_reference(symbol: str, exchange: str | None) -> str | None:
    """Static sector lookup (LLM / Yahoo / seed file) when Yahoo live fetch has no sector."""
    from modules.portfolio.db import sector_llm_cache

    sector = sector_llm_cache.get_sector(symbol, exchange)
    if sector:
        return sector
    return sector_override_for_symbol(symbol)


def _remember_sector_reference(symbol: str, exchange: str | None, sector: str | None, *, source: str) -> None:
    if not sector:
        return
    try:
        from modules.portfolio.db import sector_llm_cache

        sector_llm_cache.remember_sector(symbol, exchange, sector, source=source)
    except Exception as exc:
        logger.debug("Sector reference save skipped for %s: %s", symbol, exc)


def apply_symbol_metric_overrides(metrics: dict[str, Any], symbol: str) -> None:
    """Apply symbol-level sector and cap overrides (also when metrics come from cache)."""
    cap = cap_override_for_symbol(symbol)
    if cap:
        metrics["market_cap"] = cap
    sector = sector_override_for_symbol(symbol)
    if sector:
        metrics["sector"] = sector
        _remember_sector_reference(symbol, metrics.get("exchange") or "NSE", sector, source="seed")


def apply_holdings_metric_overrides(holdings: list[dict[str, Any]]) -> None:
    """Fast path: SQLite sector cache + symbol tables only (no Yahoo / LLM)."""
    try:
        from modules.portfolio.services.sector_llm import apply_cached_sectors

        apply_cached_sectors(holdings)
    except Exception:
        pass
    try:
        from modules.portfolio.services.buy_thesis_llm import apply_cached_buy_theses

        apply_cached_buy_theses(holdings)
    except Exception:
        pass
    for holding in holdings:
        sym = holding.get("symbol") or ""
        apply_symbol_metric_overrides(holding, sym)


def _club_sector_label(
    label: str | None,
    symbol: str,
    *,
    sector: str = "",
    industry: str = "",
) -> str | None:
    """Club metals and new-edge tech themes into portfolio sector groups."""
    base = normalize_symbol(symbol)
    if base in _NEWEDGE_TECH_SYMBOLS:
        return "Newedge Tech"
    if base in _METALS_SYMBOLS:
        return "Metals"

    blob = f"{label or ''} {sector} {industry}".lower()
    if any(
        k in blob
        for k in (
            "gold",
            "silver",
            "copper",
            "platinum",
            "palladium",
            "precious metal",
            "commodity metal",
        )
    ):
        return "Metals"
    if label and label.strip().lower() in ("gold", "copper", "silver", "platinum", "palladium"):
        return "Metals"

    if label in ("Quantum", "Data Centers", "AI / Tech ETF", "AI / Tech", "Newedge Tech"):
        return "Newedge Tech"

    return label


def classify_sector(
    sector: str | None,
    industry: str | None,
    quote_type: str | None,
    symbol: str,
) -> str | None:
    """Map Yahoo sector/industry to a short Indian-market label."""
    base = normalize_symbol(symbol)
    sec = (sector or "").lower()
    ind = (industry or "").lower()

    def finish(label: str | None) -> str | None:
        return _club_sector_label(label, symbol, sector=sec, industry=ind)

    override = _SYMBOL_SECTOR_OVERRIDES.get(base)
    if override:
        return finish(override)

    if "defense" in base.lower() or "defence" in base.lower():
        return finish("Defense")

    qt = (quote_type or "").upper()
    blob = f"{sec} {ind}"

    if qt == "ETF" or _ETF_SYMBOL_HINTS.search(base) or "exchange traded" in blob:
        return finish("ETF")
    if symbol.upper().endswith("-MF") or symbol.upper().endswith("-ETF"):
        return finish("ETF")

    if any(k in ind for k in ("defense", "defence", "aerospace")):
        return finish("Defense")

    if "bank" in ind and "credit" not in ind:
        return finish("Banking")

    if any(k in blob for k in ("credit service", "nbfc", "consumer finance", "mortgage finance", "leasing")):
        return finish("NBFC")

    if any(k in blob for k in ("drug", "pharma", "biotech", "vaccine", "medical -")):
        return finish("Pharma")

    if any(
        k in blob
        for k in (
            "information technology",
            "software",
            "it services",
            "internet content",
            "semiconductor",
            "electronic gaming",
        )
    ) or sec == "technology":
        return finish("IT")

    if any(k in blob for k in ("chemical", "specialty chemical", "agrochemical", "fertilizer")):
        return finish("Chemical")

    if any(
        k in blob
        for k in (
            "food",
            "beverage",
            "household",
            "personal care",
            "tobacco",
            "confection",
            "packaged food",
            "grocery",
            "fmcg",
        )
    ):
        return finish("FMCG")

    if any(k in blob for k in ("auto", "automobile", "motor vehicle", "auto parts", "truck")):
        return finish("Auto")

    if any(k in blob for k in ("oil", "gas", "refining", "exploration", "coal")):
        return finish("Oil & Gas")

    if any(k in blob for k in ("steel", "iron", "aluminum", "copper", "mining", "metal")):
        return finish("Metals")

    if any(k in blob for k in ("real estate", "reit")):
        return finish("Real Estate")

    if any(k in blob for k in ("utility", "utilities", "power", "electric", "renewable")):
        return finish("Power")

    if any(k in blob for k in ("telecom", "wireless", "communication")):
        return finish("Telecom")

    if any(k in blob for k in ("insurance", "life insurance", "health insurance")):
        return finish("Insurance")

    if any(k in blob for k in ("retail", "department store", "specialty retail")):
        return finish("Retail")

    if any(
        k in blob
        for k in (
            "capital goods",
            "industrial",
            "machinery",
            "engineering",
            "construction",
            "infrastructure",
            "building material",
            "manufacturing",
        )
    ) or sec in ("industrials", "basic materials"):
        return finish("Manufacturing")

    # Yahoo GICS sector names → short Indian dashboard labels
    _yahoo_sector_short: dict[str, str] = {
        "consumer cyclical": "Consumer",
        "consumer defensive": "FMCG",
        "communication services": "Telecom",
        "financial services": "Financial Services",
        "healthcare": "Healthcare",
        "technology": "IT",
        "utilities": "Power",
        "energy": "Oil & Gas",
        "basic materials": "Metals",
        "industrials": "Manufacturing",
        "real estate": "Real Estate",
    }
    if sec in _yahoo_sector_short:
        return finish(_yahoo_sector_short[sec])

    if sector:
        return finish(sector.split(" - ")[0].strip())
    if industry:
        return finish(industry.split(" - ")[0].strip())
    return finish(None)


def _pct_from_52w_high(last_price: float | None, high_52w: float | None) -> float | None:
    """Return % distance from 52-week high (negative = below high)."""
    if not last_price or not high_52w or high_52w <= 0:
        return None
    return round(((last_price - high_52w) / high_52w) * 100, 2)


def _pct_upside(last_price: float | None, target_price: float | None) -> float | None:
    """Return % upside to analyst target price."""
    if not last_price or not target_price or last_price <= 0:
        return None
    return round(((target_price - last_price) / last_price) * 100, 2)


def _recovery_upside(pct_from_52w_high: float | None) -> float | None:
    """Upside to 52-week high when no analyst target (ETFs / thin coverage)."""
    if pct_from_52w_high is None or pct_from_52w_high >= 0:
        return 0.0 if pct_from_52w_high is not None else None
    return round(-pct_from_52w_high, 2)


def resolve_yahoo_ticker(symbol: str, exchange: str | None) -> str | None:
    """Return the first Yahoo Finance ticker that resolves for a holding."""
    for ticker in _yahoo_ticker_candidates(symbol, exchange):
        try:
            with _quiet_yfinance():
                info = yf.Ticker(ticker).info or {}
        except Exception:
            continue

        if _yahoo_info_usable_for_equity(info) and (
            info.get("regularMarketPrice") or info.get("symbol") or info.get("shortName")
        ):
            return ticker

    candidates = _yahoo_ticker_candidates(symbol, exchange)
    return candidates[0] if candidates else None


def _fetch_yahoo_metrics(symbol: str, exchange: str | None) -> dict[str, Any]:
    """Load PE, 52w high, market cap, and sector from Yahoo Finance."""
    for ticker in _yahoo_ticker_candidates(symbol, exchange):
        try:
            with _quiet_yfinance():
                info = yf.Ticker(ticker).info or {}
        except Exception:
            continue

        if not _yahoo_info_usable_for_equity(info):
            continue

        if (
            info.get("regularMarketPrice")
            or info.get("marketCap")
            or info.get("trailingPE")
            or info.get("sector")
            or info.get("industry")
        ):
            qt = (info.get("quoteType") or "").upper()
            mcap = _infer_market_cap_inr(info)
            if not mcap and qt == "ETF":
                mcap = info.get("totalAssets")
            mcap = _resolve_indian_market_cap_inr(symbol, exchange, mcap)
            sector = classify_sector(
                info.get("sector"),
                info.get("industry"),
                info.get("quoteType"),
                symbol,
            )
            if sector:
                _remember_sector_reference(symbol, exchange, sector, source="yahoo")
            return {
                "pe_ratio": info.get("trailingPE") or info.get("forwardPE"),
                "roce": info.get("returnOnCapital") or info.get("returnOnEquity"),
                "debt_to_equity": _normalize_debt_to_equity(info.get("debtToEquity")),
                "high_52w": info.get("fiftyTwoWeekHigh"),
                "market_cap_inr": mcap,
                "market_cap_usd": mcap if is_us_exchange(exchange) else None,
                "target_price": info.get("targetMeanPrice") or info.get("targetMedianPrice"),
                "recommendation_key": info.get("recommendationKey"),
                "recommendation_mean": info.get("recommendationMean"),
                "analyst_count": info.get("numberOfAnalystOpinions"),
                "sector": sector or _sector_from_reference(symbol, exchange),
            }

    mcap = _resolve_indian_market_cap_inr(symbol, exchange, None)
    return {
        "pe_ratio": None,
        "roce": None,
        "debt_to_equity": None,
        "high_52w": None,
        "market_cap_inr": mcap,
        "target_price": None,
        "recommendation_key": None,
        "recommendation_mean": None,
        "analyst_count": None,
        "sector": _sector_from_reference(symbol, exchange),
    }


def _cached_base_metrics_usable(metrics: dict[str, Any], exchange: str | None) -> bool:
    """True when Yahoo fundamentals were fetched — not an empty fallback blob."""
    if metrics.get("high_52w") or metrics.get("target_price") or metrics.get("recommendation_key"):
        return True
    if metrics.get("pe_ratio") is not None and metrics.get("sector"):
        return True
    return bool(is_us_exchange(exchange) and metrics.get("market_cap"))


def invalidate_stale_metrics_cache() -> int:
    """Drop in-memory Yahoo metric entries that have no 52W / analyst data (failed fetches)."""
    drop = [
        key
        for key, (_ts, metrics) in _CACHE.items()
        if not _cached_base_metrics_usable(metrics, None)
    ]
    for key in drop:
        _CACHE.pop(key, None)
    return len(drop)


def clear_metrics_cache_for_symbol(symbol: str, exchange: str | None) -> None:
    _CACHE.pop(f"{symbol}:{exchange or 'NSE'}", None)


def holdings_need_metrics_refresh(holdings: list[dict[str, Any]]) -> bool:
    """True when most equity rows are missing 52W / signal (poisoned or never enriched)."""
    equity = [
        h
        for h in holdings
        if (h.get("asset_class") or "equity") != "mf" and h.get("symbol")
    ]
    if len(equity) < 2:
        return False
    missing = sum(
        1
        for h in equity
        if h.get("pct_from_52w_high") is None and not h.get("rating_label")
    )
    return (missing / len(equity)) > 0.25


def get_stock_metrics(
    symbol: str,
    exchange: str | None,
    last_price: float | None,
    *,
    technical: bool | None = None,
) -> dict[str, Any]:
    """Return cached fundamental metrics for a single symbol."""
    cache_key = f"{symbol}:{exchange or 'NSE'}"
    now = time.time()

    cached = _CACHE.get(cache_key)
    if cached and (now - cached[0]) < _CACHE_TTL_SECONDS:
        if _cached_base_metrics_usable(cached[1], exchange):
            metrics = cached[1].copy()
        else:
            _CACHE.pop(cache_key, None)
            cached = None

    if not cached:
        yahoo = _fetch_yahoo_metrics(symbol, exchange)
        if is_us_exchange(exchange):
            cap = classify_market_cap_usd(yahoo.get("market_cap_usd"))
            if not cap and _ETF_SYMBOL_HINTS.search(normalize_symbol(symbol)):
                cap = "ETF"
        else:
            cap_inr = yahoo.get("market_cap_inr")
            override = cap_override_for_symbol(symbol)
            if override == "Multi-cap":
                cap = "Multi-cap"
            elif override:
                cap = override
            else:
                try:
                    from modules.portfolio.services.amfi_cap import classify_indian_equity_cap

                    cap = classify_indian_equity_cap(
                        symbol,
                        market_cap_inr=cap_inr,
                    )
                except Exception as exc:
                    logger.debug("AMFI cap lookup failed for %s: %s", symbol, exc)
                    cap = None

        metrics = {
            "pe_ratio": _safe_round(yahoo.get("pe_ratio")),
            "roce": _safe_round(yahoo.get("roce")),
            "debt_to_equity": yahoo.get("debt_to_equity"),
            "high_52w": yahoo.get("high_52w"),
            "market_cap": cap,
            "sector": yahoo.get("sector"),
            "target_price": _safe_round(yahoo.get("target_price")),
            "recommendation_key": yahoo.get("recommendation_key"),
            "recommendation_mean": _safe_round(yahoo.get("recommendation_mean")),
            "analyst_count": yahoo.get("analyst_count"),
        }
        if _cached_base_metrics_usable(metrics, exchange):
            _CACHE[cache_key] = (now, metrics)

    metrics["pct_from_52w_high"] = _pct_from_52w_high(last_price, metrics.get("high_52w"))
    metrics["upside_pct"] = _pct_upside(last_price, metrics.get("target_price"))

    rating = compute_rating(
        recommendation_key=metrics.get("recommendation_key"),
        recommendation_mean=metrics.get("recommendation_mean"),
        upside_pct=metrics.get("upside_pct"),
        target_price=metrics.get("target_price"),
        last_price=last_price,
        analyst_count=metrics.get("analyst_count"),
    )
    metrics["rating_label"] = rating.get("label")
    metrics["rating_slug"] = rating.get("slug")
    metrics["rating_source"] = rating.get("source")
    metrics["rating_reasons"] = rating.get("reasons", [])
    metrics["rating_rank"] = rating.get("rank")
    if technical if technical is not None else _technical_on_load():
        _attach_technical_metrics(metrics, symbol, exchange, last_price)
    _apply_price_based_signal(metrics, last_price=last_price, exchange=exchange)
    apply_symbol_metric_overrides(metrics, symbol)

    return metrics


def _apply_price_based_signal(
    metrics: dict[str, Any],
    *,
    last_price: float | None,
    exchange: str | None,
) -> None:
    """Fill upside + signal from 52W price action when Yahoo has no analyst coverage (common for US ETFs)."""
    if metrics.get("rating_label"):
        return
    pct = metrics.get("pct_from_52w_high")
    if pct is None:
        return

    if metrics.get("upside_pct") is None:
        metrics["upside_pct"] = _recovery_upside(pct)

    upside = metrics.get("upside_pct")
    if upside is None:
        return

    rating = compute_rating(
        upside_pct=upside,
        target_price=metrics.get("high_52w"),
        last_price=last_price,
    )
    if not rating.get("label"):
        return

    rating["source"] = "price_52w"
    rating["reasons"] = [
        "US listing / ETF: signal from price vs 52-week high (Yahoo has no analyst consensus).",
        f"Price is {pct:+.1f}% vs 52-week high; room to high ≈ {upside:.1f}%.",
    ]
    if is_us_exchange(exchange):
        rating["reasons"][0] = (
            "US listing / ETF: signal from price vs 52-week high (no analyst targets on Yahoo)."
        )

    metrics["rating_label"] = rating.get("label")
    metrics["rating_slug"] = rating.get("slug")
    metrics["rating_source"] = rating.get("source")
    metrics["rating_reasons"] = rating.get("reasons", [])
    metrics["rating_rank"] = rating.get("rank")


def _rsi(closes: list[float], period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    gains: list[float] = []
    losses: list[float] = []
    for index in range(-period, 0):
        delta = closes[index] - closes[index - 1]
        if delta >= 0:
            gains.append(delta)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(-delta)
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 1)


def _attach_technical_metrics(
    metrics: dict[str, Any],
    symbol: str,
    exchange: str | None,
    last_price: float | None,
) -> None:
    """RSI, 200-DMA distance, and price vs 52W range for the expander technical panel."""
    ticker = resolve_yahoo_ticker(symbol, exchange)
    if not ticker:
        return
    try:
        with _quiet_yfinance():
            frame = yf.Ticker(ticker).history(period="1y", interval="1d", auto_adjust=True)
    except Exception:
        return
    if frame is None or frame.empty:
        return

    closes = [float(v) for v in frame["Close"].tolist() if v == v]
    if not closes:
        return

    metrics["rsi_14"] = _rsi(closes)
    dma200_series = frame["Close"].rolling(window=200, min_periods=200).mean()
    dma200 = dma200_series.iloc[-1] if len(dma200_series) else None
    if dma200 == dma200 and last_price:
        metrics["dma_200"] = _safe_round(float(dma200), 2)
        metrics["pct_vs_dma200"] = round(((last_price - float(dma200)) / float(dma200)) * 100, 2)

    low_52w = float(frame["Close"].min())
    high_52w = metrics.get("high_52w") or float(frame["Close"].max())
    if last_price and high_52w > low_52w:
        metrics["pct_in_52w_range"] = round(
            ((last_price - low_52w) / (high_52w - low_52w)) * 100,
            1,
        )


def _normalize_debt_to_equity(value: Any) -> float | None:
    """Normalize Yahoo debtToEquity (often percent) to a ratio."""
    if value is None:
        return None
    try:
        val = float(value)
    except (TypeError, ValueError):
        return None
    return round(val / 100, 2) if val > 10 else round(val, 2)


def _safe_round(value: Any, digits: int = 2) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def enrich_holdings(
    holdings: list[dict],
    *,
    technical: bool | None = None,
    sector_llm: bool | None = None,
    buy_thesis_llm: bool | None = None,
) -> list[dict]:
    """Attach fundamental metrics (equity and MF) in parallel; technical optional."""
    from modules.portfolio.services.mf_metrics import get_mf_metrics

    equity_unique: dict[str, tuple[str, str | None, float | None]] = {}
    mf_unique: dict[str, float | None] = {}

    for holding in holdings:
        if holding.get("asset_class") == "mf":
            isin = (holding.get("isin") or holding.get("symbol") or "").strip().upper()
            if isin and isin not in mf_unique:
                mf_unique[isin] = holding.get("last_price")
            continue
        symbol = holding.get("symbol")
        if not symbol:
            continue
        exchange = holding.get("exchange")
        key = f"{symbol}:{exchange or 'NSE'}"
        price_for_metrics = metric_last_price(holding)
        if key not in equity_unique:
            equity_unique[key] = (symbol, exchange, price_for_metrics)

    equity_metrics: dict[str, dict[str, Any]] = {}
    mf_metrics: dict[str, dict[str, Any]] = {}

    use_technical = technical if technical is not None else _technical_on_load()

    def _load_equity(item: tuple[str, str, str | None, float | None]) -> tuple[str, dict[str, Any]]:
        key, symbol, exchange, last_price = item
        return key, get_stock_metrics(symbol, exchange, last_price, technical=use_technical)

    def _load_mf(item: tuple[str, float | None]) -> tuple[str, dict[str, Any]]:
        isin, last_price = item
        return isin, get_mf_metrics(isin, last_price)

    equity_work = [(key, sym, exch, lp) for key, (sym, exch, lp) in equity_unique.items()]
    mf_work = list(mf_unique.items())

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(_load_equity, row) for row in equity_work]
        futures.extend(pool.submit(_load_mf, row) for row in mf_work)
        for future in as_completed(futures):
            try:
                key, metrics = future.result()
            except Exception as exc:
                logger.warning("Metrics load failed: %s", exc)
                continue
            if key in mf_unique:
                mf_metrics[key] = metrics
            else:
                equity_metrics[key] = metrics

    retry_equity = [
        (key, sym, exch, lp)
        for key, (sym, exch, lp) in equity_unique.items()
        if not _cached_base_metrics_usable(equity_metrics.get(key, {}), exch)
    ]
    if retry_equity:
        for key, sym, exch, lp in retry_equity:
            clear_metrics_cache_for_symbol(sym, exch)
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {
                pool.submit(_load_equity, (key, sym, exch, lp)): key
                for key, sym, exch, lp in retry_equity
            }
            for future in as_completed(futures):
                try:
                    key, metrics = future.result()
                    equity_metrics[key] = metrics
                except Exception as exc:
                    logger.warning("Metrics retry failed for %s: %s", futures.get(future), exc)

    enriched = []
    for holding in holdings:
        if holding.get("asset_class") == "mf":
            isin = (holding.get("isin") or holding.get("symbol") or "").strip().upper()
            row = {**holding, **mf_metrics.get(isin, {})}
            from modules.portfolio.services.mf_cap import classify_mf_cap

            mf_cap = classify_mf_cap(row.get("fund_name") or row.get("symbol"))
            if mf_cap:
                row["market_cap"] = mf_cap
            elif row.get("market_cap") == "MF":
                row["market_cap"] = None
            enriched.append(row)
            continue
        symbol = holding.get("symbol")
        key = f"{symbol}:{holding.get('exchange') or 'NSE'}"
        enriched.append({**holding, **equity_metrics.get(key, {})})

    apply_holdings_metric_overrides(enriched)
    try:
        from modules.portfolio.db import sector_llm_cache

        sector_llm_cache.export_reference_file()
    except Exception:
        pass
    if sector_llm if sector_llm is not None else _sector_llm_on_enrich():
        try:
            from modules.portfolio.services.sector_llm import classify_holdings_llm

            classify_holdings_llm(enriched)
        except Exception as exc:
            logger.warning("LLM sector classification skipped: %s", exc)

    if buy_thesis_llm if buy_thesis_llm is not None else _buy_thesis_llm_on_enrich():
        try:
            from modules.portfolio.services.buy_thesis_llm import generate_buy_theses_llm

            generate_buy_theses_llm(enriched)
        except Exception as exc:
            logger.warning("Buy thesis LLM skipped: %s", exc)

    return enriched
