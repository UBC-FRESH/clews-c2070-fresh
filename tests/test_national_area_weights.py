"""Validation tests for the national SCANFI area weights and donor inventory."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
AREA_DIR = REPO_ROOT / "data/derived/national/assortments/national_area_weights"
BATCH_PATH = REPO_ROOT / "data/derived/national/assortments/national_batch/national_stands.csv"
RSYC_PATH = REPO_ROOT / "data/raw/national/rsyc/RSYC_ecostrat.gpkg"


def _read(name: str) -> pd.DataFrame:
    path = AREA_DIR / name
    assert path.exists(), f"missing {path}"
    return pd.read_csv(path)


def _assert_area_conservation(frame: pd.DataFrame, keys: list[str]) -> None:
    grouped = frame.groupby(keys, dropna=False).agg(
        allocated=("area_ha", "sum"),
        forest=("forest_area_ha", "first"),
    )
    residual = (grouped["allocated"] - grouped["forest"]).abs()
    tolerance = grouped["forest"].abs() * 1e-6 + 1e-6
    assert (residual <= tolerance).all(), (
        f"area conservation failed for {keys}: max residual {residual.max()} ha"
    )


def test_area_conservation_by_ecoprovince() -> None:
    frame = _read("national_area_weights.csv")
    _assert_area_conservation(frame, ["ecoprovince"])


def test_area_conservation_by_ecodistrict_and_province() -> None:
    frame = _read("national_area_weights_ecodistrict.csv")
    _assert_area_conservation(frame, ["ecodistrict_id", "province"])
    province = _read("national_area_weights_province.csv")
    _assert_area_conservation(province, ["province"])


def test_no_negative_or_nan_areas() -> None:
    for name in (
        "national_area_weights.csv",
        "national_area_weights_ecodistrict.csv",
        "national_area_weights_province.csv",
    ):
        frame = _read(name)
        for column in ("area_ha", "crown_closure_area_ha", "forest_area_ha", "area_fraction_of_forest"):
            assert frame[column].notna().all(), f"{name}: NaN in {column}"
            assert (frame[column] >= 0).all(), f"{name}: negative value in {column}"
        assert (frame["area_fraction_of_forest"] <= 1.0 + 1e-9).all(), f"{name}: share above 1"


def test_all_36_ecoprovinces_present() -> None:
    import geopandas as gpd

    expected = set(gpd.read_file(RSYC_PATH, layer="ecoprovinces")["ecoprovince"])
    assert len(expected) == 36
    observed = set(_read("national_area_weights.csv")["ecoprovince"])
    assert expected <= observed, f"missing ecoprovinces: {sorted(expected - observed)}"


def test_province_totals_and_national_sum() -> None:
    province = _read("national_area_weights_province.csv")
    totals = province.drop_duplicates("province").set_index("province")["forest_area_ha"]
    for code in ("AB", "ON", "NS", "NU", "MB"):
        assert code in totals.index, f"missing province {code}"
        assert totals[code] > 0, f"{code} has no forest area"
    national = _read("national_area_weights.csv").drop_duplicates("ecoprovince")["forest_area_ha"].sum()
    assert abs(totals.sum() - national) <= max(1e-3, national * 1e-9)
    assert 300_000_000 < national < 500_000_000, (
        f"national forest area {national:,.0f} ha is outside the expected SCANFI/NFl envelope"
    )


def test_zero_area_cells_are_explicit_and_ineligible() -> None:
    zero = _read("zero_area_cells.csv")
    assert not zero.empty
    assert zero["scanfi_area_ha"].eq(0).all()
    assert (~zero["supply_eligible"]).all()
    assert zero["reason"].notna().all()
    join = _read("national_batch_area_join.csv")
    positive = join[join["area_match_status"].eq("mapped_positive")]
    assert not set(positive["cell_key"]) & set(zero["cell_key"])


def test_batch_join_preserves_cells_and_marks_mgm_pending() -> None:
    join = _read("national_batch_area_join.csv")
    batch = pd.read_csv(BATCH_PATH)
    assert len(join) == len(batch)
    assert set(join["cell_key"]) == set(batch["cell_key"])
    assert join["mgm_join_pending"].all()
    assert join["scanfi_forest_area_ha"].ge(0).all()
    assert join["scanfi_area_ha"].ge(0).all()
    assert join["ecoprovince"].nunique() == 36


def test_donor_candidates_are_non_self_and_ranked() -> None:
    donors = _read("donor_candidates.csv")
    assert not donors.empty
    assert donors["mgm_join_pending"].all()
    batch = donors[donors["target_type"].eq("national_batch_cell")]
    assert not batch.empty
    assert batch["target_ecoprovince"].ne(batch["candidate_ecoprovince"]).all()
    assert batch["rank"].between(1, 3).all()
    assert batch["similarity_score"].between(0, 1).all()
    assert set(batch["confidence"]) <= {"high", "medium", "low"}
    for province in ("AB", "ON", "NS", "NU", "MB"):
        targeted = batch["target_provinces"].fillna("").str.split(";").apply(
            lambda values, code=province: code in values
        )
        assert targeted.any(), f"no donor candidates target {province}"


def test_no_plot_ecodistricts_have_candidates() -> None:
    donors = _read("donor_candidates.csv")
    no_plot = donors[donors["target_type"].eq("no_plot_ecodistrict")]
    assert no_plot["target_id"].nunique() == 371
    assert no_plot["candidate_id"].ne(no_plot["target_id"]).all()
    assert no_plot.groupby("target_id")["rank"].min().eq(1).all()


def test_crosswalk_and_summary_qa() -> None:
    crosswalk = _read("scanfi_class_crosswalk.csv")
    forest = crosswalk[crosswalk["is_forest"]]
    assert set(forest["rsyc_species"]) >= {
        "PICE.MAR",
        "ABIE.BAL",
        "PINU.BAN",
        "PINU.CON",
        "PINU.PON",
        "PSEU.MEN",
        "LARI.LAR",
        "PINU.STR",
        "PINU.RES",
        "BROADLEAF",
        "PICE.GLA",
        "CONIFEROUS",
    }
    summary = json.loads((AREA_DIR / "national_area_summary.json").read_text())
    assert summary["qa"]["over_100_pixels"] == 0
    assert summary["qa"]["invalid_species_pixels"] == 0
    assert summary["qa"]["unassigned_forest_area_ha"] == pytest.approx(0.0, abs=1e-6)
    assert summary["qa"]["max_relative_area_residual"] < 1e-5
    assert summary["totals"]["forest_area_ha"] > 0
