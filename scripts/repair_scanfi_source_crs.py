#!/usr/bin/env python3
"""Repair CRS metadata on the diagnostic SCANFI windows.

The SCANFI source GeoTIFFs use an unregistered NAD83 Lambert Conformal Conic
CRS with latitude of false origin 0. Earlier diagnostic windows were labelled
EPSG:3978, which has latitude of false origin 49 and places the windows in Asia.
This repair preserves pixel values, transforms, and numeric bounds; it changes
only the CRS metadata and manifest label.
"""
from __future__ import annotations

import csv
from pathlib import Path

import rasterio
from pyproj import CRS

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/raw/scanfi/v2/SCANFI_att_biomass_2025_v2_20260119.tif"
WINDOW_ROOT = ROOT / "data/derived/scanfi-diagnostic/tiny-bc-real-sampling/windows"
MANIFEST_ROOT = ROOT / "data/manifests/scanfi-diagnostic/tiny-bc-real-sampling"
CRS_LABEL = "NAD83 Canada Lambert Conformal Conic (SCANFI source WKT; lat_0=0; lat_1=49; lat_2=77)"


def main() -> None:
    with rasterio.open(SOURCE) as source:
        source_crs = CRS.from_wkt(source.crs.to_wkt())
        source_wkt = source_crs.to_wkt()
    changed = 0
    for path in sorted(WINDOW_ROOT.glob("*/*.tif")):
        with rasterio.open(path, "r+") as dataset:
            dataset.crs = source_wkt
        changed += 1

    manifests = 0
    for path in sorted(MANIFEST_ROOT.glob("tiny_bc_real_sample_*.csv")):
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            rows = list(reader)
            fieldnames = reader.fieldnames or []
        for row in rows:
            row["window_crs"] = CRS_LABEL
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
        manifests += 1
    print(f"repaired {changed} TIFF CRS tags and {manifests} manifests")
    print(f"source CRS: {CRS_LABEL}")


if __name__ == "__main__":
    main()
