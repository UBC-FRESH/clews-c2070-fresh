#!/usr/bin/env python3
"""Add CanLaBS dNBR provenance columns to diagnostic_inventory_cells.csv.

This script:
1. Reads 6 window bounds from manifest CSVs
2. Extracts CanLaBS dNBR summaries for each window using canlabs_severity module
3. Adds CanLaBS provenance columns to diagnostic_inventory_cells.csv

New columns added:
- canlabs_dnbr_mean
- canlabs_dnbr_min
- canlabs_dnbr_max
- canlabs_dnbr_median
- canlabs_valid_pixels
- canlabs_nodata_pixels
- canlabs_total_pixels
- canlabs_value_policy (raw_dnbr_provenance)
- canlabs_window_bounds (the LCC bounds used)
- canlabs_source_path (path to CanLaBS raster)

No severity classes, yield multipliers, or model behavior changes.
"""

from __future__ import annotations

import csv
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import rasterio
from rasterio.windows import Window

# Add fresh_fibre to path for canlabs_severity import
sys.path.insert(0, str(Path(__file__).parent.parent / "fresh_fibre"))

from canlabs_severity import CanLaBSWindowSummary, read_window


# ---------------------------------------------------------------------------
# Configuration (constants at module level for easy discovery)
# ---------------------------------------------------------------------------

CANALBS_RASTER_PATH = Path(
    "/srv/shared-data/gep/jupyterhub16-projects/clews-c2070-fresh/"
    "tmp/canlabs_v2_fresh/CanLaBS_1985_2024_v20260121_fresh.tif"
)

MANIFEST_DIR = Path(
    "/srv/shared-data/gep/jupyterhub16-projects/clews-c2070-fresh/"
    "data/manifests/scanfi-diagnostic/tiny-bc-real-sampling/"
)

TARGET_CSV = Path(
    "/srv/shared-data/gep/jupyterhub16-projects/clews-c2070-fresh/"
    "metadata/ffcm-archive/prototype-inputs/bc-mini-scanfi-real-sampled/"
    "diagnostic_inventory_cells.csv"
)

# The CanLaBS raster uses NAD83 Canada Lambert (no EPSG code in file)
# We validate by checking the CRS name contains expected identifiers
EXPECTED_CRS_KEYWORDS = ("NAD83", "Canada_Lambert", "Lambert_Conformal_Conic")
EXPECTED_RESOLUTION_M = 30.0
NODATA_VALUE = -32768
VALUE_POLICY = "raw_dnbr_provenance"


# ---------------------------------------------------------------------------
# Typed data structures (Parse, Don't Validate)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class WindowBounds:
    """Parsed LCC window bounds from manifest."""
    left: float
    top: float
    right: float
    bottom: float
    fixture_cell_id: str

    @property
    def width(self) -> int:
        return int((self.right - self.left) / EXPECTED_RESOLUTION_M)

    @property
    def height(self) -> int:
        return int((self.top - self.bottom) / EXPECTED_RESOLUTION_M)

    @property
    def col_off(self) -> int:
        # We need to compute this from the raster's transform
        # For now, we'll compute it in the reader using the bounds directly
        return 0

    @property
    def row_off(self) -> int:
        return 0

    def to_rasterio_window(self, transform: rasterio.Affine) -> Window:
        """Convert LCC bounds to rasterio Window using the raster's transform."""
        # transform * (col, row) = (x, y)
        # We need inverse: (col, row) = ~transform * (x, y)
        inv_transform = ~transform
        col_off, row_off = inv_transform * (self.left, self.top)
        width = int((self.right - self.left) / EXPECTED_RESOLUTION_M)
        height = int((self.top - self.bottom) / EXPECTED_RESOLUTION_M)
        return Window(
            col_off=int(round(col_off)),
            row_off=int(round(row_off)),
            width=width,
            height=height,
        )

    def __str__(self) -> str:
        return f"{self.left},{self.top},{self.right},{self.bottom}"


# ---------------------------------------------------------------------------
# Pure functions (Atomic Predictability)
# ---------------------------------------------------------------------------

def parse_window_bounds(bounds_str: str, fixture_cell_id: str) -> WindowBounds:
    """Parse LCC bounds string from manifest into typed WindowBounds.

    Manifest format is: "left,bottom,right,top" (LCC coordinates, Y increases northward)

    Args:
        bounds_str: String like "-2015811,7484189,-1984191,7515809"
        fixture_cell_id: Identifier for error messages

    Returns:
        WindowBounds with validated numeric values

    Raises:
        ValueError: If bounds string is malformed
    """
    if not bounds_str:
        raise ValueError(f"Empty bounds string for {fixture_cell_id}")

    parts = bounds_str.strip().strip('"').split(",")
    if len(parts) != 4:
        raise ValueError(
            f"Expected 4 comma-separated values in bounds for {fixture_cell_id}, "
            f"got {len(parts)}: {bounds_str}"
        )

    try:
        left = float(parts[0])
        bottom = float(parts[1])
        right = float(parts[2])
        top = float(parts[3])
    except ValueError as e:
        raise ValueError(f"Non-numeric bounds for {fixture_cell_id}: {bounds_str}") from e

    if left >= right:
        raise ValueError(f"left >= right for {fixture_cell_id}: {left} >= {right}")
    if bottom >= top:
        raise ValueError(f"bottom >= top for {fixture_cell_id}: {bottom} >= {top}")

    return WindowBounds(
        left=left,
        top=top,
        right=right,
        bottom=bottom,
        fixture_cell_id=fixture_cell_id,
    )


def read_manifest_window_bounds(manifest_dir: Path) -> dict[str, WindowBounds]:
    """Read window bounds from all manifest CSVs in directory.

    Args:
        manifest_dir: Directory containing per-cell manifest CSVs

    Returns:
        Dict mapping fixture_cell_id to WindowBounds

    Raises:
        FileNotFoundError: If manifest directory doesn't exist
        ValueError: If no valid manifests found
    """
    if not manifest_dir.exists():
        raise FileNotFoundError(f"Manifest directory not found: {manifest_dir}")

    manifest_files = sorted(manifest_dir.glob("*.csv"))
    if not manifest_files:
        raise ValueError(f"No manifest CSVs found in {manifest_dir}")

    bounds_map: dict[str, WindowBounds] = {}

    for manifest_path in manifest_files:
        with manifest_path.open(newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                fixture_id = row.get("fixture_cell_id", "")
                bounds_str = row.get("window_bounds_lcc", "")
                if fixture_id and bounds_str:
                    if fixture_id not in bounds_map:
                        bounds_map[fixture_id] = parse_window_bounds(bounds_str, fixture_id)

    if not bounds_map:
        raise ValueError(f"No valid window bounds found in {manifest_dir}")

    return bounds_map


def extract_canlabs_summary(
    raster_path: Path,
    window_bounds: WindowBounds,
) -> CanLaBSWindowSummary:
    """Extract CanLaBS dNBR summary for a single window.

    Args:
        raster_path: Path to CanLaBS dNBR raster
        window_bounds: Parsed LCC window bounds

    Returns:
        CanLaBSWindowSummary with raw dNBR statistics
    """
    if not raster_path.exists():
        raise FileNotFoundError(f"CanLaBS raster not found: {raster_path}")

    with rasterio.open(raster_path) as src:
        # Validate raster properties
        if src.count != 1:
            raise ValueError(f"Expected single-band raster, got {src.count} bands")

        actual_crs = str(src.crs) if src.crs else ""
        # Validate CRS by checking for expected keywords (raster has no EPSG code)
        for keyword in EXPECTED_CRS_KEYWORDS:
            if keyword not in actual_crs:
                raise ValueError(
                    f"CRS validation failed: expected keyword '{keyword}' not found in "
                    f"raster CRS: {actual_crs}"
                )

        res_x, res_y = src.res
        if abs(res_x - EXPECTED_RESOLUTION_M) / EXPECTED_RESOLUTION_M > 0.01:
            raise ValueError(f"Resolution X mismatch: expected ~{EXPECTED_RESOLUTION_M} m, got {res_x} m")
        if abs(res_y - EXPECTED_RESOLUTION_M) / EXPECTED_RESOLUTION_M > 0.01:
            raise ValueError(f"Resolution Y mismatch: expected ~{EXPECTED_RESOLUTION_M} m, got {res_y} m")

        # Convert LCC bounds to rasterio window
        window = window_bounds.to_rasterio_window(src.transform)

        # Read and summarize
        return read_window(
            raster_path=raster_path,
            window=window,
            expected_crs=actual_crs,  # Use actual CRS from raster
            expected_resolution_m=EXPECTED_RESOLUTION_M,
            nodata_value=NODATA_VALUE,
            value_policy=VALUE_POLICY,
        )


def build_provenance_row(summary: CanLaBSWindowSummary, bounds: WindowBounds) -> dict[str, str]:
    """Build provenance column values from summary and bounds.

    Args:
        summary: CanLaBS window summary
        bounds: Original LCC window bounds

    Returns:
        Dict of column_name -> string value for CSV
    """
    return {
        "canlabs_dnbr_mean": f"{summary.mean_value:.6f}" if summary.mean_value is not None else "",
        "canlabs_dnbr_min": str(summary.min_value) if summary.min_value is not None else "",
        "canlabs_dnbr_max": str(summary.max_value) if summary.max_value is not None else "",
        "canlabs_dnbr_median": f"{summary.median_value:.6f}" if summary.median_value is not None else "",
        "canlabs_valid_pixels": str(summary.valid_count),
        "canlabs_nodata_pixels": str(summary.nodata_count),
        "canlabs_total_pixels": str(summary.valid_count + summary.nodata_count),
        "canlabs_value_policy": summary.value_policy,
        "canlabs_window_bounds": str(bounds),
        "canlabs_source_path": summary.source_path,
    }


# ---------------------------------------------------------------------------
# Main orchestration (clear flow, early exits)
# ---------------------------------------------------------------------------

def main() -> int:
    """Main entry point. Returns exit code."""
    # Guard: validate inputs exist
    if not CANALBS_RASTER_PATH.exists():
        print(f"ERROR: CanLaBS raster not found at {CANALBS_RASTER_PATH}", file=sys.stderr)
        return 1

    if not MANIFEST_DIR.exists():
        print(f"ERROR: Manifest directory not found at {MANIFEST_DIR}", file=sys.stderr)
        return 1

    if not TARGET_CSV.exists():
        print(f"ERROR: Target CSV not found at {TARGET_CSV}", file=sys.stderr)
        return 1

    # Step 1: Read window bounds from manifests
    print(f"Reading window bounds from {MANIFEST_DIR}...")
    try:
        bounds_map = read_manifest_window_bounds(MANIFEST_DIR)
    except (FileNotFoundError, ValueError) as e:
        print(f"ERROR reading manifests: {e}", file=sys.stderr)
        return 1

    print(f"Found {len(bounds_map)} window bounds: {list(bounds_map.keys())}")

    # Step 2: Extract CanLaBS summaries for each window
    print(f"Extracting CanLaBS dNBR summaries from {CANALBS_RASTER_PATH}...")
    provenance_map: dict[str, dict[str, str]] = {}

    for fixture_id, bounds in bounds_map.items():
        try:
            summary = extract_canlabs_summary(CANALBS_RASTER_PATH, bounds)
            provenance_map[fixture_id] = build_provenance_row(summary, bounds)
            print(
                f"  {fixture_id}: valid={summary.valid_count}, "
                f"mean={summary.mean_value:.2f}, "
                f"min={summary.min_value}, max={summary.max_value}"
            )
        except Exception as e:
            print(f"ERROR processing {fixture_id}: {e}", file=sys.stderr)
            return 1

    # Step 3: Read target CSV and add provenance columns
    print(f"Updating {TARGET_CSV}...")
    try:
        with TARGET_CSV.open(newline="") as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames or []
            rows = list(reader)
    except Exception as e:
        print(f"ERROR reading target CSV: {e}", file=sys.stderr)
        return 1

    # New columns to add (in specified order)
    new_columns = [
        "canlabs_dnbr_mean",
        "canlabs_dnbr_min",
        "canlabs_dnbr_max",
        "canlabs_dnbr_median",
        "canlabs_valid_pixels",
        "canlabs_nodata_pixels",
        "canlabs_total_pixels",
        "canlabs_value_policy",
        "canlabs_window_bounds",
        "canlabs_source_path",
    ]

    # Add new columns to fieldnames if not present
    for col in new_columns:
        if col not in fieldnames:
            fieldnames.append(col)

    # Update each row with provenance data
    for row in rows:
        # Match by inventory_cell_id which corresponds to fixture_cell_id
        inventory_cell_id = row.get("inventory_cell_id", "")
        # The inventory_cell_id format is like "tiny_bc_real_sample_coast_wet_001"
        # which matches the fixture_cell_id in manifests
        if inventory_cell_id in provenance_map:
            row.update(provenance_map[inventory_cell_id])
        else:
            # Fill with empty strings if no match
            for col in new_columns:
                row[col] = ""

    # Step 4: Write updated CSV
    try:
        with TARGET_CSV.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
    except Exception as e:
        print(f"ERROR writing target CSV: {e}", file=sys.stderr)
        return 1

    print(f"Successfully updated {TARGET_CSV} with {len(new_columns)} new CanLaBS provenance columns")
    return 0


if __name__ == "__main__":
    sys.exit(main())