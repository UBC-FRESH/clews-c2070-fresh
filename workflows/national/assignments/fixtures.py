"""Build the TSA24/BC diagnostic Stage A fixture from parsed source tables.

The fixture is intentionally conservative:

* supply is the TSA24 assortment matrix scaled from its per-hectare basis;
* facilities are BC SAW/PAP/PEL/BIO lines with explicit unit-normalized capacity
  and coordinate provenance (Mills DB, suppliers.dbf, TL/water hub proxy, missing);
* transport is the parsed FFCM TL graph from a documented TSA24 Prince George
  mill proxy anchor, with road/rail modes and transload costs;
* contracts remain diagnostic zero-minimum/nameplate-maximum records and are
  labelled ``diagnostic_absent``.

Every non-archive conversion is recorded in the returned assumption table.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import units
from .facilities import (
    FacilityTable,
    build_facility_capacities,
    build_facility_table,
)
from .schema import (
    AssignmentInputs,
    Contract,
    FacilityCapacity,
    FeedstockGrade,
    SupplyOffer,
)
from .transport import (
    DIAGNOSTIC_ACCESS_KM,
    TransportTable,
    build_transport_table,
)

__all__ = [
    "DIAGNOSTIC_ACCESS_KM",
    "FixtureError",
    "Tsa24Fixture",
    "build_tsa24_fixture",
    "write_fixture_tables",
]


class FixtureError(RuntimeError):
    """Raised when a source table cannot produce a defensible fixture."""


# The TSA24 residue ledger uses a 0.45 ODT/m3 basic density and 0.45 wet-basis
# moisture assumption.  The same density is the declared default for odt/y and
# bdmt/y capacity normalization; every affected row carries the basis token.
RESIDUE_DENSITY_ODT_M3 = units.DEFAULT_BASIC_DENSITY_ODT_M3
ODT_TO_M3 = 1.0 / RESIDUE_DENSITY_ODT_M3
DIAGNOSTIC_UNASSIGNED_PENALTY_CAD_M3 = 1000.0

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
        raise FixtureError(f"required fixture input is missing: {path}")
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise FixtureError(f"fixture input has no data rows: {path}")
    return rows


def _require_columns(
    path: Path, rows: list[dict[str, str]], required: Sequence[str]
) -> None:
    missing = set(required) - set(rows[0])
    if missing:
        raise FixtureError(f"{path} is missing required columns: {sorted(missing)}")


def _number(value: object, name: str, *, default: float | None = None) -> float:
    text = str(value).strip()
    if text == "" or text.lower() in {"na", "nan", "none"}:
        if default is not None:
            return default
        raise FixtureError(f"{name} is missing")
    try:
        number = float(text)
    except ValueError as error:
        raise FixtureError(f"{name} is not numeric: {value!r}") from error
    if not math.isfinite(number):
        raise FixtureError(f"{name} is not finite: {value!r}")
    return number


def _top_cm(value: object) -> float:
    text = str(value).strip().lower()
    if text in {"", "na", "nan", "none"}:
        return 0.0
    if text.endswith("+"):
        return _number(text[:-1], "top_class_cm lower bound", default=0.0)
    return _number(text.split("-", 1)[0], "top_class_cm lower bound", default=0.0)


def _truthy(value: object) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


def _identifier(value: object) -> str:
    return "".join(char if char.isalnum() else "_" for char in str(value)).strip("_")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _assumption(
    category: str,
    assumption: str,
    value: object,
    unit: str,
    evidence: str,
    impact: str,
) -> dict[str, Any]:
    return {
        "category": category,
        "assumption": assumption,
        "value": value,
        "unit": unit,
        "evidence": evidence,
        "impact": impact,
    }


@dataclass(frozen=True, slots=True)
class Tsa24Fixture:
    """A parsed Stage A problem plus the tables used to build it."""

    inputs: AssignmentInputs
    tables: Mapping[str, tuple[dict[str, Any], ...]]
    source_hashes: Mapping[str, str]
    config: Mapping[str, Any]
    facility_table: FacilityTable
    transport_table: TransportTable


def _supply_grade_rows(
    matrix_path: Path,
    ledger_path: Path,
    area_ha: float,
    period: str,
) -> tuple[tuple[FeedstockGrade, ...], tuple[SupplyOffer, ...], list[dict[str, Any]]]:
    matrix_rows = _read_csv(matrix_path)
    _require_columns(
        matrix_path,
        matrix_rows,
        {
            "species",
            "product",
            "sort_code",
            "top_class_cm",
            "length_class_m",
            "volume_m3",
            "value_cad",
            "price_cad_m3",
            "density_odt_m3",
            "is_total",
            "area_ha",
        },
    )
    ledger_rows = _read_csv(ledger_path)
    _require_columns(
        ledger_path,
        ledger_rows,
        {"residue_volume_m3", "basic_density_odt_m3", "moisture_wet_basis"},
    )
    ledger_density = _number(
        ledger_rows[0]["basic_density_odt_m3"], "ledger basic_density_odt_m3"
    )
    ledger_moisture = _number(
        ledger_rows[0]["moisture_wet_basis"], "ledger moisture_wet_basis"
    )
    if abs(ledger_density - RESIDUE_DENSITY_ODT_M3) > 1e-9:
        raise FixtureError(
            "the residue ledger density changed; update the documented 0.45 ODT/m3 "
            "conversion before running the pilot"
        )

    detail_rows = [row for row in matrix_rows if not _truthy(row["is_total"])]
    if not detail_rows:
        raise FixtureError("assortment matrix has no detail rows")

    expected_volume = sum(
        _number(row["volume_m3"], "matrix volume_m3") for row in detail_rows
    )
    totals = [row for row in matrix_rows if _truthy(row["is_total"])]
    if totals:
        declared = _number(totals[0]["volume_m3"], "matrix total volume_m3")
        target_volume = _number(
            totals[0].get("volume_m3ha", ""), "matrix total volume_m3ha", default=0.0
        )
        if abs(declared - expected_volume) > 1e-6:
            raise FixtureError(
                f"matrix detail volume {expected_volume} does not equal declared total {declared}"
            )
        if target_volume > 0.0 and abs(expected_volume - target_volume) > 1e-6:
            raise FixtureError(
                f"matrix volume {expected_volume} does not equal target_volume_m3ha {target_volume}"
            )

    grouped: dict[tuple[str, str, str, str], dict[str, float]] = {}
    for row in detail_rows:
        product = str(row["product"]).strip().lower()
        if product not in {"sawlog", "pulpwood", "other", "residue"}:
            continue
        sort_code = str(row["sort_code"]).strip().upper()
        grade_id = {
            "sawlog": "SAWLOG",
            "pulpwood": "PULP",
            "other": "OTHER_LOG",
            "residue": "RESIDUE",
        }[product]
        key = (
            grade_id,
            str(row["species"]).strip().upper(),
            sort_code,
            str(row["top_class_cm"]).strip(),
        )
        bucket = grouped.setdefault(
            key,
            {
                "volume_m3": 0.0,
                "value_cad": 0.0,
                "density_weighted": 0.0,
                "volume_for_density": 0.0,
            },
        )
        volume = _number(row["volume_m3"], "matrix volume_m3")
        value = _number(row["value_cad"], "matrix value_cad")
        density = (
            RESIDUE_DENSITY_ODT_M3
            if grade_id == "RESIDUE"
            else _number(row["density_odt_m3"], "matrix density_odt_m3", default=0.40)
        )
        bucket["volume_m3"] += volume
        bucket["value_cad"] += value
        bucket["density_weighted"] += density * volume
        bucket["volume_for_density"] += volume

    if not grouped:
        raise FixtureError("no supported assortment products found in the matrix")

    supplies: list[SupplyOffer] = []
    grade_density_weighted: dict[str, float] = {}
    grade_volume: dict[str, float] = {}
    for index, ((grade_id, species, sort_code, top_class), bucket) in enumerate(
        sorted(grouped.items()), start=1
    ):
        volume = bucket["volume_m3"] * area_ha
        if volume <= 0.0:
            continue
        density = (
            RESIDUE_DENSITY_ODT_M3
            if grade_id == "RESIDUE"
            else (
                bucket["density_weighted"] / bucket["volume_for_density"]
                if bucket["volume_for_density"] > 0
                else 0.40
            )
        )
        cost = (
            bucket["value_cad"] / bucket["volume_m3"]
            if bucket["volume_m3"] > 0
            else 0.0
        )
        offer_id = _identifier(
            f"TSA24_{index:03d}_{grade_id}_{species}_{sort_code}_{top_class}"
        )
        moisture = ledger_moisture
        evidence = (
            "derived_tsa24_assortment_matrix;"
            "roadside_cost=matrix_price_cad_m3_proxy;"
            f"moisture={moisture}_green_assumption"
        )
        supplies.append(
            SupplyOffer(
                offer_id=offer_id,
                period=period,
                grade_id=grade_id,
                product=_GRADE_PRODUCTS[grade_id],
                sort_code=sort_code,
                top_cm=_top_cm(top_class),
                origin="TSA24_AU23000",
                volume_m3=volume,
                density_odt_m3=density,
                moisture_wet_basis=moisture,
                roadside_cost_cad_m3=max(0.0, cost),
                evidence=evidence,
                source_ref=f"assortment_matrix.csv:{species}:{sort_code}:{top_class}",
            )
        )
        grade_density_weighted[grade_id] = (
            grade_density_weighted.get(grade_id, 0.0) + density * volume
        )
        grade_volume[grade_id] = grade_volume.get(grade_id, 0.0) + volume

    grades: list[FeedstockGrade] = []
    for grade_id, product in _GRADE_PRODUCTS.items():
        volume = grade_volume.get(grade_id, 0.0)
        if grade_id == "HOGFUEL" and volume <= 0.0:
            grades.append(
                FeedstockGrade(
                    grade_id=grade_id,
                    product=product,
                    unit="m3",
                    accepted_sorts=_GRADE_SORTS[grade_id],
                    density_odt_m3=RESIDUE_DENSITY_ODT_M3,
                    moisture_wet_basis=ledger_moisture,
                    description="Reserved hog-fuel grade; the TSA24 matrix has no hog-fuel offers.",
                    evidence="reserved_no_supply",
                )
            )
            continue
        if volume <= 0.0:
            continue
        grades.append(
            FeedstockGrade(
                grade_id=grade_id,
                product=product,
                unit="m3",
                accepted_sorts=_GRADE_SORTS[grade_id],
                density_odt_m3=grade_density_weighted[grade_id] / volume,
                moisture_wet_basis=ledger_moisture,
                description=(
                    "Dimension-only assortment grade; not a BC official grade."
                ),
                evidence="derived_tsa24_assortment_matrix",
            )
        )

    if not supplies:
        raise FixtureError("no positive supply offers could be built from the matrix")
    return tuple(sorted(grades, key=lambda item: item.grade_id)), tuple(supplies), []


def _diagnostic_contracts(
    facilities: Sequence[FacilityCapacity], period: str
) -> tuple[Contract, ...]:
    return tuple(
        Contract(
            contract_id=f"DIAG-{facility.facility_id}",
            period=period,
            facility_id=facility.facility_id,
            accepted_grades=facility.eligible_grades,
            min_volume_m3=0.0,
            max_volume_m3=facility.nameplate_m3_y,
            gate_cost_cad_m3=0.0,
            evidence="diagnostic_no_populated_contract_table",
            diagnostic=True,
            notes="min=0, max=nameplate; replace with real contract terms before use",
            contract_source="diagnostic_absent",
        )
        for facility in facilities
    )


def _class_mix(facility_table: FacilityTable) -> dict[str, Any]:
    rows: dict[str, dict[str, float]] = {}
    for facility_class in ("SAW", "PAP", "PEL", "BIO"):
        records = [
            record
            for record in facility_table.records
            if record.stage_a_scope and record.facility_class == facility_class
        ]
        rows[facility_class] = {
            "facility_count": float(len(records)),
            "nameplate_m3_y": float(
                sum((record.nameplate_m3_y or 0.0) for record in records)
            ),
        }
    total_count = sum(row["facility_count"] for row in rows.values())
    total_nameplate = sum(row["nameplate_m3_y"] for row in rows.values())
    for row in rows.values():
        row["facility_share"] = (
            row["facility_count"] / total_count if total_count > 0 else 0.0
        )
        row["nameplate_share"] = (
            row["nameplate_m3_y"] / total_nameplate if total_nameplate > 0 else 0.0
        )
    return {
        "classes": rows,
        "facility_count": total_count,
        "nameplate_m3_y": total_nameplate,
    }


def _assumption_rows(
    area_ha: float,
    target_stand: Mapping[str, Any],
    facility_table: FacilityTable,
    transport_table: TransportTable,
) -> list[dict[str, Any]]:
    coverage = facility_table.coverage
    coverage_in_scope = facility_table.coverage_in_scope
    transport_summary = transport_table.summary
    comparison = transport_summary.get("diagnostic_comparison", {})
    old_distance = comparison.get("old_diagnostic_distance_km", {})
    new_distance = comparison.get("new_distance_km", {})
    rows = [
        _assumption(
            "scope",
            "TSA24/BC Stage A pilot supply area is a diagnostic scale, not a TSA supply estimate.",
            area_ha,
            "ha",
            "diagnostic",
            "All volumes scale linearly with this value.",
        ),
        _assumption(
            "supply",
            "Assortment matrix rows are per-hectare values; the pilot multiplies by the diagnostic area.",
            target_stand.get("volume_m3ha", ""),
            "m3/ha",
            "derived_tsa24_assortment_matrix",
            "Base volume is area x matrix volume; no additional yield expansion.",
        ),
        _assumption(
            "supply",
            "Roadside cost is the assortment matrix price_cad_m3 proxy, not a harvest/stumpage cost.",
            "",
            "CAD/m3",
            "derived_tsa24_assortment_matrix",
            "Changes the profit margin; transport and processing remain explicit.",
        ),
        _assumption(
            "residue",
            "Residue volume uses the ledger 0.45 ODT/m3 basic density and 0.45 wet-basis moisture.",
            RESIDUE_DENSITY_ODT_M3,
            "ODT/m3",
            "tsa24_residue_ledger_assumption",
            "Residue ODT is volume x 0.45; matrix species-specific residue_odt is not used.",
        ),
        _assumption(
            "log_moisture",
            "Log moisture is set to the ledger green assumption because the matrix has no log moisture field.",
            0.45,
            "wet_basis",
            "diagnostic",
            "Only relevant if a moisture blend limit is enabled.",
        ),
        _assumption(
            "facility_capacity",
            "m3/y infeed is used directly; odt/y and bdmt/y infeed use the declared 0.45 ODT/m3 density; admt/y is rejected without a declared air-dry moisture; mbf/y is rejected without a declared recovery factor; energy units cannot become volume.",
            ODT_TO_M3,
            "m3/ODT",
            "units.py;archive_parsed_processing_lines;ledger_density",
            "PAP bdmt/y alternatives convert below the paired m3/y log line, so product capacity is unchanged.",
        ),
        _assumption(
            "facility_capacity",
            "Facility nameplate is the sum of the maximum normalized capacity per primary product; feedstock alternatives are not additive.",
            "",
            "m3/y",
            "archive_parsed_processing_lines",
            "Prevents counting the same FFCM line once per feedstock alternative.",
        ),
        _assumption(
            "coordinate",
            "Coordinates use Mills DB first, CFS suppliers.dbf second, an FFCM TL/water hub proxy third, and missing last; no Euclidean guesses are used.",
            f"mills_db={coverage.get('mills_db', 0)};suppliers_dbf={coverage.get('suppliers_dbf', 0)};tl_anchor_proxy={coverage.get('tl_anchor_proxy', 0)};missing={coverage.get('missing', 0)}",
            "facility_count",
            "facility_geocoding.csv;routes.csv",
            "TL/water proxy uses the median coordinate of geocoded facilities directly attached to the same hub; confidence is low.",
        ),
        _assumption(
            "coordinate",
            "Stage A scope coordinate coverage is tracked separately from all BC facilities.",
            f"mills_db={coverage_in_scope.get('mills_db', 0)};suppliers_dbf={coverage_in_scope.get('suppliers_dbf', 0)};tl_anchor_proxy={coverage_in_scope.get('tl_anchor_proxy', 0)};missing={coverage_in_scope.get('missing', 0)}",
            "facility_count",
            "facility_geocoding.csv;routes.csv",
            "Missing coordinates do not fabricate a location and do not by themselves exclude a facility from the LP.",
        ),
        _assumption(
            "transport",
            "The TSA24 supply anchor is a geocoded Prince George mill proxy because no TSA24 FMU exists in the FFCM graph.",
            transport_summary.get("anchor", {}).get("anchor_id", ""),
            "facility_id",
            "facility_geocoding.csv;routes.csv",
            "Anchor coordinate is Mills DB 53.99,-122.69 (BC-SAW-488); all distances are relative to this proxy.",
        ),
        _assumption(
            "transport",
            "Distances are FFCM route-graph shortest paths from the anchor through TL nodes, not Euclidean distances.",
            new_distance.get("median", ""),
            "km",
            "archive_parsed_routes",
            f"Median graph distance is {new_distance.get('median', '')} km versus {old_distance.get('median', '')} km in the previous 150 km diagnostic.",
        ),
        _assumption(
            "transport",
            "Road and rail costs use BC transporters with archive fuel and operating rates and material capacities; transload costs are added at each TL node.",
            "",
            "CAD/m3",
            "archive_parsed_transporters;transporter_materials;transload_costs",
            "Combination paths use the actual leg mode; no global average rate is assumed.",
        ),
        _assumption(
            "transport",
            "FFCM route capacity is absent from the parsed route table; every transport link is uncapped.",
            "",
            "m3",
            "diagnostic",
            "capacity=None and capacity_source=absent; do not interpret congestion or rail/water limits.",
        ),
        _assumption(
            "contracts",
            "Contracts are diagnostic: min=0 and max=facility nameplate; no populated real contract table exists.",
            "",
            "m3",
            "diagnostic_absent",
            "contract_source=diagnostic_absent on every contract; compliance is a bound check, not commercial evidence.",
        ),
        _assumption(
            "objective",
            "Unassigned supply carries a diagnostic penalty so that feasible supply is not silently dropped.",
            DIAGNOSTIC_UNASSIGNED_PENALTY_CAD_M3,
            "CAD/m3",
            "diagnostic",
            "The penalty is reported as a separate objective component.",
        ),
        _assumption(
            "grade",
            "Assortment sorts are dimension-only; no BC official grade equivalence is claimed.",
            "",
            "grade",
            "derived_tsa24_assortment_matrix",
            "Grade eligibility is a feedstock-class/product screen, not a grade-quality test.",
        ),
        _assumption(
            "scope",
            "MKRF is not used anywhere in this pilot.",
            "",
            "n/a",
            "scope_rule",
            "No MKRF input or output is read or written.",
        ),
    ]
    return rows


def build_tsa24_fixture(
    repo_root: Path,
    *,
    area_ha: float = 1000.0,
    period: str = "2026",
    scenario_id: str = "tsa24_bc_stage_a_pilot",
) -> Tsa24Fixture:
    """Build the TSA24/BC Stage A problem from parsed archive and derived tables."""
    if not math.isfinite(float(area_ha)) or float(area_ha) <= 0.0:
        raise FixtureError("area_ha must be finite and positive")
    repo_root = Path(repo_root)
    matrix_path = (
        repo_root
        / "data/derived/national/assortments/tsa24_prototype/assortment_matrix.csv"
    )
    ledger_path = (
        repo_root
        / "data/derived/national/assortments/tsa24_prototype/residue_ledger.csv"
    )
    target_path = (
        repo_root
        / "data/derived/national/assortments/tsa24_prototype/target_stand.json"
    )
    facilities_path = repo_root / "metadata/ffcm-archive/parsed/facilities.csv"
    lines_path = repo_root / "metadata/ffcm-archive/parsed/processing_lines.csv"
    flows_path = repo_root / "metadata/ffcm-archive/parsed/processing_line_flows.csv"
    routes_path = repo_root / "metadata/ffcm-archive/parsed/routes.csv"
    transporters_path = repo_root / "metadata/ffcm-archive/parsed/transporters.csv"
    materials_path = (
        repo_root / "metadata/ffcm-archive/parsed/transporter_materials.csv"
    )
    transload_path = repo_root / "metadata/ffcm-archive/parsed/transload_costs.csv"
    prices_path = repo_root / "metadata/ffcm-archive/parsed/commodities.csv"
    geocoding_path = repo_root / "metadata/facility_geocoding.csv"
    anchors_path = repo_root / "metadata/fmu_tl_anchors.csv"
    for path in (
        matrix_path,
        ledger_path,
        target_path,
        facilities_path,
        lines_path,
        flows_path,
        routes_path,
        transporters_path,
        materials_path,
        transload_path,
        prices_path,
        geocoding_path,
        anchors_path,
    ):
        if not path.exists():
            raise FixtureError(f"required fixture input is missing: {path}")

    with target_path.open(encoding="utf-8") as handle:
        target_stand = json.load(handle)

    grades, supplies, _ = _supply_grade_rows(
        matrix_path, ledger_path, float(area_ha), period
    )
    facility_table = build_facility_table(repo_root)
    facilities, line_screen = build_facility_capacities(
        repo_root, facility_table, period=period
    )
    transport_table = build_transport_table(
        repo_root, facilities, grades, period=period
    )
    status_by_facility = {
        record.facility_id: (
            "out_of_scope"
            if not record.stage_a_scope
            else (
                "zero_capacity"
                if record.capacity_status != "positive"
                else transport_table.status_by_facility.get(
                    record.facility_id, "missing_no_offer"
                )
            )
        )
        for record in facility_table.records
    }
    facility_table = facility_table.with_transport_status(status_by_facility)
    links = transport_table.to_transport_links(period)
    contracts = _diagnostic_contracts(facilities, period)

    assumptions = _assumption_rows(
        float(area_ha), target_stand, facility_table, transport_table
    )
    assumptions.extend(
        _assumption(
            "facility_screen",
            f"{row['facility_id']} {row['line_id']} rejected: {row['reason']}",
            row.get("infeed_rate", ""),
            row.get("infeed_unit", ""),
            "archive_parsed_processing_lines",
            "The line is not used for capacity or cost.",
        )
        for row in line_screen
        if row["capacity_status"] == "rejected"
    )
    assumptions.extend(
        _assumption(
            "transport_screen",
            f"{row['facility_id']} rejected: {row['status']}",
            row.get("distance_km", ""),
            "km",
            "archive_parsed_routes",
            "The facility has no defensible FFCM TL-graph path from the TSA24 anchor.",
        )
        for row in transport_table.screen
        if str(row["status"]).startswith("missing")
    )

    inputs = AssignmentInputs(
        run_id=f"{scenario_id}_v1",
        scenario_id=scenario_id,
        periods=(period,),
        grades=grades,
        supplies=supplies,
        facilities=facilities,
        transport_links=links,
        contracts=contracts,
        unassigned_penalty_cad_m3=DIAGNOSTIC_UNASSIGNED_PENALTY_CAD_M3,
        moisture_limit_by_class={},
        metadata={
            "pilot": "tsa24_bc_stage_a_facility_assignment",
            "area_ha": float(area_ha),
            "period": period,
            "target_stand": target_stand,
            "transport_summary": dict(transport_table.summary),
            "facility_coverage": facility_table.summary(),
            "tsa24_anchor": dict(transport_table.summary.get("anchor", {})),
            "unit_registry": list(units.unit_registry_rows()),
            "diagnostic": True,
            "not_a_tsa_supply_estimate": True,
            "mkrf_used": False,
        },
    )

    tables = {
        "supply_offers": tuple(supply.to_dict() for supply in inputs.supplies),
        "facility_capacity": tuple(
            facility.to_dict() for facility in inputs.facilities
        ),
        "facility_offers": tuple(
            offer.to_dict()
            for facility in inputs.facilities
            for offer in facility.offers
        ),
        "facilities_bc": tuple(
            record.to_dict() for record in facility_table.records
        ),
        "transport_links": tuple(link.to_dict() for link in inputs.transport_links),
        "transport_links_bc": tuple(
            path.to_dict() for path in transport_table.links
        ),
        "transport_screen": tuple(transport_table.screen),
        "contracts": tuple(contract.to_dict() for contract in inputs.contracts),
        "assumptions": tuple(assumptions),
        "line_screen": tuple(line_screen),
        "grades": tuple(grade.to_dict() for grade in inputs.grades),
    }
    source_hashes = {
        str(path.relative_to(repo_root)): _sha256(path)
        for path in (
            matrix_path,
            ledger_path,
            target_path,
            facilities_path,
            lines_path,
            flows_path,
            routes_path,
            transporters_path,
            materials_path,
            transload_path,
            prices_path,
            geocoding_path,
            anchors_path,
        )
    }
    class_mix = _class_mix(facility_table)
    config = {
        "area_ha": float(area_ha),
        "period": period,
        "scenario_id": scenario_id,
        "residue_density_odt_m3": RESIDUE_DENSITY_ODT_M3,
        "diagnostic_access_km": DIAGNOSTIC_ACCESS_KM,
        "unassigned_penalty_cad_m3": DIAGNOSTIC_UNASSIGNED_PENALTY_CAD_M3,
        "facility_classes": ["BIO", "PAP", "PEL", "SAW"],
        "facility_coverage": facility_table.summary(),
        "transport_summary": dict(transport_table.summary),
        "tsa24_anchor": dict(transport_table.summary.get("anchor", {})),
        "unit_registry": list(units.unit_registry_rows()),
        "ffcm_bc_class_mix": class_mix,
        "transport_rates": dict(transport_table.summary.get("rate_table", {})),
        "target_volume_m3ha": target_stand.get("volume_m3ha"),
        "target_agb_odt_ha": target_stand.get("target_agb_odt_ha"),
    }
    return Tsa24Fixture(
        inputs=inputs,
        tables=tables,
        source_hashes=source_hashes,
        config=config,
        facility_table=facility_table,
        transport_table=transport_table,
    )


def write_fixture_tables(fixture: Tsa24Fixture, out_dir: Path) -> list[Path]:
    """Write fixture tables next to the pilot outputs."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, rows in fixture.tables.items():
        if not rows:
            continue
        path = out_dir / f"{name}.csv"
        fieldnames: list[str] = []
        for row in rows:
            for key in row:
                if key not in fieldnames:
                    fieldnames.append(key)
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=fieldnames,
                extrasaction="ignore",
                lineterminator="\n",
            )
            writer.writeheader()
            writer.writerows(rows)
        written.append(path)
    return written
