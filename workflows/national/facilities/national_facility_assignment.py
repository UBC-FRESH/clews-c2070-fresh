"""Assign the 119 Mm3 provincial harvest case to national facilities.

This is a transparent screening allocation, not a solved CLEWS/FHOPS network.
It consumes the national facility inventory v0 and the 119 Mm3 provincial
harvest allocation, builds one ungraded supply offer per province, and places
that offer on eligible facilities under two capacity scenarios:

``known_only``
    Only facilities with a published capacity are eligible.  Published capacity
    is a hard upper bound.

``unbounded_unknown``
    Facilities without a published capacity are also eligible and are treated
    as a non-binding ``capacity_unbounded_proxy`` sink.  No capacity number is
    imputed for them.  Volume placed on these facilities is reported separately.

Allocation rule
---------------
1.  Local province first.  A province's offer is placed on facilities in the
    same province before any cross-border facility is considered.
2.  Within the local tier, located known-capacity facilities are filled before
    unknown-capacity proxies.  Known capacity is evidence-backed and bounded;
    unknown capacity must not displace it.
3.  Within a capacity tier, the nearest facility wins.  Distance is haversine
    from the ADM1 polygon centroid to the facility coordinate, multiplied by a
    documented road factor for ranking.
4.  If local capacity is exhausted, cross-border facilities are ranked by road
    distance plus a documented cross-border screening penalty.  No cross-border
    volume is placed while local eligible capacity remains.
5.  Province offers compete for shared cross-border capacity in descending
    residual volume, then province code.  This matches the ordering convention
    of ``workflows.national.assortments.facility.assign_feedstock``.

The artifact carries ``model_estimate`` / ``observed=false`` /
``policy_grade=false`` labels everywhere.  It makes no legal or AAC claim.

The module keeps the offer/contract vocabulary of
``workflows.national.assortments.facility`` but does not reuse
``FacilityContract``.  That contract requires a finite maximum volume, and an
unknown-capacity facility must not receive a fabricated maximum.  Capacity is
therefore a sum type here: ``KnownCapacity`` or ``UnknownCapacity``.
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

from shapely.geometry import shape

from workflows.national.facilities.national_facility_inventory import (
    CANADA_BBOX,
    PROVINCES,
    SHAPE_NAME_TO_CODE,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
INVENTORY_CSV = (
    REPO_ROOT
    / "data/derived/national/facilities/national_facility_inventory_v0"
    / "national_facilities.csv"
)
HARVEST_CSV = (
    REPO_ROOT
    / "data/derived/national/harvest_basis/provincial_allocation/119m.csv"
)
BOUNDARY_GEOJSON = (
    REPO_ROOT / "data/raw/boundaries/canada_adm1_geoboundaries_simplified.geojson"
)
OUTPUT_DIR = (
    REPO_ROOT
    / "data/derived/national/facilities/national_facility_assignment_v1"
)

ARTIFACT = "national_facility_assignment_v1"
ARTIFACT_VERSION = "1.0.0"
SCENARIOS = ("known_only", "unbounded_unknown")
RELEVANT_CLASSES = ("SAW", "PULP", "PANEL", "PELLET", "BIO")
CLASS_ORDER = ("SAW", "PULP", "PANEL", "PELLET", "BIO", "OTHER")
ALLOWED_OPERATING_STATUSES = frozenset(
    {
        "unknown",
        "operating",
        "temporarily_closed",
        "disused",
        "abandoned",
        "construction",
        "proposed",
    }
)
NON_OPERATING_STATUSES = frozenset(
    {"temporarily_closed", "disused", "abandoned", "construction", "proposed"}
)

EXPECTED_TOTAL_M3 = 118_977_339.1
TOTAL_TOLERANCE_M3 = 1.0
VOLUME_EPSILON_M3 = 1e-6
ROAD_FACTOR = 1.3
CROSS_BORDER_PENALTY_KM = 500.0
HAVERSINE_RADIUS_KM = 6371.0088

EVIDENCE_LABEL = "model_estimate"
OBSERVED = False
POLICY_GRADE = False

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
    "assigned_m3",
    "remaining_capacity_m3",
    "utilization",
    "distance_km",
    "road_distance_km",
    "cross_border_penalty_km",
    "rank_distance_km",
    "source_id",
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
    "known_assigned_m3",
    "unknown_assigned_m3",
    "facilities_used",
    "known_facilities_used",
    "unknown_facilities_used",
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
    "unassigned_m3",
    "unassigned_share",
    "reason",
    "evidence_label",
    "observed",
    "policy_grade",
]

def _caveats(capacity: dict[str, Any]) -> list[str]:
    """Build caveats so record and volume counts cannot drift from the data."""

    unlocated_records = capacity["known_unlocated_records"]
    unlocated_mm3 = capacity["known_unlocated_capacity_m3_y"] / 1e6
    unknown_records = capacity["unknown_records"]
    non_operating_records = capacity["non_operating_records"]
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
            "Known capacity is the 2015 Mills DB/FFCM processing-line infeed "
            "proxy carried by national_facility_inventory_v0, not licensed, "
            "current, or verified operating capacity."
        ),
        (
            "All known-capacity records are in BC. In `known_only`, every "
            "non-BC province must either cross a provincial border or "
            "overflow. That is a property of the inventory, not an "
            "interprovincial trade forecast."
        ),
        (
            f"{unlocated_records} known-capacity records "
            f"({unlocated_mm3:.2f} Mm3/yr) have no coordinate and are excluded "
            "from the spatial screen. `known_only` is therefore a lower bound "
            "on placement in published capacity, not a full use of it."
        ),
        (
            f"{unknown_records} relevant facilities have no published capacity. "
            "`unbounded_unknown` treats them as non-binding absorbers "
            "(`capacity_unbounded_proxy`) and does not assign them an invented "
            "capacity. Volume placed on them is not evidence of available, "
            "permitted, or operating capacity."
        ),
        (
            "The 119 Mm3 provincial volume is one ungraded offer per province. "
            "No sawlog/pulp/panel/pellet/biomass split is inferred, and class "
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
            "Cross-border assignments carry a 500 km screening penalty and are "
            "considered only after local capacity is exhausted. The penalty is "
            "an assumption, not a transport-cost estimate."
        ),
        (
            f"OSM presence does not establish operating status. "
            f"{non_operating_records} non-operating record(s) are excluded; all "
            "other inventory statuses are unknown."
        ),
        (
            "No facility data are introduced. Coordinates, classes, statuses, "
            "and published capacities come from national_facility_inventory_v0."
        ),
        "No UI or public OSeCAN surface is changed by this artifact.",
    ]


class AssignmentError(ValueError):
    """Raised when an input or allocation invariant fails."""


@dataclass(frozen=True, slots=True)
class Location:
    """A validated Canadian coordinate."""

    lat: float
    lon: float

    def __post_init__(self) -> None:
        lat, lon = float(self.lat), float(self.lon)
        if not (math.isfinite(lat) and math.isfinite(lon)):
            raise AssignmentError("location coordinates must be finite")
        if not CANADA_BBOX[0] <= lat <= CANADA_BBOX[2]:
            raise AssignmentError(f"latitude {lat} is outside the Canada bbox")
        if not CANADA_BBOX[1] <= lon <= CANADA_BBOX[3]:
            raise AssignmentError(f"longitude {lon} is outside the Canada bbox")
        object.__setattr__(self, "lat", lat)
        object.__setattr__(self, "lon", lon)


@dataclass(frozen=True, slots=True)
class KnownCapacity:
    """Published annual capacity in cubic metres."""

    capacity_m3_y: float

    def __post_init__(self) -> None:
        value = float(self.capacity_m3_y)
        if not math.isfinite(value) or value <= 0.0:
            raise AssignmentError("known capacity must be finite and positive")
        object.__setattr__(self, "capacity_m3_y", value)


@dataclass(frozen=True, slots=True)
class UnknownCapacity:
    """Explicit non-binding proxy; it deliberately carries no number."""

    proxy: str = "capacity_unbounded_proxy"

    def __post_init__(self) -> None:
        if self.proxy != "capacity_unbounded_proxy":
            raise AssignmentError(f"unexpected unknown-capacity proxy {self.proxy!r}")


Capacity = KnownCapacity | UnknownCapacity


@dataclass(frozen=True, slots=True)
class FacilityOption:
    """A parsed facility eligible for the assignment screen."""

    facility_id: str
    name: str
    facility_class: str
    province: str
    location: Location | None
    capacity: Capacity
    operating_status: str = "unknown"
    source_id: str = ""
    source_url: str = ""
    evidence_tier: str = ""

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
        if self.operating_status not in ALLOWED_OPERATING_STATUSES:
            raise AssignmentError(
                f"{self.facility_id}: unexpected operating status "
                f"{self.operating_status!r}"
            )
        if isinstance(self.capacity, UnknownCapacity) and self.location is None:
            raise AssignmentError(
                f"{self.facility_id}: unknown capacity requires a located facility"
            )

    @property
    def capacity_status(self) -> str:
        return "known" if isinstance(self.capacity, KnownCapacity) else "unknown"

    @property
    def capacity_m3_y(self) -> float | None:
        if isinstance(self.capacity, KnownCapacity):
            return self.capacity.capacity_m3_y
        return None

    @property
    def is_known(self) -> bool:
        return isinstance(self.capacity, KnownCapacity)

    @property
    def is_unknown(self) -> bool:
        return isinstance(self.capacity, UnknownCapacity)

    @property
    def is_operating(self) -> bool:
        return self.operating_status not in NON_OPERATING_STATUSES


@dataclass(frozen=True, slots=True)
class ProvinceOffer:
    """One ungraded provincial supply offer."""

    province: str
    volume_m3: float
    location: Location

    def __post_init__(self) -> None:
        if self.province not in PROVINCES:
            raise AssignmentError(f"unexpected offer province {self.province!r}")
        volume = float(self.volume_m3)
        if not math.isfinite(volume) or volume < 0.0:
            raise AssignmentError("offer volume must be finite and non-negative")
        object.__setattr__(self, "volume_m3", volume)


@dataclass(frozen=True, slots=True)
class AllocationConfig:
    """Documented screening assumptions."""

    road_factor: float = ROAD_FACTOR
    cross_border_penalty_km: float = CROSS_BORDER_PENALTY_KM

    def __post_init__(self) -> None:
        road_factor = float(self.road_factor)
        penalty = float(self.cross_border_penalty_km)
        if not math.isfinite(road_factor) or road_factor <= 0.0:
            raise AssignmentError("road_factor must be finite and positive")
        if not math.isfinite(penalty) or penalty < 0.0:
            raise AssignmentError(
                "cross_border_penalty_km must be finite and non-negative"
            )
        object.__setattr__(self, "road_factor", road_factor)
        object.__setattr__(self, "cross_border_penalty_km", penalty)


@dataclass(frozen=True, slots=True)
class Candidate:
    """A facility ranked for one province offer."""

    facility: FacilityOption
    distance_km: float
    road_distance_km: float
    cross_border: bool
    penalty_km: float
    rank_distance_km: float


@dataclass(frozen=True, slots=True)
class ScenarioResult:
    """Rows produced by one capacity scenario."""

    scenario: str
    assignments: tuple[dict[str, Any], ...]
    province_summary: tuple[dict[str, Any], ...]
    facility_utilization: tuple[dict[str, Any], ...]
    unassigned: tuple[dict[str, Any], ...]


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
    present = set(rows[0])
    missing = required - present
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


def haversine_km(lat_a: float, lon_a: float, lat_b: float, lon_b: float) -> float:
    """Great-circle distance in kilometres on the mean Earth radius."""

    phi_a, phi_b = math.radians(lat_a), math.radians(lat_b)
    delta_phi = phi_b - phi_a
    delta_lambda = math.radians(lon_b - lon_a)
    hav = (
        math.sin(delta_phi / 2.0) ** 2
        + math.cos(phi_a) * math.cos(phi_b) * math.sin(delta_lambda / 2.0) ** 2
    )
    return 2.0 * HAVERSINE_RADIUS_KM * math.asin(min(1.0, math.sqrt(hav)))


def read_harvest(path: Path = HARVEST_CSV) -> dict[str, float]:
    """Read the 119 Mm3 province harvest case and validate its total."""

    rows = _read_csv(path)
    _require_columns(rows, {"province", "harvest_m3"}, path)
    harvest: dict[str, float] = {}
    for position, row in enumerate(rows, start=2):
        province = row["province"].strip().upper()
        if province not in PROVINCES:
            raise AssignmentError(
                f"{path}:{position}: unexpected province {province!r}"
            )
        if province in harvest:
            raise AssignmentError(f"{path}:{position}: duplicate province {province}")
        volume = _finite_float(
            row["harvest_m3"], field="harvest_m3", context=f"{path}:{position}"
        )
        if volume < 0.0:
            raise AssignmentError(
                f"{path}:{position}: harvest volume cannot be negative"
            )
        harvest[province] = volume
    missing = set(PROVINCES) - set(harvest)
    if missing:
        raise AssignmentError(f"{path}: missing provinces {sorted(missing)}")
    total = sum(harvest.values())
    if abs(total - EXPECTED_TOTAL_M3) > TOTAL_TOLERANCE_M3:
        raise AssignmentError(
            f"{path}: national total {total:,.3f} m3 does not match the documented "
            f"119 Mm3 case ({EXPECTED_TOTAL_M3:,.1f} m3)"
        )
    return harvest


def load_province_centroids(
    path: Path = BOUNDARY_GEOJSON,
) -> tuple[dict[str, Location], dict[str, str]]:
    """Load one ADM1 centroid per province and record the construction method."""

    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError as error:
        raise AssignmentError(f"boundary file is missing: {path}") from error
    except OSError as error:
        raise AssignmentError(f"cannot read {path}: {error}") from error

    centroids: dict[str, Location] = {}
    methods: dict[str, str] = {}
    for feature in payload.get("features", []):
        name = feature.get("properties", {}).get("shapeName", "")
        province = SHAPE_NAME_TO_CODE.get(name)
        if province is None:
            raise AssignmentError(f"{path}: unrecognised ADM1 shapeName {name!r}")
        if province in centroids:
            raise AssignmentError(f"{path}: duplicate ADM1 feature for {province}")
        geometry = shape(feature["geometry"])
        if geometry.is_empty:
            raise AssignmentError(f"{path}: empty geometry for {province}")
        point = geometry.centroid
        method = "polygon_centroid"
        if not geometry.covers(point):
            point = geometry.representative_point()
            method = "representative_point"
        centroids[province] = Location(float(point.y), float(point.x))
        methods[province] = method

    missing = set(PROVINCES) - set(centroids)
    if missing:
        raise AssignmentError(f"{path}: missing ADM1 centroids {sorted(missing)}")
    return centroids, methods


def read_offers(
    harvest_path: Path = HARVEST_CSV,
    boundary_path: Path = BOUNDARY_GEOJSON,
) -> tuple[tuple[ProvinceOffer, ...], dict[str, str]]:
    """Build one ungraded supply offer per province."""

    harvest = read_harvest(harvest_path)
    centroids, methods = load_province_centroids(boundary_path)
    offers = tuple(
        ProvinceOffer(
            province=province,
            volume_m3=harvest[province],
            location=centroids[province],
        )
        for province in PROVINCES
    )
    return offers, methods


def read_facilities(path: Path = INVENTORY_CSV) -> tuple[FacilityOption, ...]:
    """Parse national facility inventory v0 without imputing capacity."""

    rows = _read_csv(path)
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
            "capacity_status",
            "status",
            "source_id",
            "source_url",
            "evidence_tier",
        },
        path,
    )

    facilities: list[FacilityOption] = []
    seen_ids: set[str] = set()
    for position, row in enumerate(rows, start=2):
        facility_id = row["facility_id"].strip()
        if not facility_id:
            raise AssignmentError(f"{path}:{position}: blank facility_id")
        if facility_id in seen_ids:
            raise AssignmentError(f"{path}:{position}: duplicate {facility_id}")
        seen_ids.add(facility_id)

        facility_class = row["facility_class"].strip().upper()
        if facility_class not in CLASS_ORDER:
            raise AssignmentError(
                f"{path}:{position}: unexpected facility_class "
                f"{facility_class!r}"
            )
        province = row["province"].strip().upper()
        if province not in PROVINCES:
            raise AssignmentError(
                f"{path}:{position}: unexpected province {province!r}"
            )

        capacity_text = row["capacity_m3_y"].strip()
        capacity_status = row["capacity_status"].strip().lower()
        if capacity_text:
            if capacity_status != "published":
                raise AssignmentError(
                    f"{path}:{position}: capacity present but status is "
                    f"{capacity_status!r}"
                )
            capacity: Capacity = KnownCapacity(
                _finite_float(
                    capacity_text,
                    field="capacity_m3_y",
                    context=f"{path}:{position}",
                )
            )
        else:
            if capacity_status != "not_published":
                raise AssignmentError(
                    f"{path}:{position}: blank capacity but status is "
                    f"{capacity_status!r}"
                )
            capacity = UnknownCapacity()

        raw_lat = row["lat"].strip()
        raw_lon = row["lon"].strip()
        if not raw_lat and not raw_lon:
            location = None
        elif not raw_lat or not raw_lon:
            raise AssignmentError(
                f"{path}:{position}: latitude and longitude must both be present"
            )
        else:
            location = Location(
                _finite_float(raw_lat, field="lat", context=f"{path}:{position}"),
                _finite_float(raw_lon, field="lon", context=f"{path}:{position}"),
            )

        source_id = row["source_id"].strip()
        source_url = row["source_url"].strip()
        evidence_tier = row["evidence_tier"].strip().upper()
        if not source_id:
            raise AssignmentError(f"{path}:{position}: blank source_id")
        if not source_url:
            raise AssignmentError(f"{path}:{position}: blank source_url")
        if evidence_tier not in {"A", "B", "C", "D"}:
            raise AssignmentError(
                f"{path}:{position}: unexpected evidence_tier {evidence_tier!r}"
            )

        operating_status = row["status"].strip().lower() or "unknown"
        facilities.append(
            FacilityOption(
                facility_id=facility_id,
                name=row["name"].strip(),
                facility_class=facility_class,
                province=province,
                location=location,
                capacity=capacity,
                operating_status=operating_status,
                source_id=source_id,
                source_url=source_url,
                evidence_tier=evidence_tier,
            )
        )
    return tuple(facilities)


def _is_relevant(facility: FacilityOption) -> bool:
    return facility.facility_class in RELEVANT_CLASSES


def _is_screenable(facility: FacilityOption, scenario: str) -> bool:
    if not _is_relevant(facility):
        return False
    if not facility.is_operating:
        return False
    if facility.location is None:
        return False
    return not (scenario == "known_only" and facility.is_unknown)


def _candidate(
    offer: ProvinceOffer,
    facility: FacilityOption,
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
    facility: FacilityOption,
    candidate: Candidate,
    assigned_m3: float,
    facility_assigned_m3: float,
    remaining_capacity_m3: float | None,
    phase: str,
) -> dict[str, Any]:
    capacity = facility.capacity_m3_y
    utilization = (
        facility_assigned_m3 / capacity if capacity is not None else None
    )
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
        "capacity_status": facility.capacity_status,
        "capacity_m3_y": _format_float(capacity),
        "assigned_m3": _format_float(assigned_m3),
        "remaining_capacity_m3": _format_float(remaining_capacity_m3),
        "utilization": _format_float(utilization),
        "distance_km": _format_float(candidate.distance_km),
        "road_distance_km": _format_float(candidate.road_distance_km),
        "cross_border_penalty_km": _format_float(candidate.penalty_km),
        "rank_distance_km": _format_float(candidate.rank_distance_km),
        "source_id": facility.source_id,
        "evidence_tier": facility.evidence_tier,
        "evidence_label": EVIDENCE_LABEL,
        "observed": _bool_text(OBSERVED),
        "policy_grade": _bool_text(POLICY_GRADE),
    }


def allocate_scenario(
    offers: Sequence[ProvinceOffer],
    facilities: Sequence[FacilityOption],
    scenario: str,
    config: AllocationConfig | None = None,
) -> ScenarioResult:
    """Run one capacity scenario with local-first, nearest-eligible allocation."""

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
        facility.facility_id: (
            facility.capacity_m3_y if facility.is_known else math.inf
        )
        for facility in eligible
    }
    facility_assigned = {facility.facility_id: 0.0 for facility in eligible}
    assignments: list[dict[str, Any]] = []
    allocation_order = 0
    candidate_cache: dict[tuple[str, str], Candidate] = {}

    def candidate_for(offer: ProvinceOffer, facility: FacilityOption) -> Candidate:
        key = (offer.province, facility.facility_id)
        cached = candidate_cache.get(key)
        if cached is None:
            cached = _candidate(offer, facility, config)
            candidate_cache[key] = cached
        return cached

    def record(
        offer: ProvinceOffer,
        facility: FacilityOption,
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
                remaining_capacity_m3=(
                    remaining[facility.facility_id]
                    if facility.is_known
                    else None
                ),
                phase=phase,
            )
        )

    residual = {offer.province: offer.volume_m3 for offer in offers}

    # Local phase: known capacity first, then unknown proxies in the unbounded case.
    for offer in sorted(offers, key=lambda item: item.province):
        local_known = sorted(
            (
                facility
                for facility in eligible
                if facility.province == offer.province and facility.is_known
            ),
            key=lambda facility: (
                candidate_for(offer, facility).road_distance_km,
                facility.facility_id,
            ),
        )
        for facility in local_known:
            if residual[offer.province] <= VOLUME_EPSILON_M3:
                break
            available = remaining[facility.facility_id]
            take = min(residual[offer.province], available)
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

        if (
            residual[offer.province] > VOLUME_EPSILON_M3
            and scenario == "unbounded_unknown"
        ):
            local_unknown = sorted(
                (
                    facility
                    for facility in eligible
                    if facility.province == offer.province and facility.is_unknown
                ),
                key=lambda facility: (
                    candidate_for(offer, facility).road_distance_km,
                    facility.facility_id,
                ),
            )
            if local_unknown:
                facility = local_unknown[0]
                take = residual[offer.province]
                record(
                    offer,
                    facility,
                    take,
                    candidate_for(offer, facility),
                    "local",
                )
                residual[offer.province] = 0.0

    # Cross-border phase: only residual demand, largest residual first.
    offers_by_province = {offer.province: offer for offer in offers}
    for province in sorted(
        residual, key=lambda code: (-residual[code], code)
    ):
        if residual[province] <= VOLUME_EPSILON_M3:
            continue
        offer = offers_by_province[province]
        candidates = [
            facility
            for facility in eligible
            if facility.province != province
            and (
                facility.is_unknown
                or remaining[facility.facility_id] > VOLUME_EPSILON_M3
            )
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
            if facility.is_unknown:
                take = residual[province]
            else:
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
    _validate_scenario(scenario, offers, assignments, province_summary, unassigned)
    return ScenarioResult(
        scenario=scenario,
        assignments=tuple(assignments),
        province_summary=tuple(province_summary),
        facility_utilization=tuple(utilization),
        unassigned=tuple(unassigned),
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
        known = sum(
            float(row["assigned_m3"])
            for row in rows
            if row["capacity_status"] == "known"
        )
        unknown = assigned - known
        facilities_used = {row["facility_id"] for row in rows}
        known_facilities = {
            row["facility_id"] for row in rows if row["capacity_status"] == "known"
        }
        unknown_facilities = facilities_used - known_facilities
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
                "known_assigned_m3": _format_float(known),
                "unknown_assigned_m3": _format_float(unknown),
                "facilities_used": len(facilities_used),
                "known_facilities_used": len(known_facilities),
                "unknown_facilities_used": len(unknown_facilities),
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


def _screen_status(facility: FacilityOption, scenario: str) -> str:
    if not _is_relevant(facility):
        return "excluded_class"
    if not facility.is_operating:
        return "excluded_non_operating"
    if facility.location is None:
        return "excluded_unlocated"
    if facility.is_unknown:
        if scenario == "known_only":
            return "excluded_unknown_capacity"
        return "eligible_unbounded_proxy"
    return "eligible_known"


def _build_facility_utilization(
    scenario: str,
    facilities: Sequence[FacilityOption],
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
        capacity = facility.capacity_m3_y
        screen_status = _screen_status(facility, scenario)
        screenable = screen_status.startswith("eligible_")
        remaining = (
            max(0.0, capacity - assigned)
            if capacity is not None and screenable
            else None
        )
        utilization = (
            assigned / capacity
            if capacity is not None and screenable and capacity > 0.0
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
                "capacity_m3_y": _format_float(capacity),
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
                "unassigned_m3": row["unassigned_m3"],
                "unassigned_share": _format_float(
                    unassigned / offer if offer > 0.0 else None
                ),
                "reason": (
                    "no eligible located capacity remains after the local-first "
                    "and cross-border phases"
                ),
                "evidence_label": EVIDENCE_LABEL,
                "observed": _bool_text(OBSERVED),
                "policy_grade": _bool_text(POLICY_GRADE),
            }
        )
    return rows


def _validate_scenario(
    scenario: str,
    offers: Sequence[ProvinceOffer],
    assignments: Sequence[dict[str, Any]],
    province_summary: Sequence[dict[str, Any]],
    unassigned: Sequence[dict[str, Any]],
) -> None:
    offer_total = sum(offer.volume_m3 for offer in offers)
    assigned_total = sum(float(row["assigned_m3"]) for row in assignments)
    unassigned_total = sum(float(row["unassigned_m3"]) for row in unassigned)
    if abs(offer_total - assigned_total - unassigned_total) > 0.01:
        raise AssignmentError(
            f"{scenario}: conservation failure: offer {offer_total:,.3f}, "
            f"assigned {assigned_total:,.3f}, unassigned {unassigned_total:,.3f}"
        )
    summary_offer = sum(float(row["offer_m3"]) for row in province_summary)
    summary_assigned = sum(float(row["assigned_m3"]) for row in province_summary)
    summary_unassigned = sum(float(row["unassigned_m3"]) for row in province_summary)
    if abs(summary_offer - offer_total) > 0.01:
        raise AssignmentError(f"{scenario}: province offer total does not conserve")
    if abs(summary_assigned - assigned_total) > 0.01:
        raise AssignmentError(
            f"{scenario}: province assigned total does not match assignments"
        )
    if abs(summary_unassigned - unassigned_total) > 0.01:
        raise AssignmentError(
            f"{scenario}: province unassigned total does not match unassigned rows"
        )


def _capacity_stats(
    facilities: Sequence[FacilityOption],
) -> dict[str, Any]:
    relevant = [item for item in facilities if _is_relevant(item)]
    known = [item for item in relevant if item.is_known]
    known_located = [item for item in known if item.location is not None]
    known_unlocated = [item for item in known if item.location is None]
    unknown = [item for item in relevant if item.is_unknown]
    non_operating = [item for item in relevant if not item.is_operating]
    excluded_other = [
        item for item in facilities if item.facility_class not in RELEVANT_CLASSES
    ]
    known_by_province: dict[str, dict[str, float]] = defaultdict(
        lambda: {"records": 0, "capacity_m3_y": 0.0}
    )
    for item in known:
        entry = known_by_province[item.province]
        entry["records"] += 1
        entry["capacity_m3_y"] += float(item.capacity_m3_y or 0.0)
    return {
        "inventory_records": len(facilities),
        "relevant_records": len(relevant),
        "excluded_other_records": len(excluded_other),
        "known_records": len(known),
        "known_capacity_m3_y": sum(
            float(item.capacity_m3_y or 0.0) for item in known
        ),
        "known_located_records": len(known_located),
        "known_located_capacity_m3_y": sum(
            float(item.capacity_m3_y or 0.0) for item in known_located
        ),
        "known_unlocated_records": len(known_unlocated),
        "known_unlocated_capacity_m3_y": sum(
            float(item.capacity_m3_y or 0.0) for item in known_unlocated
        ),
        "known_capacity_by_province": {
            province: {
                "records": int(values["records"]),
                "capacity_m3_y": float(values["capacity_m3_y"]),
            }
            for province, values in sorted(known_by_province.items())
        },
        "unknown_records": len(unknown),
        "unknown_capacity_m3_y": None,
        "unknown_capacity_proxy": "capacity_unbounded_proxy",
        "non_operating_records": len(non_operating),
        "non_operating_statuses": sorted(
            {item.operating_status for item in non_operating}
        ),
    }


def _scenario_totals(
    result: ScenarioResult, offer_total: float
) -> dict[str, Any]:
    assigned = sum(float(row["assigned_m3"]) for row in result.assignments)
    known = sum(
        float(row["assigned_m3"])
        for row in result.assignments
        if row["capacity_status"] == "known"
    )
    unknown = assigned - known
    cross_border = sum(
        float(row["assigned_m3"])
        for row in result.assignments
        if row["cross_border"] == "true"
    )
    facilities_used = {row["facility_id"] for row in result.assignments}
    known_facilities = {
        row["facility_id"]
        for row in result.assignments
        if row["capacity_status"] == "known"
    }
    return {
        "offer_m3": offer_total,
        "assigned_m3": assigned,
        "unassigned_m3": offer_total - assigned,
        "assigned_share": assigned / offer_total if offer_total > 0.0 else None,
        "known_assigned_m3": known,
        "unknown_assigned_m3": unknown,
        "unknown_share_of_assigned": unknown / assigned if assigned > 0.0 else None,
        "cross_border_assigned_m3": cross_border,
        "cross_border_share": cross_border / assigned if assigned > 0.0 else None,
        "facilities_used": len(facilities_used),
        "known_facilities_used": len(known_facilities),
        "unknown_facilities_used": len(facilities_used - known_facilities),
        "assignment_rows": len(result.assignments),
        "unassigned_provinces": len(result.unassigned),
    }


def _province_class_totals(
    results: dict[str, ScenarioResult],
) -> list[dict[str, Any]]:
    totals: list[dict[str, Any]] = []
    for scenario in SCENARIOS:
        grouped: dict[tuple[str, str], float] = defaultdict(float)
        for row in results[scenario].assignments:
            grouped[(row["province"], row["facility_class"])] += float(
                row["assigned_m3"]
            )
        for (province, facility_class), volume in sorted(grouped.items()):
            if volume <= VOLUME_EPSILON_M3:
                continue
            totals.append(
                {
                    "scenario": scenario,
                    "province": province,
                    "facility_class": facility_class,
                    "assigned_m3": volume,
                }
            )
    return totals


def _summary(
    *,
    generated_utc: str,
    facilities: Sequence[FacilityOption],
    offers: Sequence[ProvinceOffer],
    centroid_methods: dict[str, str],
    results: dict[str, ScenarioResult],
    config: AllocationConfig,
    inputs: dict[str, str],
) -> dict[str, Any]:
    capacity = _capacity_stats(facilities)
    offer_total = sum(offer.volume_m3 for offer in offers)
    scenario_totals = {
        scenario: _scenario_totals(results[scenario], offer_total)
        for scenario in SCENARIOS
    }
    validation: dict[str, Any] = {
        "conservation_gap_m3": {},
        "province_assigned_gap_m3": {},
        "max_known_utilization": {},
        "known_capacity_violations": 0,
        "unknown_assignments_in_known_only": 0,
        "unlocated_known_capacity_m3": capacity["known_unlocated_capacity_m3_y"],
        "unlocated_known_records": capacity["known_unlocated_records"],
    }
    for scenario in SCENARIOS:
        result = results[scenario]
        assigned = sum(float(row["assigned_m3"]) for row in result.assignments)
        unassigned = sum(float(row["unassigned_m3"]) for row in result.unassigned)
        summary_assigned = sum(
            float(row["assigned_m3"]) for row in result.province_summary
        )
        validation["conservation_gap_m3"][scenario] = (
            offer_total - assigned - unassigned
        )
        validation["province_assigned_gap_m3"][scenario] = assigned - summary_assigned
        utilizations = [
            float(row["utilization"])
            for row in result.facility_utilization
            if row["utilization"] != ""
        ]
        validation["max_known_utilization"][scenario] = (
            max(utilizations) if utilizations else 0.0
        )
        violations = sum(
            1
            for row in result.facility_utilization
            if row["capacity_status"] == "known"
            and row["utilization"] != ""
            and float(row["utilization"]) > 1.0 + 1e-9
        )
        validation["known_capacity_violations"] += violations
        if scenario == "known_only":
            validation["unknown_assignments_in_known_only"] = sum(
                1
                for row in result.assignments
                if row["capacity_status"] == "unknown"
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
            "supply_offers": (
                "one ungraded provincial offer per province; no class split inferred"
            ),
            "contract_alignment": (
                "offer/contract vocabulary follows "
                "workflows.national.assortments.facility; FacilityContract is "
                "not reused because a finite maximum cannot represent unknown "
                "capacity without inventing a number"
            ),
            "quantity_basis": (
                "province harvest_m3 treated one-to-one as facility infeed m3; "
                "no moisture, bark, grade, or recovery conversion"
            ),
            "eligible_classes": list(RELEVANT_CLASSES),
            "excluded_classes": ["OTHER"],
            "local_first": True,
            "known_capacity_rule": (
                "assigned_m3 <= capacity_m3_y for every located known-capacity "
                "facility"
            ),
            "unknown_capacity_rule": (
                "capacity_unbounded_proxy: unknown capacity is non-binding and is "
                "never given a numeric capacity"
            ),
            "local_priority": (
                "local known capacity before local unknown proxies; unknown "
                "proxies absorb local residual only"
            ),
            "cross_border_rule": (
                "cross-border only after local capacity is exhausted; candidates "
                "ranked by road distance plus the cross-border penalty"
            ),
            "cross_border_competition": (
                "province residuals processed in descending volume, then province "
                "code, matching the assign_feedstock ordering convention"
            ),
            "cross_border_penalty_km": config.cross_border_penalty_km,
            "road_factor": config.road_factor,
            "distance_basis": (
                "haversine from ADM1 polygon centroid to facility coordinate; "
                "road distance = haversine * road_factor"
            ),
            "centroid_source": str(BOUNDARY_GEOJSON.relative_to(REPO_ROOT)),
            "centroid_methods": centroid_methods,
            "province_order": list(PROVINCES),
            "non_operating_statuses_excluded": sorted(NON_OPERATING_STATUSES),
            "unlocated_known_capacity": (
                "excluded from the spatial screen and reported separately"
            ),
            "scenarios": {
                "known_only": (
                    "only published-capacity facilities are eligible; published "
                    "capacity is a hard maximum"
                ),
                "unbounded_unknown": (
                    "published-capacity plus unknown-capacity facilities; unknown "
                    "capacity is a non-binding proxy sink"
                ),
            },
        },
        "inputs": inputs,
        "capacity": capacity,
        "totals": {
            "offer_m3": offer_total,
            "scenario_totals": scenario_totals,
        },
        "province_class_totals": _province_class_totals(results),
        "validation": validation,
        "outputs": {
            "facility_assignment_csv": "facility_assignment.csv",
            "province_summary_csv": "province_summary.csv",
            "facility_utilization_csv": "facility_utilization.csv",
            "unassigned_csv": "unassigned.csv",
            "report_md": "NATIONAL_FACILITY_ASSIGNMENT_V1.md",
        },
        "reproduce": (
            ".venv/bin/python -m "
            "workflows.national.facilities.national_facility_assignment"
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
    if digits == 0:
        return f"{value:,.0f}"
    return f"{value:,.{digits}f}"


def _report(summary: dict[str, Any], results: dict[str, ScenarioResult]) -> str:
    known_provinces = ", ".join(
        summary["capacity"]["known_capacity_by_province"]
    )
    lines = [
        "# National Facility Assignment v1",
        "",
        f"Generated {summary['generated_utc']}.",
        "",
        (
            "**Labels:** `model_estimate`; `observed=false`; "
            "`policy_grade=false`."
        ),
        "",
        (
            "This artifact screens the 119 Mm3 provincial harvest case against "
            "the national facility inventory v0. It is not a legal AAC, not an "
            "observed allocation, and not policy-grade. No UI or public OSeCAN "
            "surface is changed."
        ),
        "",
        "## Assignment rule",
        "",
        (
            "1. Build one ungraded supply offer per province from `119m.csv` "
            f"({summary['totals']['offer_m3']:,.0f} m3 nationally)."
        ),
        (
            "2. Eligible facilities are located inventory records in SAW, PULP, "
            "PANEL, PELLET, or BIO. OTHER records and non-operating records are "
            "excluded."
        ),
        (
            "3. Local province first. Local known-capacity facilities are filled "
            "before local unknown-capacity proxies, nearest road-adjusted distance "
            "first."
        ),
        (
            "4. Known capacity is a hard maximum. Unknown capacity is an explicit "
            "`capacity_unbounded_proxy`; no capacity number is invented for it."
        ),
        (
            "5. Cross-border facilities are considered only after local capacity "
            "is exhausted. They are ranked by road distance plus a 500 km "
            "screening penalty."
        ),
        (
            "6. Province residuals compete for shared cross-border capacity in "
            "descending residual volume, then province code. This follows the "
            "existing `assign_feedstock` ordering convention."
        ),
        (
            "7. Distances are haversine from an unweighted ADM1 polygon centroid "
            "to the facility coordinate; the ranking road distance applies a 1.3 "
            "factor. No routing or cost model is used."
        ),
        "",
        "## Capacity basis",
        "",
        "| Measure | Records | Capacity m3/yr |",
        "|---|---:|---:|",
        (
            f"| Relevant facility records | "
            f"{summary['capacity']['relevant_records']} | |"
        ),
        (
            f"| Known capacity, all | "
            f"{summary['capacity']['known_records']} | "
            f"{_md_number(summary['capacity']['known_capacity_m3_y'], 0)} |"
        ),
        (
            f"| Known capacity, located and screenable | "
            f"{summary['capacity']['known_located_records']} | "
            f"{_md_number(summary['capacity']['known_located_capacity_m3_y'], 0)} |"
        ),
        (
            f"| Known capacity, unlocated and excluded | "
            f"{summary['capacity']['known_unlocated_records']} | "
            f"{_md_number(summary['capacity']['known_unlocated_capacity_m3_y'], 0)} |"
        ),
        (
            f"| Unknown capacity | "
            f"{summary['capacity']['unknown_records']} | not published |"
        ),
        (
            f"| Excluded OTHER class | "
            f"{summary['capacity']['excluded_other_records']} | |"
        ),
        "",
        (
            f"The {summary['capacity']['known_unlocated_records']} unlocated "
            "known-capacity records have no coordinate. They are not assigned "
            "by distance. Consequently `known_only` is a lower bound on "
            "placement in published capacity, not a full use of published "
            "capacity."
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
                    "Known m3",
                    "Unknown m3",
                    "Unassigned m3",
                ],
                [
                    [
                        row["province"],
                        _md_number(float(row["offer_m3"])),
                        _md_number(float(row["assigned_m3"])),
                        _md_number(float(row["local_assigned_m3"])),
                        _md_number(float(row["cross_border_assigned_m3"])),
                        _md_number(float(row["known_assigned_m3"])),
                        _md_number(float(row["unknown_assigned_m3"])),
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
        class_matrix[row["facility_class"]][row["scenario"]] += row["assigned_m3"]
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
        "## Known versus unknown capacity",
        "",
        *_md_table(
            [
                "Scenario",
                "Assigned m3",
                "Known-capacity m3",
                "Unknown-capacity m3",
                "Unknown share",
                "Cross-border m3",
                "Facilities used",
            ],
            [
                [
                    scenario,
                    _md_number(summary["totals"]["scenario_totals"][scenario]["assigned_m3"]),
                    _md_number(
                        summary["totals"]["scenario_totals"][scenario][
                            "known_assigned_m3"
                        ]
                    ),
                    _md_number(
                        summary["totals"]["scenario_totals"][scenario][
                            "unknown_assigned_m3"
                        ]
                    ),
                    _md_number(
                        100.0
                        * (
                            summary["totals"]["scenario_totals"][scenario][
                                "unknown_share_of_assigned"
                            ]
                            or 0.0
                        ),
                        1,
                    )
                    + "%",
                    _md_number(
                        summary["totals"]["scenario_totals"][scenario][
                            "cross_border_assigned_m3"
                        ]
                    ),
                    summary["totals"]["scenario_totals"][scenario][
                        "facilities_used"
                    ],
                ]
                for scenario in SCENARIOS
            ],
        ),
        "",
        (
            f"Known-capacity records exist only in {known_provinces}. In "
            "`known_only`, a province without local known capacity must either "
            "cross a provincial border or overflow. This is an inventory "
            "coverage result, not an interprovincial trade forecast."
        ),
        "",
        (
            "`unbounded_unknown` is deliberately permissive: every eligible "
            "unknown-capacity facility can absorb unlimited volume. The volume "
            "shown on those facilities is a scenario artefact, not evidence of "
            "available or permitted capacity."
        ),
        "",
        "## Unassigned and overflow",
        "",
    ]

    unassigned_rows = [
        row
        for scenario in SCENARIOS
        for row in results[scenario].unassigned
    ]
    if unassigned_rows:
        lines += _md_table(
            ["Scenario", "Province", "Unassigned m3", "Share of offer"],
            [
                [
                    row["scenario"],
                    row["province"],
                    _md_number(float(row["unassigned_m3"])),
                    _md_number(100.0 * float(row["unassigned_share"]), 1) + "%",
                ]
                for row in unassigned_rows
            ],
        )
    else:
        lines.append("No unassigned volume in either scenario.")
    lines += [
        "",
        (
            "Overflow is demand that no eligible located facility could take "
            "under the scenario's capacity rule. It is not a statement that the "
            "province lacks physical wood or legal harvest rights."
        ),
        "",
        "## Largest assignments in `unbounded_unknown`",
        "",
    ]

    largest = sorted(
        results["unbounded_unknown"].assignments,
        key=lambda row: -float(row["assigned_m3"]),
    )[:10]
    lines += _md_table(
        [
            "Facility",
            "Province",
            "Class",
            "Capacity status",
            "Assigned m3",
        ],
        [
            [
                row["facility_id"],
                row["facility_province"],
                row["facility_class"],
                row["capacity_status"],
                _md_number(float(row["assigned_m3"])),
            ]
            for row in largest
        ],
    )
    lines += [
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
            "workflows.national.facilities.national_facility_assignment"
        ),
        ".venv/bin/pytest tests/test_national_facility_assignment.py",
        (
            ".venv/bin/ruff check workflows/national/facilities/"
            "national_facility_assignment.py "
            "tests/test_national_facility_assignment.py"
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
        "totals": summary["totals"],
        "validation": summary["validation"],
        "reproduce": summary["reproduce"],
    }
    _write_json(output_dir / "manifest.json", manifest)


def build(
    output_dir: Path = OUTPUT_DIR,
    inventory_path: Path = INVENTORY_CSV,
    harvest_path: Path = HARVEST_CSV,
    boundary_path: Path = BOUNDARY_GEOJSON,
    config: AllocationConfig | None = None,
) -> dict[str, Any]:
    """Build the assignment artifact and return its summary."""

    config = config or AllocationConfig()
    generated_utc = _utc_now()
    facilities = read_facilities(inventory_path)
    offers, centroid_methods = read_offers(harvest_path, boundary_path)

    results = {
        scenario: allocate_scenario(offers, facilities, scenario, config)
        for scenario in SCENARIOS
    }
    inputs = {
        str(path.relative_to(REPO_ROOT)): _sha256(path)
        for path in (inventory_path, harvest_path, boundary_path)
    }
    summary = _summary(
        generated_utc=generated_utc,
        facilities=facilities,
        offers=offers,
        centroid_methods=centroid_methods,
        results=results,
        config=config,
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
    (output_dir / "NATIONAL_FACILITY_ASSIGNMENT_V1.md").write_text(
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
                "offer_m3": summary["totals"]["offer_m3"],
                "scenario_totals": {
                    scenario: {
                        "assigned_m3": totals["assigned_m3"],
                        "unassigned_m3": totals["unassigned_m3"],
                        "known_assigned_m3": totals["known_assigned_m3"],
                        "unknown_assigned_m3": totals["unknown_assigned_m3"],
                    }
                    for scenario, totals in summary["totals"][
                        "scenario_totals"
                    ].items()
                },
                "output_dir": str(OUTPUT_DIR.relative_to(REPO_ROOT)),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
