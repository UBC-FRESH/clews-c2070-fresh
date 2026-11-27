"""Build 50-km RSYC supply subnodes for the national facility assignment.

Each committed 150-km RSYC tile-province supply node is split into a 3x3 grid
of 50-km subcells in EPSG:3978.  Parent production and harvest are allocated
to subcells in proportion to the geometric subcell area inside the parent
tile-province intersection.  Parent, provincial and national totals are
preserved; no new RSYC resolution, facility, capacity, product or residue
assumption is introduced.

Area semantics
--------------
``located_area_ha`` in the committed ``supply_node_distance_v1`` artifact is
the RSYC allocated productive area (the sum of the species/group allocation
rows for the tile-province cell), not the full geometric tile-province
intersection.  The geometric intersection is reconstructed here and the 3x3
partition is validated against it.  The parent production area is validated
against the committed RSYC library sum and is allocated pro rata across the
subcells.  The two quantities are reported separately; the module does not
pretend that the productive area is the geometric tile area.

Labels: ``model_estimate`` / ``observed=false`` / ``policy_grade=false``.
This is a sensitivity-only geometric granularity sidecar.  Chain v5.1, the
frozen artifacts, the UI and official OSeCAN are unchanged.  A ``D`` in either
status column of the parent or data repository git status halts the build.
"""

from __future__ import annotations

import csv
import json
import math
import re
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import geopandas as gpd
from shapely.geometry import Polygon
from shapely.validation import make_valid

from workflows.national.facilities import (
    supply_node_distance_v1 as sn1,
)
from workflows.national.facilities.national_facility_inventory import (
    PROVINCES,
    SHAPE_NAME_TO_CODE,
)

REPO_ROOT = sn1.REPO_ROOT
DATA_ROOT = sn1.DATA_ROOT
PARENT_DIR = DATA_ROOT / "derived/national/facilities/supply_node_distance_v1"
PARENT_NODES_CSV = PARENT_DIR / "supply_nodes.csv"
RSYC_LIBRARY_CSV = sn1.RSYC_LIBRARY_CSV
RSYC_TILES_GPKG = sn1.RSYC_TILES_GPKG
BOUNDARY_GEOJSON = sn1.BOUNDARY_GEOJSON
OUTPUT_DIR = DATA_ROOT / "derived/national/facilities/supply_node_50km_v1"

ARTIFACT = "supply_node_50km_v1"
ARTIFACT_VERSION = "1.0.0"
GRID_SIZE = 3
AREA_EPSILON_HA = 1e-8
GEOMETRY_AREA_TOLERANCE_HA = 1e-4
PRODUCTION_AREA_TOLERANCE_HA = 1e-6
PRODUCTION_TOLERANCE_M3 = 1e-5
WEIGHT_TOLERANCE = 1e-12
PROVINCE_ORDER = tuple(PROVINCES)

EVIDENCE_LABEL = sn1.EVIDENCE_LABEL
OBSERVED = sn1.OBSERVED
POLICY_GRADE = sn1.POLICY_GRADE
NATIONAL_CLAIM = False
OFFICIAL_OSECAN_CHANGED = False
UI_CHANGED = False
SENSITIVITY_ONLY = True
CHAIN_V51_UNCHANGED = True

NODE_FIELDS = [
    "node_id",
    "parent_node_id",
    "tile_id",
    "province",
    "row",
    "col",
    "lat",
    "lon",
    "centroid_method",
    "production_m3",
    "located_area_ha",
    "raw_area_ha",
    "fallback_area_ha",
    "scanfi_area_ha",
    "weight",
    "area_weight",
    "province_production_m3",
    "province_scanfi_area_ha",
    "province_harvest_m3",
    "harvest_m3",
    "subcell_area_ha",
    "subcell_geometry_area_ha",
    "area_fraction",
    "parent_located_area_ha",
    "parent_geometry_area_ha",
    "parent_production_m3",
    "parent_harvest_m3",
    "area_basis",
    "evidence_label",
    "observed",
    "policy_grade",
    "national_claim",
    "official_osecan_changed",
    "ui_changed",
    "sensitivity_only",
    "chain_v51_unchanged",
]
PROVINCE_FIELDS = [
    "province",
    "parent_nodes",
    "subnodes",
    "positive_subnodes",
    "harvest_m3",
    "production_m3",
    "weight_sum",
    "hhi",
    "top_node_id",
    "top_node_share",
    "top_node_harvest_m3",
    "area_weight_sum",
    "min_weight",
    "max_weight",
    "evidence_label",
    "observed",
    "policy_grade",
    "national_claim",
    "official_osecan_changed",
    "ui_changed",
    "sensitivity_only",
    "chain_v51_unchanged",
]

NODE_ID_PATTERN = re.compile(r"^[A-Z]\d+\|[A-Z]{2}\|S[1-3][1-3]$")


class SupplyNode50kmError(sn1.SupplyNodeDistanceError):
    """Raised when a 50-km supply-node invariant fails."""


@dataclass(frozen=True, slots=True)
class ParentNode:
    node_id: str
    tile_id: str
    province: str
    lat: float
    lon: float
    production_m3: float
    located_area_ha: float
    raw_area_ha: float
    fallback_area_ha: float
    scanfi_area_ha: float
    weight: float
    area_weight: float
    province_production_m3: float
    province_scanfi_area_ha: float
    province_harvest_m3: float
    harvest_m3: float


@dataclass(frozen=True, slots=True)
class Subcell:
    row: int
    col: int
    geometry: Any
    geometry_area_ha: float
    area_fraction: float
    centroid_method: str
    point: Any


# ---------------------------------------------------------------------------
# I/O and fail-fast helpers.
# ---------------------------------------------------------------------------

def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _write_csv(
    path: Path, fields: Sequence[str], rows: Sequence[Mapping[str, Any]]
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=list(fields), lineterminator="\n"
        )
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _bool_text(value: bool) -> str:
    return "true" if value else "false"


def _label_fields() -> dict[str, str]:
    return {
        "evidence_label": EVIDENCE_LABEL,
        "observed": _bool_text(OBSERVED),
        "policy_grade": _bool_text(POLICY_GRADE),
        "national_claim": _bool_text(NATIONAL_CLAIM),
        "official_osecan_changed": _bool_text(OFFICIAL_OSECAN_CHANGED),
        "ui_changed": _bool_text(UI_CHANGED),
        "sensitivity_only": _bool_text(SENSITIVITY_ONLY),
        "chain_v51_unchanged": _bool_text(CHAIN_V51_UNCHANGED),
    }


def _nonnegative_float(value: object, *, field: str, context: str) -> float:
    number = sn1._finite_float(value, field=field, context=context)
    if number < 0.0:
        raise SupplyNode50kmError(f"{context}: {field} is negative: {number!r}")
    return number


def _positive_float(value: object, *, field: str, context: str) -> float:
    number = sn1._finite_float(value, field=field, context=context)
    if number <= 0.0:
        raise SupplyNode50kmError(f"{context}: {field} must be positive: {number!r}")
    return number


def assert_no_unexpected_deletions(
    repositories: Sequence[Path] = (REPO_ROOT, DATA_ROOT),
    *,
    status_reader: Callable[[Path], str] = sn1._git_status,
) -> None:
    """Fail closed on any ``D`` in the parent or data repository status."""

    incidents: list[str] = []
    for repository in repositories:
        for line in sn1.parse_deletion_lines(status_reader(repository)):
            incidents.append(f"{repository.name or repository}: {line}")
    if incidents:
        raise SupplyNode50kmError(
            "unexpected deletion status detected; halting: "
            + " | ".join(incidents)
        )


# ---------------------------------------------------------------------------
# Loading.
# ---------------------------------------------------------------------------

def load_parent_nodes(path: Path = PARENT_NODES_CSV) -> tuple[ParentNode, ...]:
    if not path.is_file():
        raise SupplyNode50kmError(f"parent supply nodes are missing: {path}")
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    required = {
        "node_id",
        "tile_id",
        "province",
        "lat",
        "lon",
        "production_m3",
        "located_area_ha",
        "raw_area_ha",
        "fallback_area_ha",
        "scanfi_area_ha",
        "weight",
        "area_weight",
        "province_production_m3",
        "province_scanfi_area_ha",
        "province_harvest_m3",
        "harvest_m3",
        "evidence_label",
        "observed",
        "policy_grade",
    }
    missing = required - set(rows[0] if rows else {})
    if missing:
        raise SupplyNode50kmError(f"{path}: missing columns {sorted(missing)}")
    nodes: list[ParentNode] = []
    seen: set[str] = set()
    for position, row in enumerate(rows, start=2):
        context = f"{path}:{position}"
        node_id = row["node_id"].strip()
        if not node_id:
            raise SupplyNode50kmError(f"{context}: blank node_id")
        if node_id in seen:
            raise SupplyNode50kmError(f"{context}: duplicate node_id {node_id!r}")
        seen.add(node_id)
        province = row["province"].strip().upper()
        if province not in PROVINCE_ORDER:
            raise SupplyNode50kmError(
                f"{context}: unexpected province {province!r}"
            )
        tile_id = row["tile_id"].strip()
        if not tile_id:
            raise SupplyNode50kmError(f"{context}: blank tile_id")
        if node_id != f"{tile_id}|{province}":
            raise SupplyNode50kmError(
                f"{context}: node_id {node_id!r} does not match tile/province"
            )
        lat = sn1._finite_float(row["lat"], field="lat", context=context)
        lon = sn1._finite_float(row["lon"], field="lon", context=context)
        if not -90.0 <= lat <= 90.0 or not -180.0 <= lon <= 180.0:
            raise SupplyNode50kmError(f"{context}: coordinate outside bounds")
        production = _nonnegative_float(
            row["production_m3"], field="production_m3", context=context
        )
        located_area = _nonnegative_float(
            row["located_area_ha"], field="located_area_ha", context=context
        )
        raw_area = _nonnegative_float(
            row["raw_area_ha"], field="raw_area_ha", context=context
        )
        fallback_area = _nonnegative_float(
            row["fallback_area_ha"], field="fallback_area_ha", context=context
        )
        scanfi_area = _nonnegative_float(
            row["scanfi_area_ha"], field="scanfi_area_ha", context=context
        )
        weight = _nonnegative_float(
            row["weight"], field="weight", context=context
        )
        area_weight = _nonnegative_float(
            row["area_weight"], field="area_weight", context=context
        )
        province_production = _positive_float(
            row["province_production_m3"],
            field="province_production_m3",
            context=context,
        )
        province_scanfi = _nonnegative_float(
            row["province_scanfi_area_ha"],
            field="province_scanfi_area_ha",
            context=context,
        )
        province_harvest = _nonnegative_float(
            row["province_harvest_m3"],
            field="province_harvest_m3",
            context=context,
        )
        harvest = _nonnegative_float(
            row["harvest_m3"], field="harvest_m3", context=context
        )
        if row["evidence_label"] != EVIDENCE_LABEL:
            raise SupplyNode50kmError(f"{context}: unexpected evidence label")
        if row["observed"] != _bool_text(OBSERVED):
            raise SupplyNode50kmError(f"{context}: unexpected observed flag")
        if row["policy_grade"] != _bool_text(POLICY_GRADE):
            raise SupplyNode50kmError(f"{context}: unexpected policy grade")
        nodes.append(
            ParentNode(
                node_id=node_id,
                tile_id=tile_id,
                province=province,
                lat=lat,
                lon=lon,
                production_m3=production,
                located_area_ha=located_area,
                raw_area_ha=raw_area,
                fallback_area_ha=fallback_area,
                scanfi_area_ha=scanfi_area,
                weight=weight,
                area_weight=area_weight,
                province_production_m3=province_production,
                province_scanfi_area_ha=province_scanfi,
                province_harvest_m3=province_harvest,
                harvest_m3=harvest,
            )
        )
    if len(nodes) != 525:
        raise SupplyNode50kmError(
            f"{path}: expected 525 parent nodes, found {len(nodes)}"
        )
    return tuple(nodes)


def load_located_area_reference(
    path: Path = RSYC_LIBRARY_CSV,
) -> dict[tuple[str, str], float]:
    """Return the committed RSYC allocated production area by tile-province."""

    rows = sn1._read_csv(path, "RSYC tile production library")
    sn1._require_columns(
        rows,
        {
            "tile_id",
            "province",
            "area_ha",
            "no_province_intersection_area_ha",
        },
        path,
    )
    reference: dict[tuple[str, str], float] = defaultdict(float)
    for position, row in enumerate(rows, start=2):
        context = f"{path}:{position}"
        tile_id = row["tile_id"].strip()
        province = row["province"].strip().upper()
        if not tile_id:
            raise SupplyNode50kmError(f"{context}: blank tile_id")
        if province not in PROVINCE_ORDER:
            raise SupplyNode50kmError(
                f"{context}: unexpected province {province!r}"
            )
        area = _nonnegative_float(row["area_ha"], field="area_ha", context=context)
        fallback = _nonnegative_float(
            row["no_province_intersection_area_ha"],
            field="no_province_intersection_area_ha",
            context=context,
        )
        if fallback > area + 1e-6:
            raise SupplyNode50kmError(
                f"{context}: fallback area exceeds area"
            )
        reference[(tile_id, province)] += area - fallback
    return dict(reference)


def load_geometries() -> tuple[dict[str, Any], dict[str, Any]]:
    tiles = gpd.read_file(RSYC_TILES_GPKG, layer="RSYC_tiles")
    if tiles.crs is None or tiles.crs.to_epsg() != 3978:
        raise SupplyNode50kmError(
            f"{RSYC_TILES_GPKG}: expected EPSG:3978, got {tiles.crs}"
        )
    if "TileID" not in tiles.columns:
        raise SupplyNode50kmError(f"{RSYC_TILES_GPKG}: missing TileID")
    tile_geometries: dict[str, Any] = {}
    for row in tiles.itertuples():
        tile_id = str(row.TileID)
        if tile_id in tile_geometries:
            raise SupplyNode50kmError(
                f"{RSYC_TILES_GPKG}: duplicate TileID {tile_id!r}"
            )
        geometry = make_valid(row.geometry)
        if geometry.is_empty or geometry.area <= 0.0:
            raise SupplyNode50kmError(
                f"{RSYC_TILES_GPKG}: invalid tile geometry {tile_id!r}"
            )
        tile_geometries[tile_id] = geometry

    provinces = gpd.read_file(BOUNDARY_GEOJSON).to_crs(3978)
    province_geometries: dict[str, Any] = {}
    for row in provinces.itertuples():
        code = SHAPE_NAME_TO_CODE.get(str(row.shapeName))
        if code is None:
            raise SupplyNode50kmError(
                f"{BOUNDARY_GEOJSON}: unrecognised shapeName {row.shapeName!r}"
            )
        if code in province_geometries:
            raise SupplyNode50kmError(
                f"{BOUNDARY_GEOJSON}: duplicate province {code!r}"
            )
        geometry = make_valid(row.geometry)
        if geometry.is_empty or geometry.area <= 0.0:
            raise SupplyNode50kmError(
                f"{BOUNDARY_GEOJSON}: invalid province geometry {code!r}"
            )
        province_geometries[code] = geometry
    missing = set(PROVINCE_ORDER) - set(province_geometries)
    if missing:
        raise SupplyNode50kmError(
            f"{BOUNDARY_GEOJSON}: missing province geometries {sorted(missing)}"
        )
    return tile_geometries, province_geometries


# ---------------------------------------------------------------------------
# Geometry partition and node construction.
# ---------------------------------------------------------------------------

def _subcell_geometries(
    parent_geometry: Any, tile_geometry: Any
) -> list[tuple[int, int, Any, float]]:
    """Split the tile bbox into 3x3 and return positive parent intersections."""

    min_x, min_y, max_x, max_y = tile_geometry.bounds
    width = (max_x - min_x) / GRID_SIZE
    height = (max_y - min_y) / GRID_SIZE
    if width <= 0.0 or height <= 0.0:
        raise SupplyNode50kmError("tile bounding box is degenerate")
    subcells: list[tuple[int, int, Any, float]] = []
    for row in range(1, GRID_SIZE + 1):
        for col in range(1, GRID_SIZE + 1):
            box = Polygon(
                [
                    (min_x + (col - 1) * width, min_y + (row - 1) * height),
                    (min_x + col * width, min_y + (row - 1) * height),
                    (min_x + col * width, min_y + row * height),
                    (min_x + (col - 1) * width, min_y + row * height),
                ]
            )
            intersection = box.intersection(parent_geometry)
            if intersection.is_empty:
                continue
            area_ha = intersection.area / 10_000.0
            if not math.isfinite(area_ha) or area_ha <= 0.0:
                continue
            if area_ha <= AREA_EPSILON_HA:
                continue
            subcells.append((row, col, intersection, area_ha))
    if not subcells:
        raise SupplyNode50kmError("no positive subcell intersections")
    return subcells


def _allocate_exact(
    total: float, fractions: Sequence[float], *, context: str
) -> list[float]:
    if not fractions:
        raise SupplyNode50kmError(f"{context}: no fractions")
    if any(not math.isfinite(value) or value < 0.0 for value in fractions):
        raise SupplyNode50kmError(f"{context}: invalid fraction")
    fraction_sum = math.fsum(fractions)
    if not math.isclose(fraction_sum, 1.0, rel_tol=0.0, abs_tol=1e-12):
        raise SupplyNode50kmError(
            f"{context}: fractions sum to {fraction_sum!r}"
        )
    values = [total * fraction for fraction in fractions[:-1]]
    values.append(total - math.fsum(values))
    if values[-1] < 0.0:
        if values[-1] < -1e-9 * max(1.0, total):
            raise SupplyNode50kmError(
                f"{context}: negative residual {values[-1]!r}"
            )
        values[-1] = 0.0
    if not math.isclose(
        math.fsum(values), total, rel_tol=0.0, abs_tol=1e-9 * max(1.0, total)
    ):
        raise SupplyNode50kmError(f"{context}: allocation does not conserve")
    if any(value < 0.0 or not math.isfinite(value) for value in values):
        raise SupplyNode50kmError(f"{context}: invalid allocated value")
    return values


def _point_for_geometry(geometry: Any) -> tuple[Any, str]:
    point = geometry.centroid
    method = "subcell_tile_province_intersection_centroid"
    if not geometry.covers(point):
        point = geometry.representative_point()
        method = "subcell_tile_province_intersection_representative_point"
    if not geometry.buffer(1e-7).covers(point):
        raise SupplyNode50kmError("subcell point is outside its geometry")
    return point, method


def build_subnodes(
    parents: Sequence[ParentNode],
    tile_geometries: Mapping[str, Any],
    province_geometries: Mapping[str, Any],
    located_area_reference: Mapping[tuple[str, str], float],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    province_production: dict[str, float] = defaultdict(float)
    province_harvest: dict[str, float] = defaultdict(float)
    province_scanfi: dict[str, float] = defaultdict(float)
    for parent in parents:
        province_production[parent.province] += parent.production_m3
        province_harvest[parent.province] += parent.harvest_m3
        province_scanfi[parent.province] += parent.scanfi_area_ha
    if province_harvest.get("NU", 0.0) != 0.0:
        raise SupplyNode50kmError("NU harvest must be zero")

    rows: list[dict[str, Any]] = []
    parent_diagnostics: list[dict[str, Any]] = []
    blocks: list[dict[str, Any]] = []
    points_3978: list[Any] = []

    for parent in parents:
        tile_geometry = tile_geometries.get(parent.tile_id)
        province_geometry = province_geometries.get(parent.province)
        if tile_geometry is None:
            raise SupplyNode50kmError(
                f"{parent.tile_id}: no RSYC tile geometry"
            )
        if province_geometry is None:
            raise SupplyNode50kmError(
                f"{parent.province}: no province geometry"
            )
        parent_geometry = tile_geometry.intersection(province_geometry)
        if parent_geometry.is_empty or parent_geometry.area <= 0.0:
            raise SupplyNode50kmError(
                f"{parent.node_id}: empty tile-province intersection"
            )
        parent_geometry_area_ha = parent_geometry.area / 10_000.0
        if not math.isfinite(parent_geometry_area_ha) or (
            parent_geometry_area_ha <= 0.0
        ):
            raise SupplyNode50kmError(
                f"{parent.node_id}: invalid parent geometry area"
            )

        subcell_geometries = _subcell_geometries(
            parent_geometry, tile_geometry
        )
        sum_geometry_area = math.fsum(item[3] for item in subcell_geometries)
        if abs(sum_geometry_area - parent_geometry_area_ha) > (
            GEOMETRY_AREA_TOLERANCE_HA
        ):
            raise SupplyNode50kmError(
                f"{parent.node_id}: subcell areas {sum_geometry_area!r} do not "
                f"sum to parent geometry area {parent_geometry_area_ha!r}"
            )
        fractions = [
            item[3] / sum_geometry_area for item in subcell_geometries
        ]
        fraction_sum = math.fsum(fractions)
        if not math.isclose(fraction_sum, 1.0, abs_tol=1e-12):
            raise SupplyNode50kmError(
                f"{parent.node_id}: area fractions sum to {fraction_sum!r}"
            )

        reference_area = located_area_reference.get(
            (parent.tile_id, parent.province)
        )
        if reference_area is None:
            raise SupplyNode50kmError(
                f"{parent.node_id}: no RSYC library area reference"
            )
        if abs(reference_area - parent.located_area_ha) > (
            PRODUCTION_AREA_TOLERANCE_HA
        ):
            raise SupplyNode50kmError(
                f"{parent.node_id}: located area {parent.located_area_ha!r} "
                f"does not match RSYC library area {reference_area!r}"
            )
        if parent.located_area_ha > 2.0 * parent_geometry_area_ha + 1e-3:
            raise SupplyNode50kmError(
                f"{parent.node_id}: production area is implausibly larger than "
                "the geometric parent area"
            )

        production_values = _allocate_exact(
            parent.production_m3,
            fractions,
            context=f"{parent.node_id} production",
        )
        harvest_values = _allocate_exact(
            parent.harvest_m3,
            fractions,
            context=f"{parent.node_id} harvest",
        )
        located_values = _allocate_exact(
            parent.located_area_ha,
            fractions,
            context=f"{parent.node_id} located area",
        )
        raw_values = _allocate_exact(
            parent.raw_area_ha,
            fractions,
            context=f"{parent.node_id} raw area",
        )
        fallback_values = _allocate_exact(
            parent.fallback_area_ha,
            fractions,
            context=f"{parent.node_id} fallback area",
        )
        scanfi_values = _allocate_exact(
            parent.scanfi_area_ha,
            fractions,
            context=f"{parent.node_id} SCANFI area",
        )

        subcells: list[Subcell] = []
        for index, (row, col, geometry, area_ha) in enumerate(
            subcell_geometries
        ):
            point, method = _point_for_geometry(geometry)
            subcells.append(
                Subcell(
                    row=row,
                    col=col,
                    geometry=geometry,
                    geometry_area_ha=area_ha,
                    area_fraction=fractions[index],
                    centroid_method=method,
                    point=point,
                )
            )
        points_3978.extend(subcell.point for subcell in subcells)
        blocks.append(
            {
                "parent": parent,
                "subcells": subcells,
                "production": production_values,
                "harvest": harvest_values,
                "located": located_values,
                "raw": raw_values,
                "fallback": fallback_values,
                "scanfi": scanfi_values,
                "parent_geometry_area_ha": parent_geometry_area_ha,
                "sum_geometry_area": sum_geometry_area,
            }
        )
        parent_diagnostics.append(
            {
                "parent_node_id": parent.node_id,
                "tile_id": parent.tile_id,
                "province": parent.province,
                "parent_geometry_area_ha": parent_geometry_area_ha,
                "parent_located_area_ha": parent.located_area_ha,
                "located_to_geometry_ratio": (
                    parent.located_area_ha / parent_geometry_area_ha
                ),
                "subcell_count": len(subcells),
                "subcell_geometry_area_ha": sum_geometry_area,
                "geometry_partition_residual_ha": (
                    sum_geometry_area - parent_geometry_area_ha
                ),
                "parent_production_m3": parent.production_m3,
                "subcell_production_m3": math.fsum(production_values),
                "production_residual_m3": (
                    math.fsum(production_values) - parent.production_m3
                ),
                "parent_harvest_m3": parent.harvest_m3,
                "subcell_harvest_m3": math.fsum(harvest_values),
                "harvest_residual_m3": (
                    math.fsum(harvest_values) - parent.harvest_m3
                ),
                "subcell_located_area_ha": math.fsum(located_values),
                "located_area_residual_ha": (
                    math.fsum(located_values) - parent.located_area_ha
                ),
            }
        )

    points_wgs84 = gpd.GeoSeries(points_3978, crs=3978).to_crs(4326)
    point_index = 0
    for block in blocks:
        parent: ParentNode = block["parent"]
        subcells: list[Subcell] = block["subcells"]
        production_values: list[float] = block["production"]
        harvest_values: list[float] = block["harvest"]
        located_values: list[float] = block["located"]
        raw_values: list[float] = block["raw"]
        fallback_values: list[float] = block["fallback"]
        scanfi_values: list[float] = block["scanfi"]
        for index, subcell in enumerate(subcells):
            point = points_wgs84.iloc[point_index]
            point_index += 1
            lat = float(point.y)
            lon = float(point.x)
            if not -90.0 <= lat <= 90.0 or not -180.0 <= lon <= 180.0:
                raise SupplyNode50kmError(
                    f"{parent.node_id}: invalid subnode coordinate"
                )
            node_id = (
                f"{parent.tile_id}|{parent.province}|"
                f"S{subcell.row}{subcell.col}"
            )
            production = production_values[index]
            harvest = harvest_values[index]
            weight = (
                production / province_production[parent.province]
                if province_production[parent.province] > 0.0
                else 0.0
            )
            area_weight = parent.area_weight * subcell.area_fraction
            rows.append(
                {
                    "node_id": node_id,
                    "parent_node_id": parent.node_id,
                    "tile_id": parent.tile_id,
                    "province": parent.province,
                    "row": str(subcell.row),
                    "col": str(subcell.col),
                    "lat": sn1._format_float(lat, 9),
                    "lon": sn1._format_float(lon, 9),
                    "centroid_method": subcell.centroid_method,
                    "production_m3": sn1._format_float(production, 9),
                    "located_area_ha": sn1._format_float(
                        located_values[index], 9
                    ),
                    "raw_area_ha": sn1._format_float(raw_values[index], 9),
                    "fallback_area_ha": sn1._format_float(
                        fallback_values[index], 9
                    ),
                    "scanfi_area_ha": sn1._format_float(
                        scanfi_values[index], 9
                    ),
                    "weight": sn1._format_float(weight, 15),
                    "area_weight": sn1._format_float(area_weight, 15),
                    "province_production_m3": sn1._format_float(
                        parent.province_production_m3, 9
                    ),
                    "province_scanfi_area_ha": sn1._format_float(
                        parent.province_scanfi_area_ha, 9
                    ),
                    "province_harvest_m3": sn1._format_float(
                        parent.province_harvest_m3, 9
                    ),
                    "harvest_m3": sn1._format_float(harvest, 9),
                    "subcell_area_ha": sn1._format_float(
                        located_values[index], 9
                    ),
                    "subcell_geometry_area_ha": sn1._format_float(
                        subcell.geometry_area_ha, 9
                    ),
                    "area_fraction": sn1._format_float(
                        subcell.area_fraction, 15
                    ),
                    "parent_located_area_ha": sn1._format_float(
                        parent.located_area_ha, 9
                    ),
                    "parent_geometry_area_ha": sn1._format_float(
                        block["parent_geometry_area_ha"], 9
                    ),
                    "parent_production_m3": sn1._format_float(
                        parent.production_m3, 9
                    ),
                    "parent_harvest_m3": sn1._format_float(
                        parent.harvest_m3, 9
                    ),
                    "area_basis": (
                        "rsyc_allocated_area_proportional_to_geometric_subcell_area"
                    ),
                    **_label_fields(),
                }
            )

    if point_index != len(points_3978):
        raise SupplyNode50kmError("subnode point count mismatch")
    rows.sort(
        key=lambda row: (
            PROVINCE_ORDER.index(row["province"]),
            row["tile_id"],
            int(row["row"]),
            int(row["col"]),
        )
    )
    diagnostics = {
        "parents": parent_diagnostics,
        "province_production_m3": dict(province_production),
        "province_harvest_m3": dict(province_harvest),
        "province_scanfi_area_ha": dict(province_scanfi),
    }
    return rows, diagnostics



# ---------------------------------------------------------------------------
# Aggregation and validation.
# ---------------------------------------------------------------------------

def _province_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["province"])].append(row)
    result: list[dict[str, Any]] = []
    for province in PROVINCE_ORDER:
        group = grouped.get(province)
        if not group:
            continue
        weights = [float(row["weight"]) for row in group]
        harvests = [float(row["harvest_m3"]) for row in group]
        production = math.fsum(float(row["production_m3"]) for row in group)
        harvest = math.fsum(harvests)
        top = max(
            group,
            key=lambda row: (float(row["weight"]), row["node_id"]),
        )
        result.append(
            {
                "province": province,
                "parent_nodes": str(
                    len({str(row["parent_node_id"]) for row in group})
                ),
                "subnodes": str(len(group)),
                "positive_subnodes": str(
                    sum(1 for value in harvests if value > 0.0)
                ),
                "harvest_m3": sn1._format_float(harvest, 9),
                "production_m3": sn1._format_float(production, 9),
                "weight_sum": sn1._format_float(
                    math.fsum(weights), 15
                ),
                "hhi": sn1._format_float(
                    math.fsum(weight * weight for weight in weights), 12
                ),
                "top_node_id": str(top["node_id"]),
                "top_node_share": str(top["weight"]),
                "top_node_harvest_m3": str(top["harvest_m3"]),
                "area_weight_sum": sn1._format_float(
                    math.fsum(float(row["area_weight"]) for row in group), 15
                ),
                "min_weight": sn1._format_float(min(weights), 15),
                "max_weight": sn1._format_float(max(weights), 15),
                **_label_fields(),
            }
        )
    return result


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
                raise SupplyNode50kmError(f"{label}: non-finite {field}")
            if parsed < 0.0 and field not in ("lat", "lon"):
                raise SupplyNode50kmError(f"{label}: negative {field}")


def validate(
    *,
    parents: Sequence[ParentNode],
    rows: Sequence[Mapping[str, Any]],
    province_rows: Sequence[Mapping[str, Any]],
    diagnostics: Mapping[str, Any],
    located_area_reference: Mapping[tuple[str, str], float],
) -> dict[str, Any]:
    checks: dict[str, bool] = {}
    checks["parent_count_525"] = len(parents) == 525
    checks["subnodes_present"] = len(rows) > 0
    checks["node_ids_unique"] = len({row["node_id"] for row in rows}) == len(rows)
    checks["node_id_format"] = all(
        NODE_ID_PATTERN.match(str(row["node_id"])) for row in rows
    )
    checks["parent_ids_present"] = all(
        str(row["parent_node_id"]) for row in rows
    )
    checks["rows_1_to_3"] = all(
        int(row["row"]) in (1, 2, 3) for row in rows
    )
    checks["cols_1_to_3"] = all(
        int(row["col"]) in (1, 2, 3) for row in rows
    )

    by_parent: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        by_parent[str(row["parent_node_id"])].append(row)
    checks["every_parent_has_subnode"] = set(by_parent) == {
        parent.node_id for parent in parents
    }
    checks["subnodes_per_parent_1_to_9"] = all(
        1 <= len(group) <= 9 for group in by_parent.values()
    )
    checks["parent_geometry_partition_area"] = all(
        abs(float(item["geometry_partition_residual_ha"]))
        <= GEOMETRY_AREA_TOLERANCE_HA
        for item in diagnostics["parents"]
    )
    checks["parent_production_conservation"] = all(
        abs(float(item["production_residual_m3"]))
        <= PRODUCTION_TOLERANCE_M3
        for item in diagnostics["parents"]
    )
    checks["parent_harvest_conservation"] = all(
        abs(float(item["harvest_residual_m3"]))
        <= PRODUCTION_TOLERANCE_M3
        for item in diagnostics["parents"]
    )
    checks["parent_located_area_conservation"] = all(
        abs(float(item["located_area_residual_ha"]))
        <= PRODUCTION_AREA_TOLERANCE_HA
        for item in diagnostics["parents"]
    )
    checks["area_fraction_sum_one"] = all(
        abs(
            math.fsum(float(row["area_fraction"]) for row in group) - 1.0
        )
        <= 1e-12
        for group in by_parent.values()
    )
    checks["subcell_area_conservation"] = all(
        abs(
            math.fsum(float(row["subcell_area_ha"]) for row in group)
            - float(group[0]["parent_located_area_ha"])
        )
        <= PRODUCTION_AREA_TOLERANCE_HA
        for group in by_parent.values()
    )
    checks["production_conservation"] = abs(
        math.fsum(float(row["production_m3"]) for row in rows)
        - math.fsum(parent.production_m3 for parent in parents)
    ) <= PRODUCTION_TOLERANCE_M3
    checks["harvest_conservation"] = abs(
        math.fsum(float(row["harvest_m3"]) for row in rows)
        - math.fsum(parent.harvest_m3 for parent in parents)
    ) <= PRODUCTION_TOLERANCE_M3
    checks["national_harvest_118977339"] = math.isclose(
        math.fsum(float(row["harvest_m3"]) for row in rows),
        118_977_339.1,
        rel_tol=0.0,
        abs_tol=1e-3,
    )
    province_weight_sums: dict[str, float] = defaultdict(float)
    province_production_values: dict[str, list[float]] = defaultdict(list)
    province_harvest_values: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        province = str(row["province"])
        province_weight_sums[province] += float(row["weight"])
        province_production_values[province].append(float(row["production_m3"]))
        province_harvest_values[province].append(float(row["harvest_m3"]))
    province_production = {
        province: math.fsum(values)
        for province, values in province_production_values.items()
    }
    province_harvest = {
        province: math.fsum(values)
        for province, values in province_harvest_values.items()
    }
    checks["province_weight_sum_one"] = all(
        abs(value - 1.0) <= WEIGHT_TOLERANCE
        for value in province_weight_sums.values()
    )
    checks["province_production_conservation"] = all(
        abs(
            province_production[province]
            - math.fsum(
                parent.production_m3
                for parent in parents
                if parent.province == province
            )
        )
        <= PRODUCTION_TOLERANCE_M3
        for province in province_production
    )
    checks["province_harvest_conservation"] = all(
        abs(
            province_harvest[province]
            - math.fsum(
                parent.harvest_m3
                for parent in parents
                if parent.province == province
            )
        )
        <= PRODUCTION_TOLERANCE_M3
        for province in province_harvest
    )
    checks["located_area_matches_rsyc_library"] = all(
        abs(
            parent.located_area_ha
            - located_area_reference.get(
                (parent.tile_id, parent.province), -1.0
            )
        )
        <= PRODUCTION_AREA_TOLERANCE_HA
        for parent in parents
    )
    checks["production_area_within_geometry_sanity"] = all(
        parent.located_area_ha <= 2.0 * float(item["parent_geometry_area_ha"]) + 1e-3
        for parent, item in zip(parents, diagnostics["parents"], strict=True)
    )
    checks["zero_harvest_subnodes_present"] = any(
        float(row["harvest_m3"]) == 0.0 for row in rows
    )
    checks["nu_harvest_zero"] = all(
        float(row["harvest_m3"]) == 0.0
        for row in rows
        if row["province"] == "NU"
    )
    checks["labels"] = all(
        row["evidence_label"] == EVIDENCE_LABEL
        and row["observed"] == _bool_text(OBSERVED)
        and row["policy_grade"] == _bool_text(POLICY_GRADE)
        and row["national_claim"] == _bool_text(NATIONAL_CLAIM)
        and row["official_osecan_changed"] == _bool_text(OFFICIAL_OSECAN_CHANGED)
        and row["ui_changed"] == _bool_text(UI_CHANGED)
        and row["sensitivity_only"] == _bool_text(SENSITIVITY_ONLY)
        and row["chain_v51_unchanged"] == _bool_text(CHAIN_V51_UNCHANGED)
        for row in rows
    )
    checks["province_labels"] = all(
        row["evidence_label"] == EVIDENCE_LABEL
        and row["observed"] == _bool_text(OBSERVED)
        and row["policy_grade"] == _bool_text(POLICY_GRADE)
        and row["sensitivity_only"] == _bool_text(SENSITIVITY_ONLY)
        and row["chain_v51_unchanged"] == _bool_text(CHAIN_V51_UNCHANGED)
        for row in province_rows
    )
    checks["no_nan_or_negative"] = True
    try:
        _check_numeric_rows(
            rows,
            [
                "lat",
                "lon",
                "production_m3",
                "located_area_ha",
                "raw_area_ha",
                "fallback_area_ha",
                "scanfi_area_ha",
                "weight",
                "area_weight",
                "harvest_m3",
                "subcell_area_ha",
                "subcell_geometry_area_ha",
                "area_fraction",
            ],
            "subnodes",
        )
        _check_numeric_rows(
            province_rows,
            [
                "harvest_m3",
                "production_m3",
                "weight_sum",
                "hhi",
                "top_node_share",
                "area_weight_sum",
                "min_weight",
                "max_weight",
            ],
            "province rows",
        )
    except SupplyNode50kmError:
        checks["no_nan_or_negative"] = False
    checks["all_passed"] = all(checks.values())
    return {"all_passed": checks["all_passed"], "checks": checks}


# ---------------------------------------------------------------------------
# Report and manifest.
# ---------------------------------------------------------------------------

def _report(summary: Mapping[str, Any]) -> str:
    counts = summary["counts"]
    conservation = summary["conservation"]
    geometry = summary["geometry"]
    lines = [
        "# 50-km Supply-Node Sensitivity V1",
        "",
        (
            "Status: **model-estimate geometric granularity sidecar** — "
            "`evidence_label=model_estimate`, `observed=false`, "
            "`policy_grade=false`, `sensitivity_only=true`, "
            "`chain_v51_unchanged=true`. No national claim, no policy claim, "
            "and no official OSeCAN change."
        ),
        "",
        "## 1. Method",
        "",
        (
            "Each committed 150-km RSYC tile-province supply node from "
            "`supply_node_distance_v1` is split into a 3x3 grid of 50-km "
            "subcells. The tile bounding box is divided into three equal "
            "parts in x and y in EPSG:3978 metres. Each subcell is "
            "intersected with the parent tile-province geometry; positive "
            "intersections become subnodes."
        ),
        "",
        (
            "Parent `production_m3` and `harvest_m3` are allocated to "
            "subcells in proportion to the geometric subcell area inside the "
            "parent tile-province intersection. Parent production, parent "
            "harvest and provincial totals are preserved exactly. The "
            "subcell node location is the intersection centroid, replaced by "
            "`representative_point()` when the centroid falls outside."
        ),
        "",
        ("Node IDs use `<tile_id>|<province>|S<row><col>` (for example "
        "`E5|BC|S11`)."),
        "",
        "## 2. Area-proportional assumption",
        "",
        (
            "The allocation assumes uniform production density within each "
            "parent tile-province cell. This is a geometric granularity "
            "test, not new RSYC resolution: no within-tile forest mask, "
            "species map, harvest block, cutblock or truck origin is "
            "introduced. The 50-km subnode is a location for the parent "
            "volume, not an observed harvest point."
        ),
        "",
        (
            "The committed `located_area_ha` is the RSYC allocated "
            "productive area (the sum of species/group allocation rows), "
            "not the full geometric tile-province area. The build therefore "
            "validates the geometric 3x3 partition against the reconstructed "
            "tile-province geometry and carries the production area as a "
            "separate accounting quantity. It does not claim that the two "
            "areas are equal."
        ),
        "",
        "## 3. Counts and conservation",
        "",
        f"- Parent nodes: **{counts['parent_nodes']:,}**.",
        f"- 50-km subnodes: **{counts['subnodes']:,}**.",
        (f"- Positive-harvest subnodes: **{counts['positive_subnodes']:,}**; "
        f"zero-harvest subnodes: **{counts['zero_harvest_subnodes']:,}** "
        "(NU only)."),
        (f"- Subnodes per parent: min {counts['subnodes_per_parent_min']}, "
        f"max {counts['subnodes_per_parent_max']}, mean "
        f"{counts['subnodes_per_parent_mean']:.3f}."),
        (f"- Parent production: **{conservation['parent_production_m3']:,.3f} "
        f"m3**; subnode production: "
        f"**{conservation['subnode_production_m3']:,.3f} m3**; residual "
        f"{conservation['production_residual_m3']:.9f} m3."),
        (f"- Parent harvest: **{conservation['parent_harvest_m3']:,.3f} m3**; "
        f"subnode harvest: **{conservation['subnode_harvest_m3']:,.3f} m3**; "
        f"residual {conservation['harvest_residual_m3']:.9f} m3."),
        (f"- National harvest: **{conservation['national_harvest_m3']:,.3f} "
        "m3** (the committed 118,977,339.1 m3 control total)."),
        (f"- Maximum province weight-sum deviation: "
        f"{conservation['max_province_weight_sum_deviation']:.3e}."),
        "",
        "## 4. Geometry diagnostics",
        "",
        (f"- Geometric parent area total: "
        f"**{geometry['parent_geometry_area_ha_total']:,.3f} ha**."),
        (f"- Sum of subcell geometric areas: "
        f"**{geometry['subcell_geometry_area_ha_total']:,.3f} ha**; residual "
        f"{geometry['geometry_residual_ha']:.9f} ha."),
        (f"- Parents where `located_area_ha` equals the geometric area within "
        f"tolerance: **{geometry['located_area_exact_match_count']:,}** of "
        f"**{counts['parent_nodes']:,}**."),
        (f"- `located_area_ha` / geometric area ratio: min "
        f"{geometry['located_to_geometry_ratio_min']:.6f}, median "
        f"{geometry['located_to_geometry_ratio_median']:.6f}, max "
        f"{geometry['located_to_geometry_ratio_max']:.6f}."),
        "",
        (
            "The ratio is not one because the committed production area is "
            "the RSYC species/group allocation area, not the tile polygon "
            "area. The production area is validated against the RSYC "
            "library and conserved through the subcell allocation."
        ),
        "",
        "## 5. Labels and scope",
        "",
        "- `model_estimate`, `observed=false`, `policy_grade=false`.",
        ("- `national_claim=false`, `official_osecan_changed=false`, "
        "`ui_changed=false`."),
        "- `sensitivity_only=true`, `chain_v51_unchanged=true`.",
        ("- No chain v5.1, frozen artifact, UI, portal, or official OSeCAN "
        "change is made."),
        "",
        "## 6. Reproduce",
        "",
        f"`{summary['reproduce']}`",
        "",
    ]
    return "\n".join(lines)


def build(
    output_dir: Path = OUTPUT_DIR,
    *,
    deletion_checker: Callable[[], None] = assert_no_unexpected_deletions,
) -> dict[str, Any]:
    deletion_checker()
    generated_utc = _utc_now()
    parents = load_parent_nodes()
    located_area_reference = load_located_area_reference()
    tile_geometries, province_geometries = load_geometries()
    rows, diagnostics = build_subnodes(
        parents, tile_geometries, province_geometries, located_area_reference
    )
    province_rows = _province_rows(rows)
    validation = validate(
        parents=parents,
        rows=rows,
        province_rows=province_rows,
        diagnostics=diagnostics,
        located_area_reference=located_area_reference,
    )

    by_parent: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        by_parent[str(row["parent_node_id"])].append(row)
    subnode_counts = [len(group) for group in by_parent.values()]
    production_total = math.fsum(
        float(row["production_m3"]) for row in rows
    )
    harvest_total = math.fsum(float(row["harvest_m3"]) for row in rows)
    parent_production_total = math.fsum(
        parent.production_m3 for parent in parents
    )
    parent_harvest_total = math.fsum(parent.harvest_m3 for parent in parents)
    geometry_total = math.fsum(
        float(item["parent_geometry_area_ha"])
        for item in diagnostics["parents"]
    )
    subcell_geometry_total = math.fsum(
        float(row["subcell_geometry_area_ha"]) for row in rows
    )
    ratios = [
        float(item["located_to_geometry_ratio"])
        for item in diagnostics["parents"]
    ]
    weight_sums: dict[str, float] = defaultdict(float)
    for row in rows:
        weight_sums[str(row["province"])] += float(row["weight"])
    max_weight_deviation = max(
        abs(value - 1.0) for value in weight_sums.values()
    )
    positive_subnodes = sum(
        1 for row in rows if float(row["harvest_m3"]) > 0.0
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
            "national_claim": NATIONAL_CLAIM,
            "official_osecan_changed": OFFICIAL_OSECAN_CHANGED,
            "ui_changed": UI_CHANGED,
            "sensitivity_only": SENSITIVITY_ONLY,
            "chain_v51_unchanged": CHAIN_V51_UNCHANGED,
        },
        "method": {
            "parent_basis": (
                "committed supply_node_distance_v1 RSYC 150-km tile x "
                "province supply node"
            ),
            "subnode_basis": (
                "3x3 equal-area split of the tile bounding box in EPSG:3978; "
                "intersection with the parent tile-province geometry"
            ),
            "grid_size": GRID_SIZE,
            "allocation": (
                "parent production_m3 and harvest_m3 allocated by geometric "
                "subcell area fraction inside the parent tile-province "
                "intersection; parent and provincial totals preserved"
            ),
            "uniform_density_assumption": True,
            "geometry_crs": "EPSG:3978",
            "output_coordinate_crs": "EPSG:4326",
            "area_semantics": (
                "located_area_ha is the RSYC allocated productive area, not "
                "the geometric tile-province area; both are reported"
            ),
            "new_rsyc_resolution": False,
            "new_facility_or_capacity": False,
        },
        "counts": {
            "parent_nodes": len(parents),
            "subnodes": len(rows),
            "positive_subnodes": positive_subnodes,
            "zero_harvest_subnodes": len(rows) - positive_subnodes,
            "provinces": len({row["province"] for row in rows}),
            "subnodes_per_parent_min": min(subnode_counts),
            "subnodes_per_parent_max": max(subnode_counts),
            "subnodes_per_parent_mean": math.fsum(subnode_counts)
            / len(subnode_counts),
            "parents_with_nine_subnodes": sum(
                1 for count in subnode_counts if count == 9
            ),
        },
        "conservation": {
            "parent_production_m3": parent_production_total,
            "subnode_production_m3": production_total,
            "production_residual_m3": production_total - parent_production_total,
            "parent_harvest_m3": parent_harvest_total,
            "subnode_harvest_m3": harvest_total,
            "harvest_residual_m3": harvest_total - parent_harvest_total,
            "national_harvest_m3": harvest_total,
            "max_province_weight_sum_deviation": max_weight_deviation,
        },
        "geometry": {
            "parent_geometry_area_ha_total": geometry_total,
            "subcell_geometry_area_ha_total": subcell_geometry_total,
            "geometry_residual_ha": subcell_geometry_total - geometry_total,
            "located_area_exact_match_count": sum(
                1
                for item in diagnostics["parents"]
                if abs(
                    float(item["parent_located_area_ha"])
                    - float(item["parent_geometry_area_ha"])
                )
                <= GEOMETRY_AREA_TOLERANCE_HA
            ),
            "located_to_geometry_ratio_min": min(ratios),
            "located_to_geometry_ratio_median": sorted(ratios)[len(ratios) // 2],
            "located_to_geometry_ratio_max": max(ratios),
            "note": (
                "located_area_ha is the RSYC allocated productive area; it is "
                "validated against the RSYC library, not the geometric tile "
                "area"
            ),
        },
        "province_summary": province_rows,
        "validation": validation,
        "caveats": [
            (
                "The 50-km subnode is a geometric disaggregation of the "
                "committed 150-km RSYC tile-province node, not a new RSYC "
                "tile, forest inventory, or harvest observation."
            ),
            (
                "Production and harvest are allocated by subcell area, which "
                "assumes uniform production density within the parent cell. "
                "Within-parent heterogeneity remains unmodelled."
            ),
            (
                "located_area_ha is the RSYC allocated productive area, not "
                "the geometric tile-province area. The build validates the "
                "geometric partition and the production-area accounting "
                "separately."
            ),
            (
                "Node coordinates are subcell intersection centroids or "
                "representative points, not harvest blocks, cutblocks, "
                "millsheds, or observed truck origins."
            ),
            (
                "This is a sensitivity-only geometric granularity sidecar. "
                "Chain v5.1, the frozen artifacts, the UI, the portal and "
                "official OSeCAN are unchanged. No national or policy claim "
                "is made."
            ),
        ],
        "reproduce": (
            ".venv/bin/python -m "
            "workflows.national.facilities.supply_node_50km_v1"
        ),
    }
    summary["report"] = _report(summary)

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(output_dir / "supply_nodes_50km.csv", NODE_FIELDS, rows)
    _write_csv(
        output_dir / "province_node_weights_50km.csv",
        PROVINCE_FIELDS,
        province_rows,
    )
    (output_dir / "SUPPLY_NODE_50KM_V1.md").write_text(
        summary["report"] + "\n", encoding="utf-8"
    )
    _write_json(output_dir / "summary.json", summary)

    inputs = {
        str(path.relative_to(REPO_ROOT)): sn1._sha256(path)
        for path in (
            PARENT_NODES_CSV,
            RSYC_LIBRARY_CSV,
            RSYC_TILES_GPKG,
            BOUNDARY_GEOJSON,
        )
    }
    outputs: dict[str, Any] = {}
    for path in sorted(output_dir.iterdir()):
        if path.is_file() and path.name != "manifest.json":
            outputs[path.name] = {
                "bytes": path.stat().st_size,
                "sha256": sn1._sha256(path),
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
    import json

    summary = build()
    print(
        json.dumps(
            {
                "artifact": summary["artifact"],
                "parent_nodes": summary["counts"]["parent_nodes"],
                "subnodes": summary["counts"]["subnodes"],
                "positive_subnodes": summary["counts"]["positive_subnodes"],
                "national_harvest_m3": summary["conservation"][
                    "national_harvest_m3"
                ],
                "validation": summary["validation"]["all_passed"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
