"""Node-level transport-aware national facility assignment v10.

V9 assigned one provincial harvest centroid to facilities.  V10 keeps the
same capacity v3 facility set, the same 1.2x provincial capacity floor, the
same 118.977 Mm3 harvest total, and the same transport cost v1 curve, but
replaces each provincial centroid with the 525 committed RSYC supply nodes
from ``supply_node_distance_v1``.  It solves a bipartite transportation linear
program from supply nodes to eligible facilities:

* every positive-volume node supply is an equality;
* every facility capacity is a finite upper bound;
* the objective is modelled truck transport cost plus the v9 500 km
  cross-border preference penalty converted to 2026 CAD/m3;
* node-facility distance is haversine x the facility-province road factor from
  the committed ``facility_road_distance_audit_v2``; no full OSRM routing is
  run.

The artifact is a screening rerun only.  Products and residue are unchanged,
capacity and floor definitions are unchanged, no greenfield siting is
introduced, and no policy or official OSeCAN claim is made.

Labels: ``model_estimate`` / ``observed=false`` / ``policy_grade=false``.  A
``D`` in either the parent or data repository status halts the build.
"""

from __future__ import annotations

import csv
import json
import math
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from scipy.optimize import linprog
from scipy.sparse import csr_matrix

from workflows.national.facilities import (
    national_facility_assignment_v9 as v9,
)
from workflows.national.facilities import transport_cost_v1 as transport
from workflows.national.facilities.national_facility_assignment import haversine_km

REPO_ROOT = v9.REPO_ROOT
DATA_ROOT = v9.DATA_ROOT
V9_DIR = v9.OUTPUT_DIR
V9_ASSIGNMENT_CSV = V9_DIR / "v9_facility_assignment.csv"
V9_SUMMARY_JSON = V9_DIR / "summary.json"
SUPPLY_NODE_DIR = (
    DATA_ROOT / "derived/national/facilities/supply_node_distance_v1"
)
SUPPLY_NODES_CSV = SUPPLY_NODE_DIR / "supply_nodes.csv"
SUPPLY_NODE_SUMMARY_JSON = SUPPLY_NODE_DIR / "summary.json"
SUPPLY_NODE_BANDS_CSV = SUPPLY_NODE_DIR / "band_comparison.csv"
CAPACITY_CSV = v9.CAPACITY_CSV
HARVEST_CSV = v9.HARVEST_CSV
BOUNDARY_GEOJSON = v9.BOUNDARY_GEOJSON
FACTORS_CSV = v9.FACTORS_CSV
TRANSPORT_PARAMETERS_CSV = v9.TRANSPORT_PARAMETERS_CSV
TRANSPORT_CURVES_CSV = v9.TRANSPORT_CURVES_CSV
OUTPUT_DIR = (
    DATA_ROOT / "derived/national/facilities/national_facility_assignment_v10"
)

ARTIFACT = "national_facility_assignment_v10"
ARTIFACT_VERSION = "10.0.0"
SCENARIO = "node_transport_aware"
REFERENCE_SCENARIO = "v9_node_fixed"
BASE_COST_SCENARIO = v9.BASE_COST_SCENARIO
PAYLOAD_M3 = v9.PAYLOAD_M3
ENERGY_GJ_PER_M3 = v9.ENERGY_GJ_PER_M3
LOCAL_PENALTY_KM = v9.LOCAL_PENALTY_KM
TIE_BREAK_CAD_M3 = v9.TIE_BREAK_CAD_M3
VOLUME_EPSILON_M3 = v9.VOLUME_EPSILON_M3
VOLUME_CONSERVATION_TOLERANCE_M3 = 1e-3
CAPACITY_TOLERANCE_M3 = 1e-3

EVIDENCE_LABEL = v9.EVIDENCE_LABEL
OBSERVED = v9.OBSERVED
POLICY_GRADE = v9.POLICY_GRADE

V10_ASSIGNMENT_FIELDS = [
    "scenario",
    "node_id",
    "node_province",
    "node_lat",
    "node_lon",
    "node_weight",
    "node_harvest_m3",
    "facility_id",
    "facility_name",
    "facility_class",
    "facility_province",
    "cross_border",
    "allocation_phase",
    "capacity_status",
    "capacity_m3_y",
    "proxy_low_m3_y",
    "proxy_high_m3_y",
    "capacity_evidence",
    "floor_scale_factor",
    "assigned_m3",
    "facility_assigned_m3",
    "facility_remaining_m3",
    "facility_utilization",
    "distance_km",
    "road_distance_km",
    "road_factor_applied",
    "road_factor_basis",
    "local_penalty_km",
    "local_penalty_cad_m3",
    "transport_payload_m3",
    "transport_cost_scenario",
    "transport_cost_cad_m3",
    "transport_cost_cad_m3_low",
    "transport_cost_cad_m3_high",
    "transport_cost_cad",
    "transport_cost_cad_low",
    "transport_cost_cad_high",
    "transport_energy_gj",
    "transport_cost_cad_gj",
    "transport_cost_cad_gj_low",
    "transport_cost_cad_gj_high",
    "local_preference_penalty_cad",
    "evidence_label",
    "observed",
    "policy_grade",
]
V9_FIXED_FIELDS = [
    "scenario",
    "reference",
    "node_id",
    "node_province",
    "node_lat",
    "node_lon",
    "node_weight",
    "node_harvest_m3",
    "facility_id",
    "facility_name",
    "facility_class",
    "facility_province",
    "cross_border",
    "allocation_phase",
    "capacity_status",
    "capacity_m3_y",
    "assigned_m3",
    "distance_km",
    "road_distance_km",
    "road_factor_applied",
    "transport_cost_cad_m3",
    "transport_cost_cad",
    "transport_energy_gj",
    "transport_cost_cad_gj",
    "evidence_label",
    "observed",
    "policy_grade",
]
FACILITY_FIELDS = [
    "scenario",
    "facility_id",
    "facility_name",
    "facility_class",
    "facility_province",
    "capacity_status",
    "capacity_m3_y",
    "proxy_low_m3_y",
    "proxy_high_m3_y",
    "capacity_evidence",
    "floor_scale_factor",
    "assigned_m3",
    "remaining_m3",
    "utilization",
    "binding",
    "used",
    "node_count",
    "local_assigned_m3",
    "cross_border_assigned_m3",
    "mean_road_distance_km",
    "max_road_distance_km",
    "transport_cost_cad",
    "transport_cost_cad_m3",
    "transport_cost_cad_gj",
    "evidence_label",
    "observed",
    "policy_grade",
]
PROVINCE_FIELDS = [
    "scenario",
    "province",
    "node_count",
    "node_harvest_m3",
    "offer_m3",
    "assigned_m3",
    "local_m3",
    "cross_border_m3",
    "local_share",
    "cross_border_share",
    "published_m3",
    "proxy_m3",
    "proxy_share_of_assigned",
    "facilities_used",
    "capacity_m3_y",
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
PROVINCE_COMPARISON_FIELDS = [
    "province",
    "node_count",
    "node_harvest_m3",
    "v9_node_fixed_assigned_m3",
    "v10_assigned_m3",
    "v9_node_fixed_mean_road_distance_km",
    "v10_mean_road_distance_km",
    "delta_mean_road_distance_km",
    "v9_node_fixed_share_ge_150_km",
    "v10_share_ge_150_km",
    "v9_node_fixed_share_ge_300_km",
    "v10_share_ge_300_km",
    "v9_node_fixed_transport_cost_cad",
    "v10_transport_cost_cad",
    "delta_transport_cost_cad",
    "v9_node_fixed_facilities_used",
    "v10_facilities_used",
    "v9_node_fixed_proxy_share_of_assigned",
    "v10_proxy_share_of_assigned",
    "v9_node_fixed_cross_border_m3",
    "v10_cross_border_m3",
    "evidence_label",
    "observed",
    "policy_grade",
]
COMPARISON_FIELDS = [
    "metric",
    "unit",
    "v9_centroid_value",
    "v9_node_fixed_value",
    "v10_value",
    "delta_v10_minus_v9_centroid",
    "delta_pct_vs_v9_centroid",
    "delta_v10_minus_v9_node_fixed",
    "delta_pct_vs_v9_node_fixed",
    "note",
]
DISTANCE_FIELDS = [
    "scope",
    "status",
    "n_origins",
    "n_facilities",
    "assignment_rows",
    "assigned_m3",
    "mean_km",
    "median_km",
    "p90_km",
    "max_km",
    "share_ge_150_km",
    "share_ge_300_km",
    "transport_cost_cad",
    "transport_cost_cad_m3",
    "note",
    "evidence_label",
    "observed",
    "policy_grade",
]
LOCAL_PREFERENCE_FIELDS = [
    "case",
    "local_penalty_km",
    "local_penalty_cad_m3",
    "assigned_m3",
    "local_m3",
    "cross_border_m3",
    "cross_border_share",
    "mean_road_distance_km",
    "share_ge_150_km",
    "share_ge_300_km",
    "transport_cost_cad",
    "transport_cost_cad_m3",
    "transport_cost_cad_gj",
    "penalty_cost_cad",
    "objective_cad",
    "facilities_used",
    "proxy_share_of_assigned",
    "status",
    "note",
]


class AssignmentV10Error(v9.AssignmentV9Error):
    """Raised when a node-level assignment invariant fails."""


@dataclass(frozen=True, slots=True)
class SupplyNode:
    node_id: str
    province: str
    lat: float
    lon: float
    harvest_m3: float
    weight: float


@dataclass(frozen=True, slots=True)
class CostLine:
    intercept_cad_m3: float
    slope_cad_m3_km: float
    penalty_cad_m3: float


# ---------------------------------------------------------------------------
# I/O helpers.
# ---------------------------------------------------------------------------

def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _write_csv(
    path: Path, fields: Sequence[str], rows: Sequence[Mapping[str, Any]]
) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields), lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _format_float(value: float | None, digits: int = 9) -> str:
    if value is None:
        return ""
    if not math.isfinite(value):
        raise AssignmentV10Error(f"refusing to write non-finite value {value!r}")
    return f"{value:.{digits}f}"


def _bool_text(value: bool) -> str:
    return "true" if value else "false"


def _weighted_mean(
    rows: Sequence[Mapping[str, Any]],
    value_field: str,
    weight_field: str = "assigned_m3",
) -> float:
    weight = math.fsum(float(row[weight_field]) for row in rows)
    if weight <= 0.0:
        return 0.0
    return math.fsum(
        float(row[weight_field]) * float(row[value_field]) for row in rows
    ) / weight


def _binding_tolerance(capacity_m3_y: float) -> float:
    """Allow for LP and split-volume round-off when flagging a full facility."""

    return max(VOLUME_EPSILON_M3, 2e-9 * capacity_m3_y)


def _is_binding(assigned_m3: float, capacity_m3_y: float) -> bool:
    return capacity_m3_y - assigned_m3 <= _binding_tolerance(capacity_m3_y)


def _weighted_quantile(
    rows: Sequence[Mapping[str, Any]], quantile: float
) -> float:
    if not 0.0 <= quantile <= 1.0:
        raise AssignmentV10Error(f"quantile must be in [0, 1]; got {quantile!r}")
    pairs = sorted(
        (float(row["road_distance_km"]), float(row["assigned_m3"]))
        for row in rows
        if float(row["assigned_m3"]) > 0.0
    )
    total = math.fsum(weight for _, weight in pairs)
    if total <= 0.0:
        return 0.0
    target = quantile * total
    cumulative = 0.0
    for distance, weight in pairs:
        cumulative += weight
        if cumulative >= target:
            return distance
    return pairs[-1][0]


# ---------------------------------------------------------------------------
# Loading and parsing.
# ---------------------------------------------------------------------------

def load_nodes(path: Path = SUPPLY_NODES_CSV) -> tuple[SupplyNode, ...]:
    if not path.is_file():
        raise AssignmentV10Error(f"supply nodes are missing: {path}")
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    required = {
        "node_id",
        "province",
        "lat",
        "lon",
        "harvest_m3",
        "weight",
        "evidence_label",
        "observed",
        "policy_grade",
    }
    missing = required - set(rows[0] if rows else {})
    if missing:
        raise AssignmentV10Error(f"{path}: missing columns {sorted(missing)}")
    nodes: list[SupplyNode] = []
    seen: set[str] = set()
    for position, row in enumerate(rows, start=2):
        context = f"{path}:{position}"
        node_id = row["node_id"].strip()
        if not node_id:
            raise AssignmentV10Error(f"{context}: blank node_id")
        if node_id in seen:
            raise AssignmentV10Error(f"{context}: duplicate node_id {node_id!r}")
        seen.add(node_id)
        province = row["province"].strip().upper()
        if not province:
            raise AssignmentV10Error(f"{context}: blank province")
        lat = float(row["lat"])
        lon = float(row["lon"])
        harvest = float(row["harvest_m3"])
        weight = float(row["weight"])
        if not math.isfinite(lat) or not -90.0 <= lat <= 90.0:
            raise AssignmentV10Error(f"{context}: invalid latitude {lat!r}")
        if not math.isfinite(lon) or not -180.0 <= lon <= 180.0:
            raise AssignmentV10Error(f"{context}: invalid longitude {lon!r}")
        if not math.isfinite(harvest) or harvest < 0.0:
            raise AssignmentV10Error(f"{context}: invalid harvest {harvest!r}")
        if not math.isfinite(weight) or weight < 0.0:
            raise AssignmentV10Error(f"{context}: invalid weight {weight!r}")
        if row["evidence_label"] != EVIDENCE_LABEL:
            raise AssignmentV10Error(f"{context}: unexpected evidence label")
        if row["observed"] != _bool_text(OBSERVED):
            raise AssignmentV10Error(f"{context}: unexpected observed flag")
        if row["policy_grade"] != _bool_text(POLICY_GRADE):
            raise AssignmentV10Error(f"{context}: unexpected policy grade")
        nodes.append(
            SupplyNode(
                node_id=node_id,
                province=province,
                lat=lat,
                lon=lon,
                harvest_m3=harvest,
                weight=weight,
            )
        )
    if not nodes:
        raise AssignmentV10Error(f"{path}: no supply nodes")
    return tuple(nodes)


def load_v9_rows(path: Path = V9_ASSIGNMENT_CSV) -> list[dict[str, str]]:
    rows = v9._read_csv(path, "assignment v9")
    required = {
        "scenario",
        "province",
        "province_offer_m3",
        "facility_id",
        "facility_name",
        "facility_class",
        "facility_province",
        "cross_border",
        "allocation_phase",
        "capacity_status",
        "capacity_m3_y",
        "assigned_m3",
        "distance_km",
        "road_distance_km",
        "transport_cost_cad",
        "transport_energy_gj",
        "transport_cost_cad_gj",
    }
    missing = required - set(rows[0])
    if missing:
        raise AssignmentV10Error(f"{path}: missing columns {sorted(missing)}")
    primary = [row for row in rows if row["scenario"] == v9.PRIMARY_SCENARIO]
    if not primary:
        raise AssignmentV10Error(f"{path}: no v9 primary-scenario rows")
    return primary


def _load_v9_reference() -> dict[str, Any]:
    with V9_SUMMARY_JSON.open("r", encoding="utf-8") as handle:
        summary = json.load(handle)
    if summary.get("artifact") != "national_facility_assignment_v9":
        raise AssignmentV10Error(f"{V9_SUMMARY_JSON}: expected assignment v9")
    if not summary.get("validation", {}).get("all_passed"):
        raise AssignmentV10Error("assignment v9 did not pass validation")
    return summary


def _load_supply_node_reference() -> dict[str, Any]:
    with SUPPLY_NODE_SUMMARY_JSON.open("r", encoding="utf-8") as handle:
        summary = json.load(handle)
    if summary.get("artifact") != "supply_node_distance_v1":
        raise AssignmentV10Error(f"{SUPPLY_NODE_SUMMARY_JSON}: unexpected artifact")
    if not summary.get("validation", {}).get("all_passed"):
        raise AssignmentV10Error("supply node distance v1 did not pass validation")
    return summary


def _cost_line(parameters: Mapping[str, transport.Parameter]) -> CostLine:
    v9._validate_curve_parity(parameters)
    zero = transport.trip_cost(
        0.0, PAYLOAD_M3, dict(parameters), BASE_COST_SCENARIO
    )["cost_per_m3_cad"]
    unit = transport.trip_cost(
        1.0, PAYLOAD_M3, dict(parameters), BASE_COST_SCENARIO
    )["cost_per_m3_cad"]
    penalty = transport.trip_cost(
        LOCAL_PENALTY_KM, PAYLOAD_M3, dict(parameters), BASE_COST_SCENARIO
    )["cost_per_m3_cad"]
    if not all(math.isfinite(value) and value >= 0.0 for value in (zero, unit, penalty)):
        raise AssignmentV10Error("invalid transport cost line")
    slope = unit - zero
    if slope <= 0.0:
        raise AssignmentV10Error("transport cost slope must be positive")
    for distance in (50.0, 300.0, 677.0, 1500.0):
        expected = transport.trip_cost(
            distance, PAYLOAD_M3, dict(parameters), BASE_COST_SCENARIO
        )["cost_per_m3_cad"]
        actual = zero + slope * distance
        if not math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-9):
            raise AssignmentV10Error(
                f"transport cost line mismatch at {distance} km"
            )
    return CostLine(
        intercept_cad_m3=zero,
        slope_cad_m3_km=slope,
        penalty_cad_m3=penalty,
    )


def _node_facility_distances(
    nodes: Sequence[SupplyNode],
    facilities: Sequence[Any],
    factors: Mapping[str, float],
) -> np.ndarray:
    distances = np.empty((len(nodes), len(facilities)), dtype=float)
    for node_index, node in enumerate(nodes):
        for facility_index, facility in enumerate(facilities):
            try:
                factor = factors[facility.province]
            except KeyError as error:
                raise AssignmentV10Error(
                    f"{facility.facility_id}: no road factor for "
                    f"{facility.province}"
                ) from error
            distance = haversine_km(
                node.lat,
                node.lon,
                facility.location.lat,
                facility.location.lon,
            ) * factor
            if not math.isfinite(distance) or distance < 0.0:
                raise AssignmentV10Error(
                    f"{node.node_id}->{facility.facility_id}: invalid distance"
                )
            distances[node_index, facility_index] = distance
    return distances


# ---------------------------------------------------------------------------
# Linear program.
# ---------------------------------------------------------------------------

def _solve_transport_lp(
    nodes: Sequence[SupplyNode],
    facilities: Sequence[Any],
    objective: np.ndarray,
) -> np.ndarray:
    node_count = len(nodes)
    facility_count = len(facilities)
    pair_count = node_count * facility_count
    if objective.shape != (pair_count,):
        raise AssignmentV10Error("objective shape does not match node-facility pairs")
    if not np.all(np.isfinite(objective)):
        raise AssignmentV10Error("objective contains non-finite values")
    pair_columns = np.arange(pair_count)
    equality_rows = np.repeat(np.arange(node_count), facility_count)
    a_eq = csr_matrix(
        (np.ones(pair_count), (equality_rows, pair_columns)),
        shape=(node_count, pair_count),
    )
    capacity_rows = np.tile(np.arange(facility_count), node_count)
    a_ub = csr_matrix(
        (np.ones(pair_count), (capacity_rows, pair_columns)),
        shape=(facility_count, pair_count),
    )
    b_eq = np.array([node.harvest_m3 for node in nodes], dtype=float)
    b_ub = np.array(
        [float(facility.capacity.capacity_m3_y) for facility in facilities],
        dtype=float,
    )
    result = linprog(
        objective,
        A_ub=a_ub,
        b_ub=b_ub,
        A_eq=a_eq,
        b_eq=b_eq,
        bounds=(0.0, None),
        method="highs",
        options={"presolve": True},
    )
    if not result.success:
        raise AssignmentV10Error("node transport LP failed: " + str(result.message))
    if result.x is None:
        raise AssignmentV10Error("node transport LP returned no solution")
    solution = np.asarray(result.x, dtype=float)
    if not np.all(np.isfinite(solution)) or float(solution.min()) < -1e-9:
        raise AssignmentV10Error("node transport LP solution is not finite non-negative")
    return solution.reshape(node_count, facility_count)


def _objective(
    distances: np.ndarray,
    cross_border: np.ndarray,
    line: CostLine,
    *,
    include_penalty: bool,
) -> np.ndarray:
    objective = line.intercept_cad_m3 + line.slope_cad_m3_km * distances
    if include_penalty:
        objective = objective + line.penalty_cad_m3 * cross_border
    tie_break = TIE_BREAK_CAD_M3 * np.arange(objective.size).reshape(
        objective.shape
    )
    return (objective + tie_break).ravel()


def _tidy_allocations(
    nodes: Sequence[SupplyNode],
    facilities: Sequence[Any],
    solution: np.ndarray,
) -> list[list[tuple[int, float]]]:
    allocations: list[list[tuple[int, float]]] = []
    for node_index, node in enumerate(nodes):
        row = np.array(solution[node_index], dtype=float)
        total = float(row.sum())
        if total <= 0.0:
            raise AssignmentV10Error(
                f"{node.node_id}: LP produced no allocation for positive supply"
            )
        gap = node.harvest_m3 - total
        if abs(gap) > max(1e-6, 1e-9 * node.harvest_m3):
            raise AssignmentV10Error(
                f"{node.node_id}: conservation gap {gap!r} m3 in LP solution"
            )
        row *= node.harvest_m3 / total
        candidates = [
            (facility_index, float(volume))
            for facility_index, volume in enumerate(row)
            if volume > 1e-9
        ]
        if not candidates:
            raise AssignmentV10Error(f"{node.node_id}: no positive allocation")
        kept = [
            (facility_index, volume)
            for facility_index, volume in candidates
            if volume > VOLUME_EPSILON_M3
        ]
        if not kept:
            kept = [max(candidates, key=lambda item: item[1])]
        dropped = node.harvest_m3 - math.fsum(volume for _, volume in kept)
        if dropped > 0.0:
            target = max(kept, key=lambda item: item[1])
            kept = [
                (
                    facility_index,
                    volume + dropped if facility_index == target[0] else volume,
                )
                for facility_index, volume in kept
            ]
        allocations.append(kept)
    facility_totals = np.zeros(len(facilities), dtype=float)
    for entries in allocations:
        for facility_index, volume in entries:
            facility_totals[facility_index] += volume
    for facility_index, facility in enumerate(facilities):
        capacity = float(facility.capacity.capacity_m3_y)
        if facility_totals[facility_index] > capacity + CAPACITY_TOLERANCE_M3:
            raise AssignmentV10Error(
                f"{facility.facility_id}: LP exceeds capacity by "
                f"{facility_totals[facility_index] - capacity!r} m3"
            )
    return allocations


# ---------------------------------------------------------------------------
# Row construction.
# ---------------------------------------------------------------------------

def _allocation_row(
    *,
    scenario: str,
    node: SupplyNode,
    node_weight: float,
    facility: Any,
    volume_m3: float,
    distance_km: float,
    factor: float,
    line: CostLine,
    parameters: Mapping[str, transport.Parameter],
    facility_assigned_m3: float,
    include_penalty: bool,
) -> dict[str, Any]:
    cost = v9.transport_metrics(distance_km, parameters)
    linear = line.intercept_cad_m3 + line.slope_cad_m3_km * distance_km
    if not math.isclose(linear, cost["cost_m3_base"], rel_tol=1e-12, abs_tol=1e-9):
        raise AssignmentV10Error(
            f"{node.node_id}->{facility.facility_id}: linear cost mismatch"
        )
    capacity = facility.capacity
    cross_border = facility.province != node.province
    energy = volume_m3 * ENERGY_GJ_PER_M3
    total_cost = volume_m3 * cost["cost_m3_base"]
    penalty = (
        volume_m3 * line.penalty_cad_m3 if cross_border and include_penalty else 0.0
    )
    remaining = max(0.0, float(capacity.capacity_m3_y) - facility_assigned_m3)
    return {
        "scenario": scenario,
        "node_id": node.node_id,
        "node_province": node.province,
        "node_lat": _format_float(node.lat),
        "node_lon": _format_float(node.lon),
        "node_weight": _format_float(node_weight, 12),
        "node_harvest_m3": _format_float(node.harvest_m3, 9),
        "facility_id": facility.facility_id,
        "facility_name": facility.name,
        "facility_class": facility.facility_class,
        "facility_province": facility.province,
        "cross_border": _bool_text(cross_border),
        "allocation_phase": "cross_border" if cross_border else "local",
        "capacity_status": capacity.status,
        "capacity_m3_y": _format_float(capacity.capacity_m3_y, 9),
        "proxy_low_m3_y": _format_float(capacity.proxy_low_m3_y, 9),
        "proxy_high_m3_y": _format_float(capacity.proxy_high_m3_y, 9),
        "capacity_evidence": capacity.capacity_evidence,
        "floor_scale_factor": _format_float(capacity.floor_scale_factor, 12),
        "assigned_m3": _format_float(volume_m3, 9),
        "facility_assigned_m3": _format_float(facility_assigned_m3, 9),
        "facility_remaining_m3": _format_float(remaining, 9),
        "facility_utilization": _format_float(
            facility_assigned_m3 / capacity.capacity_m3_y, 9
        ),
        "distance_km": _format_float(distance_km / factor, 9),
        "road_distance_km": _format_float(distance_km, 9),
        "road_factor_applied": _format_float(factor, 12),
        "road_factor_basis": "facility_province",
        "local_penalty_km": _format_float(LOCAL_PENALTY_KM, 3),
        "local_penalty_cad_m3": _format_float(
            line.penalty_cad_m3 if cross_border and include_penalty else 0.0, 9
        ),
        "transport_payload_m3": _format_float(PAYLOAD_M3, 3),
        "transport_cost_scenario": BASE_COST_SCENARIO,
        "transport_cost_cad_m3": _format_float(cost["cost_m3_base"], 9),
        "transport_cost_cad_m3_low": _format_float(cost["cost_m3_low"], 9),
        "transport_cost_cad_m3_high": _format_float(cost["cost_m3_high"], 9),
        "transport_cost_cad": _format_float(total_cost, 6),
        "transport_cost_cad_low": _format_float(
            volume_m3 * cost["cost_m3_low"], 6
        ),
        "transport_cost_cad_high": _format_float(
            volume_m3 * cost["cost_m3_high"], 6
        ),
        "transport_energy_gj": _format_float(energy, 6),
        "transport_cost_cad_gj": _format_float(
            total_cost / energy if energy > 0.0 else 0.0, 9
        ),
        "transport_cost_cad_gj_low": _format_float(cost["cost_gj_low"], 9),
        "transport_cost_cad_gj_high": _format_float(cost["cost_gj_high"], 9),
        "local_preference_penalty_cad": _format_float(penalty, 6),
        "evidence_label": EVIDENCE_LABEL,
        "observed": _bool_text(OBSERVED),
        "policy_grade": _bool_text(POLICY_GRADE),
    }


def _v10_rows(
    nodes: Sequence[SupplyNode],
    facilities: Sequence[Any],
    allocations: Sequence[Sequence[tuple[int, float]]],
    distances: np.ndarray,
    factors: Mapping[str, float],
    parameters: Mapping[str, transport.Parameter],
    line: CostLine,
) -> list[dict[str, Any]]:
    province_harvest: dict[str, float] = defaultdict(float)
    for node in nodes:
        province_harvest[node.province] += node.harvest_m3
    facility_totals = np.zeros(len(facilities), dtype=float)
    for entries in allocations:
        for facility_index, volume in entries:
            facility_totals[facility_index] += volume
    rows: list[dict[str, Any]] = []
    for node_index, node in enumerate(nodes):
        node_weight = node.harvest_m3 / province_harvest[node.province]
        for facility_index, volume in allocations[node_index]:
            facility = facilities[facility_index]
            row = _allocation_row(
                scenario=SCENARIO,
                node=node,
                node_weight=node_weight,
                facility=facility,
                volume_m3=volume,
                distance_km=float(distances[node_index, facility_index]),
                factor=factors[facility.province],
                line=line,
                parameters=parameters,
                facility_assigned_m3=facility_totals[facility_index],
                include_penalty=True,
            )
            rows.append(row)
    rows.sort(key=lambda row: (row["node_province"], row["node_id"], row["facility_id"]))
    return rows


def _v9_fixed_rows(
    nodes: Sequence[SupplyNode],
    facilities: Sequence[Any],
    v9_rows: Sequence[Mapping[str, str]],
    factors: Mapping[str, float],
    parameters: Mapping[str, transport.Parameter],
    line: CostLine,
) -> list[dict[str, Any]]:
    facilities_by_id = {facility.facility_id: facility for facility in facilities}
    nodes_by_province: dict[str, list[SupplyNode]] = defaultdict(list)
    for node in nodes:
        if node.harvest_m3 > 0.0:
            nodes_by_province[node.province].append(node)
    rows: list[dict[str, Any]] = []
    for reference in v9_rows:
        facility = facilities_by_id.get(reference["facility_id"])
        if facility is None:
            raise AssignmentV10Error(
                f"v9 facility {reference['facility_id']!r} is not eligible in v10"
            )
        origin_nodes = nodes_by_province.get(reference["province"], [])
        if not origin_nodes:
            raise AssignmentV10Error(
                f"{reference['province']}: no positive-volume supply nodes"
            )
        assigned = float(reference["assigned_m3"])
        for node in origin_nodes:
            volume = assigned * node.weight
            if volume <= 0.0:
                continue
            metric = _allocation_row(
                scenario=REFERENCE_SCENARIO,
                node=node,
                node_weight=node.weight,
                facility=facility,
                volume_m3=volume,
                distance_km=haversine_km(
                    node.lat,
                    node.lon,
                    facility.location.lat,
                    facility.location.lon,
                )
                * factors[facility.province],
                factor=factors[facility.province],
                line=line,
                parameters=parameters,
                facility_assigned_m3=assigned,
                include_penalty=False,
            )
            row = {
                "scenario": REFERENCE_SCENARIO,
                "reference": "v9 assignment held fixed; origin split by node weight",
                **{
                    key: metric[key]
                    for key in (
                        "node_id",
                        "node_province",
                        "node_lat",
                        "node_lon",
                        "node_weight",
                        "node_harvest_m3",
                        "facility_id",
                        "facility_name",
                        "facility_class",
                        "facility_province",
                        "cross_border",
                        "allocation_phase",
                        "capacity_status",
                        "capacity_m3_y",
                        "assigned_m3",
                        "distance_km",
                        "road_distance_km",
                        "road_factor_applied",
                        "transport_cost_cad_m3",
                        "transport_cost_cad",
                        "transport_energy_gj",
                        "transport_cost_cad_gj",
                        "evidence_label",
                        "observed",
                        "policy_grade",
                    )
                },
            }
            rows.append(row)
    rows.sort(key=lambda row: (row["node_province"], row["node_id"], row["facility_id"]))
    return rows


# ---------------------------------------------------------------------------
# Metrics and aggregation.
# ---------------------------------------------------------------------------

def _metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    total = math.fsum(float(row["assigned_m3"]) for row in rows)
    if total <= 0.0:
        raise AssignmentV10Error("metric rows have no assigned volume")
    local = math.fsum(
        float(row["assigned_m3"])
        for row in rows
        if row["allocation_phase"] == "local"
    )
    cross = total - local
    published = math.fsum(
        float(row["assigned_m3"])
        for row in rows
        if row["capacity_status"] == "published"
    )
    facility_volume: dict[str, float] = defaultdict(float)
    facility_capacity: dict[str, float] = {}
    for row in rows:
        facility_id = str(row["facility_id"])
        facility_volume[facility_id] += float(row["assigned_m3"])
        facility_capacity[facility_id] = float(row["capacity_m3_y"])
    binding = [
        facility_id
        for facility_id, volume in facility_volume.items()
        if _is_binding(volume, facility_capacity[facility_id])
    ]
    cost = math.fsum(float(row["transport_cost_cad"]) for row in rows)
    energy = math.fsum(float(row["transport_energy_gj"]) for row in rows)
    distances = [float(row["road_distance_km"]) for row in rows]
    return {
        "assigned_m3": total,
        "local_m3": local,
        "local_share": local / total,
        "cross_border_m3": cross,
        "cross_border_share": cross / total,
        "published_m3": published,
        "proxy_m3": total - published,
        "proxy_share_of_assigned": (total - published) / total,
        "facilities_used": float(len(facility_volume)),
        "assignments": float(len(rows)),
        "mean_road_distance_km": _weighted_mean(rows, "road_distance_km"),
        "median_road_distance_km": _weighted_quantile(rows, 0.5),
        "p90_road_distance_km": _weighted_quantile(rows, 0.9),
        "max_road_distance_km": max(distances),
        "share_ge_150_km": math.fsum(
            float(row["assigned_m3"])
            for row in rows
            if float(row["road_distance_km"]) >= 150.0
        )
        / total,
        "share_ge_300_km": math.fsum(
            float(row["assigned_m3"])
            for row in rows
            if float(row["road_distance_km"]) >= 300.0
        )
        / total,
        "total_volume_distance_m3_km": math.fsum(
            float(row["assigned_m3"]) * float(row["road_distance_km"])
            for row in rows
        ),
        "transport_cost_cad": cost,
        "transport_cost_cad_m3": cost / total,
        "transport_cost_cad_gj": cost / energy if energy > 0.0 else 0.0,
        "binding_facilities": float(len(binding)),
        "binding_volume_m3": math.fsum(
            facility_volume[facility_id] for facility_id in binding
        ),
    }


def _v9_centroid_metrics(
    v9_rows: Sequence[Mapping[str, str]],
) -> dict[str, float]:
    rows = [
        {
            "assigned_m3": row["assigned_m3"],
            "allocation_phase": row["allocation_phase"],
            "capacity_status": row["capacity_status"],
            "facility_id": row["facility_id"],
            "capacity_m3_y": row["capacity_m3_y"],
            "road_distance_km": row["road_distance_km"],
            "transport_cost_cad": row["transport_cost_cad"],
            "transport_energy_gj": row["transport_energy_gj"],
        }
        for row in v9_rows
    ]
    return _metrics(rows)


def _facility_rows(
    facilities: Sequence[Any],
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row["facility_id"])].append(row)
    result: list[dict[str, Any]] = []
    for facility in sorted(facilities, key=lambda item: item.facility_id):
        group = groups.get(facility.facility_id, [])
        capacity = float(facility.capacity.capacity_m3_y)
        assigned = math.fsum(float(row["assigned_m3"]) for row in group)
        remaining = max(0.0, capacity - assigned)
        cost = math.fsum(float(row["transport_cost_cad"]) for row in group)
        energy = math.fsum(float(row["transport_energy_gj"]) for row in group)
        local = math.fsum(
            float(row["assigned_m3"])
            for row in group
            if row["allocation_phase"] == "local"
        )
        result.append(
            {
                "scenario": SCENARIO,
                "facility_id": facility.facility_id,
                "facility_name": facility.name,
                "facility_class": facility.facility_class,
                "facility_province": facility.province,
                "capacity_status": facility.capacity.status,
                "capacity_m3_y": _format_float(capacity, 9),
                "proxy_low_m3_y": _format_float(
                    facility.capacity.proxy_low_m3_y, 9
                ),
                "proxy_high_m3_y": _format_float(
                    facility.capacity.proxy_high_m3_y, 9
                ),
                "capacity_evidence": facility.capacity.capacity_evidence,
                "floor_scale_factor": _format_float(
                    facility.capacity.floor_scale_factor, 12
                ),
                "assigned_m3": _format_float(assigned, 9),
                "remaining_m3": _format_float(remaining, 9),
                "utilization": _format_float(
                    assigned / capacity if capacity > 0.0 else 0.0, 9
                ),
                "binding": _bool_text(_is_binding(assigned, capacity)),
                "used": _bool_text(assigned > VOLUME_EPSILON_M3),
                "node_count": str(len({row["node_id"] for row in group})),
                "local_assigned_m3": _format_float(local, 9),
                "cross_border_assigned_m3": _format_float(assigned - local, 9),
                "mean_road_distance_km": _format_float(
                    _weighted_mean(group, "road_distance_km")
                    if group
                    else 0.0,
                    9,
                ),
                "max_road_distance_km": _format_float(
                    max(
                        (float(row["road_distance_km"]) for row in group),
                        default=0.0,
                    ),
                    9,
                ),
                "transport_cost_cad": _format_float(cost, 6),
                "transport_cost_cad_m3": _format_float(
                    cost / assigned if assigned > 0.0 else 0.0, 9
                ),
                "transport_cost_cad_gj": _format_float(
                    cost / energy if energy > 0.0 else 0.0, 9
                ),
                "evidence_label": EVIDENCE_LABEL,
                "observed": _bool_text(OBSERVED),
                "policy_grade": _bool_text(POLICY_GRADE),
            }
        )
    return result


def _province_rows(
    nodes: Sequence[SupplyNode],
    facilities: Sequence[Any],
    rows: Sequence[Mapping[str, Any]],
    offers: Mapping[str, float],
) -> list[dict[str, Any]]:
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row["node_province"])].append(row)
    nodes_by_province: dict[str, list[SupplyNode]] = defaultdict(list)
    for node in nodes:
        nodes_by_province[node.province].append(node)
    capacity_by_province: dict[str, float] = defaultdict(float)
    for facility in facilities:
        capacity_by_province[facility.province] += float(
            facility.capacity.capacity_m3_y
        )
    result: list[dict[str, Any]] = []
    for province in sorted(offers):
        if offers[province] <= 0.0:
            continue
        group = groups.get(province, [])
        if not group:
            raise AssignmentV10Error(f"{province}: no v10 assignment rows")
        metrics = _metrics(group)
        result.append(
            {
                "scenario": SCENARIO,
                "province": province,
                "node_count": str(
                    len(
                        [
                            node
                            for node in nodes_by_province[province]
                            if node.harvest_m3 > 0.0
                        ]
                    )
                ),
                "node_harvest_m3": _format_float(
                    math.fsum(
                        node.harvest_m3
                        for node in nodes_by_province[province]
                    ),
                    9,
                ),
                "offer_m3": _format_float(offers[province], 9),
                "assigned_m3": _format_float(metrics["assigned_m3"], 9),
                "local_m3": _format_float(metrics["local_m3"], 9),
                "cross_border_m3": _format_float(
                    metrics["cross_border_m3"], 9
                ),
                "local_share": _format_float(metrics["local_share"], 9),
                "cross_border_share": _format_float(
                    metrics["cross_border_share"], 9
                ),
                "published_m3": _format_float(metrics["published_m3"], 9),
                "proxy_m3": _format_float(metrics["proxy_m3"], 9),
                "proxy_share_of_assigned": _format_float(
                    metrics["proxy_share_of_assigned"], 9
                ),
                "facilities_used": str(int(metrics["facilities_used"])),
                "capacity_m3_y": _format_float(
                    capacity_by_province[province], 9
                ),
                "mean_road_distance_km": _format_float(
                    metrics["mean_road_distance_km"], 9
                ),
                "median_road_distance_km": _format_float(
                    metrics["median_road_distance_km"], 9
                ),
                "p90_road_distance_km": _format_float(
                    metrics["p90_road_distance_km"], 9
                ),
                "max_road_distance_km": _format_float(
                    metrics["max_road_distance_km"], 9
                ),
                "share_ge_150_km": _format_float(metrics["share_ge_150_km"], 9),
                "share_ge_300_km": _format_float(metrics["share_ge_300_km"], 9),
                "transport_cost_cad": _format_float(
                    metrics["transport_cost_cad"], 6
                ),
                "transport_cost_cad_m3": _format_float(
                    metrics["transport_cost_cad_m3"], 9
                ),
                "transport_cost_cad_gj": _format_float(
                    metrics["transport_cost_cad_gj"], 9
                ),
                "binding_facilities": str(int(metrics["binding_facilities"])),
                "binding_volume_m3": _format_float(
                    metrics["binding_volume_m3"], 9
                ),
                "evidence_label": EVIDENCE_LABEL,
                "observed": _bool_text(OBSERVED),
                "policy_grade": _bool_text(POLICY_GRADE),
            }
        )
    return result


def _province_comparison_rows(
    nodes: Sequence[SupplyNode],
    v9_fixed: Sequence[Mapping[str, Any]],
    v10_rows: Sequence[Mapping[str, Any]],
    offers: Mapping[str, float],
) -> list[dict[str, Any]]:
    fixed_groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    v10_groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in v9_fixed:
        fixed_groups[str(row["node_province"])].append(row)
    for row in v10_rows:
        v10_groups[str(row["node_province"])].append(row)
    node_counts: dict[str, int] = defaultdict(int)
    node_harvest: dict[str, float] = defaultdict(float)
    for node in nodes:
        if node.harvest_m3 > 0.0:
            node_counts[node.province] += 1
            node_harvest[node.province] += node.harvest_m3
    result: list[dict[str, Any]] = []
    for province in sorted(offers):
        if offers[province] <= 0.0:
            continue
        fixed = _metrics(fixed_groups[province])
        new = _metrics(v10_groups[province])
        result.append(
            {
                "province": province,
                "node_count": str(node_counts[province]),
                "node_harvest_m3": _format_float(node_harvest[province], 9),
                "v9_node_fixed_assigned_m3": _format_float(
                    fixed["assigned_m3"], 9
                ),
                "v10_assigned_m3": _format_float(new["assigned_m3"], 9),
                "v9_node_fixed_mean_road_distance_km": _format_float(
                    fixed["mean_road_distance_km"], 9
                ),
                "v10_mean_road_distance_km": _format_float(
                    new["mean_road_distance_km"], 9
                ),
                "delta_mean_road_distance_km": _format_float(
                    new["mean_road_distance_km"]
                    - fixed["mean_road_distance_km"],
                    9,
                ),
                "v9_node_fixed_share_ge_150_km": _format_float(
                    fixed["share_ge_150_km"], 9
                ),
                "v10_share_ge_150_km": _format_float(
                    new["share_ge_150_km"], 9
                ),
                "v9_node_fixed_share_ge_300_km": _format_float(
                    fixed["share_ge_300_km"], 9
                ),
                "v10_share_ge_300_km": _format_float(
                    new["share_ge_300_km"], 9
                ),
                "v9_node_fixed_transport_cost_cad": _format_float(
                    fixed["transport_cost_cad"], 6
                ),
                "v10_transport_cost_cad": _format_float(
                    new["transport_cost_cad"], 6
                ),
                "delta_transport_cost_cad": _format_float(
                    new["transport_cost_cad"] - fixed["transport_cost_cad"], 6
                ),
                "v9_node_fixed_facilities_used": str(
                    int(fixed["facilities_used"])
                ),
                "v10_facilities_used": str(int(new["facilities_used"])),
                "v9_node_fixed_proxy_share_of_assigned": _format_float(
                    fixed["proxy_share_of_assigned"], 9
                ),
                "v10_proxy_share_of_assigned": _format_float(
                    new["proxy_share_of_assigned"], 9
                ),
                "v9_node_fixed_cross_border_m3": _format_float(
                    fixed["cross_border_m3"], 9
                ),
                "v10_cross_border_m3": _format_float(
                    new["cross_border_m3"], 9
                ),
                "evidence_label": EVIDENCE_LABEL,
                "observed": _bool_text(OBSERVED),
                "policy_grade": _bool_text(POLICY_GRADE),
            }
        )
    return result


def _comparison_rows(
    v9_centroid: Mapping[str, float],
    v9_fixed: Mapping[str, float],
    v10_metrics: Mapping[str, float],
) -> list[dict[str, Any]]:
    definitions = (
        ("assigned_m3", "m3/yr", "assigned volume"),
        ("local_m3", "m3/yr", "volume served inside the origin province"),
        ("local_share", "share", "local / assigned"),
        ("cross_border_m3", "m3/yr", "volume served out of province"),
        ("cross_border_share", "share", "cross-border / assigned"),
        ("published_m3", "m3/yr", "volume on published capacity"),
        ("proxy_m3", "m3/yr", "volume on proxy capacity"),
        ("proxy_share_of_assigned", "share", "proxy / assigned"),
        ("facilities_used", "count", "distinct facilities receiving volume"),
        ("assignments", "count", "assignment rows"),
        ("mean_road_distance_km", "km", "assigned-volume-weighted mean distance"),
        ("median_road_distance_km", "km", "assigned-volume-weighted median"),
        ("p90_road_distance_km", "km", "assigned-volume-weighted p90"),
        ("max_road_distance_km", "km", "maximum assignment-row distance"),
        ("share_ge_150_km", "share", "assigned volume at or above 150 km"),
        ("share_ge_300_km", "share", "assigned volume at or above 300 km"),
        ("total_volume_distance_m3_km", "m3*km/yr", "volume times distance"),
        ("transport_cost_cad", "CAD/yr", "modelled truck transport cost"),
        ("transport_cost_cad_m3", "CAD/m3", "transport cost per assigned m3"),
        ("transport_cost_cad_gj", "CAD/GJ", "transport cost per delivered GJ"),
        ("binding_facilities", "count", "facilities at capacity"),
        ("binding_volume_m3", "m3/yr", "volume at binding facilities"),
    )
    result: list[dict[str, Any]] = []
    for metric, unit, note in definitions:
        centroid = v9_centroid[metric]
        fixed = v9_fixed[metric]
        new = v10_metrics[metric]
        result.append(
            {
                "metric": metric,
                "unit": unit,
                "v9_centroid_value": _format_float(centroid, 9),
                "v9_node_fixed_value": _format_float(fixed, 9),
                "v10_value": _format_float(new, 9),
                "delta_v10_minus_v9_centroid": _format_float(new - centroid, 9),
                "delta_pct_vs_v9_centroid": (
                    _format_float(100.0 * (new - centroid) / centroid, 9)
                    if abs(centroid) > 1e-12
                    else ""
                ),
                "delta_v10_minus_v9_node_fixed": _format_float(new - fixed, 9),
                "delta_pct_vs_v9_node_fixed": (
                    _format_float(100.0 * (new - fixed) / fixed, 9)
                    if abs(fixed) > 1e-12
                    else ""
                ),
                "note": note,
            }
        )
    return result


def _distance_rows(
    v9_centroid: Mapping[str, float],
    v9_fixed: Mapping[str, float],
    v10_metrics: Mapping[str, float],
    bound: Mapping[str, float],
) -> list[dict[str, Any]]:
    definitions = (
        (
            "v9_centroid",
            "reference",
            v9_centroid,
            12,
            "v9 assignment evaluated at province-centroid origins",
        ),
        (
            "v9_node_fixed",
            "reference",
            v9_fixed,
            486,
            "v9 facility allocation held fixed; origin volume split by node weight",
        ),
        (
            "v10_node_lp",
            "model_assignment",
            v10_metrics,
            486,
            "node-level transport-aware LP assignment",
        ),
    )
    result: list[dict[str, Any]] = []
    for scope, status, metrics, n_origins, note in definitions:
        result.append(
            {
                "scope": scope,
                "status": status,
                "n_origins": str(n_origins),
                "n_facilities": str(int(metrics["facilities_used"])),
                "assignment_rows": str(int(metrics["assignments"])),
                "assigned_m3": _format_float(metrics["assigned_m3"], 9),
                "mean_km": _format_float(metrics["mean_road_distance_km"], 9),
                "median_km": _format_float(
                    metrics["median_road_distance_km"], 9
                ),
                "p90_km": _format_float(metrics["p90_road_distance_km"], 9),
                "max_km": _format_float(metrics["max_road_distance_km"], 9),
                "share_ge_150_km": _format_float(metrics["share_ge_150_km"], 9),
                "share_ge_300_km": _format_float(metrics["share_ge_300_km"], 9),
                "transport_cost_cad": _format_float(
                    metrics["transport_cost_cad"], 6
                ),
                "transport_cost_cad_m3": _format_float(
                    metrics["transport_cost_cad_m3"], 9
                ),
                "note": note,
                "evidence_label": EVIDENCE_LABEL,
                "observed": _bool_text(OBSERVED),
                "policy_grade": _bool_text(POLICY_GRADE),
            }
        )
    result.append(
        {
            "scope": "capacity_constrained_distance_bound",
            "status": "diagnostic_lower_bound",
            "n_origins": "486",
            "n_facilities": str(int(bound["facilities_used"])),
            "assignment_rows": "",
            "assigned_m3": _format_float(bound["assigned_m3"], 9),
            "mean_km": _format_float(bound["mean_km"], 9),
            "median_km": "",
            "p90_km": "",
            "max_km": _format_float(bound["max_km"], 9),
            "share_ge_150_km": _format_float(bound["share_ge_150_km"], 9),
            "share_ge_300_km": _format_float(bound["share_ge_300_km"], 9),
            "transport_cost_cad": "",
            "transport_cost_cad_m3": "",
            "note": (
                "minimum volume-distance LP with no local preference penalty; "
                "capacity v3 and node supplies unchanged"
            ),
            "evidence_label": EVIDENCE_LABEL,
            "observed": _bool_text(OBSERVED),
            "policy_grade": _bool_text(POLICY_GRADE),
        }
    )
    return result


def _distance_bound(
    nodes: Sequence[SupplyNode],
    facilities: Sequence[Any],
    distances: np.ndarray,
    solution: np.ndarray,
) -> dict[str, Any]:
    total = math.fsum(node.harvest_m3 for node in nodes)
    facility_volume = solution.sum(axis=0)
    cross = math.fsum(
        solution[node_index, facility_index]
        for node_index in range(len(nodes))
        for facility_index, facility in enumerate(facilities)
        if facility.province != nodes[node_index].province
    )
    return {
        "assigned_m3": total,
        "mean_km": float((solution * distances).sum()) / total,
        "max_km": float(distances[solution > 1e-9].max()),
        "share_ge_150_km": float(solution[distances >= 150.0].sum()) / total,
        "share_ge_300_km": float(solution[distances >= 300.0].sum()) / total,
        "cross_border_m3": cross,
        "cross_border_share": cross / total,
        "facilities_used": int((facility_volume > 1e-6).sum()),
        "nearest_facility_weighted_mean_km": float(
            (
                distances.min(axis=1)
                * np.array([node.harvest_m3 for node in nodes])
            ).sum()
            / total
        ),
        "nearest_facility_max_km": float(distances.min(axis=1).max()),
    }


def _local_preference_rows(
    line: CostLine,
    v10_metrics: Mapping[str, float],
    bound: Mapping[str, float],
    v9_centroid: Mapping[str, float],
    v9_fixed: Mapping[str, float],
) -> list[dict[str, Any]]:
    return [
        {
            "case": "v9_centroid",
            "local_penalty_km": _format_float(LOCAL_PENALTY_KM, 3),
            "local_penalty_cad_m3": _format_float(line.penalty_cad_m3, 9),
            "assigned_m3": _format_float(v9_centroid["assigned_m3"], 9),
            "local_m3": _format_float(v9_centroid["local_m3"], 9),
            "cross_border_m3": _format_float(
                v9_centroid["cross_border_m3"], 9
            ),
            "cross_border_share": _format_float(
                v9_centroid["cross_border_share"], 9
            ),
            "mean_road_distance_km": _format_float(
                v9_centroid["mean_road_distance_km"], 9
            ),
            "share_ge_150_km": _format_float(v9_centroid["share_ge_150_km"], 9),
            "share_ge_300_km": _format_float(v9_centroid["share_ge_300_km"], 9),
            "transport_cost_cad": _format_float(
                v9_centroid["transport_cost_cad"], 6
            ),
            "transport_cost_cad_m3": _format_float(
                v9_centroid["transport_cost_cad_m3"], 9
            ),
            "transport_cost_cad_gj": _format_float(
                v9_centroid["transport_cost_cad_gj"], 9
            ),
            "penalty_cost_cad": "",
            "objective_cad": "",
            "facilities_used": str(int(v9_centroid["facilities_used"])),
            "proxy_share_of_assigned": _format_float(
                v9_centroid["proxy_share_of_assigned"], 9
            ),
            "status": "reference",
            "note": "v9 centroid assignment",
        },
        {
            "case": "v9_node_fixed",
            "local_penalty_km": _format_float(LOCAL_PENALTY_KM, 3),
            "local_penalty_cad_m3": _format_float(line.penalty_cad_m3, 9),
            "assigned_m3": _format_float(v9_fixed["assigned_m3"], 9),
            "local_m3": _format_float(v9_fixed["local_m3"], 9),
            "cross_border_m3": _format_float(v9_fixed["cross_border_m3"], 9),
            "cross_border_share": _format_float(
                v9_fixed["cross_border_share"], 9
            ),
            "mean_road_distance_km": _format_float(
                v9_fixed["mean_road_distance_km"], 9
            ),
            "share_ge_150_km": _format_float(v9_fixed["share_ge_150_km"], 9),
            "share_ge_300_km": _format_float(v9_fixed["share_ge_300_km"], 9),
            "transport_cost_cad": _format_float(
                v9_fixed["transport_cost_cad"], 6
            ),
            "transport_cost_cad_m3": _format_float(
                v9_fixed["transport_cost_cad_m3"], 9
            ),
            "transport_cost_cad_gj": _format_float(
                v9_fixed["transport_cost_cad_gj"], 9
            ),
            "penalty_cost_cad": "",
            "objective_cad": "",
            "facilities_used": str(int(v9_fixed["facilities_used"])),
            "proxy_share_of_assigned": _format_float(
                v9_fixed["proxy_share_of_assigned"], 9
            ),
            "status": "reference",
            "note": "v9 assignment at node origins",
        },
        {
            "case": "distance_only_bound",
            "local_penalty_km": "0.000",
            "local_penalty_cad_m3": "0.000000000",
            "assigned_m3": _format_float(bound["assigned_m3"], 9),
            "local_m3": "",
            "cross_border_m3": _format_float(bound["cross_border_m3"], 9),
            "cross_border_share": _format_float(
                bound["cross_border_share"], 9
            ),
            "mean_road_distance_km": _format_float(bound["mean_km"], 9),
            "share_ge_150_km": _format_float(bound["share_ge_150_km"], 9),
            "share_ge_300_km": _format_float(bound["share_ge_300_km"], 9),
            "transport_cost_cad": "",
            "transport_cost_cad_m3": "",
            "transport_cost_cad_gj": "",
            "penalty_cost_cad": "0.000000000",
            "objective_cad": "",
            "facilities_used": str(int(bound["facilities_used"])),
            "proxy_share_of_assigned": "",
            "status": "diagnostic_lower_bound",
            "note": "distance-only LP; no local-preference penalty",
        },
        {
            "case": "v10_node_lp",
            "local_penalty_km": _format_float(LOCAL_PENALTY_KM, 3),
            "local_penalty_cad_m3": _format_float(line.penalty_cad_m3, 9),
            "assigned_m3": _format_float(v10_metrics["assigned_m3"], 9),
            "local_m3": _format_float(v10_metrics["local_m3"], 9),
            "cross_border_m3": _format_float(
                v10_metrics["cross_border_m3"], 9
            ),
            "cross_border_share": _format_float(
                v10_metrics["cross_border_share"], 9
            ),
            "mean_road_distance_km": _format_float(
                v10_metrics["mean_road_distance_km"], 9
            ),
            "share_ge_150_km": _format_float(
                v10_metrics["share_ge_150_km"], 9
            ),
            "share_ge_300_km": _format_float(
                v10_metrics["share_ge_300_km"], 9
            ),
            "transport_cost_cad": _format_float(
                v10_metrics["transport_cost_cad"], 6
            ),
            "transport_cost_cad_m3": _format_float(
                v10_metrics["transport_cost_cad_m3"], 9
            ),
            "transport_cost_cad_gj": _format_float(
                v10_metrics["transport_cost_cad_gj"], 9
            ),
            "penalty_cost_cad": _format_float(
                v10_metrics["cross_border_m3"] * line.penalty_cad_m3, 6
            ),
            "objective_cad": _format_float(
                v10_metrics["transport_cost_cad"]
                + v10_metrics["cross_border_m3"] * line.penalty_cad_m3,
                6,
            ),
            "facilities_used": str(int(v10_metrics["facilities_used"])),
            "proxy_share_of_assigned": _format_float(
                v10_metrics["proxy_share_of_assigned"], 9
            ),
            "status": "model_assignment",
            "note": "v10 node-level transport-aware LP",
        },
    ]


# ---------------------------------------------------------------------------
# Validation.
# ---------------------------------------------------------------------------

def _check_numeric_rows(
    rows: Sequence[Mapping[str, Any]], fields: Sequence[str], label: str
) -> None:
    for row in rows:
        for field in fields:
            value = row.get(field, "")
            if value in ("", None):
                continue
            parsed = float(value)
            if not math.isfinite(parsed):
                raise AssignmentV10Error(f"{label}: non-finite {field}")
            if parsed < 0.0 and not field.startswith("delta_"):
                raise AssignmentV10Error(f"{label}: negative {field}")


def validate(
    *,
    nodes: Sequence[SupplyNode],
    facilities: Sequence[Any],
    v9_rows: Sequence[Mapping[str, str]],
    v9_centroid: Mapping[str, float],
    v9_fixed: Mapping[str, float],
    v10_rows: Sequence[Mapping[str, Any]],
    v10_metrics: Mapping[str, float],
    facility_rows: Sequence[Mapping[str, Any]],
    province_rows: Sequence[Mapping[str, Any]],
    comparison: Sequence[Mapping[str, Any]],
    distance_rows: Sequence[Mapping[str, Any]],
    local_preference: Sequence[Mapping[str, Any]],
    bound: Mapping[str, Any],
    supply_node_reference: Mapping[str, Any],
    parameters: Mapping[str, transport.Parameter],
    line: CostLine,
) -> dict[str, Any]:
    checks: dict[str, bool] = {}
    positive_nodes = [node for node in nodes if node.harvest_m3 > 0.0]
    checks["nodes_present"] = len(nodes) == 525
    checks["positive_nodes_present"] = len(positive_nodes) > 0
    checks["node_ids_unique"] = len({node.node_id for node in nodes}) == len(nodes)
    checks["facilities_present"] = len(facilities) > 0
    checks["v9_rows_present"] = len(v9_rows) > 0
    checks["v10_rows_present"] = len(v10_rows) > 0

    node_harvest_total = math.fsum(node.harvest_m3 for node in nodes)
    assigned_total = math.fsum(
        float(row["assigned_m3"]) for row in v10_rows
    )
    checks["node_harvest_conservation"] = (
        abs(node_harvest_total - assigned_total)
        <= VOLUME_CONSERVATION_TOLERANCE_M3
    )
    checks["v9_offer_conservation"] = abs(
        assigned_total
        - math.fsum(float(row["assigned_m3"]) for row in v9_rows)
    ) <= VOLUME_CONSERVATION_TOLERANCE_M3
    assigned_by_node: dict[str, float] = defaultdict(float)
    for row in v10_rows:
        assigned_by_node[str(row["node_id"])] += float(row["assigned_m3"])
    checks["per_node_conservation"] = all(
        abs(assigned_by_node.get(node.node_id, 0.0) - node.harvest_m3) <= 1e-6
        for node in positive_nodes
    )
    checks["no_volume_for_zero_nodes"] = all(
        assigned_by_node.get(node.node_id, 0.0) == 0.0
        for node in nodes
        if node.harvest_m3 == 0.0
    )
    assigned_by_province: dict[str, float] = defaultdict(float)
    harvest_by_province: dict[str, float] = defaultdict(float)
    for node in nodes:
        harvest_by_province[node.province] += node.harvest_m3
    for row in v10_rows:
        assigned_by_province[str(row["node_province"])] += float(
            row["assigned_m3"]
        )
    checks["province_conservation"] = all(
        abs(assigned_by_province[province] - harvest_by_province[province])
        <= VOLUME_CONSERVATION_TOLERANCE_M3
        for province in harvest_by_province
        if harvest_by_province[province] > 0.0
    )

    facility_assigned: dict[str, float] = defaultdict(float)
    facility_capacity = {
        facility.facility_id: float(facility.capacity.capacity_m3_y)
        for facility in facilities
    }
    for row in v10_rows:
        facility_id = str(row["facility_id"])
        assigned = float(row["assigned_m3"])
        if assigned < 0.0:
            raise AssignmentV10Error(f"{facility_id}: negative assignment")
        facility_assigned[facility_id] += assigned
        if facility_id not in facility_capacity:
            raise AssignmentV10Error(f"{facility_id}: unknown facility")
    checks["capacity_bounds"] = all(
        volume <= facility_capacity[facility_id] + CAPACITY_TOLERANCE_M3
        for facility_id, volume in facility_assigned.items()
    )

    distance_factor_ok = True
    cost_arithmetic_ok = True
    for row in v10_rows:
        factor = float(row["road_factor_applied"])
        distance = float(row["distance_km"])
        road = float(row["road_distance_km"])
        if not math.isclose(road, distance * factor, rel_tol=1e-9, abs_tol=1e-6):
            distance_factor_ok = False
        assigned = float(row["assigned_m3"])
        per_m3 = float(row["transport_cost_cad_m3"])
        total = float(row["transport_cost_cad"])
        energy = float(row["transport_energy_gj"])
        if not math.isclose(total, assigned * per_m3, rel_tol=1e-8, abs_tol=0.01):
            cost_arithmetic_ok = False
        if not math.isclose(
            energy, assigned * ENERGY_GJ_PER_M3, rel_tol=1e-9, abs_tol=1e-6
        ):
            cost_arithmetic_ok = False
        expected = transport.trip_cost(
            road, PAYLOAD_M3, dict(parameters), BASE_COST_SCENARIO
        )
        if not math.isclose(
            per_m3, expected["cost_per_m3_cad"], rel_tol=1e-9, abs_tol=1e-6
        ):
            cost_arithmetic_ok = False
        if not math.isclose(
            float(row["transport_cost_cad_gj"]),
            expected["cost_per_gj_cad"],
            rel_tol=1e-9,
            abs_tol=1e-6,
        ):
            cost_arithmetic_ok = False
    checks["distance_factor_arithmetic"] = distance_factor_ok
    checks["cost_arithmetic"] = cost_arithmetic_ok
    checks["penalty_arithmetic"] = all(
        math.isclose(
            float(row["local_penalty_cad_m3"]),
            line.penalty_cad_m3
            if row["cross_border"] == "true"
            else 0.0,
            rel_tol=1e-12,
            abs_tol=1e-9,
        )
        for row in v10_rows
    )

    checks["facility_capacity_identity"] = all(
        abs(
            float(row["assigned_m3"])
            + float(row["remaining_m3"])
            - float(row["capacity_m3_y"])
        )
        <= CAPACITY_TOLERANCE_M3
        for row in facility_rows
    )
    checks["binding_flags"] = all(
        row["binding"]
        == _bool_text(
            _is_binding(
                float(row["assigned_m3"]), float(row["capacity_m3_y"])
            )
        )
        for row in facility_rows
    )
    checks["province_rollup_conservation"] = abs(
        math.fsum(float(row["assigned_m3"]) for row in province_rows)
        - assigned_total
    ) <= VOLUME_CONSERVATION_TOLERANCE_M3
    checks["province_local_plus_cross"] = all(
        math.isclose(
            float(row["local_m3"]) + float(row["cross_border_m3"]),
            float(row["assigned_m3"]),
            rel_tol=1e-12,
            abs_tol=1e-3,
        )
        for row in province_rows
    )

    checks["comparison_arithmetic"] = all(
        math.isclose(
            float(row["v10_value"])
            - float(row["v9_centroid_value"]),
            float(row["delta_v10_minus_v9_centroid"]),
            rel_tol=1e-9,
            abs_tol=1e-6,
        )
        and math.isclose(
            float(row["v10_value"])
            - float(row["v9_node_fixed_value"]),
            float(row["delta_v10_minus_v9_node_fixed"]),
            rel_tol=1e-9,
            abs_tol=1e-6,
        )
        for row in comparison
    )
    checks["v10_cost_below_v9_node_fixed"] = (
        v10_metrics["transport_cost_cad"]
        < v9_fixed["transport_cost_cad"]
    )
    checks["v10_mean_below_v9_node_fixed"] = (
        v10_metrics["mean_road_distance_km"]
        < v9_fixed["mean_road_distance_km"]
    )
    checks["v10_mean_below_677_km"] = (
        v10_metrics["mean_road_distance_km"] < 677.0
    )
    checks["distance_bound_not_above_v10"] = (
        bound["mean_km"] <= v10_metrics["mean_road_distance_km"] + 1e-6
    )
    checks["shares_in_unit_interval"] = all(
        0.0 <= float(row["share_ge_150_km"]) <= 1.0
        and 0.0 <= float(row["share_ge_300_km"]) <= 1.0
        for row in distance_rows
    )
    checks["distance_rows_present"] = len(distance_rows) == 4
    checks["local_preference_rows_present"] = len(local_preference) == 4

    band_rows = v9._read_csv(SUPPLY_NODE_BANDS_CSV, "supply node bands")
    band_total = math.fsum(
        float(row["assigned_m3"]) * float(row["node_share"]) for row in band_rows
    )
    band_mean = (
        math.fsum(
            float(row["node_mean_distance_km"])
            * float(row["assigned_m3"])
            * float(row["node_share"])
            for row in band_rows
        )
        / band_total
    )
    band_gt150 = (
        math.fsum(
            float(row["assigned_m3"]) * float(row["node_share"])
            for row in band_rows
            if row["distance_band"] in ("150-300", ">300")
        )
        / band_total
    )
    band_gt300 = (
        math.fsum(
            float(row["assigned_m3"]) * float(row["node_share"])
            for row in band_rows
            if row["distance_band"] == ">300"
        )
        / band_total
    )
    checks["v9_node_fixed_matches_supply_node_distance"] = (
        math.isclose(
            v9_fixed["mean_road_distance_km"], band_mean, rel_tol=1e-9, abs_tol=1e-6
        )
        and math.isclose(
            v9_fixed["share_ge_150_km"], band_gt150, rel_tol=1e-9, abs_tol=1e-9
        )
        and math.isclose(
            v9_fixed["share_ge_300_km"], band_gt300, rel_tol=1e-9, abs_tol=1e-9
        )
    )
    expected_v9_node_cost = (
        float(v9_centroid["transport_cost_cad"])
        + float(supply_node_reference["centroid_bias"]["transport_cost_delta_cad"])
    )
    checks["v9_node_fixed_cost_matches_supply_node_distance"] = math.isclose(
        v9_fixed["transport_cost_cad"],
        expected_v9_node_cost,
        rel_tol=1e-6,
        abs_tol=1000.0,
    )

    checks["labels"] = all(
        row["evidence_label"] == EVIDENCE_LABEL
        and row["observed"] == _bool_text(OBSERVED)
        and row["policy_grade"] == _bool_text(POLICY_GRADE)
        for rows in (v10_rows, province_rows)
        for row in rows
    )
    checks["no_nan_or_negative"] = True
    try:
        _check_numeric_rows(
            v10_rows,
            [
                "assigned_m3",
                "distance_km",
                "road_distance_km",
                "transport_cost_cad_m3",
                "transport_cost_cad",
                "transport_energy_gj",
                "transport_cost_cad_gj",
                "local_preference_penalty_cad",
            ],
            "v10 assignment",
        )
        _check_numeric_rows(
            facility_rows,
            [
                "capacity_m3_y",
                "assigned_m3",
                "remaining_m3",
                "utilization",
                "transport_cost_cad",
                "transport_cost_cad_m3",
                "transport_cost_cad_gj",
            ],
            "facility rows",
        )
        _check_numeric_rows(
            province_rows,
            [
                "assigned_m3",
                "local_m3",
                "cross_border_m3",
                "mean_road_distance_km",
                "transport_cost_cad",
                "transport_cost_cad_m3",
            ],
            "province rows",
        )
    except AssignmentV10Error:
        checks["no_nan_or_negative"] = False
    checks["all_passed"] = all(checks.values())
    return {"all_passed": checks["all_passed"], "checks": checks}


# ---------------------------------------------------------------------------
# Report and manifest.
# ---------------------------------------------------------------------------

def _num(value: float, digits: int = 1) -> str:
    return f"{value:,.{digits}f}"


def _pct(value: float, digits: int = 1) -> str:
    return f"{100.0 * value:,.{digits}f}%"


def _report(
    summary: Mapping[str, Any],
    province_comparison: Sequence[Mapping[str, str]],
) -> str:
    v10 = summary["v10_primary"]
    v9n = summary["v9_node_fixed"]
    bound = summary["distance_bound"]
    comparison = {row["metric"]: row for row in summary["comparison"]}
    cost_row = comparison["transport_cost_cad"]
    lines = [
        "# Node-Level Transport-Aware Facility Assignment V10",
        "",
        ("Status: **model estimate screening** — `evidence_label=model_estimate`, "
        "`observed=false`, `policy_grade=false`. No national claim, no policy "
        "claim, and no official OSeCAN change."),
        "",
        "## 1. What changed",
        "",
        ("V10 replaces the v9 province-centroid assignment origin with the 525 "
        "committed RSYC supply nodes from `supply_node_distance_v1`. 486 nodes "
        "carry positive 119 Mm3 harvest volume; 39 NU nodes carry zero volume "
        "and therefore have no flow. Facility capacity v3, the 1.2x provincial "
        "floor, the 118.977 Mm3 harvest total, products and residue are "
        "unchanged. No greenfield or new facility is introduced."),
        "",
        "## 2. Node-facility LP",
        "",
        ("The v10 assignment is a bipartite transportation linear program "
        "(HiGHS) from positive-volume nodes to 591 eligible operating facilities:"),
        "",
        ("- Objective: transport cost from the committed transport cost v1 "
        "curve (52.5 m3 payload, empty return, 2026 CAD) plus the v9 500 km "
        "cross-border preference penalty expressed in cost."),
        (f"- Local preference penalty: **{_num(summary['method']['local_penalty_cad_m3'], 2)} "
        f"CAD/m3** (500 km equivalent); it is a soft cost penalty, not a hard "
        "flow prohibition."),
        ("- Every positive node harvest is an equality; every facility capacity "
        "v3 is a finite upper bound."),
        ("- Distance is haversine from node coordinate to facility coordinate "
        "multiplied by the facility-province road factor from the committed "
        "`facility_road_distance_audit_v2`. No full OSRM routing is run."),
        "",
        ("The supply-node v1 paired 81-route public OSRM sample is the external "
        "distance check: node median road/haversine is 1.4562 versus 1.4672 at "
        "centroids, and the province-factor transfer error at nodes has median "
        "-5.15%, p25 -11.79%, p75 +5.34%. The factor method is a screening "
        "approximation, not a routed distance for every pair."),
        "",
        "## 3. V10 result",
        "",
        (f"- Assigned **{_num(v10['assigned_m3'], 0)} m3/yr** across "
        f"**{int(v10['facilities_used'])} facilities**."),
        (f"- Transport cost **{_num(v10['transport_cost_cad'] / 1e9, 3)} billion "
        f"CAD/yr** ({_num(v10['transport_cost_cad_m3'], 2)} CAD/m3; "
        f"{_num(v10['transport_cost_cad_gj'], 3)} CAD/GJ)."),
        (f"- Mean road distance **{_num(v10['mean_road_distance_km'], 1)} km**; "
        f"median {_num(v10['median_road_distance_km'], 1)} km; p90 "
        f"{_num(v10['p90_road_distance_km'], 1)} km; maximum "
        f"{_num(v10['max_road_distance_km'], 1)} km."),
        (f"- Share at or above 150 km **{_pct(v10['share_ge_150_km'], 2)}**; at "
        f"or above 300 km **{_pct(v10['share_ge_300_km'], 2)}**."),
        (f"- Local volume **{_num(v10['local_m3'], 0)} m3/yr**; cross-border "
        f"**{_num(v10['cross_border_m3'], 0)} m3/yr** "
        f"({_pct(v10['cross_border_share'], 3)})."),
        f"- Proxy share of assigned volume **{_pct(v10['proxy_share_of_assigned'], 2)}**.",
        "",
        ("The maximum row distance is a small-volume remote-node row, not a "
        "national typical haul. It rises above the v9 centroid maximum because "
        "v10 starts at the actual northern and interior node coordinates and "
        "finite capacity pushes some remote volume to distant facilities; the "
        "volume-weighted distribution still falls sharply. The proxy share is "
        "slightly above v9 because the LP uses more facilities, including "
        "proxy-capacity facilities, to reach nearby nodes."),
        "",
        "## 4. V9 versus V10",
        "",
        ("| Metric | Unit | V9 centroid | V9 fixed at nodes | V10 node LP | "
        "V10 vs V9 centroid | V10 vs V9 node fixed |"),
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for metric in (
        "mean_road_distance_km",
        "max_road_distance_km",
        "share_ge_150_km",
        "share_ge_300_km",
        "transport_cost_cad",
        "transport_cost_cad_m3",
        "facilities_used",
        "proxy_share_of_assigned",
        "cross_border_m3",
        "local_m3",
    ):
        row = comparison[metric]
        unit = row["unit"]
        centroid = float(row["v9_centroid_value"])
        fixed = float(row["v9_node_fixed_value"])
        value = float(row["v10_value"])
        delta_c = row["delta_pct_vs_v9_centroid"]
        delta_f = row["delta_pct_vs_v9_node_fixed"]
        lines.append(
            f"| {metric} | {unit} | {_num(centroid, 3)} | {_num(fixed, 3)} | "
            f"{_num(value, 3)} | {delta_c}% | {delta_f}% |"
        )
    lines.extend(
        [
            "",
            (f"V10 transport cost is **{_num(-float(cost_row['delta_v10_minus_v9_centroid']) / 1e9, 3)} "
            f"billion CAD/yr lower** than the v9 centroid assignment and "
            f"**{_num(-float(cost_row['delta_v10_minus_v9_node_fixed']) / 1e9, 3)} "
            "billion CAD/yr lower** than the same v9 facility allocation "
            "evaluated at node origins."),
            "",
            ("V9 centroid shares are the v9 assignment distances. The V9 fixed "
            "at nodes column is not a new assignment: it holds the v9 facility "
            "allocation fixed and splits each v9 row by supply-node weight, so "
            "it isolates the origin effect and reproduces the committed "
            "supply-node v1 677.5 km node mean. The V10 maximum row distance is "
            "higher than the v9 centroid maximum because the node LP sees the "
            "remote node origins that the centroid hides; the mean, median, "
            "p90 and threshold shares all fall."),
            "",
            "## 5. Can assignment reduce the 677 km node mean?",
            "",
            (f"**Yes.** The v9 allocation at node origins has a "
            f"{_num(v9n['mean_road_distance_km'], 1)} km assigned-volume-weighted "
            f"mean. The v10 node-level LP has "
            f"**{_num(v10['mean_road_distance_km'], 1)} km**, a reduction of "
            f"**{_num(v9n['mean_road_distance_km'] - v10['mean_road_distance_km'], 1)} km "
            f"({_pct((v9n['mean_road_distance_km'] - v10['mean_road_distance_km']) / v9n['mean_road_distance_km'], 2)})**."),
            "",
            (f"The residual is not arbitrary. A diagnostic distance-only LP "
            f"with zero local-preference penalty, the same capacities and the "
            f"same node supplies reaches **{_num(bound['mean_km'], 1)} km**; the "
            f"unconstrained nearest-facility weighted mean is "
            f"**{_num(bound['nearest_facility_weighted_mean_km'], 1)} km**. "
            "The 500 km local preference therefore costs about "
            f"{_num(v10['mean_road_distance_km'] - bound['mean_km'], 1)} km of "
            "mean distance to cut cross-border volume. Facility geography and "
            "finite capacity still set a hard floor well above zero: assignment "
            "can remove the centroid-origin and local-first penalty, but cannot "
            "move facilities to the northern and interior nodes. No facility "
            "siting or greenfield optimization is performed."),
            "",
            "## 6. Province comparison",
            "",
            ("| Province | Nodes | V9 node fixed km | V10 km | Delta km | "
            "V9 node fixed cost CAD | V10 cost CAD | V10 facilities |"),
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in province_comparison:
        lines.append(
            f"| {row['province']} | {row['node_count']} | "
            f"{_num(float(row['v9_node_fixed_mean_road_distance_km']), 1)} | "
            f"{_num(float(row['v10_mean_road_distance_km']), 1)} | "
            f"{_num(float(row['delta_mean_road_distance_km']), 1)} | "
            f"{_num(float(row['v9_node_fixed_transport_cost_cad']) / 1e6, 1)} M | "
            f"{_num(float(row['v10_transport_cost_cad']) / 1e6, 1)} M | "
            f"{row['v10_facilities_used']} |"
        )
    lines.extend(
        [
            "",
            "## 7. Validation and labels",
            "",
            f"- Validation all passed: `{str(summary['validation']['all_passed']).lower()}`.",
            ("- Conservation, capacity, cost arithmetic, comparison arithmetic, "
            "distance-factor arithmetic, roll-up identities, no NaN/negative, "
            "and label checks are recorded in `summary.json`."),
            ("- V9 fixed-at-node metrics reproduce the committed supply-node v1 "
            "band table within tolerance."),
            ("- Every output row is `model_estimate`, `observed=false`, "
            "`policy_grade=false`. `assignment_change_vs_v9=true`; capacity, "
            "floor, products, residue, UI and official OSeCAN are unchanged. "
            "No national or policy claim is made."),
            "",
            "## 8. Reproduce",
            "",
            f"`{summary['reproduce']}`",
            "",
        ]
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Build.
# ---------------------------------------------------------------------------

def build(
    output_dir: Path = OUTPUT_DIR,
    *,
    deletion_checker: Callable[[], None] = v9.assert_no_unexpected_deletions,
) -> dict[str, Any]:
    """Build v10, its v9 node reference, comparison tables and manifest."""

    deletion_checker()
    generated_utc = _utc_now()
    nodes = load_nodes()
    v9_rows = load_v9_rows()
    _load_v9_reference()
    supply_node_reference = _load_supply_node_reference()
    facilities, offers, factors = v9._eligible_facilities()
    parameters = v9.load_transport_parameters()
    line = _cost_line(parameters)

    positive_nodes = [node for node in nodes if node.harvest_m3 > 0.0]
    offers_by_province = {
        offer.province: float(offer.volume_m3) for offer in offers
    }
    node_harvest_total = math.fsum(node.harvest_m3 for node in nodes)
    v9_assigned_total = math.fsum(
        float(row["assigned_m3"]) for row in v9_rows
    )
    if abs(node_harvest_total - v9_assigned_total) > 1e-3:
        raise AssignmentV10Error(
            "node harvest total does not match v9 assigned total"
        )
    if not math.isclose(
        node_harvest_total,
        math.fsum(offers_by_province.values()),
        rel_tol=1e-12,
        abs_tol=1e-3,
    ):
        raise AssignmentV10Error("node harvest total does not match offers")

    distances = _node_facility_distances(positive_nodes, facilities, factors)
    cross_border = np.zeros(distances.shape, dtype=float)
    for node_index, node in enumerate(positive_nodes):
        for facility_index, facility in enumerate(facilities):
            if facility.province != node.province:
                cross_border[node_index, facility_index] = 1.0

    primary_solution = _solve_transport_lp(
        positive_nodes,
        facilities,
        _objective(distances, cross_border, line, include_penalty=True),
    )
    allocations = _tidy_allocations(positive_nodes, facilities, primary_solution)
    v10_rows = _v10_rows(
        positive_nodes,
        facilities,
        allocations,
        distances,
        factors,
        parameters,
        line,
    )
    v9_fixed = _v9_fixed_rows(
        nodes, facilities, v9_rows, factors, parameters, line
    )
    v9_centroid = _v9_centroid_metrics(v9_rows)
    v10_metrics = _metrics(v10_rows)
    facility_rows = _facility_rows(facilities, v10_rows)
    province_rows = _province_rows(
        positive_nodes, facilities, v10_rows, offers_by_province
    )
    province_comparison = _province_comparison_rows(
        positive_nodes, v9_fixed, v10_rows, offers_by_province
    )
    comparison = _comparison_rows(v9_centroid, _metrics(v9_fixed), v10_metrics)

    # Diagnostic lower bound: distance only, with the constant transport
    # intercept and the local-preference penalty removed from the objective.
    bound_solution = _solve_transport_lp(
        positive_nodes,
        facilities,
        distances.ravel() + TIE_BREAK_CAD_M3 * np.arange(distances.size),
    )
    bound = _distance_bound(
        positive_nodes, facilities, distances, bound_solution
    )
    local_preference = _local_preference_rows(
        line, v10_metrics, bound, v9_centroid, _metrics(v9_fixed)
    )
    distance_rows = _distance_rows(
        v9_centroid, _metrics(v9_fixed), v10_metrics, bound
    )
    validation = validate(
        nodes=nodes,
        facilities=facilities,
        v9_rows=v9_rows,
        v9_centroid=v9_centroid,
        v9_fixed=_metrics(v9_fixed),
        v10_rows=v10_rows,
        v10_metrics=v10_metrics,
        facility_rows=facility_rows,
        province_rows=province_rows,
        comparison=comparison,
        distance_rows=distance_rows,
        local_preference=local_preference,
        bound=bound,
        supply_node_reference=supply_node_reference,
        parameters=parameters,
        line=line,
    )

    v9_node_metrics = _metrics(v9_fixed)
    mean_reduction = (
        v9_node_metrics["mean_road_distance_km"]
        - v10_metrics["mean_road_distance_km"]
    )
    summary: dict[str, Any] = {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": generated_utc,
        "status": "complete",
        "labels": {
            "evidence_label": EVIDENCE_LABEL,
            "observed": OBSERVED,
            "policy_grade": POLICY_GRADE,
            "national_claim": False,
            "official_osecan_changed": False,
            "ui_changed": False,
            "assignment_change_vs_v9": True,
            "capacity_change": False,
            "floor_change": False,
            "product_change": False,
            "residue_change": False,
            "greenfield_siting": False,
            "siting_optimization": False,
            "policy_claim": False,
        },
        "method": {
            "engine": "node-to-facility bipartite transportation LP (HiGHS)",
            "scenario": SCENARIO,
            "input_nodes": len(nodes),
            "positive_nodes": len(positive_nodes),
            "zero_volume_nodes": len(nodes) - len(positive_nodes),
            "eligible_facilities": len(facilities),
            "node_basis": (
                "RSYC 150-km tile x province supply node; "
                "harvest_m3 is the committed 119 Mm3 provincial allocation"
            ),
            "facility_capacity": "national_facility_capacity_v3",
            "floor_multiplier": v9.v6.FLOOR_MULTIPLIER,
            "transport_model": "transport_cost_v1 trip_cost base curve",
            "transport_payload_m3": PAYLOAD_M3,
            "transport_energy_gj_per_m3": ENERGY_GJ_PER_M3,
            "distance_method": (
                "haversine node coordinate to facility coordinate x "
                "facility-province road factor from facility_road_distance_audit_v2"
            ),
            "full_osrm": False,
            "osrm_sample": (
                "supply_node_distance_v1 81-pair public OSRM demo sample is "
                "reused as the external distance check; no full OSRM run"
            ),
            "local_preference_penalty_km": LOCAL_PENALTY_KM,
            "local_penalty_cad_m3": line.penalty_cad_m3,
            "transport_cost_line": {
                "intercept_cad_m3": line.intercept_cad_m3,
                "slope_cad_m3_km": line.slope_cad_m3_km,
                "penalty_cad_m3": line.penalty_cad_m3,
            },
            "products_residue": (
                "unchanged; no product or residue output is written or recalculated"
            ),
        },
        "counts": {
            "input_nodes": len(nodes),
            "positive_nodes": len(positive_nodes),
            "eligible_facilities": len(facilities),
            "v10_assignment_rows": len(v10_rows),
            "v9_node_fixed_rows": len(v9_fixed),
            "province_rows": len(province_rows),
            "comparison_rows": len(comparison),
        },
        "v9_centroid": v9_centroid,
        "v9_node_fixed": v9_node_metrics,
        "v10_primary": v10_metrics,
        "distance_bound": bound,
        "comparison": comparison,
        "can_assignment_reduce_677_km_node_mean": True,
        "mean_reduction_vs_v9_node_fixed_km": mean_reduction,
        "mean_reduction_vs_v9_node_fixed_pct": 100.0
        * mean_reduction
        / v9_node_metrics["mean_road_distance_km"],
        "interpretation": (
            "Assignment reduces the 677 km v9 node mean to the v10 LP mean, "
            "but finite facility geography and capacity leave a capacity-"
            "constrained distance floor well above zero; the residual is not "
            "an assignment-policy result and no facility siting is optimized."
        ),
        "province_comparison": province_comparison,
        "local_preference": local_preference,
        "distance_distribution": distance_rows,
        "validation": validation,
        "caveats": [
            "Distances are haversine x facility-province factor screening distances, not observed routes for every node-facility pair.",
            "The 81-pair OSRM sample is an external check only; it does not route the 287k candidate node-facility pairs.",
            "The transport cost covers trucking only and excludes feedstock, processing, conversion, handling and storage.",
            "The supply-node volume is the committed 119 Mm3 provincial harvest allocation, not observed harvest at each tile.",
            "The local-preference penalty is a soft 500 km cross-border cost penalty, not a legal or policy restriction.",
            "Capacity v3 and the 1.2x provincial floor are unchanged; no greenfield facility is added and no facility location is optimized.",
            "Products and residue are unchanged; this is an assignment-only screening rerun.",
            "No national claim, policy claim, UI change, or official OSeCAN change is made.",
        ],
        "reproduce": (
            ".venv/bin/python -m "
            "workflows.national.facilities.national_facility_assignment_v10"
        ),
    }
    summary["report"] = _report(summary, province_comparison)

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(
        output_dir / "v10_node_assignment.csv",
        V10_ASSIGNMENT_FIELDS,
        v10_rows,
    )
    _write_csv(
        output_dir / "v9_node_fixed_rows.csv",
        V9_FIXED_FIELDS,
        v9_fixed,
    )
    _write_csv(
        output_dir / "v10_facility_utilization.csv",
        FACILITY_FIELDS,
        facility_rows,
    )
    _write_csv(
        output_dir / "v10_province_summary.csv",
        PROVINCE_FIELDS,
        province_rows,
    )
    _write_csv(
        output_dir / "v9_v10_province_comparison.csv",
        PROVINCE_COMPARISON_FIELDS,
        province_comparison,
    )
    _write_csv(
        output_dir / "v9_v10_comparison.csv",
        COMPARISON_FIELDS,
        comparison,
    )
    _write_csv(
        output_dir / "distance_distribution.csv",
        DISTANCE_FIELDS,
        distance_rows,
    )
    _write_csv(
        output_dir / "local_preference_cases.csv",
        LOCAL_PREFERENCE_FIELDS,
        local_preference,
    )
    (output_dir / "NODE_ASSIGNMENT_V10.md").write_text(
        summary["report"] + "\n", encoding="utf-8"
    )
    _write_json(output_dir / "summary.json", summary)

    inputs = {
        str(path.relative_to(REPO_ROOT)): v9._sha256(path)
        for path in (
            V9_ASSIGNMENT_CSV,
            V9_SUMMARY_JSON,
            SUPPLY_NODES_CSV,
            SUPPLY_NODE_SUMMARY_JSON,
            SUPPLY_NODE_BANDS_CSV,
            CAPACITY_CSV,
            HARVEST_CSV,
            BOUNDARY_GEOJSON,
            FACTORS_CSV,
            TRANSPORT_PARAMETERS_CSV,
            TRANSPORT_CURVES_CSV,
        )
    }
    outputs: dict[str, Any] = {}
    for path in sorted(output_dir.iterdir()):
        if path.is_file() and path.name != "manifest.json":
            outputs[path.name] = {
                "bytes": path.stat().st_size,
                "sha256": v9._sha256(path),
            }
    manifest = {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": generated_utc,
        "status": "complete",
        "labels": summary["labels"],
        "inputs": inputs,
        "outputs": outputs,
        "reproduce": summary["reproduce"],
    }
    _write_json(output_dir / "manifest.json", manifest)
    return summary


def main() -> None:
    summary = build()
    print(
        json.dumps(
            {
                "artifact": summary["artifact"],
                "v9_node_fixed_mean_km": summary["v9_node_fixed"][
                    "mean_road_distance_km"
                ],
                "v10_mean_km": summary["v10_primary"][
                    "mean_road_distance_km"
                ],
                "v10_transport_cost_cad": summary["v10_primary"][
                    "transport_cost_cad"
                ],
                "validation": summary["validation"]["all_passed"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
