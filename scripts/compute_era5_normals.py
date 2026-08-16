#!/usr/bin/env python3
"""Compute ERA5 1991-2020 climate normals and bioclimatic variables for Canada.

Reads the 9 raw CDS NetCDF chunks (3 variables x 3 decade chunks) from RAW_DIR,
computes per-calendar-month 30-year normals, annual summaries and
forest-ecology bioclimatic variables, clips every raster to the Canada
boundary, and writes Cloud-Optimized GeoTIFFs into OUTPUT_DIR.

Unit semantics
--------------
- t2m / d2m: Kelvin monthly means, converted to degC at load time.
- tp: CDS ``monthly_averaged_reanalysis`` accumulated fields are mean *daily*
  accumulations (m/day, confirmed by inspection: domain mean ~0.0017 m/day).
  A calendar-month normal is therefore ``mean_daily_rate * days_in_month``,
  expressed in mm. MAP is the sum of the 12 monthly totals.
- Climatological month lengths use the 365-day convention:
  [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31].

Quarter variables use the 12 rolling 3-month windows with wrap-around
(e.g. window 12 = Dec-Jan-Feb), selected per pixel on mean t2m.
"""

from __future__ import annotations

import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import rioxarray  # noqa: F401  (activates the .rio accessor)
import xarray as xr

RAW_DIR = Path("/srv/shared-data/gep/jupyterhub16-projects/data/raw/ecmwf/era5/normals")
OUTPUT_DIR = Path("/srv/shared-data/gep/jupyterhub16-projects/data/processed/ecmwf/era5/normals")
CANADA_BOUNDARY = Path("/srv/shared-data/gep/jupyterhub16-projects/data/raw/boundaries/canada_boundary.gpkg")

VARIABLE_PATTERNS = {
    "t2m": "era5_monthly_t2m_mean_*.nc",
    "tp": "era5_monthly_tp_*.nc",
    "d2m": "era5_monthly_d2m_*.nc",
}

EXPECTED_CHUNKS_PER_VARIABLE = 3
EXPECTED_TIMESTEPS = 360  # 30 years x 12 months
EXPECTED_GRID = {"latitude": 173, "longitude": 357}

DAYS_IN_MONTH = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]  # 365-day climatology
GDD_BASE_C = 5.0
FROST_THRESHOLD_C = 0.0
NODATA_VALUE = -9999.0
CRS = "EPSG:4326"

COG_PROFILE = {
    "driver": "GTiff",
    "compress": "deflate",
    "tiled": True,
    "blockxsize": 256,
    "blockysize": 256,
}


# --------------------------------------------------------------------------- #
# Boundary parsing: raw CDS chunks -> trusted monthly normals (12, lat, lon)
# --------------------------------------------------------------------------- #

def load_monthly_normals(variable: str, convert) -> xr.DataArray:
    """Load one variable's decade chunks and return 12 calendar-month normals.

    Validates chunk count, temporal coverage, grid shape and data finiteness
    before any computation, so downstream code can trust the parsed state.
    ``convert`` maps raw units to the pipeline's working units.
    """
    paths = sorted(RAW_DIR.glob(VARIABLE_PATTERNS[variable]))
    if len(paths) != EXPECTED_CHUNKS_PER_VARIABLE:
        raise FileNotFoundError(
            f"{variable}: expected {EXPECTED_CHUNKS_PER_VARIABLE} chunks matching "
            f"'{VARIABLE_PATTERNS[variable]}' in {RAW_DIR}, found {len(paths)}"
        )

    dataset = xr.open_mfdataset(paths, combine="by_coords")
    if variable not in dataset.data_vars:
        dataset.close()
        raise KeyError(f"{variable}: variable not present in {paths[0].name}")

    series = dataset[variable]
    if series.sizes["valid_time"] != EXPECTED_TIMESTEPS:
        dataset.close()
        raise ValueError(
            f"{variable}: expected {EXPECTED_TIMESTEPS} timesteps, "
            f"got {series.sizes['valid_time']}"
        )
    for dim, size in EXPECTED_GRID.items():
        if series.sizes[dim] != size:
            dataset.close()
            raise ValueError(f"{variable}: expected {dim}={size}, got {series.sizes[dim]}")

    values = series.load()
    dataset.close()
    if not bool(np.isfinite(values.values).all()):
        raise ValueError(f"{variable}: non-finite values found in raw data")

    monthly_normals = values.groupby("valid_time.month").mean("valid_time")
    return convert(monthly_normals)


def kelvin_to_celsius(da: xr.DataArray) -> xr.DataArray:
    return da - 273.15


def metres_to_millimetres(da: xr.DataArray) -> xr.DataArray:
    return da * 1000.0


def month_lengths_like(monthly: xr.DataArray) -> xr.DataArray:
    """Climatological days-in-month aligned with a (month, lat, lon) array."""
    return xr.DataArray(
        DAYS_IN_MONTH, dims="month", coords={"month": monthly["month"]}
    )


# --------------------------------------------------------------------------- #
# Derived variables: pure functions over the trusted monthly normals
# --------------------------------------------------------------------------- #

def growing_degree_days(t2m_monthly: xr.DataArray) -> xr.DataArray:
    """GDD base 5: sum over months of max(0, T - 5) * days_in_month."""
    degrees_above_base = xr.where(t2m_monthly > GDD_BASE_C, t2m_monthly - GDD_BASE_C, 0.0)
    return (degrees_above_base * month_lengths_like(t2m_monthly)).sum("month")


def frost_free_period(t2m_monthly: xr.DataArray) -> xr.DataArray:
    """Approximate FFP: frost-free months x mean month length (days)."""
    frost_free_months = (t2m_monthly > FROST_THRESHOLD_C).sum("month")
    mean_month_length = sum(DAYS_IN_MONTH) / len(DAYS_IN_MONTH)
    return frost_free_months * mean_month_length


def saturation_vapor_pressure_kpa(temperature_c: xr.DataArray) -> xr.DataArray:
    """Tetens-form saturation vapour pressure (kPa) for air at temperature_c."""
    return 0.6108 * np.exp(17.27 * temperature_c / (temperature_c + 237.3))


def vapor_pressure_deficit(t2m_monthly: xr.DataArray, d2m_monthly: xr.DataArray) -> xr.DataArray:
    """Annual mean VPD (kPa) from monthly mean air and dewpoint temperatures."""
    monthly_vpd = saturation_vapor_pressure_kpa(t2m_monthly) - saturation_vapor_pressure_kpa(d2m_monthly)
    return xr.where(monthly_vpd > 0, monthly_vpd, 0.0).mean("month")  # VPD is physically >= 0


def precipitation_seasonality(tp_monthly_mm: xr.DataArray) -> xr.DataArray:
    """Coefficient of variation (%) of the 12 monthly precipitation totals."""
    monthly_mean = tp_monthly_mm.mean("month")
    coefficient_of_variation = tp_monthly_mm.std("month", ddof=0) / monthly_mean * 100.0
    return xr.where(monthly_mean > 0, coefficient_of_variation, 0.0)


def rolling_quarter_stacks(monthly: xr.DataArray) -> tuple[xr.DataArray, xr.DataArray]:
    """Stack the 12 wrap-around 3-month windows as (window means, window sums)."""
    windows = [
        monthly.isel(month=[start, (start + 1) % 12, (start + 2) % 12])
        for start in range(12)
    ]
    window_means = xr.concat([window.mean("month") for window in windows], dim="quarter")
    window_sums = xr.concat([window.sum("month") for window in windows], dim="quarter")
    return window_means, window_sums


def quarter_precipitation(
    t2m_monthly: xr.DataArray, tp_monthly_mm: xr.DataArray, pick_warmest: bool
) -> xr.DataArray:
    """Precipitation of the per-pixel warmest (or coldest) 3-month quarter."""
    t2m_window_means, _ = rolling_quarter_stacks(t2m_monthly)
    _, tp_window_sums = rolling_quarter_stacks(tp_monthly_mm)
    if pick_warmest:
        selected_quarter = t2m_window_means.argmax("quarter")
    else:
        selected_quarter = t2m_window_means.argmin("quarter")
    return tp_window_sums.isel(quarter=selected_quarter)


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #

def save_as_cog(da: xr.DataArray, output_path: Path, canada_geometries) -> None:
    """Clip a (latitude, longitude) raster to Canada and write a COG."""
    if not {"latitude", "longitude"} <= set(da.dims):
        raise ValueError(f"expected latitude/longitude dims, got {da.dims}")

    raster = da.rename({"latitude": "y", "longitude": "x"})
    raster = raster.rio.set_spatial_dims(x_dim="x", y_dim="y")
    raster = raster.rio.write_crs(CRS)

    # load_canada_geometries() guarantees geometries, so every raster is clipped.
    raster = raster.rio.clip(canada_geometries, crs=CRS, drop=False, all_touched=False)

    raster = raster.fillna(NODATA_VALUE).astype("float32")
    raster = raster.rio.write_nodata(NODATA_VALUE)
    raster.rio.to_raster(output_path, **COG_PROFILE)
    print(f"  wrote {output_path.name}")


def save_monthly_series(monthly: xr.DataArray, stem: str, canada_geometries) -> None:
    """Write the 12 per-calendar-month rasters of one variable."""
    for month in range(1, 13):
        output_path = OUTPUT_DIR / f"era5_normals_{stem}_monthly_{month:02d}.tif"
        save_as_cog(monthly.sel(month=month), output_path, canada_geometries)


def load_canada_geometries():
    """Return Canada boundary geometries in EPSG:4326. The clip is mandatory:
    a missing boundary file halts the pipeline rather than silently producing
    unclipped rasters."""
    if not CANADA_BOUNDARY.exists():
        raise FileNotFoundError(
            f"Canada boundary not found: {CANADA_BOUNDARY}. "
            "Download canada_boundary.gpkg into data/raw/boundaries/ first; "
            "unclipped rasters are not valid output."
        )
    boundary = gpd.read_file(CANADA_BOUNDARY).to_crs(CRS)
    if boundary.empty:
        raise ValueError(f"{CANADA_BOUNDARY} contains no geometries")
    return list(boundary.geometry)


# --------------------------------------------------------------------------- #

def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    canada_geometries = load_canada_geometries()

    print("Loading and converting monthly normals ...")
    t2m_monthly = load_monthly_normals("t2m", kelvin_to_celsius)          # degC
    d2m_monthly = load_monthly_normals("d2m", kelvin_to_celsius)          # degC
    tp_daily_rate_mm = load_monthly_normals("tp", metres_to_millimetres)  # mm/day
    tp_monthly_mm = tp_daily_rate_mm * month_lengths_like(tp_daily_rate_mm)  # mm/month

    # --- Step 1: monthly normals -------------------------------------------
    print("Writing monthly normals (t2m, tp, d2m) ...")
    save_monthly_series(t2m_monthly, "t2m", canada_geometries)
    save_monthly_series(tp_monthly_mm, "tp", canada_geometries)
    save_monthly_series(d2m_monthly, "d2m", canada_geometries)

    # --- Step 1: annual summaries ------------------------------------------
    print("Writing annual summaries (MAT, MAP, annual dewpoint) ...")
    mean_annual_temperature = t2m_monthly.mean("month")
    mean_annual_precipitation = tp_monthly_mm.sum("month")
    annual_mean_dewpoint = d2m_monthly.mean("month")
    save_as_cog(mean_annual_temperature, OUTPUT_DIR / "era5_normals_mat.tif", canada_geometries)
    save_as_cog(mean_annual_precipitation, OUTPUT_DIR / "era5_normals_map.tif", canada_geometries)
    save_as_cog(annual_mean_dewpoint, OUTPUT_DIR / "era5_normals_d2m_annual.tif", canada_geometries)

    # --- Step 2: bioclimatic variables --------------------------------------
    print("Writing bioclimatic variables ...")
    warmest_month = t2m_monthly.max("month")
    coldest_month = t2m_monthly.min("month")
    bioclims = {
        "era5_normals_gdd5.tif": growing_degree_days(t2m_monthly),
        "era5_normals_ffp.tif": frost_free_period(t2m_monthly),
        "era5_normals_vpd.tif": vapor_pressure_deficit(t2m_monthly, d2m_monthly),
        "era5_normals_twarm.tif": warmest_month,
        "era5_normals_tcold.tif": coldest_month,
        "era5_normals_pseas.tif": precipitation_seasonality(tp_monthly_mm),
        "era5_normals_pwarm.tif": quarter_precipitation(t2m_monthly, tp_monthly_mm, pick_warmest=True),
        "era5_normals_pcold.tif": quarter_precipitation(t2m_monthly, tp_monthly_mm, pick_warmest=False),
        "era5_normals_continentality.tif": warmest_month - coldest_month,
    }
    for filename, raster in bioclims.items():
        save_as_cog(raster, OUTPUT_DIR / filename, canada_geometries)

    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
