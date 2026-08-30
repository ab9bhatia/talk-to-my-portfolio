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


def test_dashboard_leads_with_shared_decision_and_setup_stays_in_symbol_column():
    row = _read("shared/web/templates/portfolio/_holding_row.html")
    head = _read("shared/web/templates/portfolio/_holdings_table_head.html")
    script = _read("shared/web/static/js/holdings.js")
    styles = _read("shared/web/static/css/app.css")
    assert "decision_presentation" in row
    assert "Decision <span" in head
    assert "Tgt%" not in head
    assert "Street <span" not in head
    assert "const compactMove" in script
    assert ".pattern-pill-wrap {\n  display: block;\n  max-width: 100%;" in styles
    assert ".pattern-pill-text {\n  min-width: 0;\n  overflow: hidden;" in styles


def test_expanded_holding_uses_decision_hierarchy_and_contextual_order_gate():
    row = _read("shared/web/templates/portfolio/_holding_row.html")
    detail = _read("shared/web/templates/portfolio/_holding_trade_actions.html")
    assert row.index('_holding_trade_actions.html') < row.index('_holding_account_breakdown.html')
    assert "Your decision" in detail
    assert "Do now" in detail
    assert "Why" in detail
    assert "How much" in detail
    assert "How to execute" in detail
    assert "Context only · does not change this decision" in detail
    assert "decision.get('readiness') == 'READY_TO_REVIEW'" in detail
    assert "Prepare staged add" in detail
    assert "Prepare trim" in detail
    assert "Prepare exit" in detail


def test_external_context_is_neutral_and_no_longer_competes_with_patterns():
    script = _read("shared/web/static/js/holdings.js")
    view = _read("modules/portfolio/services/holdings_view.py")
    rating = _read("shared/web/templates/portfolio/_rating_cell_detail.html")
    assert "External positive" in view
    assert "External mixed" in view
    assert "External: strong buy" not in view
    assert "streetLabelConflictsWithPattern" not in script
    assert "reconcileStreetViewWithPattern" not in script
    assert "rating-context" in rating


def test_action_center_uses_typed_conflict_copy_and_dashboard_deep_link():
    script = _read("shared/web/static/js/portfolio-advisor.js")
    assert "Timing differs — primary decision unchanged." in script
    assert "External view differs — no action change." in script
    assert 'new URLSearchParams(window.location.search).get("symbol")' in script


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
    assert "?blocking=false" in script
    assert "background scan request timed out" in script
    assert script.index(baseline) < script.index("loadPatternOverlay(version);")


def test_agent_empty_submission_is_visible_and_accessible():
    script = _read("shared/web/static/js/portfolio-agent.js")
    template = _read("shared/web/templates/portfolio/_portfolio_agent.html")
    assert "Enter a question before asking the portfolio agent." in script
    assert 'id="agent-error" role="alert"' in template
    assert 'aria-describedby="agent-hint agent-error"' in template


def test_agent_stream_errors_and_incomplete_streams_are_visible():
    script = _read("shared/web/static/js/portfolio-agent.js")
    assert "onEvent(event, parsed);" in script
    assert "Agent stream ended before a response was returned" in script
    assert "Portfolio Agent timed out after 2 minutes" in script


def test_chart_setup_filter_has_explicit_semantics_and_state():
    script = _read("shared/web/static/js/holdings.js")
    radar_script = _read("shared/web/static/js/portfolio-patterns.js")
    template = _read("shared/web/templates/portfolio/_portfolio_filters_bar.html")
    styles = _read("shared/web/static/css/app.css")
    assert ">Active setups</span>" in template
    assert "Could not load chart setups" in script
    assert 'fetch("/api/portfolio/patterns?blocking=false")' in script
    assert "Scanning setups…" in script
    assert 'fetch(`/api/portfolio/patterns${query}`)' in radar_script
    assert "Scanning in the background — keep using the dashboard." in radar_script
    assert 'row.dataset.hasPattern !== "1"' in script
    assert "color: var(--text-muted);" in styles


def test_group_allocation_is_immediate_accessible_and_chart_cdn_is_lazy():
    template = _read("shared/web/templates/portfolio/_holdings_grouped.html")
    table = _read("shared/web/templates/portfolio/_holdings_table.html")
    script = _read("shared/web/static/js/holdings.js")
    styles = _read("shared/web/static/css/app.css")

    assert 'class="allocation-overview"' in template
    assert 'role="progressbar"' in template
    assert '<canvas id="portfolio-groups-chart"' not in template
    assert "chart.umd.min.js\"></script>" not in table
    assert "function ensureChartJs()" in script
    assert "function renderAllocationOverview(groups)" in script
    assert "const rowMetadataCache = new WeakMap();" in script
    assert "updateFilteredPortfolioSummary(totals);" in script
    assert ".allocation-overview-track {" in styles


def test_primary_layout_can_shrink_inside_the_viewport():
    styles = _read("shared/web/static/css/app.css")
    assert "grid-template-columns: var(--sidebar-width) minmax(0, 1fr);" in styles
    assert ".main {\n  min-width: 0;" in styles
    assert ".os-page { display: grid; min-width: 0;" in styles
    assert "overscroll-behavior-inline: contain;" in styles


def test_data_quality_defaults_to_a_bounded_auditable_queue():
    router = _read("modules/portfolio/router.py")
    template = _read("shared/web/templates/portfolio/data_quality.html")
    assert "limit: int = Query(60, ge=20, le=500)" in router
    assert '"security_rows": security_rows[:limit]' in router
    assert "Show all {{ security_rows_total }}" in template
    assert "append-only record in <code>reconciliation_overrides</code>" in template


def test_weekly_sync_setup_flow_is_root_aware_and_explicitly_non_trading():
    template = _read("shared/web/templates/portfolio/setup.html")
    script = _read("shared/web/static/js/portfolio-weekly-sync.js")
    assert 'id="weekly-sync-run"' in template
    assert "It never places orders." in template
    assert 'window.appUrl ? window.appUrl(path) : path' in script
    assert 'endpoint("/api/portfolio/sync/weekly/async")' in script
    assert 'endpoint(`/api/portfolio/sync/jobs/${runId}`)' in script
    assert "You can keep using the app." in script
    assert 'dry_run: dryRunInput.checked' in script
