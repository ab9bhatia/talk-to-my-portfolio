"""Transparent deterministic portfolio stress engine; never a forecast."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from modules.portfolio.db import fund_intelligence as fund_store
from modules.portfolio.services.fund_intelligence import lookthrough


METHODOLOGY_VERSION = "stress-v1"
SCENARIO_LIBRARY = {
    "small_cap_correction": {"market_cap_shocks": {"SMALL": -0.30}, "default_shock": -0.04},
    "credit_stress": {"sector_shocks": {"BANKING": -0.16, "NBFC": -0.24, "REAL ESTATE": -0.18}},
    "oil_inr_shock": {"sector_shocks": {"AVIATION": -0.20, "PAINTS": -0.12}, "currency_shocks": {"USD": 0.08}},
    "fii_rate_shock": {"market_cap_shocks": {"LARGE": -0.10, "MID": -0.16, "SMALL": -0.22}},
    "capex_defence_rail_derating": {"sector_shocks": {"DEFENCE": -0.25, "RAILWAYS": -0.25, "CAPITAL GOODS": -0.20}},
    "promoter_group_event": {"promoter_group_shocks": {"TARGET": -0.35}},
    "technology_compression": {"sector_shocks": {"TECHNOLOGY": -0.22, "IT": -0.22}},
    "commodity_reversal": {"sector_shocks": {"METALS": -0.25, "CHEMICALS": -0.22, "SUGAR": -0.28}},
    "global_risk_off": {"market_shocks": {"US": -0.20}, "asset_class_shocks": {"CRYPTO": -0.35}},
}


def scenario_definition(name: str, custom: dict[str, Any] | None = None) -> dict[str, Any]:
    if name == "custom":
        if not custom:
            raise ValueError("Custom stress scenario requires explicit assumptions.")
        assumptions = custom
    elif name in SCENARIO_LIBRARY:
        assumptions = SCENARIO_LIBRARY[name]
    else:
        raise ValueError(f"Unknown stress scenario: {name}")
    return {"name": name, "methodology_version": METHODOLOGY_VERSION, "assumptions": assumptions}


def stress_portfolio(
    positions: list[dict[str, Any]],
    *,
    scenario: dict[str, Any],
    exposure_metadata: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    assumptions = scenario.get("assumptions") or scenario
    metadata_map = exposure_metadata or {}
    total_value = sum(float(row.get("current_value") or 0) for row in positions)
    account_impact: dict[str, float] = defaultdict(float)
    contributors: list[dict[str, Any]] = []
    stressed_value = 0.0
    covered_value = 0.0

    for row in positions:
        value = float(row.get("current_value") or 0)
        instrument_id = str(row.get("instrument_id") or "")
        account_code = str(row.get("account_code") or "UNKNOWN")
        scheme = fund_store.get_scheme(instrument_id)
        slices: list[tuple[str, float, dict[str, Any]]] = []
        if scheme:
            expanded = lookthrough(instrument_id)
            source_rows = {
                str(item.get("underlying_instrument_id")): item
                for item in fund_store.latest_constituents(instrument_id)
                if item.get("underlying_instrument_id")
            }
            for underlying, weight in expanded["exposures"].items():
                slices.append(
                    (
                        underlying,
                        value * float(weight) / 100,
                        {**source_rows.get(underlying, {}), **metadata_map.get(underlying, {})},
                    )
                )
            uncovered = max(0.0, value - sum(item[1] for item in slices))
            if uncovered:
                slices.append((instrument_id, uncovered, {**row, "uncovered": True}))
        else:
            slices = [(instrument_id, value, {**row, **metadata_map.get(instrument_id, {})})]

        for exposure_id, exposure_value, metadata in slices:
            shock, matched = _shock_for(metadata, assumptions)
            impact = exposure_value * shock
            stressed_value += exposure_value + impact
            account_impact[account_code] += impact
            if matched:
                covered_value += exposure_value
            contributors.append(
                {
                    "instrument_id": exposure_id,
                    "via": instrument_id if scheme else "DIRECT",
                    "account_code": account_code,
                    "value": round(exposure_value, 2),
                    "shock_pct": round(shock * 100, 2),
                    "impact": round(impact, 2),
                    "assumption_matched": matched,
                    "estimated_exit_days": (
                        round(exposure_value / float(metadata["average_traded_value"]), 2)
                        if metadata.get("average_traded_value")
                        and float(metadata["average_traded_value"]) > 0
                        else None
                    ),
                }
            )
    impact_total = stressed_value - total_value
    return {
        "scenario": scenario,
        "methodology_version": METHODOLOGY_VERSION,
        "starting_family_value": round(total_value, 2),
        "stressed_family_value": round(stressed_value, 2),
        "estimated_family_drawdown_pct": round(impact_total / total_value * 100, 2) if total_value else None,
        "estimated_family_impact": round(impact_total, 2),
        "account_drawdown": [
            {"account_code": code, "impact": round(impact, 2)}
            for code, impact in sorted(account_impact.items())
        ],
        "largest_contributors": sorted(contributors, key=lambda item: item["impact"])[:10],
        "liquidity_to_exit": sorted(
            (
                {
                    "instrument_id": row["instrument_id"],
                    "estimated_exit_days": row["estimated_exit_days"],
                }
                for row in contributors
                if row["estimated_exit_days"] is not None
            ),
            key=lambda row: -row["estimated_exit_days"],
        )[:10],
        "post_stress_allocation": _post_stress_allocation(contributors),
        "coverage_pct": round(covered_value / total_value * 100, 2) if total_value else 0,
        "model_limitations": [
            "Static shocks are deterministic assumptions, not forecasts.",
            "Beta, liquidity, second-order correlations, and gap risk may differ in live markets.",
            "Uncovered fund constituents use only the explicit default shock, if supplied.",
        ],
        "execution_enabled": False,
    }


def _shock_for(metadata: dict[str, Any], assumptions: dict[str, Any]) -> tuple[float, bool]:
    shocks: list[float] = []
    for field, assumption_key in (
        ("market_cap", "market_cap_shocks"),
        ("sector", "sector_shocks"),
        ("currency", "currency_shocks"),
        ("promoter_group", "promoter_group_shocks"),
        ("market", "market_shocks"),
        ("asset_class", "asset_class_shocks"),
        ("factor_style", "factor_shocks"),
    ):
        value = str(metadata.get(field) or "").upper()
        mapping = {str(key).upper(): float(shock) for key, shock in (assumptions.get(assumption_key) or {}).items()}
        if value in mapping:
            shocks.append(mapping[value])
    if shocks:
        return min(shocks), True
    return float(assumptions.get("default_shock") or 0), "default_shock" in assumptions


def _post_stress_allocation(contributors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    totals: dict[str, float] = defaultdict(float)
    for row in contributors:
        totals[row["account_code"]] += float(row["value"]) + float(row["impact"])
    total = sum(totals.values())
    return [
        {"account_code": code, "value": round(value, 2), "weight_pct": round(value / total * 100, 2) if total else 0}
        for code, value in sorted(totals.items())
    ]
