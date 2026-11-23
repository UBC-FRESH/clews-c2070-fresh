"""Extend the v10 node transport-aware assignment to the 100 and 150 Mm3 cases.

The 119 Mm3 primary rows stay byte-for-byte identical to
``national_facility_assignment_v10``.  The two sensitivity cases reuse the same
525 supply nodes, the same capacity-v3 eligible facilities and the same
transport cost v1 curve.  Each province's 119 Mm3 node harvest vector is scaled
to the documented 100/150 Mm3 provincial allocation, preserving the
within-province node shares; the node-to-facility transportation LP is then
solved with the unchanged capacity bounds and objective.

This is an assignment-only screening extension.  Products, residue, capacity,
floor rules and facility siting are unchanged.  All values remain
``model_estimate`` with ``observed=false`` and ``policy_grade=false``.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

from workflows.national.facilities import (
    national_facility_assignment_v10 as v10,
)

REPO_ROOT = v10.REPO_ROOT
DATA_ROOT = v10.DATA_ROOT
OUTPUT_DIR = v10.OUTPUT_DIR
ASSIGNMENT_DIR = v10.OUTPUT_DIR
HARVEST_DIR = (
    DATA_ROOT / "derived/national/harvest_basis/provincial_allocation"
)
ARTIFACT = "national_facility_assignment_v10"
ARTIFACT_VERSION = "10.0.0"
CASES = (
    ("100m", 100_000_000.1),
    ("119m", 118_977_339.1),
    ("150m", 150_000_000.2),
)
PRIMARY_CASE = "119m"
SCENARIO = v10.SCENARIO
TOTAL_TOLERANCE_M3 = 1.0
CASE_FIELDS = v10.V10_ASSIGNMENT_FIELDS
FACILITY_FIELDS = v10.FACILITY_FIELDS
PROVINCE_FIELDS = v10.PROVINCE_FIELDS
SUMMARY_FIELDS = [
    "scenario",
    "harvest_case_m3",
    "assigned_m3",
    "facilities_used",
    "cross_border_m3",
    "cross_border_share",
    "proxy_share_of_assigned",
    "hhi",
    "top5_share",
    "mean_road_distance_km",
    "median_road_distance_km",
    "p90_road_distance_km",
    "max_road_distance_km",
    "share_ge_150_km",
    "share_ge_300_km",
    "transport_cost_cad",
    "transport_cost_cad_m3",
    "transport_cost_cad_gj",
    "binding_facilities",
    "binding_volume_m3",
    "evidence_label",
    "observed",
    "policy_grade",
]
COMPARISON_FIELDS = [
    "metric",
    "unit",
    "100m_value",
    "119m_value",
    "150m_value",
    "delta_100m_vs_119m",
    "delta_150m_vs_119m",
    "delta_pct_100m_vs_119m",
    "delta_pct_150m_vs_119m",
    "note",
    "evidence_label",
    "observed",
    "policy_grade",
]


class AssignmentV10CasesError(v10.AssignmentV10Error):
    """Raised when the 100/150 Mm3 v10 extension invariant fails."""


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise AssignmentV10CasesError(f"missing input: {path}")
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(
    path: Path, fields: list[str], rows: list[dict[str, Any]]
) -> None:
    v10._write_csv(path, fields, rows)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _case_harvest(
    case: str,
) -> tuple[dict[str, float], float]:
    rows = _read_rows(HARVEST_DIR / f"{case}.csv")
    target = {
        row["province"].strip().upper(): float(row["harvest_m3"])
        for row in rows
    }
    total = math.fsum(target.values())
    expected = dict(CASES)[case]
    if abs(total - expected) > TOTAL_TOLERANCE_M3:
        raise AssignmentV10CasesError(
            f"{case}: provincial harvest total {total!r} != {expected!r}"
        )
    return target, total


def _scaled_nodes(
    nodes: tuple[v10.SupplyNode, ...],
    target: dict[str, float],
) -> tuple[list[v10.SupplyNode], float]:
    base = defaultdict(float)
    for node in nodes:
        base[node.province] += node.harvest_m3
    scaled: list[v10.SupplyNode] = []
    for node in nodes:
        if base[node.province] <= 0.0:
            harvest = 0.0
        else:
            factor = target[node.province] / base[node.province]
            harvest = node.harvest_m3 * factor
        scaled.append(
            v10.SupplyNode(
                node_id=node.node_id,
                province=node.province,
                lat=node.lat,
                lon=node.lon,
                harvest_m3=harvest,
                weight=node.weight,
            )
        )
    total = math.fsum(node.harvest_m3 for node in scaled)
    return scaled, total


def _concentration(
    rows: list[dict[str, str]],
) -> tuple[float, float, float]:
    values = [
        float(row["assigned_m3"])
        for row in rows
        if row["used"].strip().lower() == "true"
    ]
    total = math.fsum(values)
    if total <= 0.0:
        raise AssignmentV10CasesError("facility rows have no assigned volume")
    shares = sorted((value / total for value in values), reverse=True)
    return (
        math.fsum(share * share for share in shares),
        shares[0],
        math.fsum(shares[:5]),
    )


def _case_metrics(
    rows: list[dict[str, Any]],
    facility_rows: list[dict[str, Any]],
) -> dict[str, float]:
    metrics = v10._metrics(rows)
    hhi, top1, top5 = _concentration(facility_rows)
    metrics["hhi"] = hhi
    metrics["max_facility_share"] = top1
    metrics["top5_share"] = top5
    return metrics


def _case_summary_payload(
    case: str,
    case_total: float,
    metrics: dict[str, float],
) -> dict[str, Any]:
    primary = {
        key: value
        for key, value in metrics.items()
        if key not in {"hhi", "max_facility_share", "top5_share"}
    }
    primary["hhi"] = metrics["hhi"]
    return {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "scenario": SCENARIO,
        "harvest_case": case,
        "harvest_case_m3": case_total,
        "labels": {
            "evidence_label": v10.EVIDENCE_LABEL,
            "observed": v10.OBSERVED,
            "policy_grade": v10.POLICY_GRADE,
            "national_claim": False,
            "official_osecan_changed": False,
            "ui_changed": False,
        },
        "v10_primary": primary,
        "scenarios": {
            SCENARIO: {
                "scenario": SCENARIO,
                **primary,
            }
        },
        "validation": {
            "all_passed": True,
            "failed_check_count": 0,
            "checks": [
                {
                    "name": f"{case}_node_transport_lp",
                    "status": "PASS",
                    "detail": "same nodes, capacity and transport curve",
                }
            ],
        },
    }


def _build_case(
    case: str,
    nodes: tuple[v10.SupplyNode, ...],
    facilities: list[Any],
    factors: dict[str, float],
    parameters: dict[str, Any],
    line: v10.CostLine,
    distances: np.ndarray,
    cross_border: np.ndarray,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, float], float]:
    target, case_total = _case_harvest(case)
    scaled, node_total = _scaled_nodes(nodes, target)
    if abs(node_total - case_total) > TOTAL_TOLERANCE_M3:
        raise AssignmentV10CasesError(
            f"{case}: scaled node total {node_total!r} != {case_total!r}"
        )
    positive = [node for node in scaled if node.harvest_m3 > 0.0]
    if len(positive) != distances.shape[0]:
        raise AssignmentV10CasesError(
            f"{case}: positive node count changed"
        )
    solution = v10._solve_transport_lp(
        positive,
        facilities,
        v10._objective(
            distances, cross_border, line, include_penalty=True
        ),
    )
    allocations = v10._tidy_allocations(positive, facilities, solution)
    rows = v10._v10_rows(
        positive,
        facilities,
        allocations,
        distances,
        factors,
        parameters,
        line,
    )
    facility_rows = v10._facility_rows(facilities, rows)
    offers = {province: target[province] for province in target}
    province_rows = v10._province_rows(
        positive, facilities, rows, offers
    )
    metrics = _case_metrics(rows, facility_rows)
    if abs(metrics["assigned_m3"] - case_total) > TOTAL_TOLERANCE_M3:
        raise AssignmentV10CasesError(
            f"{case}: assigned total {metrics['assigned_m3']!r}"
        )
    return rows, facility_rows, province_rows, metrics, case_total


def _summary_row(
    case: str, case_total: float, metrics: dict[str, float]
) -> dict[str, Any]:
    row = {
        "scenario": case,
        "harvest_case_m3": f"{case_total:.1f}",
        "evidence_label": v10.EVIDENCE_LABEL,
        "observed": v10._bool_text(v10.OBSERVED),
        "policy_grade": v10._bool_text(v10.POLICY_GRADE),
    }
    for field in SUMMARY_FIELDS:
        if field in row:
            continue
        row[field] = v10._format_float(float(metrics[field]))
    return row


def _comparison_rows(
    by_case: dict[str, dict[str, float]],
) -> list[dict[str, Any]]:
    metrics = [
        ("assigned_m3", "m3", "assigned volume"),
        ("facilities_used", "count", "facilities used"),
        ("cross_border_m3", "m3", "cross-border volume"),
        ("cross_border_share", "share", "cross-border share"),
        ("proxy_share_of_assigned", "share", "proxy share of assigned"),
        ("hhi", "index", "facility volume HHI"),
        ("top5_share", "share", "top-5 facility share"),
        ("mean_road_distance_km", "km", "mean road distance"),
        ("median_road_distance_km", "km", "median road distance"),
        ("p90_road_distance_km", "km", "P90 road distance"),
        ("max_road_distance_km", "km", "maximum road distance"),
        ("share_ge_150_km", "share", "share at or above 150 km"),
        ("share_ge_300_km", "share", "share at or above 300 km"),
        ("transport_cost_cad", "CAD/yr", "truck transport cost"),
        ("transport_cost_cad_m3", "CAD/m3", "transport cost per m3"),
        ("transport_cost_cad_gj", "CAD/GJ", "transport cost per GJ"),
        ("binding_facilities", "count", "capacity-binding facilities"),
    ]
    rows: list[dict[str, Any]] = []
    base = by_case[PRIMARY_CASE]
    for metric, unit, note in metrics:
        values = {
            case: float(by_case[case][metric])
            for case in ("100m", "119m", "150m")
        }
        rows.append(
            {
                "metric": metric,
                "unit": unit,
                "100m_value": f"{values['100m']:.9f}",
                "119m_value": f"{values['119m']:.9f}",
                "150m_value": f"{values['150m']:.9f}",
                "delta_100m_vs_119m": f"{values['100m'] - base[metric]:.9f}",
                "delta_150m_vs_119m": f"{values['150m'] - base[metric]:.9f}",
                "delta_pct_100m_vs_119m": (
                    f"{100.0 * (values['100m'] - base[metric]) / base[metric]:.9f}"
                    if base[metric]
                    else "0.000000000"
                ),
                "delta_pct_150m_vs_119m": (
                    f"{100.0 * (values['150m'] - base[metric]) / base[metric]:.9f}"
                    if base[metric]
                    else "0.000000000"
                ),
                "note": note,
                "evidence_label": v10.EVIDENCE_LABEL,
                "observed": v10._bool_text(v10.OBSERVED),
                "policy_grade": v10._bool_text(v10.POLICY_GRADE),
            }
        )
    return rows


def build() -> dict[str, Any]:
    """Build the 100/150 Mm3 v10 case files and comparison summary."""

    v10.v9.assert_no_unexpected_deletions()
    for required in (
        OUTPUT_DIR / "v10_node_assignment.csv",
        OUTPUT_DIR / "v10_facility_utilization.csv",
        OUTPUT_DIR / "v10_province_summary.csv",
        OUTPUT_DIR / "summary.json",
        OUTPUT_DIR / "manifest.json",
    ):
        if not required.is_file():
            raise AssignmentV10CasesError(f"missing v10 artifact: {required}")
    summary_path = OUTPUT_DIR / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if summary.get("artifact") != ARTIFACT:
        raise AssignmentV10CasesError("unexpected v10 artifact")
    if not summary.get("validation", {}).get("all_passed"):
        raise AssignmentV10CasesError("v10 primary artifact did not validate")
    nodes = v10.load_nodes()
    facilities, _offers, factors = v10.v9._eligible_facilities()
    parameters = v10.v9.load_transport_parameters()
    line = v10._cost_line(parameters)
    positive = [node for node in nodes if node.harvest_m3 > 0.0]
    distances = v10._node_facility_distances(positive, facilities, factors)
    cross_border = np.zeros(distances.shape, dtype=float)
    for node_index, node in enumerate(positive):
        for facility_index, facility in enumerate(facilities):
            if facility.province != node.province:
                cross_border[node_index, facility_index] = 1.0

    by_case: dict[str, dict[str, float]] = {}
    output_entries: dict[str, dict[str, Any]] = {}
    for case, _expected in CASES:
        if case == PRIMARY_CASE:
            continue
        rows, facility_rows, province_rows, metrics, total = _build_case(
            case,
            nodes,
            facilities,
            factors,
            parameters,
            line,
            distances,
            cross_border,
        )
        _write_csv(
            OUTPUT_DIR / f"v10_node_assignment_{case}.csv",
            CASE_FIELDS,
            rows,
        )
        _write_csv(
            OUTPUT_DIR / f"v10_facility_utilization_{case}.csv",
            FACILITY_FIELDS,
            facility_rows,
        )
        _write_csv(
            OUTPUT_DIR / f"v10_province_summary_{case}.csv",
            PROVINCE_FIELDS,
            province_rows,
        )
        payload = _case_summary_payload(case, total, metrics)
        v10._write_json(
            OUTPUT_DIR / f"case_summary_{case}.json", payload
        )
        by_case[case] = metrics
        for name in (
            f"v10_node_assignment_{case}.csv",
            f"v10_facility_utilization_{case}.csv",
            f"v10_province_summary_{case}.csv",
            f"case_summary_{case}.json",
        ):
            output_entries[name] = {
                "bytes": (OUTPUT_DIR / name).stat().st_size,
                "sha256": _sha256(OUTPUT_DIR / name),
            }

    primary = dict(summary["v10_primary"])
    facility_rows = _read_rows(OUTPUT_DIR / "v10_facility_utilization.csv")
    hhi, top1, top5 = _concentration(facility_rows)
    primary["hhi"] = hhi
    primary["max_facility_share"] = top1
    primary["top5_share"] = top5
    by_case[PRIMARY_CASE] = primary
    rows = [_summary_row(case, dict(CASES)[case], by_case[case]) for case, _ in CASES]
    _write_csv(OUTPUT_DIR / "v10_case_summary.csv", SUMMARY_FIELDS, rows)
    comparison = _comparison_rows(by_case)
    _write_csv(
        OUTPUT_DIR / "v10_case_comparison.csv",
        COMPARISON_FIELDS,
        comparison,
    )
    output_entries["v10_case_summary.csv"] = {
        "bytes": (OUTPUT_DIR / "v10_case_summary.csv").stat().st_size,
        "sha256": _sha256(OUTPUT_DIR / "v10_case_summary.csv"),
    }
    output_entries["v10_case_comparison.csv"] = {
        "bytes": (OUTPUT_DIR / "v10_case_comparison.csv").stat().st_size,
        "sha256": _sha256(OUTPUT_DIR / "v10_case_comparison.csv"),
    }

    summary["artifact_version"] = ARTIFACT_VERSION
    summary["status"] = "complete"
    summary["case_coverage"] = {
        "cases": [case for case, _ in CASES],
        "primary_case": PRIMARY_CASE,
        "method": (
            "119 Mm3 node harvest vector scaled to each documented provincial "
            "allocation; same eligible facilities, capacity v3 bounds, 1.2x "
            "floor and transport cost v1 curve; node transport LP re-solved"
        ),
        "products_residue": "unchanged; assignment-only extension",
        "capacity_change": False,
        "transport_curve_change": False,
    }
    summary["cases"] = {
        case: {
            "harvest_case_m3": dict(CASES)[case],
            "metrics": by_case[case],
        }
        for case, _ in CASES
    }
    summary["case_comparison_csv"] = "v10_case_comparison.csv"
    summary["labels"].update(
        {
            "assignment_change_vs_v9": True,
            "case_extension": True,
        }
    )
    summary["caveats"] = list(summary.get("caveats", [])) + [
        (
            "The 100 and 150 Mm3 cases scale each province's node harvest "
            "vector from the 119 Mm3 allocation; within-province node shares "
            "are unchanged."
        ),
        (
            "The 100/150 cases keep the 119 Mm3 eligible facility set, "
            "capacity bounds and 1.2x floor; no capacity or siting change is "
            "introduced."
        ),
    ]
    v10._write_json(summary_path, summary)

    manifest_path = OUTPUT_DIR / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifact_version"] = ARTIFACT_VERSION
    manifest["labels"] = summary["labels"]
    manifest["case_coverage"] = summary["case_coverage"]
    manifest["cases"] = summary["cases"]
    manifest["outputs"] = {
        path.name: {
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }
        for path in sorted(OUTPUT_DIR.iterdir())
        if path.is_file() and path.name != "manifest.json"
    }
    v10._write_json(manifest_path, manifest)
    return summary


def main() -> None:
    summary = build()
    print(
        json.dumps(
            {
                "artifact": summary["artifact"],
                "artifact_version": summary["artifact_version"],
                "cases": summary["case_coverage"]["cases"],
                "transport_cost_cad": {
                    case: summary["cases"][case]["metrics"][
                        "transport_cost_cad"
                    ]
                    for case in summary["case_coverage"]["cases"]
                },
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
