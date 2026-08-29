# Today Brief, stress tests, what-if, and alerts

Milestone 10 turns the app into a material-change operating console. It remains read-only and execution-disabled.

## Today Brief priority

The queue is ordered: reconciliation/hard risk, breached constraints, result/event deadlines, recommendation changes, supported actions, then research/watch items. Unchanged HOLD/WATCH/CAP holdings are counted as no action and omitted from the queue.

## Stress methodology

Built-in scenarios cover small-cap, credit, oil/INR, FII/rates, capex/defence/rail, promoter group, technology, commodity, and global risk-off. Custom scenarios require explicit assumptions and can be saved locally.

Shocks apply to direct holdings and dated ETF/MF look-through, then sector, market-cap, factor, currency, market, asset-class, and promoter-group metadata. Results expose family/account impact, contributors, post-stress allocation, estimated liquidity-to-exit where evidence exists, coverage, assumptions, and limitations. They are deterministic sensitivities—not forecasts.

## What-if policy

Simulations deep-copy the current holdings and never persist proposed holdings. Rule traces show maximum position, sector/promoter/small-cap caps, cash buffer, turnover, approved candidate/account eligibility, and tax/CA-review blocks. Before/after output includes concentration, holding count, expected-return scenarios, stress, TER, overlap, liquidity, cash, and verified after-tax estimates where available. `execution_enabled` is always false.

## Alert policy

Alerts exist only for material action/trigger/pattern/deadline/reconciliation/evidence/constraint/MRMI/import events. Price noise is not an alert. Persistent state hashes, hysteresis, and cooldown suppress unchanged repeats.

## API

- `GET /api/portfolio/today-brief`
- `GET /api/portfolio/stress/scenarios`
- `POST /api/portfolio/stress/run`
- `POST /api/portfolio/what-if`
- `GET /api/portfolio/alerts`
- `POST /api/portfolio/alerts/evaluate`
