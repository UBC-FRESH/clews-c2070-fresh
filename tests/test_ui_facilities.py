"""Contract tests for the read-only Facilities view and its static export."""

from __future__ import annotations

import csv
import hashlib
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from workflows.ui import export_facilities_ui as exporter

BUNDLE = ROOT / "ui/public/data/facilities"
CAPACITY = ROOT / "data/derived/national/facilities/national_facility_capacity_v3"
ASSIGNMENT = ROOT / "data/derived/national/facilities/national_facility_assignment_v10"
CHAIN = ROOT / "data/derived/national/facilities/national_facility_chain_scenarios_v9"
PRODUCT_CHAIN = ROOT / "data/derived/national/facilities/national_product_chain_v5"
RESIDUE = ROOT / "data/derived/national/facilities/facility_residue_supply_v9"
CURVE = ROOT / "data/derived/national/facilities/national_residue_curve_v9"
HANDOFF = ROOT / "data/derived/national/facilities/forestry_osecan_handoff_v10"
SCENARIOS = ROOT / "data/derived/national/facilities/forestry_osecan_scenarios_v9"
V7_CHAIN = ROOT / "data/derived/national/facilities/national_facility_chain_scenarios_v7"
APP_JS = ROOT / "ui/app.js"
INDEX_HTML = ROOT / "ui/index.html"
STYLES_CSS = ROOT / "ui/styles.css"


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


@pytest.fixture(scope="module")
def payload() -> dict:
    return json.loads((BUNDLE / "facilities.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads((BUNDLE / "manifest.json").read_text(encoding="utf-8"))


def test_export_has_689_typed_records(payload: dict) -> None:
    assert payload["schema_version"] == "facilities/2"
    assert payload["artifact"] == "ui_facilities_v2"
    assert payload["labels"] == {
        "evidence": "model_estimate",
        "observed": False,
        "policy_grade": False,
        "ranking": "none",
        "official_osecan": False,
        "read_only": True,
    }
    assert payload["coverage"]["record_count"] == len(payload["facilities"]) == 689
    assert len({row["id"] for row in payload["facilities"]}) == 689
    assert payload["coverage"]["class_count"] == 6
    assert payload["coverage"]["province_count"] == 11
    assert sum(item["record_count"] for item in payload["classes"]) == 689
    assert sum(item["record_count"] for item in payload["provinces"]) == 689
    assert [item["id"] for item in payload["classes"]] == list(exporter.CLASS_ORDER)
    for row in payload["facilities"]:
        assert row["facility_class"] in exporter.CLASS_ORDER
        assert row["province"] in exporter.PROVINCE_LABELS
        assert row["capacity_status"] in {"published", "proxy", "not_published"}
        assert row["mapped"] is (row["lat"] is not None and row["lon"] is not None)
        if row["mapped"]:
            assert -141.0 <= row["lon"] <= -52.0
            assert 41.0 <= row["lat"] <= 84.0
        else:
            assert row["lat"] is None and row["lon"] is None


def test_map_counts_and_unlocated_records(payload: dict) -> None:
    coverage = payload["coverage"]
    mapped = [row for row in payload["facilities"] if row["mapped"]]
    unlocated = [row for row in payload["facilities"] if not row["mapped"]]
    assert len(mapped) == coverage["mapped_count"] == 608
    assert len(unlocated) == coverage["unlocated_count"] == 81
    assert len(mapped) + len(unlocated) == 689
    assert {row["province"] for row in unlocated} == {"BC"}
    assert coverage["unlocated_provinces"] == ["BC"]
    assert all(row["mapped"] for row in mapped)
    assert "no location is invented" in payload["caveats"]["location"]
    capacity_rows = _read_csv(CAPACITY / "national_facility_capacity_v3.csv")
    assert sum(1 for row in capacity_rows if row["lat"] and row["lon"]) == 608
    assert sum(1 for row in capacity_rows if not row["lat"] and not row["lon"]) == 81


def test_table_counts_and_capacity_match_capacity_v3(payload: dict) -> None:
    counts = json.loads((CAPACITY / "manifest.json").read_text(encoding="utf-8"))["counts"]
    assert payload["coverage"]["published_count"] == counts["published_records"] == 433
    assert payload["coverage"]["proxy_count"] == counts["proxy_records"] == 241
    assert payload["coverage"]["not_published_count"] == counts["not_published_records"] == 15
    assert payload["coverage"]["published_capacity_m3_y"] == pytest.approx(166_608_791.456421, abs=1e-6)
    assert payload["coverage"]["proxy_capacity_m3_y"] == pytest.approx(104_733_008.0, abs=1e-6)
    for province in payload["provinces"]:
        source = [
            row
            for row in _read_csv(CAPACITY / "national_facility_capacity_v3.csv")
            if row["province"] == province["id"]
        ]
        assert province["record_count"] == len(source)
        assert province["published_count"] == sum(1 for row in source if row["capacity_status"] == "published")
        assert province["proxy_count"] == sum(1 for row in source if row["capacity_status"] == "proxy")
    for scenario in payload["scenarios"]:
        assert len(scenario["rows"]) == 38
        assert sum(row["record_count"] for row in scenario["rows"]) == 689
        assert sum(row["published_count"] for row in scenario["rows"]) == 433
        assert sum(row["proxy_count"] for row in scenario["rows"]) == 241
        assert sum(row["not_published_count"] for row in scenario["rows"]) == 15
        assert sum(row["published_capacity_m3_y"] for row in scenario["rows"]) == pytest.approx(
            payload["coverage"]["published_capacity_m3_y"], abs=1e-6
        )
        assert sum(row["proxy_capacity_m3_y"] for row in scenario["rows"]) == pytest.approx(
            payload["coverage"]["proxy_capacity_m3_y"], abs=1e-6
        )


def _v10_case_files(case: str) -> tuple[Path, Path]:
    util = ASSIGNMENT / (
        "v10_facility_utilization.csv" if case == "119m" else f"v10_facility_utilization_{case}.csv"
    )
    province = ASSIGNMENT / (
        "v10_province_summary.csv" if case == "119m" else f"v10_province_summary_{case}.csv"
    )
    return util, province


def test_scenarios_reproduce_assignment_v10(payload: dict) -> None:
    case_summary = {row["scenario"]: row for row in _read_csv(ASSIGNMENT / "v10_case_summary.csv")}
    assert [scenario["id"] for scenario in payload["scenarios"]] == ["100m", "119m", "150m"]
    assert payload["default_scenario"] == "119m"
    assert payload["base_assignment"]["artifact"] == "national_facility_assignment_v10"
    assert payload["base_assignment"]["facilities"] == 296
    assert payload["base_assignment"]["assigned_m3"] == pytest.approx(118_977_339.1, abs=1e-3)
    for scenario in payload["scenarios"]:
        util_path, province_path = _v10_case_files(scenario["id"])
        utilization = _read_csv(util_path)
        provinces = _read_csv(province_path)
        source = case_summary[scenario["id"]]
        assigned = sum(float(row["assigned_m3"]) for row in utilization)
        eligible = sum(float(row["capacity_m3_y"]) for row in utilization)
        assert scenario["source_artifact"] == "national_facility_assignment_v10"
        assert scenario["assigned_m3"] == pytest.approx(assigned, abs=1e-6)
        assert scenario["assigned_m3"] == pytest.approx(float(source["assigned_m3"]), abs=1e-6)
        assert scenario["harvest_m3"] == pytest.approx(float(source["harvest_case_m3"]), abs=1e-6)
        assert scenario["facilities_used"] == int(float(source["facilities_used"]))
        assert len(scenario["facility_assignments"]) == scenario["facilities_used"]
        assert sum(row["assigned_m3_y"] for row in scenario["rows"]) == pytest.approx(assigned, abs=1e-6)
        assert sum(row["eligible_capacity_m3_y"] for row in scenario["rows"]) == pytest.approx(
            eligible, abs=1e-6
        )
        assert scenario["capacity_utilization"] == pytest.approx(assigned / eligible, abs=1e-12)
        assert scenario["capacity_utilization"] == pytest.approx(
            float(source["assigned_m3"]) / eligible, abs=1e-12
        )
        assert sum(float(row["assigned_m3"]) for row in provinces) == pytest.approx(assigned, abs=1e-6)
        assert sum(float(row["capacity_m3_y"]) for row in provinces) == pytest.approx(eligible, abs=1e-6)
        cross_border = sum(float(row["cross_border_assigned_m3"]) for row in utilization)
        assert scenario["cross_border_m3"] == pytest.approx(cross_border, abs=1e-6)
        used = {row["facility_id"] for row in utilization if row["used"] == "true"}
        assert set(scenario["facility_assignments"]) == used
        assert len(used) == scenario["facilities_used"]

    # The 119 Mm3 v10 facility utilization reproduces chain v9 assignment.
    chain_rows = [
        row for row in _read_csv(CHAIN / "scenario_assignment.csv") if row["scenario"] == "119m"
    ]
    aggregated: dict[str, float] = {}
    for row in chain_rows:
        aggregated[row["facility_id"]] = aggregated.get(row["facility_id"], 0.0) + float(row["assigned_m3"])
    scenario_119 = next(item for item in payload["scenarios"] if item["id"] == "119m")
    assert set(scenario_119["facility_assignments"]) == set(aggregated)
    for facility_id, assignment in scenario_119["facility_assignments"].items():
        assert assignment["assigned_m3_y"] == pytest.approx(aggregated[facility_id], abs=1e-6)


def test_scenarios_carry_transport_cost_and_distance(payload: dict) -> None:
    expected = {
        "100m": (2_701_643_306.369928, 255.328366274, 0.456701832, 0.273629432),
        "119m": (3_392_104_138.709088, 270.289409051, 0.486188642, 0.301630753),
        "150m": (5_018_080_790.875671, 319.790277669, 0.559701015, 0.339873097),
    }
    for scenario in payload["scenarios"]:
        cost, distance, ge150, ge300 = expected[scenario["id"]]
        assert scenario["transport_cost_cad"] == pytest.approx(cost, abs=1e-6)
        assert scenario["mean_road_distance_km"] == pytest.approx(distance, abs=1e-6)
        assert scenario["share_ge_150_km"] == pytest.approx(ge150, abs=1e-9)
        assert scenario["share_ge_300_km"] == pytest.approx(ge300, abs=1e-9)
        assert scenario["transport"]["transport_cost_cad"] == pytest.approx(cost, abs=1e-6)
        assert round(scenario["transport_cost_cad"] / 1e9, 3) in (2.702, 3.392, 5.018)
        assert scenario["transport_cost_cad_m3"] > 0
        assert scenario["transport_cost_cad_gj"] > 0
        assert 0 <= scenario["share_ge_150_km"] <= 1
        assert 0 <= scenario["share_ge_300_km"] <= 1


def test_scenarios_keep_capacity_unchanged(payload: dict) -> None:
    eligible = {
        round(sum(row["eligible_capacity_m3_y"] for row in scenario["rows"]), 6)
        for scenario in payload["scenarios"]
    }
    published = {
        round(sum(row["published_capacity_m3_y"] for row in scenario["rows"]), 6)
        for scenario in payload["scenarios"]
    }
    proxy = {
        round(sum(row["proxy_capacity_m3_y"] for row in scenario["rows"]), 6)
        for scenario in payload["scenarios"]
    }
    assert eligible == {273_468_365.081452}
    assert published == {166_608_791.456421}
    assert proxy == {104_733_008.0}
    assert [round(scenario["capacity_utilization"], 6) for scenario in payload["scenarios"]] == [
        0.365673,
        0.435068,
        0.548510,
    ]
    for scenario in payload["scenarios"]:
        assert sum(row["record_count"] for row in scenario["rows"]) == 689


def test_ui_renders_transport_values_from_payload() -> None:
    app = APP_JS.read_text(encoding="utf-8")
    for token in (
        "scenario.transport_cost_cad",
        "scenario.mean_road_distance_km",
        "scenario.share_ge_150_km",
        "scenario.share_ge_300_km",
        "Transport cost",
        "Mean road distance",
        "Share ≥150 km",
        "Share ≥300 km",
        "data.caveats.transport",
    ):
        assert token in app, f"facilities UI is missing {token}"
    html = INDEX_HTML.read_text(encoding="utf-8")
    assert "Coverage, evidence &amp; transport" in html
    assert "assignment-v10" in html
    assert "local-first" not in html[html.index('id="panel-facilities"') : html.index('id="panel-zones"')]


def test_no_stale_v6_assignment_values(payload: dict) -> None:
    serialized = json.dumps(payload)
    assert "national_facility_assignment_v6" not in serialized
    assert "national_facility_chain_scenarios_v6" not in serialized
    assert "local-first" not in serialized
    assert [scenario["facilities_used"] for scenario in payload["scenarios"]] == [270, 296, 333]
    assert [round(scenario["assigned_m3"] / 1e6, 3) for scenario in payload["scenarios"]] == [
        100.0,
        118.977,
        150.0,
    ]
    for source in payload["source_files"]:
        assert "assignment_v6" not in source["artifact"]
        assert "chain_scenarios_v6" not in source["artifact"]
    assert payload["national_chain"]["artifact"] == "national_facility_chain_scenarios_v9"
    assert payload["national_chain"]["assignment"]["artifact"] == "national_facility_assignment_v10"


def test_national_chain_summary_reproduces_v51_csvs(payload: dict) -> None:
    chain = payload["national_chain"]
    assert chain["artifact"] == "national_facility_chain_scenarios_v9"
    assert chain["artifact_version"] == "9.0.0"
    assert chain["assignment"]["artifact"] == "national_facility_assignment_v10"
    assert chain["assignment"]["cases"] == ["100m", "119m", "150m"]
    assert chain["labels"]["transport_cost_in_osecan"] is False
    assert [row["id"] for row in chain["rows"]] == ["100m", "119m", "150m"]
    source_rows = _read_csv(CHAIN / "scenario_summary.csv")
    assert [row["scenario"] for row in source_rows] == ["100m", "119m", "150m"]
    transport_rows = {row["scenario"]: row for row in _read_csv(ASSIGNMENT / "v10_case_summary.csv")}
    v7_rows = {row["scenario"]: row for row in _read_csv(V7_CHAIN / "scenario_summary.csv")}
    expected_rounded = {
        "100m": (31.693, 10.137, 419.238, 373.244, 223.946, 792.482),
        "119m": (37.674, 12.066, 498.751, 444.411, 266.646, 943.162),
        "150m": (46.137, 15.551, 626.865, 573.125, 343.875, 1_199.991),
    }
    expected_transport = {
        "100m": (2_701_643_306.369928, 255.328366274),
        "119m": (3_392_104_138.709088, 270.289409051),
        "150m": (5_018_080_790.875671, 319.790277669),
    }
    for chain_row, source_row in zip(chain["rows"], source_rows, strict=True):
        solid = float(source_row["sawlog_product_m3"]) + float(source_row["panel_product_m3"])
        assert chain_row["harvest_m3"] == pytest.approx(float(source_row["harvest_m3"]), abs=1e-6)
        assert chain_row["solid_m3"] == pytest.approx(solid, abs=1e-6)
        assert chain_row["pulp_odt"] == pytest.approx(float(source_row["pulp_product_odt"]), abs=1e-6)
        assert chain_row["process_pj"] == pytest.approx(
            float(source_row["process_residue_pj"]), abs=1e-6
        )
        assert chain_row["harvest_gross_pj"] == pytest.approx(
            float(source_row["harvest_residue_gross_pj"]), abs=1e-6
        )
        assert chain_row["harvest_recoverable_pj"] == pytest.approx(
            float(source_row["harvest_residue_recoverable_pj"]), abs=1e-6
        )
        assert chain_row["total_gross_pj"] == pytest.approx(
            float(source_row["total_residue_gross_pj"]), abs=1e-6
        )
        assert chain_row["total_gross_pj"] == pytest.approx(
            chain_row["process_pj"] + chain_row["harvest_gross_pj"], abs=1e-6
        )
        transport = transport_rows[chain_row["id"]]
        cost, distance = expected_transport[chain_row["id"]]
        assert chain_row["transport_cost_cad"] == pytest.approx(cost, abs=1e-6)
        assert chain_row["mean_road_distance_km"] == pytest.approx(distance, abs=1e-6)
        assert chain_row["transport_cost_cad"] == pytest.approx(
            float(transport["transport_cost_cad"]), abs=1e-6
        )
        assert chain_row["mean_road_distance_km"] == pytest.approx(
            float(transport["mean_road_distance_km"]), abs=1e-6
        )
        # Products and residue are invariant to the transport-aware assignment.
        v7_row = v7_rows[chain_row["id"]]
        for field in (
            "harvest_m3",
            "sawlog_product_m3",
            "panel_product_m3",
            "pulp_product_odt",
            "process_residue_pj",
            "harvest_residue_gross_pj",
            "harvest_residue_recoverable_pj",
            "total_residue_gross_pj",
        ):
            assert float(source_row[field]) == pytest.approx(float(v7_row[field]), abs=1e-6)
        assert (
            round(chain_row["solid_m3"] / 1e6, 3),
            round(chain_row["pulp_odt"] / 1e6, 3),
            round(chain_row["process_pj"], 3),
            round(chain_row["harvest_gross_pj"], 3),
            round(chain_row["harvest_recoverable_pj"], 3),
            round(chain_row["total_gross_pj"], 3),
        ) == expected_rounded[chain_row["id"]]
    reference = chain["rows"][1]
    assert reference["harvest_m3"] == pytest.approx(118_977_339.1, abs=1e-3)
    assert reference["solid_m3"] / 1e6 == pytest.approx(37.674, abs=5e-4)
    assert reference["pulp_odt"] / 1e6 == pytest.approx(12.066, abs=5e-4)
    assert reference["process_pj"] == pytest.approx(498.751, abs=5e-4)
    assert reference["harvest_gross_pj"] == pytest.approx(444.411, abs=5e-4)
    assert reference["harvest_recoverable_pj"] == pytest.approx(266.646, abs=5e-4)
    assert reference["total_gross_pj"] == pytest.approx(943.162, abs=5e-4)
    assert chain["reference"]["transport_cost_cad"] == pytest.approx(
        expected_transport["119m"][0], abs=1e-6
    )
    assert chain["reference"]["mean_road_distance_km"] == pytest.approx(
        expected_transport["119m"][1], abs=1e-6
    )
    assert [column["id"] for column in chain["columns"]] == [
        "solid",
        "pulp",
        "process",
        "harvest_gross",
        "harvest_recoverable",
        "total_gross",
        "transport_cost",
        "mean_distance",
    ]
    assert chain["columns"][-2]["unit"] == "B CAD/yr"
    assert chain["columns"][-1]["unit"] == "km"


def test_national_chain_tiers_and_provenance_labels(payload: dict) -> None:
    chain = payload["national_chain"]
    assert chain["labels"] == {
        "model_estimate": True,
        "observed": False,
        "policy_grade": False,
        "national_claim": False,
        "official_osecan_changed": False,
        "transport_cost_in_osecan": False,
    }
    provenance = chain["coefficient_provenance"]
    assert provenance["e1"]["label"] == "E1 measured"
    assert provenance["e1"]["count"] == 4
    assert provenance["e1"]["provinces"] == ["BC", "QC", "NB", "YT"]
    assert provenance["e2c"]["label"] == "E2c imputed"
    assert provenance["e2c"]["count"] == 9
    assert provenance["e2c"]["provinces"] == ["AB", "SK", "MB", "ON", "NS", "PE", "NL", "NT", "NU"]
    assert provenance["donor_product_coefficients"] == 0
    assert [tier["case"] for tier in chain["reference"]["tiers"]] == [
        "process_availability_050",
        "harvest_recovery_060",
    ]
    assert [tier["stream"] for tier in chain["reference"]["tiers"]] == ["process", "harvest"]
    assert [tier["tier"] for tier in chain["reference"]["tiers"]] == ["above_T4", "above_T4"]
    assert [tier["fraction"] for tier in chain["reference"]["tiers"]] == [0.5, 0.6]
    assert chain["reference"]["scenario"] == "119m"
    caveat = payload["caveats"]["national_chain"]
    for phrase in (
        "E1 measured",
        "E2c imputed",
        "donor product coefficients = 0",
        "model_estimate",
        "observed=false",
        "policy_grade=false",
        "No official OSeCAN change",
        "no new proxy capacity",
    ):
        assert phrase in caveat
    source_tiers = _read_csv(HANDOFF / "tier_positions_v3_2_vs_v4.csv")
    tiers = {(row["stream"], row["case"]): row["v4_e2c_tier"] for row in source_tiers}
    assert tiers[("process", "process_availability_050")] == "above_T4"
    assert tiers[("harvest", "harvest_recovery_060")] == "above_T4"
    scenario_tiers = _read_csv(SCENARIOS / "tier_positions_v3_2_vs_v4.csv")
    reference_tiers = {
        row["case"]: row["v4_e2c_tier"]
        for row in scenario_tiers
        if row["scenario"] == "119m"
    }
    assert reference_tiers["process_availability_050"] == "above_T4"
    assert reference_tiers["harvest_recovery_060"] == "above_T4"


def test_national_chain_summary_is_data_driven_and_read_only() -> None:
    html = INDEX_HTML.read_text(encoding="utf-8")
    assert 'id="facility-chain-summary"' not in html
    assert 'class="facility-chain-summary"' in html
    assert 'id="facility-chain-title"' in html
    assert 'id="facility-chain-table"' in html
    assert 'id="facility-chain-tiers"' in html
    assert 'id="facility-chain-foot"' in html
    panel = html[html.index('id="panel-facilities"') : html.index('id="panel-zones"')]
    for numeric in (
        "31.693",
        "37.674",
        "46.137",
        "10.137",
        "12.066",
        "15.551",
        "419.238",
        "498.751",
        "626.865",
        "373.244",
        "444.411",
        "573.125",
        "223.946",
        "266.646",
        "343.875",
        "792.482",
        "943.162",
        "1,199.991",
        "2.702",
        "3.392",
        "5.018",
        "255.3",
        "270.3",
        "319.8",
    ):
        assert numeric not in panel, f"facility panel hard-codes {numeric}"
    app = APP_JS.read_text(encoding="utf-8")
    assert "function renderFacilityChain()" in app
    assert "data.national_chain" in app
    assert "facility-chain-table" in app
    assert "facility-tier-badge" in app
    assert "national_facility_chain_scenarios_v9" in app
    assert "national_facility_assignment_v10" in app
    assert "national_facility_chain_scenarios_v7" not in app
    assert "national_facility_assignment_v6" not in app
    chain_code = app[app.index("function renderFacilityChain()") : app.index("function renderFacilityLegend()")]
    for numeric in (
        "31.693",
        "37.674",
        "46.137",
        "498.751",
        "444.411",
        "266.646",
        "943.162",
        "2.702",
        "3.392",
        "5.018",
    ):
        assert numeric not in chain_code, f"renderFacilityChain hard-codes {numeric}"
    assert "fetch(" not in chain_code
    assert "apiFetch" not in chain_code
    assert ".solve" not in chain_code


def test_national_chain_summary_manifest_hashes_sources(payload: dict, manifest: dict) -> None:
    assert len(manifest["sources"]) == len(payload["source_files"]) == 33
    paths = {source["path"] for source in payload["source_files"]}
    expected = {
        (CHAIN / "scenario_summary.csv").relative_to(ROOT).as_posix(),
        (CHAIN / "summary.json").relative_to(ROOT).as_posix(),
        (CHAIN / "manifest.json").relative_to(ROOT).as_posix(),
        (CHAIN / "v5_v6_product_residue_invariance.csv").relative_to(ROOT).as_posix(),
        (ASSIGNMENT / "v10_case_summary.csv").relative_to(ROOT).as_posix(),
        (ASSIGNMENT / "summary.json").relative_to(ROOT).as_posix(),
        (ASSIGNMENT / "manifest.json").relative_to(ROOT).as_posix(),
        (ASSIGNMENT / "v10_facility_utilization.csv").relative_to(ROOT).as_posix(),
        (ASSIGNMENT / "v10_facility_utilization_100m.csv").relative_to(ROOT).as_posix(),
        (ASSIGNMENT / "v10_facility_utilization_150m.csv").relative_to(ROOT).as_posix(),
        (ASSIGNMENT / "v10_province_summary.csv").relative_to(ROOT).as_posix(),
        (ASSIGNMENT / "v10_province_summary_100m.csv").relative_to(ROOT).as_posix(),
        (ASSIGNMENT / "v10_province_summary_150m.csv").relative_to(ROOT).as_posix(),
        (PRODUCT_CHAIN / "summary.json").relative_to(ROOT).as_posix(),
        (PRODUCT_CHAIN / "manifest.json").relative_to(ROOT).as_posix(),
        (PRODUCT_CHAIN / "e1_coefficient_replacement.csv").relative_to(ROOT).as_posix(),
        (PRODUCT_CHAIN / "e2c_density_evidence.csv").relative_to(ROOT).as_posix(),
        (RESIDUE / "national_residue_summary.csv").relative_to(ROOT).as_posix(),
        (RESIDUE / "summary.json").relative_to(ROOT).as_posix(),
        (RESIDUE / "manifest.json").relative_to(ROOT).as_posix(),
        (CURVE / "national_residue_curve.csv").relative_to(ROOT).as_posix(),
        (CURVE / "summary.json").relative_to(ROOT).as_posix(),
        (CURVE / "manifest.json").relative_to(ROOT).as_posix(),
        (HANDOFF / "tier_positions_v3_2_vs_v4.csv").relative_to(ROOT).as_posix(),
        (HANDOFF / "summary.json").relative_to(ROOT).as_posix(),
        (HANDOFF / "manifest.json").relative_to(ROOT).as_posix(),
        (SCENARIOS / "tier_positions_v3_2_vs_v4.csv").relative_to(ROOT).as_posix(),
        (SCENARIOS / "summary.json").relative_to(ROOT).as_posix(),
        (SCENARIOS / "manifest.json").relative_to(ROOT).as_posix(),
    }
    assert expected.issubset(paths)
    for source in payload["source_files"]:
        if source["path"] in expected:
            path = ROOT / source["path"]
            assert path.is_file()
            assert source["bytes"] == path.stat().st_size
            assert source["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_province_and_class_filters_conserve_records(payload: dict) -> None:
    scenario = next(item for item in payload["scenarios"] if item["id"] == "119m")
    capacity_rows = _read_csv(CAPACITY / "national_facility_capacity_v3.csv")
    for province in ["BC", "QC", "AB", "ON", "YT"]:
        expected = sum(1 for row in capacity_rows if row["province"] == province)
        filtered = [row for row in scenario["rows"] if row["province"] == province]
        assert sum(row["record_count"] for row in filtered) == expected
        for row in filtered:
            assert row["province"] == province
    for class_id in exporter.CLASS_ORDER:
        expected = sum(1 for row in capacity_rows if row["facility_class"] == class_id)
        filtered = [row for row in scenario["rows"] if row["facility_class"] == class_id]
        assert sum(row["record_count"] for row in filtered) == expected
        for row in filtered:
            assert row["facility_class"] == class_id
    bc_saw = [
        row
        for row in scenario["rows"]
        if row["province"] == "BC" and row["facility_class"] == "SAW"
    ]
    assert sum(row["record_count"] for row in bc_saw) == 194
    assert bc_saw[0]["assigned_m3_y"] > 0
    assert bc_saw[0]["published_count"] == 121
    assert bc_saw[0]["proxy_count"] == 73
    assert bc_saw[0]["unlocated_count"] == 61


def test_manifest_hashes_cover_payload_and_sources(payload: dict, manifest: dict) -> None:
    assert manifest["schema_version"] == "facilities/2"
    assert manifest["artifact"] == "ui_facilities_v2"
    assert manifest["validation"]["ok"] is True
    generated = manifest["generated"]
    assert len(generated) == 1
    assert generated[0]["path"] == "facilities/facilities.json"
    data = (BUNDLE / "facilities.json").read_bytes()
    assert generated[0]["bytes"] == len(data)
    assert generated[0]["sha256"] == hashlib.sha256(data).hexdigest()
    assert len(manifest["sources"]) == len(payload["source_files"]) == 33
    for source in manifest["sources"]:
        path = ROOT / source["path"]
        assert path.is_file()
        assert source["bytes"] == path.stat().st_size
        assert source["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert manifest["labels"] == payload["labels"]


def test_export_is_not_stale_against_source_csvs(payload: dict) -> None:
    fresh = exporter.build_payload(ROOT)
    committed = {key: value for key, value in payload.items() if key != "validation"}
    regenerated = {key: value for key, value in fresh.items() if key != "validation"}
    committed.pop("generated_utc", None)
    regenerated.pop("generated_utc", None)
    assert committed == regenerated


def test_facilities_ui_is_single_purpose_and_read_only(payload: dict) -> None:
    html = INDEX_HTML.read_text(encoding="utf-8")
    assert 'id="tab-facilities"' in html
    assert 'data-tab="facilities"' in html
    assert 'id="panel-facilities"' in html
    assert 'id="facility-map"' in html
    assert 'id="facilities-table"' in html
    panel = html[html.index('id="panel-facilities"') : html.index('id="panel-zones"')]
    for numeric in ("689", "608", "433", "241", "166.61", "104.73", "118.977"):
        assert numeric not in panel, f"facility panel hard-codes {numeric}"
    ids = re.findall(r'id="([^"]+)"', panel)
    assert not [item for item in ids if item.startswith(("run-", "osecan-", "lp-"))]
    assert 'id="facility-scenario"' in panel
    assert 'id="facility-province"' in panel
    assert 'id="facility-class"' in panel
    assert 'id="facility-reset"' in panel

    app = APP_JS.read_text(encoding="utf-8")
    assert "facilities: {" in app
    assert "FACILITY_DATA_PATH = 'facilities/facilities.json'" in app
    assert "FACILITY_MANIFEST_PATH = 'facilities/manifest.json'" in app
    assert "'facilities'" in app
    assert "if (state.tab === 'facilities') initFacilities();" in app
    assert "function applyFacilityMapFilter()" in app
    assert "facility-province" in app and "facility-class" in app
    facility_code = app[app.index("/* ---------------- Facilities ---------------- */") : app.index("/* ---------------- Boot ---------------- */")]
    assert "fetch(" not in facility_code
    assert "apiFetch" not in facility_code
    assert "client." not in facility_code
    assert ".solve" not in facility_code
    assert "runId" not in facility_code
    assert payload["caveats"]["no_ranking"].startswith("This ledger")


def test_mobile_facility_table_stacks_without_horizontal_scroll() -> None:
    app = APP_JS.read_text(encoding="utf-8")
    for label in ("Province", "Class", "Records", "Published capacity", "Proxy capacity", "Assigned", "Utilization", "Evidence"):
        assert f'data-label="{label}' in app
    assert "Swipe the table sideways" not in app
    css = STYLES_CSS.read_text(encoding="utf-8")
    marker = "/* Mobile stacked facility ledger: no horizontal table scroll. */"
    assert marker in css
    block = css[css.index(marker) : css.index("@media (max-width: 440px)", css.index(marker))]
    assert ".table-wrap.facilities-table-wrap" in block
    assert "overflow: visible" in block
    assert ".facilities-table thead { display: none; }" in block
    assert "grid-template-areas:" in block
    assert '"province class"' in block
    assert '"records assigned"' in block
    assert '"utilization utilization"' in block
    assert '"published proxy"' in block
    assert '"evidence evidence"' in block
    assert "min-width: 0" in block
    assert "min-width: 780px" not in block


def test_mobile_national_chain_summary_stacks_without_horizontal_scroll() -> None:
    css = STYLES_CSS.read_text(encoding="utf-8")
    marker = "/* Mobile stacked national chain summary: no horizontal table scroll. */"
    assert marker in css
    block = css[css.index(marker) : css.index("@media (max-width: 440px)", css.index(marker))]
    assert ".table-wrap.facility-chain-table-wrap" in block
    assert "overflow: visible" in block
    assert ".facility-chain-table thead { display: none; }" in block
    assert "grid-template-columns: repeat(2, minmax(0, 1fr));" in block
    assert "td[data-label]::before" in block
    assert "min-width: 0" in block
    assert "min-width: 760px" not in block
