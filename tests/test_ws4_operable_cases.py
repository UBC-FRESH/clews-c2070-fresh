"""Contract tests for the share-based operable sustainable WS4 cases."""

from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd
import pytest

from workflows.national.facilities import ws4_operable_cases as operable

REQUIRED_CASE_FILES = (
    "schedule.csv",
    "selected_blocks.csv",
    "annual_harvest.csv",
    "period_harvest.csv",
    "province_period_harvest.csv",
    "rotation_distribution.csv",
    "shortfalls.csv",
)
requires_artifacts = pytest.mark.skipif(
    not all(
        (spec.output_dir / name).exists()
        for spec in operable.CASES
        for name in REQUIRED_CASE_FILES
    )
    and (operable.CASES_ROOT / "summary.json").is_file()
    and (operable.MASK_DIR / "operable_mask.parquet").is_file(),
    reason="operable case artifacts have not been generated",
)


def _read(spec: operable.OperableCaseSpec, name: str) -> pd.DataFrame:
    return pd.read_csv(spec.output_dir / name)


def _chain(spec: operable.OperableCaseSpec, name: str) -> pd.DataFrame:
    return pd.read_csv(spec.chain_dir / name)


def _summary(spec: operable.OperableCaseSpec) -> dict:
    return json.loads(
        (spec.chain_dir / "summary.json").read_text(encoding="utf-8")
    )


def _combined() -> dict:
    return json.loads(
        (operable.CASES_ROOT / "summary.json").read_text(encoding="utf-8")
    )


@requires_artifacts
def test_mask_area_share_and_uniqueness() -> None:
    summary = pd.read_csv(operable.MASK_DIR / "operable_mask_summary.csv")
    assert set(summary["province"]) == set(operable.PROVINCES)
    assert summary["screen_case"].eq(operable.SCREEN_CASE).all()
    covered = summary[summary["coverage_available"]]
    assert bool(
        (
            covered["share_delta"].abs()
            <= covered["share_tolerance_half_block"] + 1e-12
        ).all()
    )
    assert bool(
        (
            covered["target_delta_ha"].abs()
            <= 0.5 * covered["max_block_area_ha"] + 1e-9
        ).all()
    )
    assert (covered["selected_blocks"] > 0).all()
    uncovered = summary[~summary["coverage_available"]]
    assert set(uncovered["province"]) == {"PE", "NU"}
    assert uncovered["selected_area_ha"].eq(0.0).all()
    assert uncovered["selected_blocks"].eq(0).all()
    assert uncovered["mask_status"].eq("no_tenure_coverage").all()

    mask = pd.read_parquet(operable.MASK_DIR / "operable_mask.parquet")
    assert len(mask) == 1_984_017
    assert mask["block_id"].nunique() == len(mask)
    assert mask["operable_block"].dtype == bool
    assert mask["operable_block"].sum() == int(summary["selected_blocks"].sum())
    assert mask.loc[mask["operable_block"], "area_ha"].sum() == pytest.approx(
        summary["selected_area_ha"].sum(), rel=1e-12, abs=1e-6
    )
    assert mask.loc[mask["operable_block"], "province"].nunique() == 11
    assert not mask.loc[mask["operable_block"], "province"].isin(
        ["PE", "NU"]
    ).any()


def test_mask_selection_is_deterministic_and_unique() -> None:
    block_ids = np.array([1, 2, 3, 4, 5, 6], dtype=np.int64)
    areas = np.array([10.0, 20.0, 30.0, 40.0, 50.0, 60.0])
    province = np.array(["BC"] * 6)
    target = 95.0
    hashes_a = operable.stable_block_hash(province, block_ids)
    hashes_b = operable.stable_block_hash(province.copy(), block_ids.copy())
    assert np.array_equal(hashes_a, hashes_b)
    selected_a = operable.select_blocks_for_share(
        block_ids, areas, hashes_a, target
    )
    selected_b = operable.select_blocks_for_share(
        block_ids, areas, hashes_b, target
    )
    assert np.array_equal(selected_a, selected_b)
    assert np.unique(selected_a).size == selected_a.size
    cumulative = np.cumsum(areas[selected_a])
    assert abs(float(cumulative[-1]) - target) <= 0.5 * float(areas.max())
    assert len(selected_a) > 0
    # Different province labels must not produce the same hash key.
    other = operable.stable_block_hash(np.array(["AB"] * 6), block_ids)
    assert not np.array_equal(hashes_a, other)


@requires_artifacts
def test_operable_only_eligibility() -> None:
    mask = pd.read_parquet(
        operable.MASK_DIR / "operable_mask.parquet",
        columns=["block_id", "operable_block"],
    ).set_index("block_id")["operable_block"]
    for spec in operable.CASES:
        selected = _read(spec, "selected_blocks.csv")
        assert selected["operable_block"].astype(str).str.lower().eq("true").all()
        assert (
            selected["screen_case"].astype(str).eq(operable.SCREEN_CASE).all()
        )
        merged = selected.set_index("block_id").join(
            mask.rename("mask_operable"), how="left"
        )
        assert merged["mask_operable"].fillna(False).all()
        assert not selected.duplicated(["block_id", "period"]).any()
        schedule = _read(spec, "schedule.csv")
        assert list(schedule.columns) == ["block_id", "treatment", "offset"]
        assert schedule["treatment"].eq("CC").all()
        assert schedule["offset"].between(0, operable.HORIZON - 1).all()
        merged_schedule = schedule.merge(
            selected[["block_id", "offset"]],
            on=["block_id", "offset"],
            how="outer",
            indicator=True,
        )
        assert merged_schedule["_merge"].eq("both").all()


@requires_artifacts
def test_no_under_rotation() -> None:
    for spec in operable.CASES:
        selected = _read(spec, "selected_blocks.csv")
        assert selected["age_at_harvest"].ge(
            selected["rotation_years"] - 1e-9
        ).all()
        assert selected["rotation_years"].between(40, 200).all()
        assert selected["volume_m3"].gt(0.0).all()
        assert selected["area_ha"].gt(0.0).all()
        assert (selected["offset"] // operable.PERIOD_LENGTH == selected["period"]).all()


@requires_artifacts
def test_target_and_shortfall_arithmetic() -> None:
    for spec in operable.CASES:
        summary = _summary(spec)
        target_period = summary["target"]["period_m3"]
        periods = _read(spec, "period_harvest.csv")
        assert len(periods) == operable.PERIODS
        assert periods["target_m3"].eq(target_period).all()
        for row in periods.itertuples(index=False):
            assert row.achieved_m3 - row.target_m3 == pytest.approx(
                row.delta_m3, rel=1e-12, abs=1e-6
            )
            assert row.overshoot_pct == pytest.approx(
                (row.achieved_m3 - row.target_m3) / row.target_m3 * 100.0,
                rel=1e-12,
                abs=1e-9,
            )
        shortfalls = _read(spec, "shortfalls.csv")
        assert not shortfalls.duplicated(["period", "province"]).any()
        assert set(shortfalls["province"]).issuperset({"CAN"})
        pe = shortfalls[shortfalls["province"] == "PE"]
        assert len(pe) == operable.PERIODS
        assert pe["shortfall_m3"].gt(0.0).all()
        can = shortfalls[shortfalls["province"] == "CAN"]
        short_periods = periods.loc[
            periods["achieved_m3"] + 1e-6 < periods["target_m3"], "period"
        ]
        assert set(can["period"]) == set(short_periods)
        merged = can.merge(
            periods[["period", "target_m3", "achieved_m3"]].rename(
                columns={"target_m3": "period_target_m3"}
            ),
            on="period",
            validate="one_to_one",
        )
        expected = np.maximum(
            0.0, merged["period_target_m3"] - merged["achieved_m3"]
        )
        assert np.allclose(merged["shortfall_m3"], expected, atol=1e-5, rtol=0.0)
        assert summary["schedule"]["period_shortfall_m3"] == pytest.approx(
            float(can["shortfall_m3"].sum()), rel=1e-12, abs=1e-6
        )
        province = shortfalls[shortfalls["province"] != "CAN"]
        assert summary["schedule"]["province_shortfall_m3"] == pytest.approx(
            float(province["shortfall_m3"].sum()), rel=1e-12, abs=1e-6
        )
        by_province = (
            province.groupby("province", as_index=False)["shortfall_m3"]
            .sum()
            .sort_values("shortfall_m3", ascending=False)
        )
        observed = {
            row["province"]: row["shortfall_m3"]
            for row in summary["schedule"]["shortfall_by_province"]
        }
        assert set(observed) == set(
            by_province.loc[by_province["shortfall_m3"] > 1e-6, "province"]
        )
        for row in by_province.itertuples(index=False):
            if row.shortfall_m3 > 1e-6:
                assert observed[row.province] == pytest.approx(
                    row.shortfall_m3, rel=1e-12, abs=1e-6
                )


@requires_artifacts
def test_annual_spread() -> None:
    for spec in operable.CASES:
        annual = _read(spec, "annual_harvest.csv")
        summary = _summary(spec)["schedule"]
        assert len(annual) == operable.HORIZON
        assert annual["offset"].tolist() == list(range(operable.HORIZON))
        assert annual["block_count"].gt(0).all()
        assert annual["mean_age"].between(40.0, 600.0).all()
        assert float(annual["harvest_m3"].std(ddof=0) / annual["harvest_m3"].mean()) < 0.15
        assert summary["annual_min_m3"] == pytest.approx(
            float(annual["harvest_m3"].min()), rel=1e-12
        )
        assert summary["annual_max_m3"] == pytest.approx(
            float(annual["harvest_m3"].max()), rel=1e-12
        )
        assert summary["annual_mean_m3"] == pytest.approx(
            float(annual["harvest_m3"].mean()), rel=1e-12
        )


@requires_artifacts
def test_ws4_validate_and_reconciliation() -> None:
    text = operable.base.run_ws4_validate()
    assert "valid" in text.lower()
    for spec in operable.CASES:
        run_dir = spec.output_dir / "run_100y"
        assert (run_dir / "product_values.csv").is_file()
        values = operable.base.extract_run_products(
            run_dir, horizon=operable.HORIZON
        )
        assert sorted(values["offset"].unique().tolist()) == list(
            range(operable.HORIZON)
        )
        schedule = _read(spec, "schedule.csv")
        run_schedule = pd.read_csv(run_dir / "schedule.csv")
        assert operable.chain._schedule_matches(run_schedule, schedule)
        selected = _read(spec, "selected_blocks.csv")
        province, annual = operable.base.harvest_by_province(selected, values)
        assert set(province["province"]) == set(operable.PROVINCES)
        assert province["harvest_m3"].sum() == pytest.approx(
            selected["volume_m3"].sum(), rel=1e-9, abs=1e-6
        )
        assert annual["harvest_m3"].sum() == pytest.approx(
            selected["volume_m3"].sum(), rel=1e-9, abs=1e-6
        )


@pytest.mark.slow
def test_ws4_simulate_smoke(tmp_path) -> None:
    if not operable.base.WS4_BIN.is_file():
        pytest.skip("ws4 executable is not available")
    if not operable.base.DATASET_DIR.is_dir():
        pytest.skip("national WS4 dataset has not been built")
    schedule = pd.read_csv(operable.CASES[0].output_dir / "schedule.csv").head(1)
    schedule_path = tmp_path / "schedule.csv"
    schedule.to_csv(schedule_path, index=False)
    run_dir = tmp_path / "run"
    result = operable.base.subprocess.run(
        [
            str(operable.base.WS4_BIN),
            "simulate",
            "--dataset",
            str(operable.base.DATASET_DIR),
            "--schedule",
            str(schedule_path),
            "--years",
            "1",
            "--out",
            str(run_dir),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    values = operable.base.extract_run_products(run_dir, horizon=1)
    assert sorted(values["offset"].unique().tolist()) == [0]
    assert values["value"].sum() > 0.0


@requires_artifacts
def test_chain_and_osecan_tiers() -> None:
    for spec in operable.CASES:
        product = _chain(spec, "sustainable_product_chain.csv")
        assert len(product) == 28
        for basis in (spec.primary_basis, operable.SECONDARY_BASIS):
            subset = product[product["basis"] == basis]
            assert set(subset["province"]) == set(operable.PROVINCES) | {"CAN"}
            national = subset[subset["geography_level"] == "national"]
            assert len(national) == 1
            summary = _summary(spec)
            assert float(national["harvest_m3"].iloc[0]) == pytest.approx(
                summary["chain"]["national"][basis]["harvest_m3"], rel=1e-12
            )
            assert (
                product["share_based_operable_proxy"].astype(str).str.lower() == "true"
            ).all()
            assert (product["legal_thlb"].astype(str).str.lower() == "false").all()
            assert (product["slope_screen"].astype(str).str.lower() == "false").all()
        comparison = _chain(spec, "comparison_vs_v51.csv")
        assert len(comparison) == 22
        for row in comparison.itertuples(index=False):
            assert math.isfinite(float(row.chain_v51_value))
    osecan_dir = operable.CASES_ROOT / "osecan"
    osecan = json.loads((osecan_dir / "summary.json").read_text(encoding="utf-8"))
    assert osecan["frozen_matches"] == 21
    assert osecan["frozen_21_of_21"] is True
    assert osecan["official_unchanged"] is True
    assert osecan["official_detail"].startswith("146 files, 80caa91c")
    tiers = pd.read_csv(osecan_dir / "case_tier_positions.csv")
    assert len(tiers) == 42
    assert set(tiers["scenario"]) == {"100m", "119m", "150m"}
    assert set(tiers["basis"]).issuperset(
        {"operable_100m", "operable_119m", "operable_150m"}
    )
    assert (
        tiers["share_based_operable_proxy"].astype(str).str.lower() == "true"
    ).all()
    assert (tiers["legal_thlb"].astype(str).str.lower() == "false").all()
    assert (tiers["slope_screen"].astype(str).str.lower() == "false").all()
    zones = pd.read_csv(osecan_dir / "case_zone_summary.csv")
    assert len(zones) == 546
    assert zones["zone_id"].nunique() == 13
    comparison = pd.read_csv(osecan_dir / "tier_comparison_vs_v10.csv")
    assert len(comparison) == 21
    compared = comparison[
        comparison["comparison_status"]
        == "compared_national_totals_origin_province"
    ]
    assert len(compared) == 21


@requires_artifacts
def test_levels_and_labels() -> None:
    combined = _combined()
    assert combined["status"] == "pass"
    expected_labels = {
        "model_estimate": True,
        "observed": False,
        "policy_grade": False,
        "national_claim": False,
        "ws4_derived": True,
        "sustainable_schedule": True,
        "share_based_operable_proxy": True,
        "legal_thlb": False,
        "slope_screen": False,
        "official_osecan_changed": False,
        "chain_v51_unchanged": True,
    }
    assert combined["labels"] == expected_labels
    assert combined["operable_screen"]["national"]["selected_blocks"] > 0
    for spec in operable.CASES:
        summary = _summary(spec)
        assert summary["labels"] == expected_labels
        schedule = summary["schedule"]
        assert schedule["sustained"] is False
        assert schedule["eligibility_filter"] == "operable_mask"
        assert schedule["national_fallback"] is False
        max_level = summary["max_sustainable"]
        assert math.isfinite(float(max_level["max_nominal_control_m3"]))
        assert math.isfinite(float(max_level["max_operable_harvest_m3"]))
        assert 0.0 < float(max_level["max_nominal_control_m3"]) < summary["target"]["annual_m3"]
    comparison = pd.read_csv(
        operable.CASES_ROOT / "full_forest_vs_operable.csv"
    )
    assert len(comparison) == 3
    assert comparison["full_forest_sustained"].astype(bool).all()
    assert not comparison["operable_sustained"].astype(bool).any()


@requires_artifacts
def test_no_nan_in_outputs() -> None:
    for spec in operable.CASES:
        files = [
            spec.output_dir / name
            for name in (
                "schedule.csv",
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
            assert np.isfinite(numeric.to_numpy(dtype=float)).all(), path.name
    mask = pd.read_parquet(
        operable.MASK_DIR / "operable_mask.parquet",
        columns=["area_ha", "operable_block"],
    )
    assert np.isfinite(mask["area_ha"].to_numpy(dtype=float)).all()
    assert mask["area_ha"].ge(0.0).all()
