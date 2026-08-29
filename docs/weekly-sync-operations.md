# Weekly sync operations (Milestone 7A)

The weekly sync is a single local job shared by the Setup button, CLI, and OS schedulers. It refreshes the canonical family portfolio, assigns an explicit state to every enabled account, recomputes deterministic advisory output, records daily and weekly snapshots, and writes a local Markdown/HTML digest. It never places orders.

## Recommended operation

Use `auto` every Friday at 18:30 Asia/Kolkata, with a Saturday 09:00 backup. The idempotency key includes the ISO week, mode, and enabled account set, so the backup is audited as `SKIPPED_DUPLICATE` after a successful Friday run.

Run once manually before installing a scheduler:

```bash
source .venv/bin/activate
python -m modules.portfolio.scripts.weekly_sync --mode auto --dry-run
python -m modules.portfolio.scripts.weekly_sync --mode auto
```

Modes:

| Mode | Behavior | Snapshot policy |
|---|---|---|
| `auto` | Attempts live broker refresh and safely retains trusted cached quantities when a session is unavailable | Writes if at least one trusted position exists; degraded accounts are explicit |
| `live` | Attempts live refresh and requires every enabled account to be live | Writes nothing if any account needs a cache, login, or import |
| `safe-fallback` | Uses only the durable family cache, refreshes quotes locally through the existing market-data path, and does not call brokers | Writes labelled cached-position snapshots when available |
| `--dry-run` | Fetches, classifies, and evaluates the deterministic advisory | Writes audit state only; no portfolio snapshots or digest files |

Use `--json` for the complete audit response. A successful process exits `0`; `FAILED`, `LOCKED`, and `CANCELLED` exit non-zero.

## Account states

Every enabled account ends in exactly one state:

- `LIVE_RECONCILED`: the account refreshed live; the full value-reconciliation engine is still Milestone 7B.
- `LIVE_WITH_WARNINGS`: live data loaded with a warning that requires review.
- `CACHED_POSITIONS_FRESH_PRICES`: quantities are from a trusted snapshot while prices were refreshed separately.
- `STALE_POSITIONS`: cached quantities and prices are not both current.
- `AUTH_REQUIRED`: reconnect the broker in Setup.
- `IMPORT_REQUIRED`: import current holdings for a manual account.
- `FAILED`: no safe account data was available; use the recorded recovery action and local logs.

`position_as_of` and `price_as_of` are stored separately. The Setup status card shows the latest attempted run, the last successful run, degraded accounts, and the local digest path.

## Install the scheduler

### macOS launchd

`launchd` calendar triggers use the Mac system timezone. Set macOS to Asia/Kolkata, or adjust the generated times before loading.

```bash
bash scripts/install_weekly_sync_macos.sh install
launchctl print gui/$(id -u)/com.talktomyportfolio.weekly-sync

# Remove later
bash scripts/install_weekly_sync_macos.sh uninstall
```

Override the interpreter with `PORTFOLIO_PYTHON=/absolute/path/to/python`. Logs go to the local portfolio data directory.

### Linux systemd user timer

```bash
bash scripts/install_weekly_sync_linux.sh install
systemctl --user list-timers talktomyportfolio-weekly-sync.timer
journalctl --user -u talktomyportfolio-weekly-sync.service

# Remove later
bash scripts/install_weekly_sync_linux.sh uninstall
```

The timer uses the configured IANA timezone (`PORTFOLIO_SYNC_TIMEZONE`, default `Asia/Kolkata`) and `Persistent=true` so a missed run starts after the machine returns.

Cron fallback, only when user-level systemd is unavailable:

```cron
CRON_TZ=Asia/Kolkata
30 18 * * 5 cd /absolute/path/talk-to-my-portfolio && .venv/bin/python -m modules.portfolio.scripts.weekly_sync --mode auto
0 9 * * 6 cd /absolute/path/talk-to-my-portfolio && .venv/bin/python -m modules.portfolio.scripts.weekly_sync --mode auto
```

### Windows Task Scheduler

PowerShell task triggers use the Windows system timezone. Run PowerShell with permission to create tasks:

```powershell
.\scripts\Install-WeeklySyncWindows.ps1 -Action Install
Get-ScheduledTask -TaskName "TalkToMyPortfolio Weekly Sync*"

# Remove later
.\scripts\Install-WeeklySyncWindows.ps1 -Action Uninstall
```

## Audit, storage, and recovery

Local files remain under `PORTFOLIO_DATA_DIR` (or `modules/portfolio/data/`):

- `weekly_sync.db`: runs, steps, per-account states, artifacts, and local notification audit;
- `weekly-sync.lock`: process lock, automatically removed by its owner;
- `weekly-digests/*.md` and `*.html`: at most five suggested actions plus freshness and data issues;
- `portfolio_history.db`: idempotent daily and weekly snapshots.

The audit and digest redact common secret assignments. They do not contain internal account IDs or full holdings. Delivery defaults to local files only. No email, webhook, Telegram, or LLM delivery is enabled.

Recovery:

1. `LOCKED`: wait for the active job. A lock older than four hours is treated as stale on the next run.
2. `AUTH_REQUIRED`: reconnect that account in Setup, then rerun `auto`.
3. `IMPORT_REQUIRED`: import the latest manual holdings, then rerun.
4. `FAILED`: inspect the run using `GET /api/portfolio/sync/runs/{run_id}` and local scheduler logs. No snapshot was written when all holdings were unavailable or `live` requirements failed.
5. Interrupted jobs are marked `CANCELLED` at the next safe step boundary. Snapshot upserts and weekly idempotency make a rerun safe.

## API

```text
GET  /api/portfolio/sync/status
GET  /api/portfolio/sync/runs?limit=20
GET  /api/portfolio/sync/runs/{run_id}
POST /api/portfolio/sync/weekly
```

POST body:

```json
{"mode": "auto", "dry_run": false}
```

These endpoints are additive to API v1. The POST is a local decision-support operation; it cannot submit a trade.

## Verification

```bash
source .venv/bin/activate
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python -m pytest -q -p no:cacheprovider
node --check shared/web/static/js/portfolio-weekly-sync.js
python -m modules.portfolio.scripts.weekly_sync --mode auto --dry-run --json
uvicorn main:app --reload --host 127.0.0.1 --port 9000
```

Open Setup, run an `auto` dry run, and confirm:

1. the status changes from Pending to a completed or explicit failed state;
2. every degraded account has one recovery action;
3. no digest or portfolio snapshot is created by the dry run;
4. a real run creates one weekly family snapshot and a local digest;
5. rerunning the same mode during the ISO week returns `SKIPPED_DUPLICATE`.

Known boundary: Milestone 7A reports source freshness and coarse account status. Canonical instrument identity, full broker-versus-marked-value reconciliation, corporate actions, true XIRR/TWRR, and Market Regime & Mood arrive in later milestones.
