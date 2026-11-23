"""Regression tests for the transport-aware assignment v9."""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path

import pytest

from workflows.national.facilities import (
    national_facility_assignment_v9 as v9,
)
from workflows.national.facilities import transport_cost_v1 as transport

OUTPUT_DIR = v9.OUTPUT_DIR
SUMMARY_JSON = OUTPUT_DIR / "summary.json"
MANIFEST_JSON = OUTPUT_DIR / "manifest.json"
V8_ROWS_CSV = OUTPUT_DIR / "v8_transport_cost_rows.csv"
V8_FACILITY_CSV = OUTPUT_DIR / "v8_facility_transport_cost.csv"
V8_PROVINCE_CSV = OUTPUT_DIR / "v8_province_transport_cost.csv"
V8_TIER_CSV = OUTPUT_DIR / "v8_tier_comparison.csv"
V9_ROWS_CSV = OUTPUT_DIR / "v9_facility_assignment.csv"
V9_UTILIZATION_CSV = OUTPUT_DIR / "v9_facility_utilization.csv"
V9_FACILITY_CSV = OUTPUT_DIR / "v9_facility_transport_cost.csv"
V9_PROVINCE_CSV = OUTPUT_DIR / "v9_province_transport_cost.csv"
COMPARISON_CSV = OUTPUT_DIR / "v8_v9_comparison.csv"
CHANGES_CSV = OUTPUT_DIR / "v8_v9_facility_changes.csv"
PROVINCE_COMPARISON_CSV = OUTPUT_DIR / "v8_v9_province_comparison.csv"
SENSITIVITY_CSV = OUTPUT_DIR / "local_preference_sensitivity.csv"
REPORT_MD = OUTPUT_DIR / "TRANSPORT_ASSIGNMENT_V9.md"


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


@pytest.fixture(scope="module")
def summary() -> dict:
    with SUMMARY_JSON.open("r", encoding="utf-8") as handle:
        return json.load(handle)


@pytest.fixture(scope="module")
def v8_rows() -> list[dict[str, str]]:
    return _read_csv(V8_ROWS_CSV)


@pytest.fixture(scope="module")
def v9_rows() -> list[dict[str, str]]:
    return _read_csv(V9_ROWS_CSV)


@pytest.fixture(scope="module")
def comparison() -> dict[str, dict[str, str]]:
    return {row["metric"]: row for row in _read_csv(COMPARISON_CSV)}


def test_outputs_exist_manifest_and_labels(summary) -> None:
    for path in (
        SUMMARY_JSON,
        MANIFEST_JSON,
        V8_ROWS_CSV,
        V8_FACILITY_CSV,
        V8_PROVINCE_CSV,
        V8_TIER_CSV,
        V9_ROWS_CSV,
        V9_UTILIZATION_CSV,
        V9_FACILITY_CSV,
        V9_PROVINCE_CSV,
        COMPARISON_CSV,
        CHANGES_CSV,
        PROVINCE_COMPARISON_CSV,
        SENSITIVITY_CSV,
        REPORT_MD,
    ):
        assert path.is_file(), path
    assert list(_read_csv(V9_ROWS_CSV)[0]) == v9.V9_ASSIGNMENT_FIELDS
    manifest = json.loads(MANIFEST_JSON.read_text(encoding="utf-8"))
    assert manifest["artifact"] == "national_facility_assignment_v9"
    assert manifest["artifact_version"] == "9.0.0"
    for name, entry in manifest["outputs"].items():
        path = OUTPUT_DIR / name
        assert path.is_file(), name
        assert path.stat().st_size == entry["bytes"], name
        assert v9._sha256(path) == entry["sha256"], name
    labels = summary["labels"]
    assert labels["evidence_label"] == "model_estimate"
    assert labels["observed"] is False
    assert labels["policy_grade"] is False
    assert labels["official_osecan_changed"] is False
    assert labels["national_claim"] is False
    assert labels["product_change"] is False
    assert labels["residue_change"] is False
    assert labels["policy_claim"] is False


def test_v8_cost_arithmetic(v8_rows) -> None:
    parameters = v9.load_transport_parameters()
    v9._validate_curve_parity(parameters)
    for row in v8_rows:
        assigned = float(row["assigned_m3"])
        distance = float(row["road_distance_km"])
        total = float(row["transport_cost_cad"])
        per_m3 = float(row["transport_cost_cad_m3"])
        energy = float(row["transport_energy_gj"])
        assert total == pytest.approx(assigned * per_m3, rel=1e-8, abs=0.01)
        assert energy == pytest.approx(assigned * v9.ENERGY_GJ_PER_M3, abs=1e-6)
        assert float(row["transport_cost_cad_gj"]) == pytest.approx(
            total / energy, abs=1e-9
        )
        assert float(row["transport_cost_cad_m3_low"]) <= per_m3
        assert per_m3 <= float(row["transport_cost_cad_m3_high"])
        expected = transport.trip_cost(
            distance, v9.PAYLOAD_M3, parameters, "base"
        )
        assert per_m3 == pytest.approx(expected["cost_per_m3_cad"], abs=1e-6)
        assert float(row["transport_cost_cad_gj"]) == pytest.approx(
            expected["cost_per_gj_cad"], abs=1e-6
        )


def test_conservation(v8_rows, v9_rows, summary) -> None:
    offer = 118_977_339.1
    for scenario in v9.v6.SCENARIOS:
        rows = [row for row in v8_rows if row["scenario"] == scenario]
        assigned = math.fsum(float(row["assigned_m3"]) for row in rows)
        local = math.fsum(
            float(row["assigned_m3"])
            for row in rows
            if row["allocation_phase"] == "local"
        )
        cross = math.fsum(
            float(row["assigned_m3"])
            for row in rows
            if row["allocation_phase"] == "cross_border"
        )
        assert assigned == pytest.approx(offer, abs=1e-3)
        assert local + cross == pytest.approx(assigned, abs=1e-3)
    assigned = math.fsum(float(row["assigned_m3"]) for row in v9_rows)
    local = math.fsum(
        float(row["assigned_m3"])
        for row in v9_rows
        if row["allocation_phase"] == "local"
    )
    cross = math.fsum(
        float(row["assigned_m3"])
        for row in v9_rows
        if row["allocation_phase"] == "cross_border"
    )
    assert assigned == pytest.approx(offer, abs=1e-3)
    assert local + cross == pytest.approx(assigned, abs=1e-3)
    assert summary["v9_primary"]["assigned_m3"] == pytest.approx(offer, abs=1e-3)
    assert summary["validation"]["checks"]["v9_conservation"] is True


def test_capacity_and_binding(v9_rows, summary) -> None:
    utilization = _read_csv(V9_UTILIZATION_CSV)
    by_id = {row["facility_id"]: row for row in utilization}
    assert len(by_id) == len(utilization)
    assigned_by_id: dict[str, float] = {}
    for row in v9_rows:
        assigned_by_id[row["facility_id"]] = (
            assigned_by_id.get(row["facility_id"], 0.0)
            + float(row["assigned_m3"])
        )
        capacity = float(row["capacity_m3_y"])
        assert float(row["assigned_m3"]) >= 0.0
        assert float(row["assigned_m3"]) <= capacity + 1e-6
        assert float(row["utilization"]) <= 1.0 + 1e-9
    for facility_id, assigned in assigned_by_id.items():
        util = by_id[facility_id]
        assert float(util["assigned_m3"]) == pytest.approx(assigned, abs=1e-6)
        expected_binding = (
            float(util["remaining_capacity_m3"]) <= v9.VOLUME_EPSILON_M3
        )
        assert util["binding"] == ("true" if expected_binding else "false")
    assert summary["validation"]["checks"]["v9_capacity_bounds"] is True
    assert summary["validation"]["checks"]["utilization_complete"] is True
    assert summary["v9_primary"]["binding_facilities"] > 0


def test_v8_v9_comparison(comparison, summary) -> None:
    for row in comparison.values():
        assert float(row["delta_v9_minus_v8"]) == pytest.approx(
            float(row["v9_value"]) - float(row["v8_value"]), abs=1e-6
        )
    assert float(comparison["transport_cost_cad"]["v9_value"]) < float(
        comparison["transport_cost_cad"]["v8_value"]
    )
    assert float(comparison["mean_road_distance_km"]["v9_value"]) < float(
        comparison["mean_road_distance_km"]["v8_value"]
    )
    assert float(comparison["max_road_distance_km"]["v9_value"]) <= float(
        comparison["max_road_distance_km"]["v8_value"]
    )
    assert float(comparison["local_m3"]["v9_value"]) == pytest.approx(
        float(comparison["local_m3"]["v8_value"]), abs=1e-3
    )
    assert float(comparison["cross_border_m3"]["v9_value"]) == pytest.approx(
        float(comparison["cross_border_m3"]["v8_value"]), abs=1e-3
    )
    assert float(comparison["proxy_share_of_assigned"]["v9_value"]) > float(
        comparison["proxy_share_of_assigned"]["v8_value"]
    )
    assert float(comparison["facilities_used"]["v9_value"]) < float(
        comparison["facilities_used"]["v8_value"]
    )
    assert summary["validation"]["checks"]["v9_transport_cost_below_v8"] is True
    assert summary["validation"]["checks"]["v9_mean_distance_below_v8"] is True


def test_tier_comparison(v8_rows, summary) -> None:
    tiers = _read_csv(V8_TIER_CSV)
    assert [row["tier"] for row in tiers] == list(v9.TIER_ORDER)
    by_tier = {row["tier"]: row for row in tiers}
    assert float(by_tier["T1"]["volume_above_m3"]) >= float(
        by_tier["T3"]["volume_above_m3"]
    )
    assert float(by_tier["T3"]["volume_above_m3"]) >= float(
        by_tier["T4"]["volume_above_m3"]
    )
    for row in tiers:
        assert 0.0 <= float(row["share_above"]) <= 1.0
        assert float(row["tier_cost_per_gj"]) > 0.0
    for row in v8_rows:
        for tier in v9.TIER_ORDER:
            expected = float(row["transport_cost_cad_gj"]) > float(
                by_tier[tier]["tier_cost_per_gj"]
            )
            flag = row[f"exceeds_{tier.lower()}"]
            assert flag == ("true" if expected else "false")
    assert summary["validation"]["checks"]["tier_flags_well_formed"] is True


def test_local_preference_sensitivity() -> None:
    rows = _read_csv(SENSITIVITY_CSV)
    by_penalty = {float(row["local_penalty_km"]): row for row in rows}
    assert set(by_penalty) == set(v9.PENALTY_SENSITIVITY_KM)
    assert float(by_penalty[0.0]["cross_border_share"]) >= float(
        by_penalty[500.0]["cross_border_share"]
    )
    assert float(by_penalty[0.0]["transport_cost_cad"]) <= float(
        by_penalty[500.0]["transport_cost_cad"]
    ) + 1e-3
    for row in rows:
        for field in (
            "local_m3",
            "cross_border_m3",
            "transport_cost_cad",
            "transport_cost_cad_gj",
        ):
            value = float(row[field])
            assert math.isfinite(value)
            assert value >= 0.0


def test_no_nan_or_negative(v8_rows, v9_rows) -> None:
    numeric = (
        "assigned_m3",
        "road_distance_km",
        "transport_cost_cad",
        "transport_cost_cad_m3",
        "transport_cost_cad_gj",
        "transport_energy_gj",
    )
    for row in v8_rows:
        for field in numeric:
            value = float(row[field])
            assert math.isfinite(value), (field, row["facility_id"])
            assert value >= 0.0, (field, row["facility_id"])
    for row in v9_rows:
        for field in numeric:
            value = float(row[field])
            assert math.isfinite(value), (field, row["facility_id"])
            assert value >= 0.0, (field, row["facility_id"])
    for path in (V8_FACILITY_CSV, V8_PROVINCE_CSV, V9_FACILITY_CSV, V9_PROVINCE_CSV):
        for row in _read_csv(path):
            for field in (
                "assigned_m3",
                "transport_cost_cad",
                "transport_cost_cad_gj",
            ):
                value = float(row[field])
                assert math.isfinite(value)
                assert value >= 0.0


def test_deletion_guard_and_no_product_residue_files() -> None:
    assert v9.parse_deletion_lines(" D file\nM  file\n?? untracked\n") == [
        " D file"
    ]
    assert v9.parse_deletion_lines("M  file\n?? untracked\n") == []
    v9.assert_no_unexpected_deletions()
    names = {path.name.lower() for path in OUTPUT_DIR.iterdir() if path.is_file()}
    assert not any("product" in name for name in names)
    assert not any("residue" in name for name in names)


def test_local_first_shortfall_only(v9_rows, summary) -> None:
    cross = {
        row["province"]
        for row in v9_rows
        if row["allocation_phase"] == "cross_border"
    }
    assert cross <= {"NT"}
    assert summary["validation"]["checks"]["v9_local_first_shortfall_only"] is True
    assert summary["validation"]["all_passed"] is True
