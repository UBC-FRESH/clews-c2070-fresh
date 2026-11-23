"""Trusted boundary for the 13-province E1/E2 product coefficients.

The E1 measured rows (BC/QC/NB/YT) and E2 imputed rows (AB/SK/MB/ON/NS/PE/NL/NT/NU)
come from ``open_data_imputation_v1/product_coefficients.csv``.  The reader
fails closed unless:

* all 13 jurisdictions are present with the expected tiers;
* E1 is ``measured_bucking_pilot`` and E2 is ``e2_open_data_imputation``;
* the E2 product source is the imputation, never a donor coefficient;
* solid + pulp + other shares sum to one where recovered volume is positive;
* total residue equals stem + branch/foliage and the 0.35/0.60/0.75
  scenarios equal gross total times the factor.

Within-class SAW/PANEL and PELLET/BIO splits and mill conversion ratios are
not provincial product coefficients.  They remain documented donor assumptions
owned by the product-chain builder.
"""

from __future__ import annotations

import csv
import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
COEFFICIENT_PATH = (
    REPO_ROOT
    / "data/derived/national/bucking/open_data_imputation_v1"
    / "product_coefficients.csv"
)

PROVINCES: tuple[str, ...] = (
    "BC",
    "AB",
    "SK",
    "MB",
    "ON",
    "QC",
    "NB",
    "NS",
    "PE",
    "NL",
    "YT",
    "NT",
    "NU",
)
MEASURED_PROVINCES: tuple[str, ...] = ("BC", "QC", "NB", "YT")
IMPUTED_PROVINCES: tuple[str, ...] = (
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
MEASURED_TIER = "measured_bucking_pilot"
E2_TIER = "e2_open_data_imputation"
E1_SOURCE = "E1_four_province_bucking_pilot_measured"
E2_SOURCE = "E2_open_data_imputation"
SUM_TOLERANCE = 1e-9
RECOVERY_SCENARIOS: tuple[float, ...] = (0.35, 0.60, 0.75)
REFERENCE_RECOVERY = 0.60


class CoefficientSourceError(RuntimeError):
    """Raised when an E1/E2 coefficient row cannot be trusted."""


@dataclass(frozen=True, slots=True)
class ProductCoefficients:
    """One province's trusted product and total-residue coefficients."""

    province: str
    province_name: str
    tier: str
    solid_share: float
    pulp_share: float
    other_share: float
    stem_residue_odt_per_m3: float
    branch_foliage_odt_per_m3: float
    total_residue_odt_per_m3: float
    stem_residue_m3_per_m3: float
    recoverable_odt_per_m3_r035: float
    recoverable_odt_per_m3_r060: float
    recoverable_odt_per_m3_r075: float
    n_trees: int

    @property
    def is_measured(self) -> bool:
        return self.tier == MEASURED_TIER


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise CoefficientSourceError(
            f"E1/E2 coefficient table is missing: {path}"
        )
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise CoefficientSourceError(f"E1/E2 coefficient table is empty: {path}")
    return rows


def _finite(value: Any, label: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as error:
        raise CoefficientSourceError(f"{label} is not numeric: {value!r}") from error
    if not math.isfinite(parsed) or parsed < 0.0:
        raise CoefficientSourceError(
            f"{label} is not finite and nonnegative: {parsed!r}"
        )
    return parsed


def _parse_bool(value: Any, label: str) -> bool:
    text = str(value).strip().lower()
    if text == "true":
        return True
    if text == "false":
        return False
    raise CoefficientSourceError(f"{label} is not a boolean: {value!r}")


def _require_close(left: float, right: float, label: str) -> None:
    if abs(left - right) > SUM_TOLERANCE * max(abs(left), abs(right), 1.0):
        raise CoefficientSourceError(f"{label} mismatch: {left!r} != {right!r}")


def load_coefficients(
    path: Path = COEFFICIENT_PATH,
) -> dict[str, ProductCoefficients]:
    """Parse all 13 E1/E2 coefficient rows into trusted form."""

    rows = _read_rows(path)
    by_province: dict[str, ProductCoefficients] = {}
    for row in rows:
        province = str(row["province"]).strip().upper()
        if province not in PROVINCES:
            raise CoefficientSourceError(f"unexpected province {province!r}")
        if province in by_province:
            raise CoefficientSourceError(f"duplicate province {province}")
        tier = str(row["coefficient_tier"]).strip()
        expected_tier = (
            MEASURED_TIER if province in MEASURED_PROVINCES else E2_TIER
        )
        if tier != expected_tier:
            raise CoefficientSourceError(
                f"{province}: coefficient tier {tier!r} is not {expected_tier!r}"
            )
        product_source = str(row["product_coefficient_source"]).strip()
        expected_source = E1_SOURCE if tier == MEASURED_TIER else E2_SOURCE
        if product_source != expected_source:
            raise CoefficientSourceError(
                f"{province}: product source {product_source!r} is not "
                f"{expected_source!r}"
            )
        for label, field in (
            ("model_estimate", "model_estimate"),
            ("observed", "observed"),
            ("policy_grade", "policy_grade"),
            ("national_claim", "national_claim"),
        ):
            value = _parse_bool(row[field], f"{province} {field}")
            expected = label == "model_estimate"
            if value is not expected:
                raise CoefficientSourceError(
                    f"{province}: {field} is {value}, expected {expected}"
                )
        solid = _finite(row["solid_share"], f"{province} solid share")
        pulp = _finite(row["pulp_share"], f"{province} pulp share")
        other = _finite(row["other_share"], f"{province} other share")
        reference = _finite(
            row["reference_recovered_m3_expanded"],
            f"{province} reference recovered m3",
        )
        if reference > 0.0:
            _require_close(
                solid + pulp + other,
                1.0,
                f"{province} product shares",
            )
        stem = _finite(
            row["stem_residue_odt_per_m3"], f"{province} stem residue"
        )
        branch = _finite(
            row["branch_foliage_odt_per_m3"], f"{province} branch/foliage"
        )
        total = _finite(
            row["total_residue_odt_per_m3"], f"{province} total residue"
        )
        _require_close(total, stem + branch, f"{province} residue conservation")
        recoverable = {
            factor: _finite(
                row[
                    "recoverable_odt_per_m3_r"
                    f"{round(factor * 100):03d}"
                ],
                f"{province} recoverable at {factor}",
            )
            for factor in RECOVERY_SCENARIOS
        }
        for factor in RECOVERY_SCENARIOS:
            _require_close(
                recoverable[factor],
                total * factor,
                f"{province} recovery at {factor}",
            )
        by_province[province] = ProductCoefficients(
            province=province,
            province_name=str(row["province_name"]).strip(),
            tier=tier,
            solid_share=solid,
            pulp_share=pulp,
            other_share=other,
            stem_residue_odt_per_m3=stem,
            branch_foliage_odt_per_m3=branch,
            total_residue_odt_per_m3=total,
            stem_residue_m3_per_m3=_finite(
                row["stem_residue_m3_per_m3"], f"{province} stem residue m3"
            ),
            recoverable_odt_per_m3_r035=recoverable[0.35],
            recoverable_odt_per_m3_r060=recoverable[0.60],
            recoverable_odt_per_m3_r075=recoverable[0.75],
            n_trees=int(row["n_trees"]),
        )
    if set(by_province) != set(PROVINCES):
        missing = sorted(set(PROVINCES) - set(by_province))
        extra = sorted(set(by_province) - set(PROVINCES))
        raise CoefficientSourceError(
            f"province set mismatch: missing={missing}, extra={extra}"
        )
    measured = {
        province
        for province, coefficient in by_province.items()
        if coefficient.is_measured
    }
    if measured != set(MEASURED_PROVINCES):
        raise CoefficientSourceError(
            f"measured province set is not BC/QC/NB/YT: {sorted(measured)}"
        )
    return by_province


def e2_coefficients(
    coefficients: Mapping[str, ProductCoefficients],
) -> dict[str, ProductCoefficients]:
    """Return the nine E2 imputed rows after a set check."""

    selected = {
        province: coefficients[province] for province in IMPUTED_PROVINCES
    }
    if {row.tier for row in selected.values()} != {E2_TIER}:
        raise CoefficientSourceError("E2 rows do not all carry the E2 tier")
    return selected
