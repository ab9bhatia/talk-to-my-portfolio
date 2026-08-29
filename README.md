<p align="center">
  <strong>TalkToMyPortfolio</strong><br>
  <sub>See every holding in one place — then <em>ask</em> what to buy, sell, trim, or hold.</sub>
</p>

<p align="center">
  <a href="https://github.com/ab9bhatia/talk-to-my-portfolio">GitHub</a>
  ·
  <a href="docs/product.md">Product guide</a>
  ·
  <a href="docs/user-journey.md">User journey</a>
  ·
  <a href="code_flow_and_index.md">Code index</a>
  ·
  <a href="docs/broker-api-keys.md">Broker setup</a>
  ·
  <a href="#quick-start">Quick start</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.11%2B-blue?style=flat-square" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/platform-macOS%20%7C%20Windows%20%7C%20Linux-lightgrey?style=flat-square" alt="macOS, Windows, or Linux">
  <img src="https://img.shields.io/badge/license-MIT-green?style=flat-square" alt="MIT">
</p>

---

## Why this exists

Indian families often hold stocks and funds across **Zerodha**, **Groww**, **Sarwa**, and offline sheets — but decisions still happen in fragments.

**TalkToMyPortfolio** is built around: **consolidate first, then converse**. One dashboard plus a **portfolio agent** that reads your real holdings (sector, weights, signals, guardrails) and answers in plain language.

Everything runs **on your machine**. Broker data stays local; only questions you send to the agent use your LLM API key.

**Full feature list & user journey:** [docs/product.md](docs/product.md)  
**How the code is organized:** [code_flow_and_index.md](code_flow_and_index.md)

---

## Quick start

```bash
git clone https://github.com/ab9bhatia/talk-to-my-portfolio.git
cd talk-to-my-portfolio

python3 -m venv .venv
source .venv/bin/activate   # Windows: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

bash scripts/init_local_config.sh
uvicorn main:app --reload --host 127.0.0.1 --port 9000
```

1. **[Setup](http://127.0.0.1:9000/talktomyportfolio/portfolio/setup)** — brokers, LLM, goals, and weekly sync
2. **[Portfolio](http://127.0.0.1:9000/talktomyportfolio/portfolio)** — holdings  
3. **[Agent](http://127.0.0.1:9000/talktomyportfolio/portfolio/agent)** — ask your first question  

**Docker:** `docker build -t talk-to-my-portfolio .` then `docker run --rm -p 9000:9000 --env-file .env talk-to-my-portfolio`

Default URL: **http://127.0.0.1:9000/talktomyportfolio**

---

## Configure brokers

| File | Role |
|------|------|
| `modules/portfolio/accounts.json` | Account labels & codes (gitignored) |
| `.env` | API keys — `ZERODHA_API_KEY_<ID>`, `GROWW_*`, LLM keys |

Details: **[docs/broker-api-keys.md](docs/broker-api-keys.md)**

---

## Portfolio agent (LLM)

Configure in **Setup → Portfolio agent (LLM)** — OpenAI, Claude, Gemini, or **Ollama** (local).

Goals set under **Setup → Goals & guardrails** are injected into agent context (target return, max position/sector %, risk profile). Start a **new chat** after changing goals.

---

## Weekly sync

Run the local, idempotent weekly operating loop after Friday's Indian market close:

```bash
python -m modules.portfolio.scripts.weekly_sync --mode auto --dry-run
python -m modules.portfolio.scripts.weekly_sync --mode auto
```

The Setup card shows the last attempt, last success, degraded accounts, and the local digest. macOS, Linux, Windows, recovery, and scheduler instructions: **[docs/weekly-sync-operations.md](docs/weekly-sync-operations.md)**.

---

## Chart patterns (momentum setups)

Scans your equity holdings for common technical setups and shows them **inline in the holdings table** — so momentum signals sit next to weight, signal, and P&L where decisions happen.

**Patterns detected**

| Pattern | Bias | Target rule |
|---------|------|-------------|
| Cup with handle | Bullish | rim + cup depth |
| Inverse head & shoulders | Bullish | neckline + head depth |
| Double bottom | Bullish | breakout level + depth |
| Ascending triangle | Bullish | resistance + ~½ triangle height |
| Head & shoulders | Bearish | neckline − head height |

Each match shows a lifecycle (`BUILDING`, `NEAR_BREAKOUT`, `CONFIRMED`, target completed, or expired), a heuristic **shape-quality score** such as `82/100`, a currency-safe measured target, remaining upside/downside, and a broad trading-session window. The score is not a probability and the target window is not an exact date.

**Where to find it**

- **Holdings table** — a pattern pill appears on each holding with a setup. The **📈 Setups** toolbar toggle filters the book to only those holdings.
- **Dashboard** — *Pattern execution radar* automatically scans holdings and supports lifecycle/bias filters and sortable fields.
- **Holding detail** (expand a row) — an overlay chart marks the exact anchor points the detector used (shoulders, head, cup rim, neckline, target) so you can verify the setup on the real price line.

**How it works (and its limits)**

- Source: **Yahoo Finance daily closes** (`yfinance`), computed locally — no external pattern API or AI.
- Lookback policy: fetch ~18 months; detect reversals within ~1 year and require the right edge to be recent (~3 months); cup base up to ~15 months; triangle uses the last ~100 bars.
- These are **heuristics on close prices** (no volume) — treat them as a screen, not advice. Always confirm on the chart before acting.
- A bullish target already reached is retained as `TARGET_ACHIEVED` or `TARGET_OVERSHOT`; it is never shown as negative active upside or used to justify waiting.
- The legacy `status`, `confidence`, `target_price`, and `upside_to_target_pct` API fields remain available. New clients should use `lifecycle_state`, `heuristic_score`, `target_status`, `remaining_*_pct`, `currency`, and `estimated_horizon`.
- Tunable via env: `CHART_PATTERNS_HISTORY`, `CHART_PATTERNS_MAX_SPAN`, `CHART_PATTERNS_RECENCY_BARS`, `CHART_PATTERNS_CUP_WINDOW`, `CHART_PATTERNS_CACHE_TTL`.

APIs: `GET /api/portfolio/patterns` (whole portfolio) · `GET /api/portfolio/patterns/{symbol}?exchange=NSE` (one symbol). Results cached ~6h per symbol.

Design and Stage 6A contract: [docs/pattern-execution-overlay.md](docs/pattern-execution-overlay.md).

---

## Routes

| Route | Purpose |
|-------|---------|
| `/portfolio` | Family dashboard (holdings + chart pattern pills) |
| `/portfolio/agent` | Agent chat (SSE) |
| `/portfolio/growth` | Growth & benchmarks |
| `/portfolio/setup` | Accounts, LLM, goals, weekly sync, import audit |
| `/api/portfolio/sync/status` | Weekly job health and degraded accounts (JSON) |
| `/api/portfolio/patterns` | Chart-pattern scan (JSON) |
| `/docs` | Swagger (hidden if HTTP auth on) |

---

## Security

- Do not commit `.env`, `accounts.json`, or `modules/portfolio/data/`.  
- Optional LAN auth: `PORTFOLIO_HTTP_USER` / `PORTFOLIO_HTTP_PASSWORD` in `.env`.  
- Full notes: **[docs/security.md](docs/security.md)**

---

## Docs index

| Document | Contents |
|----------|----------|
| [docs/product.md](docs/product.md) | Product journey, features, roadmap |
| [docs/user-journey.md](docs/user-journey.md) | Connect-to-ask flow and acceptance test |
| [docs/weekly-sync-operations.md](docs/weekly-sync-operations.md) | Weekly job, CLI, schedulers, audit, and recovery |
| [code_flow_and_index.md](code_flow_and_index.md) | Folders, files, request flows |
| [docs/api-contract-v1.md](docs/api-contract-v1.md) | Stable API for mobile clients |
| [docs/release-checklist.md](docs/release-checklist.md) | Release steps |
| [CHANGELOG.md](CHANGELOG.md) | Version history |

---

## License

MIT — add a `LICENSE` file if you open-source.
