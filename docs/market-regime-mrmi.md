# India Market Regime & Mood Index (MRMI)

MRMI is an original, transparent execution-context score. It is not a return forecast and is not a copy of any third-party market-mood product.

## Methodology v1

| Component | Weight | Normalization |
|---|---:|---|
| Market breadth | 20% | Percentage strength clipped between 20 and 80 |
| Index momentum | 20% | Six-month return clipped between -20% and +20% |
| Volatility regime | 15% | Volatility percentile, inverted |
| FPI flow regime | 15% | Flow z-score clipped between -2 and +2 |
| Participation strength | 10% | Breadth/price-strength range clipped between -10 and +10 |
| Derivatives sentiment | 10% | Sourced standardized signal clipped between -2 and +2 |
| Valuation stretch | 5% | Valuation percentile, inverted |
| Safe-haven/liquidity | 5% | Stress percentile, inverted |

Every component stores raw value, formula, lookback, normalized score, configured/effective weight, source, source date, and freshness. Missing components are removed and remaining weights renormalize; confidence falls with coverage, staleness, short history, and component disagreement.

Bands are stable: `[0,20)` extreme fear, `[20,40)` fear, `[40,60)` neutral, `[60,80)` greed, and `[80,100]` extreme greed. Trend needs a prior observation and changes only when the score moves by at least three points.

## Decision boundary

MRMI may adjust tranche size, deployment pace, cash-buffer posture, retest discipline, supported-reduction urgency, and alert priority. It cannot create a BUY/SELL, cancel a fundamental/governance sell, bypass reconciliation, override account/tax constraints, or enable execution.

Only `FINALIZED` observations enter the weekly digest. Provisional/backfilled observations remain visible in history with their state.

## API

- `GET /api/portfolio/market-regime/current`
- `GET /api/portfolio/market-regime/history`
- `GET /api/portfolio/market-regime/methodology`
- `POST /api/portfolio/market-regime/observations`

The POST endpoint requires sourced component inputs; it never fabricates live values. Research calibration is isolated in `mrmi_backtest.py`, aligns signals only with later prices, and preserves an optional final test partition.
