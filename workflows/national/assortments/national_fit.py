"""First national hierarchical DBH and height fit.

The module turns the national plot-to-RSYC coverage audit into fitted
distribution and height-diameter records.  It is deliberately conservative:

* A local fit is only labelled ``direct_fit`` or ``pooled_fit`` when the
  coverage audit assigned that cell to the same class and the candidate fit
  actually ran at that level.
* A ``no_data`` cell borrows the nearest ancestor shape (``pooled_parent``) or
  the national species shape (``species_national``).  A borrowed shape is never
  labelled as a local fit.
* A direct or pooled cell that fails to fit is left as ``no_fit``.  It is not
  silently replaced by a borrowed shape.
* The RSYC volume/AGB curve at the reference age is attached as a scale prior
  and its provenance is recorded separately from the shape fit.

The command line is:

```bash
python -m workflows.national.assortments.national_fit \
  --level ecoprovince \
  --out data/derived/national/assortments/national_fit \
  --workers 8 --resume --timebox-seconds 780
```

Cell-level JSON partials make a timeboxed run resumable.  The final CSV and
report are rebuilt from whatever partials exist, with unfinished direct/pooled
cells listed explicitly as remaining work.
"""

# The report body is a long markdown block; adjacent literals are intentional.
# ruff: noqa: ISC004, BLE001

from __future__ import annotations

import argparse
import hashlib
import json
import math
import multiprocessing as mp
import os
import subprocess
import sys
import time
import traceback
import zipfile
from collections.abc import Iterable, Mapping, Sequence
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

try:  # The fit estimators live in the sibling boundary modules.
    from .distributions import (
        DistributionFit,
        DistributionFitError,
        FitCandidate,
        fit_distribution_for_target,
    )
    from .national_coverage import (
        _read_ecostrat_layers,
        assert_no_unexpected_deletions,
        assign_sites_to_ecostrata,
        load_sites,
        map_species_code,
    )
except ModuleNotFoundError as error:  # pragma: no cover - environment guard
    if error.name == "nemora":
        raise ModuleNotFoundError(
            "national_fit requires the nemora package used by distributions.py; "
            "install the local nemora checkout or add its src directory to PYTHONPATH"
        ) from error
    raise

__all__ = [
    "ARTIFACT_VERSION",
    "FitConfig",
    "build_parser",
    "main",
    "run",
]

ARTIFACT_VERSION = "0.1.0"
SCHEMA_VERSION = 2
PREP_VERSION = 2

NATIONAL_ID = "Canada"
ECO_LEVELS: tuple[str, ...] = ("ecozone", "ecoprovince", "ecoregion", "ecodistrict")
LEVEL_ORDER: tuple[str, ...] = ("national",) + ECO_LEVELS

#: Support levels fitted before a target level so parent borrowing is explicit.
SUPPORT_LEVELS: dict[str, tuple[str, ...]] = {
    "national": ("national",),
    "ecozone": ("national", "ecozone"),
    "ecoprovince": ("national", "ecozone", "ecoprovince"),
    "ecoregion": ("national", "ecozone", "ecoprovince", "ecoregion"),
    "ecodistrict": ("national", "ecozone", "ecoprovince", "ecoregion", "ecodistrict"),
}

EVIDENCE_TIERS: tuple[str, ...] = (
    "direct_fit",
    "pooled_fit",
    "pooled_parent",
    "species_national",
    "no_fit",
)

#: Minimum support for a fit attempt.  Direct cells already cleared the
#: coverage threshold of 100 trees and five plots; the lower bound here only
#: protects against trees lost to the expansion-factor and modal-DBH-limit
#: filters.  Pooled cells use the coverage pooled band, so ten trees is the
#: documented floor.
MIN_DIRECT_TREES = 30
MIN_DIRECT_PLOTS = 5
MIN_POOLED_TREES = 10
MIN_POOLED_PLOTS = 2
MIN_HEIGHT_OBS = 30

BIN_WIDTH_CM = 2.0
RANDOM_STATE = 20261115
REFERENCE_AGE = 100.0

#: Candidate families for the national pass.  This is the task-specified set:
#: single Weibull/gamma, the two finite Weibull mixtures, and the constrained
#: spike families (the spike gate still decides whether they are attempted).
#: The legacy two-gamma fallback, generalized beta diagnostics, three-component
#: Weibull, and unconstrained spikes remain available to other callers but are
#: deliberately not attempted here; they are the slowest and least stable arms.
NATIONAL_FAMILIES: tuple[str, ...] = (
    "weibull",
    "gamma",
    "mix_weibull_2",
    "mix_weibull_gamma",
    "spike_weibull_constrained",
    "spike_gamma_constrained",
)

#: Per-source lower DBH limits used to keep plot protocols from being mixed.
#: These follow the harmonized source tallies and the TSA24 prototype:
#: FAIB PSP and MAGPlot use a 2 cm tally; FAIB non-PSP uses 4 cm; QC PEP uses
#: 9 cm; NL FAL retains small trees from 1 cm.  The modal limit is used inside
#: each cell, and trees from other protocol classes are excluded and reported.
DBH_LIMIT_BY_SOURCE: dict[str, float] = {
    "faib_psp": 2.0,
    "faib_non_psp": 4.0,
    "magplot": 2.0,
    "magplot_wfs": 2.0,
    "nl_fal": 1.0,
    "qc_pep": 9.0,
}
DEFAULT_DBH_LIMIT_CM = 2.0

TARGET_COLUMNS = (
    "level",
    "stratum_id",
    "stratum_name",
    "ecozone",
    "ecoprovince",
    "ecoregion",
    "ecodistrict",
    "species",
    "species_group",
    "rsyc_model_group",
    "coverage_class",
    "n_trees",
    "n_plots",
    "sources",
    "dbh_min_cm",
    "dbh_p50_cm",
    "dbh_max_cm",
)


class NationalFitError(RuntimeError):
    """Raised when a national fit cannot be completed without ambiguity."""


@dataclass(frozen=True)
class FitConfig:
    """Filesystem and runtime configuration for one national fit pass."""

    repo_root: Path
    data_root: Path
    out_dir: Path
    metadata_path: Path
    cache_dir: Path
    level: str
    workers: int = 8
    resume: bool = False
    timebox_seconds: float = 780.0
    max_cells: int | None = None
    prepare_only: bool = False
    rebuild_cache: bool = False
    verbose: bool = False
    random_state: int = RANDOM_STATE
    bin_width_cm: float = BIN_WIDTH_CM
    reference_age: float = REFERENCE_AGE

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
    def sites_path(self) -> Path:
        return self.data_root / "derived" / "fvsca_calibration" / "v0" / "sites.csv"

    @property
    def visits_path(self) -> Path:
        return self.data_root / "derived" / "fvsca_calibration" / "v0" / "visits.csv"

    @property
    def trees_path(self) -> Path:
        return self.data_root / "derived" / "fvsca_calibration" / "v0" / "trees.csv.gz"

    @property
    def ecostrat_path(self) -> Path:
        return self.data_root / "raw" / "national" / "rsyc" / "RSYC_ecostrat.gpkg"

    @property
    def models_path(self) -> Path:
        return self.data_root / "raw" / "national" / "rsyc" / "RSYC_models.zip"

    @property
    def support_levels(self) -> tuple[str, ...]:
        return SUPPORT_LEVELS[self.level]

    @property
    def cells_dir(self) -> Path:
        return self.out_dir / "cells"


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
    if value is pd.NA or value is pd.NaT:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def _clean_number(value: Any) -> float | None:
    """Return a finite float or None."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _display_path(path: Path, repo_root: Path) -> str:
    # Keep the logical repository path; resolving a git-annex symlink would
    # leak the internal annex object path into the manifest.
    try:
        return str(path.absolute().relative_to(repo_root.absolute()))
    except ValueError:
        return str(path.absolute())


def _git_value(repository: Path, *arguments: str) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def _file_fingerprint(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {"size": int(stat.st_size), "mtime_ns": int(stat.st_mtime_ns)}


def _stage(config: FitConfig, message: str, started: float) -> None:
    if config.verbose:
        print(f"[{time.time() - started:8.1f}s] {message}", file=sys.stderr, flush=True)


def _cell_key(level: str, stratum_id: str, species: str) -> str:
    return hashlib.sha1(
        f"{level}\x1f{stratum_id}\x1f{species}".encode()
    ).hexdigest()[:20]


def _fit_config_hash(config: FitConfig) -> str:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "prep_version": PREP_VERSION,
        "min_direct_trees": MIN_DIRECT_TREES,
        "min_direct_plots": MIN_DIRECT_PLOTS,
        "min_pooled_trees": MIN_POOLED_TREES,
        "min_pooled_plots": MIN_POOLED_PLOTS,
        "min_height_obs": MIN_HEIGHT_OBS,
        "bin_width_cm": config.bin_width_cm,
        "random_state": config.random_state,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


def load_coverage(config: FitConfig) -> pd.DataFrame:
    """Load the pre-registered coverage grid and normalise its identifiers."""
    path = config.coverage_path
    if not path.exists():
        raise FileNotFoundError(f"coverage audit not found: {path}")
    frame = pd.read_csv(path, low_memory=False, dtype={"stratum_id": str, "species": str})
    missing = sorted(set(TARGET_COLUMNS).difference(frame.columns))
    if missing:
        raise ValueError(f"coverage table missing columns: {', '.join(missing)}")
    frame = frame.loc[:, list(TARGET_COLUMNS)].copy()
    frame["level"] = frame["level"].astype(str).str.strip()
    frame["stratum_id"] = frame["stratum_id"].astype(str).str.strip()
    frame["species"] = frame["species"].astype(str).str.strip().str.upper()
    for column in ("stratum_name", "ecozone", "ecoprovince", "ecoregion", "ecodistrict"):
        frame[column] = frame[column].astype("string").fillna("")
    for column in ("species_group", "rsyc_model_group", "coverage_class", "sources"):
        frame[column] = frame[column].astype("string").fillna("")
    for column in ("n_trees", "n_plots"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(0).astype(int)
    for column in ("dbh_min_cm", "dbh_p50_cm", "dbh_max_cm"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    unknown = sorted(set(frame["level"]).difference(LEVEL_ORDER))
    if unknown:
        raise ValueError(f"coverage table has unknown levels: {unknown}")
    if frame.duplicated(["level", "stratum_id", "species"]).any():
        raise ValueError("coverage table has duplicate level/stratum/species keys")
    return frame.reset_index(drop=True)


def _parent_chain(level: str, stratum_id: str) -> list[tuple[str, str]]:
    """Return the nested ancestor identifiers for one cell, finest first."""
    parts = [part for part in str(stratum_id).split("/") if part]
    if level == "national":
        return []
    if level == "ecozone":
        return []
    if level == "ecoprovince":
        if len(parts) != 2:
            raise NationalFitError(f"malformed ecoprovince id: {stratum_id!r}")
        return [("ecozone", parts[0])]
    if level == "ecoregion":
        if len(parts) != 3:
            raise NationalFitError(f"malformed ecoregion id: {stratum_id!r}")
        return [
            ("ecoprovince", "/".join(parts[:2])),
            ("ecozone", parts[0]),
        ]
    if level == "ecodistrict":
        if len(parts) != 4:
            raise NationalFitError(f"malformed ecodistrict id: {stratum_id!r}")
        return [
            ("ecoregion", "/".join(parts[:3])),
            ("ecoprovince", "/".join(parts[:2])),
            ("ecozone", parts[0]),
        ]
    raise NationalFitError(f"unsupported level: {level!r}")


def _tree_cache_paths(config: FitConfig) -> tuple[Path, Path]:
    config.cache_dir.mkdir(parents=True, exist_ok=True)
    return (
        config.cache_dir / "national_fit_trees_v1.parquet",
        config.cache_dir / "national_fit_trees_v1.json",
    )


def _tree_cache_key(config: FitConfig) -> dict[str, Any]:
    return {
        "prep_version": PREP_VERSION,
        "inputs": {
            "sites": _file_fingerprint(config.sites_path),
            "visits": _file_fingerprint(config.visits_path),
            "trees": _file_fingerprint(config.trees_path),
            "ecostrat": _file_fingerprint(config.ecostrat_path),
        },
    }


def _plot_ids(trees: pd.DataFrame) -> pd.Series:
    """Return the physical plot unit used by the coverage audit."""
    source = trees["source"].astype(str)
    site = trees["site_id"].astype(str)
    visit = trees["visit_id"].astype(str)
    tree = trees["tree_id"].astype(str)
    faib = source.isin(["faib_psp", "faib_non_psp"])
    magplot = source.isin(["magplot", "magplot_wfs"])
    values = pd.Series(index=trees.index, dtype="object")
    values.loc[faib] = (
        source.loc[faib] + "|" + site.loc[faib] + "|" + tree.loc[faib].str.split("-", n=1).str[0]
    )
    values.loc[magplot] = (
        source.loc[magplot] + "|" + visit.loc[magplot].str.split("|", n=1).str[0]
    )
    remainder = ~(faib | magplot)
    values.loc[remainder] = source.loc[remainder] + "|" + site.loc[remainder]
    return values


def _attach_species(trees: pd.DataFrame) -> pd.DataFrame:
    """Map the observed source codes to RSYC species or model groups."""
    codes = trees[["source", "species_code", "species_group"]].drop_duplicates().copy()
    mapped = [
        map_species_code(source, code, group)
        for source, code, group in zip(
            codes["source"], codes["species_code"], codes["species_group"]
        )
    ]
    codes["species"] = [value[0] for value in mapped]
    codes["mapping_basis"] = [value[1] for value in mapped]
    codes["mapping_note"] = [value[2] for value in mapped]
    if (codes["mapping_basis"] == "unmapped").any():
        bad = codes[codes["mapping_basis"] == "unmapped"]
        detail = ", ".join(
            f"{row.source}:{row.species_code}" for row in bad.itertuples(index=False)
        )
        raise NationalFitError(f"unmapped source species codes: {detail}")
    trees = trees.merge(
        codes[["source", "species_code", "species_group", "species", "mapping_basis"]],
        on=["source", "species_code", "species_group"],
        how="left",
        validate="many_to_one",
    )
    # The coverage grid mixes uppercase scientific codes with lowercase model
    # groups.  Normalise the analysis table to the same uppercase keys used by
    # the candidate fitter.
    trees["species"] = trees["species"].astype(str).str.upper()
    return trees


def prepare_tree_table(config: FitConfig) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Load the harmonized plots once and cache a compact analysis table."""
    cache_path, metadata_path = _tree_cache_paths(config)
    cache_key = _tree_cache_key(config)
    if not config.rebuild_cache and cache_path.exists() and metadata_path.exists():
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if metadata.get("cache_key") == cache_key:
            trees = pd.read_parquet(cache_path)
            return trees, metadata
    started = time.time()
    _stage(config, "loading sites and RSYC ecostrata", started)
    sites = load_sites(config.sites_path)
    layers = _read_ecostrat_layers(config.ecostrat_path)
    sites, join_stats = assign_sites_to_ecostrata(sites, layers)
    sites["mapped"] = sites["mapped"].fillna(False).astype(bool)
    site_columns = [
        "site_id",
        "province",
        "mapped",
        "ecozone",
        "ecoprovince",
        "ecoregion",
        "ecodistrict",
        "ecozone_key",
        "ecoprovince_key",
        "ecoregion_key",
        "ecodistrict_key",
    ]
    site_frame = sites.loc[sites["mapped"], site_columns].set_index("site_id")

    _stage(config, "loading tree records", started)
    tree_columns = [
        "source",
        "site_id",
        "visit_id",
        "tree_id",
        "species_code",
        "species_group",
        "dbh_cm",
        "height_m",
        "status",
        "expansion_factor",
    ]
    trees = pd.read_csv(
        config.trees_path,
        usecols=tree_columns,
        low_memory=False,
        dtype={
            "source": str,
            "site_id": str,
            "visit_id": str,
            "tree_id": str,
            "species_code": str,
            "species_group": str,
            "status": str,
        },
    )
    trees["source"] = trees["source"].astype(str).str.strip().str.lower()
    trees["site_id"] = trees["site_id"].astype(str).str.strip()
    trees["visit_id"] = trees["visit_id"].astype(str).str.strip()
    trees["tree_id"] = trees["tree_id"].astype(str).str.strip()
    trees["species_code"] = trees["species_code"].astype(str).str.strip().str.upper()
    trees["species_group"] = trees["species_group"].astype(str).str.strip().str.lower()
    for column in ("dbh_cm", "height_m", "expansion_factor"):
        trees[column] = pd.to_numeric(trees[column], errors="coerce")
    trees["status"] = trees["status"].astype(str).str.strip().str.lower()
    trees = trees[
        trees["status"].eq("live")
        & trees["dbh_cm"].gt(0.0)
        & trees["dbh_cm"].le(500.0)
    ].copy()

    trees = _attach_species(trees)
    if trees["species"].isna().any():
        raise NationalFitError("species mapping produced missing RSYC species")

    # NL FAL has no published expansion factor and no per-plot TPH/BA.  A unit
    # weight is the only honest tree weight; a constant weight cancels out of the
    # multinomial shape likelihood.  Other sources with missing factors are
    # dropped and reported rather than imputed.
    missing_factor = trees["expansion_factor"].isna() | trees["expansion_factor"].le(0.0)
    nl_fal = trees["source"].eq("nl_fal")
    trees.loc[nl_fal & missing_factor, "expansion_factor"] = 1.0
    dropped_missing_factor = int(
        (missing_factor & ~nl_fal).sum()
    )
    trees = trees[~(missing_factor & ~nl_fal)].copy()

    trees = trees.join(site_frame, on="site_id")
    dropped_unmapped = int(trees["ecodistrict_key"].isna().sum())
    trees = trees[trees["ecodistrict_key"].notna()].copy()

    visits = pd.read_csv(
        config.visits_path,
        usecols=["source", "visit_id", "year", "age"],
        low_memory=False,
        dtype={"source": str, "visit_id": str},
    )
    visits["source"] = visits["source"].astype(str).str.strip().str.lower()
    visits["visit_id"] = visits["visit_id"].astype(str).str.strip()
    visits["visit_key"] = visits["source"] + "|" + visits["visit_id"]
    visits = visits.drop_duplicates("visit_key", keep="first")
    trees["visit_key"] = trees["source"] + "|" + trees["visit_id"]
    trees = trees.merge(
        visits[["visit_key", "year", "age"]],
        on="visit_key",
        how="left",
        validate="many_to_one",
    )
    trees = trees.drop(columns=["visit_key", "status", "mapping_basis"])
    trees["plot_id"] = _plot_ids(trees)
    trees["dbh_limit_cm"] = (
        trees["source"].map(DBH_LIMIT_BY_SOURCE).fillna(DEFAULT_DBH_LIMIT_CM).astype(float)
    )
    trees["national"] = NATIONAL_ID

    for column in (
        "source",
        "species",
        "species_group",
        "plot_id",
        "province",
        "ecozone",
        "ecoprovince",
        "ecoregion",
        "ecodistrict",
        "ecozone_key",
        "ecoprovince_key",
        "ecoregion_key",
        "ecodistrict_key",
        "national",
    ):
        trees[column] = trees[column].astype(str).astype("category")
    for column in ("dbh_cm", "height_m", "expansion_factor", "age", "dbh_limit_cm"):
        trees[column] = pd.to_numeric(trees[column], errors="coerce").astype("float32")
    trees["year"] = pd.to_numeric(trees["year"], errors="coerce").astype("Float32")

    metadata = {
        "cache_key": cache_key,
        "prep_version": PREP_VERSION,
        "generated_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "rows": len(trees),
        "sites": int(trees["site_id"].nunique()),
        "plots": int(trees["plot_id"].nunique()),
        "species": sorted(trees["species"].astype(str).unique().tolist()),
        "sources": {
            str(source): int(count)
            for source, count in trees["source"].value_counts().items()
        },
        "dropped_missing_expansion_factor": dropped_missing_factor,
        "dropped_unmapped_sites": dropped_unmapped,
        "mapping": join_stats,
        "dbh_limits_by_source": DBH_LIMIT_BY_SOURCE,
        "rpkm": {
            "note": (
                "live trees with finite DBH in (0, 500] cm; NL FAL uses unit "
                "weights because the source publishes no expansion factor"
            )
        },
        "runtime_seconds": time.time() - started,
    }
    trees.to_parquet(cache_path, index=False, compression="zstd")
    metadata_path.write_text(
        json.dumps(_jsonable(metadata), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _stage(config, f"cached {len(trees):,} analysis trees", started)
    return trees, metadata


# ---------------------------------------------------------------------------
# RSYC scale prior
# ---------------------------------------------------------------------------


def load_rsyc_prior_models(path: Path) -> pd.DataFrame:
    """Load publishable RSYC volume and AGB curves used as scale priors."""
    if not path.exists():
        raise FileNotFoundError(f"RSYC model archive not found: {path}")
    columns = [
        "model_id",
        "response",
        "response_units",
        "strata_type",
        "strata_level",
        "strata_id",
        "scale",
        "species",
        "model_group",
        "age_min",
        "age_max",
        "b1",
        "b2",
        "b3",
        "b4",
        "n_obs",
        "fit_ok",
        "publish_ok",
    ]
    with zipfile.ZipFile(path) as archive, archive.open("RSYC_models.csv") as handle:
        models = pd.read_csv(handle, usecols=columns, low_memory=False)
    models["response"] = models["response"].astype(str).str.strip().str.lower()
    models = models[models["response"].isin(["volume", "agb"])].copy()
    for flag in ("fit_ok", "publish_ok"):
        models = models[
            models[flag].astype(str).str.upper().isin({"TRUE", "1", "T"})
        ]
    models["scale"] = models["scale"].astype(str).str.strip().str.lower()
    models["strata_level"] = models["strata_level"].astype(str).str.strip()
    models["strata_id"] = models["strata_id"].astype(str).str.strip()
    models["species"] = models["species"].astype(str).str.strip().str.upper()
    models["model_group"] = models["model_group"].astype(str).str.strip().str.lower()
    for column in ("b1", "b2", "b3", "b4", "age_min", "age_max", "n_obs"):
        models[column] = pd.to_numeric(models[column], errors="coerce")
    models = models.dropna(subset=["b1", "b2", "b3", "b4"])
    models["n_obs"] = models["n_obs"].fillna(0)
    models["_scale_rank"] = models["scale"].eq("national").astype(int)
    models = models.sort_values(
        ["strata_level", "strata_id", "response", "species", "model_group", "_scale_rank", "n_obs"],
        ascending=[True, True, True, True, True, False, False],
    )
    return models.drop_duplicates(
        ["strata_level", "strata_id", "response", "species", "model_group"],
        keep="first",
    ).reset_index(drop=True)


def _evaluate_rsyc_curve(model: Mapping[str, Any], age: float) -> float | None:
    try:
        b1 = float(model["b1"])
        b2 = float(model["b2"])
        b3 = float(model["b3"])
        b4 = float(model["b4"])
        age_value = float(age)
    except (KeyError, TypeError, ValueError):
        return None
    if age_value <= 0.0 or not all(math.isfinite(value) for value in (b1, b2, b3, b4)):
        return None
    value = b1 * math.exp(-b4 * age_value) * (1.0 - math.exp(-b2 * age_value)) ** b3
    return float(value) if math.isfinite(value) else None


def _rsyc_prior_for_cell(
    models: pd.DataFrame,
    *,
    level: str,
    stratum_id: str,
    species: str,
    species_group: str,
    model_group: str,
    reference_age: float,
) -> dict[str, Any]:
    """Resolve the best RSYC curve for a cell and evaluate it at the reference age.

    Resolution order is exact strata/species, exact strata/functional group,
    then the parent strata chain.  The result records which basis was used; a
    missing prior is returned as ``none`` rather than guessed.
    """
    normalized_species = str(species).strip().upper()
    functional_group = str(species_group).strip().lower()
    if functional_group not in {"coniferous", "broadleaf", "generic"}:
        functional_group = str(model_group).strip().lower()
    if functional_group not in {"coniferous", "broadleaf", "generic"}:
        functional_group = ""

    candidates: list[tuple[str, str, str]] = [(level, stratum_id, "exact")]
    for parent_level, parent_id in _parent_chain(level, stratum_id):
        candidates.append((parent_level, parent_id, "parent"))

    for candidate_level, candidate_id, relation in candidates:
        subset = models[
            models["strata_level"].eq(candidate_level)
            & models["strata_id"].eq(candidate_id)
        ]
        if subset.empty:
            continue
        for basis in ("species", "group"):
            if basis == "species":
                chosen = subset[subset["species"].eq(normalized_species)]
            else:
                if not functional_group:
                    continue
                chosen = subset[subset["model_group"].eq(functional_group)]
            if chosen.empty:
                continue
            prior: dict[str, Any] = {
                "rsyc_prior_basis": (
                    f"{relation}_{basis}" if relation == "parent" else basis
                ),
                "rsyc_model_level": candidate_level,
                "rsyc_model_strata_id": candidate_id,
                "rsyc_reference_age": float(reference_age),
                "rsyc_volume_model_id": None,
                "rsyc_agb_model_id": None,
                "rsyc_volume_m3ha": None,
                "rsyc_agb_mg_ha": None,
                "rsyc_reference_age_in_range": None,
                "rsyc_volume_units": "",
                "rsyc_agb_units": "",
            }
            for response, value_key, id_key, units_key in (
                (
                    "volume",
                    "rsyc_volume_m3ha",
                    "rsyc_volume_model_id",
                    "rsyc_volume_units",
                ),
                ("agb", "rsyc_agb_mg_ha", "rsyc_agb_model_id", "rsyc_agb_units"),
            ):
                response_rows = chosen[chosen["response"].eq(response)]
                if response_rows.empty:
                    continue
                row = response_rows.iloc[0]
                prior[id_key] = str(row["model_id"])
                prior[units_key] = str(row.get("response_units", ""))
                value = _evaluate_rsyc_curve(row, reference_age)
                prior[value_key] = value
                in_range = (
                    pd.notna(row["age_min"])
                    and pd.notna(row["age_max"])
                    and float(row["age_min"]) <= reference_age <= float(row["age_max"])
                )
                previous = prior["rsyc_reference_age_in_range"]
                prior["rsyc_reference_age_in_range"] = (
                    bool(in_range) if previous is None else bool(previous and in_range)
                )
            return prior
    return {
        "rsyc_prior_basis": "none",
        "rsyc_model_level": None,
        "rsyc_model_strata_id": None,
        "rsyc_reference_age": float(reference_age),
        "rsyc_volume_model_id": None,
        "rsyc_agb_model_id": None,
        "rsyc_volume_m3ha": None,
        "rsyc_agb_mg_ha": None,
        "rsyc_reference_age_in_range": None,
        "rsyc_volume_units": "",
        "rsyc_agb_units": "",
    }


# ---------------------------------------------------------------------------
# Cell fitting (worker side)
# ---------------------------------------------------------------------------


_WORKER_STATE: dict[str, Any] = {}


def _set_worker_state(state: Mapping[str, Any]) -> None:
    _WORKER_STATE.clear()
    _WORKER_STATE.update(state)


def _fit_height_model(frame: pd.DataFrame) -> dict[str, Any] | None:
    """Fit H = 1.3 + exp(a) * DBH^b by log-linear least squares."""
    data = frame[
        frame["dbh_cm"].gt(0.0).fillna(False)
        & frame["height_m"].gt(1.3).fillna(False)
    ]
    if len(data) < MIN_HEIGHT_OBS:
        return None
    dbh = data["dbh_cm"].to_numpy(dtype=float)
    height = data["height_m"].to_numpy(dtype=float)
    design = np.column_stack([np.ones_like(dbh), np.log(dbh)])
    response = np.log(height - 1.3)
    coefficients, *_ = np.linalg.lstsq(design, response, rcond=None)
    a, b = (float(value) for value in coefficients)
    predicted = 1.3 + np.exp(a) * np.power(dbh, b)
    residual = height - predicted
    if not np.isfinite(predicted).all():
        return None
    return {
        "a": a,
        "b": b,
        "n": len(data),
        "rmse_m": float(np.sqrt(np.mean(residual**2))),
        "bias_m": float(np.mean(residual)),
    }


def _selected_record(fit: DistributionFit) -> dict[str, Any]:
    selected = fit.selected
    return {
        "distribution": selected.distribution,
        "parameters_json": selected.parameters_json(),
        "component_weights_json": selected.component_weights_json(),
        "component_names_json": (
            json.dumps(list(selected.component_names))
            if selected.component_names is not None
            else ""
        ),
        "spike_weight": _clean_number(selected.spike_weight),
        "n_parameters": int(selected.n_parameters),
        "aic": _clean_number(selected.aic),
        "aicc": _clean_number(selected.aicc),
        "bic": _clean_number(selected.bic),
        "ks": _clean_number(selected.ks),
        "log_likelihood": _clean_number(selected.log_likelihood),
        "converged": bool(selected.converged),
        "fit_quality_flag": (
            "high_ks"
            if selected.ks is not None and math.isfinite(selected.ks) and selected.ks > 0.20
            else "ok"
        ),
        "synthesis_compatible": bool(selected.synthesis_compatible),
        "method": selected.method,
    }


def _quality_flag(
    ks: Any, n_trees: Any, n_plots: Any
) -> str:
    """Return the local-fit quality flags used by the evidence table."""
    flags: list[str] = []
    ks_value = _clean_number(ks)
    if ks_value is not None and ks_value > 0.20:
        flags.append("high_ks")
    trees = _clean_number(n_trees)
    plots = _clean_number(n_plots)
    if (trees is not None and trees < MIN_DIRECT_TREES) or (
        plots is not None and plots < MIN_DIRECT_PLOTS
    ):
        flags.append("low_support")
    return ";".join(flags) if flags else "ok"


def _candidate_summary(candidate: FitCandidate, selected: FitCandidate) -> dict[str, Any]:
    return {
        "distribution": candidate.distribution,
        "fit_status": candidate.fit_status,
        "aic": _clean_number(candidate.aic),
        "aicc": _clean_number(candidate.aicc),
        "ks": _clean_number(candidate.ks),
        "n_parameters": int(candidate.n_parameters),
        "converged": bool(candidate.converged),
        "spike_weight": _clean_number(candidate.spike_weight),
        "selected": candidate is selected,
        "error": candidate.error,
    }


def _fit_one_cell(task_index: int) -> dict[str, Any]:
    """Fit one direct/pooled cell.  Executed in a forked worker."""
    state = _WORKER_STATE
    spec = state["specs"][task_index]
    started = time.time()
    view = state["trees"].iloc[spec["start"] : spec["end"]]
    if view.empty:
        return {
            "schema_version": SCHEMA_VERSION,
            "fit_config_hash": state["fit_config_hash"],
            "spec": spec,
            "status": "no_fit",
            "error": "no analysis trees remain after cleaning",
            "runtime_seconds": time.time() - started,
        }
    level = spec["level"]
    species = spec["species"]
    target = {
        "species": species,
        "target_level": level,
        "target_id": f"{level}:{spec['stratum_id']}|{species}",
        level: spec["stratum_id"],
    }
    min_trees = MIN_DIRECT_TREES if spec["coverage_class"] == "direct_fit" else MIN_POOLED_TREES
    min_plots = MIN_DIRECT_PLOTS if spec["coverage_class"] == "direct_fit" else MIN_POOLED_PLOTS
    try:
        fit = fit_distribution_for_target(
            view,
            target,
            scope=f"national_{level}",
            min_trees=min_trees,
            min_plots=min_plots,
            min_plot_trees=MIN_POOLED_TREES,
            bin_width_cm=state["bin_width_cm"],
            max_dbh_cm=None,
            random_state=state["random_state"],
            levels=(level,),
            families=NATIONAL_FAMILIES,
            mixture_n_starts=1,
            mixture_max_iter=80,
        )
    except (DistributionFitError, ValueError) as error:
        return {
            "schema_version": SCHEMA_VERSION,
            "fit_config_hash": state["fit_config_hash"],
            "spec": spec,
            "status": "no_fit",
            "error": f"{type(error).__name__}: {error}",
            "runtime_seconds": time.time() - started,
        }
    if fit.fit_level != level:
        return {
            "schema_version": SCHEMA_VERSION,
            "fit_config_hash": state["fit_config_hash"],
            "spec": spec,
            "status": "no_fit",
            "error": (
                f"fit fell back to {fit.fit_level}:{fit.fit_id}; "
                "a cell-level fit was required"
            ),
            "runtime_seconds": time.time() - started,
        }
    try:
        record = _selected_record(fit)
    except DistributionFitError as error:
        return {
            "schema_version": SCHEMA_VERSION,
            "fit_config_hash": state["fit_config_hash"],
            "spec": spec,
            "status": "no_fit",
            "error": f"selection failed: {error}",
            "candidates": [
                _candidate_summary(candidate, candidate)
                for candidate in fit.candidates
            ],
            "runtime_seconds": time.time() - started,
        }
    limit = float(fit.dbh_limit_cm)
    limit_mask = np.isclose(
        pd.to_numeric(view["dbh_limit_cm"], errors="coerce"), limit, atol=1e-6
    )
    fit_view = view.loc[limit_mask]
    record.update(
        {
            "fit_level": fit.fit_level,
            "fit_id": fit.fit_id,
            "n_trees": len(fit_view),
            "n_plots": int(fit_view["plot_id"].nunique()),
            "n_bins": int(fit.n_bins),
            "dbh_limit_cm": limit,
            "support_upper_cm": _clean_number(fit.support_upper_cm),
            "limit_excluded_trees": int(len(view) - len(fit_view)),
            "fit_sources": ";".join(
                sorted(str(value) for value in fit_view["source"].astype(str).unique())
            ),
        }
    )
    record["fit_quality_flag"] = _quality_flag(
        record.get("ks"), record["n_trees"], record["n_plots"]
    )
    height = _fit_height_model(view)
    height_available = int(
        view["height_m"].gt(1.3).fillna(False).sum()
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "fit_config_hash": state["fit_config_hash"],
        "spec": spec,
        "status": "ok",
        "fit": record,
        "candidates": [
            _candidate_summary(candidate, fit.selected) for candidate in fit.candidates
        ],
        "height": height,
        "height_observations": height_available,
        "error": None,
        "runtime_seconds": time.time() - started,
    }


def _worker_init(  # pragma: no cover - forked process entry
    specs: list[dict[str, Any]],
    trees: pd.DataFrame,
    fit_config_hash: str,
    bin_width_cm: float,
    random_state: int,
) -> None:
    _set_worker_state(
        {
            "specs": specs,
            "trees": trees,
            "fit_config_hash": fit_config_hash,
            "bin_width_cm": bin_width_cm,
            "random_state": random_state,
        }
    )


# ---------------------------------------------------------------------------
# Dispatch and resume
# ---------------------------------------------------------------------------


def _load_completed_cell(path: Path, fit_config_hash: str) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if record.get("schema_version") != SCHEMA_VERSION:
        return None
    if record.get("fit_config_hash") != fit_config_hash:
        return None
    if record.get("status") not in {"ok", "no_fit"}:
        return None
    return record


def _write_cell(path: Path, record: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(_jsonable(record), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _sort_level_groups(
    trees: pd.DataFrame, level: str
) -> tuple[pd.DataFrame, dict[tuple[str, str], tuple[int, int]]]:
    """Sort the analysis table once for a level and return contiguous group bounds."""
    key_column = "national" if level == "national" else f"{level}_key"
    keys = trees[key_column].astype(str).to_numpy()
    species = trees["species"].astype(str).to_numpy()
    group_codes, group_labels = pd.factorize(
        pd.MultiIndex.from_arrays([keys, species]), sort=False
    )
    order = np.argsort(group_codes, kind="stable")
    sorted_codes = group_codes[order]
    bounds: dict[tuple[str, str], tuple[int, int]] = {}
    starts = np.searchsorted(sorted_codes, np.arange(len(group_labels)), side="left")
    ends = np.searchsorted(sorted_codes, np.arange(len(group_labels)), side="right")
    for label, start, end in zip(group_labels, starts, ends):
        bounds[(str(label[0]), str(label[1]))] = (int(start), int(end))
    sorted_trees = trees.iloc[order].reset_index(drop=True)
    # The candidate fitter resolves the target by the level-named column.  It
    # must contain the full nested stratum id, not the leaf name that the raw
    # tree table carries for display.
    sorted_trees[level] = keys[order]
    return sorted_trees, bounds


def _fit_level(
    config: FitConfig,
    trees: pd.DataFrame,
    coverage: pd.DataFrame,
    level: str,
    *,
    fit_config_hash: str,
    deadline: float,
    started: float,
) -> tuple[int, int, int, bool]:
    """Fit all direct/pooled cells for one level.  Return (submitted, resumed, errors, timed_out)."""
    candidates = coverage[
        coverage["level"].eq(level)
        & coverage["coverage_class"].isin(["direct_fit", "pooled_fit"])
    ].copy()
    if config.max_cells is not None:
        remaining_capacity = max(0, int(config.max_cells))
        candidates = candidates.iloc[:remaining_capacity]
    if candidates.empty:
        return 0, 0, 0, False
    sorted_trees, bounds = _sort_level_groups(trees, level)
    specs: list[dict[str, Any]] = []
    for row in candidates.itertuples(index=False):
        key = (str(row.stratum_id), str(row.species))
        if key not in bounds:
            start, end = 0, 0
        else:
            start, end = bounds[key]
        specs.append(
            {
                "level": level,
                "stratum_id": str(row.stratum_id),
                "stratum_name": str(row.stratum_name),
                "species": str(row.species),
                "coverage_class": str(row.coverage_class),
                "coverage_n_trees": int(row.n_trees),
                "coverage_n_plots": int(row.n_plots),
                "start": start,
                "end": end,
            }
        )
    cell_dir = config.cells_dir / level
    cell_dir.mkdir(parents=True, exist_ok=True)
    pending_specs: list[dict[str, Any]] = []
    resumed = 0
    for spec in specs:
        path = cell_dir / f"{_cell_key(level, spec['stratum_id'], spec['species'])}.json"
        if config.resume and _load_completed_cell(path, fit_config_hash) is not None:
            resumed += 1
            continue
        pending_specs.append(spec)
    if not pending_specs:
        return 0, resumed, 0, False

    timed_out = False
    errors = 0
    submitted = 0
    context = mp.get_context("fork")
    with ProcessPoolExecutor(
        max_workers=max(1, config.workers),
        mp_context=context,
        initializer=_worker_init,
        initargs=(
            pending_specs,
            sorted_trees,
            fit_config_hash,
            config.bin_width_cm,
            config.random_state,
        ),
    ) as executor:
        next_index = 0
        pending: dict[Any, int] = {}
        while next_index < len(pending_specs) or pending:
            while (
                next_index < len(pending_specs)
                and len(pending) < max(1, config.workers) * 2
            ):
                if time.time() >= deadline:
                    timed_out = True
                    break
                future = executor.submit(_fit_one_cell, next_index)
                pending[future] = next_index
                next_index += 1
                submitted += 1
            if not pending:
                break
            timeout = max(0.0, deadline - time.time()) if not timed_out else 0.05
            done, _ = wait(pending, timeout=timeout, return_when=FIRST_COMPLETED)
            if not done and time.time() >= deadline:
                timed_out = True
            for future in done:
                task_index = pending.pop(future)
                spec = pending_specs[task_index]
                path = cell_dir / (
                    f"{_cell_key(level, spec['stratum_id'], spec['species'])}.json"
                )
                try:
                    record = future.result()
                except Exception as error:  # pragma: no cover - worker crash path
                    record = {
                        "schema_version": SCHEMA_VERSION,
                        "fit_config_hash": fit_config_hash,
                        "spec": spec,
                        "status": "error",
                        "error": f"{type(error).__name__}: {error}",
                        "traceback": traceback.format_exc(),
                    }
                if record.get("status") == "error":
                    errors += 1
                _write_cell(path, record)
            if timed_out:
                # Let in-flight work finish and collect it; do not start new cells.
                continue
    _stage(
        config,
        f"{level}: submitted={submitted} resumed={resumed} errors={errors} timed_out={timed_out}",
        started,
    )
    return submitted, resumed, errors, timed_out


def _load_all_cell_records(
    config: FitConfig, fit_config_hash: str
) -> dict[tuple[str, str, str], dict[str, Any]]:
    records: dict[tuple[str, str, str], dict[str, Any]] = {}
    for level in config.support_levels:
        directory = config.cells_dir / level
        if not directory.exists():
            continue
        for path in directory.glob("*.json"):
            record = _load_completed_cell(path, fit_config_hash)
            if record is None:
                continue
            spec = record.get("spec", {})
            key = (level, str(spec.get("stratum_id")), str(spec.get("species")))
            records[key] = record
    return records


# ---------------------------------------------------------------------------
# Finalisation: local, parent, species-national, no_fit
# ---------------------------------------------------------------------------


def _empty_distribution_row() -> dict[str, Any]:
    return {
        "distribution": None,
        "parameters_json": "",
        "component_weights_json": "",
        "component_names_json": "",
        "spike_weight": None,
        "n_parameters": None,
        "aic": None,
        "aicc": None,
        "bic": None,
        "ks": None,
        "log_likelihood": None,
        "converged": None,
        "fit_quality_flag": None,
        "synthesis_compatible": None,
        "method": None,
        "fit_level": None,
        "fit_id": None,
        "n_trees": 0,
        "n_plots": 0,
        "n_bins": None,
        "dbh_limit_cm": None,
        "support_upper_cm": None,
        "limit_excluded_trees": None,
        "fit_sources": "",
    }


def _distribution_row_from_record(
    record: Mapping[str, Any],
    *,
    evidence_tier: str,
    fit_scope: str,
    borrowed_from_level: str | None = None,
    borrowed_from_id: str | None = None,
) -> dict[str, Any]:
    row = _empty_distribution_row()
    fit = record.get("fit") or {}
    row.update(fit)
    row.update(
        {
            "evidence_tier": evidence_tier,
            "fit_scope": fit_scope,
            "fit_status": "ok",
            "borrowed_from_level": borrowed_from_level,
            "borrowed_from_id": borrowed_from_id,
            "error": None,
            "fit_level": fit.get("fit_level") or record.get("spec", {}).get("level"),
            "fit_id": fit.get("fit_id"),
        }
    )
    return row


def _local_height_record(record: Mapping[str, Any]) -> dict[str, Any] | None:
    height = record.get("height")
    if not height:
        return None
    return {
        "a": _clean_number(height.get("a")),
        "b": _clean_number(height.get("b")),
        "n": int(height.get("n", 0)),
        "rmse_m": _clean_number(height.get("rmse_m")),
        "bias_m": _clean_number(height.get("bias_m")),
        "fit_status": "ok",
        "fit_level": record.get("spec", {}).get("level"),
        "fit_id": (
            f"{record.get('spec', {}).get('level')}:"
            f"{record.get('spec', {}).get('stratum_id')}|"
            f"{record.get('spec', {}).get('species')}"
        ),
        "error": None,
    }


def _empty_height_row() -> dict[str, Any]:
    return {
        "a": None,
        "b": None,
        "n": None,
        "rmse_m": None,
        "bias_m": None,
        "fit_status": "no_model",
        "fit_level": None,
        "fit_id": None,
        "borrowed_from_level": None,
        "borrowed_from_id": None,
        "evidence_tier": None,
        "error": None,
    }


def _height_row_for_cell(
    *,
    level: str,
    stratum_id: str,
    species: str,
    local_record: Mapping[str, Any] | None,
    local_fits: Mapping[tuple[str, str, str], Mapping[str, Any]],
    national_records: Mapping[str, Mapping[str, Any]],
    coverage_class: str,
) -> dict[str, Any]:
    row = _empty_height_row()
    if local_record is not None:
        local_height = _local_height_record(local_record)
        if local_height is not None:
            row.update(local_height)
            row["evidence_tier"] = coverage_class
            return row
    # Borrow the height model from the same parent chain used for the shape.
    for parent_level, parent_id in _parent_chain(level, stratum_id):
        parent = local_fits.get((parent_level, parent_id, species))
        if parent is None:
            continue
        parent_height = _local_height_record(parent)
        if parent_height is None:
            continue
        row.update(parent_height)
        row.update(
            {
                "evidence_tier": "pooled_parent",
                "borrowed_from_level": parent_level,
                "borrowed_from_id": parent_id,
                "fit_status": "borrowed",
            }
        )
        return row
    national = national_records.get(species)
    if national is not None:
        national_height = _local_height_record(national)
        if national_height is not None:
            row.update(national_height)
            row.update(
                {
                    "evidence_tier": "species_national",
                    "borrowed_from_level": "national",
                    "borrowed_from_id": NATIONAL_ID,
                    "fit_status": "borrowed",
                }
            )
            return row
    row["evidence_tier"] = "no_fit"
    return row


def finalize_rows(
    config: FitConfig,
    coverage: pd.DataFrame,
    *,
    fit_config_hash: str,
    rsyc_models: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Build one distribution and height row per support-level cell."""
    records = _load_all_cell_records(config, fit_config_hash)
    local_fits = {
        key: record
        for key, record in records.items()
        if record.get("status") == "ok"
    }
    national_records = {
        str(record.get("spec", {}).get("species")): record
        for (level, _stratum, _species), record in local_fits.items()
        if level == "national"
    }
    distribution_rows: list[dict[str, Any]] = []
    height_rows: list[dict[str, Any]] = []
    remaining: list[dict[str, Any]] = []
    for coverage_row in coverage[coverage["level"].isin(config.support_levels)].itertuples(
        index=False
    ):
        level = str(coverage_row.level)
        stratum_id = str(coverage_row.stratum_id)
        species = str(coverage_row.species)
        coverage_class = str(coverage_row.coverage_class)
        key = (level, stratum_id, species)
        record = records.get(key)
        base = {
            "level": level,
            "stratum_id": stratum_id,
            "stratum_name": str(coverage_row.stratum_name) or stratum_id.split("/")[-1],
            "ecozone": str(coverage_row.ecozone),
            "ecoprovince": str(coverage_row.ecoprovince),
            "ecoregion": str(coverage_row.ecoregion),
            "ecodistrict": str(coverage_row.ecodistrict),
            "species": species,
            "species_group": str(coverage_row.species_group),
            "rsyc_model_group": str(coverage_row.rsyc_model_group),
            "coverage_class": coverage_class,
            "coverage_n_trees": int(coverage_row.n_trees),
            "coverage_n_plots": int(coverage_row.n_plots),
            "sources": str(coverage_row.sources),
        }
        rsyc_prior = _rsyc_prior_for_cell(
            rsyc_models,
            level=level,
            stratum_id=stratum_id,
            species=species,
            species_group=str(coverage_row.species_group),
            model_group=str(coverage_row.rsyc_model_group),
            reference_age=config.reference_age,
        )
        if record is None:
            if coverage_class == "no_data":
                row = _borrow_distribution_row(
                    base=base,
                    level=level,
                    stratum_id=stratum_id,
                    species=species,
                    local_fits=local_fits,
                    national_records=national_records,
                )
                height = _height_row_for_cell(
                    level=level,
                    stratum_id=stratum_id,
                    species=species,
                    local_record=None,
                    local_fits=local_fits,
                    national_records=national_records,
                    coverage_class=coverage_class,
                )
                height.update(
                    {
                        "level": level,
                        "stratum_id": stratum_id,
                        "stratum_name": base["stratum_name"],
                        "species": species,
                        "coverage_class": coverage_class,
                    }
                )
            else:
                row = _empty_distribution_row()
                row.update(base)
                row.update(
                    {
                        "evidence_tier": "no_fit",
                        "fit_scope": "none",
                        "fit_status": "pending_timebox",
                        "error": "cell was not fitted before the timebox expired",
                    }
                )
                remaining.append(
                    {
                        "level": level,
                        "stratum_id": stratum_id,
                        "species": species,
                        "coverage_class": coverage_class,
                        "coverage_n_trees": int(coverage_row.n_trees),
                        "coverage_n_plots": int(coverage_row.n_plots),
                        "reason": "pending_timebox",
                        "resumable": True,
                    }
                )
                height = _empty_height_row()
                height.update(
                    {
                        "level": level,
                        "stratum_id": stratum_id,
                        "stratum_name": base["stratum_name"],
                        "species": species,
                        "coverage_class": coverage_class,
                        "evidence_tier": "no_fit",
                        "fit_status": "pending_timebox",
                    }
                )
        elif record.get("status") == "no_fit":
            if coverage_class in {"direct_fit", "pooled_fit"}:
                row = _empty_distribution_row()
                row.update(base)
                row.update(
                    {
                        "evidence_tier": "no_fit",
                        "fit_scope": "none",
                        "fit_status": "no_fit",
                        "error": record.get("error", "cell-level fit failed"),
                    }
                )
                remaining.append(
                    {
                        "level": level,
                        "stratum_id": stratum_id,
                        "species": species,
                        "coverage_class": coverage_class,
                        "coverage_n_trees": int(coverage_row.n_trees),
                        "coverage_n_plots": int(coverage_row.n_plots),
                        "reason": "local_fit_failed",
                        "error": record.get("error", ""),
                        "resumable": False,
                    }
                )
            else:
                row = _borrow_distribution_row(
                    base=base,
                    level=level,
                    stratum_id=stratum_id,
                    species=species,
                    local_fits=local_fits,
                    national_records=national_records,
                )
            height = _height_row_for_cell(
                level=level,
                stratum_id=stratum_id,
                species=species,
                local_record=record,
                local_fits=local_fits,
                national_records=national_records,
                coverage_class=coverage_class,
            )
            height.update(
                {
                    "level": level,
                    "stratum_id": stratum_id,
                    "stratum_name": base["stratum_name"],
                    "species": species,
                    "coverage_class": coverage_class,
                }
            )
        else:
            row = _distribution_row_from_record(
                record,
                evidence_tier=coverage_class,
                fit_scope="local",
            )
            row.update(base)
            row.update(
                {
                    "n_trees": int(row.get("n_trees") or 0),
                    "n_plots": int(row.get("n_plots") or 0),
                    "fit_n_trees": int(row.get("n_trees") or 0),
                    "fit_n_plots": int(row.get("n_plots") or 0),
                }
            )
            row["fit_quality_flag"] = _quality_flag(
                row.get("ks"), row["fit_n_trees"], row["fit_n_plots"]
            )
            height = _height_row_for_cell(
                level=level,
                stratum_id=stratum_id,
                species=species,
                local_record=record,
                local_fits=local_fits,
                national_records=national_records,
                coverage_class=coverage_class,
            )
            height.update(
                {
                    "level": level,
                    "stratum_id": stratum_id,
                    "stratum_name": base["stratum_name"],
                    "species": species,
                    "coverage_class": coverage_class,
                }
            )
        row.update(rsyc_prior)
        distribution_rows.append(row)
        height["n_height_obs"] = int(
            (record or {}).get("height_observations", 0)
        )
        height_rows.append(height)
    return (
        pd.DataFrame(distribution_rows),
        pd.DataFrame(height_rows),
        {"remaining": remaining, "records": records},
    )


def _borrow_distribution_row(
    *,
    base: Mapping[str, Any],
    level: str,
    stratum_id: str,
    species: str,
    local_fits: Mapping[tuple[str, str, str], Mapping[str, Any]],
    national_records: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Resolve a no_data cell to a parent or national species shape."""
    for parent_level, parent_id in _parent_chain(level, stratum_id):
        parent = local_fits.get((parent_level, parent_id, species))
        if parent is None:
            continue
        row = _distribution_row_from_record(
            parent,
            evidence_tier="pooled_parent",
            fit_scope="parent",
            borrowed_from_level=parent_level,
            borrowed_from_id=parent_id,
        )
        row.update(base)
        row.update(
            {
                "n_trees": 0,
                "n_plots": 0,
                "fit_n_trees": int(row.get("fit_n_trees") or row.get("n_trees") or 0),
                "fit_n_plots": int(row.get("fit_n_plots") or row.get("n_plots") or 0),
            }
        )
        # _distribution_row_from_record leaves the source support in
        # n_trees/n_plots; move it to fit_n_* and keep the local cell at zero.
        source_trees = int((parent.get("fit") or {}).get("n_trees") or 0)
        source_plots = int((parent.get("fit") or {}).get("n_plots") or 0)
        row["fit_n_trees"] = source_trees
        row["fit_n_plots"] = source_plots
        row["n_trees"] = 0
        row["n_plots"] = 0
        return row
    national = national_records.get(species)
    if national is not None:
        row = _distribution_row_from_record(
            national,
            evidence_tier="species_national",
            fit_scope="species_national",
            borrowed_from_level="national",
            borrowed_from_id=NATIONAL_ID,
        )
        row.update(base)
        row["fit_n_trees"] = int((national.get("fit") or {}).get("n_trees") or 0)
        row["fit_n_plots"] = int((national.get("fit") or {}).get("n_plots") or 0)
        row["n_trees"] = 0
        row["n_plots"] = 0
        return row
    row = _empty_distribution_row()
    row.update(base)
    row.update(
        {
            "evidence_tier": "no_fit",
            "fit_scope": "none",
            "fit_status": "no_fit",
            "error": "no parent or national species fit is available",
        }
    )
    return row


# ---------------------------------------------------------------------------
# Validation and summaries
# ---------------------------------------------------------------------------


def validate_outputs(
    distribution: pd.DataFrame,
    height: pd.DataFrame,
    coverage: pd.DataFrame,
    *,
    reference_age: float,
) -> dict[str, Any]:
    """Check the evidence contract without inventing missing fits."""
    direct = distribution[distribution["evidence_tier"].eq("direct_fit")]
    pooled = distribution[distribution["evidence_tier"].eq("pooled_fit")]
    parent = distribution[distribution["evidence_tier"].eq("pooled_parent")]
    species_national = distribution[distribution["evidence_tier"].eq("species_national")]
    no_fit = distribution[distribution["evidence_tier"].eq("no_fit")]
    borrowed = distribution[
        distribution["evidence_tier"].isin(["pooled_parent", "species_national"])
    ]
    no_data = distribution[distribution["coverage_class"].eq("no_data")]

    checks: dict[str, Any] = {}

    def _check(name: str, passed: bool, detail: str) -> None:
        checks[name] = {"pass": bool(passed), "detail": detail}

    direct_ks = pd.to_numeric(direct["ks"], errors="coerce")
    direct_ks_finite = int(direct_ks.notna().sum())
    direct_high_ks = int((direct_ks > 0.20).sum())
    _check(
        "direct_fits_have_ks",
        direct_ks_finite == len(direct) and len(direct) > 0,
        f"{direct_ks_finite}/{len(direct)} direct rows have finite KS",
    )
    _check(
        "direct_high_ks_flagged",
        direct_high_ks == int(direct["fit_quality_flag"].eq("high_ks").sum()),
        f"{direct_high_ks} direct rows have KS > 0.20 and the same count is flagged",
    )
    local_mask = distribution["evidence_tier"].isin(["direct_fit", "pooled_fit"])
    borrowed_local = borrowed[borrowed["fit_scope"].eq("local")]
    _check(
        "borrowed_shapes_are_not_local",
        borrowed_local.empty,
        f"{len(borrowed_local)} borrowed rows are labelled local",
    )
    _check(
        "no_data_never_local",
        no_data[no_data["evidence_tier"].isin(["direct_fit", "pooled_fit"])].empty,
        "no_data cells are never labelled direct or pooled",
    )
    _check(
        "borrowed_rows_have_provenance",
        borrowed["borrowed_from_level"].notna().all()
        and borrowed["parameters_json"].astype(str).str.len().gt(2).all(),
        f"{len(borrowed)} borrowed rows have source level and parameters",
    )
    _check(
        "no_fit_rows_have_errors",
        no_fit["error"].notna().all() if len(no_fit) else True,
        f"{len(no_fit)} no_fit rows carry an error reason",
    )
    direct_low_support = int(
        direct["fit_quality_flag"].astype(str).str.contains("low_support").sum()
    )
    pooled_low_support = int(
        pooled["fit_quality_flag"].astype(str).str.contains("low_support").sum()
    )
    checks["local_low_support"] = {
        "pass": True,
        "detail": (
            f"{direct_low_support} direct and {pooled_low_support} pooled rows "
            "have fewer than 30 post-limit fit trees or five post-limit fit "
            "plots; flagged low_support"
        ),
        "direct": direct_low_support,
        "pooled": pooled_low_support,
    }

    rsyc_available = distribution["rsyc_prior_basis"].ne("none")
    rsyc_exact = distribution["rsyc_prior_basis"].isin(
        ["exact", "species", "parent_species"]
    )
    volume_values = pd.to_numeric(distribution["rsyc_volume_m3ha"], errors="coerce")
    agb_values = pd.to_numeric(distribution["rsyc_agb_mg_ha"], errors="coerce")
    rsyc_age = distribution["rsyc_reference_age_in_range"]
    checks["rsyc_prior"] = {
        "pass": bool(
            (volume_values.notna().any() or agb_values.notna().any())
            and (volume_values.dropna() >= 0).all()
            and (agb_values.dropna() >= 0).all()
        ),
        "detail": (
            f"{int(rsyc_available.sum())}/{len(distribution)} rows have an RSYC prior; "
            f"{int(rsyc_exact.sum())} are exact-species; "
            f"{int((rsyc_age == False).sum())} fall outside the published age range"
        ),
        "reference_age": reference_age,
        "basis_counts": {
            str(key): int(value)
            for key, value in distribution["rsyc_prior_basis"]
            .fillna("none")
            .value_counts()
            .items()
        },
        "no_data_basis_counts": {
            str(key): int(value)
            for key, value in no_data["rsyc_prior_basis"]
            .fillna("none")
            .value_counts()
            .items()
        },
        "no_data_missing_prior": int(no_data["rsyc_prior_basis"].eq("none").sum()),
    }

    height_ok = height["fit_status"].eq("ok")
    height_borrowed = height["fit_status"].eq("borrowed")
    height_missing = height["fit_status"].eq("no_model")
    height_rmse = pd.to_numeric(height.loc[height_ok, "rmse_m"], errors="coerce")
    checks["height_models"] = {
        "pass": bool(height_ok.any()),
        "detail": (
            f"{int(height_ok.sum())} local, {int(height_borrowed.sum())} borrowed, "
            f"{int(height_missing.sum())} missing height models; "
            f"median local RMSE {height_rmse.median():.3f} m"
            if height_rmse.notna().any()
            else f"{int(height_ok.sum())} local height models"
        ),
        "local": int(height_ok.sum()),
        "borrowed": int(height_borrowed.sum()),
        "missing": int(height_missing.sum()),
    }
    coverage_counts = (
        coverage[coverage["level"].isin(distribution["level"].unique())]
        .groupby(["level", "coverage_class"], observed=True)
        .size()
        .unstack(fill_value=0)
    )
    distribution_counts = (
        distribution.groupby("level", observed=True).size().to_dict()
    )
    coverage_totals = (
        coverage[coverage["level"].isin(distribution["level"].unique())]
        .groupby("level", observed=True)
        .size()
        .to_dict()
    )
    reconciled = all(
        distribution_counts.get(level, 0) == total
        for level, total in coverage_totals.items()
    )
    duplicate_keys = int(distribution.duplicated(["level", "stratum_id", "species"]).sum())
    checks["coverage_reconciled"] = {
        "pass": bool(reconciled and duplicate_keys == 0),
        "detail": (
            "one evidence row per coverage row at every support level; "
            f"{duplicate_keys} duplicate keys"
        ),
        "coverage": coverage_counts.to_dict(orient="index"),
    }
    return {
        "checks": checks,
        "direct": {
            "n": len(direct),
            "ks_median": _clean_number(direct_ks.median()),
            "ks_p90": _clean_number(direct_ks.quantile(0.90)),
            "high_ks": direct_high_ks,
            "low_support": direct_low_support,
            "distributions": {
                str(key): int(value)
                for key, value in direct["distribution"].fillna("none").value_counts().items()
            },
        },
        "pooled": {
            "n": len(pooled),
            "low_support": pooled_low_support,
            "distributions": {
                str(key): int(value)
                for key, value in pooled["distribution"].fillna("none").value_counts().items()
            },
        },
        "borrowed": {
            "parent": len(parent),
            "species_national": len(species_national),
        },
        "no_fit": len(no_fit),
        "no_data_cells": len(no_data),
        "local_rows": int(local_mask.sum()),
    }


def _evidence_counts(distribution: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for level in LEVEL_ORDER:
        subset = distribution[distribution["level"].eq(level)]
        if subset.empty:
            continue
        counts = {
            tier: int(subset["evidence_tier"].eq(tier).sum()) for tier in EVIDENCE_TIERS
        }
        rows.append(
            {
                "level": level,
                "total_cells": len(subset),
                "direct_fit": counts["direct_fit"],
                "pooled_fit": counts["pooled_fit"],
                "pooled_parent": counts["pooled_parent"],
                "species_national": counts["species_national"],
                "no_fit": counts["no_fit"],
                "coverage_direct": int(subset["coverage_class"].eq("direct_fit").sum()),
                "coverage_pooled": int(subset["coverage_class"].eq("pooled_fit").sum()),
                "coverage_no_data": int(subset["coverage_class"].eq("no_data").sum()),
            }
        )
    return rows


def _top_fits(distribution: pd.DataFrame, tier: str, limit: int = 12) -> list[dict[str, Any]]:
    subset = distribution[distribution["evidence_tier"].eq(tier)].copy()
    if subset.empty:
        return []
    subset["_ks"] = pd.to_numeric(subset["ks"], errors="coerce")
    subset["_n"] = pd.to_numeric(subset["fit_n_trees"], errors="coerce").fillna(0)
    subset = subset.sort_values(["_n", "_ks"], ascending=[False, True])
    rows: list[dict[str, Any]] = []
    for row in subset.head(limit).itertuples(index=False):
        rows.append(
            {
                "level": row.level,
                "stratum_id": row.stratum_id,
                "species": row.species,
                "distribution": row.distribution,
                "n_trees": _clean_number(row.fit_n_trees),
                "n_plots": _clean_number(row.fit_n_plots),
                "ks": _clean_number(row.ks),
                "aic": _clean_number(row.aic),
                "dbh_limit_cm": _clean_number(row.dbh_limit_cm),
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Report and map
# ---------------------------------------------------------------------------


def write_evidence_map(
    distribution: pd.DataFrame,
    path: Path,
    *,
    level: str,
) -> None:
    """Write the evidence-tier heatmap for the target level."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import BoundaryNorm, ListedColormap

    subset = distribution[distribution["level"].eq(level)].copy()
    if subset.empty:
        return
    tier_values = {
        "no_fit": -1,
        "species_national": 0,
        "pooled_parent": 1,
        "pooled_fit": 2,
        "direct_fit": 3,
    }
    pivot = (
        subset.pivot_table(
            index="stratum_id",
            columns="species",
            values="evidence_tier",
            aggfunc="first",
        )
        .sort_index()
    )
    species_order = sorted(
        pivot.columns,
        key=lambda value: (
            str(subset.loc[subset["species"].eq(value), "species_group"].iloc[0]),
            str(value),
        ),
    )
    pivot = pivot.reindex(columns=species_order)
    matrix = pivot.replace(tier_values).astype(float).to_numpy()
    matrix = np.where(np.isnan(matrix), -1.0, matrix)

    cmap = ListedColormap(
        ["#8c2d04", "#f6e8c3", "#c7eae5", "#80cdc1", "#01665e"]
    )
    norm = BoundaryNorm([-1.5, -0.5, 0.5, 1.5, 2.5, 3.5], cmap.N)
    height = max(6.0, 0.20 * len(pivot) + 2.0)
    width = max(10.0, 0.22 * len(species_order) + 6.0)
    fig = plt.figure(figsize=(width, height), constrained_layout=True)
    grid = fig.add_gridspec(1, 2, width_ratios=[8, 1.6])
    ax = fig.add_subplot(grid[0, 0])
    image = ax.imshow(matrix, aspect="auto", cmap=cmap, norm=norm, interpolation="nearest")
    ax.set_xticks(np.arange(len(species_order)))
    ax.set_xticklabels(species_order, rotation=90, fontsize=6)
    ax.set_yticks(np.arange(len(pivot)))
    ax.set_yticklabels(pivot.index, fontsize=max(4.0, 8.0 - 0.02 * len(pivot)))
    ax.set_xlabel("RSYC species or model group")
    ax.set_ylabel(f"{level} stratum")
    ax.set_title(
        f"Evidence tiers — {level} × species "
        f"({len(subset):,} cells; target {level})",
        fontsize=11,
    )
    colorbar = fig.colorbar(image, ax=ax, ticks=[-1, 0, 1, 2, 3], pad=0.01)
    colorbar.ax.set_yticklabels(
        ["no_fit", "species_national", "pooled_parent", "pooled_fit", "direct_fit"],
        fontsize=7,
    )
    counts = subset["evidence_tier"].value_counts()
    bar_ax = fig.add_subplot(grid[0, 1])
    labels = ["direct_fit", "pooled_fit", "pooled_parent", "species_national", "no_fit"]
    values = [int(counts.get(label, 0)) for label in labels]
    colors = ["#01665e", "#80cdc1", "#c7eae5", "#f6e8c3", "#8c2d04"]
    bar_ax.barh(labels, values, color=colors)
    bar_ax.set_xlabel("cells")
    bar_ax.tick_params(axis="y", labelsize=7)
    for index, value in enumerate(values):
        bar_ax.text(value, index, f" {value}", va="center", fontsize=7)
    path.parent.mkdir(parents=True, exist_ok=True)
    # A previously published artifact may be a read-only git-annex symlink.
    # Replace the worktree path rather than writing through the symlink.
    if path.is_symlink() or path.exists():
        path.unlink()
    fig.savefig(path, dpi=180)
    plt.close(fig)


def _md_table(rows: Iterable[Sequence[Any]], header: Sequence[str]) -> str:
    lines = ["| " + " | ".join(str(value) for value in header) + " |"]
    lines.append("|" + "|".join("---" for _ in header) + "|")
    for row in rows:
        lines.append("| " + " | ".join(str(value) for value in row) + " |")
    return "\n".join(lines)


def write_report(
    config: FitConfig,
    distribution: pd.DataFrame,
    height: pd.DataFrame,
    coverage: pd.DataFrame,
    summary: Mapping[str, Any],
    path: Path,
) -> None:
    coverage_levels = (
        coverage[coverage["level"].isin(config.support_levels)]
        .groupby(["level", "coverage_class"], observed=True)
        .size()
        .unstack(fill_value=0)
        .reindex(columns=["direct_fit", "pooled_fit", "no_data"], fill_value=0)
    )
    coverage_rows = []
    for level in config.support_levels:
        row = coverage_levels.loc[level] if level in coverage_levels.index else None
        coverage_rows.append(
            (
                level,
                int(row["direct_fit"]) if row is not None else 0,
                int(row["pooled_fit"]) if row is not None else 0,
                int(row["no_data"]) if row is not None else 0,
            )
        )
    evidence_rows = [
        (
            row["level"],
            row["total_cells"],
            row["direct_fit"],
            row["pooled_fit"],
            row["pooled_parent"],
            row["species_national"],
            row["no_fit"],
        )
        for row in summary["evidence_by_level"]
    ]
    top_direct = summary["top_direct_fits"]
    top_pooled = summary["top_pooled_fits"]
    top_rows = [
        (
            row["level"],
            row["stratum_id"],
            row["species"],
            row["distribution"],
            _fmt(row["n_trees"], 0),
            _fmt(row["n_plots"], 0),
            _fmt(row["ks"], 3),
            _fmt(row["aic"], 1),
        )
        for row in top_direct
    ]
    pooled_rows = [
        (
            row["level"],
            row["stratum_id"],
            row["species"],
            row["distribution"],
            _fmt(row["n_trees"], 0),
            _fmt(row["n_plots"], 0),
            _fmt(row["ks"], 3),
        )
        for row in top_pooled
    ]
    validation = summary["validation"]
    remaining = summary["remaining_cells"]
    lines = [
        "# National hierarchical DBH/height fit",
        "",
        f"**Generated:** {summary['generated_at_utc']}<br>",
        f"**Target level:** `{config.level}`<br>",
        f"**Support levels:** {', '.join(f'`{value}`' for value in config.support_levels)}<br>",
        f"**Artifact version:** `{ARTIFACT_VERSION}`<br>",
        "**Code:** `workflows/national/assortments/national_fit.py`<br>",
        f"**Runtime:** {summary['runtime_seconds']:.1f} s "
        f"(prep {summary['prep_seconds']:.1f} s, fit {summary['fit_seconds']:.1f} s)<br>",
        f"**Timebox:** {config.timebox_seconds:g} s; "
        f"exhausted: `{str(summary['timebox_exhausted']).lower()}`",
        "",
        "## Scope and method",
        "",
        "This pass fits DBH distributions and height–diameter models for every "
        "pre-registered coverage cell at the support levels shown above.  The "
        "coverage audit is the sampling frame: its `direct_fit` and `pooled_fit` "
        "classes are the only cells that receive a local fit.  `no_data` cells "
        "borrow the nearest ancestor shape or the national species shape; they "
        "are never labelled as local fits.",
        "",
        "- **Coverage thresholds:** direct `n_trees >= 100` and `n_plots >= 5`; "
        "pooled otherwise when `30 <= n_trees < 100` or `2 <= n_plots < 5`.",
        "- **Candidate set:** single Weibull and gamma; `mix_weibull_2`; "
        "`mix_weibull_gamma`; and the constrained spike families when the "
        "existing first-bin/KS gate fires.  The national pass deliberately does "
        "not attempt the legacy two-gamma fallback, generalized-beta "
        "diagnostics, three-component Weibull, or unconstrained spikes; those "
        "remain available to other callers.  The finite mixtures use one "
        "deterministic start and 80 EM iterations for this pass.  Selection "
        "follows the existing two-AIC-unit rule and excludes non-converged new "
        "mixtures.",
        "- **Local support floor:** the candidate attempt uses the pre-limit "
        f"support (direct at least `{MIN_DIRECT_TREES}` trees and "
        f"`{MIN_DIRECT_PLOTS}` plots; pooled at least `{MIN_POOLED_TREES}` trees "
        f"and `{MIN_POOLED_PLOTS}` plots).  `fit_n_trees`/`fit_n_plots` report "
        "the post-modal-limit support actually fitted; any local row below 30 "
        "trees or five plots is labelled `low_support`.",
        "- **DBH limits:** the modal per-source limit is used inside each cell. "
        "Trees from other protocol classes are excluded and the count is "
        "recorded in `limit_excluded_trees`.  Source floors: "
        + ", ".join(f"`{key}` {value:g} cm" for key, value in DBH_LIMIT_BY_SOURCE.items())
        + ".",
        "- **Height model:** `H = 1.3 + exp(a) * DBH^b`, fitted by log-linear "
        "least squares with at least "
        f"`{MIN_HEIGHT_OBS}` height observations.  Height models use the same "
        "parent/national fallback as the shape and are labelled separately.",
        "- **Scale prior:** the publishable RSYC volume and AGB curve is evaluated "
        f"at age `{config.reference_age:g}`.  The selected model id and strata "
        "level are recorded; a missing or out-of-range prior is flagged, never "
        "silently extrapolated.",
        "- **Resume/timebox:** each direct/pooled cell writes a JSON partial under "
        "`cells/<level>/`.  A run that exhausts the timebox rebuilds the CSV/report "
        "from completed partials and lists the exact remaining cells in "
        "`remaining_cells.csv`.",
        "",
        "## Coverage frame",
        "",
        _md_table(
            coverage_rows,
            ("Level", "Coverage direct", "Coverage pooled", "Coverage no_data"),
        ),
        "",
        "## Evidence counts",
        "",
        _md_table(
            evidence_rows,
            (
                "Level",
                "Cells",
                "direct_fit",
                "pooled_fit",
                "pooled_parent",
                "species_national",
                "no_fit",
            ),
        ),
        "",
        "`direct_fit`/`pooled_fit` are local fits.  `pooled_parent` is a borrowed "
        "ancestor shape.  `species_national` is the national species shape.  "
        "`no_fit` is an explicit gap.",
        "",
        "## Top direct fits",
        "",
        _md_table(
            top_rows,
            ("Level", "Stratum", "Species", "Distribution", "Trees", "Plots", "KS", "AIC"),
        )
        if top_rows
        else "No direct fits completed.",
        "",
        "## Top pooled fits",
        "",
        _md_table(
            pooled_rows,
            ("Level", "Stratum", "Species", "Distribution", "Trees", "Plots", "KS"),
        )
        if pooled_rows
        else "No pooled fits completed.",
        "",
        "## Validation",
        "",
        f"- Support-level direct fits: {validation['direct']['n']}; median KS "
        f"{_fmt(validation['direct']['ks_median'], 3)}; p90 KS "
        f"{_fmt(validation['direct']['ks_p90'], 3)}; "
        f"{validation['direct']['high_ks']} flagged with KS > 0.20; "
        f"{validation['direct']['low_support']} flagged low_support.",
        f"- Support-level pooled fits: {validation['pooled']['n']}; "
        f"{validation['pooled']['low_support']} flagged low_support.",
        f"- Borrowed shapes: {validation['borrowed']['parent']} parent and "
        f"{validation['borrowed']['species_national']} national species.",
        f"- Explicit gaps: {validation['no_fit']} cells labelled `no_fit`.",
        f"- RSYC prior: {validation['checks']['rsyc_prior']['detail']}.",
        f"- No-data RSYC priors: "
        f"{validation['checks']['rsyc_prior']['no_data_basis_counts']}; "
        f"{validation['checks']['rsyc_prior']['no_data_missing_prior']} no-data "
        "cells have no prior.",
        f"- Height models: {validation['checks']['height_models']['detail']}.",
        "",
        "## Caveats",
        "",
        "- A borrowed shape is a scale-compatible stand shape, not local "
        "evidence.  The `fit_scope` and `borrowed_from_*` columns are the "
        "authoritative provenance.",
        "- The national plot frame is uneven.  Alberta, Ontario, Nova Scotia, "
        "and Nunavut have no mapped plots in the coverage audit; Manitoba has "
        "two sites.  Coarse-level fits are dominated by BC, QC, NB, NL, SK, PE, "
        "YT, and NT support.",
        "- Pooled cells are fitted on small local samples by design.  They are "
        "flagged by `fit_quality_flag`/low support and should not be read as "
        "equivalent to direct cells.  A pooled cell with fewer than ten trees "
        "or fewer than three populated DBH bins remains `no_fit`; the coverage "
        "class is a sampling-support label, not a guarantee that a distribution "
        "can be estimated.",
        "- The harmonized tree table does not carry the source `DBH_LIMIT_TAG` "
        "for every plot.  The per-source floor is a documented approximation; "
        "mixed-protocol cells lose the non-modal protocol trees.",
        "- NL FAL publishes no expansion factor, so its trees carry unit "
        "weights.  The shape likelihood is invariant to a constant weight, but "
        "NL FAL cannot contribute a calibrated TPH scale from this table.",
        "- Height models are unweighted least squares on the available height "
        "measurements.  Sources with sparse height tallies borrow a parent or "
        "national height model; the `n_height_obs` column records local "
        "availability.",
        "- The RSYC curves are published ecological-stratum curves, not local "
        "re-fits.  The reference-age value is a scale prior; it does not "
        "override the fitted shape or imply that the stand is 100 years old.",
        "",
        "## Remaining cells",
        "",
        (
            f"{sum(1 for row in remaining if row.get('reason') == 'pending_timebox')} "
            "direct/pooled cells are pending (resumable), and "
            f"{sum(1 for row in remaining if row.get('reason') == 'local_fit_failed')} "
            "are local fit failures (not resumable without a data or support change).  "
            "The exact list is in `remaining_cells.csv`; the first rows are:"
            if remaining
            else "None — every direct/pooled cell was attempted and no timebox work remains."
        ),
        "",
        _md_table(
            [
                (
                    row["level"],
                    row["stratum_id"],
                    row["species"],
                    row["coverage_class"],
                    row["coverage_n_trees"],
                    row["coverage_n_plots"],
                    row["reason"],
                    "yes" if row.get("resumable") else "no",
                )
                for row in remaining[:20]
            ],
            ("Level", "Stratum", "Species", "Class", "Trees", "Plots", "Reason", "Resumable"),
        )
        if remaining
        else "",
        "",
        "## Outputs",
        "",
        "- `national_distribution_parameters.csv` — one row per support-level cell.",
        "- `national_height_models.csv` — height model per support-level cell.",
        "- `national_evidence_summary.json` — counts, runtime, inputs, validation.",
        "- `evidence_map.png` — evidence tiers at the target level.",
        "- `remaining_cells.csv` — resumable work list.",
        "- `manifest.json` — input/output hashes and code/data commit ids.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def _fmt(value: Any, digits: int = 2) -> str:
    number = _clean_number(value)
    return "" if number is None else f"{number:.{digits}f}"


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def _select_output_columns(frame: pd.DataFrame) -> pd.DataFrame:
    preferred = [
        "level",
        "stratum_id",
        "stratum_name",
        "ecozone",
        "ecoprovince",
        "ecoregion",
        "ecodistrict",
        "species",
        "species_group",
        "rsyc_model_group",
        "coverage_class",
        "evidence_tier",
        "fit_scope",
        "fit_status",
        "distribution",
        "parameters_json",
        "component_weights_json",
        "component_names_json",
        "spike_weight",
        "n_parameters",
        "n_trees",
        "n_plots",
        "fit_n_trees",
        "fit_n_plots",
        "n_bins",
        "dbh_limit_cm",
        "support_upper_cm",
        "limit_excluded_trees",
        "coverage_n_trees",
        "coverage_n_plots",
        "aic",
        "aicc",
        "bic",
        "ks",
        "log_likelihood",
        "converged",
        "fit_quality_flag",
        "synthesis_compatible",
        "fit_level",
        "fit_id",
        "borrowed_from_level",
        "borrowed_from_id",
        "sources",
        "fit_sources",
        "rsyc_volume_model_id",
        "rsyc_agb_model_id",
        "rsyc_model_level",
        "rsyc_model_strata_id",
        "rsyc_prior_basis",
        "rsyc_reference_age",
        "rsyc_volume_m3ha",
        "rsyc_agb_mg_ha",
        "rsyc_reference_age_in_range",
        "rsyc_volume_units",
        "rsyc_agb_units",
        "error",
    ]
    for column in preferred:
        if column not in frame.columns:
            frame[column] = None
    integer_columns = (
        "n_parameters",
        "n_trees",
        "n_plots",
        "fit_n_trees",
        "fit_n_plots",
        "n_bins",
        "limit_excluded_trees",
        "coverage_n_trees",
        "coverage_n_plots",
    )
    for column in integer_columns:
        frame[column] = pd.to_numeric(frame[column], errors="coerce").astype("Int64")
    return frame.loc[:, preferred]


def _select_height_columns(frame: pd.DataFrame) -> pd.DataFrame:
    preferred = [
        "level",
        "stratum_id",
        "stratum_name",
        "species",
        "coverage_class",
        "evidence_tier",
        "fit_status",
        "a",
        "b",
        "n",
        "rmse_m",
        "bias_m",
        "n_height_obs",
        "fit_level",
        "fit_id",
        "borrowed_from_level",
        "borrowed_from_id",
        "error",
    ]
    for column in preferred:
        if column not in frame.columns:
            frame[column] = None
    for column in ("n", "n_height_obs"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce").astype("Int64")
    return frame.loc[:, preferred]


def _write_outputs(
    config: FitConfig,
    distribution: pd.DataFrame,
    height: pd.DataFrame,
    coverage: pd.DataFrame,
    summary: dict[str, Any],
    *,
    prep_metadata: Mapping[str, Any],
    started: float,
) -> dict[str, Any]:
    config.out_dir.mkdir(parents=True, exist_ok=True)
    distribution = _select_output_columns(distribution)
    height = _select_height_columns(height)
    distribution_path = config.out_dir / "national_distribution_parameters.csv"
    height_path = config.out_dir / "national_height_models.csv"
    summary_path = config.out_dir / "national_evidence_summary.json"
    report_path = config.out_dir / "NATIONAL_FIT_REPORT.md"
    map_path = config.out_dir / "evidence_map.png"
    remaining_path = config.out_dir / "remaining_cells.csv"
    distribution.to_csv(distribution_path, index=False, lineterminator="\n")
    height.to_csv(height_path, index=False, lineterminator="\n")
    remaining = pd.DataFrame(summary.get("remaining_cells", []))
    if remaining.empty:
        remaining = pd.DataFrame(
            columns=[
                "level",
                "stratum_id",
                "species",
                "coverage_class",
                "coverage_n_trees",
                "coverage_n_plots",
                "reason",
                "resumable",
                "error",
            ]
        )
    remaining.to_csv(remaining_path, index=False, lineterminator="\n")
    write_evidence_map(distribution, map_path, level=config.level)
    summary["outputs"] = {
        "distribution_csv": _display_path(distribution_path, config.repo_root),
        "height_csv": _display_path(height_path, config.repo_root),
        "summary_json": _display_path(summary_path, config.repo_root),
        "report_md": _display_path(report_path, config.repo_root),
        "evidence_map_png": _display_path(map_path, config.repo_root),
        "remaining_cells_csv": _display_path(remaining_path, config.repo_root),
        "cells_dir": _display_path(config.cells_dir, config.repo_root),
    }
    summary_path.write_text(
        json.dumps(_jsonable(summary), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    write_report(config, distribution, height, coverage, summary, report_path)
    outputs = [
        distribution_path,
        height_path,
        summary_path,
        report_path,
        remaining_path,
    ]
    if map_path.exists():
        outputs.append(map_path)
    manifest = {
        "artifact": "national_hierarchical_fit",
        "artifact_version": ARTIFACT_VERSION,
        "level": config.level,
        "support_levels": list(config.support_levels),
        "generated_at_utc": summary["generated_at_utc"],
        "code": {
            "parent_commit": _git_value(config.repo_root, "rev-parse", "HEAD"),
            "parent_branch": _git_value(config.repo_root, "branch", "--show-current"),
            "data_commit": _git_value(config.data_root, "rev-parse", "HEAD"),
            "data_branch": _git_value(config.data_root, "branch", "--show-current"),
        },
        "inputs": summary["inputs"],
        "prep": prep_metadata,
        "outputs": {
            _display_path(path, config.repo_root): _sha256(path)
            for path in outputs
            if path.exists()
        },
        "runtime_seconds": time.time() - started,
        "timebox_exhausted": summary["timebox_exhausted"],
        "remaining_cells": len(summary["remaining_cells"]),
    }
    manifest_path = config.out_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(_jsonable(manifest), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    summary["outputs"]["manifest_json"] = _display_path(manifest_path, config.repo_root)
    return manifest


def _update_metadata(config: FitConfig, summary: Mapping[str, Any]) -> None:
    config.metadata_path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "run_utc": summary["generated_at_utc"],
        "level": config.level,
        "support_levels": ";".join(config.support_levels),
        "cells": summary["cells"]["total"],
        "direct_fit": summary["cells"]["direct_fit"],
        "pooled_fit": summary["cells"]["pooled_fit"],
        "pooled_parent": summary["cells"]["pooled_parent"],
        "species_national": summary["cells"]["species_national"],
        "no_fit": summary["cells"]["no_fit"],
        "remaining_cells": len(summary["remaining_cells"]),
        "runtime_seconds": round(summary["runtime_seconds"], 1),
        "timebox_seconds": config.timebox_seconds,
        "timebox_exhausted": summary["timebox_exhausted"],
        "artifact_version": ARTIFACT_VERSION,
        "out_dir": _display_path(config.out_dir, config.repo_root),
    }
    if config.metadata_path.exists():
        existing = pd.read_csv(config.metadata_path, low_memory=False)
        frame = pd.concat([existing, pd.DataFrame([row])], ignore_index=True)
    else:
        frame = pd.DataFrame([row])
    frame.to_csv(config.metadata_path, index=False, lineterminator="\n")


def _input_summary(config: FitConfig) -> dict[str, Any]:
    paths = {
        "coverage": config.coverage_path,
        "sites": config.sites_path,
        "visits": config.visits_path,
        "trees": config.trees_path,
        "rsyc_ecostrat": config.ecostrat_path,
        "rsyc_models": config.models_path,
    }
    return {
        key: {
            "path": _display_path(path, config.repo_root),
            "sha256": _sha256(path),
            "bytes": path.stat().st_size,
        }
        for key, path in paths.items()
    }


def run(config: FitConfig) -> dict[str, Any]:
    """Execute one national fit pass and write its artifacts."""
    started = time.time()
    if config.level not in SUPPORT_LEVELS:
        raise ValueError(f"level must be one of {sorted(SUPPORT_LEVELS)}")
    if config.workers < 1:
        raise ValueError("workers must be at least 1")
    if config.timebox_seconds <= 0:
        raise ValueError("timebox_seconds must be positive")
    assert_no_unexpected_deletions(config.repo_root)
    config.out_dir.mkdir(parents=True, exist_ok=True)
    fit_config_hash = _fit_config_hash(config)
    _stage(config, f"starting {config.level} pass", started)

    coverage = load_coverage(config)
    trees, prep_metadata = prepare_tree_table(config)
    prep_seconds = time.time() - started
    _stage(config, f"prepared {len(trees):,} trees in {prep_seconds:.1f}s", started)
    inputs = _input_summary(config)
    if config.prepare_only:
        summary = {
            "artifact": "national_hierarchical_fit",
            "artifact_version": ARTIFACT_VERSION,
            "generated_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "level": config.level,
            "support_levels": list(config.support_levels),
            "prep_seconds": prep_seconds,
            "runtime_seconds": time.time() - started,
            "inputs": inputs,
            "prep": prep_metadata,
        }
        summary_path = config.out_dir / "national_evidence_summary.json"
        summary_path.write_text(
            json.dumps(_jsonable(summary), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(
            f"Prepared {len(trees):,} analysis trees in {prep_seconds:.1f}s; "
            f"cache {config.cache_dir}",
            file=sys.stderr,
        )
        return summary

    deadline = started + config.timebox_seconds
    fit_started = time.time()
    resumed_total = 0
    submitted_total = 0
    errors_total = 0
    timed_out = False
    for level in config.support_levels:
        if time.time() >= deadline:
            timed_out = True
            break
        submitted, resumed, errors, level_timeout = _fit_level(
            config,
            trees,
            coverage,
            level,
            fit_config_hash=fit_config_hash,
            deadline=deadline,
            started=started,
        )
        submitted_total += submitted
        resumed_total += resumed
        errors_total += errors
        timed_out = timed_out or level_timeout
        if config.max_cells is not None and submitted >= config.max_cells:
            # Smoke mode: the per-level cap has been honoured.  Stop before
            # moving to the next level so the run stays short.
            break
    fit_seconds = time.time() - fit_started

    rsyc_models = load_rsyc_prior_models(config.models_path)
    distribution, height, finalize_info = finalize_rows(
        config,
        coverage,
        fit_config_hash=fit_config_hash,
        rsyc_models=rsyc_models,
    )
    validation = validate_outputs(
        distribution,
        height,
        coverage,
        reference_age=config.reference_age,
    )
    evidence_by_level = _evidence_counts(distribution)
    target_rows = distribution[distribution["level"].eq(config.level)]
    cell_counts = {
        tier: int(target_rows["evidence_tier"].eq(tier).sum()) for tier in EVIDENCE_TIERS
    }
    cell_counts["total"] = len(target_rows)
    pending_count = sum(
        1 for row in finalize_info["remaining"] if row.get("reason") == "pending_timebox"
    )
    local_failed_count = sum(
        1 for row in finalize_info["remaining"] if row.get("reason") == "local_fit_failed"
    )
    unresolved_no_data = int(
        (
            target_rows["coverage_class"].eq("no_data")
            & target_rows["evidence_tier"].eq("no_fit")
        ).sum()
    )
    summary = {
        "artifact": "national_hierarchical_fit",
        "artifact_version": ARTIFACT_VERSION,
        "generated_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "level": config.level,
        "support_levels": list(config.support_levels),
        "runtime_seconds": time.time() - started,
        "prep_seconds": prep_seconds,
        "fit_seconds": fit_seconds,
        "timebox_seconds": config.timebox_seconds,
        "timebox_exhausted": bool(timed_out),
        "workers": config.workers,
        "fit_config_hash": fit_config_hash,
        "thresholds": {
            "coverage_direct_min_trees": 100,
            "coverage_direct_min_plots": 5,
            "coverage_pooled_min_trees": 30,
            "coverage_pooled_max_trees": 99,
            "coverage_pooled_min_plots": 2,
            "coverage_pooled_max_plots": 4,
            "local_direct_min_trees": MIN_DIRECT_TREES,
            "local_direct_min_plots": MIN_DIRECT_PLOTS,
            "local_pooled_min_trees": MIN_POOLED_TREES,
            "local_pooled_min_plots": MIN_POOLED_PLOTS,
            "min_height_obs": MIN_HEIGHT_OBS,
            "bin_width_cm": config.bin_width_cm,
            "reference_age": config.reference_age,
        },
        "cells": {
            "total": len(target_rows),
            "direct_fit": cell_counts["direct_fit"],
            "pooled_fit": cell_counts["pooled_fit"],
            "pooled_parent": cell_counts["pooled_parent"],
            "species_national": cell_counts["species_national"],
            "no_fit": cell_counts["no_fit"],
            "submitted": submitted_total,
            "resumed": resumed_total,
            "worker_errors": errors_total,
            "pending_timebox": int(pending_count),
            "local_fit_failed": int(local_failed_count),
            "no_data_unresolved": unresolved_no_data,
        },
        "evidence_by_level": evidence_by_level,
        "top_direct_fits": _top_fits(distribution, "direct_fit"),
        "top_pooled_fits": _top_fits(distribution, "pooled_fit"),
        "validation": validation,
        "rsyc_prior": validation["checks"]["rsyc_prior"],
        "inputs": inputs,
        "prep": prep_metadata,
        "remaining_cells": finalize_info["remaining"],
    }
    manifest = _write_outputs(
        config,
        distribution,
        height,
        coverage,
        summary,
        prep_metadata=prep_metadata,
        started=started,
    )
    _update_metadata(config, summary)
    summary["manifest"] = manifest
    assert_no_unexpected_deletions(config.repo_root)
    print(
        f"Wrote {summary['cells']['total']:,} {config.level} cells "
        f"({summary['cells']['direct_fit']} direct, "
        f"{summary['cells']['pooled_fit']} pooled, "
        f"{summary['cells']['pooled_parent']} parent, "
        f"{summary['cells']['species_national']} species-national, "
        f"{summary['cells']['no_fit']} no_fit); "
        f"{len(summary['remaining_cells'])} remaining; "
        f"runtime {summary['runtime_seconds']:.1f}s",
        file=sys.stderr,
    )
    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path.cwd(),
        help="Parent repository root (default: current directory).",
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        default=None,
        help="Data submodule root (default: <repo-root>/data).",
    )
    parser.add_argument(
        "--level",
        choices=sorted(SUPPORT_LEVELS),
        default="ecoprovince",
        help="Target ecological level (default: ecoprovince).",
    )
    parser.add_argument(
        "--out",
        "--out-dir",
        dest="out_dir",
        type=Path,
        default=None,
        help="Output directory (default: data/derived/national/assortments/national_fit).",
    )
    parser.add_argument(
        "--metadata-path",
        type=Path,
        default=None,
        help="Metadata CSV path (default: metadata/national-fit.csv).",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        help="Analysis-tree cache directory (default: <repo-root>/tmp/national_fit_cache).",
    )
    parser.add_argument("--workers", type=int, default=8, help="Worker processes.")
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Reuse completed cell partials from earlier runs.",
    )
    parser.add_argument(
        "--timebox-seconds",
        type=float,
        default=780.0,
        help="Stop dispatching new cells after this many seconds (default: 780).",
    )
    parser.add_argument(
        "--max-cells",
        type=int,
        default=None,
        help="Optional smoke limit on dispatched direct/pooled cells.",
    )
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="Build the analysis-tree cache and exit.",
    )
    parser.add_argument(
        "--rebuild-cache",
        action="store_true",
        help="Rebuild the analysis-tree cache even when it matches.",
    )
    parser.add_argument("--verbose", action="store_true", help="Print stage timings.")
    parser.add_argument(
        "--random-state",
        type=int,
        default=RANDOM_STATE,
        help="Deterministic seed passed to the candidate fits.",
    )
    parser.add_argument(
        "--bin-width-cm",
        type=float,
        default=BIN_WIDTH_CM,
        help="DBH histogram bin width in cm.",
    )
    parser.add_argument(
        "--reference-age",
        type=float,
        default=REFERENCE_AGE,
        help="Age used to evaluate the RSYC scale prior.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    repo_root = Path(args.repo_root).resolve()
    data_root = (
        Path(args.data_root) if args.data_root else repo_root / "data"
    ).resolve()
    out_dir = (
        Path(args.out_dir)
        if args.out_dir
        else data_root / "derived" / "national" / "assortments" / "national_fit"
    ).resolve()
    metadata_path = (
        Path(args.metadata_path)
        if args.metadata_path
        else repo_root / "metadata" / "national-fit.csv"
    ).resolve()
    cache_dir = (
        Path(args.cache_dir)
        if args.cache_dir
        else repo_root / "tmp" / "national_fit_cache"
    ).resolve()
    config = FitConfig(
        repo_root=repo_root,
        data_root=data_root,
        out_dir=out_dir,
        metadata_path=metadata_path,
        cache_dir=cache_dir,
        level=args.level,
        workers=int(args.workers),
        resume=bool(args.resume),
        timebox_seconds=float(args.timebox_seconds),
        max_cells=None if args.max_cells is None else int(args.max_cells),
        prepare_only=bool(args.prepare_only),
        rebuild_cache=bool(args.rebuild_cache),
        verbose=bool(args.verbose),
        random_state=int(args.random_state),
        bin_width_cm=float(args.bin_width_cm),
        reference_age=float(args.reference_age),
    )
    run(config)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
