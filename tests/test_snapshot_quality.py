from modules.portfolio.services.snapshot_quality import snapshot_metadata


def test_current_stale_snapshot_is_not_comparable_even_with_same_coverage():
    metadata = snapshot_metadata(
        run_id="run-2",
        stage="MANUAL_RERUN",
        market_session_date="2026-08-28",
        accounts=[
            {
                "status": "MANUAL_STALE",
                "position_as_of": "2026-08-20T10:00:00+00:00",
                "price_as_of": "2026-08-28T10:00:00+00:00",
            }
        ],
        previous={
            "snapshot_quality": "COMPLETE_LIVE",
            "accounts_expected": 1,
            "accounts_included": 1,
        },
    )

    assert metadata["snapshot_quality"] == "STALE"
    assert metadata["comparable_to_previous"] is False
    assert metadata["comparability_reasons"] == ["CURRENT_SNAPSHOT_STALE"]


def test_legacy_upserts_preserve_existing_quality_metadata():
    from modules.portfolio.db import daily_history, weekly_history

    position = {
        "symbol": "QUALITY",
        "exchange": "NSE",
        "quantity": 1,
        "invested": 100,
        "current_value": 110,
        "pnl": 10,
    }
    metadata = {
        "sync_run_id": "quality-run",
        "sync_stage": "INDIA_CLOSE",
        "snapshot_quality": "COMPLETE_LIVE",
        "accounts_expected": 1,
        "accounts_included": 1,
        "coverage_pct": 100.0,
        "market_session_date": "2040-01-06",
        "comparable_to_previous": True,
    }

    daily_history.save_snapshot(
        scope="account",
        account_id="quality-test",
        positions=[position],
        source="sync",
        day_date="2040-01-06",
        metadata=metadata,
    )
    daily_history.save_snapshot(
        scope="account",
        account_id="quality-test",
        positions=[position],
        source="legacy-upsert",
        day_date="2040-01-06",
    )
    daily = daily_history.list_snapshots(
        scope="account", account_id="quality-test", limit=1
    )[0]

    weekly_history.save_snapshot(
        scope="account",
        account_id="quality-test",
        positions=[position],
        source="sync",
        week_start="2040-01-02",
        metadata=metadata,
    )
    weekly_history.save_snapshot(
        scope="account",
        account_id="quality-test",
        positions=[position],
        source="legacy-upsert",
        week_start="2040-01-02",
    )
    weekly = weekly_history.list_snapshots(
        scope="account", account_id="quality-test", limit=1
    )[0]

    for row in (daily, weekly):
        assert row["sync_run_id"] == "quality-run"
        assert row["snapshot_quality"] == "COMPLETE_LIVE"
        assert row["coverage_pct"] == 100.0
        assert row["comparable_to_previous"] is True
