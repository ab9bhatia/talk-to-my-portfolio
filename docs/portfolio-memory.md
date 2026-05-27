# Portfolio memory & cache architecture

Personal Hub portfolio module uses a **three-layer** model. No Redis or external services — everything is local SQLite + in-process memory, same stack as the expenses module.

## Tech stack summary

| Layer | Role | Technology | Location |
|-------|------|------------|----------|
| **Hot cache** | Sub-minute repeat requests within one server process | Python `dict` (TTL 5 min default) | `modules/portfolio/services/portfolio.py` |
| **Cache (persistent)** | Survive restarts; stale-first page load | **SQLite 3** | `modules/portfolio/data/portfolio_cache.db` |
| **STM** | Agent chat threads & messages (1 week; starred longer) | **SQLite 3** (same DB) | `agent_threads`, `agent_messages` |
| **LTM** | Investor thesis, notes, past recommendations (planned) | **SQLite 3** (same DB, tables TBD) | See schema below |
| **Yahoo metrics** | Per-symbol PE, sector, signal | In-process `dict` (6 h) | `market_data.py` |
| **Insights** | Row expander charts/news | In-process `dict` (6 h) | `stock_insights.py` |

**Runtime:** Python 3 · FastAPI · `sqlite3` stdlib · background `threading` for revalidation.

**Not used:** Redis, Memcached, vector DB, gRPC (browser uses HTTP + SSE for agent streaming).

---

## Stale-first flow

1. User opens `/portfolio` → hub/sidebar loader shows immediately (`nav-loader.js`).
2. Server returns **SQLite snapshot** if memory TTL expired (up to 7 days old).
3. If snapshot age &gt; `PORTFOLIO_CACHE_TTL_SECONDS` (default 300), response is marked `stale: true` and a **background thread** fetches Zerodha + Yahoo.
4. Client polls `GET /api/portfolio/meta` every 2s; when `fresh && !revalidating`, page reloads with live data.

Force live data: **Refresh** link (`?refresh=1`) skips stale path.

---

## SQLite schema (`portfolio_cache.db`)

### `portfolio_snapshots` — cache (LTM-style durable snapshots)

| Column | Type | Description |
|--------|------|-------------|
| `cache_key` | TEXT PK | e.g. `family:metrics=True` |
| `payload_json` | TEXT | Full portfolio JSON (holdings, summary, errors) |
| `cached_at` | REAL | Unix timestamp |
| `holdings_hash` | TEXT | Short hash of symbols/qty/LTP for change detection |
| `source` | TEXT | `live` \| `revalidate` |

### `revalidate_jobs` — background refresh status

| Column | Type | Description |
|--------|------|-------------|
| `cache_key` | TEXT PK | Same as snapshot key |
| `status` | TEXT | `running` \| `done` \| `error` |
| `started_at` | REAL | Unix timestamp |
| `finished_at` | REAL | Unix timestamp |
| `error` | TEXT | Last error message if any |

### `agent_threads` — STM (conversation state)

| Column | Type | Description |
|--------|------|-------------|
| `thread_id` | TEXT PK | UUID |
| `context_json` | TEXT | Portfolio context JSON at thread start |
| `created_at` | REAL | Unix timestamp |
| `updated_at` | REAL | Unix timestamp (TTL purge after 1 week idle; `is_important` exempt) |

### `agent_messages` — STM (chat history)

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PK | Auto-increment |
| `thread_id` | TEXT FK | → `agent_threads` |
| `role` | TEXT | `user` \| `assistant` |
| `content` | TEXT | Message body |
| `created_at` | REAL | Unix timestamp |

### LTM (planned — not migrated yet)

| Table | Purpose |
|-------|---------|
| `investor_profile_overrides` | Editable horizon, max %, themes (key/value or JSON) |
| `symbol_notes` | Per-symbol thesis: `symbol`, `note`, `stance` (core/spec/avoid), `updated_at` |
| `agent_recommendation_log` | `thread_id`, `recommendations_json`, `portfolio_hash`, `created_at` |

---

## Environment variables

```text
PORTFOLIO_CACHE_TTL_SECONDS=300      # fresh window; older = stale + revalidate
PORTFOLIO_STALE_MAX_SECONDS=604800   # max age to serve snapshot (7 days)
```

---

## API

| Endpoint | Purpose |
|----------|---------|
| `GET /api/portfolio/meta` | `{ fresh, stale, revalidating, cached_at, age_seconds }` |
| `GET /api/portfolio?refresh=1` | Bypass stale; blocking live fetch |
