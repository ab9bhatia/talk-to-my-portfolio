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


class SignalAuthority(StrEnum):
    INTERNAL_DECISION = "INTERNAL_DECISION"
    EXTERNAL_ANALYST_CONTEXT = "EXTERNAL_ANALYST_CONTEXT"
    TECHNICAL_TIMING = "TECHNICAL_TIMING"
    EXECUTION_READINESS = "EXECUTION_READINESS"


class DecisionReadiness(StrEnum):
    READY_TO_REVIEW = "READY_TO_REVIEW"
    RESEARCH_REQUIRED = "RESEARCH_REQUIRED"
    DATA_BLOCKED = "DATA_BLOCKED"
    TAX_REVIEW_REQUIRED = "TAX_REVIEW_REQUIRED"
    NOT_EXECUTABLE = "NOT_EXECUTABLE"
    MONITOR_ONLY = "MONITOR_ONLY"


class ConfidenceBand(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class ConflictCategory(StrEnum):
    DATA_QUALITY = "DATA_QUALITY"
    FUNDAMENTAL_VS_TECHNICAL = "FUNDAMENTAL_VS_TECHNICAL"
    INTERNAL_VS_EXTERNAL = "INTERNAL_VS_EXTERNAL"
    TAX_OR_SETTLEMENT = "TAX_OR_SETTLEMENT"


class ExternalAnalystStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    LOW_COVERAGE = "LOW_COVERAGE"
    OUTLIER = "OUTLIER"
    CONFLICTED = "CONFLICTED"


class ExternalAnalystSentiment(StrEnum):
    POSITIVE = "POSITIVE"
    NEUTRAL = "NEUTRAL"
    NEGATIVE = "NEGATIVE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ExternalAnalystView:
    status: ExternalAnalystStatus
    sentiment: ExternalAnalystSentiment
    consensus_label: str | None
    recommendation_key: str | None
    recommendation_mean: float | None
    analyst_count: int | None
    target_price: float | None
    target_gap_pct: float | None
    target_descriptor: str
    coverage_label: str
    freshness_label: str
    as_of: str | None
    fetched_at: str | None
    source: str
    actionable: bool = False


@dataclass(frozen=True)
class SignalLayer:
    authority: SignalAuthority
    label: str
    state: str
    summary: str
    actionable: bool


@dataclass(frozen=True)
class DecisionPresentation:
    internal_action: Action
    label: str
    short_label: str
    readiness: DecisionReadiness
    readiness_label: str
    confidence_band: ConfidenceBand
    confidence_pct: int
    do_now: str
    change_instruction: str
    review_trigger: str
    why: str
    source_label: str
    execution_enabled: bool = False


@dataclass(frozen=True)
class SignalStack:
    primary: SignalAuthority
    layers: list[SignalLayer]


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
    applicability: list[str] = field(default_factory=list)
    calculation_method: str = "review_only"
    confidence: str = "documented"
    ca_review_required: bool = True


@dataclass(frozen=True)
class ExpectedThreeYearIrr:
    bear_pct: float | None
    base_pct: float | None
    bull_pct: float | None
    probability_above_target: float | None
    method: str
    assumptions: list[str] = field(default_factory=list)
    evidence_tier: str = "documented"

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
class ChartPatternEvidence:
    """Primary chart setup normalized as execution-timing evidence only."""

    pattern: str
    label: str
    bias: str
    lifecycle_state: str
    target_status: str
    status: str
    confidence: float
    heuristic_score: float
    confidence_semantics: str
    calibrated_target_hit_probability: float | None
    sample_size: int | None
    as_of: str | None
    currency: str
    current_price: float | None
    target_price: float | None
    measured_target: float | None
    target_date: str | None
    upside_to_target_pct: float | None
    remaining_upside_pct: float | None
    remaining_downside_pct: float | None
    estimated_horizon: dict[str, Any]
    note: str
    active: bool
    stale: bool


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
    instrument_id: str | None
    isin: str | None
    reconciliation_state: str | None
    reconciliation_delta: float | None
    reconciliation_blocking: bool
    instrument_type: InstrumentType
    accounts: list[AccountPosition]
    consolidated_qty: float
    consolidated_value: float
    family_weight_pct: float
    account_weights: dict[str, float]
    action: Action
    evidence_state: str
    sell_type: SellType
    action_confidence: int
    sell_pct: float
    target_weight_pct: float
    expected_3y_irr: ExpectedThreeYearIrr
    scores: Scores
    momentum_regime: MomentumRegime | None
    momentum: MomentumSnapshot
    chart_pattern: ChartPatternEvidence | None
    decision_conflicts: list[str]
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
    recommendation_as_of: str
    decision_presentation: DecisionPresentation | None = None
    signal_stack: SignalStack | None = None
    external_analyst_view: ExternalAnalystView | None = None
    conflict_categories: list[ConflictCategory] = field(default_factory=list)


@dataclass(frozen=True)
class AdvisoryPortfolio:
    schema_version: str
    generated_at: str
    source_portfolio_cached_at: str | None
    portfolio_value: float
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
    deadlines: list[dict[str, Any]]
    evidence_status: dict[str, Any]


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
