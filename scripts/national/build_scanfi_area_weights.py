#!/usr/bin/env python3
"""Build national forest-area and species-composition weights from SCANFI v2.

The 30 m national grid holds about 21.25 billion pixels.  This script aggregates
33 x 33 pixel blocks (990 m x 990 m) using exact pixel counts, so no area is
lost to resampling.  Species layers in SCANFI v2 are absolute crown closure
(percent of a pixel covered by the species), not relative composition.  The
script therefore reports two quantities:

* ``crown_closure_area_ha``: raw species crown-closure area
  (sum(species_cc / 100) * 900 m2), which is not expected to equal forest area.
* ``area_ha``: forest area allocated to a species after normalising the ten
  species crown-closure values within each 990 m block.  This is the weight used
  for supply allocation and it conserves forest area by construction.

Pixels whose species crown closure sums above 100 percent are quarantined into a
``QA_OVER100`` row.  A national over-100 share above ``MAX_OVER100_SHARE`` halts
the run (fail-closed) instead of silently clamping the input.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
from datetime import UTC, datetime
from multiprocessing import Pool
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from pyproj import CRS
from rasterio.features import rasterize
from rasterio.transform import Affine
from rasterio.windows import Window
from scipy import __version__ as scipy_version
from scipy.ndimage import distance_transform_edt

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCANFI_DIR = REPO_ROOT / "data/raw/scanfi/v2"
DEFAULT_RSYC = REPO_ROOT / "data/raw/national/rsyc/RSYC_ecostrat.gpkg"
DEFAULT_PROVINCES = REPO_ROOT / "data/raw/boundaries/canada_adm1_geoboundaries_simplified.geojson"
DEFAULT_OUT = REPO_ROOT / "data/derived/national/assortments/national_area_weights"

PIXEL_M = 30.0
BLOCK_PIXELS = 33
BLOCK_M = PIXEL_M * BLOCK_PIXELS
PIXEL_AREA_HA = (PIXEL_M * PIXEL_M) / 10_000.0
MAX_OVER100_SHARE = 0.001
AREA_TOLERANCE_HA = 1e-3

FOREST_CLASSES = {5, 6, 7}
CLASS_NAMES = {
    1: "bryoid",
    2: "herbs",
    3: "rock",
    4: "shrub",
    5: "treed_broadleaf",
    6: "treed_conifer",
    7: "treed_mixed",
    8: "water",
}
CLASS_LABELS = {
    1: "Bryoid",
    2: "Herbs",
    3: "Rock",
    4: "Shrub",
    5: "Treed broadleaf",
    6: "Treed conifer",
    7: "Treed mixed",
    8: "Water",
    255: "Nodata",
}

SPECIES_LAYERS = [
    ("blackSpruce", "SCANFI_spsCC_blackSpruce_2025_v2_20260119.tif"),
    ("balsamFir", "SCANFI_spsCC_balsamFir_2025_v2_20260119.tif"),
    ("jackPine", "SCANFI_spsCC_jackPine_2025_v2_20260119.tif"),
    ("lodgepolePine", "SCANFI_spsCC_lodgepolePine_2025_v2_20260119.tif"),
    ("ponderosaPine", "SCANFI_spsCC_ponderosaPine_2025_v2_20260119.tif"),
    ("douglasFir", "SCANFI_spsCC_douglasFir_2025_v2_20260119.tif"),
    ("tamarack", "SCANFI_spsCC_tamarack_2025_v2_20260119.tif"),
    ("whiteRedPine", "SCANFI_spsCC_whiteRedPine_2025_v2_20260119.tif"),
    ("broadleaf", "SCANFI_spsCC_broadleaf_2025_v2_20260119.tif"),
    ("otherConiferous", "SCANFI_spsCC_otherConiferous_2025_v2_20260119.tif"),
]
SPECIES_INDEX = {name: i for i, (name, _) in enumerate(SPECIES_LAYERS)}

ATT_LAYERS = [
    ("landcover", "SCANFI_att_nfiLandcover_2025_v2_20260119.tif"),
    ("closure", "SCANFI_att_closure_2025_v2_20260119.tif"),
    ("height", "SCANFI_att_height_2025_v2_20260119.tif"),
    ("biomass", "SCANFI_att_biomass_2025_v2_20260119.tif"),
    ("age", "SCANFI_age_median_2025_v2_20260119.tif"),
]

BOREAL_ECOZONES = {
    "Boreal Cordillera",
    "Boreal Plains",
    "Boreal Shield East",
    "Boreal Shield West",
    "Hudson Plains",
    "Taiga Cordillera",
    "Taiga Plains",
    "Taiga Shield East",
    "Taiga Shield West",
}

PROVINCE_ABBREV = {
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

SPECIES_TARGETS = {
    "blackSpruce": ("PICE.MAR", 1.0, "high"),
    "balsamFir": ("ABIE.BAL", 1.0, "high"),
    "jackPine": ("PINU.BAN", 1.0, "high"),
    "lodgepolePine": ("PINU.CON", 1.0, "high"),
    "ponderosaPine": ("PINU.PON", 1.0, "high"),
    "douglasFir": ("PSEU.MEN", 1.0, "high"),
    "tamarack": ("LARI.LAR", 1.0, "high"),
    "whiteRedPine": ("PINU.STR", 0.5, "low"),
    "broadleaf": ("BROADLEAF", 1.0, "medium"),
    "otherConiferous": ("PICE.GLA", 1.0, "medium"),
}

OUTPUT_COLUMNS = [
    "level",
    "unit_id",
    "ecozone",
    "ecoprovince",
    "ecoprovince_id",
    "ecodistrict",
    "ecodistrict_id",
    "province",
    "scanfi_class",
    "rsyc_species",
    "area_ha",
    "crown_closure_area_ha",
    "forest_area_ha",
    "area_fraction_of_forest",
    "basis",
    "allocation_fraction",
    "confidence",
    "notes",
]

_RASTER_PATHS: dict[str, Path] = {}


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_manifest_rows(path: Path) -> list[dict[str, str]]:
    """Read a manifest whose final notes field may contain unquoted commas."""
    lines = path.read_text().splitlines()
    if not lines:
        return []
    header = [part.strip() for part in lines[0].split(",")]
    rows: list[dict[str, str]] = []
    for line in lines[1:]:
        if not line.strip():
            continue
        parts = line.split(",", len(header) - 1)
        if len(parts) < len(header):
            parts.extend([""] * (len(header) - len(parts)))
        rows.append({key: value.strip() for key, value in zip(header, parts)})
    return rows


def load_known_hashes() -> dict[str, dict[str, Any]]:
    known: dict[str, dict[str, Any]] = {}
    manifests = [
        REPO_ROOT / "data/manifests/scanfi-v2-2025-acquisition.csv",
        REPO_ROOT / "data/manifests/scanfi-v2-2025-species-acquisition.csv",
    ]
    for manifest in manifests:
        if not manifest.exists():
            continue
        for row in read_manifest_rows(manifest):
            filename = row.get("filename", "").strip()
            if not filename:
                continue
            size_text = row.get("size_bytes", "")
            try:
                size_bytes = int(size_text) if size_text else None
            except ValueError:
                size_bytes = None
            known[filename] = {
                "sha256": row.get("sha256") or None,
                "size_bytes": size_bytes,
                "source_url": row.get("url") or None,
                "acquisition_date": row.get("acquisition_date") or None,
            }
    return known


def read_metadata(path: Path, known_hashes: dict[str, dict[str, Any]]) -> dict[str, Any]:
    with rasterio.open(path) as dataset:
        metadata = {
            "path": str(path.relative_to(REPO_ROOT)),
            "driver": dataset.driver,
            "width": dataset.width,
            "height": dataset.height,
            "count": dataset.count,
            "dtype": dataset.dtypes[0],
            "crs_wkt": dataset.crs.to_wkt(),
            "crs_authority": dataset.crs.to_string(),
            "transform": list(dataset.transform),
            "bounds": list(dataset.bounds),
            "nodata": dataset.nodata,
            "block_shape": list(dataset.block_shapes[0]),
            "tiled": bool(dataset.profile.get("tiled")),
            "compress": dataset.profile.get("compress"),
            "overviews": dataset.overviews(1),
            "tags": dataset.tags(),
            "band_tags": dataset.tags(1),
        }
        aux_path = path.with_suffix(path.suffix + ".aux.xml")
        metadata["aux_xml_present"] = aux_path.exists()
        if aux_path.exists():
            text = aux_path.read_text(errors="ignore")
            import re

            stats = {}
            for key in ("STATISTICS_MINIMUM", "STATISTICS_MAXIMUM", "STATISTICS_MEAN", "STATISTICS_STDDEV", "STATISTICS_VALID_PERCENT"):
                match = re.search(rf'<MDI key="{key}">([^<]+)</MDI>', text)
                if match:
                    stats[key] = float(match.group(1))
            metadata["aux_statistics"] = stats
    known = known_hashes.get(path.name, {})
    metadata.update(
        {
            "sha256": known.get("sha256"),
            "size_bytes": known.get("size_bytes", path.stat().st_size),
            "source_url": known.get("source_url"),
            "acquisition_date": known.get("acquisition_date"),
        }
    )
    return metadata


def open_raster(path: Path) -> rasterio.DatasetReader:
    dataset = rasterio.open(path)
    if dataset.count != 1:
        dataset.close()
        raise ValueError(f"expected a single-band raster: {path}")
    return dataset


def _init_worker(scanfi_dir: str, raster_paths: dict[str, str]) -> None:
    global _RASTER_PATHS
    _RASTER_PATHS = {name: Path(path) for name, path in raster_paths.items()}
    for path in _RASTER_PATHS.values():
        if not path.exists():
            raise FileNotFoundError(path)


def _read_band(dataset: rasterio.DatasetReader, window: Window) -> np.ndarray:
    return dataset.read(1, window=window)


def aggregate_strip(block_row_start: int) -> dict[str, Any]:
    """Aggregate one 30-block-row strip into 990 m block sums.

    The worker is deliberately pure: it reads a fixed window, returns arrays,
    and writes no files.  That keeps multiprocessing deterministic.
    """
    landcover_path = _RASTER_PATHS["landcover"]
    with open_raster(landcover_path) as dataset:
        width = dataset.width
        height = dataset.height
        row_offset = block_row_start * BLOCK_PIXELS * 30
        if row_offset >= height:
            raise ValueError(f"block row {block_row_start} lies outside raster height {height}")
        strip_rows = min(BLOCK_PIXELS * 30, height - row_offset)
        window = Window(0, row_offset, width, strip_rows)
        landcover = _read_band(dataset, window)

    block_rows = math.ceil(strip_rows / BLOCK_PIXELS)
    block_cols = math.ceil(width / BLOCK_PIXELS)
    padded_rows = block_rows * BLOCK_PIXELS
    padded_cols = block_cols * BLOCK_PIXELS

    landcover_padded = np.full((padded_rows, padded_cols), 255, dtype=np.uint8)
    landcover_padded[:strip_rows, :width] = landcover
    forest = (landcover_padded >= 5) & (landcover_padded <= 7)
    valid = landcover_padded != 255

    block_shape = (block_rows, BLOCK_PIXELS, block_cols, BLOCK_PIXELS)
    landcover_counts = np.zeros((block_rows, block_cols, 9), dtype=np.int32)
    for class_code in range(1, 9):
        landcover_counts[:, :, class_code] = (
            (landcover_padded == class_code).reshape(block_shape).sum(axis=(1, 3), dtype=np.int32)
        )
    forest_pixels = forest.reshape(block_shape).sum(axis=(1, 3), dtype=np.int32)
    valid_pixels = valid.reshape(block_shape).sum(axis=(1, 3), dtype=np.int32)

    species_sums = np.zeros((block_rows, block_cols, len(SPECIES_LAYERS)), dtype=np.int32)
    species_cc_sum = np.zeros((padded_rows, padded_cols), dtype=np.uint16)
    invalid_species_pixels = 0
    for species_name, filename in SPECIES_LAYERS:
        with open_raster(_RASTER_PATHS[species_name]) as dataset:
            if dataset.width != width:
                raise ValueError(f"{filename}: width {dataset.width} != landcover width {width}")
            species = _read_band(dataset, window)
        missing = species == 255
        above_100 = (species > 100) & ~missing
        invalid_species_pixels += int(above_100.sum())
        species = np.where(missing | above_100, 0, species).astype(np.uint8)
        padded = np.zeros((padded_rows, padded_cols), dtype=np.uint8)
        padded[:strip_rows, :width] = species
        padded[~forest] = 0
        species_cc_sum += padded
        species_sums[:, :, SPECIES_INDEX[species_name]] = (
            padded.reshape(block_shape).sum(axis=(1, 3), dtype=np.int32)
        )

    over_100_pixels = ((species_cc_sum > 100) & forest).reshape(block_shape).sum(axis=(1, 3), dtype=np.int32)
    zero_species_pixels = ((species_cc_sum == 0) & forest).reshape(block_shape).sum(axis=(1, 3), dtype=np.int32)

    return {
        "block_row_start": block_row_start,
        "block_rows": block_rows,
        "landcover_counts": landcover_counts,
        "forest_pixels": forest_pixels,
        "valid_pixels": valid_pixels,
        "species_sums": species_sums,
        "over_100_pixels": over_100_pixels,
        "zero_species_pixels": zero_species_pixels,
        "invalid_species_pixels": invalid_species_pixels,
    }


def rasterize_units(
    frame: gpd.GeoDataFrame,
    id_column: str,
    transform: Affine,
    shape: tuple[int, int],
    crs: CRS,
) -> np.ndarray:
    local = frame.to_crs(crs)
    shapes = [
        (geometry, int(value))
        for geometry, value in zip(local.geometry, local[id_column])
        if geometry is not None and not geometry.is_empty and int(value) > 0
    ]
    if not shapes:
        raise ValueError(f"no geometries to rasterize for {id_column}")
    return rasterize(
        shapes,
        out_shape=shape,
        transform=transform,
        fill=0,
        dtype="int32",
        all_touched=False,
    )


def fill_missing_ids(ids: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Fill zero blocks with the nearest unit id.

    A 990 m block centred on a lake, river, or coastline can contain forest
    pixels while its centre falls outside every ecodistrict/province polygon.
    Nearest-neighbour fill removes that coastline gap; the number of filled
    forest pixels is retained for QA.
    """
    missing = ids == 0
    if not missing.any():
        return ids, missing
    _, (rows, cols) = distance_transform_edt(missing, return_indices=True)
    filled = ids.copy()
    filled[missing] = ids[rows[missing], cols[missing]]
    if (filled == 0).any():
        raise ValueError("nearest-unit fill left zero block ids")
    return filled, missing


def load_ecodistricts(path: Path, crs: CRS) -> tuple[gpd.GeoDataFrame, dict[int, dict[str, Any]]]:
    districts = gpd.read_file(path, layer="ecodistricts").to_crs(crs)
    districts["ecodistrict_id"] = districts["ecodistrict_id"].astype(int)
    districts["ecoprovince_id"] = districts["ecoprovince_id"].astype(float)
    attributes = {}
    for _, row in districts.iterrows():
        attributes[int(row["ecodistrict_id"])] = {
            "ecodistrict": row["ecodistrict"],
            "ecodistrict_id": int(row["ecodistrict_id"]),
            "ecoprovince": row["ecoprovince"],
            "ecoprovince_id": row["ecoprovince_id"],
            "ecozone": row["ecozone"],
            "ecoregion": row["ecoregion"],
        }
    return districts, attributes


def load_provinces(path: Path, crs: CRS) -> tuple[gpd.GeoDataFrame, dict[int, str]]:
    provinces = gpd.read_file(path).to_crs(crs)
    provinces = provinces[provinces["shapeName"].isin(PROVINCE_ABBREV)].copy()
    if len(provinces) != 13:
        raise ValueError(f"expected 13 Canadian provinces/territories, found {len(provinces)}")
    provinces["province"] = provinces["shapeName"].map(PROVINCE_ABBREV)
    provinces["province_id"] = np.arange(1, len(provinces) + 1)
    mapping = dict(zip(provinces["province_id"].astype(int), provinces["province"]))
    return provinces, mapping


def species_mapping_for_ecozone(species_name: str, ecozone: str) -> dict[str, Any]:
    target, fraction, confidence = SPECIES_TARGETS[species_name]
    basis = f"scanfi_{species_name}_crown_closure_normalised"
    notes = ""
    if species_name == "whiteRedPine":
        basis = "scanfi_whiteRedPine_composite_split_PINU.STR_PINU.RES"
        notes = "SCANFI combines white pine and red pine; area is split 50/50 between PINU.STR and PINU.RES."
    if species_name == "broadleaf":
        basis = "scanfi_broadleaf_aggregate_to_BROADLEAF"
        notes = "SCANFI broadleaf is an aggregate class; it cannot resolve ACER/BETU/POPU species."
    if species_name == "otherConiferous":
        if ecozone in BOREAL_ECOZONES:
            target = "PICE.GLA"
            basis = "scanfi_otherConiferous_boreal_proxy_to_PICE.GLA"
            notes = "In boreal ecozones, other conifer is used as a white-spruce proxy; it also contains larch and other conifers."
        else:
            target = "CONIFEROUS"
            basis = "scanfi_otherConiferous_generic_to_CONIFEROUS"
            notes = "Outside boreal ecozones, other conifer is kept as a generic CONIFEROUS target."
        confidence = "medium"
    return {
        "rsyc_species": target,
        "allocation_fraction": fraction,
        "basis": basis,
        "confidence": confidence,
        "notes": notes,
        "scanfi_class": species_name,
    }


def build_crosswalk() -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for code, name in CLASS_NAMES.items():
        if code not in FOREST_CLASSES:
            rows.append(
                {
                    "scanfi_class_code": code,
                    "scanfi_class": name,
                    "is_forest": False,
                    "rsyc_species": "",
                    "allocation_fraction": 0.0,
                    "mapping_basis": "non_forest_class",
                    "confidence": "high",
                    "notes": "Excluded from the forest mask.",
                }
            )
    mapping = [
        ("blackSpruce", "PICE.MAR", 1.0, "scanfi_blackSpruce_crown_closure_normalised", "high", "Exact species layer."),
        ("balsamFir", "ABIE.BAL", 1.0, "scanfi_balsamFir_crown_closure_normalised", "high", "Exact species layer."),
        ("jackPine", "PINU.BAN", 1.0, "scanfi_jackPine_crown_closure_normalised", "high", "Exact species layer."),
        ("lodgepolePine", "PINU.CON", 1.0, "scanfi_lodgepolePine_crown_closure_normalised", "high", "Exact species layer."),
        ("ponderosaPine", "PINU.PON", 1.0, "scanfi_ponderosaPine_crown_closure_normalised", "high", "Exact species layer."),
        ("douglasFir", "PSEU.MEN", 1.0, "scanfi_douglasFir_crown_closure_normalised", "high", "Exact species layer."),
        ("tamarack", "LARI.LAR", 1.0, "scanfi_tamarack_crown_closure_normalised", "high", "Exact species layer."),
        ("whiteRedPine", "PINU.STR", 0.5, "scanfi_whiteRedPine_composite_split_PINU.STR_PINU.RES", "low", "Composite white+red pine split 50/50; PINU.RES receives the other half."),
        ("whiteRedPine", "PINU.RES", 0.5, "scanfi_whiteRedPine_composite_split_PINU.STR_PINU.RES", "low", "Composite white+red pine split 50/50; PINU.STR receives the other half."),
        ("broadleaf", "BROADLEAF", 1.0, "scanfi_broadleaf_aggregate_to_BROADLEAF", "medium", "Aggregate broadleaf class; no species-level resolution."),
        ("otherConiferous", "PICE.GLA", 1.0, "scanfi_otherConiferous_boreal_proxy_to_PICE.GLA", "medium", "Boreal ecozones only; white-spruce proxy."),
        ("otherConiferous", "CONIFEROUS", 1.0, "scanfi_otherConiferous_generic_to_CONIFEROUS", "medium", "Non-boreal ecozones; generic conifer target."),
    ]
    for layer, species, fraction, basis, confidence, notes in mapping:
        rows.append(
            {
                "scanfi_class_code": "",
                "scanfi_class": layer,
                "is_forest": True,
                "rsyc_species": species,
                "allocation_fraction": fraction,
                "mapping_basis": basis,
                "confidence": confidence,
                "notes": notes,
            }
        )
    frame = pd.DataFrame(rows)
    frame["scanfi_class_code"] = pd.to_numeric(frame["scanfi_class_code"], errors="coerce").astype("Int64")
    return frame


def build_species_rows(
    unit: dict[str, Any],
    species_areas: np.ndarray,
    crown_areas: np.ndarray,
    unspecified_area: float,
    over100_area: float,
    forest_area: float,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for species_name, _ in SPECIES_LAYERS:
        index = SPECIES_INDEX[species_name]
        area = float(species_areas[index])
        if area <= 0:
            continue
        mapping = species_mapping_for_ecozone(species_name, unit["ecozone"])
        targets = [mapping]
        if species_name == "whiteRedPine":
            targets = [
                {**mapping, "rsyc_species": "PINU.STR", "allocation_fraction": 0.5},
                {**mapping, "rsyc_species": "PINU.RES", "allocation_fraction": 0.5},
            ]
        for target in targets:
            rows.append(
                {
                    **unit,
                    "scanfi_class": target["scanfi_class"],
                    "rsyc_species": target["rsyc_species"],
                    "area_ha": area * target["allocation_fraction"],
                    "crown_closure_area_ha": float(crown_areas[index]) * target["allocation_fraction"],
                    "forest_area_ha": forest_area,
                    "area_fraction_of_forest": (area * target["allocation_fraction"]) / forest_area if forest_area else 0.0,
                    "basis": target["basis"],
                    "allocation_fraction": target["allocation_fraction"],
                    "confidence": target["confidence"],
                    "notes": target["notes"],
                }
            )
    if unspecified_area > AREA_TOLERANCE_HA:
        rows.append(
            {
                **unit,
                "scanfi_class": "treed_unattributed",
                "rsyc_species": "UNSPECIFIED",
                "area_ha": unspecified_area,
                "crown_closure_area_ha": 0.0,
                "forest_area_ha": forest_area,
                "area_fraction_of_forest": unspecified_area / forest_area if forest_area else 0.0,
                "basis": "scanfi_forest_pixel_with_zero_species_crown_closure",
                "allocation_fraction": 1.0,
                "confidence": "low",
                "notes": "Treed land-cover pixel with zero species crown closure; cannot assign a species.",
            }
        )
    if over100_area > AREA_TOLERANCE_HA:
        rows.append(
            {
                **unit,
                "scanfi_class": "treed_unattributed",
                "rsyc_species": "QA_OVER100",
                "area_ha": over100_area,
                "crown_closure_area_ha": 0.0,
                "forest_area_ha": forest_area,
                "area_fraction_of_forest": over100_area / forest_area if forest_area else 0.0,
                "basis": "scanfi_species_cc_sum_gt_100_quarantined",
                "allocation_fraction": 1.0,
                "confidence": "low",
                "notes": "Species crown closure sums above 100 percent; excluded from allocation.",
            }
        )
    return rows


def aggregate_national(
    landcover_counts: np.ndarray,
    forest_pixels: np.ndarray,
    valid_pixels: np.ndarray,
    species_sums: np.ndarray,
    over_100_pixels: np.ndarray,
    zero_species_pixels: np.ndarray,
    district_ids: np.ndarray,
    province_ids: np.ndarray,
    nearest_fill_pixels: np.ndarray,
    district_attributes: dict[int, dict[str, Any]],
    province_names: dict[int, str],
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Aggregate block arrays to district x province units and national QA."""
    block_rows, block_cols = forest_pixels.shape
    flat_key = district_ids.astype(np.int64).ravel() * 100 + province_ids.astype(np.int64).ravel()
    max_key = int(flat_key.max()) + 1
    n_keys = max(max_key, 1)

    forest_ha = forest_pixels.astype(np.float64) * PIXEL_AREA_HA
    forest_area_by_key = np.bincount(flat_key, weights=forest_ha.ravel(), minlength=n_keys)
    forest_px_by_key = np.bincount(flat_key, weights=forest_pixels.ravel(), minlength=n_keys)
    over100_area_by_key = np.bincount(
        flat_key, weights=(over_100_pixels.astype(np.float64) * PIXEL_AREA_HA).ravel(), minlength=n_keys
    )
    zero_area_by_key = np.bincount(
        flat_key, weights=(zero_species_pixels.astype(np.float64) * PIXEL_AREA_HA).ravel(), minlength=n_keys
    )
    nearest_fill_area_by_key = np.bincount(
        flat_key, weights=(nearest_fill_pixels.astype(np.float64) * PIXEL_AREA_HA).ravel(), minlength=n_keys
    )
    class_area_by_key = np.zeros((n_keys, 9), dtype=np.float64)
    for class_code in range(1, 9):
        class_area_by_key[:, class_code] = np.bincount(
            flat_key,
            weights=(landcover_counts[:, :, class_code].astype(np.float64) * PIXEL_AREA_HA).ravel(),
            minlength=n_keys,
        )

    total_species = species_sums.sum(axis=2, dtype=np.int64)
    denominator = total_species.astype(np.float64)
    # Only pixels with a positive, non-quarantined species sum receive species
    # area.  Zero-species and over-100 pixels keep their forest area in the
    # UNSPECIFIED and QA_OVER100 residuals respectively.
    positive_forest_pixels = forest_pixels - zero_species_pixels - over_100_pixels
    positive_share = np.divide(
        positive_forest_pixels.astype(np.float64),
        forest_pixels,
        out=np.zeros_like(forest_ha, dtype=np.float64),
        where=forest_pixels > 0,
    )
    allocated = np.zeros((block_rows, block_cols, len(SPECIES_LAYERS)), dtype=np.float32)
    positive = denominator > 0
    allocated[positive] = (
        species_sums[positive].astype(np.float32)
        / denominator[positive, None].astype(np.float32)
        * forest_ha[positive, None].astype(np.float32)
        * positive_share[positive, None].astype(np.float32)
    )
    species_area_by_key = np.zeros((n_keys, len(SPECIES_LAYERS)), dtype=np.float64)
    crown_area_by_key = np.zeros((n_keys, len(SPECIES_LAYERS)), dtype=np.float64)
    for index in range(len(SPECIES_LAYERS)):
        species_area_by_key[:, index] = np.bincount(
            flat_key, weights=allocated[:, :, index].astype(np.float64).ravel(), minlength=n_keys
        )
        crown_area_by_key[:, index] = np.bincount(
            flat_key,
            weights=(species_sums[:, :, index].astype(np.float64) * PIXEL_AREA_HA / 100.0).ravel(),
            minlength=n_keys,
        )

    unit_rows: list[dict[str, Any]] = []
    species_rows: list[dict[str, Any]] = []
    key_errors: list[float] = []
    for key in np.nonzero(forest_area_by_key > 0)[0]:
        district_id = int(key // 100)
        province_id = int(key % 100)
        attributes = district_attributes.get(district_id, {
            "ecodistrict": "UNASSIGNED",
            "ecodistrict_id": 0,
            "ecoprovince": "UNASSIGNED",
            "ecoprovince_id": 0.0,
            "ecozone": "UNASSIGNED",
            "ecoregion": "UNASSIGNED",
        })
        province = province_names.get(province_id, "UNASSIGNED")
        forest_area = float(forest_area_by_key[key])
        allocated_sum = float(species_area_by_key[key].sum())
        residual = forest_area - allocated_sum - float(zero_area_by_key[key]) - float(over100_area_by_key[key])
        key_errors.append(abs(residual) / forest_area if forest_area else 0.0)
        unit = {
            "level": "ecodistrict",
            "unit_id": f"{attributes['ecoprovince_id']}:{district_id}:{province}",
            "ecozone": attributes["ecozone"],
            "ecoprovince": attributes["ecoprovince"],
            "ecoprovince_id": attributes["ecoprovince_id"],
            "ecodistrict": attributes["ecodistrict"],
            "ecodistrict_id": district_id,
            "province": province,
        }
        species_rows.extend(
            build_species_rows(
                unit,
                species_area_by_key[key],
                crown_area_by_key[key],
                float(zero_area_by_key[key]),
                float(over100_area_by_key[key]),
                forest_area,
            )
        )
        unit_rows.append(
            {
                **unit,
                "forest_area_ha": forest_area,
                "forest_pixels": float(forest_px_by_key[key]),
                "nearest_fill_forest_area_ha": float(nearest_fill_area_by_key[key]),
            }
        )

    unit_frame = pd.DataFrame(unit_rows)
    species_frame = pd.DataFrame(species_rows)
    qa = {
        "max_relative_area_residual": float(max(key_errors, default=0.0)),
        "forest_area_ha": float(forest_area_by_key.sum()),
        "forest_pixels": int(forest_pixels.astype(np.int64).sum()),
        "valid_pixels": int(valid_pixels.astype(np.int64).sum()),
        "over_100_pixels": int(over_100_pixels.astype(np.int64).sum()),
        "over_100_area_ha": float(over100_area_by_key.sum()),
        "zero_species_pixels": int(zero_species_pixels.astype(np.int64).sum()),
        "zero_species_area_ha": float(zero_area_by_key.sum()),
        "nearest_fill_forest_area_ha": float(nearest_fill_area_by_key.sum()),
        "unassigned_forest_area_ha": float(forest_area_by_key[0]) if n_keys > 0 else 0.0,

        "class_area_ha": {
            CLASS_NAMES[code]: float(class_area_by_key[:, code].sum()) for code in range(1, 9)
        },
    }
    return unit_frame, species_frame, qa


def add_aggregate_levels(
    unit_frame: pd.DataFrame,
    species_frame: pd.DataFrame,
    all_ecoprovinces: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    ecoprovince_forest = (
        unit_frame.groupby(["ecoprovince_id", "ecoprovince", "ecozone"], dropna=False)["forest_area_ha"]
        .sum()
        .reset_index()
    )
    ecoprovince_species = (
        species_frame.groupby(
            ["ecoprovince_id", "ecoprovince", "ecozone", "scanfi_class", "rsyc_species", "basis", "allocation_fraction", "confidence", "notes"],
            dropna=False,
        )["area_ha"]
        .sum()
        .reset_index()
    )
    ecoprovince_crown = (
        species_frame.groupby(
            ["ecoprovince_id", "ecoprovince", "ecozone", "scanfi_class", "rsyc_species"],
            dropna=False,
        )["crown_closure_area_ha"]
        .sum()
        .reset_index()
    )
    ecoprovince_species = ecoprovince_species.merge(
        ecoprovince_crown,
        on=["ecoprovince_id", "ecoprovince", "ecozone", "scanfi_class", "rsyc_species"],
        how="left",
    )
    ecoprovince_species = ecoprovince_species.merge(ecoprovince_forest, on=["ecoprovince_id", "ecoprovince", "ecozone"], how="left")
    ecoprovince_species["level"] = "ecoprovince"
    ecoprovince_species["unit_id"] = ecoprovince_species["ecoprovince_id"].astype(str)
    ecoprovince_species["ecodistrict"] = "ALL"
    ecoprovince_species["ecodistrict_id"] = ""
    ecoprovince_species["province"] = "ALL"
    ecoprovince_species["area_fraction_of_forest"] = np.where(
        ecoprovince_species["forest_area_ha"] > 0,
        ecoprovince_species["area_ha"] / ecoprovince_species["forest_area_ha"],
        0.0,
    )

    known = set(ecoprovince_species["ecoprovince"].dropna())
    missing = all_ecoprovinces[~all_ecoprovinces["ecoprovince"].isin(known)]
    zero_rows = []
    for _, row in missing.iterrows():
        zero_rows.append(
            {
                "level": "ecoprovince",
                "unit_id": str(row["ecoprovince_id"]),
                "ecozone": row["ecozone"],
                "ecoprovince": row["ecoprovince"],
                "ecoprovince_id": row["ecoprovince_id"],
                "ecodistrict": "ALL",
                "ecodistrict_id": "",
                "province": "ALL",
                "scanfi_class": "none",
                "rsyc_species": "NO_FOREST",
                "area_ha": 0.0,
                "crown_closure_area_ha": 0.0,
                "forest_area_ha": 0.0,
                "area_fraction_of_forest": 0.0,
                "basis": "no_forest_in_scanfi_treed_classes",
                "allocation_fraction": 0.0,
                "confidence": "high",
                "notes": "No SCANFI treed land-cover pixels in this ecoprovince.",
            }
        )
    if zero_rows:
        ecoprovince_species = pd.concat([ecoprovince_species, pd.DataFrame(zero_rows)], ignore_index=True)

    province_forest = unit_frame.groupby("province", dropna=False)["forest_area_ha"].sum().reset_index()
    province_species = (
        species_frame.groupby(
            ["province", "scanfi_class", "rsyc_species", "basis", "allocation_fraction", "confidence", "notes"],
            dropna=False,
        )["area_ha"]
        .sum()
        .reset_index()
    )
    province_crown = (
        species_frame.groupby(["province", "scanfi_class", "rsyc_species"], dropna=False)["crown_closure_area_ha"]
        .sum()
        .reset_index()
    )
    province_species = province_species.merge(
        province_crown, on=["province", "scanfi_class", "rsyc_species"], how="left"
    )
    province_species = province_species.merge(province_forest, on="province", how="left")
    province_species["level"] = "province"
    province_species["unit_id"] = province_species["province"]
    province_species["ecozone"] = "ALL"
    province_species["ecoprovince"] = "ALL"
    province_species["ecoprovince_id"] = ""
    province_species["ecodistrict"] = "ALL"
    province_species["ecodistrict_id"] = ""
    province_species["area_fraction_of_forest"] = np.where(
        province_species["forest_area_ha"] > 0,
        province_species["area_ha"] / province_species["forest_area_ha"],
        0.0,
    )

    return (
        ecoprovince_species[OUTPUT_COLUMNS].sort_values(["ecoprovince_id", "scanfi_class", "rsyc_species"]),
        species_frame[OUTPUT_COLUMNS].sort_values(["ecoprovince_id", "ecodistrict_id", "province", "scanfi_class", "rsyc_species"]),
        province_species[OUTPUT_COLUMNS].sort_values(["province", "scanfi_class", "rsyc_species"]),
    )


def build_summary(
    args: argparse.Namespace,
    raster_metadata: list[dict[str, Any]],
    qa: dict[str, Any],
    ecoprovince_frame: pd.DataFrame,
    province_frame: pd.DataFrame,
    ecodistrict_frame: pd.DataFrame,
    runtime_seconds: float,
    n_blocks: tuple[int, int],
) -> dict[str, Any]:
    def area_table(frame: pd.DataFrame, group: str) -> dict[str, float]:
        # forest_area_ha is repeated on every species row; summing allocated
        # area is the only non-duplicating aggregation.
        return {
            str(key): float(value)
            for key, value in frame.groupby(group)["area_ha"].sum().sort_values(ascending=False).items()
        }

    species_totals = (
        ecoprovince_frame.groupby("rsyc_species")[["area_ha", "crown_closure_area_ha"]].sum().reset_index()
    )
    class_totals = (
        ecoprovince_frame.groupby("scanfi_class")[["area_ha"]].sum().reset_index()
    )
    ecoprovince_units = ecoprovince_frame.drop_duplicates("ecoprovince")
    province_units = province_frame.drop_duplicates("province")
    forest_area_by_province = {
        str(row["province"]): float(row["forest_area_ha"])
        for _, row in province_units.sort_values("forest_area_ha", ascending=False).iterrows()
    }
    forest_area_by_ecozone = {
        str(key): float(value)
        for key, value in ecoprovince_units.groupby("ecozone")["forest_area_ha"]
        .sum()
        .sort_values(ascending=False)
        .items()
    }
    forest_area_by_ecoprovince = {
        str(row["ecoprovince"]): float(row["forest_area_ha"])
        for _, row in ecoprovince_units.sort_values("forest_area_ha", ascending=False).iterrows()
    }
    nfi_path = REPO_ROOT / "data/raw/nfi/standard_reports/t1/ca_t4_for_area_en.csv"
    validation: dict[str, Any] = {}
    if nfi_path.exists():
        nfi = pd.read_csv(nfi_path, skiprows=1)
        nfi_forest_ha = float(nfi.loc[0, "Forest land"]) * 1000.0
        scanfi_forest_ha = float(ecoprovince_units["forest_area_ha"].sum())
        validation = {
            "nfi_national_forest_land_ha": nfi_forest_ha,
            "scanfi_forest_area_ha": scanfi_forest_ha,
            "scanfi_to_nfi_ratio": scanfi_forest_ha / nfi_forest_ha,
            "note": "NFI Table 4.0 is a national forest-land benchmark, not a province-level control. SCANFI treed classes include treed land outside the NFI forest-land definition and are expected to differ.",
        }
    return {
        "schema_version": "national_area_weights_v1",
        "generated_utc": utc_now(),
        "command": " ".join(sys.argv),
        "runtime_seconds": round(runtime_seconds, 3),
        "software": {
            "python": sys.version.split()[0],
            "rasterio": rasterio.__version__,
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "geopandas": gpd.__version__,
            "scipy": scipy_version,
        },
        "scanfi": {
            "product": "SCANFI v2 2025 (20260119)",
            "citation": "Guindon L., Correia D.L.P., Manka F., Smiley B. 2026. SCANFI v2. NRCan CFS. https://doi.org/10.23687/07653869-f303-46c2-a04e-9ab479b73cbf",
            "license": "Open Government Licence - Canada",
            "aggregation": {
                "pixel_size_m": PIXEL_M,
                "block_pixels": BLOCK_PIXELS,
                "block_size_m": BLOCK_M,
                "method": "exact 33x33 pixel block sums at 30 m; block centroid used for ecodistrict/province attribution",
                "pixel_area_ha": PIXEL_AREA_HA,
                "block_count": [int(n_blocks[0]), int(n_blocks[1])],
            },
            "class_legend": {str(code): CLASS_LABELS[code] for code in sorted(CLASS_LABELS)},
            "forest_classes_used": sorted(FOREST_CLASSES),
            "rasters": raster_metadata,
        },
        "method": {
            "forest_mask": "NFI landcover classes 5 (treed broadleaf), 6 (treed conifer), 7 (treed mixed).",
            "species_layers": "SCANFI v2 species layers are absolute crown closure (%), not relative composition.",
            "crown_closure_area": "sum(species_cc / 100) * pixel_area within the forest mask. Not forced to equal forest area.",
            "allocated_area": "Within each 990 m block, species area shares are normalised across the ten SCANFI species layers and multiplied by forest area. Blocks with zero species crown closure are assigned to UNSPECIFIED. Pixels with species sum > 100% are quarantined as QA_OVER100.",
            "species_resolution": "SCANFI resolves ten species/classes only. Broadleaf, white/red pine, and other conifer are composite or proxy targets and are flagged.",
            "province_attribution": "Block-centroid rasterisation of geoBoundaries CAN ADM1 (simplified, derived from Statistics Canada boundaries). Boundary blocks are assigned to one province; coastal simplification may shift a small number of blocks.",
            "unit_gap_fill": "Blocks whose centroid falls outside every ecodistrict or province polygon (mainly lakes, rivers, and coastlines) are assigned to the nearest unit. Filled forest area is reported in ecodistrict_attribution_qa.csv.",
        },
        "totals": {
            "forest_area_ha": float(ecoprovince_units["forest_area_ha"].sum()),
            "allocated_area_ha": float(ecoprovince_frame["area_ha"].sum()),
            "by_province": forest_area_by_province,
            "by_ecozone": forest_area_by_ecozone,
            "by_ecoprovince": forest_area_by_ecoprovince,
            "by_province_allocated_area_ha": area_table(province_frame, "province"),
            "by_species": {
                str(row["rsyc_species"]): {
                    "area_ha": float(row["area_ha"]),
                    "crown_closure_area_ha": float(row["crown_closure_area_ha"]),
                }
                for _, row in species_totals.iterrows()
            },
            "by_scanfi_class": {str(row["scanfi_class"]): float(row["area_ha"]) for _, row in class_totals.iterrows()},
        },
        "qa": qa,
        "validation": validation,
        "outputs": {
            "national_area_weights.csv": "ecoprovince-level canonical weights (all 36 ecoprovinces)",
            "national_area_weights_ecodistrict.csv": "ecodistrict x province detail",
            "national_area_weights_province.csv": "province-level totals",
            "scanfi_class_crosswalk.csv": "SCANFI class to RSYC species mapping",
            "zero_area_units.csv": "ecodistrict units with zero SCANFI forest area",
            "ecodistrict_attribution_qa.csv": "nearest-unit fill area for coastline/lake blocks",
        },
        "caveats": [
            "SCANFI is a prediction surface, not a census or a provincial inventory.",
            "Species area is composition-weighted forest area, not a species-specific inventory volume.",
            "White/red pine is split 50/50; broadleaf and other conifer are composite/proxy targets.",
            "Province boundaries are simplified 1:10m-class polygons; use for attribution, not legal boundaries.",
            "No MGM yield curves are joined in this product.",
        ],
    }


def write_report(path: Path, summary: dict[str, Any], crosswalk: pd.DataFrame, province_frame: pd.DataFrame, ecoprovince_frame: pd.DataFrame) -> None:
    province_forest = (
        province_frame.drop_duplicates("province")[["province", "forest_area_ha"]]
        .sort_values("forest_area_ha", ascending=False)
    )
    species = (
        ecoprovince_frame.groupby("rsyc_species")["area_ha"].sum().sort_values(ascending=False).reset_index()
    )
    eco = (
        ecoprovince_frame.drop_duplicates("ecoprovince")[["ecoprovince", "ecozone", "forest_area_ha"]]
        .sort_values("forest_area_ha", ascending=False)
    )
    lines = [
        "# National SCANFI v2 Area Weights",
        "",
        f"Generated: {summary['generated_utc']}  ",
        f"Runtime: {summary['runtime_seconds']} s  ",
        "Product: SCANFI v2 2025 (20260119), 30 m, aggregated to 990 m blocks.",
        "",
        "## Method",
        "",
        "Forest area is the count of NFI land-cover classes 5 (treed broadleaf), 6 (treed conifer), and 7 (treed mixed) multiplied by 0.09 ha per 30 m pixel. Species area is allocated by normalising the ten SCANFI v2 species crown-closure layers within each 990 m block, then multiplying by block forest area. The raw crown-closure area is retained separately because SCANFI v2 species layers are absolute crown closure, not relative composition.",
        "",
        "## National totals",
        "",
        f"- Forest area: **{summary['totals']['forest_area_ha']:,.0f} ha**",
        f"- Allocated species area: **{summary['totals']['allocated_area_ha']:,.0f} ha**",
        f"- Maximum relative area residual: {summary['qa']['max_relative_area_residual']:.3e}",
        f"- Species-sum > 100% pixels: {summary['qa']['over_100_pixels']:,} ({summary['qa']['over_100_area_ha']:,.1f} ha quarantined)",
        f"- Zero-species forest pixels: {summary['qa']['zero_species_pixels']:,} ({summary['qa']['zero_species_area_ha']:,.1f} ha)",
        f"- Coastline/lake gap-filled forest area: {summary['qa']['nearest_fill_forest_area_ha']:,.1f} ha",
        f"- Unassigned forest area after nearest-unit fill: {summary['qa']['unassigned_forest_area_ha']:,.1f} ha",
        "",
        (
            f"NFI Table 4.0 national forest-land benchmark: {summary['validation']['nfi_national_forest_land_ha']:,.0f} ha; "
            f"SCANFI treed area is {summary['validation']['scanfi_to_nfi_ratio']:.3f}x the benchmark."
            if summary.get("validation")
            else "NFI Table 4.0 benchmark unavailable."
        ),
        "",
        "## Forest area by province/territory",
        "",
        "| Province | Forest area (ha) |",
        "|---|---:|",
    ]
    for _, row in province_forest.iterrows():
        lines.append(f"| {row['province']} | {row['forest_area_ha']:,.0f} |")
    lines += [
        "",
        "## Allocated area by RSYC target",
        "",
        "| RSYC species | Area (ha) |",
        "|---|---:|",
    ]
    for _, row in species.iterrows():
        lines.append(f"| {row['rsyc_species']} | {row['area_ha']:,.0f} |")
    lines += [
        "",
        "## Forest area by ecoprovince",
        "",
        "| Ecoprovince | Ecozone | Forest area (ha) |",
        "|---|---|---:|",
    ]
    for _, row in eco.iterrows():
        lines.append(f"| {row['ecoprovince']} | {row['ecozone']} | {row['forest_area_ha']:,.0f} |")
    lines += [
        "",
        "## Crosswalk",
        "",
        "```",
        crosswalk.to_string(index=False),
        "```",
        "",
        "## Caveats",
        "",
        "- SCANFI is a prediction surface, not a census or provincial inventory.",
        "- Species area is composition-weighted forest area, not species-specific volume.",
        "- White/red pine is split 50/50 between PINU.STR and PINU.RES.",
        "- Broadleaf is an aggregate class; other conifer is a white-spruce proxy in boreal ecozones and generic CONIFEROUS elsewhere.",
        "- Province attribution uses simplified geoBoundaries ADM1 polygons; treat as attribution, not legal boundaries.",
        "- No MGM yield curves are joined in this product.",
        "",
    ]
    path.write_text("\n".join(lines))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scanfi-dir", type=Path, default=DEFAULT_SCANFI_DIR)
    parser.add_argument("--rsyc", type=Path, default=DEFAULT_RSYC)
    parser.add_argument("--provinces", type=Path, default=DEFAULT_PROVINCES)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--strip-limit", type=int, default=0, help="Debug only: process the first N 990 m strips.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    start = time.time()
    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    landcover_path = args.scanfi_dir / dict(ATT_LAYERS)["landcover"]
    if not landcover_path.exists():
        raise FileNotFoundError(landcover_path)
    with rasterio.open(landcover_path) as dataset:
        width = dataset.width
        height = dataset.height
        crs = dataset.crs
        transform = dataset.transform
    if transform.a != PIXEL_M or transform.e != -PIXEL_M:
        raise ValueError(f"expected 30 m north-up grid, found transform {transform}")

    block_cols = math.ceil(width / BLOCK_PIXELS)
    block_rows = math.ceil(height / BLOCK_PIXELS)
    block_transform = Affine(BLOCK_M, 0, transform.c, 0, -BLOCK_M, transform.f)

    districts, district_attributes = load_ecodistricts(args.rsyc, crs)
    provinces, province_names = load_provinces(args.provinces, crs)
    district_ids_raw = rasterize_units(districts, "ecodistrict_id", block_transform, (block_rows, block_cols), crs)
    province_ids_raw = rasterize_units(provinces, "province_id", block_transform, (block_rows, block_cols), crs)
    district_ids, district_missing = fill_missing_ids(district_ids_raw)
    province_ids, _province_missing = fill_missing_ids(province_ids_raw)

    all_ecoprovinces = gpd.read_file(args.rsyc, layer="ecoprovinces")
    all_ecoprovinces = all_ecoprovinces[["ecozone", "ecoprovince", "ecoprovince_id"]].drop_duplicates()
    all_ecoprovinces["ecoprovince_id"] = all_ecoprovinces["ecoprovince_id"].astype(float)

    known_hashes = load_known_hashes()
    raster_metadata = [
        read_metadata(args.scanfi_dir / filename, known_hashes)
        for _, filename in ATT_LAYERS + SPECIES_LAYERS
    ]

    raster_paths = {name: str(args.scanfi_dir / filename) for name, filename in SPECIES_LAYERS}
    raster_paths["landcover"] = str(landcover_path)

    strip_count = math.ceil(block_rows / 30)
    if args.strip_limit:
        strip_count = min(strip_count, args.strip_limit)
    block_row_starts = list(range(strip_count))

    landcover_counts = np.zeros((block_rows, block_cols, 9), dtype=np.int32)
    forest_pixels = np.zeros((block_rows, block_cols), dtype=np.int32)
    valid_pixels = np.zeros((block_rows, block_cols), dtype=np.int32)
    species_sums = np.zeros((block_rows, block_cols, len(SPECIES_LAYERS)), dtype=np.int32)
    over_100_pixels = np.zeros((block_rows, block_cols), dtype=np.int32)
    zero_species_pixels = np.zeros((block_rows, block_cols), dtype=np.int32)
    invalid_species_pixels = 0

    print(f"aggregating {strip_count} strips ({block_rows} x {block_cols} blocks) with {args.workers} workers", flush=True)
    with Pool(processes=args.workers, initializer=_init_worker, initargs=(str(args.scanfi_dir), raster_paths)) as pool:
        for completed, result in enumerate(pool.imap_unordered(aggregate_strip, block_row_starts, chunksize=1), start=1):
            start_row = result["block_row_start"] * 30
            end_row = start_row + result["block_rows"]
            landcover_counts[start_row:end_row] = result["landcover_counts"]
            forest_pixels[start_row:end_row] = result["forest_pixels"]
            valid_pixels[start_row:end_row] = result["valid_pixels"]
            species_sums[start_row:end_row] = result["species_sums"]
            over_100_pixels[start_row:end_row] = result["over_100_pixels"]
            zero_species_pixels[start_row:end_row] = result["zero_species_pixels"]
            invalid_species_pixels += result["invalid_species_pixels"]
            if completed % 10 == 0 or completed == strip_count:
                print(f"  {completed}/{strip_count} strips", flush=True)

    if args.strip_limit:
        forest_pixels[strip_count * 30 :] = 0
        landcover_counts[strip_count * 30 :] = 0
        species_sums[strip_count * 30 :] = 0
        over_100_pixels[strip_count * 30 :] = 0
        zero_species_pixels[strip_count * 30 :] = 0

    nearest_fill_pixels = (forest_pixels * district_missing).astype(np.int32)
    unit_frame, species_frame, qa = aggregate_national(
        landcover_counts,
        forest_pixels,
        valid_pixels,
        species_sums,
        over_100_pixels,
        zero_species_pixels,
        district_ids,
        province_ids,
        nearest_fill_pixels,
        district_attributes,
        province_names,
    )
    qa["invalid_species_pixels"] = int(invalid_species_pixels)
    ecoprovince_frame, ecodistrict_frame, province_frame = add_aggregate_levels(
        unit_frame, species_frame, all_ecoprovinces
    )

    if qa["forest_pixels"] == 0:
        raise RuntimeError("no forest pixels found; check land-cover class mapping and raster alignment")
    over_100_share = qa["over_100_pixels"] / max(qa["forest_pixels"], 1)
    if over_100_share > MAX_OVER100_SHARE:
        raise RuntimeError(
            f"species crown closure sums above 100% on {over_100_share:.4%} of forest pixels "
            f"(threshold {MAX_OVER100_SHARE:.2%}); refusing to allocate"
        )
    if qa["max_relative_area_residual"] > 1e-5:
        raise RuntimeError(f"area conservation residual {qa['max_relative_area_residual']:.3e} exceeds 1e-5")

    observed_districts = set(unit_frame["ecodistrict_id"].astype(int))
    zero_rows = []
    for district_id, attributes in district_attributes.items():
        if district_id in observed_districts:
            continue
        zero_rows.append(
            {
                "ecozone": attributes["ecozone"],
                "ecoprovince": attributes["ecoprovince"],
                "ecoprovince_id": attributes["ecoprovince_id"],
                "ecodistrict": attributes["ecodistrict"],
                "ecodistrict_id": district_id,
                "province": "ALL",
                "forest_area_ha": 0.0,
                "reason": "no_forest_in_scanfi_treed_classes",
            }
        )
    zero_units = pd.DataFrame(
        zero_rows,
        columns=[
            "ecozone",
            "ecoprovince",
            "ecoprovince_id",
            "ecodistrict",
            "ecodistrict_id",
            "province",
            "forest_area_ha",
            "reason",
        ],
    )

    crosswalk = build_crosswalk()
    ecoprovince_frame.to_csv(out_dir / "national_area_weights.csv", index=False)
    ecodistrict_frame.to_csv(out_dir / "national_area_weights_ecodistrict.csv", index=False)
    province_frame.to_csv(out_dir / "national_area_weights_province.csv", index=False)
    crosswalk.to_csv(out_dir / "scanfi_class_crosswalk.csv", index=False)
    zero_units.to_csv(out_dir / "zero_area_units.csv", index=False)
    attribution_qa = unit_frame[
        [
            "ecozone",
            "ecoprovince",
            "ecoprovince_id",
            "ecodistrict",
            "ecodistrict_id",
            "province",
            "forest_area_ha",
            "nearest_fill_forest_area_ha",
        ]
    ].copy()
    attribution_qa["nearest_fill_fraction"] = np.where(
        attribution_qa["forest_area_ha"] > 0,
        attribution_qa["nearest_fill_forest_area_ha"] / attribution_qa["forest_area_ha"],
        0.0,
    )
    attribution_qa.to_csv(out_dir / "ecodistrict_attribution_qa.csv", index=False)

    summary = build_summary(
        args, raster_metadata, qa, ecoprovince_frame, province_frame, ecodistrict_frame, time.time() - start, (block_rows, block_cols)
    )
    (out_dir / "national_area_summary.json").write_text(json.dumps(summary, indent=2))
    write_report(out_dir / "NATIONAL_AREA_REPORT.md", summary, crosswalk, province_frame, ecoprovince_frame)

    output_files = [
        "national_area_weights.csv",
        "national_area_weights_ecodistrict.csv",
        "national_area_weights_province.csv",
        "scanfi_class_crosswalk.csv",
        "zero_area_units.csv",
        "ecodistrict_attribution_qa.csv",
        "national_area_summary.json",
        "NATIONAL_AREA_REPORT.md",
    ]
    manifest = {
        "schema_version": "national_area_weights_manifest_v1",
        "generated_utc": utc_now(),
        "command": " ".join(sys.argv),
        "runtime_seconds": round(time.time() - start, 3),
        "inputs": raster_metadata,
        "outputs": [
            {
                "path": name,
                "size_bytes": (out_dir / name).stat().st_size,
                "sha256": file_sha256(out_dir / name),
            }
            for name in output_files
        ],
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

    print(json.dumps({k: summary["totals"][k] for k in ("forest_area_ha", "allocated_area_ha")}, indent=2), flush=True)
    print(f"wrote {out_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
