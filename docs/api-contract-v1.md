# API Contract V1 (Mobile MVP)

Contract version: `2026-05-mobile-mvp-v1`  
Version endpoint: `GET /api/portfolio/version`

Product scope (read-only mobile + agent): [product.md](product.md#roadmap-product-lens)

## Stable endpoints for Android MVP

### Health and version
- `GET /health`
- `GET /api/portfolio/version`

### Portfolio reads
- `GET /api/portfolio`
- `GET /api/portfolio/account/{account_ref}`
- `GET /api/portfolio/meta`
- `GET /api/portfolio/daily/dashboard?days=...`
- `GET /api/portfolio/daily/history?scope=...&days=...`

### Agent
- `GET /api/portfolio/agent/status`
- `GET /api/portfolio/agent/sessions`
- `GET /api/portfolio/agent/sessions/{thread_id}`
- `POST /api/portfolio/agent/ask`
- `POST /api/portfolio/agent/ask/stream` (SSE)

### Deferred (web only for now)
- `POST /api/portfolio/trade/*` (if enabled)
- setup/update/import endpoints under `/api/portfolio/setup/*`

### Additive weekly-sync operations

- `GET /api/portfolio/sync/status`
- `GET /api/portfolio/sync/runs?limit=20`
- `GET /api/portfolio/sync/runs/{run_id}`
- `POST /api/portfolio/sync/weekly` with `{"mode":"auto|live|safe-fallback","dry_run":false,"stage":null|"INDIA_CLOSE"|"GLOBAL_CLOSE_FINALIZATION"|"MANUAL_RERUN"}`
- `POST /api/portfolio/sync/weekly/async` returns `202` plus a stable `run_id`
- `GET /api/portfolio/sync/jobs/{run_id}` polls queued/running/terminal progress

The sync operation records portfolio history and local audit/digest artifacts. It never submits orders. Account results expose account codes, freshness timestamps, state, and recovery action—not internal account IDs or secrets.
Zerodha OAuth completion queues one forced refresh so an earlier same-week run cannot hide a newly reconnected account; scheduled and ordinary manual runs retain weekly idempotency.

Sync responses add `stage`, `durable_queue_status`, `market_session_date`, `snapshot_quality`, `comparability`, `rerun_required`, and `followup_run_id`. Existing fields remain unchanged. Snapshot history rows add coverage/quality metadata and machine-readable comparability reasons.

### Additive instrument and reconciliation reads

- `GET /api/portfolio/instruments?query=...&limit=...`
- `POST /api/portfolio/instruments/resolve`
- `GET /api/portfolio/reconciliation/summary`
- `GET /api/portfolio/reconciliation/detail?instrument_id=...&account_code=...`
- `GET /api/portfolio/reconciliation/unresolved`
- `GET /api/portfolio/reconciliation/corporate-actions`
- `POST /api/portfolio/reconciliation/overrides`

These endpoints are additive to API v1. They expose account codes rather than private account IDs. Manual overrides require reason, source document, evidence date, and approver and remain local/audited.

### Additive transaction and performance APIs

- `POST /api/portfolio/transactions/import/preview`
- `POST /api/portfolio/transactions/import/{batch_id}/commit`
- `POST /api/portfolio/transactions/import/{batch_id}/rollback`
- `GET /api/portfolio/transactions`
- `GET /api/portfolio/transactions/unresolved`
- `GET /api/portfolio/lots`
- `GET /api/portfolio/performance/summary`
- `GET /api/portfolio/performance/series`
- `GET /api/portfolio/performance/attribution`
- `GET /api/portfolio/performance/coverage`
- `GET /api/portfolio/performance/audit.xlsx`

Import is preview-first and reversible. Public responses expose account codes, coverage, exclusions, and disclaimers; private account IDs remain local. XIRR/TWRR are unavailable rather than inferred when cash-flow or valuation evidence is insufficient.

### Additive market-regime APIs

- `GET /api/portfolio/market-regime/current?finalized_only=...`
- `GET /api/portfolio/market-regime/history?limit=...`
- `GET /api/portfolio/market-regime/methodology`
- `POST /api/portfolio/market-regime/observations`

MRMI observations expose methodology version, component source/as-of/freshness, coverage, flags, and observation state. They are execution/sizing context only and never enable trading or originate an investment action.

### Additive research APIs

- `POST /api/portfolio/research/scorecards`
- `POST /api/portfolio/research/screens/run`
- `GET|POST /api/portfolio/research/screens`
- `GET|POST /api/portfolio/research/candidates`
- `GET|POST /api/portfolio/research/watchlist`
- `GET /api/portfolio/research/thesis/{instrument_id}`
- `POST /api/portfolio/research/thesis`
- `GET|POST /api/portfolio/research/events`
- `POST /api/portfolio/research/compare`

The screening contract is a whitelisted JSON DSL, not executable text. Candidate eligibility is explicit, thesis and screen revisions are auditable, and optional LLM context is structured/redacted.

### Additive fund-intelligence APIs

- `GET|POST /api/portfolio/funds/schemes`
- `POST /api/portfolio/funds/holdings`
- `GET /api/portfolio/funds/{instrument_id}/lookthrough`
- `GET /api/portfolio/funds/overlap/pair`
- `GET /api/portfolio/funds/family`
- `GET /api/portfolio/funds/consolidation`
- `GET /api/portfolio/funds/audit.xlsx`

Overlap is numeric only when dated constituents exist. Responses preserve coverage, source freshness, unresolved labels, and tax/exit-load review requirements.

### Additive operating-console APIs

- `GET /api/portfolio/today-brief`
- `GET /api/portfolio/stress/scenarios`
- `POST /api/portfolio/stress/run`
- `POST /api/portfolio/what-if`
- `GET /api/portfolio/alerts`
- `POST /api/portfolio/alerts/evaluate`

Every stress/what-if/alert response is deterministic, traceable, and includes `execution_enabled: false`. Simulations never mutate canonical holdings.

### Additive after-tax asset-location APIs

- `GET /api/portfolio/tax/rules?as_of=YYYY-MM-DD`
- `POST /api/portfolio/tax/after-tax`
- `POST /api/portfolio/tax/asset-location`
- `POST /api/portfolio/tax/harvest`
- `GET /api/portfolio/tax/ca-package.xlsx`

Tax outcomes are `AVAILABLE`, `UNKNOWN`, or `TAX_REVIEW_REQUIRED`. Every available outcome includes dated evidence and scenario drag components. Planning responses never enable execution, infer a transfer, or represent a filed tax calculation.

### Additive security and recovery APIs

- `GET /api/portfolio/security/csrf`
- `GET /api/portfolio/security/secrets/migration-preview`
- `POST /api/portfolio/security/secrets/migrate|rollback`
- `DELETE /api/portfolio/security/secrets/{store}/{account_id}`
- `POST /api/portfolio/security/backup|restore`
- `GET /api/portfolio/security/privacy`
- `GET /api/portfolio/security/llm-context-preview`
- `GET /api/portfolio/security/diagnostics`
- `GET /api/portfolio/security/support-bundle.zip`

State-changing Basic Auth requests require same-origin evidence or `X-Portfolio-CSRF`; bearer clients are not vulnerable to ambient browser credentials. Backups and support artifacts never contain raw credential stores.

## Compatibility rules
- Additive changes only within this version.
- No field removals/renames in listed endpoints.
- Breaking changes require a new contract version string.
