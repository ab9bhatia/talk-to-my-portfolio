"""Portfolio module routes — UI, API, and Zerodha OAuth."""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import RedirectResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

import json
import os

from modules.portfolio.auth.groww import GrowwError, get_groww_connection_status
from modules.portfolio.auth.zerodha import OAuthError, build_login_url, complete_oauth
from modules.portfolio.config import (
    ACCOUNTS,
    CUSTOM_ACCOUNTS,
    GROWW_ACCOUNTS,
    SARWA_ACCOUNTS,
    get_account_code,
    get_auth_start_url,
    get_hub_url,
    is_known_account,
    resolve_account_ref,
)
from modules.portfolio.db import weekly_history
from modules.portfolio.db import tokens as token_store
from modules.portfolio.services.holdings_view import (
    all_holdings_from_view,
    build_holdings_excel,
    holdings_financials_map,
    prepare_holdings_view,
)
from modules.portfolio.services.portfolio_agent import (
    agent_status,
    ask_portfolio_agent,
    stream_portfolio_agent,
)
from modules.portfolio.services.stock_insights import get_stock_insights
from modules.portfolio.auth.groww import GrowwError
from modules.portfolio.services.portfolio import (
    CACHE_TTL_SECONDS,
    fetch_account_portfolio,
    fetch_family_portfolio,
    invalidate_portfolio_cache,
)
from modules.portfolio.services.fx import fx_meta
from modules.portfolio.services.sarwa_screenshot import parse_sarwa_screenshot
from modules.portfolio.services.weekly_recorder import (
    import_sarwa_holdings,
    record_family_from_payload,
    refresh_all_current_week_ltps,
    sync_family_weekly_snapshot,
)
from modules.portfolio.services.portfolio_revalidate import meta_for_family
from shared.web.templates import templates

router = APIRouter(tags=["portfolio"])


class PortfolioAgentAskPayload(BaseModel):
    question: str | None = Field(default=None, max_length=2000)
    thread_id: str | None = Field(default=None, max_length=64)
    refresh: bool = False
    new_thread: bool = False


class PlaceOrderPayload(BaseModel):
    account_id: str = Field(..., min_length=1, max_length=32)
    symbol: str = Field(..., min_length=1, max_length=32)
    exchange: str = Field(default="NSE", max_length=16)
    side: str = Field(..., pattern=r"^(?i)buy|sell$")
    quantity: int = Field(..., ge=1, le=1_000_000)
    order_type: str = Field(default="MARKET", pattern=r"^(?i)market|limit$")
    price: float | None = Field(default=None, ge=0)
    confirmed: bool = False


class SarwaHoldingRow(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=32)
    quantity: float = Field(..., ge=0)
    avg_price_usd: float | None = Field(default=None, ge=0)
    last_price_usd: float | None = Field(default=None, ge=0)
    exchange: str = Field(default="US", max_length=16)
    asset_class: str = Field(default="equity", max_length=32)


class SarwaImportPayload(BaseModel):
    rows: list[SarwaHoldingRow] = Field(..., min_length=1)
    notes: str | None = Field(default=None, max_length=500)
    account_id: str = Field(default="sarwa", max_length=32)


VALID_SORT = {
    "value",
    "pnl",
    "pnl_pct",
    "symbol",
    "sector",
    "cap",
    "pe",
    "pct_52w",
    "upside",
    "signal",
    "qty",
    "avg",
    "ltp",
    "weight",
}
VALID_GROUP = {"", "cap", "sector", "account", "signal", "asset_class"}


def _normalize_view_params(sort: str, order: str, group_by: str) -> dict[str, str | None]:
    if sort not in VALID_SORT:
        sort = "value"
    if order not in {"asc", "desc"}:
        order = "desc"
    if group_by not in VALID_GROUP:
        group_by = ""
    return {"sort": sort, "order": order, "group_by": group_by or None}


def _export_query_string(sort: str, order: str, group_by: str | None) -> str:
    params = {"sort": sort, "order": order}
    if group_by:
        params["group_by"] = group_by
    return urlencode(params)


def _symbol_suggestions(holdings: list[dict]) -> list[str]:
    return sorted({h["symbol"] for h in holdings if h.get("symbol")})


def _account_statuses() -> list[dict]:
    """Build account connection status for the dashboard."""
    statuses = []
    for account_id, account in ACCOUNTS.items():
        token_status = token_store.get_token_status(account_id)
        statuses.append(
            {
                "account_id": account_id,
                "code": account["code"],
                "label": account["label"],
                "user_id": account["user_id"],
                "broker": "zerodha",
                "enabled": account.get("enabled", False),
                "disabled_reason": account.get("disabled_reason"),
                "auth_port": account.get("auth_port"),
                "redirect_url": account.get("redirect_url"),
                "connect_url": get_auth_start_url(account_id) if account.get("enabled") else None,
                **token_status,
            }
        )

    for account_id, account in GROWW_ACCOUNTS.items():
        conn = get_groww_connection_status(account_id) if account.get("enabled") else {}
        statuses.append(
            {
                "account_id": account_id,
                "code": account["code"],
                "label": account["label"],
                "user_id": account.get("user_id", "groww"),
                "broker": "groww",
                "enabled": account.get("enabled", False),
                "disabled_reason": account.get("disabled_reason"),
                "auth_port": None,
                "redirect_url": None,
                "connect_url": None,
                "connected": conn.get("connected", False) if account.get("enabled") else False,
                "needs_login": conn.get("needs_login", True) if account.get("enabled") else False,
                "groww_hint": conn.get("message"),
            }
        )

    for account_id, account in SARWA_ACCOUNTS.items():
        snap = (
            weekly_history.latest_snapshot(scope="account", account_id=account_id)
            if account.get("enabled")
            else None
        )
        statuses.append(
            {
                "account_id": account_id,
                "code": account["code"],
                "label": account["label"],
                "user_id": "sarwa",
                "broker": "sarwa",
                "enabled": account.get("enabled", False),
                "disabled_reason": account.get("disabled_reason"),
                "auth_port": None,
                "redirect_url": None,
                "connect_url": None,
                "connected": snap is not None,
                "needs_login": snap is None,
                "sarwa_hint": (
                    f"Week of {snap['week_start']}" if snap else "Import weekly holdings (POST /api/portfolio/sarwa/import)"
                ),
            }
        )

    from modules.portfolio.db import custom_holdings as custom_holdings_store

    for account_id, account in CUSTOM_ACCOUNTS.items():
        has = custom_holdings_store.has_holdings(account_id) if account.get("enabled") else False
        statuses.append(
            {
                "account_id": account_id,
                "code": account["code"],
                "label": account["label"],
                "user_id": "custom",
                "broker": "custom",
                "enabled": account.get("enabled", False),
                "disabled_reason": account.get("disabled_reason"),
                "auth_port": None,
                "redirect_url": None,
                "connect_url": "/portfolio/setup",
                "connected": has,
                "needs_login": not has,
                "custom_hint": "Import CSV/Excel in Setup" if not has else "Imported",
            }
        )
    return statuses


def _family_holdings_view(
    *,
    refresh: bool,
    view_params: dict[str, str | None],
) -> tuple[dict, dict, list[dict]]:
    """Fetch family portfolio and build aggregated holdings view for UI/export."""
    family = fetch_family_portfolio(refresh=refresh, stale_ok=not refresh)
    raw_holdings = [h for p in family["portfolios"] for h in p["holdings"]]
    holdings_view = prepare_holdings_view(
        raw_holdings, **view_params, aggregate_across_accounts=True
    )
    return family, holdings_view, raw_holdings


@router.get("/portfolio")
def portfolio_dashboard(
    request: Request,
    refresh: bool = Query(False),
    sort: str = Query("value"),
    order: str = Query("desc"),
    group_by: str = Query(""),
):
    """Portfolio module dashboard with summary and holdings."""
    view_params = _normalize_view_params(sort=sort, order=order, group_by=group_by)
    family, holdings_view, raw_holdings = _family_holdings_view(
        refresh=refresh, view_params=view_params
    )
    errors = [e["error"] for e in family.get("errors", [])]
    weekly_status = weekly_history.weekly_status()
    cache_meta = meta_for_family(fresh_ttl=CACHE_TTL_SECONDS)
    export_qs = _export_query_string(view_params["sort"], view_params["order"], view_params["group_by"])

    return templates.TemplateResponse(
        request,
        "portfolio/dashboard.html",
        {
            "active_module": "portfolio",
            "summary": family["summary"],
            "holdings_view": holdings_view,
            "accounts": _account_statuses(),
            "errors": errors,
            "cached_at": family.get("cached_at"),
            "from_cache": family.get("from_cache", False),
            "stale": family.get("stale", False),
            "cache_meta": cache_meta,
            "controls_action": "/portfolio",
            "export_url": f"/api/portfolio/export?{export_qs}",
            "refresh": refresh,
            "symbol_suggestions": _symbol_suggestions(raw_holdings),
            "holdings_financials_json": json.dumps(
                holdings_financials_map(all_holdings_from_view(holdings_view))
            ),
            "weekly_status": weekly_status,
            "sarwa_vision_available": bool(
                os.getenv("PORTFOLIO_OPENAI_API_KEY")
                or os.getenv("OPENAI_API_KEY")
                or os.getenv("API_KEY")
            ),
            "trading_enabled": _trading_enabled(),
        },
    )


@router.get("/portfolio/account/{account_ref}")
def portfolio_account(
    request: Request,
    account_ref: str,
    refresh: bool = Query(False),
    sort: str = Query("value"),
    order: str = Query("desc"),
    group_by: str = Query(""),
):
    """Single-account holdings view (Zerodha or Groww). account_ref: AB, RB, SB, or HB."""
    if not is_known_account(account_ref):
        raise HTTPException(status_code=404, detail=f"Unknown account: {account_ref}")

    account_id = resolve_account_ref(account_ref)
    account_code = get_account_code(account_id)
    view_params = _normalize_view_params(sort=sort, order=order, group_by=group_by)

    try:
        portfolio = fetch_account_portfolio(account_id, refresh=refresh, stale_ok=not refresh)
    except OAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except GrowwError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    holdings_view = prepare_holdings_view(portfolio["holdings"], **view_params)
    export_qs = _export_query_string(view_params["sort"], view_params["order"], view_params["group_by"])

    return templates.TemplateResponse(
        request,
        "portfolio/account.html",
        {
            "active_module": "portfolio",
            "portfolio": portfolio,
            "holdings_view": holdings_view,
            "cached_at": portfolio.get("cached_at"),
            "from_cache": portfolio.get("from_cache", False),
            "controls_action": f"/portfolio/account/{account_code}",
            "export_url": f"/api/portfolio/export/{account_code}?{export_qs}",
            "refresh": refresh,
            "show_account": False,
            "symbol_suggestions": _symbol_suggestions(portfolio["holdings"]),
            "trading_enabled": _trading_enabled(),
        },
    )


@router.get("/auth/zerodha/callback")
def zerodha_callback(
    request_token: str = Query(...),
    account_id: str | None = Query(None),
    code: str | None = Query(None),
    status: str | None = Query(None),
):
    """Complete OAuth and redirect to portfolio dashboard."""
    if status and status != "success":
        raise HTTPException(status_code=400, detail=f"Zerodha login failed: status={status}")

    ref = code or account_id or "AB"

    try:
        complete_oauth(request_token=request_token, ref=ref)
        invalidate_portfolio_cache()
    except (KeyError, OAuthError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return RedirectResponse(url=get_hub_url("/portfolio"), status_code=302)


@router.get("/zerodha/auth/redirect")
def zerodha_callback_legacy(
    request_token: str = Query(...),
    account_id: str | None = Query(None),
    code: str | None = Query(None),
    status: str | None = Query(None),
):
    """Legacy callback path registered on some Kite Connect apps."""
    return zerodha_callback(request_token, account_id, code, status)


@router.get("/auth/zerodha/{account_ref}")
def start_zerodha_login(account_ref: str):
    """Redirect the user to Kite login (account_ref: AB, RB, SB, …)."""
    if account_ref == "callback":
        raise HTTPException(status_code=404, detail="Not found")

    try:
        login_url = build_login_url(account_ref)
    except (KeyError, OAuthError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return RedirectResponse(url=login_url, status_code=302)


@router.get("/api/portfolio")
def api_family_portfolio(refresh: bool = Query(False)):
    """JSON API — consolidated family portfolio."""
    try:
        return fetch_family_portfolio(refresh=refresh, stale_ok=not refresh)
    except OAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@router.get("/api/portfolio/meta")
def api_family_portfolio_meta():
    """JSON API — cache freshness / background revalidation status."""
    return meta_for_family(fresh_ttl=CACHE_TTL_SECONDS)


@router.get("/api/portfolio/{account_ref}")
def api_account_portfolio(account_ref: str, refresh: bool = Query(False)):
    """JSON API — single account portfolio (account_ref: AB, RB, SB, HB)."""
    if not is_known_account(account_ref):
        raise HTTPException(status_code=404, detail=f"Unknown account: {account_ref}")

    account_id = resolve_account_ref(account_ref)
    try:
        return fetch_account_portfolio(account_id, refresh=refresh, stale_ok=not refresh)
    except OAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except GrowwError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@router.get("/api/status")
def api_status():
    """JSON API — broker connection status."""
    return {"accounts": _account_statuses()}


@router.post("/api/portfolio/sectors/classify")
def api_classify_sectors(force: bool = Query(False)):
    """
    Classify missing / generic-ETF holdings via LLM (cached in sector_llm_cache.db).
    Use after refresh if many rows show Unclassified.
    """
    from modules.portfolio.services.sector_llm import classify_holdings_llm, llm_available

    if not llm_available():
        raise HTTPException(
            status_code=503,
            detail="Sector LLM unavailable: set OPENAI_API_KEY in .env",
        )

    try:
        family = fetch_family_portfolio(refresh=True, stale_ok=True)
    except OAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    holdings = [h for p in family.get("portfolios", []) for h in p.get("holdings", [])]
    stats = classify_holdings_llm(holdings, force=force)
    uncl = sum(1 for h in holdings if not (h.get("sector") or "").strip())
    invalidate_portfolio_cache()
    return {**stats, "remaining_unclassified": uncl, "total_holdings": len(holdings)}


@router.post("/api/portfolio/groww/refresh")
def api_groww_refresh():
    """
    Clear cached Groww session and portfolio cache, then verify HB can load holdings.
    Use after approving the API key on Groww.
    """
    from modules.portfolio.db import groww_tokens as groww_token_store
    from modules.portfolio.auth.groww import verify_groww_session, GrowwError

    from modules.portfolio.config import get_first_enabled_groww_account_id

    groww_id = get_first_enabled_groww_account_id()
    if not groww_id:
        raise HTTPException(status_code=404, detail="No enabled Groww account in accounts.json")
    groww_token_store.delete_token(groww_id)
    invalidate_portfolio_cache()
    try:
        verify_groww_session(groww_id)
    except GrowwError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    return {"ok": True, "message": "Groww session verified. Reload /portfolio?refresh=1"}


def _trading_enabled() -> bool:
    from modules.portfolio.services.orders import trading_enabled

    return trading_enabled()


@router.get("/api/portfolio/trading/status")
def api_trading_status():
    """Whether live order placement is enabled and which accounts can trade."""
    from modules.portfolio.services.orders import trading_status

    return trading_status()


@router.post("/api/portfolio/orders/place")
def api_place_order(payload: PlaceOrderPayload):
    """Place a CNC equity order on Zerodha or Groww (requires TRADING_ENABLED=true)."""
    from modules.portfolio.services.orders import place_equity_order
    from modules.portfolio.services.portfolio import invalidate_portfolio_cache

    try:
        result = place_equity_order(
            account_ref=payload.account_id,
            symbol=payload.symbol,
            exchange=payload.exchange,
            side=payload.side,
            quantity=payload.quantity,
            order_type=payload.order_type,
            price=payload.price,
            confirmed=payload.confirmed,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    invalidate_portfolio_cache()
    return {"ok": True, **result}


@router.get("/api/portfolio/agent/status")
def portfolio_agent_status():
    """JSON API — portfolio agent LLM configuration."""
    return agent_status()


@router.post("/api/portfolio/agent/ask")
def portfolio_agent_ask(payload: PortfolioAgentAskPayload):
    """JSON API — portfolio-level recommendations (non-streaming fallback)."""
    try:
        return ask_portfolio_agent(
            question=payload.question,
            thread_id=payload.thread_id,
            refresh=payload.refresh,
            new_thread=payload.new_thread,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/api/portfolio/agent/ask/stream")
def portfolio_agent_ask_stream(payload: PortfolioAgentAskPayload):
    """SSE stream — typed events: status | token | done | error (browser-friendly gRPC-style)."""
    if not agent_status().get("available"):
        raise HTTPException(status_code=503, detail="Portfolio agent unavailable: API key not configured")

    return StreamingResponse(
        stream_portfolio_agent(
            question=payload.question,
            thread_id=payload.thread_id,
            refresh=payload.refresh,
            new_thread=payload.new_thread,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/api/portfolio/sarwa/import")
def api_sarwa_import(payload: SarwaImportPayload):
    """
    Import Sarwa holdings from weekly screenshot data (USD → INR).

  Example body:
  {"rows": [{"symbol": "AAPL", "quantity": 10, "avg_price_usd": 150, "last_price_usd": 175}]}
    """
    try:
        account_id = resolve_account_ref(payload.account_id)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if account_id not in SARWA_ACCOUNTS:
        raise HTTPException(status_code=400, detail=f"Not a Sarwa account: {account_id}")

    rows = [row.model_dump() for row in payload.rows]
    try:
        result = import_sarwa_holdings(rows, account_id=account_id, notes=payload.notes)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    invalidate_portfolio_cache()
    return {**result, "fx": fx_meta()}


@router.post("/api/portfolio/sarwa/import-screenshot")
async def api_sarwa_import_screenshot(
    file: UploadFile = File(...),
    account_id: str = Query("sarwa"),
):
    """Parse Sarwa Trade screenshot (vision) and import as SW holdings."""
    try:
        resolved = resolve_account_ref(account_id)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if resolved not in SARWA_ACCOUNTS:
        raise HTTPException(status_code=400, detail=f"Not a Sarwa account: {resolved}")

    content = await file.read()
    media = file.content_type or "image/png"
    try:
        parsed = parse_sarwa_screenshot(content, media_type=media)
        result = import_sarwa_holdings(
            parsed["rows"],
            account_id=resolved,
            notes=parsed.get("notes"),
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    invalidate_portfolio_cache()
    return {
        **result,
        "fx": fx_meta(),
        "parsed_count": parsed.get("parsed_count"),
        "rows_preview": parsed["rows"],
    }


@router.post("/api/portfolio/sarwa/refresh-metrics")
def api_sarwa_refresh_metrics():
    """Re-fetch Yahoo fundamentals for latest Sarwa snapshot and refresh dashboard."""
    from modules.portfolio.services.weekly_recorder import sarwa_positions_from_rows
    from modules.portfolio.services.fx import usd_inr_rate

    snap = weekly_history.latest_snapshot(scope="account", account_id="sarwa")
    if not snap:
        raise HTTPException(status_code=404, detail="No Sarwa snapshot — import a screenshot first")

    rows = []
    for p in snap.get("positions") or []:
        extra = p.get("extra") or {}
        rows.append(
            {
                "symbol": p["symbol"],
                "quantity": p["quantity"],
                "exchange": p.get("exchange") or "US",
                "avg_price_usd": extra.get("avg_price_usd"),
                "last_price_usd": extra.get("last_price_usd"),
                "asset_class": p.get("asset_class") or "equity",
            }
        )
    positions = sarwa_positions_from_rows(rows, account_id="sarwa", enrich=True)
    from modules.portfolio.services.weekly_recorder import repair_sarwa_weekly_snapshot

    weekly_history.save_snapshot(
        scope="account",
        account_id="sarwa",
        positions=positions,
        source="sarwa_manual",
        usd_inr=usd_inr_rate(),
        notes="Yahoo metrics refresh",
    )
    repair_sarwa_weekly_snapshot("sarwa")
    sync_family_weekly_snapshot(source="sarwa_manual")
    invalidate_portfolio_cache()
    return {"updated": len(positions)}


@router.get("/api/portfolio/weekly/status")
def api_weekly_status():
    """Confirm weekly SQLite history and latest snapshot weeks."""
    return weekly_history.weekly_status()


@router.get("/api/portfolio/weekly/history")
def api_weekly_history(
    scope: str = Query("family"),
    account_ref: str | None = Query(None),
    weeks: int = Query(52, ge=1, le=104),
):
    """Weekly portfolio totals for growth tracking (oldest → newest)."""
    account_id = None
    if account_ref:
        try:
            account_id = resolve_account_ref(account_ref)
        except KeyError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    series = weekly_history.growth_series(scope=scope, account_id=account_id, weeks=weeks)
    return {
        "scope": scope,
        "account_id": account_id,
        "weeks": weeks,
        "series": series,
        "fx": fx_meta(),
    }


@router.get("/api/portfolio/weekly/compare")
def api_weekly_compare(
    scope: str = Query("family"),
    account_ref: str | None = Query(None),
):
    """Compare latest week vs previous — qty drops imply sales while offline."""
    account_id = None
    if account_ref:
        try:
            account_id = resolve_account_ref(account_ref)
        except KeyError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return weekly_history.compare_weeks(scope=scope, account_id=account_id)


@router.get("/api/portfolio/weekly/export")
def export_weekly_history(
    scope: str = Query("family"),
    account_id: str | None = Query(None),
    weeks: int = Query(52, ge=1, le=104),
):
    """Download weekly snapshot history (summary, positions, week-over-week changes) as Excel."""
    from modules.portfolio.services.weekly_export import build_weekly_history_excel

    if scope not in ("family", "account"):
        raise HTTPException(status_code=400, detail="scope must be family or account")
    resolved_account: str | None = None
    if scope == "account":
        if not account_id:
            raise HTTPException(status_code=400, detail="account_id required when scope=account")
        resolved_account = resolve_account_ref(account_id)

    try:
        content = build_weekly_history_excel(
            scope=scope,
            account_id=resolved_account,
            weeks=weeks,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    label = resolved_account or "family"
    filename = f"portfolio-weekly-{label}.xlsx"
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/api/portfolio/weekly/snapshot")
def api_weekly_snapshot_detail(
    week_start: str | None = Query(None),
    scope: str = Query("family"),
    account_ref: str | None = Query(None),
):
    """Full positions for a week (default: latest)."""
    account_id = None
    if account_ref:
        try:
            account_id = resolve_account_ref(account_ref)
        except KeyError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    if week_start:
        snaps = weekly_history.list_snapshots(scope=scope, account_id=account_id, limit=104)
        match = next((s for s in snaps if s["week_start"] == week_start), None)
        if not match:
            raise HTTPException(status_code=404, detail=f"No snapshot for week {week_start}")
        snap = weekly_history.get_snapshot(match["id"])
    else:
        snap = weekly_history.latest_snapshot(scope=scope, account_id=account_id)

    if not snap:
        raise HTTPException(status_code=404, detail="No weekly snapshot found")
    return snap


@router.post("/api/portfolio/weekly/snapshot")
def api_record_weekly_snapshot(force: bool = Query(False)):
    """Record family + per-account snapshots for the current ISO week."""
    try:
        family = fetch_family_portfolio(refresh=True, stale_ok=False)
    except OAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    if force:
        recorded = record_family_from_payload(family, source="manual")
    else:
        from modules.portfolio.services.weekly_recorder import record_if_new_week

        recorded = record_if_new_week(family, source="manual", force=False)
        if recorded is None:
            return {
                "recorded": False,
                "message": "Snapshot already exists for this week; pass force=true to replace",
                "week_start": weekly_history.week_start_for(),
            }
    return {"recorded": True, "snapshots": recorded}


@router.post("/api/portfolio/weekly/refresh-ltps")
def api_refresh_weekly_ltps():
    """Update LTPs on current-week snapshots via Yahoo (no broker login)."""
    from modules.portfolio.config import get_enabled_accounts, get_enabled_groww_accounts, get_enabled_sarwa_accounts

    account_ids = (
        list(get_enabled_accounts())
        + list(get_enabled_groww_accounts())
        + list(get_enabled_sarwa_accounts())
    )
    return {"refreshed": refresh_all_current_week_ltps(account_ids)}


@router.get("/api/portfolio/insights/{symbol}")
def stock_insights(
    symbol: str,
    exchange: str = Query("NSE"),
    quantity: float = Query(0),
    last_price: float | None = Query(None),
    last_price_usd: float | None = Query(None),
):
    """JSON API — chart, recent results, and 1Y forecast for a symbol."""
    from modules.portfolio.services.market_data import metric_last_price

    price = metric_last_price(
        {
            "symbol": symbol,
            "exchange": exchange,
            "last_price": last_price,
            "last_price_usd": last_price_usd,
        }
    )
    try:
        return get_stock_insights(
            symbol,
            exchange,
            quantity=quantity,
            last_price=price,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Insights unavailable: {exc}") from exc


@router.get("/api/portfolio/export")
def export_family_portfolio(
    refresh: bool = Query(False),
    sort: str = Query("value"),
    order: str = Query("desc"),
    group_by: str = Query(""),
):
    """Download family holdings as Excel."""
    view_params = _normalize_view_params(sort=sort, order=order, group_by=group_by)
    _, holdings_view, _ = _family_holdings_view(refresh=refresh, view_params=view_params)
    content = build_holdings_excel(holdings_view, include_account=True, sheet_title="Family Portfolio")

    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="portfolio-family.xlsx"'},
    )


@router.get("/api/portfolio/export/{account_ref}")
def export_account_portfolio(
    account_ref: str,
    refresh: bool = Query(False),
    sort: str = Query("value"),
    order: str = Query("desc"),
    group_by: str = Query(""),
):
    """Download single-account holdings as Excel."""
    if not is_known_account(account_ref):
        raise HTTPException(status_code=404, detail=f"Unknown account: {account_ref}")

    account_id = resolve_account_ref(account_ref)
    account_code = get_account_code(account_id)
    view_params = _normalize_view_params(sort=sort, order=order, group_by=group_by)

    try:
        portfolio = fetch_account_portfolio(account_id, refresh=refresh, stale_ok=not refresh)
    except OAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except GrowwError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    holdings_view = prepare_holdings_view(portfolio["holdings"], **view_params)
    label = portfolio.get("account_code") or account_code
    content = build_holdings_excel(holdings_view, include_account=False, sheet_title=label)

    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="portfolio-{account_code}.xlsx"'},
    )


# --- Account setup / onboarding ---


@router.get("/portfolio/setup")
def portfolio_setup_page(request: Request):
    """Onboarding — add brokers and custom portfolios."""
    from modules.portfolio.services.onboarding import account_setup_status, broker_catalog, default_callback_url

    accounts = account_setup_status()
    ready_count = sum(
        1
        for a in accounts
        if a.get("connected")
        or (a.get("credentials_ok") and a.get("broker") in ("groww", "zerodha"))
    )
    from modules.portfolio.services.holdings_screenshot import vision_available

    return templates.TemplateResponse(
        request,
        "portfolio/setup.html",
        {
            "active_module": "setup",
            "accounts": accounts,
            "default_callback_url": default_callback_url(),
            "setup_stats": {"linked": len(accounts), "ready": ready_count},
            "vision_available": vision_available(),
        },
    )


@router.get("/api/portfolio/setup/brokers")
def api_setup_brokers():
    from modules.portfolio.services.onboarding import broker_catalog, default_callback_url

    return {"brokers": broker_catalog(), "default_callback_url": default_callback_url()}


@router.get("/api/portfolio/setup/accounts")
def api_setup_accounts():
    from modules.portfolio.services.onboarding import account_setup_status

    return {"accounts": account_setup_status()}


class SetupAccountPayload(BaseModel):
    label: str = Field(..., min_length=1, max_length=64)
    id: str = Field(..., min_length=2, max_length=32)
    code: str | None = Field(default=None, max_length=8)
    user_id: str | None = None
    api_key: str | None = None
    api_secret: str | None = None
    redirect_url: str | None = None
    auth_method: str | None = None
    totp_token: str | None = None
    totp_secret: str | None = None
    enabled: bool | None = None


class SetupAccountUpdatePayload(BaseModel):
    label: str | None = Field(default=None, max_length=64)
    code: str | None = Field(default=None, max_length=8)
    user_id: str | None = None
    api_key: str | None = None
    api_secret: str | None = None
    redirect_url: str | None = None
    auth_method: str | None = None
    totp_token: str | None = None
    totp_secret: str | None = None
    enabled: bool | None = None
    relation: str | None = None


@router.get("/api/portfolio/setup/accounts/{broker}/{account_id}")
def api_setup_get_account(broker: str, account_id: str):
    from modules.portfolio.services.onboarding import get_account_for_edit

    try:
        return get_account_for_edit(broker, account_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/api/portfolio/setup/accounts/{broker}/{account_id}")
def api_setup_update_account(broker: str, account_id: str, payload: SetupAccountUpdatePayload):
    from modules.portfolio.services.onboarding import update_account
    from modules.portfolio.services.portfolio import invalidate_portfolio_cache

    try:
        result = update_account(broker, account_id, payload.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    invalidate_portfolio_cache()
    return result


@router.post("/api/portfolio/setup/accounts/{broker}")
def api_setup_add_account(broker: str, payload: SetupAccountPayload):
    from modules.portfolio.services.onboarding import add_account
    from modules.portfolio.services.portfolio import invalidate_portfolio_cache

    try:
        result = add_account(broker, payload.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    invalidate_portfolio_cache()
    return result


@router.post("/api/portfolio/setup/accounts/{broker}/{account_id}/import")
async def api_setup_import_holdings(
    broker: str,
    account_id: str,
    file: UploadFile = File(...),
):
    from modules.portfolio.services.onboarding import import_account_upload
    from modules.portfolio.services.portfolio import invalidate_portfolio_cache

    content = await file.read()
    try:
        result = import_account_upload(
            broker,
            account_id,
            content,
            filename=file.filename or "upload.csv",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    invalidate_portfolio_cache()
    return result
