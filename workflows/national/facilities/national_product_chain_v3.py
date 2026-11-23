"""Build the measured-coefficient product chain v3 with total residue.

Version 3 preserves every v2 product and feedstock quantity exactly.  It changes
only the harvest-residue dry mass for the four measured provinces: the v2 stem
taper residue is replaced by measured stem + branch + foliage gross residue from
the four-province allometry, and recoverable values are carried for the
0.35/0.60/0.75 scenarios.  The 0.60 value is a reference scenario, not a
calibrated recovery factor.

The province m3 routing fields remain stem-residue m3 because branch and
foliage have no measured volume.  Products, process residue, and class
feedstock are therefore unchanged; only harvest residue ODT and the total
residue ODT change.  This is a model estimate: ``observed=false`` and
``policy_grade=false``; no national supply claim is made.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from workflows.national.facilities import measured_bucking_coefficients as measured
from workflows.national.facilities import national_product_chain_v2 as _v2

REPO_ROOT = _v2.REPO_ROOT
DATA_ROOT = _v2.DATA_ROOT
V2_DIR = _v2.OUTPUT_DIR
V2_CSV = V2_DIR / "product_chain_by_province.csv"
OUTPUT_DIR = DATA_ROOT / "derived/national/facilities/national_product_chain_v3"
ARTIFACT = "national_product_chain_v3"
ARTIFACT_VERSION = "3.0.0"
ARTIFACT_TITLE = "National Product Chain V3 (Measured Total Residue)"
REPORT_NAME = "NATIONAL_PRODUCT_CHAIN_V3.md"
MODULE_PATH = "workflows.national.facilities.national_product_chain_v3"
TEST_PATH = "tests/test_national_product_chain_v3.py"
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


class ProductChainV3Error(_v2.ProductChainV2Error):
    """Raised when total residue cannot be reconciled to v2 products."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_csv(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    if not path.is_file():
        raise ProductChainV3Error(f"missing product-chain input: {path}")
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        fields = list(reader.fieldnames or [])
    if not rows or not fields:
        raise ProductChainV3Error(f"empty product-chain input: {path}")
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
        raise ProductChainV3Error(f"{label} is not numeric: {value!r}") from error
    if not math.isfinite(parsed) or parsed < 0.0:
        raise ProductChainV3Error(f"{label} is not finite and nonnegative: {parsed!r}")
    return parsed


def _require_close(left: float, right: float, label: str) -> None:
    if abs(left - right) > 1e-8 * max(abs(left), abs(right), 1.0):
        raise ProductChainV3Error(f"{label} mismatch: {left!r} != {right!r}")


def _baseline_products() -> dict[str, dict[str, float]]:
    rows, fields = _read_csv(V2_CSV)
    available = set(fields)
    missing = sorted(set(PRODUCT_FIELDS) - available)
    if missing:
        raise ProductChainV3Error(
            f"v2 product chain is missing fields: {', '.join(missing)}"
        )
    return {
        str(row["province"]): {
            field: _finite(row[field], f"{row['province']}/{field}")
            for field in PRODUCT_FIELDS
        }
        for row in rows
    }


def _update_measured_row(
    row: dict[str, Any],
    coefficients: measured.MeasuredCoefficients,
) -> None:
    province = coefficients.province
    harvest = _finite(row["harvest_m3"], f"{province} harvest")
    if harvest <= 0.0:
        raise ProductChainV3Error(f"{province}: positive harvest is required")
    stem = harvest * coefficients.harvest_residue_odt_per_m3
    branch = harvest * coefficients.branch_foliage_odt_per_m3
    total = harvest * coefficients.harvest_residue_total_odt_per_m3
    _require_close(total, stem + branch, f"{province} stem plus branch/foliage")
    process = _finite(row["process_residue_odt"], f"{province} process residue")
    recoverable = {
        0.35: harvest * coefficients.recoverable_odt_per_m3_r035,
        0.60: harvest * coefficients.recoverable_odt_per_m3_r060,
        0.75: harvest * coefficients.recoverable_odt_per_m3_r075,
    }
    for factor, value in recoverable.items():
        _require_close(value, total * factor, f"{province} recoverable at {factor}")
    row["harvest_residue_stem_gross_odt"] = stem
    row["harvest_residue_branch_foliage_odt"] = branch
    row["harvest_residue_total_gross_odt"] = total
    row["harvest_residue_recoverable_total_odt_r035"] = recoverable[0.35]
    row["harvest_residue_recoverable_total_odt_r060"] = recoverable[0.60]
    row["harvest_residue_recoverable_total_odt_r075"] = recoverable[0.75]
    row["total_residue_gross_total_odt"] = process + total
    for factor in SCENARIOS:
        suffix = f"r{round(factor * 100):03d}"
        row[f"total_residue_recoverable_total_odt_{suffix}"] = (
            process + recoverable[factor]
        )
    row["stem_harvest_residue_gross_odt_per_m3_harvest"] = stem / harvest
    row["harvest_residue_gross_odt"] = total
    row["harvest_residue_recoverable_odt"] = recoverable[REFERENCE_RECOVERY]
    row["harvest_residue_unrecovered_odt"] = (
        total - recoverable[REFERENCE_RECOVERY]
    )
    row["harvest_residue_gross_odt_per_m3_harvest"] = total / harvest
    row["total_residue_gross_odt"] = process + total
    row["total_residue_recoverable_odt"] = (
        process + recoverable[REFERENCE_RECOVERY]
    )
    row["total_residue_gross_odt_per_m3_harvest"] = (
        process + total
    ) / harvest
    row["harvest_residue_component_scope"] = "stem_plus_branch_foliage"
    row["harvest_residue_recovery_basis"] = (
        "0.35|0.60|0.75 scenarios; 0.60 reference, not calibrated"
    )
    row["model_recoverable_odt_base"] = recoverable[REFERENCE_RECOVERY]
    donor_base = _finite(
        row["rsyc_base_recoverable_odt"], f"{province} RSYC base"
    )
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


def _update_donor_row(row: dict[str, Any]) -> None:
    stem = _finite(row["harvest_residue_gross_odt"], "donor stem residue")
    process = _finite(row["process_residue_odt"], "donor process residue")
    row["harvest_residue_stem_gross_odt"] = stem
    row["harvest_residue_branch_foliage_odt"] = 0.0
    row["harvest_residue_total_gross_odt"] = stem
    for factor in SCENARIOS:
        suffix = f"r{round(factor * 100):03d}"
        row[f"harvest_residue_recoverable_total_odt_{suffix}"] = stem * factor
        row[f"total_residue_recoverable_total_odt_{suffix}"] = (
            process + stem * factor
        )
    row["total_residue_gross_total_odt"] = process + stem
    row["stem_harvest_residue_gross_odt_per_m3_harvest"] = (
        stem / float(row["harvest_m3"])
        if float(row["harvest_m3"]) > 0.0
        else 0.0
    )
    row["harvest_residue_component_scope"] = "stem_only_donor"
    row["harvest_residue_recovery_basis"] = (
        "donor stem residue; scenario factors applied only for comparison"
    )


def _scenario_rows(
    rows: Sequence[Mapping[str, Any]],
    coefficients: Mapping[str, measured.MeasuredCoefficients],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        province = str(row["province"])
        coefficient = coefficients.get(province)
        if coefficient is None:
            continue
        result.append(
            {
                "province": province,
                "stem_residue_odt_per_m3": coefficient.harvest_residue_odt_per_m3,
                "branch_foliage_odt_per_m3": coefficient.branch_foliage_odt_per_m3,
                "total_residue_gross_odt_per_m3": (
                    coefficient.harvest_residue_total_odt_per_m3
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
    if {row["province"] for row in result} != set(measured.MEASURED_PROVINCES):
        raise ProductChainV3Error("residue scenario rows are not BC/QC/NB/YT")
    return result


def _append_report(
    path: Path,
    baseline: Mapping[str, Mapping[str, float]],
    rows: Sequence[Mapping[str, Any]],
    scenarios: Sequence[Mapping[str, Any]],
) -> None:
    lines = [
        "",
        "## Stem-only versus total harvest residue",
        "",
        (
            "Products and process residue are unchanged from product-chain v2. "
            "Only harvest-residue dry mass changes: measured stem residue plus "
            "live branch and foliage biomass. The 0.60 column is a reference "
            "scenario, not a calibrated recovery factor."
        ),
        "",
        ("| Province | Stem ODT/m3 | Branch+foliage ODT/m3 | Total gross ODT/m3 | "
        "Recoverable 0.35 | Recoverable 0.60 | Recoverable 0.75 | "
        "Product delta |"),
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in scenarios:
        province = str(row["province"])
        match = next(item for item in rows if item["province"] == province)
        product_delta = max(
            abs(float(match[field]) - baseline[province][field])
            for field in PRODUCT_FIELDS
        )
        lines.append(
            f"| {province} | {float(row['stem_residue_odt_per_m3']):.6f} | "
            f"{float(row['branch_foliage_odt_per_m3']):.6f} | "
            f"{float(row['total_residue_gross_odt_per_m3']):.6f} | "
            f"{float(row['recoverable_odt_per_m3_r035']):.6f} | "
            f"{float(row['recoverable_odt_per_m3_r060']):.6f} | "
            f"{float(row['recoverable_odt_per_m3_r075']):.6f} | "
            f"{product_delta:.3e} |"
        )
    lines += [
        "",
        (
            "Branch and foliage have no measured volume, so the m3 routing "
            "fields remain the stem taper residue. The added dry mass is "
            "carried for energy and residue accounting, not for extra mill "
            "feedstock."
        ),
        "",
        ("Labels: `model_estimate=true`, `observed=false`, `policy_grade=false`, "
        "`national_claim=false`."),
        "",
    ]
    path.write_text(
        path.read_text(encoding="utf-8") + "\n".join(lines), encoding="utf-8"
    )


def build(output_dir: Path = OUTPUT_DIR) -> dict[str, Any]:
    """Build product chain v3 by preserving v2 products and adding total residue."""

    _v2.assert_no_unexpected_deletions()
    generated_utc = datetime.now(UTC).replace(microsecond=0).isoformat()
    baseline = _baseline_products()
    output = Path(output_dir)
    summary = _v2.build(output_dir=output)
    stale_report = output / _v2.REPORT_NAME
    if stale_report.name != REPORT_NAME and stale_report.is_file():
        stale_report.unlink()

    rows, fields = _read_csv(output / "product_chain_by_province.csv")
    coefficients = measured.load_measured_coefficients()
    updated: list[dict[str, Any]] = []
    for row in rows:
        province = str(row["province"]).strip().upper()
        row = dict(row)
        coefficient = coefficients.get(province)
        if coefficient is not None:
            _update_measured_row(row, coefficient)
        else:
            _update_donor_row(row)
        _require_close(
            float(row["harvest_residue_recoverable_odt"])
            + float(row["harvest_residue_unrecovered_odt"]),
            float(row["harvest_residue_gross_odt"]),
            f"{province} residue ODT split",
        )
        _require_close(
            float(row["process_residue_odt"])
            + float(row["harvest_residue_gross_odt"]),
            float(row["total_residue_gross_odt"]),
            f"{province} total residue gross",
        )
        for field in PRODUCT_FIELDS:
            _require_close(
                float(row[field]),
                baseline[province][field],
                f"{province}/{field} unchanged product",
            )
        updated.append(row)

    new_fields = list(fields)
    new_fields.extend(field for field in COMPONENT_FIELDS if field not in new_fields)
    _write_rows(output / "product_chain_by_province.csv", updated, new_fields)

    scenarios = _scenario_rows(updated, coefficients)
    _write_rows(
        output / "residue_scenario_coefficients.csv",
        scenarios,
        SCENARIO_FIELDS,
    )

    totals = _v2._numeric_totals(updated)
    summary["artifact"] = ARTIFACT
    summary["artifact_version"] = ARTIFACT_VERSION
    summary["generated_utc"] = generated_utc
    summary["status"] = "pass"
    summary["scope"] = (
        "Provincial product, feedstock and total harvest-residue totals from "
        "the 119 Mm3 allocation; measured total residue (stem + branch + "
        "foliage) for BC/QC/NB/YT and donor stem residue for the other nine."
    )
    summary["national_totals"] = totals
    summary["residue_components"] = {
        "scope": "measured_total_residue",
        "measured_provinces": list(measured.MEASURED_PROVINCES),
        "stem_plus_branch_foliage": True,
        "recovery_scenarios": list(SCENARIOS),
        "reference_recovery_factor": REFERENCE_RECOVERY,
        "calibrated_recovery_factor": False,
        "province_rows": scenarios,
        "stem_only_baseline_artifact": "national_product_chain_v2",
    }
    summary["stem_vs_total_residue"] = {
        "products_unchanged": True,
        "measured_rows": [
            {
                "province": str(row["province"]),
                "stem_gross_odt": _finite(
                    row["harvest_residue_stem_gross_odt"],
                    "stem gross",
                ),
                "total_gross_odt": _finite(
                    row["harvest_residue_total_gross_odt"],
                    "total gross",
                ),
                "delta_odt": _finite(row["harvest_residue_total_gross_odt"], "t")
                - _finite(row["harvest_residue_stem_gross_odt"], "s"),
                "ratio_total_over_stem": (
                    _finite(row["harvest_residue_total_gross_odt"], "t")
                    / _finite(row["harvest_residue_stem_gross_odt"], "s")
                ),
                "recoverable_odt_r035": _finite(
                    row["harvest_residue_recoverable_total_odt_r035"], "r035"
                ),
                "recoverable_odt_r060": _finite(
                    row["harvest_residue_recoverable_total_odt_r060"], "r060"
                ),
                "recoverable_odt_r075": _finite(
                    row["harvest_residue_recoverable_total_odt_r075"], "r075"
                ),
            }
            for row in updated
            if row["coefficient_tier"] == measured.MEASURED_TIER
        ],
    }
    summary["method"]["harvest_residue"] = (
        "measured stem + branch + foliage gross residue for BC/QC/NB/YT; "
        "0.35/0.60/0.75 scenarios with 0.60 reference and no calibrated factor"
    )
    summary["rsyc_comparison"]["model_recoverable_odt"] = totals[
        "model_recoverable_odt_base"
    ]
    summary["rsyc_comparison"]["ratio"] = totals["rsyc_ratio"]
    summary["rsyc_comparison"]["status"] = (
        "model_below_rsyc"
        if totals["rsyc_ratio"] < 0.80
        else "model_above_rsyc"
        if totals["rsyc_ratio"] > 1.25
        else "within_rsyc_range"
    )
    summary["validation"]["checks"] = list(summary["validation"]["checks"]) + [
        {
            "check_id": "products_unchanged_from_v2",
            "status": "PASS",
            "detail": "all product and process fields equal v2",
        },
        {
            "check_id": "total_residue_conservation",
            "status": "PASS",
            "detail": "stem + branch/foliage = total; scenarios = gross * factor",
        },
        {
            "check_id": "no_calibrated_recovery_factor",
            "status": "PASS",
            "detail": "0.35/0.60/0.75 carried; 0.60 is reference only",
        },
    ]
    summary["caveats"] = list(summary["caveats"]) + [
        (
            "Branch and foliage are model-estimated dry mass from published "
            "CFS equations, not measured component weights. They are never "
            "added to stem taper volume."
        ),
        (
            "The 0.60 recovery value is the existing reference scenario; it is "
            "not a calibrated or policy recovery factor. 0.35 and 0.75 are "
            "carried with equal status."
        ),
        (
            "m3 feedstock routing remains stem-residue m3 because branch and "
            "foliage volume was not measured. Products and process residue are "
            "therefore unchanged from product-chain v2."
        ),
    ]
    summary["labels"] = {
        "model_estimate": True,
        "observed": False,
        "policy_grade": False,
        "national_claim": False,
    }
    summary["inputs"] = {
        **summary.get("inputs", {}),
        str((output / "residue_scenario_coefficients.csv").relative_to(REPO_ROOT)): (
            _sha256(output / "residue_scenario_coefficients.csv")
        ),
    }
    summary["reproduce"] = f".venv/bin/python -m {MODULE_PATH}"

    report_path = output / REPORT_NAME
    v2_report = _v2._report(summary)
    report_path.write_text(
        v2_report.replace(
            "Version 2 replaces the product coefficients",
            "Version 3 preserves every v2 product and replaces stem harvest residue with measured total residue",
        ),
        encoding="utf-8",
    )
    _append_report(report_path, baseline, updated, scenarios)

    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    outputs = {}
    for path in sorted(output.iterdir()):
        if path.is_file() and path.name != "manifest.json":
            outputs[path.name] = {
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
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
                "validation": summary["validation"]["passed"],
                "product_chain": str(OUTPUT_DIR.relative_to(REPO_ROOT)),
                "residue_scenarios": list(SCENARIOS),
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
