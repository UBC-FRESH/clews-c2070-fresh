"""Facility residue supply v9 across the 100/119/150 Mm3 assignment cases.

Version 9 carries the assignment-v10 node transport-aware distribution at all
three harvest cases.  The 119 Mm3 facility residue rows are inherited unchanged
from frozen ``facility_residue_supply_v8``; the 100 and 150 Mm3 rows are
recomputed with the same class-matching rule and E1/E2c product-chain v5
coefficients.  A v6 local-first facility residue baseline is computed for the
same cases so the distribution change is measured like-for-like.

Process residue is facility-assigned; harvest residue remains origin-province
only.  Transport cost is trucking-only and is not part of the official OSeCAN
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
    facility_residue_supply_v8 as _v8,
)

REPO_ROOT = _v8.REPO_ROOT
DATA_ROOT = _v8.DATA_ROOT
CHAIN_DIR = (
    DATA_ROOT / "derived/national/facilities/national_facility_chain_scenarios_v9"
)
CHAIN_PRODUCT_RESIDUE = CHAIN_DIR / "scenario_product_residue.csv"
V7_CHAIN_DIR = (
    DATA_ROOT / "derived/national/facilities/national_facility_chain_scenarios_v7"
)
ASSIGNMENT_DIR = (
    DATA_ROOT / "derived/national/facilities/national_facility_assignment_v10"
)
V8_DIR = (
    DATA_ROOT / "derived/national/facilities/facility_residue_supply_v8"
)
OUTPUT_DIR = DATA_ROOT / "derived/national/facilities/facility_residue_supply_v9"
ARTIFACT = "facility_residue_supply_v9"
ARTIFACT_VERSION = "9.0.0"
ARTIFACT_TITLE = "National Facility Residue Supply v9 (Assignment v10, all cases)"
REPORT_NAME = "FACILITY_RESIDUE_SUPPLY_V9.md"
MODULE_PATH = "workflows.national.facilities.facility_residue_supply_v9"
TEST_PATH = "tests/test_national_facility_chain_v5_1.py"
CASES = ("100m", "119m", "150m")
PRIMARY_CASE = "119m"
CASE_TOTAL = {"100m": 100_000_000.1, "119m": 118_977_339.1, "150m": 150_000_000.2}
PJ_GJ = 1000.0
LHV_GJ_PER_ODT = 18.5
BANDS = (
    ("le_50_km", 0.0, 50.0),
    ("gt_50_le_100_km", 50.0, 100.0),
    ("gt_100_le_150_km", 100.0, 150.0),
    ("gt_150_le_300_km", 150.0, 300.0),
    ("gt_300_km", 300.0, math.inf),
)
FACILITY_FIELDS = [
    "scenario",
    "assignment",
    "origin_province",
    "facility_id",
    "facility_name",
    "facility_class",
    "facility_province",
    "cross_border",
    "allocation_scope",
    "assigned_m3",
    "feedstock_m3",
    "process_residue_odt",
    "process_residue_pj",
    "capacity_status",
    "capacity_evidence",
    "floor_scale_factor",
    "capacity_m3_y",
    "utilization",
    "road_distance_km",
    "transport_cost_cad",
    "evidence_label",
    "observed",
    "policy_grade",
]
TRANSPORT_FIELDS = [
    "scenario",
    "assignment",
    "record_type",
    "facility_id",
    "facility_name",
    "facility_class",
    "facility_province",
    "origin_province",
    "assigned_m3",
    "facility_assigned_m3",
    "capacity_m3_y",
    "facility_utilization",
    "cross_border",
    "road_distance_km_weighted",
    "transport_cost_cad",
    "transport_energy_gj",
    "transport_cost_cad_m3",
    "transport_cost_cad_gj",
    "distance_band",
    "evidence_label",
    "observed",
    "policy_grade",
]
PROVINCE_FIELDS = [
    "scenario",
    "assignment",
    "province",
    "process_residue_odt",
    "process_residue_pj",
    "facility_count",
    "facility_count_positive_residue",
    "origin_process_residue_odt",
    "origin_process_residue_pj",
    "published_residue_share",
    "proxy_residue_share",
    "cross_border_residue_odt",
    "cross_border_residue_share",
    "top1_facility_residue_share",
    "top5_facility_residue_share",
    "hhi_facility_residue",
    "evidence_label",
    "observed",
    "policy_grade",
]
NATIONAL_FIELDS = [
    "scenario",
    "assignment",
    "metric",
    "residue_type",
    "unit",
    "value",
    "evidence_label",
    "observed",
    "policy_grade",
]
TRANSPORT_SUMMARY_FIELDS = [
    "scenario",
    "assignment",
    "scope",
    "scope_id",
    "metric",
    "unit",
    "value",
    "note",
    "evidence_label",
    "observed",
    "policy_grade",
]
COMPARISON_FIELDS = [
    "scenario",
    "metric",
    "unit",
    "v6_value",
    "v10_value",
    "delta",
    "delta_pct",
    "note",
    "evidence_label",
    "observed",
    "policy_grade",
]


class FacilityResidueSupplyV9Error(_v8._base.FacilityResidueSupplyError):
    """Raised when the all-case facility residue extension fails."""


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise FacilityResidueSupplyV9Error(f"missing input: {path}")
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


def _fmt(value: float, digits: int = 9) -> str:
    if not math.isfinite(value):
        raise FacilityResidueSupplyV9Error(f"non-finite value {value!r}")
    return f"{value:.{digits}f}"


def _bool(value: bool) -> str:
    return "true" if value else "false"


def _band(distance: float) -> str:
    for name, low, high in BANDS:
        if distance >= low and distance < high:
            return name
    return "gt_300_km"


def _quantile(pairs: list[tuple[float, float]], q: float) -> float:
    total = math.fsum(weight for _, weight in pairs)
    target = q * total
    cumulative = 0.0
    for value, weight in sorted(pairs):
        cumulative += weight
        if cumulative >= target:
            return value
    return pairs[-1][0]


def _chain_cases() -> dict[tuple[str, str, str], dict[str, float]]:
    rows = _read_rows(CHAIN_PRODUCT_RESIDUE)
    cases: dict[tuple[str, str, str], dict[str, float]] = {}
    for row in rows:
        if row["record_type"] != "facility_process":
            continue
        key = (
            row["scenario"],
            row["province"].strip().upper(),
            row["facility_class"].strip().upper(),
        )
        cases[key] = {
            "feedstock_m3": float(row["feedstock_m3"]),
            "process_residue_odt": float(row["process_residue_odt"]),
            "process_residue_pj": float(row["process_residue_pj"]),
        }
    if {key[0] for key in cases} != set(CASES):
        raise FacilityResidueSupplyV9Error("chain v9 case coverage mismatch")
    return cases


def _chain_harvest() -> dict[str, dict[str, float]]:
    rows = _read_rows(CHAIN_PRODUCT_RESIDUE)
    national = json.loads(
        (CHAIN_DIR / "summary.json").read_text(encoding="utf-8")
    )["scenarios"]
    return {
        case: {
            "harvest_gross_odt": float(national[case]["harvest_residue_gross_odt"]),
            "harvest_gross_pj": float(national[case]["harvest_residue_gross_pj"]),
            "harvest_recoverable_odt": float(
                national[case]["harvest_residue_recoverable_odt"]
            ),
            "harvest_recoverable_pj": float(
                national[case]["harvest_residue_recoverable_pj"]
            ),
            "harvest_unrecovered_odt": float(
                national[case]["harvest_residue_unrecovered_odt"]
            ),
            "harvest_unrecovered_pj": float(
                national[case]["harvest_residue_unrecovered_pj"]
            ),
        }
        for case in CASES
    }


def _assignment_links(case: str) -> list[dict[str, Any]]:
    path = (
        ASSIGNMENT_DIR / "v10_node_assignment.csv"
        if case == PRIMARY_CASE
        else ASSIGNMENT_DIR / f"v10_node_assignment_{case}.csv"
    )
    rows = _read_rows(path)
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        origin = row["node_province"].strip().upper()
        facility_id = row["facility_id"].strip()
        key = (origin, facility_id)
        group = groups.get(key)
        if group is None:
            group = {
                "origin_province": origin,
                "facility_id": facility_id,
                "facility_name": row["facility_name"].strip(),
                "facility_class": row["facility_class"].strip().upper(),
                "facility_province": row["facility_province"].strip().upper(),
                "capacity_status": row["capacity_status"].strip(),
                "capacity_evidence": row["capacity_evidence"].strip(),
                "floor_scale_factor": float(row["floor_scale_factor"]),
                "capacity_m3_y": float(row["capacity_m3_y"]),
                "assigned_m3": 0.0,
                "transport_cost_cad": 0.0,
                "transport_energy_gj": 0.0,
                "volume_distance": 0.0,
                "distance_rows": [],
            }
            groups[key] = group
        assigned = float(row["assigned_m3"])
        distance = float(row["road_distance_km"])
        group["assigned_m3"] += assigned
        group["transport_cost_cad"] += float(row["transport_cost_cad"])
        group["transport_energy_gj"] += float(row["transport_energy_gj"])
        group["volume_distance"] += assigned * distance
        group["distance_rows"].append((distance, assigned))
    links: list[dict[str, Any]] = []
    for group in groups.values():
        assigned = float(group["assigned_m3"])
        capacity = float(group["capacity_m3_y"])
        links.append(
            {
                **group,
                "cross_border": group["facility_province"]
                != group["origin_province"],
                "utilization": assigned / capacity,
                "road_distance_km": float(group["volume_distance"]) / assigned,
            }
        )
    links.sort(key=lambda item: (item["origin_province"], item["facility_id"]))
    return links


def _v6_links(case: str) -> list[dict[str, Any]]:
    parameters = _v8._v9.load_transport_parameters()
    rows = [
        row
        for row in _read_rows(V7_CHAIN_DIR / "scenario_assignment.csv")
        if row["scenario"] == case
    ]
    links: list[dict[str, Any]] = []
    for row in rows:
        distance = float(row["road_distance_km"])
        volume = float(row["assigned_m3"])
        cost = volume * _v8._v9.transport_metrics(distance, parameters)[
            "cost_m3_base"
        ]
        links.append(
            {
                "origin_province": row["origin_province"].strip().upper(),
                "facility_id": row["facility_id"].strip(),
                "facility_name": row["facility_name"].strip(),
                "facility_class": row["facility_class"].strip().upper(),
                "facility_province": row["facility_province"].strip().upper(),
                "capacity_status": row["capacity_status"].strip(),
                "capacity_evidence": row.get("capacity_evidence", "").strip(),
                "floor_scale_factor": float(row["floor_scale_factor"]),
                "capacity_m3_y": float(row["capacity_m3_y"]),
                "assigned_m3": volume,
                "transport_cost_cad": cost,
                "transport_energy_gj": volume * _v8._v10.ENERGY_GJ_PER_M3,
                "road_distance_km": distance,
                "distance_rows": [(distance, volume)],
                "cross_border": row["cross_border"].strip().lower() == "true",
                "utilization": volume / float(row["capacity_m3_y"]),
            }
        )
    return links


def _allocate(
    case: str,
    assignment: str,
    links: list[dict[str, Any]],
    chain_cases: dict[tuple[str, str, str], dict[str, float]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, float]]:
    by_origin_class: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    by_class: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for link in links:
        by_origin_class[(link["origin_province"], link["facility_class"])].append(
            link
        )
        by_class[link["facility_class"]].append(link)
    rows: list[dict[str, Any]] = []
    for (case_key, province, facility_class), chain in sorted(
        chain_cases.items()
    ):
        if case_key != case or province == "":
            continue
        if chain["feedstock_m3"] <= 0.0:
            continue
        pool = by_origin_class.get((province, facility_class), [])
        scope = "province_assignment"
        if not pool:
            pool = by_class.get(facility_class, [])
            scope = "national_class_fallback"
        if not pool:
            raise FacilityResidueSupplyV9Error(
                f"{province}/{facility_class}: no assignment pool"
            )
        total_weight = math.fsum(link["assigned_m3"] for link in pool)
        if total_weight <= 0.0:
            raise FacilityResidueSupplyV9Error(
                f"{province}/{facility_class}: zero assignment weight"
            )
        for link in pool:
            share = link["assigned_m3"] / total_weight
            rows.append(
                {
                    "scenario": case,
                    "assignment": assignment,
                    "origin_province": province,
                    "facility_id": link["facility_id"],
                    "facility_name": link["facility_name"],
                    "facility_class": facility_class,
                    "facility_province": link["facility_province"],
                    "cross_border": _bool(link["cross_border"]),
                    "allocation_scope": scope,
                    "assigned_m3": _fmt(link["assigned_m3"]),
                    "feedstock_m3": _fmt(chain["feedstock_m3"] * share),
                    "process_residue_odt": _fmt(
                        chain["process_residue_odt"] * share
                    ),
                    "process_residue_pj": _fmt(
                        chain["process_residue_pj"] * share
                    ),
                    "capacity_status": link["capacity_status"],
                    "capacity_evidence": link["capacity_evidence"],
                    "floor_scale_factor": _fmt(
                        link["floor_scale_factor"], 12
                    ),
                    "capacity_m3_y": _fmt(link["capacity_m3_y"]),
                    "utilization": _fmt(link["utilization"]),
                    "road_distance_km": _fmt(link["road_distance_km"]),
                    "transport_cost_cad": _fmt(link["transport_cost_cad"], 6),
                    "evidence_label": "model_estimate",
                    "observed": "false",
                    "policy_grade": "false",
                }
            )
    process_odt = math.fsum(float(row["process_residue_odt"]) for row in rows)
    expected = math.fsum(
        chain["process_residue_odt"]
        for (case_key, _, _), chain in chain_cases.items()
        if case_key == case
    )
    if abs(process_odt - expected) > 1e-3:
        raise FacilityResidueSupplyV9Error(
            f"{case}/{assignment}: process residue {process_odt!r} != {expected!r}"
        )
    return rows, links, {"process_residue_odt": process_odt}


def _facility_transport_rows(
    case: str,
    assignment: str,
    links: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    facility_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for link in links:
        facility_groups[link["facility_id"]].append(link)
    rows: list[dict[str, Any]] = []
    for link in sorted(
        links,
        key=lambda item: (
            item["origin_province"],
            item["facility_id"],
        ),
    ):
        assigned = float(link["assigned_m3"])
        cost = float(link["transport_cost_cad"])
        energy = float(link["transport_energy_gj"])
        rows.append(
            {
                "scenario": case,
                "assignment": assignment,
                "record_type": "origin_facility",
                "facility_id": link["facility_id"],
                "facility_name": link["facility_name"],
                "facility_class": link["facility_class"],
                "facility_province": link["facility_province"],
                "origin_province": link["origin_province"],
                "assigned_m3": _fmt(assigned),
                "facility_assigned_m3": _fmt(
                    math.fsum(
                        item["assigned_m3"]
                        for item in facility_groups[link["facility_id"]]
                    )
                ),
                "capacity_m3_y": _fmt(link["capacity_m3_y"]),
                "facility_utilization": _fmt(link["utilization"]),
                "cross_border": _bool(link["cross_border"]),
                "road_distance_km_weighted": _fmt(link["road_distance_km"]),
                "transport_cost_cad": _fmt(cost, 6),
                "transport_energy_gj": _fmt(energy, 6),
                "transport_cost_cad_m3": _fmt(cost / assigned),
                "transport_cost_cad_gj": _fmt(cost / energy if energy else 0.0),
                "distance_band": _band(link["road_distance_km"]),
                "evidence_label": "model_estimate",
                "observed": "false",
                "policy_grade": "false",
            }
        )
    for facility_id, group in sorted(facility_groups.items()):
        assigned = math.fsum(item["assigned_m3"] for item in group)
        cost = math.fsum(item["transport_cost_cad"] for item in group)
        energy = math.fsum(item["transport_energy_gj"] for item in group)
        distances = [
            (item["road_distance_km"], item["assigned_m3"]) for item in group
        ]
        first = group[0]
        rows.append(
            {
                "scenario": case,
                "assignment": assignment,
                "record_type": "facility_total",
                "facility_id": facility_id,
                "facility_name": first["facility_name"],
                "facility_class": first["facility_class"],
                "facility_province": first["facility_province"],
                "origin_province": "",
                "assigned_m3": _fmt(assigned),
                "facility_assigned_m3": _fmt(assigned),
                "capacity_m3_y": _fmt(first["capacity_m3_y"]),
                "facility_utilization": _fmt(
                    assigned / first["capacity_m3_y"]
                ),
                "cross_border": _bool(
                    any(item["cross_border"] for item in group)
                ),
                "road_distance_km_weighted": _fmt(
                    math.fsum(d * v for d, v in distances) / assigned
                ),
                "transport_cost_cad": _fmt(cost, 6),
                "transport_energy_gj": _fmt(energy, 6),
                "transport_cost_cad_m3": _fmt(cost / assigned),
                "transport_cost_cad_gj": _fmt(cost / energy if energy else 0.0),
                "distance_band": _band(
                    math.fsum(d * v for d, v in distances) / assigned
                ),
                "evidence_label": "model_estimate",
                "observed": "false",
                "policy_grade": "false",
            }
        )
    return rows


def _province_rows(
    case: str,
    assignment: str,
    residue_rows: list[dict[str, Any]],
    links: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_facility_province: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_origin: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in residue_rows:
        by_facility_province[row["facility_province"]].append(row)
        by_origin[row["origin_province"]].append(row)
    rows: list[dict[str, Any]] = []
    for province in sorted(
        set(by_facility_province) | set(by_origin)
    ):
        facility_rows = by_facility_province.get(province, [])
        origin_rows = by_origin.get(province, [])
        residue_odt = math.fsum(
            float(row["process_residue_odt"]) for row in facility_rows
        )
        residue_pj = math.fsum(
            float(row["process_residue_pj"]) for row in facility_rows
        )
        origin_odt = math.fsum(
            float(row["process_residue_odt"]) for row in origin_rows
        )
        origin_pj = math.fsum(
            float(row["process_residue_pj"]) for row in origin_rows
        )
        facility_values = defaultdict(float)
        published = 0.0
        cross = 0.0
        for row in facility_rows:
            value = float(row["process_residue_odt"])
            facility_values[row["facility_id"]] += value
            if row["capacity_status"] == "published":
                published += value
            if row["cross_border"] == "true":
                cross += value
        values = [value for value in facility_values.values() if value > 0.0]
        total = math.fsum(values)
        shares = sorted((value / total for value in values), reverse=True)
        rows.append(
            {
                "scenario": case,
                "assignment": assignment,
                "province": province,
                "process_residue_odt": _fmt(residue_odt),
                "process_residue_pj": _fmt(residue_pj),
                "facility_count": str(len(facility_values)),
                "facility_count_positive_residue": str(len(values)),
                "origin_process_residue_odt": _fmt(origin_odt),
                "origin_process_residue_pj": _fmt(origin_pj),
                "published_residue_share": _fmt(
                    published / residue_odt if residue_odt else 0.0
                ),
                "proxy_residue_share": _fmt(
                    1.0 - published / residue_odt if residue_odt else 0.0
                ),
                "cross_border_residue_odt": _fmt(cross),
                "cross_border_residue_share": _fmt(
                    cross / residue_odt if residue_odt else 0.0
                ),
                "top1_facility_residue_share": _fmt(
                    shares[0] if shares else 0.0
                ),
                "top5_facility_residue_share": _fmt(
                    math.fsum(shares[:5])
                ),
                "hhi_facility_residue": _fmt(
                    math.fsum(share * share for share in shares)
                ),
                "evidence_label": "model_estimate",
                "observed": "false",
                "policy_grade": "false",
            }
        )
    return rows


def _national_rows(
    case: str,
    assignment: str,
    residue_rows: list[dict[str, Any]],
    harvest: dict[str, float],
) -> tuple[list[dict[str, Any]], dict[str, float]]:
    process_odt = math.fsum(
        float(row["process_residue_odt"]) for row in residue_rows
    )
    process_pj = math.fsum(
        float(row["process_residue_pj"]) for row in residue_rows
    )
    facility_values = defaultdict(float)
    published = 0.0
    cross = 0.0
    for row in residue_rows:
        value = float(row["process_residue_odt"])
        facility_values[row["facility_id"]] += value
        if row["capacity_status"] == "published":
            published += value
        if row["cross_border"] == "true":
            cross += value
    values = [value for value in facility_values.values() if value > 0.0]
    total = math.fsum(values)
    shares = sorted((value / total for value in values), reverse=True)
    totals = {
        "process_residue_odt": process_odt,
        "process_residue_pj": process_pj,
        "harvest_residue_gross_odt": harvest["harvest_gross_odt"],
        "harvest_residue_gross_pj": harvest["harvest_gross_pj"],
        "harvest_residue_recoverable_odt": harvest["harvest_recoverable_odt"],
        "harvest_residue_recoverable_pj": harvest["harvest_recoverable_pj"],
        "harvest_residue_unrecovered_odt": harvest["harvest_unrecovered_odt"],
        "harvest_residue_unrecovered_pj": harvest["harvest_unrecovered_pj"],
        "facilities_with_positive_residue": float(len(values)),
        "cross_border_residue_odt": cross,
        "cross_border_residue_share": cross / process_odt if process_odt else 0.0,
        "proxy_residue_share": (
            1.0 - published / process_odt if process_odt else 0.0
        ),
        "published_residue_share": published / process_odt if process_odt else 0.0,
        "hhi_process_residue": math.fsum(share * share for share in shares),
        "top1_process_residue_share": shares[0] if shares else 0.0,
        "top5_process_residue_share": math.fsum(shares[:5]),
        "combined_process_plus_harvest_gross_odt": process_odt
        + harvest["harvest_gross_odt"],
        "combined_process_plus_harvest_gross_pj": process_pj
        + harvest["harvest_gross_pj"],
        "combined_process_plus_harvest_recoverable_odt": process_odt
        + harvest["harvest_recoverable_odt"],
        "combined_process_plus_harvest_recoverable_pj": process_pj
        + harvest["harvest_recoverable_pj"],
    }
    units = {
        "process_residue_odt": ("odt", "process_residue"),
        "process_residue_pj": ("PJ", "process_residue"),
        "harvest_residue_gross_odt": ("odt", "harvest_residue"),
        "harvest_residue_gross_pj": ("PJ", "harvest_residue"),
        "harvest_residue_recoverable_odt": ("odt", "harvest_residue"),
        "harvest_residue_recoverable_pj": ("PJ", "harvest_residue"),
        "harvest_residue_unrecovered_odt": ("odt", "harvest_residue"),
        "harvest_residue_unrecovered_pj": ("PJ", "harvest_residue"),
        "facilities_with_positive_residue": ("count", "process_residue"),
        "cross_border_residue_odt": ("odt", "process_residue"),
        "cross_border_residue_share": ("share", "process_residue"),
        "proxy_residue_share": ("share", "process_residue"),
        "published_residue_share": ("share", "process_residue"),
        "hhi_process_residue": ("index_0_1", "process_residue"),
        "top1_process_residue_share": ("share", "process_residue"),
        "top5_process_residue_share": ("share", "process_residue"),
        "combined_process_plus_harvest_gross_odt": ("odt", "combined"),
        "combined_process_plus_harvest_gross_pj": ("PJ", "combined"),
        "combined_process_plus_harvest_recoverable_odt": ("odt", "combined"),
        "combined_process_plus_harvest_recoverable_pj": ("PJ", "combined"),
    }
    return (
        [
            {
                "scenario": case,
                "assignment": assignment,
                "metric": metric,
                "residue_type": units[metric][1],
                "unit": units[metric][0],
                "value": _fmt(value),
                "evidence_label": "model_estimate",
                "observed": "false",
                "policy_grade": "false",
            }
            for metric, value in totals.items()
        ],
        totals,
    )


def _transport_summary(
    case: str,
    assignment: str,
    links: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, float]]:
    assigned = math.fsum(link["assigned_m3"] for link in links)
    cost = math.fsum(link["transport_cost_cad"] for link in links)
    energy = math.fsum(link["transport_energy_gj"] for link in links)
    pairs = [(link["road_distance_km"], link["assigned_m3"]) for link in links]
    metrics = {
        "assigned_m3": assigned,
        "transport_cost_cad": cost,
        "transport_cost_cad_m3": cost / assigned,
        "transport_cost_cad_gj": cost / energy if energy else 0.0,
        "mean_road_distance_km": math.fsum(d * v for d, v in pairs) / assigned,
        "median_road_distance_km": _quantile(pairs, 0.5),
        "p90_road_distance_km": _quantile(pairs, 0.9),
        "max_road_distance_km": max(d for d, _ in pairs),
        "share_ge_150_km": math.fsum(v for d, v in pairs if d >= 150.0)
        / assigned,
        "share_ge_300_km": math.fsum(v for d, v in pairs if d >= 300.0)
        / assigned,
        "facilities_used": float(len({link["facility_id"] for link in links})),
        "cross_border_m3": math.fsum(
            link["assigned_m3"] for link in links if link["cross_border"]
        ),
        "cross_border_share": math.fsum(
            link["assigned_m3"] for link in links if link["cross_border"]
        )
        / assigned,
    }
    rows: list[dict[str, Any]] = []
    for metric, value in metrics.items():
        unit = (
            "CAD/yr"
            if metric == "transport_cost_cad"
            else "CAD/m3"
            if metric == "transport_cost_cad_m3"
            else "CAD/GJ"
            if metric == "transport_cost_cad_gj"
            else "km"
            if "distance" in metric or metric.endswith("_km")
            else "share"
            if "share" in metric
            else "m3"
        )
        rows.append(
            {
                "scenario": case,
                "assignment": assignment,
                "scope": "assignment",
                "scope_id": assignment,
                "metric": metric,
                "unit": unit,
                "value": _fmt(value),
                "note": "Assigned-volume-weighted screening transport metric.",
                "evidence_label": "model_estimate",
                "observed": "false",
                "policy_grade": "false",
            }
        )
    for band, low, high in BANDS:
        volume = math.fsum(
            link["assigned_m3"]
            for link in links
            if link["road_distance_km"] >= low
            and link["road_distance_km"] < high
        )
        rows.append(
            {
                "scenario": case,
                "assignment": assignment,
                "scope": "distance_band",
                "scope_id": band,
                "metric": "volume",
                "unit": "m3",
                "value": _fmt(volume),
                "note": "Assigned volume in the road-distance band.",
                "evidence_label": "model_estimate",
                "observed": "false",
                "policy_grade": "false",
            }
        )
        rows.append(
            {
                "scenario": case,
                "assignment": assignment,
                "scope": "distance_band",
                "scope_id": band,
                "metric": "share",
                "unit": "share",
                "value": _fmt(volume / assigned),
                "note": "Assigned-volume share in the road-distance band.",
                "evidence_label": "model_estimate",
                "observed": "false",
                "policy_grade": "false",
            }
        )
    return rows, metrics


def _copy_v8_119() -> tuple[
    list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]
]:
    residue = []
    for row in _read_rows(V8_DIR / "facility_residue_supply.csv"):
        residue.append(
            {
                "scenario": PRIMARY_CASE,
                "assignment": "v10_node_transport_aware",
                "origin_province": row["origin_province"],
                "facility_id": row["facility_id"],
                "facility_name": row["facility_name"],
                "facility_class": row["facility_class"],
                "facility_province": row["province"],
                "cross_border": row["cross_border"],
                "allocation_scope": row["allocation_scope"],
                "assigned_m3": row["assignment_weight_m3"],
                "feedstock_m3": row["feedstock_m3"],
                "process_residue_odt": row["residue_odt"],
                "process_residue_pj": row["residue_pj"],
                "capacity_status": row["capacity_status"],
                "capacity_evidence": row["capacity_evidence"],
                "floor_scale_factor": row["floor_scale_factor"],
                "capacity_m3_y": row["capacity_m3_y"],
                "utilization": row["assignment_utilization_v10"],
                "road_distance_km": "",
                "transport_cost_cad": "",
                "evidence_label": "model_estimate",
                "observed": "false",
                "policy_grade": "false",
            }
        )
    transport = [
        {**row, "scenario": PRIMARY_CASE, "assignment": "v10_node_transport_aware"}
        for row in _read_rows(V8_DIR / "facility_transport.csv")
    ]
    province = []
    for row in _read_rows(V8_DIR / "province_residue_supply.csv"):
        if row["residue_type"] != "process_residue":
            continue
        province.append(
            {
                "scenario": PRIMARY_CASE,
                "assignment": "v10_node_transport_aware",
                "province": row["province"],
                "process_residue_odt": row["residue_odt"],
                "process_residue_pj": row["residue_pj"],
                "facility_count": row["facility_count"],
                "facility_count_positive_residue": row[
                    "facility_count_positive_residue"
                ],
                "origin_process_residue_odt": row["origin_process_residue_odt"],
                "origin_process_residue_pj": row["origin_process_residue_pj"],
                "published_residue_share": row["published_residue_share"],
                "proxy_residue_share": row["proxy_residue_share"],
                "cross_border_residue_odt": "",
                "cross_border_residue_share": "",
                "top1_facility_residue_share": row[
                    "top1_facility_residue_share"
                ],
                "top5_facility_residue_share": "",
                "hhi_facility_residue": row["hhi_facility_residue"],
                "evidence_label": "model_estimate",
                "observed": "false",
                "policy_grade": "false",
            }
        )
    return residue, transport, province


def build() -> dict[str, Any]:
    """Build facility residue v9 for all three assignment cases."""

    _v8._v9.assert_no_unexpected_deletions()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    chain_cases = _chain_cases()
    harvest = _chain_harvest()
    residue_rows: list[dict[str, Any]] = []
    transport_rows: list[dict[str, Any]] = []
    province_rows: list[dict[str, Any]] = []
    national_rows: list[dict[str, Any]] = []
    transport_summary_rows: list[dict[str, Any]] = []
    comparison_rows: list[dict[str, Any]] = []
    case_totals: dict[str, dict[str, float]] = {}
    transport_metrics: dict[tuple[str, str], dict[str, float]] = {}

    v8_residue, v8_transport, v8_province = _copy_v8_119()
    residue_rows.extend(v8_residue)
    transport_rows.extend(v8_transport)
    province_rows.extend(v8_province)

    for case in CASES:
        v10_links = _assignment_links(case)
        v6_links = _v6_links(case)
        for assignment, links in (
            ("v6_centroid_local_first", v6_links),
            ("v10_node_transport_aware", v10_links),
        ):
            allocated, used_links, _ = _allocate(
                case, assignment, links, chain_cases
            )
            if assignment == "v6_centroid_local_first" and case == PRIMARY_CASE:
                # The v8 artifact is the frozen 119 v10 result; the computed
                # v6 baseline is retained only for the comparison table.
                pass
            if assignment == "v10_node_transport_aware" and case != PRIMARY_CASE:
                residue_rows.extend(allocated)
            p_rows = _province_rows(
                case, assignment, allocated, used_links
            )
            n_rows, totals = _national_rows(
                case, assignment, allocated, harvest[case]
            )
            t_rows, t_metrics = _transport_summary(
                case, assignment, used_links
            )
            transport_metrics[(case, assignment)] = t_metrics
            if assignment == "v10_node_transport_aware":
                if case != PRIMARY_CASE:
                    province_rows.extend(p_rows)
                national_rows.extend(n_rows)
                case_totals[case] = totals
            else:
                v6_totals = totals
                comparison_metrics = (
                    ("process_residue_odt", "odt"),
                    ("process_residue_pj", "PJ"),
                    ("harvest_residue_gross_pj", "PJ"),
                    ("harvest_residue_recoverable_pj", "PJ"),
                    ("transport_cost_cad", "CAD/yr"),
                    ("mean_road_distance_km", "km"),
                    ("share_ge_150_km", "share"),
                    ("facilities_with_positive_residue", "count"),
                    ("hhi_process_residue", "index_0_1"),
                    ("proxy_residue_share", "share"),
                )
                for metric, unit in comparison_metrics:
                    if metric == "transport_cost_cad":
                        v6_value = t_metrics["transport_cost_cad"]
                    elif metric == "mean_road_distance_km":
                        v6_value = t_metrics["mean_road_distance_km"]
                    elif metric == "share_ge_150_km":
                        v6_value = t_metrics["share_ge_150_km"]
                    else:
                        v6_value = v6_totals[metric]
                    comparison_rows.append(
                        {
                            "scenario": case,
                            "metric": metric,
                            "unit": unit,
                            "v6_value": _fmt(v6_value),
                            "v10_value": "",
                            "delta": "",
                            "delta_pct": "",
                            "note": "v6 local-first baseline",
                            "evidence_label": "model_estimate",
                            "observed": "false",
                            "policy_grade": "false",
                            "_v6_value": v6_value,
                        }
                    )
            if assignment == "v10_node_transport_aware":
                transport_summary_rows.extend(t_rows)
                if case != PRIMARY_CASE:
                    transport_rows.extend(
                        _facility_transport_rows(case, assignment, used_links)
                    )

    # The 119 Mm3 national totals are the frozen v8 values.
    v8_national = json.loads(
        (V8_DIR / "summary.json").read_text(encoding="utf-8")
    )["national_totals"]
    case_totals[PRIMARY_CASE] = {
        key: float(value)
        for key, value in v8_national.items()
        if isinstance(value, (int, float))
    }

    # Fill the v10 side of the comparison table.
    final_comparison: list[dict[str, Any]] = []
    for row in comparison_rows:
        if "v10_value" not in row:
            continue
        metric = row["metric"]
        case = row["scenario"]
        v10_metrics = transport_metrics[(case, "v10_node_transport_aware")]
        v10_totals = case_totals[case]
        if metric == "transport_cost_cad":
            v10_value = v10_metrics["transport_cost_cad"]
        elif metric == "mean_road_distance_km":
            v10_value = v10_metrics["mean_road_distance_km"]
        elif metric == "share_ge_150_km":
            v10_value = v10_metrics["share_ge_150_km"]
        else:
            v10_value = v10_totals[metric]
        base = float(row["_v6_value"])
        row.pop("_v6_value", None)
        row["v10_value"] = _fmt(v10_value)
        row["delta"] = _fmt(v10_value - base)
        row["delta_pct"] = (
            _fmt(100.0 * (v10_value - base) / base) if base else "0.000000000"
        )
        row["note"] = (
            "Assignment-v6 local-first versus assignment-v10 node "
            "transport-aware at the same harvest case."
        )
        final_comparison.append(row)

    # Product/residue invariance is verified against chain v7 at each case.
    v7_summary = json.loads(
        (V7_CHAIN_DIR / "summary.json").read_text(encoding="utf-8")
    )
    for case in CASES:
        for metric in (
            "process_residue_odt",
            "process_residue_pj",
            "harvest_residue_gross_pj",
            "harvest_residue_recoverable_pj",
        ):
            v6_value = float(v7_summary["scenarios"][case][metric])
            v10_value = float(case_totals[case][metric])
            if abs(v6_value - v10_value) > 1e-6:
                raise FacilityResidueSupplyV9Error(
                    f"{case}/{metric}: residue is not invariant"
                )

    _write_csv(
        OUTPUT_DIR / "facility_residue_supply.csv",
        FACILITY_FIELDS,
        residue_rows,
    )
    _write_csv(
        OUTPUT_DIR / "facility_transport.csv", TRANSPORT_FIELDS, transport_rows
    )
    _write_csv(
        OUTPUT_DIR / "province_residue_supply.csv",
        PROVINCE_FIELDS,
        province_rows,
    )
    _write_csv(
        OUTPUT_DIR / "national_residue_summary.csv",
        NATIONAL_FIELDS,
        national_rows,
    )
    _write_csv(
        OUTPUT_DIR / "transport_cost_summary.csv",
        TRANSPORT_SUMMARY_FIELDS,
        transport_summary_rows,
    )
    _write_csv(
        OUTPUT_DIR / "v6_v10_case_comparison.csv",
        COMPARISON_FIELDS,
        final_comparison,
    )

    for filename in (
        "facility_residue_supply.csv",
        "facility_transport.csv",
        "province_residue_supply.csv",
        "national_residue_summary.csv",
        "transport_cost_summary.csv",
        "v6_v10_case_comparison.csv",
    ):
        pass

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
            "baseline": "v6_centroid_local_first",
        },
        "national_totals": case_totals,
        "transport": {
            case: transport_metrics[(case, "v10_node_transport_aware")]
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
        "comparison_csv": "v6_v10_case_comparison.csv",
        "caveats": [
            "Process residue is facility-assigned; harvest residue is origin-province only.",
            "The 100/150 Mm3 facility residue rows use assignment v10 at the same harvest case.",
            "The 119 Mm3 facility residue rows are inherited unchanged from frozen facility_residue_supply_v8.",
            "Transport cost is trucking only and is not part of official OSeCAN tiers.",
            "All values are model estimates; observed=false and policy_grade=false.",
        ],
        "reproduce": (
            ".venv/bin/python -m "
            "workflows.national.facilities.facility_residue_supply_v9"
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
        "# Facility Residue Supply v9\n\n"
        "Assignment v10 node transport-aware facility residue for the "
        "100/119/150 Mm3 cases. The 119 Mm3 rows are invariant from frozen "
        "v8; 100/150 Mm3 are extended with the same class-matching rule. "
        "Harvest residue remains origin-province only. Transport cost is "
        "trucking-only and is not an OSeCAN tier input. Labels: "
        "model_estimate / observed=false / policy_grade=false.\n"
    )
    (OUTPUT_DIR / REPORT_NAME).write_text(report, encoding="utf-8")
    # Refresh the manifest after writing the report.
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
                "process_residue_pj": {
                    case: summary["national_totals"][case]["process_residue_pj"]
                    for case in CASES
                },
                "transport_cost_cad": {
                    case: summary["transport"][case]["transport_cost_cad"]
                    for case in CASES
                },
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
