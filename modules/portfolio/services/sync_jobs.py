"""Process-local background runner for Setup-triggered portfolio syncs."""

from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from typing import Any, Callable
from zoneinfo import ZoneInfo


_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="portfolio-sync")
_LOCK = threading.Lock()
_JOBS: dict[str, dict[str, Any]] = {}
_MAX_RETAINED_JOBS = 32


def _default_runner(**kwargs: Any) -> dict[str, Any]:
    from modules.portfolio.services.weekly_sync import run_weekly_sync

    return run_weekly_sync(**kwargs)


def _stage_value(stage: str | None) -> str:
    from modules.portfolio.services.weekly_sync import SyncStage, infer_sync_stage

    if stage:
        return SyncStage(stage).value
    return infer_sync_stage(datetime.now(tz=ZoneInfo("Asia/Kolkata"))).value


def _trim_finished_jobs() -> None:
    finished = [
        (job_id, float(job["submitted_at"]))
        for job_id, job in _JOBS.items()
        if job["future"].done()
    ]
    overflow = len(_JOBS) - _MAX_RETAINED_JOBS
    for job_id, _submitted_at in sorted(finished, key=lambda item: item[1])[: max(0, overflow)]:
        _JOBS.pop(job_id, None)


def submit_weekly_sync(
    *,
    mode: str = "auto",
    dry_run: bool = False,
    requested_by: str = "setup_ui",
    force: bool = False,
    stage: str | None = None,
    parent_run_id: str | None = None,
    runner: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Queue one sync and immediately return its stable run id.

    Only one process-local sync may be queued or running. Repeated clicks return
    the existing job instead of building an unbounded queue behind the broker.
    """
    with _LOCK:
        from modules.portfolio.db import weekly_sync as sync_store

        sync_store.init_db()
        _trim_finished_jobs()
        for job_id, job in _JOBS.items():
            future: Future[dict[str, Any]] = job["future"]
            if not future.done():
                if force:
                    sync_store.mark_rerun_required(job_id, reason=requested_by)
                return {
                    "run_id": job_id,
                    "status": "RUNNING" if future.running() else "QUEUED",
                    "accepted": False,
                    "rerun_required": bool(force),
                    "rerun_reason": requested_by if force else None,
                    "message": (
                        "A portfolio sync is already in progress; one forced rerun was coalesced."
                        if force
                        else "A portfolio sync is already in progress."
                    ),
                }

        durable_active = sync_store.find_active_run()
        if durable_active is not None:
            active_id = str(durable_active["run_id"])
            if force:
                sync_store.mark_rerun_required(active_id, reason=requested_by)
            return {
                "run_id": active_id,
                "status": str(durable_active.get("status") or "QUEUED"),
                "accepted": False,
                "rerun_required": bool(force),
                "rerun_reason": requested_by if force else None,
                "message": (
                    "A durable portfolio sync is active; one forced rerun was coalesced."
                    if force
                    else "A durable portfolio sync is already queued or running."
                ),
            }

        run_id = uuid.uuid4().hex
        submitted_at = time.time()
        resolved_stage = _stage_value(stage)
        sync_runner = runner or _default_runner
        sync_store.create_queued_run(
            run_id=run_id,
            mode=mode,
            dry_run=dry_run,
            requested_by=requested_by,
            queued_at=submitted_at,
            stage=resolved_stage,
            parent_run_id=parent_run_id,
        )

        def execute() -> dict[str, Any]:
            try:
                result = sync_runner(
                    run_id=run_id,
                    mode=mode,
                    dry_run=dry_run,
                    requested_by=requested_by,
                    force=force,
                    stage=resolved_stage,
                )
            except Exception as exc:
                from modules.portfolio.services.weekly_sync import sanitize_message

                current = sync_store.get_run(run_id)
                if current and current.get("status") == "QUEUED":
                    sync_store.finish_run(
                        run_id,
                        status="FAILED",
                        finished_at=time.time(),
                        error=sanitize_message(exc),
                    )
                raise
            current = sync_store.get_run(run_id)
            if current and current.get("status") == "QUEUED":
                sync_store.finish_run(
                    run_id,
                    status=str(result.get("status") or "COMPLETED"),
                    finished_at=time.time(),
                    summary=result,
                )
            return result

        future = _EXECUTOR.submit(execute)
        _JOBS[run_id] = {
            "future": future,
            "submitted_at": submitted_at,
            "mode": mode,
            "dry_run": dry_run,
            "force": force,
            "requested_by": requested_by,
            "stage": resolved_stage,
        }

        def enqueue_coalesced_followup(_future: Future[dict[str, Any]]) -> None:
            reason = sync_store.claim_rerun(run_id)
            if not reason:
                return
            followup = submit_weekly_sync(
                mode=mode,
                dry_run=False,
                requested_by=reason,
                force=True,
                stage="MANUAL_RERUN",
                parent_run_id=run_id,
            )
            if followup.get("run_id"):
                sync_store.set_followup_run(run_id, str(followup["run_id"]))

    # Register outside the lock: an already-finished Future invokes callbacks
    # synchronously, and the callback may submit the coalesced follow-up.
    future.add_done_callback(enqueue_coalesced_followup)
    return {
        "run_id": run_id,
        "status": "QUEUED",
        "accepted": True,
        "submitted_at": submitted_at,
        "stage": resolved_stage,
    }


def get_sync_job(run_id: str) -> dict[str, Any] | None:
    """Return durable run detail when available, otherwise process queue state."""
    from modules.portfolio.db import weekly_sync as sync_store

    sync_store.init_db()
    run = sync_store.get_run(run_id)
    if run is not None:
        return run

    with _LOCK:
        job = _JOBS.get(run_id)
        if job is None:
            return None
        future: Future[dict[str, Any]] = job["future"]
        fallback = {
            "run_id": run_id,
            "status": "RUNNING" if future.running() else "QUEUED",
            "dry_run": bool(job["dry_run"]),
            "mode": job["mode"],
            "started_at": job["submitted_at"],
            "steps": [],
            "accounts": [],
        }
        if not future.done():
            return fallback
        try:
            return future.result()
        except Exception as exc:  # Defensive: the normal runner records failures itself.
            from modules.portfolio.services.weekly_sync import sanitize_message

            return {
                **fallback,
                "status": "FAILED",
                "finished_at": time.time(),
                "error": sanitize_message(exc),
            }
