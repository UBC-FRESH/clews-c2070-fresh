"""Assign the 119 Mm3 harvest case with a provincial capacity floor.

Assignment v2 used finite published plus class-median proxy capacity, but in
four provinces total proxy capacity was below the provincial harvest.  The
shortfall was met by cross-border assignment.  This v3 adds a documented
screening floor: for each province, total finite capacity must be at least
``provincial harvest x 1.2``.  Where it is not, **proxy capacities only** are
scaled proportionally and labelled as such.  Published capacity is never
altered.

Scenarios
---------
``published_only``
    Only published-capacity facilities are eligible (unchanged v2 run).

``published_plus_proxy``
    Published plus unscaled class-median proxy capacity (unchanged v2 run).

``province_capacity_floor``
    Same as ``published_plus_proxy``, except proxy capacities in a province
    below its 1.2x harvest floor are scaled by one documented factor.  Scaled
    records carry ``capacity_status=proxy_harvest_scaled`` and
    ``capacity_evidence=class_median_proxy_scaled_to_harvest_floor``.

Allocation is local-first with the v2 cross-border penalty and competition
rule.  Every facility capacity remains finite; there is no unbounded sink.

Labels: ``model_estimate`` / ``observed=false`` / ``policy_grade=false``.
No legal or AAC claim is made.  No UI or public OSeCAN surface is changed.
"""

from __future__ import annotations

import json
import math
import subprocess
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from workflows.national.facilities import (
    national_facility_assignment_v2 as v2,
)
from workflows.national.facilities.national_facility_assignment import (
    AllocationConfig,
    Location,
    ProvinceOffer,
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
V2_SUMMARY_JSON = (
    REPO_ROOT
    / "data/derived/national/facilities/national_facility_assignment_v2"
    / "summary.json"
)
OUTPUT_DIR = (
    REPO_ROOT
    / "data/derived/national/facilities/national_facility_assignment_v3"
)

ARTIFACT = "national_facility_assignment_v3"
ARTIFACT_VERSION = "3.0.0"
SCENARIOS = (
    "published_only",
    "published_plus_proxy",
    "province_capacity_floor",
)
FLOOR_SCENARIO = "province_capacity_floor"
FLOOR_MULTIPLIER = 1.2
RELEVANT_CLASSES = v2.RELEVANT_CLASSES
CLASS_ORDER = v2.CLASS_ORDER

PUBLISHED = "published"
PROXY = "proxy"
SCALED_PROXY = "proxy_harvest_scaled"
NOT_PUBLISHED = "not_published"

PUBLISHED_EVIDENCE = "published_mills_db_2015_infeed_proxy"
PROXY_EVIDENCE = "class_median_proxy"
SCALED_PROXY_EVIDENCE = "class_median_proxy_scaled_to_harvest_floor"

EVIDENCE_LABEL = v2.EVIDENCE_LABEL
OBSERVED = v2.OBSERVED
POLICY_GRADE = v2.POLICY_GRADE

VOLUME_EPSILON_M3 = v2.VOLUME_EPSILON_M3
ROAD_FACTOR = v2.ROAD_FACTOR
CROSS_BORDER_PENALTY_KM = v2.CROSS_BORDER_PENALTY_KM

CAPACITY_STATUSES = {PUBLISHED, PROXY, SCALED_PROXY}

ASSIGNMENT_FIELDS = [
    *v2.ASSIGNMENT_FIELDS[:21],
    "capacity_evidence",
    "floor_scale_factor",
    *v2.ASSIGNMENT_FIELDS[21:],
]
PROVINCE_SUMMARY_FIELDS = v2.PROVINCE_SUMMARY_FIELDS
UTILIZATION_FIELDS = [
    *v2.UTILIZATION_FIELDS[:6],
    "capacity_evidence",
    "floor_scale_factor",
    *v2.UTILIZATION_FIELDS[6:],
]
UNASSIGNED_FIELDS = v2.UNASSIGNED_FIELDS
PROVINCE_CLASS_FIELDS = v2.PROVINCE_CLASS_FIELDS
CONCENTRATION_FIELDS = [
    *v2.CONCENTRATION_FIELDS[:7],
    "capacity_evidence",
    "floor_scale_factor",
    *v2.CONCENTRATION_FIELDS[7:],
]
COMPARISON_FIELDS = v2.COMPARISON_FIELDS
FLOOR_FIELDS = [
    "province",
    "harvest_m3",
    "floor_multiplier",
    "floor_capacity_m3_y",
    "published_capacity_m3_y",
    "proxy_capacity_before_m3_y",
    "total_capacity_before_m3_y",
    "proxy_scale_factor",
    "proxy_capacity_after_m3_y",
    "total_capacity_after_m3_y",
    "capacity_shortfall_m3_y",
    "capacity_floor_status",
    "proxy_records_total",
    "proxy_records_scaled",
    "published_records",
    "unlocated_capacity_m3_y",
    "evidence_label",
    "observed",
    "policy_grade",
]
FLOOR_FACILITY_FIELDS = [
    "facility_id",
    "name",
    "facility_class",
    "province",
    "lat",
    "lon",
    "capacity_status_before",
    "capacity_m3_y_before",
    "capacity_evidence_before",
    "capacity_status_after",
    "capacity_m3_y_after",
    "capacity_evidence_after",
    "proxy_low_m3_y_before",
    "proxy_high_m3_y_before",
    "proxy_low_m3_y_after",
    "proxy_high_m3_y_after",
    "floor_scale_factor",
    "province_floor_capacity_m3_y",
    "capacity_source_id",
    "capacity_source_url",
    "evidence_label",
    "observed",
    "policy_grade",
]
V2_V3_FIELDS = [
    "metric",
    "unit",
    "v2_value",
    "v3_value",
    "delta_v3_minus_v2",
    "v2_scenario",
    "v3_scenario",
    "note",
]


class AssignmentV3Error(v2.AssignmentError):
    """Raised when a floor or allocation invariant fails."""


@dataclass(frozen=True, slots=True)
class FloorCapacity:
    """A finite capacity after optional provincial floor scaling."""

    capacity_m3_y: float
    status: str
    capacity_evidence: str
    floor_scale_factor: float
    proxy_low_m3_y: float | None = None
    proxy_high_m3_y: float | None = None

    def __post_init__(self) -> None:
        value = float(self.capacity_m3_y)
        if not math.isfinite(value) or value <= 0.0:
            raise AssignmentV3Error("capacity must be finite and positive")
        if self.status not in CAPACITY_STATUSES:
            raise AssignmentV3Error(
                f"unexpected capacity status {self.status!r}"
            )
        factor = float(self.floor_scale_factor)
        if not math.isfinite(factor) or factor <= 0.0:
            raise AssignmentV3Error("floor scale factor must be positive")
        if self.status == PUBLISHED:
            if self.proxy_low_m3_y is not None or self.proxy_high_m3_y is not None:
                raise AssignmentV3Error(
                    "published capacity must not carry a proxy range"
                )
            if factor != 1.0:
                raise AssignmentV3Error("published capacity cannot be scaled")
        else:
            if self.proxy_low_m3_y is None or self.proxy_high_m3_y is None:
                raise AssignmentV3Error("proxy capacity requires a p25-p75 range")
            low = float(self.proxy_low_m3_y)
            high = float(self.proxy_high_m3_y)
            if not (math.isfinite(low) and math.isfinite(high)):
                raise AssignmentV3Error("proxy range must be finite")
            if not (0.0 < low <= value <= high):
                raise AssignmentV3Error(
                    "proxy capacity must lie inside its p25-p75 range"
                )
            object.__setattr__(self, "proxy_low_m3_y", low)
            object.__setattr__(self, "proxy_high_m3_y", high)
        object.__setattr__(self, "capacity_m3_y", value)
        object.__setattr__(self, "floor_scale_factor", factor)


@dataclass(frozen=True, slots=True)
class Facility:
    """A facility with a finite v3 capacity."""

    facility_id: str
    name: str
    facility_class: str
    province: str
    location: Location | None
    capacity: FloorCapacity | None
    operating_status: str
    source_id: str
    source_url: str
    capacity_source_id: str
    capacity_source_url: str
    evidence_tier: str

    def __post_init__(self) -> None:
        if not self.facility_id.strip():
            raise AssignmentV3Error("facility_id is required")
        if self.facility_class not in CLASS_ORDER:
            raise AssignmentV3Error(
                f"{self.facility_id}: unexpected facility_class "
                f"{self.facility_class!r}"
            )
        if self.province not in PROVINCES:
            raise AssignmentV3Error(
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
        return self.operating_status not in v2.NON_OPERATING_STATUSES


@dataclass(frozen=True, slots=True)
class FloorPlan:
    """Provincial floor arithmetic and the scaled facility universe."""

    facilities: tuple[Facility, ...]
    province_rows: tuple[dict[str, Any], ...]
    shortfall_before_m3_y: float
    shortfall_after_m3_y: float
    provinces_below_floor_before: int
    provinces_below_floor_after: int
    scaled_proxy_records: int
    scaled_provinces: tuple[str, ...]
    scale_by_province: dict[str, float]
    unlocated_capacity_in_scaled_provinces_m3_y: float


def _read_capacity_evidence(path: Path) -> dict[str, str]:
    rows = v2._read_csv(path)
    v2._require_columns(rows, {"facility_id", "capacity_evidence"}, path)
    evidence: dict[str, str] = {}
    for row in rows:
        facility_id = row["facility_id"].strip()
        if facility_id in evidence:
            raise AssignmentV3Error(f"{path}: duplicate facility_id {facility_id}")
        evidence[facility_id] = row["capacity_evidence"].strip()
    return evidence


def _base_facility(
    base: v2.Facility, capacity_evidence: str
) -> Facility:
    if base.capacity is None:
        capacity = None
    elif base.capacity.status == PUBLISHED:
        capacity = FloorCapacity(
            capacity_m3_y=base.capacity.capacity_m3_y,
            status=PUBLISHED,
            capacity_evidence=capacity_evidence,
            floor_scale_factor=1.0,
        )
    else:
        capacity = FloorCapacity(
            capacity_m3_y=base.capacity.capacity_m3_y,
            status=PROXY,
            capacity_evidence=capacity_evidence,
            floor_scale_factor=1.0,
            proxy_low_m3_y=base.capacity.proxy_low_m3_y,
            proxy_high_m3_y=base.capacity.proxy_high_m3_y,
        )
    return Facility(
        facility_id=base.facility_id,
        name=base.name,
        facility_class=base.facility_class,
        province=base.province,
        location=base.location,
        capacity=capacity,
        operating_status=base.operating_status,
        source_id=base.source_id,
        source_url=base.source_url,
        capacity_source_id=base.capacity_source_id,
        capacity_source_url=base.capacity_source_url,
        evidence_tier=base.evidence_tier,
    )


def _assert_no_unexpected_deletions() -> None:
    """Fail closed on any unexpected D in the parent or data status."""

    for root in (REPO_ROOT, REPO_ROOT / "data"):
        result = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise AssignmentV3Error(
                f"cannot read git status in {root}: {result.stderr.strip()}"
            )
        for line in result.stdout.splitlines():
            if len(line) >= 2 and (line[0] == "D" or line[1] == "D"):
                raise AssignmentV3Error(
                    f"unexpected deletion in {root}: {line!r}; halting"
                )


def read_facilities(
    capacity_path: Path = CAPACITY_CSV,
    inventory_path: Path = INVENTORY_CSV,
) -> tuple[Facility, ...]:
    """Read capacity v1 through the v2 validator and attach evidence labels."""

    base_facilities = v2.read_facilities(capacity_path, inventory_path)
    evidence = _read_capacity_evidence(capacity_path)
    missing = {
        facility.facility_id
        for facility in base_facilities
        if facility.capacity is not None
        and facility.facility_id not in evidence
    }
    if missing:
        raise AssignmentV3Error(
            f"{capacity_path}: missing capacity_evidence for {sorted(missing)[:5]}"
        )
    facilities = tuple(
        _base_facility(facility, evidence[facility.facility_id])
        for facility in base_facilities
    )
    for facility in facilities:
        if facility.capacity is None:
            continue
        expected = (
            PUBLISHED_EVIDENCE
            if facility.capacity.status == PUBLISHED
            else PROXY_EVIDENCE
        )
        if facility.capacity.capacity_evidence != expected:
            raise AssignmentV3Error(
                f"{facility.facility_id}: capacity evidence "
                f"{facility.capacity.capacity_evidence!r} does not match "
                f"{facility.capacity.status!r}"
            )
    return facilities


def _scaled_facility(facility: Facility, scale: float) -> Facility:
    capacity = facility.capacity
    if capacity is None or capacity.status == PUBLISHED:
        return facility
    if scale == 1.0:
        return facility
    return Facility(
        facility_id=facility.facility_id,
        name=facility.name,
        facility_class=facility.facility_class,
        province=facility.province,
        location=facility.location,
        capacity=FloorCapacity(
            capacity_m3_y=capacity.capacity_m3_y * scale,
            status=SCALED_PROXY,
            capacity_evidence=SCALED_PROXY_EVIDENCE,
            floor_scale_factor=scale,
            proxy_low_m3_y=capacity.proxy_low_m3_y * scale,
            proxy_high_m3_y=capacity.proxy_high_m3_y * scale,
        ),
        operating_status=facility.operating_status,
        source_id=facility.source_id,
        source_url=facility.source_url,
        capacity_source_id=facility.capacity_source_id,
        capacity_source_url=facility.capacity_source_url,
        evidence_tier=facility.evidence_tier,
    )


def build_floor_plan(
    facilities: Sequence[Facility],
    harvest_m3_by_province: dict[str, float],
    *,
    floor_multiplier: float = FLOOR_MULTIPLIER,
) -> FloorPlan:
    """Scale proxy capacities only so each province meets its harvest floor."""

    if not math.isfinite(floor_multiplier) or floor_multiplier <= 0.0:
        raise AssignmentV3Error("floor multiplier must be finite and positive")
    relevant = [
        facility
        for facility in facilities
        if facility.capacity is not None
        and facility.facility_class in RELEVANT_CLASSES
    ]
    by_province: dict[str, list[Facility]] = defaultdict(list)
    for facility in relevant:
        by_province[facility.province].append(facility)

    scale_by_province: dict[str, float] = {}
    rows: list[dict[str, Any]] = []
    shortfall_before = 0.0
    shortfall_after = 0.0
    below_before = 0
    below_after = 0
    scaled_provinces: list[str] = []
    scaled_records_total = 0
    unlocated_in_scaled = 0.0

    for province in PROVINCES:
        group = by_province.get(province, [])
        harvest = float(harvest_m3_by_province.get(province, 0.0))
        floor = harvest * floor_multiplier
        published = [
            facility
            for facility in group
            if facility.capacity is not None
            and facility.capacity.status == PUBLISHED
        ]
        proxies = [
            facility
            for facility in group
            if facility.capacity is not None
            and facility.capacity.status == PROXY
        ]
        published_capacity = sum(
            facility.capacity.capacity_m3_y for facility in published
        )
        proxy_capacity = sum(
            facility.capacity.capacity_m3_y for facility in proxies
        )
        total_before = published_capacity + proxy_capacity
        gap = max(0.0, floor - total_before)
        shortfall_before += gap
        if gap > VOLUME_EPSILON_M3:
            below_before += 1

        if gap <= VOLUME_EPSILON_M3:
            scale = 1.0
            status = "floor_met_no_scaling"
        elif proxy_capacity > VOLUME_EPSILON_M3:
            scale = (floor - published_capacity) / proxy_capacity
            status = "proxy_scaled_to_floor"
        else:
            scale = 1.0
            status = "shortfall_no_proxy_capacity"

        after_proxy_capacity = proxy_capacity * scale
        total_after = published_capacity + after_proxy_capacity
        gap_after = max(0.0, floor - total_after)
        shortfall_after += gap_after
        if gap_after > VOLUME_EPSILON_M3:
            below_after += 1
            if status == "proxy_scaled_to_floor":
                status = "shortfall_after_scaling"
        if not math.isfinite(scale) or scale <= 0.0:
            raise AssignmentV3Error(
                f"{province}: invalid floor scale factor {scale!r}"
            )

        scaled_records = (
            len(proxies) if status == "proxy_scaled_to_floor" else 0
        )
        scaled_records_total += scaled_records
        if scaled_records:
            scaled_provinces.append(province)
            unlocated_in_scaled += sum(
                facility.capacity.capacity_m3_y
                for facility in proxies
                if facility.location is None
            )
        scale_by_province[province] = scale
        rows.append(
            {
                "province": province,
                "harvest_m3": v2._format_float(harvest),
                "floor_multiplier": v2._format_float(floor_multiplier),
                "floor_capacity_m3_y": v2._format_float(floor),
                "published_capacity_m3_y": v2._format_float(
                    published_capacity
                ),
                "proxy_capacity_before_m3_y": v2._format_float(proxy_capacity),
                "total_capacity_before_m3_y": v2._format_float(total_before),
                "proxy_scale_factor": v2._format_float(scale, 12),
                "proxy_capacity_after_m3_y": v2._format_float(
                    after_proxy_capacity
                ),
                "total_capacity_after_m3_y": v2._format_float(total_after),
                "capacity_shortfall_m3_y": v2._format_float(gap_after),
                "capacity_floor_status": status,
                "proxy_records_total": len(proxies),
                "proxy_records_scaled": scaled_records,
                "published_records": len(published),
                "unlocated_capacity_m3_y": v2._format_float(
                    sum(
                        facility.capacity.capacity_m3_y
                        for facility in group
                        if facility.location is None
                    )
                ),
                "evidence_label": EVIDENCE_LABEL,
                "observed": v2._bool_text(OBSERVED),
                "policy_grade": v2._bool_text(POLICY_GRADE),
            }
        )

    scaled_facilities = tuple(
        _scaled_facility(
            facility, scale_by_province[facility.province]
        )
        for facility in facilities
    )
    return FloorPlan(
        facilities=scaled_facilities,
        province_rows=tuple(rows),
        shortfall_before_m3_y=shortfall_before,
        shortfall_after_m3_y=shortfall_after,
        provinces_below_floor_before=below_before,
        provinces_below_floor_after=below_after,
        scaled_proxy_records=scaled_records_total,
        scaled_provinces=tuple(scaled_provinces),
        scale_by_province=scale_by_province,
        unlocated_capacity_in_scaled_provinces_m3_y=unlocated_in_scaled,
    )


def _assignment_row(
    *,
    scenario: str,
    allocation_order: int,
    offer: ProvinceOffer,
    facility: Facility,
    candidate: v2.Candidate,
    assigned_m3: float,
    facility_assigned_m3: float,
    remaining_capacity_m3: float,
    phase: str,
) -> dict[str, Any]:
    """Build an assignment row at 9-decimal precision.

    Six decimals are enough for v2's small integer-like proxies, but scaled
    proxies produce many small takes whose six-decimal rounding can sum to a
    few micro-m3 above the offer.  Nine decimals keeps the conservation check
    well inside its tolerance without changing allocation behaviour.
    """

    if facility.capacity is None:
        raise AssignmentV3Error(
            f"{facility.facility_id}: assigned without a finite capacity"
        )
    capacity = facility.capacity
    utilization = facility_assigned_m3 / capacity.capacity_m3_y
    return {
        "scenario": scenario,
        "allocation_order": allocation_order,
        "province": offer.province,
        "province_offer_m3": v2._format_float(offer.volume_m3, 9),
        "facility_id": facility.facility_id,
        "facility_name": facility.name,
        "facility_class": facility.facility_class,
        "facility_province": facility.province,
        "cross_border": v2._bool_text(candidate.cross_border),
        "allocation_phase": phase,
        "capacity_status": capacity.status,
        "capacity_m3_y": v2._format_float(capacity.capacity_m3_y, 9),
        "proxy_low_m3_y": v2._format_float(capacity.proxy_low_m3_y, 9),
        "proxy_high_m3_y": v2._format_float(capacity.proxy_high_m3_y, 9),
        "assigned_m3": v2._format_float(assigned_m3, 12),
        "remaining_capacity_m3": v2._format_float(
            remaining_capacity_m3, 12
        ),
        "utilization": v2._format_float(utilization, 9),
        "distance_km": v2._format_float(candidate.distance_km, 9),
        "road_distance_km": v2._format_float(
            candidate.road_distance_km, 9
        ),
        "cross_border_penalty_km": v2._format_float(candidate.penalty_km, 9),
        "rank_distance_km": v2._format_float(candidate.rank_distance_km, 9),
        "source_id": facility.source_id,
        "source_url": facility.source_url,
        "capacity_source_id": facility.capacity_source_id,
        "capacity_source_url": facility.capacity_source_url,
        "evidence_tier": facility.evidence_tier,
        "evidence_label": EVIDENCE_LABEL,
        "observed": v2._bool_text(OBSERVED),
        "policy_grade": v2._bool_text(POLICY_GRADE),
    }


def _attach_capacity_labels(
    rows: Sequence[dict[str, Any]],
    facilities: Sequence[Facility],
) -> list[dict[str, Any]]:
    by_id = {facility.facility_id: facility for facility in facilities}
    labelled: list[dict[str, Any]] = []
    for row in rows:
        facility = by_id[row["facility_id"]]
        capacity = facility.capacity
        if capacity is None:
            raise AssignmentV3Error(
                f"{facility.facility_id}: assigned without capacity"
            )
        enriched = dict(row)
        enriched["capacity_evidence"] = capacity.capacity_evidence
        enriched["floor_scale_factor"] = v2._format_float(
            capacity.floor_scale_factor, 12
        )
        labelled.append(enriched)
    return labelled


def allocate_scenario(
    offers: Sequence[ProvinceOffer],
    facilities: Sequence[Facility],
    scenario: str,
    config: AllocationConfig | None = None,
) -> v2.ScenarioResult:
    """Run one v3 scenario with v2's local-first bounded allocation rule."""

    if scenario not in SCENARIOS:
        raise AssignmentV3Error(f"unknown scenario {scenario!r}")
    config = config or AllocationConfig()
    offers = tuple(offers)
    facilities = tuple(facilities)
    if not offers:
        raise AssignmentV3Error("at least one province offer is required")
    if len({offer.province for offer in offers}) != len(offers):
        raise AssignmentV3Error("province offers must be unique by province")
    if len({facility.facility_id for facility in facilities}) != len(facilities):
        raise AssignmentV3Error("facility ids must be unique")

    def screenable(facility: Facility) -> bool:
        if facility.facility_class not in RELEVANT_CLASSES:
            return False
        if not facility.is_operating:
            return False
        if facility.location is None:
            return False
        if facility.capacity is None:
            return False
        return not (
            scenario == "published_only"
            and facility.capacity.status != PUBLISHED
        )

    eligible = [facility for facility in facilities if screenable(facility)]
    remaining = {
        facility.facility_id: float(facility.capacity_m3_y or 0.0)
        for facility in eligible
    }
    facility_assigned = {facility.facility_id: 0.0 for facility in eligible}
    assignments: list[dict[str, Any]] = []
    allocation_order = 0
    candidate_cache: dict[tuple[str, str], v2.Candidate] = {}

    def candidate_for(offer: ProvinceOffer, facility: Facility) -> v2.Candidate:
        key = (offer.province, facility.facility_id)
        cached = candidate_cache.get(key)
        if cached is None:
            cached = v2._candidate(offer, facility, config)
            candidate_cache[key] = cached
        return cached

    def record(
        offer: ProvinceOffer,
        facility: Facility,
        take: float,
        candidate: v2.Candidate,
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
            raise AssignmentV3Error(
                f"{scenario}:{province}: negative residual {volume}"
            )
        residual[province] = max(0.0, volume)

    assignments = _attach_capacity_labels(assignments, facilities)
    province_summary = v2._build_province_summary(
        scenario, offers, assignments
    )
    province_locations = {offer.province: offer.location for offer in offers}
    utilization = v2._build_facility_utilization(
        scenario, facilities, facility_assigned, province_locations
    )
    utilization = _attach_capacity_labels(utilization, facilities)
    unassigned = v2._build_unassigned_rows(province_summary)
    concentration = _attach_capacity_labels(
        v2._build_concentration(scenario, assignments), facilities
    )
    province_class = v2._build_province_class(scenario, assignments)
    metrics = v2._build_metrics(
        scenario, offers, assignments, province_summary, unassigned
    )
    v2._validate_scenario(
        scenario,
        offers,
        assignments,
        province_summary,
        unassigned,
        utilization,
    )
    for row in assignments:
        if row["capacity_status"] not in CAPACITY_STATUSES:
            raise AssignmentV3Error(
                f"{scenario}:{row['facility_id']}: unexpected capacity status"
            )
    return v2.ScenarioResult(
        scenario=scenario,
        assignments=tuple(assignments),
        province_summary=tuple(province_summary),
        facility_utilization=tuple(utilization),
        unassigned=tuple(unassigned),
        concentration=tuple(concentration),
        province_class=tuple(province_class),
        metrics=metrics,
    )


def read_v2_metrics(path: Path = V2_SUMMARY_JSON) -> dict[str, Any]:
    """Read the v2 baseline metrics used for the v2-vs-v3 comparison."""

    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError as error:
        raise AssignmentV3Error(f"v2 summary is missing: {path}") from error
    metrics = payload.get("scenarios", {}).get("published_plus_proxy")
    if not isinstance(metrics, dict):
        raise AssignmentV3Error(
            f"{path}: no published_plus_proxy metrics"
        )
    return metrics


def _floor_facility_rows(
    facilities: Sequence[Facility],
    floor_plan: FloorPlan,
) -> list[dict[str, Any]]:
    floor_by_province = {
        row["province"]: row for row in floor_plan.province_rows
    }
    rows: list[dict[str, Any]] = []
    for facility in sorted(facilities, key=lambda item: item.facility_id):
        capacity = facility.capacity
        if facility.facility_class not in RELEVANT_CLASSES:
            continue
        if capacity is None:
            raise AssignmentV3Error(
                f"{facility.facility_id}: relevant record without capacity"
            )
        floor_row = floor_by_province[facility.province]
        scaled = capacity.status == SCALED_PROXY
        before_status = PROXY if scaled else capacity.status
        before_capacity = (
            capacity.capacity_m3_y / capacity.floor_scale_factor
            if scaled
            else capacity.capacity_m3_y
        )
        before_low = (
            capacity.proxy_low_m3_y / capacity.floor_scale_factor
            if scaled and capacity.proxy_low_m3_y is not None
            else capacity.proxy_low_m3_y
        )
        before_high = (
            capacity.proxy_high_m3_y / capacity.floor_scale_factor
            if scaled and capacity.proxy_high_m3_y is not None
            else capacity.proxy_high_m3_y
        )
        rows.append(
            {
                "facility_id": facility.facility_id,
                "name": facility.name,
                "facility_class": facility.facility_class,
                "province": facility.province,
                "lat": v2._format_float(facility.location.lat)
                if facility.location
                else "",
                "lon": v2._format_float(facility.location.lon)
                if facility.location
                else "",
                "capacity_status_before": before_status,
                "capacity_m3_y_before": v2._format_float(before_capacity),
                "capacity_evidence_before": (
                    PROXY_EVIDENCE if scaled else capacity.capacity_evidence
                ),
                "capacity_status_after": capacity.status,
                "capacity_m3_y_after": v2._format_float(capacity.capacity_m3_y),
                "capacity_evidence_after": capacity.capacity_evidence,
                "proxy_low_m3_y_before": v2._format_float(before_low),
                "proxy_high_m3_y_before": v2._format_float(before_high),
                "proxy_low_m3_y_after": v2._format_float(
                    capacity.proxy_low_m3_y
                ),
                "proxy_high_m3_y_after": v2._format_float(
                    capacity.proxy_high_m3_y
                ),
                "floor_scale_factor": v2._format_float(
                    capacity.floor_scale_factor, 12
                ),
                "province_floor_capacity_m3_y": floor_row["floor_capacity_m3_y"],
                "capacity_source_id": facility.capacity_source_id,
                "capacity_source_url": facility.capacity_source_url,
                "evidence_label": EVIDENCE_LABEL,
                "observed": v2._bool_text(OBSERVED),
                "policy_grade": v2._bool_text(POLICY_GRADE),
            }
        )
    return rows


def _v2_v3_rows(
    v2_metrics: dict[str, Any],
    v3_metrics: dict[str, Any],
    floor_plan: FloorPlan,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def add(
        metric: str,
        unit: str,
        v2_value: float | None,
        v3_value: float | None,
        note: str,
    ) -> None:
        if v2_value is None or v3_value is None:
            delta: float | None = None
        else:
            delta = float(v3_value) - float(v2_value)
        rows.append(
            {
                "metric": metric,
                "unit": unit,
                "v2_value": v2._format_float(
                    float(v2_value) if v2_value is not None else None
                ),
                "v3_value": v2._format_float(
                    float(v3_value) if v3_value is not None else None
                ),
                "delta_v3_minus_v2": v2._format_float(delta),
                "v2_scenario": "published_plus_proxy",
                "v3_scenario": FLOOR_SCENARIO,
                "note": note,
            }
        )

    for metric, unit, note in (
        ("assigned_m3", "m3/yr", "volume assigned to facilities"),
        ("unassigned_m3", "m3/yr", "provincial offer left unassigned"),
        ("assigned_share", "share", "assigned / offer"),
        ("published_m3", "m3/yr", "assigned volume on published capacity"),
        ("proxy_m3", "m3/yr", "assigned volume on proxy capacity"),
        (
            "proxy_share_of_assigned",
            "share",
            "proxy volume / assigned volume",
        ),
        ("cross_border_m3", "m3/yr", "assigned to an out-of-province facility"),
        ("cross_border_share", "share", "cross-border / assigned"),
        ("facilities_used", "count", "distinct facilities receiving volume"),
        ("max_facility_m3", "m3/yr", "largest single-facility assignment"),
        ("max_facility_share", "share", "largest / assigned"),
        ("top5_share", "share", "top five facilities / assigned"),
        ("hhi", "index_0_1", "sum of squared facility shares"),
    ):
        add(
            metric,
            unit,
            v2_metrics.get(metric),
            v3_metrics.get(metric),
            note,
        )
    add(
        "province_capacity_shortfall_before_m3_y",
        "m3/yr",
        floor_plan.shortfall_before_m3_y,
        floor_plan.shortfall_before_m3_y,
        "shortfall against the 1.2x harvest floor before scaling",
    )
    add(
        "province_capacity_shortfall_after_m3_y",
        "m3/yr",
        floor_plan.shortfall_before_m3_y,
        floor_plan.shortfall_after_m3_y,
        "v2 has no scaling, so its shortfall remains; v3 reports the residual",
    )
    add(
        "provinces_below_floor_before",
        "count",
        floor_plan.provinces_below_floor_before,
        floor_plan.provinces_below_floor_before,
        "provinces below the 1.2x floor before scaling",
    )
    add(
        "provinces_below_floor_after",
        "count",
        floor_plan.provinces_below_floor_before,
        floor_plan.provinces_below_floor_after,
        "v2 has no scaling; v3 reports the residual",
    )
    add(
        "scaled_proxy_records",
        "count",
        0,
        floor_plan.scaled_proxy_records,
        "proxy records carrying the scaled label",
    )
    return rows


def _summary(
    *,
    generated_utc: str,
    base_facilities: Sequence[Facility],
    floor_plan: FloorPlan,
    offers: Sequence[ProvinceOffer],
    centroid_methods: dict[str, str],
    results: dict[str, v2.ScenarioResult],
    v2_metrics: dict[str, Any],
    v2_reproduction: dict[str, float],
    inputs: dict[str, str],
) -> dict[str, Any]:
    capacity = v2._capacity_basis(base_facilities)
    metrics = {scenario: results[scenario].metrics for scenario in SCENARIOS}
    for scenario_metrics in metrics.values():
        if -0.01 < scenario_metrics["unassigned_m3"] <= 0.0:
            scenario_metrics["unassigned_m3"] = 0.0
        if scenario_metrics["assigned_share"] is not None:
            scenario_metrics["assigned_share"] = min(
                1.0, scenario_metrics["assigned_share"]
            )
    floor_metrics = metrics[FLOOR_SCENARIO]
    comparison = {
        "v2_baseline_scenario": "published_plus_proxy",
        "v3_scenario": FLOOR_SCENARIO,
        "assigned_delta_m3": (
            floor_metrics["assigned_m3"] - v2_metrics["assigned_m3"]
        ),
        "unassigned_delta_m3": (
            floor_metrics["unassigned_m3"] - v2_metrics["unassigned_m3"]
        ),
        "cross_border_delta_m3": (
            floor_metrics["cross_border_m3"] - v2_metrics["cross_border_m3"]
        ),
        "cross_border_share_delta": (
            (floor_metrics["cross_border_share"] or 0.0)
            - (v2_metrics["cross_border_share"] or 0.0)
        ),
        "proxy_share_delta": (
            (floor_metrics["proxy_share_of_assigned"] or 0.0)
            - (v2_metrics["proxy_share_of_assigned"] or 0.0)
        ),
        "max_facility_share_delta": (
            floor_metrics["max_facility_share"]
            - v2_metrics["max_facility_share"]
        ),
        "hhi_delta": floor_metrics["hhi"] - v2_metrics["hhi"],
        "assigned_delta_m3_vs_v3_reproduction": v2_reproduction["assigned_m3"],
    }
    validation = {
        "conservation_gap_m3": {},
        "known_capacity_violations": 0,
        "max_utilization": {},
        "unbounded_assignments": 0,
        "proxy_assignments_in_published_only": 0,
        "max_single_facility_share": {},
        "proxy_range_violations": 0,
        "published_capacity_unchanged": True,
        "scaled_proxy_records": floor_plan.scaled_proxy_records,
        "scaled_provinces": list(floor_plan.scaled_provinces),
        "floor_shortfall_before_m3_y": floor_plan.shortfall_before_m3_y,
        "floor_shortfall_after_m3_y": floor_plan.shortfall_after_m3_y,
        "provinces_below_floor_before": floor_plan.provinces_below_floor_before,
        "provinces_below_floor_after": floor_plan.provinces_below_floor_after,
        "floor_arithmetic_pass": True,
        "floor_cross_border_m3": floor_metrics["cross_border_m3"],
        "floor_provinces_self_sufficient": True,
        "unlocated_capacity_in_scaled_provinces_m3_y": (
            floor_plan.unlocated_capacity_in_scaled_provinces_m3_y
        ),
        "v2_reproduction": v2_reproduction,
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
            if row["capacity_status"] in CAPACITY_STATUSES
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
                if row["capacity_status"] != PUBLISHED
            )
        validation["proxy_range_violations"] += sum(
            1
            for row in result.assignments
            if row["capacity_status"] in {PROXY, SCALED_PROXY}
            and not (
                float(row["proxy_low_m3_y"])
                <= float(row["capacity_m3_y"])
                <= float(row["proxy_high_m3_y"])
            )
        )

    published_base = float(capacity["published_capacity_m3_y"])
    published_floor = sum(
        facility.capacity.capacity_m3_y
        for facility in floor_plan.facilities
        if facility.capacity is not None
        and facility.capacity.status == PUBLISHED
    )
    if abs(published_base - published_floor) > 1e-6:
        validation["published_capacity_unchanged"] = False
        raise AssignmentV3Error(
            "floor scaling altered published capacity: "
            f"{published_base} != {published_floor}"
        )
    floor_summary = {
        row["province"]: row
        for row in results[FLOOR_SCENARIO].province_summary
    }
    unexpected_cross_border = 0.0
    for row in floor_plan.province_rows:
        floor = float(row["floor_capacity_m3_y"])
        after = float(row["total_capacity_after_m3_y"])
        shortfall = float(row["capacity_shortfall_m3_y"])
        if abs(max(0.0, floor - after) - shortfall) > 0.01:
            validation["floor_arithmetic_pass"] = False
            raise AssignmentV3Error(
                f"{row['province']}: floor shortfall arithmetic failed"
            )
        offer = next(
            offer for offer in offers if offer.province == row["province"]
        )
        if after + VOLUME_EPSILON_M3 < offer.volume_m3:
            validation["floor_provinces_self_sufficient"] = False
        else:
            cross = float(
                floor_summary[row["province"]]["cross_border_assigned_m3"]
            )
            unexpected_cross_border += cross
    validation["floor_all_provinces_meet_floor"] = (
        floor_plan.provinces_below_floor_after == 0
    )
    validation["floor_cross_border_unexpected_m3_y"] = (
        unexpected_cross_border
    )
    if unexpected_cross_border > 0.01:
        raise AssignmentV3Error(
            "floor scenario has cross-border volume from a province whose "
            f"capacity meets its offer: {unexpected_cross_border:,.3f} m3"
        )
    validation["v2_reproduction_pass"] = all(
        abs(value) <= 1e-6 for value in v2_reproduction.values()
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
        "caveats": _caveats(capacity, floor_plan),
        "method": {
            "harvest_case": "119m",
            "harvest_file": str(HARVEST_CSV.relative_to(REPO_ROOT)),
            "capacity_layer": str(CAPACITY_CSV.relative_to(REPO_ROOT)),
            "v2_baseline": str(V2_SUMMARY_JSON.relative_to(REPO_ROOT)),
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
            "centroid_source": str(
                BOUNDARY_GEOJSON.relative_to(REPO_ROOT)
            ),
            "centroid_methods": centroid_methods,
            "province_order": list(PROVINCES),
            "non_operating_statuses_excluded": sorted(
                v2.NON_OPERATING_STATUSES
            ),
            "unlocated_capacity": (
                "excluded from the spatial screen and reported separately"
            ),
            "province_capacity_floor": {
                "multiplier": FLOOR_MULTIPLIER,
                "basis": (
                    "published + proxy capacity over relevant-class finite "
                    "records in the province, including unlocated records"
                ),
                "rule": (
                    "if total capacity < harvest x 1.2, scale every proxy "
                    "capacity in the province by one factor; published "
                    "capacity is never altered"
                ),
                "proxy_low_high_scaled": True,
                "shortfall_if_no_proxy": (
                    "leave capacity unchanged and report the residual "
                    "shortfall rather than fabricating capacity"
                ),
                "scaled_status": SCALED_PROXY,
                "scaled_evidence": SCALED_PROXY_EVIDENCE,
            },
            "scenarios": {
                "published_only": (
                    "only published-capacity facilities are eligible"
                ),
                "published_plus_proxy": (
                    "published plus unscaled class-median proxy facilities; "
                    "reproduces assignment v2 scenario B"
                ),
                "province_capacity_floor": (
                    "published plus proxy capacity after provincial 1.2x "
                    "harvest-floor scaling of proxies only"
                ),
            },
            "no_unbounded_facility": (
                "v3 contains no unbounded facility and no facility can absorb "
                "unlimited volume"
            ),
        },
        "inputs": inputs,
        "capacity": capacity,
        "province_capacity_floor": {
            "multiplier": FLOOR_MULTIPLIER,
            "province_rows": list(floor_plan.province_rows),
            "shortfall_before_m3_y": floor_plan.shortfall_before_m3_y,
            "shortfall_after_m3_y": floor_plan.shortfall_after_m3_y,
            "provinces_below_floor_before": (
                floor_plan.provinces_below_floor_before
            ),
            "provinces_below_floor_after": (
                floor_plan.provinces_below_floor_after
            ),
            "scaled_proxy_records": floor_plan.scaled_proxy_records,
            "scaled_provinces": list(floor_plan.scaled_provinces),
            "scale_by_province": floor_plan.scale_by_province,
            "unlocated_capacity_in_scaled_provinces_m3_y": (
                floor_plan.unlocated_capacity_in_scaled_provinces_m3_y
            ),
        },
        "v2_baseline": v2_metrics,
        "v2_v3_comparison": _v2_v3_rows(
            v2_metrics, floor_metrics, floor_plan
        ),
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
            "capacity_floor_csv": "capacity_floor.csv",
            "capacity_floor_facilities_csv": "capacity_floor_facilities.csv",
            "v2_v3_comparison_csv": "v2_v3_comparison.csv",
            "report_md": "NATIONAL_FACILITY_ASSIGNMENT_V3.md",
        },
        "reproduce": (
            ".venv/bin/python -m "
            "workflows.national.facilities.national_facility_assignment_v3"
        ),
    }


def _caveats(
    capacity: dict[str, Any], floor_plan: FloorPlan
) -> list[str]:
    caveats = list(v2._caveats(capacity))
    caveats[-1] = "No UI or public OSeCAN surface is changed by this artifact."
    caveats = [
        caveat.replace(
            "No unbounded facility exists in v2:",
            "No unbounded facility exists in v3:",
        ).replace(
            "Scenario B is not",
            "The unscaled published-plus-proxy scenario is not",
        )
        for caveat in caveats
    ]
    caveats.extend(
        [
            (
                f"The provincial capacity floor is a documented screening "
                f"assumption of {FLOOR_MULTIPLIER:.1f}x harvest, not an observed "
                "capacity requirement, a legal AAC, or evidence that a "
                "province can physically or economically process that volume."
            ),
            (
                f"Floor scaling changes proxy capacities only. In v3, "
                f"{floor_plan.scaled_proxy_records} proxy records across "
                f"{len(floor_plan.scaled_provinces)} provinces "
                f"({', '.join(floor_plan.scaled_provinces)}) are multiplied by "
                "one province-wide factor. Every published capacity is "
                "unchanged."
            ),
            (
                "Scaled proxies carry "
                f"`capacity_status={SCALED_PROXY}` and "
                f"`capacity_evidence={SCALED_PROXY_EVIDENCE}`. They are still "
                "class-median placeholders, not facility measurements; the "
                "scale factor is a planning adjustment, not evidence."
            ),
            (
                "The floor is tested on relevant-class finite capacity, "
                "including any unlocated record. No scaled province has "
                "unlocated proxy capacity, so its assignable capacity also "
                "meets the floor. BC meets the floor on either basis."
            ),
            (
                "Every province with eligible facility capacity meets the "
                "1.2x floor after scaling. NT has no eligible facility "
                f"capacity and remains {floor_plan.shortfall_after_m3_y:,.0f} "
                "m3/yr below its floor, so its offer crosses to the nearest "
                "remaining facility. Cross-border volume therefore collapses "
                "from the v2 level to this small residual. That is an "
                "artefact of the floor assumption and local-first ordering, "
                "not evidence of provincial self-sufficiency or of zero "
                "interprovincial trade."
            ),
            (
                "The floor scenario assigns less published capacity than v2 "
                "because expanded local proxy capacity displaces cross-border "
                "published capacity under the local-first rule. It is not a "
                "published-capacity-maximizing run."
            ),
            (
                "Capacity remains a caveated screening input: published values "
                "are 2015 Mills DB/FFCM processing-line infeed proxies and "
                "proxies are BC class medians. Scaling does not make either "
                "current, verified, licensed, or facility-specific."
            ),
        ]
    )
    return caveats


def _md_table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> list[str]:
    return v2._md_table(headers, rows)


def _report(summary: dict[str, Any], results: dict[str, v2.ScenarioResult]) -> str:
    metrics = summary["scenarios"]
    floor = summary["province_capacity_floor"]
    comparison = {
        row["metric"]: row for row in summary["v2_v3_comparison"]
    }
    lines = [
        "# National Facility Assignment v3",
        "",
        f"Generated {summary['generated_utc']}.",
        "",
        (
            "**Labels:** `model_estimate`; `observed=false`; "
            "`policy_grade=false`."
        ),
        "",
        (
            "This artifact adds a provincial capacity-floor sensitivity to "
            "assignment v2. Where published plus proxy capacity is below "
            f"{FLOOR_MULTIPLIER:.1f}x the provincial harvest, proxy capacities "
            "are scaled proportionally and labelled. Published capacity is "
            "never altered. It is not a legal AAC, not an observed allocation, "
            "and not policy-grade. No UI or public OSeCAN surface is changed."
        ),
        "",
        "## Method",
        "",
        (
            "1. Reuse capacity v1 and the v2 local-first allocation rule: "
            "local capacity before cross-border, published before proxy, "
            "nearest road-adjusted facility first, 500 km cross-border penalty."
        ),
        (
            "2. For each province, compute total finite capacity as published "
            "plus proxy over relevant-class records."
        ),
        (
            f"3. Compute the floor as provincial harvest x {FLOOR_MULTIPLIER:.1f}."
        ),
        (
            "4. If total capacity is below the floor and proxy capacity exists, "
            "scale every proxy capacity, including its p25-p75 range, by "
            "`(floor - published) / proxy`; leave published capacity unchanged. "
            "If no proxy exists, leave capacity unchanged and report the "
            "shortfall."
        ),
        (
            "5. Rerun assignment with the floor capacities. There is no "
            "unbounded facility; every assignment is bounded by its own "
            "finite capacity."
        ),
        "",
        "## Floor scaling",
        "",
        *_md_table(
            [
                "Province",
                "Harvest m3",
                "Floor m3",
                "Published m3",
                "Proxy before m3",
                "Total before m3",
                "Scale",
                "Proxy after m3",
                "Total after m3",
                "Shortfall m3",
                "Status",
            ],
            [
                [
                    row["province"],
                    v2._md_number(float(row["harvest_m3"])),
                    v2._md_number(float(row["floor_capacity_m3_y"])),
                    v2._md_number(float(row["published_capacity_m3_y"])),
                    v2._md_number(float(row["proxy_capacity_before_m3_y"])),
                    v2._md_number(float(row["total_capacity_before_m3_y"])),
                    f"{float(row['proxy_scale_factor']):.4f}",
                    v2._md_number(float(row["proxy_capacity_after_m3_y"])),
                    v2._md_number(float(row["total_capacity_after_m3_y"])),
                    v2._md_number(float(row["capacity_shortfall_m3_y"])),
                    row["capacity_floor_status"],
                ]
                for row in summary["province_capacity_floor"]["province_rows"]
            ],
        ),
        "",
        (
            f"Scaled records: **{floor['scaled_proxy_records']}** proxies in "
            f"{', '.join(floor['scaled_provinces'])}. Floor shortfall before "
            f"scaling: **{floor['shortfall_before_m3_y']:,.0f} m3/yr** across "
            f"{floor['provinces_below_floor_before']} provinces. Residual after "
            f"scaling: **{floor['shortfall_after_m3_y']:,.0f} m3/yr** across "
            f"{floor['provinces_below_floor_after']} "
            f"{'province' if floor['provinces_below_floor_after'] == 1 else 'provinces'}."
        ),
        "",
        "## Assignment v2 vs v3",
        "",
        *_md_table(
            [
                "Metric",
                "v2 `published_plus_proxy`",
                "v3 `province_capacity_floor`",
                "Delta v3 - v2",
            ],
            [
                [
                    metric,
                    v2._md_number(float(row["v2_value"]))
                    if row["v2_value"]
                    else "",
                    v2._md_number(float(row["v3_value"]))
                    if row["v3_value"]
                    else "",
                    v2._md_number(float(row["delta_v3_minus_v2"]))
                    if row["delta_v3_minus_v2"]
                    else "",
                ]
                for metric, row in comparison.items()
                if row["unit"] not in {"share", "index_0_1"}
            ],
        ),
        "",
        *_md_table(
            ["Share / index", "v2", "v3", "Delta"],
            [
                [
                    metric,
                    f"{float(row['v2_value']):.4f}" if row["v2_value"] else "",
                    f"{float(row['v3_value']):.4f}" if row["v3_value"] else "",
                    (
                        f"{float(row['delta_v3_minus_v2']):.4f}"
                        if row["delta_v3_minus_v2"]
                        else ""
                    ),
                ]
                for metric, row in comparison.items()
                if row["unit"] in {"share", "index_0_1"}
            ],
        ),
        "",
        (
            f"v3 floor assigns {metrics[FLOOR_SCENARIO]['assigned_m3']:,.0f} m3 "
            f"({metrics[FLOOR_SCENARIO]['assigned_share']:.1%} of the offer) "
            f"with {metrics[FLOOR_SCENARIO]['unassigned_m3']:,.0f} m3 "
            "unassigned. v2 assigned "
            f"{summary['v2_baseline']['assigned_m3']:,.0f} m3 and left "
            f"{summary['v2_baseline']['unassigned_m3']:,.0f} m3 unassigned."
        ),
        "",
        "## Cross-border and proxy concentration",
        "",
        *_md_table(
            [
                "Scenario",
                "Assigned m3",
                "Cross-border m3",
                "Cross-border share",
                "Proxy m3",
                "Proxy share",
                "Max facility share",
                "HHI",
            ],
            [
                [
                    scenario,
                    v2._md_number(metrics[scenario]["assigned_m3"]),
                    v2._md_number(metrics[scenario]["cross_border_m3"]),
                    f"{metrics[scenario]['cross_border_share']:.1%}"
                    if metrics[scenario]["cross_border_share"] is not None
                    else "",
                    v2._md_number(metrics[scenario]["proxy_m3"]),
                    f"{metrics[scenario]['proxy_share_of_assigned']:.1%}"
                    if metrics[scenario]["proxy_share_of_assigned"] is not None
                    else "",
                    f"{metrics[scenario]['max_facility_share']:.2%}",
                    f"{metrics[scenario]['hhi']:.4f}",
                ]
                for scenario in SCENARIOS
            ],
        ),
        "",
        (
            "v2 cross-border volume was "
            f"{summary['v2_baseline']['cross_border_m3']:,.0f} m3 "
            f"({summary['v2_baseline']['cross_border_share']:.1%} of assigned). "
            "v3 floor cross-border volume is "
            f"{metrics[FLOOR_SCENARIO]['cross_border_m3']:,.0f} m3 "
            f"({metrics[FLOOR_SCENARIO]['cross_border_share']:.1%})."
        ),
        "",
        (
            "v3 proxy share rises because local proxy capacity replaces "
            "cross-border published capacity and because scaled proxies are "
            "larger. Max facility share and HHI describe the resulting "
            "facility mix; they are not welfare or market-power measures."
        ),
        "",
        "## Province totals in `province_capacity_floor`",
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
                    v2._md_number(float(row["offer_m3"])),
                    v2._md_number(float(row["assigned_m3"])),
                    v2._md_number(float(row["local_assigned_m3"])),
                    v2._md_number(float(row["cross_border_assigned_m3"])),
                    v2._md_number(float(row["published_assigned_m3"])),
                    v2._md_number(float(row["proxy_assigned_m3"])),
                    v2._md_number(float(row["unassigned_m3"])),
                ]
                for row in results[FLOOR_SCENARIO].province_summary
            ],
        ),
        "",
        "## Largest assignments in `province_capacity_floor`",
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
                "Scale",
            ],
            [
                [
                    row["facility_id"],
                    row["facility_province"],
                    row["facility_class"],
                    row["capacity_status"],
                    v2._md_number(float(row["capacity_m3_y"])),
                    v2._md_number(float(row["assigned_m3"])),
                    f"{100.0 * float(row['utilization']):.1f}%",
                    f"{float(row['floor_scale_factor']):.3f}",
                ]
                for row in sorted(
                    results[FLOOR_SCENARIO].assignments,
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
            "workflows.national.facilities.national_facility_assignment_v3"
        ),
        ".venv/bin/pytest tests/test_national_facility_assignment_v3.py",
        (
            ".venv/bin/ruff check "
            "workflows/national/facilities/national_facility_assignment_v3.py "
            "tests/test_national_facility_assignment_v3.py"
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
            "sha256": v2._sha256(path),
        }
    manifest = {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": summary["generated_utc"],
        "labels": summary["labels"],
        "inputs": summary["inputs"],
        "outputs": outputs,
        "capacity": summary["capacity"],
        "province_capacity_floor": summary["province_capacity_floor"],
        "v2_baseline": summary["v2_baseline"],
        "scenarios": summary["scenarios"],
        "comparison": summary["comparison"],
        "concentration": summary["concentration"],
        "validation": summary["validation"],
        "reproduce": summary["reproduce"],
    }
    v2._write_json(output_dir / "manifest.json", manifest)


def build(
    output_dir: Path = OUTPUT_DIR,
    capacity_path: Path = CAPACITY_CSV,
    inventory_path: Path = INVENTORY_CSV,
    harvest_path: Path = HARVEST_CSV,
    boundary_path: Path = BOUNDARY_GEOJSON,
    v2_summary_path: Path = V2_SUMMARY_JSON,
    config: AllocationConfig | None = None,
) -> dict[str, Any]:
    """Build the v3 assignment artifact and return its summary."""

    _assert_no_unexpected_deletions()
    config = config or AllocationConfig(
        road_factor=ROAD_FACTOR,
        cross_border_penalty_km=CROSS_BORDER_PENALTY_KM,
    )
    generated_utc = v2._utc_now()
    base_facilities = read_facilities(capacity_path, inventory_path)
    offers, centroid_methods = v2.read_offers(harvest_path, boundary_path)
    harvest_by_province = {
        offer.province: offer.volume_m3 for offer in offers
    }
    floor_plan = build_floor_plan(base_facilities, harvest_by_province)
    facilities_by_scenario = {
        "published_only": base_facilities,
        "published_plus_proxy": base_facilities,
        FLOOR_SCENARIO: floor_plan.facilities,
    }
    results = {
        scenario: allocate_scenario(
            offers, facilities_by_scenario[scenario], scenario, config
        )
        for scenario in SCENARIOS
    }
    v2_metrics = read_v2_metrics(v2_summary_path)
    v2_reproduction = {
        field: float(results["published_plus_proxy"].metrics[field])
        - float(v2_metrics[field])
        for field in (
            "assigned_m3",
            "unassigned_m3",
            "published_m3",
            "proxy_m3",
            "cross_border_m3",
            "max_facility_share",
            "hhi",
        )
    }
    inputs = {
        str(path.relative_to(REPO_ROOT)): v2._sha256(path)
        for path in (
            capacity_path,
            inventory_path,
            harvest_path,
            boundary_path,
            v2_summary_path,
        )
    }
    summary = _summary(
        generated_utc=generated_utc,
        base_facilities=base_facilities,
        floor_plan=floor_plan,
        offers=offers,
        centroid_methods=centroid_methods,
        results=results,
        v2_metrics=v2_metrics,
        v2_reproduction=v2_reproduction,
        inputs=inputs,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    v2._write_csv(
        output_dir / "facility_assignment.csv",
        ASSIGNMENT_FIELDS,
        [
            row
            for scenario in SCENARIOS
            for row in results[scenario].assignments
        ],
    )
    v2._write_csv(
        output_dir / "province_summary.csv",
        PROVINCE_SUMMARY_FIELDS,
        [
            row
            for scenario in SCENARIOS
            for row in results[scenario].province_summary
        ],
    )
    v2._write_csv(
        output_dir / "facility_utilization.csv",
        UTILIZATION_FIELDS,
        [
            row
            for scenario in SCENARIOS
            for row in results[scenario].facility_utilization
        ],
    )
    v2._write_csv(
        output_dir / "unassigned.csv",
        UNASSIGNED_FIELDS,
        [
            row
            for scenario in SCENARIOS
            for row in results[scenario].unassigned
        ],
    )
    v2._write_csv(
        output_dir / "province_class_totals.csv",
        PROVINCE_CLASS_FIELDS,
        summary["province_class_totals"],
    )
    v2._write_csv(
        output_dir / "concentration.csv",
        CONCENTRATION_FIELDS,
        [
            row
            for scenario in SCENARIOS
            for row in results[scenario].concentration
        ],
    )
    v2._write_csv(
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
    v2._write_csv(
        output_dir / "capacity_floor.csv",
        FLOOR_FIELDS,
        summary["province_capacity_floor"]["province_rows"],
    )
    v2._write_csv(
        output_dir / "capacity_floor_facilities.csv",
        FLOOR_FACILITY_FIELDS,
        _floor_facility_rows(floor_plan.facilities, floor_plan),
    )
    v2._write_csv(
        output_dir / "v2_v3_comparison.csv",
        V2_V3_FIELDS,
        summary["v2_v3_comparison"],
    )
    (output_dir / "NATIONAL_FACILITY_ASSIGNMENT_V3.md").write_text(
        _report(summary, results), encoding="utf-8"
    )
    v2._write_json(output_dir / "summary.json", summary)
    _write_manifest(output_dir, summary)
    return summary


def main() -> None:
    summary = build()
    floor = summary["province_capacity_floor"]
    print(
        json.dumps(
            {
                "artifact": summary["artifact"],
                "offer_m3": summary["scenarios"]["published_only"]["offer_m3"],
                "floor_scaled_provinces": floor["scaled_provinces"],
                "floor_scaled_records": floor["scaled_proxy_records"],
                "floor_shortfall_after_m3_y": floor["shortfall_after_m3_y"],
                "scenarios": {
                    scenario: {
                        "assigned_m3": summary["scenarios"][scenario][
                            "assigned_m3"
                        ],
                        "unassigned_m3": summary["scenarios"][scenario][
                            "unassigned_m3"
                        ],
                        "cross_border_m3": summary["scenarios"][scenario][
                            "cross_border_m3"
                        ],
                        "proxy_share_of_assigned": summary["scenarios"][
                            scenario
                        ]["proxy_share_of_assigned"],
                        "max_facility_share": summary["scenarios"][scenario][
                            "max_facility_share"
                        ],
                        "hhi": summary["scenarios"][scenario]["hhi"],
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
