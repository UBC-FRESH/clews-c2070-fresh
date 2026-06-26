"""Unit and dimensional consistency checks for FRESH fibre prototypes."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from fresh_fibre.schema import read_rows


CURRENCY = "CAD"
DEFAULT_UNIT_REGISTRY = Path("metadata/ffcm-archive/unit-registry.csv")
DEFAULT_CONVERSION_REGISTRY = Path("metadata/ffcm-archive/unit-conversion-registry.csv")


class UnitValidationError(ValueError):
    """Raised when prototype table units are inconsistent."""


@dataclass(frozen=True)
class UnitDefinition:
    unit_id: str
    dimension: str
    canonical_unit: str
    scale_to_canonical: float | None
    status: str


@dataclass(frozen=True)
class UnitRegistry:
    units: dict[str, UnitDefinition]
    conversions: set[tuple[str, str]]

    def base_unit(self, unit: str) -> str:
        if not unit:
            return ""
        if unit in self.units:
            return unit
        candidate = unit.split("_", 1)[0]
        if candidate in self.units:
            return candidate
        return candidate

    def knows(self, unit: str) -> bool:
        return self.base_unit(unit) in self.units


def number_or_none(value: str) -> float | None:
    if value == "":
        return None
    return float(value)


def load_unit_registry(
    unit_registry_path: Path = DEFAULT_UNIT_REGISTRY,
    conversion_registry_path: Path = DEFAULT_CONVERSION_REGISTRY,
) -> UnitRegistry:
    units = {
        row["unit_id"]: UnitDefinition(
            row["unit_id"],
            row["dimension"],
            row["canonical_unit"],
            number_or_none(row["scale_to_canonical"]),
            row["status"],
        )
        for row in read_rows(unit_registry_path)
    }
    conversions = {
        (row["from_unit"], row["to_unit"])
        for row in read_rows(conversion_registry_path)
    }
    validate_registry(units, conversions)
    return UnitRegistry(units, conversions)


def validate_registry(units: dict[str, UnitDefinition], conversions: set[tuple[str, str]]) -> None:
    errors: list[str] = []
    for unit in units.values():
        if unit.canonical_unit not in units:
            errors.append(f"unit {unit.unit_id!r} references unknown canonical unit {unit.canonical_unit!r}")
        elif units[unit.canonical_unit].dimension != unit.dimension:
            errors.append(
                f"unit {unit.unit_id!r} dimension {unit.dimension!r} does not match "
                f"canonical unit {unit.canonical_unit!r}"
            )
        if unit.scale_to_canonical is None and unit.unit_id != unit.canonical_unit:
            errors.append(f"unit {unit.unit_id!r} is missing scale_to_canonical")
    for from_unit, to_unit in conversions:
        if from_unit not in units:
            errors.append(f"conversion references unknown from_unit {from_unit!r}")
        if to_unit not in units:
            errors.append(f"conversion references unknown to_unit {to_unit!r}")
        if from_unit in units and to_unit in units and units[from_unit].dimension != units[to_unit].dimension:
            errors.append(f"conversion {from_unit!r}->{to_unit!r} crosses unit dimensions")
    if errors:
        joined = "\n  - ".join(errors)
        raise UnitValidationError(f"Unit registry validation failed:\n  - {joined}")


def base_unit(unit: str, registry: UnitRegistry | None = None) -> str:
    """Return the physical base unit, dropping commodity suffixes."""
    if not unit:
        return ""
    if registry is not None:
        return registry.base_unit(unit)
    return unit.split("_", 1)[0]


def cost_basis_unit(unit: str, registry: UnitRegistry | None = None) -> str:
    prefix = f"{CURRENCY}_per_"
    if not unit.startswith(prefix):
        return ""
    return base_unit(unit[len(prefix):], registry)


def conversion_units(unit: str, registry: UnitRegistry | None = None) -> tuple[str, str]:
    if "_per_" not in unit:
        return "", ""
    numerator, denominator = unit.split("_per_", 1)
    return base_unit(numerator, registry), base_unit(denominator, registry)


def check_unit(errors: list[str], registry: UnitRegistry, unit: str, context: str) -> None:
    if unit and not registry.knows(unit):
        errors.append(f"{context} uses unknown unit {unit!r}")


def check_cost_basis(errors: list[str], registry: UnitRegistry, unit: str, expected_unit: str, context: str) -> None:
    basis = cost_basis_unit(unit, registry)
    if not basis:
        errors.append(f"{context} cost/price unit {unit!r} must start with {CURRENCY}_per_")
    elif basis != expected_unit:
        errors.append(
            f"{context} cost/price unit {unit!r} has basis {basis!r}, expected {expected_unit!r}"
        )


def validate_input_units(input_dir: Path, registry: UnitRegistry | None = None) -> None:
    """Validate current soft-link input table unit consistency."""
    registry = registry or load_unit_registry()
    errors: list[str] = []
    commodities = {
        row["commodity_id"]: row["canonical_unit"]
        for row in read_rows(input_dir / "commodity.csv")
    }
    technologies = {
        row["technology_id"]: row
        for row in read_rows(input_dir / "technology.csv")
    }

    for commodity_id, canonical_unit in commodities.items():
        check_unit(errors, registry, canonical_unit, f"commodity {commodity_id}")

    for row in read_rows(input_dir / "supply_offer.csv"):
        commodity_id = row["commodity_id"]
        expected_unit = commodities[commodity_id]
        if row["quantity_unit"] != expected_unit:
            errors.append(
                f"supply_offer {row['supply_node_id']}:{commodity_id} quantity_unit "
                f"{row['quantity_unit']!r} must match commodity canonical unit {expected_unit!r}"
            )
        check_cost_basis(
            errors,
            registry,
            row["supply_cost_unit"],
            expected_unit,
            f"supply_offer {row['supply_node_id']}:{commodity_id}",
        )

    for row in technologies.values():
        feedstock_unit = commodities[row["feedstock_commodity_id"]]
        product_unit = commodities[row["primary_product_id"]]
        infeed_num, infeed_den = conversion_units(row["infeed_rate_unit"], registry)
        if infeed_num != feedstock_unit or infeed_den != feedstock_unit:
            errors.append(
                f"technology {row['technology_id']} infeed_rate_unit {row['infeed_rate_unit']!r} "
                f"must be {feedstock_unit}_per_{feedstock_unit}_* for feedstock "
                f"{row['feedstock_commodity_id']}"
            )
        check_cost_basis(
            errors,
            registry,
            row["operating_cost_unit"],
            feedstock_unit,
            f"technology {row['technology_id']}",
        )
        check_unit(errors, registry, product_unit, f"technology {row['technology_id']} primary product")

    for row in read_rows(input_dir / "technology_flow.csv"):
        technology = technologies[row["technology_id"]]
        output_unit = commodities[row["commodity_id"]]
        feedstock_unit = commodities[technology["feedstock_commodity_id"]]
        numerator, denominator = conversion_units(row["unit"], registry)
        if numerator != output_unit:
            errors.append(
                f"technology_flow {row['technology_id']}:{row['commodity_id']} unit {row['unit']!r} "
                f"has numerator {numerator!r}, expected output unit {output_unit!r}"
            )
        if denominator != feedstock_unit:
            errors.append(
                f"technology_flow {row['technology_id']}:{row['commodity_id']} unit {row['unit']!r} "
                f"has denominator {denominator!r}, expected feedstock unit {feedstock_unit!r}"
            )
        if row.get("basis_unit") and row["basis_unit"] != feedstock_unit:
            errors.append(
                f"technology_flow {row['technology_id']}:{row['commodity_id']} basis_unit "
                f"{row['basis_unit']!r} must match feedstock unit {feedstock_unit!r}"
            )
        if row.get("basis_commodity_id") and row["basis_commodity_id"] != technology["feedstock_commodity_id"]:
            errors.append(
                f"technology_flow {row['technology_id']}:{row['commodity_id']} basis_commodity_id "
                f"{row['basis_commodity_id']!r} must match technology feedstock "
                f"{technology['feedstock_commodity_id']!r}"
            )

    for row in read_rows(input_dir / "market.csv"):
        commodity_id = row["product_commodity_id"]
        check_cost_basis(errors, registry, row["price_unit"], commodities[commodity_id], f"market {row['market_id']}")

    export_path = input_dir / "export_sink.csv"
    if export_path.exists():
        for row in read_rows(export_path):
            commodity_id = row["commodity_id"]
            check_cost_basis(
                errors,
                registry,
                row["price_unit"],
                commodities[commodity_id],
                f"export_sink {row['export_node_id']}:{commodity_id}",
            )

    if errors:
        joined = "\n  - ".join(errors)
        raise UnitValidationError(f"Unit validation failed:\n  - {joined}")


def validate_graph_units(input_dir: Path, registry: UnitRegistry | None = None) -> None:
    """Validate generated graph arc unit fields against commodity units."""
    registry = registry or load_unit_registry()
    generated_dir = input_dir / "generated"
    commodities = {
        row["commodity_id"]: row["canonical_unit"]
        for row in read_rows(input_dir / "commodity.csv")
    }
    technologies = {
        row["technology_id"]: row
        for row in read_rows(input_dir / "technology.csv")
    }
    errors: list[str] = []
    for row in read_rows(generated_dir / "graph_arcs.csv"):
        commodity_id = row["commodity_id"]
        if not commodity_id:
            continue
        expected_unit = commodities[commodity_id]
        cost_expected_unit = expected_unit
        if row["arc_family"] == "prc_to_snk_capacity":
            technology = technologies[row["technology_id"]]
            expected_unit = commodities[technology["feedstock_commodity_id"]]
            cost_expected_unit = expected_unit
        if row["capacity_unit"] and base_unit(row["capacity_unit"], registry) != expected_unit:
            errors.append(
                f"graph_arc {row['graph_arc_id']} capacity_unit {row['capacity_unit']!r} "
                f"must match expected arc unit {expected_unit!r}"
            )
        if row["cost_unit"]:
            check_cost_basis(errors, registry, row["cost_unit"], cost_expected_unit, f"graph_arc {row['graph_arc_id']}")
        if row["price_unit"]:
            check_cost_basis(errors, registry, row["price_unit"], expected_unit, f"graph_arc {row['graph_arc_id']}")
    if errors:
        joined = "\n  - ".join(errors)
        raise UnitValidationError(f"Generated graph unit validation failed:\n  - {joined}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=Path("metadata/ffcm-archive/prototype-inputs/bc-mini"),
    )
    parser.add_argument(
        "--include-generated",
        action="store_true",
        help="Also validate generated graph arc units.",
    )
    parser.add_argument("--unit-registry", type=Path, default=DEFAULT_UNIT_REGISTRY)
    parser.add_argument("--conversion-registry", type=Path, default=DEFAULT_CONVERSION_REGISTRY)
    args = parser.parse_args()
    registry = load_unit_registry(args.unit_registry, args.conversion_registry)
    validate_input_units(args.input_dir, registry)
    if args.include_generated:
        validate_graph_units(args.input_dir, registry)
    print(f"validated units in {args.input_dir}")


if __name__ == "__main__":
    main()
