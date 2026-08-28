# Advisor V2 operations

Advisor V2 is local decision support. It never places, stages, or submits an order. The stable
mobile API remains unchanged; the endpoints below are additive.

## Action Center

Open `/portfolio/advisor`. It reads the same normalized family payload as `/portfolio`, then:

1. attaches local account profiles for deterministic tax/settlement rules;
2. refreshes the optional local evidence cache;
3. attaches cached chart-pattern scans as timing evidence;
4. produces the versioned `advisor-v2-v1` payload.

Endpoints:

- `GET /api/portfolio/advisory`
- `GET /api/portfolio/advisory/{symbol}`
- `GET /api/portfolio/advisory/deadlines`
- `GET /api/portfolio/advisory/evidence/status`
- `POST /api/portfolio/advisory/rebalance`

The rebalance endpoint only evaluates target weights. Its response always contains
`execution_enabled: false`.

## Chart-pattern conflict policy

Chart patterns are timing evidence, not business quality or an independent trade decision.

- A sourced fundamental/governance `SELL` dominates a bullish setup. The exit remains intact and
  the conflict is recorded.
- A bullish, confirmed/forming setup with detector confidence of at least 55% can reduce or stage
  a `TACTICAL_REDUCE` or `PORTFOLIO_CONSOLIDATION` sale. It does not change the underlying reason.
- A bearish setup can accelerate only a sale already supported by deterministic return or
  portfolio-fit evidence.
- A pattern alone cannot create `ADD`, `REDUCE`, or `SELL`.
- Missing or older-than-10-day pattern dates exclude the pattern from decisions.

## Local evidence provider

The optional provider reads the gitignored file
`modules/portfolio/data/advisory-v2/evidence.json`. Example:

```json
{
  "observations": [
    {
      "symbol": "EXAMPLE",
      "exchange": "NSE",
      "field": "business_thesis",
      "value": "Thesis text derived from the cited filing.",
      "source": "Company FY2026 annual report",
      "source_url": "https://example.com/official-filing",
      "source_type": "official_filing",
      "as_of": "2026-03-31"
    }
  ]
}
```

Decision fields require an authoritative `source_type`: `official_filing`, `exchange`,
`official_amc`, `official_index`, `regulator`, or `tax_authority`. Invalid rows are rejected and
reported in `runtime.provider_refresh.rejected`. Fresh observations are cached in
`advisory_evidence.db`; stale rows remain visible but do not influence a recommendation.

Set `ADVISORY_EVIDENCE_TTL_SECONDS` to change the default seven-day cache lifetime. Loading or
refreshing the Action Center runs the local provider refresh. Provider evidence and raw account
profiles stay local and are not added to external LLM context.

## Groww daily approval

Groww API credentials alone are insufficient when the key requires daily approval. Approve the
key in the Groww Trade API portal, then call:

```bash
curl -X POST http://localhost:9000/talktomyportfolio/api/portfolio/groww/refresh
```

Reload the dashboard with `?refresh=1`, then confirm `accounts_loaded` includes all configured
accounts.
