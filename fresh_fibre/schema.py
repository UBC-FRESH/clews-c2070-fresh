"""Typed adapters for lightweight FRESH fibre CSV tables."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_rows(path: Path, fieldnames: list[str], rows: Iterable[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=fieldnames,
            extrasaction="ignore",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def number(value: str | float | int | None, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    return float(value)


def require_columns(path: Path, rows: list[dict[str, str]], required: set[str]) -> None:
    if not rows:
        raise ValueError(f"Table has no data rows: {path}")
    missing = required - set(rows[0])
    if missing:
        raise ValueError(f"{path} is missing required columns: {sorted(missing)}")


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    scenario_name: str
    start_period: str
    end_period: str

    @classmethod
    def from_row(cls, row: dict[str, str]) -> "Scenario":
        return cls(
            row["scenario_id"],
            row["scenario_name"],
            row["start_period"],
            row["end_period"],
        )


@dataclass(frozen=True)
class RunProvenance:
    run_id: str
    scenario_id: str
    code_version: str
    data_version: str
    ws3_run_id: str
    lp_run_id: str
    solver_name: str
    solver_version: str

    @property
    def output_run_id(self) -> str:
        return self.lp_run_id or self.run_id

    @classmethod
    def from_row(cls, row: dict[str, str]) -> "RunProvenance":
        return cls(
            row["run_id"],
            row["scenario_id"],
            row.get("code_version", ""),
            row.get("data_version", ""),
            row.get("ws3_run_id", ""),
            row.get("lp_run_id", ""),
            row.get("solver_name", ""),
            row.get("solver_version", ""),
        )


@dataclass(frozen=True)
class NetworkNode:
    node_id: str
    node_type: str
    province: str

    @classmethod
    def from_row(cls, row: dict[str, str]) -> "NetworkNode":
        return cls(row["node_id"], row["node_type"], row["province"])


@dataclass(frozen=True)
class Commodity:
    commodity_id: str
    canonical_unit: str

    @classmethod
    def from_row(cls, row: dict[str, str]) -> "Commodity":
        return cls(row["commodity_id"], row["canonical_unit"])


@dataclass(frozen=True)
class SupplyOffer:
    scenario_id: str
    period: str
    supply_node_id: str
    commodity_id: str
    quantity: float
    quantity_unit: str
    supply_cost: float
    supply_cost_unit: str

    @classmethod
    def from_row(cls, row: dict[str, str]) -> "SupplyOffer":
        return cls(
            row["scenario_id"],
            row["period"],
            row["supply_node_id"],
            row["commodity_id"],
            number(row["quantity"]),
            row["quantity_unit"],
            number(row["supply_cost"]),
            row["supply_cost_unit"],
        )


@dataclass(frozen=True)
class Technology:
    technology_id: str
    facility_node_id: str
    feedstock_commodity_id: str
    primary_product_id: str
    operating_cost: float
    operating_cost_unit: str
    minimum_production: float
    maximum_capacity: float

    @classmethod
    def from_row(cls, row: dict[str, str]) -> "Technology":
        return cls(
            row["technology_id"],
            row["facility_node_id"],
            row["feedstock_commodity_id"],
            row["primary_product_id"],
            number(row["operating_cost"]),
            row["operating_cost_unit"],
            number(row.get("minimum_production")),
            number(row["maximum_capacity"]),
        )


@dataclass(frozen=True)
class TechnologyFlow:
    technology_id: str
    flow_type: str
    commodity_id: str
    coefficient_or_quantity: float
    unit: str
    basis_commodity_id: str
    basis_unit: str

    @classmethod
    def from_row(cls, row: dict[str, str]) -> "TechnologyFlow":
        return cls(
            row["technology_id"],
            row["flow_type"],
            row["commodity_id"],
            number(row["coefficient_or_quantity"]),
            row["unit"],
            row.get("basis_commodity_id", ""),
            row.get("basis_unit", ""),
        )


@dataclass(frozen=True)
class TransportLink:
    route_id: str
    capacity: float

    @classmethod
    def from_row(cls, row: dict[str, str]) -> "TransportLink":
        return cls(row["route_id"], number(row["capacity"]))


@dataclass(frozen=True)
class Market:
    market_id: str
    product_commodity_id: str
    minimum_demand: float
    maximum_demand: float
    price: float
    price_unit: str

    @classmethod
    def from_row(cls, row: dict[str, str]) -> "Market":
        return cls(
            row["market_id"],
            row["product_commodity_id"],
            number(row.get("minimum_demand")),
            number(row["maximum_demand"]),
            number(row["price"]),
            row["price_unit"],
        )


@dataclass(frozen=True)
class GraphArc:
    graph_arc_id: str
    arc_family: str
    scenario_id: str
    period: str
    commodity_id: str
    source_node_id: str
    destination_node_id: str
    technology_id: str
    market_id: str
    transport_link_id: str
    capacity: float
    capacity_unit: str
    coefficient: float
    cost: float
    cost_unit: str
    price: float
    price_unit: str
    source_ref: str

    @classmethod
    def from_row(cls, row: dict[str, str]) -> "GraphArc":
        return cls(
            row["graph_arc_id"],
            row["arc_family"],
            row["scenario_id"],
            row["period"],
            row["commodity_id"],
            row["source_node_id"],
            row["destination_node_id"],
            row["technology_id"],
            row["market_id"],
            row["transport_link_id"],
            number(row.get("capacity")),
            row["capacity_unit"],
            number(row.get("coefficient"), 1.0),
            number(row.get("cost")),
            row["cost_unit"],
            number(row.get("price")),
            row["price_unit"],
            row["source_ref"],
        )


@dataclass(frozen=True)
class SolutionFlow:
    run_id: str
    period: str
    origin_node_id: str
    destination_node_id: str
    source_commodity_id: str
    product_commodity_id: str
    quantity: float
    quantity_unit: str
    technology_id: str
    market_id: str
    transport_link_id: str

    @classmethod
    def from_row(cls, row: dict[str, str]) -> "SolutionFlow":
        return cls(
            row["run_id"],
            row["period"],
            row["origin_node_id"],
            row["destination_node_id"],
            row["source_commodity_id"],
            row["product_commodity_id"],
            number(row["quantity"]),
            row["quantity_unit"],
            row["technology_id"],
            row["market_id"],
            row["transport_link_id"],
        )
