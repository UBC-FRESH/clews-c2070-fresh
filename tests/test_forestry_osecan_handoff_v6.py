"""Contract tests for OSeCAN handoff v6 total residue."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from workflows.national.facilities import forestry_osecan_handoff_v6 as v6

REQUIRED = (
    "forestry_residue_handoff.csv",
    "tier_positions_stem_vs_total.csv",
    "summary.json",
    "manifest.json",
)
requires_artifacts = pytest.mark.skipif(
    not all((v6.OUTPUT_DIR / name).exists() for name in REQUIRED),
    reason="forestry OSeCAN handoff v6 has not been generated",
)


@pytest.fixture(scope="module")
def summary() -> dict[str, object]:
    return json.loads(
        (v6.OUTPUT_DIR / "summary.json").read_text(encoding="utf-8")
    )


@requires_artifacts
def test_case_tiers(summary) -> None:
    expected = {
        "harvest/harvest_recovery_035": "T3",
        "harvest/harvest_recovery_060": "T4",
        "harvest/harvest_recovery_075": "above_T4",
        "process/process_availability_000": "T1",
        "process/process_availability_025": "T3",
        "process/process_availability_050": "above_T4",
        "process/process_availability_075": "above_T4",
    }
    assert summary["supply_curve"]["case_tier"] == expected
    assert summary["validation"]["passed"] is True


@requires_artifacts
def test_tier_change_vs_stem_only() -> None:
    tiers = pd.read_csv(v6.OUTPUT_DIR / "tier_positions_stem_vs_total.csv")
    harvest = tiers[tiers["stream"] == "harvest"].set_index("case")
    assert harvest.loc["harvest_recovery_035", "stem_only_v5_tier"] == "T1"
    assert harvest.loc["harvest_recovery_035", "total_residue_v6_tier"] == "T3"
    assert harvest.loc["harvest_recovery_060", "total_residue_v6_tier"] == "T4"
    assert (
        harvest.loc["harvest_recovery_075", "total_residue_v6_tier"]
        == "above_T4"
    )
    assert harvest["tier_changed"].all()
    process = tiers[tiers["stream"] == "process"]
    assert not process["tier_changed"].any()


@requires_artifacts
def test_curve_reconciliation_and_osecan_integrity(summary) -> None:
    curve = json.loads(
        (v6.CURVE_SUMMARY).read_text(encoding="utf-8")
    )
    for case, value in curve["rsyc_comparison"]["harvest_cases"].items():
        assert summary["handoff_totals"]["harvest"][case] == pytest.approx(
            value["recoverable_pj"], rel=1e-9, abs=1e-6
        )
    for case, value in curve["scenario_totals"]["process"].items():
        assert summary["handoff_totals"]["process"][case] == pytest.approx(
            value["residue_pj"], rel=1e-9, abs=1e-6
        )
    assert summary["osecan_integrity"]["unchanged"] is True
    assert summary["labels"] == {
        "model_estimate": True,
        "observed": False,
        "policy_grade": False,
        "national_claim": False,
        "official_osecan_changed": False,
    }


@requires_artifacts
def test_no_nan_or_negative() -> None:
    frame = pd.read_csv(v6.OUTPUT_DIR / "forestry_residue_handoff.csv")
    numeric = frame.select_dtypes(include=["number"])
    assert not numeric.isna().any().any()
    assert not (numeric < 0.0).any().any()
