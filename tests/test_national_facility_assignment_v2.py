"""Regression tests for the national facility assignment v2 artifact."""

from __future__ import annotations

import csv
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path

import pytest

from workflows.national.facilities import (
    national_facility_assignment_v2 as assignment,
)

OUTPUT_DIR = assignment.OUTPUT_DIR
ASSIGNMENT_CSV = OUTPUT_DIR / "facility_assignment.csv"
PROVINCE_CSV = OUTPUT_DIR / "province_summary.csv"
UTILIZATION_CSV = OUTPUT_DIR / "facility_utilization.csv"
UNASSIGNED_CSV = OUTPUT_DIR / "unassigned.csv"
PROVINCE_CLASS_CSV = OUTPUT_DIR / "province_class_totals.csv"
CONCENTRATION_CSV = OUTPUT_DIR / "concentration.csv"
COMPARISON_CSV = OUTPUT_DIR / "scenario_comparison.csv"
SUMMARY_JSON = OUTPUT_DIR / "summary.json"
MANIFEST_JSON = OUTPUT_DIR / "manifest.json"
REPORT_MD = OUTPUT_DIR / "NATIONAL_FACILITY_ASSIGNMENT_V2.md"

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
    assert list(assignments[0]) == assignment.ASSIGNMENT_FIELDS
    assert list(province_summary[0]) == assignment.PROVINCE_SUMMARY_FIELDS
    assert list(utilization[0]) == assignment.UTILIZATION_FIELDS
    assert list(unassigned[0]) == assignment.UNASSIGNED_FIELDS
    assert list(province_class[0]) == assignment.PROVINCE_CLASS_FIELDS
    assert list(concentration[0]) == assignment.CONCENTRATION_FIELDS
    assert list(comparison[0]) == assignment.COMPARISON_FIELDS
    assert summary["artifact"] == assignment.ARTIFACT
    assert summary["artifact_version"] == assignment.ARTIFACT_VERSION


def test_labels_are_consistent(
    assignments, province_summary, utilization, unassigned, summary
):
    for rows in (assignments, province_summary, utilization, unassigned):
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
    assert "not a legal AAC" in text
    assert "No UI or public OSeCAN" in text


def test_conservation_in_both_scenarios(
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


def test_every_assignment_has_finite_capacity_and_is_bounded(
    assignments, utilization, summary
):
    for row in assignments:
        capacity = float(row["capacity_m3_y"])
        assigned = float(row["assigned_m3"])
        assert capacity > 0.0
        assert math.isfinite(capacity)
        assert assigned > 0.0
        assert assigned <= capacity + 1e-6
        assert row["capacity_status"] in {"published", "proxy"}
        assert row["remaining_capacity_m3"] != ""
        assert float(row["utilization"]) <= 1.0 + 1e-9
    for row in utilization:
        if row["capacity_status"] in {"published", "proxy"}:
            assert row["capacity_m3_y"] != ""
        if row["utilization"] != "":
            assert 0.0 <= float(row["utilization"]) <= 1.0 + 1e-9
    assert summary["validation"]["known_capacity_violations"] == 0
    assert summary["validation"]["unbounded_assignments"] == 0


def test_no_single_facility_unbounded_absorption(
    assignments, concentration, summary
):
    for scenario in assignment.SCENARIOS:
        by_facility = defaultdict(float)
        for row in _rows_for(assignments, scenario):
            by_facility[row["facility_id"]] += float(row["assigned_m3"])
        total = sum(by_facility.values())
        max_share = max(by_facility.values()) / total
        assert max_share < 0.10
        top = _rows_for(concentration, scenario)[0]
        assert float(top["share_of_assigned"]) == pytest.approx(
            max_share, abs=1e-6
        )
        assert summary["validation"]["max_single_facility_share"][
            scenario
        ] == pytest.approx(max_share, abs=1e-9)
        for row in _rows_for(assignments, scenario):
            assert row["capacity_m3_y"] != ""
            assert "unbounded" not in row["capacity_status"]


def test_published_only_excludes_proxy_capacity(assignments, summary):
    published_only = _rows_for(assignments, "published_only")
    assert published_only
    assert all(row["capacity_status"] == "published" for row in published_only)
    assert summary["validation"]["proxy_assignments_in_published_only"] == 0
    assert summary["scenarios"]["published_only"]["proxy_m3"] == 0.0


def test_facility_and_capacity_sources_are_distinct(assignments):
    proxy_rows = [
        row for row in assignments if row["capacity_status"] == "proxy"
    ]
    assert proxy_rows
    for row in proxy_rows:
        assert row["capacity_source_id"] == "class_median_proxy_v1"
        assert row["capacity_source_url"].endswith("class_medians.csv")
        assert row["source_url"]
    published_rows = [
        row for row in assignments if row["capacity_status"] == "published"
    ]
    assert published_rows
    for row in published_rows:
        assert row["capacity_source_id"] == row["source_id"]


def test_proxy_scenario_uses_finite_proxy_capacity(assignments, summary):
    plus_proxy = _rows_for(assignments, "published_plus_proxy")
    proxy_rows = [row for row in plus_proxy if row["capacity_status"] == "proxy"]
    assert proxy_rows
    for row in proxy_rows:
        assert float(row["proxy_low_m3_y"]) <= float(row["capacity_m3_y"])
        assert float(row["capacity_m3_y"]) <= float(row["proxy_high_m3_y"])
    assert summary["scenarios"]["published_plus_proxy"]["proxy_m3"] > 0.0
    assert summary["validation"]["proxy_range_violations"] == 0


def test_scenario_b_assigns_at_least_scenario_a(summary, comparison):
    a = summary["scenarios"]["published_only"]
    b = summary["scenarios"]["published_plus_proxy"]
    assert b["assigned_m3"] >= a["assigned_m3"] - 0.01
    assert b["unassigned_m3"] <= a["unassigned_m3"] + 0.01
    assert summary["comparison"]["scenario_b_assigns_at_least_a"] is True
    assert summary["comparison"]["assigned_delta_m3"] == pytest.approx(
        b["assigned_m3"] - a["assigned_m3"], abs=0.01
    )
    by_scenario = {row["scenario"]: row for row in comparison}
    assert set(by_scenario) == set(assignment.SCENARIOS)
    for scenario, row in by_scenario.items():
        assert float(row["assigned_m3"]) == pytest.approx(
            summary["scenarios"][scenario]["assigned_m3"], abs=0.01
        )
        assert float(row["unassigned_m3"]) == pytest.approx(
            summary["scenarios"][scenario]["unassigned_m3"], abs=0.01
        )


def test_province_class_totals_reconcile(province_class, summary):
    for scenario in assignment.SCENARIOS:
        scenario_rows = _rows_for(province_class, scenario)
        total = sum(float(row["assigned_m3"]) for row in scenario_rows)
        assert total == pytest.approx(
            summary["scenarios"][scenario]["assigned_m3"], abs=0.01
        )
        assert all(
            row["facility_class"] in assignment.RELEVANT_CLASSES
            for row in scenario_rows
        )
        for row in scenario_rows:
            assert float(row["published_m3"]) + float(
                row["proxy_m3"]
            ) == pytest.approx(float(row["assigned_m3"]), abs=1e-6)


def test_concentration_reconciles(concentration, summary):
    for scenario in assignment.SCENARIOS:
        rows = _rows_for(concentration, scenario)
        assert [int(row["rank"]) for row in rows] == list(
            range(1, len(rows) + 1)
        )
        shares = [float(row["share_of_assigned"]) for row in rows]
        assert sum(shares) == pytest.approx(1.0, abs=1e-4)
        assert float(rows[-1]["cumulative_share"]) == pytest.approx(1.0, abs=1e-6)
        assert summary["concentration"][scenario]["hhi"] == pytest.approx(
            sum(share * share for share in shares), abs=1e-6
        )
        assert summary["concentration"][scenario]["top1_share"] == (
            pytest.approx(max(shares), abs=1e-6)
        )


def test_other_and_non_operating_records_are_excluded(
    assignments, utilization, summary
):
    capacity = summary["capacity"]
    assert capacity["other_class_records"] == 15
    assert capacity["unavailable_records"] == 0
    assert capacity["non_operating_records"] == 1
    assert capacity["published_unlocated_records"] == 81
    assert not any(row["facility_class"] == "OTHER" for row in assignments)
    assert not any(row["facility_class"] == "OTHER" for row in utilization)
    excluded = [
        row
        for row in utilization
        if row["screen_status"] == "excluded_non_operating"
    ]
    assert len(excluded) == len(assignment.SCENARIOS)
    unlocated = [
        row
        for row in utilization
        if row["screen_status"] == "excluded_unlocated"
    ]
    assert len(unlocated) == 81 * len(assignment.SCENARIOS)
    assert not any(
        row["screen_status"] == "excluded_no_capacity" for row in utilization
    )


def test_local_capacity_is_used_before_cross_border(
    assignments, utilization, province_summary
):
    for scenario in assignment.SCENARIOS:
        scenario_utilization = _rows_for(utilization, scenario)
        local_capacity: dict[str, float] = defaultdict(float)
        for row in scenario_utilization:
            if row["screen_status"].startswith("eligible_"):
                local_capacity[row["facility_province"]] += float(
                    row["capacity_m3_y"]
                )
        for row in _rows_for(province_summary, scenario):
            if float(row["cross_border_assigned_m3"]) <= assignment.VOLUME_EPSILON_M3:
                continue
            assert float(row["local_assigned_m3"]) == pytest.approx(
                local_capacity[row["province"]], abs=1e-6
            )


def test_utilization_reconciles_with_assignment_rows(
    assignments, utilization
):
    for scenario in assignment.SCENARIOS:
        assigned_by_facility: dict[str, float] = defaultdict(float)
        for row in _rows_for(assignments, scenario):
            assigned_by_facility[row["facility_id"]] += float(row["assigned_m3"])
        for row in _rows_for(utilization, scenario):
            assert float(row["assigned_m3"]) == pytest.approx(
                assigned_by_facility.get(row["facility_id"], 0.0), abs=1e-6
            )
            if row["utilization"]:
                assert float(row["remaining_capacity_m3"]) == pytest.approx(
                    float(row["capacity_m3_y"]) - float(row["assigned_m3"]),
                    abs=1e-6,
                )


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


def test_report_and_method(summary):
    method = summary["method"]
    assert method["cross_border_penalty_km"] == assignment.CROSS_BORDER_PENALTY_KM
    assert method["road_factor"] == assignment.ROAD_FACTOR
    assert method["local_first"] is True
    assert "no_unbounded_facility" in method
    assert "class-median" in method["scenarios"]["published_plus_proxy"]
    report = REPORT_MD.read_text(encoding="utf-8")
    for phrase in (
        "Scenario comparison",
        "Province totals",
        "Class totals",
        "Concentration and overflow",
        "model_estimate",
        "observed=false",
        "policy_grade=false",
        "legal/AAC",
    ):
        assert phrase in report
