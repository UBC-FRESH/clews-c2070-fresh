"""Contract tests for the total-residue national residue curve v5."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from workflows.national.facilities import national_residue_curve_v5 as v5

REQUIRED = (
    "national_residue_curve.csv",
    "province_residue_curve.csv",
    "summary.json",
    "manifest.json",
)
requires_artifacts = pytest.mark.skipif(
    not all((v5.OUTPUT_DIR / name).exists() for name in REQUIRED),
    reason="national residue curve v5 has not been generated",
)


@requires_artifacts
def test_scenario_totals_and_rsyc() -> None:
    summary = json.loads(
        (v5.OUTPUT_DIR / "summary.json").read_text(encoding="utf-8")
    )
    assert summary["artifact"] == "national_residue_curve_v5"
    assert summary["validation"]["passed"] is True
    process = summary["scenario_totals"]["process"]
    assert process["process_availability_050"]["residue_pj"] == pytest.approx(
        257.3070075, abs=1e-6
    )
    harvest = summary["rsyc_comparison"]["harvest_cases"]
    assert harvest["harvest_recovery_060"]["recoverable_pj"] == pytest.approx(
        188.1841681, abs=1e-6
    )
    assert harvest["harvest_recovery_060"]["rsyc_ratio_to_119m_base"] == (
        pytest.approx(1.4367271, abs=1e-6)
    )


@requires_artifacts
def test_no_combined_curve_row() -> None:
    national = pd.read_csv(v5.OUTPUT_DIR / "national_residue_curve.csv")
    assert set(national["stream"]) == {"process", "harvest"}
    assert not national["residue_type"].astype(str).str.contains(
        "combined"
    ).any()
    harvest = national[national["stream"] == "harvest"]
    assert harvest["rsyc_comparable"].all()
    process = national[national["stream"] == "process"]
    assert not process["rsyc_comparable"].any()
