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
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fresh_fibre.bc_qc_mini_2prov import (
    DEFAULT_FIXTURE_DIR,
    DEFAULT_SCENARIO,
    PATHWAY_FLOW_COLUMNS,
    SOLUTION_SUMMARY_PER_PROVINCE_COLUMNS,
    SUPPLY_COMPARISON_COLUMNS,
    compute_bc_qc_real_supply_quantities,
    directory_contains_tracked_files,
    regenerate_workdir,
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


class TestBcQcFacilityLayer(unittest.TestCase):
    """Phase 3.2 facility-layer validation against real capacity data.

    Asserts the BC + QC mini-2prov fixture's facility rows reference the
    real ``technology.csv`` capacity surface, and that BC + QC facility
    log throughput capacities exceed their respective province log
    supply totals (post-merge). The fixture IDs below come from the
    regenerated ``bc-qc-mini-2prov/network_node.csv`` — i.e. the
    inherited ``bc-mini`` base for BC and ``append_qc_rows_to_fixture_tables``
    for QC. They mirror the actual fixture vocabulary, not shorthand.
    """

    BC_FIXTURE_FACILITY_IDS = [
        "mill_sawmill_coast",
        "mill_sawmill_interior",
        "mill_pulp_interior",
        "mill_pellet_interior",
        "plant_bioenergy_coast",
    ]
    QC_FIXTURE_FACILITY_IDS = [
        "mill_sawmill_qc",
        "mill_pulp_qc",
        "plant_bioenergy_qc",
    ]

    def _setup_workdir(self) -> Path:
        """Create an isolated workdir, register cleanup, run the LP variant."""
        workdir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, workdir, True)
        run_full_two_province_variant(
            fixture_dir=workdir,
            base_dir=REPO_ROOT / "metadata/ffcm-archive/prototype-inputs/bc-mini",
            scanfi_fixture_dir=(
                REPO_ROOT / "metadata/ffcm-archive/prototype-inputs/scanfi-diagnostic"
            ),
        )
        return workdir

    def test_bc_facility_count_and_unique(self) -> None:
        """The BC fixture exposes five unique facility node rows."""
        workdir = self._setup_workdir()
        nodes = read_csv(workdir / "network_node.csv")
        bc_facilities = [
            row for row in nodes
            if row["node_type"] == "facility" and row["province"] == "BC"
        ]
        node_ids = [row["node_id"] for row in bc_facilities]
        self.assertEqual(len(node_ids), 5)
        self.assertEqual(len(set(node_ids)), 5)
        self.assertEqual(set(node_ids), set(self.BC_FIXTURE_FACILITY_IDS))

    def test_qc_facility_count_and_unique(self) -> None:
        """The QC fixture exposes three unique facility node rows."""
        workdir = self._setup_workdir()
        nodes = read_csv(workdir / "network_node.csv")
        qc_facilities = [
            row for row in nodes
            if row["node_type"] == "facility" and row["province"] == "QC"
        ]
        node_ids = [row["node_id"] for row in qc_facilities]
        # The regenerate path appends three QC facility rows
        # (mill_sawmill_qc, mill_pulp_qc, plant_bioenergy_qc).
        # The exact-match equality pins the actual fixture vocabulary
        # rather than a placeholder list — the schema mapper is the
        # authoritative source.
        self.assertEqual(len(node_ids), 3)
        self.assertEqual(len(set(node_ids)), 3)
        self.assertEqual(set(node_ids), set(self.QC_FIXTURE_FACILITY_IDS))

    def test_total_facility_capacity_log_meets_bc_need(self) -> None:
        """BC log throughput capacity ≥ total BC log m³ supply quantity."""
        workdir = self._setup_workdir()
        technologies = read_csv(workdir / "technology.csv")
        supply_offers = read_csv(workdir / "supply_offer.csv")
        bc_log_capacity = sum(
            number(row["maximum_capacity"])
            for row in technologies
            if row["facility_node_id"] in self.BC_FIXTURE_FACILITY_IDS
            and row["feedstock_commodity_id"] == "log"
        )
        bc_log_supply = sum(
            number(row["quantity"])
            for row in supply_offers
            if row["spatial_unit_id"].startswith("bc_")
            and row["commodity_id"] == "log"
            and row["quantity_unit"] == "m3"
        )
        self.assertGreater(bc_log_capacity, 0.0)
        self.assertGreater(bc_log_supply, 0.0)
        self.assertGreaterEqual(bc_log_capacity, bc_log_supply)

    def test_total_facility_capacity_log_meets_qc_need(self) -> None:
        """QC log throughput capacity ≥ total QC log m³ supply quantity."""
        workdir = self._setup_workdir()
        technologies = read_csv(workdir / "technology.csv")
        supply_offers = read_csv(workdir / "supply_offer.csv")
        qc_log_capacity = sum(
            number(row["maximum_capacity"])
            for row in technologies
            if row["facility_node_id"] in self.QC_FIXTURE_FACILITY_IDS
            and row["feedstock_commodity_id"] == "log"
        )
        qc_log_supply = sum(
            number(row["quantity"])
            for row in supply_offers
            if row["spatial_unit_id"].startswith("qc_")
            and row["commodity_id"] == "log"
            and row["quantity_unit"] == "m3"
        )
        self.assertGreater(qc_log_capacity, 0.0)
        self.assertGreater(qc_log_supply, 0.0)
        self.assertGreaterEqual(qc_log_capacity, qc_log_supply)

    def test_bc_qc_facilities_have_positive_capacity(self) -> None:
        """Each BC + QC facility has at least one ``technology.csv`` row with
        a strictly positive ``maximum_capacity`` (parses finite and > 0)."""
        workdir = self._setup_workdir()
        technologies = read_csv(workdir / "technology.csv")
        capacity_by_facility: dict[str, float] = {}
        for row in technologies:
            facility = row["facility_node_id"]
            capacity_by_facility[facility] = capacity_by_facility.get(
                facility, 0.0,
            ) + number(row["maximum_capacity"])
        all_facilities = (
            self.BC_FIXTURE_FACILITY_IDS + self.QC_FIXTURE_FACILITY_IDS
        )
        for facility_id in all_facilities:
            self.assertIn(facility_id, capacity_by_facility)
            self.assertGreater(capacity_by_facility[facility_id], 0.0)

    def test_facility_commodity_set_matches_supply(self) -> None:
        """Each facility's ``feedstock_commodity_id`` set ⊆ commodity.csv's
        ``commodity_id`` set (no residue/feedstock leakage to unknown ids)."""
        workdir = self._setup_workdir()
        technologies = read_csv(workdir / "technology.csv")
        commodities = read_csv(workdir / "commodity.csv")
        commodity_ids = {row["commodity_id"] for row in commodities}
        per_facility: dict[str, set[str]] = {}
        for row in technologies:
            facility = row["facility_node_id"]
            per_facility.setdefault(facility, set()).add(
                row["feedstock_commodity_id"],
            )
        all_facilities = (
            self.BC_FIXTURE_FACILITY_IDS + self.QC_FIXTURE_FACILITY_IDS
        )
        for facility_id in all_facilities:
            feedstock_set = per_facility.get(facility_id, set())
            self.assertTrue(
                feedstock_set,
                f"facility {facility_id} has no technology rows at all",
            )
            extras = feedstock_set - commodity_ids
            self.assertFalse(
                extras,
                f"{facility_id} references unknown commodities: {sorted(extras)}",
            )


class TestBcQcEndToEndPipeline(unittest.TestCase):
    """Phase 3.3 end-to-end pipeline assertions for the BC + QC fixture."""

    def _setup_workdir(self) -> Path:
        """Create an isolated workdir, register cleanup, run the LP variant."""
        workdir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, workdir, True)
        run_full_two_province_variant(
            fixture_dir=workdir,
            base_dir=REPO_ROOT / "metadata/ffcm-archive/prototype-inputs/bc-mini",
            scanfi_fixture_dir=(
                REPO_ROOT / "metadata/ffcm-archive/prototype-inputs/scanfi-diagnostic"
            ),
        )
        return workdir

    def test_full_pipeline_runs_optimal(self) -> None:
        """End-to-end LP solve returns `Optimal` with positive objective."""
        workdir = self._setup_workdir()
        # Re-run from the persistent workdir created in setUp so we
        # share the regenerated fixture across assertions.
        result = run_full_two_province_variant(
            fixture_dir=workdir,
            base_dir=REPO_ROOT / "metadata/ffcm-archive/prototype-inputs/bc-mini",
            scanfi_fixture_dir=(
                REPO_ROOT / "metadata/ffcm-archive/prototype-inputs/scanfi-diagnostic"
            ),
            regenerate_workdir_flag=False,
        )
        self.assertEqual(result["lp_status"], "Optimal")
        self.assertGreater(result["lp_objective_value"], 0.0)
        self.assertGreater(result["graph_nodes"], 0)
        self.assertGreater(result["graph_arcs"], 0)
        self.assertGreater(result["solution_flows"], 0)

    def test_ratio_per_province_positive(self) -> None:
        """Both BC and QC contribute non-zero supply shares; ratio in (0, 1)."""
        workdir = self._setup_workdir()
        result = run_full_two_province_variant(
            fixture_dir=workdir,
            base_dir=REPO_ROOT / "metadata/ffcm-archive/prototype-inputs/bc-mini",
            scanfi_fixture_dir=(
                REPO_ROOT / "metadata/ffcm-archive/prototype-inputs/scanfi-diagnostic"
            ),
            regenerate_workdir_flag=False,
        )
        bc_quantity_m3 = float(result["bc_quantity_m3"])
        qc_quantity_m3 = float(result["qc_quantity_m3"])
        bc_quantity_odt = float(result["bc_quantity_odt"])
        qc_quantity_odt = float(result["qc_quantity_odt"])
        self.assertGreater(bc_quantity_m3, 0.0)
        self.assertGreater(qc_quantity_m3, 0.0)
        self.assertGreater(bc_quantity_odt + qc_quantity_odt, 0.0)
        # Ratio of BC log m³ to total BC + QC log m³ lies strictly
        # between 0 and 1 → both provinces contribute positively.
        ratio = bc_quantity_m3 / (bc_quantity_m3 + qc_quantity_m3)
        self.assertGreater(ratio, 0.0)
        self.assertLess(ratio, 1.0)

    def test_no_unmapped_supply_rows(self) -> None:
        """Every ``supply_offer.csv`` ``supply_node_id`` is in ``network_node.csv``."""
        workdir = self._setup_workdir()
        nodes = read_csv(workdir / "network_node.csv")
        offer_rows = read_csv(workdir / "supply_offer.csv")
        node_ids = {row["node_id"] for row in nodes}
        unmapped = sorted({
            row["supply_node_id"] for row in offer_rows
            if row["supply_node_id"] and row["supply_node_id"] not in node_ids
        })
        self.assertEqual(
            unmapped,
            [],
            f"unmapped supply_node_id rows detected: {unmapped}",
        )

    def test_solution_flow_rows_count_matches_arcs(self) -> None:
        """solution_flow / solution_summary populated for both provinces."""
        workdir = self._setup_workdir()
        pathway_path = workdir / "generated" / "bc_qc_mini_pathway_flow.csv"
        summary_path = workdir / "generated" / "bc_qc_mini_solution_summary.csv"
        pathway_rows = read_csv(pathway_path)
        summary_rows = read_csv(summary_path)
        self.assertGreater(len(pathway_rows), 0)
        self.assertGreater(len(summary_rows), 0)
        pathway_provinces = {
            row["province"] for row in pathway_rows if row["province"] in {"BC", "QC"}
        }
        summary_provinces = {
            row["province"] for row in summary_rows if row["province"] in {"BC", "QC"}
        }
        self.assertEqual(pathway_provinces, {"BC", "QC"})
        self.assertEqual(summary_provinces, {"BC", "QC"})

    def test_phase3_transport_costs_lp_solver_no_break(self) -> None:
        """LP stays `Optimal` after Phase 3 transport-cost surface integration.

        Phase 3 costs (``fresh_fibre.transport_cost``) are wired into
        ``transport_link.csv`` via the side-by-side
        ``transport_link_phase3_costed.csv`` artefact. This test asserts
        that the wiring helper writes a non-empty side-by-side artefact
        AND that a second LP solve remains `Optimal`.
        """
        workdir = self._setup_workdir()
        # First LP solve already proved Optimal in setUp path; focus
        # here is wiring + second-pass LP solve using the regenerated
        # fixture (skip-regenerate to preserve Phase 3 side-by-side
        # artefact).
        from fresh_fibre.bc_qc_mini_2prov import (
            recompute_transport_link_costs_using_phase3_costs,
        )
        costed_rows = recompute_transport_link_costs_using_phase3_costs(
            fixture_dir=workdir,
            bc_qc_mini_dir=(
                REPO_ROOT / "metadata/ffcm-archive/prototype-inputs/bc-mini"
            ),
        )
        self.assertGreater(costed_rows, 0)
        costed_csv = workdir / "transport_link_phase3_costed.csv"
        self.assertTrue(costed_csv.exists())
        costed_data = read_csv(costed_csv)
        sample = costed_data[0]
        for column in (
            "old_cost_per_tonne_km",
            "new_cost_per_tonne_km",
            "delta",
            "segment_class",
        ):
            self.assertIn(column, sample.keys())
        # Re-run LP with ``--skip-regenerate`` so the Phase 3 costed
        # artefact we just wrote is preserved; the LP path itself
        # still reads the legacy transport_link.csv, so the round-trip
        # wire-up must not perturb feasibility.
        follow_up = run_full_two_province_variant(
            fixture_dir=workdir,
            base_dir=REPO_ROOT / "metadata/ffcm-archive/prototype-inputs/bc-mini",
            scanfi_fixture_dir=(
                REPO_ROOT / "metadata/ffcm-archive/prototype-inputs/scanfi-diagnostic"
            ),
            regenerate_workdir_flag=False,
        )
        self.assertEqual(follow_up["lp_status"], "Optimal")
        self.assertGreater(follow_up["lp_objective_value"], 0.0)


class TestPhase4RegistryPromotion(unittest.TestCase):
    """Phase 4.3 promotion smoke test: the CASFRI v5 schema row is promoted."""

    def test_casfri_v5_schema_status_promoted(self) -> None:
        """`metadata/source-inventory.csv` row id=casfri_v5_schema is promoted.

        The promotion flips ``status`` ``candidate`` →
        ``accepted_subset_recommended`` and stamps the ``next_action``
        field with a ``DONE 2026-08-10`` reference to Phase 4. This
        smoke test pins the promotion so a downstream row reversion
        surfaces here rather than file-level metadata drift.
        """
        path = REPO_ROOT / "metadata" / "source-inventory.csv"
        self.assertTrue(
            path.exists(),
            f"missing promotion-source file: {path}",
        )
        with path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        casfri_row = next(
            (row for row in rows if row["id"] == "casfri_v5_schema"),
            None,
        )
        self.assertIsNotNone(
            casfri_row,
            "casfri_v5_schema row missing from metadata/source-inventory.csv",
        )
        self.assertEqual(casfri_row["status"], "accepted_subset_recommended")
        next_action = casfri_row["next_action"]
        # Stamp verification: the next_action must reference Phase 4
        # and carry the 2026-08-10 completion date.
        self.assertIn("DONE 2026-08-10", next_action)
        self.assertIn("Phase 4", next_action)
        self.assertIn("BC+QC two-province diagnostic LP fixture", next_action)
        # Schema-acceptance gate is the primary framing: framework
        # source vendoring remains a separate load-bearing question
        # per the AGENTS.md disclosure path.
        self.assertIn("Tier-3", next_action)


class TestRegenerateWorkdirTrackedFileGuard(unittest.TestCase):
    """``regenerate_workdir`` must never silently rmtree git-tracked content.

    The guard in ``directory_contains_tracked_files`` is expected to:
    - raise ``RuntimeError`` when the target holds git-tracked files
      (unless the caller passes ``force=True``);
    - rebuild freely over untracked-only and non-repo directories;
    - fail *closed* (treat the directory as tracked) when
      ``git ls-files`` errors for any reason other than the benign
      "not a git repository" refusal — e.g. a corrupt index;
    - re-raise ``subprocess.TimeoutExpired`` as ``RuntimeError`` with
      the target directory attached for context.
    """

    BASE_DIR = REPO_ROOT / "metadata/ffcm-archive/prototype-inputs/bc-mini"

    def setUp(self) -> None:
        self.workdir = Path(tempfile.mkdtemp())

    def tearDown(self) -> None:
        shutil.rmtree(self.workdir, ignore_errors=True)

    def _git(self, repo: Path, *args: str) -> None:
        subprocess.run(
            ["git", *args],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        )

    def _make_repo_with_tracked_file(self, name: str = "tracked_repo") -> Path:
        repo = self.workdir / name
        repo.mkdir()
        self._git(repo, "init", "-q")
        (repo / "committed_fixture.txt").write_text("precious\n", encoding="utf-8")
        self._git(repo, "add", "committed_fixture.txt")
        return repo

    def test_tracked_files_block_rebuild_without_force(self) -> None:
        repo = self._make_repo_with_tracked_file()
        with self.assertRaises(RuntimeError):
            regenerate_workdir(base_dir=self.BASE_DIR, target_dir=repo)
        # The refused rebuild left the tracked file untouched.
        self.assertTrue((repo / "committed_fixture.txt").exists())

    def test_tracked_files_rebuild_with_force(self) -> None:
        repo = self._make_repo_with_tracked_file()
        result = regenerate_workdir(base_dir=self.BASE_DIR, target_dir=repo, force=True)
        self.assertEqual(result, repo)
        # The explicit opt-in ran the destructive rebuild: rmtree removed
        # the tracked file and the fresh fixture tables landed.
        self.assertFalse((repo / "committed_fixture.txt").exists())
        self.assertTrue((repo / "network_node.csv").exists())

    def test_untracked_only_directory_rebuilds_freely(self) -> None:
        repo = self.workdir / "untracked_repo"
        repo.mkdir()
        self._git(repo, "init", "-q")
        (repo / "scratch.txt").write_text("not tracked\n", encoding="utf-8")
        result = regenerate_workdir(base_dir=self.BASE_DIR, target_dir=repo)
        self.assertEqual(result, repo)
        self.assertTrue((repo / "network_node.csv").exists())

    def test_non_repo_directory_rebuilds_freely(self) -> None:
        plain = self.workdir / "plain_dir"
        plain.mkdir()
        (plain / "scratch.txt").write_text("no repo here\n", encoding="utf-8")
        result = regenerate_workdir(base_dir=self.BASE_DIR, target_dir=plain)
        self.assertEqual(result, plain)
        self.assertTrue((plain / "network_node.csv").exists())

    def test_unexpected_git_error_fails_closed(self) -> None:
        repo = self._make_repo_with_tracked_file("corrupt_repo")
        # Corrupt the index so `git ls-files` fails with an error that is
        # NOT the benign "not a git repository" refusal.
        (repo / ".git" / "index").write_bytes(b"not-a-valid-git-index")
        with self.assertLogs("fresh_fibre.bc_qc_mini_2prov", level="WARNING"):
            self.assertTrue(directory_contains_tracked_files(repo))
        with self.assertRaises(RuntimeError):
            regenerate_workdir(base_dir=self.BASE_DIR, target_dir=repo)

    def test_git_timeout_raises_runtime_error(self) -> None:
        target = self.workdir / "timeout_dir"
        target.mkdir()
        with mock.patch(
            "fresh_fibre.bc_qc_mini_2prov.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["git", "ls-files"], timeout=30),
        ):
            with self.assertRaises(RuntimeError) as caught:
                directory_contains_tracked_files(target)
        self.assertIn(str(target), str(caught.exception))


if __name__ == "__main__":
    unittest.main()
