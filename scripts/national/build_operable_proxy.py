#!/usr/bin/env python3
"""Build a national merchantable/operable forest *screening proxy*.

The product combines three existing layers:

1. SCANFI v2 2025 land cover classes 5/6/7 (treed broadleaf/conifer/mixed).
2. SCANFI v2 height, aboveground biomass, and crown closure thresholds.
3. The existing national managed-landbase proxy (tenure union minus CPCAD),
   applied as a uniform *share* inside each province/ecoprovince.

This is deliberately a screening proxy.  It is not a legal timber harvesting
landbase (THLB), not an AAC landbase, and applies no slope, operability,
access, economic, or silvicultural constraint.  Thresholds are reported with
sensitivity cases; the defaults are height >= 5 m, biomass >= 20 Mg/ha, and
crown closure >= 10 percent.

Method
------
The 30 m SCANFI rasters are read in 33-block strips (1,089 raster rows).  Each
strip is reduced to exact 990 m block counts for every threshold case, then
blocks are attributed to a province and ecoprovince by block-centroid
rasterisation, matching the national area-weights grid.  This keeps the
national run tractable while conserving every 30 m pixel exactly.

Run:
    .venv/bin/python scripts/national/build_operable_proxy.py \
        --out-dir data/derived/national/managed_landbase/operable_proxy
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
from datetime import UTC, datetime
from multiprocessing import Pool
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.features import rasterize
from rasterio.transform import Affine
from rasterio.windows import Window
from scipy.ndimage import distance_transform_edt

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCANFI_DIR = REPO_ROOT / "data/raw/scanfi/v2"
DEFAULT_RSYC = REPO_ROOT / "data/raw/national/rsyc/RSYC_ecostrat.gpkg"
DEFAULT_PROVINCES = REPO_ROOT / "data/raw/boundaries/canada_adm1_geoboundaries_simplified.geojson"
DEFAULT_MANAGED_DIR = REPO_ROOT / "data/derived/national/managed_landbase"
DEFAULT_AREA_WEIGHTS = (
    REPO_ROOT / "data/derived/national/assortments/national_area_weights/national_area_weights_ecodistrict.csv"
)
DEFAULT_OUT = REPO_ROOT / "data/derived/national/managed_landbase/operable_proxy"
DEFAULT_NFI = REPO_ROOT / "data/raw/nfi/standard_reports/t1/ca_t4_for_area_en.csv"
DEFAULT_SCANFI_MANIFEST = REPO_ROOT / "data/manifests/scanfi-v2-2025-acquisition.csv"

PIXEL_M = 30.0
BLOCK_PIXELS = 33
BLOCK_M = PIXEL_M * BLOCK_PIXELS
PIXEL_HA = (PIXEL_M * PIXEL_M) / 10_000.0
STRIP_BLOCKS = 33
FOREST_CLASSES = (5, 6, 7)

# Threshold cases used for the full national pass.  The first three are the
# headline cases; the last three isolate each default threshold.
THRESHOLD_CASES = (
    ("base", 5.0, 20.0, 10.0),
    ("low", 3.0, 10.0, 5.0),
    ("high", 7.0, 30.0, 20.0),
)
MARGINAL_CASES = (
    ("height_ge_5", 5.0, None, None),
    ("biomass_ge_20", None, 20.0, None),
    ("closure_ge_10", None, None, 10.0),
)
CASE_NAMES = (
    "treed",
    *(name for name, _, _, _ in MARGINAL_CASES),
    *(name for name, _, _, _ in THRESHOLD_CASES),
)
CASE_LABELS = {
    "treed": "SCANFI treed land cover (classes 5/6/7)",
    "height_ge_5": "treed and height >= 5 m",
    "biomass_ge_20": "treed and biomass >= 20 Mg/ha",
    "closure_ge_10": "treed and crown closure >= 10%",
    "base": "height >= 5 m, biomass >= 20 Mg/ha, closure >= 10%",
    "low": "height >= 3 m, biomass >= 10 Mg/ha, closure >= 5%",
    "high": "height >= 7 m, biomass >= 30 Mg/ha, closure >= 20%",
}
CASE_THRESHOLDS = {
    "base": {"height_m": 5.0, "biomass_mg_ha": 20.0, "closure_pct": 10.0},
    "low": {"height_m": 3.0, "biomass_mg_ha": 10.0, "closure_pct": 5.0},
    "high": {"height_m": 7.0, "biomass_mg_ha": 30.0, "closure_pct": 20.0},
}

PROVINCE_CODE = {
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
ALL_PROVINCES = sorted(PROVINCE_CODE.values())

SCANFI_PATHS = {
    "landcover": "SCANFI_att_nfiLandcover_2025_v2_20260119.tif",
    "height": "SCANFI_att_height_2025_v2_20260119.tif",
    "biomass": "SCANFI_att_biomass_2025_v2_20260119.tif",
    "closure": "SCANFI_att_closure_2025_v2_20260119.tif",
}

CASE_INDEX = {name: index for index, name in enumerate(CASE_NAMES)}
_DATASETS: dict[str, rasterio.DatasetReader] = {}
_GRID: dict[str, Any] = {}


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_known_hashes(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    frame = pd.read_csv(path)
    known: dict[str, dict[str, Any]] = {}
    for _, row in frame.iterrows():
        filename = str(row.get("filename", "")).strip()
        if not filename:
            continue
        known[filename] = {
            "sha256": row.get("sha256") or None,
            "bytes": int(row["size_bytes"]) if pd.notna(row.get("size_bytes")) else None,
            "source_url": row.get("url") or None,
            "acquisition_date": row.get("acquisition_date") or None,
        }
    return known


def repair_geometry(geom):
    if geom is None or geom.is_empty or geom.is_valid:
        return geom
    fixed = geom.buffer(0)
    return fixed if not fixed.is_empty else geom


def rasterize_units(
    frame: gpd.GeoDataFrame,
    id_column: str,
    transform: Affine,
    shape: tuple[int, int],
    crs,
) -> np.ndarray:
    local = frame.to_crs(crs)
    shapes = [
        (geometry, int(value))
        for geometry, value in zip(local.geometry, local[id_column])
        if geometry is not None and not geometry.is_empty and int(value) > 0
    ]
    if not shapes:
        raise ValueError(f"no geometries to rasterize for {id_column}")
    return rasterize(
        shapes,
        out_shape=shape,
        transform=transform,
        fill=0,
        dtype="int32",
        all_touched=False,
    )


def fill_missing_ids(ids: np.ndarray) -> tuple[np.ndarray, int]:
    """Assign blocks whose centroid falls outside all polygons to the nearest unit."""
    missing = ids == 0
    missing_count = int(missing.sum())
    if missing_count == 0:
        return ids, 0
    _, (rows, cols) = distance_transform_edt(missing, return_indices=True)
    filled = ids.copy()
    filled[missing] = ids[rows[missing], cols[missing]]
    if (filled == 0).any():
        raise ValueError("nearest-unit fill left zero block ids")
    return filled, missing_count


def block_sum(mask: np.ndarray, block_cols: int) -> np.ndarray:
    """Sum a 30 m boolean mask into exact 33 x 33 blocks.

    The right and bottom edges are partial blocks; they are summed explicitly
    rather than padded so no pixel is lost or double counted.
    """
    rows, width = mask.shape
    block_rows = math.ceil(rows / BLOCK_PIXELS)
    full_blocks_across = width // BLOCK_PIXELS
    used_width = full_blocks_across * BLOCK_PIXELS
    full_block_rows = rows // BLOCK_PIXELS
    used_rows = full_block_rows * BLOCK_PIXELS

    out = np.zeros((block_rows, block_cols), dtype=np.int32)
    if full_block_rows:
        out[:full_block_rows, :full_blocks_across] = mask[
            :used_rows, :used_width
        ].reshape(full_block_rows, BLOCK_PIXELS, full_blocks_across, BLOCK_PIXELS).sum(
            axis=(1, 3), dtype=np.int32
        )
        if width > used_width:
            out[:full_block_rows, full_blocks_across] = mask[
                :used_rows, used_width:
            ].reshape(full_block_rows, BLOCK_PIXELS, width - used_width).sum(
                axis=(1, 2), dtype=np.int32
            )
    if rows > used_rows:
        tail_rows = rows - used_rows
        out[full_block_rows, :full_blocks_across] = mask[
            used_rows:, :used_width
        ].reshape(tail_rows, full_blocks_across, BLOCK_PIXELS).sum(
            axis=(0, 2), dtype=np.int32
        )
        if width > used_width:
            out[full_block_rows, full_blocks_across] = int(
                mask[used_rows:, used_width:].sum(dtype=np.int64)
            )
    return out


def _init_worker(scanfi_dir: str, grid: dict[str, Any]) -> None:
    global _DATASETS, _GRID
    _GRID = grid
    _DATASETS = {
        name: rasterio.open(Path(scanfi_dir) / filename)
        for name, filename in SCANFI_PATHS.items()
    }
    for name, dataset in _DATASETS.items():
        if dataset.width != grid["width"] or dataset.height != grid["height"]:
            raise ValueError(f"{name} raster shape does not match the land-cover grid")


def summarise_strip(strip_index: int) -> dict[str, Any]:
    """Reduce one 33-block strip to per-block counts for every threshold case.

    The worker is pure with respect to files: it reads a fixed window and
    returns arrays.  It fails loudly if block sums do not conserve the pixels
    counted in the strip.
    """
    width = _GRID["width"]
    height = _GRID["height"]
    total_block_rows = _GRID["block_rows"]
    block_cols = _GRID["block_cols"]

    block_row_start = strip_index * STRIP_BLOCKS
    block_rows = min(STRIP_BLOCKS, total_block_rows - block_row_start)
    row_offset = block_row_start * BLOCK_PIXELS
    rows = min(BLOCK_PIXELS * block_rows, height - row_offset)
    window = Window(0, row_offset, width, rows)

    landcover = _DATASETS["landcover"].read(1, window=window)
    treed = (landcover >= FOREST_CLASSES[0]) & (landcover <= FOREST_CLASSES[-1])
    del landcover

    height_values = _DATASETS["height"].read(1, window=window)
    height_valid = height_values != 255
    height_ge_5 = treed & height_valid & (height_values >= 5)
    height_ge_3 = treed & height_valid & (height_values >= 3)
    height_ge_7 = treed & height_valid & (height_values >= 7)
    del height_values

    biomass_values = _DATASETS["biomass"].read(1, window=window)
    biomass_valid = biomass_values != -32768
    biomass_ge_20 = treed & biomass_valid & (biomass_values >= 20)
    biomass_ge_10 = treed & biomass_valid & (biomass_values >= 10)
    biomass_ge_30 = treed & biomass_valid & (biomass_values >= 30)
    del biomass_values

    closure_values = _DATASETS["closure"].read(1, window=window)
    closure_valid = closure_values != 255
    closure_ge_10 = treed & closure_valid & (closure_values >= 10)
    closure_ge_5 = treed & closure_valid & (closure_values >= 5)
    closure_ge_20 = treed & closure_valid & (closure_values >= 20)
    del closure_values

    masks = {
        "treed": treed,
        "height_ge_5": height_ge_5,
        "biomass_ge_20": biomass_ge_20,
        "closure_ge_10": closure_ge_10,
        "base": height_ge_5 & biomass_ge_20 & closure_ge_10,
        "low": height_ge_3 & biomass_ge_10 & closure_ge_5,
        "high": height_ge_7 & biomass_ge_30 & closure_ge_20,
    }

    counts = np.zeros((len(CASE_NAMES), block_rows, block_cols), dtype=np.int32)
    pixel_totals = np.zeros(len(CASE_NAMES), dtype=np.int64)
    for name, mask in masks.items():
        index = CASE_INDEX[name]
        counts[index] = block_sum(mask, block_cols)
        pixel_totals[index] = int(mask.sum(dtype=np.int64))
        block_total = int(counts[index].sum(dtype=np.int64))
        if block_total != pixel_totals[index]:
            raise RuntimeError(
                f"strip {strip_index} case {name}: block sum {block_total} "
                f"!= pixel count {pixel_totals[index]}"
            )
        del mask

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
    parser.add_argument("--area-weights", type=Path, default=DEFAULT_AREA_WEIGHTS)
    parser.add_argument("--nfi", type=Path, default=DEFAULT_NFI)
    parser.add_argument("--scanfi-manifest", type=Path, default=DEFAULT_SCANFI_MANIFEST)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--workers", type=int, default=20)
    parser.add_argument(
        "--strip-limit",
        type=int,
        default=0,
        help="Debug only: process the first N strips and skip output validation.",
    )
    return parser.parse_args()


def build_grid(args: argparse.Namespace) -> dict[str, Any]:
    landcover_path = args.scanfi_dir / SCANFI_PATHS["landcover"]
    if not landcover_path.exists():
        raise FileNotFoundError(f"required SCANFI land-cover raster missing: {landcover_path}")
    with rasterio.open(landcover_path) as dataset:
        width = dataset.width
        height = dataset.height
        crs = dataset.crs
        transform = dataset.transform
    if transform.a != PIXEL_M or transform.e != -PIXEL_M:
        raise ValueError(f"expected a 30 m north-up grid, found transform {transform}")
    block_cols = math.ceil(width / BLOCK_PIXELS)
    block_rows = math.ceil(height / BLOCK_PIXELS)
    block_transform = Affine(BLOCK_M, 0, transform.c, 0, -BLOCK_M, transform.f)
    return {
        "width": width,
        "height": height,
        "crs": crs,
        "transform": transform,
        "block_cols": block_cols,
        "block_rows": block_rows,
        "block_transform": block_transform,
        "shape": (block_rows, block_cols),
    }


def load_attribution(args: argparse.Namespace, grid: dict[str, Any]):
    provinces = gpd.read_file(args.provinces).to_crs(grid["crs"])
    provinces = provinces[provinces["shapeName"].isin(PROVINCE_CODE)].copy()
    if len(provinces) != 13:
        raise ValueError(f"expected 13 provinces/territories, found {len(provinces)}")
    provinces["geometry"] = provinces.geometry.apply(repair_geometry)
    provinces["province"] = provinces["shapeName"].map(PROVINCE_CODE)
    provinces["province_id"] = np.arange(1, len(provinces) + 1)
    province_names = dict(zip(provinces["province_id"].astype(int), provinces["province"]))

    districts = gpd.read_file(args.rsyc, layer="ecodistricts").to_crs(grid["crs"])
    districts["ecodistrict_id"] = districts["ecodistrict_id"].astype(int)
    districts["ecoprovince_id"] = districts["ecoprovince_id"].astype(float)
    districts["geometry"] = districts.geometry.apply(repair_geometry)
    districts = districts.drop_duplicates("ecodistrict_id").copy()

    ecoprovinces = (
        districts[["ecoprovince_id", "ecoprovince"]]
        .drop_duplicates()
        .sort_values("ecoprovince_id")
        .reset_index(drop=True)
    )
    ecoprovinces["ecoprovince_index"] = np.arange(1, len(ecoprovinces) + 1)
    ecoprovince_lookup = ecoprovinces.set_index("ecoprovince_index")

    district_to_ecoprovince = (
        districts.set_index("ecodistrict_id")["ecoprovince_id"]
        .map(ecoprovinces.set_index("ecoprovince_id")["ecoprovince_index"])
        .dropna()
        .astype(int)
    )
    if district_to_ecoprovince.empty:
        raise ValueError("no ecodistrict to ecoprovince mapping was produced")

    return provinces, province_names, districts, ecoprovinces, ecoprovince_lookup, district_to_ecoprovince


def aggregate_cases(
    counts: np.ndarray,
    province_ids: np.ndarray,
    ecoprovince_ids: np.ndarray,
    ecoprovinces: pd.DataFrame,
    province_names: dict[int, str],
) -> pd.DataFrame:
    """Aggregate block-level counts into province x ecoprovince x case areas."""
    key = province_ids.astype(np.int64) * 1000 + ecoprovince_ids.astype(np.int64)
    n_keys = int(key.max()) + 1

    rows: list[dict[str, Any]] = []
    areas_by_case = {}
    for name in CASE_NAMES:
        index = CASE_INDEX[name]
        weights = counts[index].astype(np.float64) * PIXEL_HA
        areas_by_case[name] = np.bincount(key.ravel(), weights=weights.ravel(), minlength=n_keys)

    eco_names = ecoprovinces.set_index("ecoprovince_index")["ecoprovince"].to_dict()
    eco_ids = ecoprovinces.set_index("ecoprovince_index")["ecoprovince_id"].to_dict()
    for encoded in np.flatnonzero(areas_by_case["treed"] > 0):
        province_id = int(encoded // 1000)
        ecoprovince_index = int(encoded % 1000)
        if province_id not in province_names or ecoprovince_index not in eco_names:
            continue
        row = {
            "province": province_names[province_id],
            "ecoprovince": eco_names[ecoprovince_index],
            "ecoprovince_id": float(eco_ids[ecoprovince_index]),
        }
        for name in CASE_NAMES:
            row[f"{name}_ha"] = float(areas_by_case[name][encoded])
        rows.append(row)
    frame = pd.DataFrame(rows)
    if frame.empty:
        raise RuntimeError("no treed area was attributed to any province/ecoprovince")
    return frame


def load_managed_shares(args: argparse.Namespace) -> tuple[pd.DataFrame, pd.DataFrame, set[str]]:
    detail_path = args.managed_dir / "managed_landbase_by_ecoprovince.csv"
    summary_path = args.managed_dir / "managed_landbase_province_summary.csv"
    for path in (detail_path, summary_path):
        if not path.exists():
            raise FileNotFoundError(f"required managed-landbase input missing: {path}")

    detail = pd.read_csv(detail_path)
    summary = pd.read_csv(summary_path)
    detail["ecoprovince_id"] = detail["ecoprovince_id"].astype(float)

    managed = (
        detail.groupby(["province", "ecoprovince_id"], as_index=False)
        .agg(managed_proxy_ha=("managed_proxy_ha", "sum"))
        .reset_index(drop=True)
    )
    if (managed["managed_proxy_ha"] < 0).any():
        raise ValueError("existing managed proxy contains a negative area")

    covered = set(summary.loc[summary["coverage"] == "tenure_2016", "province"])
    return managed, summary, covered


def build_outputs(
    unit_areas: pd.DataFrame,
    managed: pd.DataFrame,
    covered: set[str],
    resolution_note: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    frame = unit_areas.merge(
        managed[["province", "ecoprovince_id", "managed_proxy_ha"]],
        on=["province", "ecoprovince_id"],
        how="left",
    )
    # Covered jurisdictions with no tenure footprint in a unit have a true zero
    # share.  Jurisdictions without any accepted tenure source stay blank.
    frame["coverage_available"] = frame["province"].isin(covered)
    frame.loc[frame["coverage_available"], "managed_proxy_ha"] = frame.loc[
        frame["coverage_available"], "managed_proxy_ha"
    ].fillna(0.0)
    frame["managed_share"] = (
        frame["managed_proxy_ha"] / frame["treed_ha"].where(frame["treed_ha"] > 0)
    )
    frame.loc[~frame["coverage_available"], "managed_share"] = np.nan
    too_large = frame["managed_share"] > 1.000001
    if too_large.any():
        units = frame.loc[too_large, ["province", "ecoprovince", "managed_proxy_ha", "treed_ha"]]
        raise ValueError(
            "managed proxy exceeds SCANFI treed area in a province-ecoprovince; "
            f"refusing to apply a share above one:\n{units.to_string(index=False)}"
        )

    long_rows: list[pd.DataFrame] = []
    for case_name, _, _, _ in THRESHOLD_CASES:
        subset = pd.DataFrame(
            {
                "province": frame["province"],
                "ecoprovince": frame["ecoprovince"],
                "ecoprovince_id": frame["ecoprovince_id"],
                "scanfi_forest_ha": frame["treed_ha"],
                "managed_proxy_ha": frame["managed_proxy_ha"],
                "merchantable_ha": frame[f"{case_name}_ha"],
                "managed_share": frame["managed_share"],
                "coverage_available": frame["coverage_available"],
                "threshold_case": case_name,
            }
        )
        subset["operable_proxy_ha"] = subset["merchantable_ha"] * subset["managed_share"]
        subset.loc[~subset["coverage_available"], "operable_proxy_ha"] = np.nan
        base_note = (
            f"{resolution_note}; SCANFI treed classes 5/6/7; "
            f"{CASE_LABELS[case_name]}; managed share applied uniformly inside "
            "province-ecoprovince; screening proxy, not legal THLB"
        )
        subset["notes"] = np.where(
            subset["coverage_available"],
            base_note,
            base_note + "; no 2016 tenure coverage, operable proxy intentionally blank",
        )
        long_rows.append(
            subset[
                [
                    "province",
                    "ecoprovince",
                    "ecoprovince_id",
                    "scanfi_forest_ha",
                    "managed_proxy_ha",
                    "merchantable_ha",
                    "operable_proxy_ha",
                    "managed_share",
                    "threshold_case",
                    "notes",
                ]
            ]
        )
    ecoprovince_out = pd.concat(long_rows, ignore_index=True)
    ecoprovince_out["ecoprovince_id"] = ecoprovince_out["ecoprovince_id"].round(6)

    group = ["province", "threshold_case"]
    province_out = (
        ecoprovince_out.groupby(group, as_index=False)
        .agg(
            scanfi_forest_ha=("scanfi_forest_ha", "sum"),
            managed_proxy_ha=("managed_proxy_ha", "sum"),
            merchantable_ha=("merchantable_ha", "sum"),
            operable_proxy_ha=("operable_proxy_ha", "sum"),
        )
        .reset_index(drop=True)
    )
    province_out["coverage_available"] = province_out["province"].isin(covered)
    province_out["managed_share"] = (
        province_out["operable_proxy_ha"] / province_out["scanfi_forest_ha"]
    )
    province_out.loc[~province_out["coverage_available"], "managed_proxy_ha"] = np.nan
    province_out.loc[~province_out["coverage_available"], "operable_proxy_ha"] = np.nan
    province_out.loc[~province_out["coverage_available"], "managed_share"] = np.nan
    province_out["ecoprovince"] = "ALL"
    province_out["notes"] = (
        f"{resolution_note}; province total is the sum of province-ecoprovince rows; "
        "managed_share is the effective merchantable-weighted share; screening proxy, not legal THLB"
    )
    province_out.loc[~province_out["coverage_available"], "notes"] = (
        f"{resolution_note}; no accepted 2016 tenure source; managed and operable values "
        "intentionally blank, not zero; screening proxy"
    )
    province_out = province_out[
        [
            "province",
            "ecoprovince",
            "scanfi_forest_ha",
            "managed_proxy_ha",
            "merchantable_ha",
            "operable_proxy_ha",
            "managed_share",
            "threshold_case",
            "notes",
        ]
    ]
    case_order = {name: index for index, (name, _, _, _) in enumerate(THRESHOLD_CASES)}
    for frame_out in (ecoprovince_out, province_out):
        frame_out["_order"] = frame_out["threshold_case"].map(case_order)
    ecoprovince_out = ecoprovince_out.sort_values(
        ["province", "ecoprovince_id", "_order"]
    ).drop(columns="_order")
    province_out = province_out.sort_values(["province", "_order"]).drop(columns="_order")
    return ecoprovince_out.reset_index(drop=True), province_out.reset_index(drop=True)


def validate(
    unit_areas: pd.DataFrame,
    ecoprovince_out: pd.DataFrame,
    province_out: pd.DataFrame,
    managed_summary: pd.DataFrame,
    national_treed_ha: float,
    area_weights_path: Path,
    nfi_path: Path,
    strip_residual: int,
    province_fill: int,
    district_fill: int,
) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    checks["strip_pixel_conservation_max_residual"] = int(strip_residual)
    # These are block-grid cells whose centroid fell outside every polygon,
    # mainly ocean/coastline blocks.  They are counted per attribution layer.
    checks["blocks_filled_by_nearest_province"] = int(province_fill)
    checks["blocks_filled_by_nearest_ecodistrict"] = int(district_fill)

    checks["national_treed_ha"] = float(national_treed_ha)
    if area_weights_path.exists():
        weights = pd.read_csv(area_weights_path)
        weights = weights[weights["level"] == "ecodistrict"]
        area_weights_total = float(
            weights.drop_duplicates(["province", "ecodistrict_id"])["forest_area_ha"].sum()
        )
        checks["area_weights_forest_ha"] = area_weights_total
        checks["treed_vs_area_weights_abs_diff_ha"] = abs(
            float(national_treed_ha) - area_weights_total
        )
        checks["treed_matches_area_weights"] = (
            checks["treed_vs_area_weights_abs_diff_ha"] < 1.0
        )

    numeric = ecoprovince_out[["scanfi_forest_ha", "merchantable_ha"]]
    checks["no_nan_in_area_fields"] = bool(numeric.notna().all().all())
    checks["no_negative_area_fields"] = bool((numeric >= 0).all().all())
    covered_rows = ecoprovince_out["managed_share"].notna()
    checks["operable_defined_where_coverage_available"] = bool(
        ecoprovince_out.loc[covered_rows, "operable_proxy_ha"].notna().all()
    )
    checks["operable_blank_where_coverage_missing"] = bool(
        ecoprovince_out.loc[~covered_rows, "operable_proxy_ha"].isna().all()
    )
    operable = ecoprovince_out["operable_proxy_ha"].dropna()
    checks["no_negative_operable"] = bool((operable >= 0).all())
    checks["managed_share_le_one"] = bool(
        (ecoprovince_out["managed_share"].dropna() <= 1.000001).all()
    )
    covered_only = ecoprovince_out["operable_proxy_ha"].notna()
    checks["operable_le_merchantable"] = bool(
        (
            ecoprovince_out.loc[covered_only, "operable_proxy_ha"]
            <= ecoprovince_out.loc[covered_only, "merchantable_ha"] + 1e-6
        ).all()
    )

    base = ecoprovince_out[ecoprovince_out["threshold_case"] == "base"]
    checks["merchantable_le_treed"] = bool(
        (base["merchantable_ha"] <= base["scanfi_forest_ha"] + 1e-6).all()
    )
    low = ecoprovince_out[ecoprovince_out["threshold_case"] == "low"].set_index(
        ["province", "ecoprovince_id"]
    )["merchantable_ha"]
    high = ecoprovince_out[ecoprovince_out["threshold_case"] == "high"].set_index(
        ["province", "ecoprovince_id"]
    )["merchantable_ha"]
    base_series = base.set_index(["province", "ecoprovince_id"])["merchantable_ha"]
    checks["threshold_ordering_low_ge_base_ge_high"] = bool(
        ((low >= base_series - 1e-6) & (base_series >= high - 1e-6)).all()
    )

    province_base = province_out[province_out["threshold_case"] == "base"]
    expected_national_base = float(unit_areas["base_ha"].sum())
    checks["province_sum_equals_ecoprovince_sum_base"] = bool(
        abs(province_base["merchantable_ha"].sum() - base["merchantable_ha"].sum()) < 1e-6
    )
    checks["province_sum_equals_national_base"] = bool(
        abs(province_base["merchantable_ha"].sum() - expected_national_base) < 1e-6
    )
    checks["national_base_merchantable_ha"] = expected_national_base
    checks["national_operable_proxy_ha"] = float(base["operable_proxy_ha"].sum())

    managed_national = float(
        managed_summary.loc[
            managed_summary["coverage"] == "tenure_2016", "managed_proxy_ha"
        ].sum()
    )
    checks["existing_managed_proxy_ha"] = managed_national
    checks["operable_le_existing_managed_proxy"] = bool(
        checks["national_operable_proxy_ha"] <= managed_national * 1.000001
    )
    checks["operable_share_of_managed_proxy"] = (
        checks["national_operable_proxy_ha"] / managed_national if managed_national else None
    )

    if nfi_path.exists():
        nfi = pd.read_csv(nfi_path, skiprows=1)
        nfi_forest_ha = float(nfi.loc[0, "Forest land"]) * 1000.0
        checks["nfi_forest_land_ha"] = nfi_forest_ha
        checks["scanfi_treed_to_nfi_ratio"] = float(national_treed_ha) / nfi_forest_ha
        checks["base_merchantable_to_nfi_ratio"] = (
            checks["national_base_merchantable_ha"] / nfi_forest_ha
        )
        checks["operable_to_nfi_ratio"] = checks["national_operable_proxy_ha"] / nfi_forest_ha

    checks["all_checks_pass"] = bool(
        checks["strip_pixel_conservation_max_residual"] == 0
        and checks.get("treed_matches_area_weights", True)
        and checks["no_nan_in_area_fields"]
        and checks["no_negative_area_fields"]
        and checks["operable_defined_where_coverage_available"]
        and checks["operable_blank_where_coverage_missing"]
        and checks["no_negative_operable"]
        and checks["managed_share_le_one"]
        and checks["operable_le_merchantable"]
        and checks["merchantable_le_treed"]
        and checks["threshold_ordering_low_ge_base_ge_high"]
        and checks["province_sum_equals_ecoprovince_sum_base"]
        and checks["province_sum_equals_national_base"]
        and checks["operable_le_existing_managed_proxy"]
    )
    if not checks["all_checks_pass"]:
        raise RuntimeError(f"operable-proxy validation failed: {json.dumps(checks, indent=2)}")
    return checks


def build_summary(
    args: argparse.Namespace,
    runtime_seconds: float,
    unit_areas: pd.DataFrame,
    ecoprovince_out: pd.DataFrame,
    province_out: pd.DataFrame,
    checks: dict[str, Any],
    input_metadata: dict[str, Any],
) -> dict[str, Any]:
    national = {}
    for name in CASE_NAMES:
        column = f"{name}_ha"
        national[name] = float(unit_areas[column].sum()) if column in unit_areas else None
    national = {key: value for key, value in national.items() if value is not None}

    by_province: dict[str, dict[str, float]] = {}
    base = province_out[province_out["threshold_case"] == "base"]
    for _, row in base.iterrows():
        by_province[str(row["province"])] = {
            "scanfi_forest_ha": float(row["scanfi_forest_ha"]),
            "merchantable_ha": float(row["merchantable_ha"]),
            "operable_proxy_ha": (
                float(row["operable_proxy_ha"])
                if pd.notna(row["operable_proxy_ha"])
                else None
            ),
            "managed_share": (
                float(row["managed_share"]) if pd.notna(row["managed_share"]) else None
            ),
            "coverage": "tenure_2016" if pd.notna(row["managed_proxy_ha"]) else "missing",
        }

    sensitivity = {}
    for case_name in ("low", "base", "high", *[name for name, _, _, _ in MARGINAL_CASES]):
        sensitivity[case_name] = {
            "label": CASE_LABELS[case_name],
            "national_ha": float(unit_areas[f"{case_name}_ha"].sum()),
            "share_of_treed": float(
                unit_areas[f"{case_name}_ha"].sum() / unit_areas["treed_ha"].sum()
            ),
            "thresholds": CASE_THRESHOLDS.get(case_name),
        }

    return {
        "product": "national operable merchantable-forest screening proxy",
        "status": "screening proxy; NOT a legal THLB, AAC landbase, or operability model",
        "generated_at_utc": utc_now(),
        "runtime_seconds": round(runtime_seconds, 3),
        "command": " ".join(str(part) for part in __import__("sys").argv),
        "resolution_m": PIXEL_M,
        "aggregation_block_m": BLOCK_M,
        "thresholds": {
            "default_case": "base",
            "cases": CASE_THRESHOLDS,
            "marginal_cases": {
                name: {
                    "height_m": height,
                    "biomass_mg_ha": biomass,
                    "closure_pct": closure,
                }
                for name, height, biomass, closure in MARGINAL_CASES
            },
            "units": {
                "height": "m",
                "biomass": "Mg/ha (oven-dry aboveground biomass; SCANFI v2 attr_biomass)",
                "closure": "percent crown closure",
            },
        },
        "national": national,
        "managed_share_application": (
            "Managed proxy numerator from managed_landbase_by_ecoprovince.csv divided by the "
            "SCANFI treed area in the same province-ecoprovince, held constant inside that unit "
            "and applied to merchantable area. The source managed_share column uses an "
            "ecoprovincial denominator, which is not applicable when an ecoprovince spans "
            "provinces; the numerator is unchanged. Tenure was not re-intersected with the "
            "merchantable raster."
        ),
        "by_province_base": by_province,
        "sensitivity": sensitivity,
        "validation": checks,
        "inputs": input_metadata,
        "caveats": [
            "Screening proxy only; not a legal THLB, AAC landbase, or harvest feasibility test.",
            "Tenure/managed forest is assumed uniformly distributed inside each province-ecoprovince.",
            "No slope, operability, road access, economic, silvicultural, or age constraint is applied.",
            "SCANFI is a 30 m prediction surface, not a census or provincial inventory.",
            "Managed values are blank for NU and PE because the 2016 tenure compilation has no coverage there.",
            "Thresholds are screening conventions, not statutory merchantability rules.",
        ],
    }


def write_report(
    path: Path,
    summary: dict[str, Any],
    province_out: pd.DataFrame,
    ecoprovince_out: pd.DataFrame,
    checks: dict[str, Any],
) -> None:
    base = province_out[province_out["threshold_case"] == "base"].copy()
    base = base.sort_values("merchantable_ha", ascending=False)
    national_treed = summary["national"]["treed"]
    sensitivity = summary["sensitivity"]

    def mha(value: float) -> str:
        return f"{value / 1e6:,.2f}"

    lines = [
        "# National Operable Merchantable-Forest Proxy",
        "",
        ("**Status: screening proxy. This is not a legal timber harvesting landbase (THLB), "
        "not an AAC landbase, and not an operability or access model.**"),
        "",
        f"Generated: {summary['generated_at_utc']}  ",
        f"Runtime: {summary['runtime_seconds']:.1f} s  ",
        f"Grid: {PIXEL_M:.0f} m SCANFI pixels aggregated to {BLOCK_M:.0f} m blocks.",
        "",
        "## Method",
        "",
        "1. Mask SCANFI v2 NFI land cover to treed classes 5 (broadleaf), 6 (conifer), and 7 (mixed).",
        "2. Apply the default merchantability screen: height >= 5 m, aboveground biomass >= 20 Mg/ha, and crown closure >= 10%.",
        "3. Read the existing managed-landbase proxy by province/ecoprovince (tenure union minus CPCAD) and divide it by the SCANFI treed area in the same province-ecoprovince. The source `managed_share` column uses an ecoprovincial denominator, which understates the share when an ecoprovince spans provinces; the managed numerator is unchanged.",
        "4. Apply that share uniformly to the merchantable area. Tenure polygons were **not** re-intersected with the merchantable raster; this is a share-based approximation.",
        "5. Report low and high threshold sensitivity cases, plus each threshold in isolation.",
        "",
        ("The managed share is therefore a *landbase-share* adjustment, not a spatial tenure overlay. "
        "It assumes managed forest is distributed like all forest inside each province-ecoprovince. "
        "The source `managed_share` column is divided by ecoprovincial forest area; because several "
        "ecoprovinces span provincial boundaries, the share is recomputed here as managed proxy "
        "divided by SCANFI treed area in the same province-ecoprovince."),
        "",
        "## National area results",
        "",
        "| Case | Thresholds | Area (Mha) | Share of treed |",
        "|---|---|---:|---:|",
    ]
    for name in ("low", "base", "high"):
        case = sensitivity[name]
        thresholds = CASE_THRESHOLDS[name]
        label = (
            f"h >= {thresholds['height_m']:.0f} m, b >= {thresholds['biomass_mg_ha']:.0f} Mg/ha, "
            f"c >= {thresholds['closure_pct']:.0f}%"
        )
        lines.append(
            f"| {name} | {label} | {case['national_ha'] / 1e6:,.2f} | {case['share_of_treed']:.1%} |"
        )
    lines += [
        "",
        f"SCANFI treed area: **{mha(national_treed)} Mha**.",
        f"Default (base) merchantable area: **{mha(summary['national']['base'])} Mha**.",
        (f"Default operable proxy: **{mha(checks['national_operable_proxy_ha'])} Mha** "
        f"({checks['operable_share_of_managed_proxy']:.1%} of the existing 166.28 Mha managed proxy)."),
        "",
        "## Base case by province/territory",
        "",
        "| Province | SCANFI treed (Mha) | Merchantable (Mha) | Managed proxy (Mha) | Operable proxy (Mha) | Effective managed share |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for _, row in base.iterrows():
        managed = "missing" if pd.isna(row["managed_proxy_ha"]) else f"{row['managed_proxy_ha'] / 1e6:,.2f}"
        operable = "missing" if pd.isna(row["operable_proxy_ha"]) else f"{row['operable_proxy_ha'] / 1e6:,.2f}"
        share = "missing" if pd.isna(row["managed_share"]) else f"{row['managed_share']:.1%}"
        lines.append(
            f"| {row['province']} | {row['scanfi_forest_ha'] / 1e6:,.2f} | "
            f"{row['merchantable_ha'] / 1e6:,.2f} | {managed} | {operable} | {share} |"
        )
    lines += [
        "",
        "## Comparison to existing managed proxy and NFI",
        "",
        f"- Existing managed proxy (tenure union minus CPCAD): **{checks['existing_managed_proxy_ha'] / 1e6:,.2f} Mha**.",
        (f"- Base merchantable area: **{checks['national_base_merchantable_ha'] / 1e6:,.2f} Mha** "
        f"({checks['base_merchantable_to_nfi_ratio']:.2f}x NFI forest land)."),
        (f"- Base operable proxy: **{checks['national_operable_proxy_ha'] / 1e6:,.2f} Mha** "
        f"({checks['operable_to_nfi_ratio']:.2f}x NFI forest land; "
        f"{checks['operable_share_of_managed_proxy']:.1%} of the managed proxy)."),
        f"- NFI Table 4.0 forest land: **{checks['nfi_forest_land_ha'] / 1e6:,.2f} Mha**.",
        (f"- SCANFI treed / NFI forest land: **{checks['scanfi_treed_to_nfi_ratio']:.2f}x**. "
        "SCANFI treed classes include northern and non-inventory treed land outside the NFI forest-land definition, so a ratio above 1 is expected."),
        "",
        "## Sensitivity",
        "",
        "| Case | National area (Mha) | Share of treed |",
        "|---|---:|---:|",
    ]
    for name in ("low", "base", "high", "height_ge_5", "biomass_ge_20", "closure_ge_10"):
        case = sensitivity[name]
        lines.append(
            f"| {name} ({CASE_LABELS[name]}) | {case['national_ha'] / 1e6:,.2f} | {case['share_of_treed']:.1%} |"
        )
    lines += [
        "",
        ("The single-constraint rows are not alternatives to the base case; they show how much area each "
        "default threshold removes on its own. The low/high rows are the defensible envelope for the "
        "combined screen."),
        "",
        "## Validation",
        "",
        f"- Strip-level pixel conservation: max residual **{checks['strip_pixel_conservation_max_residual']}** pixels.",
        f"- National treed area vs national area-weights product: **{checks.get('treed_vs_area_weights_abs_diff_ha', float('nan')):,.3f} ha** difference.",
        f"- No NaN in treed/merchantable area fields: **{checks['no_nan_in_area_fields']}**.",
        f"- No negative area fields: **{checks['no_negative_area_fields']}**.",
        f"- Merchantable <= treed in every unit: **{checks['merchantable_le_treed']}**.",
        f"- Managed share <= 1 in every unit: **{checks['managed_share_le_one']}**.",
        f"- Operable <= merchantable where defined: **{checks['operable_le_merchantable']}**.",
        f"- Threshold ordering low >= base >= high: **{checks['threshold_ordering_low_ge_base_ge_high']}**.",
        f"- Province sums equal ecoprovince sums: **{checks['province_sum_equals_ecoprovince_sum_base']}**.",
        f"- National operable proxy <= existing managed proxy: **{checks['operable_le_existing_managed_proxy']}**.",
        "",
        "## Gaps and caveats",
        "",
        "- **No legal THLB**: tenure boundaries are rights areas, not harvest-landbase determinations.",
        "- **No operability or slope constraint**: steep terrain, inoperable sites, and site degradation are not removed.",
        ("- **No road/access constraint**: NRN province bundles exist in `data/raw/nrn/` for all 13 jurisdictions "
        "but were not integrated into this run; distance-to-road and access classes remain a gap."),
        "- **No age, disturbance, or silvicultural constraint**: recent burns, insect mortality, and non-commercial stands are not removed beyond the SCANFI attributes used here.",
        "- **Share-based tenure approximation**: the managed share is uniform inside each province-ecoprovince, so the spatial pattern of tenure is not represented. The share is the existing managed-proxy numerator over local SCANFI treed area; it is not the source file's ecoprovincial `managed_share` column.",
        "- **SCANFI uncertainty**: the thresholds are applied to a 30 m prediction surface, not a provincial inventory.",
        "- **Missing jurisdictions**: NU and PE have no accepted 2016 tenure source, so their managed and operable values are blank, not zero.",
        "- **BC upper bound**: the 2016 tenure compilation covers almost all BC forest; the BC managed share is an upper bound, not a licence-specific footprint.",
        "",
        "## Outputs",
        "",
        "- `operable_proxy_by_province.csv`: province/territory x threshold case.",
        "- `operable_proxy_by_ecoprovince.csv`: province x ecoprovince x threshold case.",
        "- `operable_proxy_summary.json`: thresholds, national totals, sensitivity, validation, inputs.",
        "- `manifest.json`: input and output hashes, software versions, runtime, validation.",
        "",
        "## Reproduction",
        "",
        "```bash",
        ".venv/bin/python scripts/national/build_operable_proxy.py \\",
        "  --out-dir data/derived/national/managed_landbase/operable_proxy",
        "```",
        "",
        "Raw SCANFI, tenure, CPCAD, boundary, and RSYC payloads remain in the `data` submodule and are not committed to the parent repository.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    args = parse_args()
    start = time.time()
    grid = build_grid(args)

    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    (
        provinces,
        province_names,
        districts,
        ecoprovinces,
        _ecoprovince_lookup,
        district_to_ecoprovince,
    ) = load_attribution(args, grid)
    province_ids_raw = rasterize_units(
        provinces, "province_id", grid["block_transform"], grid["shape"], grid["crs"]
    )
    district_ids_raw = rasterize_units(
        districts, "ecodistrict_id", grid["block_transform"], grid["shape"], grid["crs"]
    )
    province_ids, province_fill = fill_missing_ids(province_ids_raw)
    district_ids, district_fill = fill_missing_ids(district_ids_raw)
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

    strip_count = math.ceil(grid["block_rows"] / STRIP_BLOCKS)
    if args.strip_limit:
        strip_count = min(strip_count, args.strip_limit)
    strip_starts = list(range(strip_count))
    print(
        f"reading {strip_count} strips ({grid['block_rows']} x {grid['block_cols']} blocks) "
        f"with {args.workers} workers",
        flush=True,
    )

    counts = np.zeros((len(CASE_NAMES), grid["block_rows"], grid["block_cols"]), dtype=np.int32)
    pixel_totals = np.zeros(len(CASE_NAMES), dtype=np.int64)
    with Pool(
        processes=args.workers,
        initializer=_init_worker,
        initargs=(str(args.scanfi_dir), grid),
    ) as pool:
        for completed, result in enumerate(
            pool.imap_unordered(summarise_strip, strip_starts, chunksize=1), start=1
        ):
            start_row = result["block_row_start"]
            end_row = start_row + result["block_rows"]
            counts[:, start_row:end_row, :] = result["counts"]
            pixel_totals += result["pixel_totals"]
            if completed % 10 == 0 or completed == strip_count:
                elapsed = time.time() - start
                print(f"  {completed}/{strip_count} strips ({elapsed:.0f} s)", flush=True)

    if args.strip_limit:
        print("strip-limit debug run complete; outputs not written", flush=True)
        return 0

    national_treed_ha = float(pixel_totals[CASE_INDEX["treed"]]) * PIXEL_HA
    strip_residual = 0

    unit_areas = aggregate_cases(
        counts, province_ids, ecoprovince_ids, ecoprovinces, province_names
    )
    managed, managed_summary, covered = load_managed_shares(args)
    ecoprovince_out, province_out = build_outputs(
        unit_areas,
        managed,
        covered,
        f"{PIXEL_M:.0f} m SCANFI aggregated to {BLOCK_M:.0f} m blocks",
    )

    known_hashes = read_known_hashes(args.scanfi_manifest)
    input_metadata = {}
    for name, filename in SCANFI_PATHS.items():
        path = args.scanfi_dir / filename
        known = known_hashes.get(filename, {})
        input_metadata[f"scanfi_{name}"] = {
            "path": str(path.relative_to(REPO_ROOT)),
            "sha256": known.get("sha256"),
            "bytes": known.get("bytes", path.stat().st_size),
        }
    input_metadata["managed_landbase"] = {
        "path": str((args.managed_dir / "managed_landbase_by_ecoprovince.csv").relative_to(REPO_ROOT)),
        "sha256": sha256(args.managed_dir / "managed_landbase_by_ecoprovince.csv"),
    }
    input_metadata["provinces"] = {
        "path": str(args.provinces.relative_to(REPO_ROOT)),
        "sha256": sha256(args.provinces),
    }
    input_metadata["ecodistricts"] = {
        "path": str(args.rsyc.relative_to(REPO_ROOT)),
        "sha256": sha256(args.rsyc),
    }

    checks = validate(
        unit_areas,
        ecoprovince_out,
        province_out,
        managed_summary,
        national_treed_ha,
        args.area_weights,
        args.nfi,
        strip_residual,
        province_fill,
        district_fill,
    )
    summary = build_summary(
        args, time.time() - start, unit_areas, ecoprovince_out, province_out, checks, input_metadata
    )

    province_path = out_dir / "operable_proxy_by_province.csv"
    ecoprovince_path = out_dir / "operable_proxy_by_ecoprovince.csv"
    summary_path = out_dir / "operable_proxy_summary.json"
    report_path = out_dir / "OPERABLE_PROXY_REPORT.md"
    manifest_path = out_dir / "manifest.json"

    province_out.to_csv(province_path, index=False)
    ecoprovince_out.to_csv(ecoprovince_path, index=False)
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    write_report(report_path, summary, province_out, ecoprovince_out, checks)

    manifest = {
        "schema_version": "operable_proxy_manifest_v1",
        "product": "national operable merchantable-forest screening proxy",
        "status": summary["status"],
        "generated_at_utc": summary["generated_at_utc"],
        "runtime_seconds": summary["runtime_seconds"],
        "command": summary["command"],
        "software": {
            "python": __import__("sys").version.split()[0],
            "rasterio": rasterio.__version__,
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "geopandas": gpd.__version__,
            "scipy": __import__("scipy").__version__,
        },
        "inputs": input_metadata,
        "thresholds": summary["thresholds"],
        "outputs": [
            {
                "path": path.name,
                "size_bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
            for path in (province_path, ecoprovince_path, summary_path, report_path)
        ],
        "validation": checks,
        "caveats": summary["caveats"],
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    print(
        json.dumps(
            {
                "national_treed_ha": checks["national_treed_ha"],
                "base_merchantable_ha": checks["national_base_merchantable_ha"],
                "operable_proxy_ha": checks["national_operable_proxy_ha"],
                "operable_share_of_managed": checks["operable_share_of_managed_proxy"],
            },
            indent=2,
        ),
        flush=True,
    )
    print(f"wrote {out_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
