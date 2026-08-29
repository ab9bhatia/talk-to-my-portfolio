# Product quality review — 2026-08-29

## Outcome

The daily workflow now leads with root causes and decisions instead of raw evidence. Cached portfolio views are fast, deterministic setup scanning is non-blocking and durable for 24 hours, reconciliation explains its data lineage, and empty intelligence modules give a truthful next step.

No broker credentials, owner names, account residency values, or portfolio data are stored in this document. Account profiles remain in the local gitignored database.

## Reviewed journey

1. **Setup once:** connect accounts; set family defaults; complete each account's residency, country, account type, repatriability, and stricter guardrails.
2. **Refresh daily:** Dashboard loads the saved family snapshot first and refreshes live evidence explicitly.
3. **Triage:** Today Brief groups repeated warnings into what is wrong, why it matters, and required action.
4. **Establish trust:** Data Quality compares immutable broker snapshot value with independent market value and offers only append-only sourced explanations.
5. **Decide:** Action Center defaults to decisions requiring action and shows action, why, required next step, confidence, scenario, allocation fit, and timing.
6. **Investigate selectively:** Research starts from held exposure; Fund Intelligence identifies wrappers that still need dated factsheet mapping.
7. **Locate:** Asset Location applies residency/account eligibility and account-level guardrails after a desired exposure is known.
8. **Ask:** Portfolio Agent explains the deterministic queue. A provider 429 degrades to local decisions rather than an empty response.
9. **Review weekly:** Growth remains the trend/history surface, not the daily command center.

## Performance findings

| Interaction | Verified result |
|---|---:|
| Flat Dashboard navigation | 265 ms |
| Group by account | 312 ms |
| Group by sector | 262 ms |
| Group by market cap | 244 ms |
| Group by asset class | 243 ms |
| Group by Street signal | 296 ms |

These are warm local-browser measurements on the current saved family snapshot. The family finalization cache prevents quote consensus and reconciliation from being recomputed for each group switch. Row metadata is cached in-browser, the account allocation overview does not require Chart.js, and holding rows are paginated.

Data Quality renders the 60 highest-priority securities by default. The complete list remains available through **Show all**. At a `390 × 844` viewport, Today Brief, Action Center, and the default Data Quality view have no page-level horizontal overflow. The mobile navigation is a compact horizontal strip instead of a full-height menu.

## Setup scanning

Setup detection is not an LLM task and cannot run on an interactive Codex session in production. It uses deterministic daily OHLC data from Yahoo Finance, runs after the decision queue is usable, and stores results in local `pattern_cache.db` for 24 hours. The key includes detector version and the portfolio universe, so code/universe changes invalidate old evidence safely.

## Reconciliation lineage

- **Broker snapshot value:** broker/import quantity, value, price, source, and capture time. It is immutable evidence.
- **Independent marked value:** canonical quantity multiplied by the sourced market price, with dated FX conversion when required.
- **Review mismatch:** explains likely cause and recommended checks before input is accepted.
- **Save resolution:** inserts a typed row into `reconciliation_overrides` plus its audit row. It may lower an explained block to a warning; it never deletes or overwrites either source value.

## Empty intelligence modules

- **Market Mood:** remains unscored until broad-market breadth, index momentum, volatility, flows, participation, derivatives, valuation, and liquidity sources are approved and dated. Holdings and LLM credits are not required.
- **Research:** an empty candidate database now starts from the family's highest-value held exposures. Candidate approval remains separate from portfolio actions.
- **Fund Intelligence:** known broker wrappers and values are visible. TER, constituents, overlap, and liquidity remain blank until each wrapper is mapped to a dated AMC/index factsheet.

## Portfolio Agent failure mode

HTTP 429 means the configured external provider rejected the request for rate/quota reasons; it is not a failure of the local portfolio API. The UI now says this explicitly and returns the highest-priority deterministic local actions in degraded mode. No live provider request is made by the regression test.

## Verification

```text
JavaScript syntax checks: passed
Python regression suite: 254 passed
Desktop route and primary-control inspection: passed
Mobile 390 × 844 core-flow inspection: passed after one overflow fix
Order placement: not exercised; execution remains disabled
External LLM request: not exercised
```

Visual evidence was captured during the review under `/tmp/ttmp-product-audit/` and is intentionally not committed.
