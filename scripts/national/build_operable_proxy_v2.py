#!/usr/bin/env python3
"""Build operable landbase v2: age, fire-disturbance, and road-access screens.

v2 starts from the v1 operable merchantable-forest screening proxy (SCANFI
treed land cover x merchantability thresholds x managed share) and adds three
independent screens:

* SCANFI median stand age >= 20 and >= 30 years (sensitivity cases).
* NBAC 2020 burned area, excluded where the 2020 annual fire polygons burn a
  merchantable pixel.
* NRN nearest-road distance classes (<= 5, <= 10, <= 20 km) and a 20 km
  access screen.

The product is still a screening proxy.  It is not a legal timber harvesting
landbase (THLB), not an AAC landbase, and not an operability model.  There is
no national DEM in this workspace, so no slope filter is applied.  The NBAC
bundle is the 2020 annual vintage only: it does not contain 2010 or 2015 fire
history, so those sensitivity cases are recorded as a gap rather than
fabricated.

Method
------
The 30 m SCANFI rasters are read in 33-block strips.  Each strip is reduced to
exact 990 m block counts for the base, age, and burn masks.  NBAC polygons are
rasterised per strip at 30 m with the pixel-centre rule, so the burn mask
conserves pixels exactly.  NRN road segments are rasterised at 990 m with
all-touched and a Euclidean distance transform produces nearest-road distance
at block centres.  Road and burn masks are block attributes; age is exact at
30 m.  Managed share is applied to each screen exactly as in v1.

Run:
    .venv/bin/python scripts/national/build_operable_proxy_v2.py \
        --out-dir data/derived/national/managed_landbase/operable_proxy_v2
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
import zipfile
from datetime import UTC, datetime
from multiprocessing import Pool
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
import pyogrio
import rasterio
import shapely
from rasterio.features import rasterize
from rasterio.windows import (
    Window,
)
from rasterio.windows import (
    bounds as window_bounds,
)
from rasterio.windows import (
    transform as window_transform,
)
from scipy.ndimage import distance_transform_edt

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_operable_proxy as v1

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCANFI_DIR = REPO_ROOT / "data/raw/scanfi/v2"
DEFAULT_RSYC = REPO_ROOT / "data/raw/national/rsyc/RSYC_ecostrat.gpkg"
DEFAULT_PROVINCES = (
    REPO_ROOT / "data/raw/boundaries/canada_adm1_geoboundaries_simplified.geojson"
)
DEFAULT_MANAGED_DIR = REPO_ROOT / "data/derived/national/managed_landbase"
DEFAULT_V1_DIR = DEFAULT_MANAGED_DIR / "operable_proxy"
DEFAULT_AREA_WEIGHTS = (
    REPO_ROOT
    / "data/derived/national/assortments/national_area_weights/national_area_weights_ecodistrict.csv"
)
DEFAULT_NBAC = REPO_ROOT / "data/raw/nrcan/nbac/NBAC_2020_20260513.zip"
DEFAULT_NRN_DIR = REPO_ROOT / "data/raw/nrn"
DEFAULT_NFI = REPO_ROOT / "data/raw/nfi/standard_reports/t1/ca_t4_for_area_en.csv"
DEFAULT_SCANFI_MANIFEST = REPO_ROOT / "data/manifests/scanfi-v2-2025-acquisition.csv"
DEFAULT_OUT = DEFAULT_MANAGED_DIR / "operable_proxy_v2"

PIXEL_HA = v1.PIXEL_HA
BLOCK_M = v1.BLOCK_M
STRIP_BLOCKS = v1.STRIP_BLOCKS
FOREST_CLASSES = v1.FOREST_CLASSES
ALL_PROVINCES = v1.ALL_PROVINCES
PROVINCE_CODE = v1.PROVINCE_CODE

SCANFI_PATHS = {
    **v1.SCANFI_PATHS,
    "age": "SCANFI_age_median_2025_v2_20260119.tif",
}

AGE_MINIMUM_CASES = (20, 30)
ROAD_BREAKS_KM = (5, 10, 20)
TARGET_VOLUME_M3 = 150_000_000.0
BASE_PATH_VOLUME_M3 = 118_977_339.0
ODT_PER_M3 = 0.08831776452936206

V2_CASES = (
    "treed",
    "base",
    "age_ge_20",
    "age_ge_30",
    "age_lt_20",
    "age_lt_30",
    "age_missing",
    "burn_base",
    "burn_age20",
    "burn_age30",
)
V2_CASE_INDEX = {name: index for index, name in enumerate(V2_CASES)}

CASE_DEFS: tuple[dict[str, Any], ...] = (
    {
        "case": "base",
        "label": "base merchantable, no v2 screen",
        "area_key": "base",
        "age_min_years": None,
        "disturbance_screen": "none",
        "road_max_km": None,
    },
    {
        "case": "age_ge_20",
        "label": "base and SCANFI median age >= 20 years",
        "area_key": "age_ge_20",
        "age_min_years": 20,
        "disturbance_screen": "none",
        "road_max_km": None,
    },
    {
        "case": "age_ge_30",
        "label": "base and SCANFI median age >= 30 years",
        "area_key": "age_ge_30",
        "age_min_years": 30,
        "disturbance_screen": "none",
        "road_max_km": None,
    },
    {
        "case": "disturb_2020_excluded",
        "label": "base excluding NBAC 2020 burned pixels",
        "area_key": "disturb_2020_excluded",
        "age_min_years": None,
        "disturbance_screen": "nbac_2020_excluded",
        "road_max_km": None,
    },
    {
        "case": "road_le_5km",
        "label": "base within 5 km of an NRN road",
        "area_key": "road_le_5km",
        "age_min_years": None,
        "disturbance_screen": "none",
        "road_max_km": 5,
    },
    {
        "case": "road_le_10km",
        "label": "base within 10 km of an NRN road",
        "area_key": "road_le_10km",
        "age_min_years": None,
        "disturbance_screen": "none",
        "road_max_km": 10,
    },
    {
        "case": "road_le_20km",
        "label": "base within 20 km of an NRN road",
        "area_key": "road_le_20km",
        "age_min_years": None,
        "disturbance_screen": "none",
        "road_max_km": 20,
    },
    {
        "case": "screen_age20_disturb",
        "label": "age >= 20 and not NBAC 2020 burned",
        "area_key": "screen_age20_disturb",
        "age_min_years": 20,
        "disturbance_screen": "nbac_2020_excluded",
        "road_max_km": None,
    },
    {
        "case": "screen_age30_disturb",
        "label": "age >= 30 and not NBAC 2020 burned",
        "area_key": "screen_age30_disturb",
        "age_min_years": 30,
        "disturbance_screen": "nbac_2020_excluded",
        "road_max_km": None,
    },
    {
        "case": "screen_core20",
        "label": "age >= 20, not NBAC 2020 burned, within 20 km of road",
        "area_key": "screen_core20",
        "age_min_years": 20,
        "disturbance_screen": "nbac_2020_excluded",
        "road_max_km": 20,
    },
    {
        "case": "screen_core30",
        "label": "age >= 30, not NBAC 2020 burned, within 10 km of road",
        "area_key": "screen_core30",
        "age_min_years": 30,
        "disturbance_screen": "nbac_2020_excluded",
        "road_max_km": 10,
    },
)
CASE_ORDER = {definition["case"]: index for index, definition in enumerate(CASE_DEFS)}

GAPS: tuple[dict[str, str], ...] = (
    {
        "gap_id": "legal_thlb_missing",
        "scope": "all",
        "status": "open",
        "detail": (
            "No legal timber harvesting landbase boundary is used. Tenure is a "
            "rights footprint, not a harvest-landbase determination."
        ),
        "effect": "All areas are screening proxies and must not be labelled THLB or AAC.",
    },
    {
        "gap_id": "slope_dem_missing",
        "scope": "all",
        "status": "open",
        "detail": (
            "No national DEM is present in the workspace, so no slope, elevation, "
            "operability, or site-degradation filter is applied."
        ),
        "effect": "Steep and inoperable terrain remains in every v2 area.",
    },
    {
        "gap_id": "nbac_2010_2015_missing",
        "scope": "all",
        "status": "open",
        "detail": (
            "The NBAC bundle is the 2020 annual vintage (YEAR=2020 for all 710 "
            "polygons). It contains no 2010 or 2015 fire history, so those "
            "sensitivity screens cannot be computed from this input."
        ),
        "effect": (
            "Only a 2020 fire screen is reported; the 2010 and 2015 cases are "
            "explicit gaps, not zero-disturbance assumptions."
        ),
    },
    {
        "gap_id": "nbac_annual_not_cumulative",
        "scope": "all",
        "status": "documented",
        "detail": (
            "NBAC polygons are annual fire perimeters with YEAR/NFIREID and "
            "polygon attributes. They are not a cumulative severity or burn-class "
            "stack, so no cumulative class is interpreted."
        ),
        "effect": "The disturbance screen is an annual 2020 burned/not-burned mask.",
    },
    {
        "gap_id": "nrn_public_roads_only",
        "scope": "all",
        "status": "documented",
        "detail": (
            "All 13 NRN province bundles were materialised and used. NRN is the "
            "public road network; resource, industrial, winter, and decommissioned "
            "roads are not represented, and road condition/seasonality is absent."
        ),
        "effect": "Road distance is an optimistic public-road access proxy.",
    },
    {
        "gap_id": "age_regeneration_assumption",
        "scope": "all",
        "status": "documented",
        "detail": (
            "SCANFI median age is a stand-age prediction. Excluding stands below "
            "20 or 30 years assumes they are not merchantable at the screen date, "
            "which is stricter than the v1 height/biomass/closure thresholds and "
            "ignores partial harvest, thinning, and regeneration delay."
        ),
        "effect": "Age screens reduce area beyond merchantability alone.",
    },
    {
        "gap_id": "managed_share_uniform",
        "scope": "all",
        "status": "documented",
        "detail": (
            "The v1 managed share is uniform inside each province-ecoprovince. "
            "Tenure was not re-intersected with the age, burn, or road rasters."
        ),
        "effect": "Spatial tenure pattern is not represented.",
    },
    {
        "gap_id": "nfi_age_uncertainty",
        "scope": "all",
        "status": "documented",
        "detail": (
            "SCANFI age is a 30 m prediction surface, not a provincial inventory "
            "or a time-since-disturbance record."
        ),
        "effect": "Age thresholds are screening conventions, not statutory rules.",
    },
)

_DATASETS: dict[str, rasterio.DatasetReader] = {}
_GRID: dict[str, Any] = {}
_BURN_GEOMETRIES: list[Any] = []


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_nbac_geometries(path: Path, grid_crs) -> tuple[list[Any], dict[str, Any]]:
    """Read the NBAC annual fire polygons and reproject them to the block grid."""
    if not path.exists():
        raise FileNotFoundError(f"required NBAC input missing: {path}")
    with zipfile.ZipFile(path) as archive:
        shape_members = [name for name in archive.namelist() if name.endswith(".shp")]
        if len(shape_members) != 1:
            raise ValueError(
                f"expected exactly one NBAC shapefile in {path}, found {shape_members}"
            )
        shape_member = shape_members[0]
        extract_dir = REPO_ROOT / "tmp/nbac_v2"
        extract_dir.mkdir(parents=True, exist_ok=True)
        archive.extractall(extract_dir)
    shape_path = extract_dir / shape_member

    frame = gpd.read_file(shape_path)
    if frame.empty:
        raise ValueError(f"NBAC shapefile is empty: {shape_path}")
    if "YEAR" not in frame.columns:
        raise ValueError(
            "NBAC shapefile has no YEAR column; refusing to infer disturbance year"
        )
    years = sorted({int(value) for value in frame["YEAR"].dropna().unique()})
    if years != [2020]:
        raise ValueError(
            f"NBAC YEAR values {years} do not support a 2020-only annual screen; "
            "refusing to relabel a multi-year or cumulative layer"
        )
    if frame.geometry.isna().any():
        raise ValueError("NBAC contains null geometries")
    invalid = ~frame.geometry.is_valid
    if invalid.any():
        frame.loc[invalid, "geometry"] = frame.loc[invalid, "geometry"].apply(
            lambda geometry: shapely.make_valid(geometry)
        )
    frame = frame.to_crs(grid_crs)
    geometries = [geometry for geometry in frame.geometry if geometry is not None]
    if not geometries:
        raise ValueError("NBAC contains no usable geometries after reprojection")
    metadata = {
        "path": str(path.relative_to(REPO_ROOT)),
        "shapefile_member": shape_member,
        "features": len(frame),
        "years": years,
        "polygon_ha": float(frame["POLY_HA"].sum()) if "POLY_HA" in frame else None,
        "source_crs": str(frame.crs),
    }
    return geometries, metadata


def block_road_presence(
    nrn_dir: Path,
    grid: dict[str, Any],
) -> tuple[np.ndarray, list[dict[str, Any]]]:
    """Rasterise every NRN road segment onto the 990 m block grid."""
    province_roads = np.zeros(grid["shape"], dtype=np.uint8)
    layers: list[dict[str, Any]] = []
    zip_paths = sorted(nrn_dir.glob("nrn_*_gpkg.zip"))
    if len(zip_paths) != 13:
        raise ValueError(f"expected 13 NRN province bundles, found {len(zip_paths)}")

    for zip_path in zip_paths:
        province = zip_path.name.split("_")[1].upper()
        with zipfile.ZipFile(zip_path) as archive:
            english = [name for name in archive.namelist() if name.endswith("_en.gpkg")]
            if len(english) != 1:
                raise ValueError(
                    f"expected one English GeoPackage in {zip_path}, found {english}"
                )
            gpkg_member = english[0]
        vsi_path = f"/vsizip/{zip_path}/{gpkg_member}"
        layer_names = [name for name, _geometry_type in pyogrio.list_layers(vsi_path)]
        road_layers = [name for name in layer_names if name.endswith("_ROADSEG")]
        if len(road_layers) != 1:
            raise ValueError(
                f"expected one ROADSEG layer in {zip_path}, found {road_layers}"
            )
        road_layer = road_layers[0]

        frame = pyogrio.read_dataframe(vsi_path, layer=road_layer, columns=[])
        if frame.empty:
            raise ValueError(f"NRN road layer is empty: {zip_path} / {road_layer}")
        frame = frame.to_crs(grid["crs"])
        shapes = (
            (geometry, 1)
            for geometry in frame.geometry
            if geometry is not None and not geometry.is_empty
        )
        province_mask = rasterize(
            shapes,
            out_shape=grid["shape"],
            transform=grid["block_transform"],
            fill=0,
            dtype="uint8",
            all_touched=True,
        )
        touched = int(province_mask.sum())
        if touched == 0:
            raise ValueError(
                f"NRN road rasterisation produced no blocks for {province}"
            )
        province_roads |= province_mask
        layers.append(
            {
                "province": province,
                "zip": str(zip_path.relative_to(REPO_ROOT)),
                "gpkg_member": gpkg_member,
                "road_layer": road_layer,
                "features": len(frame),
                "touched_blocks": touched,
            }
        )
        print(
            f"  NRN {province}: {len(frame):,} road segments, {touched:,} blocks",
            flush=True,
        )

    return province_roads, layers


def _init_worker(
    scanfi_dir: str, grid: dict[str, Any], burn_geometries: list[Any]
) -> None:
    global _DATASETS, _GRID, _BURN_GEOMETRIES
    _GRID = grid
    _BURN_GEOMETRIES = burn_geometries
    _DATASETS = {
        name: rasterio.open(Path(scanfi_dir) / filename)
        for name, filename in SCANFI_PATHS.items()
    }
    for name, dataset in _DATASETS.items():
        if dataset.width != grid["width"] or dataset.height != grid["height"]:
            raise ValueError(f"{name} raster shape does not match the land-cover grid")


def _record_mask(
    counts: np.ndarray,
    pixel_totals: np.ndarray,
    strip_index: int,
    name: str,
    mask: np.ndarray,
    block_cols: int,
) -> None:
    index = V2_CASE_INDEX[name]
    counts[index] = v1.block_sum(mask, block_cols)
    pixel_totals[index] = int(mask.sum(dtype=np.int64))
    block_total = int(counts[index].sum(dtype=np.int64))
    if block_total != pixel_totals[index]:
        raise RuntimeError(
            f"strip {strip_index} case {name}: block sum {block_total} "
            f"!= pixel count {pixel_totals[index]}"
        )


def summarise_strip(strip_index: int) -> dict[str, Any]:
    """Reduce one block strip to exact counts for base, age, and burn masks."""
    width = _GRID["width"]
    height = _GRID["height"]
    total_block_rows = _GRID["block_rows"]
    block_cols = _GRID["block_cols"]

    block_row_start = strip_index * STRIP_BLOCKS
    block_rows = min(STRIP_BLOCKS, total_block_rows - block_row_start)
    row_offset = block_row_start * v1.BLOCK_PIXELS
    rows = min(v1.BLOCK_PIXELS * block_rows, height - row_offset)
    window = Window(0, row_offset, width, rows)

    counts = np.zeros((len(V2_CASES), block_rows, block_cols), dtype=np.int32)
    pixel_totals = np.zeros(len(V2_CASES), dtype=np.int64)

    landcover = _DATASETS["landcover"].read(1, window=window)
    treed = (landcover >= FOREST_CLASSES[0]) & (landcover <= FOREST_CLASSES[-1])
    _record_mask(counts, pixel_totals, strip_index, "treed", treed, block_cols)
    del landcover

    height_values = _DATASETS["height"].read(1, window=window)
    height_ok = height_values != 255
    height_pass = height_ok & (height_values >= 5)
    del height_values

    biomass_values = _DATASETS["biomass"].read(1, window=window)
    biomass_ok = biomass_values != -32768
    biomass_pass = biomass_ok & (biomass_values >= 20)
    del biomass_values

    closure_values = _DATASETS["closure"].read(1, window=window)
    closure_ok = closure_values != 255
    closure_pass = closure_ok & (closure_values >= 10)
    del closure_values

    base = treed & height_pass & biomass_pass & closure_pass
    _record_mask(counts, pixel_totals, strip_index, "base", base, block_cols)
    del height_ok, height_pass, biomass_ok, biomass_pass, closure_ok, closure_pass

    age_values = _DATASETS["age"].read(1, window=window)
    age_ok = age_values != -32768
    age_valid = base & age_ok
    age_ge_20 = age_valid & (age_values >= 20)
    age_ge_30 = age_valid & (age_values >= 30)
    age_lt_20 = age_valid & (age_values < 20)
    age_lt_30 = age_valid & (age_values < 30)
    age_missing = base & ~age_ok
    del age_values, age_ok, age_valid

    _record_mask(counts, pixel_totals, strip_index, "age_ge_20", age_ge_20, block_cols)
    _record_mask(counts, pixel_totals, strip_index, "age_ge_30", age_ge_30, block_cols)
    _record_mask(counts, pixel_totals, strip_index, "age_lt_20", age_lt_20, block_cols)
    _record_mask(counts, pixel_totals, strip_index, "age_lt_30", age_lt_30, block_cols)
    _record_mask(
        counts, pixel_totals, strip_index, "age_missing", age_missing, block_cols
    )
    del age_lt_20, age_lt_30, age_missing

    strip_transform = window_transform(window, _GRID["transform"])
    strip_bounds = window_bounds(window, _GRID["transform"])
    min_x, min_y, max_x, max_y = strip_bounds
    burn_shapes = [
        (geometry, 1)
        for geometry in _BURN_GEOMETRIES
        if geometry.bounds[2] >= min_x
        and geometry.bounds[0] <= max_x
        and geometry.bounds[3] >= min_y
        and geometry.bounds[1] <= max_y
    ]
    if burn_shapes:
        burn = rasterize(
            burn_shapes,
            out_shape=(rows, width),
            transform=strip_transform,
            fill=0,
            dtype="uint8",
            all_touched=False,
        ).astype(bool)
    else:
        burn = np.zeros((rows, width), dtype=bool)

    _record_mask(
        counts,
        pixel_totals,
        strip_index,
        "burn_base",
        base & burn,
        block_cols,
    )
    _record_mask(
        counts,
        pixel_totals,
        strip_index,
        "burn_age20",
        age_ge_20 & burn,
        block_cols,
    )
    _record_mask(
        counts,
        pixel_totals,
        strip_index,
        "burn_age30",
        age_ge_30 & burn,
        block_cols,
    )
    del burn, base, treed, age_ge_20, age_ge_30

    return {
        "strip_index": strip_index,
        "block_row_start": block_row_start,
        "block_rows": block_rows,
        "counts": counts,
        "pixel_totals": pixel_totals,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scanfi-dir", type=Path, default=DEFAULT_SCANFI_DIR)
    parser.add_argument("--rsyc", type=Path, default=DEFAULT_RSYC)
    parser.add_argument("--provinces", type=Path, default=DEFAULT_PROVINCES)
    parser.add_argument("--managed-dir", type=Path, default=DEFAULT_MANAGED_DIR)
    parser.add_argument("--v1-dir", type=Path, default=DEFAULT_V1_DIR)
    parser.add_argument("--nbac", type=Path, default=DEFAULT_NBAC)
    parser.add_argument("--nrn-dir", type=Path, default=DEFAULT_NRN_DIR)
    parser.add_argument("--area-weights", type=Path, default=DEFAULT_AREA_WEIGHTS)
    parser.add_argument("--nfi", type=Path, default=DEFAULT_NFI)
    parser.add_argument("--scanfi-manifest", type=Path, default=DEFAULT_SCANFI_MANIFEST)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--workers", type=int, default=20)
    parser.add_argument(
        "--skip-roads",
        action="store_true",
        help="Debug only: omit the NRN road screen and record a gap.",
    )
    return parser.parse_args()


def build_unit_frame(
    counts: np.ndarray,
    province_ids: np.ndarray,
    ecoprovince_ids: np.ndarray,
    ecoprovinces: pd.DataFrame,
    province_names: dict[int, str],
    road_distance_m: np.ndarray | None,
    managed: pd.DataFrame,
    covered: set[str],
) -> pd.DataFrame:
    """Aggregate block counts into a province x ecoprovince case table."""
    if int(ecoprovince_ids.max()) >= 1000:
        raise ValueError("ecoprovince index exceeds the 3-digit key space")
    key = province_ids.astype(np.int64) * 1000 + ecoprovince_ids.astype(np.int64)
    n_keys = int(key.max()) + 1

    def aggregate(weights: np.ndarray) -> np.ndarray:
        return np.bincount(key.ravel(), weights=weights.ravel(), minlength=n_keys)

    def case_area(name: str) -> np.ndarray:
        return aggregate(counts[V2_CASE_INDEX[name]].astype(np.float64) * PIXEL_HA)

    areas: dict[str, np.ndarray] = {}
    for name in V2_CASES:
        areas[name] = case_area(name)

    base_block = counts[V2_CASE_INDEX["base"]].astype(np.float64) * PIXEL_HA
    age20_block = counts[V2_CASE_INDEX["age_ge_20"]].astype(np.float64) * PIXEL_HA
    age30_block = counts[V2_CASE_INDEX["age_ge_30"]].astype(np.float64) * PIXEL_HA
    burn_base_block = counts[V2_CASE_INDEX["burn_base"]].astype(np.float64) * PIXEL_HA
    burn_age20_block = counts[V2_CASE_INDEX["burn_age20"]].astype(np.float64) * PIXEL_HA
    burn_age30_block = counts[V2_CASE_INDEX["burn_age30"]].astype(np.float64) * PIXEL_HA

    screen_age20_block = age20_block - burn_age20_block
    screen_age30_block = age30_block - burn_age30_block
    areas["disturb_2020_excluded"] = aggregate(base_block - burn_base_block)
    areas["screen_age20_disturb"] = aggregate(screen_age20_block)
    areas["screen_age30_disturb"] = aggregate(screen_age30_block)

    if road_distance_m is None:
        zero_units = np.zeros_like(areas["base"])
        for name in (
            "road_0_5km",
            "road_5_10km",
            "road_10_20km",
            "road_gt_20km",
            "road_le_5km",
            "road_le_10km",
            "road_le_20km",
            "screen_core20",
            "screen_core30",
        ):
            areas[name] = zero_units.copy()
    else:
        road_le_5 = road_distance_m <= 5_000
        road_le_10 = road_distance_m <= 10_000
        road_le_20 = road_distance_m <= 20_000
        areas["road_le_5km"] = aggregate(base_block * road_le_5)
        areas["road_le_10km"] = aggregate(base_block * road_le_10)
        areas["road_le_20km"] = aggregate(base_block * road_le_20)
        areas["road_0_5km"] = aggregate(base_block * road_le_5)
        areas["road_5_10km"] = aggregate(base_block * (road_le_10 & ~road_le_5))
        areas["road_10_20km"] = aggregate(base_block * (road_le_20 & ~road_le_10))
        areas["road_gt_20km"] = aggregate(base_block * ~road_le_20)
        areas["screen_core20"] = aggregate(screen_age20_block * road_le_20)
        areas["screen_core30"] = aggregate(screen_age30_block * road_le_10)
    del (
        base_block,
        age20_block,
        age30_block,
        burn_base_block,
        burn_age20_block,
        burn_age30_block,
        screen_age20_block,
        screen_age30_block,
    )

    eco_names = ecoprovinces.set_index("ecoprovince_index")["ecoprovince"].to_dict()
    eco_ids = ecoprovinces.set_index("ecoprovince_index")["ecoprovince_id"].to_dict()
    managed_lookup = {
        (str(row.province), float(row.ecoprovince_id)): float(row.managed_proxy_ha)
        for row in managed.itertuples()
    }

    rows: list[dict[str, Any]] = []
    for encoded in np.flatnonzero(areas["treed"] > 0):
        province_id = int(encoded // 1000)
        ecoprovince_index = int(encoded % 1000)
        if province_id not in province_names or ecoprovince_index not in eco_names:
            continue
        province = province_names[province_id]
        ecoprovince = eco_names[ecoprovince_index]
        ecoprovince_id = float(eco_ids[ecoprovince_index])
        treed_ha = float(areas["treed"][encoded])
        if treed_ha <= 0:
            continue
        coverage_available = province in covered
        managed_ha = managed_lookup.get((province, ecoprovince_id))
        if coverage_available and managed_ha is None:
            managed_ha = 0.0
        if coverage_available:
            managed_share = managed_ha / treed_ha
            if managed_share > 1.000001:
                raise ValueError(
                    f"managed share above one in {province}/{ecoprovince}: "
                    f"{managed_share:.4f}"
                )
        else:
            managed_ha = np.nan
            managed_share = np.nan
        row: dict[str, Any] = {
            "province": province,
            "ecoprovince": ecoprovince,
            "ecoprovince_id": ecoprovince_id,
            "scanfi_forest_ha": treed_ha,
            "base_ha": float(areas["base"][encoded]),
            "base_merchantable_ha": float(areas["base"][encoded]),
            "managed_proxy_ha": managed_ha,
            "managed_share": managed_share,
            "coverage_available": coverage_available,
        }
        for name in (
            "age_ge_20",
            "age_ge_30",
            "age_lt_20",
            "age_lt_30",
            "age_missing",
            "burn_base",
            "burn_age20",
            "burn_age30",
            "disturb_2020_excluded",
            "road_le_5km",
            "road_le_10km",
            "road_le_20km",
            "road_0_5km",
            "road_5_10km",
            "road_10_20km",
            "road_gt_20km",
            "screen_age20_disturb",
            "screen_age30_disturb",
            "screen_core20",
            "screen_core30",
        ):
            row[f"{name}_ha"] = float(areas[name][encoded])
        rows.append(row)

    frame = pd.DataFrame(rows)
    if frame.empty:
        raise RuntimeError("no treed area was attributed to any province/ecoprovince")
    return frame


def build_outputs(unit_frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    gap_text = "; ".join(gap["gap_id"] for gap in GAPS)
    long_rows: list[pd.DataFrame] = []
    for definition in CASE_DEFS:
        area = unit_frame[f"{definition['area_key']}_ha"].to_numpy(dtype=np.float64)
        base = unit_frame["base_merchantable_ha"].to_numpy(dtype=np.float64)
        treed = unit_frame["scanfi_forest_ha"].to_numpy(dtype=np.float64)
        managed_share = unit_frame["managed_share"].to_numpy(dtype=np.float64)
        covered = unit_frame["coverage_available"].to_numpy(dtype=bool)
        operable = area * managed_share
        operable = np.where(covered, operable, np.nan)
        subset = pd.DataFrame(
            {
                "province": unit_frame["province"],
                "ecoprovince": unit_frame["ecoprovince"],
                "ecoprovince_id": unit_frame["ecoprovince_id"],
                "screen_case": definition["case"],
                "screen_label": definition["label"],
                "age_min_years": definition["age_min_years"],
                "disturbance_screen": definition["disturbance_screen"],
                "road_max_km": definition["road_max_km"],
                "scanfi_forest_ha": treed,
                "base_merchantable_ha": base,
                "screened_merchantable_ha": area,
                "excluded_ha": base - area,
                "share_of_base_merchantable": np.where(base > 0, area / base, 0.0),
                "managed_proxy_ha": unit_frame["managed_proxy_ha"],
                "managed_share": managed_share,
                "operable_proxy_ha": operable,
                "operable_share_of_treed": operable / treed,
                "coverage_available": covered,
                "gaps": gap_text,
                "notes": (
                    f"{definition['label']}; managed share from v1 applied uniformly "
                    "inside province-ecoprovince; screening proxy, not legal THLB"
                ),
            }
        )
        subset.loc[~covered, "operable_share_of_treed"] = np.nan
        subset.loc[~covered, "notes"] = (
            f"{definition['label']}; no accepted 2016 tenure coverage, operable values "
            "intentionally blank, not zero; screening proxy"
        )
        long_rows.append(subset)

    ecoprovince_out = pd.concat(long_rows, ignore_index=True)
    ecoprovince_out["ecoprovince_id"] = ecoprovince_out["ecoprovince_id"].round(6)
    ecoprovince_out["_order"] = ecoprovince_out["screen_case"].map(CASE_ORDER)
    ecoprovince_out = (
        ecoprovince_out.sort_values(["province", "ecoprovince_id", "_order"])
        .drop(columns="_order")
        .reset_index(drop=True)
    )

    group = ["province", "screen_case"]
    province_out = (
        ecoprovince_out.groupby(group, as_index=False)
        .agg(
            scanfi_forest_ha=("scanfi_forest_ha", "sum"),
            base_merchantable_ha=("base_merchantable_ha", "sum"),
            screened_merchantable_ha=("screened_merchantable_ha", "sum"),
            excluded_ha=("excluded_ha", "sum"),
            managed_proxy_ha=(
                "managed_proxy_ha",
                lambda values: values.sum(min_count=1),
            ),
            operable_proxy_ha=(
                "operable_proxy_ha",
                lambda values: values.sum(min_count=1),
            ),
            coverage_available=("coverage_available", "any"),
        )
        .reset_index(drop=True)
    )
    province_out["ecoprovince"] = "ALL"
    province_out["ecoprovince_id"] = np.nan
    province_out["share_of_base_merchantable"] = np.where(
        province_out["base_merchantable_ha"] > 0,
        province_out["screened_merchantable_ha"] / province_out["base_merchantable_ha"],
        0.0,
    )
    province_out["managed_share"] = (
        province_out["managed_proxy_ha"] / province_out["scanfi_forest_ha"]
    )
    province_out["operable_share_of_treed"] = (
        province_out["operable_proxy_ha"] / province_out["scanfi_forest_ha"]
    )
    case_metadata = pd.DataFrame(CASE_DEFS)[
        ["case", "label", "age_min_years", "disturbance_screen", "road_max_km"]
    ].rename(columns={"case": "screen_case", "label": "screen_label"})
    province_out = province_out.merge(case_metadata, on="screen_case", how="left")
    province_out["gaps"] = gap_text
    province_out["notes"] = (
        "province total is the sum of province-ecoprovince rows; managed share is the "
        "v1 applied share; screening proxy, not legal THLB"
    )
    province_out.loc[~province_out["coverage_available"], "notes"] = (
        "no accepted 2016 tenure source; managed and operable values intentionally "
        "blank, not zero; screening proxy"
    )
    province_out["_order"] = province_out["screen_case"].map(CASE_ORDER)
    province_out = (
        province_out.sort_values(["province", "_order"])
        .drop(columns="_order")
        .reset_index(drop=True)
    )
    columns = [
        "province",
        "ecoprovince",
        "ecoprovince_id",
        "screen_case",
        "screen_label",
        "age_min_years",
        "disturbance_screen",
        "road_max_km",
        "scanfi_forest_ha",
        "base_merchantable_ha",
        "screened_merchantable_ha",
        "excluded_ha",
        "share_of_base_merchantable",
        "managed_proxy_ha",
        "managed_share",
        "operable_proxy_ha",
        "operable_share_of_treed",
        "coverage_available",
        "gaps",
        "notes",
    ]
    return ecoprovince_out[columns], province_out[columns]


def national_case_areas(frame: pd.DataFrame) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for definition in CASE_DEFS:
        case = definition["case"]
        rows = frame[frame["screen_case"] == case]
        if rows.empty:
            raise ValueError(f"no rows for screen case {case}")
        operable = rows["operable_proxy_ha"].dropna()
        treed = float(rows["scanfi_forest_ha"].sum())
        base = float(rows["base_merchantable_ha"].sum())
        screened = float(rows["screened_merchantable_ha"].sum())
        excluded = float(rows["excluded_ha"].sum())
        result[case] = {
            "label": definition["label"],
            "age_min_years": definition["age_min_years"],
            "disturbance_screen": definition["disturbance_screen"],
            "road_max_km": definition["road_max_km"],
            "screened_merchantable_ha": screened,
            "excluded_ha": excluded,
            "operable_proxy_ha": float(operable.sum()) if not operable.empty else None,
            "share_of_base_merchantable": screened / base if base > 0 else None,
            "share_of_treed": screened / treed if treed > 0 else None,
        }
    return result


def validate(
    unit_frame: pd.DataFrame,
    ecoprovince_out: pd.DataFrame,
    province_out: pd.DataFrame,
    national: dict[str, dict[str, Any]],
    strip_residual: int,
    area_weights_path: Path,
    v1_summary: dict[str, Any],
    road_available: bool,
) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    checks["strip_pixel_conservation_max_residual"] = int(strip_residual)

    national_treed_ha = float(unit_frame["scanfi_forest_ha"].sum())
    checks["national_treed_ha"] = national_treed_ha
    if area_weights_path.exists():
        weights = pd.read_csv(area_weights_path)
        weights = weights[weights["level"] == "ecodistrict"]
        area_weights_total = float(
            weights.drop_duplicates(["province", "ecodistrict_id"])[
                "forest_area_ha"
            ].sum()
        )
        checks["area_weights_forest_ha"] = area_weights_total
        checks["treed_vs_area_weights_abs_diff_ha"] = abs(
            national_treed_ha - area_weights_total
        )
        checks["treed_matches_area_weights"] = (
            checks["treed_vs_area_weights_abs_diff_ha"] < 1.0
        )

    numeric_columns = [
        "scanfi_forest_ha",
        "base_merchantable_ha",
        "age_ge_20_ha",
        "age_ge_30_ha",
        "age_lt_20_ha",
        "age_lt_30_ha",
        "age_missing_ha",
        "burn_base_ha",
        "burn_age20_ha",
        "burn_age30_ha",
        "disturb_2020_excluded_ha",
        "road_le_5km_ha",
        "road_le_10km_ha",
        "road_le_20km_ha",
        "screen_age20_disturb_ha",
        "screen_age30_disturb_ha",
        "screen_core20_ha",
        "screen_core30_ha",
    ]
    checks["no_nan_in_area_fields"] = bool(
        unit_frame[numeric_columns].notna().all().all()
    )
    checks["no_negative_area_fields"] = bool(
        (unit_frame[numeric_columns] >= 0).all().all()
    )

    base = unit_frame["base_merchantable_ha"]
    treed = unit_frame["scanfi_forest_ha"]
    checks["base_le_treed"] = bool((base <= treed + 1e-6).all())
    checks["age20_le_base"] = bool((unit_frame["age_ge_20_ha"] <= base + 1e-6).all())
    checks["age30_le_age20"] = bool(
        (unit_frame["age_ge_30_ha"] <= unit_frame["age_ge_20_ha"] + 1e-6).all()
    )
    age_partition_residual = (
        unit_frame["age_ge_20_ha"]
        + unit_frame["age_lt_20_ha"]
        + unit_frame["age_missing_ha"]
        - base
    ).abs()
    checks["age_partition_max_abs_diff_ha"] = float(age_partition_residual.max())
    checks["age_partition_conserves_base"] = bool(age_partition_residual.le(1e-2).all())
    checks["burn_le_base"] = bool((unit_frame["burn_base_ha"] <= base + 1e-6).all())
    checks["burn_age20_le_age20"] = bool(
        (unit_frame["burn_age20_ha"] <= unit_frame["age_ge_20_ha"] + 1e-6).all()
    )
    checks["screen_age20_disturb_le_age20"] = bool(
        (
            unit_frame["screen_age20_disturb_ha"] <= unit_frame["age_ge_20_ha"] + 1e-6
        ).all()
    )
    checks["screen_age30_disturb_le_age30"] = bool(
        (
            unit_frame["screen_age30_disturb_ha"] <= unit_frame["age_ge_30_ha"] + 1e-6
        ).all()
    )
    if road_available:
        checks["road_5km_le_10km_le_20km_le_base"] = bool(
            (unit_frame["road_le_5km_ha"] <= unit_frame["road_le_10km_ha"] + 1e-6).all()
            and (
                unit_frame["road_le_10km_ha"] <= unit_frame["road_le_20km_ha"] + 1e-6
            ).all()
            and (unit_frame["road_le_20km_ha"] <= base + 1e-6).all()
        )
        checks["road_classes_conserve_base"] = bool(
            (
                unit_frame["road_0_5km_ha"]
                + unit_frame["road_5_10km_ha"]
                + unit_frame["road_10_20km_ha"]
                + unit_frame["road_gt_20km_ha"]
                - base
            )
            .abs()
            .le(1e-2)
            .all()
        )
        checks["road_class_conservation_max_abs_diff_ha"] = float(
            (
                unit_frame["road_0_5km_ha"]
                + unit_frame["road_5_10km_ha"]
                + unit_frame["road_10_20km_ha"]
                + unit_frame["road_gt_20km_ha"]
                - base
            )
            .abs()
            .max()
        )
    else:
        checks["road_5km_le_10km_le_20km_le_base"] = None
        checks["road_classes_conserve_base"] = None
    checks["core20_le_age20_disturb"] = bool(
        (
            unit_frame["screen_core20_ha"]
            <= unit_frame["screen_age20_disturb_ha"] + 1e-6
        ).all()
    )
    checks["core30_le_age30_disturb"] = bool(
        (
            unit_frame["screen_core30_ha"]
            <= unit_frame["screen_age30_disturb_ha"] + 1e-6
        ).all()
    )
    checks["core30_le_core20"] = bool(
        (unit_frame["screen_core30_ha"] <= unit_frame["screen_core20_ha"] + 1e-6).all()
    )

    checks["managed_share_le_one"] = bool(
        (unit_frame["managed_share"].dropna() <= 1.000001).all()
    )
    screened = ecoprovince_out["screened_merchantable_ha"]
    operable = ecoprovince_out["operable_proxy_ha"]
    checks["operable_defined_where_coverage_available"] = bool(
        operable[ecoprovince_out["coverage_available"]].notna().all()
    )
    checks["operable_blank_where_coverage_missing"] = bool(
        operable[~ecoprovince_out["coverage_available"]].isna().all()
    )
    checks["operable_le_screened"] = bool(
        (
            operable[ecoprovince_out["coverage_available"]]
            <= screened[ecoprovince_out["coverage_available"]] + 1e-6
        ).all()
    )
    checks["no_negative_operable"] = bool((operable.dropna() >= 0).all())

    checks["province_sum_equals_ecoprovince_sum_all_cases"] = True
    for definition in CASE_DEFS:
        case = definition["case"]
        province_case = province_out[province_out["screen_case"] == case]
        ecoprovince_case = ecoprovince_out[ecoprovince_out["screen_case"] == case]
        for column in (
            "scanfi_forest_ha",
            "base_merchantable_ha",
            "screened_merchantable_ha",
        ):
            if abs(province_case[column].sum() - ecoprovince_case[column].sum()) > 1e-6:
                checks["province_sum_equals_ecoprovince_sum_all_cases"] = False
                checks[f"province_sum_mismatch_{case}_{column}"] = True

    v1_operable = float(v1_summary["validation"]["national_operable_proxy_ha"])
    checks["v1_operable_proxy_ha"] = v1_operable
    checks["v2_base_operable_proxy_ha"] = national["base"]["operable_proxy_ha"]
    checks["v2_base_equals_v1_base"] = bool(
        abs(float(national["base"]["operable_proxy_ha"]) - v1_operable) < 1.0
    )
    checks["v2_screens_le_v1_base"] = bool(
        all(
            national[definition["case"]]["screened_merchantable_ha"]
            <= national["base"]["screened_merchantable_ha"] + 1e-6
            for definition in CASE_DEFS
        )
    )

    checks["all_checks_pass"] = bool(
        checks["strip_pixel_conservation_max_residual"] == 0
        and checks.get("treed_matches_area_weights", True)
        and checks["no_nan_in_area_fields"]
        and checks["no_negative_area_fields"]
        and checks["base_le_treed"]
        and checks["age20_le_base"]
        and checks["age30_le_age20"]
        and checks["age_partition_conserves_base"]
        and checks["burn_le_base"]
        and checks["burn_age20_le_age20"]
        and checks["screen_age20_disturb_le_age20"]
        and checks["screen_age30_disturb_le_age30"]
        and checks["core20_le_age20_disturb"]
        and checks["core30_le_age30_disturb"]
        and checks["core30_le_core20"]
        and checks["managed_share_le_one"]
        and checks["operable_defined_where_coverage_available"]
        and checks["operable_blank_where_coverage_missing"]
        and checks["operable_le_screened"]
        and checks["no_negative_operable"]
        and checks["province_sum_equals_ecoprovince_sum_all_cases"]
        and checks["v2_base_equals_v1_base"]
        and checks["v2_screens_le_v1_base"]
    )
    if not checks["all_checks_pass"]:
        raise RuntimeError(
            f"operable-proxy v2 validation failed: {json.dumps(checks, indent=2)}"
        )
    return checks


def build_summary(
    args: argparse.Namespace,
    runtime_seconds: float,
    unit_frame: pd.DataFrame,
    ecoprovince_out: pd.DataFrame,
    province_out: pd.DataFrame,
    national: dict[str, dict[str, Any]],
    checks: dict[str, Any],
    input_metadata: dict[str, Any],
    nrn_layers: list[dict[str, Any]],
    nbac_metadata: dict[str, Any],
    road_available: bool,
) -> dict[str, Any]:
    by_province: dict[str, dict[str, Any]] = {}
    for province in ALL_PROVINCES:
        rows = province_out[province_out["province"] == province]
        if rows.empty:
            continue
        base_row = rows[rows["screen_case"] == "base"]
        if base_row.empty:
            continue
        base_row = base_row.iloc[0]
        province_payload: dict[str, Any] = {
            "scanfi_forest_ha": float(base_row["scanfi_forest_ha"]),
            "base_merchantable_ha": float(base_row["base_merchantable_ha"]),
            "managed_proxy_ha": (
                float(base_row["managed_proxy_ha"])
                if pd.notna(base_row["managed_proxy_ha"])
                else None
            ),
            "base_operable_proxy_ha": (
                float(base_row["operable_proxy_ha"])
                if pd.notna(base_row["operable_proxy_ha"])
                else None
            ),
            "managed_share": (
                float(base_row["managed_share"])
                if pd.notna(base_row["managed_share"])
                else None
            ),
            "coverage": "tenure_2016" if base_row["coverage_available"] else "missing",
            "screens": {},
        }
        for definition in CASE_DEFS:
            case_row = rows[rows["screen_case"] == definition["case"]]
            if case_row.empty:
                continue
            case_row = case_row.iloc[0]
            province_payload["screens"][definition["case"]] = {
                "screened_merchantable_ha": float(case_row["screened_merchantable_ha"]),
                "operable_proxy_ha": (
                    float(case_row["operable_proxy_ha"])
                    if pd.notna(case_row["operable_proxy_ha"])
                    else None
                ),
            }
        by_province[province] = province_payload

    road_classes = {}
    for name in (
        "road_0_5km_ha",
        "road_5_10km_ha",
        "road_10_20km_ha",
        "road_gt_20km_ha",
    ):
        road_classes[name.replace("_ha", "")] = float(unit_frame[name].sum())

    age_flags = {
        "age_lt_20_ha": float(unit_frame["age_lt_20_ha"].sum()),
        "age_lt_30_ha": float(unit_frame["age_lt_30_ha"].sum()),
        "age_missing_ha": float(unit_frame["age_missing_ha"].sum()),
        "base_merchantable_ha": float(unit_frame["base_merchantable_ha"].sum()),
    }
    age_flags["age_lt_20_share_of_base"] = (
        age_flags["age_lt_20_ha"] / age_flags["base_merchantable_ha"]
        if age_flags["base_merchantable_ha"] > 0
        else None
    )
    age_flags["age_lt_30_share_of_base"] = (
        age_flags["age_lt_30_ha"] / age_flags["base_merchantable_ha"]
        if age_flags["base_merchantable_ha"] > 0
        else None
    )

    disturbance = {
        "product": "NBAC 2020 annual fire polygons",
        "nbac_metadata": nbac_metadata,
        "burn_base_ha": float(unit_frame["burn_base_ha"].sum()),
        "burn_age20_ha": float(unit_frame["burn_age20_ha"].sum()),
        "burn_age30_ha": float(unit_frame["burn_age30_ha"].sum()),
        "burn_share_of_base": (
            float(unit_frame["burn_base_ha"].sum())
            / float(unit_frame["base_merchantable_ha"].sum())
            if float(unit_frame["base_merchantable_ha"].sum()) > 0
            else None
        ),
        "years_available": [2020],
        "years_unavailable": [2010, 2015],
        "note": (
            "Only the 2020 annual NBAC vintage is present. The 2010 and 2015 "
            "disturbance sensitivity cases are gaps, not zero-disturbance cases."
        ),
    }

    base_area = national["base"]["operable_proxy_ha"] or 0.0
    intensity_base = TARGET_VOLUME_M3 / base_area if base_area > 0 else None
    intensity_150 = {}
    for definition in CASE_DEFS:
        case = definition["case"]
        operable = national[case]["operable_proxy_ha"]
        if not operable:
            intensity_150[case] = {
                "operable_proxy_ha": None,
                "intensity_m3_ha_yr": None,
                "intensity_relative_to_base": None,
            }
            continue
        intensity = TARGET_VOLUME_M3 / float(operable)
        intensity_150[case] = {
            "operable_proxy_ha": float(operable),
            "intensity_m3_ha_yr": intensity,
            "intensity_relative_to_base": (
                intensity / intensity_base if intensity_base else None
            ),
            "implied_odt_per_ha_yr": intensity * ODT_PER_M3,
        }

    return {
        "product": "national operable merchantable-forest screening proxy v2",
        "status": (
            "screening proxy; NOT a legal THLB, AAC landbase, or operability model; "
            "no slope screen because no national DEM is available"
        ),
        "generated_at_utc": utc_now(),
        "runtime_seconds": round(runtime_seconds, 3),
        "command": " ".join(str(part) for part in sys.argv),
        "resolution_m": v1.PIXEL_M,
        "aggregation_block_m": BLOCK_M,
        "screens": {
            "age": {
                "source": "SCANFI_age_median_2025_v2_20260119.tif",
                "ages_excluded_below_years": list(AGE_MINIMUM_CASES),
                "assumption": (
                    "Stands below 20 or 30 years are treated as non-merchantable for "
                    "the screen; this is stricter than the v1 height/biomass/closure "
                    "thresholds and is a screening convention, not a legal rule."
                ),
            },
            "disturbance": {
                "source": "NBAC_2020_20260513.zip",
                "years_available": [2020],
                "screens": ["baseline", "exclude_2020_burn"],
                "assumption": (
                    "A 30 m pixel is burned when its centre falls inside a 2020 NBAC "
                    "fire polygon. NBAC 2020 is an annual fire layer, not a cumulative "
                    "severity stack."
                ),
            },
            "road": {
                "source": "NRN province bundles in data/raw/nrn",
                "distance_classes_km": list(ROAD_BREAKS_KM),
                "access_screen_km": 20,
                "method": (
                    "Road segments rasterised at 990 m with all-touched; Euclidean "
                    "distance transform gives nearest-public-road distance at block "
                    "centres."
                ),
                "caveat": (
                    "Public NRN roads only; resource, industrial, winter, and "
                    "decommissioned roads are absent."
                ),
                "available": road_available,
            },
            "slope": {
                "status": "gap",
                "detail": "No national DEM in the workspace; no slope filter applied.",
            },
        },
        "national": national,
        "by_province": by_province,
        "age_flags": age_flags,
        "disturbance": disturbance,
        "road_distance_classes": road_classes,
        "nrn_layers": nrn_layers,
        "intensity_150m": {
            "target_volume_m3_yr": TARGET_VOLUME_M3,
            "reference_base_path_m3_yr": BASE_PATH_VOLUME_M3,
            "odt_per_m3": ODT_PER_M3,
            "by_case": intensity_150,
            "interpretation": (
                "150,000,000 m3/yr is an upper scenario bound, not a forecast or an "
                "AAC. Intensity is target volume divided by the screened operable "
                "area. It is not a growth/yield estimate and does not demonstrate "
                "sustainability."
            ),
        },
        "validation": checks,
        "inputs": input_metadata,
        "gaps": list(GAPS),
        "caveats": [
            "Screening proxy only; not a legal THLB, AAC landbase, or harvest feasibility test.",
            "No slope, elevation, or operability screen: no national DEM is available.",
            "NBAC 2020 is an annual fire vintage; 2010 and 2015 disturbance cases are gaps.",
            "NRN is the public road network; resource and industrial roads are absent.",
            "Managed share is uniform inside each province-ecoprovince; tenure is not intersected spatially.",
            "SCANFI age is a prediction surface, not a census or disturbance record.",
            "NU and PE managed and operable values remain blank because the 2016 tenure compilation has no coverage.",
        ],
    }


def write_report(
    path: Path, summary: dict[str, Any], province_out: pd.DataFrame
) -> None:
    def mha(value: float | None) -> str:
        return "missing" if value is None else f"{value / 1e6:,.2f}"

    national = summary["national"]
    checks = summary["validation"]
    base = province_out[province_out["screen_case"] == "base"].copy()
    base = base.sort_values("base_merchantable_ha", ascending=False)

    lines = [
        "# National Operable Landbase Proxy v2",
        "",
        (
            "**Status: screening proxy. This is not a legal timber harvesting landbase (THLB), "
            "not an AAC landbase, and not an operability model.**"
        ),
        "",
        f"Generated: {summary['generated_at_utc']}  ",
        f"Runtime: {summary['runtime_seconds']:.1f} s  ",
        f"Grid: {v1.PIXEL_M:.0f} m SCANFI pixels aggregated to {BLOCK_M:.0f} m blocks.",
        "",
        "## Method",
        "",
        "1. Start from the v1 operable proxy: SCANFI treed classes 5/6/7, merchantability >= 5 m height, >= 20 Mg/ha biomass, >= 10% closure, and the v1 managed share.",
        "2. Add SCANFI median-age screens at >= 20 and >= 30 years, excluding younger stands.",
        "3. Add an NBAC 2020 annual fire screen; burned merchantable pixels are excluded.",
        "4. Add NRN nearest-public-road distance classes at 5, 10, and 20 km and a 20 km access screen.",
        "5. Report each screen alone and two combined cores. No slope/operability screen is applied because no national DEM is present.",
        "",
        "Age, burn, and road screens are applied to the v1 merchantable surface. The managed share is not re-intersected with tenure; it remains a uniform province-ecoprovince share.",
        "",
        "## Screens and thresholds",
        "",
        "| Screen | Threshold | Treatment |",
        "|---|---|---|",
        "| Age | >= 20 years | exclude younger stands |",
        "| Age | >= 30 years | exclude younger stands |",
        "| Fire | NBAC 2020 perimeter | exclude burned pixels |",
        "| Road | <= 5 km | access sensitivity |",
        "| Road | <= 10 km | access sensitivity |",
        "| Road | <= 20 km | access screen |",
        "| Slope | no national DEM | gap; no filter |",
        "",
        "NBAC 2020 contains only YEAR=2020 polygons. The 2010 and 2015 disturbance sensitivity cases are recorded as gaps, not zero-disturbance assumptions.",
        "",
        "## National area results",
        "",
        "| Case | Age | Disturbance | Road | Screened merchantable (Mha) | Share of base | Operable proxy (Mha) | Excluded (Mha) |",
        "|---|---:|---|---:|---:|---:|---:|---:|",
    ]
    for definition in CASE_DEFS:
        case = definition["case"]
        row = national[case]
        age = (
            ""
            if definition["age_min_years"] is None
            else f">= {definition['age_min_years']} yr"
        )
        road = (
            ""
            if definition["road_max_km"] is None
            else f"<= {definition['road_max_km']} km"
        )
        disturbance = (
            "none" if definition["disturbance_screen"] == "none" else "NBAC 2020"
        )
        lines.append(
            f"| {case} | {age} | {disturbance} | {road} | "
            f"{mha(row['screened_merchantable_ha'])} | "
            f"{row['share_of_base_merchantable']:.1%} | "
            f"{mha(row['operable_proxy_ha'])} | {mha(row['excluded_ha'])} |"
        )
    lines += [
        "",
        f"v1 base operable proxy: **{mha(checks['v1_operable_proxy_ha'])} Mha**.",
        f"v2 base operable proxy: **{mha(checks['v2_base_operable_proxy_ha'])} Mha**.",
        "",
        "## Base case by province/territory",
        "",
        "| Province | SCANFI treed (Mha) | Base merchantable (Mha) | Managed proxy (Mha) | Base operable (Mha) | Managed share |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for _, row in base.iterrows():
        managed = (
            "missing"
            if pd.isna(row["managed_proxy_ha"])
            else f"{row['managed_proxy_ha'] / 1e6:,.2f}"
        )
        operable = (
            "missing"
            if pd.isna(row["operable_proxy_ha"])
            else f"{row['operable_proxy_ha'] / 1e6:,.2f}"
        )
        share = (
            "missing"
            if pd.isna(row["managed_share"])
            else f"{row['managed_share']:.1%}"
        )
        lines.append(
            f"| {row['province']} | {row['scanfi_forest_ha'] / 1e6:,.2f} | "
            f"{row['base_merchantable_ha'] / 1e6:,.2f} | {managed} | {operable} | {share} |"
        )

    lines += [
        "",
        "## Age sensitivity",
        "",
        "| Case | National operable (Mha) | Change vs base |",
        "|---|---:|---:|",
    ]
    base_operable = national["base"]["operable_proxy_ha"]
    for case in ("age_ge_20", "age_ge_30"):
        operable = national[case]["operable_proxy_ha"]
        delta = (
            None
            if operable is None or base_operable is None
            else operable - base_operable
        )
        lines.append(
            f"| {case} | {mha(operable)} | "
            f"{'missing' if delta is None else f'{delta / 1e6:+,.2f} Mha'} |"
        )
    lines += [
        "",
        (
            f"Stands below 20 years: **{summary['age_flags']['age_lt_20_ha'] / 1e6:,.2f} Mha** "
            f"({summary['age_flags']['age_lt_20_share_of_base']:.1%} of base merchantable)."
        ),
        (
            f"Stands below 30 years: **{summary['age_flags']['age_lt_30_ha'] / 1e6:,.2f} Mha** "
            f"({summary['age_flags']['age_lt_30_share_of_base']:.1%} of base merchantable)."
        ),
        f"Age-missing base pixels: **{summary['age_flags']['age_missing_ha'] / 1e6:,.4f} Mha**.",
        "",
        "## Disturbance sensitivity",
        "",
    ]
    disturbance = summary["disturbance"]
    lines += [
        (
            f"NBAC 2020 burned merchantable area: **{disturbance['burn_base_ha'] / 1e6:,.4f} Mha** "
            f"({disturbance['burn_share_of_base']:.2%} of base merchantable)."
        ),
        f"NBAC 2020 burned area also age >= 20: **{disturbance['burn_age20_ha'] / 1e6:,.4f} Mha**.",
        "",
        (
            "The NBAC bundle is the 2020 annual vintage (710 polygons, all YEAR=2020). "
            "It does not contain 2010 or 2015 fire history, so those cases are not reported. "
            "The polygons are annual perimeters, not cumulative severity classes."
        ),
        "",
        "## Road proximity",
        "",
        "| Distance class | Base merchantable (Mha) | Share of base |",
        "|---|---:|---:|",
    ]
    road_classes = summary["road_distance_classes"]
    base_merchantable = national["base"]["screened_merchantable_ha"]
    for name, label in (
        ("road_0_5km", "0-5 km"),
        ("road_5_10km", "5-10 km"),
        ("road_10_20km", "10-20 km"),
        ("road_gt_20km", "> 20 km"),
    ):
        value = road_classes[name]
        lines.append(
            f"| {label} | {value / 1e6:,.2f} | {value / base_merchantable:.1%} |"
        )
    lines += [
        "",
        "NRN public roads only. Resource, industrial, winter, and decommissioned roads are absent, so this is an optimistic access proxy.",
        "",
        "## 150M implications",
        "",
        "The 150,000,000 m3/yr path is an upper scenario bound, not a forecast or AAC.",
        "",
        "| Case | Operable area (Mha) | Implied intensity (m3/ha/yr) | Relative to v1 base |",
        "|---|---:|---:|---:|",
    ]
    for case in (
        "base",
        "age_ge_20",
        "age_ge_30",
        "disturb_2020_excluded",
        "road_le_20km",
        "screen_age20_disturb",
        "screen_core20",
        "screen_core30",
    ):
        payload = summary["intensity_150m"]["by_case"][case]
        if payload["operable_proxy_ha"] is None:
            lines.append(f"| {case} | missing | missing | missing |")
            continue
        lines.append(
            f"| {case} | {payload['operable_proxy_ha'] / 1e6:,.2f} | "
            f"{payload['intensity_m3_ha_yr']:.3f} | "
            f"{payload['intensity_relative_to_base']:.2f}x |"
        )
    lines += [
        "",
        "The implied intensity is target volume divided by screened operable area. It is not a growth/yield estimate and does not demonstrate sustainability. Base-path intensity at 118,977,339 m3/yr is proportionally lower.",
        "",
        "## Validation",
        "",
        f"- Strip-level pixel conservation: max residual **{checks['strip_pixel_conservation_max_residual']}** pixels.",
        f"- National treed area vs area-weights: **{checks.get('treed_vs_area_weights_abs_diff_ha', float('nan')):,.3f} ha** difference.",
        f"- No NaN in area fields: **{checks['no_nan_in_area_fields']}**.",
        f"- No negative area fields: **{checks['no_negative_area_fields']}**.",
        f"- Base <= treed: **{checks['base_le_treed']}**.",
        f"- Age 20 <= base and age 30 <= age 20: **{checks['age20_le_base'] and checks['age30_le_age20']}**.",
        f"- Age partition conserves base: **{checks['age_partition_conserves_base']}**.",
        f"- Burn <= base: **{checks['burn_le_base']}**.",
        f"- Road classes conserve base: **{checks.get('road_classes_conserve_base')}**.",
        f"- Combined cores <= component screens: **{checks['core20_le_age20_disturb'] and checks['core30_le_age30_disturb']}**.",
        f"- Operable <= screened where defined: **{checks['operable_le_screened']}**.",
        f"- Province sums equal ecoprovince sums for every case: **{checks['province_sum_equals_ecoprovince_sum_all_cases']}**.",
        f"- v2 base equals v1 base: **{checks['v2_base_equals_v1_base']}**.",
        "",
        "## Gaps",
        "",
    ]
    for gap in GAPS:
        lines.append(
            f"- **{gap['gap_id']}** ({gap['scope']}): {gap['detail']} Effect: {gap['effect']}"
        )
    lines += [
        "",
        "## Outputs",
        "",
        "- `operable_proxy_v2_by_province.csv`: province x screen case.",
        "- `operable_proxy_v2_by_ecoprovince.csv`: province x ecoprovince x screen case.",
        "- `operable_proxy_v2_summary.json`: screens, national totals, sensitivity, 150M implications, validation, gaps.",
        "- `road_distance_classes.csv`: base merchantable area by road distance class and province.",
        "- `gaps.csv`: explicit gap register.",
        "- `manifest.json`: input and output hashes, runtime, software, validation.",
        "",
        "## Reproduction",
        "",
        "```bash",
        ".venv/bin/python scripts/national/build_operable_proxy_v2.py \\",
        "  --out-dir data/derived/national/managed_landbase/operable_proxy_v2",
        "```",
        "",
        "Raw SCANFI, NBAC, NRN, tenure, CPCAD, boundary, and RSYC payloads remain in the `data` submodule and are not committed to the parent repository.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    args = parse_args()
    start = time.time()
    grid = v1.build_grid(args)
    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    (
        provinces,
        province_names,
        districts,
        ecoprovinces,
        _ecoprovince_lookup,
        district_to_ecoprovince,
    ) = v1.load_attribution(args, grid)
    province_ids_raw = v1.rasterize_units(
        provinces, "province_id", grid["block_transform"], grid["shape"], grid["crs"]
    )
    district_ids_raw = v1.rasterize_units(
        districts, "ecodistrict_id", grid["block_transform"], grid["shape"], grid["crs"]
    )
    province_ids, province_fill = v1.fill_missing_ids(province_ids_raw)
    district_ids, district_fill = v1.fill_missing_ids(district_ids_raw)
    del province_ids_raw, district_ids_raw

    max_district = int(district_ids.max())
    district_lookup = np.zeros(max_district + 1, dtype=np.int16)
    for district_id, eco_index in district_to_ecoprovince.items():
        if district_id <= max_district:
            district_lookup[int(district_id)] = int(eco_index)
    ecoprovince_ids = district_lookup[district_ids]
    del district_ids
    if (ecoprovince_ids == 0).any():
        raise ValueError("some blocks have no ecoprovince after nearest-unit fill")

    burn_geometries, nbac_metadata = load_nbac_geometries(args.nbac, grid["crs"])
    print(
        f"NBAC: {nbac_metadata['features']} polygons, years={nbac_metadata['years']}, "
        f"{nbac_metadata['polygon_ha'] / 1e6:,.4f} Mha gross",
        flush=True,
    )

    road_available = not args.skip_roads
    road_distance_m: np.ndarray | None = None
    nrn_layers: list[dict[str, Any]] = []
    if road_available:
        road_presence, nrn_layers = block_road_presence(args.nrn_dir, grid)
        print(f"NRN: {int(road_presence.sum()):,} road blocks", flush=True)
        road_distance_m = distance_transform_edt(
            road_presence == 0, sampling=(BLOCK_M, BLOCK_M)
        )
        del road_presence

    strip_count = math.ceil(grid["block_rows"] / STRIP_BLOCKS)
    print(
        f"reading {strip_count} strips ({grid['block_rows']} x {grid['block_cols']} blocks) "
        f"with {args.workers} workers",
        flush=True,
    )
    counts = np.zeros(
        (len(V2_CASES), grid["block_rows"], grid["block_cols"]), dtype=np.int32
    )
    pixel_totals = np.zeros(len(V2_CASES), dtype=np.int64)
    strip_residual = 0
    with Pool(
        processes=args.workers,
        initializer=_init_worker,
        initargs=(str(args.scanfi_dir), grid, burn_geometries),
    ) as pool:
        for completed, result in enumerate(
            pool.imap_unordered(summarise_strip, range(strip_count), chunksize=1),
            start=1,
        ):
            start_row = result["block_row_start"]
            end_row = start_row + result["block_rows"]
            counts[:, start_row:end_row, :] = result["counts"]
            pixel_totals += result["pixel_totals"]
            if completed % 10 == 0 or completed == strip_count:
                elapsed = time.time() - start
                print(
                    f"  {completed}/{strip_count} strips ({elapsed:.0f} s)", flush=True
                )

    national_treed_ha = float(pixel_totals[V2_CASE_INDEX["treed"]]) * PIXEL_HA

    managed, _managed_summary, covered = v1.load_managed_shares(args)
    unit_frame = build_unit_frame(
        counts,
        province_ids,
        ecoprovince_ids,
        ecoprovinces,
        province_names,
        road_distance_m,
        managed,
        covered,
    )
    ecoprovince_out, province_out = build_outputs(unit_frame)
    national = national_case_areas(ecoprovince_out)

    v1_summary_path = args.v1_dir / "operable_proxy_summary.json"
    if not v1_summary_path.exists():
        raise FileNotFoundError(f"required v1 summary missing: {v1_summary_path}")
    v1_summary = json.loads(v1_summary_path.read_text(encoding="utf-8"))

    known_hashes = v1.read_known_hashes(args.scanfi_manifest)
    input_metadata: dict[str, Any] = {}
    for name, filename in SCANFI_PATHS.items():
        path = args.scanfi_dir / filename
        known = known_hashes.get(filename, {})
        input_metadata[f"scanfi_{name}"] = {
            "path": str(path.relative_to(REPO_ROOT)),
            "sha256": known.get("sha256"),
            "bytes": known.get("bytes", path.stat().st_size),
        }
    input_metadata["v1_operable_summary"] = {
        "path": str(v1_summary_path.relative_to(REPO_ROOT)),
        "sha256": sha256(v1_summary_path),
    }
    input_metadata["v1_managed_ecoprovince"] = {
        "path": str(
            (args.managed_dir / "managed_landbase_by_ecoprovince.csv").relative_to(
                REPO_ROOT
            )
        ),
        "sha256": sha256(args.managed_dir / "managed_landbase_by_ecoprovince.csv"),
    }
    input_metadata["nbac_2020"] = {
        "path": str(args.nbac.relative_to(REPO_ROOT)),
        "sha256": sha256(args.nbac),
        "bytes": args.nbac.stat().st_size,
        "shapefile_member": nbac_metadata["shapefile_member"],
        "features": nbac_metadata["features"],
        "years": nbac_metadata["years"],
    }
    nrn_inputs = []
    for layer in nrn_layers:
        zip_path = REPO_ROOT / layer["zip"]
        nrn_inputs.append(
            {
                "province": layer["province"],
                "zip": layer["zip"],
                "sha256": sha256(zip_path),
                "bytes": zip_path.stat().st_size,
                "gpkg_member": layer["gpkg_member"],
                "road_layer": layer["road_layer"],
                "features": layer["features"],
            }
        )
    input_metadata["nrn"] = {"available": road_available, "bundles": nrn_inputs}
    input_metadata["provinces"] = {
        "path": str(args.provinces.relative_to(REPO_ROOT)),
        "sha256": sha256(args.provinces),
    }
    input_metadata["ecodistricts"] = {
        "path": str(args.rsyc.relative_to(REPO_ROOT)),
        "sha256": sha256(args.rsyc),
    }

    checks = validate(
        unit_frame,
        ecoprovince_out,
        province_out,
        national,
        strip_residual,
        args.area_weights,
        v1_summary,
        road_available,
    )
    checks["national_treed_ha_from_pixels"] = national_treed_ha
    checks["blocks_filled_by_nearest_province"] = int(province_fill)
    checks["blocks_filled_by_nearest_ecodistrict"] = int(district_fill)

    summary = build_summary(
        args,
        time.time() - start,
        unit_frame,
        ecoprovince_out,
        province_out,
        national,
        checks,
        input_metadata,
        nrn_layers,
        nbac_metadata,
        road_available,
    )

    province_path = out_dir / "operable_proxy_v2_by_province.csv"
    ecoprovince_path = out_dir / "operable_proxy_v2_by_ecoprovince.csv"
    summary_path = out_dir / "operable_proxy_v2_summary.json"
    report_path = out_dir / "OPERABLE_PROXY_V2_REPORT.md"
    road_path = out_dir / "road_distance_classes.csv"
    gaps_path = out_dir / "gaps.csv"
    manifest_path = out_dir / "manifest.json"

    province_out.to_csv(province_path, index=False)
    ecoprovince_out.to_csv(ecoprovince_path, index=False)
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    write_report(report_path, summary, province_out)

    road_rows = []
    for _, row in province_out[province_out["screen_case"] == "base"].iterrows():
        province = row["province"]
        base_rows = unit_frame[unit_frame["province"] == province]
        road_rows.append(
            {
                "province": province,
                "base_merchantable_ha": float(base_rows["base_merchantable_ha"].sum()),
                "road_0_5km_ha": float(base_rows["road_0_5km_ha"].sum()),
                "road_5_10km_ha": float(base_rows["road_5_10km_ha"].sum()),
                "road_10_20km_ha": float(base_rows["road_10_20km_ha"].sum()),
                "road_gt_20km_ha": float(base_rows["road_gt_20km_ha"].sum()),
            }
        )
    road_out = pd.DataFrame(road_rows)
    road_out.to_csv(road_path, index=False)
    pd.DataFrame(GAPS).to_csv(gaps_path, index=False)

    output_paths = (
        province_path,
        ecoprovince_path,
        summary_path,
        report_path,
        road_path,
        gaps_path,
    )
    manifest = {
        "schema_version": "operable_proxy_v2_manifest_v1",
        "product": summary["product"],
        "status": summary["status"],
        "generated_at_utc": summary["generated_at_utc"],
        "runtime_seconds": summary["runtime_seconds"],
        "command": summary["command"],
        "software": {
            "python": sys.version.split()[0],
            "rasterio": rasterio.__version__,
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "geopandas": gpd.__version__,
            "pyogrio": pyogrio.__version__,
            "scipy": __import__("scipy").__version__,
        },
        "inputs": input_metadata,
        "screens": summary["screens"],
        "outputs": [
            {
                "path": path.name,
                "size_bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
            for path in output_paths
        ],
        "validation": checks,
        "gaps": list(GAPS),
        "caveats": summary["caveats"],
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    print(
        json.dumps(
            {
                "national_treed_ha": checks["national_treed_ha"],
                "base_operable_proxy_ha": checks["v2_base_operable_proxy_ha"],
                "age_ge_20_operable_ha": national["age_ge_20"]["operable_proxy_ha"],
                "age_ge_30_operable_ha": national["age_ge_30"]["operable_proxy_ha"],
                "screen_core20_operable_ha": national["screen_core20"][
                    "operable_proxy_ha"
                ],
                "screen_core30_operable_ha": national["screen_core30"][
                    "operable_proxy_ha"
                ],
                "all_checks_pass": checks["all_checks_pass"],
            },
            indent=2,
        ),
        flush=True,
    )
    print(f"wrote {out_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
