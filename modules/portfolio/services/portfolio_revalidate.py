"""Background stale-while-revalidate for portfolio snapshots."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from modules.portfolio.db import portfolio_cache as disk_cache

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_RUNNING: set[str] = set()


def _family_cache_key(with_metrics: bool) -> str:
    return f"family:metrics={with_metrics}"


def schedule_family_revalidate(*, with_metrics: bool = True) -> bool:
    """Start background refresh if not already running. Returns True if scheduled."""
    key = _family_cache_key(with_metrics)
    with _LOCK:
        if key in _RUNNING:
            return False
        _RUNNING.add(key)

    disk_cache.set_revalidate_status(key, status="running", started_at=time.time())

    def _run() -> None:
        try:
            from modules.portfolio.services.portfolio import fetch_family_portfolio

            fetch_family_portfolio(with_metrics=with_metrics, refresh=True, stale_ok=False)
            disk_cache.set_revalidate_status(
                key, status="done", finished_at=time.time(), error=None
            )
        except Exception as exc:
            logger.exception("Portfolio revalidate failed")
            disk_cache.set_revalidate_status(
                key, status="error", finished_at=time.time(), error=str(exc)[:500]
            )
        finally:
            with _LOCK:
                _RUNNING.discard(key)

    threading.Thread(target=_run, name=f"portfolio-revalidate-{key}", daemon=True).start()
    return True


def meta_for_family(*, with_metrics: bool = True, fresh_ttl: float) -> dict[str, Any]:
    key = _family_cache_key(with_metrics)
    snap = disk_cache.get_snapshot(key)
    job = disk_cache.get_revalidate_status(key) or {}
    now = time.time()

    if not snap:
        return {
            "cache_key": key,
            "has_snapshot": False,
            "fresh": False,
            "stale": False,
            "revalidating": job.get("status") == "running",
            "cached_at": None,
            "age_seconds": None,
        }

    cached_at, _ = snap
    age = now - cached_at
    fresh = age < fresh_ttl
    revalidating = job.get("status") == "running" or key in _RUNNING

    return {
        "cache_key": key,
        "has_snapshot": True,
        "fresh": fresh and not revalidating,
        "stale": not fresh,
        "revalidating": revalidating,
        "cached_at": cached_at,
        "age_seconds": round(age),
        "last_job_status": job.get("status"),
        "last_job_error": job.get("error"),
    }
