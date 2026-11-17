"""BC facility table, coordinates, and normalized processing capacity.

This module is the facility-side parsed boundary for Stage A.  It builds:

* one row per BC facility with an explicit coordinate source;
* normalized nameplate capacity in green m3/y;
* the facility/grade offers consumed by the LP.

Coordinates are never Euclidean guesses.  The precedence is Mills DB, CFS
``suppliers.dbf``, an FFCM TL/water hub anchor derived from geocoded facilities
attached to that hub, and finally ``missing``.
"""

from __future__ import annotations

import csv
import math
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from . import units
from .schema import (
    AssignmentError,
    FacilityCapacity,
    FacilityOffer,
    require_supported_capacity_unit,
)

__all__ = [
    "BC_FACILITY_CLASSES",
    "COORD_SOURCES",
    "FacilityError",
    "FacilityRecord",
    "FacilityTable",
    "accepted_grades_for",
    "build_facility_capacities",
    "build_facility_table",
    "normalize_line_capacity",
]


class FacilityError(RuntimeError):
    """Raised when a facility source table cannot produce a defensible record."""


BC_FACILITY_CLASSES = ("SAW", "PAP", "PEL", "BIO", "PLY", "OSB", "NSP")
STAGE_A_FACILITY_CLASSES = frozenset({"SAW", "PAP", "PEL", "BIO"})
COORD_SOURCES = ("mills_db", "suppliers_dbf", "tl_anchor_proxy", "missing")
COORD_CONFIDENCE = {
    "mills_db": "high",
    "suppliers_dbf": "high",
    "tl_anchor_proxy": "low",
    "missing": "missing",
}

_LOG_GRADES = frozenset({"SAWLOG", "PULP", "OTHER_LOG"})
_GRADE_PRODUCTS = {
    "SAWLOG": "sawlog",
    "PULP": "pulpwood",
    "OTHER_LOG": "other",
    "RESIDUE": "residue",
    "HOGFUEL": "hogfuel",
}
_GRADE_SORTS = {
    "SAWLOG": (
        "D125L11",
        "D125L13",
        "D125L51",
        "D125L63",
        "D125L8",
        "D20L11",
        "D20L8",
        "D30L8",
    ),
    "PULP": ("D125L50",),
    "OTHER_LOG": ("D152", "D178", "D203", "D254"),
    "RESIDUE": ("RESIDUE",),
    "HOGFUEL": ("HOGFUEL",),
}


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FacilityError(f"required facility input is missing: {path}")
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise FacilityError(f"facility input has no data rows: {path}")
    return rows


def _require_columns(
    path: Path, rows: Sequence[dict[str, str]], required: Iterable[str]
) -> None:
    if not rows:
        raise FacilityError(f"{path} has no header")
    missing = set(required) - set(rows[0])
    if missing:
        raise FacilityError(f"{path} is missing required columns: {sorted(missing)}")


def _number(value: object, name: str, *, default: float | None = None) -> float:
    text = str(value).strip()
    if text == "" or text.lower() in {"na", "nan", "none"}:
        if default is not None:
            return default
        raise FacilityError(f"{name} is missing")
    try:
        number = float(text)
    except ValueError as error:
        raise FacilityError(f"{name} is not numeric: {value!r}") from error
    if not math.isfinite(number):
        raise FacilityError(f"{name} is not finite: {value!r}")
    return number


def _truthy(value: object) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


def _identifier(value: object) -> str:
    return "".join(char if char.isalnum() else "_" for char in str(value)).strip("_")


def _valid_lat_lon(lat: float, lon: float) -> bool:
    return -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0


@dataclass(frozen=True, slots=True)
class FacilityRecord:
    """One BC facility with coordinate provenance and normalized nameplate."""

    facility_id: str
    facility_class: str
    name: str
    lat: float | None
    lon: float | None
    coord_source: str
    coord_confidence: str
    coord_evidence: str
    nameplate_m3_y: float | None
    capacity_status: str
    capacity_basis: str
    line_count: int
    lines: tuple[str, ...]
    stage_a_scope: bool
    transport_link_status: str = "not_evaluated"

    def to_dict(self) -> dict[str, Any]:
        return {
            "facility_id": self.facility_id,
            "facility_class": self.facility_class,
            "name": self.name,
            "lat": "" if self.lat is None else self.lat,
            "lon": "" if self.lon is None else self.lon,
            "coord_source": self.coord_source,
            "coord_confidence": self.coord_confidence,
            "coord_evidence": self.coord_evidence,
            "nameplate_m3_y": "" if self.nameplate_m3_y is None else self.nameplate_m3_y,
            "capacity_status": self.capacity_status,
            "capacity_basis": self.capacity_basis,
            "line_count": self.line_count,
            "lines": ";".join(self.lines),
            "stage_a_scope": self.stage_a_scope,
            "transport_link_status": self.transport_link_status,
        }


@dataclass(frozen=True, slots=True)
class FacilityTable:
    """BC facility rows plus coordinate and capacity coverage summaries."""

    records: tuple[FacilityRecord, ...]
    coverage: Mapping[str, int]
    coverage_in_scope: Mapping[str, int]
    confidence: Mapping[str, int]
    missing: tuple[str, ...]
    missing_in_scope: tuple[str, ...]
    anchor_hubs: tuple[dict[str, Any], ...]
    zero_capacity: tuple[str, ...]

    def by_id(self, facility_id: str) -> FacilityRecord:
        for record in self.records:
            if record.facility_id == facility_id:
                return record
        raise FacilityError(f"unknown facility {facility_id}")

    def with_transport_status(
        self, status_by_facility: Mapping[str, str]
    ) -> FacilityTable:
        return replace(
            self,
            records=tuple(
                replace(
                    record,
                    transport_link_status=status_by_facility.get(
                        record.facility_id, record.transport_link_status
                    ),
                )
                for record in self.records
            ),
        )

    def summary(self) -> dict[str, Any]:
        return {
            "facility_count": len(self.records),
            "stage_a_scope_count": sum(
                1 for record in self.records if record.stage_a_scope
            ),
            "coordinate_source_counts": dict(self.coverage),
            "coordinate_source_counts_in_scope": dict(self.coverage_in_scope),
            "coordinate_confidence_counts": dict(self.confidence),
            "missing_coordinate_count": len(self.missing),
            "missing_coordinate_count_in_scope": len(self.missing_in_scope),
            "missing_coordinates": list(self.missing),
            "missing_coordinates_in_scope": list(self.missing_in_scope),
            "zero_capacity_facilities": list(self.zero_capacity),
            "anchor_hubs": list(self.anchor_hubs),
        }


def accepted_grades_for(facility_class: str, feedstock: str) -> tuple[str, ...]:
    """Return the dimension-only grades a facility class accepts from a feedstock."""
    facility_class = facility_class.strip().upper()
    feedstock = feedstock.strip().lower()
    if feedstock == "log":
        if facility_class == "SAW":
            return ("SAWLOG",)
        if facility_class == "PAP":
            return ("SAWLOG", "PULP", "OTHER_LOG")
        if facility_class in {"PEL", "BIO"}:
            return ("PULP", "OTHER_LOG")
        return ()
    if feedstock == "logging_residue":
        return ("RESIDUE",)
    if feedstock == "hogfuel":
        return ("HOGFUEL",)
    return ()


def normalize_line_capacity(
    line: Mapping[str, str],
    *,
    density_book: units.DensityBook | None = None,
) -> tuple[float | None, str, str]:
    """Normalize one processing-line capacity to green m3/y.

    Returns ``(capacity_m3_y, infeed_unit, basis)``.  A rejected line returns
    ``None`` and a ``rejected_*`` basis token.
    """
    unit_text = str(line.get("infeed_unit", "")).strip().lower()
    try:
        require_supported_capacity_unit(unit_text)
    except AssignmentError as error:
        return None, unit_text, f"rejected_capacity_unit:{error}"
    rate = _number(line.get("infeed_rate", ""), "infeed_rate", default=0.0)
    if rate <= 0.0:
        return None, unit_text, "rejected_nonpositive_infeed_rate"
    unit_base = unit_text.removesuffix("/y")
    book = density_book or units.DensityBook()
    context = units.ConversionContext.from_density_book(book)
    try:
        capacity = units.to_m3_green(rate, unit_base, context=context)
    except units.MissingConversionFactorError as error:
        return None, unit_text, f"rejected_missing_conversion_factor:{error}"
    except units.AmbiguousConversionError as error:
        return None, unit_text, f"rejected_ambiguous_unit:{error}"
    except units.UnsupportedConversionError as error:
        return None, unit_text, f"rejected_unsupported_conversion:{error}"
    except units.UnitError as error:
        return None, unit_text, f"rejected_unit:{error}"
    if capacity <= 0.0 or not math.isfinite(capacity):
        return None, unit_text, "rejected_nonpositive_normalized_capacity"
    canonical = units.canonical_unit(unit_base)
    if canonical == "m3_green":
        basis = "archive_m3_y"
    elif canonical == "odt":
        basis = f"units_odt_m3_{context.basic_density_odt_m3:.3f}"
    elif canonical == "bdmt":
        basis = f"units_bdmt_m3_{context.basic_density_odt_m3:.3f}"
    elif canonical == "admt":
        basis = f"units_admt_m3_{context.basic_density_odt_m3:.3f}"
    else:  # pragma: no cover - guarded by the unit contract
        basis = f"units_{_identifier(canonical)}_to_m3_green"
    return capacity, unit_text, basis


def _nameplate_and_basis(
    lines: Sequence[Mapping[str, str]],
    *,
    density_book: units.DensityBook,
) -> tuple[float, str]:
    product_capacity: dict[str, float] = {}
    bases: set[str] = set()
    for line in lines:
        capacity, _unit, basis = normalize_line_capacity(line, density_book=density_book)
        if capacity is None:
            continue
        product = str(line.get("primary_product", "")).strip().lower()
        if not product:
            continue
        product_capacity[product] = max(product_capacity.get(product, 0.0), capacity)
        bases.add(basis)
    nameplate = sum(product_capacity.values())
    if not bases:
        return 0.0, "no_supported_capacity_line"
    return nameplate, ";".join(sorted(bases))


def _coordinate_from_row(
    row: Mapping[str, str],
) -> tuple[float | None, float | None, str, str, str]:
    """Resolve a direct coordinate from the geocoding table."""
    mills_lat = str(row.get("mills_db_latitude", "")).strip()
    mills_lon = str(row.get("mills_db_longitude", "")).strip()
    suppliers_lat = str(row.get("suppliers_lat", "")).strip()
    suppliers_lon = str(row.get("suppliers_long", "")).strip()
    status = str(row.get("match_status", "")).strip().upper()

    mill_coord: tuple[float, float] | None = None
    if mills_lat and mills_lon:
        try:
            mill = (
                float(mills_lat),
                float(mills_lon),
            )
        except ValueError:
            mill = None  # type: ignore[assignment]
        if mill is not None and _valid_lat_lon(*mill):
            mill_coord = mill
    supplier_coord: tuple[float, float] | None = None
    if suppliers_lat and suppliers_lon:
        try:
            supplier = (
                float(suppliers_lat),
                float(suppliers_lon),
            )
        except ValueError:
            supplier = None  # type: ignore[assignment]
        if supplier is not None and _valid_lat_lon(*supplier):
            supplier_coord = supplier

    if mill_coord is not None and supplier_coord is not None:
        conflict = (
            abs(mill_coord[0] - supplier_coord[0]) > 0.05
            or abs(mill_coord[1] - supplier_coord[1]) > 0.05
            or "CONFLICT" in status
        )
        evidence = "match_status=MATCHED_BOTH"
        if conflict:
            evidence += ";coordinate_conflict_mills_preferred"
        return (
            mill_coord[0],
            mill_coord[1],
            "mills_db",
            "medium" if conflict else "high",
            evidence,
        )
    if mill_coord is not None:
        return mill_coord[0], mill_coord[1], "mills_db", "high", f"match_status={status}"
    if supplier_coord is not None:
        return (
            supplier_coord[0],
            supplier_coord[1],
            "suppliers_dbf",
            "high",
            f"match_status={status}",
        )
    return None, None, "missing", "missing", f"match_status={status}"


def _direct_hubs(
    routes: Sequence[Mapping[str, str]],
    facility_ids: set[str],
) -> dict[str, list[tuple[str, float, str]]]:
    """Return direct facility <-> TL/W edges with distance and mode."""
    hubs: dict[str, list[tuple[str, float, str]]] = defaultdict(list)
    for row in routes:
        origins = [
            value.strip()
            for value in str(row.get("origin_list", "")).split(";")
            if value.strip()
        ]
        destinations = [
            value.strip()
            for value in str(row.get("destination_list", "")).split(";")
            if value.strip()
        ]
        if len(origins) != len(destinations):
            raise FacilityError("route origin/destination lists are not parallel")
        distance = _number(row.get("total_length", ""), "route total_length", default=0.0)
        if distance <= 0.0:
            continue
        mode = str(row.get("transport_mode", "")).strip().upper()
        for origin, destination in zip(origins, destinations):
            if origin in facility_ids and destination.startswith(("TL", "W")):
                hubs[origin].append((destination, distance, mode))
            if destination in facility_ids and origin.startswith(("TL", "W")):
                hubs[destination].append((origin, distance, mode))
    return hubs


def _hub_anchor_coordinates(
    geocoded: Mapping[str, tuple[float, float, str]],
    hubs_by_facility: Mapping[str, Sequence[tuple[str, float, str]]],
) -> dict[str, dict[str, Any]]:
    """Derive a TL/W hub coordinate from geocoded facilities attached to it."""
    attached: dict[str, dict[str, tuple[float, float]]] = defaultdict(dict)
    for facility_id, (lat, lon, _source) in geocoded.items():
        for hub, _distance, _mode in hubs_by_facility.get(facility_id, ()):
            attached[hub][facility_id] = (lat, lon)
    anchors: dict[str, dict[str, Any]] = {}
    for hub, facilities in sorted(attached.items()):
        lats = sorted(value[0] for value in facilities.values())
        lons = sorted(value[1] for value in facilities.values())
        if len(lats) % 2 == 1:
            lat = lats[len(lats) // 2]
            lon = lons[len(lons) // 2]
        else:
            lat = (lats[len(lats) // 2 - 1] + lats[len(lats) // 2]) / 2.0
            lon = (lons[len(lons) // 2 - 1] + lons[len(lons) // 2]) / 2.0
        anchors[hub] = {
            "hub": hub,
            "lat": lat,
            "lon": lon,
            "geocoded_facility_count": len(facilities),
            "geocoded_facilities": ";".join(sorted(facilities)),
            "method": "median_of_directly_attached_geocoded_facilities",
        }
    return anchors


def build_facility_table(repo_root: Path) -> FacilityTable:
    """Build one row per BC facility with coordinates and normalized capacity."""
    repo_root = Path(repo_root)
    facilities_path = repo_root / "metadata/ffcm-archive/parsed/facilities.csv"
    lines_path = repo_root / "metadata/ffcm-archive/parsed/processing_lines.csv"
    geocoding_path = repo_root / "metadata/facility_geocoding.csv"
    routes_path = repo_root / "metadata/ffcm-archive/parsed/routes.csv"
    facility_rows = _read_csv(facilities_path)
    _require_columns(facilities_path, facility_rows, {"facility", "facility_class"})
    line_rows = _read_csv(lines_path)
    _require_columns(
        lines_path,
        line_rows,
        {"processing_line", "facility", "primary_product", "infeed_rate", "infeed_unit"},
    )
    geocoding_rows = _read_csv(geocoding_path)
    _require_columns(
        geocoding_path,
        geocoding_rows,
        {
            "fcm_facility",
            "fcm_province",
            "mills_db_latitude",
            "mills_db_longitude",
            "suppliers_lat",
            "suppliers_long",
            "match_status",
        },
    )
    routes = _read_csv(routes_path)

    bc_rows = [
        row
        for row in facility_rows
        if str(row["facility"]).strip().startswith("BC-")
        and str(row["facility_class"]).strip().upper() in BC_FACILITY_CLASSES
    ]
    if not bc_rows:
        raise FacilityError("no BC facilities found in facilities.csv")

    lines_by_facility: dict[str, list[dict[str, str]]] = defaultdict(list)
    for line in line_rows:
        facility_id = str(line.get("facility", "")).strip()
        if facility_id.startswith("BC-"):
            lines_by_facility[facility_id].append(line)

    geocoding = {str(row["fcm_facility"]).strip(): row for row in geocoding_rows}
    geocoded: dict[str, tuple[float, float, str]] = {}
    coord_rows: dict[str, tuple[float | None, float | None, str, str, str]] = {}
    for row in bc_rows:
        facility_id = str(row["facility"]).strip()
        coord = _coordinate_from_row(geocoding.get(facility_id, {}))
        coord_rows[facility_id] = coord
        if coord[2] != "missing" and coord[0] is not None and coord[1] is not None:
            geocoded[facility_id] = (coord[0], coord[1], coord[2])

    facility_ids = {str(row["facility"]).strip() for row in bc_rows}
    hubs_by_facility = _direct_hubs(routes, facility_ids)
    hub_anchors = _hub_anchor_coordinates(geocoded, hubs_by_facility)

    density_book = units.DensityBook()
    records: list[FacilityRecord] = []
    coverage: dict[str, int] = {source: 0 for source in COORD_SOURCES}
    coverage_in_scope: dict[str, int] = {source: 0 for source in COORD_SOURCES}
    confidence: dict[str, int] = defaultdict(int)
    missing: list[str] = []
    missing_in_scope: list[str] = []
    zero_capacity: list[str] = []

    for row in bc_rows:
        facility_id = str(row["facility"]).strip()
        facility_class = str(row["facility_class"]).strip().upper()
        stage_a_scope = facility_class in STAGE_A_FACILITY_CLASSES
        lat, lon, coord_source, coord_confidence, coord_evidence = coord_rows[facility_id]
        if coord_source == "missing":
            candidates = [
                (distance, hub)
                for hub, distance, _mode in hubs_by_facility.get(facility_id, ())
                if hub in hub_anchors
            ]
            if candidates:
                distance, hub = min(candidates)
                anchor = hub_anchors[hub]
                lat = float(anchor["lat"])
                lon = float(anchor["lon"])
                coord_source = "tl_anchor_proxy"
                coord_confidence = COORD_CONFIDENCE[coord_source]
                coord_evidence = (
                    f"hub={hub};hub_anchor_n={anchor['geocoded_facility_count']};"
                    f"hub_anchor_method={anchor['method']};direct_route_km={distance:.3f}"
                )
        nameplate, capacity_basis = _nameplate_and_basis(
            lines_by_facility.get(facility_id, ()), density_book=density_book
        )
        capacity_status = "positive" if nameplate > 0.0 else "zero_or_blank"
        if capacity_status != "positive":
            zero_capacity.append(facility_id)
        line_ids = tuple(
            sorted(
                str(line.get("processing_line", "")).strip()
                for line in lines_by_facility.get(facility_id, ())
                if str(line.get("processing_line", "")).strip()
            )
        )
        record = FacilityRecord(
            facility_id=facility_id,
            facility_class=facility_class,
            name=facility_id,
            lat=lat,
            lon=lon,
            coord_source=coord_source,
            coord_confidence=coord_confidence,
            coord_evidence=coord_evidence,
            nameplate_m3_y=nameplate,
            capacity_status=capacity_status,
            capacity_basis=capacity_basis,
            line_count=len(line_ids),
            lines=line_ids,
            stage_a_scope=stage_a_scope,
        )
        records.append(record)
        coverage[coord_source] += 1
        confidence[coord_confidence] += 1
        if stage_a_scope:
            coverage_in_scope[coord_source] += 1
        if coord_source == "missing":
            missing.append(facility_id)
            if stage_a_scope:
                missing_in_scope.append(facility_id)

    return FacilityTable(
        records=tuple(sorted(records, key=lambda item: item.facility_id)),
        coverage=coverage,
        coverage_in_scope=coverage_in_scope,
        confidence=dict(confidence),
        missing=tuple(missing),
        missing_in_scope=tuple(missing_in_scope),
        anchor_hubs=tuple(hub_anchors[hub] for hub in sorted(hub_anchors)),
        zero_capacity=tuple(zero_capacity),
    )


def _load_prices(path: Path) -> dict[str, tuple[float, str]]:
    rows = _read_csv(path)
    _require_columns(path, rows, {"commodity", "price_value", "price_unit"})
    prices: dict[str, tuple[float, str]] = {}
    for row in rows:
        commodity = str(row["commodity"]).strip().lower()
        if not commodity:
            continue
        try:
            price = _number(row["price_value"], f"{commodity} price", default=0.0)
        except FacilityError:
            continue
        unit = str(row["price_unit"]).strip().lower().replace("cad", "$")
        if price <= 0.0 or not unit:
            continue
        prices[commodity] = (price, unit)
    if not prices:
        raise FacilityError(f"no positive commodity prices found in {path}")
    return prices


def _flow_totals(flows: Sequence[Mapping[str, str]], line_id: str) -> dict[str, float]:
    totals: dict[str, float] = {}
    for row in flows:
        if str(row.get("processing_line", "")).strip() != line_id:
            continue
        if str(row.get("flow_attribute", "")).strip() != "OutfeedRateByEntity":
            continue
        unit = str(row.get("unit", "")).strip().lower()
        value = _number(row.get("value", ""), "outfeed value", default=0.0)
        if value <= 0.0:
            continue
        totals[unit] = totals.get(unit, 0.0) + value
    return totals


def _normalize_processing_cost(
    line: Mapping[str, str],
    capacity_m3: float,
    outfeed: Mapping[str, float],
) -> tuple[float, str]:
    unit = str(line.get("operating_cost_unit", "")).strip().lower().replace("cad", "$")
    cost = _number(line.get("operating_cost", ""), "operating_cost", default=0.0)
    if cost < 0.0:
        return 0.0, "rejected_negative_operating_cost_used_zero"
    if unit in {"$/m3", "$/m3/y"}:
        return cost, "archive_dollar_per_m3"
    if unit in {"$/odt", "$/odt/y"}:
        outfeed_odt = outfeed.get("odt/y", 0.0)
        if outfeed_odt > 0.0:
            return cost * outfeed_odt / capacity_m3, "outfeed_odt_ratio"
        return cost * units.DEFAULT_BASIC_DENSITY_ODT_M3, "ledger_density_fallback"
    if unit in {"$/mbf", "$/mbf/y"}:
        outfeed_mbf = outfeed.get("mbf/y", 0.0)
        if outfeed_mbf <= 0.0:
            return 0.0, "rejected_missing_mbf_outfeed_used_zero"
        return cost * outfeed_mbf / capacity_m3, "outfeed_mbf_ratio"
    if unit in {"$/kwh", "$/kwh/y"}:
        outfeed_kwh = outfeed.get("kwh/y", 0.0)
        if outfeed_kwh <= 0.0:
            return 0.0, "rejected_missing_kwh_outfeed_used_zero"
        return cost * outfeed_kwh / capacity_m3, "outfeed_kwh_ratio"
    if unit in {"$/admt", "$/admt/y"}:
        outfeed_admt = outfeed.get("admt/y", 0.0)
        if outfeed_admt <= 0.0:
            return 0.0, "rejected_missing_admt_outfeed_used_zero"
        return cost * outfeed_admt / capacity_m3, "outfeed_admt_ratio"
    if unit in {"$/t", "$/t/y"}:
        outfeed_t = outfeed.get("t/y", 0.0)
        if outfeed_t <= 0.0:
            return 0.0, "rejected_missing_tonne_outfeed_used_zero"
        return cost * outfeed_t / capacity_m3, "outfeed_tonne_ratio"
    return 0.0, f"rejected_operating_cost_unit_{_identifier(unit)}_used_zero"


def _flow_quantity_in_price_unit(
    quantity: float,
    flow_unit: str,
    price_unit: str,
    density_odt_m3: float,
) -> float | None:
    flow_unit = flow_unit.strip().lower()
    price_unit = price_unit.strip().lower().replace("cad", "$")
    if price_unit in {"$/t", "$/tonne"}:
        if flow_unit in {"odt/y", "admt/y", "t/y", "bdt/y"}:
            return quantity
        if flow_unit == "kg/y":
            return quantity / 1000.0
        if flow_unit == "m3/y":
            return quantity * density_odt_m3
        return None
    if price_unit in {"$/mbf", "$/mbf/y"}:
        return quantity if flow_unit == "mbf/y" else None
    if price_unit in {"$/mwh", "$/mwh/y"}:
        if flow_unit == "kwh/y":
            return quantity / 1000.0
        if flow_unit == "mwh/y":
            return quantity
        return None
    if price_unit in {"$/m3", "$/m3/y"}:
        return quantity if flow_unit == "m3/y" else None
    if price_unit in {"$/kg", "$/kg/y"}:
        return quantity if flow_unit == "kg/y" else None
    if price_unit in {"$/gj", "$/gj/y"}:
        return quantity if flow_unit == "gj/y" else None
    return None


def _normalize_product_revenue(
    line: Mapping[str, Any],
    capacity_m3: float,
    prices: Mapping[str, tuple[float, str]],
    density_odt_m3: float,
) -> tuple[float, str]:
    revenue = 0.0
    included: list[str] = []
    mass_basis_flag = False
    for row in line.get("_outfeed_rows", ()):  # type: ignore[arg-type]
        commodity = str(row.get("commodity", "")).strip().lower()
        if commodity in {"internally_used_woodfuel", ""}:
            continue
        price = prices.get(commodity)
        if price is None:
            continue
        price_value, price_unit = price
        quantity = _number(row.get("value", ""), "outfeed value", default=0.0)
        flow_unit = str(row.get("unit", "")).strip().lower()
        converted = _flow_quantity_in_price_unit(
            quantity, flow_unit, price_unit, density_odt_m3
        )
        if converted is None or converted <= 0.0:
            continue
        revenue += price_value * converted
        included.append(f"{commodity}:{flow_unit}->{price_unit}")
        if price_unit in {"$/t", "$/tonne"} and flow_unit in {"odt/y", "admt/y"}:
            mass_basis_flag = True
    if not included:
        return 0.0, "no_priced_outfeed"
    basis = "outfeed_price_mix:" + ";".join(sorted(set(included)))
    if mass_basis_flag:
        basis += ";mass_basis_odt_or_admt_treated_as_tonne"
    return revenue / capacity_m3, basis


def build_facility_capacities(
    repo_root: Path,
    facility_table: FacilityTable,
    *,
    period: str,
) -> tuple[tuple[FacilityCapacity, ...], list[dict[str, Any]]]:
    """Build LP facility offers from the parsed processing lines.

    The nameplate comes from :class:`FacilityTable` so the CSV audit table and
    the LP cannot disagree about facility capacity.
    """
    repo_root = Path(repo_root)
    lines_path = repo_root / "metadata/ffcm-archive/parsed/processing_lines.csv"
    flows_path = repo_root / "metadata/ffcm-archive/parsed/processing_line_flows.csv"
    prices_path = repo_root / "metadata/ffcm-archive/parsed/commodities.csv"
    line_rows = _read_csv(lines_path)
    _require_columns(
        lines_path,
        line_rows,
        {
            "processing_line",
            "facility",
            "feedstock",
            "primary_product",
            "infeed_rate",
            "infeed_unit",
            "operating_cost",
            "operating_cost_unit",
        },
    )
    flow_rows = _read_csv(flows_path)
    _require_columns(
        flows_path,
        flow_rows,
        {"processing_line", "flow_attribute", "commodity", "value", "unit"},
    )
    prices = _load_prices(prices_path)
    density_book = units.DensityBook()

    records = {record.facility_id: record for record in facility_table.records}
    line_screen: list[dict[str, Any]] = []
    lines_by_facility: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in line_rows:
        facility_id = str(row["facility"]).strip()
        record = records.get(facility_id)
        if record is None or not record.stage_a_scope:
            continue
        line_id = str(row["processing_line"]).strip()
        capacity, unit, capacity_basis = normalize_line_capacity(
            row, density_book=density_book
        )
        if capacity is None:
            line_screen.append(
                {
                    "facility_id": facility_id,
                    "line_id": line_id,
                    "facility_class": record.facility_class,
                    "feedstock": str(row["feedstock"]).strip().lower(),
                    "primary_product": str(row["primary_product"]).strip().lower(),
                    "capacity_status": "rejected",
                    "capacity_basis": capacity_basis,
                    "infeed_unit": unit,
                    "infeed_rate": row.get("infeed_rate", ""),
                    "reason": capacity_basis,
                }
            )
            continue
        outfeed = _flow_totals(flow_rows, line_id)
        processing, cost_basis = _normalize_processing_cost(row, capacity, outfeed)
        outfeed_rows = [
            flow
            for flow in flow_rows
            if str(flow.get("processing_line", "")).strip() == line_id
            and str(flow.get("flow_attribute", "")).strip() == "OutfeedRateByEntity"
        ]
        line_with_flows = dict(row)
        line_with_flows["_outfeed_rows"] = outfeed_rows  # type: ignore[assignment]
        density = (
            units.DEFAULT_BASIC_DENSITY_ODT_M3
            if str(row["feedstock"]).strip().lower() == "logging_residue"
            else 0.40
        )
        revenue, revenue_basis = _normalize_product_revenue(
            line_with_flows, capacity, prices, density
        )
        record_line = {
            "line_id": line_id,
            "facility_id": facility_id,
            "facility_class": record.facility_class,
            "feedstock": str(row["feedstock"]).strip().lower(),
            "primary_product": str(row["primary_product"]).strip().lower(),
            "capacity_m3_y": capacity,
            "capacity_basis": capacity_basis,
            "processing_cost_cad_m3": processing,
            "cost_basis": cost_basis,
            "product_revenue_cad_m3": revenue,
            "revenue_basis": revenue_basis,
            "outfeed": outfeed,
        }
        lines_by_facility[facility_id].append(record_line)
        line_screen.append(
            {
                "facility_id": facility_id,
                "line_id": line_id,
                "facility_class": record.facility_class,
                "feedstock": record_line["feedstock"],
                "primary_product": record_line["primary_product"],
                "capacity_status": "accepted",
                "capacity_basis": capacity_basis,
                "infeed_unit": unit,
                "infeed_rate": row.get("infeed_rate", ""),
                "capacity_m3_y": capacity,
                "cost_basis": cost_basis,
                "revenue_basis": revenue_basis,
                "reason": "",
            }
        )

    facilities: list[FacilityCapacity] = []
    for facility_id, facility_lines in sorted(lines_by_facility.items()):
        record = records[facility_id]
        product_capacity: dict[str, float] = {}
        for line in facility_lines:
            product = line["primary_product"]
            product_capacity[product] = max(
                product_capacity.get(product, 0.0), line["capacity_m3_y"]
            )
        nameplate = record.nameplate_m3_y or 0.0
        if nameplate <= 0.0:
            continue
        candidate_grades: set[str] = set()
        for line in facility_lines:
            candidate_grades.update(
                accepted_grades_for(record.facility_class, line["feedstock"])
            )
        offers: list[FacilityOffer] = []
        for grade_id in sorted(candidate_grades):
            by_product: dict[str, list[dict[str, Any]]] = {}
            for line in facility_lines:
                if grade_id in accepted_grades_for(
                    record.facility_class, line["feedstock"]
                ):
                    by_product.setdefault(line["primary_product"], []).append(line)
            if not by_product:
                continue
            representatives: list[dict[str, Any]] = []
            for product_lines in by_product.values():
                representatives.append(
                    max(product_lines, key=lambda item: item["capacity_m3_y"])
                )
            grade_capacity = sum(line["capacity_m3_y"] for line in representatives)
            if grade_capacity <= 0.0:
                continue
            total_capacity = sum(line["capacity_m3_y"] for line in representatives)
            processing = (
                sum(
                    line["processing_cost_cad_m3"] * line["capacity_m3_y"]
                    for line in representatives
                )
                / total_capacity
            )
            revenue = (
                sum(
                    line["product_revenue_cad_m3"] * line["capacity_m3_y"]
                    for line in representatives
                )
                / total_capacity
            )
            primary_product = max(
                by_product,
                key=lambda product: max(
                    line["capacity_m3_y"] for line in by_product[product]
                ),
            )
            capacity_basis = ";".join(
                sorted({line["capacity_basis"] for line in representatives})
            )
            cost_basis = ";".join(
                sorted({line["cost_basis"] for line in representatives})
            )
            revenue_basis = ";".join(
                sorted({line["revenue_basis"] for line in representatives})
            )
            offers.append(
                FacilityOffer(
                    offer_id=f"{facility_id}::{grade_id}",
                    period=period,
                    facility_id=facility_id,
                    facility_name=record.name,
                    facility_class=record.facility_class,
                    grade_id=grade_id,
                    primary_product=primary_product,
                    capacity_m3_y=grade_capacity,
                    processing_cost_cad_m3=processing,
                    product_revenue_cad_m3=revenue,
                    capacity_basis=capacity_basis,
                    cost_basis=cost_basis,
                    revenue_basis=revenue_basis,
                    evidence=(
                        "parsed_ffcm_processing_lines;"
                        "capacity=normalized_line_infeed;"
                        "cost_and_revenue=line_outfeed_ratios"
                    ),
                )
            )
        if not offers:
            continue
        facilities.append(
            FacilityCapacity(
                facility_id=facility_id,
                facility_name=record.name,
                facility_class=record.facility_class,
                nameplate_m3_y=nameplate,
                offers=tuple(offers),
                evidence=(
                    "parsed_ffcm_processing_lines;"
                    "nameplate=max_per_product;"
                    f"coordinates={record.coord_source}"
                ),
            )
        )
    if not facilities:
        raise FacilityError(
            "no BC facility could be normalized to a supported capacity basis"
        )
    return tuple(facilities), line_screen
