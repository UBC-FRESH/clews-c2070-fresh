"""Regression tests for the BC mini-network FRESH prototype."""

from __future__ import annotations

import csv
import shutil
import tempfile
import unittest
from pathlib import Path

from fresh_fibre import graph
from fresh_fibre.contract import assert_contract_ok, validate_contract
from fresh_fibre.lp import build_and_solve
from fresh_fibre.units import UnitValidationError, load_unit_registry
from fresh_fibre.validation import validate


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = REPO_ROOT / "metadata/ffcm-archive/prototype-inputs/bc-mini"
UNIT_REGISTRY = REPO_ROOT / "metadata/ffcm-archive/unit-registry.csv"
CONVERSION_REGISTRY = REPO_ROOT / "metadata/ffcm-archive/unit-conversion-registry.csv"


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
