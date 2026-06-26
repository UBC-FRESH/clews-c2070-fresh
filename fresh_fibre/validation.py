#!/usr/bin/env python3
"""Validate generated BC mini-network graph and LP solution artifacts."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from pathlib import Path

from fresh_fibre.schema import (
    Market,
    SolutionFlow,
    SupplyOffer,
    Technology,
    TransportLink,
    number,
    read_rows,
)
from fresh_fibre.units import validate_graph_units, validate_input_units

DEFAULT_INPUT_DIR = Path("metadata/ffcm-archive/prototype-inputs/bc-mini")


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def assert_no_duplicates(rows: list[dict[str, str]], column: str, label: str) -> None:
    duplicates = [value for value, count in Counter(row[column] for row in rows).items() if count > 1]
    assert_true(not duplicates, f"{label} has duplicate {column}: {duplicates}")


def has_positive_flow(rows: list[SolutionFlow], **criteria: str) -> bool:
    for row in rows:
        if row.quantity <= 1e-7:
            continue
        if all(getattr(row, key) == value for key, value in criteria.items()):
            return True
    return False


def validate(input_dir: Path) -> None:
    validate_input_units(input_dir)
    validate_graph_units(input_dir)
    generated_dir = input_dir / "generated"
    graph_nodes = read_rows(generated_dir / "graph_nodes.csv")
    graph_arcs = read_rows(generated_dir / "graph_arcs.csv")
    variables = read_rows(generated_dir / "lp_variables.csv")
    solution_flow = [
        SolutionFlow.from_row(row)
        for row in read_rows(generated_dir / "solution_flow.csv")
    ]
    solution_summary = read_rows(generated_dir / "solution_summary.csv")
    supply_offer = [
        SupplyOffer.from_row(row)
        for row in read_rows(input_dir / "supply_offer.csv")
    ]
    technology = [
        Technology.from_row(row)
        for row in read_rows(input_dir / "technology.csv")
    ]
    market = [
        Market.from_row(row)
        for row in read_rows(input_dir / "market.csv")
    ]
    transport_link = [
        TransportLink.from_row(row)
        for row in read_rows(input_dir / "transport_link.csv")
    ]

    assert_true(len(graph_nodes) == 37, f"expected 37 graph nodes, found {len(graph_nodes)}")
    assert_true(len(graph_arcs) == 45, f"expected 45 graph arcs, found {len(graph_arcs)}")
    assert_no_duplicates(graph_nodes, "graph_node_id", "graph_nodes")
    assert_no_duplicates(graph_arcs, "graph_arc_id", "graph_arcs")
    assert_no_duplicates(variables, "variable_id", "lp_variables")

    status = [
        row["value"] for row in solution_summary
        if row["metric_id"] == "solver_status"
    ]
    assert_true(status == ["Optimal"], f"expected solver status Optimal, found {status}")

    for row in solution_flow:
        assert_true(row.quantity >= -1e-7, f"negative flow quantity: {row}")

    supply_bounds = {
        (row.supply_node_id, row.commodity_id): row.quantity
        for row in supply_offer
    }
    supply_used: dict[tuple[str, str], float] = defaultdict(float)
    for row in solution_flow:
        if row.origin_node_id == row.destination_node_id and not row.technology_id:
            supply_used[(row.origin_node_id, row.source_commodity_id)] += row.quantity
    for key, quantity in supply_used.items():
        assert_true(quantity <= supply_bounds[key] + 1e-7, f"supply bound exceeded for {key}")

    route_bounds = {row.route_id: row.capacity for row in transport_link}
    route_used: dict[str, float] = defaultdict(float)
    for row in solution_flow:
        if row.transport_link_id:
            route_used[row.transport_link_id] += row.quantity
    for route_id, quantity in route_used.items():
        assert_true(quantity <= route_bounds[route_id] + 1e-7, f"route bound exceeded for {route_id}")

    technology_bounds = {row.technology_id: row.maximum_capacity for row in technology}
    technology_activity: dict[str, float] = defaultdict(float)
    for row in solution_flow:
        if row.technology_id and not row.transport_link_id and not row.market_id:
            technology_activity[row.technology_id] += row.quantity
    for technology_id, quantity in technology_activity.items():
        assert_true(
            quantity <= technology_bounds[technology_id] + 1e-7,
            f"technology capacity exceeded for {technology_id}",
        )

    market_bounds = {row.market_id: row.maximum_demand for row in market}
    market_sales: dict[str, float] = defaultdict(float)
    for row in solution_flow:
        if row.market_id:
            market_sales[row.market_id] += row.quantity
    for market_id, quantity in market_sales.items():
        assert_true(quantity <= market_bounds[market_id] + 1e-7, f"market bound exceeded for {market_id}")

    assert_true(
        has_positive_flow(
            solution_flow,
            source_commodity_id="log",
            product_commodity_id="lumber",
            technology_id="tech_sawmill_interior",
        ),
        "expected positive log-to-sawmill pathway",
    )
    assert_true(
        has_positive_flow(
            solution_flow,
            source_commodity_id="woodchips",
            product_commodity_id="pulp",
            technology_id="tech_pulp_chips",
        ),
        "expected positive sawmill-residue-to-pulp pathway",
    )
    assert_true(
        has_positive_flow(
            solution_flow,
            source_commodity_id="sawdust_shavings",
            product_commodity_id="pellets",
            technology_id="tech_pellet_residue",
        ),
        "expected positive sawmill-residue-to-pellets pathway",
    )
    assert_true(
        has_positive_flow(
            solution_flow,
            source_commodity_id="hogfuel",
            product_commodity_id="bioenergy",
            technology_id="tech_bioenergy_hogfuel",
        ),
        "expected positive sawmill-residue-to-bioenergy pathway",
    )
    assert_true(
        has_positive_flow(
            solution_flow,
            source_commodity_id="logging_residue",
            product_commodity_id="bioenergy",
            technology_id="tech_bioenergy_logging_residue",
        ),
        "expected positive logging-residue-to-bioenergy pathway",
    )
    assert_true(
        has_positive_flow(
            solution_flow,
            source_commodity_id="log",
            product_commodity_id="log",
            market_id="market_log_export",
        ),
        "expected positive profitable log-export pathway",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR)
    args = parser.parse_args()
    validate(args.input_dir)
    print(f"validated BC mini-network solution in {args.input_dir / 'generated'}")


if __name__ == "__main__":
    main()
