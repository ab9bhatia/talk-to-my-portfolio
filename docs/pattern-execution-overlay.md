# Pattern execution overlay

Status: Stage 6A implemented on 2026-08-28

## Purpose and authority

Chart patterns are deterministic execution-timing evidence. They do not establish
business quality, valuation, expected return, tax treatment, or a buy/sell thesis.
The Advisor V2 fundamental action and sell taxonomy remain authoritative. A hard
governance or structural-risk sell cannot be cancelled by a bullish chart.

Stage 6A corrects response semantics without introducing the full technical-overlay
matrix. Stages 6B and 6C will add confirmation features and explicit staged execution
plans in separate reviewable changes.

## Lifecycle and target state

Legacy status is retained and mapped additively:

| Legacy status | Lifecycle |
|---|---|
| `early` | `BUILDING` |
| `forming` | `NEAR_BREAKOUT` |
| `confirmed` | `CONFIRMED` unless completed or expired |

The full lifecycle vocabulary is `BUILDING`, `NEAR_BREAKOUT`, `CONFIRMED`,
`RETESTING`, `FAILED_BREAKOUT`, `TARGET_ACHIEVED`, `TARGET_OVERSHOT`, `EXPIRED`,
and `INVALIDATED`. Stage 6A emits the states derivable from existing OHLC geometry;
Stage 6B will supply retest, failed-breakout, and invalidation evidence.

`target_status` is one of `ACTIVE`, `ACHIEVED`, `OVERSHOT`, `EXPIRED`, or
`INVALIDATED`. For bullish patterns, price at/above the measured target completes
the target; price at least 3% beyond it is overshot. Bearish targets apply the
symmetric rule. A completed target has zero remaining active upside/downside and
cannot affect execution timing.

A confirmed setup expires when the sessions since its detected right edge exceed
the maximum heuristic horizon. Persistence of detection-time state transitions is
deferred to the dedicated Stage 6 persistence work.

## Score, probability, and horizon

`heuristic_score` is the existing detector shape score on a 0–100 scale. It is shown
as `x/100`, never as a percentage. `confidence` remains as a deprecated compatibility
alias. `calibrated_target_hit_probability` and `sample_size` remain null until a
leakage-safe out-of-sample backtest provides enough observations.

Exact geometric target dates are removed. The compatibility key `target_date`
remains present with a null value. `estimated_horizon` returns minimum, median, and
maximum trading sessions with method `heuristic_until_calibrated`. The current range
is deliberately broad: 0.5x, 1x, and 1.75x the detector duration, with a five-session
minimum. Stage 6E will replace these ratios with empirical percentiles.

## Additive response example

```json
{
  "status": "confirmed",
  "confidence": 82,
  "target_price": 125.0,
  "upside_to_target_pct": 25.0,
  "lifecycle_state": "CONFIRMED",
  "heuristic_score": 82,
  "confidence_semantics": "heuristic_shape_score",
  "calibrated_target_hit_probability": null,
  "target_status": "ACTIVE",
  "current_price": 100.0,
  "measured_target": 125.0,
  "remaining_upside_pct": 25.0,
  "remaining_downside_pct": 0.0,
  "currency": "INR",
  "target_date": null,
  "estimated_horizon": {
    "min_trading_days": 20,
    "median_trading_days": 40,
    "max_trading_days": 70,
    "method": "heuristic_until_calibrated"
  }
}
```

The existing `/api/portfolio/patterns` and `/api/portfolio/patterns/{symbol}`
routes and legacy fields remain intact. The advisory adapter also accepts a legacy
pattern payload and derives safe lifecycle/currency defaults.

## Currency policy

Use the explicit holding/instrument currency when present. Otherwise, map U.S.
exchanges (`US`, `NASDAQ`, `NYSE`, `ARCA`, `AMEX`, `BATS`) to USD and NSE/BSE to
INR. The UI formats values with `Intl.NumberFormat`; it never hardcodes the rupee
symbol for a pattern target.

## Known limitations and next stages

Stage 6A still uses adjusted daily OHLC without volume. The following fields are not
yet claimed: breakout volume ratio, ATR, moving averages, benchmark-relative strength,
invalidation level, retest confirmation, failed-breakout state, persistence history,
or a calibrated hit probability.

- Stage 6B: OHLCV, ATR, moving averages, benchmark mapping, invalidation, retest,
  failed breakout, and explicit missing-data flags.
- Stage 6C: separate fundamental action, technical overlay, final staged action,
  and execution plan with stop/time/target rules.
- Stage 6E: leakage-safe research harness, costs, benchmark excess return,
  out-of-sample calibration, sample-size gates, and reproducible reports.
