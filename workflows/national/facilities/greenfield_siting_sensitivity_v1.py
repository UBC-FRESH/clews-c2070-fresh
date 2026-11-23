"""Transport-only greenfield siting sensitivity v1.

The committed v10 assignment holds the existing facility set and capacity v3
fixed and assigns the 118.977 Mm3 harvest from 486 positive-volume supply
nodes to 591 eligible operating facilities.  This artifact is a separate
screening that asks a narrower question:

* existing facilities stay fixed with their v3 capacity and the documented
  1.2x provincial floor;
* a greenfield module may be placed at any positive-volume supply node in a
  province with a published road factor;
* each module is a class-agnostic generic transport sink with unlimited
  throughput in the screen; no class capacity, technology, cost, product or
  residue is asserted;
* for k = 0..20 modules, choose the module set and the node-to-facility flow
  that minimise 2026 CAD truck transport cost.

The k-series is a greedy k-median/facility-location screening, not a globally
optimal solution.  Existing-only k=0 is re-solved with the same transport
objective and no local-preference penalty, while the committed v10
existing-only result (with its 500 km cross-border preference penalty) is
reported as the external baseline.  The v10 LP is independently reproduced as
a data/capacity/curve parity check.

Labels: ``transport_only_siting_screen`` / ``not_investment_advice`` /
``model_estimate`` / ``observed=false`` / ``policy_grade=false``.  No capital,
permitting, landbase, class-capacity, national, policy, UI or official OSeCAN
claim is made.  A ``D`` in either the parent or data repository git status
halts the build.
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
from workflows.national.facilities import (
    national_facility_assignment_v10 as v10,
)

REPO_ROOT = v9.REPO_ROOT
DATA_ROOT = v9.DATA_ROOT
V10_DIR = (
    DATA_ROOT / "derived/national/facilities/national_facility_assignment_v10"
)
V10_SUMMARY_JSON = V10_DIR / "summary.json"
V10_ASSIGNMENT_CSV = V10_DIR / "v10_node_assignment.csv"
SUPPLY_NODES_CSV = v10.SUPPLY_NODES_CSV
SUPPLY_NODE_SUMMARY_JSON = v10.SUPPLY_NODE_SUMMARY_JSON
CAPACITY_CSV = v9.CAPACITY_CSV
CAPACITY_SUMMARY = v9.CAPACITY_SUMMARY
HARVEST_CSV = v9.HARVEST_CSV
BOUNDARY_GEOJSON = v9.BOUNDARY_GEOJSON
FACTORS_CSV = v9.FACTORS_CSV
FACTORS_SUMMARY = v9.FACTORS_SUMMARY
TRANSPORT_PARAMETERS_CSV = v9.TRANSPORT_PARAMETERS_CSV
TRANSPORT_CURVES_CSV = v9.TRANSPORT_CURVES_CSV
OUTPUT_DIR = (
    DATA_ROOT
    / "derived/national/facilities/greenfield_siting_sensitivity_v1"
)

ARTIFACT = "greenfield_siting_sensitivity_v1"
ARTIFACT_VERSION = "1.0.0"
SENSITIVITY_LABEL = "transport_only_siting_screen"
INVESTMENT_ADVICE_LABEL = "not_investment_advice"
EVIDENCE_LABEL = v10.EVIDENCE_LABEL
OBSERVED = v10.OBSERVED
POLICY_GRADE = v10.POLICY_GRADE

K_VALUES = tuple(range(21))
SHORTLIST_SIZE = 10
GENERIC_MODULE_CLASS = "GENERIC_MODULE"
MODULE_CAPACITY_STATUS = "unlimited_screen"
MODULE_ID_PREFIX = "GFM"
MODULE_NAME = "Generic greenfield module (transport-only screen)"
MODULE_CAPACITY_EVIDENCE = "unlimited_screen_no_class_capacity_claim"

EXPECTED_NODE_COUNT = 525
EXPECTED_POSITIVE_NODE_COUNT = 486
EXPECTED_HARVEST_M3 = 118_977_339.1
CONSERVATION_TOLERANCE_M3 = 1e-3
CAPACITY_TOLERANCE_M3 = 1e-3
VOLUME_EPSILON_M3 = 1e-6
COST_TOLERANCE_CAD = 0.01
COST_RELATIVE_TOLERANCE = 1e-9
TIE_BREAK_CAD_M3 = v10.TIE_BREAK_CAD_M3

TRADEOFF_FIELDS = [
    "k",
    "selected_module_count",
    "modules_serving",
    "assigned_m3",
    "existing_served_m3",
    "existing_served_share",
    "existing_facilities_used",
    "existing_capacity_m3_y",
    "existing_utilization",
    "binding_facilities",
    "binding_volume_m3",
    "module_served_m3",
    "module_served_share",
    "assignments",
    "road_distance_m3_km",
    "mean_road_distance_km",
    "median_road_distance_km",
    "p90_road_distance_km",
    "max_road_distance_km",
    "share_ge_150_km",
    "share_ge_300_km",
    "cross_border_m3",
    "cross_border_share",
    "transport_energy_gj",
    "transport_cost_cad",
    "transport_cost_cad_m3",
    "transport_cost_cad_gj",
    "marginal_transport_cost_reduction_cad",
    "marginal_mean_distance_reduction_km",
    "delta_transport_cost_vs_k0_cad",
    "delta_transport_cost_vs_k0_pct",
    "delta_mean_road_distance_vs_k0_km",
    "delta_transport_cost_vs_v10_cad",
    "delta_transport_cost_vs_v10_pct",
    "delta_mean_road_distance_vs_v10_km",
    "capacity_relaxed_lower_bound_cost_cad",
    "capacity_relaxed_gap_pct",
    "selected_module_ids",
    "siting_method",
    "evidence_label",
    "sensitivity_label",
    "investment_advice_label",
    "observed",
    "policy_grade",
]

MODULE_FIELDS = [
    "k",
    "selection_rank",
    "first_selected_k",
    "module_id",
    "node_id",
    "province",
    "lat",
    "lon",
    "module_class",
    "class_agnostic",
    "capacity_status",
    "capacity_m3_y",
    "module_served_m3",
    "module_served_share_of_assigned",
    "module_served_share_of_module_total",
    "nodes_served",
    "mean_road_distance_km",
    "max_road_distance_km",
    "road_factor_applied",
    "road_factor_basis",
    "evidence_label",
    "sensitivity_label",
    "investment_advice_label",
    "observed",
    "policy_grade",
]

NODE_ASSIGNMENT_FIELDS = [
    "k",
    "node_id",
    "node_province",
    "node_lat",
    "node_lon",
    "node_harvest_m3",
    "facility_id",
    "facility_name",
    "facility_kind",
    "facility_class",
    "facility_province",
    "module_node_id",
    "cross_border",
    "capacity_status",
    "capacity_m3_y",
    "proxy_low_m3_y",
    "proxy_high_m3_y",
    "capacity_evidence",
    "floor_scale_factor",
    "assigned_m3",
    "road_distance_km",
    "road_factor_applied",
    "road_factor_basis",
    "transport_cost_cad_m3",
    "transport_cost_cad",
    "transport_energy_gj",
    "transport_cost_cad_gj",
    "evidence_label",
    "sensitivity_label",
    "investment_advice_label",
    "observed",
    "policy_grade",
]

EXISTING_SERVICE_FIELDS = [
    "k",
    "facility_id",
    "facility_name",
    "facility_class",
    "province",
    "capacity_m3_y",
    "served_m3",
    "utilization",
    "binding",
    "used",
    "evidence_label",
    "sensitivity_label",
    "investment_advice_label",
    "observed",
    "policy_grade",
]


class GreenfieldSitingError(v9.AssignmentV9Error):
    """Raised when a transport-only siting invariant fails."""


@dataclass(frozen=True, slots=True)
class CandidateModule:
    candidate_index: int
    node: v10.SupplyNode
    road_factor: float
    module_id: str


@dataclass(frozen=True, slots=True)
class SitingResult:
    k: int
    selected: tuple[int, ...]
    rows: tuple[dict[str, Any], ...]
    metrics: dict[str, Any]
    module_stats: dict[str, dict[str, Any]]


# ---------------------------------------------------------------------------
# I/O helpers.
# ---------------------------------------------------------------------------

def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _csv_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return v10._bool_text(value)
    if isinstance(value, (int, np.integer)):
        return str(int(value))
    if isinstance(value, (float, np.floating)):
        return v10._format_float(float(value), 9)
    return str(value)


def _write_csv(
    path: Path, fields: Sequence[str], rows: Sequence[Mapping[str, Any]]
) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields), lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: _csv_value(row.get(field, "")) for field in fields})


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _num(value: float, digits: int = 1) -> str:
    return f"{value:,.{digits}f}"


def _pct(value: float, digits: int = 2) -> str:
    return f"{100.0 * value:.{digits}f}%"


def _billions(value: float, digits: int = 3) -> str:
    return f"{value / 1e9:,.{digits}f} B"


# ---------------------------------------------------------------------------
# Loading and input checks.
# ---------------------------------------------------------------------------

def _load_v10_reference() -> dict[str, Any]:
    if not V10_SUMMARY_JSON.is_file():
        raise GreenfieldSitingError(
            f"assignment v10 summary is missing: {V10_SUMMARY_JSON}"
        )
    with V10_SUMMARY_JSON.open("r", encoding="utf-8") as handle:
        summary = json.load(handle)
    if summary.get("artifact") != "national_facility_assignment_v10":
        raise GreenfieldSitingError(
            f"{V10_SUMMARY_JSON}: unexpected artifact"
        )
    if summary.get("artifact_version") != "10.0.0":
        raise GreenfieldSitingError(
            f"{V10_SUMMARY_JSON}: unexpected artifact version"
        )
    if summary.get("status") != "complete":
        raise GreenfieldSitingError(f"{V10_SUMMARY_JSON}: status is not complete")
    if not summary.get("validation", {}).get("all_passed"):
        raise GreenfieldSitingError("assignment v10 did not pass validation")
    primary = summary.get("v10_primary", {})
    if not math.isclose(
        float(primary.get("assigned_m3", 0.0)),
        EXPECTED_HARVEST_M3,
        rel_tol=1e-12,
        abs_tol=1e-3,
    ):
        raise GreenfieldSitingError(
            "assignment v10 primary volume does not match the 119 Mm3 case"
        )
    return summary


def _load_nodes() -> tuple[v10.SupplyNode, ...]:
    nodes = v10.load_nodes()
    if len(nodes) != EXPECTED_NODE_COUNT:
        raise GreenfieldSitingError(
            f"expected {EXPECTED_NODE_COUNT} supply nodes; got {len(nodes)}"
        )
    positive = [node for node in nodes if node.harvest_m3 > 0.0]
    if len(positive) != EXPECTED_POSITIVE_NODE_COUNT:
        raise GreenfieldSitingError(
            f"expected {EXPECTED_POSITIVE_NODE_COUNT} positive-volume nodes; "
            f"got {len(positive)}"
        )
    total = math.fsum(node.harvest_m3 for node in nodes)
    if not math.isclose(
        total, EXPECTED_HARVEST_M3, rel_tol=1e-12, abs_tol=1e-3
    ):
        raise GreenfieldSitingError(
            f"node harvest total {total!r} does not match the 119 Mm3 case"
        )
    return nodes


def _load_facilities(
    v10_reference: Mapping[str, Any],
) -> tuple[list[Any], Mapping[str, float]]:
    facilities, offers, factors = v9._eligible_facilities()
    expected = int(v10_reference["counts"]["eligible_facilities"])
    if len(facilities) != expected:
        raise GreenfieldSitingError(
            f"expected {expected} eligible facilities; got {len(facilities)}"
        )
    if not facilities:
        raise GreenfieldSitingError("no eligible existing facilities")
    offer_total = math.fsum(float(offer.volume_m3) for offer in offers)
    if not math.isclose(
        offer_total, EXPECTED_HARVEST_M3, rel_tol=1e-12, abs_tol=1e-3
    ):
        raise GreenfieldSitingError(
            "provincial offers do not match the 119 Mm3 harvest case"
        )
    total_capacity = math.fsum(
        float(facility.capacity.capacity_m3_y) for facility in facilities
    )
    if total_capacity <= 0.0:
        raise GreenfieldSitingError("eligible facility capacity is not positive")
    for facility in facilities:
        if facility.province not in factors:
            raise GreenfieldSitingError(
                f"{facility.facility_id}: no road factor for "
                f"{facility.province}"
            )
    return facilities, factors


def _build_candidates(
    positive_nodes: Sequence[v10.SupplyNode],
    factors: Mapping[str, float],
) -> tuple[tuple[CandidateModule, ...], dict[str, dict[str, Any]]]:
    candidates: list[CandidateModule] = []
    excluded: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"nodes": 0, "volume_m3": 0.0}
    )
    for node in positive_nodes:
        factor = factors.get(node.province)
        if factor is None:
            excluded[node.province]["nodes"] += 1
            excluded[node.province]["volume_m3"] += node.harvest_m3
            continue
        candidates.append(
            CandidateModule(
                candidate_index=len(candidates),
                node=node,
                road_factor=float(factor),
                module_id=f"{MODULE_ID_PREFIX}|{node.node_id}",
            )
        )
    unexpected = set(excluded) - {"NT"}
    if unexpected:
        raise GreenfieldSitingError(
            "positive-volume nodes without a road factor in unexpected "
            f"provinces: {sorted(unexpected)}"
        )
    if not candidates:
        raise GreenfieldSitingError("no candidate greenfield module nodes")
    return tuple(candidates), dict(excluded)


# ---------------------------------------------------------------------------
# Distances and transport objective.
# ---------------------------------------------------------------------------

def _distance_matrices(
    positive_nodes: Sequence[v10.SupplyNode],
    facilities: Sequence[Any],
    candidates: Sequence[CandidateModule],
    factors: Mapping[str, float],
) -> tuple[np.ndarray, np.ndarray]:
    node_count = len(positive_nodes)
    facility_count = len(facilities)
    candidate_count = len(candidates)
    existing = np.empty((node_count, facility_count), dtype=float)
    modules = np.empty((node_count, candidate_count), dtype=float)
    facility_factors = np.array(
        [float(factors[facility.province]) for facility in facilities]
    )
    candidate_factors = np.array(
        [candidate.road_factor for candidate in candidates]
    )
    for node_index, node in enumerate(positive_nodes):
        for facility_index, facility in enumerate(facilities):
            distance = (
                v10.haversine_km(
                    node.lat,
                    node.lon,
                    facility.location.lat,
                    facility.location.lon,
                )
                * facility_factors[facility_index]
            )
            if not math.isfinite(distance) or distance < 0.0:
                raise GreenfieldSitingError(
                    f"{node.node_id}->{facility.facility_id}: invalid distance"
                )
            existing[node_index, facility_index] = distance
        for candidate_index, candidate in enumerate(candidates):
            distance = (
                v10.haversine_km(
                    node.lat,
                    node.lon,
                    candidate.node.lat,
                    candidate.node.lon,
                )
                * candidate_factors[candidate_index]
            )
            if not math.isfinite(distance) or distance < 0.0:
                raise GreenfieldSitingError(
                    f"{node.node_id}->{candidate.module_id}: invalid distance"
                )
            modules[node_index, candidate_index] = distance
    return existing, modules


def _transport_objective(distance: np.ndarray, line: v10.CostLine) -> np.ndarray:
    return line.intercept_cad_m3 + line.slope_cad_m3_km * distance


def _v10_objective(
    distance: np.ndarray,
    cross_border: np.ndarray,
    line: v10.CostLine,
) -> np.ndarray:
    return (
        line.intercept_cad_m3
        + line.slope_cad_m3_km * distance
        + line.penalty_cad_m3 * cross_border
    )


def _solve_lp(
    objective: np.ndarray,
    existing_facility_count: int,
    capacities: np.ndarray,
    harvest: np.ndarray,
) -> np.ndarray:
    node_count, facility_count = objective.shape
    if existing_facility_count > facility_count:
        raise GreenfieldSitingError("existing facility count exceeds LP columns")
    pair_count = node_count * facility_count
    if objective.size != pair_count:
        raise GreenfieldSitingError("objective shape does not match LP pairs")
    if not np.all(np.isfinite(objective)):
        raise GreenfieldSitingError("objective contains non-finite values")
    objective_vector = (
        objective.ravel() + TIE_BREAK_CAD_M3 * np.arange(pair_count)
    )
    equality_rows = np.repeat(np.arange(node_count), facility_count)
    equality_columns = np.arange(pair_count)
    a_eq = csr_matrix(
        (np.ones(pair_count), (equality_rows, equality_columns)),
        shape=(node_count, pair_count),
    )
    facility_rows = np.tile(np.arange(existing_facility_count), node_count)
    node_offsets = np.repeat(np.arange(node_count), existing_facility_count)
    facility_columns = (
        node_offsets * facility_count
        + np.tile(np.arange(existing_facility_count), node_count)
    )
    a_ub = csr_matrix(
        (
            np.ones(existing_facility_count * node_count),
            (facility_rows, facility_columns),
        ),
        shape=(existing_facility_count, pair_count),
    )
    result = linprog(
        objective_vector,
        A_ub=a_ub,
        b_ub=capacities,
        A_eq=a_eq,
        b_eq=harvest,
        bounds=(0.0, None),
        method="highs",
        options={"presolve": False},
    )
    if not result.success:
        raise GreenfieldSitingError(
            "transport LP failed: " + str(result.message)
        )
    if result.x is None:
        raise GreenfieldSitingError("transport LP returned no solution")
    solution = np.asarray(result.x, dtype=float)
    if not np.all(np.isfinite(solution)):
        raise GreenfieldSitingError("transport LP solution is not finite")
    if float(solution.min()) < -1e-9:
        raise GreenfieldSitingError("transport LP solution has negative flow")
    return solution.reshape(node_count, facility_count)


# ---------------------------------------------------------------------------
# Allocation tidying and row construction.
# ---------------------------------------------------------------------------

def _tidy_allocations(
    solution: np.ndarray,
    harvest: np.ndarray,
    capacities: np.ndarray,
) -> list[list[tuple[int, float]]]:
    node_count, facility_count = solution.shape
    existing_facility_count = len(capacities)
    if existing_facility_count > facility_count:
        raise GreenfieldSitingError("capacity vector is longer than LP columns")
    allocations: list[list[tuple[int, float]]] = []
    for node_index in range(node_count):
        row = np.maximum(np.asarray(solution[node_index], dtype=float), 0.0)
        total = float(row.sum())
        if total <= 0.0:
            raise GreenfieldSitingError(
                f"node {node_index}: LP produced no allocation for positive supply"
            )
        row *= float(harvest[node_index]) / total
        candidates = [
            (facility_index, float(volume))
            for facility_index, volume in enumerate(row)
            if volume > 1e-9
        ]
        if not candidates:
            raise GreenfieldSitingError(
                f"node {node_index}: no positive allocation after tidying"
            )
        kept = [
            (facility_index, volume)
            for facility_index, volume in candidates
            if volume > VOLUME_EPSILON_M3
        ]
        if not kept:
            kept = [max(candidates, key=lambda item: item[1])]
        dropped = float(harvest[node_index]) - math.fsum(
            volume for _, volume in kept
        )
        if dropped > 0.0:
            target = max(kept, key=lambda item: item[1])
            kept = [
                (
                    facility_index,
                    volume + dropped
                    if facility_index == target[0]
                    else volume,
                )
                for facility_index, volume in kept
            ]
        conserved = math.fsum(volume for _, volume in kept)
        if abs(conserved - float(harvest[node_index])) > CONSERVATION_TOLERANCE_M3:
            raise GreenfieldSitingError(
                f"node {node_index}: conservation gap "
                f"{conserved - float(harvest[node_index])!r} m3"
            )
        allocations.append(kept)
    facility_totals = np.zeros(facility_count, dtype=float)
    for entries in allocations:
        for facility_index, volume in entries:
            facility_totals[facility_index] += volume
    for facility_index in range(existing_facility_count):
        capacity = float(capacities[facility_index])
        if facility_totals[facility_index] > capacity + CAPACITY_TOLERANCE_M3:
            raise GreenfieldSitingError(
                f"existing facility {facility_index}: LP exceeds capacity by "
                f"{facility_totals[facility_index] - capacity!r} m3"
            )
    return allocations


def _build_rows(
    positive_nodes: Sequence[v10.SupplyNode],
    facilities: Sequence[Any],
    selected_candidates: Sequence[CandidateModule],
    allocations: Sequence[Sequence[tuple[int, float]]],
    distance: np.ndarray,
    factors: Mapping[str, float],
    line: v10.CostLine,
    k: int,
) -> tuple[dict[str, Any], ...]:
    existing_facility_count = len(facilities)
    rows: list[dict[str, Any]] = []
    for node_index, node in enumerate(positive_nodes):
        for facility_index, volume in allocations[node_index]:
            road_distance = float(distance[node_index, facility_index])
            cost_cad_m3 = (
                line.intercept_cad_m3
                + line.slope_cad_m3_km * road_distance
            )
            energy_gj = volume * v10.ENERGY_GJ_PER_M3
            total_cost = volume * cost_cad_m3
            if facility_index < existing_facility_count:
                facility = facilities[facility_index]
                kind = "existing"
                facility_id = facility.facility_id
                facility_name = facility.name
                facility_class = facility.facility_class
                facility_province = facility.province
                module_node_id = ""
                capacity_status = facility.capacity.status
                capacity_m3_y: Any = facility.capacity.capacity_m3_y
                proxy_low_m3_y: Any = facility.capacity.proxy_low_m3_y
                proxy_high_m3_y: Any = facility.capacity.proxy_high_m3_y
                capacity_evidence = facility.capacity.capacity_evidence
                floor_scale_factor: Any = facility.capacity.floor_scale_factor
                road_factor = float(factors[facility.province])
                road_factor_basis = "facility_province"
            else:
                candidate = selected_candidates[
                    facility_index - existing_facility_count
                ]
                kind = "greenfield_module"
                facility_id = candidate.module_id
                facility_name = MODULE_NAME
                facility_class = GENERIC_MODULE_CLASS
                facility_province = candidate.node.province
                module_node_id = candidate.node.node_id
                capacity_status = MODULE_CAPACITY_STATUS
                capacity_m3_y = ""
                proxy_low_m3_y = ""
                proxy_high_m3_y = ""
                capacity_evidence = MODULE_CAPACITY_EVIDENCE
                floor_scale_factor = ""
                road_factor = candidate.road_factor
                road_factor_basis = "candidate_province_measured_factor"
            rows.append(
                {
                    "k": k,
                    "node_id": node.node_id,
                    "node_province": node.province,
                    "node_lat": node.lat,
                    "node_lon": node.lon,
                    "node_harvest_m3": node.harvest_m3,
                    "facility_id": facility_id,
                    "facility_name": facility_name,
                    "facility_kind": kind,
                    "facility_class": facility_class,
                    "facility_province": facility_province,
                    "module_node_id": module_node_id,
                    "cross_border": facility_province != node.province,
                    "capacity_status": capacity_status,
                    "capacity_m3_y": capacity_m3_y,
                    "proxy_low_m3_y": proxy_low_m3_y,
                    "proxy_high_m3_y": proxy_high_m3_y,
                    "capacity_evidence": capacity_evidence,
                    "floor_scale_factor": floor_scale_factor,
                    "assigned_m3": volume,
                    "road_distance_km": road_distance,
                    "road_factor_applied": road_factor,
                    "road_factor_basis": road_factor_basis,
                    "transport_cost_cad_m3": cost_cad_m3,
                    "transport_cost_cad": total_cost,
                    "transport_energy_gj": energy_gj,
                    "transport_cost_cad_gj": (
                        total_cost / energy_gj if energy_gj > 0.0 else 0.0
                    ),
                    "evidence_label": EVIDENCE_LABEL,
                    "sensitivity_label": SENSITIVITY_LABEL,
                    "investment_advice_label": INVESTMENT_ADVICE_LABEL,
                    "observed": OBSERVED,
                    "policy_grade": POLICY_GRADE,
                }
            )
    rows.sort(
        key=lambda row: (
            row["node_province"],
            row["node_id"],
            row["facility_kind"],
            row["facility_id"],
        )
    )
    return tuple(rows)


# ---------------------------------------------------------------------------
# Metrics.
# ---------------------------------------------------------------------------

def _metrics(
    rows: Sequence[Mapping[str, Any]],
    selected: Sequence[int],
    total_harvest_m3: float,
    existing_capacity_m3_y: float,
    capacities: np.ndarray,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    if not rows:
        raise GreenfieldSitingError("metric rows are empty")
    assigned = math.fsum(float(row["assigned_m3"]) for row in rows)
    if abs(assigned - total_harvest_m3) > CONSERVATION_TOLERANCE_M3:
        raise GreenfieldSitingError(
            f"assigned volume {assigned!r} does not conserve "
            f"{total_harvest_m3!r}"
        )
    existing_rows = [
        row for row in rows if row["facility_kind"] == "existing"
    ]
    module_rows = [
        row for row in rows if row["facility_kind"] == "greenfield_module"
    ]
    existing_served = math.fsum(
        float(row["assigned_m3"]) for row in existing_rows
    )
    module_served = math.fsum(
        float(row["assigned_m3"]) for row in module_rows
    )
    if abs(existing_served + module_served - assigned) > CONSERVATION_TOLERANCE_M3:
        raise GreenfieldSitingError("existing plus module volume is not conserved")
    cross_border = math.fsum(
        float(row["assigned_m3"])
        for row in rows
        if bool(row["cross_border"])
    )
    energy = math.fsum(float(row["transport_energy_gj"]) for row in rows)
    cost = math.fsum(float(row["transport_cost_cad"]) for row in rows)
    volume_distance = math.fsum(
        float(row["assigned_m3"]) * float(row["road_distance_km"])
        for row in rows
    )
    facility_volume: dict[str, float] = defaultdict(float)
    facility_capacity: dict[str, float] = {}
    for row in existing_rows:
        facility_id = str(row["facility_id"])
        facility_volume[facility_id] += float(row["assigned_m3"])
        facility_capacity[facility_id] = float(row["capacity_m3_y"])
    binding = [
        facility_id
        for facility_id, volume in facility_volume.items()
        if v10._is_binding(volume, facility_capacity[facility_id])
    ]
    max_capacity_excess = max(
        (
            max(0.0, volume - facility_capacity[facility_id])
            for facility_id, volume in facility_volume.items()
        ),
        default=0.0,
    )
    module_stats: dict[str, dict[str, Any]] = {}
    for row in module_rows:
        module_id = str(row["facility_id"])
        stats = module_stats.setdefault(
            module_id,
            {
                "module_id": module_id,
                "node_id": str(row["module_node_id"]),
                "province": str(row["facility_province"]),
                "road_factor_applied": float(row["road_factor_applied"]),
                "served_m3": 0.0,
                "node_ids": set(),
                "volume_distance": 0.0,
                "max_road_distance_km": 0.0,
            },
        )
        volume = float(row["assigned_m3"])
        distance = float(row["road_distance_km"])
        stats["served_m3"] += volume
        stats["node_ids"].add(str(row["node_id"]))
        stats["volume_distance"] += volume * distance
        stats["max_road_distance_km"] = max(
            stats["max_road_distance_km"], distance
        )
    for stats in module_stats.values():
        stats["nodes_served"] = len(stats["node_ids"])
        stats["mean_road_distance_km"] = (
            stats["volume_distance"] / stats["served_m3"]
            if stats["served_m3"] > 0.0
            else 0.0
        )
        stats.pop("node_ids", None)
        stats.pop("volume_distance", None)
    metrics: dict[str, Any] = {
        "k": len(selected),
        "selected_module_count": len(selected),
        "modules_serving": len(module_stats),
        "assigned_m3": assigned,
        "existing_served_m3": existing_served,
        "existing_served_share": existing_served / assigned,
        "existing_facilities_used": len(facility_volume),
        "existing_capacity_m3_y": existing_capacity_m3_y,
        "existing_utilization": existing_served / existing_capacity_m3_y,
        "binding_facilities": len(binding),
        "binding_volume_m3": math.fsum(
            facility_volume[facility_id] for facility_id in binding
        ),
        "module_served_m3": module_served,
        "module_served_share": module_served / assigned,
        "assignments": len(rows),
        "road_distance_m3_km": volume_distance,
        "mean_road_distance_km": volume_distance / assigned,
        "median_road_distance_km": v10._weighted_quantile(rows, 0.5),
        "p90_road_distance_km": v10._weighted_quantile(rows, 0.9),
        "max_road_distance_km": max(
            float(row["road_distance_km"]) for row in rows
        ),
        "share_ge_150_km": math.fsum(
            float(row["assigned_m3"])
            for row in rows
            if float(row["road_distance_km"]) >= 150.0
        )
        / assigned,
        "share_ge_300_km": math.fsum(
            float(row["assigned_m3"])
            for row in rows
            if float(row["road_distance_km"]) >= 300.0
        )
        / assigned,
        "cross_border_m3": cross_border,
        "cross_border_share": cross_border / assigned,
        "transport_energy_gj": energy,
        "transport_cost_cad": cost,
        "transport_cost_cad_m3": cost / assigned,
        "transport_cost_cad_gj": cost / energy if energy > 0.0 else 0.0,
        "max_capacity_excess_m3": max_capacity_excess,
    }
    if max_capacity_excess > CAPACITY_TOLERANCE_M3:
        raise GreenfieldSitingError(
            f"existing capacity exceeded by {max_capacity_excess!r} m3"
        )
    return metrics, module_stats


def _node_distance_vector(
    rows: Sequence[Mapping[str, Any]],
    positive_nodes: Sequence[v10.SupplyNode],
) -> np.ndarray:
    index_by_node = {
        node.node_id: index for index, node in enumerate(positive_nodes)
    }
    volume = np.zeros(len(positive_nodes), dtype=float)
    volume_distance = np.zeros(len(positive_nodes), dtype=float)
    for row in rows:
        node_id = str(row["node_id"])
        try:
            index = index_by_node[node_id]
        except KeyError as error:
            raise GreenfieldSitingError(
                f"row node {node_id!r} is not a positive supply node"
            ) from error
        assigned = float(row["assigned_m3"])
        volume[index] += assigned
        volume_distance[index] += assigned * float(row["road_distance_km"])
    if np.any(volume <= 0.0):
        missing = [
            positive_nodes[index].node_id
            for index in np.nonzero(volume <= 0.0)[0]
        ]
        raise GreenfieldSitingError(
            f"positive nodes without assigned volume: {missing[:5]}"
        )
    return volume_distance / volume


def _capacity_relaxed_lower_bound(
    existing_distance: np.ndarray,
    module_distance: np.ndarray,
    selected: Sequence[int],
    harvest: np.ndarray,
    line: v10.CostLine,
) -> dict[str, float]:
    nearest = existing_distance.min(axis=1)
    if selected:
        nearest_module = module_distance[:, list(selected)].min(axis=1)
        nearest = np.minimum(nearest, nearest_module)
    total = float(harvest.sum())
    volume_distance = float(np.dot(harvest, nearest))
    return {
        "cost_cad": line.intercept_cad_m3 * total
        + line.slope_cad_m3_km * volume_distance,
        "mean_km": volume_distance / total,
    }


# ---------------------------------------------------------------------------
# Solution builders.
# ---------------------------------------------------------------------------

def _solve_and_build(
    k: int,
    selected: Sequence[int],
    positive_nodes: Sequence[v10.SupplyNode],
    facilities: Sequence[Any],
    candidates: Sequence[CandidateModule],
    existing_distance: np.ndarray,
    module_distance: np.ndarray,
    capacities: np.ndarray,
    harvest: np.ndarray,
    factors: Mapping[str, float],
    line: v10.CostLine,
    total_harvest_m3: float,
    existing_capacity_m3_y: float,
) -> SitingResult:
    if len(selected) != k:
        raise GreenfieldSitingError(
            f"k={k} but {len(selected)} modules were selected"
        )
    if selected:
        distance = np.hstack(
            [existing_distance, module_distance[:, list(selected)]]
        )
    else:
        distance = existing_distance
    objective = _transport_objective(distance, line)
    solution = _solve_lp(
        objective,
        len(facilities),
        capacities,
        harvest,
    )
    allocations = _tidy_allocations(solution, harvest, capacities)
    rows = _build_rows(
        positive_nodes,
        facilities,
        tuple(candidates[index] for index in selected),
        allocations,
        distance,
        factors,
        line,
        k,
    )
    metrics, module_stats = _metrics(
        rows,
        selected,
        total_harvest_m3,
        existing_capacity_m3_y,
        capacities,
    )
    metrics["selected_module_ids"] = ";".join(
        candidates[index].module_id for index in selected
    )
    return SitingResult(
        k=k,
        selected=tuple(int(index) for index in selected),
        rows=rows,
        metrics=metrics,
        module_stats=module_stats,
    )


def _greedy_select(
    positive_nodes: Sequence[v10.SupplyNode],
    facilities: Sequence[Any],
    candidates: Sequence[CandidateModule],
    existing_distance: np.ndarray,
    module_distance: np.ndarray,
    capacities: np.ndarray,
    harvest: np.ndarray,
    factors: Mapping[str, float],
    line: v10.CostLine,
    total_harvest_m3: float,
    existing_capacity_m3_y: float,
) -> tuple[list[SitingResult], int]:
    baseline = _solve_and_build(
        0,
        (),
        positive_nodes,
        facilities,
        candidates,
        existing_distance,
        module_distance,
        capacities,
        harvest,
        factors,
        line,
        total_harvest_m3,
        existing_capacity_m3_y,
    )
    results = [baseline]
    selected: list[int] = []
    current_distance = _node_distance_vector(baseline.rows, positive_nodes)
    lp_solves = 1
    for step in range(1, len(K_VALUES)):
        unselected = np.array(
            [
                index
                for index in range(len(candidates))
                if index not in selected
            ],
            dtype=int,
        )
        if unselected.size == 0:
            raise GreenfieldSitingError(
                f"k={step} requested but no candidate modules remain"
            )
        savings = np.maximum(
            0.0,
            current_distance[:, None] - module_distance[:, unselected],
        )
        benefit = harvest @ savings
        shortlist = np.argsort(-benefit, kind="stable")[:SHORTLIST_SIZE]
        best_result: SitingResult | None = None
        best_cost = math.inf
        best_benefit = -math.inf
        for shortlist_position in shortlist:
            candidate_index = int(unselected[int(shortlist_position)])
            trial_selected = (*selected, candidate_index)
            trial = _solve_and_build(
                step,
                trial_selected,
                positive_nodes,
                facilities,
                candidates,
                existing_distance,
                module_distance,
                capacities,
                harvest,
                factors,
                line,
                total_harvest_m3,
                existing_capacity_m3_y,
            )
            lp_solves += 1
            trial_cost = float(trial.metrics["transport_cost_cad"])
            trial_benefit = float(benefit[int(shortlist_position)])
            is_better = trial_cost < best_cost - 1e-6
            is_tie_better = (
                abs(trial_cost - best_cost) <= 1e-6
                and trial_benefit > best_benefit + 1e-9
            )
            if best_result is None or is_better or is_tie_better:
                best_result = trial
                best_cost = trial_cost
                best_benefit = trial_benefit
        if best_result is None:
            raise GreenfieldSitingError(f"k={step}: no trial module selected")
        previous_cost = float(results[-1].metrics["transport_cost_cad"])
        if best_cost > previous_cost + max(
            COST_TOLERANCE_CAD, COST_RELATIVE_TOLERANCE * previous_cost
        ):
            raise GreenfieldSitingError(
                f"k={step}: transport cost increased from {previous_cost!r} "
                f"to {best_cost!r}"
            )
        selected.append(best_result.selected[-1])
        results.append(best_result)
        current_distance = _node_distance_vector(
            best_result.rows, positive_nodes
        )
    return results, lp_solves


def _reproduce_v10(
    positive_nodes: Sequence[v10.SupplyNode],
    facilities: Sequence[Any],
    existing_distance: np.ndarray,
    capacities: np.ndarray,
    harvest: np.ndarray,
    factors: Mapping[str, float],
    line: v10.CostLine,
    total_harvest_m3: float,
    existing_capacity_m3_y: float,
    v10_reference: Mapping[str, Any],
) -> dict[str, Any]:
    facility_provinces = np.array(
        [facility.province for facility in facilities]
    )
    node_provinces = np.array([node.province for node in positive_nodes])
    cross_border = (
        facility_provinces[None, :] != node_provinces[:, None]
    ).astype(float)
    objective = _v10_objective(existing_distance, cross_border, line)
    solution = _solve_lp(objective, len(facilities), capacities, harvest)
    allocations = _tidy_allocations(solution, harvest, capacities)
    rows = _build_rows(
        positive_nodes,
        facilities,
        (),
        allocations,
        existing_distance,
        factors,
        line,
        0,
    )
    metrics, _ = _metrics(
        rows,
        (),
        total_harvest_m3,
        existing_capacity_m3_y,
        capacities,
    )
    reference = v10_reference["v10_primary"]
    delta_cost = float(metrics["transport_cost_cad"]) - float(
        reference["transport_cost_cad"]
    )
    delta_mean = float(metrics["mean_road_distance_km"]) - float(
        reference["mean_road_distance_km"]
    )
    delta_share_150 = float(metrics["share_ge_150_km"]) - float(
        reference["share_ge_150_km"]
    )
    delta_share_300 = float(metrics["share_ge_300_km"]) - float(
        reference["share_ge_300_km"]
    )
    passed = (
        abs(delta_cost) <= max(1.0, 1e-6 * abs(float(reference["transport_cost_cad"])))
        and abs(delta_mean) <= 1e-6
        and abs(delta_share_150) <= 1e-6
        and abs(delta_share_300) <= 1e-6
    )
    if not passed:
        raise GreenfieldSitingError(
            "v10 reproduction failed: "
            f"delta_cost={delta_cost!r}, delta_mean={delta_mean!r}, "
            f"delta_share_150={delta_share_150!r}, "
            f"delta_share_300={delta_share_300!r}"
        )
    return {
        "metrics": metrics,
        "delta_cost_cad": delta_cost,
        "delta_mean_road_distance_km": delta_mean,
        "delta_share_ge_150_km": delta_share_150,
        "delta_share_ge_300_km": delta_share_300,
        "passed": passed,
    }


# ---------------------------------------------------------------------------
# Output row builders.
# ---------------------------------------------------------------------------

def _build_lower_bounds(
    results: Sequence[SitingResult],
    existing_distance: np.ndarray,
    module_distance: np.ndarray,
    harvest: np.ndarray,
    line: v10.CostLine,
) -> list[dict[str, float]]:
    return [
        _capacity_relaxed_lower_bound(
            existing_distance,
            module_distance,
            result.selected,
            harvest,
            line,
        )
        for result in results
    ]


def _build_tradeoff_rows(
    results: Sequence[SitingResult],
    v10_metrics: Mapping[str, Any],
    lower_bounds: Sequence[Mapping[str, float]],
) -> list[dict[str, Any]]:
    k0_cost = float(results[0].metrics["transport_cost_cad"])
    k0_mean = float(results[0].metrics["mean_road_distance_km"])
    v10_cost = float(v10_metrics["transport_cost_cad"])
    v10_mean = float(v10_metrics["mean_road_distance_km"])
    rows: list[dict[str, Any]] = []
    for index, result in enumerate(results):
        metrics = result.metrics
        cost = float(metrics["transport_cost_cad"])
        mean = float(metrics["mean_road_distance_km"])
        if index == 0:
            marginal_cost = 0.0
            marginal_mean = 0.0
        else:
            previous = results[index - 1].metrics
            marginal_cost = float(previous["transport_cost_cad"]) - cost
            marginal_mean = (
                float(previous["mean_road_distance_km"]) - mean
            )
        lower_bound = lower_bounds[index]
        lower_bound_cost = float(lower_bound["cost_cad"])
        capacity_relaxed_gap_pct = (
            100.0 * (cost - lower_bound_cost) / cost if cost > 0.0 else 0.0
        )
        rows.append(
            {
                "k": result.k,
                "selected_module_count": result.k,
                "modules_serving": metrics["modules_serving"],
                "assigned_m3": metrics["assigned_m3"],
                "existing_served_m3": metrics["existing_served_m3"],
                "existing_served_share": metrics["existing_served_share"],
                "existing_facilities_used": metrics[
                    "existing_facilities_used"
                ],
                "existing_capacity_m3_y": metrics["existing_capacity_m3_y"],
                "existing_utilization": metrics["existing_utilization"],
                "binding_facilities": metrics["binding_facilities"],
                "binding_volume_m3": metrics["binding_volume_m3"],
                "module_served_m3": metrics["module_served_m3"],
                "module_served_share": metrics["module_served_share"],
                "assignments": metrics["assignments"],
                "road_distance_m3_km": metrics["road_distance_m3_km"],
                "mean_road_distance_km": mean,
                "median_road_distance_km": metrics[
                    "median_road_distance_km"
                ],
                "p90_road_distance_km": metrics["p90_road_distance_km"],
                "max_road_distance_km": metrics["max_road_distance_km"],
                "share_ge_150_km": metrics["share_ge_150_km"],
                "share_ge_300_km": metrics["share_ge_300_km"],
                "cross_border_m3": metrics["cross_border_m3"],
                "cross_border_share": metrics["cross_border_share"],
                "transport_energy_gj": metrics["transport_energy_gj"],
                "transport_cost_cad": cost,
                "transport_cost_cad_m3": metrics["transport_cost_cad_m3"],
                "transport_cost_cad_gj": metrics["transport_cost_cad_gj"],
                "marginal_transport_cost_reduction_cad": marginal_cost,
                "marginal_mean_distance_reduction_km": marginal_mean,
                "delta_transport_cost_vs_k0_cad": cost - k0_cost,
                "delta_transport_cost_vs_k0_pct": (
                    100.0 * (cost / k0_cost - 1.0) if k0_cost > 0.0 else 0.0
                ),
                "delta_mean_road_distance_vs_k0_km": mean - k0_mean,
                "delta_transport_cost_vs_v10_cad": cost - v10_cost,
                "delta_transport_cost_vs_v10_pct": (
                    100.0 * (cost / v10_cost - 1.0)
                    if v10_cost > 0.0
                    else 0.0
                ),
                "delta_mean_road_distance_vs_v10_km": mean - v10_mean,
                "capacity_relaxed_lower_bound_cost_cad": lower_bound_cost,
                "capacity_relaxed_gap_pct": capacity_relaxed_gap_pct,
                "selected_module_ids": metrics["selected_module_ids"],
                "siting_method": "greedy_forward_exact_lp_shortlist",
                "evidence_label": EVIDENCE_LABEL,
                "sensitivity_label": SENSITIVITY_LABEL,
                "investment_advice_label": INVESTMENT_ADVICE_LABEL,
                "observed": OBSERVED,
                "policy_grade": POLICY_GRADE,
            }
        )
    return rows


def _build_module_rows(
    results: Sequence[SitingResult],
    candidates: Sequence[CandidateModule],
) -> list[dict[str, Any]]:
    candidate_by_index = {
        candidate.candidate_index: candidate for candidate in candidates
    }
    first_selected: dict[int, int] = {}
    rows: list[dict[str, Any]] = []
    for result in results:
        if result.k == 0:
            continue
        module_total = float(result.metrics["module_served_m3"])
        assigned_total = float(result.metrics["assigned_m3"])
        for rank, candidate_index in enumerate(result.selected, start=1):
            candidate = candidate_by_index[candidate_index]
            stats = result.module_stats.get(candidate.module_id)
            if stats is None:
                raise GreenfieldSitingError(
                    f"k={result.k}: selected module {candidate.module_id} "
                    "served no volume"
                )
            first_selected.setdefault(candidate_index, result.k)
            served = float(stats["served_m3"])
            rows.append(
                {
                    "k": result.k,
                    "selection_rank": rank,
                    "first_selected_k": first_selected[candidate_index],
                    "module_id": candidate.module_id,
                    "node_id": candidate.node.node_id,
                    "province": candidate.node.province,
                    "lat": candidate.node.lat,
                    "lon": candidate.node.lon,
                    "module_class": GENERIC_MODULE_CLASS,
                    "class_agnostic": True,
                    "capacity_status": MODULE_CAPACITY_STATUS,
                    "capacity_m3_y": "",
                    "module_served_m3": served,
                    "module_served_share_of_assigned": (
                        served / assigned_total if assigned_total > 0.0 else 0.0
                    ),
                    "module_served_share_of_module_total": (
                        served / module_total if module_total > 0.0 else 0.0
                    ),
                    "nodes_served": stats["nodes_served"],
                    "mean_road_distance_km": stats["mean_road_distance_km"],
                    "max_road_distance_km": stats[
                        "max_road_distance_km"
                    ],
                    "road_factor_applied": candidate.road_factor,
                    "road_factor_basis": (
                        "candidate_province_measured_factor"
                    ),
                    "evidence_label": EVIDENCE_LABEL,
                    "sensitivity_label": SENSITIVITY_LABEL,
                    "investment_advice_label": INVESTMENT_ADVICE_LABEL,
                    "observed": OBSERVED,
                    "policy_grade": POLICY_GRADE,
                }
            )
    return rows


def _build_existing_service_rows(
    results: Sequence[SitingResult],
    facilities: Sequence[Any],
) -> list[dict[str, Any]]:
    selected: dict[int, SitingResult] = {
        0: results[0],
        len(K_VALUES) - 1: results[-1],
    }
    rows: list[dict[str, Any]] = []
    for k, result in sorted(selected.items()):
        volume: dict[str, float] = defaultdict(float)
        for row in result.rows:
            if row["facility_kind"] != "existing":
                continue
            volume[str(row["facility_id"])] += float(row["assigned_m3"])
        for facility in sorted(facilities, key=lambda item: item.facility_id):
            served = volume.get(facility.facility_id, 0.0)
            capacity = float(facility.capacity.capacity_m3_y)
            rows.append(
                {
                    "k": k,
                    "facility_id": facility.facility_id,
                    "facility_name": facility.name,
                    "facility_class": facility.facility_class,
                    "province": facility.province,
                    "capacity_m3_y": capacity,
                    "served_m3": served,
                    "utilization": served / capacity,
                    "binding": v10._is_binding(served, capacity),
                    "used": served > VOLUME_EPSILON_M3,
                    "evidence_label": EVIDENCE_LABEL,
                    "sensitivity_label": SENSITIVITY_LABEL,
                    "investment_advice_label": INVESTMENT_ADVICE_LABEL,
                    "observed": OBSERVED,
                    "policy_grade": POLICY_GRADE,
                }
            )
    return rows


def _build_node_assignment_rows(
    result: SitingResult,
) -> list[dict[str, Any]]:
    return [dict(row) for row in result.rows]


# ---------------------------------------------------------------------------
# Validation.
# ---------------------------------------------------------------------------

def validate(
    *,
    nodes: Sequence[v10.SupplyNode],
    positive_nodes: Sequence[v10.SupplyNode],
    facilities: Sequence[Any],
    candidates: Sequence[CandidateModule],
    factors: Mapping[str, float],
    excluded_candidates: Mapping[str, Mapping[str, Any]],
    results: Sequence[SitingResult],
    tradeoff_rows: Sequence[Mapping[str, Any]],
    module_rows: Sequence[Mapping[str, Any]],
    node_assignment_k0: Sequence[Mapping[str, Any]],
    node_assignment_k20: Sequence[Mapping[str, Any]],
    existing_service_rows: Sequence[Mapping[str, Any]],
    v10_reference: Mapping[str, Any],
    v10_reproduction: Mapping[str, Any],
    line: v10.CostLine,
    existing_capacity_m3_y: float,
) -> dict[str, Any]:
    checks: dict[str, bool] = {}
    failures: list[str] = []

    def check(name: str, condition: bool, detail: str = "") -> None:
        checks[name] = bool(condition)
        if not condition:
            failures.append(f"{name}: {detail}" if detail else name)

    total_harvest = math.fsum(node.harvest_m3 for node in nodes)
    check(
        "node_universe",
        len(nodes) == EXPECTED_NODE_COUNT
        and len(positive_nodes) == EXPECTED_POSITIVE_NODE_COUNT,
        f"nodes={len(nodes)} positive={len(positive_nodes)}",
    )
    check(
        "k_grid",
        [result.k for result in results] == list(K_VALUES),
        "k values do not cover 0..20",
    )
    check(
        "conservation",
        all(
            abs(float(result.metrics["assigned_m3"]) - total_harvest)
            <= CONSERVATION_TOLERANCE_M3
            and abs(
                float(result.metrics["existing_served_m3"])
                + float(result.metrics["module_served_m3"])
                - float(result.metrics["assigned_m3"])
            )
            <= CONSERVATION_TOLERANCE_M3
            for result in results
        ),
        "assigned volume is not conserved",
    )
    check(
        "capacity",
        all(
            float(result.metrics["max_capacity_excess_m3"])
            <= CAPACITY_TOLERANCE_M3
            for result in results
        ),
        "existing capacity is exceeded",
    )
    check(
        "k0_existing_only",
        results[0].selected == ()
        and abs(float(results[0].metrics["module_served_m3"])) <= 1e-9
        and abs(
            float(results[0].metrics["existing_served_m3"])
            - float(results[0].metrics["assigned_m3"])
        )
        <= CONSERVATION_TOLERANCE_M3,
        "k=0 is not existing-only",
    )
    costs = [float(result.metrics["transport_cost_cad"]) for result in results]
    means = [
        float(result.metrics["mean_road_distance_km"]) for result in results
    ]
    share_150 = [
        float(result.metrics["share_ge_150_km"]) for result in results
    ]
    share_300 = [
        float(result.metrics["share_ge_300_km"]) for result in results
    ]
    check(
        "k_monotonicity",
        all(
            costs[index + 1] <= costs[index] + 1e-6
            for index in range(len(costs) - 1)
        )
        and all(
            means[index + 1] <= means[index] + 1e-9
            for index in range(len(means) - 1)
        )
        and all(
            share_150[index + 1] <= share_150[index] + 1e-9
            for index in range(len(share_150) - 1)
        )
        and all(
            share_300[index + 1] <= share_300[index] + 1e-9
            for index in range(len(share_300) - 1)
        ),
        "cost, mean distance or threshold share increased with k",
    )
    check(
        "module_count",
        all(
            result.k == len(result.selected)
            and len(set(result.selected)) == result.k
            and result.metrics["selected_module_count"] == result.k
            and result.metrics["modules_serving"] <= result.k
            for result in results
        )
        and all(
            set(results[index].selected) <= set(results[index + 1].selected)
            for index in range(len(results) - 1)
        ),
        "selected module count is inconsistent",
    )
    candidate_node_ids = {
        candidate.node.node_id for candidate in candidates
    }
    check(
        "module_sources",
        all(
            candidate.module_id.startswith(f"{MODULE_ID_PREFIX}|")
            and candidate.node.node_id in candidate_node_ids
            and candidate.node.harvest_m3 > 0.0
            for candidate in candidates
        ),
        "candidate module source is not a positive supply node",
    )
    check(
        "candidate_factor_coverage",
        all(
            candidate.node.province in factors
            and math.isclose(
                candidate.road_factor,
                float(factors[candidate.node.province]),
                rel_tol=1e-12,
                abs_tol=0.0,
            )
            for candidate in candidates
        ),
        "candidate road factor coverage is incomplete",
    )
    check(
        "excluded_candidates",
        set(excluded_candidates) <= {"NT"}
        and (
            math.fsum(
                float(entry["volume_m3"])
                for entry in excluded_candidates.values()
            )
            / total_harvest
            < 1e-3
        ),
        "unexpected candidate province exclusion",
    )
    check(
        "cost_arithmetic",
        all(
            abs(
                float(result.metrics["transport_cost_cad"])
                - (
                    line.intercept_cad_m3
                    * float(result.metrics["assigned_m3"])
                    + line.slope_cad_m3_km
                    * float(result.metrics["road_distance_m3_km"])
                )
            )
            <= max(
                COST_TOLERANCE_CAD,
                COST_RELATIVE_TOLERANCE
                * float(result.metrics["transport_cost_cad"]),
            )
            and abs(
                float(result.metrics["transport_cost_cad_m3"])
                - float(result.metrics["transport_cost_cad"])
                / float(result.metrics["assigned_m3"])
            )
            <= 1e-9
            and abs(
                float(result.metrics["transport_cost_cad_gj"])
                - float(result.metrics["transport_cost_cad"])
                / float(result.metrics["transport_energy_gj"])
            )
            <= 1e-9
            and abs(
                float(result.metrics["transport_energy_gj"])
                - float(result.metrics["assigned_m3"])
                * v10.ENERGY_GJ_PER_M3
            )
            <= max(1e-6, 1e-9 * float(result.metrics["transport_energy_gj"]))
            and abs(
                float(result.metrics["mean_road_distance_km"])
                - float(result.metrics["road_distance_m3_km"])
                / float(result.metrics["assigned_m3"])
            )
            <= 1e-9
            for result in results
        ),
        "transport cost arithmetic is inconsistent",
    )
    check(
        "row_cost_arithmetic",
        all(
            abs(
                float(row["transport_cost_cad"])
                - float(row["assigned_m3"])
                * (
                    line.intercept_cad_m3
                    + line.slope_cad_m3_km * float(row["road_distance_km"])
                )
            )
            <= max(
                COST_TOLERANCE_CAD,
                COST_RELATIVE_TOLERANCE
                * float(row["transport_cost_cad"]),
            )
            for row in node_assignment_k20
        ),
        "node assignment row cost arithmetic is inconsistent",
    )
    check(
        "assignment_conservation",
        abs(
            math.fsum(
                float(row["assigned_m3"]) for row in node_assignment_k0
            )
            - total_harvest
        )
        <= CONSERVATION_TOLERANCE_M3
        and abs(
            math.fsum(
                float(row["assigned_m3"]) for row in node_assignment_k20
            )
            - total_harvest
        )
        <= CONSERVATION_TOLERANCE_M3,
        "node assignment volume is not conserved",
    )
    node_harvest = {
        node.node_id: float(node.harvest_m3) for node in positive_nodes
    }
    for label, rows in (
        ("k0", node_assignment_k0),
        ("k20", node_assignment_k20),
    ):
        grouped: dict[str, float] = defaultdict(float)
        for row in rows:
            grouped[str(row["node_id"])] += float(row["assigned_m3"])
        check(
            f"per_node_conservation_{label}",
            set(grouped) == set(node_harvest)
            and all(
                abs(grouped[node_id] - harvest)
                <= CONSERVATION_TOLERANCE_M3
                for node_id, harvest in node_harvest.items()
            ),
            f"{label} node volumes do not conserve",
        )
    check(
        "module_row_volume",
        all(
            abs(
                math.fsum(
                    float(row["module_served_m3"])
                    for row in module_rows
                    if int(row["k"]) == result.k
                )
                - float(result.metrics["module_served_m3"])
            )
            <= CONSERVATION_TOLERANCE_M3
            for result in results
        ),
        "module location volume does not match tradeoff volume",
    )
    for result in results:
        if result.k == 0:
            continue
        shares = [
            float(row["module_served_share_of_module_total"])
            for row in module_rows
            if int(row["k"]) == result.k
        ]
        check(
            f"module_share_sum_{result.k}",
            abs(math.fsum(shares) - 1.0) <= 1e-9
            if float(result.metrics["module_served_m3"]) > 0.0
            else True,
            "module shares do not sum to one",
        )
    all_rows = (
        list(tradeoff_rows)
        + list(module_rows)
        + list(node_assignment_k0)
        + list(node_assignment_k20)
        + list(existing_service_rows)
    )
    check(
        "labels",
        all(
            row["evidence_label"] == EVIDENCE_LABEL
            and row["sensitivity_label"] == SENSITIVITY_LABEL
            and row["investment_advice_label"] == INVESTMENT_ADVICE_LABEL
            and str(row["observed"]) in {"False", "false"}
            and str(row["policy_grade"]) in {"False", "false"}
            for row in all_rows
        ),
        "one or more output rows carry unexpected labels",
    )
    check(
        "module_capacity_unbounded",
        all(
            row["capacity_status"] == MODULE_CAPACITY_STATUS
            and row["capacity_m3_y"] in {"", None}
            and row["module_class"] == GENERIC_MODULE_CLASS
            and row["class_agnostic"] in {True, "True", "true"}
            for row in module_rows
        ),
        "module rows invent a class capacity or class label",
    )
    check(
        "existing_capacity_fixed",
        len(facilities) == int(v10_reference["counts"]["eligible_facilities"])
        and abs(
            math.fsum(
                float(facility.capacity.capacity_m3_y)
                for facility in facilities
            )
            - existing_capacity_m3_y
        )
        <= 1e-6,
        "existing facility capacity is not the fixed v3 universe",
    )
    check(
        "v10_reproduction",
        bool(v10_reproduction["passed"]),
        "v10 LP was not reproduced",
    )
    check(
        "v10_baseline",
        abs(
            float(v10_reference["v10_primary"]["assigned_m3"])
            - total_harvest
        )
        <= CONSERVATION_TOLERANCE_M3,
        "v10 baseline volume does not match",
    )
    check(
        "shares_and_utilization_bounds",
        all(
            0.0 <= float(result.metrics["existing_served_share"]) <= 1.0
            and 0.0 <= float(result.metrics["module_served_share"]) <= 1.0
            and 0.0 <= float(result.metrics["share_ge_150_km"]) <= 1.0
            and 0.0 <= float(result.metrics["share_ge_300_km"]) <= 1.0
            and 0.0 <= float(result.metrics["cross_border_share"]) <= 1.0
            and 0.0 <= float(result.metrics["existing_utilization"]) <= 1.0
            for result in results
        ),
        "share or utilization is outside [0, 1]",
    )
    check(
        "no_nan_or_negative",
        all(
            math.isfinite(float(result.metrics[field]))
            and (
                float(result.metrics[field]) >= 0.0
                if field
                not in {
                    "delta_transport_cost_vs_k0_cad",
                    "delta_transport_cost_vs_k0_pct",
                    "delta_mean_road_distance_vs_k0_km",
                    "delta_transport_cost_vs_v10_cad",
                    "delta_transport_cost_vs_v10_pct",
                    "delta_mean_road_distance_vs_v10_km",
                }
                else True
            )
            for result in results
            for field in (
                "assigned_m3",
                "transport_cost_cad",
                "transport_cost_cad_m3",
                "transport_cost_cad_gj",
                "mean_road_distance_km",
                "share_ge_150_km",
                "share_ge_300_km",
                "existing_utilization",
            )
        )
        and all(
            all(
                value == "" or math.isfinite(float(value))
                for key, value in row.items()
                if key
                not in {
                    "selected_module_ids",
                    "module_id",
                    "node_id",
                    "facility_id",
                    "facility_name",
                    "facility_class",
                    "facility_kind",
                    "facility_province",
                    "module_node_id",
                    "province",
                    "road_factor_basis",
                    "capacity_status",
                    "capacity_evidence",
                    "evidence_label",
                    "sensitivity_label",
                    "investment_advice_label",
                    "observed",
                    "policy_grade",
                    "siting_method",
                }
                and not isinstance(value, (str, bool))
            )
            for row in tradeoff_rows + module_rows
        ),
        "non-finite or negative metric encountered",
    )
    return {
        "all_passed": not failures,
        "checks": checks,
        "failures": failures,
    }


# ---------------------------------------------------------------------------
# Report.
# ---------------------------------------------------------------------------

def _report(
    summary: Mapping[str, Any],
    tradeoff_rows: Sequence[Mapping[str, Any]],
    module_rows: Sequence[Mapping[str, Any]],
) -> str:
    v10 = summary["baselines"]["v10_existing_only"]["metrics"]
    k0 = summary["baselines"]["k0_existing_only_transport_only"]["metrics"]
    headline = summary["headline"]
    k20 = headline["k20"]
    existing_capacity = float(summary["method"]["existing_capacity_m3_y"])
    lines: list[str] = [
        "# Transport-Only Greenfield Siting Sensitivity V1",
        "",
        "Status: **transport_only_siting_screen** — `not_investment_advice`.",
        "Evidence label: `model_estimate`; `observed=false`; `policy_grade=false`.",
        "",
        "This is a transport-cost screening exercise. It is not investment advice,",
        "not a capital or permitting estimate, and not a landbase or facility-design",
        "claim. No national, policy, UI or official OSeCAN surface is changed.",
        "",
        "## 1. Setup",
        "",
        "- **Demand:** 525 committed RSYC supply nodes; 486 positive-volume nodes;",
        f"  {_num(k0['assigned_m3'] / 1e6, 3)} Mm3/yr.",
        "- **Existing facilities:** capacity v3 with the documented 1.2x provincial",
        f"  floor, held fixed; {summary['counts']['eligible_facilities']} eligible",
        f"  facilities, {_num(existing_capacity / 1e6, 3)} Mm3/yr of capacity.",
        "- **Greenfield candidates:** positive-volume supply nodes in provinces with",
        "  a published road factor. The generic module is class-agnostic and has",
        "  unlimited throughput in the screen; no class capacity, technology, cost,",
        "  product or residue is asserted.",
        f"- **Candidate count:** {summary['counts']['candidate_modules']} nodes.",
        "  NT positive nodes are not module hosts because no NT road factor is",
        "  published; they remain demand nodes and are 0.031% of volume.",
        "- **Objective:** minimise 2026 CAD truck transport cost from the committed",
        "  transport cost v1 base curve (52.5 m3 payload, empty return). No",
        "  local-preference penalty is applied in the k-series.",
        "- **Distances:** haversine x province road factor from",
        "  `facility_road_distance_audit_v2`; no full OSRM routing.",
        "- **Method:** greedy forward k-median/facility-location screening. At each",
        "  k the shortlist is the top 10 candidates by capacity-relaxed distance",
        "  savings, and every trial and every k is re-evaluated with the exact",
        "  capacitated transport LP (HiGHS). This is not a proven global optimum.",
        "",
        "The committed v10 existing-only baseline keeps its 500 km cross-border",
        "preference penalty. The k=0 row is a transport-only re-solve with the same",
        "capacity and the same transport curve but no preference penalty, so its",
        "cost is not directly identical to v10. The independent v10 reproduction",
        (
            "check passes (cost delta "
            f"{v10['transport_cost_cad'] - summary['baselines']['v10_reproduction']['metrics']['transport_cost_cad']:.6f} CAD)."
        ),
        "",
        "## 2. k tradeoff",
        "",
        "| k | modules | cost (B CAD) | CAD/m3 | mean km | median km | p90 km | >=150 km | >=300 km | module share | existing util |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in tradeoff_rows:
        lines.append(
            "| {k} | {modules} | {cost} | {cost_m3} | {mean} | {median} | "
            "{p90} | {ge150} | {ge300} | {module_share} | {util} |".format(
                k=row["k"],
                modules=row["selected_module_count"],
                cost=_billions(float(row["transport_cost_cad"])),
                cost_m3=_num(float(row["transport_cost_cad_m3"]), 2),
                mean=_num(float(row["mean_road_distance_km"]), 1),
                median=_num(float(row["median_road_distance_km"]), 1),
                p90=_num(float(row["p90_road_distance_km"]), 1),
                ge150=_pct(float(row["share_ge_150_km"]), 1),
                ge300=_pct(float(row["share_ge_300_km"]), 1),
                module_share=_pct(float(row["module_served_share"]), 1),
                util=_pct(float(row["existing_utilization"]), 1),
            )
        )
    lines.extend(
        [
            "",
            "## 3. Module locations",
            "",
            "The selected sets are nested prefixes: the module selected at k is",
            "retained at every larger k. The table shows the k=20 locations.",
            "",
            "| module | province | first k | k=20 served (Mm3) | share of assigned | nodes served | mean km |",
            "|---|---|---:|---:|---:|---:|---:|",
        ]
    )
    for row in module_rows:
        if int(row["k"]) != len(K_VALUES) - 1:
            continue
        lines.append(
            "| {module} | {province} | {first_k} | {served} | {share} | "
            "{nodes} | {mean} |".format(
                module=str(row["module_id"]).replace("|", "\\|"),
                province=row["province"],
                first_k=row["first_selected_k"],
                served=_num(float(row["module_served_m3"]) / 1e6, 3),
                share=_pct(
                    float(row["module_served_share_of_assigned"]), 2
                ),
                nodes=row["nodes_served"],
                mean=_num(float(row["mean_road_distance_km"]), 1),
            )
        )
    v10_existing_utilization = float(k0["assigned_m3"]) / existing_capacity
    lines.extend(
        [
            "",
            "## 4. Capacity served and comparison with v10",
            "",
            "| Metric | v10 existing-only | k=0 transport-only | k=20 |",
            "|---|---:|---:|---:|",
            f"| transport cost (B CAD/yr) | {_billions(float(v10['transport_cost_cad']))} | {_billions(float(k0['transport_cost_cad']))} | {_billions(float(k20['transport_cost_cad']))} |",
            f"| transport cost (CAD/m3) | {_num(float(v10['transport_cost_cad_m3']), 2)} | {_num(float(k0['transport_cost_cad_m3']), 2)} | {_num(float(k20['transport_cost_cad_m3']), 2)} |",
            f"| mean road distance (km) | {_num(float(v10['mean_road_distance_km']), 1)} | {_num(float(k0['mean_road_distance_km']), 1)} | {_num(float(k20['mean_road_distance_km']), 1)} |",
            f"| median road distance (km) | {_num(float(v10['median_road_distance_km']), 1)} | {_num(float(k0['median_road_distance_km']), 1)} | {_num(float(k20['median_road_distance_km']), 1)} |",
            f"| p90 road distance (km) | {_num(float(v10['p90_road_distance_km']), 1)} | {_num(float(k0['p90_road_distance_km']), 1)} | {_num(float(k20['p90_road_distance_km']), 1)} |",
            f"| share >=150 km | {_pct(float(v10['share_ge_150_km']), 2)} | {_pct(float(k0['share_ge_150_km']), 2)} | {_pct(float(k20['share_ge_150_km']), 2)} |",
            f"| share >=300 km | {_pct(float(v10['share_ge_300_km']), 2)} | {_pct(float(k0['share_ge_300_km']), 2)} | {_pct(float(k20['share_ge_300_km']), 2)} |",
            f"| cross-border share | {_pct(float(v10['cross_border_share']), 2)} | {_pct(float(k0['cross_border_share']), 2)} | {_pct(float(k20['cross_border_share']), 2)} |",
            f"| existing facilities used | {int(v10['facilities_used'])} | {int(k0['existing_facilities_used'])} | {int(k20['existing_facilities_used'])} |",
            f"| modules serving | 0 | 0 | {int(k20['modules_serving'])} |",
            f"| existing capacity served (Mm3) | {_num(float(k0['assigned_m3']) / 1e6, 3)} | {_num(float(k0['existing_served_m3']) / 1e6, 3)} | {_num(float(k20['existing_served_m3']) / 1e6, 3)} |",
            f"| module volume served (Mm3) | 0.000 | 0.000 | {_num(float(k20['module_served_m3']) / 1e6, 3)} |",
            f"| existing capacity utilization | {_pct(v10_existing_utilization, 2)} | {_pct(float(k0['existing_utilization']), 2)} | {_pct(float(k20['existing_utilization']), 2)} |",
            "",
            "The k=20 screen reduces transport cost by",
            f"**{_billions(headline['cost_reduction_vs_k0_cad'])} CAD/yr**",
            f"({_pct(headline['cost_reduction_vs_k0_pct'] / 100.0, 2)}) versus the",
            "transport-only existing-only baseline, and by",
            f"**{_billions(headline['cost_reduction_vs_v10_cad'])} CAD/yr**",
            f"({_pct(headline['cost_reduction_vs_v10_pct'] / 100.0, 2)}) versus",
            "committed v10. The mean road distance falls from",
            f"{_num(float(k0['mean_road_distance_km']), 1)} km at k=0 to",
            f"{_num(float(k20['mean_road_distance_km']), 1)} km at k=20.",
            "",
            "## 5. Diminishing returns and screening diagnostics",
            "",
            (
                "- First k with marginal cost reduction below 1% of the k=0 "
                f"cost: **{headline['first_k_marginal_below_1pct']}**."
            ),
            f"- k=20 module volume share: **{_pct(float(k20['module_served_share']), 2)}**.",
            f"- k=20 existing capacity utilization: **{_pct(float(k20['existing_utilization']), 2)}**.",
            f"- k=20 capacity-relaxed lower-bound gap: **{_pct(float(tradeoff_rows[-1]['capacity_relaxed_gap_pct']) / 100.0, 2)}**.",
            "  The bound ignores existing capacity and is not an optimality gap.",
            "- The selected sets are greedy prefixes, not a global optimum; no",
            "  optimality-gap claim is made.",
            "",
            "## 6. Labels and caveats",
            "",
            "- `transport_only_siting_screen`; `not_investment_advice`;",
            "  `model_estimate`; `observed=false`; `policy_grade=false`.",
            "- No capital, operating, permitting, landbase, procurement or",
            "  facility-design claim is made.",
            "- The generic module is a class-agnostic transport sink with unlimited",
            "  throughput in the screen. No class-specific capacity is invented.",
            "- Distances are haversine x province-factor screening distances, not",
            "  observed routes for every node-module pair.",
            "- Transport cost excludes feedstock, processing, conversion, handling,",
            "  storage and product revenue.",
            "- The k-series has no local-preference penalty. Cross-border flows are",
            "  therefore unpenalised and are reported, not restricted.",
            "- Existing capacity v3 and the 1.2x provincial floor are unchanged.",
            "- NT positive nodes are not candidate module hosts because no NT road",
            "  factor is published; their 0.031% of volume remains in the demand set.",
            "- No national claim, policy claim, UI change or official OSeCAN change",
            "  is made.",
            "",
            "## 7. Reproduce",
            "",
            "```bash",
            ".venv/bin/python -m workflows.national.facilities.greenfield_siting_sensitivity_v1",
            "```",
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
    """Build the transport-only greenfield siting sensitivity."""

    deletion_checker()
    generated_utc = _utc_now()
    v10_reference = _load_v10_reference()
    nodes = _load_nodes()
    positive_nodes = tuple(
        node for node in nodes if node.harvest_m3 > 0.0
    )
    facilities, factors = _load_facilities(v10_reference)
    candidates, excluded_candidates = _build_candidates(
        positive_nodes, factors
    )
    parameters = v9.load_transport_parameters()
    line = v10._cost_line(parameters)

    existing_distance, module_distance = _distance_matrices(
        positive_nodes, facilities, candidates, factors
    )
    capacities = np.array(
        [
            float(facility.capacity.capacity_m3_y)
            for facility in facilities
        ],
        dtype=float,
    )
    harvest = np.array(
        [node.harvest_m3 for node in positive_nodes], dtype=float
    )
    total_harvest_m3 = math.fsum(node.harvest_m3 for node in nodes)
    existing_capacity_m3_y = math.fsum(float(capacity) for capacity in capacities)

    results, lp_solves = _greedy_select(
        positive_nodes,
        facilities,
        candidates,
        existing_distance,
        module_distance,
        capacities,
        harvest,
        factors,
        line,
        total_harvest_m3,
        existing_capacity_m3_y,
    )
    v10_reproduction = _reproduce_v10(
        positive_nodes,
        facilities,
        existing_distance,
        capacities,
        harvest,
        factors,
        line,
        total_harvest_m3,
        existing_capacity_m3_y,
        v10_reference,
    )
    lp_solves += 1
    lower_bounds = _build_lower_bounds(
        results,
        existing_distance,
        module_distance,
        harvest,
        line,
    )
    tradeoff_rows = _build_tradeoff_rows(
        results, v10_reference["v10_primary"], lower_bounds
    )
    module_rows = _build_module_rows(results, candidates)
    existing_service_rows = _build_existing_service_rows(results, facilities)
    node_assignment_k0 = _build_node_assignment_rows(results[0])
    node_assignment_k20 = _build_node_assignment_rows(results[-1])

    validation = validate(
        nodes=nodes,
        positive_nodes=positive_nodes,
        facilities=facilities,
        candidates=candidates,
        factors=factors,
        excluded_candidates=excluded_candidates,
        results=results,
        tradeoff_rows=tradeoff_rows,
        module_rows=module_rows,
        node_assignment_k0=node_assignment_k0,
        node_assignment_k20=node_assignment_k20,
        existing_service_rows=existing_service_rows,
        v10_reference=v10_reference,
        v10_reproduction=v10_reproduction,
        line=line,
        existing_capacity_m3_y=existing_capacity_m3_y,
    )

    k0_metrics = results[0].metrics
    k20_metrics = results[-1].metrics
    v10_metrics = dict(v10_reference["v10_primary"])
    v10_metrics["facilities_used"] = float(
        v10_reference["v10_primary"]["facilities_used"]
    )
    v10_metrics["modules_serving"] = 0.0
    v10_metrics["module_served_m3"] = 0.0
    v10_metrics["module_served_share"] = 0.0
    v10_metrics["existing_served_m3"] = float(
        v10_reference["v10_primary"]["assigned_m3"]
    )
    v10_metrics["existing_utilization"] = (
        float(v10_reference["v10_primary"]["assigned_m3"])
        / existing_capacity_m3_y
    )

    k0_cost = float(k0_metrics["transport_cost_cad"])
    k20_cost = float(k20_metrics["transport_cost_cad"])
    v10_cost = float(v10_metrics["transport_cost_cad"])
    first_diminishing = next(
        (
            int(row["k"])
            for row in tradeoff_rows[1:]
            if float(row["marginal_transport_cost_reduction_cad"])
            < 0.01 * k0_cost
        ),
        None,
    )
    headline = {
        "k20": dict(k20_metrics),
        "cost_reduction_vs_k0_cad": k0_cost - k20_cost,
        "cost_reduction_vs_k0_pct": (
            100.0 * (k0_cost - k20_cost) / k0_cost if k0_cost > 0.0 else 0.0
        ),
        "cost_reduction_vs_v10_cad": v10_cost - k20_cost,
        "cost_reduction_vs_v10_pct": (
            100.0 * (v10_cost - k20_cost) / v10_cost
            if v10_cost > 0.0
            else 0.0
        ),
        "mean_reduction_vs_k0_km": (
            float(k0_metrics["mean_road_distance_km"])
            - float(k20_metrics["mean_road_distance_km"])
        ),
        "mean_reduction_vs_v10_km": (
            float(v10_metrics["mean_road_distance_km"])
            - float(k20_metrics["mean_road_distance_km"])
        ),
        "first_k_marginal_below_1pct": first_diminishing,
        "module_locations": len(
            [row for row in module_rows if int(row["k"]) == len(K_VALUES) - 1]
        ),
    }
    comparison = {
        "k0_vs_v10": {
            "transport_cost_delta_cad": k0_cost - v10_cost,
            "transport_cost_delta_pct": (
                100.0 * (k0_cost / v10_cost - 1.0)
                if v10_cost > 0.0
                else 0.0
            ),
            "mean_road_distance_delta_km": (
                float(k0_metrics["mean_road_distance_km"])
                - float(v10_metrics["mean_road_distance_km"])
            ),
            "share_ge_150_delta": (
                float(k0_metrics["share_ge_150_km"])
                - float(v10_metrics["share_ge_150_km"])
            ),
            "share_ge_300_delta": (
                float(k0_metrics["share_ge_300_km"])
                - float(v10_metrics["share_ge_300_km"])
            ),
        },
        "k20_vs_v10": {
            "transport_cost_delta_cad": k20_cost - v10_cost,
            "transport_cost_delta_pct": (
                100.0 * (k20_cost / v10_cost - 1.0)
                if v10_cost > 0.0
                else 0.0
            ),
            "mean_road_distance_delta_km": (
                float(k20_metrics["mean_road_distance_km"])
                - float(v10_metrics["mean_road_distance_km"])
            ),
            "share_ge_150_delta": (
                float(k20_metrics["share_ge_150_km"])
                - float(v10_metrics["share_ge_150_km"])
            ),
            "share_ge_300_delta": (
                float(k20_metrics["share_ge_300_km"])
                - float(v10_metrics["share_ge_300_km"])
            ),
        },
    }

    labels = {
        "sensitivity_label": SENSITIVITY_LABEL,
        "investment_advice_label": INVESTMENT_ADVICE_LABEL,
        "evidence_label": EVIDENCE_LABEL,
        "observed": OBSERVED,
        "policy_grade": POLICY_GRADE,
        "national_claim": False,
        "official_osecan_changed": False,
        "ui_changed": False,
        "capital_cost_claim": False,
        "permitting_claim": False,
        "landbase_claim": False,
        "class_specific_capacity_claim": False,
        "investment_advice": False,
        "policy_claim": False,
        "greenfield_siting": True,
        "siting_optimization": "screening_heuristic",
        "module_class": GENERIC_MODULE_CLASS,
        "module_capacity": MODULE_CAPACITY_STATUS,
    }
    method = {
        "engine": (
            "greedy k-median/facility-location screening with exact "
            "capacitated transport LP re-evaluation (HiGHS)"
        ),
        "k_values": list(K_VALUES),
        "candidate_basis": (
            "positive-volume supply nodes in provinces with a published "
            "road factor"
        ),
        "candidate_modules": len(candidates),
        "excluded_candidate_provinces": excluded_candidates,
        "existing_facilities": len(facilities),
        "existing_capacity_m3_y": existing_capacity_m3_y,
        "capacity_basis": (
            "national_facility_capacity_v3 with the documented 1.2x "
            "provincial floor, held fixed"
        ),
        "module_basis": (
            "class-agnostic generic module at a candidate supply node; "
            "unlimited throughput in the screen; no class capacity, "
            "technology, cost, product or residue asserted"
        ),
        "objective": (
            "minimise 2026 CAD truck transport cost from transport_cost_v1 "
            "base curve (52.5 m3 payload, empty return); no local-preference "
            "penalty in the k-series"
        ),
        "distance_method": (
            "haversine x province road factor from "
            "facility_road_distance_audit_v2; candidate module uses its node "
            "province factor; no full OSRM routing"
        ),
        "full_osrm": False,
        "local_preference_penalty_km": 0.0,
        "transport_cost_line": {
            "intercept_cad_m3": line.intercept_cad_m3,
            "slope_cad_m3_km": line.slope_cad_m3_km,
            "penalty_cad_m3": line.penalty_cad_m3,
        },
        "transport_payload_m3": v10.PAYLOAD_M3,
        "transport_energy_gj_per_m3": v10.ENERGY_GJ_PER_M3,
        "heuristic": (
            "greedy forward selection; at each k the shortlist is the top "
            f"{SHORTLIST_SIZE} candidates by capacity-relaxed distance "
            "savings; every trial and every k is re-evaluated with the exact "
            "capacitated transport LP"
        ),
        "shortlist_size": SHORTLIST_SIZE,
        "optimality": (
            "heuristic screening result; not a proven global optimum; no "
            "optimality-gap claim"
        ),
        "lp_solver": "scipy.optimize.linprog(method='highs', presolve=False)",
        "lp_solves": lp_solves,
        "v10_existing_only_baseline": (
            "committed v10 primary assignment, including its 500 km "
            "cross-border preference penalty"
        ),
        "k0_existing_only_baseline": (
            "existing-only re-solve with the same transport curve and "
            "capacity but zero local-preference penalty"
        ),
    }
    counts = {
        "input_nodes": len(nodes),
        "positive_nodes": len(positive_nodes),
        "zero_volume_nodes": len(nodes) - len(positive_nodes),
        "candidate_modules": len(candidates),
        "excluded_candidate_provinces": len(excluded_candidates),
        "eligible_facilities": len(facilities),
        "k_values": len(K_VALUES),
        "module_location_rows": len(module_rows),
        "node_assignment_rows_k0": len(node_assignment_k0),
        "node_assignment_rows_k20": len(node_assignment_k20),
        "existing_service_rows": len(existing_service_rows),
        "lp_solves": lp_solves,
    }
    baselines = {
        "v10_existing_only": {
            "source": str(V10_SUMMARY_JSON.relative_to(REPO_ROOT)),
            "metrics": v10_metrics,
        },
        "k0_existing_only_transport_only": {
            "metrics": dict(k0_metrics),
        },
        "v10_reproduction": v10_reproduction,
        "comparison": comparison,
    }
    caveats = [
        (
            "Transport-only screening: no capital, operating, permitting, "
            "landbase, procurement or facility-design claim."
        ),
        (
            "The generic module is class-agnostic with unlimited throughput "
            "in the screen; no class-specific capacity, technology, cost, "
            "product or residue is asserted."
        ),
        (
            "The k-series is a greedy heuristic, not a globally optimal "
            "siting solution; no optimality-gap claim is made."
        ),
        (
            "Distances are haversine x province-factor screening distances, "
            "not observed routes for every node-module pair; no full OSRM "
            "routing."
        ),
        (
            "Transport cost excludes feedstock, processing, conversion, "
            "handling, storage and product revenue."
        ),
        (
            "The k-series has no local-preference penalty, so cross-border "
            "flows are unpenalised and are reported, not restricted."
        ),
        (
            "Existing capacity v3 and the 1.2x provincial floor are "
            "unchanged; the existing facility set is fixed."
        ),
        (
            "NT positive nodes are not candidate module hosts because no NT "
            "road factor is published; their 0.031% of volume remains in the "
            "demand set."
        ),
        (
            "No national claim, policy claim, UI change or official OSeCAN "
            "change is made."
        ),
        "This artifact is not investment advice.",
    ]
    summary: dict[str, Any] = {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": generated_utc,
        "status": "complete",
        "labels": labels,
        "method": method,
        "counts": counts,
        "baselines": baselines,
        "headline": headline,
        "k_tradeoff": tradeoff_rows,
        "module_locations": module_rows,
        "existing_capacity": {
            "capacity_m3_y": existing_capacity_m3_y,
            "served_k0_m3": float(k0_metrics["existing_served_m3"]),
            "served_k20_m3": float(k20_metrics["existing_served_m3"]),
            "utilization_k0": float(k0_metrics["existing_utilization"]),
            "utilization_k20": float(k20_metrics["existing_utilization"]),
            "facilities": len(facilities),
        },
        "validation": validation,
        "caveats": caveats,
        "reproduce": (
            ".venv/bin/python -m "
            "workflows.national.facilities.greenfield_siting_sensitivity_v1"
        ),
    }
    summary["report"] = _report(summary, tradeoff_rows, module_rows)

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(output_dir / "k_tradeoff.csv", TRADEOFF_FIELDS, tradeoff_rows)
    _write_csv(output_dir / "module_locations.csv", MODULE_FIELDS, module_rows)
    _write_csv(
        output_dir / "node_assignment_k0.csv",
        NODE_ASSIGNMENT_FIELDS,
        node_assignment_k0,
    )
    _write_csv(
        output_dir / "node_assignment_k20.csv",
        NODE_ASSIGNMENT_FIELDS,
        node_assignment_k20,
    )
    _write_csv(
        output_dir / "existing_capacity_service_k0_k20.csv",
        EXISTING_SERVICE_FIELDS,
        existing_service_rows,
    )
    (output_dir / "GREENFIELD_SITING_SENSITIVITY_V1.md").write_text(
        summary["report"] + "\n", encoding="utf-8"
    )
    _write_json(output_dir / "summary.json", summary)

    input_paths = (
        V10_SUMMARY_JSON,
        V10_ASSIGNMENT_CSV,
        SUPPLY_NODES_CSV,
        SUPPLY_NODE_SUMMARY_JSON,
        CAPACITY_CSV,
        CAPACITY_SUMMARY,
        HARVEST_CSV,
        BOUNDARY_GEOJSON,
        FACTORS_CSV,
        FACTORS_SUMMARY,
        TRANSPORT_PARAMETERS_CSV,
        TRANSPORT_CURVES_CSV,
    )
    inputs = {
        str(path.relative_to(REPO_ROOT)): v9._sha256(path)
        for path in input_paths
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
        "labels": labels,
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
                "k20_transport_cost_cad": summary["headline"]["k20"][
                    "transport_cost_cad"
                ],
                "k20_mean_road_distance_km": summary["headline"]["k20"][
                    "mean_road_distance_km"
                ],
                "k20_module_served_share": summary["headline"]["k20"][
                    "module_served_share"
                ],
                "validation": summary["validation"]["all_passed"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
