"""Populate gitignored, lower-confidence advisory screening evidence.

The output contains security symbols but no account IDs, quantities, values, or tax data.
"""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from modules.portfolio.db import advisory_evidence as evidence_store
from modules.portfolio.db import portfolio_cache as market_cache
from modules.portfolio.paths import DATA_DIR
from modules.portfolio.services.advisory.overlap import instrument_type_for
from modules.portfolio.services.advisory.runtime import build_live_advisory
from modules.portfolio.services.advisory.screening_returns import (
    MODEL_VERSION,
    build_screening_return_inputs,
)
from modules.portfolio.services.market_data import get_stock_metrics
from modules.portfolio.services.mf_metrics import get_mf_metrics
from modules.portfolio.services.portfolio import fetch_family_portfolio


DEFAULT_OUTPUT = DATA_DIR / "advisory-v2" / "evidence.json"
GENERATED_SOURCE_TYPES = {"derived_market_model", "market_data"}
MARKET_FIELDS = (
    "trailing_pe",
    "forward_pe",
    "trailing_eps",
    "forward_eps",
    "earnings_growth_pct",
    "revenue_growth_pct",
    "dividend_yield_pct",
    "return_3y_cagr_pct",
    "three_year_average_return_pct",
)


def _key(row: dict[str, Any]) -> tuple[str, str]:
    return (
        str(row.get("symbol") or "").strip().upper(),
        str(row.get("exchange") or "UNKNOWN").strip().upper(),
    )


def _load_existing(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("observations") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise ValueError(f"{path} must contain an observations list")
    return [dict(row) for row in rows if isinstance(row, dict)]


def _unique_holdings(family: dict[str, Any]) -> list[dict[str, Any]]:
    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for block in family.get("portfolios") or []:
        for holding in block.get("holdings") or []:
            key = _key(holding)
            if key[0]:
                unique.setdefault(key, dict(holding))
    return list(unique.values())


def _market_source_url(ticker: str | None) -> str | None:
    return f"https://finance.yahoo.com/quote/{ticker}" if ticker else None


def _enrich_one(
    holding: dict[str, Any],
    *,
    force_refresh: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    row = dict(holding)
    instrument = instrument_type_for(row)
    row["instrument_type"] = instrument
    if not force_refresh:
        cache_key = f"{row.get('symbol')}:{row.get('exchange') or 'NSE'}"
        cached = market_cache.get_yahoo_metrics(cache_key)
        metrics = dict(cached[1]) if cached else {}
        if cached and not metrics.get("market_data_as_of"):
            metrics["market_data_as_of"] = datetime.fromtimestamp(
                cached[0],
                tz=UTC,
            ).date().isoformat()
    elif str(getattr(instrument, "value", instrument)) == "mutual_fund":
        identifier = str(row.get("isin") or row.get("symbol") or "")
        metrics = get_mf_metrics(identifier, row.get("last_price"))
    else:
        metrics = get_stock_metrics(
            str(row.get("symbol") or ""),
            row.get("exchange"),
            row.get("last_price"),
            technical=False,
            force_refresh_base=force_refresh,
        )
    row.update(metrics)
    if str(metrics.get("quote_type") or "").upper() == "ETF":
        row["instrument_type"] = "etf"

    as_of = str(metrics.get("market_data_as_of") or "")
    if not as_of:
        from datetime import date

        as_of = date.today().isoformat()
    ticker = metrics.get("yahoo_ticker")
    source_url = _market_source_url(str(ticker)) if ticker else None
    source = "Yahoo Finance market/estimate data transformed by advisor-screening-v1"
    model = build_screening_return_inputs(
        row,
        source=source,
        as_of=as_of,
        source_url=source_url,
    )
    observations: list[dict[str, Any]] = []
    symbol, exchange = _key(row)
    if model:
        observations.append(
            {
                "symbol": symbol,
                "exchange": exchange,
                "field": "expected_return_inputs",
                "value": model,
                "source": source,
                "source_url": source_url,
                "source_type": "derived_market_model",
                "as_of": as_of,
                "generated_by": MODEL_VERSION,
            }
        )
    for field in MARKET_FIELDS:
        if metrics.get(field) is None:
            continue
        observations.append(
            {
                "symbol": symbol,
                "exchange": exchange,
                "field": field,
                "value": metrics[field],
                "source": "Yahoo Finance market/estimate field",
                "source_url": source_url,
                "source_type": "market_data",
                "as_of": as_of,
                "generated_by": MODEL_VERSION,
            }
        )
    return row, observations


def populate(
    *,
    output: Path,
    workers: int,
    force_refresh: bool,
    ttl_days: int = 7,
) -> dict[str, Any]:
    family = fetch_family_portfolio(refresh=False, stale_ok=True)
    holdings = _unique_holdings(family)
    existing = _load_existing(output)
    preserved = [row for row in existing if row.get("generated_by") != MODEL_VERSION]
    documented_keys = {
        _key(row)
        for row in preserved
        if row.get("field") == "expected_return_inputs"
    }

    generated: list[dict[str, Any]] = []
    modeled_types: Counter[str] = Counter()
    failures = 0
    with ThreadPoolExecutor(max_workers=max(1, min(workers, 8))) as pool:
        futures = {
            pool.submit(_enrich_one, row, force_refresh=force_refresh): _key(row)
            for row in holdings
        }
        for future in as_completed(futures):
            try:
                enriched, observations = future.result()
            except Exception:
                failures += 1
                continue
            key = _key(enriched)
            if key in documented_keys:
                observations = [
                    row for row in observations if row.get("field") != "expected_return_inputs"
                ]
            if any(row.get("field") == "expected_return_inputs" for row in observations):
                instrument_value = enriched.get("instrument_type") or "equity"
                model_type = str(getattr(instrument_value, "value", instrument_value))
                modeled_types[model_type] += 1
            generated.extend(observations)

    fetched_at = time.time()
    expires_at = fetched_at + max(1, ttl_days) * 24 * 60 * 60
    for observation in generated:
        observation["fetched_at"] = fetched_at
        observation["expires_at"] = expires_at

    output.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "advisor-evidence-v1",
        "generated_by": MODEL_VERSION,
        "observations": preserved + sorted(
            generated,
            key=lambda row: (row["symbol"], row["exchange"], row["field"]),
        ),
    }
    temporary = output.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(output)

    evidence_store.delete_provider_source_types("local_json", GENERATED_SOURCE_TYPES)
    advisory = build_live_advisory(
        refresh=False,
        include_patterns=False,
        family=family,
    )
    action_counts = Counter(row["action"] for row in advisory.get("recommendations") or [])
    return {
        "accounts_loaded": family.get("accounts_loaded"),
        "unique_holdings": len(holdings),
        "modeled": sum(modeled_types.values()),
        "modeled_types": dict(sorted(modeled_types.items())),
        "needs_data": advisory.get("evidence_status", {}).get("needs_data", 0),
        "actions": dict(sorted(action_counts.items())),
        "fetch_failures": failures,
        "output": str(output),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Populate local screening-tier return evidence for the Action Center."
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--ttl-days", type=int, default=7)
    parser.add_argument(
        "--cached-only",
        action="store_true",
        help="Use current Yahoo cache instead of forcing fresh market fields.",
    )
    args = parser.parse_args()
    summary = populate(
        output=args.output,
        workers=args.workers,
        force_refresh=not args.cached_only,
        ttl_days=args.ttl_days,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
