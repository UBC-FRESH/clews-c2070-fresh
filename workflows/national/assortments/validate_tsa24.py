"""TSA24 stand-to-tree disaggregation prototype.

Run from the repository root with nemora on ``PYTHONPATH``::

    PYTHONPATH="${NEMORA_ROOT:?set NEMORA_ROOT}/src" \
      .venv/bin/python -m workflows.national.assortments.validate_tsa24

The prototype fits RSYC-stratified DBH distributions, fits hierarchical
height-diameter models, synthesizes a TPH- and volume-constrained tree list,
bucks it with the existing BC interior layer, and writes the assortment matrix
and validation report.

Fail-closed rule: any unexpected ``D`` in the parent or data repository status
halts the run immediately.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

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
    SPIKE_FIRST_BIN_SHARE_TRIGGER,
    SPIKE_KS_TRIGGER,
    DistributionFit,
    FitCandidate,
    fit_dbh_distributions,
)
from workflows.national.assortments.height import (
    HeightModelBundle,
    fit_height_models,
)
from workflows.national.assortments.mixtures import (
    MIXTURE_FAMILY_COMPONENTS,
    SPIKE_CONSTRAINED_FAMILIES,
    SPIKE_CONSTRAINED_SCALE_LOWER,
    SPIKE_CONSTRAINED_SHAPE_LOWER,
    SPIKE_DIAGNOSTIC_FAMILIES,
    SPIKE_FAMILIES,
    SPIKE_FAMILY_COMPONENTS,
    spike_tail_bounds,
)
from workflows.national.assortments.rsyc import assign_sites_to_ecostrata
from workflows.national.assortments.synthesize import (
    DIAMETER_SCALE_BRACKET,
    DIAMETER_SCALE_EXPANDED_BRACKET,
    DIAMETER_SCALE_RTOL,
    DIAMETER_SCALE_XTOL,
    DistributionSpec,
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
from workflows.national.bucking.taper import (
    TaperRegistry,
    default_kozak_parameter_path,
    default_nemora_root,
)

__all__ = ["main"]

PILOT_NAME = "tsa24_assortment_disaggregation"
PILOT_VERSION = "0.4.0"
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
    if candidate.distribution in SPIKE_FAMILIES:
        weight = float(candidate.parameters["spike_weight"])
        width = float(candidate.parameters["spike_bin_width"])
        first_bin_upper = min(lower + width, upper)
        component = SPIKE_FAMILY_COMPONENTS[candidate.distribution]
        continuous = {
            key: float(value)
            for key, value in candidate.parameters.items()
            if key not in {"spike_weight", "spike_bin_width"}
        }
        fitted = np.empty_like(probabilities)
        in_spike = probabilities <= weight
        # Match the sampler: the spike mass is reported as uniform inside the
        # first bin, so the fitted quantiles are comparable to the tallies.
        fitted[in_spike] = lower + (probabilities[in_spike] / max(weight, 1e-12)) * width
        tail = (probabilities[~in_spike] - weight) / max(1.0 - weight, 1e-12)
        fitted[~in_spike] = truncated_quantile(
            component,
            continuous,
            (first_bin_upper, upper),
            tail,
        )
        return np.clip(fitted, lower, upper)
    if candidate.distribution in {"weibull", "gamma"}:
        return truncated_quantile(
            candidate.distribution,
            candidate.parameters,
            (lower, upper),
            probabilities,
        )
    grid = np.linspace(lower, upper, 1024)
    if candidate.distribution in MIXTURE_FAMILY_COMPONENTS:
        names = candidate.component_names or ("gamma",) * len(candidate.component_weights or ())
        components = [
            MixtureComponentFit(name=name, weight=float(weight), parameters=dict(params))
            for name, weight, params in zip(
                names,
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


def _load_baseline_fits(path: Path) -> dict[tuple[str, str, str], dict[str, Any]]:
    """Load the selected candidate from a previous distribution_fits.csv."""
    if not path.exists():
        return {}
    frame = pd.read_csv(path)
    required = {"scope", "target_id", "fit_id", "distribution", "selected", "aic", "ks"}
    if not required.issubset(frame.columns):
        return {}
    selected = frame[
        frame["selected"].astype(str).str.strip().str.lower().isin({"true", "1"})
    ]
    baseline: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in selected.itertuples(index=False):
        key = (str(row.scope), str(row.target_id), str(row.fit_id))
        baseline[key] = {
            "distribution": str(row.distribution),
            "aic": float(row.aic),
            "aicc": float(getattr(row, "aicc", math.nan)),
            "ks": float(row.ks),
        }
    return baseline


def _fit_key(fit: DistributionFit) -> tuple[str, str, str]:
    return (fit.scope, fit.target_id, fit.fit_id)


def _candidate_record(candidate: FitCandidate) -> dict[str, Any]:
    return {
        "distribution": candidate.distribution,
        "fit_status": candidate.fit_status,
        "aic": candidate.aic,
        "aicc": candidate.aicc,
        "ks": candidate.ks,
        "n_parameters": candidate.n_parameters,
        "converged": candidate.converged,
        "iterations": candidate.iterations,
        "n_starts": candidate.n_starts,
        "n_valid_starts": candidate.n_valid_starts,
        "ordering_swaps": candidate.ordering_swaps,
        "method": candidate.method,
        "spike_weight": candidate.spike_weight,
        "spike_component": candidate.spike_component,
        "spike_constrained": candidate.spike_constrained,
        "spike_boundary_status": candidate.spike_boundary_status,
        "spike_at_bound": candidate.spike_at_bound,
        "parameters": {
            key: float(value) for key, value in candidate.parameters.items()
        },
        "error": candidate.error,
    }


def compare_to_previous(
    baseline: Mapping[tuple[str, str, str], Mapping[str, Any]],
    fits: Sequence[DistributionFit],
) -> dict[str, Any]:
    """Compare the new selection against the previous run's selected models."""
    new_families = {"mix_weibull_2", "mix_weibull_gamma", "mix_weibull_3"} | set(
        SPIKE_FAMILIES
    )
    rows: list[dict[str, Any]] = []
    matched = 0
    for fit in fits:
        candidate = fit.selected
        previous = baseline.get(_fit_key(fit))
        new_candidates = [
            item
            for item in fit.candidates
            if item.distribution in new_families
        ]
        successful = [item for item in new_candidates if item.is_success]
        best_new = min(successful, key=lambda item: item.aic, default=None)
        unstable = [
            item.distribution
            for item in new_candidates
            if item.fit_status != "ok" or not item.converged
        ]
        unconstrained_spikes = [
            item
            for item in fit.candidates
            if item.distribution in SPIKE_DIAGNOSTIC_FAMILIES
        ]
        constrained_spikes = [
            item
            for item in fit.candidates
            if item.distribution in SPIKE_CONSTRAINED_FAMILIES
        ]
        best_unconstrained = min(
            (item for item in unconstrained_spikes if item.is_success),
            key=lambda item: item.aic,
            default=None,
        )
        best_constrained = min(
            (item for item in constrained_spikes if item.is_success),
            key=lambda item: item.aic,
            default=None,
        )
        best_constrained_any = min(
            constrained_spikes,
            key=lambda item: item.aic if math.isfinite(item.aic) else math.inf,
            default=None,
        )
        boundary_rejected = [
            item for item in constrained_spikes if item.fit_status == "boundary_reject"
        ]
        row: dict[str, Any] = {
            "scope": fit.scope,
            "fit_id": fit.fit_id,
            "target_id": fit.target_id,
            "species": fit.species,
            "evidence_tier": fit.evidence_tier,
            "n_trees": fit.n_trees,
            "n_plots": fit.n_plots,
            "n_bins": fit.n_bins,
            "dbh_limit_cm": fit.dbh_limit_cm,
            "selected_distribution": candidate.distribution,
            "selected_aic": candidate.aic,
            "selected_aicc": candidate.aicc,
            "selected_ks": candidate.ks,
            "selected_converged": candidate.converged,
            "selected_spike_weight": candidate.spike_weight,
            "selected_spike_component": candidate.spike_component,
            "selected_spike_constrained": candidate.spike_constrained,
            "selected_spike_boundary_status": candidate.spike_boundary_status,
            "best_new_mixture": best_new.distribution if best_new else None,
            "best_new_mixture_aic": best_new.aic if best_new else math.nan,
            "best_new_mixture_ks": best_new.ks if best_new else math.nan,
            "best_new_mixture_converged": best_new.converged if best_new else False,
            "new_mixture_unstable": unstable,
            "new_mixture_candidates": [
                _candidate_record(item) for item in new_candidates
            ],
            "unconstrained_spike_candidates": [
                _candidate_record(item) for item in unconstrained_spikes
            ],
            "constrained_spike_candidates": [
                _candidate_record(item) for item in constrained_spikes
            ],
            "best_unconstrained_spike": (
                _candidate_record(best_unconstrained) if best_unconstrained else None
            ),
            "best_constrained_spike": (
                _candidate_record(best_constrained) if best_constrained else None
            ),
            "best_constrained_spike_any": (
                _candidate_record(best_constrained_any)
                if best_constrained_any
                else None
            ),
            "constrained_spike_boundary_rejects": [
                _candidate_record(item) for item in boundary_rejected
            ],
            "matched_previous": previous is not None,
        }
        if previous is not None:
            matched += 1
            row["previous_distribution"] = previous["distribution"]
            row["previous_aic"] = previous["aic"]
            row["previous_aicc"] = previous["aicc"]
            row["previous_ks"] = previous["ks"]
            row["delta_aic"] = candidate.aic - previous["aic"]
            row["delta_aicc"] = candidate.aicc - previous["aicc"]
            row["delta_ks"] = candidate.ks - previous["ks"]
            row["switched"] = candidate.distribution != previous["distribution"]
            row["switched_to_new_mixture"] = (
                candidate.distribution in new_families
                and previous["distribution"] not in new_families
            )
            row["switched_to_spike"] = (
                candidate.distribution in SPIKE_FAMILIES
                and previous["distribution"] not in SPIKE_FAMILIES
            )
            row["switched_to_constrained_spike"] = (
                candidate.distribution in SPIKE_CONSTRAINED_FAMILIES
                and previous["distribution"] not in SPIKE_CONSTRAINED_FAMILIES
            )
            row["switched_to_unconstrained_spike"] = (
                candidate.distribution in SPIKE_DIAGNOSTIC_FAMILIES
                and previous["distribution"] not in SPIKE_DIAGNOSTIC_FAMILIES
            )
            row["constrained_spike_boundary_reject"] = bool(boundary_rejected)
            row["switched_to_finite_mixture"] = (
                candidate.distribution in MIXTURE_FAMILY_COMPONENTS
                and previous["distribution"] not in MIXTURE_FAMILY_COMPONENTS
            )
            row["mixture_worse"] = (
                best_new is not None and best_new.aic > previous["aic"] + 2.0
            )
            row["mixture_improves_previous"] = (
                best_new is not None and best_new.aic < previous["aic"] - 2.0
            )
        else:
            row.update(
                {
                    "previous_distribution": None,
                    "previous_aic": math.nan,
                    "previous_aicc": math.nan,
                    "previous_ks": math.nan,
                    "delta_aic": math.nan,
                    "delta_aicc": math.nan,
                    "delta_ks": math.nan,
                    "switched": False,
                    "switched_to_new_mixture": False,
                    "switched_to_spike": False,
                    "switched_to_finite_mixture": False,
                    "switched_to_constrained_spike": False,
                    "switched_to_unconstrained_spike": False,
                    "constrained_spike_boundary_reject": bool(boundary_rejected),
                    "mixture_worse": False,
                    "mixture_improves_previous": False,
                }
            )
        rows.append(row)

    matched_rows = [row for row in rows if row["matched_previous"]]
    switched = [row for row in matched_rows if row["switched"]]
    switched_new = [row for row in matched_rows if row["switched_to_new_mixture"]]
    switched_spike = [row for row in matched_rows if row["switched_to_spike"]]
    switched_constrained = [
        row for row in matched_rows if row["switched_to_constrained_spike"]
    ]
    switched_unconstrained = [
        row for row in matched_rows if row["switched_to_unconstrained_spike"]
    ]
    switched_finite = [
        row for row in matched_rows if row["switched_to_finite_mixture"]
    ]
    selected_spike = [
        row for row in matched_rows if row["selected_distribution"] in SPIKE_FAMILIES
    ]
    selected_constrained_spike = [
        row
        for row in matched_rows
        if row["selected_distribution"] in SPIKE_CONSTRAINED_FAMILIES
    ]
    boundary_reject_rows = [
        row
        for row in rows
        if row.get("constrained_spike_boundary_rejects")
    ]
    improved = [
        row
        for row in matched_rows
        if math.isfinite(row["delta_aic"]) and row["delta_aic"] < -2.0
    ]
    worse = [
        row
        for row in matched_rows
        if math.isfinite(row["delta_aic"]) and row["delta_aic"] > 2.0
    ]
    high_ks_before = [
        row
        for row in matched_rows
        if math.isfinite(row["previous_ks"]) and row["previous_ks"] > 0.20
    ]
    high_ks_after = [
        row
        for row in matched_rows
        if math.isfinite(row["selected_ks"]) and row["selected_ks"] > 0.20
    ]
    return {
        "status": "compared" if matched_rows else "no_baseline",
        "n_fits": len(rows),
        "n_matched": matched,
        "counts": {
            "switched": len(switched),
            "switched_to_new_mixture": len(switched_new),
            "switched_to_spike": len(switched_spike),
            "switched_to_constrained_spike": len(switched_constrained),
            "switched_to_unconstrained_spike": len(switched_unconstrained),
            "switched_to_finite_mixture": len(switched_finite),
            "selected_spike": len(selected_spike),
            "selected_constrained_spike": len(selected_constrained_spike),
            "selected_spike_unique": len(
                {(row["scope"], row["fit_id"]) for row in selected_spike}
            ),
            "constrained_spike_boundary_rejects": len(boundary_reject_rows),
            "improved_by_more_than_2_aic": len(improved),
            "worse_by_more_than_2_aic": len(worse),
            "mixture_worse_than_previous": sum(
                1 for row in matched_rows if row["mixture_worse"]
            ),
            "mixture_improves_previous": sum(
                1 for row in matched_rows if row["mixture_improves_previous"]
            ),
            "mixture_unstable_fits": sum(
                1 for row in matched_rows if row["new_mixture_unstable"]
            ),
            "high_ks_before": len(high_ks_before),
            "high_ks_after": len(high_ks_after),
        },
        "selected_distributions_before": {
            name: sum(
                1 for row in matched_rows if row["previous_distribution"] == name
            )
            for name in sorted(
                {row["previous_distribution"] for row in matched_rows}
            )
        },
        "selected_distributions_after": {
            name: sum(
                1 for row in matched_rows if row["selected_distribution"] == name
            )
            for name in sorted({row["selected_distribution"] for row in matched_rows})
        },
        "high_ks_before": [
            {"scope": row["scope"], "fit_id": row["fit_id"], "ks": row["previous_ks"]}
            for row in high_ks_before
        ],
        "high_ks_after": [
            {
                "scope": row["scope"],
                "fit_id": row["fit_id"],
                "ks": row["selected_ks"],
                "distribution": row["selected_distribution"],
            }
            for row in high_ks_after
        ],
        "spike_selected": [
            {
                "scope": row["scope"],
                "fit_id": row["fit_id"],
                "species": row["species"],
                "previous_distribution": row.get("previous_distribution"),
                "selected_distribution": row["selected_distribution"],
                "spike_weight": row["selected_spike_weight"],
                "spike_component": row["selected_spike_component"],
                "spike_constrained": row["selected_spike_constrained"],
                "spike_boundary_status": row["selected_spike_boundary_status"],
                "previous_aic": row.get("previous_aic"),
                "selected_aic": row["selected_aic"],
                "delta_aic": row.get("delta_aic"),
                "previous_ks": row.get("previous_ks"),
                "selected_ks": row["selected_ks"],
                "delta_ks": row.get("delta_ks"),
            }
            for row in selected_spike
        ],
        "constrained_spike_boundary_rejects": [
            {
                "scope": row["scope"],
                "fit_id": row["fit_id"],
                "species": row["species"],
                "candidates": row["constrained_spike_boundary_rejects"],
                "selected_distribution": row["selected_distribution"],
                "selected_aic": row["selected_aic"],
                "selected_ks": row["selected_ks"],
            }
            for row in boundary_reject_rows
        ],
        "spike_comparison": [
            {
                "scope": row["scope"],
                "fit_id": row["fit_id"],
                "species": row["species"],
                "unconstrained": row.get("best_unconstrained_spike"),
                "constrained": row.get("best_constrained_spike_any"),
                "boundary_rejects": row["constrained_spike_boundary_rejects"],
                "selected_distribution": row["selected_distribution"],
                "selected_aic": row["selected_aic"],
                "selected_ks": row["selected_ks"],
            }
            for row in rows
            if row.get("best_unconstrained_spike")
            or row.get("best_constrained_spike")
            or row.get("constrained_spike_boundary_rejects")
        ],
        "rows": rows,
    }


def summarise_mixture_candidates(fits: Sequence[DistributionFit]) -> dict[str, Any]:
    """Summarise convergence, selection, and instability for mixture families."""
    families = (
        "mixture",
        "mix_weibull_2",
        "mix_weibull_gamma",
        "mix_weibull_3",
        "spike_weibull",
        "spike_gamma",
        "spike_weibull_constrained",
        "spike_gamma_constrained",
    )
    counts: dict[str, dict[str, Any]] = {
        family: {
            "attempted": 0,
            "ok": 0,
            "converged": 0,
            "failed": 0,
            "boundary_reject": 0,
            "selected": 0,
            "aic_best": 0,
        }
        for family in families
    }
    unstable: list[dict[str, Any]] = []
    boundary_rejected: list[dict[str, Any]] = []
    degenerate: list[dict[str, Any]] = []
    for fit in fits:
        selected = fit.selected
        for candidate in fit.candidates:
            if candidate.distribution not in counts:
                continue
            record = counts[candidate.distribution]
            record["attempted"] += 1
            if candidate.fit_status == "ok":
                record["ok"] += 1
            elif candidate.fit_status == "boundary_reject":
                record["boundary_reject"] += 1
                boundary_rejected.append(
                    {
                        "scope": fit.scope,
                        "fit_id": fit.fit_id,
                        "species": fit.species,
                        "distribution": candidate.distribution,
                        "spike_weight": candidate.spike_weight,
                        "spike_component": candidate.spike_component,
                        "parameters": {
                            key: float(value)
                            for key, value in candidate.parameters.items()
                        },
                        "at_bound": candidate.spike_at_bound,
                        "aic": candidate.aic,
                        "ks": candidate.ks,
                        "converged": candidate.converged,
                        "error": candidate.error,
                    }
                )
            else:
                record["failed"] += 1
            if candidate.converged:
                record["converged"] += 1
            if candidate is selected:
                record["selected"] += 1
            if candidate is fit.best_aic:
                record["aic_best"] += 1
            if (
                candidate.fit_status not in {"ok", "boundary_reject"}
                or not candidate.converged
            ):
                unstable.append(
                    {
                        "scope": fit.scope,
                        "fit_id": fit.fit_id,
                        "species": fit.species,
                        "distribution": candidate.distribution,
                        "fit_status": candidate.fit_status,
                        "converged": candidate.converged,
                        "iterations": candidate.iterations,
                        "error": candidate.error,
                    }
                )
            if candidate.error and "degenerate" in candidate.error.lower():
                degenerate.append(
                    {
                        "fit_id": fit.fit_id,
                        "distribution": candidate.distribution,
                        "error": candidate.error,
                    }
                )
    selected_families = {
        family: sum(
            1 for fit in fits if fit.selected.distribution == family
        )
        for family in families
    }
    spike_selected = [
        {
            "scope": fit.scope,
            "fit_id": fit.fit_id,
            "species": fit.species,
            "distribution": fit.selected.distribution,
            "spike_weight": fit.selected.spike_weight,
            "spike_component": fit.selected.spike_component,
            "spike_constrained": fit.selected.spike_constrained,
            "spike_boundary_status": fit.selected.spike_boundary_status,
            "aic": fit.selected.aic,
            "ks": fit.selected.ks,
            "converged": fit.selected.converged,
        }
        for fit in fits
        if fit.selected.distribution in SPIKE_FAMILIES
    ]
    spike_comparison: list[dict[str, Any]] = []
    for fit in fits:
        unconstrained = [
            candidate
            for candidate in fit.candidates
            if candidate.distribution in SPIKE_DIAGNOSTIC_FAMILIES
        ]
        constrained = [
            candidate
            for candidate in fit.candidates
            if candidate.distribution in SPIKE_CONSTRAINED_FAMILIES
        ]
        if not unconstrained and not constrained:
            continue
        best_unconstrained = min(
            (candidate for candidate in unconstrained if candidate.is_success),
            key=lambda candidate: candidate.aic,
            default=None,
        )
        best_constrained = min(
            (candidate for candidate in constrained if candidate.is_success),
            key=lambda candidate: candidate.aic,
            default=None,
        )
        best_constrained_any = min(
            constrained,
            key=lambda candidate: candidate.aic
            if math.isfinite(candidate.aic)
            else math.inf,
            default=None,
        )
        rejected = [
            candidate
            for candidate in constrained
            if candidate.fit_status == "boundary_reject"
        ]
        weight_candidates = unconstrained + constrained
        first_bin_share = next(
            (
                float(candidate.spike_weight)
                for candidate in weight_candidates
                if math.isfinite(float(candidate.spike_weight))
            ),
            math.nan,
        )
        spike_comparison.append(
            {
                "scope": fit.scope,
                "fit_id": fit.fit_id,
                "species": fit.species,
                "first_bin_share": first_bin_share,
                "unconstrained": (
                    _candidate_record(best_unconstrained)
                    if best_unconstrained
                    else None
                ),
                "constrained": (
                    _candidate_record(best_constrained_any)
                    if best_constrained_any
                    else None
                ),
                "boundary_rejects": [
                    _candidate_record(candidate) for candidate in rejected
                ],
                "selected_distribution": fit.selected.distribution,
                "selected_aic": fit.selected.aic,
                "selected_ks": fit.selected.ks,
            }
        )
    return {
        "families": list(families),
        "counts": counts,
        "selected_counts": selected_families,
        "n_unstable": len(unstable),
        "n_degenerate_rejections": len(degenerate),
        "n_boundary_rejections": len(boundary_rejected),
        "unstable": unstable[:50],
        "degenerate_rejections": degenerate[:50],
        "boundary_rejections": boundary_rejected[:50],
        "spike": {
            "families": sorted(SPIKE_FAMILIES),
            "constrained_families": sorted(SPIKE_CONSTRAINED_FAMILIES),
            "diagnostic_families": sorted(SPIKE_DIAGNOSTIC_FAMILIES),
            "constrained_shape_lower": SPIKE_CONSTRAINED_SHAPE_LOWER,
            "constrained_scale_lower": SPIKE_CONSTRAINED_SCALE_LOWER,
            "constrained_bounds": {
                family: {
                    key: [float(low), float(high)]
                    for key, (low, high) in spike_tail_bounds(
                        SPIKE_FAMILY_COMPONENTS[family]
                    ).items()
                }
                for family in sorted(SPIKE_CONSTRAINED_FAMILIES)
            },
            "first_bin_share_trigger": SPIKE_FIRST_BIN_SHARE_TRIGGER,
            "ks_trigger": SPIKE_KS_TRIGGER,
            "attempted": sum(
                counts[family]["attempted"] for family in SPIKE_FAMILIES
            ),
            "attempted_constrained": sum(
                counts[family]["attempted"] for family in SPIKE_CONSTRAINED_FAMILIES
            ),
            "boundary_rejected": sum(
                counts[family]["boundary_reject"]
                for family in SPIKE_CONSTRAINED_FAMILIES
            ),
            "selected": len(spike_selected),
            "selected_constrained": sum(
                1
                for row in spike_selected
                if row["distribution"] in SPIKE_CONSTRAINED_FAMILIES
            ),
            "selected_unique": len(
                {(row["scope"], row["fit_id"]) for row in spike_selected}
            ),
            "selected_rows": spike_selected,
            "comparison_rows": spike_comparison,
            "model": (
                "two-part spike at dbh_limit plus one continuous component "
                "left-truncated at the first bin upper edge; spike weight is "
                "the first-bin share and counts as one AIC parameter"
            ),
        },
        "convergence_rule": (
            "A mixture or constrained spike model is synthesis-eligible only "
            "when it converged and, for a constrained spike, no tail parameter "
            "sits on a declared bound. Unconstrained spike families remain in "
            "the candidate table for audit but are never selected."
        ),
    }


def _comparison_frame(comparison: Mapping[str, Any]) -> pd.DataFrame:
    """Flatten the comparison rows into a CSV-safe table."""
    rows: list[dict[str, Any]] = []
    for row in comparison.get("rows", []):
        flat = {
            key: value
            for key, value in row.items()
            if not isinstance(value, (list, dict))
        }
        flat["new_mixture_unstable"] = ",".join(row.get("new_mixture_unstable", []))
        for prefix, key in (
            ("unconstrained_spike", "best_unconstrained_spike"),
            ("constrained_spike", "best_constrained_spike"),
        ):
            candidate = row.get(key) or {}
            flat[f"{prefix}_distribution"] = candidate.get("distribution")
            flat[f"{prefix}_aic"] = candidate.get("aic", math.nan)
            flat[f"{prefix}_ks"] = candidate.get("ks", math.nan)
            flat[f"{prefix}_boundary_status"] = candidate.get(
                "spike_boundary_status"
            )
            flat[f"{prefix}_at_bound"] = candidate.get("spike_at_bound")
            flat[f"{prefix}_parameters"] = json.dumps(
                candidate.get("parameters", {}), sort_keys=True, default=str
            )
        flat["constrained_spike_boundary_rejects"] = json.dumps(
            row.get("constrained_spike_boundary_rejects", []),
            sort_keys=True,
            default=str,
        )
        flat["new_mixture_candidate_summary"] = json.dumps(
            [
                {
                    "distribution": item["distribution"],
                    "aic": item["aic"],
                    "ks": item["ks"],
                    "converged": item["converged"],
                    "fit_status": item["fit_status"],
                }
                for item in row.get("new_mixture_candidates", [])
            ],
            sort_keys=True,
            default=str,
        )
        rows.append(flat)
    return pd.DataFrame(rows)


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


def _spike_parameter_text(parameters: Mapping[str, Any] | None) -> str:
    """Format a spike row's weight, shape, and scale for the report."""
    if not parameters:
        return "n/a"
    parts: list[str] = []
    if "spike_weight" in parameters:
        parts.append(f"w={_fmt(parameters['spike_weight'], 4)}")
    for key in ("a", "p", "beta"):
        if key in parameters:
            parts.append(f"{key}={_fmt(parameters[key], 4)}")
    return ", ".join(parts) if parts else "n/a"


def _spike_candidate_text(candidate: Mapping[str, Any] | None) -> str:
    """Format one audit candidate for the CW before/after table."""
    if not candidate:
        return "n/a"
    status = str(candidate.get("spike_boundary_status") or "ok")
    at_bound = str(candidate.get("spike_at_bound") or "")
    if status != "ok":
        status = f"{status} ({at_bound or 'bound'})"
    return (
        f"{candidate.get('distribution')} · "
        f"{_spike_parameter_text(candidate.get('parameters'))} · "
        f"AIC {_fmt(candidate.get('aic'), 1)} · "
        f"KS {_fmt(candidate.get('ks'), 4)} · {status}"
    )


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
    comparison = summary["dbh_fits"].get("comparison_to_previous", {})
    mixture_summary = summary["dbh_fits"].get("mixture_candidates", {})
    comparison_rows = []
    for row in comparison.get("rows", []):
        interesting = (
            row.get("switched_to_new_mixture")
            or row.get("switched_to_spike")
            or row.get("mixture_worse")
            or (math.isfinite(row.get("previous_ks", math.nan)) and row["previous_ks"] > 0.20)
            or (math.isfinite(row.get("selected_ks", math.nan)) and row["selected_ks"] > 0.20)
        )
        if not interesting:
            continue
        comparison_rows.append(
            "| {fit} | {previous} | {selected} | {delta} | {delta_aicc} | {prev_ks} | {ks} | "
            "{best_new} | {best_delta} |".format(
                fit=row["fit_id"],
                previous=row.get("previous_distribution") or "n/a",
                selected=row["selected_distribution"],
                delta=_fmt(row.get("delta_aic"), 1),
                delta_aicc=_fmt(row.get("delta_aicc"), 1),
                prev_ks=_fmt(row.get("previous_ks"), 3),
                ks=_fmt(row.get("selected_ks"), 3),
                best_new=row.get("best_new_mixture") or "n/a",
                best_delta=_fmt(
                    (
                        row["best_new_mixture_aic"] - row["previous_aic"]
                        if math.isfinite(row.get("previous_aic", math.nan))
                        and math.isfinite(row.get("best_new_mixture_aic", math.nan))
                        else math.nan
                    ),
                    1,
                ),
            )
        )
    mixture_rows = [
        "| {family} | {attempted} | {ok} | {converged} | {selected} |".format(
            family=family,
            attempted=values["attempted"],
            ok=values["ok"],
            converged=values["converged"],
            selected=values["selected"],
        )
        for family, values in mixture_summary.get("counts", {}).items()
    ]
    spike_summary = mixture_summary.get("spike", {})
    spike_selected_rows = comparison.get("spike_selected", [])
    unique_spike_rows: list[dict[str, Any]] = []
    seen_spike_keys: set[tuple[str, str]] = set()
    for row in spike_selected_rows:
        key = (str(row.get("scope")), str(row.get("fit_id")))
        if key in seen_spike_keys:
            continue
        seen_spike_keys.add(key)
        unique_spike_rows.append(row)
    cw_spike_rows = [
        row for row in unique_spike_rows if "|CW" in str(row.get("fit_id", ""))
    ]
    spike_comparison_rows = spike_summary.get("comparison_rows", [])
    cw_spike_comparison = []
    seen_cw_keys: set[tuple[str, str]] = set()
    for row in spike_comparison_rows:
        if "|CW" not in str(row.get("fit_id", "")):
            continue
        key = (str(row.get("scope")), str(row.get("fit_id")))
        if key in seen_cw_keys:
            continue
        seen_cw_keys.add(key)
        cw_spike_comparison.append(row)
    boundary_rejections = mixture_summary.get("boundary_rejections", [])
    boundary_reject_rows = [
        "| {fit} | {family} | {weight} | {params} | {aic} | {ks} | {at_bound} |".format(
            fit=row["fit_id"],
            family=row["distribution"],
            weight=_fmt(row.get("spike_weight"), 4),
            params=_spike_parameter_text(row.get("parameters", {})),
            aic=_fmt(row.get("aic"), 1),
            ks=_fmt(row.get("ks"), 4),
            at_bound=row.get("at_bound") or "n/a",
        )
        for row in boundary_rejections
    ]
    cw_comparison_rows = [
        "| {fit} | {share} | {unconstrained} | {constrained} | {selected} |".format(
            fit=row["fit_id"],
            share=_fmt(row.get("first_bin_share"), 4),
            unconstrained=_spike_candidate_text(row.get("unconstrained")),
            constrained=_spike_candidate_text(row.get("constrained")),
            selected=(
                f"{row.get('selected_distribution')} · "
                f"AIC {_fmt(row.get('selected_aic'), 1)} · "
                f"KS {_fmt(row.get('selected_ks'), 4)}"
            ),
        )
        for row in cw_spike_comparison
    ]
    spike_regressions = [
        row
        for row in unique_spike_rows
        if math.isfinite(row.get("delta_aic", math.nan)) and row["delta_aic"] > 2.0
    ]
    spike_rows = [
        "| {fit} | {previous} | {selected} | {weight} | {component} | {previous_aic} | "
        "{selected_aic} | {delta_aic} | {previous_ks} | {selected_ks} |".format(
            fit=row.get("fit_id"),
            previous=row.get("previous_distribution") or "n/a",
            selected=row.get("selected_distribution"),
            weight=_fmt(row.get("spike_weight"), 4),
            component=row.get("spike_component") or "n/a",
            previous_aic=_fmt(row.get("previous_aic"), 1),
            selected_aic=_fmt(row.get("selected_aic"), 1),
            delta_aic=_fmt(row.get("delta_aic"), 1),
            previous_ks=_fmt(row.get("previous_ks"), 3),
            selected_ks=_fmt(row.get("selected_ks"), 3),
        )
        for row in unique_spike_rows
    ]
    unstable = mixture_summary.get("unstable", [])
    unstable_rows = [
        "| {fit} | {distribution} | {status} | {converged} | {iterations} |".format(
            fit=row["fit_id"],
            distribution=row["distribution"],
            status=row["fit_status"],
            converged=row["converged"],
            iterations=row["iterations"],
        )
        for row in unstable[:12]
    ]
    comparison_counts = comparison.get("counts", {})
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
        "- TPH and volume scale come from the BTC managed-AU target, not from the",
        "  distribution. Expansion factors are fixed at the target TPH and a",
        "  positive diameter multiplier is solved for the target taper total.",
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
        f"target TPH: **{target.tph_total:,.1f}**.",
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
        "### 4b. Mixture candidates and previous-run comparison",
        "",
        f"- Switches: **{comparison_counts.get('switched', 0)}** of "
        f"**{comparison.get('n_matched', 0)}** matched strata "
        f"(spike **{comparison_counts.get('switched_to_spike', 0)}**; "
        f"new finite mixtures **{comparison_counts.get('switched_to_finite_mixture', 0)}**).",
        f"- Improved by >2 AIC: **{comparison_counts.get('improved_by_more_than_2_aic', 0)}**; "
        f"worse by >2 AIC: **{comparison_counts.get('worse_by_more_than_2_aic', 0)}**; "
        f"mixtures worse than the previous selected model: "
        f"**{comparison_counts.get('mixture_worse_than_previous', 0)}**.",
        f"- Unstable mixture fits reported: "
        f"**{mixture_summary.get('n_unstable', 0)}**; new-family unstable fits are "
        f"not selected; degenerate rejections: "
        f"**{mixture_summary.get('n_degenerate_rejections', 0)}**.",
        f"- High-KS (>0.20) selected strata before: "
        f"**{comparison_counts.get('high_ks_before', 0)}**, after: "
        f"**{comparison_counts.get('high_ks_after', 0)}**.",
        "",
        "| family | attempted | ok | converged | selected |",
        "|---|---:|---:|---:|---:|",
        *mixture_rows,
        "",
        "| fit | previous | selected | delta AIC | delta AICc | previous KS | selected KS | best new flexible | best new delta AIC |",
        "|---|---|---|---:|---:|---:|---:|---|---:|",
        *comparison_rows,
        "",
        "The new families are fitted by a left-truncated EM that delegates each "
        "component M-step to `fit_censored_inventory` (Weibull) or the documented "
        "gamma moment update. Components are sorted by scale after every update. "
        "New Weibull mixtures are synthesis-eligible only when they converged. "
        "The legacy two-gamma mixture remains available as a fallback and carries "
        "a ten-unit AIC penalty when it did not converge; non-converged legacy "
        "selections are flagged in `distribution_fits.csv`. Small AIC gains are "
        "resolved in favour of the simpler model.",
        "",
        "### 4c. Spike (first-bin) mixtures",
        "",
        f"- Spike families are attempted only when the first 2 cm bin carries more "
        f"than **{100 * float(spike_summary.get('first_bin_share_trigger', 0.0)):.0f}%** "
        f"of the expansion-factor tally, or the best single-component KS exceeds "
        f"**{_fmt(spike_summary.get('ks_trigger'), 2)}**. Attempted fits: "
        f"**{spike_summary.get('attempted', 0)}** across four families "
        f"(**{spike_summary.get('attempted_constrained', 0)}** constrained); "
        f"constrained boundary rejects: "
        f"**{spike_summary.get('boundary_rejected', 0)}**; selected fits: "
        f"**{spike_summary.get('selected', 0)}** "
        f"(**{comparison_counts.get('selected_spike_unique', 0)}** unique "
        f"`scope`/`fit_id` pairs).",
        "- The spike is a point mass at the plot DBH limit that carries the first "
        "bin; the continuous Weibull/gamma tail is left-truncated at the first bin "
        "upper edge. The spike weight is the first-bin share (the disjoint-support "
        "multinomial MLE), counts as one AIC parameter, and is sampled uniformly "
        "inside the first bin so synthesis keeps a physical diameter. The spike "
        "never sets the volume scale.",
        "- Constrained `spike_weibull_constrained`/`spike_gamma_constrained` tails "
        "are fitted by a bounded grouped MLE on the bins above the first bin, with "
        f"shape >= {SPIKE_CONSTRAINED_SHAPE_LOWER:g} and scale >= "
        f"{SPIKE_CONSTRAINED_SCALE_LOWER:g} cm. A constrained fit whose tail "
        "parameters sit on any declared bound is reported as `boundary_reject` in "
        "`distribution_fits.csv` and is never selected.",
        "- Unconstrained `spike_weibull`/`spike_gamma` rows remain as audit "
        "diagnostics only. Their tails can reach the two-stage scale floor with "
        "a Weibull shape below one, so the *untruncated* mean can be infinite; "
        "they are not synthesis-eligible even though the sampler would clip "
        "draws to the fitted support.",
        "",
    ]
    if cw_spike_rows:
        lines.extend(
            [
                "- CW high-KS strata: "
                + "; ".join(
                    f"`{row['fit_id']}` → `{row['selected_distribution']}` "
                    f"(spike {_fmt(row['spike_weight'], 3)}, KS "
                    f"{_fmt(row['previous_ks'], 3)} → {_fmt(row['selected_ks'], 3)}, "
                    f"ΔAIC {_fmt(row['delta_aic'], 1)})"
                    for row in cw_spike_rows
                )
                + ".",
                "",
            ]
        )
    if spike_regressions:
        lines.extend(
            [
                "- Spike selections whose raw AIC is more than two units worse than "
                "the previous model: "
                + "; ".join(
                    f"`{row['fit_id']}` (ΔAIC {_fmt(row['delta_aic'], 1)}, KS "
                    f"{_fmt(row['previous_ks'], 3)} → {_fmt(row['selected_ks'], 3)})"
                    for row in spike_regressions
                )
                + ". These reflect the documented ten-unit penalty on a "
                "non-converged legacy two-gamma fallback and are kept for audit; "
                "the KS improved in each case.",
                "",
            ]
        )
    if cw_comparison_rows:
        lines.extend(
            [
                "#### CW before/after (unconstrained vs constrained tail)",
                "",
                "| fit | first-bin share | unconstrained spike | constrained spike | selected |",
                "|---|---:|---|---|---|",
                *cw_comparison_rows,
                "",
            ]
        )
    if boundary_reject_rows:
        lines.extend(
            [
                "#### Constrained boundary rejections",
                "",
                "| fit | family | spike weight | tail parameters | AIC | KS | at bound |",
                "|---|---|---:|---|---:|---:|---|",
                *boundary_reject_rows,
                "",
            ]
        )
    if spike_rows:
        lines.extend(
            [
                "| fit | previous | selected | spike weight | tail component | previous AIC | selected AIC | delta AIC | previous KS | selected KS |",
                "|---|---|---|---:|---|---:|---:|---:|---:|---:|",
                *spike_rows,
                "",
            ]
        )
    if unstable_rows:
        lines.extend(
            [
                "| unstable fit | family | status | converged | iterations |",
                "|---|---|---|---:|---:|",
                *unstable_rows,
                "",
            ]
        )
    lines.extend(
        [
        "## 5. Height models",
        "",
        "| species | tier | level | n | a | b | RMSE m | bias m |",
        "|---|---|---|---:|---:|---:|---:|---:|",
        *height_rows,
        "",
        "## 6. Synthetic tree list",
        "",
        f"- Constraint mode: **{stand.constraint_mode}**; trees: "
        f"**{len(stand.trees):,}**.",
        f"- TPH: achieved **{stand.tph:,.1f}** vs target "
        f"**{float(target.target_tph) if target.target_tph is not None else math.nan:,.1f}**.",
        f"- Taper total: achieved **{stand.achieved_taper_volume_m3:,.3f} m3** vs "
        f"target **{stand.target_volume_m3:,.3f} m3**; expansion-factor scale "
        f"**{stand.scale_factor:.6f}**; diameter scale "
        f"**{stand.diameter_scale_factor:.9f}**.",
        f"- Root search: bracket "
        f"**{stand.diameter_scale_bracket if stand.diameter_scale_bracket is not None else 'n/a'}**; "
        f"iterations **{stand.root_find_iterations}**; residual "
        f"**{stand.diameter_scale_residual_m3:.3e} m3**.",
        f"- Basal area: **{stand.basal_area_m2ha:,.2f} m2/ha**; QMD: "
        f"**{stand.qmd_cm:,.2f} cm**.",
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
        "- `distribution_fits.csv`, `dbh_mixture_comparison.csv`, `height_models.csv`, `height_models_general.csv`",
        "- `synthetic_trees.csv`, `species_composition.csv`, `target_stand.json`",
        "- `assortment_matrix.csv`, `bucked_logs.csv`, `residue_ledger.csv`",
        "- `dbh_validation.csv`, `btc_grade_proxy.csv`, `validation_summary.json`, `manifest.json`",
        "",
    ]
    )
    return "\n".join(lines) + "\n"


def run(args: argparse.Namespace) -> dict[str, Any]:
    started = time.time()
    repo_root = Path(args.repo_root).resolve()
    data_root = Path(args.data_root).resolve()
    out_dir = Path(args.out_dir).resolve()
    assert_no_unexpected_deletions(repo_root)
    baseline_path = (
        Path(args.baseline_fits).resolve()
        if getattr(args, "baseline_fits", None)
        else out_dir / "distribution_fits.csv"
    )
    baseline = _load_baseline_fits(baseline_path)

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

    all_dbh_fits = list(general_fits) + list(au_fits)
    previous_comparison = compare_to_previous(baseline, all_dbh_fits)
    mixture_candidates = summarise_mixture_candidates(all_dbh_fits)

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
                component_names=candidate.component_names,
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
        target_tph=tph_total,
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
        "target_tph": tph_total,
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
                f"{stand.target_volume_m3:.6f} m3; expansion scale "
                f"{stand.scale_factor:.9f}; diameter scale "
                f"{stand.diameter_scale_factor:.9f}"
            ),
        },
        "target_tph": {
            "pass": (
                target.target_tph is None
                or abs(stand.tph - float(target.target_tph))
                <= max(1e-6, 1e-9 * abs(float(target.target_tph)))
            ),
            "detail": (
                f"constraint {stand.constraint_mode}; achieved TPH "
                f"{stand.tph:.6f} vs target "
                f"{float(target.target_tph) if target.target_tph is not None else math.nan:.6f}"
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
        "selected_mixtures_converged": {
            "pass": all(
                item.selected.converged
                for item in all_dbh_fits
                if item.selected.distribution.startswith(("mix_weibull", "spike_"))
            ),
            "detail": (
                "every selected Weibull mixture and spike model converged; a "
                "non-converged legacy two-gamma fallback may remain selected "
                "and is flagged in distribution_fits.csv"
            ),
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
            "mixture_candidates": mixture_candidates,
            "comparison_to_previous": previous_comparison,
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
                "a two-component gamma mixture, the mix_weibull_2 / "
                "mix_weibull_gamma / mix_weibull_3 families, and the spike_weibull "
                "/ spike_gamma two-part models. Mixtures are fitted on the "
                "left-truncated grouped support and selected by common AIC with a "
                "two-unit parsimony margin. Common AIC/KS are computed on one "
                "support-conditioned binned likelihood. Spike models carry the "
                "first bin as a point mass and fit the tail from the first bin "
                "upper edge; the spike weight is one AIC parameter."
            ),
            "dbh_limit": (
                "Plots with the modal DBH limit are used for the shape fit; other "
                "limit classes are excluded and reported."
            ),
            "volume_target": (
                "BTC managed_au_curves volume is treated as the taper-total target "
                "for one hectare. Recovered log volume is lower by residue."
            ),
            "tph_target": (
                "BTC managed_au_curves TPH is treated as a fixed stand density. "
                "Expansion factors sum to the target TPH and a positive diameter "
                "multiplier is solved so the taper total still equals the target "
                "volume; height is recomputed from the scaled DBH."
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
        _comparison_frame(previous_comparison),
        out_dir / "dbh_mixture_comparison.csv",
    )
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
        out_dir / "dbh_mixture_comparison.csv",
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
                "mixtures.py",
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
            "synthesis_scaling": {
                "mode": "volume_and_tph",
                "target_tph_source": "BTC managed_au_curves.csv",
                "target_volume_source": "BTC managed_au_curves.csv",
                "expansion_factors": "target_tph * relative_weight / sum(relative_weight)",
                "height_recomputed_from_scaled_dbh": True,
                "root_finder": "scipy.optimize.brentq",
                "diameter_scale_bracket": list(DIAMETER_SCALE_BRACKET),
                "diameter_scale_expanded_bracket": list(
                    DIAMETER_SCALE_EXPANDED_BRACKET
                ),
                "root_xtol": DIAMETER_SCALE_XTOL,
                "root_rtol": DIAMETER_SCALE_RTOL,
                "failure_rule": "expand bracket once, then fail loudly",
            },
            "baseline_fits": (
                str(baseline_path)
                if repo_root in baseline_path.parents
                else "previous_run_distribution_fits.csv"
            ),
            "min_trees": int(args.min_trees),
            "min_plots": int(args.min_plots),
            "min_height_n": int(args.min_height_n),
            "min_species_share": float(args.min_species_share),
            "bin_width_cm": float(args.bin_width_cm),
            "mixture": {
                "families": [
                    "mixture",
                    "mix_weibull_2",
                    "mix_weibull_gamma",
                    "mix_weibull_3",
                ],
                "new_family_starts": 2,
                "three_component_starts": 1,
                "three_component_ks_trigger": 0.15,
                "max_iter": 250,
                "tol": 1e-6,
                "min_component_weight": 1e-4,
                "selection_aic_margin": 2.0,
                "gamma_fallback": "censored",
            },
            "spike": {
                "families": sorted(SPIKE_FAMILIES),
                "constrained_families": sorted(SPIKE_CONSTRAINED_FAMILIES),
                "diagnostic_families": sorted(SPIKE_DIAGNOSTIC_FAMILIES),
                "first_bin_share_trigger": SPIKE_FIRST_BIN_SHARE_TRIGGER,
                "ks_trigger": SPIKE_KS_TRIGGER,
                "min_spike_weight": 0.001,
                "max_spike_weight": 0.95,
                "spike_location": "dbh_limit",
                "tail_support": "first_bin_upper",
                "sampling": "uniform_in_first_bin",
                "selection_aic_margin": 2.0,
                "constrained_estimator": "grouped_mle_lbfgsb",
                "constrained_shape_lower": SPIKE_CONSTRAINED_SHAPE_LOWER,
                "constrained_scale_lower": SPIKE_CONSTRAINED_SCALE_LOWER,
                "constrained_bounds": {
                    family: {
                        key: [float(low), float(high)]
                        for key, (low, high) in spike_tail_bounds(
                            SPIKE_FAMILY_COMPONENTS[family]
                        ).items()
                    }
                    for family in sorted(SPIKE_CONSTRAINED_FAMILIES)
                },
                "boundary_rule": "reject and never select a tail at any bound",
                "unconstrained_rule": (
                    "unconstrained spike tails are audit-only diagnostics and "
                    "are never synthesis-eligible because their untruncated "
                    "mean can be infinite"
                ),
            },
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
            (
                "Expansion factors are fixed to the BTC target TPH and a positive "
                "diameter multiplier is solved by a bracketed root search so the "
                "taper total still equals the BTC target volume; height is "
                "recomputed from the scaled DBH."
            ),
            (
                "Weibull-containing mixtures are fitted on the left-truncated grouped "
                "support; new-family unstable fits are reported but not selected, while "
                "the legacy two-gamma fallback may remain selected and is flagged."
            ),
            (
                "Spike families are attempted only when the first DBH bin exceeds the "
                "documented share or the best simple fit leaves a high KS. The spike "
                "carries the first bin and the continuous tail starts at the first bin "
                "upper edge; the spike weight is an AIC parameter and does not set the "
                "volume scale. Unconstrained spike rows are audit-only and are never "
                "synthesis-eligible because their untruncated tail mean can be "
                "infinite. Constrained spike tails use a bounded grouped MLE "
                "(shape >= 1, scale >= 0.1 cm); a fit at any bound is reported as "
                "boundary_reject and is not selected."
            ),
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
        "target_tph": target.target_tph,
        "diameter_scale_factor": stand.diameter_scale_factor,
        "constraint_mode": stand.constraint_mode,
        "n_species": len(target.species_weights),
        "n_trees": len(stand.trees),
        "tph": stand.tph,
        "taper_m3": stand.achieved_taper_volume_m3,
        "recovered_m3": summary["conservation"]["recovered_log_m3"],
        "residue_m3": summary["conservation"]["residue_m3"],
        "status": summary["status"],
        "notes": (
            "RSYC-stratified censored DBH fits; hierarchical height models; "
            "BTC TPH- and volume-constrained tree list; BC interior dimension "
            "sorts; dimension-only grade status; not an operational national model."
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
    parser.add_argument(
        "--baseline-fits",
        default=None,
        help=(
            "Previous distribution_fits.csv for the selection comparison; "
            "defaults to <out-dir>/distribution_fits.csv."
        ),
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
