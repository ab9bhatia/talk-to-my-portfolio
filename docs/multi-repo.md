# Multi-repo layout

The former **Personal Hub** monorepo is split into three standalone FastAPI apps:

| Repo | Path | Port | Start |
|------|------|------|--------|
| **Talk to My Portfolio** | `PA/portfolio` (repo: `talk-to-my-portfolio`) | 8000 | `uvicorn main:app --reload --port 8000` |
| **Expenses** | `PA/expenses` | 8001 | `uvicorn main:app --reload --port 8001` |
| **Learnings** | `PA/learnings` | 8002 | `uvicorn main:app --reload --port 8002` |

Each app:

- Own `main.py` and **lifespan** (only its SQLite DBs and startup jobs)
- Own `requirements.txt` (no Kite/Groww in expenses; no yfinance in learnings)
- Own `modules/<name>/` and `modules/<name>/data/`
- Own `.env` — copy from `.env-example` in that repo

## Cross-links (optional)

Set in each repo’s `.env` so the sidebar can open the others in a new tab:

**Portfolio** (`portfolio/.env`):

```text
EXPENSES_APP_URL=http://127.0.0.1:8001
LEARNINGS_APP_URL=http://127.0.0.1:8002
```

**Expenses** (`expenses/.env`):

```text
PORTFOLIO_APP_URL=http://127.0.0.1:8000
LEARNINGS_APP_URL=http://127.0.0.1:8002
```

**Learnings** (`learnings/.env`):

```text
PORTFOLIO_APP_URL=http://127.0.0.1:8000
EXPENSES_APP_URL=http://127.0.0.1:8001
```

## Git

Initialize separate git repos under `PA/expenses` and `PA/learnings` when ready:

```bash
cd ../expenses && git init && git add . && git commit -m "Initial expenses app"
cd ../learnings && git init && git add . && git commit -m "Initial learnings app"
```

Portfolio repo keeps only `modules/portfolio/`.

## Migrating data

Expense and learning SQLite files were copied with `modules/*/data/` into the new repos. Point each app’s `.env` at the same Gmail/Google token paths you used before.
