# Repository guidance

## Working agreement

- Keep changes small, reviewable, and scoped to the earliest coherent milestone.
- Never commit, push, or open/merge a pull request on the user's behalf. Prepare the change, show the diff, and provide exact commands for the user to run.
- Inspect existing behavior and run the relevant offline tests before and after editing.
- Preserve unrelated local changes. Do not rewrite broker authentication, ingestion, caching, growth analytics, or the stable mobile API without a concrete requirement.
- Keep API v1 changes additive. Do not remove or rename existing response fields; breaking changes require a new contract version.
- Preserve the local-first design. Use the same normalized holdings pipeline as the dashboard; do not create a separate portfolio-fetch path for advisory or LLM features.
- Do not place trades automatically. Execution must remain disabled by default, explicitly confirmed by the user, and auditable.

## Advisory architecture

- Use deterministic analytics first, a versioned structured recommendation second, and LLM explanation last.
- The deterministic payload is the source of truth. The LLM may explain, compare, summarize, and answer follow-ups; it must not invent holdings, prices, filings, scores, expected returns, tax outcomes, or evidence.
- Return an explainable action or an explicit insufficient-data/reconciliation state for every holding. Include confidence, target weight or change, review horizon, invalidation triggers, evidence, as-of dates, and data-quality flags.
- Use bear/base/bull three-year return scenarios with documented assumptions. Treat any target return as a stretch objective, never a guarantee.
- True XIRR requires dated cash flows. Without them, return `unavailable_without_cashflows`; never infer XIRR from unrealized P&L.
- Type every sell-like recommendation as `FUNDAMENTAL_SELL`, `TACTICAL_REDUCE`, or `PORTFOLIO_CONSOLIDATION`.
- Never recommend selling solely because a holding is cyclical, in a loss, or has a large gain. Never recommend averaging solely because price is below cost or waiting for break-even as a thesis.
- Momentum is timing evidence, not business quality. Strong momentum or a live catalyst may change execution timing but cannot erase fundamental or governance risk.
- Treat missing business, valuation, governance, tax, or provenance inputs as `UNKNOWN`; reduce confidence rather than manufacturing a score.
- Handle subscale positions explicitly as build, freeze/no-add, or consolidate. Do not replace many tiny positions with many new tiny positions.
- Apply concentration and overlap at family and account levels. Reinvestment plans must respect target weights, cash buffers, account constraints, and recent-turnover cooldowns.

## Tax, evidence, and privacy

- Make advice account-aware and residency-aware. Keep personal account metadata, tax profiles, holdings, tax lots, identifiers, and overrides in gitignored local configuration or SQLite.
- Do not claim that NRI status makes Indian share gains tax-free. Distinguish withholding/TDS from final tax and account for settlement constraints.
- Never claim a GIFT City product is zero-tax without current product-, share-class-, and investor-specific evidence. Otherwise return `TAX_REVIEW_REQUIRED` and `requires_ca_review`.
- Tax rules must carry jurisdiction, reference, effective date, source, and last-reviewed date. Lot-dependent advice requires FIFO lots and acquisition dates.
- Prefer broker data for positions/lots, official filings and exchanges for company evidence, official AMC/index documents for funds, and official tax/IFSCA sources for tax claims.
- Every external fact used in a recommendation must include its source and as-of date. Expose stale or mismatched evidence instead of hiding it.
- Never add secrets or private family data to tracked files. Keep `.env`, `modules/portfolio/accounts.json`, and `modules/portfolio/data/` untracked.

## Testing invariants

- Tests must be deterministic and must not require live broker, market-data, or LLM APIs.
- Cover cyclicals with strong momentum, winners with improving earnings, NRI and resident loss cases, subscale holdings, governance failures, corporate actions, suspended securities, ETF overlap, GIFT tax evidence, missing cash flows, cooldowns, malformed LLM output, and API v1 compatibility.
