"""Acquire and stage BC Timber Sales and BC stumpage evidence.

The module has three jobs:

1. Page through the BCGW BCTS Sales Schedule layer and stage one row per
   scheduled sale block (no geometry).
2. Parse the published Interior non-appraised tabular stumpage rates and the
   Interior/Coast MPS update text into auditable tables.
3. Build a Crown-perspective ledger sample under the forest cost/revenue
   schema.

It never invents an auction bid, a delivered log price, or a harvest cost.  The
BCTS layer is a sale schedule, not an auction-results table, so it has no upset
or winning bid and cannot supply ``stumpage_bid_premium`` rows.
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
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

BCTS_LAYER_URL = (
    "https://delivery.maps.gov.bc.ca/arcgis/rest/services/"
    "whse/bcgw_pub_whse_forest_tenure/MapServer/35"
)
BCTS_LAYER_NAME = "BCTS Sales Schedule - Public Access"
BCTS_DATASET_URL = (
    "https://catalogue.data.gov.bc.ca/dataset/"
    "307fbe58-79ea-466e-a994-347dfd2e83aa"
)
BC_OGL_URL = "https://www2.gov.bc.ca/gov/content/data/open-data/open-government-licence-bc"

NON_APPRAISED_PAGE_URL = (
    "https://www2.gov.bc.ca/gov/content/industry/forestry/"
    "competitive-forest-industry/timber-pricing/interior-timber-pricing/"
    "interior-non-appraised-tabular-stumpage-rates"
)
NON_APPRAISED_PDF_URL = (
    "https://www2.gov.bc.ca/assets/gov/farming-natural-resources-and-industry/"
    "forestry/timber-pricing/interior-timber-pricing/"
    "interior_non_appraised_tabular_rates_nov_26.pdf"
)
INTERIOR_MPS_URL = (
    "https://www2.gov.bc.ca/assets/gov/farming-natural-resources-and-industry/"
    "forestry/timber-pricing/interior-timber-pricing/"
    "interior_mps_update_2025b.pdf"
)
COAST_MPS_URL = (
    "https://www2.gov.bc.ca/assets/gov/farming-natural-resources-and-industry/"
    "forestry/timber-pricing/coast-timber-pricing/"
    "coast_mps_update_jan_25.pdf"
)

BCTS_SOURCE_ID = "bcts_sales_schedule"
NON_APPRAISED_SOURCE_ID = "bc_nonappraised_tabular"
MPS_SOURCE_ID = "bc_mps_update"

SALES_FIELDS: tuple[str, ...] = (
    "OBJECTID",
    "BCTS_SSSV_SYSID",
    "DIVI_SHORT_CODE",
    "FIELD_TEAM",
    "LICENCE_ID",
    "LICN_CATEGORY_ID",
    "LINC_CERT_LEVEL_ID",
    "GEOGRAPHIC_LOCATION",
    "BLOCK_ID",
    "BLAL_MERCH_HA_AREA",
    "BLOCK_VOLUME",
    "AUCTION_STATUS",
    "AUCTION_DATE",
    "AUCTION_FISCAL",
    "AUCTION_QUARTER",
    "AUC_QUART_MONTH",
    "CURRENT_PERIOD_START",
    "CURRENT_PERIOD_END",
    "SILVICULTURE_SYSTEM",
    "YARDING_SYSTEM",
    "PIECE_SIZE",
    "TERM",
    "SPECIES_COMPOSITION",
    "species_primary",
    "species_primary_pct",
    "species_count",
    "species_sum_pct",
    "FEATURE_AREA_SQM",
    "FEATURE_LENGTH_M",
)

_SPECIES_TOKEN_RE = re.compile(r"([A-Za-z]+)\s*([0-9]*\.?[0-9]+)?")
_DATE_FIELDS = ("AUCTION_DATE", "CURRENT_PERIOD_END")
_TABLE_RE = re.compile(r"^\s*Table\s+(6-\d+[a-z]?):\s*(.*)$")
_HEADER_RE = re.compile(r"^\s*FOREST ZONE\s+(.+?)\s*$")
_RATE_ROW_RE = re.compile(r"^\s*(.+?)\s+((?:-?\d+\.\d+\s+){9}-?\d+\.\d+)\s*$")
_EFFECTIVE_RE = re.compile(r"Effective Date:\s*(\d{4}-\d{2}-\d{2})")


class BctsDataError(ValueError):
    """The source payload is not the shape this parser was written for."""


# ---------------------------------------------------------------------------
# ArcGIS REST access
# ---------------------------------------------------------------------------


def fetch_json(url: str, params: Mapping[str, str], *, timeout: int = 90) -> Any:
    """Fetch one ArcGIS JSON response with a descriptive failure mode."""
    request_url = f"{url}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(
        request_url,
        headers={"User-Agent": "clews-c2070-fresh/0.1 (BCTS staging)"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
    except OSError as exc:
        raise BctsDataError(f"ArcGIS request failed: {request_url}: {exc}") from exc
    if isinstance(payload, dict) and "error" in payload:
        raise BctsDataError(f"ArcGIS error for {request_url}: {payload['error']}")
    return payload


def arcgis_count(query_url: str, *, fetch: Callable[..., Any] = fetch_json) -> int:
    payload = fetch(query_url, {"where": "1=1", "returnCountOnly": "true", "f": "json"})
    count = payload.get("count")
    if not isinstance(count, int):
        raise BctsDataError(f"returnCountOnly response has no integer count: {payload}")
    return count


def paginate_features(
    query_url: str,
    *,
    page_size: int = 1000,
    fetch: Callable[..., Any] = fetch_json,
) -> list[dict[str, Any]]:
    """Page until the layer stops reporting an exceeded transfer limit.

    ArcGIS can return a full page with ``exceededTransferLimit=false``; that is
    the last page.  An empty page always terminates.  Duplicate OBJECTIDs are
    rejected rather than silently de-duplicated.
    """
    if page_size <= 0:
        raise ValueError("page_size must be positive")

    features: list[dict[str, Any]] = []
    offset = 0
    while True:
        payload = fetch(
            query_url,
            {
                "where": "1=1",
                "outFields": "*",
                "returnGeometry": "false",
                "resultOffset": str(offset),
                "resultRecordCount": str(page_size),
                "f": "json",
            },
        )
        batch = payload.get("features") or []
        if not batch:
            break
        features.extend(batch)
        offset += len(batch)
        if not payload.get("exceededTransferLimit") and len(batch) < page_size:
            break
        if offset > 1_000_000:
            raise BctsDataError("pagination exceeded 1,000,000 rows; refusing to continue")

    object_ids = [row.get("attributes", {}).get("OBJECTID") for row in features]
    if any(object_id is None for object_id in object_ids):
        raise BctsDataError("every BCTS feature must carry OBJECTID")
    if len(set(object_ids)) != len(object_ids):
        raise BctsDataError("pagination returned duplicate OBJECTID values")
    return features


# ---------------------------------------------------------------------------
# Attribute parsing
# ---------------------------------------------------------------------------


def parse_species_composition(raw: str) -> dict[str, float]:
    """Parse ``PL94.9; S4.6; B.5`` into ``{'PL': 94.9, ...}``.

    BCTS omits the leading zero (``B.5``), so the numeric group is optional and
    an omitted group means zero percent.  The percentages must sum to 100 within
    one percentage point; outside that, the source row is malformed and fails
    loud.
    """
    if not raw or not raw.strip():
        raise BctsDataError("species composition is blank")
    shares: dict[str, float] = {}
    for chunk in raw.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        match = _SPECIES_TOKEN_RE.fullmatch(chunk)
        if match is None:
            raise BctsDataError(f"unparseable species chunk {chunk!r} in {raw!r}")
        code = match.group(1).upper()
        share = float(match.group(2) or 0.0)
        if share < 0 or share > 100:
            raise BctsDataError(f"species share out of range in {raw!r}: {chunk!r}")
        shares[code] = shares.get(code, 0.0) + share
    total = sum(shares.values())
    if not math.isclose(total, 100.0, abs_tol=1.0):
        raise BctsDataError(f"species composition sums to {total}, not 100: {raw!r}")
    return shares


def _epoch_ms_to_date(value: Any) -> str | None:
    if value is None or value == "":
        return None
    try:
        milliseconds = int(value)
    except (TypeError, ValueError) as exc:
        raise BctsDataError(f"expected epoch milliseconds, got {value!r}") from exc
    return datetime.fromtimestamp(milliseconds / 1000, tz=UTC).date().isoformat()


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def normalise_sale(attributes: Mapping[str, Any]) -> dict[str, Any]:
    """Return one flat sale row with derived species fields."""
    raw_species = attributes.get("SPECIES_COMPOSITION")
    if raw_species is None:
        raise BctsDataError(
            f"OBJECTID {attributes.get('OBJECTID')} has no species composition"
        )
    shares = parse_species_composition(str(raw_species))
    primary_code = max(shares, key=lambda code: (shares[code], code))

    row: dict[str, Any] = {
        "OBJECTID": attributes.get("OBJECTID"),
        "BCTS_SSSV_SYSID": attributes.get("BCTS_SSSV_SYSID"),
        "DIVI_SHORT_CODE": attributes.get("DIVI_SHORT_CODE"),
        "FIELD_TEAM": attributes.get("FIELD_TEAM"),
        "LICENCE_ID": attributes.get("LICENCE_ID"),
        "LICN_CATEGORY_ID": attributes.get("LICN_CATEGORY_ID"),
        "LINC_CERT_LEVEL_ID": attributes.get("LINC_CERT_LEVEL_ID"),
        "GEOGRAPHIC_LOCATION": attributes.get("GEOGRAPHIC_LOCATION"),
        "BLOCK_ID": attributes.get("BLOCK_ID"),
        "BLAL_MERCH_HA_AREA": _optional_float(attributes.get("BLAL_MERCH_HA_AREA")),
        "BLOCK_VOLUME": _optional_float(attributes.get("BLOCK_VOLUME")),
        "AUCTION_STATUS": attributes.get("AUCTION_STATUS"),
        "AUCTION_DATE": _epoch_ms_to_date(attributes.get("AUCTION_DATE")),
        "AUCTION_FISCAL": _optional_float(attributes.get("AUCTION_FISCAL")),
        "AUCTION_QUARTER": attributes.get("AUCTION_QUARTER"),
        "AUC_QUART_MONTH": attributes.get("AUC_QUART_MONTH"),
        "CURRENT_PERIOD_START": attributes.get("CURRENT_PERIOD_START"),
        "CURRENT_PERIOD_END": _epoch_ms_to_date(attributes.get("CURRENT_PERIOD_END")),
        "SILVICULTURE_SYSTEM": attributes.get("SILVICULTURE_SYSTEM"),
        "YARDING_SYSTEM": attributes.get("YARDING_SYSTEM"),
        "PIECE_SIZE": _optional_float(attributes.get("PIECE_SIZE")),
        "TERM": attributes.get("TERM"),
        "SPECIES_COMPOSITION": raw_species,
        "species_primary": primary_code,
        "species_primary_pct": round(shares[primary_code], 1),
        "species_count": len(shares),
        "species_sum_pct": round(sum(shares.values()), 1),
        "FEATURE_AREA_SQM": _optional_float(attributes.get("FEATURE_AREA_SQM")),
        "FEATURE_LENGTH_M": _optional_float(attributes.get("FEATURE_LENGTH_M")),
    }

    volume = row["BLOCK_VOLUME"]
    if volume is None or not math.isfinite(volume) or volume <= 0:
        raise BctsDataError(
            f"OBJECTID {row['OBJECTID']} has non-positive BLOCK_VOLUME={volume!r}"
        )
    if not row["LICENCE_ID"] or not row["BLOCK_ID"]:
        raise BctsDataError(
            f"OBJECTID {row['OBJECTID']} has a blank licence or block id"
        )
    return row


def build_sales_rows(features: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows = [normalise_sale(feature["attributes"]) for feature in features]
    rows.sort(key=lambda row: row["OBJECTID"])
    keys = [(row["LICENCE_ID"], row["BLOCK_ID"]) for row in rows]
    if len(set(keys)) != len(keys):
        raise BctsDataError("licence + block id is not unique in the sale schedule")
    return rows


def _weighted(values: Iterable[tuple[float, float]]) -> float:
    numerator = 0.0
    denominator = 0.0
    for value, weight in values:
        numerator += value * weight
        denominator += weight
    if denominator <= 0:
        raise BctsDataError("cannot weight by zero total volume")
    return numerator / denominator


def summarise_sales(
    rows: Sequence[Mapping[str, Any]],
    *,
    source_count: int,
    retrieved_utc: str,
) -> dict[str, Any]:
    if not rows:
        raise BctsDataError("cannot summarise an empty sale table")
    if len(rows) != source_count:
        raise BctsDataError(
            f"staged row count {len(rows)} does not match source count {source_count}"
        )

    volumes = [float(row["BLOCK_VOLUME"]) for row in rows]
    areas = [
        float(row["BLAL_MERCH_HA_AREA"])
        for row in rows
        if row["BLAL_MERCH_HA_AREA"] is not None
    ]
    volume_by_status: dict[str, float] = defaultdict(float)
    status_counts: Counter[str] = Counter()
    fiscal_counts: Counter[str] = Counter()
    quarter_counts: Counter[str] = Counter()
    primary_counts: Counter[str] = Counter()
    species_volume: dict[str, float] = defaultdict(float)

    for row in rows:
        status = str(row["AUCTION_STATUS"])
        status_counts[status] += 1
        volume_by_status[status] += float(row["BLOCK_VOLUME"])
        fiscal_counts[str(int(row["AUCTION_FISCAL"]))] += 1
        quarter_counts[str(row["AUCTION_QUARTER"])] += 1
        primary_counts[str(row["species_primary"])] += 1
        shares = parse_species_composition(str(row["SPECIES_COMPOSITION"]))
        share_total = sum(shares.values())
        for code, share in shares.items():
            species_volume[code] += (
                float(row["BLOCK_VOLUME"]) * share / share_total
            )

    total_volume = sum(volumes)
    species_volume_total = sum(species_volume.values())
    if not math.isclose(total_volume, species_volume_total, rel_tol=0.0, abs_tol=1.0):
        raise BctsDataError(
            "species-weighted volume does not reconcile to block volume: "
            f"{species_volume_total} vs {total_volume}"
        )

    def quantile(values: Sequence[float], fraction: float) -> float:
        ordered = sorted(values)
        index = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
        return ordered[index]

    species_ranked = sorted(
        species_volume.items(), key=lambda item: (-item[1], item[0])
    )
    return {
        "artifact_version": "0.1.0",
        "generated_utc": retrieved_utc,
        "scope": "BC Timber Sales scheduled sale blocks; attributes only, no geometry",
        "source": {
            "id": BCTS_SOURCE_ID,
            "name": BCTS_LAYER_NAME,
            "url": BCTS_LAYER_URL,
            "dataset_url": BCTS_DATASET_URL,
            "licence": "Open Government Licence - British Columbia",
            "licence_url": BC_OGL_URL,
            "access": "ArcGIS REST query, f=json, returnGeometry=false",
            "query": "where=1=1; outFields=*; paginated at 1000 records",
            "retrieved_utc": retrieved_utc,
        },
        "counts": {
            "source_count": source_count,
            "staged_rows": len(rows),
            "unique_objectid": len({row["OBJECTID"] for row in rows}),
            "unique_licences": len({row["LICENCE_ID"] for row in rows}),
            "unique_licence_block": len(
                {(row["LICENCE_ID"], row["BLOCK_ID"]) for row in rows}
            ),
            "status_codes": dict(sorted(status_counts.items())),
            "auction_fiscal": dict(sorted(fiscal_counts.items())),
            "auction_quarter": dict(sorted(quarter_counts.items())),
            "primary_species_rows": dict(
                sorted(primary_counts.items(), key=lambda item: (-item[1], item[0]))
            ),
        },
        "volume_m3": {
            "total": round(total_volume, 6),
            "min": round(min(volumes), 6),
            "max": round(max(volumes), 6),
            "median": round(quantile(volumes, 0.5), 6),
            "p90": round(quantile(volumes, 0.9), 6),
            "by_status": {
                key: round(value, 6) for key, value in sorted(volume_by_status.items())
            },
            "weighted_by_species": {
                key: round(value, 6) for key, value in species_ranked
            },
        },
        "area_ha": {
            "reported_rows": len(areas),
            "missing_rows": len(rows) - len(areas),
            "total": round(sum(areas), 6),
            "min": round(min(areas), 6) if areas else None,
            "max": round(max(areas), 6) if areas else None,
        },
        "validation": {
            "count_matches_arcgis": len(rows) == source_count,
            "objectid_unique": len({row["OBJECTID"] for row in rows}) == len(rows),
            "licence_block_unique": len(
                {(row["LICENCE_ID"], row["BLOCK_ID"]) for row in rows}
            )
            == len(rows),
            "volume_positive": all(volume > 0 for volume in volumes),
            "species_sums_within_1pct": all(
                abs(float(row["species_sum_pct"]) - 100.0) <= 1.0 for row in rows
            ),
            "species_weighted_volume_reconciles": math.isclose(
                total_volume, species_volume_total, abs_tol=1.0
            ),
            "geometry_stored": False,
        },
        "gaps": [
            "No upset price, winning bid, bonus bid, or bidder count field in this layer.",
            "No delivered log price, harvest cost, road cost, haul cost, or silviculture actual.",
            "AUCTION_STATUS codes are preserved as published; P/D semantics are not documented in the layer metadata.",
            "TERM contains negative values (44 rows); their semantics are unresolved and the values are preserved, not interpreted.",
            "BCTS is a selected auction sample, not the provincial harvest population.",
        ],
    }


# ---------------------------------------------------------------------------
# Non-appraised tabular rates
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StumpageRate:
    table: str
    table_title: str
    effective_date: str
    forest_zone: str
    species: str
    rate_cad_per_m3: float


def _numeric_tokens(line: str) -> list[float]:
    return [float(token) for token in re.findall(r"-?\d+\.\d+", line)]


def parse_nonappraised_text(text: str) -> list[StumpageRate]:
    """Parse the five Interior sawlog tables from ``pdftotext -layout``.

    The PDF uses one row per forest zone with ten numeric columns: nine species
    plus an average.  Every table is validated to have seven zones and ten
    columns.  A different shape is a source change and fails loud.
    """
    effective_match = _EFFECTIVE_RE.search(text)
    if effective_match is None:
        raise BctsDataError("non-appraised PDF has no 'Effective Date: YYYY-MM-DD'")
    effective_date = effective_match.group(1)

    tables: dict[str, dict[str, Any]] = {}
    current_table: str | None = None
    for line in text.splitlines():
        table_match = _TABLE_RE.match(line)
        if table_match is not None:
            current_table = table_match.group(1)
            tables[current_table] = {
                "title": table_match.group(2).strip(),
                "species": None,
                "rows": [],
            }
            continue
        if current_table is None:
            continue
        header_match = _HEADER_RE.match(line)
        if header_match is not None:
            species = header_match.group(1).split()
            if len(species) != 10:
                raise BctsDataError(
                    f"Table {current_table} header has {len(species)} columns, not 10"
                )
            tables[current_table]["species"] = species
            continue
        rate_match = _RATE_ROW_RE.match(line)
        if rate_match is None:
            continue
        if tables[current_table]["species"] is None:
            raise BctsDataError(f"Table {current_table} rate row before header")
        rate_values = [float(value) for value in rate_match.group(2).split()]
        if len(rate_values) != 10:
            raise BctsDataError(
                f"Table {current_table} row has {len(rate_values)} values, not 10"
            )
        tables[current_table]["rows"].append(
            (rate_match.group(1).strip(), rate_values)
        )

    expected_tables = {"6-1", "6-2", "6-4", "6-4a", "6-5"}
    if set(tables) != expected_tables:
        raise BctsDataError(
            f"expected tables {sorted(expected_tables)}, got {sorted(tables)}"
        )

    rates: list[StumpageRate] = []
    for table_id in sorted(tables):
        table = tables[table_id]
        species = table["species"]
        rows = table["rows"]
        if len(rows) != 7:
            raise BctsDataError(
                f"Table {table_id} has {len(rows)} zone rows, expected 7"
            )
        for zone, values in rows:
            for code, value in zip(species, values, strict=True):
                rates.append(
                    StumpageRate(
                        table=table_id,
                        table_title=table["title"],
                        effective_date=effective_date,
                        forest_zone=zone,
                        species=code,
                        rate_cad_per_m3=value,
                    )
                )
    return rates


def nonappraised_rows(rates: Sequence[StumpageRate]) -> list[dict[str, Any]]:
    return [
        {
            "table": rate.table,
            "table_title": rate.table_title,
            "effective_date": rate.effective_date,
            "forest_zone": rate.forest_zone,
            "species": rate.species,
            "rate_cad_per_m3": f"{rate.rate_cad_per_m3:.2f}",
        }
        for rate in rates
    ]


def _spatial_unit_for_zone(zone: str) -> str:
    token = re.sub(r"[^A-Za-z0-9]+", "_", zone).strip("_").upper()
    return f"BC-FOREST_ZONE_{token}"


def build_ledger_sample(
    rates: Sequence[StumpageRate],
    *,
    year: int,
    effective_date: str,
) -> list[dict[str, str]]:
    """Build Crown-only stumpage-base sample rows from Table 6-1.

    Only Table 6-1 (the base coniferous average sawlog schedule) enters the
    ledger.  Tables 6-2, 6-4, 6-4a, and 6-5 are alternative schedules for
    community forests, damaged timber, and post-harvest material; including
    them would double count the same Crown charge.  ``volume_m3`` is a unit-m3
    rate weight, not a harvest volume, because the published table is a rate
    table without a volume denominator.
    """
    base_rows = [rate for rate in rates if rate.table == "6-1" and rate.species != "AVG"]
    if len(base_rows) != 63:
        raise BctsDataError(
            f"expected 7 zones x 9 species = 63 Table 6-1 rows, got {len(base_rows)}"
        )

    ledger_rows: list[dict[str, str]] = []
    for rate in base_rows:
        ledger_rows.append(
            {
                "jurisdiction": "BC",
                "source": NON_APPRAISED_SOURCE_ID,
                "year": str(year),
                "spatial_unit": _spatial_unit_for_zone(rate.forest_zone),
                "species": rate.species,
                "product": "sawlog",
                "volume_m3": "1.0",
                "unit": "CAD_per_m3_log",
                "value_cad": f"{rate.rate_cad_per_m3:.2f}",
                "value_basis": "administered_rate:standing",
                "perspective": "crown",
                "component": "stumpage_base",
                "sign": "1",
                "confidence": "high",
                "licence": "none",
                "notes": (
                    "currency_basis=CAD_nominal; "
                    f"source_table=Table {rate.table} {rate.table_title}; "
                    f"rate_effective={effective_date}; "
                    "volume_weight=unit_m3_rate_sample; volume_is_not_harvest=1; "
                    "alternative_tables_excluded=6-2,6-4,6-4a,6-5; "
                    "coverage=partial; coverage_denominator=published_rate_table; "
                    "aggregated_from=1"
                ),
            }
        )
    return ledger_rows


# ---------------------------------------------------------------------------
# MPS update tables
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MpsValue:
    document: str
    section: str
    item: str
    value_text: str
    value_numeric: float | None
    unit: str
    notes: str


_INTERIOR_EQUATION_VARIABLES: tuple[str, ...] = (
    "LN (Number of Bidders)",
    "Constant",
    "Real Stand Selling Price (Scale Based) – 2MA",
    "Real Stand Selling Price (Cruise Based) – 2MA",
    "Cedar Fraction",
    "Cedar Fraction*Cedar Decay",
    "Hemlock Fraction",
    "Balsam Fraction Squared",
    "Larch Fraction + Yellow Pine Fraction",
    "Cable Yarding post 2013",
    "LN(Coniferous Volume/1000)",
    "Net Decay Fraction",
    "Fire Damaged Fraction",
    "LN (Volume per Tree)",
    "Cycle",
    "Zone 9",
    "Cruise Based * (1 – RG35)",
    "Slope (>15)",
    "Grey Fraction Squared",
    "Exchange Rate - 2MA",
    "AAC Delta (AAC – Harvest)",
    "Net Blowdown",
    "Net Deciduous (Cruise Based)",
    "Camp",
    "Distance to Support Centre",
    "Distance to Support Centre 200",
    "Partial Cut 20",
    "Other Attack",
)

_COAST_LOSS_FACTOR_VARIABLES: tuple[str, ...] = (
    "Constant",
    "Number of Bidders",
    "Cedar Lumber High Grade",
    "Cedar Lumber Mid Grade",
    "Fir Lumber High and Mid Grade",
    "Hembal Lumber High Grade",
    "Hembal Lumber Mid Grade",
    "Cypress Lumber High and Mid Grade",
    "Old Growth LN (Volume per Log)",
    "LN (Volume per Hectare/1000)",
    "Conventional Slope * (1-Heli Share)",
    "Heli Land Drop",
    "Heli Water Drop",
    "Location",
    "Isolated",
    "Lumpsum",
    "North American and Japanese Housing Starts",
    "Harvest Volume",
)

_COAST_CALL_GRADE_VARIABLES: tuple[str, ...] = (
    "Constant",
    "Number of Bidders",
    "Cedar Lumber High Grade",
    "Cedar Lumber Mid Grade",
    "Fir Lumber High and Mid Grade",
    "Cypress Lumber High and Mid Grade",
    "Old Growth LN (Volume per Log)",
    "LN (Volume per Hectare/1000)",
    "Conventional Slope",
    "Heli Land Drop",
    "Heli Water Drop",
    "Location",
    "Isolated",
    "Lumpsum",
    "North American and Japanese Housing Starts",
    "Harvest Volume",
)


def _normalise_text(text: str) -> str:
    return " ".join(text.split())


def _equation_values(
    text: str, variables: Sequence[str]
) -> list[tuple[str, float, float]]:
    normalised = _normalise_text(text)
    values: list[tuple[str, float, float]] = []
    for variable in variables:
        pattern = re.escape(variable) + r"\s+(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)"
        match = re.search(pattern, normalised)
        if match is None and variable == "North American and Japanese Housing Starts":
            # The PDF puts the coefficient between "Housing" and "Starts".
            pattern = (
                r"North American and Japanese Housing\s+"
                r"(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s+Starts"
            )
            match = re.search(pattern, normalised)
        if match is None:
            continue
        values.append((variable, float(match.group(1)), float(match.group(2))))
    if len(values) < max(10, len(variables) // 2):
        raise BctsDataError(
            f"parsed only {len(values)} of {len(variables)} MPS coefficients"
        )
    return values


def _section(text: str, start: str, end: str) -> str:
    start_index = text.find(start)
    if start_index < 0:
        raise BctsDataError(f"MPS text has no section marker {start!r}")
    end_index = text.find(end, start_index + len(start))
    if end_index < 0:
        raise BctsDataError(f"MPS text has no end marker {end!r} after {start!r}")
    return text[start_index:end_index]


def _dollar_values(
    text: str, *, document: str, section: str
) -> list[MpsValue]:
    values: list[MpsValue] = []
    for line in text.splitlines():
        stripped = " ".join(line.split())
        if "$" not in stripped or len(stripped) < 8:
            continue
        money = re.search(
            r"\$([0-9][0-9,]*(?:\.[0-9]+)?)(?:\s*/\s*(m3|tree))?",
            stripped,
        )
        if money is None:
            continue
        amount = float(money.group(1).replace(",", ""))
        unit = f"CAD_per_{money.group(2)}" if money.group(2) else "CAD"
        label = stripped[: money.start()].strip(" .:-")
        if not label:
            continue
        values.append(
            MpsValue(
                document=document,
                section=section,
                item=label,
                value_text=stripped[money.start() :].split("  ")[0].strip(),
                value_numeric=amount,
                unit=unit,
                notes="parsed from pdftotext -layout; label may be abbreviated",
            )
        )
    return values


def parse_mps_updates(
    interior_text: str, coast_text: str
) -> tuple[list[dict[str, Any]], list[MpsValue]]:
    interior_count = re.search(r"data from ([\d,]+) sales", interior_text)
    coast_count = re.search(r"data from ([\d,]+) sales", coast_text)
    if interior_count is None or coast_count is None:
        raise BctsDataError("could not find the MPS auction dataset counts")

    equations: list[dict[str, Any]] = []
    interior_equation_text = _section(
        interior_text, "3.        EQUATIONS", "4.       SPECIFIED OPERATIONS"
    )
    coast_loss_text = _section(
        coast_text,
        "3.1 2025 Winning Bid – Loss Factor Cruise",
        "3.2 2025 Winning Bid – Call Grade Net Factor Cruise",
    )
    coast_call_text = _section(
        coast_text,
        "3.2 2025 Winning Bid – Call Grade Net Factor Cruise",
        "4. Specified Operations",
    )
    for name, text, variables in (
        ("interior_final_ewb_2025", interior_equation_text, _INTERIOR_EQUATION_VARIABLES),
        (
            "coast_loss_factor_cruise_2025",
            coast_loss_text,
            _COAST_LOSS_FACTOR_VARIABLES,
        ),
        (
            "coast_call_grade_net_factor_cruise_2025",
            coast_call_text,
            _COAST_CALL_GRADE_VARIABLES,
        ),
    ):
        for variable, coefficient, t_statistic in _equation_values(text, variables):
            equations.append(
                {
                    "equation": name,
                    "variable": variable,
                    "coefficient": coefficient,
                    "t_statistic": t_statistic,
                }
            )

    values: list[MpsValue] = []
    values.append(
        MpsValue(
            document="interior_mps_update_2025b",
            section="2. Auction Dataset",
            item="sales in 2010-2024 auction dataset",
            value_text=interior_count.group(1),
            value_numeric=float(interior_count.group(1).replace(",", "")),
            unit="count",
            notes="15-year period, January 1, 2010 to December 31, 2024",
        )
    )
    values.append(
        MpsValue(
            document="coast_mps_update_2025_01",
            section="2. Auction Dataset",
            item="sales in 2006-2024 auction dataset",
            value_text=coast_count.group(1),
            value_numeric=float(coast_count.group(1).replace(",", "")),
            unit="count",
            notes="18-year period, July 1, 2006 to June 30, 2024",
        )
    )
    values.extend(
        _dollar_values(
            _section(
                interior_text,
                "4.       SPECIFIED OPERATIONS",
                "6.       SUMMARY",
            ),
            document="interior_mps_update_2025b",
            section="4-5. Specified operations and tenure obligations",
        )
    )
    values.extend(
        _dollar_values(
            _section(coast_text, "4. Specified Operations", "6. SUMMARY"),
            document="coast_mps_update_2025_01",
            section="4-5. Specified operations and tenure obligations",
        )
    )
    return equations, values


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------


def write_csv(path: Path, fieldnames: Sequence[str], rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="raise")
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name, "") for name in fieldnames})


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def utc_now() -> str:
    return datetime.now(tz=UTC).replace(microsecond=0).isoformat()
