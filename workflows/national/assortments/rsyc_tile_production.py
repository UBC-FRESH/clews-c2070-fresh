"""Tile-level RSYC production curve library with exact tile x ecodistrict weights.

The 385 published RSYC tiles are the production backbone.  This module:

1. overlays the tile grid on the 672 RSYC ecodistricts with exact polygon
   intersections in EPSG:3978;
2. allocates SCANFI ecodistrict x species area to tiles with the resulting
   intersection fractions;
3. selects one national volume curve per positive ``tile x province x species``
   cell using the tile exact species, tile species group, tile generic, and a
   dominant-ecoregion/ecoprovince control fallback, in that order.

Ecodistrict composition is diagnostic only.  No provincial curve, benchmark
correction factor, or ecodistrict curve is used as a tile production control.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..rsyc_tile_contract import (
    ALLOCATION_BASES,
    EVIDENCE_TIERS,
    require_known_allocation_basis,
)
from .rsyc import load_rsyc_volume_models
from .rsyc_backbone import (
    GROUP_TARGETS,
    SPECIES_GROUP,
    canonical_strata_id,
    evaluate_volume_curve,
)

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_TILES = ROOT / "data/raw/national/rsyc/RSYC_tiles.gpkg"
DEFAULT_ECOSTRAT = ROOT / "data/raw/national/rsyc/RSYC_ecostrat.gpkg"
DEFAULT_MODELS = ROOT / "data/raw/national/rsyc/RSYC_models.zip"
DEFAULT_PROVINCES = (
    ROOT / "data/raw/boundaries/canada_adm1_geoboundaries_simplified.geojson"
)
DEFAULT_AREA_WEIGHTS = (
    ROOT
    / "data/derived/national/assortments/national_area_weights"
    / "national_area_weights_ecodistrict.csv"
)
DEFAULT_OUTPUT_DIR = (
    ROOT / "data/derived/national/assortments/rsyc_tile_production"
)
DEFAULT_PLANNING_NOTE = ROOT / "planning/national-rsyc-backbone.md"

TARGET_CRS = "EPSG:3978"
MIN_POSITIVE_AREA_HA = 1e-9
FRACTION_TOLERANCE = 1e-9
PARTIAL_COVERAGE_TOLERANCE = 1e-4
AREA_TOLERANCE_HA = 1e-3
AGE_GRID: tuple[float, ...] = (50.0, 100.0, 150.0)

PROVINCE_ABBREV: dict[str, str] = {
    "Newfoundland and Labrador": "NL",
    "Prince Edward Island": "PE",
    "Nova Scotia": "NS",
    "New Brunswick": "NB",
    "Quebec": "QC",
    "Ontario": "ON",
    "Manitoba": "MB",
    "Saskatchewan": "SK",
    "Alberta": "AB",
    "British Columbia": "BC",
    "Yukon": "YT",
    "Northwest Territories": "NT",
    "Nunavut": "NU",
}


class TileProductionError(ValueError):
    """Raised when the tile production library cannot be built safely."""


def sha256_file(path: Path) -> str:
    """Return the SHA-256 digest of a file."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _check(name: str, passed: bool, detail: Any) -> dict[str, Any]:
    return {"check": name, "passed": bool(passed), "detail": detail}


def _require_columns(frame: pd.DataFrame, required: set[str], label: str) -> None:
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise TileProductionError(f"{label} missing columns: {', '.join(missing)}")


def _polygonal_part(geometry: Any) -> Any | None:
    """Return the polygonal part of a geometry, or None when it has no area."""
    from shapely import get_parts, unary_union

    if geometry.is_empty:
        return None
    if geometry.geom_type in {"Polygon", "MultiPolygon"}:
        return geometry
    polygons = [
        part
        for part in get_parts(geometry)
        if part.geom_type in {"Polygon", "MultiPolygon"}
    ]
    return unary_union(polygons) if polygons else None


def load_tile_layer(path: Path) -> Any:
    """Load and parse the published RSYC tile grid in EPSG:3978."""
    import geopandas as gpd

    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"RSYC tile layer not found: {path}")
    tiles = gpd.read_file(path, layer="RSYC_tiles")
    _require_columns(tiles, {"TileID", "geometry"}, "RSYC tile layer")
    if tiles.crs is None or tiles.crs.to_string() != TARGET_CRS:
        raise TileProductionError(
            f"tile layer must be {TARGET_CRS}, found {tiles.crs}"
        )
    tiles["TileID"] = tiles["TileID"].astype(str)
    if tiles["TileID"].duplicated().any():
        duplicate_ids = tiles.loc[tiles["TileID"].duplicated(), "TileID"].tolist()
        raise TileProductionError(f"duplicate tile IDs: {duplicate_ids[:5]}")
    if tiles.geometry.is_empty.any():
        raise TileProductionError("tile layer contains empty geometries")
    invalid = ~tiles.geometry.is_valid
    if invalid.any():
        raise TileProductionError(
            f"tile layer contains {int(invalid.sum())} invalid geometries"
        )
    return tiles


def load_ecodistrict_layer(path: Path) -> Any:
    """Load and parse the RSYC ecodistrict boundaries in EPSG:3978."""
    import geopandas as gpd

    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"RSYC ecostrat layer not found: {path}")
    ecodistricts = gpd.read_file(path, layer="ecodistricts")
    _require_columns(
        ecodistricts,
        {
            "ecozone",
            "ecoprovince",
            "ecoregion",
            "ecodistrict",
            "ecodistrict_id",
            "join_key",
            "geometry",
        },
        "RSYC ecodistrict layer",
    )
    if ecodistricts.crs is None or ecodistricts.crs.to_string() != TARGET_CRS:
        raise TileProductionError(
            f"ecodistrict layer must be {TARGET_CRS}, found {ecodistricts.crs}"
        )
    ecodistricts["ecodistrict_id"] = ecodistricts["ecodistrict_id"].astype(int)
    if ecodistricts["ecodistrict_id"].duplicated().any():
        raise TileProductionError("ecodistrict layer contains duplicate ecodistrict_id")
    if ecodistricts.geometry.is_empty.any():
        raise TileProductionError("ecodistrict layer contains empty geometries")
    invalid = ~ecodistricts.geometry.is_valid
    if invalid.any():
        raise TileProductionError(
            f"ecodistrict layer contains {int(invalid.sum())} invalid geometries"
        )
    return ecodistricts


def load_province_layer(path: Path) -> Any:
    """Load the 13 Canadian province/territory polygons in EPSG:3978.

    The simplified GeoBoundaries layer used by the SCANFI area builder has two
    invalid polygons.  They are repaired with ``make_valid`` and reduced to
    their polygonal parts; the repair count is reported by the overlay.
    """
    import geopandas as gpd
    from shapely import make_valid

    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"province layer not found: {path}")
    provinces = gpd.read_file(path).to_crs(TARGET_CRS)
    _require_columns(provinces, {"shapeName", "geometry"}, "province layer")
    provinces = provinces.loc[provinces["shapeName"].isin(PROVINCE_ABBREV)].copy()
    if provinces.shape[0] != len(PROVINCE_ABBREV):
        raise TileProductionError(
            "province layer must contain exactly "
            f"{len(PROVINCE_ABBREV)} provinces/territories, found {provinces.shape[0]}"
        )
    provinces["province"] = provinces["shapeName"].map(PROVINCE_ABBREV)
    if provinces["province"].duplicated().any():
        raise TileProductionError("province layer contains duplicate province labels")
    if provinces.geometry.is_empty.any():
        raise TileProductionError("province layer contains empty geometries")
    repaired = int((~provinces.geometry.is_valid).sum())
    polygonal = provinces.geometry.map(lambda geometry: _polygonal_part(make_valid(geometry)))
    if polygonal.isna().any():
        raise TileProductionError("province layer contains non-polygonal geometries")
    provinces["geometry"] = polygonal
    provinces["province_geometry_repaired"] = repaired
    return provinces[["province", "geometry", "province_geometry_repaired"]]


def build_province_overlay(
    tiles: Any,
    ecodistricts: Any,
    provinces: Any,
    district_overlay: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Intersect every tile x ecodistrict piece with the province polygons.

    The parent intersection is recomputed exactly, then split by every province
    polygon that touches it.  A province that only touches the parent boundary
    contributes zero area and is counted, never assigned.
    """
    from shapely import STRtree

    tile_geometries = tiles.set_index("TileID").geometry
    district_geometries = ecodistricts.set_index("ecodistrict_id").geometry
    province_geometries = provinces["geometry"].to_numpy()
    province_names = provinces["province"].to_numpy()
    province_tree = STRtree(province_geometries)

    records: list[dict[str, Any]] = []
    zero_area_contacts = 0
    for row in district_overlay.itertuples(index=False):
        parent = tile_geometries.loc[row.tile_id].intersection(
            district_geometries.loc[row.ecodistrict_id]
        )
        if parent.is_empty:
            continue
        for position in province_tree.query(parent, predicate="intersects"):
            piece = parent.intersection(province_geometries[position])
            polygonal = _polygonal_part(piece)
            if polygonal is None:
                zero_area_contacts += 1
                continue
            area_ha = float(polygonal.area) / 10_000.0
            if area_ha <= 0.0:
                zero_area_contacts += 1
                continue
            records.append(
                {
                    "tile_id": str(row.tile_id),
                    "ecodistrict_id": int(row.ecodistrict_id),
                    "province": str(province_names[position]),
                    "intersection_area_ha": area_ha,
                    "district_intersection_area_ha": float(row.intersection_area_ha),
                }
            )

    if not records:
        raise TileProductionError("tile x ecodistrict x province overlay is empty")

    overlay = pd.DataFrame.from_records(records)
    duplicate_triples = int(
        overlay.duplicated(["tile_id", "ecodistrict_id", "province"]).sum()
    )
    if duplicate_triples:
        raise TileProductionError(
            f"duplicate tile x ecodistrict x province intersections: {duplicate_triples}"
        )

    hierarchy = district_overlay[
        [
            "tile_id",
            "ecodistrict_id",
            "ecodistrict",
            "ecozone",
            "ecoprovince",
            "ecoregion",
            "ecodistrict_key",
            "ecoregion_key",
            "ecoprovince_key",
            "ecozone_key",
        ]
    ].drop_duplicates(["tile_id", "ecodistrict_id"])
    overlay = overlay.merge(
        hierarchy,
        on=["tile_id", "ecodistrict_id"],
        how="left",
        validate="many_to_one",
    )
    if overlay["ecodistrict_key"].isna().any():
        raise TileProductionError("province overlay lost ecodistrict attributes")

    tile_area = tiles.set_index("TileID").geometry.area / 10_000.0
    overlay["tile_area_ha"] = overlay["tile_id"].map(tile_area)
    covered = overlay.groupby(["ecodistrict_id", "province"])[
        "intersection_area_ha"
    ].transform("sum")
    overlay["district_province_covered_area_ha"] = covered
    overlay["fraction_of_district_province"] = (
        overlay["intersection_area_ha"] / covered
    )
    overlay["fraction_of_tile"] = overlay["intersection_area_ha"] / overlay["tile_area_ha"]
    overlay["province_share_of_district_intersection"] = (
        overlay["intersection_area_ha"] / overlay["district_intersection_area_ha"]
    )

    parent_sum = overlay.groupby(["tile_id", "ecodistrict_id"])[
        "district_intersection_area_ha"
    ].first()
    province_sum = overlay.groupby(["tile_id", "ecodistrict_id"])[
        "intersection_area_ha"
    ].sum()
    parent_outside_province = float((parent_sum - province_sum).sum())
    if (parent_sum - province_sum).min() < -AREA_TOLERANCE_HA:
        raise TileProductionError(
            "province pieces exceed their tile x ecodistrict parent intersection"
        )
    metrics = {
        "province_polygons": int(provinces.shape[0]),
        "province_geometry_repaired": int(
            provinces["province_geometry_repaired"].iloc[0]
        ),
        "province_intersection_pairs": int(overlay.shape[0]),
        "province_district_pairs": int(
            overlay[["ecodistrict_id", "province"]].drop_duplicates().shape[0]
        ),
        "province_overlay_zero_area_contacts_skipped": zero_area_contacts,
        "parent_intersection_area_ha": float(parent_sum.sum()),
        "province_covered_area_ha": float(province_sum.sum()),
        "parent_area_outside_provinces_ha": parent_outside_province,
        "province_cover_share_of_intersection": float(
            province_sum.sum() / parent_sum.sum()
        ),
    }
    return (
        overlay.sort_values(
            ["tile_id", "ecodistrict_id", "province"], ignore_index=True
        ),
        metrics,
    )


def validate_province_overlay(
    overlay: pd.DataFrame, provinces: Any, metrics: Mapping[str, Any]
) -> dict[str, Any]:
    """Check province attribution geometry and normalisation."""
    fraction_sums = overlay.groupby(["ecodistrict_id", "province"])[
        "fraction_of_district_province"
    ].sum()
    fraction_deviation = float((fraction_sums - 1.0).abs().max())
    tile_fraction_sums = overlay.groupby("tile_id")["fraction_of_tile"].sum()
    province_overlap = float(
        provinces.geometry.area.sum() / 10_000.0
        - provinces.geometry.union_all().area / 10_000.0
    )
    checks = [
        _check(
            "province_layer_has_no_overlaps",
            abs(province_overlap) <= AREA_TOLERANCE_HA,
            {"sum_feature_area_minus_union_area_ha": province_overlap},
        ),
        _check(
            "province_district_fractions_sum_to_one",
            fraction_deviation <= FRACTION_TOLERANCE,
            {"max_abs_deviation": fraction_deviation},
        ),
        _check(
            "province_tile_fractions_do_not_exceed_one",
            bool(tile_fraction_sums.le(1.0 + FRACTION_TOLERANCE).all()),
            {"max_tile_fraction_sum": float(tile_fraction_sums.max())},
        ),
        _check(
            "province_intersections_are_polygonal_and_positive",
            bool(
                (overlay["intersection_area_ha"] > 0.0).all()
                and np.isfinite(overlay["intersection_area_ha"]).all()
            ),
            {"min_intersection_area_ha": float(overlay["intersection_area_ha"].min())},
        ),
        _check(
            "province_cover_share_recorded",
            metrics["province_cover_share_of_intersection"] > 0.5,
            {
                "province_cover_share_of_intersection": metrics[
                    "province_cover_share_of_intersection"
                ],
                "note": "coastal and offshore tile/ecodistrict area is outside simplified province polygons",
            },
        ),
    ]
    return {"checks": checks}


def build_overlay(tiles: Any, ecodistricts: Any) -> pd.DataFrame:
    """Intersect every tile with every candidate ecodistrict exactly.

    The STRtree only prunes candidates; the area is always the exact polygon
    intersection area.  No centroid, representative point, or areal interpolation
    is used.
    """
    from shapely import STRtree

    tree = STRtree(ecodistricts.geometry.to_numpy())
    tile_ids = tiles["TileID"].to_numpy()
    tile_geometries = tiles.geometry.to_numpy()
    ecodistrict_ids = ecodistricts["ecodistrict_id"].to_numpy()
    ecodistrict_geometries = ecodistricts.geometry.to_numpy()

    records: list[dict[str, Any]] = []
    zero_area_contacts = 0
    coerced_geometries = 0
    for tile_id, tile_geometry in zip(tile_ids, tile_geometries, strict=True):
        for position in tree.query(tile_geometry, predicate="intersects"):
            intersection = tile_geometry.intersection(ecodistrict_geometries[position])
            if intersection.is_empty:
                continue
            polygonal = _polygonal_part(intersection)
            if polygonal is None:
                zero_area_contacts += 1
                continue
            if polygonal.geom_type != intersection.geom_type:
                coerced_geometries += 1
            area_ha = float(polygonal.area) / 10_000.0
            if area_ha <= 0.0:
                zero_area_contacts += 1
                continue
            records.append(
                {
                    "tile_id": str(tile_id),
                    "ecodistrict_id": int(ecodistrict_ids[position]),
                    "intersection_area_ha": area_ha,
                    "intersection_geometry_type": polygonal.geom_type,
                }
            )

    if not records:
        raise TileProductionError("tile x ecodistrict overlay produced no intersections")

    overlay = pd.DataFrame.from_records(records)
    attributes = ecodistricts[
        [
            "ecodistrict_id",
            "ecodistrict",
            "ecozone",
            "ecoprovince",
            "ecoregion",
            "join_key",
        ]
    ].copy()
    attributes["ecodistrict_key"] = attributes["join_key"].astype(str)
    attributes["ecoregion_key"] = attributes["ecodistrict_key"].str.rsplit(
        "/", n=1
    ).str[0]
    attributes["ecoprovince_key"] = attributes["ecoregion_key"].str.rsplit(
        "/", n=1
    ).str[0]
    attributes["ecozone_key"] = attributes["ecozone"].astype(str)
    attributes = attributes.drop(columns=["join_key"])

    overlay = overlay.merge(
        attributes, on="ecodistrict_id", how="left", validate="many_to_one"
    )
    if overlay["ecodistrict_key"].isna().any():
        raise TileProductionError("overlay ecodistricts missing hierarchy attributes")

    ecodistrict_area = (
        ecodistricts.set_index("ecodistrict_id").geometry.area / 10_000.0
    )
    tile_area = tiles.set_index("TileID").geometry.area / 10_000.0
    overlay["ecodistrict_area_ha"] = overlay["ecodistrict_id"].map(ecodistrict_area)
    overlay["tile_area_ha"] = overlay["tile_id"].map(tile_area)
    if overlay[["ecodistrict_area_ha", "tile_area_ha"]].isna().any().any():
        raise TileProductionError("overlay failed to attach unit areas")

    overlay["fraction_of_tile"] = (
        overlay["intersection_area_ha"] / overlay["tile_area_ha"]
    )
    overlay["fraction_of_ecodistrict_raw"] = (
        overlay["intersection_area_ha"] / overlay["ecodistrict_area_ha"]
    )
    covered = overlay.groupby("ecodistrict_id")["intersection_area_ha"].transform(
        "sum"
    )
    overlay["ecodistrict_covered_area_ha"] = covered
    overlay["ecodistrict_covered_fraction"] = (
        covered / overlay["ecodistrict_area_ha"]
    )
    # Allocation fractions are normalised within the published tile footprint.
    # Ten edge ecodistricts extend beyond the 385-tile grid; normalising keeps
    # the SCANFI total complete and records the covered fraction explicitly.
    overlay["fraction_of_ecodistrict"] = (
        overlay["intersection_area_ha"] / covered
    )
    overlay["overlay_zero_area_contacts_skipped"] = zero_area_contacts
    overlay["overlay_non_polygonal_parts_coerced"] = coerced_geometries
    return overlay.sort_values(
        ["tile_id", "ecodistrict_id"], ignore_index=True
    )


def validate_overlay(
    overlay: pd.DataFrame, tiles: Any, ecodistricts: Any
) -> dict[str, Any]:
    """Prove the overlay closes geometrically and has no ambiguous assignment."""
    tile_union = tiles.geometry.union_all()
    ecodistrict_union = ecodistricts.geometry.union_all()
    pair_area = float(overlay["intersection_area_ha"].sum())
    union_intersection = float(tile_union.intersection(ecodistrict_union).area) / 10_000.0
    tile_only = float(tile_union.difference(ecodistrict_union).area) / 10_000.0
    ecodistrict_only = (
        float(ecodistrict_union.difference(tile_union).area) / 10_000.0
    )
    tile_area = float(tile_union.area) / 10_000.0
    ecodistrict_area = float(ecodistrict_union.area) / 10_000.0
    union_area = float(tile_union.union(ecodistrict_union).area) / 10_000.0

    tile_overlap = tile_area - float(tiles.geometry.area.sum()) / 10_000.0
    ecodistrict_overlap = (
        ecodistrict_area - float(ecodistricts.geometry.area.sum()) / 10_000.0
    )
    normalized_sums = overlay.groupby("ecodistrict_id")["fraction_of_ecodistrict"].sum()
    raw_sums = overlay.groupby("ecodistrict_id")["fraction_of_ecodistrict_raw"].sum()
    covered_by_ecodistrict = (
        overlay.groupby("ecodistrict_id")["intersection_area_ha"].sum()
        / overlay.groupby("ecodistrict_id")["ecodistrict_area_ha"].first()
    )
    tile_fraction_sums = overlay.groupby("tile_id")["fraction_of_tile"].sum()
    duplicate_pairs = int(overlay.duplicated(["tile_id", "ecodistrict_id"]).sum())
    min_area = float(overlay["intersection_area_ha"].min())
    geometry_types = sorted(overlay["intersection_geometry_type"].unique().tolist())

    normalized_deviation = float((normalized_sums - 1.0).abs().max())
    raw_deviation = float((raw_sums - covered_by_ecodistrict).abs().max())
    area_closure_error = abs(pair_area - union_intersection)
    tile_closure_error = abs(tile_area - (pair_area + tile_only))
    ecodistrict_closure_error = abs(ecodistrict_area - (pair_area + ecodistrict_only))
    union_closure_error = abs(union_area - (pair_area + tile_only + ecodistrict_only))

    checks = [
        _check(
            "tile_layer_has_no_overlaps",
            abs(tile_overlap) <= AREA_TOLERANCE_HA,
            {"sum_feature_area_minus_union_area_ha": tile_overlap},
        ),
        _check(
            "ecodistrict_layer_has_no_overlaps",
            abs(ecodistrict_overlap) <= AREA_TOLERANCE_HA,
            {"sum_feature_area_minus_union_area_ha": ecodistrict_overlap},
        ),
        _check(
            "pairwise_intersection_equals_union_intersection",
            area_closure_error <= AREA_TOLERANCE_HA,
            {
                "pair_area_ha": pair_area,
                "union_intersection_ha": union_intersection,
                "abs_error_ha": area_closure_error,
            },
        ),
        _check(
            "tile_area_closes_with_intersection_and_outside_part",
            tile_closure_error <= AREA_TOLERANCE_HA,
            {
                "tile_area_ha": tile_area,
                "intersection_plus_tile_only_ha": pair_area + tile_only,
                "abs_error_ha": tile_closure_error,
            },
        ),
        _check(
            "ecodistrict_area_closes_with_intersection_and_outside_part",
            ecodistrict_closure_error <= AREA_TOLERANCE_HA,
            {
                "ecodistrict_area_ha": ecodistrict_area,
                "intersection_plus_ecodistrict_only_ha": pair_area
                + ecodistrict_only,
                "abs_error_ha": ecodistrict_closure_error,
            },
        ),
        _check(
            "union_area_closes_with_three_components",
            union_closure_error <= AREA_TOLERANCE_HA,
            {
                "union_area_ha": union_area,
                "components_ha": pair_area + tile_only + ecodistrict_only,
                "abs_error_ha": union_closure_error,
            },
        ),
        _check(
            "ecodistrict_fractions_sum_to_one",
            normalized_deviation <= FRACTION_TOLERANCE,
            {"max_abs_deviation": normalized_deviation},
        ),
        _check(
            "raw_ecodistrict_fractions_sum_to_covered_fraction",
            raw_deviation <= FRACTION_TOLERANCE,
            {"max_abs_deviation": raw_deviation},
        ),
        _check(
            "tile_fractions_do_not_exceed_one",
            bool(tile_fraction_sums.le(1.0 + FRACTION_TOLERANCE).all()),
            {"max_tile_fraction_sum": float(tile_fraction_sums.max())},
        ),
        _check(
            "no_duplicate_tile_ecodistrict_pairs",
            duplicate_pairs == 0,
            {"duplicates": duplicate_pairs},
        ),
        _check(
            "all_intersection_areas_positive",
            min_area > 0.0,
            {"min_intersection_area_ha": min_area},
        ),
        _check(
            "all_intersection_geometries_polygonal",
            set(geometry_types).issubset({"Polygon", "MultiPolygon"}),
            {"geometry_types": geometry_types},
        ),
    ]
    return {
        "checks": checks,
        "metrics": {
            "tiles": int(tiles.shape[0]),
            "ecodistricts": int(ecodistricts.shape[0]),
            "intersection_pairs": int(overlay.shape[0]),
            "tiles_touched": int(overlay["tile_id"].nunique()),
            "ecodistricts_touched": int(overlay["ecodistrict_id"].nunique()),
            "total_intersection_area_ha": pair_area,
            "tile_area_ha": tile_area,
            "ecodistrict_area_ha": ecodistrict_area,
            "union_area_ha": union_area,
            "tile_only_area_ha": tile_only,
            "ecodistrict_only_area_ha": ecodistrict_only,
            "intersection_share_of_tile_area": pair_area / tile_area,
            "intersection_share_of_ecodistrict_area": pair_area / ecodistrict_area,
            "min_intersection_area_ha": min_area,
            "intersection_geometry_types": geometry_types,
            "zero_area_contacts_skipped": int(
                overlay["overlay_zero_area_contacts_skipped"].iloc[0]
            ),
            "non_polygonal_parts_coerced": int(
                overlay["overlay_non_polygonal_parts_coerced"].iloc[0]
            ),
            "ecodistricts_with_partial_tile_coverage": int(
                (covered_by_ecodistrict < 1.0 - PARTIAL_COVERAGE_TOLERANCE).sum()
            ),
            "ecodistrict_area_outside_tile_grid_ha": float(
                (
                    (1.0 - covered_by_ecodistrict)
                    * overlay.groupby("ecodistrict_id")["ecodistrict_area_ha"].first()
                ).sum()
            ),
        },
    }


def load_area_weights(path: Path) -> pd.DataFrame:
    """Load and parse the SCANFI ecodistrict x species area weights."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"SCANFI area weights not found: {path}")
    frame = pd.read_csv(path, low_memory=False)
    _require_columns(
        frame,
        {"ecodistrict_id", "province", "rsyc_species", "area_ha"},
        "SCANFI area weights",
    )
    frame["ecodistrict_id"] = frame["ecodistrict_id"].astype(int)
    frame["province"] = frame["province"].astype(str).str.strip().str.upper()
    unknown_provinces = sorted(set(frame["province"]) - set(PROVINCE_ABBREV.values()))
    if unknown_provinces:
        raise TileProductionError(
            f"SCANFI area weights contain unknown provinces: {unknown_provinces}"
        )
    frame["area_ha"] = pd.to_numeric(frame["area_ha"], errors="coerce")
    if frame["area_ha"].isna().any():
        raise TileProductionError("SCANFI area weights contain non-numeric area_ha")
    if not np.isfinite(frame["area_ha"].to_numpy(dtype=float)).all():
        raise TileProductionError("SCANFI area weights contain non-finite area_ha")
    if frame["area_ha"].lt(0.0).any():
        raise TileProductionError("SCANFI area weights contain negative area_ha")
    frame["target_species"] = frame["rsyc_species"].astype(str).str.strip().str.upper()
    unknown = sorted(set(frame["target_species"]) - set(SPECIES_GROUP))
    if unknown:
        raise TileProductionError(f"unknown target species labels: {unknown}")
    duplicate_keys = frame.duplicated(
        ["ecodistrict_id", "province", "target_species"]
    )
    if duplicate_keys.any():
        raise TileProductionError(
            "SCANFI area weights contain duplicate ecodistrict x province x species "
            f"rows: {int(duplicate_keys.sum())}"
        )
    return frame


def allocate_area_to_tiles(
    district_overlay: pd.DataFrame,
    province_overlay: pd.DataFrame,
    area_weights: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Allocate ecodistrict x province species area to tiles.

    Province-tagged SCANFI area is allocated with the exact tile share of the
    district x province intersection, normalised within that district x
    province.  Source rows whose province has no geographic intersection with
    any tile use the district-wide tile share as an explicit fallback and are
    reported separately.
    """
    positive = area_weights.loc[area_weights["area_ha"].gt(0.0)].copy()
    zero_rows = int(area_weights.shape[0] - positive.shape[0])
    zero_area = float(area_weights.loc[area_weights["area_ha"].le(0.0), "area_ha"].sum())

    district_coverage = district_overlay.groupby("ecodistrict_id", as_index=False).agg(
        covered_area_ha=("intersection_area_ha", "sum"),
        geometric_area_ha=("ecodistrict_area_ha", "first"),
    )
    district_coverage["district_covered_fraction"] = (
        district_coverage["covered_area_ha"] / district_coverage["geometric_area_ha"]
    )
    province_coverage = province_overlay.groupby(
        ["ecodistrict_id", "province"], as_index=False
    ).agg(province_covered_area_ha=("intersection_area_ha", "sum"))

    positive = positive.merge(
        district_coverage[
            [
                "ecodistrict_id",
                "geometric_area_ha",
                "district_covered_fraction",
            ]
        ],
        on="ecodistrict_id",
        how="left",
        validate="many_to_one",
    )
    if positive["district_covered_fraction"].isna().any():
        missing = sorted(
            positive.loc[
                positive["district_covered_fraction"].isna(), "ecodistrict_id"
            ].unique()
        )
        raise TileProductionError(
            f"SCANFI area weights map to ecodistricts absent from the tile overlay: {missing[:5]}"
        )
    positive = positive.merge(
        province_coverage,
        on=["ecodistrict_id", "province"],
        how="left",
        validate="many_to_one",
    )
    positive["province_covered_area_ha"] = positive[
        "province_covered_area_ha"
    ].fillna(0.0)
    positive["allocation_basis"] = np.where(
        positive["province_covered_area_ha"].gt(0.0),
        "province_intersection",
        "district_fallback_no_province_intersection",
    )
    fallback_rows = positive["allocation_basis"].eq(
        "district_fallback_no_province_intersection"
    )

    geographic = positive.loc[~fallback_rows].merge(
        province_overlay[
            [
                "tile_id",
                "ecodistrict_id",
                "province",
                "fraction_of_district_province",
            ]
        ],
        on=["ecodistrict_id", "province"],
        how="left",
        validate="many_to_many",
    )
    if geographic["tile_id"].isna().any():
        raise TileProductionError(
            "province-constrained allocation lost a tile intersection"
        )
    geographic["allocated_area_ha"] = (
        geographic["area_ha"] * geographic["fraction_of_district_province"]
    )

    fallback = positive.loc[fallback_rows].merge(
        district_overlay[
            ["tile_id", "ecodistrict_id", "fraction_of_ecodistrict"]
        ],
        on="ecodistrict_id",
        how="left",
        validate="many_to_many",
    )
    if fallback["tile_id"].isna().any():
        raise TileProductionError("district fallback allocation lost a tile intersection")
    fallback["allocated_area_ha"] = (
        fallback["area_ha"] * fallback["fraction_of_ecodistrict"]
    )

    detail_columns = [
        "tile_id",
        "ecodistrict_id",
        "province",
        "target_species",
        "area_ha",
        "allocated_area_ha",
        "allocation_basis",
    ]
    allocation_detail = pd.concat(
        [geographic[detail_columns], fallback[detail_columns]], ignore_index=True
    )
    allocation_detail["is_fallback"] = allocation_detail["allocation_basis"].eq(
        "district_fallback_no_province_intersection"
    )
    allocation_detail["geographic_allocated_area_ha"] = np.where(
        allocation_detail["is_fallback"], 0.0, allocation_detail["allocated_area_ha"]
    )
    allocation_detail["fallback_allocated_area_ha"] = np.where(
        allocation_detail["is_fallback"], allocation_detail["allocated_area_ha"], 0.0
    )

    grouped = allocation_detail.groupby(
        ["tile_id", "province", "target_species"], as_index=False
    ).agg(
        area_ha=("allocated_area_ha", "sum"),
        geographic_area_ha=("geographic_allocated_area_ha", "sum"),
        fallback_area_ha=("fallback_allocated_area_ha", "sum"),
    )
    tolerance = AREA_TOLERANCE_HA

    def allocation_basis(row: pd.Series) -> str:
        if row["fallback_area_ha"] > tolerance and row["geographic_area_ha"] > tolerance:
            return "mixed_province_intersection_and_district_fallback"
        if row["fallback_area_ha"] > tolerance:
            return "district_fallback_no_province_intersection"
        return "province_intersection"

    grouped["allocation_basis"] = grouped.apply(allocation_basis, axis=1)
    tile_species = grouped.loc[grouped["area_ha"].gt(0.0)].copy()
    tile_species["no_province_intersection_area_ha"] = tile_species["fallback_area_ha"]
    tile_species = tile_species.drop(
        columns=["geographic_area_ha", "fallback_area_ha"]
    )

    source_total = float(area_weights["area_ha"].sum())
    allocated_total = float(tile_species["area_ha"].sum())
    difference = allocated_total - source_total
    if abs(difference) > AREA_TOLERANCE_HA:
        raise TileProductionError(
            "tile area allocation does not reconcile to SCANFI: "
            f"difference {difference} ha"
        )

    source_groups = positive.groupby(
        ["ecodistrict_id", "province", "target_species"], as_index=False
    ).agg(source_area_ha=("area_ha", "sum"))
    allocated_groups = allocation_detail.groupby(
        ["ecodistrict_id", "province", "target_species"], as_index=False
    ).agg(allocated_area_ha=("allocated_area_ha", "sum"))
    closure = source_groups.merge(
        allocated_groups,
        on=["ecodistrict_id", "province", "target_species"],
        how="outer",
        validate="one_to_one",
    )
    closure[["source_area_ha", "allocated_area_ha"]] = closure[
        ["source_area_ha", "allocated_area_ha"]
    ].fillna(0.0)
    closure["difference_ha"] = closure["allocated_area_ha"] - closure["source_area_ha"]
    closure_max_error = float(closure["difference_ha"].abs().max())
    closure_failures = int(
        closure["difference_ha"].abs().gt(AREA_TOLERANCE_HA).sum()
    )
    if closure_failures:
        raise TileProductionError(
            "district x province x species allocation does not close: "
            f"{closure_failures} groups, max error {closure_max_error} ha"
        )

    province_source = positive.groupby("province", as_index=False).agg(
        source_area_ha=("area_ha", "sum")
    )
    province_allocated = allocation_detail.groupby("province", as_index=False).agg(
        allocated_area_ha=("allocated_area_ha", "sum")
    )
    province_closure = province_source.merge(
        province_allocated, on="province", how="outer", validate="one_to_one"
    ).fillna(0.0)
    province_closure["difference_ha"] = (
        province_closure["allocated_area_ha"] - province_closure["source_area_ha"]
    )
    province_max_error = float(province_closure["difference_ha"].abs().max())

    ecodistrict_area = district_overlay.groupby("ecodistrict_id", as_index=False).agg(
        geometric_area_ha=("ecodistrict_area_ha", "first")
    )
    scanned_area = positive.groupby("ecodistrict_id", as_index=False).agg(
        scanfi_area_ha=("area_ha", "sum")
    )
    density = ecodistrict_area.merge(
        scanned_area, on="ecodistrict_id", validate="one_to_one"
    )
    density["scanfi_to_geometry_ratio"] = (
        density["scanfi_area_ha"] / density["geometric_area_ha"]
    )
    over_dense = density.loc[
        density["scanfi_to_geometry_ratio"].gt(1.0 + PARTIAL_COVERAGE_TOLERANCE)
    ]

    fallback_area = float(fallback["allocated_area_ha"].sum())
    fallback_groups = positive.loc[fallback_rows].groupby(
        ["ecodistrict_id", "province", "target_species"], as_index=False
    ).agg(area_ha=("area_ha", "sum"))
    if not fallback_groups.empty:
        attributes = district_overlay[
            ["ecodistrict_id", "ecodistrict", "ecozone", "ecoprovince", "ecoregion"]
        ].drop_duplicates("ecodistrict_id")
        fallback_groups = fallback_groups.merge(
            attributes, on="ecodistrict_id", how="left", validate="many_to_one"
        )
        fallback_groups["reason"] = (
            "province has no geographic intersection with any tile in this district; "
            "district-wide tile share used"
        )
    fallback_table = fallback_groups.to_dict("records")

    redistributed_area = float(
        (
            positive["area_ha"]
            * (1.0 - positive["district_covered_fraction"])
        ).sum()
    )
    legacy = positive.merge(
        district_overlay[
            ["tile_id", "ecodistrict_id", "fraction_of_ecodistrict"]
        ],
        on="ecodistrict_id",
        how="left",
        validate="many_to_many",
    )
    legacy["allocated_area_ha"] = (
        legacy["area_ha"] * legacy["fraction_of_ecodistrict"]
    )
    legacy_tile_province = legacy.groupby(
        ["tile_id", "province"], as_index=False
    ).agg(legacy_area_ha=("allocated_area_ha", "sum"))
    constrained_tile_province = tile_species.groupby(
        ["tile_id", "province"], as_index=False
    ).agg(constrained_area_ha=("area_ha", "sum"))
    attribution_change = legacy_tile_province.merge(
        constrained_tile_province,
        on=["tile_id", "province"],
        how="outer",
        validate="one_to_one",
    ).fillna(0.0)
    attribution_change["difference_ha"] = (
        attribution_change["constrained_area_ha"]
        - attribution_change["legacy_area_ha"]
    )
    changed = attribution_change.loc[
        attribution_change["difference_ha"].abs().gt(AREA_TOLERANCE_HA)
    ]
    top_changes = changed.reindex(
        changed["difference_ha"].abs().sort_values(ascending=False).index
    ).head(10)
    b25_legacy = attribution_change.loc[attribution_change["tile_id"].eq("B25")]

    diagnostics = {
        "source_rows": int(area_weights.shape[0]),
        "positive_source_rows": int(positive.shape[0]),
        "zero_area_rows_excluded": zero_rows,
        "zero_area_ha_excluded": zero_area,
        "source_total_ha": source_total,
        "allocated_total_ha": allocated_total,
        "difference_ha": difference,
        "redistributed_area_ha": redistributed_area,
        "redistributed_area_share_pct": redistributed_area / source_total * 100.0,
        "geographic_allocation_area_ha": float(
            allocation_detail.loc[
                ~allocation_detail["is_fallback"], "allocated_area_ha"
            ].sum()
        ),
        "fallback_area_ha": fallback_area,
        "fallback_area_share_pct": fallback_area / source_total * 100.0,
        "fallback_source_rows": int(fallback_rows.sum()),
        "fallback_source_groups": int(fallback_groups.shape[0]),
        "fallback_table": fallback_table,
        "district_province_species_groups": int(closure.shape[0]),
        "district_province_species_closure_max_abs_error_ha": closure_max_error,
        "district_province_species_closure_failures": closure_failures,
        "province_reconciliation_max_abs_error_ha": province_max_error,
        "source_province_area_ha": {
            str(row.province): float(row.source_area_ha)
            for row in province_closure.itertuples(index=False)
        },
        "allocated_province_area_ha": {
            str(row.province): float(row.allocated_area_ha)
            for row in province_closure.itertuples(index=False)
        },
        "source_species_area_ha": {
            str(row.target_species): float(row.source_area_ha)
            for row in positive.groupby("target_species", as_index=False)
            .agg(source_area_ha=("area_ha", "sum"))
            .itertuples(index=False)
        },
        "allocated_species_area_ha": {
            str(row.target_species): float(row.allocated_area_ha)
            for row in allocation_detail.groupby("target_species", as_index=False)
            .agg(allocated_area_ha=("allocated_area_ha", "sum"))
            .itertuples(index=False)
        },
        "ecodistricts_with_scanfi_area_above_polygon": int(over_dense.shape[0]),
        "max_ecodistrict_scanfi_to_geometry_ratio": float(
            density["scanfi_to_geometry_ratio"].max()
        ),
        "tile_species_cells": int(tile_species.shape[0]),
        "tiles_with_area": int(tile_species["tile_id"].nunique()),
        "province_attribution_change": {
            "legacy_total_ha": float(attribution_change["legacy_area_ha"].sum()),
            "constrained_total_ha": float(
                attribution_change["constrained_area_ha"].sum()
            ),
            "total_difference_ha": float(
                attribution_change["difference_ha"].sum()
            ),
            "tile_province_cells_changed": int(changed.shape[0]),
            "max_abs_tile_province_change_ha": float(
                changed["difference_ha"].abs().max()
            )
            if not changed.empty
            else 0.0,
            "top_tile_province_changes": top_changes.to_dict("records"),
            "b25": b25_legacy.to_dict("records"),
        },
    }
    return tile_species, allocation_detail, diagnostics


def build_model_index(models: pd.DataFrame) -> dict[tuple[str, str, str], dict[str, Any]]:
    """Index national volume curves by (level, canonical strata_id, species)."""
    required = {
        "model_id",
        "strata_level",
        "strata_id",
        "species",
        "response",
        "b1",
        "b2",
        "b3",
        "b4",
    }
    _require_columns(models, required, "RSYC model catalogue")
    frame = models.copy()
    frame["strata_id"] = frame["strata_id"].astype(str).map(canonical_strata_id)
    frame["species"] = frame["species"].astype(str)
    for column in ("b1", "b2", "b3", "b4"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if frame[["b1", "b2", "b3", "b4"]].isna().any().any():
        raise TileProductionError("RSYC model catalogue contains non-numeric coefficients")
    coefficients = frame[["b1", "b2", "b3", "b4"]].to_numpy(dtype=float)
    if not np.isfinite(coefficients).all():
        raise TileProductionError("RSYC model catalogue contains non-finite coefficients")
    if (frame[["b1", "b2", "b3"]].to_numpy(dtype=float) <= 0.0).any():
        raise TileProductionError("RSYC model catalogue contains non-positive b1/b2/b3")
    duplicate_keys = frame.duplicated(["strata_level", "strata_id", "species"])
    if duplicate_keys.any():
        raise TileProductionError(
            f"RSYC model keys are not unique: {int(duplicate_keys.sum())} duplicates"
        )
    index: dict[tuple[str, str, str], dict[str, Any]] = {}
    for record in frame.to_dict("records"):
        index[
            (
                str(record["strata_level"]),
                str(record["strata_id"]),
                str(record["species"]),
            )
        ] = record
    return index


def load_volume_models(path: Path) -> pd.DataFrame:
    """Load published national volume curves and require convergence."""
    models = load_rsyc_volume_models(path)
    if "converged" in models.columns:
        converged = models["converged"].astype(str).str.upper().isin({"TRUE", "1", "T"})
        models = models.loc[converged].copy()
    if models.empty:
        raise TileProductionError("no published converged national volume curves")
    return models.reset_index(drop=True)


def validate_ecological_model_keys(
    ecodistricts: Any, model_index: Mapping[tuple[str, str, str], Mapping[str, Any]]
) -> dict[str, int]:
    """Confirm every used ecological model stratum exists in the boundaries."""
    keys = {
        "ecodistrict": set(ecodistricts["join_key"].astype(str)),
        "ecoregion": set(
            ecodistricts["join_key"].astype(str).str.rsplit("/", n=1).str[0]
        ),
        "ecoprovince": set(
            ecodistricts["join_key"]
            .astype(str)
            .str.rsplit("/", n=2)
            .str[0]
        ),
        "ecozone": set(ecodistricts["ecozone"].astype(str)),
    }
    counts: dict[str, int] = {}
    for level, expected in keys.items():
        catalogue = {
            strata_id
            for (model_level, strata_id, _species) in model_index
            if model_level == level
        }
        missing = sorted(catalogue - expected)
        if missing:
            raise TileProductionError(
                f"{level} model strata absent from boundaries: {missing[:3]}"
            )
        counts[level] = len(catalogue)
    return counts


def dominant_ecology(overlay: pd.DataFrame) -> dict[str, dict[str, str]]:
    """Return each tile's dominant ecoregion and ecoprovince by intersection area."""
    result: dict[str, dict[str, str]] = {}
    for column in ("ecoregion_key", "ecoprovince_key", "ecozone_key"):
        grouped = (
            overlay.groupby(["tile_id", column], as_index=False)["intersection_area_ha"]
            .sum()
            .sort_values(
                ["tile_id", "intersection_area_ha", column],
                ascending=[True, False, True],
            )
            .drop_duplicates("tile_id")
        )
        for record in grouped.to_dict("records"):
            result.setdefault(str(record["tile_id"]), {})[column] = str(
                record[column]
            )
    return result


def _tile_model(
    model_index: Mapping[tuple[str, str, str], Mapping[str, Any]],
    tile_id: str,
    species: str,
) -> Mapping[str, Any] | None:
    return model_index.get(("tile", tile_id, species))


def _ecological_model(
    model_index: Mapping[tuple[str, str, str], Mapping[str, Any]],
    level: str,
    strata_id: str | None,
    species: str,
) -> Mapping[str, Any] | None:
    if not strata_id:
        return None
    return model_index.get((level, strata_id, species))


def select_tile_curve(
    *,
    tile_id: str,
    target_species: str,
    model_index: Mapping[tuple[str, str, str], Mapping[str, Any]],
    dominant: Mapping[str, str],
    tile_has_any_curve: bool,
) -> dict[str, Any]:
    """Select the tile curve ladder for one tile x species cell.

    Tile exact species, tile species group, and tile generic are tried first.
    Only when the tile has no usable curve at all does the selector borrow the
    dominant ecoregion/ecoprovince control curve.
    """
    species = str(target_species).strip().upper()
    if species not in SPECIES_GROUP:
        raise TileProductionError(f"unknown target species: {target_species!r}")
    is_group_target = species in GROUP_TARGETS
    group = SPECIES_GROUP[species]

    if tile_has_any_curve:
        if not is_group_target:
            exact = _tile_model(model_index, tile_id, species)
            if exact is not None:
                return {
                    "model": exact,
                    "evidence_tier": "rsyc_tile_exact",
                    "selected_level": "tile",
                    "source_species": species,
                    "fallback_reason": None,
                }
        group_curve = _tile_model(model_index, tile_id, group)
        if group_curve is not None:
            return {
                "model": group_curve,
                "evidence_tier": "rsyc_tile_group",
                "selected_level": "tile",
                "source_species": group,
                "fallback_reason": None,
            }
        generic = _tile_model(model_index, tile_id, "generic")
        if generic is not None:
            return {
                "model": generic,
                "evidence_tier": "rsyc_tile_generic",
                "selected_level": "tile",
                "source_species": "generic",
                "fallback_reason": None,
            }

    labels = [species] if not is_group_target else []
    labels.extend([group] if group != "generic" else [])
    labels.append("generic")
    for level, key_column in (
        ("ecoregion", "ecoregion_key"),
        ("ecoprovince", "ecoprovince_key"),
    ):
        strata_id = dominant.get(key_column)
        for label in labels:
            model = _ecological_model(model_index, level, strata_id, label)
            if model is not None:
                label_kind = "exact species" if label == species else label
                reason = (
                    "no tile curve for this cell; dominant "
                    f"{level} control borrowed ({label_kind})"
                )
                return {
                    "model": model,
                    "evidence_tier": "rsyc_tile_control_fallback",
                    "selected_level": level,
                    "source_species": label,
                    "fallback_reason": reason,
                }
    raise TileProductionError(
        f"no tile, ecoregion, or ecoprovince curve for {tile_id} x {species}"
    )


def build_library(
    tile_species: pd.DataFrame,
    model_index: Mapping[tuple[str, str, str], Mapping[str, Any]],
    dominant: Mapping[str, dict[str, str]],
) -> pd.DataFrame:
    """Attach one selected curve to every positive tile x province x species cell."""
    tile_curve_ids = {
        tile_id for (level, tile_id, _species) in model_index if level == "tile"
    }
    records: list[dict[str, Any]] = []
    for row in tile_species.itertuples(index=False):
        tile_id = str(row.tile_id)
        species = str(row.target_species)
        selection = select_tile_curve(
            tile_id=tile_id,
            target_species=species,
            model_index=model_index,
            dominant=dominant.get(tile_id, {}),
            tile_has_any_curve=tile_id in tile_curve_ids,
        )
        model = selection["model"]
        record = {
            "cell_key": f"tile:{tile_id}|{row.province}|{species}",
            "tile_id": tile_id,
            "province": str(row.province),
            "species": species,
            "species_kind": "group" if species in GROUP_TARGETS else "species",
            "area_ha": float(row.area_ha),
            "allocation_basis": require_known_allocation_basis(
                str(row.allocation_basis), source="tile production allocation"
            ),
            "no_province_intersection_area_ha": float(
                row.no_province_intersection_area_ha
            ),
            "model_id": str(model["model_id"]),
            "evidence_tier": selection["evidence_tier"],
            "selected_level": selection["selected_level"],
            "source_species": selection["source_species"],
            "fallback_reason": selection["fallback_reason"],
            "response": str(model["response"]),
            "response_units": str(model["response_units"]),
            "model_group": str(model.get("model_group", "")),
            "strata_id": str(model["strata_id"]),
            "b1": float(model["b1"]),
            "b2": float(model["b2"]),
            "b3": float(model["b3"]),
            "b4": float(model["b4"]),
            "n_obs": float(model.get("n_obs", math.nan)),
            "n_units": float(model.get("n_units", math.nan)),
            "age_min": float(model.get("age_min", 1.0)),
            "age_max": float(model.get("age_max", 600.0)),
        }
        for age in AGE_GRID:
            record[f"volume_m3ha_age{int(age)}"] = evaluate_volume_curve(
                b1=record["b1"],
                b2=record["b2"],
                b3=record["b3"],
                b4=record["b4"],
                age=age,
                model_id=record["model_id"],
            )
        record["coefficient_sign_flag"] = (
            "negative_b4_published_fit" if record["b4"] < 0.0 else "ok"
        )
        record["status"] = "ok"
        records.append(record)

    library = pd.DataFrame.from_records(records)
    if library.empty:
        raise TileProductionError("tile production library is empty")
    duplicate_cells = int(library.duplicated("cell_key").sum())
    if duplicate_cells:
        raise TileProductionError(f"duplicate tile production cells: {duplicate_cells}")
    return library.sort_values(
        ["tile_id", "province", "species"], ignore_index=True
    )


def validate_library(
    library: pd.DataFrame,
    tile_species: pd.DataFrame,
    area_diagnostics: Mapping[str, Any],
    overlay_validation: Mapping[str, Any],
) -> dict[str, Any]:
    """Run coverage, key, finiteness, coefficient, and reconciliation checks."""
    required = {
        "cell_key",
        "tile_id",
        "province",
        "species",
        "species_kind",
        "area_ha",
        "allocation_basis",
        "no_province_intersection_area_ha",
        "model_id",
        "evidence_tier",
        "b1",
        "b2",
        "b3",
        "b4",
        "coefficient_sign_flag",
        *[f"volume_m3ha_age{int(age)}" for age in AGE_GRID],
    }
    checks = list(overlay_validation["checks"])
    _require_columns(library, required, "tile production library")
    library_area = float(library["area_ha"].sum())
    target_area = float(tile_species["area_ha"].sum())
    source_area = float(area_diagnostics["source_total_ha"])
    area_difference = abs(library_area - source_area)
    missing_curves = int(library["model_id"].isna().sum())
    prediction_columns = [f"volume_m3ha_age{int(age)}" for age in AGE_GRID]
    numeric_columns = ["area_ha", "b1", "b2", "b3", "b4", *prediction_columns]
    numeric = library[numeric_columns].to_numpy(dtype=float)
    non_finite_values = int((~np.isfinite(numeric)).sum())
    nan_columns = [
        column for column in numeric_columns if library[column].isna().any()
    ]
    non_positive_area = int(library["area_ha"].le(0.0).sum())
    non_positive_shape = int(
        (library[["b1", "b2", "b3"]].to_numpy(dtype=float) <= 0.0).sum()
    )
    prediction_values = library[prediction_columns].to_numpy(dtype=float)
    invalid_predictions = int((~np.isfinite(prediction_values)).sum()) + int(
        (prediction_values < 0.0).sum()
    )
    negative_b4 = int(library["b4"].lt(0.0).sum())
    flagged_b4 = int(
        library["coefficient_sign_flag"].eq("negative_b4_published_fit").sum()
    )
    duplicate_cells = int(library.duplicated("cell_key").sum())
    duplicate_logical = int(
        library.duplicated(["tile_id", "province", "species"]).sum()
    )
    expected_keys = (
        "tile:"
        + library["tile_id"].astype(str)
        + "|"
        + library["province"].astype(str)
        + "|"
        + library["species"].astype(str)
    )
    key_mismatches = int((library["cell_key"].astype(str) != expected_keys).sum())
    blank_keys = int(
        (
            library[["cell_key", "tile_id", "province", "species"]]
            .astype(str)
            .apply(lambda column: column.str.strip().eq(""))
            .any(axis=1)
        ).sum()
    )
    unknown_tiers = sorted(set(library["evidence_tier"].astype(str)) - set(EVIDENCE_TIERS))
    unknown_bases = sorted(
        set(library["allocation_basis"].astype(str)) - set(ALLOCATION_BASES)
    )
    library_province = library.groupby("province")["area_ha"].sum().to_dict()
    source_province = area_diagnostics["source_province_area_ha"]
    province_errors = {
        province: abs(float(library_province.get(province, 0.0)) - float(source_province[province]))
        for province in source_province
    }
    extra_provinces = sorted(set(library_province) - set(source_province))
    library_species = library.groupby("species")["area_ha"].sum().to_dict()
    source_species = area_diagnostics["source_species_area_ha"]
    species_errors = {
        species: abs(float(library_species.get(species, 0.0)) - float(source_species[species]))
        for species in source_species
    }
    fallback_area = float(library["no_province_intersection_area_ha"].sum())
    fallback_reported = bool(
        math.isclose(
            fallback_area,
            float(area_diagnostics["fallback_area_ha"]),
            rel_tol=0.0,
            abs_tol=AREA_TOLERANCE_HA,
        )
        and (
            fallback_area <= AREA_TOLERANCE_HA
            or bool(area_diagnostics["fallback_table"])
        )
    )
    checks.extend(
        [
            _check(
                "every_positive_tile_species_cell_has_a_curve",
                missing_curves == 0,
                {"cells": int(library.shape[0]), "missing": missing_curves},
            ),
            _check(
                "library_area_equals_tile_species_area",
                abs(library_area - target_area) <= AREA_TOLERANCE_HA,
                {
                    "library_area_ha": library_area,
                    "tile_species_area_ha": target_area,
                    "abs_difference_ha": abs(library_area - target_area),
                },
            ),
            _check(
                "library_area_reconciles_to_scanfi",
                area_difference <= AREA_TOLERANCE_HA,
                {
                    "library_area_ha": library_area,
                    "scanfi_area_ha": source_area,
                    "abs_difference_ha": area_difference,
                },
            ),
            _check(
                "source_keys_are_complete_and_canonical",
                blank_keys == 0 and key_mismatches == 0,
                {"blank_keys": blank_keys, "cell_key_mismatches": key_mismatches},
            ),
            _check(
                "no_duplicate_library_cells",
                duplicate_cells == 0 and duplicate_logical == 0,
                {
                    "duplicate_cell_keys": duplicate_cells,
                    "duplicate_tile_province_species": duplicate_logical,
                },
            ),
            _check(
                "all_numeric_values_finite",
                non_finite_values == 0 and not nan_columns,
                {
                    "non_finite_values": non_finite_values,
                    "columns_with_nan": nan_columns,
                },
            ),
            _check(
                "all_areas_strictly_positive",
                non_positive_area == 0,
                {"non_positive_cells": non_positive_area},
            ),
            _check(
                "b1_b2_b3_strictly_positive_b4_finite",
                non_positive_shape == 0
                and bool(np.isfinite(library["b4"].to_numpy(dtype=float)).all()),
                {"non_positive_coefficients": non_positive_shape},
            ),
            _check(
                "predictions_finite_and_non_negative",
                invalid_predictions == 0,
                {"ages": list(AGE_GRID), "invalid_predictions": invalid_predictions},
            ),
            _check(
                "negative_b4_is_flagged_not_dropped",
                negative_b4 == flagged_b4,
                {"negative_b4": negative_b4, "flagged": flagged_b4},
            ),
            _check(
                "province_area_sums_reconcile_to_source",
                not extra_provinces
                and max(province_errors.values(), default=0.0) <= AREA_TOLERANCE_HA,
                {
                    "max_abs_difference_ha": max(province_errors.values(), default=0.0),
                    "extra_provinces": extra_provinces,
                },
            ),
            _check(
                "species_area_sums_reconcile_to_source",
                max(species_errors.values(), default=0.0) <= AREA_TOLERANCE_HA,
                {
                    "max_abs_difference_ha": max(species_errors.values(), default=0.0),
                    "species": len(species_errors),
                },
            ),
            _check(
                "district_province_species_allocation_closes",
                int(area_diagnostics["district_province_species_closure_failures"]) == 0
                and float(
                    area_diagnostics[
                        "district_province_species_closure_max_abs_error_ha"
                    ]
                )
                <= AREA_TOLERANCE_HA,
                {
                    "groups": int(area_diagnostics["district_province_species_groups"]),
                    "max_abs_error_ha": float(
                        area_diagnostics[
                            "district_province_species_closure_max_abs_error_ha"
                        ]
                    ),
                },
            ),
            _check(
                "no_province_intersection_area_is_reported",
                fallback_reported,
                {
                    "fallback_area_ha": fallback_area,
                    "reported_fallback_area_ha": float(
                        area_diagnostics["fallback_area_ha"]
                    ),
                    "fallback_groups": int(area_diagnostics["fallback_source_groups"]),
                },
            ),
            _check(
                "evidence_tiers_are_known",
                not unknown_tiers,
                {"tiers": sorted(library["evidence_tier"].unique().tolist())},
            ),
            _check(
                "allocation_bases_are_known",
                not unknown_bases,
                {
                    "bases": sorted(library["allocation_basis"].unique().tolist()),
                },
            ),
        ]
    )
    return {
        "passed": all(check["passed"] for check in checks),
        "checks": checks,
    }


def _tier_table(library: pd.DataFrame) -> list[dict[str, Any]]:
    total_area = float(library["area_ha"].sum())
    grouped = (
        library.groupby("evidence_tier", as_index=False)
        .agg(cells=("cell_key", "size"), area_ha=("area_ha", "sum"))
        .sort_values("evidence_tier")
    )
    grouped["area_share_pct"] = grouped["area_ha"] / total_area * 100.0
    return grouped.to_dict("records")


def _allocation_basis_table(library: pd.DataFrame) -> list[dict[str, Any]]:
    total_area = float(library["area_ha"].sum())
    grouped = (
        library.groupby("allocation_basis", as_index=False)
        .agg(cells=("cell_key", "size"), area_ha=("area_ha", "sum"))
        .sort_values("allocation_basis")
    )
    grouped["area_share_pct"] = grouped["area_ha"] / total_area * 100.0
    return grouped.to_dict("records")


def _selected_level_table(library: pd.DataFrame) -> list[dict[str, Any]]:
    grouped = (
        library.groupby("selected_level", as_index=False)
        .agg(cells=("cell_key", "size"), area_ha=("area_ha", "sum"))
        .sort_values("area_ha", ascending=False)
    )
    return grouped.to_dict("records")


def _species_table(library: pd.DataFrame) -> list[dict[str, Any]]:
    grouped = library.groupby("species", as_index=False).agg(
        cells=("cell_key", "size"),
        area_ha=("area_ha", "sum"),
        exact_area_ha=(
            "area_ha",
            lambda values: float(
                values[library.loc[values.index, "evidence_tier"].eq(
                    "rsyc_tile_exact"
                )].sum()
            ),
        ),
        group_area_ha=(
            "area_ha",
            lambda values: float(
                values[library.loc[values.index, "evidence_tier"].eq(
                    "rsyc_tile_group"
                )].sum()
            ),
        ),
        generic_area_ha=(
            "area_ha",
            lambda values: float(
                values[library.loc[values.index, "evidence_tier"].eq(
                    "rsyc_tile_generic"
                )].sum()
            ),
        ),
        fallback_area_ha=(
            "area_ha",
            lambda values: float(
                values[library.loc[values.index, "evidence_tier"].eq(
                    "rsyc_tile_control_fallback"
                )].sum()
            ),
        ),
    )
    total = grouped["area_ha"].replace(0.0, np.nan)
    for tier in ("exact", "group", "generic", "fallback"):
        grouped[f"{tier}_pct"] = grouped[f"{tier}_area_ha"] / total * 100.0
    grouped = grouped.fillna(0.0)
    return grouped.sort_values("area_ha", ascending=False).to_dict("records")


def _province_table(library: pd.DataFrame) -> list[dict[str, Any]]:
    total_area = float(library["area_ha"].sum())
    grouped = library.groupby("province", as_index=False).agg(
        cells=("cell_key", "size"),
        area_ha=("area_ha", "sum"),
        exact_area_ha=(
            "area_ha",
            lambda values: float(
                values[library.loc[values.index, "evidence_tier"].eq(
                    "rsyc_tile_exact"
                )].sum()
            ),
        ),
        group_area_ha=(
            "area_ha",
            lambda values: float(
                values[library.loc[values.index, "evidence_tier"].eq(
                    "rsyc_tile_group"
                )].sum()
            ),
        ),
        generic_area_ha=(
            "area_ha",
            lambda values: float(
                values[library.loc[values.index, "evidence_tier"].eq(
                    "rsyc_tile_generic"
                )].sum()
            ),
        ),
        fallback_area_ha=(
            "area_ha",
            lambda values: float(
                values[library.loc[values.index, "evidence_tier"].eq(
                    "rsyc_tile_control_fallback"
                )].sum()
            ),
        ),
    )
    grouped["area_share_pct"] = grouped["area_ha"] / total_area * 100.0
    for tier in ("exact", "group", "generic", "fallback"):
        grouped[f"{tier}_pct"] = grouped[f"{tier}_area_ha"] / grouped["area_ha"] * 100.0
    return grouped.sort_values("area_ha", ascending=False).to_dict("records")


def _partial_coverage_table(overlay: pd.DataFrame) -> list[dict[str, Any]]:
    coverage = (
        overlay.groupby("ecodistrict_id", as_index=False)
        .agg(
            ecodistrict=("ecodistrict", "first"),
            ecodistrict_area_ha=("ecodistrict_area_ha", "first"),
            covered_area_ha=("intersection_area_ha", "sum"),
            covered_fraction=("ecodistrict_covered_fraction", "first"),
        )
        .loc[
            lambda frame: frame["covered_fraction"]
            < 1.0 - PARTIAL_COVERAGE_TOLERANCE
        ]
        .sort_values("covered_fraction")
    )
    return coverage.to_dict("records")


def summarise(
    *,
    overlay: pd.DataFrame,
    overlay_validation: Mapping[str, Any],
    area_diagnostics: Mapping[str, Any],
    library: pd.DataFrame,
    validation: Mapping[str, Any],
    model_counts: Mapping[str, int],
) -> dict[str, Any]:
    """Build the machine-readable summary."""
    total_area = float(library["area_ha"].sum())
    tile_capacity = overlay.groupby("tile_id")["tile_area_ha"].first()
    allocated_by_tile = library.groupby("tile_id")["area_ha"].sum()
    capacity_ratio = allocated_by_tile / tile_capacity.reindex(allocated_by_tile.index)
    over_capacity = capacity_ratio.loc[
        capacity_ratio.gt(1.0 + PARTIAL_COVERAGE_TOLERANCE)
    ]
    capacity_detail = pd.DataFrame(
        {
            "tile_id": allocated_by_tile.index.astype(str),
            "allocated_area_ha": allocated_by_tile.to_numpy(dtype=float),
            "tile_polygon_area_ha": tile_capacity.reindex(
                allocated_by_tile.index
            ).to_numpy(dtype=float),
        }
    )
    capacity_detail["allocated_to_polygon_area_ratio"] = (
        capacity_detail["allocated_area_ha"]
        / capacity_detail["tile_polygon_area_ha"]
    )
    capacity_detail["excess_area_ha"] = (
        capacity_detail["allocated_area_ha"]
        - capacity_detail["tile_polygon_area_ha"]
    ).clip(lower=0.0)
    capacity_detail["over_capacity"] = capacity_detail[
        "allocated_to_polygon_area_ratio"
    ].gt(1.0 + PARTIAL_COVERAGE_TOLERANCE)
    over_capacity_rows = capacity_detail.loc[capacity_detail["over_capacity"]].sort_values(
        "allocated_to_polygon_area_ratio", ascending=False
    )
    b25 = capacity_detail.loc[capacity_detail["tile_id"].eq("B25")].to_dict("records")
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "committed": False,
        "status": "diagnostic_tile_production_library",
        "inputs": {
            "tiles": "data/raw/national/rsyc/RSYC_tiles.gpkg",
            "ecodistricts": "data/raw/national/rsyc/RSYC_ecostrat.gpkg",
            "models": "data/raw/national/rsyc/RSYC_models.zip",
            "provinces": (
                "data/raw/boundaries/canada_adm1_geoboundaries_simplified.geojson"
            ),
            "area_weights": (
                "data/derived/national/assortments/national_area_weights/"
                "national_area_weights_ecodistrict.csv"
            ),
        },
        "method": {
            "overlay": "exact_polygon_intersection_epsg3978",
            "province_overlay": (
                "tile x ecodistrict x province exact intersection; fractions "
                "normalised within district x province"
            ),
            "area_allocation": (
                "province_constrained_ecodistrict_fraction_normalised_within_"
                "published_tile_footprint; explicit district fallback when a "
                "source province has no geographic intersection"
            ),
            "curve_selection": [
                "tile exact species",
                "tile species group",
                "tile generic",
                "dominant ecoregion/ecoprovince control fallback",
            ],
            "response": "volume",
            "age_grid": list(AGE_GRID),
            "operational_age_range": [1, 150],
            "ecodistrict_composition_role": "diagnostic_only",
            "benchmark_correction_applied": False,
        },
        "overlay": overlay_validation["metrics"],
        "area": dict(area_diagnostics),
        "area_geometry_consistency": {
            "tiles_with_allocated_area_above_polygon": int(over_capacity.shape[0]),
            "tiles_above_polygon": over_capacity_rows.to_dict("records"),
            "max_allocated_to_tile_area_ratio": float(capacity_ratio.max()),
            "max_ratio_tile": str(capacity_ratio.idxmax()),
            "excess_area_above_tile_polygon_ha": float(
                capacity_detail["excess_area_ha"].sum()
            ),
            "b25": b25,
            "note": (
                "Inherited from SCANFI ecodistrict forest area exceeding the "
                "ecodistrict polygon area in over-dense ecodistricts; the "
                "geometric overlay is exact and is not the source of the excess. "
                "Excess area is flagged, not clipped."
            ),
        },
        "library": {
            "cells": int(library.shape[0]),
            "tiles": int(library["tile_id"].nunique()),
            "species": int(library["species"].nunique()),
            "provinces": int(library["province"].nunique()),
            "total_area_ha": total_area,
            "tier_table": _tier_table(library),
            "allocation_basis_table": _allocation_basis_table(library),
            "selected_level_table": _selected_level_table(library),
            "species_table": _species_table(library),
            "province_table": _province_table(library),
            "negative_b4_cells": int(library["b4"].lt(0.0).sum()),
            "fallback_cells": int(
                library["evidence_tier"].eq("rsyc_tile_control_fallback").sum()
            ),
            "no_province_intersection": {
                "area_ha": float(library["no_province_intersection_area_ha"].sum()),
                "area_share_pct": (
                    float(library["no_province_intersection_area_ha"].sum())
                    / total_area
                    * 100.0
                ),
                "source_rows": int(area_diagnostics["fallback_source_rows"]),
                "source_groups": int(area_diagnostics["fallback_source_groups"]),
                "groups": area_diagnostics["fallback_table"],
                "note": (
                    "SCANFI blocks assigned by nearest fill to a province that "
                    "has no geographic intersection with any tile in the "
                    "ecodistrict; allocated with the district-wide tile share "
                    "and flagged, never silently assigned to a province."
                ),
            },
        },
        "model_catalogue": dict(model_counts),
        "partial_tile_coverage_ecodistricts": _partial_coverage_table(overlay),
        "validation": {
            "passed": validation["passed"],
            "checks": validation["checks"],
        },
    }


def _markdown_table(records: list[dict[str, Any]], columns: list[str]) -> str:
    if not records:
        return "_None._\n"
    header = "| " + " | ".join(columns) + " |"
    separator = "|" + "|".join("---" for _ in columns) + "|"
    lines = [header, separator]
    for record in records:
        values = []
        for column in columns:
            value = record.get(column, "")
            if isinstance(value, bool):
                values.append("PASS" if value else "FAIL")
            elif isinstance(value, float):
                values.append(f"{value:,.4f}")
            elif isinstance(value, int):
                values.append(f"{value:,}")
            else:
                values.append(str(value))
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines) + "\n"


def write_report(
    *,
    path: Path,
    overlay: pd.DataFrame,
    overlay_validation: Mapping[str, Any],
    area_diagnostics: Mapping[str, Any],
    library: pd.DataFrame,
    summary: Mapping[str, Any],
) -> None:
    """Write the human-readable production-library report."""
    metrics = overlay_validation["metrics"]
    tier_rows = summary["library"]["tier_table"]
    species_rows = summary["library"]["species_table"]
    province_rows = summary["library"]["province_table"]
    partial_rows = summary["partial_tile_coverage_ecodistricts"]
    validation_rows = summary["validation"]["checks"]
    failed = [row for row in validation_rows if not row["passed"]]

    report = f"""# Tile-level RSYC production curve library

**Status:** generated artefact; diagnostic, not wired into the national batch.
**Generated:** {summary["generated_at"]}. **Commit:** none.

## 1. Method

- Overlay: exact polygon intersection of the {metrics["tiles"]} published RSYC
  tiles and the {metrics["ecodistricts"]} RSYC ecodistricts in EPSG:3978
  (Canada Atlas Lambert, metres). No centroid or representative-point
  assignment is used.
- Province overlay: each tile x ecodistrict piece is split by the 13 province
  polygons from `canada_adm1_geoboundaries_simplified.geojson`; the area is the
  exact polygon intersection. {metrics["province_geometry_repaired"]} invalid
  province polygons were repaired with `make_valid` before intersection.
- Area allocation: `tile x province species area = sum(scanfi district x
  province species area x fraction_of_district_province)`. Fractions are
  normalised within each district x province and within the published 385-tile
  footprint. Source rows whose province has no geographic intersection with
  any tile in the district use an explicit district fallback and are reported
  separately; they are never silently assigned to a province.
- Curve selection: tile exact species, tile species group
  (`coniferous`/`broadleaf`), tile `generic`, then the tile's dominant
  ecoregion/ecoprovince control curve. Only national, published, converged
  volume curves are eligible. Ecodistrict curves are not used for production.
- No benchmark correction factor is applied.

## 2. Tile x ecodistrict intersection

| Metric | Value |
|---|---:|
| Tiles | {metrics["tiles"]:,} |
| Ecodistricts | {metrics["ecodistricts"]:,} |
| Intersection rows | {metrics["intersection_pairs"]:,} |
| Tiles touched | {metrics["tiles_touched"]:,} |
| Ecodistricts touched | {metrics["ecodistricts_touched"]:,} |
| Total intersection area (ha) | {metrics["total_intersection_area_ha"]:,.4f} |
| Tile layer area (ha) | {metrics["tile_area_ha"]:,.4f} |
| Ecodistrict layer area (ha) | {metrics["ecodistrict_area_ha"]:,.4f} |
| Intersection as share of tile area | {metrics["intersection_share_of_tile_area"] * 100:.4f}% |
| Intersection as share of ecodistrict area | {metrics["intersection_share_of_ecodistrict_area"] * 100:.4f}% |
| Tile area outside ecodistricts (ha) | {metrics["tile_only_area_ha"]:,.4f} |
| Ecodistrict area outside tiles (ha) | {metrics["ecodistrict_only_area_ha"]:,.4f} |
| Minimum intersection area (ha) | {metrics["min_intersection_area_ha"]:,.6f} |
| Zero-area boundary contacts skipped | {metrics["zero_area_contacts_skipped"]:,} |
| Non-polygonal parts coerced | {metrics["non_polygonal_parts_coerced"]:,} |
| Province polygons | {metrics["province_polygons"]:,} |
| Tile x ecodistrict x province rows | {metrics["province_intersection_pairs"]:,} |
| District x province pairs | {metrics["province_district_pairs"]:,} |
| Province share of tile x ecodistrict area | {metrics["province_cover_share_of_intersection"] * 100:.4f}% |
| Tile x ecodistrict area outside province polygons (ha) | {metrics["parent_area_outside_provinces_ha"]:,.4f} |
| Province boundary-only contacts skipped | {metrics["province_overlay_zero_area_contacts_skipped"]:,} |

The intersection is the forested-ecozone portion of the tile grid:
{metrics["intersection_share_of_tile_area"] * 100:.2f}% of the tile layer
area, because the RSYC ecological layers contain forested ecozones only. The
area accounting closes exactly: the pairwise intersection equals the union
intersection, and tile and ecodistrict areas each equal the intersection plus
their outside part (see §5).

Province polygons cover
{metrics["province_cover_share_of_intersection"] * 100:.4f}% of the tile x
ecodistrict area. The remaining
{metrics["parent_area_outside_provinces_ha"]:,.4f} ha is coastal, offshore, or
simplified-boundary area outside the province polygons; it is retained as a
reported geometric gap and never assigned to a province. Province attribution
does not rely on this gap: SCANFI forest blocks are allocated only through
positive province intersections, except for the explicitly reported
nearest-fill fallback below.

### Partial tile coverage

`fraction_of_ecodistrict` sums to 1 for every ecodistrict after normalisation.
Raw coverage is below 1 for {metrics["ecodistricts_with_partial_tile_coverage"]}
edge ecodistricts, with
{metrics["ecodistrict_area_outside_tile_grid_ha"]:,.4f} ha of ecodistrict area
outside the published tile grid. Their SCANFI area
({area_diagnostics["redistributed_area_ha"]:,.4f} ha,
{area_diagnostics["redistributed_area_share_pct"]:.6f}% of the national total)
is redistributed proportionally over the covered tiles, not dropped.

{_markdown_table(partial_rows, ["ecodistrict_id", "ecodistrict", "ecodistrict_area_ha", "covered_area_ha", "covered_fraction"])}
## 3. Curve selection and species coverage

| Evidence tier | Cells | Area (ha) | Area share |
|---|---:|---:|---:|
"""
    for row in tier_rows:
        report += (
            f"| `{row['evidence_tier']}` | {row['cells']:,} | "
            f"{row['area_ha']:,.4f} | {row['area_share_pct']:.4f}% |\n"
        )

    report += f"""
Total library cells: **{summary["library"]["cells"]:,}** across
**{summary["library"]["tiles"]:,}** tiles, **{summary["library"]["species"]}**
species targets, and **{summary["library"]["provinces"]}** provinces.
Control-fallback cells: **{summary["library"]["fallback_cells"]:,}**; negative
`b4` published fits flagged: **{summary["library"]["negative_b4_cells"]:,}**.

### By species

{_markdown_table(species_rows, ["species", "cells", "area_ha", "exact_pct", "group_pct", "generic_pct", "fallback_pct"])}
## 4. Area reconciliation and province table

| Metric | Area (ha) |
|---|---:|
| SCANFI ecodistrict source total | {area_diagnostics["source_total_ha"]:,.4f} |
| Allocated tile x province x species total | {area_diagnostics["allocated_total_ha"]:,.4f} |
| Difference | {area_diagnostics["difference_ha"]:.9f} |
| SCANFI area redistributed to covered tiles | {area_diagnostics["redistributed_area_ha"]:,.4f} |
| Redistributed share of national area | {area_diagnostics["redistributed_area_share_pct"]:.6f}% |
| Positive source rows | {area_diagnostics["positive_source_rows"]:,} |
| Zero-area source rows excluded | {area_diagnostics["zero_area_rows_excluded"]:,} |
| Tile x province x species cells | {area_diagnostics["tile_species_cells"]:,} |
| Province-intersection allocation | {area_diagnostics["geographic_allocation_area_ha"]:,.4f} |
| Nearest-fill area with no province intersection | {area_diagnostics["fallback_area_ha"]:,.4f} |
| No-intersection share of national area | {area_diagnostics["fallback_area_share_pct"]:.6f}% |
| District x province x species groups | {area_diagnostics["district_province_species_groups"]:,} |
| Maximum district x province x species closure error (ha) | {area_diagnostics["district_province_species_closure_max_abs_error_ha"]:.9f} |
| Maximum province area reconciliation error (ha) | {area_diagnostics["province_reconciliation_max_abs_error_ha"]:.9f} |

`{area_diagnostics["ecodistricts_with_scanfi_area_above_polygon"]}` ecodistricts
carry more SCANFI forest area than their polygon area (maximum ratio
{area_diagnostics["max_ecodistrict_scanfi_to_geometry_ratio"]:.4f}); the area
weights use 990 m blocks with nearest-unit fill. This propagates to
**{summary["area_geometry_consistency"]["tiles_with_allocated_area_above_polygon"]}**
tile(s). `{summary["area_geometry_consistency"]["max_ratio_tile"]}` has
{summary["area_geometry_consistency"]["max_allocated_to_tile_area_ratio"]:.4f}x
its polygon area, an excess of
{summary["area_geometry_consistency"]["excess_area_above_tile_polygon_ha"]:,.4f}
ha. The overlay is exact; the excess is inherited from the SCANFI area layer,
not from double-counted intersection. It is reported, not capped, because
capping would break the SCANFI total reconciliation.

### Allocation basis

{_markdown_table(summary["library"]["allocation_basis_table"], ["allocation_basis", "cells", "area_ha", "area_share_pct"])}
### Nearest-fill area with no valid geographic intersection

{_markdown_table(summary["library"]["no_province_intersection"]["groups"], ["ecodistrict_id", "ecodistrict", "province", "target_species", "area_ha", "reason"])}
These rows are allocated with the district-wide tile share because the
province label has no positive intersection with any tile in the district.
They are flagged in the library by `allocation_basis` and
`no_province_intersection_area_ha`; they are not treated as spatially
constrained province attribution.

### Province table

{_markdown_table(province_rows, ["province", "cells", "area_ha", "area_share_pct", "exact_pct", "group_pct", "generic_pct", "fallback_pct"])}
### Province attribution change versus the district-wide allocation

National province totals are unchanged because each district x province x species
source row still closes to its own province; what changes is which tile receives
the area. {area_diagnostics["province_attribution_change"]["tile_province_cells_changed"]:,}
tile x province cells move by more than {AREA_TOLERANCE_HA} ha; the maximum
absolute move is {area_diagnostics["province_attribution_change"]["max_abs_tile_province_change_ha"]:,.4f} ha.

{_markdown_table(area_diagnostics["province_attribution_change"]["top_tile_province_changes"], ["tile_id", "province", "legacy_area_ha", "constrained_area_ha", "difference_ha"])}
B25 province split (legacy district-wide -> province-constrained):
{_markdown_table(area_diagnostics["province_attribution_change"]["b25"], ["tile_id", "province", "legacy_area_ha", "constrained_area_ha", "difference_ha"])}
## 5. Validation

Validation summary: **{"PASS" if summary["validation"]["passed"] else "FAIL"}**.
"""
    if failed:
        report += "\nFailed checks:\n\n"
        for row in failed:
            report += f"- `{row['check']}`: {row['detail']}\n"
    report += f"""
{_markdown_table(validation_rows, ["check", "passed", "detail"])}
## 6. Paths and status

Outputs are under `data/derived/national/assortments/rsyc_tile_production/`:

- `rsyc_tile_production_library.csv`
- `tile_ecodistrict_weights.csv`
- `tile_ecodistrict_province_weights.csv`
- `rsyc_tile_production_summary.json`
- `RSYC_TILE_PRODUCTION_REPORT.md`
- `manifest.json`

No git commit was made. The national batch is unchanged and remains
diagnostic. The existing ecodistrict-first library is not replaced.

## 7. Next

Wire this library into a tile-level batch with one control cell per
`tile x province x species`, then reconcile the ecodistrict composition
diagnostic against the tile totals. The batch must carry `evidence_tier`,
`selected_level`, `model_id`, `control_total_source`, and `weight_source`.
Until the G1/G2 decisions in `planning/national-rsyc-backbone.md` are made,
this library is not a national supply estimate.
"""
    path.write_text(report, encoding="utf-8")


def build_manifest(
    *,
    inputs: list[Path],
    outputs: list[Path],
    validation_passed: bool,
    command: str,
) -> dict[str, Any]:
    """Build a checksum manifest for the run."""
    input_records = [
        {
            "path": str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path),
            "sha256": sha256_file(path),
            "bytes": path.stat().st_size,
        }
        for path in inputs
        if path.exists()
    ]
    output_records = []
    for path in outputs:
        record = {
            "name": path.name,
            "path": str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path),
            "sha256": sha256_file(path),
            "bytes": path.stat().st_size,
        }
        if path.suffix == ".csv":
            record["rows"] = int(sum(1 for _ in path.open("rb")) - 1)
        output_records.append(record)
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "committed": False,
        "command": command,
        "parameters": {
            "response": "volume",
            "model_scale": "national",
            "publish_ok": True,
            "fit_ok": True,
            "converged": True,
            "age_grid": list(AGE_GRID),
            "operational_age_range": [1, 150],
            "overlay": "exact_polygon_intersection_epsg3978",
            "province_overlay": "tile_x_ecodistrict_x_province_intersection",
            "area_allocation": (
                "province_fraction_normalised_within_district_province_and_"
                "tile_footprint_with_reported_district_fallback"
            ),
            "curve_ladder": [
                "tile exact species",
                "tile group",
                "tile generic",
                "dominant ecoregion/ecoprovince control fallback",
            ],
        },
        "inputs": input_records,
        "outputs": output_records,
        "validation_passed": bool(validation_passed),
    }


def run(
    *,
    tiles_path: Path = DEFAULT_TILES,
    ecostrat_path: Path = DEFAULT_ECOSTRAT,
    models_path: Path = DEFAULT_MODELS,
    provinces_path: Path = DEFAULT_PROVINCES,
    area_weights_path: Path = DEFAULT_AREA_WEIGHTS,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    planning_note: Path = DEFAULT_PLANNING_NOTE,
) -> dict[str, Any]:
    """Build, validate, and write the tile production library."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    tiles = load_tile_layer(tiles_path)
    ecodistricts = load_ecodistrict_layer(ecostrat_path)
    overlay = build_overlay(tiles, ecodistricts)
    district_validation = validate_overlay(overlay, tiles, ecodistricts)
    if not all(check["passed"] for check in district_validation["checks"]):
        failed = [
            check["check"]
            for check in district_validation["checks"]
            if not check["passed"]
        ]
        raise TileProductionError(f"tile x ecodistrict validation failed: {failed}")

    provinces = load_province_layer(provinces_path)
    province_overlay, province_metrics = build_province_overlay(
        tiles, ecodistricts, provinces, overlay
    )
    province_validation = validate_province_overlay(
        province_overlay, provinces, province_metrics
    )
    overlay_validation = {
        "checks": district_validation["checks"] + province_validation["checks"],
        "metrics": {**district_validation["metrics"], **province_metrics},
    }
    if not all(check["passed"] for check in overlay_validation["checks"]):
        failed = [
            check["check"]
            for check in overlay_validation["checks"]
            if not check["passed"]
        ]
        raise TileProductionError(
            f"tile x ecodistrict x province validation failed: {failed}"
        )

    area_weights = load_area_weights(area_weights_path)
    tile_species, _allocation_detail, area_diagnostics = allocate_area_to_tiles(
        overlay, province_overlay, area_weights
    )

    models = load_volume_models(models_path)
    model_index = build_model_index(models)
    model_counts = {
        "volume_models": int(models.shape[0]),
        "tile_curves": int(
            sum(1 for level, _strata, _species in model_index if level == "tile")
        ),
        "ecological_curves_by_level": validate_ecological_model_keys(
            ecodistricts, model_index
        ),
    }
    dominant = dominant_ecology(overlay)
    library = build_library(tile_species, model_index, dominant)
    validation = validate_library(
        library, tile_species, area_diagnostics, overlay_validation
    )
    if not validation["passed"]:
        failed = [check["check"] for check in validation["checks"] if not check["passed"]]
        raise TileProductionError(f"tile production validation failed: {failed}")

    summary = summarise(
        overlay=overlay,
        overlay_validation=overlay_validation,
        area_diagnostics=area_diagnostics,
        library=library,
        validation=validation,
        model_counts=model_counts,
    )

    weights_output = overlay[
        [
            "tile_id",
            "ecodistrict_id",
            "ecodistrict",
            "ecozone",
            "ecoprovince",
            "ecoregion",
            "ecodistrict_key",
            "ecoregion_key",
            "ecoprovince_key",
            "ecozone_key",
            "intersection_area_ha",
            "tile_area_ha",
            "ecodistrict_area_ha",
            "fraction_of_tile",
            "fraction_of_ecodistrict_raw",
            "ecodistrict_covered_area_ha",
            "ecodistrict_covered_fraction",
            "fraction_of_ecodistrict",
            "intersection_geometry_type",
        ]
    ].copy()
    weights_output = weights_output.sort_values(
        ["tile_id", "ecodistrict_id"], ignore_index=True
    )
    province_weights_output = province_overlay[
        [
            "tile_id",
            "ecodistrict_id",
            "province",
            "intersection_area_ha",
            "district_intersection_area_ha",
            "district_province_covered_area_ha",
            "fraction_of_district_province",
            "fraction_of_tile",
            "province_share_of_district_intersection",
        ]
    ].copy()

    library_path = output_dir / "rsyc_tile_production_library.csv"
    weights_path = output_dir / "tile_ecodistrict_weights.csv"
    province_weights_path = output_dir / "tile_ecodistrict_province_weights.csv"
    summary_path = output_dir / "rsyc_tile_production_summary.json"
    report_path = output_dir / "RSYC_TILE_PRODUCTION_REPORT.md"
    manifest_path = output_dir / "manifest.json"

    library.to_csv(library_path, index=False)
    weights_output.to_csv(weights_path, index=False)
    province_weights_output.to_csv(province_weights_path, index=False)
    summary_path.write_text(
        json.dumps(summary, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    write_report(
        path=report_path,
        overlay=overlay,
        overlay_validation=overlay_validation,
        area_diagnostics=area_diagnostics,
        library=library,
        summary=summary,
    )
    outputs = [
        library_path,
        weights_path,
        province_weights_path,
        summary_path,
        report_path,
    ]
    manifest = build_manifest(
        inputs=[
            Path(tiles_path),
            Path(ecostrat_path),
            Path(models_path),
            Path(provinces_path),
            Path(area_weights_path),
            Path(planning_note),
        ],
        outputs=outputs,
        validation_passed=validation["passed"],
        command="python -m workflows.national.assortments.rsyc_tile_production",
    )
    manifest_path.write_text(
        json.dumps(manifest, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    return {
        "library_path": str(library_path),
        "weights_path": str(weights_path),
        "province_weights_path": str(province_weights_path),
        "summary_path": str(summary_path),
        "report_path": str(report_path),
        "manifest_path": str(manifest_path),
        "validation_passed": validation["passed"],
        "library_rows": int(library.shape[0]),
        "overlay_rows": int(overlay.shape[0]),
        "province_overlay_rows": int(province_overlay.shape[0]),
        "total_area_ha": float(library["area_ha"].sum()),
    }


def main() -> None:
    """CLI entry point."""
    result = run()
    if not result["validation_passed"]:
        raise SystemExit("tile production validation failed")
    print(
        "tile production library written: "
        f"{result['library_rows']} cells, {result['overlay_rows']} overlay rows, "
        f"{result['total_area_ha']:.4f} ha"
    )


if __name__ == "__main__":
    main()
