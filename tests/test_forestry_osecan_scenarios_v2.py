"""Regression tests for the assignment-v5 OSeCAN scenarios v2."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd
import pytest

from workflows.national.facilities import forestry_osecan_handoff_v3 as handoff
from workflows.national.facilities import forestry_osecan_scenarios_v2 as scenarios


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> tuple[dict, Path]:
    target = tmp_path_factory.mktemp("forestry_osecan_scenarios_v2")
    summary = scenarios.build(output_dir=target)
    return summary, target


@pytest.fixture(scope="module")
def residue_rows(built) -> pd.DataFrame:
    _, target = built
    return pd.read_csv(target / "forestry_residue_scenarios.csv", dtype=str)


def test_build_deliverables_and_labels(built):
    summary, target = built
    assert summary["artifact"] == "forestry_osecan_scenarios_v2"
    assert summary["status"] == "pass"
    assert summary["validation"]["passed"] is True
    assert summary["labels"]["observed"] is False
    assert summary["labels"]["policy_grade"] is False
    assert summary["labels"]["official_osecan_changed"] is False
    assert summary["osecan_integrity"]["unchanged"] is True
    for name in (
        "forestry_residue_scenarios.csv",
        "scenario_zone_summary.csv",
        "candidate_activity_limits.csv",
        "supply_curve_comparison.csv",
        "summary.json",
        "FORESTRY_OSECAN_SCENARIOS_V2.md",
        "manifest.json",
    ):
        assert (target / name).is_file(), name


def test_119m_reproduces_curve_v2(built):
    summary, _ = built
    curve = json.loads(
        (
            scenarios.DATA_ROOT
            / "derived/national/facilities/national_residue_curve_v2/summary.json"
        ).read_text()
    )
    national = summary["national_case_totals"]["119m"]
    for stream in ("process", "harvest"):
        for case, value in curve["scenario_totals"][stream].items():
            expected = value.get("recoverable_pj", value.get("residue_pj"))
            assert national[stream][case]["pj"] == pytest.approx(
                expected, rel=1e-9, abs=1e-9
            )


def test_zone_conservation_per_case(built):
    _, target = built
    rows = pd.read_csv(target / "scenario_zone_summary.csv", dtype=str)
    rows["pj"] = pd.to_numeric(rows["pj"], errors="raise")
    for (scenario, stream, case), group in rows.groupby(
        ["scenario", "stream", "availability_case"]
    ):
        national = group["national_scenario_case_pj"].astype(float).iloc[0]
        assert math.isclose(group["pj"].sum(), national, rel_tol=1e-9, abs_tol=1e-9)


def test_all_zones_and_no_unmapped(residue_rows):
    assert set(residue_rows["zone_id"]) == set(scenarios.ZONE_ORDER)
    assert "UNMAPPED" not in set(residue_rows["zone_id"])
    assert set(residue_rows["scenario"]) == {"100m", "119m", "150m"}


def test_rows_are_model_estimates(residue_rows):
    for column in ("model_estimate", "observed", "policy_grade", "official_osecan_changed"):
        expected = "true" if column == "model_estimate" else "false"
        assert (residue_rows[column].str.lower() == expected).all()
    assert set(residue_rows["stream"]) == {"process", "harvest"}


def test_handoff_weights_are_used(built):
    summary, target = built
    weights = pd.read_csv(
        handoff.OUTPUT_DIR / "zone_allocation_weights.csv", dtype=str
    )
    assert len(weights) == 17
    assert summary["record_counts"]["province_zone_allocation_rows"] == 17
    rows = pd.read_csv(target / "forestry_residue_scenarios.csv", dtype=str)
    assert rows["evidence_tier"].str.contains("handoff_v3").all()
