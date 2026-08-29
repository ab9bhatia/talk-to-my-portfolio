# Signal hierarchy and decision UX

Milestone 12 establishes one semantic contract for Dashboard, Action Center, Today Brief, and weekly output. The system has four signal authorities, in this order:

1. `INTERNAL_DECISION` — deterministic portfolio action from sourced fundamentals, expected return, account constraints, and portfolio fit.
2. `EXTERNAL_ANALYST_CONTEXT` — covered analyst consensus and published target context. It is always `actionable=false`.
3. `TECHNICAL_TIMING` — chart-pattern timing evidence. It may stage timing but never creates or reverses the internal decision.
4. `EXECUTION_READINESS` — data, research, tax, settlement, and tradability gates. It can block presentation of an otherwise valid action.

## Shared presentation contract

Every advisory recommendation now includes additive fields:

- `decision_presentation`: the only user-facing action label, readiness, confidence band, immediate instruction, reason, and review trigger;
- `signal_stack`: the four signal layers with explicit authority and actionability;
- `external_analyst_view`: consensus, coverage, target gap, freshness, and anomaly status;
- `conflict_categories`: typed disagreements such as `FUNDAMENTAL_VS_TECHNICAL` or `INTERNAL_VS_EXTERNAL`.

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

## Safety boundary

Milestone 12A/12B does not add order preparation or placement. Dashboard row expansion routes to Action Center and explicitly keeps order controls gated. The payload continues to expose `execution_enabled=false`.
