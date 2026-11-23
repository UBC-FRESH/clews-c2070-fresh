"""Regression tests for the measured-coefficient OSeCAN handoff and scenarios."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from workflows.national.facilities import forestry_osecan_handoff_v4 as handoff_v4
from workflows.national.facilities import forestry_osecan_handoff_v5 as handoff
from workflows.national.facilities import forestry_osecan_scenarios_v3 as scenarios_v3
from workflows.national.facilities import forestry_osecan_scenarios_v4 as scenarios


def _read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, keep_default_na=False, dtype=str)


@pytest.fixture(scope="module")
def handoff_summary() -> dict:
    return json.loads((handoff.OUTPUT_DIR / "summary.json").read_text())


@pytest.fixture(scope="module")
def scenario_summary() -> dict:
    return json.loads((scenarios.OUTPUT_DIR / "summary.json").read_text())


def test_handoff_deliverables_and_labels(handoff_summary):
    assert handoff_summary["artifact"] == "forestry_osecan_handoff_v5"
    assert handoff_summary["status"] == "pass"
    assert handoff_summary["validation"]["passed"] is True
    labels = handoff_summary["labels"]
    assert labels["model_estimate"] is True
    assert labels["observed"] is False
    assert labels["policy_grade"] is False
    assert labels["official_osecan_changed"] is False
    assert handoff_summary["osecan_integrity"]["unchanged"] is True
    assert handoff_summary["record_counts"]["handoff_rows"] == 119
    assert handoff_summary["record_counts"]["zones"] == 13


def test_handoff_zone_conservation(handoff_summary):
    rows = _read_csv(handoff.OUTPUT_DIR / "zone_summary.csv")
    rows["pj"] = pd.to_numeric(rows["pj"], errors="raise")
    for stream, cases in handoff_summary["handoff_totals"].items():
        for case, expected in cases.items():
            selected = rows[
                (rows["stream"] == stream) & (rows["availability_case"] == case)
            ]
            assert selected["pj"].sum() == pytest.approx(expected, rel=1e-9)


def test_handoff_reflects_measured_harvest_residue(handoff_summary):
    previous = json.loads(
        (handoff_v4.OUTPUT_DIR / "summary.json").read_text()
    )
    for stream, cases in handoff_summary["handoff_totals"].items():
        for case, value in cases.items():
            assert value <= previous["handoff_totals"][stream][case] + 1e-9
    assert handoff_summary["handoff_totals"]["harvest"]["harvest_recovery_060"] < (
        previous["handoff_totals"]["harvest"]["harvest_recovery_060"]
    )


def test_weights_identical_to_v4():
    current = _read_csv(handoff.OUTPUT_DIR / "zone_allocation_weights.csv")
    previous = _read_csv(handoff_v4.OUTPUT_DIR / "zone_allocation_weights.csv")
    merged = current.merge(
        previous,
        on=["province", "zone_id"],
        suffixes=("_v5", "_v4"),
        validate="one_to_one",
    )
    assert len(merged) == len(current) == len(previous)
    assert (
        pd.to_numeric(merged["weight_v5"])
        - pd.to_numeric(merged["weight_v4"])
    ).abs().max() < 1e-12


def test_scenarios_deliverables_and_labels(scenario_summary):
    assert scenario_summary["artifact"] == "forestry_osecan_scenarios_v4"
    assert scenario_summary["status"] == "pass"
    assert scenario_summary["validation"]["passed"] is True
    labels = scenario_summary["labels"]
    assert labels["model_estimate"] is True
    assert labels["observed"] is False
    assert labels["policy_grade"] is False
    assert labels["official_osecan_changed"] is False
    assert labels["candidate_activity_limits_applied"] is False
    assert scenario_summary["scenario_order"] == ["100m", "119m", "150m"]
    assert scenario_summary["record_counts"]["residue_rows"] == 357
    assert scenario_summary["record_counts"]["zone_summary_rows"] == 273


def test_scenarios_119m_matches_curve_and_handoff(scenario_summary):
    curve = json.loads(
        (
            scenarios.CURVE_DIR
            / "summary.json"
        ).read_text()
    )
    national = scenario_summary["national_case_totals"]["119m"]
    for case, expected in curve["scenario_totals"]["process"].items():
        actual = national["process"][case]["pj"]
        assert actual == pytest.approx(expected["residue_pj"], rel=1e-9)
    handoff_summary = json.loads((handoff.OUTPUT_DIR / "summary.json").read_text())
    for case, expected in handoff_summary["handoff_totals"]["process"].items():
        assert national["process"][case]["pj"] == pytest.approx(expected, rel=1e-9)


def test_scenarios_change_vs_donor():
    measured = json.loads((scenarios.OUTPUT_DIR / "summary.json").read_text())
    donor = json.loads((scenarios_v3.OUTPUT_DIR / "summary.json").read_text())
    measured_119 = measured["national_case_totals"]["119m"]
    donor_119 = donor["national_case_totals"]["119m"]
    assert measured_119["process"]["process_availability_050"]["pj"] < (
        donor_119["process"]["process_availability_050"]["pj"]
    )
    assert measured_119["harvest"]["harvest_recovery_060"]["pj"] < (
        donor_119["harvest"]["harvest_recovery_060"]["pj"]
    )
    for block in measured["scenarios"].values():
        assert block["process_base_pj"] > 0.0
