from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from modules.portfolio.db import weekly_history
from modules.portfolio.services.weekly_recorder import record_family_from_payload
from modules.portfolio.services.weekly_sync import (
    _advisory_summary,
    _recommendation_changes,
    classify_accounts,
    run_weekly_sync,
)


SPECS = [
    {"account_id": "india_one", "account_code": "I1", "broker": "zerodha"},
    {"account_id": "global_one", "account_code": "G1", "broker": "sarwa"},
]


def _holding(symbol: str, *, quantity: float = 1, price: float = 100) -> dict:
    invested = quantity * price * 0.8
    current = quantity * price
    return {
        "symbol": symbol,
        "exchange": "NSE",
        "asset_class": "equity",
        "currency": "INR",
        "quantity": quantity,
        "avg_price": price * 0.8,
        "last_price": price,
        "invested": invested,
        "current_value": current,
        "pnl": current - invested,
        "pnl_pct": 25,
    }


def _family(
    *,
    degraded: bool = True,
    secret: bool = False,
    manual_cached_at: object = 1_900_000_000,
) -> dict:
    errors = []
    if degraded:
        message = "session expired"
        if secret:
            message += " api_key=do-not-store-this"
        errors.append(
            {
                "account": "G1",
                "broker": "sarwa",
                "error": message,
                "using_snapshot": True,
            }
        )
    portfolios = [
        {
            "account_id": "india_one",
            "account_code": "I1",
            "broker": "zerodha",
            "holdings": [_holding("ALPHA", quantity=2)],
        },
        {
            "account_id": "global_one",
            "account_code": "G1",
            "broker": "sarwa",
            "cached_at": manual_cached_at,
            "stale": degraded,
            "from_cache": degraded,
            "holdings": [_holding("BETA", quantity=3)],
        },
    ]
    return {
        "accounts_requested": 2,
        "accounts_loaded": 2,
        "portfolios": portfolios,
        "errors": errors,
        "ltp_refreshed_offline": degraded,
        "summary": {
            "holdings_count": 2,
            "total_invested": 400,
            "total_current_value": 500,
            "total_pnl": 100,
            "total_pnl_pct": 25,
        },
    }


def _advisory(_family_payload: dict, generated_at: str) -> dict:
    return {
        "generated_at": generated_at,
        "action_counts": {"WATCH": 1, "REDUCE": 1},
        "no_action_count": 1,
        "suggested_actions": [
            {"symbol": "ALPHA", "action": "REDUCE", "confidence": 72}
        ],
        "by_symbol": {"ALPHA": "REDUCE", "BETA": "WATCH"},
        "evidence_status": {"recommendations": 2},
    }


def _writers(calls: list[str]):
    def weekly(_family_payload, **_kwargs):
        calls.append("weekly")
        return [{"scope": "family"}]

    def daily(_family_payload, **_kwargs):
        calls.append("daily")
        return [{"scope": "family"}]

    return weekly, daily


def test_friday_and_saturday_stages_each_run_once_and_are_idempotent(tmp_path: Path):
    calls: list[str] = []
    weekly, daily = _writers(calls)
    friday = datetime(2035, 1, 5, 18, 45, tzinfo=ZoneInfo("Asia/Kolkata"))
    result = run_weekly_sync(
        mode="auto",
        now=friday,
        account_specs=SPECS,
        family_fetcher=lambda _mode: _family(degraded=True, secret=True),
        weekly_writer=weekly,
        daily_writer=daily,
        advisory_builder=_advisory,
        digest_dir=tmp_path / "digests",
        lock_path=tmp_path / "weekly.lock",
        sleeper=lambda _seconds: None,
    )

    assert result["status"] == "COMPLETED_WITH_WARNINGS"
    assert calls == ["weekly", "daily"]
    states = {item["account_code"]: item["status"] for item in result["accounts"]}
    assert states == {
        "G1": "MANUAL_STALE",
        "I1": "LIVE_RECONCILED",
    }
    digest_path = next(
        Path(item["path"])
        for item in result["artifacts"]
        if item["kind"] == "digest_markdown"
    )
    assert digest_path.exists()
    assert "No-action holdings: 1" in digest_path.read_text(encoding="utf-8")
    assert "do-not-store-this" not in json.dumps(result)

    saturday = datetime(2035, 1, 6, 9, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    finalization = run_weekly_sync(
        mode="auto",
        now=saturday,
        account_specs=SPECS,
        family_fetcher=lambda _mode: _family(degraded=True),
        weekly_writer=weekly,
        daily_writer=daily,
        advisory_builder=_advisory,
        digest_dir=tmp_path / "digests",
        lock_path=tmp_path / "weekly.lock",
    )
    assert finalization["status"] == "COMPLETED_WITH_WARNINGS"
    assert finalization["stage"] == "GLOBAL_CLOSE_FINALIZATION"
    assert finalization["market_session_date"] == "2035-01-05"
    assert calls == ["weekly", "daily", "weekly", "daily"]

    duplicate = run_weekly_sync(
        mode="auto",
        now=saturday,
        account_specs=SPECS,
        family_fetcher=lambda _mode: (_ for _ in ()).throw(AssertionError("must skip")),
        weekly_writer=weekly,
        daily_writer=daily,
        advisory_builder=_advisory,
        digest_dir=tmp_path / "digests",
        lock_path=tmp_path / "weekly.lock",
    )
    assert duplicate["status"] == "SKIPPED_DUPLICATE"
    assert duplicate["duplicate_of"] == finalization["run_id"]
    assert {item["account_code"] for item in duplicate["accounts"]} == {"I1", "G1"}
    assert calls == ["weekly", "daily", "weekly", "daily"]

    forced = run_weekly_sync(
        mode="auto",
        force=True,
        now=saturday,
        account_specs=SPECS,
        family_fetcher=lambda _mode: _family(degraded=True),
        weekly_writer=weekly,
        daily_writer=daily,
        advisory_builder=_advisory,
        digest_dir=tmp_path / "digests",
        lock_path=tmp_path / "weekly.lock",
        sleeper=lambda _seconds: None,
    )
    assert forced["status"] == "COMPLETED_WITH_WARNINGS"
    assert forced["run_id"] != result["run_id"]
    assert calls == ["weekly", "daily", "weekly", "daily", "weekly", "daily"]


def test_dry_run_does_not_write_snapshots_or_digest(tmp_path: Path):
    calls: list[str] = []
    weekly, daily = _writers(calls)
    result = run_weekly_sync(
        mode="auto",
        dry_run=True,
        now=datetime(2035, 2, 2, 18, 45, tzinfo=ZoneInfo("Asia/Kolkata")),
        account_specs=SPECS,
        family_fetcher=lambda _mode: _family(degraded=False),
        weekly_writer=weekly,
        daily_writer=daily,
        advisory_builder=_advisory,
        digest_dir=tmp_path / "digests",
        lock_path=tmp_path / "weekly.lock",
        sleeper=lambda _seconds: None,
    )
    assert result["status"] == "COMPLETED_WITH_WARNINGS"
    assert result["dry_run"] is True
    assert calls == []
    assert not (tmp_path / "digests").exists()
    step_states = {step["step_name"]: step["status"] for step in result["steps"]}
    assert step_states["persist_snapshots"] == "DRY_RUN"
    assert step_states["generate_digest"] == "DRY_RUN"


def test_live_mode_rejects_cached_account_before_snapshot_write(tmp_path: Path):
    calls: list[str] = []
    weekly, daily = _writers(calls)
    result = run_weekly_sync(
        mode="live",
        now=datetime(2035, 3, 2, 18, 45, tzinfo=ZoneInfo("Asia/Kolkata")),
        account_specs=SPECS,
        family_fetcher=lambda _mode: _family(degraded=True),
        weekly_writer=weekly,
        daily_writer=daily,
        advisory_builder=_advisory,
        digest_dir=tmp_path / "digests",
        lock_path=tmp_path / "weekly.lock",
        fetch_retries=0,
    )
    assert result["status"] == "FAILED"
    assert "requires every enabled account to be live" in result["error"]
    assert calls == []


def test_all_accounts_failed_writes_no_snapshot(tmp_path: Path):
    calls: list[str] = []
    weekly, daily = _writers(calls)
    empty = {
        "portfolios": [],
        "errors": [
            {"account": "I1", "broker": "zerodha", "error": "token expired"},
            {"account": "G1", "broker": "sarwa", "error": "no import"},
        ],
        "summary": {},
    }
    result = run_weekly_sync(
        mode="auto",
        now=datetime(2035, 4, 6, 18, 45, tzinfo=ZoneInfo("Asia/Kolkata")),
        account_specs=SPECS,
        family_fetcher=lambda _mode: empty,
        weekly_writer=weekly,
        daily_writer=daily,
        advisory_builder=_advisory,
        digest_dir=tmp_path / "digests",
        lock_path=tmp_path / "weekly.lock",
        fetch_retries=0,
    )
    assert result["status"] == "FAILED"
    assert "No trusted holdings" in result["error"]
    assert calls == []
    assert {item["status"] for item in result["accounts"]} == {
        "AUTH_REQUIRED",
        "IMPORT_REQUIRED",
    }


def test_lock_and_retry_are_audited(tmp_path: Path):
    lock_path = tmp_path / "weekly.lock"
    lock_path.write_text("another-process", encoding="utf-8")
    locked = run_weekly_sync(
        mode="auto",
        dry_run=True,
        now=datetime(2035, 5, 4, 18, 45, tzinfo=ZoneInfo("Asia/Kolkata")),
        account_specs=SPECS,
        family_fetcher=lambda _mode: _family(),
        advisory_builder=_advisory,
        lock_path=lock_path,
    )
    assert locked["status"] == "LOCKED"

    lock_path.unlink()
    attempts = 0

    def transient(_mode: str):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise RuntimeError("temporary provider failure")
        return _family(degraded=False)

    retried = run_weekly_sync(
        mode="auto",
        dry_run=True,
        now=datetime(2035, 5, 11, 18, 45, tzinfo=ZoneInfo("Asia/Kolkata")),
        account_specs=SPECS,
        family_fetcher=transient,
        advisory_builder=_advisory,
        lock_path=lock_path,
        sleeper=lambda _seconds: None,
    )
    assert retried["status"] == "COMPLETED_WITH_WARNINGS"
    fetch_step = next(step for step in retried["steps"] if step["step_name"] == "fetch_family_portfolio")
    assert fetch_step["attempts"] == 3


def test_family_weekly_snapshot_aggregates_duplicate_symbols_and_upserts():
    week = "2036-01-07"
    family = {
        "portfolios": [
            {
                "account_id": "synthetic_a",
                "broker": "zerodha",
                "holdings": [_holding("DUPLICATE", quantity=1, price=100)],
            },
            {
                "account_id": "synthetic_b",
                "broker": "groww",
                "holdings": [_holding("DUPLICATE", quantity=2, price=110)],
            },
            {
                "account_id": "synthetic_c",
                "broker": "custom",
                "holdings": [_holding("DUPLICATE", quantity=3, price=105)],
            },
            {
                "account_id": "synthetic_d",
                "broker": "sarwa",
                "holdings": [_holding("DUPLICATE", quantity=4, price=115)],
            },
        ]
    }
    record_family_from_payload(family, source="test", week_start=week)
    record_family_from_payload(family, source="test-rerun", week_start=week)

    rows = [
        row
        for row in weekly_history.list_snapshots(scope="family", account_id=None, limit=104)
        if row["week_start"] == week
    ]
    assert len(rows) == 1
    detail = weekly_history.get_snapshot(rows[0]["id"])
    assert detail is not None
    assert len(detail["positions"]) == 1
    assert detail["positions"][0]["quantity"] == 10


def test_account_classification_exposes_separate_position_and_price_dates():
    accounts = classify_accounts(
        _family(degraded=True, manual_cached_at="2035-01-04T12:00:00Z"),
        mode="auto",
        account_specs=SPECS,
        price_as_of="2035-01-05T13:15:00Z",
        now=datetime(2035, 1, 5, 13, 15, tzinfo=ZoneInfo("UTC")),
    )
    cached = next(item for item in accounts if item["account_code"] == "G1")
    assert cached["status"] == "MANUAL_CURRENT"
    assert cached["position_as_of"] != cached["price_as_of"]
    assert cached["price_as_of"] == "2035-01-05T13:15:00Z"


def test_old_manual_import_is_stale_and_live_mode_accepts_only_current(tmp_path: Path):
    stale_accounts = classify_accounts(
        _family(degraded=True, manual_cached_at="2034-12-01T12:00:00Z"),
        mode="live",
        account_specs=SPECS,
        price_as_of="2035-01-05T13:15:00Z",
        now=datetime(2035, 1, 5, 13, 15, tzinfo=ZoneInfo("UTC")),
    )
    assert next(row for row in stale_accounts if row["account_code"] == "G1")["status"] == "MANUAL_STALE"

    calls: list[str] = []
    weekly, daily = _writers(calls)
    result = run_weekly_sync(
        mode="live",
        now=datetime(2035, 1, 5, 18, 45, tzinfo=ZoneInfo("Asia/Kolkata")),
        account_specs=SPECS,
        family_fetcher=lambda _mode: _family(
            degraded=False, manual_cached_at="2035-01-05T10:00:00Z"
        ),
        weekly_writer=weekly,
        daily_writer=daily,
        advisory_builder=_advisory,
        digest_dir=tmp_path / "digests",
        lock_path=tmp_path / "weekly.lock",
    )
    assert result["status"] == "COMPLETED"
    assert calls == ["weekly", "daily"]


def test_timed_out_worker_exits_before_retry_can_start(tmp_path: Path):
    lock = threading.Lock()
    active = 0
    max_active = 0
    attempts = 0

    def slow_then_fast(_mode: str):
        nonlocal active, max_active, attempts
        with lock:
            attempts += 1
            active += 1
            max_active = max(max_active, active)
        if attempts == 1:
            time.sleep(0.04)
        with lock:
            active -= 1
        return _family(degraded=False)

    result = run_weekly_sync(
        mode="auto",
        dry_run=True,
        now=datetime(2035, 6, 1, 18, 45, tzinfo=ZoneInfo("Asia/Kolkata")),
        account_specs=SPECS,
        family_fetcher=slow_then_fast,
        advisory_builder=_advisory,
        lock_path=tmp_path / "weekly.lock",
        step_timeout_seconds=0.01,
        fetch_retries=1,
        sleeper=lambda _seconds: None,
    )
    assert result["status"] in {"COMPLETED", "COMPLETED_WITH_WARNINGS"}
    assert attempts == 2
    assert max_active == 1


def test_recommendation_diff_detects_material_change_without_action_change():
    previous = {
        "by_security": {
            "ALPHA": {"symbol": "ALPHA", "action": "REDUCE", "sell_pct": 10}
        }
    }
    current = {
        "by_security": {
            "ALPHA": {"symbol": "ALPHA", "action": "REDUCE", "sell_pct": 25}
        }
    }
    changes = _recommendation_changes(current, previous)
    assert changes[0]["changed_fields"] == ["sell_pct"]


def test_blocked_and_ca_review_actions_are_not_execution_ready(monkeypatch):
    from modules.portfolio.services.advisory import service as advisory_service

    monkeypatch.setattr(
        advisory_service,
        "build_advisory_payload",
        lambda *_args, **_kwargs: {
            "schema_version": "test",
            "generated_at": "2035-01-05T13:15:00Z",
            "evidence_status": {},
            "recommendations": [
                {
                    "symbol": "BLOCKED",
                    "action": "SELL",
                    "sell_type": "FUNDAMENTAL_SELL",
                    "action_confidence": 90,
                    "sell_pct": 100,
                    "target_weight_pct": 0,
                    "expected_3y_irr": {},
                    "evidence_state": "DOCUMENTED_MODEL",
                    "requires_ca_review": False,
                    "data_quality_flags": [
                        {"code": "UNRESOLVED_IDENTITY", "blocking": True}
                    ],
                },
                {
                    "symbol": "CAONLY",
                    "action": "REDUCE",
                    "sell_type": "TACTICAL_REDUCE",
                    "action_confidence": 80,
                    "sell_pct": 20,
                    "target_weight_pct": 5,
                    "expected_3y_irr": {},
                    "evidence_state": "DOCUMENTED_MODEL",
                    "requires_ca_review": True,
                    "data_quality_flags": [],
                },
                {
                    "symbol": "STALEEVIDENCE",
                    "action": "ADD",
                    "sell_type": "NONE",
                    "action_confidence": 75,
                    "sell_pct": 0,
                    "target_weight_pct": 4,
                    "expected_3y_irr": {},
                    "evidence_state": "SCREENING_MODEL",
                    "requires_ca_review": False,
                    "data_quality_flags": [
                        {"code": "STALE_PORTFOLIO_SNAPSHOT", "blocking": False}
                    ],
                },
            ],
        },
    )
    summary = _advisory_summary({}, "2035-01-05T13:15:00Z")
    assert summary["execution_ready_actions"] == []
    assert {row["symbol"] for row in summary["urgent_data_risk_issues"]} == {"BLOCKED"}
    assert {row["symbol"] for row in summary["tax_ca_review_actions"]} == {"CAONLY"}
    assert "STALEEVIDENCE" in {
        row["symbol"] for row in summary["research_watch_actions"]
    }
