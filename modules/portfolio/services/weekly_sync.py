"""Milestone 7A: one auditable, idempotent weekly portfolio sync job."""

from __future__ import annotations

import hashlib
import html
import logging
import os
import re
import signal
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from modules.portfolio import config as portfolio_config
from modules.portfolio.db import profile_goals, weekly_history, weekly_sync as sync_store
from modules.portfolio.paths import DATA_DIR

logger = logging.getLogger(__name__)

VALID_MODES = frozenset({"auto", "live", "safe-fallback"})
SUCCESS_STATUSES = frozenset({"COMPLETED", "COMPLETED_WITH_WARNINGS"})
LIVE_ACCOUNT_STATUSES = frozenset({"LIVE_RECONCILED", "LIVE_WITH_WARNINGS"})
_SECRET_RE = re.compile(
    r"(?i)(api[_ -]?key|api[_ -]?secret|access[_ -]?token|totp|password|authorization)"
    r"\s*[:=]\s*[^\s,;]+"
)
_AUTH_WORDS = ("auth", "token", "login", "session", "credential", "permission", "401", "403")


class WeeklySyncError(RuntimeError):
    """An expected weekly-sync failure with a safe user-facing message."""


class WeeklySyncLocked(WeeklySyncError):
    """Another weekly-sync process owns the lock."""


class WeeklySyncCancelled(WeeklySyncError):
    """The current run was cancelled between safe step boundaries."""


@dataclass
class JobLock:
    path: Path
    stale_after_seconds: int = 4 * 60 * 60
    token: str | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        token = f"{os.getpid()}:{uuid.uuid4().hex}:{time.time()}"
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(token)
                self.token = token
                return
            except FileExistsError:
                try:
                    age = time.time() - self.path.stat().st_mtime
                except FileNotFoundError:
                    continue
                if age <= self.stale_after_seconds:
                    raise WeeklySyncLocked(
                        f"Weekly sync already running (lock age {int(age)} seconds)."
                    )
                try:
                    self.path.unlink()
                except FileNotFoundError:
                    continue
        raise WeeklySyncLocked("Could not acquire the weekly-sync lock.")

    def release(self) -> None:
        if not self.token:
            return
        try:
            try:
                current = self.path.read_text(encoding="utf-8")
            except FileNotFoundError:
                return
            if current == self.token:
                self.path.unlink(missing_ok=True)
        finally:
            self.token = None

    def __enter__(self) -> "JobLock":
        self.acquire()
        return self

    def __exit__(self, *_args: object) -> None:
        self.release()


def sanitize_message(value: object, *, limit: int = 600) -> str:
    """Strip common secret assignments before audit, logs, API, or digest output."""
    text = str(value or "").replace("\n", " ").replace("\r", " ").strip()
    text = _SECRET_RE.sub(lambda match: f"{match.group(1)}=[REDACTED]", text)
    return text[:limit]


def _enabled_account_specs() -> list[dict[str, str]]:
    registries = (
        (portfolio_config.get_enabled_accounts(), "zerodha"),
        (portfolio_config.get_enabled_groww_accounts(), "groww"),
        (portfolio_config.get_enabled_sarwa_accounts(), "sarwa"),
        (portfolio_config.get_enabled_custom_accounts(), "custom"),
    )
    specs: list[dict[str, str]] = []
    for registry, broker in registries:
        for account_id, account in registry.items():
            specs.append(
                {
                    "account_id": account_id,
                    "account_code": str(account.get("code") or account_id).upper(),
                    "broker": broker,
                }
            )
    return sorted(specs, key=lambda item: (item["broker"], item["account_code"]))


def _account_set_hash(specs: list[dict[str, str]]) -> str:
    canonical = [f"{item['broker']}:{item['account_id']}" for item in specs]
    return hashlib.sha256("|".join(sorted(canonical)).encode()).hexdigest()[:16]


def _time_text(value: object, *, fallback: str | None = None) -> str | None:
    if value is None:
        return fallback
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=UTC).isoformat().replace("+00:00", "Z")
    return str(value)


def classify_accounts(
    family: dict[str, Any],
    *,
    mode: str,
    account_specs: list[dict[str, str]],
    price_as_of: str,
) -> list[dict[str, Any]]:
    """Map every enabled account to an explicit, honest Milestone 7A state."""
    blocks = {
        str(block.get("account_id")): block
        for block in family.get("portfolios") or []
        if block.get("account_id")
    }
    errors = {
        str(item.get("account") or "").upper(): item for item in family.get("errors") or []
    }
    prices_refreshed = bool(family.get("ltp_refreshed_offline"))
    fallback_position_as_of = _time_text(family.get("cached_at"))
    results: list[dict[str, Any]] = []

    for spec in account_specs:
        account_id = spec["account_id"]
        code = spec["account_code"]
        broker = spec["broker"]
        block = blocks.get(account_id)
        error = errors.get(code)
        warnings = [sanitize_message(error.get("error"))] if error and error.get("error") else []
        recovery: str | None = None
        position_as_of = fallback_position_as_of
        account_price_as_of: str | None = None

        if block is not None:
            position_as_of = _time_text(block.get("cached_at"), fallback=position_as_of)
            cached = bool(
                mode == "safe-fallback"
                or broker in {"sarwa", "custom"}
                or block.get("stale")
                or block.get("from_cache")
                or block.get("auth_degraded")
                or (error and error.get("using_snapshot"))
            )
            refreshed = bool(prices_refreshed or block.get("ltp_refreshed_offline"))
            if cached and refreshed:
                status = "CACHED_POSITIONS_FRESH_PRICES"
                account_price_as_of = price_as_of
                recovery = (
                    "Import current quantities before relying on quantity changes."
                    if broker in {"sarwa", "custom"}
                    else "Reconnect the broker before relying on quantity changes."
                )
            elif cached:
                status = "STALE_POSITIONS"
                account_price_as_of = _time_text(block.get("cached_at"), fallback=position_as_of)
                recovery = (
                    "Import current holdings and refresh prices."
                    if broker in {"sarwa", "custom"}
                    else "Reconnect the broker and refresh prices."
                )
            elif error:
                status = "LIVE_WITH_WARNINGS"
                account_price_as_of = price_as_of
                recovery = "Review the broker warning before acting."
            else:
                status = "LIVE_RECONCILED"
                position_as_of = price_as_of
                account_price_as_of = price_as_of
        else:
            raw_error = str((error or {}).get("error") or "")
            if broker in {"sarwa", "custom"}:
                status = "IMPORT_REQUIRED"
                recovery = "Import the latest holdings in Setup."
            elif any(word in raw_error.lower() for word in _AUTH_WORDS):
                status = "AUTH_REQUIRED"
                recovery = f"Reconnect {broker.title()} in Setup, then rerun weekly sync."
            else:
                status = "FAILED"
                recovery = "Check local logs and broker availability, then rerun."
            position_as_of = None
            account_price_as_of = None

        results.append(
            {
                **spec,
                "status": status,
                "position_as_of": position_as_of,
                "price_as_of": account_price_as_of,
                "recovery_action": recovery,
                "warnings": warnings,
            }
        )
    return results


def _call_with_timeout(fn: Callable[[], Any], timeout_seconds: float) -> Any:
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="weekly-sync-step")
    future = executor.submit(fn)
    try:
        return future.result(timeout=timeout_seconds)
    except FutureTimeoutError as exc:
        future.cancel()
        raise WeeklySyncError(f"Step timed out after {timeout_seconds:g} seconds.") from exc
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _run_step(
    run_id: str,
    *,
    sequence: int,
    name: str,
    fn: Callable[[], Any],
    timeout_seconds: float,
    retries: int,
    cancel_event: threading.Event,
    sleeper: Callable[[float], None],
) -> Any:
    started_at = time.time()
    for attempt in range(1, retries + 2):
        if cancel_event.is_set():
            raise WeeklySyncCancelled("Weekly sync cancelled before the next step.")
        sync_store.upsert_step(
            run_id,
            step_name=name,
            sequence=sequence,
            status="RUNNING",
            attempts=attempt,
            started_at=started_at,
        )
        try:
            result = _call_with_timeout(fn, timeout_seconds)
            sync_store.upsert_step(
                run_id,
                step_name=name,
                sequence=sequence,
                status="COMPLETED",
                attempts=attempt,
                started_at=started_at,
                finished_at=time.time(),
            )
            return result
        except WeeklySyncCancelled:
            raise
        except Exception as exc:
            safe_error = sanitize_message(exc)
            final = attempt > retries
            sync_store.upsert_step(
                run_id,
                step_name=name,
                sequence=sequence,
                status="FAILED" if final else "RETRYING",
                attempts=attempt,
                started_at=started_at,
                finished_at=time.time() if final else None,
                error=safe_error,
            )
            if final:
                raise WeeklySyncError(f"{name}: {safe_error}") from exc
            sleeper(min(2 ** (attempt - 1), 8))


def _fetch_family(mode: str) -> dict[str, Any]:
    from modules.portfolio.services.portfolio import (
        fetch_cached_family_portfolio,
        fetch_family_portfolio,
    )

    if mode == "safe-fallback":
        return fetch_cached_family_portfolio(with_metrics=True)
    return fetch_family_portfolio(
        with_metrics=True,
        refresh=True,
        stale_ok=mode == "auto",
        persist_history=False,
    )


def _advisory_summary(family: dict[str, Any], generated_at: str) -> dict[str, Any]:
    from modules.portfolio.services.advisory.service import build_advisory_payload

    payload = build_advisory_payload(
        family,
        goals=profile_goals.get_goals(),
        generated_at=generated_at,
    )
    recommendations = payload.get("recommendations") or []
    action_counts: dict[str, int] = {}
    for item in recommendations:
        action = str(item.get("action") or "UNKNOWN")
        action_counts[action] = action_counts.get(action, 0) + 1
    priority = {
        "RECONCILE": 0,
        "SELL": 1,
        "REDUCE": 2,
        "CAP": 3,
        "STRONG_ADD": 4,
        "ADD": 5,
    }
    suggested = sorted(
        (item for item in recommendations if item.get("action") in priority),
        key=lambda item: (
            priority[str(item.get("action"))],
            -float(item.get("action_confidence") or 0),
            str(item.get("symbol") or ""),
        ),
    )[:5]
    no_action = sum(
        action_counts.get(action, 0) for action in ("HOLD", "HOLD_NO_ADD", "WATCH")
    )
    return {
        "schema_version": payload.get("schema_version"),
        "generated_at": payload.get("generated_at"),
        "action_counts": action_counts,
        "no_action_count": no_action,
        "suggested_actions": [
            {
                "symbol": item.get("symbol"),
                "action": item.get("action"),
                "confidence": item.get("action_confidence"),
                "review_trigger": item.get("review_trigger"),
            }
            for item in suggested
        ],
        "by_symbol": {
            str(item.get("symbol")): str(item.get("action")) for item in recommendations
        },
        "evidence_status": payload.get("evidence_status") or {},
    }


def _recommendation_changes(
    current: dict[str, Any], previous: dict[str, Any] | None
) -> list[dict[str, str]]:
    prior = (previous or {}).get("by_symbol") or {}
    changes = []
    for symbol, action in sorted((current.get("by_symbol") or {}).items()):
        old = prior.get(symbol)
        if old != action:
            changes.append({"symbol": symbol, "from": old or "NEW", "to": action})
    return changes


def _digest_text(
    *,
    run_id: str,
    iso_week: str,
    run_status: str,
    family: dict[str, Any],
    accounts: list[dict[str, Any]],
    advisory: dict[str, Any],
    changes: list[dict[str, str]],
) -> str:
    summary = family.get("summary") or {}
    lines = [
        f"# Portfolio weekly digest — {iso_week}",
        "",
        f"Run: `{run_id}` · Status: **{run_status}**",
        "",
        "## Data freshness",
        "",
    ]
    for account in accounts:
        lines.append(
            f"- {account['account_code']} ({account['broker']}): **{account['status']}**; "
            f"positions {account.get('position_as_of') or 'unavailable'}; "
            f"prices {account.get('price_as_of') or 'unavailable'}"
        )
        if account.get("recovery_action"):
            lines.append(f"  Recovery: {account['recovery_action']}")
    lines.extend(
        [
            "",
            "## Family snapshot",
            "",
            f"- Current value: INR {float(summary.get('total_current_value') or 0):,.2f}",
            f"- Invested value: INR {float(summary.get('total_invested') or 0):,.2f}",
            f"- Holdings: {int(summary.get('holdings_count') or 0)}",
            "- True performance: unavailable until Milestone 7C validates dated cash flows.",
            "- Market Regime & Mood: unavailable until Milestone 8.",
            "",
            "## Recommendation changes",
            "",
        ]
    )
    if changes:
        lines.extend(
            f"- {item['symbol']}: {item['from']} → {item['to']}" for item in changes[:10]
        )
    else:
        lines.append("- No deterministic action changes versus the prior successful weekly run.")
    lines.extend(["", "## Review queue", ""])
    actions = advisory.get("suggested_actions") or []
    if actions:
        for item in actions:
            lines.append(
                f"- {item['symbol']}: **{item['action']}** "
                f"(confidence {float(item.get('confidence') or 0):.0f}%)"
            )
    else:
        lines.append("- No suggested actions.")
    lines.extend(
        [
            f"- No-action holdings: {int(advisory.get('no_action_count') or 0)}",
            "",
            "## Data issues",
            "",
        ]
    )
    degraded = [item for item in accounts if item["status"] not in LIVE_ACCOUNT_STATUSES]
    if degraded:
        lines.extend(
            f"- {item['account_code']}: {item['status']} — {item.get('recovery_action') or 'review'}"
            for item in degraded
        )
    else:
        lines.append("- No degraded account states were recorded.")
    lines.extend(
        [
            "",
            "Corporate-action, tax-lot, contribution/detractor, and full value reconciliation "
            "sections will become authoritative in Milestones 7B and 7C.",
            "",
            "Decision support only. No orders were placed.",
            "",
        ]
    )
    return "\n".join(lines)


def _write_digest(
    *,
    run_id: str,
    iso_week: str,
    run_status: str,
    family: dict[str, Any],
    accounts: list[dict[str, Any]],
    advisory: dict[str, Any],
    changes: list[dict[str, str]],
    output_dir: Path,
    created_at: float,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    markdown = _digest_text(
        run_id=run_id,
        iso_week=iso_week,
        run_status=run_status,
        family=family,
        accounts=accounts,
        advisory=advisory,
        changes=changes,
    )
    stem = f"portfolio-weekly-{iso_week}-{run_id[:8]}"
    md_path = output_dir / f"{stem}.md"
    html_path = output_dir / f"{stem}.html"
    md_path.write_text(markdown, encoding="utf-8")
    escaped = html.escape(markdown)
    html_path.write_text(
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>Portfolio weekly digest</title>"
        "<style>body{font:16px/1.55 system-ui;max-width:900px;margin:40px auto;padding:0 20px;"
        "background:#0b1118;color:#e6edf7}pre{white-space:pre-wrap;font:inherit}</style>"
        f"</head><body><pre>{escaped}</pre></body></html>",
        encoding="utf-8",
    )
    digest_hash = hashlib.sha256(markdown.encode()).hexdigest()
    return {
        "markdown_path": str(md_path),
        "html_path": str(html_path),
        "content_hash": digest_hash,
        "created_at": created_at,
    }
def run_weekly_sync(
    *,
    run_id: str | None = None,
    mode: str = "auto",
    dry_run: bool = False,
    requested_by: str = "cli",
    force: bool = False,
    now: datetime | None = None,
    cancel_event: threading.Event | None = None,
    account_specs: list[dict[str, str]] | None = None,
    family_fetcher: Callable[[str], dict[str, Any]] | None = None,
    weekly_writer: Callable[..., list[dict[str, Any]]] | None = None,
    daily_writer: Callable[..., list[dict[str, Any]]] | None = None,
    advisory_builder: Callable[[dict[str, Any], str], dict[str, Any]] | None = None,
    digest_dir: Path | None = None,
    lock_path: Path | None = None,
    step_timeout_seconds: float = 240,
    fetch_retries: int = 2,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Run one weekly sync. CLI, API, and schedulers call this exact service."""
    if mode not in VALID_MODES:
        raise ValueError(f"mode must be one of: {', '.join(sorted(VALID_MODES))}")
    sync_store.init_db()
    now = now or datetime.now(tz=ZoneInfo("Asia/Kolkata"))
    if now.tzinfo is None:
        now = now.replace(tzinfo=ZoneInfo("Asia/Kolkata"))
    now_utc = now.astimezone(UTC)
    started_at = now_utc.timestamp()
    generated_at = now_utc.isoformat().replace("+00:00", "Z")
    iso_week = weekly_history.week_start_for(
        now.astimezone(ZoneInfo("Asia/Kolkata")).date()
    )
    specs = account_specs if account_specs is not None else _enabled_account_specs()
    account_hash = _account_set_hash(specs)
    idempotency_key = f"{iso_week}:{mode}:{account_hash}"
    run_id = run_id or uuid.uuid4().hex

    if not dry_run and not force:
        prior = sync_store.find_completed_run(idempotency_key)
        if prior:
            sync_store.create_run(
                run_id=run_id,
                idempotency_key=idempotency_key,
                iso_week=iso_week,
                mode=mode,
                dry_run=False,
                requested_by=requested_by,
                account_set_hash=account_hash,
                started_at=started_at,
                status="SKIPPED_DUPLICATE",
                duplicate_of=prior["run_id"],
            )
            sync_store.copy_account_results(
                source_run_id=prior["run_id"], target_run_id=run_id
            )
            summary = {
                **(prior.get("summary") or {}),
                "message": "This mode and account set already completed for the ISO week.",
                "duplicate_of": prior["run_id"],
            }
            sync_store.finish_run(
                run_id, status="SKIPPED_DUPLICATE", finished_at=time.time(), summary=summary
            )
            return sync_store.get_run(run_id) or {
                "run_id": run_id,
                "status": "SKIPPED_DUPLICATE",
            }

    sync_store.create_run(
        run_id=run_id,
        idempotency_key=idempotency_key,
        iso_week=iso_week,
        mode=mode,
        dry_run=dry_run,
        requested_by=requested_by,
        account_set_hash=account_hash,
        started_at=started_at,
    )
    cancellation = cancel_event or threading.Event()
    lock = JobLock(lock_path or DATA_DIR / "weekly-sync.lock")

    try:
        with lock:
            if not specs:
                raise WeeklySyncError("No enabled accounts are configured.")
            fetch = family_fetcher or _fetch_family
            family = _run_step(
                run_id,
                sequence=1,
                name="fetch_family_portfolio",
                fn=lambda: fetch(mode),
                timeout_seconds=step_timeout_seconds,
                retries=fetch_retries,
                cancel_event=cancellation,
                sleeper=sleeper,
            )
            accounts = classify_accounts(
                family,
                mode=mode,
                account_specs=specs,
                price_as_of=generated_at,
            )
            for account in accounts:
                sync_store.upsert_account_result(run_id, account)

            holdings_count = sum(
                len(block.get("holdings") or []) for block in family.get("portfolios") or []
            )
            if holdings_count == 0:
                raise WeeklySyncError(
                    "No trusted holdings were available; no snapshots were written."
                )
            if mode == "live" and any(
                account["status"] not in LIVE_ACCOUNT_STATUSES for account in accounts
            ):
                raise WeeklySyncError(
                    "Live mode requires every enabled account to be live; no snapshots were written."
                )

            degraded = [
                account for account in accounts if account["status"] not in LIVE_ACCOUNT_STATUSES
            ]
            run_status = "COMPLETED_WITH_WARNINGS" if degraded else "COMPLETED"
            advisory_fn = advisory_builder or _advisory_summary
            advisory = _run_step(
                run_id,
                sequence=2,
                name="recompute_deterministic_advisory",
                fn=lambda: advisory_fn(family, generated_at),
                timeout_seconds=step_timeout_seconds,
                retries=0,
                cancel_event=cancellation,
                sleeper=sleeper,
            )
            previous = sync_store.latest_artifact("advisory_summary", before=started_at)
            changes = _recommendation_changes(advisory, (previous or {}).get("content"))
            sync_store.add_artifact(
                run_id,
                kind="advisory_summary",
                created_at=time.time(),
                metadata={"change_count": len(changes)},
                content=advisory,
            )

            snapshot_results: dict[str, Any] = {"weekly": [], "daily": []}
            if dry_run:
                sync_store.upsert_step(
                    run_id,
                    step_name="persist_snapshots",
                    sequence=3,
                    status="DRY_RUN",
                    attempts=0,
                    started_at=time.time(),
                    finished_at=time.time(),
                    details={"message": "Snapshot writes intentionally skipped."},
                )
                sync_store.upsert_step(
                    run_id,
                    step_name="generate_digest",
                    sequence=4,
                    status="DRY_RUN",
                    attempts=0,
                    started_at=time.time(),
                    finished_at=time.time(),
                    details={"message": "Digest file writes intentionally skipped."},
                )
            else:
                if weekly_writer is None:
                    from modules.portfolio.services.weekly_recorder import record_family_from_payload

                    weekly_writer = record_family_from_payload
                if daily_writer is None:
                    from modules.portfolio.services.daily_recorder import record_family_from_payload

                    daily_writer = record_family_from_payload

                snapshot_results = _run_step(
                    run_id,
                    sequence=3,
                    name="persist_snapshots",
                    fn=lambda: {
                        "weekly": weekly_writer(
                            family, source=f"weekly_sync:{mode}", week_start=iso_week
                        ),
                        "daily": daily_writer(
                            family,
                            source=f"weekly_sync:{mode}",
                            day_date=now.astimezone(ZoneInfo("Asia/Kolkata")).date().isoformat(),
                        ),
                    },
                    timeout_seconds=step_timeout_seconds,
                    retries=0,
                    cancel_event=cancellation,
                    sleeper=sleeper,
                )
                digest = _run_step(
                    run_id,
                    sequence=4,
                    name="generate_digest",
                    fn=lambda: _write_digest(
                        run_id=run_id,
                        iso_week=iso_week,
                        run_status=run_status,
                        family=family,
                        accounts=accounts,
                        advisory=advisory,
                        changes=changes,
                        output_dir=digest_dir or DATA_DIR / "weekly-digests",
                        created_at=time.time(),
                    ),
                    timeout_seconds=step_timeout_seconds,
                    retries=0,
                    cancel_event=cancellation,
                    sleeper=sleeper,
                )
                digest_artifacts = (
                    ("digest_markdown", "markdown_path"),
                    ("digest_html", "html_path"),
                )
                for kind, path_key in digest_artifacts:
                    sync_store.add_artifact(
                        run_id,
                        kind=kind,
                        path=digest[path_key],
                        content_hash=digest["content_hash"],
                        created_at=digest["created_at"],
                    )
                sync_store.add_notification(
                    run_id,
                    channel="local_file",
                    status="DELIVERED",
                    destination=digest["markdown_path"],
                    created_at=time.time(),
                )

            summary = {
                "accounts_total": len(accounts),
                "degraded_accounts": len(degraded),
                "holdings_count": holdings_count,
                "recommendation_changes": len(changes),
                "no_action_count": advisory.get("no_action_count", 0),
                "snapshots_written": 0
                if dry_run
                else len(snapshot_results.get("weekly") or [])
                + len(snapshot_results.get("daily") or []),
                "execution_enabled": False,
            }
            sync_store.finish_run(
                run_id,
                status=run_status,
                finished_at=time.time(),
                summary=summary,
            )
    except WeeklySyncCancelled as exc:
        sync_store.finish_run(
            run_id,
            status="CANCELLED",
            finished_at=time.time(),
            error=sanitize_message(exc),
        )
    except WeeklySyncLocked as exc:
        sync_store.finish_run(
            run_id,
            status="LOCKED",
            finished_at=time.time(),
            error=sanitize_message(exc),
        )
    except Exception as exc:
        safe_error = sanitize_message(exc)
        logger.error("Weekly sync %s failed: %s", run_id, safe_error)
        sync_store.finish_run(
            run_id,
            status="FAILED",
            finished_at=time.time(),
            error=safe_error,
        )

    return sync_store.get_run(run_id) or {"run_id": run_id, "status": "FAILED"}


def install_signal_cancellation(cancel_event: threading.Event) -> Callable[[], None]:
    """Install SIGINT/SIGTERM handlers for the CLI and return a restore callback."""
    previous: dict[int, Any] = {}

    def handler(signum: int, _frame: object) -> None:
        logger.warning("Cancellation requested by signal %s", signum)
        cancel_event.set()

    for signum in (signal.SIGINT, signal.SIGTERM):
        previous[signum] = signal.getsignal(signum)
        signal.signal(signum, handler)

    def restore() -> None:
        for signum, old_handler in previous.items():
            signal.signal(signum, old_handler)

    return restore
