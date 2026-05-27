# README screenshots & demo media

Add PNG or WebP files here so they render on GitHub. The [README](../../README.md) references these paths.

## Suggested captures

| File | Route | What to show |
|------|--------|----------------|
| `dashboard-holdings.png` | `/portfolio` | Family summary + holdings table (filters, signals, P&amp;L) |
| `dashboard-agent.png` | `/portfolio/agent` | Portfolio agent chat with a sample question |
| `setup-accounts.png` | `/portfolio/setup` | Account hub (brokers connected / import) |
| `export-excel-modal.png` | `/portfolio` → **Export Excel** | Column + account picker modal |

**Tips:** Use 1400–1600px browser width, dark theme as you prefer, blur or crop any account IDs if the repo is public.

## Quick capture (macOS)

With the app running (`uvicorn main:app --reload`):

1. Open each route above.
2. `Cmd + Shift + 4` → window capture, or full-page extension (e.g. GoFullPage).
3. Save into this folder with the names in the table.

## Automated capture (optional)

```bash
pip install playwright
playwright install chromium
python scripts/capture_readme_screenshots.py
```

Requires a running app at `http://127.0.0.1:8000` (no HTTP auth, or set `PORTFOLIO_SCREENSHOT_BASE_URL` with credentials in the script).

`dashboard-holdings.png` is cropped and has **WT%**, **Value**, and **P&L** blurred for public README use. Re-run:

```bash
.venv/bin/python scripts/redact_readme_holdings_screenshot.py
```

after re-capturing that file.

Do not commit holdings with real names/values if the repository is public — use a demo account or redact.
