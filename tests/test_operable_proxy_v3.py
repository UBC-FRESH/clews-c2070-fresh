"""Operable proxy v3 table-combination, 150M arithmetic, and artifacts."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "scripts/national/build_operable_proxy_v3.py"
OUTPUT_DIR = REPO_ROOT / "data/derived/national/managed_landbase/operable_proxy_v3"
V2_DIR = REPO_ROOT / "data/derived/national/managed_landbase/operable_proxy_v2"
SLOPE_DIR = REPO_ROOT / "data/derived/national/managed_landbase/slope_screen"


def _load_module():
    spec = importlib.util.spec_from_file_location("build_operable_proxy_v3", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


operable_v3 = _load_module()


def _v2_fixture() -> pd.DataFrame:
    rows = []
    for province, ecoprovince, ecoprovince_id, coverage, areas in (
        ("AA", "Covered", 1.0, True, {"base": 1000.0, "core20": 500.0, "core30": 250.0}),
        ("BB", "Uncovered", 2.0, False, {"base": np.nan, "core20": np.nan, "core30": np.nan}),
    ):
        for v2_case, area in areas.items():
            rows.append(
                {
                    "province": province,
                    "ecoprovince": ecoprovince,
                    "ecoprovince_id": ecoprovince_id,
                    "v2_case": v2_case,
                    "v2_operable_ha": area,
                    "coverage_available": coverage,
                    "scanfi_forest_ha": 2000.0,
                }
            )
    return pd.DataFrame(rows)


def _slope_fixture() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "province": ["AA", "BB"],
            "ecoprovince": ["Covered", "Uncovered"],
            "share_gt15": [0.2, 0.4],
            "share_gt30": [0.1, 0.3],
            "slope_mean_pct": [2.0, 10.0],
            "slope_stats_available": [True, True],
        }
    )


def _national_fixture() -> pd.DataFrame:
    rows = []
    for v2_case, area in (("base", 200e6), ("core20", 100e6), ("core30", 50e6)):
        for slope_case, share in (("none", 0.0), ("gt15", 0.25), ("gt30", 0.10)):
            lost = area * share
            rows.append(
                {
                    "v2_case": v2_case,
                    "slope_case": slope_case,
                    "v2_operable_ha": area,
                    "lost_ha": lost,
                    "remaining_operable_ha": area - lost,
                }
            )
    return pd.DataFrame(rows)


def test_combine_v3_cases_preserves_none_and_applies_shares() -> None:
    combined = operable_v3.combine_v3_cases(_v2_fixture(), _slope_fixture())
    assert len(combined) == 2 * 3 * 3

    covered = combined[
        (combined["province"] == "AA") & (combined["v2_case"] == "base")
    ].set_index("slope_case")
    assert covered.loc["none", "lost_ha"] == pytest.approx(0.0)
    assert covered.loc["none", "remaining_operable_ha"] == pytest.approx(1000.0)
    assert covered.loc["gt15", "lost_ha"] == pytest.approx(200.0)
    assert covered.loc["gt15", "remaining_operable_ha"] == pytest.approx(800.0)
    assert covered.loc["gt15", "lost_share_of_v2_operable"] == pytest.approx(0.2)
    assert covered.loc["gt30", "lost_ha"] == pytest.approx(100.0)
    assert covered.loc["gt30", "remaining_operable_ha"] == pytest.approx(900.0)
    assert covered.loc["gt30", "lost_share_of_v2_operable"] == pytest.approx(0.1)

    uncovered = combined[combined["province"] == "BB"]
    assert uncovered[["v2_operable_ha", "lost_ha", "remaining_operable_ha"]].isna().all().all()
    assert uncovered["lost_share_of_v2_operable"].isna().all()


def test_combine_v3_cases_orders_cases_and_thresholds() -> None:
    combined = operable_v3.combine_v3_cases(_v2_fixture(), _slope_fixture())
    wide = combined.pivot_table(
        index=["province", "ecoprovince", "v2_case"],
        columns="slope_case",
        values="remaining_operable_ha",
    )
    assert (wide["gt15"] <= wide["gt30"] + 1e-12).all()
    assert (wide["gt30"] <= wide["none"] + 1e-12).all()
    case_wide = combined.pivot_table(
        index=["province", "ecoprovince", "slope_case"],
        columns="v2_case",
        values="remaining_operable_ha",
    )
    assert (case_wide["core30"] <= case_wide["core20"] + 1e-12).all()
    assert (case_wide["core20"] <= case_wide["base"] + 1e-12).all()


def test_build_150m_table_is_target_over_area_arithmetic() -> None:
    table = operable_v3.build_150m_table(_national_fixture())
    assert len(table) == 9
    for label, target in operable_v3.TARGET_VOLUMES_M3_YR.items():
        expected = target / table["post_slope_operable_ha"]
        np.testing.assert_allclose(
            table[f"intensity_{label}_m3_ha_yr"], expected, rtol=0, atol=1e-12
        )
        assert (table[f"target_{label}_m3_yr"] == target).all()

    base_none = table[
        (table["v2_case"] == "base") & (table["slope_case"] == "none")
    ].iloc[0]
    assert base_none["post_slope_operable_ha"] == pytest.approx(200e6)
    assert base_none["intensity_100m_m3_ha_yr"] == pytest.approx(0.5)
    assert base_none["intensity_119m_m3_ha_yr"] == pytest.approx(118_977_339 / 200e6)
    assert base_none["intensity_150m_m3_ha_yr"] == pytest.approx(0.75)
    assert base_none["relative_to_v2_base_none_150m"] == pytest.approx(1.0)


def test_build_150m_table_rejects_zero_area() -> None:
    national = _national_fixture()
    national.loc[0, "remaining_operable_ha"] = 0.0
    with pytest.raises(ValueError, match="positive post-slope area"):
        operable_v3.build_150m_table(national)


def _read(name: str) -> pd.DataFrame:
    path = OUTPUT_DIR / name
    assert path.is_file(), f"missing v3 artifact: {path}"
    return pd.read_csv(path)


def _read_json(name: str) -> dict:
    path = OUTPUT_DIR / name
    assert path.is_file(), f"missing v3 artifact: {path}"
    return json.loads(path.read_text(encoding="utf-8"))


def _as_bool(values: pd.Series) -> pd.Series:
    return values.astype(str).str.strip().str.lower().eq("true")


def test_artifact_columns_and_case_counts() -> None:
    province = _read("operable_v3_by_province.csv")
    ecoprovince = _read("operable_v3_by_ecoprovince.csv")
    table = _read("operable_v3_150m.csv")

    assert {
        "province",
        "v2_case",
        "slope_case",
        "v2_operable_ha",
        "lost_ha",
        "remaining_operable_ha",
        "lost_share_of_v2_operable",
    }.issubset(province.columns)
    assert {
        "province",
        "ecoprovince",
        "ecoprovince_id",
        "v2_case",
        "slope_case",
        "share_gt15",
        "share_gt30",
        "applied_share",
        "lost_ha",
        "remaining_operable_ha",
        "lost_share_of_v2_operable",
    }.issubset(ecoprovince.columns)
    assert {
        "v2_case",
        "slope_case",
        "post_slope_operable_ha",
        "intensity_100m_m3_ha_yr",
        "intensity_119m_m3_ha_yr",
        "intensity_150m_m3_ha_yr",
    }.issubset(table.columns)

    assert len(province) == 13 * 3 * 3
    assert len(ecoprovince) == 84 * 3 * 3
    assert len(table) == 3 * 3
    assert set(province["v2_case"]) == {"base", "core20", "core30"}
    assert set(province["slope_case"]) == {"none", "gt15", "gt30"}


def test_v3_none_equals_v2_all_cases() -> None:
    v2_province = pd.read_csv(V2_DIR / "operable_proxy_v2_by_province.csv")
    v2_ecoprovince = pd.read_csv(V2_DIR / "operable_proxy_v2_by_ecoprovince.csv")
    case_map = operable_v3.V2_CASES
    province = _read("operable_v3_by_province.csv")
    ecoprovince = _read("operable_v3_by_ecoprovince.csv")

    def normalize_v2(frame: pd.DataFrame, keys: list[str]) -> pd.Series:
        subset = frame[frame["screen_case"].isin(case_map.values())].copy()
        subset["v2_case"] = subset["screen_case"].map(
            {source: label for label, source in case_map.items()}
        )
        return subset.set_index([*keys, "v2_case"])["operable_proxy_ha"].sort_index()

    v3_province = (
        province[province["slope_case"] == "none"]
        .set_index(["province", "v2_case"])["v2_operable_ha"]
        .sort_index()
    )
    v3_ecoprovince = (
        ecoprovince[ecoprovince["slope_case"] == "none"]
        .set_index(["province", "ecoprovince", "v2_case"])["v2_operable_ha"]
        .sort_index()
    )
    np.testing.assert_allclose(
        v3_province.to_numpy(dtype=float),
        normalize_v2(v2_province, ["province"]).to_numpy(dtype=float),
        rtol=0,
        atol=1e-6,
        equal_nan=True,
    )
    np.testing.assert_allclose(
        v3_ecoprovince.to_numpy(dtype=float),
        normalize_v2(v2_ecoprovince, ["province", "ecoprovince"]).to_numpy(
            dtype=float
        ),
        rtol=0,
        atol=1e-6,
        equal_nan=True,
    )


def test_v3_gt_losses_match_published_slope_overlay() -> None:
    overlay = pd.read_csv(SLOPE_DIR / "slope_operable_overlay.csv")
    overlay_province = pd.read_csv(
        SLOPE_DIR / "slope_operable_overlay_by_province.csv"
    )
    ecoprovince = _read("operable_v3_by_ecoprovince.csv")
    province = _read("operable_v3_by_province.csv")
    keys = ["province", "ecoprovince"]

    for v2_case, prefix in (("base", "base"), ("core20", "core20")):
        for threshold in (15, 30):
            actual = ecoprovince[
                (ecoprovince["v2_case"] == v2_case)
                & (ecoprovince["slope_case"] == f"gt{threshold}")
            ].set_index(keys)["lost_ha"]
            expected = overlay.set_index(keys)[
                f"{prefix}_lost_gt{threshold}_ha"
            ].reindex(actual.index)
            expected = expected.where(actual.notna())
            np.testing.assert_allclose(
                actual.to_numpy(dtype=float),
                expected.to_numpy(dtype=float),
                rtol=0,
                atol=0.01,
                equal_nan=True,
            )

            actual_province = province[
                (province["v2_case"] == v2_case)
                & (province["slope_case"] == f"gt{threshold}")
            ].set_index("province")["lost_ha"]
            expected_province = overlay_province.set_index("province")[
                f"{prefix}_lost_gt{threshold}_ha"
            ].reindex(actual_province.index)
            expected_province = expected_province.where(
                actual_province.notna()
            )
            np.testing.assert_allclose(
                actual_province.to_numpy(dtype=float),
                expected_province.to_numpy(dtype=float),
                rtol=0,
                atol=0.01,
                equal_nan=True,
            )


def test_province_rows_equal_ecoprovince_sums() -> None:
    province = _read("operable_v3_by_province.csv")
    ecoprovince = _read("operable_v3_by_ecoprovince.csv")
    rollup = ecoprovince.groupby(
        ["province", "v2_case", "slope_case"]
    )[["lost_ha", "remaining_operable_ha"]].sum(min_count=1)
    indexed = province.set_index(["province", "v2_case", "slope_case"])
    np.testing.assert_allclose(
        indexed["lost_ha"].sort_index().to_numpy(dtype=float),
        rollup["lost_ha"].sort_index().to_numpy(dtype=float),
        rtol=0,
        atol=1e-6,
        equal_nan=True,
    )
    np.testing.assert_allclose(
        indexed["remaining_operable_ha"].sort_index().to_numpy(dtype=float),
        rollup["remaining_operable_ha"].sort_index().to_numpy(dtype=float),
        rtol=0,
        atol=1e-6,
        equal_nan=True,
    )


def test_no_nan_outside_coverage_gap_and_no_negative_areas() -> None:
    frame = _read("operable_v3_by_ecoprovince.csv")
    defined = _as_bool(frame["coverage_available"])
    missing = ~defined
    area_columns = [
        "v2_operable_ha",
        "lost_ha",
        "remaining_operable_ha",
        "lost_share_of_v2_operable",
    ]
    assert frame.loc[defined, area_columns].notna().all().all()
    assert frame.loc[missing, area_columns].isna().all().all()
    assert (frame.loc[defined, area_columns] >= 0).all().all()
    assert (
        frame.loc[defined, "lost_ha"]
        <= frame.loc[defined, "v2_operable_ha"] + 1e-6
    ).all()
    assert (
        frame.loc[defined, "remaining_operable_ha"]
        <= frame.loc[defined, "v2_operable_ha"] + 1e-6
    ).all()
    assert set(frame.loc[missing, "province"]) == {"NU", "PE"}


def test_share_and_case_ordering() -> None:
    frame = _read("operable_v3_by_ecoprovince.csv")
    assert (frame["share_gt15"] >= frame["share_gt30"] - 1e-12).all()

    threshold_wide = frame.pivot_table(
        index=["province", "ecoprovince", "v2_case"],
        columns="slope_case",
        values="remaining_operable_ha",
    )
    assert (threshold_wide["gt15"] <= threshold_wide["gt30"] + 1e-6).all()
    assert (threshold_wide["gt30"] <= threshold_wide["none"] + 1e-6).all()

    case_wide = frame.pivot_table(
        index=["province", "ecoprovince", "slope_case"],
        columns="v2_case",
        values="remaining_operable_ha",
    )
    assert (case_wide["core30"] <= case_wide["core20"] + 1e-6).all()
    assert (case_wide["core20"] <= case_wide["base"] + 1e-6).all()


def test_150m_artifact_is_arithmetic_only() -> None:
    table = _read("operable_v3_150m.csv")
    assert len(table) == 9
    for label, target in operable_v3.TARGET_VOLUMES_M3_YR.items():
        expected = target / table["post_slope_operable_ha"]
        # The CSV serialises to 10 decimal places, so allow one unit in the
        # last printed place rather than the in-memory 1e-12 tolerance.
        np.testing.assert_allclose(
            table[f"intensity_{label}_m3_ha_yr"],
            expected,
            rtol=0,
            atol=1e-9,
        )

    base_gt30 = table[
        (table["v2_case"] == "base") & (table["slope_case"] == "gt30")
    ].iloc[0]
    core20_gt30 = table[
        (table["v2_case"] == "core20") & (table["slope_case"] == "gt30")
    ].iloc[0]
    assert base_gt30["intensity_150m_m3_ha_yr"] == pytest.approx(
        1.0526468588839333, rel=1e-9
    )
    assert core20_gt30["intensity_150m_m3_ha_yr"] == pytest.approx(
        1.524529556046326, rel=1e-9
    )


def test_summary_and_manifest_contracts() -> None:
    summary = _read_json("summary.json")
    manifest = _read_json("manifest.json")

    assert summary["method"]["no_new_raster"] is True
    assert "pixel-exact" in summary["method"]["overlay_assumption"]
    assert summary["validation"]["all_checks_pass"] is True
    assert summary["validation"]["v3_none_equals_v2_all_cases"] is True
    assert summary["validation"]["v3_gt15_gt30_match_overlay_base_and_core20"] is True
    assert summary["validation"]["province_sum_equals_ecoprovince_sum_all_cases"] is True
    assert summary["validation"]["150m_arithmetic_exact"] is True
    assert summary["validation"]["slope_coverage_gap_matches_13_27_mha"] is True
    assert summary["national"]["v2_base_operable_ha"] == pytest.approx(
        144_689_810.13119876, rel=1e-12
    )
    assert summary["national"]["coverage_missing_provinces"] == ["NU", "PE"]
    assert summary["slope_coverage"]["treed_outside_rsyc_ecoprovince_ha"] == (
        pytest.approx(13_272_017.759958506, rel=1e-12)
    )
    gap_ids = {gap["gap_id"] for gap in summary["gaps"]}
    assert "treed_outside_rsyc_ecoprovince" in gap_ids
    assert "slope_overlay_assumption" in gap_ids
    assert "legal_thlb_missing" in gap_ids

    assert manifest["schema_version"] == "operable_proxy_v3_manifest_v1"
    assert manifest["validation"]["all_checks_pass"] is True
    assert {entry["path"] for entry in manifest["outputs"]} == {
        "OPERABLE_V3_REPORT.md",
        "operable_v3_150m.csv",
        "operable_v3_by_ecoprovince.csv",
        "operable_v3_by_province.csv",
        "summary.json",
    }
    for entry in manifest["outputs"]:
        assert len(entry["sha256"]) == 64
