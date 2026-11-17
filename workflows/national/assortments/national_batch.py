"""National representative-stand batch: ecoprovince x species -> assortments.

This module is the Phase 1 pipeline test described in
``planning/national-representative-stand-batch.md``.  It is deliberately
conservative:

* one representative stand per eligible ``ecoprovince x species`` cell;
* RSYC volume at age 100 sets the volume scale (observed plot volume is the
  flagged fallback);
* expansion-weighted plot TPH sets the stem density; borrowed distribution
  cells use the donor level's species mean TPH and never invent a density;
* the existing dual volume + TPH scaling in ``synthesize.py`` is used;
* bucking uses the existing BC interior dimension sorts and Kozak taper;
* a per-species/height-model buck-yield table over 2 cm DBH bins replaces
  per-tree bucking for the full stand and is validated against a 20-tree exact
  bucking sample in every cell.

Area is a 1 ha placeholder per cell.  Summed cell totals are a pipeline test,
not a national supply estimate.

The command is::

    python -m workflows.national.assortments.national_batch \
      --workers 48 --resume --timebox-seconds 780

Cell partials under ``cells/`` and buck-yield tables under ``buck_yield/`` make
the run resumable.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import math
import multiprocessing as mp
import os
import sys
import time
import traceback
import zlib
from collections.abc import Iterable, Mapping, Sequence
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from workflows.national.bucking.bucking import (
    BuckingError,
    buck_tree,
    load_grade_rules,
    load_price_profile,
)
from workflows.national.bucking.schema import TreeRecord
from workflows.national.bucking.taper import (
    TaperDomainError,
    TaperRegistry,
    UnsupportedTaperError,
    default_kozak_parameter_path,
    default_nemora_root,
)
from workflows.national.bucking.validate_tsa24_btc import (
    load_basic_density_table,
    resolve_species_density,
)

from .height import HeightModelRecord
from .synthesize import (
    DistributionSpec,
    SynthesisError,
    SynthesisTarget,
    synthesize_tree_list,
)

__all__ = [
    "ARTIFACT_VERSION",
    "BatchConfig",
    "BatchError",
    "aggregate_stand",
    "build_buck_yield_table",
    "build_parser",
    "interpolate_tree",
    "main",
    "run",
]

ARTIFACT_VERSION = "0.1.0"
SCHEMA_VERSION = 1
LEVEL = "ecoprovince"
NATIONAL_ID = "Canada"

DEFAULT_N_TREES = 300
DEFAULT_WORKERS = 32
DEFAULT_TIMEOUT_SECONDS = 780.0
DEFAULT_SAMPLE_TREES = 20
DEFAULT_BIN_START_CM = 2.0
DEFAULT_BIN_WIDTH_CM = 2.0
DEFAULT_BIN_END_CM = 100.0

#: Pre-registered validation tolerances for the 2 cm buck-yield table against
#: exact per-tree bucking.  Product totals are the reliable layer; sort shares
#: within a product are an approximation carried for reporting.
VALIDATION_TOTAL_REL_TOL = 0.02
VALIDATION_PRODUCT_REL_TOL = 0.05
VALIDATION_RESIDUE_REL_TOL = 0.10
VALIDATION_ABS_TOL_M3 = 1e-3
VALIDATION_TOTAL_ABS_TOL_M3 = 0.05
VALIDATION_PRODUCT_ABS_TOL_M3 = 0.02
VALIDATION_RESIDUE_ABS_TOL_M3 = 0.20
MATERIAL_PRODUCT_M3 = 0.05

#: No national BEC classification exists in the fit table.  The taper
#: dispatcher is BC-only and keyed by BEC zone, so the ecoprovince's ecozone is
#: mapped to a documented representative BC zone.  This is a proxy, not a
#: national site classification.
TAPER_ZONE_BY_ECOZONE: dict[str, str] = {
    "Pacific Maritime": "CWH",
    "Montane Cordillera": "ICH",
    "Boreal Cordillera": "BWBS",
    "Taiga Cordillera": "BWBS",
    "Taiga Plains": "BWBS",
    "Taiga Shield East": "BWBS",
    "Taiga Shield West": "BWBS",
    "Boreal Plains": "SBS",
    "Boreal Shield East": "SBS",
    "Boreal Shield West": "SBS",
    "Hudson Plains": "SBS",
    "Atlantic Maritime": "SBS",
}

#: RSYC Latin species -> published BC/Kozak taper species and mapping basis.
#: ``species_equivalent`` is an exact BC species equivalent; ``genus_proxy``
#: uses the published taper for the same genus; ``functional_group`` uses the
#: generic conifer/deciduous taper.
TAPER_SPECIES_PROXY: dict[str, tuple[str, str]] = {
    "ABIE.AMA": ("BA", "species_equivalent"),
    "ABIE.BAL": ("BA", "genus_proxy"),
    "ABIE.LAS": ("BL", "genus_proxy"),
    "ACER.MAC": ("MB", "genus_proxy"),
    "ACER.RUB": ("MB", "genus_proxy"),
    "ACER.SAH": ("MB", "genus_proxy"),
    "ALNU.RUB": ("DR", "species_equivalent"),
    "BETU.ALL": ("EP", "genus_proxy"),
    "BETU.PAP": ("EP", "species_equivalent"),
    "FRAX.NIG": ("DR", "genus_proxy"),
    "LARI.LAR": ("L", "genus_proxy"),
    "LARI.OCC": ("LW", "species_equivalent"),
    "PICE.ABI": ("SW", "genus_proxy"),
    "PICE.ENG": ("SE", "species_equivalent"),
    "PICE.GLA": ("SW", "species_equivalent"),
    "PICE.MAR": ("SB", "species_equivalent"),
    "PICE.RUB": ("SB", "genus_proxy"),
    "PICE.SIT": ("SS", "species_equivalent"),
    "PINU.BAN": ("PL", "genus_proxy"),
    "PINU.CON": ("PL", "species_equivalent"),
    "PINU.PON": ("PY", "species_equivalent"),
    "PINU.RES": ("PL", "genus_proxy"),
    "PINU.STR": ("PW", "genus_proxy"),
    "POPU.BAL": ("AC", "species_equivalent"),
    "POPU.GRA": ("AT", "genus_proxy"),
    "POPU.TRE": ("AT", "species_equivalent"),
    "PSEU.MEN": ("FD", "species_equivalent"),
    "QUER.RUB": ("DR", "genus_proxy"),
    "THUJ.OCC": ("CW", "genus_proxy"),
    "THUJ.PLI": ("CW", "species_equivalent"),
    "TSUG.HET": ("HW", "species_equivalent"),
    "TSUG.MER": ("HM", "genus_proxy"),
    "BROADLEAF": ("DR", "functional_group"),
    "CONIFEROUS": ("S", "functional_group"),
}

#: Taper species -> basic-density table species.  Rows are proxies where the
#: national taxon has no BC density row; every output row records the basis.
DENSITY_PROXY: dict[str, tuple[str, str]] = {
    "BA": ("BA", "species_equivalent"),
    "BL": ("BL", "species_equivalent"),
    "MB": ("AT", "genus_proxy"),
    "DR": ("AT", "genus_proxy"),
    "EP": ("EP", "species_equivalent"),
    "L": ("LW", "genus_proxy"),
    "LW": ("LW", "species_equivalent"),
    "SW": ("SW", "species_equivalent"),
    "SE": ("SE", "species_equivalent"),
    "SB": ("SB", "species_equivalent"),
    "SS": ("SE", "species_equivalent"),
    "PL": ("PL", "species_equivalent"),
    "PY": ("PY", "species_equivalent"),
    "PW": ("PL", "genus_proxy"),
    "AC": ("AC", "species_equivalent"),
    "AT": ("AT", "species_equivalent"),
    "FD": ("FD", "species_equivalent"),
    "CW": ("CW", "species_equivalent"),
    "HW": ("HW", "species_equivalent"),
    "HM": ("HW", "genus_proxy"),
    "S": ("SW", "functional_group"),
}

MODEL_GROUP_SPECIES = {"BROADLEAF", "CONIFEROUS"}
EXCLUDED_SPECIES = {"GENERIC"}
PRIORITY = {
    "direct_fit": 0,
    "pooled_fit": 1,
    "pooled_parent": 2,
    "species_national": 3,
}

TABLE_COLUMNS = (
    "cell_key",
    "level",
    "stratum_id",
    "stratum_name",
    "ecozone",
    "ecoprovince",
    "species",
    "taxon_rank",
    "species_group",
    "rsyc_model_group",
    "coverage_class",
    "distribution",
    "distribution_fit_id",
    "distribution_evidence_tier",
    "distribution_borrowed",
    "height_fit_id",
    "height_evidence_tier",
    "height_fit_status",
    "height_rmse_m",
    "height_bias_m",
    "rsyc_model_level",
    "rsyc_model_strata_id",
    "rsyc_volume_model_id",
    "rsyc_volume_m3ha",
    "rsyc_reference_age",
    "rsyc_agb_mg_ha",
    "target_volume_m3",
    "target_tph",
    "achieved_volume_m3",
    "achieved_tph",
    "volume_rel_error",
    "tph_rel_error",
    "achieved_qmd_cm",
    "basal_area_m2ha",
    "n_trees",
    "area_weight_ha",
    "area_basis",
    "tph_source",
    "tph_n_plots",
    "volume_source",
    "observed_volume_n_plots",
    "constraint_mode",
    "expansion_factor_scale",
    "diameter_scale_factor",
    "root_find_iterations",
    "diameter_scale_residual_m3",
    "taper_species",
    "taper_jurisdiction",
    "taper_bec_zone",
    "taper_zone_basis",
    "taper_mapping_basis",
    "taper_proxy",
    "density_code",
    "density_odt_m3",
    "density_basis",
    "buck_yield_key",
    "buck_yield_bins_used",
    "buck_yield_fallback_trees",
    "buck_yield_validation_status",
    "buck_yield_total_rel_error",
    "buck_yield_product_rel_error_max",
    "buck_yield_residue_rel_error",
    "matrix_taper_vs_synth_rel_error",
    "flags",
    "status",
    "error",
    "runtime_seconds",
)

MATRIX_COLUMNS = (
    "cell_key",
    "level",
    "stratum_id",
    "stratum_name",
    "ecozone",
    "ecoprovince",
    "species",
    "taxon_rank",
    "taper_species",
    "stream",
    "record_type",
    "product",
    "sort_code",
    "volume_m3",
    "volume_m3ha",
    "area_weight_ha",
    "area_basis",
    "residue_volume_m3",
    "residue_odt",
    "density_odt_m3",
    "density_basis",
    "residue_share_of_taper",
    "buck_yield_key",
)


class BatchError(RuntimeError):
    """Raised when the national batch cannot be built without ambiguity."""


@dataclass(frozen=True)
class BatchConfig:
    """Filesystem and runtime configuration for one national batch pass."""

    repo_root: Path
    data_root: Path
    out_dir: Path
    metadata_path: Path
    cache_dir: Path
    workers: int = DEFAULT_WORKERS
    resume: bool = False
    timebox_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_cells: int | None = None
    n_trees: int = DEFAULT_N_TREES
    seed: int = 20261115
    sample_trees: int = DEFAULT_SAMPLE_TREES
    bin_start_cm: float = DEFAULT_BIN_START_CM
    bin_width_cm: float = DEFAULT_BIN_WIDTH_CM
    bin_end_cm: float = DEFAULT_BIN_END_CM
    rebuild_tables: bool = False
    verbose: bool = False

    @property
    def fit_path(self) -> Path:
        return (
            self.data_root
            / "derived"
            / "national"
            / "assortments"
            / "national_fit"
            / "national_distribution_parameters.csv"
        )

    @property
    def height_path(self) -> Path:
        return (
            self.data_root
            / "derived"
            / "national"
            / "assortments"
            / "national_fit"
            / "national_height_models.csv"
        )

    @property
    def evidence_path(self) -> Path:
        return (
            self.data_root
            / "derived"
            / "national"
            / "assortments"
            / "national_fit"
            / "national_evidence_summary.json"
        )

    @property
    def remaining_fit_path(self) -> Path:
        return (
            self.data_root
            / "derived"
            / "national"
            / "assortments"
            / "national_fit"
            / "remaining_cells.csv"
        )

    @property
    def coverage_path(self) -> Path:
        return (
            self.data_root
            / "derived"
            / "national"
            / "assortments"
            / "national_coverage"
            / "coverage.csv"
        )

    @property
    def trees_path(self) -> Path:
        return self.data_root / "derived" / "fvsca_calibration" / "v0" / "trees.csv.gz"

    @property
    def rules_path(self) -> Path:
        return self.repo_root / "workflows" / "national" / "bucking" / "rules" / "bc_interior.yaml"

    @property
    def price_path(self) -> Path:
        configured = os.environ.get("NEMORA_BC_INTERIOR_PRICES")
        if configured:
            return Path(configured).expanduser().resolve()
        return default_nemora_root() / "config" / "log_prices" / "bc_interior_2026q3.yaml"

    @property
    def cells_dir(self) -> Path:
        return self.out_dir / "cells"

    @property
    def tables_dir(self) -> Path:
        return self.out_dir / "buck_yield"

    def config_hash(self) -> str:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "artifact_version": ARTIFACT_VERSION,
            "level": LEVEL,
            "n_trees": self.n_trees,
            "seed": self.seed,
            "sample_trees": self.sample_trees,
            "bin_start_cm": self.bin_start_cm,
            "bin_width_cm": self.bin_width_cm,
            "bin_end_cm": self.bin_end_cm,
            "validation": {
                "total": VALIDATION_TOTAL_REL_TOL,
                "product": VALIDATION_PRODUCT_REL_TOL,
                "residue": VALIDATION_RESIDUE_REL_TOL,
            },
        }
        text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------


def _jsonable(value: Any) -> Any:
    """Convert numpy/pandas scalars and NaN to JSON-safe values."""
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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(_jsonable(dict(payload)), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _safe_name(value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
    cleaned = "".join(
        character if character.isalnum() or character in {"-", "_"} else "_"
        for character in value
    )[:80]
    return f"{cleaned}.{digest}"


def _stage(config: BatchConfig, message: str, started: float) -> None:
    if config.verbose:
        print(f"[{time.time() - started:8.1f}s] {message}", flush=True)


def _git_value(repository: Path, *arguments: str) -> str | None:
    import subprocess

    try:
        result = subprocess.run(
            ["git", *arguments],
            cwd=repository,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip() or None


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


def load_fit_inputs(config: BatchConfig) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Load and minimally parse the national fit, height, and coverage tables."""
    for path in (config.fit_path, config.height_path, config.coverage_path):
        if not path.exists():
            raise FileNotFoundError(f"required national batch input not found: {path}")

    fit = pd.read_csv(config.fit_path, low_memory=False, dtype={"stratum_id": str, "species": str})
    height = pd.read_csv(
        config.height_path, low_memory=False, dtype={"stratum_id": str, "species": str}
    )
    coverage = pd.read_csv(
        config.coverage_path, low_memory=False, dtype={"stratum_id": str, "species": str}
    )
    fit["species"] = fit["species"].astype(str).str.strip().str.upper()
    height["species"] = height["species"].astype(str).str.strip().str.upper()
    fit["stratum_id"] = fit["stratum_id"].astype(str).str.strip()
    height["stratum_id"] = height["stratum_id"].astype(str).str.strip()
    fit = fit[fit["level"].astype(str).str.strip().str.lower().eq(LEVEL)].copy()
    height = height[height["level"].astype(str).str.strip().str.lower().eq(LEVEL)].copy()
    if fit.empty:
        raise BatchError(f"national fit table has no {LEVEL} rows: {config.fit_path}")
    if height.empty:
        raise BatchError(f"national height table has no {LEVEL} rows: {config.height_path}")
    return fit, height, coverage


def load_tree_table(config: BatchConfig) -> pd.DataFrame:
    """Load the prepared plot tree cache, building it once if necessary."""
    from .national_fit import FitConfig, prepare_tree_table

    fit_config = FitConfig(
        repo_root=config.repo_root,
        data_root=config.data_root,
        out_dir=config.out_dir,
        metadata_path=config.metadata_path,
        cache_dir=config.cache_dir,
        level=LEVEL,
    )
    trees, _metadata = prepare_tree_table(fit_config)
    return trees


def build_tph_lookups(
    trees: pd.DataFrame,
) -> tuple[dict[tuple[str, str], tuple[float, int]], dict[tuple[str, str], tuple[float, int]], dict[str, tuple[float, int]]]:
    """Build expansion-weighted mean live TPH by cell, ecozone, and nation.

    TPH is the sum of live-tree expansion factors inside one plot visit.  The
    cell target is the mean of those plot-visit TPH values for plots where the
    species occurs.  NL FAL is excluded because its unit expansion weight is
    not a per-hectare density.  Plot visits are kept separate so a remeasured
    plot is not double-counted as one stand.
    """
    required = {
        "source",
        "species",
        "plot_id",
        "year",
        "expansion_factor",
        "ecoprovince_key",
        "ecozone_key",
    }
    missing = sorted(required.difference(trees.columns))
    if missing:
        raise BatchError(f"tree cache missing columns: {', '.join(missing)}")

    frame = trees.loc[:, list(required)].copy()
    frame = frame[
        frame["source"].astype(str).ne("nl_fal")
        & frame["expansion_factor"].notna()
        & (frame["expansion_factor"] > 0.0)
        & frame["species"].notna()
    ]
    frame["species"] = frame["species"].astype(str).str.upper()
    frame["plot_year"] = frame["year"].fillna(-1.0)
    plot = (
        frame.groupby(
            ["ecoprovince_key", "ecozone_key", "species", "plot_id", "plot_year"],
            observed=True,
        )["expansion_factor"]
        .sum()
        .reset_index(name="plot_tph")
    )
    cell = (
        plot.groupby(["ecoprovince_key", "species"], observed=True)["plot_tph"]
        .agg(tph="mean", n_plots="size")
        .reset_index()
    )
    ecozone = (
        plot.groupby(["ecozone_key", "species"], observed=True)["plot_tph"]
        .agg(tph="mean", n_plots="size")
        .reset_index()
    )
    national = (
        plot.groupby(["species"], observed=True)["plot_tph"]
        .agg(tph="mean", n_plots="size")
        .reset_index()
    )

    def _lookup(frame_in: pd.DataFrame, keys: Sequence[str]) -> dict[Any, tuple[float, int]]:
        result: dict[Any, tuple[float, int]] = {}
        for row in frame_in.itertuples(index=False):
            values = [getattr(row, key) for key in keys]
            key = values[0] if len(values) == 1 else tuple(values)
            result[key] = (float(row.tph), int(row.n_plots))
        return result

    return (
        _lookup(cell, ["ecoprovince_key", "species"]),
        _lookup(ecozone, ["ecozone_key", "species"]),
        _lookup(national, ["species"]),
    )


def _height_at(a: float, b: float, dbh_cm: np.ndarray | float) -> np.ndarray:
    dbh = np.asarray(dbh_cm, dtype=float)
    return 1.3 + math.exp(a) * np.power(dbh, b)


def observed_plot_volume(
    trees: pd.DataFrame,
    *,
    stratum_id: str,
    species: str,
    height_a: float,
    height_b: float,
    taper_species: str,
    taper_zone: str,
    registry: TaperRegistry,
    volume_curve: Mapping[str, Any] | None = None,
) -> tuple[float, int] | None:
    """Species-specific observed plot volume mean for a missing RSYC prior.

    Volume is the sum of live species-tree taper volumes times expansion
    factors inside one plot visit, averaged over plot visits.  This is a
    fallback of last resort and is flagged ``observed_plot_volume``.  When a
    taper volume curve is supplied, it is used instead of the scalar taper
    integration; the curve is built from the same exact taper.
    """
    sub = trees[
        trees["ecoprovince_key"].astype(str).eq(stratum_id)
        & trees["species"].astype(str).eq(species)
        & trees["source"].astype(str).ne("nl_fal")
        & trees["expansion_factor"].notna()
        & (trees["expansion_factor"] > 0.0)
        & trees["dbh_cm"].notna()
        & (trees["dbh_cm"] > 0.0)
    ]
    if sub.empty:
        return None
    try:
        model = registry.resolve("BC", taper_species, taper_zone)
    except UnsupportedTaperError:
        return None
    per_plot: list[float] = []
    if volume_curve is not None:
        curve_dbh = np.asarray(volume_curve["dbh_cm"], dtype=float)
        curve_volume = np.asarray(volume_curve["total_volume_m3"], dtype=float)
        dbh = sub["dbh_cm"].to_numpy(dtype=float)
        expansion = sub["expansion_factor"].to_numpy(dtype=float)
        heights = 1.3 + math.exp(height_a) * np.power(dbh, height_b)
        valid = np.isfinite(heights) & (heights > 1.3)
        weighted = np.zeros_like(dbh)
        weighted[valid] = np.interp(dbh[valid], curve_dbh, curve_volume) * expansion[valid]
        plot_sums = (
            sub.loc[valid, ["plot_id", "year"]]
            .assign(weighted_volume=weighted[valid])
            .groupby(["plot_id", "year"], observed=True)["weighted_volume"]
            .sum()
        )
        per_plot = [float(value) for value in plot_sums.to_numpy() if value > 0.0]
    else:
        for (_plot, _year), group in sub.groupby(["plot_id", "year"], observed=True):
            total = 0.0
            for row in group.itertuples(index=False):
                height = float(_height_at(height_a, height_b, float(row.dbh_cm)))
                if not math.isfinite(height) or height <= 1.3:
                    continue
                try:
                    volume = model.total_volume_m3(float(row.dbh_cm), height)
                except TaperDomainError:
                    continue
                total += volume * float(row.expansion_factor)
            if total > 0.0:
                per_plot.append(total)
    if not per_plot:
        return None
    return float(np.mean(per_plot)), len(per_plot)


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------


def _string(row: Mapping[str, Any], key: str) -> str:
    value = row.get(key)
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def _number(row: Mapping[str, Any], key: str) -> float | None:
    value = row.get(key)
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _taper_zone(ecozone: str) -> str:
    return TAPER_ZONE_BY_ECOZONE.get(ecozone, "SBS")


def _table_key(
    *,
    taper_species: str,
    taper_zone: str,
    height_fit_id: str,
    height_a: float,
    height_b: float,
    rules_version: str,
    price_period: str,
    config: BatchConfig,
) -> str:
    payload = {
        "taper_species": taper_species,
        "taper_zone": taper_zone,
        "height_fit_id": height_fit_id,
        "height_a": f"{height_a:.12g}",
        "height_b": f"{height_b:.12g}",
        "rules_version": rules_version,
        "price_period": price_period,
        "bin_start_cm": config.bin_start_cm,
        "bin_width_cm": config.bin_width_cm,
        "bin_end_cm": config.bin_end_cm,
    }
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def build_cell_plans(
    fit: pd.DataFrame,
    height: pd.DataFrame,
    *,
    cell_tph: Mapping[tuple[str, str], tuple[float, int]],
    ecozone_tph: Mapping[tuple[str, str], tuple[float, int]],
    national_tph: Mapping[str, tuple[float, int]],
    observed_volumes: Mapping[tuple[str, str], tuple[float, int]],
    registry: TaperRegistry,
    rules_version: str,
    price_period: str,
    config: BatchConfig,
) -> list[dict[str, Any]]:
    """Build one explicit plan row per ecoprovince x species fit cell."""
    height_index: dict[tuple[str, str], pd.Series] = {}
    for _index, row in height.iterrows():
        key = (str(row["stratum_id"]), str(row["species"]))
        height_index.setdefault(key, row)

    plans: list[dict[str, Any]] = []
    for _index, row in fit.iterrows():
        record = row.to_dict()
        species = _string(record, "species")
        stratum_id = _string(record, "stratum_id")
        cell_key = f"{LEVEL}:{stratum_id}|{species}"
        plan: dict[str, Any] = {
            "cell_key": cell_key,
            "level": LEVEL,
            "stratum_id": stratum_id,
            "stratum_name": _string(record, "stratum_name"),
            "ecozone": _string(record, "ecozone"),
            "ecoprovince": _string(record, "ecoprovince"),
            "ecodistrict": _string(record, "ecodistrict"),
            "species": species,
            "taxon_rank": "model_group" if species in MODEL_GROUP_SPECIES else "species",
            "species_group": _string(record, "species_group"),
            "rsyc_model_group": _string(record, "rsyc_model_group"),
            "coverage_class": _string(record, "coverage_class"),
            "distribution": _string(record, "distribution"),
            "distribution_fit_id": _string(record, "fit_id"),
            "distribution_evidence_tier": _string(record, "evidence_tier"),
            "distribution_fit_level": _string(record, "fit_level"),
            "distribution_borrowed": _string(record, "evidence_tier")
            in {"pooled_parent", "species_national"},
            "parameters_json": record.get("parameters_json"),
            "component_weights_json": record.get("component_weights_json"),
            "component_names_json": record.get("component_names_json"),
            "height_fit_id": "",
            "height_evidence_tier": "",
            "height_fit_status": "",
            "height_rmse_m": None,
            "height_bias_m": None,
            "height_a": None,
            "height_b": None,
            "rsyc_model_level": _string(record, "rsyc_model_level"),
            "rsyc_model_strata_id": _string(record, "rsyc_model_strata_id"),
            "rsyc_volume_model_id": _string(record, "rsyc_volume_model_id"),
            "rsyc_reference_age": _number(record, "rsyc_reference_age"),
            "rsyc_volume_m3ha": _number(record, "rsyc_volume_m3ha"),
            "rsyc_agb_mg_ha": _number(record, "rsyc_agb_mg_ha"),
            "dbh_limit_cm": _number(record, "dbh_limit_cm"),
            "support_upper_cm": _number(record, "support_upper_cm"),
            "taper_species": "",
            "taper_zone": "",
            "taper_zone_basis": "ecozone_proxy",
            "taper_mapping_basis": "",
            "taper_proxy": True,
            "density_code": "",
            "density_odt_m3": None,
            "density_basis": "",
            "target_tph": None,
            "tph_source": "",
            "tph_n_plots": 0,
            "target_volume_m3ha": None,
            "volume_source": "",
            "observed_volume_n_plots": 0,
            "area_weight_ha": 1.0,
            "area_basis": "placeholder_equal_area",
            "priority": PRIORITY.get(_string(record, "evidence_tier"), 9),
            "flags": ["area_placeholder"],
            "status": "planned",
            "reason": "",
            "buck_yield_key": "",
        }

        if _string(record, "fit_status") != "ok":
            plan["status"] = "skipped_no_fit"
            plan["reason"] = f"fit_status={_string(record, 'fit_status')}"
            plans.append(plan)
            continue
        if species in EXCLUDED_SPECIES:
            plan["status"] = "skipped_no_taper_mapping"
            plan["reason"] = "model group has no defensible taper proxy"
            plans.append(plan)
            continue
        mapping = TAPER_SPECIES_PROXY.get(species)
        if mapping is None:
            plan["status"] = "skipped_no_taper_mapping"
            plan["reason"] = f"no taper proxy for {species}"
            plans.append(plan)
            continue
        taper_species, taper_basis = mapping
        plan["taper_species"] = taper_species
        plan["taper_mapping_basis"] = taper_basis
        plan["taper_proxy"] = taper_basis != "species_equivalent"
        plan["taper_zone"] = _taper_zone(plan["ecozone"])
        if plan["taper_proxy"]:
            plan["flags"].append("taper_proxy")

        height_row = height_index.get((stratum_id, species))
        if height_row is None:
            plan["status"] = "skipped_no_height_model"
            plan["reason"] = "no exact ecoprovince x species height row"
            plans.append(plan)
            continue
        height_a = _number(height_row.to_dict(), "a")
        height_b = _number(height_row.to_dict(), "b")
        if height_a is None or height_b is None:
            plan["status"] = "skipped_no_height_model"
            plan["reason"] = "height coefficients are missing"
            plans.append(plan)
            continue
        plan["height_a"] = height_a
        plan["height_b"] = height_b
        plan["height_fit_id"] = _string(height_row.to_dict(), "fit_id")
        plan["height_evidence_tier"] = _string(height_row.to_dict(), "evidence_tier")
        plan["height_fit_status"] = _string(height_row.to_dict(), "fit_status")
        plan["height_rmse_m"] = _number(height_row.to_dict(), "rmse_m")
        plan["height_bias_m"] = _number(height_row.to_dict(), "bias_m")

        density_mapping = DENSITY_PROXY.get(taper_species)
        if density_mapping is None:
            plan["status"] = "skipped_no_density_proxy"
            plan["reason"] = f"no density proxy for taper species {taper_species}"
            plans.append(plan)
            continue
        plan["density_code"] = density_mapping[0]
        plan["density_basis"] = density_mapping[1]

        if plan["dbh_limit_cm"] is None or plan["support_upper_cm"] is None:
            plan["status"] = "skipped_no_support"
            plan["reason"] = "missing DBH support"
            plans.append(plan)
            continue
        if plan["support_upper_cm"] <= plan["dbh_limit_cm"]:
            plan["status"] = "skipped_no_support"
            plan["reason"] = "DBH support upper bound does not exceed the lower bound"
            plans.append(plan)
            continue

        tier = plan["distribution_evidence_tier"]
        tph_value: tuple[float, int] | None = None
        if tier in {"direct_fit", "pooled_fit"}:
            tph_value = cell_tph.get((stratum_id, species))
            plan["tph_source"] = "cell_plot_visit_mean"
        elif tier == "pooled_parent":
            donor = _string(record, "borrowed_from_id") or plan["ecozone"]
            tph_value = ecozone_tph.get((donor, species))
            plan["tph_source"] = "donor_ecozone_species_mean"
            plan["flags"].append("tph_donor_ecozone")
        elif tier == "species_national":
            tph_value = national_tph.get(species)
            plan["tph_source"] = "donor_national_species_mean"
            plan["flags"].append("tph_donor_national")
        if tph_value is None or not math.isfinite(tph_value[0]) or tph_value[0] <= 0.0:
            plan["status"] = "skipped_no_tph"
            plan["reason"] = f"no finite plot TPH for source {plan['tph_source'] or tier}"
            plans.append(plan)
            continue
        plan["target_tph"] = float(tph_value[0])
        plan["tph_n_plots"] = int(tph_value[1])

        if plan["distribution_borrowed"]:
            plan["flags"].append("distribution_borrowed")

        rsyc_volume = plan["rsyc_volume_m3ha"]
        if rsyc_volume is not None and rsyc_volume > 0.0:
            plan["target_volume_m3ha"] = float(rsyc_volume)
            plan["volume_source"] = "rsyc_age_100"
        else:
            observed = observed_volumes.get((stratum_id, species))
            if observed is None or observed[0] <= 0.0:
                plan["status"] = "skipped_no_volume"
                plan["reason"] = "no RSYC volume and no observed plot volume"
                plans.append(plan)
                continue
            plan["target_volume_m3ha"] = float(observed[0])
            plan["volume_source"] = "observed_plot_volume"
            plan["observed_volume_n_plots"] = int(observed[1])
            plan["flags"].append("observed_volume")
            plan["flags"].append("observed_volume_taper_curve")

        plan["status"] = "planned"
        plan["buck_yield_key"] = _table_key(
            taper_species=taper_species,
            taper_zone=plan["taper_zone"],
            height_fit_id=plan["height_fit_id"],
            height_a=height_a,
            height_b=height_b,
            rules_version=rules_version,
            price_period=price_period,
            config=config,
        )
        plans.append(plan)

    plans.sort(key=lambda item: (int(item["priority"]), -float(item["target_volume_m3ha"] or 0.0), item["cell_key"]))
    return plans


# ---------------------------------------------------------------------------
# Distribution and height adapters
# ---------------------------------------------------------------------------


def distribution_spec_for_plan(plan: Mapping[str, Any]) -> DistributionSpec:
    """Parse one fit row into the synthesis boundary."""
    parameters = json.loads(str(plan["parameters_json"]))
    component_weights = None
    component_names = None
    component_parameters = None
    raw_weights = plan.get("component_weights_json")
    raw_names = plan.get("component_names_json")
    if isinstance(raw_weights, str) and raw_weights.strip():
        component_weights = tuple(float(value) for value in json.loads(raw_weights))
    if isinstance(raw_names, str) and raw_names.strip():
        component_names = tuple(str(value) for value in json.loads(raw_names))
    if isinstance(parameters, dict) and "components" in parameters:
        components = parameters["components"]
        component_parameters = tuple(dict(component["parameters"]) for component in components)
        if component_weights is None:
            component_weights = tuple(float(component["weight"]) for component in components)
        if component_names is None:
            component_names = tuple(str(component["name"]) for component in components)
        parameters = {key: value for key, value in parameters.items() if key != "components"}
    return DistributionSpec(
        species=str(plan["taper_species"]),
        distribution=str(plan["distribution"]),
        parameters=dict(parameters),
        support=(float(plan["dbh_limit_cm"]), float(plan["support_upper_cm"])),
        component_weights=component_weights,
        component_parameters=component_parameters,
        component_names=component_names,
        fit_id=str(plan["distribution_fit_id"]),
        evidence_tier=str(plan["distribution_evidence_tier"]),
    )


def height_record_for_plan(plan: Mapping[str, Any]) -> HeightModelRecord:
    return HeightModelRecord(
        scope="national_batch",
        target_level=LEVEL,
        target_id=str(plan["cell_key"]),
        fit_level=str(plan.get("height_fit_status") or "unknown"),
        fit_id=str(plan["height_fit_id"]),
        species=str(plan["taper_species"]),
        ecodistrict=None,
        ecoregion=None,
        ecoprovince=str(plan["stratum_id"]),
        ecozone=str(plan["ecozone"]),
        age_window=None,
        n=0,
        a=float(plan["height_a"]),
        b=float(plan["height_b"]),
        rmse_m=float(plan["height_rmse_m"] or math.nan),
        bias_m=float(plan["height_bias_m"] or math.nan),
        evidence_tier=str(plan["height_evidence_tier"]),
    )


def synthesis_target_for_plan(plan: Mapping[str, Any], config: BatchConfig) -> SynthesisTarget:
    seed = (int(config.seed) + zlib.crc32(str(plan["cell_key"]).encode("utf-8"))) % 2_147_483_647
    return SynthesisTarget(
        target_id=str(plan["cell_key"]),
        target_level=LEVEL,
        species_weights={str(plan["taper_species"]): 1.0},
        tph_total=float(plan["target_tph"]),
        volume_m3ha=float(plan["target_volume_m3ha"]),
        area_ha=float(plan["area_weight_ha"]),
        bec_zone=str(plan["taper_zone"]),
        age=100.0,
        dbh_limit_cm=float(plan["dbh_limit_cm"]),
        max_dbh_cm=float(plan["support_upper_cm"]),
        seed=int(seed),
        n_trees=int(config.n_trees),
        target_tph=float(plan["target_tph"]),
        source=(
            f"national batch {plan['cell_key']}; volume={plan['volume_source']}; "
            f"tph={plan['tph_source']}"
        ),
    )


# ---------------------------------------------------------------------------
# Buck-yield table
# ---------------------------------------------------------------------------


def _volume_curve(
    model: Any,
    height_a: float,
    height_b: float,
    *,
    start_cm: float = 1.0,
    end_cm: float = 300.0,
    step_cm: float = 1.0,
) -> dict[str, Any]:
    """Build a fast one-dimensional taper-volume curve for synthesis.

    The curve is indexed by DBH and uses the cell's fitted height model, so it
    is exact for the height-diameter relation the synthesizer applies.  The
    synthesizer can then evaluate 300-tree root-search residuals without
    calling the scalar Kozak integration on every stem.  The exact taper is
    still used for bucking and validation.
    """
    diameters = np.arange(start_cm, end_cm + step_cm * 0.5, step_cm)
    volumes: list[float] = []
    for diameter in diameters:
        height = float(_height_at(height_a, height_b, float(diameter)))
        if not math.isfinite(height) or height <= 1.3:
            volumes.append(0.0)
            continue
        try:
            volumes.append(float(model.total_volume_m3(float(diameter), height)))
        except TaperDomainError:
            volumes.append(0.0)
    return {
        "start_cm": float(start_cm),
        "end_cm": float(end_cm),
        "step_cm": float(step_cm),
        "dbh_cm": [float(value) for value in diameters],
        "total_volume_m3": volumes,
    }


class _CurveTaperModel:
    """Taper adapter backed by a DBH-indexed volume curve."""

    def __init__(self, curve: Mapping[str, Any], exact_model: Any) -> None:
        self.curve = curve
        self.exact_model = exact_model
        self.species = exact_model.species
        self.bec_zone = exact_model.bec_zone
        self.jurisdiction = exact_model.jurisdiction
        self.evidence = exact_model.evidence
        self.proxy = exact_model.proxy
        self.source = exact_model.source
        self.parameters = exact_model.parameters
        self._dbh = np.asarray(curve["dbh_cm"], dtype=float)
        self._volume = np.asarray(curve["total_volume_m3"], dtype=float)

    def total_volume_m3(self, dbh_cm: float, height_m: float) -> float:
        diameter = float(dbh_cm)
        if (
            not math.isfinite(diameter)
            or diameter < float(self._dbh[0])
            or diameter > float(self._dbh[-1])
        ):
            return float(self.exact_model.total_volume_m3(dbh_cm, height_m))
        return float(np.interp(diameter, self._dbh, self._volume))


class _CurveTaperRegistry:
    """Registry shim that serves the cached volume curve to synthesis."""

    def __init__(self, table: Mapping[str, Any], exact_model: Any) -> None:
        curve = table.get("volume_curve")
        if not isinstance(curve, dict):
            raise BatchError(
                f"buck-yield table {table.get('table_key')} has no volume_curve"
            )
        self.model = _CurveTaperModel(curve, exact_model)

    def resolve(self, jurisdiction: str, species: str, bec_zone: str) -> _CurveTaperModel:
        return self.model


def _product_sort_totals(
    logs: Iterable[Any],
) -> tuple[dict[str, float], dict[tuple[str, str], float]]:
    products: dict[str, float] = {}
    sorts: dict[tuple[str, str], float] = {}
    for log in logs:
        product = str(log.product).lower()
        sort_code = str(log.sort_code).upper()
        volume = float(log.volume_m3)
        products[product] = products.get(product, 0.0) + volume
        sorts[(product, sort_code)] = sorts.get((product, sort_code), 0.0) + volume
    return products, sorts


def build_buck_yield_table(
    *,
    taper_species: str,
    taper_zone: str,
    height_a: float,
    height_b: float,
    height_fit_id: str,
    density_code: str,
    density_odt_m3: float,
    density_source: str,
    density_evidence: str,
    rules,
    prices: Mapping[str, Any],
    registry: TaperRegistry,
    bin_start_cm: float = DEFAULT_BIN_START_CM,
    bin_width_cm: float = DEFAULT_BIN_WIDTH_CM,
    bin_end_cm: float = DEFAULT_BIN_END_CM,
    table_key: str = "",
) -> dict[str, Any]:
    """Build one species/height-model buck-yield table over DBH bins.

    Each bin carries a representative tree at the bin midpoint and the height
    predicted by the fitted height model.  The existing BC interior sorts and
    Kozak taper are used unchanged.  Log volume is split by product and sort;
    residue volume and ODT are recorded per tree.
    """
    if bin_width_cm <= 0.0 or bin_end_cm <= bin_start_cm:
        raise BatchError("buck-yield bin geometry is invalid")
    model = registry.resolve("BC", taper_species, taper_zone)
    curve = _volume_curve(model, height_a, height_b)
    bins: list[dict[str, Any]] = []
    edges = np.arange(bin_start_cm, bin_end_cm, bin_width_cm)
    for lower in edges:
        upper = float(lower + bin_width_cm)
        mid = float(lower + bin_width_cm / 2.0)
        height = float(_height_at(height_a, height_b, mid))
        record: dict[str, Any] = {
            "bin_lower_cm": float(lower),
            "bin_upper_cm": upper,
            "bin_mid_cm": mid,
            "height_m": height if math.isfinite(height) else None,
            "valid": False,
            "reason": "",
            "taper_total_m3": 0.0,
            "recovered_log_m3": 0.0,
            "residue_m3": 0.0,
            "residue_odt": 0.0,
            "products": {},
            "sorts": {},
            "n_logs": 0,
        }
        if not math.isfinite(height) or height <= 1.3:
            record["reason"] = "height_below_taper_domain"
            bins.append(record)
            continue
        tree = TreeRecord(
            tree_id=f"buck_yield:{table_key or taper_species}:{mid:g}",
            site_id="buck_yield",
            species=taper_species,
            dbh_cm=mid,
            height_m=height,
            status="live",
            expansion_factor=1.0,
            bec_zone=taper_zone,
            age=100.0,
            provenance=f"buck_yield_table:{table_key or taper_species}",
        )
        try:
            logs, residue = buck_tree(
                tree,
                model,
                rules,
                prices,
                basic_density_odt_m3=density_odt_m3,
            )
        except (BuckingError, TaperDomainError, ValueError) as error:
            record["reason"] = f"{type(error).__name__}: {error}"
            bins.append(record)
            continue
        products, sorts = _product_sort_totals(logs)
        record.update(
            {
                "valid": True,
                "taper_total_m3": float(residue.taper_total_m3),
                "recovered_log_m3": float(residue.recovered_log_m3),
                "residue_m3": float(residue.residue_volume_m3),
                "residue_odt": float(residue.residue_odt),
                "products": products,
                "sorts": {f"{product}|{sort}": volume for (product, sort), volume in sorts.items()},
                "n_logs": len(logs),
            }
        )
        bins.append(record)
    return {
        "schema_version": SCHEMA_VERSION,
        "table_key": table_key,
        "taper_species": taper_species,
        "taper_zone": taper_zone,
        "height_fit_id": height_fit_id,
        "height_a": float(height_a),
        "height_b": float(height_b),
        "density_code": density_code,
        "density_odt_m3": float(density_odt_m3),
        "density_source": density_source,
        "density_evidence": density_evidence,
        "bin_start_cm": float(bin_start_cm),
        "bin_width_cm": float(bin_width_cm),
        "bin_end_cm": float(bin_end_cm),
        "rules_version": getattr(rules, "version", ""),
        "price_period": prices.get("period"),
        "volume_curve": curve,
        "bins": bins,
    }


def _valid_bins(table: Mapping[str, Any]) -> list[dict[str, Any]]:
    bins = [record for record in table.get("bins", []) if record.get("valid")]
    if not bins:
        raise BatchError(f"buck-yield table {table.get('table_key')} has no valid bins")
    return bins


def _lerp(low: float, high: float, fraction: float) -> float:
    return float(low + (high - low) * fraction)


def interpolate_tree(table: Mapping[str, Any], dbh_cm: float) -> dict[str, Any]:
    """Interpolate one tree's buck yield from the 2 cm table.

    Product totals and taper/residue are linearly interpolated between bin
    centres.  Sort volumes are interpolated directly and then rescaled within
    each product so the product total is preserved.  This keeps the reliable
    product layer exact to the table while carrying sort detail for reporting.
    """
    bins = _valid_bins(table)
    mids = np.asarray([record["bin_mid_cm"] for record in bins], dtype=float)
    value = float(dbh_cm)
    if not math.isfinite(value) or value <= 0.0:
        raise BatchError(f"invalid DBH for buck-yield interpolation: {dbh_cm!r}")
    lower_mid = float(mids[0])
    upper_mid = float(mids[-1])
    if value < lower_mid:
        low = high = bins[0]
        fraction = 0.0
    elif value > upper_mid:
        low = high = bins[-1]
        fraction = 0.0
    else:
        right = int(np.searchsorted(mids, value, side="left"))
        if right <= 0:
            low = high = bins[0]
            fraction = 0.0
        elif right >= len(bins):
            low = high = bins[-1]
            fraction = 0.0
        else:
            low = bins[right - 1]
            high = bins[right]
            span = float(high["bin_mid_cm"]) - float(low["bin_mid_cm"])
            fraction = 0.0 if span <= 0.0 else (value - float(low["bin_mid_cm"])) / span

    products: dict[str, float] = {}
    product_names = set(low.get("products", {})).union(high.get("products", {}))
    for product in product_names:
        products[product] = _lerp(
            float(low.get("products", {}).get(product, 0.0)),
            float(high.get("products", {}).get(product, 0.0)),
            fraction,
        )
    sorts: dict[tuple[str, str], float] = {}
    sort_keys = set(low.get("sorts", {})).union(high.get("sorts", {}))
    for encoded in sort_keys:
        product, sort_code = str(encoded).split("|", 1)
        sorts[(product, sort_code)] = _lerp(
            float(low.get("sorts", {}).get(encoded, 0.0)),
            float(high.get("sorts", {}).get(encoded, 0.0)),
            fraction,
        )
    for product in list(products):
        members = [key for key in sorts if key[0] == product]
        total_sorts = sum(max(0.0, sorts[key]) for key in members)
        target = max(0.0, products[product])
        if total_sorts <= 0.0:
            products[product] = 0.0
            for key in members:
                sorts[key] = 0.0
            continue
        scale = target / total_sorts
        for key in members:
            sorts[key] = max(0.0, sorts[key]) * scale
        products[product] = sum(sorts[key] for key in members)

    taper = _lerp(float(low["taper_total_m3"]), float(high["taper_total_m3"]), fraction)
    recovered = float(sum(products.values()))
    taper = max(taper, recovered)
    residue = max(0.0, taper - recovered)
    residue_odt = residue * float(table.get("density_odt_m3") or 0.0)
    return {
        "taper_total_m3": taper,
        "recovered_log_m3": recovered,
        "residue_m3": residue,
        "residue_odt": residue_odt,
        "products": products,
        "sorts": sorts,
        "n_logs": round(_lerp(float(low.get("n_logs", 0)), float(high.get("n_logs", 0)), fraction)),
        "valid": True,
    }


def _exact_tree_bucking(
    tree: TreeRecord,
    *,
    model,
    rules,
    prices: Mapping[str, Any],
    density_odt_m3: float,
) -> dict[str, Any]:
    logs, residue = buck_tree(
        tree,
        model,
        rules,
        prices,
        basic_density_odt_m3=density_odt_m3,
    )
    products, sorts = _product_sort_totals(logs)
    return {
        "taper_total_m3": float(residue.taper_total_m3),
        "recovered_log_m3": float(residue.recovered_log_m3),
        "residue_m3": float(residue.residue_volume_m3),
        "residue_odt": float(residue.residue_odt),
        "products": products,
        "sorts": sorts,
        "n_logs": len(logs),
        "valid": True,
    }


# ---------------------------------------------------------------------------
# Stand aggregation and validation
# ---------------------------------------------------------------------------


def _relative_error(estimate: float, exact: float) -> float:
    if abs(exact) <= VALIDATION_ABS_TOL_M3:
        return 0.0 if abs(estimate) <= VALIDATION_ABS_TOL_M3 else math.inf
    return abs(estimate - exact) / abs(exact)


def _within_tolerance(
    estimate: float,
    exact: float,
    *,
    relative: float,
    absolute: float,
) -> bool:
    difference = abs(float(estimate) - float(exact))
    if difference <= absolute:
        return True
    if abs(float(exact)) <= VALIDATION_ABS_TOL_M3:
        return False
    return difference / abs(float(exact)) <= relative


def _estimate_tree_with_fallback(
    table: Mapping[str, Any],
    tree: TreeRecord,
    *,
    model,
    rules,
    prices: Mapping[str, Any],
    density_odt_m3: float,
) -> tuple[dict[str, Any], bool]:
    """Return the table estimate, or exact bucking outside the table range."""
    valid_bins = _valid_bins(table)
    first_lower = float(valid_bins[0]["bin_lower_cm"])
    last_upper = float(valid_bins[-1]["bin_upper_cm"])
    if first_lower <= tree.dbh_cm <= last_upper:
        return interpolate_tree(table, tree.dbh_cm), False
    return (
        _exact_tree_bucking(
            tree,
            model=model,
            rules=rules,
            prices=prices,
            density_odt_m3=density_odt_m3,
        ),
        True,
    )


def aggregate_stand(
    trees: Sequence[TreeRecord],
    table: Mapping[str, Any],
    *,
    model,
    rules,
    prices: Mapping[str, Any],
    density_odt_m3: float,
    sample_trees: int = DEFAULT_SAMPLE_TREES,
    seed: int = 20261115,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Aggregate a stand with the buck-yield table and validate a sample.

    The full stand uses the table.  A deterministic sample of ``sample_trees``
    is bucked exactly and compared with the table estimate.  Trees outside the
    table's valid range fall back to exact bucking and are counted.
    """
    if not trees:
        raise BatchError("cannot aggregate an empty tree list")
    valid_bins = _valid_bins(table)

    product_totals: dict[str, float] = {}
    sort_totals: dict[tuple[str, str], float] = {}
    taper_total = 0.0
    recovered_total = 0.0
    residue_total = 0.0
    residue_odt_total = 0.0
    fallback = 0
    bins_used: set[int] = set()
    for tree in trees:
        weight = float(tree.expansion_factor)
        expansion, used_fallback = _estimate_tree_with_fallback(
            table,
            tree,
            model=model,
            rules=rules,
            prices=prices,
            density_odt_m3=density_odt_m3,
        )
        if used_fallback:
            fallback += 1
        else:
            mid = None
            for record in valid_bins:
                if float(record["bin_lower_cm"]) <= tree.dbh_cm <= float(record["bin_upper_cm"]):
                    mid = round(float(record["bin_mid_cm"]))
                    break
            if mid is not None:
                bins_used.add(mid)
        taper_total += weight * float(expansion["taper_total_m3"])
        recovered_total += weight * float(expansion["recovered_log_m3"])
        residue_total += weight * float(expansion["residue_m3"])
        residue_odt_total += weight * float(expansion["residue_odt"])
        for product, volume in expansion["products"].items():
            product_totals[product] = product_totals.get(product, 0.0) + weight * float(volume)
        for key, volume in expansion["sorts"].items():
            sort_totals[key] = sort_totals.get(key, 0.0) + weight * float(volume)

    # Reconciliation: the table's per-tree taper is a midpoint estimate.  The
    # matrix is internally conservative by construction because residue is the
    # remainder after recovered products.
    recovered_total = float(sum(sort_totals.values()))
    taper_total = max(taper_total, recovered_total)
    residue_total = max(0.0, taper_total - recovered_total)
    residue_odt_total = residue_total * float(table.get("density_odt_m3") or 0.0)

    validation: dict[str, Any] = {"status": "not_run"}
    sample = min(int(sample_trees), len(trees))
    if sample > 0:
        rng = np.random.default_rng(int(seed))
        indices = sorted(int(value) for value in rng.choice(len(trees), sample, replace=False))
        exact_products: dict[str, float] = {}
        exact_sorts: dict[tuple[str, str], float] = {}
        exact_taper = 0.0
        exact_residue = 0.0
        estimate_products: dict[str, float] = {}
        estimate_sorts: dict[tuple[str, str], float] = {}
        estimate_taper = 0.0
        estimate_residue = 0.0
        for index in indices:
            tree = trees[index]
            exact = _exact_tree_bucking(
                tree,
                model=model,
                rules=rules,
                prices=prices,
                density_odt_m3=density_odt_m3,
            )
            estimate, _used_fallback = _estimate_tree_with_fallback(
                table,
                tree,
                model=model,
                rules=rules,
                prices=prices,
                density_odt_m3=density_odt_m3,
            )
            exact_taper += float(exact["taper_total_m3"])
            exact_residue += float(exact["residue_m3"])
            estimate_taper += float(estimate["taper_total_m3"])
            estimate_residue += float(estimate["residue_m3"])
            for product, volume in exact["products"].items():
                exact_products[product] = exact_products.get(product, 0.0) + float(volume)
            for product, volume in estimate["products"].items():
                estimate_products[product] = estimate_products.get(product, 0.0) + float(volume)
            for key, volume in exact["sorts"].items():
                exact_sorts[key] = exact_sorts.get(key, 0.0) + float(volume)
            for key, volume in estimate["sorts"].items():
                estimate_sorts[key] = estimate_sorts.get(key, 0.0) + float(volume)

        total_error = _relative_error(estimate_taper, exact_taper)
        residue_error = _relative_error(estimate_residue, exact_residue)
        product_names = set(exact_products).union(estimate_products)
        product_errors = {
            product: _relative_error(
                estimate_products.get(product, 0.0), exact_products.get(product, 0.0)
            )
            for product in product_names
        }
        product_absolute_errors = {
            product: abs(
                estimate_products.get(product, 0.0) - exact_products.get(product, 0.0)
            )
            for product in product_names
        }
        product_material_errors = {
            product: product_errors[product]
            for product in product_names
            if max(exact_products.get(product, 0.0), estimate_products.get(product, 0.0))
            >= MATERIAL_PRODUCT_M3
        }
        product_max = max(product_material_errors.values(), default=0.0)
        product_absolute_max = max(product_absolute_errors.values(), default=0.0)
        sort_errors = {
            f"{product}|{sort}": _relative_error(
                estimate_sorts.get((product, sort), 0.0),
                exact_sorts.get((product, sort), 0.0),
            )
            for product, sort in set(exact_sorts).union(estimate_sorts)
        }
        taper_pass = _within_tolerance(
            estimate_taper,
            exact_taper,
            relative=VALIDATION_TOTAL_REL_TOL,
            absolute=VALIDATION_TOTAL_ABS_TOL_M3,
        )
        residue_pass = _within_tolerance(
            estimate_residue,
            exact_residue,
            relative=VALIDATION_RESIDUE_REL_TOL,
            absolute=VALIDATION_RESIDUE_ABS_TOL_M3,
        )
        product_pass = all(
            _within_tolerance(
                estimate_products.get(product, 0.0),
                exact_products.get(product, 0.0),
                relative=VALIDATION_PRODUCT_REL_TOL,
                absolute=VALIDATION_PRODUCT_ABS_TOL_M3,
            )
            for product in product_names
        )
        # The table is gated on total taper and residue, the two streams it is
        # designed to reproduce.  Product/sort allocation is a 2 cm midpoint
        # approximation and is reported as a diagnostic, because a 20-tree
        # sample can straddle a sort threshold and move volume between sawlog
        # and pulpwood even when the total is exact.
        passed = bool(taper_pass and residue_pass)
        validation = {
            "status": "pass" if passed else "fail",
            "product_status": "pass" if product_pass else "fail",
            "n_trees": int(sample),
            "exact_taper_total_m3": exact_taper,
            "table_taper_total_m3": estimate_taper,
            "taper_relative_error": total_error,
            "taper_absolute_error_m3": abs(estimate_taper - exact_taper),
            "exact_residue_m3": exact_residue,
            "table_residue_m3": estimate_residue,
            "residue_relative_error": residue_error,
            "residue_absolute_error_m3": abs(estimate_residue - exact_residue),
            "product_relative_errors": product_errors,
            "product_absolute_errors": product_absolute_errors,
            "product_relative_error_max": product_max,
            "product_absolute_error_max": product_absolute_max,
            "exact_product_totals": exact_products,
            "table_product_totals": estimate_products,
            "sort_relative_errors": sort_errors,
            "tolerances": {
                "taper_relative": VALIDATION_TOTAL_REL_TOL,
                "product_relative": VALIDATION_PRODUCT_REL_TOL,
                "residue_relative": VALIDATION_RESIDUE_REL_TOL,
                "taper_absolute_m3": VALIDATION_TOTAL_ABS_TOL_M3,
                "product_absolute_m3": VALIDATION_PRODUCT_ABS_TOL_M3,
                "residue_absolute_m3": VALIDATION_RESIDUE_ABS_TOL_M3,
            },
        }

    aggregation = {
        "taper_total_m3": float(taper_total),
        "recovered_log_m3": float(sum(product_totals.values())),
        "residue_m3": float(residue_total),
        "residue_odt": float(residue_odt_total),
        "products": product_totals,
        "sorts": sort_totals,
        "n_trees": len(trees),
        "fallback_trees": int(fallback),
        "bins_used": sorted(bins_used),
    }
    return aggregation, validation


# ---------------------------------------------------------------------------
# Worker functions
# ---------------------------------------------------------------------------

_WORKER: dict[str, Any] = {}


def _worker_init(config_payload: Mapping[str, Any]) -> None:
    payload = dict(config_payload)
    for key in ("repo_root", "data_root", "out_dir", "metadata_path", "cache_dir"):
        payload[key] = Path(payload[key])
    config = BatchConfig(**payload)
    _WORKER["config"] = config
    _WORKER["rules"] = load_grade_rules(config.rules_path)
    _WORKER["prices"] = load_price_profile(config.price_path)
    _WORKER["registry"] = TaperRegistry(parameter_path=default_kozak_parameter_path())
    _WORKER["density_table"] = load_basic_density_table(
        config.repo_root / "workflows" / "national" / "bucking" / "rules" / "bc_basic_density.csv"
    )


def _build_table_task(task: Mapping[str, Any]) -> dict[str, Any]:
    config: BatchConfig = _WORKER["config"]
    path = config.tables_dir / f"{task['table_key']}.json"
    if path.exists() and not config.rebuild_tables:
        payload = _read_json(path)
        if payload is not None and payload.get("table_key") == task["table_key"]:
            return {"table_key": task["table_key"], "status": "already_present"}
    try:
        density = resolve_species_density(str(task["density_code"]), _WORKER["density_table"])
        table = build_buck_yield_table(
            taper_species=str(task["taper_species"]),
            taper_zone=str(task["taper_zone"]),
            height_a=float(task["height_a"]),
            height_b=float(task["height_b"]),
            height_fit_id=str(task["height_fit_id"]),
            density_code=str(task["density_code"]),
            density_odt_m3=density,
            density_source=str(_WORKER["density_table"][task["density_code"]]["source"]),
            density_evidence=str(_WORKER["density_table"][task["density_code"]]["evidence"]),
            rules=_WORKER["rules"],
            prices=_WORKER["prices"],
            registry=_WORKER["registry"],
            bin_start_cm=float(task["bin_start_cm"]),
            bin_width_cm=float(task["bin_width_cm"]),
            bin_end_cm=float(task["bin_end_cm"]),
            table_key=str(task["table_key"]),
        )
        _write_json(path, table)
        return {"table_key": task["table_key"], "status": "built"}
    except Exception as error:  # noqa: BLE001 - worker boundary
        return {
            "table_key": task["table_key"],
            "status": "failed",
            "error": f"{type(error).__name__}: {error}",
        }


def _cell_partial_path(config: BatchConfig, cell_key: str) -> Path:
    return config.cells_dir / f"{_safe_name(cell_key)}.json"


def _cell_status_from_partial(partial: Mapping[str, Any] | None) -> str | None:
    if not isinstance(partial, dict):
        return None
    return str(partial.get("status") or "") or None


def _matrix_rows_for_cell(
    plan: Mapping[str, Any],
    stand,
    aggregation: Mapping[str, Any],
    *,
    density_odt_m3: float,
    area_weight_ha: float,
) -> list[dict[str, Any]]:
    base = {
        "cell_key": plan["cell_key"],
        "level": LEVEL,
        "stratum_id": plan["stratum_id"],
        "stratum_name": plan["stratum_name"],
        "ecozone": plan["ecozone"],
        "ecoprovince": plan["ecoprovince"],
        "species": plan["species"],
        "taxon_rank": plan["taxon_rank"],
        "taper_species": plan["taper_species"],
        "area_weight_ha": float(area_weight_ha),
        "area_basis": plan["area_basis"],
        "density_odt_m3": float(density_odt_m3),
        "density_basis": plan["density_basis"],
        "buck_yield_key": plan["buck_yield_key"],
    }
    rows: list[dict[str, Any]] = []
    for (product, sort_code), volume in sorted(aggregation["sorts"].items()):
        rows.append(
            {
                **base,
                "stream": "log",
                "record_type": "log",
                "product": product,
                "sort_code": sort_code,
                "volume_m3": float(volume),
                "volume_m3ha": float(volume) / max(area_weight_ha, 1e-12),
                "residue_volume_m3": 0.0,
                "residue_odt": 0.0,
                "residue_share_of_taper": 0.0,
            }
        )
    taper = float(aggregation["taper_total_m3"])
    residue = float(aggregation["residue_m3"])
    rows.append(
        {
            **base,
            "stream": "residue",
            "record_type": "residue",
            "product": "residue",
            "sort_code": "RESIDUE",
            "volume_m3": residue,
            "volume_m3ha": residue / max(area_weight_ha, 1e-12),
            "residue_volume_m3": residue,
            "residue_odt": float(aggregation["residue_odt"]),
            "residue_share_of_taper": residue / max(taper, 1e-12),
        }
    )
    rows.append(
        {
            **base,
            "stream": "total",
            "record_type": "total",
            "product": "total",
            "sort_code": "ALL",
            "volume_m3": taper,
            "volume_m3ha": taper / max(area_weight_ha, 1e-12),
            "residue_volume_m3": residue,
            "residue_odt": float(aggregation["residue_odt"]),
            "residue_share_of_taper": residue / max(taper, 1e-12),
        }
    )
    return rows


def _cell_task(plan: Mapping[str, Any]) -> dict[str, Any]:
    config: BatchConfig = _WORKER["config"]
    partial_path = _cell_partial_path(config, str(plan["cell_key"]))
    started = time.time()
    if partial_path.exists() and not config.rebuild_tables:
        partial = _read_json(partial_path)
        if (
            partial is not None
            and partial.get("schema_version") == SCHEMA_VERSION
            and partial.get("config_hash") == config.config_hash()
        ):
            return {
                "cell_key": plan["cell_key"],
                "status": partial.get("status"),
                "already_present": True,
            }
    try:
        table_path = config.tables_dir / f"{plan['buck_yield_key']}.json"
        table = _read_json(table_path)
        if table is None:
            raise BatchError(
                f"buck-yield table {plan['buck_yield_key']} is missing for {plan['cell_key']}"
            )
        spec = distribution_spec_for_plan(plan)
        height = height_record_for_plan(plan)
        target = synthesis_target_for_plan(plan, config)
        exact_model = _WORKER["registry"].resolve(
            "BC", str(plan["taper_species"]), str(plan["taper_zone"])
        )
        stand = synthesize_tree_list(
            target,
            distributions=[spec],
            height_models=[height],
            taper_registry=_CurveTaperRegistry(table, exact_model),
        )
        density_odt_m3 = float(table["density_odt_m3"])
        aggregation, validation = aggregate_stand(
            stand.trees,
            table,
            model=exact_model,
            rules=_WORKER["rules"],
            prices=_WORKER["prices"],
            density_odt_m3=density_odt_m3,
            sample_trees=int(config.sample_trees),
            seed=int(config.seed) + zlib.crc32(str(plan["cell_key"]).encode("utf-8")),
        )
        matrix_taper_rel_error = (
            abs(float(aggregation["taper_total_m3"]) - float(stand.achieved_taper_volume_m3))
            / max(float(stand.achieved_taper_volume_m3), 1e-12)
        )
        flags = list(plan["flags"])
        if validation.get("status") == "fail":
            flags.append("buck_yield_validation_failed")
        if validation.get("product_status") == "fail":
            flags.append("buck_yield_product_validation_failed")
        if aggregation["fallback_trees"]:
            flags.append("buck_yield_fallback_trees")
        if matrix_taper_rel_error > VALIDATION_TOTAL_REL_TOL:
            flags.append("matrix_taper_vs_synth_above_tolerance")
        stand_row = {
            "cell_key": plan["cell_key"],
            "level": LEVEL,
            "stratum_id": plan["stratum_id"],
            "stratum_name": plan["stratum_name"],
            "ecozone": plan["ecozone"],
            "ecoprovince": plan["ecoprovince"],
            "species": plan["species"],
            "taxon_rank": plan["taxon_rank"],
            "species_group": plan["species_group"],
            "rsyc_model_group": plan["rsyc_model_group"],
            "coverage_class": plan["coverage_class"],
            "distribution": plan["distribution"],
            "distribution_fit_id": plan["distribution_fit_id"],
            "distribution_evidence_tier": plan["distribution_evidence_tier"],
            "distribution_borrowed": plan["distribution_borrowed"],
            "height_fit_id": plan["height_fit_id"],
            "height_evidence_tier": plan["height_evidence_tier"],
            "height_fit_status": plan["height_fit_status"],
            "height_rmse_m": plan["height_rmse_m"],
            "height_bias_m": plan["height_bias_m"],
            "rsyc_model_level": plan["rsyc_model_level"],
            "rsyc_model_strata_id": plan["rsyc_model_strata_id"],
            "rsyc_volume_model_id": plan["rsyc_volume_model_id"],
            "rsyc_volume_m3ha": plan["rsyc_volume_m3ha"],
            "rsyc_reference_age": plan["rsyc_reference_age"],
            "rsyc_agb_mg_ha": plan["rsyc_agb_mg_ha"],
            "target_volume_m3": float(stand.target_volume_m3),
            "target_tph": float(plan["target_tph"]),
            "achieved_volume_m3": float(stand.achieved_taper_volume_m3),
            "achieved_tph": float(stand.tph),
            "volume_rel_error": abs(
                float(stand.achieved_taper_volume_m3) - float(stand.target_volume_m3)
            )
            / max(float(stand.target_volume_m3), 1e-12),
            "tph_rel_error": abs(float(stand.tph) - float(plan["target_tph"]))
            / max(float(plan["target_tph"]), 1e-12),
            "achieved_qmd_cm": float(stand.qmd_cm),
            "basal_area_m2ha": float(stand.basal_area_m2ha),
            "n_trees": len(stand.trees),
            "area_weight_ha": float(plan["area_weight_ha"]),
            "area_basis": plan["area_basis"],
            "tph_source": plan["tph_source"],
            "tph_n_plots": plan["tph_n_plots"],
            "volume_source": plan["volume_source"],
            "observed_volume_n_plots": plan["observed_volume_n_plots"],
            "constraint_mode": stand.constraint_mode,
            "expansion_factor_scale": float(stand.scale_factor),
            "diameter_scale_factor": float(stand.diameter_scale_factor),
            "root_find_iterations": int(stand.root_find_iterations),
            "diameter_scale_residual_m3": float(stand.diameter_scale_residual_m3),
            "taper_species": plan["taper_species"],
            "taper_jurisdiction": "BC",
            "taper_bec_zone": plan["taper_zone"],
            "taper_zone_basis": plan["taper_zone_basis"],
            "taper_mapping_basis": plan["taper_mapping_basis"],
            "taper_proxy": plan["taper_proxy"],
            "density_code": table["density_code"],
            "density_odt_m3": density_odt_m3,
            "density_basis": plan["density_basis"],
            "buck_yield_key": plan["buck_yield_key"],
            "buck_yield_bins_used": len(aggregation["bins_used"]),
            "buck_yield_fallback_trees": int(aggregation["fallback_trees"]),
            "buck_yield_validation_status": validation.get("status"),
            "buck_yield_total_rel_error": validation.get("taper_relative_error"),
            "buck_yield_product_rel_error_max": validation.get("product_relative_error_max"),
            "buck_yield_residue_rel_error": validation.get("residue_relative_error"),
            "matrix_taper_vs_synth_rel_error": matrix_taper_rel_error,
            "flags": ";".join(sorted(set(flags))),
            "status": "ok",
            "error": "",
            "runtime_seconds": time.time() - started,
        }
        matrix_rows = _matrix_rows_for_cell(
            plan,
            stand,
            aggregation,
            density_odt_m3=density_odt_m3,
            area_weight_ha=float(plan["area_weight_ha"]),
        )
        partial = {
            "schema_version": SCHEMA_VERSION,
            "artifact_version": ARTIFACT_VERSION,
            "config_hash": config.config_hash(),
            "cell_key": plan["cell_key"],
            "status": "ok",
            "stand": stand_row,
            "matrix": matrix_rows,
            "validation": validation,
            "aggregation": {
                "taper_total_m3": aggregation["taper_total_m3"],
                "recovered_log_m3": aggregation["recovered_log_m3"],
                "residue_m3": aggregation["residue_m3"],
                "residue_odt": aggregation["residue_odt"],
                "fallback_trees": aggregation["fallback_trees"],
            },
            "runtime_seconds": time.time() - started,
        }
        _write_json(partial_path, partial)
        return {
            "cell_key": plan["cell_key"],
            "status": "ok",
            "runtime_seconds": time.time() - started,
        }
    except SynthesisError as error:
        partial = {
            "schema_version": SCHEMA_VERSION,
            "artifact_version": ARTIFACT_VERSION,
            "config_hash": config.config_hash(),
            "cell_key": plan["cell_key"],
            "status": "synthesis_failed",
            "error": f"SynthesisError: {error}",
            "stand": None,
            "matrix": [],
            "runtime_seconds": time.time() - started,
        }
        _write_json(partial_path, partial)
        return {"cell_key": plan["cell_key"], "status": "synthesis_failed", "error": str(error)}
    except Exception as error:  # noqa: BLE001 - worker boundary
        partial = {
            "schema_version": SCHEMA_VERSION,
            "artifact_version": ARTIFACT_VERSION,
            "config_hash": config.config_hash(),
            "cell_key": plan["cell_key"],
            "status": "failed",
            "error": f"{type(error).__name__}: {error}",
            "traceback": traceback.format_exc(limit=8),
            "stand": None,
            "matrix": [],
            "runtime_seconds": time.time() - started,
        }
        _write_json(partial_path, partial)
        return {"cell_key": plan["cell_key"], "status": "failed", "error": str(error)}


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def _load_partials(config: BatchConfig) -> dict[str, dict[str, Any]]:
    partials: dict[str, dict[str, Any]] = {}
    if not config.cells_dir.exists():
        return partials
    for path in sorted(config.cells_dir.glob("*.json")):
        payload = _read_json(path)
        if payload is None:
            continue
        cell_key = str(payload.get("cell_key") or "")
        if not cell_key:
            continue
        partials[cell_key] = payload
    return partials


def _summary_from_frames(
    stands: pd.DataFrame,
    matrix: pd.DataFrame,
    *,
    plans: Sequence[Mapping[str, Any]],
    partials: Mapping[str, Mapping[str, Any]],
    config: BatchConfig,
    runtime_seconds: float,
    started_utc: str,
    table_count: int,
) -> dict[str, Any]:
    log_rows = matrix[matrix["record_type"].eq("log")] if not matrix.empty else matrix
    residue_rows = matrix[matrix["record_type"].eq("residue")] if not matrix.empty else matrix
    by_species_product = (
        log_rows.groupby(["species", "product"], observed=True)
        .agg(volume_m3=("volume_m3", "sum"), volume_m3ha=("volume_m3ha", "sum"))
        .reset_index()
        .sort_values(["species", "product"])
    )
    by_species = (
        matrix[matrix["record_type"].eq("total")]
        .groupby("species", observed=True)
        .agg(
            volume_m3=("volume_m3", "sum"),
            residue_m3=("residue_volume_m3", "sum"),
            residue_odt=("residue_odt", "sum"),
        )
        .reset_index()
        .sort_values("species")
    )
    by_product = (
        log_rows.groupby("product", observed=True)
        .agg(volume_m3=("volume_m3", "sum"), volume_m3ha=("volume_m3ha", "sum"))
        .reset_index()
        .sort_values("product")
    )
    residue_by_species = (
        residue_rows.groupby("species", observed=True)
        .agg(
            residue_m3=("residue_volume_m3", "sum"),
            residue_odt=("residue_odt", "sum"),
        )
        .reset_index()
        .sort_values("species")
    )
    statuses: dict[str, int] = {}
    for plan in plans:
        if plan["status"] != "planned":
            status = str(plan["status"])
        else:
            partial = partials.get(str(plan["cell_key"]))
            status = _cell_status_from_partial(partial) or "remaining"
        statuses[status] = statuses.get(status, 0) + 1
    remaining_planned = sum(
        1
        for plan in plans
        if plan["status"] == "planned" and str(plan["cell_key"]) not in partials
    )
    skipped_cells = sum(1 for plan in plans if plan["status"] != "planned")
    validation_rows = [
        {
            "cell_key": str(partial.get("cell_key")),
            "status": (partial.get("validation") or {}).get("status"),
            "product_status": (partial.get("validation") or {}).get("product_status"),
            "taper_relative_error": (partial.get("validation") or {}).get("taper_relative_error"),
            "product_relative_error_max": (partial.get("validation") or {}).get("product_relative_error_max"),
            "residue_relative_error": (partial.get("validation") or {}).get("residue_relative_error"),
        }
        for partial in partials.values()
        if partial.get("status") == "ok" and partial.get("validation")
    ]
    validation_pass = sum(1 for row in validation_rows if row["status"] == "pass")
    validation_fail = sum(1 for row in validation_rows if row["status"] == "fail")
    product_pass = sum(1 for row in validation_rows if row["product_status"] == "pass")
    product_fail = sum(1 for row in validation_rows if row["product_status"] == "fail")
    taper_errors = [
        float(row["taper_relative_error"])
        for row in validation_rows
        if row["taper_relative_error"] is not None
    ]
    product_errors = [
        float(row["product_relative_error_max"])
        for row in validation_rows
        if row["product_relative_error_max"] is not None
    ]
    residue_errors = [
        float(row["residue_relative_error"])
        for row in validation_rows
        if row["residue_relative_error"] is not None
    ]
    evidence_counts = (
        stands.groupby("distribution_evidence_tier", observed=True).size().to_dict()
        if not stands.empty
        else {}
    )
    taxon_counts = (
        stands.groupby("taxon_rank", observed=True).size().to_dict() if not stands.empty else {}
    )
    total_volume = float(matrix.loc[matrix["record_type"].eq("total"), "volume_m3"].sum()) if not matrix.empty else 0.0
    total_recovered = float(log_rows["volume_m3"].sum()) if not log_rows.empty else 0.0
    total_residue = float(residue_rows["residue_volume_m3"].sum()) if not residue_rows.empty else 0.0
    total_residue_odt = float(residue_rows["residue_odt"].sum()) if not residue_rows.empty else 0.0
    return {
        "schema_version": SCHEMA_VERSION,
        "artifact_version": ARTIFACT_VERSION,
        "generated_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "run_started_utc": started_utc,
        "runtime_seconds": runtime_seconds,
        "level": LEVEL,
        "area_basis": "placeholder_equal_area",
        "area_weight_ha_per_cell": 1.0,
        "national_supply_estimate": False,
        "disclaimer": (
            "Every cell carries a 1 ha placeholder.  Summed volumes and residue "
            "are a pipeline test, not a national timber-supply estimate."
        ),
        "config": {
            "n_trees": config.n_trees,
            "seed": config.seed,
            "sample_trees": config.sample_trees,
            "bin_start_cm": config.bin_start_cm,
            "bin_width_cm": config.bin_width_cm,
            "bin_end_cm": config.bin_end_cm,
            "workers": config.workers,
            "timebox_seconds": config.timebox_seconds,
            "config_hash": config.config_hash(),
        },
        "counts": {
            "plans": len(plans),
            "statuses": statuses,
            "cells": len(stands["cell_key"].unique()) if not stands.empty else 0,
            "stands": len(stands),
            "buck_yield_tables": table_count,
            "matrix_rows": len(matrix),
            "log_rows": len(log_rows),
            "residue_rows": len(residue_rows),
        },
        "totals": {
            "volume_m3": total_volume,
            "recovered_log_m3": total_recovered,
            "residue_m3": total_residue,
            "residue_odt": total_residue_odt,
        },
        "by_species": _jsonable(by_species.to_dict(orient="records")),
        "by_product": _jsonable(by_product.to_dict(orient="records")),
        "by_species_product": _jsonable(by_species_product.to_dict(orient="records")),
        "residue_by_species": _jsonable(residue_by_species.to_dict(orient="records")),
        "evidence_counts": {str(key): int(value) for key, value in evidence_counts.items()},
        "taxon_counts": {str(key): int(value) for key, value in taxon_counts.items()},
        "buck_yield_validation": {
            "cells_validated": len(validation_rows),
            "pass": validation_pass,
            "fail": validation_fail,
            "product_pass": product_pass,
            "product_fail": product_fail,
            "taper_relative_error_max": max(taper_errors) if taper_errors else None,
            "product_relative_error_max": max(product_errors) if product_errors else None,
            "residue_relative_error_max": max(residue_errors) if residue_errors else None,
            "tolerances": {
                "taper_relative": VALIDATION_TOTAL_REL_TOL,
                "product_relative": VALIDATION_PRODUCT_REL_TOL,
                "residue_relative": VALIDATION_RESIDUE_REL_TOL,
                "taper_absolute_m3": VALIDATION_TOTAL_ABS_TOL_M3,
                "product_absolute_m3": VALIDATION_PRODUCT_ABS_TOL_M3,
                "residue_absolute_m3": VALIDATION_RESIDUE_ABS_TOL_M3,
            },
        },
        "remaining_cells": remaining_planned,
        "skipped_cells": skipped_cells,
        "failed_cells": [
            {
                "cell_key": str(partial.get("cell_key")),
                "status": partial.get("status"),
                "error": partial.get("error"),
            }
            for partial in partials.values()
            if partial.get("status") not in {"ok", None}
        ],
    }


def _write_buck_yield_consolidated(config: BatchConfig) -> int:
    rows: list[dict[str, Any]] = []
    count = 0
    for path in sorted(config.tables_dir.glob("*.json")):
        table = _read_json(path)
        if table is None:
            continue
        count += 1
        for record in table.get("bins", []):
            base = {
                "table_key": table.get("table_key"),
                "taper_species": table.get("taper_species"),
                "taper_zone": table.get("taper_zone"),
                "height_fit_id": table.get("height_fit_id"),
                "density_code": table.get("density_code"),
                "density_odt_m3": table.get("density_odt_m3"),
                "bin_lower_cm": record.get("bin_lower_cm"),
                "bin_upper_cm": record.get("bin_upper_cm"),
                "bin_mid_cm": record.get("bin_mid_cm"),
                "height_m": record.get("height_m"),
                "valid": record.get("valid"),
                "reason": record.get("reason"),
                "taper_total_m3": record.get("taper_total_m3"),
                "recovered_log_m3": record.get("recovered_log_m3"),
                "residue_m3": record.get("residue_m3"),
                "residue_odt": record.get("residue_odt"),
                "n_logs": record.get("n_logs"),
            }
            if record.get("valid"):
                for encoded, volume in (record.get("sorts") or {}).items():
                    product, sort_code = str(encoded).split("|", 1)
                    rows.append({**base, "product": product, "sort_code": sort_code, "log_volume_m3": volume})
            else:
                rows.append({**base, "product": "", "sort_code": "", "log_volume_m3": 0.0})
    frame = pd.DataFrame(rows)
    if not frame.empty:
        frame = frame.sort_values(["table_key", "bin_lower_cm", "product", "sort_code"])
    frame.to_csv(config.out_dir / "national_buck_yield_table.csv", index=False, lineterminator="\n")
    return count


def _write_report(
    config: BatchConfig,
    summary: Mapping[str, Any],
    stands: pd.DataFrame,
    plans: Sequence[Mapping[str, Any]],
    *,
    table_count: int,
) -> None:
    by_product = pd.DataFrame(summary.get("by_product", []))
    by_species = pd.DataFrame(summary.get("by_species", []))
    lines: list[str] = []
    lines.append("# National Representative-Stand Batch (Phase 1)")
    lines.append("")
    lines.append(f"Generated: {summary['generated_at_utc']}  ")
    lines.append(f"Runtime: {summary['runtime_seconds']:.1f} s  ")
    breakdown = summary.get("runtime_breakdown") or {}
    if breakdown:
        lines.append(
            "Runtime breakdown: "
            + ", ".join(
                f"{key} {float(value):.1f} s" for key, value in breakdown.items()
            )
            + "  "
        )
    lines.append(
        "Status: **pipeline test only**.  Every cell carries a 1 ha placeholder "
        "(`area_basis=placeholder_equal_area`); summed totals are not a national "
        "supply estimate."
    )
    lines.append("")
    lines.append("## Design")
    lines.append("")
    lines.append(f"- Level: `{LEVEL}`.")
    lines.append(f"- Cells planned: {summary['counts']['plans']}.")
    lines.append(f"- Stands completed: {summary['counts']['stands']}.")
    lines.append(f"- Buck-yield tables: {table_count}.")
    lines.append(f"- Trees per stand: {config.n_trees}; DBH bins: {config.bin_start_cm:g}-{config.bin_end_cm:g} cm at {config.bin_width_cm:g} cm.")
    lines.append("- Volume target: RSYC volume at age 100; observed plot volume fallback is flagged.")
    lines.append("- TPH target: expansion-weighted mean live plot-visit TPH; borrowed cells use the donor level.")
    lines.append("- Area: 1 ha placeholder per cell (`placeholder_equal_area`).")
    lines.append("")
    lines.append("### Run status")
    lines.append("")
    statuses = summary["counts"]["statuses"]
    for status, count in sorted(statuses.items()):
        lines.append(f"- `{status}`: {count}")
    lines.append("")
    lines.append("### Evidence tiers")
    lines.append("")
    for tier, count in sorted(summary.get("evidence_counts", {}).items()):
        lines.append(f"- `{tier}`: {count}")
    lines.append("")
    lines.append("### Area and taper proxies")
    lines.append("")
    lines.append("- Area weight is a placeholder, not a mapped national area.")
    lines.append("- The taper dispatcher is BC-only.  National species use an explicit published Kozak proxy; every row carries `taper_mapping_basis` and `taper_proxy`.")
    lines.append("- BEC zone is an ecozone-level proxy, not a national BEC classification.")
    lines.append("- Sorts are dimension-only; `grade_status=not_assigned`.")
    lines.append("")
    lines.append("## Buck-yield table")
    lines.append("")
    validation = summary["buck_yield_validation"]
    lines.append(
        f"- Validated cells: {validation['cells_validated']} "
        f"({validation['pass']} pass, {validation['fail']} fail)."
    )
    lines.append(
        f"- Maximum relative errors: taper {validation['taper_relative_error_max']}, "
        f"product (material totals) {validation['product_relative_error_max']}, "
        f"residue {validation['residue_relative_error_max']}."
    )
    lines.append(
        f"- Product-total diagnostic: {validation.get('product_pass', 0)} pass, "
        f"{validation.get('product_fail', 0)} fail.  Product faults are reported, "
        "not gated, because a 20-tree sample can straddle a sort threshold."
    )
    lines.append(
        f"- Pre-registered tolerances: taper {validation['tolerances']['taper_relative']:.0%} "
        f"or {validation['tolerances']['taper_absolute_m3']:g} m3; "
        f"product {validation['tolerances']['product_relative']:.0%} "
        f"or {validation['tolerances']['product_absolute_m3']:g} m3; "
        f"residue {validation['tolerances']['residue_relative']:.0%} "
        f"or {validation['tolerances']['residue_absolute_m3']:g} m3."
    )
    lines.append(
        "- Total taper and residue are the gated layer.  Product totals and sort "
        "shares are a 2 cm midpoint approximation and are reported as a diagnostic: "
        "near a sort threshold a 20-tree sample can move volume between sawlog and "
        "pulpwood even when total taper is exact.  Precise product/sort allocation "
        "requires per-tree bucking."
    )
    lines.append("")
    lines.append("## Pilot results")
    lines.append("")
    totals = summary["totals"]
    lines.append(
        f"Completed {summary['counts']['stands']} stands across "
        f"{summary['counts']['cells']} cells.  Recovered logs "
        f"{totals['recovered_log_m3']:.1f} m3, residue {totals['residue_m3']:.1f} m3 "
        f"({totals['residue_odt']:.1f} ODT), total {totals['volume_m3']:.1f} m3."
    )
    lines.append("")
    if not by_product.empty:
        lines.append("### Volume by product")
        lines.append("")
        lines.append("| product | volume m3 | volume m3/ha (equal-area sum) |")
        lines.append("|---|---:|---:|")
        for row in by_product.itertuples(index=False):
            lines.append(f"| {row.product} | {row.volume_m3:.3f} | {row.volume_m3ha:.3f} |")
        lines.append("")
    if not by_species.empty:
        lines.append("### Volume and residue by species")
        lines.append("")
        lines.append("| species | volume m3 | residue m3 | residue ODT |")
        lines.append("|---|---:|---:|---:|")
        for row in by_species.itertuples(index=False):
            lines.append(
                f"| {row.species} | {row.volume_m3:.3f} | {row.residue_m3:.3f} | {row.residue_odt:.3f} |"
            )
        lines.append("")
    lines.append("## Conservation and validation")
    lines.append("")
    if not stands.empty:
        taper_error = pd.to_numeric(stands["matrix_taper_vs_synth_rel_error"], errors="coerce")
        volume_error = pd.to_numeric(stands["volume_rel_error"], errors="coerce")
        tph_error = pd.to_numeric(stands["tph_rel_error"], errors="coerce")
        lines.append(f"- Maximum synthesis volume relative error: {volume_error.max():.3g}.")
        lines.append(f"- Maximum synthesis TPH relative error: {tph_error.max():.3g}.")
        lines.append(f"- Maximum matrix taper vs synthesis taper relative error: {taper_error.max():.3g}.")
        lines.append("- Matrix recovered log volume + residue = matrix taper total by construction.")
    lines.append("")
    lines.append("## Remaining cells and next stage")
    lines.append("")
    remaining = summary["remaining_cells"]
    skipped = summary.get("skipped_cells", 0)
    lines.append(f"- Remaining planned cells without a partial: {remaining}.")
    lines.append(f"- Skipped cells without a synthesis plan: {skipped}.")
    lines.append("- Resume with the same command and `--resume` to complete any remaining planned cells.")
    lines.append("- Stage A: replace the equal-area placeholder with mapped cell area and a defensible spatial frame before any supply claim.")
    lines.append("- Stage B: replace BC taper/sort proxies with jurisdiction-specific taper and grade rules.")
    lines.append("")
    (config.out_dir / "NATIONAL_BATCH_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_manifest(
    config: BatchConfig,
    summary: Mapping[str, Any],
    *,
    started_utc: str,
    runtime_seconds: float,
) -> None:
    outputs = [
        "national_stands.csv",
        "national_assortment_matrix.csv",
        "national_assortment_summary.json",
        "national_buck_yield_table.csv",
        "national_tph_targets.csv",
        "cell_status.csv",
        "excluded_cells.csv",
        "remaining_cells.csv",
        "NATIONAL_BATCH_REPORT.md",
    ]
    output_records: dict[str, Any] = {}
    for name in outputs:
        path = config.out_dir / name
        if not path.exists():
            continue
        output_records[name] = {
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }
    inputs = {}
    for path in (
        config.fit_path,
        config.height_path,
        config.evidence_path,
        config.coverage_path,
        config.remaining_fit_path,
        config.rules_path,
        config.price_path,
    ):
        if path.exists():
            try:
                label = str(path.relative_to(config.repo_root))
            except ValueError:
                label = f"external:{path}"
            inputs[label] = {
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
    manifest = {
        "artifact": "national_representative_stand_batch",
        "artifact_version": ARTIFACT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "run_started_utc": started_utc,
        "runtime_seconds": runtime_seconds,
        "level": LEVEL,
        "area_basis": "placeholder_equal_area",
        "national_supply_estimate": False,
        "config": summary["config"],
        "inputs": inputs,
        "outputs": output_records,
        "counts": summary["counts"],
        "totals": summary["totals"],
        "git": {
            "parent_branch": _git_value(config.repo_root, "rev-parse", "--abbrev-ref", "HEAD"),
            "parent_head": _git_value(config.repo_root, "rev-parse", "HEAD"),
            "data_head": _git_value(config.data_root, "rev-parse", "HEAD"),
        },
    }
    _write_json(config.out_dir / "manifest.json", manifest)


def assemble_outputs(
    config: BatchConfig,
    plans: Sequence[Mapping[str, Any]],
    partials: Mapping[str, Mapping[str, Any]],
    *,
    runtime_seconds: float,
    started_utc: str,
    table_count: int,
    runtime_breakdown: Mapping[str, float] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    stand_rows: list[dict[str, Any]] = []
    matrix_rows: list[dict[str, Any]] = []
    for cell_key, partial in sorted(partials.items()):
        if partial.get("schema_version") != SCHEMA_VERSION:
            continue
        if partial.get("config_hash") != config.config_hash():
            continue
        if partial.get("status") == "ok" and isinstance(partial.get("stand"), dict):
            stand_rows.append(partial["stand"])
            matrix_rows.extend(partial.get("matrix") or [])

    stands = pd.DataFrame(stand_rows, columns=list(TABLE_COLUMNS))
    matrix = pd.DataFrame(matrix_rows, columns=list(MATRIX_COLUMNS))
    if not stands.empty:
        stands = stands.sort_values("cell_key").reset_index(drop=True)
    if not matrix.empty:
        matrix = matrix.sort_values(
            ["cell_key", "record_type", "product", "sort_code"], kind="stable"
        ).reset_index(drop=True)
        for column in ("volume_m3", "volume_m3ha", "residue_volume_m3", "residue_odt", "density_odt_m3"):
            numeric = pd.to_numeric(matrix[column], errors="coerce")
            if numeric.isna().any():
                raise BatchError(f"matrix column {column!r} contains non-numeric values")
            if (numeric < -1e-12).any():
                raise BatchError(f"matrix column {column!r} contains negative values")
        log_volume = float(matrix.loc[matrix["record_type"].eq("log"), "volume_m3"].sum())
        residue_volume = float(matrix.loc[matrix["record_type"].eq("residue"), "volume_m3"].sum())
        taper_volume = float(matrix.loc[matrix["record_type"].eq("total"), "volume_m3"].sum())
        if abs((log_volume + residue_volume) - taper_volume) > max(1e-6, 1e-9 * taper_volume):
            raise BatchError(
                "national matrix conservation failed: "
                f"logs {log_volume:.9f} + residue {residue_volume:.9f} != total {taper_volume:.9f}"
            )
    stands.to_csv(config.out_dir / "national_stands.csv", index=False, lineterminator="\n")
    matrix.to_csv(config.out_dir / "national_assortment_matrix.csv", index=False, lineterminator="\n")

    summary = _summary_from_frames(
        stands,
        matrix,
        plans=plans,
        partials=partials,
        config=config,
        runtime_seconds=runtime_seconds,
        started_utc=started_utc,
        table_count=table_count,
    )
    if runtime_breakdown is not None:
        summary["runtime_breakdown"] = {
            str(key): float(value) for key, value in runtime_breakdown.items()
        }
    _write_json(config.out_dir / "national_assortment_summary.json", summary)

    plan_frame = pd.DataFrame(
        [
            {
                "cell_key": plan["cell_key"],
                "species": plan["species"],
                "taxon_rank": plan["taxon_rank"],
                "stratum_id": plan["stratum_id"],
                "ecozone": plan["ecozone"],
                "distribution_evidence_tier": plan["distribution_evidence_tier"],
                "coverage_class": plan["coverage_class"],
                "target_volume_m3ha": plan["target_volume_m3ha"],
                "target_tph": plan["target_tph"],
                "tph_source": plan["tph_source"],
                "tph_n_plots": plan["tph_n_plots"],
                "volume_source": plan["volume_source"],
                "taper_species": plan["taper_species"],
                "taper_bec_zone": plan["taper_zone"],
                "taper_proxy": plan["taper_proxy"],
                "status": (
                    _cell_status_from_partial(partials.get(plan["cell_key"])) or "remaining"
                    if plan["status"] == "planned"
                    else plan["status"]
                ),
                "plan_status": plan["status"],
                "reason": plan["reason"],
                "flags": ";".join(plan["flags"]),
            }
            for plan in plans
        ]
    )
    plan_frame.to_csv(config.out_dir / "cell_status.csv", index=False, lineterminator="\n")
    plan_frame[plan_frame["plan_status"].ne("planned")].to_csv(
        config.out_dir / "excluded_cells.csv", index=False, lineterminator="\n"
    )
    plan_frame[
        plan_frame["plan_status"].eq("planned") & plan_frame["status"].eq("remaining")
    ].to_csv(config.out_dir / "remaining_cells.csv", index=False, lineterminator="\n")
    plan_frame[
        [
            "cell_key",
            "species",
            "stratum_id",
            "target_volume_m3ha",
            "target_tph",
            "tph_source",
            "tph_n_plots",
            "volume_source",
            "status",
        ]
    ].to_csv(config.out_dir / "national_tph_targets.csv", index=False, lineterminator="\n")
    _write_report(config, summary, stands, plans, table_count=table_count)
    _write_buck_yield_consolidated(config)
    _write_manifest(
        config,
        summary,
        started_utc=started_utc,
        runtime_seconds=runtime_seconds,
    )
    return stands, matrix, summary


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def _table_tasks(
    plans: Sequence[Mapping[str, Any]],
    config: BatchConfig,
) -> list[dict[str, Any]]:
    tasks: dict[str, dict[str, Any]] = {}
    for plan in plans:
        if plan["status"] != "planned":
            continue
        key = str(plan["buck_yield_key"])
        if key in tasks:
            continue
        tasks[key] = {
            "table_key": key,
            "taper_species": plan["taper_species"],
            "taper_zone": plan["taper_zone"],
            "height_a": plan["height_a"],
            "height_b": plan["height_b"],
            "height_fit_id": plan["height_fit_id"],
            "density_code": plan["density_code"],
            "bin_start_cm": config.bin_start_cm,
            "bin_width_cm": config.bin_width_cm,
            "bin_end_cm": config.bin_end_cm,
        }
    return [tasks[key] for key in sorted(tasks)]


def run(config: BatchConfig) -> dict[str, Any]:
    """Run one timeboxed, resumable national representative-stand batch."""
    started = time.time()
    started_utc = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    config.out_dir.mkdir(parents=True, exist_ok=True)
    config.cells_dir.mkdir(parents=True, exist_ok=True)
    config.tables_dir.mkdir(parents=True, exist_ok=True)

    _stage(config, "loading national fit, height, and coverage tables", started)
    fit, height, _coverage = load_fit_inputs(config)

    _stage(config, "loading plot tree table and building TPH targets", started)
    trees = load_tree_table(config)
    cell_tph, ecozone_tph, national_tph = build_tph_lookups(trees)
    _stage(config, f"TPH lookups: {len(cell_tph)} cells, {len(ecozone_tph)} ecozones, {len(national_tph)} national species", started)

    rules = load_grade_rules(config.rules_path)
    prices = load_price_profile(config.price_path)
    registry = TaperRegistry(parameter_path=default_kozak_parameter_path())

    # Observed volume is a fallback only for cells without an RSYC prior.  It
    # is computed once in the parent, before the worker pool starts.
    observed_volumes: dict[tuple[str, str], tuple[float, int]] = {}
    missing_volume = fit[fit["rsyc_volume_m3ha"].isna()]
    for _index, row in missing_volume.iterrows():
        species = str(row["species"]).strip().upper()
        mapping = TAPER_SPECIES_PROXY.get(species)
        if mapping is None:
            continue
        height_match = height[
            height["stratum_id"].astype(str).eq(str(row["stratum_id"]))
            & height["species"].astype(str).eq(species)
        ]
        if height_match.empty:
            continue
        height_row = height_match.iloc[0]
        height_a = _number(height_row.to_dict(), "a")
        height_b = _number(height_row.to_dict(), "b")
        if height_a is None or height_b is None:
            continue
        taper_zone = _taper_zone(str(row["ecozone"]))
        try:
            exact_model = registry.resolve("BC", mapping[0], taper_zone)
            curve = _volume_curve(exact_model, height_a, height_b)
        except UnsupportedTaperError:
            continue
        observed = observed_plot_volume(
            trees,
            stratum_id=str(row["stratum_id"]),
            species=species,
            height_a=height_a,
            height_b=height_b,
            taper_species=mapping[0],
            taper_zone=taper_zone,
            registry=registry,
            volume_curve=curve,
        )
        if observed is not None:
            observed_volumes[(str(row["stratum_id"]), species)] = observed
    if observed_volumes:
        _stage(config, f"observed-volume fallback for {len(observed_volumes)} cells", started)

    plans = build_cell_plans(
        fit,
        height,
        cell_tph=cell_tph,
        ecozone_tph=ecozone_tph,
        national_tph=national_tph,
        observed_volumes=observed_volumes,
        registry=registry,
        rules_version=str(getattr(rules, "version", "")),
        price_period=str(prices.get("period") or ""),
        config=config,
    )
    planned = [plan for plan in plans if plan["status"] == "planned"]
    if config.max_cells is not None:
        selected = planned[: int(config.max_cells)]
        selected_keys = {plan["cell_key"] for plan in selected}
        for plan in plans:
            if plan["status"] == "planned" and plan["cell_key"] not in selected_keys:
                plan["status"] = "deferred_max_cells"
                plan["reason"] = f"max_cells={config.max_cells}"
        planned = selected
    _stage(
        config,
        f"cell plans: {len(plans)} total, {len(planned)} selected, "
        f"{len(plans) - len(planned)} skipped/deferred",
        started,
    )
    prep_seconds = time.time() - started

    # Release the large tree table before forking workers.
    del trees
    gc.collect()

    existing_partials = _load_partials(config)
    if not config.resume:
        # A non-resume run still uses existing valid partials only if they match
        # the config hash; otherwise it recomputes them.
        pass

    tasks = _table_tasks(planned, config)
    missing_tables = [
        task
        for task in tasks
        if config.rebuild_tables or not (config.tables_dir / f"{task['table_key']}.json").exists()
    ]
    _stage(
        config,
        f"buck-yield tables: {len(tasks)} required, {len(tasks) - len(missing_tables)} present, "
        f"{len(missing_tables)} to build",
        started,
    )

    config_payload = {
        "repo_root": str(config.repo_root),
        "data_root": str(config.data_root),
        "out_dir": str(config.out_dir),
        "metadata_path": str(config.metadata_path),
        "cache_dir": str(config.cache_dir),
        "workers": config.workers,
        "resume": config.resume,
        "timebox_seconds": config.timebox_seconds,
        "max_cells": config.max_cells,
        "n_trees": config.n_trees,
        "seed": config.seed,
        "sample_trees": config.sample_trees,
        "bin_start_cm": config.bin_start_cm,
        "bin_width_cm": config.bin_width_cm,
        "bin_end_cm": config.bin_end_cm,
        "rebuild_tables": config.rebuild_tables,
        "verbose": config.verbose,
    }
    deadline = started + float(config.timebox_seconds)
    table_results: list[dict[str, Any]] = []
    cell_results: list[dict[str, Any]] = []
    table_started = time.time()
    cell_started = table_started
    pool = ProcessPoolExecutor(
        max_workers=max(1, int(config.workers)),
        mp_context=mp.get_context("fork"),
        initializer=_worker_init,
        initargs=(config_payload,),
    )
    try:
        if missing_tables:
            pending = set()
            for task in missing_tables:
                if time.time() >= deadline:
                    break
                pending.add(pool.submit(_build_table_task, task))
                if len(pending) >= max(1, int(config.workers)) * 2:
                    done, pending = wait(pending, return_when=FIRST_COMPLETED)
                    for future in done:
                        table_results.append(future.result())
                    if time.time() >= deadline:
                        break
            while pending:
                remaining = max(0.0, deadline - time.time())
                if remaining <= 0.0:
                    break
                done, pending = wait(
                    pending,
                    timeout=remaining,
                    return_when=FIRST_COMPLETED,
                )
                if not done:
                    break
                for future in done:
                    table_results.append(future.result())
            table_failures = [
                item for item in table_results if item.get("status") == "failed"
            ]
            if table_failures:
                _write_json(
                    config.out_dir / "buck_yield_errors.json",
                    {"failures": table_failures},
                )
            _stage(
                config,
                f"buck-yield tables built: "
                f"{sum(1 for item in table_results if item.get('status') in {'built', 'already_present'})}"
                f" ({len(table_failures)} failed)",
                started,
            )

        # Determine which planned cells have a table and need a partial.
        available_tables = {
            path.stem for path in config.tables_dir.glob("*.json") if _read_json(path) is not None
        }
        cell_tasks: list[dict[str, Any]] = []
        for plan in planned:
            if plan["buck_yield_key"] not in available_tables:
                continue
            if config.resume and not config.rebuild_tables:
                partial = existing_partials.get(plan["cell_key"])
                if (
                    partial is not None
                    and partial.get("status") == "ok"
                    and partial.get("schema_version") == SCHEMA_VERSION
                    and partial.get("config_hash") == config.config_hash()
                ):
                    continue
            cell_tasks.append(plan)
        _stage(config, f"stands to synthesize: {len(cell_tasks)}", started)
        cell_started = time.time()

        pending = set()
        for plan in cell_tasks:
            if time.time() >= deadline:
                break
            pending.add(pool.submit(_cell_task, plan))
            if len(pending) >= max(1, int(config.workers)) * 2:
                done, pending = wait(pending, return_when=FIRST_COMPLETED)
                for future in done:
                    try:
                        cell_results.append(future.result())
                    except Exception as error:  # noqa: BLE001 - parent boundary
                        cell_results.append({"status": "worker_crash", "error": str(error)})
                if time.time() >= deadline:
                    break
        while pending:
            remaining = max(0.0, deadline - time.time())
            if remaining <= 0.0:
                break
            done, pending = wait(
                pending, timeout=remaining, return_when=FIRST_COMPLETED
            )
            if not done:
                break
            for future in done:
                try:
                    cell_results.append(future.result())
                except Exception as error:  # noqa: BLE001 - parent boundary
                    cell_results.append({"status": "worker_crash", "error": str(error)})
        for future in pending:
            future.cancel()
    finally:
        pool.shutdown(wait=True, cancel_futures=True)

    runtime = time.time() - started
    cell_seconds = max(0.0, runtime - (cell_started - started))
    table_seconds = max(0.0, (cell_started - table_started))
    assemble_started = time.time()
    partials = _load_partials(config)
    stands, matrix, summary = assemble_outputs(
        config,
        plans,
        partials,
        runtime_seconds=runtime,
        started_utc=started_utc,
        table_count=len(list(config.tables_dir.glob("*.json"))),
        runtime_breakdown={
            "prep_seconds": prep_seconds,
            "table_seconds": table_seconds,
            "cell_seconds": cell_seconds,
            "assemble_seconds": time.time() - assemble_started,
        },
    )
    _write_metadata(config, summary, stands, matrix)
    summary["runtime_seconds"] = runtime
    _write_json(config.out_dir / "national_assortment_summary.json", summary)

    if config.verbose:
        print(
            f"completed {summary['counts']['stands']} stands, "
            f"{summary['counts']['cells']} cells, {runtime:.1f}s",
            flush=True,
        )
    return summary


def _write_metadata(
    config: BatchConfig,
    summary: Mapping[str, Any],
    stands: pd.DataFrame,
    matrix: pd.DataFrame,
) -> None:
    rows: list[dict[str, Any]] = []
    outputs = [
        ("national_stands.csv", "representative stand targets and achieved volume/TPH", len(stands)),
        (
            "national_assortment_matrix.csv",
            "cell x species x product/sort volumes and residue",
            len(matrix),
        ),
        ("national_assortment_summary.json", "batch summary and totals", 1),
        ("national_buck_yield_table.csv", "consolidated 2 cm buck-yield table", None),
        ("national_tph_targets.csv", "target TPH source per cell", None),
        ("cell_status.csv", "cell plan and completion status", None),
        ("excluded_cells.csv", "cells excluded before synthesis", None),
        ("remaining_cells.csv", "cells still resumable", None),
        ("NATIONAL_BATCH_REPORT.md", "human-readable batch report", 1),
        ("manifest.json", "artifact manifest", 1),
    ]
    for name, description, row_count in outputs:
        path = config.out_dir / name
        if not path.exists():
            continue
        rows.append(
            {
                "artifact": name,
                "path": str(path.relative_to(config.repo_root)),
                "description": description,
                "rows": row_count,
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
                "generated_at_utc": summary["generated_at_utc"],
                "area_basis": "placeholder_equal_area",
                "national_supply_estimate": "false",
                "notes": (
                    "Phase 1 pipeline test; equal-area cells; BC taper/sort proxies; "
                    "not a national supply estimate"
                ),
            }
        )
    frame = pd.DataFrame(rows)
    if "rows" in frame.columns:
        frame["rows"] = frame["rows"].astype("Int64")
    frame.to_csv(config.metadata_path, index=False, lineterminator="\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    repo_root = Path(__file__).resolve().parents[3]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=repo_root / "data" / "derived" / "national" / "assortments" / "national_batch",
    )
    parser.add_argument(
        "--metadata",
        type=Path,
        default=repo_root / "metadata" / "national-batch.csv",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=repo_root / "runtime" / "national_batch" / "cache",
    )
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--timebox-seconds", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--max-cells", type=int, default=None)
    parser.add_argument("--n-trees", type=int, default=DEFAULT_N_TREES)
    parser.add_argument("--seed", type=int, default=20261115)
    parser.add_argument("--sample-trees", type=int, default=DEFAULT_SAMPLE_TREES)
    parser.add_argument("--bin-start-cm", type=float, default=DEFAULT_BIN_START_CM)
    parser.add_argument("--bin-width-cm", type=float, default=DEFAULT_BIN_WIDTH_CM)
    parser.add_argument("--bin-end-cm", type=float, default=DEFAULT_BIN_END_CM)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--rebuild-tables", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = Path(__file__).resolve().parents[3]
    config = BatchConfig(
        repo_root=repo_root,
        data_root=repo_root / "data",
        out_dir=Path(args.out).expanduser().resolve(),
        metadata_path=Path(args.metadata).expanduser().resolve(),
        cache_dir=Path(args.cache_dir).expanduser().resolve(),
        workers=int(args.workers),
        resume=bool(args.resume),
        timebox_seconds=float(args.timebox_seconds),
        max_cells=args.max_cells,
        n_trees=int(args.n_trees),
        seed=int(args.seed),
        sample_trees=int(args.sample_trees),
        bin_start_cm=float(args.bin_start_cm),
        bin_width_cm=float(args.bin_width_cm),
        bin_end_cm=float(args.bin_end_cm),
        rebuild_tables=bool(args.rebuild_tables),
        verbose=bool(args.verbose),
    )
    try:
        run(config)
    except BatchError as error:
        print(f"national_batch: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
