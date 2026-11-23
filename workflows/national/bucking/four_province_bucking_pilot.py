"""Measured four-province bucking pilot: BC, QC, NB and YT.

Scope
-----
This is a **pilot**, not inventory and not a national supply estimate.  Four
provinces use measured tree lists:

* BC: the ``national_bucking_pilot_v2`` measured aggregate of FAIB TSA24 PSP
  stems (347 retained stems, pooled PHF expansion).
* QC, NB, YT: the ``magplot_tier1_v0`` measured Tier-1 tree lists.

A manageable sample is selected from each province.  QC/NB/YT are
post-stratified within mapped taper species x 5 cm DBH strata: one measured
PHF-weighted median stem is retained per stratum and assigned the stratum's
summed expansion factor.  The selection preserves the expansion total of the
taper-supported, domain-valid measured frame exactly.  BC keeps the already
documented v2 2.5 cm stratum list.

Tapers are local where a published provincial model exists (BC Kozak 2004,
QC Schneider 2013) and explicitly labelled donors elsewhere (NB -> QC
Schneider, YT -> BC Kozak).  Sort rules are the national dimension-only pilot
set; ``grade=not_assigned`` everywhere.  No official grade, policy grade,
market observation, or national claim is made.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from nemora.workflows.taper import (
    KOZAK_SOURCE,
    KOZAK_SPECIES,
    load_kozak_parameters,
    params_for,
)

from .four_province_biomass_allometry import (
    COMPONENT_COLUMNS,
    DEFAULT_RECOVERY_FACTORS,
    REFERENCE_RECOVERY_FACTOR,
    annotate_component_row,
    load_four_province_biomass_allometry,
    tree_components_for_measured_trees,
)
from .national_bucking_pilot_v1 import (
    BASIC_DENSITY_ODT_M3,
    CHIP_YIELD_FRACTION,
    DECIDUOUS_CODES,
    FACILITY_CHAIN_PATH,
    HOG_YIELD_FRACTION,
    MOISTURE_WET_BASIS,
    NFI_TO_BC,
    NFI_TO_QC,
    SORT_RULES_PATH,
    STUMP_HEIGHT_M,
    PilotTaper,
    assert_no_unexpected_deletions,
    buck_tree,
    build_taper,
    load_national_sort_rules,
)
from .pilot_schema import PilotBuckedLog, PilotResidueLedger, PilotTree, SchemaError
from .schneider2013 import SchneiderTaperRegistry, load_taper_registry
from .taper import default_kozak_parameter_path

__all__ = [
    "DEFAULT_OUTPUT_DIR",
    "PILOT_PROVINCES",
    "assert_no_unexpected_deletions",
    "generate_pilot",
]

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT_DIR = (
    REPO_ROOT
    / "data"
    / "derived"
    / "national"
    / "bucking"
    / "four_province_bucking_pilot_v0"
)
MAGPLOT_DIR = REPO_ROOT / "data" / "derived" / "national" / "bucking" / "magplot_tier1_v0"
BC_V2_DIR = (
    REPO_ROOT / "data" / "derived" / "national" / "bucking" / "national_bucking_pilot_v2"
)
BC_V2_TREE_LIST_PATH = BC_V2_DIR / "tree_lists" / "BC.csv"
BC_V2_SELECTION_PATH = BC_V2_DIR / "bc_tree_list_selection.csv"
BC_V2_SUMMARY_PATH = BC_V2_DIR / "summary.json"
QC_TAPER_PATH = (
    REPO_ROOT
    / "workflows"
    / "national"
    / "bucking"
    / "rules"
    / "qc_schneider2013_taper.csv"
)

PILOT_PROVINCES: tuple[str, ...] = ("BC", "QC", "NB", "YT")
PROVINCE_NAMES: dict[str, str] = {
    "BC": "British Columbia",
    "QC": "Quebec",
    "NB": "New Brunswick",
    "YT": "Yukon",
}
MAGPLOT_FILES: dict[str, str] = {
    "QC": "qc_tree_list.csv.gz",
    "NB": "nb_tree_list.csv.gz",
    "YT": "yt_tree_list.csv.gz",
}
TAPER_SPECIES_MAP: dict[str, Mapping[str, str]] = {
    "QC": NFI_TO_QC,
    "NB": NFI_TO_QC,
    "YT": NFI_TO_BC,
}
DBH_STRATUM_WIDTH_CM = 5.0
TREE_LIST_LABEL = "measured_tree_list"
SORT_RULE_SET = "national_pilot_sorts"
TREE_LIST_BASIS: dict[str, str] = {
    "BC": "bc_psp_tsa24_poststratified_measured_v2",
    "QC": "magplot_tier1_qc_psp_poststratified_measured_5cm",
    "NB": "magplot_tier1_nb_cli_poststratified_measured_5cm",
    "YT": "magplot_tier1_yt_psp_poststratified_measured_5cm",
}
SAMPLE_METHOD: dict[str, str] = {
    "BC": "retained_v2_phf_weighted_median_species_bec_dbh2.5cm_stratum",
    "QC": "phf_weighted_median_within_species_dbh5cm_stratum",
    "NB": "phf_weighted_median_within_species_dbh5cm_stratum",
    "YT": "phf_weighted_median_within_species_dbh5cm_stratum",
}

_LABEL_FIELDS = {
    "tree_list_label": TREE_LIST_LABEL,
    "measured_tree_list": True,
    "synthetic": False,
    "observed": False,
    "policy_grade": False,
    "national_claim": False,
}


class _KozakHolder:
    """Minimal holder matching the v1 ``PilotTaper`` adapter contract."""

    __slots__ = ("parameters",)

    def __init__(self, parameters: Any) -> None:
        self.parameters = parameters


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _relative_to_repo(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _write_csv(path: Path, rows: Iterable[Mapping[str, Any]], columns: list[str]) -> int:
    frame = pd.DataFrame(list(rows), columns=columns)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, lineterminator="\n")
    return len(frame)


def _safe_div(numerator: float, denominator: float) -> float:
    if denominator == 0.0 or not math.isfinite(denominator):
        return 0.0
    value = numerator / denominator
    return value if math.isfinite(value) else 0.0


def _weighted_median(values: np.ndarray, weights: np.ndarray) -> float:
    order = np.argsort(values, kind="mergesort")
    sorted_values = values[order]
    sorted_weights = weights[order]
    cumulative = np.cumsum(sorted_weights)
    cutoff = 0.5 * float(cumulative[-1])
    index = int(np.searchsorted(cumulative, cutoff, side="left"))
    return float(sorted_values[min(index, len(sorted_values) - 1)])


def _bc_taper(
    species: str,
    bec_zone: str,
    kozak_parameters: Mapping[tuple[str, str], Any],
) -> PilotTaper:
    parameters = params_for(kozak_parameters, species, bec_zone)
    _kozak_code, proxy = KOZAK_SPECIES.get(species, (species, True))
    return PilotTaper(
        taper_id=f"BC-KOZAK2004-{species}-{bec_zone}",
        jurisdiction="BC",
        species=species,
        source_jurisdiction="BC",
        bec_zone=bec_zone,
        taper_status="local_published",
        proxy=proxy,
        evidence="published_table",
        source=KOZAK_SOURCE,
        model=_KozakHolder(parameters),
    )


# ---------------------------------------------------------------------------
# Source frames
# ---------------------------------------------------------------------------


def _load_bc_frame() -> tuple[pd.DataFrame, dict[str, Any]]:
    if not BC_V2_TREE_LIST_PATH.exists():
        raise FileNotFoundError(f"BC v2 measured tree list not found: {BC_V2_TREE_LIST_PATH}")
    frame = pd.read_csv(BC_V2_TREE_LIST_PATH, low_memory=False)
    required = {
        "tree_id",
        "site_id",
        "species",
        "species_group",
        "dbh_cm",
        "height_m",
        "expansion_factor",
        "status",
        "provenance",
        "bec_subzone",
        "taper_bec_zone",
        "stratum_id",
        "dbh_stratum",
        "stratum_n_trees",
        "stratum_expansion_factor",
        "selection_method",
        "source_tree_id",
        "source_expansion_factor",
        "measured_tree_list",
        "synthetic",
        "model_estimate",
        "national_claim",
    }
    missing = required.difference(frame.columns)
    if missing:
        raise SchemaError(f"BC v2 tree list is missing columns: {sorted(missing)}")
    if frame.empty:
        raise SchemaError("BC v2 tree list is empty")
    if not frame["measured_tree_list"].astype(bool).all():
        raise SchemaError("BC v2 tree list contains non-measured rows")
    if frame["synthetic"].astype(bool).any():
        raise SchemaError("BC v2 tree list contains synthetic rows")
    if (frame["status"].astype(str) != "live").any():
        raise SchemaError("BC v2 tree list contains non-live rows")
    numeric = ["dbh_cm", "height_m", "expansion_factor", "source_expansion_factor"]
    for column in numeric:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    invalid = (
        frame[numeric].isna().any(axis=1)
        | (frame["dbh_cm"] <= 0.0)
        | (frame["height_m"] <= 1.3)
        | (frame["expansion_factor"] <= 0.0)
        | (frame["source_expansion_factor"] <= 0.0)
    )
    if invalid.any():
        raise SchemaError("BC v2 tree list has invalid geometry or expansion rows")
    frame = frame.copy()
    frame["species"] = frame["species"].astype(str).str.strip().str.upper()
    frame["bec_zone"] = frame["taper_bec_zone"].astype(str).str.strip().str.upper()
    frame["bec_subzone"] = frame["bec_subzone"].fillna("").astype(str).str.strip()
    frame["jurisdiction"] = "BC"
    frame["source_program"] = "FAIB_PSP_TSA24"
    frame["source_tree_record_id"] = frame["source_tree_id"].astype(str)
    frame["source_site_id"] = frame["site_id"].astype(str)
    frame["source_plot_id"] = frame["source_plot_id"].astype(str)
    frame["source_expansion"] = frame["source_expansion_factor"]
    frame["dbh_basis"] = frame.get("dbh_basis", "field_measured")
    frame["height_basis"] = frame.get("height_basis", "field_measured")
    frame["tree_list_basis"] = TREE_LIST_BASIS["BC"]
    frame["selection_method"] = SAMPLE_METHOD["BC"]
    summary = json.loads(BC_V2_SUMMARY_PATH.read_text(encoding="utf-8"))
    bc_summary = summary.get("bc_tree_list", {})
    audit = {
        "source_rows": int(bc_summary.get("source_rows", 0)),
        "source_bucked_rows": int(bc_summary.get("source_bucked_rows", 0)),
        "excluded_not_bucked_rows": int(bc_summary.get("excluded_not_bucked_rows", 0)),
        "source_phf_total": float(bc_summary.get("source_phf_total", 0.0)),
        "selected_trees": int(bc_summary.get("selected_trees", len(frame))),
        "selected_phf_total": float(bc_summary.get("selected_phf_total", frame["expansion_factor"].sum())),
        "phf_relative_error": float(bc_summary.get("phf_relative_error", 0.0)),
        "strata": int(bc_summary.get("strata", len(frame))),
    }
    return frame.sort_values("stratum_id", kind="mergesort").reset_index(drop=True), audit


def _magplot_usecols() -> list[str]:
    return [
        "tree_record_id",
        "jurisdiction",
        "source_program",
        "magp_site_id",
        "source_plot",
        "plot_id",
        "plot_type_id",
        "meas_num",
        "meas_year",
        "plot_area_ha",
        "tree_num",
        "species_gsv",
        "species_g",
        "species_s",
        "dbh_cm",
        "dbh_basis",
        "height_m",
        "height_basis",
        "tree_status",
        "live_standing",
        "expansion_factor_stems_ha",
        "measured_tree_list",
        "model_estimate",
        "national_claim",
    ]


def _load_magplot_frame(province: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    path = MAGPLOT_DIR / MAGPLOT_FILES[province]
    if not path.exists():
        raise FileNotFoundError(f"MAGPlot tree list not found: {path}")
    frame = pd.read_csv(
        path,
        usecols=_magplot_usecols(),
        dtype={"magp_site_id": str, "plot_id": str, "tree_record_id": str},
        low_memory=False,
    )
    for column in ("dbh_cm", "height_m", "expansion_factor_stems_ha"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.copy()
    frame["jurisdiction"] = province
    frame["species_code"] = (
        frame["species_g"].fillna("").astype(str).str.strip().str.upper()
        + "."
        + frame["species_s"].fillna("").astype(str).str.strip().str.upper()
    )
    frame["taper_species"] = frame["species_code"].map(TAPER_SPECIES_MAP[province])
    frame["source_tree_record_id"] = frame["tree_record_id"].astype(str)
    frame["source_site_id"] = frame["magp_site_id"].astype(str)
    frame["source_plot_id"] = frame["plot_id"].astype(str)
    frame["source_expansion"] = frame["expansion_factor_stems_ha"]
    frame["tree_list_basis"] = TREE_LIST_BASIS[province]
    frame["selection_method"] = SAMPLE_METHOD[province]

    steps: list[dict[str, Any]] = []

    def record(stage: str, selected: pd.DataFrame, reason: str) -> None:
        steps.append(
            {
                "province": province,
                "stage": stage,
                "reason": reason,
                "n_rows": len(selected),
                "expansion_stems_ha": float(
                    selected["expansion_factor_stems_ha"].fillna(0.0).sum()
                ),
            }
        )

    source_rows = len(frame)
    source_expansion = float(frame["expansion_factor_stems_ha"].fillna(0.0).sum())
    not_live = ~frame["live_standing"].astype("boolean").fillna(False)
    record("excluded", frame.loc[not_live], "not_live_standing")
    frame_live = frame.loc[~not_live].copy()

    bad_dbh = ~(frame_live["dbh_cm"] > 0.0)
    record("excluded", frame_live.loc[bad_dbh], "missing_or_nonpositive_dbh")
    frame_live = frame_live.loc[~bad_dbh].copy()

    bad_height = ~(frame_live["height_m"] > 1.3)
    record("excluded", frame_live.loc[bad_height], "missing_or_nonpositive_height")
    frame_live = frame_live.loc[~bad_height].copy()

    bad_expansion = ~(frame_live["expansion_factor_stems_ha"] > 0.0)
    record(
        "excluded",
        frame_live.loc[bad_expansion],
        "missing_or_nonpositive_expansion",
    )
    frame_live = frame_live.loc[~bad_expansion].copy()

    unsupported = frame_live["taper_species"].isna()
    record("excluded", frame_live.loc[unsupported], "unsupported_taper_species")
    eligible_rows = len(frame_live)
    eligible_expansion = float(
        frame_live["expansion_factor_stems_ha"].sum()
    )
    mapped = frame_live.loc[~unsupported].copy()

    if province in {"QC", "NB"}:
        registry = load_taper_registry(QC_TAPER_PATH)
        domains = {
            model.species: (
                model.dbh_min_cm,
                model.dbh_max_cm,
                model.height_min_m,
                model.height_max_m,
            )
            for model in registry.models()
        }
        outside = mapped.apply(
            lambda row: not (
                domains[row["taper_species"]][0]
                <= float(row["dbh_cm"])
                <= domains[row["taper_species"]][1]
                and domains[row["taper_species"]][2]
                <= float(row["height_m"])
                <= domains[row["taper_species"]][3]
            ),
            axis=1,
        )
        record("excluded", mapped.loc[outside], "outside_schneider2013_domain")
        mapped = mapped.loc[~outside].copy()

    mapped = mapped.sort_values("source_tree_record_id", kind="mergesort").reset_index(
        drop=True
    )
    audit = {
        "source_rows": source_rows,
        "source_expansion_stems_ha": source_expansion,
        "steps": steps,
        "frame_rows": len(mapped),
        "frame_expansion_stems_ha": float(
            mapped["expansion_factor_stems_ha"].sum()
        ),
        "eligible_rows": eligible_rows,
        "eligible_expansion_stems_ha": eligible_expansion,
        "source_program": str(mapped["source_program"].iloc[0])
        if not mapped.empty
        else "",
        "source_file": _relative_to_repo(path),
        "source_hash": _sha256(path),
    }
    return mapped, audit


# ---------------------------------------------------------------------------
# Sample selection
# ---------------------------------------------------------------------------


def _dbh_stratum(dbh_cm: float, width: float) -> str:
    lower = math.floor(float(dbh_cm) / width) * width
    return f"{lower:g}-{lower + width:g}"


def _select_magplot_strata(frame: pd.DataFrame, province: str) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    if frame.empty:
        raise SchemaError(f"no taper-supported measured trees for {province}")
    work = frame.copy()
    work["dbh_stratum"] = work["dbh_cm"].map(
        lambda value: _dbh_stratum(value, DBH_STRATUM_WIDTH_CM)
    )
    work["stratum_id"] = (
        work["taper_species"].astype(str)
        + "|"
        + work["dbh_stratum"].astype(str)
    )
    selected: list[dict[str, Any]] = []
    strata: list[dict[str, Any]] = []
    for stratum_id, group in work.groupby("stratum_id", sort=True):
        values = group["dbh_cm"].to_numpy(dtype=float)
        weights = group["expansion_factor_stems_ha"].to_numpy(dtype=float)
        median = _weighted_median(values, weights)
        ordered = group.assign(
            _distance=np.abs(group["dbh_cm"].to_numpy(dtype=float) - median),
            _height_distance=np.abs(
                group["height_m"].to_numpy(dtype=float)
                - _weighted_median(
                    group["height_m"].to_numpy(dtype=float), weights
                )
            ),
        ).sort_values(
            ["_distance", "_height_distance", "source_tree_record_id"],
            kind="mergesort",
        )
        row = ordered.iloc[0]
        expansion = float(group["expansion_factor_stems_ha"].sum())
        selected.append(
            {
                "tree_id": f"{province}:{row['source_tree_record_id']}",
                "source_tree_record_id": str(row["source_tree_record_id"]),
                "source_site_id": str(row["source_site_id"]),
                "source_plot_id": str(row["source_plot_id"]),
                "site_id": str(row["source_site_id"]),
                "plot_id": str(row["source_plot_id"]),
                "species": str(row["taper_species"]).upper(),
                "species_nfi": str(row["species_code"]).upper(),
                "species_group": (
                    "deciduous"
                    if str(row["taper_species"]).upper() in DECIDUOUS_CODES
                    else "conifer"
                ),
                "dbh_cm": float(row["dbh_cm"]),
                "height_m": float(row["height_m"]),
                "dbh_basis": str(row.get("dbh_basis", "unknown")),
                "height_basis": str(row.get("height_basis", "unknown")),
                "expansion_factor": expansion,
                "source_expansion_factor": float(
                    row["expansion_factor_stems_ha"]
                ),
                "jurisdiction": province,
                "source_program": str(row["source_program"]),
                "provenance": (
                    f"magplot_tier1_v0 measured {row['source_program']} latest visit; "
                    f"source_tree_record_id={row['source_tree_record_id']}; "
                    f"stratum={stratum_id}; selected as PHF-weighted median stem; "
                    "pooled stratum expansion; no synthetic geometry"
                ),
                "evidence": "published_table",
                "status": "live",
                "defect_pct": 0.0,
                "bec_zone": "NA",
                "bec_subzone": "",
                "tree_list_basis": TREE_LIST_BASIS[province],
                "selection_method": SAMPLE_METHOD[province],
                "stratum_id": stratum_id,
                "dbh_stratum": str(row["dbh_stratum"]),
                "stratum_n_trees": len(group),
                "stratum_expansion_factor": expansion,
            }
        )
        strata.append(
            {
                "province": province,
                "stratum_id": stratum_id,
                "taper_species": str(row["taper_species"]).upper(),
                "dbh_stratum": str(row["dbh_stratum"]),
                "source_n_trees": len(group),
                "source_expansion_factor": expansion,
                "selected_tree_id": f"{province}:{row['source_tree_record_id']}",
                "selected_source_tree_id": str(row["source_tree_record_id"]),
                "selected_dbh_cm": float(row["dbh_cm"]),
                "selected_height_m": float(row["height_m"]),
                "selected_expansion_factor": expansion,
                "selection_method": SAMPLE_METHOD[province],
            }
        )
    result = pd.DataFrame(selected)
    if result.empty:
        raise SchemaError(f"sample selection produced no rows for {province}")
    return result.reset_index(drop=True), strata


def _select_bc_sample(frame: pd.DataFrame) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    work = frame.copy()
    work["site_id"] = work["site_id"].astype(str)
    work["tree_id"] = "BC:" + work["source_tree_record_id"].astype(str)
    work["species_nfi"] = work["species"].astype(str)
    work["source_expansion_factor"] = work["source_expansion"]
    work["expansion_factor"] = work["expansion_factor"].astype(float)
    work["dbh_basis"] = work["dbh_basis"].fillna("field_measured")
    work["height_basis"] = work["height_basis"].fillna("field_measured")
    work["defect_pct"] = 0.0
    work["stratum_expansion_factor"] = work["expansion_factor"]
    strata = [
        {
            "province": "BC",
            "stratum_id": str(row["stratum_id"]),
            "taper_species": str(row["species"]),
            "dbh_stratum": str(row["dbh_stratum"]),
            "source_n_trees": int(row["stratum_n_trees"]),
            "source_expansion_factor": float(row["stratum_expansion_factor"]),
            "selected_tree_id": f"BC:{row['source_tree_record_id']}",
            "selected_source_tree_id": str(row["source_tree_record_id"]),
            "selected_dbh_cm": float(row["dbh_cm"]),
            "selected_height_m": float(row["height_m"]),
            "selected_expansion_factor": float(row["expansion_factor"]),
            "selection_method": SAMPLE_METHOD["BC"],
        }
        for _, row in work.iterrows()
    ]
    return work.reset_index(drop=True), strata


# ---------------------------------------------------------------------------
# Taper dispatch
# ---------------------------------------------------------------------------


def _taper_for_tree(
    tree: PilotTree,
    province: str,
    kozak_parameters: Mapping[tuple[str, str], Any],
    qc_registry: SchneiderTaperRegistry,
    bc_bec: Mapping[str, str],
) -> PilotTaper:
    if province == "BC":
        return _bc_taper(tree.species, bc_bec[tree.tree_id], kozak_parameters)
    return build_taper(province, tree.species, qc_registry, kozak_parameters)


# ---------------------------------------------------------------------------
# Bucking and summaries
# ---------------------------------------------------------------------------


def _expand_extras(
    trees: Sequence[PilotTree],
    logs: Sequence[PilotBuckedLog],
    residues: Sequence[PilotResidueLedger],
) -> dict[str, dict[str, Any]]:
    return {tree.tree_id: {} for tree in trees}


def _province_rows(
    trees: Sequence[PilotTree],
    logs: Sequence[PilotBuckedLog],
    residues: Sequence[PilotResidueLedger],
    extras: Mapping[str, Mapping[str, Any]],
    components: Mapping[str, Any],
) -> list[dict[str, Any]]:
    logs_by: dict[str, list[PilotBuckedLog]] = {}
    for log in logs:
        logs_by.setdefault(log.jurisdiction, []).append(log)
    residue_by: dict[str, list[PilotResidueLedger]] = {}
    for row in residues:
        residue_by.setdefault(row.jurisdiction, []).append(row)
    rows: list[dict[str, Any]] = []
    for province in PILOT_PROVINCES:
        province_trees = [tree for tree in trees if tree.jurisdiction == province]
        province_logs = logs_by.get(province, [])
        province_residues = residue_by.get(province, [])
        products: dict[str, float] = {}
        products_expanded: dict[str, float] = {}
        for log in province_logs:
            weight = float(extras[log.tree_id]["expansion_factor"])
            products[log.product] = products.get(log.product, 0.0) + log.volume_m3
            products_expanded[log.product] = (
                products_expanded.get(log.product, 0.0) + log.volume_m3 * weight
            )
        taper_total = sum(row.taper_total_m3 for row in province_residues)
        recovered = sum(row.recovered_log_m3 for row in province_residues)
        residue_m3 = sum(row.residue_volume_m3 for row in province_residues)
        residue_odt = sum(row.residue_odt for row in province_residues)
        taper_total_expanded = sum(
            row.taper_total_m3 * float(extras[row.tree_id]["expansion_factor"])
            for row in province_residues
        )
        recovered_expanded = sum(
            row.recovered_log_m3 * float(extras[row.tree_id]["expansion_factor"])
            for row in province_residues
        )
        residue_m3_expanded = sum(
            row.residue_volume_m3 * float(extras[row.tree_id]["expansion_factor"])
            for row in province_residues
        )
        residue_odt_expanded = sum(
            row.residue_odt * float(extras[row.tree_id]["expansion_factor"])
            for row in province_residues
        )
        stem_component = sum(
            row.residue_odt * float(extras[row.tree_id]["expansion_factor"])
            for row in province_residues
        )
        branch_component = sum(
            components[row.tree_id].branch_odt
            * float(extras[row.tree_id]["expansion_factor"])
            for row in province_residues
        )
        foliage_component = sum(
            components[row.tree_id].foliage_odt
            * float(extras[row.tree_id]["expansion_factor"])
            for row in province_residues
        )
        branch_foliage_component = branch_component + foliage_component
        total_component = stem_component + branch_foliage_component
        scenario_components = {
            f"recoverable_residue_odt_r{round(factor * 100):03d}_expanded": (
                total_component * factor
            )
            for factor in DEFAULT_RECOVERY_FACTORS
        }
        local_trees = sum(
            1
            for tree in province_trees
            if extras[tree.tree_id]["taper_status"] == "local_published"
        )
        donor_trees = len(province_trees) - local_trees
        local_expansion = sum(
            float(extras[tree.tree_id]["expansion_factor"])
            for tree in province_trees
            if extras[tree.tree_id]["taper_status"] == "local_published"
        )
        total_expansion = sum(
            float(extras[tree.tree_id]["expansion_factor"])
            for tree in province_trees
        )
        rows.append(
            {
                "province": province,
                "province_name": PROVINCE_NAMES[province],
                "n_trees": len(province_trees),
                "n_logs": len(province_logs),
                "n_species": len({tree.species for tree in province_trees}),
                "n_measured_trees": sum(
                    1 for tree in province_trees if extras[tree.tree_id]["measured_tree_list"]
                ),
                "n_synthetic_trees": sum(
                    1 for tree in province_trees if extras[tree.tree_id]["synthetic"]
                ),
                "expansion_factor_total": total_expansion,
                "taper_total_m3": taper_total,
                "recovered_gross_m3": recovered,
                "recovered_net_m3": sum(log.net_volume_m3 for log in province_logs),
                "defect_m3": sum(log.defect_deduction_m3 for log in province_logs),
                "residue_m3": residue_m3,
                "residue_odt": residue_odt,
                "green_tonnes": sum(row.green_tonnes for row in province_residues),
                "chip_green_tonnes": sum(row.chip_green_tonnes for row in province_residues),
                "hog_green_tonnes": sum(row.hog_green_tonnes for row in province_residues),
                "sawlog_m3": products.get("sawlog", 0.0),
                "pulpwood_m3": products.get("pulpwood", 0.0),
                "other_m3": products.get("other", 0.0),
                "value_cad": sum(log.value_cad for log in province_logs),
                "taper_total_m3_expanded": taper_total_expanded,
                "recovered_gross_m3_expanded": recovered_expanded,
                "residue_m3_expanded": residue_m3_expanded,
                "residue_odt_expanded": residue_odt_expanded,
                "stem_residue_odt_expanded": stem_component,
                "branch_odt_expanded": branch_component,
                "foliage_odt_expanded": foliage_component,
                "branch_foliage_odt_expanded": branch_foliage_component,
                "total_residue_odt_expanded": total_component,
                **scenario_components,
                "branch_foliage_odt_per_recovered_m3": _safe_div(
                    branch_foliage_component, recovered_expanded
                ),
                "total_residue_odt_per_recovered_m3": _safe_div(
                    total_component, recovered_expanded
                ),
                "total_residue_recoverable_odt_per_recovered_m3_r035": _safe_div(
                    total_component * 0.35, recovered_expanded
                ),
                "total_residue_recoverable_odt_per_recovered_m3_r060": _safe_div(
                    total_component * 0.60, recovered_expanded
                ),
                "total_residue_recoverable_odt_per_recovered_m3_r075": _safe_div(
                    total_component * 0.75, recovered_expanded
                ),
                "n_allometry_species_specific_trees": sum(
                    1
                    for tree in province_trees
                    if components[tree.tree_id].species_specific
                ),
                "n_allometry_proxy_trees": sum(
                    1
                    for tree in province_trees
                    if components[tree.tree_id].proxy
                ),
                "sawlog_m3_expanded": products_expanded.get("sawlog", 0.0),
                "pulpwood_m3_expanded": products_expanded.get("pulpwood", 0.0),
                "other_m3_expanded": products_expanded.get("other", 0.0),
                "sawlog_share_of_recovered": _safe_div(
                    products_expanded.get("sawlog", 0.0), recovered_expanded
                ),
                "pulpwood_share_of_recovered": _safe_div(
                    products_expanded.get("pulpwood", 0.0), recovered_expanded
                ),
                "other_share_of_recovered": _safe_div(
                    products_expanded.get("other", 0.0), recovered_expanded
                ),
                "solid_share_of_recovered": _safe_div(
                    products_expanded.get("sawlog", 0.0)
                    + products_expanded.get("other", 0.0),
                    recovered_expanded,
                ),
                "residue_m3_per_recovered_m3": _safe_div(
                    residue_m3_expanded, recovered_expanded
                ),
                "residue_odt_per_recovered_m3": _safe_div(
                    residue_odt_expanded, recovered_expanded
                ),
                "product_residue_ratio_m3_per_odt": _safe_div(
                    recovered_expanded, residue_odt_expanded
                ),
                "sawlog_share_unweighted": _safe_div(
                    products.get("sawlog", 0.0), recovered
                ),
                "pulpwood_share_unweighted": _safe_div(
                    products.get("pulpwood", 0.0), recovered
                ),
                "other_share_unweighted": _safe_div(
                    products.get("other", 0.0), recovered
                ),
                "solid_share_unweighted": _safe_div(
                    products.get("sawlog", 0.0) + products.get("other", 0.0),
                    recovered,
                ),
                "residue_m3_per_recovered_m3_unweighted": _safe_div(
                    residue_m3, recovered
                ),
                "local_taper_trees": local_trees,
                "donor_taper_trees": donor_trees,
                "local_taper_expansion_share": _safe_div(
                    local_expansion, total_expansion
                ),
                "donor_taper_expansion_share": _safe_div(
                    total_expansion - local_expansion, total_expansion
                ),
                "volume_balance_m3": abs(
                    recovered_expanded + residue_m3_expanded - taper_total_expanded
                ),
                "max_tree_balance_error_m3": max(
                    (row.balance_error_m3 for row in province_residues), default=0.0
                ),
                "taper_status": (
                    "local_published"
                    if donor_trees == 0
                    else "donor_published"
                ),
                "taper_jurisdictions": ";".join(
                    sorted(
                        {
                            str(extras[tree.tree_id]["taper_jurisdiction"])
                            for tree in province_trees
                        }
                    )
                ),
                "tree_list_basis": TREE_LIST_BASIS[province],
                "tree_list_label": TREE_LIST_LABEL,
                "measured_tree_list": True,
                "model_estimate": True,
                "synthetic": False,
                "observed": False,
                "policy_grade": False,
                "national_claim": False,
            }
        )
    return rows


def _coefficient_rows(
    province_rows: Sequence[Mapping[str, Any]],
    sample_audit: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    facility = pd.read_csv(FACILITY_CHAIN_PATH)
    facility_by = {
        str(row["province"]).strip().upper(): row for _, row in facility.iterrows()
    }
    audit_by = {str(row["province"]): row for row in sample_audit}
    rows: list[dict[str, Any]] = []
    for province_row in province_rows:
        province = str(province_row["province"])
        record = facility_by.get(province)
        if record is None:
            raise SchemaError(f"facility product chain has no row for {province}")
        harvest = float(record["harvest_m3"])
        sawlog_facility = _safe_div(float(record["sawlog_feedstock_m3"]), harvest)
        pulp_facility = _safe_div(float(record["pulp_feedstock_m3"]), harvest)
        solid_facility = _safe_div(
            float(record["sawlog_feedstock_m3"])
            + float(record["panel_feedstock_m3"]),
            harvest,
        )
        residue_facility = _safe_div(
            float(record["harvest_residue_gross_odt"]), harvest
        )
        model_sawlog = float(province_row["sawlog_share_of_recovered"])
        model_pulp = float(province_row["pulpwood_share_of_recovered"])
        model_other = float(province_row["other_share_of_recovered"])
        model_solid = float(province_row["solid_share_of_recovered"])
        model_residue = float(province_row["residue_odt_per_recovered_m3"])
        model_branch_foliage = float(
            province_row["branch_foliage_odt_per_recovered_m3"]
        )
        model_total_residue = float(
            province_row["total_residue_odt_per_recovered_m3"]
        )
        model_total_recoverable = {
            factor: float(
                province_row[
                    "total_residue_recoverable_odt_per_recovered_m3_"
                    f"r{round(factor * 100):03d}"
                ]
            )
            for factor in DEFAULT_RECOVERY_FACTORS
        }
        total_ratio = _safe_div(model_total_residue, residue_facility)
        solid_ratio = _safe_div(model_solid, solid_facility)
        residue_ratio = _safe_div(model_residue, residue_facility)
        if 0.8 <= solid_ratio <= 1.2 and 0.8 <= residue_ratio <= 1.2:
            status = "within_20pct"
        else:
            status = "outside_20pct"
        if 0.8 <= solid_ratio <= 1.2 and 0.8 <= total_ratio <= 1.2:
            total_status = "within_20pct"
        else:
            total_status = "outside_20pct"
        audit = audit_by[province]
        rows.append(
            {
                "province": province,
                "province_name": PROVINCE_NAMES[province],
                "model_n_trees": int(province_row["n_trees"]),
                "model_recovered_m3_expanded": float(
                    province_row["recovered_gross_m3_expanded"]
                ),
                "model_sawlog_share": model_sawlog,
                "model_pulpwood_share": model_pulp,
                "model_other_share": model_other,
                "model_solid_share": model_solid,
                "model_residue_m3_per_recovered_m3": float(
                    province_row["residue_m3_per_recovered_m3"]
                ),
                "model_residue_odt_per_recovered_m3": model_residue,
                "model_stem_residue_odt_per_recovered_m3": model_residue,
                "model_branch_foliage_odt_per_recovered_m3": model_branch_foliage,
                "model_total_residue_gross_odt_per_recovered_m3": (
                    model_total_residue
                ),
                "model_total_residue_recoverable_odt_per_recovered_m3_r035": (
                    model_total_recoverable[0.35]
                ),
                "model_total_residue_recoverable_odt_per_recovered_m3_r060": (
                    model_total_recoverable[0.60]
                ),
                "model_total_residue_recoverable_odt_per_recovered_m3_r075": (
                    model_total_recoverable[0.75]
                ),
                "model_recovery_scenarios": "0.35|0.60|0.75",
                "model_recovery_reference_factor": REFERENCE_RECOVERY_FACTOR,
                "model_recovery_factor_calibrated": False,
                "model_allometry_proxy_trees": int(
                    province_row["n_allometry_proxy_trees"]
                ),
                "model_allometry_species_specific_trees": int(
                    province_row["n_allometry_species_specific_trees"]
                ),
                "facility_harvest_m3": harvest,
                "facility_sawlog_feedstock_share": sawlog_facility,
                "facility_pulp_feedstock_share": pulp_facility,
                "facility_solid_feedstock_share": solid_facility,
                "facility_harvest_residue_odt_per_m3": residue_facility,
                "sawlog_share_model_over_facility": _safe_div(
                    model_sawlog, sawlog_facility
                ),
                "solid_share_model_over_facility": solid_ratio,
                "residue_odt_model_over_facility": residue_ratio,
                "total_residue_odt_model_over_facility": total_ratio,
                "comparison_status": status,
                "comparison_status_total_residue": total_status,
                "facility_coefficient_tier": str(record.get("coefficient_tier", "")),
                "taper_status": province_row["taper_status"],
                "taper_jurisdictions": province_row["taper_jurisdictions"],
                "tree_list_basis": province_row["tree_list_basis"],
                "tree_list_label": TREE_LIST_LABEL,
                "model_expansion_share_of_live_measured_frame": _safe_div(
                    float(province_row["expansion_factor_total"]),
                    float(audit.get("source_expansion_stems_ha", 0.0)),
                ),
                "model_expansion_share_of_eligible_measured_frame": _safe_div(
                    float(province_row["expansion_factor_total"]),
                    float(audit.get("eligible_expansion_stems_ha", 0.0)),
                ),
                "model_solid_includes_other_roundwood": True,
                "measured_tree_list": True,
                "model_estimate": True,
                "synthetic": False,
                "observed": False,
                "policy_grade": False,
                "national_claim": False,
                "notes": (
                    "Model shares are expansion-weighted recovered-log volume "
                    "from the measured sample.  Facility feedstock shares are "
                    "from facility_product_chain_v1.  Harvest residue and "
                    "facility process residue are different streams; only the "
                    "harvest-residue coefficient is compared.  Unsupported "
                    "measured species are excluded and reported in the audit, "
                    "so this is a taper-covered-species comparison, not a "
                    "provincial supply estimate."
                ),
            }
        )
    if len(rows) != len(PILOT_PROVINCES):
        raise SchemaError(
            f"expected {len(PILOT_PROVINCES)} coefficient rows, got {len(rows)}"
        )
    return rows


def _allometry_coverage_rows(
    trees: Sequence[PilotTree],
    extras: Mapping[str, Mapping[str, Any]],
    components: Mapping[str, Any],
) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str, str], list[PilotTree]] = {}
    for tree in trees:
        buckets.setdefault(
            (
                tree.jurisdiction,
                tree.species,
                str(extras[tree.tree_id]["species_nfi"]),
            ),
            [],
        ).append(tree)
    rows: list[dict[str, Any]] = []
    for (province, species, species_nfi), group in sorted(buckets.items()):
        component = components[group[0].tree_id]
        rows.append(
            {
                "province": province,
                "species": species,
                "species_nfi": species_nfi,
                "n_trees": len(group),
                "expansion_factor_total": sum(
                    float(extras[tree.tree_id]["expansion_factor"])
                    for tree in group
                ),
                "branch_equation_id": component.branch_equation.equation_id,
                "foliage_equation_id": component.foliage_equation.equation_id,
                "equation_species": component.equation_species,
                "reference": component.reference,
                "species_specific": component.species_specific,
                "proxy": component.proxy,
                "proxy_reason": component.branch_equation.proxy_reason,
                "source": component.branch_equation.source_dataset,
                "source_licence": component.branch_equation.source_licence,
                "dbh_min_cm": min(tree.dbh_cm for tree in group),
                "dbh_max_cm": max(tree.dbh_cm for tree in group),
                "height_min_m": min(tree.height_m for tree in group),
                "height_max_m": max(tree.height_m for tree in group),
                "equation_dbh_min_cm": component.branch_equation.dbh_min_cm,
                "equation_dbh_max_cm": component.branch_equation.dbh_max_cm,
                "equation_height_min_m": component.branch_equation.height_min_m,
                "equation_height_max_m": component.branch_equation.height_max_m,
                "domain_basis": component.branch_equation.domain_basis,
                "model_estimate": True,
                "observed": False,
                "policy_grade": False,
                "national_claim": False,
            }
        )
    if not rows:
        raise SchemaError("allometry coverage has no rows")
    return rows


def _residue_scenario_rows(
    province_summary: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for province_row in province_summary:
        recovered = float(province_row["recovered_gross_m3_expanded"])
        total = float(province_row["total_residue_odt_expanded"])
        row = {
            "province": province_row["province"],
            "province_name": province_row["province_name"],
            "n_trees": int(province_row["n_trees"]),
            "recovered_gross_m3_expanded": recovered,
            "stem_residue_odt_expanded": float(
                province_row["stem_residue_odt_expanded"]
            ),
            "branch_odt_expanded": float(province_row["branch_odt_expanded"]),
            "foliage_odt_expanded": float(
                province_row["foliage_odt_expanded"]
            ),
            "branch_foliage_odt_expanded": float(
                province_row["branch_foliage_odt_expanded"]
            ),
            "total_residue_odt_expanded": total,
            "total_residue_gross_odt_per_recovered_m3": _safe_div(
                total, recovered
            ),
            "recovery_factor_reference": REFERENCE_RECOVERY_FACTOR,
            "recovery_factor_calibrated": False,
            "recovery_scenario_basis": "0.35|0.60|0.75; no single factor adopted",
            "model_estimate": True,
            "observed": False,
            "policy_grade": False,
            "national_claim": False,
        }
        for factor in DEFAULT_RECOVERY_FACTORS:
            suffix = f"r{round(factor * 100):03d}"
            row[f"recovery_factor_{suffix}"] = factor
            row[f"recoverable_residue_odt_{suffix}_expanded"] = total * factor
            row[f"recoverable_residue_odt_per_recovered_m3_{suffix}"] = _safe_div(
                total * factor, recovered
            )
        rows.append(row)
    if len(rows) != len(PILOT_PROVINCES):
        raise SchemaError(
            f"expected {len(PILOT_PROVINCES)} residue scenario rows, got {len(rows)}"
        )
    return rows


def _taper_coverage_rows(
    trees: Sequence[PilotTree],
    extras: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str], list[PilotTree]] = {}
    for tree in trees:
        buckets.setdefault((tree.jurisdiction, tree.species), []).append(tree)
    rows: list[dict[str, Any]] = []
    for (province, species), group in sorted(buckets.items()):
        first = extras[group[0].tree_id]
        rows.append(
            {
                "province": province,
                "species": species,
                "n_trees": len(group),
                "expansion_factor_total": sum(
                    float(extras[tree.tree_id]["expansion_factor"]) for tree in group
                ),
                "taper_id": first["taper_id"],
                "taper_jurisdiction": first["taper_jurisdiction"],
                "taper_status": first["taper_status"],
                "taper_proxy": bool(first["taper_proxy"]),
                "taper_evidence": first["taper_evidence"],
                "dbh_min_cm": min(tree.dbh_cm for tree in group),
                "dbh_max_cm": max(tree.dbh_cm for tree in group),
                "height_min_m": min(tree.height_m for tree in group),
                "height_max_m": max(tree.height_m for tree in group),
                "measured_tree_list": True,
                "synthetic": False,
                "model_estimate": True,
                "national_claim": False,
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Output documents
# ---------------------------------------------------------------------------


def _schema_rows() -> list[dict[str, str]]:
    return [
        {
            "column": "tree_list_label",
            "value": TREE_LIST_LABEL,
            "meaning": "Measured field tree list; no synthetic geometry.",
        },
        {
            "column": "model_estimate",
            "value": "true on bucked outputs",
            "meaning": "Log and residue rows are model outputs from a taper and dimension sorts.",
        },
        {
            "column": "national_claim",
            "value": "false",
            "meaning": "The pilot does not support a national supply or grade claim.",
        },
        {
            "column": "taper_status",
            "value": "local_published | donor_published",
            "meaning": "BC and QC use provincial published tapers; NB and YT use labelled donors.",
        },
        {
            "column": "grade_status",
            "value": "not_assigned",
            "meaning": "Dimension-only sorts; no official or policy grade.",
        },
    ]


def _report_markdown(summary: Mapping[str, Any]) -> str:
    lines = [
        "# Four-province measured bucking pilot (BC, QC, NB, YT)",
        "",
        ("**Pilot only.**  Measured tree lists; published local/donor tapers; "
        "national dimension-only sorts; `grade=not_assigned`; no synthetic "
        "trees; no national claim."),
        "",
        "## Sample and expansion",
        "",
        "| Province | Source rows | Eligible rows | Eligible expansion (stems/ha) | Taper-supported frame | Selected trees | Selected expansion (stems/ha) | Frame expansion (stems/ha) | Conservation error |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summary["sample_audit"]:
        lines.append(
            "| {province} | {source_rows:,} | {eligible_rows:,} | "
            "{eligible_expansion:,.3f} | {frame_rows:,} | {selected_trees:,} | "
            "{selected_expansion:,.3f} | {frame_expansion:,.3f} | {error:.3e} |".format(
                province=row["province"],
                source_rows=row["source_rows"],
                eligible_rows=row["eligible_rows"],
                eligible_expansion=row["eligible_expansion_stems_ha"],
                frame_rows=row["frame_rows"],
                selected_trees=row["selected_trees"],
                selected_expansion=row["selected_expansion_stems_ha"],
                frame_expansion=row["frame_expansion_stems_ha"],
                error=row["expansion_conservation_error"],
            )
        )
    lines.extend(
        [
            "",
            ("QC/NB/YT use one measured PHF-weighted median stem per mapped "
            "taper species x 5 cm DBH stratum; the retained stem receives the "
            "stratum expansion total.  BC retains the documented v2 measured "
            "2.5 cm BEC x species strata.  Expansion is preserved exactly "
            "within each taper-supported, domain-valid frame."),
            "",
            "## Taper coverage",
            "",
            "| Province | Taper | Source | Status | Trees | Local expansion share | Proxy species |",
            "|---|---|---|---|---:|---:|---|",
        ]
    )
    for row in summary["province_summary"]:
        coverage = [
            item
            for item in summary["taper_coverage"]
            if item["province"] == row["province"]
        ]
        taper_ids = "; ".join(sorted({item["taper_id"] for item in coverage}))
        sources = "; ".join(sorted({item["taper_jurisdiction"] for item in coverage}))
        proxies = ", ".join(
            sorted({item["species"] for item in coverage if item["taper_proxy"]})
        ) or "none"
        lines.append(
            f"| {row['province']} | {taper_ids} | {sources} | "
            f"{row['taper_status']} | {row['n_trees']:,} | "
            f"{row['local_taper_expansion_share']:.3f} | {proxies} |"
        )
    lines.extend(
        [
            "",
            ("Local tapers: BC Kozak (2004) Version 6; QC Schneider (2013).  "
            "Donor tapers: NB uses QC Schneider; YT uses BC Kozak.  Species "
            "proxies encoded by the project mapping are flagged with "
            "`taper_proxy=True`."),
            "",
            "## Logs, residue and solid share by province",
            "",
            "| Province | Trees | Logs | Recovered (m3/ha) | Residue (m3/ha) | Residue (ODT/ha) | Sawlog share | Pulpwood share | Other share | Solid share |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in summary["province_summary"]:
        lines.append(
            "| {province} | {n_trees:,} | {n_logs:,} | {recovered:,.2f} | "
            "{residue:,.2f} | {odt:,.2f} | {saw:.3f} | {pulp:.3f} | "
            "{other:.3f} | {solid:.3f} |".format(
                province=row["province"],
                n_trees=row["n_trees"],
                n_logs=row["n_logs"],
                recovered=row["recovered_gross_m3_expanded"],
                residue=row["residue_m3_expanded"],
                odt=row["residue_odt_expanded"],
                saw=row["sawlog_share_of_recovered"],
                pulp=row["pulpwood_share_of_recovered"],
                other=row["other_share_of_recovered"],
                solid=row["solid_share_of_recovered"],
            )
        )
    lines.extend(
        [
            "",
            ("Shares are expansion-weighted recovered-log volume.  Solid share "
            "is sawlog + other roundwood.  Residue is taper total minus "
            "recovered gross logs and includes stump-above-base, top and "
            "unmerchantable sections."),
            "",
            "## Facility coefficient fit",
            "",
            "| Province | Model solid | Facility solid | Solid ratio | Model residue ODT/m3 | Facility residue ODT/m3 | Residue ratio | Eligible expansion share | Status |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    for row in summary["coefficient_comparison"]:
        lines.append(
            "| {province} | {ms:.3f} | {fs:.3f} | {sr:.3f} | {mr:.4f} | "
            "{fr:.4f} | {rr:.3f} | {es:.3f} | {status} |".format(
                province=row["province"],
                ms=row["model_solid_share"],
                fs=row["facility_solid_feedstock_share"],
                sr=row["solid_share_model_over_facility"],
                mr=row["model_residue_odt_per_recovered_m3"],
                fr=row["facility_harvest_residue_odt_per_m3"],
                rr=row["residue_odt_model_over_facility"],
                es=row["model_expansion_share_of_eligible_measured_frame"],
                status=row["comparison_status"],
            )
        )
    lines.extend(
        [
            "",
            ("Facility coefficients are feedstock shares from "
            "`facility_product_chain_v1`; harvest residue and facility process "
            "residue are different streams.  The comparison is a screening "
            "check, not calibration."),
            "",
            "## Evidence and limits",
            "",
            "- `measured_tree_list=true` for all four provinces; `synthetic=false`.",
            "- `model_estimate=true` on log and residue outputs; `national_claim=false`.",
            ("- `taper_evidence=published_table`; residue density, moisture and "
            "chip/hog yields are labelled assumptions."),
            ("- Unsupported measured species and out-of-domain stems are "
            "excluded and counted in `sample_audit.csv`; they are not imputed."),
            ("- This is not inventory, not an operational supply estimate, and "
            "not an official grade."),
            "",
        ]
    )
    return "\n".join(lines)


def _readme() -> str:
    return """# Four-province measured bucking pilot v0

Pilot bucking for **BC, QC, NB and YT only**.  The other nine provinces are
deliberately excluded.  All four tree lists are measured; no synthetic trees
are introduced.

| File | Content |
|---|---|
| `tree_list.csv` | Selected measured stems with provenance and expansion |
| `sample_selection.csv` | Per-stratum source count and retained stem audit |
| `sample_audit.csv` | Source, excluded and selected expansion by province |
| `bucked_logs.csv` | Recovered dimension-sorted logs |
| `residue_ledger.csv` | Per-stem residue and chip/hog accounting |
| `province_summary.csv` | Logs, residue and product shares by province |
| `taper_dispatch.csv` | Per-tree taper, local/donor status and proxy flag |
| `taper_coverage.csv` | Taper coverage by province and species |
| `coefficient_comparison.csv` | Model versus facility feedstock coefficients |
| `summary.json`, `report.md`, `manifest.json` | Machine-readable audit and report |

Reproduce:

```bash
.venv/bin/python -m workflows.national.bucking.four_province_bucking_pilot \\
  --out-dir data/derived/national/bucking/four_province_bucking_pilot_v0
```
"""


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------


def generate_pilot(
    out_dir: Path = DEFAULT_OUTPUT_DIR,
    *,
    kozak_parameters_path: Path | None = None,
) -> dict[str, Any]:
    started = datetime.now(UTC)
    assert_no_unexpected_deletions()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    kozak_path = Path(kozak_parameters_path or default_kozak_parameter_path())
    kozak_parameters = load_kozak_parameters(kozak_path)
    qc_registry = load_taper_registry(QC_TAPER_PATH)
    sort_rules = load_national_sort_rules(SORT_RULES_PATH)

    bc_frame, bc_audit = _load_bc_frame()
    bc_sample, bc_strata = _select_bc_sample(bc_frame)
    frames: dict[str, pd.DataFrame] = {"BC": bc_sample}
    strata_rows: list[dict[str, Any]] = list(bc_strata)
    audit_rows: list[dict[str, Any]] = []
    magplot_audits: dict[str, dict[str, Any]] = {}

    bc_source_total = float(bc_audit["source_phf_total"])
    bc_selected_total = float(bc_sample["expansion_factor"].sum())
    audit_rows.append(
        {
            "province": "BC",
            "source_file": _relative_to_repo(BC_V2_TREE_LIST_PATH),
            "source_hash": _sha256(BC_V2_TREE_LIST_PATH),
            "source_rows": int(bc_audit["source_rows"]),
            "source_expansion_stems_ha": bc_source_total,
            "eligible_rows": int(bc_audit["source_bucked_rows"]),
            "eligible_expansion_stems_ha": bc_source_total,
            "frame_rows": len(bc_sample),
            "frame_expansion_stems_ha": bc_selected_total,
            "selected_trees": len(bc_sample),
            "selected_expansion_stems_ha": bc_selected_total,
            "expansion_conservation_error": abs(bc_selected_total - bc_source_total),
            "selection_method": SAMPLE_METHOD["BC"],
            "stratum_width_cm": 2.5,
            "source_bucked_rows": int(bc_audit["source_bucked_rows"]),
            "excluded_not_bucked_rows": int(bc_audit["excluded_not_bucked_rows"]),
            "excluded_unsupported_species_rows": 0,
            "excluded_outside_taper_domain_rows": 0,
            "source_program": "FAIB_PSP_TSA24",
        }
    )

    for province in ("QC", "NB", "YT"):
        source, audit = _load_magplot_frame(province)
        magplot_audits[province] = audit
        sample, strata = _select_magplot_strata(source, province)
        frames[province] = sample
        strata_rows.extend(strata)
        selected_total = float(sample["expansion_factor"].sum())
        frame_total = float(audit["frame_expansion_stems_ha"])
        excluded_steps = {step["reason"]: step for step in audit["steps"]}
        audit_rows.append(
            {
                "province": province,
                "source_file": audit["source_file"],
                "source_hash": audit["source_hash"],
                "source_rows": int(audit["source_rows"]),
                "source_expansion_stems_ha": float(audit["source_expansion_stems_ha"]),
                "eligible_rows": int(audit["eligible_rows"]),
                "eligible_expansion_stems_ha": float(
                    audit["eligible_expansion_stems_ha"]
                ),
                "frame_rows": int(audit["frame_rows"]),
                "frame_expansion_stems_ha": frame_total,
                "selected_trees": len(sample),
                "selected_expansion_stems_ha": selected_total,
                "expansion_conservation_error": abs(selected_total - frame_total),
                "selection_method": SAMPLE_METHOD[province],
                "stratum_width_cm": DBH_STRATUM_WIDTH_CM,
                "source_bucked_rows": 0,
                "excluded_not_bucked_rows": 0,
                "excluded_not_live_standing_rows": int(
                    excluded_steps.get("not_live_standing", {}).get("n_rows", 0)
                ),
                "excluded_missing_or_nonpositive_dbh_rows": int(
                    excluded_steps.get("missing_or_nonpositive_dbh", {}).get("n_rows", 0)
                ),
                "excluded_missing_or_nonpositive_height_rows": int(
                    excluded_steps.get("missing_or_nonpositive_height", {}).get(
                        "n_rows", 0
                    )
                ),
                "excluded_missing_or_nonpositive_expansion_rows": int(
                    excluded_steps.get("missing_or_nonpositive_expansion", {}).get(
                        "n_rows", 0
                    )
                ),
                "excluded_unsupported_species_rows": int(
                    excluded_steps.get("unsupported_taper_species", {}).get(
                        "n_rows", 0
                    )
                ),
                "excluded_outside_taper_domain_rows": int(
                    excluded_steps.get("outside_schneider2013_domain", {}).get(
                        "n_rows", 0
                    )
                ),
                "source_program": audit["source_program"],
            }
        )

    combined = pd.concat(list(frames.values()), ignore_index=True)
    if set(combined["jurisdiction"]) != set(PILOT_PROVINCES):
        raise SchemaError("sample does not contain exactly the four pilot provinces")
    if combined["tree_id"].duplicated().any():
        raise SchemaError("selected tree_id values are not unique")

    bc_bec = dict(zip(bc_sample["tree_id"], bc_sample["bec_zone"], strict=True))
    trees: list[PilotTree] = []
    extras: dict[str, dict[str, Any]] = {}
    tapers: dict[str, PilotTaper] = {}
    for row in combined.itertuples(index=False):
        tree = PilotTree(
            tree_id=str(row.tree_id),
            site_id=str(row.site_id),
            jurisdiction=str(row.jurisdiction),
            species=str(row.species),
            species_group=str(row.species_group),
            dbh_cm=float(row.dbh_cm),
            height_m=float(row.height_m),
            defect_pct=float(row.defect_pct),
            expansion_factor=float(row.expansion_factor),
            status=str(row.status),
            provenance=str(row.provenance),
            evidence=str(row.evidence),
            taper_id="pending",
        )
        taper = _taper_for_tree(
            tree, tree.jurisdiction, kozak_parameters, qc_registry, bc_bec
        )
        tree = PilotTree(
            tree_id=tree.tree_id,
            site_id=tree.site_id,
            jurisdiction=tree.jurisdiction,
            species=tree.species,
            species_group=tree.species_group,
            dbh_cm=tree.dbh_cm,
            height_m=tree.height_m,
            defect_pct=tree.defect_pct,
            expansion_factor=tree.expansion_factor,
            status=tree.status,
            provenance=tree.provenance,
            evidence=tree.evidence,
            taper_id=taper.taper_id,
        )
        trees.append(tree)
        tapers[tree.tree_id] = taper
        extras[tree.tree_id] = {
            "source_tree_record_id": str(row.source_tree_record_id),
            "source_site_id": str(row.source_site_id),
            "source_plot_id": str(row.source_plot_id),
            "source_expansion_factor": float(row.source_expansion_factor),
            "stratum_id": str(row.stratum_id),
            "dbh_stratum": str(row.dbh_stratum),
            "stratum_n_trees": int(row.stratum_n_trees),
            "stratum_expansion_factor": float(row.stratum_expansion_factor),
            "selection_method": str(row.selection_method),
            "species_nfi": str(row.species_nfi),
            "tree_list_basis": str(row.tree_list_basis),
            "tree_list_label": TREE_LIST_LABEL,
            "measured_tree_list": True,
            "synthetic": False,
            "model_estimate": True,
            "observed": False,
            "policy_grade": False,
            "national_claim": False,
            "taper_status": taper.taper_status,
            "taper_jurisdiction": taper.source_jurisdiction,
            "taper_bec_zone": taper.bec_zone,
            "taper_proxy": bool(taper.proxy),
            "taper_evidence": taper.evidence,
            "taper_id": taper.taper_id,
            "taper_source": taper.source,
            "expansion_factor": tree.expansion_factor,
            "dbh_basis": str(row.dbh_basis),
            "height_basis": str(row.height_basis),
            "bec_zone": str(getattr(row, "bec_zone", "NA")),
            "bec_subzone": str(getattr(row, "bec_subzone", "")),
        }

    logs: list[PilotBuckedLog] = []
    residues: list[PilotResidueLedger] = []
    for tree in trees:
        tree_logs, residue = buck_tree(tree, tapers[tree.tree_id], tuple(sort_rules))
        logs.extend(tree_logs)
        residues.append(residue)

    allometry = load_four_province_biomass_allometry()
    species_nfi_by_tree = {
        tree_id: str(extra["species_nfi"]) for tree_id, extra in extras.items()
    }
    components = tree_components_for_measured_trees(
        trees,
        allometry,
        species_nfi_by_tree=species_nfi_by_tree,
    )
    if set(components) != {tree.tree_id for tree in trees}:
        raise SchemaError("allometry components do not cover every measured tree")

    if not logs:
        raise SchemaError("pilot produced no logs")
    if len(residues) != len(trees):
        raise SchemaError("residue ledger does not cover every tree")
    for residue in residues:
        if residue.balance_error_m3 > 1e-8:
            raise SchemaError(
                f"tree {residue.tree_id} balance error {residue.balance_error_m3}"
            )

    province_summary = _province_rows(trees, logs, residues, extras, components)
    coefficient_comparison = _coefficient_rows(province_summary, audit_rows)
    taper_coverage = _taper_coverage_rows(trees, extras)
    allometry_coverage = _allometry_coverage_rows(trees, extras, components)
    residue_scenarios = _residue_scenario_rows(province_summary)
    sort_rule_columns = [
        "jurisdiction",
        "rule_id",
        "sort_code",
        "species_group",
        "product",
        "grade",
        "grade_status",
        "sort_basis",
        "min_top_cm",
        "target_length_m",
        "max_length_m",
        "priority",
        "price_cad_m3",
        "price_deck_id",
        "evidence",
    ]
    sort_rule_rows = [
        {column: getattr(rule, column) for column in sort_rule_columns}
        for rule in sort_rules
    ]

    tree_columns = [
        "tree_id",
        "jurisdiction",
        "species",
        "species_nfi",
        "species_group",
        "dbh_cm",
        "height_m",
        "dbh_basis",
        "height_basis",
        "defect_pct",
        "expansion_factor",
        "expansion_factor_basis",
        "status",
        "provenance",
        "evidence",
        "taper_id",
        "taper_status",
        "taper_jurisdiction",
        "taper_bec_zone",
        "taper_proxy",
        "taper_evidence",
        "tree_list_label",
        "tree_list_basis",
        "source_tree_record_id",
        "source_site_id",
        "source_plot_id",
        "source_expansion_factor",
        "stratum_id",
        "dbh_stratum",
        "stratum_n_trees",
        "stratum_expansion_factor",
        "selection_method",
        "bec_zone",
        "bec_subzone",
        "measured_tree_list",
        "synthetic",
        "model_estimate",
        "observed",
        "policy_grade",
        "national_claim",
    ]
    tree_rows = []
    for tree in trees:
        extra = extras[tree.tree_id]
        tree_rows.append(
            {
                "tree_id": tree.tree_id,
                "jurisdiction": tree.jurisdiction,
                "species": tree.species,
                "species_nfi": extra["species_nfi"],
                "species_group": tree.species_group,
                "dbh_cm": tree.dbh_cm,
                "height_m": tree.height_m,
                "dbh_basis": extra["dbh_basis"],
                "height_basis": extra["height_basis"],
                "defect_pct": tree.defect_pct,
                "expansion_factor": tree.expansion_factor,
                "expansion_factor_basis": "source_pooled_stratum_expansion",
                "status": tree.status,
                "provenance": tree.provenance,
                "evidence": tree.evidence,
                "taper_id": tapers[tree.tree_id].taper_id,
                "taper_status": extra["taper_status"],
                "taper_jurisdiction": extra["taper_jurisdiction"],
                "taper_bec_zone": extra["taper_bec_zone"],
                "taper_proxy": extra["taper_proxy"],
                "taper_evidence": extra["taper_evidence"],
                "tree_list_label": TREE_LIST_LABEL,
                "tree_list_basis": extra["tree_list_basis"],
                "source_tree_record_id": extra["source_tree_record_id"],
                "source_site_id": extra["source_site_id"],
                "source_plot_id": extra["source_plot_id"],
                "source_expansion_factor": extra["source_expansion_factor"],
                "stratum_id": extra["stratum_id"],
                "dbh_stratum": extra["dbh_stratum"],
                "stratum_n_trees": extra["stratum_n_trees"],
                "stratum_expansion_factor": extra["stratum_expansion_factor"],
                "selection_method": extra["selection_method"],
                "bec_zone": extra["bec_zone"],
                "bec_subzone": extra["bec_subzone"],
                "measured_tree_list": True,
                "synthetic": False,
                "model_estimate": False,
                "observed": False,
                "policy_grade": False,
                "national_claim": False,
            }
        )

    log_columns = [
        "tree_id",
        "jurisdiction",
        "species",
        "log_index",
        "length_m",
        "large_end_dib_cm",
        "small_end_dib_cm",
        "volume_m3",
        "net_volume_m3",
        "defect_deduction_m3",
        "sort_code",
        "product",
        "grade",
        "grade_status",
        "sort_basis",
        "price_cad_m3",
        "value_cad",
        "evidence",
        "taper_evidence",
        "sort_evidence",
        "value_evidence",
        "tree_evidence",
        "taper_id",
        "taper_source",
        "grade_rule_id",
        "price_deck_id",
        "provenance",
        "quality",
        "taper_status",
        "taper_jurisdiction",
        "taper_bec_zone",
        "taper_proxy",
        "species_nfi",
        "tree_list_basis",
        "tree_list_label",
        "measured_tree_list",
        "sort_rule_set",
        "expansion_factor",
        "volume_m3_expanded",
        "net_volume_m3_expanded",
        "defect_deduction_m3_expanded",
        "value_cad_expanded",
        "model_estimate",
        "synthetic",
        "observed",
        "policy_grade",
        "national_claim",
    ]
    log_rows = []
    for log in logs:
        extra = extras[log.tree_id]
        weight = float(extra["expansion_factor"])
        log_rows.append(
            {
                **asdict(log),
                "species": next(
                    tree.species for tree in trees if tree.tree_id == log.tree_id
                ),
                "taper_status": extra["taper_status"],
                "taper_jurisdiction": extra["taper_jurisdiction"],
                "taper_bec_zone": extra["taper_bec_zone"],
                "taper_proxy": extra["taper_proxy"],
                "species_nfi": extra["species_nfi"],
                "tree_list_basis": extra["tree_list_basis"],
                "tree_list_label": TREE_LIST_LABEL,
                "measured_tree_list": True,
                "sort_rule_set": SORT_RULE_SET,
                "expansion_factor": weight,
                "volume_m3_expanded": log.volume_m3 * weight,
                "net_volume_m3_expanded": log.net_volume_m3 * weight,
                "defect_deduction_m3_expanded": log.defect_deduction_m3 * weight,
                "value_cad_expanded": log.value_cad * weight,
                "model_estimate": True,
                "synthetic": False,
                "observed": False,
                "policy_grade": False,
                "national_claim": False,
            }
        )

    residue_columns = [
        "tree_id",
        "jurisdiction",
        "species",
        "residue_volume_m3",
        "basic_density_odt_m3",
        "residue_odt",
        "moisture_wet_basis",
        "green_tonnes",
        "chip_yield_fraction",
        "chip_green_tonnes",
        "hog_yield_fraction",
        "hog_green_tonnes",
        "recovered_log_m3",
        "taper_total_m3",
        "balance_error_m3",
        "evidence",
        "taper_evidence",
        "density_evidence",
        "moisture_evidence",
        "chip_evidence",
        "provenance",
        "notes",
        "taper_status",
        "taper_jurisdiction",
        "taper_bec_zone",
        "taper_proxy",
        "species_nfi",
        "tree_list_basis",
        "tree_list_label",
        "measured_tree_list",
        "sort_rule_set",
        "expansion_factor",
        "residue_volume_m3_expanded",
        "residue_odt_expanded",
        "green_tonnes_expanded",
        "chip_green_tonnes_expanded",
        "hog_green_tonnes_expanded",
        "recovered_log_m3_expanded",
        "taper_total_m3_expanded",
        "model_estimate",
        "synthetic",
        "observed",
        "policy_grade",
        "national_claim",
    ]
    residue_columns.extend(
        column for column in COMPONENT_COLUMNS if column not in residue_columns
    )
    residue_rows = []
    for residue in residues:
        extra = extras[residue.tree_id]
        weight = float(extra["expansion_factor"])
        base_row = {
            **asdict(residue),
            "taper_status": extra["taper_status"],
            "taper_jurisdiction": extra["taper_jurisdiction"],
            "taper_bec_zone": extra["taper_bec_zone"],
            "taper_proxy": extra["taper_proxy"],
            "species_nfi": extra["species_nfi"],
            "tree_list_basis": extra["tree_list_basis"],
            "tree_list_label": TREE_LIST_LABEL,
            "measured_tree_list": True,
            "sort_rule_set": SORT_RULE_SET,
            "expansion_factor": weight,
            "residue_volume_m3_expanded": residue.residue_volume_m3 * weight,
            "residue_odt_expanded": residue.residue_odt * weight,
            "green_tonnes_expanded": residue.green_tonnes * weight,
            "chip_green_tonnes_expanded": residue.chip_green_tonnes * weight,
            "hog_green_tonnes_expanded": residue.hog_green_tonnes * weight,
            "recovered_log_m3_expanded": residue.recovered_log_m3 * weight,
            "taper_total_m3_expanded": residue.taper_total_m3 * weight,
            "model_estimate": True,
            "synthetic": False,
            "observed": False,
            "policy_grade": False,
            "national_claim": False,
        }
        residue_rows.append(
            annotate_component_row(
                base_row,
                components[residue.tree_id],
                weight=weight,
                stem_residue_odt=residue.residue_odt,
                jurisdiction=residue.jurisdiction,
                species_nfi=str(extra["species_nfi"]),
            )
        )

    tree_summary_rows = []
    logs_by_tree: dict[str, list[PilotBuckedLog]] = {}
    for log in logs:
        logs_by_tree.setdefault(log.tree_id, []).append(log)
    for tree in trees:
        tree_logs = logs_by_tree.get(tree.tree_id, [])
        residue = next(item for item in residues if item.tree_id == tree.tree_id)
        extra = extras[tree.tree_id]
        tree_summary_rows.append(
            {
                "tree_id": tree.tree_id,
                "jurisdiction": tree.jurisdiction,
                "species": tree.species,
                "dbh_cm": tree.dbh_cm,
                "height_m": tree.height_m,
                "expansion_factor": tree.expansion_factor,
                "n_logs": len(tree_logs),
                "recovered_gross_m3": sum(log.volume_m3 for log in tree_logs),
                "residue_m3": residue.residue_volume_m3,
                "taper_total_m3": residue.taper_total_m3,
                "balance_error_m3": residue.balance_error_m3,
                "taper_id": tapers[tree.tree_id].taper_id,
                "taper_status": extra["taper_status"],
                "taper_jurisdiction": extra["taper_jurisdiction"],
                "measured_tree_list": True,
                "model_estimate": True,
                "synthetic": False,
                "national_claim": False,
            }
        )

    taper_dispatch_rows = []
    for tree in trees:
        extra = extras[tree.tree_id]
        taper_dispatch_rows.append(
            {
                "tree_id": tree.tree_id,
                "jurisdiction": tree.jurisdiction,
                "species": tree.species,
                "dbh_cm": tree.dbh_cm,
                "height_m": tree.height_m,
                "taper_id": tapers[tree.tree_id].taper_id,
                "taper_jurisdiction": extra["taper_jurisdiction"],
                "taper_status": extra["taper_status"],
                "taper_bec_zone": extra["taper_bec_zone"],
                "taper_proxy": extra["taper_proxy"],
                "taper_evidence": extra["taper_evidence"],
                "taper_source": tapers[tree.tree_id].source,
                "measured_tree_list": True,
                "model_estimate": True,
                "synthetic": False,
                "national_claim": False,
            }
        )

    tree_summary_columns = list(tree_summary_rows[0])
    sample_audit_columns = [
        "province",
        "source_file",
        "source_hash",
        "source_rows",
        "source_expansion_stems_ha",
        "eligible_rows",
        "eligible_expansion_stems_ha",
        "frame_rows",
        "frame_expansion_stems_ha",
        "selected_trees",
        "selected_expansion_stems_ha",
        "expansion_conservation_error",
        "selection_method",
        "stratum_width_cm",
        "source_bucked_rows",
        "excluded_not_bucked_rows",
        "excluded_not_live_standing_rows",
        "excluded_missing_or_nonpositive_dbh_rows",
        "excluded_missing_or_nonpositive_height_rows",
        "excluded_missing_or_nonpositive_expansion_rows",
        "excluded_unsupported_species_rows",
        "excluded_outside_taper_domain_rows",
        "source_program",
    ]
    audit_rows = [
        {column: row.get(column, 0) for column in sample_audit_columns}
        for row in audit_rows
    ]
    sample_selection_columns = list(strata_rows[0])
    province_columns = list(province_summary[0])
    coefficient_columns = list(coefficient_comparison[0])
    coverage_columns = list(taper_coverage[0])
    dispatch_columns = list(taper_dispatch_rows[0])

    paths = {
        "tree_list": out_dir / "tree_list.csv",
        "sample_selection": out_dir / "sample_selection.csv",
        "sample_audit": out_dir / "sample_audit.csv",
        "bucked_logs": out_dir / "bucked_logs.csv",
        "residue_ledger": out_dir / "residue_ledger.csv",
        "tree_summary": out_dir / "tree_summary.csv",
        "province_summary": out_dir / "province_summary.csv",
        "taper_dispatch": out_dir / "taper_dispatch.csv",
        "taper_coverage": out_dir / "taper_coverage.csv",
        "coefficient_comparison": out_dir / "coefficient_comparison.csv",
        "allometry_coverage": out_dir / "allometry_coverage.csv",
        "residue_recovery_scenarios": out_dir / "residue_recovery_scenarios.csv",
        "sort_rules": out_dir / "sort_rules.csv",
    }
    _write_csv(paths["tree_list"], tree_rows, tree_columns)
    _write_csv(paths["sample_selection"], strata_rows, sample_selection_columns)
    _write_csv(paths["sample_audit"], audit_rows, sample_audit_columns)
    _write_csv(paths["bucked_logs"], log_rows, log_columns)
    _write_csv(paths["residue_ledger"], residue_rows, residue_columns)
    _write_csv(paths["tree_summary"], tree_summary_rows, tree_summary_columns)
    _write_csv(paths["province_summary"], province_summary, province_columns)
    _write_csv(paths["taper_dispatch"], taper_dispatch_rows, dispatch_columns)
    _write_csv(paths["taper_coverage"], taper_coverage, coverage_columns)
    _write_csv(paths["coefficient_comparison"], coefficient_comparison, coefficient_columns)
    _write_csv(
        paths["allometry_coverage"],
        allometry_coverage,
        list(allometry_coverage[0]),
    )
    _write_csv(
        paths["residue_recovery_scenarios"],
        residue_scenarios,
        list(residue_scenarios[0]),
    )
    _write_csv(paths["sort_rules"], sort_rule_rows, sort_rule_columns)

    summary = {
        "artifact": "four_province_bucking_pilot_v0",
        "generated_at": started.isoformat(),
        "provinces": list(PILOT_PROVINCES),
        "excluded_provinces": [
            province
            for province in (
                "AB",
                "SK",
                "MB",
                "ON",
                "NS",
                "PE",
                "NL",
                "NT",
                "NU",
            )
        ],
        "labels": {
            "tree_list_label": TREE_LIST_LABEL,
            "measured_tree_list": True,
            "synthetic": False,
            "model_estimate": True,
            "observed": False,
            "policy_grade": False,
            "national_claim": False,
            "grade_status": "not_assigned",
            "sort_basis": "dimension_only",
        },
        "counts": {
            "provinces": len(PILOT_PROVINCES),
            "trees": len(trees),
            "logs": len(logs),
            "residues": len(residues),
            "trees_by_province": {
                province: sum(1 for tree in trees if tree.jurisdiction == province)
                for province in PILOT_PROVINCES
            },
        },
        "sample_audit": audit_rows,
        "taper_coverage": taper_coverage,
        "allometry": {
            "method": (
                "CFS tree-level DBH+height branch and foliage equations "
                "(Lambert/Ung); exact species or NFI mapping with documented "
                "proxy labels"
            ),
            "coverage": allometry_coverage,
            "n_trees": len(components),
            "n_proxy_trees": sum(
                1 for component in components.values() if component.proxy
            ),
            "n_species_specific_trees": sum(
                1
                for component in components.values()
                if component.species_specific
            ),
            "source": "CFS Biomass and Nutrients Calculator",
            "source_licence": "Open Government Licence - Canada",
            "reference": (
                "Lambert, Ung and Raulier 2005; Ung, Bernier and Guo 2008"
            ),
            "recovery_scenarios": list(DEFAULT_RECOVERY_FACTORS),
            "reference_recovery_factor": REFERENCE_RECOVERY_FACTOR,
            "calibrated_recovery_factor": False,
            "residue_scenarios": residue_scenarios,
        },
        "province_summary": province_summary,
        "coefficient_comparison": coefficient_comparison,
        "config": {
            "sort_rule_set": SORT_RULE_SET,
            "sort_rules_path": _relative_to_repo(SORT_RULES_PATH),
            "sort_rules_hash": _sha256(SORT_RULES_PATH),
            "facility_chain_path": _relative_to_repo(FACILITY_CHAIN_PATH),
            "facility_chain_hash": _sha256(FACILITY_CHAIN_PATH),
            "qc_taper_path": _relative_to_repo(QC_TAPER_PATH),
            "qc_taper_hash": _sha256(QC_TAPER_PATH),
            "kozak_parameters_path": _relative_to_repo(kozak_path),
            "kozak_parameters_hash": _sha256(kozak_path),
            "dbh_stratum_width_cm": DBH_STRATUM_WIDTH_CM,
            "stump_height_m": STUMP_HEIGHT_M,
            "density_odt_m3": BASIC_DENSITY_ODT_M3,
            "moisture_wet_basis": MOISTURE_WET_BASIS,
            "chip_yield_fraction": CHIP_YIELD_FRACTION,
            "hog_yield_fraction": HOG_YIELD_FRACTION,
        },
        "checks": {
            "exactly_four_provinces": {tree.jurisdiction for tree in trees}
            == set(PILOT_PROVINCES),
            "all_measured": all(extras[tree.tree_id]["measured_tree_list"] for tree in trees),
            "no_synthetic": not any(extras[tree.tree_id]["synthetic"] for tree in trees),
            "no_national_claim": not any(
                extras[tree.tree_id]["national_claim"] for tree in trees
            ),
            "all_taper_evidence_published_table": all(
                tapers[tree.tree_id].evidence == "published_table" for tree in trees
            ),
            "all_residue_balances_within_1e8_m3": all(
                residue.balance_error_m3 <= 1e-8 for residue in residues
            ),
            "expansion_conservation": all(
                row["expansion_conservation_error"] <= 1e-6 for row in audit_rows
            ),
            "all_measured_trees_have_allometry": set(components)
            == {tree.tree_id for tree in trees},
            "allometry_conservation": all(
                math.isclose(
                    float(row["total_residue_odt_expanded"]),
                    float(row["stem_residue_odt_expanded"])
                    + float(row["branch_foliage_odt_expanded"]),
                    rel_tol=1e-9,
                    abs_tol=1e-6,
                )
                for row in residue_rows
            ),
            "allometry_recovery_scenarios": all(
                math.isclose(
                    float(row[f"recoverable_residue_odt_r{round(factor * 100):03d}_expanded"]),
                    float(row["total_residue_odt_expanded"]) * factor,
                    rel_tol=1e-9,
                    abs_tol=1e-6,
                )
                for row in residue_rows
                for factor in DEFAULT_RECOVERY_FACTORS
            ),
            "no_negative_or_nan_outputs": True,
        },
        "inputs": [
            {
                "path": _relative_to_repo(BC_V2_TREE_LIST_PATH),
                "sha256": _sha256(BC_V2_TREE_LIST_PATH),
            },
            *[
                {
                    "path": magplot_audits[province]["source_file"],
                    "sha256": magplot_audits[province]["source_hash"],
                }
                for province in ("QC", "NB", "YT")
            ],
        ],
    }
    # Explicit finite/non-negative scan on the numeric output rows.
    checked_frames = {
        "bucked_logs": pd.DataFrame(log_rows, columns=log_columns),
        "residue_ledger": pd.DataFrame(residue_rows, columns=residue_columns),
        "tree_list": pd.DataFrame(tree_rows, columns=tree_columns),
        "residue_recovery_scenarios": pd.DataFrame(residue_scenarios),
    }
    for name, frame in checked_frames.items():
        numeric = frame.select_dtypes(include=[np.number])
        if numeric.isna().any().any():
            raise SchemaError(f"{name} contains NaN numeric values")
        if (numeric < 0.0).any().any():
            raise SchemaError(f"{name} contains negative numeric values")

    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    (out_dir / "report.md").write_text(_report_markdown(summary), encoding="utf-8")
    (out_dir / "README.md").write_text(_readme(), encoding="utf-8")
    (out_dir / "schema.csv").write_text(
        "column,value,meaning\n"
        + "\n".join(
            f'"{row["column"]}","{row["value"]}","{row["meaning"]}"'
            for row in _schema_rows()
        )
        + "\n",
        encoding="utf-8",
    )

    manifest = {
        "artifact": "four_province_bucking_pilot_v0",
        "generated_at": started.isoformat(),
        "elapsed_s": round(
            (datetime.now(UTC) - started).total_seconds(), 3
        ),
        "command": "python -m workflows.national.bucking.four_province_bucking_pilot",
        "provinces": list(PILOT_PROVINCES),
        "inputs": summary["inputs"],
        "config": summary["config"],
        "counts": summary["counts"],
        "outputs": {
            name: {
                "path": _relative_to_repo(path),
                "sha256": _sha256(path),
                "bytes": path.stat().st_size,
            }
            for name, path in paths.items()
        },
        "report": {
            "path": _relative_to_repo(out_dir / "report.md"),
            "sha256": _sha256(out_dir / "report.md"),
        },
        "summary": {
            "path": _relative_to_repo(out_dir / "summary.json"),
            "sha256": _sha256(out_dir / "summary.json"),
        },
        "labels": summary["labels"],
        "checks": summary["checks"],
        "unexpected_git_deletions": "none",
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    assert_no_unexpected_deletions()
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Output directory for the four-province pilot artifact.",
    )
    parser.add_argument(
        "--kozak-parameters",
        type=Path,
        default=None,
        help="Optional Kozak parameter CSV override.",
    )
    args = parser.parse_args(argv)
    try:
        summary = generate_pilot(args.out_dir, kozak_parameters_path=args.kozak_parameters)
    except (FileNotFoundError, SchemaError, RuntimeError, ValueError) as exc:
        print(f"four_province_bucking_pilot: {exc}", file=sys.stderr)
        return 1
    print(
        "four_province_bucking_pilot: "
        f"{summary['counts']['trees']} trees, "
        f"{summary['counts']['logs']} logs, "
        f"provinces={','.join(summary['provinces'])}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
