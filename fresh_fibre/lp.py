#!/usr/bin/env python3
"""Solve the synthetic BC mini-network LP prototype."""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

try:
    import pulp
except ImportError:  # pragma: no cover - exercised only when dependency missing.
    print(
        "PuLP is required. Install prototype dependencies with "
        "`python3 -m pip install -r requirements-prototype.txt`.",
        file=sys.stderr,
    )
    raise

from fresh_fibre.schema import (
    Commodity,
    GraphArc,
    Market,
    NetworkNode,
    RunProvenance,
    Scenario,
    SupplyOffer,
    Technology,
    TechnologyFlow,
    number,
    read_rows,
    write_rows,
)
from fresh_fibre.units import validate_graph_units, validate_input_units


DEFAULT_INPUT_DIR = Path("metadata/ffcm-archive/prototype-inputs/bc-mini")

VARIABLE_COLUMNS = [
    "variable_id",
    "variable_family",
    "lower_bound",
    "upper_bound",
    "quantity_unit",
    "source_ref",
]

CONSTRAINT_COLUMNS = [
    "constraint_id",
    "constraint_family",
    "sense",
    "rhs",
    "unit",
    "description",
]

OBJECTIVE_COLUMNS = [
    "variable_id",
    "objective_component",
    "coefficient",
    "coefficient_unit",
    "source_ref",
]

SOLUTION_FLOW_COLUMNS = [
    "run_id",
    "period",
    "origin_node_id",
    "destination_node_id",
    "source_commodity_id",
    "product_commodity_id",
    "quantity",
    "quantity_unit",
    "objective_contribution",
    "technology_id",
    "market_id",
    "transport_link_id",
    "reduced_cost",
    "shadow_price_ref",
]

SOLUTION_SUMMARY_COLUMNS = [
    "run_id",
    "period",
    "region_id",
    "metric_id",
    "value",
    "unit",
    "commodity_id",
    "facility_class",
    "market_id",
    "scenario_comparison_id",
]


def clean_id(value: str) -> str:
    return "".join(char if char.isalnum() else "_" for char in value)


def variable_id(prefix: str, key: str) -> str:
    return f"{prefix}__{clean_id(key)}"


def positive(value: float, tolerance: float = 1e-7) -> bool:
    return value > tolerance


def add_objective_row(
    rows: list[dict[str, object]],
    variable_name: str,
    component: str,
    coefficient: float,
    unit: str,
    source_ref: str,
) -> None:
    rows.append(
        {
            "variable_id": variable_name,
            "objective_component": component,
            "coefficient": coefficient,
            "coefficient_unit": unit,
            "source_ref": source_ref,
        }
    )


def single_row(rows: list[dict[str, str]], table_name: str) -> dict[str, str]:
    if len(rows) != 1:
        raise ValueError(f"Expected one row in {table_name}, found {len(rows)}")
    return rows[0]


def solve_region(nodes: list[NetworkNode]) -> str:
    provinces = sorted({node.province for node in nodes if node.province})
    if len(provinces) == 1:
        return provinces[0]
    if not provinces:
        return "unknown"
    return "multi_region"


def build_and_solve(input_dir: Path, output_dir: Path) -> dict[str, object]:
    validate_input_units(input_dir)
    validate_graph_units(input_dir)
    graph_dir = input_dir / "generated"
    scenario = Scenario.from_row(single_row(read_rows(input_dir / "scenario.csv"), "scenario.csv"))
    provenance = RunProvenance.from_row(
        single_row(read_rows(input_dir / "run_provenance.csv"), "run_provenance.csv")
    )
    if provenance.scenario_id != scenario.scenario_id:
        raise ValueError(
            f"run_provenance scenario {provenance.scenario_id!r} does not match "
            f"scenario table {scenario.scenario_id!r}"
        )
    run_id = provenance.output_run_id
    solve_period = scenario.start_period
    region_id = solve_region(
        [NetworkNode.from_row(row) for row in read_rows(input_dir / "network_node.csv")]
    )
    commodities = {
        row.commodity_id: row
        for row in (Commodity.from_row(row) for row in read_rows(input_dir / "commodity.csv"))
    }
    markets = {
        row.market_id: row
        for row in (Market.from_row(row) for row in read_rows(input_dir / "market.csv"))
    }
    technologies = {
        row.technology_id: row
        for row in (Technology.from_row(row) for row in read_rows(input_dir / "technology.csv"))
    }
    technology_flows = [
        TechnologyFlow.from_row(row)
        for row in read_rows(input_dir / "technology_flow.csv")
    ]
    graph_arcs = [
        GraphArc.from_row(row)
        for row in read_rows(graph_dir / "graph_arcs.csv")
    ]
    supply_offers = {
        (row.supply_node_id, row.commodity_id): row
        for row in (SupplyOffer.from_row(row) for row in read_rows(input_dir / "supply_offer.csv"))
    }
    flows_by_technology: dict[str, list[TechnologyFlow]] = defaultdict(list)
    for row in technology_flows:
        flows_by_technology[row.technology_id].append(row)

    primary_arcs = [
        row for row in graph_arcs
        if row.arc_family == "cdt_to_prc_feedstock_allocation"
    ]
    secondary_arcs = [
        row for row in graph_arcs
        if row.arc_family == "cdt_to_prc_secondary_allocation"
    ]

    problem = pulp.LpProblem(clean_id(f"fresh_{scenario.scenario_id}_network_flow"), pulp.LpMaximize)
    variables: dict[str, pulp.LpVariable] = {}
    variable_rows: list[dict[str, object]] = []
    constraint_rows: list[dict[str, object]] = []
    objective_rows: list[dict[str, object]] = []
    objective_terms: list[pulp.LpAffineExpression] = []

    supply_vars: dict[tuple[str, str], pulp.LpVariable] = {}
    primary_vars: dict[str, pulp.LpVariable] = {}
    secondary_vars: dict[str, pulp.LpVariable] = {}
    activity_vars: dict[str, pulp.LpVariable] = {}
    market_vars: dict[str, pulp.LpVariable] = {}

    def add_variable(
        name: str,
        family: str,
        lower_bound: float,
        upper_bound: float | None,
        quantity_unit: str,
        source_ref: str,
    ) -> pulp.LpVariable:
        var = pulp.LpVariable(name, lowBound=lower_bound, upBound=upper_bound)
        variables[name] = var
        variable_rows.append(
            {
                "variable_id": name,
                "variable_family": family,
                "lower_bound": lower_bound,
                "upper_bound": "" if upper_bound is None else upper_bound,
                "quantity_unit": quantity_unit,
                "source_ref": source_ref,
            }
        )
        return var

    def add_constraint(
        constraint: pulp.LpConstraint,
        constraint_id: str,
        family: str,
        sense: str,
        rhs: float,
        unit: str,
        description: str,
    ) -> None:
        problem.addConstraint(constraint, constraint_id)
        constraint_rows.append(
            {
                "constraint_id": constraint_id,
                "constraint_family": family,
                "sense": sense,
                "rhs": rhs,
                "unit": unit,
                "description": description,
            }
        )

    for offer in supply_offers.values():
        key = (offer.supply_node_id, offer.commodity_id)
        name = variable_id("supply_use", "__".join(key))
        var = add_variable(
            name,
            "supply_use",
            0.0,
            offer.quantity,
            offer.quantity_unit,
            f"supply_offer:{offer.supply_node_id}:{offer.commodity_id}",
        )
        supply_vars[key] = var
        coefficient = -offer.supply_cost
        objective_terms.append(coefficient * var)
        add_objective_row(
            objective_rows,
            name,
            "supply_cost",
            coefficient,
            offer.supply_cost_unit,
            f"supply_offer:{offer.supply_node_id}:{offer.commodity_id}",
        )

    for arc in primary_arcs:
        name = variable_id("ship_primary", arc.graph_arc_id)
        var = add_variable(
            name,
            "primary_feedstock_shipment",
            0.0,
            arc.capacity,
            arc.capacity_unit,
            arc.source_ref,
        )
        primary_vars[arc.graph_arc_id] = var
        coefficient = -arc.cost
        objective_terms.append(coefficient * var)
        add_objective_row(
            objective_rows,
            name,
            "transport_cost",
            coefficient,
            arc.cost_unit,
            arc.source_ref,
        )

    for arc in secondary_arcs:
        name = variable_id("ship_secondary", arc.graph_arc_id)
        var = add_variable(
            name,
            "secondary_residue_shipment",
            0.0,
            arc.capacity,
            arc.capacity_unit,
            arc.source_ref,
        )
        secondary_vars[arc.graph_arc_id] = var
        coefficient = -arc.cost
        objective_terms.append(coefficient * var)
        add_objective_row(
            objective_rows,
            name,
            "transport_cost",
            coefficient,
            arc.cost_unit,
            arc.source_ref,
        )

    for technology in technologies.values():
        name = variable_id("activity", technology.technology_id)
        var = add_variable(
            name,
            "technology_activity",
            technology.minimum_production,
            technology.maximum_capacity,
            commodities[technology.feedstock_commodity_id].canonical_unit,
            f"technology:{technology.technology_id}",
        )
        activity_vars[technology.technology_id] = var
        coefficient = -technology.operating_cost
        objective_terms.append(coefficient * var)
        add_objective_row(
            objective_rows,
            name,
            "processing_cost",
            coefficient,
            technology.operating_cost_unit,
            f"technology:{technology.technology_id}",
        )

    for market in markets.values():
        commodity_id = market.product_commodity_id
        name = variable_id("market_sale", market.market_id)
        var = add_variable(
            name,
            "market_sale",
            market.minimum_demand,
            market.maximum_demand,
            commodities[commodity_id].canonical_unit,
            f"market:{market.market_id}",
        )
        market_vars[market.market_id] = var
        coefficient = market.price
        objective_terms.append(coefficient * var)
        add_objective_row(
            objective_rows,
            name,
            "market_revenue",
            coefficient,
            market.price_unit,
            f"market:{market.market_id}",
        )

    problem += pulp.lpSum(objective_terms), "net_value"

    primary_by_supply: dict[tuple[str, str], list[pulp.LpVariable]] = defaultdict(list)
    primary_by_technology: dict[str, list[pulp.LpVariable]] = defaultdict(list)
    for arc in primary_arcs:
        key = (arc.source_node_id, arc.commodity_id)
        primary_by_supply[key].append(primary_vars[arc.graph_arc_id])
        primary_by_technology[arc.technology_id].append(primary_vars[arc.graph_arc_id])
        add_constraint(
            primary_vars[arc.graph_arc_id] <= arc.capacity,
            f"route_capacity__{clean_id(arc.graph_arc_id)}",
            "route_capacity",
            "<=",
            arc.capacity,
            arc.capacity_unit,
            f"Route capacity for {arc.transport_link_id}",
        )

    secondary_by_technology: dict[str, list[pulp.LpVariable]] = defaultdict(list)
    secondary_consumption_by_commodity: dict[str, list[pulp.LpVariable]] = defaultdict(list)
    for arc in secondary_arcs:
        var = secondary_vars[arc.graph_arc_id]
        secondary_by_technology[arc.technology_id].append(var)
        secondary_consumption_by_commodity[arc.commodity_id].append(var)
        add_constraint(
            var <= arc.capacity,
            f"route_capacity__{clean_id(arc.graph_arc_id)}",
            "route_capacity",
            "<=",
            arc.capacity,
            arc.capacity_unit,
            f"Route capacity for {arc.transport_link_id}",
        )

    for key, supply_var in supply_vars.items():
        add_constraint(
            supply_var == pulp.lpSum(primary_by_supply.get(key, [])),
            f"supply_dispatch_balance__{clean_id('__'.join(key))}",
            "supply_dispatch_balance",
            "=",
            0.0,
            supply_offers[key].quantity_unit,
            "Supply used must be dispatched to primary feedstock arcs.",
        )

    for technology_id, technology in technologies.items():
        inbound = primary_by_technology.get(technology_id, []) + secondary_by_technology.get(technology_id, [])
        add_constraint(
            activity_vars[technology_id] == pulp.lpSum(inbound),
            f"technology_activity_balance__{clean_id(technology_id)}",
            "technology_activity_balance",
            "=",
            0.0,
            commodities[technology.feedstock_commodity_id].canonical_unit,
            "Technology activity equals inbound feedstock.",
        )
        add_constraint(
            activity_vars[technology_id] <= technology.maximum_capacity,
            f"technology_capacity__{clean_id(technology_id)}",
            "technology_capacity",
            "<=",
            technology.maximum_capacity,
            commodities[technology.feedstock_commodity_id].canonical_unit,
            "Technology activity cannot exceed maximum capacity.",
        )

    output_by_commodity: dict[str, list[pulp.LpAffineExpression]] = defaultdict(list)
    for technology_id, flows in flows_by_technology.items():
        for flow in flows:
            output_by_commodity[flow.commodity_id].append(
                flow.coefficient_or_quantity * activity_vars[technology_id]
            )

    sales_by_commodity: dict[str, list[pulp.LpVariable]] = defaultdict(list)
    for market_id, var in market_vars.items():
        market = markets[market_id]
        sales_by_commodity[market.product_commodity_id].append(var)
        add_constraint(
            var <= market.maximum_demand,
            f"market_demand__{clean_id(market_id)}",
            "market_demand",
            "<=",
            market.maximum_demand,
            commodities[market.product_commodity_id].canonical_unit,
            "Market sales cannot exceed maximum demand.",
        )

    balanced_commodities = sorted(set(output_by_commodity) | set(sales_by_commodity) | set(secondary_consumption_by_commodity))
    for commodity_id in balanced_commodities:
        produced = pulp.lpSum(output_by_commodity.get(commodity_id, []))
        consumed = pulp.lpSum(sales_by_commodity.get(commodity_id, [])) + pulp.lpSum(
            secondary_consumption_by_commodity.get(commodity_id, [])
        )
        add_constraint(
            produced == consumed,
            f"commodity_balance__{clean_id(commodity_id)}",
            "commodity_balance",
            "=",
            0.0,
            commodities[commodity_id].canonical_unit,
            "Produced product/byproduct volume must be sold or consumed downstream.",
        )

    solver = pulp.PULP_CBC_CMD(msg=False)
    status_code = problem.solve(solver)
    status = pulp.LpStatus[status_code]
    objective_value = pulp.value(problem.objective)
    if objective_value is None:
        objective_value = 0.0

    solution_rows: list[dict[str, object]] = []
    summary_rows: list[dict[str, object]] = []

    def var_value(var: pulp.LpVariable) -> float:
        value = var.varValue
        return 0.0 if value is None else float(value)

    def objective_contribution(var: pulp.LpVariable, component: str) -> float:
        coefficient = sum(
            number(row["coefficient"])
            for row in objective_rows
            if row["variable_id"] == var.name and row["objective_component"] == component
        )
        return coefficient * var_value(var)

    def add_solution_flow(
        var: pulp.LpVariable,
        period: str,
        origin_node_id: str,
        destination_node_id: str,
        source_commodity_id: str,
        product_commodity_id: str,
        quantity_unit: str,
        objective_component: str,
        technology_id: str = "",
        market_id: str = "",
        transport_link_id: str = "",
    ) -> None:
        value = var_value(var)
        if not positive(value):
            return
        solution_rows.append(
            {
                "run_id": run_id,
                "period": period,
                "origin_node_id": origin_node_id,
                "destination_node_id": destination_node_id,
                "source_commodity_id": source_commodity_id,
                "product_commodity_id": product_commodity_id,
                "quantity": value,
                "quantity_unit": quantity_unit,
                "objective_contribution": objective_contribution(var, objective_component),
                "technology_id": technology_id,
                "market_id": market_id,
                "transport_link_id": transport_link_id,
                "reduced_cost": "",
                "shadow_price_ref": "",
            }
        )

    for (source_node_id, commodity_id), var in supply_vars.items():
        offer = supply_offers[(source_node_id, commodity_id)]
        add_solution_flow(
            var,
            offer.period,
            source_node_id,
            source_node_id,
            commodity_id,
            commodity_id,
            offer.quantity_unit,
            "supply_cost",
        )

    for arc in primary_arcs:
        tech = technologies[arc.technology_id]
        add_solution_flow(
            primary_vars[arc.graph_arc_id],
            arc.period,
            arc.source_node_id,
            arc.destination_node_id,
            arc.commodity_id,
            tech.primary_product_id,
            arc.capacity_unit,
            "transport_cost",
            technology_id=arc.technology_id,
            transport_link_id=arc.transport_link_id,
        )

    for arc in secondary_arcs:
        tech = technologies[arc.technology_id]
        add_solution_flow(
            secondary_vars[arc.graph_arc_id],
            arc.period,
            arc.source_node_id,
            arc.destination_node_id,
            arc.commodity_id,
            tech.primary_product_id,
            arc.capacity_unit,
            "transport_cost",
            technology_id=arc.technology_id,
            transport_link_id=arc.transport_link_id,
        )

    for technology_id, var in activity_vars.items():
        technology = technologies[technology_id]
        add_solution_flow(
            var,
            "0",
            technology.facility_node_id,
            technology.facility_node_id,
            technology.feedstock_commodity_id,
            technology.primary_product_id,
            commodities[technology.feedstock_commodity_id].canonical_unit,
            "processing_cost",
            technology_id=technology_id,
        )

    for market_id, var in market_vars.items():
        market = markets[market_id]
        add_solution_flow(
            var,
            "0",
            f"product_pool:{market.product_commodity_id}",
            market_id,
            market.product_commodity_id,
            market.product_commodity_id,
            commodities[market.product_commodity_id].canonical_unit,
            "market_revenue",
            market_id=market_id,
        )

    component_totals = defaultdict(float)
    for row in objective_rows:
        var = variables[str(row["variable_id"])]
        component_totals[str(row["objective_component"])] += number(row["coefficient"]) * var_value(var)

    def add_summary(metric_id: str, value: object, unit: str, commodity_id: str = "", market_id: str = "") -> None:
        summary_rows.append(
            {
                "run_id": run_id,
                "period": solve_period,
                "region_id": region_id,
                "metric_id": metric_id,
                "value": value,
                "unit": unit,
                "commodity_id": commodity_id,
                "facility_class": "",
                "market_id": market_id,
                "scenario_comparison_id": "",
            }
        )

    add_summary("solver_status", status, "status")
    add_summary("scenario_id", scenario.scenario_id, "id")
    add_summary("scenario_name", scenario.scenario_name, "label")
    add_summary("code_version", provenance.code_version, "version")
    add_summary("data_version", provenance.data_version, "version")
    add_summary("ws3_run_id", provenance.ws3_run_id, "id")
    add_summary("solver_name", provenance.solver_name or "PULP_CBC_CMD", "solver")
    add_summary("solver_version", provenance.solver_version, "version")
    add_summary("objective_value", objective_value, "CAD")
    add_summary("total_revenue", component_totals["market_revenue"], "CAD")
    add_summary("total_supply_cost", -component_totals["supply_cost"], "CAD")
    add_summary("total_transport_cost", -component_totals["transport_cost"], "CAD")
    add_summary("total_processing_cost", -component_totals["processing_cost"], "CAD")

    market_quantity_by_commodity = defaultdict(float)
    for market_id, var in market_vars.items():
        commodity_id = markets[market_id].product_commodity_id
        market_quantity_by_commodity[commodity_id] += var_value(var)
    for commodity_id, quantity in sorted(market_quantity_by_commodity.items()):
        add_summary(
            "total_market_quantity",
            quantity,
            commodities[commodity_id].canonical_unit,
            commodity_id=commodity_id,
        )

    write_rows(output_dir / "lp_variables.csv", VARIABLE_COLUMNS, variable_rows)
    write_rows(output_dir / "lp_constraints.csv", CONSTRAINT_COLUMNS, constraint_rows)
    write_rows(output_dir / "objective_terms.csv", OBJECTIVE_COLUMNS, objective_rows)
    write_rows(output_dir / "solution_flow.csv", SOLUTION_FLOW_COLUMNS, solution_rows)
    write_rows(output_dir / "solution_summary.csv", SOLUTION_SUMMARY_COLUMNS, summary_rows)

    return {
        "status": status,
        "objective_value": objective_value,
        "variables": len(variable_rows),
        "constraints": len(constraint_rows),
        "solution_flows": len(solution_rows),
        "output_dir": output_dir,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    output_dir = args.output_dir or args.input_dir / "generated"

    result = build_and_solve(args.input_dir, output_dir)
    print(
        "solved {status} objective={objective_value:.2f}; "
        "wrote {variables} variables, {constraints} constraints, and "
        "{solution_flows} positive solution flows to {output_dir}".format(**result)
    )
    if result["status"] != "Optimal":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
