#!/usr/bin/env python3
"""Validate the ERA5 normals COGs produced by compute_era5_normals.py.

Checks every expected output exists, is a valid GeoTIFF in EPSG:4326 with
nodata=-9999, then reports nodata-aware summary statistics for the annual and
bioclimatic rasters and applies physical sanity bounds. Exits 1 on failure.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import rasterio
from affine import Affine

OUTPUT_DIR = Path("/srv/shared-data/gep/jupyterhub16-projects/data/processed/ecmwf/era5/normals")

EXPECTED_MONTHLY_STEMS = ["t2m", "tp", "d2m"]
EXPECTED_SINGLE_FILES = [
    "era5_normals_mat.tif",
    "era5_normals_map.tif",
    "era5_normals_d2m_annual.tif",
    "era5_normals_gdd5.tif",
    "era5_normals_ffp.tif",
    "era5_normals_vpd.tif",
    "era5_normals_twarm.tif",
    "era5_normals_tcold.tif",
    "era5_normals_pseas.tif",
    "era5_normals_pwarm.tif",
    "era5_normals_pcold.tif",
    "era5_normals_continentality.tif",
]

# Physical sanity bounds for Canada: filename -> (min_allowed, max_allowed)
SANITY_BOUNDS = {
    "era5_normals_mat.tif": (-30.0, 20.0),
    "era5_normals_map.tif": (0.0, 5000.0),
    "era5_normals_d2m_annual.tif": (-40.0, 20.0),
    "era5_normals_gdd5.tif": (0.0, 4000.0),
    "era5_normals_ffp.tif": (0.0, 365.0),
    "era5_normals_vpd.tif": (0.0, 3.0),
    "era5_normals_twarm.tif": (-10.0, 35.0),
    "era5_normals_tcold.tif": (-50.0, 15.0),
    "era5_normals_pseas.tif": (0.0, 100.0),
    "era5_normals_pwarm.tif": (0.0, 2000.0),
    "era5_normals_pcold.tif": (0.0, 2000.0),
    "era5_normals_continentality.tif": (0.0, 60.0),
}

# Expected Canada-clip grid: the 0.25-degree ERA5 grid over the CDS area
# [84N, -141W, 41N, -52E]. Coordinates are pixel centres, so the transform
# origin sits half a pixel outside the corner coordinate.
EXPECTED_SHAPE = (173, 357)  # (height, width) = (latitude, longitude)
EXPECTED_TRANSFORM = Affine(0.25, 0.0, -141.125, 0.0, -0.25, 84.125)

NODATA_VALUE = -9999.0


def expected_files() -> list[str]:
    monthly = [
        f"era5_normals_{stem}_monthly_{month:02d}.tif"
        for stem in EXPECTED_MONTHLY_STEMS
        for month in range(1, 13)
    ]
    return monthly + EXPECTED_SINGLE_FILES


def check_geotiff(path: Path) -> list[str]:
    """Return a list of structural problems with one COG (empty = healthy)."""
    problems = []
    with rasterio.open(path) as dataset:
        if dataset.driver != "GTiff":
            problems.append(f"driver is {dataset.driver}, expected GTiff")
        if dataset.crs is None or dataset.crs.to_epsg() != 4326:
            problems.append(f"CRS is {dataset.crs}, expected EPSG:4326")
        if dataset.nodata != NODATA_VALUE:
            problems.append(f"nodata is {dataset.nodata}, expected {NODATA_VALUE}")
        if dataset.count != 1:
            problems.append(f"band count is {dataset.count}, expected 1")
        if (dataset.height, dataset.width) != EXPECTED_SHAPE:
            problems.append(
                f"shape is {(dataset.height, dataset.width)}, expected {EXPECTED_SHAPE}"
            )
        if not dataset.transform.almost_equals(EXPECTED_TRANSFORM):
            problems.append(
                f"transform is {tuple(dataset.transform)[:6]}, "
                "expected the Canada-bbox clip transform"
            )
    return problems


def summarize(path: Path) -> tuple[float, float, float, int]:
    """Nodata-aware (min, max, mean, valid_pixel_count) for one raster."""
    with rasterio.open(path) as dataset:
        values = dataset.read(1).astype("float64")
    valid = values[values != NODATA_VALUE]
    if valid.size == 0:
        raise ValueError(f"{path.name}: no valid pixels after nodata masking")
    return float(valid.min()), float(valid.max()), float(valid.mean()), int(valid.size)


def main() -> int:
    failures = []
    files = expected_files()
    print(f"Expecting {len(files)} output files in {OUTPUT_DIR}\n")

    print("--- File inventory (name, size) ---")
    for name in files:
        path = OUTPUT_DIR / name
        if not path.exists():
            failures.append(f"{name}: missing")
            print(f"  MISSING {name}")
            continue
        print(f"  {name:45s} {path.stat().st_size / 1024:8.1f} KiB")

    print("\n--- Structural checks (CRS, nodata, driver, bands, shape, transform) ---")
    for name in files:
        path = OUTPUT_DIR / name
        if not path.exists():
            continue
        for problem in check_geotiff(path):
            failures.append(f"{name}: {problem}")
            print(f"  FAIL {name}: {problem}")
    print("  all present files pass structural checks" if not failures else "")

    print("\n--- Summary statistics (annual + bioclimatic, nodata-aware) ---")
    print(f"  {'file':45s} {'min':>10s} {'max':>10s} {'mean':>10s} {'valid px':>9s}")
    statistics = {}
    for name in EXPECTED_SINGLE_FILES:
        path = OUTPUT_DIR / name
        if not path.exists():
            continue
        try:
            minimum, maximum, mean, count = summarize(path)
        except Exception as error:
            print(f"  ERROR {name}: summary failed: {error}")
            continue
        statistics[name] = (minimum, maximum, mean)
        print(f"  {name:45s} {minimum:10.2f} {maximum:10.2f} {mean:10.2f} {count:9d}")

    print("\n--- Physical sanity checks ---")
    for name, (lower, upper) in SANITY_BOUNDS.items():
        if name not in statistics:
            failures.append(f"{name}: no summary statistics, sanity check skipped")
            continue
        minimum, maximum, _ = statistics[name]
        within_bounds = minimum >= lower and maximum <= upper
        status = "PASS" if within_bounds else "FAIL"
        print(f"  {status} {name}: [{minimum:.2f}, {maximum:.2f}] within [{lower}, {upper}]")
        if not within_bounds:
            failures.append(f"{name}: values outside [{lower}, {upper}]")

    print(f"\n{'VALIDATION PASSED' if not failures else f'VALIDATION FAILED ({len(failures)} problem(s))'}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
