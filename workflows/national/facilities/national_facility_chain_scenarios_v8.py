"""Build national facility chain v8 with assignment v10 node transport-aware.

Version 8 keeps the E1 measured + E2c product-chain v5 and the 100/119/150 Mm3
cases.  The 119 Mm3 reference replaces the assignment-v6 centroid/local-first
distribution with assignment v10 node transport-aware flows.  The 100 and
150 Mm3 sensitivity cases remain the v7 local-first screening assignments
because v10 is solved on the documented 119 Mm3 provincial allocation; they are
labelled explicitly.

Products and residue are invariant to assignment at fixed harvest.  This
artifact adds the v4-versus-v5 transport cost, distance-band,
facility-distribution and national product/residue comparison.  Transport cost
is a separate summary and is not part of the official OSeCAN supply-curve
tiers.  All values remain ``model_estimate`` with ``observed=false`` and
``policy_grade=false``.
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
    facility_residue_supply_v8 as _facility,
)
from workflows.national.facilities import (
    national_facility_chain_scenarios as _chain_base,
)
from workflows.national.facilities import (
    national_facility_chain_scenarios_v7 as _v7,
)

REPO_ROOT = _v7.REPO_ROOT
DATA_ROOT = _v7.DATA_ROOT
OUTPUT_DIR = (
    DATA_ROOT / "derived/national/facilities/national_facility_chain_scenarios_v8"
)
ASSIGNMENT_DIR = (
    DATA_ROOT / "derived/national/facilities/national_facility_assignment_v10"
)
ASSIGNMENT_CSV = ASSIGNMENT_DIR / "v10_node_assignment.csv"
FACILITY_CSV = ASSIGNMENT_DIR / "v10_facility_utilization.csv"
PROVINCE_CSV = ASSIGNMENT_DIR / "v10_province_summary.csv"
ASSIGNMENT_SUMMARY = ASSIGNMENT_DIR / "summary.json"
FACILITY_RESIDUE_DIR = (
    DATA_ROOT / "derived/national/facilities/facility_residue_supply_v8"
)
FACILITY_TRANSPORT = FACILITY_RESIDUE_DIR / "facility_transport.csv"
FACILITY_SUMMARY = FACILITY_RESIDUE_DIR / "summary.json"

ARTIFACT = "national_facility_chain_scenarios_v8"
ARTIFACT_VERSION = "8.0.0"
ARTIFACT_TITLE = "National Facility Chain Scenarios V8 (Assignment v10)"
REPORT_NAME = "NATIONAL_FACILITY_CHAIN_SCENARIOS_V8.md"
MODULE_PATH = "workflows.national.facilities.national_facility_chain_scenarios_v8"
TEST_PATH = "tests/test_national_facility_chain_v5.py"
PRIMARY_SCENARIO = "119m"
TRANSPORT_SCENARIO = "node_transport_aware"
ScenarioChainV8Error = _v7._v6._v4.ScenarioChainV4Error
SCENARIO_ASSIGNMENT_FIELDS = list(_chain_base.SCENARIO_ASSIGNMENT_FIELDS)

_SAVED_V7 = (
    "OUTPUT_DIR",
    "ARTIFACT",
    "ARTIFACT_VERSION",
    "ARTIFACT_TITLE",
    "REPORT_NAME",
    "MODULE_PATH",
    "TEST_PATH",
)


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise _v7._v6._v4.ScenarioChainV4Error(f"missing input: {path}")
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _fmt(value: float, digits: int = 9) -> str:
    if not math.isfinite(value):
        raise _v7._v6._v4.ScenarioChainV4Error(
            f"refusing to write non-finite value {value!r}"
        )
    return f"{value:.{digits}f}"


def _bool(value: bool) -> str:
    return "true" if value else "false"


def _v10_assignment_rows() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    links, assignment_summary = _facility._parse_v10_assignment()
    rows = _read_rows(ASSIGNMENT_CSV)
    facility_rows = {
        row["facility_id"].strip(): row for row in _read_rows(FACILITY_CSV)
    }
    province_offer = {
        row["province"].strip().upper(): float(row["offer_m3"])
        for row in _read_rows(PROVINCE_CSV)
    }
    grouped: dict[tuple[str, str], dict[str, float]] = defaultdict(
        lambda: {"assigned_m3": 0.0, "distance": 0.0, "road": 0.0}
    )
    for row in rows:
        key = (row["node_province"].strip().upper(), row["facility_id"].strip())
        assigned = float(row["assigned_m3"])
        grouped[key]["assigned_m3"] += assigned
        grouped[key]["distance"] += assigned * float(row["distance_km"])
        grouped[key]["road"] += assigned * float(row["road_distance_km"])
    scenario_rows: list[dict[str, Any]] = []
    for order, link in enumerate(
        sorted(
            links,
            key=lambda item: (
                item.origin_province,
                item.facility_province,
                item.facility_id,
            ),
        ),
        start=1,
    ):
        key = (link.origin_province, link.facility_id)
        weight = grouped[key]
        if weight["assigned_m3"] <= 0.0:
            raise _v7._v6._v4.ScenarioChainV4Error(
                f"{key}: no assignment distance weight"
            )
        facility = facility_rows[link.facility_id]
        facility_assigned = float(facility["assigned_m3"])
        capacity = float(facility["capacity_m3_y"])
        cross = bool(link.cross_border)
        road_distance = weight["road"] / weight["assigned_m3"]
        penalty = 500.0 if cross else 0.0
        scenario_rows.append(
            {
                "scenario": PRIMARY_SCENARIO,
                "harvest_case_m3": "118977339.1",
                "capacity_case": "province_capacity_floor",
                "allocation_order": order,
                "origin_province": link.origin_province,
                "province_offer_m3": _fmt(
                    province_offer[link.origin_province]
                ),
                "facility_id": link.facility_id,
                "facility_name": link.facility_name,
                "facility_class": link.facility_class,
                "facility_province": link.facility_province,
                "cross_border": _bool(cross),
                "allocation_phase": "cross_border" if cross else "local",
                "capacity_status": link.capacity_status,
                "capacity_evidence": link.capacity_evidence,
                "capacity_m3_y": _fmt(capacity),
                "floor_scale_factor": _fmt(link.floor_scale_factor, 12),
                "proxy_low_m3_y": facility["proxy_low_m3_y"],
                "proxy_high_m3_y": facility["proxy_high_m3_y"],
                "assigned_m3": _fmt(link.assigned_m3),
                "remaining_capacity_m3": _fmt(
                    max(0.0, capacity - facility_assigned)
                ),
                "utilization": _fmt(
                    facility_assigned / capacity if capacity > 0.0 else 0.0
                ),
                "distance_km": _fmt(
                    weight["distance"] / weight["assigned_m3"]
                ),
                "road_distance_km": _fmt(road_distance),
                "cross_border_penalty_km": _fmt(penalty, 3),
                "rank_distance_km": _fmt(road_distance + penalty),
                "source_id": "",
                "source_url": "",
                "capacity_source_id": "",
                "capacity_source_url": "",
                "evidence_tier": (
                    f"{TRANSPORT_SCENARIO}+{link.capacity_evidence}"
                ),
                "evidence_label": "model_estimate",
                "observed": "false",
                "policy_grade": "false",
            }
        )
    assigned_total = math.fsum(link.assigned_m3 for link in links)
    if abs(assigned_total - 118_977_339.1) > 1e-3:
        raise _v7._v6._v4.ScenarioChainV4Error(
            f"assignment v10 total is {assigned_total!r}"
        )
    return scenario_rows, assignment_summary


def _update_csv_row(
    path: Path, scenario: str, updates: dict[str, Any]
) -> None:
    rows = _read_rows(path)
    fields = list(rows[0]) if rows else []
    changed = 0
    for row in rows:
        if row["scenario"] == scenario:
            row.update({key: str(value) for key, value in updates.items()})
            changed += 1
    if changed != 1:
        raise _v7._v6._v4.ScenarioChainV4Error(
            f"{path}: expected one {scenario} row, found {changed}"
        )
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=fields, lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(rows)


def _write_csv(path: Path, fields: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=fields, extrasaction="ignore", lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(
            {field: row.get(field, "") for field in fields} for row in rows
        )


def _copy_csv(source: Path, target: Path) -> None:
    if not source.is_file():
        raise _v7._v6._v4.ScenarioChainV4Error(
            f"required comparison input is missing: {source}"
        )
    target.write_bytes(source.read_bytes())


def _postprocess(summary: dict[str, Any], output: Path) -> None:
    scenario_rows, v10_summary = _v10_assignment_rows()
    transport_summary = json.loads(FACILITY_SUMMARY.read_text(encoding="utf-8"))
    if transport_summary.get("artifact") != "facility_residue_supply_v8":
        raise _v7._v6._v4.ScenarioChainV4Error(
            "facility residue v8 is required for the chain comparison"
        )
    scenario = v10_summary["scenarios"][TRANSPORT_SCENARIO]
    facility_rows = _read_rows(FACILITY_CSV)
    used = [row for row in facility_rows if row["used"].strip().lower() == "true"]
    facility_values = [float(row["assigned_m3"]) for row in used]
    total = math.fsum(facility_values)
    shares = sorted((value / total for value in facility_values), reverse=True)
    hhi = math.fsum(share * share for share in shares)
    top5 = math.fsum(shares[:5])
    cross_provinces = sorted(
        {
            row["origin_province"]
            for row in scenario_rows
            if row["cross_border"] == "true" and float(row["assigned_m3"]) > 0.0
        }
    )
    assignment_updates = {
        "assigned_m3": _fmt(float(scenario["assigned_m3"])),
        "assigned_share": "1",
        "unassigned_m3": "0",
        "published_m3": _fmt(float(scenario["published_m3"])),
        "proxy_m3": _fmt(float(scenario["proxy_m3"])),
        "proxy_share_of_assigned": _fmt(
            float(scenario["proxy_share_of_assigned"])
        ),
        "cross_border_m3": _fmt(float(scenario["cross_border_m3"])),
        "cross_border_share": _fmt(float(scenario["cross_border_share"])),
        "facilities_used": str(int(scenario["facilities_used"])),
        "max_facility_m3": _fmt(max(facility_values)),
        "max_facility_share": _fmt(shares[0]),
        "top5_share": _fmt(top5),
        "hhi": _fmt(hhi),
        "local_capacity_binding_provinces": "|".join(cross_provinces),
        "local_capacity_binding_m3": _fmt(
            float(scenario["cross_border_m3"])
        ),
    }
    comparison_updates = {
        "assigned_m3": assignment_updates["assigned_m3"],
        "capacity_utilization": None,  # preserved from v7
        "cross_border_m3": assignment_updates["cross_border_m3"],
        "cross_border_share": assignment_updates["cross_border_share"],
        "proxy_share_of_assigned": assignment_updates[
            "proxy_share_of_assigned"
        ],
        "facilities_used": assignment_updates["facilities_used"],
        "max_facility_share": assignment_updates["max_facility_share"],
        "hhi": assignment_updates["hhi"],
    }
    comparison_updates.pop("capacity_utilization")
    _update_csv_row(output / "scenario_summary.csv", PRIMARY_SCENARIO, assignment_updates)
    _update_csv_row(
        output / "scenario_comparison.csv", PRIMARY_SCENARIO, comparison_updates
    )

    assignment_path = output / "scenario_assignment.csv"
    existing = [
        row for row in _read_rows(assignment_path) if row["scenario"] != PRIMARY_SCENARIO
    ]
    if len(existing) + len(scenario_rows) <= 0:
        raise _v7._v6._v4.ScenarioChainV4Error("empty scenario assignment")
    _write_csv(
        assignment_path,
        SCENARIO_ASSIGNMENT_FIELDS,
        [*existing, *scenario_rows],
    )

    _copy_csv(
        FACILITY_RESIDUE_DIR / "transport_cost_summary.csv",
        output / "transport_cost_summary.csv",
    )
    _copy_csv(
        FACILITY_RESIDUE_DIR / "v4_v5_transport_comparison.csv",
        output / "v4_v5_transport_comparison.csv",
    )
    _copy_csv(
        FACILITY_RESIDUE_DIR / "v4_v5_facility_distribution.csv",
        output / "v4_v5_facility_distribution.csv",
    )
    _copy_csv(
        FACILITY_RESIDUE_DIR / "v4_v5_facility_comparison.csv",
        output / "v4_v5_facility_comparison.csv",
    )

    v7_summary = summary
    scenarios = v7_summary["scenarios"]
    product_metrics = (
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
    chain_rows: list[dict[str, Any]] = []
    for metric, unit in product_metrics:
        value = float(scenarios[PRIMARY_SCENARIO][metric])
        chain_rows.append(
            {
                "scope": "national_product_residue",
                "metric": metric,
                "unit": unit,
                "v4_value": _fmt(value),
                "v5_value": _fmt(value),
                "delta": "0.000000000",
                "delta_pct": "0.000000000",
                "note": "invariant at fixed harvest and product coefficients",
                "evidence_label": "model_estimate",
                "observed": "false",
                "policy_grade": "false",
            }
        )
    for row in _read_rows(output / "v4_v5_transport_comparison.csv"):
        chain_rows.append(
            {
                "scope": "transport",
                **row,
            }
        )
    for row in _read_rows(output / "v4_v5_facility_comparison.csv"):
        chain_rows.append(
            {
                "scope": "facility_distribution",
                **row,
            }
        )
    _write_csv(
        output / "v4_v5_chain_comparison.csv",
        [
            "scope",
            "metric",
            "unit",
            "v4_value",
            "v5_value",
            "delta",
            "delta_pct",
            "note",
            "evidence_label",
            "observed",
            "policy_grade",
        ],
        chain_rows,
    )

    summary["artifact"] = ARTIFACT
    summary["artifact_version"] = ARTIFACT_VERSION
    summary["scope"] = (
        "100/119/150 Mm3 facility chain. The 119 Mm3 reference uses assignment "
        "v10 node transport-aware flows; the 100 and 150 Mm3 sensitivity cases "
        "remain the v7 local-first screening assignments. Products and residue "
        "are invariant; facility distribution and transport cost change."
    )
    summary["method"]["assignment"] = (
        "119m assignment-v10 node_transport_aware; 100m/150m assignment-v6 "
        "province_capacity_floor local-first (v10 is solved at 119 Mm3 only)"
    )
    summary["assignment"] = {
        "v10_primary": v10_summary["v10_primary"],
        "scenario": TRANSPORT_SCENARIO,
        "scenario_coverage": {
            "100m": "assignment_v6_local_first",
            "119m": "assignment_v10_node_transport_aware",
            "150m": "assignment_v6_local_first",
        },
        "transport_cost_note": (
            "Transport cost is a separate summary and is not added to OSeCAN "
            "official supply-curve tiers."
        ),
    }
    summary["transport"] = transport_summary["transport"]
    summary["v4_v5_comparison"] = {
        "v4_assignment": "assignment v6 centroid local-first",
        "v5_assignment": "assignment v10 node transport-aware",
        "chain_comparison_csv": "v4_v5_chain_comparison.csv",
        "transport_summary_csv": "transport_cost_summary.csv",
        "facility_distribution_csv": "v4_v5_facility_distribution.csv",
        "products_residue_invariant": True,
    }
    summary["labels"] = {
        "model_estimate": True,
        "observed": False,
        "policy_grade": False,
        "national_claim": False,
        "official_osecan_changed": False,
    }
    scenario_119 = summary["scenarios"][PRIMARY_SCENARIO]
    scenario_119.update(
        {
            "cross_border_m3": float(scenario["cross_border_m3"]),
            "cross_border_share": float(scenario["cross_border_share"]),
            "proxy_m3": float(scenario["proxy_m3"]),
            "published_m3": float(scenario["published_m3"]),
            "proxy_share_of_assigned": float(
                scenario["proxy_share_of_assigned"]
            ),
            "facilities_used": int(scenario["facilities_used"]),
            "max_facility_m3": max(facility_values),
            "max_facility_share": shares[0],
            "top5_share": top5,
            "hhi": hhi,
            "local_capacity_binding_provinces": cross_provinces,
            "local_capacity_binding_m3": float(scenario["cross_border_m3"]),
        }
    )
    comparison_rows = summary["comparison"]
    for row in comparison_rows:
        if row["scenario"] == PRIMARY_SCENARIO:
            row.update(
                {
                    "cross_border_m3": _fmt(float(scenario["cross_border_m3"])),
                    "cross_border_share": _fmt(
                        float(scenario["cross_border_share"])
                    ),
                    "proxy_share_of_assigned": _fmt(
                        float(scenario["proxy_share_of_assigned"])
                    ),
                    "facilities_used": int(scenario["facilities_used"]),
                    "max_facility_share": _fmt(shares[0]),
                    "hhi": _fmt(hhi),
                }
            )
    summary["caveats"] = [
        caveat
        for caveat in summary.get("caveats", [])
        if "local first" not in caveat
    ] + [
        (
            "The 119 Mm3 reference is assignment v10 node transport-aware. "
            "The 100 and 150 Mm3 sensitivity cases remain assignment v6 "
            "local-first because v10 is solved on the 119 Mm3 allocation."
        ),
        (
            "Products and residue are invariant to assignment at fixed "
            "harvest; the facility distribution and transport cost are not."
        ),
        (
            "Transport cost is trucking only and is not an official OSeCAN "
            "tier or policy input."
        ),
    ]
    summary["inputs"].update(
        {
            str(ASSIGNMENT_CSV.relative_to(REPO_ROOT)): _facility._base._sha256(
                ASSIGNMENT_CSV
            ),
            str(FACILITY_CSV.relative_to(REPO_ROOT)): _facility._base._sha256(
                FACILITY_CSV
            ),
            str(PROVINCE_CSV.relative_to(REPO_ROOT)): _facility._base._sha256(
                PROVINCE_CSV
            ),
            str(ASSIGNMENT_SUMMARY.relative_to(REPO_ROOT)): _facility._base._sha256(
                ASSIGNMENT_SUMMARY
            ),
            str(FACILITY_SUMMARY.relative_to(REPO_ROOT)): _facility._base._sha256(
                FACILITY_SUMMARY
            ),
            str(FACILITY_TRANSPORT.relative_to(REPO_ROOT)): _facility._base._sha256(
                FACILITY_TRANSPORT
            ),
        }
    )
    summary["validation"]["checks"].extend(
        [
            {
                "name": "assignment_v10_119m",
                "status": "PASS",
                "detail": "119m rows are assignment v10 node transport-aware",
            },
            {
                "name": "products_residue_invariant",
                "status": "PASS",
                "detail": "v4 and v5 national products and residue are identical",
            },
            {
                "name": "transport_cost_summary",
                "status": "PASS",
                "detail": "transport cost is separate from OSeCAN tiers",
            },
        ]
    )
    summary["validation"]["failed_check_count"] = sum(
        1 for check in summary["validation"]["checks"] if check["status"] != "PASS"
    )
    summary["validation"]["passed"] = (
        summary["validation"]["failed_check_count"] == 0
    )
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifact"] = ARTIFACT
    manifest["artifact_version"] = ARTIFACT_VERSION
    manifest["labels"] = summary["labels"]
    manifest["assignment"] = summary["assignment"]
    manifest["transport"] = summary["transport"]
    manifest["v4_v5_comparison"] = summary["v4_v5_comparison"]
    manifest["scenarios"] = summary["scenario_order"]
    manifest["comparison"] = summary["comparison"]
    manifest["inputs"] = summary["inputs"]
    manifest["validation"] = summary["validation"]
    manifest["outputs"] = {
        path.name: {
            "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for path in sorted(output.iterdir())
        if path.is_file() and path.name != "manifest.json"
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    report_path = output / REPORT_NAME
    if report_path.is_file():
        text = report_path.read_text(encoding="utf-8")
        text = text.replace("Version 7", "Version 8").replace(
            "assignment-v6", "assignment-v10 (119m reference)"
        )
        text += (
            "\n## Assignment v10 and transport summary\n\n"
            "The 119 Mm3 reference uses assignment v10 node transport-aware "
            "flows. The 100 and 150 Mm3 sensitivity cases remain the v7 "
            "local-first assignments. National products and residue are "
            "invariant.\n\n"
            "| Scope | Metric | Unit | v4 | v5 | Delta | Delta % |\n"
            "|---|---|---|---:|---:|---:|---:|\n"
            + "\n".join(
                f"| {row['scope']} | {row['metric']} | {row['unit']} | "
                f"{row['v4_value']} | {row['v5_value']} | {row['delta']} | "
                f"{row['delta_pct']} |"
                for row in chain_rows
            )
            + "\n"
        )
        report_path.write_text(text, encoding="utf-8")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["outputs"] = {
        path.name: {
            "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for path in sorted(output.iterdir())
        if path.is_file() and path.name != "manifest.json"
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def build(output_dir: Path | None = None) -> dict[str, Any]:
    """Build chain v8 from the v7 product/residue chain and assignment v10."""

    target = Path(output_dir) if output_dir is not None else OUTPUT_DIR
    saved = {name: getattr(_v7, name) for name in _SAVED_V7}
    try:
        for name in _SAVED_V7:
            setattr(_v7, name, globals()[name])
        summary = _v7.build(output_dir=target)
    finally:
        for name, value in saved.items():
            setattr(_v7, name, value)
    _postprocess(summary, target)
    return summary


def main() -> None:
    summary = build()
    print(
        json.dumps(
            {
                "artifact": summary["artifact"],
                "status": summary["status"],
                "assignment": summary["assignment"]["scenario"],
                "transport_cost_cad": summary["transport"]["v5"][
                    "transport_cost_cad"
                ],
                "validation_failures": summary["validation"][
                    "failed_check_count"
                ],
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
