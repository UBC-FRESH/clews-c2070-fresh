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
        "data/derived/national/harvest_basis/statcan_logging_cost_proxy/provincial_logging_cost_proxy.csv",
        "data/derived/national/harvest_basis/statcan_logging_cost_proxy/sources.csv",
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
    national = panel.split('id="national-economics"', 1)[1]

    assert 'id="national-economics"' in panel
    assert 'id="national-warning"' in national
    assert 'id="national-coverage"' in national
    assert 'id="national-table"' in national
    assert 'id="national-drilldown"' in national
    assert 'id="national-margin-diagnostic"' in national
    assert 'id="national-downloads"' in national
    assert national.count("<button") == 0
    assert national.count("<form") == 0
    assert national.count("<select") == 0
    assert "provisional aggregate" in national.lower()
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

    render_block = app_js.split("function renderNational()", 1)[1].split(
        "/* ---------------- Inputs ---------------- */", 1
    )[0]
    assert render_block.index("renderNationalCoverage()") < render_block.index(
        "renderNationalTable()"
    )
    assert render_block.index("renderNationalTable()") < render_block.index(
        "renderNationalDrilldown()"
    )
    assert render_block.index("renderNationalDrilldown()") < render_block.index(
        "renderNationalDiagnostic()"
    )


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
