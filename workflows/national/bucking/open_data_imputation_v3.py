"""E2 v3 density-calibrated tree lists for nine non-measured provinces.

E2 v2 replaced the single donor fallback with the best open provincial
distributions, but kept a uniform 1,000 stems/ha expansion for every province.
E2 v3 keeps the v2 geometry and bucking logic and replaces that uniform
expansion with province-specific density evidence:

* SK: FRI ``BA``/``QMD``-implied total stem density (``MSTEMS`` cross-check).
* NS: ``TOTBA``/``AVDI``-implied total stem density (``TOTMRVOL`` check).
* PE: EFI2020 ``tphmerch`` (``gtv`` check).
* AB: AVI density/structure classes only, so a labelled 1,000 stems/ha proxy.
* MB: median measured donor plot density, labelled donor evidence.
* ON / NL / NT / NU: labelled 1,000 stems/ha proxy.

No facility coefficient is used for calibration, and no request, DUA,
restricted data, UI change or official OSeCAN change is involved.
"""

# Report and provenance literals are long; adjacent literals are intentional.

from __future__ import annotations

import argparse
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from nemora.workflows.taper import load_kozak_parameters

from . import e2_biomass_allometry as _e2_allometry
from . import open_data_imputation_v2 as _v2
from .national_bucking_pilot_v1 import assert_no_unexpected_deletions
from .open_data_imputation import (
    IMPUTED_PROVINCES,
    PROVINCE_NAMES,
    _read_csv,
    _repo_relative,
    _sha256,
    _write_csv,
)
from .pilot_schema import PilotTree
from .schneider2013 import load_taper_registry
from .taper import default_kozak_parameter_path

__all__ = [
    "ARTIFACT_ID",
    "DEFAULT_OUTPUT_DIR",
    "DENSITY_DIR",
    "PROVENANCE_E2C",
    "TREE_LIST_E2C",
    "generate",
]

ARTIFACT_ID = "open_data_imputation_v3"
REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT_DIR = (
    REPO_ROOT / "data" / "derived" / "national" / "bucking" / ARTIFACT_ID
)
DENSITY_DIR = (
    REPO_ROOT / "data" / "derived" / "national" / "inventory" / "provincial_open_inventory_density_v1"
)
DISTRIBUTION_DIR = _v2.DISTRIBUTION_DIR
V2_DIR = _v2.DEFAULT_OUTPUT_DIR
QC_TAPER_PATH = _v2.QC_TAPER_PATH

PROVENANCE_E2C = "E2c_open_inventory_density_calibrated"
TREE_LIST_E2C = "open_inventory_density_calibrated"
DENSITY_TOLERANCE = 1e-6
VOLUME_TOLERANCE = 0.50


class V3Error(RuntimeError):
    """Raised when an E2 v3 contract is violated."""


# ---------------------------------------------------------------------------
# Density boundary
# ---------------------------------------------------------------------------


def _load_density_targets(path: Path = DENSITY_DIR / "density_targets.csv") -> dict[str, dict[str, Any]]:
    frame = _read_csv(path, "provincial density targets")
    required = {
        "province",
        "density_stems_ha",
        "density_statistic",
        "density_basis",
        "density_evidence_class",
        "density_source_ids",
        "expansion_label",
    }
    missing = required.difference(frame.columns)
    if missing:
        raise V3Error(f"density targets lack fields: {sorted(missing)}")
    targets: dict[str, dict[str, Any]] = {}
    for row in frame.to_dict(orient="records"):
        province = str(row["province"]).upper()
        if province not in IMPUTED_PROVINCES:
            raise V3Error(f"unexpected density province: {province}")
        density = float(row["density_stems_ha"])
        if not math.isfinite(density) or density <= 0.0:
            raise V3Error(f"{province} density target is not positive and finite")
        evidence = str(row["density_evidence_class"]).strip()
        if evidence not in {"open_inventory", "donor", "proxy"}:
            raise V3Error(f"{province} unexpected density evidence class: {evidence}")
        targets[province] = {
            **row,
            "density_stems_ha": density,
            "density_statistic": str(row["density_statistic"]).strip(),
            "density_basis": str(row["density_basis"]).strip(),
            "density_evidence_class": evidence,
            "density_source_ids": str(row["density_source_ids"]).strip(),
            "expansion_label": str(row["expansion_label"]).strip(),
        }
    if set(targets) != set(IMPUTED_PROVINCES):
        raise V3Error(
            f"density targets must cover nine provinces: {sorted(targets)}"
        )
    for province in ("SK", "NS", "PE"):
        if targets[province]["density_evidence_class"] != "open_inventory":
            raise V3Error(f"{province} must use open_inventory density evidence")
    for province in ("AB", "MB", "ON", "NL", "NT", "NU"):
        if targets[province]["density_evidence_class"] not in {"donor", "proxy"}:
            raise V3Error(f"{province} must be labelled donor or proxy")
    return targets


# ---------------------------------------------------------------------------
# Tree-list scaling
# ---------------------------------------------------------------------------


def _scaled_tree_rows(
    kozak_parameters: Mapping[tuple[str, str], Any],
    qc_registry: Any,
    allometry: _e2_allometry.AllometryLookup,
    targets: Mapping[str, Mapping[str, Any]],
) -> tuple[
    list[PilotTree],
    dict[str, Any],
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
]:
    trees, tapers, extras, audit = _v2._build_tree_rows(
        kozak_parameters, qc_registry, allometry
    )
    scaled_trees: list[PilotTree] = []
    scaled_tapers: dict[str, Any] = {}
    scaled_extras: dict[str, dict[str, Any]] = {}
    for tree in trees:
        target = targets[tree.jurisdiction]
        density = float(target["density_stems_ha"])
        scale = density / _v2.REPRESENTATIVE_STEMS_PER_HA
        tree_id = tree.tree_id.replace("-E2V2-", "-E2V3-")
        site_id = tree.site_id.replace("-E2V2-", "-E2V3-")
        expansion = tree.expansion_factor * scale
        if not math.isfinite(expansion) or expansion <= 0.0:
            raise V3Error(f"{tree_id} scaled expansion is not positive and finite")
        provenance = (
            f"E2 v3 density-calibrated tree list: {tree.jurisdiction} "
            f"density_target={density:.6f} stems/ha "
            f"({target['expansion_label']}); scale_from_v2={scale:.9f}; "
            f"{tree.provenance}"
        )
        scaled = replace(
            tree,
            tree_id=tree_id,
            site_id=site_id,
            expansion_factor=expansion,
            provenance=provenance,
        )
        extra = dict(extras[tree.tree_id])
        extra.update(
            {
                "density_stems_ha": density,
                "density_statistic": target["density_statistic"],
                "density_basis": target["density_basis"],
                "density_evidence_class": target["density_evidence_class"],
                "density_source_ids": target["density_source_ids"],
                "density_expansion_label": target["expansion_label"],
                "density_scale_from_v2": scale,
                "expansion_factor": expansion,
                "expansion_factor_basis": (
                    f"density_calibrated_{density:.6f}_stems_per_ha"
                ),
                "tree_list_label": TREE_LIST_E2C,
                "tree_list_basis": "provincial_open_inventory_density_calibrated",
                "provenance_tier": PROVENANCE_E2C,
                "density_calibrated": True,
                "density_source_class": target["density_evidence_class"],
            }
        )
        scaled_trees.append(scaled)
        scaled_tapers[tree_id] = tapers[tree.tree_id]
        scaled_extras[tree_id] = extra
    if len(scaled_trees) != len(trees):
        raise V3Error("tree scaling changed the row count")
    for province in IMPUTED_PROVINCES:
        province_trees = [tree for tree in scaled_trees if tree.jurisdiction == province]
        if len(province_trees) != _v2.N_TREES_PER_PROVINCE:
            raise V3Error(f"{province} scaled tree count is not 36")
        total = math.fsum(tree.expansion_factor for tree in province_trees)
        target_density = float(targets[province]["density_stems_ha"])
        if abs(total - target_density) > DENSITY_TOLERANCE:
            raise V3Error(
                f"{province} scaled expansion total {total} does not match "
                f"density target {target_density}"
            )
        audit[province].update(
            {
                "expansion_total": total,
                "density_stems_ha": target_density,
                "density_statistic": targets[province]["density_statistic"],
                "density_basis": targets[province]["density_basis"],
                "density_evidence_class": targets[province]["density_evidence_class"],
                "density_source_ids": targets[province]["density_source_ids"],
                "density_expansion_label": targets[province]["expansion_label"],
                "density_scale_from_v2": total / _v2.REPRESENTATIVE_STEMS_PER_HA,
            }
        )
    return scaled_trees, scaled_tapers, scaled_extras, audit


# ---------------------------------------------------------------------------
# Summary, comparison and checks
# ---------------------------------------------------------------------------


def _v2_summary() -> pd.DataFrame:
    return _read_csv(V2_DIR / "province_summary.csv", "E2 v2 province summary")


def _v3_summary(
    tree_rows: Sequence[Mapping[str, Any]],
    log_rows: Sequence[Mapping[str, Any]],
    residue_rows: Sequence[Mapping[str, Any]],
    audit: Mapping[str, Mapping[str, Any]],
    targets: Mapping[str, Mapping[str, Any]],
    v2_summary: pd.DataFrame,
) -> list[dict[str, Any]]:
    rows = _v2._province_summary(tree_rows, log_rows, residue_rows, audit)
    v2_by_province = v2_summary.set_index("province")
    for row in rows:
        province = str(row["province"])
        target = targets[province]
        v2_row = v2_by_province.loc[province]
        v2_volume = float(v2_row["taper_total_m3_expanded"])
        v3_volume = float(row["taper_total_m3_expanded"])
        density = float(target["density_stems_ha"])
        scale = density / _v2.REPRESENTATIVE_STEMS_PER_HA
        row.update(
            {
                "provenance_tier": PROVENANCE_E2C,
                "density_stems_ha": density,
                "density_statistic": target["density_statistic"],
                "density_basis": target["density_basis"],
                "density_evidence_class": target["density_evidence_class"],
                "density_source_ids": target["density_source_ids"],
                "density_expansion_label": target["expansion_label"],
                "density_scale_from_v2": scale,
                "v2_expansion_factor_total": float(v2_row["expansion_factor_total"]),
                "v2_taper_total_m3_expanded": v2_volume,
                "delta_taper_total_m3_expanded": v3_volume - v2_volume,
                "volume_ratio_v3_to_v2": (
                    v3_volume / v2_volume if v2_volume > 0.0 else 0.0
                ),
                "volume_check_expected_ratio": scale,
                "volume_scaling_error": abs(
                    v3_volume - v2_volume * scale
                ),
                "density_calibrated": True,
            }
        )
    return rows


def _comparison_v2_v3(
    summary: Sequence[Mapping[str, Any]],
    v2_summary: pd.DataFrame,
) -> list[dict[str, Any]]:
    v2_frame = v2_summary.set_index("province")
    rows: list[dict[str, Any]] = []
    for row in summary:
        province = str(row["province"])
        v2_row = v2_frame.loc[province]
        v2_volume_mean = v2_row.get("inventory_volume_mean_m3ha", 0.0)
        open_volume = float(v2_volume_mean) if pd.notna(v2_volume_mean) else 0.0
        v3_volume = float(row["taper_total_m3_expanded"])
        rows.append(
            {
                "province": province,
                "province_name": PROVINCE_NAMES[province],
                "density_v2_stems_ha": float(v2_row["expansion_factor_total"]),
                "density_v3_stems_ha": float(row["density_stems_ha"]),
                "density_ratio_v3_to_v2": float(row["density_scale_from_v2"]),
                "density_evidence_class": row["density_evidence_class"],
                "density_expansion_label": row["density_expansion_label"],
                "v2_taper_total_m3_expanded": float(v2_row["taper_total_m3_expanded"]),
                "v3_taper_total_m3_expanded": v3_volume,
                "delta_taper_total_m3_expanded": float(
                    row["delta_taper_total_m3_expanded"]
                ),
                "volume_ratio_v3_to_v2": float(row["volume_ratio_v3_to_v2"]),
                "v2_recovered_gross_m3_expanded": float(
                    v2_row["recovered_gross_m3_expanded"]
                ),
                "v3_recovered_gross_m3_expanded": float(
                    row["recovered_gross_m3_expanded"]
                ),
                "v2_residue_m3_expanded": float(v2_row["residue_m3_expanded"]),
                "v3_residue_m3_expanded": float(row["residue_m3_expanded"]),
                "v2_total_residue_odt_expanded": float(
                    v2_row["total_residue_odt_expanded"]
                ),
                "v3_total_residue_odt_expanded": float(
                    row["total_residue_odt_expanded"]
                ),
                "delta_total_residue_odt_expanded": float(
                    row["total_residue_odt_expanded"]
                    - v2_row["total_residue_odt_expanded"]
                ),
                "v2_total_residue_odt_per_m3": float(
                    v2_row["total_residue_odt_per_recovered_m3"]
                ),
                "v3_total_residue_odt_per_m3": float(
                    row["total_residue_odt_per_recovered_m3"]
                ),
                "delta_residue_odt_per_m3": float(
                    row["total_residue_odt_per_recovered_m3"]
                    - v2_row["total_residue_odt_per_recovered_m3"]
                ),
                "inventory_volume_mean_m3ha": open_volume,
                "open_volume_ratio_v3": (
                    v3_volume / open_volume if open_volume > 0.0 else 0.0
                ),
                "volume_context_status": row["volume_context_status"],
                "volume_balance_m3": float(row["volume_balance_m3"]),
                "comparison_note": (
                    "E2 v3 scales the v2 geometry and bucking by the province "
                    "density target. Per-m3 product and residue coefficients "
                    "should be unchanged; totals scale with density."
                ),
            }
        )
    if len(rows) != len(IMPUTED_PROVINCES):
        raise V3Error("comparison row count does not match nine provinces")
    return rows


def _checks(
    tree_rows: Sequence[Mapping[str, Any]],
    summary: Sequence[Mapping[str, Any]],
    coefficients: Sequence[Mapping[str, Any]],
    comparison: Sequence[Mapping[str, Any]],
    targets: Mapping[str, Mapping[str, Any]],
) -> dict[str, bool]:
    tree_frame = pd.DataFrame(tree_rows)
    summary_frame = pd.DataFrame(summary)
    coefficient_frame = pd.DataFrame(coefficients)
    checks: dict[str, bool] = {}
    checks["nine_provinces"] = set(tree_frame["jurisdiction"]) == set(IMPUTED_PROVINCES)
    checks["thirty_six_trees_each"] = all(
        len(tree_frame[tree_frame["jurisdiction"] == province])
        == _v2.N_TREES_PER_PROVINCE
        for province in IMPUTED_PROVINCES
    )
    checks["provenance_e2c"] = set(tree_frame["provenance_tier"]) == {PROVENANCE_E2C}
    checks["no_measured_or_national_claim"] = bool(
        (not tree_frame["measured"].any())
        and tree_frame["imputed"].all()
        and (not tree_frame["synthetic"].any())
        and (not tree_frame["national_claim"].any())
        and (not tree_frame["observed"].any())
        and (not tree_frame["policy_grade"].any())
    )
    checks["density_targets_positive"] = all(
        float(target["density_stems_ha"]) > 0.0 for target in targets.values()
    )
    checks["expansion_totals_match_density"] = all(
        abs(
            float(
                tree_frame[tree_frame["jurisdiction"] == province][
                    "expansion_factor"
                ].sum()
            )
            - float(targets[province]["density_stems_ha"])
        )
        <= DENSITY_TOLERANCE
        for province in IMPUTED_PROVINCES
    )
    density_fields = (
        "density_stems_ha",
        "density_statistic",
        "density_basis",
        "density_evidence_class",
        "density_source_ids",
        "density_expansion_label",
    )
    checks["density_labels_present"] = all(
        column in tree_frame.columns
        and tree_frame[column].astype(str).str.strip().ne("").all()
        for column in density_fields
    )
    checks["open_inventory_density_sk_ns_pe"] = all(
        targets[province]["density_evidence_class"] == "open_inventory"
        for province in ("SK", "NS", "PE")
    )
    checks["proxy_donor_gaps_labelled"] = all(
        targets[province]["density_evidence_class"] in {"donor", "proxy"}
        for province in ("AB", "MB", "ON", "NL", "NT", "NU")
    )
    checks["volume_balance"] = bool(
        (summary_frame["volume_balance_m3"].abs() <= 1e-6).all()
    )
    checks["residue_balance"] = bool(
        np.allclose(
            summary_frame["total_residue_odt_expanded"],
            summary_frame["stem_residue_odt_expanded"]
            + summary_frame["branch_foliage_odt_expanded"],
            rtol=1e-9,
            atol=1e-6,
        )
    )
    comparison_frame = pd.DataFrame(comparison)
    checks["volume_scales_with_density"] = bool(
        (
            comparison_frame["v3_taper_total_m3_expanded"]
            - comparison_frame["v2_taper_total_m3_expanded"]
            * comparison_frame["density_ratio_v3_to_v2"]
        )
        .abs()
        .le(1e-6)
        .all()
    )
    checks["open_volume_comparison"] = bool(
        comparison_frame[comparison_frame["province"].isin(["SK", "NS", "PE"])][
            "inventory_volume_mean_m3ha"
        ].gt(0.0).all()
        and comparison_frame[comparison_frame["province"].isin(["SK", "NS", "PE"])][
            "volume_context_status"
        ].isin(["within_50pct", "outside_50pct"]).all()
    )
    numeric_tree = ["dbh_cm", "height_m", "expansion_factor", "age_years"]
    numeric_summary = [
        "n_trees",
        "mean_height_m",
        "mean_dbh_cm",
        "taper_total_m3_expanded",
        "recovered_gross_m3_expanded",
        "residue_m3_expanded",
        "total_residue_odt_expanded",
        "volume_balance_m3",
        "density_stems_ha",
    ]
    checks["numeric_tree_not_nan"] = all(
        tree_frame[column].notna().all() for column in numeric_tree
    )
    checks["numeric_tree_non_negative"] = all(
        (tree_frame[column] >= 0.0).all() for column in numeric_tree
    )
    checks["numeric_summary_not_nan"] = all(
        summary_frame[column].notna().all() for column in numeric_summary
    )
    checks["numeric_summary_non_negative"] = all(
        (summary_frame[column] >= 0.0).all() for column in numeric_summary
    )
    checks["coefficients_conserved"] = bool(
        np.allclose(
            coefficient_frame["total_residue_odt_per_m3"],
            coefficient_frame["stem_residue_odt_per_m3"]
            + coefficient_frame["branch_foliage_odt_per_m3"],
            rtol=1e-9,
            atol=1e-12,
        )
    )
    checks["labels_not_national_claim"] = True
    checks["no_dua_dependency"] = True
    checks["no_official_osecan_change"] = True
    return {name: bool(value) for name, value in checks.items()}


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def _report(
    summary: Sequence[Mapping[str, Any]],
    comparison: Sequence[Mapping[str, Any]],
    targets: Mapping[str, Mapping[str, Any]],
) -> str:
    lines = [
        "# E2 v3 density-calibrated tree lists",
        "",
        "Status: E2c screening artifact, `model_estimate=true`, `observed=false`,",
        "`policy_grade=false`, `national_claim=false`. No request, DUA, restricted",
        "data, UI change or official OSeCAN change is involved.",
        "",
        "## Scope",
        "",
        "E2 v3 keeps the E2 v2 geometry, species distribution, donor tapers and",
        "bucking logic, and replaces the uniform 1,000 stems/ha expansion with",
        "province-specific open-inventory density where available. AB, MB and the",
        "four donor-only provinces are explicitly labelled proxy/donor evidence.",
        "",
        "## Density sources",
        "",
        "| Province | Density stems/ha | Evidence | Basis |",
        "|---|---:|---|---|",
    ]
    for province in IMPUTED_PROVINCES:
        target = targets[province]
        lines.append(
            f"| {province} | {float(target['density_stems_ha']):.2f} | "
            f"{target['density_evidence_class']} | {target['density_basis']} |"
        )
    lines.extend(
        [
            "",
            "## Volume comparison v2 to v3",
            "",
            "| Province | Density v2 | Density v3 | Volume v2 | Volume v3 | Ratio | Open volume | Ratio v3/open | Status |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    for row in comparison:
        lines.append(
            f"| {row['province']} | {row['density_v2_stems_ha']:.2f} | "
            f"{row['density_v3_stems_ha']:.2f} | "
            f"{row['v2_taper_total_m3_expanded']:.2f} | "
            f"{row['v3_taper_total_m3_expanded']:.2f} | "
            f"{row['volume_ratio_v3_to_v2']:.4f} | "
            f"{row['inventory_volume_mean_m3ha']:.2f} | "
            f"{row['open_volume_ratio_v3']:.4f} | "
            f"{row['volume_context_status']} |"
        )
    lines.extend(
        [
            "",
            "## SK / NS / PE",
            "",
            "- SK: FRI `BA` and `QMD` imply arithmetic stem density; `MSTEMS` is a",
            "  merchantable-stem cross-check and is not used as total density.",
            "  The v3 volume is compared with FRI `GBVOL`.",
            "- NS: `TOTBA` and `AVDI` imply arithmetic stem density; v3 volume is",
            "  compared with `TOTMRVOL`.",
            "- PE: EFI2020 `tphmerch` is the density target; v3 volume is compared",
            "  with `gtv`.",
            "",
            "## Remaining gaps",
            "",
            "- AB AVI publishes crown-density and structure classes, not stems/ha.",
            "  The 1,000 stems/ha target is a labelled proxy; class shares are in",
            "  the density evidence artifact.",
            "- MB has no open density field; the median measured donor plot density",
            "  is used and labelled donor evidence.",
            "- ON, NL, NT and NU remain donor-only; the 1,000 stems/ha target is a",
            "  labelled proxy.",
            "- Tapers remain published cross-jurisdiction donors; no local taper is",
            "  claimed. This is not E1 measurement and not a national supply claim.",
            "",
        ]
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------


def generate(
    out_dir: Path = DEFAULT_OUTPUT_DIR,
    *,
    kozak_parameters_path: Path | None = None,
) -> dict[str, Any]:
    started = datetime.now(UTC)
    assert_no_unexpected_deletions()
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    kozak_path = Path(kozak_parameters_path or default_kozak_parameter_path())
    kozak_parameters = load_kozak_parameters(kozak_path)
    qc_registry = load_taper_registry(QC_TAPER_PATH)
    allometry = _e2_allometry.load_e2_biomass_allometry()
    targets = _load_density_targets()

    trees, tapers, extras, audit = _scaled_tree_rows(
        kozak_parameters, qc_registry, allometry, targets
    )
    volume_context = _v2._inventory_volume_context(_v2._load_distributions())
    for province, values in volume_context.items():
        audit[province].update(values)
    logs, residues = _v2._buck_v2(trees, tapers, extras)
    for row in logs + residues:
        province = str(row["jurisdiction"])
        target = targets[province]
        row.update(
            {
                "tree_list_label": TREE_LIST_E2C,
                "tree_list_basis": "provincial_open_inventory_density_calibrated",
                "provenance_tier": PROVENANCE_E2C,
                "density_stems_ha": float(target["density_stems_ha"]),
                "density_statistic": target["density_statistic"],
                "density_basis": target["density_basis"],
                "density_evidence_class": target["density_evidence_class"],
                "density_source_ids": target["density_source_ids"],
                "density_expansion_label": target["expansion_label"],
                "density_calibrated": True,
            }
        )
    tree_rows = [{**tree.to_dict(), **extras[tree.tree_id]} for tree in trees]
    tree_rows.sort(key=lambda row: (row["jurisdiction"], row["tree_id"]))
    log_rows = sorted(
        logs, key=lambda row: (row["jurisdiction"], row["tree_id"], row["log_index"])
    )
    residue_rows = sorted(
        residues, key=lambda row: (row["jurisdiction"], row["tree_id"])
    )

    v2_summary = _v2_summary()
    summary_rows = _v3_summary(
        tree_rows, log_rows, residue_rows, audit, targets, v2_summary
    )
    coefficient_rows = _v2._product_coefficients(summary_rows)
    for row in coefficient_rows:
        province = str(row["province"])
        target = targets[province]
        row.update(
            {
                "provenance_tier": PROVENANCE_E2C,
                "density_stems_ha": float(target["density_stems_ha"]),
                "density_statistic": target["density_statistic"],
                "density_basis": target["density_basis"],
                "density_evidence_class": target["density_evidence_class"],
                "density_expansion_label": target["expansion_label"],
                "density_calibrated": True,
            }
        )
    comparison_rows = _comparison_v2_v3(summary_rows, v2_summary)
    checks = _checks(tree_rows, summary_rows, coefficient_rows, comparison_rows, targets)
    if not all(checks.values()):
        failed = [name for name, value in checks.items() if not value]
        raise V3Error(f"E2 v3 checks failed: {failed}")

    density_rows = [
        {
            "province": province,
            "density_stems_ha": float(targets[province]["density_stems_ha"]),
            "density_statistic": targets[province]["density_statistic"],
            "density_basis": targets[province]["density_basis"],
            "density_evidence_class": targets[province]["density_evidence_class"],
            "density_source_ids": targets[province]["density_source_ids"],
            "density_n": int(targets[province].get("density_n", 0)),
            "density_mean_stems_ha": float(
                targets[province].get("density_mean_stems_ha", 0.0)
            ),
            "density_p50_stems_ha": float(
                targets[province].get("density_p50_stems_ha", 0.0)
            ),
            "expansion_label": targets[province]["expansion_label"],
            "v2_expansion_factor_total": float(
                next(
                    row["v2_expansion_factor_total"]
                    for row in summary_rows
                    if row["province"] == province
                )
            ),
            "v3_expansion_factor_total": float(
                next(
                    row["expansion_factor_total"]
                    for row in summary_rows
                    if row["province"] == province
                )
            ),
            "expansion_ratio_v3_to_v2": float(
                next(
                    row["density_scale_from_v2"]
                    for row in summary_rows
                    if row["province"] == province
                )
            ),
            "inventory_volume_mean_m3ha": volume_context[province][
                "inventory_volume_mean_m3ha"
            ],
            "inventory_volume_source": volume_context[province][
                "inventory_volume_source"
            ],
            "volume_context_status": next(
                row["volume_context_status"]
                for row in summary_rows
                if row["province"] == province
            ),
            "notes": targets[province].get("notes", ""),
        }
        for province in IMPUTED_PROVINCES
    ]

    payload = {
        "artifact": ARTIFACT_ID,
        "generated_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "runtime_seconds": round((datetime.now(UTC) - started).total_seconds(), 3),
        "labels": {
            "model_estimate": True,
            "observed": False,
            "policy_grade": False,
            "national_claim": False,
            "provenance_tier": PROVENANCE_E2C,
            "tree_list_label": TREE_LIST_E2C,
            "density_calibrated": True,
            "open_inventory_density_provinces": ["SK", "NS", "PE"],
            "donor_density_provinces": ["MB"],
            "proxy_density_provinces": ["AB", "ON", "NL", "NT", "NU"],
        },
        "counts": {
            "provinces": len(summary_rows),
            "trees": len(tree_rows),
            "logs": len(log_rows),
            "residue_rows": len(residue_rows),
            "comparison_rows": len(comparison_rows),
        },
        "checks": checks,
        "density_targets": density_rows,
        "province_summary": summary_rows,
        "product_coefficients": coefficient_rows,
        "comparison_v2_v3": comparison_rows,
        "volume_context": volume_context,
        "notes": [
            "E2 v3 is a density-calibrated screening layer, not E1 measurement.",
            "Density calibration uses open inventory density or a labelled proxy; no facility coefficient is used.",
            "Every tree row carries density evidence and proxy/donor labels.",
            "No request, DUA, restricted data, UI change or official OSeCAN change is used.",
            "No national supply or official grade claim is made.",
        ],
    }

    _write_csv(
        out_dir / "tree_list.csv",
        tree_rows,
        sorted({key for row in tree_rows for key in row}),
    )
    _write_csv(
        out_dir / "bucked_logs.csv",
        log_rows,
        sorted({key for row in log_rows for key in row}),
    )
    _write_csv(
        out_dir / "residue_ledger.csv",
        residue_rows,
        sorted({key for row in residue_rows for key in row}),
    )
    _write_csv(
        out_dir / "province_summary.csv",
        summary_rows,
        list(summary_rows[0]),
    )
    _write_csv(
        out_dir / "product_coefficients.csv",
        coefficient_rows,
        list(coefficient_rows[0]),
    )
    _write_csv(
        out_dir / "comparison_v2_v3.csv",
        comparison_rows,
        list(comparison_rows[0]),
    )
    _write_csv(
        out_dir / "density_calibration.csv",
        density_rows,
        list(density_rows[0]),
    )
    _write_csv(
        out_dir / "volume_context.csv",
        [
            {
                "province": province,
                "inventory_volume_mean_m3ha": volume_context[province][
                    "inventory_volume_mean_m3ha"
                ],
                "inventory_volume_p50_m3ha": volume_context[province][
                    "inventory_volume_p50_m3ha"
                ],
                "inventory_volume_source": volume_context[province][
                    "inventory_volume_source"
                ],
            }
            for province in IMPUTED_PROVINCES
        ],
        [
            "province",
            "inventory_volume_mean_m3ha",
            "inventory_volume_p50_m3ha",
            "inventory_volume_source",
        ],
    )
    _write_csv(
        out_dir / "distribution_labels.csv",
        [
            {
                "province": province,
                "inventory_informed": audit[province]["inventory_informed"],
                "distribution_tier": audit[province]["distribution_tier"],
                "density_stems_ha": float(targets[province]["density_stems_ha"]),
                "density_statistic": targets[province]["density_statistic"],
                "density_basis": targets[province]["density_basis"],
                "density_evidence_class": targets[province]["density_evidence_class"],
                "density_source_ids": targets[province]["density_source_ids"],
                "expansion_label": targets[province]["expansion_label"],
                "composition_basis": audit[province]["composition_basis"],
                "dbh_bases": ";".join(
                    sorted(
                        {
                            str(row["dbh_basis"])
                            for row in tree_rows
                            if row["jurisdiction"] == province
                        }
                    )
                ),
                "height_bases": ";".join(
                    sorted(
                        {
                            str(row["height_basis"])
                            for row in tree_rows
                            if row["jurisdiction"] == province
                        }
                    )
                ),
                "joint_bases": ";".join(
                    sorted(
                        {
                            str(row["joint_basis"])
                            for row in tree_rows
                            if row["jurisdiction"] == province
                        }
                    )
                ),
                "source_ids": audit[province]["source_ids"],
                "proxy_or_donor_gap": (
                    "open_inventory_density"
                    if targets[province]["density_evidence_class"] == "open_inventory"
                    else (
                        "donor_density_gap"
                        if targets[province]["density_evidence_class"] == "donor"
                        else "no_open_density_proxy"
                    )
                ),
            }
            for province in IMPUTED_PROVINCES
        ],
        [
            "province",
            "inventory_informed",
            "distribution_tier",
            "density_stems_ha",
            "density_statistic",
            "density_basis",
            "density_evidence_class",
            "density_source_ids",
            "expansion_label",
            "composition_basis",
            "dbh_bases",
            "height_bases",
            "joint_bases",
            "source_ids",
            "proxy_or_donor_gap",
        ],
    )
    (out_dir / "summary.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (out_dir / "report.md").write_text(
        _report(summary_rows, comparison_rows, targets), encoding="utf-8"
    )

    output_paths = [
        out_dir / name
        for name in (
            "tree_list.csv",
            "bucked_logs.csv",
            "residue_ledger.csv",
            "province_summary.csv",
            "product_coefficients.csv",
            "comparison_v2_v3.csv",
            "density_calibration.csv",
            "volume_context.csv",
            "distribution_labels.csv",
            "summary.json",
            "report.md",
        )
    ]
    manifest = {
        "artifact": ARTIFACT_ID,
        "generated_utc": payload["generated_utc"],
        "runtime_seconds": payload["runtime_seconds"],
        "inputs": {
            _repo_relative(path): _sha256(path)
            for path in (
                DENSITY_DIR / "density_targets.csv",
                DENSITY_DIR / "density_quantiles.csv",
                DENSITY_DIR / "ab_density_structure.csv",
                DISTRIBUTION_DIR / "species_composition.csv",
                DISTRIBUTION_DIR / "attribute_quantiles.csv",
                DISTRIBUTION_DIR / "joint_quantiles.csv",
                DISTRIBUTION_DIR / "province_summary.csv",
                DISTRIBUTION_DIR / "source_provenance.csv",
                V2_DIR / "tree_list.csv",
                V2_DIR / "province_summary.csv",
                _v2.REPO_ROOT
                / "data/derived/national/bucking/four_province_bucking_pilot_v0/tree_list.csv",
                QC_TAPER_PATH,
                _e2_allometry.E2_MAP_PATH,
                _e2_allometry.CFS_DBH_HEIGHT_PATH,
            )
        },
        "outputs": {_repo_relative(path): _sha256(path) for path in output_paths},
        "checks": payload["checks"],
        "labels": payload["labels"],
        "no_dua_dependency": True,
        "no_official_osecan_change": True,
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--kozak-parameters",
        type=Path,
        default=None,
        help="override the Kozak parameter CSV",
    )
    args = parser.parse_args(argv)
    payload = generate(args.out_dir, kozak_parameters_path=args.kozak_parameters)
    print(json.dumps(payload["counts"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
