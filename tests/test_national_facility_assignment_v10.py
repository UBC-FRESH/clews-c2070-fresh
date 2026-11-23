"""Regression tests for the node-level transport-aware assignment v10."""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path

import pytest

from workflows.national.facilities import (
    national_facility_assignment_v10 as v10,
)
from workflows.national.facilities import transport_cost_v1 as transport
from workflows.national.facilities.national_facility_assignment import (
    haversine_km,
)

OUTPUT_DIR = v10.OUTPUT_DIR
SUMMARY_JSON = OUTPUT_DIR / "summary.json"
MANIFEST_JSON = OUTPUT_DIR / "manifest.json"
V10_ROWS_CSV = OUTPUT_DIR / "v10_node_assignment.csv"
V9_FIXED_CSV = OUTPUT_DIR / "v9_node_fixed_rows.csv"
FACILITY_CSV = OUTPUT_DIR / "v10_facility_utilization.csv"
PROVINCE_CSV = OUTPUT_DIR / "v10_province_summary.csv"
COMPARISON_CSV = OUTPUT_DIR / "v9_v10_comparison.csv"
PROVINCE_COMPARISON_CSV = OUTPUT_DIR / "v9_v10_province_comparison.csv"
DISTANCE_CSV = OUTPUT_DIR / "distance_distribution.csv"
LOCAL_PREFERENCE_CSV = OUTPUT_DIR / "local_preference_cases.csv"
REPORT_MD = OUTPUT_DIR / "NODE_ASSIGNMENT_V10.md"


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


@pytest.fixture(scope="module")
def summary() -> dict:
    with SUMMARY_JSON.open("r", encoding="utf-8") as handle:
        return json.load(handle)


@pytest.fixture(scope="module")
def rows() -> list[dict[str, str]]:
    return _read_csv(V10_ROWS_CSV)


@pytest.fixture(scope="module")
def nodes() -> list[dict[str, str]]:
    return _read_csv(v10.SUPPLY_NODES_CSV)


@pytest.fixture(scope="module")
def facilities() -> list[dict[str, str]]:
    return _read_csv(v10.CAPACITY_CSV)


@pytest.fixture(scope="module")
def facility_rows() -> list[dict[str, str]]:
    return _read_csv(FACILITY_CSV)


@pytest.fixture(scope="module")
def comparison() -> dict[str, dict[str, str]]:
    return {row["metric"]: row for row in _read_csv(COMPARISON_CSV)}


def test_outputs_exist_manifest_hashes_and_labels(summary) -> None:
    for path in (
        SUMMARY_JSON,
        MANIFEST_JSON,
        V10_ROWS_CSV,
        V9_FIXED_CSV,
        FACILITY_CSV,
        PROVINCE_CSV,
        COMPARISON_CSV,
        PROVINCE_COMPARISON_CSV,
        DISTANCE_CSV,
        LOCAL_PREFERENCE_CSV,
        REPORT_MD,
    ):
        assert path.is_file(), path
    assert list(_read_csv(V10_ROWS_CSV)[0]) == v10.V10_ASSIGNMENT_FIELDS
    assert list(_read_csv(V9_FIXED_CSV)[0]) == v10.V9_FIXED_FIELDS
    assert list(_read_csv(FACILITY_CSV)[0]) == v10.FACILITY_FIELDS
    assert list(_read_csv(COMPARISON_CSV)[0]) == v10.COMPARISON_FIELDS
    manifest = json.loads(MANIFEST_JSON.read_text(encoding="utf-8"))
    assert manifest["artifact"] == "national_facility_assignment_v10"
    assert manifest["artifact_version"] == "10.0.0"
    assert manifest["status"] == "complete"
    for name, entry in manifest["outputs"].items():
        path = OUTPUT_DIR / name
        assert path.is_file(), name
        assert path.stat().st_size == entry["bytes"], name
        assert v10.v9._sha256(path) == entry["sha256"], name
    labels = summary["labels"]
    assert labels["evidence_label"] == "model_estimate"
    assert labels["observed"] is False
    assert labels["policy_grade"] is False
    assert labels["national_claim"] is False
    assert labels["official_osecan_changed"] is False
    assert labels["ui_changed"] is False
    assert labels["assignment_change_vs_v9"] is True
    assert labels["capacity_change"] is False
    assert labels["product_change"] is False
    assert labels["residue_change"] is False
    assert labels["greenfield_siting"] is False
    assert labels["policy_claim"] is False


def test_node_and_province_conservation(rows, nodes, summary) -> None:
    active = [node for node in nodes if float(node["harvest_m3"]) > 0.0]
    zeros = [node for node in nodes if float(node["harvest_m3"]) == 0.0]
    assert len(nodes) == 525
    assert len(active) == 486
    assert len(zeros) == 39
    total = math.fsum(float(row["assigned_m3"]) for row in rows)
    harvest = math.fsum(float(node["harvest_m3"]) for node in active)
    assert total == pytest.approx(harvest, abs=1e-3)
    assert total == pytest.approx(118_977_339.1, abs=1e-3)

    by_node: dict[str, float] = {}
    for row in rows:
        assert float(row["assigned_m3"]) > 0.0
        by_node[row["node_id"]] = by_node.get(row["node_id"], 0.0) + float(
            row["assigned_m3"]
        )
    for node in active:
        assert by_node[node["node_id"]] == pytest.approx(
            float(node["harvest_m3"]), abs=1e-6
        )
    assert all(node["node_id"] not in by_node for node in zeros)

    by_province: dict[str, float] = {}
    for node in active:
        by_province[node["province"]] = by_province.get(
            node["province"], 0.0
        ) + float(node["harvest_m3"])
    assigned_by_province: dict[str, float] = {}
    for row in rows:
        assigned_by_province[row["node_province"]] = assigned_by_province.get(
            row["node_province"], 0.0
        ) + float(row["assigned_m3"])
    assert set(assigned_by_province) == set(by_province)
    for province, volume in by_province.items():
        assert assigned_by_province[province] == pytest.approx(volume, abs=1e-3)
    assert summary["validation"]["checks"]["node_harvest_conservation"] is True
    assert summary["validation"]["checks"]["per_node_conservation"] is True
    assert summary["validation"]["checks"]["province_conservation"] is True


def test_capacity_and_binding(rows, facility_rows, summary) -> None:
    by_facility: dict[str, float] = {}
    for row in rows:
        assigned = float(row["assigned_m3"])
        capacity = float(row["capacity_m3_y"])
        assert assigned >= 0.0
        assert assigned <= capacity + 1e-6
        by_facility[row["facility_id"]] = (
            by_facility.get(row["facility_id"], 0.0) + assigned
        )
    assert len(by_facility) == 296
    for row in facility_rows:
        assigned = float(row["assigned_m3"])
        capacity = float(row["capacity_m3_y"])
        remaining = float(row["remaining_m3"])
        assert assigned == pytest.approx(
            by_facility.get(row["facility_id"], 0.0), abs=1e-5
        )
        assert assigned + remaining == pytest.approx(capacity, abs=1e-3)
        assert 0.0 <= float(row["utilization"]) <= 1.0 + 1e-9
        assert row["binding"] == (
            "true" if v10._is_binding(assigned, capacity) else "false"
        )
    assert summary["validation"]["checks"]["capacity_bounds"] is True
    assert summary["validation"]["checks"]["facility_capacity_identity"] is True
    assert summary["validation"]["checks"]["binding_flags"] is True


def test_cost_arithmetic(rows, summary) -> None:
    parameters = v10.v9.load_transport_parameters()
    line = v10._cost_line(parameters)
    assert line.penalty_cad_m3 == pytest.approx(51.45037624871347, abs=1e-9)
    for row in rows:
        assigned = float(row["assigned_m3"])
        distance = float(row["road_distance_km"])
        per_m3 = float(row["transport_cost_cad_m3"])
        total = float(row["transport_cost_cad"])
        energy = float(row["transport_energy_gj"])
        expected = transport.trip_cost(
            distance, v10.PAYLOAD_M3, parameters, "base"
        )
        assert per_m3 == pytest.approx(expected["cost_per_m3_cad"], abs=1e-6)
        assert total == pytest.approx(assigned * per_m3, rel=1e-9, abs=0.01)
        assert energy == pytest.approx(
            assigned * v10.ENERGY_GJ_PER_M3, rel=1e-9, abs=1e-6
        )
        assert float(row["transport_cost_cad_gj"]) == pytest.approx(
            per_m3 / v10.ENERGY_GJ_PER_M3, rel=1e-9, abs=1e-9
        )
        assert float(row["transport_cost_cad_m3_low"]) <= per_m3
        assert per_m3 <= float(row["transport_cost_cad_m3_high"])
        expected_penalty = (
            line.penalty_cad_m3 if row["cross_border"] == "true" else 0.0
        )
        assert float(row["local_penalty_cad_m3"]) == pytest.approx(
            expected_penalty, abs=1e-9
        )
    assert summary["validation"]["checks"]["cost_arithmetic"] is True
    assert summary["validation"]["checks"]["penalty_arithmetic"] is True


def test_distance_method_and_rows(rows, nodes, facilities, summary) -> None:
    factors = v10.v9._eligible_facilities()[2]
    node_by_id = {node["node_id"]: node for node in nodes}
    facility_by_id = {facility["facility_id"]: facility for facility in facilities}
    for row in rows:
        node = node_by_id[row["node_id"]]
        facility = facility_by_id[row["facility_id"]]
        factor = factors[facility["province"]]
        assert float(row["road_factor_applied"]) == pytest.approx(factor, abs=1e-12)
        assert float(row["road_distance_km"]) == pytest.approx(
            float(row["distance_km"]) * factor, rel=1e-9, abs=1e-6
        )
        expected_haversine = haversine_km(
            float(node["lat"]),
            float(node["lon"]),
            float(facility["lat"]),
            float(facility["lon"]),
        )
        assert float(row["distance_km"]) == pytest.approx(
            expected_haversine, rel=1e-9, abs=1e-6
        )
    assert summary["validation"]["checks"]["distance_factor_arithmetic"] is True
    assert summary["method"]["full_osrm"] is False
    assert "haversine" in summary["method"]["distance_method"]


def test_v9_and_v10_comparison(comparison, summary) -> None:
    mean = comparison["mean_road_distance_km"]
    cost = comparison["transport_cost_cad"]
    assert float(mean["v9_node_fixed_value"]) == pytest.approx(
        677.532874, abs=1e-4
    )
    assert float(mean["v9_centroid_value"]) == pytest.approx(
        521.971748, abs=1e-4
    )
    assert float(mean["v10_value"]) == pytest.approx(270.289409, abs=1e-4)
    assert float(mean["delta_v10_minus_v9_node_fixed"]) == pytest.approx(
        -407.243466, abs=1e-4
    )
    assert float(cost["v10_value"]) < float(cost["v9_node_fixed_value"])
    assert float(cost["v10_value"]) < float(cost["v9_centroid_value"])
    assert summary["can_assignment_reduce_677_km_node_mean"] is True
    assert summary["mean_reduction_vs_v9_node_fixed_km"] == pytest.approx(
        407.243466, abs=1e-4
    )
    assert summary["v10_primary"]["mean_road_distance_km"] < 677.0
    assert summary["distance_bound"]["mean_km"] <= (
        summary["v10_primary"]["mean_road_distance_km"] + 1e-9
    )
    assert summary["validation"]["checks"]["v10_cost_below_v9_node_fixed"] is True
    assert summary["validation"]["checks"]["v10_mean_below_677_km"] is True


def test_distance_shares_and_geography(summary) -> None:
    centroid = summary["v9_centroid"]
    fixed = summary["v9_node_fixed"]
    new = summary["v10_primary"]
    assert centroid["share_ge_150_km"] == pytest.approx(
        0.834923589, abs=1e-9
    )
    assert fixed["share_ge_150_km"] == pytest.approx(0.926996812, abs=1e-9)
    assert new["share_ge_150_km"] == pytest.approx(0.486188642, abs=1e-9)
    assert new["share_ge_300_km"] == pytest.approx(0.301630753, abs=1e-9)
    assert new["share_ge_300_km"] < fixed["share_ge_300_km"]
    assert new["max_road_distance_km"] > fixed["mean_road_distance_km"]
    assert summary["distance_bound"]["nearest_facility_weighted_mean_km"] < (
        summary["distance_bound"]["mean_km"]
    )
    assert summary["validation"]["checks"][
        "v9_node_fixed_matches_supply_node_distance"
    ] is True
    assert summary["validation"]["checks"][
        "v9_node_fixed_cost_matches_supply_node_distance"
    ] is True


def test_province_rollup(rows, summary) -> None:
    province_rows = _read_csv(PROVINCE_CSV)
    total = math.fsum(float(row["assigned_m3"]) for row in rows)
    assert math.fsum(float(row["assigned_m3"]) for row in province_rows) == (
        pytest.approx(total, abs=1e-3)
    )
    for row in province_rows:
        assert float(row["local_m3"]) + float(row["cross_border_m3"]) == (
            pytest.approx(float(row["assigned_m3"]), abs=1e-3)
        )
        assert 0.0 <= float(row["share_ge_150_km"]) <= 1.0
        assert 0.0 <= float(row["share_ge_300_km"]) <= 1.0
        assert float(row["mean_road_distance_km"]) <= float(
            row["max_road_distance_km"]
        )
    assert summary["validation"]["checks"]["province_rollup_conservation"] is True
    assert summary["validation"]["checks"]["province_local_plus_cross"] is True


def test_no_nan_or_negative(rows, facility_rows, summary) -> None:
    for row in rows:
        for field in (
            "assigned_m3",
            "distance_km",
            "road_distance_km",
            "transport_cost_cad_m3",
            "transport_cost_cad",
            "transport_energy_gj",
            "transport_cost_cad_gj",
            "local_preference_penalty_cad",
        ):
            value = float(row[field])
            assert math.isfinite(value), (row["node_id"], field)
            assert value >= 0.0, (row["node_id"], field)
    for row in facility_rows:
        for field in (
            "capacity_m3_y",
            "assigned_m3",
            "remaining_m3",
            "utilization",
            "transport_cost_cad",
        ):
            value = float(row[field])
            assert math.isfinite(value), (row["facility_id"], field)
            assert value >= 0.0, (row["facility_id"], field)
    assert summary["validation"]["checks"]["no_nan_or_negative"] is True
    assert summary["validation"]["all_passed"] is True
