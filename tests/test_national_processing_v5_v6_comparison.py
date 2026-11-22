"""Regression tests for the assignment-v5/v6 processing comparison."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from workflows.national.facilities import (
    national_processing_v5_v6_comparison as comparison,
)


@pytest.fixture(scope="module")
def built() -> tuple[dict, Path]:
    summary = json.loads((comparison.OUTPUT_DIR / "summary.json").read_text())
    return summary, comparison.OUTPUT_DIR


@pytest.fixture(scope="module")
def assignment_rows(built) -> pd.DataFrame:
    _, target = built
    frame = pd.read_csv(target / "assignment_metrics_comparison.csv", dtype=str)
    for column in ("v5_value", "v6_value", "delta_v6_minus_v5"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.set_index("metric")


@pytest.fixture(scope="module")
def province_rows(built) -> pd.DataFrame:
    _, target = built
    frame = pd.read_csv(
        target / "province_process_residue_comparison.csv", dtype=str
    )
    for column in ("v5_process_residue_odt", "v6_process_residue_odt", "delta_odt"):
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    return frame


@pytest.fixture(scope="module")
def zone_rows(built) -> pd.DataFrame:
    _, target = built
    frame = pd.read_csv(target / "zone_119m_case_comparison.csv", dtype=str)
    for column in ("v5_pj", "v6_pj", "delta_pj"):
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    return frame


def test_outputs_exist_and_labels(built):
    summary, target = built
    assert summary["artifact"] == "national_processing_v5_v6_comparison"
    assert summary["status"] == "pass"
    assert summary["labels"] == {
        "model_estimate": True,
        "observed": False,
        "policy_grade": False,
        "official_osecan_changed": False,
    }
    for name in (
        "assignment_metrics_comparison.csv",
        "province_process_residue_comparison.csv",
        "zone_119m_case_comparison.csv",
        "capacity_shortfall_comparison.csv",
        "summary.json",
        "NATIONAL_PROCESSING_V5_V6_COMPARISON.md",
        "manifest.json",
    ):
        assert (target / name).is_file(), name
    assert summary["validation"]["zone_119m_reproduces_handoff_v4"] is True


def test_assignment_metrics_cover_the_requested_set(assignment_rows):
    for metric in (
        "cross_border_m3",
        "local_share",
        "proxy_share_of_assigned",
        "hhi",
        "max_facility_share",
        "capacity_shortfall_m3_y",
    ):
        assert metric in assignment_rows.index
    assert assignment_rows.loc["cross_border_m3", "delta_v6_minus_v5"] < 0.0
    assert assignment_rows.loc["local_share", "v6_value"] > 0.999
    assert assignment_rows.loc["proxy_share_of_assigned", "delta_v6_minus_v5"] > 0.0
    assert assignment_rows.loc["hhi", "delta_v6_minus_v5"] > 0.0
    assert assignment_rows.loc["max_facility_share", "delta_v6_minus_v5"] > 0.0
    assert assignment_rows.loc["capacity_shortfall_m3_y", "v6_value"] < 100_000.0


def test_province_process_residue_moves_but_conserves(province_rows):
    values = dict(zip(province_rows["province"], province_rows["delta_odt"]))
    assert values["BC"] < -1_000_000.0
    assert values["AB"] > 1_000_000.0
    assert abs(sum(values.values())) < 1e-3


def test_zone_119m_process_shift_and_harvest_invariance(zone_rows):
    process = zone_rows[zone_rows["stream"] == "process"].set_index("zone_id")
    harvest = zone_rows[zone_rows["stream"] == "harvest"]
    assert process.loc["B2", "delta_pj"] < -10.0
    assert process.loc["A1", "delta_pj"] > 10.0
    assert abs(process["delta_pj"].sum()) < 1e-6
    assert harvest["delta_pj"].abs().max() < 1e-9


def test_capacity_shortfall_is_closed_except_nt(built):
    summary, _ = built
    rows = pd.read_csv(
        comparison.OUTPUT_DIR / "capacity_shortfall_comparison.csv", dtype=str
    )
    rows["v6_capacity_shortfall_m3_y"] = pd.to_numeric(
        rows["v6_capacity_shortfall_m3_y"], errors="raise"
    )
    nonzero = rows[rows["v6_capacity_shortfall_m3_y"] > 1e-6]
    assert set(nonzero["province"]) == {"NT"}
    assert nonzero["v6_capacity_shortfall_m3_y"].iloc[0] == pytest.approx(
        43651.2, abs=1.0
    )
    assert summary["validation"]["labels"] is True
