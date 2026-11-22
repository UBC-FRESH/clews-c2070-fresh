"""Regression tests for the national facility assignment v3 artifact."""

from __future__ import annotations

import csv
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path

import pytest

from workflows.national.facilities import (
    national_facility_assignment_v3 as assignment,
)

OUTPUT_DIR = assignment.OUTPUT_DIR
ASSIGNMENT_CSV = OUTPUT_DIR / "facility_assignment.csv"
PROVINCE_CSV = OUTPUT_DIR / "province_summary.csv"
UTILIZATION_CSV = OUTPUT_DIR / "facility_utilization.csv"
UNASSIGNED_CSV = OUTPUT_DIR / "unassigned.csv"
PROVINCE_CLASS_CSV = OUTPUT_DIR / "province_class_totals.csv"
CONCENTRATION_CSV = OUTPUT_DIR / "concentration.csv"
COMPARISON_CSV = OUTPUT_DIR / "scenario_comparison.csv"
FLOOR_CSV = OUTPUT_DIR / "capacity_floor.csv"
FLOOR_FACILITIES_CSV = OUTPUT_DIR / "capacity_floor_facilities.csv"
V2_V3_CSV = OUTPUT_DIR / "v2_v3_comparison.csv"
SUMMARY_JSON = OUTPUT_DIR / "summary.json"
MANIFEST_JSON = OUTPUT_DIR / "manifest.json"
REPORT_MD = OUTPUT_DIR / "NATIONAL_FACILITY_ASSIGNMENT_V3.md"
CAPACITY_CSV = assignment.CAPACITY_CSV

TOTAL_M3 = 118_977_339.1


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


@pytest.fixture(scope="module")
def assignments() -> list[dict[str, str]]:
    return _read_csv(ASSIGNMENT_CSV)


@pytest.fixture(scope="module")
def province_summary() -> list[dict[str, str]]:
    return _read_csv(PROVINCE_CSV)


@pytest.fixture(scope="module")
def utilization() -> list[dict[str, str]]:
    return _read_csv(UTILIZATION_CSV)


@pytest.fixture(scope="module")
def unassigned() -> list[dict[str, str]]:
    return _read_csv(UNASSIGNED_CSV)


@pytest.fixture(scope="module")
def province_class() -> list[dict[str, str]]:
    return _read_csv(PROVINCE_CLASS_CSV)


@pytest.fixture(scope="module")
def concentration() -> list[dict[str, str]]:
    return _read_csv(CONCENTRATION_CSV)


@pytest.fixture(scope="module")
def comparison() -> list[dict[str, str]]:
    return _read_csv(COMPARISON_CSV)


@pytest.fixture(scope="module")
def floor_rows() -> list[dict[str, str]]:
    return _read_csv(FLOOR_CSV)


@pytest.fixture(scope="module")
def floor_facilities() -> list[dict[str, str]]:
    return _read_csv(FLOOR_FACILITIES_CSV)


@pytest.fixture(scope="module")
def v2_v3() -> list[dict[str, str]]:
    return _read_csv(V2_V3_CSV)


@pytest.fixture(scope="module")
def summary() -> dict:
    with SUMMARY_JSON.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _rows_for(rows: list[dict[str, str]], scenario: str) -> list[dict[str, str]]:
    return [row for row in rows if row["scenario"] == scenario]


def test_outputs_exist_and_schema(
    assignments,
    province_summary,
    utilization,
    unassigned,
    province_class,
    concentration,
    comparison,
    floor_rows,
    floor_facilities,
    v2_v3,
    summary,
):
    for path in (
        ASSIGNMENT_CSV,
        PROVINCE_CSV,
        UTILIZATION_CSV,
        UNASSIGNED_CSV,
        PROVINCE_CLASS_CSV,
        CONCENTRATION_CSV,
        COMPARISON_CSV,
        FLOOR_CSV,
        FLOOR_FACILITIES_CSV,
        V2_V3_CSV,
        SUMMARY_JSON,
        MANIFEST_JSON,
        REPORT_MD,
    ):
        assert path.exists(), path
    assert assignments
    assert province_summary
    assert utilization
    assert province_class
    assert concentration
    assert comparison
    assert len(floor_rows) == len(assignment.PROVINCES)
    assert floor_facilities
    assert v2_v3
    assert list(assignments[0]) == assignment.ASSIGNMENT_FIELDS
    assert list(province_summary[0]) == assignment.PROVINCE_SUMMARY_FIELDS
    assert list(utilization[0]) == assignment.UTILIZATION_FIELDS
    assert list(unassigned[0]) == assignment.UNASSIGNED_FIELDS
    assert list(province_class[0]) == assignment.PROVINCE_CLASS_FIELDS
    assert list(concentration[0]) == assignment.CONCENTRATION_FIELDS
    assert list(comparison[0]) == assignment.COMPARISON_FIELDS
    assert list(floor_rows[0]) == assignment.FLOOR_FIELDS
    assert list(floor_facilities[0]) == assignment.FLOOR_FACILITY_FIELDS
    assert list(v2_v3[0]) == assignment.V2_V3_FIELDS
    assert summary["artifact"] == assignment.ARTIFACT
    assert summary["artifact_version"] == assignment.ARTIFACT_VERSION
    assert set(summary["scenarios"]) == set(assignment.SCENARIOS)


def test_labels_and_caveats(
    assignments, province_summary, utilization, floor_rows, floor_facilities, summary
):
    for rows in (
        assignments,
        province_summary,
        utilization,
        floor_rows,
        floor_facilities,
    ):
        for row in rows:
            assert row["evidence_label"] == "model_estimate"
            assert row["observed"] == "false"
            assert row["policy_grade"] == "false"
    assert summary["labels"] == {
        "evidence_label": "model_estimate",
        "observed": False,
        "policy_grade": False,
    }
    text = " ".join(summary["caveats"])
    assert "legal/AAC" in text
    assert "1.2x harvest" in text
    assert "proxy capacities only" in text
    assert "not evidence" in text
    assert "No UI or public OSeCAN" in text


def test_floor_arithmetic(floor_rows, summary):
    for row in floor_rows:
        harvest = float(row["harvest_m3"])
        floor = float(row["floor_capacity_m3_y"])
        published = float(row["published_capacity_m3_y"])
        proxy_before = float(row["proxy_capacity_before_m3_y"])
        total_before = float(row["total_capacity_before_m3_y"])
        scale = float(row["proxy_scale_factor"])
        proxy_after = float(row["proxy_capacity_after_m3_y"])
        total_after = float(row["total_capacity_after_m3_y"])
        shortfall = float(row["capacity_shortfall_m3_y"])
        assert floor == pytest.approx(
            harvest * assignment.FLOOR_MULTIPLIER, abs=0.01
        )
        assert total_before == pytest.approx(published + proxy_before, abs=0.01)
        assert proxy_after == pytest.approx(proxy_before * scale, abs=0.01)
        assert total_after == pytest.approx(published + proxy_after, abs=0.01)
        assert shortfall == pytest.approx(max(0.0, floor - total_after), abs=0.01)
        assert scale >= 1.0
        if row["capacity_floor_status"] == "proxy_scaled_to_floor":
            assert total_after == pytest.approx(floor, abs=1e-6)
        if row["capacity_floor_status"] == "shortfall_no_proxy_capacity":
            assert proxy_before == 0.0
            assert scale == 1.0
            assert shortfall > 0.0
        assert row["capacity_floor_status"] in {
            "floor_met_no_scaling",
            "proxy_scaled_to_floor",
            "shortfall_no_proxy_capacity",
            "shortfall_after_scaling",
        }
    floor_summary = summary["province_capacity_floor"]
    assert floor_summary["shortfall_before_m3_y"] == pytest.approx(
        sum(
            max(
                0.0,
                float(row["floor_capacity_m3_y"])
                - float(row["total_capacity_before_m3_y"]),
            )
            for row in floor_rows
        ),
        abs=0.01,
    )
    assert floor_summary["shortfall_after_m3_y"] == pytest.approx(
        sum(float(row["capacity_shortfall_m3_y"]) for row in floor_rows),
        abs=0.01,
    )
    assert floor_summary["provinces_below_floor_after"] == sum(
        1 for row in floor_rows if float(row["capacity_shortfall_m3_y"]) > 0.0
    )


def test_published_capacity_is_unchanged(floor_facilities, summary):
    published_source = {
        row["facility_id"]: float(row["capacity_m3_y"])
        for row in _read_csv(CAPACITY_CSV)
        if row["capacity_status"] == "published"
    }
    assert len(published_source) == 179
    published_floor = [
        row
        for row in floor_facilities
        if row["capacity_status_before"] == "published"
    ]
    assert len(published_floor) == len(published_source)
    for row in published_floor:
        facility_id = row["facility_id"]
        assert float(row["capacity_m3_y_before"]) == pytest.approx(
            published_source[facility_id], abs=1e-6
        )
        assert float(row["capacity_m3_y_after"]) == pytest.approx(
            published_source[facility_id], abs=1e-6
        )
        assert float(row["floor_scale_factor"]) == 1.0
        assert row["capacity_status_after"] == "published"
        assert row["capacity_evidence_after"] == assignment.PUBLISHED_EVIDENCE
    assert summary["validation"]["published_capacity_unchanged"] is True


def test_proxy_scaling_is_labelled_and_proportional(
    floor_rows, floor_facilities, summary
):
    scale_by_province = {
        row["province"]: float(row["proxy_scale_factor"]) for row in floor_rows
    }
    scaled = [
        row
        for row in floor_facilities
        if row["capacity_status_after"] == assignment.SCALED_PROXY
    ]
    scaled_provinces = {
        row["province"]
        for row in floor_rows
        if row["capacity_floor_status"] == "proxy_scaled_to_floor"
    }
    expected_scaled = sum(
        int(row["proxy_records_scaled"]) for row in floor_rows
    )
    assert len(scaled) == expected_scaled > 0
    assert {row["province"] for row in scaled} == scaled_provinces
    for row in scaled:
        factor = scale_by_province[row["province"]]
        assert factor > 1.0
        assert float(row["floor_scale_factor"]) == pytest.approx(
            factor, abs=1e-6
        )
        assert row["capacity_status_before"] == "proxy"
        assert row["capacity_evidence_before"] == assignment.PROXY_EVIDENCE
        assert (
            row["capacity_evidence_after"]
            == assignment.SCALED_PROXY_EVIDENCE
        )
        assert float(row["capacity_m3_y_after"]) == pytest.approx(
            float(row["capacity_m3_y_before"]) * factor, abs=0.01
        )
        assert float(row["proxy_low_m3_y_after"]) == pytest.approx(
            float(row["proxy_low_m3_y_before"]) * factor, abs=0.01
        )
        assert float(row["proxy_high_m3_y_after"]) == pytest.approx(
            float(row["proxy_high_m3_y_before"]) * factor, abs=0.01
        )
        assert float(row["proxy_low_m3_y_after"]) <= float(
            row["capacity_m3_y_after"]
        )
        assert float(row["capacity_m3_y_after"]) <= float(
            row["proxy_high_m3_y_after"]
        )
    unscaled_proxies = [
        row
        for row in floor_facilities
        if row["capacity_status_before"] == "proxy"
        and row["capacity_status_after"] == "proxy"
    ]
    for row in unscaled_proxies:
        assert float(row["floor_scale_factor"]) == 1.0
        assert float(row["capacity_m3_y_after"]) == pytest.approx(
            float(row["capacity_m3_y_before"]), abs=1e-6
        )
    assert summary["validation"]["scaled_proxy_records"] == len(scaled)
    assert summary["validation"]["floor_shortfall_after_m3_y"] == pytest.approx(
        sum(float(row["capacity_shortfall_m3_y"]) for row in floor_rows),
        abs=0.01,
    )


def test_conservation_in_all_scenarios(
    assignments, province_summary, unassigned, summary
):
    for scenario in assignment.SCENARIOS:
        scenario_assignments = _rows_for(assignments, scenario)
        scenario_summary = _rows_for(province_summary, scenario)
        scenario_unassigned = _rows_for(unassigned, scenario)
        assigned_total = sum(
            float(row["assigned_m3"]) for row in scenario_assignments
        )
        unassigned_total = sum(
            float(row["unassigned_m3"]) for row in scenario_unassigned
        )
        assert assigned_total + unassigned_total == pytest.approx(
            TOTAL_M3, abs=0.01
        )
        assert sum(
            float(row["offer_m3"]) for row in scenario_summary
        ) == pytest.approx(TOTAL_M3, abs=0.01)
        for row in scenario_summary:
            assert float(row["assigned_m3"]) + float(
                row["unassigned_m3"]
            ) == pytest.approx(float(row["offer_m3"]), abs=0.01)
        by_province = defaultdict(float)
        for row in scenario_assignments:
            by_province[row["province"]] += float(row["assigned_m3"])
        for row in scenario_summary:
            assert by_province[row["province"]] == pytest.approx(
                float(row["assigned_m3"]), abs=0.01
            )
        assert summary["validation"]["conservation_gap_m3"][scenario] == (
            pytest.approx(0.0, abs=0.01)
        )


def test_every_assignment_is_finite_and_bounded(assignments, utilization, summary):
    for row in assignments:
        capacity = float(row["capacity_m3_y"])
        assigned = float(row["assigned_m3"])
        assert capacity > 0.0
        assert math.isfinite(capacity)
        assert assigned > 0.0
        assert assigned <= capacity + 1e-6
        assert row["capacity_status"] in assignment.CAPACITY_STATUSES
        assert row["remaining_capacity_m3"] != ""
        assert float(row["utilization"]) <= 1.0 + 1e-9
        assert row["capacity_evidence"]
        assert float(row["floor_scale_factor"]) >= 1.0
    for row in utilization:
        if row["capacity_status"] in assignment.CAPACITY_STATUSES:
            assert row["capacity_m3_y"] != ""
            assert row["capacity_evidence"]
        if row["utilization"] != "":
            assert 0.0 <= float(row["utilization"]) <= 1.0 + 1e-9
    assert summary["validation"]["known_capacity_violations"] == 0
    assert summary["validation"]["unbounded_assignments"] == 0
    assert summary["validation"]["proxy_range_violations"] == 0
    assert not any("unbounded" in row["capacity_status"] for row in assignments)


def test_floor_scenario_is_local_first_with_only_nt_cross_border(
    assignments, province_summary, floor_rows, summary
):
    floor_assignments = _rows_for(
        assignments, assignment.FLOOR_SCENARIO
    )
    assert floor_assignments
    cross_border = [
        row for row in floor_assignments if row["cross_border"] == "true"
    ]
    assert cross_border
    assert {row["province"] for row in cross_border} == {"NT"}
    assert all(row["facility_province"] != "NT" for row in cross_border)
    floor_summary = _rows_for(province_summary, assignment.FLOOR_SCENARIO)
    total_cross = sum(
        float(row["cross_border_assigned_m3"]) for row in floor_summary
    )
    assert total_cross == pytest.approx(
        summary["scenarios"][assignment.FLOOR_SCENARIO]["cross_border_m3"],
        abs=0.01,
    )
    nt_floor = next(row for row in floor_rows if row["province"] == "NT")
    assert nt_floor["capacity_floor_status"] == "shortfall_no_proxy_capacity"
    assert float(nt_floor["capacity_shortfall_m3_y"]) > 0.0
    assert summary["validation"]["floor_cross_border_unexpected_m3_y"] == 0.0
    for row in floor_summary:
        if row["province"] in {"NT", "NU"}:
            continue
        assert float(row["cross_border_assigned_m3"]) == 0.0
        assert float(row["local_assigned_m3"]) == pytest.approx(
            float(row["assigned_m3"]), abs=1e-6
        )


def test_v3_reproduces_v2_before_floor_scaling(summary):
    v2_metrics = summary["v2_baseline"]
    v3_metrics = summary["scenarios"]["published_plus_proxy"]
    for field in (
        "assigned_m3",
        "unassigned_m3",
        "published_m3",
        "proxy_m3",
        "cross_border_m3",
        "max_facility_share",
        "hhi",
    ):
        assert v3_metrics[field] == pytest.approx(v2_metrics[field], abs=0.01)
    assert summary["validation"]["v2_reproduction_pass"] is True


def test_v2_v3_comparison_is_filled_and_reconciles(v2_v3, summary):
    rows = {row["metric"]: row for row in v2_v3}
    expected = {
        "assigned_m3",
        "unassigned_m3",
        "cross_border_m3",
        "cross_border_share",
        "proxy_share_of_assigned",
        "max_facility_share",
        "hhi",
        "province_capacity_shortfall_after_m3_y",
        "provinces_below_floor_after",
        "scaled_proxy_records",
    }
    assert expected <= set(rows)
    v2_metrics = summary["v2_baseline"]
    v3_metrics = summary["scenarios"][assignment.FLOOR_SCENARIO]
    for metric in (
        "assigned_m3",
        "unassigned_m3",
        "cross_border_m3",
        "cross_border_share",
        "proxy_share_of_assigned",
        "max_facility_share",
        "hhi",
    ):
        row = rows[metric]
        assert float(row["v2_value"]) == pytest.approx(
            v2_metrics[metric], abs=0.01
        )
        assert float(row["v3_value"]) == pytest.approx(
            v3_metrics[metric], abs=1e-6
        )
        assert float(row["delta_v3_minus_v2"]) == pytest.approx(
            v3_metrics[metric] - v2_metrics[metric], abs=0.01
        )
    assert float(rows["cross_border_m3"]["v3_value"]) < float(
        rows["cross_border_m3"]["v2_value"]
    )
    assert float(rows["cross_border_share"]["v3_value"]) < float(
        rows["cross_border_share"]["v2_value"]
    )
    assert float(rows["province_capacity_shortfall_after_m3_y"]["v3_value"]) < (
        float(rows["province_capacity_shortfall_after_m3_y"]["v2_value"])
    )
    assert int(rows["provinces_below_floor_after"]["v3_value"]) == 1
    assert int(rows["provinces_below_floor_after"]["v2_value"]) == 5


def test_concentration_reconciles(concentration, summary):
    for scenario in assignment.SCENARIOS:
        rows = _rows_for(concentration, scenario)
        assert [int(row["rank"]) for row in rows] == list(
            range(1, len(rows) + 1)
        )
        shares = [float(row["share_of_assigned"]) for row in rows]
        assert sum(shares) == pytest.approx(1.0, abs=1e-4)
        assert float(rows[-1]["cumulative_share"]) == pytest.approx(
            1.0, abs=1e-6
        )
        assert summary["concentration"][scenario]["hhi"] == pytest.approx(
            sum(share * share for share in shares), abs=1e-6
        )
        assert summary["concentration"][scenario]["top1_share"] == (
            pytest.approx(max(shares), abs=1e-6)
        )
        for row in rows:
            assert row["capacity_status"] in assignment.CAPACITY_STATUSES
            assert row["capacity_evidence"]


def test_floor_scenario_uses_scaled_proxy_capacity(assignments):
    floor_rows = _rows_for(assignments, assignment.FLOOR_SCENARIO)
    scaled = [
        row
        for row in floor_rows
        if row["capacity_status"] == assignment.SCALED_PROXY
    ]
    assert scaled
    for row in scaled:
        assert row["capacity_evidence"] == assignment.SCALED_PROXY_EVIDENCE
        assert float(row["floor_scale_factor"]) > 1.0
        assert float(row["utilization"]) <= 1.0 + 1e-9
    published = [
        row for row in floor_rows if row["capacity_status"] == "published"
    ]
    assert published
    assert all(float(row["floor_scale_factor"]) == 1.0 for row in published)


def test_manifest_hashes_and_inputs(summary):
    with MANIFEST_JSON.open("r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    assert manifest["artifact"] == assignment.ARTIFACT
    assert manifest["artifact_version"] == assignment.ARTIFACT_VERSION
    for name, metadata in manifest["outputs"].items():
        path = OUTPUT_DIR / name
        assert path.exists(), name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == metadata["sha256"]
        assert path.stat().st_size == metadata["bytes"]
    assert manifest["inputs"] == summary["inputs"]
    joined = " ".join(manifest["inputs"])
    assert "national_facility_capacity_v1.csv" in joined
    assert "national_facilities.csv" in joined
    assert "119m.csv" in joined
    assert "national_facility_assignment_v2/summary.json" in joined
    assert manifest["validation"]["floor_arithmetic_pass"] is True


def test_report_has_required_sections(summary):
    method = summary["method"]
    assert method["cross_border_penalty_km"] == assignment.CROSS_BORDER_PENALTY_KM
    assert method["road_factor"] == assignment.ROAD_FACTOR
    assert method["local_first"] is True
    assert (
        method["province_capacity_floor"]["multiplier"]
        == assignment.FLOOR_MULTIPLIER
    )
    assert "no_unbounded_facility" in method
    report = REPORT_MD.read_text(encoding="utf-8")
    for phrase in (
        "Floor scaling",
        "Assignment v2 vs v3",
        "Cross-border and proxy concentration",
        "Province totals",
        "model_estimate",
        "observed=false",
        "policy_grade=false",
        "legal/AAC",
        "capacity",
    ):
        assert phrase in report
