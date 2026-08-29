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
| OS keychain / encrypted fallback | Broker access tokens after explicit verified migration |
| `modules/portfolio/data/tokens.db` | Legacy Zerodha token metadata; plaintext remains only until explicit verified migration |
| `modules/portfolio/data/groww_tokens.db` | Legacy Groww token metadata; plaintext remains only until explicit verified migration |
| `modules/portfolio/data/weekly_sync.db` | Durable queue/run audit, account codes/states, quality/session metadata, artifact paths |
| `modules/portfolio/data/instrument_master.db` | Canonical instruments, aliases, corporate actions, sourced override audit |
| `modules/portfolio/data/transaction_ledger.db` | Private transaction history, import previews, unresolved rows, and batch audit |
| `modules/portfolio/data/market_regime.db` | Sourced daily MRMI observations and component provenance |
| `modules/portfolio/data/research_workspace.db` | Private candidates, watchlists, notes, thesis history, screens, and events |
| `modules/portfolio/data/fund_intelligence.db` | Sourced scheme metadata and dated constituent observations |
| `modules/portfolio/data/operating_console.db` | Saved stress assumptions and local alert cooldown/history |
| `modules/portfolio/data/weekly-digests/` | Local decision digests; no internal account IDs or full holdings |

Recommend:

```bash
chmod 600 .env
chmod 700 modules/portfolio/data
```

Use the preview/confirm migration in [security-recovery.md](security-recovery.md). It verifies OS-backed or AES-GCM fallback storage before replacing plaintext and provides rollback/revocation. Migration is intentionally not automatic.

Weekly-sync errors are sanitized for common API key, secret, token, TOTP, password, and authorization assignments before logs/audit. Digest delivery is local-file only by default. Do not place the data directory in a cloud-synced folder unless that is an intentional disclosure.

Reconciliation source-document fields should contain a local reference or document description, not embedded credentials. Instrument and override databases are private portfolio metadata and remain gitignored.

Transaction imports can contain sensitive dates, amounts, and local account references. Preview and audit-workbook endpoints inherit the app's HTTP Basic Auth; do not expose them on an unauthenticated LAN or copy audit exports into tracked/cloud-synced folders unintentionally.

Research notes and thesis history may contain sensitive personal reasoning. The screener never evaluates code or builds SQL from user fields. LLM research context removes account IDs and user notes; external facts still require source and as-of metadata.

Account tax profiles, transaction lots, asset-location comparisons, and CA exports are sensitive local data. They are excluded from LLM context by default. Keep downloaded CA workbooks outside tracked or cloud-synced folders and protect LAN access before using tax endpoints.

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
5. Use the built-in CSRF/origin policy and bearer token for non-browser clients

For backup, restore, lost-machine response, schema recovery, privacy controls, and release hardening, see [security-recovery.md](security-recovery.md).
