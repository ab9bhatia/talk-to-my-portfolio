"""Regression coverage for non-blocking Setup and Zerodha synchronization."""

from __future__ import annotations

import threading
import time

from fastapi.testclient import TestClient

from main import app
from modules.portfolio import router as portfolio_router
from modules.portfolio.services import sync_jobs
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
