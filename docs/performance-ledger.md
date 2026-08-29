# Transaction ledger and true performance

Milestone 7C replaces holdings-P&L inference with a local, auditable ledger.

## Import workflow

1. Submit normalized broker, statement, CAS, Sarwa/custom, GIFT, crypto, dividend, or corporate-action rows to the preview endpoint.
2. Review canonical identity, event type, cash-flow direction, and unresolved rows.
3. Commit the preview batch. Exact source rows are idempotent.
4. Roll back the batch if the source was wrong. Only rows inserted by that batch are removed.

The importer never guesses an unknown event type or instrument. Incomplete rows stay in the review queue.

## Return policy

- Family XIRR uses dated external contributions/withdrawals and ending value.
- Transfers between family accounts are excluded from family cash flow.
- Instrument XIRR uses buys, sells, distributions, and ending value.
- TWRR neutralizes external flows and excludes degraded/non-comparable valuation periods.
- Fees, taxes, income, FX, realized/unrealized returns, coverage, and exclusions remain visible.
- Missing cash flows produce `UNAVAILABLE_WITHOUT_CASHFLOWS`; quantity changes are not silently backfilled.

Tax lots use account-specific FIFO with fees/taxes in cost/proceeds. Splits and bonuses transform quantity/cost; demergers remain `LOT_HISTORY_INCOMPLETE` until sourced cost allocation exists. Outputs support planning and CA review only, not final tax filing.

## API

- `POST /api/portfolio/transactions/import/preview`
- `POST /api/portfolio/transactions/import/{batch_id}/commit`
- `POST /api/portfolio/transactions/import/{batch_id}/rollback`
- `GET /api/portfolio/transactions`
- `GET /api/portfolio/transactions/unresolved`
- `GET /api/portfolio/lots`
- `GET /api/portfolio/performance/summary`
- `GET /api/portfolio/performance/series`
- `GET /api/portfolio/performance/attribution`
- `GET /api/portfolio/performance/coverage`
- `GET /api/portfolio/performance/audit.xlsx`

The local database is `modules/portfolio/data/transaction_ledger.db` and must remain gitignored.
