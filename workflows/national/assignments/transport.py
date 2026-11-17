"""FFCM TL-graph transport links for the Stage A TSA24 pilot.

The builder replaces the previous 150 km flat access leg with the parsed FFCM
hub-and-spoke graph:

    TSA24 supply anchor (geocoded Prince George mill proxy)
      -> TL node access leg from routes.csv
      -> TL-TL shortest path from routes.csv
      -> TL-to-facility route from routes.csv

Each path records its TL nodes, modes, distance, line-haul cost, transload
cost, and a confidence label.  Route capacity is absent from the parsed route
table, so every link is uncapped with ``capacity=None`` and
``capacity_source=absent``.  A facility without a defensible TL path is marked
``missing`` and excluded from the LP.
"""

from __future__ import annotations

import csv
import heapq
import math
import statistics
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import units
from .schema import FacilityCapacity, FeedstockGrade, TransportLink

__all__ = [
    "TSA24_ANCHOR_FACILITY",
    "PathLeg",
    "TransportPath",
    "TransportTable",
    "build_transport_table",
    "commodity_for_grade",
    "is_residue_grade",
]

TSA24_ANCHOR_FACILITY = "BC-SAW-488"
TSA24_ANCHOR_BASIS = (
    "tsa24_prince_george_geocoded_mill_proxy;"
    "facility=BC-SAW-488;source=mills_db;"
    "note=no_TSA24_FMU_exists_in_FFCM_graph"
)
DIAGNOSTIC_ACCESS_KM = 150.0


class TransportError(RuntimeError):
    """Raised when the FFCM route/transporter tables cannot build a link."""


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise TransportError(f"required transport input is missing: {path}")
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise TransportError(f"transport input has no data rows: {path}")
    return rows


def _require_columns(
    path: Path, rows: Sequence[dict[str, str]], required: Iterable[str]
) -> None:
    missing = set(required) - set(rows[0])
    if missing:
        raise TransportError(f"{path} is missing required columns: {sorted(missing)}")


def _number(value: object, name: str, *, default: float | None = None) -> float:
    text = str(value).strip()
    if text == "" or text.lower() in {"na", "nan", "none"}:
        if default is not None:
            return default
        raise TransportError(f"{name} is missing")
    try:
        number = float(text)
    except ValueError as error:
        raise TransportError(f"{name} is not numeric: {value!r}") from error
    if not math.isfinite(number):
        raise TransportError(f"{name} is not finite: {value!r}")
    return number


def is_residue_grade(grade_id: str) -> bool:
    return str(grade_id).strip().upper() in {"RESIDUE", "HOGFUEL"}


def commodity_for_grade(grade_id: str) -> str:
    grade = str(grade_id).strip().upper()
    if grade == "RESIDUE":
        return "logging_residue"
    if grade == "HOGFUEL":
        return "hogfuel"
    if grade in {"SAWLOG", "PULP", "OTHER_LOG"}:
        return "log"
    raise TransportError(f"no FFCM transport commodity for grade {grade_id!r}")


def _transporter_name(mode: str, grade_id: str) -> str:
    residue = is_residue_grade(grade_id)
    if mode == "ROAD":
        return "BC_residue_truck" if residue else "BC_logging_truck"
    if mode == "RAIL":
        return "BC_residue_train" if residue else "BC_log_train"
    if mode == "WATER":
        return "residue_barge" if residue else "log_barge"
    raise TransportError(f"no BC transporter for mode {mode!r}")


@dataclass(frozen=True, slots=True)
class TransporterRate:
    """A parsed transporter with archive fuel/operating rates and capacity."""

    transporter: str
    mode: str
    rate_per_km: float
    capacity: float
    capacity_unit: str
    commodity: str

    def capacity_m3(self, density_odt_m3: float) -> float:
        context = units.ConversionContext(basic_density_odt_m3=density_odt_m3)
        if self.capacity_unit == "m3":
            return self.capacity
        return units.to_m3_green(self.capacity, self.capacity_unit, context=context)

    def cost_per_m3_km(self, density_odt_m3: float) -> float:
        capacity_m3 = self.capacity_m3(density_odt_m3)
        if capacity_m3 <= 0.0:
            raise TransportError(
                f"{self.transporter} has non-positive m3 capacity for {self.commodity}"
            )
        return self.rate_per_km / capacity_m3


def _load_transporters(path: Path) -> dict[str, TransporterRate]:
    rows = _read_csv(path)
    _require_columns(
        path,
        rows,
        {
            "transporter",
            "cruising_speed",
            "cruising_speed_unit",
            "transport_mode",
            "operating_cost",
            "operating_cost_unit",
            "fuel_cost",
            "fuel_cost_unit",
        },
    )
    rates: dict[str, TransporterRate] = {}
    for row in rows:
        transporter = str(row["transporter"]).strip()
        if not transporter.startswith("BC_") and transporter not in {
            "log_barge",
            "residue_barge",
        }:
            continue
        mode = str(row["transport_mode"]).strip().upper()
        if mode not in {"ROAD", "RAIL", "WATER"}:
            continue
        speed = _number(row["cruising_speed"], f"{transporter} speed")
        operating = _number(row["operating_cost"], f"{transporter} operating cost")
        fuel = _number(row["fuel_cost"], f"{transporter} fuel cost")
        if speed <= 0.0 or operating < 0.0 or fuel < 0.0:
            raise TransportError(f"{transporter} has invalid cost or speed")
        rates[transporter] = TransporterRate(
            transporter=transporter,
            mode=mode,
            rate_per_km=fuel + operating / speed,
            capacity=0.0,
            capacity_unit="",
            commodity="",
        )
    if not rates:
        raise TransportError("no BC transporters found")
    return rates


def _load_material_capacities(
    path: Path, rates: Mapping[str, TransporterRate]
) -> dict[str, dict[str, tuple[float, str]]]:
    rows = _read_csv(path)
    _require_columns(path, rows, {"transporter", "commodity", "capacity", "unit"})
    capacities: dict[str, dict[str, tuple[float, str]]] = defaultdict(dict)
    for row in rows:
        transporter = str(row["transporter"]).strip()
        if transporter not in rates:
            continue
        commodity = str(row["commodity"]).strip().lower()
        capacity = _number(row["capacity"], f"{transporter} {commodity} capacity")
        unit = str(row["unit"]).strip().lower()
        if capacity <= 0.0:
            raise TransportError(f"{transporter} {commodity} capacity must be positive")
        capacities[transporter][commodity] = (capacity, unit)
    return capacities


def _rate_for(
    rates: Mapping[str, TransporterRate],
    capacities: Mapping[str, Mapping[str, tuple[float, str]]],
    *,
    mode: str,
    grade_id: str,
) -> TransporterRate:
    name = _transporter_name(mode, grade_id)
    base = rates.get(name)
    if base is None:
        raise TransportError(f"transporter {name} is missing from transporters.csv")
    commodity = commodity_for_grade(grade_id)
    capacity = capacities.get(name, {}).get(commodity)
    if capacity is None:
        raise TransportError(
            f"transporter {name} has no {commodity!r} capacity in transporter_materials.csv"
        )
    return TransporterRate(
        transporter=base.transporter,
        mode=base.mode,
        rate_per_km=base.rate_per_km,
        capacity=capacity[0],
        capacity_unit=capacity[1],
        commodity=commodity,
    )


def _load_transload_costs(path: Path) -> dict[tuple[str, str], tuple[float, str]]:
    rows = _read_csv(path)
    _require_columns(
        path, rows, {"transload_node", "attribute", "commodity", "value", "unit"}
    )
    costs: dict[tuple[str, str], tuple[float, str]] = {}
    for row in rows:
        if str(row["attribute"]).strip().lower() != "operatingcost":
            continue
        node = str(row["transload_node"]).strip()
        commodity = str(row["commodity"]).strip().lower()
        value = _number(row["value"], f"{node} {commodity} transload cost")
        unit = str(row["unit"]).strip().lower().replace("cad", "$")
        costs[(node, commodity)] = (value, unit)
    if not costs:
        raise TransportError("no transload operating costs found")
    return costs


@dataclass(frozen=True, slots=True)
class PathLeg:
    origin: str
    destination: str
    mode: str
    distance_km: float
    route_id: str

    def to_text(self) -> str:
        return (
            f"{self.origin}-{self.mode}-{self.distance_km:.3f}km->{self.destination}"
        )


@dataclass(frozen=True, slots=True)
class TransportPath:
    origin: str
    destination: str
    grade_id: str
    mode: str
    distance_km: float
    linehaul_cost_cad_m3: float
    transload_cost_cad_m3: float
    cost_cad_m3: float
    capacity_m3: None
    capacity_source: str
    path_confidence: str
    path_nodes: tuple[str, ...]
    path_legs: tuple[PathLeg, ...]
    transload_nodes: tuple[str, ...]
    anchor_id: str
    anchor_basis: str
    distance_basis: str
    cost_basis: str
    evidence: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "origin": self.origin,
            "destination": self.destination,
            "grade_id": self.grade_id,
            "mode": self.mode,
            "distance_km": self.distance_km,
            "linehaul_cost_cad_m3": self.linehaul_cost_cad_m3,
            "transload_cost_cad_m3": self.transload_cost_cad_m3,
            "cost_cad_m3": self.cost_cad_m3,
            "capacity_m3": "",
            "capacity_source": self.capacity_source,
            "path_confidence": self.path_confidence,
            "path_nodes": ">".join(self.path_nodes),
            "path_legs": ";".join(leg.to_text() for leg in self.path_legs),
            "transload_nodes": ";".join(self.transload_nodes),
            "anchor_id": self.anchor_id,
            "anchor_basis": self.anchor_basis,
            "distance_basis": self.distance_basis,
            "cost_basis": self.cost_basis,
            "evidence": self.evidence,
        }

    def to_link(self, period: str) -> TransportLink:
        return TransportLink(
            link_id=f"{self.origin}->{self.destination}::{self.grade_id}",
            period=period,
            origin=self.origin,
            destination=self.destination,
            grade_id=self.grade_id,
            mode=self.mode,
            distance_km=self.distance_km,
            cost_cad_m3=self.cost_cad_m3,
            capacity_m3=None,
            distance_basis=self.distance_basis,
            cost_basis=self.cost_basis,
            evidence=self.evidence,
            path_confidence=self.path_confidence,
            capacity_source="absent",
        )


@dataclass(frozen=True, slots=True)
class TransportTable:
    links: tuple[TransportPath, ...]
    screen: tuple[dict[str, Any], ...]
    summary: Mapping[str, Any]
    status_by_facility: Mapping[str, str]

    def to_transport_links(self, period: str) -> tuple[TransportLink, ...]:
        return tuple(link.to_link(period) for link in self.links)


def _route_edges(
    rows: Sequence[Mapping[str, str]],
    facility_ids: set[str],
    anchor_facility_id: str,
) -> tuple[
    dict[str, list[tuple[str, float, str, str]]],
    dict[str, list[tuple[str, float, str, str]]],
    list[tuple[str, float, str, str]],
]:
    """Return TL-TL edges, TL->facility edges, and anchor->TL edges."""
    tl_edges: dict[str, list[tuple[str, float, str, str]]] = defaultdict(list)
    inbound: dict[str, list[tuple[str, float, str, str]]] = defaultdict(list)
    anchor_edges: list[tuple[str, float, str, str]] = []
    for row in rows:
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
            raise TransportError("route origin/destination lists are not parallel")
        distance = _number(row.get("total_length", ""), "route total_length", default=0.0)
        if distance <= 0.0:
            continue
        mode = str(row.get("transport_mode", "")).strip().upper()
        if mode not in {"ROAD", "RAIL", "WATER"}:
            continue
        route_id = str(row.get("route", "")).strip()
        for origin, destination in zip(origins, destinations):
            if origin.startswith("TL") and destination.startswith("TL"):
                tl_edges[origin].append((destination, distance, mode, route_id))
            if origin.startswith("TL") and destination in facility_ids:
                inbound[destination].append((origin, distance, mode, route_id))
            if origin == anchor_facility_id and destination.startswith("TL"):
                anchor_edges.append((destination, distance, mode, route_id))
    if not tl_edges:
        raise TransportError("routes.csv has no TL-TL edges")
    if not anchor_edges:
        raise TransportError(
            f"anchor facility {anchor_facility_id} has no route to a TL node"
        )
    return tl_edges, inbound, anchor_edges


def _shortest_paths(
    source: str,
    edges: Mapping[str, Sequence[tuple[str, float, str, str]]],
) -> tuple[dict[str, float], dict[str, tuple[str, str, str, float]]]:
    distance = {source: 0.0}
    previous: dict[str, tuple[str, str, str, float]] = {}
    queue: list[tuple[float, str]] = [(0.0, source)]
    while queue:
        current_distance, node = heapq.heappop(queue)
        if current_distance > distance.get(node, math.inf) + 1e-9:
            continue
        for destination, leg_km, mode, route_id in edges.get(node, ()):
            candidate = current_distance + leg_km
            if candidate < distance.get(destination, math.inf) - 1e-9:
                distance[destination] = candidate
                previous[destination] = (node, mode, route_id, leg_km)
                heapq.heappush(queue, (candidate, destination))
    return distance, previous


def _path_to(
    source: str,
    destination: str,
    previous: Mapping[str, tuple[str, str, str, float]],
) -> tuple[list[str], list[PathLeg]]:
    nodes = [destination]
    legs: list[PathLeg] = []
    current = destination
    while current != source:
        record = previous.get(current)
        if record is None:
            raise TransportError(f"no predecessor for {current} on path from {source}")
        predecessor, mode, route_id, leg_km = record
        legs.append(
            PathLeg(
                origin=predecessor,
                destination=current,
                mode=mode,
                distance_km=leg_km,
                route_id=route_id,
            )
        )
        nodes.append(predecessor)
        current = predecessor
    nodes.reverse()
    legs.reverse()
    return nodes, legs


def _mode_for(legs: Sequence[PathLeg]) -> str:
    modes = {leg.mode for leg in legs}
    if len(modes) == 1:
        return modes.pop()
    return "COMBINATION"


def _transload_cost(
    node: str,
    grade: FeedstockGrade,
    costs: Mapping[tuple[str, str], tuple[float, str]],
) -> float:
    commodity = commodity_for_grade(grade.grade_id)
    record = costs.get((node, commodity))
    if record is None:
        raise TransportError(
            f"transload node {node} has no {commodity!r} operating cost"
        )
    value, unit = record
    if unit in {"$/m3", "$/m3/y"}:
        return value
    if unit in {"$/t", "$/t/y", "$/tonne"}:
        context = units.ConversionContext(
            basic_density_odt_m3=grade.density_odt_m3,
            density_source="feedstock_grade_density_odt_m3",
            allow_archive_tonne=True,
            grade_id=grade.grade_id,
        )
        tonnes_per_m3 = units.convert(1.0, "m3_green", "t", context=context)
        return value * tonnes_per_m3
    raise TransportError(
        f"transload unit {unit!r} at {node} has no declared $/m3 conversion"
    )


def _stats(values: Sequence[float]) -> dict[str, float | int]:
    if not values:
        return {"count": 0}
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return {
            "count": 1,
            "min": ordered[0],
            "p25": ordered[0],
            "median": ordered[0],
            "p75": ordered[0],
            "max": ordered[0],
            "mean": ordered[0],
        }
    quartiles = statistics.quantiles(ordered, n=4)
    return {
        "count": len(ordered),
        "min": ordered[0],
        "p25": quartiles[0],
        "median": statistics.median(ordered),
        "p75": quartiles[2],
        "max": ordered[-1],
        "mean": statistics.fmean(ordered),
    }


def _old_diagnostic_distance(
    rows: Sequence[Mapping[str, str]], facility_id: str
) -> float | None:
    minimum: float | None = None
    for row in rows:
        if str(row.get("transport_mode", "")).strip().upper() != "ROAD":
            continue
        distance = _number(row.get("total_length", ""), "route total_length", default=0.0)
        if distance <= 0.0:
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
        for origin, destination in zip(origins, destinations):
            if origin == facility_id and destination.startswith("TL"):
                minimum = distance if minimum is None else min(minimum, distance)
            if destination == facility_id and origin.startswith("TL"):
                minimum = distance if minimum is None else min(minimum, distance)
    if minimum is None:
        return None
    return DIAGNOSTIC_ACCESS_KM + minimum


def _diagnostic_comparison(
    rows: Sequence[Mapping[str, str]],
    facilities: Sequence[FacilityCapacity],
    paths_by_facility: Mapping[str, Sequence[TransportPath]],
    rates: Mapping[str, TransporterRate],
    capacities: Mapping[str, Mapping[str, tuple[float, str]]],
) -> dict[str, Any]:
    new_distance: list[float] = []
    old_distance: list[float] = []
    deltas: list[float] = []
    ratios: list[float] = []
    outlier_rows: list[dict[str, Any]] = []
    for facility in facilities:
        paths = paths_by_facility.get(facility.facility_id)
        if not paths:
            continue
        old = _old_diagnostic_distance(rows, facility.facility_id)
        if old is None:
            continue
        new = paths[0].distance_km
        new_distance.append(new)
        old_distance.append(old)
        deltas.append(new - old)
        ratios.append(new / old if old > 0 else math.inf)
        if new > old + 100.0 or (old > 0 and new / old > 1.5):
            outlier_rows.append(
                {
                    "facility_id": facility.facility_id,
                    "facility_class": facility.facility_class,
                    "old_diagnostic_km": old,
                    "new_graph_km": new,
                    "delta_km": new - old,
                    "ratio": new / old if old > 0 else "",
                    "path_confidence": paths[0].path_confidence,
                }
            )
    outlier_rows.sort(key=lambda row: float(row["delta_km"]), reverse=True)

    cost_rows: dict[str, dict[str, Any]] = {}
    for grade in ("SAWLOG", "RESIDUE"):
        new_costs: list[float] = []
        old_costs: list[float] = []
        for facility in facilities:
            paths = paths_by_facility.get(facility.facility_id)
            if not paths:
                continue
            old = _old_diagnostic_distance(rows, facility.facility_id)
            if old is None:
                continue
            path = next((item for item in paths if item.grade_id == grade), None)
            if path is None:
                continue
            rate = _rate_for(
                rates, capacities, mode="ROAD", grade_id=grade
            )
            old_cost = old * rate.cost_per_m3_km(0.45 if grade == "RESIDUE" else 0.40)
            new_costs.append(path.cost_cad_m3)
            old_costs.append(old_cost)
        cost_rows[grade] = {
            "new": _stats(new_costs),
            "old": _stats(old_costs),
            "delta": _stats(
                [new - old for new, old in zip(new_costs, old_costs)]
            ),
        }
    return {
        "new_distance_km": _stats(new_distance),
        "old_diagnostic_distance_km": _stats(old_distance),
        "delta_km": _stats(deltas),
        "ratio": _stats(ratios),
        "outliers": outlier_rows[:20],
        "outlier_count": len(outlier_rows),
        "cost": cost_rows,
    }


def build_transport_table(
    repo_root: Path,
    facilities: Sequence[FacilityCapacity],
    grades: Sequence[FeedstockGrade],
    *,
    period: str,
    anchor_facility_id: str = TSA24_ANCHOR_FACILITY,
    supply_node_id: str = "TSA24_AU23000",
) -> TransportTable:
    """Build one grade-specific transport path per linked TSA24 facility."""
    if not facilities:
        raise TransportError("no facilities supplied to the transport builder")
    repo_root = Path(repo_root)
    routes_path = repo_root / "metadata/ffcm-archive/parsed/routes.csv"
    transporters_path = repo_root / "metadata/ffcm-archive/parsed/transporters.csv"
    materials_path = (
        repo_root / "metadata/ffcm-archive/parsed/transporter_materials.csv"
    )
    transload_path = repo_root / "metadata/ffcm-archive/parsed/transload_costs.csv"

    route_rows = _read_csv(routes_path)
    _require_columns(
        routes_path,
        route_rows,
        {"origin_list", "destination_list", "total_length", "transport_mode"},
    )
    rates = _load_transporters(transporters_path)
    capacities = _load_material_capacities(materials_path, rates)
    transload_costs = _load_transload_costs(transload_path)
    grade_by_id = {grade.grade_id: grade for grade in grades}
    facility_by_id = {facility.facility_id: facility for facility in facilities}
    facility_ids = set(facility_by_id)
    if anchor_facility_id not in facility_ids:
        raise TransportError(
            f"TSA24 anchor proxy {anchor_facility_id} is not a Stage A facility"
        )

    tl_edges, inbound, anchor_edges = _route_edges(
        route_rows, facility_ids, anchor_facility_id
    )
    graph = {node: list(edges) for node, edges in tl_edges.items()}
    graph.setdefault(anchor_facility_id, []).extend(anchor_edges)
    distance_from_anchor, previous = _shortest_paths(anchor_facility_id, graph)

    links: list[TransportPath] = []
    screen: list[dict[str, Any]] = []
    status_by_facility: dict[str, str] = {}
    paths_by_facility: dict[str, list[TransportPath]] = defaultdict(list)

    for facility in facilities:
        facility_id = facility.facility_id
        if facility_id == anchor_facility_id:
            status = "missing_anchor_proxy_self"
            status_by_facility[facility_id] = status
            screen.append(
                {
                    "facility_id": facility_id,
                    "facility_class": facility.facility_class,
                    "status": status,
                    "path_confidence": "missing",
                    "nearest_tl": "",
                    "access_km": "",
                    "facility_leg_km": "",
                    "distance_km": "",
                    "mode": "",
                    "cost_cad_m3_min": "",
                    "cost_cad_m3_max": "",
                    "reason": "anchor_proxy_has_no_defensible_self_haul_distance",
                }
            )
            continue
        candidates = [
            (distance_from_anchor[tl] + leg_km, tl, leg_km, mode, route_id)
            for tl, leg_km, mode, route_id in inbound.get(facility_id, ())
            if tl in distance_from_anchor
        ]
        if not inbound.get(facility_id):
            status = "missing_no_tl_route"
            status_by_facility[facility_id] = status
            screen.append(
                {
                    "facility_id": facility_id,
                    "facility_class": facility.facility_class,
                    "status": status,
                    "path_confidence": "missing",
                    "nearest_tl": "",
                    "access_km": "",
                    "facility_leg_km": "",
                    "distance_km": "",
                    "mode": "",
                    "cost_cad_m3_min": "",
                    "cost_cad_m3_max": "",
                    "reason": "no_TL_route_in_routes_csv",
                }
            )
            continue
        if not candidates:
            status = "missing_no_graph_path"
            status_by_facility[facility_id] = status
            screen.append(
                {
                    "facility_id": facility_id,
                    "facility_class": facility.facility_class,
                    "status": status,
                    "path_confidence": "missing",
                    "nearest_tl": "",
                    "access_km": "",
                    "facility_leg_km": "",
                    "distance_km": "",
                    "mode": "",
                    "cost_cad_m3_min": "",
                    "cost_cad_m3_max": "",
                    "reason": "TL_subgraph_has_no_path_from_anchor",
                }
            )
            continue
        total_distance, nearest_tl, facility_leg_km, final_mode, final_route = min(
            candidates, key=lambda item: (item[0], item[1], item[4])
        )
        tl_nodes, tl_legs = _path_to(
            anchor_facility_id, nearest_tl, previous
        )
        final_leg = PathLeg(
            origin=nearest_tl,
            destination=facility_id,
            mode=final_mode,
            distance_km=facility_leg_km,
            route_id=final_route,
        )
        path_nodes = tuple(tl_nodes + [facility_id])
        path_legs = tuple(tl_legs + [final_leg])
        mode = _mode_for(path_legs)
        access_km = total_distance - facility_leg_km
        transload_nodes = tuple(node for node in path_nodes if node.startswith("TL"))
        confidence = (
            "direct_route" if len(transload_nodes) == 1 else "graph_shortest_path"
        )
        facility_costs: list[float] = []
        for offer in facility.offers:
            grade = grade_by_id.get(offer.grade_id)
            if grade is None:
                continue
            linehaul = 0.0
            for leg in path_legs:
                rate = _rate_for(
                    rates, capacities, mode=leg.mode, grade_id=grade.grade_id
                )
                linehaul += leg.distance_km * rate.cost_per_m3_km(
                    grade.density_odt_m3
                )
            transload = sum(
                _transload_cost(node, grade, transload_costs)
                for node in transload_nodes
            )
            total_cost = linehaul + transload
            path = TransportPath(
                origin=supply_node_id,
                destination=facility_id,
                grade_id=grade.grade_id,
                mode=mode,
                distance_km=total_distance,
                linehaul_cost_cad_m3=linehaul,
                transload_cost_cad_m3=transload,
                cost_cad_m3=total_cost,
                capacity_m3=None,
                capacity_source="absent",
                path_confidence=confidence,
                path_nodes=path_nodes,
                path_legs=path_legs,
                transload_nodes=transload_nodes,
                anchor_id=anchor_facility_id,
                anchor_basis=TSA24_ANCHOR_BASIS,
                distance_basis=(
                    "ffcm_route_graph_shortest_path_from_TSA24_anchor_"
                    f"{anchor_facility_id}"
                ),
                cost_basis=(
                    "ffcm_transporters+transporter_materials+transload_costs"
                ),
                evidence=(
                    "parsed_ffcm_routes;graph_shortest_path;"
                    f"anchor_proxy={anchor_facility_id};"
                    f"modes={';'.join(sorted({leg.mode for leg in path_legs}))};"
                    "route_capacity_absent"
                ),
            )
            links.append(path)
            paths_by_facility[facility_id].append(path)
            facility_costs.append(total_cost)
        if not facility_costs:
            status = "missing_no_eligible_grade"
            status_by_facility[facility_id] = status
            screen.append(
                {
                    "facility_id": facility_id,
                    "facility_class": facility.facility_class,
                    "status": status,
                    "path_confidence": "missing",
                    "nearest_tl": nearest_tl,
                    "access_km": access_km,
                    "facility_leg_km": facility_leg_km,
                    "distance_km": total_distance,
                    "mode": mode,
                    "cost_cad_m3_min": "",
                    "cost_cad_m3_max": "",
                    "reason": "no_transportable_grade",
                }
            )
            continue
        status = (
            "linked_direct_route"
            if confidence == "direct_route"
            else "linked_graph_shortest_path"
        )
        status_by_facility[facility_id] = status
        screen.append(
            {
                "facility_id": facility_id,
                "facility_class": facility.facility_class,
                "status": status,
                "path_confidence": confidence,
                "nearest_tl": nearest_tl,
                "access_km": access_km,
                "facility_leg_km": facility_leg_km,
                "distance_km": total_distance,
                "mode": mode,
                "cost_cad_m3_min": min(facility_costs),
                "cost_cad_m3_max": max(facility_costs),
                "reason": "",
            }
        )

    if not links:
        raise TransportError("no facility could be connected to the TSA24 anchor")
    links.sort(key=lambda item: (item.destination, item.grade_id))
    screen.sort(key=lambda row: row["facility_id"])
    confidence_counts = defaultdict(int)
    mode_counts = defaultdict(int)
    for paths in paths_by_facility.values():
        confidence_counts[paths[0].path_confidence] += 1
        mode_counts[paths[0].mode] += 1
    distance_by_facility = {
        facility_id: paths[0].distance_km
        for facility_id, paths in paths_by_facility.items()
    }
    comparison = _diagnostic_comparison(
        route_rows, facilities, paths_by_facility, rates, capacities
    )
    summary = {
        "accepted_link_count": len(links),
        "accepted_facility_count": len(paths_by_facility),
        "screened_facility_count": len(screen),
        "confidence_counts": dict(confidence_counts),
        "mode_counts": dict(mode_counts),
        "distance_km": _stats(list(distance_by_facility.values())),
        "cost_cad_m3": {
            grade: _stats(
                [
                    path.cost_cad_m3
                    for paths in paths_by_facility.values()
                    for path in paths
                    if path.grade_id == grade
                ]
            )
            for grade in sorted({path.grade_id for path in links})
        },
        "capacity": {
            "capacity_none_count": len(links),
            "capacity_source": "absent",
            "route_capacity_table": "absent",
        },
        "anchor": {
            "anchor_id": anchor_facility_id,
            "supply_node_id": supply_node_id,
            "anchor_basis": TSA24_ANCHOR_BASIS,
            "access_tl_nodes": sorted(
                {destination for destination, _distance, _mode, _route in anchor_edges}
            ),
        },
        "diagnostic_comparison": comparison,
        "rate_table": {
            name: {
                "mode": rate.mode,
                "rate_per_km": rate.rate_per_km,
            }
            for name, rate in sorted(rates.items())
        },
        "transload_cost_table": {
            f"{node}:{commodity}": {"value": value, "unit": unit}
            for (node, commodity), (value, unit) in sorted(transload_costs.items())
        },
    }
    return TransportTable(
        links=tuple(links),
        screen=tuple(screen),
        summary=summary,
        status_by_facility=dict(status_by_facility),
    )
