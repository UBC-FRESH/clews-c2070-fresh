"""Stage B sawmill-residue and bioenergy allocation pilot for TSA24/BC.

The pilot links the Stage A sawmill log assignments to the FFCM residue
outfeed ratios, screens PAP/PEL/BIO recipients, routes every source-recipient
pair on the real NRN BC road graph, and solves a linear programme:

    maximise   product revenue - transport - processing
    subject to source residue balance, recipient-line capacity,
               residue-type eligibility, and link existence

If any included recipient lacks a product price, the driver switches to a
service-maximising cost-min run: first maximise assigned residue, then minimise
transport plus processing.  Prices, volumes, capacities, and locations are
never synthesised.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import statistics
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pulp
from scipy.sparse import csgraph

from . import units
from .build_road_network import (
    DEFAULT_NRN_ZIP,
    FACILITY_SNAP_TOLERANCE_M,
    RoadGraph,
    build_road_graph,
    snap_facilities,
)
from .residue import (
    DENSITY_ODT_M3,
    RESIDUE_TYPE_LABELS,
    RESIDUE_TYPES,
    FacilityLocation,
    ResidueDemandRow,
    ResidueError,
    ResidueSupplyRow,
    build_residue_demand_candidates,
    build_residue_supply,
    eligible_residue_types,
    load_facility_locations,
    load_processing_archive,
    load_sawmill_assignments,
    product_revenue_per_input_odt,
)
from .transport import load_road_rate_inputs, road_rate_for_grade

__all__ = [
    "Allocation",
    "StageBError",
    "StageBLink",
    "StageBResult",
    "StageBValidationReport",
    "build_stage_b_links",
    "run_stage_b",
    "solve_residue_allocation",
    "validate_stage_b",
]

CODE_VERSION = "0.1.0"
DEFAULT_PILOT_DIR = Path(
    "data/derived/national/assignments/tsa24_pilot/sensitivity_ws4_quota"
)
DEFAULT_OUT_DIR = Path("data/derived/national/assignments/tsa24_pilot/stage_b")
_TOLERANCE = 1e-6
_POSITIVE = 1e-9


class StageBError(RuntimeError):
    """Raised when Stage B cannot be built or solved without inventing data."""


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StageBLink:
    source_facility: str
    recipient_facility: str
    distance_km: float
    cost_cad_m3: float
    path_confidence: str
    source_coord_source: str
    recipient_coord_source: str
    source_snap_m: float
    recipient_snap_m: float
    source_component: int | None
    recipient_component: int | None
    network_source: str
    distance_basis: str
    cost_basis: str
    evidence: str

    def to_row(self) -> dict[str, Any]:
        return {
            "source_facility": self.source_facility,
            "recipient_facility": self.recipient_facility,
            "distance_km": self.distance_km,
            "cost_cad_m3": self.cost_cad_m3,
            "path_confidence": self.path_confidence,
            "source_coord_source": self.source_coord_source,
            "recipient_coord_source": self.recipient_coord_source,
            "source_snap_m": self.source_snap_m,
            "recipient_snap_m": self.recipient_snap_m,
            "source_component": (
                "" if self.source_component is None else self.source_component
            ),
            "recipient_component": (
                "" if self.recipient_component is None else self.recipient_component
            ),
            "network_source": self.network_source,
            "distance_basis": self.distance_basis,
            "cost_basis": self.cost_basis,
            "transload_cost_cad_m3": 0.0,
            "capacity_m3": "",
            "capacity_source": "absent",
            "evidence": self.evidence,
        }


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise StageBError(f"required Stage B input is missing: {path}")
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise StageBError(f"Stage B input has no data rows: {path}")
    return rows


def _facility_frame(locations: Sequence[FacilityLocation]) -> pd.DataFrame:
    records = [
        {
            "facility_id": item.facility_id,
            "lat": item.lat,
            "lon": item.lon,
            "coord_source": item.coord_source,
        }
        for item in locations
    ]
    frame = pd.DataFrame.from_records(records)
    if frame.empty:
        raise StageBError("cannot snap an empty facility set")
    if frame["lat"].isna().any() or frame["lon"].isna().any():
        missing = frame.loc[
            frame["lat"].isna() | frame["lon"].isna(), "facility_id"
        ].tolist()
        raise StageBError(f"facilities without coordinates reached the router: {missing}")
    return frame


@dataclass(frozen=True, slots=True)
class _RouteTables:
    """Hub-and-spoke FFCM road routes used only as an explicit fallback."""

    tl_edges: Mapping[str, tuple[tuple[str, float], ...]]
    facility_to_tl: Mapping[str, tuple[tuple[str, float], ...]]
    tl_to_facility: Mapping[str, tuple[tuple[str, float], ...]]
    direct: Mapping[tuple[str, str], float]


def _load_route_tables(repo_root: Path) -> _RouteTables:
    path = repo_root / "metadata/ffcm-archive/parsed/routes.csv"
    rows = _read_csv(path)
    tl_edges: dict[str, list[tuple[str, float]]] = defaultdict(list)
    facility_to_tl: dict[str, list[tuple[str, float]]] = defaultdict(list)
    tl_to_facility: dict[str, list[tuple[str, float]]] = defaultdict(list)
    direct: dict[tuple[str, str], float] = {}
    for row in rows:
        if str(row.get("transport_mode", "")).strip().upper() != "ROAD":
            continue
        try:
            distance = float(str(row.get("total_length", "")).strip())
        except ValueError:
            continue
        if not math.isfinite(distance) or distance <= 0.0:
            continue
        origins = [
            value.strip()
            for value in str(row.get("origin_list", "")).split(";")
            if value.strip()
        ]
        destinations = [
            value.strip()
            for value in str(row.get("destination_list", "")).split(";")
            if value.strip()
        ]
        if len(origins) != len(destinations):
            raise StageBError("FFCM route origin/destination lists are not parallel")
        for origin, destination in zip(origins, destinations):
            origin_is_tl = origin.startswith("TL")
            destination_is_tl = destination.startswith("TL")
            if origin_is_tl and destination_is_tl:
                tl_edges[origin].append((destination, distance))
                tl_edges[destination].append((origin, distance))
            elif destination_is_tl:
                facility_to_tl[origin].append((destination, distance))
            elif origin_is_tl:
                tl_to_facility[destination].append((origin, distance))
            else:
                key = (origin, destination)
                current = direct.get(key)
                if current is None or distance < current:
                    direct[key] = distance
                reverse = (destination, origin)
                current = direct.get(reverse)
                if current is None or distance < current:
                    direct[reverse] = distance
    if not tl_edges:
        raise StageBError("FFCM routes.csv has no road TL-TL edges")
    return _RouteTables(
        tl_edges={key: tuple(value) for key, value in tl_edges.items()},
        facility_to_tl={
            key: tuple(value) for key, value in facility_to_tl.items()
        },
        tl_to_facility={
            key: tuple(value) for key, value in tl_to_facility.items()
        },
        direct=direct,
    )


def _dijkstra(
    source: str, graph: Mapping[str, Sequence[tuple[str, float]]]
) -> dict[str, float]:
    distances: dict[str, float] = {source: 0.0}
    queue: list[tuple[float, str]] = [(0.0, source)]
    while queue:
        distance, node = queue.pop(0)
        if distance > distances.get(node, math.inf) + _TOLERANCE:
            continue
        for neighbour, leg in graph.get(node, ()):
            candidate = distance + leg
            if candidate < distances.get(neighbour, math.inf) - _TOLERANCE:
                distances[neighbour] = candidate
                queue.append((candidate, neighbour))
        queue.sort(key=lambda item: item[0])
    return distances


def _ffcm_road_distance(
    tables: _RouteTables,
    source: str,
    recipient: str,
    tl_cache: dict[str, dict[str, float]],
) -> float | None:
    """Shortest road distance through the FFCM hub-and-spoke structure.

    Facility-to-facility route rows are used only for the exact source/recipient
    pair.  They are not chained as graph edges: the archive's direct-route
    lengths are independent measurements, not a routable facility network.
    """
    best = tables.direct.get((source, recipient))
    for source_tl, source_leg in tables.facility_to_tl.get(source, ()):
        if source_tl not in tl_cache:
            tl_cache[source_tl] = _dijkstra(source_tl, tables.tl_edges)
        distances = tl_cache[source_tl]
        for recipient_tl, recipient_leg in tables.tl_to_facility.get(
            recipient, ()
        ):
            hub_distance = distances.get(recipient_tl)
            if hub_distance is None:
                continue
            candidate = source_leg + hub_distance + recipient_leg
            if best is None or candidate < best:
                best = candidate
    if best is None or not math.isfinite(best) or best <= 0.0:
        return None
    return best


def _path_confidence(
    source: FacilityLocation, recipient: FacilityLocation
) -> str:
    if source.location_quality == "defensible":
        return "real_road_network"
    return "real_road_network_source_proxy_location"


def build_stage_b_links(
    repo_root: Path,
    sources: Sequence[FacilityLocation],
    recipients: Sequence[FacilityLocation],
    *,
    nrn_zip: Path = DEFAULT_NRN_ZIP,
    graph: RoadGraph | None = None,
) -> tuple[tuple[StageBLink, ...], dict[str, str]]:
    """Route every source-recipient pair on the real road graph.

    Returns the links and a per-recipient status map.  A missing graph path may
    fall back to the road-only FFCM route graph with an explicit confidence
    label; otherwise the recipient is reported as unlinked.
    """
    repo_root = Path(repo_root)
    if not sources or not recipients:
        raise StageBError("both sources and recipients are required for routing")
    rates, capacities = load_road_rate_inputs(repo_root)
    rate = road_rate_for_grade(rates, capacities, grade_id="RESIDUE")
    cost_per_m3_km = rate.cost_per_m3_km(DENSITY_ODT_M3)

    endpoint_by_id = {item.facility_id: item for item in (*sources, *recipients)}
    frame = _facility_frame(list(endpoint_by_id.values()))
    if graph is None:
        graph = build_road_graph(nrn_zip)
    snap = snap_facilities(graph, frame, tolerance_m=FACILITY_SNAP_TOLERANCE_M)
    snap_by_id = {
        str(row["facility_id"]): row for _, row in snap.iterrows()
    }

    route_tables = _load_route_tables(repo_root)
    tl_cache: dict[str, dict[str, float]] = {}
    source_distances: dict[int, np.ndarray] = {}
    links: list[StageBLink] = []
    status_by_recipient: dict[str, str] = {}
    for recipient in recipients:
        recipient_row = snap_by_id[recipient.facility_id]
        linked = False
        if (
            str(recipient_row["snap_status"]) == "snapped"
            and not pd.isna(recipient_row["node_index"])
        ):
            recipient_component = int(recipient_row["component"])
            recipient_node = int(recipient_row["node_index"])
            for source in sources:
                source_row = snap_by_id[source.facility_id]
                if (
                    str(source_row["snap_status"]) != "snapped"
                    or pd.isna(source_row["node_index"])
                ):
                    continue
                source_component = int(source_row["component"])
                if source_component != recipient_component:
                    continue
                source_node = int(source_row["node_index"])
                if source_node not in source_distances:
                    source_distances[source_node] = csgraph.dijkstra(
                        graph.matrix,
                        directed=False,
                        indices=[source_node],
                    )[0]
                distance_km = (
                    float(source_distances[source_node][recipient_node]) / 1000.0
                )
                if not math.isfinite(distance_km) or distance_km < 0.0:
                    continue
                links.append(
                    _make_link(
                        source,
                        recipient,
                        distance_km,
                        cost_per_m3_km,
                        path_confidence=_path_confidence(source, recipient),
                        source_snap_m=float(source_row["snap_m"]),
                        recipient_snap_m=float(recipient_row["snap_m"]),
                        source_component=source_component,
                        recipient_component=recipient_component,
                        network_source=graph.gpkg_member,
                        distance_basis=(
                            "nrn_bc_road_graph_shortest_path_epsg3005;"
                            f"source_node={source_node};recipient_node={recipient_node}"
                        ),
                        evidence=(
                            "nrn_bc_real_road_network;"
                            f"snap_source_m={float(source_row['snap_m']):.3f};"
                            f"snap_recipient_m={float(recipient_row['snap_m']):.3f};"
                            "route_capacity_absent"
                        ),
                    )
                )
                linked = True
        if linked:
            status_by_recipient[recipient.facility_id] = "linked_real_road_network"
            continue
        added_fallback = False
        for source in sources:
            distance = _ffcm_road_distance(
                route_tables,
                source.facility_id,
                recipient.facility_id,
                tl_cache,
            )
            if distance is None:
                continue
            links.append(
                _make_link(
                    source,
                    recipient,
                    distance,
                    cost_per_m3_km,
                    path_confidence="ffcm_route_fallback_road",
                    source_snap_m=0.0,
                    recipient_snap_m=0.0,
                    source_component=None,
                    recipient_component=None,
                    network_source="ffcm_routes_road_hub_graph",
                    distance_basis=(
                        "ffcm_hub_and_spoke_road_graph_shortest_path"
                    ),
                    evidence=(
                        "no_real_road_path;ffcm_route_fallback_road;"
                        "facility_to_TL+TL_path+TL_to_facility;"
                        "no_direct_facility_edge_chaining;"
                        "road_mode_only;no_transload;route_capacity_absent"
                    ),
                )
            )
            added_fallback = True
        status_by_recipient[recipient.facility_id] = (
            "linked_ffcm_fallback_road"
            if added_fallback
            else "unlinked_no_road_path"
        )
    links.sort(key=lambda item: (item.source_facility, item.recipient_facility))
    return tuple(links), status_by_recipient


def _make_link(
    source: FacilityLocation,
    recipient: FacilityLocation,
    distance_km: float,
    cost_per_m3_km: float,
    *,
    path_confidence: str,
    source_snap_m: float,
    recipient_snap_m: float,
    source_component: int | None,
    recipient_component: int | None,
    network_source: str,
    distance_basis: str,
    evidence: str,
) -> StageBLink:
    if distance_km < 0.0 or not math.isfinite(distance_km):
        raise StageBError(
            f"invalid distance {distance_km} from {source.facility_id} to "
            f"{recipient.facility_id}"
        )
    if distance_km == 0.0:
        evidence = evidence + ";zero_distance_shared_road_node"
    return StageBLink(
        source_facility=source.facility_id,
        recipient_facility=recipient.facility_id,
        distance_km=distance_km,
        cost_cad_m3=distance_km * cost_per_m3_km,
        path_confidence=path_confidence,
        source_coord_source=source.coord_source,
        recipient_coord_source=recipient.coord_source,
        source_snap_m=source_snap_m,
        recipient_snap_m=recipient_snap_m,
        source_component=source_component,
        recipient_component=recipient_component,
        network_source=network_source,
        distance_basis=distance_basis,
        cost_basis=(
            "stage_a_transporter_rates:BC_residue_truck;"
            "fuel_plus_operating_per_hour_over_speed;no_transload"
        ),
        evidence=evidence,
    )


# ---------------------------------------------------------------------------
# Allocation model
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Allocation:
    period: str
    source_facility: str
    residue_type: str
    recipient_facility: str
    recipient_line: str
    facility_class: str
    odt: float
    volume_m3: float
    moisture_wet_basis: float
    distance_km: float
    transport_cost_cad_m3: float
    transport_cost_cad: float
    processing_cost_cad_per_input_odt: float
    processing_cost_cad: float
    product: str
    product_output: float
    product_output_unit: str
    product_price: float
    product_price_unit: str
    product_revenue_cad: float
    net_contribution_cad: float
    path_confidence: str
    evidence: str

    def to_row(self) -> dict[str, Any]:
        return {
            "period": self.period,
            "source_facility": self.source_facility,
            "residue_type": self.residue_type,
            "recipient_facility": self.recipient_facility,
            "recipient_line": self.recipient_line,
            "facility_class": self.facility_class,
            "odt": self.odt,
            "volume_m3": self.volume_m3,
            "moisture_wet_basis": self.moisture_wet_basis,
            "distance_km": self.distance_km,
            "transport_cost_cad_m3": self.transport_cost_cad_m3,
            "transport_cost_cad": self.transport_cost_cad,
            "processing_cost_cad_per_input_odt": (
                self.processing_cost_cad_per_input_odt
            ),
            "processing_cost_cad": self.processing_cost_cad,
            "product": self.product,
            "product_output": self.product_output,
            "product_output_unit": self.product_output_unit,
            "product_price": self.product_price,
            "product_price_unit": self.product_price_unit,
            "product_revenue_cad": self.product_revenue_cad,
            "net_contribution_cad": self.net_contribution_cad,
            "path_confidence": self.path_confidence,
            "evidence": self.evidence,
        }


@dataclass(frozen=True, slots=True)
class UnassignedResidue:
    period: str
    source_facility: str
    residue_type: str
    odt: float
    volume_m3: float
    reason: str
    detail: str

    def to_row(self) -> dict[str, Any]:
        return {
            "period": self.period,
            "source_facility": self.source_facility,
            "residue_type": self.residue_type,
            "odt": self.odt,
            "volume_m3": self.volume_m3,
            "reason": self.reason,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class StageBResult:
    period: str
    status: str
    objective_mode: str
    objective_value_cad: float
    solver_objective_cad: float
    allocations: tuple[Allocation, ...]
    unassigned: tuple[UnassignedResidue, ...]
    objective_decomposition: tuple[dict[str, Any], ...]
    model_stats: Mapping[str, Any]

    @property
    def assigned_odt(self) -> float:
        return math.fsum(item.odt for item in self.allocations)


def _transport_per_odt(cost_cad_m3: float) -> float:
    return cost_cad_m3 / DENSITY_ODT_M3


def _clean(value: object) -> str:
    return "".join(char if char.isalnum() else "_" for char in str(value))


def _variable_value(variable: pulp.LpVariable) -> float:
    value = variable.varValue
    return 0.0 if value is None else float(value)


def _solve_status(problem: pulp.LpProblem) -> str:
    return pulp.LpStatus.get(problem.status, f"status_{problem.status}")


def solve_residue_allocation(
    supplies: Sequence[ResidueSupplyRow],
    demand: Sequence[ResidueDemandRow],
    links: Sequence[StageBLink],
    *,
    period: str = "2026",
    objective_mode: str = "profit_max",
    moisture_limit_wet_basis: float | None = None,
) -> StageBResult:
    """Solve the residue transportation/allocation LP.

    ``profit_max`` maximises revenue minus transport and processing.
    ``cost_min`` first maximises assigned residue, then minimises transport and
    processing, so a missing price never leaves residue unassigned by default.

    The source balance is written as ``sum(x) <= supply``; the unassigned
    residual is derived after the solve.  That keeps the reported mass balance
    exact even when the solver leaves a tolerance-level primal residual.
    """
    if objective_mode not in {"profit_max", "cost_min"}:
        raise StageBError(f"unknown objective mode {objective_mode!r}")
    if moisture_limit_wet_basis is not None:
        limit = float(moisture_limit_wet_basis)
        if not math.isfinite(limit) or not 0.0 <= limit < 1.0:
            raise StageBError("moisture limit must be in [0, 1)")

    link_by_pair = {
        (link.source_facility, link.recipient_facility): link for link in links
    }
    feasible: dict[
        tuple[str, str, str],
        tuple[ResidueSupplyRow, ResidueDemandRow, StageBLink],
    ] = {}
    for supply in supplies:
        if supply.odt <= 0.0:
            continue
        for demand_row in demand:
            if demand_row.accepted_residue_type != supply.residue_type:
                continue
            if demand_row.status != "included":
                continue
            link = link_by_pair.get(
                (supply.source_facility, demand_row.recipient_facility)
            )
            if link is None:
                continue
            key = (
                supply.source_facility,
                supply.residue_type,
                demand_row.processing_line,
            )
            feasible[key] = (supply, demand_row, link)

    def build_problem() -> tuple[
        pulp.LpProblem,
        dict[tuple[str, str, str], pulp.LpVariable],
    ]:
        problem = pulp.LpProblem("stage_b_residue_allocation", pulp.LpMaximize)
        x: dict[tuple[str, str, str], pulp.LpVariable] = {}
        for key in sorted(feasible):
            x[key] = pulp.LpVariable("x__" + _clean("__".join(key)), lowBound=0.0)
        for supply in supplies:
            if supply.odt <= 0.0:
                continue
            key = (supply.source_facility, supply.residue_type)
            terms = [
                variable
                for variable_key, variable in x.items()
                if variable_key[0] == supply.source_facility
                and variable_key[1] == supply.residue_type
            ]
            problem += (
                pulp.lpSum(terms) <= supply.odt,
                "supply__" + _clean("__".join(key)),
            )
        for demand_row in demand:
            if demand_row.status != "included":
                continue
            terms = [
                variable
                for variable_key, variable in x.items()
                if variable_key[2] == demand_row.processing_line
            ]
            if terms:
                problem += (
                    pulp.lpSum(terms) <= demand_row.capacity_odt_y,
                    "capacity__" + _clean(demand_row.processing_line),
                )
        if moisture_limit_wet_basis is not None:
            for demand_row in demand:
                if demand_row.status != "included":
                    continue
                terms = [
                    variable
                    for variable_key, variable in x.items()
                    if variable_key[2] == demand_row.processing_line
                ]
                if not terms:
                    continue
                problem += (
                    pulp.lpSum(
                        variable * feasible[variable_key][0].moisture_wet_basis
                        for variable_key, variable in x.items()
                        if variable_key[2] == demand_row.processing_line
                    )
                    <= float(moisture_limit_wet_basis) * pulp.lpSum(terms),
                    "moisture__" + _clean(demand_row.processing_line),
                )
        return problem, x

    solver = pulp.PULP_CBC_CMD(msg=False)
    if objective_mode == "profit_max":
        problem, variables = build_problem()
        problem += pulp.lpSum(
            variable
            * (
                feasible[key][1].product_revenue_cad_per_input_odt
                - feasible[key][1].operating_cost_cad_per_input_odt
                - _transport_per_odt(feasible[key][2].cost_cad_m3)
            )
            for key, variable in variables.items()
        )
        try:
            problem.solve(solver)
        except pulp.PulpSolverError as error:  # pragma: no cover - solver guard
            raise StageBError(f"Stage B solver failed: {error}") from error
        status = _solve_status(problem)
        if status != "Optimal":
            raise StageBError(
                f"Stage B LP did not solve to optimality: status={status}"
            )
        raw_solver_objective = float(pulp.value(problem.objective) or 0.0)
        x_values = {
            key: _variable_value(variable) for key, variable in variables.items()
        }
    else:
        first, first_variables = build_problem()
        first += pulp.lpSum(variable for variable in first_variables.values())
        try:
            first.solve(solver)
        except pulp.PulpSolverError as error:  # pragma: no cover - solver guard
            raise StageBError(f"Stage B cost-min stage 1 failed: {error}") from error
        if _solve_status(first) != "Optimal":
            raise StageBError(
                f"Stage B cost-min stage 1 did not solve: {_solve_status(first)}"
            )
        assigned_floor = float(pulp.value(first.objective) or 0.0)
        second, second_variables = build_problem()
        second += (
            pulp.lpSum(variable for variable in second_variables.values())
            >= assigned_floor - 1e-6,
            "assigned_total_floor",
        )
        second.sense = pulp.LpMinimize
        second += pulp.lpSum(
            variable
            * (
                feasible[key][1].operating_cost_cad_per_input_odt
                + _transport_per_odt(feasible[key][2].cost_cad_m3)
            )
            for key, variable in second_variables.items()
        )
        try:
            second.solve(solver)
        except pulp.PulpSolverError as error:  # pragma: no cover - solver guard
            raise StageBError(f"Stage B cost-min stage 2 failed: {error}") from error
        status = _solve_status(second)
        if status != "Optimal":
            raise StageBError(
                f"Stage B cost-min stage 2 did not solve: status={status}"
            )
        raw_solver_objective = float(pulp.value(second.objective) or 0.0)
        x_values = {
            key: _variable_value(variable)
            for key, variable in second_variables.items()
        }

    supply_mass = {
        (supply.source_facility, supply.residue_type): supply.odt
        for supply in supplies
    }
    assigned_mass: dict[tuple[str, str], float] = defaultdict(float)
    for key, odt in x_values.items():
        assigned_mass[(key[0], key[1])] += odt
    for key, total in assigned_mass.items():
        limit = supply_mass[key]
        if total <= limit:
            continue
        if total > limit * (1.0 + 1e-6):
            raise StageBError(
                f"solver assigned {total:.6f} odt above supply {limit:.6f} "
                f"for {key}"
            )
        factor = limit / total
        for variable_key in x_values:
            if (variable_key[0], variable_key[1]) == key:
                x_values[variable_key] *= factor

    allocations: list[Allocation] = []
    for key, odt in sorted(x_values.items()):
        if odt <= _POSITIVE:
            continue
        supply, demand_row, link = feasible[key]
        volume_m3 = units.convert(
            odt,
            "odt",
            "m3_green",
            context=units.ConversionContext(
                basic_density_odt_m3=DENSITY_ODT_M3,
                density_source=units.DEFAULT_DENSITY_SOURCE,
            ),
        )
        transport_cost = odt * _transport_per_odt(link.cost_cad_m3)
        processing_cost = odt * demand_row.operating_cost_cad_per_input_odt
        product_output = odt * demand_row.product_output_per_input_odt
        product_revenue = odt * demand_row.product_revenue_cad_per_input_odt
        allocations.append(
            Allocation(
                period=period,
                source_facility=supply.source_facility,
                residue_type=supply.residue_type,
                recipient_facility=demand_row.recipient_facility,
                recipient_line=demand_row.processing_line,
                facility_class=demand_row.facility_class,
                odt=odt,
                volume_m3=volume_m3,
                moisture_wet_basis=supply.moisture_wet_basis,
                distance_km=link.distance_km,
                transport_cost_cad_m3=link.cost_cad_m3,
                transport_cost_cad=transport_cost,
                processing_cost_cad_per_input_odt=(
                    demand_row.operating_cost_cad_per_input_odt
                ),
                processing_cost_cad=processing_cost,
                product=demand_row.product,
                product_output=product_output,
                product_output_unit=demand_row.product_output_unit,
                product_price=demand_row.product_price,
                product_price_unit=demand_row.product_price_unit,
                product_revenue_cad=product_revenue,
                net_contribution_cad=(
                    product_revenue - transport_cost - processing_cost
                ),
                path_confidence=link.path_confidence,
                evidence=(
                    f"residue_ratio={supply.ratio_odt_per_m3_log:.12g} odt/m3;"
                    f"transport={link.distance_km:.3f} km @ "
                    f"{link.cost_cad_m3:.6f} CAD/m3;"
                    f"processing={demand_row.operating_cost_cad_per_input_odt:.6f}"
                    " CAD/odt_in;"
                    f"price_status={demand_row.price_status}"
                ),
            )
        )

    assigned_by_key: dict[tuple[str, str], float] = defaultdict(float)
    for item in allocations:
        assigned_by_key[(item.source_facility, item.residue_type)] += item.odt
    unassigned: list[UnassignedResidue] = []
    for supply in supplies:
        if supply.odt <= 0.0:
            continue
        key = (supply.source_facility, supply.residue_type)
        leftover = max(0.0, supply.odt - assigned_by_key[key])
        if leftover <= max(1e-9, 1e-7 * supply.odt):
            continue
        matching = [
            row
            for row in demand
            if row.accepted_residue_type == supply.residue_type
            and row.status == "included"
        ]
        matching_links = [
            link
            for link in links
            if link.source_facility == supply.source_facility
            and link.recipient_facility
            in {row.recipient_facility for row in matching}
        ]
        if not supply.allocatable:
            reason = "no_consumer_class"
            detail = supply.allocation_note
        elif not matching:
            reason = "no_eligible_recipient"
            detail = f"no included {supply.residue_type} recipient"
        elif not matching_links:
            reason = "no_transport_link"
            detail = "no road or FFCM road fallback link to an eligible recipient"
        elif (
            moisture_limit_wet_basis is not None
            and supply.moisture_wet_basis > moisture_limit_wet_basis
        ):
            reason = "moisture_limit"
            detail = (
                f"residue moisture {supply.moisture_wet_basis:.3f} exceeds "
                f"limit {moisture_limit_wet_basis:.3f}"
            )
        elif objective_mode == "profit_max" and not any(
            feasible[key][1].product_revenue_cad_per_input_odt
            - feasible[key][1].operating_cost_cad_per_input_odt
            - _transport_per_odt(feasible[key][2].cost_cad_m3)
            > 0.0
            for key in feasible
            if key[0] == supply.source_facility
            and key[1] == supply.residue_type
        ):
            reason = "negative_net_contribution"
            detail = "no eligible recipient has a positive net contribution"
        else:
            reason = "capacity_exhausted"
            detail = "eligible recipient capacity was fully used"
        unassigned.append(
            UnassignedResidue(
                period=period,
                source_facility=supply.source_facility,
                residue_type=supply.residue_type,
                odt=leftover,
                volume_m3=units.convert(
                    leftover,
                    "odt",
                    "m3_green",
                    context=units.ConversionContext(
                        basic_density_odt_m3=DENSITY_ODT_M3,
                        density_source=units.DEFAULT_DENSITY_SOURCE,
                    ),
                ),
                reason=reason,
                detail=detail,
            )
        )

    assigned_total = math.fsum(item.odt for item in allocations)
    revenue = math.fsum(item.product_revenue_cad for item in allocations)
    transport = math.fsum(item.transport_cost_cad for item in allocations)
    processing = math.fsum(item.processing_cost_cad for item in allocations)
    net = revenue - transport - processing
    decomposition = (
        {
            "component": "product_revenue",
            "value_cad": revenue,
            "cad_per_assigned_odt": revenue / assigned_total if assigned_total else 0.0,
            "sense": "positive",
            "notes": "assigned odt x product revenue per input odt",
        },
        {
            "component": "transport_cost",
            "value_cad": transport,
            "cad_per_assigned_odt": (
                transport / assigned_total if assigned_total else 0.0
            ),
            "sense": "negative",
            "notes": "assigned odt x road transporter cost; no transload",
        },
        {
            "component": "processing_cost",
            "value_cad": processing,
            "cad_per_assigned_odt": (
                processing / assigned_total if assigned_total else 0.0
            ),
            "sense": "negative",
            "notes": "assigned odt x FFCM line operating cost per output unit",
        },
        {
            "component": "net_contribution",
            "value_cad": net,
            "cad_per_assigned_odt": net / assigned_total if assigned_total else 0.0,
            "sense": "objective_profit_max",
            "notes": "product revenue - transport - processing",
        },
        {
            "component": "solver_objective",
            "value_cad": (
                net if objective_mode == "profit_max" else transport + processing
            ),
            "cad_per_assigned_odt": (
                (net if objective_mode == "profit_max" else transport + processing)
                / assigned_total
                if assigned_total
                else 0.0
            ),
            "sense": objective_mode,
            "notes": (
                "objective recomputed from normalised allocations; "
                "raw CBC value is in model_stats"
            ),
        },
    )
    return StageBResult(
        period=period,
        status=status,
        objective_mode=objective_mode,
        objective_value_cad=(
            net if objective_mode == "profit_max" else transport + processing
        ),
        solver_objective_cad=(
            net if objective_mode == "profit_max" else transport + processing
        ),
        allocations=tuple(allocations),
        unassigned=tuple(unassigned),
        objective_decomposition=decomposition,
        model_stats={
            "variable_count": len(x_values),
            "feasible_pair_count": len(feasible),
            "assigned_odt": assigned_total,
            "solver_status": status,
            "objective_mode": objective_mode,
            "raw_cbc_objective_cad": raw_solver_objective,
            "moisture_limit_wet_basis": moisture_limit_wet_basis,
        },
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StageBCheck:
    check_id: str
    passed: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "passed": self.passed,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class StageBValidationReport:
    checks: tuple[StageBCheck, ...]

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    @property
    def failed_checks(self) -> tuple[StageBCheck, ...]:
        return tuple(check for check in self.checks if not check.passed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "failed_check_count": len(self.failed_checks),
            "checks": [check.to_dict() for check in self.checks],
        }

    def raise_if_failed(self) -> None:
        if self.passed:
            return
        details = "; ".join(
            f"{check.check_id}: {check.detail}" for check in self.failed_checks
        )
        raise StageBError(f"Stage B validation failed: {details}")


def _close(left: float, right: float, tolerance: float = _TOLERANCE) -> bool:
    return abs(left - right) <= tolerance * max(1.0, abs(left), abs(right))


def _exceeds(value: float, limit: float, tolerance: float = _TOLERANCE) -> bool:
    return value > limit + tolerance * max(1.0, abs(limit))


def _finite(value: object) -> bool:
    return math.isfinite(float(value))  # type: ignore[arg-type]


def validate_stage_b(
    supplies: Sequence[ResidueSupplyRow],
    demand: Sequence[ResidueDemandRow],
    links: Sequence[StageBLink],
    result: StageBResult,
    *,
    tolerance: float = _TOLERANCE,
) -> StageBValidationReport:
    """Recompute conservation, capacity, eligibility, units, and objective."""
    checks: list[StageBCheck] = []

    def add(check_id: str, passed: bool, detail: str) -> None:
        checks.append(
            StageBCheck(check_id=check_id, passed=bool(passed), detail=detail)
        )

    add("solver_status", result.status == "Optimal", f"status={result.status}")

    supply_by_key = {
        (row.source_facility, row.residue_type): row for row in supplies
    }
    demand_by_line = {row.processing_line: row for row in demand}
    link_by_pair = {
        (link.source_facility, link.recipient_facility): link for link in links
    }
    assigned: dict[tuple[str, str], float] = defaultdict(float)
    balance_errors: list[str] = []
    for item in result.allocations:
        assigned[(item.source_facility, item.residue_type)] += item.odt
    unassigned_by_key = {
        (item.source_facility, item.residue_type): item
        for item in result.unassigned
    }
    for key, supply in supply_by_key.items():
        leftover = unassigned_by_key.get(key)
        unassigned_odt = leftover.odt if leftover is not None else 0.0
        if not _close(assigned[key] + unassigned_odt, supply.odt, tolerance):
            balance_errors.append(
                f"{key}: assigned {assigned[key]:.6f} + unassigned "
                f"{unassigned_odt:.6f} != supply {supply.odt:.6f}"
            )
        if assigned[key] < -tolerance or unassigned_odt < -tolerance:
            balance_errors.append(f"{key}: negative assigned or unassigned residue")
    add(
        "residue_balance",
        not balance_errors,
        "assigned + unassigned equals generated residue for every source/type"
        if not balance_errors
        else "; ".join(balance_errors[:5]),
    )

    reference_errors: list[str] = []
    for item in result.allocations:
        if (item.source_facility, item.residue_type) not in supply_by_key:
            reference_errors.append(
                f"{item.source_facility}/{item.residue_type}: unknown supply"
            )
        if item.recipient_line not in demand_by_line:
            reference_errors.append(
                f"{item.recipient_line}: unknown recipient line"
            )
    add(
        "allocation_reference_integrity",
        not reference_errors,
        "all allocations reference a declared supply row and recipient line"
        if not reference_errors
        else "; ".join(reference_errors[:5]),
    )

    eligibility_errors: list[str] = []
    capacity_errors: list[str] = []
    received_by_line: dict[str, float] = defaultdict(float)
    for item in result.allocations:
        demand_row = demand_by_line.get(item.recipient_line)
        if demand_row is None:
            continue
        if item.residue_type != demand_row.accepted_residue_type:
            eligibility_errors.append(
                f"{item.recipient_line}: {item.residue_type} is not eligible"
            )
        if item.residue_type not in eligible_residue_types(item.facility_class):
            eligibility_errors.append(
                f"{item.recipient_facility}: {item.facility_class} cannot accept "
                f"{item.residue_type}"
            )
        received_by_line[item.recipient_line] += item.odt
        if _exceeds(
            received_by_line[item.recipient_line],
            demand_row.capacity_odt_y,
            tolerance,
        ):
            capacity_errors.append(
                f"{item.recipient_line}: capacity {demand_row.capacity_odt_y:.3f} "
                f"< received {received_by_line[item.recipient_line]:.3f}"
            )
    add(
        "residue_type_eligibility",
        not eligibility_errors,
        "every allocation uses an eligible residue type and recipient class"
        if not eligibility_errors
        else "; ".join(eligibility_errors[:5]),
    )
    add(
        "recipient_capacity",
        not capacity_errors,
        "no recipient line exceeds its FFCM infeed capacity"
        if not capacity_errors
        else "; ".join(capacity_errors[:5]),
    )

    transport_errors: list[str] = []
    for item in result.allocations:
        link = link_by_pair.get(
            (item.source_facility, item.recipient_facility)
        )
        if link is None:
            transport_errors.append(
                f"{item.source_facility}->{item.recipient_facility}: missing link"
            )
            continue
        if not _finite(link.distance_km) or link.distance_km < 0.0:
            transport_errors.append(
                f"{item.source_facility}->{item.recipient_facility}: bad distance"
            )
        if not _close(item.transport_cost_cad_m3, link.cost_cad_m3, tolerance):
            transport_errors.append(
                f"{item.source_facility}->{item.recipient_facility}: cost "
                f"{item.transport_cost_cad_m3} != link {link.cost_cad_m3}"
            )
        if link.path_confidence not in {
            "real_road_network",
            "real_road_network_source_proxy_location",
            "ffcm_route_fallback_road",
        }:
            transport_errors.append(
                f"{item.source_facility}->{item.recipient_facility}: "
                f"confidence {link.path_confidence}"
            )
    add(
        "transport_link_existence",
        not transport_errors,
        "every allocation has a finite, cost-consistent road or fallback link"
        if not transport_errors
        else "; ".join(transport_errors[:5]),
    )

    unit_errors: list[str] = []
    for supply in supplies:
        if not _close(
            supply.volume_m3, supply.odt / DENSITY_ODT_M3, tolerance
        ):
            unit_errors.append(f"{supply.source_facility}/{supply.residue_type}: m3")
        if not 0.0 <= supply.moisture_wet_basis < 1.0:
            unit_errors.append(
                f"{supply.source_facility}/{supply.residue_type}: moisture"
            )
    for demand_row in demand:
        if demand_row.status != "included":
            continue
        if not _close(
            demand_row.capacity_m3_y,
            demand_row.capacity_odt_y / DENSITY_ODT_M3,
            tolerance,
        ):
            unit_errors.append(f"{demand_row.processing_line}: capacity m3")
        if not _close(
            demand_row.operating_cost_cad_per_input_odt,
            demand_row.operating_cost
            * demand_row.product_output_per_input_odt,
            tolerance,
        ):
            unit_errors.append(
                f"{demand_row.processing_line}: processing cost per input odt"
            )
        if demand_row.price_status == "available":
            try:
                expected_revenue, _basis = product_revenue_per_input_odt(
                    demand_row.product_output_per_input_odt,
                    demand_row.product_output_unit,
                    demand_row.product_price,
                    demand_row.product_price_unit,
                )
            except ResidueError as error:
                unit_errors.append(
                    f"{demand_row.processing_line}: revenue unit: {error}"
                )
            else:
                if not _close(
                    demand_row.product_revenue_cad_per_input_odt,
                    expected_revenue,
                    tolerance,
                ):
                    unit_errors.append(
                        f"{demand_row.processing_line}: revenue per input odt"
                    )
    for item in result.allocations:
        if not _close(item.volume_m3, item.odt / DENSITY_ODT_M3, tolerance):
            unit_errors.append(f"{item.source_facility}: allocation m3")
    add(
        "unit_consistency",
        not unit_errors,
        "volume/ODT, capacity, moisture, and mass-price bases reconcile"
        if not unit_errors
        else "; ".join(unit_errors[:5]),
    )

    revenue = math.fsum(item.product_revenue_cad for item in result.allocations)
    transport = math.fsum(item.transport_cost_cad for item in result.allocations)
    processing = math.fsum(item.processing_cost_cad for item in result.allocations)
    net = revenue - transport - processing
    objective_errors: list[str] = []
    components = {
        str(row["component"]): float(row["value_cad"])
        for row in result.objective_decomposition
    }
    if not _close(components.get("product_revenue", 0.0), revenue, tolerance):
        objective_errors.append("product_revenue")
    if not _close(components.get("transport_cost", 0.0), transport, tolerance):
        objective_errors.append("transport_cost")
    if not _close(components.get("processing_cost", 0.0), processing, tolerance):
        objective_errors.append("processing_cost")
    if not _close(components.get("net_contribution", 0.0), net, tolerance):
        objective_errors.append("net_contribution")
    if result.objective_mode == "profit_max":
        if not _close(result.solver_objective_cad, net, tolerance):
            objective_errors.append("solver_objective_vs_net")
    else:
        if not _close(
            result.solver_objective_cad, transport + processing, tolerance
        ):
            objective_errors.append("solver_objective_vs_cost")
    if not _close(result.objective_value_cad, result.solver_objective_cad, tolerance):
        objective_errors.append("reported_objective")
    expected_objective = (
        net if result.objective_mode == "profit_max" else transport + processing
    )
    raw_objective = float(
        result.model_stats.get("raw_cbc_objective_cad", expected_objective)
    )
    raw_delta = abs(raw_objective - expected_objective)
    if not _close(raw_objective, expected_objective, tolerance):
        objective_errors.append(f"raw_cbc_objective_delta={raw_delta:.6f}")
    add(
        "objective_reconciliation",
        not objective_errors,
        (
            "assignment components reproduce the normalised objective; "
            f"raw CBC delta {raw_delta:.6f} CAD"
            if not objective_errors
            else f"mismatched components: {objective_errors}"
        ),
    )

    finite_errors: list[str] = []
    for item in result.allocations:
        values = (
            item.odt,
            item.volume_m3,
            item.distance_km,
            item.transport_cost_cad,
            item.processing_cost_cad,
            item.product_revenue_cad,
            item.net_contribution_cad,
        )
        if any(not _finite(value) for value in values) or item.odt < 0.0:
            finite_errors.append(f"{item.source_facility}->{item.recipient_line}")
    for item in result.unassigned:
        if not _finite(item.odt) or item.odt < 0.0:
            finite_errors.append(f"unassigned {item.source_facility}")
    add(
        "finite_nonnegative_values",
        not finite_errors,
        "all allocation and slack values are finite and non-negative"
        if not finite_errors
        else "; ".join(finite_errors[:5]),
    )

    return StageBValidationReport(checks=tuple(checks))


# ---------------------------------------------------------------------------
# Tables and reporting
# ---------------------------------------------------------------------------


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]], columns: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=list(columns), lineterminator="\n"
        )
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in columns})


def _utilization_rows(
    demand: Sequence[ResidueDemandRow],
    result: StageBResult,
) -> list[dict[str, Any]]:
    received_by_facility: dict[str, float] = defaultdict(float)
    received_by_line: dict[str, float] = defaultdict(float)
    revenue_by_facility: dict[str, float] = defaultdict(float)
    for item in result.allocations:
        received_by_facility[item.recipient_facility] += item.odt
        received_by_line[item.recipient_line] += item.odt
        revenue_by_facility[item.recipient_facility] += item.product_revenue_cad

    facilities: dict[str, dict[str, Any]] = {}
    for row in demand:
        if row.status != "included":
            continue
        bucket = facilities.setdefault(
            row.recipient_facility,
            {
                "facility_id": row.recipient_facility,
                "facility_class": row.facility_class,
                "capacity_odt_y": 0.0,
                "capacity_m3_y": 0.0,
                "products": set(),
                "lines": set(),
                "price_status": set(),
                "coord_source": row.coord_source,
                "coord_confidence": row.coord_confidence,
            },
        )
        bucket["capacity_odt_y"] += row.capacity_odt_y
        bucket["capacity_m3_y"] += row.capacity_m3_y
        bucket["products"].add(row.product)
        bucket["lines"].add(row.processing_line)
        bucket["price_status"].add(row.price_status)
    rows: list[dict[str, Any]] = []
    for facility_id in sorted(facilities):
        bucket = facilities[facility_id]
        received = received_by_facility[facility_id]
        capacity = float(bucket["capacity_odt_y"])
        utilization = received / capacity if capacity > 0.0 else 0.0
        rows.append(
            {
                "facility_id": facility_id,
                "facility_class": bucket["facility_class"],
                "capacity_odt_y": capacity,
                "capacity_m3_y": bucket["capacity_m3_y"],
                "received_odt": received,
                "received_m3": received / DENSITY_ODT_M3,
                "unused_capacity_odt": max(0.0, capacity - received),
                "utilization": utilization,
                "capacity_binding": _exceeds(received, capacity, _TOLERANCE)
                or _close(received, capacity, _TOLERANCE),
                "product": ";".join(sorted(bucket["products"])),
                "processing_lines": ";".join(sorted(bucket["lines"])),
                "product_revenue_cad": revenue_by_facility[facility_id],
                "price_status": ";".join(sorted(bucket["price_status"])),
                "coord_source": bucket["coord_source"],
                "coord_confidence": bucket["coord_confidence"],
            }
        )
    return rows


def _balance_rows(
    supplies: Sequence[ResidueSupplyRow],
    result: StageBResult,
) -> list[dict[str, Any]]:
    assigned: dict[tuple[str, str], float] = defaultdict(float)
    for item in result.allocations:
        assigned[(item.source_facility, item.residue_type)] += item.odt
    unassigned = {
        (item.source_facility, item.residue_type): item
        for item in result.unassigned
    }
    rows: list[dict[str, Any]] = []
    for supply in supplies:
        key = (supply.source_facility, supply.residue_type)
        leftover = unassigned.get(key)
        assigned_odt = min(assigned[key], supply.odt)
        unassigned_odt = max(0.0, supply.odt - assigned_odt)
        rows.append(
            {
                "source_facility": supply.source_facility,
                "residue_type": supply.residue_type,
                "generated_odt": supply.odt,
                "generated_m3": supply.volume_m3,
                "assigned_odt": assigned_odt,
                "assigned_m3": assigned_odt / DENSITY_ODT_M3,
                "unassigned_odt": unassigned_odt,
                "unassigned_m3": unassigned_odt / DENSITY_ODT_M3,
                "assigned_share": assigned_odt / supply.odt if supply.odt else 0.0,
                "unassigned_reason": leftover.reason if leftover else "",
                "unassigned_detail": leftover.detail if leftover else "",
                "allocatable": "yes" if supply.allocatable else "no",
            }
        )
    rows.sort(key=lambda row: (row["source_facility"], row["residue_type"]))
    generated_odt = math.fsum(row["generated_odt"] for row in rows)
    assigned_odt = math.fsum(row["assigned_odt"] for row in rows)
    rows.append(
        {
            "source_facility": "TOTAL",
            "residue_type": "all",
            "generated_odt": generated_odt,
            "generated_m3": generated_odt / DENSITY_ODT_M3,
            "assigned_odt": assigned_odt,
            "assigned_m3": assigned_odt / DENSITY_ODT_M3,
            "unassigned_odt": generated_odt - assigned_odt,
            "unassigned_m3": (generated_odt - assigned_odt) / DENSITY_ODT_M3,
            "assigned_share": assigned_odt / generated_odt if generated_odt else 0.0,
            "unassigned_reason": "",
            "unassigned_detail": "",
            "allocatable": "",
        }
    )
    return rows


def _bioenergy_rows(
    supplies: Sequence[ResidueSupplyRow],
    result: StageBResult,
    demand: Sequence[ResidueDemandRow],
) -> list[dict[str, Any]]:
    assigned_by_type: dict[str, float] = defaultdict(float)
    bio_odt = 0.0
    electricity_mwh = 0.0
    demand_by_line = {row.processing_line: row for row in demand}
    for item in result.allocations:
        assigned_by_type[item.residue_type] += item.odt
        if item.facility_class == "BIO":
            bio_odt += item.odt
            row = demand_by_line.get(item.recipient_line)
            if row is None:
                raise StageBError(
                    f"BIO allocation references unknown line {item.recipient_line}"
                )
            if row.product_output_unit == "kwh":
                electricity_mwh += item.odt * row.product_output_per_input_odt / 1000.0
            else:
                raise StageBError(
                    f"BIO line {item.recipient_line} product unit is "
                    f"{row.product_output_unit!r}, expected kwh"
                )
    generated_by_type: dict[str, float] = defaultdict(float)
    for supply in supplies:
        generated_by_type[supply.residue_type] += supply.odt
    rows: list[dict[str, Any]] = []
    for residue_type in RESIDUE_TYPES:
        generated = generated_by_type[residue_type]
        assigned = min(assigned_by_type[residue_type], generated)
        if generated - assigned <= max(1e-9, 1e-7 * generated):
            assigned = generated
        bio = assigned if residue_type == "hogfuel" else 0.0
        rows.append(
            {
                "scope": "residue_type",
                "residue_type": residue_type,
                "residue_label": RESIDUE_TYPE_LABELS[residue_type],
                "generated_odt": generated,
                "generated_m3": generated / DENSITY_ODT_M3,
                "assigned_odt": assigned,
                "unassigned_odt": generated - assigned,
                "bioenergy_odt": bio,
                "bioenergy_share_of_generated": (
                    bio / generated if generated else 0.0
                ),
                "bioenergy_share_of_assigned": bio / assigned if assigned else 0.0,
                "electricity_mwh": electricity_mwh if residue_type == "hogfuel" else 0.0,
            }
        )
    total_generated = math.fsum(row["generated_odt"] for row in rows)
    total_assigned = math.fsum(row["assigned_odt"] for row in rows)
    bio_odt = next(
        row["bioenergy_odt"] for row in rows if row["residue_type"] == "hogfuel"
    )
    rows.append(
        {
            "scope": "TOTAL",
            "residue_type": "all",
            "residue_label": "all residue",
            "generated_odt": total_generated,
            "generated_m3": total_generated / DENSITY_ODT_M3,
            "assigned_odt": total_assigned,
            "unassigned_odt": total_generated - total_assigned,
            "bioenergy_odt": bio_odt,
            "bioenergy_share_of_generated": (
                bio_odt / total_generated if total_generated else 0.0
            ),
            "bioenergy_share_of_assigned": (
                bio_odt / total_assigned if total_assigned else 0.0
            ),
            "electricity_mwh": electricity_mwh,
        }
    )
    return rows


def _md_table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join("---" for _ in headers) + "|",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(value) for value in row) + " |")
    return "\n".join(lines)


def _report_markdown(
    *,
    supplies: Sequence[ResidueSupplyRow],
    coverage: Sequence[Mapping[str, Any]],
    demand: Sequence[ResidueDemandRow],
    screen: Sequence[Mapping[str, Any]],
    links: Sequence[StageBLink],
    result: StageBResult,
    validation: StageBValidationReport,
    balance: Sequence[Mapping[str, Any]],
    utilization: Sequence[Mapping[str, Any]],
    bioenergy: Sequence[Mapping[str, Any]],
    objective_mode_reason: str,
    stage_a_run: str,
    source_quality_counts: Mapping[str, int],
) -> str:
    generated = math.fsum(item.odt for item in supplies)
    assigned = result.assigned_odt
    source_count = len({item.source_facility for item in supplies})

    by_type: dict[str, dict[str, Any]] = {
        residue_type: {
            "generated": 0.0,
            "assigned": 0.0,
            "unassigned": 0.0,
            "reasons": set(),
        }
        for residue_type in RESIDUE_TYPES
    }
    for row in balance:
        if row["source_facility"] == "TOTAL":
            continue
        bucket = by_type[row["residue_type"]]
        bucket["generated"] += float(row["generated_odt"])
        bucket["assigned"] += float(row["assigned_odt"])
        bucket["unassigned"] += float(row["unassigned_odt"])
        if row["unassigned_reason"]:
            bucket["reasons"].add(str(row["unassigned_reason"]))
    supply_rows = [
        [
            RESIDUE_TYPE_LABELS[residue_type],
            f"{bucket['generated']:.3f}",
            f"{bucket['generated'] / 0.45:.3f}",
            f"{bucket['assigned']:.3f}",
            f"{bucket['unassigned']:.3f}",
            f"{bucket['assigned'] / bucket['generated'] * 100.0:.2f}%"
            if bucket["generated"]
            else "0.00%",
            ";".join(sorted(bucket["reasons"])),
        ]
        for residue_type, bucket in by_type.items()
    ]
    supply_rows.append(
        [
            "all residue",
            f"{generated:.3f}",
            f"{generated / 0.45:.3f}",
            f"{assigned:.3f}",
            f"{generated - assigned:.3f}",
            f"{assigned / generated * 100.0:.2f}%" if generated else "0.00%",
            "",
        ]
    )
    coverage_rows = []
    for residue_type in RESIDUE_TYPES:
        subset = [
            row for row in coverage if row["residue_type"] == residue_type
        ]
        included = [row for row in subset if row["status"] == "included"]
        ratios = sorted(
            float(row["ratio_odt_per_infeed_unit"])
            for row in included
            if row["ratio_odt_per_infeed_unit"] != ""
        )
        coverage_rows.append(
            [
                RESIDUE_TYPE_LABELS[residue_type],
                len({row["source_facility"] for row in subset}),
                len({row["source_facility"] for row in included}),
                len({row["source_facility"] for row in subset})
                - len({row["source_facility"] for row in included}),
                f"{ratios[0]:.9f}" if ratios else "",
                f"{ratios[-1]:.9f}" if ratios else "",
            ]
        )
    demand_rows = [
        [
            row.recipient_facility,
            row.facility_class,
            row.processing_line,
            row.accepted_residue_type,
            f"{row.capacity_odt_y:.1f}",
            f"{row.operating_cost_cad_per_input_odt:.2f}",
            row.product,
            f"{row.product_price:.2f} {row.product_price_unit}",
            row.price_status,
        ]
        for row in demand
    ]
    utilization_rows = [
        [
            row["facility_id"],
            row["facility_class"],
            f"{row['capacity_odt_y']:.1f}",
            f"{row['received_odt']:.1f}",
            f"{row['utilization'] * 100.0:.2f}%",
            "yes" if row["capacity_binding"] else "no",
        ]
        for row in utilization
    ]
    binding_count = sum(1 for row in utilization if row["capacity_binding"])
    link_stats: dict[str, list[float]] = {}
    for link in links:
        link_stats.setdefault(link.path_confidence, []).append(link.distance_km)
    transport_summary_rows = [
        [
            confidence,
            len(distances),
            f"{min(distances):.2f}",
            f"{statistics.median(distances):.2f}",
            f"{max(distances):.2f}",
        ]
        for confidence, distances in sorted(link_stats.items())
    ]
    used_pairs: dict[tuple[str, str], dict[str, Any]] = {}
    for item in result.allocations:
        bucket = used_pairs.setdefault(
            (item.source_facility, item.recipient_facility),
            {
                "odt": 0.0,
                "distance_km": item.distance_km,
                "cost_cad_m3": item.transport_cost_cad_m3,
                "path_confidence": item.path_confidence,
            },
        )
        bucket["odt"] += item.odt
    transport_rows = [
        [
            source,
            recipient,
            f"{values['odt']:.1f}",
            f"{values['distance_km']:.2f}",
            f"{values['cost_cad_m3']:.3f}",
            values["path_confidence"],
        ]
        for (source, recipient), values in sorted(
            used_pairs.items(), key=lambda item: item[1]["odt"], reverse=True
        )[:15]
    ]
    bio_total = bioenergy[-1]
    failed = validation.failed_checks
    report = f"""# Stage B sawmill-residue and bioenergy allocation pilot (TSA24/BC)

## Scope and status

This is a diagnostic Stage B pilot for the TSA24/BC Stage A run
`{stage_a_run}`.  It uses the Stage A sawmill log assignments, FFCM
processing-line outfeed ratios, the real NRN BC road graph, and FFCM commodity
prices.  It is not a mill-level fibre supply estimate and it does not claim that
proxy-located sawmills are at their proxy coordinates.

- Period: `{result.period}`
- Stage A run: `{stage_a_run}`
- Sawmill sources: **{source_count}** ({source_quality_counts.get('defensible', 0)} corroborated, {source_quality_counts.get('proxy', 0)} TL-hub proxy, {source_quality_counts.get('missing', 0)} missing)
- Solver status: **{result.status}**
- Validation: **{'PASS' if validation.passed else 'FAIL'}**
- Objective mode: `{result.objective_mode}` ({objective_mode_reason})
- Residue generated: **{generated:.3f} ODT** ({generated / 0.45:.3f} m3 green equivalent)
- Residue assigned: **{assigned:.3f} ODT**
- Residue unassigned: **{generated - assigned:.3f} ODT**
- Bioenergy (BIO) share of generated residue: **{bio_total['bioenergy_share_of_generated'] * 100.0:.2f}%**
- Bioenergy share of assigned residue: **{bio_total['bioenergy_share_of_assigned'] * 100.0:.2f}%**
- Electricity from allocated hogfuel: **{bio_total['electricity_mwh']:.3f} MWh**
- Capacity-binding recipient facilities: **{binding_count} of {len(utilization)}**

## Residue supply (Stage A sawmill log volume x FFCM outfeed ratio)

Every ratio is `outfeed rate / infeed rate` from `processing_line_flows.csv`.
The declared outfeed is oven-dry, so ODT is the primary quantity.  Green m3 is
`ODT / 0.45` from the Stage A residue-ledger basic density.  The conversion is
recorded line by line in `residue_conversions.csv`.

{_md_table(
    ["type", "generated ODT", "generated m3", "assigned ODT", "unassigned ODT", "assigned share", "unassigned reasons"],
    supply_rows,
)}

### Sawmill outfeed ratio coverage

{_md_table(
    ["type", "sources screened", "sources with ratio", "sources excluded", "min ratio", "max ratio"],
    coverage_rows,
)}

Missing ratios are excluded and listed in `residue_supply_coverage.csv`; no
ratio was replaced by an assumption.  The full `{len(coverage)}` source/type
rows are in `residue_supply_coverage.csv`; `{len(supplies)}` supply rows are in
`residue_supply.csv`.  `residue_balance.csv` reports unassigned ODT as the
exact supply residual, so every row closes; `unassigned_residue.csv` lists the
material leftovers with reasons.  Sub-`1e-7` solver-tolerance residuals are
folded into the balance residual rather than given a false reason.
`internally_used_woodfuel` is reported as `other` and is not allocatable in this
pilot.

## Recipients (PAP/PEL/BIO)

Only BC facilities with a corroborated Mills DB coordinate and a real (or
explicitly flagged FFCM road-fallback) path are included.  Recipient capacity is
the FFCM line `infeed_rate` normalised to ODT; no capacity was added or scaled.
Products and prices come from `commodities.csv`; `markets.csv` defines feedstock
markets only and carries no product price.  A missing price would put the run in
cost-min mode; the `price_status` column records availability.

{_md_table(
    ["facility", "class", "line", "accepts", "capacity ODT/y", "processing CAD/ODT-in", "product", "price", "price status"],
    demand_rows,
)}

{len(screen)} recipient candidates were screened; excluded rows and reasons are
in `stage_b_recipient_screen.csv`.  Stage A's own residue-consuming facilities
without corroborated coordinates are excluded here; their Stage A volumes still
appear in the Stage A tables.

## Transport

All links use the NRN BC 2018 road graph (EPSG:3005 shortest path) and the
Stage A `BC_residue_truck` rate (fuel + operating/speed).  There is no transload
for direct road.  Proxy-located sources share low-confidence TL-hub nodes, so
their NRN source-to-recipient distances are identical; source-level allocation
is degenerate for those links.  FFCM fallback links use the facility route
attachments and can differ by source.  {sum(1 for link in links if link.distance_km == 0.0)}
source-recipient pairs share a road node (0 km) and are flagged
`zero_distance_shared_road_node` in the link evidence.

{_md_table(
    ["path confidence", "links", "min km", "median km", "max km"],
    transport_summary_rows,
)}

Largest source-recipient pairs by assigned ODT (first 15 of {len(used_pairs)}):

{_md_table(
    ["source", "recipient", "assigned ODT", "distance km", "cost CAD/m3", "path confidence"],
    transport_rows,
)}

Full link evidence, snap distances, and components are in
`stage_b_transport_links.csv`.

## Stage B results

{_md_table(
    ["facility", "class", "capacity ODT/y", "received ODT", "utilization", "capacity binding"],
    utilization_rows,
)}

### Objective decomposition (CAD)

{_md_table(
    ["component", "value CAD", "CAD per assigned ODT", "sense"],
    [
        [
            row["component"],
            f"{row['value_cad']:.2f}",
            f"{row['cad_per_assigned_odt']:.2f}",
            row["sense"],
        ]
        for row in result.objective_decomposition
    ],
)}

Reported objective: **CAD {result.objective_value_cad:,.2f}**
(`{result.objective_mode}`); normalized objective: CAD
{result.solver_objective_cad:,.2f}; raw CBC objective: CAD
{float(result.model_stats.get('raw_cbc_objective_cad', result.solver_objective_cad)):,.2f}.
The assignment-level components reproduce the normalized value; the raw CBC
delta is caused by the solver-tolerance allocation normalization and is
reported in `validation_summary.json`.

## Validation

{_md_table(
    ["check", "passed", "detail"],
    [
        [check.check_id, "yes" if check.passed else "no", check.detail]
        for check in validation.checks
    ],
)}

## Data gaps and next steps

- {source_quality_counts.get('proxy', 0)} of {source_count} Stage A sawmills
  carry `tl_anchor_proxy` coordinates.  NRN haul distances from those sources
  are identical within a proxy node and are the largest transport limitation.
- Residue-consuming facilities without corroborated coordinates are excluded
  as recipients.  Their Stage A primary-feedstock volumes are unchanged.
- FFCM sawmill outfeed ratios are applied as constant returns to the Stage A
  log volume; the FFCM base case is not a closed mill mass balance.
- `internally_used_woodfuel` is reported but not allocated; no consumer class
  is declared for it in this pilot.
- Road-only transport is modelled.  Island recipients without a road path are
  reported as unlinked; no water or rail residue route is assumed.
- FFCM commodity prices are used as product-price proxies; they may represent
  delivered market prices rather than mill-gate realizations.
- Next: corroborate SAW coordinates, add residue-specific density/moisture, and
  add water/rail residue options for disconnected coastal recipients.
"""
    if failed:
        report += "\n## Failed checks\n\n" + "\n".join(
            f"- `{check.check_id}`: {check.detail}" for check in failed
        )
    return report


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _csv_row_count(path: Path) -> int:
    with path.open(encoding="utf-8") as handle:
        return max(0, sum(1 for _ in handle) - 1)


def _relative(path: Path, root: Path) -> str:
    try:
        return str(Path(path).absolute().relative_to(Path(root).absolute()))
    except ValueError:
        return str(path)


def _manifest(
    *,
    repo_root: Path,
    pilot_dir: Path,
    out_dir: Path,
    inputs: Sequence[Path],
    outputs: Sequence[Path],
    config: Mapping[str, Any],
    counts: Mapping[str, Any],
    assumptions: Sequence[str],
) -> dict[str, Any]:
    return {
        "code_version": CODE_VERSION,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "pilot_dir": _relative(pilot_dir, repo_root),
        "out_dir": _relative(out_dir, repo_root),
        "inputs": [
            {
                "path": _relative(path, repo_root),
                "sha256": _sha256(path),
                "bytes": path.stat().st_size,
            }
            for path in inputs
        ],
        "outputs": [
            {
                "path": _relative(path, repo_root),
                "sha256": _sha256(path),
                "bytes": path.stat().st_size,
                "rows": (
                    _csv_row_count(path) if path.suffix == ".csv" else None
                ),
            }
            for path in outputs
        ],
        "config": dict(config),
        "counts": dict(counts),
        "assumptions": list(assumptions),
    }


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StageBRun:
    result: StageBResult
    validation: StageBValidationReport
    supplies: tuple[ResidueSupplyRow, ...]
    demand: tuple[ResidueDemandRow, ...]
    links: tuple[StageBLink, ...]
    out_dir: Path


def run_stage_b(
    repo_root: Path,
    *,
    pilot_dir: Path = DEFAULT_PILOT_DIR,
    out_dir: Path = DEFAULT_OUT_DIR,
    period: str = "2026",
    objective_mode: str = "auto",
    moisture_limit_wet_basis: float | None = None,
    nrn_zip: Path = DEFAULT_NRN_ZIP,
) -> StageBRun:
    """Build, solve, validate, and write one Stage B pilot run."""
    repo_root = Path(repo_root)
    pilot_dir = repo_root / pilot_dir if not Path(pilot_dir).is_absolute() else Path(pilot_dir)
    out_dir = repo_root / out_dir if not Path(out_dir).is_absolute() else Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    archive = load_processing_archive(repo_root)
    assignments = load_sawmill_assignments(pilot_dir / "facility_assignment.csv")
    facilities = load_facility_locations(pilot_dir / "facilities_bc.csv")
    facility_by_id = {item.facility_id: item for item in facilities}
    built = build_residue_supply(archive, assignments)
    supplies = built.supplies
    demand_candidates = build_residue_demand_candidates(archive, facilities)

    source_ids = sorted({row.source_facility for row in supplies})
    sources = [facility_by_id[source_id] for source_id in source_ids]
    routed_sources = [
        source
        for source in sources
        if source.lat is not None and source.lon is not None
    ]
    recipient_candidates = [
        row
        for row in demand_candidates
        if row.status == "candidate"
        and row.location_quality == "defensible"
        and row.recipient_facility in facility_by_id
    ]
    recipient_locations = [
        facility_by_id[facility_id]
        for facility_id in sorted(
            {row.recipient_facility for row in recipient_candidates}
        )
    ]
    if not routed_sources:
        raise StageBError("no sawmill source has a usable location")
    if not recipient_locations:
        raise StageBError("no PAP/PEL/BIO recipient has a defensible location")

    links, link_status = build_stage_b_links(
        repo_root,
        routed_sources,
        recipient_locations,
        nrn_zip=nrn_zip,
    )
    linked_recipients = {
        link.recipient_facility for link in links
    }
    demand: list[ResidueDemandRow] = []
    for row in recipient_candidates:
        if row.recipient_facility in linked_recipients:
            demand.append(replace(row, status="included", status_reason=""))
        else:
            demand.append(
                replace(
                    row,
                    status="excluded",
                    status_reason=link_status.get(
                        row.recipient_facility, "unlinked_no_road_path"
                    ),
                )
            )
    included_demand = tuple(row for row in demand if row.status == "included")
    if not included_demand:
        raise StageBError("no recipient has a road or FFCM road-fallback link")

    if objective_mode == "auto":
        missing_prices = sorted(
            {
                row.facility_class
                for row in included_demand
                if row.price_status != "available"
            }
        )
        if missing_prices:
            objective_mode = "cost_min"
            objective_reason = (
                "missing product price for classes "
                f"{missing_prices}; service-maximising cost-min fallback"
            )
        else:
            objective_mode = "profit_max"
            objective_reason = "all included recipient classes have FFCM prices"
    elif objective_mode == "profit_max":
        missing_prices = sorted(
            {
                row.facility_class
                for row in included_demand
                if row.price_status != "available"
            }
        )
        if missing_prices:
            raise StageBError(
                "profit-max requested but product prices are missing for "
                f"{missing_prices}"
            )
        objective_reason = "explicit profit-max run; all included prices available"
    elif objective_mode == "cost_min":
        objective_reason = "explicit service-maximising cost-min run"
    else:
        raise StageBError(f"unknown objective mode {objective_mode!r}")

    result = solve_residue_allocation(
        supplies,
        included_demand,
        links,
        period=period,
        objective_mode=objective_mode,
        moisture_limit_wet_basis=moisture_limit_wet_basis,
    )
    validation = validate_stage_b(
        supplies, included_demand, links, result
    )

    screen_rows = _screen_rows(demand_candidates, demand, link_status, links)
    balance = _balance_rows(supplies, result)
    utilization = _utilization_rows(included_demand, result)
    bioenergy = _bioenergy_rows(supplies, result, included_demand)

    supply_columns = [
        "source_facility",
        "residue_type",
        "volume_m3",
        "odt",
        "moisture_wet_basis",
        "unit_basis",
        "evidence",
        "processing_line",
        "outfeed_commodity",
        "outfeed_value",
        "outfeed_unit",
        "infeed_rate",
        "infeed_unit",
        "ratio_odt_per_infeed_unit",
        "ratio_odt_per_m3_log",
        "stage_a_assigned_m3",
        "density_odt_m3",
        "allocatable",
        "allocation_note",
    ]
    coverage_columns = [
        "source_facility",
        "processing_line",
        "residue_type",
        "status",
        "reason",
        "ratio_odt_per_infeed_unit",
        "stage_a_assigned_m3",
    ]
    conversion_columns = [
        "source_facility",
        "residue_type",
        "processing_line",
        "infeed_rate",
        "infeed_unit",
        "outfeed_commodity",
        "outfeed_value",
        "outfeed_unit",
        "ratio_odt_per_infeed_unit",
        "stage_a_assigned_m3",
        "residue_odt",
        "density_odt_m3",
        "residue_m3",
        "conversion_chain",
    ]
    demand_columns = list(included_demand[0].to_row())
    screen_columns = list(screen_rows[0]) if screen_rows else []
    link_columns = list(links[0].to_row()) if links else [
        "source_facility",
        "recipient_facility",
    ]
    assignment_columns = (
        list(result.allocations[0].to_row())
        if result.allocations
        else [
            "period",
            "source_facility",
            "residue_type",
            "recipient_facility",
            "recipient_line",
            "facility_class",
            "odt",
            "volume_m3",
            "moisture_wet_basis",
            "distance_km",
            "transport_cost_cad_m3",
            "transport_cost_cad",
            "processing_cost_cad_per_input_odt",
            "processing_cost_cad",
            "product",
            "product_output",
            "product_output_unit",
            "product_price",
            "product_price_unit",
            "product_revenue_cad",
            "net_contribution_cad",
            "path_confidence",
            "evidence",
        ]
    )
    utilization_columns = list(utilization[0]) if utilization else [
        "facility_id",
        "facility_class",
    ]
    balance_columns = list(balance[0]) if balance else ["source_facility"]
    bioenergy_columns = list(bioenergy[0]) if bioenergy else ["scope"]
    objective_columns = [
        "component",
        "value_cad",
        "cad_per_assigned_odt",
        "sense",
        "notes",
    ]
    unassigned_columns = (
        list(result.unassigned[0].to_row())
        if result.unassigned
        else ["period", "source_facility", "residue_type", "odt", "volume_m3", "reason", "detail"]
    )

    outputs: list[Path] = []
    paths = {
        "residue_supply": out_dir / "residue_supply.csv",
        "residue_supply_coverage": out_dir / "residue_supply_coverage.csv",
        "residue_conversions": out_dir / "residue_conversions.csv",
        "residue_demand": out_dir / "residue_demand.csv",
        "recipient_screen": out_dir / "stage_b_recipient_screen.csv",
        "transport_links": out_dir / "stage_b_transport_links.csv",
        "assignment": out_dir / "stage_b_assignment.csv",
        "utilization": out_dir / "stage_b_utilization.csv",
        "balance": out_dir / "residue_balance.csv",
        "bioenergy": out_dir / "bioenergy_share.csv",
        "objective": out_dir / "objective_decomposition.csv",
        "unassigned": out_dir / "unassigned_residue.csv",
    }
    _write_csv(
        paths["residue_supply"],
        [row.to_row() for row in supplies],
        supply_columns,
    )
    _write_csv(
        paths["residue_supply_coverage"], built.coverage, coverage_columns
    )
    _write_csv(
        paths["residue_conversions"], built.conversions, conversion_columns
    )
    _write_csv(
        paths["residue_demand"],
        [row.to_row() for row in included_demand],
        demand_columns,
    )
    _write_csv(paths["recipient_screen"], screen_rows, screen_columns)
    _write_csv(paths["transport_links"], [link.to_row() for link in links], link_columns)
    _write_csv(
        paths["assignment"],
        [item.to_row() for item in result.allocations],
        assignment_columns,
    )
    _write_csv(paths["utilization"], utilization, utilization_columns)
    _write_csv(paths["balance"], balance, balance_columns)
    _write_csv(paths["bioenergy"], bioenergy, bioenergy_columns)
    _write_csv(
        paths["objective"],
        result.objective_decomposition,
        objective_columns,
    )
    _write_csv(
        paths["unassigned"],
        [item.to_row() for item in result.unassigned],
        unassigned_columns,
    )
    outputs = list(paths.values())

    source_quality_counts: dict[str, int] = defaultdict(int)
    for source in sources:
        source_quality_counts[source.location_quality] += 1
    report_text = _report_markdown(
        supplies=supplies,
        coverage=built.coverage,
        demand=included_demand,
        screen=screen_rows,
        links=links,
        result=result,
        validation=validation,
        balance=balance,
        utilization=utilization,
        bioenergy=bioenergy,
        objective_mode_reason=objective_reason,
        stage_a_run=pilot_dir.name,
        source_quality_counts=source_quality_counts,
    )
    report_path = out_dir / "STAGE_B_REPORT.md"
    report_path.write_text(report_text, encoding="utf-8")
    outputs.append(report_path)

    validation_payload = {
        "run_id": "tsa24_bc_stage_b_pilot",
        "scenario_id": "tsa24_bc_stage_b_pilot",
        "stage_a_run": pilot_dir.name,
        "period": period,
        "status": "pass" if validation.passed else "fail",
        "objective_mode": result.objective_mode,
        "objective_mode_reason": objective_reason,
        "model_stats": dict(result.model_stats),
        "validation": validation.to_dict(),
        "counts": {
            "source_count": len(sources),
            "routed_source_count": len(routed_sources),
            "supply_row_count": len(supplies),
            "recipient_candidate_count": len(demand_candidates),
            "included_recipient_line_count": len(included_demand),
            "transport_link_count": len(links),
            "allocation_count": len(result.allocations),
            "unassigned_count": len(result.unassigned),
        },
        "data_gaps": [
            (
                "all three Stage A sawmills use the same low-confidence TL-hub "
                "proxy coordinate (54.01, -122.73); source-level transport "
                "distances are identical for NRN links and source allocation is "
                "degenerate"
            ),
            (
                "Stage A residue-consuming facilities BC-PAP-964, BC-PEL-210, "
                "and BC-PEL-383 have no corroborated coordinate and are excluded"
            ),
            (
                "FFCM sawmill outfeed ratios are applied linearly and the FFCM "
                "base case is not a closed mill mass balance"
            ),
            (
                "internally_used_woodfuel is reported as other and is not "
                "allocatable"
            ),
            (
                "road transport only; island recipients without a road path are "
                "unlinked and no water or rail residue route is assumed"
            ),
            "route capacity is absent; transport links are uncapped",
        ],
        "assumptions": [
            (
                "residue ODT is outfeed value x Stage A log volume / FFCM line "
                "infeed"
            ),
            "green m3 uses 0.45 ODT/m3 from the Stage A residue ledger",
            "residue moisture is 0.45 wet basis from the Stage A residue grade",
            "recipient capacity is the FFCM line infeed, not an added nameplate",
            (
                "product prices are FFCM commodity prices on the declared "
                "product mass or energy basis; CAD/t is read as the declared "
                "tonne basis"
            ),
        ],
    }
    validation_path = out_dir / "validation_summary.json"
    validation_path.write_text(
        json.dumps(validation_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    outputs.append(validation_path)

    inputs = [
        pilot_dir / "facility_assignment.csv",
        pilot_dir / "facilities_bc.csv",
        repo_root / "metadata/ffcm-archive/parsed/processing_lines.csv",
        repo_root / "metadata/ffcm-archive/parsed/processing_line_flows.csv",
        repo_root / "metadata/ffcm-archive/parsed/commodities.csv",
        repo_root / "metadata/ffcm-archive/parsed/markets.csv",
        repo_root / "metadata/ffcm-archive/parsed/transporters.csv",
        repo_root / "metadata/ffcm-archive/parsed/transporter_materials.csv",
        repo_root / "metadata/ffcm-archive/parsed/routes.csv",
        Path(nrn_zip) if Path(nrn_zip).is_absolute() else repo_root / nrn_zip,
    ]
    manifest_path = out_dir / "manifest.json"
    manifest = _manifest(
        repo_root=repo_root,
        pilot_dir=pilot_dir,
        out_dir=out_dir,
        inputs=inputs,
        outputs=outputs,
        config={
            "period": period,
            "objective_mode": result.objective_mode,
            "objective_mode_reason": objective_reason,
            "moisture_limit_wet_basis": moisture_limit_wet_basis,
            "density_odt_m3": DENSITY_ODT_M3,
            "nrn_zip": _relative(
                Path(nrn_zip) if Path(nrn_zip).is_absolute() else repo_root / nrn_zip,
                repo_root,
            ),
            "facility_snap_tolerance_m": FACILITY_SNAP_TOLERANCE_M,
        },
        counts={
            "supplies": len(supplies),
            "included_demand_lines": len(included_demand),
            "links": len(links),
            "allocations": len(result.allocations),
            "unassigned": len(result.unassigned),
        },
        assumptions=validation_payload["assumptions"],
    )
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    return StageBRun(
        result=result,
        validation=validation,
        supplies=tuple(supplies),
        demand=tuple(included_demand),
        links=tuple(links),
        out_dir=out_dir,
    )


def _screen_rows(
    candidates: Sequence[ResidueDemandRow],
    final: Sequence[ResidueDemandRow],
    link_status: Mapping[str, str],
    links: Sequence[StageBLink],
) -> list[dict[str, Any]]:
    final_by_key = {
        (row.recipient_facility, row.processing_line): row for row in final
    }
    link_count: dict[str, int] = defaultdict(int)
    min_cost: dict[str, float] = {}
    for link in links:
        link_count[link.recipient_facility] += 1
        current = min_cost.get(link.recipient_facility)
        if current is None or link.cost_cad_m3 < current:
            min_cost[link.recipient_facility] = link.cost_cad_m3
    rows: list[dict[str, Any]] = []
    for candidate in candidates:
        final_row = final_by_key.get(
            (candidate.recipient_facility, candidate.processing_line)
        )
        status = final_row.status if final_row else "excluded"
        reason = final_row.status_reason if final_row else candidate.status_reason
        rows.append(
            {
                "recipient_facility": candidate.recipient_facility,
                "facility_class": candidate.facility_class,
                "processing_line": candidate.processing_line,
                "accepted_residue_type": candidate.accepted_residue_type,
                "location_quality": candidate.location_quality,
                "coord_source": candidate.coord_source,
                "coord_confidence": candidate.coord_confidence,
                "capacity_odt_y": candidate.capacity_odt_y,
                "price_status": candidate.price_status,
                "status": status,
                "status_reason": reason
                or link_status.get(candidate.recipient_facility, ""),
                "transport_link_count": link_count.get(
                    candidate.recipient_facility, 0
                ),
                "min_transport_cost_cad_m3": min_cost.get(
                    candidate.recipient_facility, ""
                ),
            }
        )
    rows.sort(key=lambda row: (row["recipient_facility"], row["processing_line"]))
    return rows


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the TSA24/BC Stage B residue and bioenergy pilot."
    )
    parser.add_argument(
        "--repo-root", type=Path, default=Path.cwd(), help="repository root"
    )
    parser.add_argument(
        "--pilot-dir",
        type=Path,
        default=DEFAULT_PILOT_DIR,
        help="Stage A run directory (default: ws4-quota sensitivity)",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=DEFAULT_OUT_DIR,
        help="Stage B output directory",
    )
    parser.add_argument("--period", default="2026", help="model period")
    parser.add_argument(
        "--objective",
        choices=["auto", "profit-max", "cost-min"],
        default="auto",
        help="objective mode; auto falls back to cost-min if prices are missing",
    )
    parser.add_argument(
        "--moisture-limit",
        type=float,
        default=None,
        help="optional wet-basis moisture blend limit in [0, 1)",
    )
    parser.add_argument(
        "--nrn-zip",
        type=Path,
        default=DEFAULT_NRN_ZIP,
        help="NRN BC GeoPackage archive",
    )
    args = parser.parse_args(argv)
    mode = {
        "auto": "auto",
        "profit-max": "profit_max",
        "cost-min": "cost_min",
    }[args.objective]
    try:
        run = run_stage_b(
            args.repo_root,
            pilot_dir=args.pilot_dir,
            out_dir=args.out_dir,
            period=args.period,
            objective_mode=mode,
            moisture_limit_wet_basis=args.moisture_limit,
            nrn_zip=args.nrn_zip,
        )
    except (StageBError, ResidueError) as error:
        print(f"stage_b: FAILED: {error}")
        return 1
    print(
        "stage_b: "
        f"status={run.result.status} "
        f"objective={run.result.objective_value_cad:.2f} CAD "
        f"assigned={run.result.assigned_odt:.3f} ODT "
        f"validation={'PASS' if run.validation.passed else 'FAIL'} "
        f"out={run.out_dir}"
    )
    if not run.validation.passed:
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
