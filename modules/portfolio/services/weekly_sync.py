"""Milestone 7A: one auditable, idempotent weekly portfolio sync job."""

from __future__ import annotations

import hashlib
import html
import json
import logging
import os
import signal
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from modules.portfolio import config as portfolio_config
from modules.portfolio.db import profile_goals, weekly_history, weekly_sync as sync_store
from modules.portfolio.paths import DATA_DIR
from modules.portfolio.services.snapshot_quality import snapshot_metadata

logger = logging.getLogger(__name__)

VALID_MODES = frozenset({"auto", "live", "safe-fallback"})
SUCCESS_STATUSES = frozenset({"COMPLETED", "COMPLETED_WITH_WARNINGS"})
LIVE_ACCOUNT_STATUSES = frozenset({"LIVE_RECONCILED", "LIVE_WITH_WARNINGS"})
LIVE_MODE_ACCEPTED_STATUSES = frozenset(
    {"LIVE_RECONCILED", "LIVE_WITH_WARNINGS", "MANUAL_CURRENT"}
)
VALUATION_POLICY_VERSION = "weekly-valuation-v2"
_AUTH_WORDS = ("auth", "token", "login", "session", "credential", "permission", "401", "403")


class WeeklySyncError(RuntimeError):
    """An expected weekly-sync failure with a safe user-facing message."""


class WeeklySyncLocked(WeeklySyncError):
    """Another weekly-sync process owns the lock."""


class WeeklySyncCancelled(WeeklySyncError):
    """The current run was cancelled between safe step boundaries."""


class WeeklySyncStepTimeout(WeeklySyncError):
    """A step exceeded its deadline and was serialized until its worker exited."""


class SyncStage(StrEnum):
    INDIA_CLOSE = "INDIA_CLOSE"
    GLOBAL_CLOSE_FINALIZATION = "GLOBAL_CLOSE_FINALIZATION"
    MANUAL_RERUN = "MANUAL_RERUN"


def infer_sync_stage(now: datetime) -> SyncStage:
    local = now.astimezone(ZoneInfo("Asia/Kolkata"))
    if local.weekday() == 4:
        return SyncStage.INDIA_CLOSE
    if local.weekday() == 5:
        return SyncStage.GLOBAL_CLOSE_FINALIZATION
    return SyncStage.MANUAL_RERUN


def market_session_date_for(stage: SyncStage, now: datetime) -> str:
    """Return the equity valuation date; Saturday finalizes Friday, never Saturday."""
    local_date = now.astimezone(ZoneInfo("Asia/Kolkata")).date()
    if stage is SyncStage.GLOBAL_CLOSE_FINALIZATION:
        local_date -= timedelta(days=1)
    while local_date.weekday() >= 5:
        local_date -= timedelta(days=1)
    return local_date.isoformat()


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
    from shared.security_redaction import redact_text

    return redact_text(value, limit=limit)


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


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.fromtimestamp(float(value), tz=UTC)
        except (TypeError, ValueError, OSError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo("Asia/Kolkata"))
    return parsed.astimezone(UTC)


def classify_accounts(
    family: dict[str, Any],
    *,
    mode: str,
    account_specs: list[dict[str, str]],
    price_as_of: str,
    now: datetime | None = None,
    manual_current_hours: float | None = None,
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
    current = (now or datetime.now(tz=UTC)).astimezone(UTC)
    if manual_current_hours is None:
        try:
            manual_current_hours = float(
                os.getenv("PORTFOLIO_MANUAL_CURRENT_HOURS", "168")
            )
        except ValueError:
            manual_current_hours = 168.0
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
            if broker in {"sarwa", "custom"}:
                imported_at = _parse_time(position_as_of)
                age_hours = (
                    (current - imported_at).total_seconds() / 3600
                    if imported_at is not None
                    else None
                )
                refreshed = bool(prices_refreshed or block.get("ltp_refreshed_offline"))
                account_price_as_of = price_as_of if refreshed else position_as_of
                if (
                    age_hours is not None
                    and 0 <= age_hours <= manual_current_hours
                ):
                    status = "MANUAL_CURRENT"
                    recovery = None
                else:
                    status = "MANUAL_STALE"
                    recovery = "Import current holdings before relying on quantity changes."
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
                continue
            cached = bool(
                mode == "safe-fallback"
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


def _call_with_timeout(
    fn: Callable[[], Any],
    timeout_seconds: float,
    *,
    on_timeout: Callable[[], None],
) -> Any:
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="weekly-sync-step")
    future = executor.submit(fn)
    try:
        return future.result(timeout=timeout_seconds)
    except FutureTimeoutError as exc:
        on_timeout()
        # A running Python thread cannot be killed. Wait until it exits before the
        # caller is allowed to retry, so two broker/cache mutations never overlap.
        try:
            future.result()
        except Exception:
            pass
        raise WeeklySyncStepTimeout(
            f"Step timed out after {timeout_seconds:g} seconds; its worker exited before retry."
        ) from exc
    finally:
        executor.shutdown(wait=True, cancel_futures=True)


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
            result = _call_with_timeout(
                fn,
                timeout_seconds,
                on_timeout=lambda: sync_store.upsert_step(
                    run_id,
                    step_name=name,
                    sequence=sequence,
                    status="TIMED_OUT_BUT_STILL_RUNNING",
                    attempts=attempt,
                    started_at=started_at,
                    details={"retry_blocked_until_worker_exit": True},
                ),
            )
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


def _fetch_family(mode: str, stage: SyncStage) -> dict[str, Any]:
    from modules.portfolio.services.portfolio import (
        fetch_cached_family_portfolio,
        fetch_family_portfolio,
    )

    if mode == "safe-fallback" or stage is SyncStage.GLOBAL_CLOSE_FINALIZATION:
        # Global finalization refreshes market prices over the last durable
        # quantities. It must never replace quantities from a late broker read.
        return fetch_cached_family_portfolio(with_metrics=True)
    return fetch_family_portfolio(
        with_metrics=True,
        refresh=True,
        stale_ok=mode == "auto",
        persist_history=False,
    )


def _previous_history_snapshot(
    *,
    cadence: str,
    current_period: str,
) -> dict[str, Any] | None:
    if cadence == "weekly":
        rows = weekly_history.list_snapshots(scope="family", account_id=None, limit=104)
        period_key = "week_start"
    else:
        from modules.portfolio.db import daily_history

        rows = daily_history.list_snapshots(scope="family", account_id=None, limit=365)
        period_key = "day_date"
    return next(
        (row for row in rows if str(row.get(period_key) or "") < current_period),
        None,
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
    material: dict[str, dict[str, Any]] = {}
    urgent: list[dict[str, Any]] = []
    execution_ready: list[dict[str, Any]] = []
    research: list[dict[str, Any]] = []
    tax_review: list[dict[str, Any]] = []

    for item in recommendations:
        symbol = str(item.get("symbol") or "UNKNOWN")
        identity = str(
            item.get("instrument_id")
            or item.get("isin")
            or item.get("security_key")
            or symbol
        )
        flags = item.get("data_quality_flags") or []
        flag_codes = {
            str(flag.get("code") or "DATA_QUALITY_WARNING") for flag in flags
        }
        blocking = [
            str(flag.get("code") or "BLOCKING_DATA_QUALITY")
            for flag in flags
            if flag.get("blocking")
        ]
        expected = item.get("expected_3y_irr") or {}
        pattern = item.get("chart_pattern") or {}
        review_trigger = {
            "hold_until": item.get("hold_until") or {},
            "add_conditions": item.get("add_conditions") or [],
            "exit_triggers": item.get("exit_triggers") or [],
        }
        row = {
            "identity": identity,
            "symbol": symbol,
            "action": item.get("action"),
            "sell_type": item.get("sell_type"),
            "sell_pct": item.get("sell_pct"),
            "target_weight_pct": item.get("target_weight_pct"),
            "bear_pct": expected.get("bear_pct"),
            "base_pct": expected.get("base_pct"),
            "bull_pct": expected.get("bull_pct"),
            "confidence": item.get("action_confidence"),
            "evidence_state": item.get("evidence_state"),
            "blocking_flags": sorted(blocking),
            "requires_ca_review": bool(item.get("requires_ca_review")),
            "chart_pattern_lifecycle": pattern.get("lifecycle_state"),
            "review_trigger": review_trigger,
        }
        material[identity] = row

        action = str(item.get("action") or "WATCH")
        execution_blocked = bool(
            blocking
            or item.get("requires_ca_review")
            or item.get("evidence_state") == "NEEDS_DATA"
            or bool(
                flag_codes.intersection(
                    {"STALE_EXTERNAL_EVIDENCE", "STALE_PORTFOLIO_SNAPSHOT"}
                )
            )
            or action == "RECONCILE"
        )
        if blocking or action == "RECONCILE":
            urgent.append({**row, "reason": ", ".join(blocking) or "RECONCILE"})
        if item.get("requires_ca_review"):
            tax_review.append(row)
        if action in priority and not execution_blocked:
            execution_ready.append(row)
        else:
            research.append(row)

    execution_ready.sort(
        key=lambda item: (
            priority.get(str(item.get("action")), 99),
            -float(item.get("confidence") or 0),
            str(item.get("symbol") or ""),
        )
    )
    suggested = execution_ready[:5]
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
                "confidence": item.get("confidence"),
                "review_trigger": item.get("review_trigger"),
            }
            for item in suggested
        ],
        "urgent_data_risk_issues": urgent,
        "execution_ready_actions": execution_ready,
        "research_watch_actions": research,
        "tax_ca_review_actions": tax_review,
        "by_security": material,
        "by_symbol": {
            str(item.get("symbol")): str(item.get("action")) for item in recommendations
        },
        "evidence_status": payload.get("evidence_status") or {},
    }


def _recommendation_changes(
    current: dict[str, Any], previous: dict[str, Any] | None
) -> list[dict[str, Any]]:
    prior = (previous or {}).get("by_security") or {}
    changes: list[dict[str, Any]] = []
    fields = (
        "action",
        "sell_type",
        "sell_pct",
        "target_weight_pct",
        "bear_pct",
        "base_pct",
        "bull_pct",
        "confidence",
        "evidence_state",
        "blocking_flags",
        "requires_ca_review",
        "chart_pattern_lifecycle",
        "review_trigger",
    )
    for identity, current_row in sorted((current.get("by_security") or {}).items()):
        old_row = prior.get(identity)
        if old_row is None:
            changes.append(
                {
                    "identity": identity,
                    "symbol": current_row.get("symbol"),
                    "changed_fields": ["NEW"],
                    "from": "NEW",
                    "to": current_row.get("action"),
                }
            )
            continue
        changed = [
            field
            for field in fields
            if json.dumps(old_row.get(field), sort_keys=True, default=str)
            != json.dumps(current_row.get(field), sort_keys=True, default=str)
        ]
        if changed:
            changes.append(
                {
                    "identity": identity,
                    "symbol": current_row.get("symbol"),
                    "changed_fields": changed,
                    "from": old_row.get("action"),
                    "to": current_row.get("action"),
                }
            )
    return changes


def _digest_text(
    *,
    run_id: str,
    iso_week: str,
    run_status: str,
    family: dict[str, Any],
    accounts: list[dict[str, Any]],
    advisory: dict[str, Any],
    changes: list[dict[str, Any]],
    stage: str,
    market_session_date: str,
    snapshot_meta: dict[str, Any],
) -> str:
    summary = family.get("summary") or {}
    from modules.portfolio.db import daily_history as daily_history_store
    from modules.portfolio.db import market_regime
    from modules.portfolio.db import transaction_ledger
    from modules.portfolio.services.performance import calculate_twrr

    performance_lines: list[str] = []
    transactions = transaction_ledger.list_transactions(limit=10000)
    for label, days in (("Weekly", 8), ("Monthly", 32)):
        snapshots = daily_history_store.growth_series(
            scope="family", account_id=None, days=days
        )
        result = calculate_twrr(snapshots, transactions, scope="family")
        if result.get("twrr_pct") is not None and not result.get("excluded_periods"):
            performance_lines.append(f"- {label} TWRR: {float(result['twrr_pct']):.2f}%")
    if not performance_lines:
        performance_lines.append(
            "- True weekly/monthly performance: unavailable until dated cash-flow and valuation coverage is complete."
        )
    mood = market_regime.latest(market="INDIA", finalized_only=True)
    mood_line = (
        f"- Market Regime & Mood: **{mood['score']:.0f}/100 {mood['band'].replace('_', ' ')}**; "
        f"{mood['trend'].lower()}; confidence {mood['confidence']}%; as of {mood['as_of']}."
        if mood
        else "- Market Regime & Mood: unavailable until a sourced daily observation is finalized."
    )
    lines = [
        f"# Portfolio weekly digest — {iso_week}",
        "",
        f"Run: `{run_id}` · Status: **{run_status}** · Stage: **{stage}**",
        f"Market session: **{market_session_date}** · Captured separately in the run audit",
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
            f"- Snapshot quality: **{snapshot_meta.get('snapshot_quality')}**; "
            f"coverage {float(snapshot_meta.get('coverage_pct') or 0):.1f}%",
            f"- Comparable to prior period: **{snapshot_meta.get('comparable_to_previous')}**"
            + (
                f" — {', '.join(snapshot_meta.get('comparability_reasons') or [])}"
                if snapshot_meta.get("comparability_reasons")
                else ""
            ),
            *performance_lines,
            mood_line,
            "",
            "## Recommendation changes",
            "",
        ]
    )
    if changes:
        lines.extend(
            f"- {item['symbol']}: {item['from']} → {item['to']} "
            f"({', '.join(item.get('changed_fields') or [])})"
            for item in changes[:10]
        )
    else:
        lines.append("- No deterministic action changes versus the prior successful weekly run.")
    lines.extend(["", "## Urgent data / risk issues", ""])
    urgent = advisory.get("urgent_data_risk_issues") or []
    degraded = [
        item for item in accounts if item["status"] not in LIVE_MODE_ACCEPTED_STATUSES
    ]
    if degraded:
        lines.extend(
            f"- {item['account_code']}: {item['status']} — "
            f"{item.get('recovery_action') or 'review'}"
            for item in degraded
        )
    if urgent:
        lines.extend(
            f"- {item['symbol']}: {item.get('reason') or 'blocking review required'}"
            for item in urgent[:5]
        )
    if not degraded and not urgent:
        lines.append("- No urgent data or deterministic risk issues.")

    lines.extend(["", "## Execution-ready actions", ""])
    execution = advisory.get("execution_ready_actions") or []
    if execution:
        for item in execution[:5]:
            lines.append(
                f"- {item['symbol']}: **{item['action']}** "
                f"(confidence {float(item.get('confidence') or 0):.0f}%)"
            )
    else:
        lines.append("- None. Blocking flags, missing evidence, and CA review are excluded.")

    remaining = max(0, 5 - len(execution[:5]))
    lines.extend(["", "## Research / watch actions", ""])
    research = advisory.get("research_watch_actions") or []
    if research and remaining:
        lines.extend(
            f"- {item['symbol']}: **{item.get('action') or 'WATCH'}** — "
            f"{item.get('evidence_state') or 'review evidence'}"
            for item in research[:remaining]
        )
    else:
        lines.append("- No additional research/watch items in the five-action digest budget.")

    lines.extend(["", "## Tax / CA-review-required actions", ""])
    tax_review = advisory.get("tax_ca_review_actions") or []
    if tax_review:
        lines.extend(
            f"- {item['symbol']}: {item.get('action') or 'REVIEW'} — CA review required; "
            "not execution-ready."
            for item in tax_review[:5]
        )
    else:
        lines.append("- None.")
    lines.extend(
        [
            "",
            "## No-action count",
            "",
            f"- No-action holdings: {int(advisory.get('no_action_count') or 0)}",
            "",
            "## Quote refresh coverage",
            "",
        ]
    )
    quote = family.get("quote_refresh") or {}
    lines.extend(
        [
            f"- Requested/resolved: {int(quote.get('requested_securities') or 0)} / "
            f"{int(quote.get('resolved_securities') or 0)}",
            f"- Count coverage: {float(quote.get('count_coverage_pct') or 0):.1f}%",
            f"- Value-weighted coverage: {float(quote.get('value_weighted_coverage_pct') or 0):.1f}%",
            f"- Stale prices retained: {', '.join(quote.get('stale_symbols') or []) or 'none'}",
            f"- Unresolved: {', '.join(quote.get('unresolved_symbols') or []) or 'none'}",
        ]
    )
    lines.extend(
        [
            "",
            "Corporate-action, tax-lot, cash-flow, and reconciliation evidence remain local and auditable.",
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
    changes: list[dict[str, Any]],
    output_dir: Path,
    created_at: float,
    stage: str,
    market_session_date: str,
    snapshot_meta: dict[str, Any],
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
        stage=stage,
        market_session_date=market_session_date,
        snapshot_meta=snapshot_meta,
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
    stage: str | SyncStage | None = None,
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
    sync_stage = SyncStage(stage) if stage is not None else infer_sync_stage(now)
    market_session_date = market_session_date_for(sync_stage, now)
    started_at = now_utc.timestamp()
    generated_at = now_utc.isoformat().replace("+00:00", "Z")
    iso_week = weekly_history.week_start_for(
        now.astimezone(ZoneInfo("Asia/Kolkata")).date()
    )
    specs = account_specs if account_specs is not None else _enabled_account_specs()
    account_hash = _account_set_hash(specs)
    idempotency_key = (
        f"{iso_week}:{sync_stage.value}:{mode}:{account_hash}:"
        f"{VALUATION_POLICY_VERSION}"
    )
    run_id = run_id or uuid.uuid4().hex
    queued_run = sync_store.get_run(run_id)

    if not dry_run and not force:
        prior = sync_store.find_completed_run(idempotency_key)
        if prior:
            if queued_run and queued_run.get("status") == "QUEUED":
                sync_store.start_queued_run(
                    run_id,
                    idempotency_key=idempotency_key,
                    iso_week=iso_week,
                    account_set_hash=account_hash,
                    started_at=started_at,
                    stage=sync_stage.value,
                    market_session_date=market_session_date,
                    valuation_policy_version=VALUATION_POLICY_VERSION,
                    duplicate_of=prior["run_id"],
                )
            else:
                sync_store.create_run(
                    run_id=run_id,
                    idempotency_key=idempotency_key,
                    iso_week=iso_week,
                    mode=mode,
                    dry_run=False,
                    requested_by=requested_by,
                    account_set_hash=account_hash,
                    started_at=started_at,
                    status="RUNNING",
                    duplicate_of=prior["run_id"],
                    stage=sync_stage.value,
                    market_session_date=market_session_date,
                    valuation_policy_version=VALUATION_POLICY_VERSION,
                )
            sync_store.copy_account_results(
                source_run_id=prior["run_id"], target_run_id=run_id
            )
            summary = {
                **(prior.get("summary") or {}),
                "message": "This mode and account set already completed for the ISO week.",
                "duplicate_of": prior["run_id"],
                "stage": sync_stage.value,
                "market_session_date": market_session_date,
            }
            sync_store.finish_run(
                run_id, status="SKIPPED_DUPLICATE", finished_at=time.time(), summary=summary
            )
            return sync_store.get_run(run_id) or {
                "run_id": run_id,
                "status": "SKIPPED_DUPLICATE",
            }

    if queued_run and queued_run.get("status") == "QUEUED":
        sync_store.start_queued_run(
            run_id,
            idempotency_key=idempotency_key,
            iso_week=iso_week,
            account_set_hash=account_hash,
            started_at=started_at,
            stage=sync_stage.value,
            market_session_date=market_session_date,
            valuation_policy_version=VALUATION_POLICY_VERSION,
        )
    else:
        sync_store.create_run(
            run_id=run_id,
            idempotency_key=idempotency_key,
            iso_week=iso_week,
            mode=mode,
            dry_run=dry_run,
            requested_by=requested_by,
            account_set_hash=account_hash,
            started_at=started_at,
            stage=sync_stage.value,
            market_session_date=market_session_date,
            valuation_policy_version=VALUATION_POLICY_VERSION,
        )
    cancellation = cancel_event or threading.Event()
    lock = JobLock(lock_path or DATA_DIR / "weekly-sync.lock")

    try:
        with lock:
            if not specs:
                raise WeeklySyncError("No enabled accounts are configured.")
            family = _run_step(
                run_id,
                sequence=1,
                name="fetch_family_portfolio",
                fn=(
                    (lambda: family_fetcher(mode))
                    if family_fetcher is not None
                    else (lambda: _fetch_family(mode, sync_stage))
                ),
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
                now=now_utc,
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
                account["status"] not in LIVE_MODE_ACCEPTED_STATUSES
                for account in accounts
            ):
                raise WeeklySyncError(
                    "Live mode requires every enabled account to be live; no snapshots were written."
                )

            degraded = [
                account
                for account in accounts
                if account["status"] not in LIVE_MODE_ACCEPTED_STATUSES
            ]
            run_status = "COMPLETED_WITH_WARNINGS" if degraded else "COMPLETED"
            weekly_meta = snapshot_metadata(
                run_id=run_id,
                stage=sync_stage.value,
                market_session_date=market_session_date,
                accounts=accounts,
                previous=_previous_history_snapshot(
                    cadence="weekly", current_period=iso_week
                ),
            )
            daily_meta = snapshot_metadata(
                run_id=run_id,
                stage=sync_stage.value,
                market_session_date=market_session_date,
                accounts=accounts,
                previous=_previous_history_snapshot(
                    cadence="daily", current_period=market_session_date
                ),
            )
            sync_store.update_run_quality(
                run_id,
                snapshot_quality=weekly_meta["snapshot_quality"],
                comparability={
                    "weekly": {
                        "comparable_to_previous": weekly_meta["comparable_to_previous"],
                        "reasons": weekly_meta["comparability_reasons"],
                    },
                    "daily": {
                        "comparable_to_previous": daily_meta["comparable_to_previous"],
                        "reasons": daily_meta["comparability_reasons"],
                    },
                },
            )
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
                            family,
                            source=f"weekly_sync:{mode}:{sync_stage.value}",
                            week_start=iso_week,
                            snapshot_metadata=weekly_meta,
                        ),
                        "daily": daily_writer(
                            family,
                            source=f"weekly_sync:{mode}:{sync_stage.value}",
                            day_date=market_session_date,
                            snapshot_metadata=daily_meta,
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
                        stage=sync_stage.value,
                        market_session_date=market_session_date,
                        snapshot_meta=weekly_meta,
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
                "stage": sync_stage.value,
                "market_session_date": market_session_date,
                "snapshot_quality": weekly_meta["snapshot_quality"],
                "coverage_pct": weekly_meta["coverage_pct"],
                "comparable_to_previous": weekly_meta["comparable_to_previous"],
                "comparability_reasons": weekly_meta["comparability_reasons"],
                "quote_refresh": family.get("quote_refresh") or {},
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
