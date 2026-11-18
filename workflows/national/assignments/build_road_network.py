"""Build the real NRN BC road graph and Stage A road-transport links.

The module reads the archived NRN BC GeoPackage, keeps road classes suitable
for log/residue hauling, projects to BC Albers (EPSG:3005), snaps segment
endpoints to a 1 m grid to create a routable topology, snaps Stage A facilities
to the nearest graph node within 5 km, and writes grade-specific road links.

Run before the Stage A re-run::

    .venv/bin/python -m workflows.national.assignments.build_road_network

Run after the Stage A re-run to refresh the report only::

    .venv/bin/python -m workflows.national.assignments.build_road_network \
        --report-only
"""

from __future__ import annotations

import argparse
import json
import math
import resource
import shutil
import tempfile
import time
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pyogrio
from pyproj import Transformer
from scipy.sparse import csgraph, csr_matrix
from scipy.spatial import cKDTree

from .transport import (
    TSA24_ANCHOR_FACILITY,
    load_road_rate_inputs,
    road_rate_for_grade,
)

__all__ = [
    "DEFAULT_NRN_ZIP",
    "DEFAULT_PILOT_DIR",
    "RoadGraph",
    "RoadNetworkError",
    "assemble_graph",
    "build_road_graph",
    "snap_facilities",
]

DEFAULT_NRN_ZIP = Path("data/raw/nrn/nrn_bc_gpkg.zip")
DEFAULT_PILOT_DIR = Path("data/derived/national/assignments/tsa24_pilot")
NRN_ROAD_LAYER = "NRN_BC_14_0_ROADSEG"
NRN_FERRY_LAYER = "NRN_BC_14_0_FERRYSEG"
METRIC_CRS = "EPSG:3005"
NODE_SNAP_TOLERANCE_M = 1.0
FACILITY_SNAP_TOLERANCE_M = 5_000.0
FERRY_TERMINAL_SNAP_TOLERANCE_M = 5_000.0
FERRY_SPEED_KMH = 33.0
#: Placeholder only: a documented, explicitly non-tariff ferry charge used to
#: keep island/coastal links from looking free.  It is not a BC Ferries rate.
FERRY_FARE_CAD_M3_KM = 0.03
FERRY_FARE_BASIS = "placeholder_not_a_bc_ferries_tariff"
FERRY_ROUTE_DEDUP_M = 3_000.0
BUILD_TIME_LIMIT_S = 780.0

HAULABLE_ROAD_CLASSES = frozenset(
    {
        "Expressway / Highway",
        "Freeway",
        "Arterial",
        "Collector",
        "Resource / Recreation",
        "Local / Street",
        "Local / Strata",
        "Local / Unknown",
    }
)
EXCLUDED_ROAD_CLASSES = frozenset({"Alleyway / Lane", "Ramp", "Service Lane"})
PEDESTRIAN_ROAD_CLASSES = frozenset(
    {"Pedestrian", "Trail", "Footpath", "Walkway", "Path"}
)


class RoadNetworkError(RuntimeError):
    """Raised when the NRN layer cannot produce a routable road graph."""


@dataclass(frozen=True, slots=True)
class RoadGraph:
    node_xy_m: np.ndarray
    edge_u: np.ndarray
    edge_v: np.ndarray
    edge_length_m: np.ndarray
    matrix: csr_matrix
    component_labels: np.ndarray
    component_sizes: np.ndarray
    total_features: int
    kept_features: int
    class_counts: Mapping[str, int]
    excluded_counts: Mapping[str, int]
    source_crs: str
    bounds_wgs84: tuple[float, float, float, float]
    traffic_direction_counts: Mapping[str, int]
    timings_s: Mapping[str, float]
    peak_rss_mb: float
    gpkg_member: str
    ferry_edge_u: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=int))
    ferry_edge_v: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=int))
    ferry_edge_length_m: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=float))
    ferry_routes: tuple[Mapping[str, Any], ...] = ()
    ferry_summary: Mapping[str, Any] = field(default_factory=dict)
    #: Winning edge mode per ``edge_length_m`` entry: ``road`` or ``ferry``.
    edge_mode: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=object))
    #: Winning edge provenance per ``edge_length_m`` entry.
    edge_provenance: np.ndarray = field(
        default_factory=lambda: np.zeros(0, dtype=object)
    )


@dataclass(frozen=True, slots=True)
class FerrySegment:
    """One NRN FERRYSEG feature in the metric CRS."""

    ferry_id: str
    name: str
    length_m: float
    first_xy: tuple[float, float]
    last_xy: tuple[float, float]
    road_class: str


#: Published BC Ferries routes used as a fallback when the NRN ferry layer is
#: absent or does not cover a route.  Distances/times are rounded published
#: schedule approximations, not survey measurements.  Fares are the documented
#: placeholder above, never a real tariff.
MAJOR_FERRY_ROUTES: tuple[Mapping[str, Any], ...] = (
    {
        "route_id": "TSA-SWB",
        "name": "Tsawwassen-Swartz Bay",
        "terminal_a": (49.0069, -123.1283),
        "terminal_b": (48.6887, -123.4143),
        "distance_km": 40.0,
        "crossing_min": 95.0,
    },
    {
        "route_id": "HSB-DNB",
        "name": "Horseshoe Bay-Departure Bay",
        "terminal_a": (49.3747, -123.2717),
        "terminal_b": (49.1946, -123.9567),
        "distance_km": 55.0,
        "crossing_min": 100.0,
    },
    {
        "route_id": "HSB-LGD",
        "name": "Horseshoe Bay-Langdale",
        "terminal_a": (49.3747, -123.2717),
        "terminal_b": (49.4344, -123.4739),
        "distance_km": 16.0,
        "crossing_min": 40.0,
    },
    {
        "route_id": "CMX-PWR",
        "name": "Comox-Powell River",
        "terminal_a": (49.6736, -124.9250),
        "terminal_b": (49.8356, -124.5247),
        "distance_km": 27.0,
        "crossing_min": 80.0,
    },
    {
        "route_id": "PTH-PRU",
        "name": "Port Hardy-Prince Rupert",
        "terminal_a": (50.7222, -127.4969),
        "terminal_b": (54.3150, -130.3240),
        "distance_km": 500.0,
        "crossing_min": 990.0,
    },
    {
        "route_id": "SKG-PRU",
        "name": "Skidegate-Prince Rupert",
        "terminal_a": (53.2469, -132.0099),
        "terminal_b": (54.3150, -130.3240),
        "distance_km": 170.0,
        "crossing_min": 390.0,
    },
)


def _extract_nrn_gpkg(nrn_zip: Path) -> tuple[str, str]:
    archive = Path(nrn_zip)
    if not archive.exists():
        raise RoadNetworkError(f"NRN archive is missing: {archive}")
    with zipfile.ZipFile(archive) as handle:
        members = [
            name
            for name in handle.namelist()
            if name.lower().endswith(".gpkg") and "/" not in name
        ]
        english = [
            name
            for name in members
            if Path(name).name.startswith("NRN_") and "_en" in name.lower()
        ]
        if not english:
            raise RoadNetworkError(
                f"no English NRN GeoPackage member in {archive}: {members}"
            )
        member = min(english)
        target = Path(tempfile.mkdtemp(prefix="nrn_bc_"))
        handle.extract(member, target)
        return str(target / member), member


def _endpoint_arrays(
    geometries: Sequence[Any],
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    first: list[tuple[float, float]] = []
    last: list[tuple[float, float]] = []
    lengths: list[float] = []
    for geometry in geometries:
        if geometry is None or geometry.is_empty:
            continue
        if geometry.geom_type == "LineString":
            parts = (geometry,)
        elif geometry.geom_type == "MultiLineString":
            parts = tuple(geometry.geoms)
        else:
            raise RoadNetworkError(
                f"unsupported road geometry type {geometry.geom_type!r}; "
                "expected LineString or MultiLineString"
            )
        for part in parts:
            if part.is_empty or part.length <= 0.0:
                continue
            coordinates = part.coords
            first.append((float(coordinates[0][0]), float(coordinates[0][1])))
            last.append((float(coordinates[-1][0]), float(coordinates[-1][1])))
            lengths.append(float(part.length))
    if not lengths:
        raise RoadNetworkError("no non-zero road segments survived extraction")
    return (
        np.asarray(first, dtype=float),
        np.asarray(last, dtype=float),
        np.asarray(lengths, dtype=float),
    )


def assemble_graph(
    first_xy: np.ndarray,
    last_xy: np.ndarray,
    lengths_m: np.ndarray,
    *,
    snap_tolerance_m: float = NODE_SNAP_TOLERANCE_M,
) -> tuple[
    np.ndarray,
    np.ndarray,
    np.ndarray,
    np.ndarray,
    csr_matrix,
    np.ndarray,
    np.ndarray,
]:
    """Assemble a deduplicated undirected graph from segment endpoints."""
    if snap_tolerance_m <= 0.0:
        raise RoadNetworkError("snap_tolerance_m must be positive")
    first = np.asarray(first_xy, dtype=float)
    last = np.asarray(last_xy, dtype=float)
    lengths = np.asarray(lengths_m, dtype=float)
    if not (len(first) == len(last) == len(lengths)):
        raise RoadNetworkError("endpoint and length arrays must have equal length")
    if len(lengths) == 0:
        raise RoadNetworkError("cannot assemble an empty road graph")

    first_key = np.rint(first / snap_tolerance_m).astype(np.int64)
    last_key = np.rint(last / snap_tolerance_m).astype(np.int64)
    node_keys = np.unique(
        np.concatenate([first_key, last_key], axis=0), axis=0
    )
    index = pd.MultiIndex.from_arrays([node_keys[:, 0], node_keys[:, 1]])
    u = index.get_indexer(pd.MultiIndex.from_arrays([first_key[:, 0], first_key[:, 1]]))
    v = index.get_indexer(pd.MultiIndex.from_arrays([last_key[:, 0], last_key[:, 1]]))
    if (u < 0).any() or (v < 0).any():
        raise RoadNetworkError("failed to index quantized road endpoints")

    valid = (u != v) & (lengths > 0.0)
    u = u[valid]
    v = v[valid]
    lengths = lengths[valid]
    if len(lengths) == 0:
        raise RoadNetworkError("every road segment collapsed to a self-loop")

    edges = pd.DataFrame(
        {
            "u": np.minimum(u, v),
            "v": np.maximum(u, v),
            "length_m": lengths,
        }
    ).groupby(["u", "v"], as_index=False)["length_m"].min()
    matrix = csr_matrix(
        (edges["length_m"].to_numpy(), (edges["u"].to_numpy(), edges["v"].to_numpy())),
        shape=(len(node_keys), len(node_keys)),
    )
    _components, labels = csgraph.connected_components(matrix, directed=False)
    sizes = np.bincount(labels)
    node_xy = node_keys.astype(float) * snap_tolerance_m
    return (
        node_xy,
        edges["u"].to_numpy(),
        edges["v"].to_numpy(),
        edges["length_m"].to_numpy(),
        matrix,
        labels,
        sizes,
    )


def _select_winning_edges(
    node_count: int,
    edge_u: np.ndarray,
    edge_v: np.ndarray,
    edge_length_m: np.ndarray,
    edge_mode: np.ndarray,
    edge_provenance: np.ndarray,
) -> tuple[
    np.ndarray,
    np.ndarray,
    np.ndarray,
    np.ndarray,
    np.ndarray,
    csr_matrix,
    np.ndarray,
    np.ndarray,
]:
    """Keep the shortest edge per node pair with its own mode and provenance.

    A pair may carry a road edge and a ferry edge.  The shortest edge wins and
    its metadata travels with it; never inherit the ferry distance of the
    losing edge.  Ties prefer the road edge so a ferry crossing is not charged
    when a road connection of equal length exists.
    """
    u = np.asarray(edge_u, dtype=np.int64)
    v = np.asarray(edge_v, dtype=np.int64)
    lengths = np.asarray(edge_length_m, dtype=float)
    modes = np.asarray(edge_mode, dtype=object)
    provenance = np.asarray(edge_provenance, dtype=object)
    if not (len(u) == len(v) == len(lengths) == len(modes) == len(provenance)):
        raise RoadNetworkError("edge arrays must have equal length")
    if len(lengths) == 0:
        raise RoadNetworkError("cannot assemble an empty edge set")
    if not np.isfinite(lengths).all() or (lengths <= 0.0).any():
        raise RoadNetworkError("edge lengths must be finite and positive")
    if (u < 0).any() or (v < 0).any():
        raise RoadNetworkError("edge node indices must be non-negative")
    if (u >= node_count).any() or (v >= node_count).any():
        raise RoadNetworkError("edge node index exceeds graph node count")

    frame = pd.DataFrame(
        {
            "u": np.minimum(u, v),
            "v": np.maximum(u, v),
            "length_m": lengths,
            "mode": modes,
            "provenance": provenance,
        }
    )
    frame = frame.loc[frame["u"] != frame["v"]]
    if frame.empty:
        raise RoadNetworkError("every edge collapsed to a self-loop")
    mode_priority = frame["mode"].map({"road": 0, "ferry": 1}).fillna(2)
    frame = (
        frame.assign(_mode_priority=mode_priority)
        .sort_values(["u", "v", "length_m", "_mode_priority"], kind="stable")
        .drop_duplicates(["u", "v"], keep="first")
        .sort_values(["u", "v"])
        .reset_index(drop=True)
    )
    matrix = csr_matrix(
        (frame["length_m"].to_numpy(), (frame["u"].to_numpy(), frame["v"].to_numpy())),
        shape=(node_count, node_count),
    )
    _components, labels = csgraph.connected_components(matrix, directed=False)
    sizes = np.bincount(labels)
    return (
        frame["u"].to_numpy(dtype=np.int64),
        frame["v"].to_numpy(dtype=np.int64),
        frame["length_m"].to_numpy(dtype=float),
        frame["mode"].to_numpy(dtype=object),
        frame["provenance"].to_numpy(dtype=object),
        matrix,
        labels,
        sizes,
    )


def _assemble_edge_matrix(
    node_xy: np.ndarray,
    edge_u: np.ndarray,
    edge_v: np.ndarray,
    edge_length_m: np.ndarray,
) -> tuple[csr_matrix, np.ndarray, np.ndarray]:
    """Build a deduplicated undirected matrix and component labels from edges."""
    lengths = np.asarray(edge_length_m, dtype=float)
    modes = np.full(len(lengths), "road", dtype=object)
    provenance = np.full(len(lengths), "nrn_roadseg", dtype=object)
    *_, matrix, labels, sizes = _select_winning_edges(
        len(node_xy),
        edge_u,
        edge_v,
        lengths,
        modes,
        provenance,
    )
    return matrix, labels, sizes


def augment_graph_with_edges(
    graph: RoadGraph,
    edge_u: Sequence[int],
    edge_v: Sequence[int],
    edge_length_m: Sequence[float],
    *,
    edge_mode: Sequence[str] | None = None,
    edge_provenance: Sequence[str] | None = None,
) -> RoadGraph:
    """Return a new graph with extra undirected edges between existing nodes.

    ``edge_mode``/``edge_provenance`` describe each new edge.  The winning
    edge per node pair keeps its own metadata, so a ferry length is never
    attached to a shorter road edge.
    """
    new_u = np.asarray(edge_u, dtype=np.int64)
    new_v = np.asarray(edge_v, dtype=np.int64)
    new_lengths = np.asarray(edge_length_m, dtype=float)
    if not (len(new_u) == len(new_v) == len(new_lengths)):
        raise RoadNetworkError("new edge arrays must have equal length")
    if len(new_lengths) == 0:
        return graph
    new_modes = (
        np.full(len(new_lengths), "road", dtype=object)
        if edge_mode is None
        else np.asarray(edge_mode, dtype=object)
    )
    new_provenance = (
        np.full(len(new_lengths), "additional_road_edge", dtype=object)
        if edge_provenance is None
        else np.asarray(edge_provenance, dtype=object)
    )
    if len(new_modes) != len(new_lengths) or len(new_provenance) != len(new_lengths):
        raise RoadNetworkError("new edge metadata must match the edge count")
    existing_modes = graph.edge_mode
    if len(existing_modes) != len(graph.edge_length_m):
        existing_modes = np.full(len(graph.edge_length_m), "road", dtype=object)
    existing_provenance = graph.edge_provenance
    if len(existing_provenance) != len(graph.edge_length_m):
        existing_provenance = np.full(
            len(graph.edge_length_m), "nrn_roadseg", dtype=object
        )

    (
        winning_u,
        winning_v,
        winning_lengths,
        winning_modes,
        winning_provenance,
        matrix,
        labels,
        sizes,
    ) = _select_winning_edges(
        len(graph.node_xy_m),
        np.concatenate([graph.edge_u, new_u]),
        np.concatenate([graph.edge_v, new_v]),
        np.concatenate([graph.edge_length_m, new_lengths]),
        np.concatenate([existing_modes, new_modes]),
        np.concatenate([existing_provenance, new_provenance]),
    )
    return replace(
        graph,
        edge_u=winning_u,
        edge_v=winning_v,
        edge_length_m=winning_lengths,
        edge_mode=winning_modes,
        edge_provenance=winning_provenance,
        matrix=matrix,
        component_labels=labels,
        component_sizes=sizes,
    )


def _ferry_segment_records(gpkg_path: str) -> list[FerrySegment]:
    info = pyogrio.read_info(gpkg_path, layer=NRN_FERRY_LAYER)
    if info["features"] == 0:
        raise RoadNetworkError(f"NRN ferry layer {NRN_FERRY_LAYER} is empty")
    frame = pyogrio.read_dataframe(gpkg_path, layer=NRN_FERRY_LAYER)
    if frame.empty:
        raise RoadNetworkError(f"NRN ferry layer {NRN_FERRY_LAYER} is empty")
    frame = frame.to_crs(METRIC_CRS)
    records: list[FerrySegment] = []
    for row in frame.itertuples(index=False):
        geometry = row.geometry
        if geometry is None or geometry.is_empty:
            continue
        if geometry.geom_type == "LineString":
            parts = (geometry,)
        elif geometry.geom_type == "MultiLineString":
            parts = tuple(geometry.geoms)
        else:
            raise RoadNetworkError(
                f"unsupported ferry geometry type {geometry.geom_type!r}"
            )
        for part_index, part in enumerate(parts):
            if part.is_empty or part.length <= 0.0:
                continue
            coordinates = part.coords
            records.append(
                FerrySegment(
                    ferry_id=f"{row.FERRYSEGID}:{part_index}",
                    name=str(row.RTENAME1EN or row.RTENAME2EN or "NRN ferry"),
                    length_m=float(part.length),
                    first_xy=(float(coordinates[0][0]), float(coordinates[0][1])),
                    last_xy=(float(coordinates[-1][0]), float(coordinates[-1][1])),
                    road_class=str(row.ROADCLASS or "Unknown"),
                )
            )
    if not records:
        raise RoadNetworkError("NRN ferry layer produced no usable segments")
    return records


def load_ferry_segments(nrn_zip: Path) -> list[FerrySegment]:
    """Read and project the NRN BC ferry segments."""
    gpkg_path, _member = _extract_nrn_gpkg(nrn_zip)
    try:
        return _ferry_segment_records(gpkg_path)
    finally:
        shutil.rmtree(Path(gpkg_path).parent, ignore_errors=True)


def ferry_pair_lookup(graph: Any) -> dict[tuple[int, int], float]:
    """Map each winning undirected ferry edge to its length in metres.

    ``graph.ferry_edge_*`` is built from the deduplicated winning edges, so a
    pair appears at most once.  A duplicate means two ferry lengths compete for
    one pair, which is an ambiguous graph state and fails closed.
    """
    lookup: dict[tuple[int, int], float] = {}
    for u, v, length in zip(
        getattr(graph, "ferry_edge_u", ()),
        getattr(graph, "ferry_edge_v", ()),
        getattr(graph, "ferry_edge_length_m", ()),
    ):
        key = (min(int(u), int(v)), max(int(u), int(v)))
        if key in lookup:
            raise RoadNetworkError(
                "ferry edge pair appears more than once in the winning graph: "
                f"{key}"
            )
        lookup[key] = float(length)
    return lookup


def ferry_path_metrics(
    predecessors: Any,
    source_node: int,
    target_node: int,
    ferry_pairs: Mapping[tuple[int, int], float],
) -> tuple[float, int]:
    """Trace one shortest-path predecessor chain and sum ferry edges."""
    if source_node == target_node:
        return 0.0, 0
    chain = np.asarray(predecessors)
    if chain.ndim == 2:
        chain = chain[0]
    ferry_m = 0.0
    crossings = 0
    node = int(target_node)
    guard = 0
    while node != source_node:
        previous = int(chain[node])
        if previous < 0:
            raise RoadNetworkError(
                f"shortest-path predecessor chain breaks before source {source_node}"
            )
        key = (min(previous, node), max(previous, node))
        if key in ferry_pairs:
            ferry_m += float(ferry_pairs[key])
            crossings += 1
        node = previous
        guard += 1
        if guard > len(chain):
            raise RoadNetworkError("shortest-path predecessor chain contains a cycle")
    return ferry_m, crossings


def road_ferry_cost(
    distance_km: float,
    ferry_distance_km: float,
    road_cost_per_m3_km: float,
    *,
    ferry_cost_per_m3_km: float = FERRY_FARE_CAD_M3_KM,
) -> tuple[float, float, float]:
    """Return ``(road_km, ferry_km, cost_cad_m3)`` for one path.

    The road rate applies only to non-ferry kilometres; ferry kilometres carry
    the explicit placeholder.  Both Stage A and Stage B use this helper so the
    same shortest path is decomposed identically.
    """
    if not math.isfinite(distance_km) or distance_km < 0.0:
        raise RoadNetworkError(f"invalid path distance {distance_km!r}")
    if not math.isfinite(ferry_distance_km) or ferry_distance_km < 0.0:
        raise RoadNetworkError(f"invalid ferry distance {ferry_distance_km!r}")
    tolerance = max(1e-6, distance_km * 1e-9)
    if ferry_distance_km > distance_km + tolerance:
        raise RoadNetworkError(
            f"ferry distance {ferry_distance_km} exceeds path distance "
            f"{distance_km}"
        )
    ferry_km = min(ferry_distance_km, distance_km)
    road_km = max(distance_km - ferry_km, 0.0)
    return (
        road_km,
        ferry_km,
        road_km * road_cost_per_m3_km + ferry_km * ferry_cost_per_m3_km,
    )


def _add_ferry_network(
    graph: RoadGraph,
    nrn_zip: Path,
    *,
    snap_tolerance_m: float = FERRY_TERMINAL_SNAP_TOLERANCE_M,
) -> RoadGraph:
    """Add NRN ferry segments and documented fallback routes to the graph."""
    tree = cKDTree(graph.node_xy_m)
    segments: list[FerrySegment] = []
    layer_error = ""
    try:
        segments = load_ferry_segments(nrn_zip)
    except (RoadNetworkError, OSError) as error:
        layer_error = str(error)

    route_rows: list[dict[str, Any]] = []
    ferry_u: list[int] = []
    ferry_v: list[int] = []
    ferry_lengths: list[float] = []
    ferry_provenance: list[str] = []
    rejected: list[dict[str, Any]] = []
    for segment in segments:
        distances, indices = tree.query(
            np.array([segment.first_xy, segment.last_xy]), k=1
        )
        if float(np.max(distances)) > snap_tolerance_m:
            rejected.append(
                {
                    "ferry_id": segment.ferry_id,
                    "name": segment.name,
                    "reason": "terminal_too_far",
                    "snap_m": float(np.max(distances)),
                }
            )
            continue
        u, v = int(indices[0]), int(indices[1])
        if u == v:
            rejected.append(
                {
                    "ferry_id": segment.ferry_id,
                    "name": segment.name,
                    "reason": "same_node",
                    "snap_m": float(np.max(distances)),
                }
            )
            continue
        ferry_u.append(u)
        ferry_v.append(v)
        ferry_lengths.append(segment.length_m)
        ferry_provenance.append("nrn_ferryseg")
        route_rows.append(
            {
                "route_id": segment.ferry_id,
                "name": segment.name,
                "source": "nrn_ferryseg",
                "u": u,
                "v": v,
                "distance_km": segment.length_m / 1000.0,
                "crossing_min": segment.length_m / 1000.0 / FERRY_SPEED_KMH * 60.0,
                "terminal_snap_m": float(np.max(distances)),
            }
        )

    # Add published major routes only when the NRN layer does not already
    # connect the same pair of terminals.  This keeps the fallback explicit
    # without double-counting a route that is already present.
    transformer = Transformer.from_crs("EPSG:4326", METRIC_CRS, always_xy=True)
    existing_pairs = list(zip(ferry_u, ferry_v))
    for route in MAJOR_FERRY_ROUTES:
        a_lon, a_lat = route["terminal_a"][1], route["terminal_a"][0]
        b_lon, b_lat = route["terminal_b"][1], route["terminal_b"][0]
        a_xy = transformer.transform(a_lon, a_lat)
        b_xy = transformer.transform(b_lon, b_lat)
        distances, indices = tree.query(np.array([a_xy, b_xy]), k=1)
        if float(np.max(distances)) > snap_tolerance_m:
            route_rows.append(
                {
                    "route_id": route["route_id"],
                    "name": route["name"],
                    "source": "published_fallback",
                    "distance_km": route["distance_km"],
                    "crossing_min": route["crossing_min"],
                    "terminal_snap_m": float(np.max(distances)),
                    "status": "rejected_terminal_too_far",
                }
            )
            continue
        u, v = int(indices[0]), int(indices[1])
        if u == v:
            route_rows.append(
                {
                    "route_id": route["route_id"],
                    "name": route["name"],
                    "source": "published_fallback",
                    "distance_km": route["distance_km"],
                    "crossing_min": route["crossing_min"],
                    "terminal_snap_m": float(np.max(distances)),
                    "status": "rejected_same_node",
                }
            )
            continue
        covered = False
        for pair_u, pair_v in existing_pairs:
            same = (
                np.linalg.norm(graph.node_xy_m[u] - graph.node_xy_m[pair_u])
                <= FERRY_ROUTE_DEDUP_M
                and np.linalg.norm(graph.node_xy_m[v] - graph.node_xy_m[pair_v])
                <= FERRY_ROUTE_DEDUP_M
            )
            swapped = (
                np.linalg.norm(graph.node_xy_m[u] - graph.node_xy_m[pair_v])
                <= FERRY_ROUTE_DEDUP_M
                and np.linalg.norm(graph.node_xy_m[v] - graph.node_xy_m[pair_u])
                <= FERRY_ROUTE_DEDUP_M
            )
            if same or swapped:
                covered = True
                break
        if covered:
            route_rows.append(
                {
                    "route_id": route["route_id"],
                    "name": route["name"],
                    "source": "published_fallback",
                    "distance_km": route["distance_km"],
                    "crossing_min": route["crossing_min"],
                    "terminal_snap_m": float(np.max(distances)),
                    "status": "covered_by_nrn",
                }
            )
            continue
        ferry_u.append(u)
        ferry_v.append(v)
        ferry_lengths.append(float(route["distance_km"]) * 1000.0)
        ferry_provenance.append("published_fallback_ferry")
        existing_pairs.append((u, v))
        route_rows.append(
            {
                "route_id": route["route_id"],
                "name": route["name"],
                "source": "published_fallback",
                "u": u,
                "v": v,
                "distance_km": route["distance_km"],
                "crossing_min": route["crossing_min"],
                "terminal_snap_m": float(np.max(distances)),
                "status": "added",
            }
        )

    if not ferry_lengths:
        raise RoadNetworkError(
            "no ferry edge could be connected to the road graph"
            + (f"; NRN ferry layer error: {layer_error}" if layer_error else "")
        )

    augmented = augment_graph_with_edges(
        graph,
        ferry_u,
        ferry_v,
        ferry_lengths,
        edge_mode=["ferry"] * len(ferry_lengths),
        edge_provenance=ferry_provenance,
    )
    # Keep only ferry edges that won the per-pair shortest-edge contest.  A
    # losing ferry must not contribute distance to a road edge's path.
    winning_modes = np.asarray(augmented.edge_mode, dtype=object)
    winning_ferry = winning_modes == "ferry"
    winning_u = np.asarray(augmented.edge_u, dtype=np.int64)[winning_ferry]
    winning_v = np.asarray(augmented.edge_v, dtype=np.int64)[winning_ferry]
    winning_lengths = np.asarray(augmented.edge_length_m, dtype=float)[winning_ferry]
    winning_pairs = {
        (min(int(u), int(v)), max(int(u), int(v))): float(length)
        for u, v, length in zip(winning_u, winning_v, winning_lengths)
    }
    for row in route_rows:
        if "u" not in row:
            continue
        key = (min(int(row["u"]), int(row["v"])), max(int(row["u"]), int(row["v"])))
        if key not in winning_pairs:
            row["status"] = "shadowed_by_shorter_road_edge"
        row.pop("u", None)
        row.pop("v", None)
    summary = {
        "layer": NRN_FERRY_LAYER,
        "layer_status": "ok" if not layer_error else f"unusable: {layer_error}",
        "nrn_segments": len(segments),
        "candidate_edges": len(ferry_lengths),
        "edges_added": int(winning_ferry.sum()),
        "shadowed_edges": len(ferry_lengths) - int(winning_ferry.sum()),
        "nrn_edges": len(segments) - len(rejected) if segments else 0,
        "rejected": rejected,
        "total_ferry_km": sum(winning_lengths) / 1000.0,
        "speed_kmh": FERRY_SPEED_KMH,
        "fare_placeholder_cad_m3_km": FERRY_FARE_CAD_M3_KM,
        "fare_basis": FERRY_FARE_BASIS,
        "routes": route_rows,
    }
    return replace(
        augmented,
        ferry_edge_u=winning_u,
        ferry_edge_v=winning_v,
        ferry_edge_length_m=winning_lengths,
        ferry_routes=tuple(route_rows),
        ferry_summary=summary,
    )


def _classify_road_classes(values: Sequence[object]) -> tuple[np.ndarray, dict[str, int]]:
    classes = pd.Series(values, dtype="object").fillna("").astype(str).str.strip()
    unknown = sorted(
        set(classes)
        - HAULABLE_ROAD_CLASSES
        - EXCLUDED_ROAD_CLASSES
        - PEDESTRIAN_ROAD_CLASSES
    )
    if unknown:
        raise RoadNetworkError(
            "unrecognised NRN ROADCLASS values; refusing to guess whether they "
            f"are haulable: {unknown}"
        )
    kept = classes.isin(HAULABLE_ROAD_CLASSES).to_numpy()
    counts = classes.value_counts().to_dict()
    return kept, {str(key): int(value) for key, value in counts.items()}


def build_road_graph(
    nrn_zip: Path,
    *,
    layer: str = NRN_ROAD_LAYER,
    snap_tolerance_m: float = NODE_SNAP_TOLERANCE_M,
    include_ferries: bool = True,
    ferry_snap_tolerance_m: float = FERRY_TERMINAL_SNAP_TOLERANCE_M,
) -> RoadGraph:
    """Read, filter, project, and assemble the real BC road graph.

    Ferry segments are added from NRN ``FERRYSEG`` by snapping each segment's
    terminal endpoints to the nearest road node.  Documented major BC Ferries
    routes are added only where the NRN layer does not already cover the pair.
    """
    start = time.perf_counter()
    gpkg_path, member = _extract_nrn_gpkg(nrn_zip)
    try:
        read_start = time.perf_counter()
        info = pyogrio.read_info(gpkg_path, layer=layer)
        frame = pyogrio.read_dataframe(
            gpkg_path,
            layer=layer,
            columns=["ROADSEGID", "ROADCLASS", "TRAFFICDIR"],
        )
        read_s = time.perf_counter() - read_start
        if frame.empty:
            raise RoadNetworkError(f"NRN layer {layer} is empty")
        source_crs = str(frame.crs)
        bounds_values = info["total_bounds"]
        bounds = (
            float(bounds_values[0]),
            float(bounds_values[1]),
            float(bounds_values[2]),
            float(bounds_values[3]),
        )

        project_start = time.perf_counter()
        frame = frame.to_crs(METRIC_CRS)
        project_s = time.perf_counter() - project_start

        kept_mask, class_counts = _classify_road_classes(frame["ROADCLASS"].tolist())
        excluded_counts = {
            key: value
            for key, value in class_counts.items()
            if key in EXCLUDED_ROAD_CLASSES or key in PEDESTRIAN_ROAD_CLASSES
        }
        directions = (
            frame["TRAFFICDIR"].fillna("Unknown").astype(str).value_counts().to_dict()
        )
        frame = frame.loc[kept_mask]
        if frame.empty:
            raise RoadNetworkError("no haulable NRN road segments survived the filter")

        graph_start = time.perf_counter()
        first, last, lengths = _endpoint_arrays(frame.geometry.values)
        (
            node_xy,
            edge_u,
            edge_v,
            edge_length,
            matrix,
            labels,
            sizes,
        ) = assemble_graph(first, last, lengths, snap_tolerance_m=snap_tolerance_m)
        graph_s = time.perf_counter() - graph_start
        timings = {
            "read_s": read_s,
            "project_s": project_s,
            "graph_s": graph_s,
            "total_s": time.perf_counter() - start,
        }
        peak_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
        graph = RoadGraph(
            node_xy_m=node_xy,
            edge_u=edge_u,
            edge_v=edge_v,
            edge_length_m=edge_length,
            matrix=matrix,
            component_labels=labels,
            component_sizes=sizes,
            total_features=len(kept_mask),
            kept_features=int(kept_mask.sum()),
            class_counts=class_counts,
            excluded_counts=excluded_counts,
            source_crs=source_crs,
            bounds_wgs84=bounds,
            traffic_direction_counts={
                str(key): int(value) for key, value in directions.items()
            },
            timings_s=timings,
            peak_rss_mb=peak_mb,
            gpkg_member=member,
            edge_mode=np.full(len(edge_length), "road", dtype=object),
            edge_provenance=np.full(len(edge_length), "nrn_roadseg", dtype=object),
        )
    finally:
        shutil.rmtree(Path(gpkg_path).parent, ignore_errors=True)

    if include_ferries:
        ferry_start = time.perf_counter()
        graph = _add_ferry_network(
            graph, nrn_zip, snap_tolerance_m=ferry_snap_tolerance_m
        )
        timings = dict(graph.timings_s)
        timings["ferry_s"] = time.perf_counter() - ferry_start
        timings["total_s"] = time.perf_counter() - start
        graph = replace(graph, timings_s=timings)
    return graph


def snap_facilities(
    graph: RoadGraph,
    facilities: pd.DataFrame,
    *,
    tolerance_m: float = FACILITY_SNAP_TOLERANCE_M,
) -> pd.DataFrame:
    """Snap facilities with coordinates to the nearest graph node."""
    required = {"facility_id", "lat", "lon", "coord_source"}
    missing_columns = required - set(facilities.columns)
    if missing_columns:
        raise RoadNetworkError(
            f"facility table is missing columns {sorted(missing_columns)}"
        )
    if tolerance_m <= 0.0:
        raise RoadNetworkError("facility snap tolerance must be positive")

    table = facilities.copy()
    table["lat"] = pd.to_numeric(table["lat"], errors="coerce")
    table["lon"] = pd.to_numeric(table["lon"], errors="coerce")
    table["coord_source"] = table["coord_source"].fillna("missing").astype(str)
    has_coordinate = table["lat"].notna() & table["lon"].notna()
    declares_missing = table["coord_source"].str.lower().eq("missing")
    if (declares_missing & has_coordinate).any():
        raise RoadNetworkError("a facility declares coord_source=missing but has coordinates")
    if (~declares_missing & ~has_coordinate).any():
        raise RoadNetworkError(
            "a facility has coord_source != missing but lacks coordinates"
        )

    snapped = table.loc[has_coordinate].copy()
    transformer = Transformer.from_crs("EPSG:4326", METRIC_CRS, always_xy=True)
    x, y = transformer.transform(snapped["lon"].to_numpy(), snapped["lat"].to_numpy())
    tree = cKDTree(graph.node_xy_m)
    distance, node_index = tree.query(np.column_stack([x, y]), k=1)
    snapped["snap_m"] = np.round(distance.astype(float), 3)
    snapped["snap_status"] = np.where(
        distance <= tolerance_m, "snapped", "too_far"
    )
    snapped["node_index"] = node_index.astype(int)
    snapped["component"] = graph.component_labels[node_index].astype(int)
    snapped["node_x_m"] = graph.node_xy_m[node_index, 0]
    snapped["node_y_m"] = graph.node_xy_m[node_index, 1]

    missing = table.loc[~has_coordinate].copy()
    missing["snap_m"] = np.nan
    missing["snap_status"] = "missing_coord"
    missing["node_index"] = pd.NA
    missing["component"] = pd.NA
    missing["node_x_m"] = np.nan
    missing["node_y_m"] = np.nan
    result = pd.concat([snapped, missing], ignore_index=True)
    order = {facility_id: position for position, facility_id in enumerate(table["facility_id"])}
    result["_order"] = result["facility_id"].map(order)
    result = result.sort_values("_order").drop(columns="_order").reset_index(drop=True)
    result["node_index"] = result["node_index"].astype("Int64")
    result["component"] = result["component"].astype("Int64")
    return result


def path_distances(
    graph: RoadGraph,
    snap: pd.DataFrame,
    *,
    anchor_facility_id: str = TSA24_ANCHOR_FACILITY,
) -> pd.DataFrame:
    """Compute shortest-path distance from the anchored node to every snap node."""
    anchor_rows = snap.loc[snap["facility_id"] == anchor_facility_id]
    if anchor_rows.empty:
        raise RoadNetworkError(f"anchor facility {anchor_facility_id} is absent")
    anchor = anchor_rows.iloc[0]
    if anchor["snap_status"] != "snapped":
        raise RoadNetworkError(
            f"anchor {anchor_facility_id} is not on the road graph: "
            f"snap_status={anchor['snap_status']}"
        )
    distances = csgraph.dijkstra(
        graph.matrix,
        directed=False,
        indices=[int(anchor["node_index"])],
    )[0]
    table = snap.copy()
    node = table["node_index"].astype("Int64")
    snapped = table["snap_status"].eq("snapped")
    table["distance_km"] = np.nan
    table.loc[snapped, "distance_km"] = (
        distances[node[snapped].astype(int).to_numpy()] / 1000.0
    )
    table["has_path"] = table["distance_km"].notna() & np.isfinite(table["distance_km"])
    table["distance_km"] = table["distance_km"].where(table["has_path"])
    return table


def _stats(values: Sequence[float]) -> dict[str, float | int]:
    finite = sorted(float(value) for value in values if math.isfinite(float(value)))
    if not finite:
        return {"count": 0}
    if len(finite) == 1:
        value = finite[0]
        return {
            "count": 1,
            "min": value,
            "p25": value,
            "median": value,
            "p75": value,
            "max": value,
            "mean": value,
        }
    quartiles: np.ndarray = np.percentile(finite, [25, 50, 75])
    p25 = float(quartiles[0])
    median = float(quartiles[1])
    p75 = float(quartiles[2])
    return {
        "count": len(finite),
        "min": finite[0],
        "p25": p25,
        "median": median,
        "p75": p75,
        "max": finite[-1],
        "mean": float(np.mean(finite)),
    }


def _cost_comparison(
    real_links: pd.DataFrame, ffcm_links: pd.DataFrame
) -> dict[str, Any]:
    real = real_links[["facility", "grade_id", "cost_cad_m3"]].rename(
        columns={"facility": "facility_id"}
    )
    ffcm = ffcm_links[["destination", "grade_id", "cost_cad_m3"]].rename(
        columns={"destination": "facility_id"}
    )
    merged = real.merge(
        ffcm, on=["facility_id", "grade_id"], suffixes=("_real", "_ffcm")
    )
    by_grade: dict[str, Any] = {}
    for grade_id, group in merged.groupby("grade_id"):
        real_cost = group["cost_cad_m3_real"].astype(float).tolist()
        ffcm_cost = group["cost_cad_m3_ffcm"].astype(float).tolist()
        delta = [new - old for new, old in zip(real_cost, ffcm_cost)]
        by_grade[str(grade_id)] = {
            "matched_links": len(group),
            "real_cost_cad_m3": _stats(real_cost),
            "ffcm_cost_cad_m3": _stats(ffcm_cost),
            "real_minus_ffcm_cad_m3": _stats(delta),
        }
    return {"matched_links": len(merged), "by_grade": by_grade}


def _read_ffcm_distances(path: Path) -> dict[str, float]:
    rows = pd.read_csv(path)
    required = {"destination", "distance_km"}
    missing = required - set(rows.columns)
    if missing:
        raise RoadNetworkError(f"FFCM snapshot is missing columns {sorted(missing)}")
    if "path_confidence" in rows.columns and rows["path_confidence"].eq(
        "real_road_network"
    ).any():
        raise RoadNetworkError(
            f"{path} already contains real NRN paths; the FFCM snapshot is lost"
        )
    return _facility_distances_from_frame(rows)


def _facility_distances_from_frame(rows: pd.DataFrame) -> dict[str, float]:
    distances: dict[str, float] = {}
    for row in rows.itertuples(index=False):
        destination = str(row.destination).strip()
        distance = float(row.distance_km)
        existing = distances.get(destination)
        if existing is not None and not math.isclose(existing, distance):
            raise RoadNetworkError(
                f"FFCM facility {destination} has inconsistent grade distances"
            )
        distances[destination] = distance
    return distances


def _comparison(
    real_distances: Mapping[str, float],
    ffcm_distances: Mapping[str, float],
    old_distances: Mapping[str, float],
) -> dict[str, Any]:
    matched = sorted(set(real_distances) & set(ffcm_distances))
    shared_old = sorted(set(matched) & set(old_distances))
    real_values = [real_distances[key] for key in matched]
    ffcm_values = [ffcm_distances[key] for key in matched]
    old_values = [old_distances[key] for key in shared_old]
    real_old_values = [real_distances[key] for key in shared_old]
    delta = [real - ffcm for real, ffcm in zip(real_values, ffcm_values)]
    ratio = [
        real / ffcm if ffcm > 0.0 else math.inf
        for real, ffcm in zip(real_values, ffcm_values)
    ]
    outliers = []
    for facility_id in matched:
        real = real_distances[facility_id]
        ffcm = ffcm_distances[facility_id]
        item_ratio = real / ffcm if ffcm > 0.0 else math.inf
        item_delta = real - ffcm
        if item_delta > 100.0 or item_ratio > 1.5 or item_ratio < 1.0 / 1.5:
            outliers.append(
                {
                    "facility_id": facility_id,
                    "real_km": real,
                    "ffcm_km": ffcm,
                    "delta_km": item_delta,
                    "ratio": item_ratio,
                }
            )
    outliers.sort(key=lambda item: abs(float(item["delta_km"])), reverse=True)  # type: ignore[arg-type]
    return {
        "matched_facility_count": len(matched),
        "real_distance_km": _stats(real_values),
        "ffcm_distance_km": _stats(ffcm_values),
        "real_minus_ffcm_km": _stats(delta),
        "real_over_ffcm": _stats(ratio),
        "old_diagnostic_facility_count": len(shared_old),
        "old_diagnostic_distance_km": _stats(old_values),
        "real_on_old_match_km": _stats(real_old_values),
        "flat_150_km": {"count": len(real_values), "value": 150.0},
        "outliers": outliers[:20],
        "outlier_count": len(outliers),
    }


def _legacy_diagnostics(
    routes: pd.DataFrame, facility_ids: Sequence[str]
) -> dict[str, float]:
    """Return the 150 km + TL-leg diagnostic for each requested facility."""
    targets = set(facility_ids)
    minimum: dict[str, float] = {}
    for row in routes.itertuples(index=False):
        if str(row.transport_mode).strip().upper() != "ROAD":
            continue
        length = float(str(row.total_length))
        if length <= 0.0:
            continue
        origins = [value.strip() for value in str(row.origin_list).split(";") if value.strip()]
        destinations = [
            value.strip() for value in str(row.destination_list).split(";") if value.strip()
        ]
        for origin, destination in zip(origins, destinations):
            if origin in targets and destination.startswith("TL"):
                current = minimum.get(origin)
                minimum[origin] = length if current is None else min(current, length)
            if destination in targets and origin.startswith("TL"):
                current = minimum.get(destination)
                minimum[destination] = (
                    length if current is None else min(current, length)
                )
    return {
        facility_id: 150.0 + length
        for facility_id, length in minimum.items()
    }


def _write_plot(
    path: Path,
    real_distances: Mapping[str, float],
    ffcm_distances: Mapping[str, float],
    old_distances: Mapping[str, float],
) -> None:
    matched = sorted(set(real_distances) & set(ffcm_distances))
    shared_old = sorted(set(matched) & set(old_distances))
    real = [real_distances[key] for key in matched]
    ffcm = [ffcm_distances[key] for key in matched]
    old = [old_distances[key] for key in shared_old]
    real_old = [real_distances[key] for key in shared_old]
    flat = [150.0] * len(matched)

    figure, (left, right) = plt.subplots(1, 2, figsize=(12.5, 4.8))
    bins = np.linspace(0.0, max(real + ffcm + old + flat) * 1.02, 36)
    left.hist(real, bins=bins, alpha=0.62, label="Real NRN road", color="#0f766e")
    left.hist(ffcm, bins=bins, alpha=0.55, label="FFCM TL graph", color="#d97706")
    left.hist(old, bins=bins, alpha=0.45, label="150 km diagnostic", color="#64748b")
    left.set_xlabel("Road distance from TSA24 anchor (km)")
    left.set_ylabel("Facilities")
    left.set_title("Matched facilities: distance distributions")
    left.legend(frameon=False)
    left.grid(alpha=0.18)

    right.boxplot(
        [real, ffcm, old, real_old],
        tick_labels=["Real NRN", "FFCM", "150 km diag.", "Real (old match)"],
        showfliers=True,
        patch_artist=True,
        boxprops={"facecolor": "#ccfbf1", "edgecolor": "#0f766e"},
        medianprops={"color": "#134e4a", "linewidth": 2},
    )
    right.set_ylabel("km")
    right.set_title("Distance spread (same facilities)")
    right.grid(axis="y", alpha=0.18)
    figure.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(path, dpi=150)
    plt.close(figure)


def _read_previous_stage_a(pilot_dir: Path) -> dict[str, Any] | None:
    manifest_path = pilot_dir / "manifest.json"
    if not manifest_path.exists():
        return None
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    summary: dict[str, Any] = {
        "objective_value_cad": manifest.get("run", {}).get("objective_value_cad"),
        "solver_status": manifest.get("run", {}).get("solver_status"),
        "transport_link_count": manifest.get("counts", {}).get(
            "transport_link_count"
        ),
        "transport_summary": manifest.get("config", {}).get(
            "transport_summary", {}
        ),
        "generated_utc": manifest.get("generated_utc"),
    }
    assignments = pilot_dir / "facility_assignment.csv"
    if assignments.exists():
        frame = pd.read_csv(assignments)
        volume = frame["volume_m3"].astype(float)
        summary["assigned_m3"] = float(volume.sum())
        summary["class_mix"] = {
            str(key): float(value)
            for key, value in volume.groupby(frame["facility_class"]).sum().items()
        }
        delivered = (
            frame["supply_cost_cad_m3"].astype(float)
            + frame["transport_cost_cad_m3"].astype(float)
            + frame["gate_cost_cad_m3"].astype(float)
            + frame["processing_cost_cad_m3"].astype(float)
        )
        summary["delivered_cost_cad_m3"] = (
            float((volume * delivered).sum() / volume.sum())
            if volume.sum() > 0.0
            else None
        )
        summary["transport_cost_cad_m3"] = (
            float(
                (volume * frame["transport_cost_cad_m3"].astype(float)).sum()
                / volume.sum()
            )
            if volume.sum() > 0.0
            else None
        )
    unassigned = pilot_dir / "unassigned_supply.csv"
    if unassigned.exists():
        frame = pd.read_csv(unassigned)
        summary["unassigned_m3"] = (
            float(frame["volume_m3"].astype(float).sum()) if not frame.empty else 0.0
        )
    return summary


def _class_shares(summary: Mapping[str, Any] | None) -> dict[str, float]:
    if not summary:
        return {}
    mix = summary.get("class_mix") or {}
    total = sum(float(value) for value in mix.values())
    if total <= 0.0:
        return {}
    return {str(key): float(value) / total for key, value in mix.items()}


def _ffcm_nameplate_shares(facilities: pd.DataFrame) -> dict[str, float]:
    scoped = facilities.loc[facilities["stage_a_scope"].astype(bool)]
    totals = scoped.groupby("facility_class")["nameplate_m3_y"].sum()
    total = float(totals.sum())
    return {
        str(key): float(value) / total for key, value in totals.items()
    } if total > 0.0 else {}


def _format_stats(stats: Mapping[str, Any], digits: int = 1) -> str:
    if not stats or not stats.get("count"):
        return "n/a"
    return (
        f"n={stats['count']}, min {stats['min']:.{digits}f}, "
        f"p25 {stats['p25']:.{digits}f}, median {stats['median']:.{digits}f}, "
        f"p75 {stats['p75']:.{digits}f}, max {stats['max']:.{digits}f}"
    )


def _markdown_table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    header = "| " + " | ".join(headers) + " |"
    rule = "|" + "|".join("---" for _ in headers) + "|"
    body = ["| " + " | ".join(str(value) for value in row) + " |" for row in rows]
    return "\n".join([header, rule, *body])


def _ferry_markdown(ferry: Mapping[str, Any]) -> str:
    if not ferry:
        return "Ferry summary is unavailable in this report."
    routes = ferry.get("routes") or []
    rows = [
        [
            str(row.get("route_id", "")),
            str(row.get("name", "")),
            str(row.get("source", "")),
            f"{float(row.get('distance_km', 0.0)):.1f}",
            f"{float(row.get('crossing_min', 0.0)):.0f}",
            str(row.get("status", "used")),
        ]
        for row in routes
    ]
    lines = [
        "## 2b. Ferries",
        "",
        (
            f"- Layer: `{ferry.get('layer', NRN_FERRY_LAYER)}` "
            f"({ferry.get('layer_status', 'unknown')})."
        ),
        (
            f"- NRN segments read: {ferry.get('nrn_segments', 0)}; ferry edges added: "
            f"{ferry.get('edges_added', 0)}; total ferry length: "
            f"{float(ferry.get('total_ferry_km', 0.0)):,.0f} km."
        ),
        (
            f"- Crossing speed assumption: {float(ferry.get('speed_kmh', FERRY_SPEED_KMH)):.0f} km/h; "
            f"cost placeholder: {float(ferry.get('fare_placeholder_cad_m3_km', FERRY_FARE_CAD_M3_KM)):.2f} "
            f"CAD/m3/km (`{ferry.get('fare_basis', FERRY_FARE_BASIS)}`).  This is not a tariff."
        ),
    ]
    if rows:
        lines.append("")
        lines.append(
            _markdown_table(
                rows,
                ["Route", "Name", "Source", "km", "min", "Status"],
            )
        )
    return "\n".join(lines)


def _write_report(
    pilot_dir: Path,
    summary: Mapping[str, Any],
    comparison: Mapping[str, Any],
    costs: pd.DataFrame,
    stage_a: Mapping[str, Any] | None,
    previous: Mapping[str, Any] | None,
    facilities: pd.DataFrame,
) -> None:
    snap = summary["facility_snap"]
    graph = summary["graph"]
    build = summary["build"]
    by_source = snap["by_coord_source"]
    source_rows = [
        (
            source,
            values["total"],
            values["with_coord"],
            values["snapped"],
            values["too_far"],
            values["missing_coord"],
            values["in_scope_total"],
            values["in_scope_snapped"],
        )
        for source, values in sorted(by_source.items())
    ]
    class_rows = [
        (name, count, "included") for name, count in sorted(summary["class_filter"]["included"].items())
    ] + [
        (name, count, "excluded") for name, count in sorted(summary["class_filter"]["excluded"].items())
    ]
    outlier_rows = [
        (
            row["facility_id"],
            f"{row['real_km']:.1f}",
            f"{row['ffcm_km']:.1f}",
            f"{row['delta_km']:.1f}",
            f"{row['ratio']:.2f}",
        )
        for row in comparison.get("outliers", [])
    ]
    cost_lines = []
    for row in costs.itertuples(index=False):
        cost_lines.append(
            (
                row.grade_id,
                row.transporter,
                f"{row.rate_per_km:.4f}",
                f"{row.capacity_m3:.2f}",
                f"{row.cost_cad_m3_km:.6f}",
            )
        )
    cost_comparison = summary.get("cost_comparison", {})
    cost_comparison_rows = [
        (
            grade,
            values.get("matched_links", 0),
            f"{values['real_cost_cad_m3']['median']:.2f}",
            f"{values['ffcm_cost_cad_m3']['median']:.2f}",
            f"{values['real_minus_ffcm_cad_m3']['median']:.2f}",
        )
        for grade, values in sorted(cost_comparison.get("by_grade", {}).items())
    ]

    stage_rows: list[tuple[Any, ...]] = []
    for label, data in (("Base 1,000 ha", stage_a), ("ws4 quota", None)):
        if label == "ws4 quota":
            data = summary.get("stage_a_sensitivity")
        if not data:
            continue
        stage_rows.append(
            (
                label,
                f"{data.get('assigned_m3', 0.0):,.0f}",
                f"{data.get('unassigned_m3', 0.0):,.0f}",
                f"{data.get('objective_value_cad', 0.0):,.0f}",
                f"{data.get('delivered_cost_cad_m3', 0.0):.2f}",
                f"{data.get('transport_cost_cad_m3', 0.0):.2f}",
            )
        )
    previous_row = (
        (
            "Previous run",
            f"{previous.get('assigned_m3', 0.0):,.0f}",
            f"{previous.get('unassigned_m3', 0.0):,.0f}",
            f"{previous.get('objective_value_cad', 0.0):,.0f}",
            f"{previous.get('delivered_cost_cad_m3', 0.0):.2f}",
            f"{previous.get('transport_cost_cad_m3', 0.0):.2f}",
        )
        if previous
        else None
    )
    previous_sensitivity = summary.get("stage_a_sensitivity_previous")
    previous_sensitivity_row = (
        (
            "Previous run ws4 quota",
            f"{previous_sensitivity.get('assigned_m3', 0.0):,.0f}",
            f"{previous_sensitivity.get('unassigned_m3', 0.0):,.0f}",
            f"{previous_sensitivity.get('objective_value_cad', 0.0):,.0f}",
            f"{previous_sensitivity.get('delivered_cost_cad_m3', 0.0):.2f}",
            f"{previous_sensitivity.get('transport_cost_cad_m3', 0.0):.2f}",
        )
        if previous_sensitivity
        else None
    )

    current_shares = _class_shares(stage_a)
    previous_shares = _class_shares(previous)
    sensitivity_shares = _class_shares(summary.get("stage_a_sensitivity"))
    previous_sensitivity_shares = _class_shares(previous_sensitivity)
    nameplate_shares = _ffcm_nameplate_shares(facilities)
    classes = sorted(
        set(current_shares)
        | set(previous_shares)
        | set(sensitivity_shares)
        | set(previous_sensitivity_shares)
        | set(nameplate_shares)
    )
    mix_rows = [
        (
            facility_class,
            f"{current_shares.get(facility_class, 0.0):.1%}",
            f"{previous_shares.get(facility_class, 0.0):.1%}",
            f"{sensitivity_shares.get(facility_class, 0.0):.1%}",
            f"{previous_sensitivity_shares.get(facility_class, 0.0):.1%}",
            f"{nameplate_shares.get(facility_class, 0.0):.1%}",
        )
        for facility_class in classes
    ]
    base_basis = summary.get("stage_a_basis", {})
    sensitivity_basis = summary.get("stage_a_sensitivity_basis", {})
    base_basis_text = (
        "Base assigned facilities: "
        f"{base_basis.get('assigned_facility_count', 0)} total; "
        f"{len(base_basis.get('assigned_real_facilities', []))} with a real NRN "
        f"path ({', '.join(base_basis.get('assigned_real_facilities', [])) or 'none'}) "
        f"and {len(base_basis.get('assigned_fallback_facilities', []))} on FFCM "
        f"fallback ({', '.join(base_basis.get('assigned_fallback_facilities', [])) or 'none'})."
        if base_basis
        else ""
    )
    sensitivity_basis_text = (
        "ws4-quota assigned facilities: "
        f"{sensitivity_basis.get('assigned_facility_count', 0)} total; "
        f"{len(sensitivity_basis.get('assigned_real_facilities', []))} real and "
        f"{len(sensitivity_basis.get('assigned_fallback_facilities', []))} fallback."
        if sensitivity_basis
        else ""
    )

    ferry = summary.get("ferry") or {}

    report = f"""# BC Road-Network Transport Pilot (NRN)

## 1. Network

- **Layer:** `{summary['network_source']['layer']}` from
  `{summary['network_source']['archive']}` (English GeoPackage member
  `{summary['network_source']['gpkg_member']}`; NRN 2.0 `SPECVERS`, archive
  member dated `{summary['network_source']['release_date']}`).
- **CRS:** source `{summary['network_source']['source_crs']}`; metric routing
  and lengths in `{summary['network_source']['metric_crs']}`.
- **Clip:** {summary['clip']['description']}
- **Graph:** {graph['nodes']:,} nodes and {graph['edges']:,} undirected edges
  after {summary['graph']['deduplication']}; {graph['components']:,} connected
  components, largest {graph['largest_component_nodes']:,} nodes
  ({graph['largest_component_share']:.1%}).
- **Build:** total {build['total_s']:.1f} s (read {build['read_s']:.1f} s,
  project {build['project_s']:.1f} s, graph {build['graph_s']:.1f} s); peak RSS
  {build['peak_rss_mb']:.0f} MB. Timebox {build['time_limit_s']:.0f} s;
  {build['timebox_status']}.
- **Node snap:** segment endpoints snapped to a
  {summary['snap_tolerance']['node_m']:.1f} m grid before deduplication.
- **Facility snap:** nearest graph node within
  {summary['snap_tolerance']['facility_m']:.0f} m.
- **One-way:** `TRAFFICDIR` values
  {summary['one_way']['values']}; all edges treated as bidirectional.
- **Class filter:** kept {summary['class_filter']['kept_features']:,} of
  {summary['class_filter']['total_features']:,} segments. Pedestrian classes
  present: {summary['class_filter']['pedestrian_present'] or 'none'}.

{_markdown_table(['ROADCLASS', 'Segments', 'Treatment'], class_rows)}

## 2. Snapping

Anchor `{TSA24_ANCHOR_FACILITY}` snapped to a node {summary['anchor']['snap_m']:.1f} m
away (status `{summary['anchor']['snap_status']}`); it is on the graph and was used
as the shortest-path source. Across all {snap['total']} facilities,
{snap['with_coord']} carry coordinates: {snap['snapped']} snapped within
{summary['snap_tolerance']['facility_m']:.0f} m, {snap['too_far']} were
`too_far`, and {snap['missing_coord']} were `missing_coord` (never snapped).

{_markdown_table(['coord_source', 'Total', 'With coord', 'Snapped', 'Too far', 'Missing coord', 'In scope', 'In-scope snapped'], source_rows)}

Real paths were available for **{summary['paths']['facilities_with_path']}**
of the {snap['with_coord']} coordinate-bearing facilities. Facilities on a
different connected component from the anchor are marked `no_path` rather than
given a Euclidean guess.  NRN `FERRYSEG` edges are added where terminal
endpoints snap to the road graph within {FERRY_TERMINAL_SNAP_TOLERANCE_M:.0f} m;
island and coastal facilities are therefore routable when a ferry terminal is
present.

{_ferry_markdown(ferry)}

## 3. Distances and costs

Matched real/FFCM facilities: {comparison['matched_facility_count']}.

{_markdown_table(['Distance set', 'Distribution (km)'], [
    ('Real NRN road', _format_stats(comparison['real_distance_km'])),
    ('FFCM TL graph (same facilities)', _format_stats(comparison['ffcm_distance_km'])),
    ('Real - FFCM', _format_stats(comparison['real_minus_ffcm_km'])),
    ('Real / FFCM', _format_stats(comparison['real_over_ffcm'], 2)),
    ('150 km diagnostic (same facilities)', _format_stats(comparison['old_diagnostic_distance_km'])),
    ('Real on old-diagnostic match', _format_stats(comparison['real_on_old_match_km'])),
])}

Outliers (|real - FFCM| > 100 km or ratio outside 0.67-1.5): 
{comparison['outlier_count']}. Top 20:

{_markdown_table(['Facility', 'Real km', 'FFCM km', 'Delta km', 'Ratio'], outlier_rows) if outlier_rows else 'None.'}

**Cost formula.** No transload for direct road. For grade `g`:

`cost_cad_m3 = distance_km * (fuel_cost + operating_cost / cruising_speed) / capacity_m3`

where capacity is the Stage A transporter-material capacity converted to green
m3 at the grade density, and costs/rates come from
`metadata/ffcm-archive/parsed/transporters.csv` and
`transporter_materials.csv`. Route capacity is absent; every link records
`capacity_m3=None`, `capacity_source=absent`.

{_markdown_table(['Grade', 'Transporter', 'CAD/km', 'Capacity m3', 'CAD/m3/km'], cost_lines)}

Matched real/FFCM cost comparison (same facility-grade pairs):

{_markdown_table(['Grade', 'Links', 'Real median CAD/m3', 'FFCM median CAD/m3', 'Delta median CAD/m3'], cost_comparison_rows) if cost_comparison_rows else 'No matched cost rows.'}

FFCM costs in this comparison include transload at TL nodes; real NRN costs are
direct road with no transload. The gap is therefore a combined distance and
transload difference, not a like-for-like rate comparison.

## 4. Stage A re-run

The base 1,000 ha and ws4-quota runs use real NRN links for facilities with a
real path and flagged FFCM fallback links elsewhere. Every fallback is recorded
in `transport_links_bc.csv` with status
`linked_ffcm_fallback_no_real_road_path`.

{_markdown_table(['Run', 'Assigned m3', 'Unassigned m3', 'Objective CAD', 'Delivered CAD/m3', 'Transport CAD/m3'], [*stage_rows, *[row for row in (previous_row, previous_sensitivity_row) if row]])}

{base_basis_text}

{sensitivity_basis_text}

{_markdown_table(['Class', 'Base current', 'Base previous', 'ws4 current', 'ws4 previous', 'FFCM nameplate'], mix_rows)}

Real-road facility count in the current run:
{summary.get('stage_a_real_road', {}).get('facility_count', 'n/a')}; FFCM
fallback facility count:
{summary.get('stage_a_real_road', {}).get('fallback_facility_count', 'n/a')}.
The fallback list is in `road_network_summary.json` and the Stage A manifest.

## 5. Gaps and next

- NRN BC archive is the 2018 NRN 2.0 release; resource-road coverage is
  incomplete relative to current forest operations.
- `TRAFFICDIR` is `Unknown` for every segment, so one-way restrictions are not
  modelled.
- {snap['too_far']} coordinate-bearing facilities are more than
  {summary['snap_tolerance']['facility_m']:.0f} m from an NRN node; they keep a
  flagged FFCM fallback rather than a fabricated distance.
- The 5 km snap access leg is recorded as `snap_m` but is not added to the
  graph distance or cost; it remains an unmodelled access gap.
- Vancouver Island and coastal islands are connected through NRN `FERRYSEG`
  edges and documented fallback routes.  Ferry fare is a flagged placeholder,
  crossing time is distance / {FERRY_SPEED_KMH:.0f} km/h, and no sailing
  schedule, queue, or seasonal capacity is modelled.
- NRWN rail is not used in this pass; all links are road.
- Route and transload capacity remain absent; the LP cannot model congestion or
  mode-shift limits.
- Real and FFCM-fallback links have different cost bases; the Stage A objective
  is a mixed-basis diagnostic, not a production delivered-cost estimate.
"""
    (pilot_dir / "NETWORK_REPORT.md").write_text(report, encoding="utf-8")


def _write_csv(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, lineterminator="\n")


def _preserve_ffcm_snapshot(pilot_dir: Path) -> Path:
    snapshot = pilot_dir / "transport_links_bc_ffcm.csv"
    if snapshot.exists():
        return snapshot
    current = pilot_dir / "transport_links_bc.csv"
    if not current.exists():
        raise RoadNetworkError(
            f"neither {snapshot} nor {current} exists; cannot preserve FFCM baseline"
        )
    frame = pd.read_csv(current)
    if "path_confidence" in frame.columns and frame["path_confidence"].eq(
        "real_road_network"
    ).any():
        raise RoadNetworkError(
            "transport_links_bc.csv already contains real paths and no FFCM "
            "snapshot exists"
        )
    shutil.copyfile(current, snapshot)
    return snapshot


def _build_links(
    repo_root: Path,
    pilot_dir: Path,
    facilities: pd.DataFrame,
    snap: pd.DataFrame,
    offers: pd.DataFrame,
    grades: pd.DataFrame,
    graph: RoadGraph,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    anchor_rows = snap.loc[snap["facility_id"] == TSA24_ANCHOR_FACILITY]
    if anchor_rows.empty or anchor_rows.iloc[0]["snap_status"] != "snapped":
        raise RoadNetworkError("the TSA24 anchor is not snapped to the road graph")
    usable = snap.loc[
        snap["has_path"] & snap["facility_id"].ne(TSA24_ANCHOR_FACILITY)
    ].copy()
    usable = usable.set_index("facility_id")

    grade_density = {
        str(row.grade_id): float(row.density_odt_m3)
        for row in grades.itertuples(index=False)
    }
    rates, capacities = load_road_rate_inputs(repo_root)
    rate_rows = []
    rate_by_grade = {}
    for grade_id, density in grade_density.items():
        rate = road_rate_for_grade(rates, capacities, grade_id=grade_id)
        cost_per_m3_km = rate.cost_per_m3_km(density)
        rate_by_grade[grade_id] = (rate, density, cost_per_m3_km)
        rate_rows.append(
            {
                "grade_id": grade_id,
                "transporter": rate.transporter,
                "rate_per_km": rate.rate_per_km,
                "capacity_m3": rate.capacity_m3(density),
                "cost_cad_m3_km": cost_per_m3_km,
                "density_odt_m3": density,
            }
        )
    rates_frame = pd.DataFrame(rate_rows)

    rows: list[dict[str, Any]] = []
    for facility_id, facility in facilities.set_index("facility_id").iterrows():
        if facility_id not in usable.index:
            continue
        distance_km = float(usable.loc[facility_id, "distance_km"])
        snap_m = float(usable.loc[facility_id, "snap_m"])
        node_index = int(usable.loc[facility_id, "node_index"])
        grade_rows = offers.loc[offers["facility_id"] == facility_id, "grade_id"]
        if grade_rows.empty:
            continue
        for grade_id in sorted(set(grade_rows.astype(str))):
            if grade_id not in rate_by_grade:
                raise RoadNetworkError(
                    f"facility {facility_id} offers unknown grade {grade_id}"
                )
            rate, _density, cost_per_km = rate_by_grade[grade_id]
            cost = distance_km * cost_per_km
            rows.append(
                {
                    "origin": "TSA24_AU23000",
                    "facility": facility_id,
                    "destination": facility_id,
                    "grade_id": grade_id,
                    "mode": "road",
                    "distance_km": round(distance_km, 6),
                    "linehaul_cost_cad_m3": round(cost, 6),
                    "transload_cost_cad_m3": 0.0,
                    "cost_cad_m3": round(cost, 6),
                    "capacity_m3": "",
                    "capacity_source": "absent",
                    "path_confidence": "real_road_network",
                    "snap_m": round(snap_m, 3),
                    "network_source": (
                        "NRN_BC_14_0_ROADSEG;EPSG:3005;graph_shortest_path"
                    ),
                    "anchor_id": TSA24_ANCHOR_FACILITY,
                    "distance_basis": "nrn_bc_road_graph_shortest_path_epsg3005",
                    "cost_basis": (
                        f"stage_a_transporter_rates:{rate.transporter};"
                        "fuel_plus_operating_per_hour_over_speed;no_transload"
                    ),
                    "evidence": (
                        f"nrn_bc_2018;graph_node={node_index};"
                        f"snap_m={snap_m:.3f};route_capacity_absent"
                    ),
                }
            )
    links = pd.DataFrame(rows)
    if links.empty:
        raise RoadNetworkError("no facility produced a real road link")
    return links, rates_frame


def _summary_payload(
    graph: RoadGraph,
    snap: pd.DataFrame,
    links: pd.DataFrame,
    facilities: pd.DataFrame,
    rates: pd.DataFrame,
    comparison: Mapping[str, Any],
) -> dict[str, Any]:
    anchor = snap.loc[snap["facility_id"] == TSA24_ANCHOR_FACILITY].iloc[0]
    by_source: dict[str, dict[str, int]] = {}
    fac = facilities.set_index("facility_id")
    for source, group in snap.groupby(snap["coord_source"].astype(str)):
        ids = set(group["facility_id"])
        in_scope = group["facility_id"].map(fac["stage_a_scope"].astype(bool))
        by_source[str(source)] = {
            "total": len(group),
            "with_coord": int((group["snap_status"] != "missing_coord").sum()),
            "snapped": int((group["snap_status"] == "snapped").sum()),
            "too_far": int((group["snap_status"] == "too_far").sum()),
            "missing_coord": int((group["snap_status"] == "missing_coord").sum()),
            "in_scope_total": len(ids & set(fac.index[fac["stage_a_scope"].astype(bool)])),
            "in_scope_snapped": int(
                ((group["snap_status"] == "snapped") & in_scope).sum()
            ),
        }
    path_facilities = int(
        snap.loc[snap["has_path"] & snap["facility_id"].ne(TSA24_ANCHOR_FACILITY)].shape[0]
    )
    largest = int(graph.component_sizes.max())
    timebox = graph.timings_s["total_s"] <= BUILD_TIME_LIMIT_S
    return {
        "network_source": {
            "archive": "data/raw/nrn/nrn_bc_gpkg.zip",
            "layer": NRN_ROAD_LAYER,
            "gpkg_member": graph.gpkg_member,
            "source_crs": graph.source_crs,
            "metric_crs": METRIC_CRS,
            "release_date": "2018-10-25",
        },
        "clip": {
            "type": "none",
            "description": (
                "No clip: the full BC NRN road layer was processed "
                f"(bounds {graph.bounds_wgs84})."
            ),
            "bounds_wgs84": list(graph.bounds_wgs84),
        },
        "class_filter": {
            "included": {
                key: value
                for key, value in graph.class_counts.items()
                if key in HAULABLE_ROAD_CLASSES
            },
            "excluded": graph.excluded_counts,
            "pedestrian_present": sorted(
                set(graph.class_counts) & PEDESTRIAN_ROAD_CLASSES
            ),
            "kept_features": graph.kept_features,
            "total_features": graph.total_features,
        },
        "snap_tolerance": {
            "node_m": NODE_SNAP_TOLERANCE_M,
            "facility_m": FACILITY_SNAP_TOLERANCE_M,
            "method": (
                "segment endpoints rounded to a 1 m EPSG:3005 grid; facilities "
                "assigned to the nearest graph node by cKDTree"
            ),
        },
        "one_way": {
            "field": "TRAFFICDIR",
            "values": graph.traffic_direction_counts,
            "treatment": "all edges bidirectional; no one-way attribute available",
        },
        "ferry": dict(graph.ferry_summary),
        "graph": {
            "nodes": len(graph.node_xy_m),
            "edges": len(graph.edge_length_m),
            "components": len(graph.component_sizes),
            "largest_component_nodes": largest,
            "largest_component_share": largest / len(graph.node_xy_m),
            "deduplication": "minimum length per unordered endpoint pair",
        },
        "build": {
            **{key: float(value) for key, value in graph.timings_s.items()},
            "peak_rss_mb": float(graph.peak_rss_mb),
            "time_limit_s": BUILD_TIME_LIMIT_S,
            "timebox_status": "within timebox" if timebox else "exceeded timebox",
        },
        "anchor": {
            "facility_id": TSA24_ANCHOR_FACILITY,
            "lat": float(anchor["lat"]),
            "lon": float(anchor["lon"]),
            "snap_m": float(anchor["snap_m"]),
            "snap_status": str(anchor["snap_status"]),
            "node_index": int(anchor["node_index"]),
        },
        "facility_snap": {
            "total": len(snap),
            "with_coord": int((snap["snap_status"] != "missing_coord").sum()),
            "snapped": int((snap["snap_status"] == "snapped").sum()),
            "too_far": int((snap["snap_status"] == "too_far").sum()),
            "missing_coord": int((snap["snap_status"] == "missing_coord").sum()),
            "by_coord_source": by_source,
        },
        "paths": {
            "facilities_with_path": path_facilities,
            "grade_links": len(links),
            "no_path_facilities": sorted(
                snap.loc[
                    snap["snap_status"].eq("snapped")
                    & snap["facility_id"].ne(TSA24_ANCHOR_FACILITY)
                    & ~snap["has_path"],
                    "facility_id",
                ].astype(str)
            ),
        },
        "distance_comparison": dict(comparison),
        "rate_table": rates.to_dict(orient="records"),
        "route_capacity": {
            "capacity_none_count": len(links),
            "capacity_source": "absent",
        },
    }


def _build_mode(
    repo_root: Path,
    nrn_zip: Path,
    pilot_dir: Path,
) -> None:
    graph = build_road_graph(nrn_zip)
    if graph.timings_s["total_s"] > BUILD_TIME_LIMIT_S:
        raise RoadNetworkError(
            f"road graph build took {graph.timings_s['total_s']:.1f} s, above the "
            f"{BUILD_TIME_LIMIT_S:.0f} s timebox; add a documented clip before retrying"
        )
    facilities = pd.read_csv(pilot_dir / "facilities_bc.csv")
    offers = pd.read_csv(pilot_dir / "facility_offers.csv")
    grades = pd.read_csv(pilot_dir / "grades.csv")
    snap = snap_facilities(graph, facilities)
    snap = path_distances(graph, snap)
    links, rates = _build_links(
        repo_root, pilot_dir, facilities, snap, offers, grades, graph
    )

    ffcm_path = _preserve_ffcm_snapshot(pilot_dir)
    ffcm_distances = _read_ffcm_distances(ffcm_path)
    real_distances = {
        str(row.facility): float(row.distance_km)
        for row in links.itertuples(index=False)
    }
    routes = pd.read_csv(
        repo_root / "metadata/ffcm-archive/parsed/routes.csv", dtype=str
    )
    old_distances = _legacy_diagnostics(
        routes, sorted(set(real_distances) | set(ffcm_distances))
    )
    comparison = _comparison(real_distances, ffcm_distances, old_distances)
    summary = _summary_payload(graph, snap, links, facilities, rates, comparison)
    summary["cost_comparison"] = _cost_comparison(
        links, pd.read_csv(ffcm_path)
    )
    summary["stage_a_previous"] = _read_previous_stage_a(pilot_dir)
    summary["stage_a_sensitivity_previous"] = _read_previous_stage_a(
        pilot_dir / "sensitivity_ws4_quota"
    )

    _write_csv(pilot_dir / "transport_links_bc_real.csv", links)
    snap_out = snap.rename(columns={"facility_id": "facility"})
    snap_out["path_status"] = np.where(
        snap_out["has_path"], "path_found", "no_path"
    )
    snap_out.loc[snap_out["snap_status"] == "too_far", "path_status"] = "too_far"
    snap_out.loc[
        snap_out["snap_status"] == "missing_coord", "path_status"
    ] = "missing_coord"
    _write_csv(pilot_dir / "snap_diagnostics.csv", snap_out)
    _write_plot(
        pilot_dir / "distance_comparison.png",
        real_distances,
        ffcm_distances,
        old_distances,
    )
    _write_report(
        pilot_dir,
        summary,
        comparison,
        rates,
        None,
        summary.get("stage_a_previous"),
        facilities,
    )
    (pilot_dir / "road_network_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        "road network: "
        f"nodes={len(graph.node_xy_m):,} edges={len(graph.edge_length_m):,} "
        f"build={graph.timings_s['total_s']:.1f}s "
        f"snapped={summary['facility_snap']['snapped']}/"
        f"{summary['facility_snap']['with_coord']} "
        f"real_paths={summary['paths']['facilities_with_path']} "
        f"links={len(links)}"
    )


def _assignment_basis(
    pilot_dir: Path, real_facilities: set[str]
) -> dict[str, Any]:
    assignments = pilot_dir / "facility_assignment.csv"
    if not assignments.exists():
        return {}
    frame = pd.read_csv(assignments)
    assigned = set(frame["facility_id"].astype(str))
    return {
        "assigned_facility_count": len(assigned),
        "assigned_real_facilities": sorted(assigned & real_facilities),
        "assigned_fallback_facilities": sorted(assigned - real_facilities),
    }


def _report_mode(pilot_dir: Path, repo_root: Path) -> None:
    summary = json.loads(
        (pilot_dir / "road_network_summary.json").read_text(encoding="utf-8")
    )
    links = pd.read_csv(pilot_dir / "transport_links_bc_real.csv")
    facilities = pd.read_csv(pilot_dir / "facilities_bc.csv")
    rates = pd.DataFrame(summary["rate_table"])
    comparison = summary["distance_comparison"]
    ffcm_path = pilot_dir / "transport_links_bc_ffcm.csv"
    if ffcm_path.exists():
        summary["cost_comparison"] = _cost_comparison(
            links, pd.read_csv(ffcm_path)
        )
    previous = summary.get("stage_a_previous")
    base = _read_previous_stage_a(pilot_dir)
    sensitivity = _read_previous_stage_a(pilot_dir / "sensitivity_ws4_quota")
    summary["stage_a_sensitivity"] = sensitivity
    transport_summary = (base or {}).get("transport_summary", {})
    summary["stage_a_real_road"] = {
        "facility_count": transport_summary.get("real_road", {}).get(
            "facility_count"
        ),
        "fallback_facility_count": transport_summary.get("ffcm_fallback", {}).get(
            "facility_count"
        ),
    }
    real_facilities = set(links["facility"].astype(str))
    summary["stage_a_basis"] = _assignment_basis(pilot_dir, real_facilities)
    summary["stage_a_sensitivity_basis"] = _assignment_basis(
        pilot_dir / "sensitivity_ws4_quota", real_facilities
    )
    _write_report(
        pilot_dir,
        summary,
        comparison,
        rates,
        base,
        previous,
        facilities,
    )
    (pilot_dir / "road_network_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"updated {pilot_dir / 'NETWORK_REPORT.md'}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[3],
    )
    parser.add_argument("--pilot-dir", type=Path, default=DEFAULT_PILOT_DIR)
    parser.add_argument("--nrn-zip", type=Path, default=DEFAULT_NRN_ZIP)
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="Refresh NETWORK_REPORT.md from existing network and Stage A outputs.",
    )
    args = parser.parse_args(argv)
    repo_root = args.repo_root.resolve()
    pilot_dir = args.pilot_dir
    if not pilot_dir.is_absolute():
        pilot_dir = (repo_root / pilot_dir).resolve()
    nrn_zip = args.nrn_zip
    if not nrn_zip.is_absolute():
        nrn_zip = (repo_root / nrn_zip).resolve()
    if args.report_only:
        _report_mode(pilot_dir, repo_root)
        return 0
    _build_mode(repo_root, nrn_zip, pilot_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
