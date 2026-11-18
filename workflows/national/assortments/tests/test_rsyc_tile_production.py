"""Regression fixtures for the province-constrained RSYC tile library builders."""

from __future__ import annotations

import math

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import box

from workflows.national.assortments.rsyc_tile_production import (
    TileProductionError,
    allocate_area_to_tiles,
    build_overlay,
    build_province_overlay,
    load_area_weights,
    validate_province_overlay,
)
from workflows.national.rsyc_tile_contract import (
    EVIDENCE_TIERS,
    TileContractError,
    require_known_tier,
)

CRS = "EPSG:3978"


def _tiles(*boxes: tuple[str, tuple[float, float, float, float]]) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"TileID": [name for name, _ in boxes]},
        geometry=[box(*bounds) for _, bounds in boxes],
        crs=CRS,
    )


def _ecodistricts(
    *districts: tuple[int, str, tuple[float, float, float, float]]
) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "ecodistrict_id": [district_id for district_id, _, _ in districts],
            "ecodistrict": [name for _, name, _ in districts],
            "ecozone": ["Test Zone" for _ in districts],
            "ecoprovince": ["Test Province" for _ in districts],
            "ecoregion": ["Test Region" for _ in districts],
            "join_key": [f"Test Zone/Test Province/Test Region/{name}" for _, name, _ in districts],
        },
        geometry=[box(*bounds) for _, _, bounds in districts],
        crs=CRS,
    )


def _provinces(
    *provinces: tuple[str, tuple[float, float, float, float]]
) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "province": [name for name, _ in provinces],
            "province_geometry_repaired": [0 for _ in provinces],
        },
        geometry=[box(*bounds) for _, bounds in provinces],
        crs=CRS,
    )


def _area_weights(rows: list[tuple[int, str, str, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ecodistrict_id": [row[0] for row in rows],
            "province": [row[1] for row in rows],
            "rsyc_species": [row[2] for row in rows],
            "target_species": [row[2] for row in rows],
            "area_ha": [row[3] for row in rows],
        }
    )


def _allocation_fixture():
    tiles = _tiles(("T1", (0.0, 0.0, 1000.0, 1000.0)), ("T2", (1000.0, 0.0, 2000.0, 1000.0)))
    ecodistricts = _ecodistricts((1, "Cross", (0.0, 0.0, 2000.0, 1000.0)))
    provinces = _provinces(
        ("P1", (0.0, 0.0, 500.0, 1000.0)),
        ("P2", (500.0, 0.0, 3000.0, 1000.0)),
    )
    district_overlay = build_overlay(tiles, ecodistricts)
    province_overlay, metrics = build_province_overlay(
        tiles, ecodistricts, provinces, district_overlay
    )
    return tiles, ecodistricts, provinces, district_overlay, province_overlay, metrics


def test_cross_province_asymmetric_overlay_allocates_by_province_fraction() -> None:
    _, _, provinces, district_overlay, province_overlay, metrics = _allocation_fixture()
    validation = validate_province_overlay(province_overlay, provinces, metrics)
    assert all(check["passed"] for check in validation["checks"])

    fractions = {
        (row.tile_id, row.province): row.fraction_of_district_province
        for row in province_overlay.itertuples(index=False)
    }
    assert fractions[("T1", "P1")] == pytest.approx(1.0)
    assert fractions[("T1", "P2")] == pytest.approx(1.0 / 3.0)
    assert fractions[("T2", "P2")] == pytest.approx(2.0 / 3.0)

    weights = _area_weights([(1, "P1", "PICE.MAR", 100.0), (1, "P2", "PICE.MAR", 200.0)])
    tile_species, detail, diagnostics = allocate_area_to_tiles(
        district_overlay, province_overlay, weights
    )
    allocated = {
        (row.tile_id, row.province): row.area_ha
        for row in tile_species.itertuples(index=False)
    }
    assert allocated[("T1", "P1")] == pytest.approx(100.0)
    assert allocated[("T1", "P2")] == pytest.approx(200.0 / 3.0)
    assert allocated[("T2", "P2")] == pytest.approx(400.0 / 3.0)
    assert diagnostics["fallback_area_ha"] == 0.0
    assert diagnostics["district_province_species_closure_failures"] == 0
    assert len(detail) == 3
    assert math.isclose(float(tile_species["area_ha"].sum()), 300.0, abs_tol=1e-9)


def test_boundary_only_contact_is_skipped_not_assigned() -> None:
    tiles = _tiles(("T1", (0.0, 0.0, 1000.0, 1000.0)))
    ecodistricts = _ecodistricts((1, "Edge", (0.0, 0.0, 1000.0, 1000.0)))
    provinces = _provinces(
        ("P1", (0.0, 0.0, 1000.0, 1000.0)),
        ("P2", (1000.0, 0.0, 2000.0, 1000.0)),
    )
    district_overlay = build_overlay(tiles, ecodistricts)
    province_overlay, metrics = build_province_overlay(
        tiles, ecodistricts, provinces, district_overlay
    )
    assert set(province_overlay["province"]) == {"P1"}
    assert metrics["province_overlay_zero_area_contacts_skipped"] >= 1
    assert province_overlay["fraction_of_district_province"].sum() == pytest.approx(1.0)


def test_partially_covered_district_normalises_and_wholly_uncovered_fails() -> None:
    tiles = _tiles(("T1", (0.0, 0.0, 1000.0, 1000.0)))
    ecodistricts = _ecodistricts(
        (1, "Partial", (0.0, 0.0, 2000.0, 1000.0)),
        (2, "Outside", (3000.0, 0.0, 4000.0, 1000.0)),
    )
    provinces = _provinces(("P1", (0.0, 0.0, 1000.0, 1000.0)))
    district_overlay = build_overlay(tiles, ecodistricts)
    province_overlay, _ = build_province_overlay(
        tiles, ecodistricts, provinces, district_overlay
    )
    partial = district_overlay.loc[district_overlay["ecodistrict_id"].eq(1)].iloc[0]
    assert partial["fraction_of_ecodistrict"] == pytest.approx(1.0)
    assert partial["fraction_of_ecodistrict_raw"] == pytest.approx(0.5)
    assert partial["ecodistrict_covered_fraction"] == pytest.approx(0.5)

    weights = _area_weights([(1, "P1", "PICE.MAR", 100.0)])
    tile_species, _, diagnostics = allocate_area_to_tiles(
        district_overlay, province_overlay, weights
    )
    assert float(tile_species["area_ha"].sum()) == pytest.approx(100.0)
    assert diagnostics["redistributed_area_ha"] == pytest.approx(50.0)

    uncovered = _area_weights(
        [(1, "P1", "PICE.MAR", 100.0), (2, "P1", "PICE.MAR", 10.0)]
    )
    with pytest.raises(TileProductionError, match="absent from the tile overlay"):
        allocate_area_to_tiles(district_overlay, province_overlay, uncovered)


def test_area_weights_reject_unknown_province_species_and_non_finite(tmp_path) -> None:
    good = _area_weights([(1, "ON", "PICE.MAR", 100.0)])
    good.to_csv(tmp_path / "good.csv", index=False)
    assert len(load_area_weights(tmp_path / "good.csv")) == 1

    unknown_province = good.assign(province="ZZ")
    unknown_province.to_csv(tmp_path / "province.csv", index=False)
    with pytest.raises(TileProductionError, match="unknown provinces"):
        load_area_weights(tmp_path / "province.csv")

    unknown_species = good.assign(rsyc_species="NOT.A.SPECIES")
    unknown_species.to_csv(tmp_path / "species.csv", index=False)
    with pytest.raises(TileProductionError, match="unknown target species"):
        load_area_weights(tmp_path / "species.csv")

    non_finite = good.assign(area_ha=float("inf"))
    non_finite.to_csv(tmp_path / "infinite.csv", index=False)
    with pytest.raises(TileProductionError, match="non-finite"):
        load_area_weights(tmp_path / "infinite.csv")


def test_shared_contract_has_four_tiers_and_rejects_unknown() -> None:
    assert len(EVIDENCE_TIERS) == 4
    assert "rsyc_tile_control_fallback" in EVIDENCE_TIERS
    with pytest.raises(TileContractError, match="unknown evidence tier"):
        require_known_tier("rsyc_made_up", source="test")
