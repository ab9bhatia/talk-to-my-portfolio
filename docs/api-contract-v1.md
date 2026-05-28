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

## Compatibility rules
- Additive changes only within this version.
- No field removals/renames in listed endpoints.
- Breaking changes require a new contract version string.
