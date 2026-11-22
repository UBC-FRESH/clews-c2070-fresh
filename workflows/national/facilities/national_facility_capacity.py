"""Build national facility capacity v1 from published BC capacities.

The inventory v0 publishes capacity for 179 BC records and leaves the other
307 records blank.  This builder keeps every published value and fills the
blank relevant-class records with a **class-median proxy** anchored on the BC
published distribution.  It never invents a facility-level measurement, and a
class without a BC anchor is left explicitly unavailable instead of being
given a made-up number.

Labels carried by the artifact: ``model_estimate`` / ``observed=false`` /
``policy_grade=false``.  No legal or AAC claim is made.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from workflows.national.facilities.national_facility_inventory import PROVINCES

REPO_ROOT = Path(__file__).resolve().parents[3]
INVENTORY_CSV = (
    REPO_ROOT
    / "data/derived/national/facilities/national_facility_inventory_v0"
    / "national_facilities.csv"
)
OUTPUT_DIR = (
    REPO_ROOT
    / "data/derived/national/facilities/national_facility_capacity_v1"
)

ARTIFACT = "national_facility_capacity_v1"
ARTIFACT_VERSION = "1.0.0"

CLASS_ORDER = ("SAW", "PULP", "PANEL", "PELLET", "BIO", "OTHER")
RELEVANT_CLASSES = ("SAW", "PULP", "PANEL", "PELLET", "BIO")
ANCHOR_PROVINCE = "BC"
MIN_ANCHOR_RECORDS = 5
ANCHOR_QUANTILE_METHOD = "linear_interpolation_r7"

BC_SOURCE_ID = "bc_mills_db_2015"
PROXY_SOURCE_ID = "class_median_proxy_v1"

EVIDENCE_LABEL = "model_estimate"
OBSERVED = False
POLICY_GRADE = False

PUBLISHED = "published"
PROXY = "proxy"
NOT_PUBLISHED = "not_published"

CAPACITY_FIELDS = [
    "facility_id",
    "name",
    "facility_class",
    "province",
    "lat",
    "lon",
    "capacity_m3_y",
    "capacity_status",
    "capacity_evidence",
    "proxy_low_m3_y",
    "proxy_high_m3_y",
    "anchor_class",
    "anchor_records",
    "province_scale",
    "source_id",
    "source_url",
    "licence",
    "evidence_tier",
    "status",
    "notes",
]
CLASS_MEDIAN_FIELDS = [
    "facility_class",
    "anchor_province",
    "anchor_records",
    "min_m3_y",
    "p25_m3_y",
    "median_m3_y",
    "p75_m3_y",
    "max_m3_y",
    "proxy_applied_records",
    "proxy_capacity_m3_y",
    "quantile_method",
    "notes",
]
PROVINCE_CLASS_FIELDS = [
    "province",
    "facility_class",
    "records",
    "published_records",
    "published_capacity_m3_y",
    "proxy_records",
    "proxy_capacity_m3_y",
    "unavailable_records",
    "total_capacity_m3_y",
    "capacity_coverage_share",
]
SOURCE_FIELDS = [
    "source_id",
    "title",
    "publisher",
    "source_url",
    "licence",
    "licence_url",
    "access_method",
    "retrieved_utc",
    "coverage",
    "access_status",
    "used_for_capacity",
    "notes",
]


class CapacityError(ValueError):
    """Raised when an inventory or proxy invariant fails."""


@dataclass(frozen=True, slots=True)
class InventoryRecord:
    """One validated inventory record."""

    facility_id: str
    name: str
    facility_class: str
    province: str
    lat: str
    lon: str
    capacity_m3_y: float | None
    capacity_status: str
    status: str
    source_id: str
    source_url: str
    licence: str
    evidence_tier: str


@dataclass(frozen=True, slots=True)
class ClassAnchor:
    """BC published capacity distribution for one facility class."""

    facility_class: str
    values: tuple[float, ...]
    p25_m3_y: float
    median_m3_y: float
    p75_m3_y: float

    @property
    def count(self) -> int:
        return len(self.values)

    @property
    def minimum(self) -> float:
        return self.values[0]

    @property
    def maximum(self) -> float:
        return self.values[-1]


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
        raise CapacityError(f"input file is missing: {path}") from error
    except OSError as error:
        raise CapacityError(f"cannot read {path}: {error}") from error
    if not rows:
        raise CapacityError(f"{path}: no data rows")
    return rows


def _require_columns(
    rows: Sequence[dict[str, str]], required: set[str], path: Path
) -> None:
    missing = required - set(rows[0])
    if missing:
        raise CapacityError(f"{path}: missing required columns {sorted(missing)}")


def _finite_float(value: str | None, *, field: str, context: str) -> float:
    if value is None or not value.strip():
        raise CapacityError(f"{context}: missing {field}")
    try:
        number = float(value)
    except ValueError as error:
        raise CapacityError(
            f"{context}: {field} is not numeric: {value!r}"
        ) from error
    if not math.isfinite(number):
        raise CapacityError(f"{context}: {field} is not finite: {value!r}")
    return number


def _format_float(value: float | None, digits: int = 6) -> str:
    if value is None:
        return ""
    if not math.isfinite(value):
        raise CapacityError(f"cannot format non-finite value {value!r}")
    text = f"{value:.{digits}f}".rstrip("0").rstrip(".")
    return text or "0"


def _bool_text(value: bool) -> str:
    return "true" if value else "false"


def _quantile(sorted_values: Sequence[float], quantile: float) -> float:
    """Linear-interpolation quantile (R-7) on an already sorted sequence."""

    if not sorted_values:
        raise CapacityError("cannot take a quantile of an empty sequence")
    if not 0.0 <= quantile <= 1.0:
        raise CapacityError(f"quantile must be in [0, 1], got {quantile}")
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    position = (len(sorted_values) - 1) * quantile
    lower_index = math.floor(position)
    upper_index = math.ceil(position)
    if lower_index == upper_index:
        return float(sorted_values[lower_index])
    fraction = position - lower_index
    lower = float(sorted_values[lower_index])
    upper = float(sorted_values[upper_index])
    return lower + (upper - lower) * fraction


def read_inventory(path: Path = INVENTORY_CSV) -> tuple[InventoryRecord, ...]:
    """Parse and validate national facility inventory v0."""

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
            "licence",
            "evidence_tier",
        },
        path,
    )

    records: list[InventoryRecord] = []
    seen_ids: set[str] = set()
    for position, row in enumerate(rows, start=2):
        context = f"{path}:{position}"
        facility_id = row["facility_id"].strip()
        if not facility_id:
            raise CapacityError(f"{context}: blank facility_id")
        if facility_id in seen_ids:
            raise CapacityError(f"{context}: duplicate facility_id {facility_id}")
        seen_ids.add(facility_id)

        facility_class = row["facility_class"].strip().upper()
        if facility_class not in CLASS_ORDER:
            raise CapacityError(
                f"{context}: unexpected facility_class {facility_class!r}"
            )
        province = row["province"].strip().upper()
        if province not in PROVINCES:
            raise CapacityError(f"{context}: unexpected province {province!r}")

        capacity_text = row["capacity_m3_y"].strip()
        capacity_status = row["capacity_status"].strip().lower()
        if capacity_text:
            if capacity_status != PUBLISHED:
                raise CapacityError(
                    f"{context}: capacity present but status is {capacity_status!r}"
                )
            capacity = _finite_float(
                capacity_text, field="capacity_m3_y", context=context
            )
            if capacity <= 0.0:
                raise CapacityError(f"{context}: capacity must be positive")
        else:
            if capacity_status != NOT_PUBLISHED:
                raise CapacityError(
                    f"{context}: blank capacity but status is {capacity_status!r}"
                )
            capacity = None

        raw_lat = row["lat"].strip()
        raw_lon = row["lon"].strip()
        if bool(raw_lat) != bool(raw_lon):
            raise CapacityError(
                f"{context}: latitude and longitude must both be present"
            )
        if raw_lat:
            _finite_float(raw_lat, field="lat", context=context)
            _finite_float(raw_lon, field="lon", context=context)

        evidence_tier = row["evidence_tier"].strip().upper()
        if evidence_tier not in {"A", "B", "C", "D"}:
            raise CapacityError(
                f"{context}: unexpected evidence_tier {evidence_tier!r}"
            )
        if not row["source_id"].strip():
            raise CapacityError(f"{context}: blank source_id")
        if not row["source_url"].strip():
            raise CapacityError(f"{context}: blank source_url")
        if not row["licence"].strip():
            raise CapacityError(f"{context}: blank licence")

        records.append(
            InventoryRecord(
                facility_id=facility_id,
                name=row["name"].strip(),
                facility_class=facility_class,
                province=province,
                lat=raw_lat,
                lon=raw_lon,
                capacity_m3_y=capacity,
                capacity_status=capacity_status,
                status=row["status"].strip().lower() or "unknown",
                source_id=row["source_id"].strip(),
                source_url=row["source_url"].strip(),
                licence=row["licence"].strip(),
                evidence_tier=evidence_tier,
            )
        )
    return tuple(records)


def compute_anchors(
    records: Sequence[InventoryRecord],
    *,
    anchor_province: str = ANCHOR_PROVINCE,
    min_records: int = MIN_ANCHOR_RECORDS,
) -> dict[str, ClassAnchor]:
    """Compute one BC published capacity distribution per relevant class."""

    grouped: dict[str, list[float]] = defaultdict(list)
    for record in records:
        if record.province != anchor_province:
            continue
        if record.capacity_status != PUBLISHED:
            continue
        if record.facility_class not in RELEVANT_CLASSES:
            continue
        if record.capacity_m3_y is None:
            raise CapacityError(
                f"{record.facility_id}: published status without capacity"
            )
        grouped[record.facility_class].append(record.capacity_m3_y)

    anchors: dict[str, ClassAnchor] = {}
    for facility_class in RELEVANT_CLASSES:
        values = sorted(grouped.get(facility_class, []))
        if len(values) < min_records:
            raise CapacityError(
                f"{anchor_province} anchor for {facility_class} has "
                f"{len(values)} published records; at least {min_records} are "
                "required before a class-median proxy is allowed"
            )
        p25 = _quantile(values, 0.25)
        median = _quantile(values, 0.50)
        p75 = _quantile(values, 0.75)
        anchors[facility_class] = ClassAnchor(
            facility_class=facility_class,
            values=tuple(values),
            p25_m3_y=float(round(p25)),
            median_m3_y=float(round(median)),
            p75_m3_y=float(round(p75)),
        )

    for anchor in anchors.values():
        if not (
            anchor.p25_m3_y
            <= anchor.median_m3_y
            <= anchor.p75_m3_y
        ):
            raise CapacityError(
                f"{anchor.facility_class}: anchor quantiles are not ordered"
            )
    return anchors


def _capacity_row(
    record: InventoryRecord, anchor: ClassAnchor | None
) -> dict[str, Any]:
    if record.capacity_status == PUBLISHED:
        if record.capacity_m3_y is None:
            raise CapacityError(
                f"{record.facility_id}: published status without capacity"
            )
        return {
            "facility_id": record.facility_id,
            "name": record.name,
            "facility_class": record.facility_class,
            "province": record.province,
            "lat": record.lat,
            "lon": record.lon,
            "capacity_m3_y": _format_float(record.capacity_m3_y),
            "capacity_status": PUBLISHED,
            "capacity_evidence": "published_mills_db_2015_infeed_proxy",
            "proxy_low_m3_y": "",
            "proxy_high_m3_y": "",
            "anchor_class": "",
            "anchor_records": "",
            "province_scale": "1",
            "source_id": record.source_id,
            "source_url": record.source_url,
            "licence": record.licence,
            "evidence_tier": record.evidence_tier,
            "status": record.status,
            "notes": (
                "Published capacity carried from national_facility_inventory_v0; "
                "2015 Mills DB/FFCM processing-line infeed proxy, not licensed "
                "or current operating capacity"
            ),
        }

    if anchor is None:
        return {
            "facility_id": record.facility_id,
            "name": record.name,
            "facility_class": record.facility_class,
            "province": record.province,
            "lat": record.lat,
            "lon": record.lon,
            "capacity_m3_y": "",
            "capacity_status": NOT_PUBLISHED,
            "capacity_evidence": "unavailable_no_class_anchor",
            "proxy_low_m3_y": "",
            "proxy_high_m3_y": "",
            "anchor_class": "",
            "anchor_records": "",
            "province_scale": "",
            "source_id": record.source_id,
            "source_url": record.source_url,
            "licence": record.licence,
            "evidence_tier": record.evidence_tier,
            "status": record.status,
            "notes": (
                "No published capacity and no BC class anchor for this class; "
                "capacity is left blank rather than fabricated. Excluded from "
                "proxy assignment."
            ),
        }

    return {
        "facility_id": record.facility_id,
        "name": record.name,
        "facility_class": record.facility_class,
        "province": record.province,
        "lat": record.lat,
        "lon": record.lon,
        "capacity_m3_y": _format_float(anchor.median_m3_y),
        "capacity_status": PROXY,
        "capacity_evidence": "class_median_proxy",
        "proxy_low_m3_y": _format_float(anchor.p25_m3_y),
        "proxy_high_m3_y": _format_float(anchor.p75_m3_y),
        "anchor_class": anchor.facility_class,
        "anchor_records": str(anchor.count),
        "province_scale": "1",
        "source_id": PROXY_SOURCE_ID,
        "source_url": (
            "data/derived/national/facilities/national_facility_capacity_v1/"
            "class_medians.csv"
        ),
        "licence": (
            "Model-derived proxy by UBC FRESH. Anchor values are BC 2015 "
            "Mills DB/FFCM processing-line infeed proxies; upstream source is "
            "not relicensed here. Not a facility measurement."
        ),
        "evidence_tier": record.evidence_tier,
        "status": record.status,
        "notes": (
            f"Class-median proxy: {anchor.facility_class} median "
            f"{anchor.median_m3_y:,.0f} m3/yr from {anchor.count} BC published "
            f"records; proxy range p25-p75 {anchor.p25_m3_y:,.0f}-"
            f"{anchor.p75_m3_y:,.0f} m3/yr. No provincial scaling applied "
            "(province_scale=1)."
        ),
    }


def build_capacity_rows(
    records: Sequence[InventoryRecord], anchors: dict[str, ClassAnchor]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for record in records:
        anchor = (
            anchors.get(record.facility_class)
            if record.capacity_status != PUBLISHED
            else None
        )
        rows.append(_capacity_row(record, anchor))
    return rows


def build_class_medians(
    anchors: dict[str, ClassAnchor], capacity_rows: Sequence[dict[str, Any]]
) -> list[dict[str, Any]]:
    applied: dict[str, list[float]] = defaultdict(list)
    for row in capacity_rows:
        if row["capacity_status"] == PROXY:
            applied[row["facility_class"]].append(float(row["capacity_m3_y"]))

    rows: list[dict[str, Any]] = []
    for facility_class in RELEVANT_CLASSES:
        anchor = anchors[facility_class]
        values = applied.get(facility_class, [])
        rows.append(
            {
                "facility_class": facility_class,
                "anchor_province": ANCHOR_PROVINCE,
                "anchor_records": anchor.count,
                "min_m3_y": _format_float(anchor.minimum),
                "p25_m3_y": _format_float(anchor.p25_m3_y),
                "median_m3_y": _format_float(anchor.median_m3_y),
                "p75_m3_y": _format_float(anchor.p75_m3_y),
                "max_m3_y": _format_float(anchor.maximum),
                "proxy_applied_records": len(values),
                "proxy_capacity_m3_y": _format_float(sum(values)),
                "quantile_method": ANCHOR_QUANTILE_METHOD,
                "notes": (
                    "Median and p25/p75 are computed only from BC published "
                    "capacities in the inventory; no other province informs "
                    "the anchor."
                ),
            }
        )
    return rows


def build_province_class(
    capacity_rows: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in capacity_rows:
        grouped[(row["province"], row["facility_class"])].append(row)

    rows: list[dict[str, Any]] = []
    for province in PROVINCES:
        for facility_class in CLASS_ORDER:
            group = grouped.get((province, facility_class), [])
            if not group:
                continue
            published = [
                item for item in group if item["capacity_status"] == PUBLISHED
            ]
            proxies = [
                item for item in group if item["capacity_status"] == PROXY
            ]
            published_capacity = sum(
                float(item["capacity_m3_y"]) for item in published
            )
            proxy_capacity = sum(
                float(item["capacity_m3_y"]) for item in proxies
            )
            total_records = len(group)
            with_capacity = len(published) + len(proxies)
            rows.append(
                {
                    "province": province,
                    "facility_class": facility_class,
                    "records": total_records,
                    "published_records": len(published),
                    "published_capacity_m3_y": _format_float(published_capacity),
                    "proxy_records": len(proxies),
                    "proxy_capacity_m3_y": _format_float(proxy_capacity),
                    "unavailable_records": total_records - with_capacity,
                    "total_capacity_m3_y": _format_float(
                        published_capacity + proxy_capacity
                    ),
                    "capacity_coverage_share": _format_float(
                        with_capacity / total_records if total_records else None
                    ),
                }
            )
    return rows


def _sources(retrieved_utc: str) -> list[dict[str, str]]:
    return [
        {
            "source_id": BC_SOURCE_ID,
            "title": "BC facility layer (FFCM / ISED Mills DB 2015 proxies)",
            "publisher": "UBC FRESH, derived from ISED Mills DB 2015 and FFCM",
            "source_url": "https://github.com/UBC-FRESH/clews-c2070-fresh-data",
            "licence": (
                "Project-derived BC chain layer; upstream ISED Mills DB 2015 / "
                "FFCM proxies. Upstream source is not relicensed here."
            ),
            "licence_url": "",
            "access_method": (
                "Existing derived CSV in the project data repository: "
                "national_facility_inventory_v0/national_facilities.csv"
            ),
            "retrieved_utc": retrieved_utc,
            "coverage": "British Columbia, 179 published records",
            "access_status": "available",
            "used_for_capacity": "yes_published_anchor",
            "notes": (
                "Published capacities are 2015 Mills DB/FFCM processing-line "
                "infeed proxies, not licensed or current capacity. They are "
                "the sole anchor distribution in v1."
            ),
        },
        {
            "source_id": PROXY_SOURCE_ID,
            "title": "Class-median capacity proxy derived from BC anchors",
            "publisher": "UBC FRESH",
            "source_url": (
                "data/derived/national/facilities/national_facility_capacity_v1/"
                "class_medians.csv"
            ),
            "licence": (
                "Model-derived proxy. Anchor values inherit the BC Mills DB / "
                "FFCM lineage; not a facility measurement and not relicensed."
            ),
            "licence_url": "",
            "access_method": "Computed from national_facility_inventory_v0",
            "retrieved_utc": retrieved_utc,
            "coverage": "All provinces and territories",
            "access_status": "computed",
            "used_for_capacity": "yes_proxy",
            "notes": (
                "Median capacity by facility class from BC published records, "
                "applied to blank records of the same class. The p25-p75 range "
                "is reported; no provincial scaling is applied."
            ),
        },
        {
            "source_id": "canadian_biomass_pellet_map_2025",
            "title": "Canadian Biomass 2025 Pellet Mill Map",
            "publisher": "Annex Business Media / Canadian Biomass",
            "source_url": (
                "https://www.canadianbiomassmagazine.ca/wp-content/uploads/2025/05/"
                "CB_PelletMap25_CWM_Digital.pdf"
            ),
            "licence": (
                "Public PDF, copyright Annex Business Media. Factual capacity "
                "figures referenced for cross-check only; not redistributed."
            ),
            "licence_url": "",
            "access_method": "Public HTTP PDF, accessed 2026-11-22",
            "retrieved_utc": retrieved_utc,
            "coverage": "43 current Canadian pellet producers",
            "access_status": "available_not_adopted",
            "used_for_capacity": "no",
            "notes": (
                "Capacities are finished pellet tonnes/yr, not facility infeed "
                "m3/yr. 42 of 43 listed plants are absent from inventory v0 and "
                "no published m3 conversion exists, so no value is transferred "
                "into capacity_m3_y. Recorded as a public cross-check source."
            ),
        },
        {
            "source_id": "alberta_forest_products_members",
            "title": "Alberta Forest Products Association member directory",
            "publisher": "Alberta Forest Products Association",
            "source_url": "https://www.albertaforestproducts.ca/members/",
            "licence": "Public website; factual member listing, all rights reserved",
            "licence_url": "",
            "access_method": "Public HTTP HTML, accessed 2026-11-22",
            "retrieved_utc": retrieved_utc,
            "coverage": "Alberta member companies",
            "access_status": "available_not_adopted",
            "used_for_capacity": "no",
            "notes": (
                "Directory lists member companies without facility-level annual "
                "capacity in m3, so it cannot support a published-capacity row."
            ),
        },
        {
            "source_id": "fpac_member_mills",
            "title": "FPAC member mill list",
            "publisher": "Forest Products Association of Canada",
            "source_url": "https://fpac.ca/en/member-mills/",
            "licence": "Public website; all rights reserved",
            "licence_url": "",
            "access_method": "Attempted public HTTP HTML, accessed 2026-11-22",
            "retrieved_utc": retrieved_utc,
            "coverage": "National member mills",
            "access_status": "http_404",
            "used_for_capacity": "no",
            "notes": (
                "The documented member-mill path returned HTTP 404 at build "
                "time. No capacity data were retrieved."
            ),
        },
        {
            "source_id": "ofia_members",
            "title": "OFIA member directory",
            "publisher": "Ontario Forest Industries Association",
            "source_url": "https://www.ofia.com/members/",
            "licence": "Public website; all rights reserved",
            "licence_url": "",
            "access_method": "Attempted public HTTP HTML, accessed 2026-11-22",
            "retrieved_utc": retrieved_utc,
            "coverage": "Ontario member mills",
            "access_status": "http_403",
            "used_for_capacity": "no",
            "notes": (
                "The member path returned HTTP 403 at build time. No capacity "
                "data were retrieved."
            ),
        },
    ]


def _summary(
    *,
    generated_utc: str,
    records: Sequence[InventoryRecord],
    capacity_rows: Sequence[dict[str, Any]],
    anchors: dict[str, ClassAnchor],
    class_medians: Sequence[dict[str, Any]],
    province_class: Sequence[dict[str, Any]],
    sources: Sequence[dict[str, str]],
) -> dict[str, Any]:
    status_counts = Counter(row["capacity_status"] for row in capacity_rows)
    class_counts = Counter(row["facility_class"] for row in capacity_rows)
    published_capacity = sum(
        float(row["capacity_m3_y"])
        for row in capacity_rows
        if row["capacity_status"] == PUBLISHED
    )
    proxy_capacity = sum(
        float(row["capacity_m3_y"])
        for row in capacity_rows
        if row["capacity_status"] == PROXY
    )
    published_inventory_capacity = sum(
        record.capacity_m3_y or 0.0 for record in records
    )
    proxy_rows = [
        row for row in capacity_rows if row["capacity_status"] == PROXY
    ]
    range_violations = sum(
        1
        for row in proxy_rows
        if not (
            float(row["proxy_low_m3_y"])
            <= float(row["capacity_m3_y"])
            <= float(row["proxy_high_m3_y"])
        )
    )
    relevant_missing = [
        row
        for row in capacity_rows
        if row["facility_class"] in RELEVANT_CLASSES
        and row["capacity_status"] == NOT_PUBLISHED
    ]
    if relevant_missing:
        raise CapacityError(
            f"{len(relevant_missing)} relevant-class records have no capacity"
        )

    by_class: dict[str, dict[str, Any]] = {}
    for facility_class in CLASS_ORDER:
        group = [
            row
            for row in capacity_rows
            if row["facility_class"] == facility_class
        ]
        by_class[facility_class] = {
            "records": len(group),
            "published": sum(
                1 for row in group if row["capacity_status"] == PUBLISHED
            ),
            "proxy": sum(1 for row in group if row["capacity_status"] == PROXY),
            "unavailable": sum(
                1 for row in group if row["capacity_status"] == NOT_PUBLISHED
            ),
            "published_capacity_m3_y": sum(
                float(row["capacity_m3_y"])
                for row in group
                if row["capacity_status"] == PUBLISHED
            ),
            "proxy_capacity_m3_y": sum(
                float(row["capacity_m3_y"])
                for row in group
                if row["capacity_status"] == PROXY
            ),
        }

    by_province: dict[str, dict[str, Any]] = {}
    for province in PROVINCES:
        group = [
            row for row in capacity_rows if row["province"] == province
        ]
        by_province[province] = {
            "records": len(group),
            "published": sum(
                1 for row in group if row["capacity_status"] == PUBLISHED
            ),
            "proxy": sum(1 for row in group if row["capacity_status"] == PROXY),
            "unavailable": sum(
                1 for row in group if row["capacity_status"] == NOT_PUBLISHED
            ),
            "total_capacity_m3_y": sum(
                float(row["capacity_m3_y"])
                for row in group
                if row["capacity_status"] in {PUBLISHED, PROXY}
            ),
        }

    return {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": generated_utc,
        "labels": {
            "evidence_label": EVIDENCE_LABEL,
            "observed": OBSERVED,
            "policy_grade": POLICY_GRADE,
        },
        "method": {
            "published_rule": (
                "carry every published inventory capacity unchanged"
            ),
            "proxy_rule": (
                "for a blank relevant-class record, assign the median capacity "
                "of BC published records in the same facility class"
            ),
            "anchor_province": ANCHOR_PROVINCE,
            "anchor_classes": list(RELEVANT_CLASSES),
            "anchor_min_records": MIN_ANCHOR_RECORDS,
            "proxy_range": "p25-p75 of the BC class anchor",
            "quantile_method": ANCHOR_QUANTILE_METHOD,
            "province_scaling": (
                "none: province_scale=1 for every proxy; no documented "
                "per-class provincial scaling was available"
            ),
            "unavailable_rule": (
                "a class without a BC anchor is left blank and excluded from "
                "assignment rather than given a fabricated capacity"
            ),
            "rounding": "anchor median and p25/p75 are rounded to whole m3/yr",
            "capacity_units": "m3/yr of processing-line infeed proxy",
        },
        "inputs": {
            str(INVENTORY_CSV.relative_to(REPO_ROOT)): _sha256(INVENTORY_CSV)
        },
        "counts": {
            "records_total": len(capacity_rows),
            "published_records": status_counts.get(PUBLISHED, 0),
            "proxy_records": status_counts.get(PROXY, 0),
            "not_published_records": status_counts.get(NOT_PUBLISHED, 0),
            "records_by_class": {
                facility_class: class_counts.get(facility_class, 0)
                for facility_class in CLASS_ORDER
            },
        },
        "capacity": {
            "published_capacity_m3_y": published_capacity,
            "proxy_capacity_m3_y": proxy_capacity,
            "total_capacity_m3_y": published_capacity + proxy_capacity,
            "published_inventory_capacity_m3_y": published_inventory_capacity,
            "proxy_share_of_total": (
                proxy_capacity / (published_capacity + proxy_capacity)
                if published_capacity + proxy_capacity > 0.0
                else None
            ),
            "by_class": by_class,
            "by_province": by_province,
        },
        "anchors": {
            facility_class: {
                "anchor_province": ANCHOR_PROVINCE,
                "count": anchor.count,
                "min_m3_y": anchor.minimum,
                "p25_m3_y": anchor.p25_m3_y,
                "median_m3_y": anchor.median_m3_y,
                "p75_m3_y": anchor.p75_m3_y,
                "max_m3_y": anchor.maximum,
            }
            for facility_class, anchor in anchors.items()
        },
        "class_medians": list(class_medians),
        "province_class_totals": list(province_class),
        "sources": list(sources),
        "validation": {
            "records_match_inventory": len(capacity_rows) == len(records),
            "unique_facility_ids": len(
                {row["facility_id"] for row in capacity_rows}
            )
            == len(capacity_rows),
            "published_capacity_matches_inventory": abs(
                published_capacity - published_inventory_capacity
            )
            <= 1e-6,
            "proxy_range_violations": range_violations,
            "relevant_records_without_capacity": len(relevant_missing),
            "all_relevant_classes_proxied": all(
                by_class[facility_class]["proxy"] >= 0
                and (
                    by_class[facility_class]["published"]
                    + by_class[facility_class]["proxy"]
                    == by_class[facility_class]["records"]
                )
                for facility_class in RELEVANT_CLASSES
            ),
            "anchor_minimum_met": all(
                anchor.count >= MIN_ANCHOR_RECORDS
                for anchor in anchors.values()
            ),
        },
        "caveats": [
            (
                "All volumes are model estimates (`model_estimate`); "
                "`observed=false` and `policy_grade=false`."
            ),
            (
                "Published capacity is the 2015 Mills DB/FFCM processing-line "
                "infeed proxy carried by national_facility_inventory_v0, not "
                "licensed, current, or verified operating capacity."
            ),
            (
                "Class-median proxies are statistical placeholders, not "
                "facility measurements. They are labelled `class_median_proxy` "
                "and `not_published`; their p25-p75 range is reported."
            ),
            (
                "The anchor distribution is BC-only. Applying BC class medians "
                "to other provinces assumes comparable class structure; that "
                "assumption is untested and is a first-order uncertainty."
            ),
            (
                "No provincial scaling is applied. A province's harvest share "
                "does not change its facility proxy capacity in v1."
            ),
            (
                "OTHER-class records have no BC published anchor and are left "
                "blank. They are excluded from assignment v2 rather than given "
                "an invented capacity."
            ),
            (
                "The Canadian Biomass pellet map is public but reports finished "
                "pellet tonnes/yr, not infeed m3/yr; it was not adopted. No "
                "restricted or proprietary data are used."
            ),
            (
                "These capacities are planning inputs only. They are not a "
                "legal/AAC determination, a timber-supply approval, or an "
                "observed operating capacity."
            ),
            "No UI or public OSeCAN surface is changed by this artifact.",
        ],
    }


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


def _report(summary: dict[str, Any]) -> str:
    counts = summary["counts"]
    capacity = summary["capacity"]
    lines = [
        "# National Facility Capacity v1",
        "",
        f"Generated {summary['generated_utc']}.",
        "",
        (
            "**Labels:** `model_estimate`; `observed=false`; "
            "`policy_grade=false`."
        ),
        "",
        (
            "Scope: one capacity row per national facility inventory v0 record. "
            "Published capacities are carried unchanged. Blank relevant-class "
            "records receive a BC class-median proxy with a p25-p75 range. "
            "No facility measurement is invented and no legal/AAC claim is made."
        ),
        "",
        "## Method",
        "",
        (
            "1. Carry every published inventory capacity unchanged "
            f"({counts['published_records']} records)."
        ),
        (
            "2. Compute the median BC published capacity for each relevant "
            "facility class (SAW, PULP, PANEL, PELLET, BIO)."
        ),
        (
            "3. Apply that class median to blank records of the same class "
            f"({counts['proxy_records']} records). Label them "
            "`capacity_status=proxy`, `capacity_evidence=class_median_proxy`, "
            "`not_published` in the source data, and report p25-p75."
        ),
        (
            "4. Leave a class without a BC anchor blank "
            f"({counts['not_published_records']} OTHER records)."
        ),
        (
            "5. Do not scale by provincial harvest share. No documented "
            "per-class provincial scaling exists, so `province_scale=1`."
        ),
        "",
        "## Published versus proxy capacity",
        "",
        *_md_table(
            [
                "Class",
                "Records",
                "Published",
                "Proxy",
                "Unavailable",
                "Published m3/yr",
                "Proxy m3/yr",
            ],
            [
                [
                    facility_class,
                    counts["records_by_class"][facility_class],
                    capacity["by_class"][facility_class]["published"],
                    capacity["by_class"][facility_class]["proxy"],
                    capacity["by_class"][facility_class]["unavailable"],
                    _md_number(
                        capacity["by_class"][facility_class][
                            "published_capacity_m3_y"
                        ]
                    ),
                    _md_number(
                        capacity["by_class"][facility_class]["proxy_capacity_m3_y"]
                    ),
                ]
                for facility_class in CLASS_ORDER
            ],
        ),
        "",
        (
            f"Published capacity total: **{_md_number(capacity['published_capacity_m3_y'])} "
            f"m3/yr**. Proxy capacity total: "
            f"**{_md_number(capacity['proxy_capacity_m3_y'])} m3/yr** "
            f"({(capacity['proxy_share_of_total'] or 0.0):.1%} of the combined "
            "total)."
        ),
        "",
        "## BC anchor distribution",
        "",
        *_md_table(
            [
                "Class",
                "BC records",
                "Min m3/yr",
                "p25 m3/yr",
                "Median m3/yr",
                "p75 m3/yr",
                "Max m3/yr",
            ],
            [
                [
                    facility_class,
                    summary["anchors"][facility_class]["count"],
                    _md_number(
                        summary["anchors"][facility_class]["min_m3_y"]
                    ),
                    _md_number(
                        summary["anchors"][facility_class]["p25_m3_y"]
                    ),
                    _md_number(
                        summary["anchors"][facility_class]["median_m3_y"]
                    ),
                    _md_number(
                        summary["anchors"][facility_class]["p75_m3_y"]
                    ),
                    _md_number(
                        summary["anchors"][facility_class]["max_m3_y"]
                    ),
                ]
                for facility_class in RELEVANT_CLASSES
            ],
        ),
        "",
        (
            "Quantiles use linear interpolation (R-7) and are rounded to whole "
            "m3/yr. The median is the proxy point value; p25 and p75 are the "
            "reported proxy range."
        ),
        "",
        "## Province and class totals",
        "",
        *_md_table(
            ["Province", "Records", "Published", "Proxy", "Unavailable", "Total m3/yr"],
            [
                [
                    province,
                    values["records"],
                    values["published"],
                    values["proxy"],
                    values["unavailable"],
                    _md_number(values["total_capacity_m3_y"]),
                ]
                for province, values in capacity["by_province"].items()
            ],
        ),
        "",
        "## Public sources screened",
        "",
        *_md_table(
            ["Source", "Access", "Used", "Licence / note"],
            [
                [
                    source["source_id"],
                    source["access_status"],
                    source["used_for_capacity"],
                    source["licence"],
                ]
                for source in summary["sources"]
            ],
        ),
        "",
        (
            "The Canadian Biomass pellet map is directly accessible and public, "
            "but its capacity unit is finished pellet tonnes/yr. Converting it "
            "to infeed m3/yr would require an unpublished conversion and 42 of "
            "its 43 plants are absent from inventory v0, so it is recorded as a "
            "cross-check source only. No restricted or proprietary data are used."
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
            "workflows.national.facilities.national_facility_capacity"
        ),
        ".venv/bin/pytest tests/test_national_facility_capacity.py",
        (
            ".venv/bin/ruff check "
            "workflows/national/facilities/national_facility_capacity.py "
            "tests/test_national_facility_capacity.py"
        ),
        "```",
        "",
    ]
    return "\n".join(lines)


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
        "counts": summary["counts"],
        "capacity": summary["capacity"],
        "anchors": summary["anchors"],
        "sources": summary["sources"],
        "validation": summary["validation"],
        "reproduce": (
            ".venv/bin/python -m "
            "workflows.national.facilities.national_facility_capacity"
        ),
    }
    _write_json(output_dir / "manifest.json", manifest)


def build(
    output_dir: Path = OUTPUT_DIR,
    inventory_path: Path = INVENTORY_CSV,
) -> dict[str, Any]:
    """Build the capacity artifact and return its summary."""

    generated_utc = _utc_now()
    records = read_inventory(inventory_path)
    anchors = compute_anchors(records)
    capacity_rows = build_capacity_rows(records, anchors)
    class_medians = build_class_medians(anchors, capacity_rows)
    province_class = build_province_class(capacity_rows)
    sources = _sources(generated_utc)
    summary = _summary(
        generated_utc=generated_utc,
        records=records,
        capacity_rows=capacity_rows,
        anchors=anchors,
        class_medians=class_medians,
        province_class=province_class,
        sources=sources,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(
        output_dir / "national_facility_capacity_v1.csv",
        CAPACITY_FIELDS,
        capacity_rows,
    )
    _write_csv(
        output_dir / "class_medians.csv", CLASS_MEDIAN_FIELDS, class_medians
    )
    _write_csv(
        output_dir / "province_class_capacity.csv",
        PROVINCE_CLASS_FIELDS,
        province_class,
    )
    _write_csv(output_dir / "sources.csv", SOURCE_FIELDS, sources)
    (output_dir / "NATIONAL_FACILITY_CAPACITY_V1.md").write_text(
        _report(summary), encoding="utf-8"
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
                "records_total": summary["counts"]["records_total"],
                "published_records": summary["counts"]["published_records"],
                "proxy_records": summary["counts"]["proxy_records"],
                "total_capacity_m3_y": summary["capacity"][
                    "total_capacity_m3_y"
                ],
                "output_dir": str(OUTPUT_DIR.relative_to(REPO_ROOT)),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
