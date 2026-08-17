#!/usr/bin/env python3
"""Download SCANFI v2 species crown-closure layers for British Columbia.

This script downloads the SCANFI v2 species-specific crown closure (spsCC_*) 
GeoTIFF rasters for British Columbia from the NRCan FTP server.

The SCANFI v2 dataset contains species crown-closure layers for major tree species
across Canada at 30m resolution. This script filters for BC-relevant species and
downloads only the 2025 vintage (latest) layers.

Species layers downloaded (BC-relevant):
- spsCC_Picea (spruce)
- spsCC_Pinus (pine)  
- spsCC_Abies (fir)
- spsCC_Populus (poplar/aspen)
- spsCC_Betula (birch)
- spsCC_Thuja (cedar)
- spsCC_Tsuga (hemlock)
- spsCC_Larix (larch)
- spsCC_Acer (maple)
- spsCC_Quercus (oak)
- spsCC_Alnus (alder)
- spsCC_Fraxinus (ash)
- spsCC_OtherConif (other conifers)
- spsCC_OtherBroad (other broadleaf)

Data is stored in the DataLad-backed data submodule under 
raw/nrcan/scanfi_v2/species/.

Usage:
    python scripts/download_scanfi_species_bc.py --output-dir data/raw/nrcan/scanfi_v2/species
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import urllib.request
from pathlib import Path
from typing import Optional

try:
    from tqdm import tqdm
except ImportError:
    tqdm = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Configuration (constants at module level for easy discovery)
# ---------------------------------------------------------------------------

# NRCan FTP base URL for SCANFI v2
SCANFI_V2_BASE_URL = "https://ftp.maps.canada.ca/pub/nrcan_rncan/Forests_Foret/SCANFI/v2/"

# SCANFI v2 species crown-closure layer naming pattern:
# SCANFI_spsCC_{SpeciesCode}_{Year}_v2_{Date}.tif
# We target the 2025 vintage (latest available as of 2026)

# BC-relevant species codes from SCANFI v2
BC_SPECIES_CODES = [
    "Picea",      # Spruce
    "Pinus",      # Pine
    "Abies",      # Fir
    "Populus",    # Poplar / Aspen
    "Betula",     # Birch
    "Thuja",      # Cedar (Thuja plicata - western redcedar)
    "Tsuga",      # Hemlock
    "Larix",      # Larch
    "Acer",       # Maple
    "Quercus",    # Oak
    "Alnus",      # Alder
    "Fraxinus",   # Ash
    "OtherConif", # Other conifers
    "OtherBroad", # Other broadleaf
]

# Target year (2025 vintage)
TARGET_YEAR = "2025"
TARGET_VERSION_DATE = "20260119"  # Observed in required-datasets.yaml

# Expected file pattern
FILE_PATTERN = "SCANFI_spsCC_{species}_{year}_v2_{date}.tif"


# ---------------------------------------------------------------------------
# Pure functions (Atomic Predictability)
# ---------------------------------------------------------------------------


def generate_species_urls() -> list[tuple[str, str]]:
    """Generate (url, filename) pairs for all BC species layers."""
    urls: list[tuple[str, str]] = []
    for species in BC_SPECIES_CODES:
        filename = FILE_PATTERN.format(
            species=species,
            year=TARGET_YEAR,
            date=TARGET_VERSION_DATE,
        )
        url = f"{SCANFI_V2_BASE_URL}{filename}"
        urls.append((url, filename))
    return urls


def validate_output_dir(output_dir: Path) -> None:
    """Validate and create output directory."""
    output_dir.mkdir(parents=True, exist_ok=True)
    if not output_dir.is_dir():
        raise NotADirectoryError(f"Output path is not a directory: {output_dir}")


def file_exists_and_valid(filepath: Path, expected_size: Optional[int] = None) -> bool:
    """Check if file exists and optionally validate size."""
    if not filepath.exists():
        return False
    if expected_size is not None and filepath.stat().st_size != expected_size:
        return False
    return True


def compute_sha256(filepath: Path) -> str:
    """Compute SHA-256 hash of a file."""
    sha256 = hashlib.sha256()
    with filepath.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def download_file(
    url: str,
    output_path: Path,
    overwrite: bool = False,
    expected_size: Optional[int] = None,
    progress: bool = True,
) -> bool:
    """Download a single file with progress tracking.

    Args:
        url: Source URL
        output_path: Destination path
        overwrite: Whether to overwrite existing file
        expected_size: Expected file size in bytes (for validation)
        progress: Whether to show progress bar

    Returns:
        True if downloaded (or already exists and valid), False on failure
    """
    if file_exists_and_valid(output_path, expected_size):
        size_mb = output_path.stat().st_size / (1024 * 1024)
        print(f"  SKIP: {output_path.name} ({size_mb:.1f} MB) - already exists")
        return True

    # Create temporary file for atomic write
    temp_path = output_path.with_suffix(output_path.suffix + ".tmp")

    try:
        request = urllib.request.Request(url, headers={"User-Agent": "CLEWS-FRESH/1.0"})
        
        with urllib.request.urlopen(request, timeout=300) as response:
            # Get content length if available
            content_length = response.headers.get("Content-Length")
            total_size = int(content_length) if content_length else None
            
            if expected_size and total_size and total_size != expected_size:
                print(f"  WARNING: Size mismatch for {output_path.name}: "
                      f"expected {expected_size}, server reports {total_size}")
            
            # Progress bar setup
            pbar = None
            if progress and tqdm and total_size:
                pbar = tqdm(
                    total=total_size,
                    unit="B",
                    unit_scale=True,
                    unit_divisor=1024,
                    desc=f"  DOWNLOAD: {output_path.name}",
                    leave=False,
                )

            with temp_path.open("wb") as f:
                while True:
                    chunk = response.read(8192)
                    if not chunk:
                        break
                    f.write(chunk)
                    if pbar:
                        pbar.update(len(chunk))

            if pbar:
                pbar.close()

        # Validate download
        if expected_size and temp_path.stat().st_size != expected_size:
            raise ValueError(
                f"Downloaded file size mismatch: expected {expected_size}, "
                f"got {temp_path.stat().st_size}"
            )

        # Atomic move
        temp_path.replace(output_path)
        size_mb = output_path.stat().st_size / (1024 * 1024)
        print(f"  OK: {output_path.name} ({size_mb:.1f} MB)")
        return True

    except Exception as e:
        print(f"  ERROR: {output_path.name} - {e}", file=sys.stderr)
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)
        return False


# ---------------------------------------------------------------------------
# Main orchestration (clear flow, early exits)
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Download SCANFI v2 species crown-closure layers for BC."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/raw/nrcan/scanfi_v2/species"),
        help="Output directory for GeoTIFF files "
             "(default: data/raw/nrcan/scanfi_v2/species)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing files",
    )
    parser.add_argument(
        "--species",
        nargs="+",
        choices=BC_SPECIES_CODES,
        default=BC_SPECIES_CODES,
        help="Species codes to download (default: all BC-relevant species)",
    )
    parser.add_argument(
        "--year",
        default=TARGET_YEAR,
        help=f"Target year vintage (default: {TARGET_YEAR})",
    )
    parser.add_argument(
        "--date",
        default=TARGET_VERSION_DATE,
        help=f"Version date in filename (default: {TARGET_VERSION_DATE})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be downloaded without downloading",
    )
    parser.add_argument(
        "--no-progress",
        action="store_true",
        help="Disable progress bars",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        help="Write download manifest CSV to this path",
    )
    args = parser.parse_args()

    # Validate output directory
    try:
        validate_output_dir(args.output_dir)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    # Build download list
    download_list: list[tuple[str, str]] = []
    for species in args.species:
        filename = FILE_PATTERN.format(
            species=species,
            year=args.year,
            date=args.date,
        )
        url = f"{SCANFI_V2_BASE_URL}{filename}"
        download_list.append((url, filename))

    if args.dry_run:
        print("DRY RUN - would download the following files:")
        for url, filename in download_list:
            print(f"  {filename}")
            print(f"    -> {url}")
        return 0

    # Download files
    print(f"Downloading SCANFI v2 species layers to {args.output_dir}")
    print(f"Base URL: {SCANFI_V2_BASE_URL}")
    print(f"Species: {', '.join(args.species)}")
    print(f"Year: {args.year}, Date: {args.date}")
    print(f"Total files: {len(download_list)}")

    successful: list[tuple[str, Path]] = []
    failed: list[str] = []
    manifest_rows: list[dict[str, str]] = []

    for url, filename in download_list:
        output_path = args.output_dir / filename
        success = download_file(
            url=url,
            output_path=output_path,
            overwrite=args.overwrite,
            progress=not args.no_progress,
        )
        if success:
            successful.append((filename, output_path))
            manifest_rows.append({
                "filename": filename,
                "url": url,
                "local_path": str(output_path),
                "size_bytes": str(output_path.stat().st_size),
                "sha256": compute_sha256(output_path),
                "status": "downloaded",
            })
        else:
            failed.append(filename)
            manifest_rows.append({
                "filename": filename,
                "url": url,
                "local_path": "",
                "size_bytes": "0",
                "sha256": "",
                "status": "failed",
            })

    # Summary
    print("\n--- Download Summary ---")
    print(f"Successful: {len(successful)}")
    print(f"Failed: {len(failed)}")

    if failed:
        print(f"Failed files: {', '.join(failed)}")

    # Write manifest if requested
    if args.manifest:
        import csv
        manifest_fields = ["filename", "url", "local_path", "size_bytes", "sha256", "status"]
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        with args.manifest.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=manifest_fields)
            writer.writeheader()
            writer.writerows(manifest_rows)
        print(f"Manifest written to: {args.manifest}")

    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())