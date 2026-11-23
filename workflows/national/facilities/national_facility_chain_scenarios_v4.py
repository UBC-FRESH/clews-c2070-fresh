"""Build the measured-coefficient national facility chain v4.

Version 4 keeps the assignment-v6 / capacity-v3 provincial-floor allocation
from chain v3 and replaces the product chain with
``national_product_chain_v2``.  The measured four-province coefficients change
solid, pulp, other and harvest residue, while the other nine jurisdictions keep
the donor coefficients.

The artifact adds an explicit donor-versus-measured comparison at the national
and provincial levels.  All values remain ``model_estimate`` with
``observed=false`` and ``policy_grade=false``.  No national supply, policy, or
operating claim is made.
"""

from __future__ import annotations

import csv
import json
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from workflows.national.facilities import (
    national_facility_chain_scenarios as _base,
)
from workflows.national.facilities import (
    national_facility_chain_scenarios_v3 as _v3,
)

REPO_ROOT = _base.REPO_ROOT
DATA_ROOT = _base.DATA_ROOT
OUTPUT_DIR = (
    DATA_ROOT
    / "derived/national/facilities/national_facility_chain_scenarios_v4"
)
DONOR_DIR = _v3.OUTPUT_DIR
PRODUCT_CHAIN_DIR = (
    DATA_ROOT / "derived/national/facilities/national_product_chain_v2"
)
PRODUCT_CHAIN_CSV = PRODUCT_CHAIN_DIR / "product_chain_by_province.csv"
PRODUCT_CHAIN_SUMMARY = PRODUCT_CHAIN_DIR / "summary.json"
MEASURED_REPLACEMENT = PRODUCT_CHAIN_DIR / "measured_coefficient_replacement.csv"

ARTIFACT = "national_facility_chain_scenarios_v4"
ARTIFACT_VERSION = "4.0.0"
ARTIFACT_TITLE = "National Facility Chain Scenarios V4 (Measured Coefficients)"
REPORT_NAME = "NATIONAL_FACILITY_CHAIN_SCENARIOS_V4.md"
CAPACITY_LABEL = _v3.CAPACITY_LABEL
ASSIGNMENT_LABEL = _v3.ASSIGNMENT_LABEL
MODULE_PATH = "workflows.national.facilities.national_facility_chain_scenarios_v4"
TEST_PATH = "tests/test_national_facility_chain_scenarios_v4.py"
CAPACITY_CASE = _v3.CAPACITY_CASE
SCENARIO_ORDER = _base.SCENARIO_ORDER
PRIMARY_SCENARIO = _base.PRIMARY_SCENARIO
ScenarioChainV4Error = _base.ScenarioChainError

PRODUCT_METRICS: tuple[tuple[str, str], ...] = (
    ("solid_product_m3", "m3"),
    ("sawlog_product_m3", "m3"),
    ("panel_product_m3", "m3"),
    ("pulp_product_odt", "odt"),
    ("pellet_product_odt", "odt"),
    ("bio_product_mwh", "MWh"),
    ("process_residue_pj", "PJ"),
    ("harvest_residue_gross_pj", "PJ"),
    ("harvest_residue_recoverable_pj", "PJ"),
    ("total_residue_gross_pj", "PJ"),
    ("total_residue_recoverable_pj", "PJ"),
)
CLASS_PRODUCT_FIELD = {
    "SAW": "sawlog_product_m3",
    "PULP": "pulp_product_odt",
    "PANEL": "panel_product_m3",
    "PELLET": "pellet_product_odt",
    "BIO": "bio_product_mwh",
}
COMPARISON_FIELDS = [
    "scenario",
    "scope",
    "province",
    "metric",
    "unit",
    "donor_value",
    "measured_value",
    "delta",
    "delta_pct",
    "evidence_label",
    "observed",
    "policy_grade",
]

_V3_OVERRIDES: dict[str, Any] = {
    "OUTPUT_DIR": OUTPUT_DIR,
    "ARTIFACT": ARTIFACT,
    "ARTIFACT_VERSION": ARTIFACT_VERSION,
    "ARTIFACT_TITLE": ARTIFACT_TITLE,
    "REPORT_NAME": REPORT_NAME,
    "CAPACITY_LABEL": CAPACITY_LABEL,
    "ASSIGNMENT_LABEL": ASSIGNMENT_LABEL,
    "MODULE_PATH": MODULE_PATH,
    "TEST_PATH": TEST_PATH,
    "CAPACITY_CASE": CAPACITY_CASE,
}
_BASE_OVERRIDES: dict[str, Any] = {
    "PRODUCT_CHAIN_CSV": PRODUCT_CHAIN_CSV,
    "PRODUCT_CHAIN_SUMMARY": PRODUCT_CHAIN_SUMMARY,
    "PRODUCT_CHAIN_ARTIFACT": "national_product_chain_v2",
    "PRODUCT_CHAIN_DIR": PRODUCT_CHAIN_DIR,
    "ARTIFACT": ARTIFACT,
    "ARTIFACT_VERSION": ARTIFACT_VERSION,
    "ARTIFACT_TITLE": ARTIFACT_TITLE,
    "REPORT_NAME": REPORT_NAME,
    "CAPACITY_LABEL": CAPACITY_LABEL,
    "ASSIGNMENT_LABEL": ASSIGNMENT_LABEL,
    "CAPACITY_CASE": CAPACITY_CASE,
    "MODULE_PATH": MODULE_PATH,
    "TEST_PATH": TEST_PATH,
}


def _read_rows(path: Path, label: str) -> list[dict[str, str]]:
    if not path.is_file():
        raise ScenarioChainV4Error(f"{label} is missing: {path}")
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ScenarioChainV4Error(f"{label} is empty: {path}")
    return rows


def _finite(value: Any, label: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as error:
        raise ScenarioChainV4Error(f"{label} is not numeric: {value!r}") from error
    if not math.isfinite(parsed):
        raise ScenarioChainV4Error(f"{label} is not finite: {parsed!r}")
    return parsed


def _metric_values(
    product_rows: Sequence[Mapping[str, str]],
    scenario: str,
    province: str | None,
) -> dict[str, float]:
    selected = [
        row
        for row in product_rows
        if row["scenario"] == scenario
        and (province is None or row["province"] == province)
    ]
    if not selected:
        raise ScenarioChainV4Error(
            f"no product/residue rows for {scenario}/{province or 'national'}"
        )
    class_product = {facility_class: 0.0 for facility_class in CLASS_PRODUCT_FIELD}
    process_pj = 0.0
    harvest_gross_pj = 0.0
    harvest_recoverable_pj = 0.0
    for row in selected:
        if row["record_type"] == "facility_process":
            facility_class = row["facility_class"].strip()
            if facility_class not in CLASS_PRODUCT_FIELD:
                raise ScenarioChainV4Error(
                    f"unexpected facility class {facility_class!r}"
                )
            class_product[facility_class] += _finite(
                row["product_quantity"],
                f"{scenario}/{row['province']}/{facility_class} product",
            )
            process_pj += _finite(
                row["process_residue_pj"],
                f"{scenario}/{row['province']}/{facility_class} process PJ",
            )
        elif row["record_type"] == "harvest_residue":
            harvest_gross_pj += _finite(
                row["harvest_residue_gross_pj"],
                f"{scenario}/{row['province']} harvest gross PJ",
            )
            harvest_recoverable_pj += _finite(
                row["harvest_residue_recoverable_pj"],
                f"{scenario}/{row['province']} harvest recoverable PJ",
            )
        else:
            raise ScenarioChainV4Error(
                f"unexpected record type {row['record_type']!r}"
            )
    values = {
        CLASS_PRODUCT_FIELD[facility_class]: value
        for facility_class, value in class_product.items()
    }
    values["solid_product_m3"] = (
        values["sawlog_product_m3"] + values["panel_product_m3"]
    )
    values["process_residue_pj"] = process_pj
    values["harvest_residue_gross_pj"] = harvest_gross_pj
    values["harvest_residue_recoverable_pj"] = harvest_recoverable_pj
    values["total_residue_gross_pj"] = process_pj + harvest_gross_pj
    values["total_residue_recoverable_pj"] = (
        process_pj + harvest_recoverable_pj
    )
    return values


def _comparison_rows(
    donor_rows: Sequence[Mapping[str, str]],
    measured_rows: Sequence[Mapping[str, str]],
    provinces: Sequence[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for scenario in SCENARIO_ORDER:
        for scope, province in (
            ("national", None),
            *[("province", province) for province in provinces],
        ):
            donor = _metric_values(donor_rows, scenario, province)
            measured = _metric_values(measured_rows, scenario, province)
            for metric, unit in PRODUCT_METRICS:
                donor_value = donor[metric]
                measured_value = measured[metric]
                delta = measured_value - donor_value
                rows.append(
                    {
                        "scenario": scenario,
                        "scope": scope,
                        "province": province or "ALL",
                        "metric": metric,
                        "unit": unit,
                        "donor_value": donor_value,
                        "measured_value": measured_value,
                        "delta": delta,
                        "delta_pct": (
                            delta / donor_value * 100.0
                            if donor_value > 0.0
                            else 0.0
                        ),
                        "evidence_label": "model_estimate",
                        "observed": "false",
                        "policy_grade": "false",
                    }
                )
    return rows


def _replacement_rows() -> list[dict[str, Any]]:
    return [
        {
            key: value
            for key, value in row.items()
            if key
            in {
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
            }
        }
        for row in _read_rows(MEASURED_REPLACEMENT, "measured coefficient replacement")
    ]


def _coefficient_split(
    replacement: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    by_province: dict[str, dict[str, float]] = {}
    for row in replacement:
        province = str(row["province"])
        by_province.setdefault(province, {})[str(row["coefficient_name"])] = float(
            row["measured_value"]
        )
    if set(by_province) != set(_base.PROVINCES).intersection(
        {"BC", "QC", "NB", "YT"}
    ):
        raise ScenarioChainV4Error(
            f"unexpected measured province set: {sorted(by_province)}"
        )
    return {
        "measured_provinces": sorted(by_province),
        "donor_provinces": [
            province
            for province in _base.PROVINCES
            if province not in by_province
        ],
        "measured_coefficients": by_province,
        "replacement": list(replacement),
    }


def _fmt(value: float, digits: int = 3) -> str:
    return f"{value:,.{digits}f}"


def _append_report(
    report_path: Path,
    comparison: Sequence[Mapping[str, Any]],
    replacement: Sequence[Mapping[str, Any]],
) -> None:
    lines = [
        "",
        "## Measured versus donor coefficients",
        "",
        (
            "BC, QC, NB and YT use measured bucking-pilot solid/pulp/other and "
            "harvest-residue coefficients. The other nine jurisdictions keep "
            "the donor coefficients. Differences below are coefficient effects "
            "only; the assignment and harvest are identical."
        ),
        "",
        "### Coefficient replacement",
        "",
        "| Province | Coefficient | Donor | Measured | Delta | Ratio |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for row in replacement:
        lines.append(
            f"| {row['province']} | {row['coefficient_name']} | "
            f"{float(row['donor_value']):.6f} | "
            f"{float(row['measured_value']):.6f} | "
            f"{float(row['delta']):+.6f} | "
            f"{float(row['ratio_measured_over_donor']):.3f} |"
        )
    lines += [
        "",
        "### National 119m products and residue",
        "",
        "| Metric | Donor | Measured | Delta | Delta % |",
        "|---|---:|---:|---:|---:|",
    ]
    selected = [
        row
        for row in comparison
        if row["scenario"] == PRIMARY_SCENARIO
        and row["scope"] == "national"
    ]
    for row in selected:
        lines.append(
            f"| {row['metric']} ({row['unit']}) | "
            f"{_fmt(float(row['donor_value']))} | "
            f"{_fmt(float(row['measured_value']))} | "
            f"{_fmt(float(row['delta']))} | "
            f"{float(row['delta_pct']):+.2f}% |"
        )
    lines += [
        "",
        "### Provincial 119m process and harvest residue",
        "",
        (
            "| Province | Donor process PJ | Measured process PJ | Delta | "
            "Donor harvest gross PJ | Measured harvest gross PJ | Delta |"
        ),
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    province_rows = [
        row
        for row in comparison
        if row["scenario"] == PRIMARY_SCENARIO and row["scope"] == "province"
    ]
    by_province: dict[str, dict[str, Mapping[str, Any]]] = {}
    for row in province_rows:
        by_province.setdefault(str(row["province"]), {})[str(row["metric"])] = row
    for province in _base.PROVINCES:
        metrics = by_province.get(province, {})
        process = metrics.get("process_residue_pj")
        harvest = metrics.get("harvest_residue_gross_pj")
        if process is None or harvest is None:
            continue
        lines.append(
            f"| {province} | {_fmt(float(process['donor_value']))} | "
            f"{_fmt(float(process['measured_value']))} | "
            f"{_fmt(float(process['delta']))} | "
            f"{_fmt(float(harvest['donor_value']))} | "
            f"{_fmt(float(harvest['measured_value']))} | "
            f"{_fmt(float(harvest['delta']))} |"
        )
    lines += [
        "",
        (
            "Process residue is facility-assigned; harvest residue is "
            "province-only. They are never combined into a single OSeCAN row."
        ),
        "",
    ]
    report_path.write_text(
        report_path.read_text(encoding="utf-8") + "\n".join(lines),
        encoding="utf-8",
    )


def build(output_dir: Path = OUTPUT_DIR) -> dict[str, Any]:
    """Build chain v4 with measured coefficients and a donor comparison."""

    saved_v3 = {name: getattr(_v3, name) for name in _V3_OVERRIDES}
    saved_base = {
        name: getattr(_base, name)
        for name in set(_v3.BASE_OVERRIDES) | set(_BASE_OVERRIDES)
    }
    try:
        for name, value in _V3_OVERRIDES.items():
            setattr(_v3, name, value)
        for name, value in _v3.BASE_OVERRIDES.items():
            setattr(_base, name, value)
        for name, value in _BASE_OVERRIDES.items():
            setattr(_base, name, value)
        summary = _v3._build(output_dir=output_dir)
    finally:
        for name, value in saved_v3.items():
            setattr(_v3, name, value)
        for name, value in saved_base.items():
            setattr(_base, name, value)

    donor_rows = _read_rows(
        DONOR_DIR / "scenario_product_residue.csv", "donor product/residue"
    )
    measured_rows = _read_rows(
        output_dir / "scenario_product_residue.csv", "measured product/residue"
    )
    comparison = _comparison_rows(
        donor_rows, measured_rows, tuple(_base.PROVINCES)
    )
    replacement = _replacement_rows()
    coefficient_split = _coefficient_split(replacement)

    summary["artifact"] = ARTIFACT
    summary["artifact_version"] = ARTIFACT_VERSION
    summary["product_chain"] = {
        "artifact": "national_product_chain_v2",
        "reference_harvest_m3": 118_977_339.1,
        "conversion": (
            "measured four-province solid/pulp/other and harvest-residue "
            "coefficients with donor within-group splits and class ratios; "
            "v1 donor coefficients for the other nine jurisdictions"
        ),
        "measured_provinces": coefficient_split["measured_provinces"],
        "donor_provinces": coefficient_split["donor_provinces"],
        "harvest_residue_scope": "province_only",
        "process_residue_scope": "facility_assigned",
    }
    summary["coefficient_replacement"] = coefficient_split
    summary["measured_vs_donor"] = {
        "primary_scenario": PRIMARY_SCENARIO,
        "metrics": [metric for metric, _ in PRODUCT_METRICS],
        "national_119m": {
            row["metric"]: {
                "unit": row["unit"],
                "donor_value": float(row["donor_value"]),
                "measured_value": float(row["measured_value"]),
                "delta": float(row["delta"]),
                "delta_pct": float(row["delta_pct"]),
            }
            for row in comparison
            if row["scenario"] == PRIMARY_SCENARIO
            and row["scope"] == "national"
        },
        "comparison_csv": "measured_vs_donor_comparison.csv",
    }
    summary["caveats"] = [
        caveat.replace(
            "product-chain-v1 province/class coefficients",
            "measured four-province product-chain-v2 coefficients",
        )
        for caveat in summary["caveats"]
    ]
    summary["method"]["product_residue"] = (
        "measured four-province product-chain-v2 coefficients applied to "
        "assigned class feedstock; donor coefficients for the other nine"
    )
    summary["caveats"] = list(summary["caveats"]) + [
        (
            "BC, QC, NB and YT use measured bucking-pilot product shares; the "
            "other nine jurisdictions keep donor coefficients. This is a "
            "four-province pilot, not a national measured coefficient set."
        ),
        (
            "Measured solid is the sawlog-grade share and replaces SAW+PANEL "
            "feedstock. Panel split, pellet/bio split, residue recovery, and "
            "class conversion ratios remain donor assumptions."
        ),
        (
            "Taper coverage is local for BC and QC, donor for NB and YT. "
            "Unsupported species and out-of-domain stems are excluded, so the "
            "measured coefficients do not represent full provincial species "
            "coverage; hardwood limitations remain."
        ),
    ]
    summary["inputs"]["data/derived/national/facilities/national_product_chain_v2/product_chain_by_province.csv"] = _base._sha256(
        PRODUCT_CHAIN_CSV
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / "measured_vs_donor_comparison.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.DictWriter(
            handle, fieldnames=COMPARISON_FIELDS, lineterminator="\n"
        )
        writer.writeheader()
        for row in comparison:
            writer.writerow(
                {
                    field: (
                        f"{row[field]:.12g}"
                        if isinstance(row.get(field), float)
                        else row.get(field, "")
                    )
                    for field in COMPARISON_FIELDS
                }
            )
    report_path = output_dir / REPORT_NAME
    report_text = report_path.read_text(encoding="utf-8").replace(
        "product-chain-v1 province/class coefficients",
        "measured four-province product-chain-v2 coefficients",
    )
    report_path.write_text(report_text, encoding="utf-8")
    _append_report(
        report_path,
        comparison,
        replacement,
    )
    _base.v2._write_json(output_dir / "summary.json", summary)
    _base._write_manifest(output_dir, summary)
    return summary


def main() -> None:
    summary = build()
    print(
        json.dumps(
            {
                "artifact": summary["artifact"],
                "status": summary["status"],
                "validation": summary["validation"]["passed"],
                "measured_provinces": summary["coefficient_replacement"][
                    "measured_provinces"
                ],
                "output_dir": str(OUTPUT_DIR.relative_to(REPO_ROOT)),
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
