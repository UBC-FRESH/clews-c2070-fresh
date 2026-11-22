"""Regression tests for the floor-primary national facility assignment v6."""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path

import pytest

from workflows.national.facilities import (
    national_facility_assignment_v5 as v5,
)
from workflows.national.facilities import (
    national_facility_assignment_v6 as v6,
)

OUTPUT_DIR = v6.OUTPUT_DIR
ASSIGNMENT_CSV = OUTPUT_DIR / "facility_assignment.csv"
SUMMARY_JSON = OUTPUT_DIR / "summary.json"
MANIFEST_JSON = OUTPUT_DIR / "manifest.json"
COMPARISON_CSV = OUTPUT_DIR / "v5_v6_comparison.csv"
FLOOR_CSV = OUTPUT_DIR / "capacity_floor.csv"
FLOOR_FACILITY_CSV = OUTPUT_DIR / "capacity_floor_facilities.csv"
PROVINCE_CAPACITY_CSV = OUTPUT_DIR / "province_capacity_comparison.csv"
REPORT_MD = OUTPUT_DIR / "NATIONAL_FACILITY_ASSIGNMENT_V6.md"


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


def test_outputs_exist_and_primary_is_floor(summary, assignments) -> None:
    for path in (
        ASSIGNMENT_CSV,
        SUMMARY_JSON,
        MANIFEST_JSON,
        COMPARISON_CSV,
        FLOOR_CSV,
        FLOOR_FACILITY_CSV,
        PROVINCE_CAPACITY_CSV,
        REPORT_MD,
    ):
        assert path.is_file(), path
    assert list(assignments[0]) == v6.ASSIGNMENT_FIELDS
    assert summary["artifact"] == "national_facility_assignment_v6"
    assert summary["artifact_version"] == "6.0.0"
    assert summary["method"]["primary_scenario"] == "province_capacity_floor"
    assert summary["labels"] == {
        "evidence_label": "model_estimate",
        "observed": False,
        "policy_grade": False,
        "official_osecan_changed": False,
    }


def test_floor_scales_proxy_only(summary) -> None:
    floor = summary["province_capacity_floor"]
    assert floor["multiplier"] == 1.2
    assert floor["scaled_provinces"] == ["AB", "SK", "MB", "ON"]
    assert floor["scaled_proxy_records"] == 47
    assert floor["provinces_below_floor_before"] == 5
    assert floor["provinces_below_floor_after"] == 1
    assert floor["shortfall_after_m3_y"] == pytest.approx(43651.2, abs=1.0)
    rows = _read_csv(FLOOR_FACILITY_CSV)
    scaled = [row for row in rows if row["capacity_status_after"] == "proxy_harvest_scaled"]
    assert len(scaled) == 47
    for row in scaled:
        assert row["capacity_status_before"] == "proxy"
        assert float(row["floor_scale_factor"]) > 1.0
        assert row["capacity_evidence_after"] == v6.SCALED_PROXY_EVIDENCE
    published = [row for row in rows if row["capacity_status_after"] == "published"]
    assert published
    for row in published:
        assert float(row["floor_scale_factor"]) == pytest.approx(1.0)
        assert float(row["capacity_m3_y_before"]) == pytest.approx(
            float(row["capacity_m3_y_after"])
        )


def test_local_first_and_small_cross_border(summary, assignments) -> None:
    primary = [
        row for row in assignments if row["scenario"] == "province_capacity_floor"
    ]
    assert primary
    local_orders = [
        int(row["allocation_order"])
        for row in primary
        if row["allocation_phase"] == "local"
    ]
    cross_orders = [
        int(row["allocation_order"])
        for row in primary
        if row["allocation_phase"] == "cross_border"
    ]
    assert max(local_orders) < min(cross_orders)
    metrics = summary["scenarios"]["province_capacity_floor"]
    assert float(metrics["cross_border_m3"]) < 50_000.0
    assert float(metrics["local_share"]) > 0.999
    assert float(metrics["assigned_m3"]) == pytest.approx(118_977_339.1, abs=0.1)


def test_no_unbounded_sink(summary, assignments) -> None:
    assert summary["validation"]["unbounded_assignments"] == 0
    assert summary["validation"]["non_operating_assignments"] == 0
    assert summary["validation"]["no_unbounded_facility"] is True
    assert summary["validation"]["all_facilities_finite_capacity"] is True
    for row in assignments:
        capacity = float(row["capacity_m3_y"])
        assert math.isfinite(capacity) and capacity > 0.0
        assert float(row["assigned_m3"]) <= capacity + 1e-6
    for value in summary["validation"]["max_utilization"].values():
        assert 0.0 <= float(value) <= 1.0 + 1e-9


def test_v5_v6_comparison_moves_the_expected_metrics(summary) -> None:
    rows = _read_csv(COMPARISON_CSV)
    assert list(rows[0]) == v6.ASSIGNMENT_COMPARISON_FIELDS
    by_metric = {row["metric"]: row for row in rows}
    assert float(by_metric["cross_border_m3"]["delta_v6_minus_v5"]) < 0.0
    assert float(by_metric["local_share"]["v6_value"]) > 0.999
    assert float(by_metric["proxy_share_of_assigned"]["delta_v6_minus_v5"]) > 0.0
    assert float(by_metric["scaled_proxy_m3"]["v6_value"]) > 30_000_000.0
    assert float(by_metric["hhi"]["delta_v6_minus_v5"]) > 0.0
    assert float(by_metric["max_facility_share"]["delta_v6_minus_v5"]) > 0.0
    assert float(by_metric["capacity_shortfall_m3_y"]["v6_value"]) < 100_000.0
    assert summary["validation"]["comparison_rows"] == len(rows)


def test_v5_floor_reproduction(summary) -> None:
    with v5.OUTPUT_DIR.joinpath("summary.json").open("r", encoding="utf-8") as handle:
        baseline = json.load(handle)
    old = baseline["scenarios"]["province_capacity_floor"]
    new = summary["scenarios"]["province_capacity_floor"]
    for metric in (
        "assigned_m3",
        "published_m3",
        "proxy_m3",
        "cross_border_m3",
        "facilities_used",
        "max_facility_share",
        "hhi",
    ):
        assert float(new[metric]) == pytest.approx(float(old[metric]), rel=1e-9)
    assert summary["validation"]["v5_floor_reproduced"] is True
