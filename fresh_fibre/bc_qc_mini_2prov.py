"""Run the BC + QC two-province LP fixture.

This module is the network-flow LP runner step at the end of the
two-province supply chain. The fixture
``metadata/ffcm-archive/prototype-inputs/bc-qc-mini-2prov/`` carries
two parallel ``supply_offer`` rowsets — one for BC
(synthetic-mini + ws3-shape) and one for QC
(synthetic-mini + casfri_v5 + qc_ws3 bridge) — and a downstream LP
that routes both provinces through the same facility / export / market
network.

**Output artefacts.**

- ``generated/solution_flow.csv`` / ``generated/solution_summary.csv`` —
  standard LP outputs from
  ``fresh_fibre.lp.build_and_solve``.
- ``generated/bc_qc_mini_supply_comparison.csv`` — BC vs. QC
  supply side-by-side (commodity × unit × province).
- ``generated/bc_qc_mini_pathway_flow.csv`` — province-keyed
  solution-flows for the inter-province node.
- ``generated/bc_qc_mini_solution_summary.csv`` — province-keyed
  solution-summary metrics.
- ``generated/contract_validation.csv`` — standard contract validation
  output from the LP.
- ``metadata/source-reviews/..`` — links out from this point in the LP
  boundary to the schema crosswalk and the data submodule.

**Boundary discipline.** The fixture is synthetic-bounded — no raw
provincial payloads live in the parent repo's fixture directory. The
real QC GeoPackage sample is staged in the data submodule at
``data/raw/qc_inventory/2023-ieqm-eighth/``; CASFRI translation tables
are in ``data/raw/casfri_v5/translation_tables/``. The adapter reads
them at runtime. GPL-3.0 framework source is **not** vendored.
"""

from __future__ import annotations

import argparse
import shutil
from collections import OrderedDict, defaultdict
from pathlib import Path

from fresh_fibre import graph
from fresh_fibre.contract import VALIDATION_COLUMNS, assert_contract_ok, validate_contract
from fresh_fibre.lp import build_and_solve
from fresh_fibre.qc_ws3_export import (
    DEFAULT_QC_FIXTURE_DIR,
    DEFAULT_TRANSLATION_TABLE_DIR,
    QC_VALUE_POLICIES,
    QcLookupTables,
    diagnostic_inventory_cells_csv_path,
    run_qc_ws3_export_bridge,
    write_qc_yield_rules,
)
from fresh_fibre.schema import number, read_rows, write_rows
from fresh_fibre.scanfi_supply import (
    DEFAULT_BASE_INPUT_DIR as DEFAULT_SCANFI_BASE,
    DEFAULT_SCANFI_INPUT_DIR as DEFAULT_SCANFI_DIAG_DIR,
    SCANFI_HARVEST_FIELDNAMES,
    SOURCE_MODEL as SCANFI_SOURCE_MODEL,
    WS3_RUN_ID as SCANFI_WS3_RUN_ID,
    build_scanfi_diagnostic_supply,
    GAUTHIER_VALUE_POLICY,
)
from fresh_fibre.ws3_export import export_supply_offer


DEFAULT_FIXTURE_DIR = Path("metadata/ffcm-archive/prototype-inputs/bc-qc-mini-2prov")
DEFAULT_SCENARIO = "bc_qc_mini_2prov_001"
DEFAULT_RUN_ID = "bc_qc_mini_2prov_lp_001"
DEFAULT_GRADE = "bc_qc_mini_2prov_diagnostic"
DEFAULT_WS3_RUN_ID = "bc_qc_mini_2prov_ws3_supply_001"
DEFAULT_SOURCE_MODEL = "qc_5e_ieqm_casfri_v5_diagnostic_mini_bc_mini_scanfi_mini"

VARIANT_RUN_MATRIX_COLUMNS = [
    "province",
    "scanfi_ws3_run_id",
    "qc_ws3_run_id",
    "scenario_id",
    "supply_rows",
    "supply_quantity_m3",
    "supply_quantity_odt",
    "objective_contribution_log",
    "objective_contribution_residue",
    "notes",
]

SUPPLY_COMPARISON_COLUMNS = [
    "province",
    "commodity_id",
    "quantity_unit",
    "total_quantity",
    "supply_cost_average",
    "supply_count",
    "source_model",
    "ws3_run_id",
]

PATHWAY_FLOW_COLUMNS = [
    "province",
    "origin_node_id",
    "destination_node_id",
    "source_commodity_id",
    "product_commodity_id",
    "transport_link_id",
    "technology_id",
    "market_id",
    "quantity",
    "quantity_unit",
    "run_id",
]

SOLUTION_SUMMARY_PER_PROVINCE_COLUMNS = [
    "province",
    "metric_id",
    "commodity_id",
    "facility_class",
    "market_id",
    "value",
    "unit",
]


def write_variant_run_matrix(
    matrix_rows: list[dict[str, object]],
    output_path: Path,
) -> list[dict[str, object]]:
    write_rows(output_path, VARIANT_RUN_MATRIX_COLUMNS, matrix_rows)
    return matrix_rows


def run_bc_supply(
    scanfi_fixture_dir: Path = DEFAULT_SCANFI_DIAG_DIR,
    bc_mini_dir: Path = DEFAULT_SCANFI_BASE,
    output_dir: Path = DEFAULT_FIXTURE_DIR,
) -> dict[str, object]:
    """Build the BC mini SCANFI diagnostic supply rows into the fixture dir.

    The SCANFI BC mini is the same shape as the existing
    ``bc-mini-scanfi-diagnostic`` fixture; it gets re-staged onto the
    shared ``bc-qc-mini-2prov`` variant directory in place of the
    synthetic BC mini supply offers.
    """
    inventory_path = scanfi_fixture_dir / "diagnostic_inventory_cells.csv"
    yield_rules_path = scanfi_fixture_dir / "diagnostic_yield_rules.csv"
    output_path = output_dir / "ws3_harvest_output_bc.csv"
    bc_supply_offer_path = output_dir / "supply_offer_bc.csv"
    rows = build_scanfi_diagnostic_supply(
        inventory_path=inventory_path,
        yield_rules_path=yield_rules_path,
        output_path=output_path,
        input_dir=bc_mini_dir,
    )
    export_supply_offer(
        output_path,
        bc_supply_offer_path,
        bc_mini_dir,
        output_path.parent / "bc_mini_scanfi_export_manifest.csv",
    )
    return {
        "scenario_id": "bc_mini_001",
        "ws3_run_id": SCANFI_WS3_RUN_ID,
        "source_model": SCANFI_SOURCE_MODEL,
        "supply_rows": rows,
        "supply_offer_path": bc_supply_offer_path,
        "harvest_output_path": output_path,
    }


def run_qc_supply(
    qc_di_dir: Path = DEFAULT_QC_FIXTURE_DIR,
    output_dir: Path = DEFAULT_FIXTURE_DIR,
    translation_dir: Path = DEFAULT_TRANSLATION_TABLE_DIR,
    scenario_id: str = DEFAULT_SCENARIO,
) -> dict[str, object]:
    """Build the QC mini diagnostic supply rows into the fixture dir."""
    lookup_tables = QcLookupTables.from_csv_dir(translation_dir)
    output_path = output_dir / "ws3_harvest_output_qc.csv"
    qc_supply_offer_path = output_dir / "supply_offer_qc.csv"
    rows = run_qc_ws3_export_bridge(
        qc_diag_dir=qc_di_dir,
        output_path=output_path,
        lookup_tables=lookup_tables,
    )
    # Re-write the harvest rows with the unified scenario_id so ws3_export
    # reference validation succeeds against the merged-fixture scenario.
    rewritten_rows: list[dict[str, str]] = []
    for row in read_rows(output_path):
        updated = dict(row)
        if updated.get("scenario_id") in ("qc_mini_001", ""):
            updated["scenario_id"] = scenario_id
        rewritten_rows.append(updated)
    write_rows(output_path, SCANFI_HARVEST_FIELDNAMES, rewritten_rows)
    export_supply_offer(
        output_path,
        qc_supply_offer_path,
        output_dir,
        output_path.parent / "qc_mini_ws3_export_manifest.csv",
    )
    return {
        "scenario_id": scenario_id,
        "ws3_run_id": "qc_casfri_diagnostic_supply_001",
        "source_model": DEFAULT_SOURCE_MODEL,
        "supply_rows": rows,
        "supply_offer_path": qc_supply_offer_path,
        "harvest_output_path": output_path,
    }


def merge_supply_offers(
    output_dir: Path,
    bc_supply_offer_path: Path,
    qc_supply_offer_path: Path,
    scenario_id: str,
) -> Path:
    """One supply-offer table that covers both provinces.

    The merged table preserves the bc/qc scenario_id split so LP
    references resolve per province; downstream consumers can filter by
    scenario_id when needed.
    """
    bc_rows = read_rows(bc_supply_offer_path)
    qc_rows = read_rows(qc_supply_offer_path)
    # Normalize scenario_id for QC rows to the unified scenario_id.
    normalized_qc_rows: list[dict[str, str]] = []
    for row in qc_rows:
        normalized = dict(row)
        if normalized.get("scenario_id") == "qc_mini_001":
            normalized["scenario_id"] = scenario_id
        normalized_qc_rows.append(normalized)
    merged: list[dict[str, str]] = []
    for row in bc_rows:
        merged_row = dict(row)
        if merged_row.get("scenario_id") == "bc_mini_001":
            merged_row["scenario_id"] = scenario_id
        merged.append(merged_row)
    merged.extend(normalized_qc_rows)
    output_path = output_dir / "supply_offer.csv"
    fieldnames = [
        "scenario_id", "period", "spatial_unit_id", "supply_node_id", "commodity_id",
        "quantity", "quantity_unit", "supply_cost", "supply_cost_unit", "species_group",
        "assortment_class", "grade", "moisture_basis", "carbon_factor", "harvest_action_id",
        "roadside_cost", "delivered_cost_proxy",
    ]
    write_rows(output_path, fieldnames, merged)
    return output_path


def build_supply_comparison(
    bc_supply_offer_path: Path,
    qc_supply_offer_path: Path,
    output_path: Path,
) -> list[dict[str, object]]:
    """Emit BC vs QC supply side-by-side (commodity × unit × province)."""
    rows = []
    for province_label, path in (("BC", bc_supply_offer_path), ("QC", qc_supply_offer_path)):
        totals: dict[tuple[str, str], dict[str, float]] = defaultdict(
            lambda: {"quantity": 0.0, "supply_cost_weighted": 0.0, "count": 0},
        )
        for row in read_rows(path):
            key = (row["commodity_id"], row["quantity_unit"])
            stats = totals[key]
            quantity = number(row["quantity"])
            stats["quantity"] += quantity
            stats["supply_cost_weighted"] += number(row["supply_cost"]) * quantity
            stats["count"] += 1
        for (commodity_id, quantity_unit), stats in sorted(totals.items()):
            rows.append(
                {
                    "province": province_label,
                    "commodity_id": commodity_id,
                    "quantity_unit": quantity_unit,
                    "total_quantity": format_number(stats["quantity"]),
                    "supply_cost_average": format_number(
                        stats["supply_cost_weighted"] / stats["quantity"]
                        if stats["quantity"] > 0 else 0.0
                    ),
                    "supply_count": int(stats["count"]),
                    "source_model": (
                        "scanfi_v2_2025_diagnostic_inventory_fixture" if province_label == "BC"
                        else DEFAULT_SOURCE_MODEL
                    ),
                    "ws3_run_id": (
                        "scanfi_diagnostic_supply_001" if province_label == "BC"
                        else "qc_casfri_diagnostic_supply_001"
                    ),
                }
            )
    write_rows(output_path, SUPPLY_COMPARISON_COLUMNS, rows)
    return rows


def build_pathway_flow(
    solution_flow_path: Path,
    output_path: Path,
) -> list[dict[str, object]]:
    """Split solution flows into BC vs QC provinces using node-id prefixes."""
    bc_supply_prefixes = ("supply_coast", "supply_interior", "supply_residue", "mill_coast", "mill_interior")
    qc_supply_prefixes = ("supply_qc_", "mill_qc", "mill_sawmill_qc", "mill_pulp_qc", "plant_bioenergy_qc", "export_montreal")
    bc_route_prefixes = ("route_001", "route_002", "route_003", "route_004", "route_005", "route_006", "route_007", "route_008", "route_009", "route_010", "route_011", "route_012")
    qc_route_prefixes = ("qc_route_", )

    def classify(label: str)  -> str:
        if label.startswith(bc_supply_prefixes):
            return "BC"
        if label.startswith(qc_supply_prefixes):
            return "QC"
        if label.startswith(bc_route_prefixes):
            return "BC"
        if label.startswith(qc_route_prefixes):
            return "QC"
        if "bc_" in label:
            return "BC"
        if "qc_" in label:
            return "QC"
        return ""

    rows = []
    for row in read_rows(solution_flow_path):
        origin = row.get("origin_node_id", "")
        destination = row.get("destination_node_id", "")
        province_classifications = {classify(origin), classify(destination)}
        # Pick the first non-empty classification
        province = ""
        for candidate in (
            classify(origin), classify(destination), "shared",
        ):
            if candidate:
                province = candidate
                break
        rows.append(
            {
                "province": province,
                "origin_node_id": row["origin_node_id"],
                "destination_node_id": row["destination_node_id"],
                "source_commodity_id": row["source_commodity_id"],
                "product_commodity_id": row["product_commodity_id"],
                "transport_link_id": row["transport_link_id"],
                "technology_id": row["technology_id"],
                "market_id": row["market_id"],
                "quantity": row["quantity"],
                "quantity_unit": row["quantity_unit"],
                "run_id": row["run_id"],
            }
        )
    write_rows(output_path, PATHWAY_FLOW_COLUMNS, rows)
    return rows


def build_solution_summary_per_province(
    base_dir: Path = DEFAULT_FIXTURE_DIR,
    output_path: Path = DEFAULT_FIXTURE_DIR / "generated" / "bc_qc_mini_solution_summary.csv",
) -> list[dict[str, object]]:
    """Province-keyed per-region breakdown of the canonical solution summary."""
    supply_offer_path = base_dir / "supply_offer.csv"
    if not supply_offer_path.exists():
        raise FileNotFoundError(
            f"Two-province supply_offer table missing: {supply_offer_path}",
        )
    summary_rows: list[dict[str, object]] = []
    saw_quantity: dict[str, dict[tuple[str, str], float]] = defaultdict(lambda: defaultdict(float))
    saw_cost: dict[str, dict[tuple[str, str], float]] = defaultdict(lambda: defaultdict(float))
    for offer in read_rows(supply_offer_path):
        spatial = offer["spatial_unit_id"]
        province = "BC" if spatial.startswith("bc_") else ("QC" if spatial.startswith("qc_") else "shared")
        commodity_id = offer["commodity_id"]
        quantity_unit = offer["quantity_unit"]
        saw_quantity[province][(commodity_id, quantity_unit)] += number(offer["quantity"])
        saw_cost[province][(commodity_id, quantity_unit)] += number(offer["supply_cost"])
    for province, per_commodity in saw_quantity.items():
        for (commodity_id, quantity_unit), quantity in sorted(per_commodity.items()):
            summary_rows.append(
                {
                    "province": province,
                    "metric_id": "period_supply_quantity",
                    "commodity_id": commodity_id,
                    "facility_class": "",
                    "market_id": "",
                    "value": format_number(quantity),
                    "unit": quantity_unit,
                }
            )
        for (commodity_id, quantity_unit), cost_total in sorted(saw_cost[province].items()):
            summary_rows.append(
                {
                    "province": province,
                    "metric_id": "period_supply_cost_total",
                    "commodity_id": commodity_id,
                    "facility_class": "",
                    "market_id": "",
                    "value": format_number(cost_total),
                    "unit": "CAD",
                }
            )
    write_rows(output_path, SOLUTION_SUMMARY_PER_PROVINCE_COLUMNS, summary_rows)
    return summary_rows


def format_number(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".")


def regenerate_workdir(
    base_dir: Path = DEFAULT_SCANFI_BASE,
    target_dir: Path = DEFAULT_FIXTURE_DIR,
) -> None:
    """Copy ``bc-mini`` fixture tables (minus generated/) to ``bc-qc-mini-2prov`` and add QC tables."""
    if target_dir.exists():
        shutil.rmtree(target_dir)
    shutil.copytree(base_dir, target_dir, ignore=shutil.ignore_patterns("generated"))
    # Append QC rows onto the relevant tables.
    append_qc_rows_to_fixture_tables(target_dir)
    create_qc_diagnostic_subdir(target_dir)


def append_qc_rows_to_fixture_tables(target_dir: Path) -> None:
    """Append QC rows onto the inherited BC tables."""
    # Network nodes
    nodes_path = target_dir / "network_node.csv"
    nodes_existing = read_rows(nodes_path)
    new_nodes = [
        {
            "node_id": "supply_qc_saguenay_01",
            "node_type": "supply",
            "province": "QC",
            "active_from_period": "0",
            "active_to_period": "0",
            "spatial_unit_id": "qc_fmu_saguenay_01",
            "geometry_ref": "data:diagnostic/supply_qc_saguenay_01",
            "facility_class": "",
            "owner_ref": "",
            "license_status": "diagnostic",
        },
        {
            "node_id": "supply_qc_mixedwood_02",
            "node_type": "supply",
            "province": "QC",
            "active_from_period": "0",
            "active_to_period": "0",
            "spatial_unit_id": "qc_fmu_saguenay_02",
            "geometry_ref": "data:diagnostic/supply_qc_mixedwood_02",
            "facility_class": "",
            "owner_ref": "",
            "license_status": "diagnostic",
        },
        {
            "node_id": "supply_qc_disturbance_02",
            "node_type": "supply",
            "province": "QC",
            "active_from_period": "0",
            "active_to_period": "0",
            "spatial_unit_id": "qc_fmu_saguenay_02",
            "geometry_ref": "data:diagnostic/supply_qc_disturbance_02",
            "facility_class": "",
            "owner_ref": "",
            "license_status": "diagnostic",
        },
        {
            "node_id": "supply_qc_residue_01",
            "node_type": "supply",
            "province": "QC",
            "active_from_period": "0",
            "active_to_period": "0",
            "spatial_unit_id": "qc_fmu_saguenay_02",
            "geometry_ref": "data:diagnostic/supply_qc_residue_01",
            "facility_class": "",
            "owner_ref": "",
            "license_status": "diagnostic",
        },
        {
            "node_id": "mill_sawmill_qc",
            "node_type": "facility",
            "province": "QC",
            "active_from_period": "0",
            "active_to_period": "0",
            "spatial_unit_id": "",
            "geometry_ref": "data:diagnostic/mill_sawmill_qc",
            "facility_class": "sawmill",
            "owner_ref": "synthetic",
            "license_status": "diagnostic",
        },
        {
            "node_id": "mill_pulp_qc",
            "node_type": "facility",
            "province": "QC",
            "active_from_period": "0",
            "active_to_period": "0",
            "spatial_unit_id": "",
            "geometry_ref": "data:diagnostic/mill_pulp_qc",
            "facility_class": "pulp",
            "owner_ref": "synthetic",
            "license_status": "diagnostic",
        },
        {
            "node_id": "plant_bioenergy_qc",
            "node_type": "facility",
            "province": "QC",
            "active_from_period": "0",
            "active_to_period": "0",
            "spatial_unit_id": "",
            "geometry_ref": "data:diagnostic/plant_bioenergy_qc",
            "facility_class": "bioenergy",
            "owner_ref": "synthetic",
            "license_status": "diagnostic",
        },
        {
            "node_id": "export_montreal",
            "node_type": "export",
            "province": "QC",
            "active_from_period": "0",
            "active_to_period": "0",
            "spatial_unit_id": "",
            "geometry_ref": "data:diagnostic/export_montreal",
            "facility_class": "export",
            "owner_ref": "synthetic",
            "license_status": "diagnostic",
        },
    ]
    nodes_existing.extend(new_nodes)
    write_rows(nodes_path, list(nodes_existing[0].keys()), nodes_existing)

    # Spatial units
    spatial_path = target_dir / "spatial_unit.csv"
    spatial_existing = read_rows(spatial_path)
    new_spatial = [
        {
            "spatial_unit_id": "qc_fmu_saguenay_01",
            "spatial_unit_type": "diagnostic_fmu",
            "parent_unit_id": "qc",
            "province": "QC",
            "geometry_ref": "data:diagnostic/qc_fmu_saguenay_01",
            "area_ha": "85000",
            "management_regime": "synthetic_even_flow",
            "access_class": "roaded",
            "ownership_or_tenure": "public",
            "source_dataset_id": "qc_5e_ieqm_casfri_v5_mini",
        },
        {
            "spatial_unit_id": "qc_fmu_saguenay_02",
            "spatial_unit_type": "diagnostic_fmu",
            "parent_unit_id": "qc",
            "province": "QC",
            "geometry_ref": "data:diagnostic/qc_fmu_saguenay_02",
            "area_ha": "55000",
            "management_regime": "synthetic_disturbance_mix",
            "access_class": "mixed_access",
            "ownership_or_tenure": "public",
            "source_dataset_id": "qc_5e_ieqm_casfri_v5_mini",
        },
    ]
    spatial_existing.extend(new_spatial)
    write_rows(spatial_path, list(spatial_existing[0].keys()), spatial_existing)

    # Scenario row with the unified scenario_id
    scenario_path = target_dir / "scenario.csv"
    scenario_rows = read_rows(scenario_path)
    for row in scenario_rows:
        row["scenario_id"] = DEFAULT_SCENARIO
        row["scenario_name"] = "BC + QC synthetic 2-province mini-network (P7.1.CASFRI extension)"
    write_rows(scenario_path, list(scenario_rows[0].keys()), scenario_rows)

    # Transport links: add QC routes
    transport_path = target_dir / "transport_link.csv"
    transport_existing = read_rows(transport_path)
    for row in transport_existing:
        row["scenario_id"] = DEFAULT_SCENARIO
    new_links = [
        {
            "scenario_id": DEFAULT_SCENARIO,
            "period": "0",
            "origin_node_id": "supply_qc_saguenay_01",
            "destination_node_id": "mill_sawmill_qc",
            "transport_mode": "truck",
            "distance": "120",
            "distance_unit": "km",
            "route_id": "qc_route_001",
            "segment_lengths": "",
            "speed_limits": "",
            "travel_time": "2.6",
            "transport_cost": "18",
            "transport_cost_unit": "CAD_per_m3",
            "capacity": "40000",
        },
        {
            "scenario_id": DEFAULT_SCENARIO,
            "period": "0",
            "origin_node_id": "supply_qc_mixedwood_02",
            "destination_node_id": "mill_sawmill_qc",
            "transport_mode": "truck",
            "distance": "180",
            "distance_unit": "km",
            "route_id": "qc_route_001b",
            "segment_lengths": "",
            "speed_limits": "",
            "travel_time": "4.0",
            "transport_cost": "22",
            "transport_cost_unit": "CAD_per_m3",
            "capacity": "24000",
        },
        {
            "scenario_id": DEFAULT_SCENARIO,
            "period": "0",
            "origin_node_id": "supply_qc_disturbance_02",
            "destination_node_id": "mill_sawmill_qc",
            "transport_mode": "truck",
            "distance": "210",
            "distance_unit": "km",
            "route_id": "qc_route_001c",
            "segment_lengths": "",
            "speed_limits": "",
            "travel_time": "4.5",
            "transport_cost": "26",
            "transport_cost_unit": "CAD_per_m3",
            "capacity": "18000",
        },
        {
            "scenario_id": DEFAULT_SCENARIO,
            "period": "0",
            "origin_node_id": "supply_qc_saguenay_01",
            "destination_node_id": "export_montreal",
            "transport_mode": "rail",
            "distance": "540",
            "distance_unit": "km",
            "route_id": "qc_route_002",
            "segment_lengths": "",
            "speed_limits": "",
            "travel_time": "10.2",
            "transport_cost": "30",
            "transport_cost_unit": "CAD_per_m3",
            "capacity": "8000",
        },
        {
            "scenario_id": DEFAULT_SCENARIO,
            "period": "0",
            "origin_node_id": "supply_qc_residue_01",
            "destination_node_id": "plant_bioenergy_qc",
            "transport_mode": "truck",
            "distance": "240",
            "distance_unit": "km",
            "route_id": "qc_route_003",
            "segment_lengths": "",
            "speed_limits": "",
            "travel_time": "5.1",
            "transport_cost": "30",
            "transport_cost_unit": "CAD_per_odt",
            "capacity": "9000",
        },
        {
            "scenario_id": DEFAULT_SCENARIO,
            "period": "0",
            "origin_node_id": "mill_sawmill_qc",
            "destination_node_id": "mill_pulp_qc",
            "transport_mode": "truck",
            "distance": "105",
            "distance_unit": "km",
            "route_id": "qc_route_004",
            "segment_lengths": "",
            "speed_limits": "",
            "travel_time": "2.4",
            "transport_cost": "18",
            "transport_cost_unit": "CAD_per_odt",
            "capacity": "16000",
        },
    ]
    transport_existing.extend(new_links)
    write_rows(transport_path, list(transport_existing[0].keys()), transport_existing)

    # Markets: add QC markets
    markets_path = target_dir / "market.csv"
    markets_existing = read_rows(markets_path)
    new_markets = [
        {
            "market_id": "market_lumber_qc",
            "product_commodity_id": "lumber",
            "seller_group": "sawmill",
            "buyer_group": "construction",
            "minimum_demand": "0",
            "maximum_demand": "50000",
            "price": "200",
            "price_unit": "CAD_per_m3",
            "report_group": "primary_products",
            "sankey_group": "lumber",
        },
        {
            "market_id": "market_bioenergy_qc",
            "product_commodity_id": "bioenergy",
            "seller_group": "bioenergy",
            "buyer_group": "local_energy",
            "minimum_demand": "0",
            "maximum_demand": "120000",
            "price": "16",
            "price_unit": "CAD_per_GJ",
            "report_group": "energy_products",
            "sankey_group": "bioenergy",
        },
        {
            "market_id": "market_log_export_qc",
            "product_commodity_id": "log",
            "seller_group": "export",
            "buyer_group": "export_market",
            "minimum_demand": "0",
            "maximum_demand": "8000",
            "price": "98",
            "price_unit": "CAD_per_m3",
            "report_group": "exports",
            "sankey_group": "log_export",
        },
    ]
    markets_existing.extend(new_markets)
    write_rows(markets_path, list(markets_existing[0].keys()), markets_existing)

    # Export sink: rewrite scenario_id and add QC export
    export_sink_path = target_dir / "export_sink.csv"
    export_sink_existing = read_rows(export_sink_path)
    for row in export_sink_existing:
        row["scenario_id"] = DEFAULT_SCENARIO
    new_sink = [
        {
            "scenario_id": DEFAULT_SCENARIO,
            "period": "0",
            "export_node_id": "export_montreal",
            "commodity_id": "log",
            "price_or_netback": "98",
            "price_unit": "CAD_per_m3",
            "maximum_export": "8000",
            "destination_region": "Atlantic Rim",
            "trade_case": "diagnostic",
        },
    ]
    export_sink_existing.extend(new_sink)
    write_rows(export_sink_path, list(export_sink_existing[0].keys()), export_sink_existing)

    # Technology: add QC facilities
    tech_path = target_dir / "technology.csv"
    tech_existing = read_rows(tech_path)
    new_techs = [
        {
            "technology_id": "tech_sawmill_qc",
            "facility_node_id": "mill_sawmill_qc",
            "feedstock_commodity_id": "log",
            "primary_product_id": "lumber",
            "infeed_rate": "1",
            "infeed_rate_unit": "m3_infeed_per_m3_log",
            "operating_cost": "26",
            "operating_cost_unit": "CAD_per_m3_log",
            "throughput_id": "sawmill_qc_base",
            "mutually_exclusive_group_id": "",
            "minimum_production": "0",
            "maximum_capacity": "44000",
            "capital_status": "existing",
        },
        {
            "technology_id": "tech_pulp_qc",
            "facility_node_id": "mill_pulp_qc",
            "feedstock_commodity_id": "woodchips",
            "primary_product_id": "pulp",
            "infeed_rate": "1",
            "infeed_rate_unit": "odt_infeed_per_odt_chip",
            "operating_cost": "32",
            "operating_cost_unit": "CAD_per_odt_chip",
            "throughput_id": "pulp_qc_chips_base",
            "mutually_exclusive_group_id": "",
            "minimum_production": "0",
            "maximum_capacity": "20000",
            "capital_status": "existing",
        },
        {
            "technology_id": "tech_bioenergy_qc",
            "facility_node_id": "plant_bioenergy_qc",
            "feedstock_commodity_id": "logging_residue",
            "primary_product_id": "bioenergy",
            "infeed_rate": "1",
            "infeed_rate_unit": "odt_infeed_per_odt_logging_residue",
            "operating_cost": "10",
            "operating_cost_unit": "CAD_per_odt_logging_residue",
            "throughput_id": "bioenergy_qc_base",
            "mutually_exclusive_group_id": "",
            "minimum_production": "0",
            "maximum_capacity": "12000",
            "capital_status": "existing",
        },
        {
            "technology_id": "tech_export_logs_qc",
            "facility_node_id": "export_montreal",
            "feedstock_commodity_id": "log",
            "primary_product_id": "log",
            "infeed_rate": "1",
            "infeed_rate_unit": "m3_infeed_per_m3_log",
            "operating_cost": "5",
            "operating_cost_unit": "CAD_per_m3_log",
            "throughput_id": "export_log_qc_base",
            "mutually_exclusive_group_id": "",
            "minimum_production": "0",
            "maximum_capacity": "8000",
            "capital_status": "export_option",
        },
    ]
    tech_existing.extend(new_techs)
    write_rows(tech_path, list(tech_existing[0].keys()), tech_existing)

    # Technology flows: add QC flow rows
    tech_flow_path = target_dir / "technology_flow.csv"
    tech_flow_existing = read_rows(tech_flow_path)
    new_flows = [
        {
            "technology_id": "tech_sawmill_qc",
            "flow_type": "output",
            "commodity_id": "lumber",
            "coefficient_or_quantity": "0.49",
            "unit": "m3_per_m3_log",
            "basis_commodity_id": "log",
            "basis_unit": "m3",
            "conversion_source": "synthetic_qc_mini",
            "loss_factor": "0",
        },
        {
            "technology_id": "tech_sawmill_qc",
            "flow_type": "byproduct",
            "commodity_id": "woodchips",
            "coefficient_or_quantity": "0.21",
            "unit": "odt_per_m3_log",
            "basis_commodity_id": "log",
            "basis_unit": "m3",
            "conversion_source": "synthetic_qc_mini",
            "loss_factor": "0",
        },
        {
            "technology_id": "tech_sawmill_qc",
            "flow_type": "byproduct",
            "commodity_id": "sawdust_shavings",
            "coefficient_or_quantity": "0.07",
            "unit": "odt_per_m3_log",
            "basis_commodity_id": "log",
            "basis_unit": "m3",
            "conversion_source": "synthetic_qc_mini",
            "loss_factor": "0",
        },
        {
            "technology_id": "tech_sawmill_qc",
            "flow_type": "byproduct",
            "commodity_id": "hogfuel",
            "coefficient_or_quantity": "0.03",
            "unit": "odt_per_m3_log",
            "basis_commodity_id": "log",
            "basis_unit": "m3",
            "conversion_source": "synthetic_qc_mini",
            "loss_factor": "0",
        },
        {
            "technology_id": "tech_pulp_qc",
            "flow_type": "output",
            "commodity_id": "pulp",
            "coefficient_or_quantity": "0.92",
            "unit": "odt_per_odt_chip",
            "basis_commodity_id": "woodchips",
            "basis_unit": "odt",
            "conversion_source": "synthetic_qc_mini",
            "loss_factor": "0.08",
        },
        {
            "technology_id": "tech_bioenergy_qc",
            "flow_type": "output",
            "commodity_id": "bioenergy",
            "coefficient_or_quantity": "12.0",
            "unit": "GJ_per_odt_logging_residue",
            "basis_commodity_id": "logging_residue",
            "basis_unit": "odt",
            "conversion_source": "synthetic_qc_mini",
            "loss_factor": "0",
        },
        {
            "technology_id": "tech_export_logs_qc",
            "flow_type": "output",
            "commodity_id": "log",
            "coefficient_or_quantity": "1.0",
            "unit": "m3_per_m3_log",
            "basis_commodity_id": "log",
            "basis_unit": "m3",
            "conversion_source": "synthetic_qc_mini",
            "loss_factor": "0",
        },
    ]
    tech_flow_existing.extend(new_flows)
    write_rows(tech_flow_path, list(tech_flow_existing[0].keys()), tech_flow_existing)

    # Run provenance: replace with two-province run id
    run_provenance_path = target_dir / "run_provenance.csv"
    rows = read_rows(run_provenance_path)
    if len(rows) != 1:
        raise ValueError(
            f"Expected one run_provenance row in {run_provenance_path}, found {len(rows)}",
        )
    row = rows[0]
    row["scenario_id"] = DEFAULT_SCENARIO
    row["run_id"] = DEFAULT_RUN_ID
    row["ws3_run_id"] = DEFAULT_WS3_RUN_ID
    row["lp_run_id"] = DEFAULT_RUN_ID
    row["data_version"] = "diagnostic_bc_qc_mini_2prov"
    row["source_manifest_ref"] = (
        "metadata/source-crosswalks/qc_casfri_v5_schema.md"
    )
    row["notes"] = (
        "Two-province BC + QC diagnostic fixture (P7.1.CASFRI extension). "
        "BC supply regenerated from SCANFI mini-shape; QC supply generated "
        "through the casfri_qc_reader adapter into ws3-shape rows; both feed "
        "the same downstream LP via the unified bc-qc-mini-2prov supply table."
    )
    write_rows(run_provenance_path, list(row.keys()), rows)


def create_qc_diagnostic_subdir(target_dir: Path) -> None:
    """Generate the QC diagnostic mini fixture files in ``qc-diagnostic/``."""
    qc_diag_dir = target_dir / "qc-diagnostic"
    qc_diag_dir.mkdir(parents=True, exist_ok=True)

    write_qc_yield_rules(qc_diag_dir / "qc_diagnostic_yield_rules.csv")

    cells_csv_path = qc_diag_dir / "qc_diagnostic_inventory_cells.csv"
    cell_rows = [
        {
            "inventory_cell_id": "qc_cell_boreal_softwood_sawlog_001",
            "scenario_id": "qc_mini_001",
            "period": "0",
            "province": "QC",
            "diagnostic_region": "qc_boreal_softwood_sawlog",
            "spatial_unit_id": "qc_fmu_saguenay_01",
            "supply_node_id": "supply_qc_saguenay_01",
            "geocode": "21M_0000000001",
            "essence_1": "SAB",
            "essence_qualite": "SAB",
            "essence_pct_1": "55",
            "essence_2": "EPB",
            "essence_pct_2": "30",
            "essence_3": "BOP",
            "essence_pct_3": "15",
            "cl_age": "1070",
            "cl_dens": "B",
            "cl_haut": "3",
            "cl_drai": "20",
            "type_eco": "MS2",
            "hauteur": "16",
            "superficie": "16000000",
            "iqs_pot": "12",
            "b_arbv_tot": "85",
            "value_policy": "qc_species_resolved_via_lookup",
            "source_manifest": "metadata/source-crosswalks/qc_casfri_v5_schema.md",
        },
        {
            "inventory_cell_id": "qc_cell_boreal_softwood_sawlog_002",
            "scenario_id": "qc_mini_001",
            "period": "0",
            "province": "QC",
            "diagnostic_region": "qc_boreal_softwood_sawlog",
            "spatial_unit_id": "qc_fmu_saguenay_01",
            "supply_node_id": "supply_qc_saguenay_01",
            "geocode": "21M_0000000002",
            "essence_1": "EPB",
            "essence_qualite": "EPB",
            "essence_pct_1": "60",
            "essence_2": "SAB",
            "essence_pct_2": "25",
            "essence_3": "PEU",
            "essence_pct_3": "15",
            "cl_age": "1090",
            "cl_dens": "C",
            "cl_haut": "4",
            "cl_drai": "30",
            "type_eco": "MS22",
            "hauteur": "22",
            "superficie": "11000000",
            "iqs_pot": "14",
            "b_arbv_tot": "110",
            "value_policy": "qc_species_resolved_via_lookup",
            "source_manifest": "metadata/source-crosswalks/qc_casfri_v5_schema.md",
        },
        {
            "inventory_cell_id": "qc_cell_mixedwood_sawlog_001",
            "scenario_id": "qc_mini_001",
            "period": "0",
            "province": "QC",
            "diagnostic_region": "qc_mixedwood_sawlog",
            "spatial_unit_id": "qc_fmu_saguenay_02",
            "supply_node_id": "supply_qc_mixedwood_02",
            "geocode": "21M_0000000003",
            "essence_1": "BOJ",
            "essence_qualite": "BOJ",
            "essence_pct_1": "40",
            "essence_2": "SAB",
            "essence_pct_2": "35",
            "essence_3": "PEU",
            "essence_pct_3": "25",
            "cl_age": "1050",
            "cl_dens": "B",
            "cl_haut": "3",
            "cl_drai": "20",
            "type_eco": "MS2",
            "hauteur": "16",
            "superficie": "9000000",
            "iqs_pot": "13",
            "b_arbv_tot": "90",
            "value_policy": "qc_species_resolved_via_lookup",
            "source_manifest": "metadata/source-crosswalks/qc_casfri_v5_schema.md",
        },
        {
            "inventory_cell_id": "qc_cell_disturbance_mix_001",
            "scenario_id": "qc_mini_001",
            "period": "0",
            "province": "QC",
            "diagnostic_region": "qc_disturbance_mix",
            "spatial_unit_id": "qc_fmu_saguenay_02",
            "supply_node_id": "supply_qc_disturbance_02",
            "geocode": "21M_0000000004",
            "essence_1": "MEH",
            "essence_qualite": "MEH",
            "essence_pct_1": "45",
            "essence_2": "SAB",
            "essence_pct_2": "30",
            "essence_3": "BOP",
            "essence_pct_3": "25",
            "cl_age": "1070",
            "cl_dens": "D",
            "cl_haut": "3",
            "cl_drai": "30",
            "type_eco": "MS22",
            "hauteur": "13",
            "superficie": "7000000",
            "iqs_pot": "11",
            "b_arbv_tot": "70",
            "value_policy": "qc_species_resolved_via_lookup",
            "source_manifest": "metadata/source-crosswalks/qc_casfri_v5_schema.md",
        },
        {
            "inventory_cell_id": "qc_cell_residue_candidate_001",
            "scenario_id": "qc_mini_001",
            "period": "0",
            "province": "QC",
            "diagnostic_region": "qc_residue_candidate",
            "spatial_unit_id": "qc_fmu_saguenay_02",
            "supply_node_id": "supply_qc_residue_01",
            "geocode": "21M_0000000005",
            "essence_1": "SAB",
            "essence_qualite": "SAB",
            "essence_pct_1": "70",
            "essence_2": "EPB",
            "essence_pct_2": "30",
            "essence_3": "",
            "essence_pct_3": "0",
            "cl_age": "1010",
            "cl_dens": "C",
            "cl_haut": "2",
            "cl_drai": "20",
            "type_eco": "MS2",
            "hauteur": "10",
            "superficie": "12000000",
            "iqs_pot": "13",
            "b_arbv_tot": "60",
            "value_policy": "qc_species_resolved_via_lookup",
            "source_manifest": "metadata/source-crosswalks/qc_casfri_v5_schema.md",
        },
    ]
    fieldnames = [
        "inventory_cell_id", "scenario_id", "period", "province", "diagnostic_region",
        "spatial_unit_id", "supply_node_id", "geocode", "essence_1", "essence_qualite",
        "essence_pct_1", "essence_2", "essence_pct_2", "essence_3", "essence_pct_3",
        "cl_age", "cl_dens", "cl_haut", "cl_drai", "type_eco", "hauteur", "superficie",
        "iqs_pot", "b_arbv_tot", "value_policy", "source_manifest",
    ]
    write_rows(cells_csv_path, fieldnames, cell_rows)


def build_graph_tables(target_dir: Path) -> tuple[int, int]:
    tables = {
        table: graph.read_table(target_dir, table)
        for table in graph.TABLE_COLUMNS
    }
    graph.validate_references(tables)
    nodes, arcs = graph.build_graph(tables)
    output_dir = target_dir / "generated"
    graph.write_table(output_dir / "graph_nodes.csv", graph.NODE_COLUMNS, nodes)
    graph.write_table(output_dir / "graph_arcs.csv", graph.ARC_COLUMNS, arcs)
    graph.write_table(
        output_dir / "graph_summary.csv", graph.SUMMARY_COLUMNS, graph.build_summary(nodes, arcs),
    )
    return len(nodes), len(arcs)


def run_full_two_province_variant(
    fixture_dir: Path = DEFAULT_FIXTURE_DIR,
    base_dir: Path = DEFAULT_SCANFI_BASE,
    scanfi_fixture_dir: Path = DEFAULT_SCANFI_DIAG_DIR,
    regenerate_workdir_flag: bool = True,
) -> dict[str, object]:
    """Run the full BC + QC two-province LP variant."""
    if regenerate_workdir_flag:
        regenerate_workdir(base_dir=base_dir, target_dir=fixture_dir)

    bc_supply = run_bc_supply(
        scanfi_fixture_dir=scanfi_fixture_dir, bc_mini_dir=base_dir, output_dir=fixture_dir,
    )
    qc_supply = run_qc_supply(
        qc_di_dir=fixture_dir / "qc-diagnostic",
        output_dir=fixture_dir,
        translation_dir=DEFAULT_TRANSLATION_TABLE_DIR,
    )

    supply_offer_path = merge_supply_offers(
        output_dir=fixture_dir,
        bc_supply_offer_path=bc_supply["supply_offer_path"],
        qc_supply_offer_path=qc_supply["supply_offer_path"],
        scenario_id=DEFAULT_SCENARIO,
    )

    node_count, arc_count = build_graph_tables(fixture_dir)
    lp_result = build_and_solve(fixture_dir, fixture_dir / "generated")

    contract_rows = validate_contract(fixture_dir)
    write_rows(
        fixture_dir / "generated" / "contract_validation.csv",
        VALIDATION_COLUMNS,
        contract_rows,
    )
    assert_contract_ok(contract_rows)

    supply_comparison_rows = build_supply_comparison(
        bc_supply["supply_offer_path"],
        qc_supply["supply_offer_path"],
        fixture_dir / "generated" / "bc_qc_mini_supply_comparison.csv",
    )
    pathway_rows = build_pathway_flow(
        fixture_dir / "generated" / "solution_flow.csv",
        fixture_dir / "generated" / "bc_qc_mini_pathway_flow.csv",
    )
    summary_rows = build_solution_summary_per_province(
        base_dir=fixture_dir,
        output_path=fixture_dir / "generated" / "bc_qc_mini_solution_summary.csv",
    )

    bc_supply_rows = read_rows(bc_supply["supply_offer_path"])
    qc_supply_rows = read_rows(qc_supply["supply_offer_path"])

    def quantity_sum_by_unit(rows: list[dict[str, str]], unit: str) -> float:
        return sum(number(offer["quantity"]) for offer in rows if offer["quantity_unit"] == unit)

    bc_quantity_m3 = quantity_sum_by_unit(bc_supply_rows, "m3")
    bc_quantity_odt = quantity_sum_by_unit(bc_supply_rows, "odt")
    qc_quantity_m3 = quantity_sum_by_unit(qc_supply_rows, "m3")
    qc_quantity_odt = quantity_sum_by_unit(qc_supply_rows, "odt")

    matrix_rows = [
        {
            "province": "BC",
            "scanfi_ws3_run_id": SCANFI_WS3_RUN_ID,
            "qc_ws3_run_id": "",
            "scenario_id": "bc_mini_001",
            "supply_rows": str(len(bc_supply["supply_rows"])),
            "supply_quantity_m3": format_number(bc_quantity_m3),
            "supply_quantity_odt": format_number(bc_quantity_odt),
            "objective_contribution_log": "0.0",
            "objective_contribution_residue": "0.0",
            "notes": "Inherited SCANFI diagnostic mini path; placeholders for BC yield.",
        },
        {
            "province": "QC",
            "scanfi_ws3_run_id": "",
            "qc_ws3_run_id": "qc_casfri_diagnostic_supply_001",
            "scenario_id": "qc_mini_001",
            "supply_rows": str(len(qc_supply["supply_rows"])),
            "supply_quantity_m3": format_number(qc_quantity_m3),
            "supply_quantity_odt": format_number(qc_quantity_odt),
            "objective_contribution_log": "0.0",
            "objective_contribution_residue": "0.0",
            "notes": "QC diagnostic mini path through casfri_qc_reader + qc_ws3_export bridge.",
        },
    ]
    write_variant_run_matrix(
        matrix_rows,
        fixture_dir / "generated" / "bc_qc_mini_variant_run_matrix.csv",
    )

    return {
        "fixture_dir": str(fixture_dir),
        "bc_supply_rows": len(bc_supply["supply_rows"]),
        "qc_supply_rows": len(qc_supply["supply_rows"]),
        "bc_quantity_m3": bc_quantity_m3,
        "bc_quantity_odt": bc_quantity_odt,
        "qc_quantity_m3": qc_quantity_m3,
        "qc_quantity_odt": qc_quantity_odt,
        "supply_comparison_rows": len(supply_comparison_rows),
        "pathway_flow_rows": len(pathway_rows),
        "solution_summary_rows": len(summary_rows),
        "lp_status": lp_result["status"],
        "lp_objective_value": lp_result["objective_value"],
        "graph_nodes": node_count,
        "graph_arcs": arc_count,
        "lp_variables": lp_result["variables"],
        "lp_constraints": lp_result["constraints"],
        "solution_flows": lp_result["solution_flows"],
        "contract_rows": len(contract_rows),
        "supply_offer_path": str(supply_offer_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workdir", type=Path, default=DEFAULT_FIXTURE_DIR)
    parser.add_argument(
        "--base-dir",
        type=Path,
        default=DEFAULT_SCANFI_BASE,
    )
    parser.add_argument(
        "--scanfi-fixture-dir",
        type=Path,
        default=DEFAULT_SCANFI_DIAG_DIR,
    )
    parser.add_argument(
        "--skip-regenerate-workdir",
        action="store_true",
        help="Do not rebuild the fixture directory; re-use the existing tables.",
    )
    parser.add_argument(
        "--translation-table-dir",
        type=Path,
        default=DEFAULT_TRANSLATION_TABLE_DIR,
    )
    args = parser.parse_args()

    result = run_full_two_province_variant(
        fixture_dir=args.workdir,
        base_dir=args.base_dir,
        scanfi_fixture_dir=args.scanfi_fixture_dir,
        regenerate_workdir_flag=not args.skip_regenerate_workdir,
    )
    print(
        f"solved bc-qc-mini-2prov {result['lp_status']} objective={float(result['lp_objective_value']):.6f}; "
        f"wrote {result['fixture_dir']}"
    )


if __name__ == "__main__":
    main()
