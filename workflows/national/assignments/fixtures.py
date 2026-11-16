"""Build the TSA24/BC diagnostic Stage A fixture from parsed source tables.

The fixture is intentionally conservative:

* supply is the TSA24 assortment matrix scaled from its per-hectare basis;
* facilities are BC SAW/PAP/PEL/BIO lines with explicitly normalized capacity;
* transport is a diagnostic road estimate because the archive has no TSA24
  supply-node-to-facility route and only eight BC facilities carry coordinates;
* contracts are diagnostic zero-minimum/nameplate-maximum records because the
  archive has no populated contract table.

Every non-archive conversion is recorded in the returned assumption table.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .schema import (
    REJECTED_CAPACITY_UNITS,
    AssignmentError,
    AssignmentInputs,
    Contract,
    FacilityCapacity,
    FacilityOffer,
    FeedstockGrade,
    SupplyOffer,
    TransportLink,
    require_supported_capacity_unit,
)

__all__ = [
    "FixtureError",
    "Tsa24Fixture",
    "build_tsa24_fixture",
    "write_fixture_tables",
]


class FixtureError(RuntimeError):
    """Raised when a source table cannot produce a defensible fixture."""


# The TSA24 residue ledger uses a 0.45 ODT/m3 basic density and 0.45 wet-basis
# moisture assumption.  The same density is used to normalize odt/y facility
# capacity to the model's green-m3 basis; this is the only global conversion in
# the fixture and it is recorded on every affected row.
RESIDUE_DENSITY_ODT_M3 = 0.45
ODT_TO_M3 = 1.0 / RESIDUE_DENSITY_ODT_M3

# The archive has no TSA24 supply-node-to-transload link.  This is the
# documented diagnostic access leg; the facility-to-transload leg is taken from
# the FFCM road graph.
DIAGNOSTIC_ACCESS_KM = 150.0
DIAGNOSTIC_UNASSIGNED_PENALTY_CAD_M3 = 1000.0

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
        raise FixtureError(f"required fixture input is missing: {path}")
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise FixtureError(f"fixture input has no data rows: {path}")
    return rows


def _require_columns(
    path: Path, rows: list[dict[str, str]], required: Iterable[str]
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
        except FixtureError:
            continue
        unit = str(row["price_unit"]).strip().lower().replace("cad", "$")
        if price <= 0.0 or not unit:
            continue
        prices[commodity] = (price, unit)
    if not prices:
        raise FixtureError(f"no positive commodity prices found in {path}")
    return prices


def _grade_volume_basis(grade_id: str) -> str:
    return "m3"


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


def _normalize_capacity(line: Mapping[str, str]) -> tuple[float | None, str, str]:
    unit = str(line.get("infeed_unit", "")).strip().lower()
    try:
        require_supported_capacity_unit(unit)
    except AssignmentError as error:
        reason = (
            "rejected_capacity_unit"
            if unit in REJECTED_CAPACITY_UNITS
            else "unknown_capacity_unit"
        )
        return None, unit, f"{reason}:{error}"
    rate = _number(line.get("infeed_rate", ""), "infeed_rate", default=0.0)
    if rate <= 0.0:
        return None, unit, "rejected_nonpositive_infeed_rate"
    if unit == "m3/y":
        return rate, "m3/y", "archive_m3_y"
    if unit == "odt/y":
        return rate * ODT_TO_M3, "odt/y", "ledger_0.45_odt_m3"
    return None, unit, f"unknown_capacity_unit:{unit}"


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
        return cost * RESIDUE_DENSITY_ODT_M3, "ledger_density_fallback"
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


def _accepted_grades(facility_class: str, feedstock: str) -> tuple[str, ...]:
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


def _facility_rows(
    facilities_path: Path,
    lines_path: Path,
    flows_path: Path,
    prices_path: Path,
    period: str,
) -> tuple[tuple[FacilityCapacity, ...], list[dict[str, Any]], list[dict[str, Any]]]:
    facility_rows = _read_csv(facilities_path)
    _require_columns(facilities_path, facility_rows, {"facility", "facility_class"})
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
    selected_classes = {"SAW", "PAP", "PEL", "BIO"}
    facility_class = {
        str(row["facility"]).strip(): str(row["facility_class"]).strip().upper()
        for row in facility_rows
        if str(row["facility"]).strip().startswith("BC-")
        and str(row["facility_class"]).strip().upper() in selected_classes
    }
    if not facility_class:
        raise FixtureError("no BC SAW/PAP/PEL/BIO facilities found")

    line_screen: list[dict[str, Any]] = []
    lines_by_facility: dict[str, list[dict[str, Any]]] = {}
    for row in line_rows:
        facility_id = str(row["facility"]).strip()
        if facility_id not in facility_class:
            continue
        line_id = str(row["processing_line"]).strip()
        capacity, unit, capacity_basis = _normalize_capacity(row)
        if capacity is None:
            line_screen.append(
                {
                    "facility_id": facility_id,
                    "line_id": line_id,
                    "facility_class": facility_class[facility_id],
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
            RESIDUE_DENSITY_ODT_M3
            if str(row["feedstock"]).strip().lower() == "logging_residue"
            else 0.40
        )
        revenue, revenue_basis = _normalize_product_revenue(
            line_with_flows, capacity, prices, density
        )
        record = {
            "line_id": line_id,
            "facility_id": facility_id,
            "facility_class": facility_class[facility_id],
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
        lines_by_facility.setdefault(facility_id, []).append(record)
        line_screen.append(
            {
                "facility_id": facility_id,
                "line_id": line_id,
                "facility_class": facility_class[facility_id],
                "feedstock": record["feedstock"],
                "primary_product": record["primary_product"],
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
        product_capacity: dict[str, float] = {}
        for line in facility_lines:
            product = line["primary_product"]
            product_capacity[product] = max(
                product_capacity.get(product, 0.0), line["capacity_m3_y"]
            )
        nameplate = sum(product_capacity.values())
        if nameplate <= 0.0:
            continue
        candidate_grades: set[str] = set()
        for line in facility_lines:
            candidate_grades.update(
                _accepted_grades(facility_class[facility_id], line["feedstock"])
            )
        offers: list[FacilityOffer] = []
        for grade_id in sorted(candidate_grades):
            by_product: dict[str, list[dict[str, Any]]] = {}
            for line in facility_lines:
                if grade_id in _accepted_grades(
                    facility_class[facility_id], line["feedstock"]
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
                    facility_name=facility_id,
                    facility_class=facility_class[facility_id],
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
                facility_name=facility_id,
                facility_class=facility_class[facility_id],
                nameplate_m3_y=nameplate,
                offers=tuple(offers),
                evidence="parsed_ffcm_processing_lines;nameplate=max_per_product",
            )
        )
    if not facilities:
        raise FixtureError(
            "no BC facility could be normalized to a supported capacity basis"
        )
    return tuple(facilities), line_screen, []


def _facility_tl_distances(
    routes_path: Path, facility_ids: set[str]
) -> dict[str, tuple[str, float]]:
    rows = _read_csv(routes_path)
    _require_columns(
        routes_path,
        rows,
        {"origin_list", "destination_list", "total_length", "transport_mode"},
    )
    distances: dict[str, tuple[str, float]] = {}
    for row in rows:
        if str(row.get("transport_mode", "")).strip().upper() != "ROAD":
            continue
        distance = _number(
            row.get("total_length", ""), "route total_length", default=0.0
        )
        if distance <= 0.0:
            continue
        origins = [
            value.strip()
            for value in str(row["origin_list"]).split(";")
            if value.strip()
        ]
        destinations = [
            value.strip()
            for value in str(row["destination_list"]).split(";")
            if value.strip()
        ]
        for origin in origins:
            for destination in destinations:
                facility = None
                transload = None
                if origin in facility_ids and destination.startswith("TL"):
                    facility, transload = origin, destination
                elif destination in facility_ids and origin.startswith("TL"):
                    facility, transload = destination, origin
                if facility is None or transload is None:
                    continue
                current = distances.get(facility)
                if current is None or distance < current[1]:
                    distances[facility] = (transload, distance)
    return distances


def _transporter_rates(
    transporters_path: Path, materials_path: Path
) -> tuple[float, float, dict[str, float]]:
    rows = _read_csv(transporters_path)
    _require_columns(
        transporters_path,
        rows,
        {
            "transporter",
            "cruising_speed",
            "cruising_speed_unit",
            "transport_mode",
            "operating_cost",
            "operating_cost_unit",
            "fuel_cost",
            "fuel_cost_unit",
        },
    )
    selected = {
        str(row["transporter"]).strip(): row
        for row in rows
        if str(row["transporter"]).strip() in {"BC_logging_truck", "BC_residue_truck"}
    }
    if set(selected) != {"BC_logging_truck", "BC_residue_truck"}:
        raise FixtureError("BC logging/residue transporter rows are missing")
    rates: dict[str, float] = {}
    for name, row in selected.items():
        if str(row["transport_mode"]).strip().upper() != "ROAD":
            raise FixtureError(f"{name} is not a road transporter")
        speed = _number(row["cruising_speed"], f"{name} speed")
        operating = _number(row["operating_cost"], f"{name} operating cost")
        fuel = _number(row["fuel_cost"], f"{name} fuel cost")
        if speed <= 0.0 or operating < 0.0 or fuel < 0.0:
            raise FixtureError(f"{name} has invalid non-positive cost or speed")
        rates[name] = fuel + operating / speed

    material_rows = _read_csv(materials_path)
    _require_columns(
        materials_path, material_rows, {"transporter", "commodity", "capacity", "unit"}
    )
    capacities: dict[str, dict[str, float]] = {}
    for row in material_rows:
        transporter = str(row["transporter"]).strip()
        if transporter not in rates:
            continue
        commodity = str(row["commodity"]).strip().lower()
        capacities.setdefault(transporter, {})[commodity] = _number(
            row["capacity"], f"{transporter} {commodity} capacity"
        )
    log_capacity = capacities.get("BC_logging_truck", {}).get("log")
    residue_capacity = capacities.get("BC_residue_truck", {}).get("logging_residue")
    if not log_capacity or not residue_capacity:
        raise FixtureError(
            "transporter material capacities for log/residue are missing"
        )
    return (
        rates["BC_logging_truck"],
        rates["BC_residue_truck"],
        {
            "log_capacity_m3": log_capacity,
            "residue_capacity_bdt": residue_capacity,
        },
    )


def _transport_links(
    facilities: Sequence[FacilityCapacity],
    routes_path: Path,
    transporters_path: Path,
    materials_path: Path,
    period: str,
) -> tuple[tuple[TransportLink, ...], list[dict[str, Any]], dict[str, float]]:
    distances = _facility_tl_distances(
        routes_path, {facility.facility_id for facility in facilities}
    )
    log_rate, residue_rate, capacities = _transporter_rates(
        transporters_path, materials_path
    )
    log_capacity = capacities["log_capacity_m3"]
    residue_capacity = capacities["residue_capacity_bdt"]
    log_cost_per_m3_km = log_rate / log_capacity
    residue_cost_per_m3_km = residue_rate / residue_capacity * RESIDUE_DENSITY_ODT_M3
    links: list[TransportLink] = []
    screen: list[dict[str, Any]] = []
    for facility in facilities:
        route = distances.get(facility.facility_id)
        if route is None:
            screen.append(
                {
                    "facility_id": facility.facility_id,
                    "facility_class": facility.facility_class,
                    "status": "rejected_no_ffcm_road_route_to_tl",
                    "transload_node": "",
                    "ffcm_route_km": "",
                    "diagnostic_access_km": DIAGNOSTIC_ACCESS_KM,
                    "total_distance_km": "",
                }
            )
            continue
        transload, route_km = route
        total_distance = DIAGNOSTIC_ACCESS_KM + route_km
        for offer in facility.offers:
            is_residue = offer.grade_id in {"RESIDUE", "HOGFUEL"}
            per_m3_km = residue_cost_per_m3_km if is_residue else log_cost_per_m3_km
            links.append(
                TransportLink(
                    link_id=f"TSA24_AU23000->{facility.facility_id}::{offer.grade_id}",
                    period=period,
                    origin="TSA24_AU23000",
                    destination=facility.facility_id,
                    grade_id=offer.grade_id,
                    mode="ROAD",
                    distance_km=total_distance,
                    cost_cad_m3=total_distance * per_m3_km,
                    capacity_m3=None,
                    distance_basis=(
                        "diagnostic_tsa24_access_150km_plus_"
                        "ffcm_facility_to_tl_min_road_km"
                    ),
                    cost_basis=(
                        "transporters_bc_residue_truck"
                        if is_residue
                        else "transporters_bc_logging_truck"
                    ),
                    evidence="diagnostic_not_a_production_route",
                )
            )
        screen.append(
            {
                "facility_id": facility.facility_id,
                "facility_class": facility.facility_class,
                "status": "accepted_diagnostic_route",
                "transload_node": transload,
                "ffcm_route_km": route_km,
                "diagnostic_access_km": DIAGNOSTIC_ACCESS_KM,
                "total_distance_km": total_distance,
            }
        )
    if not links:
        raise FixtureError("no facility has an FFCM road route to a transload node")
    return (
        tuple(links),
        screen,
        {
            "log_cost_per_m3_km": log_cost_per_m3_km,
            "residue_cost_per_m3_km": residue_cost_per_m3_km,
            "log_truck_capacity_m3": log_capacity,
            "residue_truck_capacity_bdt": residue_capacity,
            "bc_logging_truck_cost_per_km": log_rate,
            "bc_residue_truck_cost_per_km": residue_rate,
        },
    )


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
        )
        for facility in facilities
    )


def _assumption_rows(
    area_ha: float,
    target_stand: Mapping[str, Any],
    transport_rates: Mapping[str, float],
) -> list[dict[str, Any]]:
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
            "m3/y infeed is used directly; odt/y infeed is converted at 1/0.45 m3 per ODT; bdmt/y is rejected.",
            ODT_TO_M3,
            "m3/ODT",
            "archive_parsed_processing_lines;ledger_density",
            "PAP bdmt/y lines are duplicate alternatives of the m3/y log lines and are excluded.",
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
            "processing_cost",
            "Operating cost is converted to CAD/m3 infeed with the same line's outfeed ratio when the archive unit is per mbf, kwh, admt, odt, or t.",
            "",
            "CAD/m3",
            "archive_parsed_processing_line_flows",
            "No global mbf/kwh/admt conversion is assumed.",
        ),
        _assumption(
            "revenue",
            "Product revenue uses parsed commodity prices and each line's outfeed mix.",
            "",
            "CAD/m3",
            "archive_parsed_commodities;processing_line_flows",
            "Mass prices are applied to odt/admt outfeed with an explicit flag.",
        ),
        _assumption(
            "transport",
            "The TSA24 supply node has no archive route to an FFCM transload hub; a 150 km access leg is added to the minimum facility-to-TL road distance.",
            DIAGNOSTIC_ACCESS_KM,
            "km",
            "diagnostic",
            "This is the primary transport uncertainty; it is not a Euclidean estimate.",
        ),
        _assumption(
            "transport",
            "Road cost uses BC_logging_truck for logs and BC_residue_truck for residue, with archive fuel and operating rates.",
            transport_rates.get("log_cost_per_m3_km", ""),
            "CAD/m3-km",
            "archive_parsed_transporters;transporter_materials",
            "No transload cost is applied because the diagnostic path is direct road.",
        ),
        _assumption(
            "transport",
            "FFCM route capacity is absent from the parsed route table; pilot transport links are uncapped.",
            "",
            "m3",
            "diagnostic",
            "Add route capacity before interpreting congestion or rail/water limits.",
        ),
        _assumption(
            "contracts",
            "Contracts are diagnostic: min=0 and max=facility nameplate.",
            "",
            "m3",
            "diagnostic_no_populated_contract_table",
            "Contract compliance is reported but does not constrain the base pilot.",
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
    prices_path = repo_root / "metadata/ffcm-archive/parsed/commodities.csv"
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
        prices_path,
    ):
        if not path.exists():
            raise FixtureError(f"required fixture input is missing: {path}")

    with target_path.open(encoding="utf-8") as handle:
        target_stand = json.load(handle)

    grades, supplies, _ = _supply_grade_rows(
        matrix_path, ledger_path, float(area_ha), period
    )
    facilities, line_screen, _ = _facility_rows(
        facilities_path, lines_path, flows_path, prices_path, period
    )
    links, transport_screen, transport_rates = _transport_links(
        facilities, routes_path, transporters_path, materials_path, period
    )
    contracts = _diagnostic_contracts(facilities, period)

    assumptions = _assumption_rows(float(area_ha), target_stand, transport_rates)
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
            "",
            "km",
            "archive_parsed_routes",
            "The facility has no defensible FFCM facility-to-transload road distance.",
        )
        for row in transport_screen
        if row["status"] != "accepted_diagnostic_route"
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
            "transport_rates": transport_rates,
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
        "transport_links": tuple(link.to_dict() for link in inputs.transport_links),
        "contracts": tuple(contract.to_dict() for contract in inputs.contracts),
        "assumptions": tuple(assumptions),
        "line_screen": tuple(line_screen),
        "transport_screen": tuple(transport_screen),
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
            prices_path,
        )
    }
    config = {
        "area_ha": float(area_ha),
        "period": period,
        "scenario_id": scenario_id,
        "residue_density_odt_m3": RESIDUE_DENSITY_ODT_M3,
        "diagnostic_access_km": DIAGNOSTIC_ACCESS_KM,
        "unassigned_penalty_cad_m3": DIAGNOSTIC_UNASSIGNED_PENALTY_CAD_M3,
        "facility_classes": ["BIO", "PAP", "PEL", "SAW"],
        "transport_rates": transport_rates,
        "target_volume_m3ha": target_stand.get("volume_m3ha"),
        "target_agb_odt_ha": target_stand.get("target_agb_odt_ha"),
    }
    return Tsa24Fixture(
        inputs=inputs,
        tables=tables,
        source_hashes=source_hashes,
        config=config,
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
