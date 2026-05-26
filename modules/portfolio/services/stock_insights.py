"""Expanded stock insights: chart, recent results, and 1Y forecast."""

from __future__ import annotations

import time
from datetime import date, datetime, timezone
from typing import Any

import yfinance as yf

from modules.portfolio.services.analyst_rating import compute_rating
from modules.portfolio.services.market_data import _quiet_yfinance, resolve_yahoo_ticker

_INSIGHTS_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_CACHE_TTL_SECONDS = 6 * 60 * 60


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
        if result != result:  # NaN
            return None
        return result
    except (TypeError, ValueError):
        return None


def _format_period(value: Any) -> str:
    if hasattr(value, "strftime"):
        return value.strftime("%b %Y")
    return str(value)


def _income_row(frame, labels: tuple[str, ...]) -> Any | None:
    for label in labels:
        if label in frame.index:
            return frame.loc[label]
    return None


def _pct_change(current: float | None, previous: float | None) -> float | None:
    """Return % change between two values."""
    if current is None or previous is None or previous == 0:
        return None
    return round(((current - previous) / abs(previous)) * 100, 1)


_DISPLAY_QUARTERS = 5
_CALC_QUARTERS = _DISPLAY_QUARTERS + 4  # need +4 for YoY on oldest displayed quarter


def _attach_growth(
    rows: list[dict[str, Any]],
    metrics: list[tuple[str, str, str]],
) -> list[dict[str, Any]]:
    """Attach QoQ and YoY % changes for each metric. Rows are newest-first."""
    enriched: list[dict[str, Any]] = []
    for index, row in enumerate(rows[:_DISPLAY_QUARTERS]):
        item = {key: value for key, value in row.items() if not key.endswith("_raw")}
        for value_key, qoq_key, yoy_key in metrics:
            current = row.get(value_key)
            if index + 1 < len(rows):
                item[qoq_key] = _pct_change(current, rows[index + 1].get(value_key))
            else:
                item[qoq_key] = None
            if index + 4 < len(rows):
                item[yoy_key] = _pct_change(current, rows[index + 4].get(value_key))
            else:
                item[yoy_key] = None
        enriched.append(item)
    return enriched


def _recent_results(ticker: yf.Ticker) -> list[dict[str, Any]]:
    """Best-effort quarterly revenue / profit from Yahoo."""
    try:
        with _quiet_yfinance():
            income = ticker.quarterly_income_stmt
    except Exception:
        income = None

    if income is not None and not income.empty:
        revenue_row = _income_row(income, ("Total Revenue", "Operating Revenue"))
        net_row = _income_row(income, ("Net Income", "Net Income Common Stockholders"))
        raw: list[dict[str, Any]] = []
        for column in list(income.columns[:_CALC_QUARTERS]):
            revenue = _safe_float(revenue_row[column]) if revenue_row is not None else None
            net_income = _safe_float(net_row[column]) if net_row is not None else None
            if revenue is None and net_income is None:
                continue
            raw.append(
                {
                    "period": _format_period(column),
                    "revenue_cr": round(revenue / 1e7, 2) if revenue else None,
                    "net_income_cr": round(net_income / 1e7, 2) if net_income else None,
                    "revenue_raw": revenue,
                    "net_income_raw": net_income,
                }
            )

        if raw:
            return _attach_growth(
                raw,
                [
                    ("revenue_raw", "revenue_qoq_pct", "revenue_yoy_pct"),
                    ("net_income_raw", "net_income_qoq_pct", "net_income_yoy_pct"),
                ],
            )

    try:
        with _quiet_yfinance():
            earnings = ticker.earnings_dates
    except Exception:
        earnings = None

    if earnings is not None and not earnings.empty:
        raw = []
        for index, row in earnings.head(_CALC_QUARTERS).iterrows():
            reported = _safe_float(row.get("Reported EPS"))
            raw.append(
                {
                    "period": _format_period(index),
                    "eps_estimate": _safe_float(row.get("EPS Estimate")),
                    "reported_eps": reported,
                    "surprise_pct": _safe_float(row.get("Surprise(%)")),
                    "reported_eps_raw": reported,
                }
            )
        if raw:
            enriched = _attach_growth(raw, [("reported_eps_raw", "eps_qoq_pct", "eps_yoy_pct")])
            return enriched

    return []


def _format_news_date(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc).strftime("%d %b %Y")
        except (OSError, OverflowError, ValueError):
            return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.strftime("%d %b %Y")
    if isinstance(value, datetime):
        return value.strftime("%d %b %Y")
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.strftime("%d %b %Y")
        except ValueError:
            return value[:10] if len(value) >= 10 else value
    return str(value)


def _news_url(content: dict[str, Any], item: dict[str, Any]) -> str | None:
    canonical = content.get("canonicalUrl") or item.get("canonicalUrl")
    if isinstance(canonical, dict):
        return canonical.get("url")
    if isinstance(canonical, str):
        return canonical
    return item.get("link") or item.get("url")


def _parse_news_item(item: dict[str, Any]) -> dict[str, Any] | None:
    content = item.get("content") if isinstance(item.get("content"), dict) else item
    title = content.get("title") or item.get("title")
    if not title:
        return None

    summary = (
        content.get("summary")
        or content.get("description")
        or item.get("summary")
        or item.get("description")
        or ""
    )
    summary = " ".join(str(summary).split())
    if len(summary) > 280:
        summary = summary[:277].rstrip() + "…"

    provider = content.get("provider") or item.get("provider") or {}
    publisher = provider.get("displayName") if isinstance(provider, dict) else item.get("publisher")

    pub_raw = (
        content.get("pubDate")
        or content.get("displayTime")
        or item.get("providerPublishTime")
        or item.get("pubDate")
    )

    return {
        "title": str(title).strip(),
        "summary": summary,
        "date": _format_news_date(pub_raw),
        "url": _news_url(content, item),
        "publisher": str(publisher).strip() if publisher else None,
    }


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
        except ValueError:
            return None
    return None


def _upcoming_events(ticker: yf.Ticker) -> list[dict[str, Any]]:
    """Best-effort upcoming earnings / ex-dividend from Yahoo calendar."""
    events: list[dict[str, Any]] = []
    today = date.today()
    try:
        with _quiet_yfinance():
            calendar = ticker.calendar
    except Exception:
        calendar = None

    if not calendar or not isinstance(calendar, dict):
        return events

    earnings_dates = calendar.get("Earnings Date")
    if earnings_dates is not None:
        if not isinstance(earnings_dates, list):
            earnings_dates = [earnings_dates]
        for earnings_date in earnings_dates:
            parsed = _as_date(earnings_date)
            if parsed is None or parsed < today:
                continue
            formatted = _format_news_date(earnings_date)
            if formatted:
                events.append(
                    {
                        "type": "earnings",
                        "label": "Upcoming earnings",
                        "date": formatted,
                    }
                )

    ex_div = calendar.get("Ex-Dividend Date")
    if ex_div is not None:
        parsed = _as_date(ex_div)
        if parsed is not None and parsed >= today:
            formatted = _format_news_date(ex_div)
            if formatted:
                events.append(
                    {
                        "type": "dividend",
                        "label": "Ex-dividend date",
                        "date": formatted,
                    }
                )

    return events


def _recent_news(ticker: yf.Ticker, *, limit: int = 5) -> list[dict[str, Any]]:
    """Recent headlines from Yahoo Finance."""
    try:
        with _quiet_yfinance():
            raw = ticker.news or []
    except Exception:
        raw = []

    news: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        parsed = _parse_news_item(item)
        if parsed:
            news.append(parsed)
        if len(news) >= limit:
            break
    return news


def _signal_context(ticker: yf.Ticker) -> dict[str, Any]:
    return {
        "events": _upcoming_events(ticker),
        "news": _recent_news(ticker),
    }


_TRADING_DAYS_1Y = 252

_EPS_ROW_LABELS = ("Diluted EPS", "Basic EPS", "Diluted EPS (I/B)", "Basic EPS (I/B)")


def _quarter_eps_points(ticker: yf.Ticker) -> list[tuple[date, float]]:
    """Quarter-end diluted/basic EPS for trailing P/E on the chart."""
    try:
        with _quiet_yfinance():
            income = ticker.quarterly_income_stmt
    except Exception:
        return []
    if income is None or income.empty:
        return []

    eps_row = _income_row(income, _EPS_ROW_LABELS)
    if eps_row is None:
        return []

    points: list[tuple[date, float]] = []
    for period, value in eps_row.items():
        as_of = _as_date(period)
        eps = _safe_float(value)
        if as_of and eps and eps > 0:
            points.append((as_of, eps))
    points.sort(key=lambda item: item[0])
    return points


def _ttm_eps_on_date(quarter_eps: list[tuple[date, float]], as_of: date) -> float | None:
    """Trailing twelve months EPS: sum of the four most recent quarters on or before as_of."""
    available = [eps for qdate, eps in quarter_eps if qdate <= as_of]
    if len(available) < 4:
        return None
    ttm = sum(available[-4:])
    return ttm if ttm > 0 else None


def _pe_ratio_series(
    index: Any,
    prices: list[float],
    quarter_eps: list[tuple[date, float]],
) -> list[float | None]:
    """Daily trailing P/E ≈ price / TTM EPS (last four quarters)."""
    if not quarter_eps or not prices:
        return [None] * len(prices)

    ratios: list[float | None] = []
    for stamp, price in zip(index, prices, strict=False):
        as_of = _as_date(stamp)
        if not as_of or not price:
            ratios.append(None)
            continue
        eps = _ttm_eps_on_date(quarter_eps, as_of)
        if eps and eps > 0:
            ratios.append(round(float(price) / eps, 1))
        else:
            ratios.append(None)
    return ratios


def _price_history(ticker: yf.Ticker) -> dict[str, list[Any]]:
    """Up to 10Y daily history with 200 DMA (client slices 1Y/3Y/5Y/10Y)."""
    history = None
    for period in ("10y", "5y", "2y", "1y"):
        try:
            with _quiet_yfinance():
                history = ticker.history(period=period, interval="1d", auto_adjust=True)
        except Exception:
            history = None
        if history is not None and not history.empty:
            break

    if history is None or history.empty:
        return {"labels": [], "prices": [], "dma200": [], "pe_ratio": [], "default_range": "1y"}

    closes = history["Close"]
    dma200 = closes.rolling(window=200, min_periods=200).mean()
    quarter_eps = _quarter_eps_points(ticker)

    labels = [index.strftime("%Y-%m-%d") for index in history.index]
    prices: list[float | None] = []
    for raw in history["Close"].tolist():
        close = _safe_float(raw)
        prices.append(round(close, 2) if close is not None else None)
    dma_values = []
    for index in history.index:
        value = _safe_float(dma200.get(index))
        dma_values.append(round(value, 2) if value is not None else None)

    pe_values = _pe_ratio_series(history.index, prices, quarter_eps)

    return {
        "labels": labels,
        "prices": prices,
        "dma200": dma_values,
        "pe_ratio": pe_values,
        "default_range": "1y",
    }


def _forecast(
    info: dict[str, Any],
    chart: dict[str, list[Any]],
    *,
    quantity: float,
    last_price: float | None,
) -> dict[str, Any]:
    current_price = _safe_float(info.get("regularMarketPrice")) or last_price
    qty = quantity or 0
    current_value = (current_price * qty) if current_price and qty else None

    target_price = _safe_float(info.get("targetMeanPrice")) or _safe_float(info.get("targetMedianPrice"))
    method = "analyst_target"
    note = "Analyst mean target from Yahoo Finance × your quantity."

    trend_prices = chart["prices"]
    if len(trend_prices) > _TRADING_DAYS_1Y:
        trend_prices = trend_prices[-_TRADING_DAYS_1Y:]

    if not target_price and trend_prices:
        first = trend_prices[0]
        last = trend_prices[-1]
        if first and first > 0:
            growth = (last / first) - 1
            target_price = round(last * (1 + growth), 2) if last else None
            method = "trailing_trend"
            note = "1Y price trend extrapolated from Yahoo history (no analyst target available)."

    projected_value = round(target_price * qty, 2) if target_price and qty else None
    upside_pct = None
    if projected_value is not None and current_value and current_value > 0:
        upside_pct = round(((projected_value - current_value) / current_value) * 100, 2)

    price_upside_pct = None
    if target_price and current_price and current_price > 0:
        price_upside_pct = round(((target_price - current_price) / current_price) * 100, 2)

    rating = compute_rating(
        recommendation_key=info.get("recommendationKey"),
        recommendation_mean=_safe_float(info.get("recommendationMean")),
        upside_pct=price_upside_pct if price_upside_pct is not None else upside_pct,
        target_price=target_price,
        last_price=current_price,
        analyst_count=info.get("numberOfAnalystOpinions"),
    )

    return {
        "target_price": target_price,
        "current_price": current_price,
        "quantity": qty,
        "current_value": round(current_value, 2) if current_value else None,
        "projected_value_1y": projected_value,
        "upside_pct": upside_pct,
        "analyst_count": info.get("numberOfAnalystOpinions"),
        "recommendation": info.get("recommendationKey"),
        "rating": rating,
        "method": method,
        "note": note,
    }


def get_stock_insights(
    symbol: str,
    exchange: str | None,
    *,
    quantity: float = 0,
    last_price: float | None = None,
) -> dict[str, Any]:
    """Return chart, recent results, and 1Y value forecast for a holding."""
    cache_key = f"{symbol}:{exchange or 'NSE'}:{quantity}:{last_price}"
    now = time.time()

    cached = _INSIGHTS_CACHE.get(cache_key)
    if cached and (now - cached[0]) < _CACHE_TTL_SECONDS:
        return cached[1]

    yahoo_ticker = resolve_yahoo_ticker(symbol, exchange)
    if not yahoo_ticker:
        payload = {
            "symbol": symbol,
            "exchange": exchange,
            "available": False,
            "message": "No Yahoo Finance data for this symbol.",
        }
        _INSIGHTS_CACHE[cache_key] = (now, payload)
        return payload

    try:
        with _quiet_yfinance():
            ticker = yf.Ticker(yahoo_ticker)
            info = ticker.info or {}
    except Exception:
        info = {}

    chart = _price_history(ticker)
    results = _recent_results(ticker)
    forecast = _forecast(info, chart, quantity=quantity, last_price=last_price)

    try:
        context = _signal_context(ticker)
    except Exception:
        context = {"events": [], "news": []}

    payload = {
        "symbol": symbol,
        "exchange": exchange,
        "yahoo_ticker": yahoo_ticker,
        "name": info.get("shortName") or info.get("longName"),
        "available": bool(yahoo_ticker),
        "chart": chart,
        "results": results,
        "forecast": forecast,
        "events": context["events"],
        "news": context["news"],
    }
    _INSIGHTS_CACHE[cache_key] = (now, payload)
    return payload
