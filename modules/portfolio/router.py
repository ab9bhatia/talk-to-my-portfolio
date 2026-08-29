"""Portfolio module routes — UI, API, and Zerodha OAuth."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import RedirectResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

import json
import os

from modules.portfolio.auth.groww import GrowwError, get_groww_connection_status
from modules.portfolio.account_profile import (
    AccountType,
    EstateTaxReviewStatus,
    IndiaResidencyStatus,
    Repatriability,
    RiskProfile,
    TaxLossHarvestingMode,
)
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
from modules.portfolio.db import daily_history
from modules.portfolio.db import import_audit as import_audit_store
from modules.portfolio.db import profile_goals as profile_goals_store
from modules.portfolio.db import weekly_history
from modules.portfolio.db import tokens as token_store
from modules.portfolio.services.holdings_view import (
    all_holdings_from_view,
    build_holdings_excel,
    export_column_options,
    filter_holdings_by_account_codes,
    holdings_financials_map,
    normalize_export_columns,
    prepare_holdings_view,
)
from modules.portfolio.services.agent_threads import (
    delete_thread,
    get_thread,
    list_sessions,
    set_thread_important,
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
from modules.portfolio.services.daily_sheet_import import (
    DEFAULT_ACCOUNT_ALIASES,
    import_distribution_history,
)
from shared.web.formatters import format_data_as_of_label
from shared.web.app_urls import app_path
from shared.web.templates import templates

router = APIRouter(tags=["portfolio"])
API_CONTRACT_VERSION = "2026-05-mobile-mvp-v1"


class PortfolioAgentAskPayload(BaseModel):
    question: str | None = Field(default=None, max_length=2000)
    thread_id: str | None = Field(default=None, max_length=64)
    refresh: bool = False
    new_thread: bool = False


class PortfolioAgentSessionPatchPayload(BaseModel):
    important: bool


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


class DailySheetImportPayload(BaseModel):
    sheet_url: str = Field(..., min_length=16, max_length=500)
    sheet_name: str = Field(default="Distribution", min_length=1, max_length=120)
    overwrite_existing: bool = False
    account_aliases: dict[str, list[str]] | None = None


class PortfolioGoalsPayload(BaseModel):
    target_return_pct: float = Field(15, ge=0, le=200)
    max_position_pct: float = Field(12, ge=1, le=100)
    max_sector_pct: float = Field(30, ge=1, le=100)
    cash_buffer_pct: float = Field(5, ge=0, le=100)
    risk_profile: str = Field("moderate", max_length=20)


class WeeklySyncPayload(BaseModel):
    mode: str = Field(default="auto", pattern=r"^(auto|live|safe-fallback)$")
    dry_run: bool = False
    stage: str | None = Field(
        default=None,
        pattern=r"^(INDIA_CLOSE|GLOBAL_CLOSE_FINALIZATION|MANUAL_RERUN)$",
    )


class InstrumentResolvePayload(BaseModel):
    symbol: str | None = Field(default=None, max_length=80)
    exchange: str = Field(default="NSE", max_length=20)
    isin: str | None = Field(default=None, max_length=32)
    broker_instrument_id: str | None = Field(default=None, max_length=120)
    yahoo_ticker: str | None = Field(default=None, max_length=80)
    display_name: str | None = Field(default=None, max_length=240)
    fund_name: str | None = Field(default=None, max_length=240)
    asset_class: str = Field(default="equity", max_length=40)
    quote_type: str | None = Field(default=None, max_length=40)
    currency: str | None = Field(default=None, max_length=8)


class ReconciliationOverridePayload(BaseModel):
    instrument_id: str = Field(..., min_length=8, max_length=64)
    account_code: str | None = Field(default=None, max_length=16)
    override_type: str = Field(..., min_length=3, max_length=64)
    value: Any = None
    reason: str = Field(..., min_length=8, max_length=1000)
    source_document: str = Field(..., min_length=3, max_length=1000)
    as_of_date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    approved_by: str = Field(..., min_length=2, max_length=120)


class TransactionImportPayload(BaseModel):
    source: str = Field(..., min_length=2, max_length=64)
    source_document: str | None = Field(default=None, max_length=500)
    rows: list[dict[str, Any]] = Field(..., min_length=1, max_length=10000)


class MrmiObservationPayload(BaseModel):
    as_of: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    observation_state: str = Field(
        default="PROVISIONAL", pattern=r"^(PROVISIONAL|FINALIZED|BACKFILLED)$"
    )
    components: dict[str, dict[str, Any]]


class ResearchScorecardPayload(BaseModel):
    instrument_id: str = Field(..., min_length=8, max_length=64)
    evidence: dict[str, Any]


class ResearchScreenRunPayload(BaseModel):
    definition: dict[str, Any]
    rows: list[dict[str, Any]] = Field(..., max_length=5000)


class SavedResearchScreenPayload(BaseModel):
    name: str = Field(..., min_length=2, max_length=120)
    definition: dict[str, Any]
    screen_id: str | None = Field(default=None, max_length=64)
    reason: str = Field(default="saved", min_length=2, max_length=240)


class ResearchCandidatePayload(BaseModel):
    instrument_id: str = Field(..., min_length=8, max_length=64)
    research_status: str = Field(..., pattern=r"^(APPROVED|RESEARCH|REJECTED)$")
    source_coverage_pct: float = Field(default=0, ge=0, le=100)
    account_eligibility: list[str] = Field(default_factory=list, max_length=50)
    role: str = Field(..., min_length=2, max_length=120)
    max_weight_pct: float = Field(default=0, ge=0, le=100)
    liquidity_threshold: float = Field(default=0, ge=0)
    overlap_impact: str = Field(default="UNKNOWN", max_length=240)
    source: str = Field(..., min_length=2, max_length=500)
    source_as_of: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")


class ResearchWatchlistPayload(BaseModel):
    watchlist_name: str = Field(default="Research", max_length=120)
    instrument_id: str = Field(..., min_length=8, max_length=64)
    target_role: str = Field(..., min_length=2, max_length=120)
    entry_condition: str = Field(..., min_length=3, max_length=1000)
    desired_weight_pct: float = Field(default=0, ge=0, le=100)
    valuation_range: str | None = Field(default=None, max_length=240)
    event_deadline: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    invalidation_trigger: str = Field(..., min_length=3, max_length=1000)
    source_evidence: str = Field(..., min_length=3, max_length=1000)
    user_notes: str | None = Field(default=None, max_length=2000)


class ResearchThesisPayload(BaseModel):
    instrument_id: str = Field(..., min_length=8, max_length=64)
    thesis: str = Field(..., min_length=8, max_length=4000)
    invalidation_trigger: str = Field(..., min_length=3, max_length=2000)
    source: str = Field(..., min_length=2, max_length=1000)
    source_as_of: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    decision: str = Field(default="WATCH", max_length=40)
    author: str = Field(default="local-user", min_length=2, max_length=120)


class ResearchEventPayload(BaseModel):
    instrument_id: str | None = Field(default=None, max_length=64)
    event_type: str = Field(..., min_length=2, max_length=80)
    event_date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    title: str = Field(..., min_length=3, max_length=500)
    source: str = Field(..., min_length=2, max_length=1000)
    source_as_of: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    verified: bool = False
    ownership_change_pct: float | None = None


class ResearchComparePayload(BaseModel):
    items: list[dict[str, Any]] = Field(..., min_length=2, max_length=5)


class FundSchemePayload(BaseModel):
    instrument_id: str = Field(..., min_length=8, max_length=64)
    canonical_scheme_name: str = Field(..., min_length=3, max_length=300)
    isin: str | None = Field(default=None, max_length=32)
    ticker: str | None = Field(default=None, max_length=80)
    amc_issuer: str | None = Field(default=None, max_length=240)
    scheme_plan: str | None = Field(default=None, pattern=r"^(Direct|Regular)$")
    scheme_option: str | None = Field(default=None, pattern=r"^(Growth|IDCW)$")
    domicile: str | None = Field(default=None, max_length=8)
    currency: str = Field(default="INR", max_length=8)
    underlying_index_category: str | None = Field(default=None, max_length=240)
    aum: float | None = Field(default=None, ge=0)
    ter_pct: float | None = Field(default=None, ge=0, le=20)
    tracking_error_pct: float | None = Field(default=None, ge=0)
    tracking_difference_pct: float | None = None
    inception_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    manager: str | None = Field(default=None, max_length=240)
    manager_tenure_years: float | None = Field(default=None, ge=0)
    exit_load: str | None = Field(default=None, max_length=500)
    bid_ask_spread_pct: float | None = Field(default=None, ge=0)
    average_traded_value: float | None = Field(default=None, ge=0)
    premium_discount_pct: float | None = None
    rebalance_schedule: str | None = Field(default=None, max_length=240)
    factsheet_source: str = Field(..., min_length=3, max_length=1000)
    factsheet_as_of: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    instrument_type: str = Field(..., pattern=r"^(etf|mutual_fund)$")


class FundConstituentPayload(BaseModel):
    underlying_instrument_id: str | None = Field(default=None, max_length=64)
    unresolved_label: str | None = Field(default=None, max_length=300)
    weight_pct: float = Field(..., ge=0, le=100)
    sector: str | None = Field(default=None, max_length=120)
    market_cap: str | None = Field(default=None, max_length=40)
    factor_style: str | None = Field(default=None, max_length=120)
    promoter_group: str | None = Field(default=None, max_length=240)


class FundHoldingsIngestPayload(BaseModel):
    fund_instrument_id: str = Field(..., min_length=8, max_length=64)
    as_of: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    source: str = Field(..., min_length=3, max_length=1000)
    source_type: str = Field(..., pattern=r"^(AMC_FACTSHEET|INDEX_FILE|OFFICIAL_DOCUMENT|MANUAL)$")
    coverage_type: str = Field(..., pattern=r"^(FULL|TOP_HOLDINGS|PARTIAL)$")
    coverage_pct: float = Field(..., ge=0, le=100)
    rows: list[FundConstituentPayload] = Field(..., min_length=1, max_length=10000)


class StressScenarioPayload(BaseModel):
    name: str = Field(..., min_length=2, max_length=120)
    custom_assumptions: dict[str, Any] | None = None
    save: bool = False


class WhatIfPayload(BaseModel):
    operations: list[dict[str, Any]] = Field(..., min_length=1, max_length=100)
    constraints: dict[str, Any] = Field(default_factory=dict)


class AlertEvaluationPayload(BaseModel):
    events: list[dict[str, Any]] = Field(..., max_length=1000)
    cooldown_seconds: int = Field(default=86400, ge=60, le=2592000)


class AfterTaxPayload(BaseModel):
    candidate: dict[str, Any]
    account: dict[str, Any]
    as_of: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")


class AssetLocationPayload(BaseModel):
    candidate: dict[str, Any]
    accounts: list[dict[str, Any]] = Field(..., min_length=1, max_length=100)
    as_of: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")


class TaxHarvestPayload(BaseModel):
    holding: dict[str, Any]
    account: dict[str, Any]
    lots: list[dict[str, Any]] = Field(default_factory=list, max_length=10000)
    as_of: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")


class SecretMigrationPayload(BaseModel):
    store: str = Field(..., pattern=r"^(zerodha|groww)$")
    confirmed: bool = False


class BackupPayload(BaseModel):
    filename: str = Field(..., pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{1,100}\.ttmpbackup$")
    password: str = Field(..., min_length=12, max_length=500)


class RestorePayload(BackupPayload):
    selected: list[str] | None = Field(default=None, max_length=100)
    dry_run: bool = True
    confirmed: bool = False


class AdvisoryRebalanceTarget(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=32)
    target_weight_pct: float = Field(..., ge=0, le=100)


class AdvisoryRebalancePayload(BaseModel):
    targets: list[AdvisoryRebalanceTarget] = Field(..., min_length=1, max_length=250)


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


@router.get("/api/portfolio/version")
def api_portfolio_version():
    return {
        "contract_version": API_CONTRACT_VERSION,
        "app_version": os.getenv("APP_VERSION", "dev"),
    }


@router.get("/api/portfolio/security/csrf")
def api_security_csrf():
    from shared.web.http_auth import csrf_token

    return {"csrf_token": csrf_token(), "header": "X-Portfolio-CSRF"}


def _secret_store_spec(store: str):
    from modules.portfolio.db import groww_tokens, tokens

    return (
        (tokens.DB_PATH, "tokens", "zerodha")
        if store == "zerodha"
        else (groww_tokens.DB_PATH, "groww_tokens", "groww")
    )


@router.get("/api/portfolio/security/secrets/migration-preview")
def api_secret_migration_preview(store: str = Query(..., pattern=r"^(zerodha|groww)$")):
    from modules.portfolio.services.secret_storage import migration_preview

    path, table, _namespace = _secret_store_spec(store)
    return migration_preview(path, table=table)


@router.post("/api/portfolio/security/secrets/migrate")
def api_migrate_secrets(payload: SecretMigrationPayload):
    from datetime import UTC, datetime

    from modules.portfolio.services.secret_storage import migrate_plaintext_secrets

    path, table, namespace = _secret_store_spec(payload.store)
    try:
        return migrate_plaintext_secrets(
            path, table=table, namespace=namespace, confirmed=payload.confirmed,
            now=datetime.now(UTC).isoformat(),
        )
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/api/portfolio/security/secrets/rollback")
def api_rollback_secrets(payload: SecretMigrationPayload):
    from modules.portfolio.services.secret_storage import rollback_secret_migration

    path, table, _namespace = _secret_store_spec(payload.store)
    try:
        return rollback_secret_migration(path, table=table, confirmed=payload.confirmed)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/api/portfolio/security/secrets/{store}/{account_id}")
def api_revoke_local_token(
    store: str, account_id: str,
):
    if store not in {"zerodha", "groww"} or not account_id or len(account_id) > 64:
        raise HTTPException(status_code=422, detail="Invalid token-store reference.")
    from modules.portfolio.db import groww_tokens, tokens

    if store == "zerodha":
        tokens.delete_token(account_id)
    else:
        groww_tokens.delete_token(account_id)
    return {"revoked": True, "store": store, "account_id_exposed": False}


@router.post("/api/portfolio/security/backup")
def api_create_backup(payload: BackupPayload):
    from modules.portfolio.paths import DATA_DIR
    from modules.portfolio.services.backup_restore import create_encrypted_backup

    output = DATA_DIR / "backups" / payload.filename
    return create_encrypted_backup(output, password=payload.password)


@router.post("/api/portfolio/security/restore")
def api_restore_backup(payload: RestorePayload):
    from modules.portfolio.paths import DATA_DIR
    from modules.portfolio.services.backup_restore import restore_backup

    if not payload.dry_run:
        raise HTTPException(
            status_code=409,
            detail="Stop the app and use scripts/portfolio_recovery.py for an applied restore.",
        )
    path = DATA_DIR / "backups" / payload.filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Backup file not found.")
    try:
        return restore_backup(
            path, password=payload.password, selected=payload.selected, dry_run=payload.dry_run
        )
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/api/portfolio/security/privacy")
def api_privacy_controls():
    from modules.portfolio.services.privacy_controls import privacy_status

    return privacy_status()


@router.get("/api/portfolio/security/llm-context-preview")
def api_llm_context_preview():
    from modules.portfolio.services.portfolio_agent import external_context_preview
    from modules.portfolio.services.portfolio_context import build_portfolio_context

    return external_context_preview(build_portfolio_context(refresh=False))


@router.get("/api/portfolio/security/diagnostics")
def api_security_diagnostics():
    from modules.portfolio.services.diagnostics import collect_diagnostics

    return collect_diagnostics()


@router.get("/api/portfolio/security/support-bundle.zip")
def api_support_bundle(include_raw_holdings: bool = Query(False)):
    from modules.portfolio.services.diagnostics import build_support_bundle

    family = fetch_family_portfolio(refresh=False, stale_ok=True) if include_raw_holdings else None
    return StreamingResponse(
        build_support_bundle(family=family, include_raw_holdings=include_raw_holdings),
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=ttmp-support-bundle.zip"},
    )


@router.get("/api/portfolio/instruments")
def api_instruments(
    query: str | None = Query(default=None, max_length=120),
    limit: int = Query(100, ge=1, le=500),
):
    from modules.portfolio.db import instrument_master

    return {
        "schema_version": instrument_master.SCHEMA_VERSION,
        "instruments": instrument_master.list_instruments(query=query, limit=limit),
    }


@router.post("/api/portfolio/instruments/resolve")
def api_resolve_instrument(payload: InstrumentResolvePayload):
    from modules.portfolio.services.instrument_master import resolve_holding

    result = resolve_holding(payload.model_dump(exclude_none=True))
    if not result["resolved"]:
        raise HTTPException(status_code=422, detail=result["reason"])
    return result


def _reconciliation_payload(*, refresh: bool = False) -> dict[str, Any]:
    family = fetch_family_portfolio(refresh=refresh, stale_ok=True)
    return family.get("reconciliation") or {
        "summary": {},
        "by_account": [],
        "by_security": [],
        "unresolved_instruments": [],
        "corporate_action_review": [],
    }


@router.get("/api/portfolio/reconciliation/summary")
def api_reconciliation_summary(refresh: bool = Query(False)):
    return _reconciliation_payload(refresh=refresh)


@router.get("/api/portfolio/reconciliation/detail")
def api_reconciliation_detail(
    instrument_id: str | None = Query(default=None, max_length=64),
    account_code: str | None = Query(default=None, max_length=16),
):
    payload = _reconciliation_payload()
    securities = payload.get("by_security") or []
    accounts = payload.get("by_account") or []
    if instrument_id:
        securities = [row for row in securities if row.get("instrument_id") == instrument_id]
    if account_code:
        code = account_code.strip().upper()
        securities = [row for row in securities if code in (row.get("accounts") or [])]
        accounts = [
            row
            for row in accounts
            if str(row.get("account_code") or "").upper() == code
        ]
    return {"by_security": securities, "by_account": accounts}


@router.get("/api/portfolio/reconciliation/unresolved")
def api_reconciliation_unresolved():
    payload = _reconciliation_payload()
    return {"unresolved_instruments": payload.get("unresolved_instruments") or []}


@router.get("/api/portfolio/reconciliation/corporate-actions")
def api_corporate_action_review():
    from modules.portfolio.db import instrument_master

    return {
        "corporate_actions": instrument_master.list_corporate_actions(pending_only=True)
    }


@router.post("/api/portfolio/reconciliation/overrides")
def api_create_reconciliation_override(payload: ReconciliationOverridePayload):
    from modules.portfolio.db import instrument_master

    if instrument_master.get_instrument(payload.instrument_id) is None:
        raise HTTPException(status_code=404, detail="Unknown instrument_id.")
    try:
        row = instrument_master.create_override(payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    invalidate_portfolio_cache(preserve_disk=True)
    return {
        "override": row,
        "audit": instrument_master.override_audit(int(row["override_id"])),
    }


def _account_id_from_code(account_code: str | None) -> str | None:
    if not account_code:
        return None
    try:
        return resolve_account_ref(account_code)
    except KeyError:
        return account_code.strip()


def _public_transaction(row: dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    account_id = str(item.pop("account_id", ""))
    try:
        item["account_code"] = get_account_code(account_id)
    except KeyError:
        item["account_code"] = account_id.upper() or "UNKNOWN"
    return item


def _public_import_batch(batch: dict[str, Any]) -> dict[str, Any]:
    return {
        key: batch.get(key)
        for key in (
            "import_batch_id", "schema_version", "source", "status", "row_count",
            "valid_count", "unresolved_count", "committed_count", "duplicate_count",
            "created_at", "committed_at", "rolled_back_at",
        )
    }


@router.post("/api/portfolio/transactions/import/preview")
def api_transaction_import_preview(payload: TransactionImportPayload):
    from modules.portfolio.services.transaction_import import preview_import

    rows = []
    for raw in payload.rows:
        row = dict(raw)
        if not row.get("account_id") and row.get("account_code"):
            row["account_id"] = _account_id_from_code(str(row["account_code"]))
        rows.append(row)
    result = preview_import(
        source=payload.source,
        source_document=payload.source_document,
        rows=rows,
    )
    result["transactions"] = [_public_transaction(row) for row in result["transactions"]]
    for unresolved in result["unresolved"]:
        unresolved["row"] = _public_transaction(unresolved.get("row") or {})
    return result


@router.post("/api/portfolio/transactions/import/{import_batch_id}/commit")
def api_transaction_import_commit(import_batch_id: str):
    from modules.portfolio.services.transaction_import import commit_import

    try:
        return _public_import_batch(commit_import(import_batch_id))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown import batch.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/api/portfolio/transactions/import/{import_batch_id}/rollback")
def api_transaction_import_rollback(import_batch_id: str):
    from modules.portfolio.services.transaction_import import rollback_import

    try:
        return _public_import_batch(rollback_import(import_batch_id))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown import batch.") from exc


@router.get("/api/portfolio/transactions")
def api_transactions(
    account_code: str | None = Query(default=None, max_length=16),
    instrument_id: str | None = Query(default=None, max_length=64),
    limit: int = Query(1000, ge=1, le=10000),
):
    from modules.portfolio.db import transaction_ledger

    rows = transaction_ledger.list_transactions(
        account_id=_account_id_from_code(account_code),
        instrument_id=instrument_id,
        limit=limit,
    )
    return {"transactions": [_public_transaction(row) for row in rows]}


@router.get("/api/portfolio/transactions/unresolved")
def api_unresolved_transactions():
    from modules.portfolio.db import transaction_ledger

    rows = transaction_ledger.list_unresolved()
    for row in rows:
        row["row"] = _public_transaction(row.get("row") or {})
    return {"unresolved_transactions": rows}


@router.get("/api/portfolio/lots")
def api_tax_lots(
    account_code: str | None = Query(default=None, max_length=16),
    instrument_id: str | None = Query(default=None, max_length=64),
):
    from modules.portfolio.db import transaction_ledger
    from modules.portfolio.services.tax_lots import build_tax_lots

    transactions = transaction_ledger.list_transactions(
        account_id=_account_id_from_code(account_code), instrument_id=instrument_id, limit=10000
    )
    result = build_tax_lots(transactions)
    result["lots"] = [_public_transaction(row) for row in result["lots"]]
    result["disposals"] = [_public_transaction(row) for row in result["disposals"]]
    return result


def _performance_payload(
    *, scope: str = "family", account_code: str | None = None, instrument_id: str | None = None
) -> dict[str, Any]:
    from modules.portfolio.services.performance import build_performance_summary

    family = fetch_family_portfolio(refresh=False, stale_ok=True)
    ending_value = float((family.get("summary") or {}).get("total_current_value") or 0)
    account_id = _account_id_from_code(account_code)
    if scope == "account" and account_id:
        portfolio = next(
            (
                row
                for row in family.get("portfolios") or []
                if row.get("account_id") == account_id
            ),
            {},
        )
        account_summary = portfolio.get("summary") or {}
        ending_value = float(
            account_summary.get("total_current_value")
            or account_summary.get("total_current")
            or 0
        )
    elif scope == "instrument" and instrument_id:
        ending_value = sum(
            float(row.get("current_value") or row.get("marked_value") or 0)
            for portfolio in family.get("portfolios") or []
            for row in portfolio.get("holdings") or []
            if row.get("instrument_id") == instrument_id
        )
    return build_performance_summary(
        ending_value=ending_value,
        scope=scope,
        account_id=account_id,
        instrument_id=instrument_id,
    )


@router.get("/api/portfolio/performance/summary")
def api_performance_summary(
    scope: str = Query("family", pattern=r"^(family|account|instrument)$"),
    account_code: str | None = Query(default=None, max_length=16),
    instrument_id: str | None = Query(default=None, max_length=64),
):
    return _performance_payload(scope=scope, account_code=account_code, instrument_id=instrument_id)


@router.get("/api/portfolio/performance/series")
def api_performance_series(
    scope: str = Query("family", pattern=r"^(family|account)$"),
    account_code: str | None = Query(default=None, max_length=16),
    days: int = Query(365, ge=7, le=3650),
):
    from modules.portfolio.db import transaction_ledger
    from modules.portfolio.services.performance import calculate_twrr

    account_id = _account_id_from_code(account_code)
    snapshots = daily_history.growth_series(scope=scope, account_id=account_id, days=days)
    transactions = transaction_ledger.list_transactions(account_id=account_id, limit=10000)
    return {"series": snapshots, "twrr": calculate_twrr(snapshots, transactions, scope=scope)}


@router.get("/api/portfolio/performance/attribution")
def api_performance_attribution():
    from modules.portfolio.db import transaction_ledger
    from modules.portfolio.services.performance import attribution

    result = attribution(transaction_ledger.list_transactions(limit=10000))
    result["by_account"] = [_public_transaction(row) for row in result["by_account"]]
    return result


@router.get("/api/portfolio/performance/coverage")
def api_performance_coverage():
    result = _performance_payload()
    return {
        key: result[key]
        for key in (
            "cashflow_coverage_pct", "lot_coverage_pct", "valuation_coverage_pct",
            "xirr_status", "excluded_periods", "data_quality_flags",
        )
    }


@router.get("/api/portfolio/performance/audit.xlsx")
def api_performance_audit_workbook():
    from modules.portfolio.db import transaction_ledger
    from modules.portfolio.services.performance_export import build_performance_audit_workbook
    from modules.portfolio.services.tax_lots import build_tax_lots

    transactions = transaction_ledger.list_transactions(limit=10000)
    workbook = build_performance_audit_workbook(
        transactions=transactions,
        lot_result=build_tax_lots(transactions),
        performance=_performance_payload(),
        reconciliation=_reconciliation_payload(),
    )
    return StreamingResponse(
        workbook,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=portfolio-performance-audit.xlsx"},
    )


@router.get("/api/portfolio/market-regime/current")
def api_market_regime_current(finalized_only: bool = Query(False)):
    from modules.portfolio.db import market_regime

    return {
        "observation": market_regime.latest(
            market="INDIA", finalized_only=finalized_only
        )
    }


@router.get("/api/portfolio/market-regime/history")
def api_market_regime_history(limit: int = Query(365, ge=1, le=5000)):
    from modules.portfolio.db import market_regime

    return {"history": market_regime.history(market="INDIA", limit=limit)}


@router.get("/api/portfolio/market-regime/methodology")
def api_market_regime_methodology():
    from modules.portfolio.services.market_regime import methodology

    return methodology()


@router.post("/api/portfolio/market-regime/observations")
def api_market_regime_observation(payload: MrmiObservationPayload):
    from modules.portfolio.services.market_regime import calculate_and_store

    try:
        return calculate_and_store(
            payload.components,
            as_of=payload.as_of,
            observation_state=payload.observation_state,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/api/portfolio/research/scorecards")
def api_research_scorecard(payload: ResearchScorecardPayload):
    from modules.portfolio.db import instrument_master
    from modules.portfolio.services.research_scorecards import build_scorecard

    instrument = instrument_master.get_instrument(payload.instrument_id)
    if instrument is None:
        raise HTTPException(status_code=404, detail="Unknown instrument_id.")
    return build_scorecard(instrument, payload.evidence)


@router.post("/api/portfolio/research/screens/run")
def api_research_screen_run(payload: ResearchScreenRunPayload):
    from modules.portfolio.services.research_screener import run_screen

    safe_rows = [
        {key: value for key, value in row.items() if key not in {"account_id", "source_document", "user_notes"}}
        for row in payload.rows
    ]
    try:
        return run_screen(safe_rows, payload.definition)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/api/portfolio/research/screens")
def api_research_screens():
    from modules.portfolio.db import research

    return {"saved_screens": research.list_screens()}


@router.post("/api/portfolio/research/screens")
def api_save_research_screen(payload: SavedResearchScreenPayload):
    from modules.portfolio.db import research
    from modules.portfolio.services.research_screener import run_screen

    try:
        run_screen([], payload.definition)
        return research.save_screen(**payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/api/portfolio/research/candidates")
def api_research_candidates():
    from modules.portfolio.db import research

    return {"candidates": research.list_candidates()}


@router.post("/api/portfolio/research/candidates")
def api_save_research_candidate(payload: ResearchCandidatePayload):
    from modules.portfolio.db import instrument_master, research

    if instrument_master.get_instrument(payload.instrument_id) is None:
        raise HTTPException(status_code=404, detail="Unknown instrument_id.")
    return research.upsert_candidate(payload.model_dump())


@router.get("/api/portfolio/research/watchlist")
def api_research_watchlist():
    from modules.portfolio.db import research

    return {"watchlist": research.list_watchlist()}


@router.post("/api/portfolio/research/watchlist")
def api_add_research_watchlist(payload: ResearchWatchlistPayload):
    from modules.portfolio.db import research

    return research.add_watchlist_entry(payload.model_dump())


@router.get("/api/portfolio/research/thesis/{instrument_id}")
def api_research_thesis_history(instrument_id: str):
    from modules.portfolio.db import research

    return {"history": research.thesis_history(instrument_id)}


@router.post("/api/portfolio/research/thesis")
def api_append_research_thesis(payload: ResearchThesisPayload):
    from modules.portfolio.db import research

    return research.append_thesis(payload.model_dump())


@router.get("/api/portfolio/research/events")
def api_research_events(instrument_id: str | None = Query(default=None, max_length=64)):
    from datetime import date

    from modules.portfolio.db import research
    from modules.portfolio.services.research_events import assess_event

    return {
        "events": [
            assess_event(row, as_of=date.today().isoformat())
            for row in research.list_events(instrument_id=instrument_id)
        ]
    }


@router.post("/api/portfolio/research/events")
def api_add_research_event(payload: ResearchEventPayload):
    from modules.portfolio.db import research

    return research.add_event(payload.model_dump())


@router.post("/api/portfolio/research/compare")
def api_research_compare(payload: ResearchComparePayload):
    from modules.portfolio.services.research_compare import compare_instruments

    try:
        return compare_instruments(payload.items)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/api/portfolio/funds/schemes")
def api_fund_schemes():
    from modules.portfolio.db import fund_intelligence

    return {"schemes": fund_intelligence.list_schemes()}


@router.post("/api/portfolio/funds/schemes")
def api_save_fund_scheme(payload: FundSchemePayload):
    from modules.portfolio.db import fund_intelligence, instrument_master

    if instrument_master.get_instrument(payload.instrument_id) is None:
        raise HTTPException(status_code=404, detail="Unknown canonical fund instrument_id.")
    try:
        return fund_intelligence.upsert_scheme(payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/api/portfolio/funds/holdings")
def api_ingest_fund_holdings(payload: FundHoldingsIngestPayload):
    from modules.portfolio.db import fund_intelligence

    try:
        return fund_intelligence.save_constituents(
            fund_instrument_id=payload.fund_instrument_id,
            as_of=payload.as_of,
            rows=[row.model_dump() for row in payload.rows],
            source=payload.source,
            source_type=payload.source_type,
            coverage_type=payload.coverage_type,
            coverage_pct=payload.coverage_pct,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/api/portfolio/funds/{instrument_id}/lookthrough")
def api_fund_lookthrough(instrument_id: str, as_of: str | None = Query(default=None)):
    from modules.portfolio.services.fund_intelligence import lookthrough

    return lookthrough(instrument_id, as_of=as_of)


@router.get("/api/portfolio/funds/overlap/pair")
def api_fund_pair_overlap(
    first_instrument_id: str = Query(..., max_length=64),
    second_instrument_id: str = Query(..., max_length=64),
    as_of: str | None = Query(default=None),
):
    from modules.portfolio.services.fund_intelligence import pairwise_overlap

    return pairwise_overlap(first_instrument_id, second_instrument_id, as_of=as_of)


def _family_fund_positions() -> list[dict[str, Any]]:
    family = fetch_family_portfolio(refresh=False, stale_ok=True)
    return [
        row
        for portfolio in family.get("portfolios") or []
        for row in portfolio.get("holdings") or []
    ]


@router.get("/api/portfolio/funds/family")
def api_family_fund_intelligence():
    from modules.portfolio.services.fund_intelligence import family_lookthrough, weighted_ter

    positions = _family_fund_positions()
    return {"lookthrough": family_lookthrough(positions), "cost": weighted_ter(positions)}


@router.get("/api/portfolio/funds/consolidation")
def api_fund_consolidation_candidates():
    from modules.portfolio.services.fund_intelligence import consolidation_candidates

    return {"candidates": consolidation_candidates(_family_fund_positions())}


@router.get("/api/portfolio/funds/audit.xlsx")
def api_fund_intelligence_export():
    from modules.portfolio.db import fund_intelligence
    from modules.portfolio.services.fund_export import build_fund_workbook
    from modules.portfolio.services.fund_intelligence import pairwise_overlap

    schemes = fund_intelligence.list_schemes()
    overlaps = [
        pairwise_overlap(first["instrument_id"], second["instrument_id"])
        for index, first in enumerate(schemes)
        for second in schemes[index + 1 :]
    ]
    workbook = build_fund_workbook(
        schemes=schemes,
        constituents=fund_intelligence.all_constituents(),
        overlaps=overlaps,
    )
    return StreamingResponse(
        workbook,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=fund-intelligence-audit.xlsx"},
    )


def _today_brief_payload() -> dict[str, Any]:
    from modules.portfolio.db import market_regime, research
    from modules.portfolio.db import weekly_sync as weekly_sync_store
    from modules.portfolio.services.advisory.service import build_advisory_payload
    from modules.portfolio.services.today_brief import build_today_brief

    family = fetch_family_portfolio(refresh=False, stale_ok=True)
    return build_today_brief(
        family=family,
        advisory=build_advisory_payload(family, goals=profile_goals_store.get_goals()),
        sync_status=weekly_sync_store.sync_status(),
        market_regime=market_regime.latest(finalized_only=True),
        events=research.list_events(),
    )


@router.get("/api/portfolio/today-brief")
def api_today_brief():
    return _today_brief_payload()


@router.get("/api/portfolio/stress/scenarios")
def api_stress_scenarios():
    from modules.portfolio.db import operating_console
    from modules.portfolio.services.stress_testing import SCENARIO_LIBRARY

    return {"library": SCENARIO_LIBRARY, "saved": operating_console.list_scenarios()}


@router.post("/api/portfolio/stress/run")
def api_run_stress(payload: StressScenarioPayload):
    from modules.portfolio.db import operating_console
    from modules.portfolio.services.stress_testing import scenario_definition, stress_portfolio

    try:
        scenario = scenario_definition(payload.name, payload.custom_assumptions)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if payload.save:
        saved = operating_console.save_scenario(
            name=payload.name,
            assumptions=scenario["assumptions"],
        )
        scenario["scenario_id"] = saved["scenario_id"]
    return stress_portfolio(_family_fund_positions(), scenario=scenario)


@router.post("/api/portfolio/what-if")
def api_what_if(payload: WhatIfPayload):
    from modules.portfolio.db import research
    from modules.portfolio.services.what_if import simulate_rebalance

    approved = {
        row["instrument_id"]
        for row in research.list_candidates()
        if row["research_status"] == "APPROVED"
    }
    return simulate_rebalance(
        _family_fund_positions(),
        operations=payload.operations,
        constraints=payload.constraints,
        approved_candidates=approved,
    )


@router.get("/api/portfolio/alerts")
def api_alert_history(limit: int = Query(100, ge=1, le=1000)):
    from modules.portfolio.db import operating_console

    return {"alerts": operating_console.list_alerts(limit=limit)}


@router.post("/api/portfolio/alerts/evaluate")
def api_evaluate_alerts(payload: AlertEvaluationPayload):
    from modules.portfolio.services.alerts import evaluate_alerts

    return evaluate_alerts(payload.events, cooldown_seconds=payload.cooldown_seconds)


@router.get("/api/portfolio/tax/rules")
def api_tax_rules(as_of: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")):
    from datetime import date

    from modules.portfolio.services.advisory.tax_rules import public_registry

    return public_registry(as_of or date.today().isoformat())


@router.post("/api/portfolio/tax/after-tax")
def api_after_tax(payload: AfterTaxPayload):
    from modules.portfolio.services.after_tax import estimate_after_tax

    try:
        return estimate_after_tax(payload.candidate, payload.account, as_of=payload.as_of)
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/api/portfolio/tax/asset-location")
def api_asset_location(payload: AssetLocationPayload):
    from modules.portfolio.services.asset_location import optimize_asset_location

    try:
        return optimize_asset_location(payload.candidate, payload.accounts, as_of=payload.as_of)
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/api/portfolio/tax/harvest")
def api_tax_harvest(payload: TaxHarvestPayload):
    from modules.portfolio.services.tax_harvesting import evaluate_harvest

    try:
        return evaluate_harvest(
            payload.holding, payload.account, lots=payload.lots, as_of=payload.as_of
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/api/portfolio/tax/ca-package.xlsx")
def api_tax_ca_package(as_of: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")):
    from datetime import date

    from modules.portfolio.db import transaction_ledger
    from modules.portfolio.services.advisory.tax_rules import public_registry
    from modules.portfolio.services.tax_location_export import build_ca_workbook
    from modules.portfolio.services.tax_lots import build_tax_lots

    effective_as_of = as_of or date.today().isoformat()
    lot_result = build_tax_lots(transaction_ledger.list_transactions(limit=10000))
    workbook = build_ca_workbook(
        rules=public_registry(effective_as_of)["rules"],
        assumptions=[
            {"name": "as_of", "value": effective_as_of},
            {"name": "tax_lot_method", "value": "FIFO planning estimate"},
            {"name": "execution_enabled", "value": False},
        ],
        lots=lot_result["lots"],
        actions=[],
    )
    return StreamingResponse(
        workbook,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=asset-location-ca-review.xlsx"},
    )


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


def _account_statuses(*, family: dict | None = None) -> list[dict]:
    """Build account status; a loaded family snapshot avoids live broker probes."""
    loaded_accounts = {
        str(value)
        for block in (family or {}).get("portfolios") or []
        for value in (block.get("account_id"), block.get("account_code"))
        if value
    }
    errors_by_account = {
        str(error.get("account")): str(error.get("error") or "Account was not loaded.")
        for error in (family or {}).get("errors") or []
        if error.get("account")
    }
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
        if account.get("enabled") and family is not None:
            connected = account_id in loaded_accounts or account["code"] in loaded_accounts
            conn = {
                "connected": connected,
                "needs_login": not connected,
                "message": (
                    "Synced in the current portfolio snapshot"
                    if connected
                    else errors_by_account.get(account["code"], "Refresh to verify this account")
                ),
            }
        else:
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
                "connect_url": app_path("/portfolio/setup"),
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
    account_codes: list[str] | None = None,
) -> tuple[dict, dict, list[dict]]:
    """Fetch family portfolio and build aggregated holdings view for UI/export."""
    family = fetch_family_portfolio(refresh=refresh, stale_ok=not refresh)
    raw_holdings = [h for p in family["portfolios"] for h in p["holdings"]]
    raw_holdings = filter_holdings_by_account_codes(raw_holdings, account_codes)
    holdings_view = prepare_holdings_view(
        raw_holdings, **view_params, aggregate_across_accounts=True
    )
    return family, holdings_view, raw_holdings


def _export_account_choices(
    account_statuses: list[dict] | None = None,
) -> list[dict[str, str]]:
    return [
        {"code": account["code"], "label": account["label"]}
        for account in (
            account_statuses if account_statuses is not None else _account_statuses()
        )
        if account.get("enabled")
    ]


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
    auth_degraded = bool(family.get("auth_degraded")) or any(
        e.get("using_snapshot") for e in family.get("errors", [])
    )
    data_as_of_label = format_data_as_of_label(
        family.get("cached_at"), auth_degraded=auth_degraded
    )
    weekly_status = weekly_history.weekly_status()
    cache_meta = meta_for_family(fresh_ttl=CACHE_TTL_SECONDS)
    account_statuses = _account_statuses(family=family)
    export_qs = _export_query_string(view_params["sort"], view_params["order"], view_params["group_by"])

    return templates.TemplateResponse(
        request,
        "portfolio/dashboard.html",
        {
            "active_module": "portfolio",
            "summary": family["summary"],
            "holdings_view": holdings_view,
            "accounts": account_statuses,
            "errors": errors,
            "cached_at": family.get("cached_at"),
            "from_cache": family.get("from_cache", False),
            "stale": family.get("stale", False),
            "auth_degraded": auth_degraded,
            "data_as_of_label": data_as_of_label,
            "ltp_refreshed_offline": family.get("ltp_refreshed_offline", False),
            "cache_meta": cache_meta,
            "controls_action": app_path("/portfolio"),
            "export_url": f"/api/portfolio/export?{export_qs}",
            "export_api_url": "/api/portfolio/export",
            "export_column_options": export_column_options(include_account=True),
            "export_include_account": True,
            "export_account_choices": _export_account_choices(account_statuses),
            "refresh": refresh,
            "symbol_suggestions": _symbol_suggestions(raw_holdings),
            "holdings_financials_json": json.dumps(
                holdings_financials_map(all_holdings_from_view(holdings_view))
            ),
            "weekly_status": weekly_status,
            "sarwa_vision_available": _vision_available(),
            "trading_enabled": _trading_enabled(),
        },
    )


@router.get("/portfolio/agent")
def portfolio_agent_page(request: Request):
    """Dedicated Portfolio Agent tab."""
    return templates.TemplateResponse(
        request,
        "portfolio/agent.html",
        {
            "active_module": "agent",
            "trading_enabled": _trading_enabled(),
        },
    )


@router.get("/portfolio/advisor")
def portfolio_advisor_page(request: Request):
    """Local deterministic Advisor / Action Center."""
    return templates.TemplateResponse(
        request,
        "portfolio/advisor.html",
        {
            "active_module": "advisor",
            "trading_enabled": False,
        },
    )


@router.get("/portfolio/data-quality")
def portfolio_data_quality_page(
    request: Request,
    refresh: bool = Query(False),
    limit: int = Query(60, ge=20, le=500),
):
    """Canonical identity and reconciliation control center."""
    family = fetch_family_portfolio(refresh=refresh, stale_ok=True)
    reconciliation = family.get("reconciliation") or {}
    security_rows = reconciliation.get("by_security") or []
    return templates.TemplateResponse(
        request,
        "portfolio/data_quality.html",
        {
            "active_module": "data_quality",
            "reconciliation": reconciliation,
            "summary": reconciliation.get("summary") or {},
            "security_rows": security_rows[:limit],
            "security_rows_total": len(security_rows),
            "security_rows_limit": limit,
            "account_rows": reconciliation.get("by_account") or [],
            "corporate_actions": reconciliation.get("corporate_action_review") or [],
            "trading_enabled": False,
        },
    )


@router.get("/portfolio/market-regime")
def portfolio_market_regime_page(request: Request):
    """Original, transparent India Market Regime & Mood Index."""
    from modules.portfolio.db import market_regime
    from modules.portfolio.services.market_regime import methodology
    from modules.portfolio.services.privacy_controls import privacy_status

    observation = market_regime.latest(market="INDIA")
    method = methodology()
    source_guidance = {
        "market_breadth": "NSE advancing/declining and 200-day breadth",
        "index_momentum": "NIFTY 50 daily index history",
        "volatility_regime": "India VIX daily history",
        "fpi_flow_regime": "NSDL/CDSL or exchange FPI flow data",
        "participation_strength": "NSE equal-weight vs headline-index participation",
        "derivatives_sentiment": "NSE index futures/options positioning",
        "valuation_stretch": "Dated NIFTY valuation percentile",
        "safe_haven_liquidity": "Dated liquidity and safe-haven stress series",
    }
    return templates.TemplateResponse(
        request,
        "portfolio/market_regime.html",
        {
            "active_module": "market_regime",
            "observation": observation,
            "history": market_regime.history(market="INDIA", limit=365),
            "methodology": method,
            "component_readiness": [
                {"name": name, "weight": spec["weight"], "required_source": source_guidance[name]}
                for name, spec in method["components"].items()
            ],
            "market_data_sharing_enabled": privacy_status()["market_data_symbol_queries"]["enabled"],
            "trading_enabled": False,
        },
    )


@router.get("/portfolio/research")
def portfolio_research_page(request: Request):
    """Local, instrument-specific research workspace."""
    from modules.portfolio.db import research

    family = fetch_family_portfolio(refresh=False, stale_ok=True)
    held_by_key: dict[str, dict[str, Any]] = {}
    for portfolio in family.get("portfolios") or []:
        for row in portfolio.get("holdings") or []:
            key = str(row.get("instrument_id") or f"{row.get('exchange')}:{row.get('symbol')}")
            item = held_by_key.setdefault(
                key,
                {
                    "instrument_id": row.get("instrument_id"),
                    "symbol": row.get("symbol") or key,
                    "display_name": row.get("canonical_display_name") or row.get("symbol") or key,
                    "sector": row.get("sector") or "Unclassified",
                    "current_value": 0.0,
                    "accounts": set(),
                },
            )
            item["current_value"] += float(row.get("display_value") or row.get("current_value") or 0)
            item["accounts"].add(str(portfolio.get("account_code") or portfolio.get("account_id") or ""))
    held_universe = sorted(held_by_key.values(), key=lambda row: -row["current_value"])
    for row in held_universe:
        row["accounts"] = sorted(code for code in row["accounts"] if code)
        row["current_value"] = round(row["current_value"], 2)
    return templates.TemplateResponse(
        request,
        "portfolio/research.html",
        {
            "active_module": "research",
            "saved_screens": research.list_screens(),
            "candidates": research.list_candidates(),
            "watchlist": research.list_watchlist(),
            "events": research.list_events(),
            "held_universe": held_universe[:16],
            "held_universe_count": len(held_universe),
            "trading_enabled": False,
        },
    )


@router.get("/portfolio/research/scorecard/{instrument_id}")
def portfolio_research_scorecard_page(request: Request, instrument_id: str):
    from modules.portfolio.db import instrument_master, research
    from modules.portfolio.services.research_scorecards import build_scorecard

    instrument = instrument_master.get_instrument(instrument_id)
    if instrument is None:
        raise HTTPException(status_code=404, detail="Unknown instrument_id.")
    family = fetch_family_portfolio(refresh=False, stale_ok=True)
    evidence = next(
        (
            row
            for portfolio in family.get("portfolios") or []
            for row in portfolio.get("holdings") or []
            if row.get("instrument_id") == instrument_id
        ),
        {},
    )
    evidence = {**evidence, "evidence_as_of": family.get("cached_at")}
    return templates.TemplateResponse(
        request,
        "portfolio/research_scorecard.html",
        {
            "active_module": "research",
            "instrument": instrument,
            "scorecard": build_scorecard(instrument, evidence),
            "candidate": research.get_candidate(instrument_id),
            "thesis_history": research.thesis_history(instrument_id),
            "events": research.list_events(instrument_id=instrument_id),
            "trading_enabled": False,
        },
    )


@router.get("/portfolio/funds")
def portfolio_fund_intelligence_page(request: Request):
    from modules.portfolio.db import fund_intelligence
    from modules.portfolio.services.fund_intelligence import (
        consolidation_candidates,
        etf_analytics,
        family_lookthrough,
        pairwise_overlap,
        weighted_ter,
    )

    schemes = fund_intelligence.list_schemes()
    positions = _family_fund_positions()
    scheme_ids = {str(row.get("instrument_id") or "") for row in schemes}
    unmapped_by_key: dict[str, dict[str, Any]] = {}
    for row in positions:
        instrument_type = str(row.get("instrument_type") or row.get("asset_class") or "").lower()
        if instrument_type not in {"mf", "mutual_fund", "etf", "fund"}:
            continue
        instrument_id = str(row.get("instrument_id") or "")
        if instrument_id and instrument_id in scheme_ids:
            continue
        key = instrument_id or str(row.get("symbol") or row.get("name") or "UNKNOWN")
        item = unmapped_by_key.setdefault(
            key,
            {
                "instrument_id": instrument_id,
                "symbol": row.get("symbol") or row.get("name") or key,
                "instrument_type": instrument_type,
                "current_value": 0.0,
            },
        )
        item["current_value"] += float(row.get("display_value") or row.get("current_value") or 0)
    unmapped_funds = sorted(unmapped_by_key.values(), key=lambda row: -row["current_value"])
    overlaps = [
        pairwise_overlap(first["instrument_id"], second["instrument_id"])
        for index, first in enumerate(schemes)
        for second in schemes[index + 1 :]
    ]
    return templates.TemplateResponse(
        request,
        "portfolio/fund_intelligence.html",
        {
            "active_module": "funds",
            "schemes": schemes,
            "overlaps": overlaps,
            "family_lookthrough": family_lookthrough(positions),
            "cost": weighted_ter(positions),
            "liquidity": [etf_analytics(row) for row in schemes if row.get("instrument_type") == "etf"],
            "consolidation": consolidation_candidates(positions),
            "unmapped_funds": unmapped_funds,
            "trading_enabled": False,
        },
    )


@router.get("/portfolio/brief")
def portfolio_today_brief_page(request: Request):
    from modules.portfolio.db import operating_console
    from modules.portfolio.services.stress_testing import SCENARIO_LIBRARY

    return templates.TemplateResponse(
        request,
        "portfolio/today_brief.html",
        {
            "active_module": "brief",
            "brief": _today_brief_payload(),
            "scenario_library": SCENARIO_LIBRARY,
            "saved_scenarios": operating_console.list_scenarios(),
            "alerts": operating_console.list_alerts(limit=20),
            "trading_enabled": False,
        },
    )


@router.get("/portfolio/asset-location")
def portfolio_asset_location_page(request: Request):
    """After-tax account matrix and CA-review queue."""
    from datetime import date

    from modules.portfolio.config import get_account_profile
    from modules.portfolio.services.advisory.tax_rules import public_registry

    family = fetch_family_portfolio(refresh=False, stale_ok=True)
    rows = []
    for block in family.get("portfolios") or []:
        account_id = str(block.get("account_id") or "")
        try:
            profile = get_account_profile(account_id)
        except KeyError:
            profile = {}
        missing = [
            label
            for label, value in {
                "residency": profile.get("india_residency_status"),
                "account type": profile.get("account_type"),
                "repatriability": profile.get("repatriability"),
                "permitted instruments": profile.get("permitted_instrument_types"),
            }.items()
            if value in (None, "", "UNKNOWN", [])
        ]
        rows.append(
            {
                "account_id": account_id,
                "code": block.get("account_code") or account_id,
                "owner_ref": profile.get("owner_ref") or "Unspecified",
                "country_of_residence": profile.get("country_of_residence") or "UNKNOWN",
                "india_residency_status": profile.get("india_residency_status") or "UNKNOWN",
                "account_type": profile.get("account_type") or "UNKNOWN",
                "currency": profile.get("base_currency") or block.get("currency") or "UNKNOWN",
                "repatriability": profile.get("repatriability") or "UNKNOWN",
                "risk_profile": profile.get("risk_profile") or "unknown",
                "target_return_pct": profile.get("target_return_pct"),
                "max_position_pct": profile.get("max_position_pct"),
                "holding_count": len(block.get("holdings") or []),
                "missing": missing,
            }
        )
    as_of = date.today().isoformat()
    return templates.TemplateResponse(
        request,
        "portfolio/asset_location.html",
        {
            "active_module": "asset_location",
            "accounts": rows,
            "review_queue": [row for row in rows if row["missing"]],
            "tax_registry": public_registry(as_of),
            "as_of": as_of,
            "trading_enabled": False,
        },
    )


@router.get("/portfolio/system-health")
def portfolio_system_health_page(request: Request):
    from modules.portfolio.services.diagnostics import collect_diagnostics
    from modules.portfolio.services.privacy_controls import privacy_status

    return templates.TemplateResponse(
        request,
        "portfolio/system_health.html",
        {
            "active_module": "system_health",
            "diagnostics": collect_diagnostics(),
            "privacy": privacy_status(),
            "trading_enabled": False,
        },
    )


@router.get("/portfolio/growth")
def portfolio_growth_page(
    request: Request,
    refresh: bool = Query(False),
    days: int = Query(90, ge=7, le=365),
):
    """Daily portfolio growth — value trend and day-over-day breakdowns."""
    from modules.portfolio.services.daily_analytics import build_growth_dashboard
    from modules.portfolio.services.daily_recorder import seed_today_if_missing

    if refresh:
        try:
            family = fetch_family_portfolio(refresh=True, stale_ok=False)
            seed_today_if_missing(family, source="manual_refresh")
        except OAuthError:
            pass

    dashboard = build_growth_dashboard(days=days)
    if not dashboard["series"]:
        try:
            family = fetch_family_portfolio(refresh=False, stale_ok=True)
            seed_today_if_missing(family, source="bootstrap")
            dashboard = build_growth_dashboard(days=days)
        except Exception:
            pass

    return templates.TemplateResponse(
        request,
        "portfolio/growth.html",
        {
            "active_module": "growth",
            "days": days,
            "daily_status": dashboard["status"],
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
        portfolio = fetch_account_portfolio(account_id, refresh=refresh, stale_ok=True)
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
            "controls_action": app_path(f"/portfolio/account/{account_code}"),
            "export_url": f"/api/portfolio/export/{account_code}?{export_qs}",
            "export_api_url": f"/api/portfolio/export/{account_code}",
            "export_column_options": export_column_options(include_account=False),
            "export_include_account": False,
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
        account_id = resolve_account_ref(ref)
        complete_oauth(request_token=request_token, ref=ref)
        # Keep the last trusted snapshot available while the slow live refresh
        # runs outside this OAuth navigation request.
        invalidate_portfolio_cache(preserve_disk=True)
        from modules.portfolio.services.sync_jobs import submit_weekly_sync

        job = submit_weekly_sync(
            mode="auto",
            dry_run=False,
            requested_by="zerodha_oauth",
            force=True,
        )
    except (KeyError, OAuthError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    query = urlencode(
        {
            "broker_connected": "zerodha",
            "account": get_account_code(account_id),
            "sync_run_id": job["run_id"],
        }
    )
    return RedirectResponse(url=get_hub_url(f"/portfolio/setup?{query}"), status_code=302)


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


@router.get("/api/portfolio/patterns")
def api_portfolio_patterns(
    refresh: bool = Query(False),
    blocking: bool = Query(True),
):
    """
    Scan portfolio equities for chart patterns (cup & handle, inverse H&S, etc.).
    Uses Yahoo daily history; portfolio scans are cached locally for one day.
    """
    from modules.portfolio.services.chart_patterns import scan_holdings, scan_holdings_async
    from modules.portfolio.services.holdings_view import all_holdings_from_view, prepare_holdings_view
    from modules.portfolio.services.portfolio import fetch_family_portfolio

    family = fetch_family_portfolio(refresh=refresh, stale_ok=True)
    raw = [h for p in family.get("portfolios", []) for h in p.get("holdings", [])]
    holdings_view = prepare_holdings_view(raw, aggregate_across_accounts=True)
    holdings = all_holdings_from_view(holdings_view)
    if blocking:
        scanned = scan_holdings(holdings, refresh=refresh)
        scan_status = "complete"
        scan_error = None
    else:
        scan = scan_holdings_async(holdings, refresh=refresh)
        scanned = scan.get("results") or []
        scan_status = str(scan.get("status") or "scanning")
        scan_error = scan.get("error")
    hits = [row for row in scanned if row.get("patterns")]
    actionable = [row for row in scanned if row.get("actionable_primary")]
    return {
        "status": scan_status,
        "error": scan_error,
        "scanned": len(scanned),
        "with_patterns": len(hits),
        "actionable_setups": len(actionable),
        "as_of": hits[0]["primary"]["as_of"] if hits and hits[0].get("primary") else None,
        "holdings": hits,
    }


@router.get("/api/portfolio/patterns/{symbol}")
def api_symbol_patterns(
    symbol: str,
    exchange: str = Query("NSE"),
):
    """Pattern scan for a single symbol."""
    from modules.portfolio.services.chart_patterns import detect_patterns_for_symbol

    return detect_patterns_for_symbol(symbol.upper(), exchange, use_cache=True)


@router.get("/api/portfolio/advisory")
def api_portfolio_advisory(
    response: Response,
    refresh: bool = Query(False),
    patterns: bool = Query(True),
):
    """Versioned deterministic recommendations for the local Action Center."""
    from modules.portfolio.services.advisory.runtime import build_live_advisory

    payload = build_live_advisory(refresh=refresh, include_patterns=patterns)
    response.headers["ETag"] = f'"{payload["fingerprint"]}"'
    response.headers["Cache-Control"] = "private, no-store"
    return payload


@router.get("/api/portfolio/advisory/deadlines")
def api_portfolio_advisory_deadlines(refresh: bool = Query(False)):
    from modules.portfolio.services.advisory.runtime import build_live_advisory

    payload = build_live_advisory(refresh=refresh, include_patterns=True)
    return {
        "schema_version": payload["schema_version"],
        "generated_at": payload["generated_at"],
        "deadlines": payload.get("deadlines") or [],
    }


@router.get("/api/portfolio/advisory/evidence/status")
def api_portfolio_advisory_evidence_status():
    from modules.portfolio.services.advisory.providers import evidence_status

    return evidence_status()


@router.post("/api/portfolio/advisory/rebalance")
def api_portfolio_advisory_rebalance(payload: AdvisoryRebalancePayload):
    from modules.portfolio.services.advisory.rebalance import evaluate_rebalance
    from modules.portfolio.services.advisory.runtime import build_live_advisory

    advisory = build_live_advisory(refresh=False, include_patterns=True)
    goals = profile_goals_store.get_goals()
    return evaluate_rebalance(
        advisory,
        [item.model_dump() for item in payload.targets],
        max_position_pct=float(goals.get("max_position_pct") or 12),
        cash_buffer_pct=float(goals.get("cash_buffer_pct") or 5),
    )


@router.get("/api/portfolio/advisory/{symbol}")
def api_portfolio_advisory_symbol(
    symbol: str,
    refresh: bool = Query(False),
):
    from modules.portfolio.services.advisory.runtime import build_live_advisory

    payload = build_live_advisory(refresh=refresh, include_patterns=True)
    wanted = symbol.upper()
    for item in payload.get("recommendations") or []:
        if str(item.get("symbol") or "").upper() == wanted:
            return {
                "schema_version": payload["schema_version"],
                "generated_at": payload["generated_at"],
                "recommendation": item,
            }
    raise HTTPException(status_code=404, detail=f"No advisory recommendation for {wanted}")


@router.get("/api/portfolio/{account_ref}")
def api_account_portfolio(account_ref: str, refresh: bool = Query(False)):
    """JSON API — single account portfolio (account_ref: AB, RB, SB, HB)."""
    if not is_known_account(account_ref):
        raise HTTPException(status_code=404, detail=f"Unknown account: {account_ref}")

    account_id = resolve_account_ref(account_ref)
    try:
        return fetch_account_portfolio(account_id, refresh=refresh, stale_ok=True)
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
    from modules.portfolio.services.llm_config import agent_configured
    from modules.portfolio.services.sector_llm import classify_holdings_llm

    if not agent_configured():
        raise HTTPException(
            status_code=503,
            detail="LLM unavailable: configure provider in Connect accounts → Portfolio agent",
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


def _vision_available() -> bool:
    from modules.portfolio.services.llm_config import vision_configured

    return vision_configured()


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


@router.get("/api/portfolio/agent/sessions")
def portfolio_agent_sessions():
    """List saved agent chat sessions (threads with messages), newest first."""
    return {"sessions": list_sessions()}


@router.get("/api/portfolio/agent/sessions/{thread_id}")
def portfolio_agent_session(thread_id: str):
    """Load a single agent session for revisiting in the UI."""
    thread = get_thread(thread_id)
    if not thread:
        raise HTTPException(status_code=404, detail="Session not found or expired")
    bubbles = [
        {"role": m["role"], "text": m["content"]}
        for m in thread.get("messages") or []
        if m.get("content")
    ]
    return {
        "thread_id": thread["thread_id"],
        "title": thread.get("title") or "Portfolio chat",
        "created_at": thread["created_at"],
        "updated_at": thread["updated_at"],
        "important": thread.get("important", False),
        "bubbles": bubbles,
        "recommendations": thread.get("recommendations"),
    }


@router.patch("/api/portfolio/agent/sessions/{thread_id}")
def portfolio_agent_session_patch(thread_id: str, payload: PortfolioAgentSessionPatchPayload):
    """Mark or unmark a session as important (kept past the usual 4h TTL)."""
    if not set_thread_important(thread_id, important=payload.important):
        raise HTTPException(status_code=404, detail="Session not found or expired")
    return {"ok": True, "thread_id": thread_id, "important": payload.important}


@router.delete("/api/portfolio/agent/sessions/{thread_id}")
def portfolio_agent_session_delete(thread_id: str):
    """Delete a saved agent session."""
    if not delete_thread(thread_id):
        raise HTTPException(status_code=404, detail="Session not found or expired")
    return {"ok": True, "thread_id": thread_id}


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

    from shared.web.uploads import read_upload_bounded

    content = await read_upload_bounded(
        file,
        allowed_extensions={".png", ".jpg", ".jpeg", ".webp"},
        allowed_content_types={"image/png", "image/jpeg", "image/webp"},
        require_image_signature=True,
    )
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


@router.get("/api/portfolio/daily/status")
def api_daily_status():
    """Confirm daily SQLite history."""
    return daily_history.daily_status()


@router.get("/api/portfolio/daily/dashboard")
def api_daily_dashboard(days: int = Query(90, ge=7, le=365)):
    """Daily growth series + day-over-day breakdown by account, cap, sector."""
    from modules.portfolio.services.daily_analytics import build_growth_dashboard

    return build_growth_dashboard(days=days)


@router.get("/api/portfolio/daily/history")
def api_daily_history(
    scope: str = Query("family"),
    account_ref: str | None = Query(None),
    days: int = Query(90, ge=1, le=365),
):
    """Daily portfolio totals (oldest → newest)."""
    account_id = None
    if account_ref:
        try:
            account_id = resolve_account_ref(account_ref)
        except KeyError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    series = daily_history.growth_series(scope=scope, account_id=account_id, days=days)
    return {
        "scope": scope,
        "account_id": account_id,
        "days": days,
        "series": series,
        "fx": fx_meta(),
    }


@router.post("/api/portfolio/daily/snapshot")
def api_record_daily_snapshot(refresh: bool = Query(True)):
    """Record today's family + per-account daily snapshots."""
    try:
        family = fetch_family_portfolio(refresh=refresh, stale_ok=not refresh)
    except OAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    from modules.portfolio.services.daily_recorder import record_today_from_family

    recorded = record_today_from_family(family, source="manual")
    if not recorded:
        return {
            "recorded": False,
            "message": "No holdings to record",
            "day_date": daily_history.day_date_for(),
        }
    return {"recorded": True, "snapshots": recorded, "day_date": daily_history.day_date_for()}


@router.post("/api/portfolio/daily/import-sheet")
def api_daily_import_sheet(payload: DailySheetImportPayload):
    """Import historical day-wise totals from a Google Sheet tab."""
    aliases = payload.account_aliases or DEFAULT_ACCOUNT_ALIASES
    try:
        result = import_distribution_history(
            sheet_url=payload.sheet_url,
            sheet_name=payload.sheet_name,
            account_aliases=aliases,
            overwrite_existing=payload.overwrite_existing,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "ok": True,
        "message": "Historical sheet data imported into daily history.",
        **result,
    }


@router.get("/api/portfolio/profile/goals")
def api_get_portfolio_goals():
    """Return saved goals/guardrails (used by Portfolio Agent context)."""
    return profile_goals_store.get_goals()


@router.put("/api/portfolio/profile/goals")
def api_update_portfolio_goals(payload: PortfolioGoalsPayload):
    """Persist goal and risk guardrail preferences."""
    saved = profile_goals_store.save_goals(
        target_return_pct=payload.target_return_pct,
        max_position_pct=payload.max_position_pct,
        max_sector_pct=payload.max_sector_pct,
        cash_buffer_pct=payload.cash_buffer_pct,
        risk_profile=payload.risk_profile,
    )
    return {"ok": True, "goals": saved}


@router.get("/api/portfolio/data-quality")
def api_data_quality(limit: int = Query(20, ge=1, le=100)):
    """Recent import audit events (uploads and sheet imports) with unresolved mappings."""
    events = import_audit_store.latest(limit=limit)
    unresolved_total = sum(len(e.get("unresolved_codes") or []) for e in events)
    return {
        "events": events,
        "unresolved_total": unresolved_total,
    }


@router.get("/api/portfolio/weekly/status")
def api_weekly_status():
    """Confirm weekly SQLite history and latest snapshot weeks."""
    return weekly_history.weekly_status()


@router.get("/api/portfolio/sync/status")
def api_sync_status():
    """Latest attempted/successful weekly job and explicit degraded accounts."""
    from modules.portfolio.db import weekly_sync as weekly_sync_store

    weekly_sync_store.init_db()
    return weekly_sync_store.sync_status()


@router.get("/api/portfolio/sync/runs")
def api_sync_runs(limit: int = Query(20, ge=1, le=100)):
    """Auditable weekly job history; secrets and internal account ids are excluded."""
    from modules.portfolio.db import weekly_sync as weekly_sync_store

    weekly_sync_store.init_db()
    return {"runs": weekly_sync_store.list_runs(limit=limit)}


@router.get("/api/portfolio/sync/runs/{run_id}")
def api_sync_run(run_id: str):
    from modules.portfolio.db import weekly_sync as weekly_sync_store

    run = weekly_sync_store.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Weekly sync run not found")
    return run


@router.post("/api/portfolio/sync/weekly")
def api_run_weekly_sync(payload: WeeklySyncPayload):
    """Run the same one-shot service used by the CLI and OS schedulers."""
    from modules.portfolio.services.weekly_sync import run_weekly_sync

    return run_weekly_sync(
        mode=payload.mode,
        dry_run=payload.dry_run,
        requested_by="setup_ui",
        stage=payload.stage,
    )


@router.post("/api/portfolio/sync/weekly/async", status_code=202)
def api_queue_weekly_sync(payload: WeeklySyncPayload):
    """Queue the Setup sync without holding the browser request open."""
    from modules.portfolio.services.sync_jobs import submit_weekly_sync

    job = submit_weekly_sync(
        mode=payload.mode,
        dry_run=payload.dry_run,
        requested_by="setup_ui",
        stage=payload.stage,
    )
    return {
        **job,
        "status_url": app_path(f"/api/portfolio/sync/jobs/{job['run_id']}"),
    }


@router.get("/api/portfolio/sync/jobs/{run_id}")
def api_sync_job(run_id: str):
    from modules.portfolio.services.sync_jobs import get_sync_job

    job = get_sync_job(run_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Portfolio sync job not found")
    return job


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


class HoldingsExportBody(BaseModel):
    columns: list[str] = Field(default_factory=list)
    accounts: list[str] = Field(default_factory=list)
    sort: str = "value"
    order: str = "desc"
    refresh: bool = False


def _excel_download_response(content: bytes, filename: str) -> Response:
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/api/portfolio/export")
def export_family_portfolio(
    refresh: bool = Query(False),
    sort: str = Query("value"),
    order: str = Query("desc"),
    group_by: str = Query(""),
    columns: str = Query(""),
):
    """Download family holdings as Excel."""
    view_params = _normalize_view_params(sort=sort, order=order, group_by=group_by)
    _, holdings_view, _ = _family_holdings_view(refresh=refresh, view_params=view_params)
    col_ids = normalize_export_columns(columns or None, include_account=True)
    content = build_holdings_excel(
        holdings_view,
        columns=col_ids,
        include_account=True,
        sheet_title="Family Portfolio",
    )
    return _excel_download_response(content, "portfolio-family.xlsx")


@router.post("/api/portfolio/export")
def export_family_portfolio_post(body: HoldingsExportBody):
    """Download family holdings as Excel (column picker from UI)."""
    view_params = _normalize_view_params(sort=body.sort, order=body.order, group_by="")
    account_codes = [c.strip().upper() for c in body.accounts if c and str(c).strip()]
    allowed = {a["code"] for a in _export_account_choices()}
    if account_codes and not set(account_codes).issubset(allowed):
        raise HTTPException(status_code=400, detail="Invalid account selection.")
    _, holdings_view, raw = _family_holdings_view(
        refresh=body.refresh,
        view_params=view_params,
        account_codes=account_codes or None,
    )
    if account_codes and not raw:
        raise HTTPException(status_code=400, detail="No holdings for the selected accounts.")
    col_ids = normalize_export_columns(body.columns or None, include_account=True)
    if not col_ids:
        raise HTTPException(status_code=400, detail="Select at least one column.")
    sheet_title = "Family Portfolio"
    if account_codes:
        sheet_title = f"Portfolio ({','.join(account_codes)})"
    content = build_holdings_excel(
        holdings_view,
        columns=col_ids,
        include_account=True,
        sheet_title=sheet_title,
    )
    return _excel_download_response(content, "portfolio-family.xlsx")


@router.get("/api/portfolio/export/{account_ref}")
def export_account_portfolio(
    account_ref: str,
    refresh: bool = Query(False),
    sort: str = Query("value"),
    order: str = Query("desc"),
    group_by: str = Query(""),
    columns: str = Query(""),
):
    """Download single-account holdings as Excel."""
    if not is_known_account(account_ref):
        raise HTTPException(status_code=404, detail=f"Unknown account: {account_ref}")

    account_id = resolve_account_ref(account_ref)
    account_code = get_account_code(account_id)
    view_params = _normalize_view_params(sort=sort, order=order, group_by=group_by)

    try:
        portfolio = fetch_account_portfolio(account_id, refresh=refresh, stale_ok=True)
    except OAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except GrowwError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    holdings_view = prepare_holdings_view(portfolio["holdings"], **view_params)
    label = portfolio.get("account_code") or account_code
    col_ids = normalize_export_columns(columns or None, include_account=False)
    content = build_holdings_excel(
        holdings_view,
        columns=col_ids,
        include_account=False,
        sheet_title=label,
    )
    return _excel_download_response(content, f"portfolio-{account_code}.xlsx")


@router.post("/api/portfolio/export/{account_ref}")
def export_account_portfolio_post(account_ref: str, body: HoldingsExportBody):
    """Download single-account holdings as Excel (column picker from UI)."""
    if not is_known_account(account_ref):
        raise HTTPException(status_code=404, detail=f"Unknown account: {account_ref}")

    account_id = resolve_account_ref(account_ref)
    account_code = get_account_code(account_id)
    view_params = _normalize_view_params(sort=body.sort, order=body.order, group_by="")

    try:
        portfolio = fetch_account_portfolio(account_id, refresh=body.refresh, stale_ok=True)
    except OAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except GrowwError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    holdings_view = prepare_holdings_view(portfolio["holdings"], **view_params)
    label = portfolio.get("account_code") or account_code
    col_ids = normalize_export_columns(body.columns or None, include_account=False)
    if not col_ids:
        raise HTTPException(status_code=400, detail="Select at least one column.")
    content = build_holdings_excel(
        holdings_view,
        columns=col_ids,
        include_account=False,
        sheet_title=label,
    )
    return _excel_download_response(content, f"portfolio-{account_code}.xlsx")


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
    from modules.portfolio.services.llm_config import llm_setup_status
    from modules.portfolio.db import weekly_sync as weekly_sync_store

    weekly_sync_store.init_db()

    return templates.TemplateResponse(
        request,
        "portfolio/setup.html",
        {
            "active_module": "setup",
            "accounts": accounts,
            "default_callback_url": default_callback_url(),
            "setup_stats": {"linked": len(accounts), "ready": ready_count},
            "vision_available": vision_available() or _vision_available(),
            "llm_status": llm_setup_status(),
            "portfolio_goals": profile_goals_store.get_goals(),
            "import_quality_events": import_audit_store.latest(limit=12),
            "weekly_sync_status": weekly_sync_store.sync_status(),
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


class LlmSetupPayload(BaseModel):
    provider: str = Field(..., min_length=2, max_length=32)
    api_key: str | None = Field(default=None, max_length=512)
    model: str | None = Field(default=None, max_length=128)
    base_url: str | None = Field(default=None, max_length=256)


@router.get("/api/portfolio/setup/llm")
def api_setup_llm_get():
    from modules.portfolio.services.llm_config import llm_config_for_edit

    return llm_config_for_edit()


@router.get("/api/portfolio/setup/llm/ollama-models")
def api_setup_ollama_models(base_url: str = Query(default="http://localhost:11434")):
    """Models installed locally (Ollama /api/tags) for the setup dropdown."""
    from modules.portfolio.services.llm_config import fetch_ollama_model_names, validate_ollama_base_url

    try:
        validate_ollama_base_url(base_url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"models": fetch_ollama_model_names(base_url)}


@router.put("/api/portfolio/setup/llm")
def api_setup_llm_save(payload: LlmSetupPayload):
    from modules.portfolio.services.llm_config import save_llm_config

    try:
        return save_llm_config(payload.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class AccountProfilePayload(BaseModel):
    owner_ref: str | None = Field(default=None, max_length=64)
    country_of_residence: str | None = Field(default=None, min_length=2, max_length=7)
    india_residency_status: IndiaResidencyStatus | None = None
    tax_profile: str | None = Field(default=None, max_length=64)
    base_currency: str | None = Field(default=None, min_length=3, max_length=3)
    account_type: AccountType | None = None
    risk_profile: RiskProfile | None = None
    target_return_pct: float | None = Field(default=None, gt=0, le=100)
    max_position_pct: float | None = Field(default=None, gt=0, le=100)
    max_sector_pct: float | None = Field(default=None, gt=0, le=100)
    max_group_exposure_pct: float | None = Field(default=None, gt=0, le=100)
    cash_buffer_pct: float | None = Field(default=None, ge=0, le=100)
    tax_loss_harvesting_mode: TaxLossHarvestingMode | None = None
    tax_lots_available: bool | None = None
    gift_product_tax_verified: bool | None = None
    gift_product_tax_source: str | None = Field(default=None, max_length=500)
    gift_product_tax_as_of: str | None = Field(default=None, max_length=10)
    repatriability: Repatriability | None = None
    estate_tax_review_status: EstateTaxReviewStatus | None = None
    permitted_instrument_types: list[str] | None = Field(default=None, max_length=50)
    family_transfers_permitted: bool | None = None


class SetupAccountPayload(AccountProfilePayload):
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
    account_profile: AccountProfilePayload | None = None


class SetupAccountUpdatePayload(AccountProfilePayload):
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
    account_profile: AccountProfilePayload | None = None


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

    from shared.web.uploads import read_upload_bounded

    content = await read_upload_bounded(
        file,
        allowed_extensions={".csv", ".xlsx", ".xls"},
        allowed_content_types={
            "text/csv", "application/csv", "application/vnd.ms-excel",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/octet-stream",
        },
    )
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
