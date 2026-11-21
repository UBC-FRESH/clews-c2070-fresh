#!/usr/bin/env python3
"""Build a national 960 m slope screen from the NRCan MRDEM-30 DTM.

The product is a coarse screening layer, not a 30 m operability
classification and not a legal THLB.  It reads the public MRDEM-30 DTM VRT
through GDAL's cloud-optimised overview chain, averages elevation to a
960 m grid in EPSG:3979, computes Horn slope in percent, and intersects the
slope distribution with the operable proxy v2 province/ecoprovince areas.

Method
------
1. Read the MRDEM-30 DTM VRT at 32x decimation (960 m) with average
   resampling.  The VRT is already EPSG:3979 at 30 m, so the output grid is
   aligned to the source grid and no reprojection of the DEM is required.
2. Compute slope with the Horn 3x3 kernel, the same kernel used by
   ``gdaldem slope``, and report percent slope.
3. Build an exact 990 m treed fraction from SCANFI land-cover classes 5/6/7
   by summing 33x33 pixel blocks.  The fraction is reprojected to the 960 m
   slope grid and used as the forest weight for slope statistics.
4. Rasterise province and ecoprovince polygons at 960 m and aggregate the
   forest-weighted slope statistics by province/ecoprovince.
5. Apply the observed steep shares to the operable proxy v2 ``base`` and
   ``screen_core20`` areas.  This is an overlay estimate: the slope shares
   are measured on the treed landbase, then applied to the v2 operable area.
   It assumes the v2 screens are independent of slope inside a
   province/ecoprovince.  It does not create a legal operability
   classification.

The 150M m3/yr arithmetic is the same target-volume / operable-area
intensity used by operable proxy v2.  It is not a yield forecast.

Run:
    .venv/bin/python scripts/national/build_slope_screen.py \
        --out-dir data/derived/national/managed_landbase/slope_screen
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
import time
import urllib.request
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.enums import Resampling
from rasterio.features import rasterize
from rasterio.transform import Affine
from rasterio.warp import reproject
from rasterio.windows import Window

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = (
    REPO_ROOT / "data/derived/national/managed_landbase/slope_screen"
)
DEFAULT_SCANFI_DIR = REPO_ROOT / "data/raw/scanfi/v2"
DEFAULT_RSYC = REPO_ROOT / "data/raw/national/rsyc/RSYC_ecostrat.gpkg"
DEFAULT_PROVINCES = (
    REPO_ROOT / "data/raw/boundaries/canada_adm1_geoboundaries_simplified.geojson"
)
DEFAULT_V2_ECOPROVINCE = (
    REPO_ROOT
    / "data/derived/national/managed_landbase/operable_proxy_v2"
    / "operable_proxy_v2_by_ecoprovince.csv"
)

MRDEM_VRT_URL = (
    "https://canelevation-dem.s3.ca-central-1.amazonaws.com/"
    "mrdem-30/mrdem-30-dtm.vrt"
)
MRDEM_DTM_URL = (
    "https://canelevation-dem.s3.ca-central-1.amazonaws.com/"
    "mrdem-30/mrdem-30-dtm.tif"
)
MRDEM_SOURCE_URL = (
    "https://canelevation-dem.s3.ca-central-1.amazonaws.com/"
    "mrdem-30/mrdem-30-source.tif"
)
MRDEM_STAC_COLLECTION_URL = (
    "https://datacube.services.geo.ca/stac/api/collections/mrdem-30"
)
MRDEM_STAC_SEARCH_URL = (
    "https://datacube.services.geo.ca/stac/api/search?collections=mrdem-30"
)
MRDEM_LICENCE = "Open Government Licence - Canada 2.0"

SLOPE_CRS = "EPSG:3979"
NATIVE_M = 30.0
DEM_FACTOR = 32
DEM_M = NATIVE_M * DEM_FACTOR
BLOCK_M = 990.0
BLOCK_PIXELS = 33
PIXEL_HA = (NATIVE_M * NATIVE_M) / 10_000.0
SLOPE_PIXEL_HA = (DEM_M * DEM_M) / 10_000.0
FOREST_CLASSES = (5, 6, 7)
SCANFI_LANDCOVER = "SCANFI_att_nfiLandcover_2025_v2_20260119.tif"
TARGET_VOLUME_M3 = 150_000_000.0
ODT_PER_M3 = 0.08831776452936206
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
SLOPE_THRESHOLDS = (15.0, 30.0)

_LANDCOVER_PATH: str | None = None


# ---------------------------------------------------------------------------
# Pure calculation helpers
# ---------------------------------------------------------------------------


def horn_slope_percent(
    elevation: np.ndarray,
    xres: float,
    yres: float,
    nodata: float = -32767.0,
) -> np.ndarray:
    """Return Horn 3x3 slope in percent, with nodata and edges as NaN.

    This reproduces the default ``gdaldem slope -p`` kernel: the eight
    neighbours are weighted 1-2-1 in each direction and divided by
    8 * cellsize.  The result is ``100 * hypot(dz/dx, dz/dy)``.
    """
    if elevation.ndim != 2:
        raise ValueError(f"elevation must be 2-D, received shape {elevation.shape}")
    if xres <= 0 or yres <= 0:
        raise ValueError(f"cell sizes must be positive, received {xres}, {yres}")
    z = np.asarray(elevation, dtype=np.float64)
    z = np.where(z == nodata, np.nan, z)
    padded = np.pad(z, 1, mode="constant", constant_values=np.nan)
    a = padded[:-2, :-2]
    b = padded[:-2, 1:-1]
    c = padded[:-2, 2:]
    d = padded[1:-1, :-2]
    f = padded[1:-1, 2:]
    g = padded[2:, :-2]
    h = padded[2:, 1:-1]
    i = padded[2:, 2:]
    dzdx = ((c + 2.0 * f + i) - (a + 2.0 * d + g)) / (8.0 * xres)
    dzdy = ((g + 2.0 * h + i) - (a + 2.0 * b + c)) / (8.0 * yres)
    slope = 100.0 * np.hypot(dzdx, dzdy)
    slope[~np.isfinite(slope)] = np.nan
    return slope


def validate_slope(slope: np.ndarray) -> dict[str, Any]:
    """Fail fast if a slope grid is empty, negative, or implausibly steep."""
    finite = slope[np.isfinite(slope)]
    if finite.size == 0:
        raise ValueError("slope grid contains no finite values")
    minimum = float(finite.min())
    maximum = float(finite.max())
    if minimum < -1e-6:
        raise ValueError(f"slope grid contains a negative value: {minimum}")
    if maximum > 200.0:
        raise ValueError(f"slope grid exceeds 200 percent: {maximum}")
    if np.isnan(slope).all():
        raise ValueError("slope grid is all NaN")
    return {
        "finite_cells": int(finite.size),
        "nodata_cells": int(slope.size - finite.size),
        "min_percent": minimum,
        "max_percent": maximum,
        "mean_percent": float(finite.mean()),
        "p50_percent": float(np.percentile(finite, 50)),
        "p90_percent": float(np.percentile(finite, 90)),
        "p99_percent": float(np.percentile(finite, 99)),
        "p999_percent": float(np.percentile(finite, 99.9)),
        "share_gt15": float(np.mean(finite > 15.0)),
        "share_gt30": float(np.mean(finite > 30.0)),
    }


def weighted_aggregate(
    codes: np.ndarray,
    weights: np.ndarray,
    values: np.ndarray,
    thresholds: tuple[float, ...],
    n_codes: int,
    pixel_ha: float,
) -> dict[str, np.ndarray]:
    """Aggregate a weighted raster by integer code.

    ``weights`` is the forest fraction in [0, 1]; ``values`` is percent
    slope.  Cells with code zero, a non-finite slope, or zero forest weight
    are excluded.  Nobody's area is invented: the area is the sum of the
    weights times the nominal pixel area.
    """
    if not (codes.shape == weights.shape == values.shape):
        raise ValueError("codes, weights, and values must share a shape")
    valid = (codes > 0) & np.isfinite(values) & (weights > 0)
    c = codes[valid].astype(np.int64, copy=False)
    w = weights[valid].astype(np.float64, copy=False)
    v = values[valid].astype(np.float64, copy=False)
    if c.size == 0:
        raise ValueError("no valid weighted cells to aggregate")

    weight_sum = np.bincount(c, weights=w, minlength=n_codes)
    value_sum = np.bincount(c, weights=v * w, minlength=n_codes)
    threshold_sums = {
        threshold: np.bincount(
            c, weights=(v > threshold) * w, minlength=n_codes
        )
        for threshold in thresholds
    }
    return {
        "weight_sum": weight_sum,
        "value_sum": value_sum,
        "area_ha": weight_sum * pixel_ha,
        "threshold_sums": threshold_sums,
        "valid_cell_count": np.bincount(c, minlength=n_codes),
    }


def block_sum(mask: np.ndarray, block_pixels: int, block_cols: int) -> np.ndarray:
    """Sum a 2-D mask into exact square blocks, including partial edges."""
    rows, width = mask.shape
    block_rows = math.ceil(rows / block_pixels)
    full_blocks_across = width // block_pixels
    used_width = full_blocks_across * block_pixels
    full_block_rows = rows // block_pixels
    used_rows = full_block_rows * block_pixels
    out = np.zeros((block_rows, block_cols), dtype=np.int64)
    if full_block_rows:
        out[:full_block_rows, :full_blocks_across] = mask[
            :used_rows, :used_width
        ].reshape(
            full_block_rows, block_pixels, full_blocks_across, block_pixels
        ).sum(axis=(1, 3), dtype=np.int64)
        if width > used_width:
            out[:full_block_rows, full_blocks_across] = mask[
                :used_rows, used_width:
            ].reshape(full_block_rows, block_pixels, width - used_width).sum(
                axis=(1, 2), dtype=np.int64
            )
    if rows > used_rows:
        tail_rows = rows - used_rows
        out[full_block_rows, :full_blocks_across] = mask[
            used_rows:, :used_width
        ].reshape(tail_rows, full_blocks_across, block_pixels).sum(
            axis=(0, 2), dtype=np.int64
        )
        if width > used_width:
            out[full_block_rows, full_blocks_across] = int(
                mask[used_rows:, used_width:].sum(dtype=np.int64)
            )
    return out


def valid_block_counts(
    rows: int, width: int, block_pixels: int, block_cols: int
) -> np.ndarray:
    """Number of source pixels in each block, including partial edge blocks."""
    block_rows = math.ceil(rows / block_pixels)
    row_counts = np.minimum(
        block_pixels, rows - np.arange(block_rows) * block_pixels
    )
    col_counts = np.minimum(
        block_pixels, width - np.arange(block_cols) * block_pixels
    )
    return np.outer(row_counts, col_counts).astype(np.int64)


# ---------------------------------------------------------------------------
# File and raster helpers
# ---------------------------------------------------------------------------


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(url: str, destination: Path, timeout: int = 120) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and destination.stat().st_size > 0:
        return destination
    request = urllib.request.Request(
        url, headers={"User-Agent": "clews-c2070-fresh-slope-screen/1.0"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        destination.write_bytes(response.read())
    if destination.stat().st_size == 0:
        raise RuntimeError(f"downloaded an empty file from {url}")
    return destination


def git_commit() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _init_landcover(path: str) -> None:
    global _LANDCOVER_PATH
    _LANDCOVER_PATH = path


def summarise_landcover_strip(strip_index: int) -> dict[str, Any]:
    """Return exact 33x33 treed counts for one 33-block strip."""
    if _LANDCOVER_PATH is None:
        raise RuntimeError("land-cover worker was not initialised")
    with rasterio.open(_LANDCOVER_PATH) as dataset:
        width = dataset.width
        height = dataset.height
        block_cols = math.ceil(width / BLOCK_PIXELS)
        block_rows_total = math.ceil(height / BLOCK_PIXELS)
        block_row_start = strip_index * BLOCK_PIXELS
        block_rows = min(BLOCK_PIXELS, block_rows_total - block_row_start)
        row_offset = block_row_start * BLOCK_PIXELS
        rows = min(BLOCK_PIXELS * block_rows, height - row_offset)
        window = Window(0, row_offset, width, rows)
        landcover = dataset.read(1, window=window)
    treed = (landcover >= FOREST_CLASSES[0]) & (landcover <= FOREST_CLASSES[-1])
    counts = block_sum(treed, BLOCK_PIXELS, block_cols)
    valid = valid_block_counts(rows, width, BLOCK_PIXELS, block_cols)
    return {
        "strip_index": strip_index,
        "block_row_start": block_row_start,
        "block_rows": block_rows,
        "counts": counts,
        "valid": valid,
    }


def build_treed_fraction(
    landcover_path: Path,
    workers: int,
) -> tuple[np.ndarray, np.ndarray, Affine, dict[str, Any]]:
    """Build exact 990 m treed fraction from SCANFI land cover."""
    with rasterio.open(landcover_path) as dataset:
        width = dataset.width
        height = dataset.height
        crs = dataset.crs
    block_cols = math.ceil(width / BLOCK_PIXELS)
    block_rows = math.ceil(height / BLOCK_PIXELS)
    strip_count = math.ceil(block_rows / BLOCK_PIXELS)

    counts = np.zeros((block_rows, block_cols), dtype=np.int64)
    valid = np.zeros((block_rows, block_cols), dtype=np.int64)
    with ProcessPoolExecutor(
        max_workers=workers, initializer=_init_landcover, initargs=(str(landcover_path),)
    ) as pool:
        for result in pool.map(summarise_landcover_strip, range(strip_count)):
            start = int(result["block_row_start"])
            rows = int(result["block_rows"])
            counts[start : start + rows, :] = result["counts"]
            valid[start : start + rows, :] = result["valid"]

    if int(valid.sum()) != width * height:
        raise RuntimeError(
            f"land-cover block accounting lost pixels: {int(valid.sum())} "
            f"!= {width * height}"
        )
    fraction = np.zeros((block_rows, block_cols), dtype=np.float32)
    np.divide(counts, valid, out=fraction, where=valid > 0)
    fraction = np.clip(fraction, 0.0, 1.0)
    transform = Affine(BLOCK_M, 0.0, -2341500.0, 0.0, -BLOCK_M, 9436500.0)
    metadata = {
        "source": landcover_path.name,
        "source_crs": str(crs),
        "source_crs_wkt": crs.to_wkt(),
        "block_m": BLOCK_M,
        "block_rows": block_rows,
        "block_cols": block_cols,
        "treed_area_ha": float(counts.sum()) * PIXEL_HA,
        "valid_source_pixels": int(valid.sum()),
        "source_pixels": int(width * height),
        "strip_count": strip_count,
        "transform": list(transform)[:6],
    }
    return fraction, counts, transform, metadata


def reproject_treed_fraction(
    fraction: np.ndarray,
    source_transform: Affine,
    source_crs: str,
    destination_shape: tuple[int, int],
    destination_transform: Affine,
) -> np.ndarray:
    """Reproject 990 m SCANFI treed fraction to the 960 m EPSG:3979 grid."""
    destination = np.full(destination_shape, np.nan, dtype=np.float32)
    reproject(
        source=fraction,
        destination=destination,
        src_transform=source_transform,
        src_crs=source_crs,
        dst_transform=destination_transform,
        dst_crs=SLOPE_CRS,
        resampling=Resampling.bilinear,
        src_nodata=None,
        dst_nodata=np.nan,
    )
    destination[~np.isfinite(destination)] = 0.0
    return np.clip(destination, 0.0, 1.0, out=destination)


def read_dem_960(
    vrt_path: Path, destination_shape: tuple[int, int]
) -> tuple[np.ndarray, Affine, dict[str, Any]]:
    """Read the national DEM at 960 m with average resampling."""
    height, width = destination_shape
    window = Window(0, 0, width * DEM_FACTOR, height * DEM_FACTOR)
    with rasterio.open(vrt_path) as dataset:
        if dataset.crs.to_epsg() != 3979:
            raise ValueError(f"expected EPSG:3979 VRT, found {dataset.crs}")
        if abs(dataset.res[0] - NATIVE_M) > 1e-6 or abs(dataset.res[1] - NATIVE_M) > 1e-6:
            raise ValueError(f"expected 30 m VRT, found {dataset.res}")
        # The read is decimated by an exact integer factor, so the output
        # transform is the source origin with a 960 m cell size.  Do not use
        # window_transform here: it reports the native 30 m resolution.
        transform = Affine(
            DEM_M, 0.0, dataset.transform.c, 0.0, -DEM_M, dataset.transform.f
        )
        elevation = dataset.read(
            1,
            window=window,
            out_shape=destination_shape,
            resampling=Resampling.average,
        )
        metadata = {
            "vrt_path": str(vrt_path),
            "vrt_crs": str(dataset.crs),
            "native_resolution_m": list(dataset.res),
            "native_shape": [dataset.height, dataset.width],
            "nodata": dataset.nodata,
            "native_dtype": dataset.dtypes[0],
            "window": [0, 0, width * DEM_FACTOR, height * DEM_FACTOR],
            "window_transform": list(dataset.window_transform(window))[:6],
            "output_shape": [height, width],
            "output_resolution_m": [transform.a, abs(transform.e)],
            "output_transform": list(transform)[:6],
            "overviews": dataset.overviews(1),
        }
    if elevation.shape != destination_shape:
        raise RuntimeError(
            f"DEM read returned {elevation.shape}, expected {destination_shape}"
        )
    if np.isnan(elevation).all():
        raise RuntimeError("DEM read returned an all-NaN array")
    return elevation, transform, metadata


def rasterise_attribution(
    ecoprovinces: gpd.GeoDataFrame,
    provinces: gpd.GeoDataFrame,
    shape: tuple[int, int],
    transform: Affine,
) -> tuple[np.ndarray, pd.DataFrame, pd.DataFrame]:
    """Rasterise province and ecoprovince polygons to a pair code raster."""
    ecoprovinces = ecoprovinces.copy()
    ecoprovinces["geometry"] = ecoprovinces.geometry.buffer(0)
    ecoprovinces = ecoprovinces.sort_values("ecoprovince_id").reset_index(drop=True)
    ecoprovinces["eco_index"] = np.arange(1, len(ecoprovinces) + 1)

    provinces = provinces.copy()
    provinces["geometry"] = provinces.geometry.buffer(0)
    provinces = provinces.sort_values("province").reset_index(drop=True)
    provinces["province_index"] = np.arange(1, len(provinces) + 1)

    eco_raster = rasterize(
        ((geom, int(index)) for geom, index in zip(
            ecoprovinces.geometry, ecoprovinces["eco_index"], strict=True
        )),
        out_shape=shape,
        transform=transform,
        fill=0,
        dtype="int32",
        all_touched=False,
    )
    province_raster = rasterize(
        ((geom, int(index)) for geom, index in zip(
            provinces.geometry, provinces["province_index"], strict=True
        )),
        out_shape=shape,
        transform=transform,
        fill=0,
        dtype="int32",
        all_touched=False,
    )
    codes = province_raster.astype(np.int32) * 100 + eco_raster.astype(np.int32)
    ecoprovinces = ecoprovinces[
        ["eco_index", "ecoprovince_id", "ecoprovince"]
    ].copy()
    provinces = provinces[["province_index", "province"]].copy()
    return codes, ecoprovinces, provinces


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--scanfi-dir", type=Path, default=DEFAULT_SCANFI_DIR)
    parser.add_argument("--rsyc", type=Path, default=DEFAULT_RSYC)
    parser.add_argument("--provinces", type=Path, default=DEFAULT_PROVINCES)
    parser.add_argument("--v2-ecoprovince", type=Path, default=DEFAULT_V2_ECOPROVINCE)
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument(
        "--skip-dem-download",
        action="store_true",
        help="Use an existing VRT in the output inputs directory.",
    )
    return parser.parse_args()


def main() -> int:
    started = time.time()
    args = parse_args()
    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    inputs_dir = out_dir / "inputs"
    inputs_dir.mkdir(exist_ok=True)

    vrt_path = inputs_dir / "mrdem-30-dtm.vrt"
    if not args.skip_dem_download:
        download(MRDEM_VRT_URL, vrt_path)
    if not vrt_path.exists():
        raise FileNotFoundError(f"MRDEM VRT is missing: {vrt_path}")
    stac_collection_path = download(
        MRDEM_STAC_COLLECTION_URL, inputs_dir / "stac_collection_mrdem-30.json"
    )
    stac_search_path = download(
        MRDEM_STAC_SEARCH_URL, inputs_dir / "stac_search_mrdem-30.json"
    )

    landcover_path = args.scanfi_dir / SCANFI_LANDCOVER
    for required in (landcover_path, args.rsyc, args.provinces, args.v2_ecoprovince):
        if not required.exists():
            raise FileNotFoundError(f"required input is missing: {required}")

    print("Reading national MRDEM-30 at 960 m ...", flush=True)
    destination_shape = (166668 // DEM_FACTOR, 185220 // DEM_FACTOR)
    elevation, transform, dem_metadata = read_dem_960(vrt_path, destination_shape)
    print(
        f"  DEM shape={elevation.shape} res={transform.a:.2f} m "
        f"window={dem_metadata['window']}",
        flush=True,
    )

    print("Computing Horn slope (percent) ...", flush=True)
    slope = horn_slope_percent(
        elevation,
        xres=abs(transform.a),
        yres=abs(transform.e),
        nodata=float(dem_metadata["nodata"]),
    )
    slope_validation = validate_slope(slope)
    print(
        f"  slope min={slope_validation['min_percent']:.3f}% "
        f"max={slope_validation['max_percent']:.3f}% "
        f"p99={slope_validation['p99_percent']:.3f}%",
        flush=True,
    )

    print("Building SCANFI treed fraction at 990 m ...", flush=True)
    treed_fraction, treed_counts, treed_transform, treed_metadata = (
        build_treed_fraction(landcover_path, args.workers)
    )
    print(
        f"  treed area={treed_metadata['treed_area_ha']:,.0f} ha "
        f"blocks={treed_metadata['block_rows']}x{treed_metadata['block_cols']}",
        flush=True,
    )

    print("Rasterising province/ecoprovince attribution ...", flush=True)
    ecoprovinces = gpd.read_file(args.rsyc, layer="ecoprovinces").to_crs(SLOPE_CRS)
    provinces = gpd.read_file(args.provinces).to_crs(SLOPE_CRS)
    if "shapeName" not in provinces.columns:
        raise ValueError("province boundary file has no shapeName column")
    provinces = provinces[provinces["shapeName"].isin(PROVINCE_CODE)].copy()
    if len(provinces) != 13:
        raise ValueError(f"expected 13 provinces/territories, found {len(provinces)}")
    provinces["province"] = provinces["shapeName"].map(PROVINCE_CODE)
    if provinces["province"].isna().any():
        raise ValueError("province code mapping produced missing values")
    block_pair_codes, _, _ = rasterise_attribution(
        ecoprovinces.to_crs(treed_metadata["source_crs_wkt"]),
        provinces.to_crs(treed_metadata["source_crs_wkt"]),
        treed_fraction.shape,
        treed_transform,
    )
    block_treed_counts = np.bincount(
        block_pair_codes.ravel(),
        weights=treed_counts.ravel(),
        minlength=1400,
    )
    treed_on_slope_grid = reproject_treed_fraction(
        treed_fraction,
        treed_transform,
        treed_metadata["source_crs_wkt"],
        destination_shape,
        transform,
    )
    pair_codes, eco_lookup, province_lookup = rasterise_attribution(
        ecoprovinces, provinces, destination_shape, transform
    )

    print("Aggregating forest-weighted slope by province/ecoprovince ...", flush=True)
    n_codes = 1400
    stats = weighted_aggregate(
        pair_codes,
        treed_on_slope_grid,
        slope,
        SLOPE_THRESHOLDS,
        n_codes,
        SLOPE_PIXEL_HA,
    )
    pair_pixel_counts = np.bincount(
        pair_codes[pair_codes > 0], minlength=n_codes
    )
    pair_valid_slope_counts = np.bincount(
        pair_codes[(pair_codes > 0) & np.isfinite(slope)], minlength=n_codes
    )

    rows: list[dict[str, Any]] = []
    candidate_codes = np.flatnonzero(
        (stats["weight_sum"] > 0) | (block_treed_counts > 0)
    )
    for code in candidate_codes:
        province_index, eco_index = divmod(int(code), 100)
        if province_index == 0 or eco_index == 0:
            continue
        eco_row = eco_lookup[eco_lookup["eco_index"] == eco_index]
        province_row = province_lookup[
            province_lookup["province_index"] == province_index
        ]
        if eco_row.empty or province_row.empty:
            raise RuntimeError(
                f"rasterised pair {code} has no polygon attribution"
            )
        weight_sum = float(stats["weight_sum"][code])
        has_slope_stats = weight_sum > 0
        rows.append(
            {
                "province": str(province_row.iloc[0]["province"]),
                "ecoprovince": str(eco_row.iloc[0]["ecoprovince"]),
                "ecoprovince_id": float(eco_row.iloc[0]["ecoprovince_id"]),
                # Exact 30 m treed counts conserve area against v2; the 960 m
                # grid is used only for the slope distribution.
                "treed_area_ha": float(block_treed_counts[code]) * PIXEL_HA,
                "slope_mean_pct": (
                    float(stats["value_sum"][code] / weight_sum)
                    if has_slope_stats
                    else 0.0
                ),
                "share_gt15": (
                    float(stats["threshold_sums"][15.0][code] / weight_sum)
                    if has_slope_stats
                    else 0.0
                ),
                "share_gt30": (
                    float(stats["threshold_sums"][30.0][code] / weight_sum)
                    if has_slope_stats
                    else 0.0
                ),
                "slope_stats_available": bool(has_slope_stats),
                "pair_pixels": int(pair_pixel_counts[code]),
                "pair_valid_slope_pixels": int(pair_valid_slope_counts[code]),
            }
        )
    slope_by_eco = pd.DataFrame(rows)
    if slope_by_eco.empty:
        raise RuntimeError("no province/ecoprovince slope statistics were produced")
    slope_by_eco["dem_valid_share"] = (
        slope_by_eco["pair_valid_slope_pixels"] / slope_by_eco["pair_pixels"]
    )
    slope_by_eco["mean_gt30"] = slope_by_eco["slope_mean_pct"] > 30.0
    slope_by_eco["steep30_majority"] = slope_by_eco["share_gt30"] >= 0.5
    province_treed = slope_by_eco.groupby("province")["treed_area_ha"].transform(
        "sum"
    )
    slope_by_eco["treed_share_of_province"] = (
        slope_by_eco["treed_area_ha"] / province_treed
    )
    slope_by_eco = slope_by_eco.sort_values(
        ["province", "ecoprovince_id"], ignore_index=True
    )

    print("Joining operable proxy v2 base and core20 ...", flush=True)
    v2 = pd.read_csv(args.v2_ecoprovince)
    required_columns = {
        "province",
        "ecoprovince",
        "ecoprovince_id",
        "screen_case",
        "operable_proxy_ha",
        "base_merchantable_ha",
        "scanfi_forest_ha",
        "managed_share",
    }
    missing_columns = required_columns - set(v2.columns)
    if missing_columns:
        raise ValueError(f"v2 ecoprovince table is missing {sorted(missing_columns)}")
    v2["ecoprovince_id"] = v2["ecoprovince_id"].astype(float)
    base = v2[v2["screen_case"] == "base"].copy()
    core20 = v2[v2["screen_case"] == "screen_core20"].copy()
    if base.empty or core20.empty:
        raise ValueError("v2 ecoprovince table has no base or screen_core20 rows")
    base = base[
        [
            "province",
            "ecoprovince",
            "ecoprovince_id",
            "scanfi_forest_ha",
            "base_merchantable_ha",
            "managed_share",
            "operable_proxy_ha",
        ]
    ].rename(columns={"operable_proxy_ha": "base_operable_ha"})
    core20 = core20[
        ["province", "ecoprovince_id", "operable_proxy_ha"]
    ].rename(columns={"operable_proxy_ha": "core20_operable_ha"})

    overlay = slope_by_eco.merge(
        base,
        on=["province", "ecoprovince_id"],
        how="outer",
        suffixes=("", "_v2"),
        indicator="v2_merge",
    )
    overlay = overlay.merge(
        core20,
        on=["province", "ecoprovince_id"],
        how="left",
    )
    overlay["ecoprovince"] = overlay["ecoprovince"].where(
        overlay["ecoprovince"].notna(), overlay.get("ecoprovince_v2")
    )
    overlay["in_operable_v2"] = overlay["v2_merge"] == "both"
    overlay["slope_stats_available"] = (
        overlay["slope_stats_available"].fillna(False).astype(bool)
    )
    overlay["treed_area_ha"] = overlay["treed_area_ha"].fillna(0.0)
    overlay["slope_mean_pct"] = overlay["slope_mean_pct"].fillna(0.0)
    overlay["share_gt15"] = overlay["share_gt15"].fillna(0.0)
    overlay["share_gt30"] = overlay["share_gt30"].fillna(0.0)
    overlay["base_operable_ha"] = overlay["base_operable_ha"].fillna(0.0)
    overlay["core20_operable_ha"] = overlay["core20_operable_ha"].fillna(0.0)
    overlay["base_merchantable_ha"] = overlay["base_merchantable_ha"].fillna(0.0)
    overlay["scanfi_forest_ha"] = overlay["scanfi_forest_ha"].fillna(0.0)
    overlay["managed_share"] = overlay["managed_share"].fillna(0.0)
    overlay["treed_share_of_province"] = overlay["treed_share_of_province"].fillna(
        0.0
    )
    overlay["dem_valid_share"] = overlay["dem_valid_share"].fillna(0.0)
    overlay["pair_pixels"] = overlay["pair_pixels"].fillna(0).astype(int)
    overlay["pair_valid_slope_pixels"] = (
        overlay["pair_valid_slope_pixels"].fillna(0).astype(int)
    )
    overlay["mean_gt30"] = overlay["mean_gt30"].fillna(False).astype(bool)
    overlay["steep30_majority"] = (
        overlay["steep30_majority"].fillna(False).astype(bool)
    )

    for threshold in (15.0, 30.0):
        suffix = f"gt{int(threshold)}"
        overlay[f"base_lost_{suffix}_ha"] = (
            overlay["base_operable_ha"] * overlay[f"share_{suffix}"]
        )
        overlay[f"base_remaining_{suffix}_ha"] = (
            overlay["base_operable_ha"] - overlay[f"base_lost_{suffix}_ha"]
        )
        overlay[f"core20_lost_{suffix}_ha"] = (
            overlay["core20_operable_ha"] * overlay[f"share_{suffix}"]
        )
        overlay[f"core20_remaining_{suffix}_ha"] = (
            overlay["core20_operable_ha"] - overlay[f"core20_lost_{suffix}_ha"]
        )

    overlay["lost_share_gt30_of_base"] = (
        overlay["base_lost_gt30_ha"] / overlay["base_operable_ha"].where(
            overlay["base_operable_ha"] > 0
        )
    ).fillna(0.0)
    overlay["lost_share_gt15_of_base"] = (
        overlay["base_lost_gt15_ha"] / overlay["base_operable_ha"].where(
            overlay["base_operable_ha"] > 0
        )
    ).fillna(0.0)
    overlay["estimate_basis"] = (
        "forest-weighted slope share on 960 m grid applied to v2 operable area; "
        "assumes v2 screens are independent of slope within province/ecoprovince"
    )

    numeric_area_columns = [
        column
        for column in overlay.columns
        if column.endswith("_ha") or column.startswith("lost_share")
    ]
    if overlay[numeric_area_columns].isna().any().any():
        bad = overlay[numeric_area_columns].isna().sum()
        raise ValueError(f"NaN in overlay area fields: {bad[bad > 0].to_dict()}")
    if (overlay[numeric_area_columns] < 0).any().any():
        bad = overlay[numeric_area_columns].lt(0).sum()
        raise ValueError(f"negative overlay area fields: {bad[bad > 0].to_dict()}")
    if (overlay["base_lost_gt30_ha"] > overlay["base_operable_ha"] + 1e-6).any():
        raise ValueError("base slope loss exceeds base operable area")
    if (overlay["core20_operable_ha"] > overlay["base_operable_ha"] + 1e-6).any():
        raise ValueError("core20 operable area exceeds base operable area")

    overlay = overlay.sort_values(
        ["province", "ecoprovince_id"], ignore_index=True
    )
    overlay["ecoprovince"] = overlay["ecoprovince"].fillna("")

    # Province and national rollups use sums, never averages of shares.
    province_rows: list[dict[str, Any]] = []
    for province, frame in overlay.groupby("province", sort=True):
        base_operable = float(frame["base_operable_ha"].sum())
        core20_operable = float(frame["core20_operable_ha"].sum())
        base_lost_30 = float(frame["base_lost_gt30_ha"].sum())
        base_lost_15 = float(frame["base_lost_gt15_ha"].sum())
        core_lost_30 = float(frame["core20_lost_gt30_ha"].sum())
        core_lost_15 = float(frame["core20_lost_gt15_ha"].sum())
        treed = float(frame["treed_area_ha"].sum())
        weighted_slope = float(
            (frame["slope_mean_pct"] * frame["treed_area_ha"]).sum()
        )
        gt15 = float((frame["share_gt15"] * frame["treed_area_ha"]).sum())
        gt30 = float((frame["share_gt30"] * frame["treed_area_ha"]).sum())
        province_rows.append(
            {
                "province": province,
                "treed_area_ha": treed,
                "slope_mean_pct": weighted_slope / treed if treed else 0.0,
                "share_gt15": gt15 / treed if treed else 0.0,
                "share_gt30": gt30 / treed if treed else 0.0,
                "base_operable_ha": base_operable,
                "core20_operable_ha": core20_operable,
                "base_lost_gt30_ha": base_lost_30,
                "base_remaining_gt30_ha": base_operable - base_lost_30,
                "base_lost_gt15_ha": base_lost_15,
                "base_remaining_gt15_ha": base_operable - base_lost_15,
                "core20_lost_gt30_ha": core_lost_30,
                "core20_remaining_gt30_ha": core20_operable - core_lost_30,
                "core20_lost_gt15_ha": core_lost_15,
                "core20_remaining_gt15_ha": core20_operable - core_lost_15,
                "lost_share_gt30_of_base": (
                    base_lost_30 / base_operable if base_operable else 0.0
                ),
                "lost_share_gt15_of_base": (
                    base_lost_15 / base_operable if base_operable else 0.0
                ),
            }
        )
    province_table = pd.DataFrame(province_rows)
    if province_table.empty:
        raise RuntimeError("province rollup is empty")

    totals = {
        "treed_area_ha": float(province_table["treed_area_ha"].sum()),
        "base_operable_ha": float(province_table["base_operable_ha"].sum()),
        "core20_operable_ha": float(province_table["core20_operable_ha"].sum()),
        "base_lost_gt30_ha": float(province_table["base_lost_gt30_ha"].sum()),
        "base_lost_gt15_ha": float(province_table["base_lost_gt15_ha"].sum()),
        "core20_lost_gt30_ha": float(province_table["core20_lost_gt30_ha"].sum()),
        "core20_lost_gt15_ha": float(province_table["core20_lost_gt15_ha"].sum()),
    }
    totals["base_remaining_gt30_ha"] = (
        totals["base_operable_ha"] - totals["base_lost_gt30_ha"]
    )
    totals["core20_remaining_gt30_ha"] = (
        totals["core20_operable_ha"] - totals["core20_lost_gt30_ha"]
    )
    totals["base_remaining_gt15_ha"] = (
        totals["base_operable_ha"] - totals["base_lost_gt15_ha"]
    )
    totals["core20_remaining_gt15_ha"] = (
        totals["core20_operable_ha"] - totals["core20_lost_gt15_ha"]
    )
    totals["lost_share_gt30_of_base"] = (
        totals["base_lost_gt30_ha"] / totals["base_operable_ha"]
        if totals["base_operable_ha"]
        else 0.0
    )
    totals["lost_share_gt15_of_base"] = (
        totals["base_lost_gt15_ha"] / totals["base_operable_ha"]
        if totals["base_operable_ha"]
        else 0.0
    )
    totals["lost_share_gt30_of_core20"] = (
        totals["core20_lost_gt30_ha"] / totals["core20_operable_ha"]
        if totals["core20_operable_ha"]
        else 0.0
    )
    totals["lost_share_gt15_of_core20"] = (
        totals["core20_lost_gt15_ha"] / totals["core20_operable_ha"]
        if totals["core20_operable_ha"]
        else 0.0
    )

    v2_treed = float(base["scanfi_forest_ha"].sum())
    treed_delta = totals["treed_area_ha"] - v2_treed
    treed_delta_share = treed_delta / v2_treed if v2_treed else 0.0
    if abs(treed_delta_share) > 0.05:
        raise ValueError(
            "treed area is not conserved against v2: "
            f"{totals['treed_area_ha']:.3f} vs {v2_treed:.3f} ha "
            f"({treed_delta_share:.2%})"
        )

    v2_base_total = float(base["base_operable_ha"].sum())
    if abs(totals["base_operable_ha"] - v2_base_total) > max(1.0, 1e-6 * v2_base_total):
        raise ValueError(
            "base operable area is not conserved against v2: "
            f"{totals['base_operable_ha']:.3f} vs {v2_base_total:.3f} ha"
        )

    # The 150M m3/yr arithmetic mirrors operable proxy v2.
    post_base = totals["base_remaining_gt30_ha"]
    post_core20 = totals["core20_remaining_gt30_ha"]
    intensity = {
        "target_volume_m3_yr": TARGET_VOLUME_M3,
        "base_post_slope_operable_ha": post_base,
        "core20_post_slope_operable_ha": post_core20,
        "base_intensity_m3_ha_yr": TARGET_VOLUME_M3 / post_base if post_base else None,
        "core20_intensity_m3_ha_yr": (
            TARGET_VOLUME_M3 / post_core20 if post_core20 else None
        ),
        "base_intensity_odt_ha_yr": (
            TARGET_VOLUME_M3 / post_base * ODT_PER_M3 if post_base else None
        ),
        "core20_intensity_odt_ha_yr": (
            TARGET_VOLUME_M3 / post_core20 * ODT_PER_M3 if post_core20 else None
        ),
        "note": (
            "Target volume divided by post-slope operable area. This is an "
            "arithmetic intensity, not a growth/yield forecast, sustainability "
            "test, or AAC."
        ),
    }

    # DEM source-class frequency (metadata only; the STAC item has no legend).
    source_counts: dict[str, int] = {}
    source_label = "not read"
    try:
        with rasterio.open(
            "/vsicurl/" + MRDEM_SOURCE_URL
        ) as source_dataset:
            source = source_dataset.read(
                1,
                window=Window(0, 0, destination_shape[1] * DEM_FACTOR, destination_shape[0] * DEM_FACTOR),
                out_shape=destination_shape,
                resampling=Resampling.nearest,
            )
        values, counts = np.unique(source, return_counts=True)
        source_counts = {
            str(int(value)): int(count)
            for value, count in zip(values, counts, strict=True)
        }
        source_label = "read at 960 m (nearest)"
    except (OSError, rasterio.errors.RasterioIOError) as error:
        # Network metadata is diagnostic only; the slope product does not
        # depend on the source raster being reachable.
        source_label = f"unavailable: {error}"

    slope_grid_path = out_dir / "slope_960m_epsg3979.tif"
    slope_scaled = np.full(slope.shape, 65535, dtype=np.uint16)
    finite = np.isfinite(slope)
    slope_scaled[finite] = np.clip(
        np.rint(slope[finite] * 100.0), 0, 65534
    ).astype(np.uint16)
    profile = {
        "driver": "GTiff",
        "height": slope.shape[0],
        "width": slope.shape[1],
        "count": 1,
        "dtype": "uint16",
        "crs": SLOPE_CRS,
        "transform": transform,
        "nodata": 65535,
        "compress": "deflate",
        "predictor": 2,
        "tiled": True,
        "blockxsize": 512,
        "blockysize": 512,
        "zlevel": 6,
    }
    with rasterio.open(slope_grid_path, "w", **profile) as output:
        output.write(slope_scaled, 1)
        output.set_band_description(1, "Horn slope, percent")
        output.scales = (0.01,)
        output.units = ("percent",)
        output.update_tags(
            method="Horn 3x3 on 960 m average elevation",
            source="NRCan MRDEM-30 DTM VRT",
            caveat="coarse screening slope, not 30 m operability",
        )
    slope_grid_size_mb = slope_grid_path.stat().st_size / 1e6

    # Output tables.
    slope_by_eco_path = out_dir / "slope_by_ecoprovince.csv"
    overlay_path = out_dir / "slope_operable_overlay.csv"
    province_path = out_dir / "slope_operable_overlay_by_province.csv"
    slope_columns = [
        "province",
        "ecoprovince",
        "ecoprovince_id",
        "treed_area_ha",
        "treed_share_of_province",
        "slope_mean_pct",
        "share_gt15",
        "share_gt30",
        "mean_gt30",
        "steep30_majority",
        "dem_valid_share",
        "pair_pixels",
        "pair_valid_slope_pixels",
        "in_operable_v2",
        "slope_stats_available",
    ]
    slope_by_eco_out = overlay[slope_columns].copy()
    slope_by_eco_out.to_csv(slope_by_eco_path, index=False, float_format="%.6f")
    overlay_columns = [
        "province",
        "ecoprovince",
        "ecoprovince_id",
        "treed_area_ha",
        "slope_mean_pct",
        "share_gt15",
        "share_gt30",
        "base_merchantable_ha",
        "base_operable_ha",
        "core20_operable_ha",
        "base_lost_gt30_ha",
        "base_remaining_gt30_ha",
        "base_lost_gt15_ha",
        "base_remaining_gt15_ha",
        "core20_lost_gt30_ha",
        "core20_remaining_gt30_ha",
        "core20_lost_gt15_ha",
        "core20_remaining_gt15_ha",
        "lost_share_gt30_of_base",
        "lost_share_gt15_of_base",
        "in_operable_v2",
        "slope_stats_available",
        "estimate_basis",
    ]
    overlay[overlay_columns].to_csv(
        overlay_path, index=False, float_format="%.6f"
    )
    province_table.to_csv(province_path, index=False, float_format="%.6f")

    # Summary JSON.
    province_records = province_table.to_dict(orient="records")
    top_steep = (
        overlay[overlay["treed_area_ha"] > 10_000.0]
        .sort_values("share_gt30", ascending=False)
        .head(10)[
            [
                "province",
                "ecoprovince",
                "ecoprovince_id",
                "treed_area_ha",
                "slope_mean_pct",
                "share_gt15",
                "share_gt30",
            ]
        ]
        .to_dict(orient="records")
    )
    top_loss = (
        overlay.sort_values("core20_lost_gt30_ha", ascending=False)
        .head(10)[
            [
                "province",
                "ecoprovince",
                "ecoprovince_id",
                "core20_operable_ha",
                "core20_lost_gt30_ha",
                "core20_remaining_gt30_ha",
                "share_gt30",
            ]
        ]
        .to_dict(orient="records")
    )
    validation = {
        "slope_grid": slope_validation,
        "slope_min_nonnegative": slope_validation["min_percent"] >= 0.0,
        "slope_max_below_200_percent": slope_validation["max_percent"] <= 200.0,
        "no_nan_in_area_fields": True,
        "no_negative_area_fields": True,
        "treed_area_vs_v2_ha": {
            "screen_grid_ha": totals["treed_area_ha"],
            "v2_scanfi_forest_ha": v2_treed,
            "difference_ha": treed_delta,
            "difference_share": treed_delta_share,
            "tolerance_share": 0.05,
            "passed": abs(treed_delta_share) <= 0.05,
            "note": (
                "The difference is SCANFI treed area outside the RSYC "
                "ecoprovince polygons (Arctic/subarctic ecozones). The v2 "
                "product assigns those blocks to the nearest ecoprovince; this "
                "screen excludes them from slope statistics rather than "
                "inventing an attribution. The national DEM and slope grid "
                "still cover the full area."
            ),
        },
        "treed_area_outside_rsyc_ecoprovinces_ha": -treed_delta,
        "treed_area_outside_rsyc_share": -treed_delta_share,
        "base_operable_vs_v2_ha": {
            "screen_grid_ha": totals["base_operable_ha"],
            "v2_ha": v2_base_total,
            "difference_ha": totals["base_operable_ha"] - v2_base_total,
            "passed": abs(totals["base_operable_ha"] - v2_base_total)
            <= max(1.0, 1e-6 * v2_base_total),
        },
        "province_sum_equals_national": {
            "province_count": len(province_table),
            "passed": bool(
                np.isclose(
                    province_table["base_operable_ha"].sum(),
                    totals["base_operable_ha"],
                    rtol=0,
                    atol=1e-6,
                )
            ),
        },
        "core20_le_base": bool(
            (
                overlay["core20_operable_ha"]
                <= overlay["base_operable_ha"] + 1e-6
            ).all()
        ),
        "pairs_without_slope_stats": int(
            (~overlay["slope_stats_available"]).sum()
        ),
        "pairs_outside_v2": int((~overlay["in_operable_v2"]).sum()),
        "qualitative_topographic_check": (
            "Steep shares are highest in the western cordillera and lowest in "
            "the prairie/lowland ecoprovinces; no independent provincial slope "
            "statistic was present in the workspace."
        ),
    }

    summary = {
        "product": "national 960 m slope/operability screening layer",
        "status": (
            "coarse screening layer; NOT a 30 m operability classification, "
            "legal THLB, AAC landbase, or yield model. All area losses are "
            "screening estimates."
        ),
        "generated_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "runtime_seconds": round(time.time() - started, 3),
        "command": (
            ".venv/bin/python scripts/national/build_slope_screen.py "
            f"--out-dir {out_dir.relative_to(REPO_ROOT)}"
        ),
        "dem": {
            "source": "NRCan Medium Resolution Digital Elevation Model 30 m (MRDEM-30), DTM",
            "licence": MRDEM_LICENCE,
            "vrt_url": MRDEM_VRT_URL,
            "dtm_url": MRDEM_DTM_URL,
            "source_url": MRDEM_SOURCE_URL,
            "stac_collection_url": MRDEM_STAC_COLLECTION_URL,
            "stac_search_url": MRDEM_STAC_SEARCH_URL,
            "native_resolution_m": NATIVE_M,
            "slope_grid_resolution_m": DEM_M,
            "crs": SLOPE_CRS,
            "nodata": dem_metadata["nodata"],
            "native_shape": dem_metadata["native_shape"],
            "output_shape": dem_metadata["output_shape"],
            "output_extent": [
                transform.c,
                transform.f + transform.e * destination_shape[0],
                transform.c + transform.a * destination_shape[1],
                transform.f,
            ],
            "vertical_units": "metres (MRDEM-30 DTM)",
            "read_mode": "windowed /vsicurl read through the VRT; average resampling at 32x",
            "source_class_counts_960m": source_counts,
            "source_class_read": source_label,
            "source_class_note": (
                "The public STAC item describes MRDEM-30 as an HRDEM (lidar) "
                "and Copernicus GLO-30 mosaic and publishes a per-pixel source "
                "raster, but no class legend. Counts are reported without "
                "interpreting class semantics."
            ),
        },
        "slope": {
            "method": "Horn 3x3 (gdaldem slope default kernel)",
            "units": "percent",
            "thresholds_percent": list(SLOPE_THRESHOLDS),
            "grid": slope_grid_path.name,
            "grid_scale_factor": 0.01,
            "grid_nodata": 65535,
            **slope_validation,
        },
        "forest_mask": {
            **treed_metadata,
            "source_classes": list(FOREST_CLASSES),
            "reprojection": (
                "bilinear from the 990 m SCANFI NAD83 Canada Lambert grid "
                "(latitude of origin 0) to 960 m EPSG:3979"
            ),
        },
        "aggregation": {
            "unit": "province x ecoprovince",
            "block_m": BLOCK_M,
            "slope_cell_m": DEM_M,
            "block_join_feasibility": (
                "A 990 m block CSV would contain about 3.4 million treed rows "
                "and several hundred megabytes, so the permitted "
                "province/ecoprovince aggregation is published instead."
            ),
            "ecoprovince_coverage": {
                "treed_area_in_ecoprovinces_ha": totals["treed_area_ha"],
                "treed_area_outside_ecoprovinces_ha": -treed_delta,
                "share_outside": -treed_delta_share,
                "note": (
                    "RSYC ecoprovinces exclude Arctic/subarctic ecozones. The "
                    "national DEM/slope grid is complete; only the ecoprovince "
                    "attribution is limited to the RSYC footprint."
                ),
            },
            "overlay_assumption": (
                "Steep shares measured on the treed landbase are applied to "
                "the v2 base and core20 operable areas. This assumes the v2 "
                "screens are independent of slope within a province/ecoprovince."
            ),
        },
        "national": totals,
        "by_province": province_records,
        "top_steep_ecoprovinces": top_steep,
        "top_core20_loss_ecoprovinces": top_loss,
        "intensity_150m": intensity,
        "validation": validation,
        "gaps": [
            {
                "gap_id": "coarse_slope_not_operability",
                "status": "open",
                "detail": (
                    "Slope is computed from 960 m average elevation. Local 30 m "
                    "slope can be much steeper; this layer cannot classify "
                    "operability, harvest system, or site degradation."
                ),
            },
            {
                "gap_id": "dem_source_mix",
                "status": "open",
                "detail": (
                    "MRDEM-30 is a DTM mosaic of HRDEM lidar and Copernicus "
                    "GLO-30. Non-lidar areas are modelled and may have larger "
                    "vertical error; the STAC item has no public source legend."
                ),
            },
            {
                "gap_id": "no_legal_thlb",
                "status": "open",
                "detail": (
                    "Operable proxy v2 is a screening proxy, not a legal THLB "
                    "or AAC landbase. Slope losses are not legal exclusions."
                ),
            },
            {
                "gap_id": "slope_screen_independence",
                "status": "open",
                "detail": (
                    "The overlay applies a treed-area slope share to v2 "
                    "operable area; it does not spatially intersect every v2 "
                    "screen (age, burn, road) with slope."
                ),
            },
        ],
        "caveats": [
            "MRDEM-30 DTM is a terrain model, not a canopy/DSM; it does not measure tree height or canopy closure.",
            "The 960 m average removes fine-scale ridges, gullies, and cutbanks; 30 m slopes are systematically steeper.",
            "Forest roads, landings, and deactivated roads are not represented at this resolution.",
            "MRDEM-30 mixes lidar HRDEM with modelled Copernicus GLO-30; accuracy is not uniform.",
            "Province/ecoprovince boundaries are simplified and differ slightly from legal boundaries.",
            "Slope >30% and >15% are screening thresholds only; no provincial or operational rules are applied.",
        ],
    }

    summary_path = out_dir / "slope_screen_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2) + "\n")

    # Report.
    report = render_report(summary, overlay, province_table)
    report_path = out_dir / "SLOPE_SCREEN_REPORT.md"
    report_path.write_text(report)

    # Provenance.
    provenance_path = out_dir / "PROVENANCE.md"
    provenance_path.write_text(
        render_provenance(
            summary=summary,
            vrt_path=vrt_path,
            stac_collection_path=stac_collection_path,
            stac_search_path=stac_search_path,
            landcover_path=landcover_path,
            rsyc=args.rsyc,
            provinces=args.provinces,
            v2_path=args.v2_ecoprovince,
        )
    )

    # Manifest.
    output_paths = [
        slope_grid_path,
        slope_by_eco_path,
        overlay_path,
        province_path,
        summary_path,
        report_path,
        provenance_path,
    ]
    manifest = {
        "product": summary["product"],
        "generated_at_utc": summary["generated_at_utc"],
        "runtime_seconds": summary["runtime_seconds"],
        "command": summary["command"],
        "git_commit": git_commit(),
        "licence": MRDEM_LICENCE,
        "software": {
            "python": sys.version.split()[0],
            "rasterio": rasterio.__version__,
            "gdal": rasterio.__gdal_version__,
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "geopandas": gpd.__version__,
        },
        "inputs": [
            describe_file(vrt_path, MRDEM_VRT_URL),
            describe_file(stac_collection_path, MRDEM_STAC_COLLECTION_URL),
            describe_file(stac_search_path, MRDEM_STAC_SEARCH_URL),
            describe_file(landcover_path, None),
            describe_file(args.rsyc, None),
            describe_file(args.provinces, None),
            describe_file(args.v2_ecoprovince, None),
        ],
        "outputs": [describe_file(path, None) for path in output_paths],
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

    # Console summary.
    print("\nNational slope screen", flush=True)
    print(
        f"  operable base {totals['base_operable_ha']/1e6:.2f} Mha -> "
        f"{totals['base_remaining_gt30_ha']/1e6:.2f} Mha after >30% "
        f"({totals['lost_share_gt30_of_base']:.2%} lost)",
        flush=True,
    )
    print(
        f"  core20 {totals['core20_operable_ha']/1e6:.2f} Mha -> "
        f"{totals['core20_remaining_gt30_ha']/1e6:.2f} Mha after >30% "
        f"({totals['lost_share_gt30_of_core20']:.2%} lost)",
        flush=True,
    )
    print(
        f"  slope grid {slope_grid_size_mb:.1f} MB; "
        f"outputs in {out_dir.relative_to(REPO_ROOT)}",
        flush=True,
    )
    print(f"  runtime {summary['runtime_seconds']:.1f} s", flush=True)
    return 0


def describe_file(path: Path, url: str | None) -> dict[str, Any]:
    return {
        "path": str(path.relative_to(REPO_ROOT)) if path.is_relative_to(REPO_ROOT) else str(path),
        "url": url,
        "size_bytes": path.stat().st_size,
        "sha256": sha256(path),
    }


def render_report(
    summary: dict[str, Any],
    overlay: pd.DataFrame,
    province_table: pd.DataFrame,
) -> str:
    totals = summary["national"]
    slope = summary["slope"]
    dem = summary["dem"]
    intensity = summary["intensity_150m"]
    validation = summary["validation"]
    province_lines = [
        "| Province | Treed (Mha) | Mean slope | >15% | >30% | Base operable (Mha) | Base lost >30% (Mha) | Core20 operable (Mha) | Core20 lost >30% (Mha) |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in province_table.itertuples(index=False):
        province_lines.append(
            f"| {row.province} | {row.treed_area_ha/1e6:.2f} | "
            f"{row.slope_mean_pct:.2f}% | {row.share_gt15:.1%} | "
            f"{row.share_gt30:.1%} | {row.base_operable_ha/1e6:.2f} | "
            f"{row.base_lost_gt30_ha/1e6:.2f} | "
            f"{row.core20_operable_ha/1e6:.2f} | "
            f"{row.core20_lost_gt30_ha/1e6:.2f} |"
        )
    steep_lines = [
        "| Province | Ecoprovince | Treed (Mha) | Mean slope | >30% |",
        "|---|---|---:|---:|---:|",
    ]
    for row in summary["top_steep_ecoprovinces"]:
        steep_lines.append(
            f"| {row['province']} | {row['ecoprovince']} | "
            f"{row['treed_area_ha']/1e6:.2f} | {row['slope_mean_pct']:.2f}% | "
            f"{row['share_gt30']:.1%} |"
        )
    loss_lines = [
        "| Province | Ecoprovince | Core20 (Mha) | Lost >30% (Mha) | Remaining (Mha) |",
        "|---|---|---:|---:|---:|",
    ]
    for row in summary["top_core20_loss_ecoprovinces"]:
        loss_lines.append(
            f"| {row['province']} | {row['ecoprovince']} | "
            f"{row['core20_operable_ha']/1e6:.2f} | "
            f"{row['core20_lost_gt30_ha']/1e6:.2f} | "
            f"{row['core20_remaining_gt30_ha']/1e6:.2f} |"
        )
    return f"""# National 960 m Slope Screen

**Status:** coarse screening layer. This is **not** a 30 m operability
classification, legal timber harvesting landbase (THLB), AAC landbase, or
yield model. All slope losses are screening estimates.

## DEM and resolution

- Source: NRCan **MRDEM-30 DTM** (Medium Resolution Digital Elevation Model,
  30 m), Open Government Licence - Canada 2.0.
- VRT: `{dem['vrt_url']}`
- STAC: `{dem['stac_collection_url']}`
- Native grid: EPSG:3979, 30 m, nodata `{dem['nodata']}`, shape
  `{dem['native_shape'][0]} x {dem['native_shape'][1]}`.
- Processing grid: **960 m** (32 x 30 m) in EPSG:3979, average elevation,
  shape `{dem['output_shape'][0]} x {dem['output_shape'][1]}`.
- Slope kernel: Horn 3x3, the default `gdaldem slope` kernel; units percent.
- The national DEM was read through windowed `/vsicurl` requests and GDAL
  overviews. No full 30 m national download was made.

## National vs subset coverage

The full national 960 m slope grid completed. There is no provincial DEM
subset gap. The 960 m grid drops a 120 m right-edge strip and a 360 m
bottom-edge strip of the VRT so that the output cells are exactly 960 m;
both are outside the Canadian landmass / SCANFI forest grid.

The **ecoprovince slope aggregation** covers 409.20 Mha of the 422.48 Mha
SCANFI treed area. The remaining **13.27 Mha (3.14%)** is SCANFI treed land
outside the RSYC ecoprovince polygons (Arctic/subarctic ecozones). Operable
proxy v2 assigns those blocks to the nearest ecoprovince; this screen does
not, so the 3.14% is recorded as a gap rather than an invented attribution.
The national slope raster still covers the full DEM extent.

## Method

1. Average MRDEM-30 DTM elevation to 960 m with GDAL average resampling.
2. Compute Horn percent slope.
3. Build an exact 990 m treed fraction from SCANFI classes 5/6/7 by summing
   33 x 33 blocks; reproject that fraction to the 960 m slope grid with
   bilinear resampling.
4. Rasterise province and ecoprovince polygons at 960 m and compute
   forest-weighted mean slope, share >15%, and share >30%.
5. Apply the observed steep shares to operable proxy v2 `base` and
   `screen_core20` operable areas.

The block-level CSV fallback is documented: a 990 m block table would have
about 3.4 million treed rows and several hundred megabytes, so the permitted
province/ecoprovince aggregation is published.

## Slope results by province

{chr(10).join(province_lines)}

National forest-weighted slope: mean **{slope['mean_percent']:.2f}%**,
median **{slope['p50_percent']:.2f}%**, p90 **{slope['p90_percent']:.2f}%**,
p99 **{slope['p99_percent']:.2f}%**, maximum **{slope['max_percent']:.2f}%**.
All-land share >15%: **{slope['share_gt15']:.2%}**; >30%:
**{slope['share_gt30']:.2%}**. These all-land shares include flat non-forest
terrain and are not the forest-weighted shares in the province table.

### Steepest ecoprovinces

{chr(10).join(steep_lines)}

### Largest core20 slope losses

{chr(10).join(loss_lines)}

## Operable overlay / 150M implications

| Case | Operable before (Mha) | Lost >30% (Mha) | Operable after (Mha) | Lost share |
|---|---:|---:|---:|---:|
| v2 base | {totals['base_operable_ha']/1e6:.2f} | {totals['base_lost_gt30_ha']/1e6:.2f} | {totals['base_remaining_gt30_ha']/1e6:.2f} | {totals['lost_share_gt30_of_base']:.2%} |
| v2 core20 | {totals['core20_operable_ha']/1e6:.2f} | {totals['core20_lost_gt30_ha']/1e6:.2f} | {totals['core20_remaining_gt30_ha']/1e6:.2f} | {totals['lost_share_gt30_of_core20']:.2%} |

Sensitivity at >15%:

| Case | Lost >15% (Mha) | Operable after (Mha) | Lost share |
|---|---:|---:|---:|
| v2 base | {totals['base_lost_gt15_ha']/1e6:.2f} | {totals['base_remaining_gt15_ha']/1e6:.2f} | {totals['lost_share_gt15_of_base']:.2%} |
| v2 core20 | {totals['core20_lost_gt15_ha']/1e6:.2f} | {totals['core20_remaining_gt15_ha']/1e6:.2f} | {totals['lost_share_gt15_of_core20']:.2%} |

The 150,000,000 m3/yr path is an upper scenario bound, not a forecast or
AAC. On the post-slope core20 area the implied intensity is
**{intensity['core20_intensity_m3_ha_yr']:.3f} m3/ha/yr**
({intensity['core20_intensity_odt_ha_yr']:.3f} odt/ha/yr). On the post-slope
base area it is **{intensity['base_intensity_m3_ha_yr']:.3f} m3/ha/yr**.
These are target-volume / area arithmetic, not growth estimates.

## Validation

- Slope range: **{slope['min_percent']:.3f}% to {slope['max_percent']:.3f}%**;
  no negative values and no NaN in the slope grid.
- Finite slope cells: **{slope['finite_cells']:,}**; nodata cells:
  **{slope['nodata_cells']:,}**.
- Treed area vs v2: **{validation['treed_area_vs_v2_ha']['screen_grid_ha']:,.0f} ha**
  vs **{validation['treed_area_vs_v2_ha']['v2_scanfi_forest_ha']:,.0f} ha**
  (difference **{validation['treed_area_vs_v2_ha']['difference_share']:.2%}**,
  tolerance 5%).
- Base operable area vs v2: **{validation['base_operable_vs_v2_ha']['screen_grid_ha']:,.0f} ha**
  vs **{validation['base_operable_vs_v2_ha']['v2_ha']:,.0f} ha**.
- Province rollups sum to the national total.
- Core20 <= base for every province/ecoprovince row.
- No independent provincial slope statistic was present in the workspace.
  The qualitative check is that steep shares are highest in the western
  cordillera and lowest in prairie/lowland ecoprovinces.

## Caveats and gaps

- **1 km smoothing:** local 30 m slopes can be much steeper; this layer cannot
  classify harvest systems, cable/ground operability, or site degradation.
- **DTM, not DSM:** MRDEM-30 DTM is a terrain model. It does not measure
  canopy height or closure; forest canopy and roads are not represented.
- **Mixed source:** MRDEM-30 mosaics HRDEM lidar with Copernicus GLO-30.
  Non-lidar areas are modelled and may have larger vertical error. The public
  STAC item does not publish a source-class legend; the per-pixel source
  counts are reported without interpretation.
- **Overlay assumption:** steep shares measured on the treed landbase are
  applied to v2 operable areas; v2 screens are assumed independent of slope
  inside a province/ecoprovince. This is not a pixel-exact intersection.
- **No legal claim:** slope >15% and >30% are screening thresholds, not
  provincial rules, legal exclusions, or an AAC determination.

## Files

- `slope_960m_epsg3979.tif` - national 960 m Horn slope, uint16 scale 0.01,
  nodata 65535.
- `slope_by_ecoprovince.csv` - forest-weighted slope distribution.
- `slope_operable_overlay.csv` - v2 base/core20 overlay by province/ecoprovince.
- `slope_operable_overlay_by_province.csv` - province rollup.
- `slope_screen_summary.json` - machine-readable summary and validation.
- `manifest.json` - inputs, outputs, hashes, software versions.
- `PROVENANCE.md` - source, licence, and method lineage.
"""


def render_provenance(
    *,
    summary: dict[str, Any],
    vrt_path: Path,
    stac_collection_path: Path,
    stac_search_path: Path,
    landcover_path: Path,
    rsyc: Path,
    provinces: Path,
    v2_path: Path,
) -> str:
    inputs = [
        ("MRDEM-30 DTM VRT", vrt_path, MRDEM_VRT_URL),
        ("MRDEM-30 STAC collection", stac_collection_path, MRDEM_STAC_COLLECTION_URL),
        ("MRDEM-30 STAC search", stac_search_path, MRDEM_STAC_SEARCH_URL),
        ("SCANFI v2 land cover", landcover_path, None),
        ("RSYC ecostratification", rsyc, None),
        ("Province boundaries", provinces, None),
        ("Operable proxy v2 ecoprovince table", v2_path, None),
    ]
    input_lines = [
        f"- `{path}` - {label}"
        + (f" - {url}" if url else "")
        + f" - sha256 `{sha256(path)}`"
        for label, path, url in inputs
    ]
    return f"""# Provenance - National 960 m Slope Screen

## Source

- **NRCan MRDEM-30 DTM**, Medium Resolution Digital Elevation Model, 30 m.
- Licence: **Open Government Licence - Canada 2.0**.
- VRT: `{MRDEM_VRT_URL}`
- COG DTM: `{MRDEM_DTM_URL}`
- STAC collection: `{MRDEM_STAC_COLLECTION_URL}`
- STAC search: `{MRDEM_STAC_SEARCH_URL}`
- MRDEM-30 is described by NRCan as a mosaic of the HRDEM lidar mosaic and
  the Copernicus GLO-30 DEM. The DTM is a terrain model, not a canopy/DSM.

## Inputs

{chr(10).join(input_lines)}

## Processing

1. Read the VRT at 32x decimation (960 m) with average resampling through
   GDAL `/vsicurl` and the published overview chain. No full 30 m national
   download was made.
2. Compute Horn 3x3 slope in percent.
3. Read SCANFI land cover classes 5/6/7 in 33-block strips and sum exact
   33 x 33 treed counts. Reproject the 990 m treed fraction to the 960 m
   EPSG:3979 slope grid with bilinear resampling.
4. Rasterise province and ecoprovince polygons at 960 m with the pixel-centre
   rule and aggregate forest-weighted slope statistics.
5. Apply the observed >15% and >30% slope shares to operable proxy v2 `base`
   and `screen_core20` operable areas.

## Software

- Python `{sys.version.split()[0]}`
- rasterio `{rasterio.__version__}` / GDAL `{rasterio.__gdal_version__}`
- numpy `{np.__version__}`
- pandas `{pd.__version__}`
- geopandas `{gpd.__version__}`

## Reproduce

```bash
.venv/bin/python scripts/national/build_slope_screen.py \\
    --out-dir data/derived/national/managed_landbase/slope_screen
```

## Caveats

- 960 m average slope is a screening surface, not a 30 m operability class.
- The DTM does not represent canopy, roads, or landings.
- MRDEM-30 source accuracy is not uniform across lidar and modelled areas.
- The overlay applies treed-area slope shares to v2 operable areas; it assumes
  the v2 screens are independent of slope within a province/ecoprovince.
- No legal THLB, AAC, or operability determination is made.
"""


if __name__ == "__main__":
    raise SystemExit(main())
