"""Regression tests for the province-factor assignment v8."""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path

import pytest

from workflows.national.facilities import (
    facility_road_distance_audit_v2 as audit,
)
from workflows.national.facilities import (
    national_facility_assignment_v6 as v6,
)
from workflows.national.facilities import (
    national_facility_assignment_v8 as v8,
)

OUTPUT_DIR = v8.OUTPUT_DIR
ASSIGNMENT_CSV = OUTPUT_DIR / "facility_assignment.csv"
SUMMARY_JSON = OUTPUT_DIR / "summary.json"
MANIFEST_JSON = OUTPUT_DIR / "manifest.json"
COMPARISON_CSV = OUTPUT_DIR / "v6_v8_comparison.csv"
DISTANCE_CSV = OUTPUT_DIR / "facility_distance_comparison.csv"
PROVINCE_DISTANCE_CSV = OUTPUT_DIR / "province_distance_comparison.csv"
FACTOR_COMPARISON_CSV = OUTPUT_DIR / "province_factor_comparison.csv"
CAPACITY_FLOOR_CSV = OUTPUT_DIR / "capacity_floor.csv"
REPORT_MD = OUTPUT_DIR / "NATIONAL_FACILITY_ASSIGNMENT_V8.md"
V6_DIR = v6.OUTPUT_DIR
V6_FLOOR_CSV = V6_DIR / "capacity_floor.csv"


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


@pytest.fixture(scope="module")
def summary() -> dict:
    with SUMMARY_JSON.open("r", encoding="utf-8") as handle:
        return json.load(handle)


@pytest.fixture(scope="module")
def assignments() -> list[dict[str, str]]:
    return _read_csv(ASSIGNMENT_CSV)


@pytest.fixture(scope="module")
def factors() -> dict[str, float]:
    rows = _read_csv(audit.OUTPUT_DIR / "province_road_factors.csv")
    return {
        row["province"]: float(row["factor_applied"])
        for row in rows
        if row["factor_applied"]
    }


def test_outputs_exist_and_labels(summary) -> None:
    for path in (
        ASSIGNMENT_CSV,
        SUMMARY_JSON,
        MANIFEST_JSON,
        COMPARISON_CSV,
        DISTANCE_CSV,
        PROVINCE_DISTANCE_CSV,
        FACTOR_COMPARISON_CSV,
        CAPACITY_FLOOR_CSV,
        REPORT_MD,
    ):
        assert path.is_file(), path
    assert list(_read_csv(ASSIGNMENT_CSV)[0]) == v8.V8_ASSIGNMENT_FIELDS
    assert summary["artifact"] == "national_facility_assignment_v8"
    assert summary["artifact_version"] == "8.0.0"
    assert summary["labels"] == {
        "evidence_label": "model_estimate",
        "observed": False,
        "policy_grade": False,
        "national_claim": False,
        "official_osecan_changed": False,
        "assignment_change_vs_v6": False,
        "capacity_change": False,
        "product_change": False,
        "residue_change": False,
    }
    assert summary["method"]["distance_only_change"] is True
    assert summary["method"]["road_factor_basis"] == "facility_province"


def test_assignment_conservation(assignments, summary) -> None:
    for scenario in v8.SCENARIOS:
        rows = [row for row in assignments if row["scenario"] == scenario]
        offer = float(summary["scenarios"][scenario]["offer_m3"])
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
        unassigned = float(
            summary["scenarios"][scenario]["unassigned_m3"]
        )
        assert assigned + unassigned == pytest.approx(offer, abs=1e-6)
        assert local + cross == pytest.approx(assigned, abs=1e-6)
    assert summary["scenarios"][v8.PRIMARY_SCENARIO]["assigned_m3"] == (
        pytest.approx(118_977_339.1, abs=0.1)
    )
    assert summary["scenarios"][v8.PRIMARY_SCENARIO]["unassigned_m3"] == (
        pytest.approx(0.0, abs=1e-6)
    )


def test_capacity_bounds_and_factor_arithmetic(
    assignments, factors, summary
) -> None:
    assert summary["validation"]["capacity_bounds"] is True
    assert summary["validation"]["factor_coverage"] is True
    for row in assignments:
        capacity = float(row["capacity_m3_y"])
        assigned = float(row["assigned_m3"])
        assert math.isfinite(capacity) and capacity > 0.0
        assert assigned >= 0.0
        assert assigned <= capacity + 1e-6
        factor = float(row["road_factor_applied"])
        assert factor == pytest.approx(factors[row["facility_province"]])
        assert audit.MIN_FACTOR <= factor <= audit.MAX_FACTOR
        assert float(row["road_distance_km"]) == pytest.approx(
            float(row["distance_km"]) * factor, abs=1e-5
        )
        assert row["road_factor_basis"] == "facility_province"


def test_v6_v8_comparison() -> None:
    rows = _read_csv(COMPARISON_CSV)
    assert list(rows[0]) == v8.COMPARISON_FIELDS
    by_metric = {row["metric"]: row for row in rows}
    for metric in (
        "assigned_m3",
        "local_m3",
        "local_share",
        "cross_border_m3",
        "cross_border_share",
        "proxy_m3",
        "proxy_share_of_assigned",
        "facilities_used",
        "max_facility_share",
        "top5_share",
        "hhi",
        "unassigned_m3",
        "volume_weighted_mean_road_distance_km",
    ):
        assert metric in by_metric
        assert by_metric[metric]["v6_value"] != ""
        assert by_metric[metric]["v8_value"] != ""
    assert float(by_metric["local_m3"]["delta_v8_minus_v6"]) == 0.0
    assert float(by_metric["cross_border_m3"]["delta_v8_minus_v6"]) == 0.0
    assert float(by_metric["proxy_share_of_assigned"]["delta_v8_minus_v6"]) == 0.0
    assert float(by_metric["facilities_used"]["delta_v8_minus_v6"]) == 0.0
    assert float(by_metric["hhi"]["delta_v8_minus_v6"]) == 0.0
    assert (
        float(
            by_metric["volume_weighted_mean_road_distance_km"][
                "delta_v8_minus_v6"
            ]
        )
        > 0.0
    )
    assert (
        float(by_metric["total_volume_distance_m3_km"]["delta_pct"]) > 0.0
    )


def test_province_distance_comparison() -> None:
    rows = _read_csv(PROVINCE_DISTANCE_CSV)
    assert list(rows[0]) == v8.PROVINCE_DISTANCE_FIELDS
    assert len(rows) == 12
    total_v6 = math.fsum(float(row["v6_assigned_m3"]) for row in rows)
    total_v8 = math.fsum(float(row["v8_assigned_m3"]) for row in rows)
    assert total_v6 == pytest.approx(118_977_339.1, abs=0.1)
    assert total_v8 == pytest.approx(118_977_339.1, abs=0.1)
    for row in rows:
        assigned = float(row["v6_assigned_m3"])
        mean = float(row["v6_volume_weighted_mean_road_distance_km"])
        total = float(row["v6_total_volume_distance_m3_km"])
        if assigned > 0.0:
            assert mean == pytest.approx(total / assigned, abs=1e-5)
        assert float(row["n_distance_changed"]) <= float(row["n_rows_common"])


def test_distance_comparison_arithmetic() -> None:
    rows = _read_csv(DISTANCE_CSV)
    assert list(rows[0]) == v8.DISTANCE_FIELDS
    assert len(rows) == 208
    for row in rows:
        if row["status"] != "both":
            continue
        before = float(row["v6_road_distance_km"])
        after = float(row["v8_road_distance_km"])
        delta = float(row["delta_road_distance_km"])
        assert delta == pytest.approx(after - before, abs=1e-6)
        assert float(row["delta_road_distance_pct"]) == pytest.approx(
            100.0 * (after - before) / before, abs=1e-5
        )
        assert after == pytest.approx(
            float(row["haversine_km"]) * float(row["v8_road_factor"]),
            abs=1e-5,
        )
        assert before == pytest.approx(
            float(row["haversine_km"]) * float(row["v6_road_factor"]),
            abs=1e-5,
        )
    summary = json.loads(SUMMARY_JSON.read_text(encoding="utf-8"))
    distance = summary["distance_comparison"]
    assert distance["n_rows_v6"] == 208
    assert distance["n_rows_v8"] == 208
    assert distance["n_rows_common"] == 208
    assert distance["n_rows_added"] == 0
    assert distance["n_rows_removed"] == 0
    assert distance["n_distance_changed"] == 208
    assert distance["assignment_changed"] is False
    assert distance["moved_volume_m3"] == 0.0
    assert distance["changed_provinces"] == []
    assert summary["labels"]["assignment_change_vs_v6"] is False


def test_capacity_floor_and_capacity_unchanged() -> None:
    old = {
        row["province"]: row
        for row in _read_csv(V6_FLOOR_CSV)
    }
    new = {
        row["province"]: row
        for row in _read_csv(CAPACITY_FLOOR_CSV)
    }
    assert set(old) == set(new)
    for province, row in new.items():
        for field in (
            "floor_multiplier",
            "floor_capacity_m3_y",
            "published_capacity_m3_y",
            "proxy_capacity_before_m3_y",
            "total_capacity_before_m3_y",
            "proxy_scale_factor",
            "proxy_capacity_after_m3_y",
            "total_capacity_after_m3_y",
            "capacity_shortfall_m3_y",
            "capacity_floor_status",
        ):
            assert row[field] == old[province][field]
    summary = json.loads(SUMMARY_JSON.read_text(encoding="utf-8"))
    assert summary["validation"]["capacity_floor_unchanged"] is True
    assert summary["labels"]["capacity_change"] is False
    assert summary["labels"]["product_change"] is False
    assert summary["labels"]["residue_change"] is False
    names = {path.name.lower() for path in OUTPUT_DIR.iterdir() if path.is_file()}
    assert not any("product" in name for name in names)
    assert not any("residue" in name for name in names)


def test_no_nan_or_negative(assignments, summary) -> None:
    assert summary["validation"]["no_nan_or_negative"] is True
    for row in assignments:
        for field in (
            "capacity_m3_y",
            "assigned_m3",
            "remaining_capacity_m3",
            "distance_km",
            "road_distance_km",
            "road_factor_applied",
        ):
            value = float(row[field])
            assert math.isfinite(value)
            assert value >= 0.0
    assert all(summary["validation"].values())
    assert summary["validation"]["conservation_gap_m3"] is True
    assert summary["validation"]["local_plus_cross_equals_assigned"] is True
    assert summary["validation"]["local_first"] is True
