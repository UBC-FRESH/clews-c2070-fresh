"""National residue supply-curve sensitivity v9 across the 100/119/150 cases.

Version 9 keeps the frozen v8 119 Mm3 curve files byte-identical and adds a
case-level curve for the 100 and 150 Mm3 assignment-v10 facility residue
distribution.  Process residue is aggregated by facility province; harvest
residue remains origin-province only.  The 119 Mm3 case-level rows reproduce
the frozen v8 curve exactly.

All values remain ``model_estimate`` with ``observed=false`` and
``policy_grade=false``.  Transport cost is not part of this curve.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

from workflows.national.facilities import (
    national_residue_curve_v8 as _v8,
)

REPO_ROOT = _v8.REPO_ROOT
DATA_ROOT = _v8.DATA_ROOT
FACILITY_DIR = (
    DATA_ROOT / "derived/national/facilities/facility_residue_supply_v9"
)
CHAIN_DIR = (
    DATA_ROOT / "derived/national/facilities/national_facility_chain_scenarios_v9"
)
V8_CURVE_DIR = (
    DATA_ROOT / "derived/national/facilities/national_residue_curve_v8"
)
OUTPUT_DIR = DATA_ROOT / "derived/national/facilities/national_residue_curve_v9"
ARTIFACT = "national_residue_curve_v9"
ARTIFACT_VERSION = "9.0.0"
ARTIFACT_TITLE = "National Residue Supply-Curve Sensitivity v9 (all cases)"
REPORT_NAME = "NATIONAL_RESIDUE_CURVE_V9.md"
MODULE_PATH = "workflows.national.facilities.national_residue_curve_v9"
TEST_PATH = "tests/test_national_facility_chain_v5_1.py"
CASES = ("100m", "119m", "150m")
PRIMARY_CASE = "119m"
LHV_GJ_PER_ODT = 18.5
PJ_GJ = 1_000_000.0
PROCESS_CASES = (
    ("process_availability_000", 0.0),
    ("process_availability_025", 0.25),
    ("process_availability_050", 0.5),
    ("process_availability_075", 0.75),
)
HARVEST_CASES = (
    ("harvest_recovery_035", 0.35),
    ("harvest_recovery_060", 0.60),
    ("harvest_recovery_075", 0.75),
)
SCENARIO_FIELDS = [
    "scenario",
    "geography_level",
    "province",
    "residue_type",
    "residue_class",
    "stream",
    "availability_case",
    "availability_fraction",
    "base_residue_odt",
    "residue_odt",
    "residue_pj",
    "lhv_gj_per_odt",
    "province_basis",
    "facility_assigned",
    "evidence_label",
    "observed",
    "policy_grade",
]


class NationalResidueCurveV9Error(RuntimeError):
    """Raised when the all-case curve extension fails."""


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise NationalResidueCurveV9Error(f"missing input: {path}")
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(
    path: Path, fields: list[str], rows: list[dict[str, Any]]
) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=fields, extrasaction="ignore", lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(
            {field: row.get(field, "") for field in fields} for row in rows
        )


def _fmt(value: float) -> str:
    if not math.isfinite(value):
        raise NationalResidueCurveV9Error(f"non-finite value {value!r}")
    return f"{value:.9f}"


def _process_bases() -> dict[tuple[str, str, str], float]:
    rows = _read_rows(FACILITY_DIR / "facility_residue_supply.csv")
    bases: dict[tuple[str, str, str], float] = defaultdict(float)
    for row in rows:
        bases[
            (
                row["scenario"],
                row["facility_province"].strip().upper(),
                row["facility_class"].strip().upper(),
            )
        ] += float(row["process_residue_odt"])
    return dict(bases)


def _harvest_bases() -> dict[tuple[str, str], float]:
    rows = _read_rows(CHAIN_DIR / "scenario_product_residue.csv")
    bases: dict[tuple[str, str], float] = defaultdict(float)
    for row in rows:
        if row["record_type"] != "harvest_residue":
            continue
        bases[
            (row["scenario"], row["province"].strip().upper())
        ] += float(row["harvest_residue_gross_odt"])
    return dict(bases)


def _province_names() -> dict[str, str]:
    rows = _read_rows(V8_CURVE_DIR / "province_residue_curve.csv")
    return {
        row["province"]: row["province_name"]
        for row in rows
        if row["province"] != "CAN"
    }


def build() -> dict[str, Any]:
    """Build the 119-invariant curve v9 plus all-case scenario curve."""

    _v8._v7._v6._base.assert_no_unexpected_deletions()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for name in (
        "national_residue_curve.csv",
        "province_residue_curve.csv",
        "osecan_demand_context.csv",
    ):
        source = V8_CURVE_DIR / name
        if name == "osecan_demand_context.csv" and not source.is_file():
            continue
        (OUTPUT_DIR / name).write_bytes(source.read_bytes())

    process = _process_bases()
    harvest = _harvest_bases()
    names = _province_names()
    scenario_rows: list[dict[str, Any]] = []
    for case in CASES:
        provinces = sorted(
            {
                province
                for scenario, province, _class in process
                if scenario == case
            }
            | {province for scenario, province in harvest if scenario == case}
        )
        for province in provinces:
            classes = sorted(
                {
                    facility_class
                    for scenario, prov, facility_class in process
                    if scenario == case and prov == province
                }
            )
            for facility_class in classes:
                base = process[(case, province, facility_class)]
                if base <= 0.0:
                    continue
                for availability_case, fraction in PROCESS_CASES:
                    residue_odt = base * fraction
                    scenario_rows.append(
                        {
                            "scenario": case,
                            "geography_level": "province",
                            "province": province,
                            "residue_type": "process_residue",
                            "residue_class": facility_class,
                            "stream": "process",
                            "availability_case": availability_case,
                            "availability_fraction": f"{fraction:.2f}",
                            "base_residue_odt": _fmt(base),
                            "residue_odt": _fmt(residue_odt),
                            "residue_pj": _fmt(
                                residue_odt * LHV_GJ_PER_ODT / PJ_GJ
                            ),
                            "lhv_gj_per_odt": f"{LHV_GJ_PER_ODT:.1f}",
                            "province_basis": "facility_province",
                            "facility_assigned": "true",
                            "evidence_label": "model_estimate",
                            "observed": "false",
                            "policy_grade": "false",
                        }
                    )
            harvest_base = harvest.get((case, province), 0.0)
            if harvest_base <= 0.0:
                continue
            for availability_case, fraction in HARVEST_CASES:
                residue_odt = harvest_base * fraction
                scenario_rows.append(
                    {
                        "scenario": case,
                        "geography_level": "province",
                        "province": province,
                        "residue_type": "harvest_residue_recoverable",
                        "residue_class": "ALL",
                        "stream": "harvest",
                        "availability_case": availability_case,
                        "availability_fraction": f"{fraction:.2f}",
                        "base_residue_odt": _fmt(harvest_base),
                        "residue_odt": _fmt(residue_odt),
                        "residue_pj": _fmt(
                            residue_odt * LHV_GJ_PER_ODT / PJ_GJ
                        ),
                        "lhv_gj_per_odt": f"{LHV_GJ_PER_ODT:.1f}",
                        "province_basis": "origin_province",
                        "facility_assigned": "false",
                        "evidence_label": "model_estimate",
                        "observed": "false",
                        "policy_grade": "false",
                    }
                )
        # National rows.
        for stream, cases in (
            ("process", PROCESS_CASES),
            ("harvest", HARVEST_CASES),
        ):
            for availability_case, fraction in cases:
                rows = [
                    row
                    for row in scenario_rows
                    if row["scenario"] == case
                    and row["stream"] == stream
                    and row["availability_case"] == availability_case
                    and row["geography_level"] == "province"
                ]
                base = math.fsum(
                    float(row["base_residue_odt"]) for row in rows
                )
                residue_odt = math.fsum(
                    float(row["residue_odt"]) for row in rows
                )
                scenario_rows.append(
                    {
                        "scenario": case,
                        "geography_level": "national",
                        "province": "CAN",
                        "residue_type": (
                            "process_residue"
                            if stream == "process"
                            else "harvest_residue_recoverable"
                        ),
                        "residue_class": (
                            "TOTAL" if stream == "process" else "ALL"
                        ),
                        "stream": stream,
                        "availability_case": availability_case,
                        "availability_fraction": f"{fraction:.2f}",
                        "base_residue_odt": _fmt(base),
                        "residue_odt": _fmt(residue_odt),
                        "residue_pj": _fmt(
                            residue_odt * LHV_GJ_PER_ODT / PJ_GJ
                        ),
                        "lhv_gj_per_odt": f"{LHV_GJ_PER_ODT:.1f}",
                        "province_basis": (
                            "facility_province"
                            if stream == "process"
                            else "origin_province"
                        ),
                        "facility_assigned": (
                            "true" if stream == "process" else "false"
                        ),
                        "evidence_label": "model_estimate",
                        "observed": "false",
                        "policy_grade": "false",
                    }
                )

    _write_csv(OUTPUT_DIR / "scenario_residue_curve.csv", SCENARIO_FIELDS, scenario_rows)

    # 119 reproduction guard against the frozen v8 curve.
    v8_national = [
        row
        for row in _read_rows(V8_CURVE_DIR / "national_residue_curve.csv")
        if row["geography_level"] == "national"
        and (
            (row["stream"] == "process" and row["residue_class"] == "TOTAL")
            or (row["stream"] == "harvest" and row["residue_class"] == "ALL")
        )
    ]
    v9_119 = [
        row
        for row in scenario_rows
        if row["scenario"] == PRIMARY_CASE
        and row["geography_level"] == "national"
    ]
    lookup = {
        (row["stream"], row["availability_case"]): float(row["residue_pj"])
        for row in v8_national
    }
    max_gap = 0.0
    for row in v9_119:
        key = (row["stream"], row["availability_case"])
        max_gap = max(max_gap, abs(float(row["residue_pj"]) - lookup[key]))
    if max_gap > 1e-6:
        raise NationalResidueCurveV9Error(
            f"119 curve does not reproduce v8: {max_gap!r} PJ"
        )

    summary: dict[str, Any] = {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "status": "complete",
        "scenario_order": list(CASES),
        "primary_scenario": PRIMARY_CASE,
        "assignment": {
            "100m": "v10_node_transport_aware",
            "119m": "v10_node_transport_aware",
            "150m": "v10_node_transport_aware",
        },
        "residue_components": (
            "E1_measured_plus_E2c_density_calibrated_stem_branch_foliage"
        ),
        "national_119_reproduction_max_pj_gap": max_gap,
        "scenario_totals": {
            case: {
                stream: {
                    availability_case: float(
                        next(
                            row["residue_pj"]
                            for row in scenario_rows
                            if row["scenario"] == case
                            and row["geography_level"] == "national"
                            and row["stream"] == stream
                            and row["availability_case"] == availability_case
                        )
                    )
                    for availability_case, _ in cases
                }
                for stream, cases in (
                    ("process", PROCESS_CASES),
                    ("harvest", HARVEST_CASES),
                )
            }
            for case in CASES
        },
        "labels": {
            "model_estimate": True,
            "observed": False,
            "policy_grade": False,
            "national_claim": False,
            "official_osecan_changed": False,
            "ui_changed": False,
            "transport_cost_in_osecan": False,
        },
        "caveats": [
            "The 119 Mm3 national and province curve files are inherited unchanged from frozen national_residue_curve_v8.",
            "The 100/150 Mm3 scenario curve applies the same 0/25/50/75% process and 35/60/75% harvest cases to case-specific facility-province process residue.",
            "Process residue is facility-assigned; harvest residue is origin-province only.",
            "All values are model estimates; observed=false and policy_grade=false.",
        ],
        "reproduce": (
            ".venv/bin/python -m "
            "workflows.national.facilities.national_residue_curve_v9"
        ),
    }
    (OUTPUT_DIR / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    outputs = {
        path.name: {
            "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for path in sorted(OUTPUT_DIR.iterdir())
        if path.is_file() and path.name != "manifest.json"
    }
    manifest = {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "status": "complete",
        "labels": summary["labels"],
        "scenario_order": list(CASES),
        "assignment": summary["assignment"],
        "outputs": outputs,
        "reproduce": summary["reproduce"],
    }
    (OUTPUT_DIR / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    report = (
        "# National Residue Supply-Curve Sensitivity v9\n\n"
        "119 Mm3 national and province curve files are invariant from frozen "
        "v8. The scenario curve extends the 0/25/50/75% process and 35/60/75% "
        "harvest cases to the 100 and 150 Mm3 assignment-v10 facility residue "
        "distribution. Process residue is facility-province based; harvest "
        "residue is origin-province only. Transport cost is not part of this "
        "curve. Labels: model_estimate / observed=false / policy_grade=false.\n"
    )
    (OUTPUT_DIR / REPORT_NAME).write_text(report, encoding="utf-8")
    outputs = {
        path.name: {
            "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for path in sorted(OUTPUT_DIR.iterdir())
        if path.is_file() and path.name != "manifest.json"
    }
    manifest["outputs"] = outputs
    (OUTPUT_DIR / "manifest.json").write_text(
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
                "scenario_totals": summary["scenario_totals"],
                "national_119_reproduction_max_pj_gap": summary[
                    "national_119_reproduction_max_pj_gap"
                ],
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
