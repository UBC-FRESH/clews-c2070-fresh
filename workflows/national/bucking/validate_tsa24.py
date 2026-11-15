"""TSA24 bucking pilot: stratified sample, bucking, validation, and report.

Run from the repository root with nemora on ``PYTHONPATH``::

    PYTHONPATH="${NEMORA_ROOT:?set NEMORA_ROOT}/src" \
      .venv/bin/python -m workflows.national.bucking.validate_tsa24 \
      --out-dir data/derived/national/bucking/tsa24_pilot

The pilot is deliberately bounded.  It uses a documented 20-plot, 300-tree
stratified sample and never presents the result as the pre-registered
three-way validation gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from itertools import pairwise
import subprocess
import sys
import time
import warnings
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

from .bucking import (
    BUCKER_BALANCE_ABS_TOL,
    BUCKER_BALANCE_REL_TOL,
    BuckingBatch,
    buck_trees,
    load_grade_rules,
    load_price_profile,
    logs_frame,
    residue_frame,
)
from .schema import BuckedLog, TreeRecord
from .taper import (
    TaperRegistry,
    default_kozak_parameter_path,
    default_nemora_root,
)
from .tree_lists import (
    DEFAULT_ALLOCATION,
    FaibTreeList,
    load_faib_psp_tree_list,
    select_stratified_sample,
)
from .validate_tsa24_btc import (
    DEFAULT_CHIP_YIELD,
    DEFAULT_MOISTURE_GREEN,
    DEFAULT_MOISTURE_SEASONED,
    DEFAULT_RECOVERY_FACTOR,
    btc_volume_benchmark,
    harvest_residue_ledger,
    load_basic_density_table,
)
from .validate_tsa24_grades import benchmark_grade_residue

__all__ = ["main"]

PILOT_NAME = "tsa24_bucking_phase0"
TSA_NUMBER = 24
TSA_NAME = "Prince George"
BASIC_DENSITY_ODT_M3 = 0.45
MOISTURE_GREEN = DEFAULT_MOISTURE_GREEN
MOISTURE_SEASONED = DEFAULT_MOISTURE_SEASONED
CHIP_YIELD = DEFAULT_CHIP_YIELD
HARVEST_RECOVERY_FACTOR = DEFAULT_RECOVERY_FACTOR
CONSERVATION_REL_TOL = 1e-5
CONSERVATION_ABS_TOL = 1e-7


def default_femic_root() -> Path:
    configured = os.environ.get("FEMIC_TSA24_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    return default_nemora_root().parent / "femic" / "external" / "femic-tsa24-instance"


def default_price_profile() -> Path:
    configured = os.environ.get("BC_LOG_PRICE_PROFILE")
    if configured:
        return Path(configured).expanduser().resolve()
    return default_nemora_root() / "config" / "log_prices" / "bc_interior_2026q3.yaml"


def default_species_crosswalk() -> Path:
    configured = os.environ.get("BC_PSP_SPECIES_CROSSWALK")
    if configured:
        return Path(configured).expanduser().resolve()
    return default_nemora_root() / "config" / "species" / "bc_psp_to_group.csv"


def default_rules_path() -> Path:
    return Path(__file__).resolve().parent / "rules" / "bc_interior.yaml"


def default_basic_density_path() -> Path:
    return Path(__file__).resolve().parent / "rules" / "bc_basic_density.csv"


class TimeboxExceeded(RuntimeError):
    pass


class Deadline:
    def __init__(self, seconds: float | None) -> None:
        self.seconds = seconds
        self.started = time.monotonic()
        self.deadline = self.started + seconds if seconds and seconds > 0 else None

    def check(self, label: str) -> None:
        if self.deadline is not None and time.monotonic() > self.deadline:
            raise TimeboxExceeded(f"timebox exceeded before {label}")

    def expired(self) -> bool:
        return self.deadline is not None and time.monotonic() > self.deadline

    def remaining(self) -> float | None:
        if self.deadline is None:
            return None
        return max(0.0, self.deadline - time.monotonic())


def assert_no_unexpected_deletions(repo_root: Path) -> None:
    """Fail closed on any unexpected ``D`` in parent or data status."""
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
            raise RuntimeError(
                f"git status failed in {repository}: {result.stderr.strip()}"
            )
        for line in result.stdout.splitlines():
            if len(line) >= 2 and (line[0] == "D" or line[1] == "D"):
                raise RuntimeError(
                    f"unexpected deletion in {repository}: {line!r}; halting fail-closed"
                )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _hash_record(label: str, path: Path, logical_path: str) -> dict[str, Any]:
    if not path.exists():
        return {
            "label": label,
            "path": logical_path,
            "status": "missing",
        }
    return {
        "label": label,
        "path": logical_path,
        "bytes": int(path.stat().st_size),
        "sha256": _sha256(path),
    }


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


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


def _format_shares(
    shares: Mapping[str, float] | None,
    keys: Sequence[str] | None = None,
) -> str:
    if not shares:
        return "n/a"
    selected = list(keys) if keys is not None else list(shares)
    parts = []
    for key in selected:
        value = shares.get(key)
        if value is None:
            continue
        parts.append(f"{key}={_fmt(float(value) * 100, 1)}%")
    return ", ".join(parts) if parts else "n/a"


def _markdown_table(frame: pd.DataFrame, digits: int = 2) -> str:
    if frame.empty:
        return "_No rows._"
    columns = list(frame.columns)
    lines = [
        "| " + " | ".join(str(column) for column in columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for row in frame.itertuples(index=False):
        cells = []
        for value in row:
            if isinstance(value, (float, np.floating)):
                cells.append(_fmt(value, digits))
            elif value is None or (isinstance(value, float) and math.isnan(value)):
                cells.append("")
            else:
                cells.append(str(value))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _load_inputs(args: argparse.Namespace, registry: TaperRegistry) -> tuple[FaibTreeList, dict[str, Any]]:
    faib = load_faib_psp_tree_list(
        Path(args.data_root),
        tsa_name=TSA_NAME,
        species_crosswalk_path=Path(args.species_crosswalk),
        supported_species=registry.supported_species,
    )
    return faib, faib.stats


def _select_sample(faib: FaibTreeList, registry: TaperRegistry, args: argparse.Namespace):
    return select_stratified_sample(
        faib.trees,
        faib.plots,
        max_plots=args.max_plots,
        max_trees_per_plot=args.max_trees_per_plot,
        seed=args.seed,
        supported_species=registry.supported_species,
        allocation=DEFAULT_ALLOCATION,
    )


def _build_tree_summary(
    sample_trees: Sequence[TreeRecord],
    batch: BuckingBatch,
) -> pd.DataFrame:
    logs_by_tree: dict[str, list[BuckedLog]] = {}
    for log in batch.logs:
        logs_by_tree.setdefault(log.tree_id, []).append(log)
    residue_by_tree = {ledger.tree_id: ledger for ledger in batch.residue}

    rows: list[dict[str, Any]] = []
    for tree in sample_trees:
        ledger = residue_by_tree.get(tree.tree_id)
        if ledger is None:
            continue
        logs = logs_by_tree.get(tree.tree_id, [])
        recovered = sum(log.volume_m3 for log in logs)
        value = sum(log.value_cad or 0.0 for log in logs)
        priced_volume = sum(log.volume_m3 for log in logs if log.price_cad_m3 is not None)
        residue_volume = ledger.residue_volume_m3
        residue_odt = ledger.residue_odt
        green_45 = residue_odt / (1.0 - MOISTURE_GREEN)
        green_30 = residue_odt / (1.0 - MOISTURE_SEASONED)
        row = {
            "tree_id": tree.tree_id,
            "site_id": tree.site_id,
            "plot_id": tree.plot_id,
            "species": tree.species,
            "species_group": tree.species_group,
            "bec_zone": tree.bec_zone,
            "dbh_cm": tree.dbh_cm,
            "height_m": tree.height_m,
            "expansion_factor": tree.expansion_factor,
            "age": tree.age,
            "stand_age": tree.stand_age,
            "site_index": tree.site_index,
            "latitude": tree.latitude,
            "longitude": tree.longitude,
            "n_logs": len(logs),
            "taper_total_m3": ledger.taper_total_m3,
            "recovered_log_m3": recovered,
            "residue_m3": residue_volume,
            "residue_odt": residue_odt,
            "green_tonnes_45": green_45,
            "green_tonnes_30": green_30,
            "chip_green_tonnes": green_45 * CHIP_YIELD,
            "priced_log_m3": priced_volume,
            "value_cad": value,
            "observed_merchantable_m3": tree.observed_merchantable_m3 or 0.0,
            "observed_whole_stem_m3": tree.observed_whole_stem_m3 or 0.0,
            "bucker_residue_m3": ledger.bucker_residue_m3,
            "bucker_balance_error_m3": ledger.bucker_balance_error_m3,
        }
        for name in (
            "taper_total_m3",
            "recovered_log_m3",
            "residue_m3",
            "residue_odt",
            "green_tonnes_45",
            "green_tonnes_30",
            "chip_green_tonnes",
            "priced_log_m3",
            "value_cad",
            "observed_merchantable_m3",
            "observed_whole_stem_m3",
        ):
            row[f"{name}_ha"] = row[name] * tree.expansion_factor
        rows.append(row)
    return pd.DataFrame(rows)


def _conservation_summary(tree_summary: pd.DataFrame) -> dict[str, Any]:
    if tree_summary.empty:
        return {"pass": False, "reason": "no bucked trees"}
    total = tree_summary["taper_total_m3"].astype(float)
    recovered = tree_summary["recovered_log_m3"].astype(float)
    residue = tree_summary["residue_m3"].astype(float)
    combined = recovered + residue
    absolute = (combined - total).abs()
    relative = absolute / total.where(total > 0)
    with_logs = tree_summary["n_logs"] > 0
    no_logs = ~with_logs
    bucker_error = tree_summary["bucker_balance_error_m3"].astype(float)
    bucker_total = tree_summary["taper_total_m3"].astype(float)
    bucker_relative = bucker_error / bucker_total.where(bucker_total > 0)
    ledger_tolerance = CONSERVATION_ABS_TOL + CONSERVATION_REL_TOL * total
    pass_ledger = bool((absolute <= ledger_tolerance).all())
    bucker_tolerance = BUCKER_BALANCE_ABS_TOL + BUCKER_BALANCE_REL_TOL * bucker_total
    pass_bucker = bool((bucker_error <= bucker_tolerance).all())
    return {
        "tolerance_absolute_m3": CONSERVATION_ABS_TOL,
        "tolerance_relative": CONSERVATION_REL_TOL,
        "bucker_tolerance_absolute_m3": BUCKER_BALANCE_ABS_TOL,
        "bucker_tolerance_relative": BUCKER_BALANCE_REL_TOL,
        "n_trees": int(len(tree_summary)),
        "n_trees_with_logs": int(with_logs.sum()),
        "n_trees_without_logs": int(no_logs.sum()),
        "total_taper_m3": float(total.sum()),
        "recovered_log_m3": float(recovered.sum()),
        "residue_m3": float(residue.sum()),
        "recovered_plus_residue_m3": float(combined.sum()),
        "residue_volume_share": float(residue.sum() / total.sum()) if total.sum() > 0 else None,
        "max_absolute_balance_m3": float(absolute.max()),
        "max_relative_balance": float(relative.max()) if relative.notna().any() else None,
        "pass": pass_ledger,
        "bucker_internal_check": {
            "n_trees": int(len(tree_summary)),
            "max_absolute_balance_m3": float(bucker_error.max()) if not bucker_error.empty else None,
            "max_relative_balance": (
                float(bucker_relative.max()) if bucker_relative.notna().any() else None
            ),
            "pass": pass_bucker,
            "note": (
                "The reference bucker returns the full stem as residue when no log is "
                "feasible; the ledger independently sets residue = taper total - recovered."
            ),
        },
        "no_log_trees": int(no_logs.sum()),
        "no_log_trees_with_bucker_residue_equal_total": int(
            (
                no_logs
                & np.isclose(
                    tree_summary["bucker_residue_m3"].astype(float),
                    tree_summary["taper_total_m3"].astype(float),
                    rtol=1e-9,
                    atol=1e-9,
                )
            ).sum()
        ),
    }


def _plot_summary(
    tree_summary: pd.DataFrame,
    faib: FaibTreeList,
    sample_plot_ids: set[str],
    supported_species: Iterable[str],
) -> pd.DataFrame:
    if tree_summary.empty:
        return pd.DataFrame()
    grouped = tree_summary.groupby("plot_id", dropna=False)
    rows: list[dict[str, Any]] = []
    supported = set(supported_species)
    all_plot_trees = [
        tree
        for tree in faib.trees
        if tree.plot_id in sample_plot_ids and tree.species in supported
    ]
    all_by_plot: dict[str, list[TreeRecord]] = {}
    for tree in all_plot_trees:
        all_by_plot.setdefault(str(tree.plot_id), []).append(tree)
    plot_meta = faib.plots.set_index("plot_id")

    for plot_id, group in grouped:
        first = group.iloc[0]
        all_trees = all_by_plot.get(str(plot_id), [])
        meta = plot_meta.loc[str(plot_id)] if str(plot_id) in plot_meta.index else None
        leading_species = (
            str(meta["leading_species"]) if meta is not None else None
        )
        observed_all = sum(
            (tree.observed_merchantable_m3 or 0.0) * tree.expansion_factor for tree in all_trees
        )
        observed_whole_all = sum(
            (tree.observed_whole_stem_m3 or 0.0) * tree.expansion_factor for tree in all_trees
        )
        observed_sampled = float(group["observed_merchantable_m3_ha"].sum())
        bucked = float(group["recovered_log_m3_ha"].sum())
        taper = float(group["taper_total_m3_ha"].sum())
        residue = float(group["residue_m3_ha"].sum())
        residue_odt = float(group["residue_odt_ha"].sum())
        value_sample = float(group["value_cad_ha"].sum())
        ratio_est = value_sample * (observed_all / observed_sampled) if observed_sampled > 0 else None
        bucked_ratio_est = bucked * (observed_all / observed_sampled) if observed_sampled > 0 else None
        row = {
            "plot_id": str(plot_id),
            "site_id": str(first["site_id"]),
            "bec_zone": str(first["bec_zone"]),
            "leading_species": leading_species,
            "stand_age": meta.get("stand_age") if meta is not None else first.get("stand_age"),
            "site_index": meta.get("site_index") if meta is not None else first.get("site_index"),
            "latitude": meta.get("latitude") if meta is not None else first.get("latitude"),
            "longitude": meta.get("longitude") if meta is not None else first.get("longitude"),
            "n_live_supported_all": (
                int(meta["n_live_supported"]) if meta is not None else len(all_trees)
            ),
            "n_sampled": int(len(group)),
            "observed_merchantable_all_m3ha": observed_all,
            "observed_whole_stem_all_m3ha": observed_whole_all,
            "observed_merchantable_sampled_m3ha": observed_sampled,
            "taper_total_m3ha": taper,
            "bucked_log_m3ha": bucked,
            "bucked_log_m3ha_ratio_est": bucked_ratio_est,
            "residue_m3ha": residue,
            "residue_odt_ha": residue_odt,
            "green_tonnes_45_ha": residue_odt / (1.0 - MOISTURE_GREEN),
            "green_tonnes_30_ha": residue_odt / (1.0 - MOISTURE_SEASONED),
            "chip_green_tonnes_ha": residue_odt / (1.0 - MOISTURE_GREEN) * CHIP_YIELD,
            "value_cad_ha_sample": value_sample,
            "value_cad_ha_ratio_est": ratio_est,
            "bias_m3ha": bucked - observed_sampled,
            "ratio_bucked_observed": (bucked / observed_sampled) if observed_sampled > 0 else None,
            "bias_all_m3ha": (bucked * observed_all / observed_sampled - observed_all)
            if observed_sampled > 0
            else None,
        }
        rows.append(row)
    frame = pd.DataFrame(rows).sort_values("plot_id").reset_index(drop=True)
    return frame


def _psp_metrics(plot_summary: pd.DataFrame) -> dict[str, Any]:
    if plot_summary.empty:
        return {"status": "no_plots"}
    observed = plot_summary["observed_merchantable_sampled_m3ha"].astype(float)
    bucked = plot_summary["bucked_log_m3ha"].astype(float)
    bias = bucked - observed
    valid = observed > 0
    ratio = bucked[valid] / observed[valid]
    relative_bias = (
        float(bias.sum() / observed.sum()) if observed.sum() > 0 else None
    )
    all_observed = plot_summary["observed_merchantable_all_m3ha"].astype(float)
    all_bias = plot_summary["bias_all_m3ha"].dropna().astype(float)
    return {
        "n_plots": int(len(plot_summary)),
        "n_plots_observed_positive": int(valid.sum()),
        "observed_merchantable_m3ha_sum": float(observed.sum()),
        "bucked_log_m3ha_sum": float(bucked.sum()),
        "mean_bias_m3ha": float(bias.mean()),
        "mae_m3ha": float(bias.abs().mean()),
        "rmse_m3ha": float(np.sqrt(np.mean(np.square(bias)))),
        "relative_bias": relative_bias,
        "mean_ratio_bucked_observed": float(ratio.mean()) if not ratio.empty else None,
        "median_ratio_bucked_observed": float(ratio.median()) if not ratio.empty else None,
        "observed_all_m3ha_sum": float(all_observed.sum()),
        "mean_bias_all_m3ha": float(all_bias.mean()) if not all_bias.empty else None,
        "mae_all_m3ha": float(all_bias.abs().mean()) if not all_bias.empty else None,
        "note": (
            "Primary comparison is paired sampled trees: observed VOL_MER*PHF_TREE "
            "versus bucked log volume*PHF_TREE. The *_all columns use all live "
            "supported trees in each selected plot on the observed side and a "
            "ratio-expanded bucked estimate; that is a secondary estimate."
        ),
    }


def _sort_value_summary(
    logs: pd.DataFrame,
    plot_summary: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    if logs.empty:
        return pd.DataFrame(), {"status": "no_logs"}
    logs = logs.copy()
    logs["value_cad"] = logs["value_cad"].fillna(0.0)
    logs["priced"] = logs["price_cad_m3"].notna()
    grouped = (
        logs.groupby(["sort_code", "product", "species_market_group"], dropna=False)
        .agg(
            n_logs=("volume_m3", "size"),
            volume_m3=("volume_m3", "sum"),
            priced_volume_m3=("volume_m3", lambda values: 0.0),
            value_cad=("value_cad", "sum"),
        )
        .reset_index()
    )
    priced = (
        logs[logs["priced"]]
        .groupby(["sort_code", "product", "species_market_group"], dropna=False)["volume_m3"]
        .sum()
        .rename("priced_volume_m3")
        .reset_index()
    )
    grouped = grouped.drop(columns=["priced_volume_m3"]).merge(
        priced, on=["sort_code", "product", "species_market_group"], how="left"
    )
    grouped["priced_volume_m3"] = grouped["priced_volume_m3"].fillna(0.0)
    grouped["unpriced_volume_m3"] = grouped["volume_m3"] - grouped["priced_volume_m3"]
    grouped["volume_share"] = grouped["volume_m3"] / grouped["volume_m3"].sum()
    grouped["value_per_m3_priced"] = grouped.apply(
        lambda row: row["value_cad"] / row["priced_volume_m3"]
        if row["priced_volume_m3"] > 0
        else None,
        axis=1,
    )
    grouped = grouped.sort_values(["volume_m3", "sort_code"], ascending=[False, True])
    total_volume = float(logs["volume_m3"].sum())
    priced_volume = float(logs.loc[logs["priced"], "volume_m3"].sum())
    total_value = float(logs["value_cad"].sum())
    summary = {
        "n_logs": int(len(logs)),
        "total_log_volume_m3": total_volume,
        "priced_volume_m3": priced_volume,
        "unpriced_volume_m3": total_volume - priced_volume,
        "unpriced_volume_share": (total_volume - priced_volume) / total_volume
        if total_volume > 0
        else None,
        "total_value_cad": total_value,
        "value_per_m3_priced": total_value / priced_volume if priced_volume > 0 else None,
        "value_per_m3_all_logs": total_value / total_volume if total_volume > 0 else None,
        "mean_value_cad_ha_sample": float(plot_summary["value_cad_ha_sample"].mean())
        if not plot_summary.empty
        else None,
        "mean_value_cad_ha_ratio_est": float(
            plot_summary["value_cad_ha_ratio_est"].dropna().mean()
        )
        if not plot_summary.empty and plot_summary["value_cad_ha_ratio_est"].notna().any()
        else None,
        "by_product": (
            logs.groupby("product", dropna=False)
            .agg(n_logs=("volume_m3", "size"), volume_m3=("volume_m3", "sum"), value_cad=("value_cad", "sum"))
            .reset_index()
            .sort_values("volume_m3", ascending=False)
            .to_dict(orient="records")
        ),
        "by_sort": grouped.head(20).to_dict(orient="records"),
        "by_market_group": (
            logs.groupby("species_market_group", dropna=False)
            .agg(n_logs=("volume_m3", "size"), volume_m3=("volume_m3", "sum"), value_cad=("value_cad", "sum"))
            .reset_index()
            .sort_values("volume_m3", ascending=False)
            .to_dict(orient="records")
        ),
    }
    return grouped, summary


def _residue_summary(tree_summary: pd.DataFrame, plot_summary: pd.DataFrame) -> dict[str, Any]:
    if tree_summary.empty:
        return {"status": "no_residue"}
    total_volume = float(tree_summary["residue_m3"].sum())
    total_odt = float(tree_summary["residue_odt"].sum())
    total_green_45 = float(tree_summary["green_tonnes_45"].sum())
    total_green_30 = float(tree_summary["green_tonnes_30"].sum())
    total_chip = float(tree_summary["chip_green_tonnes"].sum())
    return {
        "n_trees": int(len(tree_summary)),
        "residue_volume_m3": total_volume,
        "residue_odt": total_odt,
        "green_tonnes_45": total_green_45,
        "green_tonnes_30": total_green_30,
        "chip_green_tonnes_45": total_chip,
        "mean_residue_m3ha": float(plot_summary["residue_m3ha"].mean())
        if not plot_summary.empty
        else None,
        "mean_residue_odt_ha": float(plot_summary["residue_odt_ha"].mean())
        if not plot_summary.empty
        else None,
        "mean_green_tonnes_45_ha": float(plot_summary["green_tonnes_45_ha"].mean())
        if not plot_summary.empty
        else None,
        "mean_green_tonnes_30_ha": float(plot_summary["green_tonnes_30_ha"].mean())
        if not plot_summary.empty
        else None,
        "mean_chip_green_tonnes_ha": float(plot_summary["chip_green_tonnes_ha"].mean())
        if not plot_summary.empty
        else None,
        "assumptions": {
            "basic_density_odt_m3": BASIC_DENSITY_ODT_M3,
            "basic_density_source": "default assumption; no species-specific BC table applied",
            "moisture_green_wet_basis": MOISTURE_GREEN,
            "moisture_seasoned_wet_basis": MOISTURE_SEASONED,
            "chip_yield_fraction": CHIP_YIELD,
        },
    }


def _vdyp_stratum(bec_zone: str, species: str) -> str | None:
    bec = str(bec_zone).upper()
    code = str(species).upper()
    if bec == "SBS":
        if code in {"SW", "SE", "SB", "S"}:
            return "SBS_SX"
        if code == "PL":
            return "SBS_PLI"
        if code == "BL":
            return "SBS_BL"
        if code == "FD":
            return "SBS_FDI"
        if code == "AT":
            return "SBS_AT"
        return None
    if bec == "ESSF":
        if code == "BL":
            return "ESSF_BL"
        if code in {"SE", "SW", "SB", "S"}:
            return "ESSF_SE"
        return None
    return None


def _assign_si_levels(plots: pd.DataFrame, stratum_column: str) -> pd.DataFrame:
    frame = plots.copy()
    frame["si_level"] = None
    for _stratum, group in frame.groupby(stratum_column, dropna=True):
        valid = group["site_index"].dropna().astype(float)
        if len(valid) < 3:
            continue
        lower, upper = valid.quantile([1.0 / 3.0, 2.0 / 3.0])
        for index, value in group["site_index"].items():
            if pd.isna(value):
                continue
            if value <= lower:
                level = "L"
            elif value <= upper:
                level = "M"
            else:
                level = "H"
            frame.at[index, "si_level"] = level
    return frame


_SPECIES_TOKEN: Mapping[str, str] = {
    "SW": "SX",
    "SE": "SX",
    "SB": "SX",
    "S": "SX",
    "SX": "SX",
    "PL": "PLI",
    "PLI": "PLI",
    "PLC": "PLI",
    "FD": "FDI",
    "FDI": "FDI",
    "AT": "AT",
    "BL": "BL",
    "CW": "CW",
    "HW": "HWI",
    "HWI": "HWI",
}

_SI_SITE_INDEX_COLUMNS: Mapping[str, str] = {
    "SW": "sw_si",
    "SX": "sw_si",
    "S": "sw_si",
    "SB": "sw_si",
    "SE": "se_si",
    "BL": "bl_si",
    "PL": "pl_si",
    "PLI": "pl_si",
    "FD": "fd_si",
    "FDI": "fd_si",
    "AT": "at_si",
    "CW": "cw_si",
    "HW": "hw_si",
    "HWI": "hw_si",
}


def _species_token(species: str) -> str:
    code = str(species).strip().upper()
    return _SPECIES_TOKEN.get(code, code)


def _stratum_species_tokens(stratum_code: str) -> frozenset[str]:
    _, _, species_part = str(stratum_code).partition("_")
    return frozenset(token.strip() for token in species_part.split("+") if token.strip())


def _stratum_leading_species(stratum_code: str) -> str:
    _, _, species_part = str(stratum_code).partition("_")
    tokens = [token.strip() for token in species_part.split("+") if token.strip()]
    return tokens[0] if tokens else ""


def _site_index_for_species(row: pd.Series, species: str) -> float | None:
    column = _SI_SITE_INDEX_COLUMNS.get(str(species).strip().upper())
    if column is None:
        return None
    value = row.get(column)
    if pd.notna(value) and float(value) > 0:
        return float(value)
    return None


def _load_au_join(femic_root: Path | None) -> pd.DataFrame | None:
    """Build the managed FEMIC AU crosswalk used by all TSA24 benchmarks."""
    if femic_root is None:
        return None
    input_path = femic_root / "data" / "03_input-tsa24.csv"
    au_path = femic_root / "data" / "model_input_bundle" / "au_table.csv"
    if not input_path.exists() or not au_path.exists():
        return None
    inputs = pd.read_csv(input_path)
    au_table = pd.read_csv(au_path)
    managed = au_table[
        pd.to_numeric(au_table["source_managed_local_au_id"], errors="coerce").notna()
    ].copy()
    merged = managed.merge(
        inputs,
        left_on="source_managed_local_au_id",
        right_on="feature_id",
        how="inner",
    )
    if merged.empty:
        return None
    # The AU stratum code is the authoritative BEC/species identity.  The
    # 03_input BEC can differ from the untreated stratum attached to a managed
    # planted feature (for example an ICH opening whose managed curve is SBS_SX).
    merged["bec_zone"] = merged["stratum_code"].astype(str).str.partition("_")[0]
    merged["species_token"] = merged["stratum_code"].map(_stratum_leading_species)
    merged["au_site_index"] = [
        _site_index_for_species(
            row,
            _stratum_leading_species(str(row["stratum_code"])),
        )
        for _, row in merged.iterrows()
    ]
    return merged[
        [
            "au_id",
            "stratum_code",
            "si_level",
            "bec_zone",
            "species_token",
            "au_site_index",
            "feature_id",
        ]
    ].copy()


def _best_au_stratum(
    join: pd.DataFrame,
    bec_zone: str,
    leading_species: str,
) -> str | None:
    """Pick the managed AU stratum for a plot, including combined-species AUs."""
    token = _species_token(leading_species)
    bec = str(bec_zone).strip().upper()
    candidates = [
        str(stratum)
        for stratum in join.loc[join["bec_zone"].astype(str).str.upper() == bec, "stratum_code"].unique()
        if token in _stratum_species_tokens(stratum)
    ]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda stratum: (
            len(_stratum_species_tokens(stratum)),
            -int(
                (
                    (join["stratum_code"].astype(str) == stratum)
                    & (join["bec_zone"].astype(str).str.upper() == bec)
                ).sum()
            ),
            stratum,
        ),
    )


def _assign_si_level_from_join(
    join: pd.DataFrame,
    stratum_code: str | None,
    site_index: float | None,
) -> str | None:
    """Assign a plot to the nearest FEMIC AU site-index class."""
    if stratum_code is None or site_index is None or not math.isfinite(float(site_index)):
        return None
    rows = join[join["stratum_code"].astype(str) == str(stratum_code)]
    level_means: dict[str, float] = {}
    for level in ("L", "M", "H"):
        values = rows.loc[rows["si_level"].astype(str) == level, "au_site_index"].dropna()
        if not values.empty:
            level_means[level] = float(values.astype(float).mean())
    if not level_means:
        return None
    ordered = sorted(level_means, key=lambda level: level_means[level])
    for left, right in pairwise(ordered):
        midpoint = 0.5 * (level_means[left] + level_means[right])
        if float(site_index) <= midpoint:
            return left
    return ordered[-1]


def _au_id_from_join(
    join: pd.DataFrame,
    stratum_code: str | None,
    si_level: str | None,
) -> int | None:
    if stratum_code is None or si_level is None:
        return None
    matches = join[
        (join["stratum_code"].astype(str) == str(stratum_code))
        & (join["si_level"].astype(str) == str(si_level))
    ]
    if matches.empty:
        return None
    return int(matches.iloc[0]["feature_id"])


def _plot_benchmark_attributes(
    faib: FaibTreeList,
    femic_root: Path | None = None,
) -> pd.DataFrame:
    """Join FAIB plots to FEMIC managed AUs by BEC, species, and SI class.

    The join uses the FEMIC AU crosswalk (``au_table.csv`` + ``03_input``)
    rather than re-deriving independent site-index tertiles.  Combined-species
    AUs such as ``SBS_SX+AT`` are eligible when their species set contains the
    plot-leading token.
    """
    frame = faib.plots.copy()
    join = _load_au_join(femic_root)
    if join is None:
        frame["stratum_code"] = [
            _vdyp_stratum(bec, species)
            for bec, species in zip(frame["bec_zone"], frame["leading_species"])
        ]
        return _assign_si_levels(frame, "stratum_code")

    frame["stratum_code"] = [
        _best_au_stratum(join, bec, species)
        for bec, species in zip(frame["bec_zone"], frame["leading_species"])
    ]
    frame["si_level"] = [
        _assign_si_level_from_join(join, stratum, site_index)
        for stratum, site_index in zip(frame["stratum_code"], frame["site_index"])
    ]
    frame["au_id"] = [
        _au_id_from_join(join, stratum, level)
        for stratum, level in zip(frame["stratum_code"], frame["si_level"])
    ]
    return frame


def _benchmark_metrics(frame: pd.DataFrame, observed_col: str, predicted_col: str) -> dict[str, Any]:
    if frame.empty:
        return {"n": 0}
    observed = frame[observed_col].astype(float)
    predicted = frame[predicted_col].astype(float)
    valid = observed.notna() & predicted.notna()
    if not valid.any():
        return {"n": 0}
    observed = observed[valid]
    predicted = predicted[valid]
    bias = predicted - observed
    return {
        "n": int(len(frame)),
        "n_valid": int(valid.sum()),
        "observed_sum": float(observed.sum()),
        "predicted_sum": float(predicted.sum()),
        "mean_bias": float(bias.mean()),
        "mae": float(bias.abs().mean()),
        "rmse": float(np.sqrt(np.mean(np.square(bias)))),
        "relative_bias": float(bias.sum() / observed.sum()) if observed.sum() > 0 else None,
        "mean_ratio": float((predicted / observed).replace([np.inf, -np.inf], np.nan).dropna().mean())
        if (observed > 0).any()
        else None,
    }


def _run_vdyp(
    femic_root: Path,
    faib: FaibTreeList,
    plot_summary: pd.DataFrame,
    deadline: Deadline,
) -> tuple[dict[str, Any], pd.DataFrame]:
    path = femic_root / "data" / "vdyp_curves_smooth-tsa24.feather"
    if not path.exists():
        return {"status": "missing", "path": "vdyp_curves_smooth-tsa24.feather"}, pd.DataFrame()
    if deadline.expired():
        return {"status": "skipped_timebox"}, pd.DataFrame()
    try:
        curves = pd.read_feather(path)
    except Exception as exc:  # pragma: no cover - optional benchmark
        return {"status": "read_error", "error": f"{type(exc).__name__}: {exc}"}, pd.DataFrame()
    required = {"age", "volume", "stratum_code", "si_level"}
    if not required.issubset(curves.columns):
        return {
            "status": "schema_error",
            "missing": sorted(required.difference(curves.columns)),
        }, pd.DataFrame()
    attrs = _plot_benchmark_attributes(faib, femic_root)
    attrs = attrs.dropna(subset=["stratum_code", "si_level"])
    curve_groups = {
        (stratum, level): group.sort_values("age")
        for (stratum, level), group in curves.groupby(["stratum_code", "si_level"])
    }
    rows: list[dict[str, Any]] = []
    for plot in plot_summary.itertuples(index=False):
        attr = attrs[attrs["plot_id"].astype(str) == str(plot.plot_id)]
        if attr.empty:
            continue
        attr_row = attr.iloc[0]
        key = (attr_row["stratum_code"], attr_row["si_level"])
        curve = curve_groups.get(key)
        if curve is None or pd.isna(plot.stand_age):
            continue
        volume = float(np.interp(float(plot.stand_age), curve["age"], curve["volume"]))
        rows.append(
            {
                "plot_id": plot.plot_id,
                "bec_zone": plot.bec_zone,
                "leading_species": plot.leading_species,
                "stratum_code": key[0],
                "si_level": key[1],
                "site_index": attr_row["site_index"],
                "stand_age": plot.stand_age,
                "vdyp_volume_m3ha": volume,
                "observed_merchantable_all_m3ha": plot.observed_merchantable_all_m3ha,
                "observed_merchantable_sampled_m3ha": plot.observed_merchantable_sampled_m3ha,
                "bucked_log_m3ha": plot.bucked_log_m3ha,
                "bucked_log_m3ha_ratio_est": plot.bucked_log_m3ha_ratio_est,
            }
        )
    frame = pd.DataFrame(rows)
    if frame.empty:
        return {
            "status": "no_matched_plots",
            "note": "No defensible BEC x leading-species x SI-level x age match.",
        }, frame
    summary = {
        "status": "reported_with_join_assumptions",
        "n_plots_in_sample": int(len(plot_summary)),
        "n_matched_plots": int(len(frame)),
        "coverage": float(len(frame) / len(plot_summary)) if len(plot_summary) else None,
        "observed_all_vs_vdyp": _benchmark_metrics(
            frame, "observed_merchantable_all_m3ha", "vdyp_volume_m3ha"
        ),
        "bucked_vs_vdyp": _benchmark_metrics(
            frame, "bucked_log_m3ha_ratio_est", "vdyp_volume_m3ha"
        ),
        "input": "vdyp_curves_smooth-tsa24.feather",
        "join_basis": (
            "BEC + leading supported species -> managed FEMIC AU stratum "
            "(including combined-species strata) -> FEMIC AU site-index class -> "
            "VDYP stratum_code + L/M/H. This is the FEMIC AU crosswalk used as a "
            "benchmark join, not an operational AU assignment."
        ),
        "caveat": (
            "VDYP smooth volume units are assumed m3/ha; the curve may represent gross "
            "or total volume while VOL_MER is merchantable. The comparison is indicative, "
            "not a gate."
        ),
    }
    return summary, frame


def _tipsy_grade_columns(femic_root: Path) -> dict[str, Any]:
    path = femic_root / "data" / "04_output-tsa24.csv"
    if not path.exists():
        return {"status": "missing"}
    columns = list(pd.read_csv(path, nrows=0).columns)
    lower = [column.lower() for column in columns]
    grade_columns = [column for column in columns if "grade" in column.lower()]
    residue_columns = [column for column in columns if "resid" in column.lower()]
    value_columns = [
        column
        for column in columns
        if any(token in column.lower() for token in ("value", "price", "sort", "recovery"))
    ]
    return {
        "status": "inspected",
        "n_columns": len(columns),
        "grade_columns": grade_columns,
        "residue_columns": residue_columns,
        "value_columns": value_columns,
        "has_grade_or_residue_or_value": any(
            token in " ".join(lower) for token in ("grade", "resid", "value", "price", "sort")
        ),
        "note": (
            "The refreshed TSA24 BTC /TSR output now exposes Logs_Grade_D/F/H/I/J/"
            "U/X/Y and Residual_Chips/Sawdust/Shavings/Trim/Bark by age. It still "
            "has no value, price, or sort columns."
        ),
    }


def _run_tipsy(
    femic_root: Path,
    faib: FaibTreeList,
    plot_summary: pd.DataFrame,
    deadline: Deadline,
) -> tuple[dict[str, Any], pd.DataFrame]:
    curve_path = femic_root / "data" / "tipsy_curves_tsa24.csv"
    au_path = femic_root / "data" / "model_input_bundle" / "au_table.csv"
    inspection = _tipsy_grade_columns(femic_root)
    if not curve_path.exists() or not au_path.exists():
        return {
            "status": "missing",
            "curve": curve_path.name,
            "au_table": au_path.name,
            "grade_residue_value_inspection": inspection,
        }, pd.DataFrame()
    if deadline.expired():
        return {
            "status": "skipped_timebox",
            "grade_residue_value_inspection": inspection,
        }, pd.DataFrame()
    curves = pd.read_csv(curve_path)
    au_table = pd.read_csv(au_path)
    required = {"AU", "Age", "Yield"}
    if not required.issubset(curves.columns):
        return {
            "status": "schema_error",
            "missing": sorted(required.difference(curves.columns)),
            "grade_residue_value_inspection": inspection,
        }, pd.DataFrame()
    if not {"stratum_code", "si_level", "source_local_au_id"}.issubset(au_table.columns):
        return {
            "status": "au_table_schema_error",
            "missing": sorted(
                {"stratum_code", "si_level", "source_local_au_id"}.difference(au_table.columns)
            ),
            "grade_residue_value_inspection": inspection,
        }, pd.DataFrame()
    au_table = au_table.copy()
    for column in (
        "source_unmanaged_local_au_id",
        "source_managed_local_au_id",
        "source_local_au_id",
    ):
        if column not in au_table.columns:
            au_table[column] = np.nan
    au_table["tipsy_au"] = (
        au_table["source_managed_local_au_id"]
        .fillna(au_table["source_unmanaged_local_au_id"])
        .fillna(au_table["source_local_au_id"])
    )
    au_lookup = (
        au_table.dropna(subset=["tipsy_au"])
        .drop_duplicates(["stratum_code", "si_level"])
        .set_index(["stratum_code", "si_level"])["tipsy_au"]
        .to_dict()
    )
    available_au = set(curves["AU"].astype(int).unique())
    attrs = _plot_benchmark_attributes(faib, femic_root).dropna(
        subset=["stratum_code", "si_level"]
    )
    curve_groups = {
        int(au): group.sort_values("Age") for au, group in curves.groupby("AU")
    }
    rows: list[dict[str, Any]] = []
    for plot in plot_summary.itertuples(index=False):
        attr = attrs[attrs["plot_id"].astype(str) == str(plot.plot_id)]
        if attr.empty:
            continue
        attr_row = attr.iloc[0]
        au_value = attr_row.get("au_id") if "au_id" in attr_row else None
        au = (
            int(au_value)
            if au_value is not None and pd.notna(au_value)
            else au_lookup.get((attr_row["stratum_code"], attr_row["si_level"]))
        )
        if au is None or int(au) not in available_au or pd.isna(plot.stand_age):
            continue
        curve = curve_groups[int(au)]
        volume = float(np.interp(float(plot.stand_age), curve["Age"], curve["Yield"]))
        rows.append(
            {
                "plot_id": plot.plot_id,
                "bec_zone": plot.bec_zone,
                "leading_species": plot.leading_species,
                "stratum_code": attr_row["stratum_code"],
                "si_level": attr_row["si_level"],
                "au": int(au),
                "stand_age": plot.stand_age,
                "tipsy_yield_m3ha": volume,
                "observed_merchantable_all_m3ha": plot.observed_merchantable_all_m3ha,
                "bucked_log_m3ha": plot.bucked_log_m3ha,
                "bucked_log_m3ha_ratio_est": plot.bucked_log_m3ha_ratio_est,
            }
        )
    frame = pd.DataFrame(rows)
    covered = int(curves["AU"].nunique())
    summary = {
        "status": "reported_with_join_assumptions" if not frame.empty else "no_matched_plots",
        "n_plots_in_sample": int(len(plot_summary)),
        "n_matched_plots": int(len(frame)),
        "coverage": float(len(frame) / len(plot_summary)) if len(plot_summary) else None,
        "tipsy_au_available": covered,
        "tipsy_au_total": int(len(au_lookup)),
        "observed_all_vs_tipsy": _benchmark_metrics(
            frame, "observed_merchantable_all_m3ha", "tipsy_yield_m3ha"
        ),
        "bucked_vs_tipsy": _benchmark_metrics(
            frame, "bucked_log_m3ha_ratio_est", "tipsy_yield_m3ha"
        ),
        "grade_residue_value_inspection": inspection,
        "join_basis": (
            "BEC + leading species -> managed FEMIC AU stratum (including "
            "combined-species strata such as SBS_SX+AT) -> FEMIC AU site-index "
            "class -> managed source AU in au_table -> TIPSY curve at FAIB stand "
            "age."
        ),
        "caveat": (
            "TIPSY Yield is treated as m3/ha. Grade and residue surfaces are "
            "reported separately by the TSA24 grade benchmark; this volume join "
            "does not assert grade equivalence. The sort and price profile is a "
            "placeholder."
        ),
    }
    return summary, frame


def _rsyc_species(species: str) -> str | None:
    mapping = {
        "SW": "PICE.GLA",
        "SE": "PICE.ENG",
        "SB": "PICE.MAR",
        "S": "PICE.GLA",
        "PL": "PINU.CON",
        "BL": "ABIE.BAL",
        "FD": "PSEU.MEN",
        "AT": "POPU.TRE",
        "EP": "POPU.BAL",
        "HW": "TSUG.HET",
        "CW": "THUJ.PLI",
    }
    return mapping.get(str(species).upper())


def _evaluate_rsyc(model: pd.Series, age: float) -> float | None:
    try:
        b1 = float(model["b1"])
        b2 = float(model["b2"])
        b3 = float(model["b3"])
        b4 = float(model["b4"])
        age = float(age)
        if age <= 0 or not all(math.isfinite(value) for value in (b1, b2, b3, b4)):
            return None
        value = b1 * math.exp(-b4 * age) * (1.0 - math.exp(-b2 * age)) ** b3
    except (TypeError, ValueError, KeyError):
        return None
    return float(value) if math.isfinite(value) else None


def _run_rsyc(
    repo_root: Path,
    femic_root: Path,
    faib: FaibTreeList,
    plot_summary: pd.DataFrame,
    deadline: Deadline,
) -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame]:
    rsyc_root = repo_root / "data" / "raw" / "national" / "rsyc"
    models_path = rsyc_root / "RSYC_models.zip"
    tiles_path = rsyc_root / "RSYC_tiles.gpkg"
    ecostrat_path = rsyc_root / "RSYC_ecostrat.gpkg"
    boundary_path = femic_root / "data" / "tsa_boundaries.feather"
    missing = [
        str(path)
        for path in (models_path, tiles_path, ecostrat_path, boundary_path)
        if not path.exists()
    ]
    if missing:
        return {
            "status": "missing",
            "missing": [path.split("/")[-1] for path in missing],
        }, pd.DataFrame(), pd.DataFrame()
    if deadline.expired():
        return {"status": "skipped_timebox"}, pd.DataFrame(), pd.DataFrame()
    try:
        import geopandas as gpd
    except ImportError:
        return {
            "status": "skipped_geopandas_unavailable",
            "next_step": "Install geopandas and pyogrio/pyarrow, then rerun the RSYC block.",
        }, pd.DataFrame(), pd.DataFrame()
    try:
        boundary = gpd.read_feather(boundary_path)
        if "TSA_NUMBER" not in boundary.columns:
            boundary = boundary.reset_index()
        tsa = boundary[
            boundary["TSA_NUMBER"].astype(str) == str(TSA_NUMBER)
        ].to_crs(3978)
        if tsa.empty:
            return {"status": "tsa_boundary_missing"}, pd.DataFrame(), pd.DataFrame()
        centroid = tsa.geometry.centroid.iloc[0]
        tiles = gpd.read_file(tiles_path)
        ecodistricts = gpd.read_file(ecostrat_path, layer="ecodistricts")
        tile_row = tiles[tiles.contains(centroid)]
        if tile_row.empty:
            tile_row = tiles.iloc[[tiles.distance(centroid).argmin()]]
        ecodistrict_row = ecodistricts[ecodistricts.contains(centroid)]
        if ecodistrict_row.empty:
            ecodistrict_row = ecodistricts.iloc[[ecodistricts.distance(centroid).argmin()]]
        tile_id = str(tile_row.iloc[0]["TileID"])
        ecodistrict_key = str(ecodistrict_row.iloc[0]["join_key"])
        with zipfile.ZipFile(models_path) as archive:
            with archive.open("RSYC_models.csv") as handle:
                models = pd.read_csv(handle, low_memory=False)
    except Exception as exc:  # pragma: no cover - optional benchmark
        return {
            "status": "spatial_join_error",
            "error": f"{type(exc).__name__}: {exc}",
            "next_step": "Resolve the TSA boundary / RSYC GPKG CRS and join, then rerun.",
        }, pd.DataFrame(), pd.DataFrame()

    models = models[models["response"].astype(str).str.lower() == "volume"].copy()
    if "publish_ok" in models.columns:
        publish = models["publish_ok"].astype(str).str.upper().isin({"TRUE", "1", "T"})
        models = models[publish]
    if "fit_ok" in models.columns:
        fit = models["fit_ok"].astype(str).str.upper().isin({"TRUE", "1", "T"})
        models = models[fit]
    if models.empty:
        return {"status": "no_publishable_volume_models"}, pd.DataFrame(), pd.DataFrame()

    if "scale" in models.columns and (models["scale"] == "national").any():
        models = models[models["scale"] == "national"].copy()

    curve_rows: list[dict[str, Any]] = []
    species_to_plot = []
    for species in sorted(
        {
            _rsyc_species(code)
            for code in plot_summary["leading_species"].dropna().unique()
            if _rsyc_species(code)
        }
    ):
        for strata_type, strata_id, scope in (
            ("ecodistrict", ecodistrict_key, "tsa24_centroid_ecodistrict"),
            ("tile", tile_id, "tsa24_centroid_tile"),
        ):
            subset = models[
                (models["strata_type"].astype(str) == strata_type)
                & (models["strata_id"].astype(str) == strata_id)
                & (models["species"].astype(str) == species)
            ]
            if subset.empty:
                continue
            model = subset.iloc[0]
            species_to_plot.append(species)
            for age in range(20, 141, 10):
                value = _evaluate_rsyc(model, age)
                if value is None:
                    continue
                curve_rows.append(
                    {
                        "scope": scope,
                        "strata_type": strata_type,
                        "strata_id": strata_id,
                        "species": species,
                        "age": age,
                        "volume_m3ha": value,
                        "model_id": model.get("model_id"),
                        "model_version": model.get("model_version"),
                        "scale": model.get("scale"),
                        "response_units": model.get("response_units"),
                    }
                )
    curves = pd.DataFrame(curve_rows)

    plot_rows: list[dict[str, Any]] = []
    try:
        points = gpd.GeoDataFrame(
            plot_summary.copy(),
            geometry=gpd.points_from_xy(plot_summary["longitude"], plot_summary["latitude"]),
            crs=4326,
        ).to_crs(3978)
        joined = gpd.sjoin(
            points,
            ecodistricts[["join_key", "geometry"]],
            how="left",
            predicate="within",
        )
        joined = joined.drop_duplicates("plot_id")
        for plot in joined.itertuples(index=False):
            species = _rsyc_species(plot.leading_species)
            if not species or pd.isna(plot.stand_age):
                continue
            subset = models[
                (models["strata_type"].astype(str) == "ecodistrict")
                & (models["strata_id"].astype(str) == str(plot.join_key))
                & (models["species"].astype(str) == species)
            ]
            if subset.empty:
                continue
            model = subset.iloc[0]
            value = _evaluate_rsyc(model, plot.stand_age)
            if value is None:
                continue
            plot_rows.append(
                {
                    "plot_id": plot.plot_id,
                    "ecodistrict": plot.join_key,
                    "leading_species": plot.leading_species,
                    "rsyc_species": species,
                    "stand_age": plot.stand_age,
                    "rsyc_volume_m3ha": value,
                    "observed_merchantable_all_m3ha": plot.observed_merchantable_all_m3ha,
                    "bucked_log_m3ha": plot.bucked_log_m3ha,
                    "bucked_log_m3ha_ratio_est": plot.bucked_log_m3ha_ratio_est,
                }
            )
    except Exception as exc:  # pragma: no cover - optional benchmark
        plot_rows = []
        plot_error = f"{type(exc).__name__}: {exc}"
    else:
        plot_error = None

    plot_frame = pd.DataFrame(plot_rows)
    summary = {
        "status": "reported_with_join_assumptions",
        "tsa_boundary": "tsa24",
        "tsa_centroid_tile": tile_id,
        "tsa_centroid_ecodistrict": ecodistrict_key,
        "curve_join": "TSA24 centroid -> RSYC tile and ecodistrict; volume curves at ages 20-140",
        "plot_join": "sample plot coordinates -> RSYC ecodistrict; plot-level volume at stand age",
        "n_curve_rows": int(len(curves)),
        "n_plot_matches": int(len(plot_frame)),
        "species_curves": species_to_plot,
        "plot_comparison": _benchmark_metrics(
            plot_frame, "observed_merchantable_all_m3ha", "rsyc_volume_m3ha"
        )
        if not plot_frame.empty
        else {"n": 0},
        "bucked_plot_comparison": _benchmark_metrics(
            plot_frame, "bucked_log_m3ha_ratio_est", "rsyc_volume_m3ha"
        )
        if not plot_frame.empty
        else {"n": 0},
        "plot_join_error": plot_error,
        "caveat": (
            "RSYC is a stand-level remote-sensing yield product. Its volume response "
            "is total volume, not merchantable volume, and it has no tree lists, "
            "diameter distribution, grade, or residue. This is a coarse sanity check, "
            "not a bucking calibration."
        ),
        "next_step": (
            "If the plot-level ecodistrict join is sparse, join all TSA24 VRI polygons "
            "to RSYC tiles/ecodistricts and compare area-weighted curves rather than "
            "using a centroid-only check."
        ),
    }
    return summary, curves, plot_frame


def _write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, lineterminator="\n")


def _build_report(
    args: argparse.Namespace,
    faib: FaibTreeList,
    selection,
    tree_summary: pd.DataFrame,
    logs: pd.DataFrame,
    plot_summary: pd.DataFrame,
    conservation: dict[str, Any],
    psp: dict[str, Any],
    sort_summary: dict[str, Any],
    residue: dict[str, Any],
    harvest_residue: dict[str, Any],
    harvest_frame: pd.DataFrame,
    vdyp: dict[str, Any],
    tipsy: dict[str, Any],
    tsa24_grades: dict[str, Any],
    btc_volume: dict[str, Any],
    btc_volume_frame: pd.DataFrame,
    rsyc: dict[str, Any],
    taper_warnings: Sequence[str],
    elapsed_seconds: float,
) -> str:
    strata_frame = pd.DataFrame(selection.strata)
    species_counts = (
        pd.Series([tree.species for tree in selection.trees]).value_counts().rename_axis("species").reset_index(name="trees")
    )
    bec_counts = (
        pd.Series([tree.bec_zone for tree in selection.trees]).value_counts().rename_axis("bec_zone").reset_index(name="trees")
    )
    plot_columns = [
        "plot_id",
        "bec_zone",
        "leading_species",
        "n_live_supported_all",
        "n_sampled",
        "stand_age",
        "site_index",
        "observed_merchantable_sampled_m3ha",
        "bucked_log_m3ha",
        "residue_m3ha",
        "value_cad_ha_sample",
    ]
    available_plot_columns = [column for column in plot_columns if column in plot_summary.columns]
    sort_table = pd.DataFrame(sort_summary.get("by_product", []))
    market_table = pd.DataFrame(sort_summary.get("by_market_group", []))
    sort_code_table = pd.DataFrame(sort_summary.get("by_sort", []))
    if not sort_code_table.empty:
        sort_code_table = sort_code_table[
            [
                "sort_code",
                "product",
                "species_market_group",
                "n_logs",
                "volume_m3",
                "volume_share",
                "value_cad",
                "unpriced_volume_m3",
            ]
        ]
    lines = [
        "# TSA24 bucking pilot — validation report",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Pilot | `{PILOT_NAME}` |",
        f"| TSA | {TSA_NUMBER} — {TSA_NAME} |",
        f"| Generated (UTC) | {datetime.now(timezone.utc).isoformat(timespec='seconds')} |",
        "| Status | pilot complete; BTC volume benchmarked; grade not validated; harvest-slash ledger reported |",
        f"| Sample | {len(selection.plots)} plots, {len(selection.trees)} live trees |",
        f"| Logs | {len(logs)} |",
        f"| Elapsed | {elapsed_seconds:.1f} s |",
        "",
        "> This is the Phase 0/1 pilot. It is **not** the pre-registered three-way",
        "> validation gate. The refreshed TSA24 FEMIC BTC /TSR output exposes",
        "> `Logs_Grade_*`, `Residual_*`, `Biomass_Live_*`, and `Carbon_Live_*`",
        "> surfaces at each managed AU and age. Merchantable volume and harvest",
        "> slash are reported against the BTC volume and `biomass-live` banks.",
        "> Grade shares remain **not validated** because the pilot bucker is",
        "> dimension-only and D/F are zero in TSA24.",
        "",
        "## 1. Scope and contracts",
        "",
        "- Tree list: FAIB PSP `tree_detail`, `header`, `sample_byvisit`, and",
        "  `faib_compiled_spcsmries_siteage`; live trees only; latest visit per",
        "  `CLSTR_ID`. `PHF_TREE` is the per-hectare expansion factor.",
        "- Taper: nemora Kozak (2004) Version 6 parameter table, keyed by",
        "  `(jurisdiction=BC, species, BEC zone)`. Unsupported species and zones",
        "  fail loudly; the pilot records explicit skips.",
        "- Sort rules: `workflows/national/bucking/rules/bc_interior.yaml`, derived",
        "  from TIPSY 4.5 industrial interior dimensions. Geometry/dimension-based,",
        "  **not official grade**. Defect, sweep, knot, and rot are not represented.",
        "- Price profile: BC Interior Log Market Report, 3 months ending 2026-09-30.",
        "  Product-level averages, not grade-specific prices. The profile is a",
        "  placeholder for this pilot.",
        "- Taper residue: `taper total - recovered logs`. Density, moisture, and",
        "  chip yield are labelled assumptions.",
        "- Harvest slash: `AGB - merchantable log biomass`, where AGB is the BTC",
        "  `biomass-live` `Biomass_Live_Above` surface at the matched AU and age,",
        "  and log biomass is `sum(log_volume_m3 * basic_density_odt_m3)`. This is",
        "  **not** the BTC `Residual_*` mill-processing residue surface.",
        "",
        "## 2. TSA24 sample",
        "",
        "Selection method: within-BEC and within-leading-supported-species strata;",
        "one plot per site; up to "
        f"{selection.max_trees_per_plot} trees per plot sampled across species and",
        f"DBH quantiles; seed `{selection.seed}`. The full TSA24 live tree population",
        f"is {faib.stats.get('live_valid_trees', 0):,} trees on",
        f"{faib.stats.get('plots_with_live_trees', 0):,} plots.",
        "",
        "### Stratum allocation and selected counts",
        "",
        _markdown_table(strata_frame, digits=0),
        "",
        "### Sampled species and BEC",
        "",
        _markdown_table(species_counts, digits=0),
        "",
        _markdown_table(bec_counts, digits=0),
        "",
        "### Selected plots",
        "",
        _markdown_table(plot_summary[available_plot_columns], digits=2),
        "",
        "The per-plot observed and bucked volumes in the table are paired over the",
        "sampled trees only. `observed_merchantable_all_m3ha` (not shown) uses all",
        "live supported trees in the plot and is used only for the secondary",
        "ratio-expanded comparison.",
        "",
        "## 3. Bucking results",
        "",
        f"- Taper total: **{_fmt(conservation.get('total_taper_m3'), 3)} m³**",
        f"- Recovered logs: **{_fmt(conservation.get('recovered_log_m3'), 3)} m³**",
        f"- Residue: **{_fmt(conservation.get('residue_m3'), 3)} m³**",
        f"- Residue share of taper: **{_fmt((conservation.get('residue_volume_share') or 0) * 100, 1)}%**",
        f"- Conservation tolerance: abs {CONSERVATION_ABS_TOL:g} m³ + rel {CONSERVATION_REL_TOL:g}",
        f"- Bucker balance tolerance: abs {BUCKER_BALANCE_ABS_TOL:g} m³ + rel {BUCKER_BALANCE_REL_TOL:g}",
        f"- Max ledger balance error: **{_fmt(conservation.get('max_absolute_balance_m3'), 9)} m³**",
        f"- Conservation pass: **{conservation.get('pass')}**",
        f"- Bucker-internal max balance error (all trees): **{_fmt(conservation.get('bucker_internal_check', {}).get('max_absolute_balance_m3'), 9)} m³**",
        f"- Trees with no feasible log: **{conservation.get('n_trees_without_logs', 0)}**",
        f"- Reference taper warnings observed during bucking: **{len(taper_warnings)}** unique",
        "",
    ]
    for warning in taper_warnings:
        lines.append(f"  - `{warning}`")
    lines.extend(
        [
        "",
        "### Log volume and value",
        "",
        f"- Log volume: **{_fmt(sort_summary.get('total_log_volume_m3'), 3)} m³**",
        f"- Priced volume: **{_fmt(sort_summary.get('priced_volume_m3'), 3)} m³**",
        f"- Unpriced volume: **{_fmt(sort_summary.get('unpriced_volume_m3'), 3)} m³** "
        f"({_fmt((sort_summary.get('unpriced_volume_share') or 0) * 100, 1)}%)",
        f"- Total value: **${_fmt(sort_summary.get('total_value_cad'), 0)} CAD**",
        f"- Value per priced m³: **${_fmt(sort_summary.get('value_per_m3_priced'), 2)}/m³**",
        f"- Value per all-log m³: **${_fmt(sort_summary.get('value_per_m3_all_logs'), 2)}/m³**",
        f"- Mean sample value: **${_fmt(sort_summary.get('mean_value_cad_ha_sample'), 0)}/ha** "
        "(sampled trees; not a full-plot estimate)",
        f"- Mean ratio-expanded value: **${_fmt(sort_summary.get('mean_value_cad_ha_ratio_est'), 0)}/ha** "
        "(secondary estimate)",
        "",
        "### Sort and product shares",
        "",
        _markdown_table(sort_table, digits=3),
        "",
        _markdown_table(market_table, digits=3),
        "",
        "### Sort-code shares (top 20 by volume)",
        "",
        _markdown_table(sort_code_table, digits=3),
        "",
        ]
    )
    lines.extend(
        [
        "## 4. Validation",
        "",
        "### FAIB PSP observed merchantable volume vs bucked logs",
        "",
        f"- Plots: **{psp.get('n_plots', 0)}** "
        f"({psp.get('n_plots_observed_positive', 0)} with positive observed volume)",
        f"- Sum observed (sampled): **{_fmt(psp.get('observed_merchantable_m3ha_sum'), 2)} m³/ha**",
        f"- Sum bucked (sampled): **{_fmt(psp.get('bucked_log_m3ha_sum'), 2)} m³/ha**",
        f"- Mean bias: **{_fmt(psp.get('mean_bias_m3ha'), 2)} m³/ha**",
        f"- MAE: **{_fmt(psp.get('mae_m3ha'), 2)} m³/ha**",
        f"- RMSE: **{_fmt(psp.get('rmse_m3ha'), 2)} m³/ha**",
        f"- Relative bias: **{_fmt((psp.get('relative_bias') or 0) * 100, 1)}%**",
        f"- Mean ratio (bucked/observed): **{_fmt(psp.get('mean_ratio_bucked_observed'), 3)}**",
        f"- Median ratio (bucked/observed): **{_fmt(psp.get('median_ratio_bucked_observed'), 3)}**",
        "",
        "The primary comparison is paired over sampled trees. `VOL_MER` is the",
        "FAIB observed merchantable volume; the bucked log volume uses the nemora",
        "Kozak taper and dimension sorts. A difference is expected because the two",
        "merchantability rules, taper families, and sort boundaries differ.",
        "",
        "### VDYP7",
        "",
        f"- Status: **{vdyp.get('status')}**",
        f"- Matched plots: **{vdyp.get('n_matched_plots', 0)}** of "
        f"**{vdyp.get('n_plots_in_sample', 0)}**",
        f"- Join basis: {vdyp.get('join_basis', 'n/a')}",
        f"- Observed-all vs VDYP: {_compact_metrics(vdyp.get('observed_all_vs_vdyp'))}",
        f"- Bucked ratio-estimated vs VDYP: {_compact_metrics(vdyp.get('bucked_vs_vdyp'))}",
        f"- Caveat: {vdyp.get('caveat', 'n/a')}",
        "",
        "### TIPSY / FANSIER",
        "",
        f"- Status: **{tipsy.get('status')}**",
        f"- Matched plots: **{tipsy.get('n_matched_plots', 0)}** of "
        f"**{tipsy.get('n_plots_in_sample', 0)}**",
        f"- TIPSY AU coverage: **{tipsy.get('tipsy_au_available', 0)}** of "
        f"**{tipsy.get('tipsy_au_total', 0)}** mapped AU combinations",
        f"- Join basis: {tipsy.get('join_basis', 'n/a')}",
        f"- Observed-all vs TIPSY: {_compact_metrics(tipsy.get('observed_all_vs_tipsy'))}",
        f"- Bucked ratio-estimated vs TIPSY: {_compact_metrics(tipsy.get('bucked_vs_tipsy'))}",
        f"- Grade/residue/value inspection: {json.dumps(tipsy.get('grade_residue_value_inspection', {}), default=str)}",
        f"- Caveat: {tipsy.get('caveat', 'n/a')}",
        "- Note: the TIPSY volume metrics include the two zero-volume age joins;",
        "  the BTC volume section screens them out, so the two sections have",
        "  different n and should not be compared row-for-row.",
        "",
        "### BTC merchantable volume",
        "",
        f"- Status: **{btc_volume.get('status')}**",
        f"- Matched plots: **{btc_volume.get('n_au_matched', 0)}** of "
        f"**{btc_volume.get('n_plots_total', 0)}** have a managed AU; "
        f"**{btc_volume.get('n_positive_volume', 0)}** have a positive BTC volume "
        f"and enter the comparison; **{btc_volume.get('n_zero_btc_volume', 0)}** "
        f"match an AU but have zero BTC volume at the plot age; "
        f"**{btc_volume.get('n_no_au_match', 0)}** have no AU match.",
        "- Match tiers: "
        + ", ".join(
            f"**{key}**={value}"
            for key, value in sorted(
                (btc_volume.get("match_tier_counts") or {}).items()
            )
        )
        + ".",
        f"- Join basis: {btc_volume.get('join_basis', 'n/a')}",
        "",
        _markdown_table(_volume_benchmark_table(btc_volume), digits=2),
        "",
        "Plot-level volume join:",
        "",
        _markdown_table(_volume_join_table(btc_volume_frame), digits=2),
        "",
        "The primary comparison is the ratio-expanded pilot bucked volume against",
        "the BTC `volume` at the same AU and FAIB stand age. The observed PSP",
        "`VOL_MER*PHF` comparison is a sanity check on the same join. The",
        "sampled-tree comparison is shown only to make the expansion explicit; it",
        "is not a stand-level estimate.",
        "",
        f"- Caveat: {btc_volume.get('caveat', 'n/a')}",
        "",
        "### TSA24 grade benchmark (BTC /TSR) — not validated",
        "",
        "- Status: **not validated** (grade shares are a model-output proxy, not a field-grade check).",
        f"- Compiled curves: `{tsa24_grades.get('compiled_dir', 'n/a')}`",
        f"- Match tiers: **{tsa24_grades.get('n_au_curve', 0)}** AU-curve, "
        f"**{tsa24_grades.get('n_species_curve', 0)}** species-curve, "
        f"**{tsa24_grades.get('n_no_match', 0)}** no-match; "
        f"**{tsa24_grades.get('n_grade_comparable', 0)}** grade-comparable.",
        f"- TIPSY grade shares (proxy; `Logs_Grade_*`): "
        f"{_format_shares(tsa24_grades.get('grade_share_comparison', {}).get('aggregate_tipsy_grades'))}",
        f"- TIPSY grade-group proxy: "
        f"{_format_shares(tsa24_grades.get('grade_share_comparison', {}).get('aggregate_tipsy_groups'))}",
        f"- Pilot product shares (comparable plots): "
        f"{_format_shares(tsa24_grades.get('grade_share_comparison', {}).get('pilot_product_shares'))}",
        f"- Value: **{tsa24_grades.get('value_comparison', {}).get('status', 'n/a')}** — "
        f"{tsa24_grades.get('value_comparison', {}).get('note', 'n/a')}",
        f"- Join basis: {tsa24_grades.get('join_basis', 'n/a')}",
        "- Why this is not validated: the pilot bucker assigns dimension-only",
        "  industrial sorts (`bc_interior.yaml`), not BC log grades. `Logs_Grade_*`",
        "  is BTC /TSR model output at its own interior rules, not a measured field",
        "  grade. The two surfaces do not share a grade definition.",
        "- D/F status: `Logs_Grade_D` and `Logs_Grade_F` are zero for every TSA24",
        "  AU in this run. Any comparison is therefore limited to H/I/J/U/X/Y and",
        "  cannot validate premium-grade shares.",
        f"- Caveat: {tsa24_grades.get('grade_share_comparison', {}).get('note', 'n/a')}",
        "",
        "### RSYC",
        "",
        f"- Status: **{rsyc.get('status')}**",
        f"- TSA centroid tile: **{rsyc.get('tsa_centroid_tile', 'n/a')}**; "
        f"ecodistrict: **{rsyc.get('tsa_centroid_ecodistrict', 'n/a')}**",
        f"- Curve join: {rsyc.get('curve_join', 'n/a')}",
        f"- Plot join: {rsyc.get('plot_join', 'n/a')}",
        f"- Curve rows: **{rsyc.get('n_curve_rows', 0)}**; plot matches: "
        f"**{rsyc.get('n_plot_matches', 0)}**",
        f"- Observed-all vs RSYC: {_compact_metrics(rsyc.get('plot_comparison'))}",
        f"- Bucked ratio-estimated vs RSYC: {_compact_metrics(rsyc.get('bucked_plot_comparison'))}",
        f"- Caveat: {rsyc.get('caveat', 'n/a')}",
        f"- Next step: {rsyc.get('next_step', 'n/a')}",
        "",
        "## 5. Residue ledgers",
        "",
        "### Taper residue (pilot bucker)",
        "",
        f"- Residue volume: **{_fmt(residue.get('residue_volume_m3'), 3)} m³**",
        f"- Residue ODT: **{_fmt(residue.get('residue_odt'), 3)} ODT**",
        f"- Green tonnes at 45% moisture: **{_fmt(residue.get('green_tonnes_45'), 3)} t**",
        f"- Green tonnes at 30% moisture: **{_fmt(residue.get('green_tonnes_30'), 3)} t**",
        f"- Chip green tonnes at 45% and 0.90 yield: **{_fmt(residue.get('chip_green_tonnes_45'), 3)} t**",
        f"- Mean residue: **{_fmt(residue.get('mean_residue_m3ha'), 3)} m³/ha**; "
        f"**{_fmt(residue.get('mean_residue_odt_ha'), 3)} ODT/ha**",
        f"- Mean green tonnes/ha (45% / 30%): "
        f"**{_fmt(residue.get('mean_green_tonnes_45_ha'), 3)}** / "
        f"**{_fmt(residue.get('mean_green_tonnes_30_ha'), 3)}**",
        "",
        "Taper residue is `taper total - recovered logs` over the sampled stems.",
        "It covers the stump-height section, top, and unmerchantable stem",
        "sections. It does **not** include branches, foliage, or bark.",
        "",
        "### Harvest slash (AGB - merchantable log biomass)",
        "",
        f"- Status: **{harvest_residue.get('status')}**",
        f"- Primary plots: **{harvest_residue.get('n_plots_primary', 0)}** of "
        f"**{harvest_residue.get('n_plots_total', 0)}**; "
        f"**{harvest_residue.get('n_plots_excluded', 0)}** excluded by the "
        "mass-balance / volume-ratio screen.",
        "- Status counts: "
        + ", ".join(
            f"**{key}**={value}"
            for key, value in sorted(
                (harvest_residue.get("status_counts") or {}).items()
            )
        )
        + ".",
        "",
        _markdown_table(_harvest_residue_table(harvest_frame), digits=3),
        "",
        "Excluded plots (retained in the CSV with a reason):",
        "",
        _markdown_table(_harvest_excluded_table(harvest_residue), digits=2),
        "",
        "Summary over the primary plots (each plot treated as one hectare):",
        "",
        _markdown_table(_harvest_residue_summary_table(harvest_residue), digits=3),
        "",
        "Assumptions:",
        "",
        "- AGB: BTC `biomass-live` `Biomass_Live_Above`, ODT/ha, at the matched",
        "  managed AU and FAIB stand age. The stand-level value is applied to the",
        "  plot on a per-hectare basis and scaled by the plot's observed/BTC",
        "  merchantable-volume ratio, so AGB and pilot logs share a productivity",
        "  basis; no per-tree AGB is invented. The unscaled AGB is retained in the",
        "  ledger CSV as `agb_odt_ha_unscaled`.",
        "- Merchantable log biomass: `sum(log_volume_m3 * basic_density_odt_m3)`.",
        "  Log volume is expanded by `PHF_TREE` and then by the observed all-tree",
        "  / sampled-tree merchantable-volume ratio.",
        "- Basic density: species-specific defaults in",
        "  `workflows/national/bucking/rules/bc_basic_density.csv`. The table is",
        "  derived from the FVS-BC variant NBEL/FIA wood dry-weight table",
        "  (Smith & Miles 2009), converted at 16.01846 kg/m³ per lb/ft³. It is a",
        "  species default, **not** a BC-local sampled value.",
        f"- Roadside recovery: **{HARVEST_RECOVERY_FACTOR:.0%}** of the AGB-based",
        "  slash is assumed recoverable at roadside.",
        f"- Moisture: **{MOISTURE_GREEN:.0%}** wet basis green, **{MOISTURE_SEASONED:.0%}**",
        f"  seasoned; chip yield **{CHIP_YIELD:.0%}** of green residue tonnes.",
        "- Volume-ratio screen: plots outside "
        f"**{harvest_residue.get('assumptions', {}).get('volume_ratio_screen', {}).get('min', 0.40):.2f}-"
        f"{harvest_residue.get('assumptions', {}).get('volume_ratio_screen', {}).get('max', 2.50):.2f}** "
        "observed/BTC volume, or with AGB not above log biomass, are reported but",
        "  excluded from the primary ledger. This prevents a bad AU/age join from",
        "  being presented as negative slash.",
        "- Why AGB-based slash exceeds taper residue: AGB includes bark, branches,",
        "  and foliage, while the taper ledger only covers top, stump, and",
        "  unmerchantable stem sections. The per-plot table reports both, plus the",
        "  branch+foliage+bark and unmerchantable-wood components.",
        "",
        "**Not mill residue.** BTC `Residual_Chips`, `Residual_Sawdust`,",
        "`Residual_Shavings`, `Residual_Trim`, and `Residual_Bark` are",
        "mill-processing residues. They are not harvest slash and are deliberately",
        "not used as a validation target for this ledger.",
        "",
        "## 6. Evidence and provenance",
        "",
        "| Item | Evidence tier |",
        "|---|---|",
        "| FAIB PSP tree list | measured plot data |",
        "| Kozak 2004 taper | published_table |",
        "| TIPSY interior dimension sorts | published_table, adapted to geometry-only |",
        "| TSA24 BTC /TSR volume and grade | model_output; AU and site-index specific |",
        "| TSA24 BTC `biomass-live` AGB | model_output; AU and age specific |",
        "| Species basic density | species_default_not_bc_local (FVS-BC NBEL/FIA) |",
        "| BC Interior log prices | published product averages; placeholder for grade value |",
        "| Recovery / moisture / chip yield | assumption |",
        "| Grade assignment | none (`not_assigned`); grade shares not validated |",
        "",
        "Input and output hashes are in `manifest.json`.",
        "",
        "## 7. Gaps and next steps",
        "",
        "- No official grade benchmark: the pilot is dimension-only, so its sort",
        "  shares are not BC log grades. The BTC `Logs_Grade_*` shares are a",
        "  model-output proxy, not a field-grade gate. Grade shares are explicitly",
        "  **not validated**.",
        "- Logs_Grade_D and Logs_Grade_F are zero for every TSA24 AU in this run.",
        "  The grade comparison therefore covers the H/I/J/U/X/Y surface only.",
        "- BTC `Residual_*` is mill-processing residue (chips/sawdust/shavings/",
        "  trim/bark), not harvest slash. It is deliberately excluded from the",
        "  harvest-slash ledger.",
        "- The BTC volume join matches 14 of 20 pilot plots. The six unmatched",
        "  plots are reported with reasons; three have a species-curve grade",
        "  fallback but no managed AU and therefore no BTC volume or AGB join.",
        "- The harvest-slash ledger is a stand-level AGB model comparison, not a",
        "  per-tree biomass audit. It is screened to plots whose observed/BTC",
        "  volume ratio is within the documented band and whose AGB exceeds log",
        "  biomass. Expand the sample and refine the AU/age clock before using it",
        "  as an operational slash supply estimate.",
        "- The VDYP and TIPSY joins use the FEMIC AU crosswalk (BEC + species",
        "  + FEMIC site-index class), including combined-species AUs. The join is",
        "  still a benchmark mapping, not an operational AU assignment.",
        "- The price profile has no pulpwood price for Hem-Bal or Cedar and no prices",
        "  for White Pine or Yellow Pine. Unpriced volume is reported separately.",
        "- Basic density is a species-specific default table (FVS-BC NBEL/FIA),",
        "  not a BC-local sample. Recovery, moisture, and chip yield are assumptions.",
        "  Replace them with species- and product-specific values before publishing",
        "  a residue supply offer.",
        "- The sample is 20 plots and 300 trees. It is a pilot, not a TSA-wide",
        "  estimate. Expand to all TSA24 plots and add a proper design-based",
        "  expansion before operational use.",
        "- RSYC is a stand-level total-volume/AGB check, not a bucking calibration.",
        "",
    ]
    )
    return "\n".join(lines)


def _build_harvest_residue_note(
    harvest_residue: Mapping[str, Any],
    btc_volume: Mapping[str, Any],
) -> str:
    per_ha = harvest_residue.get("per_ha") or {}
    per_m3 = harvest_residue.get("per_m3") or {}
    totals = harvest_residue.get("totals_over_primary_plot_ha") or {}
    assumptions = harvest_residue.get("assumptions") or {}

    def stat(section: Mapping[str, Any], key: str, field: str = "mean") -> Any:
        return (section.get(key) or {}).get(field)

    lines = [
        "# TSA24 harvest-slash residue ledger",
        "",
        "This note documents the AGB-based harvest-slash ledger for the TSA24",
        "bucking pilot. It is a model-output comparison, not a delivered-fuel",
        "offer and not a field residue audit.",
        "",
        "## Method",
        "",
        "```",
        "harvest_residue_odt = agb_odt - merchantable_log_biomass_odt",
        "merchantable_log_biomass_odt = sum(log_volume_m3 * basic_density_odt_m3)",
        "```",
        "",
        "AGB is the FEMIC BTC `biomass-live` bank `Biomass_Live_Above` value at",
        "the managed AU and FAIB stand age matched by BEC + leading species ->",
        "AU stratum -> FEMIC site-index class -> AU. The BTC biomass family is",
        "reported in oven-dry tonnes per hectare; `Biomass_Live_Above` is stem",
        "wood + bark + foliage + branch, with roots and total reported separately.",
        "",
        "The stand-level AGB is applied to the plot on a per-hectare basis and",
        "scaled by `observed_merchantable_all / BTC_volume` so AGB and the pilot",
        "log volume share the same plot productivity basis. No per-tree AGB is",
        "invented. Merchantable log biomass is expanded from the sampled trees by",
        "`PHF_TREE` and then by the observed all-tree/sampled-tree merchantable",
        "volume ratio.",
        "",
        "## Coverage and screen",
        "",
        f"- Pilot plots: {harvest_residue.get('n_plots_total', 0)}",
        f"- Primary ledger plots: {harvest_residue.get('n_plots_primary', 0)}",
        f"- Excluded plots: {harvest_residue.get('n_plots_excluded', 0)}",
        f"- BTC volume AU matches: {btc_volume.get('n_au_matched', 0)} of "
        f"{btc_volume.get('n_plots_total', 0)}; positive-volume joins: "
        f"{btc_volume.get('n_positive_volume', 0)}",
        "",
        "Plots are excluded from the primary ledger when there is no managed AU,",
        "the BTC volume is zero at the plot age, the observed/BTC volume ratio is",
        "outside the documented band, or AGB does not exceed log biomass. Excluded",
        "plots remain in `harvest_residue_ledger.csv` with a status and reason.",
        "",
        "## Basic density",
        "",
        "Species-specific defaults are in",
        "`workflows/national/bucking/rules/bc_basic_density.csv`. The values come",
        "from the FVS-BC variant NBEL/FIA wood dry-weight table (Smith & Miles",
        "2009), converted from dry lb/ft³ to ODT/m³ at 16.01846 kg/m³ per lb/ft³.",
        "They are species defaults, **not** BC-local sampled values.",
        "",
        "| Species | ODT/m³ |",
        "| --- | --- |",
        "| SW / S (white spruce) | 0.3598 |",
        "| SB (black spruce) | 0.3798 |",
        "| SE (Engelmann spruce) | 0.3298 |",
        "| PL (lodgepole pine) | 0.3798 |",
        "| FD (Douglas-fir) | 0.4498 |",
        "| BL (subalpine fir) | 0.3098 |",
        "| CW (western redcedar) | 0.3098 |",
        "| HW (western hemlock) | 0.4198 |",
        "| AT (trembling aspen) | 0.3498 |",
        "| EP (paper birch) | 0.4798 |",
        "| AC (black cottonwood) | 0.3098 |",
        "",
        "## Recovery, moisture, and chip assumptions",
        "",
        f"- Roadside recovery factor: {assumptions.get('recovery_factor', 0.60):.0%} of AGB-based slash.",
        f"- Moisture, green: {assumptions.get('moisture_green_wet_basis', 0.45):.0%} wet basis.",
        f"- Moisture, seasoned: {assumptions.get('moisture_seasoned_wet_basis', 0.30):.0%} wet basis.",
        f"- Chip yield: {assumptions.get('chip_yield_fraction', 0.90):.0%} of green residue tonnes.",
        "",
        "## Results (primary plots)",
        "",
        "| Metric | Mean | Median | Min | Max |",
        "| --- | --- | --- | --- | --- |",
        f"| AGB (ODT/ha) | {_fmt(stat(per_ha, 'agb_odt_ha'), 3)} | {_fmt(stat(per_ha, 'agb_odt_ha', 'median'), 3)} | {_fmt(stat(per_ha, 'agb_odt_ha', 'min'), 3)} | {_fmt(stat(per_ha, 'agb_odt_ha', 'max'), 3)} |",
        f"| Merchantable log biomass (ODT/ha) | {_fmt(stat(per_ha, 'merchantable_log_odt_ha'), 3)} | {_fmt(stat(per_ha, 'merchantable_log_odt_ha', 'median'), 3)} | {_fmt(stat(per_ha, 'merchantable_log_odt_ha', 'min'), 3)} | {_fmt(stat(per_ha, 'merchantable_log_odt_ha', 'max'), 3)} |",
        f"| Harvest slash (ODT/ha) | {_fmt(stat(per_ha, 'harvest_residue_odt_ha'), 3)} | {_fmt(stat(per_ha, 'harvest_residue_odt_ha', 'median'), 3)} | {_fmt(stat(per_ha, 'harvest_residue_odt_ha', 'min'), 3)} | {_fmt(stat(per_ha, 'harvest_residue_odt_ha', 'max'), 3)} |",
        f"| Taper residue (ODT/ha) | {_fmt(stat(per_ha, 'taper_residue_odt_ha'), 3)} | {_fmt(stat(per_ha, 'taper_residue_odt_ha', 'median'), 3)} | {_fmt(stat(per_ha, 'taper_residue_odt_ha', 'min'), 3)} | {_fmt(stat(per_ha, 'taper_residue_odt_ha', 'max'), 3)} |",
        f"| Non-taper residue (ODT/ha) | {_fmt(stat(per_ha, 'non_taper_residue_odt_ha'), 3)} | {_fmt(stat(per_ha, 'non_taper_residue_odt_ha', 'median'), 3)} | {_fmt(stat(per_ha, 'non_taper_residue_odt_ha', 'min'), 3)} | {_fmt(stat(per_ha, 'non_taper_residue_odt_ha', 'max'), 3)} |",
        f"| Recoverable slash (ODT/ha) | {_fmt(stat(per_ha, 'recoverable_residue_odt_ha'), 3)} | {_fmt(stat(per_ha, 'recoverable_residue_odt_ha', 'median'), 3)} | {_fmt(stat(per_ha, 'recoverable_residue_odt_ha', 'min'), 3)} | {_fmt(stat(per_ha, 'recoverable_residue_odt_ha', 'max'), 3)} |",
        f"| Slash per m³ log (ODT/m³) | {_fmt(stat(per_m3, 'harvest_residue_odt_per_m3_log'), 3)} | {_fmt(stat(per_m3, 'harvest_residue_odt_per_m3_log', 'median'), 3)} | {_fmt(stat(per_m3, 'harvest_residue_odt_per_m3_log', 'min'), 3)} | {_fmt(stat(per_m3, 'harvest_residue_odt_per_m3_log', 'max'), 3)} |",
        f"| Taper residue per m³ taper total (ODT/m³) | {_fmt(stat(per_m3, 'taper_residue_odt_per_m3_taper_total'), 3)} | {_fmt(stat(per_m3, 'taper_residue_odt_per_m3_taper_total', 'median'), 3)} | {_fmt(stat(per_m3, 'taper_residue_odt_per_m3_taper_total', 'min'), 3)} | {_fmt(stat(per_m3, 'taper_residue_odt_per_m3_taper_total', 'max'), 3)} |",
        "",
        f"- Primary-plot hectares (each plot treated as one hectare): {totals.get('sample_hectares', 0)}",
        f"- Harvest slash total: {_fmt(totals.get('harvest_residue_odt'), 3)} ODT",
        f"- Taper residue total: {_fmt(totals.get('taper_residue_odt'), 3)} ODT",
        f"- Recoverable slash total: {_fmt(totals.get('recoverable_residue_odt'), 3)} ODT",
        f"- Recoverable green tonnes at 45%: {_fmt(totals.get('recoverable_green_tonnes_45'), 3)} t",
        f"- Recoverable chip green tonnes at 45% and 0.90 yield: {_fmt(totals.get('recoverable_chip_green_tonnes'), 3)} t",
        f"- Harvest-to-taper residue ratio: {_fmt(harvest_residue.get('ratio_harvest_to_taper_residue'), 2)}",
        "",
        "## Difference from taper residue and mill residue",
        "",
        "AGB-based harvest slash is larger than the pilot taper residue because",
        "AGB includes bark, branches, and foliage, while the taper ledger only",
        "covers the stump-height section, top, and unmerchantable stem sections.",
        "The per-plot ledger reports both quantities and the branch+foliage+bark",
        "and unmerchantable-wood components.",
        "",
        "BTC `Residual_Chips`, `Residual_Sawdust`, `Residual_Shavings`,",
        "`Residual_Trim`, and `Residual_Bark` are **mill-processing residues**.",
        "They are not harvest slash, are not on the same basis, and are deliberately",
        "excluded from this ledger. No ratio against `Residual_*` is computed.",
        "",
        "## Limitations",
        "",
        "- AGB is a stand-level BTC model output at the managed AU site index, not a",
        "  plot measurement.",
        "- The FAIB stand age and the managed AU age origin are not guaranteed to be",
        "  the same clock; the volume-ratio screen removes the worst mismatches but",
        "  does not eliminate the join uncertainty.",
        "- Basic density is a species default, not a BC-local sample.",
        "- Recovery, moisture, and chip yield are assumptions.",
        "- The sample is 20 plots / 300 trees; this is a pilot, not a TSA-wide",
        "  residue supply estimate.",
        "",
    ]
    return "\n".join(lines)


def _compact_metrics(metrics: dict[str, Any] | None) -> str:
    if not metrics or not metrics.get("n_valid"):
        return "n/a"
    return (
        f"n={metrics['n_valid']}, bias={_fmt(metrics.get('mean_bias'))}, "
        f"MAE={_fmt(metrics.get('mae'))}, "
        f"rel.bias={_fmt((metrics.get('relative_bias') or 0) * 100, 1)}%"
    )


def _volume_join_table(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    columns = [
        "plot_id",
        "leading_species",
        "stand_age",
        "au_id",
        "match_tier",
        "btc_volume_m3ha",
        "observed_merchantable_all_m3ha",
        "pilot_bucked_log_m3ha_ratio_est",
    ]
    return frame[columns].rename(
        columns={
            "leading_species": "leading species",
            "stand_age": "stand age",
            "btc_volume_m3ha": "BTC m3/ha",
            "observed_merchantable_all_m3ha": "observed m3/ha",
            "pilot_bucked_log_m3ha_ratio_est": "pilot m3/ha (ratio est.)",
        }
    )


def _volume_benchmark_table(btc_volume: Mapping[str, Any]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for label, key in (
        ("Observed PSP VOL_MER*PHF (all live trees)", "observed_all_vs_btc"),
        ("Pilot bucked logs (ratio-expanded)", "bucked_ratio_est_vs_btc"),
        ("Pilot bucked logs (sampled trees only)", "bucked_sampled_vs_btc"),
    ):
        metrics = btc_volume.get(key) or {}
        if not metrics.get("n"):
            continue
        mean_value = float(metrics["mean_observed"])
        mean_btc = float(metrics["mean_predicted"])
        rows.append(
            {
                "comparison": label,
                "n": int(metrics["n"]),
                "mean_value": mean_value,
                "mean_btc": mean_btc,
                "bias_value_minus_btc": mean_value - mean_btc,
                "mae": float(metrics["mae"]),
                "rmse": float(metrics["rmse"]),
                "relative_bias_value_vs_btc": (
                    mean_value / mean_btc - 1.0 if mean_btc > 0 else None
                ),
            }
        )
    return pd.DataFrame(rows)


def _harvest_residue_table(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    primary = frame[frame["status"] == "ok"].copy()
    if primary.empty:
        return primary
    columns = [
        "plot_id",
        "leading_species",
        "stand_age",
        "au_id",
        "agb_odt_ha",
        "merchantable_log_odt_ha",
        "harvest_residue_odt_ha",
        "taper_residue_odt_ha",
        "branch_foliage_bark_odt_ha",
        "harvest_residue_odt_per_m3_log",
        "recoverable_residue_odt_ha",
        "recoverable_green_tonnes_45_ha",
    ]
    return primary[columns].rename(
        columns={
            "agb_odt_ha": "AGB ODT/ha",
            "merchantable_log_odt_ha": "log ODT/ha",
            "harvest_residue_odt_ha": "slash ODT/ha",
            "taper_residue_odt_ha": "taper ODT/ha",
            "branch_foliage_bark_odt_ha": "branch+foliage+bark ODT/ha",
            "harvest_residue_odt_per_m3_log": "slash ODT/m3 log",
            "recoverable_residue_odt_ha": "recoverable ODT/ha",
            "recoverable_green_tonnes_45_ha": "recoverable green t/ha (45%)",
        }
    )


def _harvest_excluded_table(harvest_residue: Mapping[str, Any]) -> pd.DataFrame:
    rows = harvest_residue.get("excluded_plots") or []
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).rename(
        columns={
            "plot_id": "plot_id",
            "bec_zone": "BEC",
            "leading_species": "leading species",
            "stand_age": "stand age",
            "status": "status",
            "status_reason": "reason",
        }
    )


def _harvest_residue_summary_table(harvest_residue: Mapping[str, Any]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    units = {
        "agb_odt_ha": "ODT/ha",
        "agb_odt_ha_unscaled": "ODT/ha",
        "merchantable_log_odt_ha": "ODT/ha",
        "harvest_residue_odt_ha": "ODT/ha",
        "taper_residue_odt_ha": "ODT/ha",
        "non_taper_residue_odt_ha": "ODT/ha",
        "branch_foliage_bark_odt_ha": "ODT/ha",
        "unmerchantable_wood_odt_ha": "ODT/ha",
        "recoverable_residue_odt_ha": "ODT/ha",
        "green_tonnes_45_ha": "green t/ha",
        "green_tonnes_30_ha": "green t/ha",
        "recoverable_green_tonnes_45_ha": "green t/ha",
        "recoverable_chip_green_tonnes_ha": "green t/ha",
    }
    for key, unit in units.items():
        stats = (harvest_residue.get("per_ha") or {}).get(key) or {}
        if not stats.get("n"):
            continue
        rows.append(
            {
                "metric": f"{key} ({unit})",
                "n": stats["n"],
                "mean": stats["mean"],
                "median": stats["median"],
                "min": stats["min"],
                "max": stats["max"],
            }
        )
    for key, unit in (
        ("harvest_residue_odt_per_m3_log", "ODT/m3 log"),
        ("taper_residue_odt_per_m3_taper_total", "ODT/m3 taper total"),
    ):
        stats = (harvest_residue.get("per_m3") or {}).get(key) or {}
        if not stats.get("n"):
            continue
        rows.append(
            {
                "metric": f"{key} ({unit})",
                "n": stats["n"],
                "mean": stats["mean"],
                "median": stats["median"],
                "min": stats["min"],
                "max": stats["max"],
            }
        )
    return pd.DataFrame(rows)


def _build_manifest(
    args: argparse.Namespace,
    faib: FaibTreeList,
    selection,
    registry: TaperRegistry,
    rules_path: Path,
    price_path: Path,
    parameter_path: Path,
    crosswalk_path: Path,
    femic_root: Path,
    out_dir: Path,
    conservation: dict[str, Any],
    psp: dict[str, Any],
    taper_warnings: Sequence[str],
    elapsed_seconds: float,
) -> dict[str, Any]:
    inputs = [
        _hash_record(
            "faib_header",
            Path(args.data_root) / "bc_faib_ground_plots" / "psp" / "faib_header.csv.gz",
            "raw/bc_faib_ground_plots/psp/faib_header.csv.gz",
        ),
        _hash_record(
            "faib_sample_byvisit",
            Path(args.data_root) / "bc_faib_ground_plots" / "psp" / "faib_sample_byvisit.csv.gz",
            "raw/bc_faib_ground_plots/psp/faib_sample_byvisit.csv.gz",
        ),
        _hash_record(
            "faib_tree_detail",
            Path(args.data_root) / "bc_faib_ground_plots" / "psp" / "faib_tree_detail.csv.gz",
            "raw/bc_faib_ground_plots/psp/faib_tree_detail.csv.gz",
        ),
        _hash_record(
            "faib_siteage",
            Path(args.data_root)
            / "bc_faib_ground_plots"
            / "psp"
            / "faib_compiled_spcsmries_siteage.csv.gz",
            "raw/bc_faib_ground_plots/psp/faib_compiled_spcsmries_siteage.csv.gz",
        ),
        _hash_record("kozak_parameters", parameter_path, "nemora/config/kozak/kozak_parameters_v94.csv"),
        _hash_record("species_crosswalk", crosswalk_path, "nemora/config/species/bc_psp_to_group.csv"),
        _hash_record("grade_rules", rules_path, "workflows/national/bucking/rules/bc_interior.yaml"),
        _hash_record("price_profile", price_path, "nemora/config/log_prices/bc_interior_2026q3.yaml"),
        _hash_record(
            "vdyp_smooth",
            femic_root / "data" / "vdyp_curves_smooth-tsa24.feather",
            "femic-tsa24-instance/data/vdyp_curves_smooth-tsa24.feather",
        ),
        _hash_record(
            "tipsy_curves",
            femic_root / "data" / "tipsy_curves_tsa24.csv",
            "femic-tsa24-instance/data/tipsy_curves_tsa24.csv",
        ),
        _hash_record(
            "tipsy_output",
            femic_root / "data" / "04_output-tsa24.csv",
            "femic-tsa24-instance/data/04_output-tsa24.csv",
        ),
        _hash_record(
            "biomass_output",
            femic_root / "data" / "04_output-tsa24_biomass.csv",
            "femic-tsa24-instance/data/04_output-tsa24_biomass.csv",
        ),
        _hash_record(
            "au_table",
            femic_root / "data" / "model_input_bundle" / "au_table.csv",
            "femic-tsa24-instance/data/model_input_bundle/au_table.csv",
        ),
        _hash_record(
            "tsa24_grade_curves",
            _resolve_path(args.grade_curves_dir, Path(args.repo_root))
            / "managed_au_curves.csv",
            "tsa24_grade_curves/managed_au_curves.csv",
        ),
        _hash_record(
            "tsa24_biomass_curves",
            _resolve_path(args.grade_curves_dir, Path(args.repo_root))
            / "biomass_au_curves.csv",
            "tsa24_grade_curves/biomass_au_curves.csv",
        ),
        _hash_record(
            "tsa24_biomass_manifest",
            _resolve_path(args.grade_curves_dir, Path(args.repo_root))
            / "biomass_curves_manifest.json",
            "tsa24_grade_curves/biomass_curves_manifest.json",
        ),
        _hash_record(
            "basic_density",
            _resolve_path(args.basic_density, Path(args.repo_root)),
            "workflows/national/bucking/rules/bc_basic_density.csv",
        ),
        _hash_record(
            "tsa24_grade_industrial_sorts",
            _resolve_path(args.grade_curves_dir, Path(args.repo_root))
            / "managed_au_industrial_sorts.csv",
            "tsa24_grade_curves/managed_au_industrial_sorts.csv",
        ),
        _hash_record(
            "rsyc_models",
            Path(args.repo_root) / "data" / "raw" / "national" / "rsyc" / "RSYC_models.zip",
            "raw/national/rsyc/RSYC_models.zip",
        ),
        _hash_record(
            "rsyc_tiles",
            Path(args.repo_root) / "data" / "raw" / "national" / "rsyc" / "RSYC_tiles.gpkg",
            "raw/national/rsyc/RSYC_tiles.gpkg",
        ),
        _hash_record(
            "rsyc_ecostrat",
            Path(args.repo_root) / "data" / "raw" / "national" / "rsyc" / "RSYC_ecostrat.gpkg",
            "raw/national/rsyc/RSYC_ecostrat.gpkg",
        ),
        _hash_record(
            "tsa_boundaries",
            femic_root / "data" / "tsa_boundaries.feather",
            "femic-tsa24-instance/data/tsa_boundaries.feather",
        ),
    ]
    output_paths = sorted(out_dir.glob("*"))
    outputs = [
        {
            "path": path.name,
            "bytes": int(path.stat().st_size),
            "sha256": _sha256(path),
        }
        for path in output_paths
        if path.is_file() and path.name != "manifest.json"
    ]
    return {
        "pilot": PILOT_NAME,
        "tsa_number": TSA_NUMBER,
        "tsa_name": TSA_NAME,
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "elapsed_seconds": elapsed_seconds,
        "code": {
            "package": "workflows.national.bucking",
            "version": "0.1.0",
            "schema": "TreeRecord, BuckedLog, ResidueLedger, GradeRule",
        },
        "inputs": inputs,
        "config": {
            "jurisdiction": "BC",
            "taper": registry.manifest_record(),
            "grade_rules": {
                "path": "workflows/national/bucking/rules/bc_interior.yaml",
                "authority": "TIPSY 4.5 industrial interior dimensions",
                "basis": "dimension_only",
                "grade_status": "not_assigned",
            },
            "price_profile": {
                "path": "nemora/config/log_prices/bc_interior_2026q3.yaml",
                "period": "2026-07-01/2026-09-30",
                "note": "product-level averages, not grade-specific prices",
            },
            "residue_assumptions": {
                "basic_density_odt_m3": BASIC_DENSITY_ODT_M3,
                "basic_density_source": (
                    "species-specific defaults in rules/bc_basic_density.csv "
                    "(FVS-BC NBEL/FIA table; not a BC-local sample)"
                ),
                "moisture_green_wet_basis": MOISTURE_GREEN,
                "moisture_seasoned_wet_basis": MOISTURE_SEASONED,
                "chip_yield_fraction": CHIP_YIELD,
                "harvest_recovery_factor": HARVEST_RECOVERY_FACTOR,
            },
        },
        "sample": selection.manifest_record(),
        "population": faib.stats,
        "conservation": conservation,
        "psp_volume": psp,
        "taper_warnings": list(taper_warnings),
        "outputs": outputs,
        "notes": [
            "Pilot, not the pre-registered three-way validation gate.",
            "No official grade assigned; grade shares are not validated.",
            "Merchantable volume is benchmarked against the compiled BTC volume curve.",
            "Harvest slash is AGB - merchantable log biomass from the BTC biomass-live bank.",
            "BTC Residual_* is mill-processing residue and is not used as harvest slash.",
            "Logs_Grade_D and Logs_Grade_F are zero for every TSA24 AU in this run.",
        ],
    }


def _update_metadata_csv(path: Path, row: dict[str, Any]) -> None:
    columns = [
        "pilot",
        "tsa",
        "dataset",
        "n_trees",
        "n_logs",
        "log_m3",
        "residue_m3",
        "odt",
        "status",
        "notes",
    ]
    if path.exists():
        frame = pd.read_csv(path, dtype=str)
    else:
        frame = pd.DataFrame(columns=columns)
    frame = frame[frame["pilot"] != row["pilot"]]
    frame = pd.concat([frame, pd.DataFrame([row])], ignore_index=True)
    frame = frame[columns]
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, lineterminator="\n")


def _resolve_path(value: str | Path, repo_root: Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return (repo_root / path).resolve()


def run(args: argparse.Namespace) -> dict[str, Any]:
    started = time.monotonic()
    deadline = Deadline(args.timebox_seconds)
    repo_root = Path(args.repo_root).resolve()
    assert_no_unexpected_deletions(repo_root)
    out_dir = Path(args.out_dir)
    if not out_dir.is_absolute():
        out_dir = repo_root / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    parameter_path = _resolve_path(args.kozak_parameters, repo_root)
    price_path = _resolve_path(args.price_profile, repo_root)
    rules_path = _resolve_path(args.rules, repo_root)
    crosswalk_path = _resolve_path(args.species_crosswalk, repo_root)
    femic_root = _resolve_path(args.femic_root, repo_root)

    registry = TaperRegistry(parameter_path=parameter_path)
    rules = load_grade_rules(rules_path)
    prices = load_price_profile(price_path)

    print("Loading FAIB PSP tree list...", file=sys.stderr)
    faib, population = _load_inputs(args, registry)
    print(
        f"Loaded {len(faib.trees):,} live trees on {len(faib.plots):,} plots.",
        file=sys.stderr,
    )
    print("Selecting stratified sample...", file=sys.stderr)
    selection = _select_sample(faib, registry, args)
    print(
        f"Selected {len(selection.plots)} plots and {len(selection.trees)} trees.",
        file=sys.stderr,
    )
    deadline.check("bucking")
    print("Bucking sample...", file=sys.stderr)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        batch = buck_trees(
            selection.trees,
            registry,
            rules,
            prices,
            basic_density_odt_m3=BASIC_DENSITY_ODT_M3,
            moisture_wet_basis=MOISTURE_GREEN,
            chip_yield_fraction=CHIP_YIELD,
            skip_invalid=True,
        )
    taper_warnings = sorted(
        {f"{warning.category.__name__}: {warning.message}" for warning in caught}
    )
    if batch.skipped:
        print(f"Skipped {len(batch.skipped)} sampled trees.", file=sys.stderr)
    trees_by_id = {tree.tree_id: tree for tree in selection.trees}
    logs = logs_frame(batch.logs, trees_by_id)
    residue_rows = residue_frame(batch.residue, trees_by_id)
    tree_summary = _build_tree_summary(selection.trees, batch)
    conservation = _conservation_summary(tree_summary)
    plot_summary = _plot_summary(
        tree_summary, faib, {str(plot["plot_id"]) for plot in selection.plots}, registry.supported_species
    )
    psp = _psp_metrics(plot_summary)
    sort_table, sort_summary = _sort_value_summary(logs, plot_summary)
    residue_summary = _residue_summary(tree_summary, plot_summary)
    print(
        f"Bucked {len(logs)} logs; recovered {conservation['recovered_log_m3']:.3f} m3; "
        f"residue {conservation['residue_m3']:.3f} m3.",
        file=sys.stderr,
    )

    print("Benchmark: VDYP7...", file=sys.stderr)
    vdyp, vdyp_frame = _run_vdyp(femic_root, faib, plot_summary, deadline)
    print("Benchmark: TIPSY...", file=sys.stderr)
    tipsy, tipsy_frame = _run_tipsy(femic_root, faib, plot_summary, deadline)
    print("Benchmark: RSYC...", file=sys.stderr)
    rsyc, rsyc_curves, rsyc_plots = _run_rsyc(
        repo_root, femic_root, faib, plot_summary, deadline
    )
    print("Benchmark: TSA24 biological curves...", file=sys.stderr)
    grade_curves_dir = _resolve_path(args.grade_curves_dir, repo_root)
    plot_attrs = _plot_benchmark_attributes(faib, femic_root)
    if grade_curves_dir.exists():
        grade_benchmark, grade_frame = benchmark_grade_residue(
            attrs=plot_attrs,
            plot_summary=plot_summary,
            logs=logs,
            tree_summary=tree_summary,
            compiled_dir=grade_curves_dir,
        )
        grade_benchmark["compiled_dir"] = str(args.grade_curves_dir)

        print("Benchmark: BTC merchantable volume...", file=sys.stderr)
        btc_volume, btc_volume_frame = btc_volume_benchmark(
            attrs=plot_attrs,
            plot_summary=plot_summary,
            compiled_dir=grade_curves_dir,
        )

        print("Ledger: harvest slash...", file=sys.stderr)
        density_table = load_basic_density_table(
            _resolve_path(args.basic_density, repo_root)
        )
        harvest_residue, harvest_frame = harvest_residue_ledger(
            attrs=plot_attrs,
            plot_summary=plot_summary,
            tree_summary=tree_summary,
            logs=logs,
            compiled_dir=grade_curves_dir,
            density_table=density_table,
            recovery_factor=HARVEST_RECOVERY_FACTOR,
            moisture_green=MOISTURE_GREEN,
            moisture_seasoned=MOISTURE_SEASONED,
            chip_yield=CHIP_YIELD,
        )
    else:
        grade_benchmark = {
            "status": "missing_compiled_curves",
            "compiled_dir": str(grade_curves_dir),
            "note": (
                "Run workflows.national.bucking.compile_tsa24_grade_curves "
                "to build the TSA24 grade/residue table."
            ),
        }
        grade_frame = pd.DataFrame()
        btc_volume = {
            "status": "missing_compiled_curves",
            "compiled_dir": str(grade_curves_dir),
        }
        btc_volume_frame = pd.DataFrame()
        harvest_residue = {
            "status": "missing_compiled_curves",
            "compiled_dir": str(grade_curves_dir),
        }
        harvest_frame = pd.DataFrame()
        density_table = {}

    elapsed = time.monotonic() - started
    summary = {
        "pilot": PILOT_NAME,
        "tsa_number": TSA_NUMBER,
        "tsa_name": TSA_NAME,
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "elapsed_seconds": elapsed,
        "population": population,
        "sample": selection.manifest_record(),
        "skipped": batch.skipped,
        "taper_warnings": taper_warnings,
        "conservation": conservation,
        "psp_volume": psp,
        "sorts": sort_summary,
        "residue": residue_summary,
        "harvest_residue": harvest_residue,
        "benchmarks": {
            "vdyp": vdyp,
            "tipsy": tipsy,
            "tsa24_grades": grade_benchmark,
            "btc_volume": btc_volume,
            "rsyc": rsyc,
        },
        "assumptions": {
            "basic_density_odt_m3": BASIC_DENSITY_ODT_M3,
            "basic_density_source": (
                "species-specific defaults in rules/bc_basic_density.csv "
                "(FVS-BC NBEL/FIA table; not a BC-local sample)"
            ),
            "moisture_green_wet_basis": MOISTURE_GREEN,
            "moisture_seasoned_wet_basis": MOISTURE_SEASONED,
            "chip_yield_fraction": CHIP_YIELD,
            "harvest_recovery_factor": HARVEST_RECOVERY_FACTOR,
        },
        "status": "pilot_complete_with_gaps",
    }

    _write_csv(logs, out_dir / "bucked_logs.csv")
    _write_csv(residue_rows, out_dir / "residue_ledger.csv")
    _write_csv(tree_summary, out_dir / "tree_summary.csv")
    _write_csv(plot_summary, out_dir / "plot_summary.csv")
    _write_csv(pd.DataFrame(selection.plots), out_dir / "sample_plots.csv")
    _write_csv(pd.DataFrame(selection.strata), out_dir / "sample_strata.csv")
    _write_csv(sort_table, out_dir / "sort_value_summary.csv")
    _write_csv(pd.DataFrame(sort_summary.get("by_product", [])), out_dir / "product_summary.csv")
    _write_csv(
        pd.DataFrame(sort_summary.get("by_market_group", [])), out_dir / "market_group_summary.csv"
    )
    if not vdyp_frame.empty:
        _write_csv(vdyp_frame, out_dir / "vdyp_benchmark.csv")
    if not tipsy_frame.empty:
        _write_csv(tipsy_frame, out_dir / "tipsy_benchmark.csv")
    if not grade_frame.empty:
        _write_csv(grade_frame, out_dir / "grade_benchmark.csv")
    (out_dir / "grade_benchmark_summary.json").write_text(
        json.dumps(_jsonable(grade_benchmark), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if not btc_volume_frame.empty:
        _write_csv(btc_volume_frame, out_dir / "btc_volume_benchmark.csv")
    if not harvest_frame.empty:
        _write_csv(harvest_frame, out_dir / "harvest_residue_ledger.csv")
    (out_dir / "harvest_residue_summary.json").write_text(
        json.dumps(_jsonable(harvest_residue), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if not rsyc_curves.empty:
        _write_csv(rsyc_curves, out_dir / "rsyc_curves.csv")
    if not rsyc_plots.empty:
        _write_csv(rsyc_plots, out_dir / "rsyc_benchmark.csv")

    report = _build_report(
        args,
        faib,
        selection,
        tree_summary,
        logs,
        plot_summary,
        conservation,
        psp,
        sort_summary,
        residue_summary,
        harvest_residue,
        harvest_frame,
        vdyp,
        tipsy,
        grade_benchmark,
        btc_volume,
        btc_volume_frame,
        rsyc,
        taper_warnings,
        elapsed,
    )
    (out_dir / "VALIDATION_REPORT.md").write_text(report, encoding="utf-8")
    (out_dir / "HARVEST_RESIDUE.md").write_text(
        _build_harvest_residue_note(harvest_residue, btc_volume),
        encoding="utf-8",
    )
    (out_dir / "validation_summary.json").write_text(
        json.dumps(_jsonable(summary), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    pilot_row = {
        "pilot": PILOT_NAME,
        "tsa": f"TSA{TSA_NUMBER}",
        "dataset": "FAIB PSP",
        "n_trees": len(selection.trees),
        "n_logs": int(len(logs)),
        "log_m3": round(float(conservation["recovered_log_m3"]), 3),
        "residue_m3": round(float(conservation["residue_m3"]), 3),
        "odt": round(float(residue_summary["residue_odt"]), 3),
        "status": "pilot_complete_with_gaps",
        "notes": (
            f"{len(selection.plots)} stratified plots / {len(selection.trees)} trees; "
            "Kozak taper + TIPSY interior dimension sorts; BTC volume benchmarked; "
            "AGB-based harvest-slash ledger with species density/recovery assumptions; "
            "grade shares not validated (dimension-only pilot; D/F zero in TSA24); "
            "BTC mill residue excluded."
        ),
    }
    (out_dir / "pilot_row.json").write_text(
        json.dumps(_jsonable(pilot_row), indent=2) + "\n", encoding="utf-8"
    )
    if args.metadata_csv:
        metadata_path = Path(args.metadata_csv)
        if not metadata_path.is_absolute():
            metadata_path = repo_root / metadata_path
        _update_metadata_csv(metadata_path, pilot_row)
        print(f"Updated {metadata_path}", file=sys.stderr)

    manifest = _build_manifest(
        args,
        faib,
        selection,
        registry,
        rules_path,
        price_path,
        parameter_path,
        crosswalk_path,
        femic_root,
        out_dir,
        conservation,
        psp,
        taper_warnings,
        elapsed,
    )
    (out_dir / "manifest.json").write_text(
        json.dumps(_jsonable(manifest), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"Wrote pilot outputs to {out_dir}", file=sys.stderr)
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the bounded TSA24 bucking pilot and write validation artifacts."
    )
    parser.add_argument(
        "--repo-root",
        default=".",
        help="Repository root (default: current directory)",
    )
    parser.add_argument(
        "--data-root",
        default="data/raw",
        help="Raw data root (default: data/raw)",
    )
    parser.add_argument(
        "--out-dir",
        default="data/derived/national/bucking/tsa24_pilot",
        help="Output directory (default: data/derived/national/bucking/tsa24_pilot)",
    )
    parser.add_argument("--kozak-parameters", default=str(default_kozak_parameter_path()))
    parser.add_argument("--price-profile", default=str(default_price_profile()))
    parser.add_argument("--species-crosswalk", default=str(default_species_crosswalk()))
    parser.add_argument("--rules", default=str(default_rules_path()))
    parser.add_argument(
        "--basic-density",
        default=str(default_basic_density_path()),
        help="Species basic-density table (default: rules/bc_basic_density.csv).",
    )
    parser.add_argument("--femic-root", default=str(default_femic_root()))
    parser.add_argument(
        "--grade-curves-dir",
        default="data/derived/national/bucking/tsa24_grade_curves",
        help=(
            "Compiled TSA24 grade/residue curve directory from "
            "compile_tsa24_grade_curves (default: "
            "data/derived/national/bucking/tsa24_grade_curves)."
        ),
    )
    parser.add_argument("--max-plots", type=int, default=20)
    parser.add_argument("--max-trees-per-plot", type=int, default=15)
    parser.add_argument("--seed", type=int, default=20261115)
    parser.add_argument(
        "--timebox-seconds",
        type=float,
        default=900.0,
        help="Soft time budget for the pilot; optional benchmarks are skipped if exceeded.",
    )
    parser.add_argument(
        "--metadata-csv",
        default="metadata/bucking-pilots.csv",
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
    try:
        summary = run(args)
    except TimeboxExceeded as exc:
        print(f"Timebox exceeded: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "pilot": summary["pilot"],
                "status": summary["status"],
                "n_trees": summary["sample"]["n_trees"],
                "n_logs": summary["sorts"]["n_logs"],
                "recovered_m3": round(summary["conservation"]["recovered_log_m3"], 3),
                "residue_m3": round(summary["conservation"]["residue_m3"], 3),
                "conservation_pass": summary["conservation"]["pass"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
