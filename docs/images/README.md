# README screenshots & demo GIF

Assets for [README.md](../../README.md). Use **1400px** width, dark theme. Blur or redact sensitive amounts if the repo is public.

## Files

| File | What to show |
|------|----------------|
| `dashboard-holdings.png` | Family dashboard (WT%, Value, P&L redacted for public README) |
| `holding-expanded.png` | Expanded row: Fund/Tech strips, chart, forecast, **Why this signal?**, news |
| `trade-order-modal.png` | Buy/Sell order dialog (requires `TRADING_ENABLED=true`) |
| `dashboard-agent.png` | Portfolio agent chat |
| `growth-overview.png` | Growth tab — charts & timeline |
| `setup-accounts.png` | Setup hub (accounts, LLM, goals) |
| `export-excel-modal.png` | Export column + account picker |
| `demo.gif` | Short walkthrough of main features |

## Capture everything (app must be running)

```bash
# From repo root, with venv active:
pip install playwright pillow
playwright install chromium

# Optional: enable trading UI for trade-order-modal.png
# TRADING_ENABLED=true in .env — then restart uvicorn

python scripts/capture_readme_screenshots.py
```

`dashboard-holdings.png` is captured and redacted by `scripts/redact_readme_holdings_screenshot.py` (called automatically).

## Demo GIF

Requires **ffmpeg** (`brew install ffmpeg` on macOS):

```bash
python scripts/capture_readme_demo_gif.py
```

Produces `docs/images/demo.gif` (~15–25s): dashboard → expand holding → growth → agent → setup → export modal.

**GitHub tip:** Keep GIF under ~10MB. If too large, lower fps in `capture_readme_demo_gif.py` or trim duration.

## Manual capture

1. Open each route in the table above.
2. For expanded view: click **▸** on a liquid equity row; wait for chart/news.
3. For trade modal: expand a row with **Buy** / **Sell**; click **Buy**.
4. Save PNGs into this folder with the exact filenames.

Do not commit real account IDs or full portfolio values on a public repo.
