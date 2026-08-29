#!/usr/bin/env python3
"""Run the unified, idempotent weekly portfolio sync once."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.portfolio.services.weekly_sync import (
    SUCCESS_STATUSES,
    VALID_MODES,
    SyncStage,
    install_signal_cancellation,
    run_weekly_sync,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the auditable weekly portfolio sync job exactly once."
    )
    parser.add_argument(
        "--mode",
        choices=sorted(VALID_MODES),
        default="auto",
        help=(
            "auto=live with safe fallback; live=all accounts live; "
            "safe-fallback=durable quantities"
        ),
    )
    parser.add_argument(
        "--stage",
        choices=[stage.value for stage in SyncStage],
        default=None,
        help="Override the inferred Friday/Saturday/manual market-session stage.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch, classify, and evaluate without snapshots or digest files.",
    )
    parser.add_argument(
        "--json", action="store_true", help="Print the complete run record as JSON."
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    cancel_event = threading.Event()
    restore_signals = install_signal_cancellation(cancel_event)
    try:
        result = run_weekly_sync(
            mode=args.mode,
            dry_run=args.dry_run,
            requested_by="cli",
            cancel_event=cancel_event,
            stage=args.stage,
        )
    finally:
        restore_signals()

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        summary = result.get("summary") or {}
        print(
            f"Weekly sync {result.get('status')} · run={result.get('run_id')} · "
            f"week={result.get('iso_week')} · mode={result.get('mode')}"
        )
        print(
            f"Accounts={summary.get('accounts_total', 0)} · "
            f"degraded={summary.get('degraded_accounts', 0)} · "
            f"snapshot rows={summary.get('snapshots_written', 0)}"
        )
        if result.get("error"):
            print(f"Error: {result['error']}", file=sys.stderr)
        for artifact in result.get("artifacts") or []:
            if artifact.get("kind") == "digest_markdown":
                print(f"Digest: {artifact.get('path')}")

    return 0 if result.get("status") in SUCCESS_STATUSES | {"SKIPPED_DUPLICATE"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
