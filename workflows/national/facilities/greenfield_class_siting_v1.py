"""Class-specific transport-only greenfield siting screen v1.

The generic greenfield siting sensitivity v1 places class-agnostic modules at
supply nodes.  This artifact asks the narrower class-specific question at the
119 Mm3 case:

* split each committed supply-node harvest weight into SAW/PULP/PANEL/PELLET/
  BIO feedstock using the v5.1 E1/E2c province/class feedstock shares;
* keep the existing capacity-v3 facilities fixed, partitioned by class and
  capacity, including the documented 1.2x provincial floor;
* allow a class-specific module at any positive class feedstock node in a
  province with a published road factor;
* make each module unlimited-throughput in the screen, so no class capacity is
  invented;
* for k = 0..5 modules per class, choose the module set and the class
  node-to-facility flow that minimise 2026 CAD truck transport cost.

The k-series is a greedy forward k-median/facility-location screen with exact
capacitated transport-LP re-evaluation (HiGHS), not a proven global optimum.
The class split is a proportional screening allocation of node harvest, not a
mass-balance routing of harvest residue.

Labels: ``transport_only_siting_screen`` / ``not_investment_advice`` /
``model_estimate`` / ``observed=false`` / ``policy_grade=false``.  No capital,
permitting, technology, landbase, class-capacity, national, policy, UI or
official OSeCAN claim is made.  A ``D`` in either the parent or data repository
git status halts the build.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from workflows.national.facilities import (
    e1_e2c_coefficients as e1e2c,
)
from workflows.national.facilities import (
    greenfield_siting_sensitivity_v1 as generic,
)
from workflows.national.facilities import (
    national_facility_assignment_v9 as v9,
)
from workflows.national.facilities import (
    national_facility_assignment_v10 as v10,
)
from workflows.national.facilities.national_facility_assignment import (
    haversine_km,
)

REPO_ROOT = v9.REPO_ROOT
DATA_ROOT = v9.DATA_ROOT
PRODUCT_CHAIN_DIR = (
    DATA_ROOT / "derived/national/facilities/national_product_chain_v5"
)
PRODUCT_CHAIN_CSV = PRODUCT_CHAIN_DIR / "product_chain_by_province.csv"
PRODUCT_CHAIN_SUMMARY = PRODUCT_CHAIN_DIR / "summary.json"
GENERIC_DIR = (
    DATA_ROOT / "derived/national/facilities/greenfield_siting_sensitivity_v1"
)
GENERIC_SUMMARY_JSON = GENERIC_DIR / "summary.json"
GENERIC_TRADEOFF_CSV = GENERIC_DIR / "k_tradeoff.csv"
V10_SUMMARY_JSON = (
    DATA_ROOT
    / "derived/national/facilities/national_facility_assignment_v10"
    / "summary.json"
)
OUTPUT_DIR = (
    DATA_ROOT / "derived/national/facilities/greenfield_class_siting_v1"
)

ARTIFACT = "greenfield_class_siting_v1"
ARTIFACT_VERSION = "1.0.0"
REPORT_NAME = "GREENFIELD_CLASS_SITING_V1.md"
MODULE_PATH = "workflows.national.facilities.greenfield_class_siting_v1"
TEST_PATH = "tests/test_greenfield_class_siting_v1.py"

CLASS_ORDER: tuple[str, ...] = ("SAW", "PULP", "PANEL", "PELLET", "BIO")
CLASS_FEEDSTOCK_COLUMN = {
    "SAW": "sawlog_feedstock_m3",
    "PULP": "pulp_feedstock_m3",
    "PANEL": "panel_feedstock_m3",
    "PELLET": "pellet_feedstock_m3",
    "BIO": "bio_feedstock_m3",
}
PROVINCES = e1e2c.PROVINCES

K_VALUES = tuple(range(6))
SHORTLIST_SIZE = 10
MODULE_ID_PREFIX = "GFC"
MODULE_NAME = "Class-specific greenfield module (transport-only screen)"
MODULE_CAPACITY_STATUS = "unlimited_screen"
MODULE_CAPACITY_EVIDENCE = "unlimited_screen_no_class_capacity_claim"

EXPECTED_NODE_COUNT = 525
EXPECTED_POSITIVE_NODE_COUNT = 486
EXPECTED_HARVEST_M3 = 118_977_339.1
EXPECTED_FACILITY_COUNT = 591
CONSERVATION_TOLERANCE_M3 = 1e-3
CAPACITY_TOLERANCE_M3 = 1e-3
VOLUME_EPSILON_M3 = generic.VOLUME_EPSILON_M3
COST_TOLERANCE_CAD = generic.COST_TOLERANCE_CAD
COST_RELATIVE_TOLERANCE = generic.COST_RELATIVE_TOLERANCE

SENSITIVITY_LABEL = generic.SENSITIVITY_LABEL
INVESTMENT_ADVICE_LABEL = generic.INVESTMENT_ADVICE_LABEL
EVIDENCE_LABEL = generic.EVIDENCE_LABEL
OBSERVED = generic.OBSERVED
POLICY_GRADE = generic.POLICY_GRADE

CLASS_TRADEOFF_FIELDS = [
    "class",
    "k",
    "selected_module_count",
    "modules_serving",
    "class_feedstock_m3",
    "assigned_m3",
    "existing_served_m3",
    "existing_served_share",
    "existing_facilities_total",
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
    "selected_module_ids",
    "siting_method",
    "evidence_label",
    "sensitivity_label",
    "investment_advice_label",
    "observed",
    "policy_grade",
]

AGGREGATE_FIELDS = [
    "k_per_class",
    "total_modules",
    "classes",
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
    "modules_serving",
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
    "generic_same_k",
    "generic_same_k_cost_cad",
    "generic_same_k_mean_road_distance_km",
    "delta_vs_generic_same_k_cad",
    "delta_vs_generic_same_k_pct",
    "generic_matched_k",
    "generic_matched_exact",
    "generic_matched_cost_cad",
    "generic_matched_mean_road_distance_km",
    "delta_vs_generic_matched_cad",
    "delta_vs_generic_matched_pct",
    "delta_vs_generic_matched_mean_km",
    "siting_method",
    "evidence_label",
    "sensitivity_label",
    "investment_advice_label",
    "observed",
    "policy_grade",
]

MODULE_FIELDS = [
    "class",
    "k",
    "selection_rank",
    "first_selected_k",
    "module_id",
    "node_id",
    "province",
    "lat",
    "lon",
    "module_class",
    "class_specific",
    "capacity_status",
    "capacity_m3_y",
    "serving",
    "module_served_m3",
    "module_served_share_of_class_assigned",
    "module_served_share_of_class_module_total",
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
    "class",
    "k",
    "node_id",
    "node_province",
    "node_lat",
    "node_lon",
    "node_harvest_m3",
    "class_share",
    "node_class_feedstock_m3",
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
    "class",
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

SPLIT_FIELDS = [
    "province",
    "province_name",
    "facility_class",
    "v5_class_feedstock_m3",
    "v5_total_feedstock_m3",
    "province_harvest_m3",
    "class_share",
    "class_harvest_split_m3",
    "share_source",
    "evidence_label",
    "sensitivity_label",
    "investment_advice_label",
    "observed",
    "policy_grade",
]

GENERIC_COMPARISON_FIELDS = [
    "comparison_basis",
    "k_per_class",
    "class_total_modules",
    "generic_k",
    "module_count_exact",
    "class_transport_cost_cad",
    "generic_transport_cost_cad",
    "delta_transport_cost_cad",
    "delta_transport_cost_pct",
    "class_mean_road_distance_km",
    "generic_mean_road_distance_km",
    "delta_mean_road_distance_km",
    "class_module_served_share",
    "generic_module_served_share",
    "delta_module_served_share",
    "class_share_ge_150_km",
    "generic_share_ge_150_km",
    "class_share_ge_300_km",
    "generic_share_ge_300_km",
    "note",
    "evidence_label",
    "sensitivity_label",
    "investment_advice_label",
    "observed",
    "policy_grade",
]


class ClassSitingError(generic.GreenfieldSitingError):
    """Raised when a class-specific siting invariant fails."""


@dataclass(frozen=True, slots=True)
class ProvinceShare:
    province: str
    province_name: str
    harvest_m3: float
    total_feedstock_m3: float
    class_feedstock_m3: Mapping[str, float]
    class_share: Mapping[str, float]


@dataclass(frozen=True, slots=True)
class ClassNode:
    node: v10.SupplyNode
    class_m3: float
    share: float


@dataclass(frozen=True, slots=True)
class CandidateModule:
    candidate_index: int
    node: v10.SupplyNode
    class_m3: float
    road_factor: float
    module_id: str


@dataclass(frozen=True, slots=True)
class SitingResult:
    class_name: str
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


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_csv(
    path: Path, fields: Sequence[str], rows: Sequence[Mapping[str, Any]]
) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields), lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {field: generic._csv_value(row.get(field, "")) for field in fields}
            )


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


_num = generic._num
_pct = generic._pct
_billions = generic._billions


# ---------------------------------------------------------------------------
# Inputs: class shares, nodes, facilities, candidates.
# ---------------------------------------------------------------------------

def _read_rows(path: Path, label: str) -> list[dict[str, str]]:
    if not path.is_file():
        raise ClassSitingError(f"{label} is missing: {path}")
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ClassSitingError(f"{label} is empty: {path}")
    return rows


def _finite_nonnegative(value: Any, label: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as error:
        raise ClassSitingError(f"{label} is not numeric: {value!r}") from error
    if not math.isfinite(parsed) or parsed < 0.0:
        raise ClassSitingError(
            f"{label} is not finite and nonnegative: {parsed!r}"
        )
    return parsed


def _load_class_shares(nodes: Sequence[v10.SupplyNode]) -> dict[str, ProvinceShare]:
    summary = json.loads(PRODUCT_CHAIN_SUMMARY.read_text(encoding="utf-8"))
    if summary.get("artifact") != "national_product_chain_v5":
        raise ClassSitingError(
            f"{PRODUCT_CHAIN_SUMMARY}: unexpected product-chain artifact"
        )
    if summary.get("status") not in {"pass", "complete"}:
        raise ClassSitingError(
            f"{PRODUCT_CHAIN_SUMMARY}: product chain is not complete"
        )
    labels = summary.get("labels", {})
    if labels.get("model_estimate") is not True or labels.get("observed") is not False:
        raise ClassSitingError(
            f"{PRODUCT_CHAIN_SUMMARY}: product-chain labels are unexpected"
        )

    rows = _read_rows(PRODUCT_CHAIN_CSV, "v5.1 class feedstock table")
    by_province: dict[str, dict[str, str]] = {}
    for row in rows:
        province = str(row["province"]).strip().upper()
        if province in by_province:
            raise ClassSitingError(f"product chain repeats province {province}")
        by_province[province] = row
    if set(by_province) != set(PROVINCES):
        raise ClassSitingError(
            "product-chain province set is not the 13 jurisdictions: "
            f"{sorted(by_province)}"
        )

    node_harvest_by_province: dict[str, float] = defaultdict(float)
    for node in nodes:
        node_harvest_by_province[node.province] += node.harvest_m3
    total_node_harvest = math.fsum(node.harvest_m3 for node in nodes)

    shares: dict[str, ProvinceShare] = {}
    for province in PROVINCES:
        row = by_province[province]
        harvest = _finite_nonnegative(
            row["harvest_m3"], f"{province} product-chain harvest"
        )
        total_feedstock = _finite_nonnegative(
            row["total_feedstock_m3"], f"{province} total feedstock"
        )
        class_feedstock = {
            facility_class: _finite_nonnegative(
                row[CLASS_FEEDSTOCK_COLUMN[facility_class]],
                f"{province}/{facility_class} class feedstock",
            )
            for facility_class in CLASS_ORDER
        }
        class_sum = math.fsum(class_feedstock.values())
        if total_feedstock > 0.0:
            if abs(class_sum - total_feedstock) > 1e-6 * total_feedstock:
                raise ClassSitingError(
                    f"{province}: class feedstock does not sum to total feedstock"
                )
            class_share = {
                facility_class: value / class_sum
                for facility_class, value in class_feedstock.items()
            }
            if abs(math.fsum(class_share.values()) - 1.0) > 1e-9:
                raise ClassSitingError(
                    f"{province}: class shares do not sum to one"
                )
        else:
            if class_sum > 0.0:
                raise ClassSitingError(
                    f"{province}: zero total feedstock but positive class volumes"
                )
            class_share = {facility_class: 0.0 for facility_class in CLASS_ORDER}
        node_harvest = node_harvest_by_province.get(province, 0.0)
        if abs(node_harvest - harvest) > CONSERVATION_TOLERANCE_M3:
            raise ClassSitingError(
                f"{province}: node harvest {node_harvest!r} does not match "
                f"product-chain harvest {harvest!r}"
            )
        shares[province] = ProvinceShare(
            province=province,
            province_name=str(row["province_name"]).strip(),
            harvest_m3=harvest,
            total_feedstock_m3=total_feedstock,
            class_feedstock_m3=class_feedstock,
            class_share=class_share,
        )
    if not math.isclose(
        total_node_harvest, EXPECTED_HARVEST_M3, rel_tol=1e-12, abs_tol=1e-3
    ):
        raise ClassSitingError(
            f"node harvest {total_node_harvest!r} is not the 119 Mm3 case"
        )
    if not math.isclose(
        math.fsum(share.harvest_m3 for share in shares.values()),
        total_node_harvest,
        rel_tol=1e-12,
        abs_tol=1e-3,
    ):
        raise ClassSitingError("product-chain harvest does not match node harvest")
    return shares


def _class_nodes(
    nodes: Sequence[v10.SupplyNode],
    shares: Mapping[str, ProvinceShare],
) -> dict[str, tuple[ClassNode, ...]]:
    result: dict[str, list[ClassNode]] = {
        facility_class: [] for facility_class in CLASS_ORDER
    }
    for node in nodes:
        if node.harvest_m3 <= 0.0:
            continue
        province_share = shares[node.province]
        for facility_class in CLASS_ORDER:
            share = province_share.class_share[facility_class]
            class_m3 = node.harvest_m3 * share
            if class_m3 > 0.0:
                result[facility_class].append(
                    ClassNode(node=node, class_m3=class_m3, share=share)
                )
        node_split = math.fsum(
            node.harvest_m3 * province_share.class_share[facility_class]
            for facility_class in CLASS_ORDER
        )
        if abs(node_split - node.harvest_m3) > 1e-6:
            raise ClassSitingError(
                f"{node.node_id}: class split does not conserve node harvest"
            )
    class_nodes: dict[str, tuple[ClassNode, ...]] = {}
    for facility_class in CLASS_ORDER:
        expected = math.fsum(
            share.harvest_m3 * share.class_share[facility_class]
            for share in shares.values()
        )
        actual = math.fsum(
            class_node.class_m3 for class_node in result[facility_class]
        )
        if (expected > 0.0 and not result[facility_class]) or abs(
            actual - expected
        ) > CONSERVATION_TOLERANCE_M3:
            raise ClassSitingError(
                f"{facility_class}: node split {actual!r} does not match "
                f"province/class total {expected!r}"
            )
        class_nodes[facility_class] = tuple(result[facility_class])
    return class_nodes


def _class_facilities(
    facilities: Sequence[Any],
) -> tuple[dict[str, tuple[Any, ...]], dict[str, np.ndarray]]:
    grouped: dict[str, list[Any]] = {
        facility_class: [] for facility_class in CLASS_ORDER
    }
    for facility in facilities:
        if facility.facility_class not in grouped:
            raise ClassSitingError(
                f"{facility.facility_id}: unexpected class "
                f"{facility.facility_class!r}"
            )
        grouped[facility.facility_class].append(facility)
    by_class: dict[str, tuple[Any, ...]] = {}
    capacities: dict[str, np.ndarray] = {}
    for facility_class in CLASS_ORDER:
        group = tuple(
            sorted(grouped[facility_class], key=lambda item: item.facility_id)
        )
        if not group:
            raise ClassSitingError(f"{facility_class}: no eligible existing facility")
        by_class[facility_class] = group
        capacities[facility_class] = np.array(
            [float(facility.capacity.capacity_m3_y) for facility in group],
            dtype=float,
        )
        if float(capacities[facility_class].sum()) <= 0.0:
            raise ClassSitingError(f"{facility_class}: capacity is not positive")
    return by_class, capacities


def _build_candidates(
    facility_class: str,
    class_nodes: Sequence[ClassNode],
    factors: Mapping[str, float],
) -> tuple[tuple[CandidateModule, ...], dict[str, dict[str, float]]]:
    candidates: list[CandidateModule] = []
    excluded: dict[str, dict[str, float]] = defaultdict(
        lambda: {"nodes": 0.0, "volume_m3": 0.0}
    )
    for class_node in class_nodes:
        factor = factors.get(class_node.node.province)
        if factor is None:
            excluded[class_node.node.province]["nodes"] += 1.0
            excluded[class_node.node.province]["volume_m3"] += class_node.class_m3
            continue
        candidates.append(
            CandidateModule(
                candidate_index=len(candidates),
                node=class_node.node,
                class_m3=class_node.class_m3,
                road_factor=float(factor),
                module_id=(
                    f"{MODULE_ID_PREFIX}|{facility_class}|{class_node.node.node_id}"
                ),
            )
        )
    unexpected = set(excluded) - {"NT"}
    if unexpected:
        raise ClassSitingError(
            f"{facility_class}: class nodes without a road factor in unexpected "
            f"provinces: {sorted(unexpected)}"
        )
    if len(candidates) <= max(K_VALUES):
        raise ClassSitingError(
            f"{facility_class}: only {len(candidates)} candidate modules"
        )
    return tuple(candidates), dict(excluded)


# ---------------------------------------------------------------------------
# Distances and LP.
# ---------------------------------------------------------------------------

def _distance_matrices(
    class_nodes: Sequence[ClassNode],
    facilities: Sequence[Any],
    candidates: Sequence[CandidateModule],
    factors: Mapping[str, float],
) -> tuple[np.ndarray, np.ndarray]:
    existing = np.empty((len(class_nodes), len(facilities)), dtype=float)
    modules = np.empty((len(class_nodes), len(candidates)), dtype=float)
    facility_factors = np.array(
        [float(factors[facility.province]) for facility in facilities]
    )
    candidate_factors = np.array(
        [candidate.road_factor for candidate in candidates]
    )
    for node_index, class_node in enumerate(class_nodes):
        node = class_node.node
        for facility_index, facility in enumerate(facilities):
            distance = (
                haversine_km(
                    node.lat,
                    node.lon,
                    facility.location.lat,
                    facility.location.lon,
                )
                * facility_factors[facility_index]
            )
            if not math.isfinite(distance) or distance < 0.0:
                raise ClassSitingError(
                    f"{node.node_id}->{facility.facility_id}: invalid distance"
                )
            existing[node_index, facility_index] = distance
        for candidate_index, candidate in enumerate(candidates):
            distance = (
                haversine_km(
                    node.lat,
                    node.lon,
                    candidate.node.lat,
                    candidate.node.lon,
                )
                * candidate_factors[candidate_index]
            )
            if not math.isfinite(distance) or distance < 0.0:
                raise ClassSitingError(
                    f"{node.node_id}->{candidate.module_id}: invalid distance"
                )
            modules[node_index, candidate_index] = distance
    return existing, modules


# ---------------------------------------------------------------------------
# Rows and metrics.
# ---------------------------------------------------------------------------

def _build_rows(
    facility_class: str,
    class_nodes: Sequence[ClassNode],
    facilities: Sequence[Any],
    selected_candidates: Sequence[CandidateModule],
    allocations: Sequence[Sequence[tuple[int, float]]],
    distance: np.ndarray,
    factors: Mapping[str, float],
    line: v10.CostLine,
    k: int,
) -> tuple[dict[str, Any], ...]:
    existing_count = len(facilities)
    rows: list[dict[str, Any]] = []
    for node_index, class_node in enumerate(class_nodes):
        node = class_node.node
        for facility_index, volume in allocations[node_index]:
            road_distance = float(distance[node_index, facility_index])
            cost_cad_m3 = (
                line.intercept_cad_m3 + line.slope_cad_m3_km * road_distance
            )
            energy_gj = volume * v10.ENERGY_GJ_PER_M3
            total_cost = volume * cost_cad_m3
            if facility_index < existing_count:
                facility = facilities[facility_index]
                kind = "existing"
                facility_id = facility.facility_id
                facility_name = facility.name
                row_class = facility.facility_class
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
                    facility_index - existing_count
                ]
                kind = "greenfield_module"
                facility_id = candidate.module_id
                facility_name = MODULE_NAME
                row_class = facility_class
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
                    "class": facility_class,
                    "k": k,
                    "node_id": node.node_id,
                    "node_province": node.province,
                    "node_lat": node.lat,
                    "node_lon": node.lon,
                    "node_harvest_m3": node.harvest_m3,
                    "class_share": class_node.share,
                    "node_class_feedstock_m3": class_node.class_m3,
                    "facility_id": facility_id,
                    "facility_name": facility_name,
                    "facility_kind": kind,
                    "facility_class": row_class,
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


def _metrics(
    facility_class: str,
    rows: Sequence[Mapping[str, Any]],
    demand_m3: float,
    existing_capacity_m3_y: float,
    capacities: np.ndarray,
    selected_candidates: Sequence[CandidateModule],
    k: int,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    assigned = math.fsum(float(row["assigned_m3"]) for row in rows)
    if abs(assigned - demand_m3) > CONSERVATION_TOLERANCE_M3:
        raise ClassSitingError(
            f"{facility_class}: assigned volume {assigned!r} does not conserve "
            f"class demand {demand_m3!r}"
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
    module_served = math.fsum(float(row["assigned_m3"]) for row in module_rows)
    if abs(existing_served + module_served - assigned) > CONSERVATION_TOLERANCE_M3:
        raise ClassSitingError(
            f"{facility_class}: existing plus module volume is not conserved"
        )
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
    for candidate in selected_candidates:
        module_stats[candidate.module_id] = {
            "module_id": candidate.module_id,
            "node_id": candidate.node.node_id,
            "province": candidate.node.province,
            "road_factor_applied": candidate.road_factor,
            "served_m3": 0.0,
            "node_ids": set(),
            "volume_distance": 0.0,
            "max_road_distance_km": 0.0,
        }
    for row in module_rows:
        module_id = str(row["facility_id"])
        stats = module_stats.get(module_id)
        if stats is None:
            raise ClassSitingError(
                f"{facility_class}: row module {module_id} was not selected"
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
    modules_serving = sum(
        1
        for stats in module_stats.values()
        if float(stats["served_m3"]) > VOLUME_EPSILON_M3
    )
    metrics: dict[str, Any] = {
        "class": facility_class,
        "k": k,
        "selected_module_count": k,
        "modules_serving": modules_serving,
        "class_feedstock_m3": demand_m3,
        "assigned_m3": assigned,
        "existing_served_m3": existing_served,
        "existing_served_share": existing_served / assigned if assigned > 0.0 else 0.0,
        "existing_facilities_total": len(capacities),
        "existing_facilities_used": len(
            [
                facility_id
                for facility_id, volume in facility_volume.items()
                if volume > VOLUME_EPSILON_M3
            ]
        ),
        "existing_capacity_m3_y": existing_capacity_m3_y,
        "existing_utilization": (
            existing_served / existing_capacity_m3_y
            if existing_capacity_m3_y > 0.0
            else 0.0
        ),
        "binding_facilities": len(binding),
        "binding_volume_m3": math.fsum(
            facility_volume[facility_id] for facility_id in binding
        ),
        "module_served_m3": module_served,
        "module_served_share": module_served / assigned if assigned > 0.0 else 0.0,
        "assignments": len(rows),
        "road_distance_m3_km": volume_distance,
        "mean_road_distance_km": (
            volume_distance / assigned if assigned > 0.0 else 0.0
        ),
        "median_road_distance_km": v10._weighted_quantile(rows, 0.5),
        "p90_road_distance_km": v10._weighted_quantile(rows, 0.9),
        "max_road_distance_km": max(
            (float(row["road_distance_km"]) for row in rows), default=0.0
        ),
        "share_ge_150_km": (
            math.fsum(
                float(row["assigned_m3"])
                for row in rows
                if float(row["road_distance_km"]) >= 150.0
            )
            / assigned
            if assigned > 0.0
            else 0.0
        ),
        "share_ge_300_km": (
            math.fsum(
                float(row["assigned_m3"])
                for row in rows
                if float(row["road_distance_km"]) >= 300.0
            )
            / assigned
            if assigned > 0.0
            else 0.0
        ),
        "cross_border_m3": cross_border,
        "cross_border_share": (
            cross_border / assigned if assigned > 0.0 else 0.0
        ),
        "transport_energy_gj": energy,
        "transport_cost_cad": cost,
        "transport_cost_cad_m3": cost / assigned if assigned > 0.0 else 0.0,
        "transport_cost_cad_gj": cost / energy if energy > 0.0 else 0.0,
        "max_capacity_excess_m3": max_capacity_excess,
        "selected_module_ids": ";".join(
            candidate.module_id for candidate in selected_candidates
        ),
    }
    if max_capacity_excess > CAPACITY_TOLERANCE_M3:
        raise ClassSitingError(
            f"{facility_class}: existing capacity exceeded by "
            f"{max_capacity_excess!r} m3"
        )
    return metrics, module_stats


def _node_distance_vector(
    rows: Sequence[Mapping[str, Any]],
    class_nodes: Sequence[ClassNode],
) -> np.ndarray:
    index_by_node = {
        class_node.node.node_id: index
        for index, class_node in enumerate(class_nodes)
    }
    volume = np.zeros(len(class_nodes), dtype=float)
    volume_distance = np.zeros(len(class_nodes), dtype=float)
    for row in rows:
        node_id = str(row["node_id"])
        try:
            index = index_by_node[node_id]
        except KeyError as error:
            raise ClassSitingError(
                f"row node {node_id!r} is not a class demand node"
            ) from error
        assigned = float(row["assigned_m3"])
        volume[index] += assigned
        volume_distance[index] += assigned * float(row["road_distance_km"])
    if np.any(volume <= 0.0):
        missing = [
            class_nodes[index].node.node_id
            for index in np.nonzero(volume <= 0.0)[0]
        ]
        raise ClassSitingError(
            f"class demand nodes without assigned volume: {missing[:5]}"
        )
    return volume_distance / volume


# ---------------------------------------------------------------------------
# Solution builders.
# ---------------------------------------------------------------------------

def _solve_and_build(
    facility_class: str,
    k: int,
    selected: Sequence[int],
    class_nodes: Sequence[ClassNode],
    facilities: Sequence[Any],
    candidates: Sequence[CandidateModule],
    existing_distance: np.ndarray,
    module_distance: np.ndarray,
    capacities: np.ndarray,
    harvest: np.ndarray,
    factors: Mapping[str, float],
    line: v10.CostLine,
    demand_m3: float,
    existing_capacity_m3_y: float,
) -> SitingResult:
    if len(selected) != k:
        raise ClassSitingError(
            f"{facility_class}: k={k} but {len(selected)} modules selected"
        )
    if selected:
        distance = np.hstack(
            [existing_distance, module_distance[:, list(selected)]]
        )
    else:
        distance = existing_distance
    objective = generic._transport_objective(distance, line)
    solution = generic._solve_lp(
        objective, len(facilities), capacities, harvest
    )
    allocations = generic._tidy_allocations(solution, harvest, capacities)
    selected_candidates = tuple(candidates[index] for index in selected)
    rows = _build_rows(
        facility_class,
        class_nodes,
        facilities,
        selected_candidates,
        allocations,
        distance,
        factors,
        line,
        k,
    )
    metrics, module_stats = _metrics(
        facility_class,
        rows,
        demand_m3,
        existing_capacity_m3_y,
        capacities,
        selected_candidates,
        k,
    )
    return SitingResult(
        class_name=facility_class,
        k=k,
        selected=tuple(int(index) for index in selected),
        rows=rows,
        metrics=metrics,
        module_stats=module_stats,
    )


def _greedy_select(
    facility_class: str,
    class_nodes: Sequence[ClassNode],
    facilities: Sequence[Any],
    candidates: Sequence[CandidateModule],
    existing_distance: np.ndarray,
    module_distance: np.ndarray,
    capacities: np.ndarray,
    harvest: np.ndarray,
    factors: Mapping[str, float],
    line: v10.CostLine,
    demand_m3: float,
    existing_capacity_m3_y: float,
) -> tuple[list[SitingResult], int]:
    baseline = _solve_and_build(
        facility_class,
        0,
        (),
        class_nodes,
        facilities,
        candidates,
        existing_distance,
        module_distance,
        capacities,
        harvest,
        factors,
        line,
        demand_m3,
        existing_capacity_m3_y,
    )
    results = [baseline]
    selected: list[int] = []
    current_distance = _node_distance_vector(baseline.rows, class_nodes)
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
            raise ClassSitingError(
                f"{facility_class}: k={step} requested but no candidates remain"
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
                facility_class,
                step,
                trial_selected,
                class_nodes,
                facilities,
                candidates,
                existing_distance,
                module_distance,
                capacities,
                harvest,
                factors,
                line,
                demand_m3,
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
            raise ClassSitingError(
                f"{facility_class}: k={step} selected no trial module"
            )
        previous_cost = float(results[-1].metrics["transport_cost_cad"])
        if best_cost > previous_cost + max(
            COST_TOLERANCE_CAD, COST_RELATIVE_TOLERANCE * previous_cost
        ):
            raise ClassSitingError(
                f"{facility_class}: k={step} transport cost increased from "
                f"{previous_cost!r} to {best_cost!r}"
            )
        selected.append(best_result.selected[-1])
        results.append(best_result)
        current_distance = _node_distance_vector(
            best_result.rows, class_nodes
        )
    return results, lp_solves


# ---------------------------------------------------------------------------
# Tradeoff, aggregate and comparison rows.
# ---------------------------------------------------------------------------

def _build_class_tradeoff_rows(
    class_name: str, results: Sequence[SitingResult]
) -> list[dict[str, Any]]:
    k0_cost = float(results[0].metrics["transport_cost_cad"])
    k0_mean = float(results[0].metrics["mean_road_distance_km"])
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
            marginal_mean = float(previous["mean_road_distance_km"]) - mean
        row = {
            "class": class_name,
            "k": result.k,
            "selected_module_count": result.k,
            "modules_serving": metrics["modules_serving"],
            "class_feedstock_m3": metrics["class_feedstock_m3"],
            "assigned_m3": metrics["assigned_m3"],
            "existing_served_m3": metrics["existing_served_m3"],
            "existing_served_share": metrics["existing_served_share"],
            "existing_facilities_total": metrics["existing_facilities_total"],
            "existing_facilities_used": metrics["existing_facilities_used"],
            "existing_capacity_m3_y": metrics["existing_capacity_m3_y"],
            "existing_utilization": metrics["existing_utilization"],
            "binding_facilities": metrics["binding_facilities"],
            "binding_volume_m3": metrics["binding_volume_m3"],
            "module_served_m3": metrics["module_served_m3"],
            "module_served_share": metrics["module_served_share"],
            "assignments": metrics["assignments"],
            "road_distance_m3_km": metrics["road_distance_m3_km"],
            "mean_road_distance_km": mean,
            "median_road_distance_km": metrics["median_road_distance_km"],
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
            "selected_module_ids": metrics["selected_module_ids"],
            "siting_method": "greedy_forward_exact_lp_shortlist",
            "evidence_label": EVIDENCE_LABEL,
            "sensitivity_label": SENSITIVITY_LABEL,
            "investment_advice_label": INVESTMENT_ADVICE_LABEL,
            "observed": OBSERVED,
            "policy_grade": POLICY_GRADE,
        }
        rows.append(row)
    return rows


def _aggregate_metrics(
    class_results: Sequence[SitingResult],
    existing_capacity_m3_y: float,
) -> dict[str, Any]:
    rows = [row for result in class_results for row in result.rows]
    assigned = math.fsum(float(row["assigned_m3"]) for row in rows)
    existing_rows = [
        row for row in rows if row["facility_kind"] == "existing"
    ]
    existing_served = math.fsum(
        float(row["assigned_m3"]) for row in existing_rows
    )
    module_served = math.fsum(
        float(row["assigned_m3"])
        for row in rows
        if row["facility_kind"] == "greenfield_module"
    )
    volume_distance = math.fsum(
        float(row["assigned_m3"]) * float(row["road_distance_km"])
        for row in rows
    )
    cost = math.fsum(float(row["transport_cost_cad"]) for row in rows)
    energy = math.fsum(float(row["transport_energy_gj"]) for row in rows)
    cross_border = math.fsum(
        float(row["assigned_m3"])
        for row in rows
        if bool(row["cross_border"])
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
    module_ids = {
        str(row["facility_id"])
        for row in rows
        if row["facility_kind"] == "greenfield_module"
    }
    modules_serving = len(
        {
            str(row["facility_id"])
            for row in rows
            if row["facility_kind"] == "greenfield_module"
            and float(row["assigned_m3"]) > VOLUME_EPSILON_M3
        }
    )
    total_modules = sum(result.k for result in class_results)
    if len(module_ids) > total_modules:
        raise ClassSitingError("aggregate module count exceeds selected modules")
    if abs(existing_served + module_served - assigned) > CONSERVATION_TOLERANCE_M3:
        raise ClassSitingError("aggregate existing plus module volume is not conserved")
    return {
        "k_per_class": class_results[0].k,
        "total_modules": total_modules,
        "classes": len(class_results),
        "assigned_m3": assigned,
        "existing_served_m3": existing_served,
        "existing_served_share": existing_served / assigned if assigned > 0.0 else 0.0,
        "existing_facilities_used": len(
            [
                facility_id
                for facility_id, volume in facility_volume.items()
                if volume > VOLUME_EPSILON_M3
            ]
        ),
        "existing_capacity_m3_y": existing_capacity_m3_y,
        "existing_utilization": (
            existing_served / existing_capacity_m3_y
            if existing_capacity_m3_y > 0.0
            else 0.0
        ),
        "binding_facilities": len(binding),
        "binding_volume_m3": math.fsum(
            facility_volume[facility_id] for facility_id in binding
        ),
        "module_served_m3": module_served,
        "module_served_share": module_served / assigned if assigned > 0.0 else 0.0,
        "modules_serving": modules_serving,
        "assignments": len(rows),
        "road_distance_m3_km": volume_distance,
        "mean_road_distance_km": (
            volume_distance / assigned if assigned > 0.0 else 0.0
        ),
        "median_road_distance_km": v10._weighted_quantile(rows, 0.5),
        "p90_road_distance_km": v10._weighted_quantile(rows, 0.9),
        "max_road_distance_km": max(
            (float(row["road_distance_km"]) for row in rows), default=0.0
        ),
        "share_ge_150_km": (
            math.fsum(
                float(row["assigned_m3"])
                for row in rows
                if float(row["road_distance_km"]) >= 150.0
            )
            / assigned
            if assigned > 0.0
            else 0.0
        ),
        "share_ge_300_km": (
            math.fsum(
                float(row["assigned_m3"])
                for row in rows
                if float(row["road_distance_km"]) >= 300.0
            )
            / assigned
            if assigned > 0.0
            else 0.0
        ),
        "cross_border_m3": cross_border,
        "cross_border_share": cross_border / assigned if assigned > 0.0 else 0.0,
        "transport_energy_gj": energy,
        "transport_cost_cad": cost,
        "transport_cost_cad_m3": cost / assigned if assigned > 0.0 else 0.0,
        "transport_cost_cad_gj": cost / energy if energy > 0.0 else 0.0,
    }


def _load_generic() -> tuple[dict[str, Any], dict[int, dict[str, float]]]:
    if not GENERIC_SUMMARY_JSON.is_file():
        raise ClassSitingError(f"generic siting summary is missing: {GENERIC_SUMMARY_JSON}")
    summary = json.loads(GENERIC_SUMMARY_JSON.read_text(encoding="utf-8"))
    if summary.get("artifact") != "greenfield_siting_sensitivity_v1":
        raise ClassSitingError("generic siting artifact is unexpected")
    if summary.get("status") != "complete":
        raise ClassSitingError("generic siting artifact is not complete")
    if not summary.get("validation", {}).get("all_passed"):
        raise ClassSitingError("generic siting artifact did not pass validation")
    rows = _read_rows(GENERIC_TRADEOFF_CSV, "generic siting k tradeoff")
    by_k: dict[int, dict[str, float]] = {}
    for row in rows:
        k = int(row["k"])
        if k in by_k:
            raise ClassSitingError(f"generic tradeoff repeats k={k}")
        by_k[k] = {
            "transport_cost_cad": float(row["transport_cost_cad"]),
            "mean_road_distance_km": float(row["mean_road_distance_km"]),
            "module_served_share": float(row["module_served_share"]),
            "share_ge_150_km": float(row["share_ge_150_km"]),
            "share_ge_300_km": float(row["share_ge_300_km"]),
        }
    if sorted(by_k) != list(range(21)):
        raise ClassSitingError("generic tradeoff does not cover k=0..20")
    return summary, by_k


def _build_aggregate_rows(
    results_by_class: Mapping[str, Sequence[SitingResult]],
    generic_by_k: Mapping[int, Mapping[str, float]],
    existing_capacity_m3_y: float,
) -> list[dict[str, Any]]:
    aggregates: list[dict[str, Any]] = []
    for k in K_VALUES:
        class_results = [results_by_class[name][k] for name in CLASS_ORDER]
        aggregates.append(
            _aggregate_metrics(class_results, existing_capacity_m3_y)
        )
    k0_cost = float(aggregates[0]["transport_cost_cad"])
    k0_mean = float(aggregates[0]["mean_road_distance_km"])
    rows: list[dict[str, Any]] = []
    for index, metrics in enumerate(aggregates):
        k = int(metrics["k_per_class"])
        cost = float(metrics["transport_cost_cad"])
        mean = float(metrics["mean_road_distance_km"])
        if index == 0:
            marginal_cost = 0.0
            marginal_mean = 0.0
        else:
            previous = aggregates[index - 1]
            marginal_cost = float(previous["transport_cost_cad"]) - cost
            marginal_mean = float(previous["mean_road_distance_km"]) - mean
        same_k = generic_by_k[k]
        matched_k = min(k * len(CLASS_ORDER), max(generic_by_k))
        matched = generic_by_k[matched_k]
        matched_exact = k * len(CLASS_ORDER) <= max(generic_by_k)
        rows.append(
            {
                **metrics,
                "marginal_transport_cost_reduction_cad": marginal_cost,
                "marginal_mean_distance_reduction_km": marginal_mean,
                "delta_transport_cost_vs_k0_cad": cost - k0_cost,
                "delta_transport_cost_vs_k0_pct": (
                    100.0 * (cost / k0_cost - 1.0) if k0_cost > 0.0 else 0.0
                ),
                "delta_mean_road_distance_vs_k0_km": mean - k0_mean,
                "generic_same_k": k,
                "generic_same_k_cost_cad": same_k["transport_cost_cad"],
                "generic_same_k_mean_road_distance_km": same_k[
                    "mean_road_distance_km"
                ],
                "delta_vs_generic_same_k_cad": cost
                - same_k["transport_cost_cad"],
                "delta_vs_generic_same_k_pct": (
                    100.0 * (cost / same_k["transport_cost_cad"] - 1.0)
                    if same_k["transport_cost_cad"] > 0.0
                    else 0.0
                ),
                "generic_matched_k": matched_k,
                "generic_matched_exact": matched_exact,
                "generic_matched_cost_cad": matched["transport_cost_cad"],
                "generic_matched_mean_road_distance_km": matched[
                    "mean_road_distance_km"
                ],
                "delta_vs_generic_matched_cad": cost
                - matched["transport_cost_cad"],
                "delta_vs_generic_matched_pct": (
                    100.0 * (cost / matched["transport_cost_cad"] - 1.0)
                    if matched["transport_cost_cad"] > 0.0
                    else 0.0
                ),
                "delta_vs_generic_matched_mean_km": mean
                - matched["mean_road_distance_km"],
                "siting_method": "greedy_forward_exact_lp_shortlist",
                "evidence_label": EVIDENCE_LABEL,
                "sensitivity_label": SENSITIVITY_LABEL,
                "investment_advice_label": INVESTMENT_ADVICE_LABEL,
                "observed": OBSERVED,
                "policy_grade": POLICY_GRADE,
            }
        )
    return rows


def _build_generic_comparison_rows(
    aggregate_rows: Sequence[Mapping[str, Any]],
    generic_by_k: Mapping[int, Mapping[str, float]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for aggregate in aggregate_rows:
        k = int(aggregate["k_per_class"])
        total_modules = int(aggregate["total_modules"])
        for basis, generic_k in (
            ("same_k_per_class", k),
            ("module_count_matched", min(total_modules, max(generic_by_k))),
        ):
            if basis == "module_count_matched" and k == 0:
                continue
            if basis == "module_count_matched" and total_modules > max(generic_by_k):
                note = (
                    "class-specific total modules exceed the generic k=20 "
                    "maximum; generic k=20 is the nearest available row, not a "
                    "count match"
                )
            elif basis == "module_count_matched":
                note = "generic module count equals the class-specific total"
            else:
                note = (
                    "generic modules are shared and class-agnostic; the "
                    "class-specific total is five times k_per_class"
                )
            if basis == "same_k_per_class" and k == 0:
                note = "both rows are existing-only; no modules"
            generic = generic_by_k[generic_k]
            class_cost = float(aggregate["transport_cost_cad"])
            class_mean = float(aggregate["mean_road_distance_km"])
            rows.append(
                {
                    "comparison_basis": basis,
                    "k_per_class": k,
                    "class_total_modules": total_modules,
                    "generic_k": generic_k,
                    "module_count_exact": total_modules == generic_k,
                    "class_transport_cost_cad": class_cost,
                    "generic_transport_cost_cad": generic[
                        "transport_cost_cad"
                    ],
                    "delta_transport_cost_cad": class_cost
                    - generic["transport_cost_cad"],
                    "delta_transport_cost_pct": (
                        100.0 * (class_cost / generic["transport_cost_cad"] - 1.0)
                        if generic["transport_cost_cad"] > 0.0
                        else 0.0
                    ),
                    "class_mean_road_distance_km": class_mean,
                    "generic_mean_road_distance_km": generic[
                        "mean_road_distance_km"
                    ],
                    "delta_mean_road_distance_km": class_mean
                    - generic["mean_road_distance_km"],
                    "class_module_served_share": aggregate[
                        "module_served_share"
                    ],
                    "generic_module_served_share": generic[
                        "module_served_share"
                    ],
                    "delta_module_served_share": float(
                        aggregate["module_served_share"]
                    )
                    - generic["module_served_share"],
                    "class_share_ge_150_km": aggregate["share_ge_150_km"],
                    "generic_share_ge_150_km": generic["share_ge_150_km"],
                    "class_share_ge_300_km": aggregate["share_ge_300_km"],
                    "generic_share_ge_300_km": generic["share_ge_300_km"],
                    "note": note,
                    "evidence_label": EVIDENCE_LABEL,
                    "sensitivity_label": SENSITIVITY_LABEL,
                    "investment_advice_label": INVESTMENT_ADVICE_LABEL,
                    "observed": OBSERVED,
                    "policy_grade": POLICY_GRADE,
                }
            )
    return rows


def _build_module_rows(
    results_by_class: Mapping[str, Sequence[SitingResult]],
    candidates_by_class: Mapping[str, Sequence[CandidateModule]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for facility_class in CLASS_ORDER:
        candidate_by_index = {
            candidate.candidate_index: candidate
            for candidate in candidates_by_class[facility_class]
        }
        first_selected: dict[int, int] = {}
        for result in results_by_class[facility_class]:
            if result.k == 0:
                continue
            assigned_total = float(result.metrics["assigned_m3"])
            module_total = float(result.metrics["module_served_m3"])
            for rank, candidate_index in enumerate(result.selected, start=1):
                candidate = candidate_by_index[candidate_index]
                stats = result.module_stats[candidate.module_id]
                first_selected.setdefault(candidate_index, result.k)
                served = float(stats["served_m3"])
                rows.append(
                    {
                        "class": facility_class,
                        "k": result.k,
                        "selection_rank": rank,
                        "first_selected_k": first_selected[candidate_index],
                        "module_id": candidate.module_id,
                        "node_id": candidate.node.node_id,
                        "province": candidate.node.province,
                        "lat": candidate.node.lat,
                        "lon": candidate.node.lon,
                        "module_class": facility_class,
                        "class_specific": True,
                        "capacity_status": MODULE_CAPACITY_STATUS,
                        "capacity_m3_y": "",
                        "serving": served > VOLUME_EPSILON_M3,
                        "module_served_m3": served,
                        "module_served_share_of_class_assigned": (
                            served / assigned_total if assigned_total > 0.0 else 0.0
                        ),
                        "module_served_share_of_class_module_total": (
                            served / module_total if module_total > 0.0 else 0.0
                        ),
                        "nodes_served": stats["nodes_served"],
                        "mean_road_distance_km": stats["mean_road_distance_km"],
                        "max_road_distance_km": stats["max_road_distance_km"],
                        "road_factor_applied": candidate.road_factor,
                        "road_factor_basis": "candidate_province_measured_factor",
                        "evidence_label": EVIDENCE_LABEL,
                        "sensitivity_label": SENSITIVITY_LABEL,
                        "investment_advice_label": INVESTMENT_ADVICE_LABEL,
                        "observed": OBSERVED,
                        "policy_grade": POLICY_GRADE,
                    }
                )
    return rows


def _build_existing_service_rows(
    results_by_class: Mapping[str, Sequence[SitingResult]],
    facilities_by_class: Mapping[str, Sequence[Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for facility_class in CLASS_ORDER:
        for k in (0, max(K_VALUES)):
            result = results_by_class[facility_class][k]
            volume: dict[str, float] = defaultdict(float)
            for row in result.rows:
                if row["facility_kind"] != "existing":
                    continue
                volume[str(row["facility_id"])] += float(row["assigned_m3"])
            for facility in facilities_by_class[facility_class]:
                served = volume.get(facility.facility_id, 0.0)
                capacity = float(facility.capacity.capacity_m3_y)
                rows.append(
                    {
                        "k": k,
                        "class": facility_class,
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


def _build_split_rows(
    shares: Mapping[str, ProvinceShare],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for province in PROVINCES:
        share = shares[province]
        for facility_class in CLASS_ORDER:
            rows.append(
                {
                    "province": province,
                    "province_name": share.province_name,
                    "facility_class": facility_class,
                    "v5_class_feedstock_m3": share.class_feedstock_m3[facility_class],
                    "v5_total_feedstock_m3": share.total_feedstock_m3,
                    "province_harvest_m3": share.harvest_m3,
                    "class_share": share.class_share[facility_class],
                    "class_harvest_split_m3": (
                        share.harvest_m3 * share.class_share[facility_class]
                    ),
                    "share_source": (
                        "national_product_chain_v5 class feedstock divided by "
                        "the province five-class feedstock total; applied to "
                        "node harvest"
                    ),
                    "evidence_label": EVIDENCE_LABEL,
                    "sensitivity_label": SENSITIVITY_LABEL,
                    "investment_advice_label": INVESTMENT_ADVICE_LABEL,
                    "observed": OBSERVED,
                    "policy_grade": POLICY_GRADE,
                }
            )
    return rows


# ---------------------------------------------------------------------------
# Validation.
# ---------------------------------------------------------------------------

def validate(
    *,
    nodes: Sequence[v10.SupplyNode],
    positive_nodes: Sequence[v10.SupplyNode],
    shares: Mapping[str, ProvinceShare],
    class_nodes: Mapping[str, Sequence[ClassNode]],
    facilities_by_class: Mapping[str, Sequence[Any]],
    capacities_by_class: Mapping[str, np.ndarray],
    candidates_by_class: Mapping[str, Sequence[CandidateModule]],
    excluded_by_class: Mapping[str, Mapping[str, Mapping[str, float]]],
    factors: Mapping[str, float],
    results_by_class: Mapping[str, Sequence[SitingResult]],
    class_tradeoff_rows: Sequence[Mapping[str, Any]],
    aggregate_rows: Sequence[Mapping[str, Any]],
    module_rows: Sequence[Mapping[str, Any]],
    node_assignment_k0: Sequence[Mapping[str, Any]],
    node_assignment_k5: Sequence[Mapping[str, Any]],
    existing_rows: Sequence[Mapping[str, Any]],
    split_rows: Sequence[Mapping[str, Any]],
    generic_summary: Mapping[str, Any],
    generic_by_k: Mapping[int, Mapping[str, float]],
    generic_comparison_rows: Sequence[Mapping[str, Any]],
    line: v10.CostLine,
    total_harvest_m3: float,
    existing_capacity_m3_y: float,
) -> dict[str, Any]:
    checks: dict[str, bool] = {}
    failures: list[str] = []

    def check(name: str, condition: bool, detail: str = "") -> None:
        checks[name] = bool(condition)
        if not condition:
            failures.append(f"{name}: {detail}" if detail else name)

    class_total = {
        facility_class: math.fsum(
            class_node.class_m3 for class_node in class_nodes[facility_class]
        )
        for facility_class in CLASS_ORDER
    }
    check(
        "node_universe",
        len(nodes) == EXPECTED_NODE_COUNT
        and len(positive_nodes) == EXPECTED_POSITIVE_NODE_COUNT
        and math.isclose(
            total_harvest_m3, EXPECTED_HARVEST_M3, rel_tol=1e-12, abs_tol=1e-3
        ),
        f"nodes={len(nodes)} positive={len(positive_nodes)}",
    )
    check(
        "class_share_conservation",
        all(
            (
                share.harvest_m3 <= 0.0
                and abs(math.fsum(share.class_share.values())) <= 1e-12
            )
            or abs(math.fsum(share.class_share.values()) - 1.0) <= 1e-9
            for share in shares.values()
        )
        and abs(math.fsum(class_total.values()) - total_harvest_m3)
        <= CONSERVATION_TOLERANCE_M3,
        "province class shares or total class demand do not conserve",
    )
    check(
        "class_feedstock_totals",
        all(
            class_total[facility_class] > 0.0
            and abs(
                math.fsum(
                    float(row["class_harvest_split_m3"])
                    for row in split_rows
                    if row["facility_class"] == facility_class
                )
                - class_total[facility_class]
            )
            <= CONSERVATION_TOLERANCE_M3
            for facility_class in CLASS_ORDER
        )
        and len(split_rows) == len(PROVINCES) * len(CLASS_ORDER),
        "class feedstock totals do not match the split table",
    )
    check(
        "class_split_table",
        all(
            abs(
                math.fsum(
                    float(row["class_share"])
                    for row in split_rows
                    if row["province"] == province
                )
                - 1.0
            )
            <= 1e-9
            or all(
                float(row["province_harvest_m3"]) == 0.0
                for row in split_rows
                if row["province"] == province
            )
            for province in PROVINCES
        ),
        "split table shares do not sum to one",
    )
    check(
        "class_partition",
        set(facilities_by_class) == set(CLASS_ORDER)
        and sum(len(group) for group in facilities_by_class.values())
        == EXPECTED_FACILITY_COUNT
        and abs(
            math.fsum(
                float(capacities.sum())
                for capacities in capacities_by_class.values()
            )
            - existing_capacity_m3_y
        )
        <= 1e-6,
        "existing facility partition does not cover the fixed universe",
    )
    check(
        "class_demand_nodes",
        all(
            class_nodes[facility_class]
            and all(
                class_node.class_m3 > 0.0
                and class_node.node.harvest_m3 > 0.0
                for class_node in class_nodes[facility_class]
            )
            for facility_class in CLASS_ORDER
        ),
        "one or more classes have no positive demand node",
    )
    check(
        "class_candidate_coverage",
        all(
            set(excluded_by_class[facility_class]) <= {"NT"}
            and all(
                candidate.class_m3 > 0.0
                and candidate.node.province in factors
                and candidate.module_id.startswith(
                    f"{MODULE_ID_PREFIX}|{facility_class}|"
                )
                for candidate in candidates_by_class[facility_class]
            )
            for facility_class in CLASS_ORDER
        ),
        "class candidate coverage or module naming is invalid",
    )
    check(
        "k_grid",
        all(
            [result.k for result in results_by_class[facility_class]]
            == list(K_VALUES)
            for facility_class in CLASS_ORDER
        ),
        "class k values do not cover 0..5",
    )
    for facility_class in CLASS_ORDER:
        results = results_by_class[facility_class]
        demand = class_total[facility_class]
        check(
            f"class_conservation_{facility_class}",
            all(
                abs(float(result.metrics["assigned_m3"]) - demand)
                <= CONSERVATION_TOLERANCE_M3
                and abs(
                    float(result.metrics["existing_served_m3"])
                    + float(result.metrics["module_served_m3"])
                    - float(result.metrics["assigned_m3"])
                )
                <= CONSERVATION_TOLERANCE_M3
                for result in results
            ),
            f"{facility_class}: assigned volume is not conserved",
        )
        check(
            f"class_k0_existing_only_{facility_class}",
            results[0].selected == ()
            and abs(float(results[0].metrics["module_served_m3"])) <= 1e-9
            and abs(
                float(results[0].metrics["existing_served_m3"])
                - float(results[0].metrics["assigned_m3"])
            )
            <= CONSERVATION_TOLERANCE_M3,
            f"{facility_class}: k=0 is not existing-only",
        )
        check(
            f"class_capacity_{facility_class}",
            all(
                float(result.metrics["max_capacity_excess_m3"])
                <= CAPACITY_TOLERANCE_M3
                for result in results
            ),
            f"{facility_class}: existing capacity is exceeded",
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
            f"class_k_monotonicity_{facility_class}",
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
            f"{facility_class}: cost or distance increased with k",
        )
        check(
            f"class_module_count_{facility_class}",
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
            f"{facility_class}: selected module count is inconsistent",
        )
        check(
            f"class_cost_arithmetic_{facility_class}",
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
                    float(result.metrics["transport_energy_gj"])
                    - float(result.metrics["assigned_m3"])
                    * v10.ENERGY_GJ_PER_M3
                )
                <= max(1e-6, 1e-9 * float(result.metrics["transport_energy_gj"]))
                and abs(
                    float(result.metrics["transport_cost_cad_gj"])
                    - float(result.metrics["transport_cost_cad"])
                    / float(result.metrics["transport_energy_gj"])
                )
                <= 1e-9
                and abs(
                    float(result.metrics["mean_road_distance_km"])
                    - float(result.metrics["road_distance_m3_km"])
                    / float(result.metrics["assigned_m3"])
                )
                <= 1e-9
                for result in results
            ),
            f"{facility_class}: transport arithmetic is inconsistent",
        )
        for label, rows in (
            ("k0", node_assignment_k0),
            ("k5", node_assignment_k5),
        ):
            expected = {
                class_node.node.node_id: class_node.class_m3
                for class_node in class_nodes[facility_class]
            }
            grouped: dict[str, float] = defaultdict(float)
            for row in rows:
                if row["class"] != facility_class:
                    continue
                grouped[str(row["node_id"])] += float(row["assigned_m3"])
            check(
                f"class_node_conservation_{facility_class}_{label}",
                set(grouped) == set(expected)
                and all(
                    abs(grouped[node_id] - expected[node_id])
                    <= CONSERVATION_TOLERANCE_M3
                    for node_id in expected
                ),
                f"{facility_class} {label}: node volume does not conserve",
            )
        for result in results:
            class_module_rows = [
                row
                for row in module_rows
                if row["class"] == facility_class and int(row["k"]) == result.k
            ]
            check(
                f"class_module_rows_{facility_class}_{result.k}",
                len(class_module_rows) == result.k
                and len(
                    {row["module_id"] for row in class_module_rows}
                )
                == result.k
                and abs(
                    math.fsum(
                        float(row["module_served_m3"])
                        for row in class_module_rows
                    )
                    - float(result.metrics["module_served_m3"])
                )
                <= CONSERVATION_TOLERANCE_M3
                and all(
                    row["module_class"] == facility_class
                    and row["class_specific"] in {True, "True", "true"}
                    and row["capacity_status"] == MODULE_CAPACITY_STATUS
                    and row["capacity_m3_y"] in {"", None}
                    for row in class_module_rows
                ),
                f"{facility_class} k={result.k}: module rows are inconsistent",
            )
            if float(result.metrics["module_served_m3"]) > 0.0:
                check(
                    f"class_module_share_{facility_class}_{result.k}",
                    abs(
                        math.fsum(
                            float(
                                row[
                                    "module_served_share_of_class_module_total"
                                ]
                            )
                            for row in class_module_rows
                        )
                        - 1.0
                    )
                    <= 1e-9,
                    f"{facility_class} k={result.k}: module shares do not sum to one",
                )
    check(
        "aggregate_conservation",
        all(
            abs(float(row["assigned_m3"]) - total_harvest_m3)
            <= CONSERVATION_TOLERANCE_M3
            and abs(
                float(row["existing_served_m3"])
                + float(row["module_served_m3"])
                - float(row["assigned_m3"])
            )
            <= CONSERVATION_TOLERANCE_M3
            and abs(
                float(row["existing_capacity_m3_y"]) - existing_capacity_m3_y
            )
            <= 1e-6
            for row in aggregate_rows
        ),
        "aggregate volume, capacity or class sum is not conserved",
    )
    check(
        "aggregate_module_count",
        all(
            int(row["total_modules"]) == len(CLASS_ORDER) * int(row["k_per_class"])
            and int(row["modules_serving"]) <= int(row["total_modules"])
            for row in aggregate_rows
        ),
        "aggregate module count is inconsistent",
    )
    aggregate_costs = [
        float(row["transport_cost_cad"]) for row in aggregate_rows
    ]
    aggregate_means = [
        float(row["mean_road_distance_km"]) for row in aggregate_rows
    ]
    check(
        "aggregate_k_monotonicity",
        all(
            aggregate_costs[index + 1] <= aggregate_costs[index] + 1e-6
            for index in range(len(aggregate_costs) - 1)
        )
        and all(
            aggregate_means[index + 1] <= aggregate_means[index] + 1e-9
            for index in range(len(aggregate_means) - 1)
        ),
        "aggregate cost or mean distance increased with k",
    )
    for row in aggregate_rows:
        k = int(row["k_per_class"])
        class_results = [
            results_by_class[facility_class][k] for facility_class in CLASS_ORDER
        ]
        check(
            f"aggregate_class_sum_{k}",
            abs(
                float(row["transport_cost_cad"])
                - math.fsum(
                    float(result.metrics["transport_cost_cad"])
                    for result in class_results
                )
            )
            <= max(COST_TOLERANCE_CAD, 1e-9 * float(row["transport_cost_cad"]))
            and abs(
                float(row["assigned_m3"])
                - math.fsum(
                    float(result.metrics["assigned_m3"])
                    for result in class_results
                )
            )
            <= CONSERVATION_TOLERANCE_M3,
            f"k={k}: aggregate does not equal the class sums",
        )
    check(
        "generic_inputs",
        generic_summary.get("artifact") == "greenfield_siting_sensitivity_v1"
        and generic_summary.get("status") == "complete"
        and bool(generic_summary.get("validation", {}).get("all_passed"))
        and sorted(generic_by_k) == list(range(21)),
        "generic siting reference is not the committed artifact",
    )
    check(
        "generic_k0_dominance",
        float(aggregate_rows[0]["transport_cost_cad"])
        >= float(generic_by_k[0]["transport_cost_cad"])
        - max(
            COST_TOLERANCE_CAD,
            1e-6 * float(generic_by_k[0]["transport_cost_cad"]),
        ),
        "class-specific existing-only cost is below the class-agnostic bound",
    )
    for row in generic_comparison_rows:
        generic = generic_by_k[int(row["generic_k"])]
        class_cost = float(row["class_transport_cost_cad"])
        check(
            "generic_comparison_arithmetic_"
            f"{row['comparison_basis']}_{row['k_per_class']}",
            abs(
                float(row["generic_transport_cost_cad"])
                - generic["transport_cost_cad"]
            )
            <= 1e-6
            and abs(
                float(row["delta_transport_cost_cad"])
                - (class_cost - generic["transport_cost_cad"])
            )
            <= 1e-6
            and abs(
                float(row["delta_mean_road_distance_km"])
                - (
                    float(row["class_mean_road_distance_km"])
                    - generic["mean_road_distance_km"]
                )
            )
            <= 1e-9,
            "generic comparison arithmetic is inconsistent",
        )
    check(
        "generic_comparison_rows",
        len(generic_comparison_rows) == len(K_VALUES) + len(K_VALUES) - 1
        and any(
            row["comparison_basis"] == "module_count_matched"
            and int(row["k_per_class"]) == 4
            and int(row["generic_k"]) == 20
            for row in generic_comparison_rows
        ),
        "generic comparison rows do not cover same-k and count-matched bases",
    )
    check(
        "existing_capacity_fixed",
        len(existing_rows) == 2 * EXPECTED_FACILITY_COUNT
        and all(
            abs(
                math.fsum(
                    float(row["capacity_m3_y"])
                    for row in existing_rows
                    if int(row["k"]) == k
                )
                - existing_capacity_m3_y
            )
            <= 1e-6
            for k in (0, max(K_VALUES))
        )
        and all(
            float(row["served_m3"]) >= 0.0
            and float(row["served_m3"])
            <= float(row["capacity_m3_y"]) + 1e-6
            and abs(
                float(row["utilization"])
                - float(row["served_m3"]) / float(row["capacity_m3_y"])
            )
            <= 1e-9
            for row in existing_rows
        ),
        "existing facility service rows are inconsistent",
    )
    all_rows = (
        list(class_tradeoff_rows)
        + list(aggregate_rows)
        + list(module_rows)
        + list(node_assignment_k0)
        + list(node_assignment_k5)
        + list(existing_rows)
        + list(split_rows)
        + list(generic_comparison_rows)
    )
    check(
        "labels",
        all(
            row["evidence_label"] == EVIDENCE_LABEL
            and row["sensitivity_label"] == SENSITIVITY_LABEL
            and row["investment_advice_label"] == INVESTMENT_ADVICE_LABEL
            and str(row["observed"]).lower() == "false"
            and str(row["policy_grade"]).lower() == "false"
            for row in all_rows
        ),
        "one or more rows carry unexpected labels",
    )
    delta_fields = {
        "delta_transport_cost_vs_k0_cad",
        "delta_transport_cost_vs_k0_pct",
        "delta_mean_road_distance_vs_k0_km",
        "delta_vs_generic_same_k_cad",
        "delta_vs_generic_same_k_pct",
        "delta_vs_generic_matched_cad",
        "delta_vs_generic_matched_pct",
        "delta_vs_generic_matched_mean_km",
        "delta_transport_cost_cad",
        "delta_transport_cost_pct",
        "delta_mean_road_distance_km",
        "delta_module_served_share",
        "delta_transport_energy_gj",
    }
    check(
        "no_nan_or_negative",
        all(
            math.isfinite(float(row[field]))
            and (float(row[field]) >= 0.0 or field in delta_fields)
            for row in class_tradeoff_rows
            for field in (
                "assigned_m3",
                "transport_cost_cad",
                "transport_cost_cad_m3",
                "transport_cost_cad_gj",
                "mean_road_distance_km",
                "share_ge_150_km",
                "share_ge_300_km",
                "existing_utilization",
                "module_served_share",
            )
        )
        and all(
            math.isfinite(float(row[field]))
            and (float(row[field]) >= 0.0 or field in delta_fields)
            for row in aggregate_rows
            for field in (
                "assigned_m3",
                "transport_cost_cad",
                "transport_cost_cad_m3",
                "transport_cost_cad_gj",
                "mean_road_distance_km",
                "share_ge_150_km",
                "share_ge_300_km",
                "existing_utilization",
                "module_served_share",
            )
        )
        and all(
            value == ""
            or not isinstance(value, (int, float))
            or math.isfinite(float(value))
            for row in module_rows
            for value in row.values()
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
    class_tradeoff_rows: Sequence[Mapping[str, Any]],
    aggregate_rows: Sequence[Mapping[str, Any]],
    module_rows: Sequence[Mapping[str, Any]],
    generic_comparison_rows: Sequence[Mapping[str, Any]],
    split_rows: Sequence[Mapping[str, Any]],
) -> str:
    class_summary = summary["classes"]
    aggregate = summary["headline"]["aggregate"]
    k0 = aggregate["k0"]
    k5 = aggregate["k5"]
    generic = summary["generic_comparison"]
    lines: list[str] = [
        "# Class-Specific Transport-Only Greenfield Siting V1",
        "",
        "Status: **transport_only_siting_screen** — `not_investment_advice`.",
        "Evidence label: `model_estimate`; `observed=false`; `policy_grade=false`.",
        "",
        "This is a transport-cost screening exercise. It is not investment advice,",
        "not a capital, permitting, technology or landbase estimate, and not a",
        "facility-design or class-capacity claim. No national, policy, UI or",
        "official OSeCAN surface is changed.",
        "",
        "## 1. Setup",
        "",
        "- **Demand:** 525 committed RSYC supply nodes; 486 positive-volume nodes;",
        f"  {_num(k0['assigned_m3'] / 1e6, 3)} Mm3/yr split into SAW, PULP, PANEL,",
        "  PELLET and BIO feedstock.",
        "- **Class split:** v5.1 E1/E2c province/class feedstock shares, normalised",
        "  within each province and applied to node harvest. The split conserves",
        "  node harvest and each province/class total.",
        "- **Existing facilities:** capacity v3 with the documented 1.2x provincial",
        f"  floor, partitioned by class and held fixed; {summary['counts']['eligible_facilities']}",
        f"  eligible facilities and {_num(summary['method']['existing_capacity_m3_y'] / 1e6, 3)} Mm3/yr",
        "  of capacity.",
        "- **Greenfield candidates:** positive class feedstock nodes in provinces",
        "  with a published road factor. The class module is unlimited-throughput",
        "  in the screen; no class capacity, technology, cost, product or residue",
        "  is asserted.",
        f"- **Candidate modules:** {summary['counts']['candidate_modules']} across the five classes.",
        "  NT positive nodes are demand nodes but not module hosts because no NT",
        "  road factor is published.",
        "- **Objective:** minimise 2026 CAD truck transport cost from transport",
        "  cost v1 (52.5 m3 payload, empty return). No local-preference penalty.",
        "- **Distances:** haversine x destination province road factor from",
        "  `facility_road_distance_audit_v2`; no full OSRM routing.",
        "- **Method:** greedy forward k-median/facility-location screening with",
        "  exact capacitated transport-LP re-evaluation (HiGHS). At each k the",
        f"  shortlist is the top {summary['method']['shortlist_size']} candidates by",
        "  capacity-relaxed distance savings. This is not a proven global optimum.",
        "",
        "## 2. Class feedstock split",
        "",
        "| class | demand (Mm3) | share of harvest | candidates | existing facilities | existing capacity (Mm3) |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for facility_class in CLASS_ORDER:
        block = class_summary[facility_class]
        lines.append(
            "| {class_name} | {demand} | {share} | {candidates} | {facilities} | "
            "{capacity} |".format(
                class_name=facility_class,
                demand=_num(float(block["class_feedstock_m3"]) / 1e6, 3),
                share=_pct(float(block["share_of_harvest"]), 2),
                candidates=block["candidate_modules"],
                facilities=block["existing_facilities"],
                capacity=_num(float(block["existing_capacity_m3_y"]) / 1e6, 3),
            )
        )
    lines.extend(
        [
            "",
            "The class split is a proportional screening allocation of node",
            "harvest. It is not a mass-balance routing of harvest residue, and it",
            "does not assert that the v5.1 absolute class feedstock totals are",
            "physically available at every node.",
            "",
            "### Province/class shares",
            "",
            "| province | SAW | PULP | PANEL | PELLET | BIO |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    by_province: dict[str, dict[str, Mapping[str, Any]]] = defaultdict(dict)
    for row in split_rows:
        by_province[str(row["province"])][str(row["facility_class"])] = row
    for province in PROVINCES:
        rows = by_province[province]
        lines.append(
            "| {province} | {values} |".format(
                province=province,
                values=" | ".join(
                    _pct(float(rows[facility_class]["class_share"]), 2)
                    for facility_class in CLASS_ORDER
                ),
            )
        )
    lines.extend(
        [
            "",
            "## 3. Class-specific k tradeoff",
            "",
            "| class | k | cost (B CAD) | CAD/m3 | mean km | >=150 km | >=300 km | module share | existing util |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in class_tradeoff_rows:
        lines.append(
            "| {class_name} | {k} | {cost} | {cost_m3} | {mean} | {ge150} | "
            "{ge300} | {module_share} | {util} |".format(
                class_name=row["class"],
                k=row["k"],
                cost=_billions(float(row["transport_cost_cad"])),
                cost_m3=_num(float(row["transport_cost_cad_m3"]), 2),
                mean=_num(float(row["mean_road_distance_km"]), 1),
                ge150=_pct(float(row["share_ge_150_km"]), 1),
                ge300=_pct(float(row["share_ge_300_km"]), 1),
                module_share=_pct(float(row["module_served_share"]), 1),
                util=_pct(float(row["existing_utilization"]), 1),
            )
        )
    lines.extend(
        [
            "",
            "## 4. Aggregate k tradeoff and generic comparison",
            "",
            "| k/class | modules | cost (B CAD) | CAD/m3 | mean km | >=150 km | >=300 km | module share | generic same-k (B) | generic matched k | generic matched (B) |",
            "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in aggregate_rows:
        lines.append(
            "| {k} | {modules} | {cost} | {cost_m3} | {mean} | {ge150} | "
            "{ge300} | {module_share} | {same} | {matched_k} | {matched} |".format(
                k=row["k_per_class"],
                modules=row["total_modules"],
                cost=_billions(float(row["transport_cost_cad"])),
                cost_m3=_num(float(row["transport_cost_cad_m3"]), 2),
                mean=_num(float(row["mean_road_distance_km"]), 1),
                ge150=_pct(float(row["share_ge_150_km"]), 1),
                ge300=_pct(float(row["share_ge_300_km"]), 1),
                module_share=_pct(float(row["module_served_share"]), 1),
                same=_billions(float(row["generic_same_k_cost_cad"])),
                matched_k=row["generic_matched_k"],
                matched=_billions(float(row["generic_matched_cost_cad"])),
            )
        )
    lines.extend(
        [
            "",
            "The aggregate has five classes, so k per class means 5k total modules.",
            "The same-k generic column has only k shared class-agnostic modules and",
            "is not module-count matched. The matched column uses the generic k",
            "nearest to 5k; the k=4 row is the exact 20-module match. The k=5",
            "aggregate has 25 class modules, above the generic k=20 maximum.",
            "",
            "## 5. Module locations at k=5",
            "",
            "| class | module | province | first k | served (Mm3) | share of class demand | nodes | mean km |",
            "|---|---|---|---:|---:|---:|---:|---:|",
        ]
    )
    for row in module_rows:
        if int(row["k"]) != max(K_VALUES):
            continue
        lines.append(
            "| {class_name} | {module} | {province} | {first_k} | {served} | "
            "{share} | {nodes} | {mean} |".format(
                class_name=row["class"],
                module=str(row["module_id"]).replace("|", "\\|"),
                province=row["province"],
                first_k=row["first_selected_k"],
                served=_num(float(row["module_served_m3"]) / 1e6, 3),
                share=_pct(
                    float(row["module_served_share_of_class_assigned"]), 2
                ),
                nodes=row["nodes_served"],
                mean=_num(float(row["mean_road_distance_km"]), 1),
            )
        )
    lines.extend(
        [
            "",
            "## 6. Generic comparison detail",
            "",
            "| basis | k/class | class modules | generic k | class (B CAD) | generic (B CAD) | delta | class mean km | generic mean km | delta mean km |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in generic_comparison_rows:
        lines.append(
            "| {basis} | {k} | {modules} | {generic_k} | {class_cost} | "
            "{generic_cost} | {delta} | {class_mean} | {generic_mean} | "
            "{delta_mean} |".format(
                basis=row["comparison_basis"],
                k=row["k_per_class"],
                modules=row["class_total_modules"],
                generic_k=row["generic_k"],
                class_cost=_billions(float(row["class_transport_cost_cad"])),
                generic_cost=_billions(float(row["generic_transport_cost_cad"])),
                delta=_pct(
                    float(row["delta_transport_cost_pct"]) / 100.0, 2
                ),
                class_mean=_num(
                    float(row["class_mean_road_distance_km"]), 1
                ),
                generic_mean=_num(
                    float(row["generic_mean_road_distance_km"]), 1
                ),
                delta_mean=_num(
                    float(row["delta_mean_road_distance_km"]), 1
                ),
            )
        )
    exact = generic["k4_exact_count_match"]
    lines.extend(
        [
            "",
            "## 7. Findings",
            "",
            "- The class-specific existing-only cost is",
            f"**{_billions(float(k0['transport_cost_cad']))} CAD/yr** at",
            f"{_num(float(k0['mean_road_distance_km']), 1)} km mean distance.",
            "  This is",
            f"{_pct(generic['k0']['delta_transport_cost_pct'] / 100.0, 2)} above",
            "  the class-agnostic generic k=0 cost of",
            f"{_billions(generic['k0']['generic_transport_cost_cad'])} CAD/yr.",
            "- At k=5 per class (25 modules), the aggregate transport cost is",
            f"**{_billions(float(k5['transport_cost_cad']))} CAD/yr**",
            f"({_num(float(k5['mean_road_distance_km']), 1)} km mean;",
            f"{_pct(float(k5['module_served_share']), 2)} module share).",
            "- The aggregate k=5 screen reduces transport cost by",
            f"**{_billions(float(aggregate['cost_reduction_vs_k0_cad']))} CAD/yr**",
            f"({_pct(float(aggregate['cost_reduction_vs_k0_pct']) / 100.0, 2)})",
            "versus class-specific k=0.",
            "- No class or aggregate marginal reduction from k=1 to k=5 falls",
            "  below 1% of its k=0 cost; the diminishing-returns cutoff is not",
            "  reached inside the tested range.",
            "- At the exact 20-module count match (class k=4 versus generic k=20),",
            f"the class-specific cost is {_billions(float(exact['class_transport_cost_cad']))}",
            f"CAD/yr at {_num(float(exact['class_mean_road_distance_km']), 1)} km",
            f"versus {_billions(float(exact['generic_transport_cost_cad']))} CAD/yr",
            f"at {_num(float(exact['generic_mean_road_distance_km']), 1)} km generic",
            f"(+{_pct(float(exact['delta_transport_cost_pct']) / 100.0, 2)};",
            f"mean distance +{_num(float(exact['delta_mean_road_distance_km']), 1)} km).",
            "- Class costs at k=5 differ widely because the existing class facility",
            "  geography and capacity differ; the class split is not a single",
            "  national facility pool.",
            "- The k-series is a greedy prefix, not a globally optimal siting set;",
            "  no optimality-gap claim is made.",
            "",
            "## 8. Labels and caveats",
            "",
            "- `transport_only_siting_screen`; `not_investment_advice`;",
            "  `model_estimate`; `observed=false`; `policy_grade=false`.",
            "- No capital, operating, permitting, technology, landbase, procurement,",
            "  class-capacity or facility-design claim is made.",
            "- The v5.1 class split is a proportional screening allocation of node",
            "  harvest, not a facility-level mass balance.",
            "- Existing facilities are capacity v3 records partitioned by class; the",
            "  published/proxy capacity mix is unchanged.",
            "- The class module is a class-specific transport sink with unlimited",
            "  throughput in the screen. No class capacity is invented.",
            "- Distances are haversine x province-factor screening distances, not",
            "  observed routes for every pair; no full OSRM routing.",
            "- Transport cost excludes feedstock, processing, conversion, handling,",
            "  storage and product revenue.",
            "- No local-preference penalty is applied; cross-border flows are",
            "  reported, not restricted.",
            "- No national claim, policy claim, UI change or official OSeCAN change",
            "  is made.",
            "",
            "## 9. Reproduce",
            "",
            "```bash",
            f".venv/bin/python -m {MODULE_PATH}",
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
    """Build the class-specific transport-only greenfield siting screen."""

    deletion_checker()
    generated_utc = _utc_now()
    generic_summary, generic_by_k = _load_generic()
    v10_reference = generic._load_v10_reference()
    nodes = generic._load_nodes()
    positive_nodes = tuple(
        node for node in nodes if node.harvest_m3 > 0.0
    )
    facilities, factors = generic._load_facilities(v10_reference)
    shares = _load_class_shares(nodes)
    class_nodes = _class_nodes(nodes, shares)
    facilities_by_class, capacities_by_class = _class_facilities(facilities)
    line = v10._cost_line(v9.load_transport_parameters())

    candidates_by_class: dict[str, tuple[CandidateModule, ...]] = {}
    excluded_by_class: dict[str, dict[str, dict[str, float]]] = {}
    for facility_class in CLASS_ORDER:
        candidates, excluded = _build_candidates(
            facility_class, class_nodes[facility_class], factors
        )
        candidates_by_class[facility_class] = candidates
        excluded_by_class[facility_class] = excluded

    results_by_class: dict[str, list[SitingResult]] = {}
    lp_solves = 0
    for facility_class in CLASS_ORDER:
        facilities_group = facilities_by_class[facility_class]
        capacities = capacities_by_class[facility_class]
        harvest = np.array(
            [
                class_node.class_m3
                for class_node in class_nodes[facility_class]
            ],
            dtype=float,
        )
        demand_m3 = math.fsum(harvest)
        existing_capacity_m3_y = math.fsum(
            float(capacity) for capacity in capacities
        )
        existing_distance, module_distance = _distance_matrices(
            class_nodes[facility_class],
            facilities_group,
            candidates_by_class[facility_class],
            factors,
        )
        results, solves = _greedy_select(
            facility_class,
            class_nodes[facility_class],
            facilities_group,
            candidates_by_class[facility_class],
            existing_distance,
            module_distance,
            capacities,
            harvest,
            factors,
            line,
            demand_m3,
            existing_capacity_m3_y,
        )
        results_by_class[facility_class] = results
        lp_solves += solves

    class_tradeoff_rows = [
        row
        for facility_class in CLASS_ORDER
        for row in _build_class_tradeoff_rows(
            facility_class, results_by_class[facility_class]
        )
    ]
    total_harvest_m3 = math.fsum(node.harvest_m3 for node in nodes)
    existing_capacity_m3_y = math.fsum(
        float(capacities.sum())
        for capacities in capacities_by_class.values()
    )
    aggregate_rows = _build_aggregate_rows(
        results_by_class, generic_by_k, existing_capacity_m3_y
    )
    generic_comparison_rows = _build_generic_comparison_rows(
        aggregate_rows, generic_by_k
    )
    module_rows = _build_module_rows(
        results_by_class, candidates_by_class
    )
    existing_rows = _build_existing_service_rows(
        results_by_class, facilities_by_class
    )
    node_assignment_k0 = [
        dict(row)
        for facility_class in CLASS_ORDER
        for row in results_by_class[facility_class][0].rows
    ]
    node_assignment_k5 = [
        dict(row)
        for facility_class in CLASS_ORDER
        for row in results_by_class[facility_class][max(K_VALUES)].rows
    ]
    split_rows = _build_split_rows(shares)

    validation = validate(
        nodes=nodes,
        positive_nodes=positive_nodes,
        shares=shares,
        class_nodes=class_nodes,
        facilities_by_class=facilities_by_class,
        capacities_by_class=capacities_by_class,
        candidates_by_class=candidates_by_class,
        excluded_by_class=excluded_by_class,
        factors=factors,
        results_by_class=results_by_class,
        class_tradeoff_rows=class_tradeoff_rows,
        aggregate_rows=aggregate_rows,
        module_rows=module_rows,
        node_assignment_k0=node_assignment_k0,
        node_assignment_k5=node_assignment_k5,
        existing_rows=existing_rows,
        split_rows=split_rows,
        generic_summary=generic_summary,
        generic_by_k=generic_by_k,
        generic_comparison_rows=generic_comparison_rows,
        line=line,
        total_harvest_m3=total_harvest_m3,
        existing_capacity_m3_y=existing_capacity_m3_y,
    )
    if not validation["all_passed"]:
        raise ClassSitingError(
            "class siting validation failed: " + "; ".join(validation["failures"])
        )

    aggregate_by_k = {
        int(row["k_per_class"]): row for row in aggregate_rows
    }
    class_summary: dict[str, Any] = {}
    for facility_class in CLASS_ORDER:
        results = results_by_class[facility_class]
        k0 = results[0].metrics
        k5 = results[-1].metrics
        k0_cost = float(k0["transport_cost_cad"])
        first_diminishing = next(
            (
                int(row["k"])
                for row in class_tradeoff_rows
                if row["class"] == facility_class
                and int(row["k"]) > 0
                and float(row["marginal_transport_cost_reduction_cad"])
                < 0.01 * k0_cost
            ),
            None,
        )
        class_summary[facility_class] = {
            "class_feedstock_m3": float(k0["class_feedstock_m3"]),
            "share_of_harvest": float(k0["class_feedstock_m3"])
            / total_harvest_m3,
            "candidate_modules": len(candidates_by_class[facility_class]),
            "excluded_candidate_provinces": excluded_by_class[facility_class],
            "class_demand_nodes": len(class_nodes[facility_class]),
            "existing_facilities": len(facilities_by_class[facility_class]),
            "existing_capacity_m3_y": float(
                capacities_by_class[facility_class].sum()
            ),
            "k0": dict(k0),
            "k5": dict(k5),
            "cost_reduction_vs_k0_cad": k0_cost
            - float(k5["transport_cost_cad"]),
            "cost_reduction_vs_k0_pct": (
                100.0 * (1.0 - float(k5["transport_cost_cad"]) / k0_cost)
                if k0_cost > 0.0
                else 0.0
            ),
            "mean_reduction_vs_k0_km": float(k0["mean_road_distance_km"])
            - float(k5["mean_road_distance_km"]),
            "first_k_marginal_below_1pct": first_diminishing,
            "selected_module_ids_k5": str(k5["selected_module_ids"]),
        }

    k0_aggregate = aggregate_by_k[0]
    k5_aggregate = aggregate_by_k[max(K_VALUES)]
    k5_cost = float(k5_aggregate["transport_cost_cad"])
    k0_cost = float(k0_aggregate["transport_cost_cad"])
    aggregate_first_diminishing = next(
        (
            int(row["k_per_class"])
            for row in aggregate_rows
            if int(row["k_per_class"]) > 0
            and float(row["marginal_transport_cost_reduction_cad"])
            < 0.01 * k0_cost
        ),
        None,
    )
    exact_rows = [
        row
        for row in generic_comparison_rows
        if row["comparison_basis"] == "module_count_matched"
        and row["module_count_exact"]
    ]
    generic_comparison = {
        "generic_artifact": generic_summary["artifact"],
        "generic_artifact_version": generic_summary["artifact_version"],
        "generic_k_range": [0, 20],
        "class_k0": dict(k0_aggregate),
        "class_k5": dict(k5_aggregate),
        "k0": {
            "class_transport_cost_cad": k0_cost,
            "generic_transport_cost_cad": float(
                generic_by_k[0]["transport_cost_cad"]
            ),
            "delta_transport_cost_cad": k0_cost
            - float(generic_by_k[0]["transport_cost_cad"]),
            "delta_transport_cost_pct": (
                100.0
                * (k0_cost / float(generic_by_k[0]["transport_cost_cad"]) - 1.0)
                if float(generic_by_k[0]["transport_cost_cad"]) > 0.0
                else 0.0
            ),
            "class_mean_road_distance_km": float(
                k0_aggregate["mean_road_distance_km"]
            ),
            "generic_mean_road_distance_km": float(
                generic_by_k[0]["mean_road_distance_km"]
            ),
        },
        "same_k5": {
            "class_total_modules": int(k5_aggregate["total_modules"]),
            "generic_k": 5,
            "class_transport_cost_cad": k5_cost,
            "generic_transport_cost_cad": float(
                generic_by_k[5]["transport_cost_cad"]
            ),
            "delta_transport_cost_cad": k5_cost
            - float(generic_by_k[5]["transport_cost_cad"]),
            "delta_transport_cost_pct": (
                100.0 * (k5_cost / float(generic_by_k[5]["transport_cost_cad"]) - 1.0)
                if float(generic_by_k[5]["transport_cost_cad"]) > 0.0
                else 0.0
            ),
            "class_mean_road_distance_km": float(
                k5_aggregate["mean_road_distance_km"]
            ),
            "generic_mean_road_distance_km": float(
                generic_by_k[5]["mean_road_distance_km"]
            ),
        },
        "k4_exact_count_match": {
            "class_total_modules": int(aggregate_by_k[4]["total_modules"]),
            "generic_k": 20,
            "class_transport_cost_cad": float(
                aggregate_by_k[4]["transport_cost_cad"]
            ),
            "generic_transport_cost_cad": float(
                generic_by_k[20]["transport_cost_cad"]
            ),
            "delta_transport_cost_cad": float(
                aggregate_by_k[4]["transport_cost_cad"]
            )
            - float(generic_by_k[20]["transport_cost_cad"]),
            "delta_transport_cost_pct": (
                100.0
                * (
                    float(aggregate_by_k[4]["transport_cost_cad"])
                    / float(generic_by_k[20]["transport_cost_cad"])
                    - 1.0
                )
                if float(generic_by_k[20]["transport_cost_cad"]) > 0.0
                else 0.0
            ),
            "class_mean_road_distance_km": float(
                aggregate_by_k[4]["mean_road_distance_km"]
            ),
            "generic_mean_road_distance_km": float(
                generic_by_k[20]["mean_road_distance_km"]
            ),
            "delta_mean_road_distance_km": float(
                aggregate_by_k[4]["mean_road_distance_km"]
            )
            - float(generic_by_k[20]["mean_road_distance_km"]),
        },
        "k5_nearest_generic_k20": {
            "class_total_modules": int(k5_aggregate["total_modules"]),
            "generic_k": 20,
            "class_transport_cost_cad": k5_cost,
            "generic_transport_cost_cad": float(
                generic_by_k[20]["transport_cost_cad"]
            ),
            "delta_transport_cost_cad": k5_cost
            - float(generic_by_k[20]["transport_cost_cad"]),
            "delta_transport_cost_pct": (
                100.0 * (k5_cost / float(generic_by_k[20]["transport_cost_cad"]) - 1.0)
                if float(generic_by_k[20]["transport_cost_cad"]) > 0.0
                else 0.0
            ),
            "class_mean_road_distance_km": float(
                k5_aggregate["mean_road_distance_km"]
            ),
            "generic_mean_road_distance_km": float(
                generic_by_k[20]["mean_road_distance_km"]
            ),
            "delta_mean_road_distance_km": float(
                k5_aggregate["mean_road_distance_km"]
            )
            - float(generic_by_k[20]["mean_road_distance_km"]),
        },
        "exact_module_count_matches": exact_rows,
    }
    headline = {
        "aggregate": {
            "k0": dict(k0_aggregate),
            "k5": dict(k5_aggregate),
            "cost_reduction_vs_k0_cad": k0_cost - k5_cost,
            "cost_reduction_vs_k0_pct": (
                100.0 * (1.0 - k5_cost / k0_cost) if k0_cost > 0.0 else 0.0
            ),
            "mean_reduction_vs_k0_km": float(
                k0_aggregate["mean_road_distance_km"]
            )
            - float(k5_aggregate["mean_road_distance_km"]),
            "module_share_k5": float(k5_aggregate["module_served_share"]),
            "total_modules_k5": int(k5_aggregate["total_modules"]),
            "first_k_marginal_below_1pct": aggregate_first_diminishing,
        }
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
        "technology_claim": False,
        "class_specific_capacity_claim": False,
        "class_specific_module": True,
        "module_capacity": MODULE_CAPACITY_STATUS,
        "investment_advice": False,
        "policy_claim": False,
        "greenfield_siting": True,
        "siting_optimization": "screening_heuristic",
        "transport_only": True,
    }
    method = {
        "engine": (
            "greedy k-median/facility-location screening per class with exact "
            "capacitated transport LP re-evaluation (HiGHS)"
        ),
        "k_values": list(K_VALUES),
        "k_scope": "per_class",
        "class_order": list(CLASS_ORDER),
        "class_share_source": (
            "national_product_chain_v5 product_chain_by_province.csv "
            "(v5.1 E1/E2c class feedstock layer)"
        ),
        "class_split_basis": (
            "province class feedstock divided by the province five-class "
            "feedstock total, applied to each node harvest weight"
        ),
        "candidate_basis": (
            "positive class feedstock supply nodes in provinces with a "
            "published road factor"
        ),
        "candidate_modules_by_class": {
            facility_class: len(candidates_by_class[facility_class])
            for facility_class in CLASS_ORDER
        },
        "excluded_candidate_provinces_by_class": excluded_by_class,
        "existing_facilities": len(facilities),
        "existing_capacity_m3_y": existing_capacity_m3_y,
        "existing_facility_partition": {
            facility_class: {
                "facilities": len(facilities_by_class[facility_class]),
                "capacity_m3_y": float(
                    capacities_by_class[facility_class].sum()
                ),
            }
            for facility_class in CLASS_ORDER
        },
        "capacity_basis": (
            "national_facility_capacity_v3 with the documented 1.2x "
            "provincial floor, partitioned by class and held fixed"
        ),
        "module_basis": (
            "class-specific module at a positive class feedstock supply node; "
            "unlimited throughput in the screen; no class capacity, "
            "technology, cost, product or residue asserted"
        ),
        "objective": (
            "minimise 2026 CAD truck transport cost from transport_cost_v1 "
            "base curve (52.5 m3 payload, empty return); no local-preference "
            "penalty"
        ),
        "distance_method": (
            "haversine x destination province road factor from "
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
            "greedy forward selection per class; at each k the shortlist is "
            f"the top {SHORTLIST_SIZE} candidates by capacity-relaxed distance "
            "savings; every trial and every k is re-evaluated with the exact "
            "capacitated transport LP"
        ),
        "shortlist_size": SHORTLIST_SIZE,
        "optimality": (
            "heuristic screening result per class; not a proven global "
            "optimum; no optimality-gap claim"
        ),
        "lp_solver": "scipy.optimize.linprog(method='highs', presolve=False)",
        "lp_solves": lp_solves,
        "generic_comparison_basis": (
            "same per-class k versus generic shared-module k, and class total "
            "module count matched to generic k where available (k<=20)"
        ),
    }
    counts = {
        "input_nodes": len(nodes),
        "positive_nodes": len(positive_nodes),
        "zero_volume_nodes": len(nodes) - len(positive_nodes),
        "classes": len(CLASS_ORDER),
        "class_demand_nodes": sum(
            len(class_nodes[facility_class]) for facility_class in CLASS_ORDER
        ),
        "candidate_modules": sum(
            len(candidates_by_class[facility_class]) for facility_class in CLASS_ORDER
        ),
        "eligible_facilities": len(facilities),
        "k_values": len(K_VALUES),
        "total_modules_k5": max(K_VALUES) * len(CLASS_ORDER),
        "module_location_rows": len(module_rows),
        "node_assignment_rows_k0": len(node_assignment_k0),
        "node_assignment_rows_k5": len(node_assignment_k5),
        "existing_service_rows": len(existing_rows),
        "split_rows": len(split_rows),
        "generic_comparison_rows": len(generic_comparison_rows),
        "lp_solves": lp_solves,
    }
    input_paths = (
        v10.SUPPLY_NODES_CSV,
        v10.SUPPLY_NODE_SUMMARY_JSON,
        v9.CAPACITY_CSV,
        v9.CAPACITY_SUMMARY,
        v9.HARVEST_CSV,
        v9.BOUNDARY_GEOJSON,
        v9.FACTORS_CSV,
        v9.FACTORS_SUMMARY,
        v9.TRANSPORT_PARAMETERS_CSV,
        v9.TRANSPORT_CURVES_CSV,
        PRODUCT_CHAIN_CSV,
        PRODUCT_CHAIN_SUMMARY,
        GENERIC_SUMMARY_JSON,
        GENERIC_TRADEOFF_CSV,
        V10_SUMMARY_JSON,
    )
    inputs = {
        str(path.relative_to(REPO_ROOT)): _sha256(path)
        for path in input_paths
    }
    caveats = [
        (
            "Transport-only screening: no capital, operating, permitting, "
            "technology, landbase, procurement or facility-design claim."
        ),
        (
            "The class split is a proportional screening allocation of node "
            "harvest using v5.1 province/class feedstock shares; it is not a "
            "mass-balance routing of harvest residue."
        ),
        (
            "The class module is class-specific but has unlimited throughput "
            "in the screen; no class-specific capacity is invented."
        ),
        (
            "The k-series is a greedy heuristic per class, not a globally "
            "optimal siting solution; no optimality-gap claim is made."
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
            "Existing capacity v3 and the 1.2x provincial floor are held "
            "fixed; the existing facility set is partitioned by class."
        ),
        (
            "NT positive nodes are demand nodes but not module hosts because "
            "no NT road factor is published."
        ),
        (
            "Generic siting is class-agnostic with shared modules; class "
            "results are not directly comparable except at k=0 and at exact "
            "total-module-count matches."
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
        "scope": (
            "119 Mm3 class-specific transport-only greenfield siting screen "
            "over five facility classes"
        ),
        "labels": labels,
        "counts": counts,
        "method": method,
        "inputs": inputs,
        "classes": class_summary,
        "headline": headline,
        "generic_comparison": generic_comparison,
        "baselines": {
            "k0_existing_only": dict(k0_aggregate),
            "k5_class_modules": dict(k5_aggregate),
            "generic": generic_summary["headline"],
        },
        "caveats": caveats,
        "validation": validation,
        "reproduce": f".venv/bin/python -m {MODULE_PATH}",
    }

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    _write_csv(
        output / "class_k_tradeoff.csv",
        CLASS_TRADEOFF_FIELDS,
        class_tradeoff_rows,
    )
    _write_csv(
        output / "aggregate_k_tradeoff.csv",
        AGGREGATE_FIELDS,
        aggregate_rows,
    )
    _write_csv(
        output / "class_module_locations.csv",
        MODULE_FIELDS,
        module_rows,
    )
    _write_csv(
        output / "class_feedstock_split.csv",
        SPLIT_FIELDS,
        split_rows,
    )
    _write_csv(
        output / "generic_comparison.csv",
        GENERIC_COMPARISON_FIELDS,
        generic_comparison_rows,
    )
    _write_csv(
        output / "node_assignment_k0.csv",
        NODE_ASSIGNMENT_FIELDS,
        node_assignment_k0,
    )
    _write_csv(
        output / "node_assignment_k5.csv",
        NODE_ASSIGNMENT_FIELDS,
        node_assignment_k5,
    )
    _write_csv(
        output / "existing_capacity_service_k0_k5.csv",
        EXISTING_SERVICE_FIELDS,
        existing_rows,
    )
    _write_json(output / "summary.json", summary)
    (output / REPORT_NAME).write_text(
        _report(
            summary,
            class_tradeoff_rows,
            aggregate_rows,
            module_rows,
            generic_comparison_rows,
            split_rows,
        ),
        encoding="utf-8",
    )
    outputs = {
        path.name: {"bytes": path.stat().st_size, "sha256": _sha256(path)}
        for path in sorted(output.iterdir())
        if path.is_file() and path.name != "manifest.json"
    }
    manifest = {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": generated_utc,
        "status": "complete",
        "labels": labels,
        "inputs": inputs,
        "outputs": outputs,
        "validation": validation,
        "reproduce": summary["reproduce"],
    }
    _write_json(output / "manifest.json", manifest)
    return summary


def main() -> None:
    summary = build()
    aggregate = summary["headline"]["aggregate"]
    print(
        json.dumps(
            {
                "artifact": summary["artifact"],
                "status": summary["status"],
                "classes": {
                    facility_class: {
                        "demand_m3": block["class_feedstock_m3"],
                        "k5_cost_cad": block["k5"]["transport_cost_cad"],
                        "k5_mean_km": block["k5"]["mean_road_distance_km"],
                        "k5_module_share": block["k5"]["module_served_share"],
                    }
                    for facility_class, block in summary["classes"].items()
                },
                "aggregate": {
                    "k0_cost_cad": aggregate["k0"]["transport_cost_cad"],
                    "k5_cost_cad": aggregate["k5"]["transport_cost_cad"],
                    "k5_mean_km": aggregate["k5"]["mean_road_distance_km"],
                    "k5_module_share": aggregate["k5"]["module_served_share"],
                    "cost_reduction_vs_k0_cad": aggregate[
                        "cost_reduction_vs_k0_cad"
                    ],
                },
                "generic_k4_exact_match": summary["generic_comparison"][
                    "k4_exact_count_match"
                ],
                "validation": summary["validation"]["all_passed"],
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
