"""Contract tests for forestry OSeCAN scenarios v5 (100/119/150)."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from workflows.national.facilities import forestry_osecan_scenarios_v5 as v5

REQUIRED = (
    "forestry_residue_scenarios.csv",
    "scenario_zone_summary.csv",
    "tier_positions_stem_vs_total.csv",
    "summary.json",
    "manifest.json",
)
requires_artifacts = pytest.mark.skipif(
    not all((v5.OUTPUT_DIR / name).exists() for name in REQUIRED),
    reason="forestry OSeCAN scenarios v5 has not been generated",
)


@pytest.fixture(scope="module")
def summary() -> dict[str, object]:
    return json.loads(
        (v5.OUTPUT_DIR / "summary.json").read_text(encoding="utf-8")
    )


@requires_artifacts
def test_scenarios_and_labels(summary) -> None:
    assert summary["scenario_order"] == ["100m", "119m", "150m"]
    assert summary["labels"] == {
        "model_estimate": True,
        "observed": False,
        "policy_grade": False,
        "national_claim": False,
        "official_osecan_changed": False,
    }
    assert summary["validation"]["passed"] is True
    assert summary["osecan_integrity"]["unchanged"] is True


@requires_artifacts
def test_119m_reconciles_to_handoff_v6(summary) -> None:
    handoff = json.loads(
        (v5.HANDOFF_V2_SUMMARY).read_text(encoding="utf-8")
    )
    for stream in ("process", "harvest"):
        for case, value in handoff["handoff_totals"][stream].items():
            total = summary["national_case_totals"]["119m"][stream][case]["pj"]
            assert total == pytest.approx(value, rel=1e-9, abs=1e-6)


@requires_artifacts
def test_harvest_tier_positions_shift() -> None:
    tiers = pd.read_csv(v5.OUTPUT_DIR / "tier_positions_stem_vs_total.csv")
    assert set(tiers["scenario"]) == {"100m", "119m", "150m"}
    harvest = tiers[tiers["stream"] == "harvest"]
    assert (harvest["delta_pj"] > 0.0).all()
    assert (harvest["tier_changed"]).all()
    row = harvest[
        (harvest["scenario"] == "119m")
        & (harvest["case"] == "harvest_recovery_060")
    ].iloc[0]
    assert row["stem_only_v4_tier"] == "T1"
    assert row["total_residue_v5_tier"] == "T4"
    process = tiers[tiers["stream"] == "process"]
    assert not process["tier_changed"].any()


@requires_artifacts
def test_residue_rows_finite_and_ordered(summary) -> None:
    frame = pd.read_csv(v5.OUTPUT_DIR / "forestry_residue_scenarios.csv")
    numeric = frame.select_dtypes(include=["number"])
    assert not numeric.isna().any().any()
    assert not (numeric < 0.0).any().any()
    for scenario in ("100m", "119m", "150m"):
        totals = summary["national_case_totals"][scenario]
        gross = totals["harvest"]["harvest_recovery_075"]["pj"]
        low = totals["harvest"]["harvest_recovery_035"]["pj"]
        assert low < gross
