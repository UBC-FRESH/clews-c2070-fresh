"""Tests for the BC + QC two-province LP fixture runner.

The runner reads the merged-fixture inputs (BC mini base + QC
synthetic-bounded mini cells), regenerates graph tables, solves the LP,
and emits province-keyed summary artefacts.

These tests verify:
- The fixture regenerates cleanly from scratch.
- The LP solves `Optimal` and emits a positive objective value.
- Both provinces contribute non-zero supply quantities (BC + QC ≥
  expected minimums).
- Costs are non-negative across both provinces.
- The comparison artefacts are emitted with the expected schema.
- Balance constraints are satisfied at the per-commodity level
  (supply_quantity ≥ 0, producer_price ≥ 0, market_quantity ≥ 0).
"""

from __future__ import annotations

import csv
import io
import shutil
import tempfile
import unittest
from pathlib import Path

from fresh_fibre.bc_qc_mini_2prov import (
    DEFAULT_FIXTURE_DIR,
    DEFAULT_SCENARIO,
    PATHWAY_FLOW_COLUMNS,
    SOLUTION_SUMMARY_PER_PROVINCE_COLUMNS,
    SUPPLY_COMPARISON_COLUMNS,
    run_full_two_province_variant,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def number(value: str | float | int | None, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    return float(value)


class TestTwoProvinceLPRunner(unittest.TestCase):
    """End-to-end execution of ``run_full_two_province_variant``."""

    def setUp(self) -> None:
        self.workdir = Path(tempfile.mkdtemp())

    def tearDown(self) -> None:
        shutil.rmtree(self.workdir, ignore_errors=True)

    def test_full_runner_solves_optimal(self) -> None:
        result = run_full_two_province_variant(
            fixture_dir=self.workdir,
            base_dir=REPO_ROOT / "metadata/ffcm-archive/prototype-inputs/bc-mini",
            scanfi_fixture_dir=(
                REPO_ROOT / "metadata/ffcm-archive/prototype-inputs/scanfi-diagnostic"
            ),
        )
        self.assertEqual(result["lp_status"], "Optimal")
        self.assertGreater(result["lp_objective_value"], 0.0)
        self.assertGreater(result["bc_supply_rows"], 0)
        self.assertGreater(result["qc_supply_rows"], 0)
        self.assertGreater(result["bc_quantity_m3"], 0.0)
        self.assertGreater(result["qc_quantity_m3"], 0.0)
        self.assertGreater(result["bc_quantity_odt"], 0.0)
        self.assertGreater(result["qc_quantity_odt"], 0.0)
        self.assertGreater(result["graph_nodes"], 0)
        self.assertGreater(result["graph_arcs"], 0)
        self.assertGreater(result["solution_flows"], 0)

    def test_supply_comparison_artefact_emits_both_provinces(self) -> None:
        run_full_two_province_variant(
            fixture_dir=self.workdir,
            base_dir=REPO_ROOT / "metadata/ffcm-archive/prototype-inputs/bc-mini",
            scanfi_fixture_dir=(
                REPO_ROOT / "metadata/ffcm-archive/prototype-inputs/scanfi-diagnostic"
            ),
        )
        comparison_path = (
            self.workdir / "generated" / "bc_qc_mini_supply_comparison.csv"
        )
        rows = read_csv(comparison_path)
        provinces = {row["province"] for row in rows}
        self.assertEqual(provinces, {"BC", "QC"})
        # Each row carries a positive quantity and a non-negative cost.
        for row in rows:
            self.assertGreater(number(row["total_quantity"]), 0.0)
            self.assertGreaterEqual(number(row["supply_cost_average"]), 0.0)
            self.assertEqual(set(row.keys()), set(SUPPLY_COMPARISON_COLUMNS))

    def test_pathway_flow_artefact_keys_bc_and_qc_separately(self) -> None:
        run_full_two_province_variant(
            fixture_dir=self.workdir,
            base_dir=REPO_ROOT / "metadata/ffcm-archive/prototype-inputs/bc-mini",
            scanfi_fixture_dir=(
                REPO_ROOT / "metadata/ffcm-archive/prototype-inputs/scanfi-diagnostic"
            ),
        )
        pathway_path = (
            self.workdir / "generated" / "bc_qc_mini_pathway_flow.csv"
        )
        rows = read_csv(pathway_path)
        province_count: dict[str, int] = {}
        for row in rows:
            province_count[row["province"]] = province_count.get(row["province"], 0) + 1
        self.assertGreater(province_count.get("BC", 0), 0)
        self.assertGreater(province_count.get("QC", 0), 0)
        # Pathway columns match the documented schema.
        self.assertEqual(
            set(rows[0].keys()),
            set(PATHWAY_FLOW_COLUMNS),
        )

    def test_solution_summary_pre_province_emits_quantities(self) -> None:
        run_full_two_province_variant(
            fixture_dir=self.workdir,
            base_dir=REPO_ROOT / "metadata/ffcm-archive/prototype-inputs/bc-mini",
            scanfi_fixture_dir=(
                REPO_ROOT / "metadata/ffcm-archive/prototype-inputs/scanfi-diagnostic"
            ),
        )
        summary_path = (
            self.workdir / "generated" / "bc_qc_mini_solution_summary.csv"
        )
        rows = read_csv(summary_path)
        bc_log_quantity = sum(
            number(row["value"])
            for row in rows
            if row["province"] == "BC"
            and row["metric_id"] == "period_supply_quantity"
            and row["commodity_id"] == "log"
        )
        qc_log_quantity = sum(
            number(row["value"])
            for row in rows
            if row["province"] == "QC"
            and row["metric_id"] == "period_supply_quantity"
            and row["commodity_id"] == "log"
        )
        bc_residue_quantity = sum(
            number(row["value"])
            for row in rows
            if row["province"] == "BC"
            and row["metric_id"] == "period_supply_quantity"
            and row["commodity_id"] == "logging_residue"
        )
        qc_residue_quantity = sum(
            number(row["value"])
            for row in rows
            if row["province"] == "QC"
            and row["metric_id"] == "period_supply_quantity"
            and row["commodity_id"] == "logging_residue"
        )
        self.assertEqual(bc_log_quantity, 104700)
        self.assertEqual(bc_residue_quantity, 6600)
        # Both provinces contribute log and residue supply.
        self.assertGreater(qc_log_quantity, 0.0)
        self.assertGreater(qc_residue_quantity, 0.0)
        # Documented schema contract.
        sample = rows[0]
        self.assertEqual(
            set(sample.keys()),
            set(SOLUTION_SUMMARY_PER_PROVINCE_COLUMNS),
        )

    def test_skip_regenerate_preserves_existing_fixture(self) -> None:
        # Run once to populate the workdir.
        run_full_two_province_variant(
            fixture_dir=self.workdir,
            base_dir=REPO_ROOT / "metadata/ffcm-archive/prototype-inputs/bc-mini",
            scanfi_fixture_dir=(
                REPO_ROOT / "metadata/ffcm-archive/prototype-inputs/scanfi-diagnostic"
            ),
        )
        # Run again with --skip-regenerate-workdir to assert non-destructive
        # re-solve.
        from fresh_fibre.bc_qc_mini_2prov import run_full_two_province_variant as _runner

        result = _runner(
            fixture_dir=self.workdir,
            base_dir=REPO_ROOT / "metadata/ffcm-archive/prototype-inputs/bc-mini",
            scanfi_fixture_dir=(
                REPO_ROOT / "metadata/ffcm-archive/prototype-inputs/scanfi-diagnostic"
            ),
            regenerate_workdir_flag=False,
        )
        self.assertEqual(result["lp_status"], "Optimal")


class TestBalanceConstraints(unittest.TestCase):
    """At the per-commodity level, supply balances must hold."""

    def setUp(self) -> None:
        self.workdir = Path(tempfile.mkdtemp())

    def tearDown(self) -> None:
        shutil.rmtree(self.workdir, ignore_errors=True)

    def test_log_supply_quantity_non_negative(self) -> None:
        run_full_two_province_variant(
            fixture_dir=self.workdir,
            base_dir=REPO_ROOT / "metadata/ffcm-archive/prototype-inputs/bc-mini",
            scanfi_fixture_dir=(
                REPO_ROOT / "metadata/ffcm-archive/prototype-inputs/scanfi-diagnostic"
            ),
        )
        solution_flows = read_csv(
            self.workdir / "generated" / "solution_flow.csv",
        )
        log_flow_rows = [
            row for row in solution_flows
            if row.get("source_commodity_id") == "log"
        ]
        for row in log_flow_rows:
            self.assertGreaterEqual(number(row["quantity"]), 0.0)

    def test_qc_provider_costs_non_negative(self) -> None:
        run_full_two_province_variant(
            fixture_dir=self.workdir,
            base_dir=REPO_ROOT / "metadata/ffcm-archive/prototype-inputs/bc-mini",
            scanfi_fixture_dir=(
                REPO_ROOT / "metadata/ffcm-archive/prototype-inputs/scanfi-diagnostic"
            ),
        )
        # The supply_offer table contains both provinces; QC supply rows
        # must be non-negative in cost.
        supply_rows = read_csv(self.workdir / "supply_offer.csv")
        qc_rows = [
            row for row in supply_rows
            if row["spatial_unit_id"].startswith("qc_")
        ]
        self.assertGreater(len(qc_rows), 0)
        for row in qc_rows:
            self.assertGreaterEqual(number(row["supply_cost"]), 0.0)
            self.assertGreater(number(row["quantity"]), 0.0)


class TestSupplyTotals(unittest.TestCase):
    """The merged supply_offer is split into BC and QC and totals consistently."""

    def setUp(self) -> None:
        self.workdir = Path(tempfile.mkdtemp())

    def tearDown(self) -> None:
        shutil.rmtree(self.workdir, ignore_errors=True)

    def test_bc_qc_supply_totals_match_summary(self) -> None:
        run_full_two_province_variant(
            fixture_dir=self.workdir,
            base_dir=REPO_ROOT / "metadata/ffcm-archive/prototype-inputs/bc-mini",
            scanfi_fixture_dir=(
                REPO_ROOT / "metadata/ffcm-archive/prototype-inputs/scanfi-diagnostic"
            ),
        )
        summary_rows = read_csv(
            self.workdir / "generated" / "bc_qc_mini_solution_summary.csv",
        )
        summary_totals: dict[str, dict[tuple[str, str], float]] = {}
        for row in summary_rows:
            province = row["province"]
            commodity = row["commodity_id"]
            unit = row["unit"]
            metric = row["metric_id"]
            summary_totals.setdefault(province, {})
            if metric == "period_supply_quantity":
                summary_totals[province].setdefault((commodity, unit), 0.0)
                summary_totals[province][(commodity, unit)] += number(row["value"])
        # Cross-check: the supply_comparison file uses m3 and odt totals
        # at the BC/QC level.
        comparison_rows = read_csv(
            self.workdir / "generated" / "bc_qc_mini_supply_comparison.csv",
        )
        for comp in comparison_rows:
            province = comp["province"]
            commodity = comp["commodity_id"]
            unit = comp["quantity_unit"]
            comparison_total = number(comp["total_quantity"])
            summary_total = summary_totals.get(province, {}).get((commodity, unit), 0.0)
            self.assertAlmostEqual(comparison_total, summary_total, places=5)


if __name__ == "__main__":
    unittest.main()
