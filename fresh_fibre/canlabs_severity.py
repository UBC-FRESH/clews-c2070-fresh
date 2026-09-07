"""CanLaBS v2 burn-severity raster window summaries (raw dNBR, no classification).

This module provides a small, isolated interface for reading and summarising
windows of the CanLaBS v2 primary severity raster.  It is deliberately
**scientifically neutral**:

* Values are raw difference-normalised burn ratio (dNBR) integers.
* No severity class, threshold, multiplier, yield attenuation, or production
  calibration is implied or applied by this module.
* Negative and positive values are both valid; the declared nodata sentinel
  (``-32768`` for int16) is the only excluded value.

Importing this module does **not** require ``rasterio`` or ``numpy``.  Both
are loaded lazily on first use so that the rest of ``fresh_fibre`` remains
lightweight.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
from pathlib import Path


# ---------------------------------------------------------------------------
# Lazy import helpers
# ---------------------------------------------------------------------------

def _import_rasterio():
    """Return the ``rasterio`` module, importing it on first call."""
    import rasterio  # noqa: F401
    return rasterio


def _import_numpy():
    """Return the ``numpy`` module, importing it on first call."""
    import numpy  # noqa: F0401
    return numpy


# ---------------------------------------------------------------------------
# Typed immutable summary
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CanLaBSWindowSummary:
    """Immutable summary of a single CanLaBS raster window.

    Attributes
    ----------
    valid_count : int
        Number of pixels whose value is not the declared nodata sentinel.
    nodata_count : int
        Number of pixels equal to the declared nodata sentinel.
    min_value : int | None
        Minimum valid (non-nodata) value, or ``None`` when all pixels are nodata.
    max_value : int | None
        Maximum valid (non-nodata) value, or ``None`` when all pixels are nodata.
    mean_value : float | None
        Arithmetic mean of valid values, or ``None`` when all pixels are nodata.
    median_value : float | None
        Median of valid values, or ``None`` when all pixels are nodata.
    source_path : str
        Filesystem path (or URI) of the source raster.
    source_crs : str
        CRS string of the source raster (e.g. ``"EPSG:3978"``).
    resolution_m : float
        Pixel resolution in metres (typically 30.0).
    nodata_value : int
        Declared nodata sentinel value.
    value_policy : str
        Policy label describing the provenance / calibration status of the
        raw values.  Defaults to a descriptive placeholder.
    manifest_ref : str | None
        Optional reference to a manifest or checksum file.
    checksum_sha256 : str | None
        Optional SHA-256 hex digest of the source raster file.
    window_shape : tuple[int, int]
        ``(height, width)`` of the window in pixels.
    """

    valid_count: int
    nodata_count: int
    min_value: Optional[int]
    max_value: Optional[int]
    mean_value: Optional[float]
    median_value: Optional[float]
    source_path: str
    source_crs: str
    resolution_m: float
    nodata_value: int
    value_policy: str = "raw_dnbr_not_classified"
    manifest_ref: Optional[str] = None
    checksum_sha256: Optional[str] = None
    window_shape: tuple[int, int] = (0, 0)


# ---------------------------------------------------------------------------
# Pure-array summariser (no rasterio dependency)
# ---------------------------------------------------------------------------

def summarise_array(
    values: "list[int]",
    nodata_value: int,
    *,
    source_path: str = "",
    source_crs: str = "",
    resolution_m: float = 0.0,
    value_policy: str = "raw_dnbr_not_classified",
    manifest_ref: Optional[str] = None,
    checksum_sha256: Optional[str] = None,
    window_shape: tuple[int, int] = (0, 0),
) -> CanLaBSWindowSummary:
    """Summarise a flat list of integer values as a CanLaBS window.

    Parameters
    ----------
    values : list[int]
        Flat list of integer pixel values (may include the nodata sentinel).
    nodata_value : int
        The declared nodata sentinel to exclude from statistics.
    source_path, source_crs, resolution_m, value_policy, manifest_ref,
    checksum_sha256, window_shape
        Metadata forwarded to the returned :class:`CanLaBSWindowSummary`.

    Returns
    -------
    CanLaBSWindowSummary
        Immutable summary.  When all values are nodata, numeric statistics
        are ``None`` and counts reflect the all-nodata state.
    """
    import numpy as np  # lazy

    arr = np.asarray(values, dtype=np.int32)
    mask = arr != nodata_value
    valid = arr[mask]
    nodata_count = int((~mask).sum())
    valid_count = int(mask.sum())

    if valid_count == 0:
        return CanLaBSWindowSummary(
            valid_count=0,
            nodata_count=nodata_count,
            min_value=None,
            max_value=None,
            mean_value=None,
            median_value=None,
            source_path=source_path,
            source_crs=source_crs,
            resolution_m=resolution_m,
            nodata_value=nodata_value,
            value_policy=value_policy,
            manifest_ref=manifest_ref,
            checksum_sha256=checksum_sha256,
            window_shape=window_shape,
        )

    return CanLaBSWindowSummary(
        valid_count=valid_count,
        nodata_count=nodata_count,
        min_value=int(valid.min()),
        max_value=int(valid.max()),
        mean_value=float(valid.mean()),
        median_value=float(np.median(valid)),
        source_path=source_path,
        source_crs=source_crs,
        resolution_m=resolution_m,
        nodata_value=nodata_value,
        value_policy=value_policy,
        manifest_ref=manifest_ref,
        checksum_sha256=checksum_sha256,
        window_shape=window_shape,
    )


# ---------------------------------------------------------------------------
# Raster-window reader
# ---------------------------------------------------------------------------

def read_window(
    raster_path: str | Path,
    window,
    *,
    expected_crs: str = "EPSG:3978",
    expected_resolution_m: float = 30.0,
    nodata_value: int = -32768,
    value_policy: str = "raw_dnbr_not_classified",
    manifest_ref: Optional[str] = None,
    checksum_sha256: Optional[str] = None,
) -> CanLaBSWindowSummary:
    """Read a single window from a CanLaBS v2 primary-severity raster.

    Parameters
    ----------
    raster_path : str | Path
        Path to the BigTIFF raster file.
    window
        A ``rasterio.windows.Window`` (or any object exposing ``row_off``,
        ``col_off``, ``height``, ``width``) defining the region to read.
    expected_crs : str
        CRS the raster is expected to have.  Raises ``ValueError`` if the
        actual CRS does not match.
    expected_resolution_m : float
        Pixel resolution in metres the raster is expected to have.  Raises
        ``ValueError`` if the actual resolution differs by more than 1 %.
    nodata_value : int
        Nodata sentinel to exclude.  Defaults to the CanLaBS v2 int16 sentinel.
    value_policy, manifest_ref, checksum_sha256
        Metadata forwarded to the returned summary.

    Returns
    -------
    CanLaBSWindowSummary
        Immutable summary of the window.

    Raises
    ------
    ImportError
        If ``rasterio`` is not installed.
    ValueError
        If the raster is not single-band, or CRS/resolution do not match
        expectations.
    """
    rio = _import_rasterio()

    with rio.open(raster_path) as src:
        # Single-band check
        if src.count != 1:
            raise ValueError(
                f"Expected single-band raster, got {src.count} bands in {raster_path}"
            )

        # CRS validation
        actual_crs = str(src.crs) if src.crs else ""
        if actual_crs != expected_crs:
            raise ValueError(
                f"CRS mismatch: expected {expected_crs}, got {actual_crs} "
                f"in {raster_path}"
            )

        # Resolution validation (allow 1% tolerance)
        res_x, res_y = src.res
        if abs(res_x - expected_resolution_m) / expected_resolution_m > 0.01:
            raise ValueError(
                f"Resolution X mismatch: expected ~{expected_resolution_m} m, "
                f"got {res_x} m in {raster_path}"
            )
        if abs(res_y - expected_resolution_m) / expected_resolution_m > 0.01:
            raise ValueError(
                f"Resolution Y mismatch: expected ~{expected_resolution_m} m, "
                f"got {res_y} m in {raster_path}"
            )

        # Read the window
        band = src.read(1, window=window)
        values = band.flatten().tolist()

    return summarise_array(
        values,
        nodata_value,
        source_path=str(raster_path),
        source_crs=actual_crs,
        resolution_m=expected_resolution_m,
        value_policy=value_policy,
        manifest_ref=manifest_ref,
        checksum_sha256=checksum_sha256,
        window_shape=(int(band.shape[0]), int(band.shape[1])),
    )