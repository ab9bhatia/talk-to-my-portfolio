"""Regression coverage for non-blocking Setup and Zerodha synchronization."""

from __future__ import annotations

import threading
import time

from fastapi.testclient import TestClient

from main import app
from modules.portfolio import router as portfolio_router
from modules.portfolio.services import sync_jobs
from modules.portfolio.db import weekly_sync as sync_store
from shared.config import APP_ROOT_PATH


API = f"{APP_ROOT_PATH}/api/portfolio"


def test_background_runner_returns_immediately_and_deduplicates_active_job():
    started = threading.Event()
    release = threading.Event()

    def slow_runner(**kwargs):
        started.set()
        assert release.wait(timeout=2)
        return {"run_id": kwargs["run_id"], "status": "COMPLETED"}

    before = time.monotonic()
    first = sync_jobs.submit_weekly_sync(runner=slow_runner)
    elapsed = time.monotonic() - before
    assert elapsed < 0.5
    assert first["accepted"] is True
    assert first["status"] == "QUEUED"
    assert started.wait(timeout=1)

    duplicate = sync_jobs.submit_weekly_sync(runner=slow_runner)
    assert duplicate["accepted"] is False
    assert duplicate["run_id"] == first["run_id"]

    release.set()
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        status = sync_jobs.get_sync_job(first["run_id"])
        if status and status["status"] == "COMPLETED":
            break
        time.sleep(0.01)
    assert status and status["status"] == "COMPLETED"


def test_accepted_job_is_readable_from_sqlite_after_registry_loss():
    started = threading.Event()
    release = threading.Event()

    def runner(**kwargs):
        started.set()
        assert release.wait(timeout=2)
        return {"run_id": kwargs["run_id"], "status": "COMPLETED"}

    job = sync_jobs.submit_weekly_sync(runner=runner)
    assert started.wait(timeout=1)
    with sync_jobs._LOCK:
        registry_row = sync_jobs._JOBS.pop(job["run_id"])
    try:
        durable = sync_jobs.get_sync_job(job["run_id"])
        assert durable is not None
        assert durable["status"] == "QUEUED"
        assert durable["durable_queue_status"] == "QUEUED"
        duplicate = sync_jobs.submit_weekly_sync(runner=runner)
        assert duplicate["accepted"] is False
        assert duplicate["run_id"] == job["run_id"]
    finally:
        with sync_jobs._LOCK:
            sync_jobs._JOBS[job["run_id"]] = registry_row
        release.set()

    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        completed = sync_store.get_run(job["run_id"])
        if completed and completed["status"] == "COMPLETED":
            break
        time.sleep(0.01)
    assert completed and completed["status"] == "COMPLETED"


def test_startup_recovery_marks_orphaned_queue_interrupted():
    run_id = "orphaned" + "0" * 24
    sync_store.create_queued_run(
        run_id=run_id,
        mode="auto",
        dry_run=False,
        requested_by="test",
        queued_at=time.time() - 60,
        stage="MANUAL_RERUN",
    )
    assert sync_store.recover_orphaned_runs(recovered_at=time.time()) >= 1
    recovered = sync_store.get_run(run_id)
    assert recovered is not None
    assert recovered["status"] == "INTERRUPTED"


def test_force_during_active_job_coalesces_exactly_one_followup(monkeypatch):
    started = threading.Event()
    release = threading.Event()

    def active_runner(**kwargs):
        started.set()
        assert release.wait(timeout=2)
        return {"run_id": kwargs["run_id"], "status": "COMPLETED"}

    def followup_runner(**kwargs):
        return {"run_id": kwargs["run_id"], "status": "COMPLETED"}

    monkeypatch.setattr(sync_jobs, "_default_runner", followup_runner)
    active = sync_jobs.submit_weekly_sync(runner=active_runner)
    assert started.wait(timeout=1)
    first = sync_jobs.submit_weekly_sync(force=True, requested_by="zerodha_oauth")
    second = sync_jobs.submit_weekly_sync(force=True, requested_by="zerodha_oauth")
    assert first["run_id"] == active["run_id"] == second["run_id"]
    assert first["rerun_required"] is True
    release.set()

    deadline = time.monotonic() + 3
    parent = None
    while time.monotonic() < deadline:
        parent = sync_store.get_run(active["run_id"])
        if parent and parent.get("followup_run_id"):
            break
        time.sleep(0.01)
    assert parent and parent.get("followup_run_id")
    children = [
        row
        for row in sync_store.list_runs(limit=100)
        if row.get("parent_run_id") == active["run_id"]
    ]
    assert len(children) == 1


def test_async_sync_api_returns_accepted_run_and_status_url(monkeypatch):
    monkeypatch.setattr(
        sync_jobs,
        "submit_weekly_sync",
        lambda **_kwargs: {
            "run_id": "a" * 32,
            "status": "QUEUED",
            "accepted": True,
        },
    )

    response = TestClient(app).post(
        f"{API}/sync/weekly/async",
        json={"mode": "auto", "dry_run": False},
    )

    assert response.status_code == 202
    assert response.json() == {
        "run_id": "a" * 32,
        "status": "QUEUED",
        "accepted": True,
        "status_url": f"{APP_ROOT_PATH}/api/portfolio/sync/jobs/{'a' * 32}",
    }


def test_sync_job_api_returns_404_for_unknown_job(monkeypatch):
    monkeypatch.setattr(sync_jobs, "get_sync_job", lambda _run_id: None)

    response = TestClient(app).get(f"{API}/sync/jobs/missing")

    assert response.status_code == 404
    assert response.json()["detail"] == "Portfolio sync job not found"


def test_zerodha_callback_redirects_immediately_and_preserves_snapshot(monkeypatch):
    invalidations = []
    submissions = []
    monkeypatch.setattr(portfolio_router, "resolve_account_ref", lambda ref: "primary")
    monkeypatch.setattr(portfolio_router, "complete_oauth", lambda **_kwargs: None)
    monkeypatch.setattr(
        portfolio_router,
        "invalidate_portfolio_cache",
        lambda **kwargs: invalidations.append(kwargs),
    )
    monkeypatch.setattr(portfolio_router, "get_account_code", lambda _account_id: "AB")
    monkeypatch.setattr(portfolio_router, "get_hub_url", lambda path: path)
    monkeypatch.setattr(
        sync_jobs,
        "submit_weekly_sync",
        lambda **kwargs: submissions.append(kwargs)
        or {"run_id": "b" * 32, "status": "QUEUED", "accepted": True},
    )

    response = TestClient(app).get(
        f"{APP_ROOT_PATH}/auth/zerodha/callback?request_token=test&code=AB",
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["location"].startswith("/portfolio/setup?")
    assert "account=AB" in response.headers["location"]
    assert f"sync_run_id={'b' * 32}" in response.headers["location"]
    assert invalidations == [{"preserve_disk": True}]
    assert submissions == [
        {
            "mode": "auto",
            "dry_run": False,
            "requested_by": "zerodha_oauth",
            "force": True,
        }
    ]
