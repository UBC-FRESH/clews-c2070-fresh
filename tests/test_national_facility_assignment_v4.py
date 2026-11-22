"""Regression tests for national facility assignment v4."""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path

import pytest

from workflows.national.facilities import national_facility_assignment_v4 as v4

OUTPUT_DIR = v4.OUTPUT_DIR
ASSIGNMENT_CSV = OUTPUT_DIR / "facility_assignment.csv"
PROVINCE_SUMMARY_CSV = OUTPUT_DIR / "province_summary.csv"
UTILIZATION_CSV = OUTPUT_DIR / "facility_utilization.csv"
UNASSIGNED_CSV = OUTPUT_DIR / "unassigned.csv"
PROVINCE_CLASS_CSV = OUTPUT_DIR / "province_class_totals.csv"
CONCENTRATION_CSV = OUTPUT_DIR / "concentration.csv"
SCENARIO_COMPARISON_CSV = OUTPUT_DIR / "scenario_comparison.csv"
FLOOR_CSV = OUTPUT_DIR / "capacity_floor.csv"
FLOOR_FACILITIES_CSV = OUTPUT_DIR / "capacity_floor_facilities.csv"
COMPARISON_CSV = OUTPUT_DIR / "v3_v4_comparison.csv"
PROVINCE_CAPACITY_CSV = OUTPUT_DIR / "province_capacity_comparison.csv"
SUMMARY_JSON = OUTPUT_DIR / "summary.json"
MANIFEST_JSON = OUTPUT_DIR / "manifest.json"
REPORT_MD = OUTPUT_DIR / "NATIONAL_FACILITY_ASSIGNMENT_V4.md"
V3_SUMMARY = v4.V3_SUMMARY_JSON


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


@pytest.fixture(scope="module")
def assignments() -> list[dict[str, str]]:
    return _read_csv(ASSIGNMENT_CSV)


@pytest.fixture(scope="module")
def utilization() -> list[dict[str, str]]:
    return _read_csv(UTILIZATION_CSV)


@pytest.fixture(scope="module")
def summary() -> dict:
    with SUMMARY_JSON.open("r", encoding="utf-8") as handle:
        return json.load(handle)


@pytest.fixture(scope="module")
def v3_summary() -> dict:
    with V3_SUMMARY.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def test_outputs_exist_and_schema(assignments, summary) -> None:
    for path in (
        ASSIGNMENT_CSV,
        PROVINCE_SUMMARY_CSV,
        UTILIZATION_CSV,
        UNASSIGNED_CSV,
        PROVINCE_CLASS_CSV,
        CONCENTRATION_CSV,
        SCENARIO_COMPARISON_CSV,
        FLOOR_CSV,
        FLOOR_FACILITIES_CSV,
        COMPARISON_CSV,
        PROVINCE_CAPACITY_CSV,
        SUMMARY_JSON,
        MANIFEST_JSON,
        REPORT_MD,
    ):
        assert path.exists(), path
    assert list(assignments[0]) == v4.ASSIGNMENT_FIELDS
    assert summary["artifact"] == "national_facility_assignment_v4"
    assert summary["comparison"]["primary_scenario"] == "published_plus_proxy"


def test_all_scenarios_present_and_conserve(assignments, summary) -> None:
    assert set(summary["scenarios"]) == set(v4.SCENARIOS)
    for scenario, metrics in summary["scenarios"].items():
        assert abs(metrics["assigned_m3"] - 118_977_339.1) < 1.0 or (
            scenario == "published_only"
        )
        gap = (
            metrics["offer_m3"]
            - metrics["assigned_m3"]
            - metrics["unassigned_m3"]
        )
        assert abs(gap) < 0.01
        assert summary["validation"]["conservation_gap_m3"][scenario] == pytest.approx(0.0)
    assert summary["scenarios"]["published_plus_proxy"]["assigned_m3"] == pytest.approx(
        118_977_339.1
    )
    assert summary["scenarios"]["published_plus_proxy"]["unassigned_m3"] == pytest.approx(
        0.0, abs=1e-6
    )


def test_no_unbounded_sink(assignments, utilization, summary) -> None:
    assert summary["validation"]["unbounded_assignments"] == 0
    assert summary["validation"]["no_unbounded_facility"] is True
    for row in assignments:
        capacity = float(row["capacity_m3_y"])
        assigned = float(row["assigned_m3"])
        assert math.isfinite(capacity) and capacity > 0.0
        assert assigned <= capacity + 1e-6
    for row in utilization:
        if row["utilization"] == "":
            continue
        assert float(row["utilization"]) <= 1.0 + 1e-9


def test_non_operating_and_other_excluded(assignments) -> None:
    fac_ids = {row["facility_id"] for row in assignments}
    assert "CBM25-BC-11" not in fac_ids  # Skeena Bioenergy, temporarily closed
    assert not any(
        row["facility_class"] == "OTHER" for row in assignments
    )
    assert v4.v2.NON_OPERATING_STATUSES >= {"temporarily_closed", "disused"}


def test_converted_pellet_capacity_is_used(assignments, summary) -> None:
    converted = [
        row
        for row in assignments
        if row["capacity_evidence"] == "published_capacity_converted"
    ]
    assert converted
    assert {
        row["facility_id"] for row in converted if row["facility_id"].startswith("CBM25-")
    }
    assert summary["validation"]["capacity_evidence"][
        "published_converted_records"
    ] == 33


def test_primary_scenario_improves_on_v3(summary, v3_summary) -> None:
    old = v3_summary["scenarios"]["published_plus_proxy"]
    new = summary["scenarios"]["published_plus_proxy"]
    comparison = summary["comparison"]
    assert new["cross_border_m3"] < old["cross_border_m3"]
    assert new["cross_border_share"] < old["cross_border_share"]
    assert new["proxy_share_of_assigned"] < old["proxy_share_of_assigned"]
    assert new["hhi"] < old["hhi"]
    assert new["max_facility_share"] == pytest.approx(
        old["max_facility_share"], abs=1e-12
    )
    assert comparison["cross_border_delta_m3"] < 0.0
    assert comparison["proxy_share_delta"] < 0.0
    assert comparison["hhi_delta"] < 0.0


def test_comparison_rows_cover_requested_metrics(summary) -> None:
    rows = _read_csv(COMPARISON_CSV)
    assert len(rows) == len(v4.SCENARIOS) * len(v4.COMPARISON_METRICS)
    metrics = {(row["scenario"], row["metric"]) for row in rows}
    for scenario in v4.SCENARIOS:
        for metric in (
            "cross_border_m3",
            "proxy_share_of_assigned",
            "max_facility_share",
            "hhi",
        ):
            assert (scenario, metric) in metrics
    assert all("v3_value" in row and "v4_value" in row for row in rows)


def test_province_capacity_comparison(summary, v3_summary) -> None:
    rows = _read_csv(PROVINCE_CAPACITY_CSV)
    assert len(rows) == 13
    by_province = {row["province"]: row for row in rows}
    for province in ("AB", "ON", "BC", "QC", "MB"):
        row = by_province[province]
        assert float(row["v4_published_capacity_m3_y"]) > float(
            row["v3_published_capacity_m3_y"]
        )
    nt = by_province["NT"]
    assert float(nt["v4_capacity_shortfall_m3_y"]) == pytest.approx(43_651.2)
    assert summary["province_capacity_floor"]["shortfall_after_m3_y"] == pytest.approx(
        43_651.2
    )


def test_labels_and_no_restricted_data(assignments, summary) -> None:
    for row in assignments:
        assert row["evidence_label"] == "model_estimate"
        assert row["observed"] == "false"
        assert row["policy_grade"] == "false"
    assert summary["labels"] == {
        "evidence_label": "model_estimate",
        "observed": False,
        "policy_grade": False,
    }
    assert summary["validation"]["no_restricted_sources"] is True
    assert summary["validation"]["no_ui_or_public_osecan_change"] is True
    caveats = " ".join(summary["caveats"]).lower()
    assert "legal/aac" in caveats
    assert "no ui or public osecan" in caveats


def test_floor_sensitivity_still_has_nt_residual(summary) -> None:
    floor = summary["province_capacity_floor"]
    assert floor["multiplier"] == 1.2
    assert floor["provinces_below_floor_after"] == 1
    assert floor["shortfall_after_m3_y"] == pytest.approx(43_651.2)
    assert "temporarily_closed" in summary["method"][
        "non_operating_statuses_excluded"
    ]
