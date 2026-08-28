# Advisor V2 design

Status: Milestones 1–5 implemented; Milestone 6A pattern semantics implemented

Date: 2026-08-28

Scope: deterministic advisory architecture and migration plan; no investment model is implemented by this document

## 1. Repository understanding and baseline

TalkToMyPortfolio is a local-first FastAPI/Jinja application. `portfolio.py` is the canonical ingestion path: it fetches Zerodha, Groww, Sarwa, and custom holdings, normalizes them, enriches them with market data, and persists stale-first snapshots. Both the dashboard and `portfolio_context.py` reuse this family payload. That shared path must remain the only source of portfolio positions.

The current advisory path is:

```text
broker/import holdings
  -> portfolio.py normalization and family cache
  -> Yahoo/AMFI enrichment and analyst signal
  -> portfolio_context.py concentration flags and macro snapshot
  -> portfolio_agent.py prompt
  -> LLM-selected buy/sell/trim/rebalance JSON
```

Existing strengths:

- Local SQLite caches, goals, history, and conversation threads.
- Stale-first portfolio reads with background revalidation.
- A single normalized holdings pipeline shared by UI and agent.
- Multi-provider LLM support with SSE and a usable fallback for malformed JSON.
- Deterministic chart-pattern heuristics and basic concentration flags.
- Stable read-only mobile API v1 with an explicit additive-only rule.
- Trading is separate, disabled by default, and requires explicit confirmation.

Baseline on 2026-08-28:

- Branch: `main`, aligned with `origin/main` at `3297468` (`Add chart pattern detection for holdings`).
- Worktree was already dirty before this milestone. Existing changes affect chart patterns, daily analytics, CSS/JS/templates, and their tests; `data/`, `scripts/diagnose_chart_patterns.py`, and `tests/test_daily_analytics.py` were untracked. This milestone does not modify those files.
- Test suite: 16 tests collected; 16 passed in 1.49 seconds using `.venv/bin/python -m pytest -p no:cacheprovider` with local SQLite write access.
- A sandbox-only run first produced two SQLite read-only errors; these disappeared when the same suite could write its normal local test databases.

## 2. Current-state gaps

| Area | Current behavior | Gap to Advisor V2 |
|---|---|---|
| Decision authority | LLM selects buy/sell/trim actions from contextual holdings | No deterministic action contract or rule trace |
| Returns | Analyst target/upside and historical P&L are exposed | No bear/base/bull three-year IRR model; no probability or XIRR availability state |
| Sell semantics | Generic `trim`, `exit`, or `watch` text | No fundamental/tactical/consolidation taxonomy |
| Momentum | 52-week distance, optional RSI/200-DMA, chart patterns | No unified 1/3/6/12-month relative-strength regime with freshness |
| Fundamentals | Yahoo P/E, target, sector, limited ROCE/debt | Insufficient trend, governance, cash-flow, moat, and provenance coverage |
| Portfolio fit | Single-position and sector limits | No family consolidation, subscale-position math, group/fund overlap, or turnover cooldown |
| Accounts and tax | Broker/code/label plus global goals | No residency, account type, tax profile, settlement, lot sufficiency, or CA-review model |
| Evidence | Macro block has an as-of value; holding metrics largely do not | No claim-level source, observation date, freshness, or conflicting-source policy |
| API/UI | Portfolio, patterns, agent, growth, setup | No additive advisory resource or Action Center |
| Tests | 16 tests cover current chart, growth, import, health, goals/context | Required advisory invariants are not represented |

The current `rating_label` is an analyst-consensus/price heuristic and must not be reused as an Advisor V2 action. The chart-pattern target is a technical measured move and must not be presented as a fundamental three-year return estimate.

## 3. Target architecture

Add `modules/portfolio/services/advisory/` as a pure, dependency-directed package:

```text
canonical family portfolio + local account profiles + sourced observations
  -> normalize/consolidate by security identity
  -> derive typed features and coverage/freshness
  -> expected-return scenarios + momentum regime
  -> deterministic rules and guardrails
  -> overlap, target weights, proceeds, tax/settlement notes
  -> versioned AdvisoryPortfolio payload
  -> additive API and Action Center
  -> portfolio context
  -> LLM explanation of the immutable deterministic result
```

Recommended package ownership:

| Module | Responsibility |
|---|---|
| `models.py` | Enums and versioned typed input/output structures |
| `provenance.py` | Evidence, observation dates, freshness, conflicts, coverage |
| `features.py` | Normalized quality, growth, valuation, governance, liquidity, macro, and fit features |
| `momentum.py` | Total returns, benchmark-relative strength, moving averages, drawdown, volume, chart-pattern adapter |
| `expected_return.py` | Instrument-specific bear/base/bull three-year models |
| `rules.py` | Actions, sell taxonomy, reconciliation and confidence guardrails |
| `overlap.py` | Consolidated security identity, family weights, direct/fund/group overlap |
| `tax.py` | Account/residency rules, lot sufficiency, settlement notes, CA-review flags |
| `rebalance.py` | Target weights, exact account proceeds, replacement sleeves, cooldown |
| `service.py` | Orchestration, schema version, stable serialization, caching |

Dependency rule: advisory modules may consume normalized portfolio services and cached observations; portfolio ingestion must not import advisory modules. This prevents broker refreshes from becoming coupled to recommendation generation.

## 4. Versioned data contract

Use enums plus dataclasses or Pydantic-compatible models, with explicit JSON serialization. Store percentages as percentage points (`18.5`, not `0.185`) at the public boundary and document that convention.

Key enums:

- `Action`: `STRONG_ADD`, `ADD`, `HOLD`, `HOLD_NO_ADD`, `CAP`, `WATCH`, `REDUCE`, `SELL`, `RECONCILE`.
- `SellType`: `NONE`, `FUNDAMENTAL_SELL`, `TACTICAL_REDUCE`, `PORTFOLIO_CONSOLIDATION`.
- `MomentumRegime`: `STRONG`, `POSITIVE`, `NEUTRAL`, `WEAK`, `BROKEN`.
- `InstrumentType`: `equity`, `etf`, `mutual_fund`, `bond`, `gold`, `crypto`, `cash`.
- `DataQualityFlag`: typed code, severity, affected fields, and remediation; do not expose only prose.

`AdvisoryPortfolioV2` should include:

- `schema_version`, `generated_at`, source portfolio cache timestamp, and engine configuration version.
- One consolidated recommendation per security and one position view per account.
- Full-exit, partial-reduction, conditional-hold, and add/build queues represented as references to the same recommendation objects.
- Sleeve targets, exact proceeds by account, account-specific reinvestment plans, overlap findings, and cooldown state.
- Portfolio-level XIRR with `xirr_status`; no estimate when dated cash flows are absent.

Each `HoldingRecommendationV2` should contain the requested symbol, identity, account quantities/weights, action, sell type, confidence, sell percentage, target weight, three scenarios, component scores, momentum, thesis, timing, conditions/triggers, tax/settlement notes, replacement plan, evidence, and quality flags.

Add these implementation fields to make the contract auditable:

- `rule_trace`: ordered rule IDs, inputs, result, and any override applied.
- `feature_coverage_pct`: available weighted features divided by applicable weighted features.
- `evidence_freshness`: `fresh`, `stale`, `mixed`, or `unknown` plus oldest/newest observation dates.
- `model_applicability`: why EPS, cash-flow, NAV, book-value, or fund methodology was selected.
- `manual_overrides_applied`: override ID, local source, effective date, and optional expiry.

The API should use `RECONCILE` for operationally unreliable positions such as corporate-action cost-basis mismatches. Internally, `INSUFFICIENT_DATA` can be a decision state, but the public action should remain `WATCH` with a blocking quality flag unless the instrument needs reconciliation.

## 5. Deterministic algorithm

### 5.1 Identity and consolidation

Resolve a stable security key in this order: ISIN, provider-specific canonical instrument ID, then normalized `(exchange, symbol)`. Preserve original broker symbols. Consolidate quantities and value at family level while retaining account positions and currency. Never merge distinct share classes or similarly named instruments.

Calculate family and account weights from current marked value. A sub-0.5% position receives a `SUBSCALE_POSITION` feature, not an automatic sell. Show contribution math; for example, a 0.2% weight gaining 50% contributes about 10 basis points before interactions.

### 5.2 Feature normalization and scoring

Start with the transparent 100-point allocation:

| Component | Maximum |
|---|---:|
| Quality and capital efficiency | 20 |
| Earnings growth and revisions | 20 |
| Valuation and expected three-year IRR | 20 |
| Momentum and relative strength | 15 |
| Moat and governance | 10 |
| Portfolio fit, concentration, overlap | 10 |
| Macro/FII alignment | 5 |

Each component returns `raw_value`, `normalized_score`, `applicability`, inputs, evidence references, and missing fields. Unknown inputs do not silently receive neutral scores. Calculate both:

```text
covered_score = sum(component score for observed/applicable inputs)
coverage_pct = observed applicable weight / total applicable weight
reported_total = covered_score - explicit stale/conflict penalties
```

Do not extrapolate the covered score to 100 when coverage is low. Score is supporting evidence, not the action selector; hard rules and return bands remain visible.

### 5.3 Expected three-year return

Choose the model by instrument and economic driver:

- EPS model for normal profitable equities.
- Free-cash-flow model where accounting EPS is not representative.
- Book/NAV model for relevant financial or asset-backed businesses.
- Revenue/unit-economics model only when margins and dilution assumptions are explicit.
- Index/fund model based on underlying earnings growth, yield, valuation reversion, fees, tracking difference, and factor exposure.
- No fabricated model when the required driver is missing; return unavailable scenarios and lower confidence.

For the EPS model:

```text
terminal_value = expected_eps_year3 * justified_exit_multiple
irr = ((terminal_value + cumulative_dividends) / current_price) ** (1/3) - 1
```

Bear/base/bull scenarios must show every material assumption. Base must not assume multiple expansion without evidence, and extraordinary quarters must not be mechanically annualized. `probability_above_target` stays `null` until a documented probabilistic method and sufficient history exist.

Default configurable base-IRR bands are: above 25% strong-add candidate, 20–25% add, 16–20% hold/add-on-correction, 12–16% hold/no-add, 8–12% reduce/rotate, and below 8% sell/consolidate. Concentration, governance, liquidity, evidence, tax, and cooldown rules may cap or override the candidate action.

### 5.4 Momentum

Using split/dividend-adjusted daily history where reliable, calculate 1/3/6/12-month total return, benchmark-relative return, 50/200-day state, 52-week-high distance, peak drawdown, and volume confirmation. Reuse chart-pattern output only as one timing input.

Map the observed inputs to `STRONG`, `POSITIVE`, `NEUTRAL`, `WEAK`, or `BROKEN` using a documented table and an as-of date. Missing volume or benchmark data reduces momentum coverage; it does not imply neutral momentum. Momentum may stage an exit or improve an add entry, but cannot convert a governance failure into a hold.

### 5.5 Action rules and guardrail order

Evaluate rules in this precedence order:

1. Identity, price, corporate-action, and tradability checks. Return `RECONCILE` or operational guidance when the position cannot be evaluated or sold normally.
2. High-confidence governance, fraud, solvency, or structural impairment. This alone can produce `FUNDAMENTAL_SELL`.
3. User `do_not_sell_before`, legal, liquidity, account, and settlement constraints.
4. Expected-return band and business-quality evidence.
5. Concentration, valuation, cycle, event, and momentum timing. Reductions here are `TACTICAL_REDUCE`.
6. Family overlap, subscale weight, tax inefficiency, and superior replacements. Exits here are `PORTFOLIO_CONSOLIDATION`.
7. Cooldown suppression unless a hard risk event bypasses it.

Required invariants:

- P&L percentage, purchase price, cyclicality, or a large gain cannot independently produce `SELL`.
- A cyclical with improving sector momentum cannot be classified as a fundamental sell without separate impairment evidence.
- Tax-loss harvesting cannot force an otherwise sound NRI holding out of the portfolio.
- Conflicting or insufficient evidence produces `WATCH`/`HOLD_NO_ADD` with a dated review event, not false precision.
- Every sell-like action has a non-`NONE` sell type; every non-sell action has `NONE`.

### 5.6 Confidence

Action confidence is separate from score. Start from feature coverage, then apply deterministic penalties for stale prices, old filings, conflicting sources, unresolved identity, unsuitable valuation model, missing lots, and unverified tax/product claims. Cap confidence when critical evidence is missing; for example, an action depending on governance evidence cannot be high confidence without a dated authoritative source.

### 5.7 Rebalance and overlap

Rebalancing is a constrained allocation problem over existing high-conviction positions and a small approved sleeve catalog. It must respect account cash availability, currency, tax profile, settlement, cash buffer, max position/sector/group weights, and minimum meaningful position size.

Look-through ETF/fund overlap is computed only when dated constituent data are available. Otherwise report name/mandate-level overlap as provisional and add `LOOKTHROUGH_UNAVAILABLE`. Sale proceeds remain tied to their account until settlement and transfer constraints are satisfied.

Cooldown uses recorded turnover over a configurable window. Recent high turnover suppresses optional rotations, while governance, insolvency, reconciliation, or other hard-risk events bypass it.

## 6. Account and tax model

Extend local account rows backward-compatibly with optional fields:

```json
{
  "owner_ref": "local-owner-key",
  "country_of_residence": "AE",
  "india_residency_status": "NRI",
  "tax_profile": "AE_NRI_INDIA",
  "base_currency": "INR",
  "account_type": "NRO_NON_PIS",
  "risk_profile": "aggressive",
  "target_return_pct": 18,
  "max_position_pct": 12,
  "max_sector_pct": 30,
  "max_group_exposure_pct": 20,
  "cash_buffer_pct": 5,
  "tax_loss_harvesting_mode": "review_only"
}
```

These values belong in gitignored `accounts.json` or local SQLite, never the example with real data. Defaults must reproduce current behavior when fields are absent.

Milestone 2 implements these as optional top-level account-row fields. Legacy rows are
normalized at load time with `UNKNOWN` residency/account/tax values and broker-currency
defaults; the file is not rewritten until profile data are explicitly edited. Invalid
percentages, enums, currencies, country codes, booleans, and incomplete GIFT verification
claims fail closed. The setup API accepts both top-level fields and a nested
`account_profile` object.

Raw owner, residency, and tax-profile fields are deliberately not copied into the general
portfolio payload or LLM context. The deterministic engine can consume an explicit
`account_profile` on an account block, but external-model disclosure requires a separate,
explicit opt-in design. Account codes remain the only account identifiers in recommendation
positions.

Tax knowledge is versioned data, not prompt prose. A rule record contains jurisdiction, account/instrument applicability, rule/reference, effective-from/to, authoritative source URL, last-reviewed date, and required inputs. Outputs are planning notes. Set `requires_ca_review=true` when lots, treaty status, exact product/share class, or another decisive fact is missing.

The Milestone 2 rule dataset covers Indian share/loss lot requirements, NRI withholding,
RBI NRI settlement/repatriation classification, IFSCA product-level evidence, and possible
U.S.-situs estate exposure. Recommendation tax output includes `requires_ca_review` and
`tax_rule_refs`; it never computes tax from broker average-price P&L.

The engine must distinguish NRO Non-PIS, NRE-PIS, resident demat, GIFT IBU, and global brokerage settlement and movement constraints. It must not equate NRI status with zero Indian tax, TDS with final liability, or `GIFT City` in a product name with verified tax treatment. Track U.S.-situs estate exposure and dividend withholding separately from capital gains for global accounts.

## 7. Additive API and UI plan

Keep every endpoint in `docs/api-contract-v1.md` unchanged. Add an advisory contract version independent of mobile v1:

- `GET /api/portfolio/advisory` returns `AdvisoryPortfolioV2`.
- `GET /api/portfolio/advisory/{symbol}` returns one consolidated recommendation plus account positions.
- `POST /api/portfolio/advisory/rebalance` evaluates a proposed target plan; it never places orders.
- `GET /api/portfolio/advisory/deadlines` returns result/event/review deadlines.

Use `ETag` or a request fingerprint derived from holdings snapshot, account profile, engine configuration, overrides, and source observations. Do not regenerate advice merely because the user refreshes the UI.

The Action Center consumes the same payload and adds no decision logic in JavaScript. It supports action/sell-type filters, deterministic sorting, scenario returns, confidence/coverage, momentum, hold-until, conditions/triggers, tax/settlement flags, proceeds, replacements, overlap, and a dated evidence drawer.

## 8. LLM integration boundary

After the deterministic vertical slice exists, `portfolio_context.py` should attach the complete advisory payload, account profile summaries, evidence timestamps, and quality flags. `portfolio_agent.py` should treat that payload as immutable source material.

Add a versioned response envelope and provider-independent validation. If the model output is malformed, return the deterministic recommendations plus a plain explanatory fallback; never lose the underlying action center. For a symbol-specific question, select that recommendation and relevant evidence rather than resending a generic portfolio narrative.

The LLM may not upgrade `WATCH`/insufficient data into buy or sell, calculate an undocumented return, invent a tax result, or claim a guaranteed outcome. Explanation text must identify whether a reduction is fundamental, tactical, or consolidation-driven.

## 9. Migration strategy

1. Add the advisory package and tests without changing existing endpoints or UI. Accept the current family payload through an adapter.
2. Implement pure deterministic models for currently available inputs. Missing fundamentals and provenance remain explicit quality flags.
3. Extend account configuration with optional fields; preserve old JSON and Setup behavior. Add validation and examples using placeholders only.
4. Add additive API endpoints behind a feature flag until the schema stabilizes. Snapshot-test existing API v1 fields.
5. Add the Action Center as a new route/page using the advisory API.
6. Add pluggable cached sources and observation records; do not bind source clients directly into rule functions.
7. Move the conversational agent to deterministic-source-of-truth behavior after API results are stable.

SQLite migrations must be idempotent and transactional. Prefer additive tables/columns with defaults. Back up user databases before any transformation that cannot be represented as a simple additive migration. Never copy personal values into fixtures.

## 10. Privacy and security implications

- The advisory payload is more sensitive than the current holdings view because it combines ownership, residency, tax profile, lots, constraints, and recommendations. Keep it local by default and apply the existing HTTP-auth perimeter to all new routes.
- Minimize LLM context: send account aliases and necessary constraints, not names, account numbers, tax IDs, raw statements, or broker tokens.
- Keep evidence caches separate from credentials. Never log provider keys, raw OAuth responses, tax IDs, account numbers, full LLM context, or imported statements.
- Validate all local overrides and record their source/effective date. Treat CA approval as a local attestation, not a globally reusable tax fact.
- Root-level `data/` is currently untracked but is not covered by the repository's portfolio-data ignore rule. Confirm it contains no private or generated artifacts before any commit, then ignore or relocate it if it is runtime data.
- Existing plaintext broker-token storage remains a known risk documented in `docs/security.md`; Advisor V2 must not widen access to those databases.

## 11. Test strategy

Milestone 1 should use fixtures with synthetic prices, fundamentals, evidence, accounts, and lots. No test may call a broker, Yahoo, tax site, or LLM. Add the 14 mandatory scenarios as focused rule tests, then add:

- Schema serialization and enum validation.
- Percentage-unit and currency invariants.
- Evidence freshness/conflict penalties.
- Identity consolidation across accounts without merging share classes.
- Rule-trace stability and sell-type consistency.
- Additive API v1 snapshot/field compatibility.
- Rebalance constraints and no-microscopic-replacement invariant.

Tests set `PORTFOLIO_DATA_DIR` to a session-scoped temporary directory before application modules load. SQLite-writing tests therefore cannot mutate the developer's real portfolio databases. The runtime default remains `modules/portfolio/data/` when the override is absent.

## 12. Milestone 1 outcome and next recommendation

Milestone 1 implements the offline deterministic vertical slice under
`modules/portfolio/services/advisory/`:

1. Typed models, evidence/quality flags, ISIN-first consolidation, explicit overlap, and a versioned service envelope.
2. EPS and fund build-up scenario interfaces plus price-history momentum; unavailable inputs remain explicit rather than inferred.
3. Sell taxonomy, subscale and concentration rules, reconciliation/tradability states, confidence caps, and turnover cooldown.
4. An additive `advisory` block in portfolio-agent context without changing API v1.
5. Malformed LLM JSON fallback that preserves deterministic recommendations.
6. Synthetic, no-network behavioral tests for the mandatory failure modes.

Milestone 1 intentionally does not add live fundamental, tax, macro, or fund-constituent providers. Most current holdings will return `WATCH` or `HOLD_NO_ADD` until documented return assumptions and evidence are available.

Milestones 2–5 subsequently added backward-compatible account/tax profiles,
versioned rules, provider evidence, pattern conflict handling, the constrained
agent boundary, and the local Action Center. Real residency, account, lot, and tax
values remain in gitignored local storage.

## 13. Milestone 6A: lifecycle-safe technical evidence

The chart detector remains execution-timing evidence. Its legacy `status` and
`confidence` fields remain available, but the deterministic engine now consumes
`lifecycle_state`, `target_status`, and `heuristic_score`.

- `early` maps to `BUILDING`; `forming` maps to `NEAR_BREAKOUT`; `confirmed`
  maps to `CONFIRMED` unless target-completion or expiry logic changes it.
- Only a fresh `CONFIRMED` or `RETESTING` setup with an `ACTIVE` target and a
  sufficient heuristic score is eligible to stage execution. Building, completed,
  expired, failed, or invalidated setups are evidence only.
- A bullish current price at/above target becomes `TARGET_ACHIEVED`; at least 3%
  beyond target becomes `TARGET_OVERSHOT`. Bearish targets use symmetric rules.
  Completed targets expose zero remaining upside/downside.
- `confidence` is a deprecated shape score. UI text uses `heuristic_score/100`;
  `calibrated_target_hit_probability` remains null until the Stage 6E out-of-sample
  calibration has adequate samples.
- Exact target dates are no longer asserted. `estimated_horizon` returns a broad
  minimum/median/maximum trading-session range using
  `heuristic_until_calibrated`.
- Pattern prices use an explicit ISO currency from the holding where available,
  otherwise an exchange mapping (U.S. exchanges to USD; NSE/BSE to INR).

Stage 6A deliberately does not add OHLCV confirmation, ATR, invalidation math,
retests, relative strength, a separate technical-overlay matrix, persistence, or
calibrated probabilities. Those remain Stages 6B, 6C, and 6E respectively. See
`docs/pattern-execution-overlay.md` for the contract and transition policy.
