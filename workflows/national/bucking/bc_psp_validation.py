"""Full-population BC PSP TSA24 bucking validation.

This module is deliberately narrower than a national model.  It takes the
BC FAIB PSP TSA24 tree list at the latest *live visit per site*, runs the
published BC Kozak taper and the existing TIPSY interior dimension sorts,
and compares the recovered log and stem-residue volumes with the compiled
PSP whole-stem and merchantable volume banks at 7.5 cm and 12.5 cm
utilization.

Key distinctions from the Phase 0 pilot in ``validate_tsa24.py``:

* the population is the full latest-visit TSA24 tree list, not a 20-plot
  sample;
* the visit key is ``SITE_IDENTIFIER``; FAIB ``CLSTR_ID`` changes with each
  remeasurement (``...-PSP1``, ``...-PSP2``), so selecting the latest
  ``CLSTR_ID`` would retain every visit;
* volume comparison is paired against the published compiled PSP banks,
  with expansion factors applied per tree;
* residue is stem residue only.  Branches and foliage are outside the taper
  and are not estimated from the stem ledger.

No official BC grade is assigned.  The sort rules are dimension-only and
the output must not be described as a grade or national validation.
"""

from __future__ import annotations

import argparse
import json
import math
import multiprocessing
import os
import subprocess
import sys
import time
import warnings
from collections.abc import Iterable, Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .bucking import (
    BUCKER_BALANCE_ABS_TOL,
    BUCKER_BALANCE_REL_TOL,
    buck_tree,
    load_grade_rules,
    load_price_profile,
)
from .schema import TreeRecord
from .taper import (
    TaperDomainError,
    TaperRegistry,
    UnsupportedTaperError,
    default_kozak_parameter_path,
    default_nemora_root,
)
from .tree_lists import FaibTreeList
from .validate_tsa24 import (
    Deadline,
    TimeboxExceeded,
    _fmt,
    _hash_record,
    _jsonable,
    _markdown_table,
    _run_rsyc,
    _sha256,
    assert_no_unexpected_deletions,
)
from .validate_tsa24_btc import (
    DEFAULT_CHIP_YIELD,
    DEFAULT_MOISTURE_GREEN,
    DEFAULT_MOISTURE_SEASONED,
    load_basic_density_table,
)

__all__ = [
    "BCPSPValidationError",
    "Population",
    "build_parser",
    "comparison_metrics",
    "load_tsa24_latest_visit",
    "main",
    "run",
]

TSA_NUMBER = 24
TSA_NAME = "Prince George"
VALIDATION_NAME = "bc_psp_tsa24_bucking_validation_v0"
UTILIZATIONS: tuple[float, ...] = (7.5, 12.5)
MIN_DBH_CM = 2.0
GENERIC_DENSITY_ODT_M3 = 0.45
CONSERVATION_ABS_TOL = 1e-8
CONSERVATION_REL_TOL = 1e-7
COMPILED_ARITHMETIC_TOL = 0.01
DEFAULT_TIMEBOX_SECONDS = 1200.0
DEFAULT_WORKERS = min(32, os.cpu_count() or 1)

_TREE_COLUMNS = (
    "SITE_IDENTIFIER",
    "CLSTR_ID",
    "VISIT_NUMBER",
    "PLOT",
    "TREE_NO",
    "SPECIES",
    "DBH",
    "HEIGHT",
    "LV_D",
    "PHF_TREE",
    "VOL_MER",
    "VOL_WSV",
    "AGE_TOT",
    "HEIGHT_SOURCE",
)

_COMPILED_COLUMNS = (
    "VHA_WSV_LIV",
    "VHA_MER_LIV",
    "VHA_DWB_LIV",
    "STEMS_HA_LIV",
)


class BCPSPValidationError(RuntimeError):
    """Raised when the validation cannot be completed without hiding a defect."""


@dataclass(frozen=True, slots=True)
class Population:
    """Parsed, trusted latest-live-visit TSA24 tree population."""

    trees: tuple[TreeRecord, ...]
    tree_context: pd.DataFrame
    plots: pd.DataFrame
    stats: dict[str, Any]


@dataclass(frozen=True, slots=True)
class BuckingOutputs:
    logs: pd.DataFrame
    residue: pd.DataFrame
    skipped: pd.DataFrame
    models: tuple[dict[str, Any], ...]
    taper_warnings: tuple[str, ...]
    elapsed_seconds: float


# ---------------------------------------------------------------------------
# Parsing boundary
# ---------------------------------------------------------------------------


def _clean_id(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, (int, np.integer)):
        return str(int(value))
    if isinstance(value, (float, np.floating)) and float(value).is_integer():
        return str(int(value))
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return None
    return text


def _numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def _finite(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _text_or_none(value: Any) -> str | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return text or None


def _height_evidence(source: Any) -> str:
    text = str(source).strip().lower() if source is not None else ""
    if "field" in text:
        return "field_measured"
    if "dbh" in text or "estimate" in text:
        return "dbh_estimated"
    return "unknown"


def _species_crosswalk(path: Path) -> dict[str, dict[str, Any]]:
    frame = pd.read_csv(path, dtype=str)
    required = {"psp_species", "group", "known"}
    missing = required.difference(frame.columns)
    if missing:
        raise BCPSPValidationError(
            f"species crosswalk missing columns: {', '.join(sorted(missing))}"
        )
    mapping: dict[str, dict[str, Any]] = {}
    for row in frame.itertuples(index=False):
        code = str(row.psp_species).strip().upper()
        if not code:
            continue
        mapping[code] = {
            "group": str(row.group).strip() if pd.notna(row.group) else "UNKNOWN",
            "known": str(row.known).strip().lower() == "true",
        }
    return mapping


def load_tsa24_latest_visit(
    data_root: Path,
    *,
    tsa_name: str = TSA_NAME,
    species_crosswalk_path: Path | None = None,
) -> Population:
    """Load live TSA24 trees at the latest visit with live trees per site.

    ``CLSTR_ID`` is a visit identifier, not a site identifier.  The selection
    below groups by ``SITE_IDENTIFIER`` and chooses the latest
    ``(MEAS_YR, VISIT_NUMBER)`` with at least one valid live tree.
    """
    base = Path(data_root) / "bc_faib_ground_plots" / "psp"
    header_path = base / "faib_header.csv.gz"
    visit_path = base / "faib_sample_byvisit.csv.gz"
    tree_path = base / "faib_tree_detail.csv.gz"
    density_path = base / "faib_compiled_spcsmries_siteage.csv.gz"
    for path in (header_path, visit_path, tree_path):
        if not path.exists():
            raise FileNotFoundError(f"FAIB PSP input not found: {path}")

    header = pd.read_csv(
        header_path,
        usecols=[
            "SITE_IDENTIFIER",
            "TSA_DESC",
            "BEC_ZONE",
            "BEC_SBZ",
            "Latitude",
            "Longitude",
        ],
        low_memory=False,
    )
    header["site_id"] = header["SITE_IDENTIFIER"].map(_clean_id)
    tsa_mask = header["TSA_DESC"].astype(str).str.contains(tsa_name, case=False, na=False)
    selected_header = header.loc[tsa_mask].drop_duplicates("site_id").copy()
    if selected_header.empty:
        raise BCPSPValidationError(
            f"no FAIB PSP sites matched TSA_DESC containing {tsa_name!r}"
        )
    site_ids = set(selected_header["site_id"].dropna())

    visits = pd.read_csv(
        visit_path,
        usecols=[
            "SITE_IDENTIFIER",
            "CLSTR_ID",
            "VISIT_NUMBER",
            "MEAS_YR",
            "PROJ_AGE_1",
            "NO_PLOTS",
            "SAMP_TYP",
            "SAMPLE_SITE_PURPOSE_TYPE_CODE",
        ],
        low_memory=False,
    )
    visits["site_id"] = visits["SITE_IDENTIFIER"].map(_clean_id)
    visits["plot_id"] = visits["CLSTR_ID"].map(_clean_id)
    visits = visits[visits["site_id"].isin(site_ids)].copy()
    visits["VISIT_NUMBER"] = _numeric(visits["VISIT_NUMBER"]).astype("Int64")
    visits["MEAS_YR"] = _numeric(visits["MEAS_YR"])
    visits["PROJ_AGE_1"] = _numeric(visits["PROJ_AGE_1"])
    visits = visits.dropna(subset=["plot_id", "VISIT_NUMBER"])

    trees = pd.read_csv(tree_path, usecols=list(_TREE_COLUMNS), low_memory=False)
    trees["site_id"] = trees["SITE_IDENTIFIER"].map(_clean_id)
    trees["plot_id"] = trees["CLSTR_ID"].map(_clean_id)
    trees["VISIT_NUMBER"] = _numeric(trees["VISIT_NUMBER"]).astype("Int64")
    trees = trees[trees["site_id"].isin(site_ids)].copy()

    for column in ("DBH", "HEIGHT", "PHF_TREE", "VOL_MER", "VOL_WSV", "AGE_TOT"):
        trees[column] = _numeric(trees[column])
    trees["species"] = trees["SPECIES"].astype(str).str.strip().str.upper()
    trees.loc[trees["SPECIES"].isna(), "species"] = None

    stats: dict[str, Any] = {
        "tsa_name": tsa_name,
        "input_sites": len(header),
        "selected_sites": len(site_ids),
        "tsa_tree_rows": len(trees),
        "dropped": {},
    }
    dropped: dict[str, int] = {}
    dropped["dead"] = int((trees["LV_D"].astype(str).str.upper() != "L").sum())
    live = trees[trees["LV_D"].astype(str).str.upper() == "L"].copy()
    dropped["invalid_dbh"] = int((live["DBH"] <= 0).sum())
    dropped["short_height"] = int((live["HEIGHT"] <= 1.3).sum())
    dropped["invalid_expansion"] = int((live["PHF_TREE"] <= 0).sum())
    dropped["missing_species"] = int(live["species"].isna().sum())
    valid = live[
        (live["DBH"] >= MIN_DBH_CM)
        & (live["HEIGHT"] > 1.3)
        & (live["PHF_TREE"] > 0)
        & live["species"].notna()
    ].copy()
    dropped["below_min_dbh"] = int(
        (live["DBH"] < MIN_DBH_CM).sum()
    )
    stats["valid_live_rows_all_visits"] = len(valid)
    valid_keys = valid[["site_id", "plot_id", "VISIT_NUMBER"]].drop_duplicates()
    valid_keys = valid_keys.merge(
        visits[
            [
                "site_id",
                "plot_id",
                "VISIT_NUMBER",
                "MEAS_YR",
                "PROJ_AGE_1",
                "NO_PLOTS",
                "SAMP_TYP",
                "SAMPLE_SITE_PURPOSE_TYPE_CODE",
            ]
        ],
        on=["site_id", "plot_id", "VISIT_NUMBER"],
        how="left",
    )
    if valid_keys["MEAS_YR"].isna().any():
        missing = valid_keys.loc[valid_keys["MEAS_YR"].isna(), "plot_id"].nunique()
        raise BCPSPValidationError(
            f"{missing} tree plots could not be matched to a visit measurement year"
        )

    latest_per_site = (
        valid_keys.sort_values(["site_id", "MEAS_YR", "VISIT_NUMBER"])
        .drop_duplicates("site_id", keep="last")[["site_id", "VISIT_NUMBER"]]
    )
    latest_all_visits = (
        visits.sort_values(["site_id", "MEAS_YR", "VISIT_NUMBER"])
        .drop_duplicates("site_id", keep="last")
        .set_index("site_id")["VISIT_NUMBER"]
    )
    latest_live_lookup = latest_per_site.set_index("site_id")["VISIT_NUMBER"]
    fallback_sites = int(
        sum(
            int(latest_all_visits.get(site, -1)) != int(visit)
            for site, visit in latest_live_lookup.items()
        )
    )
    chosen_visits = visits.merge(
        latest_per_site, on=["site_id", "VISIT_NUMBER"], how="inner"
    )
    if chosen_visits.empty:
        raise BCPSPValidationError("no TSA24 latest live visits were selected")

    selected = valid.merge(
        chosen_visits[["site_id", "plot_id", "VISIT_NUMBER"]],
        on=["site_id", "plot_id", "VISIT_NUMBER"],
        how="inner",
    )
    if selected.empty:
        raise BCPSPValidationError("latest live visits contain no valid live trees")
    dropped["not_latest_live_visit"] = int(len(valid) - len(selected))

    tree_header = selected_header[
        ["site_id", "BEC_ZONE", "BEC_SBZ", "Latitude", "Longitude"]
    ].rename(
        columns={
            "BEC_ZONE": "bec_zone",
            "BEC_SBZ": "bec_subzone",
            "Latitude": "latitude",
            "Longitude": "longitude",
        }
    )
    selected = selected.merge(tree_header, on="site_id", how="left")
    selected["bec_zone"] = selected["bec_zone"].astype(str).str.strip().str.upper()
    dropped["missing_bec"] = int(
        selected["bec_zone"].isin({"", "NAN", "NONE"}).sum()
    )
    selected = selected[~selected["bec_zone"].isin({"", "NAN", "NONE"})].copy()

    plot_age = (
        chosen_visits.drop_duplicates("plot_id")
        .set_index("plot_id")["PROJ_AGE_1"]
        .to_dict()
    )
    plot_site_index: dict[str, float] = {}
    if density_path.exists():
        siteage = pd.read_csv(
            density_path,
            usecols=["CLSTR_ID", "SI1", "SI2", "SI3"],
            low_memory=False,
        )
        siteage["plot_id"] = siteage["CLSTR_ID"].map(_clean_id)
        siteage = siteage[siteage["plot_id"].isin(set(chosen_visits["plot_id"]))]
        for column in ("SI1", "SI2", "SI3"):
            siteage[column] = _numeric(siteage[column])
        siteage["site_index"] = siteage[["SI1", "SI2", "SI3"]].max(axis=1)
        siteage = siteage[siteage["site_index"] > 0]
        plot_site_index = (
            siteage.groupby("plot_id")["site_index"].max().to_dict()
        )

    crosswalk_path = species_crosswalk_path
    if crosswalk_path is None:
        crosswalk_path = (
            default_nemora_root() / "config" / "species" / "bc_psp_to_group.csv"
        )
    crosswalk = _species_crosswalk(crosswalk_path) if Path(crosswalk_path).exists() else {}
    if not crosswalk:
        stats.setdefault("warnings", []).append(
            f"species crosswalk not found at {crosswalk_path}; species_group set to UNKNOWN"
        )

    records: list[TreeRecord] = []
    context_rows: list[dict[str, Any]] = []
    for row in selected.itertuples(index=False):
        species = str(row.species).strip().upper()
        mapping = crosswalk.get(species, {"group": "UNKNOWN", "known": False})
        plot_id = _clean_id(row.plot_id)
        visit_number = _clean_id(row.VISIT_NUMBER)
        plot_number = _clean_id(row.PLOT)
        tree_number = _clean_id(row.TREE_NO)
        tree_id = ":".join(
            part
            for part in (str(row.site_id), plot_id, visit_number, plot_number, tree_number)
            if part
        )
        stand_age = _finite(plot_age.get(plot_id))
        age = _finite(row.AGE_TOT)
        if age is None:
            age = stand_age
        height_evidence = _height_evidence(row.HEIGHT_SOURCE)
        record = TreeRecord(
            tree_id=tree_id,
            site_id=str(row.site_id),
            species=species,
            dbh_cm=float(row.DBH),
            height_m=float(row.HEIGHT),
            status="live",
            expansion_factor=float(row.PHF_TREE),
            bec_zone=str(row.bec_zone),
            age=age,
            provenance=(
                "faib_psp:tree_detail+header+sample_byvisit; "
                f"TSA24={tsa_name}; latest live visit per SITE_IDENTIFIER"
            ),
            plot_id=plot_id,
            visit_id=f"{plot_id}:{visit_number}" if visit_number else None,
            species_group=str(mapping["group"]),
            bec_subzone=_text_or_none(row.bec_subzone),
            site_index=_finite(plot_site_index.get(plot_id)),
            stand_age=stand_age,
            observed_merchantable_m3=_finite(row.VOL_MER),
            observed_whole_stem_m3=_finite(row.VOL_WSV),
            latitude=_finite(row.latitude),
            longitude=_finite(row.longitude),
        )
        records.append(record)
        context_rows.append(
            {
                "tree_id": tree_id,
                "site_id": str(row.site_id),
                "plot_id": plot_id,
                "visit_id": record.visit_id,
                "visit_number": int(row.VISIT_NUMBER),
                "plot_number": plot_number,
                "tree_number": tree_number,
                "species": species,
                "species_group": str(mapping["group"]),
                "species_known": bool(mapping["known"]),
                "bec_zone": str(row.bec_zone),
                "bec_subzone": record.bec_subzone,
                "dbh_cm": float(row.DBH),
                "height_m": float(row.HEIGHT),
                "height_source": _text_or_none(row.HEIGHT_SOURCE),
                "height_evidence": height_evidence,
                "expansion_factor": float(row.PHF_TREE),
                "age": age,
                "stand_age": stand_age,
                "site_index": record.site_index,
                "latitude": record.latitude,
                "longitude": record.longitude,
                "observed_merchantable_m3": record.observed_merchantable_m3,
                "observed_whole_stem_m3": record.observed_whole_stem_m3,
            }
        )

    tree_context = pd.DataFrame(context_rows)
    if tree_context["tree_id"].duplicated().any():
        duplicates = int(tree_context["tree_id"].duplicated().sum())
        raise BCPSPValidationError(f"duplicate tree_id values in selected population: {duplicates}")

    plot_rows: list[dict[str, Any]] = []
    for plot_id, group in tree_context.groupby("plot_id", sort=True):
        first = group.iloc[0]
        species_counts = group["species"].value_counts()
        plot_rows.append(
            {
                "plot_id": plot_id,
                "site_id": str(first["site_id"]),
                "bec_zone": str(first["bec_zone"]),
                "bec_subzone": first["bec_subzone"],
                "latitude": first["latitude"],
                "longitude": first["longitude"],
                "stand_age": first["stand_age"],
                "site_index": first["site_index"],
                "n_live": len(group),
                "leading_species": str(species_counts.index[0]) if len(species_counts) else None,
                "species_counts": ";".join(
                    f"{code}:{count}" for code, count in species_counts.items()
                ),
            }
        )
    plots = pd.DataFrame(plot_rows)
    stats.update(
        {
            "selected_sites": int(chosen_visits["site_id"].nunique()),
            "selected_visits": int(
                chosen_visits[["site_id", "VISIT_NUMBER"]].drop_duplicates().shape[0]
            ),
            "selected_plots": int(chosen_visits["plot_id"].nunique()),
            "selected_tree_rows": len(selected),
            "selected_live_trees": len(tree_context),
            "plots_with_live_trees": len(plots),
            "latest_live_visit_fallback_sites": fallback_sites,
            "one_plot_per_site": bool(
                chosen_visits["site_id"].nunique() == chosen_visits["plot_id"].nunique()
            ),
            "samp_types": sorted(visits["SAMP_TYP"].dropna().astype(str).unique()),
            "sample_purposes": sorted(
                visits["SAMPLE_SITE_PURPOSE_TYPE_CODE"].dropna().astype(str).unique()
            ),
            "species_crosswalk": Path(crosswalk_path).name,
            "dropped": dropped,
        }
    )
    stats["dbh"] = {
        "min": float(tree_context["dbh_cm"].min()),
        "median": float(tree_context["dbh_cm"].median()),
        "max": float(tree_context["dbh_cm"].max()),
        "n_ge_7_5": int((tree_context["dbh_cm"] >= 7.5).sum()),
        "n_ge_12_5": int((tree_context["dbh_cm"] >= 12.5).sum()),
    }
    stats["species"] = (
        tree_context.groupby("species")
        .agg(n=("tree_id", "size"))
        .sort_values("n", ascending=False)["n"]
        .to_dict()
    )
    return Population(
        trees=tuple(records),
        tree_context=tree_context,
        plots=plots,
        stats=stats,
    )


# ---------------------------------------------------------------------------
# Bucking workers
# ---------------------------------------------------------------------------

_WORKER: dict[str, Any] = {}


def _init_bucking_worker(
    parameter_path: str,
    rules_path: str,
    price_path: str,
    density_by_species: dict[str, float],
) -> None:
    _WORKER.clear()
    _WORKER["registry"] = TaperRegistry(parameter_path=Path(parameter_path))
    _WORKER["rules"] = load_grade_rules(Path(rules_path))
    _WORKER["prices"] = load_price_profile(Path(price_path))
    _WORKER["density"] = dict(density_by_species)


def _density_for(species: str) -> tuple[float, str]:
    density = _WORKER["density"].get(str(species).upper())
    if density is None:
        return GENERIC_DENSITY_ODT_M3, "generic_assumption"
    return float(density), "species_table"


def _buck_one(tree: TreeRecord) -> dict[str, Any]:
    """Bucket one stem in a worker; return plain rows, never mutate globals."""
    registry: TaperRegistry = _WORKER["registry"]
    rules = _WORKER["rules"]
    prices = _WORKER["prices"]
    density, density_evidence = _density_for(tree.species)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            model = registry.resolve("BC", tree.species, tree.bec_zone)
            logs, residue = buck_tree(
                tree,
                model,
                rules,
                prices,
                basic_density_odt_m3=density,
                moisture_wet_basis=DEFAULT_MOISTURE_GREEN,
                chip_yield_fraction=DEFAULT_CHIP_YIELD,
            )
        except (UnsupportedTaperError, TaperDomainError, ValueError) as exc:
            return {
                "status": "skipped",
                "tree_id": tree.tree_id,
                "species": tree.species,
                "bec_zone": tree.bec_zone,
                "dbh_cm": tree.dbh_cm,
                "height_m": tree.height_m,
                "expansion_factor": tree.expansion_factor,
                "observed_whole_stem_m3": tree.observed_whole_stem_m3,
                "reason": f"{type(exc).__name__}: {exc}",
                "warnings": sorted(
                    {f"{warning.category.__name__}: {warning.message}" for warning in caught}
                ),
            }
    log_rows = []
    for log in logs:
        row = log.to_dict()
        row["plot_id"] = tree.plot_id
        row["bec_zone"] = tree.bec_zone
        log_rows.append(row)
    residue_row = residue.to_dict()
    residue_row["plot_id"] = tree.plot_id
    residue_row["species"] = tree.species
    residue_row["bec_zone"] = tree.bec_zone
    residue_row["dbh_cm"] = tree.dbh_cm
    residue_row["height_m"] = tree.height_m
    residue_row["expansion_factor"] = tree.expansion_factor
    residue_row["density_evidence"] = density_evidence
    return {
        "status": "bucked",
        "tree_id": tree.tree_id,
        "species": tree.species,
        "bec_zone": tree.bec_zone,
        "density_odt_m3": density,
        "density_evidence": density_evidence,
        "logs": log_rows,
        "residue": residue_row,
        "model": model.manifest_record(),
        "warnings": sorted(
            {f"{warning.category.__name__}: {warning.message}" for warning in caught}
        ),
    }


def _density_map(density_table: Mapping[str, Mapping[str, Any]]) -> dict[str, float]:
    return {
        str(code).upper(): float(entry["basic_density_odt_m3"])
        for code, entry in density_table.items()
    }


def buck_population(
    population: Population,
    registry: TaperRegistry,
    *,
    parameter_path: Path,
    rules_path: Path,
    price_path: Path,
    density_table: Mapping[str, Mapping[str, Any]],
    workers: int,
    deadline: Deadline,
) -> BuckingOutputs:
    """Buck every taper-supported tree, parallelised but deterministically ordered."""
    started = time.monotonic()
    supported = set(registry.supported_species)
    buckable = [tree for tree in population.trees if tree.species in supported]
    unsupported = [tree for tree in population.trees if tree.species not in supported]
    if not buckable:
        raise BCPSPValidationError("selected population contains no taper-supported trees")

    skip_rows: list[dict[str, Any]] = []
    for tree in unsupported:
        skip_rows.append(
            {
                "tree_id": tree.tree_id,
                "site_id": tree.site_id,
                "plot_id": tree.plot_id,
                "species": tree.species,
                "bec_zone": tree.bec_zone,
                "dbh_cm": tree.dbh_cm,
                "height_m": tree.height_m,
                "expansion_factor": tree.expansion_factor,
                "observed_whole_stem_m3": tree.observed_whole_stem_m3,
                "reason": "unsupported_taper_species",
            }
        )

    density_by_species = _density_map(density_table)
    tree_lookup = {tree.tree_id: tree for tree in buckable}
    workers = max(1, min(int(workers), len(buckable)))
    results: list[dict[str, Any]] = []
    if workers == 1:
        _init_bucking_worker(
            str(parameter_path), str(rules_path), str(price_path), density_by_species
        )
        for index, tree in enumerate(buckable, start=1):
            results.append(_buck_one(tree))
            if index % 5000 == 0:
                deadline.check("bucking")
    else:
        context = multiprocessing.get_context("fork")
        with ProcessPoolExecutor(
            max_workers=workers,
            mp_context=context,
            initializer=_init_bucking_worker,
            initargs=(
                str(parameter_path),
                str(rules_path),
                str(price_path),
                density_by_species,
            ),
        ) as executor:
            iterator = executor.map(_buck_one, buckable, chunksize=128)
            for index, result in enumerate(iterator, start=1):
                results.append(result)
                if index % 5000 == 0:
                    deadline.check("bucking")

    log_rows: list[dict[str, Any]] = []
    residue_rows: list[dict[str, Any]] = []
    model_records: dict[tuple[str, str], dict[str, Any]] = {}
    warning_set: set[str] = set()
    for result in results:
        warning_set.update(result.get("warnings") or [])
        if result["status"] != "bucked":
            source_tree = tree_lookup.get(result["tree_id"])
            skip_rows.append(
                {
                    "tree_id": result["tree_id"],
                    "site_id": source_tree.site_id if source_tree else None,
                    "plot_id": source_tree.plot_id if source_tree else None,
                    "species": result["species"],
                    "bec_zone": result["bec_zone"],
                    "dbh_cm": result["dbh_cm"],
                    "height_m": result["height_m"],
                    "expansion_factor": result["expansion_factor"],
                    "observed_whole_stem_m3": result["observed_whole_stem_m3"],
                    "reason": result["reason"],
                }
            )
            continue
        log_rows.extend(result["logs"])
        residue_rows.append(result["residue"])
        model = result["model"]
        model_records[(str(model["species"]), str(model["bec_zone"]))] = model

    logs = pd.DataFrame(log_rows)
    if logs.empty:
        logs = pd.DataFrame(
            columns=[
                "tree_id",
                "site_id",
                "plot_id",
                "species",
                "bec_zone",
                "log_index",
                "length_m",
                "large_end_dib_cm",
                "small_end_dib_cm",
                "volume_m3",
                "sort_code",
                "product",
                "price_cad_m3",
                "value_cad",
                "evidence",
                "species_market_group",
                "sort_basis",
                "grade_status",
                "price_period",
                "price_source",
            ]
        )
    logs = logs.sort_values(["tree_id", "log_index"]).reset_index(drop=True)
    residue = pd.DataFrame(residue_rows).sort_values("tree_id").reset_index(drop=True)
    skipped = pd.DataFrame(skip_rows)
    if not skipped.empty:
        skipped = skipped.sort_values("tree_id").reset_index(drop=True)
    elapsed = time.monotonic() - started
    if residue.empty:
        raise BCPSPValidationError("bucking produced no residue ledger rows")
    return BuckingOutputs(
        logs=logs,
        residue=residue,
        skipped=skipped,
        models=tuple(model_records[key] for key in sorted(model_records)),
        taper_warnings=tuple(sorted(warning_set)),
        elapsed_seconds=elapsed,
    )


# ---------------------------------------------------------------------------
# Tree and comparison tables
# ---------------------------------------------------------------------------


def build_tree_ledger(
    population: Population,
    bucking: BuckingOutputs,
    density_table: Mapping[str, Mapping[str, Any]],
) -> pd.DataFrame:
    """Join selection context, recovered logs, and residue into one ledger."""
    context = population.tree_context.copy()
    log_agg = (
        bucking.logs.groupby("tree_id")
        .agg(recovered_log_m3=("volume_m3", "sum"), n_logs=("volume_m3", "size"))
        .reset_index()
        if not bucking.logs.empty
        else pd.DataFrame(columns=["tree_id", "recovered_log_m3", "n_logs"])
    )
    residue_cols = [
        "tree_id",
        "residue_volume_m3",
        "basic_density_odt_m3",
        "residue_odt",
        "green_tonnes",
        "chip_green_tonnes",
        "recovered_log_m3",
        "taper_total_m3",
        "bucker_residue_m3",
        "bucker_balance_error_m3",
        "evidence",
        "density_evidence",
    ]
    residue = bucking.residue[residue_cols].rename(
        columns={
            "residue_volume_m3": "stem_residue_m3",
            "basic_density_odt_m3": "density_odt_m3",
            "recovered_log_m3": "residue_recovered_log_m3",
            "taper_total_m3": "taper_total_m3",
            "evidence": "residue_evidence",
        }
    )
    ledger = context.merge(log_agg, on="tree_id", how="left").merge(
        residue, on="tree_id", how="left"
    )
    skipped_reason = (
        bucking.skipped.set_index("tree_id")["reason"].to_dict()
        if not bucking.skipped.empty
        else {}
    )
    ledger["bucked_status"] = np.where(
        ledger["tree_id"].isin(set(residue["tree_id"])), "bucked", "skipped"
    )
    ledger["skip_reason"] = ledger["tree_id"].map(skipped_reason)
    ledger["skip_reason"] = ledger["skip_reason"].fillna("")
    ledger.loc[ledger["bucked_status"] == "bucked", "skip_reason"] = ""
    for column in (
        "n_logs",
        "recovered_log_m3",
        "stem_residue_m3",
        "density_odt_m3",
        "residue_odt",
        "green_tonnes",
        "chip_green_tonnes",
        "taper_total_m3",
        "bucker_residue_m3",
        "bucker_balance_error_m3",
    ):
        ledger[column] = pd.to_numeric(ledger[column], errors="coerce").fillna(0.0)
    ledger["n_logs"] = ledger["n_logs"].astype(int)
    ledger["residue_evidence"] = ledger["residue_evidence"].fillna("not_applicable")
    ledger["density_evidence"] = ledger["density_evidence"].fillna("not_applicable")
    ledger["residue_share"] = np.where(
        ledger["taper_total_m3"] > 0,
        ledger["stem_residue_m3"] / ledger["taper_total_m3"],
        0.0,
    )
    ledger["taper_evidence"] = "published_table"
    ledger["sort_basis"] = "dimension_only"
    ledger["grade_status"] = "not_assigned"
    ledger["branch_foliage_included"] = False
    ledger["util_7_5"] = ledger["dbh_cm"] >= 7.5
    ledger["util_12_5"] = ledger["dbh_cm"] >= 12.5
    for source, target in (
        ("taper_total_m3", "taper_total_m3_ha"),
        ("recovered_log_m3", "recovered_log_m3_ha"),
        ("stem_residue_m3", "stem_residue_m3_ha"),
        ("residue_odt", "residue_odt_ha"),
        ("green_tonnes", "green_tonnes_ha"),
        ("chip_green_tonnes", "chip_green_tonnes_ha"),
        ("observed_whole_stem_m3", "observed_whole_stem_m3_ha"),
        ("observed_merchantable_m3", "observed_merchantable_m3_ha"),
    ):
        ledger[target] = pd.to_numeric(ledger[source], errors="coerce").fillna(0.0) * ledger[
            "expansion_factor"
        ]
    ledger = ledger.sort_values("tree_id").reset_index(drop=True)
    return ledger


def load_compiled_volumes(
    data_root: Path, plot_ids: Iterable[str]
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Load compiled PSP volume banks for the selected visits and utilizations."""
    base = Path(data_root) / "bc_faib_ground_plots" / "psp"
    smries_path = base / "faib_compiled_smries.csv.gz"
    spc_path = base / "faib_compiled_spcsmries.csv.gz"
    for path in (smries_path, spc_path):
        if not path.exists():
            raise FileNotFoundError(f"FAIB compiled input not found: {path}")
    wanted = {str(plot) for plot in plot_ids}
    smries = pd.read_csv(smries_path, low_memory=False)
    smries = smries[smries["CLSTR_ID"].isin(wanted) & smries["UTIL"].isin(UTILIZATIONS)].copy()
    spc = pd.read_csv(spc_path, low_memory=False)
    spc = spc[spc["CLSTR_ID"].isin(wanted) & spc["UTIL"].isin(UTILIZATIONS)].copy()
    if smries.empty or spc.empty:
        raise BCPSPValidationError("compiled PSP volume banks are empty for the selected plots")
    for frame in (smries, spc):
        frame["plot_id"] = frame["CLSTR_ID"].map(_clean_id)
        frame["UTIL"] = pd.to_numeric(frame["UTIL"], errors="coerce")
        for column in _COMPILED_COLUMNS:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    spc["species"] = spc["SPECIES"].astype(str).str.strip().str.upper()

    duplicate_count = int(
        smries.duplicated(["plot_id", "UTIL"]).sum()
        + spc.duplicated(["plot_id", "UTIL", "species"]).sum()
    )
    if duplicate_count:
        raise BCPSPValidationError(
            f"compiled PSP volume banks contain {duplicate_count} duplicate keys"
        )

    species_totals = (
        spc.groupby(["plot_id", "UTIL"])[list(_COMPILED_COLUMNS)].sum().reset_index()
    )
    check = smries[
        ["plot_id", "UTIL", *list(_COMPILED_COLUMNS)]
    ].merge(
        species_totals,
        on=["plot_id", "UTIL"],
        how="outer",
        suffixes=("_stand", "_species"),
        indicator=True,
    )
    check["max_abs_difference"] = 0.0
    for column in _COMPILED_COLUMNS:
        check["max_abs_difference"] = np.maximum(
            check["max_abs_difference"],
            (check[f"{column}_stand"].fillna(0.0) - check[f"{column}_species"].fillna(0.0)).abs(),
        )
    summary = {
        "n_stand_rows": len(smries),
        "n_species_rows": len(spc),
        "n_plot_util_keys": len(check),
        "max_abs_stand_species_difference": float(check["max_abs_difference"].max()),
        "tolerance": COMPILED_ARITHMETIC_TOL,
        "pass": bool(
            (check["_merge"] == "both").all()
            and check["max_abs_difference"].max() <= COMPILED_ARITHMETIC_TOL
        ),
    }
    return smries, spc, summary


def _comparison_arithmetic(
    observed: pd.Series, predicted: pd.Series
) -> dict[str, Any]:
    """Pure summary arithmetic; callers pass aligned numeric series."""
    observed_array = pd.to_numeric(observed, errors="coerce").to_numpy(dtype=float)
    predicted_array = pd.to_numeric(predicted, errors="coerce").to_numpy(dtype=float)
    valid = np.isfinite(observed_array) & np.isfinite(predicted_array)
    if not valid.any():
        return {"n": 0}
    observed_array = observed_array[valid]
    predicted_array = predicted_array[valid]
    bias = predicted_array - observed_array
    observed_sum = float(observed_array.sum())
    ratio = (
        predicted_array[observed_array > 0] / observed_array[observed_array > 0]
        if (observed_array > 0).any()
        else np.array([], dtype=float)
    )
    return {
        "n": int(valid.sum()),
        "observed_sum": observed_sum,
        "predicted_sum": float(predicted_array.sum()),
        "bias_sum": float(bias.sum()),
        "mean_bias": float(bias.mean()),
        "mae": float(np.abs(bias).mean()),
        "rmse": float(np.sqrt(np.mean(np.square(bias)))),
        "relative_bias": float(bias.sum() / observed_sum) if observed_sum > 0 else None,
        "mean_ratio": float(ratio.mean()) if ratio.size else None,
        "median_ratio": float(np.median(ratio)) if ratio.size else None,
        "ratio_n": int(ratio.size),
    }


def comparison_metrics(observed: pd.Series, predicted: pd.Series) -> dict[str, Any]:
    """Public wrapper used by tests and report assembly."""
    return _comparison_arithmetic(observed, predicted)


def build_comparisons(
    tree_ledger: pd.DataFrame,
    smries: pd.DataFrame,
    spc: pd.DataFrame,
    bucked_species: Iterable[str],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[float, dict[str, Any]]]:
    """Build plot, plot-species, species, and overall comparison tables."""
    bucked = {str(code).upper() for code in bucked_species}
    plot_frames: list[pd.DataFrame] = []
    plot_species_frames: list[pd.DataFrame] = []
    species_frames: list[pd.DataFrame] = []
    metrics: dict[float, dict[str, Any]] = {}
    for utilization in UTILIZATIONS:
        util_key = float(utilization)
        observed_stand = smries[smries["UTIL"] == util_key].set_index("plot_id")
        observed_species = spc[spc["UTIL"] == util_key].copy()
        observed_species["is_bucked_species"] = observed_species["species"].isin(bucked)
        matched = (
            observed_species[observed_species["is_bucked_species"]]
            .groupby("plot_id")[list(_COMPILED_COLUMNS)]
            .sum()
        )
        predicted = (
            tree_ledger[tree_ledger["dbh_cm"] >= util_key]
            .groupby("plot_id")
            .agg(
                n_trees=("tree_id", "size"),
                n_bucked_trees=(
                    "bucked_status",
                    lambda values: int((values == "bucked").sum()),
                ),
                n_logs=("n_logs", "sum"),
                predicted_taper_m3ha=("taper_total_m3_ha", "sum"),
                predicted_log_m3ha=("recovered_log_m3_ha", "sum"),
                predicted_residue_m3ha=("stem_residue_m3_ha", "sum"),
                predicted_residue_odt_ha=("residue_odt_ha", "sum"),
                predicted_green_tonnes_ha=("green_tonnes_ha", "sum"),
                predicted_chip_green_tonnes_ha=("chip_green_tonnes_ha", "sum"),
            )
        )
        plot = observed_stand[
            [
                "VHA_WSV_LIV",
                "VHA_MER_LIV",
                "VHA_DWB_LIV",
                "STEMS_HA_LIV",
            ]
        ].rename(
            columns={
                "VHA_WSV_LIV": "observed_all_wsv_m3ha",
                "VHA_MER_LIV": "observed_all_mer_m3ha",
                "VHA_DWB_LIV": "observed_all_dwb_m3ha",
                "STEMS_HA_LIV": "observed_all_stems_ha",
            }
        )
        plot = plot.join(
            matched.rename(
                columns={
                    "VHA_WSV_LIV": "observed_matched_wsv_m3ha",
                    "VHA_MER_LIV": "observed_matched_mer_m3ha",
                    "VHA_DWB_LIV": "observed_matched_dwb_m3ha",
                    "STEMS_HA_LIV": "observed_matched_stems_ha",
                }
            ),
            how="left",
        )
        plot = plot.join(predicted, how="left")
        for column in (
            "observed_matched_wsv_m3ha",
            "observed_matched_mer_m3ha",
            "observed_matched_dwb_m3ha",
            "observed_matched_stems_ha",
            "n_trees",
            "n_bucked_trees",
            "n_logs",
            "predicted_taper_m3ha",
            "predicted_log_m3ha",
            "predicted_residue_m3ha",
            "predicted_residue_odt_ha",
            "predicted_green_tonnes_ha",
            "predicted_chip_green_tonnes_ha",
        ):
            plot[column] = pd.to_numeric(plot[column], errors="coerce").fillna(0.0)
        plot["utilization"] = util_key
        plot["taper_bias_m3ha"] = plot["predicted_taper_m3ha"] - plot["observed_matched_wsv_m3ha"]
        plot["log_bias_m3ha"] = plot["predicted_log_m3ha"] - plot["observed_matched_mer_m3ha"]
        plot["net_bias_m3ha"] = plot["predicted_log_m3ha"] - plot["observed_matched_dwb_m3ha"]
        plot["residue_share"] = np.where(
            plot["predicted_taper_m3ha"] > 0,
            plot["predicted_residue_m3ha"] / plot["predicted_taper_m3ha"],
            0.0,
        )
        for source, target in (
            ("predicted_taper_m3ha", "taper_ratio"),
            ("predicted_log_m3ha", "log_ratio"),
            ("predicted_log_m3ha", "net_ratio"),
        ):
            denominator = (
                plot["observed_matched_wsv_m3ha"]
                if source == "predicted_taper_m3ha"
                else (
                    plot["observed_matched_mer_m3ha"]
                    if source == "predicted_log_m3ha"
                    else plot["observed_matched_dwb_m3ha"]
                )
            )
            plot[target] = np.where(denominator > 0, plot[source] / denominator, np.nan)
        plot = plot.reset_index()
        plot = plot.rename(columns={"index": "plot_id"})
        if "plot_id" not in plot.columns:
            plot = plot.rename(columns={"level_0": "plot_id"})
        plot_frames.append(plot)

        predicted_species = (
            tree_ledger[tree_ledger["dbh_cm"] >= util_key]
            .groupby(["plot_id", "species"])
            .agg(
                n_trees=("tree_id", "size"),
                n_bucked_trees=(
                    "bucked_status",
                    lambda values: int((values == "bucked").sum()),
                ),
                n_logs=("n_logs", "sum"),
                predicted_taper_m3ha=("taper_total_m3_ha", "sum"),
                predicted_log_m3ha=("recovered_log_m3_ha", "sum"),
                predicted_residue_m3ha=("stem_residue_m3_ha", "sum"),
                predicted_residue_odt_ha=("residue_odt_ha", "sum"),
            )
            .reset_index()
        )
        observed_cols = observed_species.rename(
            columns={
                "VHA_WSV_LIV": "observed_wsv_m3ha",
                "VHA_MER_LIV": "observed_mer_m3ha",
                "VHA_DWB_LIV": "observed_dwb_m3ha",
                "STEMS_HA_LIV": "observed_stems_ha",
            }
        )[
            [
                "plot_id",
                "species",
                "observed_wsv_m3ha",
                "observed_mer_m3ha",
                "observed_dwb_m3ha",
                "observed_stems_ha",
                "is_bucked_species",
            ]
        ]
        plot_species = observed_cols.merge(
            predicted_species,
            on=["plot_id", "species"],
            how="outer",
        )
        plot_species = plot_species[
            plot_species["species"].notna()
            & (plot_species["species"].astype(str).str.upper() != "NAN")
        ].copy()
        for column in (
            "observed_wsv_m3ha",
            "observed_mer_m3ha",
            "observed_dwb_m3ha",
            "observed_stems_ha",
            "n_trees",
            "n_bucked_trees",
            "n_logs",
            "predicted_taper_m3ha",
            "predicted_log_m3ha",
            "predicted_residue_m3ha",
            "predicted_residue_odt_ha",
        ):
            plot_species[column] = pd.to_numeric(
                plot_species[column], errors="coerce"
            ).fillna(0.0)
        plot_species["is_bucked_species"] = plot_species["is_bucked_species"].fillna(
            plot_species["species"].isin(bucked)
        )
        plot_species["bucking_status"] = np.where(
            plot_species["is_bucked_species"], "bucked", "not_bucked_unsupported_species"
        )
        plot_species.loc[
            plot_species["is_bucked_species"] & (plot_species["n_trees"] == 0),
            "bucking_status",
        ] = "no_dbh_eligible_tree"
        plot_species.loc[
            plot_species["is_bucked_species"]
            & (plot_species["n_trees"] > 0)
            & (plot_species["n_bucked_trees"] == 0),
            "bucking_status",
        ] = "all_trees_skipped"
        plot_species.loc[
            plot_species["is_bucked_species"]
            & (plot_species["n_bucked_trees"] > 0)
            & (plot_species["n_bucked_trees"] < plot_species["n_trees"]),
            "bucking_status",
        ] = "partially_bucked"
        plot_species["bias_m3ha"] = (
            plot_species["predicted_taper_m3ha"] - plot_species["observed_wsv_m3ha"]
        )
        plot_species["ratio"] = np.where(
            plot_species["observed_wsv_m3ha"] > 0,
            plot_species["predicted_taper_m3ha"] / plot_species["observed_wsv_m3ha"],
            np.nan,
        )
        plot_species["utilization"] = util_key
        plot_species_frames.append(plot_species)

        species = (
            plot_species.groupby("species", dropna=False)
            .agg(
                n_plots=("plot_id", "nunique"),
                observed_wsv_m3ha=("observed_wsv_m3ha", "sum"),
                observed_mer_m3ha=("observed_mer_m3ha", "sum"),
                observed_dwb_m3ha=("observed_dwb_m3ha", "sum"),
                predicted_taper_m3ha=("predicted_taper_m3ha", "sum"),
                predicted_log_m3ha=("predicted_log_m3ha", "sum"),
                predicted_residue_m3ha=("predicted_residue_m3ha", "sum"),
                predicted_residue_odt_ha=("predicted_residue_odt_ha", "sum"),
                n_trees=("n_trees", "sum"),
                n_bucked_trees=("n_bucked_trees", "sum"),
                n_logs=("n_logs", "sum"),
            )
            .reset_index()
        )
        species_metrics = (
            plot_species.groupby("species")
            .apply(
                lambda group: pd.Series(
                    _comparison_arithmetic(
                        group["observed_wsv_m3ha"], group["predicted_taper_m3ha"]
                    )
                ),
                include_groups=False,
            )
            .reset_index()
            .rename(
                columns={
                    "observed_sum": "paired_observed_wsv_m3ha",
                    "predicted_sum": "paired_predicted_taper_m3ha",
                    "bias_sum": "paired_bias_m3ha",
                    "relative_bias": "paired_relative_bias",
                    "mean_ratio": "paired_mean_ratio",
                    "median_ratio": "paired_median_ratio",
                    "mae": "paired_mae_m3ha",
                    "rmse": "paired_rmse_m3ha",
                    "n": "n_paired_plots",
                    "ratio_n": "n_ratio_plots",
                }
            )
        )
        species = species.merge(species_metrics, on="species", how="left")
        species["bias_m3ha"] = species["predicted_taper_m3ha"] - species["observed_wsv_m3ha"]
        species["ratio"] = np.where(
            species["observed_wsv_m3ha"] > 0,
            species["predicted_taper_m3ha"] / species["observed_wsv_m3ha"],
            np.nan,
        )
        species["relative_bias"] = np.where(
            species["observed_wsv_m3ha"] > 0,
            species["bias_m3ha"] / species["observed_wsv_m3ha"],
            np.nan,
        )
        species["bucking_status"] = np.where(
            species["species"].isin(bucked), "bucked", "not_bucked_unsupported_species"
        )
        species["utilization"] = util_key
        species_frames.append(species)

        metrics[util_key] = {
            "taper_vs_whole_stem": _comparison_arithmetic(
                plot["observed_matched_wsv_m3ha"], plot["predicted_taper_m3ha"]
            ),
            "log_vs_gross_merch": _comparison_arithmetic(
                plot["observed_matched_mer_m3ha"], plot["predicted_log_m3ha"]
            ),
            "log_vs_net_merch": _comparison_arithmetic(
                plot["observed_matched_dwb_m3ha"], plot["predicted_log_m3ha"]
            ),
            "observed_all_wsv_m3ha_sum": float(plot["observed_all_wsv_m3ha"].sum()),
            "observed_matched_wsv_m3ha_sum": float(
                plot["observed_matched_wsv_m3ha"].sum()
            ),
            "predicted_taper_m3ha_sum": float(plot["predicted_taper_m3ha"].sum()),
            "predicted_log_m3ha_sum": float(plot["predicted_log_m3ha"].sum()),
            "predicted_residue_m3ha_sum": float(plot["predicted_residue_m3ha"].sum()),
            "predicted_residue_odt_ha_sum": float(
                plot["predicted_residue_odt_ha"].sum()
            ),
            "predicted_green_tonnes_ha_sum": float(
                plot["predicted_green_tonnes_ha"].sum()
            ),
            "predicted_chip_green_tonnes_ha_sum": float(
                plot["predicted_chip_green_tonnes_ha"].sum()
            ),
            "n_plots": int(plot["plot_id"].nunique()),
            "n_trees": int(plot["n_trees"].sum()),
            "n_bucked_trees": int(plot["n_bucked_trees"].sum()),
            "n_logs": int(plot["n_logs"].sum()),
            "residue_share": (
                float(plot["predicted_residue_m3ha"].sum() / plot["predicted_taper_m3ha"].sum())
                if plot["predicted_taper_m3ha"].sum() > 0
                else None
            ),
            "plot_bias_quantiles_m3ha": {
                "p05": _quantile(plot["taper_bias_m3ha"], 0.05),
                "p25": _quantile(plot["taper_bias_m3ha"], 0.25),
                "p50": _quantile(plot["taper_bias_m3ha"], 0.50),
                "p75": _quantile(plot["taper_bias_m3ha"], 0.75),
                "p95": _quantile(plot["taper_bias_m3ha"], 0.95),
            },
        }

    plot_frame = pd.concat(plot_frames, ignore_index=True, sort=False)
    plot_species_frame = pd.concat(plot_species_frames, ignore_index=True, sort=False)
    species_frame = pd.concat(species_frames, ignore_index=True, sort=False)
    for frame in (plot_frame, plot_species_frame, species_frame):
        frame.sort_values(
            [column for column in ("utilization", "plot_id", "species") if column in frame.columns],
            inplace=True,
        )
        frame.reset_index(drop=True, inplace=True)
    return plot_frame, plot_species_frame, species_frame, metrics


def _quantile(series: pd.Series, q: float) -> float | None:
    values = pd.to_numeric(series, errors="coerce").dropna()
    if values.empty:
        return None
    return float(values.quantile(q))


def build_utilization_table(metrics: Mapping[float, Mapping[str, Any]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for utilization in UTILIZATIONS:
        entry = metrics[float(utilization)]
        taper = entry["taper_vs_whole_stem"]
        logs = entry["log_vs_gross_merch"]
        net = entry["log_vs_net_merch"]
        rows.append(
            {
                "utilization_cm": float(utilization),
                "n_plots": entry["n_plots"],
                "n_trees": entry["n_trees"],
                "n_bucked_trees": entry["n_bucked_trees"],
                "n_logs": entry["n_logs"],
                "observed_all_wsv_m3ha_sum": entry["observed_all_wsv_m3ha_sum"],
                "observed_matched_wsv_m3ha_sum": entry["observed_matched_wsv_m3ha_sum"],
                "predicted_taper_m3ha_sum": entry["predicted_taper_m3ha_sum"],
                "predicted_log_m3ha_sum": entry["predicted_log_m3ha_sum"],
                "predicted_residue_m3ha_sum": entry["predicted_residue_m3ha_sum"],
                "residue_share": entry["residue_share"],
                "predicted_residue_odt_ha_sum": entry["predicted_residue_odt_ha_sum"],
                "predicted_green_tonnes_ha_sum": entry["predicted_green_tonnes_ha_sum"],
                "predicted_chip_green_tonnes_ha_sum": entry[
                    "predicted_chip_green_tonnes_ha_sum"
                ],
                "taper_bias_m3ha": taper["bias_sum"],
                "taper_mean_bias_m3ha": taper["mean_bias"],
                "taper_rmse_m3ha": taper["rmse"],
                "taper_relative_bias": taper["relative_bias"],
                "taper_mean_ratio": taper["mean_ratio"],
                "taper_median_ratio": taper["median_ratio"],
                "log_bias_m3ha": logs["bias_sum"],
                "log_mean_bias_m3ha": logs["mean_bias"],
                "log_rmse_m3ha": logs["rmse"],
                "log_relative_bias": logs["relative_bias"],
                "log_mean_ratio": logs["mean_ratio"],
                "log_median_ratio": logs["median_ratio"],
                "net_bias_m3ha": net["bias_sum"],
                "net_mean_bias_m3ha": net["mean_bias"],
                "net_rmse_m3ha": net["rmse"],
                "net_relative_bias": net["relative_bias"],
                "net_mean_ratio": net["mean_ratio"],
                "net_median_ratio": net["median_ratio"],
            }
        )
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Residue tables and validation checks
# ---------------------------------------------------------------------------


def build_residue_tables(
    tree_ledger: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Summarise stem residue by utilization and species.

    Branch and foliage biomass is outside the FAIB whole-stem volume and is
    represented once as a non-estimated component, never as zero biomass.
    """
    rows: list[dict[str, Any]] = []
    for utilization in UTILIZATIONS:
        subset = tree_ledger[
            (tree_ledger["dbh_cm"] >= utilization)
            & (tree_ledger["bucked_status"] == "bucked")
        ]
        for species, group in subset.groupby("species"):
            rows.append(_residue_row(group, utilization, species))
        rows.append(_residue_row(subset, utilization, "ALL"))
    residue_summary = pd.DataFrame(rows)

    total_taper = float(
        tree_ledger.loc[tree_ledger["bucked_status"] == "bucked", "taper_total_m3"].sum()
    )
    total_logs = float(
        tree_ledger.loc[tree_ledger["bucked_status"] == "bucked", "recovered_log_m3"].sum()
    )
    total_residue = float(
        tree_ledger.loc[tree_ledger["bucked_status"] == "bucked", "stem_residue_m3"].sum()
    )
    total_odt = float(
        tree_ledger.loc[tree_ledger["bucked_status"] == "bucked", "residue_odt"].sum()
    )
    total_green = float(
        tree_ledger.loc[tree_ledger["bucked_status"] == "bucked", "green_tonnes"].sum()
    )
    total_chip = float(
        tree_ledger.loc[tree_ledger["bucked_status"] == "bucked", "chip_green_tonnes"].sum()
    )
    components = pd.DataFrame(
        [
            {
                "component": "stem_residue_recovered_log_gap",
                "estimated": True,
                "volume_m3": total_residue,
                "odt": total_odt,
                "green_tonnes": total_green,
                "chip_green_tonnes": total_chip,
                "evidence": "taper_published_table_plus_dimension_sort",
                "note": (
                    "taper total minus recovered dimension logs; includes stump, "
                    "top above the log top, and stem sections that do not meet a "
                    "sort length/top limit"
                ),
            },
            {
                "component": "branches_foliage",
                "estimated": False,
                "volume_m3": 0.0,
                "odt": 0.0,
                "green_tonnes": 0.0,
                "chip_green_tonnes": 0.0,
                "evidence": "not_available",
                "note": (
                    "FAIB VOL_WSV is main-stem volume including stump and top; "
                    "branches and foliage are not in the taper ledger and are not "
                    "estimated here. This row is not a zero-biomass observation."
                ),
            },
            {
                "component": "taper_total",
                "estimated": True,
                "volume_m3": total_taper,
                "odt": float(
                    (tree_ledger["taper_total_m3"] * tree_ledger["density_odt_m3"])
                    .loc[tree_ledger["bucked_status"] == "bucked"]
                    .sum()
                ),
                "green_tonnes": None,
                "chip_green_tonnes": None,
                "evidence": "taper_published_table",
                "note": "recovered logs plus stem residue",
            },
            {
                "component": "recovered_dimension_logs",
                "estimated": True,
                "volume_m3": total_logs,
                "odt": float(
                    (tree_ledger["recovered_log_m3"] * tree_ledger["density_odt_m3"])
                    .loc[tree_ledger["bucked_status"] == "bucked"]
                    .sum()
                ),
                "green_tonnes": None,
                "chip_green_tonnes": None,
                "evidence": "taper_published_table_plus_dimension_sort",
                "note": "dimension-only sorts; no grade or defect assignment",
            },
        ]
    )
    summary = {
        "n_bucked_trees": int(
            (tree_ledger["bucked_status"] == "bucked").sum()
        ),
        "taper_total_m3": total_taper,
        "recovered_log_m3": total_logs,
        "stem_residue_m3": total_residue,
        "residue_share": total_residue / total_taper if total_taper > 0 else None,
        "residue_odt": total_odt,
        "green_tonnes_45": total_green,
        "chip_green_tonnes_45": total_chip,
        "branch_foliage_included": False,
        "note": (
            "Stem residue only. Branches and foliage are outside the FAIB "
            "whole-stem volume and are reported as not estimated."
        ),
    }
    return residue_summary, components, summary


def _residue_row(group: pd.DataFrame, utilization: float, species: str) -> dict[str, Any]:
    taper = float(group["taper_total_m3"].sum())
    recovered = float(group["recovered_log_m3"].sum())
    residue = float(group["stem_residue_m3"].sum())
    odt = float(group["residue_odt"].sum())
    green = float(group["green_tonnes"].sum())
    chip = float(group["chip_green_tonnes"].sum())
    n_plots = int(group["plot_id"].nunique()) if "plot_id" in group else 0
    return {
        "utilization_cm": float(utilization),
        "species": species,
        "n_trees": len(group),
        "n_plots": n_plots,
        "taper_total_m3": taper,
        "recovered_log_m3": recovered,
        "stem_residue_m3": residue,
        "residue_share": residue / taper if taper > 0 else None,
        "residue_odt": odt,
        "green_tonnes_45": green,
        "chip_green_tonnes_45": chip,
        "taper_total_m3ha_sum": float(group["taper_total_m3_ha"].sum()),
        "recovered_log_m3ha_sum": float(group["recovered_log_m3_ha"].sum()),
        "stem_residue_m3ha_sum": float(group["stem_residue_m3_ha"].sum()),
        "residue_odt_ha_sum": float(group["residue_odt_ha"].sum()),
        "green_tonnes_ha_sum": float(group["green_tonnes_ha"].sum()),
        "chip_green_tonnes_ha_sum": float(group["chip_green_tonnes_ha"].sum()),
        "mean_residue_m3ha": (
            float(group["stem_residue_m3_ha"].sum() / n_plots) if n_plots else None
        ),
        "mean_residue_odt_ha": (
            float(group["residue_odt_ha"].sum() / n_plots) if n_plots else None
        ),
        "evidence": "published_table_for_volume_assumption_for_density_and_moisture",
        "branch_foliage_included": False,
    }


def validate_tree_against_compiled(
    tree_ledger: pd.DataFrame,
    smries: pd.DataFrame,
) -> dict[str, Any]:
    """Check that tree x expansion reproduces the compiled volume banks."""
    checks: dict[str, Any] = {}
    for utilization in UTILIZATIONS:
        subset = tree_ledger[tree_ledger["dbh_cm"] >= utilization]
        observed = (
            subset.groupby("plot_id")
            .agg(
                calc_wsv=("observed_whole_stem_m3_ha", "sum"),
                calc_mer=("observed_merchantable_m3_ha", "sum"),
            )
            .reset_index()
        )
        compiled = smries[smries["UTIL"] == utilization][
            ["plot_id", "VHA_WSV_LIV", "VHA_MER_LIV"]
        ]
        joined = compiled.merge(observed, on="plot_id", how="outer")
        for column, calc_column in (
            ("VHA_WSV_LIV", "calc_wsv"),
            ("VHA_MER_LIV", "calc_mer"),
        ):
            difference = (joined[column] - joined[calc_column]).abs()
            checks[f"{utilization:g}_cm_{column}"] = {
                "max_abs_difference_m3ha": float(difference.max()),
                "tolerance": COMPILED_ARITHMETIC_TOL,
                "pass": bool(difference.max() <= COMPILED_ARITHMETIC_TOL),
            }
    return {
        "checks": checks,
        "pass": all(entry["pass"] for entry in checks.values()),
    }


def conservation_summary(tree_ledger: pd.DataFrame) -> dict[str, Any]:
    bucked = tree_ledger[tree_ledger["bucked_status"] == "bucked"].copy()
    if bucked.empty:
        raise BCPSPValidationError("no bucked trees; conservation cannot be assessed")
    balance = (
        bucked["recovered_log_m3"] + bucked["stem_residue_m3"] - bucked["taper_total_m3"]
    ).abs()
    tolerance = CONSERVATION_ABS_TOL + CONSERVATION_REL_TOL * bucked["taper_total_m3"]
    bucker_error = bucked["bucker_balance_error_m3"].astype(float)
    bucker_tolerance = BUCKER_BALANCE_ABS_TOL + BUCKER_BALANCE_REL_TOL * bucked[
        "taper_total_m3"
    ]
    return {
        "n_trees": len(bucked),
        "taper_total_m3": float(bucked["taper_total_m3"].sum()),
        "recovered_log_m3": float(bucked["recovered_log_m3"].sum()),
        "stem_residue_m3": float(bucked["stem_residue_m3"].sum()),
        "recovered_plus_residue_m3": float(
            (bucked["recovered_log_m3"] + bucked["stem_residue_m3"]).sum()
        ),
        "max_abs_ledger_balance_m3": float(balance.max()),
        "tolerance_absolute_m3": CONSERVATION_ABS_TOL,
        "tolerance_relative": CONSERVATION_REL_TOL,
        "pass": bool((balance <= tolerance).all()),
        "bucker_internal_max_abs_balance_m3": float(bucker_error.max()),
        "bucker_internal_tolerance_absolute_m3": BUCKER_BALANCE_ABS_TOL,
        "bucker_internal_tolerance_relative": BUCKER_BALANCE_REL_TOL,
        "bucker_internal_pass": bool((bucker_error <= bucker_tolerance).all()),
        "n_trees_without_logs": int((bucked["n_logs"] == 0).sum()),
        "n_trees_with_logs": int((bucked["n_logs"] > 0).sum()),
    }


def coverage_summary(
    population: Population,
    tree_ledger: pd.DataFrame,
    smries: pd.DataFrame,
) -> dict[str, Any]:
    bucked = tree_ledger[tree_ledger["bucked_status"] == "bucked"]
    skipped = tree_ledger[tree_ledger["bucked_status"] != "bucked"]
    species_counts = (
        tree_ledger.groupby(["species", "bucked_status"]).size().unstack(fill_value=0)
    )
    skipped_species = (
        skipped.groupby("species")
        .agg(
            n_trees=("tree_id", "size"),
            observed_wsv_m3ha=("observed_whole_stem_m3_ha", "sum"),
        )
        .sort_values("observed_wsv_m3ha", ascending=False)
        .reset_index()
    )
    at_12_5 = smries[smries["UTIL"] == 12.5]
    total_compiled = float(at_12_5["VHA_WSV_LIV"].sum())
    skipped_at_12_5 = skipped[skipped["dbh_cm"] >= 12.5]
    unsupported_observed = float(skipped_at_12_5["observed_whole_stem_m3_ha"].sum())
    return {
        "n_selected_trees": len(tree_ledger),
        "n_bucked_trees": len(bucked),
        "n_skipped_trees": len(skipped),
        "bucked_volume_share_of_selected_observed_wsv": (
            float(
                bucked["observed_whole_stem_m3_ha"].sum()
                / tree_ledger["observed_whole_stem_m3_ha"].sum()
            )
            if tree_ledger["observed_whole_stem_m3_ha"].sum() > 0
            else None
        ),
        "skipped_species": skipped_species.to_dict(orient="records"),
        "skip_reasons": {
            str(reason): int(count)
            for reason, count in skipped["skip_reason"].value_counts().items()
        },
        "skip_reason_groups": {
            "unsupported_taper_species": int(
                (skipped["skip_reason"] == "unsupported_taper_species").sum()
            ),
            "bucker_volume_balance_failure": int(
                skipped["skip_reason"]
                .astype(str)
                .str.contains("bucker volume balance failed", regex=False)
                .sum()
            ),
            "other": int(
                (
                    (skipped["skip_reason"] != "unsupported_taper_species")
                    & ~skipped["skip_reason"]
                    .astype(str)
                    .str.contains("bucker volume balance failed", regex=False)
                ).sum()
            ),
        },
        "skipped_observed_wsv_at_12_5_m3ha": unsupported_observed,
        "compiled_wsv_at_12_5_m3ha_sum": total_compiled,
        "skipped_observed_wsv_share_of_compiled": (
            unsupported_observed / total_compiled if total_compiled > 0 else None
        ),
        "species_status": {
            str(species): {
                str(status): int(count)
                for status, count in row.items()
                if count
            }
            for species, row in species_counts.iterrows()
        },
        "population_stats": population.stats,
    }


# ---------------------------------------------------------------------------
# Artifact and report assembly
# ---------------------------------------------------------------------------


def _write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, lineterminator="\n")


def _require_clean_numeric(
    frame: pd.DataFrame, columns: Sequence[str], label: str
) -> None:
    for column in columns:
        if column not in frame.columns:
            raise BCPSPValidationError(f"{label} is missing required column {column!r}")
        values = pd.to_numeric(frame[column], errors="coerce")
        if values.isna().any():
            raise BCPSPValidationError(f"{label}.{column} contains NaN")
        if (values < 0).any():
            raise BCPSPValidationError(f"{label}.{column} contains negative values")


def validate_output_tables(
    selected: pd.DataFrame,
    tree_ledger: pd.DataFrame,
    logs: pd.DataFrame,
    residue: pd.DataFrame,
    plot_comparison: pd.DataFrame,
    species_comparison: pd.DataFrame,
) -> dict[str, Any]:
    """Fail fast on structural output defects before writing artifacts."""
    _require_clean_numeric(
        selected,
        ["dbh_cm", "height_m", "expansion_factor"],
        "selected_trees",
    )
    _require_clean_numeric(
        tree_ledger,
        [
            "dbh_cm",
            "height_m",
            "expansion_factor",
            "taper_total_m3",
            "recovered_log_m3",
            "stem_residue_m3",
            "residue_odt",
            "green_tonnes",
            "chip_green_tonnes",
        ],
        "tree_ledger",
    )
    if not logs.empty:
        _require_clean_numeric(
            logs,
            [
                "length_m",
                "large_end_dib_cm",
                "small_end_dib_cm",
                "volume_m3",
            ],
            "bucked_logs",
        )
        for column in ("price_cad_m3", "value_cad"):
            values = pd.to_numeric(logs[column], errors="coerce")
            present = values.notna()
            if present.any() and (values[present] < 0).any():
                raise BCPSPValidationError(
                    f"bucked_logs.{column} contains negative values"
                )
    if not residue.empty:
        _require_clean_numeric(
            residue,
            [
                "residue_volume_m3",
                "basic_density_odt_m3",
                "residue_odt",
                "green_tonnes",
                "chip_green_tonnes",
                "recovered_log_m3",
                "taper_total_m3",
            ],
            "residue",
        )
    _require_clean_numeric(
        plot_comparison,
        [
            "observed_matched_wsv_m3ha",
            "observed_matched_mer_m3ha",
            "observed_matched_dwb_m3ha",
            "predicted_taper_m3ha",
            "predicted_log_m3ha",
            "predicted_residue_m3ha",
            "n_trees",
            "n_logs",
        ],
        "plot_comparison",
    )
    _require_clean_numeric(
        species_comparison,
        [
            "observed_wsv_m3ha",
            "predicted_taper_m3ha",
            "predicted_log_m3ha",
            "predicted_residue_m3ha",
            "n_trees",
            "n_logs",
        ],
        "species_comparison",
    )
    evidence_values = set()
    for frame, columns in (
        (selected, ("taper_evidence",)),
        (logs, ("evidence",)),
        (residue, ("evidence",)),
    ):
        for column in columns:
            evidence_values.update(frame[column].dropna().astype(str).unique())
    if logs.empty:
        evidence_values.add("published_table")
    allowed = {"measured_taper", "published_table", "assumption"}
    unexpected = sorted(evidence_values.difference(allowed))
    if unexpected:
        raise BCPSPValidationError(f"unexpected evidence tiers: {unexpected}")
    if not logs.empty:
        if (logs["sort_basis"] != "dimension_only").any():
            raise BCPSPValidationError("bucked logs contain a non-dimension sort basis")
        if (logs["grade_status"] != "not_assigned").any():
            raise BCPSPValidationError("bucked logs contain an assigned grade status")
    return {
        "selected_rows": len(selected),
        "ledger_rows": len(tree_ledger),
        "log_rows": len(logs),
        "residue_rows": len(residue),
        "evidence_tiers": sorted(evidence_values),
        "no_nan_or_negative_key_columns": True,
    }


def _build_report(
    population: Population,
    bucking: BuckingOutputs,
    tree_ledger: pd.DataFrame,
    utilization_table: pd.DataFrame,
    species_comparison: pd.DataFrame,
    residue_summary: pd.DataFrame,
    residue_summary_json: Mapping[str, Any],
    conservation: Mapping[str, Any],
    compiled_check: Mapping[str, Any],
    tree_compiled_check: Mapping[str, Any],
    coverage: Mapping[str, Any],
    rsyc: Mapping[str, Any],
    elapsed_seconds: float,
    args: argparse.Namespace,
) -> str:
    stats = population.stats
    total_12_5 = utilization_table[utilization_table["utilization_cm"] == 12.5].iloc[0]
    lines: list[str] = []
    lines.append("# BC PSP TSA24 bucking validation v0")
    lines.append("")
    lines.append("| Field | Value |")
    lines.append("|---|---|")
    lines.append(f"| Validation | `{VALIDATION_NAME}` |")
    lines.append("| TSA | 24 — Prince George |")
    lines.append(
        f"| Generated (UTC) | {datetime.now(UTC).isoformat(timespec='seconds')} |"
    )
    lines.append("| Population | full latest live visit per site, not a sample |")
    lines.append(
        f"| Sites / visits / plots | {stats['selected_sites']} / "
        f"{stats['selected_visits']} / {stats['selected_plots']} |"
    )
    lines.append(f"| Live selected trees | {stats['selected_live_trees']:,} |")
    lines.append(f"| Bucked trees | {coverage['n_bucked_trees']:,} |")
    lines.append(f"| Logs | {len(bucking.logs):,} |")
    lines.append(f"| Elapsed | {elapsed_seconds:.1f} s |")
    lines.append(
        "| Status | full-population dimension validation; grade not assigned |"
    )
    lines.append("")
    lines.append(
        "> This is a dimension-only bucking validation against the published "
        "FAIB PSP compiled volume banks. It is **not** an official BC grade "
        "validation, a national taper library, or an operational supply model. "
        "Sorts are geometry-based; defect, sweep, knot, rot, and product quality "
        "are not represented."
    )
    lines.append("")
    lines.append("## 1. Sample and coverage")
    lines.append("")
    lines.append(
        f"- TSA24 sites in the FAIB header: **{stats['selected_sites']:,}**. "
        f"One latest live visit was selected per site; FAIB `CLSTR_ID` changes "
        f"at remeasurement, so `CLSTR_ID` is not a site key."
    )
    lines.append(
        f"- Selected plots: **{stats['selected_plots']:,}**; live trees with "
        f"DBH ≥ {MIN_DBH_CM:g} cm, height > 1.3 m, positive `PHF_TREE`, and a "
        f"species code: **{stats['selected_live_trees']:,}**."
    )
    lines.append(
        f"- Latest-visit fallbacks because the latest overall visit had no valid "
        f"live tree: **{stats['latest_live_visit_fallback_sites']}**."
    )
    lines.append(
        f"- DBH min/median/max: **{stats['dbh']['min']:.1f} / "
        f"{stats['dbh']['median']:.1f} / {stats['dbh']['max']:.1f} cm**; "
        f"trees ≥ 7.5 cm: **{stats['dbh']['n_ge_7_5']:,}**; "
        f"trees ≥ 12.5 cm: **{stats['dbh']['n_ge_12_5']:,}**."
    )
    dropped = stats["dropped"]
    lines.append(
        "- Exclusions from the TSA24 tree rows: dead "
        f"**{dropped.get('dead', 0):,}**, invalid DBH "
        f"**{dropped.get('invalid_dbh', 0):,}**, height ≤ 1.3 m "
        f"**{dropped.get('short_height', 0):,}**, non-positive expansion "
        f"**{dropped.get('invalid_expansion', 0):,}**, missing species "
        f"**{dropped.get('missing_species', 0):,}**, below "
        f"{MIN_DBH_CM:g} cm **{dropped.get('below_min_dbh', 0):,}**, missing BEC "
        f"**{dropped.get('missing_bec', 0):,}**, not the site's latest live visit "
        f"**{dropped.get('not_latest_live_visit', 0):,}**."
    )
    lines.append(
        f"- Taper-supported species cover "
        f"**{(coverage['bucked_volume_share_of_selected_observed_wsv'] or 0) * 100:.2f}%** "
        f"of selected observed whole-stem volume. Unsupported species are listed "
        f"in `skipped_trees.csv`; they are excluded from the matched comparison, "
        f"not silently assigned a proxy taper."
    )
    skip_groups = coverage.get("skip_reason_groups") or {}
    if skip_groups:
        reason_text = "; ".join(
            f"{reason.replace('_', ' ')}: {count:,}"
            for reason, count in skip_groups.items()
            if count
        )
        lines.append(f"- Explicitly skipped rows: {reason_text}. No skip is silent.")
        balance_skips = int(skip_groups.get("bucker_volume_balance_failure", 0))
        if balance_skips:
            lines.append(
                f"- Of these, **{balance_skips}** small stems failed the reference "
                f"bucker's internal integration tolerance and were left unbucked. "
                f"They are retained in `skipped_trees.csv`; their observed volume "
                f"share of the compiled bank is reported in `validation_summary.json`."
            )
    if coverage["skipped_species"]:
        skipped_table = pd.DataFrame(coverage["skipped_species"]).rename(
            columns={
                "species": "species",
                "n_trees": "trees",
                "observed_wsv_m3ha": "observed WSV m³/ha-sum",
            }
        )
        lines.append("")
        lines.append("Skipped rows by species (unsupported taper or bucker balance):")
        lines.append("")
        lines.append(_markdown_table(skipped_table.head(12), digits=3))
    lines.append("")
    lines.append("## 2. Volume comparison")
    lines.append("")
    lines.append(
        "Definitions: `observed_matched_wsv` is the compiled PSP whole-stem "
        "live volume (`VHA_WSV_LIV`) summed over taper-supported species; "
        "`predicted_taper` is the Kozak total for the same trees × `PHF_TREE`; "
        "`predicted_log` is recovered dimension-log volume; `predicted_residue` "
        "is taper total minus recovered logs. All volume columns are sums of "
        "per-hectare plot values, so they are m³/ha summed across plots, not "
        "absolute cubic metres."
    )
    lines.append("")
    display = utilization_table[
        [
            "utilization_cm",
            "n_plots",
            "n_trees",
            "n_logs",
            "observed_matched_wsv_m3ha_sum",
            "predicted_taper_m3ha_sum",
            "predicted_log_m3ha_sum",
            "predicted_residue_m3ha_sum",
            "taper_bias_m3ha",
            "taper_rmse_m3ha",
            "taper_relative_bias",
            "log_bias_m3ha",
            "log_rmse_m3ha",
            "log_relative_bias",
        ]
    ].copy()
    display["taper_relative_bias"] = display["taper_relative_bias"] * 100
    display["log_relative_bias"] = display["log_relative_bias"] * 100
    lines.append(_markdown_table(display, digits=2))
    lines.append("")
    lines.append(
        "Primary taper comparison: predicted taper (recovered + stem residue) "
        "against compiled gross whole-stem volume at the matching utilization. "
        "Primary recovery comparison: recovered logs against compiled gross "
        "merchantable volume (`VHA_MER_LIV`, 10 cm top). The net merchantable "
        "bank (`VHA_DWB_LIV`, BEC decay/waste) is reported as a secondary check; "
        "the taper ledger is gross, so positive bias against the net bank is "
        "expected."
    )
    lines.append("")
    log12 = total_12_5
    lines.append(
        f"At 12.5 cm utilization: taper bias **{_fmt(log12['taper_bias_m3ha'])} "
        f"m³/ha-sum** ({_fmt((log12['taper_relative_bias'] or 0) * 100, 2)}%), "
        f"taper RMSE **{_fmt(log12['taper_rmse_m3ha'])}**; recovered-log bias "
        f"**{_fmt(log12['log_bias_m3ha'])}** "
        f"({_fmt((log12['log_relative_bias'] or 0) * 100, 2)}%), log RMSE "
        f"**{_fmt(log12['log_rmse_m3ha'])}**."
    )
    lines.append("")
    species_12 = species_comparison[
        species_comparison["utilization"] == 12.5
    ].sort_values("observed_wsv_m3ha", ascending=False)
    species_table = species_12[
        [
            "species",
            "n_plots",
            "n_trees",
            "observed_wsv_m3ha",
            "predicted_taper_m3ha",
            "bias_m3ha",
            "paired_rmse_m3ha",
            "relative_bias",
            "ratio",
            "bucking_status",
        ]
    ].rename(columns={"paired_rmse_m3ha": "plot_rmse_m3ha"}).head(15).copy()
    species_table["relative_bias"] = species_table["relative_bias"] * 100
    for column in ("n_plots", "n_trees"):
        species_table[column] = species_table[column].astype(int)
    lines.append("### Species comparison at 12.5 cm")
    lines.append("")
    lines.append(_markdown_table(species_table, digits=2))
    lines.append("")
    lines.append(
        "Full plot and species tables are in `plot_comparison.csv`, "
        "`plot_species_comparison.csv`, and `species_comparison.csv`. "
        "No threshold was tuned to improve these statistics."
    )
    lines.append("")
    lines.append("## 3. Residue")
    lines.append("")
    lines.append(
        f"- Bucked trees: **{residue_summary_json['n_bucked_trees']:,}**; "
        f"taper total **{_fmt(residue_summary_json['taper_total_m3'], 3)} m³**; "
        f"recovered logs **{_fmt(residue_summary_json['recovered_log_m3'], 3)} m³**; "
        f"stem residue **{_fmt(residue_summary_json['stem_residue_m3'], 3)} m³** "
        f"(**{_fmt((residue_summary_json['residue_share'] or 0) * 100, 1)}%** of taper)."
    )
    lines.append(
        f"- Residue ODT at species basic density: "
        f"**{_fmt(residue_summary_json['residue_odt'], 2)} ODT**; green tonnes "
        f"(45% moisture) **{_fmt(residue_summary_json['green_tonnes_45'], 2)}**; "
        f"chip green tonnes at {DEFAULT_CHIP_YIELD:.0%} yield "
        f"**{_fmt(residue_summary_json['chip_green_tonnes_45'], 2)}**."
    )
    lines.append(
        "- Residue is **stem residue only**: taper total minus recovered logs. "
        "It includes stump, top above the log top, and stem sections that do not "
        "meet a sort length/top limit. Branches and foliage are outside FAIB "
        "`VOL_WSV` and are **not estimated**; `residue_components.csv` records "
        "that component explicitly as not available, not as zero biomass."
    )
    residue_display = residue_summary[
        (residue_summary["species"] == "ALL")
    ][
        [
            "utilization_cm",
            "n_trees",
            "n_plots",
            "taper_total_m3",
            "recovered_log_m3",
            "stem_residue_m3",
            "residue_share",
            "residue_odt",
            "green_tonnes_45",
            "chip_green_tonnes_45",
        ]
    ].copy()
    lines.append("")
    lines.append(_markdown_table(residue_display, digits=3))
    lines.append("")
    lines.append("## 4. Grade and taper evidence")
    lines.append("")
    lines.append(
        "- Taper: **BC Kozak (2004) Version 6**, published parameter table "
        "`kozak_parameters_v94.csv`, applied by species and BEC zone through "
        "`nemora`. Trees outside a supported species or zone are skipped and "
        "reported; no curve is averaged, interpolated, or silently substituted."
    )
    lines.append(
        "- Sorts: **TIPSY 4.5 interior industrial dimensions** "
        "(`rules/bc_interior.yaml`). Logs are selected by minimum top diameter "
        "and minimum length. This is dimension-only geometry, **not official BC "
        "grade**; `grade_status = not_assigned` and `sort_basis = dimension_only` "
        "on every log."
    )
    lines.append(
        "- Evidence tiers: taper and volume rows are `published_table`; residue "
        "density/moisture/chip conversions are `assumption`; no tree carries "
        "`measured_taper` because the FAIB PSP data do not include stem analysis. "
        "`HEIGHT_SOURCE` is retained so DBH-estimated heights are distinguishable "
        "from field-measured heights."
    )
    lines.append(
        "- No official grade, sort recovery, or national claim is made from this "
        "validation."
    )
    lines.append("")
    lines.append("## 5. RSYC secondary check")
    lines.append("")
    lines.append(
        f"- Status: **{rsyc.get('status', 'n/a')}**; curve rows "
        f"**{rsyc.get('n_curve_rows', 0)}**; plot matches "
        f"**{rsyc.get('n_plot_matches', 0)}**."
    )
    comparison = rsyc.get("plot_comparison") or {}
    if comparison.get("n"):
        lines.append(
            f"- Observed merchantable vs RSYC: n={comparison.get('n')}, "
            f"relative bias {_fmt((comparison.get('relative_bias') or 0) * 100, 1)}%, "
            f"RMSE {_fmt(comparison.get('rmse'))} m³/ha."
        )
    lines.append(
        "- RSYC is a stand-level remote-sensing yield product with no tree lists, "
        "diameter distribution, grade, or residue. It is a coarse secondary "
        "sanity check, **not** a calibration target."
    )
    lines.append("")
    lines.append("## 6. Validation checks")
    lines.append("")
    lines.append(
        f"- Tree × expansion reproduces compiled PSP banks: "
        f"**{'PASS' if tree_compiled_check['pass'] else 'FAIL'}** "
        f"(tolerance {COMPILED_ARITHMETIC_TOL} m³/ha per plot)."
    )
    lines.append(
        f"- Compiled stand and species banks agree: "
        f"**{'PASS' if compiled_check['pass'] else 'FAIL'}** "
        f"(max difference {compiled_check['max_abs_stand_species_difference']:.3g} m³/ha)."
    )
    lines.append(
        f"- Ledger conservation (recovered + residue = taper): "
        f"**{'PASS' if conservation['pass'] else 'FAIL'}**; max balance error "
        f"**{conservation['max_abs_ledger_balance_m3']:.3g} m³**; trees without "
        f"a feasible log **{conservation['n_trees_without_logs']:,}**."
    )
    lines.append(
        f"- Reference bucker internal balance: "
        f"**{'PASS' if conservation['bucker_internal_pass'] else 'FAIL'}**; max "
        f"error **{conservation['bucker_internal_max_abs_balance_m3']:.3g} m³**."
    )
    lines.append(
        "- Output tables are checked for NaN and negative values in key volume, "
        "dimension, count, and residue columns before writing."
    )
    lines.append("")
    lines.append("## 7. Caveats")
    lines.append("")
    lines.append(
        "- The comparison tests the *combination* of the nemora Kozak (2004) "
        "taper, FAIB tree list, expansion factors, and dimension sorts. It does "
        "not isolate taper error from compiled-volume error."
    )
    lines.append(
        "- FAIB `VOL_WSV` includes stump and top and is based on Kozak (2002) "
        "with mixed-model volumes for unenhanced trees; the nemora taper starts "
        "at 0.3 m stump height. A small negative taper bias is therefore expected."
    )
    lines.append(
        "- FAIB `VOL_MER` is gross merchantable volume to a 10 cm top. The sort "
        "rules use a 12.5 cm minimum top and minimum lengths, so recovered log "
        "volume is expected to be below gross merchantable volume. This is a "
        "definition difference, not a fitted correction."
    )
    lines.append(
        "- Species basic density is a reference default table (FVS-BC NBEL/FIA), "
        "not a BC-local sample. Moisture and chip yield are labelled assumptions."
    )
    lines.append(
        "- There is no official grade validation in this dataset because FAIB PSP "
        "tree records do not carry the defect, knot, sweep, rot, or product-quality "
        "information required for BC log grades."
    )
    lines.append("")
    lines.append("## 8. Artifacts")
    lines.append("")
    for name in (
        "selected_trees.csv",
        "tree_ledger.csv",
        "bucked_logs.csv",
        "residue.csv",
        "skipped_trees.csv",
        "plot_comparison.csv",
        "plot_species_comparison.csv",
        "species_comparison.csv",
        "utilization_comparison.csv",
        "residue_summary.csv",
        "residue_components.csv",
        "rsyc_benchmark.csv",
        "rsyc_curves.csv",
        "validation_summary.json",
        "manifest.json",
    ):
        lines.append(f"- `{name}`")
    lines.append("")
    lines.append(
        "Input and output hashes, git state, and the no-deletion check are in "
        "`manifest.json`."
    )
    lines.append("")
    return "\n".join(lines)


def _git_state(repo_root: Path) -> dict[str, Any]:
    def run_git(*arguments: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(repo_root), *arguments],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise BCPSPValidationError(
                f"git {' '.join(arguments)} failed in {repo_root}: {result.stderr.strip()}"
            )
        return result.stdout.strip()

    status = run_git("status", "--porcelain")
    return {
        "head": run_git("rev-parse", "HEAD"),
        "branch": run_git("rev-parse", "--abbrev-ref", "HEAD"),
        "dirty_paths": [line for line in status.splitlines() if line],
        "deletions": [
            line
            for line in status.splitlines()
            if len(line) >= 2 and (line[0] == "D" or line[1] == "D")
        ],
    }


def _build_manifest(
    args: argparse.Namespace,
    repo_root: Path,
    out_dir: Path,
    population: Population,
    bucking: BuckingOutputs,
    conservation: Mapping[str, Any],
    metrics: Mapping[float, Mapping[str, Any]],
    coverage: Mapping[str, Any],
    compiled_check: Mapping[str, Any],
    tree_compiled_check: Mapping[str, Any],
    residue_summary_json: Mapping[str, Any],
    rsyc: Mapping[str, Any],
    elapsed_seconds: float,
    registry: TaperRegistry,
    paths: Mapping[str, Path],
) -> dict[str, Any]:
    inputs = [
        _hash_record(
            "faib_header",
            paths["data_root"] / "bc_faib_ground_plots" / "psp" / "faib_header.csv.gz",
            "raw/bc_faib_ground_plots/psp/faib_header.csv.gz",
        ),
        _hash_record(
            "faib_plot_header",
            paths["data_root"] / "bc_faib_ground_plots" / "psp" / "faib_plot_header.csv.gz",
            "raw/bc_faib_ground_plots/psp/faib_plot_header.csv.gz",
        ),
        _hash_record(
            "faib_sample_byvisit",
            paths["data_root"]
            / "bc_faib_ground_plots"
            / "psp"
            / "faib_sample_byvisit.csv.gz",
            "raw/bc_faib_ground_plots/psp/faib_sample_byvisit.csv.gz",
        ),
        _hash_record(
            "faib_tree_detail",
            paths["data_root"] / "bc_faib_ground_plots" / "psp" / "faib_tree_detail.csv.gz",
            "raw/bc_faib_ground_plots/psp/faib_tree_detail.csv.gz",
        ),
        _hash_record(
            "faib_compiled_smries",
            paths["data_root"]
            / "bc_faib_ground_plots"
            / "psp"
            / "faib_compiled_smries.csv.gz",
            "raw/bc_faib_ground_plots/psp/faib_compiled_smries.csv.gz",
        ),
        _hash_record(
            "faib_compiled_spcsmries",
            paths["data_root"]
            / "bc_faib_ground_plots"
            / "psp"
            / "faib_compiled_spcsmries.csv.gz",
            "raw/bc_faib_ground_plots/psp/faib_compiled_spcsmries.csv.gz",
        ),
        _hash_record(
            "faib_contents",
            paths["data_root"] / "bc_faib_ground_plots" / "CONTENTS.md",
            "raw/bc_faib_ground_plots/CONTENTS.md",
        ),
        _hash_record(
            "kozak_parameters", paths["parameter_path"], "nemora/config/kozak/kozak_parameters_v94.csv"
        ),
        _hash_record(
            "species_crosswalk",
            paths["crosswalk_path"],
            "nemora/config/species/bc_psp_to_group.csv",
        ),
        _hash_record("grade_rules", paths["rules_path"], "workflows/national/bucking/rules/bc_interior.yaml"),
        _hash_record("basic_density", paths["density_path"], "workflows/national/bucking/rules/bc_basic_density.csv"),
        _hash_record("price_profile", paths["price_path"], "nemora/config/log_prices/bc_interior_2026q3.yaml"),
        _hash_record(
            "rsyc_models",
            repo_root / "data" / "raw" / "national" / "rsyc" / "RSYC_models.zip",
            "raw/national/rsyc/RSYC_models.zip",
        ),
        _hash_record(
            "rsyc_tiles",
            repo_root / "data" / "raw" / "national" / "rsyc" / "RSYC_tiles.gpkg",
            "raw/national/rsyc/RSYC_tiles.gpkg",
        ),
        _hash_record(
            "rsyc_ecostrat",
            repo_root / "data" / "raw" / "national" / "rsyc" / "RSYC_ecostrat.gpkg",
            "raw/national/rsyc/RSYC_ecostrat.gpkg",
        ),
        _hash_record(
            "tsa_boundaries",
            paths["femic_root"] / "data" / "tsa_boundaries.feather",
            "femic-tsa24-instance/data/tsa_boundaries.feather",
        ),
    ]
    outputs = [
        {
            "path": path.name,
            "bytes": int(path.stat().st_size),
            "sha256": _sha256(path),
        }
        for path in sorted(out_dir.glob("*"))
        if path.is_file() and path.name != "manifest.json"
    ]
    assert_no_unexpected_deletions(repo_root)
    return {
        "validation": VALIDATION_NAME,
        "tsa_number": TSA_NUMBER,
        "tsa_name": TSA_NAME,
        "created_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "elapsed_seconds": elapsed_seconds,
        "command": sys.argv,
        "cwd": str(Path.cwd()),
        "python": sys.version.split()[0],
        "pandas": pd.__version__,
        "numpy": np.__version__,
        "code": {
            "module": "workflows.national.bucking.bc_psp_validation",
            "population": "full TSA24 latest live visit per SITE_IDENTIFIER",
            "not": [
                "official BC grade",
                "national taper library",
                "operational supply model",
            ],
        },
        "inputs": inputs,
        "config": {
            "jurisdiction": "BC",
            "taper": registry.manifest_record(),
            "models_used": list(bucking.models),
            "grade_rules": {
                "path": "workflows/national/bucking/rules/bc_interior.yaml",
                "authority": "TIPSY 4.5 industrial interior dimensions",
                "basis": "dimension_only",
                "grade_status": "not_assigned",
            },
            "residue_assumptions": {
                "basic_density": (
                    "species table rules/bc_basic_density.csv where available; "
                    f"{GENERIC_DENSITY_ODT_M3} ODT/m3 generic fallback labelled by row"
                ),
                "moisture_green_wet_basis": DEFAULT_MOISTURE_GREEN,
                "moisture_seasoned_wet_basis": DEFAULT_MOISTURE_SEASONED,
                "chip_yield_fraction": DEFAULT_CHIP_YIELD,
                "branch_foliage": "not estimated",
            },
            "utilizations_cm": list(UTILIZATIONS),
            "workers": int(args.workers),
            "timebox_seconds": float(args.timebox_seconds),
        },
        "population": population.stats,
        "coverage": coverage,
        "conservation": conservation,
        "compiled_arithmetic": compiled_check,
        "tree_expansion_arithmetic": tree_compiled_check,
        "comparison": {str(key): value for key, value in metrics.items()},
        "residue": residue_summary_json,
        "rsyc": rsyc,
        "taper_warnings": list(bucking.taper_warnings),
        "outputs": outputs,
        "git": {
            "parent": _git_state(repo_root),
            "data": _git_state(repo_root / "data"),
        },
        "no_deletion_check": {
            "pass": True,
            "repositories": [
                str(repo_root),
                str(repo_root / "data"),
            ],
            "note": "fail-closed check for any git status D in parent or data",
        },
    }


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------


def run(args: argparse.Namespace) -> dict[str, Any]:
    started = time.monotonic()
    deadline = Deadline(args.timebox_seconds)
    repo_root = Path(args.repo_root).resolve()
    assert_no_unexpected_deletions(repo_root)
    out_dir = Path(args.out_dir)
    if not out_dir.is_absolute():
        out_dir = repo_root / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    data_root = Path(args.data_root)
    if not data_root.is_absolute():
        data_root = repo_root / data_root
    parameter_path = Path(args.kozak_parameters)
    if not parameter_path.is_absolute():
        parameter_path = repo_root / parameter_path
    rules_path = Path(args.rules)
    if not rules_path.is_absolute():
        rules_path = repo_root / rules_path
    density_path = Path(args.basic_density)
    if not density_path.is_absolute():
        density_path = repo_root / density_path
    price_path = Path(args.price_profile)
    if not price_path.is_absolute():
        price_path = repo_root / price_path
    crosswalk_path = Path(args.species_crosswalk)
    if not crosswalk_path.is_absolute():
        crosswalk_path = repo_root / crosswalk_path
    femic_root = Path(args.femic_root)
    if not femic_root.is_absolute():
        femic_root = repo_root / femic_root

    registry = TaperRegistry(parameter_path=parameter_path)
    density_table = load_basic_density_table(density_path)

    print("Loading TSA24 latest live visits...", file=sys.stderr)
    population = load_tsa24_latest_visit(
        data_root,
        tsa_name=TSA_NAME,
        species_crosswalk_path=crosswalk_path,
    )
    print(
        f"Selected {population.stats['selected_live_trees']:,} live trees on "
        f"{population.stats['selected_plots']:,} plots / "
        f"{population.stats['selected_sites']:,} sites.",
        file=sys.stderr,
    )
    deadline.check("bucking")

    print(
        f"Bucking {sum(tree.species in set(registry.supported_species) for tree in population.trees):,} "
        f"taper-supported trees with {args.workers} workers...",
        file=sys.stderr,
    )
    bucking = buck_population(
        population,
        registry,
        parameter_path=parameter_path,
        rules_path=rules_path,
        price_path=price_path,
        density_table=density_table,
        workers=args.workers,
        deadline=deadline,
    )
    print(
        f"Bucked {len(bucking.logs):,} logs; residue rows {len(bucking.residue):,}; "
        f"skips {len(bucking.skipped):,} in {bucking.elapsed_seconds:.1f} s.",
        file=sys.stderr,
    )
    bucking.residue["volume_evidence"] = "published_table"
    bucking.residue["odt_evidence"] = bucking.residue["density_evidence"]
    bucking.residue["notes"] = (
        "stem residue = taper total - recovered logs; density, moisture, and "
        "chip yield are assumptions; branches and foliage excluded"
    )
    deadline.check("tree ledger")

    tree_ledger = build_tree_ledger(population, bucking, density_table)
    selected = tree_ledger[
        [
            "tree_id",
            "site_id",
            "plot_id",
            "visit_id",
            "visit_number",
            "plot_number",
            "tree_number",
            "species",
            "species_group",
            "species_known",
            "bec_zone",
            "bec_subzone",
            "dbh_cm",
            "height_m",
            "height_source",
            "height_evidence",
            "expansion_factor",
            "age",
            "stand_age",
            "site_index",
            "latitude",
            "longitude",
            "observed_merchantable_m3",
            "observed_whole_stem_m3",
            "bucked_status",
            "skip_reason",
            "density_odt_m3",
            "density_evidence",
            "taper_evidence",
            "sort_basis",
            "grade_status",
        ]
    ].copy()

    print("Loading compiled PSP volume banks...", file=sys.stderr)
    smries, spc, compiled_check = load_compiled_volumes(
        data_root, population.plots["plot_id"]
    )
    tree_compiled_check = validate_tree_against_compiled(tree_ledger, smries)
    if not tree_compiled_check["pass"]:
        raise BCPSPValidationError(
            "tree list × PHF_TREE does not reproduce the compiled PSP volume banks"
        )
    if not compiled_check["pass"]:
        raise BCPSPValidationError(
            "compiled PSP stand and species volume banks disagree"
        )

    print("Building plot and species comparisons...", file=sys.stderr)
    plot_comparison, plot_species_comparison, species_comparison, metrics = build_comparisons(
        tree_ledger, smries, spc, registry.supported_species
    )
    utilization_table = build_utilization_table(metrics)
    residue_summary, residue_components, residue_summary_json = build_residue_tables(
        tree_ledger
    )
    conservation = conservation_summary(tree_ledger)
    if not conservation["pass"]:
        raise BCPSPValidationError(
            "stem ledger conservation failed: recovered + residue != taper total"
        )
    coverage = coverage_summary(population, tree_ledger, smries)

    print("Running RSYC secondary check...", file=sys.stderr)
    rsyc_plot_summary = plot_comparison[plot_comparison["utilization"] == 12.5].copy()
    rsyc_plot_summary = rsyc_plot_summary.merge(
        population.plots[
            ["plot_id", "leading_species", "stand_age", "longitude", "latitude"]
        ],
        on="plot_id",
        how="left",
    )
    rsyc_plot_summary["observed_merchantable_all_m3ha"] = rsyc_plot_summary[
        "observed_all_mer_m3ha"
    ]
    rsyc_plot_summary["bucked_log_m3ha"] = rsyc_plot_summary["predicted_log_m3ha"]
    rsyc_plot_summary["bucked_log_m3ha_ratio_est"] = rsyc_plot_summary[
        "predicted_log_m3ha"
    ]
    faib_proxy = FaibTreeList(
        trees=(),
        plots=population.plots,
        stats=population.stats,
    )
    rsyc, rsyc_curves, rsyc_plots = _run_rsyc(
        repo_root,
        femic_root,
        faib_proxy,
        rsyc_plot_summary,
        deadline,
    )

    validation_checks = validate_output_tables(
        selected,
        tree_ledger,
        bucking.logs,
        bucking.residue,
        plot_comparison,
        species_comparison,
    )
    elapsed = time.monotonic() - started
    summary = {
        "validation": VALIDATION_NAME,
        "tsa_number": TSA_NUMBER,
        "tsa_name": TSA_NAME,
        "created_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "elapsed_seconds": elapsed,
        "population": population.stats,
        "coverage": coverage,
        "conservation": conservation,
        "compiled_arithmetic": compiled_check,
        "tree_expansion_arithmetic": tree_compiled_check,
        "comparison": {str(key): value for key, value in metrics.items()},
        "residue": residue_summary_json,
        "rsyc": rsyc,
        "taper_warnings": list(bucking.taper_warnings),
        "validation_checks": validation_checks,
        "status": "dimension_validation_complete_grade_not_assigned",
        "assumptions": {
            "population": "latest live visit per SITE_IDENTIFIER",
            "utilization_cm": list(UTILIZATIONS),
            "merchantable_bank": "VHA_MER_LIV, gross, 10 cm top",
            "net_bank": "VHA_DWB_LIV, BEC decay/waste",
            "residue": "stem only; branches/foliage not estimated",
            "sort_basis": "dimension_only",
            "grade_status": "not_assigned",
        },
    }

    print("Writing artifacts...", file=sys.stderr)
    _write_csv(selected, out_dir / "selected_trees.csv")
    _write_csv(tree_ledger, out_dir / "tree_ledger.csv")
    _write_csv(bucking.logs, out_dir / "bucked_logs.csv")
    _write_csv(bucking.residue, out_dir / "residue.csv")
    _write_csv(bucking.skipped, out_dir / "skipped_trees.csv")
    _write_csv(plot_comparison, out_dir / "plot_comparison.csv")
    _write_csv(plot_species_comparison, out_dir / "plot_species_comparison.csv")
    _write_csv(species_comparison, out_dir / "species_comparison.csv")
    _write_csv(utilization_table, out_dir / "utilization_comparison.csv")
    _write_csv(residue_summary, out_dir / "residue_summary.csv")
    _write_csv(residue_components, out_dir / "residue_components.csv")
    if not rsyc_curves.empty:
        _write_csv(rsyc_curves, out_dir / "rsyc_curves.csv")
    if not rsyc_plots.empty:
        _write_csv(rsyc_plots, out_dir / "rsyc_benchmark.csv")
    (out_dir / "validation_summary.json").write_text(
        json.dumps(_jsonable(summary), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    report = _build_report(
        population,
        bucking,
        tree_ledger,
        utilization_table,
        species_comparison,
        residue_summary,
        residue_summary_json,
        conservation,
        compiled_check,
        tree_compiled_check,
        coverage,
        rsyc,
        elapsed,
        args,
    )
    (out_dir / "VALIDATION_REPORT.md").write_text(report, encoding="utf-8")

    manifest = _build_manifest(
        args,
        repo_root,
        out_dir,
        population,
        bucking,
        conservation,
        metrics,
        coverage,
        compiled_check,
        tree_compiled_check,
        residue_summary_json,
        rsyc,
        elapsed,
        registry,
        {
            "data_root": data_root,
            "parameter_path": parameter_path,
            "rules_path": rules_path,
            "density_path": density_path,
            "price_path": price_path,
            "crosswalk_path": crosswalk_path,
            "femic_root": femic_root,
        },
    )
    (out_dir / "manifest.json").write_text(
        json.dumps(_jsonable(manifest), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote validation artifacts to {out_dir}", file=sys.stderr)
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate BC Kozak taper + TIPSY dimension bucking against the "
            "full TSA24 FAIB PSP compiled volume banks."
        )
    )
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--data-root", default="data/raw")
    parser.add_argument(
        "--out-dir",
        default="data/derived/national/bucking/bc_psp_validation_v0",
    )
    parser.add_argument(
        "--kozak-parameters", default=str(default_kozak_parameter_path())
    )
    parser.add_argument(
        "--rules",
        default="workflows/national/bucking/rules/bc_interior.yaml",
    )
    parser.add_argument(
        "--basic-density",
        default="workflows/national/bucking/rules/bc_basic_density.csv",
    )
    parser.add_argument(
        "--price-profile",
        default=str(
            default_nemora_root() / "config" / "log_prices" / "bc_interior_2026q3.yaml"
        ),
    )
    parser.add_argument(
        "--species-crosswalk",
        default=str(
            default_nemora_root() / "config" / "species" / "bc_psp_to_group.csv"
        ),
    )
    parser.add_argument(
        "--femic-root",
        default=str(default_nemora_root().parent / "femic" / "external" / "femic-tsa24-instance"),
    )
    parser.add_argument(
        "--timebox-seconds",
        type=float,
        default=DEFAULT_TIMEBOX_SECONDS,
        help="Fail-closed wall-clock timebox for the run (default: 1200 s).",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help="Bucking worker processes (default: min(32, CPU count)).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        run(args)
    except TimeboxExceeded as exc:
        print(f"TIMEBOX EXCEEDED: {exc}", file=sys.stderr)
        return 2
    except (BCPSPValidationError, FileNotFoundError) as exc:
        print(f"VALIDATION FAILED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
