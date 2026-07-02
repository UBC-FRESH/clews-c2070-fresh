#!/usr/bin/env python3
"""Build the SCANFI v2 2025 diagnostic inventory subset manifest."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from fresh_fibre.schema import read_rows, write_rows


DEFAULT_SOURCE_MANIFEST = Path("metadata/source-file-manifests/scanfi_v2_files.csv")
DEFAULT_OUTPUT_DIR = Path("metadata/ffcm-archive/prototype-inputs/scanfi-diagnostic")
DEFAULT_OUTPUT_PATH = DEFAULT_OUTPUT_DIR / "scanfi_v2_2025_diagnostic_subset_manifest.csv"

OUTPUT_COLUMNS = [
    "source_id",
    "file_name",
    "layer_family",
    "year",
    "source_url",
    "size_label",
    "size_bytes",
    "modified",
    "license_position",
    "intended_ws3_role",
    "status",
    "notes",
]

SELECTED_FILES = [
    (
        "SCANFI_att_biomass_2025_v2_20260119.tif",
        "att_biomass",
        "2025",
        "inventory_biomass_screening",
        "Selected 2025 aboveground biomass raster for first diagnostic inventory fixture.",
    ),
    (
        "SCANFI_att_height_2025_v2_20260119.tif",
        "att_height",
        "2025",
        "inventory_height_screening",
        "Selected 2025 forest height raster for first diagnostic inventory fixture.",
    ),
    (
        "SCANFI_att_closure_2025_v2_20260119.tif",
        "att_closure",
        "2025",
        "inventory_canopy_closure_screening",
        "Selected 2025 crown closure raster for first diagnostic inventory fixture.",
    ),
    (
        "SCANFI_att_nfiLandcover_2025_v2_20260119.tif",
        "att_nfiLandcover",
        "2025",
        "inventory_landcover_screening",
        "Selected 2025 NFI-compatible land-cover raster for first diagnostic inventory fixture.",
    ),
    (
        "SCANFI_age_median_2025_v2_20260119.tif",
        "age_median",
        "2025",
        "inventory_age_screening",
        "Selected 2025 median age raster for first diagnostic inventory fixture.",
    ),
    (
        "SCANFI_north_extrapol_area.tif",
        "north_extrapol_area",
        "",
        "inventory_mask_follow_up",
        "Recommended in source review but absent from the current local and live SCANFI v2 directory listings.",
    ),
    (
        "_SCANFI_v2_read_me.txt",
        "documentation",
        "",
        "source_documentation",
        "Selected English README for provenance and modelling notes.",
    ),
    (
        "___SCANFI v2 – EN - Update report.pdf",
        "documentation",
        "",
        "source_documentation",
        "Selected English update report for provenance and v2 change notes.",
    ),
]


def build_manifest(source_manifest: Path) -> list[dict[str, object]]:
    source_rows = {row["name"]: row for row in read_rows(source_manifest)}
    output_rows: list[dict[str, object]] = []
    for file_name, layer_family, year, intended_role, notes in SELECTED_FILES:
        source_row = source_rows.get(file_name)
        status = "selected_verified" if source_row else "missing_from_source_manifest"
        output_rows.append(
            {
                "source_id": "scanfi_v2",
                "file_name": file_name,
                "layer_family": layer_family,
                "year": year,
                "source_url": source_row["url"] if source_row else "",
                "size_label": source_row["size_label"] if source_row else "",
                "size_bytes": source_row["size_bytes"] if source_row else "",
                "modified": source_row["modified"] if source_row else "",
                "license_position": "Open Government Licence - Canada",
                "intended_ws3_role": intended_role,
                "status": status,
                "notes": notes,
            }
        )
    return output_rows


def validate_manifest(rows: list[dict[str, object]]) -> None:
    verified_rows = [row for row in rows if row["status"] == "selected_verified"]
    missing_rows = [row for row in rows if row["status"] == "missing_from_source_manifest"]
    if len(verified_rows) != 7:
        raise ValueError(f"Expected 7 verified SCANFI rows, found {len(verified_rows)}")
    if [row["file_name"] for row in missing_rows] != ["SCANFI_north_extrapol_area.tif"]:
        raise ValueError(f"Unexpected missing SCANFI rows: {missing_rows}")
    for row in verified_rows:
        if not row["source_url"] or not row["size_bytes"]:
            raise ValueError(f"Verified SCANFI row lacks URL or size: {row}")


def write_readme(output_dir: Path) -> None:
    text = """# SCANFI Diagnostic Inventory Subset

This directory contains lightweight metadata for the first SCANFI v2 diagnostic
inventory subset selected for the ws3 supply replacement path. It does not
contain raw SCANFI rasters, reports, or downloaded payloads.

`scanfi_v2_2025_diagnostic_subset_manifest.csv` is generated from
`metadata/source-file-manifests/scanfi_v2_files.csv` by
`scripts/ffcm_archive/build_scanfi_diagnostic_manifest.py`.

The five 2025 raster rows, English README, and English update report are
verified against the local SCANFI source-file manifest. The north extrapolation
mask is recorded as missing because it was recommended during source review but
is absent from the current local and live public SCANFI v2 directory listings.

`diagnostic_inventory_cells.csv` is generated by
`scripts/ffcm_archive/build_scanfi_diagnostic_inventory_fixture.py`. It contains
synthetic proxy cells for designing the accepted ws3 inventory input path. The
cell values are not sampled from SCANFI rasters; they are deliberately labelled
`synthetic_proxy_not_sampled_from_raster` and only use SCANFI as source
provenance for the planned biomass, height, closure, land-cover, and age fields.

`diagnostic_yield_rules.csv` and `scanfi_diagnostic_harvest_output.csv` are
generated by `scripts/ffcm_archive/build_scanfi_diagnostic_supply.py`. The yield
rules are synthetic placeholders, and the harvest-output rows aggregate
inventory-cell contributions at the supply-node/commodity boundary for LP
handoff.
"""
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "README.md").write_text(text, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-manifest", type=Path, default=DEFAULT_SOURCE_MANIFEST)
    parser.add_argument("--output-path", type=Path, default=DEFAULT_OUTPUT_PATH)
    args = parser.parse_args()
    rows = build_manifest(args.source_manifest)
    validate_manifest(rows)
    write_rows(args.output_path, OUTPUT_COLUMNS, rows)
    write_readme(args.output_path.parent)
    print(f"wrote {len(rows)} SCANFI diagnostic subset rows to {args.output_path}")


if __name__ == "__main__":
    main()
