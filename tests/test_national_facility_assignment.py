"""Regression tests for the national facility assignment v1 artifact."""

from __future__ import annotations

import csv
import hashlib
import json
from collections import defaultdict
from pathlib import Path

import pytest

from workflows.national.facilities import national_facility_assignment as assignment

OUTPUT_DIR = assignment.OUTPUT_DIR
ASSIGNMENT_CSV = OUTPUT_DIR / "facility_assignment.csv"
PROVINCE_CSV = OUTPUT_DIR / "province_summary.csv"
UTILIZATION_CSV = OUTPUT_DIR / "facility_utilization.csv"
UNASSIGNED_CSV = OUTPUT_DIR / "unassigned.csv"
SUMMARY_JSON = OUTPUT_DIR / "summary.json"
MANIFEST_JSON = OUTPUT_DIR / "manifest.json"
REPORT_MD = OUTPUT_DIR / "NATIONAL_FACILITY_ASSIGNMENT_V1.md"

TOTAL_M3 = assignment.EXPECTED_TOTAL_M3


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
def summary() -> dict:
    with SUMMARY_JSON.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _rows_for(
    rows: list[dict[str, str]], scenario: str
) -> list[dict[str, str]]:
    return [row for row in rows if row["scenario"] == scenario]


def _facility(
    facility_id: str,
    province: str,
    lat: float,
    lon: float,
    capacity: assignment.Capacity,
) -> assignment.FacilityOption:
    return assignment.FacilityOption(
        facility_id=facility_id,
        name=facility_id,
        facility_class="SAW",
        province=province,
        location=assignment.Location(lat, lon),
        capacity=capacity,
        operating_status="unknown",
        source_id="test",
        source_url="https://example.test/facility",
        evidence_tier="C",
    )


def test_outputs_exist_and_schema(assignments, province_summary, utilization, unassigned):
    for path in (
        ASSIGNMENT_CSV,
        PROVINCE_CSV,
        UTILIZATION_CSV,
        UNASSIGNED_CSV,
        SUMMARY_JSON,
        MANIFEST_JSON,
        REPORT_MD,
    ):
        assert path.exists(), path
    assert assignments
    assert province_summary
    assert utilization
    assert list(assignments[0]) == assignment.ASSIGNMENT_FIELDS
    assert list(province_summary[0]) == assignment.PROVINCE_SUMMARY_FIELDS
    assert list(utilization[0]) == assignment.UTILIZATION_FIELDS
    assert list(unassigned[0]) == assignment.UNASSIGNED_FIELDS


def test_labels_are_consistent(assignments, province_summary, utilization, unassigned, summary):
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


def test_conservation_in_both_scenarios(assignments, province_summary, unassigned, summary):
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
        assert sum(float(row["offer_m3"]) for row in scenario_summary) == pytest.approx(
            TOTAL_M3, abs=0.01
        )
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
        totals = summary["totals"]["scenario_totals"][scenario]
        assert totals["assigned_m3"] == pytest.approx(assigned_total, abs=0.01)
        assert totals["unassigned_m3"] == pytest.approx(unassigned_total, abs=0.01)
        assert summary["validation"]["conservation_gap_m3"][scenario] == pytest.approx(
            0.0, abs=0.01
        )


def test_known_capacity_is_enforced(assignments, utilization, summary):
    for row in assignments:
        if row["capacity_status"] != "known":
            continue
        assigned = float(row["assigned_m3"])
        capacity = float(row["capacity_m3_y"])
        assert 0.0 < assigned <= capacity + 1e-6
    for row in utilization:
        if row["capacity_status"] != "known" or not row["utilization"]:
            continue
        assert 0.0 <= float(row["utilization"]) <= 1.0 + 1e-9
        assert float(row["assigned_m3"]) <= float(row["capacity_m3_y"]) + 1e-6
    assert summary["validation"]["known_capacity_violations"] == 0
    assert summary["validation"]["max_known_utilization"]["known_only"] <= 1.0 + 1e-9
    assert (
        summary["validation"]["max_known_utilization"]["unbounded_unknown"]
        <= 1.0 + 1e-9
    )


def test_known_only_excludes_unknown_capacity(assignments, summary):
    known_only = _rows_for(assignments, "known_only")
    assert known_only
    assert all(row["capacity_status"] == "known" for row in known_only)
    assert summary["totals"]["scenario_totals"]["known_only"]["unknown_assigned_m3"] == 0.0
    assert summary["validation"]["unknown_assignments_in_known_only"] == 0


def test_unknown_capacity_is_not_invented(assignments, utilization, summary):
    unknown_utilization = [
        row for row in utilization if row["capacity_status"] == "unknown"
    ]
    assert len(unknown_utilization) == (
        summary["capacity"]["unknown_records"] * len(assignment.SCENARIOS)
    )
    for row in unknown_utilization:
        assert row["capacity_m3_y"] == ""
        assert row["remaining_capacity_m3"] == ""
        assert row["utilization"] == ""
    assert summary["capacity"]["unknown_capacity_m3_y"] is None
    assert (
        summary["capacity"]["unknown_capacity_proxy"]
        == "capacity_unbounded_proxy"
    )
    unbounded_unknown = [
        row
        for row in _rows_for(assignments, "unbounded_unknown")
        if row["capacity_status"] == "unknown"
    ]
    assert unbounded_unknown
    assert all(row["capacity_m3_y"] == "" for row in unbounded_unknown)
    assert (
        summary["totals"]["scenario_totals"]["unbounded_unknown"][
            "unknown_assigned_m3"
        ]
        > 0.0
    )


def test_unlocated_known_capacity_is_reported_not_assigned(utilization, summary):
    capacity = summary["capacity"]
    assert capacity["known_unlocated_records"] == 81
    assert capacity["known_unlocated_capacity_m3_y"] == pytest.approx(
        27_301_811.685736, abs=1e-6
    )
    assert capacity["known_located_records"] == 98
    assert capacity["known_located_capacity_m3_y"] == pytest.approx(
        68_536_597.539914, abs=1e-6
    )
    assert set(capacity["known_capacity_by_province"]) == {"BC"}
    assert capacity["known_capacity_by_province"]["BC"]["records"] == 179
    unlocated = [
        row for row in utilization if row["screen_status"] == "excluded_unlocated"
    ]
    assert len(unlocated) == 81 * len(assignment.SCENARIOS)
    for row in unlocated:
        assert row["location_status"] == "unlocated"
        assert row["assigned_m3"] == "0"
        assert row["utilization"] == ""


def test_cross_border_requires_exhausted_local_capacity(
    assignments, province_summary, utilization
):
    for scenario in assignment.SCENARIOS:
        for row in _rows_for(province_summary, scenario):
            if float(row["cross_border_assigned_m3"]) <= assignment.VOLUME_EPSILON_M3:
                continue
            local_known_capacity = sum(
                float(item["capacity_m3_y"])
                for item in _rows_for(utilization, scenario)
                if item["facility_province"] == row["province"]
                and item["screen_status"] == "eligible_known"
            )
            assert float(row["local_assigned_m3"]) == pytest.approx(
                local_known_capacity, abs=1e-6
            )
            if scenario == "unbounded_unknown":
                assert not any(
                    item["facility_province"] == row["province"]
                    and item["screen_status"] == "eligible_unbounded_proxy"
                    for item in _rows_for(utilization, scenario)
                )

    unbounded_cross_border = {
        row["province"]
        for row in _rows_for(province_summary, "unbounded_unknown")
        if float(row["cross_border_assigned_m3"]) > assignment.VOLUME_EPSILON_M3
    }
    assert unbounded_cross_border <= {"NT", "NU"}

    known_cross_border = [
        row
        for row in _rows_for(assignments, "known_only")
        if row["cross_border"] == "true"
    ]
    assert known_cross_border
    assert all(row["facility_province"] == "BC" for row in known_cross_border)


def test_allocator_local_first_then_cross_border():
    offer = assignment.ProvinceOffer(
        "AB", 150.0, assignment.Location(55.0, -114.0)
    )
    local = _facility(
        "LOCAL", "AB", 55.0, -113.0, assignment.KnownCapacity(100.0)
    )
    cross = _facility(
        "CROSS", "SK", 55.0, -114.1, assignment.KnownCapacity(1000.0)
    )
    result = assignment.allocate_scenario((offer,), (local, cross), "known_only")
    rows = list(result.assignments)
    assert [row["facility_id"] for row in rows] == ["LOCAL", "CROSS"]
    assert float(rows[0]["assigned_m3"]) == pytest.approx(100.0)
    assert float(rows[1]["assigned_m3"]) == pytest.approx(50.0)
    assert rows[0]["cross_border"] == "false"
    assert rows[1]["cross_border"] == "true"
    assert float(rows[1]["cross_border_penalty_km"]) == pytest.approx(500.0)
    assert float(rows[1]["rank_distance_km"]) == pytest.approx(
        float(rows[1]["road_distance_km"]) + 500.0
    )

    local_unknown = _facility(
        "LOCAL_UNKNOWN", "AB", 55.0, -113.5, assignment.UnknownCapacity()
    )
    result = assignment.allocate_scenario(
        (offer,), (local, cross, local_unknown), "unbounded_unknown"
    )
    rows = list(result.assignments)
    assert [row["facility_id"] for row in rows] == ["LOCAL", "LOCAL_UNKNOWN"]
    assert float(rows[1]["assigned_m3"]) == pytest.approx(50.0)


def test_local_known_capacity_is_used_before_cross_border():
    offer = assignment.ProvinceOffer(
        "AB", 80.0, assignment.Location(55.0, -114.0)
    )
    local = _facility(
        "LOCAL", "AB", 55.0, -113.0, assignment.KnownCapacity(100.0)
    )
    cross = _facility(
        "CROSS", "SK", 55.0, -114.1, assignment.KnownCapacity(1000.0)
    )
    result = assignment.allocate_scenario((offer,), (local, cross), "known_only")
    assert [row["facility_id"] for row in result.assignments] == ["LOCAL"]


def test_distances_are_reproducible(assignments):
    assert assignment.haversine_km(43.6532, -79.3832, 45.5017, -73.5673) == pytest.approx(
        504.3, abs=2.0
    )
    facilities = {
        facility.facility_id: facility for facility in assignment.read_facilities()
    }
    centroids, _ = assignment.load_province_centroids()
    for row in assignments:
        centroid = centroids[row["province"]]
        facility = facilities[row["facility_id"]]
        assert facility.location is not None
        expected = assignment.haversine_km(
            centroid.lat,
            centroid.lon,
            facility.location.lat,
            facility.location.lon,
        )
        assert float(row["distance_km"]) == pytest.approx(expected, abs=1e-6)
        assert float(row["road_distance_km"]) == pytest.approx(
            expected * assignment.ROAD_FACTOR, abs=1e-6
        )
        penalty = (
            assignment.CROSS_BORDER_PENALTY_KM
            if row["cross_border"] == "true"
            else 0.0
        )
        assert float(row["cross_border_penalty_km"]) == pytest.approx(penalty)
        assert float(row["rank_distance_km"]) == pytest.approx(
            float(row["road_distance_km"]) + penalty, abs=1e-6
        )


def test_province_class_totals_reconcile(summary):
    totals = summary["province_class_totals"]
    for scenario in assignment.SCENARIOS:
        scenario_total = sum(
            row["assigned_m3"] for row in totals if row["scenario"] == scenario
        )
        assert scenario_total == pytest.approx(
            summary["totals"]["scenario_totals"][scenario]["assigned_m3"],
            abs=0.01,
        )
    assert all(
        row["facility_class"] in assignment.RELEVANT_CLASSES for row in totals
    )


def test_utilization_reconciles_with_assignment_rows(assignments, utilization):
    for scenario in assignment.SCENARIOS:
        assigned_by_facility: dict[str, float] = defaultdict(float)
        for row in _rows_for(assignments, scenario):
            assigned_by_facility[row["facility_id"]] += float(row["assigned_m3"])
        for row in _rows_for(utilization, scenario):
            assert float(row["assigned_m3"]) == pytest.approx(
                assigned_by_facility.get(row["facility_id"], 0.0), abs=1e-6
            )
            if row["capacity_status"] == "known" and row["utilization"]:
                assert float(row["remaining_capacity_m3"]) == pytest.approx(
                    float(row["capacity_m3_y"]) - float(row["assigned_m3"]),
                    abs=1e-6,
                )


def test_unassigned_rows_reconcile(unassigned, summary):
    for scenario in assignment.SCENARIOS:
        scenario_rows = _rows_for(unassigned, scenario)
        total = sum(float(row["unassigned_m3"]) for row in scenario_rows)
        assert total == pytest.approx(
            summary["totals"]["scenario_totals"][scenario]["unassigned_m3"],
            abs=0.01,
        )
        for row in scenario_rows:
            assert float(row["unassigned_m3"]) > 0.0
            assert float(row["unassigned_share"]) > 0.0
            assert row["reason"]


def test_other_and_non_operating_records_are_excluded(assignments, utilization, summary):
    assert summary["capacity"]["excluded_other_records"] == 15
    assert summary["capacity"]["non_operating_records"] == 1
    assert not any(row["facility_class"] == "OTHER" for row in assignments)
    assert not any(row["facility_class"] == "OTHER" for row in utilization)
    excluded = [
        row for row in utilization if row["screen_status"] == "excluded_non_operating"
    ]
    assert len(excluded) == len(assignment.SCENARIOS)
    for row in excluded:
        assert row["assigned_m3"] == "0"
        assert row["used"] == "false"


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
    assert "national_facilities.csv" in joined
    assert "119m.csv" in joined
    assert "canada_adm1_geoboundaries_simplified.geojson" in joined


def test_summary_method_and_report(summary):
    method = summary["method"]
    assert method["cross_border_penalty_km"] == assignment.CROSS_BORDER_PENALTY_KM
    assert method["road_factor"] == assignment.ROAD_FACTOR
    assert "capacity_unbounded_proxy" in method["unknown_capacity_rule"]
    assert method["local_first"] is True
    text = REPORT_MD.read_text(encoding="utf-8")
    for scenario in assignment.SCENARIOS:
        assert scenario in text
    assert "Assignment rule" in text
    assert "Known versus unknown capacity" in text
    assert "Unassigned and overflow" in text
    assert "model_estimate" in text
    assert "observed=false" in text
    assert "policy_grade=false" in text
    assert "legal/AAC" in text
