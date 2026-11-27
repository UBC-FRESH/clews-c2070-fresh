"""Contract tests for the sustainable 100/150 Mm3 WS4 cases."""

from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd
import pytest

from workflows.national.facilities import ws4_sustainable_cases as cases

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
    not all(
        (spec.output_dir / name).exists() for spec in cases.CASES for name in REQUIRED
    )
    and (cases.CASES_ROOT / "summary.json").is_file(),
    reason="sustainable case artifacts have not been generated",
)


def _read(spec: cases.CaseSpec, name: str) -> pd.DataFrame:
    return pd.read_csv(spec.output_dir / name)


def _chain(spec: cases.CaseSpec, name: str) -> pd.DataFrame:
    return pd.read_csv(spec.chain_dir / name)


def _summary(spec: cases.CaseSpec) -> dict:
    return json.loads((spec.chain_dir / "summary.json").read_text(encoding="utf-8"))


def _combined() -> dict:
    return json.loads((cases.CASES_ROOT / "summary.json").read_text(encoding="utf-8"))


def _has_duplicate_block_in_period(schedule: pd.DataFrame) -> bool:
    work = schedule.assign(period=schedule["offset"] // cases.PERIOD_LENGTH)
    return bool(work.duplicated(["block_id", "period"]).any())


@requires_artifacts
def test_control_targets() -> None:
    for spec in cases.CASES:
        shares, total = cases.load_case_control(spec)
        assert set(shares) == set(cases.PROVINCES)
        assert math.isclose(sum(shares.values()), 1.0, abs_tol=1e-12)
        assert total == pytest.approx(spec.target_annual_m3, abs=0.5)
        reference = cases.load_reference_totals(spec)
        assert reference["harvest_m3"] == pytest.approx(total, abs=0.5)
        expected = {
            name: (value, tolerance)
            for name, value, tolerance in cases.EXPECTED_REFERENCE[spec.key]
        }
        for metric, (value, tolerance) in expected.items():
            assert reference[metric] == pytest.approx(value, abs=tolerance)


@requires_artifacts
def test_rotation_and_eligibility() -> None:
    for spec in cases.CASES:
        schedule = _read(spec, "schedule.csv")
        assert list(schedule.columns) == ["block_id", "treatment", "offset"]
        assert schedule["treatment"].eq("CC").all()
        assert schedule["offset"].between(0, cases.HORIZON - 1).all()
        assert not schedule.duplicated(["block_id", "offset"]).any()
        assert not _has_duplicate_block_in_period(schedule)

        selected = _read(spec, "selected_blocks.csv")
        assert len(selected) == len(schedule)
        assert selected["age_at_harvest"].ge(selected["rotation_years"] - 1e-9).all()
        assert selected["rotation_years"].between(40, 200).all()
        assert selected["rotation_raw_years"].lt(40).all()
        assert (selected["rotation_raw_years"] != selected["rotation_years"]).all()
        assert selected["volume_m3"].gt(0.0).all()
        assert selected["area_ha"].gt(0.0).all()
        assert selected["period"].between(0, cases.PERIODS - 1).all()
        assert (selected["offset"] // cases.PERIOD_LENGTH == selected["period"]).all()
        merged = schedule.merge(
            selected[["block_id", "offset"]],
            on=["block_id", "offset"],
            how="outer",
            indicator=True,
        )
        assert merged["_merge"].eq("both").all()


@requires_artifacts
def test_period_targets_and_shortfalls() -> None:
    for spec in cases.CASES:
        periods = _read(spec, "period_harvest.csv")
        summary = _summary(spec)
        target_period = summary["target"]["period_m3"]
        assert len(periods) == cases.PERIODS
        assert periods["target_m3"].eq(target_period).all()
        assert periods["achieved_m3"].ge(target_period).all()
        assert periods["delta_m3"].ge(0.0).all()
        assert periods["block_count"].gt(0).all()
        shortfalls = _read(spec, "shortfalls.csv")
        assert shortfalls.empty
        assert summary["schedule"]["sustained"] is True
        assert summary["schedule"]["period_shortfall_m3"] == pytest.approx(0.0)
        annual_min = float(summary["schedule"]["annual_min_m3"])
        assert summary["schedule"]["annual_floor_met"] == (
            annual_min + 1e-6 >= summary["target"]["annual_m3"]
        )
        assert summary["schedule"]["annual_floor_gap_m3"] == pytest.approx(
            max(0.0, summary["target"]["annual_m3"] - annual_min)
        )


@requires_artifacts
def test_annual_spread() -> None:
    for spec in cases.CASES:
        annual = _read(spec, "annual_harvest.csv")
        summary = _summary(spec)["schedule"]
        target = _summary(spec)["target"]["annual_m3"]
        assert len(annual) == cases.HORIZON
        assert annual["offset"].tolist() == list(range(cases.HORIZON))
        assert annual["block_count"].gt(0).all()
        assert annual["mean_age"].between(40.0, 600.0).all()
        cv = annual["harvest_m3"].std(ddof=0) / annual["harvest_m3"].mean()
        assert cv < 0.02
        assert annual["harvest_m3"].max() <= target * 1.04
        assert annual["harvest_m3"].min() >= target * 0.9999
        assert summary["annual_min_m3"] == pytest.approx(
            annual["harvest_m3"].min(), rel=1e-12
        )
        assert summary["annual_max_m3"] == pytest.approx(
            annual["harvest_m3"].max(), rel=1e-12
        )
        assert summary["annual_mean_m3"] == pytest.approx(
            annual["harvest_m3"].mean(), rel=1e-12
        )


@requires_artifacts
def test_chain_comparison() -> None:
    for spec in cases.CASES:
        chain = _chain(spec, "sustainable_product_chain.csv")
        summary = _summary(spec)
        for basis in (spec.primary_basis, cases.SECONDARY_BASIS):
            subset = chain[chain["basis"] == basis]
            assert set(subset["province"]) == set(cases.PROVINCES) | {"CAN"}
            national = subset.loc[subset["geography_level"] == "national"]
            assert len(national) == 1
            harvest = float(national["harvest_m3"].iloc[0])
            assert harvest == pytest.approx(
                summary["chain"]["national"][basis]["harvest_m3"], rel=1e-12
            )
        primary_harvest = float(
            summary["chain"]["national"][spec.primary_basis]["harvest_m3"]
        )
        achieved_harvest = float(
            summary["chain"]["national"][cases.SECONDARY_BASIS]["harvest_m3"]
        )
        assert primary_harvest == pytest.approx(
            summary["harvest"]["first_period_annual_mean_m3"], rel=1e-12
        )
        assert achieved_harvest == pytest.approx(
            summary["harvest"]["annual_mean_m3"], rel=1e-12
        )
        assert primary_harvest >= spec.target_annual_m3 * 0.999
        assert achieved_harvest >= spec.target_annual_m3 * 0.999

        comparison = _chain(spec, "comparison_vs_v51.csv")
        assert len(comparison) == 22
        for basis in (spec.primary_basis, cases.SECONDARY_BASIS):
            assert np.isfinite(comparison[f"{basis}_delta"].to_numpy(dtype=float)).all()
        harvest_row = comparison.loc[comparison["metric"] == "harvest_m3"].iloc[0]
        assert harvest_row["chain_v51_value"] == pytest.approx(
            spec.target_annual_m3, abs=0.5
        )
        for metric, expected, tolerance in cases.EXPECTED_REFERENCE[spec.key]:
            row = comparison.loc[comparison["metric"] == metric].iloc[0]
            assert row["chain_v51_value"] == pytest.approx(expected, abs=tolerance)
        labels = _chain(spec, "sustainable_product_chain.csv")
        assert labels["sustainable_schedule"].astype(str).str.lower().eq("true").all()
        assert (
            labels["official_osecan_changed"].astype(str).str.lower().eq("false").all()
        )
        assert labels["chain_v51_unchanged"].astype(str).str.lower().eq("true").all()


@requires_artifacts
def test_osecan_tiers_and_official_tree() -> None:
    osecan_dir = cases.CASES_ROOT / "osecan"
    summary = json.loads((osecan_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["frozen_matches"] == 21
    assert summary["frozen_21_of_21"] is True
    assert summary["cases"]["100m"]["primary_matches"] == 7
    assert summary["cases"]["100m"]["secondary_matches"] == 7
    assert summary["cases"]["150m"]["primary_matches"] == 7
    assert summary["cases"]["150m"]["secondary_matches"] == 7
    assert summary["official_unchanged"] is True
    assert summary["official_detail"].startswith("146 files, 80caa91c")

    tiers = pd.read_csv(osecan_dir / "case_tier_positions.csv")
    assert len(tiers) == 28
    assert set(tiers["scenario"]) == {"100m", "150m"}
    assert tiers["tier_changed"].astype(str).str.lower().eq("false").all()
    comparison = pd.read_csv(osecan_dir / "tier_comparison_vs_v10.csv")
    assert len(comparison) == 21
    compared = comparison[
        comparison["comparison_status"] == "compared_national_totals_origin_province"
    ]
    assert len(compared) == 14
    assert (
        compared["ws4_tier_changed_primary"].astype(str).str.lower() == "false"
    ).all()
    assert (
        compared["ws4_tier_changed_secondary"].astype(str).str.lower() == "false"
    ).all()
    for spec in cases.CASES:
        case_tiers = pd.read_csv(osecan_dir / spec.key / "case_tier_positions.csv")
        assert len(case_tiers) == 14
        zones = pd.read_csv(osecan_dir / spec.key / "case_zone_summary.csv")
        assert len(zones) == 182
        assert zones["zone_id"].nunique() == 13


@requires_artifacts
def test_no_nan_in_outputs() -> None:
    for spec in cases.CASES:
        files = [
            spec.output_dir / name
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
    combined = _combined()
    assert combined["status"] == "pass"
    assert combined["validation"]["both_cases_sustained"] is True
    assert combined["validation"]["comparison_rows"] == 44
    assert all(
        math.isfinite(float(value))
        for spec in cases.CASES
        for value in (
            _summary(spec)["schedule"]["annual_min_m3"],
            _summary(spec)["schedule"]["annual_max_m3"],
            _summary(spec)["schedule"]["annual_cv"],
        )
    )


@requires_artifacts
def test_frozen_integrity() -> None:
    combined = _combined()
    assert combined["frozen_integrity"]["chain_v51_unchanged"] is True
    assert combined["frozen_integrity"]["official_osecan_unchanged"] is True
    osecan = json.loads(
        (cases.CASES_ROOT / "osecan" / "summary.json").read_text(encoding="utf-8")
    )
    assert osecan["official_unchanged"] is True
    checks = {check["check"] for check in osecan["validation"]["checks"]}
    assert "official OSeCAN tree hash unchanged" in checks
    assert "v10 reference 21/21 tier positions match frozen v5.1" in checks
