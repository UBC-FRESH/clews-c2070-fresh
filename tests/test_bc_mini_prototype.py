"""Regression tests for the BC mini-network FRESH prototype."""

from __future__ import annotations

import csv
import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path

from fresh_fibre import graph
from fresh_fibre.compare import compare_runs
from fresh_fibre.contract import assert_contract_ok, validate_contract
from fresh_fibre.gauthier_yield import build_gauthier_diagnostic_fixture
from fresh_fibre.lp import build_and_solve
from fresh_fibre.scanfi_supply import (
    GAUTHIER_VALUE_POLICY,
    INVENTORY_DIAGNOSTIC_POLICIES,
    RESIDUE_VALUE_POLICY,
    build_gauthier_scanfi_yield_rules,
    build_scanfi_diagnostic_supply,
    run_scanfi_diagnostic_variant,
    run_scanfi_gauthier_diagnostic_variant,
    validate_harvest_rows,
)
from fresh_fibre.units import UnitValidationError, load_unit_registry
from fresh_fibre.validation import validate
from fresh_fibre.variant_compare import compare_three_way
from fresh_fibre.ws3_export import export_supply_offer
from fresh_fibre.ws3_smoke import run_ws3_smoke
from fresh_fibre.ws3_variant import run_ws3_smoke_variant


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = REPO_ROOT / "metadata/ffcm-archive/prototype-inputs/bc-mini"
SCANFI_FIXTURE_DIR = REPO_ROOT / "metadata/ffcm-archive/prototype-inputs/scanfi-diagnostic"
GAUTHIER_FIXTURE_DIR = REPO_ROOT / "metadata/ffcm-archive/prototype-inputs/gauthier-diagnostic"
TINY_REAL_SAMPLING_FIXTURE = REPO_ROOT / "metadata/ffcm-archive/tiny-real-sampling-fixture-cells.csv"
TINY_REAL_SAMPLING_EXTRACTION_CONTRACT = (
    REPO_ROOT / "metadata/ffcm-archive/tiny-real-sampling-extraction-contract.csv"
)
TINY_REAL_SAMPLING_VALIDATION_CONTRACT = (
    REPO_ROOT / "metadata/ffcm-archive/tiny-real-sampling-validation-contract.csv"
)
CONCEPTUAL_ALPHA_READINESS = REPO_ROOT / "metadata/ffcm-archive/conceptual-alpha-readiness.csv"
PROTOTYPE_GAP_REGISTER = REPO_ROOT / "metadata/ffcm-archive/prototype-gap-register.csv"
PHASE5_PHASE6_AUTHORIZATION = REPO_ROOT / "metadata/ffcm-archive/phase-5-phase-6-authorization.csv"
UNIT_REGISTRY = REPO_ROOT / "metadata/ffcm-archive/unit-registry.csv"
CONVERSION_REGISTRY = REPO_ROOT / "metadata/ffcm-archive/unit-conversion-registry.csv"
WS3_REPO = REPO_ROOT.parent / "ws3"
WS3_SMOKE_MODEL = WS3_REPO / "examples/data/woodstock_model_files_tsa24_clipped"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys(), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


class BcMiniPrototypeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.input_dir = Path(self.tmp.name) / "bc-mini"
        shutil.copytree(FIXTURE_DIR, self.input_dir)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def regenerate_graph(self) -> None:
        tables = {
            table: graph.read_table(self.input_dir, table)
            for table in graph.TABLE_COLUMNS
        }
        graph.validate_references(tables)
        nodes, arcs = graph.build_graph(tables)
        summary = graph.build_summary(nodes, arcs)
        output_dir = self.input_dir / "generated"
        graph.write_table(output_dir / "graph_nodes.csv", graph.NODE_COLUMNS, nodes)
        graph.write_table(output_dir / "graph_arcs.csv", graph.ARC_COLUMNS, arcs)
        graph.write_table(output_dir / "graph_summary.csv", graph.SUMMARY_COLUMNS, summary)

    def test_end_to_end_solution_contract_and_counts(self) -> None:
        self.regenerate_graph()
        result = build_and_solve(self.input_dir, self.input_dir / "generated")
        validate(self.input_dir)
        contract_rows = validate_contract(self.input_dir)
        assert_contract_ok(contract_rows)

        self.assertEqual(result["status"], "Optimal")
        self.assertAlmostEqual(float(result["objective_value"]), 6140832.0)
        self.assertEqual(result["variables"], 28)
        self.assertEqual(result["constraints"], 43)
        self.assertEqual(result["solution_flows"], 24)

        generated = self.input_dir / "generated"
        expected_counts = {
            "graph_nodes.csv": 37,
            "graph_arcs.csv": 45,
            "graph_summary.csv": 15,
            "lp_variables.csv": 28,
            "lp_constraints.csv": 43,
            "objective_terms.csv": 28,
            "solution_flow.csv": 24,
            "solution_summary.csv": 18,
        }
        for filename, expected_count in expected_counts.items():
            self.assertEqual(len(read_csv(generated / filename)), expected_count, filename)

        statuses = {row["table_name"]: row["status"] for row in contract_rows}
        self.assertEqual(statuses["solution_flow"], "ok")
        self.assertEqual(statuses["solution_summary"], "ok")
        self.assertEqual(statuses["transporter"], "missing_optional")
        self.assertEqual(statuses["import_offer"], "missing_optional")

    def test_contract_validation_rejects_missing_required_field(self) -> None:
        self.regenerate_graph()
        build_and_solve(self.input_dir, self.input_dir / "generated")
        path = self.input_dir / "supply_offer.csv"
        rows = read_csv(path)
        trimmed_rows = [
            {key: value for key, value in row.items() if key != "quantity"}
            for row in rows
        ]
        write_csv(path, trimmed_rows)

        contract_rows = validate_contract(self.input_dir)
        statuses = {row["table_name"]: row for row in contract_rows}
        self.assertEqual(statuses["supply_offer"]["status"], "missing_required_fields")
        with self.assertRaises(ValueError):
            assert_contract_ok(contract_rows)

    def test_ws3_export_reproduces_supply_offer_contract_rows(self) -> None:
        output_path = self.input_dir / "generated" / "supply_offer_from_ws3.csv"
        rows = export_supply_offer(
            self.input_dir / "ws3_harvest_output.csv",
            output_path,
            self.input_dir,
        )

        self.assertEqual(rows, read_csv(self.input_dir / "supply_offer.csv"))
        self.assertEqual(read_csv(output_path), read_csv(self.input_dir / "supply_offer.csv"))
        manifest_rows = read_csv(self.input_dir / "generated" / "ws3_export_manifest.csv")
        manifest_by_role = {row["artifact_role"]: row for row in manifest_rows}
        self.assertEqual(
            set(manifest_by_role),
            {
                "normalized_ws3_harvest_output",
                "exported_supply_offer",
                "downstream_supply_offer_contract",
            },
        )
        self.assertEqual(manifest_by_role["normalized_ws3_harvest_output"]["row_count"], "4")
        self.assertEqual(manifest_by_role["exported_supply_offer"]["row_count"], "4")
        self.assertEqual(manifest_by_role["downstream_supply_offer_contract"]["row_count"], "4")
        self.assertEqual(
            manifest_by_role["exported_supply_offer"]["sha256"],
            manifest_by_role["downstream_supply_offer_contract"]["sha256"],
        )
        for row in manifest_rows:
            self.assertEqual(len(row["sha256"]), 64)
            self.assertEqual(row["ws3_run_ids"], "synthetic_ws3_shape_001")

    def test_ws3_export_rejects_unknown_supply_node(self) -> None:
        path = self.input_dir / "ws3_harvest_output.csv"
        rows = read_csv(path)
        rows[0]["supply_node_id"] = "unknown_supply_node"
        write_csv(path, rows)

        with self.assertRaisesRegex(ValueError, "unknown supply node"):
            export_supply_offer(
                path,
                self.input_dir / "generated" / "supply_offer_from_ws3.csv",
                self.input_dir,
            )

    def test_scanfi_diagnostic_supply_preserves_provenance(self) -> None:
        scanfi_dir = Path(self.tmp.name) / "scanfi-diagnostic"
        shutil.copytree(SCANFI_FIXTURE_DIR, scanfi_dir)
        output_path = scanfi_dir / "scanfi_diagnostic_harvest_output.csv"

        rows = build_scanfi_diagnostic_supply(
            inventory_path=scanfi_dir / "diagnostic_inventory_cells.csv",
            yield_rules_path=scanfi_dir / "diagnostic_yield_rules.csv",
            output_path=output_path,
            input_dir=self.input_dir,
        )

        self.assertEqual(len(rows), 4)
        self.assertEqual(len(read_csv(scanfi_dir / "diagnostic_yield_rules.csv")), 6)
        harvest_rows = read_csv(output_path)
        self.assertEqual(len(harvest_rows), 4)
        self.assertEqual({row["grade"] for row in harvest_rows}, {"scanfi_diagnostic"})
        self.assertEqual({row["yield_value_policy"] for row in harvest_rows}, {
            "synthetic_placeholder_yield_not_calibrated",
        })
        self.assertTrue(all(row["biomass_source_file"].startswith("SCANFI_att_biomass_2025") for row in harvest_rows))
        self.assertTrue(any("; " in row["inventory_cell_id"] for row in harvest_rows))

    def test_validate_harvest_rows_accepts_real_sampled_inventory_policy(self) -> None:
        real_sampled_row = {
            "source_record_id": "test_real_sampled_001",
            "quantity": 100.0,
            "inventory_value_policy": "real_sampling_fixture_design_not_sampled",
            "yield_value_policy": "synthetic_placeholder_yield_not_calibrated",
        }
        validate_harvest_rows([real_sampled_row])

        legacy_row = {
            "source_record_id": "test_legacy_001",
            "quantity": 200.0,
            "inventory_value_policy": "synthetic_proxy_not_sampled_from_raster",
            "yield_value_policy": "synthetic_placeholder_yield_not_calibrated",
        }
        validate_harvest_rows([legacy_row])

        mixed_rows = [real_sampled_row, legacy_row]
        validate_harvest_rows(mixed_rows)

        self.assertIn(
            "real_sampling_fixture_design_not_sampled",
            INVENTORY_DIAGNOSTIC_POLICIES,
        )
        self.assertIn(
            "synthetic_proxy_not_sampled_from_raster",
            INVENTORY_DIAGNOSTIC_POLICIES,
        )
        self.assertNotIn("production_calibrated_yield", INVENTORY_DIAGNOSTIC_POLICIES)

        with self.assertRaises(ValueError) as ctx:
            validate_harvest_rows([{
                "source_record_id": "test_prod_001",
                "quantity": 300.0,
                "inventory_value_policy": "production_calibrated_yield",
                "yield_value_policy": "synthetic_placeholder_yield_not_calibrated",
            }])
        self.assertIn("production_calibrated_yield", str(ctx.exception))

    def test_scanfi_diagnostic_lp_variant_solves_with_replaced_supply(self) -> None:
        scanfi_dir = Path(self.tmp.name) / "scanfi-diagnostic"
        variant_dir = Path(self.tmp.name) / "bc-mini-scanfi-diagnostic"
        shutil.copytree(SCANFI_FIXTURE_DIR, scanfi_dir)

        result = run_scanfi_diagnostic_variant(self.input_dir, scanfi_dir, variant_dir)

        self.assertEqual(result["status"], "Optimal")
        self.assertAlmostEqual(float(result["objective_value"]), 5795998.91275, places=5)
        self.assertEqual(result["graph_nodes"], 37)
        self.assertEqual(result["graph_arcs"], 45)
        self.assertEqual(result["variables"], 28)
        self.assertEqual(result["constraints"], 43)
        self.assertEqual(result["solution_flows"], 21)
        self.assertEqual(result["harvest_output_rows"], 4)

        supply_rows = read_csv(variant_dir / "supply_offer.csv")
        summary_rows = read_csv(variant_dir / "generated" / "scanfi_diagnostic_variant_summary.csv")
        summary = {row["metric"]: row["value"] for row in summary_rows}
        self.assertEqual(len(supply_rows), 4)
        self.assertEqual(summary["solver_status"], "Optimal")
        self.assertEqual(summary["diagnostic_quantity_m3"], "104700")
        self.assertEqual(summary["diagnostic_quantity_odt"], "6600")

    def test_gauthier_scanfi_adapter_replaces_only_log_yields(self) -> None:
        scanfi_dir = Path(self.tmp.name) / "scanfi-diagnostic"
        gauthier_dir = Path(self.tmp.name) / "gauthier-diagnostic"
        shutil.copytree(SCANFI_FIXTURE_DIR, scanfi_dir)
        shutil.copytree(GAUTHIER_FIXTURE_DIR, gauthier_dir)
        output_path = scanfi_dir / "gauthier_diagnostic_yield_rules.csv"

        rows = build_gauthier_scanfi_yield_rules(
            placeholder_rules_path=scanfi_dir / "diagnostic_yield_rules.csv",
            gauthier_candidate_rules_path=gauthier_dir / "gauthier_diagnostic_yield_rules_candidate.csv",
            output_path=output_path,
        )

        self.assertEqual(len(rows), 6)
        self.assertEqual(read_csv(output_path), [{key: str(value) for key, value in row.items()} for row in rows])
        log_rows = [row for row in rows if row["commodity_id"] == "log"]
        residue_rows = [row for row in rows if row["commodity_id"] == "logging_residue"]
        self.assertEqual(len(log_rows), 5)
        self.assertEqual(len(residue_rows), 1)
        self.assertEqual({row["value_policy"] for row in log_rows}, {GAUTHIER_VALUE_POLICY})
        self.assertEqual({row["value_policy"] for row in residue_rows}, {RESIDUE_VALUE_POLICY})
        self.assertEqual(residue_rows[0]["period_available_quantity_per_ha"], "0.55")
        self.assertEqual(
            {row["period_available_quantity_per_ha"] for row in log_rows},
            {"1.612903", "2", "1.25", "1.204819", "0.598802"},
        )

    def test_scanfi_gauthier_diagnostic_lp_variant_solves_with_log_yield_replacement(self) -> None:
        scanfi_dir = Path(self.tmp.name) / "scanfi-diagnostic"
        gauthier_dir = Path(self.tmp.name) / "gauthier-diagnostic"
        variant_dir = Path(self.tmp.name) / "bc-mini-scanfi-gauthier-diagnostic"
        shutil.copytree(SCANFI_FIXTURE_DIR, scanfi_dir)
        shutil.copytree(GAUTHIER_FIXTURE_DIR, gauthier_dir)

        result = run_scanfi_gauthier_diagnostic_variant(
            self.input_dir,
            scanfi_dir,
            gauthier_dir,
            variant_dir,
        )

        self.assertEqual(result["status"], "Optimal")
        self.assertAlmostEqual(float(result["objective_value"]), 6562205.49236, places=5)
        self.assertEqual(result["graph_nodes"], 37)
        self.assertEqual(result["graph_arcs"], 45)
        self.assertEqual(result["variables"], 28)
        self.assertEqual(result["constraints"], 43)
        self.assertEqual(result["solution_flows"], 24)
        self.assertEqual(result["harvest_output_rows"], 4)

        supply_rows = read_csv(variant_dir / "supply_offer.csv")
        harvest_rows = read_csv(variant_dir / "ws3_harvest_output.csv")
        summary_rows = read_csv(variant_dir / "generated" / "scanfi_gauthier_diagnostic_variant_summary.csv")
        summary = {row["metric"]: row["value"] for row in summary_rows}
        self.assertEqual(len(supply_rows), 4)
        self.assertEqual(summary["solver_status"], "Optimal")
        self.assertEqual(summary["diagnostic_quantity_m3"], "222824.665")
        self.assertEqual(summary["diagnostic_quantity_odt"], "6600")
        self.assertEqual({row["grade"] for row in harvest_rows}, {"scanfi_gauthier_diagnostic"})
        yield_policies = {
            policy
            for row in harvest_rows
            for policy in row["yield_value_policy"].split("; ")
        }
        self.assertEqual(yield_policies, {GAUTHIER_VALUE_POLICY, RESIDUE_VALUE_POLICY})
        for filename in ("solution_flow.csv", "solution_summary.csv"):
            rows = read_csv(variant_dir / "generated" / filename)
            self.assertTrue(rows)
            for row in rows:
                if "quantity" in row and row["quantity"]:
                    self.assertGreaterEqual(float(row["quantity"]), 0.0)

    def test_tiny_real_sampling_fixture_cells_cover_bc_mini_supply_nodes(self) -> None:
        rows = read_csv(TINY_REAL_SAMPLING_FIXTURE)
        self.assertEqual(len(rows), 6)

        required_columns = {
            "fixture_cell_id",
            "source_proxy_cell_id",
            "fixture_id",
            "scenario_id",
            "period",
            "province",
            "diagnostic_region",
            "spatial_unit_id",
            "supply_node_id",
            "geometry_ref",
            "representative_area_ha",
            "inventory_year",
            "required_source_layers",
            "data_source_window_dir",
            "data_manifest_path",
            "parent_repo_derived_inventory_path",
            "value_policy",
            "sampling_status",
            "notes",
        }
        self.assertLessEqual(required_columns, rows[0].keys())

        fixture_ids = {row["fixture_cell_id"] for row in rows}
        self.assertEqual(len(fixture_ids), len(rows))

        required_nonempty = {
            "spatial_unit_id",
            "supply_node_id",
            "data_source_window_dir",
            "data_manifest_path",
            "parent_repo_derived_inventory_path",
            "value_policy",
            "sampling_status",
        }
        for row in rows:
            for column in required_nonempty:
                self.assertTrue(row[column], f"{column} is empty for {row['fixture_cell_id']}")
            self.assertEqual(row["value_policy"], "real_sampling_fixture_design_not_sampled")
            self.assertEqual(row["sampling_status"], "planned_not_materialized")
            self.assertTrue(
                row["data_source_window_dir"].startswith(
                    "data/derived/scanfi-diagnostic/tiny-bc-real-sampling/windows/"
                )
            )
            self.assertTrue(
                row["data_manifest_path"].startswith(
                    "data/manifests/scanfi-diagnostic/tiny-bc-real-sampling/"
                )
            )
            self.assertEqual(
                row["parent_repo_derived_inventory_path"],
                "metadata/ffcm-archive/prototype-inputs/bc-mini-scanfi-real-sampled/diagnostic_inventory_cells.csv",
            )

        supply_rows = read_csv(
            REPO_ROOT
            / "metadata/ffcm-archive/prototype-inputs/bc-mini-scanfi-gauthier-diagnostic/supply_offer.csv"
        )
        self.assertEqual(
            {row["spatial_unit_id"] for row in rows},
            {row["spatial_unit_id"] for row in supply_rows},
        )
        self.assertEqual(
            {row["supply_node_id"] for row in rows},
            {row["supply_node_id"] for row in supply_rows},
        )

    def test_tiny_real_sampling_extraction_contract_keeps_payloads_in_data_boundary(self) -> None:
        contract_rows = read_csv(TINY_REAL_SAMPLING_EXTRACTION_CONTRACT)
        self.assertEqual(len(contract_rows), 5)

        required_columns = {
            "contract_id",
            "fixture_id",
            "layer_role",
            "source_file_name",
            "source_manifest_path",
            "data_raw_source_path",
            "data_window_output_dir",
            "data_window_manifest_path",
            "parent_repo_derived_output_path",
            "expected_crs",
            "expected_resolution_m",
            "nodata_policy",
            "sampling_method",
            "summary_statistics",
            "checksum_policy",
            "license_position",
            "materialization_status",
            "notes",
        }
        self.assertLessEqual(required_columns, contract_rows[0].keys())

        expected_contract_ids = {
            "scanfi_biomass_2025",
            "scanfi_height_2025",
            "scanfi_closure_2025",
            "scanfi_landcover_2025",
            "scanfi_age_2025",
        }
        self.assertEqual({row["contract_id"] for row in contract_rows}, expected_contract_ids)

        fixture_layers = {
            source_layer
            for row in read_csv(TINY_REAL_SAMPLING_FIXTURE)
            for source_layer in row["required_source_layers"].split("|")
        }
        self.assertEqual({row["source_file_name"] for row in contract_rows}, fixture_layers)
        self.assertNotIn("SCANFI_north_extrapol_area.tif", fixture_layers)

        for row in contract_rows:
            self.assertTrue(row["data_raw_source_path"].startswith("data/raw/scanfi/v2/"))
            self.assertTrue(
                row["data_window_output_dir"].startswith(
                    "data/derived/scanfi-diagnostic/tiny-bc-real-sampling/windows/"
                )
            )
            self.assertTrue(
                row["data_window_manifest_path"].startswith(
                    "data/manifests/scanfi-diagnostic/tiny-bc-real-sampling/"
                )
            )
            self.assertEqual(
                row["parent_repo_derived_output_path"],
                "metadata/ffcm-archive/prototype-inputs/bc-mini-scanfi-real-sampled/diagnostic_inventory_cells.csv",
            )
            self.assertEqual(row["materialization_status"], "planned_not_materialized")
            self.assertEqual(row["sampling_method"], "window_extract_then_cell_summary")
            self.assertEqual(row["checksum_policy"], "sha256_for_window_outputs_and_manifest_rows")
            self.assertNotIn(
                row["data_raw_source_path"].split("/", 1)[0],
                {"metadata", "planning", "fresh_fibre", "scripts", "tests"},
            )
            self.assertNotIn(
                row["data_window_output_dir"].split("/", 1)[0],
                {"metadata", "planning", "fresh_fibre", "scripts", "tests"},
            )

    def test_tiny_real_sampling_validation_contract_separates_diagnostic_and_production_gates(self) -> None:
        rows = read_csv(TINY_REAL_SAMPLING_VALIDATION_CONTRACT)
        self.assertGreaterEqual(len(rows), 12)

        required_columns = {
            "validation_id",
            "fixture_id",
            "validation_group",
            "validation_level",
            "applies_to",
            "source_contract",
            "required_inputs",
            "check_type",
            "acceptance_rule",
            "failure_severity",
            "diagnostic_status",
            "production_status",
            "notes",
        }
        self.assertLessEqual(required_columns, rows[0].keys())

        source_contracts = {
            source
            for row in rows
            for source in row["source_contract"].split("|")
        }
        self.assertIn(
            "metadata/ffcm-archive/tiny-real-sampling-fixture-cells.csv",
            source_contracts,
        )
        self.assertIn(
            "metadata/ffcm-archive/tiny-real-sampling-extraction-contract.csv",
            source_contracts,
        )

        diagnostic_groups = {
            row["validation_group"]
            for row in rows
            if row["validation_level"] == "diagnostic_acceptance_gate"
        }
        self.assertIn("coverage", diagnostic_groups)
        self.assertIn("provenance", diagnostic_groups)

        production_only_groups = {
            "residue_yield",
            "cost_calibration",
            "climate_predictors",
            "nfi_access",
            "production_benchmark_validation",
        }
        production_rows = [
            row for row in rows if row["validation_group"] in production_only_groups
        ]
        self.assertEqual({row["validation_group"] for row in production_rows}, production_only_groups)
        self.assertTrue(
            all(row["validation_level"] == "production_calibration_gate" for row in production_rows)
        )
        self.assertTrue(
            all(row["diagnostic_status"] == "not_required_for_phase6_diagnostic" for row in production_rows)
        )

        for row in rows:
            self.assertNotEqual(row["production_status"], "production_ready")
            self.assertTrue(row["fixture_id"])
            self.assertTrue(row["acceptance_rule"])

    def test_conceptual_alpha_readiness_tracks_phase5_without_production_claims(self) -> None:
        rows = read_csv(CONCEPTUAL_ALPHA_READINESS)
        required_columns = {
            "component_id",
            "component_name",
            "readiness_state",
            "release_relevance",
            "diagnostic_status",
            "production_status",
            "blocking_gates",
            "evidence_paths",
            "next_action",
        }
        self.assertLessEqual(required_columns, rows[0].keys())

        by_component = {row["component_id"]: row for row in rows}
        expected_components = {
            "ffcm_archive_evidence",
            "bc_mini_lp_prototype",
            "ws3_smoke_bridge",
            "scanfi_diagnostic_lane",
            "scanfi_gauthier_diagnostic_lane",
            "tiny_real_sampling_fixture_cells",
            "tiny_real_sampling_extraction_contract",
            "tiny_real_sampling_validation_contract",
            "tiny_real_sampled_lane",
            "production_national_model",
        }
        self.assertEqual(set(by_component), expected_components)

        self.assertEqual(
            by_component["tiny_real_sampling_fixture_cells"]["readiness_state"],
            "designed_not_materialized",
        )
        self.assertEqual(
            by_component["tiny_real_sampling_extraction_contract"]["readiness_state"],
            "designed_not_materialized",
        )
        self.assertEqual(
            by_component["tiny_real_sampling_validation_contract"]["readiness_state"],
            "designed_not_materialized",
        )
        self.assertEqual(by_component["tiny_real_sampled_lane"]["readiness_state"], "not_implemented")
        self.assertEqual(
            by_component["production_national_model"]["production_status"],
            "blocked_open_gates",
        )
        self.assertTrue(all(row["production_status"] != "production_ready" for row in rows))

        evidence = "\n".join(row["evidence_paths"] for row in rows)
        for path in (
            "metadata/ffcm-archive/tiny-real-sampling-fixture-cells.csv",
            "metadata/ffcm-archive/tiny-real-sampling-extraction-contract.csv",
            "metadata/ffcm-archive/tiny-real-sampling-validation-contract.csv",
        ):
            self.assertIn(path, evidence)

        gap_rows = {row["gap_id"]: row for row in read_csv(PROTOTYPE_GAP_REGISTER)}
        self.assertIn("gap_002", gap_rows)
        gap_evidence = gap_rows["gap_002"]["current_evidence"]
        for path in (
            "metadata/ffcm-archive/tiny-real-sampling-fixture-cells.csv",
            "metadata/ffcm-archive/tiny-real-sampling-extraction-contract.csv",
            "metadata/ffcm-archive/tiny-real-sampling-validation-contract.csv",
        ):
            self.assertIn(path, gap_evidence)
        self.assertIn("not materialized", gap_rows["gap_002"]["why_it_matters"])

    def test_phase5_phase6_authorization_blocks_broad_or_production_scope(self) -> None:
        rows = read_csv(PHASE5_PHASE6_AUTHORIZATION)
        required_columns = {
            "authorization_id",
            "phase6_action",
            "authorization_status",
            "allowed_scope",
            "allowed_paths",
            "blocked_paths_or_actions",
            "evidence_paths",
            "required_followup",
            "notes",
        }
        self.assertLessEqual(required_columns, rows[0].keys())

        by_id = {row["authorization_id"]: row for row in rows}
        self.assertEqual(
            {
                "allow_scanfi_source_references",
                "allow_tiny_raster_windows",
                "allow_tiny_manifests",
                "allow_parent_derived_inventory",
                "block_broad_raw_checkout",
                "block_private_nfi_inputs",
                "block_production_assumptions",
                "block_national_scaling",
            },
            set(by_id),
        )

        authorized_rows = [
            row for row in rows if row["authorization_status"] == "authorized"
        ]
        blocked_rows = [
            row for row in rows if row["authorization_status"] == "blocked"
        ]
        self.assertEqual(len(authorized_rows), 4)
        self.assertEqual(len(blocked_rows), 4)

        authorized_paths = "\n".join(row["allowed_paths"] for row in authorized_rows)
        self.assertIn("data/raw/scanfi/v2/SCANFI_att_biomass_2025_v2_20260119.tif", authorized_paths)
        self.assertIn(
            "data/derived/scanfi-diagnostic/tiny-bc-real-sampling/windows/",
            authorized_paths,
        )
        self.assertIn(
            "data/manifests/scanfi-diagnostic/tiny-bc-real-sampling/",
            authorized_paths,
        )
        self.assertIn(
            "metadata/ffcm-archive/prototype-inputs/bc-mini-scanfi-real-sampled/diagnostic_inventory_cells.csv",
            authorized_paths,
        )
        self.assertNotIn("data/raw/|", authorized_paths)
        self.assertNotIn("data/restricted/nfi/", authorized_paths)

        blocked_actions = "\n".join(row["blocked_paths_or_actions"] for row in blocked_rows)
        for blocked in (
            "data/raw/",
            "data/restricted/nfi/",
            "production_residue_yield",
            "calibrated_costs",
            "climate_predictor_policy",
            "production_benchmark_validation",
            "national_inventory_sampling",
            "production_model_release",
        ):
            self.assertIn(blocked, blocked_actions)

        evidence_paths = "\n".join(row["evidence_paths"] for row in rows)
        for evidence in (
            "metadata/ffcm-archive/tiny-real-sampling-fixture-cells.csv",
            "metadata/ffcm-archive/tiny-real-sampling-extraction-contract.csv",
            "metadata/ffcm-archive/tiny-real-sampling-validation-contract.csv",
            "planning/phase-5-decision-memo.md",
        ):
            self.assertIn(evidence, evidence_paths)
        self.assertTrue(all("production_ready" not in row.values() for row in rows))

    def test_gauthier_diagnostic_yield_fixture_reproduces_method_shape(self) -> None:
        output_dir = Path(self.tmp.name) / "gauthier-diagnostic"

        result = build_gauthier_diagnostic_fixture(output_dir)

        self.assertEqual(result["coefficient_rows"], 2)
        self.assertEqual(result["input_rows"], 6)
        self.assertEqual(result["curve_rows"], 1200)
        self.assertEqual(result["maturity_rows"], 6)
        self.assertGreaterEqual(result["productive_rows"], 1)

        coefficients = read_csv(output_dir / "gauthier_2015_coefficients.csv")
        self.assertEqual({row["species_group"] for row in coefficients}, {"coniferous", "deciduous"})
        for row in coefficients:
            for field in ("beta0", "beta1", "beta2", "beta3", "beta4", "beta5", "duan_correction", "r2"):
                self.assertNotEqual(row[field], "")

        curve_rows = read_csv(output_dir / "gauthier_diagnostic_curve_points.csv")
        self.assertTrue(all(float(row["scaled_composite_volume_m3_per_ha"]) >= 0 for row in curve_rows))
        self.assertEqual({row["value_policy"] for row in curve_rows}, {
            "synthetic_method_reproduction_not_calibrated",
        })

        maturity_rows = read_csv(output_dir / "gauthier_diagnostic_maturity.csv")
        self.assertEqual(len(maturity_rows), 6)
        self.assertTrue(any(row["productive_flag"] == "true" for row in maturity_rows))
        self.assertTrue(
            all(
                row["productive_flag"] != "true" or float(row["maturity_age_years"]) <= 200
                for row in maturity_rows
            )
        )

        candidate_rules = read_csv(output_dir / "gauthier_diagnostic_yield_rules_candidate.csv")
        self.assertEqual(len(candidate_rules), 6)
        self.assertEqual({row["replacement_status"] for row in candidate_rules}, {"candidate_not_accepted"})
        self.assertEqual({row["value_policy"] for row in candidate_rules}, {
            "synthetic_method_reproduction_not_calibrated",
        })

    @unittest.skipUnless(
        importlib.util.find_spec("ws3") and WS3_SMOKE_MODEL.exists(),
        "ws3 editable install and example Woodstock files are required",
    )
    def test_ws3_smoke_run_exports_real_harvest_output(self) -> None:
        generated = self.input_dir / "generated"
        result = run_ws3_smoke(
            ws3_path=WS3_REPO,
            model_path=WS3_SMOKE_MODEL,
            input_dir=self.input_dir,
            output_path=generated / "ws3_smoke_harvest_output.csv",
            supply_offer_path=generated / "ws3_smoke_supply_offer.csv",
            manifest_path=generated / "ws3_smoke_export_manifest.csv",
            crosswalk_path=self.input_dir / "ws3_supply_crosswalk.csv",
        )

        self.assertEqual(result["harvest_output_rows"], 2)
        self.assertEqual(result["supply_offer_rows"], 2)
        self.assertAlmostEqual(float(result["total_quantity_m3"]), 1604.452295, places=6)

        harvest_rows = read_csv(generated / "ws3_smoke_harvest_output.csv")
        supply_rows = read_csv(generated / "ws3_smoke_supply_offer.csv")
        manifest_rows = read_csv(generated / "ws3_smoke_export_manifest.csv")

        self.assertEqual(len(harvest_rows), 2)
        self.assertEqual(len(supply_rows), 2)
        self.assertEqual({row["source_model"] for row in harvest_rows}, {"ws3.examples.tsa24_clipped"})
        self.assertEqual([row["spatial_unit_id"] for row in supply_rows], [
            "bc_fmu_coast_01",
            "bc_fmu_interior_01",
        ])
        self.assertEqual([row["supply_node_id"] for row in supply_rows], [
            "supply_coast_01",
            "supply_interior_01",
        ])
        self.assertEqual({row["artifact_role"] for row in manifest_rows}, {
            "normalized_ws3_harvest_output",
            "exported_supply_offer",
            "downstream_supply_offer_contract",
        })
        self.assertTrue(all(row["grade"] == "ws3_smoke" for row in supply_rows))

    @unittest.skipUnless(
        importlib.util.find_spec("ws3") and WS3_SMOKE_MODEL.exists(),
        "ws3 editable install and example Woodstock files are required",
    )
    def test_ws3_smoke_rejects_bad_crosswalk_reference(self) -> None:
        crosswalk_path = self.input_dir / "ws3_supply_crosswalk.csv"
        rows = read_csv(crosswalk_path)
        rows[0]["supply_node_id"] = "unknown_supply_node"
        write_csv(crosswalk_path, rows)

        with self.assertRaisesRegex(ValueError, "unknown supply node"):
            run_ws3_smoke(
                ws3_path=WS3_REPO,
                model_path=WS3_SMOKE_MODEL,
                input_dir=self.input_dir,
                output_path=self.input_dir / "generated" / "ws3_smoke_harvest_output.csv",
                supply_offer_path=self.input_dir / "generated" / "ws3_smoke_supply_offer.csv",
                manifest_path=self.input_dir / "generated" / "ws3_smoke_export_manifest.csv",
                crosswalk_path=crosswalk_path,
            )

    @unittest.skipUnless(
        importlib.util.find_spec("ws3") and WS3_SMOKE_MODEL.exists(),
        "ws3 editable install and example Woodstock files are required",
    )
    def test_ws3_smoke_lp_variant_solves_with_replaced_supply(self) -> None:
        variant_dir = Path(self.tmp.name) / "bc-mini-ws3-smoke"
        result = run_ws3_smoke_variant(self.input_dir, variant_dir)

        self.assertEqual(result["status"], "Optimal")
        self.assertAlmostEqual(float(result["objective_value"]), 75520.15192, places=5)
        self.assertEqual(result["graph_nodes"], 32)
        self.assertEqual(result["graph_arcs"], 39)
        self.assertEqual(result["variables"], 24)
        self.assertEqual(result["constraints"], 39)
        self.assertEqual(result["solution_flows"], 16)

        supply_rows = read_csv(variant_dir / "supply_offer.csv")
        summary_rows = read_csv(variant_dir / "generated" / "ws3_smoke_variant_summary.csv")
        summary = {row["metric"]: row["value"] for row in summary_rows}
        self.assertEqual(len(supply_rows), 2)
        self.assertEqual(summary["solver_status"], "Optimal")
        self.assertEqual(summary["graph_nodes"], "32")
        self.assertEqual(summary["graph_arcs"], "39")
        self.assertEqual(summary["smoke_supply_offer_rows"], "2")

    @unittest.skipUnless(
        importlib.util.find_spec("ws3") and WS3_SMOKE_MODEL.exists(),
        "ws3 editable install and example Woodstock files are required",
    )
    def test_variant_comparison_reports_summary_and_flow_deltas(self) -> None:
        self.regenerate_graph()
        build_and_solve(self.input_dir, self.input_dir / "generated")
        variant_dir = Path(self.tmp.name) / "bc-mini-ws3-smoke"
        run_ws3_smoke_variant(self.input_dir, variant_dir)
        output_dir = Path(self.tmp.name) / "comparison"

        result = compare_runs(self.input_dir, variant_dir, output_dir)

        self.assertEqual(result["summary_rows"], 10)
        self.assertEqual(result["flow_rows"], 24)
        summary_rows = read_csv(output_dir / "solution_summary_comparison.csv")
        flow_rows = read_csv(output_dir / "solution_flow_comparison.csv")
        objective = next(row for row in summary_rows if row["metric_id"] == "objective_value")
        self.assertAlmostEqual(float(objective["base_value"]), 6140832.0)
        self.assertAlmostEqual(float(objective["variant_value"]), 75520.15192, places=5)
        self.assertAlmostEqual(float(objective["ratio_variant_to_base"]), 0.012298032566271148)
        lumber_market = next(
            row for row in flow_rows
            if row["origin_node_id"] == "product_pool:lumber"
            and row["destination_node_id"] == "market_lumber_bc"
        )
        self.assertAlmostEqual(float(lumber_market["base_quantity"]), 54160.0)
        self.assertAlmostEqual(float(lumber_market["variant_quantity"]), 799.53376)

    @unittest.skipUnless(
        importlib.util.find_spec("ws3") and WS3_SMOKE_MODEL.exists(),
        "ws3 editable install and example Woodstock files are required",
    )
    def test_three_way_variant_comparison_reports_decision_tables(self) -> None:
        ws3_variant_dir = Path(self.tmp.name) / "bc-mini-ws3-smoke"
        scanfi_variant_dir = Path(self.tmp.name) / "bc-mini-scanfi-diagnostic"
        scanfi_gauthier_variant_dir = Path(self.tmp.name) / "bc-mini-scanfi-gauthier-diagnostic"
        output_dir = Path(self.tmp.name) / "three-way"

        result = compare_three_way(
            self.input_dir,
            ws3_variant_dir,
            scanfi_variant_dir,
            output_dir,
            scanfi_gauthier_variant_dir=scanfi_gauthier_variant_dir,
        )

        self.assertEqual(result["variant_rows"], 4)
        self.assertEqual(result["objective_rows"], 5)
        self.assertEqual(result["supply_rows"], 2)
        self.assertEqual(result["summary_rows"], 10)
        self.assertEqual(result["flow_rows"], 25)

        matrix = {row["variant_id"]: row for row in read_csv(output_dir / "variant_run_matrix.csv")}
        self.assertEqual(
            set(matrix),
            {
                "synthetic_bc_mini",
                "ws3_smoke",
                "scanfi_diagnostic",
                "scanfi_gauthier_diagnostic",
            },
        )
        self.assertEqual({row["solver_status"] for row in matrix.values()}, {"Optimal"})
        self.assertEqual(matrix["synthetic_bc_mini"]["graph_nodes"], "37")
        self.assertEqual(matrix["ws3_smoke"]["graph_nodes"], "32")
        self.assertEqual(matrix["scanfi_diagnostic"]["graph_nodes"], "37")
        self.assertEqual(matrix["scanfi_gauthier_diagnostic"]["graph_nodes"], "37")

        objectives = {row["metric_id"]: row for row in read_csv(output_dir / "variant_objective_comparison.csv")}
        self.assertAlmostEqual(float(objectives["objective_value"]["synthetic_bc_mini"]), 6140832.0)
        self.assertAlmostEqual(float(objectives["objective_value"]["ws3_smoke"]), 75520.15192, places=5)
        self.assertAlmostEqual(float(objectives["objective_value"]["scanfi_diagnostic"]), 5795998.91275, places=5)
        self.assertAlmostEqual(
            float(objectives["objective_value"]["scanfi_gauthier_diagnostic"]),
            6562205.49236,
            places=5,
        )

        supply = {
            (row["commodity_id"], row["quantity_unit"]): row
            for row in read_csv(output_dir / "variant_supply_comparison.csv")
        }
        self.assertAlmostEqual(float(supply[("log", "m3")]["scanfi_diagnostic_quantity"]), 104700.0)
        self.assertAlmostEqual(
            float(supply[("log", "m3")]["scanfi_gauthier_diagnostic_quantity"]),
            222824.665,
        )
        self.assertAlmostEqual(
            float(supply[("log", "m3")]["scanfi_gauthier_delta_vs_scanfi"]),
            118124.665,
        )
        self.assertAlmostEqual(float(supply[("logging_residue", "odt")]["scanfi_diagnostic_quantity"]), 6600.0)
        self.assertAlmostEqual(
            float(supply[("logging_residue", "odt")]["scanfi_gauthier_diagnostic_quantity"]),
            6600.0,
        )
        self.assertTrue((output_dir / "decision_memo.md").exists())

    def test_unit_registry_rejects_cross_dimension_conversion(self) -> None:
        unit_registry = Path(self.tmp.name) / "unit-registry.csv"
        conversion_registry = Path(self.tmp.name) / "unit-conversion-registry.csv"
        shutil.copy(UNIT_REGISTRY, unit_registry)
        shutil.copy(CONVERSION_REGISTRY, conversion_registry)
        rows = read_csv(conversion_registry)
        rows.append(
            {
                "from_unit": "MWh",
                "to_unit": "m3",
                "multiplier": "1",
                "status": "bad_test",
                "notes": "intentional dimension mismatch",
            }
        )
        write_csv(conversion_registry, rows)

        with self.assertRaises(UnitValidationError):
            load_unit_registry(unit_registry, conversion_registry)


if __name__ == "__main__":
    unittest.main()
