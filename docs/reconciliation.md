# Instrument identity and reconciliation

Milestone 7B adds the local trust layer used before valuation-dependent advice.

## Operating flow

1. Refresh the family portfolio through the existing canonical holdings pipeline.
2. Resolve each position by authoritative identity: instrument ID, ISIN, broker ID, Yahoo ticker, then exchange-aware symbol.
3. Preserve broker-reported price, value, and P&L separately from independent market marks.
4. Reconcile at position, canonical security, account, and family levels.
5. Open **Data Quality** and repair blocking discrepancies with sourced, audited local overrides.
6. Return to **Action Center** only after blocking identity/value issues are explained.

## States

| State | Meaning | Advisory effect |
|---|---|---|
| `RECONCILED` | Source values agree within tolerances | No block |
| `RECONCILED_WITH_TIMING_DIFFERENCE` | Small source/session timing difference | Visible, non-blocking |
| `WARNING` | Explained or material non-blocking issue | Confidence may fall |
| `BLOCKING_MISMATCH` | Material value discrepancy | Expected return unavailable; `RECONCILE` |
| `CORPORATE_ACTION_REVIEW` | Split, merger, demerger, or lineage needs review | `RECONCILE` |
| `UNRESOLVED_IDENTITY` | No stable canonical identity | BUY/SELL blocked; `RECONCILE` |

`broker_reported_value` is never replaced by `marked_value`. Each price/value retains source, timestamp, market-session date, currency, and FX evidence when present.

## Manual resolution

Use **Data Quality → Review mismatch**. First compare the broker/import source with the independent market-price source and check quantity, timestamp, currency/FX, and corporate actions. A resolution requires a typed explanation, reason, source document, evidence date, and approver. Saving inserts an append-only row in `reconciliation_overrides` plus its audit row. The original values remain unchanged. Overrides explain discrepancies; they do not enable execution.

## API

- `GET /api/portfolio/instruments`
- `POST /api/portfolio/instruments/resolve`
- `GET /api/portfolio/reconciliation/summary`
- `GET /api/portfolio/reconciliation/detail`
- `GET /api/portfolio/reconciliation/unresolved`
- `GET /api/portfolio/reconciliation/corporate-actions`
- `POST /api/portfolio/reconciliation/overrides`

All data is stored locally in `modules/portfolio/data/instrument_master.db`, which is gitignored and must not be shared or committed.
