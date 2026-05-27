"""Jinja2 template engine configured for the Personal Hub."""

from pathlib import Path

from fastapi.templating import Jinja2Templates

from shared.config import APP_NAME, APP_TAGLINE, EXPENSES_APP_URL, LEARNINGS_APP_URL
from shared.web.formatters import (
    account_column_compact,
    build_sort_url,
    cap_badge_class,
    trade_accounts_for_holding,
    format_aed,
    format_cache_time,
    format_data_as_of_label,
    format_inr,
    format_inr_whole,
    format_debt_equity,
    format_na,
    format_na_compact,
    format_pct,
    format_pct_compact,
    format_pct_metric,
    format_pe,
    pnl_class,
    sector_badge_class,
    signal_short,
    signal_display_full,
    signal_group_slug,
    sort_indicator,
)

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"

templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
templates.env.globals.update(
    app_name=APP_NAME,
    app_tagline=APP_TAGLINE,
    expenses_app_url=EXPENSES_APP_URL,
    learnings_app_url=LEARNINGS_APP_URL,
    cap_badge_class=cap_badge_class,
    build_sort_url=build_sort_url,
    format_aed=format_aed,
    format_cache_time=format_cache_time,
    format_data_as_of_label=format_data_as_of_label,
    format_inr=format_inr,
    format_inr_whole=format_inr_whole,
    format_debt_equity=format_debt_equity,
    format_na=format_na,
    format_na_compact=format_na_compact,
    format_pct=format_pct,
    format_pct_compact=format_pct_compact,
    format_pct_metric=format_pct_metric,
    format_pe=format_pe,
    pnl_class=pnl_class,
    sector_badge_class=sector_badge_class,
    signal_short=signal_short,
    signal_display_full=signal_display_full,
    signal_group_slug=signal_group_slug,
    account_column_compact=account_column_compact,
    sort_indicator=sort_indicator,
    trade_accounts_for_holding=trade_accounts_for_holding,
)
