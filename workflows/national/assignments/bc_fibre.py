"""BC facility coordinate recovery and diagnostic contract parsing.

The CFS Mills DB (2015) and the FFCM 2018 facility registry do not share a
stable facility-code system: several identical codes refer to different mills.
A code match is therefore only accepted when a name, city, or coordinate signal
corroborates it.  The recovery layer never forces a match.

Two BC Fibre Database workbooks are parsed for diagnostic evidence:

* ``Sawmill Fibre Allocation Input V3.xls`` contains dimensionless residue
  allocation shares from sawmills to pulp/paper destinations.  It does not
  contain residue volumes.
* ``TSA Tenure Apportionment V5.xls`` contains TSA-level AAC apportionment and
  licence volumes.  It has no facility identifier.

Consequently the diagnostic contract table can carry real TSA volumes and real
residue shares, but it cannot fabricate facility-level contract volumes.  Stage
A keeps its ``diagnostic_absent`` fallback unless a row supplies both a matched
facility and a volume.
"""

from __future__ import annotations

import argparse
import csv
import math
import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "BC_FIBRE_EVIDENCE_TIER",
    "BcFibreError",
    "ContractRow",
    "CrosswalkResult",
    "FacilityMatch",
    "FfcmSite",
    "MillsRow",
    "SawmillAllocation",
    "TsaApportionment",
    "build_contract_rows",
    "build_crosswalk",
    "city_matches",
    "company_matches",
    "haversine_km",
    "normalize_city",
    "normalize_code",
    "normalize_company",
    "normalize_text",
    "read_ffcm_sites",
    "read_mills_db",
    "read_sawmill_allocation",
    "read_tsa_tenure_apportionment",
    "write_bc_fibre_outputs",
]

BC_FIBRE_EVIDENCE_TIER = "bc_fibre_db_2018_diagnostic"
DEFAULT_MILLS_DB = Path("tmp/External/CFS/00 - resources/millsdb_08242015.csv")
DEFAULT_FFCM_REGISTRY = Path("tmp/FCM/Carbon/Archive/original_list_BP.xlsx")
DEFAULT_SAWMILL_ALLOCATION = Path(
    "tmp/BC Fibre Database Benchmark 2018 V3  August/"
    "Pulp Mill Detail Module/Sawmill Fibre Allocation Input V3.xls"
)
DEFAULT_TSA_APPORTIONMENT = Path(
    "tmp/BC Fibre Database Benchmark 2018 V3  August/"
    "Tenure to Capacity Module/TSA Tenure Apportionment V5.xls"
)
DEFAULT_OUT_DIR = Path("data/derived/national/assignments/tsa24_pilot")

BC_LAT_RANGE = (48.0, 60.1)
BC_LON_RANGE = (-139.2, -113.9)

_GENERIC_COMPANY_TOKENS = frozenset(
    {
        "ltd",
        "limited",
        "inc",
        "incorporated",
        "corp",
        "corporation",
        "co",
        "company",
        "ulc",
        "lp",
        "llp",
        "partnership",
        "the",
        "industries",
        "industry",
        "forest",
        "forests",
        "products",
        "product",
        "group",
        "division",
        "div",
        "sawmill",
        "sawmills",
        "mill",
        "mills",
        "wood",
        "lumber",
        "enterprises",
        "enterprise",
        "holdings",
        "resources",
        "specialty",
        "specialties",
        "timber",
        "pulp",
        "paper",
        "energy",
        "power",
        "pellet",
        "pellets",
        "shake",
        "shingle",
        "cedar",
        "custom",
        "milling",
        "logging",
        "management",
        "ventures",
        "development",
        "dev",
        "manufacturing",
        "mfg",
        "forestry",
        "fibre",
        "fiber",
        "and",
        "of",
    }
)
_GENERIC_PLACE_TOKENS = frozenset(
    {
        "river",
        "lake",
        "falls",
        "bay",
        "creek",
        "point",
        "island",
        "city",
        "north",
        "south",
        "east",
        "west",
        "grand",
        "forks",
        "fort",
        "st",
        "saint",
        "john",
        "james",
        "george",
        "prince",
        "new",
        "upper",
        "lower",
    }
)

_TSA_EXCLUDED_SHEETS = frozenset({"Overview", "README"})


class BcFibreError(RuntimeError):
    """Raised when a BC Fibre Database input cannot be parsed defensibly."""


@dataclass(frozen=True, slots=True)
class MillsRow:
    facility_code: str
    normalized_code: str
    operator: str
    name: str
    city: str
    normalized_city: str
    company: str
    facility_class: str
    total_capacity_m3_y: float
    lat: float
    lon: float
    source_row: int


@dataclass(frozen=True, slots=True)
class FfcmSite:
    facility_id: str
    normalized_code: str
    site_name: str
    site_owner: str
    city: str
    normalized_city: str
    company: str
    facility_class: str
    lat: float | None
    lon: float | None
    source_row: int

    @property
    def distinctive_tokens(self) -> frozenset[str]:
        return frozenset(
            token
            for token in _text_tokens(self.site_name)
            if token not in _text_tokens(self.site_owner)
            and token not in _GENERIC_PLACE_TOKENS
        )


@dataclass(frozen=True, slots=True)
class FacilityMatch:
    facility_id: str
    facility_class: str
    site: FfcmSite | None
    mills: MillsRow | None
    match_method: str
    match_confidence: str
    distance_km: float | None
    evidence: str


@dataclass(frozen=True, slots=True)
class CrosswalkResult:
    matches: tuple[FacilityMatch, ...]
    conflicts: tuple[dict[str, Any], ...]
    method_counts: Mapping[str, int]
    confidence_counts: Mapping[str, int]
    unmatched: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SawmillAllocation:
    residue_stream: str
    source_owner: str
    source_mill: str
    source_location: str
    region: str
    destination_mill: str
    allocation_share: float
    cost_class: str
    period_year: int
    source_sheet: str
    source_row: int


@dataclass(frozen=True, slots=True)
class TsaApportionment:
    tsa: str
    tenure_class: str
    tenure_subclass: str
    licence_number: str
    holder: str
    expiry: str
    row_type: str
    period_year: int
    volume_m3: float
    source_sheet: str
    source_row: int


@dataclass(frozen=True, slots=True)
class ContractRow:
    contract_id: str
    facility_id: str
    facility_name: str
    tsa_tfl: str
    tenure_apportionment_class: str
    allocated_volume: float | None
    unit: str
    period_year: int
    capacity: float | None
    source: str
    evidence_tier: str
    match_confidence: str
    match_method: str
    crosswalk_status: str
    allocation_share: float | None
    source_name: str
    source_location: str
    residue_stream: str
    notes: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_id": self.contract_id,
            "facility_id": self.facility_id,
            "facility_name": self.facility_name,
            "tsa_tfl": self.tsa_tfl,
            "tenure_apportionment_class": self.tenure_apportionment_class,
            "allocated_volume": "" if self.allocated_volume is None else self.allocated_volume,
            "unit": self.unit,
            "period_year": self.period_year,
            "capacity": "" if self.capacity is None else self.capacity,
            "source": self.source,
            "evidence_tier": self.evidence_tier,
            "match_confidence": self.match_confidence,
            "match_method": self.match_method,
            "crosswalk_status": self.crosswalk_status,
            "allocation_share": "" if self.allocation_share is None else self.allocation_share,
            "source_name": self.source_name,
            "source_location": self.source_location,
            "residue_stream": self.residue_stream,
            "notes": self.notes,
        }


def normalize_text(value: object) -> str:
    """Lowercase, strip accents, and collapse non-alphanumeric separators."""
    text = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode()
    text = text.lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_code(value: object) -> str:
    """Return a separator-free uppercase facility code."""
    return re.sub(r"[^A-Z0-9]", "", str(value).upper())


def normalize_company(value: object) -> str:
    """Return company tokens without legal and generic sector suffixes."""
    return " ".join(
        token
        for token in normalize_text(value).split()
        if token not in _GENERIC_COMPANY_TOKENS
    )


def normalize_city(value: object) -> str:
    """Normalize common BC place abbreviations before comparison."""
    replacements = {
        "saint": "st",
        "fort": "ft",
        "mount": "mt",
        "north": "n",
        "south": "s",
        "east": "e",
        "west": "w",
    }
    return " ".join(replacements.get(token, token) for token in normalize_text(value).split())


def _text_tokens(value: object) -> frozenset[str]:
    return frozenset(
        token
        for token in normalize_text(value).split()
        if token not in _GENERIC_COMPANY_TOKENS
    )


def _company_tokens(value: object) -> frozenset[str]:
    return frozenset(normalize_company(value).split())


def _levenshtein_distance(left: str, right: str) -> int:
    if left == right:
        return 0
    if abs(len(left) - len(right)) > 2:
        return 3
    previous = list(range(len(right) + 1))
    for left_index, left_char in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_char in enumerate(right, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[right_index] + 1,
                    previous[right_index - 1] + (left_char != right_char),
                )
            )
        previous = current
    return previous[-1]


def city_matches(left: str, right: str) -> bool:
    """Return True for exact, abbreviated, or one-edit city variants."""
    if not left or not right:
        return False
    if left == right:
        return True
    left_tokens = left.split()
    right_tokens = right.split()
    shared = min(len(left_tokens), len(right_tokens))
    if (
        shared > 0
        and left_tokens[:shared] == right_tokens[:shared]
        and len("".join(left_tokens[:shared])) >= 4
    ):
        return True
    return len(left) >= 6 and len(right) >= 6 and _levenshtein_distance(left, right) <= 1


def company_matches(left: str, right: str) -> bool:
    """Return True when two company strings share a distinctive token."""
    left_tokens = _company_tokens(left)
    right_tokens = _company_tokens(right)
    if not left_tokens or not right_tokens:
        return False
    if left_tokens == right_tokens:
        return True
    if any(len(token) >= 4 for token in left_tokens & right_tokens):
        return True
    union = left_tokens | right_tokens
    return (
        len(left_tokens & right_tokens) / len(union) >= 0.8
        and all(len(token) >= 3 for token in union)
    )


def haversine_km(lat_a: float, lon_a: float, lat_b: float, lon_b: float) -> float:
    """Return great-circle distance in kilometres."""
    radius_km = 6371.0088
    phi_a = math.radians(lat_a)
    phi_b = math.radians(lat_b)
    delta_phi = phi_b - phi_a
    delta_lambda = math.radians(lon_b - lon_a)
    hav = (
        math.sin(delta_phi / 2.0) ** 2
        + math.cos(phi_a) * math.cos(phi_b) * math.sin(delta_lambda / 2.0) ** 2
    )
    return 2.0 * radius_km * math.asin(math.sqrt(hav))


def _valid_bc_coordinate(lat: float, lon: float) -> bool:
    return (
        math.isfinite(lat)
        and math.isfinite(lon)
        and BC_LAT_RANGE[0] <= lat <= BC_LAT_RANGE[1]
        and BC_LON_RANGE[0] <= lon <= BC_LON_RANGE[1]
    )


def _number(value: object, name: str, *, default: float | None = None) -> float:
    text = str(value).strip()
    if text == "" or text.lower() in {"na", "nan", "none", "null"}:
        if default is not None:
            return default
        raise BcFibreError(f"{name} is missing")
    try:
        number = float(text)
    except ValueError as error:
        raise BcFibreError(f"{name} is not numeric: {value!r}") from error
    if not math.isfinite(number):
        raise BcFibreError(f"{name} is not finite: {value!r}")
    return number


def _read_ragged_csv(path: Path) -> list[dict[str, str]]:
    """Read the Mills DB, which has a trailing empty header cell and ragged rows."""
    if not path.exists():
        raise BcFibreError(f"required BC facility input is missing: {path}")
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.reader(handle)
        header = [value.strip() for value in next(reader, [])]
        if not header:
            raise BcFibreError(f"{path} has no header")
        rows = []
        for raw in reader:
            padded = list(raw) + [""] * (len(header) - len(raw))
            rows.append(dict(zip(header, padded)))
    if not rows:
        raise BcFibreError(f"{path} has no data rows")
    return rows


def read_mills_db(path: Path) -> tuple[MillsRow, ...]:
    """Parse BC rows from the 2015 CFS Mills DB."""
    rows = _read_ragged_csv(path)
    required = {"facility_c", "facility_o", "facility_n", "province", "facility_t", "total_capa", "latitude", "longitude"}
    missing = required - set(rows[0])
    if missing:
        raise BcFibreError(f"{path} is missing required columns: {sorted(missing)}")

    mills: list[MillsRow] = []
    for row_number, row in enumerate(rows, start=2):
        if str(row["province"]).strip().upper() != "BC":
            continue
        code = str(row["facility_c"]).strip()
        facility_class = str(row["facility_t"]).strip().upper()
        if not code or not facility_class:
            raise BcFibreError(f"{path}:{row_number} has no facility code or class")
        lat = _number(row["latitude"], f"{path}:{row_number} latitude")
        lon = _number(row["longitude"], f"{path}:{row_number} longitude")
        if not _valid_bc_coordinate(lat, lon):
            raise BcFibreError(f"{path}:{row_number} coordinate is outside BC: {lat},{lon}")
        mills.append(
            MillsRow(
                facility_code=code,
                normalized_code=normalize_code(code),
                operator=str(row["facility_o"]).strip(),
                name=str(row["facility_n"]).strip(),
                city=str(row["facility_n"]).strip(),
                normalized_city=normalize_city(row["facility_n"]),
                company=normalize_company(row["facility_o"]),
                facility_class=facility_class,
                total_capacity_m3_y=_number(
                    row["total_capa"], f"{path}:{row_number} total_capa", default=0.0
                ),
                lat=lat,
                lon=lon,
                source_row=row_number,
            )
        )
    if not mills:
        raise BcFibreError(f"{path} has no BC rows")
    return tuple(mills)


def read_ffcm_sites(path: Path) -> tuple[FfcmSite, ...]:
    """Read FFCM site names, owners, cities, and reference coordinates."""
    if not path.exists():
        raise BcFibreError(f"FFCM raw site registry is missing: {path}")
    try:
        import pandas as pd
    except ImportError as error:  # pragma: no cover - environment guard
        raise BcFibreError("pandas is required to read the FFCM site registry") from error

    frame = pd.read_excel(path, sheet_name="facility_list", header=0)
    required = {"cfs_id", "site_name", "site_own", "city", "prov", "simple_subsector", "lat", "long"}
    missing = required - set(frame.columns)
    if missing:
        raise BcFibreError(f"{path} is missing required columns: {sorted(missing)}")

    sites: list[FfcmSite] = []
    seen: set[str] = set()
    for index, row in frame.iterrows():
        facility_id = str(row["cfs_id"]).strip()
        if not facility_id or facility_id.lower() == "nan":
            continue
        if str(row["prov"]).strip().upper() != "BC":
            continue
        normalized = normalize_code(facility_id)
        if normalized in seen:
            continue
        seen.add(normalized)
        lat_value = row["lat"]
        lon_value = row["long"]
        lat = float(lat_value) if pd.notna(lat_value) else None
        lon = float(lon_value) if pd.notna(lon_value) else None
        if lat is not None and lon is not None and not _valid_bc_coordinate(lat, lon):
            raise BcFibreError(
                f"{path} row {index + 2} has a coordinate outside BC: {lat},{lon}"
            )
        sites.append(
            FfcmSite(
                facility_id=facility_id,
                normalized_code=normalized,
                site_name=str(row["site_name"]).strip(),
                site_owner=str(row["site_own"]).strip(),
                city=str(row["city"]).strip(),
                normalized_city=normalize_city(row["city"]),
                company=normalize_company(row["site_own"]),
                facility_class=str(row["simple_subsector"]).strip().upper(),
                lat=lat,
                lon=lon,
                source_row=int(index) + 2,
            )
        )
    if not sites:
        raise BcFibreError(f"{path} has no BC facility rows")
    return tuple(sites)


def _site_company_tokens(site: FfcmSite) -> frozenset[str]:
    prefix = str(site.site_name).split("-", 1)[0]
    return _company_tokens(site.site_owner) | _company_tokens(prefix)


def _company_is_unique(
    site: FfcmSite, candidates: Sequence[MillsRow]
) -> bool:
    company_tokens = _site_company_tokens(site)
    if not company_tokens:
        return False
    return (
        sum(
            1
            for candidate in candidates
            if company_matches(" ".join(company_tokens), candidate.company)
        )
        == 1
    )


def _name_hits(site: FfcmSite, mills: MillsRow) -> int:
    return len(site.distinctive_tokens & _text_tokens(f"{mills.name} {mills.operator}"))


def _candidate_evidence(site: FfcmSite, mills: MillsRow) -> str:
    hits = sorted(site.distinctive_tokens & _text_tokens(f"{mills.name} {mills.operator}"))
    return ";".join(hits)


def _distance(site: FfcmSite, mills: MillsRow) -> float | None:
    if site.lat is None or site.lon is None:
        return None
    return haversine_km(site.lat, site.lon, mills.lat, mills.lon)


def _matching_candidates(site: FfcmSite, mills_rows: Sequence[MillsRow]) -> list[MillsRow]:
    return [row for row in mills_rows if row.facility_class == site.facility_class]


def _unique_candidate(
    matches: Sequence[MillsRow],
) -> MillsRow | None:
    return matches[0] if len(matches) == 1 else None


def _confidence_for(method: str, site: FfcmSite, mills: MillsRow, distance_km: float | None) -> str:
    if method == "exact_code_confirmed":
        if city_matches(site.normalized_city, mills.normalized_city):
            return "high"
        if _name_hits(site, mills) >= 2:
            return "high"
        if distance_km is not None and distance_km <= 0.5:
            return "high"
        return "medium"
    if method == "mill_name_class":
        if _name_hits(site, mills) >= 2 and (
            company_matches(" ".join(_site_company_tokens(site)), mills.company)
            or city_matches(site.normalized_city, mills.normalized_city)
        ):
            return "high"
        return "medium"
    if method == "company_city_class":
        return "high" if city_matches(site.normalized_city, mills.normalized_city) else "medium"
    if method == "coordinate_corroborated":
        return "medium"
    if method == "coordinate_named":
        return "medium" if _name_hits(site, mills) >= 1 else "low"
    return "missing"


def build_crosswalk(
    facilities: Sequence[Mapping[str, str]],
    mills_rows: Sequence[MillsRow],
    sites: Sequence[FfcmSite],
) -> CrosswalkResult:
    """Match FFCM BC facilities to Mills DB rows without forcing a match."""
    site_by_code = {site.normalized_code: site for site in sites}
    mills_by_code: dict[str, list[MillsRow]] = defaultdict(list)
    for row in mills_rows:
        mills_by_code[row.normalized_code].append(row)

    matches: list[FacilityMatch] = []
    conflicts: list[dict[str, Any]] = []
    unmatched: list[str] = []
    method_counts: Counter[str] = Counter()
    confidence_counts: Counter[str] = Counter()

    for facility in facilities:
        facility_id = str(facility["facility"]).strip()
        facility_class = str(facility["facility_class"]).strip().upper()
        site = site_by_code.get(normalize_code(facility_id))
        candidates = _matching_candidates(site, mills_rows) if site else []
        match: FacilityMatch | None = None

        if site is None:
            unmatched.append(facility_id)
            matches.append(
                FacilityMatch(
                    facility_id=facility_id,
                    facility_class=facility_class,
                    site=None,
                    mills=None,
                    match_method="unmatched_no_registry",
                    match_confidence="missing",
                    distance_km=None,
                    evidence="FFCM raw site registry has no BC row for this facility",
                )
            )
            continue

        # 1. Exact code is a candidate, never an automatic match.
        code_candidates = [
            row for row in mills_by_code.get(site.normalized_code, []) if row.facility_class == facility_class
        ]
        if code_candidates:
            for row in code_candidates:
                distance = _distance(site, row)
                name_hits = _name_hits(site, row)
                confirmed = (
                    city_matches(site.normalized_city, row.normalized_city)
                    or name_hits >= 2
                    or (distance is not None and distance <= 1.5)
                )
                if confirmed:
                    distance_text = (
                        "distance_km=n/a"
                        if distance is None
                        else f"distance_km={distance:.3f}"
                    )
                    match = FacilityMatch(
                        facility_id=facility_id,
                        facility_class=facility_class,
                        site=site,
                        mills=row,
                        match_method="exact_code_confirmed",
                        match_confidence=_confidence_for(
                            "exact_code_confirmed", site, row, distance
                        ),
                        distance_km=distance,
                        evidence=(
                            f"normalized_code={site.normalized_code};"
                            f"mills_code={row.facility_code};"
                            f"city={site.city!r}->{row.city!r};"
                            f"name_tokens={_candidate_evidence(site, row)};"
                            f"{distance_text}"
                        ),
                    )
                    break
                conflicts.append(
                    {
                        "facility_id": facility_id,
                        "mills_facility_code": row.facility_code,
                        "conflict_type": "exact_code_name_or_city_conflict",
                        "ffcm_site_name": site.site_name,
                        "ffcm_city": site.city,
                        "mills_operator": row.operator,
                        "mills_city": row.city,
                        "distance_km": "" if distance is None else f"{distance:.3f}",
                        "name_tokens": _candidate_evidence(site, row),
                        "notes": "code match rejected; code systems are not stable across vintages",
                    }
                )

        # 2. Distinctive mill-name tokens, with company or city corroboration.
        if match is None and site.distinctive_tokens:
            site_company = " ".join(_site_company_tokens(site))
            name_candidates = [
                row
                for row in candidates
                if _name_hits(site, row) >= 2
                or (
                    _name_hits(site, row) >= 1
                    and (
                        company_matches(site_company, row.company)
                        or city_matches(site.normalized_city, row.normalized_city)
                    )
                )
            ]
            candidate = _unique_candidate(name_candidates)
            if candidate is not None:
                distance = _distance(site, candidate)
                match = FacilityMatch(
                    facility_id=facility_id,
                    facility_class=facility_class,
                    site=site,
                    mills=candidate,
                    match_method="mill_name_class",
                    match_confidence=_confidence_for("mill_name_class", site, candidate, distance),
                    distance_km=distance,
                    evidence=(
                        f"class={facility_class};"
                        f"name_tokens={_candidate_evidence(site, candidate)};"
                        f"company={site.site_owner!r}->{candidate.operator!r};"
                        f"city={site.city!r}->{candidate.city!r}"
                    ),
                )

        # 3. Company plus city, unique within the class.
        if match is None and site.company and site.normalized_city:
            site_company = " ".join(_site_company_tokens(site))
            company_city_candidates = [
                row
                for row in candidates
                if company_matches(site_company, row.company)
                and city_matches(site.normalized_city, row.normalized_city)
            ]
            candidate = _unique_candidate(company_city_candidates)
            if candidate is not None:
                distance = _distance(site, candidate)
                match = FacilityMatch(
                    facility_id=facility_id,
                    facility_class=facility_class,
                    site=site,
                    mills=candidate,
                    match_method="company_city_class",
                    match_confidence=_confidence_for("company_city_class", site, candidate, distance),
                    distance_km=distance,
                    evidence=(
                        f"class={facility_class};"
                        f"company={site.site_owner!r}->{candidate.operator!r};"
                        f"city={site.city!r}->{candidate.city!r}"
                    ),
                )

        # 4. Coordinate corroboration within 1.5 km.
        if match is None:
            site_company = " ".join(_site_company_tokens(site))
            coordinate_candidates: list[MillsRow] = []
            for row in candidates:
                distance = _distance(site, row)
                if distance is None or distance > 1.5:
                    continue
                name_hits = _name_hits(site, row)
                company_confirmed = company_matches(site_company, row.company)
                city_confirmed = city_matches(
                    site.normalized_city, row.normalized_city
                )
                if (
                    name_hits >= 1
                    and (company_confirmed or city_confirmed or distance <= 1.0)
                ) or (
                    company_confirmed and (city_confirmed or distance <= 0.5)
                ):
                    coordinate_candidates.append(row)

            candidate = _unique_candidate(coordinate_candidates)
            if candidate is not None:
                distance = _distance(site, candidate)
                match = FacilityMatch(
                    facility_id=facility_id,
                    facility_class=facility_class,
                    site=site,
                    mills=candidate,
                    match_method="coordinate_corroborated",
                    match_confidence=_confidence_for("coordinate_corroborated", site, candidate, distance),
                    distance_km=distance,
                    evidence=(
                        f"distance_km={distance:.3f};"
                        f"name_tokens={_candidate_evidence(site, candidate)};"
                        f"company={site.site_owner!r}->{candidate.operator!r};"
                        f"city={site.city!r}->{candidate.city!r}"
                    ),
                )

        # 5. Wider coordinate corroboration only with a name or company signal.
        if match is None:
            site_company = " ".join(_site_company_tokens(site))
            company_is_unique = _company_is_unique(site, candidates)
            wider_candidates: list[MillsRow] = []
            for row in candidates:
                distance = _distance(site, row)
                if distance is None or distance > 2.5:
                    continue
                name_hits = _name_hits(site, row)
                company_confirmed = company_matches(site_company, row.company)
                name_or_city = company_confirmed or city_matches(
                    site.normalized_city, row.normalized_city
                )
                if (name_hits >= 1 and name_or_city) or (
                    company_confirmed and distance <= 1.0 and company_is_unique
                ):
                    wider_candidates.append(row)

            candidate = _unique_candidate(wider_candidates)
            if candidate is not None:
                distance = _distance(site, candidate)
                match = FacilityMatch(
                    facility_id=facility_id,
                    facility_class=facility_class,
                    site=site,
                    mills=candidate,
                    match_method="coordinate_named",
                    match_confidence=_confidence_for("coordinate_named", site, candidate, distance),
                    distance_km=distance,
                    evidence=(
                        f"distance_km={distance:.3f};"
                        f"name_tokens={_candidate_evidence(site, candidate)};"
                        f"company={site.site_owner!r}->{candidate.operator!r};"
                        f"city={site.city!r}->{candidate.city!r}"
                    ),
                )

        if match is None:
            unmatched.append(facility_id)
            match = FacilityMatch(
                facility_id=facility_id,
                facility_class=facility_class,
                site=site,
                mills=None,
                match_method="unmatched",
                match_confidence="missing",
                distance_km=None,
                evidence=(
                    "no unique Mills DB candidate passed code, name, city, or "
                    "coordinate corroboration"
                ),
            )

        matches.append(match)
        method_counts[match.match_method] += 1
        confidence_counts[match.match_confidence] += 1

    return CrosswalkResult(
        matches=tuple(matches),
        conflicts=tuple(conflicts),
        method_counts=dict(method_counts),
        confidence_counts=dict(confidence_counts),
        unmatched=tuple(unmatched),
    )


def _read_processing_lines(path: Path) -> tuple[dict[str, list[str]], dict[str, float]]:
    if not path.exists():
        raise BcFibreError(f"required processing-line table is missing: {path}")
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    lines_by_facility: dict[str, list[str]] = defaultdict(list)
    capacity_by_facility: dict[str, float] = defaultdict(float)
    for row in rows:
        facility_id = str(row.get("facility", "")).strip()
        if not facility_id.startswith("BC-"):
            continue
        line_id = str(row.get("processing_line", "")).strip()
        if line_id:
            lines_by_facility[facility_id].append(line_id)
        unit = str(row.get("infeed_unit", "")).strip().lower()
        if unit != "m3/y":
            continue
        rate = _number(row.get("infeed_rate", ""), "infeed_rate", default=0.0)
        product = str(row.get("primary_product", "")).strip().lower()
        if rate > 0.0 and product:
            capacity_by_facility[facility_id] = max(
                capacity_by_facility[facility_id], rate
            )
    return (
        {key: sorted(values) for key, values in lines_by_facility.items()},
        dict(capacity_by_facility),
    )


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]], columns: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=list(columns),
            extrasaction="ignore",
            lineterminator="\n",
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _destination_aliases() -> dict[str, tuple[str, ...]]:
    """Unique diagnostic aliases only; ambiguous labels stay ambiguous."""
    return {
        "taylor": ("BC-PAP-1319",),
        "domtar": ("BC-PAP-511",),
        "celgar": ("BC-PAP-179",),
        "skookumchuck": ("BC-PAP-1163",),
        "hspp": ("BC-PAP-938",),
        "nanaimo fp": ("BC-PAP-731",),
    }


def _ambiguous_destinations() -> frozenset[str]:
    return frozenset(
        {
            "canfor pg",
            "paper excellence",
            "cariboo qrp",
            "catalyst",
            "neucel",
            "other",
        }
    )


def _destination_lookup(
    label: str,
    site_by_code: Mapping[str, FfcmSite],
) -> tuple[str, str, str, str]:
    """Return (facility_id, facility_name, match_confidence, match_method)."""
    normalized = normalize_text(label)
    aliases = _destination_aliases()
    if normalized in aliases:
        facility_id = aliases[normalized][0]
        site = site_by_code.get(normalize_code(facility_id))
        return facility_id, site.site_name if site else facility_id, "high", "destination_alias"
    if normalized in _ambiguous_destinations():
        return "", "", "ambiguous", "destination_alias_ambiguous"
    return "", "", "unmatched", "destination_alias_unmatched"


def read_sawmill_allocation(path: Path) -> tuple[SawmillAllocation, ...]:
    """Parse the dimensionless sawmill residue allocation matrix."""
    if not path.exists():
        raise BcFibreError(f"BC sawmill allocation workbook is missing: {path}")
    try:
        import pandas as pd
    except ImportError as error:  # pragma: no cover - environment guard
        raise BcFibreError("pandas is required to read the sawmill allocation workbook") from error

    workbook = pd.ExcelFile(path)
    allocations: list[SawmillAllocation] = []
    for sheet_name in workbook.sheet_names:
        frame = pd.read_excel(path, sheet_name=sheet_name, header=None)
        if frame.shape[0] < 4 or frame.shape[1] < 4:
            continue
        first_cell = frame.iat[0, 0]
        header_column = 2 if frame.shape[1] > 2 else 1
        is_other_biomass = (
            (first_cell is None or str(first_cell).strip().lower() in {"", "nan", "none"})
            and normalize_text(frame.iat[0, header_column]) == "destination mill"
        )
        if is_other_biomass:
            allocations.extend(_parse_other_biomass_sheet(frame, sheet_name))
            continue
        if normalize_text(frame.iat[0, 4]) != "destination mill":
            continue
        allocations.extend(_parse_sawmill_sheet(frame, sheet_name))
    if not allocations:
        raise BcFibreError(f"{path} has no sawmill allocation rows")
    return tuple(allocations)


def _parse_sawmill_sheet(frame: Any, sheet_name: str) -> list[SawmillAllocation]:
    rows: list[SawmillAllocation] = []
    destinations = {
        column: str(frame.iat[1, column]).strip()
        for column in range(4, frame.shape[1], 2)
        if str(frame.iat[1, column]).strip() not in {"", "nan", "Total"}
    }
    for row_index in range(4, frame.shape[0]):
        source_owner = str(frame.iat[row_index, 0]).strip()
        source_mill = str(frame.iat[row_index, 1]).strip()
        source_location = str(frame.iat[row_index, 2]).strip()
        region = str(frame.iat[row_index, 3]).strip()
        if not source_mill or source_mill.lower() == "nan":
            continue
        for column, destination in destinations.items():
            value = frame.iat[row_index, column]
            if str(value).strip() in {"", "nan", "None"}:
                continue
            share = _number(value, f"{sheet_name}:{row_index + 1} allocation")
            if not 0.0 <= share <= 1.0:
                raise BcFibreError(
                    f"{sheet_name} row {row_index + 1} allocation share is outside [0,1]: {share}"
                )
            if share <= 0.0:
                continue
            cost_class = frame.iat[row_index, column + 1] if column + 1 < frame.shape[1] else ""
            rows.append(
                SawmillAllocation(
                    residue_stream=sheet_name,
                    source_owner=source_owner,
                    source_mill=source_mill,
                    source_location=source_location,
                    region=region,
                    destination_mill=destination,
                    allocation_share=share,
                    cost_class=str(cost_class).strip(),
                    period_year=2018,
                    source_sheet=sheet_name,
                    source_row=row_index + 1,
                )
            )
    return rows


def _parse_other_biomass_sheet(frame: Any, sheet_name: str) -> list[SawmillAllocation]:
    rows: list[SawmillAllocation] = []
    destinations = {
        column: str(frame.iat[1, column]).strip()
        for column in range(2, frame.shape[1], 2)
        if str(frame.iat[1, column]).strip() not in {"", "nan", "Total"}
    }
    for row_index in range(3, frame.shape[0]):
        region = str(frame.iat[row_index, 0]).strip()
        fuel_source = str(frame.iat[row_index, 1]).strip()
        if not fuel_source or fuel_source.lower() == "nan":
            continue
        for column, destination in destinations.items():
            value = frame.iat[row_index, column]
            if str(value).strip() in {"", "nan", "None"}:
                continue
            share = _number(value, f"{sheet_name}:{row_index + 1} allocation")
            if not 0.0 <= share <= 1.0:
                raise BcFibreError(
                    f"{sheet_name} row {row_index + 1} allocation share is outside [0,1]: {share}"
                )
            if share <= 0.0:
                continue
            cost_class = frame.iat[row_index, column + 1] if column + 1 < frame.shape[1] else ""
            rows.append(
                SawmillAllocation(
                    residue_stream=sheet_name,
                    source_owner="",
                    source_mill=fuel_source,
                    source_location="",
                    region=region,
                    destination_mill=destination,
                    allocation_share=share,
                    cost_class=str(cost_class).strip(),
                    period_year=2018,
                    source_sheet=sheet_name,
                    source_row=row_index + 1,
                )
            )
    return rows


def _tsa_year_columns(frame: Any) -> tuple[int, dict[int, int]]:
    for row_index in range(min(6, frame.shape[0])):
        years: dict[int, int] = {}
        for column in range(frame.shape[1]):
            value = frame.iat[row_index, column]
            try:
                year = int(float(value))
            except (TypeError, ValueError):
                continue
            if 2000 <= year <= 2100:
                years[column] = year
        if len(years) >= 10:
            return row_index, years
    raise BcFibreError("could not locate the TSA year header")


def _find_row(frame: Any, predicate: Any, start: int = 0) -> int | None:
    for row_index in range(start, frame.shape[0]):
        if predicate(row_index):
            return row_index
    return None


def _cell(frame: Any, row: int, column: int) -> str:
    if row < 0 or row >= frame.shape[0] or column < 0 or column >= frame.shape[1]:
        return ""
    value = frame.iat[row, column]
    return "" if str(value).strip().lower() in {"", "nan", "none"} else str(value).strip()


def _year_values(frame: Any, row: int, year_columns: Mapping[int, int]) -> dict[int, float]:
    values: dict[int, float] = {}
    for column, year in year_columns.items():
        text = _cell(frame, row, column)
        if not text:
            continue
        try:
            values[year] = float(text)
        except ValueError as error:
            raise BcFibreError(
                f"TSA cell at row {row + 1}, column {column + 1} is not numeric: {text!r}"
            ) from error
    return values


def canonical_tenure_class(value: object) -> str:
    """Collapse workbook spelling variants into one tenure-class vocabulary."""
    text = normalize_text(value)
    if not text:
        return ""
    if "replaceable forest" in text or text in {
        "replaceable licences",
        "replaceable licenses",
        "replaceable licence",
        "replaceable license",
    }:
        return "Replaceable Forest Licences"
    if "non replaceable" in text or "nonreplaceable" in text:
        return "Non-Replaceable Timber Sale Licences"
    if "bcts" in text or "bc timber sales" in text:
        return "BC Timber Sales"
    if "pulpwood" in text:
        return "Pulpwood Agreement"
    if "woodlot" in text:
        return "Woodlot Licence"
    if "un apportioned" in text or "unapportioned" in text:
        return "Un-Apportioned (Reserve)"
    if "forest service reserve" in text:
        return "Forest Service Reserve"
    return str(value).strip()


def _tsa_name(frame: Any, header_row: int) -> str:
    candidates: list[str] = []
    for row_index in range(header_row + 1, min(header_row + 5, frame.shape[0])):
        for column in range(min(5, frame.shape[1])):
            text = _cell(frame, row_index, column)
            if "TSA" in text.upper():
                candidates.append(text.replace("TSA", "").strip() or text)
    if not candidates:
        return ""
    preferred = [text for text in candidates if not text.startswith("(") and "OLD QUEEN" not in text.upper()]
    return (preferred or candidates)[0]


def _parse_tsa_sheet(frame: Any, sheet_name: str) -> list[TsaApportionment]:
    year_header_row, year_columns = _tsa_year_columns(frame)
    year_start = min(year_columns)
    aac_row = _find_row(
        frame,
        lambda row: _cell(frame, row, year_start - 1).upper() == "AAC",
        start=year_header_row + 1,
    )
    if aac_row is None:
        raise BcFibreError(f"{sheet_name}: could not locate the AAC row")
    tsa = _tsa_name(frame, year_header_row)
    if not tsa:
        raise BcFibreError(f"{sheet_name}: could not locate the TSA name")

    rows: list[TsaApportionment] = []
    green_aac_row = _find_row(
        frame,
        lambda row: _cell(frame, row, year_start - 1).upper() == "GREEN AAC",
        start=aac_row + 1,
    )
    for row_index, row_type, label in (
        (aac_row, "aac", "AAC"),
        (green_aac_row, "green_aac", "Green AAC") if green_aac_row is not None else (-1, "", ""),
    ):
        if row_index < 0:
            continue
        for year, volume in _year_values(frame, row_index, year_columns).items():
            rows.append(
                TsaApportionment(
                    tsa=tsa,
                    tenure_class=label,
                    tenure_subclass="",
                    licence_number="",
                    holder="",
                    expiry="",
                    row_type=row_type,
                    period_year=year,
                    volume_m3=volume,
                    source_sheet=sheet_name,
                    source_row=row_index + 1,
                )
            )

    apportionment_header = _find_row(
        frame,
        lambda row: "INDICATED APPORTIONMENT" in _cell(frame, row, 1).upper(),
        start=aac_row + 1,
    )
    if apportionment_header is not None:
        current_class = ""
        for row_index in range(apportionment_header, frame.shape[0]):
            label = _cell(frame, row_index, 1)
            subclass = _cell(frame, row_index, 2)
            if not label and not subclass:
                break
            if "TOTAL" in f"{label} {subclass}".upper():
                break
            if subclass:
                current_class = canonical_tenure_class(subclass)
            elif label and "INDICATED APPORTIONMENT" not in label.upper():
                current_class = canonical_tenure_class(label)
            if not current_class:
                continue
            for year, volume in _year_values(frame, row_index, year_columns).items():
                rows.append(
                    TsaApportionment(
                        tsa=tsa,
                        tenure_class=current_class,
                        tenure_subclass="",
                        licence_number="",
                        holder="",
                        expiry="",
                        row_type="apportionment_class",
                        period_year=year,
                        volume_m3=volume,
                        source_sheet=sheet_name,
                        source_row=row_index + 1,
                    )
                )

    licence_header = _find_row(
        frame,
        lambda row: _cell(frame, row, 1).upper() == "TENURE" and _cell(frame, row, 2).upper() == "NUMBER",
        start=aac_row + 1,
    )
    if licence_header is not None:
        current_class = ""
        for row_index in range(licence_header + 1, frame.shape[0]):
            tenure_class = _cell(frame, row_index, 1)
            licence_number = _cell(frame, row_index, 2)
            holder = _cell(frame, row_index, 3)
            expiry = _cell(frame, row_index, 4)
            if not tenure_class and not licence_number and not holder:
                break
            if "TOTAL" in tenure_class.upper():
                break
            if tenure_class:
                current_class = canonical_tenure_class(tenure_class)
            if not current_class or not (licence_number or holder):
                continue
            for year, volume in _year_values(frame, row_index, year_columns).items():
                rows.append(
                    TsaApportionment(
                        tsa=tsa,
                        tenure_class=current_class,
                        tenure_subclass="",
                        licence_number=licence_number,
                        holder=holder,
                        expiry=expiry,
                        row_type="licence",
                        period_year=year,
                        volume_m3=volume,
                        source_sheet=sheet_name,
                        source_row=row_index + 1,
                    )
                )

    return rows


def read_tsa_tenure_apportionment(path: Path) -> tuple[TsaApportionment, ...]:
    """Parse TSA AAC apportionment and licence volumes by year."""
    if not path.exists():
        raise BcFibreError(f"BC TSA tenure apportionment workbook is missing: {path}")
    try:
        import pandas as pd
    except ImportError as error:  # pragma: no cover - environment guard
        raise BcFibreError("pandas is required to read the TSA apportionment workbook") from error

    workbook = pd.ExcelFile(path)
    records: list[TsaApportionment] = []
    for sheet_name in workbook.sheet_names:
        if sheet_name in _TSA_EXCLUDED_SHEETS or sheet_name.startswith("Region"):
            continue
        frame = pd.read_excel(path, sheet_name=sheet_name, header=None)
        if frame.shape[0] < 10:
            continue
        try:
            records.extend(_parse_tsa_sheet(frame, sheet_name))
        except BcFibreError:
            continue
    if not records:
        raise BcFibreError(f"{path} has no parseable TSA apportionment rows")
    return tuple(records)


def build_contract_rows(
    allocations: Sequence[SawmillAllocation],
    apportionments: Sequence[TsaApportionment],
    facilities: Sequence[Mapping[str, str]],
    sites: Sequence[FfcmSite],
    *,
    period_year: int = 2018,
) -> tuple[ContractRow, ...]:
    """Build the diagnostic contract table without inventing volumes or IDs."""
    site_by_code = {site.normalized_code: site for site in sites}
    facility_by_code = {
        normalize_code(str(row["facility"])): row for row in facilities
    }
    rows: list[ContractRow] = []

    for apportionment in apportionments:
        if apportionment.period_year != period_year:
            continue
        if apportionment.row_type != "apportionment_class":
            continue
        if apportionment.volume_m3 <= 0.0:
            continue
        rows.append(
            ContractRow(
                contract_id=f"TSA-{normalize_code(apportionment.tsa)}-{period_year}-{normalize_code(apportionment.tenure_class)}-{apportionment.source_row}",
                facility_id="",
                facility_name="",
                tsa_tfl=apportionment.tsa,
                tenure_apportionment_class=apportionment.tenure_class,
                allocated_volume=apportionment.volume_m3,
                unit="m3/y",
                period_year=period_year,
                capacity=None,
                source=f"{apportionment.source_sheet}:{apportionment.source_row}",
                evidence_tier=BC_FIBRE_EVIDENCE_TIER,
                match_confidence="tsa_only",
                match_method="tsa_name_exact",
                crosswalk_status="tsa_only_no_facility_field",
                allocation_share=None,
                source_name="",
                source_location="",
                residue_stream="",
                notes=(
                    "TSA-level AAC apportionment from the BC Fibre Database; "
                    "the workbook has no facility identifier, so no facility "
                    "contract is inferred."
                ),
            )
        )

    for allocation in allocations:
        if allocation.period_year != period_year:
            continue
        facility_id, facility_name, confidence, method = _destination_lookup(
            allocation.destination_mill,
            site_by_code,
        )
        if facility_id:
            facility_row = facility_by_code.get(normalize_code(facility_id))
            if facility_row is None:
                facility_id = ""
                facility_name = ""
                confidence = "unmatched"
                method = "destination_alias_not_in_ffcm"
        rows.append(
            ContractRow(
                contract_id=(
                    f"SAW-{normalize_code(allocation.source_mill)}-"
                    f"{normalize_code(allocation.destination_mill)}-"
                    f"{normalize_code(allocation.residue_stream)}-"
                    f"{allocation.source_row}"
                ),
                facility_id=facility_id,
                facility_name=facility_name,
                tsa_tfl="",
                tenure_apportionment_class="residue_allocation",
                allocated_volume=None,
                unit="share_of_residue",
                period_year=period_year,
                capacity=None,
                source=f"{allocation.source_sheet}:{allocation.source_row}",
                evidence_tier=BC_FIBRE_EVIDENCE_TIER,
                match_confidence=confidence,
                match_method=method,
                crosswalk_status=(
                    "matched"
                    if facility_id
                    else ("ambiguous" if confidence == "ambiguous" else "unmatched")
                ),
                allocation_share=allocation.allocation_share,
                source_name=allocation.source_mill,
                source_location=allocation.source_location,
                residue_stream=allocation.residue_stream,
                notes=(
                    "Dimensionless residue allocation share from the sawmill "
                    "allocation input; no residue volume is present in the "
                    "workbook, so no contract volume is derived."
                ),
            )
        )
    if not rows:
        raise BcFibreError("no diagnostic contract rows could be built")
    return tuple(rows)


def _coverage_summary(matches: Sequence[FacilityMatch]) -> dict[str, Any]:
    method_counts = Counter(match.match_method for match in matches)
    confidence_counts = Counter(match.match_confidence for match in matches)
    return {
        "facility_count": len(matches),
        "matched_count": sum(1 for match in matches if match.mills is not None),
        "unmatched_count": sum(1 for match in matches if match.mills is None),
        "method_counts": dict(sorted(method_counts.items())),
        "confidence_counts": dict(sorted(confidence_counts.items())),
    }


def write_bc_fibre_outputs(
    repo_root: Path,
    out_dir: Path,
    *,
    mills_db_path: Path | None = None,
    ffcm_registry_path: Path | None = None,
    sawmill_path: Path | None = None,
    tsa_path: Path | None = None,
    period_year: int = 2018,
) -> dict[str, Any]:
    """Parse, match, write the recovered facility table and diagnostic contracts."""
    repo_root = Path(repo_root)
    out_dir = Path(out_dir)
    mills_db_path = mills_db_path or repo_root / DEFAULT_MILLS_DB
    ffcm_registry_path = ffcm_registry_path or repo_root / DEFAULT_FFCM_REGISTRY
    sawmill_path = sawmill_path or repo_root / DEFAULT_SAWMILL_ALLOCATION
    tsa_path = tsa_path or repo_root / DEFAULT_TSA_APPORTIONMENT

    facilities_path = repo_root / "metadata/ffcm-archive/parsed/facilities.csv"
    lines_path = repo_root / "metadata/ffcm-archive/parsed/processing_lines.csv"
    with facilities_path.open(newline="", encoding="utf-8") as handle:
        facilities = [
            row
            for row in csv.DictReader(handle)
            if str(row["facility"]).startswith("BC-")
        ]
    if not facilities:
        raise BcFibreError(f"no BC facilities found in {facilities_path}")

    mills = read_mills_db(mills_db_path)
    sites = read_ffcm_sites(ffcm_registry_path)
    crosswalk = build_crosswalk(facilities, mills, sites)
    lines_by_facility, capacity_by_facility = _read_processing_lines(lines_path)
    stage_a_capacity: dict[str, float] = {}
    capacity_path = out_dir / "facility_capacity.csv"
    if capacity_path.exists():
        with capacity_path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                facility_id = str(row.get("facility_id", "")).strip()
                if not facility_id:
                    continue
                try:
                    value = float(str(row.get("nameplate_m3_y", "")).strip())
                except ValueError:
                    continue
                if math.isfinite(value) and value > 0.0:
                    stage_a_capacity[facility_id] = value

    recovered_rows = []
    for match in crosswalk.matches:
        site = match.site
        mills_row = match.mills
        if mills_row is None:
            recovered_rows.append(
                {
                    "facility_id": match.facility_id,
                    "facility_class": match.facility_class,
                    "name": site.site_name if site else match.facility_id,
                    "lat": "",
                    "lon": "",
                    "coord_source": "missing",
                    "match_method": match.match_method,
                    "match_confidence": match.match_confidence,
                    "match_evidence": match.evidence,
                    "mills_db_facility_code": "",
                    "mills_db_name": "",
                    "mills_db_operator": "",
                    "mills_db_capacity_m3_y": "",
                    "ffcm_site_name": site.site_name if site else "",
                    "ffcm_city": site.city if site else "",
                    "ffcm_class": site.facility_class if site else match.facility_class,
                    "distance_km": "",
                    "lines": ";".join(lines_by_facility.get(match.facility_id, [])),
                    "capacity_m3_y": stage_a_capacity.get(
                        match.facility_id,
                        capacity_by_facility.get(match.facility_id, ""),
                    ),
                    "notes": "no Mills DB match; Stage A keeps tl_anchor_proxy or missing",
                }
            )
            continue
        recovered_rows.append(
            {
                "facility_id": match.facility_id,
                "facility_class": match.facility_class,
                "name": site.site_name if site else match.facility_id,
                "lat": mills_row.lat,
                "lon": mills_row.lon,
                "coord_source": "mills_db_2015",
                "match_method": match.match_method,
                "match_confidence": match.match_confidence,
                "match_evidence": match.evidence,
                "mills_db_facility_code": mills_row.facility_code,
                "mills_db_name": mills_row.name,
                "mills_db_operator": mills_row.operator,
                "mills_db_capacity_m3_y": mills_row.total_capacity_m3_y,
                "ffcm_site_name": site.site_name if site else "",
                "ffcm_city": site.city if site else "",
                "ffcm_class": site.facility_class if site else match.facility_class,
                "distance_km": "" if match.distance_km is None else f"{match.distance_km:.3f}",
                "lines": ";".join(lines_by_facility.get(match.facility_id, [])),
                "capacity_m3_y": stage_a_capacity.get(
                        match.facility_id,
                        capacity_by_facility.get(match.facility_id, ""),
                    ),
                "notes": (
                    "Mills DB coordinate accepted only after name, city, or "
                    "coordinate corroboration; the 2015 and 2018 codes are not "
                    "a stable crosswalk."
                ),
            }
        )

    allocations = read_sawmill_allocation(sawmill_path)
    apportionments = read_tsa_tenure_apportionment(tsa_path)
    contract_rows = build_contract_rows(
        allocations, apportionments, facilities, sites, period_year=period_year
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(
        out_dir / "facilities_bc_recovered.csv",
        recovered_rows,
        [
            "facility_id",
            "facility_class",
            "name",
            "lat",
            "lon",
            "coord_source",
            "match_method",
            "match_confidence",
            "match_evidence",
            "mills_db_facility_code",
            "mills_db_name",
            "mills_db_operator",
            "mills_db_capacity_m3_y",
            "ffcm_site_name",
            "ffcm_city",
            "ffcm_class",
            "distance_km",
            "lines",
            "capacity_m3_y",
            "notes",
        ],
    )
    _write_csv(
        out_dir / "bc_facility_match_conflicts.csv",
        crosswalk.conflicts,
        [
            "facility_id",
            "mills_facility_code",
            "conflict_type",
            "ffcm_site_name",
            "ffcm_city",
            "mills_operator",
            "mills_city",
            "distance_km",
            "name_tokens",
            "notes",
        ],
    )
    _write_csv(
        out_dir / "sawmill_allocation_long.csv",
        [
            {
                "residue_stream": row.residue_stream,
                "source_owner": row.source_owner,
                "source_mill": row.source_mill,
                "source_location": row.source_location,
                "region": row.region,
                "destination_mill": row.destination_mill,
                "allocation_share": row.allocation_share,
                "cost_class": row.cost_class,
                "period_year": row.period_year,
                "source_sheet": row.source_sheet,
                "source_row": row.source_row,
            }
            for row in allocations
        ],
        [
            "residue_stream",
            "source_owner",
            "source_mill",
            "source_location",
            "region",
            "destination_mill",
            "allocation_share",
            "cost_class",
            "period_year",
            "source_sheet",
            "source_row",
        ],
    )
    _write_csv(
        out_dir / "tsa_tenure_apportionment_long.csv",
        [
            {
                "tsa": row.tsa,
                "tenure_class": row.tenure_class,
                "tenure_subclass": row.tenure_subclass,
                "licence_number": row.licence_number,
                "holder": row.holder,
                "expiry": row.expiry,
                "row_type": row.row_type,
                "period_year": row.period_year,
                "volume_m3": row.volume_m3,
                "source_sheet": row.source_sheet,
                "source_row": row.source_row,
            }
            for row in apportionments
        ],
        [
            "tsa",
            "tenure_class",
            "tenure_subclass",
            "licence_number",
            "holder",
            "expiry",
            "row_type",
            "period_year",
            "volume_m3",
            "source_sheet",
            "source_row",
        ],
    )
    _write_csv(
        out_dir / "contracts_bc_diagnostic.csv",
        [row.to_dict() for row in contract_rows],
        list(contract_rows[0].to_dict()),
    )

    tsa_rows = [row for row in contract_rows if row.allocated_volume is not None]
    share_rows = [row for row in contract_rows if row.allocation_share is not None]
    matched_destination_ids = sorted(
        {row.facility_id for row in share_rows if row.facility_id}
    )
    summary = {
        "evidence_tier": BC_FIBRE_EVIDENCE_TIER,
        "period_year": period_year,
        "facility_crosswalk": _coverage_summary(crosswalk.matches),
        "conflict_count": len(crosswalk.conflicts),
        "tsa_rows": len(tsa_rows),
        "tsa_sheets_parsed": len({row.tsa for row in apportionments}),
        "tsa_count": len({row.tsa_tfl for row in tsa_rows}),
        "tsa_total_allocated_volume_m3_y": sum(
            row.allocated_volume or 0.0 for row in tsa_rows
        ),
        "sawmill_allocation_rows": len(share_rows),
        "sawmill_residue_streams": sorted({row.residue_stream for row in share_rows}),
        "sawmill_share_sum": sum(row.allocation_share or 0.0 for row in share_rows),
        "matched_diagnostic_destination_count": len(matched_destination_ids),
        "matched_diagnostic_destination_ids": matched_destination_ids,
        "facility_level_contract_volume_count": sum(
            1
            for row in contract_rows
            if row.facility_id and row.allocated_volume is not None
        ),
        "notes": [
            "TSA volumes are real 2018 benchmark AAC apportionment values but have no facility identifier.",
            "Sawmill allocation values are dimensionless residue shares, not volumes.",
            "No facility-level contract volume is derived; Stage A keeps diagnostic_absent fallbacks.",
        ],
    }

    import json

    (out_dir / "bc_fibre_diagnostic_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[3],
    )
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--period-year", type=int, default=2018)
    args = parser.parse_args(argv)

    repo_root = args.repo_root.resolve()
    out_dir = args.out_dir
    if not out_dir.is_absolute():
        out_dir = (repo_root / out_dir).resolve()
    summary = write_bc_fibre_outputs(
        repo_root,
        out_dir,
        period_year=args.period_year,
    )
    print(
        "bc fibre: "
        f"facilities={summary['facility_crosswalk']['facility_count']} "
        f"matched={summary['facility_crosswalk']['matched_count']} "
        f"unmatched={summary['facility_crosswalk']['unmatched_count']} "
        f"conflicts={summary['conflict_count']} "
        f"tsa_rows={summary['tsa_rows']} "
        f"tsa_volume={summary['tsa_total_allocated_volume_m3_y']:,.0f} "
        f"shares={summary['sawmill_allocation_rows']} "
        f"matched_destinations={summary['matched_diagnostic_destination_count']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
