<p align="center">
  <strong>Talk to My Portfolio</strong><br>
  <sub>See every holding in one place — then <em>ask</em> what to buy, sell, trim, or hold.</sub>
</p>

<p align="center">
  <a href="https://github.com/ab9bhatia/talk-to-my-portfolio">GitHub</a>
  ·
  <a href="#talk-to-your-portfolio">Portfolio agent</a>
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

Indian families often hold stocks and funds across **Zerodha**, **Groww**, **Sarwa**, and offline sheets — but decisions still happen in fragments: one app for prices, another for news, gut feel for trim vs hold.

**Talk to My Portfolio** is built around a simple idea: **consolidate first, then converse**. You get a unified dashboard *and* an integrated **portfolio agent** that reads your real holdings (sector, industry, signals, concentration) and answers in plain language — what to add, what to trim, what to watch, and how long to hold.

Everything runs **on your machine**. Broker data stays local; only the questions you explicitly send to the agent use your configured LLM API key.

---

## Talk to your portfolio

The agent is **built into the dashboard**, not a separate product. After your brokers are linked, open the **Portfolio agent** panel on [`/portfolio`](http://127.0.0.1:8000/portfolio), ask a question, and get a structured advisory reply streamed in real time.

### What it helps with

| Area | Examples |
|------|----------|
| **Buy / add** | Which names to initiate, add to, or watch — with rationale |
| **Sell / trim** | Overweight positions, trim vs exit, concentration risks |
| **Hold horizon** | Time horizon guidance per idea (e.g. 3y+ core holdings) |
| **Portfolio view** | Overall stance, XIRR outlook vs your goals, macro read |
| **Themes** | Sector/theme opportunities aligned to your actual book |
| **Red flags** | Governance, concentration, or mix issues surfaced from context |
| **Follow-ups** | Multi-turn chat — “what if I drop X and add Y?” |

It uses **your** JSON context: live holdings, sector/industry labels, business summaries, deterministic flags, and dashboard signals (e.g. upside where available) — not ticker guesswork.

### Example questions

- *Should I trim banking and add to infrastructure themes?*
- *Which holdings are weakest vs my 15% return goal?*
- *What would you exit in the next rebalance given current weights?*
- *Any red flags in my top ten positions by value?*

### How it works (privacy-first)

```mermaid
flowchart TB
  B[Zerodha / Groww / Sarwa / Custom] --> C[Local holdings + metrics]
  C --> D[Dashboard]
  C --> E[Portfolio context JSON]
  U[You click Ask] --> E
  E --> L[Your LLM API]
  L --> R[Buy · Sell · Hold · Horizon · Stance]
  R --> D
```

- **No LLM calls on page load** — only when you click **Ask** (or send a follow-up).
- **Threaded chats** — sessions saved locally for continuity.
- **Not financial advice** — personal decision support using data you already trust; you stay in control of every trade.

Configure any supported provider under **Connect accounts → Portfolio agent (LLM)**. See [Enable the agent (LLM)](#enable-the-agent-llm).

---

## What else is included

| | |
|---|---|
| **Unified dashboard** | Family P&amp;L, filters by account, sector, cap bucket, 52W, upside, signals |
| **Account hub** | Add, edit, reconnect brokers; import CSV / Excel / screenshots |
| **Brokers** | Zerodha (Kite), Groww (Trade API), Sarwa (USD), Custom portfolios |
| **Smart cache** | Stale-first SQLite + background refresh; Yahoo fundamentals cached 24h and refreshed daily in off-hours |
| **Daily growth** | Auto-saved each live refresh; charts on **Growth** tab |
| **Weekly history** | Weekly snapshots + Excel export in `portfolio_history.db` |
| **Optional trading** | Live Buy/Sell when `TRADING_ENABLED=true` |

---

## Screens & routes

| Route | Purpose |
|-------|---------|
| [`/portfolio`](http://127.0.0.1:8000/portfolio) | Dashboard + **Portfolio agent** |
| [`/portfolio/growth`](http://127.0.0.1:8000/portfolio/growth) | **Daily growth** — value trend & day-over-day by account / cap |
| [`/portfolio/setup`](http://127.0.0.1:8000/portfolio/setup) | Connect & edit accounts |
| [`/docs`](http://127.0.0.1:8000/docs) | Swagger API |
| `POST /api/portfolio/agent/ask` | Agent (SSE stream) |

---

## Quick start

Works on **macOS, Windows, and Linux** (Python 3.11+). Example on macOS/Linux:

```bash
git clone https://github.com/ab9bhatia/talk-to-my-portfolio.git
cd talk-to-my-portfolio

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

bash scripts/init_local_config.sh
uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

**Windows (PowerShell):**

```powershell
git clone https://github.com/ab9bhatia/talk-to-my-portfolio.git
cd talk-to-my-portfolio

py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

copy .env-example .env
copy modules\portfolio\accounts.example.json modules\portfolio\accounts.json
uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

1. **[Connect accounts](http://127.0.0.1:8000/portfolio/setup)** — Zerodha, Groww, or custom import  
2. **[Portfolio](http://127.0.0.1:8000/portfolio)** — review holdings, scroll to **Portfolio agent**, ask your first question  

---

## Configure brokers

Two gitignored files must share the same account `"id"`:

| File | Role |
|------|------|
| `modules/portfolio/accounts.json` | Who — labels, codes (`AB`, `HB`), enabled |
| `.env` | Secrets — `ZERODHA_API_KEY_<ID>`, `GROWW_*`, etc. |

`"id": "primary"` → `ZERODHA_API_KEY_PRIMARY`, …

<details>
<summary><strong>Zerodha, Groww, Sarwa, Custom</strong> — setup steps</summary>

- **Zerodha** — [developers.kite.trade](https://developers.kite.trade/), redirect `http://127.0.0.1:8000/auth/zerodha/callback`, then **Connect** on the dashboard  
- **Groww** — [groww.in/trade-api](https://groww.in/trade-api), TOTP or API keys in `.env`  
- **Sarwa / Custom** — weekly or file import via **Connect accounts**  

Full guide: **[docs/broker-api-keys.md](docs/broker-api-keys.md)**

</details>

---

## Enable the agent (LLM)

Configure from **Connect accounts** → **Portfolio agent (LLM)** — pick a provider and model from dropdowns; **Save** writes to `.env` automatically (same as broker keys):

| Provider | What you enter |
|----------|----------------|
| **OpenAI** | API key + model (e.g. `gpt-4o-mini`) |
| **Claude (Anthropic)** | API key + model (e.g. `claude-sonnet-4-20250514`) |
| **Google Gemini** | API key + model (e.g. `gemini-2.0-flash`) |
| **Ollama (local)** | Base URL (`http://localhost:11434`) + model name (e.g. `llama3.2`) — no cloud key |

Settings are written to `.env` as `PORTFOLIO_LLM_PROVIDER`, `PORTFOLIO_LLM_MODEL`, and provider-specific keys (`PORTFOLIO_OPENAI_API_KEY`, `PORTFOLIO_ANTHROPIC_API_KEY`, `PORTFOLIO_GEMINI_API_KEY`, `PORTFOLIO_OLLAMA_BASE_URL`, …).

Manual `.env` example (OpenAI):

```text
PORTFOLIO_LLM_PROVIDER=openai
PORTFOLIO_OPENAI_API_KEY=sk-...
PORTFOLIO_LLM_MODEL=gpt-4o-mini
```

Ollama example:

```text
PORTFOLIO_LLM_PROVIDER=ollama
PORTFOLIO_OLLAMA_BASE_URL=http://localhost:11434
PORTFOLIO_LLM_MODEL=llama3.2
```

**Chat history** is kept for **1 week** (starred chats are kept longer). Other LLM-powered features:

| Feature | When it runs | Default |
|---------|----------------|---------|
| **Portfolio agent** | You click **Ask** | On once provider is configured |
| Sarwa / screenshot import | File upload | Needs vision-capable key (OpenAI recommended) |
| Sector / buy thesis on refresh | `?refresh=1` | Off |

---

## Project layout

```text
talk-to-my-portfolio/
├── main.py
├── modules/portfolio/services/
│   ├── portfolio_agent.py    # talk-to-your-portfolio brain
│   ├── portfolio_context.py  # holdings → agent context
│   └── portfolio.py          # broker fetch + cache
├── shared/web/               # dashboard + agent UI
└── docs/
```

---

## Requirements

- **Python 3.11+** on macOS, Windows, or Linux  
- Zerodha Kite app(s) per login  
- Groww Trade API (optional)  
- **LLM provider** — OpenAI, Claude, Gemini, or local [Ollama](https://ollama.com) for the portfolio agent (configure in app or `.env`)

**Platform notes:** The web app and brokers are cross-platform. Optional `scripts/install_groww_reminder.sh` (macOS launchd email reminder) is macOS-only; skip it on Windows/Linux.

---

## Security

- Never commit `.env`, `accounts.json`, or `modules/portfolio/data/`  
- Agent sends **portfolio context + your question** to your chosen LLM provider when you ask — nothing automatic in the background  
- Prefer a private GitHub repo for personal forks  

### LAN / phone access (recommended)

If another device on your network can reach the app (phone on Wi‑Fi, `0.0.0.0`, tunnel), set in `.env`:

```text
PORTFOLIO_HTTP_USER=you
PORTFOLIO_HTTP_PASSWORD=choose-a-strong-password
```

The browser will prompt once; all routes (portfolio, setup, trading API, agent) are protected. Zerodha OAuth callback stays open so Kite login still works. `/docs` is hidden while auth is on.

Leave both unset for **localhost-only** dev with no login prompt (default).

Run with `uvicorn main:app --host 127.0.0.1 --port 8000` unless you need LAN; use auth if you bind to `0.0.0.0`.

See [docs/security.md](docs/security.md) for the full threat model.

---

## API snapshot

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/portfolio` | Dashboard + agent UI |
| `POST` | `/api/portfolio/agent/ask` | Stream advisory JSON (SSE) |
| `GET` | `/api/portfolio` | Family holdings JSON |

---

## Publish

```bash
git remote add origin https://github.com/YOUR_USER/talk-to-my-portfolio.git
git push -u origin main
```

**[github.com/ab9bhatia/talk-to-my-portfolio](https://github.com/ab9bhatia/talk-to-my-portfolio)**

---

## License

MIT — add a `LICENSE` file if you open-source.
