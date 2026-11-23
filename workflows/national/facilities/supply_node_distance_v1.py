"""Supply-node distance v1: replace province centroids with open harvest nodes.

The v1 screening artifact builds province-specific harvest supply nodes from the
open RSYC 150-km tile production library.  Each tile x province cell that has a
geographic province intersection becomes one node at the centroid of the tile
intersection.  The node's open harvest weight is its RSYC age-100 standing
volume on the geographically located part of the cell.  The 119 Mm3 provincial
harvest case is assigned to nodes in proportion to those weights; fallback tile
area that has no province intersection is excluded rather than being given a
fabricated location.  A second SCANFI forest-area weight is carried as a
transparency and sensitivity weight only.

The artifact then re-computes the v9 assignment's distance bands, transport cost
and local-capacity accessibility at node origins, without changing the
assignment.  Province-centroid results are retained as the comparison baseline.
A paired sample of public OSRM demo routes from the highest-weight node in each
province to the audit-v2 facility sample is used to test whether the province
road factors transfer from centroids to node origins.  No paid routing key is
used.

Labels: ``model_estimate`` / ``observed=false`` / ``policy_grade=false``.  No
national claim, no policy claim, no greenfield optimization, no assignment
change, and no UI or official OSeCAN surface is changed.  A ``D`` in either
status column of the parent or data repository git status halts the build before
any output is written.
"""

from __future__ import annotations

import bisect
import csv
import hashlib
import json
import math
import subprocess
import time
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import geopandas as gpd
from shapely.validation import make_valid

from workflows.national.facilities import facility_road_distance_audit as audit_v1
from workflows.national.facilities import transport_cost_v1 as transport
from workflows.national.facilities.national_facility_assignment import (
    haversine_km,
    load_province_centroids,
)
from workflows.national.facilities.national_facility_inventory import (
    PROVINCES,
    SHAPE_NAME_TO_CODE,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_ROOT = REPO_ROOT / "data"
OUTPUT_DIR = DATA_ROOT / "derived/national/facilities/supply_node_distance_v1"

RSYC_LIBRARY_CSV = (
    DATA_ROOT
    / "derived/national/assortments/rsyc_tile_production"
    / "rsyc_tile_production_library.csv"
)
RSYC_TILES_GPKG = DATA_ROOT / "raw/national/rsyc/RSYC_tiles.gpkg"
TILE_ECODISTRICT_PROVINCE_CSV = (
    DATA_ROOT
    / "derived/national/assortments/rsyc_tile_production"
    / "tile_ecodistrict_province_weights.csv"
)
SCANFI_ECODISTRICT_CSV = (
    DATA_ROOT
    / "derived/national/assortments/national_area_weights"
    / "national_area_weights_ecodistrict.csv"
)
BOUNDARY_GEOJSON = (
    DATA_ROOT / "raw/boundaries/canada_adm1_geoboundaries_simplified.geojson"
)
HARVEST_CSV = (
    DATA_ROOT / "derived/national/harvest_basis/provincial_allocation/119m.csv"
)
CAPACITY_CSV = (
    DATA_ROOT
    / "derived/national/facilities/national_facility_capacity_v3"
    / "national_facility_capacity_v3.csv"
)
FACTORS_CSV = (
    DATA_ROOT
    / "derived/national/facilities/facility_road_distance_audit_v2"
    / "province_road_factors.csv"
)
V9_ASSIGNMENT_CSV = (
    DATA_ROOT
    / "derived/national/facilities/national_facility_assignment_v9"
    / "v9_facility_assignment.csv"
)
AUDIT_V2_SAMPLE_CSV = (
    DATA_ROOT
    / "derived/national/facilities/facility_road_distance_audit_v2"
    / "facility_road_distance_sample.csv"
)
TRANSPORT_PARAMETERS_CSV = (
    DATA_ROOT
    / "derived/national/facilities/transport_cost_v1"
    / "parameters.csv"
)

ARTIFACT = "supply_node_distance_v1"
ARTIFACT_VERSION = "1.0.0"
PRIMARY_SCENARIO = "province_capacity_floor"

EVIDENCE_LABEL = "model_estimate"
OBSERVED = False
POLICY_GRADE = False
ASSIGNMENT_CHANGE = False
NATIONAL_CLAIM = False
OFFICIAL_OSECAN_CHANGED = False
UI_CHANGED = False
SITING_OPTIMIZATION = False
GREENFIELD_OPTIMIZATION = False
POLICY_CLAIM = False

RELEVANT_CLASSES = ("SAW", "PULP", "PANEL", "PELLET", "BIO")
CLASS_ORDER = tuple(RELEVANT_CLASSES)
PROVINCE_ORDER = tuple(PROVINCES)
NON_OPERATING_STATUSES = frozenset(
    {"abandoned", "construction", "disused", "proposed", "temporarily_closed"}
)

BAND_ORDER = ("0-50", "50-100", "100-150", "150-300", ">300")
BANDS = (
    ("0-50", 0.0, 50.0),
    ("50-100", 50.0, 100.0),
    ("100-150", 100.0, 150.0),
    ("150-300", 150.0, 300.0),
    (">300", 300.0, None),
)
BANDS_BY_LABEL = {
    label: (low, high) for label, low, high in BANDS
}
VOLUME_TOLERANCE_M3 = 1e-3
CAPACITY_TOLERANCE_M3 = 1e-3
WEIGHT_TOLERANCE = 1e-9
PAYLOAD_M3 = 52.5
DENSITY_ODT_PER_M3 = 0.22
LHV_GJ_PER_ODT = 18.5
ENERGY_GJ_PER_M3 = DENSITY_ODT_PER_M3 * LHV_GJ_PER_ODT
LOCALIZATION_REFERENCE_KM = 50.0
RATE_LIMIT_SECONDS = audit_v1.RATE_LIMIT_SECONDS
SNAP_WARNING_KM = audit_v1.SNAP_WARNING_KM
OSRM_PROVIDER = "OSRM public demo server"
OSRM_BASE_URL = audit_v1.OSRM_BASE_URL
OSRM_PROFILE = audit_v1.OSRM_PROFILE
OSRM_LICENCE = audit_v1.OSRM_LICENCE


class SupplyNodeDistanceError(RuntimeError):
    """Raised when a supply-node distance v1 invariant fails."""


@dataclass(frozen=True, slots=True)
class RawNode:
    tile_id: str
    province: str
    production_m3: float
    located_area_ha: float
    raw_area_ha: float
    fallback_area_ha: float


@dataclass(frozen=True, slots=True)
class SupplyNode:
    node_id: str
    tile_id: str
    province: str
    lat: float
    lon: float
    centroid_method: str
    production_m3: float
    located_area_ha: float
    raw_area_ha: float
    fallback_area_ha: float
    scanfi_area_ha: float
    weight: float
    area_weight: float
    province_harvest_m3: float
    harvest_m3: float
    province_production_m3: float
    province_scanfi_area_ha: float


@dataclass(frozen=True, slots=True)
class Facility:
    facility_id: str
    name: str
    facility_class: str
    province: str
    lat: float
    lon: float
    capacity_m3_y: float
    status: str


@dataclass(frozen=True, slots=True)
class AssignmentRow:
    province: str
    facility_id: str
    facility_class: str
    facility_province: str
    assigned_m3: float
    v9_distance_km: float
    v9_road_distance_km: float
    centroid_haversine_km: float
    centroid_road_km: float


@dataclass(frozen=True, slots=True)
class Distribution:
    scope: str
    origin_basis: str
    n_pairs: int
    n_origins: int
    n_facilities: int
    mean_km: float
    minimum_km: float
    p10_km: float
    p25_km: float
    median_km: float
    p75_km: float
    p90_km: float
    maximum_km: float


# ---------------------------------------------------------------------------
# I/O and fail-fast helpers.
# ---------------------------------------------------------------------------

def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_csv(path: Path, role: str) -> list[dict[str, str]]:
    if not path.is_file():
        raise SupplyNodeDistanceError(f"{role} is missing: {path}")
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
    except OSError as error:
        raise SupplyNodeDistanceError(f"cannot read {role} {path}: {error}") from error
    if not rows:
        raise SupplyNodeDistanceError(f"{role} has no data rows: {path}")
    return rows


def _require_columns(
    rows: Sequence[dict[str, str]], required: set[str], path: Path
) -> None:
    missing = required - set(rows[0])
    if missing:
        raise SupplyNodeDistanceError(
            f"{path}: missing required columns {sorted(missing)}"
        )


def _finite_float(value: object, *, field: str, context: str) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as error:
        raise SupplyNodeDistanceError(
            f"{context}: {field} is not numeric: {value!r}"
        ) from error
    if not math.isfinite(number):
        raise SupplyNodeDistanceError(
            f"{context}: {field} is not finite: {value!r}"
        )
    return number


def _nonnegative_float(value: object, *, field: str, context: str) -> float:
    number = _finite_float(value, field=field, context=context)
    if number < 0.0:
        raise SupplyNodeDistanceError(
            f"{context}: {field} is negative: {number!r}"
        )
    return number


def _positive_float(value: object, *, field: str, context: str) -> float:
    number = _finite_float(value, field=field, context=context)
    if number <= 0.0:
        raise SupplyNodeDistanceError(
            f"{context}: {field} must be positive: {number!r}"
        )
    return number


def _format_float(value: float | None, digits: int = 9) -> str:
    if value is None:
        return ""
    if not math.isfinite(value):
        raise SupplyNodeDistanceError(f"refusing to write non-finite value {value!r}")
    if abs(value) < 0.5 * 10**-digits:
        return "0"
    return f"{value:.{digits}f}".rstrip("0").rstrip(".")


def _bool_text(value: bool) -> str:
    return "true" if value else "false"


def _write_csv(
    path: Path, fieldnames: Sequence[str], rows: Iterable[Mapping[str, Any]]
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=fieldnames, extrasaction="ignore", lineterminator="\n"
        )
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")


def parse_deletion_lines(status_text: str) -> list[str]:
    """Return porcelain lines with a ``D`` in either status column."""

    return [
        line
        for line in status_text.splitlines()
        if len(line) >= 2 and (line[0] == "D" or line[1] == "D")
    ]


def _git_status(repository: Path) -> str:
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repository,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise SupplyNodeDistanceError(
            f"cannot inspect git status in {repository}: {result.stderr.strip()}"
        )
    return result.stdout


def assert_no_unexpected_deletions(
    repositories: Sequence[Path] = (REPO_ROOT, DATA_ROOT),
    *,
    status_reader: Callable[[Path], str] = _git_status,
) -> None:
    """Fail closed on any ``D`` in the parent or data repository status."""

    incidents: list[str] = []
    for repository in repositories:
        for line in parse_deletion_lines(status_reader(repository)):
            incidents.append(f"{repository.name or repository}: {line}")
    if incidents:
        raise SupplyNodeDistanceError(
            "unexpected deletion status detected; halting: " + " | ".join(incidents)
        )


# ---------------------------------------------------------------------------
# Node construction.
# ---------------------------------------------------------------------------

def load_harvest() -> tuple[dict[str, float], float]:
    rows = _read_csv(HARVEST_CSV, "119 Mm3 provincial harvest")
    _require_columns(rows, {"province", "harvest_m3"}, HARVEST_CSV)
    harvest: dict[str, float] = {}
    for position, row in enumerate(rows, start=2):
        province = row["province"].strip().upper()
        if province not in PROVINCE_ORDER:
            raise SupplyNodeDistanceError(
                f"{HARVEST_CSV}:{position}: unexpected province {province!r}"
            )
        if province in harvest:
            raise SupplyNodeDistanceError(
                f"{HARVEST_CSV}:{position}: duplicate province {province!r}"
            )
        harvest[province] = _nonnegative_float(
            row["harvest_m3"],
            field="harvest_m3",
            context=f"{HARVEST_CSV}:{position}",
        )
    missing = set(PROVINCE_ORDER) - set(harvest)
    if missing:
        raise SupplyNodeDistanceError(
            f"{HARVEST_CSV}: missing provinces {sorted(missing)}"
        )
    total = math.fsum(harvest.values())
    if total <= 0.0:
        raise SupplyNodeDistanceError("119 Mm3 harvest total must be positive")
    return harvest, total


def load_rsyc_nodes() -> tuple[tuple[RawNode, ...], dict[str, Any]]:
    rows = _read_csv(RSYC_LIBRARY_CSV, "RSYC tile production library")
    _require_columns(
        rows,
        {
            "tile_id",
            "province",
            "area_ha",
            "volume_m3ha_age100",
            "no_province_intersection_area_ha",
            "allocation_basis",
        },
        RSYC_LIBRARY_CSV,
    )
    grouped: dict[tuple[str, str], dict[str, float]] = defaultdict(
        lambda: {
            "production_m3": 0.0,
            "located_area_ha": 0.0,
            "raw_area_ha": 0.0,
            "fallback_area_ha": 0.0,
            "raw_production_m3": 0.0,
        }
    )
    province_raw_production: dict[str, float] = defaultdict(float)
    province_fallback_production: dict[str, float] = defaultdict(float)
    province_raw_area: dict[str, float] = defaultdict(float)
    province_fallback_area: dict[str, float] = defaultdict(float)
    for position, row in enumerate(rows, start=2):
        context = f"{RSYC_LIBRARY_CSV}:{position}"
        tile_id = row["tile_id"].strip()
        province = row["province"].strip().upper()
        if not tile_id:
            raise SupplyNodeDistanceError(f"{context}: blank tile_id")
        if province not in PROVINCE_ORDER:
            raise SupplyNodeDistanceError(
                f"{context}: unexpected province {province!r}"
            )
        area_ha = _nonnegative_float(row["area_ha"], field="area_ha", context=context)
        fallback_area_ha = _nonnegative_float(
            row["no_province_intersection_area_ha"],
            field="no_province_intersection_area_ha",
            context=context,
        )
        if fallback_area_ha > area_ha + 1e-6:
            raise SupplyNodeDistanceError(
                f"{context}: fallback area exceeds total area"
            )
        located_area_ha = max(0.0, area_ha - fallback_area_ha)
        volume_per_ha = _nonnegative_float(
            row["volume_m3ha_age100"],
            field="volume_m3ha_age100",
            context=context,
        )
        production_m3 = volume_per_ha * located_area_ha
        if not math.isfinite(production_m3) or production_m3 < 0.0:
            raise SupplyNodeDistanceError(
                f"{context}: invalid located production {production_m3!r}"
            )
        raw_production_m3 = volume_per_ha * area_ha
        fallback_production_m3 = raw_production_m3 - production_m3
        key = (tile_id, province)
        bucket = grouped[key]
        bucket["production_m3"] += production_m3
        bucket["located_area_ha"] += located_area_ha
        bucket["raw_area_ha"] += area_ha
        bucket["fallback_area_ha"] += fallback_area_ha
        bucket["raw_production_m3"] += raw_production_m3
        province_raw_production[province] += raw_production_m3
        province_fallback_production[province] += fallback_production_m3
        province_raw_area[province] += area_ha
        province_fallback_area[province] += fallback_area_ha

    nodes: list[RawNode] = []
    for (tile_id, province), bucket in grouped.items():
        if bucket["production_m3"] <= 0.0:
            continue
        nodes.append(
            RawNode(
                tile_id=tile_id,
                province=province,
                production_m3=bucket["production_m3"],
                located_area_ha=bucket["located_area_ha"],
                raw_area_ha=bucket["raw_area_ha"],
                fallback_area_ha=bucket["fallback_area_ha"],
            )
        )
    if not nodes:
        raise SupplyNodeDistanceError("no geographically located RSYC nodes")
    total_raw_production = math.fsum(province_raw_production.values())
    total_fallback_production = math.fsum(
        province_fallback_production.values()
    )
    diagnostics = {
        "source_rows": len(rows),
        "node_candidates": len(nodes),
        "located_production_m3": math.fsum(
            node.production_m3 for node in nodes
        ),
        "raw_production_m3": total_raw_production,
        "fallback_production_m3": total_fallback_production,
        "fallback_production_share": (
            total_fallback_production / total_raw_production
            if total_raw_production > 0.0
            else 0.0
        ),
        "raw_area_ha": math.fsum(province_raw_area.values()),
        "fallback_area_ha": math.fsum(province_fallback_area.values()),
        "fallback_area_share": (
            math.fsum(province_fallback_area.values())
            / math.fsum(province_raw_area.values())
            if math.fsum(province_raw_area.values()) > 0.0
            else 0.0
        ),
        "province_fallback_production_m3": {
            province: province_fallback_production[province]
            for province in PROVINCE_ORDER
            if province_fallback_production.get(province, 0.0) > 0.0
        },
        "province_fallback_production_share": {
            province: (
                province_fallback_production[province]
                / province_raw_production[province]
                if province_raw_production.get(province, 0.0) > 0.0
                else 0.0
            )
            for province in PROVINCE_ORDER
            if province_fallback_production.get(province, 0.0) > 0.0
        },
    }
    return (
        tuple(sorted(nodes, key=lambda node: (node.province, node.tile_id))),
        diagnostics,
    )


def load_scanfi_area_by_node() -> dict[tuple[str, str], float]:
    tile_rows = _read_csv(
        TILE_ECODISTRICT_PROVINCE_CSV, "tile x ecodistrict x province weights"
    )
    _require_columns(
        tile_rows,
        {
            "tile_id",
            "ecodistrict_id",
            "province",
            "intersection_area_ha",
            "district_province_covered_area_ha",
        },
        TILE_ECODISTRICT_PROVINCE_CSV,
    )
    area_rows = _read_csv(SCANFI_ECODISTRICT_CSV, "SCANFI ecodistrict area weights")
    _require_columns(
        area_rows,
        {"ecodistrict_id", "province", "area_ha"},
        SCANFI_ECODISTRICT_CSV,
    )
    district_area: dict[tuple[str, str], float] = defaultdict(float)
    for position, row in enumerate(area_rows, start=2):
        ecodistrict_id = row["ecodistrict_id"].strip()
        province = row["province"].strip().upper()
        if not ecodistrict_id:
            raise SupplyNodeDistanceError(
                f"{SCANFI_ECODISTRICT_CSV}:{position}: blank ecodistrict_id"
            )
        if province not in PROVINCE_ORDER:
            raise SupplyNodeDistanceError(
                f"{SCANFI_ECODISTRICT_CSV}:{position}: unexpected province "
                f"{province!r}"
            )
        district_area[(ecodistrict_id, province)] += _nonnegative_float(
            row["area_ha"],
            field="area_ha",
            context=f"{SCANFI_ECODISTRICT_CSV}:{position}",
        )
    if not district_area:
        raise SupplyNodeDistanceError("SCANFI ecodistrict area table is empty")

    node_area: dict[tuple[str, str], float] = defaultdict(float)
    unmatched_pairs = 0
    for position, row in enumerate(tile_rows, start=2):
        context = f"{TILE_ECODISTRICT_PROVINCE_CSV}:{position}"
        tile_id = row["tile_id"].strip()
        province = row["province"].strip().upper()
        ecodistrict_id = row["ecodistrict_id"].strip()
        if not tile_id or not ecodistrict_id:
            raise SupplyNodeDistanceError(f"{context}: blank tile or ecodistrict")
        if province not in PROVINCE_ORDER:
            raise SupplyNodeDistanceError(
                f"{context}: unexpected province {province!r}"
            )
        covered_area_ha = _positive_float(
            row["district_province_covered_area_ha"],
            field="district_province_covered_area_ha",
            context=context,
        )
        intersection_area_ha = _nonnegative_float(
            row["intersection_area_ha"],
            field="intersection_area_ha",
            context=context,
        )
        fraction = intersection_area_ha / covered_area_ha
        if fraction > 1.0 + 1e-9:
            raise SupplyNodeDistanceError(
                f"{context}: intersection exceeds district-province coverage"
            )
        district_area_ha = district_area.get((ecodistrict_id, province), 0.0)
        if district_area_ha <= 0.0:
            unmatched_pairs += 1
            continue
        node_area[(tile_id, province)] += district_area_ha * min(1.0, fraction)
    if unmatched_pairs > 3:
        raise SupplyNodeDistanceError(
            f"too many unmatched SCANFI district-province pairs: {unmatched_pairs}"
        )
    return dict(node_area)


def load_geometries() -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, str],
]:
    tiles = gpd.read_file(RSYC_TILES_GPKG, layer="RSYC_tiles")
    if tiles.crs is None or tiles.crs.to_epsg() != 3978:
        raise SupplyNodeDistanceError(
            f"{RSYC_TILES_GPKG}: expected EPSG:3978, got {tiles.crs}"
        )
    if "TileID" not in tiles.columns:
        raise SupplyNodeDistanceError(f"{RSYC_TILES_GPKG}: missing TileID")
    tile_geometries = {
        str(row.TileID): make_valid(row.geometry) for row in tiles.itertuples()
    }
    if len(tile_geometries) != len(tiles):
        raise SupplyNodeDistanceError("duplicate TileID in RSYC tile layer")

    provinces = gpd.read_file(BOUNDARY_GEOJSON).to_crs(3978)
    province_geometries: dict[str, Any] = {}
    for row in provinces.itertuples():
        code = SHAPE_NAME_TO_CODE.get(str(row.shapeName))
        if code is None:
            raise SupplyNodeDistanceError(
                f"{BOUNDARY_GEOJSON}: unrecognised shapeName {row.shapeName!r}"
            )
        if code in province_geometries:
            raise SupplyNodeDistanceError(
                f"{BOUNDARY_GEOJSON}: duplicate province {code!r}"
            )
        province_geometries[code] = make_valid(row.geometry)
    missing = set(PROVINCE_ORDER) - set(province_geometries)
    if missing:
        raise SupplyNodeDistanceError(
            f"{BOUNDARY_GEOJSON}: missing province geometries {sorted(missing)}"
        )
    centroids, methods = load_province_centroids(BOUNDARY_GEOJSON)
    return tile_geometries, province_geometries, centroids, methods


def build_supply_nodes() -> tuple[
    tuple[SupplyNode, ...],
    dict[str, float],
    dict[str, Any],
    dict[str, str],
    dict[str, Any],
]:
    harvest, _ = load_harvest()
    raw_nodes, rsyc_diagnostics = load_rsyc_nodes()
    scanfi_by_node = load_scanfi_area_by_node()
    tile_geometries, province_geometries, centroids, centroid_methods = (
        load_geometries()
    )

    production_by_province: dict[str, float] = defaultdict(float)
    for node in raw_nodes:
        production_by_province[node.province] += node.production_m3
    for province, volume in harvest.items():
        if volume > 0.0 and production_by_province.get(province, 0.0) <= 0.0:
            raise SupplyNodeDistanceError(
                f"{province}: positive harvest but no located RSYC production"
            )

    scanfi_by_province: dict[str, float] = defaultdict(float)
    for raw in raw_nodes:
        scanfi_by_province[raw.province] += scanfi_by_node.get(
            (raw.tile_id, raw.province), 0.0
        )
    for province, volume in harvest.items():
        if volume > 0.0 and scanfi_by_province.get(province, 0.0) <= 0.0:
            raise SupplyNodeDistanceError(
                f"{province}: positive harvest but no SCANFI area weight"
            )

    points: list[Any] = []
    node_fields: list[dict[str, Any]] = []
    for raw in raw_nodes:
        tile_geometry = tile_geometries.get(raw.tile_id)
        province_geometry = province_geometries.get(raw.province)
        if tile_geometry is None:
            raise SupplyNodeDistanceError(
                f"{raw.tile_id}: no RSYC tile geometry"
            )
        if province_geometry is None:
            raise SupplyNodeDistanceError(
                f"{raw.province}: no province geometry"
            )
        intersection = tile_geometry.intersection(province_geometry)
        if intersection.is_empty or intersection.area <= 0.0:
            raise SupplyNodeDistanceError(
                f"{raw.tile_id}|{raw.province}: located production has no "
                "province intersection"
            )
        point = intersection.centroid
        method = "tile_province_intersection_centroid"
        if not intersection.covers(point):
            point = intersection.representative_point()
            method = "tile_province_intersection_representative_point"
        points.append(point)
        node_fields.append(
            {
                "tile_id": raw.tile_id,
                "province": raw.province,
                "production_m3": raw.production_m3,
                "located_area_ha": raw.located_area_ha,
                "raw_area_ha": raw.raw_area_ha,
                "fallback_area_ha": raw.fallback_area_ha,
                "scanfi_area_ha": scanfi_by_node.get(
                    (raw.tile_id, raw.province), 0.0
                ),
                "method": method,
            }
        )

    points_wgs84 = gpd.GeoSeries(points, crs=3978).to_crs(4326)
    nodes: list[SupplyNode] = []
    for fields, point_series in zip(node_fields, points_wgs84, strict=True):
        point = point_series
        province = fields["province"]
        province_production = production_by_province[province]
        province_scanfi = scanfi_by_province[province]
        weight = fields["production_m3"] / province_production
        area_weight = (
            fields["scanfi_area_ha"] / province_scanfi
            if province_scanfi > 0.0
            else 0.0
        )
        province_harvest = harvest[province]
        nodes.append(
            SupplyNode(
                node_id=f"{fields['tile_id']}|{province}",
                tile_id=fields["tile_id"],
                province=province,
                lat=float(point.y),
                lon=float(point.x),
                centroid_method=fields["method"],
                production_m3=fields["production_m3"],
                located_area_ha=fields["located_area_ha"],
                raw_area_ha=fields["raw_area_ha"],
                fallback_area_ha=fields["fallback_area_ha"],
                scanfi_area_ha=fields["scanfi_area_ha"],
                weight=weight,
                area_weight=area_weight,
                province_harvest_m3=province_harvest,
                harvest_m3=province_harvest * weight,
                province_production_m3=province_production,
                province_scanfi_area_ha=province_scanfi,
            )
        )
    nodes.sort(key=lambda node: (PROVINCE_ORDER.index(node.province), node.tile_id))
    return tuple(nodes), harvest, centroids, centroid_methods, rsyc_diagnostics


# ---------------------------------------------------------------------------
# Facilities, road factors and the unchanged v9 assignment.
# ---------------------------------------------------------------------------

def load_facilities() -> tuple[Facility, ...]:
    rows = _read_csv(CAPACITY_CSV, "national facility capacity v3")
    _require_columns(
        rows,
        {
            "facility_id",
            "name",
            "facility_class",
            "province",
            "lat",
            "lon",
            "capacity_m3_y",
            "status",
        },
        CAPACITY_CSV,
    )
    facilities: list[Facility] = []
    seen: set[str] = set()
    for position, row in enumerate(rows, start=2):
        context = f"{CAPACITY_CSV}:{position}"
        facility_id = row["facility_id"].strip()
        if not facility_id:
            raise SupplyNodeDistanceError(f"{context}: blank facility_id")
        if facility_id in seen:
            raise SupplyNodeDistanceError(
                f"{context}: duplicate facility_id {facility_id!r}"
            )
        seen.add(facility_id)
        province = row["province"].strip().upper()
        if province not in PROVINCE_ORDER:
            raise SupplyNodeDistanceError(
                f"{context}: unexpected province {province!r}"
            )
        facility_class = row["facility_class"].strip().upper()
        if facility_class not in CLASS_ORDER + ("OTHER",):
            raise SupplyNodeDistanceError(
                f"{context}: unexpected facility_class {facility_class!r}"
            )
        status = row["status"].strip().lower()
        if facility_class not in CLASS_ORDER or status in NON_OPERATING_STATUSES:
            continue
        latitude_text = row["lat"].strip()
        longitude_text = row["lon"].strip()
        if bool(latitude_text) != bool(longitude_text):
            raise SupplyNodeDistanceError(
                f"{context}: facility has only one coordinate"
            )
        if not latitude_text:
            continue
        latitude = _finite_float(latitude_text, field="lat", context=context)
        longitude = _finite_float(longitude_text, field="lon", context=context)
        if not -90.0 <= latitude <= 90.0:
            raise SupplyNodeDistanceError(
                f"{context}: latitude outside [-90, 90]: {latitude!r}"
            )
        if not -180.0 <= longitude <= 180.0:
            raise SupplyNodeDistanceError(
                f"{context}: longitude outside [-180, 180]: {longitude!r}"
            )
        capacity = _nonnegative_float(
            row["capacity_m3_y"], field="capacity_m3_y", context=context
        )
        facilities.append(
            Facility(
                facility_id=facility_id,
                name=row["name"].strip(),
                facility_class=facility_class,
                province=province,
                lat=latitude,
                lon=longitude,
                capacity_m3_y=capacity,
                status=status,
            )
        )
    if not facilities:
        raise SupplyNodeDistanceError("no eligible located facilities")
    facilities.sort(key=lambda f: (PROVINCE_ORDER.index(f.province), f.facility_id))
    return tuple(facilities)


def load_factors() -> dict[str, float]:
    rows = _read_csv(FACTORS_CSV, "province road factors")
    _require_columns(rows, {"province", "factor_applied"}, FACTORS_CSV)
    factors: dict[str, float] = {}
    for position, row in enumerate(rows, start=2):
        province = row["province"].strip().upper()
        if province not in PROVINCE_ORDER:
            raise SupplyNodeDistanceError(
                f"{FACTORS_CSV}:{position}: unexpected province {province!r}"
            )
        value = row["factor_applied"].strip()
        if not value:
            continue
        factors[province] = _positive_float(
            value,
            field="factor_applied",
            context=f"{FACTORS_CSV}:{position}",
        )
    if not factors:
        raise SupplyNodeDistanceError("no province road factors")
    return factors


def _facility_distance_km(
    origin_lat: float,
    origin_lon: float,
    facility: Facility,
    factors: Mapping[str, float],
) -> float:
    try:
        factor = factors[facility.province]
    except KeyError as error:
        raise SupplyNodeDistanceError(
            f"{facility.facility_id}: no road factor for {facility.province}"
        ) from error
    haversine = haversine_km(
        origin_lat, origin_lon, facility.lat, facility.lon
    )
    distance = haversine * factor
    if not math.isfinite(distance) or distance < 0.0:
        raise SupplyNodeDistanceError(
            f"{facility.facility_id}: invalid road distance {distance!r}"
        )
    return distance


def load_assignments(
    facilities: Sequence[Facility],
    factors: Mapping[str, float],
    harvest: Mapping[str, float],
    centroids: Mapping[str, Any],
) -> tuple[AssignmentRow, ...]:
    rows = _read_csv(V9_ASSIGNMENT_CSV, "v9 facility assignment")
    _require_columns(
        rows,
        {
            "scenario",
            "province",
            "facility_id",
            "facility_class",
            "facility_province",
            "assigned_m3",
            "distance_km",
            "road_distance_km",
        },
        V9_ASSIGNMENT_CSV,
    )
    facilities_by_id = {facility.facility_id: facility for facility in facilities}
    assignments: list[AssignmentRow] = []
    assigned_by_province: dict[str, float] = defaultdict(float)
    for position, row in enumerate(rows, start=2):
        context = f"{V9_ASSIGNMENT_CSV}:{position}"
        if row["scenario"].strip() != PRIMARY_SCENARIO:
            continue
        province = row["province"].strip().upper()
        if province not in PROVINCE_ORDER:
            raise SupplyNodeDistanceError(
                f"{context}: unexpected province {province!r}"
            )
        facility_id = row["facility_id"].strip()
        facility = facilities_by_id.get(facility_id)
        if facility is None:
            raise SupplyNodeDistanceError(
                f"{context}: assigned facility {facility_id!r} is not an eligible "
                "located facility"
            )
        facility_province = row["facility_province"].strip().upper()
        if facility_province != facility.province:
            raise SupplyNodeDistanceError(
                f"{context}: facility province mismatch for {facility_id!r}"
            )
        facility_class = row["facility_class"].strip().upper()
        if facility_class != facility.facility_class:
            raise SupplyNodeDistanceError(
                f"{context}: facility class mismatch for {facility_id!r}"
            )
        assigned = _nonnegative_float(
            row["assigned_m3"], field="assigned_m3", context=context
        )
        if assigned <= 0.0:
            continue
        centroid = centroids[province]
        centroid_haversine = haversine_km(
            centroid.lat, centroid.lon, facility.lat, facility.lon
        )
        centroid_road = _facility_distance_km(
            centroid.lat, centroid.lon, facility, factors
        )
        v9_distance = _nonnegative_float(
            row["distance_km"], field="distance_km", context=context
        )
        v9_road = _nonnegative_float(
            row["road_distance_km"], field="road_distance_km", context=context
        )
        if abs(centroid_haversine - v9_distance) > 1e-4:
            raise SupplyNodeDistanceError(
                f"{context}: centroid haversine {centroid_haversine!r} does not "
                f"match v9 distance {v9_distance!r}"
            )
        if abs(centroid_road - v9_road) > 1e-3:
            raise SupplyNodeDistanceError(
                f"{context}: centroid road distance {centroid_road!r} does not "
                f"match v9 road distance {v9_road!r}"
            )
        assignments.append(
            AssignmentRow(
                province=province,
                facility_id=facility_id,
                facility_class=facility_class,
                facility_province=facility_province,
                assigned_m3=assigned,
                v9_distance_km=v9_distance,
                v9_road_distance_km=v9_road,
                centroid_haversine_km=centroid_haversine,
                centroid_road_km=centroid_road,
            )
        )
        assigned_by_province[province] += assigned
    if not assignments:
        raise SupplyNodeDistanceError("no primary-scenario assignment rows")
    for province, assigned in assigned_by_province.items():
        expected = harvest[province]
        if abs(assigned - expected) > VOLUME_TOLERANCE_M3:
            raise SupplyNodeDistanceError(
                f"{province}: assigned volume {assigned!r} does not match harvest "
                f"{expected!r}"
            )
    total_assigned = math.fsum(row.assigned_m3 for row in assignments)
    total_harvest = math.fsum(harvest.values())
    if abs(total_assigned - total_harvest) > VOLUME_TOLERANCE_M3:
        raise SupplyNodeDistanceError(
            f"assigned total {total_assigned!r} does not match harvest total "
            f"{total_harvest!r}"
        )
    return tuple(assignments)


# ---------------------------------------------------------------------------
# Distance distributions.
# ---------------------------------------------------------------------------

def band_for_distance(distance_km: float) -> str:
    if not math.isfinite(distance_km) or distance_km < 0.0:
        raise SupplyNodeDistanceError(
            f"road distance must be finite and non-negative; got {distance_km!r}"
        )
    for label, low, high in BANDS:
        if distance_km < low:
            continue
        if high is None or distance_km < high:
            return label
    raise SupplyNodeDistanceError(
        f"distance outside defined bands: {distance_km!r}"
    )


def weighted_quantile(
    values: Sequence[float], weights: Sequence[float], fraction: float
) -> float:
    """Return a deterministic weighted quantile (lower-step convention)."""

    if not values or len(values) != len(weights):
        raise SupplyNodeDistanceError("weighted quantile requires paired values")
    if not 0.0 <= fraction <= 1.0:
        raise SupplyNodeDistanceError(
            f"quantile fraction outside [0, 1]: {fraction!r}"
        )
    pairs = sorted(zip(values, weights, strict=True))
    total_weight = math.fsum(weight for _, weight in pairs)
    if total_weight <= 0.0:
        raise SupplyNodeDistanceError("weighted quantile total weight is not positive")
    if fraction <= 0.0:
        return pairs[0][0]
    if fraction >= 1.0:
        return pairs[-1][0]
    target = fraction * total_weight
    cumulative = 0.0
    for value, weight in pairs:
        if not math.isfinite(value) or not math.isfinite(weight) or weight < 0.0:
            raise SupplyNodeDistanceError("invalid value or weight in quantile")
        cumulative += weight
        if cumulative >= target:
            return value
    return pairs[-1][0]


def _distribution_stats(
    *,
    scope: str,
    origin_basis: str,
    values: Sequence[float],
    weights: Sequence[float],
    n_origins: int,
    n_facilities: int,
) -> Distribution:
    if not values:
        raise SupplyNodeDistanceError(f"{scope}/{origin_basis}: no distances")
    if any(not math.isfinite(value) or value < 0.0 for value in values):
        raise SupplyNodeDistanceError(
            f"{scope}/{origin_basis}: invalid distance"
        )
    total_weight = math.fsum(weights)
    if total_weight <= 0.0:
        raise SupplyNodeDistanceError(
            f"{scope}/{origin_basis}: total weight is not positive"
        )
    mean = math.fsum(
        value * weight for value, weight in zip(values, weights, strict=True)
    ) / total_weight
    return Distribution(
        scope=scope,
        origin_basis=origin_basis,
        n_pairs=len(values),
        n_origins=n_origins,
        n_facilities=n_facilities,
        mean_km=mean,
        minimum_km=min(values),
        p10_km=weighted_quantile(values, weights, 0.10),
        p25_km=weighted_quantile(values, weights, 0.25),
        median_km=weighted_quantile(values, weights, 0.50),
        p75_km=weighted_quantile(values, weights, 0.75),
        p90_km=weighted_quantile(values, weights, 0.90),
        maximum_km=max(values),
    )


def _weighted_cdf_many(
    values: Sequence[float], weights: Sequence[float], points: Sequence[float]
) -> list[float]:
    """Return weighted CDF values at ``points`` with one sort."""

    if not values or len(values) != len(weights):
        raise SupplyNodeDistanceError("weighted CDF requires paired values")
    pairs = sorted(zip(values, weights, strict=True))
    sorted_values = [value for value, _ in pairs]
    cumulative: list[float] = []
    running = 0.0
    for _, weight in pairs:
        if not math.isfinite(weight) or weight < 0.0:
            raise SupplyNodeDistanceError("invalid weight in weighted CDF")
        running += weight
        cumulative.append(running)
    total = running
    if total <= 0.0:
        raise SupplyNodeDistanceError("weighted CDF total weight is not positive")
    result: list[float] = []
    for point in points:
        index = bisect.bisect_right(sorted_values, point)
        below = cumulative[index - 1] if index > 0 else 0.0
        result.append(below / total)
    return result


def build_distance_distributions(
    nodes: Sequence[SupplyNode],
    facilities: Sequence[Facility],
    factors: Mapping[str, float],
    centroids: Mapping[str, Any],
    harvest: Mapping[str, float],
) -> tuple[list[Distribution], list[dict[str, Any]]]:
    nodes_by_province: dict[str, list[SupplyNode]] = defaultdict(list)
    facilities_by_province: dict[str, list[Facility]] = defaultdict(list)
    for node in nodes:
        nodes_by_province[node.province].append(node)
    for facility in facilities:
        facilities_by_province[facility.province].append(facility)

    distributions: list[Distribution] = []
    percentiles: list[dict[str, Any]] = []
    national_node_values: list[float] = []
    national_node_weights: list[float] = []
    national_node_unit_weights: list[float] = []
    national_centroid_values: list[float] = []
    national_centroid_weights: list[float] = []

    for province in PROVINCE_ORDER:
        province_nodes = nodes_by_province.get(province, [])
        province_facilities = facilities_by_province.get(province, [])
        if not province_nodes or not province_facilities:
            continue
        node_values: list[float] = []
        node_weights: list[float] = []
        for node in province_nodes:
            for facility in province_facilities:
                node_values.append(
                    _facility_distance_km(
                        node.lat, node.lon, facility, factors
                    )
                )
                node_weights.append(node.harvest_m3)
        centroid = centroids[province]
        centroid_values = [
            _facility_distance_km(
                centroid.lat, centroid.lon, facility, factors
            )
            for facility in province_facilities
        ]
        province_harvest = harvest[province]
        centroid_weights = [province_harvest for _ in province_facilities]
        node_distribution = _distribution_stats(
            scope=province,
            origin_basis="node_weighted",
            values=node_values,
            weights=node_weights,
            n_origins=len(province_nodes),
            n_facilities=len(province_facilities),
        )
        unit_distribution = _distribution_stats(
            scope=province,
            origin_basis="node_unweighted",
            values=node_values,
            weights=[1.0 for _ in node_values],
            n_origins=len(province_nodes),
            n_facilities=len(province_facilities),
        )
        centroid_distribution = _distribution_stats(
            scope=province,
            origin_basis="centroid",
            values=centroid_values,
            weights=centroid_weights,
            n_origins=1,
            n_facilities=len(province_facilities),
        )
        distributions.extend(
            (node_distribution, unit_distribution, centroid_distribution)
        )
        cdf_values = _weighted_cdf_many(
            node_values, node_weights, centroid_values
        )
        median_percentile = sorted(value * 100.0 for value in cdf_values)[
            len(cdf_values) // 2
        ]
        percentiles.append(
            {
                "scope": province,
                "centroid_median_percentile_in_node_distribution": (
                    median_percentile
                ),
                "centroid_mean_km": centroid_distribution.mean_km,
                "node_weighted_mean_km": node_distribution.mean_km,
            }
        )
        national_node_values.extend(node_values)
        national_node_weights.extend(node_weights)
        national_node_unit_weights.extend(1.0 for _ in node_values)
        national_centroid_values.extend(centroid_values)
        national_centroid_weights.extend(centroid_weights)

    if not national_node_values:
        raise SupplyNodeDistanceError("no node-facility distances")
    national_node_distribution = _distribution_stats(
        scope="national",
        origin_basis="node_weighted",
        values=national_node_values,
        weights=national_node_weights,
        n_origins=len(nodes),
        n_facilities=len(facilities),
    )
    national_unit_distribution = _distribution_stats(
        scope="national",
        origin_basis="node_unweighted",
        values=national_node_values,
        weights=national_node_unit_weights,
        n_origins=len(nodes),
        n_facilities=len(facilities),
    )
    national_centroid_distribution = _distribution_stats(
        scope="national",
        origin_basis="centroid",
        values=national_centroid_values,
        weights=national_centroid_weights,
        n_origins=len({facility.province for facility in facilities}),
        n_facilities=len(facilities),
    )
    distributions.extend(
        (
            national_node_distribution,
            national_unit_distribution,
            national_centroid_distribution,
        )
    )
    cdf_values = _weighted_cdf_many(
        national_node_values, national_node_weights, national_centroid_values
    )
    national_percentile = sorted(value * 100.0 for value in cdf_values)[
        len(cdf_values) // 2
    ]
    percentiles.append(
        {
            "scope": "national",
            "centroid_median_percentile_in_node_distribution": (
                national_percentile
            ),
            "centroid_mean_km": national_centroid_distribution.mean_km,
            "node_weighted_mean_km": national_node_distribution.mean_km,
        }
    )
    return distributions, percentiles


# ---------------------------------------------------------------------------
# Transport cost and band comparison.
# ---------------------------------------------------------------------------

def load_transport_parameters() -> dict[str, transport.Parameter]:
    rows = _read_csv(TRANSPORT_PARAMETERS_CSV, "transport cost parameters")
    _require_columns(
        rows,
        {
            "parameter_id",
            "component",
            "unit",
            "low",
            "base",
            "high",
            "direction",
            "source_id",
            "notes",
        },
        TRANSPORT_PARAMETERS_CSV,
    )
    parameters: dict[str, transport.Parameter] = {}
    for position, row in enumerate(rows, start=2):
        context = f"{TRANSPORT_PARAMETERS_CSV}:{position}"
        parameter = transport.Parameter(
            parameter_id=row["parameter_id"].strip(),
            component=row["component"].strip(),
            unit=row["unit"].strip(),
            low=_finite_float(row["low"], field="low", context=context),
            base=_finite_float(row["base"], field="base", context=context),
            high=_finite_float(row["high"], field="high", context=context),
            direction=row["direction"].strip(),
            source_id=row["source_id"].strip(),
            notes=row["notes"].strip(),
        )
        if parameter.parameter_id in parameters:
            raise SupplyNodeDistanceError(
                f"{context}: duplicate parameter {parameter.parameter_id!r}"
            )
        if not parameter.low <= parameter.base <= parameter.high:
            raise SupplyNodeDistanceError(
                f"{context}: parameter bounds are not ordered"
            )
        parameters[parameter.parameter_id] = parameter
    for needed in (
        "residue_bulk_density_odt_per_m3",
        "residue_lhv_gj_per_odt",
        "payload_m3",
    ):
        if needed not in parameters:
            raise SupplyNodeDistanceError(
                f"{TRANSPORT_PARAMETERS_CSV}: missing parameter {needed!r}"
            )
    density = parameters["residue_bulk_density_odt_per_m3"].value_for("base")
    lhv = parameters["residue_lhv_gj_per_odt"].value_for("base")
    if not math.isclose(density, DENSITY_ODT_PER_M3, rel_tol=1e-12):
        raise SupplyNodeDistanceError(
            "transport density parameter does not match the documented base"
        )
    if not math.isclose(lhv, LHV_GJ_PER_ODT, rel_tol=1e-12):
        raise SupplyNodeDistanceError(
            "transport LHV parameter does not match the documented base"
        )
    return parameters


def _transport_cost_per_m3(
    distance_km: float,
    parameters: Mapping[str, transport.Parameter],
    cache: dict[float, float],
) -> float:
    key = float(distance_km)
    cached = cache.get(key)
    if cached is not None:
        return cached
    cost = float(
        transport.trip_cost(
            distance_km, PAYLOAD_M3, dict(parameters), "base"
        )["cost_per_m3_cad"]
    )
    if not math.isfinite(cost) or cost < 0.0:
        raise SupplyNodeDistanceError(
            f"invalid transport cost {cost!r} at {distance_km!r} km"
        )
    cache[key] = cost
    return cost


def _accumulate_band(
    *,
    origin_lat: float,
    origin_lon: float,
    volume_m3: float,
    facility: Facility,
    factors: Mapping[str, float],
    parameters: Mapping[str, transport.Parameter],
    cost_cache: dict[float, float],
    band_volume: dict[str, float],
    band_cost: dict[str, float],
    band_volume_distance: dict[str, float],
) -> None:
    if volume_m3 <= 0.0:
        return
    distance = _facility_distance_km(
        origin_lat, origin_lon, facility, factors
    )
    label = band_for_distance(distance)
    band_volume[label] += volume_m3
    band_cost[label] += volume_m3 * _transport_cost_per_m3(
        distance, parameters, cost_cache
    )
    band_volume_distance[label] += volume_m3 * distance


def _new_band_map() -> dict[str, float]:
    return {label: 0.0 for label in BAND_ORDER}


def _threshold_metrics(
    assigned_m3: float,
    band_volume: Mapping[str, float],
    band_cost: Mapping[str, float],
    band_volume_distance: Mapping[str, float],
) -> dict[str, float]:
    within_50 = math.fsum(
        band_volume.get(label, 0.0) for label in ("0-50",)
    )
    within_150 = math.fsum(
        band_volume.get(label, 0.0)
        for label in ("0-50", "50-100", "100-150")
    )
    within_300 = within_150 + band_volume.get("150-300", 0.0)
    gt150 = assigned_m3 - within_150
    gt300 = assigned_m3 - within_300
    volume_distance = math.fsum(band_volume_distance.values())
    cost = math.fsum(band_cost.values())
    return {
        "assigned_m3": assigned_m3,
        "within_50_m3_y": within_50,
        "within_150_m3_y": within_150,
        "within_300_m3_y": within_300,
        "gt150_m3_y": gt150,
        "gt300_m3_y": gt300,
        "gt150_share": gt150 / assigned_m3 if assigned_m3 > 0.0 else 0.0,
        "gt300_share": gt300 / assigned_m3 if assigned_m3 > 0.0 else 0.0,
        "mean_distance_km": (
            volume_distance / assigned_m3 if assigned_m3 > 0.0 else 0.0
        ),
        "transport_cost_cad": cost,
        "transport_cost_cad_m3": (
            cost / assigned_m3 if assigned_m3 > 0.0 else 0.0
        ),
        "transport_cost_cad_gj": (
            cost / (assigned_m3 * ENERGY_GJ_PER_M3)
            if assigned_m3 > 0.0
            else 0.0
        ),
    }


def build_band_comparison(
    nodes: Sequence[SupplyNode],
    facilities: Sequence[Facility],
    assignments: Sequence[AssignmentRow],
    factors: Mapping[str, float],
    centroids: Mapping[str, Any],
    parameters: Mapping[str, transport.Parameter],
) -> tuple[list[dict[str, Any]], dict[tuple[str, str], dict[str, Any]]]:
    nodes_by_province: dict[str, list[SupplyNode]] = defaultdict(list)
    facilities_by_id = {facility.facility_id: facility for facility in facilities}
    for node in nodes:
        nodes_by_province[node.province].append(node)

    centroid_band: dict[tuple[str, str], dict[str, float]] = defaultdict(
        _new_band_map
    )
    node_band: dict[tuple[str, str], dict[str, float]] = defaultdict(_new_band_map)
    centroid_cost: dict[tuple[str, str], dict[str, float]] = defaultdict(
        _new_band_map
    )
    node_cost: dict[tuple[str, str], dict[str, float]] = defaultdict(_new_band_map)
    centroid_volume_distance: dict[
        tuple[str, str], dict[str, float]
    ] = defaultdict(_new_band_map)
    node_volume_distance: dict[
        tuple[str, str], dict[str, float]
    ] = defaultdict(_new_band_map)
    cell_assigned: dict[tuple[str, str], float] = defaultdict(float)
    cost_cache: dict[float, float] = {}

    for row in assignments:
        facility = facilities_by_id[row.facility_id]
        cell = (row.province, row.facility_class)
        cell_assigned[cell] += row.assigned_m3
        centroid = centroids[row.province]
        _accumulate_band(
            origin_lat=centroid.lat,
            origin_lon=centroid.lon,
            volume_m3=row.assigned_m3,
            facility=facility,
            factors=factors,
            parameters=parameters,
            cost_cache=cost_cache,
            band_volume=centroid_band[cell],
            band_cost=centroid_cost[cell],
            band_volume_distance=centroid_volume_distance[cell],
        )
        province_nodes = nodes_by_province.get(row.province, [])
        if not province_nodes:
            raise SupplyNodeDistanceError(
                f"{row.province}: assignment has no supply nodes"
            )
        for node in province_nodes:
            _accumulate_band(
                origin_lat=node.lat,
                origin_lon=node.lon,
                volume_m3=row.assigned_m3 * node.weight,
                facility=facility,
                factors=factors,
                parameters=parameters,
                cost_cache=cost_cache,
                band_volume=node_band[cell],
                band_cost=node_cost[cell],
                band_volume_distance=node_volume_distance[cell],
            )

    band_rows: list[dict[str, Any]] = []
    cell_totals: dict[tuple[str, str], dict[str, Any]] = {}
    for province in PROVINCE_ORDER:
        for facility_class in CLASS_ORDER:
            cell = (province, facility_class)
            assigned = cell_assigned.get(cell, 0.0)
            if assigned <= 0.0:
                continue
            centroid_metrics = _threshold_metrics(
                assigned,
                centroid_band[cell],
                centroid_cost[cell],
                centroid_volume_distance[cell],
            )
            node_metrics = _threshold_metrics(
                assigned,
                node_band[cell],
                node_cost[cell],
                node_volume_distance[cell],
            )
            cell_totals[cell] = {
                "assigned_m3": assigned,
                "centroid": centroid_metrics,
                "node": node_metrics,
            }
            for label in BAND_ORDER:
                centroid_volume = centroid_band[cell].get(label, 0.0)
                node_volume = node_band[cell].get(label, 0.0)
                centroid_band_cost = centroid_cost[cell].get(label, 0.0)
                node_band_cost = node_cost[cell].get(label, 0.0)
                centroid_vd = centroid_volume_distance[cell].get(label, 0.0)
                node_vd = node_volume_distance[cell].get(label, 0.0)
                band_rows.append(
                    {
                        "province": province,
                        "facility_class": facility_class,
                        "distance_band": label,
                        "band_low_km": _format_float(
                            BANDS_BY_LABEL[label][0]
                        ),
                        "band_high_km": (
                            _format_float(BANDS_BY_LABEL[label][1])
                            if BANDS_BY_LABEL[label][1] is not None
                            else ""
                        ),
                        "assigned_m3": _format_float(assigned),
                        "centroid_assigned_m3": _format_float(centroid_volume),
                        "node_assigned_m3": _format_float(node_volume),
                        "delta_assigned_m3": _format_float(
                            node_volume - centroid_volume
                        ),
                        "centroid_share": _format_float(
                            centroid_volume / assigned
                        ),
                        "node_share": _format_float(node_volume / assigned),
                        "delta_share_pp": _format_float(
                            100.0 * (node_volume - centroid_volume) / assigned
                        ),
                        "centroid_mean_distance_km": _format_float(
                            centroid_vd / centroid_volume
                            if centroid_volume > 0.0
                            else 0.0
                        ),
                        "node_mean_distance_km": _format_float(
                            node_vd / node_volume if node_volume > 0.0 else 0.0
                        ),
                        "centroid_transport_cost_cad": _format_float(
                            centroid_band_cost
                        ),
                        "node_transport_cost_cad": _format_float(node_band_cost),
                        "delta_transport_cost_cad": _format_float(
                            node_band_cost - centroid_band_cost
                        ),
                        "evidence_label": EVIDENCE_LABEL,
                        "observed": _bool_text(OBSERVED),
                        "policy_grade": _bool_text(POLICY_GRADE),
                    }
                )
    return band_rows, cell_totals


# ---------------------------------------------------------------------------
# Local capacity access and transport gap.
# ---------------------------------------------------------------------------

def build_capacity_access(
    nodes: Sequence[SupplyNode],
    facilities: Sequence[Facility],
    factors: Mapping[str, float],
    centroids: Mapping[str, Any],
) -> dict[str, dict[tuple[str, str], dict[str, float]]]:
    nodes_by_province: dict[str, list[SupplyNode]] = defaultdict(list)
    facilities_by_province_class: dict[
        tuple[str, str], list[Facility]
    ] = defaultdict(list)
    for node in nodes:
        nodes_by_province[node.province].append(node)
    for facility in facilities:
        facilities_by_province_class[
            (facility.province, facility.facility_class)
        ].append(facility)

    node_access: dict[tuple[str, str], dict[str, float]] = {}
    centroid_access: dict[tuple[str, str], dict[str, float]] = {}
    for province in PROVINCE_ORDER:
        province_nodes = nodes_by_province.get(province, [])
        if not province_nodes:
            continue
        for facility_class in CLASS_ORDER:
            class_facilities = facilities_by_province_class.get(
                (province, facility_class), []
            )
            total_capacity = math.fsum(
                facility.capacity_m3_y for facility in class_facilities
            )
            weighted = {
                "within_50_m3_y": 0.0,
                "within_150_m3_y": 0.0,
                "within_300_m3_y": 0.0,
                "total_m3_y": total_capacity,
            }
            for node in province_nodes:
                node_within_50 = 0.0
                node_within_150 = 0.0
                node_within_300 = 0.0
                for facility in class_facilities:
                    distance = _facility_distance_km(
                        node.lat, node.lon, facility, factors
                    )
                    if distance < 50.0:
                        node_within_50 += facility.capacity_m3_y
                    if distance < 150.0:
                        node_within_150 += facility.capacity_m3_y
                    if distance < 300.0:
                        node_within_300 += facility.capacity_m3_y
                weighted["within_50_m3_y"] += (
                    node.weight * node_within_50
                )
                weighted["within_150_m3_y"] += (
                    node.weight * node_within_150
                )
                weighted["within_300_m3_y"] += (
                    node.weight * node_within_300
                )
            node_access[(province, facility_class)] = weighted

            centroid = centroids[province]
            centroid_within_50 = 0.0
            centroid_within_150 = 0.0
            centroid_within_300 = 0.0
            for facility in class_facilities:
                distance = _facility_distance_km(
                    centroid.lat, centroid.lon, facility, factors
                )
                if distance < 50.0:
                    centroid_within_50 += facility.capacity_m3_y
                if distance < 150.0:
                    centroid_within_150 += facility.capacity_m3_y
                if distance < 300.0:
                    centroid_within_300 += facility.capacity_m3_y
            centroid_access[(province, facility_class)] = {
                "within_50_m3_y": centroid_within_50,
                "within_150_m3_y": centroid_within_150,
                "within_300_m3_y": centroid_within_300,
                "total_m3_y": total_capacity,
            }
    return {"node": node_access, "centroid": centroid_access}


def build_transport_gap_rows(
    cell_totals: Mapping[tuple[str, str], Mapping[str, Any]],
    capacity_access: Mapping[str, Mapping[tuple[str, str], Mapping[str, float]]],
    province_assigned: Mapping[str, float],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for province in PROVINCE_ORDER:
        province_total = province_assigned.get(province, 0.0)
        for facility_class in CLASS_ORDER:
            cell = (province, facility_class)
            totals = cell_totals.get(cell)
            if totals is None:
                continue
            centroid = totals["centroid"]
            node = totals["node"]
            centroid_capacity = capacity_access["centroid"].get(cell, {})
            node_capacity = capacity_access["node"].get(cell, {})
            centroid_capacity_50 = centroid_capacity.get(
                "within_50_m3_y", 0.0
            )
            node_capacity_50 = node_capacity.get("within_50_m3_y", 0.0)
            centroid_capacity_150 = centroid_capacity.get(
                "within_150_m3_y", 0.0
            )
            node_capacity_150 = node_capacity.get("within_150_m3_y", 0.0)
            centroid_capacity_300 = centroid_capacity.get(
                "within_300_m3_y", 0.0
            )
            node_capacity_300 = node_capacity.get("within_300_m3_y", 0.0)
            assigned = float(totals["assigned_m3"])
            rows.append(
                {
                    "province": province,
                    "facility_class": facility_class,
                    "assigned_m3": _format_float(assigned),
                    "assigned_share_of_province": _format_float(
                        assigned / province_total
                        if province_total > 0.0
                        else 0.0
                    ),
                    "centroid_local_capacity_within_50_m3_y": _format_float(
                        centroid_capacity_50
                    ),
                    "node_weighted_local_capacity_within_50_m3_y": _format_float(
                        node_capacity_50
                    ),
                    "centroid_within_50_m3_y": _format_float(
                        centroid["within_50_m3_y"]
                    ),
                    "centroid_within_150_m3_y": _format_float(
                        centroid["within_150_m3_y"]
                    ),
                    "centroid_within_300_m3_y": _format_float(
                        centroid["within_300_m3_y"]
                    ),
                    "node_within_50_m3_y": _format_float(
                        node["within_50_m3_y"]
                    ),
                    "node_within_150_m3_y": _format_float(
                        node["within_150_m3_y"]
                    ),
                    "node_within_300_m3_y": _format_float(
                        node["within_300_m3_y"]
                    ),
                    "centroid_gt150_m3_y": _format_float(centroid["gt150_m3_y"]),
                    "node_gt150_m3_y": _format_float(node["gt150_m3_y"]),
                    "centroid_gt150_share": _format_float(
                        centroid["gt150_share"]
                    ),
                    "node_gt150_share": _format_float(node["gt150_share"]),
                    "delta_gt150_share_pp": _format_float(
                        100.0
                        * (node["gt150_share"] - centroid["gt150_share"])
                    ),
                    "centroid_gt300_m3_y": _format_float(centroid["gt300_m3_y"]),
                    "node_gt300_m3_y": _format_float(node["gt300_m3_y"]),
                    "centroid_gt300_share": _format_float(
                        centroid["gt300_share"]
                    ),
                    "node_gt300_share": _format_float(node["gt300_share"]),
                    "delta_gt300_share_pp": _format_float(
                        100.0
                        * (node["gt300_share"] - centroid["gt300_share"])
                    ),
                    "centroid_mean_distance_km": _format_float(
                        centroid["mean_distance_km"]
                    ),
                    "node_mean_distance_km": _format_float(
                        node["mean_distance_km"]
                    ),
                    "delta_mean_distance_km": _format_float(
                        node["mean_distance_km"] - centroid["mean_distance_km"]
                    ),
                    "centroid_transport_cost_cad": _format_float(
                        centroid["transport_cost_cad"]
                    ),
                    "node_transport_cost_cad": _format_float(
                        node["transport_cost_cad"]
                    ),
                    "delta_transport_cost_cad": _format_float(
                        node["transport_cost_cad"]
                        - centroid["transport_cost_cad"]
                    ),
                    "centroid_transport_cost_cad_m3": _format_float(
                        centroid["transport_cost_cad_m3"]
                    ),
                    "node_transport_cost_cad_m3": _format_float(
                        node["transport_cost_cad_m3"]
                    ),
                    "delta_transport_cost_cad_m3": _format_float(
                        node["transport_cost_cad_m3"]
                        - centroid["transport_cost_cad_m3"]
                    ),
                    "centroid_transport_cost_cad_gj": _format_float(
                        centroid["transport_cost_cad_gj"]
                    ),
                    "node_transport_cost_cad_gj": _format_float(
                        node["transport_cost_cad_gj"]
                    ),
                    "centroid_local_capacity_within_150_m3_y": _format_float(
                        centroid_capacity_150
                    ),
                    "node_weighted_local_capacity_within_150_m3_y": _format_float(
                        node_capacity_150
                    ),
                    "centroid_local_capacity_within_300_m3_y": _format_float(
                        centroid_capacity_300
                    ),
                    "node_weighted_local_capacity_within_300_m3_y": _format_float(
                        node_capacity_300
                    ),
                    "local_capacity_total_m3_y": _format_float(
                        centroid_capacity.get("total_m3_y", 0.0)
                    ),
                    "centroid_local_access_gap_within_150_m3_y": _format_float(
                        max(0.0, assigned - centroid_capacity_150)
                    ),
                    "node_local_access_gap_within_150_m3_y": _format_float(
                        max(0.0, assigned - node_capacity_150)
                    ),
                    "centroid_local_access_gap_within_300_m3_y": _format_float(
                        max(0.0, assigned - centroid_capacity_300)
                    ),
                    "node_local_access_gap_within_300_m3_y": _format_float(
                        max(0.0, assigned - node_capacity_300)
                    ),
                    "capacity_aggregation": "harvest_weighted_node_expectation",
                    "localization_reference_km": _format_float(
                        LOCALIZATION_REFERENCE_KM
                    ),
                    "evidence_label": EVIDENCE_LABEL,
                    "observed": _bool_text(OBSERVED),
                    "policy_grade": _bool_text(POLICY_GRADE),
                }
            )
    return rows


def build_province_gap_rows(
    gap_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in gap_rows:
        grouped[row["province"]].append(row)
    output: list[dict[str, Any]] = []
    for province in PROVINCE_ORDER:
        rows = grouped.get(province)
        if not rows:
            continue
        assigned = math.fsum(float(row["assigned_m3"]) for row in rows)
        centroid_gt150 = math.fsum(
            float(row["centroid_gt150_m3_y"]) for row in rows
        )
        node_gt150 = math.fsum(float(row["node_gt150_m3_y"]) for row in rows)
        centroid_gt300 = math.fsum(
            float(row["centroid_gt300_m3_y"]) for row in rows
        )
        node_gt300 = math.fsum(float(row["node_gt300_m3_y"]) for row in rows)
        centroid_cost = math.fsum(
            float(row["centroid_transport_cost_cad"]) for row in rows
        )
        node_cost = math.fsum(
            float(row["node_transport_cost_cad"]) for row in rows
        )
        centroid_vd = math.fsum(
            float(row["centroid_mean_distance_km"])
            * float(row["assigned_m3"])
            for row in rows
        )
        node_vd = math.fsum(
            float(row["node_mean_distance_km"]) * float(row["assigned_m3"])
            for row in rows
        )
        centroid_capacity_150 = math.fsum(
            float(row["centroid_local_capacity_within_150_m3_y"])
            for row in rows
        )
        node_capacity_150 = math.fsum(
            float(row["node_weighted_local_capacity_within_150_m3_y"])
            for row in rows
        )
        centroid_capacity_300 = math.fsum(
            float(row["centroid_local_capacity_within_300_m3_y"])
            for row in rows
        )
        node_capacity_300 = math.fsum(
            float(row["node_weighted_local_capacity_within_300_m3_y"])
            for row in rows
        )
        output.append(
            {
                "province": province,
                "assigned_m3": _format_float(assigned),
                "classes_with_assignment": str(
                    sum(1 for row in rows if float(row["assigned_m3"]) > 0.0)
                ),
                "centroid_within_150_m3_y": _format_float(
                    assigned - centroid_gt150
                ),
                "node_within_150_m3_y": _format_float(assigned - node_gt150),
                "centroid_gt150_m3_y": _format_float(centroid_gt150),
                "node_gt150_m3_y": _format_float(node_gt150),
                "centroid_gt150_share": _format_float(
                    centroid_gt150 / assigned if assigned > 0.0 else 0.0
                ),
                "node_gt150_share": _format_float(
                    node_gt150 / assigned if assigned > 0.0 else 0.0
                ),
                "delta_gt150_share_pp": _format_float(
                    100.0
                    * (
                        node_gt150 / assigned
                        - centroid_gt150 / assigned
                    )
                    if assigned > 0.0
                    else 0.0
                ),
                "centroid_within_300_m3_y": _format_float(
                    assigned - centroid_gt300
                ),
                "node_within_300_m3_y": _format_float(assigned - node_gt300),
                "centroid_gt300_m3_y": _format_float(centroid_gt300),
                "node_gt300_m3_y": _format_float(node_gt300),
                "centroid_gt300_share": _format_float(
                    centroid_gt300 / assigned if assigned > 0.0 else 0.0
                ),
                "node_gt300_share": _format_float(
                    node_gt300 / assigned if assigned > 0.0 else 0.0
                ),
                "delta_gt300_share_pp": _format_float(
                    100.0
                    * (
                        node_gt300 / assigned
                        - centroid_gt300 / assigned
                    )
                    if assigned > 0.0
                    else 0.0
                ),
                "centroid_mean_distance_km": _format_float(
                    centroid_vd / assigned if assigned > 0.0 else 0.0
                ),
                "node_mean_distance_km": _format_float(
                    node_vd / assigned if assigned > 0.0 else 0.0
                ),
                "delta_mean_distance_km": _format_float(
                    (node_vd - centroid_vd) / assigned
                    if assigned > 0.0
                    else 0.0
                ),
                "centroid_transport_cost_cad": _format_float(centroid_cost),
                "node_transport_cost_cad": _format_float(node_cost),
                "delta_transport_cost_cad": _format_float(
                    node_cost - centroid_cost
                ),
                "centroid_transport_cost_cad_m3": _format_float(
                    centroid_cost / assigned if assigned > 0.0 else 0.0
                ),
                "node_transport_cost_cad_m3": _format_float(
                    node_cost / assigned if assigned > 0.0 else 0.0
                ),
                "delta_transport_cost_cad_m3": _format_float(
                    (node_cost - centroid_cost) / assigned
                    if assigned > 0.0
                    else 0.0
                ),
                "centroid_local_capacity_within_150_m3_y": _format_float(
                    centroid_capacity_150
                ),
                "node_weighted_local_capacity_within_150_m3_y": _format_float(
                    node_capacity_150
                ),
                "centroid_local_capacity_within_300_m3_y": _format_float(
                    centroid_capacity_300
                ),
                "node_weighted_local_capacity_within_300_m3_y": _format_float(
                    node_capacity_300
                ),
                "centroid_local_access_gap_within_150_m3_y": _format_float(
                    max(0.0, assigned - centroid_capacity_150)
                ),
                "node_local_access_gap_within_150_m3_y": _format_float(
                    max(0.0, assigned - node_capacity_150)
                ),
                "centroid_local_access_gap_within_300_m3_y": _format_float(
                    max(0.0, assigned - centroid_capacity_300)
                ),
                "node_local_access_gap_within_300_m3_y": _format_float(
                    max(0.0, assigned - node_capacity_300)
                ),
                "evidence_label": EVIDENCE_LABEL,
                "observed": _bool_text(OBSERVED),
                "policy_grade": _bool_text(POLICY_GRADE),
            }
        )
    return output


# ---------------------------------------------------------------------------
# Weight-basis sensitivity.
# ---------------------------------------------------------------------------

def build_weight_sensitivity(
    nodes: Sequence[SupplyNode],
    assignments: Sequence[AssignmentRow],
    facilities_by_id: Mapping[str, Facility],
    factors: Mapping[str, float],
    parameters: Mapping[str, transport.Parameter],
) -> list[dict[str, Any]]:
    nodes_by_province: dict[str, list[SupplyNode]] = defaultdict(list)
    area_weight_sum: dict[str, float] = defaultdict(float)
    for node in nodes:
        nodes_by_province[node.province].append(node)
        area_weight_sum[node.province] += node.area_weight

    output: list[dict[str, Any]] = []
    cost_cache: dict[float, float] = {}
    for weight_basis in ("rsyc_production", "scanfi_area"):
        national_band: dict[str, float] = _new_band_map()
        national_cost: dict[str, float] = _new_band_map()
        national_vd: dict[str, float] = _new_band_map()
        province_band: dict[str, dict[str, float]] = defaultdict(_new_band_map)
        province_cost: dict[str, dict[str, float]] = defaultdict(_new_band_map)
        province_vd: dict[str, dict[str, float]] = defaultdict(_new_band_map)
        assigned_by_province: dict[str, float] = defaultdict(float)
        national_assigned = 0.0

        for row in assignments:
            facility = facilities_by_id[row.facility_id]
            assigned_by_province[row.province] += row.assigned_m3
            national_assigned += row.assigned_m3
            province_nodes = nodes_by_province.get(row.province, [])
            if not province_nodes:
                raise SupplyNodeDistanceError(
                    f"{row.province}: assignment has no supply nodes"
                )
            if weight_basis == "scanfi_area" and area_weight_sum[row.province] <= 0.0:
                raise SupplyNodeDistanceError(
                    f"{row.province}: no SCANFI area weight for sensitivity"
                )
            for node in province_nodes:
                if weight_basis == "rsyc_production":
                    weight = node.weight
                else:
                    weight = node.area_weight / area_weight_sum[row.province]
                volume = row.assigned_m3 * weight
                _accumulate_band(
                    origin_lat=node.lat,
                    origin_lon=node.lon,
                    volume_m3=volume,
                    facility=facility,
                    factors=factors,
                    parameters=parameters,
                    cost_cache=cost_cache,
                    band_volume=national_band,
                    band_cost=national_cost,
                    band_volume_distance=national_vd,
                )
                _accumulate_band(
                    origin_lat=node.lat,
                    origin_lon=node.lon,
                    volume_m3=volume,
                    facility=facility,
                    factors=factors,
                    parameters=parameters,
                    cost_cache=cost_cache,
                    band_volume=province_band[row.province],
                    band_cost=province_cost[row.province],
                    band_volume_distance=province_vd[row.province],
                )

        metrics = _threshold_metrics(
            national_assigned, national_band, national_cost, national_vd
        )
        output.append(
            {
                "weight_basis": weight_basis,
                "level": "national",
                "province": "ALL",
                **_metric_fields(metrics),
            }
        )
        for province in PROVINCE_ORDER:
            assigned = assigned_by_province.get(province, 0.0)
            if assigned <= 0.0:
                continue
            metrics = _threshold_metrics(
                assigned,
                province_band[province],
                province_cost[province],
                province_vd[province],
            )
            output.append(
                {
                    "weight_basis": weight_basis,
                    "level": "province",
                    "province": province,
                    **_metric_fields(metrics),
                }
            )
    return output


def _metric_fields(metrics: Mapping[str, float]) -> dict[str, str]:
    return {
        "assigned_m3": _format_float(metrics["assigned_m3"]),
        "within_150_m3_y": _format_float(metrics["within_150_m3_y"]),
        "gt150_m3_y": _format_float(metrics["gt150_m3_y"]),
        "gt150_share": _format_float(metrics["gt150_share"]),
        "within_300_m3_y": _format_float(metrics["within_300_m3_y"]),
        "gt300_m3_y": _format_float(metrics["gt300_m3_y"]),
        "gt300_share": _format_float(metrics["gt300_share"]),
        "mean_distance_km": _format_float(metrics["mean_distance_km"]),
        "transport_cost_cad": _format_float(metrics["transport_cost_cad"]),
        "transport_cost_cad_m3": _format_float(
            metrics["transport_cost_cad_m3"]
        ),
        "transport_cost_cad_gj": _format_float(
            metrics["transport_cost_cad_gj"]
        ),
        "evidence_label": EVIDENCE_LABEL,
        "observed": _bool_text(OBSERVED),
        "policy_grade": _bool_text(POLICY_GRADE),
    }


# ---------------------------------------------------------------------------
# OSRM node-origin sample.
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class OSRMRequest:
    pair_id: str
    province: str
    node_id: str
    node_lat: float
    node_lon: float
    node_weight: float
    node_harvest_m3: float
    facility_id: str
    facility_name: str
    facility_class: str
    facility_province: str
    facility_lat: float
    facility_lon: float
    centroid_haversine_km: float
    centroid_factor_km: float
    centroid_osrm_road_km: float | None
    centroid_osrm_duration_h: float | None
    centroid_origin_snap_km: float | None
    centroid_destination_snap_km: float | None


def select_osrm_requests(
    nodes: Sequence[SupplyNode],
    factors: Mapping[str, float],
) -> tuple[OSRMRequest, ...]:
    sample_rows = _read_csv(AUDIT_V2_SAMPLE_CSV, "audit v2 paired distance sample")
    _require_columns(
        sample_rows,
        {
            "pair_id",
            "province",
            "facility_id",
            "facility_name",
            "facility_class",
            "facility_province",
            "facility_lat",
            "facility_lon",
            "haversine_km",
            "province_road_factor_km",
            "osrm_road_km",
            "osrm_duration_h",
            "origin_snap_km",
            "destination_snap_km",
        },
        AUDIT_V2_SAMPLE_CSV,
    )
    nodes_by_province: dict[str, list[SupplyNode]] = defaultdict(list)
    for node in nodes:
        nodes_by_province[node.province].append(node)
    top_nodes: dict[str, SupplyNode] = {}
    for province, province_nodes in nodes_by_province.items():
        top_nodes[province] = max(
            province_nodes, key=lambda node: (node.weight, node.node_id)
        )

    requests: list[OSRMRequest] = []
    for position, row in enumerate(sample_rows, start=2):
        context = f"{AUDIT_V2_SAMPLE_CSV}:{position}"
        province = row["province"].strip().upper()
        if province not in PROVINCE_ORDER:
            raise SupplyNodeDistanceError(
                f"{context}: unexpected province {province!r}"
            )
        top_node = top_nodes.get(province)
        if top_node is None:
            raise SupplyNodeDistanceError(
                f"{context}: no supply node for province {province!r}"
            )
        facility_id = row["facility_id"].strip()
        facility_lat = _finite_float(
            row["facility_lat"], field="facility_lat", context=context
        )
        facility_lon = _finite_float(
            row["facility_lon"], field="facility_lon", context=context
        )
        facility_province = row["facility_province"].strip().upper()
        if facility_province not in factors:
            raise SupplyNodeDistanceError(
                f"{context}: no road factor for {facility_province}"
            )
        centroid_haversine = _nonnegative_float(
            row["haversine_km"], field="haversine_km", context=context
        )
        centroid_factor_km = _nonnegative_float(
            row["province_road_factor_km"],
            field="province_road_factor_km",
            context=context,
        )
        centroid_osrm = (
            _positive_float(
                row["osrm_road_km"], field="osrm_road_km", context=context
            )
            if row["osrm_road_km"].strip()
            else None
        )
        centroid_duration = (
            _positive_float(
                row["osrm_duration_h"],
                field="osrm_duration_h",
                context=context,
            )
            if row["osrm_duration_h"].strip()
            else None
        )
        requests.append(
            OSRMRequest(
                pair_id=row["pair_id"].strip(),
                province=province,
                node_id=top_node.node_id,
                node_lat=top_node.lat,
                node_lon=top_node.lon,
                node_weight=top_node.weight,
                node_harvest_m3=top_node.harvest_m3,
                facility_id=facility_id,
                facility_name=row["facility_name"].strip(),
                facility_class=row["facility_class"].strip().upper(),
                facility_province=facility_province,
                facility_lat=facility_lat,
                facility_lon=facility_lon,
                centroid_haversine_km=centroid_haversine,
                centroid_factor_km=centroid_factor_km,
                centroid_osrm_road_km=centroid_osrm,
                centroid_osrm_duration_h=centroid_duration,
                centroid_origin_snap_km=(
                    _nonnegative_float(
                        row["origin_snap_km"],
                        field="origin_snap_km",
                        context=context,
                    )
                    if row["origin_snap_km"].strip()
                    else None
                ),
                centroid_destination_snap_km=(
                    _nonnegative_float(
                        row["destination_snap_km"],
                        field="destination_snap_km",
                        context=context,
                    )
                    if row["destination_snap_km"].strip()
                    else None
                ),
            )
        )
    if not requests:
        raise SupplyNodeDistanceError("no OSRM sample requests")
    return tuple(requests)


def run_osrm_sample(
    requests: Sequence[OSRMRequest],
    factors: Mapping[str, float],
    *,
    fetcher: Callable[..., audit_v1.RouteResult] = audit_v1.fetch_osrm_route,
    sleeper: Callable[[float], None] = time.sleep,
    rate_limit_seconds: float = RATE_LIMIT_SECONDS,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for index, request in enumerate(requests):
        node_haversine = haversine_km(
            request.node_lat,
            request.node_lon,
            request.facility_lat,
            request.facility_lon,
        )
        origin = _Location(lat=request.node_lat, lon=request.node_lon)
        facility = audit_v1.Facility(
            facility_id=request.facility_id,
            name=request.facility_name,
            facility_class=request.facility_class,
            province=request.facility_province,
            lat=request.facility_lat,
            lon=request.facility_lon,
            evidence_tier="",
            status="",
        )
        url = audit_v1.osrm_route_url(origin, facility)
        requested_at = _utc_now()
        route = fetcher(url, expected_haversine_km=node_haversine)
        results.append(
            {
                "request": request,
                "request_url": url,
                "requested_at_utc": requested_at,
                "route": route,
                "node_haversine_km": node_haversine,
                "node_factor_km": node_haversine * factors[
                    request.facility_province
                ],
            }
        )
        if index < len(requests) - 1:
            sleeper(rate_limit_seconds)
    return results


@dataclass(frozen=True, slots=True)
class _Location:
    lat: float
    lon: float


def _stats(values: Sequence[float]) -> dict[str, Any]:
    if not values:
        return {
            "n": 0,
            "min": None,
            "p25": None,
            "median": None,
            "p75": None,
            "max": None,
            "mean": None,
        }
    ordered = sorted(values)
    n = len(ordered)
    return {
        "n": n,
        "min": ordered[0],
        "p25": ordered[n // 4],
        "median": ordered[n // 2],
        "p75": ordered[(3 * n) // 4],
        "max": ordered[-1],
        "mean": math.fsum(ordered) / n,
    }


def build_osrm_outputs(
    results: Sequence[Mapping[str, Any]],
    *,
    factors: Mapping[str, float],
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    sample_rows: list[dict[str, Any]] = []
    route_rows: list[dict[str, Any]] = []
    node_ratios: list[float] = []
    centroid_ratios: list[float] = []
    ratio_deltas: list[float] = []
    node_factor_errors: list[float] = []
    centroid_factor_errors: list[float] = []
    node_snaps: list[float] = []
    centroid_snaps: list[float] = []
    paired_count = 0
    status_counts: dict[str, int] = defaultdict(int)

    for item in results:
        request: OSRMRequest = item["request"]
        route: audit_v1.RouteResult = item["route"]
        status_counts[route.status] += 1
        route_rows.append(
            {
                "pair_id": request.pair_id,
                "province": request.province,
                "node_id": request.node_id,
                "facility_id": request.facility_id,
                "request_url": item["request_url"],
                "requested_at_utc": item["requested_at_utc"],
                "status": route.status,
                "http_status": route.http_status,
                "attempts": route.attempts,
                "response_raw": route.response_text,
                "response_sha256": hashlib.sha256(
                    route.response_text.encode("utf-8")
                ).hexdigest(),
                "error": route.error,
            }
        )
        node_ratio = (
            route.distance_km / item["node_haversine_km"]
            if route.status == "ok"
            and route.distance_km is not None
            and item["node_haversine_km"] > 0.0
            else None
        )
        centroid_ratio = (
            request.centroid_osrm_road_km / request.centroid_haversine_km
            if request.centroid_osrm_road_km is not None
            and request.centroid_haversine_km > 0.0
            else None
        )
        node_factor_error = (
            100.0
            * (item["node_factor_km"] - route.distance_km)
            / route.distance_km
            if route.status == "ok"
            and route.distance_km is not None
            and route.distance_km > 0.0
            else None
        )
        centroid_factor_error = (
            100.0
            * (request.centroid_factor_km - request.centroid_osrm_road_km)
            / request.centroid_osrm_road_km
            if request.centroid_osrm_road_km is not None
            and request.centroid_osrm_road_km > 0.0
            else None
        )
        ratio_delta = (
            node_ratio - centroid_ratio
            if node_ratio is not None and centroid_ratio is not None
            else None
        )
        if node_ratio is not None:
            node_ratios.append(node_ratio)
        if centroid_ratio is not None:
            centroid_ratios.append(centroid_ratio)
        if ratio_delta is not None:
            ratio_deltas.append(ratio_delta)
        if node_factor_error is not None:
            node_factor_errors.append(node_factor_error)
        if centroid_factor_error is not None:
            centroid_factor_errors.append(centroid_factor_error)
        if route.origin_snap_km is not None:
            node_snaps.append(route.origin_snap_km)
        if request.centroid_origin_snap_km is not None:
            centroid_snaps.append(request.centroid_origin_snap_km)
        if node_ratio is not None and centroid_ratio is not None:
            paired_count += 1
        sample_rows.append(
            {
                "pair_id": request.pair_id,
                "province": request.province,
                "node_id": request.node_id,
                "node_lat": _format_float(request.node_lat),
                "node_lon": _format_float(request.node_lon),
                "node_weight": _format_float(request.node_weight),
                "node_harvest_m3": _format_float(request.node_harvest_m3),
                "facility_id": request.facility_id,
                "facility_name": request.facility_name,
                "facility_class": request.facility_class,
                "facility_province": request.facility_province,
                "facility_lat": _format_float(request.facility_lat),
                "facility_lon": _format_float(request.facility_lon),
                "node_haversine_km": _format_float(item["node_haversine_km"]),
                "node_province_road_factor": _format_float(
                    factors[request.facility_province]
                ),
                "node_factor_km": _format_float(item["node_factor_km"]),
                "osrm_status": route.status,
                "osrm_http_status": (
                    route.http_status if route.http_status is not None else ""
                ),
                "osrm_road_km": _format_float(route.distance_km),
                "osrm_duration_h": _format_float(route.duration_h),
                "node_road_haversine_ratio": _format_float(node_ratio),
                "node_factor_error_pct": _format_float(node_factor_error),
                "centroid_haversine_km": _format_float(
                    request.centroid_haversine_km
                ),
                "centroid_factor_km": _format_float(request.centroid_factor_km),
                "centroid_osrm_road_km": _format_float(
                    request.centroid_osrm_road_km
                ),
                "centroid_osrm_duration_h": _format_float(
                    request.centroid_osrm_duration_h
                ),
                "centroid_road_haversine_ratio": _format_float(
                    centroid_ratio
                ),
                "centroid_factor_error_pct": _format_float(
                    centroid_factor_error
                ),
                "node_minus_centroid_ratio": _format_float(ratio_delta),
                "origin_snap_km": _format_float(route.origin_snap_km),
                "destination_snap_km": _format_float(
                    route.destination_snap_km
                ),
                "centroid_origin_snap_km": _format_float(
                    request.centroid_origin_snap_km
                ),
                "centroid_destination_snap_km": _format_float(
                    request.centroid_destination_snap_km
                ),
                "snap_warning": _bool_text(
                    route.status == "ok"
                    and max(
                        route.origin_snap_km or 0.0,
                        route.destination_snap_km or 0.0,
                    )
                    > SNAP_WARNING_KM
                ),
                "error": route.error,
                "evidence_label": EVIDENCE_LABEL,
                "observed": _bool_text(OBSERVED),
                "policy_grade": _bool_text(POLICY_GRADE),
            }
        )

    province_medians: dict[str, dict[str, Any]] = {}
    for province in PROVINCE_ORDER:
        province_ratios = [
            float(row["node_road_haversine_ratio"])
            for row in sample_rows
            if row["province"] == province
            and row["node_road_haversine_ratio"] != ""
        ]
        if not province_ratios:
            continue
        province_medians[province] = {
            "n_ok": len(province_ratios),
            "node_median_road_haversine_ratio": sorted(province_ratios)[
                len(province_ratios) // 2
            ],
            "audit_province_road_factor": factors.get(province),
        }
        factor = factors.get(province)
        if factor is not None:
            province_medians[province]["factor_delta_pct"] = 100.0 * (
                sorted(province_ratios)[len(province_ratios) // 2] / factor
                - 1.0
            )

    summary = {
        "provider": OSRM_PROVIDER,
        "base_url": OSRM_BASE_URL,
        "profile": OSRM_PROFILE,
        "licence": OSRM_LICENCE,
        "attribution": "© OpenStreetMap contributors",
        "rate_limit_seconds": RATE_LIMIT_SECONDS,
        "requests_attempted": len(results),
        "requests_ok": status_counts.get("ok", 0),
        "requests_failed": len(results) - status_counts.get("ok", 0),
        "status_counts": dict(status_counts),
        "paired_ok_count": paired_count,
        "node_road_haversine_ratio": _stats(node_ratios),
        "centroid_road_haversine_ratio": _stats(centroid_ratios),
        "node_minus_centroid_ratio": _stats(ratio_deltas),
        "node_factor_error_pct": _stats(node_factor_errors),
        "centroid_factor_error_pct": _stats(centroid_factor_errors),
        "node_origin_snap_km": _stats(node_snaps),
        "centroid_origin_snap_km": _stats(centroid_snaps),
        "province_medians": province_medians,
        "usage_policy": (
            "Public OSRM demo server, best-effort; one request at a time with "
            f"{RATE_LIMIT_SECONDS:.1f} s spacing. No paid API key."
        ),
    }
    routes_json = {"routes": route_rows}
    return sample_rows, summary, routes_json


# ---------------------------------------------------------------------------
# Validation.
# ---------------------------------------------------------------------------

def _check_numeric_rows(
    rows: Sequence[Mapping[str, Any]]
) -> tuple[bool, bool]:
    finite = True
    nonnegative = True
    for row in rows:
        for key, value in row.items():
            if value is None or value == "":
                continue
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(number):
                finite = False
                continue
            if key.endswith(("lat", "lon")):
                continue
            signed = (
                "delta" in key
                or "minus" in key
                or "error_pct" in key
                or key in {"factor_delta_pct"}
            )
            if number < 0.0 and not signed:
                nonnegative = False
    return finite, nonnegative


def validate(
    *,
    nodes: Sequence[SupplyNode],
    harvest: Mapping[str, float],
    rsyc_diagnostics: Mapping[str, Any],
    assignments: Sequence[AssignmentRow],
    facilities: Sequence[Facility],
    band_rows: Sequence[Mapping[str, Any]],
    gap_rows: Sequence[Mapping[str, Any]],
    province_rows: Sequence[Mapping[str, Any]],
    sensitivity_rows: Sequence[Mapping[str, Any]],
    distributions: Sequence[Distribution],
    percentiles: Sequence[Mapping[str, Any]],
    sample_rows: Sequence[Mapping[str, Any]],
    osrm_summary: Mapping[str, Any],
    summary_labels: Mapping[str, Any],
) -> dict[str, Any]:
    checks: dict[str, bool] = {}
    checks["node_count_positive"] = len(nodes) > 0
    checks["node_ids_unique"] = len({node.node_id for node in nodes}) == len(nodes)
    checks["facility_count_positive"] = len(facilities) > 0
    checks["assignment_count_positive"] = len(assignments) > 0

    weights_by_province: dict[str, float] = defaultdict(float)
    area_weights_by_province: dict[str, float] = defaultdict(float)
    harvest_by_province: dict[str, float] = defaultdict(float)
    for node in nodes:
        weights_by_province[node.province] += node.weight
        area_weights_by_province[node.province] += node.area_weight
        harvest_by_province[node.province] += node.harvest_m3
    checks["node_weight_conservation"] = all(
        abs(weights_by_province[province] - 1.0) <= WEIGHT_TOLERANCE
        for province in {node.province for node in nodes}
    )
    checks["node_area_weight_conservation"] = all(
        abs(area_weights_by_province[province] - 1.0) <= 1e-8
        for province in {node.province for node in nodes}
        if area_weights_by_province[province] > 0.0
    )
    checks["node_harvest_conservation"] = all(
        abs(harvest_by_province[province] - harvest[province])
        <= VOLUME_TOLERANCE_M3
        for province in harvest
        if harvest[province] > 0.0
    ) and abs(
        math.fsum(node.harvest_m3 for node in nodes)
        - math.fsum(harvest.values())
    ) <= VOLUME_TOLERANCE_M3
    checks["harvest_positive_provinces_have_nodes"] = all(
        harvest_by_province.get(province, 0.0) > 0.0
        for province in harvest
        if harvest[province] > 0.0
    )
    checks["node_coordinates_finite"] = all(
        math.isfinite(node.lat)
        and math.isfinite(node.lon)
        and -90.0 <= node.lat <= 90.0
        and -180.0 <= node.lon <= 180.0
        for node in nodes
    )
    checks["node_values_finite_nonnegative"] = all(
        math.isfinite(node.production_m3)
        and node.production_m3 > 0.0
        and math.isfinite(node.weight)
        and node.weight > 0.0
        and math.isfinite(node.harvest_m3)
        and node.harvest_m3 >= 0.0
        for node in nodes
    )
    checks["fallback_area_accounted"] = (
        rsyc_diagnostics["fallback_production_share"] >= 0.0
        and rsyc_diagnostics["fallback_production_share"] < 0.10
    )

    assigned_by_cell: dict[tuple[str, str], float] = defaultdict(float)
    for row in assignments:
        assigned_by_cell[(row.province, row.facility_class)] += row.assigned_m3
    checks["assignment_total_conservation"] = abs(
        math.fsum(row.assigned_m3 for row in assignments)
        - math.fsum(harvest.values())
    ) <= VOLUME_TOLERANCE_M3
    checks["centroid_baseline_matches_v9"] = all(
        abs(assignment.centroid_haversine_km - assignment.v9_distance_km) <= 1e-4
        and abs(assignment.centroid_road_km - assignment.v9_road_distance_km)
        <= 1e-3
        for assignment in assignments
    )

    band_cell_totals: dict[tuple[str, str], dict[str, float]] = defaultdict(
        lambda: {"centroid": 0.0, "node": 0.0}
    )
    for band_row in band_rows:
        cell = (band_row["province"], band_row["facility_class"])
        band_cell_totals[cell]["centroid"] += float(
            band_row["centroid_assigned_m3"]
        )
        band_cell_totals[cell]["node"] += float(band_row["node_assigned_m3"])
    checks["band_volume_conservation"] = all(
        abs(values["centroid"] - assigned_by_cell[cell])
        <= VOLUME_TOLERANCE_M3
        and abs(values["node"] - assigned_by_cell[cell])
        <= VOLUME_TOLERANCE_M3
        for cell, values in band_cell_totals.items()
    )
    checks["band_cell_coverage"] = set(band_cell_totals) == set(assigned_by_cell)

    share_sums: dict[tuple[str, str], dict[str, float]] = defaultdict(
        lambda: {"centroid": 0.0, "node": 0.0}
    )
    for band_row in band_rows:
        cell = (band_row["province"], band_row["facility_class"])
        share_sums[cell]["centroid"] += float(band_row["centroid_share"])
        share_sums[cell]["node"] += float(band_row["node_share"])
    checks["band_shares_sum_to_one"] = all(
        abs(values["centroid"] - 1.0) <= 1e-6
        and abs(values["node"] - 1.0) <= 1e-6
        for values in share_sums.values()
    )

    checks["threshold_partitions"] = all(
        abs(
            float(row["centroid_within_150_m3_y"])
            + float(row["centroid_gt150_m3_y"])
            - float(row["assigned_m3"])
        )
        <= VOLUME_TOLERANCE_M3
        and abs(
            float(row["node_within_150_m3_y"])
            + float(row["node_gt150_m3_y"])
            - float(row["assigned_m3"])
        )
        <= VOLUME_TOLERANCE_M3
        and abs(
            float(row["centroid_within_300_m3_y"])
            + float(row["centroid_gt300_m3_y"])
            - float(row["assigned_m3"])
        )
        <= VOLUME_TOLERANCE_M3
        and abs(
            float(row["node_within_300_m3_y"])
            + float(row["node_gt300_m3_y"])
            - float(row["assigned_m3"])
        )
        <= VOLUME_TOLERANCE_M3
        and float(row["node_gt300_m3_y"])
        <= float(row["node_gt150_m3_y"]) + VOLUME_TOLERANCE_M3
        and float(row["centroid_gt300_m3_y"])
        <= float(row["centroid_gt150_m3_y"]) + VOLUME_TOLERANCE_M3
        for row in gap_rows
    )

    checks["capacity_partitions"] = all(
        float(row["centroid_local_capacity_within_50_m3_y"])
        <= float(row["centroid_local_capacity_within_150_m3_y"])
        + CAPACITY_TOLERANCE_M3
        and float(row["centroid_local_capacity_within_150_m3_y"])
        <= float(row["centroid_local_capacity_within_300_m3_y"])
        + CAPACITY_TOLERANCE_M3
        and float(row["centroid_local_capacity_within_300_m3_y"])
        <= float(row["local_capacity_total_m3_y"]) + CAPACITY_TOLERANCE_M3
        and float(row["node_weighted_local_capacity_within_50_m3_y"])
        <= float(row["node_weighted_local_capacity_within_150_m3_y"])
        + CAPACITY_TOLERANCE_M3
        and float(row["node_weighted_local_capacity_within_150_m3_y"])
        <= float(row["node_weighted_local_capacity_within_300_m3_y"])
        + CAPACITY_TOLERANCE_M3
        for row in gap_rows
    )

    checks["capacity_gap_identity"] = all(
        abs(
            float(row["centroid_local_access_gap_within_150_m3_y"])
            - max(
                0.0,
                float(row["assigned_m3"])
                - float(row["centroid_local_capacity_within_150_m3_y"]),
            )
        )
        <= CAPACITY_TOLERANCE_M3
        and abs(
            float(row["node_local_access_gap_within_150_m3_y"])
            - max(
                0.0,
                float(row["assigned_m3"])
                - float(row["node_weighted_local_capacity_within_150_m3_y"]),
            )
        )
        <= CAPACITY_TOLERANCE_M3
        and abs(
            float(row["centroid_local_access_gap_within_300_m3_y"])
            - max(
                0.0,
                float(row["assigned_m3"])
                - float(row["centroid_local_capacity_within_300_m3_y"]),
            )
        )
        <= CAPACITY_TOLERANCE_M3
        and abs(
            float(row["node_local_access_gap_within_300_m3_y"])
            - max(
                0.0,
                float(row["assigned_m3"])
                - float(row["node_weighted_local_capacity_within_300_m3_y"]),
            )
        )
        <= CAPACITY_TOLERANCE_M3
        for row in gap_rows
    )

    checks["cost_arithmetic"] = all(
        abs(
            float(row["delta_transport_cost_cad"])
            - (
                float(row["node_transport_cost_cad"])
                - float(row["centroid_transport_cost_cad"])
            )
        )
        <= 1e-3
        and abs(
            float(row["centroid_transport_cost_cad_m3"])
            - float(row["centroid_transport_cost_cad"])
            / float(row["assigned_m3"])
        )
        <= 1e-6
        and abs(
            float(row["node_transport_cost_cad_m3"])
            - float(row["node_transport_cost_cad"])
            / float(row["assigned_m3"])
        )
        <= 1e-6
        for row in gap_rows
        if float(row["assigned_m3"]) > 0.0
    )

    checks["province_rollup_conservation"] = abs(
        math.fsum(float(row["assigned_m3"]) for row in province_rows)
        - math.fsum(harvest.values())
    ) <= VOLUME_TOLERANCE_M3 and abs(
        math.fsum(float(row["node_gt150_m3_y"]) for row in province_rows)
        - math.fsum(float(row["node_gt150_m3_y"]) for row in gap_rows)
    ) <= VOLUME_TOLERANCE_M3

    sensitivity_by_basis: dict[str, dict[str, float]] = defaultdict(
        lambda: defaultdict(float)
    )
    for sensitivity_row in sensitivity_rows:
        basis = sensitivity_row["weight_basis"]
        sensitivity_by_basis[basis]["assigned"] += float(
            sensitivity_row["assigned_m3"]
        )
        if sensitivity_row["level"] == "national":
            sensitivity_by_basis[basis]["national_assigned"] = float(
                sensitivity_row["assigned_m3"]
            )
    checks["weight_sensitivity_conservation"] = all(
        abs(values["national_assigned"] - math.fsum(harvest.values()))
        <= VOLUME_TOLERANCE_M3
        for values in sensitivity_by_basis.values()
    )

    checks["distribution_ordering"] = all(
        distribution.minimum_km
        <= distribution.p10_km
        <= distribution.p25_km
        <= distribution.median_km
        <= distribution.p75_km
        <= distribution.p90_km
        <= distribution.maximum_km
        and distribution.minimum_km
        <= distribution.mean_km
        <= distribution.maximum_km
        for distribution in distributions
    )
    checks["distribution_distance_nonnegative"] = all(
        distribution.minimum_km >= 0.0
        and distribution.p90_km >= 0.0
        and distribution.maximum_km >= 0.0
        for distribution in distributions
    )
    checks["centroid_percentiles_valid"] = all(
        0.0
        <= float(row["centroid_median_percentile_in_node_distribution"])
        <= 100.0
        for row in percentiles
    )

    checks["osrm_sample_count"] = len(sample_rows) == int(
        osrm_summary.get("requests_attempted", 0)
    )
    checks["osrm_sample_arithmetic"] = all(
        (
            row["osrm_status"] != "ok"
            or (
                float(row["osrm_road_km"]) > 0.0
                and float(row["osrm_duration_h"]) > 0.0
                and float(row["node_road_haversine_ratio"]) > 0.0
                and abs(
                    float(row["node_factor_error_pct"])
                    - 100.0
                    * (
                        float(row["node_factor_km"])
                        - float(row["osrm_road_km"])
                    )
                    / float(row["osrm_road_km"])
                )
                <= 1e-6
            )
        )
        for row in sample_rows
    )

    all_rows: list[Mapping[str, Any]] = [
        *band_rows,
        *gap_rows,
        *province_rows,
        *sensitivity_rows,
        *sample_rows,
    ]
    finite, nonnegative = _check_numeric_rows(all_rows)
    checks["no_nan_or_infinite"] = finite
    checks["no_negative_values"] = nonnegative
    checks["no_nan_or_negative"] = finite and nonnegative

    checks["labels"] = (
        summary_labels["evidence_label"] == EVIDENCE_LABEL
        and summary_labels["observed"] is False
        and summary_labels["policy_grade"] is False
        and summary_labels["national_claim"] is False
        and summary_labels["assignment_change"] is False
        and summary_labels["official_osecan_changed"] is False
        and summary_labels["ui_changed"] is False
        and summary_labels["siting_optimization"] is False
        and summary_labels["greenfield_optimization"] is False
        and summary_labels["policy_claim"] is False
    )
    checks["no_assignment_change"] = (
        summary_labels["assignment_change"] is False
    )
    checks["no_ui_or_official_osecan_change"] = (
        summary_labels["official_osecan_changed"] is False
        and summary_labels["ui_changed"] is False
    )

    all_passed = all(checks.values())
    if not all_passed:
        failed = [name for name, passed in checks.items() if not passed]
        raise SupplyNodeDistanceError(
            "supply-node distance validation failed: " + ", ".join(failed)
        )
    return {"checks": checks, "all_passed": all_passed}


# ---------------------------------------------------------------------------
# Summary and report.
# ---------------------------------------------------------------------------

def _pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    mean_x = math.fsum(xs) / len(xs)
    mean_y = math.fsum(ys) / len(ys)
    numerator = math.fsum(
        (x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True)
    )
    denominator = math.sqrt(
        math.fsum((x - mean_x) ** 2 for x in xs)
        * math.fsum((y - mean_y) ** 2 for y in ys)
    )
    if denominator <= 0.0:
        return None
    return numerator / denominator


def _national_metrics(
    rows: Sequence[Mapping[str, Any]], prefix: str
) -> dict[str, float]:
    assigned = math.fsum(float(row["assigned_m3"]) for row in rows)
    within_150 = math.fsum(
        float(row[f"{prefix}_within_150_m3_y"]) for row in rows
    )
    within_300 = math.fsum(
        float(row[f"{prefix}_within_300_m3_y"]) for row in rows
    )
    cost = math.fsum(
        float(row[f"{prefix}_transport_cost_cad"]) for row in rows
    )
    volume_distance = math.fsum(
        float(row["assigned_m3"])
        * float(row[f"{prefix}_mean_distance_km"])
        for row in rows
    )
    return {
        "assigned_m3": assigned,
        "within_150_m3_y": within_150,
        "gt150_m3_y": assigned - within_150,
        "gt150_share": (
            (assigned - within_150) / assigned if assigned > 0.0 else 0.0
        ),
        "within_300_m3_y": within_300,
        "gt300_m3_y": assigned - within_300,
        "gt300_share": (
            (assigned - within_300) / assigned if assigned > 0.0 else 0.0
        ),
        "mean_distance_km": (
            volume_distance / assigned if assigned > 0.0 else 0.0
        ),
        "transport_cost_cad": cost,
        "transport_cost_cad_m3": (
            cost / assigned if assigned > 0.0 else 0.0
        ),
        "transport_cost_cad_gj": (
            cost / (assigned * ENERGY_GJ_PER_M3) if assigned > 0.0 else 0.0
        ),
    }


def build_summary(
    *,
    generated_utc: str,
    nodes: Sequence[SupplyNode],
    harvest: Mapping[str, float],
    rsyc_diagnostics: Mapping[str, Any],
    centroid_methods: Mapping[str, str],
    facilities: Sequence[Facility],
    assignments: Sequence[AssignmentRow],
    band_rows: Sequence[Mapping[str, Any]],
    gap_rows: Sequence[Mapping[str, Any]],
    province_rows: Sequence[Mapping[str, Any]],
    sensitivity_rows: Sequence[Mapping[str, Any]],
    distributions: Sequence[Distribution],
    percentiles: Sequence[Mapping[str, Any]],
    sample_rows: Sequence[Mapping[str, Any]],
    osrm_summary: Mapping[str, Any],
    validation: Mapping[str, Any],
    inputs: Mapping[str, Any],
) -> dict[str, Any]:
    centroid_metrics = _national_metrics(gap_rows, "centroid")
    node_metrics = _national_metrics(gap_rows, "node")
    centroid_bias = {
        "mean_distance_delta_km": (
            node_metrics["mean_distance_km"]
            - centroid_metrics["mean_distance_km"]
        ),
        "mean_distance_delta_pct": (
            100.0
            * (
                node_metrics["mean_distance_km"]
                - centroid_metrics["mean_distance_km"]
            )
            / centroid_metrics["mean_distance_km"]
            if centroid_metrics["mean_distance_km"] > 0.0
            else None
        ),
        "gt150_share_delta_pp": 100.0
        * (node_metrics["gt150_share"] - centroid_metrics["gt150_share"]),
        "gt300_share_delta_pp": 100.0
        * (node_metrics["gt300_share"] - centroid_metrics["gt300_share"]),
        "gt150_m3_delta": (
            node_metrics["gt150_m3_y"] - centroid_metrics["gt150_m3_y"]
        ),
        "gt300_m3_delta": (
            node_metrics["gt300_m3_y"] - centroid_metrics["gt300_m3_y"]
        ),
        "transport_cost_delta_cad": (
            node_metrics["transport_cost_cad"]
            - centroid_metrics["transport_cost_cad"]
        ),
        "transport_cost_delta_pct": (
            100.0
            * (
                node_metrics["transport_cost_cad"]
                - centroid_metrics["transport_cost_cad"]
            )
            / centroid_metrics["transport_cost_cad"]
            if centroid_metrics["transport_cost_cad"] > 0.0
            else None
        ),
        "transport_cost_cad_m3_delta": (
            node_metrics["transport_cost_cad_m3"]
            - centroid_metrics["transport_cost_cad_m3"]
        ),
        "remaining_bias_note": (
            "The centroid-origin result is the measured bias baseline. "
            "Node-origin recomputation removes that measured province-centroid "
            "bias by construction, but does not remove within-tile harvest "
            "heterogeneity, road-factor transfer error, or off-road connector "
            "error. The OSRM paired sample quantifies the road-factor transfer "
            "and endpoint-snap uncertainty."
        ),
    }

    node_summary: dict[str, dict[str, Any]] = {}
    for province in PROVINCE_ORDER:
        province_nodes = [node for node in nodes if node.province == province]
        if not province_nodes:
            continue
        weights = [node.weight for node in province_nodes]
        top = max(province_nodes, key=lambda node: (node.weight, node.node_id))
        node_summary[province] = {
            "node_count": len(province_nodes),
            "harvest_m3": math.fsum(node.harvest_m3 for node in province_nodes),
            "production_m3": math.fsum(
                node.production_m3 for node in province_nodes
            ),
            "scanfi_area_ha": math.fsum(
                node.scanfi_area_ha for node in province_nodes
            ),
            "weight_sum": math.fsum(weights),
            "area_weight_sum": math.fsum(
                node.area_weight for node in province_nodes
            ),
            "top_node_id": top.node_id,
            "top_node_share": top.weight,
            "top_node_harvest_m3": top.harvest_m3,
            "hhi": math.fsum(weight * weight for weight in weights),
            "min_weight": min(weights),
            "max_weight": max(weights),
        }

    correlation_nodes = [
        node for node in nodes if node.area_weight > 0.0
    ]
    weight_correlation = _pearson(
        [node.weight for node in correlation_nodes],
        [node.area_weight for node in correlation_nodes],
    )

    province_bias = []
    for row in province_rows:
        province_bias.append(
            {
                "province": row["province"],
                "assigned_m3": float(row["assigned_m3"]),
                "centroid_mean_distance_km": float(
                    row["centroid_mean_distance_km"]
                ),
                "node_mean_distance_km": float(row["node_mean_distance_km"]),
                "delta_mean_distance_km": float(
                    row["delta_mean_distance_km"]
                ),
                "delta_gt150_share_pp": float(row["delta_gt150_share_pp"]),
                "delta_gt300_share_pp": float(row["delta_gt300_share_pp"]),
                "delta_transport_cost_cad_m3": float(
                    row["delta_transport_cost_cad_m3"]
                ),
            }
        )
    province_bias.sort(
        key=lambda row: -abs(row["delta_mean_distance_km"])
    )

    top_nodes = sorted(
        nodes, key=lambda node: (-node.harvest_m3, node.node_id)
    )[:15]
    top_node_rows = [
        {
            "node_id": node.node_id,
            "province": node.province,
            "lat": node.lat,
            "lon": node.lon,
            "weight": node.weight,
            "harvest_m3": node.harvest_m3,
            "production_m3": node.production_m3,
            "area_weight": node.area_weight,
            "scanfi_area_ha": node.scanfi_area_ha,
        }
        for node in top_nodes
    ]

    national_distribution = next(
        distribution
        for distribution in distributions
        if distribution.scope == "national"
        and distribution.origin_basis == "node_weighted"
    )
    national_centroid_distribution = next(
        distribution
        for distribution in distributions
        if distribution.scope == "national"
        and distribution.origin_basis == "centroid"
    )
    distribution_delta = {
        "mean_delta_km": (
            national_distribution.mean_km
            - national_centroid_distribution.mean_km
        ),
        "median_delta_km": (
            national_distribution.median_km
            - national_centroid_distribution.median_km
        ),
        "p90_delta_km": (
            national_distribution.p90_km
            - national_centroid_distribution.p90_km
        ),
    }

    return {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": generated_utc,
        "status": "complete",
        "labels": {
            "evidence_label": EVIDENCE_LABEL,
            "observed": OBSERVED,
            "policy_grade": POLICY_GRADE,
            "national_claim": NATIONAL_CLAIM,
            "assignment_change": ASSIGNMENT_CHANGE,
            "official_osecan_changed": OFFICIAL_OSECAN_CHANGED,
            "ui_changed": UI_CHANGED,
            "siting_optimization": SITING_OPTIMIZATION,
            "greenfield_optimization": GREENFIELD_OPTIMIZATION,
            "policy_claim": POLICY_CLAIM,
            "capacity_change": False,
        },
        "method": {
            "artifact_role": (
                "replace province-centroid origins with open RSYC harvest "
                "supply nodes and recompute screening distances, bands, "
                "transport cost and local-capacity access without changing "
                "the v9 assignment"
            ),
            "node_basis": (
                "RSYC 150-km tile x province cell with a geographic province "
                "intersection; node location is the tile-province intersection "
                "centroid (representative point when the centroid is outside)"
            ),
            "node_weight": (
                "RSYC age-100 standing volume (m3/ha at age 100 times the "
                "geographically located part of the tile-province cell), "
                "normalised within province"
            ),
            "area_weight": (
                "SCANFI forest area allocated from ecodistrict-province totals "
                "to tile-province cells in proportion to tile ecodistrict "
                "intersection area; transparency and sensitivity weight only"
            ),
            "harvest_allocation": (
                "119 Mm3 provincial harvest x node production weight; "
                "province totals are preserved and no node is assigned volume "
                "outside its province"
            ),
            "fallback_handling": (
                "RSYC district-fallback area with no province intersection is "
                "excluded from node production and renormalised within "
                "province; it is not given a fabricated location"
            ),
            "distance_basis": (
                "haversine from node or province centroid to eligible facility "
                "coordinate, multiplied by the facility-province road factor "
                "from facility_road_distance_audit_v2"
            ),
            "assignment_basis": (
                "v9 province_capacity_floor assignment; facility targets and "
                "assigned volumes are unchanged. Node-origin volume is the "
                "row volume times the origin-province node weight."
            ),
            "band_definition": {
                label: {
                    "low_km_inclusive": low,
                    "high_km_exclusive": high,
                }
                for label, low, high in BANDS
            },
            "transport_cost": (
                "transport_cost_v1 base curve at 52.5 m3 payload and empty "
                "return, applied to each centroid or node distance"
            ),
            "capacity_access": (
                "same-province eligible capacity of the assigned facility "
                "class within 50/150/300 km; node capacity is the "
                "harvest-weighted expectation across nodes and therefore "
                "double-counts facilities across nodes by design"
            ),
            "osrm_sample": (
                "audit-v2 81-pair facility sample re-originated at the "
                "highest-weight node of each origin province; paired against "
                "the audit-v2 centroid routes; public OSRM demo at 1.2 s "
                "spacing, no paid key"
            ),
            "harvest_total_m3": math.fsum(harvest.values()),
            "energy_gj_per_m3": ENERGY_GJ_PER_M3,
        },
        "nodes": {
            "count": len(nodes),
            "provinces_with_nodes": len(node_summary),
            "province_summary": node_summary,
            "rsyc_diagnostics": dict(rsyc_diagnostics),
            "production_area_weight_correlation": weight_correlation,
            "centroid_methods": dict(centroid_methods),
            "top_nodes": top_node_rows,
        },
        "distance_distributions": [
            {
                "scope": distribution.scope,
                "origin_basis": distribution.origin_basis,
                "n_pairs": distribution.n_pairs,
                "n_origins": distribution.n_origins,
                "n_facilities": distribution.n_facilities,
                "mean_km": distribution.mean_km,
                "minimum_km": distribution.minimum_km,
                "p10_km": distribution.p10_km,
                "p25_km": distribution.p25_km,
                "median_km": distribution.median_km,
                "p75_km": distribution.p75_km,
                "p90_km": distribution.p90_km,
                "maximum_km": distribution.maximum_km,
            }
            for distribution in distributions
        ],
        "centroid_percentiles": [dict(row) for row in percentiles],
        "band_comparison": {
            "national": {
                "centroid": centroid_metrics,
                "node": node_metrics,
                "delta": {
                    key: node_metrics[key] - centroid_metrics[key]
                    for key in (
                        "assigned_m3",
                        "within_150_m3_y",
                        "gt150_m3_y",
                        "within_300_m3_y",
                        "gt300_m3_y",
                        "mean_distance_km",
                        "transport_cost_cad",
                        "transport_cost_cad_m3",
                        "transport_cost_cad_gj",
                    )
                },
            },
            "provinces": [dict(row) for row in province_rows],
            "cells": len(gap_rows),
            "band_rows": len(band_rows),
        },
        "centroid_bias": {
            **centroid_bias,
            "distribution_delta": distribution_delta,
            "province_ranking": province_bias,
        },
        "weight_sensitivity": [dict(row) for row in sensitivity_rows],
        "osrm_sample": dict(osrm_summary),
        "osrm_sample_rows": len(sample_rows),
        "counts": {
            "nodes": len(nodes),
            "facilities": len(facilities),
            "assignments": len(assignments),
            "band_rows": len(band_rows),
            "gap_rows": len(gap_rows),
            "province_rows": len(province_rows),
            "sensitivity_rows": len(sensitivity_rows),
            "osrm_sample_rows": len(sample_rows),
        },
        "validation": dict(validation),
        "inputs": dict(inputs),
        "caveats": [
            (
                "Node origins are RSYC tile-province intersection centroids, "
                "not actual harvest blocks, cutblocks, millsheds, or observed "
                "truck trip origins. Within-tile harvest heterogeneity remains."
            ),
            (
                "RSYC production weights are open model estimates of age-100 "
                "standing volume, not observed annual harvest. The 119 Mm3 "
                "provincial control total is a planning case, not a legal AAC."
            ),
            (
                "The assignment is unchanged: v9 facility targets and volumes "
                "are retained. Node-origin volumes are a weighted re-origin "
                "of the same assignment, not a new allocation or optimization."
            ),
            (
                "Node-weighted local capacity is an expected accessibility "
                "measure that double-counts facilities across nodes. It is "
                "not an additional capacity stock and not a siting constraint."
            ),
            (
                "Road distances use documented province factors and a paired "
                "OSRM demo sample. OSRM endpoint snaps, resource roads, winter "
                "roads, ferries, weight limits, and log-truck restrictions are "
                "not validated; routed distances omit the off-road connector."
            ),
            (
                "OSRM may route through the United States where that is the "
                "shortest road path; no border delay or customs cost is "
                "modelled."
            ),
            (
                "This is a model-estimate screening artifact. It is not a "
                "national statistic, national average, policy recommendation, "
                "official OSeCAN result, UI product, or greenfield optimization."
            ),
        ],
        "reproduce": (
            ".venv/bin/python -m "
            "workflows.national.facilities.supply_node_distance_v1"
        ),
    }


# ---------------------------------------------------------------------------
# Output field definitions and writers.
# ---------------------------------------------------------------------------

NODE_FIELDS = [
    "node_id",
    "tile_id",
    "province",
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
    "evidence_label",
    "observed",
    "policy_grade",
]
PROVINCE_WEIGHT_FIELDS = [
    "province",
    "node_count",
    "harvest_m3",
    "production_m3",
    "scanfi_area_ha",
    "weight_sum",
    "area_weight_sum",
    "top_node_id",
    "top_node_share",
    "top_node_harvest_m3",
    "hhi",
    "min_weight",
    "max_weight",
    "evidence_label",
    "observed",
    "policy_grade",
]
SAMPLE_FIELDS = [
    "pair_id",
    "province",
    "node_id",
    "node_lat",
    "node_lon",
    "node_weight",
    "node_harvest_m3",
    "facility_id",
    "facility_name",
    "facility_class",
    "facility_province",
    "facility_lat",
    "facility_lon",
    "node_haversine_km",
    "node_province_road_factor",
    "node_factor_km",
    "osrm_status",
    "osrm_http_status",
    "osrm_road_km",
    "osrm_duration_h",
    "node_road_haversine_ratio",
    "node_factor_error_pct",
    "centroid_haversine_km",
    "centroid_factor_km",
    "centroid_osrm_road_km",
    "centroid_osrm_duration_h",
    "centroid_road_haversine_ratio",
    "centroid_factor_error_pct",
    "node_minus_centroid_ratio",
    "origin_snap_km",
    "destination_snap_km",
    "centroid_origin_snap_km",
    "centroid_destination_snap_km",
    "snap_warning",
    "error",
    "evidence_label",
    "observed",
    "policy_grade",
]
DISTRIBUTION_COMPARISON_FIELDS = [
    "scope",
    "n_nodes",
    "n_facilities",
    "node_pairs",
    "centroid_mean_km",
    "node_weighted_mean_km",
    "delta_mean_km",
    "centroid_median_km",
    "node_weighted_median_km",
    "delta_median_km",
    "centroid_p90_km",
    "node_weighted_p90_km",
    "delta_p90_km",
    "node_unweighted_mean_km",
    "centroid_median_percentile_in_node_distribution",
    "evidence_label",
    "observed",
    "policy_grade",
]
BAND_FIELDS = [
    "province",
    "facility_class",
    "distance_band",
    "band_low_km",
    "band_high_km",
    "assigned_m3",
    "centroid_assigned_m3",
    "node_assigned_m3",
    "delta_assigned_m3",
    "centroid_share",
    "node_share",
    "delta_share_pp",
    "centroid_mean_distance_km",
    "node_mean_distance_km",
    "centroid_transport_cost_cad",
    "node_transport_cost_cad",
    "delta_transport_cost_cad",
    "evidence_label",
    "observed",
    "policy_grade",
]
GAP_FIELDS = [
    "province",
    "facility_class",
    "assigned_m3",
    "assigned_share_of_province",
    "centroid_local_capacity_within_50_m3_y",
    "node_weighted_local_capacity_within_50_m3_y",
    "centroid_within_50_m3_y",
    "centroid_within_150_m3_y",
    "centroid_within_300_m3_y",
    "node_within_50_m3_y",
    "node_within_150_m3_y",
    "node_within_300_m3_y",
    "centroid_gt150_m3_y",
    "node_gt150_m3_y",
    "centroid_gt150_share",
    "node_gt150_share",
    "delta_gt150_share_pp",
    "centroid_gt300_m3_y",
    "node_gt300_m3_y",
    "centroid_gt300_share",
    "node_gt300_share",
    "delta_gt300_share_pp",
    "centroid_mean_distance_km",
    "node_mean_distance_km",
    "delta_mean_distance_km",
    "centroid_transport_cost_cad",
    "node_transport_cost_cad",
    "delta_transport_cost_cad",
    "centroid_transport_cost_cad_m3",
    "node_transport_cost_cad_m3",
    "delta_transport_cost_cad_m3",
    "centroid_transport_cost_cad_gj",
    "node_transport_cost_cad_gj",
    "centroid_local_capacity_within_150_m3_y",
    "node_weighted_local_capacity_within_150_m3_y",
    "centroid_local_capacity_within_300_m3_y",
    "node_weighted_local_capacity_within_300_m3_y",
    "local_capacity_total_m3_y",
    "centroid_local_access_gap_within_150_m3_y",
    "node_local_access_gap_within_150_m3_y",
    "centroid_local_access_gap_within_300_m3_y",
    "node_local_access_gap_within_300_m3_y",
    "capacity_aggregation",
    "localization_reference_km",
    "evidence_label",
    "observed",
    "policy_grade",
]
PROVINCE_GAP_FIELDS = [
    "province",
    "assigned_m3",
    "classes_with_assignment",
    "centroid_within_150_m3_y",
    "node_within_150_m3_y",
    "centroid_gt150_m3_y",
    "node_gt150_m3_y",
    "centroid_gt150_share",
    "node_gt150_share",
    "delta_gt150_share_pp",
    "centroid_within_300_m3_y",
    "node_within_300_m3_y",
    "centroid_gt300_m3_y",
    "node_gt300_m3_y",
    "centroid_gt300_share",
    "node_gt300_share",
    "delta_gt300_share_pp",
    "centroid_mean_distance_km",
    "node_mean_distance_km",
    "delta_mean_distance_km",
    "centroid_transport_cost_cad",
    "node_transport_cost_cad",
    "delta_transport_cost_cad",
    "centroid_transport_cost_cad_m3",
    "node_transport_cost_cad_m3",
    "delta_transport_cost_cad_m3",
    "centroid_local_capacity_within_150_m3_y",
    "node_weighted_local_capacity_within_150_m3_y",
    "centroid_local_capacity_within_300_m3_y",
    "node_weighted_local_capacity_within_300_m3_y",
    "centroid_local_access_gap_within_150_m3_y",
    "node_local_access_gap_within_150_m3_y",
    "centroid_local_access_gap_within_300_m3_y",
    "node_local_access_gap_within_300_m3_y",
    "evidence_label",
    "observed",
    "policy_grade",
]
SENSITIVITY_FIELDS = [
    "weight_basis",
    "level",
    "province",
    "assigned_m3",
    "within_150_m3_y",
    "gt150_m3_y",
    "gt150_share",
    "within_300_m3_y",
    "gt300_m3_y",
    "gt300_share",
    "mean_distance_km",
    "transport_cost_cad",
    "transport_cost_cad_m3",
    "transport_cost_cad_gj",
    "evidence_label",
    "observed",
    "policy_grade",
]
PROVENANCE_FIELDS = [
    "source_id",
    "role",
    "name",
    "licence",
    "url",
    "path",
    "sha256",
    "notes",
]


def _node_csv_rows(
    nodes: Sequence[SupplyNode],
) -> list[dict[str, Any]]:
    return [
        {
            "node_id": node.node_id,
            "tile_id": node.tile_id,
            "province": node.province,
            "lat": _format_float(node.lat),
            "lon": _format_float(node.lon),
            "centroid_method": node.centroid_method,
            "production_m3": _format_float(node.production_m3),
            "located_area_ha": _format_float(node.located_area_ha),
            "raw_area_ha": _format_float(node.raw_area_ha),
            "fallback_area_ha": _format_float(node.fallback_area_ha),
            "scanfi_area_ha": _format_float(node.scanfi_area_ha),
            "weight": _format_float(node.weight),
            "area_weight": _format_float(node.area_weight),
            "province_production_m3": _format_float(
                node.province_production_m3
            ),
            "province_scanfi_area_ha": _format_float(
                node.province_scanfi_area_ha
            ),
            "province_harvest_m3": _format_float(node.province_harvest_m3),
            "harvest_m3": _format_float(node.harvest_m3),
            "evidence_label": EVIDENCE_LABEL,
            "observed": _bool_text(OBSERVED),
            "policy_grade": _bool_text(POLICY_GRADE),
        }
        for node in nodes
    ]


def _province_weight_rows(
    summary: Mapping[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for province, values in summary["nodes"]["province_summary"].items():
        rows.append(
            {
                "province": province,
                **{
                    key: (
                        _format_float(value)
                        if isinstance(value, float)
                        else value
                    )
                    for key, value in values.items()
                },
                "evidence_label": EVIDENCE_LABEL,
                "observed": _bool_text(OBSERVED),
                "policy_grade": _bool_text(POLICY_GRADE),
            }
        )
    return rows


def _distribution_comparison_rows(
    summary: Mapping[str, Any],
) -> list[dict[str, Any]]:
    distributions = summary["distance_distributions"]
    by_key = {
        (row["scope"], row["origin_basis"]): row for row in distributions
    }
    percentiles = {
        row["scope"]: row for row in summary["centroid_percentiles"]
    }
    scopes = ["national", *[
        province
        for province in PROVINCE_ORDER
        if (province, "node_weighted") in by_key
    ]]
    rows: list[dict[str, Any]] = []
    for scope in scopes:
        node = by_key[(scope, "node_weighted")]
        centroid = by_key[(scope, "centroid")]
        unit = by_key[(scope, "node_unweighted")]
        rows.append(
            {
                "scope": scope,
                "n_nodes": node["n_origins"],
                "n_facilities": node["n_facilities"],
                "node_pairs": node["n_pairs"],
                "centroid_mean_km": _format_float(centroid["mean_km"]),
                "node_weighted_mean_km": _format_float(node["mean_km"]),
                "delta_mean_km": _format_float(
                    node["mean_km"] - centroid["mean_km"]
                ),
                "centroid_median_km": _format_float(centroid["median_km"]),
                "node_weighted_median_km": _format_float(node["median_km"]),
                "delta_median_km": _format_float(
                    node["median_km"] - centroid["median_km"]
                ),
                "centroid_p90_km": _format_float(centroid["p90_km"]),
                "node_weighted_p90_km": _format_float(node["p90_km"]),
                "delta_p90_km": _format_float(
                    node["p90_km"] - centroid["p90_km"]
                ),
                "node_unweighted_mean_km": _format_float(unit["mean_km"]),
                "centroid_median_percentile_in_node_distribution": (
                    _format_float(
                        percentiles[scope][
                            "centroid_median_percentile_in_node_distribution"
                        ]
                    )
                ),
                "evidence_label": EVIDENCE_LABEL,
                "observed": _bool_text(OBSERVED),
                "policy_grade": _bool_text(POLICY_GRADE),
            }
        )
    return rows


def _input_hashes() -> dict[str, Any]:
    specs = [
        (
            "rsyc_tile_production_library",
            RSYC_LIBRARY_CSV,
            "node production weight and RSYC age-100 volume",
        ),
        (
            "rsyc_tiles",
            RSYC_TILES_GPKG,
            "tile geometry for node location",
        ),
        (
            "tile_ecodistrict_province_weights",
            TILE_ECODISTRICT_PROVINCE_CSV,
            "tile x ecodistrict x province area crosswalk",
        ),
        (
            "scanfi_ecodistrict_area_weights",
            SCANFI_ECODISTRICT_CSV,
            "SCANFI forest-area transparency weight",
        ),
        (
            "canada_adm1_geoboundaries_simplified",
            BOUNDARY_GEOJSON,
            "province geometry and centroids",
        ),
        (
            "provincial_harvest_119m",
            HARVEST_CSV,
            "119 Mm3 provincial harvest control",
        ),
        (
            "national_facility_capacity_v3",
            CAPACITY_CSV,
            "eligible facility coordinates, class and capacity",
        ),
        (
            "facility_road_distance_audit_v2_factors",
            FACTORS_CSV,
            "province road factors",
        ),
        (
            "national_facility_assignment_v9",
            V9_ASSIGNMENT_CSV,
            "unchanged assignment context",
        ),
        (
            "facility_road_distance_audit_v2_sample",
            AUDIT_V2_SAMPLE_CSV,
            "paired centroid OSRM sample for node re-origin",
        ),
        (
            "transport_cost_v1_parameters",
            TRANSPORT_PARAMETERS_CSV,
            "base transport cost curve parameters",
        ),
    ]
    return {
        name: {
            "path": str(path.relative_to(REPO_ROOT)),
            "sha256": _sha256(path),
            "role": role,
        }
        for name, path, role in specs
    }


def _provenance_rows(inputs: Mapping[str, Any]) -> list[dict[str, Any]]:
    sources = [
        (
            "rsyc_tile_production_library",
            "node production weight",
            "RSYC tile production library (project-derived)",
            "project-derived; see source metadata",
            "https://github.com/UBC-FRESH/clews-c2070-fresh-data",
            "RSYC age-100 standing volume and located tile-province area.",
        ),
        (
            "rsyc_tiles",
            "node geometry",
            "RSYC 150-km tile layer (project-derived)",
            "project-derived; see source metadata",
            "https://github.com/UBC-FRESH/clews-c2070-fresh-data",
            "Tile polygons used only to locate tile-province intersection nodes.",
        ),
        (
            "tile_ecodistrict_province_weights",
            "area crosswalk",
            "Tile x ecodistrict x province weights (project-derived)",
            "project-derived; see source metadata",
            "https://github.com/UBC-FRESH/clews-c2070-fresh-data",
            "Used to allocate SCANFI ecodistrict area to tile-province nodes.",
        ),
        (
            "scanfi_ecodistrict_area_weights",
            "area weight",
            "SCANFI ecodistrict area weights (project-derived)",
            "project-derived; see source metadata",
            "https://github.com/UBC-FRESH/clews-c2070-fresh-data",
            "Transparency and sensitivity weight only; not the assigned weight.",
        ),
        (
            "canada_adm1_geoboundaries_simplified",
            "province geometry",
            "GeoBoundaries Canada ADM1 simplified",
            "CC BY 4.0",
            "https://github.com/UBC-FRESH/clews-c2070-fresh-data",
            "Province polygons and the centroid baseline.",
        ),
        (
            "provincial_harvest_119m",
            "harvest control",
            "119 Mm3 provincial harvest allocation",
            "project-derived; see source metadata",
            "https://github.com/UBC-FRESH/clews-c2070-fresh-data",
            "Province harvest control totals distributed to nodes.",
        ),
        (
            "national_facility_capacity_v3",
            "facility universe",
            "National facility capacity v3",
            "project-derived; see source metadata",
            "https://github.com/UBC-FRESH/clews-c2070-fresh-data",
            "Eligible located facilities and capacity.",
        ),
        (
            "facility_road_distance_audit_v2_factors",
            "road factors",
            "Facility road-distance audit v2 province factors",
            "project-derived; see source metadata",
            "https://github.com/UBC-FRESH/clews-c2070-fresh",
            "Province road/haversine median ratios.",
        ),
        (
            "national_facility_assignment_v9",
            "assignment context",
            "National facility assignment v9",
            "project-derived; see source metadata",
            "https://github.com/UBC-FRESH/clews-c2070-fresh",
            "Facility targets and volumes, unchanged.",
        ),
        (
            "facility_road_distance_audit_v2_sample",
            "paired sample",
            "Facility road-distance audit v2 sample",
            "project-derived; see source metadata",
            "https://github.com/UBC-FRESH/clews-c2070-fresh",
            "Centroid OSRM routes paired with node-origin routes.",
        ),
        (
            "transport_cost_v1_parameters",
            "transport cost",
            "Transport cost v1 parameter register",
            "project-derived; see source metadata; underlying StatCan Open Licence",
            "https://github.com/UBC-FRESH/clews-c2070-fresh",
            "Base 52.5 m3 payload curve applied to distances.",
        ),
        (
            "osrm_demo",
            "routing",
            "OSRM public demo server",
            "ODbL-1.0",
            OSRM_BASE_URL,
            "Node-origin paired sample; public best-effort service, no paid key.",
        ),
        (
            "openstreetmap",
            "road data",
            "OpenStreetMap contributors",
            "ODbL-1.0",
            "https://www.openstreetmap.org/copyright",
            "Road network used by OSRM; attribution © OpenStreetMap contributors.",
        ),
    ]
    rows: list[dict[str, Any]] = []
    for source_id, role, name, licence, url, notes in sources:
        input_row = inputs.get(source_id, {})
        rows.append(
            {
                "source_id": source_id,
                "role": role,
                "name": name,
                "licence": licence,
                "url": url,
                "path": input_row.get("path", ""),
                "sha256": input_row.get("sha256", ""),
                "notes": notes,
            }
        )
    return rows


def _md_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join("---" for _ in headers) + "|",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return "\n".join(lines)


def _million(value: float) -> str:
    return f"{value / 1e6:,.2f}"


def _percent(value: float) -> str:
    return f"{value:.1%}"


def _num(value: float, digits: int = 1) -> str:
    if abs(value) < 0.5 * 10**-digits:
        return f"{0:.{digits}f}"
    return f"{value:,.{digits}f}"


def write_report(
    summary: Mapping[str, Any],
    band_rows: Sequence[Mapping[str, Any]],
    province_rows: Sequence[Mapping[str, Any]],
    sensitivity_rows: Sequence[Mapping[str, Any]],
) -> str:
    nodes = summary["nodes"]
    bias = summary["centroid_bias"]
    osrm = summary["osrm_sample"]
    national = summary["band_comparison"]["national"]
    centroid = national["centroid"]
    node = national["node"]
    labels = summary["labels"]
    band_by_label = {
        label: [
            row for row in band_rows if row["distance_band"] == label
        ]
        for label in BAND_ORDER
    }
    band_table: list[list[str]] = []
    for label in BAND_ORDER:
        rows = band_by_label[label]
        centroid_volume = math.fsum(
            float(row["centroid_assigned_m3"]) for row in rows
        )
        node_volume = math.fsum(float(row["node_assigned_m3"]) for row in rows)
        band_table.append(
            [
                label,
                _percent(centroid_volume / float(centroid["assigned_m3"])),
                _percent(node_volume / float(node["assigned_m3"])),
                _num(
                    100.0
                    * (node_volume - centroid_volume)
                    / float(node["assigned_m3"])
                ),
                _num(_weighted_band_mean(rows, "centroid")),
                _num(_weighted_band_mean(rows, "node")),
            ]
        )

    diagnostics = summary["nodes"]["rsyc_diagnostics"]
    fallback_m3 = diagnostics["fallback_production_m3"]
    fallback_share_pct = 100.0 * diagnostics["fallback_production_share"]
    nu_fallback_share_pct = 100.0 * diagnostics[
        "province_fallback_production_share"
    ].get("NU", 0.0)
    ab_sk_fallback_m3 = (
        diagnostics["province_fallback_production_m3"].get("AB", 0.0)
        + diagnostics["province_fallback_production_m3"].get("SK", 0.0)
    )

    distributions = {
        (row["scope"], row["origin_basis"]): row
        for row in summary["distance_distributions"]
    }
    percentiles = {
        row["scope"]: row for row in summary["centroid_percentiles"]
    }
    province_distribution_rows: list[list[str]] = []
    for province in PROVINCE_ORDER:
        if (province, "node_weighted") not in distributions:
            continue
        node_row = distributions[(province, "node_weighted")]
        centroid_row = distributions[(province, "centroid")]
        province_distribution_rows.append(
            [
                province,
                str(node_row["n_origins"]),
                str(node_row["n_facilities"]),
                _num(centroid_row["mean_km"]),
                _num(node_row["mean_km"]),
                _num(node_row["mean_km"] - centroid_row["mean_km"]),
                _num(centroid_row["median_km"]),
                _num(node_row["median_km"]),
                _num(node_row["median_km"] - centroid_row["median_km"]),
                _num(
                    percentiles[province][
                        "centroid_median_percentile_in_node_distribution"
                    ]
                ),
            ]
        )

    osrm_province_rows: list[list[str]] = []
    for province, values in osrm.get("province_medians", {}).items():
        osrm_province_rows.append(
            [
                province,
                str(values.get("n_ok", "")),
                _num(values.get("node_median_road_haversine_ratio", 0.0), 4),
                _num(values.get("audit_province_road_factor", 0.0), 4),
                _num(values.get("factor_delta_pct", 0.0), 2),
            ]
        )

    lines = [
        "# Supply-node distance v1",
        "",
        f"Generated: {summary['generated_utc']}",
        "",
        (
            "Status: **model estimate**. Open RSYC harvest supply nodes replace "
            "province-centroid origins; the v9 assignment is unchanged. "
            "`model_estimate`, `observed=false`, `policy_grade=false`. No "
            "national claim, no policy claim, no siting or greenfield "
            "optimization, and no UI or official OSeCAN change."
        ),
        "",
        "## 1. Node construction",
        "",
        (
            f"- **{nodes['count']:,} nodes** in "
            f"{nodes['provinces_with_nodes']} provinces from RSYC 150-km tile "
            "x province cells with a geographic province intersection."
        ),
        (
            "- Node location: centroid of the tile-province intersection "
            "(representative point when the centroid falls outside the "
            "intersection)."
        ),
        (
            "- Node weight: RSYC age-100 standing volume on the geographically "
            "located part of the tile-province cell, normalised within "
            "province."
        ),
        (
            "- 119 Mm3 provincial harvest is assigned as province harvest x "
            "node weight. Province totals and the 118.977 Mm3 control total are "
            "preserved."
        ),
        (
            "- RSYC district-fallback area with no province intersection is "
            f"excluded: {_million(fallback_m3)} Mm3 of raw RSYC production "
            f"({fallback_share_pct:.2f}%). AB and SK contribute "
            f"{_million(ab_sk_fallback_m3)} Mm3 of the excluded total; "
            f"Nunavut loses {nu_fallback_share_pct:.2f}% of its own RSYC "
            "production but has zero harvest. Remaining weights are "
            "renormalised within province. No fallback cell is given a "
            "fabricated location."
        ),
        (
            "- A SCANFI forest-area weight is carried for transparency and "
            "sensitivity. The production-area weight correlation is "
            f"{_num(nodes['production_area_weight_correlation'], 3) if nodes['production_area_weight_correlation'] is not None else 'n/a'}."
        ),
        "",
        _md_table(
            [
                "Province",
                "Nodes",
                "Harvest Mm3",
                "Top node share",
                "HHI",
                "Top node",
            ],
            [
                [
                    province,
                    str(values["node_count"]),
                    _million(values["harvest_m3"]),
                    _percent(values["top_node_share"]),
                    _num(values["hhi"], 3),
                    values["top_node_id"],
                ]
                for province, values in nodes["province_summary"].items()
            ],
        ),
        "",
        "## 2. Distance comparison",
        "",
        (
            "Distances are haversine to eligible facility coordinates "
            "multiplied by the facility-province road factor. The node figure "
            "is harvest-weighted across nodes; the centroid figure is the "
            "unweighted ADM1 polygon centroid."
        ),
        "",
        _md_table(
            [
                "Scope",
                "Centroid mean km",
                "Node weighted mean km",
                "Delta km",
                "Centroid median km",
                "Node weighted median km",
                "Centroid p90 km",
                "Node p90 km",
                "Centroid percentile in node distribution",
            ],
            [
                [
                    "national",
                    _num(next(
                        row["mean_km"]
                        for row in summary["distance_distributions"]
                        if row["scope"] == "national"
                        and row["origin_basis"] == "centroid"
                    )),
                    _num(next(
                        row["mean_km"]
                        for row in summary["distance_distributions"]
                        if row["scope"] == "national"
                        and row["origin_basis"] == "node_weighted"
                    )),
                    _num(bias["distribution_delta"]["mean_delta_km"]),
                    _num(next(
                        row["median_km"]
                        for row in summary["distance_distributions"]
                        if row["scope"] == "national"
                        and row["origin_basis"] == "centroid"
                    )),
                    _num(next(
                        row["median_km"]
                        for row in summary["distance_distributions"]
                        if row["scope"] == "national"
                        and row["origin_basis"] == "node_weighted"
                    )),
                    _num(next(
                        row["p90_km"]
                        for row in summary["distance_distributions"]
                        if row["scope"] == "national"
                        and row["origin_basis"] == "centroid"
                    )),
                    _num(next(
                        row["p90_km"]
                        for row in summary["distance_distributions"]
                        if row["scope"] == "national"
                        and row["origin_basis"] == "node_weighted"
                    )),
                    _num(next(
                        row["centroid_median_percentile_in_node_distribution"]
                        for row in summary["centroid_percentiles"]
                        if row["scope"] == "national"
                    )),
                ]
            ],
        ),
        "",
        (
            "The national centroid mean sits at the "
            f"{_num(next(row['centroid_median_percentile_in_node_distribution'] for row in summary['centroid_percentiles'] if row['scope'] == 'national'))} "
            "percentile of the node-facility distance distribution. The "
            "all-facility distribution is an accessibility surface; it is not "
            "the assigned-facility distance in Section 3."
        ),
        "",
        _md_table(
            [
                "Province",
                "Nodes",
                "Facilities",
                "Centroid mean km",
                "Node mean km",
                "Delta km",
                "Centroid median km",
                "Node median km",
                "Delta km",
                "Centroid percentile",
            ],
            province_distribution_rows,
        ),
        "",
        (
            "Section 2 uses every eligible facility in the origin province. "
            "Section 3 uses only the facilities that the unchanged v9 "
            "assignment actually selects. The two can move in opposite "
            "directions: the all-facility centroid can sit above most node "
            "pairs while the assigned-volume-weighted mean rises, because v9 "
            "targets a selected facility subset."
        ),
        "",
        "### Paired OSRM sample",
        "",
        (
            f"- {osrm.get('requests_attempted', 0)} requests to the public OSRM "
            f"demo server: {osrm.get('requests_ok', 0)} ok, "
            f"{osrm.get('requests_failed', 0)} failed; "
            f"{osrm.get('paired_ok_count', 0)} pairs have both node and "
            "centroid routes."
        ),
        (
            f"- Median node road/haversine ratio: "
            f"{_num(osrm['node_road_haversine_ratio']['median'], 4) if osrm['node_road_haversine_ratio']['median'] is not None else 'n/a'}; "
            f"centroid ratio: "
            f"{_num(osrm['centroid_road_haversine_ratio']['median'], 4) if osrm['centroid_road_haversine_ratio']['median'] is not None else 'n/a'}; "
            f"node minus centroid: "
            f"{_num(osrm['node_minus_centroid_ratio']['median'], 4) if osrm['node_minus_centroid_ratio']['median'] is not None else 'n/a'}."
        ),
        (
            f"- Median node factor error: "
            f"{_num(osrm['node_factor_error_pct']['median'], 2) if osrm['node_factor_error_pct']['median'] is not None else 'n/a'}%; "
            f"centroid factor error: "
            f"{_num(osrm['centroid_factor_error_pct']['median'], 2) if osrm['centroid_factor_error_pct']['median'] is not None else 'n/a'}%."
        ),
        (
            f"- Node origin snap: median "
            f"{_num(osrm['node_origin_snap_km']['median'], 2) if osrm['node_origin_snap_km']['median'] is not None else 'n/a'} km, "
            f"max "
            f"{_num(osrm['node_origin_snap_km']['max'], 2) if osrm['node_origin_snap_km']['max'] is not None else 'n/a'} km. "
            "Large snaps mean the node is not on the road network; routed "
            "distance is a lower bound because the off-road connector is not "
            "included."
        ),
        "",
        _md_table(
            [
                "Province",
                "Pairs ok",
                "Node median ratio",
                "Audit factor",
                "Node factor delta",
            ],
            osrm_province_rows,
        ),
        "",
        (
            "The node-origin factor delta is the top-node median "
            "road/haversine ratio relative to the audit-v2 province factor. "
            "It is a transfer diagnostic only; no factor is changed and no "
            "assignment is re-run. Positive values mean the centroid-fitted "
            "factor understates node-origin road distance; negative values "
            "mean it overstates it."
        ),
        "",
        (
            "The OSRM sample is a paired factor-transfer check, not a new "
            "routing product. If the sample is unavailable, the documented "
            "province factors still drive the full distance and band tables."
        ),
        "",
        "## 3. Band and transport-gap comparison",
        "",
        (
            "Bands: `[0,50)`, `[50,100)`, `[100,150)`, `[150,300)`, "
            "`[300,∞)` km. The assignment is unchanged; each v9 row is split "
            "across origin-province nodes by node weight."
        ),
        "",
        _md_table(
            [
                "Band",
                "Centroid share",
                "Node share",
                "Delta pp",
                "Centroid mean km",
                "Node mean km",
            ],
            band_table,
        ),
        "",
        (
            f"At the unchanged v9 assignment, the centroid-origin screen gives "
            f"{_million(centroid['gt150_m3_y'])} Mm3 "
            f"({_percent(centroid['gt150_share'])}) at or above 150 km and "
            f"{_million(centroid['gt300_m3_y'])} Mm3 "
            f"({_percent(centroid['gt300_share'])}) at or above 300 km. "
            f"Node origins give {_million(node['gt150_m3_y'])} Mm3 "
            f"({_percent(node['gt150_share'])}) and "
            f"{_million(node['gt300_m3_y'])} Mm3 "
            f"({_percent(node['gt300_share'])})."
        ),
        "",
        _md_table(
            [
                "Province",
                "Assigned Mm3",
                "Centroid >150",
                "Node >150",
                "Delta pp",
                "Centroid >300",
                "Node >300",
                "Delta pp",
                "Centroid mean km",
                "Node mean km",
                "Delta km",
            ],
            [
                [
                    row["province"],
                    _million(float(row["assigned_m3"])),
                    _percent(float(row["centroid_gt150_share"])),
                    _percent(float(row["node_gt150_share"])),
                    _num(float(row["delta_gt150_share_pp"]), 2),
                    _percent(float(row["centroid_gt300_share"])),
                    _percent(float(row["node_gt300_share"])),
                    _num(float(row["delta_gt300_share_pp"]), 2),
                    _num(float(row["centroid_mean_distance_km"])),
                    _num(float(row["node_mean_distance_km"])),
                    _num(float(row["delta_mean_distance_km"])),
                ]
                for row in province_rows
            ],
        ),
        "",
        "### Local-capacity access",
        "",
        (
            "Node local capacity is the harvest-weighted expectation of "
            "same-province, same-class capacity within 150 or 300 km. It "
            "double-counts facilities across nodes and is an accessibility "
            "measure, not an additional capacity stock. The gap is "
            "`max(0, assigned - expected accessible capacity)`."
        ),
        "",
        _md_table(
            [
                "Province",
                "Assigned Mm3",
                "Centroid cap 150 Mm3",
                "Node cap 150 Mm3",
                "Centroid gap 150 Mm3",
                "Node gap 150 Mm3",
                "Centroid cap 300 Mm3",
                "Node cap 300 Mm3",
            ],
            [
                [
                    row["province"],
                    _million(float(row["assigned_m3"])),
                    _million(
                        float(row["centroid_local_capacity_within_150_m3_y"])
                    ),
                    _million(
                        float(
                            row[
                                "node_weighted_local_capacity_within_150_m3_y"
                            ]
                        )
                    ),
                    _million(
                        float(row["centroid_local_access_gap_within_150_m3_y"])
                    ),
                    _million(
                        float(row["node_local_access_gap_within_150_m3_y"])
                    ),
                    _million(
                        float(row["centroid_local_capacity_within_300_m3_y"])
                    ),
                    _million(
                        float(
                            row[
                                "node_weighted_local_capacity_within_300_m3_y"
                            ]
                        )
                    ),
                ]
                for row in province_rows
            ],
        ),
        "",
        "## 4. Centroid bias",
        "",
        (
            f"- Assigned-volume-weighted mean distance: centroid "
            f"{_num(centroid['mean_distance_km'])} km, node "
            f"{_num(node['mean_distance_km'])} km; delta "
            f"{_num(bias['mean_distance_delta_km'])} km "
            f"({_num(bias['mean_distance_delta_pct'], 1) if bias['mean_distance_delta_pct'] is not None else 'n/a'}%)."
        ),
        (
            f"- >150 km share delta: {_num(bias['gt150_share_delta_pp'], 2)} "
            f"percentage points; >300 km share delta: "
            f"{_num(bias['gt300_share_delta_pp'], 2)} percentage points."
        ),
        (
            f"- Transport cost delta: "
            f"{_num(bias['transport_cost_delta_cad'] / 1e9, 3)} billion CAD "
            f"({_num(bias['transport_cost_delta_pct'], 2) if bias['transport_cost_delta_pct'] is not None else 'n/a'}%); "
            f"CAD/m3 delta {_num(bias['transport_cost_cad_m3_delta'], 2)}."
        ),
        (
            f"- National node-weighted distance distribution vs centroid: "
            f"mean delta {_num(bias['distribution_delta']['mean_delta_km'])} km, "
            f"median delta {_num(bias['distribution_delta']['median_delta_km'])} km, "
            f"p90 delta {_num(bias['distribution_delta']['p90_delta_km'])} km."
        ),
        "",
        _md_table(
            [
                "Province",
                "Centroid km",
                "Node km",
                "Delta km",
                ">150 delta pp",
                ">300 delta pp",
                "CAD/m3 delta",
            ],
            [
                [
                    row["province"],
                    _num(row["centroid_mean_distance_km"]),
                    _num(row["node_mean_distance_km"]),
                    _num(row["delta_mean_distance_km"]),
                    _num(row["delta_gt150_share_pp"], 2),
                    _num(row["delta_gt300_share_pp"], 2),
                    _num(row["delta_transport_cost_cad_m3"], 2),
                ]
                for row in bias["province_ranking"]
            ],
        ),
        "",
        (
            "**How much centroid bias remains?** The measured province-centroid "
            "bias is the centroid-origin deviation above; the node-origin "
            "recomputation removes that measured bias by construction. "
            "Residual uncertainty is within-tile harvest heterogeneity, "
            "node-to-facility road-factor transfer, and off-road endpoint "
            "connectors. The OSRM paired sample quantifies the factor and snap "
            "component; the within-tile component cannot be removed with "
            "150-km tiles and is not fabricated."
        ),
        "",
        "## 5. Weight-basis sensitivity",
        "",
        (
            "The assignment and distances are identical; only the within-province "
            "node weights change. This is a sensitivity check, not a second "
            "assignment."
        ),
        "",
        _md_table(
            [
                "Weight basis",
                "Assigned Mm3",
                ">150 share",
                ">300 share",
                "Mean km",
                "CAD/m3",
            ],
            [
                [
                    row["weight_basis"],
                    _million(float(row["assigned_m3"])),
                    _percent(float(row["gt150_share"])),
                    _percent(float(row["gt300_share"])),
                    _num(float(row["mean_distance_km"])),
                    _num(float(row["transport_cost_cad_m3"]), 2),
                ]
                for row in sensitivity_rows
                if row["level"] == "national"
            ],
        ),
        "",
        "## 6. Method and limits",
        "",
        (
            "- The v9 `province_capacity_floor` assignment is read-only; no "
            "facility target, assigned volume, capacity, or cross-border flow is "
            "changed."
        ),
        (
            "- Node volumes are proportional to RSYC age-100 standing volume on "
            "the located tile-province area, not observed harvest. The 119 Mm3 "
            "provincial control is a planning case, not a legal AAC."
        ),
        (
            "- Distances are road-factor screening distances, not observed "
            "routes. The OSRM demo sample is best-effort and public; resource "
            "roads, winter roads, ferries, weight limits, log-truck "
            "restrictions, and border delays are not validated."
        ),
        (
            "- Node local capacity is a harvest-weighted expectation and "
            "double-counts facilities across nodes. It is not a siting "
            "constraint or an additional capacity stock."
        ),
        (
            "- This is not a national statistic, national average, policy "
            "recommendation, official OSeCAN result, UI product, or greenfield "
            "optimization."
        ),
        "",
        "## 7. Labels and provenance",
        "",
        (
            f"- `evidence_label={labels['evidence_label']}`; "
            f"`observed={str(labels['observed']).lower()}`; "
            f"`policy_grade={str(labels['policy_grade']).lower()}`; "
            "`national_claim=false`; `assignment_change=false`; "
            "`official_osecan_changed=false`; `ui_changed=false`; "
            "`siting_optimization=false`; `greenfield_optimization=false`."
        ),
        (
            "- Inputs: RSYC tile production library and tiles, tile ecodistrict "
            "province weights, SCANFI ecodistrict area weights, GeoBoundaries "
            "ADM1, 119 Mm3 harvest, facility capacity v3, road-distance audit "
            "v2 factors and paired sample, transport cost v1 parameters, v9 "
            "assignment, and OSRM/OpenStreetMap."
        ),
        (
            "- OSRM: public demo server, ODbL-1.0, © OpenStreetMap "
            "contributors. No paid API key is used."
        ),
        "",
        f"Reproduce: `{summary['reproduce']}`",
    ]
    return "\n".join(lines) + "\n"


def _weighted_band_mean(
    rows: Sequence[Mapping[str, Any]], basis: str
) -> float:
    volume = math.fsum(float(row[f"{basis}_assigned_m3"]) for row in rows)
    if volume <= 0.0:
        return 0.0
    return math.fsum(
        float(row[f"{basis}_assigned_m3"])
        * float(row[f"{basis}_mean_distance_km"])
        for row in rows
    ) / volume


def _write_manifest(
    output_dir: Path,
    generated_utc: str,
    inputs: Mapping[str, Any],
    summary: Mapping[str, Any],
) -> None:
    output_paths = sorted(
        path
        for path in output_dir.iterdir()
        if path.is_file() and path.name != "manifest.json"
    )
    manifest = {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": generated_utc,
        "status": "complete",
        "labels": summary["labels"],
        "counts": summary["counts"],
        "inputs": dict(inputs),
        "outputs": {
            path.name: {
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
            for path in output_paths
        },
        "reproduce": summary["reproduce"],
    }
    _write_json(output_dir / "manifest.json", manifest)


# ---------------------------------------------------------------------------
# Build entry point.
# ---------------------------------------------------------------------------

def build(
    output_dir: Path = OUTPUT_DIR,
    *,
    run_osrm: bool = True,
) -> dict[str, Any]:
    """Build the supply-node distance artifact and return its summary."""

    assert_no_unexpected_deletions()
    generated_utc = _utc_now()
    nodes, harvest, centroids, centroid_methods, rsyc_diagnostics = (
        build_supply_nodes()
    )
    facilities = load_facilities()
    factors = load_factors()
    assignments = load_assignments(
        facilities, factors, harvest, centroids
    )
    parameters = load_transport_parameters()

    distributions, percentiles = build_distance_distributions(
        nodes, facilities, factors, centroids, harvest
    )
    band_rows, cell_totals = build_band_comparison(
        nodes, facilities, assignments, factors, centroids, parameters
    )
    capacity_access = build_capacity_access(
        nodes, facilities, factors, centroids
    )
    province_assigned: dict[str, float] = defaultdict(float)
    for row in assignments:
        province_assigned[row.province] += row.assigned_m3
    gap_rows = build_transport_gap_rows(
        cell_totals, capacity_access, province_assigned
    )
    province_rows = build_province_gap_rows(gap_rows)
    facilities_by_id = {
        facility.facility_id: facility for facility in facilities
    }
    sensitivity_rows = build_weight_sensitivity(
        nodes, assignments, facilities_by_id, factors, parameters
    )

    if run_osrm:
        requests = select_osrm_requests(nodes, factors)
        results = run_osrm_sample(requests, factors)
        sample_rows, osrm_summary, routes_json = build_osrm_outputs(
            results, factors=factors
        )
    else:
        sample_rows = []
        routes_json = {
            "routes": [],
            "status": "skipped",
            "note": "OSRM sample was skipped at the caller's request.",
        }
        osrm_summary = {
            "status": "skipped",
            "provider": OSRM_PROVIDER,
            "base_url": OSRM_BASE_URL,
            "profile": OSRM_PROFILE,
            "licence": OSRM_LICENCE,
            "attribution": "© OpenStreetMap contributors",
            "rate_limit_seconds": RATE_LIMIT_SECONDS,
            "requests_attempted": 0,
            "requests_ok": 0,
            "requests_failed": 0,
            "status_counts": {},
            "paired_ok_count": 0,
            "node_road_haversine_ratio": _stats([]),
            "centroid_road_haversine_ratio": _stats([]),
            "node_minus_centroid_ratio": _stats([]),
            "node_factor_error_pct": _stats([]),
            "centroid_factor_error_pct": _stats([]),
            "node_origin_snap_km": _stats([]),
            "centroid_origin_snap_km": _stats([]),
            "province_medians": {},
            "usage_policy": "OSRM sample skipped; documented factors only.",
        }

    inputs = _input_hashes()
    summary_labels = {
        "evidence_label": EVIDENCE_LABEL,
        "observed": OBSERVED,
        "policy_grade": POLICY_GRADE,
        "national_claim": NATIONAL_CLAIM,
        "assignment_change": ASSIGNMENT_CHANGE,
        "official_osecan_changed": OFFICIAL_OSECAN_CHANGED,
        "ui_changed": UI_CHANGED,
        "siting_optimization": SITING_OPTIMIZATION,
        "greenfield_optimization": GREENFIELD_OPTIMIZATION,
        "policy_claim": POLICY_CLAIM,
    }
    validation = validate(
        nodes=nodes,
        harvest=harvest,
        rsyc_diagnostics=rsyc_diagnostics,
        assignments=assignments,
        facilities=facilities,
        band_rows=band_rows,
        gap_rows=gap_rows,
        province_rows=province_rows,
        sensitivity_rows=sensitivity_rows,
        distributions=distributions,
        percentiles=percentiles,
        sample_rows=sample_rows,
        osrm_summary=osrm_summary,
        summary_labels=summary_labels,
    )
    summary = build_summary(
        generated_utc=generated_utc,
        nodes=nodes,
        harvest=harvest,
        rsyc_diagnostics=rsyc_diagnostics,
        centroid_methods=centroid_methods,
        facilities=facilities,
        assignments=assignments,
        band_rows=band_rows,
        gap_rows=gap_rows,
        province_rows=province_rows,
        sensitivity_rows=sensitivity_rows,
        distributions=distributions,
        percentiles=percentiles,
        sample_rows=sample_rows,
        osrm_summary=osrm_summary,
        validation=validation,
        inputs=inputs,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(output_dir / "supply_nodes.csv", NODE_FIELDS, _node_csv_rows(nodes))
    _write_csv(
        output_dir / "province_node_weights.csv",
        PROVINCE_WEIGHT_FIELDS,
        _province_weight_rows(summary),
    )
    _write_csv(
        output_dir / "distance_sample.csv", SAMPLE_FIELDS, sample_rows
    )
    _write_json(output_dir / "distance_sample_routes.json", routes_json)
    _write_csv(
        output_dir / "distance_distribution_comparison.csv",
        DISTRIBUTION_COMPARISON_FIELDS,
        _distribution_comparison_rows(summary),
    )
    _write_csv(output_dir / "band_comparison.csv", BAND_FIELDS, band_rows)
    _write_csv(
        output_dir / "transport_gap_comparison.csv", GAP_FIELDS, gap_rows
    )
    _write_csv(
        output_dir / "province_transport_gap.csv",
        PROVINCE_GAP_FIELDS,
        province_rows,
    )
    _write_csv(
        output_dir / "weight_sensitivity.csv",
        SENSITIVITY_FIELDS,
        sensitivity_rows,
    )
    _write_csv(
        output_dir / "provenance.csv",
        PROVENANCE_FIELDS,
        _provenance_rows(inputs),
    )
    _write_json(output_dir / "summary.json", summary)
    report = write_report(summary, band_rows, province_rows, sensitivity_rows)
    (output_dir / "SUPPLY_NODE_DISTANCE_V1.md").write_text(
        report, encoding="utf-8"
    )
    _write_manifest(output_dir, generated_utc, inputs, summary)
    return summary


def main() -> None:
    summary = build()
    print(f"{ARTIFACT}: {summary['status']}")


if __name__ == "__main__":
    main()







