"""Contract tests for the evidence-first national economics v2.3 panel."""

from __future__ import annotations

import csv
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from workflows.national.harvest import national_economics_ui as exporter

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/derived/national/harvest_basis/national_economics_v2_3"
STATCAN = ROOT / "data/derived/national/harvest_basis/statcan_logging_cost_proxy"
TOTAL_SOURCE = (
    ROOT / "data/derived/national/harvest_basis/national_margin_totals"
)
TIER_SOURCE = (
    ROOT / "data/derived/national/harvest_basis/national_tier_coverage"
    / "national_tier_coverage.csv"
)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


@pytest.fixture(scope="module")
def built() -> tuple[dict, dict]:
    return exporter.build_export(check_deletions=False)


def _province(payload: dict, code: str) -> dict:
    return next(
        row for row in payload["jurisdictions"] if row["province"] == code
    )


def test_export_is_v23_evidence_first_with_13_jurisdictions_and_65_cells(built):
    payload, _ = built
    coverage = payload["evidence_coverage"]

    assert payload["schema_version"] == "economics/national/3"
    assert payload["model_id"] == "national_economics_v2_3"
    assert payload["artifact_version"] == "2.3.0"
    assert payload["default_view"] == "evidence_coverage"
    assert payload["role"] == "evidence_coverage_with_national_diagnostic"
    assert payload["section_order"][0] == "evidence_coverage"
    assert payload["coverage"]["province_count"] == 13
    assert payload["coverage"]["jurisdiction_rows"] == 13
    assert len(payload["jurisdictions"]) == 13
    assert len({row["province"] for row in payload["jurisdictions"]}) == 13
    assert coverage["jurisdiction_count"] == 13
    assert coverage["cell_count"] == 65
    assert coverage["component_count"] == 5
    assert coverage["not_decision_grade"] is True

    direct = coverage["volume_weighted"]["direct_reported"]
    assert direct["share"] == pytest.approx(0.33601842)
    assert direct["pct"] == pytest.approx(33.6018, abs=1e-4)
    assert direct["volume_basis"] == "119m_allocation_sensitivity"
    assert direct["jurisdictions"] == ["BC"]
    assert coverage["donor_price_count"] == 12
    assert coverage["donor_only_count"] == 11
    assert coverage["direct_cell_count"] == 3
    assert coverage["proxy_cell_count"] == 2
    assert coverage["donor_cell_count"] == 60
    assert coverage["direct_jurisdictions"] == ["BC"]
    assert "provisional aggregate" in coverage["warning"]


def test_default_cost_coverage_is_v23_aggregate_proxy_not_direct(built):
    payload, _ = built
    cost = payload["evidence_coverage"]["cost_evidence"]
    shares = cost["volume_weighted"]

    assert cost["volume_basis"] == "observed_2024_m3"
    assert cost["sensitivity_volume_basis"] == "119m_allocation_sensitivity"
    assert shares["component_stack"]["share"] == pytest.approx(0.54477285)
    assert shares["industry_aggregate_proxy"]["share"] == pytest.approx(
        0.45522715
    )
    assert shares["donor_fallback"]["share"] == pytest.approx(0.0)
    assert sum(item["share"] for item in shares.values()) == pytest.approx(1.0)
    assert shares["component_stack"]["cells"] == 2
    assert shares["industry_aggregate_proxy"]["cells"] == 10
    assert shares["donor_fallback"]["cells"] == 1
    assert shares["component_stack"]["jurisdictions"] == ["BC", "QC"]
    assert shares["donor_fallback"]["jurisdictions"] == ["NU"]
    assert payload["evidence_coverage"]["industry_aggregate_proxy"]["share"] == (
        pytest.approx(0.45522715)
    )
    assert payload["tier_coverage"]["cost"]["upgrade_count"] == 10
    assert payload["tier_coverage"]["cost"]["full_direct_coverage_claimed"] is False
    assert payload["evidence_coverage"]["cost_evidence"][
        "full_direct_coverage_claimed"
    ] is False


def test_province_cost_tiers_are_component_aggregate_and_fallback(built):
    payload, _ = built
    tiers = {row["province"]: row["cost_tier"] for row in payload["jurisdictions"]}

    assert tiers["BC"] == "component_stack"
    assert tiers["QC"] == "component_stack"
    for province in ("AB", "SK", "MB", "ON", "NB", "NS", "NL", "PE", "YT", "NT"):
        assert tiers[province] == "industry_aggregate_proxy"
    assert tiers["NU"] == "donor_fallback"
    assert {row["status_label"] for row in payload["jurisdictions"] if row["cost_tier"] == "industry_aggregate_proxy"} == {
        "provisional aggregate"
    }
    for row in payload["jurisdictions"]:
        assert row["ranked"] is False
        assert "rank" not in row
        assert row["cost_source"]["title"]
        assert row["cost_source"]["year"]
        if row["cost_tier"] == "industry_aggregate_proxy":
            assert row["cost_source"]["source_key"] == "statcan_16100114"
            assert row["cost_source"]["year"] == "2024"
            assert row["cost_source"]["includes_overhead"] is True
            assert row["cost_source"]["not_logging_haul_breakdown"] is True
            assert row["cost_source"]["is_direct"] is False
            assert "not a logging-only" in row["cost_source"]["caveat"].lower()
            assert (
                row["total_cost_low_cad_m3"]
                == row["total_cost_base_cad_m3"]
                == row["total_cost_high_cad_m3"]
            )
        elif row["cost_tier"] == "component_stack":
            assert len(row["cost_components"]) >= 3
            assert row["total_cost_low_cad_m3"] <= row["total_cost_base_cad_m3"]
        else:
            assert row["cost_source"]["fallback_used"] is True
            assert row["cost_source"]["source_key"] == "national_economics_v2_2"


def test_ab_on_display_uses_v23_aggregate_costs_and_margins(built):
    payload, _ = built
    ab = _province(payload, "AB")
    on = _province(payload, "ON")

    assert ab["total_cost_base_cad_m3"] == pytest.approx(54.9865)
    assert ab["donor_flat"]["base_cad_m3"] == pytest.approx(60.8415)
    assert ab["v2_2_to_v2_3"]["margin_base_delta_cad_m3"] == pytest.approx(
        25.8055
    )
    assert ab["status_label"] == "provisional aggregate"

    assert on["total_cost_base_cad_m3"] == pytest.approx(83.7293)
    assert on["donor_flat"]["base_cad_m3"] == pytest.approx(32.0987)
    assert on["v2_2_to_v2_3"]["margin_base_delta_cad_m3"] == pytest.approx(
        -3.1028
    )
    assert on["status_label"] == "provisional aggregate"
    assert on["stumpage_scaled"]["base_cad_m3"] == pytest.approx(-60.9518)


def test_bc_qc_and_national_margins_match_v23(built):
    payload, _ = built
    bc = _province(payload, "BC")
    qc = _province(payload, "QC")
    nu = _province(payload, "NU")

    assert bc["donor_flat"]["base_cad_m3"] == pytest.approx(41.5880)
    assert bc["stumpage_scaled"]["base_cad_m3"] == pytest.approx(41.5880)
    assert bc["price_tier"] == "reported_average"
    assert bc["price_source"]["is_direct"] is True
    assert qc["donor_flat"]["base_cad_m3"] == pytest.approx(32.4580)
    assert qc["cost_tier"] == "component_stack"
    assert nu["cost_tier"] == "donor_fallback"
    assert nu["donor_flat"]["base_cad_m3"] == pytest.approx(28.8899)

    diagnostic = payload["national_diagnostic"]
    assert diagnostic["donor_flat"]["base_cad_m3"] == pytest.approx(43.0187)
    assert diagnostic["stumpage_scaled"]["base_cad_m3"] == pytest.approx(-5.7444)
    assert diagnostic["sensitivity_119m"]["donor_flat"]["base_cad_m3"] == (
        pytest.approx(43.7506)
    )
    assert diagnostic["sensitivity_119m"]["stumpage_scaled"]["base_cad_m3"] == (
        pytest.approx(-5.5079)
    )
    assert diagnostic["national_comparison"][
        "scaled_minus_donor_flat_cad_m3"
    ] == pytest.approx(-48.7631)


def test_margins_are_recomputed_from_the_committed_csvs(built):
    payload, _ = built
    source = {
        (row["price_scenario"], row["province"]): row
        for row in _read_csv(SOURCE / "national_margin_summary.csv")
        if row["row_type"] == "province"
    }
    for jurisdiction in payload["jurisdictions"]:
        province = jurisdiction["province"]
        for scenario in ("donor_flat", "stumpage_scaled"):
            raw = source[(scenario, province)]
            values = jurisdiction[scenario]
            assert values["low_cad_m3"] == pytest.approx(
                float(raw["margin_low_cad_m3"])
            )
            assert values["base_cad_m3"] == pytest.approx(
                float(raw["margin_base_cad_m3"])
            )
            assert values["high_cad_m3"] == pytest.approx(
                float(raw["margin_high_cad_m3"])
            )
            assert values["cost_tier"] == jurisdiction["cost_tier"]


def test_aggregate_cost_inputs_match_the_statcan_proxy_csv(built):
    payload, _ = built
    statcan = {
        row["province"]: row
        for row in _read_csv(STATCAN / "provincial_logging_cost_proxy.csv")
    }
    for row in payload["jurisdictions"]:
        if row["cost_tier"] != "industry_aggregate_proxy":
            continue
        raw = statcan[row["province"]]
        assert row["total_cost_base_cad_m3"] == pytest.approx(
            float(raw["industry_expense_cad_m3"]), abs=1e-4
        )
        assert row["cost_source"]["year"] == raw["reference_year"]
        assert row["cost_source"]["source_key"] == "statcan_16100114"
        assert row["cost_source"]["statcan_status"] == raw[
            "industry_worst_quality_status"
        ]


def test_total_margin_column_and_national_cards_use_the_committed_ledger(built):
    payload, manifest = built
    totals = payload["national_margin_totals"]
    assert totals["label"] == "model_estimate_not_policy"
    assert totals["observed"] is False
    assert totals["policy_grade"] is False
    assert totals["not_policy_result"] is True
    assert totals["ranking_performed"] is False
    assert totals["combined_national_net_value"] is False
    assert totals["volume_basis"] == "119m_allocation"
    assert totals["province_count"] == 13

    source_rows = {
        (row["province"], row["scenario"]): row
        for row in _read_csv(TOTAL_SOURCE / "province_margin_totals.csv")
    }
    source_payload = json.loads(
        (TOTAL_SOURCE / "national_margin_totals.json").read_text(
            encoding="utf-8"
        )
    )
    assert totals["scenarios"] == source_payload["scenarios"]
    donor_sum = 0.0
    scaled_sum = 0.0
    for row in payload["jurisdictions"]:
        total = row["total_margin_119m"]
        assert total["volume_basis"] == "119m_allocation"
        for scenario in ("donor_flat", "stumpage_scaled"):
            raw = source_rows[(row["province"], scenario)]
            values = total[scenario]
            assert values["total_low_m_cad"] == pytest.approx(
                float(raw["total_low_m_cad"])
            )
            assert values["total_base_m_cad"] == pytest.approx(
                float(raw["total_base_m_cad"])
            )
            assert values["total_high_m_cad"] == pytest.approx(
                float(raw["total_high_m_cad"])
            )
            assert values["negative_flag"] == (
                raw["negative_flag"] == "true"
            )
        donor_sum += total["donor_flat"]["total_base_m_cad"]
        scaled_sum += total["stumpage_scaled"]["total_base_m_cad"]
    assert donor_sum == pytest.approx(
        totals["scenarios"]["donor_flat"]["national_total_base_m_cad"],
        abs=1e-4,
    )
    assert scaled_sum == pytest.approx(
        totals["scenarios"]["stumpage_scaled"]["national_total_base_m_cad"],
        abs=1e-4,
    )
    assert manifest["validation"][
        "national_total_margin_donor_flat_m_cad"
    ] == pytest.approx(5205.328748, abs=1e-6)
    assert manifest["validation"][
        "national_total_margin_stumpage_scaled_m_cad"
    ] == pytest.approx(-655.319124, abs=1e-6)
    assert manifest["validation"]["national_total_margin_province_count"] == 13
    assert manifest["validation"]["national_total_margin_policy_result"] is False


def test_total_margin_header_and_cards_are_not_hand_coded():
    app_js = (ROOT / "ui/app.js").read_text(encoding="utf-8")
    assert "Base total" in app_js
    assert "Sensitivity total" in app_js
    assert "row.total_margin_119m.donor_flat.total_base_m_cad" in app_js
    assert "row.total_margin_119m.stumpage_scaled.total_base_m_cad" in app_js
    assert "national.national_margin_totals" in app_js
    assert "totals.scenarios.donor_flat" in app_js
    assert "totals.scenarios.stumpage_scaled" in app_js
    assert "Totals use the 119 Mm³ allocation" in app_js
    assert "5,205" not in app_js
    assert "655.31" not in app_js


def test_national_diagnostic_is_not_a_combined_net_value(built):
    payload, _ = built
    diagnostic = payload["national_diagnostic"]
    assert diagnostic["label"] == "model_estimate_not_policy"
    assert diagnostic["not_decision_grade"] is True
    assert diagnostic["display_role"] == "diagnostic_not_headline"
    assert diagnostic["ranking_performed"] is False
    assert diagnostic["combined_national_net_value"] is False
    assert payload["labels"]["observed"] is False
    assert payload["labels"]["policy_grade"] is False
    assert payload["labels"]["forest_lp_input"] is False
    assert payload["labels"]["public_osecan_input"] is False
    text = json.dumps(payload)
    assert '"observed": true' not in text
    assert '"policy_grade": true' not in text
    assert '"combined_national_net_value": true' not in text


def test_table_order_is_canonical_not_margin_sorted(built):
    payload, _ = built
    order = [row["province"] for row in payload["jurisdictions"]]
    assert order == [
        "BC", "AB", "SK", "MB", "ON", "QC", "NB", "NS", "NL", "PE", "YT", "NT", "NU"
    ]
    margins = [row["donor_flat"]["base_cad_m3"] for row in payload["jurisdictions"]]
    assert margins != sorted(margins)
    assert margins != sorted(margins, reverse=True)
    assert payload["ranking"]["performed"] is False
    assert payload["ranking"]["aggregate_proxy_excluded"] is True


def test_manifest_hashes_v23_payload_and_sources(built):
    payload, manifest = built
    assert manifest["schema_version"] == "economics/national-manifest/3"
    assert manifest["model_id"] == "national_economics_v2_3"
    validation = manifest["validation"]
    assert validation["ok"] is True
    assert validation["jurisdiction_count"] == 13
    assert validation["price_cell_count"] == 65
    assert validation["direct_reported_price_share"] == pytest.approx(0.33601842)
    assert validation["industry_aggregate_proxy_share"] == pytest.approx(
        0.45522715
    )
    assert validation["donor_fallback_share"] == pytest.approx(0.0)
    assert validation["aggregate_proxy_count"] == 10
    assert validation["donor_fallback_count"] == 1
    assert validation["ranking_performed"] is False
    assert validation["statcan_unexpected_d_cells"] == 0

    generated = manifest["generated"][0]
    assert generated == manifest["files"][0]
    assert generated["path"] == "economics/national.json"
    assert generated["sha256"] == exporter._sha256_bytes(
        exporter._serialize(payload)
    )
    expected = {
        "data/derived/national/harvest_basis/national_economics_v2_3/national_margin_estimates.csv",
        "data/derived/national/harvest_basis/national_economics_v2_3/national_margin_summary.csv",
        "data/derived/national/harvest_basis/national_economics_v2_3/v2_2_v2_3_comparison.csv",
        "data/derived/national/harvest_basis/national_economics_v2_3/tier_coverage.csv",
        "data/derived/national/harvest_basis/national_economics_v2_3/national_cost_inputs.csv",
        "data/derived/national/harvest_basis/national_economics_v2_3/summary.json",
        "data/derived/national/harvest_basis/national_tier_coverage/national_tier_coverage.csv",
        "data/derived/national/harvest_basis/national_tier_coverage/national_tier_summary.json",
        "data/derived/national/harvest_basis/national_economics_v2_1/sources.csv",
        "data/derived/national/harvest_basis/national_economics_v2/provincial_stumpage_references.csv",
        "data/derived/national/harvest_basis/provincial_allocation/119m.csv",
        "data/derived/national/harvest_basis/national_margin_totals/province_margin_totals.csv",
        "data/derived/national/harvest_basis/national_margin_totals/national_margin_totals.json",
        "data/derived/national/harvest_basis/statcan_logging_cost_proxy/provincial_logging_cost_proxy.csv",
        "data/derived/national/harvest_basis/statcan_logging_cost_proxy/sources.csv",
        "data/derived/national/harvest_basis/forest_lp_net_value_sensitivity/allocation_net_value.csv",
        "data/derived/national/harvest_basis/forest_lp_net_value_sensitivity/allocation_net_value_summary.csv",
        "data/derived/national/harvest_basis/forest_lp_net_value_sensitivity/national_net_value_summary.csv",
        "data/derived/national/harvest_basis/forest_lp_net_value_sensitivity/summary.json",
    }
    assert {item["path"] for item in manifest["sources"]} == expected
    for item in manifest["sources"]:
        path = ROOT / item["path"]
        assert item["bytes"] == path.stat().st_size
        assert item["sha256"] == exporter._sha256(path)


def test_unexpected_deletions_and_unexpected_statcan_d_fail_closed():
    assert exporter._deletion_lines(" D data/foo.csv") == [" D data/foo.csv"]
    assert exporter._deletion_lines("D  data/foo.csv") == ["D  data/foo.csv"]
    assert exporter._deletion_lines("?? data/new.csv") == []
    assert exporter._deletion_lines(" M ui/app.js") == []

    rows = _read_csv(STATCAN / "provincial_logging_cost_proxy.csv")
    summary = json.loads((SOURCE / "summary.json").read_text(encoding="utf-8"))
    allowed = exporter._validate_statcan_d(rows, summary)
    assert allowed["unexpected_cells"] == []
    assert allowed["observed_d_cell_count"] == 16

    mutated = [dict(row) for row in rows]
    mutated[0]["unexpected_quality_status"] = "D"
    with pytest.raises(exporter.NationalEconomicsUiError, match="unexpected StatCan"):
        exporter._validate_statcan_d(mutated, summary)


def test_ui_panel_remains_read_only_and_names_v23_cost_tiers():
    html = (ROOT / "ui/index.html").read_text(encoding="utf-8")
    app_js = (ROOT / "ui/app.js").read_text(encoding="utf-8")
    panel = html.split('id="panel-economics"', 1)[1].split(
        'id="panel-osecan"', 1
    )[0]
    national = panel.split('id="national-economics"', 1)[1].split(
        'id="economics-view-bc"', 1
    )[0]

    assert 'id="economics-subtab-national"' in panel
    assert 'id="economics-subtab-bc"' in panel
    assert 'id="economics-view-national"' in panel
    assert 'id="economics-view-bc"' in panel
    assert panel.count("<button") == 2
    assert panel.count("<form") == 0
    assert panel.count("<select") == 0
    assert 'type="range"' not in panel

    assert 'id="national-economics"' in panel
    assert 'id="national-headline"' in national
    assert 'id="national-evidence-strip"' in national
    assert 'id="national-table"' in national
    assert 'id="national-drilldown"' in national
    assert 'id="national-net-value"' in national
    assert 'id="national-caveat"' in national
    assert 'id="national-methods"' in national
    assert 'id="national-downloads"' in national
    for removed in (
        'id="national-warning"',
        'id="national-coverage"',
        'id="national-margin-diagnostic"',
        'id="national-net-value-chart"',
        'id="national-net-value-summary"',
        'id="national-net-value-badges"',
    ):
        assert removed not in national
    assert national.count("<button") == 0
    assert national.count("<form") == 0
    assert national.count("<select") == 0
    assert "v2.3" in national.lower()

    assert "loadJSON('economics/national.json')" in app_js
    assert "loadJSON('economics/national.manifest.json')" in app_js
    assert "function validateNational" in app_js
    assert "function renderNational" in app_js
    assert "NATIONAL_SCHEMA = 'economics/national/3'" in app_js
    assert "national_economics_v2_3" in app_js
    assert "provisional aggregate" in app_js.lower()
    assert "component_stack" in app_js
    assert "industry_aggregate_proxy" in app_js
    assert "donor_fallback" in app_js
    assert "not a logging-only" in app_js.lower()
    assert "ranking.performed === false" in app_js
    assert "not Forest LP/OSeCAN input" in app_js
    assert "observed=false" in app_js
    assert "policy_grade=false" in app_js
    for removed in (
        "function renderNationalCoverage",
        "function renderNationalDiagnostic",
        "function renderNationalNetValueChart",
        "renderNationalBadges",
    ):
        assert removed not in app_js

    render_block = app_js.split("function renderNational()", 1)[1].split(
        "/* ---------------- Inputs ---------------- */", 1
    )[0]
    assert render_block.index("renderNationalHeadline()") < render_block.index(
        "renderNationalEvidenceStrip()"
    )
    assert render_block.index("renderNationalEvidenceStrip()") < render_block.index(
        "renderNationalTable()"
    )
    assert render_block.index("renderNationalTable()") < render_block.index(
        "renderNationalDrilldown()"
    )
    assert render_block.index("renderNationalDrilldown()") < render_block.index(
        "renderNationalNetValueTable()"
    )
    assert render_block.index("renderNationalNetValueTable()") < render_block.index(
        "renderNationalCaveat()"
    )
    assert render_block.index("renderNationalCaveat()") < render_block.index(
        "renderNationalMethods()"
    )


def test_economics_subtabs_default_to_national_and_hide_bc_detail():
    html = (ROOT / "ui/index.html").read_text(encoding="utf-8")
    app_js = (ROOT / "ui/app.js").read_text(encoding="utf-8")

    buttons = html.split("<button")
    national_button = next(
        button for button in buttons if 'id="economics-subtab-national"' in button
    ).split(">", 1)[0]
    bc_button = next(
        button for button in buttons if 'id="economics-subtab-bc"' in button
    ).split(">", 1)[0]
    assert 'aria-selected="true"' in national_button
    assert 'aria-selected="false"' in bc_button

    sections = html.split("<section")
    national_view = next(
        section for section in sections if 'id="economics-view-national"' in section
    ).split(">", 1)[0]
    bc_view = next(
        section for section in sections if 'id="economics-view-bc"' in section
    ).split(">", 1)[0]
    assert "hidden" not in national_view
    assert "hidden" in bc_view

    assert "economicsView: 'national'" in app_js
    assert "function activateEconomicsView" in app_js
    assert "function bindEconomicsSubtabs" in app_js
    assert "state.economicsView === 'bc'" in app_js
    assert 'name="national-province-detail"' in app_js
    assert "card.open = false" in app_js
    assert "Collapsed by default" in html

    bc_detail = html.split('id="economics-view-bc"', 1)[1].split(
        'id="panel-osecan"', 1
    )[0]
    assert 'id="economics-chart"' in bc_detail
    assert 'id="economics-table"' in bc_detail
    assert 'id="economics-qc"' in bc_detail
    assert 'id="economics-evidence"' in bc_detail
    assert 'id="economics-downloads"' in bc_detail
    assert 'id="national-table"' not in bc_detail
    assert 'id="national-net-value"' not in bc_detail


def test_national_tables_stack_readably_on_mobile():
    app_js = (ROOT / "ui/app.js").read_text(encoding="utf-8")
    css = (ROOT / "ui/styles.css").read_text(encoding="utf-8")

    assert 'data-label="Base total · M CAD"' in app_js
    assert 'data-label="Sensitivity total · M CAD"' in app_js
    assert 'data-label="Status"' in app_js
    assert "national-price-tier-cell" in app_js
    assert "national-cost-tier-cell" in app_js
    assert 'data-label="Base low"' in app_js
    assert 'data-label="Sensitivity high"' in app_js
    assert 'data-label="Status / note"' in app_js

    mobile = css.rsplit("@media (max-width: 720px)", 1)[1].split(
        "@media (max-width: 560px)", 1
    )[0]
    assert ".national-table thead" in mobile
    assert ".national-net-value-table thead" in mobile
    assert ".national-price-tier-cell" in mobile
    assert ".national-cost-tier-cell" in mobile
    assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in mobile
    assert "grid-template-columns: repeat(3, minmax(0, 1fr))" in mobile
    assert "content: attr(data-label)" in mobile
    assert ".national-net-value-row" in mobile
    assert "national-net-value-case-note" in app_js


def test_live_payload_is_not_stale(built):
    payload, _ = built
    live = json.loads(
        (ROOT / "ui/public/data/economics/national.json").read_text(
            encoding="utf-8"
        )
    )
    assert live == payload


def test_export_does_not_touch_the_static_bundle():
    before_json = (ROOT / "ui/public/data/economics/national.json").stat().st_mtime_ns
    before_manifest = (
        ROOT / "ui/public/data/economics/national.manifest.json"
    ).stat().st_mtime_ns
    exporter.build_export(check_deletions=False)
    assert (
        ROOT / "ui/public/data/economics/national.json"
    ).stat().st_mtime_ns == before_json
    assert (
        ROOT / "ui/public/data/economics/national.manifest.json"
    ).stat().st_mtime_ns == before_manifest


def test_forest_lp_net_value_sensitivity_values_and_labels(built):
    payload, manifest = built
    net_value = payload["forest_lp_net_value_sensitivity"]

    assert net_value["model_id"] == "forest_lp_net_value_sensitivity_v1"
    assert net_value["schema_version"] == "national/forest-lp-net-value-sensitivity/1"
    assert net_value["status"] == "model_estimate_not_policy"
    assert net_value["labels"] == {
        "net_value_label": "model_estimate",
        "net_value_status": "model_estimate_not_policy",
        "observed": False,
        "policy_grade": False,
        "lp_objective": "not_used",
        "forest_lp_input": False,
        "public_osecan_input": False,
        "forest_lp_objective_changed": False,
        "v1_outputs_changed": False,
        "combined_national_net_value": False,
        "ranking_performed": False,
        "restricted_data_used": False,
    }
    assert net_value["coverage"]["allocation_rows"] == 117
    assert net_value["coverage"]["group_rows"] == 18
    assert net_value["coverage"]["documented_ceiling_rows"] == 6
    assert net_value["coverage"]["infeasible_rows"] == 4
    assert net_value["infeasible_row_count"] == 4

    expected = {
        ("100m", "donor_flat"): (-3230.786923, 4347.226340, 7633.971898),
        ("100m", "stumpage_scaled"): (-6750.691500, -559.722836, 3359.095964),
        ("119m", "donor_flat"): (-3854.908562, 5205.328748, 9137.988762),
        ("119m", "stumpage_scaled"): (-8058.885699, -655.319123, 4032.879740),
        ("150m", "donor_flat"): (-4299.883636, 6662.823950, 11436.002324),
        ("150m", "stumpage_scaled"): (-9950.394662, -1194.863823, 4505.859579),
    }
    documented = [
        row
        for row in net_value["rows"]
        if row["cap_case"] == "documented_ceiling"
    ]
    assert len(documented) == 6
    assert {(row["allocation_case"], row["scenario"]) for row in documented} == set(expected)
    for row in documented:
        low, base, high = expected[(row["allocation_case"], row["scenario"])]
        assert row["feasible"] is True
        assert row["case_status"] == "optimal"
        assert row["net_value_low_m_cad"] == pytest.approx(low, abs=1e-6)
        assert row["net_value_base_m_cad"] == pytest.approx(base, abs=1e-6)
        assert row["net_value_high_m_cad"] == pytest.approx(high, abs=1e-6)
        assert row["scenario_label"] == row["scenario_role"]
        assert row["negative_province_count"] == (
            0 if row["scenario"] == "donor_flat" else 5
        )
    assert {
        (row["allocation_case"], row["scenario"]) for row in documented
    } == {
        (case, scenario)
        for case in ("100m", "119m", "150m")
        for scenario in ("donor_flat", "stumpage_scaled")
    }

    assert manifest["validation"]["net_value_sensitivity_allocation_rows"] == 117
    assert manifest["validation"]["net_value_sensitivity_groups"] == 18
    assert manifest["validation"]["net_value_sensitivity_documented_ceiling_rows"] == 6
    assert manifest["validation"]["net_value_sensitivity_infeasible_rows"] == 4
    assert manifest["validation"]["net_value_sensitivity_label"] == (
        "model_estimate_not_policy"
    )
    assert manifest["validation"]["net_value_sensitivity_lp_objective"] == "not_used"
    assert manifest["validation"]["net_value_sensitivity_observed_claim"] is False
    assert manifest["validation"]["net_value_sensitivity_policy_grade_claim"] is False


def test_150m_intensity_rows_are_blank_not_zero(built):
    payload, _ = built
    net_value = payload["forest_lp_net_value_sensitivity"]
    infeasible = [row for row in net_value["rows"] if not row["feasible"]]

    assert len(infeasible) == 4
    assert {
        (row["cap_case"], row["allocation_case"], row["scenario"])
        for row in infeasible
    } == {
        (cap_case, "150m", scenario)
        for cap_case in ("intensity_1.0", "intensity_1.2")
        for scenario in ("donor_flat", "stumpage_scaled")
    }
    for row in infeasible:
        assert row["case_status"] == "infeasible"
        assert row["net_value_low_m_cad"] is None
        assert row["net_value_base_m_cad"] is None
        assert row["net_value_high_m_cad"] is None
        assert row["allocation_m3"] is None
        assert row["negative_province_count"] is None
        assert row["negative_province_counts"] == {
            "low": None,
            "base": None,
            "high": None,
        }
        assert row["net_value_low_m_cad"] != 0
        assert row["net_value_base_m_cad"] != 0
        assert row["net_value_high_m_cad"] != 0


def test_unexpected_d_in_net_value_rows_fails_closed():
    groups = _read_csv(
        ROOT
        / "data/derived/national/harvest_basis/forest_lp_net_value_sensitivity"
        / "allocation_net_value_summary.csv"
    )
    allocation_rows = _read_csv(
        ROOT
        / "data/derived/national/harvest_basis/forest_lp_net_value_sensitivity"
        / "allocation_net_value.csv"
    )
    national_rows = _read_csv(
        ROOT
        / "data/derived/national/harvest_basis/forest_lp_net_value_sensitivity"
        / "national_net_value_summary.csv"
    )
    summary = json.loads(
        (
            ROOT
            / "data/derived/national/harvest_basis/forest_lp_net_value_sensitivity"
            / "summary.json"
        ).read_text(encoding="utf-8")
    )
    exporter._forest_lp_net_value_sensitivity(
        allocation_rows, groups, national_rows, summary
    )

    mutated = [dict(row) for row in groups]
    mutated[0]["case_status"] = "D"
    with pytest.raises(exporter.NationalEconomicsUiError, match="unexpected D"):
        exporter._forest_lp_net_value_sensitivity(
            allocation_rows, mutated, national_rows, summary
        )


def test_ui_net_value_section_is_read_only_and_renders_blanks():
    html = (ROOT / "ui/index.html").read_text(encoding="utf-8")
    app_js = (ROOT / "ui/app.js").read_text(encoding="utf-8")
    section = html.split('id="national-net-value"', 1)[1].split(
        "</section>", 1
    )[0]

    assert 'id="national-net-value-title"' in section
    assert "Forest LP net-value table" in section
    assert 'id="national-net-value-table"' in section
    assert 'id="national-net-value-footnote"' in section
    assert 'id="national-net-value-chart"' not in section
    assert 'id="national-net-value-badges"' not in section
    assert 'id="national-net-value-summary"' not in section
    assert "150 Mm³ at intensity 1.0 and 1.2 is infeasible" in section
    assert "blank, not zero" in section
    assert "model_estimate_not_policy" in section
    assert "no LP/OSeCAN input" in section
    assert section.count("<button") == 0
    assert section.count("<form") == 0
    assert section.count("<select") == 0
    assert "function renderNationalNetValueTable" in app_js
    assert "national-net-value-blank" in app_js
    assert "documented ceiling" in app_js
    assert "lp_objective=not_used" in app_js
    assert "no LP/OSeCAN input" in app_js

    renderer = app_js.split("function renderNationalNetValueTable", 1)[1].split(
        "function renderNationalCaveat", 1
    )[0]
    assert "row.cap_case === 'documented_ceiling'" in renderer
    assert "['100m', '119m', '150m'].map" in renderer
    assert "netValue.rows.map" not in renderer
    assert "national-net-value-chart" not in app_js

    render_block = app_js.split("function renderNational()", 1)[1].split(
        "/* ---------------- Inputs ---------------- */", 1
    )[0]
    assert render_block.index("renderNationalNetValueTable()") < render_block.index(
        "renderNationalCaveat()"
    )
    assert render_block.index("renderNationalCaveat()") < render_block.index(
        "renderNationalMethods()"
    )


def test_app_js_syntax():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    result = subprocess.run(
        [node, "--check", str(ROOT / "ui/app.js")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
