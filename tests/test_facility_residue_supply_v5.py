"""Contract tests for facility residue supply v5 total residue."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from workflows.national.facilities import facility_residue_supply_v5 as v5

REQUIRED = (
    "facility_residue_supply.csv",
    "province_residue_supply.csv",
    "national_residue_summary.csv",
    "summary.json",
    "manifest.json",
)
requires_artifacts = pytest.mark.skipif(
    not all((v5.OUTPUT_DIR / name).exists() for name in REQUIRED),
    reason="facility residue supply v5 has not been generated",
)


@requires_artifacts
def test_labels_and_totals() -> None:
    summary = json.loads(
        (v5.OUTPUT_DIR / "summary.json").read_text(encoding="utf-8")
    )
    assert summary["artifact"] == "facility_residue_supply_v5"
    assert summary["scenario"] == "province_capacity_floor"
    assert summary["validation"]["failed_check_count"] == 0
    assert summary["national_totals"]["process_residue_odt"] == pytest.approx(
        27_816_973.784, abs=1.0
    )
    assert summary["national_totals"]["harvest_residue_gross_pj"] == (
        pytest.approx(313.640280, abs=1e-5)
    )


@requires_artifacts
def test_province_rows_conserve() -> None:
    rows = pd.read_csv(v5.OUTPUT_DIR / "province_residue_supply.csv")
    process = rows[rows["residue_type"] == "process_residue"]
    assert process["residue_odt"].sum() == pytest.approx(
        process["origin_process_residue_odt"].sum(), rel=1e-9
    )
    assert (process["residue_pj"] >= 0.0).all()
    assert rows["model_estimate"].all()
    assert not rows["observed"].any()
    assert not rows["policy_grade"].any()
