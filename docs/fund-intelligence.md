# ETF and mutual-fund intelligence

Milestone 9B calculates portfolio overlap from dated constituents—not fund names.

## Scheme identity

The local scheme master stores canonical name, ISIN/ticker, AMC, plan, option, domicile/currency, index/category, AUM, TER, tracking error, tracking difference, manager/tenure, exit load, ETF liquidity, and factsheet source/as-of. Direct and Regular plans and Growth and IDCW options retain separate canonical instrument IDs.

## Look-through policy

- Official AMC factsheets, index files, and other sourced documents enter through a provider-neutral constituent contract.
- Each observation records fund, underlying canonical ID or unresolved label, weight, date, source/type, and coverage.
- Full, top-holdings-only, partial, delayed, stale, and unavailable states remain explicit.
- Fund-of-fund exposure recurses with cycle protection.
- Missing constituents return `LOOKTHROUGH_UNAVAILABLE`; no overlap is guessed.

Family look-through reallocates each fund wrapper's existing value across its underlying exposures, so family value is never added twice. It separately reports direct-stock duplication, common top holdings, and source paths.

## Costs and action boundary

Fund Intelligence reports family-weighted TER, estimated annual wrapper cost, ETF spread/traded value/premium-discount, tracking error and tracking difference separately, and mutual-fund rolling/downside analytics. A trailing three-year return alone is never treated as forward expected return.

Consolidation candidates require high dated overlap and show TER impact, preferred destination, rationale, and mandatory tax/exit-load review. Missing look-through cannot support consolidation.

## API

Additive endpoints under `/api/portfolio/funds/` cover scheme master, holdings ingestion, look-through, pair overlap, family exposure/cost, consolidation, and the audit workbook.
