"""BC + QC transport cost helpers for the Phase 3 diagnostic LP path.

Cost-surface carrier referenced by ``metadata/source-reviews/nrn_vectors.md``
and ``metadata/source-reviews/nrwn_vectors.md`` (forward plan). Until Phase
3.10 materialises the NRN and NRWN GeoGratis vector downloads into
``data/external/nrcan_geogratis/``, this module ships a CAG-friendly
placeholder cost surface for BC + QC so the LP path in
``fresh_fibre/bc_qc_mini_2prov.py`` has a deterministic per-tonne-km cost
arithmetic to call against.

PLACEHOLDER — verify against NRN/NRWn post-Phase 3.10.

Reference: BC Rural Dividend + SaskSource / Ontario Trucking Association
benchmarks. NRN/NRWn-derived tariff surface is a Phase 3.10 workstream.

Public surface: ``haversine_km``, ``great_circle_distance_km``,
``road_haul_cost_per_tonne_km``, ``rail_haul_cost_per_tonne_km``,
``road_segment_cost``, ``rail_segment_cost``, ``bc_supply_node_centroid``,
``qc_supply_node_centroid``, ``bc_facility_centroid``, ``qc_facility_centroid``,
``mileage_placeholder_km``, ``total_haul_cost_bc_qc``.

Guard-clause posture: negative distances → ``ValueError``; empty tables →
``0.0`` (early-exit); unknown centroids → DEBUG log + supply-bucketed
mileage placeholder → ``(None, None)`` (no fail-fast; per AGENTS.md the
fail-fast posture is reserved for exceptional cases).
"""

from __future__ import annotations

import logging
import math
from typing import Final


LOGGER = logging.getLogger("fresh_fibre.transport_cost")
EARTH_RADIUS_KM: Final[float] = 6371.0088  # IUGG mean Earth radius

# Locomotive-diesel placeholder for railcar switching + initial-surge duty
# cycle. Class-1 locomotives consume about 0.05 L diesel per railcar·km;
# verify against NRWN-derived tariff data post-Phase 3.10.
RAIL_DIESEL_L_PER_RAILCAR_KM: Final[float] = 0.05

# Province-aware cost parameter table (CAG-friendly placeholders,
# filled by BC Rural Dividend + SaskSource / OTA benchmarks).
# ``rail_electricity_cad_per_tonne_trip`` is the per-trip-equivalent
# surcharge (amortised per·km in ``rail_haul_cost_per_tonne_km``).
PROVINCE_COST_PARAMS: Final[dict[str, dict[str, float]]] = {
    "BC": {
        "fuel_cad_per_l": 1.45, "fuel_l_per_km": 0.45,
        "labour_cad_per_tonne": 6.50, "truck_payload_tonnes": 33.0,
        "overhead_pct_road": 0.18,
        "rail_electricity_cad_per_tonne_trip": 0.085,
        "rail_labour_cad_per_tonne": 2.20,
        "railcar_payload_tonnes": 95.0, "overhead_pct_rail": 0.10,
    },
    "QC": {
        "fuel_cad_per_l": 1.40, "fuel_l_per_km": 0.42,
        "labour_cad_per_tonne": 6.20, "truck_payload_tonnes": 33.0,
        "overhead_pct_road": 0.18,
        "rail_electricity_cad_per_tonne_trip": 0.090,
        "rail_labour_cad_per_tonne": 2.10,
        "railcar_payload_tonnes": 95.0, "overhead_pct_rail": 0.10,
    },
}

ROAD_SEGMENT_MULTIPLIERS: Final[dict[str, float]] = {
    "pipeline": 1.00, "arterial": 1.15, "local": 1.30,  # resource / forest
}
RAIL_SEGMENT_MULTIPLIERS: Final[dict[str, float]] = {
    "main": 0.85, "branch": 1.10, "yard": 1.50,
}
# Supply-bucket mileage placeholders (km). Operational fallback when a
# centroid is missing per AGENTS.md (no fail-fast).
SUPPLY_BUCKET_MILEAGE_KM: Final[dict[str, float]] = {
    "coast": 110.0, "interior": 145.0, "residue": 175.0,
    "qc_saguenay": 130.0, "qc_mixedwood": 165.0,
    "qc_disturbance": 195.0, "qc_residue": 125.0, "shared": 150.0,
}


# ---------------------------------------------------------------------------
# Geometry primitives (pure).
# ---------------------------------------------------------------------------

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance (km) between two lat/lon points in degrees.

    Pure function; same inputs → same output. Numerical formulation is
    the spherical haversine law, stable for short distances (≪ 1/4
    circumference).
    """
    if not all(math.isfinite(x) for x in (lat1, lon1, lat2, lon2)):
        raise ValueError(
            f"haversine_km requires finite lat/lon; got "
            f"({lat1!r}, {lon1!r}, {lat2!r}, {lon2!r})."
        )
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    return EARTH_RADIUS_KM * 2.0 * math.asin(min(1.0, math.sqrt(a)))


def great_circle_distance_km(
    coords_a: tuple[float, float], coords_b: tuple[float, float],
) -> float:
    """Great-circle distance (km) between two ``(lat, lon)`` tuples."""
    if len(coords_a) != 2 or len(coords_b) != 2:
        raise ValueError(
            f"great_circle_distance_km expects (lat, lon) tuples; got "
            f"{coords_a!r}, {coords_b!r}."
        )
    return haversine_km(coords_a[0], coords_a[1], coords_b[0], coords_b[1])


# ---------------------------------------------------------------------------
# Per-tonne-km cost primitives.
# ---------------------------------------------------------------------------

def _province_params(province: str) -> dict[str, float]:
    """Resolve a province parameter row with a fail-fast on unknown keys.

    Fail-fast justified: province is a controlled call-site literal
    (``"BC"`` / ``"QC"``); an unknown key signals a programmer error.
    """
    try:
        return PROVINCE_COST_PARAMS[province]
    except KeyError as exc:
        raise ValueError(
            f"transport_cost: unknown province {province!r}; "
            f"supported: {sorted(PROVINCE_COST_PARAMS.keys())}."
        ) from exc


def road_haul_cost_per_tonne_km(
    distance_km: float,
    fuel_cad_per_l: float = 1.45,
    fuel_l_per_km: float = 0.42,
    labour_cad_per_tonne: float = 6.50,
    truck_payload_tonnes: float = 33.0,
    overhead_pct: float = 0.18,
) -> float:
    """Return the per-tonne·km cost of a road haul trip.

    Cost: marginal fuel (amortised by payload) + trip-fixed labour
    (amortised per·km) + overhead. Defaults match CAG placeholders for
    BC + QC; call sites needing strict BC vs QC rates should pass the
    province parameters from :data:`PROVINCE_COST_PARAMS` explicitly.

    Raises ``ValueError`` on negative distance or non-positive payload.
    """
    if distance_km < 0:
        raise ValueError(f"distance_km must be non-negative; got {distance_km}")
    if truck_payload_tonnes <= 0:
        raise ValueError(
            f"truck_payload_tonnes must be positive; got {truck_payload_tonnes}"
        )
    safe_distance = max(distance_km, 1.0)
    fuel_per_tonne_km = (fuel_cad_per_l * fuel_l_per_km) / truck_payload_tonnes
    labour_per_tonne_km = labour_cad_per_tonne / safe_distance
    return (fuel_per_tonne_km + labour_per_tonne_km) * (1.0 + overhead_pct)


def rail_haul_cost_per_tonne_km(
    distance_km: float,
    fuel_cad_per_l: float = 1.35,
    electricity_cad_per_tonne_km: float = 0.085,
    labour_cad_per_tonne: float = 2.20,
    railcar_payload_tonnes: float = 95.0,
    overhead_pct: float = 0.10,
) -> float:
    """Return the per-tonne·km cost of a rail haul trip.

    Cost decomposition:

    1. ``electricity_cad_per_tonne_km ÷ distance_km`` — placeholder
       surcharge. We treat the parameter as a *per-trip-equivalent*
       CAD-per-tonne traction surcharge (CAG-conservative rate) and
       amortise across the trip. Phase 3.10 will replace this with a
       straight per-km marginal rate once NRWN tariff data lands.
    2. ``labour_cad_per_tonne ÷ distance_km`` — trip-fixed crew cost.
    3. ``fuel_cad_per_l × RAIL_DIESEL_L_PER_RAILCAR_KM ÷ railcar_payload_tonnes``
       — locomotive diesel for yard-switching + surge cycle.

    Raises ``ValueError`` on negative distance or non-positive payload.
    """
    if distance_km < 0:
        raise ValueError(f"distance_km must be non-negative; got {distance_km}")
    if railcar_payload_tonnes <= 0:
        raise ValueError(
            f"railcar_payload_tonnes must be positive; got {railcar_payload_tonnes}"
        )
    safe_distance = max(distance_km, 1.0)
    electric_per_tonne_km = electricity_cad_per_tonne_km / safe_distance
    labour_per_tonne_km = labour_cad_per_tonne / safe_distance
    diesel_per_tonne_km = (
        fuel_cad_per_l * RAIL_DIESEL_L_PER_RAILCAR_KM / railcar_payload_tonnes
    )
    return (
        electric_per_tonne_km + labour_per_tonne_km + diesel_per_tonne_km
    ) * (1.0 + overhead_pct)


# ---------------------------------------------------------------------------
# Segment-cost helpers (per-tonne × length × class multiplier).
# ---------------------------------------------------------------------------

def road_segment_cost(seg_length_km: float, seg_class: str) -> float:
    """Per-tonne cost for a single road segment of class ``seg_class``.

    ``seg_class`` ∈ ``{"pipeline", "arterial", "local"}``;
    see :data:`ROAD_SEGMENT_MULTIPLIERS`. Unknown classes fall back to
    multiplier 1.0 with a DEBUG log.
    """
    if seg_length_km < 0:
        raise ValueError(f"seg_length_km must be non-negative; got {seg_length_km}")
    per_tonne_km = road_haul_cost_per_tonne_km(seg_length_km)
    multiplier = ROAD_SEGMENT_MULTIPLIERS.get(seg_class)
    if multiplier is None:
        LOGGER.debug(
            "road_segment_cost: unknown seg_class %r; defaulting to multiplier 1.00",
            seg_class,
        )
        multiplier = 1.0
    return per_tonne_km * seg_length_km * multiplier


def rail_segment_cost(seg_length_km: float, seg_class: str) -> float:
    """Per-tonne cost for a single rail segment of class ``seg_class``.

    ``seg_class`` ∈ ``{"main", "branch", "yard"}``; see
    :data:`RAIL_SEGMENT_MULTIPLIERS`. Unknown classes fall back to
    multiplier 1.0 with a DEBUG log.
    """
    if seg_length_km < 0:
        raise ValueError(f"seg_length_km must be non-negative; got {seg_length_km}")
    per_tonne_km = rail_haul_cost_per_tonne_km(seg_length_km)
    multiplier = RAIL_SEGMENT_MULTIPLIERS.get(seg_class)
    if multiplier is None:
        LOGGER.debug(
            "rail_segment_cost: unknown seg_class %r; defaulting to multiplier 1.00",
            seg_class,
        )
        multiplier = 1.0
    return per_tonne_km * seg_length_km * multiplier


# ---------------------------------------------------------------------------
# Centroid lookup tables (BC + QC supply + facility nodes).
# ---------------------------------------------------------------------------
# Placeholder centroids aligned to the BCMini / QCMini fixture
# vocabulary (``metadata/ffcm-archive/prototype-inputs/bc-qc-mini-2prov``).
# Phase 3.10 ingestion of NRN 2.0 vectors will refine these via DataLad
# materialisation; here they serve as deterministic routings for the
# Phase 3 cost-surface validation.
_BC_SUPPLY_CENTROIDS = {
    "supply_coast_01": (50.10, -125.30),
    "supply_interior_01": (52.10, -121.40),
    "supply_interior_02": (53.20, -122.50),
    "supply_residue_02": (49.30, -123.10),
}
_BC_FACILITY_CENTROIDS = {
    "mill_sawmill_coast": (50.00, -125.20),
    "mill_sawmill_interior": (52.20, -121.20),
    "mill_pulp_interior": (52.30, -121.80),
    "mill_pellet_interior": (52.50, -121.40),
    "plant_bioenergy_coast": (50.10, -125.10),
    "export_vancouver": (49.30, -123.10),
}
_QC_SUPPLY_CENTROIDS = {
    "supply_qc_saguenay_01": (48.50, -71.00),
    "supply_qc_mixedwood_02": (47.40, -72.80),
    "supply_qc_disturbance_02": (47.30, -73.10),
    "supply_qc_residue_01": (48.20, -71.80),
}
_QC_FACILITY_CENTROIDS = {
    "mill_sawmill_qc": (48.40, -71.10),
    "mill_pulp_qc": (48.60, -71.30),
    "plant_bioenergy_qc": (48.20, -71.50),
    "export_montreal": (45.50, -73.60),
}


def _centroid_from_table(
    table: dict[str, tuple[float, float]],
    node_id: str,
    *,
    table_label: str,
) -> tuple[float | None, float | None]:
    if node_id in table:
        return table[node_id]
    LOGGER.debug(
        "%s: no centroid registered for node_id=%r; "
        "callers should fall back to supply-bucketed mileage",
        table_label,
        node_id,
    )
    return (None, None)


def bc_supply_node_centroid(supply_node_id: str) -> tuple[float | None, float | None]:
    """Return ``(lat, lon)`` for a BC supply node or ``(None, None)`` if unknown."""
    return _centroid_from_table(
        _BC_SUPPLY_CENTROIDS, supply_node_id, table_label="bc_supply_node_centroid",
    )


def qc_supply_node_centroid(supply_node_id: str) -> tuple[float | None, float | None]:
    """Return ``(lat, lon)`` for a QC supply node or ``(None, None)`` if unknown."""
    return _centroid_from_table(
        _QC_SUPPLY_CENTROIDS, supply_node_id, table_label="qc_supply_node_centroid",
    )


def bc_facility_centroid(facility_id: str) -> tuple[float | None, float | None]:
    """Return ``(lat, lon)`` for a BC facility or ``(None, None)`` if unknown."""
    return _centroid_from_table(
        _BC_FACILITY_CENTROIDS, facility_id, table_label="bc_facility_centroid",
    )


def qc_facility_centroid(facility_id: str) -> tuple[float | None, float | None]:
    """Return ``(lat, lon)`` for a QC facility or ``(None, None)`` if unknown."""
    return _centroid_from_table(
        _QC_FACILITY_CENTROIDS, facility_id, table_label="qc_facility_centroid",
    )


def mileage_placeholder_km(supply_node_id: str) -> float:
    """Mileage placeholder (km) for an unknown supply bucket.

    Bucket key is derived from the supply node id prefix; unknown
    buckets default to ``SUPPLY_BUCKET_MILEAGE_KM['shared']``. Per
    AGENTS.md this is the operational fallback when a centroid is
    missing (no fail-fast).
    """
    bucket = "shared"
    if supply_node_id.startswith("supply_coast"):
        bucket = "coast"
    elif supply_node_id.startswith("supply_interior"):
        bucket = "interior"
    elif supply_node_id.startswith("supply_residue"):
        bucket = "residue"
    elif supply_node_id.startswith("supply_qc_saguenay"):
        bucket = "qc_saguenay"
    elif supply_node_id.startswith("supply_qc_mixedwood"):
        bucket = "qc_mixedwood"
    elif supply_node_id.startswith("supply_qc_disturbance"):
        bucket = "qc_disturbance"
    elif supply_node_id.startswith("supply_qc_residue"):
        bucket = "qc_residue"
    return SUPPLY_BUCKET_MILEAGE_KM[bucket]


# ---------------------------------------------------------------------------
# Total haul cost aggregator.
# ---------------------------------------------------------------------------

def _row_distance_km(row: dict) -> float:
    """Extract a route distance (km) from a supply-to-facility row.

    Accepts ``distance_km`` (numeric) or ``distance`` (string in km).
    Falls back to :func:`mileage_placeholder_km` on the supply node id
    when no distance is present.
    """
    raw = row.get("distance_km")
    if raw not in (None, ""):
        try:
            return float(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"total_haul_cost_bc_qc: row distance_km is not float-coercible: "
                f"{raw!r}"
            ) from exc
    raw = row.get("distance")
    if raw not in (None, ""):
        try:
            return float(raw)
        except (TypeError, ValueError):
            pass
    supply_node_id = str(row.get("supply_node_id") or row.get("origin_node_id") or "")
    return mileage_placeholder_km(supply_node_id)


def _row_mode(row: dict) -> str:
    """Normalise the mode string on a supply-to-facility row."""
    raw = str(row.get("mode") or row.get("transport_mode") or "road").strip().lower()
    return "rail" if raw in {"rail", "train"} else "road"


def _row_province(row: dict) -> str:
    """Infer the province key on a supply-to-facility row."""
    raw = str(row.get("province") or "").strip().upper()
    if raw in PROVINCE_COST_PARAMS:
        return raw
    node_id = str(row.get("supply_node_id") or row.get("origin_node_id") or "")
    return "QC" if node_id.startswith("supply_qc_") or "qc_" in node_id else "BC"


def total_haul_cost_bc_qc(supply_to_facility_table: list[dict]) -> float:
    """Sum the per-tonne·km haul cost across a BC + QC route table.

    Each row must contain a ``supply_node_id`` (or ``origin_node_id``)
    and a ``transport_mode`` (or ``mode``). Optional: ``distance_km`` /
    ``distance`` (string km), ``province``. Empty input → ``0.0``
    (early-exit before any arithmetic).
    """
    if not supply_to_facility_table:
        return 0.0
    total = 0.0
    for row in supply_to_facility_table:
        if not isinstance(row, dict):
            raise ValueError(
                f"total_haul_cost_bc_qc: row must be a dict; got {type(row).__name__}"
            )
        distance_km = _row_distance_km(row)
        params = _province_params(_row_province(row))
        if _row_mode(row) == "rail":
            per_tonne_km = rail_haul_cost_per_tonne_km(
                distance_km=distance_km,
                fuel_cad_per_l=params["fuel_cad_per_l"],
                electricity_cad_per_tonne_km=params["rail_electricity_cad_per_tonne_trip"],
                labour_cad_per_tonne=params["rail_labour_cad_per_tonne"],
                railcar_payload_tonnes=params["railcar_payload_tonnes"],
                overhead_pct=params["overhead_pct_rail"],
            )
        else:
            per_tonne_km = road_haul_cost_per_tonne_km(
                distance_km=distance_km,
                fuel_cad_per_l=params["fuel_cad_per_l"],
                fuel_l_per_km=params["fuel_l_per_km"],
                labour_cad_per_tonne=params["labour_cad_per_tonne"],
                truck_payload_tonnes=params["truck_payload_tonnes"],
                overhead_pct=params["overhead_pct_road"],
            )
        total += per_tonne_km * distance_km
    return total


__all__ = [
    "haversine_km", "great_circle_distance_km",
    "road_haul_cost_per_tonne_km", "rail_haul_cost_per_tonne_km",
    "road_segment_cost", "rail_segment_cost",
    "bc_supply_node_centroid", "qc_supply_node_centroid",
    "bc_facility_centroid", "qc_facility_centroid",
    "mileage_placeholder_km", "total_haul_cost_bc_qc",
]
