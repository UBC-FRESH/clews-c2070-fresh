"""Regression tests for national facility assignment v5."""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path

import pytest

from workflows.national.facilities import (
    national_facility_assignment_v5 as v5,
)

OUTPUT_DIR = v5.OUTPUT_DIR
ASSIGNMENT_CSV = OUTPUT_DIR / "facility_assignment.csv"
PROVINCE_SUMMARY_CSV = OUTPUT_DIR / "province_summary.csv"
UTILIZATION_CSV = OUTPUT_DIR / "facility_utilization.csv"
UNASSIGNED_CSV = OUTPUT_DIR / "unassigned.csv"
CONCENTRATION_CSV = OUTPUT_DIR / "concentration.csv"
SCENARIO_COMPARISON_CSV = OUTPUT_DIR / "scenario_comparison.csv"
COMPARISON_CSV = OUTPUT_DIR / "v4_v5_comparison.csv"
PROVINCE_CAPACITY_CSV = OUTPUT_DIR / "province_capacity_comparison.csv"
SUMMARY_JSON = OUTPUT_DIR / "summary.json"
MANIFEST_JSON = OUTPUT_DIR / "manifest.json"
REPORT_MD = OUTPUT_DIR / "NATIONAL_FACILITY_ASSIGNMENT_V5.md"
V4_SUMMARY = v5.V4_SUMMARY_JSON


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


@pytest.fixture(scope="module")
def assignments() -> list[dict[str, str]]:
    return _read_csv(ASSIGNMENT_CSV)


@pytest.fixture(scope="module")
def summary() -> dict:
    with SUMMARY_JSON.open("r", encoding="utf-8") as handle:
        return json.load(handle)


@pytest.fixture(scope="module")
def v4_summary() -> dict:
    with V4_SUMMARY.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def test_outputs_exist_and_schema(assignments, summary) -> None:
    for path in (
        ASSIGNMENT_CSV,
        PROVINCE_SUMMARY_CSV,
        UTILIZATION_CSV,
        UNASSIGNED_CSV,
        CONCENTRATION_CSV,
        SCENARIO_COMPARISON_CSV,
        COMPARISON_CSV,
        PROVINCE_CAPACITY_CSV,
        SUMMARY_JSON,
        MANIFEST_JSON,
        REPORT_MD,
    ):
        assert path.exists(), path
    assert list(assignments[0]) == v5.ASSIGNMENT_FIELDS
    assert summary["artifact"] == "national_facility_assignment_v5"
    assert summary["artifact_version"] == "5.0.0"


def test_conservation_per_scenario(summary) -> None:
    for scenario, metrics in summary["scenarios"].items():
        gap = float(metrics["offer_m3"]) - float(metrics["assigned_m3"]) - float(
            metrics["unassigned_m3"]
        )
        assert abs(gap) < 1e-6, (scenario, gap)
        assert summary["validation"]["conservation_gap_m3"][scenario] == pytest.approx(
            0.0, abs=1e-6
        )


def test_no_unbounded_or_operating_violations(summary) -> None:
    assert summary["validation"]["unbounded_assignments"] == 0
    assert summary["validation"]["non_operating_assignments"] == 0
    assert summary["validation"]["no_unbounded_facility"] is True
    assert summary["validation"]["all_facilities_finite_capacity"] is True
    for value in summary["validation"]["max_utilization"].values():
        assert 0.0 <= float(value) <= 1.0 + 1e-9


def test_published_evidence_labels_are_accepted(summary) -> None:
    assert summary["validation"]["published_evidence_accepted"] == sorted(
        v5.PUBLISHED_EVIDENCE
    )
    assert summary["labels"] == {
        "evidence_label": "model_estimate",
        "observed": False,
        "policy_grade": False,
    }
    assert summary["validation"]["no_restricted_sources"] is True


def test_v4_to_v5_comparison_is_complete(summary, v4_summary) -> None:
    rows = _read_csv(COMPARISON_CSV)
    assert len(rows) == len(v5.SCENARIOS) * len(v5.COMPARISON_METRICS)
    assert list(rows[0]) == v5.COMPARISON_FIELDS
    assert {row["scenario"] for row in rows} == set(v5.SCENARIOS)
    assert summary["validation"]["comparison_rows"] == len(rows)
    primary_v4 = v4_summary["scenarios"]["published_plus_proxy"]
    primary_v5 = summary["scenarios"]["published_plus_proxy"]
    assert primary_v5["assigned_m3"] == pytest.approx(primary_v4["assigned_m3"])
    assert primary_v5["published_m3"] > primary_v4["published_m3"]
    assert primary_v5["proxy_m3"] < primary_v4["proxy_m3"]
    assert (
        primary_v5["proxy_share_of_assigned"]
        < primary_v4["proxy_share_of_assigned"]
    )
    assert primary_v5["cross_border_m3"] < primary_v4["cross_border_m3"]
    assert summary["comparison"]["proxy_share_delta"] < 0.0
    assert summary["comparison"]["cross_border_delta_m3"] < 0.0


def test_capacity_v3_new_records_are_eligible(assignments, summary) -> None:
    qc_ids = {
        row["facility_id"]
        for row in assignments
        if row["facility_id"].startswith("PUB26-QC-")
    }
    assert qc_ids
    assert summary["validation"]["capacity_v3_record_count"] == 689
    total_assigned = sum(
        float(row["assigned_m3"] or 0.0)
        for row in assignments
        if row["scenario"] == "published_plus_proxy"
    )
    assert total_assigned == pytest.approx(
        summary["scenarios"]["published_plus_proxy"]["assigned_m3"]
    )


def test_report_and_manifest_match_summary(summary) -> None:
    report = REPORT_MD.read_text(encoding="utf-8")
    assert "National Facility Assignment V5" in report
    assert "proxy share of assigned" in report.lower()
    manifest = json.loads(MANIFEST_JSON.read_text(encoding="utf-8"))
    assert manifest["artifact"] == summary["artifact"]
    assert manifest["outputs"]["summary.json"]["sha256"]
    assert manifest["validation"]["no_unbounded_facility"] is True
    assert math.isfinite(
        float(summary["scenarios"]["published_plus_proxy"]["hhi"])
    )
