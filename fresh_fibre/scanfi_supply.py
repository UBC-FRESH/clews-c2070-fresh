"""Build SCANFI-derived diagnostic harvest output and LP supply variants."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from fresh_fibre import graph
from fresh_fibre.contract import VALIDATION_COLUMNS, assert_contract_ok, validate_contract
from fresh_fibre.lp import build_and_solve
from fresh_fibre.schema import number, read_rows, require_columns, write_rows
from fresh_fibre.ws3_export import WS3_HARVEST_FIELDNAMES, export_supply_offer


DEFAULT_BASE_INPUT_DIR = Path("metadata/ffcm-archive/prototype-inputs/bc-mini")
DEFAULT_SCANFI_INPUT_DIR = Path("metadata/ffcm-archive/prototype-inputs/scanfi-diagnostic")
DEFAULT_INVENTORY_PATH = DEFAULT_SCANFI_INPUT_DIR / "diagnostic_inventory_cells.csv"
DEFAULT_YIELD_RULES_PATH = DEFAULT_SCANFI_INPUT_DIR / "diagnostic_yield_rules.csv"
DEFAULT_VARIANT_DIR = Path("metadata/ffcm-archive/prototype-inputs/bc-mini-scanfi-diagnostic")
VARIANT_RUN_ID = "bc_mini_scanfi_diagnostic_lp_001"
WS3_RUN_ID = "scanfi_diagnostic_supply_001"
SOURCE_MODEL = "scanfi_v2_2025_diagnostic_inventory_fixture"
VALUE_POLICY = "synthetic_placeholder_yield_not_calibrated"

YIELD_RULE_COLUMNS = [
    "yield_rule_id",
    "diagnostic_region",
    "commodity_id",
    "quantity_unit",
    "period_available_quantity_per_ha",
    "supply_cost",
    "supply_cost_unit",
    "roadside_cost",
    "delivered_cost_proxy",
    "species_group",
    "assortment_class",
    "moisture_basis",
    "value_policy",
    "notes",
]

YIELD_RULES = [
    {
        "yield_rule_id": "scanfi_diag_yield_coast_wet_log",
        "diagnostic_region": "coast_wet_forest",
        "commodity_id": "log",
        "quantity_unit": "m3",
        "period_available_quantity_per_ha": 0.65,
        "supply_cost": 57,
        "supply_cost_unit": "CAD_per_m3",
        "roadside_cost": 41,
        "delivered_cost_proxy": 73,
        "species_group": "softwood",
        "assortment_class": "sawlog",
        "moisture_basis": "green",
        "notes": "Synthetic period-available log yield for coast wet forest proxy cells.",
    },
    {
        "yield_rule_id": "scanfi_diag_yield_coast_second_log",
        "diagnostic_region": "coast_second_growth",
        "commodity_id": "log",
        "quantity_unit": "m3",
        "period_available_quantity_per_ha": 0.5,
        "supply_cost": 55,
        "supply_cost_unit": "CAD_per_m3",
        "roadside_cost": 39,
        "delivered_cost_proxy": 70,
        "species_group": "softwood",
        "assortment_class": "sawlog",
        "moisture_basis": "green",
        "notes": "Synthetic period-available log yield for coast second-growth proxy cells.",
    },
    {
        "yield_rule_id": "scanfi_diag_yield_int_dry_log",
        "diagnostic_region": "interior_dry_forest",
        "commodity_id": "log",
        "quantity_unit": "m3",
        "period_available_quantity_per_ha": 0.75,
        "supply_cost": 50,
        "supply_cost_unit": "CAD_per_m3",
        "roadside_cost": 36,
        "delivered_cost_proxy": 67,
        "species_group": "softwood",
        "assortment_class": "sawlog",
        "moisture_basis": "green",
        "notes": "Synthetic period-available log yield for interior dry forest proxy cells.",
    },
    {
        "yield_rule_id": "scanfi_diag_yield_int_cold_log",
        "diagnostic_region": "interior_cold_forest",
        "commodity_id": "log",
        "quantity_unit": "m3",
        "period_available_quantity_per_ha": 0.5,
        "supply_cost": 52,
        "supply_cost_unit": "CAD_per_m3",
        "roadside_cost": 38,
        "delivered_cost_proxy": 69,
        "species_group": "softwood",
        "assortment_class": "sawlog",
        "moisture_basis": "green",
        "notes": "Synthetic period-available log yield for older interior proxy cells.",
    },
    {
        "yield_rule_id": "scanfi_diag_yield_int_salvage_log",
        "diagnostic_region": "interior_disturbance_mix",
        "commodity_id": "log",
        "quantity_unit": "m3",
        "period_available_quantity_per_ha": 0.7,
        "supply_cost": 43,
        "supply_cost_unit": "CAD_per_m3",
        "roadside_cost": 30,
        "delivered_cost_proxy": 62,
        "species_group": "mixed",
        "assortment_class": "smallwood",
        "moisture_basis": "green",
        "notes": "Synthetic period-available log yield for disturbance-mix proxy cells.",
    },
    {
        "yield_rule_id": "scanfi_diag_yield_int_residue",
        "diagnostic_region": "interior_residue_candidate",
        "commodity_id": "logging_residue",
        "quantity_unit": "odt",
        "period_available_quantity_per_ha": 0.55,
        "supply_cost": 23,
        "supply_cost_unit": "CAD_per_odt",
        "roadside_cost": 17,
        "delivered_cost_proxy": 33,
        "species_group": "mixed",
        "assortment_class": "residue",
        "moisture_basis": "dry",
        "notes": "Synthetic period-available logging residue yield for residue proxy cells.",
    },
]

SCANFI_HARVEST_FIELDNAMES = [
    *WS3_HARVEST_FIELDNAMES,
    "inventory_cell_id",
    "diagnostic_region",
    "inventory_year",
    "landcover_class",
    "inventory_value_policy",
    "yield_rule_id",
    "yield_value_per_ha",
    "yield_value_unit",
    "yield_value_policy",
    "biomass_source_file",
    "height_source_file",
    "closure_source_file",
    "landcover_source_file",
    "age_source_file",
]

REQUIRED_INVENTORY_COLUMNS = {
    "inventory_cell_id",
    "scenario_id",
    "period",
    "spatial_unit_id",
    "supply_node_id",
    "diagnostic_region",
    "representative_area_ha",
    "inventory_year",
    "landcover_class",
    "value_policy",
    "biomass_source_file",
    "height_source_file",
    "closure_source_file",
    "landcover_source_file",
    "age_source_file",
}


def format_number(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".")


def yield_rule_rows() -> list[dict[str, object]]:
    return [{**row, "value_policy": VALUE_POLICY} for row in YIELD_RULES]


def write_yield_rules(path: Path) -> list[dict[str, object]]:
    rows = yield_rule_rows()
    validate_yield_rules(rows)
    write_rows(path, YIELD_RULE_COLUMNS, rows)
    return rows


def validate_yield_rules(rows: list[dict[str, object]]) -> None:
    seen_regions: set[str] = set()
    for row in rows:
        region = str(row["diagnostic_region"])
        if region in seen_regions:
            raise ValueError(f"Duplicate diagnostic yield rule for region {region}")
        seen_regions.add(region)
        if number(row["period_available_quantity_per_ha"]) < 0:
            raise ValueError(f"Negative yield for region {region}")
        if number(row["supply_cost"]) < 0:
            raise ValueError(f"Negative supply cost for region {region}")
        if row["value_policy"] != VALUE_POLICY:
            raise ValueError(f"Unexpected yield value policy for region {region}")


def validate_inventory_references(rows: list[dict[str, str]], input_dir: Path) -> None:
    spatial_unit_ids = {row["spatial_unit_id"] for row in read_rows(input_dir / "spatial_unit.csv")}
    supply_node_ids = {
        row["node_id"]
        for row in read_rows(input_dir / "network_node.csv")
        if row["node_type"] == "supply"
    }

    errors: list[str] = []
    for row in rows:
        label = row["inventory_cell_id"]
        if row["spatial_unit_id"] not in spatial_unit_ids:
            errors.append(f"{label} references unknown spatial unit {row['spatial_unit_id']}")
        if row["supply_node_id"] not in supply_node_ids:
            errors.append(f"{label} references unknown supply node {row['supply_node_id']}")
    if errors:
        joined = "\n  - ".join(errors)
        raise ValueError(f"SCANFI inventory reference validation failed:\n  - {joined}")


def build_harvest_rows(
    inventory_path: Path,
    yield_rules_path: Path,
    input_dir: Path,
) -> list[dict[str, object]]:
    inventory_rows = read_rows(inventory_path)
    require_columns(inventory_path, inventory_rows, REQUIRED_INVENTORY_COLUMNS)
    validate_inventory_references(inventory_rows, input_dir)

    yield_rows = read_rows(yield_rules_path)
    require_columns(yield_rules_path, yield_rows, set(YIELD_RULE_COLUMNS))
    validate_yield_rules(yield_rows)
    rules_by_region = {row["diagnostic_region"]: row for row in yield_rows}

    cell_rows: list[dict[str, object]] = []
    for index, cell in enumerate(inventory_rows, start=1):
        region = cell["diagnostic_region"]
        if region not in rules_by_region:
            raise ValueError(f"No diagnostic yield rule for region {region}")
        rule = rules_by_region[region]
        quantity = number(cell["representative_area_ha"]) * number(
            rule["period_available_quantity_per_ha"]
        )
        record_id = cell["inventory_cell_id"]
        cell_rows.append(
            {
                "ws3_run_id": WS3_RUN_ID,
                "scenario_id": "bc_mini_001",
                "period": cell["period"],
                "spatial_unit_id": cell["spatial_unit_id"],
                "supply_node_id": cell["supply_node_id"],
                "commodity_id": rule["commodity_id"],
                "quantity": format_number(quantity),
                "quantity_unit": rule["quantity_unit"],
                "supply_cost": rule["supply_cost"],
                "supply_cost_unit": rule["supply_cost_unit"],
                "species_group": rule["species_group"],
                "assortment_class": rule["assortment_class"],
                "grade": "scanfi_diagnostic",
                "moisture_basis": rule["moisture_basis"],
                "carbon_factor": "",
                "harvest_action_id": f"scanfi_diag_harvest_{index:03d}",
                "roadside_cost": rule["roadside_cost"],
                "delivered_cost_proxy": rule["delivered_cost_proxy"],
                "source_model": SOURCE_MODEL,
                "source_record_id": f"{WS3_RUN_ID}:{record_id}",
                "notes": (
                    "Synthetic SCANFI diagnostic supply row; quantity is "
                    "representative_area_ha times period_available_quantity_per_ha; "
                    "not calibrated and not sampled from raw rasters."
                ),
                "inventory_cell_id": record_id,
                "diagnostic_region": region,
                "inventory_year": cell["inventory_year"],
                "landcover_class": cell["landcover_class"],
                "inventory_value_policy": cell["value_policy"],
                "yield_rule_id": rule["yield_rule_id"],
                "yield_value_per_ha": rule["period_available_quantity_per_ha"],
                "yield_value_unit": f"{rule['quantity_unit']}_per_ha",
                "yield_value_policy": rule["value_policy"],
                "biomass_source_file": cell["biomass_source_file"],
                "height_source_file": cell["height_source_file"],
                "closure_source_file": cell["closure_source_file"],
                "landcover_source_file": cell["landcover_source_file"],
                "age_source_file": cell["age_source_file"],
            }
        )
    harvest_rows = aggregate_cell_rows_for_lp(cell_rows)
    validate_harvest_rows(harvest_rows)
    return harvest_rows


def aggregate_cell_rows_for_lp(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[tuple[str, str, str, str, str, str], list[dict[str, object]]] = {}
    for row in rows:
        key = (
            str(row["scenario_id"]),
            str(row["period"]),
            str(row["spatial_unit_id"]),
            str(row["supply_node_id"]),
            str(row["commodity_id"]),
            str(row["quantity_unit"]),
        )
        grouped.setdefault(key, []).append(row)

    aggregated_rows: list[dict[str, object]] = []
    for index, (key, group) in enumerate(sorted(grouped.items()), start=1):
        scenario_id, period, spatial_unit_id, supply_node_id, commodity_id, quantity_unit = key
        quantity = sum(number(row["quantity"]) for row in group)

        def weighted_average(column: str) -> str:
            if quantity == 0:
                return format_number(number(group[0][column]))
            value = sum(number(row[column]) * number(row["quantity"]) for row in group) / quantity
            return format_number(value)

        def unique_join(column: str) -> str:
            return "; ".join(sorted({str(row[column]) for row in group if str(row[column])}))

        first = group[0]
        aggregated_rows.append(
            {
                "ws3_run_id": WS3_RUN_ID,
                "scenario_id": scenario_id,
                "period": period,
                "spatial_unit_id": spatial_unit_id,
                "supply_node_id": supply_node_id,
                "commodity_id": commodity_id,
                "quantity": format_number(quantity),
                "quantity_unit": quantity_unit,
                "supply_cost": weighted_average("supply_cost"),
                "supply_cost_unit": first["supply_cost_unit"],
                "species_group": unique_join("species_group"),
                "assortment_class": unique_join("assortment_class"),
                "grade": "scanfi_diagnostic",
                "moisture_basis": unique_join("moisture_basis"),
                "carbon_factor": "",
                "harvest_action_id": f"scanfi_diag_supply_{index:03d}",
                "roadside_cost": weighted_average("roadside_cost"),
                "delivered_cost_proxy": weighted_average("delivered_cost_proxy"),
                "source_model": SOURCE_MODEL,
                "source_record_id": f"{WS3_RUN_ID}:{supply_node_id}:{commodity_id}",
                "notes": (
                    "Aggregated synthetic SCANFI diagnostic supply row for LP handoff; "
                    "cell-level provenance remains in diagnostic_inventory_cells.csv."
                ),
                "inventory_cell_id": unique_join("inventory_cell_id"),
                "diagnostic_region": unique_join("diagnostic_region"),
                "inventory_year": unique_join("inventory_year"),
                "landcover_class": unique_join("landcover_class"),
                "inventory_value_policy": unique_join("inventory_value_policy"),
                "yield_rule_id": unique_join("yield_rule_id"),
                "yield_value_per_ha": unique_join("yield_value_per_ha"),
                "yield_value_unit": unique_join("yield_value_unit"),
                "yield_value_policy": unique_join("yield_value_policy"),
                "biomass_source_file": unique_join("biomass_source_file"),
                "height_source_file": unique_join("height_source_file"),
                "closure_source_file": unique_join("closure_source_file"),
                "landcover_source_file": unique_join("landcover_source_file"),
                "age_source_file": unique_join("age_source_file"),
            }
        )
    return aggregated_rows


def validate_harvest_rows(rows: list[dict[str, object]]) -> None:
    seen_record_ids: set[str] = set()
    for row in rows:
        record_id = str(row["source_record_id"])
        if record_id in seen_record_ids:
            raise ValueError(f"Duplicate SCANFI diagnostic source_record_id: {record_id}")
        seen_record_ids.add(record_id)
        if number(row["quantity"]) < 0:
            raise ValueError(f"Negative diagnostic supply quantity for {record_id}")
        inventory_policies = set(str(row["inventory_value_policy"]).split("; "))
        if inventory_policies != {"synthetic_proxy_not_sampled_from_raster"}:
            raise ValueError(f"Unexpected inventory value policy for {record_id}")
        yield_policies = set(str(row["yield_value_policy"]).split("; "))
        if yield_policies != {VALUE_POLICY}:
            raise ValueError(f"Unexpected yield value policy for {record_id}")


def build_scanfi_diagnostic_supply(
    inventory_path: Path = DEFAULT_INVENTORY_PATH,
    yield_rules_path: Path = DEFAULT_YIELD_RULES_PATH,
    output_path: Path = DEFAULT_SCANFI_INPUT_DIR / "scanfi_diagnostic_harvest_output.csv",
    input_dir: Path = DEFAULT_BASE_INPUT_DIR,
) -> list[dict[str, object]]:
    write_yield_rules(yield_rules_path)
    rows = build_harvest_rows(inventory_path, yield_rules_path, input_dir)
    write_rows(output_path, SCANFI_HARVEST_FIELDNAMES, rows)
    return rows


def copy_fixture(base_input_dir: Path, variant_dir: Path) -> None:
    if variant_dir.exists():
        shutil.rmtree(variant_dir)
    shutil.copytree(base_input_dir, variant_dir, ignore=shutil.ignore_patterns("generated"))


def update_run_provenance(variant_dir: Path, scanfi_input_dir: Path) -> None:
    path = variant_dir / "run_provenance.csv"
    rows = read_rows(path)
    if len(rows) != 1:
        raise ValueError(f"Expected one run_provenance row in {path}, found {len(rows)}")
    row = rows[0]
    row["run_id"] = VARIANT_RUN_ID
    row["ws3_run_id"] = WS3_RUN_ID
    row["lp_run_id"] = VARIANT_RUN_ID
    row["data_version"] = "diagnostic_scanfi_inventory_fixture"
    row["source_manifest_ref"] = str(scanfi_input_dir / "scanfi_v2_2025_diagnostic_subset_manifest.csv")
    row["notes"] = (
        "Diagnostic variant replacing the synthetic BC mini supply_offer table "
        "with SCANFI-provenance inventory-cell supply rows; yield quantities "
        "and costs remain synthetic placeholders."
    )
    write_rows(path, list(row.keys()), rows)


def write_variant_readme(variant_dir: Path) -> None:
    text = """# BC Mini-Network SCANFI Diagnostic Variant

This directory is generated by `fresh_fibre.scanfi_supply` from the base
`bc-mini` fixture. It replaces `supply_offer.csv` with supply rows generated
from the lightweight SCANFI diagnostic inventory cells and synthetic yield
rules while keeping downstream BC mini network, technology, transport, and
market tables unchanged.

The variant is diagnostic only. It proves that accepted SCANFI inventory
provenance can flow into the existing ws3-shaped harvest-output boundary and
downstream LP path, but the yield rules, quantities, and costs are not
calibrated production assumptions.
"""
    (variant_dir / "README.md").write_text(text, encoding="utf-8")


def build_graph_tables(input_dir: Path) -> tuple[int, int]:
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
    return len(nodes), len(arcs)


def write_contract_validation(input_dir: Path) -> list[dict[str, object]]:
    rows = validate_contract(input_dir)
    write_rows(input_dir / "generated" / "contract_validation.csv", VALIDATION_COLUMNS, rows)
    assert_contract_ok(rows)
    return rows


VARIANT_SUMMARY_COLUMNS = ["metric", "value", "unit", "notes"]


def write_variant_summary(
    variant_dir: Path,
    harvest_rows: list[dict[str, object]],
    lp_result: dict[str, object],
    node_count: int,
    arc_count: int,
    contract_rows: list[dict[str, object]],
) -> None:
    total_by_unit: dict[str, float] = {}
    for row in harvest_rows:
        total_by_unit[str(row["quantity_unit"])] = total_by_unit.get(str(row["quantity_unit"]), 0.0) + number(
            row["quantity"]
        )
    summary_rows = [
        {
            "metric": "variant_run_id",
            "value": VARIANT_RUN_ID,
            "unit": "id",
            "notes": "LP run id written to run_provenance.csv.",
        },
        {
            "metric": "ws3_run_id",
            "value": WS3_RUN_ID,
            "unit": "id",
            "notes": "Synthetic SCANFI diagnostic harvest-output boundary id.",
        },
        {
            "metric": "diagnostic_harvest_rows",
            "value": len(harvest_rows),
            "unit": "rows",
            "notes": "Rows generated from SCANFI diagnostic inventory cells.",
        },
        {
            "metric": "diagnostic_quantity_m3",
            "value": format_number(total_by_unit.get("m3", 0.0)),
            "unit": "m3",
            "notes": "Synthetic log quantity generated by placeholder yield rules.",
        },
        {
            "metric": "diagnostic_quantity_odt",
            "value": format_number(total_by_unit.get("odt", 0.0)),
            "unit": "odt",
            "notes": "Synthetic logging residue quantity generated by placeholder yield rules.",
        },
        {
            "metric": "graph_nodes",
            "value": node_count,
            "unit": "count",
            "notes": "Generated graph node count for the variant.",
        },
        {
            "metric": "graph_arcs",
            "value": arc_count,
            "unit": "count",
            "notes": "Generated graph arc count for the variant.",
        },
        {
            "metric": "solver_status",
            "value": lp_result["status"],
            "unit": "status",
            "notes": "LP solver status.",
        },
        {
            "metric": "objective_value",
            "value": lp_result["objective_value"],
            "unit": "CAD",
            "notes": "Diagnostic LP objective.",
        },
        {
            "metric": "lp_variables",
            "value": lp_result["variables"],
            "unit": "count",
            "notes": "Generated LP variable count.",
        },
        {
            "metric": "lp_constraints",
            "value": lp_result["constraints"],
            "unit": "count",
            "notes": "Generated LP constraint count.",
        },
        {
            "metric": "solution_flows",
            "value": lp_result["solution_flows"],
            "unit": "rows",
            "notes": "Positive solution_flow rows.",
        },
        {
            "metric": "contract_tables_validated",
            "value": len(contract_rows),
            "unit": "tables",
            "notes": "Contract validation row count.",
        },
    ]
    write_rows(variant_dir / "generated" / "scanfi_diagnostic_variant_summary.csv", VARIANT_SUMMARY_COLUMNS, summary_rows)


def run_scanfi_diagnostic_variant(
    base_input_dir: Path = DEFAULT_BASE_INPUT_DIR,
    scanfi_input_dir: Path = DEFAULT_SCANFI_INPUT_DIR,
    variant_dir: Path = DEFAULT_VARIANT_DIR,
) -> dict[str, object]:
    copy_fixture(base_input_dir, variant_dir)
    harvest_output_path = variant_dir / "ws3_harvest_output.csv"
    yield_rules_path = scanfi_input_dir / "diagnostic_yield_rules.csv"
    harvest_rows = build_scanfi_diagnostic_supply(
        inventory_path=scanfi_input_dir / "diagnostic_inventory_cells.csv",
        yield_rules_path=yield_rules_path,
        output_path=harvest_output_path,
        input_dir=variant_dir,
    )
    export_supply_offer(
        harvest_output_path,
        variant_dir / "supply_offer.csv",
        variant_dir,
        variant_dir / "generated" / "scanfi_diagnostic_export_manifest.csv",
    )
    update_run_provenance(variant_dir, scanfi_input_dir)
    write_variant_readme(variant_dir)
    node_count, arc_count = build_graph_tables(variant_dir)
    lp_result = build_and_solve(variant_dir, variant_dir / "generated")
    contract_rows = write_contract_validation(variant_dir)
    write_variant_summary(variant_dir, harvest_rows, lp_result, node_count, arc_count, contract_rows)
    return {
        "variant_dir": str(variant_dir),
        "ws3_run_id": WS3_RUN_ID,
        "status": lp_result["status"],
        "objective_value": lp_result["objective_value"],
        "graph_nodes": node_count,
        "graph_arcs": arc_count,
        "variables": lp_result["variables"],
        "constraints": lp_result["constraints"],
        "solution_flows": lp_result["solution_flows"],
        "harvest_output_rows": len(harvest_rows),
    }


def main_supply() -> None:
    parser = argparse.ArgumentParser(description="Build SCANFI diagnostic harvest output.")
    parser.add_argument("--scanfi-input-dir", type=Path, default=DEFAULT_SCANFI_INPUT_DIR)
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_BASE_INPUT_DIR)
    parser.add_argument(
        "--output-path",
        type=Path,
        default=DEFAULT_SCANFI_INPUT_DIR / "scanfi_diagnostic_harvest_output.csv",
    )
    args = parser.parse_args()
    rows = build_scanfi_diagnostic_supply(
        inventory_path=args.scanfi_input_dir / "diagnostic_inventory_cells.csv",
        yield_rules_path=args.scanfi_input_dir / "diagnostic_yield_rules.csv",
        output_path=args.output_path,
        input_dir=args.input_dir,
    )
    print(f"wrote {len(rows)} SCANFI diagnostic harvest rows to {args.output_path}")


def main_variant() -> None:
    parser = argparse.ArgumentParser(description="Solve the BC mini SCANFI diagnostic supply variant.")
    parser.add_argument("--base-input-dir", type=Path, default=DEFAULT_BASE_INPUT_DIR)
    parser.add_argument("--scanfi-input-dir", type=Path, default=DEFAULT_SCANFI_INPUT_DIR)
    parser.add_argument("--variant-dir", type=Path, default=DEFAULT_VARIANT_DIR)
    args = parser.parse_args()
    result = run_scanfi_diagnostic_variant(args.base_input_dir, args.scanfi_input_dir, args.variant_dir)
    print(
        f"solved SCANFI diagnostic LP variant {result['status']} "
        f"objective={float(result['objective_value']):.5f}; "
        f"wrote {result['variant_dir']}"
    )
