# Security

Talk to My Portfolio is a **personal, single-user** app. It is not multi-tenant. Security is mostly **network perimeter + filesystem permissions**.

## Threat model

| Scenario | Risk |
|----------|------|
| `127.0.0.1` only, auth unset | Low — same as any local daemon |
| Wi‑Fi / `0.0.0.0` without auth | **Critical** — anyone can read portfolio, change `.env`, place orders |
| Wi‑Fi / `0.0.0.0` with HTTP Basic Auth | **Much better** — shared family password on home network |
| Public internet | **Do not** without TLS reverse proxy + strong auth |

## HTTP Basic Auth (implemented)

Set in `.env`:

```text
PORTFOLIO_HTTP_USER=you
PORTFOLIO_HTTP_PASSWORD=strong-secret-here
```

- Protects HTML, JSON APIs, setup, trading, agent, uploads.
- **Exempt:** `/auth/zerodha/*`, `/zerodha/auth/*`, `/health` (OAuth redirects cannot send `Authorization`).
- **Swagger** `/docs` disabled while auth is enabled.
- Unset both vars → no auth (localhost dev).

Browsers cache credentials for the session; `fetch()` to same origin includes them automatically.

## Secrets on disk

| Store | Contents |
|-------|----------|
| `.env` | Broker API keys, LLM keys, HTTP password |
| `modules/portfolio/data/tokens.db` | Zerodha access tokens (plaintext) |
| `modules/portfolio/data/groww_tokens.db` | Groww tokens (plaintext) |

Recommend:

```bash
chmod 600 .env
chmod 700 modules/portfolio/data
```

Token encryption (SQLCipher / OS keychain) is not implemented yet — filesystem access still implies broker access.

## Trading

- Off by default: `TRADING_ENABLED=false`
- Requires JSON `confirmed: true` from the UI
- Still requires HTTP auth when `PORTFOLIO_HTTP_*` is set

## Uploads

- Max size: `PORTFOLIO_MAX_UPLOAD_BYTES` (default 10 MB)

## Ollama setup

- Server only fetches model lists from **localhost or private IPs** (SSRF protection).

## LLM privacy

Clicking **Ask** sends holdings context and your question to the configured provider (OpenAI, etc.). Use local Ollama if you want data to stay on-machine.

## Checklist before exposing on LAN

1. Set `PORTFOLIO_HTTP_USER` / `PORTFOLIO_HTTP_PASSWORD`
2. `chmod 600 .env`
3. Keep `TRADING_ENABLED=false` unless you need in-browser orders
4. Prefer `127.0.0.1` + VPN/tunnel over `0.0.0.0` on untrusted networks
