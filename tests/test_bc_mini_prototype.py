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
from fresh_fibre.lp import build_and_solve
from fresh_fibre.units import UnitValidationError, load_unit_registry
from fresh_fibre.validation import validate
from fresh_fibre.ws3_export import export_supply_offer
from fresh_fibre.ws3_smoke import run_ws3_smoke
from fresh_fibre.ws3_variant import run_ws3_smoke_variant


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = REPO_ROOT / "metadata/ffcm-archive/prototype-inputs/bc-mini"
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
