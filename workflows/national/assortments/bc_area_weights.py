"""All-BC forest/THLB area weights for the national ecoprovince batch.

The national representative-stand batch carries a 1 ha placeholder per cell.
This module replaces that placeholder for British Columbia with an explicit
VRI 2025 forest-land-base area table, split by RSYC species and attributed to
FADM TSA and RSYC ecoprovince/ecoregion.

Design rules
------------
* The VRI filter is explicit and is written into every output: Crown
  management land base, vegetated, treed.
* Species percentages are normalised per polygon.  A polygon whose six
  percentages sum to zero is excluded and reported, never silently dropped.
* BC species codes are translated through the CASFRI crosswalk.  A code with
  no RSYC species target becomes ``UNMAPPED_BC`` and its area is reported.
* THLB is sampled per polygon label point from the provincial 1 ha raster.
  ``thlb_area_ha = area_ha * thlb_percent / 100``.  Invalid raster values are
  treated as zero THLB and counted.
* Spatial labels come from a 500 m rasterisation of the authoritative
  boundaries.  This avoids GEOS predicates on the few FADM TSA features whose
  ring organisation is pathological, and keeps a 5 million polygon run inside
  the operating timebox.  The approximation is documented in the report.

Run from the repository root::

    .venv/bin/python -m workflows.national.assortments.bc_area_weights
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyogrio
import rasterio
from rasterio.features import rasterize
from rasterio.transform import from_origin

__all__ = [
    "ARTIFACT_VERSION",
    "AreaConfig",
    "AreaWeightError",
    "aggregate_area_table",
    "build_batch_join",
    "build_species_crosswalk",
    "map_bc_species",
    "normalise_species_percentages",
    "run",
    "thlb_area_from_percent",
]

ARTIFACT_VERSION = "0.1.0"
SOURCE = "BC VRI 2025 R1 VEG_COMP_LYR_R1_POLY"
VINTAGE = "2025"
CRS = "EPSG:3005"
AREA_BASIS = "vri2025_forest_thlb"
LABEL_RASTER_RESOLUTION_M = 500.0
DEFAULT_TIMEBOX_SECONDS = 780.0

UNMAPPED_SPECIES = "UNMAPPED_BC"
NO_TSA = "NO_TSA"
NO_ECOPROVINCE = "NO_ECOPROVINCE"
NO_ECOREGION = "NO_ECOREGION"

#: The 32 RSYC scientific species (the batch also carries three model groups,
#: which are intentionally not treated as species here).
RSYC_SPECIES = frozenset(
    {
        "ABIE.AMA",
        "ABIE.BAL",
        "ABIE.LAS",
        "ACER.MAC",
        "ACER.RUB",
        "ACER.SAH",
        "ALNU.RUB",
        "BETU.ALL",
        "BETU.PAP",
        "FRAX.NIG",
        "LARI.LAR",
        "LARI.OCC",
        "PICE.ABI",
        "PICE.ENG",
        "PICE.GLA",
        "PICE.MAR",
        "PICE.RUB",
        "PICE.SIT",
        "PINU.BAN",
        "PINU.CON",
        "PINU.PON",
        "PINU.RES",
        "PINU.STR",
        "POPU.BAL",
        "POPU.GRA",
        "POPU.TRE",
        "PSEU.MEN",
        "QUER.RUB",
        "THUJ.OCC",
        "THUJ.PLI",
        "TSUG.HET",
        "TSUG.MER",
    }
)

MODEL_GROUP_TARGETS = frozenset({"CONIFEROUS", "BROADLEAF"})

#: Ecoprovinces treated as coastal for the spruce alias rule.  The rule is a
#: documented BC commercial-code convention, not a species-level identification:
#: coastal spruce is predominantly Sitka spruce, interior/boreal spruce is
#: predominantly hybrid white spruce (PICE.GLA complex).
COASTAL_ECOPROVINCES = frozenset(
    {
        "Georgia Depression",
        "Northern Coastal Mountains",
        "Southern Coastal Mountains",
    }
)

#: Documented alias table for BC commercial codes whose CASFRI target has no
#: RSYC species.  ``coast``/``interior`` targets are selected by ecoprovince;
#: ``default`` is used everywhere.  Confidence is a judgement about the
#: species-level substitution, not about the area accounting.
ALIAS_RULES: dict[str, dict[str, str]] = {
    "SX": {
        "coast": "PICE.SIT",
        "interior": "PICE.GLA",
        "confidence": "medium",
        "rule": "SX_hybrid_spruce_coast_SIT_interior_GLA",
        "source": "bc_alias_table_v1",
        "note": (
            "Hybrid spruce (Picea x) has no RSYC target; coastal populations are "
            "assigned to Sitka spruce and interior/boreal populations to the "
            "white-spruce complex. This preserves the commercial spruce stream."
        ),
    },
    "S": {
        "coast": "PICE.SIT",
        "interior": "PICE.GLA",
        "confidence": "medium",
        "rule": "S_spruce_coast_SIT_interior_GLA",
        "source": "bc_alias_table_v1",
        "note": (
            "Unspecified spruce (Picea spp.) is split by ecoprovince using the "
            "same coastal/interior convention as SX."
        ),
    },
    "YC": {
        "default": "CONIFEROUS",
        "confidence": "low",
        "rule": "YC_yellow_cedar_no_rsyc_target_group_conifer",
        "source": "bc_alias_table_v1",
        "note": (
            "Yellow-cedar (Callitropsis/Chamaecyparis nootkatensis) has no RSYC "
            "species target. No other RSYC Cupressaceae is defensible at species "
            "level, so the area is carried in the coniferous model group and flagged."
        ),
    },
    "H": {
        "default": "TSUG.HET",
        "confidence": "high",
        "rule": "H_hemlock_to_western_hemlock",
        "source": "bc_alias_table_v1",
        "note": (
            "BC code H is hemlock; western hemlock (TSUG.HET) is the dominant "
            "commercial hemlock in the province. Mountain hemlock is not separated "
            "in the source code, so this is a species-level assumption."
        ),
    },
    "B": {
        "default": "ABIE.LAS",
        "confidence": "medium",
        "rule": "B_balsam_to_subalpine_fir_ABIE_LAS",
        "source": "bc_alias_table_v1",
        "note": (
            "BC balsam is subalpine fir (Abies lasiocarpa), not eastern balsam fir "
            "(ABIE.BAL). The brief's ABIE.BAL suggestion was rejected because "
            "ABIE.BAL is not a BC commercial species; CASFRI maps B to ABIE_SPP."
        ),
    },
    "ACT": {
        "default": "POPU.BAL",
        "confidence": "medium",
        "rule": "ACT_black_cottonwood_to_balsam_poplar_Tacamahaca",
        "source": "bc_alias_table_v1",
        "note": (
            "Black cottonwood (Populus trichocarpa) is treated as balsam poplar "
            "(P. balsamifera subsp. trichocarpa), the closest RSYC species in "
            "section Tacamahaca. POPU.TRE (trembling aspen) is a poorer match."
        ),
    },
    "PA": {
        "default": "CONIFEROUS",
        "confidence": "low",
        "rule": "PA_whitebark_pine_no_rsyc_target_group_conifer",
        "source": "bc_alias_table_v1",
        "note": (
            "The brief labelled PA as paper birch, but the CASFRI crosswalk and BC "
            "VRI code set identify PA as whitebark pine (Pinus albicaulis). Paper "
            "birch is EP/BETU.PAP and is already an exact crosswalk. Whitebark pine "
            "has no RSYC target, so the area is carried in the coniferous group."
        ),
    },
}

CONIFER_GENERA = frozenset(
    {"ABIE", "CHAM", "JUNI", "LARI", "PICE", "PINU", "PSEU", "TAXU", "THUJ", "TSUG"}
)
BROADLEAF_GENERA = frozenset(
    {"ACER", "ALNU", "ARBU", "BETU", "CORN", "FRAX", "MALU", "POPU", "PRUN", "QUER", "RHAM", "SALI"}
)

#: Ecoprovinces named by the task as crossing the BC boundary.  The area table
#: is BC-only, so batch totals for these cells are BC partials, not full
#: ecoprovince supply.
CROSS_BORDER_ECOZONES = frozenset({"Boreal Cordillera", "Boreal Plains", "Taiga Plains"})

VRI_LAYER = "VEG_COMP_LYR_R1_POLY"
VRI_COLUMNS = (
    "FEATURE_ID",
    "ORG_UNIT_CODE",
    "FOR_MGMT_LAND_BASE_IND",
    "BCLCS_LEVEL_1",
    "BCLCS_LEVEL_2",
    "BCLCS_LEVEL_4",
    "BEC_ZONE_CODE",
    "POLYGON_AREA",
    "LABEL_CENTRE_X",
    "LABEL_CENTRE_Y",
    "SPECIES_CD_1",
    "SPECIES_PCT_1",
    "SPECIES_CD_2",
    "SPECIES_PCT_2",
    "SPECIES_CD_3",
    "SPECIES_PCT_3",
    "SPECIES_CD_4",
    "SPECIES_PCT_4",
    "SPECIES_CD_5",
    "SPECIES_PCT_5",
    "SPECIES_CD_6",
    "SPECIES_PCT_6",
)
SPECIES_CODE_COLUMNS = tuple(f"SPECIES_CD_{index}" for index in range(1, 7))
SPECIES_PCT_COLUMNS = tuple(f"SPECIES_PCT_{index}" for index in range(1, 7))

#: Crown management land base, vegetated and treed.  The BCLCS level-2 treed
#: flag is the reliable discriminator in this release; LAND_COVER_CLASS_CD_1 is
#: null for a large share of treed polygons and is not used as the primary key.
VRI_FILTER_SQL = (
    "FOR_MGMT_LAND_BASE_IND = 'Y' AND BCLCS_LEVEL_1 = 'V' AND BCLCS_LEVEL_2 = 'T'"
)
VRI_FILTER_TEXT = (
    "FOR_MGMT_LAND_BASE_IND = 'Y' (Crown management land base) AND "
    "BCLCS_LEVEL_1 = 'V' (vegetated) AND BCLCS_LEVEL_2 = 'T' (treed); "
    "polygon area = POLYGON_AREA (ha), validated against FEATURE_AREA_SQM/10000"
)

TABLE_COLUMNS = (
    "tsa_number",
    "tsa_name",
    "ecoprovince",
    "ecoregion",
    "rsyc_species",
    "area_ha",
    "thlb_area_ha",
    "area_basis",
    "source",
    "vintage",
    "crs",
    "n_polygons",
)

JOIN_COLUMNS = (
    "cell_key",
    "ecozone",
    "ecoprovince",
    "species",
    "product",
    "sort_code",
    "stream",
    "record_type",
    "density_basis",
    "density_odt_m3",
    "taper_species",
    "volume_m3ha",
    "volume_m3",
    "residue_m3ha",
    "residue_m3",
    "residue_odt_ha",
    "residue_odt",
    "area_ha",
    "thlb_area_ha",
    "area_basis",
    "area_status",
    "evidence_tier",
    "batch_status",
    "cross_border_ecoprovince",
)


class AreaWeightError(RuntimeError):
    """Raised when the BC area table cannot be built without ambiguity."""


@dataclass(frozen=True)
class AreaConfig:
    """Filesystem configuration for one area-weight pass."""

    repo_root: Path
    data_root: Path
    vri_path: Path
    tsa_path: Path
    ecostrat_path: Path
    thlb_path: Path
    crosswalk_path: Path
    cells_dir: Path
    out_dir: Path
    timebox_seconds: float = DEFAULT_TIMEBOX_SECONDS
    label_resolution_m: float = LABEL_RASTER_RESOLUTION_M
    verbose: bool = False

    @classmethod
    def default(cls, repo_root: Path | None = None) -> AreaConfig:
        root = (repo_root or Path.cwd()).resolve()
        data = root / "data"
        external = Path(
            "/srv/shared-data/gep/ws4/femic/external/femic-public-data/data"
        )
        return cls(
            repo_root=root,
            data_root=data,
            vri_path=external
            / "bc"
            / "vri"
            / "2025"
            / "VEG_COMP_LYR_R1_POLY_2025.gdb",
            tsa_path=external / "bc" / "tsa" / "FADM_TSA.gdb",
            ecostrat_path=data / "raw" / "national" / "rsyc" / "RSYC_ecostrat.gpkg",
            thlb_path=external / "misc.thlb.tif",
            crosswalk_path=data
            / "raw"
            / "casfri_v5"
            / "translation_tables"
            / "species_code_mapping.csv",
            cells_dir=data
            / "derived"
            / "national"
            / "assortments"
            / "national_batch"
            / "cells",
            out_dir=data
            / "derived"
            / "national"
            / "assortments"
            / "bc_area_weights",
        )


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested)
# ---------------------------------------------------------------------------


def normalise_species_percentages(percentages: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Return per-polygon species weights and a validity mask.

    ``percentages`` is ``(n, k)``.  Non-finite or negative values are treated
    as zero.  A row whose positive percentages sum to zero is invalid; the
    caller must exclude it and report the lost area.
    """
    values = np.asarray(percentages, dtype="float64")
    if values.ndim != 2:
        raise AreaWeightError("species percentages must be a two-dimensional array")
    clean = np.where(np.isfinite(values) & (values > 0), values, 0.0)
    totals = clean.sum(axis=1)
    valid = totals > 0
    weights = np.zeros_like(clean)
    weights[valid] = clean[valid] / totals[valid, None]
    return weights, valid


def thlb_area_from_percent(area_ha: np.ndarray, thlb_percent: np.ndarray) -> np.ndarray:
    """Convert 0-100 THLB attribution to hectares.

    Missing, negative, and out-of-range raster values contribute zero THLB.
    The caller reports how much area was affected rather than guessing.
    """
    area = np.asarray(area_ha, dtype="float64")
    percent = np.asarray(thlb_percent, dtype="float64")
    if area.shape != percent.shape:
        raise AreaWeightError("area_ha and thlb_percent must have the same shape")
    valid = np.isfinite(percent) & (percent >= 0.0) & (percent <= 100.0)
    fraction = np.where(valid, percent / 100.0, 0.0)
    return area * np.clip(fraction, 0.0, 1.0)


def _canonical_to_rsyc(canonical: object) -> str | None:
    text = str(canonical).strip().upper()
    if not text:
        return None
    parts = text.split("_")
    if len(parts) < 2:
        return None
    candidate = f"{parts[0]}.{parts[1]}"
    return candidate if candidate in RSYC_SPECIES else None


def build_species_crosswalk(crosswalk: pd.DataFrame) -> tuple[dict[str, str], dict[str, str]]:
    """Build BC-code -> RSYC-species and BC-code -> canonical maps."""
    required = {"bc_species_codes", "casfri_species_codes"}
    missing = required.difference(crosswalk.columns)
    if missing:
        raise AreaWeightError(f"species crosswalk is missing columns: {sorted(missing)}")
    usable = crosswalk.dropna(subset=["bc_species_codes", "casfri_species_codes"])
    code_to_canonical: dict[str, str] = {}
    code_to_rsyc: dict[str, str] = {}
    for code, canonical in zip(
        usable["bc_species_codes"].astype(str).str.strip(),
        usable["casfri_species_codes"].astype(str).str.strip(),
    ):
        if not code:
            continue
        code_to_canonical[code] = canonical
        target = _canonical_to_rsyc(canonical)
        if target is not None:
            code_to_rsyc[code] = target
    return code_to_rsyc, code_to_canonical


def map_bc_species(code: object, code_to_rsyc: Mapping[str, str]) -> tuple[str, str]:
    """Return ``(rsyc_species, mapping_basis)`` for one BC species code."""
    key = "" if code is None else str(code).strip().upper()
    target = code_to_rsyc.get(key)
    if target is None:
        return UNMAPPED_SPECIES, "unmapped"
    return target, "exact_crosswalk"


def resolve_bc_species(
    code: object,
    ecoprovince: object,
    code_to_rsyc: Mapping[str, str],
) -> tuple[str, str, str, str]:
    """Resolve one BC code to a reporting species, alias source, rule, confidence.

    Exact CASFRI crosswalk targets win over aliases.  A documented alias is used
    only when the crosswalk has no RSYC species target.  Unknown codes remain
    ``UNMAPPED_BC``; they are never silently folded into a group.
    """
    key = "" if code is None else str(code).strip().upper()
    exact = code_to_rsyc.get(key)
    if exact is not None:
        return exact, "bc_casfri_crosswalk", "exact_crosswalk", "high"

    rule = ALIAS_RULES.get(key)
    if rule is None:
        return UNMAPPED_SPECIES, "none", "unmapped_no_alias", "none"

    province = "" if ecoprovince is None else str(ecoprovince).strip()
    if "default" in rule:
        target = rule["default"]
    elif province in COASTAL_ECOPROVINCES:
        target = rule["coast"]
    else:
        target = rule["interior"]
    return target, rule["source"], rule["rule"], rule["confidence"]


def alias_table_frame() -> pd.DataFrame:
    """Return the documented alias table in long form for the output artifact."""
    rows: list[dict[str, str]] = []
    for code, rule in sorted(ALIAS_RULES.items()):
        if "default" in rule:
            rows.append(
                {
                    "bc_code": code,
                    "ecoprovince_scope": "all",
                    "target": rule["default"],
                    "rule": rule["rule"],
                    "source": rule["source"],
                    "confidence": rule["confidence"],
                    "note": rule["note"],
                }
            )
            continue
        for scope, target_key in (("coastal", "coast"), ("interior_boreal", "interior")):
            rows.append(
                {
                    "bc_code": code,
                    "ecoprovince_scope": scope,
                    "target": rule[target_key],
                    "rule": rule["rule"],
                    "source": rule["source"],
                    "confidence": rule["confidence"],
                    "note": rule["note"],
                }
            )
    return pd.DataFrame(rows)


def _species_group(species: str) -> str:
    if species == UNMAPPED_SPECIES:
        return "unmapped"
    if species in MODEL_GROUP_TARGETS:
        return species.lower()
    genus = species.split(".", 1)[0]
    if genus in CONIFER_GENERA:
        return "coniferous"
    if genus in BROADLEAF_GENERA:
        return "broadleaf"
    return "unknown"


def aggregate_area_table(rows: pd.DataFrame) -> pd.DataFrame:
    """Aggregate per-polygon species allocations to the reporting grain."""
    required = {
        "tsa_number",
        "tsa_name",
        "ecoprovince",
        "ecoregion",
        "rsyc_species",
        "area_ha",
        "thlb_area_ha",
    }
    missing = required.difference(rows.columns)
    if missing:
        raise AreaWeightError(f"area rows are missing columns: {sorted(missing)}")
    grouped = (
        rows.groupby(
            ["tsa_number", "tsa_name", "ecoprovince", "ecoregion", "rsyc_species"],
            dropna=False,
            as_index=False,
        )
        .agg(
            area_ha=("area_ha", "sum"),
            thlb_area_ha=("thlb_area_ha", "sum"),
            n_polygons=("area_ha", "size"),
        )
        .sort_values(["ecoprovince", "ecoregion", "tsa_number", "rsyc_species"])
        .reset_index(drop=True)
    )
    return grouped


# ---------------------------------------------------------------------------
# Reading and spatial labelling
# ---------------------------------------------------------------------------


def _read_vri(config: AreaConfig) -> pd.DataFrame:
    if not config.vri_path.exists():
        raise AreaWeightError(f"VRI polygon layer not found: {config.vri_path}")
    started = time.monotonic()
    frame = pyogrio.read_dataframe(
        config.vri_path,
        layer=VRI_LAYER,
        columns=list(VRI_COLUMNS),
        where=VRI_FILTER_SQL,
        read_geometry=False,
    )
    if frame.empty:
        raise AreaWeightError("VRI filter returned zero polygons; refusing to write an empty table")
    if config.verbose:
        print(f"  VRI rows={len(frame):,} read in {time.monotonic() - started:.1f}s", flush=True)
    return frame


def _read_current_tsa(config: AreaConfig) -> pd.DataFrame:
    """Read current FADM TSA features.

    ``OGR_ORGANIZE_POLYGONS=SKIP`` is set by :func:`run` before this call.  The
    geometries are used only for rasterisation, which is robust to the few
    pathological ring sets that make GEOS predicates extremely slow.
    """
    if not config.tsa_path.exists():
        raise AreaWeightError(f"FADM TSA layer not found: {config.tsa_path}")
    frame = pyogrio.read_dataframe(
        config.tsa_path,
        layer="WHSE_ADMIN_BOUNDARIES_FADM_TSA",
        columns=["TSA_NUMBER", "TSA_NUMBER_DESCRIPTION", "RETIREMENT_DATE"],
        where="RETIREMENT_DATE IS NULL",
    )
    if frame.empty:
        raise AreaWeightError("FADM TSA current-feature query returned zero rows")
    frame = frame[frame.geometry.notna() & ~frame.geometry.is_empty].copy()
    frame["TSA_NUMBER"] = frame["TSA_NUMBER"].astype(str).str.strip()
    frame["tsa_name"] = (
        frame["TSA_NUMBER_DESCRIPTION"].astype(str).str.replace(r"\s+TSA$", "", regex=True)
    )
    return frame


def _read_ecoregions(config: AreaConfig) -> pd.DataFrame:
    if not config.ecostrat_path.exists():
        raise AreaWeightError(f"RSYC ecostrat layer not found: {config.ecostrat_path}")
    frame = pyogrio.read_dataframe(
        config.ecostrat_path,
        layer="ecoregions",
        columns=["ecozone", "ecoprovince", "ecoregion"],
    ).to_crs(CRS)
    if frame.empty:
        raise AreaWeightError("RSYC ecoregion layer returned zero rows")
    return frame


def _grid_from_bounds(bounds: rasterio.coords.BoundingBox, resolution: float) -> tuple[Any, int, int]:
    width = math.ceil((bounds.right - bounds.left) / resolution)
    height = math.ceil((bounds.top - bounds.bottom) / resolution)
    return from_origin(bounds.left, bounds.top, resolution, resolution), width, height


def _burn_labels(
    polygons: Iterable[Any],
    values: Iterable[int],
    transform: Any,
    shape: tuple[int, int],
) -> np.ndarray:
    shapes = [
        (geometry, int(value))
        for geometry, value in zip(polygons, values)
        if geometry is not None and not geometry.is_empty
    ]
    if not shapes:
        raise AreaWeightError("no non-empty polygon labels to rasterise")
    return rasterize(
        shapes,
        out_shape=shape,
        transform=transform,
        fill=0,
        dtype="int16",
        all_touched=False,
    )


def _sample_grid(
    grid: np.ndarray,
    transform: Any,
    x: np.ndarray,
    y: np.ndarray,
) -> np.ndarray:
    inverse = ~transform
    columns = np.floor(inverse.a * x + inverse.b * y + inverse.c).astype("int64")
    rows = np.floor(inverse.d * x + inverse.e * y + inverse.f).astype("int64")
    height, width = grid.shape
    inside = (columns >= 0) & (columns < width) & (rows >= 0) & (rows < height)
    sampled = np.zeros(len(x), dtype=grid.dtype)
    sampled[inside] = grid[rows[inside], columns[inside]]
    return sampled


def _sample_thlb(
    config: AreaConfig, x: np.ndarray, y: np.ndarray
) -> tuple[np.ndarray, dict[str, float]]:
    if not config.thlb_path.exists():
        raise AreaWeightError(f"THLB raster not found: {config.thlb_path}")
    with rasterio.open(config.thlb_path) as dataset:
        values = dataset.read(1)
        nodata = dataset.nodata
        sampled = _sample_grid(values, dataset.transform, x, y).astype("float64")
        valid_pixels = (values >= 0) & (values <= 100)
        raster_stats = {
            "valid_pixel_fraction": float(valid_pixels.mean()),
            "valid_pixel_mean_percent": float(values[valid_pixels].mean()),
            "raster_thlb_area_ha": float(values[valid_pixels].sum() / 100.0),
        }
        if nodata is not None:
            sampled = np.where(sampled == float(nodata), np.nan, sampled)
        invalid = ~np.isfinite(sampled) | (sampled < 0.0) | (sampled > 100.0)
    return sampled, {**raster_stats, "sampled_nodata_count": int(invalid.sum())}


# ---------------------------------------------------------------------------
# Area table
# ---------------------------------------------------------------------------


def _build_area_table(
    frame: pd.DataFrame,
    code_to_rsyc: Mapping[str, str],
    tsa_number: np.ndarray,
    tsa_name: np.ndarray,
    ecoprovince: np.ndarray,
    ecoregion: np.ndarray,
    thlb_percent: np.ndarray,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    area_ha = frame["POLYGON_AREA"].to_numpy(dtype="float64")
    percentages = frame[list(SPECIES_PCT_COLUMNS)].to_numpy(dtype="float64")
    weights, valid_species = normalise_species_percentages(percentages)
    valid = valid_species & np.isfinite(area_ha) & (area_ha > 0)
    dropped_no_species = int((~valid_species).sum())
    dropped_no_species_area = float(area_ha[~valid_species].sum())

    thlb_area = thlb_area_from_percent(area_ha, thlb_percent)
    valid_thlb = np.isfinite(thlb_percent) & (thlb_percent >= 0) & (thlb_percent <= 100)
    invalid_thlb = int((~valid_thlb).sum())
    invalid_thlb_area = float(area_ha[~valid_thlb].sum())

    tsa_values = np.asarray(tsa_number, dtype=object)
    tsa_name_values = np.asarray(tsa_name, dtype=object)
    ecoprov_values = np.asarray(ecoprovince, dtype=object)
    ecoreg_values = np.asarray(ecoregion, dtype=object)

    tsa_labels, tsa_index = np.unique(tsa_values, return_inverse=True)
    ecoprov_labels, ecoprov_index = np.unique(ecoprov_values, return_inverse=True)
    ecoreg_labels, ecoreg_index = np.unique(ecoreg_values, return_inverse=True)

    species_order = np.array(
        sorted(RSYC_SPECIES | MODEL_GROUP_TARGETS) + [UNMAPPED_SPECIES], dtype=object
    )
    species_to_index = {str(species): index for index, species in enumerate(species_order)}
    unmapped_index = species_to_index[UNMAPPED_SPECIES]

    n_tsa = len(tsa_labels)
    n_ecoprov = len(ecoprov_labels)
    n_ecoreg = len(ecoreg_labels)
    n_species = len(species_order)
    group_count = n_tsa * n_ecoprov * n_ecoreg * n_species

    area_sum = np.zeros(group_count, dtype="float64")
    thlb_sum = np.zeros(group_count, dtype="float64")
    polygon_count = np.zeros(group_count, dtype="int64")
    exact_group = np.zeros(group_count, dtype=bool)
    alias_rules_by_group: dict[int, set[int]] = {}
    unmapped_by_code: dict[str, float] = {}
    alias_area_by_id = np.zeros(0, dtype="float64")
    alias_id_meta: list[dict[str, str]] = []

    for slot, (code_column, percent_column) in enumerate(
        zip(SPECIES_CODE_COLUMNS, SPECIES_PCT_COLUMNS)
    ):
        codes = np.array(
            ["" if value is None else str(value).strip().upper() for value in frame[code_column]],
            dtype=object,
        )
        code_index, code_labels = pd.factorize(codes, sort=False)
        if (code_index < 0).any():
            raise AreaWeightError("species code factorisation produced an unknown code")

        species_lookup = np.empty((len(code_labels), n_ecoprov), dtype="int64")
        alias_lookup = np.full((len(code_labels), n_ecoprov), -1, dtype="int64")
        for code_position, code in enumerate(code_labels):
            for province_position, province in enumerate(ecoprov_labels):
                target, source, rule, confidence = resolve_bc_species(
                    code, province, code_to_rsyc
                )
                species_lookup[code_position, province_position] = species_to_index[target]
                if source not in {"bc_casfri_crosswalk", "none"}:
                    alias_id = len(alias_id_meta)
                    alias_id_meta.append(
                        {
                            "code": str(code),
                            "target": str(target),
                            "source": source,
                            "rule": rule,
                            "confidence": confidence,
                            "ecoprovince": str(province),
                        }
                    )
                    alias_lookup[code_position, province_position] = alias_id

        species_index = species_lookup[code_index, ecoprov_index]
        alias_index = alias_lookup[code_index, ecoprov_index]
        keep = valid & (weights[:, slot] > 0)
        if not keep.any():
            continue

        contribution_area = area_ha[keep] * weights[keep, slot]
        contribution_thlb = thlb_area[keep] * weights[keep, slot]
        group_index = (
            ((tsa_index[keep] * n_ecoprov + ecoprov_index[keep]) * n_ecoreg + ecoreg_index[keep])
            * n_species
            + species_index[keep]
        )
        area_sum += np.bincount(group_index, weights=contribution_area, minlength=group_count)
        thlb_sum += np.bincount(group_index, weights=contribution_thlb, minlength=group_count)
        polygon_count += np.bincount(group_index, minlength=group_count).astype("int64")

        alias_keep = keep & (alias_index >= 0)
        exact_keep = keep & (alias_index < 0) & (species_index != unmapped_index)
        if exact_keep.any():
            exact_group[np.unique(group_index[exact_keep[keep]])] = True
        if alias_keep.any():
            alias_ids = alias_index[alias_keep]
            if len(alias_area_by_id) < len(alias_id_meta):
                grown = np.zeros(len(alias_id_meta), dtype="float64")
                grown[: len(alias_area_by_id)] = alias_area_by_id
                alias_area_by_id = grown
            alias_area_by_id += np.bincount(
                alias_ids,
                weights=contribution_area[alias_keep[keep]],
                minlength=len(alias_id_meta),
            )
            pairs = np.unique(
                np.column_stack([group_index[alias_keep[keep]], alias_ids]), axis=0
            )
            for group_id, alias_id in pairs:
                alias_rules_by_group.setdefault(int(group_id), set()).add(int(alias_id))

        unmapped_keep = keep & (species_index == unmapped_index)
        if unmapped_keep.any():
            unmapped_codes = code_index[unmapped_keep]
            unmapped_area = contribution_area[unmapped_keep[keep]]
            totals = np.bincount(
                unmapped_codes, weights=unmapped_area, minlength=len(code_labels)
            )
            for position in np.flatnonzero(totals):
                code = str(code_labels[position])
                unmapped_by_code[code] = unmapped_by_code.get(code, 0.0) + float(
                    totals[position]
                )

    present = np.flatnonzero(polygon_count)
    species_index = present % n_species
    remainder = present // n_species
    ecoreg_index = remainder % n_ecoreg
    remainder //= n_ecoreg
    ecoprov_index = remainder % n_ecoprov
    tsa_index = remainder // n_ecoprov

    name_by_tsa = (
        pd.Series(tsa_name_values, index=tsa_values).groupby(level=0).first().to_dict()
    )
    table = pd.DataFrame(
        {
            "tsa_number": tsa_labels[tsa_index],
            "tsa_name": [
                NO_TSA if number == NO_TSA else name_by_tsa.get(number, NO_TSA)
                for number in tsa_labels[tsa_index]
            ],
            "ecoprovince": ecoprov_labels[ecoprov_index],
            "ecoregion": ecoreg_labels[ecoreg_index],
            "rsyc_species": species_order[species_index],
            "area_ha": area_sum[present],
            "thlb_area_ha": thlb_sum[present],
            "n_polygons": polygon_count[present],
        }
    )
    table["area_basis"] = AREA_BASIS
    table["source"] = SOURCE
    table["vintage"] = VINTAGE
    table["crs"] = CRS
    table["rsyc_model_group"] = table["rsyc_species"].map(_species_group)

    mapping_rows = []
    for group_id, species in zip(present, species_index):
        if species == unmapped_index:
            mapping_rows.append(("unmapped", "none", "unmapped_no_alias", "none"))
            continue
        alias_ids = alias_rules_by_group.get(int(group_id), set())
        has_exact = bool(exact_group[int(group_id)])
        if not alias_ids:
            mapping_rows.append(("exact_crosswalk", "bc_casfri_crosswalk", "exact_crosswalk", "high"))
            continue
        rules = [alias_id_meta[alias_id] for alias_id in sorted(alias_ids)]
        rule_text = "|".join(sorted({item["rule"] for item in rules}))
        source_text = "|".join(sorted({item["source"] for item in rules}))
        confidence_rank = {"low": 0, "medium": 1, "high": 2}
        confidence = min(
            (item["confidence"] for item in rules),
            key=lambda value: confidence_rank.get(value, 0),
        )
        if has_exact:
            mapping_rows.append(
                ("mixed", f"{source_text}+bc_casfri_crosswalk", rule_text, confidence)
            )
        else:
            mapping_rows.append(("alias", source_text, rule_text, confidence))
    table["mapping_basis"] = [row[0] for row in mapping_rows]
    table["alias_source"] = [row[1] for row in mapping_rows]
    table["alias_rule"] = [row[2] for row in mapping_rows]
    table["alias_confidence"] = [row[3] for row in mapping_rows]
    table = table.sort_values(
        ["ecoprovince", "ecoregion", "tsa_number", "rsyc_species"]
    ).reset_index(drop=True)

    alias_recovery = []
    for alias_id, recovered_area in enumerate(alias_area_by_id):
        if recovered_area <= 0.0:
            continue
        meta = alias_id_meta[alias_id]
        alias_recovery.append(
            {
                "bc_code": meta["code"],
                "target": meta["target"],
                "ecoprovince": meta["ecoprovince"],
                "area_ha": float(recovered_area),
                "rule": meta["rule"],
                "source": meta["source"],
                "confidence": meta["confidence"],
            }
        )
    aggregated_recovery: dict[tuple[str, str, str, str, str, str], float] = {}
    for row in alias_recovery:
        key = (
            row["bc_code"],
            row["target"],
            row["ecoprovince"],
            row["rule"],
            row["source"],
            row["confidence"],
        )
        aggregated_recovery[key] = aggregated_recovery.get(key, 0.0) + row["area_ha"]
    alias_recovery = [
        {
            "bc_code": key[0],
            "target": key[1],
            "ecoprovince": key[2],
            "area_ha": area,
            "rule": key[3],
            "source": key[4],
            "confidence": key[5],
        }
        for key, area in aggregated_recovery.items()
    ]
    alias_recovery.sort(key=lambda row: (-row["area_ha"], row["bc_code"], row["ecoprovince"]))

    diagnostics = {
        "n_polygons_used": int(valid.sum()),
        "n_polygons_no_species": dropped_no_species,
        "area_no_species_ha": dropped_no_species_area,
        "n_polygons_thlb_nodata": invalid_thlb,
        "area_thlb_nodata_ha": invalid_thlb_area,
        "unmapped_by_code": dict(sorted(unmapped_by_code.items(), key=lambda item: -item[1])),
        "alias_recovery": alias_recovery,
        "alias_recovered_area_ha": float(sum(row["area_ha"] for row in alias_recovery)),
        "area_conservation_ha": float(table["area_ha"].sum()),
    }
    return table, diagnostics


# ---------------------------------------------------------------------------
# Batch join
# ---------------------------------------------------------------------------


def _area_lookup(area_table: pd.DataFrame) -> dict[tuple[str, str], tuple[float, float]]:
    grouped = area_table.groupby(["ecoprovince", "rsyc_species"], dropna=False)[
        ["area_ha", "thlb_area_ha"]
    ].sum()
    return {
        (str(ecoprovince), str(species)): (float(row.area_ha), float(row.thlb_area_ha))
        for (ecoprovince, species), row in grouped.iterrows()
    }


def build_batch_join(
    cells_dir: Path,
    area_table: pd.DataFrame,
    output_path: Path,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Scale national batch per-hectare rows by the BC area table.

    Only BC-relevant ecoprovinces are emitted.  A cell with no area match is
    retained with ``area_status='no_area'`` and zero totals; the 1 ha
    placeholder is never substituted.
    """
    if not cells_dir.exists():
        raise AreaWeightError(f"national batch cells directory not found: {cells_dir}")
    lookup = _area_lookup(area_table)
    bc_ecoprovinces = set(area_table["ecoprovince"].astype(str))
    rows: list[dict[str, Any]] = []
    matched_cells = 0
    no_area_cells = 0
    matched_keys: set[tuple[str, str]] = set()
    no_area_keys: set[tuple[str, str]] = set()
    all_keys: set[tuple[str, str]] = set()

    for path in sorted(cells_dir.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        matrix = payload.get("matrix") or []
        if not matrix:
            continue
        ecoprovince = str(matrix[0].get("ecoprovince", ""))
        if ecoprovince not in bc_ecoprovinces:
            continue
        species = str(matrix[0].get("species", ""))
        ecozone = str(matrix[0].get("ecozone", ""))
        stand = payload.get("stand") or {}
        evidence_tier = str(
            stand.get("distribution_evidence_tier")
            or stand.get("coverage_class")
            or "not_recorded"
        )
        batch_status = str(payload.get("status", "unknown"))
        key = (ecoprovince, species)
        all_keys.add(key)
        area_ha, thlb_area_ha = lookup.get(key, (0.0, 0.0))
        area_status = "matched" if area_ha > 0.0 else "no_area"
        if area_status == "matched":
            matched_cells += 1
            matched_keys.add(key)
        else:
            no_area_cells += 1
            no_area_keys.add(key)

        for record in matrix:
            stream = str(record.get("stream", ""))
            volume_m3ha = float(record.get("volume_m3ha") or 0.0)
            residue_m3ha = float(record.get("residue_volume_m3") or 0.0)
            residue_odt_ha = float(record.get("residue_odt") or 0.0)
            volume_m3 = volume_m3ha * area_ha if stream in {"log", "total"} else 0.0
            residue_m3 = residue_m3ha * area_ha if stream == "residue" else 0.0
            residue_odt = residue_odt_ha * area_ha if stream == "residue" else 0.0
            rows.append(
                {
                    "cell_key": record.get("cell_key", payload.get("cell_key")),
                    "ecozone": ecozone,
                    "ecoprovince": ecoprovince,
                    "species": species,
                    "product": record.get("product"),
                    "sort_code": record.get("sort_code"),
                    "stream": stream,
                    "record_type": record.get("record_type"),
                    "density_basis": record.get("density_basis"),
                    "density_odt_m3": record.get("density_odt_m3"),
                    "taper_species": record.get("taper_species"),
                    "volume_m3ha": volume_m3ha,
                    "volume_m3": volume_m3,
                    "residue_m3ha": residue_m3ha,
                    "residue_m3": residue_m3,
                    "residue_odt_ha": residue_odt_ha,
                    "residue_odt": residue_odt,
                    "area_ha": area_ha,
                    "thlb_area_ha": thlb_area_ha,
                    "area_basis": AREA_BASIS if area_status == "matched" else "no_area",
                    "area_status": area_status,
                    "evidence_tier": evidence_tier,
                    "batch_status": batch_status,
                    "cross_border_ecoprovince": bool(ecozone in CROSS_BORDER_ECOZONES),
                }
            )

    if not rows:
        raise AreaWeightError("no BC-relevant batch cells were found")
    joined = pd.DataFrame(rows, columns=list(JOIN_COLUMNS))
    joined.to_csv(output_path, index=False)
    summary = {
        "n_bc_relevant_cells": matched_cells + no_area_cells,
        "n_matched_cells": matched_cells,
        "n_no_area_cells": no_area_cells,
        "n_matched_cell_keys": len(matched_keys),
        "n_no_area_cell_keys": len(no_area_keys),
        "no_area_species": sorted({species for _, species in no_area_keys}),
        "no_area_ecoprovinces": sorted({ecoprovince for ecoprovince, _ in no_area_keys}),
        "species_without_any_area": sorted(
            {species for _, species in all_keys} - {species for _, species in matched_keys}
        ),
        "no_area_cells": sorted(f"{ecoprovince}|{species}" for ecoprovince, species in no_area_keys),
    }
    return joined, summary


def _batch_totals(joined: pd.DataFrame) -> dict[str, Any]:
    log_rows = joined[joined["stream"] == "log"]
    matched = joined[joined["area_status"] == "matched"]

    if log_rows.empty:
        return {"product_volume_m3": [], "residue": [], "taper_total": []}

    product_totals = (
        matched[matched["stream"] == "log"]
        .groupby(["species", "product"], as_index=False)["volume_m3"]
        .sum()
        .sort_values(["species", "product"])
    )
    residue_totals = (
        matched[matched["stream"] == "residue"]
        .groupby("species", as_index=False)[["residue_m3", "residue_odt"]]
        .sum()
        .sort_values("species")
    )
    taper_totals = (
        matched[matched["stream"] == "total"]
        .groupby("species", as_index=False)["volume_m3"]
        .sum()
        .rename(columns={"volume_m3": "taper_total_m3"})
        .sort_values("species")
    )
    return {
        "product_volume_m3": product_totals.to_dict(orient="records"),
        "residue": residue_totals.to_dict(orient="records"),
        "taper_total": taper_totals.to_dict(orient="records"),
        "log_volume_m3": float(matched.loc[matched["stream"] == "log", "volume_m3"].sum()),
        "residue_m3": float(matched.loc[matched["stream"] == "residue", "residue_m3"].sum()),
        "residue_odt": float(matched.loc[matched["stream"] == "residue", "residue_odt"].sum()),
        "taper_total_m3": float(matched.loc[matched["stream"] == "total", "volume_m3"].sum()),
    }


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        value = float(value)
    if isinstance(value, float):
        return None if not math.isfinite(value) else value
    if isinstance(value, (np.bool_,)):
        return bool(value)
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_jsonable(dict(payload)), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _fmt_ha(value: float) -> str:
    return f"{value:,.0f}"


def _markdown_table(rows: list[list[Any]], headers: list[str]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join(["---"] * len(headers)) + "|",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(lines)


def _write_report(
    path: Path,
    *,
    runtime_seconds: float,
    filter_text: str,
    diagnostics: dict[str, Any],
    totals: dict[str, Any],
    ecoprovince_totals: pd.DataFrame,
    unmapped: pd.DataFrame,
    batch_summary: dict[str, Any],
    batch_totals: dict[str, Any],
    n_vri_rows: int,
    n_area_rows: int,
) -> None:
    lines: list[str] = []
    lines.append("# BC forest and THLB area weights")
    lines.append("")
    lines.append(f"Generated {datetime.now(UTC).isoformat(timespec='seconds')}.")
    lines.append(f"Runtime: {runtime_seconds:.1f} s.")
    lines.append("")
    lines.append("## Source and filter")
    lines.append("")
    lines.append(f"- Polygon source: `{SOURCE}` (EPSG:3005).")
    lines.append(f"- Vintage: {VINTAGE}.")
    lines.append(f"- Filter: {filter_text}.")
    lines.append(
        "- Species: `SPECIES_CD_1..6` allocated by normalised `SPECIES_PCT_1..6`; "
        "crosswalk `bc_species_codes` -> `casfri_species_codes` -> RSYC dotted code."
    )
    lines.append(
        "- THLB: `misc.thlb.tif` sampled at each polygon label point; "
        "`thlb_area_ha = area_ha * percent / 100`."
    )
    lines.append(
        f"- Spatial labels: {LABEL_RASTER_RESOLUTION_M:.0f} m rasterisation of "
        "current FADM TSA features and RSYC ecoregions (EPSG:3005)."
    )
    lines.append("")
    lines.append("## Row counts")
    lines.append("")
    lines.append(f"- VRI polygons passing the filter and read: {n_vri_rows:,}.")
    lines.append(f"- Polygons used (positive area and species): {diagnostics['n_polygons_used']:,}.")
    lines.append(
        f"- Polygons with zero species percentage: {diagnostics['n_polygons_no_species']:,} "
        f"({_fmt_ha(diagnostics['area_no_species_ha'])} ha excluded)."
    )
    lines.append(f"- Aggregated area rows: {n_area_rows:,}.")
    lines.append("")
    lines.append("## Totals")
    lines.append("")
    lines.append(f"- Forest area: {_fmt_ha(totals['forest_area_ha'])} ha.")
    lines.append(f"- THLB area: {_fmt_ha(totals['thlb_area_ha'])} ha.")
    lines.append(
        f"- THLB share: {100.0 * totals['thlb_area_ha'] / totals['forest_area_ha']:.1f}%."
    )
    lines.append(
        f"- THLB raster valid pixels: {100.0 * totals['thlb_valid_pixel_fraction']:.1f}% "
        f"of the grid; raster total THLB: {_fmt_ha(totals['raster_thlb_area_ha'])} ha.  "
        "The polygon-sampled total is a subset of that raster total."
    )
    lines.append(
        f"- Polygons with no THLB attribution (nodata/out-of-range): "
        f"{diagnostics['n_polygons_thlb_nodata']:,} "
        f"({_fmt_ha(diagnostics['area_thlb_nodata_ha'])} ha treated as zero THLB)."
    )
    lines.append("")
    lines.append("### By ecoprovince")
    lines.append("")
    eco_rows = [
        [
            row.ecoprovince,
            _fmt_ha(row.area_ha),
            _fmt_ha(row.thlb_area_ha),
            f"{100.0 * row.thlb_area_ha / row.area_ha:.1f}%" if row.area_ha else "0.0%",
        ]
        for row in ecoprovince_totals.itertuples()
    ]
    lines.append(_markdown_table(eco_rows, ["Ecoprovince", "Forest ha", "THLB ha", "THLB share"]))
    lines.append("")
    lines.append("## Species aliases")
    lines.append("")
    alias_recovery = diagnostics.get("alias_recovery", [])
    lines.append(
        "Documented aliases resolve BC commercial codes whose CASFRI target has no "
        "RSYC species.  Exact crosswalk targets always win; aliases are flagged and "
        "carry a confidence, and no alias is applied to a code with a defensible "
        "species target."
    )
    lines.append("")
    if alias_recovery:
        alias_rows = [
            [
                row["bc_code"],
                row["target"],
                row["ecoprovince"],
                _fmt_ha(row["area_ha"]),
                row["rule"],
                row["confidence"],
            ]
            for row in alias_recovery[:40]
        ]
        lines.append(
            _markdown_table(
                alias_rows,
                ["BC code", "Target", "Ecoprovince", "Area ha", "Rule", "Confidence"],
            )
        )
        lines.append("")
        lines.append(
            f"- Recovered alias area: {_fmt_ha(diagnostics.get('alias_recovered_area_ha', 0.0))} ha."
        )
    else:
        lines.append("- No alias area was recovered.")
    lines.append("")
    lines.append("## Unmapped species")
    lines.append("")
    lines.append(
        f"- Area with no RSYC species target and no documented alias: "
        f"{_fmt_ha(totals['unmapped_area_ha'])} ha "
        f"({100.0 * totals['unmapped_area_ha'] / totals['forest_area_ha']:.1f}% of forest area)."
    )
    if not unmapped.empty:
        lines.append("")
        unmapped_rows = [
            [row.raw_code, _fmt_ha(row.area_ha)]
            for row in unmapped.head(20).itertuples()
        ]
        lines.append(_markdown_table(unmapped_rows, ["BC code", "Area ha"]))
    lines.append("")
    lines.append("## National batch join")
    lines.append("")
    lines.append(
        f"- BC-relevant cells: {batch_summary['n_bc_relevant_cells']}; "
        f"matched: {batch_summary['n_matched_cells']}; "
        f"no area: {batch_summary['n_no_area_cells']}."
    )
    lines.append(
        f"- No-area cells: {batch_summary['n_no_area_cell_keys']}; "
        f"species with no area anywhere in BC: "
        f"{', '.join(batch_summary['species_without_any_area']) or 'none'}."
    )
    lines.append(
        f"- Joined log volume: {batch_totals['log_volume_m3']:,.0f} m3; "
        f"residue: {batch_totals['residue_m3']:,.0f} m3 / "
        f"{batch_totals['residue_odt']:,.0f} odt; "
        f"taper total: {batch_totals['taper_total_m3']:,.0f} m3."
    )
    lines.append("")
    lines.append("### BC totals by species")
    lines.append("")
    species_rows: dict[str, dict[str, float]] = {}
    for record in batch_totals["product_volume_m3"]:
        species_rows.setdefault(record["species"], {})["log_m3"] = (
            species_rows.get(record["species"], {}).get("log_m3", 0.0) + float(record["volume_m3"])
        )
    for record in batch_totals["residue"]:
        species_rows.setdefault(record["species"], {})["residue_m3"] = float(record["residue_m3"])
        species_rows[record["species"]]["residue_odt"] = float(record["residue_odt"])
    for record in batch_totals["taper_total"]:
        species_rows.setdefault(record["species"], {})["taper_m3"] = float(record["taper_total_m3"])
    species_table = [
        [
            species,
            _fmt_ha(values.get("log_m3", 0.0)),
            _fmt_ha(values.get("residue_m3", 0.0)),
            _fmt_ha(values.get("residue_odt", 0.0)),
            _fmt_ha(values.get("taper_m3", 0.0)),
        ]
        for species, values in sorted(species_rows.items())
    ]
    lines.append(
        _markdown_table(species_table, ["Species", "Log m3", "Residue m3", "Residue odt", "Taper m3"])
    )
    lines.append("")
    lines.append("## Validation and caveats")
    lines.append("")
    lines.append(
        f"- Total forest area {_fmt_ha(totals['forest_area_ha'])} ha and THLB "
        f"{_fmt_ha(totals['thlb_area_ha'])} ha are reported as computed.  Working "
        "order-of-magnitude references are roughly 50-60 million ha of BC forest "
        "land base and 20-25 million ha of THLB; these are checks, not calibration "
        "targets."
    )
    lines.append(
        "- `UNMAPPED_BC` is not redistributed.  Codes without an exact RSYC target "
        "or a documented alias remain explicit gaps; alias area is assigned to a "
        "named species or model group and is flagged with rule and confidence."
    )
    lines.append(
        "- `LAND_COVER_CLASS_CD_1` was inspected but is null for roughly a quarter "
        "of treed polygons in this release; `BCLCS_LEVEL_2 = 'T'` is the reliable "
        "treed discriminator and is used in the filter."
    )
    lines.append(
        "- The 500 m TSA/ecoregion label raster is an approximation.  It is used "
        "only to assign polygon labels; area and THLB come from the polygon and "
        "the 1 ha THLB raster."
    )
    lines.append(
        "- Cross-border ecoprovinces (Boreal Cordillera, Boreal Plains, Taiga "
        "Plains) receive BC-only area.  Joined totals are BC partials, not full "
        "ecoprovince supply."
    )
    lines.append(
        "- The batch matrix contains `total` and `residue` streams that duplicate "
        "log totals.  Only `log` rows are summed for product volumes, and only "
        "`residue` rows for residue."
    )
    lines.append("")
    lines.append("## Gaps and next steps")
    lines.append("")
    lines.append(
        f"- TSA label gaps: {_fmt_ha(totals['no_tsa_area_ha'])} ha. "
        f"Ecoregion gaps: {_fmt_ha(totals['no_ecoregion_area_ha'])} ha."
    )
    lines.append(
        "- Add documented aliases for unresolved BC commercial codes after review; "
        "do not silently fold them into a species."
    )
    lines.append(
        "- THLB nodata is dominated by forest outside the raster's TSA coverage "
        "(parks, private land, and other non-TSA tenure); it is kept at zero, not "
        "imputed from a TSA mean."
    )
    lines.append(
        "- Replace the 500 m label raster with exact polygon assignment if the "
        "FADM TSA geometry repair is completed."
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def run(config: AreaConfig) -> dict[str, Any]:
    started = time.monotonic()
    os.environ.setdefault("OGR_ORGANIZE_POLYGONS", "SKIP")
    config.out_dir.mkdir(parents=True, exist_ok=True)

    def stage(message: str) -> None:
        if config.verbose:
            print(f"[{time.monotonic() - started:7.1f}s] {message}", flush=True)

    stage("read species crosswalk")
    crosswalk = pd.read_csv(config.crosswalk_path, dtype=str)
    code_to_rsyc, _code_to_canonical = build_species_crosswalk(crosswalk)

    stage("read VRI forest polygons")
    frame = _read_vri(config).reset_index(drop=True)
    n_vri_rows = len(frame)

    frame = frame[
        frame["LABEL_CENTRE_X"].notna()
        & frame["LABEL_CENTRE_Y"].notna()
        & frame["POLYGON_AREA"].notna()
    ].reset_index(drop=True)
    x = frame["LABEL_CENTRE_X"].to_numpy(dtype="float64")
    y = frame["LABEL_CENTRE_Y"].to_numpy(dtype="float64")

    stage("read TSA and RSYC boundaries")
    tsa = _read_current_tsa(config)
    ecoregions = _read_ecoregions(config)

    stage("build label rasters")
    with rasterio.open(config.thlb_path) as dataset:
        bounds = dataset.bounds
        resolution = config.label_resolution_m
        transform, width, height = _grid_from_bounds(bounds, resolution)

    tsa_ids = np.array(
        [int(float(value)) for value in tsa["TSA_NUMBER"].tolist()], dtype="int16"
    )
    tsa_grid = _burn_labels(tsa.geometry.tolist(), tsa_ids, transform, (height, width))
    tsa_lookup = {
        int(float(row.TSA_NUMBER)): str(row.tsa_name) for row in tsa.itertuples()
    }
    sampled_tsa = _sample_grid(tsa_grid, transform, x, y)
    tsa_number = np.array(
        [f"{int(value):02d}" if value > 0 else NO_TSA for value in sampled_tsa],
        dtype=object,
    )
    tsa_name = np.array(
        [tsa_lookup.get(int(value), NO_TSA) if value > 0 else NO_TSA for value in sampled_tsa],
        dtype=object,
    )

    ecoregion_ids = np.arange(1, len(ecoregions) + 1, dtype="int16")
    ecoregion_grid = _burn_labels(
        ecoregions.geometry.tolist(), ecoregion_ids, transform, (height, width)
    )
    sampled_ecoregion = _sample_grid(ecoregion_grid, transform, x, y)
    ecoregion_names = np.concatenate(
        [np.array([NO_ECOREGION], dtype=object), ecoregions["ecoregion"].astype(str).to_numpy()]
    )
    ecoprovince_names = np.concatenate(
        [np.array([NO_ECOPROVINCE], dtype=object), ecoregions["ecoprovince"].astype(str).to_numpy()]
    )
    clipped_ecoregion = np.clip(sampled_ecoregion.astype("int64"), 0, len(ecoregions))
    ecoregion = ecoregion_names[clipped_ecoregion]
    ecoprovince = ecoprovince_names[clipped_ecoregion]

    stage("sample THLB raster")
    thlb_percent, thlb_raster_stats = _sample_thlb(config, x, y)

    stage("allocate species and aggregate")
    area_table, diagnostics = _build_area_table(
        frame,
        code_to_rsyc,
        tsa_number,
        tsa_name,
        ecoprovince,
        ecoregion,
        thlb_percent,
    )

    area_table.to_csv(config.out_dir / "bc_area_weights.csv", index=False)
    alias_table = alias_table_frame()
    alias_table.to_csv(
        config.out_dir / "bc_species_alias_table.csv", index=False, lineterminator="\n"
    )
    stage(f"area rows={len(area_table):,}")

    forest_area_ha = float(area_table["area_ha"].sum())
    thlb_area_ha = float(area_table["thlb_area_ha"].sum())
    if forest_area_ha <= 0.0:
        raise AreaWeightError("aggregated forest area is zero; refusing to write outputs")

    ecoprovince_totals = (
        area_table.groupby("ecoprovince", as_index=False)[["area_ha", "thlb_area_ha"]]
        .sum()
        .sort_values("area_ha", ascending=False)
    )
    unmapped_by_code = diagnostics.pop("unmapped_by_code", {})
    unmapped_frame = pd.DataFrame(
        [{"raw_code": code, "area_ha": area} for code, area in unmapped_by_code.items()]
    ).sort_values("area_ha", ascending=False)
    unmapped_area_ha = float(unmapped_frame["area_ha"].sum())

    no_tsa_area = float(area_table.loc[area_table["tsa_number"] == NO_TSA, "area_ha"].sum())
    no_ecoregion_area = float(
        area_table.loc[area_table["ecoregion"] == NO_ECOREGION, "area_ha"].sum()
    )

    stage("join national batch cells")
    joined, batch_summary = build_batch_join(
        config.cells_dir, area_table, config.out_dir / "bc_national_batch_area_joined.csv"
    )
    batch_totals = _batch_totals(joined)

    runtime_seconds = time.monotonic() - started
    totals = {
        "forest_area_ha": forest_area_ha,
        "thlb_area_ha": thlb_area_ha,
        "unmapped_area_ha": unmapped_area_ha,
        "alias_recovered_area_ha": float(diagnostics.get("alias_recovered_area_ha", 0.0)),
        "no_tsa_area_ha": no_tsa_area,
        "no_ecoregion_area_ha": no_ecoregion_area,
        "thlb_valid_pixel_fraction": thlb_raster_stats["valid_pixel_fraction"],
        "raster_thlb_area_ha": thlb_raster_stats["raster_thlb_area_ha"],
    }
    summary = {
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "runtime_seconds": runtime_seconds,
        "source": SOURCE,
        "vintage": VINTAGE,
        "crs": CRS,
        "area_basis": AREA_BASIS,
        "vri_filter": VRI_FILTER_TEXT,
        "n_vri_rows": n_vri_rows,
        "n_area_rows": len(area_table),
        "totals": totals,
        "diagnostics": diagnostics,
        "area_by_ecoprovince": ecoprovince_totals.to_dict(orient="records"),
        "unmapped_by_code": unmapped_by_code,
        "alias_rules": alias_table.to_dict(orient="records"),
        "alias_recovery": diagnostics.get("alias_recovery", []),
        "batch_join": batch_summary,
        "batch_totals": batch_totals,
        "thlb_raster": thlb_raster_stats,
        "reference_ranges": {
            "bc_forest_land_base_ha": [50_000_000, 60_000_000],
            "bc_thlb_ha": [20_000_000, 25_000_000],
            "note": "Working order-of-magnitude reference ranges; not calibration targets.",
        },
    }
    _write_json(config.out_dir / "bc_area_summary.json", summary)

    _write_report(
        config.out_dir / "BC_AREA_REPORT.md",
        runtime_seconds=runtime_seconds,
        filter_text=VRI_FILTER_TEXT,
        diagnostics=diagnostics,
        totals=totals,
        ecoprovince_totals=ecoprovince_totals,
        unmapped=unmapped_frame,
        batch_summary=batch_summary,
        batch_totals=batch_totals,
        n_vri_rows=n_vri_rows,
        n_area_rows=len(area_table),
    )

    outputs = [
        "bc_area_weights.csv",
        "bc_species_alias_table.csv",
        "bc_area_summary.json",
        "bc_national_batch_area_joined.csv",
        "BC_AREA_REPORT.md",
    ]
    manifest = {
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": summary["generated_utc"],
        "runtime_seconds": runtime_seconds,
        "inputs": {
            "vri": str(config.vri_path),
            "tsa": str(config.tsa_path),
            "rsyc_ecostrat": str(config.ecostrat_path),
            "thlb": str(config.thlb_path),
            "species_crosswalk": str(config.crosswalk_path),
            "national_batch_cells": str(config.cells_dir),
        },
        "outputs": {
            name: {
                "path": str(config.out_dir / name),
                "bytes": (config.out_dir / name).stat().st_size,
                "sha256": _sha256(config.out_dir / name),
            }
            for name in outputs
        },
        "filter": VRI_FILTER_TEXT,
        "label_raster_resolution_m": config.label_resolution_m,
        "crs": CRS,
    }
    _write_json(config.out_dir / "manifest.json", manifest)
    stage("done")
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--timebox-seconds", type=float, default=DEFAULT_TIMEBOX_SECONDS)
    parser.add_argument("--label-resolution-m", type=float, default=LABEL_RASTER_RESOLUTION_M)
    parser.add_argument("--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    config = AreaConfig.default(arguments.repo_root)
    config = AreaConfig(
        **{
            **config.__dict__,
            "timebox_seconds": arguments.timebox_seconds,
            "label_resolution_m": arguments.label_resolution_m,
            "verbose": arguments.verbose,
        }
    )
    summary = run(config)
    print(
        f"forest_area_ha={summary['totals']['forest_area_ha']:.0f} "
        f"thlb_area_ha={summary['totals']['thlb_area_ha']:.0f} "
        f"rows={summary['n_area_rows']} runtime_s={summary['runtime_seconds']:.1f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
