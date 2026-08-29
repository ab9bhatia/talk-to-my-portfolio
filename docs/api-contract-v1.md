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
- `POST /api/portfolio/sync/weekly` with `{"mode":"auto|live|safe-fallback","dry_run":false}`
- `POST /api/portfolio/sync/weekly/async` returns `202` plus a stable `run_id`
- `GET /api/portfolio/sync/jobs/{run_id}` polls queued/running/terminal progress

The sync operation records portfolio history and local audit/digest artifacts. It never submits orders. Account results expose account codes, freshness timestamps, state, and recovery action—not internal account IDs or secrets.
Zerodha OAuth completion queues one forced refresh so an earlier same-week run cannot hide a newly reconnected account; scheduled and ordinary manual runs retain weekly idempotency.

## Compatibility rules
- Additive changes only within this version.
- No field removals/renames in listed endpoints.
- Breaking changes require a new contract version string.
