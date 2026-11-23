"""Transport-radius capacity gap v1.

This screening artifact asks a narrow question: for each province and facility
class, how much assigned harvest volume moves in each road-distance band, how
much eligible local capacity exists in the same band, and where long-haul
volume is concentrated?  It uses the committed v8 local-first assignment and
the v9 transport-aware assignment, the capacity v3 facility layer, and the
transport cost v1 truck curve.

The distance origin is the unweighted ADM1 polygon centroid of each province,
not a harvest node or a routed origin.  It is therefore a centroid-origin
screening measure, not an observed route distance or a siting study.  No
national claim, policy recommendation, official OSeCAN change, or UI change is
made.

Labels: ``model_estimate`` / ``observed=false`` / ``policy_grade=false``.  A
``D`` in either status column of the parent or data repository git status halts
the build before any output is written.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import subprocess
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from workflows.national.facilities import national_facility_assignment_v9 as v9
from workflows.national.facilities import transport_cost_v1 as transport
from workflows.national.facilities.national_facility_assignment import (
    haversine_km,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_ROOT = REPO_ROOT / "data"
OUTPUT_DIR = (
    DATA_ROOT / "derived/national/facilities/transport_capacity_gap_v1"
)

V8_ASSIGNMENT_CSV = (
    DATA_ROOT
    / "derived/national/facilities/national_facility_assignment_v8"
    / "facility_assignment.csv"
)
V9_ASSIGNMENT_CSV = (
    DATA_ROOT
    / "derived/national/facilities/national_facility_assignment_v9"
    / "v9_facility_assignment.csv"
)
CAPACITY_CSV = (
    DATA_ROOT
    / "derived/national/facilities/national_facility_capacity_v3"
    / "national_facility_capacity_v3.csv"
)
INVENTORY_CSV = (
    DATA_ROOT
    / "derived/national/facilities/national_facility_inventory_v1"
    / "national_facilities.csv"
)
HARVEST_CSV = (
    DATA_ROOT / "derived/national/harvest_basis/provincial_allocation/119m.csv"
)
BOUNDARY_GEOJSON = (
    DATA_ROOT / "raw/boundaries/canada_adm1_geoboundaries_simplified.geojson"
)
FACTORS_CSV = (
    DATA_ROOT
    / "derived/national/facilities/facility_road_distance_audit_v2"
    / "province_road_factors.csv"
)
TRANSPORT_PARAMETERS_CSV = (
    DATA_ROOT / "derived/national/facilities/transport_cost_v1" / "parameters.csv"
)

ARTIFACT = "transport_capacity_gap_v1"
ARTIFACT_VERSION = "1.0.0"
PRIMARY_SCENARIO = "province_capacity_floor"

EVIDENCE_LABEL = "model_estimate"
OBSERVED = False
POLICY_GRADE = False
NATIONAL_CLAIM = False
OFFICIAL_OSECAN_CHANGED = False
UI_CHANGED = False
SITING_OPTIMIZATION = False

PAYLOAD_M3 = v9.PAYLOAD_M3
DENSITY_ODT_PER_M3 = v9.DENSITY_ODT_PER_M3
LHV_GJ_PER_ODT = v9.LHV_GJ_PER_ODT
ENERGY_GJ_PER_M3 = DENSITY_ODT_PER_M3 * LHV_GJ_PER_ODT
LOCALIZATION_REFERENCE_KM = 50.0
HIGH_EXPOSURE_SHARE = 0.50
HIGH_EXPOSURE_MIN_M3 = 1_000_000.0
VOLUME_TOLERANCE_M3 = 1e-3
CAPACITY_TOLERANCE_M3 = 1e-3

CLASS_ORDER = tuple(v9.v6.RELEVANT_CLASSES)
PROVINCE_ORDER = tuple(v9.a2.PROVINCES)
BAND_ORDER = ("0-50", "50-100", "100-150", "150-300", ">300")


class TransportCapacityGapError(RuntimeError):
    """Raised when a transport-capacity-gap invariant fails."""


@dataclass(frozen=True, slots=True)
class DistanceBand:
    label: str
    low_km: float
    high_km: float | None

    def contains(self, distance_km: float) -> bool:
        if distance_km < self.low_km:
            return False
        if self.high_km is None:
            return True
        return distance_km < self.high_km


BANDS = (
    DistanceBand("0-50", 0.0, 50.0),
    DistanceBand("50-100", 50.0, 100.0),
    DistanceBand("100-150", 100.0, 150.0),
    DistanceBand("150-300", 150.0, 300.0),
    DistanceBand(">300", 300.0, None),
)
BANDS_BY_LABEL = {band.label: band for band in BANDS}


@dataclass(frozen=True, slots=True)
class ScenarioSource:
    name: str
    assignment_path: Path
    assignment_scenario: str


SCENARIO_SOURCES = (
    ScenarioSource("v8", V8_ASSIGNMENT_CSV, PRIMARY_SCENARIO),
    ScenarioSource("v9", V9_ASSIGNMENT_CSV, PRIMARY_SCENARIO),
)
SCENARIOS = tuple(source.name for source in SCENARIO_SOURCES)


@dataclass(frozen=True, slots=True)
class AssignmentRow:
    scenario: str
    province: str
    facility_class: str
    facility_province: str
    facility_id: str
    assigned_m3: float
    road_distance_km: float
    capacity_status: str
    cross_border: bool


@dataclass(frozen=True, slots=True)
class CapacityBands:
    local_capacity: Mapping[tuple[str, str, str], float]
    all_province_capacity: Mapping[tuple[str, str, str], float]
    local_facility_count: Mapping[tuple[str, str, str], int]
    all_province_facility_count: Mapping[tuple[str, str, str], int]
    local_published_capacity: Mapping[tuple[str, str, str], float]
    local_proxy_capacity: Mapping[tuple[str, str, str], float]


@dataclass(frozen=True, slots=True)
class CapacityPool:
    facilities: Sequence[Any]
    offers: Sequence[Any]
    factors: Mapping[str, float]
    bands: CapacityBands
    total_capacity_m3_y: float
    capacity_by_class: Mapping[str, float]
    capacity_by_province: Mapping[str, float]


# ---------------------------------------------------------------------------
# Boundary parsing and fail-fast guards.
# ---------------------------------------------------------------------------

def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _read_csv(path: Path, role: str) -> list[dict[str, str]]:
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
    except FileNotFoundError as error:
        raise TransportCapacityGapError(f"{role} is missing: {path}") from error
    except OSError as error:
        raise TransportCapacityGapError(f"cannot read {role} {path}: {error}") from error
    if not rows:
        raise TransportCapacityGapError(f"{role} has no data rows: {path}")
    return rows


def _require_columns(
    rows: Sequence[dict[str, str]], required: set[str], path: Path
) -> None:
    missing = required - set(rows[0])
    if missing:
        raise TransportCapacityGapError(
            f"{path}: missing required columns {sorted(missing)}"
        )


def _finite_float(value: str | None, *, field: str, context: str) -> float:
    if value is None or not value.strip():
        raise TransportCapacityGapError(f"{context}: missing {field}")
    try:
        number = float(value)
    except ValueError as error:
        raise TransportCapacityGapError(
            f"{context}: {field} is not numeric: {value!r}"
        ) from error
    if not math.isfinite(number):
        raise TransportCapacityGapError(
            f"{context}: {field} is not finite: {value!r}"
        )
    return number


def _nonnegative_float(
    value: str | None, *, field: str, context: str
) -> float:
    number = _finite_float(value, field=field, context=context)
    if number < 0.0:
        raise TransportCapacityGapError(
            f"{context}: {field} is negative: {number!r}"
        )
    return number


def _format_float(value: float, digits: int = 9) -> str:
    if not math.isfinite(value):
        raise TransportCapacityGapError(f"cannot format non-finite value {value!r}")
    if abs(value) < 0.5 * 10**-digits:
        return "0"
    return f"{value:.{digits}f}".rstrip("0").rstrip(".")


def _bool_text(value: bool) -> str:
    return "true" if value else "false"


def parse_deletion_lines(status_text: str) -> list[str]:
    """Return porcelain lines with a ``D`` in either status column."""

    deletions: list[str] = []
    for line in status_text.splitlines():
        if len(line) < 2:
            continue
        if line[0] == "D" or line[1] == "D":
            deletions.append(line)
    return deletions


def assert_no_unexpected_deletions() -> None:
    """Fail closed on any ``D`` in the parent or data repository status."""

    incidents: list[str] = []
    for repository in (REPO_ROOT, DATA_ROOT):
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repository,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise TransportCapacityGapError(
                f"cannot read git status in {repository}: {result.stderr.strip()}"
            )
        incidents.extend(
            f"{repository.name or repository}: {line}"
            for line in parse_deletion_lines(result.stdout)
        )
    if incidents:
        raise TransportCapacityGapError(
            "unexpected deletion status detected; halting: " + " | ".join(incidents)
        )


def band_for_distance(distance_km: float) -> DistanceBand:
    if not math.isfinite(distance_km) or distance_km < 0.0:
        raise TransportCapacityGapError(
            f"road distance must be finite and non-negative; got {distance_km!r}"
        )
    for band in BANDS:
        if band.contains(distance_km):
            return band
    raise TransportCapacityGapError(f"distance outside defined bands: {distance_km!r}")


# ---------------------------------------------------------------------------
# Input parsing.
# ---------------------------------------------------------------------------

def read_assignment_rows(source: ScenarioSource) -> tuple[AssignmentRow, ...]:
    rows = _read_csv(source.assignment_path, f"{source.name} assignment")
    _require_columns(
        rows,
        {
            "scenario",
            "province",
            "facility_id",
            "facility_class",
            "facility_province",
            "cross_border",
            "capacity_status",
            "assigned_m3",
            "road_distance_km",
        },
        source.assignment_path,
    )
    parsed: list[AssignmentRow] = []
    for position, row in enumerate(rows, start=2):
        context = f"{source.assignment_path}:{position}"
        if row["scenario"].strip() != source.assignment_scenario:
            continue
        province = row["province"].strip().upper()
        if province not in PROVINCE_ORDER:
            raise TransportCapacityGapError(
                f"{context}: unexpected origin province {province!r}"
            )
        facility_province = row["facility_province"].strip().upper()
        if facility_province not in PROVINCE_ORDER:
            raise TransportCapacityGapError(
                f"{context}: unexpected facility province {facility_province!r}"
            )
        facility_class = row["facility_class"].strip().upper()
        if facility_class not in CLASS_ORDER:
            raise TransportCapacityGapError(
                f"{context}: unexpected facility class {facility_class!r}"
            )
        assigned = _nonnegative_float(
            row["assigned_m3"], field="assigned_m3", context=context
        )
        if assigned <= 0.0:
            continue
        cross_border_text = row["cross_border"].strip().lower()
        if cross_border_text not in {"true", "false"}:
            raise TransportCapacityGapError(
                f"{context}: cross_border must be true or false; "
                f"got {cross_border_text!r}"
            )
        parsed.append(
            AssignmentRow(
                scenario=source.name,
                province=province,
                facility_class=facility_class,
                facility_province=facility_province,
                facility_id=row["facility_id"].strip(),
                assigned_m3=assigned,
                road_distance_km=_nonnegative_float(
                    row["road_distance_km"],
                    field="road_distance_km",
                    context=context,
                ),
                capacity_status=row["capacity_status"].strip().lower(),
                cross_border=cross_border_text == "true",
            )
        )
    if not parsed:
        raise TransportCapacityGapError(
            f"{source.assignment_path}: no rows for scenario "
            f"{source.assignment_scenario!r}"
        )
    return tuple(parsed)


def read_offer_rows() -> tuple[Any, ...]:
    offers, _ = v9.a2.read_offers(HARVEST_CSV, BOUNDARY_GEOJSON)
    active = tuple(offer for offer in offers if offer.volume_m3 > 0.0)
    if not active:
        raise TransportCapacityGapError("no active provincial harvest offers")
    return active


def load_capacity_pool(offers: Sequence[Any]) -> CapacityPool:
    facilities, eligible_offers, factors = v9._eligible_facilities()
    if not facilities:
        raise TransportCapacityGapError("no eligible facilities returned by v9")
    offer_provinces = {offer.province for offer in offers}
    eligible_provinces = {offer.province for offer in eligible_offers}
    if offer_provinces != eligible_provinces:
        raise TransportCapacityGapError(
            "active offers do not match v9 eligible offer provinces"
        )
    if len({facility.facility_id for facility in facilities}) != len(facilities):
        raise TransportCapacityGapError("eligible facilities contain duplicate ids")

    local_capacity: dict[tuple[str, str, str], float] = defaultdict(float)
    all_capacity: dict[tuple[str, str, str], float] = defaultdict(float)
    local_count: dict[tuple[str, str, str], int] = defaultdict(int)
    all_count: dict[tuple[str, str, str], int] = defaultdict(int)
    local_published: dict[tuple[str, str, str], float] = defaultdict(float)
    local_proxy: dict[tuple[str, str, str], float] = defaultdict(float)
    capacity_by_class: dict[str, float] = defaultdict(float)
    capacity_by_province: dict[str, float] = defaultdict(float)

    for offer in offers:
        for facility in facilities:
            if facility.location is None or facility.capacity is None:
                raise TransportCapacityGapError(
                    f"{facility.facility_id}: eligible facility lacks location or "
                    "capacity"
                )
            capacity = _nonnegative_float(
                str(facility.capacity.capacity_m3_y),
                field="capacity_m3_y",
                context=facility.facility_id,
            )
            if facility.province not in factors:
                raise TransportCapacityGapError(
                    f"{facility.province}: no province road factor"
                )
            distance = (
                haversine_km(
                    offer.location.lat,
                    offer.location.lon,
                    facility.location.lat,
                    facility.location.lon,
                )
                * factors[facility.province]
            )
            band = band_for_distance(distance)
            key = (offer.province, facility.facility_class, band.label)
            all_capacity[key] += capacity
            all_count[key] += 1
            if facility.province != offer.province:
                continue
            local_capacity[key] += capacity
            local_count[key] += 1
            if facility.capacity.status == "published":
                local_published[key] += capacity
            else:
                local_proxy[key] += capacity

    for facility in facilities:
        if facility.capacity is None:
            continue
        capacity = float(facility.capacity.capacity_m3_y)
        capacity_by_class[facility.facility_class] += capacity
        capacity_by_province[facility.province] += capacity

    bands = CapacityBands(
        local_capacity=dict(local_capacity),
        all_province_capacity=dict(all_capacity),
        local_facility_count=dict(local_count),
        all_province_facility_count=dict(all_count),
        local_published_capacity=dict(local_published),
        local_proxy_capacity=dict(local_proxy),
    )
    return CapacityPool(
        facilities=tuple(facilities),
        offers=tuple(offers),
        factors=dict(factors),
        bands=bands,
        total_capacity_m3_y=math.fsum(
            float(facility.capacity.capacity_m3_y)
            for facility in facilities
            if facility.capacity is not None
        ),
        capacity_by_class=dict(capacity_by_class),
        capacity_by_province=dict(capacity_by_province),
    )


# ---------------------------------------------------------------------------
# Band and gap tables.
# ---------------------------------------------------------------------------

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
        raise TransportCapacityGapError(
            f"invalid transport cost {cost!r} at {distance_km!r} km"
        )
    cache[key] = cost
    return cost


def _is_proxy_status(capacity_status: str) -> bool:
    return capacity_status != "published"


def build_band_rows(
    assignments: Mapping[str, Sequence[AssignmentRow]],
    capacity_bands: CapacityBands,
    cost_by_distance: Mapping[float, float],
    offer_total_m3: float,
) -> list[dict[str, str]]:
    assigned_m3: dict[tuple[str, str, str, str], float] = defaultdict(float)
    assigned_cost: dict[tuple[str, str, str, str], float] = defaultdict(float)
    assigned_volume_distance: dict[tuple[str, str, str, str], float] = defaultdict(float)
    assigned_published: dict[tuple[str, str, str, str], float] = defaultdict(float)
    assigned_proxy: dict[tuple[str, str, str, str], float] = defaultdict(float)
    cross_border_assigned: dict[tuple[str, str, str, str], float] = defaultdict(float)
    row_count: dict[tuple[str, str, str, str], int] = defaultdict(int)
    facilities: dict[tuple[str, str, str, str], set[str]] = defaultdict(set)

    for scenario, scenario_assignments in assignments.items():
        for row in scenario_assignments:
            band = band_for_distance(row.road_distance_km)
            key = (scenario, row.province, row.facility_class, band.label)
            assigned_m3[key] += row.assigned_m3
            assigned_cost[key] += row.assigned_m3 * cost_by_distance[
                row.road_distance_km
            ]
            assigned_volume_distance[key] += (
                row.assigned_m3 * row.road_distance_km
            )
            if _is_proxy_status(row.capacity_status):
                assigned_proxy[key] += row.assigned_m3
            else:
                assigned_published[key] += row.assigned_m3
            if row.cross_border:
                cross_border_assigned[key] += row.assigned_m3
            row_count[key] += 1
            facilities[key].add(row.facility_id)

    # A cell enters the grid when it has assigned volume or any eligible local
    # capacity.  Both scenarios share the same capacity layer.
    cell_keys = {
        (scenario, province, facility_class)
        for scenario in assignments
        for province, facility_class, _ in capacity_bands.local_capacity
    }
    cell_keys.update(
        (scenario, province, facility_class)
        for scenario, province, facility_class, _ in assigned_m3
    )

    assigned_by_cell: dict[tuple[str, str, str], float] = defaultdict(float)
    for (scenario, province, facility_class, _), volume in assigned_m3.items():
        assigned_by_cell[(scenario, province, facility_class)] += volume

    band_rows_out: list[dict[str, str]] = []
    for scenario in SCENARIOS:
        scenario_total = math.fsum(
            row.assigned_m3 for row in assignments[scenario]
        )
        if abs(scenario_total - offer_total_m3) > VOLUME_TOLERANCE_M3:
            raise TransportCapacityGapError(
                f"{scenario}: assigned volume {scenario_total!r} does not match "
                f"offer total {offer_total_m3!r}"
            )
        for province in PROVINCE_ORDER:
            for facility_class in CLASS_ORDER:
                cell = (scenario, province, facility_class)
                if cell not in cell_keys:
                    continue
                cell_assigned = assigned_by_cell.get(cell, 0.0)
                for band in BANDS:
                    key = (scenario, province, facility_class, band.label)
                    volume = assigned_m3.get(key, 0.0)
                    capacity = capacity_bands.local_capacity.get(
                        (province, facility_class, band.label), 0.0
                    )
                    all_capacity = capacity_bands.all_province_capacity.get(
                        (province, facility_class, band.label), 0.0
                    )
                    headroom = capacity - volume
                    utilization = volume / capacity if capacity > 0.0 else 0.0
                    volume_distance = assigned_volume_distance.get(key, 0.0)
                    mean_distance = (
                        volume_distance / volume if volume > 0.0 else 0.0
                    )
                    cost = assigned_cost.get(key, 0.0)
                    band_rows_out.append(
                        {
                            "scenario": scenario,
                            "province": province,
                            "facility_class": facility_class,
                            "distance_band": band.label,
                            "band_low_km": _format_float(band.low_km),
                            "band_high_km": (
                                _format_float(band.high_km)
                                if band.high_km is not None
                                else ""
                            ),
                            "assigned_m3": _format_float(volume),
                            "assigned_share_of_province_class": _format_float(
                                volume / cell_assigned if cell_assigned > 0.0 else 0.0
                            ),
                            "assigned_share_of_scenario": _format_float(
                                volume / scenario_total if scenario_total > 0.0 else 0.0
                            ),
                            "assigned_published_m3": _format_float(
                                assigned_published.get(key, 0.0)
                            ),
                            "assigned_proxy_m3": _format_float(
                                assigned_proxy.get(key, 0.0)
                            ),
                            "cross_border_assigned_m3": _format_float(
                                cross_border_assigned.get(key, 0.0)
                            ),
                            "row_count": str(row_count.get(key, 0)),
                            "facility_count_used": str(len(facilities.get(key, set()))),
                            "eligible_local_capacity_m3_y": _format_float(capacity),
                            "eligible_local_published_capacity_m3_y": _format_float(
                                capacity_bands.local_published_capacity.get(
                                    (province, facility_class, band.label), 0.0
                                )
                            ),
                            "eligible_local_proxy_capacity_m3_y": _format_float(
                                capacity_bands.local_proxy_capacity.get(
                                    (province, facility_class, band.label), 0.0
                                )
                            ),
                            "eligible_all_provinces_capacity_m3_y": _format_float(
                                all_capacity
                            ),
                            "local_capacity_headroom_m3_y": _format_float(headroom),
                            "local_capacity_deficit_m3_y": _format_float(
                                max(0.0, -headroom)
                            ),
                            "unused_local_capacity_m3_y": _format_float(
                                max(0.0, headroom)
                            ),
                            "local_capacity_utilization": _format_float(
                                utilization
                            ),
                            "mean_road_distance_km": _format_float(mean_distance),
                            "transport_cost_cad": _format_float(cost),
                            "transport_cost_cad_m3": _format_float(
                                cost / volume if volume > 0.0 else 0.0
                            ),
                            "transport_cost_cad_gj": _format_float(
                                cost / (volume * ENERGY_GJ_PER_M3)
                                if volume > 0.0
                                else 0.0
                            ),
                            "volume_distance_m3_km": _format_float(volume_distance),
                            "evidence_label": EVIDENCE_LABEL,
                            "observed": _bool_text(OBSERVED),
                            "policy_grade": _bool_text(POLICY_GRADE),
                        }
                    )
    return band_rows_out


def _impact_by_cell(
    assignments: Mapping[str, Sequence[AssignmentRow]],
    cost_by_distance: Mapping[float, float],
) -> dict[tuple[str, str, str], dict[str, float]]:
    impacts: dict[tuple[str, str, str], dict[str, float]] = {}
    for scenario, rows in assignments.items():
        for row in rows:
            if row.road_distance_km <= 150.0:
                continue
            key = (scenario, row.province, row.facility_class)
            impact = impacts.setdefault(
                key,
                {
                    "gt150_m3": 0.0,
                    "gt300_m3": 0.0,
                    "gt150_volume_distance": 0.0,
                    "gt300_volume_distance": 0.0,
                    "gt150_cost_delta": 0.0,
                    "gt300_cost_delta": 0.0,
                },
            )
            excess_distance = row.road_distance_km - LOCALIZATION_REFERENCE_KM
            cost_delta = (
                cost_by_distance[row.road_distance_km]
                - cost_by_distance[LOCALIZATION_REFERENCE_KM]
            )
            impact["gt150_m3"] += row.assigned_m3
            impact["gt150_volume_distance"] += row.assigned_m3 * excess_distance
            impact["gt150_cost_delta"] += row.assigned_m3 * cost_delta
            if row.road_distance_km <= 300.0:
                continue
            impact["gt300_m3"] += row.assigned_m3
            impact["gt300_volume_distance"] += row.assigned_m3 * excess_distance
            impact["gt300_cost_delta"] += row.assigned_m3 * cost_delta
    return impacts


def build_capacity_gap_rows(
    band_rows: Sequence[dict[str, str]],
    impacts: Mapping[tuple[str, str, str], Mapping[str, float]],
) -> list[dict[str, str]]:
    grouped: dict[tuple[str, str, str], list[dict[str, str]]] = defaultdict(list)
    for row in band_rows:
        grouped[
            (row["scenario"], row["province"], row["facility_class"])
        ].append(row)

    scenario_assignments: dict[str, float] = defaultdict(float)
    for (scenario, _, _), rows in grouped.items():
        scenario_assignments[scenario] += math.fsum(
            float(row["assigned_m3"]) for row in rows
        )

    output: list[dict[str, str]] = []
    for scenario in SCENARIOS:
        for province in PROVINCE_ORDER:
            for facility_class in CLASS_ORDER:
                key = (scenario, province, facility_class)
                cell_rows = grouped.get(key)
                if cell_rows is None:
                    continue
                by_band = {row["distance_band"]: row for row in cell_rows}
                assigned = math.fsum(
                    float(row["assigned_m3"]) for row in cell_rows
                )
                local_capacity = math.fsum(
                    float(row["eligible_local_capacity_m3_y"])
                    for row in cell_rows
                )
                all_capacity = math.fsum(
                    float(row["eligible_all_provinces_capacity_m3_y"])
                    for row in cell_rows
                )
                published_assigned = math.fsum(
                    float(row["assigned_published_m3"]) for row in cell_rows
                )
                proxy_assigned = math.fsum(
                    float(row["assigned_proxy_m3"]) for row in cell_rows
                )
                assigned_50 = float(by_band["0-50"]["assigned_m3"])
                assigned_150 = math.fsum(
                    float(by_band[label]["assigned_m3"])
                    for label in ("0-50", "50-100", "100-150")
                )
                assigned_300 = assigned_150 + float(
                    by_band["150-300"]["assigned_m3"]
                )
                capacity_50 = float(
                    by_band["0-50"]["eligible_local_capacity_m3_y"]
                )
                capacity_150 = math.fsum(
                    float(by_band[label]["eligible_local_capacity_m3_y"])
                    for label in ("0-50", "50-100", "100-150")
                )
                capacity_300 = capacity_150 + float(
                    by_band["150-300"]["eligible_local_capacity_m3_y"]
                )
                all_capacity_150 = math.fsum(
                    float(by_band[label]["eligible_all_provinces_capacity_m3_y"])
                    for label in ("0-50", "50-100", "100-150")
                )
                all_capacity_300 = all_capacity_150 + float(
                    by_band["150-300"]["eligible_all_provinces_capacity_m3_y"]
                )
                volume_distance = math.fsum(
                    float(row["volume_distance_m3_km"]) for row in cell_rows
                )
                transport_cost = math.fsum(
                    float(row["transport_cost_cad"]) for row in cell_rows
                )
                row_count = sum(int(row["row_count"]) for row in cell_rows)
                impact = impacts.get(key, {})
                gt150 = assigned - assigned_150
                gt300 = assigned - assigned_300
                gap_150 = max(0.0, assigned - capacity_150)
                gap_300 = max(0.0, assigned - capacity_300)
                gt150_volume_distance = float(
                    impact.get("gt150_volume_distance", 0.0)
                )
                gt300_volume_distance = float(
                    impact.get("gt300_volume_distance", 0.0)
                )
                gt150_cost_delta = float(impact.get("gt150_cost_delta", 0.0))
                gt300_cost_delta = float(impact.get("gt300_cost_delta", 0.0))
                gt150_mean = (
                    gt150_volume_distance / gt150 + LOCALIZATION_REFERENCE_KM
                    if gt150 > 0.0
                    else 0.0
                )
                gt300_mean = (
                    gt300_volume_distance / gt300 + LOCALIZATION_REFERENCE_KM
                    if gt300 > 0.0
                    else 0.0
                )
                output.append(
                    {
                        "scenario": scenario,
                        "province": province,
                        "facility_class": facility_class,
                        "assigned_m3": _format_float(assigned),
                        "assigned_share_of_scenario": _format_float(
                            assigned
                            / scenario_assignments[scenario]
                            if scenario_assignments[scenario] > 0.0
                            else 0.0
                        ),
                        "assigned_published_m3": _format_float(published_assigned),
                        "assigned_proxy_m3": _format_float(proxy_assigned),
                        "proxy_share_of_assigned": _format_float(
                            proxy_assigned / assigned if assigned > 0.0 else 0.0
                        ),
                        "assigned_within_50_m3_y": _format_float(assigned_50),
                        "assigned_within_150_m3_y": _format_float(assigned_150),
                        "assigned_within_300_m3_y": _format_float(assigned_300),
                        "assigned_gt150_m3_y": _format_float(gt150),
                        "assigned_gt300_m3_y": _format_float(gt300),
                        "assigned_gt150_share": _format_float(
                            gt150 / assigned if assigned > 0.0 else 0.0
                        ),
                        "assigned_gt300_share": _format_float(
                            gt300 / assigned if assigned > 0.0 else 0.0
                        ),
                        "eligible_local_capacity_m3_y": _format_float(
                            local_capacity
                        ),
                        "eligible_local_capacity_within_50_m3_y": _format_float(
                            capacity_50
                        ),
                        "eligible_local_capacity_within_150_m3_y": _format_float(
                            capacity_150
                        ),
                        "eligible_local_capacity_within_300_m3_y": _format_float(
                            capacity_300
                        ),
                        "eligible_all_provinces_capacity_within_150_m3_y": (
                            _format_float(all_capacity_150)
                        ),
                        "eligible_all_provinces_capacity_within_300_m3_y": (
                            _format_float(all_capacity_300)
                        ),
                        "eligible_all_provinces_capacity_m3_y": _format_float(
                            all_capacity
                        ),
                        "local_access_gap_within_150_m3_y": _format_float(gap_150),
                        "local_access_gap_within_300_m3_y": _format_float(gap_300),
                        "local_capacity_headroom_total_m3_y": _format_float(
                            local_capacity - assigned
                        ),
                        "local_capacity_deficit_total_m3_y": _format_float(
                            max(0.0, assigned - local_capacity)
                        ),
                        "mean_road_distance_km": _format_float(
                            volume_distance / assigned if assigned > 0.0 else 0.0
                        ),
                        "gt150_mean_road_distance_km": _format_float(gt150_mean),
                        "gt300_mean_road_distance_km": _format_float(gt300_mean),
                        "transport_cost_cad": _format_float(transport_cost),
                        "transport_cost_cad_m3": _format_float(
                            transport_cost / assigned if assigned > 0.0 else 0.0
                        ),
                        "transport_cost_cad_gj": _format_float(
                            transport_cost / (assigned * ENERGY_GJ_PER_M3)
                            if assigned > 0.0
                            else 0.0
                        ),
                        "row_count": str(row_count),
                        "avoidable_distance_gt150_m3_km": _format_float(
                            gt150_volume_distance
                        ),
                        "avoidable_distance_gt300_m3_km": _format_float(
                            gt300_volume_distance
                        ),
                        "avoidable_cost_gt150_cad": _format_float(gt150_cost_delta),
                        "avoidable_cost_gt300_cad": _format_float(gt300_cost_delta),
                        "avoidable_cost_gt150_cad_gj": _format_float(
                            gt150_cost_delta / (gt150 * ENERGY_GJ_PER_M3)
                            if gt150 > 0.0
                            else 0.0
                        ),
                        "localization_reference_km": _format_float(
                            LOCALIZATION_REFERENCE_KM
                        ),
                        "evidence_label": EVIDENCE_LABEL,
                        "observed": _bool_text(OBSERVED),
                        "policy_grade": _bool_text(POLICY_GRADE),
                    }
                )
    return output


def build_province_rows(
    gap_rows: Sequence[dict[str, str]],
) -> list[dict[str, str]]:
    grouped: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in gap_rows:
        grouped[(row["scenario"], row["province"])].append(row)

    scenario_assigned: dict[str, float] = defaultdict(float)
    scenario_avoidable: dict[str, float] = defaultdict(float)
    for (scenario, _), rows in grouped.items():
        scenario_assigned[scenario] += math.fsum(
            float(row["assigned_m3"]) for row in rows
        )
        scenario_avoidable[scenario] += math.fsum(
            float(row["avoidable_cost_gt150_cad"]) for row in rows
        )

    output: list[dict[str, str]] = []
    for scenario in SCENARIOS:
        for province in PROVINCE_ORDER:
            key = (scenario, province)
            cell_rows = grouped.get(key)
            if cell_rows is None:
                continue
            assigned = math.fsum(
                float(row["assigned_m3"]) for row in cell_rows
            )
            gt150 = math.fsum(
                float(row["assigned_gt150_m3_y"]) for row in cell_rows
            )
            gt300 = math.fsum(
                float(row["assigned_gt300_m3_y"]) for row in cell_rows
            )
            within150 = assigned - gt150
            within300 = assigned - gt300
            avoidable_distance = math.fsum(
                float(row["avoidable_distance_gt150_m3_km"])
                for row in cell_rows
            )
            avoidable_cost = math.fsum(
                float(row["avoidable_cost_gt150_cad"]) for row in cell_rows
            )
            local_capacity = math.fsum(
                float(row["eligible_local_capacity_m3_y"])
                for row in cell_rows
            )
            capacity_150 = math.fsum(
                float(row["eligible_local_capacity_within_150_m3_y"])
                for row in cell_rows
            )
            capacity_300 = math.fsum(
                float(row["eligible_local_capacity_within_300_m3_y"])
                for row in cell_rows
            )
            all_capacity_150 = math.fsum(
                float(row["eligible_all_provinces_capacity_within_150_m3_y"])
                for row in cell_rows
            )
            all_capacity_300 = math.fsum(
                float(row["eligible_all_provinces_capacity_within_300_m3_y"])
                for row in cell_rows
            )
            transport_cost = math.fsum(
                float(row["transport_cost_cad"]) for row in cell_rows
            )
            volume_distance = math.fsum(
                float(row["mean_road_distance_km"])
                * float(row["assigned_m3"])
                for row in cell_rows
            )
            stranded_rows = [
                row
                for row in cell_rows
                if float(row["assigned_gt300_m3_y"]) > 0.0
            ]
            top_stranded = max(
                stranded_rows,
                key=lambda row: float(row["assigned_gt300_m3_y"]),
                default=None,
            )
            top_class = top_stranded["facility_class"] if top_stranded else ""
            top_class_m3 = (
                float(top_stranded["assigned_gt300_m3_y"]) if top_stranded else 0.0
            )
            gt300_share = gt300 / assigned if assigned > 0.0 else 0.0
            gt150_share = gt150 / assigned if assigned > 0.0 else 0.0
            output.append(
                {
                    "scenario": scenario,
                    "province": province,
                    "assigned_m3": _format_float(assigned),
                    "assigned_share_of_scenario": _format_float(
                        assigned
                        / scenario_assigned[scenario]
                        if scenario_assigned[scenario] > 0.0
                        else 0.0
                    ),
                    "assigned_within_150_m3_y": _format_float(within150),
                    "assigned_gt150_m3_y": _format_float(gt150),
                    "assigned_within_300_m3_y": _format_float(within300),
                    "assigned_gt300_m3_y": _format_float(gt300),
                    "assigned_gt150_share": _format_float(gt150_share),
                    "assigned_gt300_share": _format_float(gt300_share),
                    "eligible_local_capacity_m3_y": _format_float(local_capacity),
                    "eligible_local_capacity_within_150_m3_y": _format_float(
                        capacity_150
                    ),
                    "eligible_local_capacity_within_300_m3_y": _format_float(
                        capacity_300
                    ),
                    "eligible_all_provinces_capacity_within_150_m3_y": (
                        _format_float(all_capacity_150)
                    ),
                    "eligible_all_provinces_capacity_within_300_m3_y": (
                        _format_float(all_capacity_300)
                    ),
                    "local_access_gap_within_150_m3_y": _format_float(
                        max(0.0, assigned - capacity_150)
                    ),
                    "local_access_gap_within_300_m3_y": _format_float(
                        max(0.0, assigned - capacity_300)
                    ),
                    "local_capacity_headroom_total_m3_y": _format_float(
                        local_capacity - assigned
                    ),
                    "local_capacity_deficit_total_m3_y": _format_float(
                        max(0.0, assigned - local_capacity)
                    ),
                    "classes_with_assignment": str(
                        sum(
                            1
                            for row in cell_rows
                            if float(row["assigned_m3"]) > 0.0
                        )
                    ),
                    "stranded_classes": str(len(stranded_rows)),
                    "top_stranded_class": top_class,
                    "top_stranded_class_m3_y": _format_float(top_class_m3),
                    "top_stranded_class_share_of_stranded": _format_float(
                        top_class_m3 / gt300 if gt300 > 0.0 else 0.0
                    ),
                    "mean_road_distance_km": _format_float(
                        volume_distance / assigned if assigned > 0.0 else 0.0
                    ),
                    "transport_cost_cad": _format_float(transport_cost),
                    "transport_cost_cad_m3": _format_float(
                        transport_cost / assigned if assigned > 0.0 else 0.0
                    ),
                    "transport_cost_cad_gj": _format_float(
                        transport_cost / (assigned * ENERGY_GJ_PER_M3)
                        if assigned > 0.0
                        else 0.0
                    ),
                    "avoidable_distance_gt150_m3_km": _format_float(
                        avoidable_distance
                    ),
                    "avoidable_cost_gt150_cad": _format_float(avoidable_cost),
                    "avoidable_cost_gt150_share_of_scenario": _format_float(
                        avoidable_cost / scenario_avoidable[scenario]
                        if scenario_avoidable[scenario] > 0.0
                        else 0.0
                    ),
                    "high_gt150_exposure": _bool_text(
                        assigned >= HIGH_EXPOSURE_MIN_M3
                        and gt150_share >= HIGH_EXPOSURE_SHARE
                    ),
                    "high_gt300_exposure": _bool_text(
                        assigned >= HIGH_EXPOSURE_MIN_M3
                        and gt300_share >= HIGH_EXPOSURE_SHARE
                    ),
                    "evidence_label": EVIDENCE_LABEL,
                    "observed": _bool_text(OBSERVED),
                    "policy_grade": _bool_text(POLICY_GRADE),
                }
            )
    return output


def build_priority_rows(
    gap_rows: Sequence[dict[str, str]],
) -> list[dict[str, str]]:
    output: list[dict[str, str]] = []
    for scenario in SCENARIOS:
        candidates = [
            row
            for row in gap_rows
            if row["scenario"] == scenario
            and float(row["assigned_gt150_m3_y"]) > 0.0
            and float(row["avoidable_cost_gt150_cad"]) > 0.0
        ]
        scenario_avoidable = math.fsum(
            float(row["avoidable_cost_gt150_cad"]) for row in candidates
        )
        cost_sorted = sorted(
            candidates,
            key=lambda row: (
                -float(row["avoidable_cost_gt150_cad"]),
                -float(row["assigned_gt300_m3_y"]),
                row["province"],
                row["facility_class"],
            ),
        )
        distance_sorted = sorted(
            candidates,
            key=lambda row: (
                -float(row["avoidable_distance_gt150_m3_km"]),
                row["province"],
                row["facility_class"],
            ),
        )
        distance_rank = {
            (row["province"], row["facility_class"]): rank
            for rank, row in enumerate(distance_sorted, start=1)
        }
        for rank, row in enumerate(cost_sorted, start=1):
            avoidable_cost = float(row["avoidable_cost_gt150_cad"])
            output.append(
                {
                    "scenario": scenario,
                    "cost_rank": str(rank),
                    "distance_rank": str(
                        distance_rank[(row["province"], row["facility_class"])]
                    ),
                    "province": row["province"],
                    "facility_class": row["facility_class"],
                    "assigned_m3": row["assigned_m3"],
                    "assigned_gt150_m3_y": row["assigned_gt150_m3_y"],
                    "assigned_gt300_m3_y": row["assigned_gt300_m3_y"],
                    "assigned_gt150_share": row["assigned_gt150_share"],
                    "assigned_gt300_share": row["assigned_gt300_share"],
                    "local_access_gap_within_150_m3_y": row[
                        "local_access_gap_within_150_m3_y"
                    ],
                    "mean_road_distance_km": row["mean_road_distance_km"],
                    "gt150_mean_road_distance_km": row[
                        "gt150_mean_road_distance_km"
                    ],
                    "gt300_mean_road_distance_km": row[
                        "gt300_mean_road_distance_km"
                    ],
                    "avoidable_distance_gt150_m3_km": row[
                        "avoidable_distance_gt150_m3_km"
                    ],
                    "avoidable_cost_gt150_cad": row["avoidable_cost_gt150_cad"],
                    "avoidable_cost_gt150_cad_gj": row[
                        "avoidable_cost_gt150_cad_gj"
                    ],
                    "avoidable_distance_gt300_m3_km": row[
                        "avoidable_distance_gt300_m3_km"
                    ],
                    "avoidable_cost_gt300_cad": row["avoidable_cost_gt300_cad"],
                    "share_of_scenario_avoidable_cost_gt150": _format_float(
                        avoidable_cost / scenario_avoidable
                        if scenario_avoidable > 0.0
                        else 0.0
                    ),
                    "localization_reference_km": row["localization_reference_km"],
                    "evidence_label": EVIDENCE_LABEL,
                    "observed": _bool_text(OBSERVED),
                    "policy_grade": _bool_text(POLICY_GRADE),
                }
            )
    return output


# ---------------------------------------------------------------------------
# Validation and summary.
# ---------------------------------------------------------------------------

def validate_outputs(
    *,
    assignments: Mapping[str, Sequence[AssignmentRow]],
    band_rows: Sequence[Mapping[str, str]],
    gap_rows: Sequence[Mapping[str, str]],
    province_rows: Sequence[Mapping[str, str]],
    priority_rows: Sequence[Mapping[str, str]],
    capacity_pool: CapacityPool,
    offer_total_m3: float,
) -> dict[str, Any]:
    checks: dict[str, bool] = {}

    band_assigned: dict[str, float] = defaultdict(float)
    for row in band_rows:
        band_assigned[row["scenario"]] += float(row["assigned_m3"])
    checks["band_volume_conservation"] = all(
        abs(band_assigned[scenario] - offer_total_m3) <= VOLUME_TOLERANCE_M3
        for scenario in SCENARIOS
    )
    checks["source_assignment_totals"] = all(
        abs(
            math.fsum(row.assigned_m3 for row in assignments[scenario])
            - offer_total_m3
        )
        <= VOLUME_TOLERANCE_M3
        for scenario in SCENARIOS
    )

    band_by_cell: dict[tuple[str, str, str], float] = defaultdict(float)
    for row in band_rows:
        band_by_cell[
            (row["scenario"], row["province"], row["facility_class"])
        ] += float(row["assigned_m3"])
    gap_by_cell = {
        (row["scenario"], row["province"], row["facility_class"]): float(
            row["assigned_m3"]
        )
        for row in gap_rows
    }
    checks["band_cell_sums_match_capacity_gap"] = all(
        abs(band_by_cell[key] - gap_by_cell.get(key, math.inf))
        <= VOLUME_TOLERANCE_M3
        for key in band_by_cell
    )

    checks["band_shares_sum_to_one"] = all(
        abs(
            math.fsum(
                float(row["assigned_share_of_province_class"])
                for row in band_rows
                if (
                    row["scenario"],
                    row["province"],
                    row["facility_class"],
                )
                == key
            )
            - 1.0
        )
        <= 1e-6
        for key, assigned in gap_by_cell.items()
        if assigned > 0.0
    )

    checks["threshold_partitions"] = all(
        abs(
            float(row["assigned_within_150_m3_y"])
            + float(row["assigned_gt150_m3_y"])
            - float(row["assigned_m3"])
        )
        <= VOLUME_TOLERANCE_M3
        and abs(
            float(row["assigned_within_300_m3_y"])
            + float(row["assigned_gt300_m3_y"])
            - float(row["assigned_m3"])
        )
        <= VOLUME_TOLERANCE_M3
        for row in gap_rows
    )

    checks["capacity_partitions"] = all(
        float(row["eligible_local_capacity_within_50_m3_y"])
        <= float(row["eligible_local_capacity_within_150_m3_y"])
        + CAPACITY_TOLERANCE_M3
        and float(row["eligible_local_capacity_within_150_m3_y"])
        <= float(row["eligible_local_capacity_within_300_m3_y"])
        + CAPACITY_TOLERANCE_M3
        and float(row["eligible_local_capacity_within_300_m3_y"])
        <= float(row["eligible_local_capacity_m3_y"])
        + CAPACITY_TOLERANCE_M3
        for row in gap_rows
    )

    checks["band_capacity_comparison"] = all(
        float(row["assigned_m3"])
        <= float(row["eligible_local_capacity_m3_y"])
        + float(row["cross_border_assigned_m3"])
        + CAPACITY_TOLERANCE_M3
        for row in band_rows
    )
    checks["capacity_headroom_identity"] = all(
        abs(
            float(row["local_capacity_headroom_m3_y"])
            - (
                float(row["eligible_local_capacity_m3_y"])
                - float(row["assigned_m3"])
            )
        )
        <= CAPACITY_TOLERANCE_M3
        for row in band_rows
    )

    checks["province_partitions"] = all(
        abs(
            math.fsum(
                float(row["assigned_m3"])
                for row in province_rows
                if row["scenario"] == scenario
            )
            - offer_total_m3
        )
        <= VOLUME_TOLERANCE_M3
        for scenario in SCENARIOS
    )

    checks["priority_rank_order"] = all(
        int(rows[i]["cost_rank"]) < int(rows[i + 1]["cost_rank"])
        and float(rows[i]["avoidable_cost_gt150_cad"])
        >= float(rows[i + 1]["avoidable_cost_gt150_cad"]) - 1e-6
        for scenario in SCENARIOS
        for rows in [
            [
                row
                for row in priority_rows
                if row["scenario"] == scenario
            ]
        ]
        for i in range(len(rows) - 1)
    )

    numeric_fields = {
        "assigned_m3": False,
        "assigned_gt150_m3_y": False,
        "assigned_gt300_m3_y": False,
        "eligible_local_capacity_m3_y": False,
        "transport_cost_cad": False,
        "mean_road_distance_km": False,
        "avoidable_cost_gt150_cad": False,
        "avoidable_distance_gt150_m3_km": False,
    }
    numeric_rows: list[Mapping[str, str]] = [
        *band_rows,
        *gap_rows,
        *province_rows,
        *priority_rows,
    ]
    values_are_finite = True
    values_are_nonnegative = True
    for row in numeric_rows:
        for field in numeric_fields:
            if field not in row or row[field] == "":
                continue
            value = float(row[field])
            values_are_finite = values_are_finite and math.isfinite(value)
            values_are_nonnegative = values_are_nonnegative and value >= 0.0
    checks["no_nan_or_infinite"] = values_are_finite
    checks["no_negative_volumes_or_costs"] = values_are_nonnegative
    checks["capacity_total_matches_layer"] = (
        abs(
            math.fsum(
                float(row["eligible_local_capacity_m3_y"])
                for row in band_rows
                if row["scenario"] == SCENARIOS[0]
            )
            - capacity_pool.total_capacity_m3_y
        )
        <= CAPACITY_TOLERANCE_M3
    )

    labels = {
        "evidence_label": EVIDENCE_LABEL,
        "observed": OBSERVED,
        "policy_grade": POLICY_GRADE,
        "national_claim": NATIONAL_CLAIM,
        "official_osecan_changed": OFFICIAL_OSECAN_CHANGED,
        "ui_changed": UI_CHANGED,
        "siting_optimization": SITING_OPTIMIZATION,
    }
    checks["labels"] = (
        labels["evidence_label"] == "model_estimate"
        and labels["observed"] is False
        and labels["policy_grade"] is False
        and labels["national_claim"] is False
        and labels["official_osecan_changed"] is False
        and labels["ui_changed"] is False
        and labels["siting_optimization"] is False
    )

    all_passed = all(checks.values())
    if not all_passed:
        failed = [name for name, passed in checks.items() if not passed]
        raise TransportCapacityGapError(
            "transport-capacity-gap validation failed: " + ", ".join(failed)
        )
    return {"checks": checks, "all_passed": all_passed}


def _scenario_totals(
    scenario: str,
    band_rows: Sequence[Mapping[str, str]],
    gap_rows: Sequence[Mapping[str, str]],
    assignments: Sequence[AssignmentRow],
) -> dict[str, Any]:
    assigned = math.fsum(row.assigned_m3 for row in assignments)
    volume_distance = math.fsum(
        row.assigned_m3 * row.road_distance_km for row in assignments
    )
    transport_cost = math.fsum(
        float(row["transport_cost_cad"])
        for row in gap_rows
        if row["scenario"] == scenario
    )
    gt150 = math.fsum(
        float(row["assigned_gt150_m3_y"])
        for row in gap_rows
        if row["scenario"] == scenario
    )
    gt300 = math.fsum(
        float(row["assigned_gt300_m3_y"])
        for row in gap_rows
        if row["scenario"] == scenario
    )
    local_capacity = math.fsum(
        float(row["eligible_local_capacity_m3_y"])
        for row in gap_rows
        if row["scenario"] == scenario
    )
    capacity_150 = math.fsum(
        float(row["eligible_local_capacity_within_150_m3_y"])
        for row in gap_rows
        if row["scenario"] == scenario
    )
    capacity_300 = math.fsum(
        float(row["eligible_local_capacity_within_300_m3_y"])
        for row in gap_rows
        if row["scenario"] == scenario
    )
    avoidable_distance = math.fsum(
        float(row["avoidable_distance_gt150_m3_km"])
        for row in gap_rows
        if row["scenario"] == scenario
    )
    avoidable_cost = math.fsum(
        float(row["avoidable_cost_gt150_cad"])
        for row in gap_rows
        if row["scenario"] == scenario
    )
    proxy_assigned = math.fsum(
        float(row["assigned_proxy_m3"])
        for row in gap_rows
        if row["scenario"] == scenario
    )
    bands = []
    for band in BANDS:
        band_rows_for_band = [
            row
            for row in band_rows
            if row["scenario"] == scenario and row["distance_band"] == band.label
        ]
        band_assigned = math.fsum(
            float(row["assigned_m3"]) for row in band_rows_for_band
        )
        band_capacity = math.fsum(
            float(row["eligible_local_capacity_m3_y"])
            for row in band_rows_for_band
        )
        bands.append(
            {
                "distance_band": band.label,
                "assigned_m3": band_assigned,
                "assigned_share": band_assigned / assigned if assigned > 0.0 else 0.0,
                "eligible_local_capacity_m3_y": band_capacity,
                "local_capacity_headroom_m3_y": band_capacity - band_assigned,
                "local_capacity_utilization": (
                    band_assigned / band_capacity if band_capacity > 0.0 else 0.0
                ),
                "facility_count_used": sum(
                    int(row["facility_count_used"]) for row in band_rows_for_band
                ),
            }
        )
    return {
        "assigned_m3": assigned,
        "assigned_gt150_m3_y": gt150,
        "assigned_gt150_share": gt150 / assigned if assigned > 0.0 else 0.0,
        "assigned_gt300_m3_y": gt300,
        "assigned_gt300_share": gt300 / assigned if assigned > 0.0 else 0.0,
        "eligible_local_capacity_m3_y": local_capacity,
        "eligible_local_capacity_within_150_m3_y": capacity_150,
        "eligible_local_capacity_within_300_m3_y": capacity_300,
        "local_access_gap_within_150_m3_y": max(0.0, assigned - capacity_150),
        "local_access_gap_within_300_m3_y": max(0.0, assigned - capacity_300),
        "mean_road_distance_km": (
            volume_distance / assigned if assigned > 0.0 else 0.0
        ),
        "transport_cost_cad": transport_cost,
        "transport_cost_cad_m3": transport_cost / assigned if assigned > 0.0 else 0.0,
        "transport_cost_cad_gj": (
            transport_cost / (assigned * ENERGY_GJ_PER_M3)
            if assigned > 0.0
            else 0.0
        ),
        "proxy_share_of_assigned": proxy_assigned / assigned if assigned > 0.0 else 0.0,
        "avoidable_distance_gt150_m3_km": avoidable_distance,
        "avoidable_cost_gt150_cad": avoidable_cost,
        "bands": bands,
        "facilities_used": len({row.facility_id for row in assignments}),
    }


def build_summary(
    *,
    generated_utc: str,
    assignments: Mapping[str, Sequence[AssignmentRow]],
    band_rows: Sequence[Mapping[str, str]],
    gap_rows: Sequence[Mapping[str, str]],
    province_rows: Sequence[Mapping[str, str]],
    priority_rows: Sequence[Mapping[str, str]],
    capacity_pool: CapacityPool,
    validation: Mapping[str, Any],
    inputs: Mapping[str, str],
    offer_total_m3: float,
) -> dict[str, Any]:
    scenarios = {
        scenario: _scenario_totals(
            scenario, band_rows, gap_rows, assignments[scenario]
        )
        for scenario in SCENARIOS
    }
    high_exposure: dict[str, dict[str, list[dict[str, Any]]]] = {
        scenario: {
            "gt150": [
                {
                    "province": row["province"],
                    "assigned_m3": float(row["assigned_m3"]),
                    "assigned_gt150_share": float(row["assigned_gt150_share"]),
                    "assigned_gt300_share": float(row["assigned_gt300_share"]),
                    "local_access_gap_within_150_m3_y": float(
                        row["local_access_gap_within_150_m3_y"]
                    ),
                }
                for row in province_rows
                if row["scenario"] == scenario
                and row["high_gt150_exposure"] == "true"
            ],
            "gt300": [
                {
                    "province": row["province"],
                    "assigned_m3": float(row["assigned_m3"]),
                    "assigned_gt150_share": float(row["assigned_gt150_share"]),
                    "assigned_gt300_share": float(row["assigned_gt300_share"]),
                    "local_access_gap_within_300_m3_y": float(
                        row["local_access_gap_within_300_m3_y"]
                    ),
                }
                for row in province_rows
                if row["scenario"] == scenario
                and row["high_gt300_exposure"] == "true"
            ],
        }
        for scenario in SCENARIOS
    }
    high_exposure = {
        scenario: {
            name: sorted(
                rows,
                key=lambda row: (
                    -float(row["assigned_gt300_share"]),
                    -float(row["assigned_m3"]),
                ),
            )
            for name, rows in scenario_rows.items()
        }
        for scenario, scenario_rows in high_exposure.items()
    }
    top_greenfield = {
        scenario: [
            dict(row)
            for row in priority_rows
            if row["scenario"] == scenario
        ][:10]
        for scenario in SCENARIOS
    }
    capacity_by_class = {
        facility_class: capacity_pool.capacity_by_class.get(facility_class, 0.0)
        for facility_class in CLASS_ORDER
    }
    capacity_by_province = {
        province: capacity_pool.capacity_by_province.get(province, 0.0)
        for province in PROVINCE_ORDER
        if capacity_pool.capacity_by_province.get(province, 0.0) > 0.0
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
            "official_osecan_changed": OFFICIAL_OSECAN_CHANGED,
            "ui_changed": UI_CHANGED,
            "siting_optimization": SITING_OPTIMIZATION,
            "policy_claim": False,
            "assignment_change": False,
            "capacity_change": False,
        },
        "method": {
            "artifact_role": (
                "screening of assigned harvest volume by road-distance band "
                "and eligible local capacity; not a siting optimization"
            ),
            "assignment_inputs": {
                "v8": {
                    "path": str(V8_ASSIGNMENT_CSV.relative_to(REPO_ROOT)),
                    "scenario": PRIMARY_SCENARIO,
                    "rule": "published-first local-first bounded allocation",
                },
                "v9": {
                    "path": str(V9_ASSIGNMENT_CSV.relative_to(REPO_ROOT)),
                    "scenario": PRIMARY_SCENARIO,
                    "rule": "transport-cost minimization with 500 km local preference",
                },
            },
            "capacity_input": str(CAPACITY_CSV.relative_to(REPO_ROOT)),
            "capacity_basis": (
                "eligible operating located facilities in capacity v3 after the "
                "1.2x provincial harvest floor scaling used by v9"
            ),
            "harvest_input": str(HARVEST_CSV.relative_to(REPO_ROOT)),
            "harvest_total_m3": offer_total_m3,
            "distance_basis": (
                "haversine from the unweighted ADM1 polygon centroid of the "
                "origin province to the facility coordinate, multiplied by the "
                "facility-province road factor from facility_road_distance_audit_v2; "
                "this is a centroid-origin screening distance, not a harvest-node "
                "or observed route distance"
            ),
            "band_definition": {
                band.label: {
                    "low_km_inclusive": band.low_km,
                    "high_km_exclusive": band.high_km,
                }
                for band in BANDS
            },
            "gap_definitions": {
                "long_haul_gt150": "assigned volume with road_distance_km >= 150",
                "stranded_gt300": "assigned volume with road_distance_km >= 300",
                "local_access_gap_within_150": (
                    "max(0, assigned_m3 - eligible local capacity of the same "
                    "class within 150 km of the origin centroid)"
                ),
                "local_access_gap_within_300": (
                    "max(0, assigned_m3 - eligible local capacity of the same "
                    "class within 300 km of the origin centroid)"
                ),
                "local_capacity_scope": (
                    "same-province facilities of the assigned facility class; "
                    "all-province capacity is reported separately for context"
                ),
            },
            "localization_reference_km": LOCALIZATION_REFERENCE_KM,
            "localization_assumption": (
                "avoidable distance and cost compare each >150 km or >300 km row "
                "with a hypothetical 50 km haul; this is an upper-bound screening "
                "counterfactual, not a siting, investment, or policy recommendation"
            ),
            "high_exposure_screen": {
                "share_threshold": HIGH_EXPOSURE_SHARE,
                "minimum_assigned_m3": HIGH_EXPOSURE_MIN_M3,
            },
            "energy_gj_per_m3": ENERGY_GJ_PER_M3,
        },
        "capacity": {
            "eligible_facility_count": len(capacity_pool.facilities),
            "eligible_capacity_m3_y": capacity_pool.total_capacity_m3_y,
            "capacity_by_class_m3_y": capacity_by_class,
            "capacity_by_province_m3_y": capacity_by_province,
            "origin_offer_count": len(capacity_pool.offers),
        },
        "scenarios": scenarios,
        "high_exposure_provinces": high_exposure,
        "top_greenfield_local_capacity": top_greenfield,
        "counts": {
            "band_rows": len(band_rows),
            "province_class_cells": len(gap_rows),
            "province_rows": len(province_rows),
            "priority_rows": len(priority_rows),
            "scenarios": len(SCENARIOS),
            "classes": len(CLASS_ORDER),
            "provinces_with_assignment": len(
                {
                    row.province
                    for rows in assignments.values()
                    for row in rows
                }
            ),
        },
        "validation": validation,
        "inputs": dict(inputs),
        "caveats": [
            ("Road distances are province-centroid to facility screening "
            "distances, not actual harvest-node origins, routed distances, or "
            "observed truck trips. The centroid is an unweighted ADM1 polygon "
            "centroid."),
            ("The analysis inherits the v8/v9 assignment and capacity v3 model "
            "assumptions, including the 1.2x provincial capacity floor and "
            "proxy capacity scaling. It does not re-optimize assignment or "
            "capacity."),
            ("Eligible local capacity is same-province operating located "
            "capacity of the assigned facility class. All-province capacity is "
            "shown only as context and can overlap across origin provinces."),
            ("The local access gap is a capacity-existence measure. It is not "
            "the same as the volume that actually traveled beyond a threshold; "
            "v8 in particular fills published capacity before nearer proxy "
            "capacity, so actual long-haul volume can exceed the gap."),
            ("Avoidable distance and cost assume the long-haul volume could be "
            "served at 50 km. They ignore greenfield capital, feedstock "
            "availability, landbase, facility scale, permitting, and product "
            "or residue constraints, and are not additive across classes "
            "without capacity reconciliation."),
            ("This is a model-estimate screening artifact. It is not a national "
            "statistic, national average, policy recommendation, official "
            "OSeCAN result, or UI product."),
        ],
        "reproduce": (
            ".venv/bin/python -m "
            "workflows.national.facilities.transport_capacity_gap_v1"
        ),
    }


# ---------------------------------------------------------------------------
# Writers.
# ---------------------------------------------------------------------------

BAND_FIELDS = [
    "scenario",
    "province",
    "facility_class",
    "distance_band",
    "band_low_km",
    "band_high_km",
    "assigned_m3",
    "assigned_share_of_province_class",
    "assigned_share_of_scenario",
    "assigned_published_m3",
    "assigned_proxy_m3",
    "cross_border_assigned_m3",
    "row_count",
    "facility_count_used",
    "eligible_local_capacity_m3_y",
    "eligible_local_published_capacity_m3_y",
    "eligible_local_proxy_capacity_m3_y",
    "eligible_all_provinces_capacity_m3_y",
    "local_capacity_headroom_m3_y",
    "local_capacity_deficit_m3_y",
    "unused_local_capacity_m3_y",
    "local_capacity_utilization",
    "mean_road_distance_km",
    "transport_cost_cad",
    "transport_cost_cad_m3",
    "transport_cost_cad_gj",
    "volume_distance_m3_km",
    "evidence_label",
    "observed",
    "policy_grade",
]
GAP_FIELDS = [
    "scenario",
    "province",
    "facility_class",
    "assigned_m3",
    "assigned_share_of_scenario",
    "assigned_published_m3",
    "assigned_proxy_m3",
    "proxy_share_of_assigned",
    "assigned_within_50_m3_y",
    "assigned_within_150_m3_y",
    "assigned_within_300_m3_y",
    "assigned_gt150_m3_y",
    "assigned_gt300_m3_y",
    "assigned_gt150_share",
    "assigned_gt300_share",
    "eligible_local_capacity_m3_y",
    "eligible_local_capacity_within_50_m3_y",
    "eligible_local_capacity_within_150_m3_y",
    "eligible_local_capacity_within_300_m3_y",
    "eligible_all_provinces_capacity_within_150_m3_y",
    "eligible_all_provinces_capacity_within_300_m3_y",
    "eligible_all_provinces_capacity_m3_y",
    "local_access_gap_within_150_m3_y",
    "local_access_gap_within_300_m3_y",
    "local_capacity_headroom_total_m3_y",
    "local_capacity_deficit_total_m3_y",
    "mean_road_distance_km",
    "gt150_mean_road_distance_km",
    "gt300_mean_road_distance_km",
    "transport_cost_cad",
    "transport_cost_cad_m3",
    "transport_cost_cad_gj",
    "row_count",
    "avoidable_distance_gt150_m3_km",
    "avoidable_distance_gt300_m3_km",
    "avoidable_cost_gt150_cad",
    "avoidable_cost_gt300_cad",
    "avoidable_cost_gt150_cad_gj",
    "localization_reference_km",
    "evidence_label",
    "observed",
    "policy_grade",
]
PROVINCE_FIELDS = [
    "scenario",
    "province",
    "assigned_m3",
    "assigned_share_of_scenario",
    "assigned_within_150_m3_y",
    "assigned_gt150_m3_y",
    "assigned_within_300_m3_y",
    "assigned_gt300_m3_y",
    "assigned_gt150_share",
    "assigned_gt300_share",
    "eligible_local_capacity_m3_y",
    "eligible_local_capacity_within_150_m3_y",
    "eligible_local_capacity_within_300_m3_y",
    "eligible_all_provinces_capacity_within_150_m3_y",
    "eligible_all_provinces_capacity_within_300_m3_y",
    "local_access_gap_within_150_m3_y",
    "local_access_gap_within_300_m3_y",
    "local_capacity_headroom_total_m3_y",
    "local_capacity_deficit_total_m3_y",
    "classes_with_assignment",
    "stranded_classes",
    "top_stranded_class",
    "top_stranded_class_m3_y",
    "top_stranded_class_share_of_stranded",
    "mean_road_distance_km",
    "transport_cost_cad",
    "transport_cost_cad_m3",
    "transport_cost_cad_gj",
    "avoidable_distance_gt150_m3_km",
    "avoidable_cost_gt150_cad",
    "avoidable_cost_gt150_share_of_scenario",
    "high_gt150_exposure",
    "high_gt300_exposure",
    "evidence_label",
    "observed",
    "policy_grade",
]
PRIORITY_FIELDS = [
    "scenario",
    "cost_rank",
    "distance_rank",
    "province",
    "facility_class",
    "assigned_m3",
    "assigned_gt150_m3_y",
    "assigned_gt300_m3_y",
    "assigned_gt150_share",
    "assigned_gt300_share",
    "local_access_gap_within_150_m3_y",
    "mean_road_distance_km",
    "gt150_mean_road_distance_km",
    "gt300_mean_road_distance_km",
    "avoidable_distance_gt150_m3_km",
    "avoidable_cost_gt150_cad",
    "avoidable_cost_gt150_cad_gj",
    "avoidable_distance_gt300_m3_km",
    "avoidable_cost_gt300_cad",
    "share_of_scenario_avoidable_cost_gt150",
    "localization_reference_km",
    "evidence_label",
    "observed",
    "policy_grade",
]


def write_csv(path: Path, fieldnames: Sequence[str], rows: Sequence[Mapping[str, str]]) -> None:
    if not rows:
        raise TransportCapacityGapError(f"refusing to write empty table: {path}")
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


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


def _cad(value: float) -> str:
    return f"{value:,.0f}"


def write_report(
    summary: Mapping[str, Any],
    band_rows: Sequence[Mapping[str, str]],
    gap_rows: Sequence[Mapping[str, str]],
    province_rows: Sequence[Mapping[str, str]],
    priority_rows: Sequence[Mapping[str, str]],
) -> str:
    scenarios = summary["scenarios"]
    v8 = scenarios["v8"]
    v9 = scenarios["v9"]
    lines = [
        "# Transport-Radius Capacity Gap V1",
        "",
        ("Status: **model estimate screening** — `evidence_label=model_estimate`, "
        "`observed=false`, `policy_grade=false`. No national claim, no policy "
        "recommendation, no official OSeCAN change, no UI change, and no siting "
        "optimization."),
        "",
        "## 1. Scope and method",
        "",
        ("- Harvest volume: the committed 119 Mm3 provincial offers assigned to "
        "facilities in v8 (published-first local-first) and v9 (transport-cost "
        "minimization with a 500 km local preference)."),
        ("- Capacity: eligible operating, located facilities in capacity v3 after "
        "the 1.2x provincial harvest floor scaling used by v9."),
        ("- Distance: haversine from the **unweighted ADM1 polygon centroid** of "
        "the origin province to the facility coordinate, multiplied by the "
        "facility-province road factor from `facility_road_distance_audit_v2`."),
        "- Bands: `[0, 50)`, `[50, 100)`, `[100, 150)`, `[150, 300)`, `[300, ∞)` km.",
        ("- Long-haul exposure: assigned volume at or above 150 km. Stranded "
        "volume: assigned volume at or above 300 km."),
        ("- Local access gap at a threshold: `max(0, assigned - eligible "
        "same-province capacity of the same facility class within the "
        "threshold)`. This is a capacity-existence measure, not the actual "
        "volume that traveled beyond the threshold."),
        "",
        ("> **Centroid-origin caveat.** These are province-centroid to facility "
        "screening distances, not actual harvest-node origins, routed distances, "
        "or observed truck trips. A facility near a province's population or "
        "geometric centre can still be hundreds of kilometres from the centroid. "
        "The results are therefore an accessibility screen, not a local supply "
        "audit or a siting recommendation."),
        "",
        "## 2. Scenario totals",
        "",
        _md_table(
            [
                "Scenario",
                "Assigned Mm3",
                ">150 km Mm3",
                ">150 share",
                ">300 km Mm3",
                ">300 share",
                "Mean km",
                "Transport CAD",
                "CAD/m3",
                "Avoidable CAD (>150, 50 km ref)",
            ],
            [
                [
                    scenario,
                    _million(scenarios[scenario]["assigned_m3"]),
                    _million(scenarios[scenario]["assigned_gt150_m3_y"]),
                    _percent(scenarios[scenario]["assigned_gt150_share"]),
                    _million(scenarios[scenario]["assigned_gt300_m3_y"]),
                    _percent(scenarios[scenario]["assigned_gt300_share"]),
                    f"{scenarios[scenario]['mean_road_distance_km']:,.1f}",
                    _cad(scenarios[scenario]["transport_cost_cad"]),
                    f"{scenarios[scenario]['transport_cost_cad_m3']:,.2f}",
                    _cad(scenarios[scenario]["avoidable_cost_gt150_cad"]),
                ]
                for scenario in SCENARIOS
            ],
        ),
        "",
        (f"- Assigned volume is conserved at **{_million(v9['assigned_m3'])} Mm3** "
        "in both v8 and v9."),
        (f"- V9 lowers the assigned-volume-weighted mean centroid-origin road "
        f"distance from **{v8['mean_road_distance_km']:,.1f} km** to "
        f"**{v9['mean_road_distance_km']:,.1f} km**."),
        (f"- V9 transport cost is **{v9['transport_cost_cad_m3']:,.2f} CAD/m3** "
        f"versus **{v8['transport_cost_cad_m3']:,.2f} CAD/m3** in v8 on the same "
        "truck curve."),
        "",
        "## 3. Distance bands",
        "",
        ("Aggregated across province/class cells. Eligible local capacity is "
        "same-province capacity of the assigned class; each facility appears "
        "once in this local-capacity column."),
        "",
    ]
    for scenario in SCENARIOS:
        lines.extend(
            [
                f"### {scenario.upper()}",
                "",
                _md_table(
                    [
                        "Band",
                        "Assigned Mm3",
                        "Share",
                        "Eligible local capacity Mm3",
                        "Local headroom Mm3",
                        "Utilization",
                    ],
                    [
                        [
                            band["distance_band"],
                            _million(band["assigned_m3"]),
                            _percent(band["assigned_share"]),
                            _million(band["eligible_local_capacity_m3_y"]),
                            _million(band["local_capacity_headroom_m3_y"]),
                            _percent(band["local_capacity_utilization"]),
                        ]
                        for band in scenarios[scenario]["bands"]
                    ],
                ),
                "",
            ]
        )
    lines.extend(
        [
            ("The >300 km band carries the largest assigned share. The large "
            ">300 km eligible local capacity is partly a centroid artifact: "
            "facilities in large provinces such as BC and QC are far from the "
            "single ADM1 centroid even when they are locally important."),
            "",
            "## 4. Capacity gaps by province and class",
            "",
        ]
    )
    top_gap_rows = sorted(
        [row for row in gap_rows if row["scenario"] == "v9"],
        key=lambda row: (
            -float(row["local_access_gap_within_150_m3_y"]),
            -float(row["assigned_gt300_m3_y"]),
        ),
    )[:12]
    lines.extend(
        [
            ("Top v9 cells by local access gap within 150 km. `Gap 0-150` is "
            "assigned volume minus eligible same-province, same-class capacity "
            "within 150 km."),
            "",
            _md_table(
                [
                    "Province",
                    "Class",
                    "Assigned Mm3",
                    "GT150 Mm3",
                    "GT300 Mm3",
                    "Local cap 0-150 Mm3",
                    "Gap 0-150 Mm3",
                    "Mean km",
                    "Avoidable CAD",
                ],
                [
                    [
                        row["province"],
                        row["facility_class"],
                        _million(float(row["assigned_m3"])),
                        _million(float(row["assigned_gt150_m3_y"])),
                        _million(float(row["assigned_gt300_m3_y"])),
                        _million(
                            float(row["eligible_local_capacity_within_150_m3_y"])
                        ),
                        _million(float(row["local_access_gap_within_150_m3_y"])),
                        f"{float(row['mean_road_distance_km']):,.1f}",
                        _cad(float(row["avoidable_cost_gt150_cad"])),
                    ]
                    for row in top_gap_rows
                ],
            ),
            "",
            "The same cells under v8, for comparison:",
            "",
        ]
    )
    v8_by_cell = {
        (row["province"], row["facility_class"]): row
        for row in gap_rows
        if row["scenario"] == "v8"
    }
    lines.append(
        _md_table(
            [
                "Province",
                "Class",
                "v8 assigned Mm3",
                "v8 GT300 Mm3",
                "v9 assigned Mm3",
                "v9 GT300 Mm3",
                "v9 mean km",
            ],
            [
                [
                    row["province"],
                    row["facility_class"],
                    _million(float(v8_by_cell[(row["province"], row["facility_class"])]["assigned_m3"])),
                    _million(float(v8_by_cell[(row["province"], row["facility_class"])]["assigned_gt300_m3_y"])),
                    _million(float(row["assigned_m3"])),
                    _million(float(row["assigned_gt300_m3_y"])),
                    f"{float(row['mean_road_distance_km']):,.1f}",
                ]
                for row in top_gap_rows
                if (row["province"], row["facility_class"]) in v8_by_cell
            ],
        )
    )
    lines.extend(
        [
            "",
            "## 5. Stranded provinces",
            "",
            ("The high-exposure flag is a screening flag: at least 50% of assigned "
            "volume at or above the threshold and at least 1 Mm3/yr assigned. "
            "Small provinces can have high shares on small absolute volumes."),
            "",
        ]
    )
    v9_province_rows = sorted(
        [row for row in province_rows if row["scenario"] == "v9"],
        key=lambda row: (-float(row["assigned_gt300_share"]), -float(row["assigned_m3"])),
    )
    lines.extend(
        [
            _md_table(
                [
                    "Province",
                    "Assigned Mm3",
                    ">150 share",
                    ">300 share",
                    "Top stranded class",
                    "Top class Mm3",
                    "Gap 0-150 Mm3",
                    "Mean km",
                    "Avoidable CAD",
                    "High >300 flag",
                ],
                [
                    [
                        row["province"],
                        _million(float(row["assigned_m3"])),
                        _percent(float(row["assigned_gt150_share"])),
                        _percent(float(row["assigned_gt300_share"])),
                        row["top_stranded_class"] or "—",
                        _million(float(row["top_stranded_class_m3_y"])),
                        _million(float(row["local_access_gap_within_150_m3_y"])),
                        f"{float(row['mean_road_distance_km']):,.1f}",
                        _cad(float(row["avoidable_cost_gt150_cad"])),
                        row["high_gt300_exposure"],
                    ]
                    for row in v9_province_rows
                ],
            ),
            "",
        ]
    )
    high_gt300 = summary["high_exposure_provinces"]["v9"]["gt300"]
    if high_gt300:
        lines.append(
            "V9 provinces meeting the high >300 km exposure screen: "
            + ", ".join(
                f"**{row['province']}** ({_percent(row['assigned_gt300_share'])} "
                f"of {_million(row['assigned_m3'])} Mm3)"
                for row in high_gt300
            )
            + "."
        )
    else:
        lines.append("No v9 province meets the high >300 km exposure screen.")
    lines.extend(
        [
            "",
            "Provinces with the largest absolute >300 km volumes dominate the "
            "stranded total even when their share is lower: "
            + ", ".join(
                f"**{row['province']}** ({_million(float(row['assigned_gt300_m3_y']))} Mm3)"
                for row in sorted(
                    v9_province_rows,
                    key=lambda row: -float(row["assigned_gt300_m3_y"]),
                )[:5]
            )
            + ".",
            "",
            "## 6. Greenfield/local capacity screening",
            "",
            ("The table ranks v9 province/class cells by the modelled transport "
            "cost reduction if their >150 km volume were served at a 50 km "
            "reference distance. This is a screening counterfactual, not a "
            "siting optimization. It ignores greenfield capital, feedstock "
            "availability, landbase, facility scale, permitting, and product or "
            "residue constraints; rows are not additive across classes without "
            "capacity reconciliation."),
            "",
        ]
    )
    v9_priority = [
        row for row in priority_rows if row["scenario"] == "v9"
    ][:10]
    lines.extend(
        [
            _md_table(
                [
                    "Rank",
                    "Province",
                    "Class",
                    "GT150 Mm3",
                    "GT300 Mm3",
                    "Avoidable m3*km",
                    "Avoidable CAD",
                    "Share of v9 avoidable CAD",
                ],
                [
                    [
                        row["cost_rank"],
                        row["province"],
                        row["facility_class"],
                        _million(float(row["assigned_gt150_m3_y"])),
                        _million(float(row["assigned_gt300_m3_y"])),
                        f"{float(row['avoidable_distance_gt150_m3_km']) / 1e9:,.2f} billion",
                        _cad(float(row["avoidable_cost_gt150_cad"])),
                        _percent(float(row["share_of_scenario_avoidable_cost_gt150"])),
                    ]
                    for row in v9_priority
                ],
            ),
            "",
            ("The v8 ranking is retained in "
            "`greenfield_local_capacity_priority.csv`; the v9 ranking is the "
            "primary transport-aware screen."),
            "",
            "## 7. Validation",
            "",
        ]
    )
    lines.extend(
        f"- `{name}`: {value}"
        for name, value in summary["validation"]["checks"].items()
    )
    lines.extend(
        [
            f"- `all_passed`: {summary['validation']['all_passed']}",
            "",
            "## 8. Caveats",
            "",
            *[f"- {caveat}" for caveat in summary["caveats"]],
            "",
            "## 9. Reproduce",
            "",
            f"`{summary['reproduce']}`",
            "",
        ]
    )
    return "\n".join(lines)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _input_hashes() -> dict[str, str]:
    paths = (
        V8_ASSIGNMENT_CSV,
        V9_ASSIGNMENT_CSV,
        CAPACITY_CSV,
        INVENTORY_CSV,
        HARVEST_CSV,
        BOUNDARY_GEOJSON,
        FACTORS_CSV,
        TRANSPORT_PARAMETERS_CSV,
    )
    return {
        str(path.relative_to(REPO_ROOT)): _sha256(path) for path in paths
    }


def _write_manifest(
    output_dir: Path,
    generated_utc: str,
    inputs: Mapping[str, str],
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
    with (output_dir / "manifest.json").open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, sort_keys=True)
        handle.write("\n")


# ---------------------------------------------------------------------------
# Build entry point.
# ---------------------------------------------------------------------------

def build(output_dir: Path = OUTPUT_DIR) -> dict[str, Any]:
    """Build the transport-radius capacity gap artifact and return its summary."""

    assert_no_unexpected_deletions()
    generated_utc = _utc_now()
    offers = read_offer_rows()
    offer_total_m3 = math.fsum(offer.volume_m3 for offer in offers)
    assignments = {
        source.name: read_assignment_rows(source) for source in SCENARIO_SOURCES
    }
    capacity_pool = load_capacity_pool(offers)
    parameters = v9.load_transport_parameters()
    cost_cache: dict[float, float] = {}
    _transport_cost_per_m3(LOCALIZATION_REFERENCE_KM, parameters, cost_cache)
    for source in SCENARIO_SOURCES:
        for row in assignments[source.name]:
            _transport_cost_per_m3(row.road_distance_km, parameters, cost_cache)

    band_rows = build_band_rows(
        assignments, capacity_pool.bands, cost_cache, offer_total_m3
    )
    impacts = _impact_by_cell(assignments, cost_cache)
    gap_rows = build_capacity_gap_rows(band_rows, impacts)
    province_rows = build_province_rows(gap_rows)
    priority_rows = build_priority_rows(gap_rows)
    validation = validate_outputs(
        assignments=assignments,
        band_rows=band_rows,
        gap_rows=gap_rows,
        province_rows=province_rows,
        priority_rows=priority_rows,
        capacity_pool=capacity_pool,
        offer_total_m3=offer_total_m3,
    )
    inputs = _input_hashes()
    summary = build_summary(
        generated_utc=generated_utc,
        assignments=assignments,
        band_rows=band_rows,
        gap_rows=gap_rows,
        province_rows=province_rows,
        priority_rows=priority_rows,
        capacity_pool=capacity_pool,
        validation=validation,
        inputs=inputs,
        offer_total_m3=offer_total_m3,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(
        output_dir / "province_class_distance_bands.csv", BAND_FIELDS, band_rows
    )
    write_csv(
        output_dir / "province_class_capacity_gap.csv", GAP_FIELDS, gap_rows
    )
    write_csv(output_dir / "province_summary.csv", PROVINCE_FIELDS, province_rows)
    write_csv(
        output_dir / "greenfield_local_capacity_priority.csv",
        PRIORITY_FIELDS,
        priority_rows,
    )
    report = write_report(
        summary, band_rows, gap_rows, province_rows, priority_rows
    )
    (output_dir / "TRANSPORT_CAPACITY_GAP_V1.md").write_text(
        report, encoding="utf-8"
    )
    with (output_dir / "summary.json").open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, sort_keys=True)
        handle.write("\n")
    _write_manifest(output_dir, generated_utc, inputs, summary)
    return summary


def main() -> None:
    summary = build()
    print(f"{ARTIFACT}: {summary['status']}")


if __name__ == "__main__":
    main()
