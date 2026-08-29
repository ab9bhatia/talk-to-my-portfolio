"""Scenario-based expected three-year return calculations."""

from __future__ import annotations

from typing import Any

from modules.portfolio.services.advisory.models import DataQualityFlag, ExpectedThreeYearIrr


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result


def _eps_irr(current_price: float, scenario: dict[str, Any]) -> float | None:
    eps = _number(scenario.get("eps_year3"))
    multiple = _number(scenario.get("exit_multiple"))
    dividends = _number(scenario.get("cumulative_dividends")) or 0.0
    if current_price <= 0 or eps is None or multiple is None:
        return None
    terminal_value = eps * multiple + dividends
    if terminal_value <= 0:
        return None
    return round((((terminal_value / current_price) ** (1 / 3)) - 1) * 100, 2)


def _fund_build_up_irr(scenario: dict[str, Any]) -> float | None:
    growth = _number(scenario.get("earnings_growth_pct"))
    valuation = _number(scenario.get("annual_valuation_reversion_pct"))
    yield_pct = _number(scenario.get("yield_pct"))
    fees = _number(scenario.get("fees_pct"))
    if growth is None or valuation is None:
        return None
    return round(growth + valuation + (yield_pct or 0.0) - (fees or 0.0), 2)


def _assumption_lines(method: str, scenarios: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for name in ("bear", "base", "bull"):
        values = scenarios.get(name) or {}
        if method == "eps":
            eps = values.get("eps_year3")
            multiple = values.get("exit_multiple")
            dividends = values.get("cumulative_dividends", 0)
            if eps is not None and multiple is not None:
                lines.append(
                    f"{name}: year-3 EPS {eps}, exit multiple {multiple}x, "
                    f"cumulative dividends {dividends}."
                )
        elif method == "fund_build_up":
            growth = values.get("earnings_growth_pct")
            valuation = values.get("annual_valuation_reversion_pct")
            if growth is not None and valuation is not None:
                lines.append(
                    f"{name}: earnings growth {growth}%, annual valuation effect {valuation}%, "
                    f"yield {values.get('yield_pct', 0)}%, fees {values.get('fees_pct', 0)}%."
                )
    return lines


def expected_three_year_irr(
    holding: dict[str, Any],
) -> tuple[ExpectedThreeYearIrr, list[DataQualityFlag]]:
    """Calculate documented scenarios or return an explicit unavailable result."""
    inputs = holding.get("expected_return_inputs") or {}
    method = str(inputs.get("method") or "").strip().lower()
    scenarios = inputs.get("scenarios") or {}
    current_price = _number(holding.get("last_price"))
    flags: list[DataQualityFlag] = []
    evidence_tier = str(inputs.get("model_quality") or "documented")

    if holding.get("reconciliation_blocking"):
        flags.append(
            DataQualityFlag(
                code="RECONCILIATION_BLOCKS_VALUATION",
                severity="error",
                message=(
                    "Expected return is unavailable until identity, quantity, price, "
                    "FX, or corporate-action reconciliation is complete."
                ),
                blocking=True,
            )
        )
        return (
            ExpectedThreeYearIrr(
                bear_pct=None,
                base_pct=None,
                bull_pct=None,
                probability_above_target=None,
                method="unavailable_reconciliation",
                assumptions=["Valuation-dependent output suppressed by reconciliation."],
                evidence_tier="needs_data",
            ),
            flags,
        )

    if method not in {"eps", "fund_build_up"}:
        flags.append(
            DataQualityFlag(
                code="MISSING_EXPECTED_RETURN_INPUTS",
                severity="warning",
                message="Documented three-year valuation assumptions are unavailable.",
                blocking=True,
            )
        )
        return (
            ExpectedThreeYearIrr(
                bear_pct=None,
                base_pct=None,
                bull_pct=None,
                probability_above_target=None,
                method="unavailable",
                assumptions=["No sourced, instrument-appropriate three-year model inputs."],
                evidence_tier="needs_data",
            ),
            flags,
        )

    if method == "eps" and (current_price is None or current_price <= 0):
        flags.append(
            DataQualityFlag(
                code="MISSING_CURRENT_PRICE",
                severity="error",
                message="A positive current price is required for the EPS return model.",
                blocking=True,
            )
        )
        return (
            ExpectedThreeYearIrr(
                None,
                None,
                None,
                None,
                "unavailable",
                [],
                "needs_data",
            ),
            flags,
        )

    calculator = (
        (lambda row: _eps_irr(float(current_price), row))
        if method == "eps"
        else _fund_build_up_irr
    )
    values = {name: calculator(scenarios.get(name) or {}) for name in ("bear", "base", "bull")}
    if values["base"] is None:
        flags.append(
            DataQualityFlag(
                code="INCOMPLETE_EXPECTED_RETURN_INPUTS",
                severity="error",
                message="The base three-year scenario is incomplete.",
                blocking=True,
            )
        )

    if not inputs.get("source") or not inputs.get("as_of"):
        flags.append(
            DataQualityFlag(
                code="EXPECTED_RETURN_PROVENANCE_MISSING",
                severity="warning",
                message="Return assumptions need a source and as-of date.",
            )
        )

    if evidence_tier == "screening_proxy":
        flags.append(
            DataQualityFlag(
                code="EXPECTED_RETURN_SCREENING_PROXY",
                severity="info",
                message=(
                    "Three-year scenarios are a dated market-data screening model, "
                    "not a filing/AMC-backed forecast."
                ),
            )
        )

    return (
        ExpectedThreeYearIrr(
            bear_pct=values["bear"],
            base_pct=values["base"],
            bull_pct=values["bull"],
            probability_above_target=None,
            method=method,
            assumptions=(
                [
                    "Screening proxy only; verify against filings or AMC factsheets before execution."
                ]
                if evidence_tier == "screening_proxy"
                else []
            )
            + _assumption_lines(method, scenarios),
            evidence_tier=evidence_tier,
        ),
        flags,
    )
