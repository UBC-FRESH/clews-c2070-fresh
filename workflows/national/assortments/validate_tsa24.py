"""TSA24 stand-to-tree disaggregation prototype.

Run from the repository root with nemora on ``PYTHONPATH``::

    PYTHONPATH="${NEMORA_ROOT:?set NEMORA_ROOT}/src" \
      .venv/bin/python -m workflows.national.assortments.validate_tsa24

The prototype fits RSYC-stratified DBH distributions, fits hierarchical
height-diameter models, synthesizes a volume-constrained tree list, bucks it
with the existing BC interior layer, and writes the assortment matrix and
validation report.

Fail-closed rule: any unexpected ``D`` in the parent or data repository status
halts the run immediately.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
import time
import zipfile
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd
from scipy.integrate import cumulative_trapezoid

from nemora.core import MixtureComponentFit
from nemora.distributions import get_distribution
from nemora.fit.mixture import mixture_cdf
from nemora.sampling import truncated_quantile
from nemora.workflows.taper import KOZAK_SPECIES

from workflows.national.assortments.assortments import (
    build_assortment_matrix,
    conservation_summary,
)
from workflows.national.assortments.distributions import (
    DistributionFit,
    fit_dbh_distributions,
)
from workflows.national.assortments.height import (
    HeightModelBundle,
    fit_height_models,
)
from workflows.national.assortments.rsyc import (
    assign_sites_to_ecostrata,
    load_rsyc_volume_models,
    rsyc_curve_table,
    rsyc_species_code,
)
from workflows.national.assortments.synthesize import (
    DistributionSpec,
    SynthesisError,
    SynthesisTarget,
    SynthesizedStand,
    normalize_species_weights,
    synthesize_tree_list,
)
from workflows.national.bucking.bucking import (
    buck_trees,
    load_grade_rules,
    load_price_profile,
    logs_frame,
    residue_frame,
)
from workflows.national.bucking.schema import TreeRecord
from workflows.national.bucking.taper import (
    TaperRegistry,
    default_kozak_parameter_path,
    default_nemora_root,
)
from workflows.national.bucking.validate_tsa24_btc import load_basic_density_table

__all__ = ["main"]

PILOT_NAME = "tsa24_assortment_disaggregation"
PILOT_VERSION = "0.1.0"
TSA_NAME = "Prince George"
DEFAULT_AU_ID = 23000
DEFAULT_AGE = 100.0
DEFAULT_SEED = 20261115
DEFAULT_N_TREES = 600
CONSERVATION_REL_TOL = 1e-8

ECO_LEVELS = ("ecodistrict", "ecoregion", "ecoprovince", "ecozone")

AU_LEADING_SPECIES_GROUPS: dict[str, set[str]] = {
    "SX": {"SW", "SE", "S", "SB"},
    "SX+AT": {"SW", "SE", "S", "SB", "AT"},
    "PLI": {"PL"},
    "FDI": {"FD"},
    "BL": {"BL"},
    "SE": {"SE"},
}

_TREE_COLUMNS = (
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
)

_VISIT_COLUMNS = (
    "source",
    "site_id",
    "visit_id",
    "year",
    "n_trees",
    "ba_m2ha",
    "tph",
    "qmd_cm",
    "top_height_m",
    "volume_m3ha",
    "site_index",
    "age",
)

_SITE_COLUMNS = (
    "source",
    "site_id",
    "province",
    "eco_zone",
    "eco_subzone",
    "site_index",
    "plot_design",
    "plot_area_ha",
    "baf",
    "lat",
    "lon",
)


@dataclass(frozen=True, slots=True)
class Tsa24Data:
    sites: pd.DataFrame
    plots: pd.DataFrame
    trees: pd.DataFrame
    stats: dict[str, Any]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def assert_no_unexpected_deletions(repo_root: Path) -> None:
    """Halt immediately on any worktree or index deletion."""
    repositories = [repo_root]
    data_root = repo_root / "data"
    if (data_root / ".git").exists():
        repositories.append(data_root)
    for repository in repositories:
        result = subprocess.run(
            ["git", "-C", str(repository), "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(f"git status failed in {repository}: {result.stderr.strip()}")
        for line in result.stdout.splitlines():
            if len(line) >= 2 and (line[0] == "D" or line[1] == "D"):
                raise RuntimeError(
                    f"unexpected deletion in {repository}: {line!r}; halting fail-closed"
                )


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _write_json(payload: Mapping[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_jsonable(payload), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, lineterminator="\n")


def _hash_record(label: str, path: Path, repo_root: Path) -> dict[str, Any]:
    if not path.exists():
        return {"label": label, "path": str(path), "status": "missing"}
    try:
        logical = str(path.resolve().relative_to(repo_root.resolve()))
    except ValueError:
        logical = str(path)
    return {
        "label": label,
        "path": logical,
        "status": "present",
        "bytes": int(path.stat().st_size),
        "sha256": _sha256(path),
    }


def load_tsa24_data(
    repo_root: Path,
    data_root: Path,
    *,
    tsa_name: str = TSA_NAME,
) -> Tsa24Data:
    """Load the TSA24 FAIB PSP subset and join RSYC ecological strata."""
    psp = data_root / "raw" / "bc_faib_ground_plots" / "psp"
    derived = data_root / "derived" / "fvsca_calibration" / "v0"
    rsyc = data_root / "raw" / "national" / "rsyc"
    for path in (
        psp / "faib_header.csv.gz",
        psp / "faib_sample_byvisit.csv.gz",
        derived / "sites.csv",
        derived / "visits.csv",
        derived / "trees.csv.gz",
        rsyc / "RSYC_ecostrat.gpkg",
        rsyc / "RSYC_tiles.gpkg",
    ):
        if not path.exists():
            raise FileNotFoundError(f"TSA24 input not found: {path}")

    header = pd.read_csv(
        psp / "faib_header.csv.gz",
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
    header["site_id"] = header["SITE_IDENTIFIER"].astype(str)
    selected = header[
        header["TSA_DESC"].astype(str).str.contains(tsa_name, case=False, na=False)
    ].copy()
    if selected.empty:
        raise ValueError(f"no FAIB PSP sites matched TSA_DESC containing {tsa_name!r}")
    selected = selected.drop_duplicates("site_id")
    site_ids = set(selected["site_id"])

    harmonized_sites = pd.read_csv(
        derived / "sites.csv",
        usecols=list(_SITE_COLUMNS),
        low_memory=False,
    )
    harmonized_sites = harmonized_sites[
        harmonized_sites["source"].eq("faib_psp")
        & harmonized_sites["site_id"].astype(str).isin(site_ids)
    ].copy()
    harmonized_sites["site_id"] = harmonized_sites["site_id"].astype(str)
    sites = selected.merge(
        harmonized_sites[
            ["site_id", "lat", "lon", "eco_zone", "eco_subzone", "site_index", "baf"]
        ],
        on="site_id",
        how="left",
    )
    sites["latitude"] = pd.to_numeric(sites["Latitude"], errors="coerce").fillna(
        pd.to_numeric(sites["lat"], errors="coerce")
    )
    sites["longitude"] = pd.to_numeric(sites["Longitude"], errors="coerce").fillna(
        pd.to_numeric(sites["lon"], errors="coerce")
    )
    sites["bec_zone"] = sites["BEC_ZONE"].astype(str).str.strip().str.upper()
    sites["bec_subzone"] = sites["BEC_SBZ"].astype(str).str.strip()

    ecostrata = assign_sites_to_ecostrata(
        sites,
        ecostrat_path=rsyc / "RSYC_ecostrat.gpkg",
        tiles_path=rsyc / "RSYC_tiles.gpkg",
    )
    ecostrata = ecostrata.drop(columns=["latitude", "longitude"], errors="ignore")
    sites = sites.merge(ecostrata, on="site_id", how="left")
    sites["mapped"] = sites["mapped"].fillna(False).astype(bool)
    sites = sites.drop(columns=["Latitude", "Longitude", "lat", "lon"], errors="ignore")

    visits = pd.read_csv(
        derived / "visits.csv",
        usecols=list(_VISIT_COLUMNS),
        low_memory=False,
    )
    visits = visits[
        visits["source"].eq("faib_psp")
        & visits["site_id"].astype(str).isin(site_ids)
    ].copy()
    visits["site_id"] = visits["site_id"].astype(str)
    visits["plot_id"] = visits["visit_id"].astype(str)
    visits = visits.drop_duplicates("plot_id", keep="last")

    raw_visits = pd.read_csv(
        psp / "faib_sample_byvisit.csv.gz",
        usecols=[
            "SITE_IDENTIFIER",
            "CLSTR_ID",
            "VISIT_NUMBER",
            "MEAS_YR",
            "DBH_LIMIT_TAG",
            "PROJ_AGE_1",
        ],
        low_memory=False,
    )
    raw_visits["site_id"] = raw_visits["SITE_IDENTIFIER"].astype(str)
    raw_visits["plot_id"] = raw_visits["CLSTR_ID"].astype(str)
    raw_visits = raw_visits[raw_visits["site_id"].isin(site_ids)].copy()
    raw_visits["DBH_LIMIT_TAG"] = pd.to_numeric(
        raw_visits["DBH_LIMIT_TAG"], errors="coerce"
    )
    raw_visits = raw_visits.sort_values(
        ["plot_id", "MEAS_YR", "VISIT_NUMBER"]
    ).drop_duplicates("plot_id", keep="last")
    limits = raw_visits[["plot_id", "DBH_LIMIT_TAG"]].rename(
        columns={"DBH_LIMIT_TAG": "dbh_limit_cm"}
    )

    trees = pd.read_csv(
        derived / "trees.csv.gz",
        usecols=list(_TREE_COLUMNS),
        low_memory=False,
    )
    trees = trees[
        trees["source"].eq("faib_psp")
        & trees["site_id"].astype(str).isin(site_ids)
    ].copy()
    trees["site_id"] = trees["site_id"].astype(str)
    trees["plot_id"] = trees["visit_id"].astype(str)
    trees["species"] = trees["species_code"].astype(str).str.strip().str.upper()
    trees["status"] = trees["status"].astype(str).str.lower()
    for column in ("dbh_cm", "height_m", "expansion_factor"):
        trees[column] = pd.to_numeric(trees[column], errors="coerce")
    trees["tree_id"] = trees["tree_id"].astype(str)

    trees = trees.merge(
        visits[
            [
                "plot_id",
                "year",
                "age",
                "tph",
                "ba_m2ha",
                "qmd_cm",
                "volume_m3ha",
                "site_index",
            ]
        ],
        on="plot_id",
        how="left",
    )
    trees = trees.merge(limits, on="plot_id", how="left")
    trees = trees.merge(
        sites[
            [
                "site_id",
                "bec_zone",
                "bec_subzone",
                "mapped",
                "join_key",
                "ecozone",
                "ecoprovince",
                "ecoregion",
                "ecodistrict",
                "ecodistrict_id",
                "tile_id",
            ]
        ],
        on="site_id",
        how="left",
    )
    trees["mapped"] = trees["mapped"].fillna(False).astype(bool)

    all_tree_rows = int(len(trees))
    live = trees[trees["status"].eq("live")].copy()
    dropped = {
        "dead": int((trees["status"] != "live").sum()),
        "invalid_dbh": int((live["dbh_cm"] <= 0.0).sum()),
        "invalid_expansion": int((live["expansion_factor"] <= 0.0).sum()),
        "missing_species": int(live["species"].isna().sum()),
        "missing_plot": int(live["plot_id"].isna().sum()),
    }
    live = live[
        (live["dbh_cm"] > 0.0)
        & (live["expansion_factor"] > 0.0)
        & live["species"].notna()
        & live["plot_id"].notna()
    ].copy()
    if live.empty:
        raise ValueError("no live, valid TSA24 trees remain")

    plots = (
        live.groupby("plot_id", as_index=False)
        .agg(
            site_id=("site_id", "first"),
            n_live_trees=("tree_id", "size"),
            tph_from_trees=("expansion_factor", "sum"),
        )
        .merge(
            visits.drop(
                columns=["source", "visit_id", "n_trees", "site_id"], errors="ignore"
            ),
            on="plot_id",
            how="left",
        )
    )
    plots = plots.merge(limits, on="plot_id", how="left")
    plots = plots.merge(
        sites[
            [
                "site_id",
                "bec_zone",
                "bec_subzone",
                "mapped",
                "join_key",
                "ecozone",
                "ecoprovince",
                "ecoregion",
                "ecodistrict",
                "ecodistrict_id",
                "tile_id",
            ]
        ],
        on="site_id",
        how="left",
    )
    leading = (
        live.groupby(["plot_id", "species"], as_index=False)["expansion_factor"]
        .sum()
        .sort_values(["plot_id", "expansion_factor"], ascending=[True, False])
        .drop_duplicates("plot_id")
        .rename(columns={"species": "leading_species"})
    )
    plots = plots.merge(leading[["plot_id", "leading_species"]], on="plot_id", how="left")
    plots["mapped"] = plots["mapped"].fillna(False).astype(bool)
    plots["age"] = pd.to_numeric(plots["age"], errors="coerce")
    plots["tph"] = pd.to_numeric(plots["tph"], errors="coerce").fillna(
        plots["tph_from_trees"]
    )
    plots["tph_from_trees"] = pd.to_numeric(plots["tph_from_trees"], errors="coerce")

    stats = {
        "tsa_name": tsa_name,
        "n_sites_selected": int(len(site_ids)),
        "n_sites_with_coordinates": int(
            sites[["latitude", "longitude"]].notna().all(axis=1).sum()
        ),
        "n_sites_mapped": int(sites["mapped"].sum()),
        "n_plots": int(len(plots)),
        "n_plots_mapped": int(plots["mapped"].sum()),
        "n_tree_rows": all_tree_rows,
        "n_live_valid_trees": int(len(live)),
        "n_live_trees_mapped": int(live["mapped"].sum()),
        "n_species": int(live["species"].nunique()),
        "age_min": float(live["age"].min()) if live["age"].notna().any() else None,
        "age_max": float(live["age"].max()) if live["age"].notna().any() else None,
        "bec_zone_counts": live["bec_zone"].value_counts().to_dict(),
        "dropped": dropped,
    }
    return Tsa24Data(sites=sites, plots=plots, trees=live, stats=stats)


def _modal_limit(frame: pd.DataFrame) -> float:
    values = pd.to_numeric(frame["dbh_limit_cm"], errors="coerce").dropna().round(3)
    if values.empty:
        raise ValueError("no DBH limits available for the target stratum")
    counts = values.value_counts()
    tied = [float(value) for value, count in counts.items() if count == counts.iloc[0]]
    return min(tied)


def _weighted_quantile(
    values: np.ndarray, weights: np.ndarray, probabilities: Sequence[float]
) -> np.ndarray:
    order = np.argsort(values)
    ordered_values = np.asarray(values, dtype=float)[order]
    ordered_weights = np.asarray(weights, dtype=float)[order]
    total = float(ordered_weights.sum())
    if total <= 0.0:
        raise ValueError("weighted quantile needs positive total weight")
    cdf = np.cumsum(ordered_weights) / total
    return np.interp(np.asarray(probabilities, dtype=float), cdf, ordered_values)


def _fitted_quantiles(fit: DistributionFit, probabilities: Sequence[float]) -> np.ndarray:
    candidate = fit.selected
    lower, upper = float(fit.dbh_limit_cm), float(fit.support_upper_cm)
    probabilities = np.asarray(probabilities, dtype=float)
    if candidate.distribution in {"weibull", "gamma"}:
        return truncated_quantile(
            candidate.distribution,
            candidate.parameters,
            (lower, upper),
            probabilities,
        )
    grid = np.linspace(lower, upper, 1024)
    if candidate.distribution == "mixture":
        components = [
            MixtureComponentFit(name="gamma", weight=float(weight), parameters=dict(params))
            for weight, params in zip(
                candidate.component_weights or (),
                candidate.component_parameters or (),
                strict=False,
            )
        ]
        raw = mixture_cdf(grid, components)
        lower_mass = float(mixture_cdf(np.array([lower]), components)[0])
    else:
        definition = get_distribution(candidate.distribution)
        raw = cumulative_trapezoid(
            np.clip(definition.pdf(grid, candidate.parameters), 1e-12, None),
            grid,
            initial=0.0,
        )
        lower_mass = float(
            np.interp(lower, grid, raw)
        )
    conditional = np.clip((raw - lower_mass) / max(1.0 - lower_mass, 1e-12), 0.0, 1.0)
    unique = np.unique(conditional)
    if unique.size < 2:
        raise ValueError(f"cannot invert fitted {candidate.distribution} CDF")
    # np.interp needs increasing x; conditional is monotone non-decreasing.
    return np.interp(probabilities, conditional, grid)


def _fit_subset(
    observations: pd.DataFrame,
    fit: DistributionFit,
    age_window: tuple[float, float],
) -> pd.DataFrame:
    subset = observations[observations["species"].eq(fit.species)].copy()
    if fit.fit_level in ECO_LEVELS:
        key = getattr(fit, fit.fit_level)
        subset = subset[subset[fit.fit_level].astype(str).str.strip().eq(str(key))]
    subset = subset[
        (subset["age"] >= age_window[0]) & (subset["age"] <= age_window[1])
    ]
    subset = subset[
        np.isclose(
            pd.to_numeric(subset["dbh_limit_cm"], errors="coerce"),
            fit.dbh_limit_cm,
            atol=1e-6,
        )
    ]
    return subset


def validate_dbh_fits(
    observations: pd.DataFrame,
    fits: Sequence[DistributionFit],
    age_window: tuple[float, float],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    probabilities = (0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95)
    rows: list[dict[str, Any]] = []
    summaries: dict[str, Any] = {}
    for fit in fits:
        subset = _fit_subset(observations, fit, age_window)
        if subset.empty:
            raise ValueError(f"no validation trees for {fit.species} ({fit.fit_id})")
        observed = _weighted_quantile(
            subset["dbh_cm"].to_numpy(dtype=float),
            subset["expansion_factor"].to_numpy(dtype=float),
            probabilities,
        )
        fitted = _fitted_quantiles(fit, probabilities)
        differences = fitted - observed
        for probability, observed_value, fitted_value, difference in zip(
            probabilities, observed, fitted, differences, strict=False
        ):
            rows.append(
                {
                    "species": fit.species,
                    "fit_id": fit.fit_id,
                    "evidence_tier": fit.evidence_tier,
                    "distribution": fit.selected.distribution,
                    "quantile": probability,
                    "observed_dbh_cm": float(observed_value),
                    "fitted_dbh_cm": float(fitted_value),
                    "difference_cm": float(difference),
                }
            )
        summaries[fit.species] = {
            "fit_id": fit.fit_id,
            "evidence_tier": fit.evidence_tier,
            "distribution": fit.selected.distribution,
            "n_trees": fit.n_trees,
            "n_plots": fit.n_plots,
            "n_bins": fit.n_bins,
            "dbh_limit_cm": fit.dbh_limit_cm,
            "aic": fit.selected.aic,
            "ks": fit.selected.ks,
            "quantile_bias_cm": float(np.mean(differences)),
            "quantile_mae_cm": float(np.mean(np.abs(differences))),
            "observed_quantiles_cm": {
                f"{probability:.2f}": float(value)
                for probability, value in zip(probabilities, observed, strict=False)
            },
            "fitted_quantiles_cm": {
                f"{probability:.2f}": float(value)
                for probability, value in zip(probabilities, fitted, strict=False)
            },
        }
    return pd.DataFrame(rows), summaries


def _composition_frame(
    trees: pd.DataFrame,
    *,
    target_ecodistrict: str,
    target_bec_zone: str,
    age_window: tuple[float, float],
    min_share: float,
    leading_species_group: set[str] | None = None,
) -> tuple[pd.DataFrame, dict[str, float], list[dict[str, Any]], dict[str, Any]]:
    subset = trees[
        trees["mapped"]
        & trees["ecodistrict"].astype(str).eq(target_ecodistrict)
        & trees["bec_zone"].astype(str).eq(target_bec_zone)
        & (trees["age"] >= age_window[0])
        & (trees["age"] <= age_window[1])
    ].copy()
    if subset.empty:
        raise ValueError(
            f"no trees for composition in {target_ecodistrict}/{target_bec_zone} "
            f"at ages {age_window[0]:g}-{age_window[1]:g}"
        )
    n_plots_all = int(subset["plot_id"].nunique())
    fallback = False
    if leading_species_group:
        leading = (
            subset.groupby(["plot_id", "species"], as_index=False)["expansion_factor"]
            .sum()
            .sort_values(["plot_id", "expansion_factor"], ascending=[True, False])
            .drop_duplicates("plot_id")
            .rename(columns={"species": "leading_species"})
        )
        keep = set(
            leading.loc[
                leading["leading_species"].isin(leading_species_group), "plot_id"
            ].astype(str)
        )
        filtered = subset[subset["plot_id"].astype(str).isin(keep)].copy()
        if filtered["plot_id"].nunique() >= 5:
            subset = filtered
        else:
            fallback = True
    filter_info = {
        "leading_species_group": sorted(leading_species_group or ()),
        "n_plots_all": n_plots_all,
        "n_plots_used": int(subset["plot_id"].nunique()),
        "fallback_to_all_plots": fallback,
    }
    totals = (
        subset.groupby("species", as_index=False)["expansion_factor"]
        .sum()
        .rename(columns={"expansion_factor": "tph"})
    )
    total_tph = float(totals["tph"].sum())
    totals["raw_share"] = totals["tph"] / total_tph
    totals["taper_supported"] = totals["species"].isin(set(KOZAK_SPECIES))
    totals["retained"] = totals["taper_supported"] & (
        totals["raw_share"] >= float(min_share)
    )
    totals = totals.sort_values("raw_share", ascending=False).reset_index(drop=True)
    retained = totals[totals["retained"]].copy()
    if retained.empty:
        raise ValueError("no taper-supported species reaches the minimum composition share")
    raw_weights = dict(zip(retained["species"], retained["raw_share"], strict=False))
    weights = normalize_species_weights(raw_weights, min_share=0.0)
    dropped = [
        {
            "species": str(row.species),
            "raw_share": float(row.raw_share),
            "reason": (
                "unsupported_by_taper_registry"
                if not bool(row.taper_supported)
                else "below_minimum_species_share"
            ),
        }
        for row in totals.itertuples(index=False)
        if not bool(row.retained) and float(row.raw_share) > 0.0
    ]
    totals["final_share"] = totals["species"].map(weights).fillna(0.0)
    return totals, weights, dropped, filter_info


def _build_matrix_summary(matrix: pd.DataFrame) -> dict[str, Any]:
    logs = matrix[matrix["record_type"].eq("log")]
    residue = matrix[matrix["record_type"].eq("residue")]
    product = (
        logs.groupby("product", as_index=False)["volume_m3"]
        .sum()
        .sort_values("volume_m3", ascending=False)
    )
    species = (
        logs.groupby("species", as_index=False)
        .agg(volume_m3=("volume_m3", "sum"), n_logs=("n_logs", "sum"))
        .sort_values("volume_m3", ascending=False)
    )
    sort_mix = (
        logs.groupby(["species", "product", "sort_code"], as_index=False)[
            "volume_m3"
        ]
        .sum()
        .sort_values(["species", "volume_m3"], ascending=[True, False])
    )
    return {
        "n_rows": int(len(matrix)),
        "n_logs": int(logs["n_logs"].sum()),
        "n_species": int(logs["species"].nunique()),
        "n_sorts": int(logs["sort_code"].nunique()),
        "recovered_log_m3": float(logs["volume_m3"].sum()),
        "residue_m3": float(residue["volume_m3"].sum()),
        "taper_total_m3": float(residue["taper_total_m3"].sum()),
        "residue_odt": float(residue["residue_odt"].sum()),
        "agb_wood_odt": float(residue["agb_wood_odt"].sum()),
        "product_volumes": product.to_dict(orient="records"),
        "species_volumes": species.to_dict(orient="records"),
        "sort_mix": sort_mix.to_dict(orient="records"),
    }


def _btc_grade_proxy(
    btc_row: pd.Series,
    matrix: pd.DataFrame,
    *,
    area_ha: float,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    grade_columns = [column for column in btc_row.index if str(column).startswith("Logs_Grade_")]
    residual_columns = [column for column in btc_row.index if str(column).startswith("Residual_")]
    grade_total = float(btc_row.get("grade_total_m3ha", math.nan))
    rows: list[dict[str, Any]] = []
    if grade_columns and math.isfinite(grade_total) and grade_total > 0.0:
        for column in grade_columns:
            value = float(btc_row.get(column, math.nan))
            rows.append(
                {
                    "series": "btc_log_grade",
                    "label": column.replace("Logs_Grade_", ""),
                    "volume_m3ha": value,
                    "share_of_reference": value / grade_total,
                    "share_basis": "btc_grade_total_m3ha",
                    "comparison_status": "proxy_only_not_grade_equivalent",
                }
            )
        for column in residual_columns:
            value = float(btc_row.get(column, math.nan))
            rows.append(
                {
                    "series": "btc_mill_residue",
                    "label": column.replace("Residual_", ""),
                    "volume_m3ha": value,
                    "share_of_reference": value / grade_total,
                    "share_basis": "btc_grade_total_m3ha",
                    "comparison_status": "proxy_only_not_grade_equivalent",
                }
            )
    logs = matrix[matrix["record_type"].eq("log")]
    residue = matrix[matrix["record_type"].eq("residue")]
    taper_total = float(residue["taper_total_m3"].sum())
    if taper_total > 0.0:
        for product, volume in (
            logs.groupby("product")["volume_m3"].sum().sort_values(ascending=False).items()
        ):
            rows.append(
                {
                    "series": "assortment_product",
                    "label": str(product),
                    "volume_m3ha": float(volume) / area_ha,
                    "share_of_reference": float(volume) / taper_total,
                    "share_basis": "assortment_taper_total_m3",
                    "comparison_status": "proxy_only_not_grade_equivalent",
                }
            )
        rows.append(
            {
                "series": "assortment_residue",
                "label": "harvest_residue",
                "volume_m3ha": float(residue["volume_m3"].sum()) / area_ha,
                "share_of_reference": float(residue["volume_m3"].sum()) / taper_total,
                "share_basis": "assortment_taper_total_m3",
                "comparison_status": "proxy_only_not_grade_equivalent",
            }
        )
    summary = {
        "status": "reported_proxy_only" if rows else "not_available",
        "btc_grade_total_m3ha": grade_total,
        "grade_columns": [column.replace("Logs_Grade_", "") for column in grade_columns],
        "residual_columns": [column.replace("Residual_", "") for column in residual_columns],
        "caveat": (
            "BTC Logs_Grade_* are TIPSY grade volumes, while the assortment matrix "
            "uses dimension-only sawlog/pulpwood/other sorts. The rows are reported "
            "side by side for context; no grade equivalence is asserted."
        ),
    }
    return pd.DataFrame(rows), summary


def _fmt(value: Any, digits: int = 2) -> str:
    if value is None:
        return "n/a"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if not math.isfinite(number):
        return "n/a"
    return f"{number:,.{digits}f}"


def _build_report(
    *,
    data: Tsa24Data,
    target: SynthesisTarget,
    target_context: Mapping[str, Any],
    fits: Sequence[DistributionFit],
    height_bundle: HeightModelBundle,
    stand: SynthesizedStand,
    matrix: pd.DataFrame,
    conservation: Mapping[str, Any],
    validation: Mapping[str, Any],
    btc_proxy: Mapping[str, Any],
    summary: Mapping[str, Any],
) -> str:
    fit_rows = []
    for fit in fits:
        candidate = fit.selected
        fit_rows.append(
            "| {species} | {tier} | {level} | {n_trees} | {n_plots} | {limit} | "
            "{distribution} | {aic} | {ks} |".format(
                species=fit.species,
                tier=fit.evidence_tier,
                level=fit.fit_level,
                n_trees=fit.n_trees,
                n_plots=fit.n_plots,
                limit=_fmt(fit.dbh_limit_cm, 1),
                distribution=candidate.distribution,
                aic=_fmt(candidate.aic, 1),
                ks=_fmt(candidate.ks, 3),
            )
        )
    height_rows = [
        "| {species} | {tier} | {level} | {n} | {a} | {b} | {rmse} | {bias} |".format(
            species=model.species,
            tier=model.evidence_tier,
            level=model.fit_level,
            n=model.n,
            a=_fmt(model.a, 5),
            b=_fmt(model.b, 4),
            rmse=_fmt(model.rmse_m, 2),
            bias=_fmt(model.bias_m, 2),
        )
        for model in height_bundle.models
    ]
    species_rows = [
        "| {species} | {weight:.3f} | {n} | {tph} | {qmd} | {volume} |".format(
            species=row["species"],
            weight=row["weight"],
            n=row["n_trees"],
            tph=_fmt(row["tph"], 1),
            qmd=_fmt(row["qmd_cm"], 2),
            volume=_fmt(row["taper_volume_m3ha"], 1),
        )
        for row in stand.species_summary
    ]
    product_rows = [
        "| {product} | {volume} | {share} |".format(
            product=row["product"],
            volume=_fmt(row["volume_m3"], 2),
            share=_fmt(
                row["volume_m3"] / max(conservation["taper_total_m3"], 1e-12), 4
            ),
        )
        for row in summary["assortment"]["product_volumes"]
    ]
    checks = validation["checks"]
    check_rows = [
        f"- **{name}**: {'PASS' if bool(value.get('pass')) else 'FAIL'} — {value.get('detail', '')}"
        for name, value in checks.items()
    ]
    lines = [
        "# TSA24 stand-to-tree disaggregation — prototype validation",
        "",
        f"Generated: `{summary['generated_utc']}`",
        "",
        f"Status: **{summary['status']}**",
        "",
        "> This is a prototype contract and a first TSA24 demonstration. It is not an",
        "> operational national yield model. The assortment sorts are dimension-only;",
        "> no official BC log grade is assigned.",
        "",
        "## 1. Contract",
        "",
        "- DBH shape comes from the selected RSYC-stratified distribution.",
        "- Height comes from the hierarchical height-diameter model.",
        "- Species composition comes from the TSA24 plot subset.",
        "- Volume scale comes from the BTC managed-AU target, not from the distribution.",
        "- The bucking layer is the existing BC interior rule set and Kozak taper.",
        "- `StandDBHSampler` is not used for the censored fit because it has no support",
        "  bound; the documented `sample_truncated`/`sample_mixture` samplers are used",
        "  instead. This is a sampling-boundary adapter, not a refit.",
        "",
        "## 2. TSA24 subset",
        "",
        f"- Sites selected: **{data.stats['n_sites_selected']:,}**; "
        f"with coordinates: **{data.stats['n_sites_with_coordinates']:,}**; "
        f"mapped to RSYC ecodistricts: **{data.stats['n_sites_mapped']:,}**.",
        f"- Plots: **{data.stats['n_plots']:,}**; live valid trees: "
        f"**{data.stats['n_live_valid_trees']:,}**; species: "
        f"**{data.stats['n_species']}**.",
        f"- Dropped: {data.stats['dropped']}.",
        "",
        "## 3. Target stand",
        "",
        f"- BTC AU `{target_context['au_id']}` / `{target_context['stratum_code']}` "
        f"({target_context['si_level']}) at age **{target.age:g}**.",
        f"- Target volume: **{target.volume_m3ha:,.1f} m3/ha** × "
        f"{target.area_ha:g} ha = **{target.target_volume_m3:,.1f} m3**.",
        f"- Target ecodistrict: **{target_context['ecodistrict']}**; "
        f"initial TPH: **{target.tph_total:,.1f}**.",
        f"- Species weights (retained, sum=1): "
        f"{', '.join(f'{k}={v:.3f}' for k, v in sorted(target.species_weights.items()))}.",
        f"- Composition exclusions: {target_context['composition_exclusions']}.",
        f"- Composition plot filter: {target_context.get('composition_filter')}.",
        "",
        "## 4. DBH fits",
        "",
        "| species | tier | level | trees | plots | limit cm | distribution | AIC | KS |",
        "|---|---|---|---:|---:|---:|---|---:|---:|",
        *fit_rows,
        "",
        "Hierarchical threshold: at least **30 trees** and **5 plots** at the direct",
        "ecodistrict level. The fit falls back to ecoregion, ecoprovince, ecozone, and",
        "then the TSA24 species pool; the fallback level is recorded on every row.",
        "Plots with a non-modal DBH limit are excluded from the shape fit to avoid",
        "mixing different truncation points. Candidate AIC/KS are scored on one common",
        "support-conditioned binned likelihood; native estimator metrics are also kept.",
        "",
        f"General all-age fits: **{summary['dbh_fits']['general']['n_fits']}** strata "
        f"(`stratum_fit` {summary['dbh_fits']['general']['evidence_tiers']['stratum_fit']}, "
        f"`pooled_fit` {summary['dbh_fits']['general']['evidence_tiers']['pooled_fit']}, "
        f"`default` {summary['dbh_fits']['general']['evidence_tiers']['default']}); "
        f"selected KS median **{_fmt(summary['dbh_fits']['general']['selected_ks']['median'], 3)}**, "
        f"max **{_fmt(summary['dbh_fits']['general']['selected_ks']['max'], 3)}**, "
        f"high-KS (>0.20) **{summary['dbh_fits']['general']['selected_ks']['n_high_ks']}**. "
        "The full per-candidate table is `distribution_fits.csv`; poor general strata "
        "are retained rather than hidden.",
        "",
        "## 5. Height models",
        "",
        "| species | tier | level | n | a | b | RMSE m | bias m |",
        "|---|---|---|---:|---:|---:|---:|---:|",
        *height_rows,
        "",
        "## 6. Synthetic tree list",
        "",
        f"- Trees: **{len(stand.trees):,}**; scale factor: "
        f"**{stand.scale_factor:.6f}**; achieved taper total: "
        f"**{stand.achieved_taper_volume_m3:,.3f} m3**.",
        f"- TPH: **{stand.tph:,.1f}**; basal area: "
        f"**{stand.basal_area_m2ha:,.2f} m2/ha**; QMD: **{stand.qmd_cm:,.2f} cm**.",
        "",
        "| species | weight | trees | TPH | QMD cm | taper m3/ha |",
        "|---|---:|---:|---:|---:|---:|",
        *species_rows,
        "",
        "## 7. Assortment matrix",
        "",
        f"- Logs: **{summary['assortment']['n_logs']:,}**; recovered: "
        f"**{summary['assortment']['recovered_log_m3']:,.2f} m3**; residue: "
        f"**{summary['assortment']['residue_m3']:,.2f} m3**; taper total: "
        f"**{summary['assortment']['taper_total_m3']:,.2f} m3**.",
        f"- Residue: **{summary['assortment']['residue_odt']:,.2f} ODT**; wood AGB "
        f"proxy: **{summary['assortment']['agb_wood_odt']:,.2f} ODT**.",
        "",
        "| product | volume m3 | share of taper |",
        "|---|---:|---:|",
        *product_rows,
        "",
        "The matrix is written as `log`, `residue`, and `total` record types. Residue",
        "is taper total minus recovered logs; the species ledger carries density, ODT,",
        "green tonnes, chip tonnes, and a wood-only AGB proxy. The BTC AGB target is",
        "reported beside the wood proxy; it is not expected to match because BTC AGB",
        "includes bark, branches, and foliage. The near-zero pulpwood share is a",
        "property of the dimension-only sawlog rules (12.5 cm top, 5.1 m minimum),",
        "which dominate the value objective; it is not evidence that pulp fibre is absent.",
        "BTC mill residue columns are not logging slash.",
        "",
        "## 8. Validation",
        "",
        *check_rows,
        "",
        "DBH quantile validation is in `dbh_validation.csv`; conservation is in",
        "`validation_summary.json`. The optional BTC grade proxy is **not** a grade",
        "validation: BTC TIPSY grades and dimension-only sorts are different objects.",
        "",
        "## 9. Evidence tiers",
        "",
        "| tier | use in this run |",
        "|---|---|",
        "| `stratum_fit` | direct ecodistrict/species DBH or height fit |",
        "| `pooled_fit` | ecoregion/ecoprovince/ecozone fallback |",
        "| `default` | TSA24 species-only fallback |",
        "",
        "## 10. Artifacts",
        "",
        "- `distribution_fits.csv`, `height_models.csv`, `height_models_general.csv`",
        "- `synthetic_trees.csv`, `species_composition.csv`, `target_stand.json`",
        "- `assortment_matrix.csv`, `bucked_logs.csv`, `residue_ledger.csv`",
        "- `dbh_validation.csv`, `btc_grade_proxy.csv`, `validation_summary.json`, `manifest.json`",
        "",
    ]
    return "\n".join(lines) + "\n"


def run(args: argparse.Namespace) -> dict[str, Any]:
    started = time.time()
    repo_root = Path(args.repo_root).resolve()
    data_root = Path(args.data_root).resolve()
    out_dir = Path(args.out_dir).resolve()
    assert_no_unexpected_deletions(repo_root)

    data = load_tsa24_data(repo_root, data_root, tsa_name=args.tsa_name)
    mapped_trees = data.trees[data.trees["mapped"]].copy()
    mapped_trees = mapped_trees[
        mapped_trees["dbh_limit_cm"].notna() & mapped_trees["age"].notna()
    ].copy()
    if mapped_trees.empty:
        raise ValueError("no mapped trees with DBH limits and ages")

    btc_curves_path = (
        data_root
        / "derived"
        / "national"
        / "bucking"
        / "tsa24_grade_curves"
        / "managed_au_curves.csv"
    )
    biomass_path = (
        data_root
        / "derived"
        / "national"
        / "bucking"
        / "tsa24_grade_curves"
        / "biomass_au_curves.csv"
    )
    btc = pd.read_csv(btc_curves_path, low_memory=False)
    target_row = btc[
        btc["au_id"].astype(int).eq(int(args.au_id))
        & np.isclose(pd.to_numeric(btc["age"], errors="coerce"), float(args.age))
    ]
    if target_row.empty:
        raise ValueError(
            f"BTC managed AU {args.au_id} has no row at age {args.age}; "
            "check managed_au_curves.csv"
        )
    btc_row = target_row.iloc[0]
    target_volume_m3ha = float(btc_row["volume"])
    target_bec_zone = str(btc_row["stratum_code"]).split("_")[0].upper()
    target_stratum_code = str(btc_row["stratum_code"])
    target_si_level = str(btc_row["si_level"])
    au_leading_species = str(btc_row.get("leading_species", "")).strip().upper()
    leading_species_group = AU_LEADING_SPECIES_GROUPS.get(au_leading_species)

    target_ecodistrict = args.ecodistrict
    if target_ecodistrict is None:
        candidates = data.plots[
            data.plots["mapped"]
            & data.plots["bec_zone"].astype(str).eq(target_bec_zone)
            & data.plots["ecodistrict"].notna()
        ]
        if candidates.empty:
            raise ValueError(f"no mapped {target_bec_zone} plots for target selection")
        target_ecodistrict = str(candidates["ecodistrict"].value_counts().index[0])
    target_ecodistrict = str(target_ecodistrict)

    age_window = (
        max(0.0, float(args.age) - float(args.age_window_years)),
        float(args.age) + float(args.age_window_years),
    )
    composition, species_weights, composition_exclusions, composition_filter = _composition_frame(
        data.trees,
        target_ecodistrict=target_ecodistrict,
        target_bec_zone=target_bec_zone,
        age_window=age_window,
        min_share=float(args.min_species_share),
        leading_species_group=leading_species_group,
    )
    target_plots = data.plots[
        data.plots["mapped"]
        & data.plots["ecodistrict"].astype(str).eq(target_ecodistrict)
        & data.plots["bec_zone"].astype(str).eq(target_bec_zone)
        & (data.plots["age"] >= age_window[0])
        & (data.plots["age"] <= age_window[1])
    ].copy()
    if leading_species_group:
        filtered_plots = target_plots[
            target_plots["leading_species"].astype(str).isin(leading_species_group)
        ]
        if filtered_plots["plot_id"].nunique() >= 5:
            target_plots = filtered_plots
            composition_filter["target_plot_filter"] = "leading_species_group"
        else:
            composition_filter["target_plot_filter"] = "fallback_all_plots"
    if target_plots.empty:
        raise ValueError(
            f"no target plots in {target_ecodistrict}/{target_bec_zone} at ages "
            f"{age_window[0]:g}-{age_window[1]:g}"
        )
    tph_total = float(
        pd.to_numeric(target_plots["tph"], errors="coerce").dropna().mean()
    )
    if not math.isfinite(tph_total) or tph_total <= 0.0:
        raise ValueError("target TPH is not positive")
    dbh_limit = _modal_limit(target_plots)

    target_agb_odt_ha: float | None = None
    if biomass_path.exists():
        biomass = pd.read_csv(biomass_path, low_memory=False)
        biomass_row = biomass[
            biomass["au_id"].astype(int).eq(int(args.au_id))
            & np.isclose(pd.to_numeric(biomass["age"], errors="coerce"), float(args.age))
        ]
        if not biomass_row.empty:
            target_agb_odt_ha = float(biomass_row.iloc[0]["agb_odt_ha"])

    fit_failures: list[dict[str, Any]] = []
    general_fits = fit_dbh_distributions(
        mapped_trees,
        scope="tsa24_all_ages",
        min_trees=int(args.min_trees),
        min_plots=int(args.min_plots),
        min_plot_trees=10,
        bin_width_cm=float(args.bin_width_cm),
        random_state=int(args.seed),
        failures=fit_failures,
    )
    au_targets = [
        {
            "species": code,
            "ecodistrict": target_ecodistrict,
            "target_level": "au",
            "target_id": f"au:{args.au_id}|age:{args.age:g}|{target_ecodistrict}|{code}",
        }
        for code in sorted(species_weights)
    ]
    au_fits = fit_dbh_distributions(
        mapped_trees,
        scope="tsa24_au_age_window",
        targets=au_targets,
        age_window=age_window,
        min_trees=int(args.min_trees),
        min_plots=int(args.min_plots),
        min_plot_trees=10,
        bin_width_cm=float(args.bin_width_cm),
        random_state=int(args.seed),
    )
    if len(au_fits) != len(species_weights):
        raise RuntimeError(
            f"expected {len(species_weights)} AU fits, got {len(au_fits)}"
        )

    height_obs = data.trees[data.trees["mapped"] & data.trees["height_m"].notna()].copy()
    height_obs = height_obs[height_obs["height_m"] > 1.3].copy()
    general_heights = fit_height_models(
        height_obs,
        scope="tsa24_all_ages",
        min_n=int(args.min_height_n),
    )
    au_height_targets = [
        {
            "species": code,
            "ecodistrict": target_ecodistrict,
            "target_level": "au",
            "target_id": f"au:{args.au_id}|age:{args.age:g}|{target_ecodistrict}|{code}",
        }
        for code in sorted(species_weights)
    ]
    au_heights = fit_height_models(
        height_obs,
        scope="tsa24_au_age_window",
        targets=au_height_targets,
        age_window=age_window,
        min_n=int(args.min_height_n),
    )
    height_by_species = au_heights.by_species()
    missing_heights = sorted(set(species_weights).difference(height_by_species))
    if missing_heights:
        raise RuntimeError(f"missing AU height models: {', '.join(missing_heights)}")

    specs: list[DistributionSpec] = []
    for fit in au_fits:
        candidate = fit.selected
        specs.append(
            DistributionSpec(
                species=fit.species,
                distribution=candidate.distribution,
                parameters=dict(candidate.parameters),
                support=(float(fit.dbh_limit_cm), float(fit.support_upper_cm)),
                component_weights=candidate.component_weights,
                component_parameters=candidate.component_parameters,
                fit_id=fit.fit_id,
                evidence_tier=fit.evidence_tier,
            )
        )
    max_dbh = max(spec.support[1] for spec in specs)
    target = SynthesisTarget(
        target_id=f"au:{args.au_id}|age:{args.age:g}|{target_ecodistrict}",
        target_level="au",
        species_weights=species_weights,
        tph_total=tph_total,
        volume_m3ha=target_volume_m3ha,
        area_ha=float(args.area_ha),
        bec_zone=target_bec_zone,
        age=float(args.age),
        dbh_limit_cm=dbh_limit,
        max_dbh_cm=max_dbh,
        seed=int(args.seed),
        n_trees=int(args.n_trees),
        target_agb_odt_ha=target_agb_odt_ha,
        source=f"BTC managed_au_curves.csv au_id={args.au_id} age={args.age:g}",
    )

    taper_registry = TaperRegistry(parameter_path=default_kozak_parameter_path())
    stand = synthesize_tree_list(
        target,
        distributions=specs,
        height_models=tuple(height_by_species[code] for code in sorted(species_weights)),
        taper_registry=taper_registry,
    )

    rules_path = repo_root / "workflows" / "national" / "bucking" / "rules" / "bc_interior.yaml"
    price_path = default_nemora_root() / "config" / "log_prices" / "bc_interior_2026q3.yaml"
    rules = load_grade_rules(rules_path)
    prices = load_price_profile(price_path)
    batch = buck_trees(
        stand.trees,
        taper_registry,
        rules,
        prices,
        jurisdiction="BC",
        skip_invalid=False,
    )
    trees_by_id = {tree.tree_id: tree for tree in stand.trees}
    logs = logs_frame(batch.logs, trees_by_id)
    residue = residue_frame(batch.residue, trees_by_id)
    matrix = build_assortment_matrix(
        logs,
        residue,
        stand.to_frame(),
        area_ha=float(args.area_ha),
        btc_agb_odt_ha=target_agb_odt_ha,
    )
    conservation = conservation_summary(matrix)
    if not conservation["pass"]:
        raise RuntimeError(f"volume conservation failed: {conservation}")

    dbh_validation, dbh_summary = validate_dbh_fits(mapped_trees, au_fits, age_window)
    matrix_summary = _build_matrix_summary(matrix)
    btc_proxy_frame, btc_proxy_summary = _btc_grade_proxy(
        btc_row, matrix, area_ha=float(args.area_ha)
    )

    target_context = {
        "au_id": int(args.au_id),
        "stratum_code": target_stratum_code,
        "si_level": target_si_level,
        "leading_species": str(btc_row.get("leading_species", "")),
        "ecodistrict": target_ecodistrict,
        "bec_zone": target_bec_zone,
        "age": float(args.age),
        "age_window": [float(age_window[0]), float(age_window[1])],
        "volume_m3ha": target_volume_m3ha,
        "target_agb_odt_ha": target_agb_odt_ha,
        "tph_total": tph_total,
        "dbh_limit_cm": dbh_limit,
        "species_weights": species_weights,
        "composition_exclusions": composition_exclusions,
        "composition_filter": composition_filter,
        "source": "BTC managed_au_curves.csv",
    }

    checks = {
        "dbh_fit_quantiles": {
            "pass": all(
                item["quantile_mae_cm"] < 3.0 for item in dbh_summary.values()
            ),
            "detail": (
                "per-species weighted quantile MAE < 3.0 cm; "
                f"max={max((item['quantile_mae_cm'] for item in dbh_summary.values()), default=math.nan):.3f} cm"
            ),
        },
        "target_volume": {
            "pass": abs(
                stand.achieved_taper_volume_m3 - stand.target_volume_m3
            )
            <= max(1e-6, 1e-8 * abs(stand.target_volume_m3)),
            "detail": (
                f"achieved {stand.achieved_taper_volume_m3:.6f} m3 vs target "
                f"{stand.target_volume_m3:.6f} m3; scale {stand.scale_factor:.9f}"
            ),
        },
        "volume_conservation": {
            "pass": bool(conservation["pass"] and conservation["total_row_pass"]),
            "detail": (
                f"logs {conservation['recovered_log_m3']:.6f} + residue "
                f"{conservation['residue_m3']:.6f} = "
                f"{conservation['recovered_plus_residue_m3']:.6f} m3 vs taper "
                f"{conservation['taper_total_m3']:.6f} m3; "
                f"error {conservation['absolute_error_m3']:.3g} m3"
            ),
        },
        "species_weights_sum": {
            "pass": math.isclose(sum(target.species_weights.values()), 1.0, rel_tol=1e-9),
            "detail": f"sum={sum(target.species_weights.values()):.12f}",
        },
        "no_negative_values": {
            "pass": not any(
                (pd.to_numeric(matrix[column], errors="coerce").dropna() < 0.0).any()
                for column in (
                    "volume_m3",
                    "residue_m3",
                    "residue_odt",
                    "agb_wood_odt",
                    "n_logs",
                )
            ),
            "detail": "checked matrix volume, residue, ODT, AGB proxy, and log counts",
        },
    }

    summary = {
        "pilot": PILOT_NAME,
        "version": PILOT_VERSION,
        "status": "prototype_complete" if all(v["pass"] for v in checks.values()) else "failed_checks",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "runtime_seconds": time.time() - started,
        "repo_root": str(repo_root),
        "data_root": str(data_root),
        "code_version": {
            "parent_commit": _git(repo_root, "rev-parse", "HEAD"),
            "parent_branch": _git(repo_root, "branch", "--show-current"),
            "data_commit": _git(data_root, "rev-parse", "HEAD"),
            "data_branch": _git(data_root, "branch", "--show-current"),
            "dirty": bool(_git(repo_root, "status", "--porcelain")),
        },
        "inputs": {
            "btc_managed_au_curves": str(btc_curves_path),
            "btc_biomass_curves": str(biomass_path),
            "faib_psp_header": str(data_root / "raw" / "bc_faib_ground_plots" / "psp" / "faib_header.csv.gz"),
            "faib_psp_visits": str(data_root / "raw" / "bc_faib_ground_plots" / "psp" / "faib_sample_byvisit.csv.gz"),
            "harmonized_sites": str(data_root / "derived" / "fvsca_calibration" / "v0" / "sites.csv"),
            "harmonized_visits": str(data_root / "derived" / "fvsca_calibration" / "v0" / "visits.csv"),
            "harmonized_trees": str(data_root / "derived" / "fvsca_calibration" / "v0" / "trees.csv.gz"),
            "rsyc_models": str(data_root / "raw" / "national" / "rsyc" / "RSYC_models.zip"),
            "rsyc_ecostrat": str(data_root / "raw" / "national" / "rsyc" / "RSYC_ecostrat.gpkg"),
            "rsyc_tiles": str(data_root / "raw" / "national" / "rsyc" / "RSYC_tiles.gpkg"),
            "grade_rules": str(rules_path),
            "price_profile": str(price_path),
        },
        "tsa24": data.stats,
        "target": target.to_dict(),
        "target_context": target_context,
        "dbh_fits": {
            "general": {
                "n_fits": len(general_fits),
                "n_failures": len(fit_failures),
                "failures": fit_failures[:20],
                "selected_distributions": {
                    fit.selected.distribution: sum(
                        1
                        for item in general_fits
                        if item.selected.distribution == fit.selected.distribution
                    )
                    for fit in general_fits
                },
                "evidence_tiers": {
                    tier: sum(1 for item in general_fits if item.evidence_tier == tier)
                    for tier in ("stratum_fit", "pooled_fit", "default")
                },
                "selected_ks": {
                    "min": min(
                        (item.selected.ks for item in general_fits), default=math.nan
                    ),
                    "median": float(
                        np.median([item.selected.ks for item in general_fits])
                    )
                    if general_fits
                    else math.nan,
                    "max": max(
                        (item.selected.ks for item in general_fits), default=math.nan
                    ),
                    "n_high_ks": sum(
                        1 for item in general_fits if item.selected.ks > 0.20
                    ),
                },
            },
            "au": dbh_summary,
        },
        "height_models": {
            "general": {
                "n_models": len(general_heights.models),
                "n_failures": len(general_heights.failures),
                "models": [record.to_row() for record in general_heights.models],
            },
            "au": {
                "n_models": len(au_heights.models),
                "n_failures": len(au_heights.failures),
                "models": [record.to_row() for record in au_heights.models],
            },
        },
        "synthesis": stand.manifest_record(),
        "assortment": matrix_summary,
        "conservation": conservation,
        "btc_grade_proxy": btc_proxy_summary,
        "checks": checks,
        "assumptions": {
            "dbh_fit": (
                "Two-stage Weibull/gamma censored fits, grouped generalized beta, "
                "and a two-component gamma mixture. Common AIC/KS are computed on "
                "one support-conditioned binned likelihood."
            ),
            "dbh_limit": (
                "Plots with the modal DBH limit are used for the shape fit; other "
                "limit classes are excluded and reported."
            ),
            "volume_target": (
                "BTC managed_au_curves volume is treated as the taper-total target "
                "for one hectare. Recovered log volume is lower by residue."
            ),
            "composition": (
                "Species weights are expansion-factor shares in the target "
                "ecodistrict, BEC zone, and age window. Species below the minimum "
                "share or unsupported by the taper registry are excluded."
            ),
            "residue": (
                "Residue is taper total minus recovered logs. ODT uses the species "
                "basic-density table; moisture 0.45 wet basis and chip yield 0.90 "
                "are planning assumptions."
            ),
            "agb": (
                "agb_wood_odt is a wood-only AGB proxy (taper total x basic density); "
                "BTC agb_odt_ha includes bark, branches, and foliage and is reported "
                "separately."
            ),
            "grade": (
                "The BC interior sorts are dimension-only; grade_status=not_assigned. "
                "BTC grade shares are a context proxy, not a validation."
            ),
        },
        "artifacts": {},
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    fit_frames = [pd.DataFrame(fit.to_rows()) for fit in list(general_fits) + list(au_fits)]
    fit_frame = pd.concat(fit_frames, ignore_index=True, sort=False) if fit_frames else pd.DataFrame()
    _write_csv(fit_frame, out_dir / "distribution_fits.csv")
    _write_csv(
        pd.DataFrame([record.to_row() for record in general_heights.models] + list(general_heights.failures)),
        out_dir / "height_models_general.csv",
    )
    _write_csv(
        pd.DataFrame([record.to_row() for record in au_heights.models] + list(au_heights.failures)),
        out_dir / "height_models.csv",
    )
    _write_csv(composition, out_dir / "species_composition.csv")
    _write_csv(stand.to_frame(), out_dir / "synthetic_trees.csv")
    _write_csv(logs, out_dir / "bucked_logs.csv")
    _write_csv(residue, out_dir / "residue_ledger.csv")
    _write_csv(matrix, out_dir / "assortment_matrix.csv")
    _write_csv(dbh_validation, out_dir / "dbh_validation.csv")
    _write_csv(btc_proxy_frame, out_dir / "btc_grade_proxy.csv")
    _write_json(target_context, out_dir / "target_stand.json")

    artifact_paths = [
        out_dir / "distribution_fits.csv",
        out_dir / "height_models.csv",
        out_dir / "height_models_general.csv",
        out_dir / "species_composition.csv",
        out_dir / "synthetic_trees.csv",
        out_dir / "bucked_logs.csv",
        out_dir / "residue_ledger.csv",
        out_dir / "assortment_matrix.csv",
        out_dir / "dbh_validation.csv",
        out_dir / "btc_grade_proxy.csv",
        out_dir / "target_stand.json",
    ]
    summary["artifacts"] = {
        path.name: _hash_record(path.name, path, repo_root) for path in artifact_paths
    }

    report = _build_report(
        data=data,
        target=target,
        target_context=target_context,
        fits=au_fits,
        height_bundle=au_heights,
        stand=stand,
        matrix=matrix,
        conservation=conservation,
        validation=summary,
        btc_proxy=btc_proxy_summary,
        summary=summary,
    )
    report_path = out_dir / "VALIDATION_REPORT.md"
    report_path.write_text(report, encoding="utf-8")
    summary["artifacts"]["VALIDATION_REPORT.md"] = _hash_record(
        "VALIDATION_REPORT.md", report_path, repo_root
    )
    _write_json(summary, out_dir / "validation_summary.json")
    validation_summary_path = out_dir / "validation_summary.json"
    validation_summary_hash = _hash_record(
        "validation_summary.json", validation_summary_path, repo_root
    )

    manifest = {
        "pilot": PILOT_NAME,
        "version": PILOT_VERSION,
        "status": summary["status"],
        "generated_utc": summary["generated_utc"],
        "code": {
            "package": "workflows.national.assortments",
            "modules": [
                "distributions.py",
                "height.py",
                "synthesize.py",
                "assortments.py",
                "facility.py",
                "rsyc.py",
                "validate_tsa24.py",
            ],
            "parent_commit": summary["code_version"]["parent_commit"],
            "data_commit": summary["code_version"]["data_commit"],
        },
        "config": {
            "au_id": int(args.au_id),
            "age": float(args.age),
            "age_window": list(age_window),
            "ecodistrict": target_ecodistrict,
            "seed": int(args.seed),
            "n_trees": int(args.n_trees),
            "min_trees": int(args.min_trees),
            "min_plots": int(args.min_plots),
            "min_height_n": int(args.min_height_n),
            "min_species_share": float(args.min_species_share),
            "bin_width_cm": float(args.bin_width_cm),
            "basic_density_table": str(
                repo_root / "workflows" / "national" / "bucking" / "rules" / "bc_basic_density.csv"
            ),
            "moisture_wet_basis": 0.45,
            "chip_yield_fraction": 0.90,
        },
        "inputs": {
            label: _hash_record(label, Path(path), repo_root)
            for label, path in summary["inputs"].items()
        },
        "outputs": {
            **summary["artifacts"],
            "validation_summary.json": validation_summary_hash,
        },
        "checks": summary["checks"],
        "notes": [
            "Prototype contract; not an operational national yield model.",
            "Assortment sorts are dimension-only; no official BC grade is assigned.",
            "BTC grade shares are context only.",
            "Fail-closed on unexpected git deletions.",
        ],
    }
    _write_json(manifest, out_dir / "manifest.json")

    if args.metadata_csv:
        metadata_path = Path(args.metadata_csv)
        if not metadata_path.is_absolute():
            metadata_path = repo_root / metadata_path
        _update_metadata(metadata_path, summary, target, stand)

    assert_no_unexpected_deletions(repo_root)
    return summary


def _update_metadata(
    path: Path,
    summary: Mapping[str, Any],
    target: SynthesisTarget,
    stand: SynthesizedStand,
) -> None:
    row = {
        "pilot": PILOT_NAME,
        "tsa": "TSA24",
        "dataset": "FAIB PSP + RSYC strata + BTC managed AU",
        "target_au": f"{summary['target_context']['au_id']}@{summary['target_context']['age']:g}",
        "target_volume_m3ha": target.volume_m3ha,
        "n_species": len(target.species_weights),
        "n_trees": len(stand.trees),
        "tph": stand.tph,
        "taper_m3": stand.achieved_taper_volume_m3,
        "recovered_m3": summary["conservation"]["recovered_log_m3"],
        "residue_m3": summary["conservation"]["residue_m3"],
        "status": summary["status"],
        "notes": (
            "RSYC-stratified censored DBH fits; hierarchical height models; "
            "BTC volume-constrained tree list; BC interior dimension sorts; "
            "dimension-only grade status; not an operational national model."
        ),
    }
    columns = list(row)
    if path.exists():
        frame = pd.read_csv(path)
        frame = frame[frame["pilot"].astype(str).ne(PILOT_NAME)]
    else:
        frame = pd.DataFrame(columns=columns)
    for column in columns:
        if column not in frame.columns:
            frame[column] = None
    frame = pd.concat([frame[columns], pd.DataFrame([row])], ignore_index=True)
    _write_csv(frame, path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--data-root", default="data")
    parser.add_argument(
        "--out-dir",
        default="data/derived/national/assortments/tsa24_prototype",
    )
    parser.add_argument("--tsa-name", default=TSA_NAME)
    parser.add_argument("--au-id", type=int, default=DEFAULT_AU_ID)
    parser.add_argument("--age", type=float, default=DEFAULT_AGE)
    parser.add_argument("--ecodistrict", default=None)
    parser.add_argument("--age-window-years", type=float, default=20.0)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--n-trees", type=int, default=DEFAULT_N_TREES)
    parser.add_argument("--area-ha", type=float, default=1.0)
    parser.add_argument("--min-trees", type=int, default=30)
    parser.add_argument("--min-plots", type=int, default=5)
    parser.add_argument("--min-height-n", type=int, default=30)
    parser.add_argument("--min-species-share", type=float, default=0.01)
    parser.add_argument("--bin-width-cm", type=float, default=2.0)
    parser.add_argument(
        "--metadata-csv",
        default="metadata/assortment-pilots.csv",
        help="Pilot metadata CSV to update (set empty to disable).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.repo_root == ".":
        args.repo_root = str(Path.cwd())
    if not Path(args.data_root).is_absolute():
        args.data_root = str(Path(args.repo_root) / args.data_root)
    if not Path(args.out_dir).is_absolute():
        args.out_dir = str(Path(args.repo_root) / args.out_dir)
    try:
        summary = run(args)
    except Exception as error:  # noqa: BLE001 - CLI boundary
        print(f"TSA24 assortment prototype failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "pilot": summary["pilot"],
                "status": summary["status"],
                "target_au": summary["target_context"]["au_id"],
                "target_age": summary["target_context"]["age"],
                "target_volume_m3": summary["target"]["target_volume_m3"],
                "achieved_taper_m3": summary["synthesis"]["achieved_taper_volume_m3"],
                "tph": summary["synthesis"]["tph"],
                "n_logs": summary["assortment"]["n_logs"],
                "recovered_m3": summary["assortment"]["recovered_log_m3"],
                "residue_m3": summary["assortment"]["residue_m3"],
                "conservation_pass": summary["conservation"]["pass"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
