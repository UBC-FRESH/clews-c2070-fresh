"""Regression tests for the assignment-v5 national residue curve v2."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd
import pytest

from workflows.national.facilities import facility_residue_supply_v2 as supply
from workflows.national.facilities import national_residue_curve_v2 as curve


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> tuple[dict, Path]:
    target = tmp_path_factory.mktemp("national_residue_curve_v2")
    summary = curve.build(output_dir=target)
    return summary, target


@pytest.fixture(scope="module")
def province_rows(built) -> pd.DataFrame:
    _, target = built
    return pd.read_csv(target / "province_residue_curve.csv", dtype=str)


def test_build_deliverables(built):
    summary, target = built
    assert summary["artifact"] == "national_residue_curve_v2"
    assert summary["status"] == "pass"
    assert summary["validation"]["passed"] is True
    assert summary["labels"]["ui_or_public_osecan_changed"] is False
    for name in (
        "national_residue_curve.csv",
        "province_residue_curve.csv",
        "osecan_demand_context.csv",
        "summary.json",
        "NATIONAL_RESIDUE_CURVE_V2.md",
        "manifest.json",
    ):
        assert (target / name).is_file(), name
    manifest = json.loads((target / "manifest.json").read_text())
    assert manifest["artifact"] == "national_residue_curve_v2"


def test_v5_process_total_and_cases(built):
    summary, _ = built
    facility = json.loads((supply.OUTPUT_DIR / "summary.json").read_text())
    process = summary["scenario_totals"]["process"]
    assert math.isclose(
        process["process_availability_050"]["residue_odt"] * 2.0,
        facility["national_totals"]["process_residue_odt"],
        rel_tol=1e-9,
    )
    assert math.isclose(
        process["process_availability_050"]["residue_pj"] * 2.0,
        facility["national_totals"]["process_residue_pj"],
        rel_tol=1e-9,
    )
    assert process["process_availability_025"]["residue_pj"] == pytest.approx(
        131.501391777, rel=1e-8
    )
    assert process["process_availability_075"]["residue_pj"] == pytest.approx(
        394.504175332, rel=1e-8
    )


def test_harvest_cases_unchanged(built):
    summary, _ = built
    harvest = summary["scenario_totals"]["harvest"]
    assert harvest["harvest_recovery_035"]["recoverable_pj"] == pytest.approx(
        58.722479769, rel=1e-8
    )
    assert harvest["harvest_recovery_060"]["recoverable_pj"] == pytest.approx(
        100.667108175, rel=1e-8
    )
    assert harvest["harvest_recovery_075"]["recoverable_pj"] == pytest.approx(
        125.833885219, rel=1e-8
    )
    assert "gross_pj" not in harvest["harvest_recovery_060"]


def test_province_rows_labels_and_no_combined_row(built, province_rows):
    _, target = built
    assert set(province_rows["residue_type"]) == {
        "process_residue",
        "harvest_residue_recoverable",
    }
    assert (province_rows["model_estimate"].str.lower() == "true").all()
    assert (province_rows["observed"].str.lower() == "false").all()
    assert (province_rows["policy_grade"].str.lower() == "false").all()
    assert not (target / "province_residue_curve.csv").read_text().count(
        "combined"
    )
