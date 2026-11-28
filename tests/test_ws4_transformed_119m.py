"""Contract tests for the transformed RSYC national WS4 119 Mm3 case."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from workflows.national.facilities import ws4_transformed_119m as case
from workflows.national.facilities import ws4_transformed_dataset as transformed

REQUIRED_SCHEDULE = (
    "schedule.csv",
    "selected_blocks.csv",
    "annual_harvest.csv",
    "period_harvest.csv",
    "shortfalls.csv",
)
REQUIRED_CHAIN = (
    "summary.json",
    "comparison_vs_v51.csv",
    "comparison_first_period_vs_v51.csv",
    "comparison_to_original_119m.csv",
    "osecan/summary.json",
    "osecan/tier_comparison_vs_v10.csv",
)
requires_artifacts = pytest.mark.skipif(
    not all((case.OUTPUT_DIR / name).exists() for name in REQUIRED_SCHEDULE)
    or not all((case.CHAIN_DIR / name).exists() for name in REQUIRED_CHAIN),
    reason="transformed WS4 119 Mm3 artifacts have not been generated",
)
requires_dataset = pytest.mark.skipif(
    not (case.DATASET_DIR / "curves.parquet").is_file(),
    reason="transformed WS4 dataset has not been generated",
)


def _summary() -> dict:
    return json.loads((case.CHAIN_DIR / "summary.json").read_text(encoding="utf-8"))


def _schedule(name: str) -> pd.DataFrame:
    return pd.read_csv(case.OUTPUT_DIR / name)


def _chain(name: str) -> pd.DataFrame:
    return pd.read_csv(case.CHAIN_DIR / name)


@requires_dataset
def test_curve_mapping_is_unique_and_complete() -> None:
    inputs = transformed.load_transform_inputs()
    model_curves = transformed.build_model_curves(inputs=inputs)
    mapping = transformed.map_ws4_curves(case.DATASET_DIR, model_curves)
    assert len(mapping) == 718
    assert mapping["curve_id"].is_unique
    assert mapping["model_id"].is_unique
    assert mapping["track_count"].sum() == 2799
    assert set(mapping["model_id"]) <= set(model_curves.index)
    assert model_curves.shape[1] == 200


@requires_dataset
def test_v100_preservation_and_patched_curve_shape() -> None:
    original = pd.read_parquet(
        transformed.ORIGINAL_DATASET / "curves.parquet",
        columns=["curve_id", "x", "y"],
    )
    patched = pd.read_parquet(
        case.DATASET_DIR / "curves.parquet", columns=["curve_id", "x", "y"]
    )
    inputs = transformed.load_transform_inputs()
    model_curves = transformed.build_model_curves(inputs=inputs)
    mapping = transformed.map_ws4_curves(case.DATASET_DIR, model_curves)
    checks = transformed.check_original_v100(original, mapping, model_curves)
    assert checks["abs_error_m3_ha"].max() <= 0.01
    assert set(mapping["curve_id"]) == set(patched["curve_id"]) - {1}

    mapped = patched["curve_id"].isin(mapping["curve_id"])
    age_zero = patched.loc[mapped & (patched["x"] == 0.0), "y"]
    assert np.allclose(age_zero, 0.0, atol=0.0)
    assert patched["y"].ge(0.0).all()
    assert patched["y"].notna().all()
    for curve_id in mapping["curve_id"]:
        curve = patched.loc[patched["curve_id"] == curve_id].sort_values("x")
        assert curve["x"].is_unique
        assert curve["x"].is_monotonic_increasing
        age_200 = curve.loc[curve["x"] == 200.0, "y"].iloc[0]
        held = curve.loc[curve["x"] >= 200.0, "y"]
        assert np.allclose(held, age_200, rtol=0.0, atol=1e-12)


@requires_dataset
def test_ws4_validate() -> None:
    text = case.sustainable.run_ws4_validate(case.DATASET_DIR)
    assert "valid" in text.lower()


@requires_artifacts
def test_sustainable_eligibility_and_no_under_rotation() -> None:
    schedule = _schedule("schedule.csv")
    assert list(schedule.columns) == ["block_id", "treatment", "offset"]
    assert schedule["treatment"].eq("CC").all()
    assert schedule["offset"].between(0, 99).all()
    assert not schedule["block_id"].duplicated().any()

    selected = _schedule("selected_blocks.csv")
    assert len(selected) == len(schedule)
    assert selected["age_at_harvest"].ge(selected["rotation_years"] - 1e-9).all()
    assert selected["rotation_years"].between(40, 200).all()
    assert selected["volume_m3"].gt(0.0).all()
    assert selected["area_ha"].gt(0.0).all()
    assert selected["period"].between(0, 9).all()
    assert (
        selected["offset"] // case.sustainable.PERIOD_LENGTH == selected["period"]
    ).all()
    assert selected["block_id"].is_unique
    merged = schedule.merge(
        selected[["block_id", "offset"]],
        on=["block_id", "offset"],
        how="outer",
        indicator=True,
    )
    assert merged["_merge"].eq("both").all()


@requires_artifacts
def test_period_targets_annual_spread_and_no_shortfall() -> None:
    periods = _schedule("period_harvest.csv")
    assert len(periods) == case.sustainable.PERIODS
    assert periods["target_m3"].eq(case.sustainable.TARGET_PERIOD_M3).all()
    assert periods["achieved_m3"].ge(case.sustainable.TARGET_PERIOD_M3).all()
    assert periods["block_count"].gt(0).all()
    assert _schedule("shortfalls.csv").empty

    annual = _schedule("annual_harvest.csv")
    assert len(annual) == case.sustainable.HORIZON
    assert annual["offset"].tolist() == list(range(case.sustainable.HORIZON))
    assert annual["harvest_m3"].ge(case.sustainable.TARGET_ANNUAL_M3).all()
    assert annual["harvest_m3"].notna().all()

    schedule = _summary()["schedule"]
    assert schedule["sustained_119m"] is True
    assert schedule["period_shortfall_m3"] == pytest.approx(0.0)
    assert schedule["annual_min_m3"] == pytest.approx(
        annual["harvest_m3"].min(), rel=1e-12
    )
    assert schedule["annual_max_m3"] == pytest.approx(
        annual["harvest_m3"].max(), rel=1e-12
    )


@requires_artifacts
def test_chain_comparison_and_first_period_basis() -> None:
    comparison = _chain("comparison_vs_v51.csv").set_index("metric")
    expected = {
        "solid_product_m3": (37_674_000.0, 1_000.0),
        "pulp_product_odt": (12_066_000.0, 1_000.0),
        "process_residue_pj": (498.751, 1e-3),
        "harvest_residue_gross_pj": (444.411, 1e-3),
        "harvest_residue_recoverable_r060_pj": (266.646, 1e-3),
        "total_residue_gross_pj": (943.162, 1e-3),
    }
    for metric, (value, tolerance) in expected.items():
        assert comparison.loc[metric, "chain_v51_value"] == pytest.approx(
            value, rel=0.0, abs=tolerance
        )
        assert comparison.loc[metric, "sustainable_119m_value"] > 0.0
        assert abs(comparison.loc[metric, "sustainable_119m_delta_pct"]) < 2.0
    assert comparison.loc["harvest_m3", "sustainable_119m_value"] == pytest.approx(
        case.sustainable.TARGET_ANNUAL_M3, rel=1e-12
    )

    first = _chain("comparison_first_period_vs_v51.csv").set_index("metric")
    assert first.loc["harvest_m3", "first_period_119m_value"] == pytest.approx(
        case.sustainable.TARGET_ANNUAL_M3, rel=1e-12
    )
    assert first.loc["solid_product_m3", "first_period_119m_value"] > 0.0

    original = _chain("comparison_to_original_119m.csv").set_index("metric")
    assert (
        original.loc["selected_blocks", "transformed_rsyc_119m"]
        < original.loc["selected_blocks", "original_rsyc_119m"]
    )
    assert (
        original.loc["mean_rotation_years_area_weighted", "transformed_rsyc_119m"]
        > 40.0
    )


@requires_artifacts
def test_osecan_tiers_and_official_tree() -> None:
    summary = json.loads(
        (case.CHAIN_DIR / "osecan" / "summary.json").read_text(encoding="utf-8")
    )
    assert summary["frozen_21_of_21"] is True
    assert summary["frozen_matches"] == 21
    assert summary["primary_7_of_7"] is True
    assert summary["primary_matches"] == 7
    assert summary["secondary_7_of_7"] is True
    assert summary["secondary_matches"] == 7
    assert summary["official_unchanged"] is True
    assert summary["labels"]["transformed_curves"] is True
    assert summary["labels"]["official_osecan_changed"] is False

    tiers = _chain("osecan/tier_comparison_vs_v10.csv")
    assert len(tiers) == 21
    scenario = tiers[tiers["scenario"] == "119m"]
    assert len(scenario) == 7
    assert (scenario["ws4_basis_primary"] == "sustainable_119m").all()
    assert (
        scenario["ws4_tier_changed_primary"].astype(str).str.lower().eq("false").all()
    )


@requires_artifacts
def test_no_nan_in_artifacts() -> None:
    roots = (case.OUTPUT_DIR, case.CHAIN_DIR)
    for root in roots:
        for path in root.rglob("*.csv"):
            frame = pd.read_csv(path)
            assert not frame.isna().any().any(), f"NaN in {path}"
    for path in (
        case.DATASET_DIR / "curves.parquet",
        case.DATASET_DIR / "blocks.parquet",
        case.DATASET_DIR / "fragments.parquet",
    ):
        frame = pd.read_parquet(path)
        assert not frame.isna().any().any(), f"NaN in {path}"


def test_case_paths_do_not_alias_original_or_frozen_trees() -> None:
    assert case.DATASET_DIR != transformed.ORIGINAL_DATASET
    assert transformed.ORIGINAL_DATASET not in case.DATASET_DIR.parents
    assert case.CHAIN_DIR != case.sustainable.chain.CHAIN_V51_DIR
    assert case.CHAIN_DIR != case.sustainable.chain.CHAIN_V51_DIR.parent / (
        "national_product_chain_v5"
    )
    assert "derived/national/clews/osecan" not in str(case.CHAIN_DIR)
