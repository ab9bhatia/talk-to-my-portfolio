"""Static regression contracts for the primary connect-to-ask journey."""

from pathlib import Path


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
