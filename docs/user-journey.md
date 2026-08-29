# TalkToMyPortfolio user journey

This is the acceptance flow for a portfolio owner who wants to connect every account, understand the consolidated book, make a deterministic decision, and then ask a private follow-up question.

## Journey map

| Step | User goal | Primary screen | Successful outcome |
|---|---|---|---|
| 1. Connect | Bring every account into one local portfolio | Setup & Config | Every enabled account shows `Ready` or a specific recovery action; LLM and guardrails are explicit |
| 2. Understand | Verify total value, allocation, holdings, and data freshness | Dashboard | Family totals are masked by default, account status is visible, and every holding can be inspected or filtered |
| 3. Track | Understand change over time | Growth | Period return, drawdown, best recorded day, charts, and breakdowns match the recorded daily snapshots |
| 4. Decide | Turn evidence into a review queue | Action Center | Deterministic recommendations render promptly; chart-pattern timing enriches them without blocking the queue |
| 5. Ask | Explore a decision in plain language | Portfolio Agent | A non-empty question starts a local portfolio-grounded conversation; no question is sent without an explicit click |

## Detailed flow and acceptance criteria

### 1. Setup & Config — connect

1. Open `/portfolio/setup`.
2. Confirm each broker account is enabled and reports its current state.
3. Use the account-specific recovery action for missing or expired credentials.
4. Configure the LLM provider only if Portfolio Agent is required.
5. Set risk, target-return, position, sector, and cash guardrails.
6. Run **Weekly portfolio sync** once in dry-run mode and resolve any account-specific warning.
7. Install the OS scheduler after a successful real run, then return to the dashboard.

Pass conditions:

- Account cards expose one unambiguous status and recovery action.
- Secrets are never displayed after save.
- Saving one account does not overwrite another account's credentials.
- Dashboard navigation respects `APP_ROOT_PATH`.
- Dry run records an audit but never creates portfolio snapshots or digest files.
- Every weekly run distinguishes position freshness from price freshness, captured time from market-session date, and current from stale manual imports.

### 2. Dashboard — understand

1. Verify the data-freshness label and connected-account chips.
2. Reveal family values only when needed; masking is the default.
3. Review allocation, concentration, and the holdings table.
4. Search, sort, group, or filter to isolate a position.
5. Expand a holding to inspect signals and pattern evidence.

Pass conditions:

- Connected-account chips open Setup & Config without leaving a permanent loader.
- Refresh has a visible loading state and resolves to current or clearly labelled snapshot data.
- Filtering never changes the underlying consolidated totals.
- Wide tables scroll inside their panel; the page itself does not overflow horizontally.

### 3. Growth — track

1. Record one snapshot after each market close; do not use intraday refreshes as performance history.
2. Open Growth for the weekly review rather than as the daily landing page.
3. After two closes, select a time range and compare portfolio value, indexed performance, account mix, benchmark, and attribution.
4. Inspect account, market-cap, asset-class, or sector breakdowns; treat invested change as a contribution/trading proxy, not true time-weighted return.

Pass conditions:

- One recorded day shows `Need two daily snapshots` for best-day analysis, never an infinite value.
- One recorded day replaces the empty one-point chart with the next useful recording milestones.
- Partial, stale, or coverage-changed points remain visible but suppress performance claims and explain why.
- Missing history explains how to create the first snapshot.
- Tables remain usable on desktop and mobile without page-level horizontal overflow.

### 4. Action Center — decide

1. Open Action Center.
2. Review the deterministic queue immediately after local evidence loads.
3. While the queue remains usable, let the background chart scan add lifecycle-aware timing.
4. Treat fundamentals and portfolio constraints as authoritative.
5. If a bullish setup conflicts with a reduce/sell decision, keep the fundamental action but stage execution around the setup; never turn a timing signal into an automatic buy.
6. Inspect dated evidence, data-quality flags, constraints, and rule trace before acting.

Pass conditions:

- Baseline decisions load independently of Yahoo Finance pattern latency.
- The notice distinguishes `decisions ready`, `refreshing pattern timing`, `pattern timing ready`, and a recoverable pattern failure.
- Pattern enrichment recomputes decision conflicts rather than visually decorating a stale recommendation.
- Execution remains disabled; the page never places an order.

### 5. Portfolio Agent — ask

1. Open Portfolio Agent.
2. Enter a decision-focused question.
3. Click **Ask portfolio agent**.
4. Review the streamed answer and deterministic portfolio context.
5. Ask follow-ups in the same chat or start a new chat after changing goals.

Pass conditions:

- Empty Ask and Follow-up actions show visible validation and focus the relevant field.
- The UI clearly reports a missing provider key or stream error.
- No prompt is sent until the user explicitly submits it.
- Account IDs, quantities, values, tax details, and proceeds remain local unless the explicit privacy override permits them.

## Daily operating rhythm

### After market close — 5 minutes

1. Open Dashboard and confirm every enabled account is `Live` or clearly labelled as a cached snapshot.
2. Click **Refresh live** once, check account freshness, and investigate only failed or stale accounts.
3. Scan holdings for concentration, unusual price moves, and setup/street conflicts; expand evidence before acting.
4. Open Action Center and review `SELL / REDUCE`, `ADD / BUILD`, and signal conflicts. Fundamentals decide the action; patterns only time execution.
5. Open Growth and record the market close once. A single day is a baseline, five sessions is a useful weekly view, and roughly 20 sessions makes drawdown and benchmark comparisons more meaningful.

### Weekly — 20 minutes

1. Confirm the Friday weekly sync completed; use the Setup recovery action for any degraded account.
2. Open the local weekly digest and review its maximum five suggested actions plus no-action count.
3. Review Growth for portfolio trend, drawdown, benchmark gap, account attribution, and allocation drift.
4. Review Action Center changes since the prior week and inspect evidence dates before making a broker-side trade.
5. Use Portfolio Agent for one focused question, such as concentration risk or why two signals conflict.

### Monthly — 45 minutes

1. Reconfirm goals and allocation guardrails in Setup & Config.
2. Review cash flows, tax lots, stale evidence, duplicated exposure, and positions too small to matter.
3. Record any external broker actions in the source system, refresh all accounts, and verify that the consolidated portfolio reconciles.

For a new exposure or planned sale, open Asset Location after Data Quality is clear. Complete residency, account type, repatriability, permitted instruments, domicile/share class, treaty evidence, and FIFO lots. Compare only `AVAILABLE` scenarios; route `UNKNOWN` and `TAX_REVIEW_REQUIRED` rows through the downloadable CA package before acting outside the app.

Once per week, open System Health before the portfolio review. Resolve degraded SQLite integrity, stale accounts, failed/orphaned sync, or scheduler issues first. Before upgrades, create and validate an encrypted backup. Review the exact external LLM preview before sending sensitive questions; private account/tax context stays excluded by default.

Growth should remain in the product, but it is a review surface—not the daily command center. Today its value-change view can mix market movement with contributions and trades. A future performance milestone should add a cash-flow ledger plus time-weighted return/XIRR before the app presents return attribution as investment skill.

## Failure recovery

| Failure | User-facing recovery |
|---|---|
| Broker login expired | Continue with a labelled snapshot and provide the account-specific login action |
| Account refresh fails | Keep the last safe snapshot, name the affected account, and avoid claiming all accounts are current |
| Weekly job already running | Report `LOCKED`; do not start a second writer |
| Friday and Saturday jobs both run | Execute `INDIA_CLOSE` and `GLOBAL_CLOSE_FINALIZATION` once each; both use Friday's equity session date |
| Manual account is out of date | Report `MANUAL_STALE`; a recent import is `MANUAL_CURRENT`, with position and price dates separated |
| Browser/app restarts during accepted sync | Poll durable SQLite truth; startup marks the orphaned job `INTERRUPTED`, never temporary 404 |
| OAuth reconnect occurs during active sync | Coalesce exactly one forced `MANUAL_RERUN` after the current job finishes |
| Yahoo pattern scan is slow or unavailable | Keep deterministic decisions usable and show pattern timing as pending/unavailable |
| Only one growth snapshot exists | Explain that two snapshots are required for a best-day calculation |
| LLM is unavailable | Keep Dashboard, Growth, and Action Center functional; direct the user to Setup & Config |

## Regression test

Run this after any navigation, data-loading, advisory, or responsive-layout change:

```bash
source .venv/bin/activate
PYTHONPATH=. python -m pytest -q
node --check shared/web/static/js/nav-loader.js
node --check shared/web/static/js/portfolio-growth.js
node --check shared/web/static/js/portfolio-performance.js
node --check shared/web/static/js/portfolio-market-regime.js
node --check shared/web/static/js/portfolio-operating-console.js
node --check shared/web/static/js/portfolio-advisor.js
node --check shared/web/static/js/portfolio-agent.js
node --check shared/web/static/js/portfolio-weekly-sync.js
uvicorn main:app --reload --host 127.0.0.1 --port 9000
```

Then test at both `1280 × 720` and `390 × 844`:

1. Complete Setup → Dashboard → Growth → Action Center → Portfolio Agent.
2. Confirm `document.documentElement.scrollWidth === window.innerWidth` on every screen.
3. Click a connected-account chip and confirm Setup loads and the page loader clears.
4. With one growth day, confirm the best-day card shows the two-snapshot explanation.
5. Confirm Action Center rows appear before pattern timing completes.
6. Click Ask with an empty question and confirm visible validation; do not submit real holdings to an external LLM during a smoke test.

## Trust boundary

Broker credentials, normalized holdings, deterministic evidence, and rule evaluation stay in the local application. Yahoo Finance receives instrument symbols when chart screening runs. The LLM receives only a user-submitted question and the redacted context defined by the portfolio-agent privacy policy. Live order placement is outside this journey and remains disabled by default.
