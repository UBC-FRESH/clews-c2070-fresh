"""Audit open road-network distances against the haversine x 1.3 screen.

The national facility assignment ranks candidate facilities by haversine
distance from an ADM1 polygon centroid to the facility coordinate, multiplied
by a documented ``ROAD_FACTOR = 1.3``.  That factor is an assumption, not an
observation.  This audit samples eligible facility/origin pairs across six
provinces and compares the screening proxy with public OSRM driving routes.

Design
------
* Universe: national facility capacity v3, the 689-record inventory of which
  608 records carry coordinates.
* Sample: four eligible facilities per province in BC, AB, ON, QC, NS and NL,
  selected deterministically at the 12.5/37.5/62.5/87.5 distance percentiles.
  The sample is stratified by distance; it is not a random national sample.
* Routing: public OSRM demo server (OpenStreetMap road network), one request
  per pair, rate-limited to one request every 1.2 s.  No paid API key is used.
* Comparison: road distance versus haversine, and the current proxy
  ``haversine x 1.3`` versus road distance.  A positive proxy error means the
  proxy is conservative (it exceeds the routed distance).
* Fail closed: any unexpected ``D`` in the parent or data repository git
  status halts the build before routing.  No route is fabricated.

The artifact is labelled ``model_estimate`` / ``observed=false`` /
``policy_grade=false``.  It does not change any assignment and makes no
national claim from 24 sampled pairs.  No UI or public OSeCAN surface is
changed.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import statistics
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from workflows.national.facilities.national_facility_assignment import (
    ALLOWED_OPERATING_STATUSES,
    NON_OPERATING_STATUSES,
    haversine_km,
    load_province_centroids,
)
from workflows.national.facilities.national_facility_inventory import PROVINCES

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_ROOT = REPO_ROOT / "data"
CAPACITY_CSV = (
    DATA_ROOT
    / "derived/national/facilities/national_facility_capacity_v3"
    / "national_facility_capacity_v3.csv"
)
V6_ASSIGNMENT_CSV = (
    DATA_ROOT
    / "derived/national/facilities/national_facility_assignment_v6"
    / "facility_assignment.csv"
)
BOUNDARY_GEOJSON = (
    DATA_ROOT / "raw/boundaries/canada_adm1_geoboundaries_simplified.geojson"
)
OUTPUT_DIR = (
    DATA_ROOT
    / "derived/national/facilities/facility_road_distance_audit_v1"
)

ARTIFACT = "facility_road_distance_audit_v1"
ARTIFACT_VERSION = "1.0.0"
SAMPLE_PROVINCES = ("BC", "AB", "ON", "QC", "NS", "NL")
SAMPLE_PER_PROVINCE = 4
SAMPLE_QUANTILES = (0.125, 0.375, 0.625, 0.875)
RELEVANT_CLASSES = ("SAW", "PULP", "PANEL", "PELLET", "BIO")
EXPECTED_TOTAL_RECORDS = 689
EXPECTED_MAPPED_RECORDS = 608
ROAD_FACTOR = 1.3

OSRM_BASE_URL = "https://router.project-osrm.org"
OSRM_PROFILE = "driving"
OSRM_LICENCE = "ODbL-1.0"
OSRM_ATTRIBUTION = "© OpenStreetMap contributors"
USER_AGENT = "clews-c2070-fresh-road-distance-audit/1.0"
RATE_LIMIT_SECONDS = 1.2
REQUEST_TIMEOUT_SECONDS = 30.0
MAX_ATTEMPTS = 3
SNAP_WARNING_KM = 5.0
MAX_PLAUSIBLE_ROAD_KM = 20_000.0
# A road route cannot be materially shorter than the great-circle distance.
MIN_ROAD_TO_HAVERSINE = 0.90

EVIDENCE_LABEL = "model_estimate"
OBSERVED = False
POLICY_GRADE = False
ASSIGNMENT_CHANGE = False
NATIONAL_CLAIM = False

SAMPLE_FIELDS = [
    "pair_id",
    "province",
    "distance_stratum",
    "origin_lat",
    "origin_lon",
    "facility_id",
    "facility_name",
    "facility_class",
    "facility_province",
    "facility_lat",
    "facility_lon",
    "facility_status",
    "evidence_tier",
    "haversine_km",
    "haversine_road_factor_km",
    "osrm_status",
    "osrm_http_status",
    "osrm_road_km",
    "osrm_duration_h",
    "osrm_speed_kmh",
    "road_haversine_ratio",
    "proxy_road_ratio",
    "proxy_error_km",
    "proxy_error_pct",
    "abs_proxy_error_pct",
    "origin_snap_km",
    "destination_snap_km",
    "snap_warning",
    "used_in_v6_primary",
    "error",
    "evidence_label",
    "observed",
    "policy_grade",
]
PROVINCE_FIELDS = [
    "province",
    "province_name",
    "n_pairs",
    "n_ok",
    "n_failed",
    "haversine_km_total",
    "road_km_total",
    "proxy_km_total",
    "sample_fitted_road_factor",
    "median_road_haversine_ratio",
    "median_proxy_road_ratio",
    "mean_abs_proxy_error_pct",
    "share_proxy_underestimates",
    "share_within_10_percent",
    "mean_speed_kmh",
]
PROVENANCE_FIELDS = [
    "source_id",
    "role",
    "name",
    "url",
    "licence",
    "retrieved_utc",
    "path",
    "sha256",
    "notes",
]


class RoadDistanceAuditError(RuntimeError):
    """Raised when a road-distance audit invariant fails."""


@dataclass(frozen=True)
class Facility:
    facility_id: str
    name: str
    facility_class: str
    province: str
    lat: float
    lon: float
    evidence_tier: str
    status: str


@dataclass(frozen=True)
class FacilityCensus:
    total_records: int
    mapped_records: int
    facilities: tuple[Facility, ...]

    @property
    def unmapped_records(self) -> int:
        return self.total_records - self.mapped_records


@dataclass(frozen=True)
class SamplePair:
    pair_id: str
    province: str
    stratum: int
    facility: Facility
    haversine_km: float


@dataclass(frozen=True)
class RouteResult:
    status: str
    distance_km: float | None
    duration_h: float | None
    origin_snap_km: float | None
    destination_snap_km: float | None
    http_status: int | None
    attempts: int
    error: str
    response_text: str


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))
    except FileNotFoundError as error:
        raise RoadDistanceAuditError(f"missing input: {path}") from error
    except OSError as error:
        raise RoadDistanceAuditError(f"cannot read {path}: {error}") from error


def _require_columns(
    rows: Sequence[dict[str, str]], required: set[str], path: Path
) -> None:
    if not rows:
        raise RoadDistanceAuditError(f"{path}: no data rows")
    missing = required - set(rows[0])
    if missing:
        raise RoadDistanceAuditError(
            f"{path}: missing columns {sorted(missing)}"
        )


def _finite_float(value: object, *, field: str, context: str) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as error:
        raise RoadDistanceAuditError(
            f"{context}: {field} is not numeric: {value!r}"
        ) from error
    if not math.isfinite(number):
        raise RoadDistanceAuditError(
            f"{context}: {field} is not finite: {value!r}"
        )
    return number


def _nonnegative_float(value: object, *, field: str, context: str) -> float:
    number = _finite_float(value, field=field, context=context)
    if number < 0.0:
        raise RoadDistanceAuditError(
            f"{context}: {field} cannot be negative: {number!r}"
        )
    return number


def _format_float(value: float | None, digits: int = 6) -> str:
    if value is None:
        return ""
    if not math.isfinite(value):
        raise RoadDistanceAuditError(f"refusing to write non-finite value {value!r}")
    text = f"{value:.{digits}f}".rstrip("0").rstrip(".")
    return text or "0"


def _bool_text(value: bool) -> str:
    return "true" if value else "false"


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


def parse_deletion_lines(status_text: str) -> list[str]:
    """Return porcelain lines with a ``D`` in either status column."""

    deletions: list[str] = []
    for line in status_text.splitlines():
        if len(line) < 2:
            continue
        if line[0] == "D" or line[1] == "D":
            deletions.append(line)
    return deletions


def _git_status(repository: Path) -> str:
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repository,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RoadDistanceAuditError(
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
        raise RoadDistanceAuditError(
            "unexpected deletion status detected; halting: "
            + " | ".join(incidents)
        )


def load_facilities(path: Path = CAPACITY_CSV) -> FacilityCensus:
    """Parse capacity v3 coordinates without imputing locations."""

    rows = _read_csv_rows(path)
    _require_columns(
        rows,
        {
            "facility_id",
            "name",
            "facility_class",
            "province",
            "lat",
            "lon",
            "status",
            "evidence_tier",
        },
        path,
    )
    facilities: list[Facility] = []
    seen: set[str] = set()
    mapped_records = 0
    for position, row in enumerate(rows, start=2):
        context = f"{path}:{position}"
        facility_id = row["facility_id"].strip()
        if not facility_id:
            raise RoadDistanceAuditError(f"{context}: blank facility_id")
        if facility_id in seen:
            raise RoadDistanceAuditError(
                f"{context}: duplicate facility_id {facility_id!r}"
            )
        seen.add(facility_id)
        province = row["province"].strip().upper()
        if province not in PROVINCES:
            raise RoadDistanceAuditError(
                f"{context}: unexpected province {province!r}"
            )
        facility_class = row["facility_class"].strip().upper()
        if facility_class not in RELEVANT_CLASSES + ("OTHER",):
            raise RoadDistanceAuditError(
                f"{context}: unexpected facility_class {facility_class!r}"
            )
        latitude_text = row["lat"].strip()
        longitude_text = row["lon"].strip()
        if bool(latitude_text) != bool(longitude_text):
            raise RoadDistanceAuditError(
                f"{context}: facility has only one coordinate"
            )
        if not latitude_text:
            continue
        latitude = _finite_float(latitude_text, field="lat", context=context)
        longitude = _finite_float(longitude_text, field="lon", context=context)
        if not -90.0 <= latitude <= 90.0:
            raise RoadDistanceAuditError(
                f"{context}: latitude outside [-90, 90]: {latitude!r}"
            )
        if not -180.0 <= longitude <= 180.0:
            raise RoadDistanceAuditError(
                f"{context}: longitude outside [-180, 180]: {longitude!r}"
            )
        evidence_tier = row["evidence_tier"].strip().upper()
        if evidence_tier not in {"A", "B", "C", "D"}:
            raise RoadDistanceAuditError(
                f"{context}: unexpected evidence_tier {evidence_tier!r}"
            )
        status = row["status"].strip().lower() or "unknown"
        if status not in ALLOWED_OPERATING_STATUSES:
            raise RoadDistanceAuditError(
                f"{context}: unexpected facility status {status!r}"
            )
        facilities.append(
            Facility(
                facility_id=facility_id,
                name=row["name"].strip(),
                facility_class=facility_class,
                province=province,
                lat=latitude,
                lon=longitude,
                evidence_tier=evidence_tier,
                status=status,
            )
        )
        mapped_records += 1
    if len(rows) != EXPECTED_TOTAL_RECORDS:
        raise RoadDistanceAuditError(
            f"{path}: expected {EXPECTED_TOTAL_RECORDS} records, found {len(rows)}"
        )
    if mapped_records != EXPECTED_MAPPED_RECORDS:
        raise RoadDistanceAuditError(
            f"{path}: expected {EXPECTED_MAPPED_RECORDS} mapped records, "
            f"found {mapped_records}"
        )
    return FacilityCensus(
        total_records=len(rows),
        mapped_records=mapped_records,
        facilities=tuple(facilities),
    )


def is_eligible(facility: Facility) -> bool:
    return (
        facility.facility_class in RELEVANT_CLASSES
        and facility.status not in NON_OPERATING_STATUSES
    )


def select_sample(
    census: FacilityCensus,
    centroids: dict[str, Any],
    *,
    provinces: Sequence[str] = SAMPLE_PROVINCES,
    quantiles: Sequence[float] = SAMPLE_QUANTILES,
) -> tuple[SamplePair, ...]:
    """Select a deterministic distance-stratified sample per province."""

    if len(quantiles) < 2 or any(not 0.0 <= value <= 1.0 for value in quantiles):
        raise RoadDistanceAuditError(f"invalid sample quantiles: {quantiles!r}")
    sample: list[SamplePair] = []
    for province in provinces:
        if province not in centroids:
            raise RoadDistanceAuditError(
                f"missing province centroid for {province}"
            )
        origin = centroids[province]
        located = [
            facility
            for facility in census.facilities
            if facility.province == province and is_eligible(facility)
        ]
        if len(located) < len(quantiles):
            raise RoadDistanceAuditError(
                f"{province}: only {len(located)} eligible facilities for "
                f"{len(quantiles)} strata"
            )
        located.sort(
            key=lambda facility: (
                haversine_km(
                    origin.lat,
                    origin.lon,
                    facility.lat,
                    facility.lon,
                ),
                facility.facility_id,
            )
        )
        selected: list[int] = []
        for quantile in quantiles:
            index = round(quantile * (len(located) - 1))
            while index in selected:
                index = (index + 1) % len(located)
            selected.append(index)
        for stratum, index in enumerate(selected, start=1):
            facility = located[index]
            pair_id = f"{province}-{stratum:02d}"
            sample.append(
                SamplePair(
                    pair_id=pair_id,
                    province=province,
                    stratum=stratum,
                    facility=facility,
                    haversine_km=haversine_km(
                        origin.lat,
                        origin.lon,
                        facility.lat,
                        facility.lon,
                    ),
                )
            )
    if len({pair.facility.facility_id for pair in sample}) != len(sample):
        raise RoadDistanceAuditError("sample contains duplicate facilities")
    return tuple(sample)


def load_v6_used_facilities(path: Path = V6_ASSIGNMENT_CSV) -> frozenset[str]:
    """Facilities receiving volume in the v6 primary scenario, if present."""

    if not path.is_file():
        return frozenset()
    rows = _read_csv_rows(path)
    _require_columns(rows, {"scenario", "facility_id"}, path)
    return frozenset(
        row["facility_id"].strip()
        for row in rows
        if row["scenario"].strip() == "province_capacity_floor"
    )


def osrm_route_url(origin: Any, facility: Facility) -> str:
    coordinates = (
        f"{origin.lon:.6f},{origin.lat:.6f};"
        f"{facility.lon:.6f},{facility.lat:.6f}"
    )
    return (
        f"{OSRM_BASE_URL}/route/v1/{OSRM_PROFILE}/{coordinates}"
        "?overview=false&alternatives=false&steps=false"
    )


def _snap_distance_km(waypoints: Sequence[dict[str, Any]], index: int) -> float | None:
    if len(waypoints) <= index:
        return None
    value = waypoints[index].get("distance")
    if value is None:
        return None
    return _nonnegative_float(
        value,
        field="snap distance",
        context=f"OSRM waypoint {index}",
    ) / 1000.0


def _parse_osrm_payload(
    payload_text: str,
    *,
    expected_haversine_km: float,
    http_status: int,
    attempts: int,
) -> RouteResult:
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError as error:
        return RouteResult(
            status="failed",
            distance_km=None,
            duration_h=None,
            origin_snap_km=None,
            destination_snap_km=None,
            http_status=http_status,
            attempts=attempts,
            error=f"invalid OSRM JSON: {error}",
            response_text=payload_text,
        )
    if payload.get("code") != "Ok":
        return RouteResult(
            status="failed",
            distance_km=None,
            duration_h=None,
            origin_snap_km=None,
            destination_snap_km=None,
            http_status=http_status,
            attempts=attempts,
            error=f"OSRM code {payload.get('code')!r}: {payload.get('message', '')}",
            response_text=payload_text,
        )
    routes = payload.get("routes") or []
    if not routes:
        return RouteResult(
            status="failed",
            distance_km=None,
            duration_h=None,
            origin_snap_km=None,
            destination_snap_km=None,
            http_status=http_status,
            attempts=attempts,
            error="OSRM returned no route",
            response_text=payload_text,
        )
    route = routes[0]
    try:
        distance_km = _nonnegative_float(
            route.get("distance"), field="distance", context="OSRM route"
        ) / 1000.0
        duration_h = _nonnegative_float(
            route.get("duration"), field="duration", context="OSRM route"
        ) / 3600.0
    except RoadDistanceAuditError as error:
        return RouteResult(
            status="failed",
            distance_km=None,
            duration_h=None,
            origin_snap_km=None,
            destination_snap_km=None,
            http_status=http_status,
            attempts=attempts,
            error=str(error),
            response_text=payload_text,
        )
    waypoints = payload.get("waypoints") or []
    origin_snap_km = _snap_distance_km(waypoints, 0)
    destination_snap_km = _snap_distance_km(waypoints, 1)
    error = ""
    if distance_km <= 0.0 and expected_haversine_km > 0.2:
        error = "OSRM returned zero distance for a non-zero pair"
    elif duration_h <= 0.0 and distance_km > 0.0:
        error = "OSRM returned zero duration for a non-zero route"
    elif distance_km > MAX_PLAUSIBLE_ROAD_KM:
        error = f"OSRM distance exceeds {MAX_PLAUSIBLE_ROAD_KM:.0f} km"
    elif (
        expected_haversine_km > 0.0
        and distance_km < expected_haversine_km * MIN_ROAD_TO_HAVERSINE
    ):
        error = (
            "OSRM distance is implausibly below the great-circle distance "
            f"({distance_km:.3f} < {expected_haversine_km:.3f} km)"
        )
    if error:
        return RouteResult(
            status="failed",
            distance_km=None,
            duration_h=None,
            origin_snap_km=origin_snap_km,
            destination_snap_km=destination_snap_km,
            http_status=http_status,
            attempts=attempts,
            error=error,
            response_text=payload_text,
        )
    return RouteResult(
        status="ok",
        distance_km=distance_km,
        duration_h=duration_h,
        origin_snap_km=origin_snap_km,
        destination_snap_km=destination_snap_km,
        http_status=http_status,
        attempts=attempts,
        error="",
        response_text=payload_text,
    )


def fetch_osrm_route(
    url: str,
    *,
    expected_haversine_km: float,
    opener: Callable[..., Any] = urllib.request.urlopen,
    sleeper: Callable[[float], None] = time.sleep,
) -> RouteResult:
    """Fetch one route with bounded retries; never invent a distance."""

    last_error = "route request was not attempted"
    last_status: int | None = None
    last_body = ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        request = urllib.request.Request(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        try:
            with opener(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                status = int(getattr(response, "status", 200))
                body = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as error:
            status = int(error.code)
            body = error.read().decode("utf-8", errors="replace")
            last_error = f"HTTP {status}: {body[:200]}"
            last_status = status
            last_body = body
            if status == 429 or 500 <= status < 600:
                sleeper(2.0**attempt)
                continue
            return RouteResult(
                status="failed",
                distance_km=None,
                duration_h=None,
                origin_snap_km=None,
                destination_snap_km=None,
                http_status=status,
                attempts=attempt,
                error=last_error,
                response_text=body,
            )
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            last_error = f"{type(error).__name__}: {error}"
            if attempt < MAX_ATTEMPTS:
                sleeper(2.0**attempt)
            continue
        result = _parse_osrm_payload(
            body,
            expected_haversine_km=expected_haversine_km,
            http_status=status,
            attempts=attempt,
        )
        if result.status == "ok":
            return result
        last_error = result.error
        last_status = status
        last_body = body
        if status == 429 or 500 <= status < 600:
            sleeper(2.0**attempt)
            continue
        return result
    return RouteResult(
        status="failed",
        distance_km=None,
        duration_h=None,
        origin_snap_km=None,
        destination_snap_km=None,
        http_status=last_status,
        attempts=MAX_ATTEMPTS,
        error=last_error,
        response_text=last_body,
    )


def _pair_row(
    pair: SamplePair,
    origin: Any,
    route: RouteResult,
    *,
    used_in_v6: bool,
) -> dict[str, Any]:
    haversine = pair.haversine_km
    proxy_km = haversine * ROAD_FACTOR
    row: dict[str, Any] = {
        "pair_id": pair.pair_id,
        "province": pair.province,
        "distance_stratum": pair.stratum,
        "origin_lat": _format_float(origin.lat),
        "origin_lon": _format_float(origin.lon),
        "facility_id": pair.facility.facility_id,
        "facility_name": pair.facility.name,
        "facility_class": pair.facility.facility_class,
        "facility_province": pair.facility.province,
        "facility_lat": _format_float(pair.facility.lat),
        "facility_lon": _format_float(pair.facility.lon),
        "facility_status": pair.facility.status,
        "evidence_tier": pair.facility.evidence_tier,
        "haversine_km": _format_float(haversine),
        "haversine_road_factor_km": _format_float(proxy_km),
        "osrm_status": route.status,
        "osrm_http_status": route.http_status if route.http_status is not None else "",
        "osrm_road_km": _format_float(route.distance_km),
        "osrm_duration_h": _format_float(route.duration_h),
        "origin_snap_km": _format_float(route.origin_snap_km),
        "destination_snap_km": _format_float(route.destination_snap_km),
        "snap_warning": _bool_text(
            route.status == "ok"
            and max(route.origin_snap_km or 0.0, route.destination_snap_km or 0.0)
            > SNAP_WARNING_KM
        ),
        "used_in_v6_primary": _bool_text(used_in_v6),
        "error": route.error,
        "evidence_label": EVIDENCE_LABEL,
        "observed": _bool_text(OBSERVED),
        "policy_grade": _bool_text(POLICY_GRADE),
    }
    if route.status != "ok" or route.distance_km is None or route.duration_h is None:
        row.update(
            {
                "osrm_speed_kmh": "",
                "road_haversine_ratio": "",
                "proxy_road_ratio": "",
                "proxy_error_km": "",
                "proxy_error_pct": "",
                "abs_proxy_error_pct": "",
            }
        )
        return row
    road_km = route.distance_km
    duration_h = route.duration_h
    road_haversine_ratio = road_km / haversine
    proxy_road_ratio = proxy_km / road_km
    proxy_error_km = proxy_km - road_km
    proxy_error_pct = 100.0 * proxy_error_km / road_km
    row.update(
        {
            "osrm_speed_kmh": _format_float(road_km / duration_h),
            "road_haversine_ratio": _format_float(road_haversine_ratio, digits=9),
            "proxy_road_ratio": _format_float(proxy_road_ratio, digits=9),
            "proxy_error_km": _format_float(proxy_error_km),
            "proxy_error_pct": _format_float(proxy_error_pct, digits=6),
            "abs_proxy_error_pct": _format_float(abs(proxy_error_pct), digits=6),
        }
    )
    return row


def _percentile(values: Sequence[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = fraction * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _distribution(values: Sequence[float]) -> dict[str, Any]:
    if not values:
        return {
            "n": 0,
            "mean": None,
            "median": None,
            "p25": None,
            "p75": None,
            "min": None,
            "max": None,
        }
    return {
        "n": len(values),
        "mean": statistics.fmean(values),
        "median": statistics.median(values),
        "p25": _percentile(values, 0.25),
        "p75": _percentile(values, 0.75),
        "min": min(values),
        "max": max(values),
    }


def _comparison(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    successful = [row for row in rows if row["osrm_status"] == "ok"]
    haversine_values = [float(row["haversine_km"]) for row in successful]
    road_values = [float(row["osrm_road_km"]) for row in successful]
    proxy_values = [float(row["haversine_road_factor_km"]) for row in successful]
    road_haversine_ratios = [
        float(row["road_haversine_ratio"]) for row in successful
    ]
    proxy_road_ratios = [float(row["proxy_road_ratio"]) for row in successful]
    proxy_errors = [float(row["proxy_error_pct"]) for row in successful]
    absolute_errors = [abs(value) for value in proxy_errors]
    speeds = [float(row["osrm_speed_kmh"]) for row in successful]
    durations = [float(row["osrm_duration_h"]) for row in successful]
    haversine_total = math.fsum(haversine_values)
    road_total = math.fsum(road_values)
    return {
        "n_ok": len(successful),
        "n_failed": len(rows) - len(successful),
        "haversine_km_total": haversine_total,
        "road_km_total": road_total,
        "proxy_km_total": math.fsum(proxy_values),
        "sample_fitted_road_factor": (
            road_total / haversine_total if haversine_total > 0.0 else None
        ),
        "road_haversine_ratio": _distribution(road_haversine_ratios),
        "proxy_road_ratio": _distribution(proxy_road_ratios),
        "proxy_error_pct": _distribution(proxy_errors),
        "abs_proxy_error_pct": _distribution(absolute_errors),
        "share_proxy_underestimates": (
            math.fsum(1.0 for value in proxy_errors if value < 0.0)
            / len(proxy_errors)
            if proxy_errors
            else None
        ),
        "share_within_10_percent": (
            math.fsum(1.0 for value in absolute_errors if value <= 10.0)
            / len(absolute_errors)
            if absolute_errors
            else None
        ),
        "duration_h": _distribution(durations),
        "speed_kmh": _distribution(speeds),
    }


def _province_rows(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for province in SAMPLE_PROVINCES:
        subset = [row for row in rows if row["province"] == province]
        comparison = _comparison(subset)
        result.append(
            {
                "province": province,
                "province_name": PROVINCES[province],
                "n_pairs": len(subset),
                "n_ok": comparison["n_ok"],
                "n_failed": comparison["n_failed"],
                "haversine_km_total": _format_float(
                    comparison["haversine_km_total"]
                ),
                "road_km_total": _format_float(comparison["road_km_total"]),
                "proxy_km_total": _format_float(comparison["proxy_km_total"]),
                "sample_fitted_road_factor": _format_float(
                    comparison["sample_fitted_road_factor"]
                ),
                "median_road_haversine_ratio": _format_float(
                    comparison["road_haversine_ratio"]["median"], digits=9
                ),
                "median_proxy_road_ratio": _format_float(
                    comparison["proxy_road_ratio"]["median"], digits=9
                ),
                "mean_abs_proxy_error_pct": _format_float(
                    comparison["abs_proxy_error_pct"]["mean"], digits=6
                ),
                "share_proxy_underestimates": _format_float(
                    comparison["share_proxy_underestimates"], digits=6
                ),
                "share_within_10_percent": _format_float(
                    comparison["share_within_10_percent"], digits=6
                ),
                "mean_speed_kmh": _format_float(
                    comparison["speed_kmh"]["mean"], digits=6
                ),
            }
        )
    return result


def _route_snapshot(
    pair: SamplePair,
    url: str,
    route: RouteResult,
    *,
    requested_at_utc: str,
) -> dict[str, Any]:
    return {
        "pair_id": pair.pair_id,
        "province": pair.province,
        "facility_id": pair.facility.facility_id,
        "request_url": url,
        "requested_at_utc": requested_at_utc,
        "status": route.status,
        "http_status": route.http_status,
        "attempts": route.attempts,
        "error": route.error,
        "response_sha256": hashlib.sha256(
            route.response_text.encode("utf-8")
        ).hexdigest(),
        "response_raw": route.response_text,
    }


def _validation(
    *,
    sample: Sequence[SamplePair],
    rows: Sequence[dict[str, Any]],
    census: FacilityCensus,
    summary: dict[str, Any],
) -> dict[str, bool]:
    expected_pairs = len(SAMPLE_PROVINCES) * SAMPLE_PER_PROVINCE
    successful = [row for row in rows if row["osrm_status"] == "ok"]
    ratio_errors: list[str] = []
    numeric_errors: list[str] = []
    for row in successful:
        haversine = float(row["haversine_km"])
        road = float(row["osrm_road_km"])
        duration = float(row["osrm_duration_h"])
        proxy = float(row["haversine_road_factor_km"])
        road_haversine_ratio = float(row["road_haversine_ratio"])
        proxy_road_ratio = float(row["proxy_road_ratio"])
        proxy_error_pct = float(row["proxy_error_pct"])
        if abs(road_haversine_ratio - road / haversine) > 1e-6:
            ratio_errors.append(f"{row['pair_id']}: road/haversine")
        if abs(proxy_road_ratio - proxy / road) > 1e-6:
            ratio_errors.append(f"{row['pair_id']}: proxy/road")
        if abs(proxy_error_pct - 100.0 * (proxy - road) / road) > 1e-6:
            ratio_errors.append(f"{row['pair_id']}: proxy error")
        values = [haversine, road, duration, proxy]
        if any(not math.isfinite(value) or value < 0.0 for value in values):
            numeric_errors.append(row["pair_id"])
    return {
        "sample_pair_count": len(sample) == expected_pairs,
        "sample_provinces": set(summary["sample"]["provinces"])
        == set(SAMPLE_PROVINCES),
        "per_province_count": all(
            summary["sample"]["per_province_counts"].get(province)
            == SAMPLE_PER_PROVINCE
            for province in SAMPLE_PROVINCES
        ),
        "unique_facilities": len({pair.facility.facility_id for pair in sample})
        == len(sample),
        "positive_haversine": all(pair.haversine_km > 0.0 for pair in sample),
        "facility_census": (
            census.total_records == EXPECTED_TOTAL_RECORDS
            and census.mapped_records == EXPECTED_MAPPED_RECORDS
        ),
        "ratio_arithmetic": not ratio_errors,
        "no_nan_or_negative": not numeric_errors,
        "labels": (
            summary["labels"]["evidence_label"] == EVIDENCE_LABEL
            and summary["labels"]["observed"] is False
            and summary["labels"]["policy_grade"] is False
        ),
        "no_assignment_change": (
            summary["labels"]["assignment_change"] is False
            and summary["labels"]["national_claim"] is False
        ),
        "repository_hygiene": True,
    }


def _report(summary: dict[str, Any], rows: Sequence[dict[str, Any]]) -> str:
    sample = summary["sample"]
    overall = summary["comparison"]["overall"]
    lines = [
        "# Facility road-distance audit v1",
        "",
        f"Generated: {summary['generated_utc']}",
        "",
        ("Status: **model estimate**. This audit does not change the facility "
        "assignment and makes no national claim from its 24-pair sample."),
        "",
        "## Routing source",
        "",
        f"- Provider: {summary['routing']['provider']}",
        (f"- Endpoint: `{summary['routing']['base_url']}` "
        f"({summary['routing']['profile']} profile)"),
        (f"- Road data: {summary['routing']['osm_data']} "
        f"({summary['routing']['licence']}, {summary['routing']['attribution']})"),
        (f"- Requests: {summary['routing']['requests_ok']} ok / "
        f"{summary['routing']['requests_attempted']} attempted; "
        f"{summary['routing']['requests_failed']} failed."),
        f"- Rate limit: one request every {RATE_LIMIT_SECONDS:.1f} s.",
        "",
        "## Sample",
        "",
        (f"- Facilities sampled: {sample['n_pairs']} "
        f"({SAMPLE_PER_PROVINCE} per province across "
        f"{', '.join(SAMPLE_PROVINCES)})."),
        (f"- Facility universe: {sample['universe_records']} records, "
        f"{sample['universe_mapped_records']} mapped; "
        f"{sample['universe_unmapped_records']} unmapped records excluded."),
        ("- Selection: deterministic distance quantiles "
        f"{', '.join(f'{q:.3f}' for q in SAMPLE_QUANTILES)} of eligible "
        "facilities per province. This is a stratified sample, not a random "
        "national sample."),
        "",
        "## Comparison",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Successful routes | {overall['n_ok']} / {sample['n_pairs']} |",
        (f"| Median road / haversine | "
        f"{_format_float(overall['road_haversine_ratio']['median'], 6)} |"),
        (f"| Median proxy / road | "
        f"{_format_float(overall['proxy_road_ratio']['median'], 6)} |"),
        (f"| Mean absolute proxy error | "
        f"{_format_float(overall['abs_proxy_error_pct']['mean'], 6)} % |"),
        (f"| Sample-fitted road factor (sum road / sum haversine) | "
        f"{_format_float(overall['sample_fitted_road_factor'], 6)} |"),
        (f"| Proxy underestimates road | "
        f"{_format_float(overall['share_proxy_underestimates'], 6)} |"),
        (f"| Within 10% of road distance | "
        f"{_format_float(overall['share_within_10_percent'], 6)} |"),
        "",
        ("Positive proxy error means `haversine x 1.3` exceeds the OSRM road "
        "distance (conservative for ranking); negative means it underestimates."),
        "",
        "## Province summary",
        "",
        ("| Province | Pairs | OK | Median road/hav | Median proxy/road | "
        "Mean abs error % | Fitted factor |"),
        "|---|---|---|---|---|---|---|",
    ]
    for row in summary["comparison"]["by_province"]:
        lines.append(
            f"| {row['province']} | {row['n_pairs']} | {row['n_ok']} | "
            f"{row['median_road_haversine_ratio'] or 'n/a'} | "
            f"{row['median_proxy_road_ratio'] or 'n/a'} | "
            f"{row['mean_abs_proxy_error_pct'] or 'n/a'} | "
            f"{row['sample_fitted_road_factor'] or 'n/a'} |"
        )
    lines.extend(
        [
            "",
            "## Sample detail",
            "",
            ("| Pair | Facility | Class | Haversine km | OSRM km | "
            "Road/hav | Proxy/road | Proxy error % | Status |"),
            "|---|---|---|---|---|---|---|---|---|",
        ]
    )
    for row in rows:
        lines.append(
            f"| {row['pair_id']} | "
            f"{(row['facility_name'] or row['facility_id'])[:34]} | "
            f"{row['facility_class']} | {row['haversine_km']} | "
            f"{row['osrm_road_km'] or 'n/a'} | "
            f"{row['road_haversine_ratio'] or 'n/a'} | "
            f"{row['proxy_road_ratio'] or 'n/a'} | "
            f"{row['proxy_error_pct'] or 'n/a'} | {row['osrm_status']} |"
        )
    lines.extend(
        [
            "",
            "## Failures and limits",
            "",
            ("- Failures are reported with their exact OSRM error; no distance "
            "is imputed."),
            (
                f"- Endpoint snap distances are recorded. "
                f"{sum(1 for row in rows if row['snap_warning'] == 'true')} "
                f"pair(s) snap more than {SNAP_WARNING_KM:.0f} km from the "
                "requested coordinate."
            ),
            (
                "- OSRM routes start and end at the nearest road point; the "
                "off-road connector from the province centroid or facility "
                "coordinate is not included in the routed distance. Because a "
                "connector would only add distance, the finding that the 1.3 "
                "factor underestimates road distance is conservative for these "
                "sampled centroid-to-facility pairs."
            ),
            ("- OSRM demo is a public best-effort service; it is suitable for "
            "this 24-request audit, not for production or bulk routing."),
            ("- The OSRM car profile may route through the United States where "
            "that is the shortest road path; no border delay or customs cost "
            "is modelled."),
            ("- Only car-road distance/duration is measured. Resource-road, "
            "winter, ferry, weight, and log-truck restrictions are not "
            "validated."),
            ("- The sample is distance-stratified and province-stratified; it "
            "does not support a national mean or a replacement road factor."),
            "",
            "## Labels",
            "",
            "- `model_estimate`; `observed=false`; `policy_grade=false`.",
            "- No assignment change; no legal, AAC, or operating-status claim.",
            "- No UI or public OSeCAN surface is changed.",
            "",
            "## Provenance",
            "",
        ]
    )
    for source in summary["provenance"]["sources"]:
        location = f"`{source['path']}` " if source["path"] else ""
        lines.append(
            f"- {source['name']}: {location}"
            f"({source['licence']}); `{source['url']}`"
        )
    lines.extend(
        [
            "",
            ("Reproduce: "
            "`.venv/bin/python -m "
            "workflows.national.facilities.facility_road_distance_audit`"),
            "",
        ]
    )
    return "\n".join(lines)


def build(
    output_dir: Path = OUTPUT_DIR,
    *,
    route_fetcher: Callable[..., RouteResult] = fetch_osrm_route,
    sleeper: Callable[[float], None] = time.sleep,
    deletion_checker: Callable[[], None] = assert_no_unexpected_deletions,
) -> dict[str, Any]:
    """Run the audit and write the data artifact."""

    deletion_checker()
    generated_utc = datetime.now(UTC).replace(microsecond=0).isoformat()
    census = load_facilities()
    centroids, centroid_methods = load_province_centroids(BOUNDARY_GEOJSON)
    sample = select_sample(census, centroids)
    used_in_v6 = load_v6_used_facilities()

    rows: list[dict[str, Any]] = []
    snapshots: list[dict[str, Any]] = []
    for position, pair in enumerate(sample):
        origin = centroids[pair.province]
        url = osrm_route_url(origin, pair.facility)
        route = route_fetcher(url, expected_haversine_km=pair.haversine_km)
        rows.append(
            _pair_row(
                pair,
                origin,
                route,
                used_in_v6=pair.facility.facility_id in used_in_v6,
            )
        )
        snapshots.append(
            _route_snapshot(
                pair,
                url,
                route,
                requested_at_utc=datetime.now(UTC)
                .replace(microsecond=0)
                .isoformat(),
            )
        )
        if position < len(sample) - 1:
            sleeper(RATE_LIMIT_SECONDS)

    successful = [row for row in rows if row["osrm_status"] == "ok"]
    if not successful:
        errors = "; ".join(
            f"{row['pair_id']}: {row['error']}" for row in rows[:3]
        )
        raise RoadDistanceAuditError(
            "all OSRM route requests failed; no distances were fabricated. "
            f"First failures: {errors}"
        )

    overall = _comparison(rows)
    province_rows = _province_rows(rows)
    by_province = [
        {
            **row,
            "n_pairs": int(row["n_pairs"]),
            "n_ok": int(row["n_ok"]),
            "n_failed": int(row["n_failed"]),
            "haversine_km_total": float(row["haversine_km_total"]),
            "road_km_total": float(row["road_km_total"]),
            "proxy_km_total": float(row["proxy_km_total"]),
            "sample_fitted_road_factor": (
                float(row["sample_fitted_road_factor"])
                if row["sample_fitted_road_factor"]
                else None
            ),
            "median_road_haversine_ratio": (
                float(row["median_road_haversine_ratio"])
                if row["median_road_haversine_ratio"]
                else None
            ),
            "median_proxy_road_ratio": (
                float(row["median_proxy_road_ratio"])
                if row["median_proxy_road_ratio"]
                else None
            ),
            "mean_abs_proxy_error_pct": (
                float(row["mean_abs_proxy_error_pct"])
                if row["mean_abs_proxy_error_pct"]
                else None
            ),
            "share_proxy_underestimates": (
                float(row["share_proxy_underestimates"])
                if row["share_proxy_underestimates"]
                else None
            ),
            "share_within_10_percent": (
                float(row["share_within_10_percent"])
                if row["share_within_10_percent"]
                else None
            ),
            "mean_speed_kmh": (
                float(row["mean_speed_kmh"]) if row["mean_speed_kmh"] else None
            ),
        }
        for row in province_rows
    ]
    failures = [
        {
            "pair_id": row["pair_id"],
            "facility_id": row["facility_id"],
            "http_status": row["osrm_http_status"],
            "error": row["error"],
        }
        for row in rows
        if row["osrm_status"] != "ok"
    ]
    origin_snaps = [
        float(row["origin_snap_km"])
        for row in successful
        if row["origin_snap_km"] != ""
    ]
    destination_snaps = [
        float(row["destination_snap_km"])
        for row in successful
        if row["destination_snap_km"] != ""
    ]

    summary: dict[str, Any] = {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": generated_utc,
        "status": "complete" if not failures else "partial",
        "labels": {
            "evidence_label": EVIDENCE_LABEL,
            "observed": OBSERVED,
            "policy_grade": POLICY_GRADE,
            "assignment_change": ASSIGNMENT_CHANGE,
            "national_claim": NATIONAL_CLAIM,
        },
        "routing": {
            "provider": "OSRM public demo server",
            "base_url": OSRM_BASE_URL,
            "profile": OSRM_PROFILE,
            "osm_data": "OpenStreetMap road network",
            "licence": OSRM_LICENCE,
            "attribution": OSRM_ATTRIBUTION,
            "usage_policy": (
                "Public demo server, best-effort; 24 low-rate requests for this "
                "audit only. No paid API key."
            ),
            "requests_attempted": len(sample),
            "requests_ok": len(successful),
            "requests_failed": len(failures),
            "rate_limit_seconds": RATE_LIMIT_SECONDS,
            "max_attempts": MAX_ATTEMPTS,
            "user_agent": USER_AGENT,
            "snap": {
                "warning_threshold_km": SNAP_WARNING_KM,
                "origin_snap_km": _distribution(origin_snaps),
                "destination_snap_km": _distribution(destination_snaps),
                "pairs_over_warning_threshold": sum(
                    1 for row in successful if row["snap_warning"] == "true"
                ),
            },
            "failures": failures,
        },
        "sample": {
            "provinces": list(SAMPLE_PROVINCES),
            "per_province": SAMPLE_PER_PROVINCE,
            "per_province_counts": {
                province: sum(1 for pair in sample if pair.province == province)
                for province in SAMPLE_PROVINCES
            },
            "n_pairs": len(sample),
            "selection": (
                "deterministic eligible-facility distance quantiles "
                f"{list(SAMPLE_QUANTILES)} per province"
            ),
            "universe_records": census.total_records,
            "universe_mapped_records": census.mapped_records,
            "universe_unmapped_records": census.unmapped_records,
            "eligible_filter": (
                "facility_class in "
                f"{list(RELEVANT_CLASSES)} and status not in "
                f"{sorted(NON_OPERATING_STATUSES)}"
            ),
            "centroid_methods": centroid_methods,
        },
        "comparison": {
            "road_factor": ROAD_FACTOR,
            "overall": overall,
            "by_province": by_province,
        },
        "validation": {},
        "caveats": [
            ("All values are model estimates (`model_estimate`); "
            "`observed=false` and `policy_grade=false`."),
            ("The 24-pair sample is distance-stratified and province-stratified; "
            "it is not a random national sample and does not support a national "
            "mean or a replacement road factor."),
            ("OSRM demo is a public best-effort service; results are not a "
            "production routing service or a guarantee of current road "
            "conditions."),
            ("Distances are car-road distances. Resource roads, winter roads, "
            "ferries, weight limits, and log-truck restrictions are not "
            "validated."),
            ("OSRM may route through the United States where that is the "
            "shortest road path; no border delay or customs cost is modelled."),
            (
                "Endpoint snap distances are reported; large snaps indicate "
                "that the province centroid or facility coordinate is not on "
                "the road network. OSRM routes omit the off-road connector "
                "from the requested coordinate to the snapped road point, so "
                "the routed distance is a lower-bound comparison for these "
                "centroid-to-facility pairs."
            ),
            ("No facility assignment is changed and no UI or public OSeCAN "
            "surface is touched."),
        ],
        "reproduce": (
            ".venv/bin/python -m "
            "workflows.national.facilities.facility_road_distance_audit"
        ),
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(output_dir / "facility_road_distance_sample.csv", SAMPLE_FIELDS, rows)
    _write_csv(output_dir / "province_road_distance_summary.csv", PROVINCE_FIELDS, province_rows)
    _write_json(output_dir / "osrm_routes.json", {"routes": snapshots})
    validation = _validation(
        sample=sample,
        rows=rows,
        census=census,
        summary=summary,
    )
    summary["validation"] = {
        "checks": validation,
        "all_passed": all(validation.values()),
    }
    if not summary["validation"]["all_passed"]:
        raise RoadDistanceAuditError(
            f"validation failed: {summary['validation']['checks']}"
        )

    input_specs = [
        (
            "national_facility_capacity_v3",
            "input",
            "National facility capacity v3",
            CAPACITY_CSV,
            "Facility coordinates and eligibility fields",
            "project-derived; see source metadata",
        ),
        (
            "canada_adm1_geoboundaries_simplified",
            "input",
            "GeoBoundaries Canada ADM1 simplified",
            BOUNDARY_GEOJSON,
            "Province ADM1 centroids for origins",
            "CC BY 4.0",
        ),
    ]
    if V6_ASSIGNMENT_CSV.is_file():
        input_specs.append(
            (
                "national_facility_assignment_v6",
                "context",
                "National facility assignment v6",
                V6_ASSIGNMENT_CSV,
                "Context only: marks facilities used in the v6 primary run",
                "project-derived; see source metadata",
            )
        )
    sources = [
        {
            "source_id": source_id,
            "role": role,
            "name": name,
            "url": (
                "https://github.com/UBC-FRESH/clews-c2070-fresh-data"
                if role == "input"
                else "https://github.com/UBC-FRESH/clews-c2070-fresh"
            ),
            "licence": licence,
            "retrieved_utc": generated_utc,
            "path": str(path.relative_to(REPO_ROOT)),
            "sha256": _sha256(path),
            "notes": notes,
        }
        for source_id, role, name, path, notes, licence in input_specs
    ]
    sources.extend(
        [
            {
                "source_id": "osrm_demo",
                "role": "routing",
                "name": "OSRM public demo server",
                "url": f"{OSRM_BASE_URL}/route/v1/{OSRM_PROFILE}",
                "licence": OSRM_LICENCE,
                "retrieved_utc": generated_utc,
                "path": "osrm_routes.json",
                "sha256": _sha256(output_dir / "osrm_routes.json"),
                "notes": (
                    "Route distance and duration for the sample; raw responses "
                    "stored in osrm_routes.json"
                ),
            },
            {
                "source_id": "openstreetmap",
                "role": "road_data",
                "name": "OpenStreetMap contributors",
                "url": "https://www.openstreetmap.org/copyright",
                "licence": OSRM_LICENCE,
                "retrieved_utc": generated_utc,
                "path": "",
                "sha256": "",
                "notes": (
                    "Road network used by the OSRM demo server; attribution "
                    f"{OSRM_ATTRIBUTION}"
                ),
            },
        ]
    )
    _write_csv(output_dir / "provenance.csv", PROVENANCE_FIELDS, sources)
    summary["provenance"] = {"sources": sources}
    summary["inputs"] = {
        source["source_id"]: {
            "path": source["path"],
            "sha256": source["sha256"],
            "role": source["role"],
        }
        for source in sources
        if source["role"] in {"input", "context"}
    }
    _write_json(output_dir / "summary.json", summary)
    (output_dir / "FACILITY_ROAD_DISTANCE_AUDIT_V1.md").write_text(
        _report(summary, rows), encoding="utf-8"
    )
    manifest = {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": generated_utc,
        "labels": summary["labels"],
        "inputs": summary["inputs"],
        "outputs": {
            path.name: {"bytes": path.stat().st_size, "sha256": _sha256(path)}
            for path in sorted(output_dir.iterdir())
            if path.is_file() and path.name != "manifest.json"
        },
        "routing": {
            key: summary["routing"][key]
            for key in (
                "provider",
                "base_url",
                "profile",
                "licence",
                "attribution",
                "requests_attempted",
                "requests_ok",
                "requests_failed",
                "rate_limit_seconds",
            )
        },
        "sample": {
            "n_pairs": summary["sample"]["n_pairs"],
            "provinces": summary["sample"]["provinces"],
            "per_province": summary["sample"]["per_province"],
        },
        "validation": summary["validation"],
        "reproduce": summary["reproduce"],
    }
    _write_json(output_dir / "manifest.json", manifest)
    return summary


def main() -> None:
    summary = build()
    print(
        json.dumps(
            {
                "artifact": summary["artifact"],
                "status": summary["status"],
                "n_pairs": summary["sample"]["n_pairs"],
                "n_ok": summary["comparison"]["overall"]["n_ok"],
                "n_failed": summary["routing"]["requests_failed"],
                "median_road_haversine_ratio": summary["comparison"]["overall"][
                    "road_haversine_ratio"
                ]["median"],
                "median_proxy_road_ratio": summary["comparison"]["overall"][
                    "proxy_road_ratio"
                ]["median"],
                "mean_abs_proxy_error_pct": summary["comparison"]["overall"][
                    "abs_proxy_error_pct"
                ]["mean"],
                "validation": summary["validation"]["all_passed"],
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
