"""Display formatting helpers for templates."""

import re
from urllib.parse import urlencode


def _coerce_number(value: float | int | None) -> float | int | None:
    """Accept None or numeric; treat Jinja Undefined / bad types as missing."""
    if value is None:
        return None
    try:
        from jinja2.runtime import Undefined

        if isinstance(value, Undefined):
            return None
    except ImportError:
        pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def format_inr(value: float | int | None) -> str:
    """Format a number as Indian Rupees."""
    num = _coerce_number(value)
    if num is None:
        return "₹0"
    return f"₹{num:,.2f}"


def format_inr_whole(value: float | int | None) -> str:
    """Compact ₹ for table cells (no decimals)."""
    num = _coerce_number(value)
    if num is None:
        return "—"
    return f"₹{num:,.0f}"


def format_aed(value: float | int | None) -> str:
    """Format a number as UAE Dirhams."""
    if value is None:
        return "AED 0.00"
    return f"AED {value:,.2f}"


def format_pct(value: float | int | None) -> str:
    """Format a percentage with sign."""
    num = _coerce_number(value)
    if num is None:
        return "0.00%"
    sign = "+" if num > 0 else ""
    return f"{sign}{num:.2f}%"


def format_pct_compact(value: float | int | None) -> str:
    """Compact % for table cells (one decimal)."""
    num = _coerce_number(value)
    if num is None:
        return "—"
    sign = "+" if num > 0 else ""
    return f"{sign}{num:.1f}%"


def format_cache_time(timestamp: float | None) -> str:
    """Format cache timestamp for display."""
    if timestamp is None:
        return "—"
    from datetime import datetime

    return datetime.fromtimestamp(timestamp).strftime("%I:%M %p")


def format_pe(value: float | int | None) -> str:
    """Format P/E ratio."""
    num = _coerce_number(value)
    if num is None:
        return "—"
    return f"{num:.1f}"


def format_pct_metric(value: float | int | None) -> str:
    """Format ROCE/ROE-style metrics (ratio or percent)."""
    num = _coerce_number(value)
    if num is None:
        return "—"
    if abs(num) <= 1.5:
        num *= 100
    return f"{num:.1f}%"


def format_debt_equity(value: float | int | None) -> str:
    """Format debt-to-equity ratio."""
    num = _coerce_number(value)
    if num is None:
        return "—"
    return f"{num:.2f}"


def sector_badge_class(sector: str | None) -> str:
    """Return CSS class for sector badge."""
    if not sector:
        return "sector-unknown"
    slug = re.sub(r"[^a-z0-9]+", "-", sector.lower()).strip("-")
    return f"sector-{slug}" if slug else "sector-unknown"


def format_na(value: float | int | None, suffix: str = "") -> str:
    """Format a numeric metric or em dash if missing."""
    num = _coerce_number(value)
    if num is None:
        return "—"
    return f"{num:.2f}{suffix}"


def format_na_compact(value: float | int | None, suffix: str = "") -> str:
    """Compact metric for table cells (one decimal)."""
    num = _coerce_number(value)
    if num is None:
        return "—"
    return f"{num:.1f}{suffix}"


def pnl_class(value: float | int | None) -> str:
    """Return CSS class name for profit/loss coloring."""
    num = _coerce_number(value)
    if num is None or num == 0:
        return "neutral"
    return "positive" if num > 0 else "negative"


def signal_short(label: str | None) -> str:
    """One- or two-letter signal for narrow table cells."""
    if not label:
        return ""
    mapping = {
        "Strong buy": "B+",
        "Buy": "B",
        "Hold": "H",
        "Sell": "S",
        "Strong sell": "S+",
    }
    return mapping.get(label, label[:2])


def signal_group_slug(code: str | None) -> str:
    """CSS slug for signal group headers (B+, B, …)."""
    return {
        "B+": "strong-buy",
        "B": "buy",
        "H": "hold",
        "S": "sell",
        "S+": "strong-sell",
    }.get(code or "", "unknown")


def signal_display_full(label: str | None) -> str:
    """Full signal label for expander rows, e.g. Strong buy (B+)."""
    if not label:
        return "—"
    short = signal_short(label)
    if short:
        return f"{label} ({short})"
    return label


def account_column_compact(account_label: str | None) -> str:
    """Short account codes for table cell; full detail on hover."""
    if not account_label:
        return "—"
    if " + " in account_label:
        return "Multi"
    return account_label.split("(")[0].strip()


def trade_accounts_for_holding(holding: dict) -> list:
    """Template helper — tradable accounts for Buy/Sell on a holding row."""
    from modules.portfolio.services.orders import trade_accounts_for_holding as _fn

    return _fn(holding)


def cap_badge_class(cap: str | None) -> str:
    """Return CSS class for market-cap badge."""
    mapping = {
        "Large": "cap-large",
        "Mid": "cap-mid",
        "Small": "cap-small",
        "Multi-cap": "cap-multicap",
        "Unclassified": "cap-mf",
    }
    return mapping.get(cap or "", "cap-unknown")


def build_sort_url(
    base_path: str,
    column: str,
    current_sort: str,
    current_order: str,
    group_by: str | None = None,
    refresh: bool = False,
) -> str:
    """Build URL toggling sort order for a column header click."""
    order = "asc" if column == current_sort and current_order == "desc" else "desc"
    params: dict[str, str] = {"sort": column, "order": order}
    if group_by:
        params["group_by"] = group_by
    if refresh:
        params["refresh"] = "1"
    return f"{base_path}?{urlencode(params)}"


def sort_indicator(column: str, current_sort: str, current_order: str) -> str:
    """Return ▲/▼ for the active sort column."""
    if column != current_sort:
        return ""
    return "▲" if current_order == "asc" else "▼"
