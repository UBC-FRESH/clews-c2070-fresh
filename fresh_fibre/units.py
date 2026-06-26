"""Unit and dimensional consistency checks for FRESH fibre prototypes."""

from __future__ import annotations

import argparse
from pathlib import Path

from fresh_fibre.schema import read_rows


KNOWN_UNITS = {"m3", "odt", "GJ"}
CURRENCY = "CAD"


class UnitValidationError(ValueError):
    """Raised when prototype table units are inconsistent."""


def base_unit(unit: str) -> str:
    """Return the physical base unit, dropping commodity suffixes."""
    if not unit:
        return ""
    return unit.split("_", 1)[0]


def cost_basis_unit(unit: str) -> str:
    prefix = f"{CURRENCY}_per_"
    if not unit.startswith(prefix):
        return ""
    return base_unit(unit[len(prefix):])


def conversion_units(unit: str) -> tuple[str, str]:
    if "_per_" not in unit:
        return "", ""
    numerator, denominator = unit.split("_per_", 1)
    return base_unit(numerator), base_unit(denominator)


def check_unit(errors: list[str], unit: str, context: str) -> None:
    if unit and base_unit(unit) not in KNOWN_UNITS:
        errors.append(f"{context} uses unknown unit {unit!r}")


def check_cost_basis(errors: list[str], unit: str, expected_unit: str, context: str) -> None:
    basis = cost_basis_unit(unit)
    if not basis:
        errors.append(f"{context} cost/price unit {unit!r} must start with {CURRENCY}_per_")
    elif basis != expected_unit:
        errors.append(
            f"{context} cost/price unit {unit!r} has basis {basis!r}, expected {expected_unit!r}"
        )


def validate_input_units(input_dir: Path) -> None:
    """Validate current soft-link input table unit consistency."""
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
        check_unit(errors, canonical_unit, f"commodity {commodity_id}")

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
            row["supply_cost_unit"],
            expected_unit,
            f"supply_offer {row['supply_node_id']}:{commodity_id}",
        )

    for row in technologies.values():
        feedstock_unit = commodities[row["feedstock_commodity_id"]]
        product_unit = commodities[row["primary_product_id"]]
        infeed_num, infeed_den = conversion_units(row["infeed_rate_unit"])
        if infeed_num != feedstock_unit or infeed_den != feedstock_unit:
            errors.append(
                f"technology {row['technology_id']} infeed_rate_unit {row['infeed_rate_unit']!r} "
                f"must be {feedstock_unit}_per_{feedstock_unit}_* for feedstock "
                f"{row['feedstock_commodity_id']}"
            )
        check_cost_basis(
            errors,
            row["operating_cost_unit"],
            feedstock_unit,
            f"technology {row['technology_id']}",
        )
        check_unit(errors, product_unit, f"technology {row['technology_id']} primary product")

    for row in read_rows(input_dir / "technology_flow.csv"):
        technology = technologies[row["technology_id"]]
        output_unit = commodities[row["commodity_id"]]
        feedstock_unit = commodities[technology["feedstock_commodity_id"]]
        numerator, denominator = conversion_units(row["unit"])
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
        check_cost_basis(errors, row["price_unit"], commodities[commodity_id], f"market {row['market_id']}")

    export_path = input_dir / "export_sink.csv"
    if export_path.exists():
        for row in read_rows(export_path):
            commodity_id = row["commodity_id"]
            check_cost_basis(
                errors,
                row["price_unit"],
                commodities[commodity_id],
                f"export_sink {row['export_node_id']}:{commodity_id}",
            )

    if errors:
        joined = "\n  - ".join(errors)
        raise UnitValidationError(f"Unit validation failed:\n  - {joined}")


def validate_graph_units(input_dir: Path) -> None:
    """Validate generated graph arc unit fields against commodity units."""
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
        if row["capacity_unit"] and base_unit(row["capacity_unit"]) != expected_unit:
            errors.append(
                f"graph_arc {row['graph_arc_id']} capacity_unit {row['capacity_unit']!r} "
                f"must match expected arc unit {expected_unit!r}"
            )
        if row["cost_unit"]:
            check_cost_basis(errors, row["cost_unit"], cost_expected_unit, f"graph_arc {row['graph_arc_id']}")
        if row["price_unit"]:
            check_cost_basis(errors, row["price_unit"], expected_unit, f"graph_arc {row['graph_arc_id']}")
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
    args = parser.parse_args()
    validate_input_units(args.input_dir)
    if args.include_generated:
        validate_graph_units(args.input_dir)
    print(f"validated units in {args.input_dir}")


if __name__ == "__main__":
    main()
