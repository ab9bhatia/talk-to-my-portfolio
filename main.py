"""Portfolio — FastAPI application (standalone)."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from modules.portfolio.db import portfolio_cache as portfolio_cache_store
from modules.portfolio.db import tokens as token_store
from modules.portfolio.router import router as portfolio_router
from shared.config import APP_NAME, APP_TAGLINE

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "shared" / "web" / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize portfolio databases on startup."""
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
    yield


app = FastAPI(
    title=APP_NAME,
    description=APP_TAGLINE,
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def home():
    """Default route — portfolio dashboard."""
    return RedirectResponse(url="/portfolio", status_code=302)


@app.get("/health")
def health():
    return {"status": "ok", "app": "portfolio"}


app.include_router(portfolio_router)
