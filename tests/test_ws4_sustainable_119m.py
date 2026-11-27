"""Contract tests for the sustainable 119 Mm3 WS4 schedule and chain."""

from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd
import pytest

from workflows.national.facilities import ws4_sustainable_119m as sustainable

REQUIRED = (
    "schedule.csv",
    "selected_blocks.csv",
    "annual_harvest.csv",
    "period_harvest.csv",
    "province_period_harvest.csv",
    "rotation_distribution.csv",
    "shortfalls.csv",
)
requires_artifacts = pytest.mark.skipif(
    not all((sustainable.OUTPUT_DIR / name).exists() for name in REQUIRED),
    reason="sustainable WS4 schedule has not been generated",
)


def _read(name: str) -> pd.DataFrame:
    return pd.read_csv(sustainable.OUTPUT_DIR / name)


def _chain(name: str) -> pd.DataFrame:
    return pd.read_csv(sustainable.CHAIN_DIR / name)


def _summary() -> dict:
    return json.loads(
        (sustainable.CHAIN_DIR / "summary.json").read_text(encoding="utf-8")
    )


def test_rotation_max_mai_and_cap() -> None:
    ages = np.arange(601, dtype=np.float64)
    linear = 2.0 * ages
    delayed = np.where(
        ages <= 60,
        ages * (1.0 + 0.002 * ages),
        (60.0 * (1.0 + 0.002 * 60.0)) + 1.0 * (ages - 60.0),
    )
    long_rotation = np.where(
        ages <= 300, 2.0 * ages * (1.0 + 0.0001 * ages), 600.0
    )
    matrix = np.vstack([linear, delayed, long_rotation])
    table = sustainable.compute_rotation_table(
        np.array([10, 11, 12], dtype=np.int64), matrix
    )
    assert table["rotation_raw_years"].tolist() == [1, 60, 300]
    assert table["rotation_years"].tolist() == [40, 60, 200]
    assert table["rotation_out_of_range"].tolist() == [True, False, True]
    assert table.loc[1, "max_mai_m3_per_ha_year"] == pytest.approx(1.12)


@requires_artifacts
def test_target_arithmetic_and_control_shares() -> None:
    shares = sustainable.load_control_shares()
    assert set(shares) == set(sustainable.PROVINCES)
    assert sum(shares.values()) == pytest.approx(1.0, abs=1e-12)
    targets = sustainable._period_targets(shares)
    assert sum(targets.values()) == pytest.approx(
        sustainable.TARGET_PERIOD_M3, rel=1e-12
    )
    assert sustainable.TARGET_PERIOD_M3 == pytest.approx(
        sustainable.TARGET_ANNUAL_M3 * 10.0, rel=1e-15
    )
    harvest = _chain("sustainable_harvest_by_province.csv")
    national = harvest.loc[harvest["province"] == "CAN"].iloc[0]
    assert national["annual_119m_m3"] == pytest.approx(
        sustainable.TARGET_ANNUAL_M3, rel=1e-12
    )
    assert national["target_annual_m3"] == pytest.approx(
        sustainable.TARGET_ANNUAL_M3, rel=1e-12
    )


@requires_artifacts
def test_schedule_eligibility_and_no_under_rotation() -> None:
    schedule = _read("schedule.csv")
    assert list(schedule.columns) == ["block_id", "treatment", "offset"]
    assert schedule["treatment"].eq("CC").all()
    assert schedule["offset"].between(0, 99).all()
    assert not schedule["block_id"].duplicated().any()

    selected = _read("selected_blocks.csv")
    assert len(selected) == len(schedule)
    assert selected["age_at_harvest"].ge(selected["rotation_years"] - 1e-9).all()
    assert selected["rotation_years"].between(40, 200).all()
    assert selected["rotation_raw_years"].lt(40).all()
    assert (selected["rotation_raw_years"] != selected["rotation_years"]).all()
    assert selected["volume_m3"].gt(0.0).all()
    assert selected["area_ha"].gt(0.0).all()
    assert selected["period"].between(0, 9).all()
    assert (
        selected["offset"] // sustainable.PERIOD_LENGTH == selected["period"]
    ).all()

    # Every selected block's offset is unique and the schedule is complete.
    merged = schedule.merge(
        selected[["block_id", "offset"]], on=["block_id", "offset"], how="outer",
        indicator=True,
    )
    assert merged["_merge"].eq("both").all()
    assert selected["block_id"].nunique() == len(selected)


@requires_artifacts
def test_period_targets_and_no_shortfall() -> None:
    periods = _read("period_harvest.csv")
    assert len(periods) == sustainable.PERIODS
    assert periods["target_m3"].eq(sustainable.TARGET_PERIOD_M3).all()
    assert periods["achieved_m3"].ge(sustainable.TARGET_PERIOD_M3).all()
    assert periods["delta_m3"].ge(0.0).all()
    assert periods["block_count"].gt(0).all()
    shortfalls = _read("shortfalls.csv")
    assert shortfalls.empty
    summary = _summary()
    assert summary["schedule"]["sustained_119m"] is True
    assert summary["schedule"]["period_shortfall_m3"] == pytest.approx(0.0)


@requires_artifacts
def test_annual_spread() -> None:
    annual = _read("annual_harvest.csv")
    assert len(annual) == sustainable.HORIZON
    assert annual["offset"].tolist() == list(range(sustainable.HORIZON))
    assert annual["harvest_m3"].ge(sustainable.TARGET_ANNUAL_M3).all()
    assert annual["harvest_m3"].max() <= sustainable.TARGET_ANNUAL_M3 * 1.05
    cv = annual["harvest_m3"].std(ddof=0) / annual["harvest_m3"].mean()
    assert cv < 0.02
    assert annual["block_count"].gt(0).all()
    assert annual["mean_age"].between(40.0, 600.0).all()
    summary = _summary()["schedule"]
    assert summary["annual_min_m3"] == pytest.approx(
        annual["harvest_m3"].min(), rel=1e-12
    )
    assert summary["annual_max_m3"] == pytest.approx(
        annual["harvest_m3"].max(), rel=1e-12
    )


@requires_artifacts
def test_ws4_validate_simulate_and_reconciliation() -> None:
    text = sustainable.run_ws4_validate()
    assert "valid" in text.lower()
    run = sustainable.RUN_DIR
    assert (run / "product_values.csv").is_file()
    values = sustainable.extract_run_products()
    assert sorted(values["offset"].unique().tolist()) == list(
        range(sustainable.HORIZON)
    )
    schedule = _read("schedule.csv")
    run_schedule = pd.read_csv(run / "schedule.csv")
    assert sustainable.chain._schedule_matches(run_schedule, schedule)
    selected = _read("selected_blocks.csv")
    province, annual = sustainable.harvest_by_province(selected, values)
    assert set(province["province"]) == set(sustainable.PROVINCES)
    assert province["harvest_m3"].sum() > 0.0
    assert annual["harvest_m3"].sum() == pytest.approx(
        selected["volume_m3"].sum(), rel=1e-9, abs=1e-6
    )


@requires_artifacts
def test_chain_comparison() -> None:
    chain = _chain("sustainable_product_chain.csv")
    for basis in sustainable.BASES:
        subset = chain[chain["basis"] == basis]
        assert set(subset["province"]) == set(sustainable.PROVINCES) | {"CAN"}
        national = subset.loc[subset["geography_level"] == "national"]
        assert len(national) == 1
        harvest = float(national["harvest_m3"].iloc[0])
        if basis == "sustainable_119m":
            assert harvest == pytest.approx(
                sustainable.TARGET_ANNUAL_M3, rel=1e-12
            )
        else:
            assert harvest > sustainable.TARGET_ANNUAL_M3
        assert national["sustainable_schedule"].astype(str).str.lower().eq("true").all()
        assert national["official_osecan_changed"].astype(str).str.lower().eq("false").all()
        assert national["chain_v51_unchanged"].astype(str).str.lower().eq("true").all()
    comparison = _chain("comparison_vs_v51.csv")
    assert len(comparison) == 22
    for column in (
        "sustainable_119m_delta",
        "sustainable_achieved_delta",
    ):
        assert np.isfinite(comparison[column].to_numpy(dtype=float)).all()
    harvest_row = comparison.loc[comparison["metric"] == "harvest_m3"].iloc[0]
    assert harvest_row["sustainable_119m_value"] == pytest.approx(
        sustainable.TARGET_ANNUAL_M3, rel=1e-12
    )
    assert harvest_row["sustainable_achieved_delta_pct"] == pytest.approx(
        harvest_row["sustainable_achieved_delta"]
        / harvest_row["chain_v51_value"]
        * 100.0,
        rel=1e-12,
    )


@requires_artifacts
def test_osecan_tiers_and_official_tree() -> None:
    osecan_dir = sustainable.CHAIN_DIR / "osecan"
    summary = json.loads(
        (osecan_dir / "summary.json").read_text(encoding="utf-8")
    )
    assert summary["frozen_matches"] == 21
    assert summary["frozen_21_of_21"] is True
    assert summary["primary_matches"] == 7
    assert summary["primary_7_of_7"] is True
    assert summary["secondary_matches"] == 7
    assert summary["secondary_7_of_7"] is True
    assert summary["official_unchanged"] is True
    assert summary["official_detail"].startswith("146 files, 80caa91c")
    tiers = pd.read_csv(osecan_dir / "case_tier_positions.csv")
    assert len(tiers) == 14
    assert set(tiers["basis"]) == set(sustainable.BASES)
    assert not tiers["tier_changed"].astype(bool).any()
    zones = pd.read_csv(osecan_dir / "case_zone_summary.csv")
    assert len(zones) == 182
    assert zones["zone_id"].nunique() == 13


@requires_artifacts
def test_no_nan_in_outputs() -> None:
    files = [
        sustainable.OUTPUT_DIR / name
        for name in (
            "schedule.csv",
            "selected_blocks.csv",
            "annual_harvest.csv",
            "period_harvest.csv",
            "province_period_harvest.csv",
            "rotation_distribution.csv",
        )
    ]
    files.extend(
        sustainable.CHAIN_DIR / name
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
    summary = _summary()
    assert all(
        math.isfinite(float(value))
        for value in (
            summary["schedule"]["annual_min_m3"],
            summary["schedule"]["annual_max_m3"],
            summary["schedule"]["annual_cv"],
            summary["chain"]["basis"]["scale_factor"],
        )
    )
