# Talk to My Portfolio

A self-hosted **family portfolio dashboard** for Indian markets: consolidate **Zerodha (Kite Connect)**, **Groww Trade API**, and optional **Sarwa (manual USD)** holdings—with live metrics, ratings, weekly history, and an optional **“talk to my portfolio”** agent (OpenAI).

**Repository:** [github.com/ab9bhatia/talk-to-my-portfolio](https://github.com/ab9bhatia/talk-to-my-portfolio)

## Features

- Multi-account Zerodha OAuth (one Kite app per login)
- Groww holdings via Trade API (TOTP or API key + secret)
- Sarwa weekly snapshot import (screenshot parsing optional)
- Holdings table: cap bucket (AMFI-style), sector, 52W, upside, buy/hold/sell signal
- Stale-first SQLite cache + background refresh
- Optional live **Buy/Sell** (Zerodha CNC + Groww) when `TRADING_ENABLED=true`
- Portfolio agent — **only when you click Ask** and set an API key
- LLM sector / buy thesis — **opt-in only** (off by default)

## Requirements

- Python 3.11+ (3.12+ recommended)
- macOS or Linux
- [Kite Connect](https://developers.kite.trade/) app per Zerodha login you want to link
- Optional: [Groww Trade API](https://groww.in/trade-api)
- Optional: [OpenAI API key](https://platform.openai.com/api-keys) for the agent

## Quick start

```bash
git clone https://github.com/ab9bhatia/talk-to-my-portfolio.git
cd talk-to-my-portfolio

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

bash scripts/init_local_config.sh
# Then configure accounts + keys (below)

uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

Open [http://127.0.0.1:8000/portfolio](http://127.0.0.1:8000/portfolio) → **Connect Zerodha** for each enabled account.

**Prefer a UI?** Use **[Connect accounts](http://127.0.0.1:8000/portfolio/setup)** (`/portfolio/setup`) — add or **edit** brokers, update `.env` + `accounts.json`, upload CSV/Excel/screenshots, then open the dashboard. Dhan is listed as coming soon.

---

## Configure brokers (3 steps)

You use **two local files** (both gitignored). They must agree on each account’s `"id"`.

| File | What it defines |
|------|------------------|
| `modules/portfolio/accounts.json` | *Who* — labels, short codes, which brokers are on |
| `.env` | *Secrets* — API keys matched to each `"id"` |

### Step 1 — List accounts (`accounts.json`)

Copy `accounts.example.json` → `accounts.json`. For each person/broker you want on the dashboard:

```json
{
  "id": "primary",
  "code": "AB",
  "label": "My Zerodha",
  "user_id": "YOUR_KITE_CLIENT_ID",
  "enabled": true,
  "redirect_url": "http://127.0.0.1:8000/auth/zerodha/callback"
}
```

| Field | Purpose |
|--------|---------|
| `id` | Stable key — drives `.env` variable names (see step 2) |
| `code` | Short tag on the UI (e.g. `AB`, `HB`) |
| `label` | Display name |
| `enabled` | `true` = fetch this account on the family dashboard |

### Step 2 — Add secrets (`.env`)

Copy [.env-example](.env-example) → `.env`.

**Rule:** JSON `"id"` → **uppercase** suffix in `.env`.

| `accounts.json` `"id"` | Zerodha vars in `.env` | Groww vars in `.env` |
|------------------------|-------------------------|----------------------|
| `primary` | `ZERODHA_API_KEY_PRIMARY`, `ZERODHA_API_SECRET_PRIMARY`, `ZERODHA_REDIRECT_URL_PRIMARY` | — |
| `member2` | `ZERODHA_API_KEY_MEMBER2`, … | — |
| `groww1` | — | `GROWW_TOTP_TOKEN_GROWW1` + `GROWW_TOTP_SECRET_GROWW1` **or** `GROWW_API_KEY_GROWW1` + `GROWW_API_SECRET_GROWW1` |

Example — two Zerodha logins + one Groww:

```text
# Zerodha — id "primary" and id "member2" in accounts.json
ZERODHA_API_KEY_PRIMARY=...
ZERODHA_API_SECRET_PRIMARY=...
ZERODHA_REDIRECT_URL_PRIMARY=http://127.0.0.1:8000/auth/zerodha/callback

ZERODHA_API_KEY_MEMBER2=...
ZERODHA_API_SECRET_MEMBER2=...
ZERODHA_REDIRECT_URL_MEMBER2=http://127.0.0.1:8000/auth/zerodha/callback

# Groww — id "groww1" in accounts.json (TOTP shown; or use API key + secret)
GROWW_TOTP_TOKEN_GROWW1=...
GROWW_TOTP_SECRET_GROWW1=...
```

`HUB_BASE_URL` must match the host/port in your Kite redirect URLs (default `http://127.0.0.1:8000`).

### Step 3 — Get keys & connect

| Broker | Where to create keys | In the app |
|--------|----------------------|------------|
| **Zerodha** | [developers.kite.trade](https://developers.kite.trade/) — **one app per login** | **Connect Zerodha** on `/portfolio` |
| **Groww** | [groww.in/trade-api](https://groww.in/trade-api) — subscribe, then TOTP or API keys | Status on dashboard; refresh if needed |
| **Sarwa** | No API | Enable in JSON; weekly manual entry |
| **OpenAI** (optional) | [platform.openai.com/api-keys](https://platform.openai.com/api-keys) | **Ask** button only |

**Step-by-step screenshots and troubleshooting:** [docs/broker-api-keys.md](docs/broker-api-keys.md)

#### Multiple Zerodha accounts (family)

- Each Zerodha **login** = its own Kite Connect app (own API key + secret).
- Use the **same** redirect URL on every app: `http://127.0.0.1:8000/auth/zerodha/callback`
- Add one `zerodha[]` row per person in `accounts.json` (unique `"id"` + `"code"`).
- Add matching `ZERODHA_*_<ID>` lines in `.env` for each enabled row.

#### Multiple Groww accounts

- One `groww[]` row per Groww login (unique `"id"`).
- Env vars use that id: `GROWW_TOTP_TOKEN_<ID>` or `GROWW_API_KEY_<ID>`.

---

## OpenAI (optional)

No OpenAI calls on a normal page load unless you enable them.

| Feature | When it calls OpenAI | Enable |
|---------|----------------------|--------|
| Portfolio agent | Click **Ask** | `OPENAI_API_KEY` |
| B+ buy thesis | `?refresh=1` | `BUY_THESIS_LLM_ENABLED=true` and `BUY_THESIS_LLM_ON_ENRICH=true` |
| Sector LLM | Refresh or API | `SECTOR_LLM_ON_ENRICH=true` |
| Sarwa screenshot | Upload image | `OPENAI_API_KEY` |

Defaults: all LLM-on-enrich flags **false**.

---

## Project layout

```text
talk-to-my-portfolio/
├── main.py
├── .env-example
├── modules/portfolio/
│   ├── accounts.example.json
│   ├── accounts.json          # gitignored
│   └── data/                  # gitignored
├── shared/web/
└── docs/
    └── broker-api-keys.md     # broker key walkthrough
```

## API (selection)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/portfolio` | Dashboard |
| GET | `/api/portfolio` | Family JSON |
| POST | `/api/portfolio/agent/ask` | Agent (SSE) |
| GET | `/docs` | Swagger |

## Security

- Never commit `.env`, `accounts.json`, or `modules/portfolio/data/`.
- Use a **private** GitHub repo if you prefer.
- Rotate keys if they were ever exposed.

## Publish to GitHub

```bash
git remote add origin https://github.com/YOUR_USER/talk-to-my-portfolio.git
git push -u origin main
```

Repo: [github.com/ab9bhatia/talk-to-my-portfolio](https://github.com/ab9bhatia/talk-to-my-portfolio)

## License

MIT or your choice — add a `LICENSE` file if you open-source.
