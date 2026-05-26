# Portfolio architecture (overview)

High-level design for the standalone portfolio FastAPI app. For setup, see [README.md](../README.md).

## Components

| Layer | Responsibility |
|--------|----------------|
| `router.py` | HTTP routes, HTML + JSON APIs |
| `services/portfolio.py` | Fetch holdings, cache, family merge |
| `services/market_data.py` | Yahoo metrics, ratings, optional LLM enrich |
| `auth/zerodha.py` | Kite Connect OAuth |
| `auth/groww.py` | Groww Trade API session |
| `db/*` | SQLite: tokens, portfolio cache, weekly history, LLM caches |

## Data flow

1. User opens `/portfolio` → serve cached snapshot from SQLite (stale-first).
2. Background or `?refresh=1` → Zerodha/Groww APIs → normalize rows → `enrich_holdings` (Yahoo; LLM only if env flags set).
3. Write through to in-memory + disk cache.

## Multi-account model

- Account registry: `accounts.json` (gitignored) from `accounts.example.json`.
- Credentials: `.env` per account id (`ZERODHA_API_KEY_<ID>`).
- OAuth: `/auth/zerodha/{code}` with Kite `redirect_params` for account disambiguation.

## Optional LLM

All OpenAI usage is opt-in (agent button, explicit API routes, or env flags). See README “OpenAI / LLM” table.
