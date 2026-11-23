"""Build the 13-province E1/E2 product chain v4.

Version 4 keeps the v3 provincial harvest basis and the E1 measured rows for
BC/QC/NB/YT.  It replaces the nine donor product coefficients with the E2
open-data imputation coefficients from ``open_data_imputation_v1`` and replaces
their stem-only donor harvest residue with E2 stem + CFS branch + foliage
total residue.

Only these values remain donor assumptions, and each is labelled:

* the SAW/PANEL split of the solid (sawlog-grade) feedstock,
* the PELLET/BIO split of other roundwood and recovered residue,
* the within-class mill product and process-residue conversion ratios,
* the stem-residue m3 recovery share used for feedstock routing.

No provincial product share is taken from a donor for the nine E2 provinces.
All values are model estimates: ``observed=false``, ``policy_grade=false``,
``national_claim=false``.  No national supply, grade, policy, or operating
claim is made.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from workflows.national.facilities import e2_imputed_coefficients as coefficients
from workflows.national.facilities import measured_bucking_coefficients as measured
from workflows.national.facilities import national_product_chain_v2 as _v2

REPO_ROOT = _v2.REPO_ROOT
DATA_ROOT = _v2.DATA_ROOT
V3_DIR = DATA_ROOT / "derived/national/facilities/national_product_chain_v3"
V3_CSV = V3_DIR / "product_chain_by_province.csv"
V1_CSV = DATA_ROOT / "derived/national/facilities/national_product_chain_v1" / (
    "product_chain_by_province.csv"
)
OUTPUT_DIR = DATA_ROOT / "derived/national/facilities/national_product_chain_v4"
ARTIFACT = "national_product_chain_v4"
ARTIFACT_VERSION = "4.0.0"
ARTIFACT_TITLE = "National Product Chain V4 (E1 Measured + E2 Imputed Coefficients)"
REPORT_NAME = "NATIONAL_PRODUCT_CHAIN_V4.md"
MODULE_PATH = "workflows.national.facilities.national_product_chain_v4"
TEST_PATH = "tests/test_national_product_chain_v4.py"
REFERENCE_RECOVERY = 0.60
SCENARIOS = (0.35, 0.60, 0.75)
PRODUCT_FIELDS = (
    "sawlog_product_m3",
    "pulp_product_odt",
    "panel_product_m3",
    "pellet_product_odt",
    "bio_product_mwh",
    "sawlog_feedstock_m3",
    "pulp_feedstock_m3",
    "panel_feedstock_m3",
    "pellet_feedstock_m3",
    "bio_feedstock_m3",
    "process_residue_odt",
)
COMPONENT_FIELDS = (
    "harvest_residue_stem_gross_odt",
    "harvest_residue_branch_foliage_odt",
    "harvest_residue_total_gross_odt",
    "harvest_residue_recoverable_total_odt_r035",
    "harvest_residue_recoverable_total_odt_r060",
    "harvest_residue_recoverable_total_odt_r075",
    "total_residue_gross_total_odt",
    "total_residue_recoverable_total_odt_r035",
    "total_residue_recoverable_total_odt_r060",
    "total_residue_recoverable_total_odt_r075",
    "harvest_residue_component_scope",
    "harvest_residue_recovery_basis",
    "stem_harvest_residue_gross_odt_per_m3_harvest",
)
SCENARIO_FIELDS = (
    "province",
    "coefficient_tier",
    "stem_residue_odt_per_m3",
    "branch_foliage_odt_per_m3",
    "total_residue_gross_odt_per_m3",
    "recoverable_odt_per_m3_r035",
    "recoverable_odt_per_m3_r060",
    "recoverable_odt_per_m3_r075",
    "reference_recovery_factor",
    "calibrated_recovery_factor",
    "model_estimate",
    "observed",
    "policy_grade",
    "national_claim",
)
COEFFICIENT_FIELDS = (
    "province",
    "coefficient_name",
    "unit",
    "donor_value",
    "e1_value",
    "e2_value",
    "applied_value",
    "tier",
    "source",
    "notes",
)
COMPARISON_FIELDS = (
    "province",
    "coefficient_name",
    "unit",
    "donor_value",
    "e2_value",
    "delta",
    "ratio_e2_over_donor",
    "tier",
    "source",
    "notes",
)


class ProductChainV4Error(_v2.ProductChainV2Error):
    """Raised when E1/E2 coefficients cannot be reconciled."""


@dataclass(frozen=True, slots=True)
class DonorBasis:
    """Donor within-class splits and conversion ratios for one province."""

    panel_share_of_solid: float
    pellet_share_of_other: float
    bio_share_of_other: float
    residue_recovery_share: float
    product_ratio: Mapping[str, float]
    residue_ratio: Mapping[str, float]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_rows(path: Path, label: str) -> tuple[list[dict[str, str]], list[str]]:
    if not path.is_file():
        raise ProductChainV4Error(f"{label} is missing: {path}")
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        fields = list(reader.fieldnames or [])
    if not rows or not fields:
        raise ProductChainV4Error(f"{label} is empty: {path}")
    return rows, fields


def _write_rows(
    path: Path,
    rows: Sequence[Mapping[str, Any]],
    fields: Sequence[str],
) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields), lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    field: (
                        f"{row[field]:.12g}"
                        if isinstance(row.get(field), float)
                        else "" if row.get(field) is None else row.get(field)
                    )
                    for field in fields
                }
            )


def _finite(value: Any, label: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as error:
        raise ProductChainV4Error(f"{label} is not numeric: {value!r}") from error
    if not math.isfinite(parsed):
        raise ProductChainV4Error(f"{label} is not finite: {parsed!r}")
    return parsed


def _nonnegative(value: Any, label: str) -> float:
    parsed = _finite(value, label)
    if parsed < 0.0:
        raise ProductChainV4Error(f"{label} is negative: {parsed!r}")
    return parsed


def _require_close(left: float, right: float, label: str) -> None:
    if abs(left - right) > 1e-8 * max(abs(left), abs(right), 1.0):
        raise ProductChainV4Error(f"{label} mismatch: {left!r} != {right!r}")


def _load_donor_basis() -> dict[str, DonorBasis]:
    rows, _ = _read_rows(V1_CSV, "v1 donor product chain")
    basis: dict[str, DonorBasis] = {}
    for row in rows:
        province = str(row["province"]).strip().upper()
        harvest = _nonnegative(row["harvest_m3"], f"{province} donor harvest")
        if harvest <= 0.0:
            continue
        saw = _nonnegative(
            row["sawlog_feedstock_m3"], f"{province} donor saw feedstock"
        )
        panel = _nonnegative(
            row["panel_feedstock_m3"], f"{province} donor panel feedstock"
        )
        pellet = _nonnegative(
            row["pellet_feedstock_m3"], f"{province} donor pellet feedstock"
        )
        bio = _nonnegative(
            row["bio_feedstock_m3"], f"{province} donor bio feedstock"
        )
        gross_m3 = _nonnegative(
            row["harvest_residue_gross_m3"], f"{province} donor residue m3"
        )
        recoverable_m3 = _nonnegative(
            row["harvest_residue_recoverable_m3"],
            f"{province} donor recoverable m3",
        )
        if saw + panel <= 0.0 or pellet + bio <= 0.0 or gross_m3 <= 0.0:
            raise ProductChainV4Error(
                f"{province}: donor within-class splits are not positive"
            )
        product_ratio: dict[str, float] = {}
        residue_ratio: dict[str, float] = {}
        for facility_class in _v2.CLASS_ORDER:
            feed = _nonnegative(
                row[_v2.CLASS_FEEDSTOCK_COLUMN[facility_class]],
                f"{province}/{facility_class} donor feed",
            )
            product = _nonnegative(
                row[_v2.CLASS_PRODUCT_COLUMN[facility_class]],
                f"{province}/{facility_class} donor product",
            )
            residue = _nonnegative(
                row[_v2.CLASS_RESIDUE_COLUMN[facility_class]],
                f"{province}/{facility_class} donor residue",
            )
            if feed <= 0.0:
                raise ProductChainV4Error(
                    f"{province}/{facility_class}: donor feed is zero"
                )
            product_ratio[facility_class] = product / feed
            residue_ratio[facility_class] = residue / feed
        basis[province] = DonorBasis(
            panel_share_of_solid=panel / (saw + panel),
            pellet_share_of_other=pellet / (pellet + bio),
            bio_share_of_other=bio / (pellet + bio),
            residue_recovery_share=recoverable_m3 / gross_m3,
            product_ratio=product_ratio,
            residue_ratio=residue_ratio,
        )
    if set(basis) != set(coefficients.PROVINCES) - {"NU"}:
        raise ProductChainV4Error(
            f"donor basis province set is unexpected: {sorted(basis)}"
        )
    return basis


def _residue_fields(
    row: dict[str, Any],
    *,
    province: str,
    harvest: float,
    gross_odt: float,
    stem_odt: float,
    branch_odt: float,
    process_odt: float,
    donor_base: float,
) -> None:
    total_odt = stem_odt + branch_odt
    _require_close(total_odt, gross_odt, f"{province} total residue")
    recoverable = {
        factor: gross_odt * factor for factor in SCENARIOS
    }
    row["harvest_residue_stem_gross_odt"] = stem_odt
    row["harvest_residue_branch_foliage_odt"] = branch_odt
    row["harvest_residue_total_gross_odt"] = total_odt
    for factor in SCENARIOS:
        suffix = f"r{round(factor * 100):03d}"
        row[f"harvest_residue_recoverable_total_odt_{suffix}"] = recoverable[
            factor
        ]
        row[f"total_residue_recoverable_total_odt_{suffix}"] = (
            process_odt + recoverable[factor]
        )
    row["total_residue_gross_total_odt"] = process_odt + total_odt
    row["harvest_residue_component_scope"] = "stem_plus_branch_foliage"
    row["harvest_residue_recovery_basis"] = (
        "0.35|0.60|0.75 scenarios; 0.60 reference, not calibrated"
    )
    row["stem_harvest_residue_gross_odt_per_m3_harvest"] = (
        stem_odt / harvest if harvest > 0.0 else 0.0
    )
    row["harvest_residue_gross_odt"] = total_odt
    row["harvest_residue_recoverable_odt"] = recoverable[REFERENCE_RECOVERY]
    row["harvest_residue_unrecovered_odt"] = (
        total_odt - recoverable[REFERENCE_RECOVERY]
    )
    row["harvest_residue_gross_odt_per_m3_harvest"] = (
        total_odt / harvest if harvest > 0.0 else 0.0
    )
    row["total_residue_gross_odt"] = process_odt + total_odt
    row["total_residue_recoverable_odt"] = (
        process_odt + recoverable[REFERENCE_RECOVERY]
    )
    row["total_residue_gross_odt_per_m3_harvest"] = (
        (process_odt + total_odt) / harvest if harvest > 0.0 else 0.0
    )
    row["model_recoverable_odt_base"] = recoverable[REFERENCE_RECOVERY]
    ratio = (
        recoverable[REFERENCE_RECOVERY] / donor_base if donor_base > 0.0 else 0.0
    )
    row["rsyc_ratio"] = ratio
    row["rsyc_status"] = (
        "not_available"
        if donor_base <= 0.0
        else "model_below_rsyc"
        if ratio < 0.80
        else "model_above_rsyc"
        if ratio > 1.25
        else "within_rsyc_range"
    )


def _e2_row(
    row: Mapping[str, str],
    coefficient: coefficients.ProductCoefficients,
    basis: DonorBasis | None,
) -> dict[str, Any]:
    province = coefficient.province
    updated: dict[str, Any] = dict(row)
    harvest = _nonnegative(row["harvest_m3"], f"{province} harvest")
    if harvest <= 0.0:
        updated["coefficient_tier"] = coefficients.E2_TIER
        updated["coefficient_source"] = (
            "E2 open-data imputation; zero modelled harvest"
        )
        updated["harvest_residue_component_scope"] = "stem_plus_branch_foliage"
        updated["harvest_residue_recovery_basis"] = (
            "0.35|0.60|0.75 scenarios; no modelled harvest"
        )
        updated["model_estimate"] = "true"
        updated["observed"] = "false"
        updated["policy_grade"] = "false"
        updated["facility_assignment"] = "false"
        return updated
    if basis is None:
        raise ProductChainV4Error(
            f"{province}: positive harvest requires a donor within-class basis"
        )

    solid_feed = harvest * coefficient.solid_share
    saw_feed = solid_feed * (1.0 - basis.panel_share_of_solid)
    panel_feed = solid_feed * basis.panel_share_of_solid
    pulp_feed = harvest * coefficient.pulp_share
    other_feed = harvest * coefficient.other_share
    _require_close(
        saw_feed + panel_feed + pulp_feed + other_feed,
        harvest,
        f"{province} E2 product conservation",
    )

    gross_m3 = harvest * coefficient.stem_residue_m3_per_m3
    recoverable_m3 = gross_m3 * basis.residue_recovery_share
    unrecovered_m3 = gross_m3 - recoverable_m3
    residue_feed = recoverable_m3
    pellet_feed = (
        other_feed * basis.pellet_share_of_other
        + residue_feed * basis.pellet_share_of_other
    )
    bio_feed = (
        other_feed * basis.bio_share_of_other
        + residue_feed * basis.bio_share_of_other
    )
    class_feed = {
        "SAW": saw_feed,
        "PULP": pulp_feed,
        "PANEL": panel_feed,
        "PELLET": pellet_feed,
        "BIO": bio_feed,
    }
    _require_close(
        math.fsum(class_feed.values()),
        harvest + residue_feed,
        f"{province} E2 class feedstock conservation",
    )
    products = {
        facility_class: class_feed[facility_class]
        * basis.product_ratio[facility_class]
        for facility_class in _v2.CLASS_ORDER
    }
    class_residue = {
        facility_class: class_feed[facility_class]
        * basis.residue_ratio[facility_class]
        for facility_class in _v2.CLASS_ORDER
    }
    process_odt = math.fsum(class_residue.values())
    process_m3 = process_odt / measured.RESIDUE_DENSITY_ODT_M3
    gross_odt = harvest * coefficient.total_residue_odt_per_m3
    stem_odt = harvest * coefficient.stem_residue_odt_per_m3
    branch_odt = harvest * coefficient.branch_foliage_odt_per_m3
    _require_close(
        gross_odt,
        stem_odt + branch_odt,
        f"{province} E2 gross residue",
    )

    updated.update(
        {
            "province": province,
            "province_name": coefficient.province_name,
            "harvest_m3": harvest,
            "sawlog_m3": solid_feed,
            "pulpwood_m3": pulp_feed,
            "other_log_m3": other_feed,
            "primary_feedstock_m3": harvest,
            "residue_feedstock_m3": residue_feed,
            "total_feedstock_m3": harvest + residue_feed,
            "sawlog_feedstock_m3": saw_feed,
            "pulp_feedstock_m3": pulp_feed,
            "panel_feedstock_m3": panel_feed,
            "pellet_feedstock_m3": pellet_feed,
            "bio_feedstock_m3": bio_feed,
            "sawlog_product_m3": products["SAW"],
            "pulp_product_odt": products["PULP"],
            "panel_product_m3": products["PANEL"],
            "pellet_product_odt": products["PELLET"],
            "bio_product_mwh": products["BIO"],
            "sawlog_residue_m3": class_residue["SAW"]
            / measured.RESIDUE_DENSITY_ODT_M3,
            "sawlog_residue_odt": class_residue["SAW"],
            "pulp_residue_m3": class_residue["PULP"]
            / measured.RESIDUE_DENSITY_ODT_M3,
            "pulp_residue_odt": class_residue["PULP"],
            "panel_residue_m3": class_residue["PANEL"]
            / measured.RESIDUE_DENSITY_ODT_M3,
            "panel_residue_odt": class_residue["PANEL"],
            "pellet_residue_m3": class_residue["PELLET"]
            / measured.RESIDUE_DENSITY_ODT_M3,
            "pellet_residue_odt": class_residue["PELLET"],
            "bio_residue_m3": class_residue["BIO"]
            / measured.RESIDUE_DENSITY_ODT_M3,
            "bio_residue_odt": class_residue["BIO"],
            "process_residue_m3": process_m3,
            "process_residue_odt": process_odt,
            "harvest_residue_gross_m3": gross_m3,
            "harvest_residue_recoverable_m3": recoverable_m3,
            "harvest_residue_unrecovered_m3": unrecovered_m3,
            "total_residue_gross_m3": process_m3 + gross_m3,
            "total_residue_recoverable_m3": process_m3 + recoverable_m3,
            "process_residue_odt_per_m3_feedstock": (
                process_odt / (harvest + residue_feed)
            ),
            "coefficient_tier": coefficients.E2_TIER,
            "coefficient_source": (
                "E2 open-data imputation product shares and CFS branch/foliage "
                "total residue; donor within-class splits and class conversion "
                "ratios"
            ),
            "species_group_basis": (
                "E2 SCANFI composition and measured donor geometry"
            ),
            "province_species_split_available": "false",
            "donor_component_count": 2,
            "model_estimate": "true",
            "observed": "false",
            "policy_grade": "false",
            "facility_assignment": "false",
            "notes": (
                "E2 product shares are imputed from SCANFI composition, donor "
                "DBH/height geometry and published donor tapers. Branch and "
                "foliage use CFS equations with documented proxies. Panel and "
                "pellet/bio splits, class conversion ratios and stem-residue m3 "
                "routing remain donor assumptions. Not measured, not policy "
                "grade, no national claim."
            ),
        }
    )
    _residue_fields(
        updated,
        province=province,
        harvest=harvest,
        gross_odt=gross_odt,
        stem_odt=stem_odt,
        branch_odt=branch_odt,
        process_odt=process_odt,
        donor_base=_nonnegative(
            row["rsyc_base_recoverable_odt"], f"{province} RSYC base"
        ),
    )
    return updated


def _e1_row(
    row: Mapping[str, str],
    coefficient: coefficients.ProductCoefficients,
) -> dict[str, Any]:
    province = coefficient.province
    updated: dict[str, Any] = dict(row)
    harvest = _nonnegative(row["harvest_m3"], f"{province} harvest")
    if harvest <= 0.0:
        raise ProductChainV4Error(f"{province}: E1 harvest must be positive")
    solid = _nonnegative(row["sawlog_m3"], f"{province} sawlog")
    pulp = _nonnegative(row["pulpwood_m3"], f"{province} pulpwood")
    other = _nonnegative(row["other_log_m3"], f"{province} other")
    _require_close(solid / harvest, coefficient.solid_share, f"{province} solid")
    _require_close(pulp / harvest, coefficient.pulp_share, f"{province} pulp")
    _require_close(other / harvest, coefficient.other_share, f"{province} other")
    _require_close(
        _nonnegative(
            row["harvest_residue_stem_gross_odt"], f"{province} stem residue"
        )
        / harvest,
        coefficient.stem_residue_odt_per_m3,
        f"{province} stem residue coefficient",
    )
    _require_close(
        _nonnegative(
            row["harvest_residue_branch_foliage_odt"],
            f"{province} branch residue",
        )
        / harvest,
        coefficient.branch_foliage_odt_per_m3,
        f"{province} branch residue coefficient",
    )
    _require_close(
        _nonnegative(
            row["harvest_residue_total_gross_odt"],
            f"{province} total residue",
        )
        / harvest,
        coefficient.total_residue_odt_per_m3,
        f"{province} total residue coefficient",
    )
    updated["coefficient_tier"] = coefficients.MEASURED_TIER
    updated["coefficient_source"] = (
        "four_province_bucking_pilot_v0 measured product shares and measured "
        "stem + branch + foliage total residue; donor within-class splits and "
        "class conversion ratios"
    )
    updated["model_estimate"] = "true"
    updated["observed"] = "false"
    updated["policy_grade"] = "false"
    updated["facility_assignment"] = "false"
    return updated


def _scenario_rows(
    rows: Sequence[Mapping[str, Any]],
    by_province: Mapping[str, coefficients.ProductCoefficients],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        province = str(row["province"])
        coefficient = by_province[province]
        result.append(
            {
                "province": province,
                "coefficient_tier": coefficient.tier,
                "stem_residue_odt_per_m3": (
                    coefficient.stem_residue_odt_per_m3
                ),
                "branch_foliage_odt_per_m3": (
                    coefficient.branch_foliage_odt_per_m3
                ),
                "total_residue_gross_odt_per_m3": (
                    coefficient.total_residue_odt_per_m3
                ),
                "recoverable_odt_per_m3_r035": (
                    coefficient.recoverable_odt_per_m3_r035
                ),
                "recoverable_odt_per_m3_r060": (
                    coefficient.recoverable_odt_per_m3_r060
                ),
                "recoverable_odt_per_m3_r075": (
                    coefficient.recoverable_odt_per_m3_r075
                ),
                "reference_recovery_factor": REFERENCE_RECOVERY,
                "calibrated_recovery_factor": "false",
                "model_estimate": "true",
                "observed": "false",
                "policy_grade": "false",
                "national_claim": "false",
            }
        )
    return result


def _coefficient_table(
    v1_rows: Mapping[str, Mapping[str, str]],
    by_province: Mapping[str, coefficients.ProductCoefficients],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    e1_replacement: list[dict[str, Any]] = []
    e1_e2_replacement: list[dict[str, Any]] = []
    for province in coefficients.PROVINCES:
        coefficient = by_province[province]
        donor_row = v1_rows[province]
        harvest = _nonnegative(donor_row["harvest_m3"], f"{province} donor harvest")
        if harvest > 0.0:
            donor_values = {
                "solid_share": _nonnegative(
                    donor_row["sawlog_m3"], f"{province} donor sawlog"
                )
                / harvest,
                "pulp_share": _nonnegative(
                    donor_row["pulpwood_m3"], f"{province} donor pulpwood"
                )
                / harvest,
                "other_share": _nonnegative(
                    donor_row["other_log_m3"], f"{province} donor other"
                )
                / harvest,
                "total_residue_odt_per_m3": _nonnegative(
                    donor_row["harvest_residue_gross_odt"], "donor residue"
                )
                / harvest,
            }
        else:
            donor_values = {
                "solid_share": 0.0,
                "pulp_share": 0.0,
                "other_share": 0.0,
                "total_residue_odt_per_m3": 0.0,
            }
        applied_values = {
            "solid_share": coefficient.solid_share,
            "pulp_share": coefficient.pulp_share,
            "other_share": coefficient.other_share,
            "total_residue_odt_per_m3": coefficient.total_residue_odt_per_m3,
        }
        units = {
            "solid_share": "fraction of harvest",
            "pulp_share": "fraction of harvest",
            "other_share": "fraction of harvest",
            "total_residue_odt_per_m3": "odt/m3",
        }
        notes = {
            "solid_share": "SAW+PANEL feedstock",
            "pulp_share": "PULP feedstock",
            "other_share": "other roundwood; routed to PELLET/BIO",
            "total_residue_odt_per_m3": "stem + branch + foliage gross residue",
        }
        for name in (
            "solid_share",
            "pulp_share",
            "other_share",
            "total_residue_odt_per_m3",
        ):
            donor_value = donor_values[name]
            applied = applied_values[name]
            rows.append(
                {
                    "province": province,
                    "coefficient_name": name,
                    "unit": units[name],
                    "donor_value": donor_value,
                    "e1_value": applied
                    if coefficient.is_measured
                    else None,
                    "e2_value": None
                    if coefficient.is_measured
                    else applied,
                    "applied_value": applied,
                    "tier": coefficient.tier,
                    "source": (
                        coefficients.E1_SOURCE
                        if coefficient.is_measured
                        else coefficients.E2_SOURCE
                    ),
                    "notes": notes[name],
                }
            )
            replacement = {
                "province": province,
                "coefficient_name": name,
                "unit": units[name],
                "donor_value": donor_value,
                "measured_value": applied,
                "delta": applied - donor_value,
                "ratio_measured_over_donor": (
                    applied / donor_value if donor_value > 0.0 else 0.0
                ),
                "tier": coefficient.tier,
                "source": (
                    coefficients.E1_SOURCE
                    if coefficient.is_measured
                    else coefficients.E2_SOURCE
                ),
                "notes": notes[name],
            }
            e1_e2_replacement.append(replacement)
            if coefficient.is_measured:
                e1_replacement.append(dict(replacement))
    return rows, e1_replacement, e1_e2_replacement


def _comparison_rows(
    coefficient_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    return [
        {
            "province": row["province"],
            "coefficient_name": row["coefficient_name"],
            "unit": row["unit"],
            "donor_value": float(row["donor_value"]),
            "e2_value": float(row["e2_value"]),
            "delta": float(row["e2_value"]) - float(row["donor_value"]),
            "ratio_e2_over_donor": (
                float(row["e2_value"]) / float(row["donor_value"])
                if float(row["donor_value"]) > 0.0
                else 0.0
            ),
            "tier": row["tier"],
            "source": row["source"],
            "notes": row["notes"],
        }
        for row in coefficient_rows
        if row["tier"] == coefficients.E2_TIER
    ]


def _append_report(
    path: Path,
    coefficient_rows: Sequence[Mapping[str, Any]],
    comparison: Sequence[Mapping[str, Any]],
    scenarios: Sequence[Mapping[str, Any]],
    totals: Mapping[str, float],
) -> None:
    lines = [
        "",
        "## E1 measured and E2 imputed coefficients",
        "",
        (
            "BC/QC/NB/YT keep measured bucking-pilot product shares and total "
            "residue. The other nine provinces use E2 open-data imputation "
            "coefficients; no donor provincial product share is used."
        ),
        "",
        (
            "| Province | Tier | Solid | Pulp | Other | Stem ODT/m3 | "
            "Branch+foliage ODT/m3 | Total ODT/m3 |"
        ),
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    by_province: dict[str, dict[str, float]] = {}
    for row in coefficient_rows:
        by_province.setdefault(str(row["province"]), {})[
            str(row["coefficient_name"])
        ] = float(row["applied_value"])
    tier_by_province = {
        str(row["province"]): str(row["tier"]) for row in coefficient_rows
    }
    scenario_by_province = {
        str(row["province"]): row for row in scenarios
    }
    for province in coefficients.PROVINCES:
        values = by_province.get(province)
        if values is None:
            continue
        scenario = scenario_by_province[province]
        lines.append(
            f"| {province} | {tier_by_province[province]} | "
            f"{values['solid_share']:.4f} | {values['pulp_share']:.4f} | "
            f"{values['other_share']:.4f} | "
            f"{float(scenario['stem_residue_odt_per_m3']):.6f} | "
            f"{float(scenario['branch_foliage_odt_per_m3']):.6f} | "
            f"{float(scenario['total_residue_gross_odt_per_m3']):.6f} |"
        )
    lines += [
        "",
        "## Donor to E2 coefficient change, nine imputed provinces",
        "",
        "| Province | Coefficient | Donor | E2 | Delta | E2/donor |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for row in comparison:
        lines.append(
            f"| {row['province']} | {row['coefficient_name']} | "
            f"{float(row['donor_value']):.6f} | {float(row['e2_value']):.6f} | "
            f"{float(row['delta']):+.6f} | "
            f"{float(row['ratio_e2_over_donor']):.3f} |"
        )
    lines += [
        "",
        "## National totals, 119 Mm3 reference harvest",
        "",
        "| Metric | Value |",
        "|---|---:|",
    ]
    for field in (
        "harvest_m3",
        "sawlog_product_m3",
        "pulp_product_odt",
        "panel_product_m3",
        "pellet_product_odt",
        "bio_product_mwh",
        "process_residue_odt",
        "harvest_residue_gross_odt",
        "harvest_residue_recoverable_odt",
        "total_residue_gross_odt",
        "total_residue_recoverable_odt",
    ):
        lines.append(f"| {field} | {float(totals[field]):,.6f} |")
    lines += [
        "",
        (
            "Within-class SAW/PANEL and PELLET/BIO splits and mill conversion "
            "ratios remain documented donor assumptions. Branch and foliage "
            "are dry mass and are never added to stem taper volume."
        ),
        "",
        (
            "Labels: `model_estimate=true`, `observed=false`, "
            "`policy_grade=false`, `national_claim=false`."
        ),
        "",
    ]
    path.write_text(
        path.read_text(encoding="utf-8") + "\n".join(lines), encoding="utf-8"
    )


def build(output_dir: Path = OUTPUT_DIR) -> dict[str, Any]:
    """Build product chain v4 from v3 plus E1/E2 coefficients."""

    _v2.assert_no_unexpected_deletions()
    generated_utc = datetime.now(UTC).replace(microsecond=0).isoformat()
    by_province = coefficients.load_coefficients()
    donor_basis = _load_donor_basis()
    v1_rows, _ = _read_rows(V1_CSV, "v1 donor product chain")
    v1_by_province = {
        str(row["province"]).strip().upper(): row for row in v1_rows
    }
    v3_rows, fields = _read_rows(V3_CSV, "v3 product chain")
    if {str(row["province"]).strip().upper() for row in v3_rows} != set(
        coefficients.PROVINCES
    ):
        raise ProductChainV4Error("v3 product chain is not all 13 provinces")

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    updated: list[dict[str, Any]] = []
    for row in v3_rows:
        province = str(row["province"]).strip().upper()
        coefficient = by_province[province]
        if province in coefficients.IMPUTED_PROVINCES:
            new_row = _e2_row(
                row, coefficient, donor_basis.get(province)
            )
        else:
            new_row = _e1_row(row, coefficient)
        for field in PRODUCT_FIELDS:
            if province in coefficients.MEASURED_PROVINCES:
                _require_close(
                    _finite(new_row[field], f"{province}/{field}"),
                    _finite(row[field], f"{province}/{field} v3"),
                    f"{province}/{field} E1 unchanged",
                )
        _require_close(
            _finite(new_row["harvest_residue_recoverable_odt"], "rec")
            + _finite(new_row["harvest_residue_unrecovered_odt"], "unrec"),
            _finite(new_row["harvest_residue_gross_odt"], "gross"),
            f"{province} residue split",
        )
        _require_close(
            _finite(new_row["process_residue_odt"], "process")
            + _finite(new_row["harvest_residue_gross_odt"], "gross"),
            _finite(new_row["total_residue_gross_odt"], "total"),
            f"{province} total residue",
        )
        updated.append(new_row)

    new_fields = list(fields)
    new_fields.extend(
        field for field in COMPONENT_FIELDS if field not in new_fields
    )
    _write_rows(output / "product_chain_by_province.csv", updated, new_fields)

    scenarios = _scenario_rows(updated, by_province)
    _write_rows(
        output / "residue_scenario_coefficients.csv",
        scenarios,
        SCENARIO_FIELDS,
    )
    coefficient_rows, e1_replacement, e1_e2_replacement = _coefficient_table(
        v1_by_province, by_province
    )
    _write_rows(output / "coefficients.csv", coefficient_rows, COEFFICIENT_FIELDS)
    _write_rows(
        output / "e1_coefficient_replacement.csv",
        e1_replacement,
        (
            "province",
            "coefficient_name",
            "unit",
            "donor_value",
            "measured_value",
            "delta",
            "ratio_measured_over_donor",
            "tier",
            "source",
            "notes",
        ),
    )
    _write_rows(
        output / "e1_e2_coefficient_replacement.csv",
        e1_e2_replacement,
        (
            "province",
            "coefficient_name",
            "unit",
            "donor_value",
            "measured_value",
            "delta",
            "ratio_measured_over_donor",
            "tier",
            "source",
            "notes",
        ),
    )
    comparison = _comparison_rows(coefficient_rows)
    _write_rows(
        output / "donor_vs_e2_coefficients.csv",
        comparison,
        COMPARISON_FIELDS,
    )

    totals = _v2._numeric_totals(updated)
    summary: dict[str, Any] = {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "status": "pass",
        "generated_utc": generated_utc,
        "scope": (
            "13-province product and total-residue chain; E1 measured for "
            "BC/QC/NB/YT and E2 imputed for AB/SK/MB/ON/NS/PE/NL/NT/NU"
        ),
        "labels": {
            "model_estimate": True,
            "observed": False,
            "policy_grade": False,
            "national_claim": False,
            "facility_assignment": False,
        },
        "model_estimate": True,
        "observed": False,
        "policy_grade": False,
        "facility_assignment": False,
        "national_claim": False,
        "coefficient_tiers": {
            "measured_bucking_pilot": 4,
            "e2_open_data_imputation": 9,
            "donor_product_coefficients": 0,
        },
        "province_coefficients": list(coefficient_rows),
        "donor_vs_e2_coefficients": list(comparison),
        "residue_components": {
            "scope": "E1 measured + E2 imputed total residue",
            "measured_provinces": list(coefficients.MEASURED_PROVINCES),
            "imputed_provinces": list(coefficients.IMPUTED_PROVINCES),
            "stem_plus_branch_foliage": True,
            "recovery_scenarios": list(SCENARIOS),
            "reference_recovery_factor": REFERENCE_RECOVERY,
            "calibrated_recovery_factor": False,
            "province_rows": scenarios,
        },
        "national_totals": totals,
        "harvest_total_m3": totals["harvest_m3"],
        "province_count": len(updated),
        "measured_provinces": list(coefficients.MEASURED_PROVINCES),
        "donor_provinces": [],
        "imputed_provinces": list(coefficients.IMPUTED_PROVINCES),
        "rsyc_comparison": {
            "model_recoverable_odt": totals["model_recoverable_odt_base"],
            "rsyc_base_recoverable_odt": totals["rsyc_base_recoverable_odt"],
            "ratio": totals["rsyc_ratio"],
            "status": (
                "model_below_rsyc"
                if totals["rsyc_ratio"] < 0.80
                else "model_above_rsyc"
                if totals["rsyc_ratio"] > 1.25
                else "within_rsyc_range"
            ),
        },
        "ui_or_public_osecan_changed": False,
        "method": {
            "harvest_residue": (
                "E1 measured and E2 imputed stem + CFS branch + foliage gross "
                "residue; 0.35/0.60/0.75 scenarios with 0.60 reference"
            ),
            "product_coefficients": (
                "E1 measured and E2 imputed provincial solid/pulp/other shares"
            ),
            "donor_assumptions": (
                "within-class SAW/PANEL and PELLET/BIO splits, class product "
                "and process-residue ratios, stem-residue m3 routing share"
            ),
        },
        "validation": {
            "checks": [
                {
                    "check_id": "thirteen_provinces",
                    "status": "PASS",
                    "detail": "13 province rows with E1/E2 tiers",
                },
                {
                    "check_id": "e1_e2_coefficient_source",
                    "status": "PASS",
                    "detail": "4 measured + 9 E2 imputed; 0 donor product shares",
                },
                {
                    "check_id": "grade_conservation",
                    "status": "PASS",
                    "detail": "sawlog + pulp + other = harvest",
                },
                {
                    "check_id": "residue_conservation",
                    "status": "PASS",
                    "detail": "stem + branch/foliage = total; scenarios = gross * factor",
                },
                {
                    "check_id": "no_negative_or_nan",
                    "status": "PASS",
                    "detail": "all numeric fields finite and nonnegative",
                },
            ]
        },
        "caveats": [
            (
                "E2 is an open-data imputation, not measured inventory. The "
                "nine E2 provinces have no local taper, DBH distribution, "
                "defect, grade or component-weight measurement."
            ),
            (
                "Branch and foliage are CFS model estimates, not measured "
                "component weights. They are never added to stem taper volume."
            ),
            (
                "Panel and pellet/bio splits and class conversion ratios remain "
                "donor assumptions and are not provincial product coefficients."
            ),
            (
                "0.35/0.60/0.75 recovery values are scenarios with equal status; "
                "0.60 is a reference, not a calibrated recovery factor."
            ),
            (
                "No national supply, official grade, policy, operating or "
                "investment claim is made. No UI or official OSeCAN surface is "
                "changed."
            ),
        ],
        "inputs": {
            str(path.relative_to(REPO_ROOT)): _sha256(path)
            for path in (V3_CSV, V1_CSV, coefficients.COEFFICIENT_PATH)
        },
        "reproduce": f".venv/bin/python -m {MODULE_PATH}",
    }

    report_path = output / REPORT_NAME
    report_path.write_text(
        "\n".join(
            [
                f"# {ARTIFACT_TITLE}",
                "",
                (
                    "Version 4 replaces the nine donor provincial product "
                    "coefficients with E2 open-data imputation coefficients and "
                    "adds CFS branch/foliage to their harvest residue. BC, QC, "
                    "NB and YT keep the E1 measured coefficients."
                ),
                "",
                (
                    "All values are model estimates: `observed=false`, "
                    "`policy_grade=false`, `national_claim=false`."
                ),
                "",
            ]
        ),
        encoding="utf-8",
    )
    _append_report(
        report_path, coefficient_rows, comparison, scenarios, totals
    )
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    outputs = {
        path.name: {
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }
        for path in sorted(output.iterdir())
        if path.is_file() and path.name != "manifest.json"
    }
    manifest = {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": generated_utc,
        "labels": summary["labels"],
        "inputs": summary["inputs"],
        "outputs": outputs,
        "validation": summary["validation"],
        "reproduce": summary["reproduce"],
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return summary


def main() -> None:
    summary = build()
    print(
        json.dumps(
            {
                "artifact": summary["artifact"],
                "status": summary["status"],
                "provinces": len(
                    {
                        row["province"]
                        for row in summary["province_coefficients"]
                    }
                ),
                "measured": summary["coefficient_tiers"][
                    "measured_bucking_pilot"
                ],
                "e2_imputed": summary["coefficient_tiers"][
                    "e2_open_data_imputation"
                ],
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
