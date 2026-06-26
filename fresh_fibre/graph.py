#!/usr/bin/env python3
"""Build solver-independent graph tables from FRESH prototype inputs."""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from fresh_fibre.schema import number as num
from fresh_fibre.schema import read_rows, require_columns, write_rows


TABLE_COLUMNS = {
    "scenario": {"scenario_id", "start_period", "end_period"},
    "commodity": {"commodity_id", "canonical_unit"},
    "supply_offer": {
        "scenario_id",
        "period",
        "spatial_unit_id",
        "supply_node_id",
        "commodity_id",
        "quantity",
        "quantity_unit",
        "supply_cost",
        "supply_cost_unit",
    },
    "network_node": {"node_id", "node_type", "active_from_period", "active_to_period"},
    "technology": {
        "technology_id",
        "facility_node_id",
        "feedstock_commodity_id",
        "primary_product_id",
        "operating_cost",
        "operating_cost_unit",
        "maximum_capacity",
    },
    "technology_flow": {
        "technology_id",
        "flow_type",
        "commodity_id",
        "coefficient_or_quantity",
        "unit",
    },
    "transport_link": {
        "scenario_id",
        "period",
        "origin_node_id",
        "destination_node_id",
        "transport_mode",
        "transport_cost",
        "transport_cost_unit",
        "capacity",
    },
    "market": {
        "market_id",
        "product_commodity_id",
        "maximum_demand",
        "price",
        "price_unit",
    },
}


NODE_COLUMNS = [
    "graph_node_id",
    "node_layer",
    "scenario_id",
    "period",
    "source_table",
    "source_id",
    "node_type",
    "commodity_id",
    "facility_node_id",
    "technology_id",
    "label",
]


ARC_COLUMNS = [
    "graph_arc_id",
    "arc_family",
    "scenario_id",
    "period",
    "origin_graph_node_id",
    "destination_graph_node_id",
    "commodity_id",
    "source_node_id",
    "destination_node_id",
    "technology_id",
    "market_id",
    "transport_link_id",
    "capacity",
    "capacity_unit",
    "coefficient",
    "cost",
    "cost_unit",
    "price",
    "price_unit",
    "source_ref",
]


SUMMARY_COLUMNS = ["metric", "value"]


def read_table(input_dir: Path, table: str) -> list[dict[str, str]]:
    path = input_dir / f"{table}.csv"
    if not path.exists():
        raise FileNotFoundError(f"Missing required table: {path}")
    rows = read_rows(path)
    require_columns(path, rows, TABLE_COLUMNS[table])
    return rows


def write_table(path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
    write_rows(path, fieldnames, rows)


def period_key(row: dict[str, str]) -> tuple[str, str]:
    return row["scenario_id"], row["period"]


def node(
    graph_node_id: str,
    node_layer: str,
    scenario_id: str,
    period: str,
    source_table: str,
    source_id: str,
    node_type: str,
    label: str,
    commodity_id: str = "",
    facility_node_id: str = "",
    technology_id: str = "",
) -> dict[str, object]:
    return {
        "graph_node_id": graph_node_id,
        "node_layer": node_layer,
        "scenario_id": scenario_id,
        "period": period,
        "source_table": source_table,
        "source_id": source_id,
        "node_type": node_type,
        "commodity_id": commodity_id,
        "facility_node_id": facility_node_id,
        "technology_id": technology_id,
        "label": label,
    }


def arc(
    graph_arc_id: str,
    arc_family: str,
    scenario_id: str,
    period: str,
    origin: str,
    destination: str,
    commodity_id: str,
    source_ref: str,
    source_node_id: str = "",
    destination_node_id: str = "",
    technology_id: str = "",
    market_id: str = "",
    transport_link_id: str = "",
    capacity: object = "",
    capacity_unit: str = "",
    coefficient: object = "",
    cost: object = "",
    cost_unit: str = "",
    price: object = "",
    price_unit: str = "",
) -> dict[str, object]:
    return {
        "graph_arc_id": graph_arc_id,
        "arc_family": arc_family,
        "scenario_id": scenario_id,
        "period": period,
        "origin_graph_node_id": origin,
        "destination_graph_node_id": destination,
        "commodity_id": commodity_id,
        "source_node_id": source_node_id,
        "destination_node_id": destination_node_id,
        "technology_id": technology_id,
        "market_id": market_id,
        "transport_link_id": transport_link_id,
        "capacity": capacity,
        "capacity_unit": capacity_unit,
        "coefficient": coefficient,
        "cost": cost,
        "cost_unit": cost_unit,
        "price": price,
        "price_unit": price_unit,
        "source_ref": source_ref,
    }


def validate_references(tables: dict[str, list[dict[str, str]]]) -> None:
    scenario_ids = {row["scenario_id"] for row in tables["scenario"]}
    commodity_ids = {row["commodity_id"] for row in tables["commodity"]}
    node_ids = {row["node_id"] for row in tables["network_node"]}
    technology_ids = {row["technology_id"] for row in tables["technology"]}

    errors: list[str] = []
    for row in tables["supply_offer"]:
        if row["scenario_id"] not in scenario_ids:
            errors.append(f"supply_offer references unknown scenario {row['scenario_id']}")
        if row["supply_node_id"] not in node_ids:
            errors.append(f"supply_offer references unknown node {row['supply_node_id']}")
        if row["commodity_id"] not in commodity_ids:
            errors.append(f"supply_offer references unknown commodity {row['commodity_id']}")

    for row in tables["technology"]:
        if row["facility_node_id"] not in node_ids:
            errors.append(f"technology references unknown facility {row['facility_node_id']}")
        for column in ("feedstock_commodity_id", "primary_product_id"):
            if row[column] not in commodity_ids:
                errors.append(f"technology references unknown commodity {row[column]}")

    for row in tables["technology_flow"]:
        if row["technology_id"] not in technology_ids:
            errors.append(f"technology_flow references unknown technology {row['technology_id']}")
        if row["commodity_id"] not in commodity_ids:
            errors.append(f"technology_flow references unknown commodity {row['commodity_id']}")

    for row in tables["transport_link"]:
        if row["scenario_id"] not in scenario_ids:
            errors.append(f"transport_link references unknown scenario {row['scenario_id']}")
        for column in ("origin_node_id", "destination_node_id"):
            if row[column] not in node_ids:
                errors.append(f"transport_link references unknown node {row[column]}")

    for row in tables["market"]:
        if row["product_commodity_id"] not in commodity_ids:
            errors.append(f"market references unknown commodity {row['product_commodity_id']}")

    if errors:
        joined = "\n  - ".join(errors)
        raise ValueError(f"Reference validation failed:\n  - {joined}")


def build_graph(tables: dict[str, list[dict[str, str]]]) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    supply_offers = tables["supply_offer"]
    technologies = tables["technology"]
    technology_flows = tables["technology_flow"]
    transport_links = tables["transport_link"]
    markets = tables["market"]

    commodity_units = {
        row["commodity_id"]: row["canonical_unit"] for row in tables["commodity"]
    }
    flows_by_technology: dict[str, list[dict[str, str]]] = {}
    for row in technology_flows:
        flows_by_technology.setdefault(row["technology_id"], []).append(row)

    links_by_origin: dict[tuple[str, str, str], list[dict[str, str]]] = {}
    for row in transport_links:
        key = (row["scenario_id"], row["period"], row["origin_node_id"])
        links_by_origin.setdefault(key, []).append(row)

    nodes_by_id: dict[str, dict[str, object]] = {}
    arcs: list[dict[str, object]] = []

    def add_node(row: dict[str, object]) -> str:
        nodes_by_id.setdefault(str(row["graph_node_id"]), row)
        return str(row["graph_node_id"])

    for offer in supply_offers:
        scenario_id, period = period_key(offer)
        source_ref = (
            f"supply_offer:{offer['scenario_id']}:{offer['period']}:"
            f"{offer['supply_node_id']}:{offer['commodity_id']}"
        )
        src_id = add_node(
            node(
                f"src__{offer['supply_node_id']}__{offer['commodity_id']}",
                "src",
                scenario_id,
                period,
                "supply_offer",
                offer["supply_node_id"],
                "supply_offer",
                f"{offer['supply_node_id']} {offer['commodity_id']}",
                commodity_id=offer["commodity_id"],
            )
        )
        dsp_id = add_node(
            node(
                f"dsp__{offer['supply_node_id']}__{offer['commodity_id']}",
                "dsp",
                scenario_id,
                period,
                "supply_offer",
                offer["supply_node_id"],
                "dispatch",
                f"dispatch {offer['supply_node_id']} {offer['commodity_id']}",
                commodity_id=offer["commodity_id"],
            )
        )
        cdt_id = add_node(
            node(
                f"cdt__{scenario_id}__{period}__{offer['commodity_id']}",
                "cdt",
                scenario_id,
                period,
                "commodity",
                offer["commodity_id"],
                "commodity_pool",
                f"{offer['commodity_id']} pool",
                commodity_id=offer["commodity_id"],
            )
        )

        arcs.append(
            arc(
                f"arc__src_dsp__{offer['supply_node_id']}__{offer['commodity_id']}",
                "src_to_dsp_supply_bound",
                scenario_id,
                period,
                src_id,
                dsp_id,
                offer["commodity_id"],
                source_ref,
                source_node_id=offer["supply_node_id"],
                capacity=offer["quantity"],
                capacity_unit=offer["quantity_unit"],
                coefficient=1.0,
                cost=offer["supply_cost"],
                cost_unit=offer["supply_cost_unit"],
            )
        )
        arcs.append(
            arc(
                f"arc__dsp_cdt__{offer['supply_node_id']}__{offer['commodity_id']}",
                "dsp_to_cdt_commodity_release",
                scenario_id,
                period,
                dsp_id,
                cdt_id,
                offer["commodity_id"],
                source_ref,
                source_node_id=offer["supply_node_id"],
                coefficient=1.0,
            )
        )

    for tech in technologies:
        technology_id = tech["technology_id"]
        prc_id = add_node(
            node(
                f"prc__{technology_id}",
                "prc",
                "",
                "",
                "technology",
                technology_id,
                "processor",
                technology_id,
                commodity_id=tech["feedstock_commodity_id"],
                facility_node_id=tech["facility_node_id"],
                technology_id=technology_id,
            )
        )
        facility_snk_id = add_node(
            node(
                f"snk__facility__{tech['facility_node_id']}__{technology_id}",
                "snk",
                "",
                "",
                "network_node",
                tech["facility_node_id"],
                "facility_sink",
                f"{tech['facility_node_id']} capacity sink",
                facility_node_id=tech["facility_node_id"],
                technology_id=technology_id,
            )
        )
        arcs.append(
            arc(
                f"arc__prc_snk_capacity__{technology_id}",
                "prc_to_snk_capacity",
                "",
                "",
                prc_id,
                facility_snk_id,
                tech["primary_product_id"],
                f"technology:{technology_id}",
                destination_node_id=tech["facility_node_id"],
                technology_id=technology_id,
                capacity=tech["maximum_capacity"],
                capacity_unit=commodity_units.get(tech["feedstock_commodity_id"], ""),
                coefficient=1.0,
                cost=tech["operating_cost"],
                cost_unit=tech["operating_cost_unit"],
            )
        )

        for flow in flows_by_technology.get(technology_id, []):
            product_cdt_id = add_node(
                node(
                    f"cdt__product__{flow['commodity_id']}",
                    "cdt",
                    "",
                    "",
                    "commodity",
                    flow["commodity_id"],
                    "commodity_pool",
                    f"{flow['commodity_id']} product pool",
                    commodity_id=flow["commodity_id"],
                )
            )
            arcs.append(
                arc(
                    f"arc__prc_cdt_output__{technology_id}__{flow['commodity_id']}",
                    f"prc_to_cdt_{flow['flow_type']}",
                    "",
                    "",
                    prc_id,
                    product_cdt_id,
                    flow["commodity_id"],
                    f"technology_flow:{technology_id}:{flow['commodity_id']}",
                    destination_node_id=tech["facility_node_id"],
                    technology_id=technology_id,
                    coefficient=flow["coefficient_or_quantity"],
                    capacity_unit=flow["unit"],
                )
            )

    for offer in supply_offers:
        scenario_id, period = period_key(offer)
        cdt_id = f"cdt__{scenario_id}__{period}__{offer['commodity_id']}"
        for link in links_by_origin.get((scenario_id, period, offer["supply_node_id"]), []):
            destination_techs = [
                tech
                for tech in technologies
                if tech["facility_node_id"] == link["destination_node_id"]
                and tech["feedstock_commodity_id"] == offer["commodity_id"]
            ]
            for tech in destination_techs:
                arcs.append(
                    arc(
                        (
                            f"arc__cdt_prc__{offer['supply_node_id']}__"
                            f"{tech['technology_id']}__{offer['commodity_id']}"
                        ),
                        "cdt_to_prc_feedstock_allocation",
                        scenario_id,
                        period,
                        cdt_id,
                        f"prc__{tech['technology_id']}",
                        offer["commodity_id"],
                        f"transport_link:{link['route_id']}",
                        source_node_id=offer["supply_node_id"],
                        destination_node_id=tech["facility_node_id"],
                        technology_id=tech["technology_id"],
                        transport_link_id=link["route_id"],
                        capacity=link["capacity"],
                        capacity_unit=offer["quantity_unit"],
                        coefficient=1.0,
                        cost=link["transport_cost"],
                        cost_unit=link["transport_cost_unit"],
                    )
                )

    for source_tech in technologies:
        for flow in flows_by_technology.get(source_tech["technology_id"], []):
            commodity_id = flow["commodity_id"]
            product_cdt_id = f"cdt__product__{commodity_id}"
            for link in transport_links:
                if link["origin_node_id"] != source_tech["facility_node_id"]:
                    continue
                destination_techs = [
                    tech
                    for tech in technologies
                    if tech["facility_node_id"] == link["destination_node_id"]
                    and tech["feedstock_commodity_id"] == commodity_id
                ]
                for destination_tech in destination_techs:
                    arcs.append(
                        arc(
                            (
                                f"arc__cdt_prc_secondary__{source_tech['technology_id']}__"
                                f"{destination_tech['technology_id']}__{commodity_id}__"
                                f"{link['route_id']}"
                            ),
                            "cdt_to_prc_secondary_allocation",
                            link["scenario_id"],
                            link["period"],
                            product_cdt_id,
                            f"prc__{destination_tech['technology_id']}",
                            commodity_id,
                            f"transport_link:{link['route_id']}",
                            source_node_id=source_tech["facility_node_id"],
                            destination_node_id=destination_tech["facility_node_id"],
                            technology_id=destination_tech["technology_id"],
                            transport_link_id=link["route_id"],
                            capacity=link["capacity"],
                            capacity_unit=commodity_units.get(commodity_id, ""),
                            coefficient=1.0,
                            cost=link["transport_cost"],
                            cost_unit=link["transport_cost_unit"],
                        )
                    )

    market_arc_keys: set[tuple[str, str]] = set()
    for tech in technologies:
        for flow in flows_by_technology.get(tech["technology_id"], []):
            market_key = flow["commodity_id"]
            product_cdt_id = f"cdt__product__{flow['commodity_id']}"
            matching_markets = [
                market
                for market in markets
                if market["product_commodity_id"] == flow["commodity_id"]
            ]
            for market in matching_markets:
                dedupe_key = (market_key, market["market_id"])
                if dedupe_key in market_arc_keys:
                    continue
                market_arc_keys.add(dedupe_key)
                market_snk_id = add_node(
                    node(
                        f"snk__market__{market['market_id']}",
                        "snk",
                        "",
                        "",
                        "market",
                        market["market_id"],
                        "market_sink",
                        market["market_id"],
                        commodity_id=market["product_commodity_id"],
                    )
                )
                arcs.append(
                    arc(
                        f"arc__cdt_snk_market__{flow['commodity_id']}__{market['market_id']}",
                        "cdt_to_snk_market_demand",
                        "",
                        "",
                        product_cdt_id,
                        market_snk_id,
                        flow["commodity_id"],
                        f"market:{market['market_id']}",
                        market_id=market["market_id"],
                        capacity=market["maximum_demand"],
                        capacity_unit=commodity_units.get(flow["commodity_id"], ""),
                        coefficient=1.0,
                        price=market["price"],
                        price_unit=market["price_unit"],
                    )
                )

    nodes = sorted(nodes_by_id.values(), key=lambda row: str(row["graph_node_id"]))
    return nodes, arcs


def build_summary(nodes: list[dict[str, object]], arcs: list[dict[str, object]]) -> list[dict[str, object]]:
    node_layers = Counter(str(row["node_layer"]) for row in nodes)
    arc_families = Counter(str(row["arc_family"]) for row in arcs)
    summary: list[dict[str, object]] = [
        {"metric": "node_count", "value": len(nodes)},
        {"metric": "arc_count", "value": len(arcs)},
    ]
    summary.extend(
        {"metric": f"node_layer:{layer}", "value": count}
        for layer, count in sorted(node_layers.items())
    )
    summary.extend(
        {"metric": f"arc_family:{family}", "value": count}
        for family, count in sorted(arc_families.items())
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=Path("metadata/ffcm-archive/prototype-inputs/bc-mini"),
    )
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()

    input_dir = args.input_dir
    output_dir = args.output_dir or input_dir / "generated"

    tables = {
        table: read_table(input_dir, table)
        for table in TABLE_COLUMNS
    }
    validate_references(tables)
    nodes, arcs = build_graph(tables)
    summary = build_summary(nodes, arcs)

    write_table(output_dir / "graph_nodes.csv", NODE_COLUMNS, nodes)
    write_table(output_dir / "graph_arcs.csv", ARC_COLUMNS, arcs)
    write_table(output_dir / "graph_summary.csv", SUMMARY_COLUMNS, summary)

    print(f"wrote {len(nodes)} nodes and {len(arcs)} arcs to {output_dir}")


if __name__ == "__main__":
    main()
