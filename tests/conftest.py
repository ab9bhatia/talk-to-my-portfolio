"""Keep tests isolated from the developer's local portfolio databases."""

from __future__ import annotations

import os
import tempfile

import pytest


_TEMP_DATA = tempfile.TemporaryDirectory(prefix="talk-to-my-portfolio-tests-")
os.environ["PORTFOLIO_DATA_DIR"] = _TEMP_DATA.name


@pytest.fixture(scope="session", autouse=True)
def initialize_isolated_databases():
    """Initialize only local temporary SQLite stores; never mutate real portfolio data."""
    from modules.portfolio.db import (
        advisory_evidence,
        buy_thesis_cache,
        custom_holdings,
        daily_history,
        groww_tokens,
        import_audit,
        portfolio_cache,
        profile_goals,
        sector_llm_cache,
        tokens,
        weekly_history,
        weekly_sync,
    )

    for store in (
        advisory_evidence,
        tokens,
        groww_tokens,
        portfolio_cache,
        daily_history,
        weekly_history,
        weekly_sync,
        sector_llm_cache,
        buy_thesis_cache,
        custom_holdings,
        import_audit,
        profile_goals,
    ):
        store.init_db()
    yield
