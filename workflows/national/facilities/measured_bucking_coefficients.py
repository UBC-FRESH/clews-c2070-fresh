"""Parse measured four-province bucking coefficients for facility chains.

The pilot publishes expansion-weighted shares of recovered log volume for
BC, QC, NB and YT.  This module is the single trusted boundary between that
pilot artifact and the facility product chain.  It deliberately exposes only
the three measured product coefficients the pilot supports:

* ``solid`` -- sawlog-grade share; the facility chain splits it into SAW and
  PANEL feedstock using the donor within-solid split,
* ``pulp`` -- pulpwood-grade share; routed to PULP,
* ``other`` -- other-roundwood share; routed to PELLET/BIO using the donor
  within-other split because the facility chain has no OTHER class,
* ``harvest_residue_odt_per_m3`` -- gross harvest residue per recovered m3.

The pilot does not measure mill conversion, panel share of solid, pellet/bio
split, or residue recovery.  Those stay explicitly labelled donor assumptions.
No value is invented for the other nine jurisdictions.
"""

from __future__ import annotations

import csv
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
PILOT_DIR = (
    REPO_ROOT
    / "data/derived/national/bucking/four_province_bucking_pilot_v0"
)
PILOT_SUMMARY = PILOT_DIR / "summary.json"
PILOT_COMPARISON = PILOT_DIR / "coefficient_comparison.csv"
V1_PRODUCT_CHAIN = (
    REPO_ROOT
    / "data/derived/national/facilities/national_product_chain_v1"
    / "product_chain_by_province.csv"
)

MEASURED_PROVINCES: tuple[str, ...] = ("BC", "QC", "NB", "YT")
MEASURED_TIER = "measured_bucking_pilot"
DONOR_TIER = "donor_coefficient"
RESIDUE_DENSITY_ODT_M3 = 0.45
RSYC_BASE_RECOVERY = 0.60
SUM_TOLERANCE = 1e-9
RECOVERY_SCENARIOS: tuple[float, ...] = (0.35, 0.60, 0.75)


class MeasuredCoefficientError(RuntimeError):
    """Raised when a measured coefficient cannot be trusted."""


@dataclass(frozen=True)
class MeasuredCoefficients:
    """One province's measured product and harvest-residue coefficients."""

    province: str
    province_name: str
    solid_share: float
    pulp_share: float
    other_share: float
    harvest_residue_odt_per_m3: float
    harvest_residue_m3_per_m3: float
    harvest_residue_total_odt_per_m3: float
    branch_foliage_odt_per_m3: float
    recoverable_odt_per_m3_r035: float
    recoverable_odt_per_m3_r060: float
    recoverable_odt_per_m3_r075: float
    taper_status: str
    taper_jurisdictions: str
    tree_list_basis: str
    n_trees: int


@dataclass(frozen=True)
class DonorSplits:
    """Donor assumptions used only where the pilot has no measurement."""

    panel_share_of_solid: float
    pellet_share_of_other: float
    bio_share_of_other: float
    residue_recovery_share: float


def _read_csv(path: Path, label: str) -> list[dict[str, str]]:
    if not path.is_file():
        raise MeasuredCoefficientError(f"{label} does not exist: {path}")
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise MeasuredCoefficientError(f"{label} is empty: {path}")
    return rows


def _finite_shares(value: Any, label: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as error:
        raise MeasuredCoefficientError(
            f"{label} is not numeric: {value!r}"
        ) from error
    if not math.isfinite(parsed):
        raise MeasuredCoefficientError(f"{label} is not finite: {parsed!r}")
    if parsed < 0.0 or parsed > 1.0:
        raise MeasuredCoefficientError(
            f"{label} is outside [0, 1]: {parsed!r}"
        )
    return parsed


def _finite_positive(value: Any, label: str) -> float:
    parsed = _finite_shares(value, label)
    if parsed <= 0.0:
        raise MeasuredCoefficientError(f"{label} must be positive: {parsed!r}")
    return parsed


def _require_sum_to_one(values: Mapping[str, float], label: str) -> None:
    total = math.fsum(values.values())
    if abs(total - 1.0) > SUM_TOLERANCE:
        raise MeasuredCoefficientError(
            f"{label} shares sum to {total!r}, not 1"
        )


def load_measured_coefficients() -> dict[str, MeasuredCoefficients]:
    """Parse the pilot summary and return the four measured provinces."""

    if not PILOT_SUMMARY.is_file():
        raise MeasuredCoefficientError(
            f"pilot summary is missing: {PILOT_SUMMARY}"
        )
    payload = json.loads(PILOT_SUMMARY.read_text(encoding="utf-8"))
    if payload.get("artifact") != "four_province_bucking_pilot_v0":
        raise MeasuredCoefficientError(
            f"unexpected pilot artifact: {payload.get('artifact')!r}"
        )
    checks = payload.get("checks", {})
    for check in (
        "exactly_four_provinces",
        "all_measured",
        "no_national_claim",
        "no_synthetic",
        "all_measured_trees_have_allometry",
        "allometry_conservation",
        "allometry_recovery_scenarios",
    ):
        if checks.get(check) is not True:
            raise MeasuredCoefficientError(f"pilot check failed: {check}")
    labels = payload.get("labels", {})
    if labels.get("observed") is not False:
        raise MeasuredCoefficientError("pilot is not labelled observed=false")
    if labels.get("policy_grade") is not False:
        raise MeasuredCoefficientError(
            "pilot is not labelled policy_grade=false"
        )
    if labels.get("national_claim") is not False:
        raise MeasuredCoefficientError(
            "pilot is not labelled national_claim=false"
        )

    comparison = {
        row["province"].strip().upper(): row
        for row in _read_csv(PILOT_COMPARISON, "pilot coefficient comparison")
    }
    if set(comparison) != set(MEASURED_PROVINCES):
        raise MeasuredCoefficientError(
            "pilot coefficient comparison province set is not BC/QC/NB/YT: "
            f"{sorted(comparison)}"
        )

    result: dict[str, MeasuredCoefficients] = {}
    for province in MEASURED_PROVINCES:
        row = comparison[province]
        solid = _finite_shares(
            row["model_sawlog_share"], f"{province} measured solid share"
        )
        pulp = _finite_shares(
            row["model_pulpwood_share"], f"{province} measured pulp share"
        )
        other = _finite_shares(
            row["model_other_share"], f"{province} measured other share"
        )
        _require_sum_to_one(
            {"solid": solid, "pulp": pulp, "other": other},
            f"{province} measured product",
        )
        residue_odt = _finite_positive(
            row["model_residue_odt_per_recovered_m3"],
            f"{province} measured residue ODT/m3",
        )
        residue_m3 = _finite_positive(
            row["model_residue_m3_per_recovered_m3"],
            f"{province} measured residue m3/m3",
        )
        total_residue = _finite_positive(
            row["model_total_residue_gross_odt_per_recovered_m3"],
            f"{province} measured total residue ODT/m3",
        )
        branch_foliage = _finite_positive(
            row["model_branch_foliage_odt_per_recovered_m3"],
            f"{province} measured branch/foliage ODT/m3",
        )
        recoverable = {
            factor: _finite_positive(
                row[
                    "model_total_residue_recoverable_odt_per_recovered_m3_"
                    f"r{int(round(factor * 100)):03d}"
                ],
                f"{province} measured recoverable ODT/m3 at {factor}",
            )
            for factor in RECOVERY_SCENARIOS
        }
        if not math.isclose(
            total_residue,
            residue_odt + branch_foliage,
            rel_tol=1e-9,
            abs_tol=1e-12,
        ):
            raise MeasuredCoefficientError(
                f"{province}: total residue does not equal stem + branch/foliage"
            )
        if not all(
            recoverable[left] < recoverable[right]
            for left, right in zip(RECOVERY_SCENARIOS, RECOVERY_SCENARIOS[1:])
        ):
            raise MeasuredCoefficientError(
                f"{province}: residue recovery scenarios are not increasing"
            )
        for factor in RECOVERY_SCENARIOS:
            if not math.isclose(
                recoverable[factor],
                total_residue * factor,
                rel_tol=1e-9,
                abs_tol=1e-12,
            ):
                raise MeasuredCoefficientError(
                    f"{province}: recoverable residue at {factor} is not "
                    "gross * factor"
                )
        result[province] = MeasuredCoefficients(
            province=province,
            province_name=row["province_name"].strip(),
            solid_share=solid,
            pulp_share=pulp,
            other_share=other,
            harvest_residue_odt_per_m3=residue_odt,
            harvest_residue_m3_per_m3=residue_m3,
            harvest_residue_total_odt_per_m3=total_residue,
            branch_foliage_odt_per_m3=branch_foliage,
            recoverable_odt_per_m3_r035=recoverable[0.35],
            recoverable_odt_per_m3_r060=recoverable[0.60],
            recoverable_odt_per_m3_r075=recoverable[0.75],
            taper_status=row["taper_status"].strip(),
            taper_jurisdictions=row["taper_jurisdictions"].strip(),
            tree_list_basis=row["tree_list_basis"].strip(),
            n_trees=int(row["model_n_trees"]),
        )
    return result


def load_donor_splits() -> dict[str, DonorSplits]:
    """Read within-group donor splits from the v1 province rows.

    These are routing/technology splits, not provincial product shares.  They
    are retained only where the pilot has no measurement.
    """

    rows = _read_csv(V1_PRODUCT_CHAIN, "v1 product-chain province table")
    result: dict[str, DonorSplits] = {}
    for row in rows:
        province = row["province"].strip().upper()
        harvest = float(row["harvest_m3"])
        if harvest <= 0.0:
            continue
        saw = float(row["sawlog_feedstock_m3"])
        panel = float(row["panel_feedstock_m3"])
        pellet = float(row["pellet_feedstock_m3"])
        bio = float(row["bio_feedstock_m3"])
        residue_m3 = float(row["harvest_residue_gross_m3"])
        recoverable_m3 = float(row["harvest_residue_recoverable_m3"])
        if saw + panel <= 0.0:
            raise MeasuredCoefficientError(
                f"{province}: donor solid feedstock is not positive"
            )
        if pellet + bio <= 0.0:
            raise MeasuredCoefficientError(
                f"{province}: donor energy feedstock is not positive"
            )
        if residue_m3 <= 0.0:
            raise MeasuredCoefficientError(
                f"{province}: donor harvest residue is not positive"
            )
        panel_share = panel / (saw + panel)
        pellet_share = pellet / (pellet + bio)
        recovery_share = recoverable_m3 / residue_m3
        for label, value in (
            ("panel share of solid", panel_share),
            ("pellet share of other", pellet_share),
            ("residue recovery share", recovery_share),
        ):
            if not math.isfinite(value) or value < 0.0 or value > 1.0:
                raise MeasuredCoefficientError(
                    f"{province} {label} is outside [0, 1]: {value!r}"
                )
        result[province] = DonorSplits(
            panel_share_of_solid=panel_share,
            pellet_share_of_other=pellet_share,
            bio_share_of_other=1.0 - pellet_share,
            residue_recovery_share=recovery_share,
        )
    return result


def assert_measured_province_set(
    measured: Mapping[str, MeasuredCoefficients],
) -> None:
    """Fail closed if the measured province set is not exactly BC/QC/NB/YT."""

    if set(measured) != set(MEASURED_PROVINCES):
        raise MeasuredCoefficientError(
            f"measured province set is not {MEASURED_PROVINCES}: "
            f"{sorted(measured)}"
        )
