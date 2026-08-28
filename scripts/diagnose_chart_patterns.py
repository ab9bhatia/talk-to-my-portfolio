"""One-off diagnostic: chart pattern hit rate across portfolio holdings."""

from __future__ import annotations

import json
import os
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# Disable in-memory cache so each run is fresh.
os.environ["CHART_PATTERNS_CACHE_TTL"] = "0"

from modules.portfolio.services.chart_patterns import (  # noqa: E402
    _detect_ascending_triangle,
    _detect_cup_with_handle,
    _detect_double_bottom,
    _detect_head_shoulders,
    _detect_inverse_head_shoulders,
    _load_series,
    analyze_series,
)
from modules.portfolio.services.holdings_view import (  # noqa: E402
    all_holdings_from_view,
    prepare_holdings_view,
)
from modules.portfolio.services.portfolio import fetch_family_portfolio  # noqa: E402

DETECTORS = {
    "inverse_hs": _detect_inverse_head_shoulders,
    "cup_handle": _detect_cup_with_handle,
    "double_bottom": _detect_double_bottom,
    "asc_triangle": _detect_ascending_triangle,
    "head_shoulders": _detect_head_shoulders,
}

OUT = Path(__file__).resolve().parent.parent / "data" / "chart_patterns_diagnostic.json"


def _unique_equity_symbols(holdings: list[dict]) -> list[tuple[str, str | None]]:
    seen: set[str] = set()
    out: list[tuple[str, str | None]] = []
    for h in holdings:
        if h.get("asset_class") == "mf":
            continue
        sym = (h.get("symbol") or "").strip().upper()
        if not sym or sym in seen:
            continue
        seen.add(sym)
        out.append((sym, h.get("exchange")))
    return out


def _scan_one(sym: str, exch: str | None) -> dict:
    series = _load_series(sym, exch)
    if not series:
        return {"symbol": sym, "exchange": exch, "available": False, "patterns": [], "detectors": {}}

    per_detector: dict[str, dict | None] = {}
    for name, fn in DETECTORS.items():
        try:
            per_detector[name] = fn(series)
        except Exception:
            per_detector[name] = None

    patterns = analyze_series(series)
    return {
        "symbol": sym,
        "exchange": exch,
        "available": True,
        "bars": len(series.closes),
        "patterns": patterns,
        "detectors": {k: (v["pattern"] if v else None) for k, v in per_detector.items()},
    }


def main() -> int:
    t0 = time.time()
    family = fetch_family_portfolio(refresh=False, stale_ok=True)
    raw = [h for p in family.get("portfolios", []) for h in p.get("holdings", [])]
    holdings_view = prepare_holdings_view(raw, aggregate_across_accounts=True)
    holdings = all_holdings_from_view(holdings_view)
    symbols = _unique_equity_symbols(holdings)

    print(f"Unique equity symbols: {len(symbols)}", flush=True)
    results: list[dict] = []
    done = 0
    workers = int(os.getenv("CHART_PATTERNS_DIAG_WORKERS", "8"))

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_scan_one, sym, exch): (sym, exch) for sym, exch in symbols}
        for fut in as_completed(futures):
            row = fut.result()
            results.append(row)
            done += 1
            if done % 25 == 0 or done == len(symbols):
                hits = sum(1 for r in results if r.get("patterns"))
                print(f"  progress {done}/{len(symbols)} — hits so far: {hits}", flush=True)

    no_data = [r["symbol"] for r in results if not r.get("available")]
    hits = [r for r in results if r.get("patterns")]
    per_detector = Counter()
    status_counts = Counter()
    pattern_labels = Counter()
    for r in results:
        for name, pat in (r.get("detectors") or {}).items():
            if pat:
                per_detector[name] += 1
        for p in r.get("patterns") or []:
            status_counts[p["status"]] += 1
            pattern_labels[p["pattern"]] += 1

    loaded = len(symbols) - len(no_data)
    summary = {
        "unique_symbols": len(symbols),
        "loaded": loaded,
        "no_data": len(no_data),
        "with_patterns": len(hits),
        "hit_rate_pct": round(100 * len(hits) / max(1, loaded), 1),
        "per_detector": dict(per_detector),
        "by_status": dict(status_counts),
        "by_pattern": dict(pattern_labels),
        "elapsed_sec": round(time.time() - t0, 1),
        "hits": [
            {
                "symbol": r["symbol"],
                "exchange": r.get("exchange"),
                "primary": r["patterns"][0],
                "all_patterns": [p["pattern"] for p in r["patterns"]],
            }
            for r in sorted(hits, key=lambda x: -x["patterns"][0]["confidence"])
        ],
        "no_data_symbols": no_data[:50],
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print("\n=== SUMMARY ===")
    print(f"Loaded: {loaded}/{len(symbols)}")
    print(f"No data: {len(no_data)}")
    print(f"With patterns: {len(hits)} ({summary['hit_rate_pct']}%)")
    print(f"Per detector: {summary['per_detector']}")
    print(f"By status: {summary['by_status']}")
    print(f"By pattern: {summary['by_pattern']}")
    print(f"Elapsed: {summary['elapsed_sec']}s")
    print(f"Written: {OUT}")
    if hits:
        print("\nTop hits:")
        for h in summary["hits"][:15]:
            p = h["primary"]
            print(f"  {h['symbol']}: {p['label']} [{p['status']}] conf={p['confidence']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
