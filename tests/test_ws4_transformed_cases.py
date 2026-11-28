"""Contract tests for the transformed-curve WS4 sustainable/operable cases."""

from __future__ import annotations

import json
import math
from typing import Any

import numpy as np
import pandas as pd
import pytest

from workflows.national.facilities import ws4_transformed_cases as transformed

REQUIRED_SCHEDULE = (
    "schedule.csv",
    "selected_blocks.csv",
    "annual_harvest.csv",
    "period_harvest.csv",
    "province_period_harvest.csv",
    "rotation_distribution.csv",
    "shortfalls.csv",
)
REQUIRED_CHAIN = (
    "summary.json",
    "comparison_vs_v51.csv",
    "sustainable_product_chain.csv",
    "sustainable_harvest_by_province.csv",
)
requires_artifacts = pytest.mark.skipif(
    not (transformed.CASES_ROOT / "summary.json").is_file()
    or not all(
        (spec.output_dir / name).exists()
        for spec in transformed.SUSTAINABLE_SPECS + transformed.OPERABLE_SPECS
        for name in REQUIRED_SCHEDULE
    )
    or not all(
        (spec.chain_dir / name).exists()
        for spec in transformed.SUSTAINABLE_SPECS + transformed.OPERABLE_SPECS
        for name in REQUIRED_CHAIN
    ),
    reason="transformed WS4 case artifacts have not been generated",
)
requires_dataset = pytest.mark.skipif(
    not (transformed.DATASET_DIR / "curves.parquet").is_file(),
    reason="transformed WS4 dataset has not been generated",
)


def _summary() -> dict:
    return json.loads(
        (transformed.CASES_ROOT / "summary.json").read_text(encoding="utf-8")
    )


def _case_summary(spec: Any) -> dict:
    return json.loads(
        (spec.chain_dir / "summary.json").read_text(encoding="utf-8")
    )


def _schedule(spec: Any, name: str) -> pd.DataFrame:
    return pd.read_csv(spec.output_dir / name)


def _chain(spec: Any, name: str) -> pd.DataFrame:
    return pd.read_csv(spec.chain_dir / name)


@requires_dataset
def test_transformed_dataset_preserves_v100_and_maps_718_curves() -> None:
    build = json.loads(
        (transformed.TRANSFORMED_ROOT / "build_summary.json").read_text(
            encoding="utf-8"
        )
    )
    assert build["mapping"]["ws4_curves_mapped"] == 718
    assert build["mapping"]["transformed_cell_curves"] == 3948
    assert build["v100"]["max_abs_error_m3_ha"] <= build["v100"]["tolerance_m3_ha"]
    assert transformed.DATASET_DIR != transformed.ORIGINAL_DATASET
    assert transformed.ORIGINAL_DATASET not in transformed.DATASET_DIR.parents


@requires_artifacts
def test_case_targets_and_controls() -> None:
    for spec in transformed.SUSTAINABLE_SPECS + transformed.OPERABLE_SPECS:
        rows = pd.read_csv(spec.control_csv)
        assert math.isclose(
            float(rows["harvest_m3"].sum()),
            spec.target_annual_m3,
            rel_tol=0.0,
            abs_tol=0.5,
        )
        assert math.isclose(
            float(rows["allocated_share"].sum()), 1.0, rel_tol=1e-9, abs_tol=1e-6
        )
        summary = _case_summary(spec)
        assert summary["target"]["annual_m3"] == pytest.approx(
            spec.target_annual_m3, abs=0.5
        )
        assert summary["target"]["period_m3"] == pytest.approx(
            spec.target_annual_m3 * 10.0, rel=1e-12
        )


@requires_artifacts
def test_rotation_and_eligibility() -> None:
    for spec in transformed.SUSTAINABLE_SPECS + transformed.OPERABLE_SPECS:
        schedule = _schedule(spec, "schedule.csv")
        assert list(schedule.columns) == ["block_id", "treatment", "offset"]
        assert schedule["treatment"].eq("CC").all()
        assert schedule["offset"].between(0, transformed.base.HORIZON - 1).all()
        assert not schedule.duplicated(["block_id", "offset"]).any()

        selected = _schedule(spec, "selected_blocks.csv")
        assert len(selected) == len(schedule)
        assert selected["age_at_harvest"].ge(
            selected["rotation_years"] - 1e-9
        ).all()
        assert selected["rotation_years"].between(40, 200).all()
        assert selected["volume_m3"].gt(0.0).all()
        assert selected["area_ha"].gt(0.0).all()
        assert not selected.duplicated(["block_id", "period"]).any()
        assert (
            selected["offset"] // transformed.base.PERIOD_LENGTH
            == selected["period"]
        ).all()
        merged = schedule.merge(
            selected[["block_id", "offset"]],
            on=["block_id", "offset"],
            how="outer",
            indicator=True,
        )
        assert merged["_merge"].eq("both").all()


@requires_artifacts
def test_sustainable_period_targets_and_zero_shortfall() -> None:
    for spec in transformed.SUSTAINABLE_SPECS:
        periods = _schedule(spec, "period_harvest.csv")
        summary = _case_summary(spec)
        target_period = summary["target"]["period_m3"]
        assert len(periods) == transformed.base.PERIODS
        assert periods["target_m3"].eq(target_period).all()
        assert periods["achieved_m3"].ge(target_period).all()
        assert periods["block_count"].gt(0).all()
        assert _schedule(spec, "shortfalls.csv").empty
        assert summary["schedule"]["sustained"] is True
        assert summary["schedule"]["period_shortfall_m3"] == pytest.approx(0.0)


@requires_artifacts
def test_operable_shortfalls_are_visible_not_hidden() -> None:
    for spec in transformed.OPERABLE_SPECS:
        summary = _case_summary(spec)
        shortfalls = _schedule(spec, "shortfalls.csv")
        schedule = summary["schedule"]
        can = shortfalls[shortfalls["province"] == "CAN"]
        provinces = shortfalls[shortfalls["province"] != "CAN"]
        assert schedule["period_shortfall_m3"] == pytest.approx(
            float(can["shortfall_m3"].sum()), rel=1e-12, abs=1e-6
        )
        assert schedule["province_shortfall_m3"] == pytest.approx(
            float(provinces["shortfall_m3"].sum()), rel=1e-12, abs=1e-6
        )
        assert schedule["sustained"] == (
            float(schedule["period_min_m3"]) + 1e-6
            >= float(schedule["period_target_m3"])
        )
        covered = {
            row["province"]
            for row in _summary()["operable_screen"]["by_province"]
            if row["coverage_available"]
        }
        province_period = _schedule(spec, "province_period_harvest.csv")
        covered_rows = province_period[province_period["province"].isin(covered)]
        assert schedule["sustained_covered_provinces"] == bool(
            (covered_rows["achieved_m3"] + 1e-6 >= covered_rows["target_m3"]).all()
        )
        max_level = summary["max_sustainable"]
        assert math.isfinite(float(max_level["max_nominal_control_m3"]))
        assert math.isfinite(float(max_level["max_operable_harvest_m3"]))
        assert float(max_level["max_nominal_control_m3"]) <= spec.target_annual_m3


@requires_artifacts
def test_annual_spread_and_summary_consistency() -> None:
    for spec in transformed.SUSTAINABLE_SPECS + transformed.OPERABLE_SPECS:
        annual = _schedule(spec, "annual_harvest.csv")
        summary = _case_summary(spec)["schedule"]
        assert len(annual) == transformed.base.HORIZON
        assert annual["offset"].tolist() == list(range(transformed.base.HORIZON))
        assert annual["harvest_m3"].notna().all()
        assert annual["harvest_m3"].ge(0.0).all()
        assert summary["annual_min_m3"] == pytest.approx(
            annual["harvest_m3"].min(), rel=1e-12
        )
        assert summary["annual_max_m3"] == pytest.approx(
            annual["harvest_m3"].max(), rel=1e-12
        )
        assert summary["annual_mean_m3"] == pytest.approx(
            annual["harvest_m3"].mean(), rel=1e-12
        )
        cv = annual["harvest_m3"].std(ddof=0) / annual["harvest_m3"].mean()
        assert cv == pytest.approx(summary["annual_cv"], rel=1e-12)
        assert summary["selected_blocks"] == len(_schedule(spec, "selected_blocks.csv"))


@requires_artifacts
def test_chain_bases_and_v51_comparison() -> None:
    for spec in transformed.SUSTAINABLE_SPECS + transformed.OPERABLE_SPECS:
        summary = _case_summary(spec)
        chain = _chain(spec, "sustainable_product_chain.csv")
        for basis in (spec.primary_basis, transformed.cases.SECONDARY_BASIS):
            subset = chain[chain["basis"] == basis]
            assert set(subset["province"]) == set(
                transformed.base.PROVINCES
            ) | {"CAN"}
            national = subset[subset["geography_level"] == "national"]
            assert len(national) == 1
            assert float(national["harvest_m3"].iloc[0]) == pytest.approx(
                summary["chain"]["national"][basis]["harvest_m3"], rel=1e-12
            )
        primary = float(
            summary["chain"]["national"][spec.primary_basis]["harvest_m3"]
        )
        achieved = float(
            summary["chain"]["national"][
                transformed.cases.SECONDARY_BASIS
            ]["harvest_m3"]
        )
        assert primary == pytest.approx(
            summary["harvest"]["first_period_annual_mean_m3"], rel=1e-12
        )
        assert achieved == pytest.approx(
            summary["harvest"]["annual_mean_m3"], rel=1e-12
        )
        comparison = _chain(spec, "comparison_vs_v51.csv")
        assert len(comparison) == 22
        for basis in (spec.primary_basis, transformed.cases.SECONDARY_BASIS):
            values = comparison[f"{basis}_value"].to_numpy(dtype=float)
            deltas = comparison[f"{basis}_delta"].to_numpy(dtype=float)
            assert np.isfinite(values).all()
            assert np.isfinite(deltas).all()
        harvest_row = comparison.loc[comparison["metric"] == "harvest_m3"].iloc[0]
        assert float(harvest_row["chain_v51_value"]) == pytest.approx(
            spec.target_annual_m3, abs=0.5
        )
        labels = _chain(spec, "sustainable_product_chain.csv")
        assert labels["transformed_curves"].astype(str).str.lower().eq("true").all()
        assert labels["chain_v51_unchanged"].astype(str).str.lower().eq("true").all()


@requires_artifacts
def test_osecan_tiers_and_official_tree() -> None:
    summary = _summary()
    sustainable = summary["osecan"]["sustainable"]
    assert sustainable["frozen_matches"] == 21
    assert sustainable["frozen_21_of_21"] is True
    assert sustainable["official_unchanged"] is True
    for value in sustainable["cases"].values():
        assert value["positions"] == 7
        assert value["primary_matches"] == 7
        assert value["secondary_matches"] == 7

    operable = summary["osecan"]["operable"]
    assert operable["frozen_matches"] == 21
    assert operable["frozen_21_of_21"] is True
    assert operable["official_unchanged"] is True
    for value in operable["cases"].values():
        assert value["positions"] == 7
        assert 0 < value["primary_matches"] <= 7
        assert 0 < value["secondary_matches"] <= 7

    for tree in (
        transformed.OSECAN_ROOT / "sustainable",
        transformed.OSECAN_ROOT / "operable",
    ):
        tier = pd.read_csv(tree / "tier_comparison_vs_v10.csv")
        assert tier["transformed_curves"].astype(str).str.lower().eq("true").all()
        assert tier["official_osecan_changed"].astype(str).str.lower().eq("false").all()


@requires_artifacts
def test_operable_mask_matches_original_curve_mask() -> None:
    transformed_mask = pd.read_parquet(
        transformed.OPERABLE_MASK_DIR / "operable_mask.parquet",
        columns=["block_id", "operable_block"],
    ).sort_values("block_id", kind="mergesort")
    original_mask = pd.read_parquet(
        transformed.ORIGINAL_OPERABLE_MASK / "operable_mask.parquet",
        columns=["block_id", "operable_block"],
    ).sort_values("block_id", kind="mergesort")
    assert np.array_equal(
        transformed_mask["block_id"].to_numpy(dtype=np.int64),
        original_mask["block_id"].to_numpy(dtype=np.int64),
    )
    assert np.array_equal(
        transformed_mask["operable_block"].to_numpy(dtype=bool),
        original_mask["operable_block"].to_numpy(dtype=bool),
    )


@requires_artifacts
def test_no_nan_in_numeric_artifacts() -> None:
    for spec in transformed.SUSTAINABLE_SPECS + transformed.OPERABLE_SPECS:
        files = [
            spec.output_dir / name
            for name in (
                "selected_blocks.csv",
                "annual_harvest.csv",
                "period_harvest.csv",
                "province_period_harvest.csv",
                "rotation_distribution.csv",
                "shortfalls.csv",
            )
        ]
        files.extend(
            spec.chain_dir / name
            for name in (
                "sustainable_harvest_by_province.csv",
                "sustainable_product_chain.csv",
                "comparison_vs_v51.csv",
            )
        )
        for path in files:
            frame = pd.read_csv(path)
            numeric = frame.select_dtypes(include=[np.number])
            assert np.isfinite(numeric.to_numpy(dtype=float)).all(), path
    for path in transformed.DATASET_DIR.glob("*.parquet"):
        frame = pd.read_parquet(path)
        numeric = frame.select_dtypes(include=[np.number])
        assert np.isfinite(numeric.to_numpy(dtype=float)).all(), path


@requires_artifacts
def test_ws4_validate_and_run_reconciliation() -> None:
    text = transformed.base.run_ws4_validate(transformed.DATASET_DIR)
    assert "valid" in text.lower()
    for spec in transformed.SUSTAINABLE_SPECS + transformed.OPERABLE_SPECS:
        run = spec.output_dir / "run_100y"
        assert (run / "product_values.csv").is_file()
        values = transformed.base.extract_run_products(
            run, horizon=transformed.base.HORIZON
        )
        assert sorted(values["offset"].unique().tolist()) == list(
            range(transformed.base.HORIZON)
        )
        run_schedule = pd.read_csv(run / "schedule.csv")
        schedule = _schedule(spec, "schedule.csv")
        assert transformed.chain._schedule_matches(run_schedule, schedule)
        selected = _schedule(spec, "selected_blocks.csv")
        province, annual = transformed.base.harvest_by_province(selected, values)
        assert set(province["province"]) == set(transformed.base.PROVINCES)
        assert annual["harvest_m3"].sum() == pytest.approx(
            selected["volume_m3"].sum(), rel=1e-9, abs=1e-6
        )


@requires_artifacts
def test_combined_summary_integrity_and_no_alias() -> None:
    summary = _summary()
    assert summary["frozen_integrity"]["chain_v51_unchanged"] is True
    assert summary["frozen_integrity"]["official_osecan_unchanged"] is True
    assert summary["frozen_integrity"]["original_dataset_unchanged"] is True
    assert summary["d_status"]["no_unexpected_deletions"] is True
    assert summary["labels"]["transformed_curves"] is True
    assert summary["labels"]["official_osecan_changed"] is False

    for root in (transformed.CASES_ROOT, transformed.TRANSFORMED_ROOT):
        assert transformed.ORIGINAL_OPERABLE_CASES not in root.parents
        assert "derived/national/clews/osecan" not in str(root)
    assert transformed.DATASET_DIR != transformed.ORIGINAL_DATASET
    assert transformed.CASES_ROOT != transformed.ORIGINAL_SUSTAINABLE_CASES
    assert transformed.CASES_ROOT != transformed.ORIGINAL_OPERABLE_CASES
