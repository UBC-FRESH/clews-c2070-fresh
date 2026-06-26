#!/usr/bin/env python3
"""Build a tiny SCANFI-derived diagnostic inventory fixture.

The output rows are synthetic proxy cells for testing the ws3 replacement input
shape. They are not sampled raster values.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from fresh_fibre.schema import read_rows, write_rows


DEFAULT_INPUT_DIR = Path("metadata/ffcm-archive/prototype-inputs/scanfi-diagnostic")
DEFAULT_MANIFEST_PATH = DEFAULT_INPUT_DIR / "scanfi_v2_2025_diagnostic_subset_manifest.csv"
DEFAULT_OUTPUT_PATH = DEFAULT_INPUT_DIR / "diagnostic_inventory_cells.csv"

OUTPUT_COLUMNS = [
    "inventory_cell_id",
    "scenario_id",
    "period",
    "province",
    "diagnostic_region",
    "spatial_unit_id",
    "supply_node_id",
    "geometry_ref",
    "representative_area_ha",
    "inventory_year",
    "landcover_class",
    "forest_candidate",
    "biomass_proxy_t_per_ha",
    "height_proxy_m",
    "closure_proxy_pct",
    "age_proxy_years",
    "biomass_source_file",
    "height_source_file",
    "closure_source_file",
    "landcover_source_file",
    "age_source_file",
    "source_manifest_path",
    "value_policy",
    "notes",
]

REQUIRED_LAYER_FAMILIES = {
    "att_biomass": "biomass_source_file",
    "att_height": "height_source_file",
    "att_closure": "closure_source_file",
    "att_nfiLandcover": "landcover_source_file",
    "age_median": "age_source_file",
}

DIAGNOSTIC_CELLS = [
    {
        "inventory_cell_id": "scanfi_bc_coast_wet_forest_001",
        "scenario_id": "scanfi_diagnostic_001",
        "period": "0",
        "province": "BC",
        "diagnostic_region": "coast_wet_forest",
        "spatial_unit_id": "bc_fmu_coast_01",
        "supply_node_id": "supply_coast_01",
        "geometry_ref": "data:diagnostic/scanfi_bc_coast_wet_forest_001",
        "representative_area_ha": 35000,
        "landcover_class": "nfi_forest_proxy",
        "forest_candidate": "true",
        "biomass_proxy_t_per_ha": 220,
        "height_proxy_m": 28,
        "closure_proxy_pct": 72,
        "age_proxy_years": 95,
        "notes": "Synthetic high-biomass coast forest proxy for supply-path design.",
    },
    {
        "inventory_cell_id": "scanfi_bc_coast_second_growth_002",
        "scenario_id": "scanfi_diagnostic_001",
        "period": "0",
        "province": "BC",
        "diagnostic_region": "coast_second_growth",
        "spatial_unit_id": "bc_fmu_coast_01",
        "supply_node_id": "supply_coast_01",
        "geometry_ref": "data:diagnostic/scanfi_bc_coast_second_growth_002",
        "representative_area_ha": 28000,
        "landcover_class": "nfi_forest_proxy",
        "forest_candidate": "true",
        "biomass_proxy_t_per_ha": 155,
        "height_proxy_m": 21,
        "closure_proxy_pct": 64,
        "age_proxy_years": 55,
        "notes": "Synthetic coast second-growth proxy for diagnostic contrast.",
    },
    {
        "inventory_cell_id": "scanfi_bc_int_dry_forest_001",
        "scenario_id": "scanfi_diagnostic_001",
        "period": "0",
        "province": "BC",
        "diagnostic_region": "interior_dry_forest",
        "spatial_unit_id": "bc_fmu_interior_01",
        "supply_node_id": "supply_interior_01",
        "geometry_ref": "data:diagnostic/scanfi_bc_int_dry_forest_001",
        "representative_area_ha": 45000,
        "landcover_class": "nfi_forest_proxy",
        "forest_candidate": "true",
        "biomass_proxy_t_per_ha": 125,
        "height_proxy_m": 18,
        "closure_proxy_pct": 48,
        "age_proxy_years": 85,
        "notes": "Synthetic interior dry-forest proxy for supply-path design.",
    },
    {
        "inventory_cell_id": "scanfi_bc_int_cold_forest_002",
        "scenario_id": "scanfi_diagnostic_001",
        "period": "0",
        "province": "BC",
        "diagnostic_region": "interior_cold_forest",
        "spatial_unit_id": "bc_fmu_interior_01",
        "supply_node_id": "supply_interior_01",
        "geometry_ref": "data:diagnostic/scanfi_bc_int_cold_forest_002",
        "representative_area_ha": 32000,
        "landcover_class": "nfi_forest_proxy",
        "forest_candidate": "true",
        "biomass_proxy_t_per_ha": 145,
        "height_proxy_m": 20,
        "closure_proxy_pct": 55,
        "age_proxy_years": 110,
        "notes": "Synthetic older interior proxy for age/yield-policy design.",
    },
    {
        "inventory_cell_id": "scanfi_bc_int_salvage_001",
        "scenario_id": "scanfi_diagnostic_001",
        "period": "0",
        "province": "BC",
        "diagnostic_region": "interior_disturbance_mix",
        "spatial_unit_id": "bc_fmu_interior_02",
        "supply_node_id": "supply_interior_02",
        "geometry_ref": "data:diagnostic/scanfi_bc_int_salvage_001",
        "representative_area_ha": 26000,
        "landcover_class": "nfi_forest_proxy",
        "forest_candidate": "true",
        "biomass_proxy_t_per_ha": 80,
        "height_proxy_m": 14,
        "closure_proxy_pct": 35,
        "age_proxy_years": 130,
        "notes": "Synthetic lower-closure interior proxy for salvage-style supply tests.",
    },
    {
        "inventory_cell_id": "scanfi_bc_int_residue_001",
        "scenario_id": "scanfi_diagnostic_001",
        "period": "0",
        "province": "BC",
        "diagnostic_region": "interior_residue_candidate",
        "spatial_unit_id": "bc_fmu_interior_02",
        "supply_node_id": "supply_residue_02",
        "geometry_ref": "data:diagnostic/scanfi_bc_int_residue_001",
        "representative_area_ha": 12000,
        "landcover_class": "nfi_forest_proxy",
        "forest_candidate": "true",
        "biomass_proxy_t_per_ha": 65,
        "height_proxy_m": 11,
        "closure_proxy_pct": 28,
        "age_proxy_years": 70,
        "notes": "Synthetic low-biomass residue candidate linked to the BC mini residue node.",
    },
]


def verified_source_files(manifest_path: Path) -> dict[str, str]:
    rows = read_rows(manifest_path)
    verified = {
        row["layer_family"]: row["file_name"]
        for row in rows
        if row["status"] == "selected_verified"
    }
    missing = set(REQUIRED_LAYER_FAMILIES) - set(verified)
    if missing:
        raise ValueError(
            f"{manifest_path} lacks verified SCANFI source rows for {sorted(missing)}"
        )
    return verified


def build_inventory_cells(manifest_path: Path) -> list[dict[str, object]]:
    source_files = verified_source_files(manifest_path)
    rows: list[dict[str, object]] = []
    for cell in DIAGNOSTIC_CELLS:
        row = {
            **cell,
            "inventory_year": "2025",
            "source_manifest_path": str(manifest_path),
            "value_policy": "synthetic_proxy_not_sampled_from_raster",
        }
        for layer_family, output_column in REQUIRED_LAYER_FAMILIES.items():
            row[output_column] = source_files[layer_family]
        rows.append(row)
    validate_inventory_cells(rows)
    return rows


def validate_inventory_cells(rows: list[dict[str, object]]) -> None:
    seen_ids: set[str] = set()
    supply_nodes = {row["supply_node_id"] for row in rows}
    required_supply_nodes = {
        "supply_coast_01",
        "supply_interior_01",
        "supply_interior_02",
        "supply_residue_02",
    }
    if not required_supply_nodes <= supply_nodes:
        missing_nodes = required_supply_nodes - supply_nodes
        raise ValueError(f"Missing required diagnostic supply nodes: {missing_nodes}")

    for row in rows:
        cell_id = str(row["inventory_cell_id"])
        if cell_id in seen_ids:
            raise ValueError(f"Duplicate inventory_cell_id: {cell_id}")
        seen_ids.add(cell_id)
        if row["forest_candidate"] not in {"true", "false"}:
            raise ValueError(
                f"Invalid forest_candidate flag for {cell_id}: {row['forest_candidate']}"
            )
        for field in (
            "representative_area_ha",
            "biomass_proxy_t_per_ha",
            "height_proxy_m",
            "closure_proxy_pct",
            "age_proxy_years",
        ):
            if float(row[field]) < 0:
                raise ValueError(f"Negative {field} for {cell_id}: {row[field]}")
        closure = float(row["closure_proxy_pct"])
        if closure > 100:
            raise ValueError(f"Closure exceeds 100 percent for {cell_id}: {closure}")
        if row["value_policy"] != "synthetic_proxy_not_sampled_from_raster":
            raise ValueError(f"Unexpected value policy for {cell_id}: {row['value_policy']}")


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
    parser.add_argument("--manifest-path", type=Path, default=DEFAULT_MANIFEST_PATH)
    parser.add_argument("--output-path", type=Path, default=DEFAULT_OUTPUT_PATH)
    args = parser.parse_args()

    rows = build_inventory_cells(args.manifest_path)
    write_rows(args.output_path, OUTPUT_COLUMNS, rows)
    write_readme(args.output_path.parent)
    print(f"wrote {len(rows)} SCANFI diagnostic inventory cells to {args.output_path}")


if __name__ == "__main__":
    main()
