"""Typed, versioned structures returned by the deterministic advisory engine."""

from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from enum import Enum, StrEnum
from typing import Any


class Action(StrEnum):
    STRONG_ADD = "STRONG_ADD"
    ADD = "ADD"
    HOLD = "HOLD"
    HOLD_NO_ADD = "HOLD_NO_ADD"
    CAP = "CAP"
    WATCH = "WATCH"
    REDUCE = "REDUCE"
    SELL = "SELL"
    RECONCILE = "RECONCILE"


class SellType(StrEnum):
    NONE = "NONE"
    FUNDAMENTAL_SELL = "FUNDAMENTAL_SELL"
    TACTICAL_REDUCE = "TACTICAL_REDUCE"
    PORTFOLIO_CONSOLIDATION = "PORTFOLIO_CONSOLIDATION"


class MomentumRegime(StrEnum):
    STRONG = "STRONG"
    POSITIVE = "POSITIVE"
    NEUTRAL = "NEUTRAL"
    WEAK = "WEAK"
    BROKEN = "BROKEN"


class InstrumentType(StrEnum):
    EQUITY = "equity"
    ETF = "etf"
    MUTUAL_FUND = "mutual_fund"
    BOND = "bond"
    GOLD = "gold"
    CRYPTO = "crypto"
    CASH = "cash"


@dataclass(frozen=True)
class Evidence:
    claim: str
    source: str
    as_of: str
    source_type: str


@dataclass(frozen=True)
class DataQualityFlag:
    code: str
    severity: str
    message: str
    blocking: bool = False


@dataclass(frozen=True)
class TaxRuleReference:
    rule_id: str
    jurisdiction: str
    reference: str
    effective_from: str
    effective_to: str | None
    source: str
    source_url: str
    last_reviewed: str
    required_inputs: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ExpectedThreeYearIrr:
    bear_pct: float | None
    base_pct: float | None
    bull_pct: float | None
    probability_above_target: float | None
    method: str
    assumptions: list[str] = field(default_factory=list)

    @property
    def available(self) -> bool:
        return self.base_pct is not None


@dataclass(frozen=True)
class Scores:
    quality: float
    growth: float
    valuation: float
    momentum: float
    moat_governance: float
    portfolio_fit: float
    macro: float
    total: float
    feature_coverage_pct: float
    missing_data_penalty: float = 0.0


@dataclass(frozen=True)
class MomentumSnapshot:
    regime: MomentumRegime | None
    score: float
    coverage_pct: float
    as_of: str | None
    metrics: dict[str, float | bool | None] = field(default_factory=dict)


@dataclass(frozen=True)
class AccountPosition:
    account_id: str
    account_code: str
    broker: str
    quantity: float
    current_value: float
    account_weight_pct: float


@dataclass(frozen=True)
class HoldingRecommendation:
    symbol: str
    instrument_type: InstrumentType
    accounts: list[AccountPosition]
    consolidated_qty: float
    consolidated_value: float
    family_weight_pct: float
    account_weights: dict[str, float]
    action: Action
    sell_type: SellType
    action_confidence: int
    sell_pct: float
    target_weight_pct: float
    expected_3y_irr: ExpectedThreeYearIrr
    scores: Scores
    momentum_regime: MomentumRegime | None
    momentum: MomentumSnapshot
    business_thesis: str
    why_now: str
    hold_until: dict[str, str]
    add_conditions: list[str]
    exit_triggers: list[str]
    tax_note: str
    settlement_note: str
    requires_ca_review: bool
    tax_rule_refs: list[TaxRuleReference]
    replacement_plan: list[dict[str, Any]]
    evidence: list[Evidence]
    data_quality_flags: list[DataQualityFlag]
    rule_trace: list[dict[str, Any]]
    feature_coverage_pct: float


@dataclass(frozen=True)
class AdvisoryPortfolio:
    schema_version: str
    generated_at: str
    source_portfolio_cached_at: str | None
    xirr_status: str
    recommendations: list[HoldingRecommendation]
    full_exit_queue: list[str]
    partial_reduction_queue: list[str]
    conditional_hold_queue: list[str]
    add_build_queue: list[str]
    target_sleeve_allocation: list[dict[str, Any]]
    proceeds_by_account: dict[str, float]
    reinvestment_plan: list[dict[str, Any]]
    overlap_report: list[dict[str, Any]]
    cooldown_warning: str | None


def to_primitive(value: Any) -> Any:
    """Convert advisory dataclasses/enums to a JSON-safe primitive tree."""
    if is_dataclass(value) and not isinstance(value, type):
        return {item.name: to_primitive(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): to_primitive(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_primitive(item) for item in value]
    return value
