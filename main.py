"""Portfolio — FastAPI application (standalone)."""

from __future__ import annotations

import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from modules.portfolio.db import portfolio_cache as portfolio_cache_store
from modules.portfolio.db import tokens as token_store
from modules.portfolio.router import router as portfolio_router
from shared.config import APP_BASE_URL, APP_HOST, APP_NAME, APP_PORT, APP_ROOT_PATH, APP_TAGLINE
from shared.web.app_urls import app_path, portfolio_display_url
from shared.web.http_auth import add_http_basic_auth, http_auth_enabled

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "shared" / "web" / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize portfolio databases on startup."""
    if http_auth_enabled():
        logger.info("HTTP Basic Auth enabled (PORTFOLIO_HTTP_USER is set)")
    token_store.init_db()
    from modules.portfolio.db import groww_tokens as groww_token_store

    groww_token_store.init_db()
    portfolio_cache_store.init_db()
    from modules.portfolio.db import daily_history as daily_history_store
    from modules.portfolio.db import weekly_history as weekly_history_store

    weekly_history_store.init_db()
    daily_history_store.init_db()
    from modules.portfolio.db import sector_llm_cache as sector_llm_cache_store

    sector_llm_cache_store.init_db()
    from modules.portfolio.db import buy_thesis_cache as buy_thesis_cache_store

    buy_thesis_cache_store.init_db()
    from modules.portfolio.db import custom_holdings as custom_holdings_store

    custom_holdings_store.init_db()
    from modules.portfolio.db import import_audit as import_audit_store
    from modules.portfolio.db import profile_goals as profile_goals_store

    import_audit_store.init_db()
    profile_goals_store.init_db()
    from modules.portfolio.db import advisory_evidence as advisory_evidence_store
    from modules.portfolio.db import weekly_sync as weekly_sync_store

    advisory_evidence_store.init_db()
    weekly_sync_store.init_db()
    from modules.portfolio.db import instrument_master
    from modules.portfolio.db import transaction_ledger
    from modules.portfolio.db import market_regime
    from modules.portfolio.db import research
    from modules.portfolio.db import fund_intelligence
    from modules.portfolio.db import operating_console

    instrument_master.init_db()
    transaction_ledger.init_db()
    market_regime.init_db()
    research.init_db()
    fund_intelligence.init_db()
    operating_console.init_db()
    from modules.portfolio.db.schema_migrations import ensure_all_databases

    ensure_all_databases()
    recovered = weekly_sync_store.recover_orphaned_runs(recovered_at=time.time())
    if recovered:
        logger.warning("Marked %s orphaned portfolio sync job(s) INTERRUPTED", recovered)
    from modules.portfolio.services.market_data import start_daily_yahoo_refresh_scheduler

    start_daily_yahoo_refresh_scheduler()
    portfolio_url = portfolio_display_url(
        APP_BASE_URL,
        bind_host=APP_HOST,
        display_host=os.getenv("APP_DISPLAY_HOST"),
    )
    logging.getLogger("uvicorn.error").info(
        "%s ready — open from this device or your local network: %s",
        APP_NAME,
        portfolio_url,
    )
    if APP_HOST in {"0.0.0.0", "::"} and not http_auth_enabled():
        logging.getLogger("uvicorn.error").warning(
            "Portfolio is exposed on the local network without HTTP Basic Auth; "
            "set PORTFOLIO_HTTP_USER and PORTFOLIO_HTTP_PASSWORD before sharing it."
        )
    yield


app = FastAPI(
    title=APP_NAME,
    description=APP_TAGLINE,
    lifespan=lifespan,
    docs_url=None if http_auth_enabled() else "/docs",
    redoc_url=None if http_auth_enabled() else "/redoc",
    openapi_url=None if http_auth_enabled() else "/openapi.json",
)

app.mount(app_path("/static"), StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def home():
    """Default route — portfolio dashboard."""
    return RedirectResponse(url=app_path("/portfolio"), status_code=302)


if APP_ROOT_PATH:

    @app.get(APP_ROOT_PATH)
    @app.get(f"{APP_ROOT_PATH}/")
    def app_root():
        return RedirectResponse(url=app_path("/portfolio"), status_code=302)


@app.get("/health")
def health():
    return {"status": "ok", "app": "portfolio"}


app.include_router(portfolio_router, prefix=APP_ROOT_PATH)

app = add_http_basic_auth(app)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host=APP_HOST, port=APP_PORT, reload=True)
