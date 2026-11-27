#!/usr/bin/env python3
"""Build the national WS4 forest estate at 990-m fragments.

The national estate aggregates the 30-m SCANFI age and landcover rasters into
990-m fragments (33 x 33 SCANFI pixels).  Each fragment carries the forest area
inside the 990-m cell, an area-weighted mean age, and the dominant landcover
group.  The parent RSYC tile-province allocation supplies the group yield
curve.  Connected components of equal ``(age_class, curve_key, province)``
fragments become WS4 blocks; 50-km subcells are inventory windows and do not
clip a block.

The 30-m inventory resolution is retained for area and age aggregation.  The
WS4 fragment resolution is 990 m.  Those two resolutions are distinct and are
labelled separately in the manifest and documentation.

The build is fail-closed.  It refuses to write when either repository reports
an unexpected deletion, when a raster is not an aligned 30-m layer, when a
forest fragment has no RSYC group curve, or when an area/count invariant does
not close.  It never fabricates a fragment, curve, track, or schedule row.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.features import rasterize
from rasterio.transform import Affine
from rasterio.warp import transform_geom
from rasterio.windows import Window
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from shapely.geometry import Polygon
from shapely.validation import make_valid

from workflows.national.facilities.national_facility_inventory import (
    PROVINCES,
    SHAPE_NAME_TO_CODE,
)
from workflows.ws4_instances.build_scanfi_estate_pilot import (
    AGE_RASTER,
    AREA_CURVE_ID,
    CURVE_DOMAIN,
    DATA_ROOT,
    LANDCOVER_RASTER,
    PIXEL_AREA_HA,
    PIXEL_SIZE_M,
    REPO_ROOT,
    RSYC_LIBRARY_CSV,
    RSYC_TILES_GPKG,
    SUPPLY_NODES_CSV,
    WS4_BIN,
    CurveSpec,
    ScanfiEstateError,
    _repo_path,
    _safe_reset_output,
    _table_schema,
    _utc_now,
    _write_gitattributes,
    assert_no_unexpected_deletions,
    run_ws4_validate,
    sha256_file,
    ws4_provenance,
)

BOUNDARY_GEOJSON = DATA_ROOT / "raw/boundaries/canada_adm1_geoboundaries_simplified.geojson"
WS4_OUT_ROOT = DATA_ROOT / "derived/ws4"
NATIONAL_OUT_DIR = WS4_OUT_ROOT / "scanfi-national-990m"

BLOCK_PIXELS = 33
WS4_FRAGMENT_RESOLUTION_M = 990.0
WS4_FRAGMENT_AREA_HA = PIXEL_AREA_HA * BLOCK_PIXELS * BLOCK_PIXELS
FRAGMENT_RESOLUTION_LABEL = "990m"
INVENTORY_RESOLUTION_LABEL = "30m"
FOREST_LANDCOVER = (5, 6, 7)
LANDCOVER_NODATA = 255
AGE_NODATA = -32768
AGE_CLASS_WIDTH = 10
TREATMENT = "CC"
TREATMENT_BEGIN = 40
TREATMENT_END = 1000
CONNECTIVITY = "8-neighbour"
CHUNK_BLOCK_ROWS = 8
PROVINCE_ORDER = tuple(PROVINCES)
PROVINCE_INDEX = {code: index for index, code in enumerate(PROVINCE_ORDER)}
TILE_GRID_SIZE = 3

LABELS = {
    "evidence_label": "model_estimate",
    "observed": False,
    "policy_grade": False,
    "national_claim": False,
    "official_osecan_changed": False,
    "ui_changed": False,
    "sensitivity_only": True,
    "chain_v51_unchanged": True,
}


@dataclass(frozen=True, slots=True)
class BlockGrid:
    """Aligned national 990-m block grid derived from the SCANFI rasters."""

    crs: Any
    transform: Affine
    raster_width: int
    raster_height: int
    block_cols: int
    block_rows: int
    pixel_width: int
    pixel_height: int
    ignored_edge_columns: int
    ignored_edge_rows: int


@dataclass(frozen=True, slots=True)
class NodeGrid:
    """Rasterized parent-node identity at every 990-m block centre."""

    node_grid: np.ndarray
    node_ids: np.ndarray
    node_tile_ids: np.ndarray
    node_provinces: np.ndarray
    node_rows: np.ndarray
    node_cols: np.ndarray
    node_geom_area_ha: np.ndarray
    node_raster_area_ha: np.ndarray
    diagnostics: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class NationalCurveRegistry:
    """Resolved per-node group curves and their coefficient specifications."""

    specs: tuple[CurveSpec, ...]
    node_conifer_index: np.ndarray
    node_broadleaf_index: np.ndarray
    diagnostics: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ForestScan:
    """Aggregated 990-m forest cells from the chunked national scan."""

    cell_rows: np.ndarray
    cell_cols: np.ndarray
    forest_pixels: np.ndarray
    conifer_pixels: np.ndarray
    broadleaf_pixels: np.ndarray
    mixed_pixels: np.ndarray
    age: np.ndarray
    age_class: np.ndarray
    node_index: np.ndarray
    curve_index: np.ndarray
    province_index: np.ndarray
    landcover_group: np.ndarray
    group_fraction: np.ndarray
    totals: Mapping[str, Any]
    timings: Mapping[str, float]


@dataclass(frozen=True, slots=True)
class BlockAssignment:
    """Fragments, connected blocks, and grouping derived from a forest scan."""

    fragments: pd.DataFrame
    blocks: pd.DataFrame
    groups: pd.DataFrame
    block_province: np.ndarray
    block_curve_index: np.ndarray
    block_group_fraction: np.ndarray
    clique_summary: Mapping[str, Any]
    diagnostics: Mapping[str, Any]


# ---------------------------------------------------------------------------
# Grid and node identity.
# ---------------------------------------------------------------------------


def load_block_grid() -> BlockGrid:
    """Read both SCANFI rasters and derive the aligned 990-m block grid."""
    with rasterio.open(AGE_RASTER) as age_dataset, rasterio.open(
        LANDCOVER_RASTER
    ) as landcover_dataset:
        for path, dataset in (
            (AGE_RASTER, age_dataset),
            (LANDCOVER_RASTER, landcover_dataset),
        ):
            if dataset.crs is None:
                raise ScanfiEstateError(f"{path}: missing CRS")
            if dataset.transform.b != 0.0 or dataset.transform.d != 0.0:
                raise ScanfiEstateError(f"{path}: rotated transforms are unsupported")
            x_res, y_res = dataset.res
            if not (
                math.isclose(x_res, PIXEL_SIZE_M, abs_tol=1e-9)
                and math.isclose(y_res, PIXEL_SIZE_M, abs_tol=1e-9)
            ):
                raise ScanfiEstateError(
                    f"{path}: expected 30-m pixels, got {x_res!r} x {y_res!r}"
                )
            if not math.isclose(abs(dataset.transform.a), PIXEL_SIZE_M, abs_tol=1e-9):
                raise ScanfiEstateError(f"{path}: pixel width is not 30 m")
            if not math.isclose(abs(dataset.transform.e), PIXEL_SIZE_M, abs_tol=1e-9):
                raise ScanfiEstateError(f"{path}: pixel height is not 30 m")
        if age_dataset.crs != landcover_dataset.crs:
            raise ScanfiEstateError("SCANFI age and landcover CRS differ")
        if age_dataset.transform != landcover_dataset.transform:
            raise ScanfiEstateError("SCANFI age and landcover transform differ")
        if age_dataset.width != landcover_dataset.width:
            raise ScanfiEstateError("SCANFI age and landcover width differ")
        if age_dataset.height != landcover_dataset.height:
            raise ScanfiEstateError("SCANFI age and landcover height differ")
        block_rows = age_dataset.height // BLOCK_PIXELS
        block_cols = age_dataset.width // BLOCK_PIXELS
        if block_rows <= 0 or block_cols <= 0:
            raise ScanfiEstateError("SCANFI raster is smaller than one 990-m block")
        block_transform = Affine(
            WS4_FRAGMENT_RESOLUTION_M,
            0.0,
            age_dataset.transform.c,
            0.0,
            -WS4_FRAGMENT_RESOLUTION_M,
            age_dataset.transform.f,
        )
        return BlockGrid(
            crs=age_dataset.crs,
            transform=block_transform,
            raster_width=age_dataset.width,
            raster_height=age_dataset.height,
            block_cols=block_cols,
            block_rows=block_rows,
            pixel_width=block_cols * BLOCK_PIXELS,
            pixel_height=block_rows * BLOCK_PIXELS,
            ignored_edge_columns=age_dataset.width - block_cols * BLOCK_PIXELS,
            ignored_edge_rows=age_dataset.height - block_rows * BLOCK_PIXELS,
        )


def load_supply_nodes(path: Path = SUPPLY_NODES_CSV) -> pd.DataFrame:
    """Load and validate the committed 50-km supply-node table."""
    if not path.is_file():
        raise ScanfiEstateError(f"supply-node table is missing: {path}")
    frame = pd.read_csv(path)
    required = {
        "node_id",
        "tile_id",
        "province",
        "row",
        "col",
        "subcell_geometry_area_ha",
    }
    missing = required - set(frame.columns)
    if missing:
        raise ScanfiEstateError(f"{path}: missing columns {sorted(missing)}")
    if frame["node_id"].duplicated().any():
        raise ScanfiEstateError(f"{path}: duplicate node_id")
    if not set(frame["province"].astype(str)) <= set(PROVINCE_ORDER):
        unknown = set(frame["province"].astype(str)) - set(PROVINCE_ORDER)
        raise ScanfiEstateError(f"{path}: unknown provinces {sorted(unknown)}")
    for column in ("row", "col"):
        values = pd.to_numeric(frame[column], errors="raise").astype(int)
        if not values.between(1, TILE_GRID_SIZE).all():
            raise ScanfiEstateError(f"{path}: {column} outside 1..3")
        frame[column] = values
    frame["subcell_geometry_area_ha"] = pd.to_numeric(
        frame["subcell_geometry_area_ha"], errors="raise"
    )
    if not np.all(np.isfinite(frame["subcell_geometry_area_ha"])):
        raise ScanfiEstateError(f"{path}: non-finite node area")
    if (frame["subcell_geometry_area_ha"] <= 0.0).any():
        raise ScanfiEstateError(f"{path}: non-positive node area")
    key = frame[["tile_id", "province", "row", "col"]]
    if key.duplicated().any():
        raise ScanfiEstateError(f"{path}: duplicate tile/province/row/col node")
    return frame.reset_index(drop=True)


def _subcell_box(tile_geometry: Any, row: int, col: int) -> Polygon:
    min_x, min_y, max_x, max_y = tile_geometry.bounds
    width = (max_x - min_x) / TILE_GRID_SIZE
    height = (max_y - min_y) / TILE_GRID_SIZE
    return Polygon(
        [
            (min_x + (col - 1) * width, min_y + (row - 1) * height),
            (min_x + col * width, min_y + (row - 1) * height),
            (min_x + col * width, min_y + row * height),
            (min_x + (col - 1) * width, min_y + row * height),
        ]
    )


def load_rsyc_geometries() -> tuple[dict[str, Any], dict[str, Any]]:
    """Return RSYC tile geometries (EPSG:3978) and province geometries."""
    tiles = gpd.read_file(RSYC_TILES_GPKG, layer="RSYC_tiles")
    if tiles.crs is None or tiles.crs.to_epsg() != 3978:
        raise ScanfiEstateError(f"{RSYC_TILES_GPKG}: expected EPSG:3978")
    tile_geometries: dict[str, Any] = {}
    for tile_id, geometry in zip(
        tiles["TileID"].astype(str), tiles.geometry, strict=True
    ):
        if tile_id in tile_geometries:
            raise ScanfiEstateError(f"{RSYC_TILES_GPKG}: duplicate tile {tile_id!r}")
        valid = make_valid(geometry)
        if valid.is_empty or valid.area <= 0.0:
            raise ScanfiEstateError(f"{RSYC_TILES_GPKG}: invalid tile {tile_id!r}")
        tile_geometries[tile_id] = valid
    provinces = gpd.read_file(BOUNDARY_GEOJSON).to_crs(3978)
    province_geometries: dict[str, Any] = {}
    for name, geometry in zip(
        provinces["shapeName"].astype(str), provinces.geometry, strict=True
    ):
        code = SHAPE_NAME_TO_CODE.get(name)
        if code is None:
            raise ScanfiEstateError(f"{BOUNDARY_GEOJSON}: unknown province {name!r}")
        if code in province_geometries:
            raise ScanfiEstateError(f"{BOUNDARY_GEOJSON}: duplicate province {code!r}")
        valid = make_valid(geometry)
        if valid.is_empty or valid.area <= 0.0:
            raise ScanfiEstateError(f"{BOUNDARY_GEOJSON}: invalid province {code!r}")
        province_geometries[code] = valid
    missing = set(PROVINCE_ORDER) - set(province_geometries)
    if missing:
        raise ScanfiEstateError(
            f"{BOUNDARY_GEOJSON}: missing provinces {sorted(missing)}"
        )
    return tile_geometries, province_geometries


def build_node_grid(
    nodes: pd.DataFrame,
    grid: BlockGrid,
    *,
    tile_geometries: Mapping[str, Any] | None = None,
    province_geometries: Mapping[str, Any] | None = None,
) -> NodeGrid:
    """Rasterize each node's 50-km tile-province intersection at 990 m.

    A node's true geometry is ``tile bbox 3x3 subcell`` intersected with the
    parent ``tile ∩ province`` allocation.  Using the allocation geometry, not
    a province raster alone, prevents cells in an unallocated neighbour
    province from being assigned to a node that does not exist.
    """
    if tile_geometries is None or province_geometries is None:
        tile_geometries, province_geometries = load_rsyc_geometries()
    shapes: list[tuple[Mapping[str, Any], int]] = []
    reconstructed_area = np.zeros(len(nodes), dtype=float)
    for index, row in enumerate(nodes.itertuples(index=False)):
        tile_id = str(row.tile_id)
        province = str(row.province)
        tile_geometry = tile_geometries.get(tile_id)
        province_geometry = province_geometries.get(province)
        if tile_geometry is None:
            raise ScanfiEstateError(f"RSYC tile {tile_id!r} has no geometry")
        if province_geometry is None:
            raise ScanfiEstateError(f"province {province!r} has no geometry")
        parent = make_valid(tile_geometry.intersection(province_geometry))
        if parent.is_empty:
            raise ScanfiEstateError(
                f"node {row.node_id!r}: tile-province intersection is empty"
            )
        box = _subcell_box(tile_geometry, int(row.row), int(row.col))
        geometry = make_valid(box.intersection(parent))
        area_ha = float(geometry.area / 10_000.0)
        if geometry.is_empty or area_ha <= 0.0:
            raise ScanfiEstateError(f"node {row.node_id!r}: empty subcell geometry")
        expected = float(row.subcell_geometry_area_ha)
        if not math.isclose(area_ha, expected, rel_tol=1e-9, abs_tol=1e-6):
            raise ScanfiEstateError(
                f"node {row.node_id!r}: reconstructed area {area_ha} != {expected}"
            )
        reconstructed_area[index] = area_ha
        shapes.append(
            (
                transform_geom(
                    "EPSG:3978", grid.crs, geometry.__geo_interface__
                ),
                index,
            )
        )

    node_grid = rasterize(
        shapes,
        out_shape=(grid.block_rows, grid.block_cols),
        transform=grid.transform,
        fill=-1,
        dtype="int32",
        all_touched=False,
    )
    mapped = node_grid >= 0
    counts = np.bincount(node_grid[mapped], minlength=len(nodes))
    raster_area = counts.astype(float) * WS4_FRAGMENT_AREA_HA
    total_geometry = float(reconstructed_area.sum())
    total_raster = float(raster_area.sum())
    if total_geometry <= 0.0:
        raise ScanfiEstateError("no positive node geometry")
    area_error = abs(total_raster - total_geometry) / total_geometry
    if area_error > 0.01:
        raise ScanfiEstateError(
            "rasterized node area differs from geometric area by "
            f"{area_error:.2%}"
        )
    diagnostics = {
        "nodes": len(nodes),
        "tiles": len(tile_geometries),
        "provinces": len(province_geometries),
        "block_cells": int(grid.block_rows * grid.block_cols),
        "node_cells": int(np.count_nonzero(mapped)),
        "node_cells_without_allocation": int(
            np.count_nonzero(~mapped)
        ),
        "nodes_with_zero_raster_cells": int(np.count_nonzero(counts == 0)),
        "geometric_area_ha": total_geometry,
        "rasterized_area_ha": total_raster,
        "relative_area_error": area_error,
    }
    return NodeGrid(
        node_grid=node_grid,
        node_ids=nodes["node_id"].astype(str).to_numpy(),
        node_tile_ids=nodes["tile_id"].astype(str).to_numpy(),
        node_provinces=nodes["province"].astype(str).to_numpy(),
        node_rows=nodes["row"].to_numpy(dtype=np.int64),
        node_cols=nodes["col"].to_numpy(dtype=np.int64),
        node_geom_area_ha=reconstructed_area,
        node_raster_area_ha=raster_area,
        diagnostics=diagnostics,
    )


# ---------------------------------------------------------------------------
# RSYC group curves.
# ---------------------------------------------------------------------------


def _coefficient_signature(frame: pd.DataFrame) -> np.ndarray:
    return (
        frame["b1"].round(12).astype(str)
        + "|"
        + frame["b2"].round(12).astype(str)
        + "|"
        + frame["b3"].round(12).astype(str)
        + "|"
        + frame["b4"].round(12).astype(str)
    ).to_numpy()


def resolve_national_curves(
    library: pd.DataFrame, nodes: pd.DataFrame
) -> NationalCurveRegistry:
    """Resolve one tile group curve per node, with generic fallback.

    The national basis is the RSYC tile group curve.  A tile's coniferous and
    broadleaf rows are used as the group curves; when a literal broadleaf group
    row is absent, the tile generic row is the fallback.  Species-specific
    curves are deliberately not selected because the national builder does not
    process the SCANFI species rasters.  A node with no group and no generic
    curve fails the build.
    """
    required = {
        "tile_id",
        "province",
        "species",
        "species_kind",
        "model_id",
        "selected_level",
        "source_species",
        "status",
        "b1",
        "b2",
        "b3",
        "b4",
    }
    missing = required - set(library.columns)
    if missing:
        raise ScanfiEstateError(f"RSYC library missing columns {sorted(missing)}")
    frame = library.copy()
    for column in ("b1", "b2", "b3", "b4"):
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    numeric = frame[["b1", "b2", "b3", "b4"]]
    if not np.all(np.isfinite(numeric.to_numpy())):
        raise ScanfiEstateError("RSYC library has non-finite coefficients")
    if (frame[["b1", "b2", "b3"]] <= 0.0).any().any():
        raise ScanfiEstateError("RSYC library has non-positive b1/b2/b3")
    status = frame["status"].astype(str)
    if (status != "ok").any():
        raise ScanfiEstateError("RSYC library has non-ok rows")
    frame["_source_species"] = frame["source_species"].astype(str).str.lower()
    frame["_species"] = frame["species"].astype(str)

    signatures = _coefficient_signature(frame)
    coefficient_sets: dict[str, set[str]] = defaultdict(set)
    for model_id, signature in zip(frame["model_id"].astype(str), signatures, strict=True):
        coefficient_sets[model_id].add(signature)
    ambiguous = sorted(
        model_id for model_id, values in coefficient_sets.items() if len(values) > 1
    )
    if ambiguous:
        raise ScanfiEstateError(
            "RSYC model_id maps to multiple coefficient sets: " + ", ".join(ambiguous)
        )

    conifer_mask = (frame["_source_species"] == "coniferous") | (
        frame["_species"] == "CONIFEROUS"
    )
    broadleaf_mask = (
        (frame["_source_species"] == "broadleaf") | (frame["_species"] == "BROADLEAF")
    ) & (frame["_source_species"] != "generic")
    generic_mask = frame["_source_species"] == "generic"

    def group_model_map(mask: pd.Series, label: str) -> dict[tuple[str, str], str]:
        subset = frame.loc[mask]
        mapping: dict[tuple[str, str], str] = {}
        for (tile_id, province), group in subset.groupby(
            ["tile_id", "province"], sort=True
        ):
            models = group["model_id"].astype(str).unique()
            if len(models) != 1:
                raise ScanfiEstateError(
                    f"RSYC {label} group has multiple models for "
                    f"{tile_id}|{province}: {sorted(models)}"
                )
            mapping[(str(tile_id), str(province))] = str(models[0])
        return mapping

    conifer_models = group_model_map(conifer_mask, "coniferous")
    broadleaf_models = group_model_map(broadleaf_mask, "broadleaf")
    generic_models = group_model_map(generic_mask, "generic")
    if not conifer_models:
        raise ScanfiEstateError("RSYC library has no coniferous group curve")

    node_conifer_model: list[str | None] = []
    node_broadleaf_model: list[str | None] = []
    resolution_counts = {"group": 0, "generic": 0}
    missing_examples: list[str] = []
    used_models: set[str] = set()
    for row in nodes.itertuples(index=False):
        key = (str(row.tile_id), str(row.province))
        conifer_model = conifer_models.get(key)
        if conifer_model is None:
            missing_examples.append(f"{key[0]}|{key[1]}|CONIFEROUS")
            node_conifer_model.append(None)
            node_broadleaf_model.append(None)
            continue
        broadleaf_model = broadleaf_models.get(key)
        if broadleaf_model is None:
            broadleaf_model = generic_models.get(key)
            if broadleaf_model is None:
                missing_examples.append(f"{key[0]}|{key[1]}|BROADLEAF")
                node_conifer_model.append(None)
                node_broadleaf_model.append(None)
                continue
            resolution_counts["generic"] += 1
        else:
            resolution_counts["group"] += 1
        node_conifer_model.append(conifer_model)
        node_broadleaf_model.append(broadleaf_model)
        used_models.update((conifer_model, broadleaf_model))
    if missing_examples:
        raise ScanfiEstateError(
            "nodes have no RSYC group or generic curve: "
            + ", ".join(sorted(set(missing_examples))[:20])
        )

    # Build one specification per used model.  A model row is represented by
    # its literal group row when one exists, otherwise by its first row.
    representative: dict[str, pd.Series] = {}
    for model_id in sorted(used_models):
        candidates = frame[frame["model_id"].astype(str) == model_id]
        literal = candidates[
            (candidates["_species"].isin(["CONIFEROUS", "BROADLEAF"]))
            & (candidates["_source_species"] != "generic")
        ]
        row = literal.iloc[0] if not literal.empty else candidates.iloc[0]
        representative[model_id] = row
    specs = tuple(
        CurveSpec(
            key=model_id,
            model_id=model_id,
            species=str(representative[model_id]["species"]),
            species_kind=str(representative[model_id]["species_kind"]),
            selected_level=str(representative[model_id]["selected_level"]),
            source_species=str(representative[model_id]["source_species"]),
            b1=float(representative[model_id]["b1"]),
            b2=float(representative[model_id]["b2"]),
            b3=float(representative[model_id]["b3"]),
            b4=float(representative[model_id]["b4"]),
        )
        for model_id in sorted(used_models)
    )
    model_index = {spec.key: index for index, spec in enumerate(specs)}
    for spec in specs:
        spec.curve_values()
    if any(value is None for value in node_conifer_model) or any(
        value is None for value in node_broadleaf_model
    ):
        raise ScanfiEstateError("node curve resolution left a missing curve")
    node_conifer = np.array(
        [model_index[str(value)] for value in node_conifer_model], dtype=np.int32
    )
    node_broadleaf = np.array(
        [model_index[str(value)] for value in node_broadleaf_model], dtype=np.int32
    )
    diagnostics = {
        "library_rows": len(frame),
        "models_resolved": len(specs),
        "coniferous_group_keys": len(conifer_models),
        "broadleaf_group_keys": len(broadleaf_models),
        "generic_fallback_keys": len(generic_models),
        "node_group_resolution": resolution_counts,
        "coefficient_ambiguities": ambiguous,
        "basis": "tile group curve; broadleaf falls back to tile generic",
        "species_specific_curves_used": False,
    }
    return NationalCurveRegistry(
        specs=specs,
        node_conifer_index=node_conifer,
        node_broadleaf_index=node_broadleaf,
        diagnostics=diagnostics,
    )


# ---------------------------------------------------------------------------
# Chunked SCANFI scan.
# ---------------------------------------------------------------------------


def aggregate_chunk(age: np.ndarray, landcover: np.ndarray) -> dict[str, np.ndarray]:
    """Aggregate one 33x33-pixel block chunk into forest-cell statistics.

    ``age`` and ``landcover`` must have shapes divisible by 33.  The returned
    arrays are two-dimensional in block rows and block columns.
    """
    if age.ndim != 2 or landcover.ndim != 2:
        raise ScanfiEstateError("chunk arrays must be two-dimensional")
    if age.shape != landcover.shape:
        raise ScanfiEstateError("chunk age and landcover shapes differ")
    block_rows, block_cols = age.shape[0] // BLOCK_PIXELS, age.shape[1] // BLOCK_PIXELS
    if block_rows * BLOCK_PIXELS != age.shape[0] or block_cols * BLOCK_PIXELS != age.shape[1]:
        raise ScanfiEstateError("chunk shape is not divisible by 33")
    shape = (block_rows, BLOCK_PIXELS, block_cols, BLOCK_PIXELS)
    forest = np.isin(landcover, FOREST_LANDCOVER) & (age > 0) & (age != AGE_NODATA)
    conifer = forest & (landcover == 6)
    broadleaf = forest & (landcover == 5)
    age_sum = np.where(forest, age, 0).reshape(shape).sum(axis=(1, 3), dtype=np.int64)
    forest_pixels = forest.reshape(shape).sum(axis=(1, 3), dtype=np.int32)
    conifer_pixels = conifer.reshape(shape).sum(axis=(1, 3), dtype=np.int32)
    broadleaf_pixels = broadleaf.reshape(shape).sum(axis=(1, 3), dtype=np.int32)
    mixed_pixels = forest_pixels - conifer_pixels - broadleaf_pixels
    if np.any(mixed_pixels < 0):
        raise ScanfiEstateError("mixed-pixel count is negative")
    if np.any((forest_pixels > 0) & (age_sum <= 0)):
        raise ScanfiEstateError("forest pixels have no positive age mass")
    safe_count = np.maximum(forest_pixels, 1)
    block_age = np.floor(age_sum / safe_count + 0.5).astype(np.int32)
    block_age[forest_pixels == 0] = 0
    age_class = (block_age // AGE_CLASS_WIDTH) * AGE_CLASS_WIDTH
    conifer_dominant = conifer_pixels >= broadleaf_pixels
    dominant_pixels = np.where(
        conifer_dominant, conifer_pixels, broadleaf_pixels
    )
    group_fraction = (dominant_pixels + mixed_pixels) / safe_count
    group_fraction[forest_pixels == 0] = 0.0
    landcover_group = np.where(conifer_dominant, "CONIFEROUS", "BROADLEAF")
    return {
        "forest_pixels": forest_pixels,
        "conifer_pixels": conifer_pixels,
        "broadleaf_pixels": broadleaf_pixels,
        "mixed_pixels": mixed_pixels,
        "age_sum": age_sum,
        "age": block_age,
        "age_class": age_class,
        "landcover_group": landcover_group,
        "group_fraction": group_fraction.astype(np.float32),
    }


def scan_forest_cells(
    grid: BlockGrid,
    node_grid: NodeGrid,
    curves: NationalCurveRegistry,
    *,
    chunk_block_rows: int = CHUNK_BLOCK_ROWS,
) -> ForestScan:
    """Scan the national rasters once in block-row strips and aggregate blocks."""
    if chunk_block_rows <= 0:
        raise ScanfiEstateError("chunk_block_rows must be positive")
    chunks: dict[str, list[np.ndarray]] = defaultdict(list)
    total_pixels = 0
    forest_pixels_all = 0
    landcover_nodata = 0
    age_nodata_in_forest_class = 0
    nonforest = 0
    nonpositive_age = 0
    outside_node_cells = 0
    outside_node_pixels = 0
    province_lookup = np.array(
        [PROVINCE_INDEX[value] for value in node_grid.node_provinces],
        dtype=np.int32,
    )
    started = time.perf_counter()
    with rasterio.open(AGE_RASTER) as age_dataset, rasterio.open(
        LANDCOVER_RASTER
    ) as landcover_dataset:
        for start in range(0, grid.block_rows, chunk_block_rows):
            row_count = min(chunk_block_rows, grid.block_rows - start)
            window = Window(
                0,
                start * BLOCK_PIXELS,
                grid.pixel_width,
                row_count * BLOCK_PIXELS,
            )
            age = age_dataset.read(1, window=window)
            landcover = landcover_dataset.read(1, window=window)
            if age.shape != landcover.shape:
                raise ScanfiEstateError("chunk rasters differ in shape")
            aggregated = aggregate_chunk(age, landcover)
            total_pixels += int(age.size)
            landcover_nodata += int(np.count_nonzero(landcover == LANDCOVER_NODATA))
            forest_class = np.isin(landcover, FOREST_LANDCOVER)
            age_nodata_in_forest_class += int(
                np.count_nonzero(forest_class & (age == AGE_NODATA))
            )
            nonforest += int(
                forest_class.size
                - int(np.count_nonzero(forest_class))
                - int(np.count_nonzero(landcover == LANDCOVER_NODATA))
            )
            nonpositive_age += int(
                np.count_nonzero(
                    forest_class
                    & (landcover != LANDCOVER_NODATA)
                    & (age != AGE_NODATA)
                    & (age <= 0)
                )
            )
            forest_pixels_all += int(aggregated["forest_pixels"].sum())

            forest = aggregated["forest_pixels"] > 0
            if not forest.any():
                continue
            block_row_index, block_col_index = np.nonzero(forest)
            forest_pixels = aggregated["forest_pixels"][forest]
            node_index = node_grid.node_grid[
                start + block_row_index, block_col_index
            ]
            included = node_index >= 0
            excluded_pixels = int(forest_pixels[~included].sum())
            outside_node_pixels += excluded_pixels
            outside_node_cells += int(np.count_nonzero(~included))
            if not included.any():
                continue
            block_row_index = block_row_index[included]
            block_col_index = block_col_index[included]
            forest_pixels = forest_pixels[included]
            node_index = node_index[included].astype(np.int32)
            conifer_pixels = aggregated["conifer_pixels"][forest][included]
            broadleaf_pixels = aggregated["broadleaf_pixels"][forest][included]
            mixed_pixels = aggregated["mixed_pixels"][forest][included]
            age_values = aggregated["age"][forest][included]
            age_class = aggregated["age_class"][forest][included]
            group_fraction = aggregated["group_fraction"][forest][included]
            landcover_group = aggregated["landcover_group"][forest][included]
            conifer_curve = curves.node_conifer_index[node_index]
            broadleaf_curve = curves.node_broadleaf_index[node_index]
            if np.any(conifer_curve < 0) or np.any(broadleaf_curve < 0):
                raise ScanfiEstateError("a forest node has no resolved RSYC curve")
            use_conifer = landcover_group == "CONIFEROUS"
            curve_index = np.where(use_conifer, conifer_curve, broadleaf_curve)
            province_index = province_lookup[node_index]
            chunks["cell_rows"].append((start + block_row_index).astype(np.int32))
            chunks["cell_cols"].append(block_col_index.astype(np.int32))
            chunks["forest_pixels"].append(forest_pixels.astype(np.int32))
            chunks["conifer_pixels"].append(conifer_pixels.astype(np.int32))
            chunks["broadleaf_pixels"].append(broadleaf_pixels.astype(np.int32))
            chunks["mixed_pixels"].append(mixed_pixels.astype(np.int32))
            chunks["age"].append(age_values.astype(np.int32))
            chunks["age_class"].append(age_class.astype(np.int32))
            chunks["node_index"].append(node_index)
            chunks["curve_index"].append(curve_index.astype(np.int32))
            chunks["province_index"].append(province_index)
            chunks["landcover_group"].append(landcover_group.astype(str))
            chunks["group_fraction"].append(group_fraction.astype(np.float32))
            if (start // chunk_block_rows) % 20 == 0:
                elapsed = time.perf_counter() - started
                fraction = (start + row_count) / grid.block_rows
                print(
                    f"  scan blocks {start + row_count}/{grid.block_rows} "
                    f"({fraction:.1%}) in {elapsed:.1f}s",
                    flush=True,
                )

    required = (
        "cell_rows",
        "cell_cols",
        "forest_pixels",
        "conifer_pixels",
        "broadleaf_pixels",
        "mixed_pixels",
        "age",
        "age_class",
        "node_index",
        "curve_index",
        "province_index",
        "landcover_group",
        "group_fraction",
    )
    if any(not chunks[name] for name in required):
        raise ScanfiEstateError("national scan found no forest fragments")
    arrays = {
        name: np.concatenate(chunks[name]) if chunks[name] else np.empty(0)
        for name in required
    }
    if len(arrays["cell_rows"]) == 0:
        raise ScanfiEstateError("national scan found no forest fragments")
    if not np.array_equal(
        arrays["forest_pixels"],
        arrays["conifer_pixels"]
        + arrays["broadleaf_pixels"]
        + arrays["mixed_pixels"],
    ):
        raise ScanfiEstateError("landcover group counts do not sum to forest pixels")
    if np.any(arrays["age"] <= 0):
        raise ScanfiEstateError("a forest fragment has non-positive age")
    if np.any(arrays["age_class"] < 0):
        raise ScanfiEstateError("a forest fragment has a negative age class")
    timings = {"scan_seconds": time.perf_counter() - started}
    included_pixels = int(arrays["forest_pixels"].sum())
    totals = {
        "scanned_pixels": total_pixels,
        "forest_pixels_in_scan": forest_pixels_all,
        "included_forest_pixels": included_pixels,
        "included_forest_area_ha": included_pixels * PIXEL_AREA_HA,
        "outside_node_pixels": outside_node_pixels,
        "outside_node_area_ha": outside_node_pixels * PIXEL_AREA_HA,
        "outside_node_cells": outside_node_cells,
        "landcover_nodata_pixels": landcover_nodata,
        "age_nodata_in_forest_class_pixels": age_nodata_in_forest_class,
        "nonforest_pixels": nonforest,
        "forest_class_age_nonpositive_pixels": nonpositive_age,
        "ignored_edge_columns": grid.ignored_edge_columns,
        "ignored_edge_rows": grid.ignored_edge_rows,
        "ignored_edge_pixels": (
            grid.ignored_edge_rows * grid.raster_width
            + grid.ignored_edge_columns * grid.pixel_height
        ),
    }
    return ForestScan(
        cell_rows=arrays["cell_rows"],
        cell_cols=arrays["cell_cols"],
        forest_pixels=arrays["forest_pixels"],
        conifer_pixels=arrays["conifer_pixels"],
        broadleaf_pixels=arrays["broadleaf_pixels"],
        mixed_pixels=arrays["mixed_pixels"],
        age=arrays["age"],
        age_class=arrays["age_class"],
        node_index=arrays["node_index"],
        curve_index=arrays["curve_index"],
        province_index=arrays["province_index"],
        landcover_group=arrays["landcover_group"],
        group_fraction=arrays["group_fraction"],
        totals=totals,
        timings=timings,
    )


# ---------------------------------------------------------------------------
# Connected blocks.
# ---------------------------------------------------------------------------


def label_blocks(
    cell_rows: np.ndarray,
    cell_cols: np.ndarray,
    code: np.ndarray,
    grid_shape: tuple[int, int],
) -> tuple[np.ndarray, np.ndarray, int]:
    """Label 8-neighbour connected components of equal block codes.

    Returns ``(fragment_block_id, representative_fragment_index, block_count)``.
    Block ids are ordered by the first fragment in row-major scan order.
    """
    fragment_count = len(cell_rows)
    if fragment_count == 0:
        raise ScanfiEstateError("cannot label an empty fragment set")
    if len(cell_cols) != fragment_count or len(code) != fragment_count:
        raise ScanfiEstateError("fragment grid arrays differ in length")
    block_rows, block_cols = grid_shape
    cell_index = np.full((block_rows, block_cols), -1, dtype=np.int32)
    code_grid = np.full((block_rows, block_cols), -1, dtype=np.int64)
    positions = np.arange(fragment_count, dtype=np.int32)
    cell_index[cell_rows, cell_cols] = positions
    code_grid[cell_rows, cell_cols] = code
    edge_sources: list[np.ndarray] = []
    edge_targets: list[np.ndarray] = []
    directions = ((0, 1), (1, 0), (1, 1), (1, -1))
    for delta_row, delta_col in directions:
        if delta_row == 0:
            source_slice = (slice(None), slice(0, block_cols - 1))
            target_slice = (slice(None), slice(1, block_cols))
        elif delta_col == 0:
            source_slice = (slice(0, block_rows - 1), slice(None))
            target_slice = (slice(1, block_rows), slice(None))
        elif delta_col > 0:
            source_slice = (
                slice(0, block_rows - 1),
                slice(0, block_cols - 1),
            )
            target_slice = (slice(1, block_rows), slice(1, block_cols))
        else:
            source_slice = (
                slice(0, block_rows - 1),
                slice(1, block_cols),
            )
            target_slice = (slice(1, block_rows), slice(0, block_cols - 1))
        source_index = cell_index[source_slice]
        target_index = cell_index[target_slice]
        same_code = code_grid[source_slice] == code_grid[target_slice]
        mask = (source_index >= 0) & (target_index >= 0) & same_code
        if mask.any():
            edge_sources.append(source_index[mask].astype(np.int32))
            edge_targets.append(target_index[mask].astype(np.int32))
    if edge_sources:
        sources = np.concatenate(edge_sources)
        targets = np.concatenate(edge_targets)
        graph = coo_matrix(
            (np.ones(len(sources), dtype=np.uint8), (sources, targets)),
            shape=(fragment_count, fragment_count),
        )
        component_count, labels = connected_components(
            graph, directed=False, return_labels=True
        )
        component_count = int(component_count)
    else:
        component_count = fragment_count
        labels = np.arange(fragment_count, dtype=np.int32)
    first_position = np.full(component_count, fragment_count, dtype=np.int64)
    np.minimum.at(
        first_position, labels, np.arange(fragment_count, dtype=np.int64)
    )
    if np.any(first_position >= fragment_count):
        raise ScanfiEstateError("a connected component has no representative")
    order = np.argsort(first_position, kind="stable")
    rank = np.empty(component_count, dtype=np.int64)
    rank[order] = np.arange(1, component_count + 1, dtype=np.int64)
    representative_fragment = first_position[order]
    return (
        rank[labels].astype(np.int64),
        representative_fragment.astype(np.int64),
        component_count,
    )


def assign_blocks(
    scan: ForestScan,
    grid: BlockGrid,
    used_specs: Sequence[CurveSpec],
) -> BlockAssignment:
    """Build fragments, connected blocks, and groups from aggregated cells."""
    fragment_count = len(scan.cell_rows)
    if fragment_count == 0:
        raise ScanfiEstateError("no fragments to assign")
    province_count = len(PROVINCE_ORDER)
    curve_count = len(used_specs)
    age_bins = scan.age_class // AGE_CLASS_WIDTH
    if np.any(age_bins >= 1024):
        raise ScanfiEstateError("age class exceeds the block code capacity")
    code = (
        (scan.province_index.astype(np.int64) * 1024 + age_bins.astype(np.int64))
        * curve_count
        + scan.curve_index.astype(np.int64)
    )
    block_ids, representative, block_count = label_blocks(
        scan.cell_rows,
        scan.cell_cols,
        code,
        (grid.block_rows, grid.block_cols),
    )
    fragment_area = scan.forest_pixels.astype(float) * PIXEL_AREA_HA
    block_area = np.bincount(
        block_ids, weights=fragment_area, minlength=block_count + 1
    )[1:]
    if int(block_ids.min()) != 1 or int(block_ids.max()) != block_count:
        raise ScanfiEstateError("block ids are not contiguous from one")
    if len(representative) != block_count:
        raise ScanfiEstateError("block representative count differs from blocks")
    if not math.isclose(
        float(block_area.sum()), float(fragment_area.sum()), rel_tol=1e-12, abs_tol=1e-6
    ):
        raise ScanfiEstateError("fragment and block areas do not conserve")
    block_province = scan.province_index[representative]
    block_curve_index = scan.curve_index[representative]
    block_age_class = scan.age_class[representative]
    block_track = np.full(block_count, -1, dtype=np.int64)
    natural_by_pair = np.full(province_count * curve_count, -1, dtype=np.int64)
    pair_code = block_province * curve_count + block_curve_index
    for index, pair in enumerate(np.unique(pair_code)):
        natural_by_pair[pair] = 2 * index + 1

    fragments = pd.DataFrame(
        {
            "fragment_id": np.arange(1, fragment_count + 1, dtype=np.int64),
            "block_id": block_ids.astype(np.int64),
            "area_ha": fragment_area,
            "age": scan.age.astype(np.int64),
            "track_id": natural_by_pair[scan.province_index * curve_count + scan.curve_index],
        }
    )
    block_track = natural_by_pair[pair_code]
    blocks = pd.DataFrame(
        {
            "block_id": np.arange(1, block_count + 1, dtype=np.int64),
            "track_id": block_track.astype(np.int64),
            "offset": block_age_class.astype(np.int64),
            "area_ha": block_area,
        }
    )
    if (fragments["track_id"] < 0).any() or (blocks["track_id"] < 0).any():
        raise ScanfiEstateError("a fragment or block has no natural track")
    group_fraction_sum = np.bincount(
        block_ids,
        weights=fragment_area * scan.group_fraction,
        minlength=block_count + 1,
    )[1:]
    block_group_fraction = group_fraction_sum / block_area

    block_sizes = np.bincount(block_ids, minlength=block_count + 1)[1:]
    size_bins = ((1, 1), (2, 4), (5, 9), (10, 19), (20, 49), (50, 99), (100, None))
    histogram = []
    for lower, upper in size_bins:
        upper_value = int(block_sizes.max()) if upper is None else upper
        histogram.append(
            {
                "min_fragments": lower,
                "max_fragments": upper,
                "blocks": int(
                    np.count_nonzero(
                        (block_sizes >= lower) & (block_sizes <= upper_value)
                    )
                ),
            }
        )
    clique_summary = {
        "count": int(block_count),
        "fragments_mean": float(block_sizes.mean()),
        "fragments_median": float(np.median(block_sizes)),
        "fragments_p90": float(np.percentile(block_sizes, 90)),
        "fragments_p99": float(np.percentile(block_sizes, 99)),
        "fragments_max": int(block_sizes.max()),
        "area_ha_mean": float(block_area.mean()),
        "area_ha_median": float(np.median(block_area)),
        "area_ha_max": float(block_area.max()),
        "singleton_blocks": int(np.count_nonzero(block_sizes == 1)),
        "size_histogram": histogram,
    }
    diagnostics = {
        "fragments": fragment_count,
        "blocks": int(block_count),
        "fragment_area_ha": float(fragment_area.sum()),
        "block_area_ha": float(block_area.sum()),
        "area_conserved": True,
        "block_ids_contiguous": True,
    }
    group_labels = [
        f"province:{PROVINCE_ORDER[province]}|curve:{used_specs[curve].key}"
        for province, curve in zip(block_province, block_curve_index, strict=True)
    ]
    groups = pd.DataFrame(
        {
            "block_id": np.arange(1, block_count + 1, dtype=np.int64),
            "group": pd.Series(group_labels, dtype="string"),
        }
    )
    return BlockAssignment(
        fragments=fragments,
        blocks=blocks,
        groups=groups,
        block_province=block_province,
        block_curve_index=block_curve_index,
        block_group_fraction=block_group_fraction,
        clique_summary=clique_summary,
        diagnostics=diagnostics,
    )


def restrict_to_used_curves(
    scan: ForestScan, curves: NationalCurveRegistry
) -> tuple[ForestScan, tuple[CurveSpec, ...], np.ndarray]:
    """Drop resolved-but-unused curves and remap fragment curve indices."""
    used_indices = np.unique(scan.curve_index)
    used_specs = tuple(curves.specs[int(index)] for index in used_indices)
    remap = np.full(len(curves.specs), -1, dtype=np.int32)
    remap[used_indices] = np.arange(len(used_indices), dtype=np.int32)
    if np.any(remap[scan.curve_index] < 0):
        raise ScanfiEstateError("curve remapping lost a forest fragment")
    return replace(scan, curve_index=remap[scan.curve_index]), used_specs, used_indices


# ---------------------------------------------------------------------------
# WS4 tables.
# ---------------------------------------------------------------------------


def _track_name(
    province: str, *, ifm: str, origin: str, silv_state: str, curve_key: str
) -> str:
    return (
        f"AU({province})+IFM({ifm})+ORIGIN({origin})"
        f"+SILV_STATE({silv_state})+CURVE({curve_key})+treatment()"
    )


def build_ws4_tables(
    assignment: BlockAssignment,
    used_specs: Sequence[CurveSpec],
) -> dict[str, pd.DataFrame]:
    """Build the canonical 12 WS4 tables for the national estate."""
    blocks = assignment.blocks
    fragments = assignment.fragments
    curve_count = len(used_specs)
    if curve_count == 0:
        raise ScanfiEstateError("no curves are used by the national estate")

    pair_code = (
        assignment.block_province.astype(np.int64) * curve_count
        + assignment.block_curve_index.astype(np.int64)
    )
    unique_pairs = np.unique(pair_code)
    if len(unique_pairs) == 0:
        raise ScanfiEstateError("no province-curve pairs are used")
    natural_by_pair = np.full(len(PROVINCE_ORDER) * curve_count, -1, dtype=np.int64)
    pair_rows: list[tuple[int, int, int]] = []
    for index, pair in enumerate(unique_pairs):
        province_index = int(pair // curve_count)
        curve_index = int(pair % curve_count)
        natural = 2 * index + 1
        natural_by_pair[pair] = natural
        pair_rows.append((province_index, curve_index, natural))

    # Fragment natural tracks are exactly the block pair tracks.
    fragment_block_index = fragments["block_id"].to_numpy(dtype=np.int64) - 1
    fragment_track = natural_by_pair[
        assignment.block_province[fragment_block_index] * curve_count
        + assignment.block_curve_index[fragment_block_index]
    ]
    if np.any(fragment_track < 0):
        raise ScanfiEstateError("fragment track ids are not resolved")
    if not np.array_equal(fragment_track, fragments["track_id"].to_numpy()):
        raise ScanfiEstateError("fragment and block natural track ids disagree")
    block_natural = natural_by_pair[pair_code]
    if not np.array_equal(block_natural, blocks["track_id"].to_numpy()):
        raise ScanfiEstateError("block natural track ids disagree with pairing")

    curve_rows: list[dict[str, Any]] = [
        {"curve_id": AREA_CURVE_ID, "x": float(x), "y": 1.0}
        for x in CURVE_DOMAIN
    ]
    for index, spec in enumerate(used_specs):
        curve_id = index + 2
        for x, y in zip(CURVE_DOMAIN, spec.curve_values(), strict=True):
            curve_rows.append({"curve_id": curve_id, "x": float(x), "y": float(y)})

    track_rows: list[dict[str, Any]] = []
    strata_rows: list[dict[str, Any]] = []
    treatment_rows: list[dict[str, Any]] = []
    feature_rows: list[dict[str, Any]] = []
    product_rows: list[dict[str, Any]] = []
    for province_index, curve_index, natural_track in pair_rows:
        province = PROVINCE_ORDER[province_index]
        spec = used_specs[curve_index]
        managed_track = natural_track + 1
        curve_id = curve_index + 2
        track_rows.append(
            {
                "track_id": natural_track,
                "name": _track_name(
                    province,
                    ifm="unmanaged",
                    origin="natural",
                    silv_state="baseline",
                    curve_key=spec.key,
                ),
            }
        )
        track_rows.append(
            {
                "track_id": managed_track,
                "name": _track_name(
                    province,
                    ifm="managed",
                    origin="planted",
                    silv_state="cc_pl",
                    curve_key=spec.key,
                ),
            }
        )
        for track in (natural_track, managed_track):
            strata_rows.append(
                {"track_id": track, "offset": 0, "next_age": 300, "cycle": 0}
            )
        treatment_rows.append(
            {
                "track_id": natural_track,
                "begin": TREATMENT_BEGIN,
                "end": TREATMENT_END,
                "treatment": TREATMENT,
                "new_track": managed_track,
                "offset": 0,
                "retain": 0,
            }
        )
        treatment_rows.append(
            {
                "track_id": managed_track,
                "begin": TREATMENT_BEGIN,
                "end": TREATMENT_END,
                "treatment": TREATMENT,
                "new_track": managed_track,
                "offset": 0,
                "retain": 0,
            }
        )
        feature_rows.append(
            {
                "track_id": natural_track,
                "label": f"feature.Yield.{province}.unmanaged.Total",
                "curve_id": curve_id,
            }
        )
        feature_rows.append(
            {
                "track_id": managed_track,
                "label": f"feature.Yield.{province}.managed.Total",
                "curve_id": curve_id,
            }
        )
        for track, ifm in ((natural_track, "unmanaged"), (managed_track, "managed")):
            product_rows.append(
                {
                    "track_id": track,
                    "treatment": TREATMENT,
                    "label": f"product.HarvestedVolume.{province}.{ifm}.CC",
                    "curve_id": curve_id,
                }
            )
            product_rows.append(
                {
                    "track_id": track,
                    "treatment": TREATMENT,
                    "label": f"product.Treated.{province}.{ifm}.CC",
                    "curve_id": AREA_CURVE_ID,
                }
            )

    tracks = pd.DataFrame(track_rows, columns=["track_id", "name"]).astype(
        {"track_id": "int64", "name": "string"}
    )
    strata = pd.DataFrame(
        strata_rows, columns=["track_id", "offset", "next_age", "cycle"]
    ).astype("int64")
    treatments = pd.DataFrame(
        treatment_rows,
        columns=[
            "track_id",
            "begin",
            "end",
            "treatment",
            "new_track",
            "offset",
            "retain",
        ],
    ).astype(
        {
            "track_id": "int64",
            "begin": "int64",
            "end": "int64",
            "treatment": "string",
            "new_track": "int64",
            "offset": "int64",
            "retain": "int64",
        }
    )
    curves = pd.DataFrame(curve_rows, columns=["curve_id", "x", "y"]).astype(
        {"curve_id": "int64", "x": "float64", "y": "float64"}
    )
    features = pd.DataFrame(
        feature_rows, columns=["track_id", "label", "curve_id"]
    ).astype({"track_id": "int64", "label": "string", "curve_id": "int64"})
    products = pd.DataFrame(
        product_rows, columns=["track_id", "treatment", "label", "curve_id"]
    ).astype(
        {
            "track_id": "int64",
            "treatment": "string",
            "label": "string",
            "curve_id": "int64",
        }
    )
    groups = assignment.groups.astype({"block_id": "int64", "group": "string"})
    account_labels = sorted(
        set(features["label"].astype(str)) | set(products["label"].astype(str))
    )
    accounts = pd.DataFrame(
        {
            "group": ["_ALL_"] * len(account_labels),
            "attribute": account_labels,
            "account": account_labels,
            "sum": [1.0] * len(account_labels),
        }
    ).astype(
        {
            "group": "string",
            "attribute": "string",
            "account": "string",
            "sum": "float64",
        }
    )
    frames = {
        "fragments": fragments,
        "blocks": blocks,
        "tracks": tracks,
        "strata": strata,
        "treatments": treatments,
        "curves": curves,
        "features": features,
        "products": products,
        "protoaccounts": accounts.copy(),
        "accounts": accounts.copy(),
        "groups": groups,
        "messages": pd.DataFrame(
            {
                "severity": ["info"],
                "message": [
                    (
                        f"compiled {len(fragments)} SCANFI 990-m forest fragments "
                        f"into {len(blocks)} 8-neighbour blocks across "
                        f"{len(unique_pairs)} province-curve pairs and "
                        f"{curve_count} RSYC group curves; "
                        "managed tracks share the natural RSYC curves "
                        "(no planting response)"
                    )
                ],
            }
        ).astype({"severity": "string", "message": "string"}),
    }
    _validate_ws4_frames(frames)
    return frames


def _validate_ws4_frames(frames: Mapping[str, pd.DataFrame]) -> None:
    """Fail on any cross-table reference or curve-domain defect."""
    tracks = frames["tracks"]
    track_ids = set(tracks["track_id"])
    for name in ("blocks", "strata", "treatments", "features", "products"):
        unknown = set(frames[name]["track_id"]) - track_ids
        if unknown:
            raise ScanfiEstateError(f"{name}: unknown track ids {sorted(unknown)[:5]}")
    unknown_new = set(frames["treatments"]["new_track"]) - track_ids
    if unknown_new:
        raise ScanfiEstateError(
            f"treatments: unknown new_track ids {sorted(unknown_new)[:5]}"
        )
    unknown_blocks = set(frames["fragments"]["block_id"]) - set(
        frames["blocks"]["block_id"]
    )
    if unknown_blocks:
        raise ScanfiEstateError(
            f"fragments: unknown block ids {sorted(unknown_blocks)[:5]}"
        )
    curve_ids = set(frames["curves"]["curve_id"])
    for name in ("features", "products"):
        unknown = set(frames[name]["curve_id"]) - curve_ids
        if unknown:
            raise ScanfiEstateError(f"{name}: unknown curve ids {sorted(unknown)[:5]}")
    for curve_id, group in frames["curves"].groupby("curve_id", sort=True):
        if not group["x"].is_monotonic_increasing or group["x"].duplicated().any():
            raise ScanfiEstateError(f"curve {curve_id}: x is not strictly increasing")
        if len(group) != len(CURVE_DOMAIN):
            raise ScanfiEstateError(f"curve {curve_id}: incomplete x domain")
    if (frames["blocks"]["area_ha"] < 0).any():
        raise ScanfiEstateError("blocks: negative area")
    if (frames["fragments"]["area_ha"] < 0).any():
        raise ScanfiEstateError("fragments: negative area")


# ---------------------------------------------------------------------------
# Schedule, manifest, and inventory artifacts.
# ---------------------------------------------------------------------------


def build_schedule(
    blocks: pd.DataFrame,
    block_province: np.ndarray,
    *,
    sample_fraction: float = 0.02,
    max_blocks: int = 80_000,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Select a deterministic province-stratified sample of eligible blocks."""
    if not 0.0 < sample_fraction <= 1.0:
        raise ScanfiEstateError("sample_fraction must lie in (0, 1]")
    if max_blocks <= 0:
        raise ScanfiEstateError("max_blocks must be positive")
    eligible_mask = blocks["offset"].to_numpy() >= TREATMENT_BEGIN
    eligible_indices = np.flatnonzero(eligible_mask)
    if eligible_indices.size == 0:
        raise ScanfiEstateError("no blocks are eligible for the CC treatment")
    selected: list[int] = []
    per_province_target: list[tuple[int, int]] = []
    province_values = block_province[eligible_indices]
    for province_index in range(len(PROVINCE_ORDER)):
        province_indices = eligible_indices[province_values == province_index]
        if province_indices.size == 0:
            continue
        target = max(1, round(province_indices.size * sample_fraction))
        target = min(target, province_indices.size)
        per_province_target.append((province_index, target))
    planned = sum(target for _, target in per_province_target)
    if planned > max_blocks:
        scale = max_blocks / planned
        per_province_target = [
            (province_index, max(1, round(target * scale)))
            for province_index, target in per_province_target
        ]
        planned = sum(target for _, target in per_province_target)
        if planned > max_blocks:
            # Trim the largest strata deterministically until the cap is met.
            while planned > max_blocks:
                province_index, target = max(
                    per_province_target, key=lambda item: item[1]
                )
                if target <= 1:
                    break
                per_province_target = [
                    (index, value - 1 if index == province_index else value)
                    for index, value in per_province_target
                ]
                planned -= 1
    for province_index, target in per_province_target:
        province_indices = eligible_indices[province_values == province_index]
        stride = province_indices.size / target
        positions = np.floor(np.arange(target) * stride).astype(np.int64)
        positions = positions[positions < province_indices.size]
        selected.extend(int(value) for value in province_indices[positions])
    selected_indices = np.array(selected, dtype=np.int64)
    schedule = pd.DataFrame(
        {
            "block_id": blocks["block_id"].to_numpy(dtype=np.int64)[selected_indices],
            "treatment": [TREATMENT] * len(selected_indices),
            "offset": [0] * len(selected_indices),
        }
    ).sort_values("block_id", kind="stable", ignore_index=True)
    if schedule.empty:
        raise ScanfiEstateError("schedule sample is empty")
    if not (
        blocks["offset"].to_numpy()[selected_indices] >= TREATMENT_BEGIN
    ).all():
        raise ScanfiEstateError("schedule selected an ineligible block")
    scheduled = blocks.merge(schedule[["block_id"]], on="block_id", how="inner")
    selected_provinces = pd.Series(
        [PROVINCE_ORDER[block_province[index]] for index in selected_indices],
        dtype="string",
    )
    counts = selected_provinces.value_counts().sort_index()
    stats = {
        "eligible_blocks": int(eligible_indices.size),
        "eligible_area_ha": float(blocks.loc[eligible_mask, "area_ha"].sum()),
        "rows": len(schedule),
        "scheduled_area_ha": float(scheduled["area_ha"].sum()),
        "effective_fraction": float(len(schedule) / eligible_indices.size),
        "max_blocks": max_blocks,
        "by_province": {str(key): int(value) for key, value in counts.items()},
    }
    return schedule, stats


def _source_records(paths: Iterable[Path]) -> list[dict[str, str]]:
    records = []
    for path in paths:
        if not path.is_file():
            raise ScanfiEstateError(f"source file is missing: {path}")
        records.append({"path": _repo_path(path), "sha256": sha256_file(path)})
    return records


def write_manifest(
    dataset_dir: Path,
    *,
    grid: BlockGrid,
    node_grid: NodeGrid,
    curves: NationalCurveRegistry,
    used_specs: Sequence[CurveSpec],
    scan: ForestScan,
    assignment: BlockAssignment,
    frames: Mapping[str, pd.DataFrame],
    source_paths: Sequence[Path],
    schedule_stats: Mapping[str, Any],
) -> dict[str, Any]:
    """Write the dataset manifest with resolution and provenance metadata."""
    curve_specs = [
        {
            "curve_id": index + 2,
            "curve_key": spec.key,
            "model_id": spec.model_id,
            "species": spec.species,
            "species_kind": spec.species_kind,
            "selected_level": spec.selected_level,
            "source_species": spec.source_species,
            "b1": spec.b1,
            "b2": spec.b2,
            "b3": spec.b3,
            "b4": spec.b4,
        }
        for index, spec in enumerate(used_specs)
    ]
    manifest = {
        "schema_version": "0.1",
        "tables": {name: f"{name}.parquet" for name in _table_schema()},
        "sources": _source_records(source_paths),
        "options": {
            "period_length": 1,
            "recipe": "SCANFI_RSYC_national_990m",
            "model": "II",
            "inventory_resolution": INVENTORY_RESOLUTION_LABEL,
            "ws4_fragment_resolution": FRAGMENT_RESOLUTION_LABEL,
            "inventory_resolution_m": PIXEL_SIZE_M,
            "ws4_fragment_resolution_m": WS4_FRAGMENT_RESOLUTION_M,
            "fragment_pixels": BLOCK_PIXELS,
            "fragment_area_ha_max": WS4_FRAGMENT_AREA_HA,
            "forest_landcover_classes": list(FOREST_LANDCOVER),
            "age_class_width_years": AGE_CLASS_WIDTH,
            "connectivity": CONNECTIVITY,
            "curve_domain": [int(CURVE_DOMAIN[0]), int(CURVE_DOMAIN[-1])],
            "curve_basis": "RSYC tile coniferous/broadleaf group; generic fallback",
            "managed_curve_same_as_natural": True,
            "planting_yield_response": "none: no data in this build",
            "clique_policy": (
                "connected (age_class, curve_key, province); 50-km subcells "
                "do not clip cliques"
            ),
        },
        "metadata": {
            **LABELS,
            "estate": "scanfi-national-990m",
            "scope": "national_estate",
            "coverage": "Canada within the RSYC tile-province allocation grid",
            "national_scope": True,
            "national_claim": False,
            "resolution_change": (
                "SCANFI inventory is 30 m; WS4 fragments are 990 m "
                "(33 x 33 pixels). The 50-km nodes are inventory windows."
            ),
            "inventory_resolution": INVENTORY_RESOLUTION_LABEL,
            "ws4_fragment_resolution": FRAGMENT_RESOLUTION_LABEL,
            "resource_unit": "990-m block cell",
            "area_basis": "forest pixel count x 0.09 ha inside each 990-m cell",
            "fragments": len(frames["fragments"]),
            "blocks": len(frames["blocks"]),
            "tracks": len(frames["tracks"]),
            "curves": len(used_specs),
            "forest_area_ha": assignment.diagnostics["fragment_area_ha"],
            "forest_area_km2": assignment.diagnostics["fragment_area_ha"] / 100.0,
            "provinces": sorted(
                {PROVINCE_ORDER[index] for index in assignment.block_province}
            ),
            "curve_keys": [spec.key for spec in used_specs],
            "curve_specs": curve_specs,
            "curve_diagnostics": curves.diagnostics,
            "node_grid_diagnostics": node_grid.diagnostics,
            "scan_totals": scan.totals,
            "schedule_sample": schedule_stats,
            "grid": {
                "block_rows": grid.block_rows,
                "block_cols": grid.block_cols,
                "raster_width": grid.raster_width,
                "raster_height": grid.raster_height,
                "block_transform": list(grid.transform)[:6],
                "crs": str(grid.crs),
            },
            "ws4": ws4_provenance(),
            "built_utc": _utc_now(),
        },
    }
    dataset_dir.joinpath("manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return manifest


def write_inventory_artifacts(
    out_dir: Path,
    *,
    grid: BlockGrid,
    node_grid: NodeGrid,
    scan: ForestScan,
    assignment: BlockAssignment,
    used_specs: Sequence[CurveSpec],
) -> dict[str, str]:
    """Write auditable per-cell inventory and node lookup tables."""
    inventory_dir = out_dir / "inventory"
    inventory_dir.mkdir(parents=True, exist_ok=True)
    node_indices = scan.node_index.astype(np.int64)
    cells = pd.DataFrame(
        {
            "fragment_id": np.arange(1, len(scan.cell_rows) + 1, dtype=np.int64),
            "block_id": assignment.fragments["block_id"].to_numpy(),
            "cell_row": scan.cell_rows.astype(np.int64),
            "cell_col": scan.cell_cols.astype(np.int64),
            "node_index": node_indices,
            "node_id": pd.Categorical(node_grid.node_ids[node_indices]),
            "tile_id": pd.Categorical(node_grid.node_tile_ids[node_indices]),
            "province": pd.Categorical(
                node_grid.node_provinces[node_indices]
            ),
            "curve_key": pd.Categorical(
                [used_specs[index].key for index in scan.curve_index]
            ),
            "landcover_group": pd.Categorical(scan.landcover_group),
            "group_fraction": scan.group_fraction.astype(np.float64),
            "forest_pixels": scan.forest_pixels.astype(np.int64),
            "conifer_pixels": scan.conifer_pixels.astype(np.int64),
            "broadleaf_pixels": scan.broadleaf_pixels.astype(np.int64),
            "mixed_pixels": scan.mixed_pixels.astype(np.int64),
            "area_ha": assignment.fragments["area_ha"].to_numpy(),
            "age": assignment.fragments["age"].to_numpy(),
            "age_class": scan.age_class.astype(np.int64),
        }
    )
    cells_path = inventory_dir / "forest_cells_990m.parquet"
    cells.to_parquet(cells_path, index=False)
    node_lookup = pd.DataFrame(
        {
            "node_index": np.arange(len(node_grid.node_ids), dtype=np.int64),
            "node_id": node_grid.node_ids,
            "tile_id": node_grid.node_tile_ids,
            "province": node_grid.node_provinces,
            "row": node_grid.node_rows,
            "col": node_grid.node_cols,
            "subcell_geometry_area_ha": node_grid.node_geom_area_ha,
            "raster_area_ha": node_grid.node_raster_area_ha,
        }
    )
    node_path = inventory_dir / "node_lookup.parquet"
    node_lookup.to_parquet(node_path, index=False)
    curves = pd.DataFrame(
        [
            {
                "curve_id": index + 2,
                "curve_key": spec.key,
                "model_id": spec.model_id,
                "species": spec.species,
                "species_kind": spec.species_kind,
                "selected_level": spec.selected_level,
                "source_species": spec.source_species,
                "b1": spec.b1,
                "b2": spec.b2,
                "b3": spec.b3,
                "b4": spec.b4,
            }
            for index, spec in enumerate(used_specs)
        ]
    )
    curve_path = inventory_dir / "curves_used.csv"
    curves.to_csv(curve_path, index=False, lineterminator="\n")
    return {
        "cells": _repo_path(cells_path),
        "nodes": _repo_path(node_path),
        "curves": _repo_path(curve_path),
    }


def _distribution_rows(values: np.ndarray, area: np.ndarray) -> list[dict[str, Any]]:
    frame = pd.DataFrame({"value": values, "area_ha": area})
    grouped = (
        frame.groupby("value", sort=True)["area_ha"]
        .agg(["size", "sum"])
        .reset_index()
    )
    return [
        {
            "value": int(row.value),
            "fragments": int(row.size),
            "area_ha": float(row.sum),
        }
        for row in grouped.itertuples(index=False)
    ]


def build_summary(
    *,
    grid: BlockGrid,
    node_grid: NodeGrid,
    curves: NationalCurveRegistry,
    used_specs: Sequence[CurveSpec],
    scan: ForestScan,
    assignment: BlockAssignment,
    frames: Mapping[str, pd.DataFrame],
    schedule_stats: Mapping[str, Any],
    inventory_paths: Mapping[str, str],
) -> dict[str, Any]:
    """Assemble the national estate inventory summary."""
    fragments = frames["fragments"]
    blocks = frames["blocks"]
    block_province = assignment.block_province
    province_distribution = []
    for province_index, province in enumerate(PROVINCE_ORDER):
        fragment_mask = scan.province_index == province_index
        block_mask = block_province == province_index
        if not fragment_mask.any():
            continue
        province_distribution.append(
            {
                "province": province,
                "fragments": int(fragment_mask.sum()),
                "area_ha": float(fragments.loc[fragment_mask, "area_ha"].sum()),
                "blocks": int(block_mask.sum()),
                "eligible_blocks": int(
                    np.count_nonzero(
                        block_mask & (blocks["offset"].to_numpy() >= TREATMENT_BEGIN)
                    )
                ),
            }
        )
    curve_usage = []
    for curve_index, spec in enumerate(used_specs):
        fragment_mask = scan.curve_index == curve_index
        provinces = sorted(
            {
                PROVINCE_ORDER[index]
                for index in np.unique(scan.province_index[fragment_mask])
            }
        )
        curve_usage.append(
            {
                "curve_id": curve_index + 2,
                "curve_key": spec.key,
                "model_id": spec.model_id,
                "species": spec.species,
                "source_species": spec.source_species,
                "selected_level": spec.selected_level,
                "fragments": int(fragment_mask.sum()),
                "area_ha": float(fragments.loc[fragment_mask, "area_ha"].sum()),
                "provinces": provinces,
            }
        )
    group_distribution = []
    for group in ("CONIFEROUS", "BROADLEAF"):
        mask = scan.landcover_group == group
        group_distribution.append(
            {
                "landcover_group": group,
                "fragments": int(mask.sum()),
                "area_ha": float(fragments.loc[mask, "area_ha"].sum()),
                "group_fraction_mean": float(scan.group_fraction[mask].mean()),
                "group_fraction_median": float(np.median(scan.group_fraction[mask])),
            }
        )
    track_pair_count = len(np.unique(
        block_province.astype(np.int64) * len(used_specs)
        + assignment.block_curve_index.astype(np.int64)
    ))
    return {
        "estate": "scanfi-national-990m",
        "built_utc": _utc_now(),
        "resolution": {
            "inventory_resolution": INVENTORY_RESOLUTION_LABEL,
            "ws4_fragment_resolution": FRAGMENT_RESOLUTION_LABEL,
            "fragment_pixels": BLOCK_PIXELS,
            "fragment_area_ha_max": WS4_FRAGMENT_AREA_HA,
            "pixel_area_ha": PIXEL_AREA_HA,
        },
        "grid": {
            "block_rows": grid.block_rows,
            "block_cols": grid.block_cols,
            "block_cells": grid.block_rows * grid.block_cols,
            "raster_width": grid.raster_width,
            "raster_height": grid.raster_height,
            "crs": str(grid.crs),
        },
        "counts": {
            **scan.totals,
            **assignment.diagnostics,
            "blocks": len(blocks),
            "tracks": len(frames["tracks"]),
            "used_curves": len(used_specs),
            "resolved_curves": len(curves.specs),
            "province_curve_pairs": track_pair_count,
            "curves_table_rows": len(frames["curves"]),
        },
        "node_grid": dict(node_grid.diagnostics),
        "curve_diagnostics": dict(curves.diagnostics),
        "age_distribution": _distribution_rows(
            scan.age_class, fragments["area_ha"].to_numpy()
        ),
        "province_distribution": province_distribution,
        "curve_usage": curve_usage,
        "landcover_group_distribution": group_distribution,
        "clique_summary": dict(assignment.clique_summary),
        "schedule": dict(schedule_stats),
        "table_rows": {name: len(frame) for name, frame in frames.items()},
        "inventory_artifacts": dict(inventory_paths),
        "timings": dict(scan.timings),
        "labels": LABELS,
    }


# ---------------------------------------------------------------------------
# WS4 simulation.
# ---------------------------------------------------------------------------


def run_national_simulation(
    dataset_dir: Path, schedule_path: Path, run_dir: Path, *, years: int
) -> dict[str, Any]:
    """Run ws4 simulate and summarise product and account totals."""
    import subprocess

    if not WS4_BIN.is_file():
        raise ScanfiEstateError(f"ws4 executable is missing: {WS4_BIN}")
    run_dir.mkdir(parents=True, exist_ok=True)
    command = [
        str(WS4_BIN),
        "simulate",
        "--dataset",
        str(dataset_dir),
        "--schedule",
        str(schedule_path),
        "--years",
        str(years),
        "--out",
        str(run_dir),
    ]
    result = subprocess.run(
        command,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    payload: dict[str, Any] = {
        "command": command,
        "exit_code": int(result.returncode),
        "stdout": result.stdout,
        "stderr": result.stderr,
        "years": years,
    }
    if result.returncode != 0:
        raise ScanfiEstateError(
            "ws4 simulate failed:\n" + result.stdout + result.stderr
        )
    product_path = run_dir / "product_values.csv"
    account_path = run_dir / "account_values.csv"
    if not product_path.is_file() or not account_path.is_file():
        raise ScanfiEstateError("simulation did not write product/account values")
    products = pd.read_csv(product_path)
    product_totals = (
        products.groupby("account", as_index=False)["value"]
        .sum()
        .sort_values("account")
    )
    harvested = product_totals[
        product_totals["account"].str.startswith("product.HarvestedVolume")
    ]
    treated = product_totals[
        product_totals["account"].str.startswith("product.Treated")
    ]
    harvested_volume = float(harvested["value"].sum())
    treated_area = float(treated["value"].sum())
    if harvested_volume <= 0.0:
        raise ScanfiEstateError("simulation produced zero harvested volume")
    if treated_area <= 0.0:
        raise ScanfiEstateError("simulation produced zero treated/managed area")
    accounts = pd.read_csv(account_path)
    last_offset = int(accounts["offset"].max())
    final = accounts[accounts["offset"] == last_offset]
    final_features = (
        final[final["account"].str.startswith("feature.")]
        .groupby("account", as_index=False)["value"]
        .sum()
        .sort_values("account")
    )
    payload.update(
        {
            "product_totals": product_totals.to_dict(orient="records"),
            "harvested_volume_m3": harvested_volume,
            "treated_area_ha": treated_area,
            "final_offset": last_offset,
            "final_feature_totals": final_features.to_dict(orient="records"),
            "final_managed_yield_m3": float(
                final_features[
                    final_features["account"].str.contains(r"\.managed\.Total$")
                ]["value"].sum()
            ),
            "final_unmanaged_yield_m3": float(
                final_features[
                    final_features["account"].str.contains(r"\.unmanaged\.Total$")
                ]["value"].sum()
            ),
        }
    )
    return payload


# ---------------------------------------------------------------------------
# Orchestration.
# ---------------------------------------------------------------------------


def build_national_estate(
    *,
    out_dir: Path = NATIONAL_OUT_DIR,
    sample_fraction: float = 0.02,
    max_schedule_blocks: int = 80_000,
    years: int = 20,
    run_ws4: bool = False,
    chunk_block_rows: int = CHUNK_BLOCK_ROWS,
) -> dict[str, Any]:
    """Build, write, and optionally simulate the national 990-m WS4 estate."""
    assert_no_unexpected_deletions()
    started = time.perf_counter()
    print("loading national grid and inputs", flush=True)
    grid = load_block_grid()
    nodes = load_supply_nodes()
    tile_geometries, province_geometries = load_rsyc_geometries()
    print(
        f"grid {grid.block_rows} x {grid.block_cols} blocks "
        f"({grid.block_rows * grid.block_cols:,} cells)",
        flush=True,
    )
    print("resolving RSYC group curves", flush=True)
    library = pd.read_csv(RSYC_LIBRARY_CSV)
    curves = resolve_national_curves(library, nodes)
    print(
        f"resolved {len(curves.specs)} curves for {len(nodes)} nodes",
        flush=True,
    )
    print("rasterizing 50-km node identities at 990 m", flush=True)
    node_grid = build_node_grid(
        nodes,
        grid,
        tile_geometries=tile_geometries,
        province_geometries=province_geometries,
    )
    print(
        f"node grid: {node_grid.diagnostics['node_cells']:,} cells mapped",
        flush=True,
    )
    print("scanning SCANFI 30-m rasters in block-row strips", flush=True)
    scan = scan_forest_cells(
        grid, node_grid, curves, chunk_block_rows=chunk_block_rows
    )
    scan, used_specs, _used_indices = restrict_to_used_curves(scan, curves)
    print(
        f"scan complete: {len(scan.cell_rows):,} forest fragments, "
        f"{scan.totals['included_forest_area_ha']:,.2f} ha, "
        f"{len(used_specs)} used curves",
        flush=True,
    )
    print("labelling 8-neighbour blocks", flush=True)
    assignment = assign_blocks(scan, grid, used_specs)
    print(
        f"blocks: {assignment.diagnostics['blocks']:,} "
        f"(area {assignment.diagnostics['block_area_ha']:,.2f} ha)",
        flush=True,
    )
    print("building WS4 tables", flush=True)
    frames = build_ws4_tables(assignment, used_specs)
    schedule, schedule_stats = build_schedule(
        frames["blocks"],
        assignment.block_province,
        sample_fraction=sample_fraction,
        max_blocks=max_schedule_blocks,
    )

    print(f"writing dataset to {out_dir}", flush=True)
    _safe_reset_output(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_gitattributes(out_dir)
    dataset_dir = out_dir / "dataset"
    dataset_dir.mkdir(parents=True, exist_ok=True)
    for name, columns in _table_schema().items():
        frame = frames[name]
        if list(frame.columns) != columns:
            raise ScanfiEstateError(
                f"table {name!r} columns {list(frame.columns)} != {columns}"
            )
        frame.to_parquet(dataset_dir / f"{name}.parquet", index=False)
    schedules_dir = out_dir / "schedules"
    schedules_dir.mkdir(parents=True, exist_ok=True)
    schedule_path = schedules_dir / "national_cc_sample.csv"
    schedule.to_csv(schedule_path, index=False, lineterminator="\n")
    inventory_paths = write_inventory_artifacts(
        out_dir,
        grid=grid,
        node_grid=node_grid,
        scan=scan,
        assignment=assignment,
        used_specs=used_specs,
    )
    source_paths = [
        AGE_RASTER,
        LANDCOVER_RASTER,
        RSYC_LIBRARY_CSV,
        RSYC_TILES_GPKG,
        SUPPLY_NODES_CSV,
    ]
    manifest = write_manifest(
        dataset_dir,
        grid=grid,
        node_grid=node_grid,
        curves=curves,
        used_specs=used_specs,
        scan=scan,
        assignment=assignment,
        frames=frames,
        source_paths=source_paths,
        schedule_stats=schedule_stats,
    )
    summary = build_summary(
        grid=grid,
        node_grid=node_grid,
        curves=curves,
        used_specs=used_specs,
        scan=scan,
        assignment=assignment,
        frames=frames,
        schedule_stats=schedule_stats,
        inventory_paths=inventory_paths,
    )
    summary["manifest_sources"] = manifest["sources"]
    if run_ws4:
        print("running ws4 validate", flush=True)
        validation = run_ws4_validate(dataset_dir)
        summary["validation"] = validation
        print("running ws4 simulate", flush=True)
        run_dir = out_dir / "runs" / f"national_cc_{years}y"
        simulation = run_national_simulation(
            dataset_dir, schedule_path, run_dir, years=years
        )
        expected_area = float(schedule_stats["scheduled_area_ha"])
        if not math.isclose(
            float(simulation["treated_area_ha"]),
            expected_area,
            rel_tol=1e-9,
            abs_tol=1e-6,
        ):
            raise ScanfiEstateError(
                "simulated treated area does not equal scheduled block area: "
                f"{simulation['treated_area_ha']} != {expected_area}"
            )
        simulation["expected_scheduled_area_ha"] = expected_area
        simulation["transition_evidence"] = (
            "all scheduled natural-track fragments transitioned to the managed "
            "track; product.Treated area equals scheduled block area"
        )
        summary["simulation"] = simulation
    summary["elapsed_seconds"] = time.perf_counter() - started
    summary["labels"] = LABELS
    out_dir.joinpath("inventory_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    write_readme(out_dir, summary, manifest)
    assert_no_unexpected_deletions()
    return summary


def write_readme(
    out_dir: Path, summary: Mapping[str, Any], manifest: Mapping[str, Any]
) -> None:
    counts = summary["counts"]
    schedule = summary["schedule"]
    lines = [
        "# WS4 national forest estate — SCANFI 30 m to 990 m fragments",
        "",
        "This directory is a compiled national WS4 forest-estate dataset built",
        "from SCANFI 30-m age/landcover pixels and RSYC tile group curves. It is",
        "`model_estimate` / `sensitivity_only`; it is not official OSeCAN, not a",
        "policy-grade supply forecast, and does not change chain v5.1 or the UI.",
        "",
        "## Resolution change",
        "",
        "- `inventory_resolution=30m`: SCANFI age and landcover are read at 30 m.",
        "- `ws4_fragment_resolution=990m`: one WS4 fragment per forest 33 x 33",
        "  pixel block (maximum 98.01 ha). The 50-km supply nodes are inventory",
        "  windows, not fragments.",
        "",
        "## Counts",
        "",
        f"- Forest fragments: {counts['fragments']:,}",
        f"- Blocks: {counts['blocks']:,}",
        f"- Forest area: {counts['fragment_area_ha']:,.2f} ha",
        f"- Tracks: {counts['tracks']:,}",
        f"- Used curves: {counts['used_curves']:,}",
        f"- Schedule rows: {schedule['rows']:,}",
        f"- Scheduled area: {schedule['scheduled_area_ha']:,.2f} ha",
        "",
        "## WS4",
        "",
        f"- Executable: `{manifest['metadata']['ws4']['executable']}`",
        f"- Version: `{manifest['metadata']['ws4']['version']}`",
        "",
        "See `inventory_summary.json` for age/province/curve distributions,",
        "clique sizes, exclusions, validation output, and simulation totals.",
        "",
    ]
    if "simulation" in summary:
        simulation = summary["simulation"]
        lines.extend(
            [
                "## Simulation",
                "",
                f"- Harvested volume: {simulation['harvested_volume_m3']:,.3f} m3",
                f"- Treated/managed area: {simulation['treated_area_ha']:,.3f} ha",
                f"- Final managed yield: {simulation['final_managed_yield_m3']:,.3f} m3",
                "",
            ]
        )
    out_dir.joinpath("README.md").write_text("\n".join(lines), encoding="utf-8")


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=NATIONAL_OUT_DIR)
    parser.add_argument("--sample-fraction", type=float, default=0.02)
    parser.add_argument("--max-schedule-blocks", type=int, default=80_000)
    parser.add_argument("--years", type=int, default=20)
    parser.add_argument("--chunk-block-rows", type=int, default=CHUNK_BLOCK_ROWS)
    parser.add_argument("--run-ws4", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        summary = build_national_estate(
            out_dir=args.out.resolve(),
            sample_fraction=args.sample_fraction,
            max_schedule_blocks=args.max_schedule_blocks,
            years=args.years,
            run_ws4=args.run_ws4,
            chunk_block_rows=args.chunk_block_rows,
        )
    except ScanfiEstateError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(json.dumps(summary["counts"], indent=2, sort_keys=True))
    print(f"dataset: {args.out.resolve() / 'dataset'}")
    print(f"schedule rows: {summary['schedule']['rows']}")
    if "simulation" in summary:
        simulation = summary["simulation"]
        print(f"harvested volume m3: {simulation['harvested_volume_m3']}")
        print(f"treated area ha: {simulation['treated_area_ha']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
