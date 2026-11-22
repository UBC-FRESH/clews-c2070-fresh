"""Contract tests for the grouped, non-clipping scenario rail."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_rail_chrome_is_pinned_and_only_the_list_scrolls():
    css = (ROOT / "ui/styles.css").read_text(encoding="utf-8")
    shell = css.split(".app-shell {", 1)[1].split("}", 1)[0]
    rail = css.split(".rail {", 1)[1].split("}", 1)[0]
    inner = css.split(".rail-inner {", 1)[1].split("}", 1)[0]
    scenario_list = css.split(".scenario-list {", 1)[1].split("}", 1)[0]

    assert "overflow: hidden" not in shell
    assert "position: sticky" in rail
    assert "top: 0" in rail
    assert "height: 100vh" in rail
    assert "height: 100dvh" in rail
    assert "align-self: start" in rail
    assert "overflow: hidden" in rail

    assert "position: static" in inner
    assert "height: 100%" in inner
    assert "min-height: 0" in inner
    assert "max-height: none" in inner
    assert "box-sizing: border-box" in inner
    assert "overflow: hidden" in inner

    assert "flex: 1 1 auto" in scenario_list or "flex: 1" in scenario_list
    assert "min-height: 0" in scenario_list
    assert "overflow: auto" in scenario_list
    assert ".rail-head,\n.search-field,\n.family-filter,\n.rail-divider,\n.rail-foot { flex: 0 0 auto; }" in css

    assert ".rail.is-open .rail-head { display: none; }" in css
    rail_mobile = css.split(".rail.is-open .rail-inner {", 1)[1].split("}", 1)[0]
    assert "height: 70dvh" in rail_mobile
    assert "max-height: 70dvh" in rail_mobile
    assert "overflow: hidden" in rail_mobile
    assert ".scenario-list { flex: 1 1 0; max-height: none; }" in css


def test_scenarios_render_in_collapsible_family_groups():
    app_js = (ROOT / "ui/app.js").read_text(encoding="utf-8")
    css = (ROOT / "ui/styles.css").read_text(encoding="utf-8")
    html = (ROOT / "ui/index.html").read_text(encoding="utf-8")

    assert 'id="scenario-list"' in html
    assert "function renderScenarioList" in app_js
    assert "function scenarioOrderRank" in app_js
    assert "var order = { 100: 1, 119: 2, 150: 3, 200: 4 };" in app_js
    assert "function familyRoleSummary" in app_js
    assert "function railGroupHtml" in app_js
    assert "scenario-group-toggle" in app_js
    assert "scenario-group-count" in app_js
    assert "scenario-group-role" in app_js
    assert "aria-expanded" in app_js
    assert "scenario-group-items" in app_js
    assert "data-rail-group" in app_js
    assert "function toggleRailGroup" in app_js
    assert "if (state.search.trim() || state.familyFilter !== 'all') return true;" in app_js
    assert "family.canonical || family.family === selectedFamily" in app_js
    assert "state.railExpanded[sc.family] = true;" in app_js
    assert "function scenarioBadges" in app_js
    assert "s-audit" in app_js
    assert "badge--prefleet" in app_js

    assert ".scenario-group-toggle" in css
    assert ".scenario-group-items[hidden] { display: none; }" in css
    assert ".s-audit" in css
