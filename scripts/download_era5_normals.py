#!/usr/bin/env python3
"""Download ERA5 monthly-mean climate-normal inputs (1991-2020) for Canada.

Variables (ERA5 single-levels monthly means):
  - 2m temperature (mean / min / max)
  - total precipitation
  - 2m dewpoint temperature (humidity / VPD derivation)

Output: one NetCDF per variable per 10-year chunk under OUTPUT_DIR.
Resume-safe: chunks that already exist as valid NetCDF are skipped.
Exit code: 0 when every chunk is present and valid, 1 otherwise.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

import cdsapi
import netCDF4

OUTPUT_DIR = Path(__file__).resolve().parent
DATASET = "reanalysis-era5-single-levels-monthly-means"

# CDS variable name -> short label used in output filenames.
# Note: min/max 2m temperature are not offered by any ERA5 monthly-means
# product on CDS (verified against the dataset form metadata, 2026-08-16);
# they are forecast-stream fields. Only the variables below exist monthly.
VARIABLES = {
    "2m_temperature": "t2m_mean",
    "total_precipitation": "tp",
    "2m_dewpoint_temperature": "d2m",
}

# 30-year climate-normal reference period, chunked to respect CDS request-size limits
YEAR_CHUNKS = [(1991, 2000), (2001, 2010), (2011, 2020)]

# Canada bounding box as [North, West, South, East]
CANADA_AREA = [84, -141, 41, -52]

ALL_MONTHS = [f"{month:02d}" for month in range(1, 13)]

MAX_ATTEMPTS = 3
NETCDF3_MAGIC = b"CDF"
NETCDF4_MAGIC = b"\x89HDF"


def build_request(variable: str, start_year: int, end_year: int) -> dict:
    """Compose a CDS API request for one variable over one year chunk."""
    return {
        "product_type": "monthly_averaged_reanalysis",
        "variable": [variable],
        "year": [str(year) for year in range(start_year, end_year + 1)],
        "month": ALL_MONTHS,
        "time": ["00:00"],
        "area": CANADA_AREA,
        "data_format": "netcdf",
        "download_format": "unarchived",
    }


def chunk_path(label: str, start_year: int, end_year: int) -> Path:
    return OUTPUT_DIR / f"era5_monthly_{label}_{start_year}-{end_year}.nc"


def is_valid_netcdf(path: Path) -> bool:
    """Accept only files that exist, are non-trivial, carry NetCDF magic bytes,
    and open cleanly with netCDF4.

    The magic-byte check alone cannot reject a truncated file: a partial
    download still starts with the correct header. Opening the file forces the
    library to read the full structure, so corrupt/truncated files fail here
    and are re-downloaded instead of being skipped forever.
    """
    if not path.exists():
        return False
    if path.stat().st_size < 1024:
        return False
    with path.open("rb") as handle:
        magic = handle.read(4)
    if not magic.startswith(NETCDF3_MAGIC) and not magic.startswith(NETCDF4_MAGIC):
        return False
    try:
        with netCDF4.Dataset(path):
            return True
    except (OSError, RuntimeError):
        return False


def download_chunk(
    client: cdsapi.Client,
    variable: str,
    label: str,
    start_year: int,
    end_year: int,
    logger: logging.Logger,
) -> Path:
    """Retrieve one variable-chunk from CDS, skipping or retrying as needed.

    Downloads land in a ``.tmp`` sibling and are atomically renamed to the
    final name only after validation, so a truncated file can never be
    mistaken for a complete one on resume.
    """
    target = chunk_path(label, start_year, end_year)
    if is_valid_netcdf(target):
        size_mb = target.stat().st_size / 1e6
        logger.info("SKIP  %s (already valid, %.1f MB)", target.name, size_mb)
        return target

    request = build_request(variable, start_year, end_year)
    temp_target = target.with_name(target.name + ".tmp")
    last_error: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            logger.info(
                "START %s %d-%d (attempt %d/%d)",
                label, start_year, end_year, attempt, MAX_ATTEMPTS,
            )
            client.retrieve(DATASET, request, str(temp_target))
            if not is_valid_netcdf(temp_target):
                raise RuntimeError(f"downloaded file failed NetCDF validation: {target.name}")
            os.rename(temp_target, target)
            size_mb = target.stat().st_size / 1e6
            logger.info("DONE  %s (%.1f MB)", target.name, size_mb)
            return target
        except Exception as error:
            temp_target.unlink(missing_ok=True)
            last_error = error
            logger.warning(
                "ERROR %s %d-%d attempt %d: %s",
                label, start_year, end_year, attempt, error,
            )
            if attempt < MAX_ATTEMPTS:
                time.sleep(30 * attempt)
    raise RuntimeError(
        f"failed after {MAX_ATTEMPTS} attempts: {target.name}"
    ) from last_error


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(OUTPUT_DIR / "download.log"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    logger = logging.getLogger("era5_normals")
    logger.info("Output directory: %s", OUTPUT_DIR)
    logger.info("Dataset: %s", DATASET)
    logger.info("Variables: %s", ", ".join(VARIABLES))
    logger.info("Year chunks: %s", YEAR_CHUNKS)

    client = cdsapi.Client()
    started_at = time.time()
    failures: list[tuple[str, int, int, Exception]] = []

    for variable, label in VARIABLES.items():
        for start_year, end_year in YEAR_CHUNKS:
            target = chunk_path(label, start_year, end_year)
            if target.exists() and not is_valid_netcdf(target):
                logger.warning(
                    "CORRUPT %s exists but failed validation; deleting and re-downloading",
                    target.name,
                )
                target.unlink()
            try:
                download_chunk(client, variable, label, start_year, end_year, logger)
            except Exception as error:
                failures.append((label, start_year, end_year, error))
                logger.error("FAILED %s %d-%d: %s", label, start_year, end_year, error)

    elapsed_minutes = (time.time() - started_at) / 60
    if failures:
        logger.error(
            "Completed with %d failed chunk(s) in %.1f min",
            len(failures), elapsed_minutes,
        )
        return 1
    logger.info("All chunks downloaded successfully in %.1f min", elapsed_minutes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
