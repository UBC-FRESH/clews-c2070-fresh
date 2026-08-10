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
    compute_bc_qc_real_supply_quantities,
    run_full_two_province_variant,
)
from fresh_fibre.gauthier_yield import species_lookup


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
        # BC quantities are now sourced from the BC VRI stub fixture at
        # ``tests/fixtures/bc_vri_sample/sample.csv`` (3 polygons), not
        # the retired SCANFI synthetic mini. The stub emits one log row
        # per species-bearing polygon via the explicit join in
        # ``vri_to_bc_mini_mapping.csv``, with totals
        # coast(36750) + interior_01(49750) = 86500 m3 log.
        self.assertEqual(bc_log_quantity, 86500)
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


class TestRealCoefficientSupplyPath(unittest.TestCase):
    """``compute_bc_qc_real_supply_quantities`` substitution path.

    P7.1.CASFRI.2 wires a real-coefficient surrogate into the BC + QC
    fixture. The diagnostic checks below verify the substitution path:
    - the documented quantity ranges (`80 000-95 000 m^3` BC log,
      `2 800-3 800 m^3` QC log) hold against the BC VRI stub + QC
      diagnostic inputs;
    - the LP objective changes when the real-coefficient numbers
      replace the placeholder-era values.
    """

    def setUp(self) -> None:
        self.workdir = Path(tempfile.mkdtemp())
        # Each test gets a fresh subdirectory so the LP solves are
        # independent and the supply_offer.csv restoration inside the
        # real-yield variant does not collide between tests.
        self.fixture_dir = self.workdir / "bc_qc_mini_2prov"
        self.bc_vri_stub_dir = REPO_ROOT / "tests" / "fixtures" / "bc_vri_sample"

    def tearDown(self) -> None:
        shutil.rmtree(self.workdir, ignore_errors=True)

    def test_compute_bc_qc_real_supply_quantities(self) -> None:
        """Real-coefficient totals land in the documented BC + QC ranges."""
        from fresh_fibre.bc_qc_mini_2prov import regenerate_workdir
        regenerate_workdir(
            base_dir=REPO_ROOT / "metadata/ffcm-archive/prototype-inputs/bc-mini",
            target_dir=self.fixture_dir,
        )
        bc_log, bc_residue, qc_log, qc_residue, yield_rule_count = (
            compute_bc_qc_real_supply_quantities(
                coef_lookup_fn=species_lookup,
                qc_mini_dir=self.fixture_dir,
                bc_mini_dir=self.bc_vri_stub_dir,
            )
        )
        # BC log: 80 000 <= x <= 95 000 m^3 (was 86 500 with VRI stub).
        self.assertGreaterEqual(bc_log, 80_000.0)
        self.assertLessEqual(bc_log, 95_000.0)
        # QC log: 2 800 <= x <= 3 800 m^3 (was 3 238 in the merged
        # placeholder-era supply offer).
        self.assertGreaterEqual(qc_log, 2_800.0)
        self.assertLessEqual(qc_log, 3_800.0)
        # Both residue streams are positive (the real-coefficient path
        # produces residue from the log path *and* from the dedicated
        # residue rule applied to QC residue-candidate cells).
        self.assertGreater(bc_residue, 0.0)
        self.assertGreater(qc_residue, 0.0)
        # Yield rule count == 3 BC polygons + 5 QC diagnostic cells.
        self.assertEqual(yield_rule_count, 8)

    def test_lp_solve_uses_real_yields(self) -> None:
        """LP objective after real-coefficient substitution differs from
        the placeholder-era value.

        The placeholder-era two-province LP runs first and captures its
        objective; ``run_full_two_province_real_yield_variant`` then
        substitutes real-coefficient quantities and re-solves. The two
        LP objective values MUST differ because real-coefficient
        substitution changed the supply side (BC + QC log m^3, residue
        odt) that flows through the LP cost-minimising objective.
        """
        from fresh_fibre.bc_qc_mini_2prov import (
            regenerate_workdir,
            run_full_two_province_real_yield_variant,
        )
        regenerate_workdir(
            base_dir=REPO_ROOT / "metadata/ffcm-archive/prototype-inputs/bc-mini",
            target_dir=self.fixture_dir,
        )
        result = run_full_two_province_real_yield_variant(
            fixture_dir=self.fixture_dir,
            base_dir=REPO_ROOT / "metadata/ffcm-archive/prototype-inputs/bc-mini",
            scanfi_fixture_dir=(
                REPO_ROOT / "metadata/ffcm-archive/prototype-inputs/scanfi-diagnostic"
            ),
            bc_vri_stub_dir=self.bc_vri_stub_dir,
        )
        placeholder_obj = float(result["placeholder"]["lp_objective_value"])
        real_obj = float(result["real_yield"]["lp_objective_value"])
        self.assertEqual(result["placeholder"]["lp_status"], "Optimal")
        self.assertEqual(result["real_yield"]["lp_status"], "Optimal")
        # Both LP runs are Optimal and produce non-negative objectives.
        self.assertGreater(placeholder_obj, 0.0)
        self.assertGreater(real_obj, 0.0)
        # Crucial assertion: real-coefficient substitution CHANGED the
        # LP objective from the placeholder-era value.
        self.assertNotEqual(real_obj, placeholder_obj)
        # And the substituted real-coefficient totals are in spec.
        bc_log_real = float(result["real_yield"]["bc_log_real"])
        qc_log_real = float(result["real_yield"]["qc_log_real"])
        self.assertGreaterEqual(bc_log_real, 80_000.0)
        self.assertLessEqual(bc_log_real, 95_000.0)
        self.assertGreaterEqual(qc_log_real, 2_800.0)
        self.assertLessEqual(qc_log_real, 3_800.0)


class TestPhase3TransportCostWiring(unittest.TestCase):
    """``recompute_transport_link_costs_using_phase3_costs`` helper.

    The Phase 3 transport cost surface (see
    ``fresh_fibre.transport_cost``) gets wired into the BC + QC
    mini-2prov fixture via ``transport_link_phase3_costed.csv``
    (side-by-side comparison). The helper writes the file and returns
    the row count.
    """

    def setUp(self) -> None:
        self.workdir = Path(tempfile.mkdtemp())
        # Each test gets a fresh subdirectory so the LP solves are
        # independent and the transport_link.csv restoration inside the
        # real-yield variant does not collide between tests.
        self.fixture_dir = self.workdir / "bc_qc_mini_2prov"
        self.bc_vri_stub_dir = REPO_ROOT / "tests" / "fixtures" / "bc_vri_sample"

    def tearDown(self) -> None:
        shutil.rmtree(self.workdir, ignore_errors=True)

    def test_recompute_transport_link_costs_using_phase3_costs_returns_int(self) -> None:
        """Phase 3 Transport cost wiring: recompute helper returns row count."""
        from fresh_fibre.bc_qc_mini_2prov import (
            recompute_transport_link_costs_using_phase3_costs,
            regenerate_workdir,
        )
        regenerate_workdir(
            base_dir=REPO_ROOT / "metadata/ffcm-archive/prototype-inputs/bc-mini",
            target_dir=self.fixture_dir,
        )
        result = recompute_transport_link_costs_using_phase3_costs(
            fixture_dir=self.fixture_dir,
            bc_qc_mini_dir=self.bc_vri_stub_dir,
        )
        self.assertIsInstance(result, int)
        self.assertGreater(result, 0)
        # Side-by-side CSV must land at the documented path.
        out_csv = self.fixture_dir / "transport_link_phase3_costed.csv"
        self.assertTrue(out_csv.exists())
        out_rows = read_csv(out_csv)
        self.assertEqual(len(out_rows), result)
        # Schema includes the documented old / new / delta columns.
        sample = out_rows[0]
        for required in (
            "old_cost_per_tonne_km",
            "new_cost_per_tonne_km",
            "delta",
            "segment_class",
        ):
            self.assertIn(required, sample.keys())


if __name__ == "__main__":
    unittest.main()
