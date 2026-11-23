"""Build the measured-coefficient national product chain v2.

Version 2 replaces the BC-anchored / donor product coefficients for the four
provinces with measured bucking pilot shares:

* solid (sawlog + panel facility feedstock) = measured sawlog-grade share,
* pulp (PULP facility feedstock) = measured pulpwood-grade share,
* other (PELLET/BIO facility feedstock) = measured other-roundwood share,
* gross harvest residue ODT/m3 = measured pilot coefficient.

The pilot supports exactly those three product coefficients and the harvest
residue coefficient.  It does not measure mill conversion, the panel share of
solid, the pellet/bio split of other roundwood, or the recoverable share of
gross harvest residue.  Those remain labelled donor assumptions from
``national_product_chain_v1``.  The other nine jurisdictions keep their v1
donor coefficients unchanged.

Labels: ``model_estimate=true``, ``observed=false``, ``policy_grade=false``,
``facility_assignment=false``.  No national supply, grade, policy, or
operating claim is made.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import subprocess
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from workflows.national.facilities import measured_bucking_coefficients as measured

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_ROOT = REPO_ROOT / "data"
V1_DIR = (
    DATA_ROOT / "derived/national/facilities/national_product_chain_v1"
)
V1_CSV = V1_DIR / "product_chain_by_province.csv"
V1_SUMMARY = V1_DIR / "summary.json"
OUTPUT_DIR = (
    DATA_ROOT / "derived/national/facilities/national_product_chain_v2"
)

ARTIFACT = "national_product_chain_v2"
ARTIFACT_VERSION = "2.0.0"
ARTIFACT_TITLE = "National Product/Residue Chain v2 (Measured Four-Province Coefficients)"
REPORT_NAME = "NATIONAL_PRODUCT_CHAIN_V2.md"
MODULE_PATH = "workflows.national.facilities.national_product_chain_v2"
TEST_PATH = "tests/test_national_product_chain_v2.py"

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
CLASS_ORDER: tuple[str, ...] = ("SAW", "PULP", "PANEL", "PELLET", "BIO")
CLASS_FEEDSTOCK_COLUMN = {
    "SAW": "sawlog_feedstock_m3",
    "PULP": "pulp_feedstock_m3",
    "PANEL": "panel_feedstock_m3",
    "PELLET": "pellet_feedstock_m3",
    "BIO": "bio_feedstock_m3",
}
CLASS_PRODUCT_COLUMN = {
    "SAW": "sawlog_product_m3",
    "PULP": "pulp_product_odt",
    "PANEL": "panel_product_m3",
    "PELLET": "pellet_product_odt",
    "BIO": "bio_product_mwh",
}
CLASS_RESIDUE_COLUMN = {
    "SAW": "sawlog_residue_odt",
    "PULP": "pulp_residue_odt",
    "PANEL": "panel_residue_odt",
    "PELLET": "pellet_residue_odt",
    "BIO": "bio_residue_odt",
}
RESIDUE_DENSITY_ODT_M3 = measured.RESIDUE_DENSITY_ODT_M3
RSYC_BASE_RECOVERY = measured.RSYC_BASE_RECOVERY
REL_TOL = 1e-9
LABEL_TRUE = "true"
LABEL_FALSE = "false"


class ProductChainV2Error(RuntimeError):
    """Raised when a measured-coefficient chain invariant fails."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_deletions(repository: Path) -> list[str]:
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repository,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise ProductChainV2Error(
            f"cannot inspect git status in {repository}: {result.stderr.strip()}"
        )
    return [
        line
        for line in result.stdout.splitlines()
        if len(line) >= 2 and (line[0] == "D" or line[1] == "D")
    ]


def assert_no_unexpected_deletions() -> None:
    """Fail closed on any ``D`` in the parent or data repository status."""

    incidents: list[str] = []
    for repository in (REPO_ROOT, DATA_ROOT):
        for line in _git_deletions(repository):
            incidents.append(f"{repository.name or repository}: {line}")
    if incidents:
        raise ProductChainV2Error(
            "unexpected deletion status detected; halting: "
            + " | ".join(incidents)
        )


def _read_rows(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    if not path.is_file():
        raise ProductChainV2Error(f"v1 product chain is missing: {path}")
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        fields = list(reader.fieldnames or [])
    if not rows or not fields:
        raise ProductChainV2Error(f"v1 product chain is empty: {path}")
    return rows, fields


def _finite(value: Any, label: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as error:
        raise ProductChainV2Error(f"{label} is not numeric: {value!r}") from error
    if not math.isfinite(parsed) or parsed < 0.0:
        raise ProductChainV2Error(f"{label} is not finite and nonnegative: {parsed!r}")
    return parsed


def _require_close(left: float, right: float, label: str) -> None:
    scale = max(abs(left), abs(right), 1.0)
    if abs(left - right) > REL_TOL * scale:
        raise ProductChainV2Error(
            f"{label} mismatch: {left!r} != {right!r}"
        )


def _donor_ratios(row: Mapping[str, str]) -> dict[str, float]:
    ratios: dict[str, float] = {}
    for facility_class in CLASS_ORDER:
        feed = _finite(
            row[CLASS_FEEDSTOCK_COLUMN[facility_class]],
            f"{row['province']}/{facility_class} donor feedstock",
        )
        if feed <= 0.0:
            raise ProductChainV2Error(
                f"{row['province']}/{facility_class}: donor feedstock is zero"
            )
        product = _finite(
            row[CLASS_PRODUCT_COLUMN[facility_class]],
            f"{row['province']}/{facility_class} donor product",
        )
        residue = _finite(
            row[CLASS_RESIDUE_COLUMN[facility_class]],
            f"{row['province']}/{facility_class} donor residue",
        )
        ratios[f"product_{facility_class}"] = product / feed
        ratios[f"residue_{facility_class}"] = residue / feed
    return ratios


def _rsyc_fields(
    province: str,
    harvest_m3: float,
    gross_odt: float,
    donor_base: float,
    donor_status: str,
) -> dict[str, Any]:
    model_recoverable = gross_odt * RSYC_BASE_RECOVERY
    if donor_base <= 0.0:
        ratio = 0.0
        status = "not_available" if harvest_m3 > 0.0 else "zero_harvest"
    else:
        ratio = model_recoverable / donor_base
        if ratio < 0.80:
            status = "model_below_rsyc"
        elif ratio > 1.25:
            status = "model_above_rsyc"
        else:
            status = "within_rsyc_range"
    return {
        "rsyc_base_recoverable_odt": donor_base,
        "model_recoverable_odt_base": model_recoverable,
        "rsyc_ratio": ratio,
        "rsyc_status": status,
    }


def _measured_row(
    donor_row: Mapping[str, str],
    coefficients: measured.MeasuredCoefficients,
    splits: measured.DonorSplits,
    ratios: Mapping[str, float],
) -> dict[str, Any]:
    province = coefficients.province
    harvest = _finite(donor_row["harvest_m3"], f"{province} harvest")
    if harvest <= 0.0:
        raise ProductChainV2Error(
            f"{province}: measured coefficient replacement requires positive harvest"
        )

    solid_feed = harvest * coefficients.solid_share
    saw_feed = solid_feed * (1.0 - splits.panel_share_of_solid)
    panel_feed = solid_feed * splits.panel_share_of_solid
    pulp_feed = harvest * coefficients.pulp_share
    other_feed = harvest * coefficients.other_share
    _require_close(
        saw_feed + panel_feed + pulp_feed + other_feed,
        harvest,
        f"{province} measured product conservation",
    )

    gross_m3 = harvest * coefficients.harvest_residue_m3_per_m3
    gross_odt = harvest * coefficients.harvest_residue_odt_per_m3
    recoverable_m3 = gross_m3 * splits.residue_recovery_share
    recoverable_odt = gross_odt * splits.residue_recovery_share
    unrecovered_m3 = gross_m3 - recoverable_m3
    unrecovered_odt = gross_odt - recoverable_odt
    residue_feed = recoverable_m3

    pellet_feed = (
        other_feed * splits.pellet_share_of_other
        + residue_feed * splits.pellet_share_of_other
    )
    bio_feed = (
        other_feed * splits.bio_share_of_other
        + residue_feed * splits.bio_share_of_other
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
        f"{province} measured class feedstock conservation",
    )

    products = {
        facility_class: class_feed[facility_class]
        * ratios[f"product_{facility_class}"]
        for facility_class in CLASS_ORDER
    }
    class_residue = {
        facility_class: class_feed[facility_class]
        * ratios[f"residue_{facility_class}"]
        for facility_class in CLASS_ORDER
    }
    process_odt = math.fsum(class_residue.values())
    process_m3 = process_odt / RESIDUE_DENSITY_ODT_M3
    total_gross_odt = process_odt + gross_odt
    total_recoverable_odt = process_odt + recoverable_odt
    rsyc = _rsyc_fields(
        province,
        harvest,
        gross_odt,
        _finite(
            donor_row["rsyc_base_recoverable_odt"],
            f"{province} donor RSYC base",
        ),
        str(donor_row["rsyc_status"]),
    )

    row: dict[str, Any] = dict(donor_row)
    row.update(
        {
            "province": province,
            "province_name": coefficients.province_name,
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
            "sawlog_residue_m3": class_residue["SAW"] / RESIDUE_DENSITY_ODT_M3,
            "sawlog_residue_odt": class_residue["SAW"],
            "pulp_residue_m3": class_residue["PULP"] / RESIDUE_DENSITY_ODT_M3,
            "pulp_residue_odt": class_residue["PULP"],
            "panel_residue_m3": class_residue["PANEL"] / RESIDUE_DENSITY_ODT_M3,
            "panel_residue_odt": class_residue["PANEL"],
            "pellet_residue_m3": class_residue["PELLET"] / RESIDUE_DENSITY_ODT_M3,
            "pellet_residue_odt": class_residue["PELLET"],
            "bio_residue_m3": class_residue["BIO"] / RESIDUE_DENSITY_ODT_M3,
            "bio_residue_odt": class_residue["BIO"],
            "process_residue_m3": process_m3,
            "process_residue_odt": process_odt,
            "harvest_residue_gross_m3": gross_m3,
            "harvest_residue_gross_odt": gross_odt,
            "harvest_residue_recoverable_m3": recoverable_m3,
            "harvest_residue_recoverable_odt": recoverable_odt,
            "harvest_residue_unrecovered_m3": unrecovered_m3,
            "harvest_residue_unrecovered_odt": unrecovered_odt,
            "total_residue_gross_m3": process_m3 + gross_m3,
            "total_residue_gross_odt": total_gross_odt,
            "total_residue_recoverable_m3": process_m3 + recoverable_m3,
            "total_residue_recoverable_odt": total_recoverable_odt,
            "process_residue_odt_per_m3_feedstock": (
                process_odt / (harvest + residue_feed)
            ),
            "harvest_residue_gross_odt_per_m3_harvest": gross_odt / harvest,
            "total_residue_gross_odt_per_m3_harvest": total_gross_odt / harvest,
            "coefficient_tier": measured.MEASURED_TIER,
            "coefficient_source": (
                "four_province_bucking_pilot_v0 measured solid/pulp/other and "
                "harvest residue; donor within-group splits and class ratios"
            ),
            "species_group_basis": (
                "measured four-province aggregate; no species-group split"
            ),
            "province_species_split_available": LABEL_FALSE,
            "donor_component_count": 3,
            "model_estimate": LABEL_TRUE,
            "observed": LABEL_FALSE,
            "policy_grade": LABEL_FALSE,
            "facility_assignment": LABEL_FALSE,
            "notes": (
                "Measured solid = sawlog-grade share and replaces SAW+PANEL "
                "facility feedstock; measured pulp = pulpwood share; measured "
                "other = other-roundwood share and is routed to PELLET/BIO "
                "because the facility chain has no OTHER class. Panel split, "
                "pellet/bio split, residue recovery, and class conversion "
                "ratios remain donor assumptions. Taper coverage and hardwood "
                "limits are documented in the pilot."
            ),
            **rsyc,
        }
    )
    return row


def _donor_row(
    row: Mapping[str, str],
    label: str,
) -> dict[str, Any]:
    copied = dict(row)
    copied["coefficient_tier"] = measured.DONOR_TIER
    copied["coefficient_source"] = label
    copied["model_estimate"] = LABEL_TRUE
    copied["observed"] = LABEL_FALSE
    copied["policy_grade"] = LABEL_FALSE
    copied["facility_assignment"] = LABEL_FALSE
    return copied


def _numeric_totals(rows: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    numeric_fields = [
        "harvest_m3",
        "sawlog_m3",
        "pulpwood_m3",
        "other_log_m3",
        "primary_feedstock_m3",
        "residue_feedstock_m3",
        "total_feedstock_m3",
        "sawlog_feedstock_m3",
        "pulp_feedstock_m3",
        "panel_feedstock_m3",
        "pellet_feedstock_m3",
        "bio_feedstock_m3",
        "sawlog_product_m3",
        "pulp_product_odt",
        "panel_product_m3",
        "pellet_product_odt",
        "bio_product_mwh",
        "sawlog_residue_m3",
        "sawlog_residue_odt",
        "pulp_residue_m3",
        "pulp_residue_odt",
        "panel_residue_m3",
        "panel_residue_odt",
        "pellet_residue_m3",
        "pellet_residue_odt",
        "bio_residue_m3",
        "bio_residue_odt",
        "process_residue_m3",
        "process_residue_odt",
        "harvest_residue_gross_m3",
        "harvest_residue_gross_odt",
        "harvest_residue_recoverable_m3",
        "harvest_residue_recoverable_odt",
        "harvest_residue_unrecovered_m3",
        "harvest_residue_unrecovered_odt",
        "total_residue_gross_m3",
        "total_residue_gross_odt",
        "total_residue_recoverable_m3",
        "total_residue_recoverable_odt",
        "rsyc_base_recoverable_odt",
        "model_recoverable_odt_base",
    ]
    totals = {
        field: math.fsum(float(row[field]) for row in rows)
        for field in numeric_fields
    }
    totals["province_count"] = float(len(rows))
    totals["measured_province_count"] = float(
        sum(1 for row in rows if row["coefficient_tier"] == measured.MEASURED_TIER)
    )
    totals["donor_province_count"] = float(
        sum(1 for row in rows if row["coefficient_tier"] == measured.DONOR_TIER)
    )
    totals["process_residue_odt_per_m3_feedstock"] = (
        totals["process_residue_odt"] / totals["total_feedstock_m3"]
        if totals["total_feedstock_m3"] > 0.0
        else 0.0
    )
    totals["harvest_residue_gross_odt_per_m3_harvest"] = (
        totals["harvest_residue_gross_odt"] / totals["harvest_m3"]
        if totals["harvest_m3"] > 0.0
        else 0.0
    )
    totals["total_residue_gross_odt_per_m3_harvest"] = (
        totals["total_residue_gross_odt"] / totals["harvest_m3"]
        if totals["harvest_m3"] > 0.0
        else 0.0
    )
    totals["rsyc_ratio"] = (
        totals["model_recoverable_odt_base"] / totals["rsyc_base_recoverable_odt"]
        if totals["rsyc_base_recoverable_odt"] > 0.0
        else 0.0
    )
    return totals


def _coefficient_rows(
    rows: Sequence[Mapping[str, Any]],
    donor_rows: Mapping[str, Mapping[str, str]],
    measured_coefficients: Mapping[str, measured.MeasuredCoefficients],
    splits: Mapping[str, measured.DonorSplits],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in rows:
        province = str(row["province"])
        donor_row = donor_rows[province]
        harvest = float(donor_row["harvest_m3"])
        if harvest <= 0.0:
            continue
        donor_solid = (
            float(donor_row["sawlog_feedstock_m3"])
            + float(donor_row["panel_feedstock_m3"])
        ) / harvest
        donor_pulp = float(donor_row["pulp_feedstock_m3"]) / harvest
        donor_other = float(donor_row["other_log_m3"]) / harvest
        donor_residue = float(donor_row["harvest_residue_gross_odt"]) / harvest
        applied = {
            "solid_share": donor_solid,
            "pulp_share": donor_pulp,
            "other_share": donor_other,
            "harvest_residue_odt_per_m3": donor_residue,
        }
        measured_values: dict[str, float | None] = {
            "solid_share": None,
            "pulp_share": None,
            "other_share": None,
            "harvest_residue_odt_per_m3": None,
        }
        tier = measured.DONOR_TIER
        source = str(row["coefficient_source"])
        if province in measured_coefficients:
            coefficients = measured_coefficients[province]
            measured_values = {
                "solid_share": coefficients.solid_share,
                "pulp_share": coefficients.pulp_share,
                "other_share": coefficients.other_share,
                "harvest_residue_odt_per_m3": (
                    coefficients.harvest_residue_odt_per_m3
                ),
            }
            applied = dict(measured_values)  # type: ignore[arg-type]
            tier = measured.MEASURED_TIER
            source = (
                "four_province_bucking_pilot_v0 expansion-weighted recovered-log "
                "shares; donor within-group splits"
            )
        for name, unit, note in (
            (
                "solid_share",
                "fraction of harvest",
                "SAW+PANEL feedstock; measured sawlog share for four provinces",
            ),
            ("pulp_share", "fraction of harvest", "PULP feedstock"),
            (
                "other_share",
                "fraction of harvest",
                "other-roundwood feedstock; routed to PELLET/BIO",
            ),
            (
                "harvest_residue_odt_per_m3",
                "odt/m3",
                "gross harvest residue per m3 harvest",
            ),
        ):
            output.append(
                {
                    "province": province,
                    "coefficient_name": name,
                    "unit": unit,
                    "donor_value": applied[name]
                    if measured_values[name] is None
                    else donor_solid
                    if name == "solid_share"
                    else donor_pulp
                    if name == "pulp_share"
                    else donor_other
                    if name == "other_share"
                    else donor_residue,
                    "measured_value": measured_values[name],
                    "applied_value": applied[name],
                    "tier": tier,
                    "source": source,
                    "notes": note,
                }
            )
    return output


def _replacement_rows(
    coefficients: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in coefficients:
        if row["tier"] != measured.MEASURED_TIER:
            continue
        donor = float(row["donor_value"])
        value = float(row["measured_value"])
        rows.append(
            {
                "province": row["province"],
                "coefficient_name": row["coefficient_name"],
                "unit": row["unit"],
                "donor_value": donor,
                "measured_value": value,
                "delta": value - donor,
                "ratio_measured_over_donor": (
                    value / donor if donor > 0.0 else 0.0
                ),
                "tier": row["tier"],
                "source": row["source"],
                "notes": row["notes"],
            }
        )
    return rows


def _validation_checks(
    rows: Sequence[Mapping[str, Any]],
    coefficients: Sequence[Mapping[str, Any]],
    measured_coefficients: Mapping[str, measured.MeasuredCoefficients],
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []

    def add(check_id: str, passed: bool, detail: str) -> None:
        checks.append(
            {
                "check_id": check_id,
                "status": "PASS" if passed else "FAIL",
                "detail": detail,
            }
        )
        if not passed:
            raise ProductChainV2Error(f"{check_id}: {detail}")

    measured_rows = [
        row for row in rows if row["coefficient_tier"] == measured.MEASURED_TIER
    ]
    donor_rows = [
        row for row in rows if row["coefficient_tier"] == measured.DONOR_TIER
    ]
    add(
        "measured_province_set",
        {row["province"] for row in measured_rows} == set(measured.MEASURED_PROVINCES),
        f"measured={sorted(row['province'] for row in measured_rows)}",
    )
    add(
        "donor_province_count",
        len(donor_rows) == 9,
        f"donor rows={len(donor_rows)}",
    )

    grade_errors: list[str] = []
    feedstock_errors: list[str] = []
    residue_errors: list[str] = []
    label_errors: list[str] = []
    for row in rows:
        province = str(row["province"])
        if abs(
            float(row["sawlog_m3"])
            + float(row["pulpwood_m3"])
            + float(row["other_log_m3"])
            - float(row["harvest_m3"])
        ) > 1e-6 * max(1.0, float(row["harvest_m3"])):
            grade_errors.append(province)
        if abs(
            float(row["primary_feedstock_m3"])
            + float(row["residue_feedstock_m3"])
            - float(row["total_feedstock_m3"])
        ) > 1e-6 * max(1.0, float(row["total_feedstock_m3"])):
            feedstock_errors.append(province)
        if abs(
            math.fsum(
                float(row[CLASS_FEEDSTOCK_COLUMN[facility_class]])
                for facility_class in CLASS_ORDER
            )
            - float(row["total_feedstock_m3"])
        ) > 1e-6 * max(1.0, float(row["total_feedstock_m3"])):
            feedstock_errors.append(province)
        if abs(
            float(row["process_residue_odt"])
            + float(row["harvest_residue_gross_odt"])
            - float(row["total_residue_gross_odt"])
        ) > 1e-6 * max(1.0, float(row["total_residue_gross_odt"])):
            residue_errors.append(province)
        if (
            str(row["model_estimate"]).lower() != LABEL_TRUE
            or str(row["observed"]).lower() != LABEL_FALSE
            or str(row["policy_grade"]).lower() != LABEL_FALSE
            or str(row["facility_assignment"]).lower() != LABEL_FALSE
        ):
            label_errors.append(province)
    add("grade_conservation", not grade_errors, f"failed: {grade_errors}")
    add("feedstock_conservation", not feedstock_errors, f"failed: {feedstock_errors}")
    add("residue_consistency", not residue_errors, f"failed: {residue_errors}")
    add("labels", not label_errors, f"failed: {label_errors}")

    share_errors: list[str] = []
    for province, coefficients_value in measured_coefficients.items():
        total = (
            coefficients_value.solid_share
            + coefficients_value.pulp_share
            + coefficients_value.other_share
        )
        if abs(total - 1.0) > 1e-9:
            share_errors.append(province)
    add("measured_share_sum", not share_errors, f"failed: {share_errors}")

    replacement_bad = [
        row["province"]
        for row in coefficients
        if row["tier"] == measured.MEASURED_TIER
        and row["measured_value"] is None
    ]
    add("replacement_measured_values", not replacement_bad, f"failed: {replacement_bad}")
    return checks


def _report(summary: Mapping[str, Any]) -> str:
    lines = [
        f"# {ARTIFACT_TITLE}",
        "",
        (
            "Version 2 replaces the product coefficients for BC, QC, NB and YT "
            "with measured bucking-pilot shares. The other nine jurisdictions "
            "keep the v1 donor coefficients. All values are model estimates: "
            "`observed=false`, `policy_grade=false`; no national supply claim."
        ),
        "",
        "## Measured coefficient replacement",
        "",
        "| Province | Coefficient | Donor | Measured | Applied | Ratio |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for row in summary["measured_replacement"]:
        lines.append(
            f"| {row['province']} | {row['coefficient_name']} | "
            f"{float(row['donor_value']):.6f} | "
            f"{float(row['measured_value']):.6f} | "
            f"{float(row['applied_value']) if 'applied_value' in row else float(row['measured_value']):.6f} | "
            f"{float(row['ratio_measured_over_donor']):.3f} |"
        )
    lines += [
        "",
        (
            "Solid is the measured sawlog-grade share and replaces SAW+PANEL "
            "feedstock; the within-solid SAW/PANEL split remains the donor "
            "split. Pulp is the measured pulpwood share. Other is the measured "
            "other-roundwood share and is routed to PELLET/BIO because the "
            "facility chain has no OTHER class. Harvest residue is the measured "
            "gross ODT/m3; the recoverable share remains the donor assumption."
        ),
        "",
        "## Taper coverage and hardwood limits",
        "",
        (
            "| Province | Taper status | Taper jurisdictions | Trees | "
            "Taper-supported share of eligible frame | Proxy species |"
        ),
        "|---|---|---|---:|---:|---|",
    ]
    for row in summary["taper_coverage"]:
        lines.append(
            f"| {row['province']} | {row['taper_status']} | "
            f"{row['taper_jurisdictions']} | {row['n_trees']:,} | "
            f"{float(row['expansion_share_of_eligible_frame']):.3f} | "
            f"{row['proxy_species'] or 'none'} |"
        )
    lines += [
        "",
        (
            "BC and QC use local published tapers (Kozak 2004; Schneider "
            "2013); NB uses the QC donor and YT the BC donor. Species without "
            "a mapped taper, stems outside the taper domain, and unsupported "
            "hardwoods are excluded and not imputed. The measured coefficients "
            "therefore cover only the taper-supported measured frame, not all "
            "provincial species or all hardwood volume; BC, NB and YT proxy "
            "species remain labelled proxies."
        ),
        "",
        "## 119 Mm3 national totals",
        "",
        "| Metric | v2 measured |",
        "|---|---:|",
    ]
    totals = summary["national_totals"]
    for metric, unit in (
        ("harvest_m3", "m3"),
        ("total_feedstock_m3", "m3"),
        ("sawlog_product_m3", "m3"),
        ("pulp_product_odt", "odt"),
        ("panel_product_m3", "m3"),
        ("pellet_product_odt", "odt"),
        ("bio_product_mwh", "MWh"),
        ("process_residue_odt", "odt"),
        ("harvest_residue_gross_odt", "odt"),
        ("harvest_residue_recoverable_odt", "odt"),
        ("total_residue_gross_odt", "odt"),
    ):
        lines.append(f"| {metric} ({unit}) | {float(totals[metric]):,.3f} |")
    lines += [
        "",
        "## Validation",
        "",
        "| Check | Status | Detail |",
        "|---|---|---|",
    ]
    for check in summary["validation"]["checks"]:
        lines.append(
            f"| {check['check_id']} | {check['status']} | {check['detail']} |"
        )
    lines += [
        "",
        "## Caveats",
        "",
        *[f"- {caveat}" for caveat in summary["caveats"]],
        "",
        "Labels: `model_estimate`, `observed=false`, `policy_grade=false`.",
        "",
    ]
    return "\n".join(lines)


def build(output_dir: Path = OUTPUT_DIR) -> dict[str, Any]:
    """Build product-chain v2 from v1 plus measured four-province coefficients."""

    assert_no_unexpected_deletions()
    generated_utc = datetime.now(UTC).replace(microsecond=0).isoformat()
    donor_rows, fields = _read_rows(V1_CSV)
    if not V1_SUMMARY.is_file():
        raise ProductChainV2Error(f"v1 summary is missing: {V1_SUMMARY}")
    v1_summary = json.loads(V1_SUMMARY.read_text(encoding="utf-8"))
    if v1_summary.get("artifact") != "national_product_chain_v1":
        raise ProductChainV2Error(
            f"unexpected v1 artifact: {v1_summary.get('artifact')!r}"
        )

    measured_coefficients = measured.load_measured_coefficients()
    measured.assert_measured_province_set(measured_coefficients)
    pilot_payload = json.loads(measured.PILOT_SUMMARY.read_text(encoding="utf-8"))
    comparison_by_province = {
        row["province"]: row
        for row in pilot_payload["coefficient_comparison"]
    }
    taper_coverage = []
    for province in measured.MEASURED_PROVINCES:
        comparison_row = comparison_by_province[province]
        taper_rows = [
            row
            for row in pilot_payload["taper_coverage"]
            if row["province"] == province
        ]
        proxy_species = sorted(
            {str(row["species"]) for row in taper_rows if row["taper_proxy"]}
        )
        taper_coverage.append(
            {
                "province": province,
                "taper_status": comparison_row["taper_status"],
                "taper_jurisdictions": comparison_row["taper_jurisdictions"],
                "n_trees": int(comparison_row["model_n_trees"]),
                "expansion_share_of_eligible_frame": float(
                    comparison_row[
                        "model_expansion_share_of_eligible_measured_frame"
                    ]
                ),
                "proxy_species": ", ".join(proxy_species),
            }
        )
    donor_splits = measured.load_donor_splits()
    for province in measured.MEASURED_PROVINCES:
        if province not in donor_splits:
            raise ProductChainV2Error(f"donor split missing for {province}")

    by_province = {
        row["province"].strip().upper(): row for row in donor_rows
    }
    if set(by_province) != set(PROVINCES):
        raise ProductChainV2Error(
            f"v1 province set mismatch: {sorted(by_province)}"
        )

    output_rows: list[dict[str, Any]] = []
    for province in PROVINCES:
        donor_row = by_province[province]
        if province in measured_coefficients:
            ratios = _donor_ratios(donor_row)
            output_rows.append(
                _measured_row(
                    donor_row,
                    measured_coefficients[province],
                    donor_splits[province],
                    ratios,
                )
            )
        else:
            output_rows.append(
                _donor_row(
                    donor_row,
                    "national_product_chain_v1 BC aggregate donor; unchanged",
                )
            )

    coefficients = _coefficient_rows(
        output_rows, by_province, measured_coefficients, donor_splits
    )
    replacement = _replacement_rows(coefficients)
    checks = _validation_checks(output_rows, coefficients, measured_coefficients)
    totals = _numeric_totals(output_rows)

    if abs(totals["harvest_m3"] - float(v1_summary["harvest_total_m3"])) > 1e-6:
        raise ProductChainV2Error("harvest total changed from v1")

    rsyc_base = totals["rsyc_base_recoverable_odt"]
    model_recoverable = totals["model_recoverable_odt_base"]
    summary: dict[str, Any] = {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": generated_utc,
        "status": "pass",
        "scope": (
            "Provincial feedstock, product and residue totals from the 119 Mm3 "
            "allocation; measured coefficients for BC/QC/NB/YT; donor "
            "coefficients for the other nine jurisdictions."
        ),
        "model_estimate": True,
        "observed": False,
        "policy_grade": False,
        "facility_assignment": False,
        "ui_or_public_osecan_changed": False,
        "harvest_total_m3": totals["harvest_m3"],
        "province_count": len(output_rows),
        "measured_provinces": list(measured.MEASURED_PROVINCES),
        "donor_provinces": [
            province
            for province in PROVINCES
            if province not in measured.MEASURED_PROVINCES
        ],
        "national_totals": totals,
        "measured_replacement": replacement,
        "taper_coverage": taper_coverage,
        "coefficient_tiers": {
            measured.MEASURED_TIER: sum(
                1 for row in output_rows if row["coefficient_tier"] == measured.MEASURED_TIER
            ),
            measured.DONOR_TIER: sum(
                1 for row in output_rows if row["coefficient_tier"] == measured.DONOR_TIER
            ),
        },
        "method": {
            "product_coefficients": (
                "measured solid/pulp/other shares for four provinces; v1 donor "
                "coefficients for the other nine"
            ),
            "within_group_splits": (
                "donor SAW/PANEL split of solid, donor PELLET/BIO split of "
                "other, donor recoverable share of gross harvest residue"
            ),
            "class_conversion": "v1 donor product and process-residue ratios",
            "grade_status": "measured dimension-only bucking, not official grade",
        },
        "rsyc_comparison": {
            "base_recovery_fraction": RSYC_BASE_RECOVERY,
            "rsyc_base_recoverable_odt": rsyc_base,
            "model_recoverable_odt": model_recoverable,
            "ratio": totals["rsyc_ratio"],
            "status": (
                "model_below_rsyc"
                if totals["rsyc_ratio"] < 0.80
                else "model_above_rsyc"
                if totals["rsyc_ratio"] > 1.25
                else "within_rsyc_range"
            ),
        },
        "validation": {
            "passed": True,
            "failed_check_count": 0,
            "checks": checks,
        },
        "caveats": [
            "All values are model estimates; observed=false and policy_grade=false.",
            (
                "Measured product shares are expansion-weighted recovered-log "
                "shares from the four-province pilot, not mill receipts or a "
                "provincial supply estimate."
            ),
            (
                "Solid is the measured sawlog share and replaces SAW+PANEL "
                "feedstock. The SAW/PANEL split remains a donor split because "
                "the pilot does not measure panel logs separately."
            ),
            (
                "Other roundwood is routed to PELLET/BIO because the facility "
                "chain has no OTHER class. That routing and the pellet/bio split "
                "are donor assumptions, not measured coefficients."
            ),
            (
                "Taper coverage is local for BC and QC, donor for NB (QC "
                "Schneider) and YT (BC Kozak). Unsupported species and "
                "out-of-domain stems are excluded and not imputed; hardwood "
                "coverage is limited by the mapped taper species."
            ),
            (
                "The recoverable share of gross harvest residue and all mill "
                "conversion ratios remain donor assumptions from "
                "national_product_chain_v1."
            ),
            "No UI or public OSeCAN surface is changed by this artifact.",
        ],
        "inputs": {
            str(path.relative_to(REPO_ROOT)): _sha256(path)
            for path in (
                V1_CSV,
                V1_SUMMARY,
                measured.PILOT_SUMMARY,
                measured.PILOT_COMPARISON,
            )
        },
        "reproduce": f".venv/bin/python -m {MODULE_PATH}",
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / "product_chain_by_province.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for row in output_rows:
            writer.writerow(
                {
                    field: (
                        f"{row[field]:.12g}"
                        if isinstance(row.get(field), float)
                        else row.get(field, "")
                    )
                    for field in fields
                }
            )
    with (output_dir / "coefficients.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        coefficient_fields = [
            "province",
            "coefficient_name",
            "unit",
            "donor_value",
            "measured_value",
            "applied_value",
            "tier",
            "source",
            "notes",
        ]
        writer = csv.DictWriter(
            handle, fieldnames=coefficient_fields, lineterminator="\n"
        )
        writer.writeheader()
        for row in coefficients:
            writer.writerow(
                {
                    field: (
                        f"{row[field]:.12g}"
                        if isinstance(row.get(field), float)
                        else "" if row.get(field) is None else row.get(field)
                    )
                    for field in coefficient_fields
                }
            )
    with (output_dir / "measured_coefficient_replacement.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        replacement_fields = [
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
        ]
        writer = csv.DictWriter(
            handle, fieldnames=replacement_fields, lineterminator="\n"
        )
        writer.writeheader()
        for row in replacement:
            writer.writerow(
                {
                    field: (
                        f"{row[field]:.12g}"
                        if isinstance(row.get(field), float)
                        else row.get(field, "")
                    )
                    for field in replacement_fields
                }
            )
    (output_dir / REPORT_NAME).write_text(_report(summary), encoding="utf-8")
    (output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    outputs = {}
    for path in sorted(output_dir.iterdir()):
        if path.is_file() and path.name != "manifest.json":
            outputs[path.name] = {
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
    manifest = {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": generated_utc,
        "labels": {
            "model_estimate": True,
            "observed": False,
            "policy_grade": False,
            "facility_assignment": False,
        },
        "inputs": summary["inputs"],
        "outputs": outputs,
        "validation": summary["validation"],
        "reproduce": summary["reproduce"],
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary


def main() -> None:
    summary = build()
    print(
        json.dumps(
            {
                "artifact": summary["artifact"],
                "status": summary["status"],
                "measured_provinces": summary["measured_provinces"],
                "validation": summary["validation"]["passed"],
                "output_dir": str(OUTPUT_DIR.relative_to(REPO_ROOT)),
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
