"""Match 2018 TSA tenure licence rows to BC facilities without guessing.

The TSA Tenure Apportionment workbook reports licence volumes by TSA and
holder, not by facility.  This module turns those rows into an auditable
facility crosswalk only when the holder-to-facility evidence is unique.  A
second table converts matched volumes into diagnostic maximum-entitlement
contracts for Stage A.
"""

from __future__ import annotations

import csv
import json
import math
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .bc_fibre import (
    DEFAULT_FFCM_REGISTRY,
    DEFAULT_TSA_APPORTIONMENT,
    BcFibreError,
    TsaApportionment,
    normalize_code,
    normalize_text,
    read_ffcm_sites,
    read_tsa_tenure_apportionment,
)

__all__ = [
    "APPORTIONMENT_EVIDENCE_TIER",
    "CONTRACT_EVIDENCE_TIER",
    "ApportionmentParse",
    "FacilityRecord",
    "MatchDecision",
    "build_tenure_crosswalk",
    "canonical_tsa",
    "entity_alias_family",
    "match_licence",
    "normalize_entity_name",
    "parse_apportionment_rows",
    "read_facility_tsa_cache",
    "write_tenure_crosswalk_outputs",
]

APPORTIONMENT_EVIDENCE_TIER = "parsed_apportionment_2018"
CONTRACT_EVIDENCE_TIER = "apportionment_derived_diagnostic"
APPORTIONMENT_SOURCE_LABEL = "TSA Tenure Apportionment V5.xls"
DEFAULT_TSA_BOUNDARIES = Path("tmp/tsa_boundaries.geojson")
DEFAULT_FACILITY_TSA_CACHE = "bc_facility_tsa.csv"

# Legal-form tokens only.  Industry words such as "forest", "products",
# "timber", and "pulp" stay in the normalized name because they distinguish
# corporate families and legal entities.
_LEGAL_SUFFIX_TOKENS = frozenset(
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
        "gp",
        "partnership",
        "partners",
        "the",
    }
)

# Explicit corporate-family aliases.  Each alias is a token sequence; single
# distinctive tokens are matched as whole tokens, never as substrings.
_ALIAS_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "canfor",
        ("canfor", "canfor pulp", "canfor pulp lp", "canfor pulp and paper"),
    ),
    (
        "west_fraser",
        ("west fraser", "west fraser timber", "west fraser mills"),
    ),
    ("tolko", ("tolko", "tolko industries")),
    ("interfor", ("interfor", "interfor corporation", "interfor prod")),
    ("conifex", ("conifex", "conifex timber", "conifex power")),
    (
        "western_forest_products",
        ("western forest products", "western forest", "wfp"),
    ),
    (
        "teal_jones",
        ("teal jones", "teal jones group", "teal cedar", "teal cedar products"),
    ),
    ("domtar", ("domtar",)),
    ("paper_excellence", ("paper excellence", "paper excellence canada")),
    (
        "catalyst",
        ("catalyst", "catalyst paper", "catalyst paper general partnership"),
    ),
    ("carrier_lumber", ("carrier lumber",)),
    ("hampton", ("hampton", "hampton affiliates", "hampton lumber")),
    ("sinclar", ("sinclar", "sinclar group")),
    ("dunkley", ("dunkley", "dunkley lumber", "1040806 dunkley")),
    (
        "gilbert_smith",
        ("gilbert smith", "gilbert smith forest", "gilbert smith forest products"),
    ),
    ("downie", ("downie", "downie timber")),
    ("galloway", ("galloway", "galloway lumber")),
    ("skeena", ("skeena", "skeena sawmills")),
    ("richmond_plywood", ("richmond plywood",)),
    ("pinnacle", ("pinnacle", "pinnacle renewable energy")),
    ("pacific_bioenergy", ("pacific bioenergy",)),
    ("mercer", ("mercer",)),
    ("cariboo_pulp", ("cariboo pulp", "cariboo pulp and paper")),
    ("west_chilcotin", ("west chilcotin", "west chilcotin fp")),
    ("mill_timber", ("mill and timber", "mill timber products")),
    (
        "louisiana_pacific",
        ("louisiana pacific", "louisiana pacific canada"),
    ),
    ("norbord", ("norbord", "norbord industries")),
    ("weyerhaeuser", ("weyerhaeuser",)),
    ("atco", ("atco", "atco lumber")),
)

# Workbook sheet names are abbreviated.  FADM names are canonicalized to the
# workbook vocabulary so a licence row and a facility polygon compare directly.
_TSA_ALIASES = {
    "pg": "prince george",
    "prince george": "prince george",
    "robson": "robson valley",
    "robson valley": "robson valley",
    "ftst john": "fort st john",
    "ft st john": "fort st john",
    "fort st john": "fort st john",
    "mackenzie": "mackenzie",
    "haida gwaii": "queen charlotte",
    "queen charlotte": "queen charlotte",
    "great bear north": "gbr north",
    "gbr north": "gbr north",
    "great bear south": "gbr south",
    "gbr south": "gbr south",
    "100 mile": "100 mile house",
    "100 mile house": "100 mile house",
    "north coast": "north coast",
    "mid coast": "mid coast",
}

_STAGE_A_CLASSES = frozenset({"SAW", "PAP", "PEL", "BIO"})


@dataclass(frozen=True, slots=True)
class FacilityRecord:
    facility_id: str
    facility_name: str
    facility_class: str
    company: str
    normalized_company: str
    family: str
    tsa_label: str
    tsa_key: str
    tsa_source: str
    stage_a_scope: bool
    nameplate_m3_y: float | None
    source_row: int


@dataclass(frozen=True, slots=True)
class MatchDecision:
    allocation: TsaApportionment
    facility: FacilityRecord | None
    status: str
    method: str
    confidence: str
    candidates: tuple[FacilityRecord, ...]
    same_tsa_candidate_count: int
    evidence: str


@dataclass(frozen=True, slots=True)
class ApportionmentParse:
    period_year: int
    workbook_sheet_count: int
    worksheet_count: int
    row_count: int
    period_row_count: int
    positive_rows: tuple[TsaApportionment, ...]
    nonpositive_rows: tuple[TsaApportionment, ...]

    @property
    def positive_by_type(self) -> dict[str, int]:
        return dict(Counter(row.row_type for row in self.positive_rows))

    @property
    def nonpositive_by_type(self) -> dict[str, int]:
        return dict(Counter(row.row_type for row in self.nonpositive_rows))

    @property
    def positive_volume_by_type(self) -> dict[str, float]:
        totals: dict[str, float] = defaultdict(float)
        for row in self.positive_rows:
            totals[row.row_type] += row.volume_m3
        return dict(totals)

    @property
    def positive_apportionment_rows(self) -> tuple[TsaApportionment, ...]:
        return tuple(
            row for row in self.positive_rows if row.row_type == "apportionment_class"
        )

    @property
    def positive_licence_rows(self) -> tuple[TsaApportionment, ...]:
        return tuple(row for row in self.positive_rows if row.row_type == "licence")


def normalize_entity_name(value: object) -> str:
    """Lowercase, strip punctuation and legal-form suffixes, keep industry words."""
    tokens = [
        token
        for token in normalize_text(value).split()
        if token not in _LEGAL_SUFFIX_TOKENS
    ]
    return " ".join(tokens)


def _contains_alias_token_sequence(
    name_tokens: Sequence[str], alias_tokens: Sequence[str]
) -> bool:
    if not alias_tokens or not name_tokens:
        return False
    if len(alias_tokens) == 1:
        return alias_tokens[0] in name_tokens
    width = len(alias_tokens)
    return any(
        tuple(name_tokens[start : start + width]) == tuple(alias_tokens)
        for start in range(len(name_tokens) - width + 1)
    )


def entity_alias_family(value: object) -> str:
    """Return the explicit corporate family for a holder or facility owner."""
    tokens = normalize_entity_name(value).split()
    for family, aliases in _ALIAS_RULES:
        for alias in aliases:
            if _contains_alias_token_sequence(tokens, alias.split()):
                return family
    return ""


def canonical_tsa(value: object) -> str:
    """Normalize workbook sheet names and FADM labels to one TSA vocabulary."""
    text = normalize_text(value)
    for suffix in (" timber supply area", " tsa"):
        if text.endswith(suffix):
            text = text[: -len(suffix)].strip()
    return _TSA_ALIASES.get(text, text)


def _number(value: object) -> float | None:
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _truthy(value: object) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def _write_csv(
    path: Path, rows: Sequence[Mapping[str, Any]], columns: Sequence[str]
) -> None:
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


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _apportionment_from_long_csv(path: Path, period_year: int) -> tuple[TsaApportionment, ...]:
    rows: list[TsaApportionment] = []
    for row in _read_csv(path):
        year = _number(row.get("period_year"))
        volume = _number(row.get("volume_m3"))
        source_row = _number(row.get("source_row"))
        if year is None or volume is None or source_row is None:
            continue
        if int(year) != period_year:
            continue
        rows.append(
            TsaApportionment(
                tsa=str(row.get("tsa", "")).strip(),
                tenure_class=str(row.get("tenure_class", "")).strip(),
                tenure_subclass=str(row.get("tenure_subclass", "")).strip(),
                licence_number=str(row.get("licence_number", "")).strip(),
                holder=str(row.get("holder", "")).strip(),
                expiry=str(row.get("expiry", "")).strip(),
                row_type=str(row.get("row_type", "")).strip(),
                period_year=period_year,
                volume_m3=volume,
                source_sheet=str(row.get("source_sheet", "")).strip(),
                source_row=int(source_row),
            )
        )
    return tuple(rows)


def _workbook_sheet_count(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        import pandas as pd
    except ImportError:  # pragma: no cover - environment guard
        return 0
    return len(pd.ExcelFile(path).sheet_names)


def parse_apportionment_rows(
    workbook_path: Path,
    *,
    period_year: int = 2018,
    long_table_path: Path | None = None,
) -> ApportionmentParse:
    """Parse the workbook and split the requested year into positive and rejected rows."""
    if workbook_path.exists():
        rows = read_tsa_tenure_apportionment(workbook_path)
    elif long_table_path is not None and long_table_path.exists():
        rows = _apportionment_from_long_csv(long_table_path, period_year)
    else:
        raise BcFibreError(
            "TSA Tenure Apportionment workbook and long table are both missing: "
            f"{workbook_path}; {long_table_path}"
        )
    period_rows = tuple(row for row in rows if row.period_year == period_year)
    if not period_rows:
        raise BcFibreError(
            f"TSA Tenure Apportionment workbook has no rows for {period_year}"
        )
    return ApportionmentParse(
        period_year=period_year,
        workbook_sheet_count=_workbook_sheet_count(workbook_path),
        worksheet_count=len({row.source_sheet for row in rows}),
        row_count=len(rows),
        period_row_count=len(period_rows),
        positive_rows=tuple(row for row in period_rows if row.volume_m3 > 0.0),
        nonpositive_rows=tuple(row for row in period_rows if row.volume_m3 <= 0.0),
    )


def read_facility_tsa_cache(path: Path) -> dict[str, dict[str, str]]:
    """Read the derived facility-to-TSA spatial join table."""
    if not path.exists():
        return {}
    return {
        str(row["facility_id"]).strip(): row
        for row in _read_csv(path)
        if str(row.get("facility_id", "")).strip()
    }


def _facility_coordinates(
    recovered_rows: Sequence[Mapping[str, str]],
    stage_a_rows: Sequence[Mapping[str, str]],
) -> list[dict[str, str]]:
    stage_a_by_id = {
        str(row["facility_id"]).strip(): row for row in stage_a_rows
    }
    coordinates: list[dict[str, str]] = []
    for recovered in recovered_rows:
        facility_id = str(recovered["facility_id"]).strip()
        stage_a = stage_a_by_id.get(facility_id, {})
        lat = _number(stage_a.get("lat", recovered.get("lat", "")))
        lon = _number(stage_a.get("lon", recovered.get("lon", "")))
        coord_source = str(
            stage_a.get("coord_source", recovered.get("coord_source", "missing"))
        ).strip()
        coordinates.append(
            {
                "facility_id": facility_id,
                "facility_class": str(recovered["facility_class"]).strip().upper(),
                "lat": "" if lat is None else str(lat),
                "lon": "" if lon is None else str(lon),
                "coord_source": coord_source,
            }
        )
    return coordinates


def _build_facility_tsa_rows(
    coordinates: Sequence[Mapping[str, str]],
    boundaries_path: Path,
) -> list[dict[str, Any]]:
    try:
        import geopandas as gpd
    except ImportError as error:  # pragma: no cover - environment guard
        raise BcFibreError(
            "geopandas is required to join facilities to FADM TSA polygons"
        ) from error
    with_coordinates = [
        row for row in coordinates if row["lat"] and row["lon"]
    ]
    if not with_coordinates:
        raise BcFibreError("no facility coordinates are available for TSA joining")
    facility_ids = [str(row["facility_id"]) for row in with_coordinates]
    if len(facility_ids) != len(set(facility_ids)):
        raise BcFibreError("facility coordinate table has duplicate facility_id rows")

    boundaries = gpd.read_file(boundaries_path)
    required = {"TSA_NUMBER", "TSA_NUMBER_DESCRIPTION", "geometry"}
    missing = required - set(boundaries.columns)
    if missing:
        raise BcFibreError(
            f"{boundaries_path} is missing TSA columns: {sorted(missing)}"
        )
    boundaries = (
        boundaries[["TSA_NUMBER", "TSA_NUMBER_DESCRIPTION", "geometry"]]
        .dissolve(by=["TSA_NUMBER", "TSA_NUMBER_DESCRIPTION"])
        .reset_index()
    )
    points = gpd.GeoDataFrame(
        with_coordinates,
        geometry=gpd.points_from_xy(
            [float(row["lon"]) for row in with_coordinates],
            [float(row["lat"]) for row in with_coordinates],
        ),
        crs="EPSG:4326",
    )
    joined = gpd.sjoin(points, boundaries, how="left", predicate="within")
    duplicated = sorted(
        {
            str(row["facility_id"])
            for _index, row in joined[joined.index.duplicated(keep=False)].iterrows()
        }
    )
    if duplicated:
        raise BcFibreError(
            "facility points fall inside more than one TSA polygon: "
            + ", ".join(duplicated)
        )

    joined_by_id = {
        str(row["facility_id"]): row for _index, row in joined.iterrows()
    }
    rows: list[dict[str, Any]] = []
    for coordinate in coordinates:
        facility_id = coordinate["facility_id"]
        joined_row = joined_by_id.get(facility_id)
        tsa_name = ""
        tsa_number = ""
        tsa_key = ""
        tsa_source = "missing_coordinate"
        if coordinate["lat"] and coordinate["lon"]:
            if joined_row is None:
                raise BcFibreError(f"TSA join dropped facility {facility_id}")
            tsa_name = str(joined_row.get("TSA_NUMBER_DESCRIPTION") or "").strip()
            tsa_number = str(joined_row.get("TSA_NUMBER") or "").strip()
            if tsa_name:
                tsa_key = canonical_tsa(tsa_name)
                tsa_source = (
                    f"{coordinate['coord_source']}_spatial_join"
                    if coordinate["coord_source"] != "missing"
                    else "spatial_join"
                )
            else:
                tsa_source = f"{coordinate['coord_source']}_no_tsa_polygon"
        rows.append(
            {
                "facility_id": facility_id,
                "facility_class": coordinate["facility_class"],
                "lat": coordinate["lat"],
                "lon": coordinate["lon"],
                "coord_source": coordinate["coord_source"],
                "tsa_number": tsa_number,
                "tsa_name": tsa_name,
                "tsa_key": tsa_key,
                "tsa_source": tsa_source,
            }
        )
    return rows


def ensure_facility_tsa_table(
    coordinates: Sequence[Mapping[str, str]],
    *,
    boundaries_path: Path,
    cache_path: Path,
) -> list[dict[str, Any]]:
    """Build and cache the spatial join, or reuse the committed cache."""
    if boundaries_path.exists():
        rows = _build_facility_tsa_rows(coordinates, boundaries_path)
        _write_csv(
            cache_path,
            rows,
            [
                "facility_id",
                "facility_class",
                "lat",
                "lon",
                "coord_source",
                "tsa_number",
                "tsa_name",
                "tsa_key",
                "tsa_source",
            ],
        )
        return rows
    cache = read_facility_tsa_cache(cache_path)
    if not cache:
        raise BcFibreError(
            "FADM TSA boundaries are unavailable and no facility TSA cache exists: "
            f"{boundaries_path}; {cache_path}"
        )
    return [cache[row["facility_id"]] for row in coordinates if row["facility_id"] in cache]


def _facility_company(
    recovered_row: Mapping[str, str],
    owner: str,
) -> str:
    company = str(owner).strip()
    if company and company.lower() != "nan":
        return company
    name = str(recovered_row.get("name", "")).strip()
    if not name or name.startswith("BC-"):
        return ""
    return name.split("-", 1)[0].strip()


def read_facility_records(
    recovered_path: Path,
    *,
    stage_a_path: Path | None,
    ffcm_registry_path: Path,
    tsa_cache: Mapping[str, Mapping[str, str]],
) -> tuple[FacilityRecord, ...]:
    """Build typed facility records with company, family, and TSA evidence."""
    recovered_rows = _read_csv(recovered_path)
    if not recovered_rows:
        raise BcFibreError(f"recovered facility table is empty: {recovered_path}")
    stage_a_by_id = {}
    if stage_a_path is not None and stage_a_path.exists():
        stage_a_by_id = {
            str(row["facility_id"]).strip(): row for row in _read_csv(stage_a_path)
        }
    sites = read_ffcm_sites(ffcm_registry_path)
    site_by_code = {site.normalized_code: site for site in sites}

    records: list[FacilityRecord] = []
    for source_row, row in enumerate(recovered_rows, start=2):
        facility_id = str(row["facility_id"]).strip()
        facility_class = str(row["facility_class"]).strip().upper()
        site = site_by_code.get(normalize_code(facility_id))
        company = _facility_company(row, site.site_owner if site else "")
        normalized_company = normalize_entity_name(company) if company else ""
        family = entity_alias_family(company) if company else ""
        stage_a = stage_a_by_id.get(facility_id, {})
        if stage_a:
            stage_a_scope = _truthy(stage_a.get("stage_a_scope", ""))
            nameplate = _number(stage_a.get("nameplate_m3_y", ""))
        else:
            nameplate = _number(row.get("capacity_m3_y", ""))
            stage_a_scope = (
                facility_class in _STAGE_A_CLASSES
                and nameplate is not None
                and nameplate > 0.0
            )
        tsa = tsa_cache.get(facility_id, {})
        tsa_source = str(tsa.get("tsa_source", "missing")).strip()
        tsa_key = str(tsa.get("tsa_key", "")).strip()
        # A TL-hub anchor is a proxy coordinate.  Keep the resulting TSA label
        # for audit, but never use it to disambiguate a holder match.
        if tsa_source.startswith("tl_anchor_proxy"):
            tsa_key = ""
        records.append(
            FacilityRecord(
                facility_id=facility_id,
                facility_name=str(row.get("name", "")).strip() or facility_id,
                facility_class=facility_class,
                company=company,
                normalized_company=normalized_company,
                family=family,
                tsa_label=str(tsa.get("tsa_name", "")).strip(),
                tsa_key=tsa_key,
                tsa_source=tsa_source,
                stage_a_scope=stage_a_scope,
                nameplate_m3_y=nameplate,
                source_row=source_row,
            )
        )
    return tuple(records)


def _matched(
    allocation: TsaApportionment,
    facility: FacilityRecord,
    method: str,
    confidence: str,
    evidence: str,
) -> MatchDecision:
    return MatchDecision(
        allocation=allocation,
        facility=facility,
        status="matched",
        method=method,
        confidence=confidence,
        candidates=(facility,),
        same_tsa_candidate_count=1,
        evidence=evidence,
    )


def _unresolved(
    allocation: TsaApportionment,
    status: str,
    method: str,
    evidence: str,
    candidates: Sequence[FacilityRecord] = (),
) -> MatchDecision:
    ordered = tuple(sorted(candidates, key=lambda item: item.facility_id))
    return MatchDecision(
        allocation=allocation,
        facility=None,
        status=status,
        method=method,
        confidence="ambiguous" if status == "ambiguous" else "none",
        candidates=ordered,
        same_tsa_candidate_count=len(_same_tsa(allocation, ordered)),
        evidence=evidence,
    )


def _same_tsa(
    allocation: TsaApportionment,
    candidates: Sequence[FacilityRecord],
) -> list[FacilityRecord]:
    tsa_key = canonical_tsa(allocation.tsa)
    return [
        candidate
        for candidate in candidates
        if candidate.tsa_key and candidate.tsa_key == tsa_key
    ]


def _tsa_evidence(allocation: TsaApportionment, facility: FacilityRecord) -> str:
    tsa_key = canonical_tsa(allocation.tsa)
    if not facility.tsa_key:
        label = facility.tsa_label or "unknown"
        return (
            f"facility_tsa={label} tsa_key_unused;"
            f"tsa_source={facility.tsa_source}"
        )
    if facility.tsa_key == tsa_key:
        return f"facility_tsa={facility.tsa_label} same_tsa=true"
    return (
        f"facility_tsa={facility.tsa_label} same_tsa=false "
        f"licence_tsa={allocation.tsa}"
    )


def _match_exact(
    allocation: TsaApportionment,
    exact: Sequence[FacilityRecord],
) -> MatchDecision:
    same_tsa = _same_tsa(allocation, exact)
    if len(exact) == 1:
        facility = exact[0]
        confidence = "high" if facility.tsa_key == canonical_tsa(allocation.tsa) else "medium"
        return _matched(
            allocation,
            facility,
            "exact_holder_unique",
            confidence,
            (
                "exact_holder=true;"
                f"holder={allocation.holder!r};"
                f"facility_company={facility.company!r};"
                f"{_tsa_evidence(allocation, facility)}"
            ),
        )
    if len(same_tsa) == 1:
        facility = same_tsa[0]
        return _matched(
            allocation,
            facility,
            "exact_holder_same_tsa",
            "high",
            (
                "exact_holder=true;"
                f"holder={allocation.holder!r};"
                f"candidate_count={len(exact)};"
                f"same_tsa_candidate_count=1;"
                f"{_tsa_evidence(allocation, facility)}"
            ),
        )
    if len(same_tsa) > 1:
        return _unresolved(
            allocation,
            "ambiguous",
            "exact_holder_same_tsa_ambiguous",
            (
                "exact_holder=true;"
                f"holder={allocation.holder!r};"
                f"same_tsa_candidate_count={len(same_tsa)};"
                "multiple facilities in the licence TSA"
            ),
            same_tsa,
        )
    return _unresolved(
        allocation,
        "ambiguous",
        "exact_holder_multiple_no_tsa_match",
        (
            "exact_holder=true;"
            f"holder={allocation.holder!r};"
            f"candidate_count={len(exact)};"
            "no exact facility in the licence TSA"
        ),
        exact,
    )


def _match_alias(
    allocation: TsaApportionment,
    alias_candidates: Sequence[FacilityRecord],
    family: str,
) -> MatchDecision:
    same_tsa = _same_tsa(allocation, alias_candidates)
    if len(same_tsa) == 1:
        facility = same_tsa[0]
        return _matched(
            allocation,
            facility,
            "alias_holder_same_tsa",
            "medium",
            (
                f"alias_family={family};"
                f"holder={allocation.holder!r};"
                f"facility_company={facility.company!r};"
                f"same_tsa_candidate_count=1;"
                f"{_tsa_evidence(allocation, facility)}"
            ),
        )
    if len(same_tsa) > 1:
        return _unresolved(
            allocation,
            "ambiguous",
            "alias_holder_same_tsa_ambiguous",
            (
                f"alias_family={family};"
                f"holder={allocation.holder!r};"
                f"same_tsa_candidate_count={len(same_tsa)};"
                "multiple alias-family facilities in the licence TSA"
            ),
            same_tsa,
        )
    if len(alias_candidates) == 1:
        facility = alias_candidates[0]
        return _matched(
            allocation,
            facility,
            "alias_holder_unique",
            "low",
            (
                f"alias_family={family};"
                f"holder={allocation.holder!r};"
                f"facility_company={facility.company!r};"
                "only one alias-family facility exists province-wide;"
                f"{_tsa_evidence(allocation, facility)}"
            ),
        )
    return _unresolved(
        allocation,
        "ambiguous",
        "alias_holder_multiple_no_tsa_match",
        (
            f"alias_family={family};"
            f"holder={allocation.holder!r};"
            f"candidate_count={len(alias_candidates)};"
            "no alias-family facility in the licence TSA"
        ),
        alias_candidates,
    )


def match_licence(
    allocation: TsaApportionment,
    facilities: Sequence[FacilityRecord],
) -> MatchDecision:
    """Match one licence row under the exact -> alias -> unique -> ambiguous order."""
    if allocation.volume_m3 <= 0.0:
        return _unresolved(
            allocation,
            "unmatched",
            "nonpositive_volume",
            "licence row has no positive 2018 volume",
        )
    normalized_holder = normalize_entity_name(allocation.holder)
    if not normalized_holder:
        return _unresolved(
            allocation,
            "unmatched",
            "missing_holder",
            "licence row has no holder name",
        )
    exact = tuple(
        facility
        for facility in facilities
        if facility.normalized_company
        and facility.normalized_company == normalized_holder
    )
    if exact:
        return _match_exact(allocation, exact)

    family = entity_alias_family(allocation.holder)
    if not family:
        return _unresolved(
            allocation,
            "unmatched",
            "no_exact_or_alias_company",
            (
                f"normalized_holder={normalized_holder!r};"
                "no exact company and no explicit alias family"
            ),
        )
    alias_candidates = tuple(
        facility for facility in facilities if facility.family == family
    )
    if not alias_candidates:
        return _unresolved(
            allocation,
            "unmatched",
            "alias_family_without_facility",
            f"alias_family={family}; no facility carries this family",
        )
    return _match_alias(allocation, alias_candidates, family)


def _sorted_licence_rows(
    rows: Sequence[TsaApportionment],
) -> tuple[TsaApportionment, ...]:
    return tuple(
        sorted(
            rows,
            key=lambda row: (
                canonical_tsa(row.tsa),
                normalize_text(row.holder),
                row.licence_number,
                row.source_sheet,
                row.source_row,
            ),
        )
    )


def _source(row: TsaApportionment) -> str:
    return f"{APPORTIONMENT_SOURCE_LABEL}:{row.source_sheet}:{row.source_row}"


def _crosswalk_rows(decisions: Sequence[MatchDecision]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for decision in decisions:
        facility = decision.facility
        if facility is None:
            continue
        allocation = decision.allocation
        tsa_match = (
            "unknown"
            if not facility.tsa_key
            else (
                "true"
                if facility.tsa_key == canonical_tsa(allocation.tsa)
                else "false"
            )
        )
        notes = ""
        if tsa_match == "false":
            notes = (
                "Licence TSA differs from the facility TSA; matched only because "
                "the holder-to-facility evidence was unique."
            )
        elif tsa_match == "unknown":
            notes = (
                "Facility TSA is missing or comes from a TL-hub proxy coordinate; "
                "the match rests on holder evidence only."
            )
        rows.append(
            {
                "facility_id": facility.facility_id,
                "facility_name": facility.facility_name,
                "facility_class": facility.facility_class,
                "tsa": allocation.tsa,
                "tenure_class": allocation.tenure_class,
                "licence_number": allocation.licence_number,
                "holder": allocation.holder,
                "allocated_m3_y": allocation.volume_m3,
                "match_method": decision.method,
                "match_confidence": decision.confidence,
                "source": _source(allocation),
                "evidence_tier": APPORTIONMENT_EVIDENCE_TIER,
                "facility_tsa": facility.tsa_label,
                "tsa_match": tsa_match,
                "evidence": decision.evidence,
                "notes": notes,
            }
        )
    return rows


def _candidate_rows(decisions: Sequence[MatchDecision]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for decision in decisions:
        allocation = decision.allocation
        facility = decision.facility
        rows.append(
            {
                "facility_id": facility.facility_id if facility else "",
                "facility_name": facility.facility_name if facility else "",
                "facility_class": facility.facility_class if facility else "",
                "tsa": allocation.tsa,
                "tenure_class": allocation.tenure_class,
                "licence_number": allocation.licence_number,
                "holder": allocation.holder,
                "allocated_m3_y": allocation.volume_m3,
                "status": decision.status,
                "match_method": decision.method,
                "match_confidence": decision.confidence,
                "candidate_count": len(decision.candidates),
                "same_tsa_candidate_count": decision.same_tsa_candidate_count,
                "candidate_facility_ids": ";".join(
                    candidate.facility_id for candidate in decision.candidates
                ),
                "candidate_facility_names": ";".join(
                    candidate.facility_name for candidate in decision.candidates
                ),
                "candidate_tsas": ";".join(
                    candidate.tsa_label for candidate in decision.candidates
                ),
                "evidence": decision.evidence,
                "source": _source(allocation),
                "evidence_tier": APPORTIONMENT_EVIDENCE_TIER,
            }
        )
    return rows


def _contract_rows(decisions: Sequence[MatchDecision]) -> list[dict[str, Any]]:
    grouped: dict[str, list[MatchDecision]] = defaultdict(list)
    for decision in decisions:
        if decision.facility is not None:
            grouped[decision.facility.facility_id].append(decision)

    rows: list[dict[str, Any]] = []
    for facility_id in sorted(grouped):
        decisions_for_facility = grouped[facility_id]
        facility = decisions_for_facility[0].facility
        if facility is None:
            continue
        allocated = sum(
            decision.allocation.volume_m3 for decision in decisions_for_facility
        )
        source_rows = sorted(
            {
                f"{decision.allocation.source_sheet}:{decision.allocation.source_row}"
                for decision in decisions_for_facility
            }
        )
        licence_numbers = sorted(
            {
                decision.allocation.licence_number
                for decision in decisions_for_facility
                if decision.allocation.licence_number
            }
        )
        holders = sorted(
            {decision.allocation.holder for decision in decisions_for_facility}
        )
        tsas = sorted({decision.allocation.tsa for decision in decisions_for_facility})
        exceeds_nameplate = (
            facility.nameplate_m3_y is not None
            and allocated > facility.nameplate_m3_y
        )
        rows.append(
            {
                "facility_id": facility.facility_id,
                "grade": "all_logs",
                "min_volume": 0.0,
                "max_volume": allocated,
                "gate_cost": 0.0,
                "source": f"{APPORTIONMENT_SOURCE_LABEL}:" + ";".join(source_rows),
                "evidence_tier": CONTRACT_EVIDENCE_TIER,
                "contract_source": CONTRACT_EVIDENCE_TIER,
                "facility_name": facility.facility_name,
                "facility_class": facility.facility_class,
                "tsa": ";".join(tsas),
                "holder": ";".join(holders),
                "licence_numbers": ";".join(licence_numbers),
                "licence_count": len(decisions_for_facility),
                "nameplate_m3_y": (
                    "" if facility.nameplate_m3_y is None else facility.nameplate_m3_y
                ),
                "exceeds_nameplate": exceeds_nameplate,
                "stage_a_scope": facility.stage_a_scope,
                "notes": (
                    "Apportionment-derived maximum entitlement, not a supply "
                    "agreement. min=0 and gate=0 are diagnostic placeholders; "
                    "the facility nameplate still caps Stage A."
                ),
            }
        )
    return rows


def _parse_summary(parsed: ApportionmentParse) -> dict[str, Any]:
    apportionment_rows = parsed.positive_apportionment_rows
    licence_rows = parsed.positive_licence_rows
    return {
        "period_year": parsed.period_year,
        "workbook_sheet_count": parsed.workbook_sheet_count,
        "parsed_tsa_worksheet_count": parsed.worksheet_count,
        "row_count_all_years": parsed.row_count,
        "period_row_count": parsed.period_row_count,
        "positive_rows": len(parsed.positive_rows),
        "positive_by_row_type": parsed.positive_by_type,
        "positive_volume_m3_y_by_row_type": parsed.positive_volume_by_type,
        "positive_apportionment_rows": len(apportionment_rows),
        "positive_apportionment_tsas": len({row.tsa for row in apportionment_rows}),
        "positive_apportionment_volume_m3_y": sum(
            row.volume_m3 for row in apportionment_rows
        ),
        "positive_licence_rows": len(licence_rows),
        "positive_licence_tsas": len({row.tsa for row in licence_rows}),
        "positive_licence_holders": len({row.holder for row in licence_rows}),
        "positive_licence_volume_m3_y": sum(row.volume_m3 for row in licence_rows),
        "nonpositive_rows": len(parsed.nonpositive_rows),
        "nonpositive_by_row_type": parsed.nonpositive_by_type,
        "nonpositive_volume_m3_y": sum(
            row.volume_m3 for row in parsed.nonpositive_rows
        ),
    }


def _crosswalk_summary(
    parsed: ApportionmentParse,
    decisions: Sequence[MatchDecision],
    contracts: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    matched = [decision for decision in decisions if decision.status == "matched"]
    ambiguous = [decision for decision in decisions if decision.status == "ambiguous"]
    unmatched = [decision for decision in decisions if decision.status == "unmatched"]
    matched_volume = sum(
        decision.allocation.volume_m3 for decision in matched
    )
    apportionment_volume = sum(
        row.volume_m3 for row in parsed.positive_apportionment_rows
    )
    licence_volume = sum(
        row.volume_m3 for row in parsed.positive_licence_rows
    )
    matched_facilities = {decision.facility.facility_id for decision in matched if decision.facility}
    stage_a_contracts = [row for row in contracts if row["stage_a_scope"]]
    matched_holders = {decision.allocation.holder for decision in matched}
    holders_without_any_match = sorted(
        {decision.allocation.holder for decision in decisions} - matched_holders
    )
    matched_tsas = {decision.allocation.tsa for decision in matched}
    tsas_without_any_match = sorted(
        {decision.allocation.tsa for decision in decisions} - matched_tsas
    )
    return {
        "licence_rows": len(decisions),
        "licence_volume_m3_y": licence_volume,
        "matched_rows": len(matched),
        "matched_volume_m3_y": matched_volume,
        "matched_facility_count": len(matched_facilities),
        "matched_by_method": dict(Counter(decision.method for decision in matched)),
        "matched_by_confidence": dict(
            Counter(decision.confidence for decision in matched)
        ),
        "ambiguous_rows": len(ambiguous),
        "ambiguous_volume_m3_y": sum(
            decision.allocation.volume_m3 for decision in ambiguous
        ),
        "ambiguous_by_method": dict(
            Counter(decision.method for decision in ambiguous)
        ),
        "unmatched_rows": len(unmatched),
        "unmatched_volume_m3_y": sum(
            decision.allocation.volume_m3 for decision in unmatched
        ),
        "unmatched_by_method": dict(
            Counter(decision.method for decision in unmatched)
        ),
        "holders_with_unmatched_rows": sorted(
            {
                decision.allocation.holder
                for decision in decisions
                if decision.status != "matched"
            }
        ),
        "holders_without_any_match": holders_without_any_match,
        "tsas_with_unmatched_rows": sorted(
            {
                decision.allocation.tsa
                for decision in decisions
                if decision.status != "matched"
            }
        ),
        "tsas_without_any_match": tsas_without_any_match,
        "matched_fraction_of_positive_apportionment": (
            matched_volume / apportionment_volume if apportionment_volume else 0.0
        ),
        "matched_fraction_of_positive_licence": (
            matched_volume / licence_volume if licence_volume else 0.0
        ),
        "contract_rows": len(contracts),
        "stage_a_contract_rows": len(stage_a_contracts),
        "out_of_scope_contract_rows": len(contracts) - len(stage_a_contracts),
        "contract_total_max_volume_m3_y": sum(
            float(row["max_volume"]) for row in contracts
        ),
        "contracts_exceeding_nameplate": sum(
            1 for row in contracts if row["exceeds_nameplate"]
        ),
    }


def write_tenure_crosswalk_outputs(
    out_dir: Path,
    parsed: ApportionmentParse,
    decisions: Sequence[MatchDecision],
    contracts: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Write the parsed, candidate, crosswalk, and contract tables."""
    out_dir.mkdir(parents=True, exist_ok=True)
    positive_rows = [
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
            "unit": "m3/y",
            "source_sheet": row.source_sheet,
            "source_row": row.source_row,
        }
        for row in parsed.positive_rows
    ]
    rejected_rows = [
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
            "unit": "m3/y",
            "source_sheet": row.source_sheet,
            "source_row": row.source_row,
        }
        for row in parsed.nonpositive_rows
    ]
    apportionment_columns = [
        "tsa",
        "tenure_class",
        "tenure_subclass",
        "licence_number",
        "holder",
        "expiry",
        "row_type",
        "period_year",
        "volume_m3",
        "unit",
        "source_sheet",
        "source_row",
    ]
    _write_csv(out_dir / "tsa_apportionment_2018.csv", positive_rows, apportionment_columns)
    _write_csv(
        out_dir / "tsa_apportionment_2018_rejected.csv",
        rejected_rows,
        apportionment_columns,
    )
    _write_csv(
        out_dir / "facility_tenure_crosswalk.csv",
        _crosswalk_rows(decisions),
        [
            "facility_id",
            "facility_name",
            "facility_class",
            "tsa",
            "tenure_class",
            "licence_number",
            "holder",
            "allocated_m3_y",
            "match_method",
            "match_confidence",
            "source",
            "evidence_tier",
            "facility_tsa",
            "tsa_match",
            "evidence",
            "notes",
        ],
    )
    _write_csv(
        out_dir / "facility_tenure_crosswalk_candidates.csv",
        _candidate_rows(decisions),
        [
            "facility_id",
            "facility_name",
            "facility_class",
            "tsa",
            "tenure_class",
            "licence_number",
            "holder",
            "allocated_m3_y",
            "status",
            "match_method",
            "match_confidence",
            "candidate_count",
            "same_tsa_candidate_count",
            "candidate_facility_ids",
            "candidate_facility_names",
            "candidate_tsas",
            "evidence",
            "source",
            "evidence_tier",
        ],
    )
    _write_csv(
        out_dir / "contracts_bc_apportionment.csv",
        contracts,
        [
            "facility_id",
            "grade",
            "min_volume",
            "max_volume",
            "gate_cost",
            "source",
            "evidence_tier",
            "contract_source",
            "facility_name",
            "facility_class",
            "tsa",
            "holder",
            "licence_numbers",
            "licence_count",
            "nameplate_m3_y",
            "exceeds_nameplate",
            "stage_a_scope",
            "notes",
        ],
    )
    summary = {
        "parsed": _parse_summary(parsed),
        "crosswalk": _crosswalk_summary(parsed, decisions, contracts),
        "contracts": {
            "evidence_tier": CONTRACT_EVIDENCE_TIER,
            "min_volume": 0.0,
            "gate_cost": 0.0,
            "caveat": (
                "Apportionment-derived maximum entitlements, not real supply "
                "contracts. They cap Stage A only where a 2018 licence row could "
                "be matched to exactly one facility."
            ),
        },
        "notes": [
            "Only positive 2018 rows are matched; non-positive rows are written to tsa_apportionment_2018_rejected.csv.",
            "Licence rows carry holders and volumes; apportionment-class rows carry TSA totals without a holder and are used only as the reported denominator.",
            "BCTS, woodlot, community forest, First Nation woodland, and unapportioned rows have no licence holder in the parsed licence detail and therefore cannot enter the crosswalk.",
            "FACILITY TSA is a point-in-polygon join against the FADM TSA view; TL-hub proxy coordinates are flagged in bc_facility_tsa.csv.",
            "Ambiguous holder/TSA combinations are left unmatched; no facility ID is invented.",
        ],
    }
    (out_dir / "tenure_crosswalk_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return summary


def build_tenure_crosswalk(
    repo_root: Path,
    out_dir: Path,
    *,
    apportionments: Sequence[TsaApportionment] | None = None,
    recovered_path: Path | None = None,
    stage_a_path: Path | None = None,
    ffcm_registry_path: Path | None = None,
    tsa_boundaries_path: Path | None = None,
    tsa_cache_path: Path | None = None,
    workbook_path: Path | None = None,
    period_year: int = 2018,
) -> dict[str, Any]:
    """Build the facility tenure crosswalk and apportionment-derived contracts."""
    repo_root = Path(repo_root)
    out_dir = Path(out_dir)
    recovered_path = recovered_path or out_dir / "facilities_bc_recovered.csv"
    stage_a_path = stage_a_path or out_dir / "facilities_bc.csv"
    ffcm_registry_path = ffcm_registry_path or repo_root / DEFAULT_FFCM_REGISTRY
    tsa_boundaries_path = tsa_boundaries_path or repo_root / DEFAULT_TSA_BOUNDARIES
    tsa_cache_path = tsa_cache_path or out_dir / DEFAULT_FACILITY_TSA_CACHE
    workbook_path = workbook_path or repo_root / DEFAULT_TSA_APPORTIONMENT

    if apportionments is None:
        parsed = parse_apportionment_rows(
            workbook_path,
            period_year=period_year,
            long_table_path=out_dir / "tsa_tenure_apportionment_long.csv",
        )
    else:
        period_rows = tuple(
            row for row in apportionments if row.period_year == period_year
        )
        parsed = ApportionmentParse(
            period_year=period_year,
            workbook_sheet_count=_workbook_sheet_count(workbook_path),
            worksheet_count=len({row.source_sheet for row in apportionments}),
            row_count=len(apportionments),
            period_row_count=len(period_rows),
            positive_rows=tuple(row for row in period_rows if row.volume_m3 > 0.0),
            nonpositive_rows=tuple(
                row for row in period_rows if row.volume_m3 <= 0.0
            ),
        )

    if not recovered_path.exists():
        raise BcFibreError(f"recovered facility table is missing: {recovered_path}")
    recovered_rows = _read_csv(recovered_path)
    stage_a_rows = _read_csv(stage_a_path) if stage_a_path.exists() else []
    coordinates = _facility_coordinates(recovered_rows, stage_a_rows)
    tsa_rows = ensure_facility_tsa_table(
        coordinates,
        boundaries_path=tsa_boundaries_path,
        cache_path=tsa_cache_path,
    )
    tsa_cache = {str(row["facility_id"]): row for row in tsa_rows}
    facilities = read_facility_records(
        recovered_path,
        stage_a_path=stage_a_path,
        ffcm_registry_path=ffcm_registry_path,
        tsa_cache=tsa_cache,
    )
    decisions = tuple(
        match_licence(row, facilities)
        for row in _sorted_licence_rows(parsed.positive_licence_rows)
    )
    contracts = _contract_rows(decisions)
    summary = write_tenure_crosswalk_outputs(out_dir, parsed, decisions, contracts)
    return summary
