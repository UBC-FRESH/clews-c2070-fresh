"""Build national facility inventory v0 from the BC derived layer and OpenStreetMap.

The inventory is fail-closed on unknown input classes and never invents capacity.
Every output row carries a source id, source URL, licence, and evidence tier.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import re
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from shapely.geometry import Point, shape
from shapely.prepared import prep

REPO_ROOT = Path(__file__).resolve().parents[3]
OUTPUT_DIR = (
    REPO_ROOT / "data/derived/national/facilities/national_facility_inventory_v0"
)
BC_INPUT = REPO_ROOT / "data/derived/national/bc_chain/facilities_bc.csv"
BOUNDARY_INPUT = (
    REPO_ROOT / "data/raw/boundaries/canada_adm1_geoboundaries_simplified.geojson"
)
CACHE_DIR = REPO_ROOT / "tmp/national_facility_inventory/osm_cache"

OVERPASS_ENDPOINT = "https://overpass.openstreetmap.fr/api/interpreter"
OSM_SOURCE_ID = "osm_overpass_canada"
BC_SOURCE_ID = "bc_mills_db_2015"
BOUNDARY_SOURCE_ID = "canada_adm1_boundaries"
USER_AGENT = "clews-c2070-fresh-facility-inventory/0.1 (UBC-FRESH research)"

CANADA_BBOX = (41.6, -141.1, 83.2, -52.6)
PROVINCES: dict[str, str] = {
    "BC": "British Columbia",
    "AB": "Alberta",
    "SK": "Saskatchewan",
    "MB": "Manitoba",
    "ON": "Ontario",
    "QC": "Quebec",
    "NB": "New Brunswick",
    "NS": "Nova Scotia",
    "PE": "Prince Edward Island",
    "NL": "Newfoundland and Labrador",
    "YT": "Yukon",
    "NT": "Northwest Territories",
    "NU": "Nunavut",
}
SHAPE_NAME_TO_CODE = {
    "British Columbia": "BC",
    "Alberta": "AB",
    "Saskatchewan": "SK",
    "Manitoba": "MB",
    "Ontario": "ON",
    "Quebec": "QC",
    "New Brunswick": "NB",
    "Nova Scotia": "NS",
    "Prince Edward Island": "PE",
    "Newfoundland and Labrador": "NL",
    "Yukon": "YT",
    "Northwest Territories": "NT",
    "Nunavut": "NU",
}
CLASS_ORDER = ("SAW", "PULP", "PANEL", "PELLET", "BIO", "OTHER")
BC_CLASS_MAP = {
    "SAW": "SAW",
    "PLY": "PANEL",
    "OSB": "PANEL",
    "PAP": "PULP",
    "NSP": "PULP",
    "PEL": "PELLET",
    "BIO": "BIO",
}
FIELDNAMES = [
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
    "notes",
]
COVERAGE_FIELDS = [
    "province",
    "province_name",
    "records",
    "classes",
    "capacity_known_count",
    "capacity_unknown_count",
    "coordinate_count",
    "coordinate_missing_count",
    "source_coverage",
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
    "notes",
]

STRONG_NAME_RE = re.compile(
    r"\b(?:sawmill|saw mill|lumber|pulp|paper|pellet|plywood|osb|"
    r"oriented strand|veneer|particleboard|mdf|fibreboard|hardboard|timber|"
    r"biomass|bioenergy|cogeneration|newsprint|kraft|forest products)\b",
    re.IGNORECASE,
)
EXCLUDED_NAME_RE = re.compile(
    r"\b(?:plastics?|shoe|delftware|paper tube|paper products?|packaging|design|"
    r"loggers?|logging|forestry|consult\w*|museum|shop|store|retail|office|"
    r"laborator\w*|pharmaceutical|building components?|roof\w*|insulat\w*|"
    r"greenhouse|nursery|windsor plywood|home depot|rona|canadian tire|"
    r"home hardware|building supplies|lumber yard|machinery|sharpening|"
    r"shelters?|showroom|waste area|very old)\b|"
    r"\bbuilding\s*$",
    re.IGNORECASE,
)
NON_MILL_NAME_RE = re.compile(
    r"\b(?:yard|log sort|truss|flooring|timberframe|timber frame|firewood|"
    r"recycl\w*|preserv\w*|mat|bridge|pellet stove|stove)\b",
    re.IGNORECASE,
)
NON_WOOD_BIOMASS_RE = re.compile(
    r"\b(?:biogas|landfill|manure|digester|agricultural|farm|ethanol|compost)\b",
    re.IGNORECASE,
)
PRODUCT_ALLOWED = {
    "wood",
    "timber",
    "lumber",
    "pulp",
    "paper",
    "pellet",
    "pellets",
    "plywood",
    "osb",
    "veneer",
    "particleboard",
    "mdf",
    "fibreboard",
    "hardboard",
    "biomass",
    "bioenergy",
    "chips",
    "hogfuel",
    "sawdust",
    "logs",
    "log",
    "hardwood",
    "softwood",
    "sawmill",
    "panel",
    "panels",
}
PRODUCT_DISALLOWED = {
    "equipment",
    "tool",
    "tools",
    "machinery",
    "machine",
    "systems",
    "system",
    "heating",
    "floor",
    "floors",
    "roof",
    "roofing",
    "structure",
    "structures",
    "assembly",
    "assemblies",
    "composite",
    "composites",
    "furniture",
    "cabinet",
    "cabinets",
    "door",
    "doors",
    "window",
    "windows",
    "gates",
    "fencing",
    "stalls",
    "livestock",
    "horse",
    "rodeo",
    "bison",
    "cattle",
    "wellhead",
    "pens",
    "corrals",
    "squeeze",
    "chutes",
    "hydraulic",
    "feeders",
    "handling",
    "pharmaceutical",
    "plastic",
    "plastics",
    "tube",
}
INDUSTRIAL_CLASS_MAP = {
    "pulp_mill": "PULP",
    "paper_mill": "PULP",
    "pellet_mill": "PELLET",
    "panel": "PANEL",
    "sawmill": "SAW",
    "wood": "SAW",
    "timber": "SAW",
    "biomass": "BIO",
    "bioenergy": "BIO",
}
LEGAL_SUFFIX_RE = re.compile(
    r"\b(?:ltd|limited|inc|incorporated|corp|corporation|ulc|lp|llp|co|company|"
    r"the|canada|industries|forest|products|mill|mills|division)\b",
    re.IGNORECASE,
)
GENERIC_NAME_TOKENS = {
    "ltd",
    "limited",
    "inc",
    "incorporated",
    "corp",
    "corporation",
    "ulc",
    "lp",
    "llp",
    "the",
    "and",
    "canada",
    "industries",
    "forest",
    "products",
    "mill",
    "mills",
    "division",
}


@dataclass
class Facility:
    facility_id: str
    name: str
    facility_class: str
    province: str
    lat: float | None
    lon: float | None
    capacity_m3_y: float | None
    capacity_status: str
    status: str
    source_id: str
    source_url: str
    licence: str
    evidence_tier: str
    notes: str
    osm_type: str = ""
    osm_id: int | None = None
    tags: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Boundary:
    province: str
    prepared: Any
    buffered: Any


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _format_float(value: float | None, digits: int = 6) -> str:
    if value is None:
        return ""
    text = f"{value:.{digits}f}".rstrip("0").rstrip(".")
    return text or "0"


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(
    path: Path, fieldnames: list[str], rows: Iterable[dict[str, Any]]
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def load_boundaries(path: Path = BOUNDARY_INPUT) -> list[Boundary]:
    """Load Canadian ADM1 polygons, smallest first for deterministic overlap."""
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    boundaries: list[Boundary] = []
    for feature in payload["features"]:
        name = feature["properties"].get("shapeName", "")
        province = SHAPE_NAME_TO_CODE.get(name)
        if province is None:
            raise ValueError(f"Unrecognised ADM1 shapeName in {path}: {name!r}")
        geometry = shape(feature["geometry"])
        buffered = prep(geometry.buffer(0.05)) if province == "BC" else None
        boundaries.append(
            Boundary(
                province=province,
                prepared=prep(geometry),
                buffered=buffered,
            )
        )
    if {item.province for item in boundaries} != set(PROVINCES):
        raise ValueError("ADM1 boundary file does not cover all 13 jurisdictions")
    return sorted(boundaries, key=lambda item: item.prepared.context.area)


def province_for_point(
    lat: float, lon: float, boundaries: list[Boundary], *, allow_buffer: bool = False
) -> tuple[str | None, bool]:
    """Return (province, used_buffer); buffer tolerates simplified coastlines."""
    point = Point(lon, lat)
    for boundary in boundaries:
        if boundary.prepared.covers(point):
            return boundary.province, False
    if allow_buffer:
        for boundary in boundaries:
            if boundary.buffered is not None and boundary.buffered.covers(point):
                return boundary.province, True
    return None, False


def _normalise_name(name: str) -> str:
    text = re.sub(r"\s+", " ", name).strip()
    duplicated = re.match(r"^(.+?)-\1$", text, flags=re.IGNORECASE)
    if duplicated:
        text = duplicated.group(1).strip()
    return text


def _name_tokens(name: str) -> set[str]:
    cleaned = LEGAL_SUFFIX_RE.sub(" ", name.lower())
    return {
        token
        for token in re.findall(r"[a-z0-9]+", cleaned)
        if len(token) >= 4 and token not in GENERIC_NAME_TOKENS
    }


def _haversine_km(lat_a: float, lon_a: float, lat_b: float, lon_b: float) -> float:
    radius_km = 6371.0088
    phi_a, phi_b = math.radians(lat_a), math.radians(lat_b)
    delta_phi = phi_b - phi_a
    delta_lambda = math.radians(lon_b - lon_a)
    hav = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi_a) * math.cos(phi_b) * math.sin(delta_lambda / 2) ** 2
    )
    return 2 * radius_km * math.asin(min(1.0, math.sqrt(hav)))


def _parse_coordinate(
    raw_lat: str, raw_lon: str, context: str
) -> tuple[float | None, float | None]:
    lat_text, lon_text = raw_lat.strip(), raw_lon.strip()
    if not lat_text and not lon_text:
        return None, None
    if not lat_text or not lon_text:
        raise ValueError(f"{context}: latitude and longitude must both be present")
    try:
        lat, lon = float(lat_text), float(lon_text)
    except ValueError as error:
        raise ValueError(
            f"{context}: invalid coordinate {raw_lat!r}, {raw_lon!r}"
        ) from error
    if not (CANADA_BBOX[0] <= lat <= CANADA_BBOX[2]):
        raise ValueError(f"{context}: latitude {lat} outside Canada")
    if not (CANADA_BBOX[1] <= lon <= CANADA_BBOX[3]):
        raise ValueError(f"{context}: longitude {lon} outside Canada")
    return lat, lon


def read_bc_facilities(
    path: Path = BC_INPUT, boundaries: list[Boundary] | None = None
) -> tuple[list[Facility], dict[str, int]]:
    """Normalise the BC derived layer without inventing missing fields."""
    boundaries = boundaries or load_boundaries()
    rows = _read_csv(path)
    required = {
        "facility_id",
        "facility_class",
        "name",
        "lat",
        "lon",
        "capacity_m3_y",
        "mills_db_capacity_m3_y",
        "notes",
    }
    missing = required - set(rows[0]) if rows else required
    if missing:
        raise ValueError(f"{path}: missing required columns {sorted(missing)}")

    facilities: list[Facility] = []
    audit = Counter()
    for position, row in enumerate(rows, start=2):
        facility_id = row["facility_id"].strip()
        if not facility_id:
            raise ValueError(f"{path}:{position}: blank facility_id")
        source_class = row["facility_class"].strip().upper()
        if source_class not in BC_CLASS_MAP:
            raise ValueError(
                f"{path}:{position}: unexpected BC facility_class {source_class!r}"
            )
        facility_class = BC_CLASS_MAP[source_class]

        name = _normalise_name(row["name"]) or _normalise_name(
            row.get("mills_db_name", "")
        )
        if not name:
            name = ""

        lat, lon = _parse_coordinate(row["lat"], row["lon"], f"{path}:{position}")
        coordinate_note = ""
        if lat is not None and lon is not None:
            province, used_buffer = province_for_point(
                lat, lon, boundaries, allow_buffer=True
            )
            if province != "BC":
                coordinate_note = (
                    "source coordinate failed BC polygon validation and was dropped; "
                    f"point fell in {province or 'no Canadian province'}"
                )
                lat, lon = None, None
                audit["bc_coordinates_dropped"] += 1
            else:
                audit["bc_coordinates_valid"] += 1
                if used_buffer:
                    coordinate_note = (
                        "coordinate accepted within 5 km of the simplified BC "
                        "boundary (coastal tolerance)"
                    )
                    audit["bc_coordinates_buffered"] += 1
        else:
            audit["bc_coordinates_missing"] += 1

        capacity_text = row["capacity_m3_y"].strip()
        capacity = float(capacity_text) if capacity_text else None
        if capacity is not None and capacity < 0:
            raise ValueError(f"{path}:{position}: negative capacity {capacity}")
        capacity_status = "published" if capacity is not None else "not_published"
        evidence_tier = "A" if lat is not None else "B"

        notes = (
            "BC derived facility layer v0.1; "
            f"source class {source_class} mapped to {facility_class}; "
            "capacity is a 2015 Mills DB/FFCM processing-line infeed proxy, "
            "not licensed or current operating capacity; operating status not verified"
        )
        if row.get("notes", "").strip():
            notes += f"; source note: {row['notes'].strip()}"
        if coordinate_note:
            notes += f"; {coordinate_note}"

        facilities.append(
            Facility(
                facility_id=facility_id,
                name=name,
                facility_class=facility_class,
                province="BC",
                lat=lat,
                lon=lon,
                capacity_m3_y=capacity,
                capacity_status=capacity_status,
                status="unknown",
                source_id=BC_SOURCE_ID,
                source_url=("https://github.com/UBC-FRESH/clews-c2070-fresh-data"),
                licence=(
                    "Project-derived BC chain layer; upstream ISED Mills DB 2015 / "
                    "FFCM proxies. Upstream source is not relicensed here."
                ),
                evidence_tier=evidence_tier,
                notes=notes,
            )
        )
        audit["bc_rows"] += 1
    return facilities, dict(audit)


def _bbox_clause() -> str:
    south, west, north, east = CANADA_BBOX
    return f"({south},{west},{north},{east})"


def overpass_queries() -> dict[str, str]:
    bbox = _bbox_clause()
    return {
        "craft_sawmill": (
            f'[out:json][timeout:120];nwr["craft"="sawmill"]{bbox};out center tags;'
        ),
        "industrial_sawmill": (
            f'[out:json][timeout:120];nwr["industrial"="sawmill"]{bbox};'
            "out center tags;"
        ),
        "industrial_class": (
            '[out:json][timeout:120];nwr["industrial"~"^(sawmill|pulp_mill|'
            f'paper_mill|pellet_mill|wood|timber|panel|biomass|bioenergy)$",i]'
            f"{bbox};out center tags;"
        ),
        "works_product": (
            '[out:json][timeout:120];nwr["man_made"="works"]["product"~"wood|'
            'timber|lumber|pulp|paper|pellet|biomass|panel|plywood|osb",i]'
            f"{bbox};out center tags;"
        ),
        "works_industrial": (
            '[out:json][timeout:120];nwr["man_made"="works"]["industrial"~"pulp|'
            f'paper|sawmill|pellet",i]{bbox};out center tags;'
        ),
        "power_plant_biomass": (
            '[out:json][timeout:120];nwr["power"="plant"]["plant:source"~"biomass|'
            f'wood",i]{bbox};out center tags;'
        ),
        "power_generator_biomass": (
            '[out:json][timeout:120];nwr["power"="generator"]'
            '["generator:source"~"biomass|wood",i]'
            f"{bbox};out center tags;"
        ),
        "named_works": (
            f'[out:json][timeout:120];nwr["man_made"="works"]["name"]{bbox};'
            "out center tags;"
        ),
        "landuse_named": (
            '[out:json][timeout:120];nwr["landuse"="industrial"]["name"~"sawmill|'
            'saw mill|pulp mill|paper mill|pellet mill|plywood|osb",i]'
            f"{bbox};out center tags;"
        ),
    }


def fetch_overpass(
    key: str,
    query: str,
    cache_dir: Path = CACHE_DIR,
    endpoint: str = OVERPASS_ENDPOINT,
) -> dict[str, Any]:
    """Load a cached Overpass response or fetch it once into the cache."""
    cache_path = cache_dir / f"{key}.json"
    if cache_path.exists():
        with cache_path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    cache_dir.mkdir(parents=True, exist_ok=True)
    body = urllib.parse.urlencode({"data": query}).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=body,
        headers={
            "User-Agent": USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        payload = json.load(response)
    with cache_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)
    return payload


def _osm_tags(element: dict[str, Any]) -> dict[str, str]:
    return {str(key): str(value) for key, value in element.get("tags", {}).items()}


def _osm_coordinate(element: dict[str, Any]) -> tuple[float, float] | None:
    if element.get("type") == "node":
        if "lat" in element and "lon" in element:
            return float(element["lat"]), float(element["lon"])
        return None
    center = element.get("center")
    if center and "lat" in center and "lon" in center:
        return float(center["lat"]), float(center["lon"])
    return None


def _tags_text(tags: dict[str, str]) -> str:
    return " ".join(
        tags.get(key, "")
        for key in (
            "name",
            "operator",
            "product",
            "industrial",
            "craft",
            "description",
            "plant:source",
            "generator:source",
        )
    )


def _product_tokens(tags: dict[str, str]) -> set[str]:
    raw = tags.get("product", "").lower()
    return {token for token in re.split(r"[^a-z0-9]+", raw) if token}


def _has_clear_product(tags: dict[str, str]) -> bool:
    tokens = _product_tokens(tags)
    if not tokens or tokens & PRODUCT_DISALLOWED:
        return False
    return bool(tokens & PRODUCT_ALLOWED)


def _classify_osm(tags: dict[str, str]) -> str:
    text = _tags_text(tags)
    industrial = tags.get("industrial", "").lower()
    product_tokens = _product_tokens(tags)
    operator = tags.get("operator", "").lower()
    name = tags.get("name", "")

    if industrial in {"pulp_mill", "paper_mill"}:
        return "PULP"
    if industrial == "pellet_mill":
        return "PELLET"
    if industrial == "panel":
        return "PANEL"
    if re.search(
        r"\b(?:pellet|pellets|granul[eé]s?)\b", text, re.IGNORECASE
    ) or product_tokens & {"pellet", "pellets"}:
        return "PELLET"
    if re.search(
        r"\b(?:pulp|paper|newsprint|kraft|tissue|cardboard|containerboard|"
        r"p[aâ]te|papier)\b",
        text,
        re.IGNORECASE,
    ):
        return "PULP"
    if re.search(
        r"\b(?:plywood|osb|oriented strand|panel|panneaux|veneer|particleboard|"
        r"mdf|fibreboard|hardboard|uniboard)\b",
        text,
        re.IGNORECASE,
    ) or product_tokens & {"plywood", "osb", "veneer", "particleboard", "mdf"}:
        return "PANEL"
    if "norbord" in operator:
        return "PANEL"
    if NON_MILL_NAME_RE.search(name):
        return "OTHER"
    if re.search(
        r"\b(?:sawmill|saw mill|scierie|lumber|timber|forest products|"
        r"wood products)\b",
        text,
        re.IGNORECASE,
    ):
        return "SAW"
    if industrial in {"sawmill", "wood", "timber"}:
        return "SAW"
    if product_tokens & {"lumber", "timber", "logs", "log"}:
        return "SAW"
    if re.search(
        r"\b(?:biomass|bioenergy|cogeneration|power plant|generator|energy)\b",
        text,
        re.IGNORECASE,
    ):
        return "BIO"
    return "OTHER"


def _osm_status(tags: dict[str, str]) -> str:
    for prefix in ("abandoned", "disused", "construction", "proposed"):
        if any(key == prefix or key.startswith(f"{prefix}:") for key in tags):
            return prefix
    operational = tags.get("operational_status", "").lower()
    if operational in {"operational", "operating"}:
        return "operating"
    return "unknown"


def _has_direct_facility_tag(tags: dict[str, str]) -> bool:
    if tags.get("craft", "").lower() == "sawmill":
        return True
    if tags.get("industrial", "").lower() in INDUSTRIAL_CLASS_MAP:
        return True
    if tags.get("man_made", "").lower() == "works" and _has_clear_product(tags):
        return True
    return bool(
        tags.get("power", "").lower() in {"plant", "generator"}
        and re.search(
            r"biomass|wood",
            tags.get("plant:source", "") + " " + tags.get("generator:source", ""),
            re.IGNORECASE,
        )
    )


def _is_relevant_osm(tags: dict[str, str]) -> bool:
    name = tags.get("name", "").strip()
    if name and len(name) < 2:
        return False
    if name and EXCLUDED_NAME_RE.search(name):
        return False
    if _has_direct_facility_tag(tags):
        is_non_wood_biomass = bool(
            tags.get("power", "").lower() in {"plant", "generator"}
            and NON_WOOD_BIOMASS_RE.search(name)
        )
        return not is_non_wood_biomass
    return bool(name and STRONG_NAME_RE.search(name))


def _osm_evidence_tier(tags: dict[str, str]) -> str:
    return "C" if _has_direct_facility_tag(tags) else "D"


def _osm_notes(tags: dict[str, str], element_type: str, element_id: int) -> str:
    interesting = {
        key: value
        for key, value in tags.items()
        if key
        in {
            "name",
            "operator",
            "craft",
            "industrial",
            "man_made",
            "product",
            "power",
            "plant:source",
            "generator:source",
            "landuse",
            "addr:city",
        }
    }
    tag_text = "; ".join(f"{key}={value}" for key, value in sorted(interesting.items()))
    return (
        f"OSM {element_type}/{element_id}; {tag_text}; capacity not published in OSM; "
        "presence does not establish operating status"
    )


def read_osm_facilities(
    boundaries: list[Boundary] | None = None,
    cache_dir: Path = CACHE_DIR,
    queries: dict[str, str] | None = None,
) -> tuple[list[Facility], dict[str, Any], dict[str, Any]]:
    """Read cached Overpass responses, validate points, and normalise facilities."""
    boundaries = boundaries or load_boundaries()
    queries = queries or overpass_queries()
    facilities_by_key: dict[tuple[str, int], Facility] = {}
    audit = Counter()
    timestamps: set[str] = set()
    query_meta: dict[str, Any] = {}

    for key, query in queries.items():
        payload = fetch_overpass(key, query, cache_dir=cache_dir)
        elements = payload.get("elements", [])
        query_meta[key] = {
            "elements": len(elements),
            "query_sha256": hashlib.sha256(query.encode("utf-8")).hexdigest(),
            "endpoint": OVERPASS_ENDPOINT,
        }
        timestamp = payload.get("osm3s", {}).get("timestamp_osm_base")
        if timestamp:
            timestamps.add(timestamp)

        for element in elements:
            element_type = element.get("type", "")
            element_id = element.get("id")
            if element_type not in {"node", "way", "relation"} or element_id is None:
                audit["osm_malformed_elements"] += 1
                continue
            key_id = (element_type, int(element_id))
            if key_id in facilities_by_key:
                audit["osm_duplicate_elements"] += 1
                continue

            tags = _osm_tags(element)
            if not _is_relevant_osm(tags):
                audit["osm_not_relevant"] += 1
                continue
            coordinate = _osm_coordinate(element)
            if coordinate is None:
                audit["osm_missing_coordinate"] += 1
                continue
            lat, lon = coordinate
            if not (
                CANADA_BBOX[0] <= lat <= CANADA_BBOX[2]
                and CANADA_BBOX[1] <= lon <= CANADA_BBOX[3]
            ):
                audit["osm_outside_canada"] += 1
                continue
            province = province_for_point(lat, lon, boundaries)[0]
            if province is None:
                audit["osm_outside_canada"] += 1
                continue

            facility_class = _classify_osm(tags)
            name = _normalise_name(tags.get("name", ""))
            if not name:
                name = _normalise_name(tags.get("operator", ""))
            notes = _osm_notes(tags, element_type, int(element_id))
            if not name:
                notes += "; unnamed OSM feature"
            facility_id = f"OSM-{province}-{element_type[0].upper()}{element_id}"
            facilities_by_key[key_id] = Facility(
                facility_id=facility_id,
                name=name,
                facility_class=facility_class,
                province=province,
                lat=lat,
                lon=lon,
                capacity_m3_y=None,
                capacity_status="not_published",
                status=_osm_status(tags),
                source_id=OSM_SOURCE_ID,
                source_url=(
                    f"https://www.openstreetmap.org/{element_type}/{element_id}"
                ),
                licence="ODbL-1.0",
                evidence_tier=_osm_evidence_tier(tags),
                notes=notes,
                osm_type=element_type,
                osm_id=int(element_id),
                tags=tags,
            )
            audit["osm_retained_raw"] += 1

    facilities = list(facilities_by_key.values())
    facilities = _dedupe_osm_elements(facilities, audit)
    facilities.sort(key=lambda item: item.facility_id)
    return (
        facilities,
        dict(audit),
        {
            "timestamps": sorted(timestamps),
            "queries": query_meta,
        },
    )


def _dedupe_osm_elements(
    facilities: list[Facility], audit: Counter[str]
) -> list[Facility]:
    """Collapse node/way duplicates with identical names within 250 m."""
    ordered = sorted(
        facilities,
        key=lambda item: (
            item.evidence_tier != "C",
            item.osm_type == "node",
            item.facility_id,
        ),
    )
    kept: list[Facility] = []
    for facility in ordered:
        if not facility.name:
            kept.append(facility)
            continue
        match = next(
            (
                candidate
                for candidate in kept
                if candidate.name.lower() == facility.name.lower()
                and candidate.lat is not None
                and facility.lat is not None
                and _haversine_km(
                    candidate.lat,
                    candidate.lon or 0.0,
                    facility.lat,
                    facility.lon or 0.0,
                )
                <= 0.25
            ),
            None,
        )
        if match is None:
            kept.append(facility)
            continue
        match.notes += f"; duplicate_osm={facility.osm_type}/{facility.osm_id}"
        audit["osm_duplicate_names"] += 1
    return kept


def _find_bc_overlap(
    osm: Facility, bc_facilities: list[Facility]
) -> tuple[Facility | None, float | None]:
    if osm.lat is None or osm.lon is None:
        return None, None
    osm_tokens = _name_tokens(osm.name)
    candidates: list[tuple[float, Facility]] = []
    for bc in bc_facilities:
        if bc.lat is None or bc.lon is None:
            continue
        if bc.facility_class != osm.facility_class:
            continue
        distance = _haversine_km(bc.lat, bc.lon, osm.lat, osm.lon)
        name_overlap = bool(osm_tokens & _name_tokens(bc.name))
        if distance <= 1.0 or (distance <= 5.0 and name_overlap):
            candidates.append((distance, bc))
    if not candidates:
        return None, None
    distance, facility = min(candidates, key=lambda item: item[0])
    return facility, distance


def merge_osm_into_bc(
    bc_facilities: list[Facility], osm_facilities: list[Facility]
) -> tuple[list[Facility], list[Facility], list[dict[str, Any]]]:
    """Drop OSM features that are the same facility as a BC source record."""
    remaining: list[Facility] = []
    crosswalk: list[dict[str, Any]] = []
    for osm in osm_facilities:
        overlap, distance = _find_bc_overlap(osm, bc_facilities)
        if overlap is None:
            remaining.append(osm)
            continue
        overlap.notes += f"; dedup_osm={osm.osm_type}/{osm.osm_id}"
        crosswalk.append(
            {
                "bc_facility_id": overlap.facility_id,
                "osm_type": osm.osm_type,
                "osm_id": osm.osm_id,
                "osm_facility_id": osm.facility_id,
                "distance_km": round(distance or 0.0, 3),
                "facility_class": osm.facility_class,
            }
        )
    return bc_facilities, remaining, crosswalk


def _facility_row(facility: Facility) -> dict[str, Any]:
    return {
        "facility_id": facility.facility_id,
        "name": facility.name,
        "facility_class": facility.facility_class,
        "province": facility.province,
        "lat": _format_float(facility.lat),
        "lon": _format_float(facility.lon),
        "capacity_m3_y": _format_float(facility.capacity_m3_y),
        "capacity_status": facility.capacity_status,
        "status": facility.status,
        "source_id": facility.source_id,
        "source_url": facility.source_url,
        "licence": facility.licence,
        "evidence_tier": facility.evidence_tier,
        "notes": facility.notes,
    }


def build_coverage(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_province: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_province[row["province"]].append(row)

    coverage: list[dict[str, Any]] = []
    for province, province_name in PROVINCES.items():
        province_rows = by_province.get(province, [])
        class_counts = Counter(row["facility_class"] for row in province_rows)
        source_counts = Counter(row["source_id"] for row in province_rows)
        known = sum(bool(row["capacity_m3_y"]) for row in province_rows)
        coordinates = sum(bool(row["lat"] and row["lon"]) for row in province_rows)
        coverage.append(
            {
                "province": province,
                "province_name": province_name,
                "records": len(province_rows),
                "classes": ";".join(
                    f"{name}:{class_counts[name]}"
                    for name in CLASS_ORDER
                    if class_counts[name]
                )
                or "none",
                "capacity_known_count": known,
                "capacity_unknown_count": len(province_rows) - known,
                "coordinate_count": coordinates,
                "coordinate_missing_count": len(province_rows) - coordinates,
                "source_coverage": ";".join(
                    f"{name}:{source_counts[name]}" for name in sorted(source_counts)
                )
                or "none",
            }
        )
    return coverage


def _sources(retrieved_utc: str, osm_timestamp: str) -> list[dict[str, str]]:
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
                "data/derived/national/bc_chain/facilities_bc.csv"
            ),
            "retrieved_utc": retrieved_utc,
            "coverage": "British Columbia",
            "notes": (
                "Capacities are normalised processing-line infeed proxies from the "
                "2015 Mills DB/FFCM lineage, not licensed or current capacity. "
                "81 source records have no coordinate and are retained explicitly."
            ),
        },
        {
            "source_id": OSM_SOURCE_ID,
            "title": "OpenStreetMap facility features via Overpass API",
            "publisher": "OpenStreetMap contributors",
            "source_url": OVERPASS_ENDPOINT,
            "licence": "ODbL-1.0",
            "licence_url": "https://opendatacommons.org/licenses/odbl/1-0/",
            "access_method": "Overpass API, cached JSON responses",
            "retrieved_utc": osm_timestamp or retrieved_utc,
            "coverage": "Canada, 13 jurisdictions",
            "notes": (
                "Tags used: craft=sawmill, industrial=sawmill and related values, "
                "man_made=works with wood/product or mill-name tags, power=plant or "
                "generator with biomass/wood source, and named industrial landuse. "
                "OSM features are mapped presence, not verified operating status."
            ),
        },
        {
            "source_id": BOUNDARY_SOURCE_ID,
            "title": "Canada ADM1 boundaries (validation only)",
            "publisher": "GeoBoundaries",
            "source_url": "https://www.geoboundaries.org/",
            "licence": "CC-BY-4.0",
            "licence_url": "https://creativecommons.org/licenses/by/4.0/",
            "access_method": "Existing local GeoJSON",
            "retrieved_utc": retrieved_utc,
            "coverage": "Canada, 13 jurisdictions",
            "notes": (
                "Used only to validate coordinates and assign OSM features to "
                "provinces/territories; no facility attributes are taken from it."
            ),
        },
    ]


def _summary(
    rows: list[dict[str, Any]],
    coverage: list[dict[str, Any]],
    bc_audit: dict[str, int],
    osm_audit: dict[str, int],
    osm_meta: dict[str, Any],
    crosswalk: list[dict[str, Any]],
    retrieved_utc: str,
) -> dict[str, Any]:
    class_counts = Counter(row["facility_class"] for row in rows)
    source_counts = Counter(row["source_id"] for row in rows)
    tier_counts = Counter(row["evidence_tier"] for row in rows)
    province_counts = Counter(row["province"] for row in rows)
    capacity_known = sum(bool(row["capacity_m3_y"]) for row in rows)
    unnamed_records = sum(not row["name"].strip() for row in rows)
    return {
        "artifact": "national_facility_inventory_v0",
        "artifact_version": "0.1.0",
        "generated_utc": retrieved_utc,
        "jurisdictions_expected": len(PROVINCES),
        "jurisdictions_with_records": sum(
            1 for item in coverage if item["records"] > 0
        ),
        "records_total": len(rows),
        "records_by_province": {
            province: province_counts.get(province, 0) for province in PROVINCES
        },
        "records_by_class": {
            facility_class: class_counts.get(facility_class, 0)
            for facility_class in CLASS_ORDER
        },
        "capacity_known_count": capacity_known,
        "capacity_unknown_count": len(rows) - capacity_known,
        "capacity_coverage_share": (capacity_known / len(rows) if rows else 0.0),
        "unnamed_records": unnamed_records,
        "unnamed_share": unnamed_records / len(rows) if rows else 0.0,
        "source_counts": dict(sorted(source_counts.items())),
        "evidence_tier_counts": dict(sorted(tier_counts.items())),
        "dedup": {
            "osm_merged_into_bc": len(crosswalk),
            "crosswalk": crosswalk,
        },
        "validation": {
            "bc": bc_audit,
            "osm": osm_audit,
        },
        "osm": osm_meta,
        "caveats": [
            (
                "No capacity is imputed: OSM records have blank capacity and "
                "capacity_status=not_published."
            ),
            (
                "BC capacities are 2015 Mills DB/FFCM processing-line infeed proxies, "
                "not licensed or current operating capacity."
            ),
            (
                "OSM presence does not establish operating status; status is unknown "
                "unless explicit lifecycle tags exist."
            ),
            (
                "Province/territory coverage is a count of mapped records, not a "
                "complete census of Canadian facilities."
            ),
            (
                "BC source coordinates within 5 km of the simplified BC boundary "
                "are accepted with a note; all other coordinates must fall inside "
                "the named province or territory."
            ),
            "No UI or public OSeCAN surface is changed by this artifact.",
        ],
    }


def _report(
    summary: dict[str, Any],
    coverage: list[dict[str, Any]],
    sources: list[dict[str, str]],
) -> str:
    lines = [
        "# National Facility Inventory v0",
        "",
        f"Generated {summary['generated_utc']}.",
        "",
        (
            "Scope: Canadian forest-product and wood-biomass facilities assembled from "
            "the project BC derived facility layer and OpenStreetMap. The inventory is "
            "a mapped-record inventory, not a complete national census. Capacity is "
            "reported only where the input publishes it; OSM capacity is left blank."
        ),
        "",
        "## Record counts by province and class",
        "",
        "| Province | Records | Classes | Capacity known | Coordinates | Sources |",
        "|---|---:|---|---:|---:|---|",
    ]
    for item in coverage:
        lines.append(
            f"| {item['province']} ({item['province_name']}) | {item['records']} | "
            f"{item['classes']} | {item['capacity_known_count']} | "
            f"{item['coordinate_count']}/{item['records']} | "
            f"{item['source_coverage']} |"
        )
    lines += [
        "",
        (
            f"Total records: **{summary['records_total']}** across "
            f"{summary['jurisdictions_with_records']} of "
            f"{summary['jurisdictions_expected']} jurisdictions."
        ),
        "",
        "## Capacity coverage",
        "",
        (
            f"- Capacity known: {summary['capacity_known_count']} of "
            f"{summary['records_total']} records "
            f"({summary['capacity_coverage_share']:.1%})."
        ),
        (
            "- All known capacities are BC source proxies; every OSM record is blank "
            "with `capacity_status=not_published`."
        ),
        "- Missing capacity is never replaced by zero or a class average.",
        (
            f"- Unnamed OSM features retained explicitly: {summary['unnamed_records']} "
            f"({summary['unnamed_share']:.1%}); `name` is blank rather than invented."
        ),
        "",
        "## Sources and evidence tiers",
        "",
        "| Source | Licence | Records |",
        "|---|---|---:|",
    ]
    for source in sources:
        count = summary["source_counts"].get(source["source_id"], 0)
        if source["source_id"] == BOUNDARY_SOURCE_ID:
            continue
        lines.append(f"| `{source['source_id']}` | {source['licence']} | {count} |")
    lines += [
        "",
        (
            "Evidence tiers: A = BC record with validated coordinate; B = BC record "
            "with missing/invalid coordinate; C = OSM feature with a direct facility "
            "tag; D = OSM feature inferred from a mill-like name only."
        ),
        "",
        f"Tier counts: `{json.dumps(summary['evidence_tier_counts'], sort_keys=True)}`.",
        "",
        "## Deduplication and validation",
        "",
        (
            f"- OSM features merged into BC records: "
            f"{summary['dedup']['osm_merged_into_bc']}."
        ),
        (
            f"- OSM elements discarded as outside Canada or not a Canadian province: "
            f"{summary['validation']['osm'].get('osm_outside_canada', 0)}."
        ),
        (
            f"- OSM elements not relevant after tag/name screening: "
            f"{summary['validation']['osm'].get('osm_not_relevant', 0)}."
        ),
        (
            f"- OSM duplicate OSM IDs: "
            f"{summary['validation']['osm'].get('osm_duplicate_elements', 0)}; "
            f"duplicate names within 250 m: "
            f"{summary['validation']['osm'].get('osm_duplicate_names', 0)}."
        ),
        (
            f"- BC coordinates validated: "
            f"{summary['validation']['bc'].get('bc_coordinates_valid', 0)} "
            f"({summary['validation']['bc'].get('bc_coordinates_buffered', 0)} "
            f"within the 5 km coastal tolerance); "
            f"missing: {summary['validation']['bc'].get('bc_coordinates_missing', 0)}; "
            f"dropped after BC polygon check: "
            f"{summary['validation']['bc'].get('bc_coordinates_dropped', 0)}."
        ),
        (
            "- Coordinates must fall inside the named province/territory polygon. "
            "BC source points within 5 km of the simplified BC coastline are "
            "accepted with a note; BC records with missing coordinates are "
            "retained, not dropped."
        ),
        "",
        "## Caveats",
        "",
    ]
    lines.extend(f"- {caveat}" for caveat in summary["caveats"])
    lines += [
        "",
        "## Reproduce and verify",
        "",
        "```bash",
        ".venv/bin/python -m workflows.national.facilities.national_facility_inventory",
        ".venv/bin/pytest tests/test_national_facility_inventory.py",
        (
            ".venv/bin/ruff check workflows/national/facilities "
            "tests/test_national_facility_inventory.py"
        ),
        "```",
        "",
        (
            "Overpass responses are cached under `tmp/national_facility_inventory/osm_cache/` "
            "for this build. The OSM snapshot timestamp is recorded in `summary.json` "
            "and `manifest.json`."
        ),
        "",
    ]
    return "\n".join(lines)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def build(
    output_dir: Path = OUTPUT_DIR,
    bc_input: Path = BC_INPUT,
    boundary_input: Path = BOUNDARY_INPUT,
    cache_dir: Path = CACHE_DIR,
) -> dict[str, Any]:
    """Build the national inventory and return its summary."""
    retrieved_utc = _utc_now()
    boundaries = load_boundaries(boundary_input)
    bc_facilities, bc_audit = read_bc_facilities(bc_input, boundaries)
    osm_facilities, osm_audit, osm_meta = read_osm_facilities(boundaries, cache_dir)
    bc_facilities, osm_facilities, crosswalk = merge_osm_into_bc(
        bc_facilities, osm_facilities
    )
    facilities = sorted(
        bc_facilities + osm_facilities,
        key=lambda item: (item.province, item.facility_class, item.facility_id),
    )

    facility_ids = [item.facility_id for item in facilities]
    if len(facility_ids) != len(set(facility_ids)):
        duplicates = [
            item for item, count in Counter(facility_ids).items() if count > 1
        ]
        raise ValueError(f"Duplicate facility_id values: {duplicates}")
    for item in facilities:
        if item.facility_class not in CLASS_ORDER:
            raise ValueError(f"{item.facility_id}: invalid class {item.facility_class}")
        if item.province not in PROVINCES:
            raise ValueError(f"{item.facility_id}: invalid province {item.province}")
        if item.capacity_m3_y is not None and item.capacity_status != "published":
            raise ValueError(
                f"{item.facility_id}: capacity present but status is "
                f"{item.capacity_status}"
            )
        if item.capacity_m3_y is None and item.capacity_status != "not_published":
            raise ValueError(
                f"{item.facility_id}: blank capacity but status is "
                f"{item.capacity_status}"
            )

    rows = [_facility_row(item) for item in facilities]
    coverage = build_coverage(rows)
    osm_timestamp = osm_meta["timestamps"][-1] if osm_meta["timestamps"] else ""
    summary = _summary(
        rows,
        coverage,
        bc_audit,
        osm_audit,
        osm_meta,
        crosswalk,
        retrieved_utc,
    )
    sources = _sources(retrieved_utc, osm_timestamp)

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(output_dir / "national_facilities.csv", FIELDNAMES, rows)
    _write_csv(output_dir / "province_coverage.csv", COVERAGE_FIELDS, coverage)
    _write_csv(output_dir / "sources.csv", SOURCE_FIELDS, sources)
    _write_csv(
        output_dir / "dedup_crosswalk.csv",
        [
            "bc_facility_id",
            "osm_type",
            "osm_id",
            "osm_facility_id",
            "distance_km",
            "facility_class",
        ],
        crosswalk,
    )
    _write_json(output_dir / "summary.json", summary)
    (output_dir / "NATIONAL_FACILITY_INVENTORY_V0.md").write_text(
        _report(summary, coverage, sources), encoding="utf-8"
    )

    outputs: dict[str, Any] = {}
    for path in sorted(output_dir.iterdir()):
        if not path.is_file() or path.name == "manifest.json":
            continue
        outputs[path.name] = {
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }
    manifest = {
        "artifact": "national_facility_inventory_v0",
        "artifact_version": "0.1.0",
        "generated_utc": retrieved_utc,
        "inputs": {
            str(path.relative_to(REPO_ROOT)): _sha256(path)
            for path in (bc_input, boundary_input)
        },
        "outputs": outputs,
        "record_counts": {
            "total": len(rows),
            "by_province": summary["records_by_province"],
            "by_class": summary["records_by_class"],
            "capacity_known": summary["capacity_known_count"],
            "capacity_unknown": summary["capacity_unknown_count"],
        },
        "sources": sources,
        "osm": osm_meta,
        "dedup": summary["dedup"],
        "validation": summary["validation"],
        "reproduce": (
            ".venv/bin/python -m "
            "workflows.national.facilities.national_facility_inventory"
        ),
    }
    _write_json(output_dir / "manifest.json", manifest)
    return summary


def main() -> None:
    summary = build()
    print(
        json.dumps(
            {
                "records_total": summary["records_total"],
                "jurisdictions_with_records": summary["jurisdictions_with_records"],
                "capacity_known_count": summary["capacity_known_count"],
                "output_dir": str(OUTPUT_DIR.relative_to(REPO_ROOT)),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
