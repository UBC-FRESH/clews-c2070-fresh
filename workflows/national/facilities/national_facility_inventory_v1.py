"""Build national facility inventory v1 with public pellet-map capacity.

Inventory v0 is the BC/OSM facility layer.  v1 adds the current producers in
the public Canadian Biomass 2025 Pellet Mill Map, converts their reported
finished-pellet tonnes to m3 with a documented bulk density, and dedupes them
against the pellet facilities already present.  Capacity values are labelled
``published_capacity_converted``; no value is invented and no restricted data
are used.

The builder is fail-closed: unexpected classes, provinces, duplicate ids,
missing provenance, and duplicate pellet locations stop the build.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from workflows.national.facilities import pellet_map_2025

REPO_ROOT = Path(__file__).resolve().parents[3]
INVENTORY_V0_CSV = (
    REPO_ROOT
    / "data/derived/national/facilities/national_facility_inventory_v0"
    / "national_facilities.csv"
)
OUTPUT_DIR = (
    REPO_ROOT / "data/derived/national/facilities/national_facility_inventory_v1"
)
ARTIFACT = "national_facility_inventory_v1"
ARTIFACT_VERSION = "1.0.0"
CLASS_ORDER = ("SAW", "PULP", "PANEL", "PELLET", "BIO", "OTHER")
PROVINCES = (
    "BC",
    "AB",
    "SK",
    "MB",
    "ON",
    "QC",
    "NB",
    "NS",
    "PE",
    "NL",
    "YT",
    "NT",
    "NU",
)
PUBLISHED = "published"
NOT_PUBLISHED = "not_published"
PUBLISHED_INFEED_EVIDENCE = "published_mills_db_2015_infeed_proxy"
PUBLISHED_CONVERTED_EVIDENCE = "published_capacity_converted"
EVIDENCE_LABEL = "model_estimate"
OBSERVED = False
POLICY_GRADE = False

V1_EXTRA_FIELDS = [
    "capacity_evidence",
    "location_source_id",
    "location_source_url",
    "location_licence",
    "location_precision",
]
DEDUP_FIELDS = [
    "map_row",
    "map_name",
    "city",
    "province",
    "capacity_tonnes_y",
    "capacity_m3_y_converted",
    "plant_status",
    "match_status",
    "matched_facility_id",
    "matched_name",
    "match_method",
    "match_distance_km",
    "action",
    "notes",
]
COVERAGE_FIELDS = [
    "province",
    "records",
    "published_records",
    "not_published_records",
    "capacity_known_records",
    "capacity_unknown_records",
    "coordinate_records",
    "coordinate_missing_records",
    "pellet_records",
    "pellet_new_records",
    "published_capacity_m3_y",
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
    "used_for_inventory",
    "notes",
]

# Pellet-map rows whose pellet line is physically co-located with a facility
# already in inventory v0.  The pellet line stays a separate PELLET record,
# consistent with the BC layer's one-record-per-product-line convention.
COLOCATED: dict[int, str] = {
    11: "OSM-BC-W192229517",  # Skeena Sawmills, Terrace
    15: "OSM-AB-W334796375",  # La Crete Sawmills
    20: "OSM-ON-W299165594",  # Thunder Bay Pulp and Paper
    36: "OSM-NB-W499738346",  # Crabbe Lumber, Bristol
    42: "OSM-NS-R15024787",  # Great Northern Timber
}

STOPWORDS = frozenset(
    {
        "a",
        "and",
        "au",
        "aux",
        "bioenergy",
        "biomasse",
        "camp",
        "canfor",
        "co",
        "company",
        "contractors",
        "corp",
        "corporation",
        "de",
        "des",
        "division",
        "drax",
        "du",
        "energy",
        "energie",
        "first",
        "forest",
        "forestier",
        "fraser",
        "fuel",
        "generation",
        "group",
        "groupe",
        "inc",
        "incorporated",
        "industries",
        "industry",
        "la",
        "le",
        "les",
        "limited",
        "lp",
        "ltd",
        "mill",
        "mills",
        "nation",
        "of",
        "partnership",
        "pellet",
        "pellets",
        "pinnacle",
        "product",
        "products",
        "recycled",
        "renewable",
        "resources",
        "sawmill",
        "sawmills",
        "solutions",
        "specialty",
        "the",
        "tolko",
        "west",
        "witset",
        "wood",
    }
)
RESTRICTED_SOURCE_MARKERS = (
    "proprietary",
    "restricted",
    "confidential",
    "licensed_only",
    "subscription",
)


class InventoryError(ValueError):
    """Raised when an inventory v1 invariant fails."""


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
        raise InventoryError(f"input file is missing: {path}") from error
    if not rows:
        raise InventoryError(f"{path}: no data rows")
    return rows


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


def _format_float(value: float | None, digits: int = 6) -> str:
    if value is None:
        return ""
    text = f"{float(value):.{digits}f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def _normalise_name(name: str) -> str:
    text = unicodedata.normalize("NFKD", name)
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    tokens = [token for token in text.split() if token]
    while tokens and tokens[-1] in {
        "co",
        "company",
        "corp",
        "corporation",
        "inc",
        "incorporated",
        "limited",
        "lp",
        "ltd",
    }:
        tokens.pop()
    return " ".join(tokens)


def _tokens(name: str) -> set[str]:
    return {
        token
        for token in _normalise_name(name).split()
        if token not in STOPWORDS and len(token) > 1
    }


def _haversine_km(
    lat_a: float, lon_a: float, lat_b: float, lon_b: float
) -> float:
    radius = 6371.0088
    phi_a = math.radians(lat_a)
    phi_b = math.radians(lat_b)
    d_phi = phi_b - phi_a
    d_lambda = math.radians(lon_b - lon_a)
    hav = (
        math.sin(d_phi / 2.0) ** 2
        + math.cos(phi_a) * math.cos(phi_b) * math.sin(d_lambda / 2.0) ** 2
    )
    return 2.0 * radius * math.asin(min(1.0, math.sqrt(hav)))


def _coordinates(row: dict[str, str]) -> tuple[float, float] | None:
    lat = row.get("lat", "").strip()
    lon = row.get("lon", "").strip()
    if not lat or not lon:
        return None
    try:
        return float(lat), float(lon)
    except ValueError as error:
        raise InventoryError(
            f"{row.get('facility_id')}: invalid coordinate {lat!r},{lon!r}"
        ) from error


def _validate_base_rows(rows: Sequence[dict[str, str]]) -> None:
    seen: set[str] = set()
    for position, row in enumerate(rows, start=2):
        context = f"inventory v0 row {position}"
        facility_id = row["facility_id"].strip()
        if not facility_id:
            raise InventoryError(f"{context}: blank facility_id")
        if facility_id in seen:
            raise InventoryError(f"{context}: duplicate facility_id {facility_id}")
        seen.add(facility_id)
        if row["facility_class"].strip() not in CLASS_ORDER:
            raise InventoryError(
                f"{context}: unexpected class {row['facility_class']!r}"
            )
        if row["province"].strip() not in PROVINCES:
            raise InventoryError(
                f"{context}: unexpected province {row['province']!r}"
            )
        status = row["capacity_status"].strip().lower()
        capacity = row["capacity_m3_y"].strip()
        if status == PUBLISHED:
            if not capacity:
                raise InventoryError(f"{context}: published row without capacity")
            if float(capacity) <= 0.0:
                raise InventoryError(f"{context}: capacity must be positive")
        elif status == NOT_PUBLISHED:
            if capacity:
                raise InventoryError(f"{context}: blank-status row with capacity")
        else:
            raise InventoryError(f"{context}: unexpected capacity status {status!r}")
        if not row["source_id"].strip() or not row["licence"].strip():
            raise InventoryError(f"{context}: missing source id or licence")


def _find_match(
    plant: pellet_map_2025.PelletPlant,
    rows: Sequence[dict[str, str]],
) -> tuple[dict[str, str] | None, str, float | None]:
    """Find an existing PELLET record for one pellet-map plant."""

    candidates = [
        row
        for row in rows
        if row["province"].strip() == plant.province
        and row["facility_class"].strip() == "PELLET"
    ]
    plant_name = _normalise_name(plant.name)
    plant_tokens = _tokens(plant.name)
    city_tokens = _tokens(plant.city)
    best: tuple[float, dict[str, str], str, float | None] | None = None
    for candidate in candidates:
        candidate_name = _normalise_name(candidate["name"])
        candidate_tokens = _tokens(candidate["name"])
        method = ""
        distance: float | None = None
        if plant_name == candidate_name:
            method = "exact_normalised_name"
        else:
            shared = plant_tokens & candidate_tokens
            if not shared:
                continue
            coords = _coordinates(candidate)
            if coords is not None:
                distance = _haversine_km(
                    plant.city_lat, plant.city_lon, coords[0], coords[1]
                )
            city_in_name = bool(city_tokens) and city_tokens <= set(
                candidate_name.split()
            )
            if city_in_name:
                method = "shared_name_token_and_city"
            elif distance is not None and distance <= 20.0:
                method = "shared_name_token_and_coordinate"
            else:
                continue
        if method == "exact_normalised_name":
            score = 0.0
        elif method == "shared_name_token_and_city":
            score = 1.0 + (0.0 if distance is None else distance)
        else:
            score = 2.0 + (0.0 if distance is None else distance)
        if best is None or score < best[0]:
            best = (score, candidate, method, distance)
    if best is None:
        return None, "", None
    return best[1], best[2], best[3]


def _appended_note(existing: str, addition: str) -> str:
    existing = existing.strip()
    return f"{existing}; {addition}" if existing else addition


def _new_pellet_row(
    plant: pellet_map_2025.PelletPlant,
    *,
    colocated: dict[str, str] | None,
) -> dict[str, str]:
    converted = plant.capacity_m3_y
    if plant.capacity_tonnes_y is not None:
        if converted is None:
            raise InventoryError(f"map row {plant.map_row}: conversion failed")
        capacity_status = PUBLISHED
        capacity = _format_float(converted)
        capacity_evidence = PUBLISHED_CONVERTED_EVIDENCE
    else:
        capacity_status = NOT_PUBLISHED
        capacity = ""
        capacity_evidence = ""

    if colocated is not None:
        location_lat = colocated["lat"].strip()
        location_lon = colocated["lon"].strip()
        location_source_id = colocated["source_id"].strip()
        location_source_url = colocated["source_url"].strip()
        location_licence = colocated["licence"].strip()
        location_precision = "co_located_facility"
        location_note = (
            f"Coordinates and presence shared with {colocated['facility_id']} "
            f"({colocated['name']}); pellet line treated as a separate product "
            "record."
        )
    else:
        location_lat = _format_float(plant.city_lat, 6)
        location_lon = _format_float(plant.city_lon, 6)
        location_source_id = plant.location_source_id
        location_source_url = plant.location_source_url
        location_licence = plant.location_licence
        location_precision = "city_centroid"
        location_note = (
            f"City-centroid location for {plant.city}, {plant.province}; not "
            "a plant gate coordinate."
        )

    if plant.capacity_tonnes_y is not None:
        conversion_note = (
            f"Published capacity converted from {plant.capacity_tonnes_y:,.0f} "
            "finished pellet t/yr at 650 kg/m3 to "
            f"{converted:,.0f} m3/yr finished-pellet bulk volume; no pellet "
            "yield or infeed conversion applied."
        )
    else:
        conversion_note = "No capacity reported in the 2025 map."

    status = plant.plant_status
    return {
        "facility_id": f"CBM25-{plant.province}-{plant.map_row:02d}",
        "name": f"{plant.name} - {plant.city}",
        "facility_class": "PELLET",
        "province": plant.province,
        "lat": location_lat,
        "lon": location_lon,
        "capacity_m3_y": capacity,
        "capacity_status": capacity_status,
        "status": status,
        "source_id": pellet_map_2025.SOURCE_ID,
        "source_url": pellet_map_2025.SOURCE_URL,
        "licence": pellet_map_2025.LICENCE,
        "evidence_tier": "C",
        "notes": (
            f"Canadian Biomass 2025 pellet map current-producer row "
            f"{plant.map_row}. {conversion_note} {location_note}"
        ),
        "capacity_evidence": capacity_evidence,
        "location_source_id": location_source_id,
        "location_source_url": location_source_url,
        "location_licence": location_licence,
        "location_precision": location_precision,
    }


def _base_row_with_provenance(row: dict[str, str]) -> dict[str, str]:
    enriched = {field: row.get(field, "") for field in row}
    published = row["capacity_status"].strip() == PUBLISHED
    enriched["capacity_evidence"] = (
        PUBLISHED_INFEED_EVIDENCE if published else ""
    )
    enriched["location_source_id"] = row["source_id"]
    enriched["location_source_url"] = row["source_url"]
    enriched["location_licence"] = row["licence"]
    enriched["location_precision"] = (
        "facility" if row.get("lat", "").strip() else "unknown"
    )
    return enriched


def _build_sources(retrieved_utc: str) -> list[dict[str, str]]:
    return [
        {
            "source_id": "bc_mills_db_2015",
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
            "used_for_inventory": "yes_published_capacity_and_presence",
            "notes": (
                "Published capacities are 2015 Mills DB/FFCM processing-line "
                "infeed proxies, not licensed or current capacity."
            ),
        },
        {
            "source_id": "osm_overpass_canada",
            "title": "OpenStreetMap facility presence, Canada",
            "publisher": "OpenStreetMap contributors",
            "source_url": "https://www.openstreetmap.org/",
            "licence": "ODbL-1.0",
            "licence_url": "https://opendatacommons.org/licenses/odbl/1-0/",
            "access_method": (
                "Carried from national_facility_inventory_v0; original "
                "Overpass extraction documented there"
            ),
            "retrieved_utc": retrieved_utc,
            "coverage": "Non-BC facility presence",
            "access_status": "available",
            "used_for_inventory": "yes_presence",
            "notes": (
                "OSM presence does not establish operating status or capacity."
            ),
        },
        {
            "source_id": pellet_map_2025.SOURCE_ID,
            "title": pellet_map_2025.SOURCE_TITLE,
            "publisher": pellet_map_2025.PUBLISHER,
            "source_url": pellet_map_2025.SOURCE_URL,
            "licence": pellet_map_2025.LICENCE,
            "licence_url": "",
            "access_method": "Public HTTP PDF, accessed 2026-11-22",
            "retrieved_utc": retrieved_utc,
            "coverage": "43 current Canadian pellet producers; 4 proposed excluded",
            "access_status": "available",
            "used_for_inventory": "yes_published_capacity_converted",
            "notes": (
                "Current-producer table only. Capacities are finished pellet "
                "tonnes/yr; converted to m3 at 650 kg/m3 and labelled "
                "published_capacity_converted. Proposed plants are excluded."
            ),
        },
        {
            "source_id": pellet_map_2025.DENSITY_SOURCE_ID,
            "title": pellet_map_2025.DENSITY_SOURCE_TITLE,
            "publisher": pellet_map_2025.DENSITY_SOURCE_PUBLISHER,
            "source_url": pellet_map_2025.DENSITY_SOURCE_URL,
            "licence": (
                "Government of Canada publication; factual bulk-density range "
                "cited, not reproduced."
            ),
            "licence_url": "",
            "access_method": "Public HTTP PDF, accessed 2026-11-22",
            "retrieved_utc": retrieved_utc,
            "coverage": "Bulk density 600-750 kg/m3 for graded wood pellets",
            "access_status": "available",
            "used_for_inventory": "yes_conversion_basis",
            "notes": (
                "CAN/CSA-ISO 17225-2 bulk-density range 600-750 kg/m3. The "
                "planning conversion uses 650 kg/m3, inside that range."
            ),
        },
        {
            "source_id": "osm_nominatim_city_centroid",
            "title": "OpenStreetMap Nominatim city-centroid geocoding",
            "publisher": "OpenStreetMap contributors",
            "source_url": "https://nominatim.openstreetmap.org/",
            "licence": "ODbL-1.0",
            "licence_url": "https://opendatacommons.org/licenses/odbl/1-0/",
            "access_method": "Public HTTP API, one query per city, 2026-11-22",
            "retrieved_utc": retrieved_utc,
            "coverage": "Pellet-map plant cities",
            "access_status": "available",
            "used_for_inventory": "yes_city_centroid_location",
            "notes": (
                "City centroids are not plant-gate coordinates and carry "
                "location uncertainty; co-located records use the existing "
                "facility coordinate instead."
            ),
        },
        {
            "source_id": "alberta_forest_products_members",
            "title": "Alberta Forest Products Association association members",
            "publisher": "Alberta Forest Products Association",
            "source_url": "https://albertaforestproducts.ca/about/association-members/",
            "licence": "Public website; factual member listing, all rights reserved",
            "licence_url": "",
            "access_method": "Public HTTP HTML, accessed 2026-11-22",
            "retrieved_utc": retrieved_utc,
            "coverage": "Alberta member companies and mill towns",
            "access_status": "available_no_capacity",
            "used_for_inventory": "no",
            "notes": (
                "The public member page lists companies and towns but no "
                "facility-level annual capacity, so it cannot support a "
                "published-capacity row. It was used only to cross-check names."
            ),
        },
        {
            "source_id": "ofia_members",
            "title": "OFIA member directory",
            "publisher": "Ontario Forest Industries Association",
            "source_url": "https://ofia.com/our-members/",
            "licence": "Public website; all rights reserved",
            "licence_url": "",
            "access_method": "Public HTTP HTML, accessed 2026-11-22",
            "retrieved_utc": retrieved_utc,
            "coverage": "Ontario member mills",
            "access_status": "available_no_capacity",
            "used_for_inventory": "no",
            "notes": (
                "The public member page did not expose facility-level annual "
                "capacity in its server-rendered content. No capacity data "
                "were retrieved."
            ),
        },
    ]


def _coverage(rows: Sequence[dict[str, Any]], new_ids: set[str]) -> list[dict[str, Any]]:
    coverage: list[dict[str, Any]] = []
    for province in PROVINCES:
        group = [row for row in rows if row["province"] == province]
        coverage.append(
            {
                "province": province,
                "records": len(group),
                "published_records": sum(
                    1 for row in group if row["capacity_status"] == PUBLISHED
                ),
                "not_published_records": sum(
                    1 for row in group if row["capacity_status"] == NOT_PUBLISHED
                ),
                "capacity_known_records": sum(
                    1 for row in group if row["capacity_status"] == PUBLISHED
                ),
                "capacity_unknown_records": sum(
                    1 for row in group if row["capacity_status"] == NOT_PUBLISHED
                ),
                "coordinate_records": sum(
                    1 for row in group if row.get("lat", "").strip()
                ),
                "coordinate_missing_records": sum(
                    1 for row in group if not row.get("lat", "").strip()
                ),
                "pellet_records": sum(
                    1 for row in group if row["facility_class"] == "PELLET"
                ),
                "pellet_new_records": sum(
                    1 for row in group if row["facility_id"] in new_ids
                ),
                "published_capacity_m3_y": _format_float(
                    sum(
                        float(row["capacity_m3_y"])
                        for row in group
                        if row["capacity_status"] == PUBLISHED
                        and row["capacity_m3_y"]
                    )
                ),
            }
        )
    return coverage


def _summary(
    *,
    generated_utc: str,
    rows: Sequence[dict[str, Any]],
    base_rows: Sequence[dict[str, str]],
    crosswalk: Sequence[dict[str, Any]],
    coverage: Sequence[dict[str, Any]],
    sources: Sequence[dict[str, str]],
) -> dict[str, Any]:
    status_counts = Counter(row["capacity_status"] for row in rows)
    class_counts = Counter(row["facility_class"] for row in rows)
    published_capacity = sum(
        float(row["capacity_m3_y"])
        for row in rows
        if row["capacity_status"] == PUBLISHED
    )
    converted_rows = [
        row
        for row in rows
        if row.get("capacity_evidence") == PUBLISHED_CONVERTED_EVIDENCE
    ]
    new_rows = [row for row in rows if row["facility_id"].startswith("CBM25-")]
    matched = [
        row
        for row in crosswalk
        if row["match_status"] == "matched_existing_pellet"
    ]
    preserved = [
        row
        for row in matched
        if row["action"] == "preserve_existing_published_capacity"
    ]
    upgraded = [
        row
        for row in matched
        if row["action"] == "upgrade_existing_proxy_to_published"
    ]
    new_capacity_rows = [
        row
        for row in new_rows
        if row["capacity_status"] == PUBLISHED
    ]
    duplicate_keys: dict[tuple[str, str], int] = defaultdict(int)
    for row in rows:
        if row["facility_class"] != "PELLET":
            continue
        duplicate_keys[
            (_normalise_name(row["name"]), row["province"])
        ] += 1
    duplicate_pellet_names = {
        f"{name} ({province})": count
        for (name, province), count in duplicate_keys.items()
        if count > 1
    }
    # Same company name with different cities is legitimate; exact duplicate
    # normalised names within one province are not.
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
            "base_inventory": "national_facility_inventory_v0",
            "pellet_source": pellet_map_2025.SOURCE_ID,
            "pellet_rows": pellet_map_2025.EXPECTED_ROWS,
            "pellet_scope": "current producers only; four proposed plants excluded",
            "dedupe_rule": (
                "match a pellet-map plant to an existing same-province PELLET "
                "record by exact normalised name, or by a shared distinctive "
                "name token plus a matching city token or <=20 km coordinate "
                "proximity; co-located non-pellet mills keep their own record"
            ),
            "capacity_rule": (
                "preserve every existing published capacity; upgrade an "
                "existing unpublished pellet record with a converted published "
                "value; add unmatched pellet plants"
            ),
            "conversion": "capacity_m3_y = capacity_tonnes_y / 0.65",
            "conversion_basis": (
                "650 kg/m3 finished-pellet bulk density, inside the NRCan/"
                "CAN-CSA-ISO 17225-2 range 600-750 kg/m3; finished-pellet "
                "bulk volume, no pellet yield or infeed conversion applied"
            ),
            "conversion_evidence_label": PUBLISHED_CONVERTED_EVIDENCE,
            "location_rule": (
                "existing facility coordinate for matched records; co-located "
                "facility coordinate for a pellet line at an existing mill; "
                "Nominatim city centroid otherwise"
            ),
            "proposed_plants_excluded": True,
        },
        "counts": {
            "records_total": len(rows),
            "base_records": len(base_rows),
            "new_records": len(new_rows),
            "matched_existing_records": len(matched),
            "matched_published_preserved": len(preserved),
            "matched_proxy_upgraded": len(upgraded),
            "records_by_class": {
                facility_class: class_counts.get(facility_class, 0)
                for facility_class in CLASS_ORDER
            },
            "records_by_province": dict(
                Counter(row["province"] for row in rows)
            ),
            "published_records": status_counts.get(PUBLISHED, 0),
            "not_published_records": status_counts.get(NOT_PUBLISHED, 0),
        },
        "capacity": {
            "published_capacity_m3_y": published_capacity,
            "converted_publication_records": len(converted_rows),
            "converted_publication_capacity_m3_y": sum(
                float(row["capacity_m3_y"]) for row in converted_rows
            ),
            "new_published_records": len(new_capacity_rows),
            "new_published_capacity_m3_y": sum(
                float(row["capacity_m3_y"]) for row in new_capacity_rows
            ),
            "new_published_capacity_tonnes_y": sum(
                float(row["capacity_tonnes_y"])
                for row in crosswalk
                if row["match_status"] == "new_pellet"
                and row["capacity_tonnes_y"]
            ),
            "total_reported_pellet_capacity_tonnes_y": sum(
                float(row["capacity_tonnes_y"])
                for row in crosswalk
                if row["capacity_tonnes_y"]
            ),
            "total_converted_pellet_capacity_m3_y": sum(
                float(row["capacity_m3_y_converted"])
                for row in crosswalk
                if row["capacity_m3_y_converted"]
            ),
        },
        "dedupe": {
            "crosswalk_rows": len(crosswalk),
            "new_pellet": sum(
                1 for row in crosswalk if row["match_status"] == "new_pellet"
            ),
            "matched_existing_pellet": len(matched),
            "preserved_existing_published": len(preserved),
            "upgraded_existing_proxy": len(upgraded),
            "duplicate_normalised_pellet_names": duplicate_pellet_names,
        },
        "province_coverage": list(coverage),
        "sources": list(sources),
        "validation": {
            "unique_facility_ids": len({row["facility_id"] for row in rows})
            == len(rows),
            "base_records_preserved": all(
                row["facility_id"]
                in {item["facility_id"] for item in rows}
                for row in base_rows
            ),
            "no_duplicate_normalised_pellet_names": not duplicate_pellet_names,
            "published_rows_have_capacity": all(
                row["capacity_m3_y"]
                for row in rows
                if row["capacity_status"] == PUBLISHED
            ),
            "unpublished_rows_blank": all(
                not row["capacity_m3_y"]
                for row in rows
                if row["capacity_status"] == NOT_PUBLISHED
            ),
            "all_capacities_positive": all(
                float(row["capacity_m3_y"]) > 0.0
                for row in rows
                if row["capacity_m3_y"]
            ),
            "all_converted_values_labelled": all(
                row["capacity_evidence"] == PUBLISHED_CONVERTED_EVIDENCE
                for row in converted_rows
            ),
            "no_restricted_sources": not any(
                marker in (
                    source["licence"] + " " + source["notes"]
                ).lower()
                for source in sources
                for marker in RESTRICTED_SOURCE_MARKERS
            ),
            "pellet_map_rows_accounted": len(crosswalk)
            == pellet_map_2025.EXPECTED_ROWS,
        },
        "caveats": [
            ("All values are model estimates (`model_estimate`); "
            "`observed=false` and `policy_grade=false`."),
            ("Pellet-map capacities are published finished-pellet tonnes/yr, "
            "converted at 650 kg/m3 to finished-pellet bulk volume. They are "
            "not infeed-equivalent, observed throughput, or a legal/AAC "
            "determination."),
            ("Existing BC published infeed-proxy capacities are preserved even "
            "when the pellet map reports a different finished-product value; "
            "the two values use different bases."),
            ("There is no legal, AAC, timber-supply, or operating-status claim. "
            "Most inventory statuses remain unknown."),
            ("City-centroid coordinates are not plant-gate coordinates; "
            "distance-based assignment for those records carries location "
            "uncertainty."),
            ("No restricted or proprietary data are used. The public PDF is "
            "not redistributed; only the factual extract is stored."),
            "No UI or public OSeCAN surface is changed by this artifact.",
        ],
    }


def _md_table(headers: Sequence[str], rows: Iterable[Sequence[Any]]) -> list[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join("---" for _ in headers) + "|",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(value) for value in row) + " |")
    return lines


def _report(summary: dict[str, Any]) -> str:
    counts = summary["counts"]
    capacity = summary["capacity"]
    dedupe = summary["dedupe"]
    lines = [
        "# National Facility Inventory V1",
        "",
        ("Inventory v0 plus the public Canadian Biomass 2025 pellet-map current "
        "producers, deduped against existing pellet records. All values are "
        "model estimates: `observed=false`, `policy_grade=false`."),
        "",
        "## Headline",
        "",
        (f"- Records: **{counts['records_total']:,}** "
        f"(v0: {counts['base_records']:,}; new: {counts['new_records']:,})."),
        f"- Published-capacity records: **{counts['published_records']:,}**.",
        (f"- New published pellet records: **{capacity['new_published_records']}** "
        f"carrying **{capacity['new_published_capacity_m3_y']:,.0f} m3/yr** of "
        "converted finished-pellet volume."),
        (f"- Pellet-map rows accounted for: **{dedupe['crosswalk_rows']} / 43**; "
        f"matched existing: **{dedupe['matched_existing_pellet']}**; new: "
        f"**{dedupe['new_pellet']}**."),
        (f"- Existing published capacities preserved: "
        f"**{dedupe['preserved_existing_published']}**; existing unpublished "
        f"pellet records upgraded: **{dedupe['upgraded_existing_proxy']}**."),
        "",
        "## Conversion",
        "",
        "`capacity_m3_y = capacity_tonnes_y / 0.65`",
        "",
        ("The 650 kg/m3 value is inside the Natural Resources Canada / "
        "CAN-CSA-ISO 17225-2 graded-wood-pellet bulk-density range of "
        "600-750 kg/m3. The result is finished-pellet bulk volume; no pellet "
        "yield or infeed conversion is applied. Converted rows are labelled "
        "`published_capacity_converted`."),
        "",
        "## Coverage by province",
        "",
        *_md_table(
            [
                "Province",
                "Records",
                "Published",
                "Unknown",
                "Coordinates",
                "Pellet",
                "New pellet",
            ],
            [
                [
                    row["province"],
                    row["records"],
                    row["published_records"],
                    row["not_published_records"],
                    row["coordinate_records"],
                    row["pellet_records"],
                    row["pellet_new_records"],
                ]
                for row in summary["province_coverage"]
                if row["records"]
            ],
        ),
        "",
        "## Dedupe crosswalk",
        "",
        ("Every pellet-map row is mapped in `dedup_crosswalk.csv`, including "
        "plants that matched an existing pellet record and were not added "
        "again. Co-located sawmill and pulp records stay separate because the "
        "inventory convention is one record per product line."),
        "",
        "## Validation",
        "",
    ]
    for key, value in summary["validation"].items():
        lines.append(f"- `{key}`: {value}")
    lines.extend(["", "## Caveats", ""])
    for caveat in summary["caveats"]:
        lines.append(f"- {caveat}")
    lines.append("")
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
        "counts": summary["counts"],
        "capacity": summary["capacity"],
        "dedupe": summary["dedupe"],
        "sources": summary["sources"],
        "validation": summary["validation"],
        "reproduce": (
            ".venv/bin/python -m "
            "workflows.national.facilities.national_facility_inventory_v1"
        ),
    }
    _write_json(output_dir / "manifest.json", manifest)


def build(
    output_dir: Path = OUTPUT_DIR,
    inventory_v0_path: Path = INVENTORY_V0_CSV,
    pellet_extract_path: Path = pellet_map_2025.EXTRACT_CSV,
) -> dict[str, Any]:
    """Build inventory v1 and return its summary."""

    generated_utc = _utc_now()
    base_rows = _read_csv(inventory_v0_path)
    _validate_base_rows(base_rows)
    plants = pellet_map_2025.read_extract(pellet_extract_path)
    rows_by_id = {
        row["facility_id"]: _base_row_with_provenance(row) for row in base_rows
    }
    crosswalk: list[dict[str, Any]] = []
    new_ids: set[str] = set()

    for plant in plants:
        match, method, distance = _find_match(plant, base_rows)
        converted = plant.capacity_m3_y
        base_crosswalk = {
            "map_row": plant.map_row,
            "map_name": plant.name,
            "city": plant.city,
            "province": plant.province,
            "capacity_tonnes_y": _format_float(plant.capacity_tonnes_y),
            "capacity_m3_y_converted": _format_float(converted),
            "plant_status": plant.plant_status,
        }
        if match is not None:
            row = rows_by_id[match["facility_id"]]
            if row["capacity_status"] == PUBLISHED:
                action = "preserve_existing_published_capacity"
                note = (
                    f"Pellet map row {plant.map_row} cross-check: "
                    f"{plant.capacity_tonnes_y:,.0f} finished t/yr = "
                    f"{converted:,.0f} m3/yr finished-pellet bulk volume at "
                    "650 kg/m3. Existing published infeed-proxy capacity "
                    "preserved because the bases differ."
                    if plant.capacity_tonnes_y is not None
                    else f"Pellet map row {plant.map_row} has no capacity."
                )
                row["notes"] = _appended_note(row["notes"], note)
            else:
                if plant.capacity_tonnes_y is None:
                    raise InventoryError(
                        f"map row {plant.map_row}: matched unpublished record "
                        "has no converted capacity"
                    )
                action = "upgrade_existing_proxy_to_published"
                row["capacity_m3_y"] = _format_float(converted)
                row["capacity_status"] = PUBLISHED
                row["capacity_evidence"] = PUBLISHED_CONVERTED_EVIDENCE
                row["notes"] = _appended_note(
                    row["notes"],
                    f"Capacity upgraded from Canadian Biomass 2025 pellet map "
                    f"row {plant.map_row}: {plant.capacity_tonnes_y:,.0f} "
                    "finished t/yr at 650 kg/m3 = "
                    f"{converted:,.0f} m3/yr finished-pellet bulk volume; "
                    "presence source retained.",
                )
            crosswalk.append(
                {
                    **base_crosswalk,
                    "match_status": "matched_existing_pellet",
                    "matched_facility_id": row["facility_id"],
                    "matched_name": row["name"],
                    "match_method": method,
                    "match_distance_km": _format_float(distance, 3),
                    "action": action,
                    "notes": (
                        "Matched to an existing same-province PELLET record; "
                        "no duplicate row added."
                    ),
                }
            )
            continue

        colocated = None
        if plant.map_row in COLOCATED:
            colocated = rows_by_id.get(COLOCATED[plant.map_row])
            if colocated is None:
                raise InventoryError(
                    f"map row {plant.map_row}: co-located facility "
                    f"{COLOCATED[plant.map_row]} is missing"
                )
        new_row = _new_pellet_row(plant, colocated=colocated)
        if new_row["facility_id"] in rows_by_id:
            raise InventoryError(
                f"duplicate new facility id {new_row['facility_id']}"
            )
        rows_by_id[new_row["facility_id"]] = new_row
        new_ids.add(new_row["facility_id"])
        crosswalk.append(
            {
                **base_crosswalk,
                "match_status": "new_pellet",
                "matched_facility_id": new_row["facility_id"],
                "matched_name": new_row["name"],
                "match_method": (
                    "co_located_existing_facility"
                    if colocated is not None
                    else "new_city_centroid"
                ),
                "match_distance_km": (
                    _format_float(
                        _haversine_km(
                            plant.city_lat,
                            plant.city_lon,
                            float(colocated["lat"]),
                            float(colocated["lon"]),
                        ),
                        3,
                    )
                    if colocated is not None
                    else ""
                ),
                "action": (
                    "add_new_pellet_line_at_existing_mill"
                    if colocated is not None
                    else "add_new_pellet_facility"
                ),
                "notes": (
                    "New separate PELLET record; co-located with an existing "
                    "non-pellet mill but represents a distinct product line."
                    if colocated is not None
                    else "New PELLET record located at the city centroid."
                ),
            }
        )

    rows = list(rows_by_id.values())
    province_order = {province: index for index, province in enumerate(PROVINCES)}
    rows.sort(
        key=lambda row: (
            province_order[row["province"]],
            row["facility_class"],
            row["facility_id"],
        )
    )
    crosswalk.sort(key=lambda row: int(row["map_row"]))

    # Fail closed on duplicate pellet identities.  The two Lauzon rows have the
    # same company name but different cities, so name+city is the key.
    seen_pellet: dict[tuple[str, str], str] = {}
    for row in rows:
        if row["facility_class"] != "PELLET":
            continue
        key = (
            _normalise_name(row["name"]),
            row["province"],
        )
        if key in seen_pellet:
            raise InventoryError(
                "duplicate normalised pellet name in province "
                f"{row['province']}: {row['name']!r} and "
                f"{seen_pellet[key]!r}"
            )
        seen_pellet[key] = row["name"]

    coverage = _coverage(rows, new_ids)
    sources = _build_sources(generated_utc)
    summary = _summary(
        generated_utc=generated_utc,
        rows=rows,
        base_rows=base_rows,
        crosswalk=crosswalk,
        coverage=coverage,
        sources=sources,
    )
    summary["inputs"] = {
        str(inventory_v0_path.relative_to(REPO_ROOT)): _sha256(inventory_v0_path),
        str(pellet_extract_path.relative_to(REPO_ROOT)): _sha256(
            pellet_extract_path
        ),
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    fieldnames = list(base_rows[0].keys()) + V1_EXTRA_FIELDS
    _write_csv(output_dir / "national_facilities.csv", fieldnames, rows)
    _write_csv(output_dir / "dedup_crosswalk.csv", DEDUP_FIELDS, crosswalk)
    _write_csv(output_dir / "province_coverage.csv", COVERAGE_FIELDS, coverage)
    _write_csv(output_dir / "sources.csv", SOURCE_FIELDS, sources)
    (output_dir / "NATIONAL_FACILITY_INVENTORY_V1.md").write_text(
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
                "new_records": summary["counts"]["new_records"],
                "published_records": summary["counts"]["published_records"],
                "new_published_capacity_m3_y": summary["capacity"][
                    "new_published_capacity_m3_y"
                ],
                "output_dir": str(OUTPUT_DIR.relative_to(REPO_ROOT)),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
