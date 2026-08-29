"""Process-local background runner for Setup-triggered portfolio syncs."""

from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable


_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="portfolio-sync")
_LOCK = threading.Lock()
_JOBS: dict[str, dict[str, Any]] = {}
_MAX_RETAINED_JOBS = 32


def _default_runner(**kwargs: Any) -> dict[str, Any]:
    from modules.portfolio.services.weekly_sync import run_weekly_sync

    return run_weekly_sync(**kwargs)


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
    runner: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Queue one sync and immediately return its stable run id.

    Only one process-local sync may be queued or running. Repeated clicks return
    the existing job instead of building an unbounded queue behind the broker.
    """
    with _LOCK:
        _trim_finished_jobs()
        for job_id, job in _JOBS.items():
            future: Future[dict[str, Any]] = job["future"]
            if not future.done():
                return {
                    "run_id": job_id,
                    "status": "RUNNING" if future.running() else "QUEUED",
                    "accepted": False,
                    "message": "A portfolio sync is already in progress.",
                }

        run_id = uuid.uuid4().hex
        submitted_at = time.time()
        sync_runner = runner or _default_runner
        future = _EXECUTOR.submit(
            sync_runner,
            run_id=run_id,
            mode=mode,
            dry_run=dry_run,
            requested_by=requested_by,
            force=force,
        )
        _JOBS[run_id] = {
            "future": future,
            "submitted_at": submitted_at,
            "mode": mode,
            "dry_run": dry_run,
            "force": force,
        }
    return {
        "run_id": run_id,
        "status": "QUEUED",
        "accepted": True,
        "submitted_at": submitted_at,
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
