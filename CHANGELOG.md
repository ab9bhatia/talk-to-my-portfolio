# Changelog

All notable changes to this project will be documented in this file.

The format is inspired by Keep a Changelog and semantic versioning.

## [Unreleased]

### Milestone 10 — portfolio operating console

- Added a material-change-first Today Brief with hard-risk/reconciliation priority and explicit no-action majority.
- Added transparent versioned stress scenarios across direct and fund look-through exposure with family/account impact, liquidity, coverage, and limitations.
- Added immutable what-if simulation with position/sector/group/small-cap/cash/turnover/candidate/tax constraints and before/after evidence.
- Added material-event alerts with persisted hysteresis/cooldown; trivial price moves never alert.
- Added the original Today Brief, stress viewer, what-if builder, alert center, and additive read-only APIs.

### Milestone 9B — ETF and mutual-fund look-through

- Added a sourced scheme master that keeps Direct/Regular and Growth/IDCW economic instruments distinct.
- Added dated full/partial/top-holdings constituent ingestion and recursive fund-of-fund look-through with cycle protection.
- Replaced inferred name overlap with constituent overlap where available, plus explicit unavailable/stale/partial states, direct-stock duplication, and value-conserving family exposure.
- Added weighted TER, ETF liquidity/premium/tracking analytics, mutual-fund rolling analytics, tax-aware consolidation candidates, Fund Intelligence UI, and audit workbook.

### Milestone 9A — research workspace

- Added instrument-specific transparent scorecards for corporate, financial, fund, listed-yield, cyclical, pre-profit, and risk-sleeve instruments.
- Added a safe typed AND/OR screener, local schema migration, append-only saved-screen revisions, and evidence-based elimination reasons.
- Added explained two-to-five instrument comparisons, explicitly approved candidate universe, watchlists, append-only thesis history, and sourced event calendar.
- Added structured redacted LLM research context and original Research/scorecard UI; research evidence cannot invent or override deterministic actions.

### Milestone 8 — Market Regime & Mood Index

- Added the original India MRMI with eight documented, configurable components, missing-data reweighting, freshness/disagreement confidence, stable bands, and source provenance.
- Added append-only methodology-versioned history, transitions, provisional/final/backfilled states, original UI, and finalized weekly-digest integration.
- Added a deterministic advisory execution overlay that changes tranche/pace/cash/priority only and cannot create or cancel BUY/SELL.
- Added a no-look-ahead research harness with forward-return bands and a preserved final-test partition.

### Milestone 7C — transaction ledger and true performance

- Added preview-first, idempotent, reversible transaction imports with unresolved-row review and source lineage.
- Added account-specific FIFO lots, fee/tax cost policy, transfer history flags, split/bonus transforms, and demerger cost-allocation blocking.
- Added coverage-aware XIRR/MWRR, TWRR, realized/unrealized return, income, FX, fee/tax drag, return bridge, and date-aligned benchmark attribution.
- Upgraded Growth and weekly digest to show true performance only when dated cash-flow and valuation evidence permits.
- Added transaction, lot, performance, coverage, attribution, rollback, and audit-workbook APIs without changing API v1.

### Milestone 7B — canonical identity and reconciliation

- Added a versioned local instrument master, exchange-aware aliases, stable canonical IDs, corporate-action lineage, and audited sourced overrides.
- Kept broker-reported price/value/P&L separate from market marks and derived values with source, timestamp, session, currency, and FX provenance.
- Added account, security, and family reconciliation states, value-weighted quality coverage, and deterministic advisory blocking through `RECONCILE`.
- Added the Data Quality Center, additive reconciliation APIs, and canonical/reconciliation columns in Excel export.
- Added deterministic coverage for identity collisions, mutual funds, U.S. ETFs, timing/FX/value mismatches, corporate actions, suspension, overrides, advisory blocking, and family totals.

### Milestone 7A.1 — weekly-sync stabilization

- Split Friday India close and Saturday global finalization into typed, independently idempotent market-session stages.
- Persisted async `QUEUED` truth before executor submission, startup orphan recovery, and one coalesced forced post-OAuth rerun.
- Added snapshot quality, account coverage, market-session date, and comparability metadata to daily/weekly history.
- Made Growth suppress performance claims across degraded or coverage-changed observations.
- Serialized timed-out workers until exit, canonically aggregated every family snapshot path, and added bounded deduplicated quote coverage reporting.
- Expanded the conservative weekly digest, CI quality gates, API/UI status, deterministic regression suite, and operating documentation.

### Milestone 6A — pattern semantics and experience
- Added lifecycle-aware pattern results (`BUILDING`, `NEAR_BREAKOUT`, `CONFIRMED`,
  `TARGET_ACHIEVED`, `TARGET_OVERSHOT`, and `EXPIRED`) plus explicit target state.
- Reframed detector confidence as a heuristic shape score (`x/100`) and added an
  always-null calibrated probability until out-of-sample calibration exists.
- Replaced false-precision target dates with broad trading-session ranges and
  added instrument currency so U.S. targets render in USD and Indian targets in INR.
- Retained legacy pattern response fields additively; completed targets remain
  visible for audit but cannot postpone a deterministic sale or reduction.
- Redesigned the dashboard pattern radar and Advisor Action Center with lifecycle
  filters, currency-safe targets, responsive decision cards, and clearer policy cues.
- Added synthetic regression coverage for active/completed/expired targets,
  symmetric bearish completion, currency selection, horizon ranges, and legacy fields.

### Changed
- Consolidated documentation: `code_flow_and_index.md`, `docs/product.md`, slimmer README.
- Removed duplicate / obsolete docs and dev spike scripts.
- Removed KMCP/kagent docs and `POST /api/portfolio/kmcp/invoke` (not in scope for now).
- Removed README screenshot assets and Playwright capture scripts.

### Added
- Chart pattern detection for holdings: heuristic recognition of inverse head &
  shoulders, head & shoulders, double bottom, cup with handle, and ascending
  triangle from daily Yahoo OHLC history. Returns bias, status (early/forming/
  confirmed), neckline, target price, upside-to-target, confidence, and anchor
  points for chart overlays.
  - New service `modules/portfolio/services/chart_patterns.py` with a fixed
    ~1-year lookback, a max-reversal-span cap, and a recency gate so only fresh
    setups surface.
  - New endpoints `GET /api/portfolio/patterns` (scan all holdings) and
    `GET /api/portfolio/patterns/{symbol}`.
  - Holdings UI: inline pattern pills, a "Setups" toolbar filter, and an overlay
    chart in the row detail that draws the detected pattern's geometry
    (pivots, neckline, target) over price history for visual verification.
  - Dashboard "Chart patterns" scan panel.
  - Unit tests in `tests/test_chart_patterns.py`.
- Configurable app root path: the portfolio is served under
  `APP_ROOT_PATH` (default `/talktomyportfolio`), e.g.
  `http://127.0.0.1:9000/talktomyportfolio`. Added `app_path()` template helper
  and `app-root.js` to prefix client-side fetches.
- Portfolio goals and guardrails API + dashboard controls.
- Import quality audit trail endpoint and dashboard visibility.
- Growth analytics enhancements (indexed performance, account mix, date-wise table).
- Daily history Google Sheet importer endpoint.
- CI workflow, Dockerfile, baseline tests, and release checklist docs.

### Fixed
- Chart patterns endpoint returned 500 (`Out of range float values are not JSON
  compliant: nan`) when Yahoo history contained gap/halted days. NaN bars are
  now dropped on load and a finite-value guard prevents non-serializable
  pattern fields.
- Route ordering: `/api/portfolio/patterns` is now matched before the
  `/api/portfolio/{account_ref}` catch-all (previously caused "Unknown
  account" / internal errors on scan).
### Milestone 11A — after-tax asset location

- Extended account profiles with repatriability, instrument eligibility, estate-review state, and explicit family-transfer policy.
- Added a date-enforced, sourced tax-rule registry plus bear/base/bull after-tax comparisons that remain unknown when product, domicile, treaty, or account evidence is incomplete.
- Added contribution-first asset-location recommendations and FIFO harvesting review without assuming tax-free family transfers or finality of broker TDS.
- Added the Asset Location page, additive APIs, and a CA-review workbook containing rules, assumptions, lots, and actions; execution and ITR filing remain disabled.
### Milestone 11B — security, backup, recovery, observability, and release hardening

- Added explicit preview/confirm/verify/rollback migration from plaintext token columns to OS secret storage or an AES-GCM fallback, plus rotation/revocation primitives and restrictive file permissions.
- Added dynamic Basic/Bearer auth, CSRF/origin enforcement, endpoint rate limits, security headers, stricter upload validation, centralized redaction, and exact default-deny LLM context preview.
- Added schema metadata/integrity refusal for every local SQLite store and transactional backup-before-migrate behavior.
- Added encrypted checksummed backup, selective staged restore, dry-run validation, redacted diagnostics/support bundle, provider latency/failure metadata, System Health UI, recovery CLI, and incident playbook.
- Pinned dependencies and expanded CI with vulnerability, secret, Bandit, coverage, static, syntax, and deterministic test gates.
