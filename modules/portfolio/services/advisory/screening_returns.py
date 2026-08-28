"""Deterministic, bounded return screens from dated market-data inputs.

These models rank holdings for research.  They are deliberately lower-trust than
documented filing/AMC models and must never be presented as authoritative forecasts.
"""

from __future__ import annotations

from statistics import median
from typing import Any


MODEL_VERSION = "advisor-screening-v1"
MODEL_QUALITY = "screening_proxy"


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def _pct(value: Any) -> float | None:
    result = _number(value)
    if result is None:
        return None
    if 0 < abs(result) <= 1:
        result *= 100
    return result


def _bounded(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _instrument_name(holding: dict[str, Any]) -> str:
    value = holding.get("instrument_type") or holding.get("asset_class") or "equity"
    return str(getattr(value, "value", value)).strip().lower()


def _metadata(
    *,
    method: str,
    scenarios: dict[str, dict[str, float]],
    source: str,
    source_type: str,
    as_of: str,
    source_url: str | None,
    drivers: list[str],
) -> dict[str, Any]:
    return {
        "method": method,
        "model_quality": MODEL_QUALITY,
        "model_version": MODEL_VERSION,
        "source": source,
        "source_type": source_type,
        "source_url": source_url,
        "as_of": as_of,
        "drivers": drivers,
        "scenarios": scenarios,
    }


def _equity_screen(
    holding: dict[str, Any],
    *,
    source: str,
    source_type: str,
    as_of: str,
    source_url: str | None,
) -> dict[str, Any] | None:
    price = _number(holding.get("last_price"))
    trailing_pe = _number(holding.get("trailing_pe") or holding.get("pe_ratio"))
    forward_pe = _number(holding.get("forward_pe"))
    trailing_eps = _number(holding.get("trailing_eps"))
    forward_eps = _number(holding.get("forward_eps"))
    if not price or price <= 0:
        return None

    drivers: list[str] = []
    if trailing_eps and trailing_eps > 0:
        current_eps = trailing_eps
        drivers.append("trailing_eps")
    elif trailing_pe and trailing_pe > 0:
        current_eps = price / trailing_pe
        drivers.append("price_divided_by_trailing_pe")
    else:
        return None

    growth_inputs: list[float] = []
    if forward_eps and forward_eps > 0 and current_eps > 0:
        growth_inputs.append(((forward_eps / current_eps) - 1) * 100)
        drivers.append("forward_vs_trailing_eps")
    for field in ("earnings_growth_pct", "revenue_growth_pct"):
        value = _pct(holding.get(field))
        if value is not None:
            growth_inputs.append(value)
            drivers.append(field)
    if not growth_inputs:
        return None

    base_growth = _bounded(float(median(growth_inputs)), -10, 30)
    current_multiple = trailing_pe or (price / current_eps)
    if not current_multiple or current_multiple <= 0:
        return None
    market_anchor = 18.0
    base_multiple = _bounded(current_multiple * 0.75 + market_anchor * 0.25, 6, 40)
    if forward_pe and forward_pe > 0:
        base_multiple = _bounded(float(median([base_multiple, forward_pe])), 6, 40)
        drivers.append("forward_pe")

    dividend_yield = _pct(holding.get("dividend_yield_pct")) or 0.0
    dividend_yield = _bounded(dividend_yield, 0, 8)
    cumulative_dividends = price * dividend_yield / 100 * 3
    scenario_specs = {
        "bear": (_bounded(base_growth - 8, -20, 22), _bounded(base_multiple * 0.75, 5, 30)),
        "base": (base_growth, base_multiple),
        "bull": (_bounded(base_growth + 8, -2, 38), _bounded(base_multiple * 1.10, 7, 45)),
    }
    scenarios = {
        name: {
            "eps_year3": round(current_eps * ((1 + growth / 100) ** 3), 4),
            "exit_multiple": round(multiple, 2),
            "cumulative_dividends": round(cumulative_dividends, 4),
        }
        for name, (growth, multiple) in scenario_specs.items()
    }
    return _metadata(
        method="eps",
        scenarios=scenarios,
        source=source,
        source_type=source_type,
        as_of=as_of,
        source_url=source_url,
        drivers=drivers,
    )


def _fund_screen(
    holding: dict[str, Any],
    *,
    source: str,
    source_type: str,
    as_of: str,
    source_url: str | None,
) -> dict[str, Any] | None:
    historical_return = _pct(
        holding.get("return_3y_cagr_pct") or holding.get("three_year_average_return_pct")
    )
    if historical_return is None:
        return None
    base = _bounded(historical_return, -15, 28)
    scenarios = {
        "bear": {
            "earnings_growth_pct": round(_bounded(base - 7, -25, 20), 2),
            "annual_valuation_reversion_pct": 0.0,
            "yield_pct": 0.0,
            "fees_pct": 0.0,
        },
        "base": {
            "earnings_growth_pct": round(base, 2),
            "annual_valuation_reversion_pct": 0.0,
            "yield_pct": 0.0,
            "fees_pct": 0.0,
        },
        "bull": {
            "earnings_growth_pct": round(_bounded(base + 5, -5, 33), 2),
            "annual_valuation_reversion_pct": 0.0,
            "yield_pct": 0.0,
            "fees_pct": 0.0,
        },
    }
    return _metadata(
        method="fund_build_up",
        scenarios=scenarios,
        source=source,
        source_type=source_type,
        as_of=as_of,
        source_url=source_url,
        drivers=["trailing_3y_total_return_cagr"],
    )


def build_screening_return_inputs(
    holding: dict[str, Any],
    *,
    source: str,
    as_of: str,
    source_type: str = "derived_market_model",
    source_url: str | None = None,
) -> dict[str, Any] | None:
    """Build a lower-confidence screen only when sufficient dated inputs exist."""
    instrument = _instrument_name(holding)
    if instrument in {"etf", "mutual_fund", "mf"}:
        return _fund_screen(
            holding,
            source=source,
            source_type=source_type,
            as_of=as_of,
            source_url=source_url,
        )
    if instrument != "equity":
        return None
    return _equity_screen(
        holding,
        source=source,
        source_type=source_type,
        as_of=as_of,
        source_url=source_url,
    )
