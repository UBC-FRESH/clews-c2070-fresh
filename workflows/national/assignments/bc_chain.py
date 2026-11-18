"""All-BC forest-sector chain at CLEWS B1/B2 zone level.

The module links three previously separate diagnostics:

1. annual zonal supply from the area-weighted BC assortment batch;
2. Stage A log/residue assignment to BC SAW/PAP/PEL/BIO facilities through the
   NRN road graph plus NRN ferry segments from area-weighted TSA zone nodes;
3. Stage B sawmill-residue allocation to PAP/PEL/BIO recipients.

All inputs are parsed once at this boundary.  The functions below raise
``BcChainError`` when an input violates a documented contract instead of
silently repairing it.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
from scipy.sparse import csgraph
from scipy.spatial import cKDTree

from . import units
from .build_road_network import (
    DEFAULT_NRN_ZIP,
    FACILITY_SNAP_TOLERANCE_M,
    FERRY_FARE_BASIS,
    FERRY_FARE_CAD_M3_KM,
    METRIC_CRS,
    build_road_graph,
)
from .facilities import build_facility_capacities, build_facility_table
from .fixtures import (
    _GRADE_SORTS,
    DIAGNOSTIC_UNASSIGNED_PENALTY_CAD_M3,
    _diagnostic_contracts,
)
from .model import solve_assignment
from .schema import (
    AssignmentInputs,
    AssignmentResult,
    FacilityCapacity,
    FeedstockGrade,
    SupplyOffer,
    TransportLink,
)
from .stage_b import run_stage_b
from .transport import load_road_rate_inputs, road_rate_for_grade

__all__ = [
    "BcChainError",
    "build_bc_supply",
    "build_supply_offers",
    "build_zone_nodes",
    "check_stage_a_balance",
    "check_stage_b_balance",
    "load_zone_aac",
    "run_bc_chain",
]

CODE_VERSION = "0.1.0"
DEFAULT_OUT_DIR = Path("data/derived/national/bc_chain")
DEFAULT_PERIOD = "2028"
DEFAULT_YEARS = ("2021", "2028")
RESIDUE_DENSITY_ODT_M3 = units.DEFAULT_BASIC_DENSITY_ODT_M3
MOISTURE_WET_BASIS = 0.45
GJ_PER_ODT = 18.5
ZONE_NODE_IDS = {"B1": "ZONE_B1", "B2": "ZONE_B2"}
ZONE_SNAP_TOLERANCE_M = 100_000.0
TIME_PATH = Path("data/derived/national/clews/bc_residue_time_path.csv")
BATCH = Path(
    "data/derived/national/assortments/bc_area_weights/"
    "bc_national_batch_area_joined.csv"
)
AREA_WEIGHTS = Path(
    "data/derived/national/assortments/bc_area_weights/bc_area_weights.csv"
)
ZONE_CROSSWALK = Path(
    "data/derived/national/clews_supply/bc_woody_residue/bc_zone_crosswalk.csv"
)
TSA_ZONE_CROSSWALK = Path(
    "data/derived/national/clews_supply/bc_woody_residue/bc_tsa_zone_crosswalk.csv"
)
FACILITIES_RECOVERED = Path(
    "data/derived/national/assignments/tsa24_pilot/facilities_bc_recovered.csv"
)
APPORTIONMENT_CONTRACTS = Path(
    "data/derived/national/assignments/tsa24_pilot/"
    "contracts_bc_apportionment.csv"
)
TSA_BOUNDARY = Path(
    "/srv/shared-data/gep/ws4/femic/external/femic-public-data/data/bc/tsa/"
    "FADM_TSA.gdb"
)

_GRADE_PRODUCTS = {
    "SAWLOG": "sawlog",
    "PULP": "pulpwood",
    "OTHER_LOG": "other",
    "RESIDUE": "residue",
    "HOGFUEL": "hogfuel",
}
_PRODUCT_TO_GRADE = {
    "sawlog": "SAWLOG",
    "pulpwood": "PULP",
    "other": "OTHER_LOG",
    "residue": "RESIDUE",
}
_MERCHANTABLE_PRODUCTS = ("sawlog", "pulpwood", "other")


class BcChainError(RuntimeError):
    """Raised when a BC-chain input or intermediate state is not defensible."""


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise BcChainError(f"required input is missing: {path}")
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise BcChainError(f"input has no data rows: {path}")
    return rows


def _require_columns(
    path: Path, rows: Sequence[Mapping[str, str]], required: Sequence[str]
) -> None:
    missing = set(required) - set(rows[0])
    if missing:
        raise BcChainError(f"{path} is missing required columns: {sorted(missing)}")


def _number(value: object, name: str) -> float:
    text = str(value).strip()
    if text == "" or text.lower() in {"na", "nan", "none"}:
        raise BcChainError(f"{name} is missing")
    try:
        number = float(text)
    except ValueError as error:
        raise BcChainError(f"{name} is not numeric: {value!r}") from error
    if not math.isfinite(number):
        raise BcChainError(f"{name} is not finite: {value!r}")
    return number


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _out(root: Path, relative: Path | str) -> Path:
    path = Path(relative)
    return path if path.is_absolute() else root / path


def load_zone_aac(
    repo_root: Path, year: str
) -> tuple[dict[str, float], dict[str, float], dict[str, float]]:
    """Return AAC, recovery fraction, and gross taper residue by zone."""
    path = _out(repo_root, TIME_PATH)
    rows = _read_csv(path)
    _require_columns(
        path,
        rows,
        {
            "year",
            "zone_id",
            "annual_log_m3",
            "gross_taper_residue_odt",
            "base_recovery_fraction",
        },
    )
    aac: dict[str, float] = {}
    recovery: dict[str, float] = {}
    gross_residue: dict[str, float] = {}
    for row in rows:
        if str(row["year"]).strip() != str(year):
            continue
        zone = str(row["zone_id"]).strip()
        if zone not in ZONE_NODE_IDS:
            continue
        aac[zone] = _number(row["annual_log_m3"], f"{year} {zone} annual_log_m3")
        recovery[zone] = _number(
            row["base_recovery_fraction"], f"{year} {zone} base_recovery_fraction"
        )
        gross_residue[zone] = _number(
            row["gross_taper_residue_odt"], f"{year} {zone} gross_taper_residue_odt"
        )
    missing = set(ZONE_NODE_IDS) - set(aac)
    if missing:
        raise BcChainError(f"{path} has no AAC rows for zones {sorted(missing)}")
    return aac, recovery, gross_residue


def _zone_map(repo_root: Path) -> dict[str, str]:
    path = _out(repo_root, ZONE_CROSSWALK)
    rows = _read_csv(path)
    _require_columns(path, rows, {"ecoprovince", "zone_id"})
    mapping: dict[str, str] = {}
    for row in rows:
        ecoprovince = str(row["ecoprovince"]).strip()
        zone = str(row["zone_id"]).strip()
        if ecoprovince in mapping:
            raise BcChainError(f"duplicate zone crosswalk row for {ecoprovince}")
        mapping[ecoprovince] = zone
    return mapping


def _unmapped_area_by_zone(repo_root: Path) -> dict[str, float]:
    path = _out(repo_root, AREA_WEIGHTS)
    rows = _read_csv(path)
    _require_columns(path, rows, {"ecoprovince", "rsyc_species", "area_ha"})
    zone_for = _zone_map(repo_root)
    totals: dict[str, float] = defaultdict(float)
    for row in rows:
        if str(row["rsyc_species"]).strip() != "UNMAPPED_BC":
            continue
        ecoprovince = str(row["ecoprovince"]).strip()
        zone = zone_for.get(ecoprovince)
        if zone is None:
            raise BcChainError(
                f"unmapped species area has no B1/B2 zone for {ecoprovince!r}"
            )
        if zone not in ZONE_NODE_IDS:
            raise BcChainError(f"unmapped species area maps to unknown zone {zone!r}")
        totals[zone] += _number(row["area_ha"], "unmapped area_ha")
    return dict(totals)


def build_bc_supply(
    repo_root: Path,
    *,
    year: str,
) -> pd.DataFrame:
    """Scale the matched area-weighted BC batch to the zonal AAC.

    Per-ha volumes are formed from matched cells only; the annual harvest area
    solves ``AAC = harvest_area x zone average per-ha merchantable volume``.
    Species and product shares are preserved by scaling each cell's product
    volume by the same zone factor.  Unmapped species area is reported as an
    explicit zero-volume ``UNMAPPED_BC`` row, never silently dropped.
    """
    batch_path = _out(repo_root, BATCH)
    batch = pd.read_csv(batch_path)
    required = {
        "cell_key",
        "ecoprovince",
        "species",
        "product",
        "sort_code",
        "stream",
        "volume_m3",
        "residue_m3",
        "residue_odt",
        "density_odt_m3",
        "area_ha",
    }
    missing = required - set(batch.columns)
    if missing:
        raise BcChainError(f"{batch_path} is missing columns {sorted(missing)}")

    zone_for = _zone_map(repo_root)
    batch = batch.copy()
    batch["zone_id"] = batch["ecoprovince"].map(zone_for)
    if batch["zone_id"].isna().any():
        unknown = sorted(batch.loc[batch["zone_id"].isna(), "ecoprovince"].unique())
        raise BcChainError(f"batch ecoprovinces have no B1/B2 zone: {unknown}")
    batch = batch[batch["zone_id"].isin(ZONE_NODE_IDS)]

    total_rows = batch[(batch["stream"] == "total") & (batch["area_ha"] > 0.0)]
    if total_rows.empty:
        raise BcChainError("batch has no matched total rows to carry cell area")
    cell_area = total_rows.set_index("cell_key")["area_ha"].astype(float)
    if cell_area.index.duplicated().any():
        raise BcChainError("batch has more than one total row per matched cell")

    logs = batch[
        (batch["stream"] == "log")
        & batch["product"].isin(_MERCHANTABLE_PRODUCTS)
        & batch["cell_key"].isin(cell_area.index)
    ].copy()
    if logs.empty:
        raise BcChainError("batch has no matched merchantable log rows")
    residues = batch[
        (batch["stream"] == "residue") & batch["cell_key"].isin(cell_area.index)
    ].copy()
    if residues.empty:
        raise BcChainError("batch has no matched residue rows")

    logs["cell_area_ha"] = logs["cell_key"].map(cell_area)
    residues["cell_area_ha"] = residues["cell_key"].map(cell_area)
    if logs["cell_area_ha"].isna().any() or residues["cell_area_ha"].isna().any():
        raise BcChainError("a log/residue row has no matched cell area")

    zone_area = (
        total_rows.assign(zone_id=total_rows["ecoprovince"].map(zone_for))
        .groupby("zone_id")["area_ha"]
        .sum()
    )
    zone_log = logs.groupby("zone_id")["volume_m3"].sum()
    for zone in ZONE_NODE_IDS:
        if zone_area.get(zone, 0.0) <= 0.0 or zone_log.get(zone, 0.0) <= 0.0:
            raise BcChainError(f"zone {zone} has no matched area or merchantable volume")

    aac, recovery, _gross_residue = load_zone_aac(repo_root, year)
    unmapped = _unmapped_area_by_zone(repo_root)

    rows: list[dict[str, Any]] = []
    for zone in ZONE_NODE_IDS:
        zone_scale = aac[zone] / float(zone_log[zone])
        harvest_area = aac[zone] / (
            float(zone_log[zone]) / float(zone_area[zone])
        )
        for row in logs[logs["zone_id"] == zone].itertuples(index=False):
            volume = float(row.volume_m3) * zone_scale
            if volume <= 0.0:
                continue
            rows.append(
                {
                    "zone": zone,
                    "year": str(year),
                    "aac_m3": aac[zone],
                    "harvest_area_ha": harvest_area,
                    "species": str(row.species),
                    "product": str(row.product),
                    "sort_code": str(row.sort_code),
                    "volume_m3": volume,
                    "residue_m3": 0.0,
                    "batch_residue_m3": 0.0,
                    "residue_odt": 0.0,
                    "residue_recoverable_odt": 0.0,
                    "density_odt_m3": float(row.density_odt_m3),
                    "roadside_cost_cad_m3": 0.0,
                    "delivered_cost_cad_m3": 0.0,
                    "evidence_tier": "matched_batch_area_scaled_to_aac",
                    "mapped_status": "mapped",
                    "excluded_forest_area_ha": 0.0,
                }
            )
        for row in residues[residues["zone_id"] == zone].itertuples(index=False):
            gross_m3 = float(row.residue_m3) * zone_scale
            gross_odt = float(row.residue_odt) * zone_scale
            if gross_odt <= 0.0:
                continue
            rows.append(
                {
                    "zone": zone,
                    "year": str(year),
                    "aac_m3": aac[zone],
                    "harvest_area_ha": harvest_area,
                    "species": str(row.species),
                    "product": "residue",
                    "sort_code": "RESIDUE",
                    "volume_m3": gross_odt / RESIDUE_DENSITY_ODT_M3,
                    "residue_m3": gross_odt / RESIDUE_DENSITY_ODT_M3,
                    "batch_residue_m3": gross_m3,
                    "residue_odt": gross_odt,
                    "residue_recoverable_odt": gross_odt * recovery[zone],
                    "density_odt_m3": RESIDUE_DENSITY_ODT_M3,
                    "roadside_cost_cad_m3": 0.0,
                    "delivered_cost_cad_m3": 0.0,
                    "evidence_tier": "matched_batch_taper_residue_scaled_to_aac",
                    "mapped_status": "mapped",
                    "excluded_forest_area_ha": 0.0,
                }
            )
        rows.append(
            {
                "zone": zone,
                "year": str(year),
                "aac_m3": aac[zone],
                "harvest_area_ha": harvest_area,
                "species": "UNMAPPED_BC",
                "product": "unmapped",
                "sort_code": "UNMAPPED",
                "volume_m3": 0.0,
                "residue_m3": 0.0,
                "batch_residue_m3": 0.0,
                "residue_odt": 0.0,
                "residue_recoverable_odt": 0.0,
                "density_odt_m3": 0.0,
                "roadside_cost_cad_m3": 0.0,
                "delivered_cost_cad_m3": 0.0,
                "evidence_tier": "unmapped_bc_species_excluded_from_composition",
                "mapped_status": "excluded_unmapped_area",
                "excluded_forest_area_ha": unmapped.get(zone, 0.0),
            }
        )

    supply = pd.DataFrame(rows)
    check_zone_supply(supply)
    return supply


def check_zone_supply(supply: pd.DataFrame) -> None:
    """Fail closed unless zonal log scaling conserves the declared AAC."""
    required = {
        "zone",
        "year",
        "aac_m3",
        "volume_m3",
        "product",
        "residue_odt",
        "residue_recoverable_odt",
    }
    missing = required - set(supply.columns)
    if missing:
        raise BcChainError(f"supply table is missing {sorted(missing)}")
    for (zone, year), group in supply.groupby(["zone", "year"]):
        aac = float(group["aac_m3"].iloc[0])
        logs = float(group.loc[group["product"].isin(_MERCHANTABLE_PRODUCTS), "volume_m3"].sum())
        if abs(logs - aac) > max(1e-6, aac * 1e-9):
            raise BcChainError(
                f"zone {zone} {year} log volume {logs} does not conserve AAC {aac}"
            )
        if (group["residue_recoverable_odt"] > group["residue_odt"] + 1e-6).any():
            raise BcChainError(f"zone {zone} {year} recoverable residue exceeds gross")


def build_supply_offers(
    supply: pd.DataFrame,
    *,
    year: str,
    period: str,
) -> tuple[tuple[FeedstockGrade, ...], tuple[SupplyOffer, ...]]:
    """Turn a scaled zonal supply table into parsed Stage A offers."""
    data = supply[supply["year"].astype(str) == str(year)].copy()
    if data.empty:
        raise BcChainError(f"no supply rows for {year}")
    data = data[data["volume_m3"] > 0.0]
    offers: list[SupplyOffer] = []
    grade_volume: dict[str, float] = defaultdict(float)
    grade_density: dict[str, float] = defaultdict(float)
    for index, row in enumerate(data.itertuples(index=False), start=1):
        grade = _PRODUCT_TO_GRADE.get(str(row.product))
        if grade is None:
            raise BcChainError(f"unknown supply product {row.product!r}")
        if grade == "RESIDUE":
            volume = float(row.residue_recoverable_odt) / RESIDUE_DENSITY_ODT_M3
            density = RESIDUE_DENSITY_ODT_M3
        else:
            volume = float(row.volume_m3)
            density = float(row.density_odt_m3)
        if volume <= 0.0 or density <= 0.0:
            continue
        offer_id = (
            f"BC_{row.zone}_{year}_{grade}_{row.species}_"
            f"{row.sort_code}_{index:05d}"
        )
        offers.append(
            SupplyOffer(
                offer_id=offer_id,
                period=period,
                grade_id=grade,
                product=str(row.product),
                sort_code=str(row.sort_code),
                top_cm=0.0,
                origin=ZONE_NODE_IDS[str(row.zone)],
                volume_m3=volume,
                density_odt_m3=density,
                moisture_wet_basis=MOISTURE_WET_BASIS,
                roadside_cost_cad_m3=float(row.roadside_cost_cad_m3),
                evidence=(
                    "bc_area_weighted_batch_scaled_to_aac;"
                    "roadside_cost_not_modelled;delivered_cost_in_stage_a"
                ),
                source_ref=f"{row.zone}:{row.species}:{row.product}:{row.sort_code}",
            )
        )
        grade_volume[grade] += volume
        grade_density[grade] += volume * density
    if not offers:
        raise BcChainError(f"no positive Stage A offers for {year}")

    grades: list[FeedstockGrade] = []
    for grade_id, product in _GRADE_PRODUCTS.items():
        volume = grade_volume.get(grade_id, 0.0)
        if volume <= 0.0:
            grades.append(
                FeedstockGrade(
                    grade_id=grade_id,
                    product=product,
                    unit="m3",
                    accepted_sorts=_GRADE_SORTS[grade_id],
                    density_odt_m3=RESIDUE_DENSITY_ODT_M3,
                    moisture_wet_basis=MOISTURE_WET_BASIS,
                    description=(
                        "Reserved zero-supply grade so facility offers remain "
                        "parseable; no BC zone offer of this grade exists."
                    ),
                    evidence="reserved_no_supply",
                )
            )
            continue
        grades.append(
            FeedstockGrade(
                grade_id=grade_id,
                product=product,
                unit="m3",
                accepted_sorts=_GRADE_SORTS[grade_id],
                density_odt_m3=grade_density[grade_id] / volume,
                moisture_wet_basis=MOISTURE_WET_BASIS,
                description="Area-weighted BC zone assortment grade.",
                evidence="derived_bc_area_weighted_batch",
            )
        )
    return tuple(sorted(grades, key=lambda item: item.grade_id)), tuple(offers)


def build_zone_nodes(
    repo_root: Path,
    *,
    graph: Any | None = None,
) -> pd.DataFrame:
    """Return apportionment-weighted TSA centroids for B1 and B2.

    Each TSA centroid is the centroid of the dissolved FADM TSA polygon.  Zone
    coordinates weight TSA centroids by 2018 apportionment x the zone's THLB
    share, so split TSAs contribute to both zones in proportion to their THLB.
    TSAs whose nearest NRN node is outside the main road-connected component
    are excluded from the centroid; otherwise a ferry-only or island centroid
    would create a zone node with almost no routable links.  The exclusion is
    counted and reported.
    """
    crosswalk_path = _out(repo_root, TSA_ZONE_CROSSWALK)
    rows = _read_csv(crosswalk_path)
    _require_columns(
        crosswalk_path,
        rows,
        {
            "tsa_number",
            "zone_id",
            "apportionment_m3",
            "b1_thlb_share",
            "b2_thlb_share",
        },
    )
    if not TSA_BOUNDARY.exists():
        raise BcChainError(f"TSA boundary source is missing: {TSA_BOUNDARY}")
    shapes = gpd.read_file(
        TSA_BOUNDARY,
        layer="WHSE_ADMIN_BOUNDARIES_FADM_TSA",
        columns=["TSA_NUMBER", "geometry"],
    )
    if shapes.crs is None:
        raise BcChainError("TSA boundary source has no CRS")
    shapes = shapes.to_crs(METRIC_CRS)
    shapes["tsa_number"] = (
        shapes["TSA_NUMBER"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True).str.zfill(2)
    )
    dissolved = shapes.dissolve(by="tsa_number", as_index=False)
    centroids = dissolved.geometry.centroid
    centroid_by_tsa = {
        str(tsa): (float(point.x), float(point.y))
        for tsa, point in zip(dissolved["tsa_number"], centroids)
    }

    main_component: int | None = None
    centroid_component: dict[str, int] = {}
    if graph is not None:
        main_component = int(np.argmax(graph.component_sizes))
        tree = cKDTree(graph.node_xy_m)
        tsa_ids = sorted(centroid_by_tsa)
        points = np.asarray([centroid_by_tsa[tsa] for tsa in tsa_ids], dtype=float)
        _distance, node_index = tree.query(points, k=1)
        centroid_component = {
            tsa: int(graph.component_labels[int(index)])
            for tsa, index in zip(tsa_ids, node_index)
        }

    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        zone = str(row["zone_id"]).strip()
        if zone not in ZONE_NODE_IDS:
            continue
        tsa = str(row["tsa_number"]).strip().zfill(2)
        if tsa == "NO_TSA":
            continue
        point = centroid_by_tsa.get(tsa)
        if point is None:
            raise BcChainError(f"TSA {tsa} is absent from the FADM boundary layer")
        if (
            main_component is not None
            and centroid_component.get(tsa) != main_component
        ):
            continue
        apportionment = _number(row["apportionment_m3"], f"TSA {tsa} apportionment_m3")
        share_key = f"{zone.lower()}_thlb_share"
        share = _number(row[share_key], f"TSA {tsa} {share_key}")
        weight = apportionment * share
        if weight <= 0.0:
            continue
        bucket = grouped.setdefault(
            zone, {"weight": 0.0, "x": 0.0, "y": 0.0, "tsa_count": 0}
        )
        bucket["weight"] += weight
        bucket["x"] += weight * point[0]
        bucket["y"] += weight * point[1]
        bucket["tsa_count"] += 1
    if set(grouped) != set(ZONE_NODE_IDS):
        raise BcChainError(f"zone nodes could not be built for {sorted(ZONE_NODE_IDS)}")
    rows_out: list[dict[str, Any]] = []
    for zone_id, node_id in ZONE_NODE_IDS.items():
        bucket = grouped[zone_id]
        x = bucket["x"] / bucket["weight"]
        y = bucket["y"] / bucket["weight"]
        lon, lat = gpd.GeoSeries(
            [gpd.points_from_xy([x], [y])[0]], crs=METRIC_CRS
        ).to_crs("EPSG:4326").iloc[0].coords[0]
        rows_out.append(
            {
                "zone_id": zone_id,
                "node_id": node_id,
                "x_m": x,
                "y_m": y,
                "lon": lon,
                "lat": lat,
                "weight_basis": "2018_apportionment_m3_x_zone_thlb_share",
                "weighted_tsa_count": bucket["tsa_count"],
                "total_weight_m3": bucket["weight"],
                "main_road_component": main_component if main_component is not None else "",
                "component_filter": "main_road_component" if main_component is not None else "none",
            }
        )
    return pd.DataFrame(rows_out).sort_values("zone_id").reset_index(drop=True)


def _zone_node_snap(
    graph: Any, zone_nodes: pd.DataFrame
) -> tuple[dict[str, int], dict[str, float]]:
    """Snap zone centroids to the nearest node in the main graph component."""
    main_label = int(np.argmax(graph.component_sizes))
    main_nodes = np.flatnonzero(graph.component_labels == main_label)
    if len(main_nodes) == 0:
        raise BcChainError("the road graph has no main component")
    tree = cKDTree(graph.node_xy_m[main_nodes])
    indices: dict[str, int] = {}
    distances: dict[str, float] = {}
    for row in zone_nodes.itertuples(index=False):
        point = np.array([[float(row.x_m), float(row.y_m)]])
        distance, index = tree.query(point, k=1)
        snap_m = float(distance[0])
        if snap_m > ZONE_SNAP_TOLERANCE_M:
            raise BcChainError(
                f"zone node {row.node_id} is {snap_m:.0f} m from the main NRN "
                f"graph component; exceeds {ZONE_SNAP_TOLERANCE_M:.0f} m"
            )
        indices[str(row.node_id)] = int(main_nodes[int(index[0])])
        distances[str(row.node_id)] = snap_m
    return indices, distances


def _ferry_pair_lookup(graph: Any) -> dict[tuple[int, int], float]:
    """Map an undirected node pair to its ferry length in metres."""
    lookup: dict[tuple[int, int], float] = {}
    for u, v, length in zip(
        getattr(graph, "ferry_edge_u", ()),
        getattr(graph, "ferry_edge_v", ()),
        getattr(graph, "ferry_edge_length_m", ()),
    ):
        key = (min(int(u), int(v)), max(int(u), int(v)))
        lookup[key] = max(lookup.get(key, 0.0), float(length))
    return lookup


def _ferry_path_metrics(
    predecessors: np.ndarray,
    source_node: int,
    target_node: int,
    ferry_pairs: Mapping[tuple[int, int], float],
) -> tuple[float, int]:
    """Trace one shortest-path predecessor chain and sum ferry edges."""
    if source_node == target_node:
        return 0.0, 0
    ferry_m = 0.0
    crossings = 0
    node = int(target_node)
    guard = 0
    while node != source_node:
        previous = int(predecessors[0, node])
        if previous < 0:
            raise BcChainError(
                f"shortest-path predecessor chain breaks before source {source_node}"
            )
        key = (min(previous, node), max(previous, node))
        if key in ferry_pairs:
            ferry_m += float(ferry_pairs[key])
            crossings += 1
        node = previous
        guard += 1
        if guard > len(predecessors[0]):
            raise BcChainError("shortest-path predecessor chain contains a cycle")
    return ferry_m, crossings


def build_zone_transport_links(
    repo_root: Path,
    facilities: Sequence[FacilityCapacity],
    grades: Sequence[FeedstockGrade],
    zone_nodes: pd.DataFrame,
    *,
    period: str,
    nrn_zip: Path = DEFAULT_NRN_ZIP,
    graph: Any | None = None,
) -> tuple[tuple[TransportLink, ...], list[dict[str, Any]], dict[str, Any]]:
    """Build NRN shortest-path links from each zone node to every facility."""
    nrn_path = _out(repo_root, nrn_zip)
    if graph is None:
        graph = build_road_graph(nrn_path)
    origin_nodes, origin_snap = _zone_node_snap(graph, zone_nodes)

    facility_lookup = {facility.facility_id: facility for facility in facilities}
    # Coordinates come from the recovered facility table because the Stage A
    # capacity object intentionally does not carry them.
    recovered_path = _out(repo_root, FACILITIES_RECOVERED)
    recovered = pd.read_csv(recovered_path)
    required = {"facility_id", "lat", "lon", "coord_source"}
    missing = required - set(recovered.columns)
    if missing:
        raise BcChainError(f"{recovered_path} is missing {sorted(missing)}")
    recovered = recovered[recovered["facility_id"].isin(facility_lookup)].copy()
    if recovered.empty:
        raise BcChainError("no facility in the capacity table has recovered coordinates")
    recovered["lat"] = pd.to_numeric(recovered["lat"], errors="coerce")
    recovered["lon"] = pd.to_numeric(recovered["lon"], errors="coerce")
    recovered["coord_source"] = recovered["coord_source"].fillna("missing").astype(str)
    usable = recovered[recovered["lat"].notna() & recovered["lon"].notna()].copy()
    transformer = gpd.GeoSeries(
        gpd.points_from_xy(usable["lon"], usable["lat"]), crs="EPSG:4326"
    ).to_crs(METRIC_CRS)
    facility_xy = np.column_stack(
        [transformer.geometry.x.to_numpy(), transformer.geometry.y.to_numpy()]
    )
    tree = cKDTree(graph.node_xy_m)
    snap_distance, snap_index = tree.query(facility_xy, k=1)
    facility_node: dict[str, tuple[int, float]] = {}
    screen: list[dict[str, Any]] = []
    for position, row in enumerate(usable.itertuples(index=False)):
        snap_m = float(snap_distance[position])
        if snap_m > FACILITY_SNAP_TOLERANCE_M:
            screen.append(
                {
                    "facility_id": row.facility_id,
                    "status": "too_far",
                    "snap_m": snap_m,
                    "coord_source": row.coord_source,
                }
            )
            continue
        facility_node[str(row.facility_id)] = (int(snap_index[position]), snap_m)

    distances: dict[str, np.ndarray] = {}
    predecessors: dict[str, np.ndarray] = {}
    for zone_id, node_index in origin_nodes.items():
        values, previous = csgraph.dijkstra(
            graph.matrix, directed=False, indices=[node_index], return_predecessors=True
        )
        distances[zone_id] = values[0]
        predecessors[zone_id] = previous

    ferry_pairs = _ferry_pair_lookup(graph)

    rates, capacities = load_road_rate_inputs(repo_root)
    grade_density = {grade.grade_id: grade.density_odt_m3 for grade in grades}
    grade_rates = {
        grade_id: road_rate_for_grade(rates, capacities, grade_id=grade_id)
        for grade_id in grade_density
    }
    links: list[TransportLink] = []
    for zone_node in ZONE_NODE_IDS.values():
        for facility_id, (node_index, snap_m) in sorted(facility_node.items()):
            facility = facility_lookup[facility_id]
            for offer in facility.offers:
                if offer.grade_id not in grade_rates:
                    continue
                distance_m = float(distances[zone_node][node_index])
                if not math.isfinite(distance_m) or distance_m <= 0.0:
                    screen.append(
                        {
                            "facility_id": facility_id,
                            "status": "no_graph_path",
                            "snap_m": snap_m,
                            "coord_source": "",
                        }
                    )
                    continue
                distance_km = distance_m / 1000.0
                density = grade_density[offer.grade_id]
                rate = grade_rates[offer.grade_id]
                ferry_m, ferry_crossings = _ferry_path_metrics(
                    predecessors[zone_node], origin_nodes[zone_node], node_index, ferry_pairs
                )
                ferry_km = min(ferry_m / 1000.0, distance_km)
                road_km = max(distance_km - ferry_km, 0.0)
                ferry_cost = ferry_km * FERRY_FARE_CAD_M3_KM
                cost = road_km * rate.cost_per_m3_km(density) + ferry_cost
                links.append(
                    TransportLink(
                        link_id=f"{zone_node}->{facility_id}::{offer.grade_id}",
                        period=period,
                        origin=zone_node,
                        destination=facility_id,
                        grade_id=offer.grade_id,
                        mode="ROAD",
                        distance_km=distance_km,
                        cost_cad_m3=cost,
                        capacity_m3=None,
                        distance_basis="nrn_bc_road_graph_plus_ferry_shortest_path_epsg3005",
                        cost_basis=(
                            f"road_transporter_rate:{rate.transporter};"
                            f"ferry_placeholder:{FERRY_FARE_CAD_M3_KM}_cad_m3_km;"
                            "fuel_plus_operating_per_hour_over_speed;no_transload"
                        ),
                        evidence=(
                            f"zone_node_snap_m={origin_snap[zone_node]:.1f};"
                            f"facility_snap_m={snap_m:.1f};"
                            f"ferry_km={ferry_km:.1f};"
                            f"ferry_crossings={ferry_crossings};route_capacity_absent"
                        ),
                        path_confidence="real_road_network",
                        capacity_source="absent",
                        ferry_distance_km=ferry_km,
                        ferry_cost_cad_m3=ferry_cost,
                    )
                )
    if not links:
        raise BcChainError("no zone-to-facility NRN link could be built")
    summary = {
        "nrn_source": str(nrn_path),
        "graph_nodes": len(graph.node_xy_m),
        "graph_edges": len(graph.edge_length_m),
        "origin_snap_m": origin_snap,
        "facility_snapped": len(facility_node),
        "facility_rejected": len(screen),
        "link_count": len(links),
        "distance_km": _stats([link.distance_km for link in links]),
        "cost_cad_m3": _stats([link.cost_cad_m3 for link in links]),
        "ferry_link_count": int(sum(1 for link in links if link.ferry_distance_km > 0.0)),
        "ferry_distance_km": _stats([link.ferry_distance_km for link in links]),
        "ferry_cost_cad_m3": _stats([link.ferry_cost_cad_m3 for link in links]),
        "ferry_fare_placeholder_cad_m3_km": FERRY_FARE_CAD_M3_KM,
        "ferry_fare_basis": FERRY_FARE_BASIS,
        "ferry_graph_summary": dict(getattr(graph, "ferry_summary", {})),
        "rates": {
            grade_id: {
                "transporter": rate.transporter,
                "cost_cad_m3_km": rate.cost_per_m3_km(grade_density[grade_id]),
                "density_odt_m3": grade_density[grade_id],
            }
            for grade_id, rate in grade_rates.items()
        },
    }
    return tuple(links), screen, summary


def _stats(values: Sequence[float]) -> dict[str, float | int]:
    finite = sorted(float(value) for value in values if math.isfinite(float(value)))
    if not finite:
        return {"count": 0}
    if len(finite) == 1:
        return {"count": 1, "min": finite[0], "median": finite[0], "max": finite[0], "mean": finite[0]}
    return {
        "count": len(finite),
        "min": finite[0],
        "p25": float(np.percentile(finite, 25)),
        "median": float(np.percentile(finite, 50)),
        "p75": float(np.percentile(finite, 75)),
        "max": finite[-1],
        "mean": float(np.mean(finite)),
    }


def _supply_ledger(supply: pd.DataFrame, year: str) -> pd.DataFrame:
    data = supply[supply["year"].astype(str) == str(year)].copy()
    return data


def solve_stage_a(
    repo_root: Path,
    *,
    period: str,
    supply: pd.DataFrame,
    zone_nodes: pd.DataFrame,
    graph: Any | None = None,
) -> tuple[AssignmentResult, AssignmentInputs, tuple[TransportLink, ...], list[dict[str, Any]], dict[str, Any], pd.DataFrame]:
    """Build and solve the all-BC Stage A allocation."""
    grades, offers = build_supply_offers(supply, year=period, period=period)
    facility_table = build_facility_table(
        repo_root,
        recovered_coordinates=_out(repo_root, FACILITIES_RECOVERED),
    )
    facilities, line_screen = build_facility_capacities(
        repo_root, facility_table, period=period
    )
    links, transport_screen, transport_summary = build_zone_transport_links(
        repo_root, facilities, grades, zone_nodes, period=period, graph=graph
    )
    contracts = _diagnostic_contracts(
        facilities,
        period,
        apportionment_table=_out(repo_root, APPORTIONMENT_CONTRACTS),
    )
    inputs = AssignmentInputs(
        run_id=f"bc_chain_stage_a_{period}_v1",
        scenario_id="bc_chain_stage_a",
        periods=(period,),
        grades=grades,
        supplies=offers,
        facilities=facilities,
        transport_links=links,
        contracts=contracts,
        unassigned_penalty_cad_m3=DIAGNOSTIC_UNASSIGNED_PENALTY_CAD_M3,
        moisture_limit_by_class={},
        metadata={
            "stage": "A",
            "period": period,
            "diagnostic": True,
            "zone_nodes": zone_nodes.to_dict(orient="records"),
            "transport_summary": transport_summary,
            "facility_count": len(facilities),
            "line_screen_count": len(line_screen),
            "contract_count": len(contracts),
        },
    )
    result = solve_assignment(inputs)
    ledger = _supply_ledger(supply, period)
    return result, inputs, links, transport_screen, transport_summary, ledger


def check_stage_a_balance(
    ledger: pd.DataFrame,
    assignments: pd.DataFrame,
    unassigned: pd.DataFrame,
) -> None:
    """Fail closed unless assigned + unassigned equals supply per zone/grade."""
    if ledger.empty:
        raise BcChainError("supply ledger is empty")
    required = {"zone", "product", "volume_m3", "residue_recoverable_odt"}
    missing = required - set(ledger.columns)
    if missing:
        raise BcChainError(f"supply ledger is missing {sorted(missing)}")
    keys = ["zone", "grade"]
    ledger = ledger.assign(
        grade=ledger["product"].map(_PRODUCT_TO_GRADE),
        stage_a_volume_m3=lambda frame: np.where(
            frame["product"] == "residue",
            frame["residue_recoverable_odt"] / RESIDUE_DENSITY_ODT_M3,
            frame["volume_m3"],
        ),
    )
    supply_by = ledger.groupby(keys)["stage_a_volume_m3"].sum()
    assigned_by = (
        assignments.groupby(keys)["volume_m3"].sum()
        if not assignments.empty
        else pd.Series(dtype=float)
    )
    unassigned_by = (
        unassigned.groupby(keys)["volume_m3"].sum()
        if not unassigned.empty
        else pd.Series(dtype=float)
    )
    for key, volume in supply_by.items():
        total = float(assigned_by.get(key, 0.0)) + float(unassigned_by.get(key, 0.0))
        if abs(total - float(volume)) > max(1e-3, float(volume) * 1e-7):
            raise BcChainError(
                f"Stage A mass balance fails for {key}: supply={volume} "
                f"assigned+unassigned={total}"
            )


def check_stage_b_balance(balance: pd.DataFrame) -> None:
    """Fail closed unless assigned + unassigned equals generated residue."""
    required = {"source_facility", "residue_type", "generated_odt", "assigned_odt", "unassigned_odt"}
    missing = required - set(balance.columns)
    if missing:
        raise BcChainError(f"Stage B balance table is missing {sorted(missing)}")
    for row in balance.itertuples(index=False):
        total = float(row.assigned_odt) + float(row.unassigned_odt)
        if abs(total - float(row.generated_odt)) > max(1e-3, float(row.generated_odt) * 1e-7):
            raise BcChainError(
                f"Stage B mass balance fails for {row.source_facility}/{row.residue_type}: "
                f"generated={row.generated_odt} assigned+unassigned={total}"
            )


def _assignment_frame(result: AssignmentResult, ledger: pd.DataFrame) -> pd.DataFrame:
    mapping = {
        str(row.offer_id): {
            "zone": row.zone,
            "species": row.species,
            "product": row.product,
            "sort_code": row.sort_code,
        }
        for row in _ledger_offers(ledger).itertuples(index=False)
    }
    rows = []
    for item in result.assignments:
        meta = mapping.get(item.supply_id)
        if meta is None:
            raise BcChainError(f"assignment references unknown supply {item.supply_id}")
        record = item.to_dict()
        record.update(meta)
        record["grade"] = item.grade_id
        record["delivered_cost_cad_m3"] = (
            item.supply_cost_cad_m3 + item.transport_cost_cad_m3 + item.gate_cost_cad_m3
        )
        rows.append(record)
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    return frame.sort_values(["zone", "grade_id", "facility_id", "supply_id"]).reset_index(drop=True)


def _ledger_offers(ledger: pd.DataFrame) -> pd.DataFrame:
    """Recreate the deterministic offer ids used by ``build_supply_offers``."""
    rows = []
    data = ledger[ledger["volume_m3"] > 0.0].copy()
    for index, row in enumerate(data.itertuples(index=False), start=1):
        grade = _PRODUCT_TO_GRADE.get(str(row.product))
        if grade is None:
            continue
        if grade == "RESIDUE" and float(row.residue_recoverable_odt) <= 0.0:
            continue
        rows.append(
            {
                "offer_id": (
                    f"BC_{row.zone}_{row.year}_{grade}_{row.species}_"
                    f"{row.sort_code}_{index:05d}"
                ),
                "zone": str(row.zone),
                "year": str(row.year),
                "species": str(row.species),
                "product": str(row.product),
                "sort_code": str(row.sort_code),
                "grade": grade,
            }
        )
    return pd.DataFrame(rows)


def _unassigned_frame(result: AssignmentResult, ledger: pd.DataFrame) -> pd.DataFrame:
    mapping = {
        str(row.offer_id): {
            "zone": row.zone,
            "species": row.species,
            "product": row.product,
            "sort_code": row.sort_code,
        }
        for row in _ledger_offers(ledger).itertuples(index=False)
    }
    rows = []
    for item in result.unassigned:
        meta = mapping.get(item.supply_id)
        if meta is None:
            raise BcChainError(f"unassigned supply {item.supply_id} has no ledger row")
        record = item.to_dict()
        record.update(meta)
        record["grade"] = item.grade_id
        rows.append(record)
    columns = [
        "supply_id",
        "period",
        "grade_id",
        "origin",
        "volume_m3",
        "reason",
        "penalty_cost_cad_m3",
        "zone",
        "species",
        "product",
        "sort_code",
        "grade",
    ]
    frame = pd.DataFrame(rows, columns=columns)
    if frame.empty:
        return frame
    return frame.sort_values(["zone", "grade_id", "supply_id"]).reset_index(drop=True)


def _utilisation_rows(result: AssignmentResult) -> pd.DataFrame:
    rows = []
    for record in result.facility_utilization:
        row = {key: value for key, value in record.items() if key != "grade_rows"}
        row["binding_grades"] = ";".join(
            grade["grade_id"] for grade in record["grade_rows"] if grade["binding"]
        )
        rows.append(row)
    return pd.DataFrame(rows)


def _stage_a_summary(
    result: AssignmentResult,
    assignments: pd.DataFrame,
    unassigned: pd.DataFrame,
    utilization: pd.DataFrame,
    transport_summary: Mapping[str, Any],
    *,
    period: str,
) -> dict[str, Any]:
    total_supply = result.total_supply_m3
    assigned = result.assigned_volume_m3
    by_zone_grade = []
    if not assignments.empty:
        for (zone, grade), group in assignments.groupby(["zone", "grade_id"]):
            volumes = group["volume_m3"].astype(float)
            delivered = group["delivered_cost_cad_m3"].astype(float)
            by_zone_grade.append(
                {
                    "zone": zone,
                    "grade": grade,
                    "assigned_m3": float(volumes.sum()),
                    "mean_delivered_cost_cad_m3": float((volumes * delivered).sum() / volumes.sum()),
                }
            )
    unassigned_by_grade = []
    if not unassigned.empty:
        for (zone, grade), group in unassigned.groupby(["zone", "grade_id"]):
            unassigned_by_grade.append(
                {
                    "zone": zone,
                    "grade": grade,
                    "unassigned_m3": float(group["volume_m3"].sum()),
                    "reason": ";".join(sorted(set(group["reason"].astype(str)))),
                }
            )
    class_rows = []
    for facility_class, group in utilization.groupby("facility_class"):
        nameplate = float(group["nameplate_m3_y"].astype(float).sum())
        assigned_class = float(group["assigned_m3"].astype(float).sum())
        class_rows.append(
            {
                "facility_class": facility_class,
                "facility_count": len(group),
                "nameplate_m3_y": nameplate,
                "assigned_m3": assigned_class,
                "utilization": assigned_class / nameplate if nameplate > 0 else 0.0,
                "binding_facility_count": int(group["capacity_binding"].astype(bool).sum()),
            }
        )
    binding_facilities = utilization[utilization["capacity_binding"].astype(bool)]
    binding_contracts = [
        row
        for row in result.contract_compliance
        if row["max_volume_m3"] > 0.0
        and float(row["assigned_m3"]) >= float(row["max_volume_m3"]) - 1e-6
    ]
    return {
        "period": period,
        "status": result.status,
        "objective_value_cad": result.objective_value_cad,
        "total_supply_m3": total_supply,
        "assigned_m3": assigned,
        "unassigned_m3": result.unassigned_volume_m3,
        "assigned_share": assigned / total_supply if total_supply > 0 else 0.0,
        "by_zone_grade": sorted(by_zone_grade, key=lambda row: (row["zone"], row["grade"])),
        "unassigned_by_zone_grade": sorted(
            unassigned_by_grade, key=lambda row: (row["zone"], row["grade"])
        ),
        "class_utilization": sorted(class_rows, key=lambda row: row["facility_class"]),
        "binding_facility_count": len(binding_facilities),
        "binding_facilities": binding_facilities[
            ["facility_id", "facility_name", "facility_class", "assigned_m3", "nameplate_m3_y", "utilization"]
        ].to_dict(orient="records"),
        "binding_contract_count": len(binding_contracts),
        "binding_contracts": [
            {
                "contract_id": row["contract_id"],
                "facility_id": row["facility_id"],
                "assigned_m3": row["assigned_m3"],
                "max_volume_m3": row["max_volume_m3"],
                "contract_source": row["contract_source"],
            }
            for row in binding_contracts[:50]
        ],
        "objective_decomposition": [dict(row) for row in result.objective_decomposition],
        "transport_summary": dict(transport_summary),
        "class_mix": {
            row["facility_class"]: {
                "assigned_m3": row["assigned_m3"],
                "share_of_assigned": (
                    row["assigned_m3"] / assigned if assigned > 0 else 0.0
                ),
                "nameplate_m3_y": row["nameplate_m3_y"],
            }
            for row in class_rows
        },
    }


def attach_stage_a_delivered_costs(
    supply: pd.DataFrame, stage_a_summary: Mapping[str, Any], *, period: str
) -> pd.DataFrame:
    """Attach zone/grade Stage A mean delivered cost to the supply ledger.

    The 2028 weighted mean is used for 2028 rows and as an explicit transport
    proxy for the 2021 report rows.  Unmapped rows carry no grade and stay at
    zero.
    """
    cost_by = {
        (row["zone"], row["grade"]): float(row["mean_delivered_cost_cad_m3"])
        for row in stage_a_summary["by_zone_grade"]
    }
    grades = supply["product"].map(_PRODUCT_TO_GRADE)
    delivered: list[float] = []
    basis: list[str] = []
    for zone, grade, year in zip(supply["zone"], grades, supply["year"].astype(str)):
        if grade is None:
            delivered.append(0.0)
            basis.append("not_applicable")
            continue
        delivered.append(cost_by.get((str(zone), str(grade)), 0.0))
        basis.append(
            "stage_a_2028_zone_grade_weighted_mean"
            if year == str(period)
            else "stage_a_2028_zone_grade_proxy"
        )
    result = supply.copy()
    result["delivered_cost_cad_m3"] = delivered
    result["delivered_cost_basis"] = basis
    return result


def _write_csv(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, lineterminator="\n")


def _json_safe(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(_json_safe(payload), indent=2, sort_keys=True, allow_nan=False)
    path.write_text(text + "\n", encoding="utf-8")


def _stage_b_residue_table(stage_b_dir: Path) -> pd.DataFrame:
    supply_path = stage_b_dir / "residue_supply.csv"
    assignment_path = stage_b_dir / "stage_b_assignment.csv"
    unassigned_path = stage_b_dir / "unassigned_residue.csv"
    supply = pd.read_csv(supply_path)
    assignments = pd.read_csv(assignment_path) if assignment_path.exists() else pd.DataFrame()
    unassigned = pd.read_csv(unassigned_path) if unassigned_path.exists() else pd.DataFrame()
    assigned_by = (
        assignments.groupby(["source_facility", "residue_type"])["odt"].sum()
        if not assignments.empty
        else pd.Series(dtype=float)
    )
    unassigned_by = (
        unassigned.groupby(["source_facility", "residue_type"])["odt"].sum()
        if not unassigned.empty
        else pd.Series(dtype=float)
    )
    recipients: dict[tuple[str, str], set[str]] = defaultdict(set)
    if not assignments.empty:
        for row in assignments.itertuples(index=False):
            recipients[(row.source_facility, row.residue_type)].add(row.recipient_facility)
    generated = supply.groupby(["source_facility", "residue_type"])["odt"].sum()
    generated_m3 = supply.groupby(["source_facility", "residue_type"])["volume_m3"].sum()
    rows = []
    for key, odt in generated.items():
        rows.append(
            {
                "source_facility": key[0],
                "residue_type": key[1],
                "generated_odt": float(odt),
                "generated_m3": float(generated_m3.get(key, 0.0)),
                "assigned_odt": float(assigned_by.get(key, 0.0)),
                "unassigned_odt": float(unassigned_by.get(key, 0.0)),
                "recipients": ";".join(sorted(recipients.get(key, set()))),
            }
        )
    frame = pd.DataFrame(rows)
    if frame.empty:
        raise BcChainError("Stage B produced no residue balance rows")
    frame["recipients"] = frame["recipients"].fillna("")
    return frame.sort_values(["source_facility", "residue_type"]).reset_index(drop=True)


def _stage_b_summary(stage_b_dir: Path, balance: pd.DataFrame) -> dict[str, Any]:
    validation_path = stage_b_dir / "validation_summary.json"
    bioenergy_path = stage_b_dir / "bioenergy_share.csv"
    objective_path = stage_b_dir / "objective_decomposition.csv"
    utilization_path = stage_b_dir / "stage_b_utilization.csv"
    validation = json.loads(validation_path.read_text(encoding="utf-8"))
    bioenergy = pd.read_csv(bioenergy_path)
    objective = pd.read_csv(objective_path)
    utilization = pd.read_csv(utilization_path)
    total = balance[["generated_odt", "assigned_odt", "unassigned_odt"]].sum()
    bio_row = bioenergy[bioenergy["scope"] == "TOTAL"]
    bioenergy_odt = float(bio_row["bioenergy_odt"].iloc[0]) if not bio_row.empty else 0.0
    class_rows = []
    for facility_class, group in utilization.groupby("facility_class"):
        received = float(group["received_odt"].astype(float).sum())
        capacity = float(group["capacity_odt_y"].astype(float).sum())
        class_rows.append(
            {
                "facility_class": facility_class,
                "facility_count": len(group),
                "capacity_odt_y": capacity,
                "received_odt": received,
                "utilization": received / capacity if capacity > 0 else 0.0,
                "binding_facility_count": int(group["capacity_binding"].astype(bool).sum()),
            }
        )
    objective_net = next(
        (
            row["value_cad"]
            for row in objective.to_dict(orient="records")
            if row["component"] == "net_contribution"
        ),
        0.0,
    )
    return {
        "status": validation.get("status", "unknown"),
        "generated_odt": float(total["generated_odt"]),
        "assigned_odt": float(total["assigned_odt"]),
        "unassigned_odt": float(total["unassigned_odt"]),
        "assigned_share": (
            float(total["assigned_odt"]) / float(total["generated_odt"])
            if float(total["generated_odt"]) > 0
            else 0.0
        ),
        "bioenergy_odt": bioenergy_odt,
        "bioenergy_share_of_generated": (
            bioenergy_odt / float(total["generated_odt"])
            if float(total["generated_odt"]) > 0
            else 0.0
        ),
        "bioenergy_share_of_assigned": (
            bioenergy_odt / float(total["assigned_odt"])
            if float(total["assigned_odt"]) > 0
            else 0.0
        ),
        "objective": objective.to_dict(orient="records"),
        "objective_net_contribution_cad": objective_net,
        "utilization": utilization.to_dict(orient="records"),
        "class_utilization": sorted(class_rows, key=lambda row: row["facility_class"]),
        "validation": validation.get("validation", {}),
        "counts": validation.get("counts", {}),
        "data_gaps": validation.get("data_gaps", []),
        "objective_mode": validation.get("objective_mode"),
        "objective_mode_reason": validation.get("objective_mode_reason"),
    }


def _residue_caps(repo_root: Path, year: str) -> dict[str, float]:
    aac, recovery, gross = load_zone_aac(repo_root, year)
    del aac
    caps = {}
    for zone in ZONE_NODE_IDS:
        # The time-path recovery fraction is applied to the gross taper residue;
        # the resulting base residue is the published OSeCAN cap basis.
        caps[zone] = gross[zone] * recovery[zone] * GJ_PER_ODT / 1_000_000.0
    return caps


def _markdown_table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    lines = ["| " + " | ".join(str(value) for value in headers) + " |"]
    lines.append("|" + "|".join("---" for _ in headers) + "|")
    for row in rows:
        lines.append("| " + " | ".join(str(value) for value in row) + " |")
    return "\n".join(lines)


def _build_report(
    supply: pd.DataFrame,
    stage_a_summary: Mapping[str, Any],
    stage_a_assignments: pd.DataFrame,
    utilization: pd.DataFrame,
    stage_b_summary: Mapping[str, Any],
    stage_b_balance: pd.DataFrame,
    caps: Mapping[str, float],
    years: Sequence[str],
    verification: Mapping[str, Any],
) -> str:
    lines = [
        "# All-BC forest-sector chain at CLEWS B1/B2 zone level",
        "",
        f"Generated {datetime.now(UTC).isoformat(timespec='seconds')}.",
        "",
        ("This is a diagnostic chain, not an operational plan.  It links the "
        "2025 VRI/RSYC area-weighted assortment batch to the 2028 AAC, the "
        "FFCM-derived BC facility network through the 2018 NRN road graph, and "
        "the Stage B sawmill-residue allocation.  Every proxy is named in the "
        "caveats."),
        "",
        "## Supply by zone (AAC, area, species/product/residue)",
        "",
    ]
    supply_rows = []
    for year in years:
        data = supply[supply["year"].astype(str) == str(year)]
        for zone in ("B1", "B2"):
            group = data[data["zone"] == zone]
            if group.empty:
                continue
            logs = group[group["product"].isin(_MERCHANTABLE_PRODUCTS)]
            residue = group[group["product"] == "residue"]
            supply_rows.append(
                [
                    zone,
                    year,
                    f"{group['aac_m3'].iloc[0]:,.0f}",
                    f"{group['harvest_area_ha'].iloc[0]:,.0f}",
                    f"{logs['volume_m3'].sum():,.0f}",
                    f"{residue['residue_odt'].sum():,.0f}",
                    f"{residue['residue_recoverable_odt'].sum():,.0f}",
                    f"{residue['residue_recoverable_odt'].sum() * GJ_PER_ODT / 1_000_000:,.2f}",
                    int(logs["species"].nunique()),
                ]
            )
    lines.append(
        _markdown_table(
            [
                "Zone",
                "Year",
                "AAC m3",
                "Harvest ha",
                "Log m3",
                "Gross residue odt",
                "Recoverable odt",
                "Recoverable PJ",
                "Species",
            ],
            supply_rows,
        )
    )
    lines.extend(
        [
            "",
            ("Product/species detail is in `bc_supply_by_zone.csv`.  Documented "
            "aliases recover the former SX/S/YC/H/B/ACT/PA gap; residual "
            "`UNMAPPED_BC` area is carried as an explicit zero-volume row with its "
            "excluded forest area and is not redistributed into named species."),
            "",
            "## Stage A (assigned/unassigned, utilization, binds, cost, class mix)",
            "",
            (f"Period {stage_a_summary['period']}.  Assigned "
            f"{stage_a_summary['assigned_m3']:,.0f} m3 of "
            f"{stage_a_summary['total_supply_m3']:,.0f} m3 "
            f"({stage_a_summary['assigned_share']:.1%}); objective "
            f"{stage_a_summary['objective_value_cad']:,.0f} CAD."),
            "",
        ]
    )
    a_rows = []
    for row in stage_a_summary["by_zone_grade"]:
        a_rows.append(
            [
                row["zone"],
                row["grade"],
                f"{row['assigned_m3']:,.0f}",
                f"{row['mean_delivered_cost_cad_m3']:.2f}",
            ]
        )
    for row in stage_a_summary["unassigned_by_zone_grade"]:
        a_rows.append(
            [row["zone"], row["grade"], f"-{row['unassigned_m3']:,.0f}", row["reason"]]
        )
    lines.append(
        _markdown_table(["Zone", "Grade", "Volume m3", "Delivered CAD/m3 / reason"], a_rows)
    )
    lines.extend(
        [
            "",
            "Facility class utilization:",
            "",
        ]
    )
    class_rows = [
        [
            row["facility_class"],
            row["facility_count"],
            f"{row['nameplate_m3_y']:,.0f}",
            f"{row['assigned_m3']:,.0f}",
            f"{row['utilization']:.1%}",
            row["binding_facility_count"],
        ]
        for row in stage_a_summary["class_utilization"]
    ]
    lines.append(
        _markdown_table(
            ["Class", "Facilities", "Nameplate m3/y", "Assigned m3", "Utilization", "Binding"],
            class_rows,
        )
    )
    lines.extend(
        [
            "",
            (f"Binding facilities: {stage_a_summary['binding_facility_count']}; "
            f"binding apportionment/nameplate contracts: "
            f"{stage_a_summary['binding_contract_count']}.  "
            "Top binding facilities and contracts are in "
            "`bc_stage_a_summary.json`."),
            "",
            (f"Transport: {stage_a_summary['transport_summary']['link_count']} "
            f"zone-facility-grade links; "
            f"{stage_a_summary['transport_summary'].get('ferry_link_count', 0)} "
            "use at least one ferry edge.  Ferry cost uses the flagged placeholder "
            f"{stage_a_summary['transport_summary'].get('ferry_fare_placeholder_cad_m3_km', FERRY_FARE_CAD_M3_KM):.2f} "
            "CAD/m3/km, not a BC Ferries tariff; crossing time is distance / 33 km/h."),
            "",
            "## Stage B (residue assigned/unassigned, bioenergy share, utilization)",
            "",
            (f"Generated {stage_b_summary['generated_odt']:,.0f} odt; assigned "
            f"{stage_b_summary['assigned_odt']:,.0f} odt "
            f"({stage_b_summary['assigned_share']:.1%}); unassigned "
            f"{stage_b_summary['unassigned_odt']:,.0f} odt; bioenergy "
            f"{stage_b_summary['bioenergy_odt']:,.0f} odt "
            f"({stage_b_summary['bioenergy_share_of_generated']:.1%} of generated, "
            f"{stage_b_summary['bioenergy_share_of_assigned']:.1%} of assigned)."),
            "",
        ]
    )
    b_rows = []
    for row in stage_b_balance.groupby("residue_type").sum(numeric_only=True).reset_index().itertuples(index=False):
        b_rows.append(
            [
                row.residue_type,
                f"{row.generated_odt:,.0f}",
                f"{row.assigned_odt:,.0f}",
                f"{row.unassigned_odt:,.0f}",
            ]
        )
    lines.append(
        _markdown_table(["Residue type", "Generated odt", "Assigned odt", "Unassigned odt"], b_rows)
    )
    lines.extend(
        [
            "",
            "Recipient class utilization:",
            "",
        ]
    )
    b_class_rows = [
        [
            row["facility_class"],
            row["facility_count"],
            f"{row['capacity_odt_y']:,.0f}",
            f"{row['received_odt']:,.0f}",
            f"{row['utilization']:.1%}",
            row["binding_facility_count"],
        ]
        for row in stage_b_summary["class_utilization"]
    ]
    lines.append(
        _markdown_table(
            ["Class", "Facilities", "Capacity odt/y", "Received odt", "Utilization", "Binding"],
            b_class_rows,
        )
    )
    lines.extend(
        [
            "",
            (f"Stage B net contribution {stage_b_summary['objective_net_contribution_cad']:,.0f} "
            "CAD (revenue - transport - processing).  Full objective decomposition "
            "is in `bc_stage_b_summary.json`."),
            "",
            "## Comparison to OSeCAN caps",
            "",
            ("The OSeCAN caps are the 2028+ base residue availability: B1 "
            "3.672 PJ/y and B2 39.287 PJ/y.  The comparison below uses the "
            "time-path gross taper residue x base recovery fraction, the same "
            "basis as the published caps; the scaled batch residue is a second "
            "independent check."),
            "",
        ]
    )
    cap_rows = []
    for zone in ("B1", "B2"):
        group = supply[(supply["year"] == "2028") & (supply["zone"] == zone)]
        batch_pj = (
            group.loc[group["product"] == "residue", "residue_recoverable_odt"].sum()
            * GJ_PER_ODT
            / 1_000_000
        )
        cap = caps[zone]
        cap_rows.append(
            [
                zone,
                f"{cap:.3f}",
                f"{batch_pj:.3f}",
                f"{batch_pj - cap:+.3f}",
                f"{(batch_pj / cap - 1):+.1%}" if cap > 0 else "n/a",
            ]
        )
    lines.append(
        _markdown_table(
            ["Zone", "OSeCAN cap PJ", "Batch recoverable PJ", "Difference PJ", "Difference %"],
            cap_rows,
        )
    )
    lines.extend(
        [
            "",
            ("The batch residue is not a cap calibration: the batch is a "
            "per-hectare taper estimate scaled to the same AAC, while OSeCAN is "
            "a recovery-adjusted policy cap.  Agreement within a few percent is "
            "supportive; disagreement is an evidence gap, not a model target."),
            "",
            "## Caveats/tests/commits/publish/status",
            "",
            ("- **Zone nodes**: area-weighted TSA centroids from dissolved FADM "
            "TSA polygons, weighted by 2018 apportionment x THLB share, and "
            "restricted to TSAs whose nearest NRN node is in the main "
            "road-connected component.  With NRN ferry edges, the main component "
            "includes Vancouver Island and coastal TSAs where a terminal snapped "
            "within the documented tolerance.  Remaining island-only facilities "
            "stay unlinked and B1 sawlog unassigned volume is an upper bound under "
            "this proxy, not evidence that the fibre is uneconomic."),
            ("- **Facilities**: FFCM/Mills DB proxies; capacities are normalized "
            "processing-line infeed, not licensed or current operating capacity."),
            ("- **Contracts**: 2018 apportionment-derived maximum entitlements are "
            "diagnostic; min=0 and gate=0 are placeholders, not supply agreements."),
            ("- **Unmapped species**: the area table now applies documented aliases "
            "(SX, S, YC, H, B, ACT, PA) with rule and confidence columns.  Any "
            "residual `UNMAPPED_BC` area is excluded from species composition and "
            "reported explicitly; it is never silently redistributed."),
            (f"- **Transport**: 2018 NRN road graph plus NRN `FERRYSEG` shortest "
             f"paths.  Ferry cost uses a flagged placeholder of "
             f"{stage_a_summary.get('transport_summary', {}).get('ferry_fare_placeholder_cad_m3_km', FERRY_FARE_CAD_M3_KM):.2f} "
             "CAD/m3/km, not a BC Ferries tariff; crossing time is distance / "
             "33 km/h.  No route capacity, sailing schedule, queue, or rail/water "
             "mode is modelled."),
            ("- **Stage B separation**: Stage A residue assigned to BIO/PEL is a "
            "separate harvest-residue allocation; Stage B sawmill residue is "
            "derived from Stage A SAW log assignments.  The two models do not "
            "share recipient capacity, so their bioenergy totals are additive "
            "diagnostics, not one co-optimized balance."),
            ("- **Roadside cost**: not modelled; Stage A delivered cost is "
            "transport + gate, and processing/revenue come from FFCM lines."),
            "",
            "### Tests",
            "",
        ]
    )
    for name, status in verification.items():
        lines.append(f"- `{name}`: {status}")
    lines.extend(
        [
            "",
            "### Reproducibility and status",
            "",
            ("- Run `make`-free command: `.venv/bin/python -m "
            "workflows.national.assignments.bc_chain`."),
            "- Manifest hashes and row counts are in `manifest.json`.",
            ("- Parent and `data` submodule commits, annex publication, and "
            "`git status --porcelain` are recorded by the operator after the run."),
            "",
        ]
    )
    return "\n".join(lines)


def _manifest(
    repo_root: Path,
    out_dir: Path,
    *,
    years: Sequence[str],
    period: str,
    stage_a_summary: Mapping[str, Any],
    stage_b_summary: Mapping[str, Any],
    zone_nodes: pd.DataFrame,
    transport_summary: Mapping[str, Any],
    verification: Mapping[str, Any],
    started: datetime,
) -> dict[str, Any]:
    outputs = {}
    for path in sorted(out_dir.glob("*")):
        if path.is_file():
            outputs[path.name] = {
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
    inputs = {}
    for relative in (
        TIME_PATH,
        BATCH,
        AREA_WEIGHTS,
        ZONE_CROSSWALK,
        TSA_ZONE_CROSSWALK,
        FACILITIES_RECOVERED,
        APPORTIONMENT_CONTRACTS,
    ):
        path = _out(repo_root, relative)
        inputs[str(relative)] = _sha256(path)
    return {
        "artifact_version": CODE_VERSION,
        "generated_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "runtime_seconds": (datetime.now(UTC) - started).total_seconds(),
        "scenario": "bc_chain_b1_b2_stage_a_b",
        "period": period,
        "years": list(years),
        "status": (
            "pass"
            if stage_b_summary.get("status") == "pass"
            and all(value == "pass" for value in verification.values())
            else "diagnostic_partial"
        ),
        "inputs": inputs,
        "outputs": outputs,
        "zone_nodes": zone_nodes.to_dict(orient="records"),
        "transport_summary": dict(transport_summary),
        "stage_a_summary": dict(stage_a_summary),
        "stage_b_summary": dict(stage_b_summary),
        "tests": dict(verification),
        "caveats": [
            "zone nodes are area-weighted TSA centroids, not harvest origins",
            "facilities and capacities are FFCM/Mills DB proxies",
            "apportionment contracts are diagnostic maximum entitlements",
            "19.5% unmapped species area is excluded from species composition",
            "NRN 2018 road+ferry shortest paths; no route capacity or rail/water",
            "Stage A harvest residue and Stage B sawmill residue are separate models",
            "roadside/stumpage cost is not modelled",
        ],
    }


def run_bc_chain(
    repo_root: Path,
    *,
    out_dir: Path = DEFAULT_OUT_DIR,
    period: str = DEFAULT_PERIOD,
    years: Sequence[str] = DEFAULT_YEARS,
    nrn_zip: Path = DEFAULT_NRN_ZIP,
) -> dict[str, Any]:
    """Run the reproducible B1/B2 supply, Stage A, and Stage B chain."""
    started = datetime.now(UTC)
    repo_root = Path(repo_root)
    out_path = _out(repo_root, out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    supply_frames = [build_bc_supply(repo_root, year=str(year)) for year in years]
    supply = pd.concat(supply_frames, ignore_index=True)
    _write_csv(out_path / "bc_supply_by_zone.csv", supply)

    graph = build_road_graph(_out(repo_root, nrn_zip))
    zone_nodes = build_zone_nodes(repo_root, graph=graph)
    _write_csv(out_path / "bc_zone_nodes.csv", zone_nodes)

    stage_a, inputs, links, transport_screen, transport_summary, ledger = solve_stage_a(
        repo_root, period=period, supply=supply, zone_nodes=zone_nodes, graph=graph
    )
    assignments = _assignment_frame(stage_a, ledger)
    unassigned = _unassigned_frame(stage_a, ledger)
    utilization = _utilisation_rows(stage_a)
    check_stage_a_balance(ledger, assignments, unassigned)
    _write_csv(out_path / "bc_stage_a_assignment.csv", assignments)
    _write_csv(out_path / "bc_stage_a_unassigned.csv", unassigned)
    _write_csv(out_path / "bc_facility_utilization.csv", utilization)
    _write_csv(out_path / "bc_stage_a_transport_links.csv", pd.DataFrame([link.to_dict() for link in links]))
    _write_csv(out_path / "bc_stage_a_transport_screen.csv", pd.DataFrame(transport_screen))
    _write_csv(out_path / "bc_stage_a_contracts.csv", pd.DataFrame([contract.to_dict() for contract in inputs.contracts]))
    stage_a_summary = _stage_a_summary(
        stage_a, assignments, unassigned, utilization, transport_summary, period=period
    )
    _write_json(out_path / "bc_stage_a_summary.json", stage_a_summary)
    supply = attach_stage_a_delivered_costs(supply, stage_a_summary, period=period)
    _write_csv(out_path / "bc_supply_by_zone.csv", supply)

    # Stage B expects the TSA24-compatible file names and columns in its run
    # directory.  Stage A assignments already carry those fields plus zonal
    # diagnostics, so write the compatible projection explicitly.
    stage_a_run = out_path / "stage_a_run"
    stage_a_run.mkdir(parents=True, exist_ok=True)
    compatible_columns = [
        "assignment_id",
        "period",
        "supply_id",
        "origin",
        "grade_id",
        "facility_id",
        "facility_name",
        "facility_class",
        "volume_m3",
        "moisture_wet_basis",
        "supply_cost_cad_m3",
        "transport_cost_cad_m3",
        "gate_cost_cad_m3",
        "processing_cost_cad_m3",
        "product_revenue_cad_m3",
        "net_contribution_cad_m3",
        "contribution_cad",
        "transport_link_id",
        "evidence",
    ]
    compatible = assignments.reindex(columns=compatible_columns)
    _write_csv(stage_a_run / "facility_assignment.csv", compatible)
    facilities = pd.read_csv(_out(repo_root, FACILITIES_RECOVERED))
    facilities.to_csv(out_path / "facilities_bc.csv", index=False, lineterminator="\n")
    facilities.to_csv(
        stage_a_run / "facilities_bc.csv", index=False, lineterminator="\n"
    )

    stage_b_dir = out_path / "stage_b"
    stage_b_run = run_stage_b(
        repo_root,
        pilot_dir=stage_a_run,
        out_dir=stage_b_dir,
        period=period,
        nrn_zip=nrn_zip,
    )
    del stage_b_run
    stage_b_balance = _stage_b_residue_table(stage_b_dir)
    check_stage_b_balance(stage_b_balance)
    stage_b_summary = _stage_b_summary(stage_b_dir, stage_b_balance)
    _write_csv(out_path / "bc_stage_b_residue.csv", stage_b_balance)
    _write_csv(
        out_path / "bc_stage_b_utilization.csv",
        pd.read_csv(stage_b_dir / "stage_b_utilization.csv"),
    )
    _write_csv(
        out_path / "bc_stage_b_assignment.csv",
        pd.read_csv(stage_b_dir / "stage_b_assignment.csv"),
    )
    _write_json(out_path / "bc_stage_b_summary.json", stage_b_summary)

    caps = _residue_caps(repo_root, period)
    verification = {
        "zone_supply_aac_conservation": "pass",
        "zone_supply_recoverable_le_gross": "pass",
        "stage_a_mass_balance": "pass",
        "stage_a_unit_basis": "pass",
        "stage_b_mass_balance": "pass",
        "stage_b_validation": stage_b_summary.get("status", "unknown"),
    }
    report = _build_report(
        supply,
        stage_a_summary,
        assignments,
        utilization,
        stage_b_summary,
        stage_b_balance,
        caps,
        years,
        verification,
    )
    (out_path / "BC_CHAIN_REPORT.md").write_text(report, encoding="utf-8")
    manifest = _manifest(
        repo_root,
        out_path,
        years=years,
        period=period,
        stage_a_summary=stage_a_summary,
        stage_b_summary=stage_b_summary,
        zone_nodes=zone_nodes,
        transport_summary=transport_summary,
        verification=verification,
        started=started,
    )
    _write_json(out_path / "manifest.json", manifest)
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--period", default=DEFAULT_PERIOD)
    parser.add_argument(
        "--years", nargs="+", default=list(DEFAULT_YEARS), help="supply years"
    )
    args = parser.parse_args(argv)
    manifest = run_bc_chain(
        args.repo_root,
        out_dir=args.out_dir,
        period=args.period,
        years=tuple(str(year) for year in args.years),
    )
    print(json.dumps({"status": manifest["status"], "outputs": len(manifest["outputs"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
