"""Research-only MRMI calibration harness with strict forward-date alignment."""

from __future__ import annotations

from collections import defaultdict
from typing import Any


def run_backtest(
    observations: list[dict[str, Any]],
    prices: list[dict[str, Any]],
    *,
    final_test_start: str | None = None,
) -> dict[str, Any]:
    price_by_date = {str(row["date"]): float(row["close"]) for row in prices}
    ordered_dates = sorted(price_by_date)
    horizons = {"1m": 21, "3m": 63, "6m": 126}
    samples: list[dict[str, Any]] = []
    for observation in sorted(observations, key=lambda row: row["as_of"]):
        as_of = str(observation["as_of"])
        eligible = [day for day in ordered_dates if day <= as_of]
        if not eligible:
            continue
        base_day = eligible[-1]
        base_index = ordered_dates.index(base_day)
        sample = {"as_of": as_of, "price_date": base_day, "band": observation["band"], "score": observation["score"]}
        for label, bars in horizons.items():
            forward_index = base_index + bars
            sample[f"forward_{label}_pct"] = (
                round((price_by_date[ordered_dates[forward_index]] / price_by_date[base_day] - 1) * 100, 4)
                if forward_index < len(ordered_dates)
                else None
            )
        sample["partition"] = "FINAL_TEST" if final_test_start and as_of >= final_test_start else "DEVELOPMENT"
        samples.append(sample)
    by_band: dict[str, dict[str, Any]] = {}
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for sample in samples:
        grouped[sample["band"]].append(sample)
    for band, rows in grouped.items():
        by_band[band] = {"samples": len(rows)}
        for label in horizons:
            values = [row[f"forward_{label}_pct"] for row in rows if row[f"forward_{label}_pct"] is not None]
            by_band[band][f"average_{label}_pct"] = round(sum(values) / len(values), 4) if values else None
    return {
        "samples": samples,
        "by_band": by_band,
        "final_test_start": final_test_start,
        "lookahead_guard": "Signal uses observation as_of; returns use only later trading dates.",
        "optimization_policy": "Weights are not optimized solely for historical return.",
    }
