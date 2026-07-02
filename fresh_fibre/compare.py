"""Compare two generated FRESH fibre LP solution bundles."""

from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

from fresh_fibre.schema import number, read_rows, write_rows


DEFAULT_BASE_DIR = Path("metadata/ffcm-archive/prototype-inputs/bc-mini")
DEFAULT_VARIANT_DIR = Path("metadata/ffcm-archive/prototype-inputs/bc-mini-ws3-smoke")
DEFAULT_OUTPUT_DIR = Path("metadata/ffcm-archive/prototype-comparisons/bc-mini-vs-ws3-smoke")

SUMMARY_COMPARISON_COLUMNS = [
    "comparison_id",
    "metric_id",
    "commodity_id",
    "facility_class",
    "market_id",
    "unit",
    "base_value",
    "variant_value",
    "delta",
    "ratio_variant_to_base",
    "base_run_id",
    "variant_run_id",
    "notes",
]

FLOW_COMPARISON_COLUMNS = [
    "comparison_id",
    "origin_node_id",
    "destination_node_id",
    "source_commodity_id",
    "product_commodity_id",
    "technology_id",
    "market_id",
    "transport_link_id",
    "quantity_unit",
    "base_quantity",
    "variant_quantity",
    "quantity_delta",
    "base_objective_contribution",
    "variant_objective_contribution",
    "objective_delta",
    "base_run_id",
    "variant_run_id",
]


def ratio(variant: float, base: float) -> str:
    if abs(base) < 1e-12:
        return ""
    return str(variant / base)


def generated_path(input_dir: Path, filename: str) -> Path:
    return input_dir / "generated" / filename


def run_id(rows: list[dict[str, str]]) -> str:
    values = sorted({row["run_id"] for row in rows if row.get("run_id")})
    if len(values) != 1:
        raise ValueError(f"Expected one run_id, found {values}")
    return values[0]


def summary_key(row: dict[str, str]) -> tuple[str, str, str, str, str]:
    return (
        row["metric_id"],
        row.get("commodity_id", ""),
        row.get("facility_class", ""),
        row.get("market_id", ""),
        row["unit"],
    )


def comparable_summary_value(row: dict[str, str]) -> float | None:
    try:
        return number(row["value"])
    except ValueError:
        return None


def compare_solution_summary(
    base_dir: Path,
    variant_dir: Path,
    comparison_id: str,
) -> list[dict[str, object]]:
    base_rows = read_rows(generated_path(base_dir, "solution_summary.csv"))
    variant_rows = read_rows(generated_path(variant_dir, "solution_summary.csv"))
    base_run_id = run_id(base_rows)
    variant_run_id = run_id(variant_rows)
    base_by_key = {
        summary_key(row): comparable_summary_value(row)
        for row in base_rows
        if comparable_summary_value(row) is not None
    }
    variant_by_key = {
        summary_key(row): comparable_summary_value(row)
        for row in variant_rows
        if comparable_summary_value(row) is not None
    }
    rows: list[dict[str, object]] = []
    for key in sorted(set(base_by_key) | set(variant_by_key)):
        base_value = base_by_key.get(key, 0.0) or 0.0
        variant_value = variant_by_key.get(key, 0.0) or 0.0
        metric_id, commodity_id, facility_class, market_id, unit = key
        rows.append(
            {
                "comparison_id": comparison_id,
                "metric_id": metric_id,
                "commodity_id": commodity_id,
                "facility_class": facility_class,
                "market_id": market_id,
                "unit": unit,
                "base_value": base_value,
                "variant_value": variant_value,
                "delta": variant_value - base_value,
                "ratio_variant_to_base": ratio(variant_value, base_value),
                "base_run_id": base_run_id,
                "variant_run_id": variant_run_id,
                "notes": "Non-numeric metadata rows are excluded.",
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


def aggregate_flows(rows: list[dict[str, str]]) -> dict[tuple[str, str, str, str, str, str, str, str], dict[str, float]]:
    result: dict[tuple[str, str, str, str, str, str, str, str], dict[str, float]] = defaultdict(
        lambda: {"quantity": 0.0, "objective_contribution": 0.0}
    )
    for row in rows:
        key = flow_key(row)
        result[key]["quantity"] += number(row["quantity"])
        result[key]["objective_contribution"] += number(row["objective_contribution"])
    return result


def compare_solution_flow(
    base_dir: Path,
    variant_dir: Path,
    comparison_id: str,
) -> list[dict[str, object]]:
    base_rows = read_rows(generated_path(base_dir, "solution_flow.csv"))
    variant_rows = read_rows(generated_path(variant_dir, "solution_flow.csv"))
    base_run_id = run_id(base_rows)
    variant_run_id = run_id(variant_rows)
    base_by_key = aggregate_flows(base_rows)
    variant_by_key = aggregate_flows(variant_rows)
    rows: list[dict[str, object]] = []
    for key in sorted(set(base_by_key) | set(variant_by_key)):
        base_value = base_by_key.get(key, {"quantity": 0.0, "objective_contribution": 0.0})
        variant_value = variant_by_key.get(key, {"quantity": 0.0, "objective_contribution": 0.0})
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
        rows.append(
            {
                "comparison_id": comparison_id,
                "origin_node_id": origin_node_id,
                "destination_node_id": destination_node_id,
                "source_commodity_id": source_commodity_id,
                "product_commodity_id": product_commodity_id,
                "technology_id": technology_id,
                "market_id": market_id,
                "transport_link_id": transport_link_id,
                "quantity_unit": quantity_unit,
                "base_quantity": base_value["quantity"],
                "variant_quantity": variant_value["quantity"],
                "quantity_delta": variant_value["quantity"] - base_value["quantity"],
                "base_objective_contribution": base_value["objective_contribution"],
                "variant_objective_contribution": variant_value["objective_contribution"],
                "objective_delta": (
                    variant_value["objective_contribution"] - base_value["objective_contribution"]
                ),
                "base_run_id": base_run_id,
                "variant_run_id": variant_run_id,
            }
        )
    return rows


def compare_runs(
    base_dir: Path = DEFAULT_BASE_DIR,
    variant_dir: Path = DEFAULT_VARIANT_DIR,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    comparison_id: str = "bc_mini_vs_ws3_smoke",
) -> dict[str, object]:
    summary_rows = compare_solution_summary(base_dir, variant_dir, comparison_id)
    flow_rows = compare_solution_flow(base_dir, variant_dir, comparison_id)
    write_rows(output_dir / "solution_summary_comparison.csv", SUMMARY_COMPARISON_COLUMNS, summary_rows)
    write_rows(output_dir / "solution_flow_comparison.csv", FLOW_COMPARISON_COLUMNS, flow_rows)
    return {
        "comparison_id": comparison_id,
        "summary_rows": len(summary_rows),
        "flow_rows": len(flow_rows),
        "output_dir": str(output_dir),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-dir", type=Path, default=DEFAULT_BASE_DIR)
    parser.add_argument("--variant-dir", type=Path, default=DEFAULT_VARIANT_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--comparison-id", default="bc_mini_vs_ws3_smoke")
    args = parser.parse_args()
    result = compare_runs(args.base_dir, args.variant_dir, args.output_dir, args.comparison_id)
    print(
        f"compared {result['comparison_id']}: "
        f"{result['summary_rows']} summary rows, {result['flow_rows']} flow rows; "
        f"wrote {result['output_dir']}"
    )


if __name__ == "__main__":
    main()
