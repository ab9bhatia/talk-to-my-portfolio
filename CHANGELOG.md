# Changelog

All notable changes to this project will be documented in this file.

The format is inspired by Keep a Changelog and semantic versioning.

## [Unreleased]

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
