# Signal hierarchy and decision UX

Milestone 12 establishes one semantic contract for Dashboard, Action Center, Today Brief, Portfolio Agent, and weekly output. The system has four signal authorities, in this order:

1. `BLOCKER` — data, research, tax, settlement, and tradability gates; blocks execution but does not rewrite the underlying action.
2. `PRIMARY_DECISION` — deterministic portfolio action from sourced fundamentals, expected return, account constraints, and portfolio fit.
3. `EXECUTION_TIMING` — chart-pattern and momentum timing evidence; never changes action, sell type, target weight, or sell percentage.
4. `CONTEXT_ONLY` — external analyst consensus and published target context; always `actionable=false`.

The older authority values remain accepted for compatibility but are not emitted by new recommendations.

## Shared presentation contract

Every advisory recommendation now includes additive fields:

- `decision_presentation`: the only user-facing action label, action code, readiness, confidence band, immediate instruction, size change, execution timing, reason, and review trigger;
- `signal_stack`: the four signal layers with explicit authority and actionability;
- `external_analyst_view`: consensus, coverage, target gap, freshness, and anomaly status;
- `conflict_categories`: `TIMING_VS_DECISION`, `EXTERNAL_CONTEXT_DIFFERS`, `DATA_BLOCKS_DECISION`, or `TAX_BLOCKS_EXECUTION`.

Internal enum values remain backward-compatible. Examples of the shared presentation mapping:

| Internal action | User-facing decision |
|---|---|
| `STRONG_ADD` | Add more |
| `ADD` | Add gradually |
| `HOLD` | Hold |
| `HOLD_NO_ADD` | Hold — no new money |
| `CAP` | Hold — position full |
| `WATCH` | Wait for evidence |
| `REDUCE` | Trim gradually |
| `SELL` | Exit |
| `RECONCILE` | Fix data first |

Readiness can safely override that label. For example, a screening-model add becomes **Research before adding**, a sell-like decision requiring CA review becomes **Tax review first**, and a reconciliation failure becomes **Fix data first**.

## External analyst semantics

A target-price gap never maps to Buy/Hold/Sell. A consensus label is shown only when Yahoo supplies a covered `recommendationKey` or `recommendationMean`. Target-only data is described as above, near, or below market and remains context only.

Coverage, freshness, and outliers are explicit:

- minimum coverage defaults to three analysts (`EXTERNAL_ANALYST_MIN_COVERAGE`);
- publication age defaults to 120 days (`EXTERNAL_ANALYST_STALE_DAYS`);
- target gaps above +100% or below −50% are outliers by default;
- missing publication dates are labeled unavailable rather than assumed current.

## Dashboard projection and cache

`GET /api/portfolio/advisory/decision-summary` returns an additive `decision-presentation-v1` projection of the same deterministic advisory engine. It:

- joins to Dashboard holdings by canonical `instrument_id`, then ISIN; symbol fallback is limited to unresolved reconciliation rows;
- excludes pattern scans and LLM calls;
- caches by portfolio snapshot, goal update, evidence update, and presentation schema version;
- exposes `patterns_evaluated=false` and `llm_used=false` for auditability.

The Dashboard Decision column and Decision grouping consume this projection. Action Center, Today Brief, and weekly output read the same `decision_presentation.readiness`; they do not maintain separate label maps.

## Expanded holding and execution gate

Expanded holdings follow one hierarchy: **Your decision → Do now → Why → How much → Review when → How to execute**. External analyst context is collapsed and visually neutral. Raw metrics, account breakdowns, charts, and audit evidence follow the decision.

An order-preparation control is rendered only when all of these are true:

- `decision_presentation.readiness == READY_TO_REVIEW`;
- the action is `ADD`, `STRONG_ADD`, `REDUCE`, or `SELL`;
- live trading is explicitly enabled; and
- a supported Zerodha/Groww Indian delivery account is available.

The control is contextual: **Prepare staged add**, **Prepare trim**, or **Prepare exit**. All other readiness states expose review/evidence navigation only. The existing broker confirmation dialog remains the final safety boundary; the deterministic engine never places an order while rendering a recommendation.
