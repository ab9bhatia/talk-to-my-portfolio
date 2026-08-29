"""Static regression contracts for the primary connect-to-ask journey."""

from pathlib import Path

from shared.web.formatters import format_quote_price_whole


ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_account_chip_respects_configured_app_root():
    template = _read("shared/web/templates/portfolio/_broker_status_strip.html")
    assert "{{ app_path('/portfolio/setup') }}" in template
    assert 'href="/portfolio/setup"' not in template


def test_growth_single_snapshot_has_a_finite_explanation():
    script = _read("shared/web/static/js/portfolio-growth.js")
    assert "Need two daily snapshots" in script
    assert "pct: -Infinity" not in script


def test_growth_single_snapshot_is_guided_instead_of_charting_one_point():
    script = _read("shared/web/static/js/portfolio-growth.js")
    template = _read("shared/web/templates/portfolio/growth.html")
    assert "const hasComparison = points.length >= 2;" in script
    assert "Five sessions gives a useful weekly baseline" in script
    assert 'id="growth-insights-panel"' in template
    assert 'id="growth-comparison-panel"' in template


def test_holding_target_shows_native_price_and_setup_stays_in_symbol_column():
    row = _read("shared/web/templates/portfolio/_holding_row.html")
    script = _read("shared/web/static/js/holdings.js")
    styles = _read("shared/web/static/css/app.css")
    assert "format_quote_price_whole(h.get('target_price'), h.exchange)" in row
    assert "const compactMove" in script
    assert ".pattern-pill-wrap {\n  display: block;\n  max-width: 100%;" in styles
    assert ".pattern-pill-text {\n  min-width: 0;\n  overflow: hidden;" in styles


def test_target_price_uses_native_quote_currency():
    assert format_quote_price_whole(2037.4, "NSE") == "₹2,037"
    assert format_quote_price_whole(187.6, "US") == "$188"
    assert format_quote_price_whole(None, "US") == "—"


def test_action_center_uses_progressive_pattern_enrichment():
    script = _read("shared/web/static/js/portfolio-advisor.js")
    baseline = 'patterns=false'
    enrichment = 'patterns=true'
    scan = 'fetch("/api/portfolio/patterns"'
    assert baseline in script
    assert enrichment in script
    assert scan in script
    assert "scan exceeded 120 seconds" in script
    assert script.index(baseline) < script.index("loadPatternOverlay(version);")


def test_agent_empty_submission_is_visible_and_accessible():
    script = _read("shared/web/static/js/portfolio-agent.js")
    template = _read("shared/web/templates/portfolio/_portfolio_agent.html")
    assert "Enter a question before asking the portfolio agent." in script
    assert 'id="agent-error" role="alert"' in template
    assert 'aria-describedby="agent-hint agent-error"' in template


def test_primary_layout_can_shrink_inside_the_viewport():
    styles = _read("shared/web/static/css/app.css")
    assert "grid-template-columns: var(--sidebar-width) minmax(0, 1fr);" in styles
    assert ".main {\n  min-width: 0;" in styles


def test_weekly_sync_setup_flow_is_root_aware_and_explicitly_non_trading():
    template = _read("shared/web/templates/portfolio/setup.html")
    script = _read("shared/web/static/js/portfolio-weekly-sync.js")
    assert 'id="weekly-sync-run"' in template
    assert "It never places orders." in template
    assert 'window.appUrl ? window.appUrl(path) : path' in script
    assert 'endpoint("/api/portfolio/sync/weekly")' in script
    assert 'dry_run: dryRunInput.checked' in script
