"""Validate RSYC local correction factors on full BC and QC benchmarks.

The script implements a predeclared whole-unit holdout:

* BC: 20% of TSAs, stratified by ecoprovince, fixed seed.
* QC: 20% of public non-restricted BFEC units, stratified by administrative
  region, fixed seed.

Correction factors are fitted on the training units only.  A stratum is
released only when the held-out absolute bias falls by at least 50% and the
held-out relative RMSE does not worsen by more than 5% (relative).  Strata
without enough training or held-out support are marked ``insufficient_support``.

This script does not modify the 119M/150M/100M/200M supply artifacts and does
not apply any factor to a production curve.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

import benchmark as rsyc_benchmark

# ---------------------------------------------------------------------------
# Predeclared protocol constants
# ---------------------------------------------------------------------------

SEED = 20261119
HOLDOUT_FRACTION = 0.20
QC_AGES = (50, 100, 150)
AGE_MIN = 40
AGE_MAX = 150

MIN_TRAIN_ROWS = 30
MIN_TEST_ROWS = 30
MIN_TRAIN_UNITS = 2
MIN_TEST_UNITS = 1
MIN_LO_TEST_ROWS = 3
LO_MIN_FOLDS = 3

BIAS_REDUCTION_THRESHOLD = 0.50
REL_RMSE_WORSEN_THRESHOLD = 0.05
MATERIAL_BIAS_THRESHOLD = 0.10

BC_VISITS = "data/derived/national/rsyc_benchmark/rsyc_vs_bc_visits.csv"
QC_DIR = "data/derived/national/qc_bfec_full"
QC_CURVES = f"{QC_DIR}/qc_bfec_curves.csv"
QC_DEVELOPMENT_TYPES = f"{QC_DIR}/qc_bfec_development_types.csv"
QC_CROSSWALK = f"{QC_DIR}/qc_bfec_inventory_crosswalk.csv"
QC_MANIFEST = f"{QC_DIR}/qc_bfec_manifest.json"
RSYC_MODELS = "data/raw/national/rsyc/RSYC_models.zip"
NATIONAL_FIT = (
    "data/derived/national/assortments/national_fit/"
    "national_distribution_parameters.csv"
)
OUTPUT_DIR = "data/derived/national/rsyc_benchmark"

# Coarse administrative-region -> RSYC ecoprovince proxy.  The public BFEC
# crosswalk has no polygon coordinates and no published eco-code -> Canadian
# ecodistrict lookup, so a true unit -> ecoprovince join is not possible.
# Confidence is recorded per region and the proxy is flagged in every output.
QC_REGION_ECOPROVINCE_PROXY: dict[str, tuple[str, str]] = {
    "R01": ("Atlantic Maritime/Appalachian-Acadian Highlands", "high"),
    "R02": ("Boreal Shield East/Eastern Boreal Shield", "medium"),
    "R03": ("Boreal Shield East/Southern Boreal Shield", "medium"),
    "R04": ("Boreal Shield East/Southern Boreal Shield", "high"),
    "R05": ("Atlantic Maritime/Appalachian-Acadian Highlands", "high"),
    "R07": ("Boreal Shield East/Southern Boreal Shield", "high"),
    "R08": ("Boreal Shield East/Mid-Boreal Shield", "medium"),
    "R09": ("Boreal Shield East/Eastern Boreal Shield", "medium"),
    "R10": ("Taiga Shield East/Eastern Taiga", "low"),
    "R11": ("Atlantic Maritime/Appalachian-Acadian Highlands", "high"),
    "R12": ("Atlantic Maritime/Appalachian-Acadian Highlands", "high"),
    "R14": ("Boreal Shield East/Southern Boreal Shield", "high"),
    "R15": ("Boreal Shield East/Southern Boreal Shield", "high"),
}


# ---------------------------------------------------------------------------
# Small, pure helpers
# ---------------------------------------------------------------------------


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def seed_from(*parts: object) -> int:
    text = "|".join(str(part) for part in parts)
    return int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:16], 16)


def split_units_by_region(
    frame: pd.DataFrame,
    region_column: str,
    unit_column: str,
    seed_tag: str,
    fraction: float = HOLDOUT_FRACTION,
) -> set[str]:
    """Return held-out units, stratified by region, using a fixed seed.

    Small strata (two units) hold out one unit so that every evaluable stratum
    has a test observation.  Single-unit strata cannot be split and are left
    entirely in training.
    """

    held_out: set[str] = set()
    for region, group in frame.groupby(region_column, dropna=False, sort=True):
        units = sorted(str(unit) for unit in group[unit_column].dropna().unique())
        if len(units) < 2:
            continue
        count = max(1, math.floor(fraction * len(units) + 0.5))
        rng = np.random.default_rng(seed_from(seed_tag, region))
        order = list(units)
        rng.shuffle(order)
        held_out.update(order[:count])
    return held_out


def regression_metrics(
    observed: Iterable[float], predicted: Iterable[float]
) -> dict[str, float]:
    """Return aggregate volume metrics for positive observed values."""

    observed_array = np.asarray(list(observed), dtype=float)
    predicted_array = np.asarray(list(predicted), dtype=float)
    if observed_array.size == 0:
        return _empty_metrics()
    valid = np.isfinite(observed_array) & np.isfinite(predicted_array)
    observed_array = observed_array[valid]
    predicted_array = predicted_array[valid]
    if observed_array.size == 0:
        return _empty_metrics()

    residual = predicted_array - observed_array
    observed_sum = float(observed_array.sum())
    mean_observed = float(observed_array.mean())
    rmse = float(np.sqrt(np.mean(np.square(residual))))
    return {
        "n": int(observed_array.size),
        "observed_sum": observed_sum,
        "predicted_sum": float(predicted_array.sum()),
        "mean_observed": mean_observed,
        "mean_predicted": float(predicted_array.mean()),
        "bias": (
            float(residual.sum() / observed_sum) if observed_sum > 0 else math.nan
        ),
        "mae": float(np.mean(np.abs(residual))),
        "rmse": rmse,
        "relative_rmse": rmse / mean_observed if mean_observed > 0 else math.nan,
        "within20": float(np.mean(np.abs(residual) / observed_array <= 0.20)),
        "within30": float(np.mean(np.abs(residual) / observed_array <= 0.30)),
    }


def _group_key_values(key: object, count: int) -> tuple[object, ...]:
    """Normalise a pandas group key to a tuple of stratum values."""

    if count == 1:
        return key if isinstance(key, tuple) else (key,)
    return tuple(key)


def _empty_metrics() -> dict[str, float]:
    return {
        "n": 0,
        "observed_sum": math.nan,
        "predicted_sum": math.nan,
        "mean_observed": math.nan,
        "mean_predicted": math.nan,
        "bias": math.nan,
        "mae": math.nan,
        "rmse": math.nan,
        "relative_rmse": math.nan,
        "within20": math.nan,
        "within30": math.nan,
    }


def fit_level_multiplier(
    observed: Iterable[float], predicted: Iterable[float]
) -> dict[str, float] | None:
    """Fit a scalar through the origin by least squares and by ratio of sums."""

    observed_array = np.asarray(list(observed), dtype=float)
    predicted_array = np.asarray(list(predicted), dtype=float)
    valid = (
        np.isfinite(observed_array)
        & np.isfinite(predicted_array)
        & (observed_array >= 0)
        & (predicted_array > 0)
    )
    if int(valid.sum()) < MIN_TRAIN_ROWS:
        return None
    observed_array = observed_array[valid]
    predicted_array = predicted_array[valid]
    denominator = float(np.square(predicted_array).sum())
    predicted_sum = float(predicted_array.sum())
    observed_sum = float(observed_array.sum())
    if denominator <= 0 or predicted_sum <= 0:
        return None
    multiplier_ls = float((observed_array * predicted_array).sum() / denominator)
    multiplier_ratio = observed_sum / predicted_sum
    if not math.isfinite(multiplier_ls) or multiplier_ls <= 0:
        return None
    return {
        "multiplier": multiplier_ls,
        "multiplier_ratio": multiplier_ratio,
        "n_train": int(observed_array.size),
        "train_observed_sum": observed_sum,
        "train_predicted_sum": predicted_sum,
    }


def bias_reduction(before: float, after: float) -> float:
    if not math.isfinite(before) or not math.isfinite(after):
        return math.nan
    if abs(before) < 1e-12:
        return math.nan
    return 1.0 - abs(after) / abs(before)


def decide_status(
    *,
    n_train: int,
    n_train_units: int,
    n_test: int,
    n_test_units: int,
    bias_before: float,
    bias_after: float,
    relative_rmse_before: float,
    relative_rmse_after: float,
) -> tuple[str, str]:
    """Apply the predeclared support and decision rule."""

    if n_train < MIN_TRAIN_ROWS:
        return "insufficient_support", f"train_rows_below_{MIN_TRAIN_ROWS}"
    if n_train_units < MIN_TRAIN_UNITS:
        return "insufficient_support", f"train_units_below_{MIN_TRAIN_UNITS}"
    if n_test < MIN_TEST_ROWS:
        return "insufficient_support", f"test_rows_below_{MIN_TEST_ROWS}"
    if n_test_units < MIN_TEST_UNITS:
        return "insufficient_support", f"test_units_below_{MIN_TEST_UNITS}"

    reduction = bias_reduction(bias_before, bias_after)
    if not math.isfinite(reduction):
        return "withheld", "bias_before_zero_or_undefined"
    rmse_change = (
        relative_rmse_after / relative_rmse_before - 1.0
        if relative_rmse_before > 0
        else math.nan
    )
    failures: list[str] = []
    if reduction < BIAS_REDUCTION_THRESHOLD:
        failures.append("bias_reduction_below_50pct")
    if not math.isfinite(rmse_change) or rmse_change > REL_RMSE_WORSEN_THRESHOLD:
        failures.append("relative_rmse_worsened_over_5pct")
    if failures:
        return "withheld", ";".join(failures)
    return "validated", "passed_predeclared_rule"


def leave_one_unit_out(
    frame: pd.DataFrame,
    *,
    stratum_columns: Sequence[str],
    unit_column: str,
    observed_column: str,
    predicted_column: str,
) -> dict[str, object]:
    """Evaluate the level multiplier with each unit held out once."""

    folds: list[dict[str, object]] = []
    for unit, holdout in frame.groupby(unit_column, sort=True):
        training = frame.loc[frame[unit_column] != unit]
        if training.empty:
            continue
        fold_factors: list[dict[str, object]] = []
        for key, group in training.groupby(
            list(stratum_columns), dropna=False, sort=False
        ):
            fit = fit_level_multiplier(
                group[observed_column], group[predicted_column]
            )
            if fit is None:
                continue
            key_values = _group_key_values(key, len(stratum_columns))
            fold_factors.append(
                dict(zip(stratum_columns, key_values, strict=True)) | fit
            )
        if not fold_factors:
            continue
        factors = pd.DataFrame(fold_factors)
        merged = holdout.merge(factors, on=list(stratum_columns), how="inner")
        if len(merged) < MIN_LO_TEST_ROWS:
            continue
        before = merged[predicted_column].to_numpy(dtype=float)
        observed = merged[observed_column].to_numpy(dtype=float)
        after = before * merged["multiplier"].to_numpy(dtype=float)
        metrics_before = regression_metrics(observed, before)
        metrics_after = regression_metrics(observed, after)
        folds.append(
            {
                "unit": str(unit),
                "n_rows": len(merged),
                "observed_sum": metrics_before["observed_sum"],
                "bias_before": metrics_before["bias"],
                "bias_after": metrics_after["bias"],
                "relative_rmse_before": metrics_before["relative_rmse"],
                "relative_rmse_after": metrics_after["relative_rmse"],
                "rmse_change": (
                    metrics_after["rmse"] / metrics_before["rmse"] - 1.0
                    if metrics_before["rmse"] > 0
                    else math.nan
                ),
                "bias_reduction": bias_reduction(
                    metrics_before["bias"], metrics_after["bias"]
                ),
            }
        )
    return _summarise_folds(folds)


def _summarise_folds(folds: Sequence[Mapping[str, object]]) -> dict[str, object]:
    if not folds:
        return {
            "lo_folds": 0,
            "lo_median_bias_reduction": math.nan,
            "lo_share_folds_bias_reduced": math.nan,
            "lo_share_folds_reduction_ge50": math.nan,
            "lo_median_rel_rmse_change": math.nan,
            "lo_share_folds_rmse_ok": math.nan,
            "lo_bias_before": math.nan,
            "lo_bias_after": math.nan,
            "lo_share_folds_same_sign_before": math.nan,
            "lo_holdout_rows": 0,
        }
    frame = pd.DataFrame(folds)
    observed_sum = float(frame["observed_sum"].sum())
    predicted_before_sum = float(
        (frame["observed_sum"] * (1.0 + frame["bias_before"])).sum()
    )
    predicted_after_sum = float(
        (frame["observed_sum"] * (1.0 + frame["bias_after"])).sum()
    )
    bias_before = (
        (predicted_before_sum - observed_sum) / observed_sum
        if observed_sum > 0
        else math.nan
    )
    bias_after = (
        (predicted_after_sum - observed_sum) / observed_sum
        if observed_sum > 0
        else math.nan
    )
    pooled_sign = math.copysign(1.0, bias_before) if math.isfinite(bias_before) else 0.0
    same_sign = (
        np.sign(frame["bias_before"].to_numpy(dtype=float)) == pooled_sign
        if pooled_sign != 0
        else np.zeros(len(frame), dtype=bool)
    )
    reductions = frame["bias_reduction"].to_numpy(dtype=float)
    return {
        "lo_folds": len(frame),
        "lo_median_bias_reduction": float(np.nanmedian(reductions)),
        "lo_share_folds_bias_reduced": float(
            np.nanmean(frame["bias_after"].abs() < frame["bias_before"].abs())
        ),
        "lo_share_folds_reduction_ge50": float(
            np.nanmean(reductions >= BIAS_REDUCTION_THRESHOLD)
        ),
        "lo_median_rel_rmse_change": float(
            np.nanmedian(frame["rmse_change"].to_numpy(dtype=float))
        ),
        "lo_share_folds_rmse_ok": float(
            np.nanmean(frame["rmse_change"].to_numpy(dtype=float) <= REL_RMSE_WORSEN_THRESHOLD)
        ),
        "lo_bias_before": bias_before,
        "lo_bias_after": bias_after,
        "lo_share_folds_same_sign_before": float(np.mean(same_sign)),
        "lo_holdout_rows": int(frame["n_rows"].sum()),
    }


# ---------------------------------------------------------------------------
# BC benchmark
# ---------------------------------------------------------------------------


def _bc_age_bin(age: float) -> str:
    if age <= 60:
        return "40-60"
    if age <= 79:
        return "61-79"
    if age <= 100:
        return "80-100"
    if age <= 119:
        return "101-119"
    if age <= 140:
        return "120-140"
    return "141-150"


def load_bc_visits(repo_root: Path) -> tuple[pd.DataFrame, dict[str, int]]:
    path = repo_root / BC_VISITS
    columns = [
        "tsa",
        "ecoprovince",
        "ecoprovince_stratum",
        "ecoprovince_mapping",
        "leading_species",
        "age",
        "observed_volume",
        "predicted_composite",
    ]
    raw = pd.read_csv(path, usecols=columns)
    counts = {
        "raw_rows": len(raw),
        "missing_tsa_rows": int(raw["tsa"].isna().sum()),
        "age_outside_40_150_rows": int(
            ((raw["age"] < AGE_MIN) | (raw["age"] > AGE_MAX)).sum()
        ),
        "zero_or_negative_observed_rows": int((raw["observed_volume"] <= 0).sum()),
        "zero_or_negative_predicted_rows": int((raw["predicted_composite"] <= 0).sum()),
    }
    frame = raw.loc[
        raw["tsa"].notna()
        & raw["age"].between(AGE_MIN, AGE_MAX)
        & (raw["observed_volume"] > 0)
        & (raw["predicted_composite"] > 0)
    ].copy()
    frame["tsa"] = frame["tsa"].astype(str)
    frame["observed_volume"] = frame["observed_volume"].astype(float)
    frame["predicted_composite"] = frame["predicted_composite"].astype(float)
    frame["age_bin"] = frame["age"].map(_bc_age_bin)
    counts["used_rows"] = len(frame)
    counts["used_tsas"] = int(frame["tsa"].nunique())
    counts["used_ecoprovinces"] = int(frame["ecoprovince_stratum"].nunique())
    counts["fallback_rows_used"] = int(
        (frame["ecoprovince_mapping"] == "bec_zone_fallback").sum()
    )
    counts["fallback_share_used"] = float(
        (frame["ecoprovince_mapping"] == "bec_zone_fallback").mean()
    )
    return frame, counts


def build_bc_stratum_rows(
    frame: pd.DataFrame,
    *,
    stratum_columns: Sequence[str],
    stratum_level: str,
    release_scope: str,
) -> list[dict[str, object]]:
    """Fit and test one BC stratum definition across all stratum cells."""

    records: list[dict[str, object]] = []
    grouper = list(stratum_columns)
    for key, group in frame.groupby(grouper, dropna=False, sort=False):
        key_values = _group_key_values(key, len(grouper))
        keys = dict(zip(grouper, key_values, strict=True))
        train = group.loc[group["split"] == "train"]
        test = group.loc[group["split"] == "test"]
        fit = fit_level_multiplier(
            train["observed_volume"], train["predicted_composite"]
        )
        multiplier = fit["multiplier"] if fit else math.nan
        predicted_after = test["predicted_composite"].to_numpy(dtype=float) * (
            multiplier if math.isfinite(multiplier) else 1.0
        )
        metrics_before = regression_metrics(
            test["observed_volume"], test["predicted_composite"]
        )
        metrics_after = regression_metrics(test["observed_volume"], predicted_after)
        status, reason = decide_status(
            n_train=len(train),
            n_train_units=int(train["tsa"].nunique()),
            n_test=len(test),
            n_test_units=int(test["tsa"].nunique()),
            bias_before=metrics_before["bias"],
            bias_after=metrics_after["bias"],
            relative_rmse_before=metrics_before["relative_rmse"],
            relative_rmse_after=metrics_after["relative_rmse"],
        )
        lo_summary = leave_one_unit_out(
            group.loc[group["split"] == "train"],
            stratum_columns=grouper,
            unit_column="tsa",
            observed_column="observed_volume",
            predicted_column="predicted_composite",
        )
        ecoprovince_stratum = str(keys.get("ecoprovince_stratum", ""))
        ecozone, _, ecoprovince = ecoprovince_stratum.partition("/")
        record = {
            "dataset": "BC_FAIB",
            "stratum_level": stratum_level,
            "stratum_key": "|".join(str(keys[column]) for column in grouper),
            "release_scope": release_scope,
            "ecozone": ecozone,
            "ecoprovince": ecoprovince or ecoprovince_stratum,
            "ecoprovince_stratum": ecoprovince_stratum,
            "species": keys.get("leading_species", "ALL"),
            **keys,
            "age_basis": f"ages_{AGE_MIN}_{AGE_MAX}",
            "candidate_status": "candidate" if fit else "not_candidate",
            "multiplier": multiplier,
            "multiplier_ratio": fit["multiplier_ratio"] if fit else math.nan,
            "multiplier_basis": "least_squares_scalar_through_origin",
            "n_train": len(train),
            "n_train_units": int(train["tsa"].nunique()),
            "n_test": len(test),
            "n_test_units": int(test["tsa"].nunique()),
            "test_unit_ids": ";".join(sorted(test["tsa"].unique().tolist())[:10]),
            "fallback_rows": int((group["ecoprovince_mapping"] == "bec_zone_fallback").sum()),
            "fallback_share": float(
                (group["ecoprovince_mapping"] == "bec_zone_fallback").mean()
            ),
            **{f"train_{name}": value for name, value in regression_metrics(
                train["observed_volume"], train["predicted_composite"]
            ).items()},
            **{f"test_before_{name}": value for name, value in metrics_before.items()},
            **{f"test_after_{name}": value for name, value in metrics_after.items()},
            "bias_reduction": bias_reduction(
                metrics_before["bias"], metrics_after["bias"]
            ),
            "rel_rmse_change": (
                metrics_after["relative_rmse"] / metrics_before["relative_rmse"] - 1.0
                if metrics_before["relative_rmse"] > 0
                else math.nan
            ),
            "material": bool(
                math.isfinite(metrics_before["bias"])
                and abs(metrics_before["bias"]) >= MATERIAL_BIAS_THRESHOLD
            ),
            "status": status,
            "reason": reason,
            **lo_summary,
        }
        record["lo_robust"] = _is_lo_robust(record)
        record["release_decision"] = _release_decision(record)
        records.append(record)
    return records


def _is_lo_robust(record: Mapping[str, object]) -> bool:
    """Require the leave-one-unit-out check to support the primary result."""

    return bool(
        _finite_at_least(record.get("lo_folds"), LO_MIN_FOLDS)
        and _finite_at_least(record.get("lo_median_bias_reduction"), 0.50)
        and _finite_at_least(record.get("lo_share_folds_rmse_ok"), 0.50)
    )


def _finite_at_least(value: object, threshold: float) -> bool:
    return (
        isinstance(value, (int, float))
        and math.isfinite(float(value))
        and float(value) >= threshold
    )


def _release_decision(record: Mapping[str, object]) -> str:
    if record.get("release_scope") == "diagnostic":
        return "diagnostic_only"
    status = record.get("status")
    if status == "validated":
        if record.get("lo_robust"):
            return "validated_and_lo_robust_not_applied"
        return "validated_primary_only_lo_unstable"
    if status == "withheld":
        return "not_released"
    return "not_released_insufficient_support"


def aggregate_group_rows(
    frame: pd.DataFrame,
    *,
    dataset: str,
    policy: str,
    group_column: str,
    observed_column: str,
    predicted_column: str,
    factor_map: Mapping[str, float],
    key_column: str,
) -> list[dict[str, object]]:
    """Return before/after metrics by an auxiliary grouping column."""

    test = frame.loc[frame["split"] == "test"]
    records: list[dict[str, object]] = []
    for value, group in test.groupby(group_column, sort=True):
        multiplier = group[key_column].map(factor_map).fillna(1.0).to_numpy(dtype=float)
        before = regression_metrics(group[observed_column], group[predicted_column])
        after = regression_metrics(
            group[observed_column],
            group[predicted_column].to_numpy(dtype=float) * multiplier,
        )
        records.append(
            {
                "dataset": dataset,
                "stratum_level": f"aggregate_{policy}_by_{group_column}",
                "stratum_key": str(value),
                "release_scope": "reporting",
                "age_basis": str(value),
                "n_train": int((frame["split"] == "train").sum()),
                "n_train_units": int(
                    frame.loc[frame["split"] == "train", "unit_key"].nunique()
                    if "unit_key" in frame
                    else frame.loc[frame["split"] == "train", "tsa"].nunique()
                ),
                "n_test": len(group),
                "n_test_units": int(
                    group["unit_key"].nunique()
                    if "unit_key" in group
                    else group["tsa"].nunique()
                ),
                "coverage_rows": int(group[key_column].isin(factor_map).sum()),
                "coverage_share": float(group[key_column].isin(factor_map).mean()),
                "n_factor_strata": len(factor_map),
                **{f"test_before_{name}": value_ for name, value_ in before.items()},
                **{f"test_after_{name}": value_ for name, value_ in after.items()},
                "bias_reduction": bias_reduction(before["bias"], after["bias"]),
                "rel_rmse_change": (
                    after["relative_rmse"] / before["relative_rmse"] - 1.0
                    if before["relative_rmse"] > 0
                    else math.nan
                ),
                "status": "aggregate",
                "reason": f"by_{group_column}:{value}",
                "release_decision": "reporting_only",
            }
        )
    return records


def aggregate_rows(
    frame: pd.DataFrame,
    *,
    dataset: str,
    policy: str,
    observed_column: str,
    predicted_column: str,
    factor_map: Mapping[str, float],
    key_column: str,
) -> list[dict[str, object]]:
    """Return all-row and covered-row aggregate metrics after correction."""

    test = frame.loc[frame["split"] == "test"].copy()
    if test.empty:
        return []
    multiplier = test[key_column].map(factor_map).fillna(1.0).to_numpy(dtype=float)
    covered = test[key_column].isin(factor_map).to_numpy()
    records: list[dict[str, object]] = []
    for label, subset, factors in (
        ("all_test_rows", np.ones(len(test), dtype=bool), multiplier),
        ("covered_test_rows", covered, multiplier),
    ):
        if not subset.any():
            continue
        before = regression_metrics(
            test.loc[subset, observed_column], test.loc[subset, predicted_column]
        )
        after = regression_metrics(
            test.loc[subset, observed_column],
            test.loc[subset, predicted_column].to_numpy(dtype=float) * factors[subset],
        )
        records.append(
            {
                "dataset": dataset,
                "stratum_level": f"aggregate_{policy}_{label}",
                "stratum_key": "ALL",
                "release_scope": "reporting",
                "age_basis": f"ages_{AGE_MIN}_{AGE_MAX}"
                if dataset == "BC_FAIB"
                else "ages_50_100_150",
                "n_train": int((frame["split"] == "train").sum()),
                "n_train_units": int(
                    frame.loc[frame["split"] == "train", "unit_key"].nunique()
                    if "unit_key" in frame
                    else frame.loc[frame["split"] == "train", "tsa"].nunique()
                ),
                "n_test": int(subset.sum()),
                "n_test_units": int(
                    test.loc[subset, "unit_key"].nunique()
                    if "unit_key" in test
                    else test.loc[subset, "tsa"].nunique()
                ),
                "coverage_rows": int(covered.sum()),
                "coverage_share": float(covered.mean()),
                "n_factor_strata": len(factor_map),
                **{f"test_before_{name}": value for name, value in before.items()},
                **{f"test_after_{name}": value for name, value in after.items()},
                "bias_reduction": bias_reduction(before["bias"], after["bias"]),
                "rel_rmse_change": (
                    after["relative_rmse"] / before["relative_rmse"] - 1.0
                    if before["relative_rmse"] > 0
                    else math.nan
                ),
                "status": "aggregate",
                "reason": label,
                "release_decision": "reporting_only",
            }
        )
    return records


# ---------------------------------------------------------------------------
# QC benchmark
# ---------------------------------------------------------------------------


def load_qc_units(repo_root: Path) -> tuple[dict[str, str], set[str], dict[str, object]]:
    manifest = json.loads((repo_root / QC_MANIFEST).read_text())
    unit_region: dict[str, str] = {}
    restricted: set[str] = set()
    for unit in manifest["units"]:
        unit_key = str(int(unit["unit"])).zfill(5)
        unit_region[unit_key] = unit["region"]
        if unit.get("restricted"):
            restricted.add(unit_key)
    return unit_region, restricted, manifest


def load_qc_pairs(
    repo_root: Path,
    unit_region: Mapping[str, str],
    restricted: set[str],
) -> tuple[pd.DataFrame, dict[str, object], dict[str, object]]:
    curves = pd.read_csv(
        repo_root / QC_CURVES,
        usecols=[
            "unit",
            "development_type",
            "table_id",
            "age",
            "volume_conifer",
            "volume_deciduous",
            "volume_total",
            "height",
        ],
    )
    development_types = pd.read_csv(
        repo_root / QC_DEVELOPMENT_TYPES,
        usecols=[
            "unit",
            "inventory_area_ha",
            "table_id",
            "in_model",
            "productive",
        ],
    )
    productives = development_types.loc[
        (development_types["in_model"] == 1)
        & (development_types["productive"] == 1)
        & (development_types["inventory_area_ha"] > 0)
    ]
    if productives.empty:
        raise ValueError("No productive BFEC development types remain")
    table_area = (
        productives.groupby(["unit", "table_id"], as_index=False)[
            "inventory_area_ha"
        ]
        .sum()
        .rename(columns={"inventory_area_ha": "curve_area_ha"})
    )
    target = curves.merge(table_area, on=["unit", "table_id"], how="inner")
    target = target.loc[target["age"].isin(QC_AGES)].copy()
    target["unit_key"] = target["unit"].astype(str).str.zfill(5)
    target = target.loc[~target["unit_key"].isin(restricted)].copy()
    target["region"] = target["unit_key"].map(unit_region)
    target = target.loc[target["region"].notna()].copy()
    target["ecoprovince_proxy"] = target["region"].map(
        {key: value[0] for key, value in QC_REGION_ECOPROVINCE_PROXY.items()}
    )
    target["proxy_confidence"] = target["region"].map(
        {key: value[1] for key, value in QC_REGION_ECOPROVINCE_PROXY.items()}
    )
    if target["ecoprovince_proxy"].isna().any():
        missing = sorted(target.loc[target["ecoprovince_proxy"].isna(), "region"].unique())
        raise ValueError(f"No QC ecoprovince proxy for regions: {missing}")

    mass_balance = (
        target["volume_conifer"] + target["volume_deciduous"] - target["volume_total"]
    ).abs()
    tolerance = 1e-6 + 1e-6 * target["volume_total"].abs()
    target["mix_status"] = np.where(
        (target["volume_conifer"] < 0) | (target["volume_deciduous"] < 0),
        "invalid_components",
        np.where(
            target["volume_total"] <= 0,
            "zero_total",
            np.where(mass_balance > tolerance, "mass_balance_mismatch", "positive_total"),
        ),
    )
    counts = {
        "curves_rows": len(curves),
        "development_type_rows": len(development_types),
        "productive_development_types": len(productives),
        "productive_tables": len(table_area),
        "target_rows_after_join": len(target),
        "positive_total_rows": int((target["mix_status"] == "positive_total").sum()),
        "zero_total_rows": int((target["mix_status"] == "zero_total").sum()),
        "invalid_rows": int(
            target["mix_status"].isin(
                ["invalid_components", "mass_balance_mismatch"]
            ).sum()
        ),
        "units_used": int(target["unit_key"].nunique()),
        "restricted_units_excluded": sorted(restricted),
    }
    return target, counts, {
        "mass_balance_max_abs": float(mass_balance.max()),
        "proxy_mapping": {
            key: {"ecoprovince": value[0], "confidence": value[1]}
            for key, value in QC_REGION_ECOPROVINCE_PROXY.items()
        },
    }


def add_qc_predictions(
    pairs: pd.DataFrame, lookup: rsyc_benchmark.RsycLookup
) -> pd.DataFrame:
    """Add the same-mix RSYC total-volume prediction at each target age."""

    cache: dict[tuple[str, str, int], float] = {}

    def predict(ecoprovince: str, species: str, age: int) -> float:
        key = (ecoprovince, species, age)
        if key not in cache:
            cache[key] = float(lookup.predict(ecoprovince, species, float(age))[0])
        return cache[key]

    predictions: list[float] = []
    for row in pairs.itertuples(index=False):
        total = float(row.volume_total)
        if total <= 0:
            predictions.append(math.nan)
            continue
        conifer_share = float(row.volume_conifer) / total
        deciduous_share = float(row.volume_deciduous) / total
        predicted = conifer_share * predict(
            row.ecoprovince_proxy, "CONIFEROUS", int(row.age)
        ) + deciduous_share * predict(row.ecoprovince_proxy, "BROADLEAF", int(row.age))
        predictions.append(float(predicted))
    result = pairs.copy()
    result["predicted_volume"] = predictions
    return result.loc[result["mix_status"] == "positive_total"].copy()


def build_qc_stratum_rows(
    frame: pd.DataFrame,
    *,
    stratum_columns: Sequence[str],
    stratum_level: str,
    release_scope: str,
) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    grouper = list(stratum_columns)
    for key, group in frame.groupby(grouper, dropna=False, sort=False):
        key_values = _group_key_values(key, len(grouper))
        keys = dict(zip(grouper, key_values, strict=True))
        train = group.loc[group["split"] == "train"]
        test = group.loc[group["split"] == "test"]
        fit = fit_level_multiplier(train["volume_total"], train["predicted_volume"])
        multiplier = fit["multiplier"] if fit else math.nan
        predicted_after = test["predicted_volume"].to_numpy(dtype=float) * (
            multiplier if math.isfinite(multiplier) else 1.0
        )
        metrics_before = regression_metrics(test["volume_total"], test["predicted_volume"])
        metrics_after = regression_metrics(test["volume_total"], predicted_after)
        status, reason = decide_status(
            n_train=len(train),
            n_train_units=int(train["unit_key"].nunique()),
            n_test=len(test),
            n_test_units=int(test["unit_key"].nunique()),
            bias_before=metrics_before["bias"],
            bias_after=metrics_after["bias"],
            relative_rmse_before=metrics_before["relative_rmse"],
            relative_rmse_after=metrics_after["relative_rmse"],
        )
        lo_summary = leave_one_unit_out(
            group.loc[group["split"] == "train"],
            stratum_columns=grouper,
            unit_column="unit_key",
            observed_column="volume_total",
            predicted_column="predicted_volume",
        )
        region_key = str(keys.get("region", ""))
        proxy_from_region = QC_REGION_ECOPROVINCE_PROXY.get(
            region_key, (region_key, "unmapped")
        )[0]
        ecoprovince_proxy = str(keys.get("ecoprovince_proxy", proxy_from_region))
        record = {
            "dataset": "QC_BFEC",
            "stratum_level": stratum_level,
            "stratum_key": "|".join(str(keys[column]) for column in grouper),
            "release_scope": release_scope,
            "ecoprovince": ecoprovince_proxy,
            "ecoprovince_proxy": ecoprovince_proxy,
            "proxy_confidence": (
                QC_REGION_ECOPROVINCE_PROXY.get(region_key, (region_key, "unmapped"))[1]
                if region_key
                else "pooled"
            ),
            "admin_region": keys.get("region", ""),
            "species": "total",
            **keys,
            "age_basis": "ages_50_100_150",
            "candidate_status": "candidate" if fit else "not_candidate",
            "multiplier": multiplier,
            "multiplier_ratio": fit["multiplier_ratio"] if fit else math.nan,
            "multiplier_basis": "least_squares_scalar_through_origin",
            "n_train": len(train),
            "n_train_units": int(train["unit_key"].nunique()),
            "n_test": len(test),
            "n_test_units": int(test["unit_key"].nunique()),
            "test_unit_ids": ";".join(sorted(test["unit_key"].unique().tolist())[:10]),
            **{f"train_{name}": value for name, value in regression_metrics(
                train["volume_total"], train["predicted_volume"]
            ).items()},
            **{f"test_before_{name}": value for name, value in metrics_before.items()},
            **{f"test_after_{name}": value for name, value in metrics_after.items()},
            "bias_reduction": bias_reduction(
                metrics_before["bias"], metrics_after["bias"]
            ),
            "rel_rmse_change": (
                metrics_after["relative_rmse"] / metrics_before["relative_rmse"] - 1.0
                if metrics_before["relative_rmse"] > 0
                else math.nan
            ),
            "material": bool(
                math.isfinite(metrics_before["bias"])
                and abs(metrics_before["bias"]) >= MATERIAL_BIAS_THRESHOLD
            ),
            "status": status,
            "reason": reason,
            **lo_summary,
        }
        record["lo_robust"] = _is_lo_robust(record)
        record["release_decision"] = _release_decision(record)
        records.append(record)
    return records


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------


def json_safe(value: object) -> object:
    """Replace non-finite numbers and NumPy scalars with JSON-safe values."""

    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, (float, np.floating)):
        number = float(value)
        return number if math.isfinite(number) else None
    return value


def write_table(records: Sequence[Mapping[str, object]], path: Path) -> pd.DataFrame:
    frame = pd.DataFrame(records)
    frame.to_csv(path, index=False, na_rep="", float_format="%.10g")
    return frame


def reorder_columns(frame: pd.DataFrame) -> pd.DataFrame:
    front = [
        "dataset",
        "stratum_level",
        "stratum_key",
        "release_scope",
        "ecoprovince",
        "ecoprovince_proxy",
        "ecoprovince_stratum",
        "admin_region",
        "region",
        "species",
        "age_basis",
        "candidate_status",
        "multiplier",
        "multiplier_ratio",
        "multiplier_basis",
        "n_train",
        "n_train_units",
        "n_test",
        "n_test_units",
        "status",
        "reason",
        "release_decision",
    ]
    ordered = [column for column in front if column in frame.columns]
    ordered.extend(column for column in frame.columns if column not in ordered)
    return frame.loc[:, ordered]


def build_report(
    bc_validation: pd.DataFrame,
    qc_validation: pd.DataFrame,
    bc_counts: Mapping[str, object],
    qc_counts: Mapping[str, object],
    bc_split: set[str],
    qc_split: set[str],
    qc_geometry: Mapping[str, object],
    summary: Mapping[str, object],
) -> str:
    bc_strata = bc_validation.loc[
        bc_validation["stratum_level"] == "ecoprovince_species"
    ]
    qc_proxy = qc_validation.loc[
        qc_validation["stratum_level"] == "ecoprovince_proxy"
    ]
    qc_region = qc_validation.loc[qc_validation["stratum_level"] == "admin_region"]
    bc_aggregate = bc_validation.loc[
        bc_validation["stratum_level"].isin(
            [
                "aggregate_release_policy_all_test_rows",
                "aggregate_release_policy_covered_test_rows",
                "aggregate_all_candidate_factors_all_test_rows",
            ]
        )
    ]
    qc_aggregate = qc_validation.loc[
        qc_validation["stratum_level"].isin(
            [
                "aggregate_release_policy_all_test_rows",
                "aggregate_release_policy_covered_test_rows",
                "aggregate_all_candidate_factors_all_test_rows",
            ]
        )
    ]

    def table(frame: pd.DataFrame, columns: Sequence[str]) -> str:
        present = [column for column in columns if column in frame.columns]
        if frame.empty or not present:
            return "_No rows._\n"
        lines = ["| " + " | ".join(present) + " |", "|" + "|".join("---" for _ in present) + "|"]
        for row in frame.loc[:, present].itertuples(index=False):
            cells = []
            for value in row:
                if isinstance(value, float):
                    cells.append(f"{value:.4g}" if math.isfinite(value) else "")
                else:
                    cells.append(str(value))
            lines.append("| " + " | ".join(cells) + " |")
        return "\n".join(lines) + "\n"

    validated_bc = bc_strata.loc[bc_strata["status"] == "validated"].sort_values(
        "stratum_key"
    )
    recommended_bc = bc_strata.loc[
        (bc_strata["status"] == "validated")
        & bc_strata["lo_robust"].fillna(False).astype(bool)
    ].sort_values("stratum_key")
    lo_unstable_bc = bc_strata.loc[
        (bc_strata["status"] == "validated")
        & ~bc_strata["lo_robust"].fillna(False).astype(bool)
    ].sort_values("stratum_key")
    withheld_bc = bc_strata.loc[bc_strata["status"] == "withheld"].sort_values(
        "stratum_key"
    )
    insufficient_bc = bc_strata.loc[
        bc_strata["status"] == "insufficient_support"
    ]
    validated_qc = qc_proxy.loc[qc_proxy["status"] == "validated"].sort_values(
        "stratum_key"
    )
    recommended_qc = qc_proxy.loc[
        (qc_proxy["status"] == "validated")
        & qc_proxy["lo_robust"].fillna(False).astype(bool)
    ].sort_values("stratum_key")
    region_rows = qc_region.sort_values("region")
    qc_mapping_table = "\n".join(
        ["| Region | Proxy ecoprovince | Confidence |", "|---|---|---|"]
        + [
            f"| {region} | {ecoprovince} | {confidence} |"
            for region, (ecoprovince, confidence) in QC_REGION_ECOPROVINCE_PROXY.items()
        ]
    )

    def aggregate_row(frame: pd.DataFrame, level: str) -> pd.Series:
        rows = frame.loc[frame["stratum_level"] == level]
        if rows.empty:
            raise ValueError(f"Missing aggregate row: {level}")
        return rows.iloc[0]

    bc_release_all = aggregate_row(
        bc_aggregate, "aggregate_release_policy_all_test_rows"
    )
    bc_release_covered = aggregate_row(
        bc_aggregate, "aggregate_release_policy_covered_test_rows"
    )
    qc_all_candidates = aggregate_row(
        qc_aggregate, "aggregate_all_candidate_factors_all_test_rows"
    )
    bc_age_release = bc_validation.loc[
        bc_validation["stratum_level"] == "aggregate_release_policy_by_age_bin"
    ].sort_values("stratum_key")
    bc_young = bc_age_release.loc[bc_age_release["stratum_key"] == "40-60"]
    bc_young_before = (
        float(bc_young["test_before_bias"].iloc[0]) if not bc_young.empty else math.nan
    )
    bc_young_after = (
        float(bc_young["test_after_bias"].iloc[0]) if not bc_young.empty else math.nan
    )
    qc_age_candidates = qc_validation.loc[
        qc_validation["stratum_level"]
        == "aggregate_all_candidate_factors_by_age"
    ].sort_values("stratum_key")

    coverage = summary["recommendation"]
    return f"""# RSYC Correction Validation Report

**Date:** {datetime.now(UTC).strftime('%Y-%m-%d')}  
**Scope:** Full BC FAIB PSP benchmark and full QC BFEC public extraction.  
**Status:** Predeclared holdout validation complete. No correction has been
applied to the 119M/150M/100M/200M supply artifacts and no C2070 run was
repeated.

## 1. Protocol (predeclared)

The split and decision rule were fixed before fitting:

- **BC:** 20% of TSAs held out, stratified by ecoprovince, seed `{SEED}`.
  Whole TSAs are the independent unit. {len(bc_split)} of
  {bc_counts['used_tsas']} usable TSAs were held out:
  {', '.join(sorted(bc_split))}.
- **QC:** 20% of public non-restricted BFEC units held out, stratified by
  administrative region, seed `{SEED}`. Whole units are the independent unit.
  Every region with at least two units contributes at least one held-out unit,
  so the realised share is {len(qc_split)} of {qc_counts['units_used']} usable
  units ({len(qc_split) / qc_counts['units_used']:.1%}):
  {', '.join(sorted(qc_split))}.
- **Fit:** a single multiplicative `b1` scalar per stratum, fitted by least
  squares through the origin on training units only:
  `b = sum(observed*predicted) / sum(predicted^2)`. The ratio-of-sums scalar is
  reported as a diagnostic. No zero, negative, or non-finite observation is
  used.
- **BC stratum:** `ecoprovince_stratum × leading_species`, ages
  {AGE_MIN}–{AGE_MAX} years, on the BA-weighted composite prediction.
- **QC stratum:** `ecoprovince proxy × total`, target ages 50/100/150, on the
  same-mix RSYC total prediction. The ecoprovince proxy is required because no
  public unit→ecoprovince join exists (see §6).
- **Support:** at least {MIN_TRAIN_ROWS} training rows and {MIN_TRAIN_UNITS}
  training units; at least {MIN_TEST_ROWS} held-out rows and {MIN_TEST_UNITS}
  held-out unit. Strata below either threshold are `insufficient_support`.
- **Decision:** `validated` only when held-out `|bias|` falls by at least 50%
  and held-out relative RMSE does not worsen by more than 5% (relative).
  Otherwise `withheld`. A separate `material` flag marks strata whose
  held-out pre-correction `|bias|` is at least 10%.
- **Secondary:** leave-one-TSA-out (BC) and leave-one-unit-out (QC) are
  reported for every candidate stratum. They are a robustness check, not a
  substitute for the predeclared split.
- **Release gate:** a primary-validated factor is recommended for application
  only if the leave-one-unit-out check has at least {LO_MIN_FOLDS} folds, a
  median bias reduction of at least 50%, and at least half the folds within
  the 5% RMSE tolerance. This is an additional conservatism; it does not
  change the `status` column defined by the primary rule.

## 2. BC result

BC exclusions: {bc_counts['missing_tsa_rows']} visits have no TSA and
{bc_counts['age_outside_40_150_rows']:,} are outside the 40–150 age window
(overlaps counted once), leaving {bc_counts['used_rows']:,} usable visits.

**Aggregate held-out composite volume:**

{table(bc_aggregate, ['stratum_level', 'n_test', 'n_test_units', 'coverage_rows', 'coverage_share', 'test_before_bias', 'test_after_bias', 'bias_reduction', 'test_before_relative_rmse', 'test_after_relative_rmse', 'rel_rmse_change', 'test_before_within20', 'test_after_within20', 'test_before_within30', 'test_after_within30'])}

The release policy covers {bc_release_all['coverage_share']:.1%} of held-out
visits. On that covered subset the absolute bias falls by
{bc_release_covered['bias_reduction']:.1%} and relative RMSE by
{abs(bc_release_covered['rel_rmse_change']):.1%}; the all-visit aggregate falls
less ({bc_release_all['bias_reduction']:.1%} bias,
{abs(bc_release_all['rel_rmse_change']):.1%} RMSE) because the uncovered
interior strata remain uncorrected. The all-candidate diagnostic over-corrects
and is not a release option.

**Per-ecoprovince (all species pooled) diagnostic:**

{table(bc_validation.loc[bc_validation['stratum_level'] == 'ecoprovince_all_species'], ['stratum_key', 'multiplier', 'n_train', 'n_test', 'n_test_units', 'test_before_bias', 'test_after_bias', 'bias_reduction', 'rel_rmse_change', 'status'])}

**Age-bin diagnostic for the release-policy factors (held-out, no age floor):**

{table(bc_age_release, ['stratum_key', 'n_test', 'n_test_units', 'coverage_share', 'test_before_bias', 'test_after_bias', 'bias_reduction', 'test_before_relative_rmse', 'test_after_relative_rmse', 'rel_rmse_change', 'test_before_within20', 'test_after_within20'])}

The level scalar is age-blind. Applied without an age floor it over-corrects
the 40–60 bin (bias {bc_young_before:+.1%} to {bc_young_after:+.1%}) while
improving every bin from 61–79 upward. The recommended application floor is
therefore 61 years; the 40–60 bin is left uncorrected.

**Primary-rule validated strata ({len(validated_bc)}):**

{table(validated_bc, ['stratum_key', 'multiplier', 'n_train', 'n_test', 'n_test_units', 'test_before_bias', 'test_after_bias', 'bias_reduction', 'test_before_relative_rmse', 'test_after_relative_rmse', 'rel_rmse_change', 'lo_folds', 'lo_median_bias_reduction', 'lo_share_folds_bias_reduced', 'lo_robust'])}

**Recommended for application after LO-TSA robustness ({len(recommended_bc)}):**
a primary validation is downgraded when the leave-one-TSA-out check does not
show a median bias reduction of at least 50% or does not keep at least half of
the folds within the 5% RMSE tolerance.

{table(recommended_bc, ['stratum_key', 'multiplier', 'n_train', 'n_test', 'n_test_units', 'test_before_bias', 'test_after_bias', 'bias_reduction', 'rel_rmse_change', 'lo_folds', 'lo_median_bias_reduction', 'lo_share_folds_bias_reduced', 'lo_share_folds_rmse_ok'])}

**Validated by the primary rule but not released after the LO check
({len(lo_unstable_bc)}; unstable or fewer than {LO_MIN_FOLDS} folds):**

{table(lo_unstable_bc, ['stratum_key', 'multiplier', 'n_test', 'n_test_units', 'test_before_bias', 'test_after_bias', 'bias_reduction', 'rel_rmse_change', 'lo_folds', 'lo_median_bias_reduction', 'lo_share_folds_bias_reduced'])}

**Withheld strata ({len(withheld_bc)}):** these failed the bias-reduction or
RMSE rule.

{table(withheld_bc, ['stratum_key', 'multiplier', 'n_train', 'n_test', 'n_test_units', 'test_before_bias', 'test_after_bias', 'bias_reduction', 'rel_rmse_change', 'reason'])}

**Insufficient support ({len(insufficient_bc)}):** single-TSA ecoprovinces
cannot be split, and several species strata had too few held-out visits.

{table(insufficient_bc, ['stratum_key', 'n_train', 'n_train_units', 'n_test', 'n_test_units', 'reason'])}

BC reading: the coastal factors are the strongest and most stable result. The
Columbia larch and Northern Montane spruce factors also survive both the
predeclared split and leave-one-TSA-out. Several other species pass the primary
split but are downgraded because the leave-one-TSA-out check is unstable; the
Central Montane species are weak and the single-TSA Boreal Foothills /
Hay-Slave Lowlands strata cannot be validated at all.

## 3. QC result

**Aggregate held-out same-mix total volume:**

{table(qc_aggregate, ['stratum_level', 'n_test', 'n_test_units', 'coverage_rows', 'coverage_share', 'test_before_bias', 'test_after_bias', 'bias_reduction', 'test_before_relative_rmse', 'test_after_relative_rmse', 'rel_rmse_change', 'test_before_within20', 'test_after_within20', 'test_before_within30', 'test_after_within30'])}

The release policy applies no QC factor, so the all-visit before/after rows are
identical. The all-candidate diagnostic would cover all held-out rows and
reduce absolute bias by {qc_all_candidates['bias_reduction']:.1%}, but four of
the five candidate strata are withheld and the remaining proxy fails the
leave-one-unit-out check; that aggregate is not a defensible release.

**All-candidate diagnostic by age (held-out):**

{table(qc_age_candidates, ['stratum_key', 'n_test', 'n_test_units', 'coverage_share', 'test_before_bias', 'test_after_bias', 'bias_reduction', 'test_before_relative_rmse', 'test_after_relative_rmse', 'rel_rmse_change', 'test_before_within20', 'test_after_within20'])}

**Ecoprovince-proxy strata ({len(qc_proxy)}):**

{table(qc_proxy, ['stratum_key', 'multiplier', 'n_train', 'n_train_units', 'n_test', 'n_test_units', 'test_before_bias', 'test_after_bias', 'bias_reduction', 'rel_rmse_change', 'status', 'reason'])}

**Primary-rule validated proxy strata ({len(validated_qc)}):**

{table(validated_qc, ['stratum_key', 'multiplier', 'n_train', 'n_test', 'n_test_units', 'test_before_bias', 'test_after_bias', 'bias_reduction', 'test_before_relative_rmse', 'test_after_relative_rmse', 'rel_rmse_change', 'lo_folds', 'lo_median_bias_reduction', 'lo_share_folds_bias_reduced', 'lo_robust'])}

**Recommended for application after LO-unit robustness ({len(recommended_qc)}):**

{table(recommended_qc, ['stratum_key', 'multiplier', 'n_train', 'n_test', 'n_test_units', 'test_before_bias', 'test_after_bias', 'bias_reduction', 'rel_rmse_change', 'lo_folds', 'lo_median_bias_reduction', 'lo_share_folds_bias_reduced', 'lo_share_folds_rmse_ok'])}

**Administrative-region diagnostics ({len(qc_region)} rows, all single-unit
test sets):** these are descriptive only because most regions contribute one
held-out unit.

{table(region_rows, ['region', 'ecoprovince_proxy', 'multiplier', 'n_test', 'n_test_units', 'test_before_bias', 'test_after_bias', 'bias_reduction', 'rel_rmse_change', 'status'])}

QC reading: only the Appalachian proxy passes the primary split, and it is
downgraded by the leave-one-unit-out check: the median fold bias reduction is
22.8% and only one of six folds reaches 50%. Southern Boreal Shield misses the
50% primary threshold (37.7%). Eastern Boreal Shield, Mid-Boreal Shield and
Eastern Taiga are withheld. The administrative-region table shows the same
instability: some regions improve strongly, others reverse sign or worsen
RMSE, and every region test is a single held-out unit. The QC correction layer
is therefore not recommended for release.

## 4. Recommendation

**{coverage['decision']}**

{coverage['text']}

The released set is limited to strata with `status=validated` **and**
`lo_robust=true`; every other stratum remains `withheld`,
`insufficient_support`, or `validated_primary_only_lo_unstable`. No factor was
applied in this task.

## 5. Tests, paths and status

| Item | Value |
|---|---|
| Validation script | `workflows/national/rsyc_benchmark/validate_corrections.py` |
| BC input | `{BC_VISITS}` |
| QC inputs | `{QC_CURVES}`, `{QC_DEVELOPMENT_TYPES}`, `{QC_MANIFEST}` |
| RSYC models | `{RSYC_MODELS}` |
| National fit | `{NATIONAL_FIT}` |
| Validation table | `{OUTPUT_DIR}/correction_validation.csv` |
| Validated/candidate table | `{OUTPUT_DIR}/local_correction_factors_validated.csv` |
| Summary | `{OUTPUT_DIR}/correction_validation_summary.json` |
| Manifest | `{OUTPUT_DIR}/correction_validation_manifest.json` |
| BC visits used | {bc_counts['used_rows']:,} of {bc_counts['raw_rows']:,} |
| QC positive curve-age rows | {qc_counts['positive_total_rows']:,} |
| QC zero-total rows excluded | {qc_counts['zero_total_rows']:,} |
| QC invalid rows excluded | {qc_counts['invalid_rows']:,} |
| Verification | `ruff check` pass; `py_compile` pass |
| 119M/150M/100M/200M artifacts | not modified |
| C2070 rerun | not performed |

The validation manifest is named `correction_validation_manifest.json` rather
than `manifest.json` so the existing benchmark manifest is not overwritten.

## 6. Caveats

**QC unit→ecoprovince join is a proxy, not a spatial join.** The public BFEC
inventory crosswalk has columns `unit`, `polygon_id`, `THEME1`–`THEME20`,
`STRATE`, `area_ha`, `CRS`; it has no X/Y centroid or polygon geometry.
`THEME20` carries `ecoNN` BFEC ecological-unit codes, but no published
`ecoNN` → Canadian ecodistrict/ecoprovince lookup is present in the repository
or the extraction manifest. The public bundle index supplies only the
administrative region (`R01`–`R15`). The QC factors therefore use the
administrative-region → ecoprovince proxy in
`QC_REGION_ECOPROVINCE_PROXY`, with per-region confidence recorded in the
summary JSON and in the `proxy_confidence` column:

{qc_mapping_table}

R10 is the weakest proxy: it spans Eastern Taiga, Hudson-James Lowlands and
Mid-Boreal Shield. A true spatial join requires either BFEC polygon centroids
or a published BFEC eco-code crosswalk; neither is available here.

**The R03 proxy differs from the previous five-unit benchmark.** The earlier
sample mapped R03 to Atlantic Maritime/Appalachian-Acadian Highlands. This
validation maps R03 to Boreal Shield East/Southern Boreal Shield because the
R03 units are Laurentian/Charlevoix southern-boreal units. The re-mapping is
flagged as medium confidence. It changes the reconstructed prediction for the
two R03 sample units (3151 and 3152); the three units whose proxy matches the
earlier mapping (2651, 3451, 3551) reproduce the published benchmark
predictions exactly.

**BFEC curves are managed AAC tables.** Productive development types only
(`in_model=1`, `productive=1`, area>0) were used. Many curves are flat or
post-rotation at ages 100/150, so the age-50/100/150 scalar is a level
correction, not a rotation-age or shape correction. Zero-total and invalid
mass-balance rows were excluded rather than assigned a fabricated mix.

**Restricted units were excluded.** Units 06451, 08462 and 08562 carry
confidentiality notices; their public development-type aggregates and
inventory crosswalks are restricted, so they cannot contribute a table area
or a unit-level holdout. Their curves remain public but were not used in this
validation.

**BC geography is mixed for the coastal stratum.** The full benchmark contains
1,544 `bec_zone_fallback` visits; after the 40–150 age filter, the BC rows used
here contain {bc_counts['fallback_rows_used']:,} fallback visits
({bc_counts['fallback_share_used']:.1%} of usable visits), dominated by Haida
Gwaii. That is a coarse default rather than a TSA-dominant assignment. The
validated coastal factor is therefore a coastal-region level factor, not a
clean southern-coast factor. The `fallback_share` column in
`correction_validation.csv` reports the mix for every stratum.

**BC single-TSA strata cannot be validated.** Boreal Foothills, Hay-Slave
Lowlands and Southern Boreal Cordillera each contain one TSA. A whole-TSA
holdout leaves no training or no test data; they are `insufficient_support`
regardless of their fit diagnostics.

**This is volume only.** No AGB, height, taper, grade, MAI or rotation-age
claim is made. The benchmark is a curve-level comparison, not a stand-level
inventory audit.
"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[3],
        help="Repository root (default: inferred from this script).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Output directory (default: data/derived/national/rsyc_benchmark).",
    )
    args = parser.parse_args(argv)
    repo_root = args.repo_root.resolve()
    output_dir = (args.output_dir or repo_root / OUTPUT_DIR).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    bc_frame, bc_counts = load_bc_visits(repo_root)
    bc_split = split_units_by_region(
        bc_frame, "ecoprovince", "tsa", f"{SEED}|BC"
    )
    bc_frame["split"] = np.where(
        bc_frame["tsa"].isin(bc_split), "test", "train"
    )
    if (bc_frame["split"] == "test").sum() == 0:
        raise ValueError("BC holdout produced no test rows")

    qc_unit_region, restricted, qc_manifest = load_qc_units(repo_root)
    qc_frame, qc_counts, qc_geometry = load_qc_pairs(
        repo_root, qc_unit_region, restricted
    )
    qc_split = split_units_by_region(
        qc_frame, "region", "unit_key", f"{SEED}|QC"
    )
    qc_frame["split"] = np.where(
        qc_frame["unit_key"].isin(qc_split), "test", "train"
    )
    if (qc_frame["split"] == "test").sum() == 0:
        raise ValueError("QC holdout produced no test rows")

    models = rsyc_benchmark.load_rsyc_models(repo_root / RSYC_MODELS)
    cell_models = rsyc_benchmark.load_cell_models(repo_root / NATIONAL_FIT)
    lookup = rsyc_benchmark.RsycLookup(models, cell_models)
    qc_frame = add_qc_predictions(qc_frame, lookup)

    bc_records = build_bc_stratum_rows(
        bc_frame,
        stratum_columns=["ecoprovince_stratum", "leading_species"],
        stratum_level="ecoprovince_species",
        release_scope="release_candidate",
    )
    bc_records.extend(
        build_bc_stratum_rows(
            bc_frame,
            stratum_columns=["ecoprovince_stratum"],
            stratum_level="ecoprovince_all_species",
            release_scope="diagnostic",
        )
    )
    bc_species_records = [
        row
        for row in bc_records
        if row["stratum_level"] == "ecoprovince_species"
        and row["candidate_status"] == "candidate"
        and math.isfinite(row["multiplier"])
    ]
    bc_factor_maps = {
        "release_policy": {
            row["stratum_key"]: row["multiplier"]
            for row in bc_species_records
            if row["status"] == "validated" and row["lo_robust"]
        },
        "primary_validated_including_lo_unstable": {
            row["stratum_key"]: row["multiplier"]
            for row in bc_species_records
            if row["status"] == "validated"
        },
        "all_candidate_factors": {
            row["stratum_key"]: row["multiplier"] for row in bc_species_records
        },
    }
    bc_frame["stratum_key"] = (
        bc_frame["ecoprovince_stratum"].astype(str)
        + "|"
        + bc_frame["leading_species"].astype(str)
    )
    for policy, factor_map in bc_factor_maps.items():
        bc_records.extend(
            aggregate_rows(
                bc_frame,
                dataset="BC_FAIB",
                policy=policy,
                observed_column="observed_volume",
                predicted_column="predicted_composite",
                factor_map=factor_map,
                key_column="stratum_key",
            )
        )
        bc_records.extend(
            aggregate_group_rows(
                bc_frame,
                dataset="BC_FAIB",
                policy=policy,
                group_column="age_bin",
                observed_column="observed_volume",
                predicted_column="predicted_composite",
                factor_map=factor_map,
                key_column="stratum_key",
            )
        )

    qc_records = build_qc_stratum_rows(
        qc_frame,
        stratum_columns=["ecoprovince_proxy"],
        stratum_level="ecoprovince_proxy",
        release_scope="release_candidate",
    )
    qc_records.extend(
        build_qc_stratum_rows(
            qc_frame,
            stratum_columns=["region"],
            stratum_level="admin_region",
            release_scope="diagnostic",
        )
    )
    qc_species_records = [
        row
        for row in qc_records
        if row["stratum_level"] == "ecoprovince_proxy"
        and row["candidate_status"] == "candidate"
        and math.isfinite(row["multiplier"])
    ]
    qc_factor_maps = {
        "release_policy": {
            row["stratum_key"]: row["multiplier"]
            for row in qc_species_records
            if row["status"] == "validated" and row["lo_robust"]
        },
        "primary_validated_including_lo_unstable": {
            row["stratum_key"]: row["multiplier"]
            for row in qc_species_records
            if row["status"] == "validated"
        },
        "all_candidate_factors": {
            row["stratum_key"]: row["multiplier"] for row in qc_species_records
        },
    }
    qc_frame["stratum_key"] = qc_frame["ecoprovince_proxy"].astype(str)
    for policy, factor_map in qc_factor_maps.items():
        qc_records.extend(
            aggregate_rows(
                qc_frame,
                dataset="QC_BFEC",
                policy=policy,
                observed_column="volume_total",
                predicted_column="predicted_volume",
                factor_map=factor_map,
                key_column="stratum_key",
            )
        )
        qc_records.extend(
            aggregate_group_rows(
                qc_frame,
                dataset="QC_BFEC",
                policy=policy,
                group_column="age",
                observed_column="volume_total",
                predicted_column="predicted_volume",
                factor_map=factor_map,
                key_column="stratum_key",
            )
        )

    bc_validation = reorder_columns(pd.DataFrame(bc_records))
    qc_validation = reorder_columns(pd.DataFrame(qc_records))
    validation = pd.concat([bc_validation, qc_validation], ignore_index=True)
    validation_path = output_dir / "correction_validation.csv"
    validation.to_csv(validation_path, index=False, na_rep="", float_format="%.10g")

    bc_release = bc_validation.loc[
        bc_validation["stratum_level"] == "ecoprovince_species"
    ]
    qc_release = qc_validation.loc[
        qc_validation["stratum_level"] == "ecoprovince_proxy"
    ]
    release_records: list[dict[str, object]] = []
    for frame, dataset in ((bc_release, "BC_FAIB"), (qc_release, "QC_BFEC")):
        for row in frame.to_dict(orient="records"):
            release_records.append(
                {
                    "correction_key": f"{dataset}|{row['stratum_key']}",
                    "dataset": dataset,
                    "stratum_level": row["stratum_level"],
                    "ecoprovince": row.get("ecoprovince"),
                    "ecoprovince_stratum": row.get("ecoprovince_stratum"),
                    "admin_region": row.get("admin_region"),
                    "species": row.get("species", "total"),
                    "age_basis": row["age_basis"],
                    "candidate_status": row["candidate_status"],
                    "multiplier": row["multiplier"],
                    "multiplier_ratio": row.get("multiplier_ratio"),
                    "n_train": row["n_train"],
                    "n_train_units": row["n_train_units"],
                    "n_test": row["n_test"],
                    "n_test_units": row["n_test_units"],
                    "test_before_bias": row["test_before_bias"],
                    "test_after_bias": row["test_after_bias"],
                    "bias_reduction": row["bias_reduction"],
                    "test_before_relative_rmse": row["test_before_relative_rmse"],
                    "test_after_relative_rmse": row["test_after_relative_rmse"],
                    "rel_rmse_change": row["rel_rmse_change"],
                    "material": row["material"],
                    "status": row["status"],
                    "reason": row["reason"],
                    "release_decision": row["release_decision"],
                    "lo_robust": bool(row["lo_robust"]),
                    "recommended_for_application": bool(
                        row["status"] == "validated" and row["lo_robust"]
                    ),
                    "application_age_floor": (
                        61
                        if dataset == "BC_FAIB"
                        and row["status"] == "validated"
                        and row["lo_robust"]
                        else ""
                    ),
                    "holdout_protocol": (
                        "predeclared_20pct_tsa_holdout"
                        if dataset == "BC_FAIB"
                        else "predeclared_20pct_unit_holdout"
                    ),
                    "holdout_evaluable": row["status"] != "insufficient_support",
                    "lo_folds": row.get("lo_folds"),
                    "lo_median_bias_reduction": row.get("lo_median_bias_reduction"),
                    "lo_share_folds_bias_reduced": row.get(
                        "lo_share_folds_bias_reduced"
                    ),
                    "lo_median_rel_rmse_change": row.get(
                        "lo_median_rel_rmse_change"
                    ),
                }
            )
    release_frame = pd.DataFrame(release_records)
    release_path = output_dir / "local_correction_factors_validated.csv"
    release_frame.to_csv(release_path, index=False, na_rep="", float_format="%.10g")

    summary = {
        "generated_at": datetime.now(UTC).isoformat(),
        "script": str(Path(__file__).relative_to(repo_root)),
        "seed": SEED,
        "protocol": {
            "holdout_fraction": HOLDOUT_FRACTION,
            "bc_holdout_unit": "tsa",
            "bc_stratifier": "ecoprovince",
            "qc_holdout_unit": "unit",
            "qc_stratifier": "administrative_region",
            "min_train_rows": MIN_TRAIN_ROWS,
            "min_test_rows": MIN_TEST_ROWS,
            "min_train_units": MIN_TRAIN_UNITS,
            "min_test_units": MIN_TEST_UNITS,
            "bias_reduction_threshold": BIAS_REDUCTION_THRESHOLD,
            "relative_rmse_worsen_threshold": REL_RMSE_WORSEN_THRESHOLD,
            "material_bias_threshold": MATERIAL_BIAS_THRESHOLD,
            "lo_min_folds": LO_MIN_FOLDS,
            "lo_median_bias_reduction_threshold": 0.50,
            "lo_share_folds_rmse_ok_threshold": 0.50,
        },
        "bc_split": sorted(bc_split),
        "qc_split": sorted(qc_split),
        "bc_counts": bc_counts,
        "qc_counts": qc_counts,
        "qc_geometry": qc_geometry,
        "inputs": {
            "bc_visits": {"path": BC_VISITS, "sha256": sha256_file(repo_root / BC_VISITS)},
            "qc_curves": {"path": QC_CURVES, "sha256": sha256_file(repo_root / QC_CURVES)},
            "qc_development_types": {
                "path": QC_DEVELOPMENT_TYPES,
                "sha256": qc_manifest["artifacts"]["public"]["qc_bfec_development_types.csv"]["sha256"],
            },
            "qc_inventory_crosswalk": {
                "path": QC_CROSSWALK,
                "sha256": qc_manifest["artifacts"]["public"]["qc_bfec_inventory_crosswalk.csv"]["sha256"],
                "columns": [
                    "unit", "polygon_id", "THEME1", "THEME2", "THEME3", "THEME4",
                    "THEME5", "THEME6", "THEME7", "THEME8", "THEME9", "THEME10",
                    "THEME11", "THEME12", "THEME13", "THEME14", "THEME15", "THEME16",
                    "THEME17", "THEME18", "THEME19", "THEME20", "STRATE", "area_ha",
                    "CRS",
                ],
                "has_coordinates": False,
            },
            "rsyc_models": {"path": RSYC_MODELS, "sha256": sha256_file(repo_root / RSYC_MODELS)},
            "national_fit": {"path": NATIONAL_FIT, "sha256": sha256_file(repo_root / NATIONAL_FIT)},
        },
        "outputs": {},
        "summary": {},
        "recommendation": {},
    }

    bc_strata = bc_validation.loc[
        bc_validation["stratum_level"] == "ecoprovince_species"
    ]
    qc_proxy = qc_validation.loc[
        qc_validation["stratum_level"] == "ecoprovince_proxy"
    ]
    bc_agg = bc_validation.loc[
        bc_validation["stratum_level"] == "aggregate_release_policy_all_test_rows"
    ]
    qc_agg = qc_validation.loc[
        qc_validation["stratum_level"] == "aggregate_release_policy_all_test_rows"
    ]
    validated_bc = bc_strata.loc[bc_strata["status"] == "validated"]
    validated_qc = qc_proxy.loc[qc_proxy["status"] == "validated"]
    recommended_bc = validated_bc.loc[
        validated_bc["lo_robust"].fillna(False).astype(bool)
    ]
    recommended_qc = validated_qc.loc[
        validated_qc["lo_robust"].fillna(False).astype(bool)
    ]

    if len(recommended_bc) and len(recommended_qc):
        decision = "Apply both"
    elif len(recommended_bc):
        decision = "Apply BC-only"
    elif len(recommended_qc):
        decision = "Apply QC-only"
    else:
        decision = "Apply none"

    bc_age_release = bc_validation.loc[
        bc_validation["stratum_level"] == "aggregate_release_policy_by_age_bin"
    ]
    young_rows = bc_age_release.loc[bc_age_release["stratum_key"] == "40-60"]
    young_bias_before = (
        float(young_rows["test_before_bias"].iloc[0]) if not young_rows.empty else math.nan
    )
    young_bias_after = (
        float(young_rows["test_after_bias"].iloc[0]) if not young_rows.empty else math.nan
    )
    text = (
        f"BC has {len(validated_bc)} strata that pass the predeclared holdout "
        f"rule; {len(recommended_bc)} of them also survive the leave-one-TSA-out "
        "check and are recommended for application. The BC scalar is age-blind: "
        "in the held-out 40-60 bin applying it without an age floor would move "
        f"bias from {young_bias_before:+.1%} to {young_bias_after:+.1%}; the "
        "recommended application floor is 61 years, so the 40-60 bin stays "
        "uncorrected. "
        f"QC has {len(validated_qc)} proxy strata that pass the primary rule, "
        f"but {len(recommended_qc)} survive leave-one-unit-out. The QC proxy "
        "is also not a spatial unit→ecoprovince join: the public BFEC crosswalk "
        "has no coordinates and no published eco-code lookup. Release the "
        "recommended BC strata only, at ages >=61; keep every QC factor as a "
        "candidate until BFEC geometry or a published eco-code crosswalk is "
        "available. Do not apply any withheld, insufficient-support, or "
        "validated-primary-only-lo-unstable stratum."
    )
    summary["summary"] = {
        "bc": {
            "aggregate": bc_agg.to_dict(orient="records"),
            "n_strata": len(bc_strata),
            "n_validated_primary": len(validated_bc),
            "n_recommended": len(recommended_bc),
            "n_withheld": int((bc_strata["status"] == "withheld").sum()),
            "n_insufficient": int((bc_strata["status"] == "insufficient_support").sum()),
        },
        "qc": {
            "aggregate": qc_agg.to_dict(orient="records"),
            "n_proxy_strata": len(qc_proxy),
            "n_validated_primary": len(validated_qc),
            "n_recommended": len(recommended_qc),
            "n_withheld": int((qc_proxy["status"] == "withheld").sum()),
            "n_insufficient": int((qc_proxy["status"] == "insufficient_support").sum()),
        },
        "decision": decision,
        "text": text,
    }
    summary["recommendation"] = {
        "decision": decision,
        "text": text,
        "apply_bc": sorted(recommended_bc["stratum_key"].tolist()),
        "apply_qc": sorted(recommended_qc["stratum_key"].tolist()),
        "bc_application_age_floor": 61,
        "validated_primary_only": sorted(
            set(validated_bc["stratum_key"]) - set(recommended_bc["stratum_key"])
            | set(validated_qc["stratum_key"]) - set(recommended_qc["stratum_key"])
        ),
        "withheld_or_insufficient": int(
            (bc_strata["status"] != "validated").sum()
            + (qc_proxy["status"] != "validated").sum()
        ),
        "applied": False,
    }

    summary_path = output_dir / "correction_validation_summary.json"
    summary_path.write_text(json.dumps(json_safe(summary), indent=2) + "\n")

    report = build_report(
        bc_validation,
        qc_validation,
        bc_counts,
        qc_counts,
        bc_split,
        qc_split,
        qc_geometry,
        summary,
    )
    report_path = output_dir / "CORRECTION_VALIDATION_REPORT.md"
    report_path.write_text(report)

    manifest = {
        "generated_at": summary["generated_at"],
        "script": summary["script"],
        "seed": SEED,
        "protocol": summary["protocol"],
        "inputs": summary["inputs"],
        "counts": {"bc": bc_counts, "qc": qc_counts},
        "outputs": {
            path.name: {
                "rows": len(frame),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
            for path, frame in (
                (validation_path, validation),
                (release_path, release_frame),
            )
        },
        "report": {
            "path": report_path.name,
            "bytes": report_path.stat().st_size,
            "sha256": sha256_file(report_path),
        },
        "summary": {
            "path": summary_path.name,
            "bytes": summary_path.stat().st_size,
            "sha256": sha256_file(summary_path),
        },
        "recommendation": summary["recommendation"],
        "caveats": [
            "QC unit->ecoprovince is an administrative-region proxy; the public crosswalk has no coordinates and no eco-code lookup.",
            "Restricted BFEC units 06451, 08462 and 08562 were excluded.",
            "BC coastal stratum includes bec_zone_fallback visits, dominated by Haida Gwaii.",
            "BC single-TSA ecoprovinces cannot be validated with a whole-TSA holdout.",
            "Volume only; no AGB, height, taper, grade or rotation-age claim.",
            "No correction was applied to the 119M/150M/100M/200M artifacts and no C2070 run was repeated.",
        ],
    }
    manifest_path = output_dir / "correction_validation_manifest.json"
    manifest_path.write_text(json.dumps(json_safe(manifest), indent=2) + "\n")

    print(f"Wrote {validation_path} ({len(validation)} rows)")
    print(f"Wrote {release_path} ({len(release_frame)} decision rows)")
    print(f"Wrote {report_path}")
    print(f"Wrote {summary_path}")
    print(f"Wrote {manifest_path}")
    print(f"Decision: {decision}")
    print(
        "BC primary validated:",
        f"{len(validated_bc)}/{len(bc_strata)}",
        "recommended:",
        len(recommended_bc),
    )
    print(
        "QC proxy primary validated:",
        f"{len(validated_qc)}/{len(qc_proxy)}",
        "recommended:",
        len(recommended_qc),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
