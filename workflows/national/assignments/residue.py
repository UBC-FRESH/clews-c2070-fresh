"""Sawmill residue supply and recipient demand for the Stage B pilot.

The module is a parsed boundary over the FFCM processing-line archive.  Every
ratio is taken from a declared outfeed/infeed pair, every unit crossing goes
through :mod:`workflows.national.assignments.units`, and a missing ratio is
reported and excluded rather than replaced by an assumed value.

Residue types
-------------
``chips``
    ``woodchips`` outfeed of a sawmill line.  Consumed by PAP.
``sawdust_shavings``
    ``sawdust_shavings`` outfeed.  Consumed by PEL.
``hogfuel``
    ``hogfuel`` outfeed (hogfuel/bark).  Consumed by BIO.
``other``
    ``internally_used_woodfuel`` outfeed.  Retained on site; reported as
    generated but not allocatable in this pilot.

The FFCM outfeed values are annual rates for the line's base-case infeed.  The
pilot applies the declared ratio linearly to the Stage A log volume assigned to
the same facility.  This is a constant-returns-to-scale assumption and is
recorded in every supply row.
"""

from __future__ import annotations

import csv
import math
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from . import units

__all__ = [
    "DENSITY_ODT_M3",
    "RESIDUE_TYPES",
    "CommodityPrice",
    "FacilityLocation",
    "ProcessingArchive",
    "ResidueDemandRow",
    "ResidueError",
    "ResidueSupplyRow",
    "SawmillAssignment",
    "SupplyBuildResult",
    "build_residue_demand_candidates",
    "build_residue_supply",
    "class_for_residue_type",
    "compute_line_demand",
    "compute_line_residue_supply",
    "eligible_residue_types",
    "load_facility_locations",
    "load_processing_archive",
    "load_sawmill_assignments",
    "product_revenue_per_input_odt",
]

DENSITY_ODT_M3 = units.DEFAULT_BASIC_DENSITY_ODT_M3
DENSITY_SOURCE = units.DEFAULT_DENSITY_SOURCE
DEFAULT_MOISTURE_WET_BASIS = 0.45
MOISTURE_SOURCE = "stage_a_grades.csv:RESIDUE/HOGFUEL moisture_wet_basis=0.45"

RESIDUE_TYPES = ("chips", "sawdust_shavings", "hogfuel", "other")
RESIDUE_TYPE_LABELS = {
    "chips": "chips",
    "sawdust_shavings": "sawdust/shavings",
    "hogfuel": "hogfuel/bark",
    "other": "other (internally used woodfuel)",
}
FFCM_COMMODITY_BY_RESIDUE = {
    "chips": "woodchips",
    "sawdust_shavings": "sawdust_shavings",
    "hogfuel": "hogfuel",
    "other": "internally_used_woodfuel",
}
RESIDUE_TYPE_BY_FEEDSTOCK = {
    "woodchips": "chips",
    "sawdust_shavings": "sawdust_shavings",
    "hogfuel": "hogfuel",
}
CONSUMER_CLASS_BY_RESIDUE = {
    "chips": "PAP",
    "sawdust_shavings": "PEL",
    "hogfuel": "BIO",
}
CONSUMER_FEEDSTOCK_BY_CLASS = {
    "PAP": "woodchips",
    "PEL": "sawdust_shavings",
    "BIO": "hogfuel",
}
ALLOCATABLE_RESIDUE_TYPES = frozenset(CONSUMER_CLASS_BY_RESIDUE)


class ResidueError(RuntimeError):
    """Raised when a residue ratio, unit, or reference cannot be trusted."""


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise ResidueError(f"required stage B input is missing: {path}")
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ResidueError(f"stage B input has no data rows: {path}")
    return rows


def _require_columns(
    path: Path, rows: Sequence[Mapping[str, str]], required: Iterable[str]
) -> None:
    missing = set(required) - set(rows[0])
    if missing:
        raise ResidueError(f"{path} is missing required columns: {sorted(missing)}")


def _identifier(value: object, name: str) -> str:
    text = str(value).strip()
    if not text:
        raise ResidueError(f"{name} is empty")
    return text


def _number(
    value: object,
    name: str,
    *,
    minimum: float | None = None,
    allow_missing: bool = False,
) -> float:
    text = str(value).strip()
    if text == "" or text.lower() in {"na", "nan", "none"}:
        if allow_missing:
            return 0.0
        raise ResidueError(f"{name} is missing")
    try:
        number = float(text)
    except ValueError as error:
        raise ResidueError(f"{name} is not numeric: {value!r}") from error
    if not math.isfinite(number):
        raise ResidueError(f"{name} is not finite: {value!r}")
    if minimum is not None and number < minimum:
        raise ResidueError(f"{name} must be >= {minimum}: {value!r}")
    return number


def _annual_unit(unit_text: object) -> str:
    """Return a unit token without the annual ``/y`` suffix."""
    text = str(unit_text).strip().lower().replace("cad", "$").replace(" ", "")
    return text.removesuffix("/y")


def _cost_unit_base(unit_text: object) -> str:
    text = _annual_unit(unit_text)
    if text.startswith("$/"):
        return text[2:]
    return text


def _conversion_context(
    *, density_odt_m3: float = DENSITY_ODT_M3, allow_archive_tonne: bool = False
) -> units.ConversionContext:
    return units.ConversionContext(
        basic_density_odt_m3=density_odt_m3,
        density_source=DENSITY_SOURCE,
        allow_archive_tonne=allow_archive_tonne,
    )


def _canonical_flow_unit(unit_text: object, *, name: str) -> str:
    base = _annual_unit(unit_text)
    try:
        return units.canonical_unit(base)
    except units.UnitError as error:
        raise ResidueError(f"{name} has unsupported unit {unit_text!r}: {error}") from error


def _to_odt(
    value: float,
    unit_text: object,
    *,
    name: str,
    density_odt_m3: float = DENSITY_ODT_M3,
    allow_archive_tonne: bool = False,
) -> float:
    base = _canonical_flow_unit(unit_text, name=name)
    try:
        return units.to_odt(
            value,
            base,
            context=_conversion_context(
                density_odt_m3=density_odt_m3,
                allow_archive_tonne=allow_archive_tonne,
            ),
        )
    except units.UnitError as error:
        raise ResidueError(f"{name} cannot be converted to odt: {error}") from error


def _to_m3_from_odt(odt: float, *, density_odt_m3: float = DENSITY_ODT_M3) -> float:
    return units.convert(
        odt,
        "odt",
        "m3_green",
        context=_conversion_context(density_odt_m3=density_odt_m3),
    )


def _to_infeed_unit(
    volume_m3: float, infeed_unit: object, *, density_odt_m3: float = DENSITY_ODT_M3
) -> float:
    base = _canonical_flow_unit(infeed_unit, name="infeed_unit")
    return units.convert(
        volume_m3,
        "m3_green",
        base,
        context=_conversion_context(density_odt_m3=density_odt_m3),
    )


def class_for_residue_type(residue_type: str) -> str:
    """Return the only facility class allowed to consume a residue type."""
    try:
        return CONSUMER_CLASS_BY_RESIDUE[residue_type]
    except KeyError as error:
        raise ResidueError(
            f"residue type {residue_type!r} has no consumer class"
        ) from error


def eligible_residue_types(facility_class: str) -> frozenset[str]:
    """Return the residue types a facility class may accept."""
    normalized = str(facility_class).strip().upper()
    accepted = {
        residue_type
        for residue_type, consumer_class in CONSUMER_CLASS_BY_RESIDUE.items()
        if consumer_class == normalized
    }
    return frozenset(accepted)


@dataclass(frozen=True, slots=True)
class CommodityPrice:
    commodity: str
    value: float
    unit: str
    unit_type: str
    energy_density: float | None
    energy_density_unit: str


@dataclass(frozen=True, slots=True)
class ProcessingArchive:
    lines_by_facility: Mapping[str, tuple[Mapping[str, str], ...]]
    flows_by_line: Mapping[str, tuple[Mapping[str, str], ...]]
    prices: Mapping[str, CommodityPrice]
    source_files: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class FacilityLocation:
    facility_id: str
    facility_class: str
    name: str
    lat: float | None
    lon: float | None
    coord_source: str
    coord_confidence: str

    @property
    def location_quality(self) -> str:
        source = self.coord_source.strip().lower()
        if source in {"mills_db_2015", "suppliers_dbf"}:
            return "defensible"
        if source == "tl_anchor_proxy":
            return "proxy"
        return "missing"


@dataclass(frozen=True, slots=True)
class SawmillAssignment:
    facility_id: str
    grade_id: str
    period: str
    volume_m3: float
    assignment_count: int


@dataclass(frozen=True, slots=True)
class ResidueSupplyRow:
    source_facility: str
    residue_type: str
    volume_m3: float
    odt: float
    moisture_wet_basis: float
    unit_basis: str
    evidence: str
    processing_line: str
    outfeed_commodity: str
    outfeed_value: float
    outfeed_unit: str
    infeed_rate: float
    infeed_unit: str
    ratio_odt_per_infeed_unit: float
    ratio_odt_per_m3_log: float
    stage_a_assigned_m3: float
    density_odt_m3: float
    allocatable: bool
    allocation_note: str

    def to_row(self) -> dict[str, Any]:
        return {
            "source_facility": self.source_facility,
            "residue_type": self.residue_type,
            "volume_m3": self.volume_m3,
            "odt": self.odt,
            "moisture_wet_basis": self.moisture_wet_basis,
            "unit_basis": self.unit_basis,
            "evidence": self.evidence,
            "processing_line": self.processing_line,
            "outfeed_commodity": self.outfeed_commodity,
            "outfeed_value": self.outfeed_value,
            "outfeed_unit": self.outfeed_unit,
            "infeed_rate": self.infeed_rate,
            "infeed_unit": self.infeed_unit,
            "ratio_odt_per_infeed_unit": self.ratio_odt_per_infeed_unit,
            "ratio_odt_per_m3_log": self.ratio_odt_per_m3_log,
            "stage_a_assigned_m3": self.stage_a_assigned_m3,
            "density_odt_m3": self.density_odt_m3,
            "allocatable": "yes" if self.allocatable else "no",
            "allocation_note": self.allocation_note,
        }


@dataclass(frozen=True, slots=True)
class ResidueDemandRow:
    recipient_facility: str
    facility_class: str
    processing_line: str
    accepted_residue_type: str
    capacity_odt_y: float
    capacity_m3_y: float
    capacity_basis: str
    operating_cost: float
    operating_cost_unit: str
    operating_cost_cad_per_input_odt: float
    product: str
    product_output_per_input_odt: float
    product_output_unit: str
    product_price: float
    product_price_unit: str
    product_revenue_cad_per_input_odt: float
    price_status: str
    coord_source: str
    coord_confidence: str
    location_quality: str
    evidence: str
    status: str
    status_reason: str

    def to_row(self) -> dict[str, Any]:
        return {
            "recipient_facility": self.recipient_facility,
            "facility_class": self.facility_class,
            "processing_line": self.processing_line,
            "accepted_residue_type": self.accepted_residue_type,
            "capacity_odt_y": self.capacity_odt_y,
            "capacity_m3_y": self.capacity_m3_y,
            "capacity_basis": self.capacity_basis,
            "operating_cost": self.operating_cost,
            "operating_cost_unit": self.operating_cost_unit,
            "operating_cost_cad_per_input_odt": (
                self.operating_cost_cad_per_input_odt
            ),
            "product": self.product,
            "product_output_per_input_odt": self.product_output_per_input_odt,
            "product_output_unit": self.product_output_unit,
            "product_price": self.product_price,
            "product_price_unit": self.product_price_unit,
            "product_revenue_cad_per_input_odt": (
                self.product_revenue_cad_per_input_odt
            ),
            "price_status": self.price_status,
            "coord_source": self.coord_source,
            "coord_confidence": self.coord_confidence,
            "location_quality": self.location_quality,
            "evidence": self.evidence,
            "status": self.status,
            "status_reason": self.status_reason,
        }


@dataclass(frozen=True, slots=True)
class SupplyBuildResult:
    supplies: tuple[ResidueSupplyRow, ...]
    coverage: tuple[dict[str, Any], ...]
    conversions: tuple[dict[str, Any], ...]
    excluded: tuple[dict[str, Any], ...]


def _load_prices(path: Path) -> dict[str, CommodityPrice]:
    rows = _read_csv(path)
    _require_columns(path, rows, {"commodity", "price_value", "price_unit"})
    prices: dict[str, CommodityPrice] = {}
    for row in rows:
        commodity = str(row["commodity"]).strip().lower()
        if not commodity:
            continue
        value_text = str(row.get("price_value", "")).strip()
        if value_text == "":
            continue
        value = _number(value_text, f"{commodity} price")
        unit = str(row.get("price_unit", "")).strip().lower().replace("cad", "$")
        if not unit:
            continue
        energy_density: float | None = None
        density_text = str(row.get("energy_density", "")).strip()
        if density_text not in {"", "nan", "none", "na"}:
            energy_density = _number(density_text, f"{commodity} energy density")
        prices[commodity] = CommodityPrice(
            commodity=commodity,
            value=value,
            unit=unit,
            unit_type=str(row.get("price_unit_type", "")).strip(),
            energy_density=energy_density,
            energy_density_unit=str(row.get("energy_density_unit", "")).strip(),
        )
    return prices


def load_processing_archive(repo_root: Path) -> ProcessingArchive:
    """Parse the FFCM processing lines, flows, and commodity prices."""
    repo_root = Path(repo_root)
    parsed = repo_root / "metadata/ffcm-archive/parsed"
    lines_path = parsed / "processing_lines.csv"
    flows_path = parsed / "processing_line_flows.csv"
    prices_path = parsed / "commodities.csv"
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
    lines_by_facility: dict[str, list[Mapping[str, str]]] = defaultdict(list)
    for row in line_rows:
        facility = _identifier(row["facility"], "processing line facility")
        lines_by_facility[facility].append(row)
    flows_by_line: dict[str, list[Mapping[str, str]]] = defaultdict(list)
    for row in flow_rows:
        line_id = _identifier(row["processing_line"], "flow processing line")
        flows_by_line[line_id].append(row)
    return ProcessingArchive(
        lines_by_facility=MappingProxyType(
            {key: tuple(value) for key, value in lines_by_facility.items()}
        ),
        flows_by_line=MappingProxyType(
            {key: tuple(value) for key, value in flows_by_line.items()}
        ),
        prices=MappingProxyType(_load_prices(prices_path)),
        source_files=MappingProxyType(
            {
                "processing_lines": str(lines_path),
                "processing_line_flows": str(flows_path),
                "commodities": str(prices_path),
            }
        ),
    )


def load_facility_locations(path: Path) -> tuple[FacilityLocation, ...]:
    """Read the Stage A facility table and parse coordinates as a boundary."""
    rows = _read_csv(path)
    _require_columns(
        path,
        rows,
        {"facility_id", "facility_class", "name", "lat", "lon", "coord_source"},
    )
    locations: list[FacilityLocation] = []
    for row in rows:
        lat_text = str(row.get("lat", "")).strip()
        lon_text = str(row.get("lon", "")).strip()
        lat = None if lat_text in {"", "nan", "none"} else _number(lat_text, "lat")
        lon = None if lon_text in {"", "nan", "none"} else _number(lon_text, "lon")
        if (lat is None) != (lon is None):
            raise ResidueError(
                f"facility {row['facility_id']} has only one coordinate"
            )
        locations.append(
            FacilityLocation(
                facility_id=_identifier(row["facility_id"], "facility_id"),
                facility_class=_identifier(
                    row["facility_class"], "facility_class"
                ).upper(),
                name=str(row.get("name", "")).strip(),
                lat=lat,
                lon=lon,
                coord_source=str(row.get("coord_source", "missing")).strip().lower(),
                coord_confidence=str(row.get("coord_confidence", "")).strip().lower(),
            )
        )
    return tuple(locations)


def load_sawmill_assignments(path: Path) -> tuple[SawmillAssignment, ...]:
    """Aggregate Stage A log assignments into sawmill volumes."""
    rows = _read_csv(path)
    _require_columns(
        path,
        rows,
        {"facility_id", "facility_class", "grade_id", "volume_m3", "period"},
    )
    grouped: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    for row in rows:
        facility_class = str(row["facility_class"]).strip().upper()
        if facility_class != "SAW":
            continue
        facility = _identifier(row["facility_id"], "facility_id")
        grade = _identifier(row["grade_id"], "grade_id").upper()
        period = _identifier(row["period"], "period")
        volume = _number(row["volume_m3"], f"{facility} volume_m3", minimum=0.0)
        if volume == 0.0:
            continue
        grouped[(facility, grade, period)].append(volume)
    if not grouped:
        raise ResidueError(f"no positive SAW assignments found in {path}")
    return tuple(
        SawmillAssignment(
            facility_id=facility,
            grade_id=grade,
            period=period,
            volume_m3=math.fsum(volumes),
            assignment_count=len(volumes),
        )
        for (facility, grade, period), volumes in sorted(grouped.items())
    )


def _find_single_outfeed(
    flows: Sequence[Mapping[str, str]], commodity: str, *, line_id: str
) -> Mapping[str, str] | None:
    matches = [
        row
        for row in flows
        if str(row.get("flow_attribute", "")).strip() == "OutfeedRateByEntity"
        and str(row.get("commodity", "")).strip().lower() == commodity
    ]
    if len(matches) > 1:
        raise ResidueError(
            f"{line_id} has {len(matches)} outfeed rows for {commodity!r}; "
            "refusing to double count"
        )
    return matches[0] if matches else None


def compute_line_residue_supply(
    line: Mapping[str, str],
    flows: Sequence[Mapping[str, str]],
    assigned_m3: float,
    *,
    density_odt_m3: float = DENSITY_ODT_M3,
    moisture_wet_basis: float = DEFAULT_MOISTURE_WET_BASIS,
) -> tuple[dict[str, ResidueSupplyRow], dict[str, str]]:
    """Return residue rows and per-type exclusion reasons for one SAW line.

    The returned mapping is keyed by residue type.  Types without a declared
    outfeed ratio are absent from the mapping and carry a reason in the second
    return value.
    """
    line_id = _identifier(line["processing_line"], "processing_line")
    infeed_rate = _number(line["infeed_rate"], f"{line_id} infeed_rate", minimum=0.0)
    if infeed_rate <= 0.0:
        raise ResidueError(f"{line_id} has a non-positive infeed rate")
    infeed_base = _canonical_flow_unit(line["infeed_unit"], name=f"{line_id} infeed")
    assigned_in_infeed = _to_infeed_unit(
        assigned_m3, infeed_base, density_odt_m3=density_odt_m3
    )

    rows: dict[str, ResidueSupplyRow] = {}
    reasons: dict[str, str] = {}
    for residue_type in RESIDUE_TYPES:
        commodity = FFCM_COMMODITY_BY_RESIDUE[residue_type]
        flow = _find_single_outfeed(flows, commodity, line_id=line_id)
        if flow is None:
            reasons[residue_type] = "missing_outfeed_ratio"
            continue
        outfeed_value = _number(
            flow["value"], f"{line_id} {commodity} value", minimum=0.0
        )
        if outfeed_value <= 0.0:
            reasons[residue_type] = "nonpositive_outfeed_ratio"
            continue
        outfeed_base = _canonical_flow_unit(
            flow["unit"], name=f"{line_id} {commodity} outfeed"
        )
        if outfeed_base == "admt":
            raise ResidueError(
                f"{line_id} {commodity} outfeed is admt; a declared air-dry "
                "moisture is required before conversion to odt"
            )
        outfeed_odt = _to_odt(
            outfeed_value,
            outfeed_base,
            name=f"{line_id} {commodity} outfeed",
            density_odt_m3=density_odt_m3,
        )
        ratio = outfeed_value / infeed_rate
        residue_odt = _to_odt(
            ratio * assigned_in_infeed,
            outfeed_base,
            name=f"{line_id} {commodity} assigned residue",
            density_odt_m3=density_odt_m3,
        )
        volume_m3 = _to_m3_from_odt(residue_odt, density_odt_m3=density_odt_m3)
        ratio_per_m3 = residue_odt / assigned_m3 if assigned_m3 > 0.0 else 0.0
        allocatable = residue_type in ALLOCATABLE_RESIDUE_TYPES
        allocation_note = (
            "allocatable to "
            f"{class_for_residue_type(residue_type)}"
            if allocatable
            else "internally_used_woodfuel; retained on site, no Stage B consumer"
        )
        rows[residue_type] = ResidueSupplyRow(
            source_facility=_identifier(line["facility"], "facility"),
            residue_type=residue_type,
            volume_m3=volume_m3,
            odt=residue_odt,
            moisture_wet_basis=moisture_wet_basis,
            unit_basis=(
                f"outfeed_ratio={ratio:.12g} {outfeed_base}/{infeed_base};"
                f"infeed={infeed_rate:.6f} {infeed_base}/y;"
                f"outfeed={outfeed_value:.6f} {outfeed_base}/y"
                f" ({outfeed_odt:.6f} odt/y);"
                f"density={density_odt_m3:.6f} odt/m3"
            ),
            evidence=(
                f"ffcm_processing_line={line_id};"
                f"sawmill_feedstock={line['feedstock']};"
                f"stage_a_assigned_m3={assigned_m3:.6f};"
                "constant_returns_to_scale_outfeed_ratio;"
                f"moisture={moisture_wet_basis:.2f} wet basis"
            ),
            processing_line=line_id,
            outfeed_commodity=commodity,
            outfeed_value=outfeed_value,
            outfeed_unit=outfeed_base,
            infeed_rate=infeed_rate,
            infeed_unit=infeed_base,
            ratio_odt_per_infeed_unit=ratio,
            ratio_odt_per_m3_log=ratio_per_m3,
            stage_a_assigned_m3=assigned_m3,
            density_odt_m3=density_odt_m3,
            allocatable=allocatable,
            allocation_note=allocation_note,
        )
    return rows, reasons


def build_residue_supply(
    archive: ProcessingArchive,
    assignments: Sequence[SawmillAssignment],
    *,
    density_odt_m3: float = DENSITY_ODT_M3,
) -> SupplyBuildResult:
    """Build one residue supply row per SAW facility/residue type."""
    if not assignments:
        raise ResidueError("no sawmill assignments supplied")
    supplies: list[ResidueSupplyRow] = []
    coverage: list[dict[str, Any]] = []
    conversions: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []

    volumes_by_facility: dict[str, list[SawmillAssignment]] = defaultdict(list)
    for assignment in assignments:
        volumes_by_facility[assignment.facility_id].append(assignment)

    for facility in sorted(volumes_by_facility):
        facility_assignments = volumes_by_facility[facility]
        assigned_m3 = math.fsum(item.volume_m3 for item in facility_assignments)
        lines = [
            line
            for line in archive.lines_by_facility.get(facility, ())
            if str(line.get("feedstock", "")).strip().lower() == "log"
        ]
        if len(lines) != 1:
            reason = (
                "no_sawmill_log_line"
                if not lines
                else f"multiple_sawmill_log_lines:{len(lines)}"
            )
            excluded.append(
                {
                    "source_facility": facility,
                    "reason": reason,
                    "stage_a_assigned_m3": assigned_m3,
                }
            )
            for residue_type in RESIDUE_TYPES:
                coverage.append(
                    {
                        "source_facility": facility,
                        "processing_line": "",
                        "residue_type": residue_type,
                        "status": "excluded",
                        "reason": reason,
                        "ratio_odt_per_infeed_unit": "",
                        "stage_a_assigned_m3": assigned_m3,
                    }
                )
            continue
        line = lines[0]
        line_id = _identifier(line["processing_line"], "processing_line")
        flows = archive.flows_by_line.get(line_id, ())
        try:
            rows, reasons = compute_line_residue_supply(
                line,
                flows,
                assigned_m3,
                density_odt_m3=density_odt_m3,
            )
        except ResidueError as error:
            excluded.append(
                {
                    "source_facility": facility,
                    "reason": f"line_rejected:{error}",
                    "stage_a_assigned_m3": assigned_m3,
                }
            )
            for residue_type in RESIDUE_TYPES:
                coverage.append(
                    {
                        "source_facility": facility,
                        "processing_line": line_id,
                        "residue_type": residue_type,
                        "status": "excluded",
                        "reason": f"line_rejected:{error}",
                        "ratio_odt_per_infeed_unit": "",
                        "stage_a_assigned_m3": assigned_m3,
                    }
                )
            continue
        for residue_type in RESIDUE_TYPES:
            row = rows.get(residue_type)
            if row is None:
                coverage.append(
                    {
                        "source_facility": facility,
                        "processing_line": line_id,
                        "residue_type": residue_type,
                        "status": "excluded",
                        "reason": reasons.get(residue_type, "missing_outfeed_ratio"),
                        "ratio_odt_per_infeed_unit": "",
                        "stage_a_assigned_m3": assigned_m3,
                    }
                )
                if residue_type in ALLOCATABLE_RESIDUE_TYPES:
                    excluded.append(
                        {
                            "source_facility": facility,
                            "reason": reasons.get(
                                residue_type, "missing_outfeed_ratio"
                            ),
                            "residue_type": residue_type,
                            "stage_a_assigned_m3": assigned_m3,
                        }
                    )
                continue
            supplies.append(row)
            coverage.append(
                {
                    "source_facility": facility,
                    "processing_line": line_id,
                    "residue_type": residue_type,
                    "status": "included",
                    "reason": "",
                    "ratio_odt_per_infeed_unit": row.ratio_odt_per_infeed_unit,
                    "stage_a_assigned_m3": assigned_m3,
                }
            )
            conversions.append(
                {
                    "source_facility": facility,
                    "residue_type": residue_type,
                    "processing_line": line_id,
                    "infeed_rate": row.infeed_rate,
                    "infeed_unit": row.infeed_unit,
                    "outfeed_commodity": row.outfeed_commodity,
                    "outfeed_value": row.outfeed_value,
                    "outfeed_unit": row.outfeed_unit,
                    "ratio_odt_per_infeed_unit": row.ratio_odt_per_infeed_unit,
                    "stage_a_assigned_m3": assigned_m3,
                    "residue_odt": row.odt,
                    "density_odt_m3": row.density_odt_m3,
                    "residue_m3": row.volume_m3,
                    "conversion_chain": (
                        f"{row.outfeed_value:.6f} {row.outfeed_unit}/y / "
                        f"{row.infeed_rate:.6f} {row.infeed_unit}/y = "
                        f"{row.ratio_odt_per_infeed_unit:.12g} "
                        f"{row.outfeed_unit}/{row.infeed_unit}; "
                        f"{assigned_m3:.6f} m3 x ratio = {row.odt:.6f} odt; "
                        f"{row.odt:.6f} odt / {row.density_odt_m3:.6f} odt/m3 = "
                        f"{row.volume_m3:.6f} m3"
                    ),
                }
            )
    if not supplies:
        raise ResidueError("no residue supply row survived the outfeed screen")
    return SupplyBuildResult(
        supplies=tuple(supplies),
        coverage=tuple(coverage),
        conversions=tuple(conversions),
        excluded=tuple(excluded),
    )


def _price_quantity(
    quantity: float, output_unit: str, price_unit: str
) -> tuple[float, str] | None:
    """Express ``quantity`` in the price unit, or return None if impossible."""
    output = _annual_unit(output_unit)
    price = _annual_unit(price_unit)
    mass_output = output in {"odt", "admt", "bdmt", "t"}
    mass_price = price in {"$/t", "$/tonne", "$/odt", "$/admt", "$/bdmt"}
    if mass_price and mass_output:
        return quantity, "ffcm_tonne_basis_equals_declared_product_mass_unit"
    if price == "$/mwh" and output == "kwh":
        return quantity / 1000.0, "kwh_to_mwh_si_1e-3"
    if price == "$/mwh" and output == "mwh":
        return quantity, "identity"
    if price == "$/kwh" and output == "kwh":
        return quantity, "identity"
    if price == "$/gj" and output == "gj":
        return quantity, "identity"
    if price == "$/gj" and output == "mwh":
        return quantity * 3.6, "mwh_to_gj_si_3.6"
    if price == "$/m3" and output == "m3_green":
        return quantity, "identity"
    if price == "$/m3" and output == "m3":
        return quantity, "identity"
    if price == "$/mbf" and output == "mbf":
        return quantity, "identity"
    if price == "$/kg" and output == "kg":
        return quantity, "identity"
    return None


def product_revenue_per_input_odt(
    output_per_input_odt: float,
    output_unit: str,
    price_value: float,
    price_unit: str,
) -> tuple[float, str]:
    """Return product revenue per input ODT and the price-basis note."""
    expressed = _price_quantity(output_per_input_odt, output_unit, price_unit)
    if expressed is None:
        raise ResidueError(
            f"price unit {price_unit!r} cannot express product unit "
            f"{output_unit!r}"
        )
    quantity, basis = expressed
    return price_value * quantity, basis


def _product_ratio(
    line: Mapping[str, str],
    flows: Sequence[Mapping[str, str]],
    product: str,
    *,
    density_odt_m3: float,
) -> tuple[float, str, float]:
    """Return (output per input odt, output unit, capacity odt/y)."""
    line_id = _identifier(line["processing_line"], "processing_line")
    infeed_rate = _number(line["infeed_rate"], f"{line_id} infeed_rate", minimum=0.0)
    if infeed_rate <= 0.0:
        raise ResidueError(f"{line_id} has a non-positive infeed rate")
    infeed_base = _canonical_flow_unit(line["infeed_unit"], name=f"{line_id} infeed")
    capacity_odt = _to_odt(
        infeed_rate,
        infeed_base,
        name=f"{line_id} infeed",
        density_odt_m3=density_odt_m3,
    )
    outfeed = _find_single_outfeed(flows, product, line_id=line_id)
    if outfeed is None:
        raise ResidueError(f"{line_id} has no outfeed row for product {product!r}")
    outfeed_value = _number(
        outfeed["value"], f"{line_id} {product} value", minimum=0.0
    )
    if outfeed_value <= 0.0:
        raise ResidueError(f"{line_id} has no positive {product!r} outfeed")
    outfeed_base = _canonical_flow_unit(
        outfeed["unit"], name=f"{line_id} {product} outfeed"
    )
    odt_per_infeed = units.convert(
        1.0,
        "odt",
        infeed_base,
        context=_conversion_context(density_odt_m3=density_odt_m3),
    )
    output_per_input_odt = (outfeed_value / infeed_rate) * odt_per_infeed
    return output_per_input_odt, outfeed_base, capacity_odt


def compute_line_demand(
    line: Mapping[str, str],
    flows: Sequence[Mapping[str, str]],
    facility: FacilityLocation,
    price: CommodityPrice | None,
    *,
    density_odt_m3: float = DENSITY_ODT_M3,
) -> ResidueDemandRow | None:
    """Build one demand row for a residue-consuming processing line.

    Returns ``None`` when the line feedstock is not a Stage B residue stream.
    Unit or cost mismatches are returned as an excluded row rather than raised,
    so the screen table records why a recipient was dropped.
    """
    feedstock = str(line.get("feedstock", "")).strip().lower()
    residue_type = RESIDUE_TYPE_BY_FEEDSTOCK.get(feedstock)
    if residue_type is None:
        return None
    line_id = _identifier(line["processing_line"], "processing_line")
    product = _identifier(line["primary_product"], "primary_product").lower()
    location_quality = facility.location_quality

    def excluded(reason: str) -> ResidueDemandRow:
        return ResidueDemandRow(
            recipient_facility=facility.facility_id,
            facility_class=facility.facility_class,
            processing_line=line_id,
            accepted_residue_type=residue_type,
            capacity_odt_y=0.0,
            capacity_m3_y=0.0,
            capacity_basis="rejected",
            operating_cost=0.0,
            operating_cost_unit=str(line.get("operating_cost_unit", "")),
            operating_cost_cad_per_input_odt=0.0,
            product=product,
            product_output_per_input_odt=0.0,
            product_output_unit="",
            product_price=0.0,
            product_price_unit="",
            product_revenue_cad_per_input_odt=0.0,
            price_status="unavailable",
            coord_source=facility.coord_source,
            coord_confidence=facility.coord_confidence,
            location_quality=location_quality,
            evidence=f"ffcm_processing_line={line_id}",
            status="excluded",
            status_reason=reason,
        )

    try:
        output_per_input_odt, output_unit, capacity_odt = _product_ratio(
            line,
            flows,
            product,
            density_odt_m3=density_odt_m3,
        )
    except ResidueError as error:
        return excluded(f"line_rejected:{error}")
    if capacity_odt <= 0.0:
        return excluded("nonpositive_capacity")

    operating_cost = _number(
        line["operating_cost"], f"{line_id} operating_cost", minimum=0.0
    )
    operating_unit = str(line.get("operating_cost_unit", "")).strip()
    if _cost_unit_base(operating_unit) != output_unit:
        return excluded(
            "operating_cost_unit_mismatch:"
            f"{operating_unit} vs product {output_unit}"
        )
    processing_per_odt = operating_cost * output_per_input_odt

    price_value = 0.0
    price_unit = ""
    revenue_per_odt = 0.0
    price_status = "missing"
    price_basis = "no_price"
    if price is not None and price.value > 0.0:
        price_unit = price.unit
        try:
            revenue_per_odt, price_basis = product_revenue_per_input_odt(
                output_per_input_odt,
                output_unit,
                price.value,
                price.unit,
            )
        except ResidueError:
            price_status = "unsupported_price_unit"
            price_basis = f"price_unit_cannot_express_{output_unit}"
        else:
            price_value = price.value
            price_status = "available"

    capacity_m3 = _to_m3_from_odt(capacity_odt, density_odt_m3=density_odt_m3)
    status = "candidate"
    status_reason = ""
    if location_quality == "proxy":
        status = "excluded"
        status_reason = "proxy_location_low_confidence"
    elif location_quality == "missing":
        status = "excluded"
        status_reason = "missing_location"
    return ResidueDemandRow(
        recipient_facility=facility.facility_id,
        facility_class=facility.facility_class,
        processing_line=line_id,
        accepted_residue_type=residue_type,
        capacity_odt_y=capacity_odt,
        capacity_m3_y=capacity_m3,
        capacity_basis=(
            f"processing_lines.infeed_rate={line['infeed_rate']} "
            f"{line['infeed_unit']} -> {capacity_odt:.6f} odt/y;"
            f"density={density_odt_m3:.6f} odt/m3"
        ),
        operating_cost=operating_cost,
        operating_cost_unit=operating_unit,
        operating_cost_cad_per_input_odt=processing_per_odt,
        product=product,
        product_output_per_input_odt=output_per_input_odt,
        product_output_unit=output_unit,
        product_price=price_value,
        product_price_unit=price_unit,
        product_revenue_cad_per_input_odt=revenue_per_odt,
        price_status=price_status,
        coord_source=facility.coord_source,
        coord_confidence=facility.coord_confidence,
        location_quality=location_quality,
        evidence=(
            f"ffcm_processing_line={line_id};"
            f"feedstock={feedstock};product={product};"
            f"outfeed_ratio={output_per_input_odt:.12g} "
            f"{output_unit}/odt_in;"
            f"operating_cost={operating_cost:.6f} {operating_unit};"
            f"price_status={price_status};price_basis={price_basis}"
        ),
        status=status,
        status_reason=status_reason,
    )


def build_residue_demand_candidates(
    archive: ProcessingArchive,
    facilities: Sequence[FacilityLocation],
    *,
    density_odt_m3: float = DENSITY_ODT_M3,
) -> tuple[ResidueDemandRow, ...]:
    """Build recipient candidates for PAP, PEL, and BIO facilities."""
    rows: list[ResidueDemandRow] = []
    for facility in facilities:
        if facility.facility_class not in CONSUMER_FEEDSTOCK_BY_CLASS:
            continue
        feedstock = CONSUMER_FEEDSTOCK_BY_CLASS[facility.facility_class]
        lines = [
            line
            for line in archive.lines_by_facility.get(facility.facility_id, ())
            if str(line.get("feedstock", "")).strip().lower() == feedstock
        ]
        if not lines:
            rows.append(
                ResidueDemandRow(
                    recipient_facility=facility.facility_id,
                    facility_class=facility.facility_class,
                    processing_line="",
                    accepted_residue_type=RESIDUE_TYPE_BY_FEEDSTOCK[feedstock],
                    capacity_odt_y=0.0,
                    capacity_m3_y=0.0,
                    capacity_basis="missing_processing_line",
                    operating_cost=0.0,
                    operating_cost_unit="",
                    operating_cost_cad_per_input_odt=0.0,
                    product="",
                    product_output_per_input_odt=0.0,
                    product_output_unit="",
                    product_price=0.0,
                    product_price_unit="",
                    product_revenue_cad_per_input_odt=0.0,
                    price_status="unavailable",
                    coord_source=facility.coord_source,
                    coord_confidence=facility.coord_confidence,
                    location_quality=facility.location_quality,
                    evidence="no_matching_ffcm_processing_line",
                    status="excluded",
                    status_reason="no_processing_line",
                )
            )
            continue
        for line in lines:
            line_id = _identifier(line["processing_line"], "processing_line")
            price = archive.prices.get(
                str(line.get("primary_product", "")).strip().lower()
            )
            row = compute_line_demand(
                line,
                archive.flows_by_line.get(line_id, ()),
                facility,
                price,
                density_odt_m3=density_odt_m3,
            )
            if row is not None:
                rows.append(row)
    rows.sort(key=lambda item: (item.recipient_facility, item.processing_line))
    return tuple(rows)
