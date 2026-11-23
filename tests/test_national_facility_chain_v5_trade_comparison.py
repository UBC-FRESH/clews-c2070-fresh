"""Contract tests for the local-v6 versus trade-v5 measured comparison."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from workflows.national.facilities import (
    national_facility_chain_v5_trade_comparison as comparison,
)

REQUIRED = (
    "assignment_comparison.csv",
    "chain_comparison.csv",
    "facility_province_comparison.csv",
    "curve_case_comparison.csv",
    "osecan_handoff_comparison.csv",
    "summary.json",
    "manifest.json",
)
requires_artifacts = pytest.mark.skipif(
    not all((comparison.OUTPUT_DIR / name).exists() for name in REQUIRED),
    reason="local/trade comparison has not been generated",
)


@requires_artifacts
def test_comparison_contract() -> None:
    summary = json.loads(
        (comparison.OUTPUT_DIR / "summary.json").read_text(encoding="utf-8")
    )
    assert summary["artifact"] == "national_facility_chain_v5_trade_comparison"
    assert summary["status"] == "pass"
    assert summary["validation"]["passed"] is True
    assert summary["labels"]["official_osecan_changed"] is False
    assert summary["local_case"]["assignment_artifact"] == (
        "national_facility_assignment_v6"
    )
    assert summary["trade_case"]["assignment_artifact"] == (
        "national_facility_assignment_v5"
    )


@requires_artifacts
def test_cross_border_and_conservation() -> None:
    assignment = pd.read_csv(
        comparison.OUTPUT_DIR / "assignment_comparison.csv"
    ).set_index("metric")
    assert assignment.loc["cross_border_m3", "delta_trade_minus_local"] > (
        20_000_000.0
    )
    assert assignment.loc["local_share", "delta_trade_minus_local"] < -0.18

    facility = pd.read_csv(
        comparison.OUTPUT_DIR / "facility_province_comparison.csv"
    )
    process = facility[facility["metric"] == "process_residue_pj"]
    assert abs(process["delta_trade_minus_local"].sum()) < 1e-6
    values = dict(zip(process["province"], process["delta_trade_minus_local"]))
    assert values["BC"] > 50.0
    assert values["AB"] < -50.0

    handoff = pd.read_csv(
        comparison.OUTPUT_DIR / "osecan_handoff_comparison.csv"
    )
    harvest = handoff[handoff["stream"] == "harvest"]
    assert harvest["delta_trade_minus_local"].abs().max() < 1e-9
    process_handoff = handoff[handoff["stream"] == "process"]
    assert abs(process_handoff["delta_trade_minus_local"].sum()) < 1e-6
