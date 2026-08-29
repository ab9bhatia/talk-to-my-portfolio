# Research workspace

Milestone 9A provides original, local research tools. Research scores rank evidence; they do not create investment actions.

## Instrument adapters

Scorecards select formulas by instrument economics: non-financial equity, bank, NBFC/HFC, insurer, commodity/cyclical, holding company, pre-profit growth, REIT/InvIT, ETF, active mutual fund, and gold/crypto risk sleeve. A bank uses ROA, NPA, and capital adequacy—not industrial debt/equity. An ETF uses tracking error, expense, AUM, and spread—not corporate ROCE.

Every dimension shows formula inputs, missing evidence, source/as-of, coverage, and methodology version. Total score exists only for sorting.

## Safe screens and comparisons

Screens are typed JSON trees with `AND`/`OR`, whitelisted fields, and fixed operators. The engine never evaluates Python, free-form expressions, or SQL. Saved screens migrate to schema v2 and every edit appends a revision.

Comparisons accept two to five instruments. Common metrics align side-by-side; type-specific metrics are excluded with an explanation when instrument economics differ.

## Candidate and thesis governance

- Current holdings and candidate instruments are separate.
- Only candidates explicitly marked `APPROVED` are recommendation-eligible.
- Watchlists require portfolio role, entry condition, desired weight, invalidation, and source evidence.
- Thesis changes append; earlier reasoning is never overwritten.
- Events and ownership changes require source/as-of/verification. Stale or unverified evidence remains visible but cannot change deterministic action.
- Optional LLM explanations receive structured scorecards and elimination reasons only; private account IDs and notes are removed.

## API

Endpoints under `/api/portfolio/research/` cover scorecards, screen runs/saves, candidates, watchlists, thesis history, events, and comparisons. All remain local and execution-disabled.
