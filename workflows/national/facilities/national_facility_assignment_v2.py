"""Assign the 119 Mm3 provincial harvest case using bounded capacity v1.

Assignment v1 carried two scenarios: published-only, and an explicitly
unbounded ``capacity_unbounded_proxy`` sink.  The unbounded sink was useful as
a diagnostic but is not a capacity scenario: one facility could absorb a whole
province.  This v2 uses the capacity layer v1, where every assignable facility
has a finite capacity, either published or a labelled class-median proxy.

Scenarios
---------
``published_only``
    Only published-capacity facilities are eligible.

``published_plus_proxy``
    Published plus class-median proxy facilities are eligible.  Every proxy is
    a finite class median with a reported p25-p75 range.

The allocation rule is unchanged from v1: local province first, nearest
road-adjusted facility first, published capacity before proxy capacity within
a province, and cross-border facilities only after local capacity is
exhausted.  There is no unbounded facility and no fabricated facility-specific
capacity.

Labels: ``model_estimate`` / ``observed=false`` / ``policy_grade=false``.
No legal or AAC claim is made.  No UI or public OSeCAN surface is changed.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from workflows.national.facilities.national_facility_assignment import (
    NON_OPERATING_STATUSES,
    AllocationConfig,
    Location,
    ProvinceOffer,
    haversine_km,
    read_offers,
)
from workflows.national.facilities.national_facility_inventory import PROVINCES

REPO_ROOT = Path(__file__).resolve().parents[3]
INVENTORY_CSV = (
    REPO_ROOT
    / "data/derived/national/facilities/national_facility_inventory_v0"
    / "national_facilities.csv"
)
CAPACITY_CSV = (
    REPO_ROOT
    / "data/derived/national/facilities/national_facility_capacity_v1"
    / "national_facility_capacity_v1.csv"
)
HARVEST_CSV = (
    REPO_ROOT / "data/derived/national/harvest_basis/provincial_allocation/119m.csv"
)
BOUNDARY_GEOJSON = (
    REPO_ROOT / "data/raw/boundaries/canada_adm1_geoboundaries_simplified.geojson"
)
OUTPUT_DIR = (
    REPO_ROOT
    / "data/derived/national/facilities/national_facility_assignment_v2"
)

ARTIFACT = "national_facility_assignment_v2"
ARTIFACT_VERSION = "2.0.0"
SCENARIOS = ("published_only", "published_plus_proxy")
RELEVANT_CLASSES = ("SAW", "PULP", "PANEL", "PELLET", "BIO")
CLASS_ORDER = ("SAW", "PULP", "PANEL", "PELLET", "BIO", "OTHER")

PUBLISHED = "published"
PROXY = "proxy"
NOT_PUBLISHED = "not_published"

EVIDENCE_LABEL = "model_estimate"
OBSERVED = False
POLICY_GRADE = False

VOLUME_EPSILON_M3 = 1e-6
ROAD_FACTOR = 1.3
CROSS_BORDER_PENALTY_KM = 500.0

ASSIGNMENT_FIELDS = [
    "scenario",
    "allocation_order",
    "province",
    "province_offer_m3",
    "facility_id",
    "facility_name",
    "facility_class",
    "facility_province",
    "cross_border",
    "allocation_phase",
    "capacity_status",
    "capacity_m3_y",
    "proxy_low_m3_y",
    "proxy_high_m3_y",
    "assigned_m3",
    "remaining_capacity_m3",
    "utilization",
    "distance_km",
    "road_distance_km",
    "cross_border_penalty_km",
    "rank_distance_km",
    "source_id",
    "source_url",
    "capacity_source_id",
    "capacity_source_url",
    "evidence_tier",
    "evidence_label",
    "observed",
    "policy_grade",
]
PROVINCE_SUMMARY_FIELDS = [
    "scenario",
    "province",
    "offer_m3",
    "assigned_m3",
    "unassigned_m3",
    "assigned_share",
    "local_assigned_m3",
    "cross_border_assigned_m3",
    "cross_border_share",
    "published_assigned_m3",
    "proxy_assigned_m3",
    "facilities_used",
    "published_facilities_used",
    "proxy_facilities_used",
    "mean_distance_km",
    "mean_road_distance_km",
    "max_distance_km",
    "assignment_status",
    "evidence_label",
    "observed",
    "policy_grade",
]
UTILIZATION_FIELDS = [
    "scenario",
    "facility_id",
    "facility_name",
    "facility_class",
    "facility_province",
    "capacity_status",
    "capacity_m3_y",
    "proxy_low_m3_y",
    "proxy_high_m3_y",
    "assigned_m3",
    "remaining_capacity_m3",
    "utilization",
    "used",
    "location_status",
    "screen_status",
    "distance_to_province_centroid_km",
    "operating_status",
    "source_id",
    "source_url",
    "capacity_source_id",
    "capacity_source_url",
    "evidence_tier",
    "evidence_label",
    "observed",
    "policy_grade",
]
UNASSIGNED_FIELDS = [
    "scenario",
    "province",
    "offer_m3",
    "assigned_m3",
    "local_assigned_m3",
    "cross_border_assigned_m3",
    "published_assigned_m3",
    "proxy_assigned_m3",
    "unassigned_m3",
    "unassigned_share",
    "reason",
    "evidence_label",
    "observed",
    "policy_grade",
]
PROVINCE_CLASS_FIELDS = [
    "scenario",
    "province",
    "facility_class",
    "assigned_m3",
    "published_m3",
    "proxy_m3",
    "facilities_used",
    "share_of_scenario_assigned",
]
CONCENTRATION_FIELDS = [
    "scenario",
    "rank",
    "facility_id",
    "facility_name",
    "facility_class",
    "facility_province",
    "capacity_status",
    "capacity_m3_y",
    "assigned_m3",
    "share_of_assigned",
    "cumulative_share",
]
COMPARISON_FIELDS = [
    "scenario",
    "offer_m3",
    "assigned_m3",
    "unassigned_m3",
    "assigned_share",
    "published_m3",
    "proxy_m3",
    "proxy_share_of_assigned",
    "cross_border_m3",
    "cross_border_share",
    "facilities_used",
    "max_facility_m3",
    "max_facility_share",
    "top5_share",
    "hhi",
    "unassigned_provinces",
    "assignment_rows",
]


class AssignmentError(ValueError):
    """Raised when an input or allocation invariant fails."""


@dataclass(frozen=True, slots=True)
class BoundedCapacity:
    """A finite facility capacity, either published or a labelled proxy."""

    capacity_m3_y: float
    status: str
    proxy_low_m3_y: float | None = None
    proxy_high_m3_y: float | None = None

    def __post_init__(self) -> None:
        value = float(self.capacity_m3_y)
        if not math.isfinite(value) or value <= 0.0:
            raise AssignmentError("capacity must be finite and positive")
        if self.status not in {PUBLISHED, PROXY}:
            raise AssignmentError(f"unexpected capacity status {self.status!r}")
        object.__setattr__(self, "capacity_m3_y", value)

        if self.status == PUBLISHED:
            if self.proxy_low_m3_y is not None or self.proxy_high_m3_y is not None:
                raise AssignmentError(
                    "published capacity must not carry a proxy range"
                )
            return

        if self.proxy_low_m3_y is None or self.proxy_high_m3_y is None:
            raise AssignmentError("proxy capacity requires a p25-p75 range")
        low = float(self.proxy_low_m3_y)
        high = float(self.proxy_high_m3_y)
        if not (math.isfinite(low) and math.isfinite(high)):
            raise AssignmentError("proxy range must be finite")
        if not (0.0 < low <= value <= high):
            raise AssignmentError(
                "proxy capacity must lie inside its p25-p75 range"
            )
        object.__setattr__(self, "proxy_low_m3_y", low)
        object.__setattr__(self, "proxy_high_m3_y", high)

    @property
    def is_proxy(self) -> bool:
        return self.status == PROXY


@dataclass(frozen=True, slots=True)
class Facility:
    """A facility with optional finite capacity."""

    facility_id: str
    name: str
    facility_class: str
    province: str
    location: Location | None
    capacity: BoundedCapacity | None
    operating_status: str
    source_id: str
    source_url: str
    capacity_source_id: str
    capacity_source_url: str
    evidence_tier: str

    def __post_init__(self) -> None:
        if not self.facility_id.strip():
            raise AssignmentError("facility_id is required")
        if self.facility_class not in CLASS_ORDER:
            raise AssignmentError(
                f"{self.facility_id}: unexpected facility_class "
                f"{self.facility_class!r}"
            )
        if self.province not in PROVINCES:
            raise AssignmentError(
                f"{self.facility_id}: unexpected province {self.province!r}"
            )

    @property
    def capacity_status(self) -> str:
        return self.capacity.status if self.capacity is not None else NOT_PUBLISHED

    @property
    def capacity_m3_y(self) -> float | None:
        return self.capacity.capacity_m3_y if self.capacity is not None else None

    @property
    def is_operating(self) -> bool:
        return self.operating_status not in NON_OPERATING_STATUSES


@dataclass(frozen=True, slots=True)
class Candidate:
    """A facility ranked for one province offer."""

    facility: Facility
    distance_km: float
    road_distance_km: float
    cross_border: bool
    penalty_km: float
    rank_distance_km: float


@dataclass(frozen=True, slots=True)
class ScenarioResult:
    """Rows produced by one bounded-capacity scenario."""

    scenario: str
    assignments: tuple[dict[str, Any], ...]
    province_summary: tuple[dict[str, Any], ...]
    facility_utilization: tuple[dict[str, Any], ...]
    unassigned: tuple[dict[str, Any], ...]
    concentration: tuple[dict[str, Any], ...]
    province_class: tuple[dict[str, Any], ...]
    metrics: dict[str, Any]


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_csv(path: Path) -> list[dict[str, str]]:
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
    except FileNotFoundError as error:
        raise AssignmentError(f"input file is missing: {path}") from error
    except OSError as error:
        raise AssignmentError(f"cannot read {path}: {error}") from error
    if not rows:
        raise AssignmentError(f"{path}: no data rows")
    return rows


def _require_columns(
    rows: Sequence[dict[str, str]], required: set[str], path: Path
) -> None:
    missing = required - set(rows[0])
    if missing:
        raise AssignmentError(f"{path}: missing required columns {sorted(missing)}")


def _finite_float(value: str | None, *, field: str, context: str) -> float:
    if value is None or not value.strip():
        raise AssignmentError(f"{context}: missing {field}")
    try:
        number = float(value)
    except ValueError as error:
        raise AssignmentError(
            f"{context}: {field} is not numeric: {value!r}"
        ) from error
    if not math.isfinite(number):
        raise AssignmentError(f"{context}: {field} is not finite: {value!r}")
    return number


def _format_float(value: float | None, digits: int = 6) -> str:
    if value is None:
        return ""
    if not math.isfinite(value):
        raise AssignmentError(f"cannot format non-finite value {value!r}")
    if abs(value) < 0.5 * 10**-digits:
        return "0"
    text = f"{value:.{digits}f}".rstrip("0").rstrip(".")
    return text or "0"


def _bool_text(value: bool) -> str:
    return "true" if value else "false"


def read_facilities(
    capacity_path: Path = CAPACITY_CSV,
    inventory_path: Path = INVENTORY_CSV,
) -> tuple[Facility, ...]:
    """Join capacity v1 to inventory v0 and fail on any drift."""

    capacity_rows = _read_csv(capacity_path)
    inventory_rows = _read_csv(inventory_path)
    _require_columns(
        capacity_rows,
        {
            "facility_id",
            "name",
            "facility_class",
            "province",
            "lat",
            "lon",
            "capacity_m3_y",
            "capacity_status",
            "proxy_low_m3_y",
            "proxy_high_m3_y",
            "evidence_tier",
            "source_id",
            "source_url",
            "status",
        },
        capacity_path,
    )
    _require_columns(
        inventory_rows,
        {
            "facility_id",
            "name",
            "facility_class",
            "province",
            "lat",
            "lon",
            "capacity_m3_y",
            "capacity_status",
            "status",
            "source_id",
            "source_url",
            "evidence_tier",
        },
        inventory_path,
    )

    inventory_by_id = {
        row["facility_id"].strip(): row for row in inventory_rows
    }
    if len(inventory_by_id) != len(inventory_rows):
        raise AssignmentError(f"{inventory_path}: duplicate facility_id")
    capacity_ids = {row["facility_id"].strip() for row in capacity_rows}
    missing = set(inventory_by_id) - capacity_ids
    extra = capacity_ids - set(inventory_by_id)
    if missing or extra:
        raise AssignmentError(
            "capacity v1 and inventory v0 do not cover the same facilities: "
            f"missing={sorted(missing)[:5]}, extra={sorted(extra)[:5]}"
        )

    facilities: list[Facility] = []
    for position, row in enumerate(capacity_rows, start=2):
        context = f"{capacity_path}:{position}"
        facility_id = row["facility_id"].strip()
        source = inventory_by_id[facility_id]
        for field in (
            "name",
            "facility_class",
            "province",
            "lat",
            "lon",
            "status",
            "evidence_tier",
        ):
            if row[field].strip() != source[field].strip():
                raise AssignmentError(
                    f"{context}: {field} differs from inventory v0"
                )

        facility_class = row["facility_class"].strip().upper()
        province = row["province"].strip().upper()
        capacity_status = row["capacity_status"].strip().lower()
        if capacity_status == PUBLISHED:
            for field in ("source_id", "source_url"):
                if row[field].strip() != source[field].strip():
                    raise AssignmentError(
                        f"{context}: published {field} differs from inventory v0"
                    )

        capacity: BoundedCapacity | None
        capacity_text = row["capacity_m3_y"].strip()
        if capacity_status == NOT_PUBLISHED:
            if capacity_text:
                raise AssignmentError(
                    f"{context}: not_published row carries a capacity"
                )
            capacity = None
        elif capacity_status == PUBLISHED:
            capacity = BoundedCapacity(
                capacity_m3_y=_finite_float(
                    capacity_text,
                    field="capacity_m3_y",
                    context=context,
                ),
                status=PUBLISHED,
            )
        elif capacity_status == PROXY:
            capacity = BoundedCapacity(
                capacity_m3_y=_finite_float(
                    capacity_text,
                    field="capacity_m3_y",
                    context=context,
                ),
                status=PROXY,
                proxy_low_m3_y=_finite_float(
                    row["proxy_low_m3_y"],
                    field="proxy_low_m3_y",
                    context=context,
                ),
                proxy_high_m3_y=_finite_float(
                    row["proxy_high_m3_y"],
                    field="proxy_high_m3_y",
                    context=context,
                ),
            )
        else:
            raise AssignmentError(
                f"{context}: unexpected capacity_status {capacity_status!r}"
            )

        raw_lat = row["lat"].strip()
        raw_lon = row["lon"].strip()
        if not raw_lat and not raw_lon:
            location = None
        elif not raw_lat or not raw_lon:
            raise AssignmentError(
                f"{context}: latitude and longitude must both be present"
            )
        else:
            location = Location(
                _finite_float(raw_lat, field="lat", context=context),
                _finite_float(raw_lon, field="lon", context=context),
            )

        facilities.append(
            Facility(
                facility_id=facility_id,
                name=row["name"].strip(),
                facility_class=facility_class,
                province=province,
                location=location,
                capacity=capacity,
                operating_status=row["status"].strip().lower() or "unknown",
                source_id=source["source_id"].strip(),
                source_url=source["source_url"].strip(),
                capacity_source_id=row["source_id"].strip(),
                capacity_source_url=row["source_url"].strip(),
                evidence_tier=row["evidence_tier"].strip().upper(),
            )
        )
    return tuple(facilities)


def _is_relevant(facility: Facility) -> bool:
    return facility.facility_class in RELEVANT_CLASSES


def _is_screenable(facility: Facility, scenario: str) -> bool:
    if not _is_relevant(facility):
        return False
    if not facility.is_operating:
        return False
    if facility.location is None:
        return False
    if facility.capacity is None:
        return False
    return not (
        scenario == "published_only" and facility.capacity.status != PUBLISHED
    )


def _candidate(
    offer: ProvinceOffer,
    facility: Facility,
    config: AllocationConfig,
) -> Candidate:
    if facility.location is None:
        raise AssignmentError(
            f"{facility.facility_id}: cannot rank an unlocated facility"
        )
    distance = haversine_km(
        offer.location.lat,
        offer.location.lon,
        facility.location.lat,
        facility.location.lon,
    )
    road_distance = distance * config.road_factor
    cross_border = facility.province != offer.province
    penalty = config.cross_border_penalty_km if cross_border else 0.0
    return Candidate(
        facility=facility,
        distance_km=distance,
        road_distance_km=road_distance,
        cross_border=cross_border,
        penalty_km=penalty,
        rank_distance_km=road_distance + penalty,
    )


def _assignment_row(
    *,
    scenario: str,
    allocation_order: int,
    offer: ProvinceOffer,
    facility: Facility,
    candidate: Candidate,
    assigned_m3: float,
    facility_assigned_m3: float,
    remaining_capacity_m3: float,
    phase: str,
) -> dict[str, Any]:
    if facility.capacity is None:
        raise AssignmentError(
            f"{facility.facility_id}: assigned without a finite capacity"
        )
    capacity = facility.capacity
    utilization = facility_assigned_m3 / capacity.capacity_m3_y
    return {
        "scenario": scenario,
        "allocation_order": allocation_order,
        "province": offer.province,
        "province_offer_m3": _format_float(offer.volume_m3),
        "facility_id": facility.facility_id,
        "facility_name": facility.name,
        "facility_class": facility.facility_class,
        "facility_province": facility.province,
        "cross_border": _bool_text(candidate.cross_border),
        "allocation_phase": phase,
        "capacity_status": capacity.status,
        "capacity_m3_y": _format_float(capacity.capacity_m3_y),
        "proxy_low_m3_y": _format_float(capacity.proxy_low_m3_y),
        "proxy_high_m3_y": _format_float(capacity.proxy_high_m3_y),
        "assigned_m3": _format_float(assigned_m3),
        "remaining_capacity_m3": _format_float(remaining_capacity_m3),
        "utilization": _format_float(utilization),
        "distance_km": _format_float(candidate.distance_km),
        "road_distance_km": _format_float(candidate.road_distance_km),
        "cross_border_penalty_km": _format_float(candidate.penalty_km),
        "rank_distance_km": _format_float(candidate.rank_distance_km),
        "source_id": facility.source_id,
        "source_url": facility.source_url,
        "capacity_source_id": facility.capacity_source_id,
        "capacity_source_url": facility.capacity_source_url,
        "evidence_tier": facility.evidence_tier,
        "evidence_label": EVIDENCE_LABEL,
        "observed": _bool_text(OBSERVED),
        "policy_grade": _bool_text(POLICY_GRADE),
    }


def allocate_scenario(
    offers: Sequence[ProvinceOffer],
    facilities: Sequence[Facility],
    scenario: str,
    config: AllocationConfig | None = None,
) -> ScenarioResult:
    """Run one bounded-capacity scenario with local-first allocation."""

    if scenario not in SCENARIOS:
        raise AssignmentError(f"unknown scenario {scenario!r}")
    config = config or AllocationConfig()
    offers = tuple(offers)
    facilities = tuple(facilities)
    if not offers:
        raise AssignmentError("at least one province offer is required")
    if len({offer.province for offer in offers}) != len(offers):
        raise AssignmentError("province offers must be unique by province")
    if len({facility.facility_id for facility in facilities}) != len(facilities):
        raise AssignmentError("facility ids must be unique")

    eligible = [
        facility for facility in facilities if _is_screenable(facility, scenario)
    ]
    remaining = {
        facility.facility_id: float(facility.capacity_m3_y or 0.0)
        for facility in eligible
    }
    facility_assigned = {facility.facility_id: 0.0 for facility in eligible}
    assignments: list[dict[str, Any]] = []
    allocation_order = 0
    candidate_cache: dict[tuple[str, str], Candidate] = {}

    def candidate_for(offer: ProvinceOffer, facility: Facility) -> Candidate:
        key = (offer.province, facility.facility_id)
        cached = candidate_cache.get(key)
        if cached is None:
            cached = _candidate(offer, facility, config)
            candidate_cache[key] = cached
        return cached

    def record(
        offer: ProvinceOffer,
        facility: Facility,
        take: float,
        candidate: Candidate,
        phase: str,
    ) -> None:
        nonlocal allocation_order
        allocation_order += 1
        facility_assigned[facility.facility_id] += take
        remaining[facility.facility_id] = max(
            0.0, remaining[facility.facility_id] - take
        )
        assignments.append(
            _assignment_row(
                scenario=scenario,
                allocation_order=allocation_order,
                offer=offer,
                facility=facility,
                candidate=candidate,
                assigned_m3=take,
                facility_assigned_m3=facility_assigned[facility.facility_id],
                remaining_capacity_m3=remaining[facility.facility_id],
                phase=phase,
            )
        )

    residual = {offer.province: offer.volume_m3 for offer in offers}

    # Local phase: published capacity before proxy capacity, nearest first.
    for offer in sorted(offers, key=lambda item: item.province):
        local = sorted(
            (
                facility
                for facility in eligible
                if facility.province == offer.province
            ),
            key=lambda facility: (
                facility.capacity.status != PUBLISHED,
                candidate_for(offer, facility).road_distance_km,
                facility.facility_id,
            ),
        )
        for facility in local:
            if residual[offer.province] <= VOLUME_EPSILON_M3:
                break
            take = min(
                residual[offer.province], remaining[facility.facility_id]
            )
            if take <= VOLUME_EPSILON_M3:
                continue
            record(
                offer,
                facility,
                take,
                candidate_for(offer, facility),
                "local",
            )
            residual[offer.province] -= take

    # Cross-border phase: only residual demand, largest residual first.
    offers_by_province = {offer.province: offer for offer in offers}
    for province in sorted(residual, key=lambda code: (-residual[code], code)):
        if residual[province] <= VOLUME_EPSILON_M3:
            continue
        offer = offers_by_province[province]
        candidates = [
            facility
            for facility in eligible
            if facility.province != province
            and remaining[facility.facility_id] > VOLUME_EPSILON_M3
        ]
        candidates.sort(
            key=lambda facility: (
                candidate_for(offer, facility).rank_distance_km,
                facility.facility_id,
            )
        )
        for facility in candidates:
            if residual[province] <= VOLUME_EPSILON_M3:
                break
            take = min(residual[province], remaining[facility.facility_id])
            if take <= VOLUME_EPSILON_M3:
                continue
            record(
                offer,
                facility,
                take,
                candidate_for(offer, facility),
                "cross_border",
            )
            residual[province] -= take

    for province, volume in residual.items():
        if volume < -VOLUME_EPSILON_M3:
            raise AssignmentError(
                f"{scenario}:{province}: negative residual {volume}"
            )
        residual[province] = max(0.0, volume)

    province_summary = _build_province_summary(scenario, offers, assignments)
    province_locations = {
        offer.province: offer.location for offer in offers
    }
    utilization = _build_facility_utilization(
        scenario, facilities, facility_assigned, province_locations
    )
    unassigned = _build_unassigned_rows(province_summary)
    concentration = _build_concentration(scenario, assignments)
    province_class = _build_province_class(scenario, assignments)
    metrics = _build_metrics(
        scenario, offers, assignments, province_summary, unassigned
    )
    _validate_scenario(
        scenario,
        offers,
        assignments,
        province_summary,
        unassigned,
        utilization,
    )
    return ScenarioResult(
        scenario=scenario,
        assignments=tuple(assignments),
        province_summary=tuple(province_summary),
        facility_utilization=tuple(utilization),
        unassigned=tuple(unassigned),
        concentration=tuple(concentration),
        province_class=tuple(province_class),
        metrics=metrics,
    )


def _weighted_mean(
    rows: Sequence[dict[str, Any]], value_field: str
) -> float | None:
    total = sum(float(row["assigned_m3"]) for row in rows)
    if total <= 0.0:
        return None
    weighted = sum(
        float(row[value_field]) * float(row["assigned_m3"]) for row in rows
    )
    return weighted / total


def _build_province_summary(
    scenario: str,
    offers: Sequence[ProvinceOffer],
    assignments: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows_by_province: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in assignments:
        rows_by_province[row["province"]].append(row)

    summary: list[dict[str, Any]] = []
    for offer in sorted(offers, key=lambda item: item.province):
        rows = rows_by_province.get(offer.province, [])
        assigned = sum(float(row["assigned_m3"]) for row in rows)
        local = sum(
            float(row["assigned_m3"])
            for row in rows
            if row["allocation_phase"] == "local"
        )
        cross_border = assigned - local
        published = sum(
            float(row["assigned_m3"])
            for row in rows
            if row["capacity_status"] == PUBLISHED
        )
        proxy = assigned - published
        facilities_used = {row["facility_id"] for row in rows}
        published_facilities = {
            row["facility_id"]
            for row in rows
            if row["capacity_status"] == PUBLISHED
        }
        unassigned = offer.volume_m3 - assigned
        if unassigned < -VOLUME_EPSILON_M3:
            raise AssignmentError(
                f"{scenario}:{offer.province}: assigned more than the offer"
            )
        unassigned = max(0.0, unassigned)
        if offer.volume_m3 <= 0.0:
            status = "zero_offer"
        elif unassigned <= VOLUME_EPSILON_M3:
            status = "fully_assigned"
        elif assigned <= VOLUME_EPSILON_M3:
            status = "unassigned"
        else:
            status = "partial"

        distances = [float(row["distance_km"]) for row in rows]
        summary.append(
            {
                "scenario": scenario,
                "province": offer.province,
                "offer_m3": _format_float(offer.volume_m3),
                "assigned_m3": _format_float(assigned),
                "unassigned_m3": _format_float(unassigned),
                "assigned_share": _format_float(
                    assigned / offer.volume_m3 if offer.volume_m3 > 0.0 else None
                ),
                "local_assigned_m3": _format_float(local),
                "cross_border_assigned_m3": _format_float(cross_border),
                "cross_border_share": _format_float(
                    cross_border / assigned if assigned > 0.0 else None
                ),
                "published_assigned_m3": _format_float(published),
                "proxy_assigned_m3": _format_float(proxy),
                "facilities_used": len(facilities_used),
                "published_facilities_used": len(published_facilities),
                "proxy_facilities_used": len(
                    facilities_used - published_facilities
                ),
                "mean_distance_km": _format_float(_weighted_mean(rows, "distance_km")),
                "mean_road_distance_km": _format_float(
                    _weighted_mean(rows, "road_distance_km")
                ),
                "max_distance_km": _format_float(
                    max(distances) if distances else None
                ),
                "assignment_status": status,
                "evidence_label": EVIDENCE_LABEL,
                "observed": _bool_text(OBSERVED),
                "policy_grade": _bool_text(POLICY_GRADE),
            }
        )
    return summary


def _screen_status(facility: Facility, scenario: str) -> str:
    if not _is_relevant(facility):
        return "excluded_class"
    if not facility.is_operating:
        return "excluded_non_operating"
    if facility.location is None:
        return "excluded_unlocated"
    if facility.capacity is None:
        return "excluded_no_capacity"
    if scenario == "published_only" and facility.capacity.status != PUBLISHED:
        return "excluded_proxy_in_published_only"
    return f"eligible_{facility.capacity.status}"


def _build_facility_utilization(
    scenario: str,
    facilities: Sequence[Facility],
    facility_assigned: dict[str, float],
    province_locations: dict[str, Location],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for facility in sorted(
        (item for item in facilities if _is_relevant(item)),
        key=lambda item: (
            item.province,
            item.facility_class,
            item.facility_id,
        ),
    ):
        assigned = facility_assigned.get(facility.facility_id, 0.0)
        capacity = facility.capacity
        screen_status = _screen_status(facility, scenario)
        screenable = screen_status.startswith("eligible_")
        remaining = (
            max(0.0, capacity.capacity_m3_y - assigned)
            if capacity is not None and screenable
            else None
        )
        utilization = (
            assigned / capacity.capacity_m3_y
            if capacity is not None and screenable
            else None
        )
        distance = None
        if facility.location is not None:
            centroid = province_locations.get(facility.province)
            if centroid is not None:
                distance = haversine_km(
                    centroid.lat,
                    centroid.lon,
                    facility.location.lat,
                    facility.location.lon,
                )
        rows.append(
            {
                "scenario": scenario,
                "facility_id": facility.facility_id,
                "facility_name": facility.name,
                "facility_class": facility.facility_class,
                "facility_province": facility.province,
                "capacity_status": facility.capacity_status,
                "capacity_m3_y": _format_float(facility.capacity_m3_y),
                "proxy_low_m3_y": _format_float(
                    capacity.proxy_low_m3_y if capacity is not None else None
                ),
                "proxy_high_m3_y": _format_float(
                    capacity.proxy_high_m3_y if capacity is not None else None
                ),
                "assigned_m3": _format_float(assigned),
                "remaining_capacity_m3": _format_float(remaining),
                "utilization": _format_float(utilization),
                "used": _bool_text(assigned > VOLUME_EPSILON_M3),
                "location_status": "located" if facility.location else "unlocated",
                "screen_status": screen_status,
                "distance_to_province_centroid_km": _format_float(distance),
                "operating_status": facility.operating_status,
                "source_id": facility.source_id,
                "source_url": facility.source_url,
                "capacity_source_id": facility.capacity_source_id,
                "capacity_source_url": facility.capacity_source_url,
                "evidence_tier": facility.evidence_tier,
                "evidence_label": EVIDENCE_LABEL,
                "observed": _bool_text(OBSERVED),
                "policy_grade": _bool_text(POLICY_GRADE),
            }
        )
    return rows


def _build_unassigned_rows(
    province_summary: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in province_summary:
        unassigned = float(row["unassigned_m3"])
        if unassigned <= VOLUME_EPSILON_M3:
            continue
        offer = float(row["offer_m3"])
        rows.append(
            {
                "scenario": row["scenario"],
                "province": row["province"],
                "offer_m3": row["offer_m3"],
                "assigned_m3": row["assigned_m3"],
                "local_assigned_m3": row["local_assigned_m3"],
                "cross_border_assigned_m3": row["cross_border_assigned_m3"],
                "published_assigned_m3": row["published_assigned_m3"],
                "proxy_assigned_m3": row["proxy_assigned_m3"],
                "unassigned_m3": row["unassigned_m3"],
                "unassigned_share": _format_float(
                    unassigned / offer if offer > 0.0 else None
                ),
                "reason": (
                    "no eligible located finite capacity remains after the "
                    "local-first and cross-border phases"
                ),
                "evidence_label": EVIDENCE_LABEL,
                "observed": _bool_text(OBSERVED),
                "policy_grade": _bool_text(POLICY_GRADE),
            }
        )
    return rows


def _build_concentration(
    scenario: str, assignments: Sequence[dict[str, Any]]
) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in assignments:
        entry = grouped.setdefault(
            row["facility_id"],
            {
                "facility_id": row["facility_id"],
                "facility_name": row["facility_name"],
                "facility_class": row["facility_class"],
                "facility_province": row["facility_province"],
                "capacity_status": row["capacity_status"],
                "capacity_m3_y": row["capacity_m3_y"],
                "assigned_m3": 0.0,
            },
        )
        entry["assigned_m3"] += float(row["assigned_m3"])

    total = sum(float(entry["assigned_m3"]) for entry in grouped.values())
    ordered = sorted(
        grouped.values(),
        key=lambda entry: (-entry["assigned_m3"], entry["facility_id"]),
    )
    rows: list[dict[str, Any]] = []
    cumulative = 0.0
    for rank, entry in enumerate(ordered, start=1):
        share = entry["assigned_m3"] / total if total > 0.0 else 0.0
        cumulative += share
        rows.append(
            {
                "scenario": scenario,
                "rank": rank,
                "facility_id": entry["facility_id"],
                "facility_name": entry["facility_name"],
                "facility_class": entry["facility_class"],
                "facility_province": entry["facility_province"],
                "capacity_status": entry["capacity_status"],
                "capacity_m3_y": entry["capacity_m3_y"],
                "assigned_m3": _format_float(entry["assigned_m3"]),
                "share_of_assigned": _format_float(share),
                "cumulative_share": _format_float(cumulative),
            }
        )
    return rows


def _build_province_class(
    scenario: str, assignments: Sequence[dict[str, Any]]
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in assignments:
        grouped[(row["province"], row["facility_class"])].append(row)

    total = sum(float(row["assigned_m3"]) for row in assignments)
    rows: list[dict[str, Any]] = []
    for province in PROVINCES:
        for facility_class in CLASS_ORDER:
            group = grouped.get((province, facility_class), [])
            if not group:
                continue
            assigned = sum(float(row["assigned_m3"]) for row in group)
            published = sum(
                float(row["assigned_m3"])
                for row in group
                if row["capacity_status"] == PUBLISHED
            )
            rows.append(
                {
                    "scenario": scenario,
                    "province": province,
                    "facility_class": facility_class,
                    "assigned_m3": _format_float(assigned),
                    "published_m3": _format_float(published),
                    "proxy_m3": _format_float(assigned - published),
                    "facilities_used": len(
                        {row["facility_id"] for row in group}
                    ),
                    "share_of_scenario_assigned": _format_float(
                        assigned / total if total > 0.0 else None
                    ),
                }
            )
    return rows


def _build_metrics(
    scenario: str,
    offers: Sequence[ProvinceOffer],
    assignments: Sequence[dict[str, Any]],
    province_summary: Sequence[dict[str, Any]],
    unassigned: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    offer_total = sum(offer.volume_m3 for offer in offers)
    assigned = sum(float(row["assigned_m3"]) for row in assignments)
    published = sum(
        float(row["assigned_m3"])
        for row in assignments
        if row["capacity_status"] == PUBLISHED
    )
    proxy = assigned - published
    cross_border = sum(
        float(row["assigned_m3"])
        for row in assignments
        if row["cross_border"] == "true"
    )
    facilities_used = {row["facility_id"] for row in assignments}
    by_facility: dict[str, float] = defaultdict(float)
    for row in assignments:
        by_facility[row["facility_id"]] += float(row["assigned_m3"])
    shares = sorted(
        (
            volume / assigned
            for volume in by_facility.values()
            if assigned > 0.0
        ),
        reverse=True,
    )
    return {
        "scenario": scenario,
        "offer_m3": offer_total,
        "assigned_m3": assigned,
        "unassigned_m3": offer_total - assigned,
        "assigned_share": assigned / offer_total if offer_total > 0.0 else None,
        "published_m3": published,
        "proxy_m3": proxy,
        "proxy_share_of_assigned": proxy / assigned if assigned > 0.0 else None,
        "cross_border_m3": cross_border,
        "cross_border_share": (
            cross_border / assigned if assigned > 0.0 else None
        ),
        "facilities_used": len(facilities_used),
        "max_facility_m3": max(by_facility.values(), default=0.0),
        "max_facility_share": max(shares, default=0.0),
        "top5_share": sum(shares[:5]),
        "hhi": sum(share * share for share in shares),
        "unassigned_provinces": len(unassigned),
        "assignment_rows": len(assignments),
        "province_rows": len(province_summary),
    }


def _validate_scenario(
    scenario: str,
    offers: Sequence[ProvinceOffer],
    assignments: Sequence[dict[str, Any]],
    province_summary: Sequence[dict[str, Any]],
    unassigned: Sequence[dict[str, Any]],
    utilization: Sequence[dict[str, Any]],
) -> None:
    offer_total = sum(offer.volume_m3 for offer in offers)
    assigned_total = sum(float(row["assigned_m3"]) for row in assignments)
    unassigned_total = sum(float(row["unassigned_m3"]) for row in unassigned)
    if abs(offer_total - assigned_total - unassigned_total) > 0.01:
        raise AssignmentError(
            f"{scenario}: conservation failure: offer {offer_total:,.3f}, "
            f"assigned {assigned_total:,.3f}, unassigned "
            f"{unassigned_total:,.3f}"
        )
    summary_offer = sum(float(row["offer_m3"]) for row in province_summary)
    summary_assigned = sum(float(row["assigned_m3"]) for row in province_summary)
    summary_unassigned = sum(
        float(row["unassigned_m3"]) for row in province_summary
    )
    if abs(summary_offer - offer_total) > 0.01:
        raise AssignmentError(f"{scenario}: province offer total does not conserve")
    if abs(summary_assigned - assigned_total) > 0.01:
        raise AssignmentError(
            f"{scenario}: province assigned total does not match assignments"
        )
    if abs(summary_unassigned - unassigned_total) > 0.01:
        raise AssignmentError(
            f"{scenario}: province unassigned total does not match unassigned"
        )
    for row in assignments:
        capacity = float(row["capacity_m3_y"])
        if not math.isfinite(capacity) or capacity <= 0.0:
            raise AssignmentError(
                f"{scenario}:{row['facility_id']}: non-finite capacity"
            )
        if float(row["assigned_m3"]) > capacity + VOLUME_EPSILON_M3:
            raise AssignmentError(
                f"{scenario}:{row['facility_id']}: assigned above capacity"
            )
        if scenario == "published_only" and row["capacity_status"] != PUBLISHED:
            raise AssignmentError(
                f"{scenario}:{row['facility_id']}: proxy assigned in "
                "published-only scenario"
            )
    for row in utilization:
        if row["utilization"] == "":
            continue
        if float(row["utilization"]) > 1.0 + 1e-9:
            raise AssignmentError(
                f"{scenario}:{row['facility_id']}: utilization above 1"
            )


def _capacity_basis(
    facilities: Sequence[Facility],
) -> dict[str, Any]:
    relevant = [item for item in facilities if _is_relevant(item)]
    published = [
        item for item in relevant if item.capacity_status == PUBLISHED
    ]
    proxies = [item for item in relevant if item.capacity_status == PROXY]
    unavailable = [
        item for item in relevant if item.capacity is None
    ]
    published_located = [
        item for item in published if item.location is not None
    ]
    published_unlocated = [
        item for item in published if item.location is None
    ]
    proxies_located = [
        item for item in proxies if item.location is not None
    ]
    proxies_unlocated = [
        item for item in proxies if item.location is None
    ]

    by_province: dict[str, dict[str, Any]] = {}
    for province in PROVINCES:
        province_facilities = [
            item for item in relevant if item.province == province
        ]
        by_province[province] = {
            "records": len(province_facilities),
            "published": sum(
                1
                for item in province_facilities
                if item.capacity_status == PUBLISHED
            ),
            "proxy": sum(
                1
                for item in province_facilities
                if item.capacity_status == PROXY
            ),
            "unavailable": sum(
                1 for item in province_facilities if item.capacity is None
            ),
            "total_capacity_m3_y": sum(
                float(item.capacity_m3_y or 0.0)
                for item in province_facilities
            ),
        }

    return {
        "inventory_records": len(facilities),
        "relevant_records": len(relevant),
        "published_records": len(published),
        "published_capacity_m3_y": sum(
            float(item.capacity_m3_y or 0.0) for item in published
        ),
        "published_located_records": len(published_located),
        "published_located_capacity_m3_y": sum(
            float(item.capacity_m3_y or 0.0) for item in published_located
        ),
        "published_unlocated_records": len(published_unlocated),
        "published_unlocated_capacity_m3_y": sum(
            float(item.capacity_m3_y or 0.0) for item in published_unlocated
        ),
        "proxy_records": len(proxies),
        "proxy_capacity_m3_y": sum(
            float(item.capacity_m3_y or 0.0) for item in proxies
        ),
        "proxy_located_records": len(proxies_located),
        "proxy_located_capacity_m3_y": sum(
            float(item.capacity_m3_y or 0.0) for item in proxies_located
        ),
        "proxy_unlocated_records": len(proxies_unlocated),
        "proxy_unlocated_capacity_m3_y": sum(
            float(item.capacity_m3_y or 0.0) for item in proxies_unlocated
        ),
        "unavailable_records": len(unavailable),
        "other_class_records": len(facilities) - len(relevant),
        "non_operating_records": sum(
            1 for item in relevant if not item.is_operating
        ),
        "by_province": by_province,
    }


def _caveats(capacity: dict[str, Any]) -> list[str]:
    return [
        (
            "All volumes are model estimates (`model_estimate`); "
            "`observed=false` and `policy_grade=false`."
        ),
        (
            "The 119 Mm3 case is a planning allocation, not a legal AAC, a "
            "sustainable-supply determination, or an observed harvest outcome. "
            "No legal/AAC claim is made."
        ),
        (
            "Published capacity is the 2015 Mills DB/FFCM processing-line infeed "
            "proxy carried by national_facility_inventory_v0, not licensed, "
            "current, or verified operating capacity."
        ),
        (
            f"Class-median proxies cover {capacity['proxy_records']} relevant "
            f"records ({capacity['proxy_capacity_m3_y'] / 1e6:.2f} Mm3/yr). "
            "Each proxy is a BC class median with a p25-p75 range, not a "
            "facility measurement."
        ),
        (
            f"{capacity['published_unlocated_records']} published-capacity "
            f"records ({capacity['published_unlocated_capacity_m3_y'] / 1e6:.2f} "
            "Mm3/yr) have no coordinate and are excluded from the spatial "
            "screen. `published_only` is therefore a lower bound on placement "
            "in published capacity, not a full use of it."
        ),
        (
            f"{capacity['other_class_records']} OTHER-class records are excluded "
            "before capacity screening. No unbounded facility exists in v2: "
            "every assignment row carries a finite capacity, and no relevant "
            "record is left without one."
        ),
        (
            "The allocation remains local-first with a 500 km cross-border "
            "ranking penalty. Cross-border volume is a screening result, not "
            "an interprovincial trade forecast."
        ),
        (
            "The 119 Mm3 provincial volume is one ungraded offer per province. "
            "No sawlog/pulp/panel/pellet/biomass split is inferred; class "
            "totals are recipient facility classes, not feedstock grades."
        ),
        (
            "Harvest volume is treated one-to-one as facility infeed volume. "
            "Moisture, bark, oversize, grade, and recovery conversions are not "
            "modelled."
        ),
        (
            "Distances are haversine from an unweighted ADM1 polygon centroid "
            "to the facility coordinate, multiplied by a 1.3 road factor for "
            "ranking. No routing, road network, travel time, or cost model is "
            "used."
        ),
        (
            "OSM presence does not establish operating status. Non-operating "
            "records are excluded; all other inventory statuses are unknown."
        ),
        (
            "The class-median proxy assumes BC class structure is transferable "
            "to other provinces. That assumption is untested and is a "
            "first-order uncertainty, not an observed capacity."
        ),
        (
            "Scenario B is not a published-capacity-maximizing run. Local "
            "proxies displace some cross-border published volume because the "
            "rule fills local capacity first and ranks cross-border candidates "
            "by distance."
        ),
        "No UI or public OSeCAN surface is changed by this artifact.",
    ]


def _summary(
    *,
    generated_utc: str,
    facilities: Sequence[Facility],
    offers: Sequence[ProvinceOffer],
    centroid_methods: dict[str, str],
    results: dict[str, ScenarioResult],
    inputs: dict[str, str],
) -> dict[str, Any]:
    capacity = _capacity_basis(facilities)
    metrics = {scenario: results[scenario].metrics for scenario in SCENARIOS}
    comparison = {
        "assigned_delta_m3": (
            metrics["published_plus_proxy"]["assigned_m3"]
            - metrics["published_only"]["assigned_m3"]
        ),
        "unassigned_delta_m3": (
            metrics["published_plus_proxy"]["unassigned_m3"]
            - metrics["published_only"]["unassigned_m3"]
        ),
        "proxy_share_of_assigned": metrics["published_plus_proxy"][
            "proxy_share_of_assigned"
        ],
        "scenario_b_assigns_at_least_a": (
            metrics["published_plus_proxy"]["assigned_m3"]
            + 0.01
            >= metrics["published_only"]["assigned_m3"]
        ),
    }
    validation = {
        "conservation_gap_m3": {},
        "known_capacity_violations": 0,
        "max_utilization": {},
        "unbounded_assignments": 0,
        "proxy_assignments_in_published_only": 0,
        "max_single_facility_share": {},
        "proxy_range_violations": 0,
    }
    for scenario in SCENARIOS:
        result = results[scenario]
        assigned = sum(
            float(row["assigned_m3"]) for row in result.assignments
        )
        unassigned = sum(
            float(row["unassigned_m3"]) for row in result.unassigned
        )
        validation["conservation_gap_m3"][scenario] = (
            sum(offer.volume_m3 for offer in offers) - assigned - unassigned
        )
        utilizations = [
            float(row["utilization"])
            for row in result.facility_utilization
            if row["utilization"] != ""
        ]
        validation["max_utilization"][scenario] = (
            max(utilizations) if utilizations else 0.0
        )
        validation["known_capacity_violations"] += sum(
            1
            for row in result.facility_utilization
            if row["capacity_status"] in {PUBLISHED, PROXY}
            and row["utilization"] != ""
            and float(row["utilization"]) > 1.0 + 1e-9
        )
        validation["max_single_facility_share"][scenario] = metrics[scenario][
            "max_facility_share"
        ]
        validation["unbounded_assignments"] += sum(
            1
            for row in result.assignments
            if row["capacity_m3_y"] == ""
        )
        if scenario == "published_only":
            validation["proxy_assignments_in_published_only"] = sum(
                1
                for row in result.assignments
                if row["capacity_status"] == PROXY
            )
        validation["proxy_range_violations"] += sum(
            1
            for row in result.assignments
            if row["capacity_status"] == PROXY
            and not (
                float(row["proxy_low_m3_y"])
                <= float(row["capacity_m3_y"])
                <= float(row["proxy_high_m3_y"])
            )
        )

    return {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": generated_utc,
        "labels": {
            "evidence_label": EVIDENCE_LABEL,
            "observed": OBSERVED,
            "policy_grade": POLICY_GRADE,
        },
        "caveats": _caveats(capacity),
        "method": {
            "harvest_case": "119m",
            "harvest_file": str(HARVEST_CSV.relative_to(REPO_ROOT)),
            "capacity_layer": str(CAPACITY_CSV.relative_to(REPO_ROOT)),
            "supply_offers": (
                "one ungraded provincial offer per province; no class split"
            ),
            "quantity_basis": (
                "province harvest_m3 treated one-to-one as facility infeed m3; "
                "no moisture, bark, grade, or recovery conversion"
            ),
            "eligible_classes": list(RELEVANT_CLASSES),
            "excluded_classes": ["OTHER"],
            "local_first": True,
            "capacity_rule": (
                "every assignable facility has a finite capacity; assigned_m3 "
                "<= capacity_m3_y for all rows"
            ),
            "published_priority": (
                "within a province, published-capacity facilities are filled "
                "before proxy-capacity facilities"
            ),
            "cross_border_rule": (
                "cross-border only after local capacity is exhausted; ranked by "
                "road distance plus the 500 km penalty"
            ),
            "cross_border_competition": (
                "province residuals processed in descending volume, then "
                "province code"
            ),
            "cross_border_penalty_km": CROSS_BORDER_PENALTY_KM,
            "road_factor": ROAD_FACTOR,
            "distance_basis": (
                "haversine from ADM1 polygon centroid to facility coordinate; "
                "road distance = haversine * road factor"
            ),
            "centroid_source": str(BOUNDARY_GEOJSON.relative_to(REPO_ROOT)),
            "centroid_methods": centroid_methods,
            "province_order": list(PROVINCES),
            "non_operating_statuses_excluded": sorted(NON_OPERATING_STATUSES),
            "unlocated_capacity": (
                "excluded from the spatial screen and reported separately"
            ),
            "scenarios": {
                "published_only": (
                    "only published-capacity facilities are eligible"
                ),
                "published_plus_proxy": (
                    "published plus class-median proxy facilities are eligible; "
                    "every proxy is finite and bounded by its own capacity"
                ),
            },
            "no_unbounded_facility": (
                "v2 contains no capacity_unbounded_proxy and no facility can "
                "absorb unlimited volume"
            ),
        },
        "inputs": inputs,
        "capacity": capacity,
        "scenarios": metrics,
        "comparison": comparison,
        "province_class_totals": [
            row
            for scenario in SCENARIOS
            for row in results[scenario].province_class
        ],
        "concentration": {
            scenario: {
                "top1_share": results[scenario].metrics["max_facility_share"],
                "top5_share": results[scenario].metrics["top5_share"],
                "hhi": results[scenario].metrics["hhi"],
                "facilities_used": results[scenario].metrics["facilities_used"],
            }
            for scenario in SCENARIOS
        },
        "validation": validation,
        "outputs": {
            "facility_assignment_csv": "facility_assignment.csv",
            "province_summary_csv": "province_summary.csv",
            "facility_utilization_csv": "facility_utilization.csv",
            "unassigned_csv": "unassigned.csv",
            "province_class_totals_csv": "province_class_totals.csv",
            "concentration_csv": "concentration.csv",
            "scenario_comparison_csv": "scenario_comparison.csv",
            "report_md": "NATIONAL_FACILITY_ASSIGNMENT_V2.md",
        },
        "reproduce": (
            ".venv/bin/python -m "
            "workflows.national.facilities.national_facility_assignment_v2"
        ),
    }


def _write_csv(
    path: Path, fieldnames: Sequence[str], rows: Iterable[dict[str, Any]]
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def _md_table(headers: Sequence[str], rows: Iterable[Sequence[Any]]) -> list[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join("---" for _ in headers) + "|",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return lines


def _md_number(value: float | None, digits: int = 0) -> str:
    if value is None:
        return ""
    return f"{value:,.{digits}f}"


def _report(summary: dict[str, Any], results: dict[str, ScenarioResult]) -> str:
    metrics = summary["scenarios"]
    capacity = summary["capacity"]
    lines = [
        "# National Facility Assignment v2",
        "",
        f"Generated {summary['generated_utc']}.",
        "",
        (
            "**Labels:** `model_estimate`; `observed=false`; "
            "`policy_grade=false`."
        ),
        "",
        (
            "This artifact replaces the v1 unbounded-unknown diagnostic with two "
            "bounded scenarios: published capacity only, and published plus "
            "class-median proxy capacity. Every assignment row has a finite "
            "capacity. It is not a legal AAC, not an observed allocation, and "
            "not policy-grade. No UI or public OSeCAN surface is changed."
        ),
        "",
        "## Method",
        "",
        (
            f"1. Read capacity v1, which carries "
            f"{capacity['published_records']} published values and "
            f"{capacity['proxy_records']} labelled class-median proxies."
        ),
        (
            "2. Build one ungraded supply offer per province from `119m.csv` "
            f"({metrics['published_only']['offer_m3']:,.0f} m3 nationally)."
        ),
        (
            "3. Scenario A (`published_only`) admits only published-capacity "
            "facilities. Scenario B (`published_plus_proxy`) admits published "
            "and proxy facilities."
        ),
        (
            "4. Local province first. Within a province, published capacity is "
            "filled before proxy capacity, nearest road-adjusted facility first."
        ),
        (
            "5. Cross-border facilities are considered only after local capacity "
            "is exhausted, ranked by road distance plus a 500 km penalty."
        ),
        (
            "6. Every facility capacity is finite. There is no unbounded sink "
            "and no facility-level capacity is fabricated."
        ),
        "",
        "## Capacity basis",
        "",
        *_md_table(
            ["Measure", "Records", "Capacity m3/yr"],
            [
                ["Relevant facility records", capacity["relevant_records"], ""],
                [
                    "Published, all",
                    capacity["published_records"],
                    _md_number(capacity["published_capacity_m3_y"]),
                ],
                [
                    "Published, located and screenable",
                    capacity["published_located_records"],
                    _md_number(capacity["published_located_capacity_m3_y"]),
                ],
                [
                    "Published, unlocated and excluded",
                    capacity["published_unlocated_records"],
                    _md_number(capacity["published_unlocated_capacity_m3_y"]),
                ],
                [
                    "Class-median proxy, all",
                    capacity["proxy_records"],
                    _md_number(capacity["proxy_capacity_m3_y"]),
                ],
                [
                    "Class-median proxy, located and screenable",
                    capacity["proxy_located_records"],
                    _md_number(capacity["proxy_located_capacity_m3_y"]),
                ],
                [
                    "Class-median proxy, unlocated",
                    capacity["proxy_unlocated_records"],
                    _md_number(capacity["proxy_unlocated_capacity_m3_y"]),
                ],
                [
                    "OTHER-class records (excluded)",
                    capacity["other_class_records"],
                    "",
                ],
                [
                    "Relevant records without capacity",
                    capacity["unavailable_records"],
                    "",
                ],
            ],
        ),
        "",
        (
            "The unlocated published capacity is excluded from the spatial "
            "screen. Scenario A is therefore a lower bound on placement in "
            "published capacity."
        ),
        "",
        "## Scenario comparison",
        "",
        *_md_table(
            [
                "Scenario",
                "Assigned m3",
                "Unassigned m3",
                "Published m3",
                "Proxy m3",
                "Cross-border m3",
                "Facilities",
                "Max facility share",
                "HHI",
            ],
            [
                [
                    scenario,
                    _md_number(metrics[scenario]["assigned_m3"]),
                    _md_number(metrics[scenario]["unassigned_m3"]),
                    _md_number(metrics[scenario]["published_m3"]),
                    _md_number(metrics[scenario]["proxy_m3"]),
                    _md_number(metrics[scenario]["cross_border_m3"]),
                    metrics[scenario]["facilities_used"],
                    f"{metrics[scenario]['max_facility_share']:.2%}",
                    f"{metrics[scenario]['hhi']:.4f}",
                ]
                for scenario in SCENARIOS
            ],
        ),
        "",
        (
            f"Scenario B assigns "
            f"{summary['comparison']['assigned_delta_m3']:,.0f} m3 more than "
            "scenario A. Proxy capacity supplies "
            f"{100.0 * (summary['comparison']['proxy_share_of_assigned'] or 0.0):.1f}% "
            "of scenario B's assigned volume."
        ),
        "",
        (
            "Scenario B uses less published capacity than scenario A. Local "
            "proxy capacity absorbs demand that would otherwise cross a border "
            "in scenario A, and cross-border candidates are ranked by distance "
            "rather than capacity status. Scenario B is a bounded-absorption "
            "screening run, not a published-capacity-maximizing run."
        ),
        "",
        "## Province totals",
        "",
    ]

    for scenario in SCENARIOS:
        lines += [
            f"### `{scenario}`",
            "",
            *_md_table(
                [
                    "Province",
                    "Offer m3",
                    "Assigned m3",
                    "Local m3",
                    "Cross-border m3",
                    "Published m3",
                    "Proxy m3",
                    "Unassigned m3",
                ],
                [
                    [
                        row["province"],
                        _md_number(float(row["offer_m3"])),
                        _md_number(float(row["assigned_m3"])),
                        _md_number(float(row["local_assigned_m3"])),
                        _md_number(float(row["cross_border_assigned_m3"])),
                        _md_number(float(row["published_assigned_m3"])),
                        _md_number(float(row["proxy_assigned_m3"])),
                        _md_number(float(row["unassigned_m3"])),
                    ]
                    for row in results[scenario].province_summary
                ],
            ),
            "",
        ]

    class_matrix: dict[str, dict[str, float]] = defaultdict(
        lambda: {scenario: 0.0 for scenario in SCENARIOS}
    )
    for row in summary["province_class_totals"]:
        class_matrix[row["facility_class"]][row["scenario"]] += float(
            row["assigned_m3"]
        )
    lines += [
        "## Class totals",
        "",
        (
            "Recipient facility classes, not feedstock grades. The 119 Mm3 offer "
            "is ungraded; no sawlog/pulp/panel/pellet/biomass split is inferred."
        ),
        "",
        *_md_table(
            ["Facility class", *[f"`{scenario}` m3" for scenario in SCENARIOS]],
            [
                [
                    facility_class,
                    *[
                        _md_number(class_matrix[facility_class][scenario])
                        for scenario in SCENARIOS
                    ],
                ]
                for facility_class in RELEVANT_CLASSES
                if any(class_matrix[facility_class].values())
            ],
        ),
        "",
        "## Concentration and overflow",
        "",
        *_md_table(
            [
                "Scenario",
                "Facilities used",
                "Top-1 share",
                "Top-5 share",
                "HHI",
                "Unassigned provinces",
            ],
            [
                [
                    scenario,
                    metrics[scenario]["facilities_used"],
                    f"{metrics[scenario]['max_facility_share']:.2%}",
                    f"{metrics[scenario]['top5_share']:.2%}",
                    f"{metrics[scenario]['hhi']:.4f}",
                    metrics[scenario]["unassigned_provinces"],
                ]
                for scenario in SCENARIOS
            ],
        ),
        "",
        (
            "HHI is the sum of squared facility shares of assigned volume "
            "(0-1 scale). No single facility can absorb unbounded volume because "
            "every facility has a finite capacity."
        ),
        "",
    ]

    unassigned_rows = [
        row for scenario in SCENARIOS for row in results[scenario].unassigned
    ]
    if unassigned_rows:
        lines += [
            "### Unassigned and overflow",
            "",
            *_md_table(
                ["Scenario", "Province", "Unassigned m3", "Share of offer"],
                [
                    [
                        row["scenario"],
                        row["province"],
                        _md_number(float(row["unassigned_m3"])),
                        f"{100.0 * float(row['unassigned_share']):.1f}%",
                    ]
                    for row in unassigned_rows
                ],
            ),
            "",
        ]
    else:
        lines += [
            "### Unassigned and overflow",
            "",
            "No unassigned volume in either scenario.",
            "",
        ]

    lines += [
        (
            "Overflow is demand that no eligible located facility could take "
            "under the scenario's capacity rule. It is not a statement that the "
            "province lacks physical wood or legal harvest rights."
        ),
        "",
        "## Largest assignments in `published_plus_proxy`",
        "",
        *_md_table(
            [
                "Facility",
                "Province",
                "Class",
                "Capacity status",
                "Capacity m3",
                "Assigned m3",
                "Utilization",
            ],
            [
                [
                    row["facility_id"],
                    row["facility_province"],
                    row["facility_class"],
                    row["capacity_status"],
                    _md_number(float(row["capacity_m3_y"])),
                    _md_number(float(row["assigned_m3"])),
                    f"{100.0 * float(row['utilization']):.1f}%",
                ]
                for row in sorted(
                    results["published_plus_proxy"].assignments,
                    key=lambda item: -float(item["assigned_m3"]),
                )[:10]
            ],
        ),
        "",
        "## Caveats",
        "",
    ]
    lines.extend(f"- {caveat}" for caveat in summary["caveats"])
    lines += [
        "",
        "## Verification",
        "",
        "```bash",
        (
            ".venv/bin/python -m "
            "workflows.national.facilities.national_facility_assignment_v2"
        ),
        ".venv/bin/pytest tests/test_national_facility_assignment_v2.py",
        (
            ".venv/bin/ruff check "
            "workflows/national/facilities/national_facility_assignment_v2.py "
            "tests/test_national_facility_assignment_v2.py"
        ),
        "```",
        "",
    ]
    return "\n".join(lines)


def _write_manifest(output_dir: Path, summary: dict[str, Any]) -> None:
    outputs: dict[str, Any] = {}
    for path in sorted(output_dir.iterdir()):
        if not path.is_file() or path.name == "manifest.json":
            continue
        outputs[path.name] = {
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }
    manifest = {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": summary["generated_utc"],
        "labels": summary["labels"],
        "inputs": summary["inputs"],
        "outputs": outputs,
        "capacity": summary["capacity"],
        "scenarios": summary["scenarios"],
        "comparison": summary["comparison"],
        "concentration": summary["concentration"],
        "validation": summary["validation"],
        "reproduce": summary["reproduce"],
    }
    _write_json(output_dir / "manifest.json", manifest)


def build(
    output_dir: Path = OUTPUT_DIR,
    capacity_path: Path = CAPACITY_CSV,
    inventory_path: Path = INVENTORY_CSV,
    harvest_path: Path = HARVEST_CSV,
    boundary_path: Path = BOUNDARY_GEOJSON,
    config: AllocationConfig | None = None,
) -> dict[str, Any]:
    """Build the v2 assignment artifact and return its summary."""

    config = config or AllocationConfig(
        road_factor=ROAD_FACTOR,
        cross_border_penalty_km=CROSS_BORDER_PENALTY_KM,
    )
    generated_utc = _utc_now()
    facilities = read_facilities(capacity_path, inventory_path)
    offers, centroid_methods = read_offers(harvest_path, boundary_path)

    results = {
        scenario: allocate_scenario(offers, facilities, scenario, config)
        for scenario in SCENARIOS
    }
    inputs = {
        str(path.relative_to(REPO_ROOT)): _sha256(path)
        for path in (
            capacity_path,
            inventory_path,
            harvest_path,
            boundary_path,
        )
    }
    summary = _summary(
        generated_utc=generated_utc,
        facilities=facilities,
        offers=offers,
        centroid_methods=centroid_methods,
        results=results,
        inputs=inputs,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(
        output_dir / "facility_assignment.csv",
        ASSIGNMENT_FIELDS,
        [
            row
            for scenario in SCENARIOS
            for row in results[scenario].assignments
        ],
    )
    _write_csv(
        output_dir / "province_summary.csv",
        PROVINCE_SUMMARY_FIELDS,
        [
            row
            for scenario in SCENARIOS
            for row in results[scenario].province_summary
        ],
    )
    _write_csv(
        output_dir / "facility_utilization.csv",
        UTILIZATION_FIELDS,
        [
            row
            for scenario in SCENARIOS
            for row in results[scenario].facility_utilization
        ],
    )
    _write_csv(
        output_dir / "unassigned.csv",
        UNASSIGNED_FIELDS,
        [
            row
            for scenario in SCENARIOS
            for row in results[scenario].unassigned
        ],
    )
    _write_csv(
        output_dir / "province_class_totals.csv",
        PROVINCE_CLASS_FIELDS,
        summary["province_class_totals"],
    )
    _write_csv(
        output_dir / "concentration.csv",
        CONCENTRATION_FIELDS,
        [
            row
            for scenario in SCENARIOS
            for row in results[scenario].concentration
        ],
    )
    _write_csv(
        output_dir / "scenario_comparison.csv",
        COMPARISON_FIELDS,
        [
            {
                field: summary["scenarios"][scenario][field]
                for field in COMPARISON_FIELDS
            }
            for scenario in SCENARIOS
        ],
    )
    (output_dir / "NATIONAL_FACILITY_ASSIGNMENT_V2.md").write_text(
        _report(summary, results), encoding="utf-8"
    )
    _write_json(output_dir / "summary.json", summary)
    _write_manifest(output_dir, summary)
    return summary


def main() -> None:
    summary = build()
    print(
        json.dumps(
            {
                "artifact": summary["artifact"],
                "offer_m3": summary["scenarios"]["published_only"]["offer_m3"],
                "scenarios": {
                    scenario: {
                        "assigned_m3": summary["scenarios"][scenario][
                            "assigned_m3"
                        ],
                        "unassigned_m3": summary["scenarios"][scenario][
                            "unassigned_m3"
                        ],
                        "published_m3": summary["scenarios"][scenario][
                            "published_m3"
                        ],
                        "proxy_m3": summary["scenarios"][scenario]["proxy_m3"],
                        "max_facility_share": summary["scenarios"][scenario][
                            "max_facility_share"
                        ],
                    }
                    for scenario in SCENARIOS
                },
                "output_dir": str(OUTPUT_DIR.relative_to(REPO_ROOT)),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
