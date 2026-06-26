"""Build three-way comparisons across BC mini supply variants."""

from __future__ import annotations

import argparse
from pathlib import Path

from fresh_fibre import graph
from fresh_fibre.contract import VALIDATION_COLUMNS, assert_contract_ok, validate_contract
from fresh_fibre.lp import build_and_solve
from fresh_fibre.scanfi_supply import DEFAULT_VARIANT_DIR as DEFAULT_SCANFI_VARIANT_DIR
from fresh_fibre.scanfi_supply import run_scanfi_diagnostic_variant
from fresh_fibre.schema import number, read_rows, write_rows
from fresh_fibre.ws3_variant import DEFAULT_VARIANT_DIR as DEFAULT_WS3_VARIANT_DIR
from fresh_fibre.ws3_variant import run_ws3_smoke_variant


DEFAULT_BASE_DIR = Path("metadata/ffcm-archive/prototype-inputs/bc-mini")
DEFAULT_OUTPUT_DIR = Path("metadata/ffcm-archive/prototype-comparisons/bc-mini-three-way")

VARIANT_RUN_MATRIX_COLUMNS = [
    "variant_id",
    "variant_label",
    "input_dir",
    "run_id",
    "ws3_run_id",
    "data_version",
    "solver_status",
    "objective_value",
    "graph_nodes",
    "graph_arcs",
    "lp_variables",
    "lp_constraints",
    "solution_flows",
    "supply_rows",
    "notes",
]

OBJECTIVE_COMPARISON_COLUMNS = [
    "metric_id",
    "unit",
    "synthetic_bc_mini",
    "ws3_smoke",
    "scanfi_diagnostic",
    "ws3_smoke_delta_vs_synthetic",
    "scanfi_delta_vs_synthetic",
    "scanfi_delta_vs_ws3_smoke",
    "notes",
]

SUPPLY_COMPARISON_COLUMNS = [
    "commodity_id",
    "quantity_unit",
    "synthetic_bc_mini_quantity",
    "ws3_smoke_quantity",
    "scanfi_diagnostic_quantity",
    "ws3_smoke_delta_vs_synthetic",
    "scanfi_delta_vs_synthetic",
    "scanfi_delta_vs_ws3_smoke",
]

SUMMARY_COMPARISON_COLUMNS = [
    "metric_id",
    "commodity_id",
    "facility_class",
    "market_id",
    "unit",
    "synthetic_bc_mini_value",
    "ws3_smoke_value",
    "scanfi_diagnostic_value",
    "ws3_smoke_delta_vs_synthetic",
    "scanfi_delta_vs_synthetic",
    "scanfi_delta_vs_ws3_smoke",
]

FLOW_COMPARISON_COLUMNS = [
    "pathway_id",
    "origin_node_id",
    "destination_node_id",
    "source_commodity_id",
    "product_commodity_id",
    "technology_id",
    "market_id",
    "transport_link_id",
    "quantity_unit",
    "synthetic_bc_mini_quantity",
    "ws3_smoke_quantity",
    "scanfi_diagnostic_quantity",
    "ws3_smoke_delta_vs_synthetic",
    "scanfi_delta_vs_synthetic",
    "scanfi_delta_vs_ws3_smoke",
]


VARIANTS = [
    {
        "variant_id": "synthetic_bc_mini",
        "variant_label": "Base synthetic BC mini",
        "notes": "Stable synthetic BC mini supply fixture and LP solve.",
    },
    {
        "variant_id": "ws3_smoke",
        "variant_label": "Real ws3 TSA24 smoke",
        "notes": "Real local ws3 smoke harvest output mapped through diagnostic crosswalk rows.",
    },
    {
        "variant_id": "scanfi_diagnostic",
        "variant_label": "SCANFI diagnostic inventory",
        "notes": "SCANFI-provenance inventory cells with synthetic placeholder yield rules.",
    },
]


def generated_path(input_dir: Path, filename: str) -> Path:
    return input_dir / "generated" / filename


def build_base_graph_and_solution(input_dir: Path) -> dict[str, object]:
    tables = {
        table: graph.read_table(input_dir, table)
        for table in graph.TABLE_COLUMNS
    }
    graph.validate_references(tables)
    nodes, arcs = graph.build_graph(tables)
    output_dir = input_dir / "generated"
    graph.write_table(output_dir / "graph_nodes.csv", graph.NODE_COLUMNS, nodes)
    graph.write_table(output_dir / "graph_arcs.csv", graph.ARC_COLUMNS, arcs)
    graph.write_table(output_dir / "graph_summary.csv", graph.SUMMARY_COLUMNS, graph.build_summary(nodes, arcs))
    lp_result = build_and_solve(input_dir, output_dir)
    contract_rows = validate_contract(input_dir)
    write_rows(output_dir / "contract_validation.csv", VALIDATION_COLUMNS, contract_rows)
    assert_contract_ok(contract_rows)
    return {
        "status": lp_result["status"],
        "objective_value": lp_result["objective_value"],
        "graph_nodes": len(nodes),
        "graph_arcs": len(arcs),
        "variables": lp_result["variables"],
        "constraints": lp_result["constraints"],
        "solution_flows": lp_result["solution_flows"],
    }


def solution_metric(input_dir: Path, metric_id: str) -> str:
    for row in read_rows(generated_path(input_dir, "solution_summary.csv")):
        if row["metric_id"] == metric_id and not row.get("commodity_id"):
            return row["value"]
    return ""


def run_provenance(input_dir: Path) -> dict[str, str]:
    rows = read_rows(input_dir / "run_provenance.csv")
    if len(rows) != 1:
        raise ValueError(f"Expected one run_provenance row in {input_dir}, found {len(rows)}")
    return rows[0]


def variant_dirs(base_dir: Path, ws3_variant_dir: Path, scanfi_variant_dir: Path) -> dict[str, Path]:
    return {
        "synthetic_bc_mini": base_dir,
        "ws3_smoke": ws3_variant_dir,
        "scanfi_diagnostic": scanfi_variant_dir,
    }


def regenerate_variants(
    base_dir: Path,
    ws3_variant_dir: Path,
    scanfi_variant_dir: Path,
) -> dict[str, dict[str, object]]:
    results: dict[str, dict[str, object]] = {}
    results["synthetic_bc_mini"] = build_base_graph_and_solution(base_dir)
    results["ws3_smoke"] = run_ws3_smoke_variant(base_dir, ws3_variant_dir)
    results["scanfi_diagnostic"] = run_scanfi_diagnostic_variant(base_dir, variant_dir=scanfi_variant_dir)
    return results


def graph_summary_value(input_dir: Path, metric: str) -> str:
    rows = read_rows(generated_path(input_dir, "graph_summary.csv"))
    for row in rows:
        if row["metric"] == metric:
            return row["value"]
    return "0"


def variant_run_matrix(
    dirs: dict[str, Path],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for variant in VARIANTS:
        variant_id = variant["variant_id"]
        input_dir = dirs[variant_id]
        provenance = run_provenance(input_dir)
        supply_rows = read_rows(input_dir / "supply_offer.csv")
        rows.append(
            {
                "variant_id": variant_id,
                "variant_label": variant["variant_label"],
                "input_dir": str(input_dir),
                "run_id": provenance["run_id"],
                "ws3_run_id": provenance.get("ws3_run_id", ""),
                "data_version": provenance.get("data_version", ""),
                "solver_status": solution_metric(input_dir, "solver_status"),
                "objective_value": solution_metric(input_dir, "objective_value"),
                "graph_nodes": graph_summary_value(input_dir, "node_count"),
                "graph_arcs": graph_summary_value(input_dir, "arc_count"),
                "lp_variables": len(read_rows(generated_path(input_dir, "lp_variables.csv"))),
                "lp_constraints": len(read_rows(generated_path(input_dir, "lp_constraints.csv"))),
                "solution_flows": len(read_rows(generated_path(input_dir, "solution_flow.csv"))),
                "supply_rows": len(supply_rows),
                "notes": variant["notes"],
            }
        )
    return rows


def summary_key(row: dict[str, str]) -> tuple[str, str, str, str, str]:
    return (
        row["metric_id"],
        row.get("commodity_id", ""),
        row.get("facility_class", ""),
        row.get("market_id", ""),
        row["unit"],
    )


def numeric_summary_rows(input_dir: Path) -> dict[tuple[str, str, str, str, str], float]:
    result: dict[tuple[str, str, str, str, str], float] = {}
    for row in read_rows(generated_path(input_dir, "solution_summary.csv")):
        try:
            result[summary_key(row)] = number(row["value"])
        except ValueError:
            continue
    return result


def wide_metric_row(
    key: tuple[str, ...],
    values_by_variant: dict[str, dict[tuple[str, ...], float]],
    prefix_columns: dict[str, object],
) -> dict[str, object]:
    synthetic = values_by_variant["synthetic_bc_mini"].get(key, 0.0)
    ws3 = values_by_variant["ws3_smoke"].get(key, 0.0)
    scanfi = values_by_variant["scanfi_diagnostic"].get(key, 0.0)
    return {
        **prefix_columns,
        "synthetic_bc_mini_value": synthetic,
        "ws3_smoke_value": ws3,
        "scanfi_diagnostic_value": scanfi,
        "ws3_smoke_delta_vs_synthetic": ws3 - synthetic,
        "scanfi_delta_vs_synthetic": scanfi - synthetic,
        "scanfi_delta_vs_ws3_smoke": scanfi - ws3,
    }


def solution_summary_comparison(dirs: dict[str, Path]) -> list[dict[str, object]]:
    values_by_variant = {
        variant_id: numeric_summary_rows(input_dir)
        for variant_id, input_dir in dirs.items()
    }
    rows: list[dict[str, object]] = []
    for key in sorted(set().union(*[set(values) for values in values_by_variant.values()])):
        metric_id, commodity_id, facility_class, market_id, unit = key
        rows.append(
            wide_metric_row(
                key,
                values_by_variant,
                {
                    "metric_id": metric_id,
                    "commodity_id": commodity_id,
                    "facility_class": facility_class,
                    "market_id": market_id,
                    "unit": unit,
                },
            )
        )
    return rows


def objective_comparison(summary_rows: list[dict[str, object]]) -> list[dict[str, object]]:
    metric_ids = {
        "objective_value": "LP objective value.",
        "total_revenue": "Market revenue.",
        "total_supply_cost": "Supply acquisition cost.",
        "total_transport_cost": "Transport cost.",
        "total_processing_cost": "Processing cost.",
    }
    rows: list[dict[str, object]] = []
    for row in summary_rows:
        metric_id = str(row["metric_id"])
        if metric_id not in metric_ids or row["commodity_id"]:
            continue
        rows.append(
            {
                "metric_id": metric_id,
                "unit": row["unit"],
                "synthetic_bc_mini": row["synthetic_bc_mini_value"],
                "ws3_smoke": row["ws3_smoke_value"],
                "scanfi_diagnostic": row["scanfi_diagnostic_value"],
                "ws3_smoke_delta_vs_synthetic": row["ws3_smoke_delta_vs_synthetic"],
                "scanfi_delta_vs_synthetic": row["scanfi_delta_vs_synthetic"],
                "scanfi_delta_vs_ws3_smoke": row["scanfi_delta_vs_ws3_smoke"],
                "notes": metric_ids[metric_id],
            }
        )
    return rows


def supply_totals(input_dir: Path) -> dict[tuple[str, str], float]:
    totals: dict[tuple[str, str], float] = {}
    for row in read_rows(input_dir / "supply_offer.csv"):
        key = (row["commodity_id"], row["quantity_unit"])
        totals[key] = totals.get(key, 0.0) + number(row["quantity"])
    return totals


def supply_comparison(dirs: dict[str, Path]) -> list[dict[str, object]]:
    values_by_variant = {
        variant_id: supply_totals(input_dir)
        for variant_id, input_dir in dirs.items()
    }
    rows: list[dict[str, object]] = []
    for key in sorted(set().union(*[set(values) for values in values_by_variant.values()])):
        commodity_id, quantity_unit = key
        synthetic = values_by_variant["synthetic_bc_mini"].get(key, 0.0)
        ws3 = values_by_variant["ws3_smoke"].get(key, 0.0)
        scanfi = values_by_variant["scanfi_diagnostic"].get(key, 0.0)
        rows.append(
            {
                "commodity_id": commodity_id,
                "quantity_unit": quantity_unit,
                "synthetic_bc_mini_quantity": synthetic,
                "ws3_smoke_quantity": ws3,
                "scanfi_diagnostic_quantity": scanfi,
                "ws3_smoke_delta_vs_synthetic": ws3 - synthetic,
                "scanfi_delta_vs_synthetic": scanfi - synthetic,
                "scanfi_delta_vs_ws3_smoke": scanfi - ws3,
            }
        )
    return rows


def flow_key(row: dict[str, str]) -> tuple[str, str, str, str, str, str, str, str]:
    return (
        row["origin_node_id"],
        row["destination_node_id"],
        row["source_commodity_id"],
        row["product_commodity_id"],
        row.get("technology_id", ""),
        row.get("market_id", ""),
        row.get("transport_link_id", ""),
        row["quantity_unit"],
    )


def pathway_id(key: tuple[str, str, str, str, str, str, str, str]) -> str:
    origin, destination, source_commodity, product_commodity, technology, market, transport, unit = key
    parts = [origin, destination, source_commodity, product_commodity, technology, market, transport, unit]
    return "__".join(part or "na" for part in parts)


def flow_totals(input_dir: Path) -> dict[tuple[str, str, str, str, str, str, str, str], float]:
    totals: dict[tuple[str, str, str, str, str, str, str, str], float] = {}
    for row in read_rows(generated_path(input_dir, "solution_flow.csv")):
        key = flow_key(row)
        totals[key] = totals.get(key, 0.0) + number(row["quantity"])
    return totals


def pathway_flow_comparison(dirs: dict[str, Path]) -> list[dict[str, object]]:
    values_by_variant = {
        variant_id: flow_totals(input_dir)
        for variant_id, input_dir in dirs.items()
    }
    rows: list[dict[str, object]] = []
    for key in sorted(set().union(*[set(values) for values in values_by_variant.values()])):
        (
            origin_node_id,
            destination_node_id,
            source_commodity_id,
            product_commodity_id,
            technology_id,
            market_id,
            transport_link_id,
            quantity_unit,
        ) = key
        synthetic = values_by_variant["synthetic_bc_mini"].get(key, 0.0)
        ws3 = values_by_variant["ws3_smoke"].get(key, 0.0)
        scanfi = values_by_variant["scanfi_diagnostic"].get(key, 0.0)
        rows.append(
            {
                "pathway_id": pathway_id(key),
                "origin_node_id": origin_node_id,
                "destination_node_id": destination_node_id,
                "source_commodity_id": source_commodity_id,
                "product_commodity_id": product_commodity_id,
                "technology_id": technology_id,
                "market_id": market_id,
                "transport_link_id": transport_link_id,
                "quantity_unit": quantity_unit,
                "synthetic_bc_mini_quantity": synthetic,
                "ws3_smoke_quantity": ws3,
                "scanfi_diagnostic_quantity": scanfi,
                "ws3_smoke_delta_vs_synthetic": ws3 - synthetic,
                "scanfi_delta_vs_synthetic": scanfi - synthetic,
                "scanfi_delta_vs_ws3_smoke": scanfi - ws3,
            }
        )
    return rows


def write_decision_memo(output_dir: Path, matrix_rows: list[dict[str, object]]) -> None:
    matrix = {row["variant_id"]: row for row in matrix_rows}
    text = f"""# BC Mini Three-Way Variant Comparison

This memo compares the three currently runnable BC mini supply paths:

- `synthetic_bc_mini`: stable synthetic supply fixture.
- `ws3_smoke`: real local ws3 TSA24 smoke run mapped through diagnostic
  crosswalk rows.
- `scanfi_diagnostic`: SCANFI-provenance diagnostic inventory cells with
  synthetic placeholder yield rules.

All three variants solve through the same downstream LP machinery. Current
solver statuses are `{matrix['synthetic_bc_mini']['solver_status']}`,
`{matrix['ws3_smoke']['solver_status']}`, and
`{matrix['scanfi_diagnostic']['solver_status']}`.

The SCANFI branch is now useful as an inventory-provenance bridge: it carries
verified SCANFI layer references through a ws3-shaped harvest-output boundary and
into the downstream LP. It should not be interpreted as calibrated supply. The
yield rules in `diagnostic_yield_rules.csv` are placeholders, and the costs are
diagnostic assumptions.

Decision implication: keep the SCANFI path as the preferred diagnostic
inventory branch, but make the next gate a yield-policy decision rather than a
solver or network-mechanics decision.
"""
    (output_dir / "decision_memo.md").write_text(text, encoding="utf-8")


def compare_three_way(
    base_dir: Path = DEFAULT_BASE_DIR,
    ws3_variant_dir: Path = DEFAULT_WS3_VARIANT_DIR,
    scanfi_variant_dir: Path = DEFAULT_SCANFI_VARIANT_DIR,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    regenerate: bool = True,
) -> dict[str, object]:
    if regenerate:
        regenerate_variants(base_dir, ws3_variant_dir, scanfi_variant_dir)
    dirs = variant_dirs(base_dir, ws3_variant_dir, scanfi_variant_dir)
    matrix_rows = variant_run_matrix(dirs)
    summary_rows = solution_summary_comparison(dirs)
    objective_rows = objective_comparison(summary_rows)
    supply_rows = supply_comparison(dirs)
    flow_rows = pathway_flow_comparison(dirs)

    write_rows(output_dir / "variant_run_matrix.csv", VARIANT_RUN_MATRIX_COLUMNS, matrix_rows)
    write_rows(output_dir / "variant_objective_comparison.csv", OBJECTIVE_COMPARISON_COLUMNS, objective_rows)
    write_rows(output_dir / "variant_supply_comparison.csv", SUPPLY_COMPARISON_COLUMNS, supply_rows)
    write_rows(output_dir / "variant_solution_summary_comparison.csv", SUMMARY_COMPARISON_COLUMNS, summary_rows)
    write_rows(output_dir / "variant_pathway_flow_comparison.csv", FLOW_COMPARISON_COLUMNS, flow_rows)
    write_decision_memo(output_dir, matrix_rows)

    return {
        "output_dir": str(output_dir),
        "variant_rows": len(matrix_rows),
        "objective_rows": len(objective_rows),
        "supply_rows": len(supply_rows),
        "summary_rows": len(summary_rows),
        "flow_rows": len(flow_rows),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-dir", type=Path, default=DEFAULT_BASE_DIR)
    parser.add_argument("--ws3-variant-dir", type=Path, default=DEFAULT_WS3_VARIANT_DIR)
    parser.add_argument("--scanfi-variant-dir", type=Path, default=DEFAULT_SCANFI_VARIANT_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--skip-regenerate", action="store_true")
    args = parser.parse_args()
    result = compare_three_way(
        args.base_dir,
        args.ws3_variant_dir,
        args.scanfi_variant_dir,
        args.output_dir,
        regenerate=not args.skip_regenerate,
    )
    print(
        f"wrote three-way comparison to {result['output_dir']}: "
        f"{result['variant_rows']} variants, "
        f"{result['objective_rows']} objective rows, "
        f"{result['supply_rows']} supply rows, "
        f"{result['flow_rows']} flow rows"
    )
