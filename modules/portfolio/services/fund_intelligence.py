"""Dated fund look-through, overlap, cost, liquidity, and consolidation analytics."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any, Protocol

from modules.portfolio.db import fund_intelligence as store


class FundHoldingsProvider(Protocol):
    def fetch_holdings(self, *, fund_instrument_id: str, as_of: str) -> dict[str, Any]: ...


def lookthrough(
    fund_instrument_id: str,
    *,
    as_of: str | None = None,
    _path: tuple[str, ...] = (),
) -> dict[str, Any]:
    if fund_instrument_id in _path:
        return {
            "fund_instrument_id": fund_instrument_id,
            "status": "CYCLE_BLOCKED",
            "exposures": {},
            "unresolved": [],
            "coverage_pct": 0.0,
            "data_quality_flags": ["FUND_OF_FUND_CYCLE_BLOCKED"],
        }
    rows = store.latest_constituents(fund_instrument_id)
    if not rows:
        return {
            "fund_instrument_id": fund_instrument_id,
            "status": "LOOKTHROUGH_UNAVAILABLE",
            "exposures": {},
            "unresolved": [],
            "coverage_pct": 0.0,
            "data_quality_flags": ["LOOKTHROUGH_UNAVAILABLE"],
        }
    observation_as_of = str(rows[0]["as_of"])
    reference_day = date.fromisoformat(as_of or date.today().isoformat())
    age_days = max(0, (reference_day - date.fromisoformat(observation_as_of)).days)
    flags: set[str] = set()
    if age_days > 45:
        flags.add("STALE_FUND_HOLDINGS")
    if rows[0]["coverage_type"] != "FULL" or float(rows[0]["coverage_pct"]) < 99:
        flags.add("PARTIAL_LOOKTHROUGH_COVERAGE")
    exposures: dict[str, float] = defaultdict(float)
    unresolved: list[dict[str, Any]] = []
    for row in rows:
        weight = float(row["weight_pct"])
        underlying = row.get("underlying_instrument_id")
        if not underlying:
            unresolved.append({"label": row.get("unresolved_label"), "weight_pct": weight})
            continue
        if store.get_scheme(str(underlying)):
            nested = lookthrough(str(underlying), as_of=as_of, _path=(*_path, fund_instrument_id))
            flags.update(nested["data_quality_flags"])
            if nested["exposures"]:
                for instrument_id, nested_weight in nested["exposures"].items():
                    exposures[instrument_id] += weight * float(nested_weight) / 100
            else:
                unresolved.append({"label": underlying, "weight_pct": weight, "reason": nested["status"]})
            continue
        exposures[str(underlying)] += weight
    coverage = float(rows[0]["coverage_pct"])
    if unresolved:
        flags.add("UNRESOLVED_FUND_CONSTITUENTS")
    confidence = coverage
    if age_days > 45:
        confidence *= 0.6
    return {
        "fund_instrument_id": fund_instrument_id,
        "status": "AVAILABLE" if not flags else "PARTIAL",
        "as_of": observation_as_of,
        "age_days": age_days,
        "coverage_type": rows[0]["coverage_type"],
        "coverage_pct": round(coverage, 2),
        "confidence": round(confidence, 2),
        "exposures": {key: round(value, 6) for key, value in sorted(exposures.items())},
        "unresolved": unresolved,
        "data_quality_flags": sorted(flags),
        "source": rows[0]["source"],
        "source_type": rows[0]["source_type"],
    }


def pairwise_overlap(first_id: str, second_id: str, *, as_of: str | None = None) -> dict[str, Any]:
    first = lookthrough(first_id, as_of=as_of)
    second = lookthrough(second_id, as_of=as_of)
    if not first["exposures"] or not second["exposures"]:
        return {
            "first_instrument_id": first_id,
            "second_instrument_id": second_id,
            "status": "LOOKTHROUGH_UNAVAILABLE",
            "weighted_overlap_pct": None,
            "common_holdings": [],
            "data_quality_flags": sorted(set(first["data_quality_flags"] + second["data_quality_flags"])),
        }
    common = sorted(set(first["exposures"]) & set(second["exposures"]))
    rows = [
        {
            "instrument_id": instrument_id,
            "first_weight_pct": first["exposures"][instrument_id],
            "second_weight_pct": second["exposures"][instrument_id],
            "overlap_weight_pct": min(first["exposures"][instrument_id], second["exposures"][instrument_id]),
        }
        for instrument_id in common
    ]
    overlap = sum(row["overlap_weight_pct"] for row in rows)
    confidence = min(first["confidence"], second["confidence"])
    return {
        "first_instrument_id": first_id,
        "second_instrument_id": second_id,
        "status": "AVAILABLE" if confidence >= 80 else "PARTIAL",
        "weighted_overlap_pct": round(overlap, 2),
        "common_holdings": sorted(rows, key=lambda row: -row["overlap_weight_pct"]),
        "confidence": confidence,
        "data_quality_flags": sorted(set(first["data_quality_flags"] + second["data_quality_flags"])),
    }


def family_lookthrough(positions: list[dict[str, Any]], *, as_of: str | None = None) -> dict[str, Any]:
    total_value = sum(float(row.get("current_value") or 0) for row in positions)
    exposures_value: dict[str, float] = defaultdict(float)
    direct_values: dict[str, float] = defaultdict(float)
    source_paths: dict[str, list[dict[str, Any]]] = defaultdict(list)
    flags: set[str] = set()
    for row in positions:
        instrument_id = str(row.get("instrument_id") or "")
        value = float(row.get("current_value") or 0)
        if not instrument_id:
            continue
        if store.get_scheme(instrument_id):
            result = lookthrough(instrument_id, as_of=as_of)
            flags.update(result["data_quality_flags"])
            for underlying, weight in result["exposures"].items():
                allocated = value * weight / 100
                exposures_value[underlying] += allocated
                source_paths[underlying].append({"via": instrument_id, "value": round(allocated, 2), "weight_pct": weight})
        else:
            exposures_value[instrument_id] += value
            direct_values[instrument_id] += value
            source_paths[instrument_id].append({"via": "DIRECT", "value": round(value, 2), "weight_pct": 100})
    exposures = [
        {
            "underlying_instrument_id": instrument_id,
            "value": round(value, 2),
            "family_weight_pct": round(value / total_value * 100, 2) if total_value else 0,
            "sources": source_paths[instrument_id],
            "direct_value": round(direct_values.get(instrument_id, 0), 2),
            "through_funds_value": round(value - direct_values.get(instrument_id, 0), 2),
        }
        for instrument_id, value in sorted(exposures_value.items(), key=lambda item: -item[1])
    ]
    duplicates = [row for row in exposures if row["direct_value"] > 0 and row["through_funds_value"] > 0]
    return {
        "family_value": round(total_value, 2),
        "underlying_exposures": exposures,
        "direct_stock_duplication": duplicates,
        "data_quality_flags": sorted(flags),
        "value_conservation_note": "Family value is counted once; look-through only reallocates fund wrapper value to underlying exposures.",
    }


def weighted_ter(positions: list[dict[str, Any]]) -> dict[str, Any]:
    fund_rows = []
    for row in positions:
        scheme = store.get_scheme(str(row.get("instrument_id") or ""))
        if scheme:
            fund_rows.append((row, scheme))
    fund_value = sum(float(row.get("current_value") or 0) for row, _ in fund_rows)
    covered_value = sum(float(row.get("current_value") or 0) for row, scheme in fund_rows if scheme.get("ter_pct") is not None)
    annual_cost = sum(float(row.get("current_value") or 0) * float(scheme.get("ter_pct") or 0) / 100 for row, scheme in fund_rows)
    return {
        "fund_value": round(fund_value, 2),
        "weighted_ter_pct": round(annual_cost / fund_value * 100, 4) if fund_value else None,
        "estimated_annual_cost": round(annual_cost, 2),
        "coverage_pct": round(covered_value / fund_value * 100, 2) if fund_value else 0,
    }


def etf_analytics(scheme: dict[str, Any]) -> dict[str, Any]:
    spread = scheme.get("bid_ask_spread_pct")
    traded = scheme.get("average_traded_value")
    poor_liquidity = (spread is not None and float(spread) > 0.5) or (traded is not None and float(traded) < 1_000_000)
    return {
        "instrument_id": scheme.get("instrument_id"),
        "bid_ask_spread_pct": spread,
        "average_traded_value": traded,
        "premium_discount_pct": scheme.get("premium_discount_pct"),
        "tracking_error_pct": scheme.get("tracking_error_pct"),
        "tracking_difference_pct": scheme.get("tracking_difference_pct"),
        "rebalance_schedule": scheme.get("rebalance_schedule"),
        "market_order_warning": bool(poor_liquidity),
        "execution_note": "Use a limit order and verify iNAV/NAV for illiquid ETFs." if poor_liquidity else "Normal liquidity checks still apply.",
    }


def mutual_fund_analytics(scheme: dict[str, Any], rolling_periods: list[dict[str, Any]]) -> dict[str, Any]:
    returns = [float(row["return_pct"]) for row in rolling_periods if row.get("return_pct") is not None]
    benchmark = [float(row["benchmark_return_pct"]) for row in rolling_periods if row.get("benchmark_return_pct") is not None]
    downside_pairs = [
        (float(row["return_pct"]), float(row["benchmark_return_pct"]))
        for row in rolling_periods
        if row.get("return_pct") is not None and row.get("benchmark_return_pct") is not None and float(row["benchmark_return_pct"]) < 0
    ]
    downside_capture = (
        sum(item[0] for item in downside_pairs) / sum(item[1] for item in downside_pairs) * 100
        if downside_pairs and sum(item[1] for item in downside_pairs)
        else None
    )
    return {
        "rolling_return_pct": round(sum(returns) / len(returns), 2) if returns else None,
        "rolling_alpha_pct": round(sum(r - b for r, b in zip(returns, benchmark)) / min(len(returns), len(benchmark)), 2) if returns and benchmark else None,
        "downside_capture_pct": round(downside_capture, 2) if downside_capture is not None else None,
        "manager_tenure_years": scheme.get("manager_tenure_years"),
        "ter_pct": scheme.get("ter_pct"),
        "scheme_plan": scheme.get("scheme_plan"),
        "scheme_option": scheme.get("scheme_option"),
        "forward_return_policy": "Trailing return alone is not used as forward expected return.",
    }


def consolidation_candidates(positions: list[dict[str, Any]], *, as_of: str | None = None) -> list[dict[str, Any]]:
    fund_ids = [str(row.get("instrument_id")) for row in positions if store.get_scheme(str(row.get("instrument_id") or ""))]
    recommendations: list[dict[str, Any]] = []
    for index, first_id in enumerate(fund_ids):
        for second_id in fund_ids[index + 1 :]:
            overlap = pairwise_overlap(first_id, second_id, as_of=as_of)
            if overlap.get("weighted_overlap_pct") is None or float(overlap["weighted_overlap_pct"]) < 70:
                continue
            first, second = store.get_scheme(first_id) or {}, store.get_scheme(second_id) or {}
            preferred = min((first, second), key=lambda row: float(row.get("ter_pct") or 999))
            other = second if preferred.get("instrument_id") == first_id else first
            ter_saving = max(0.0, float(other.get("ter_pct") or 0) - float(preferred.get("ter_pct") or 0))
            recommendations.append(
                {
                    "source_instrument_id": other.get("instrument_id"),
                    "preferred_destination": preferred.get("instrument_id"),
                    "weighted_overlap_pct": overlap["weighted_overlap_pct"],
                    "cost_impact_ter_saving_pct": round(ter_saving, 4),
                    "tax_exit_load_review_required": True,
                    "why": "High dated constituent overlap plus lower wrapper cost may simplify the portfolio after tax and exit-load review.",
                    "evidence": overlap,
                }
            )
    return recommendations
