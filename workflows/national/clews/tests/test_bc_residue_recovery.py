"""Tests for the recoverable-residue supply curve and time path builder."""

from __future__ import annotations

import csv
import math
import tempfile
import unittest
from pathlib import Path

from workflows.national.clews.bc_residue_recovery import (
    AGB_AGE,
    ALL_BC_AAC_2018_M3,
    BASE_RECOVERY_FALLBACK,
    CASE_COST_MULTIPLIER,
    HORIZON_END,
    HORIZON_START,
    LHV_GJ_PER_ODT,
    PLACEHOLDER_COST_USD_PER_GJ,
    PLACEHOLDER_PJ,
    PREVIOUS_GROSS_PJ,
    RECOVERY_MAX,
    RECOVERY_MIN,
    FfcmBcSupply,
    FfcmFmu,
    build_all,
    build_recovery,
    ffcm_fmu_rows,
    parameter_rows,
    summary_payload,
    write_artifacts,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
OUTPUT_DIR = Path("data/derived/national/clews")


class BcResidueRecoveryTestCase(unittest.TestCase):
    """Integration tests over the committed derived artifacts."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.artifacts = build_all(REPO_ROOT)

    def test_ffcm_bc_ledger(self) -> None:
        ffcm = self.artifacts.ffcm
        self.assertEqual(ffcm.fmu_count, 467)
        self.assertAlmostEqual(ffcm.total_log_m3, 134_745_540.34532154, places=6)
        self.assertAlmostEqual(ffcm.total_residue_odt, 8_488_969.041755255, places=6)
        self.assertAlmostEqual(ffcm.residue_odt_per_m3, 0.063, places=12)
        self.assertAlmostEqual(
            ffcm.residue_cost_usd_per_odt, 45.4540846589377, places=12
        )
        self.assertAlmostEqual(
            ffcm.residue_cost_usd_per_gj, 45.4540846589377 / 18.5, places=12
        )
        self.assertEqual(
            ffcm.residue_cost_min_usd_per_odt,
            ffcm.residue_cost_max_usd_per_odt,
        )
        self.assertTrue(all(fmu.minimum_supply_odt == 0.0 for fmu in ffcm.fmus))

    def test_aac_workbook_reconciles_2018(self) -> None:
        aac = self.artifacts.aac
        self.assertEqual(len(aac.by_tsa), 37)
        self.assertAlmostEqual(aac.total_by_year[2018], ALL_BC_AAC_2018_M3, places=6)
        self.assertAlmostEqual(aac.total_by_year[2021], 49_216_819.8, places=6)
        self.assertAlmostEqual(aac.total_by_year[2028], 43_370_775.8, places=6)
        self.assertEqual(
            aac.zero_aac_sheets,
            (
                "Kingcome",
                "Mid Coast",
                "North Coast",
                "Strathcona",
            ),
        )
        for year in (2018, *range(2021, 2029)):
            self.assertIn(year, aac.complete_years)
        self.assertEqual(
            set(aac.missing_tsa_years),
            {"GBR North", "GBR South", "North Island"},
        )
        for tsa, years in aac.missing_tsa_years.items():
            self.assertEqual(years, tuple(range(2011, 2017)), tsa)

    def test_recovery_bounds_and_b1_fallback(self) -> None:
        recovery = {row.zone_id: row for row in self.artifacts.recovery}
        self.assertGreater(recovery["B1"].implied_recovery_fraction, RECOVERY_MAX)
        self.assertFalse(recovery["B1"].recovery_in_bounds)
        self.assertAlmostEqual(
            recovery["B1"].base_recovery_fraction, BASE_RECOVERY_FALLBACK, places=12
        )
        self.assertIn("outside", recovery["B1"].flag)

        self.assertGreaterEqual(recovery["B2"].implied_recovery_fraction, RECOVERY_MIN)
        self.assertLessEqual(recovery["B2"].implied_recovery_fraction, RECOVERY_MAX)
        self.assertTrue(recovery["B2"].recovery_in_bounds)
        self.assertAlmostEqual(
            recovery["B2"].base_recovery_fraction,
            recovery["B2"].implied_recovery_fraction,
            places=12,
        )
        self.assertTrue(recovery["CAN"].comparison_only)
        for row in recovery.values():
            self.assertGreaterEqual(row.base_recovery_fraction, RECOVERY_MIN)
            self.assertLessEqual(row.base_recovery_fraction, RECOVERY_MAX)

    def test_recovery_synthetic_out_of_bounds_fallback(self) -> None:
        ffcm = FfcmBcSupply(
            fmus=(
                FfcmFmu(
                    fmu="BC1",
                    log_m3=1000.0,
                    residue_odt=50.0,
                    log_cost_usd_per_m3=20.0,
                    residue_cost_usd_per_odt=37.0,
                    minimum_supply_odt=0.0,
                ),
            ),
            total_log_m3=1000.0,
            total_residue_odt=50.0,
            residue_odt_per_m3=0.05,
            residue_cost_usd_per_odt=37.0,
            residue_cost_usd_per_gj=2.0,
            residue_cost_min_usd_per_odt=37.0,
            residue_cost_max_usd_per_odt=37.0,
            log_cost_usd_per_m3=20.0,
            source_path=Path("synthetic.csv"),
        )
        rows = build_recovery(
            ffcm,
            zone_shares={"B1": 0.5, "B2": 0.5},
            taper_ratios={"B1": 0.04, "B2": 0.10},
        )
        by_zone = {row.zone_id: row for row in rows}
        self.assertAlmostEqual(by_zone["B1"].implied_recovery_fraction, 1.25, places=12)
        self.assertFalse(by_zone["B1"].recovery_in_bounds)
        self.assertAlmostEqual(
            by_zone["B1"].base_recovery_fraction, BASE_RECOVERY_FALLBACK, places=12
        )
        self.assertAlmostEqual(by_zone["B2"].implied_recovery_fraction, 0.50, places=12)
        self.assertAlmostEqual(by_zone["B2"].base_recovery_fraction, 0.50, places=12)

    def test_time_path_has_50_years_and_2028_hold(self) -> None:
        rows = self.artifacts.time_path
        self.assertEqual(len(rows), 50 * 3)
        for zone_id in ("B1", "B2", "CAN"):
            zone_rows = [row for row in rows if row.zone_id == zone_id]
            self.assertEqual(len(zone_rows), 50)
            self.assertEqual(
                [row.year for row in zone_rows],
                list(range(HORIZON_START, HORIZON_END + 1)),
            )
            hold = next(row for row in zone_rows if row.year == 2028)
            for row in zone_rows:
                if row.year < 2029:
                    self.assertEqual(row.aac_year, row.year)
                else:
                    self.assertEqual(row.aac_year, 2028)
                    self.assertAlmostEqual(
                        row.annual_log_m3, hold.annual_log_m3, places=9
                    )
                self.assertEqual(row.time_path_basis, "aac_2028_hold")

    def test_odt_to_pj_everywhere(self) -> None:
        for row in self.artifacts.time_path:
            self.assertAlmostEqual(
                row.gross_taper_residue_pj,
                row.gross_taper_residue_odt * LHV_GJ_PER_ODT / 1_000_000.0,
                places=9,
            )
        for case in self.artifacts.cases:
            for row in case.rows:
                self.assertAlmostEqual(
                    row.residue_pj,
                    row.residue_odt * LHV_GJ_PER_ODT / 1_000_000.0,
                    places=9,
                )

    def test_cost_conversion_and_multipliers(self) -> None:
        base_cost = 45.4540846589377 / 18.5
        cases = {case.name: case for case in self.artifacts.cases}
        recovery = {row.zone_id: row for row in self.artifacts.recovery}
        for zone_id in ("B1", "B2"):
            self.assertAlmostEqual(
                cases["base"].recovery_by_zone[zone_id],
                recovery[zone_id].base_recovery_fraction,
                places=12,
            )
            self.assertAlmostEqual(
                cases["base"].cost_usd_per_gj_by_zone[zone_id], base_cost, places=12
            )
            self.assertAlmostEqual(
                cases["low"].cost_usd_per_gj_by_zone[zone_id],
                base_cost * CASE_COST_MULTIPLIER["low"],
                places=12,
            )
            self.assertAlmostEqual(
                cases["high"].cost_usd_per_gj_by_zone[zone_id],
                base_cost * CASE_COST_MULTIPLIER["high"],
                places=12,
            )

    def test_case_monotonicity(self) -> None:
        cases = {case.name: case for case in self.artifacts.cases}
        for year in range(HORIZON_START, HORIZON_END + 1):
            for zone_id in ("B1", "B2", "CAN"):
                low = cases["low"].row(year, zone_id)
                base = cases["base"].row(year, zone_id)
                high = cases["high"].row(year, zone_id)
                self.assertLess(low.residue_pj, base.residue_pj)
                self.assertLess(base.residue_pj, high.residue_pj)
                self.assertGreater(low.cost_usd_per_gj, base.cost_usd_per_gj)
                self.assertGreater(base.cost_usd_per_gj, high.cost_usd_per_gj)

    def test_conservation(self) -> None:
        cases = {case.name: case for case in self.artifacts.cases}
        for year in range(HORIZON_START, HORIZON_END + 1):
            for case in cases.values():
                b1 = case.row(year, "B1")
                b2 = case.row(year, "B2")
                can = case.row(year, "CAN")
                self.assertAlmostEqual(
                    can.annual_log_m3, b1.annual_log_m3 + b2.annual_log_m3, places=6
                )
                self.assertAlmostEqual(
                    can.gross_taper_residue_odt,
                    b1.gross_taper_residue_odt + b2.gross_taper_residue_odt,
                    places=6,
                )
                self.assertAlmostEqual(
                    can.residue_odt, b1.residue_odt + b2.residue_odt, places=6
                )
                self.assertAlmostEqual(
                    can.residue_pj, b1.residue_pj + b2.residue_pj, places=9
                )

        recovery = {row.zone_id: row for row in self.artifacts.recovery}
        self.assertAlmostEqual(
            recovery["CAN"].ffcm_residue_odt,
            recovery["B1"].ffcm_residue_odt + recovery["B2"].ffcm_residue_odt,
            places=6,
        )
        audit_rows = ffcm_fmu_rows(self.artifacts)
        self.assertEqual(len(audit_rows), 467)
        self.assertAlmostEqual(
            sum(float(row["residue_odt_b1"]) for row in audit_rows),
            recovery["B1"].ffcm_residue_odt,
            delta=1e-3,
        )
        self.assertAlmostEqual(
            sum(float(row["residue_odt_b2"]) for row in audit_rows),
            recovery["B2"].ffcm_residue_odt,
            delta=1e-3,
        )

    def test_parameter_rows_shape(self) -> None:
        for case in self.artifacts.cases:
            for technology, zone_id in (("SUPBCWB1", "B1"), ("SUPBCWB2", "B2")):
                activity = parameter_rows(case, technology, zone_id, "activity")
                cost = parameter_rows(case, technology, zone_id, "cost")
                for rows in (activity, cost):
                    self.assertEqual(len(rows), 50)
                    self.assertEqual(
                        list(rows[0]),
                        ["REGION", "TECHNOLOGY", "YEAR", "VALUE"],
                    )
                    self.assertTrue(all(row["REGION"] == "CAN" for row in rows))
                    self.assertEqual(
                        [int(row["YEAR"]) for row in rows],
                        list(range(HORIZON_START, HORIZON_END + 1)),
                    )
                self.assertTrue(all(float(row["VALUE"]) >= 0 for row in activity))
                self.assertTrue(all(float(row["VALUE"]) > 0 for row in cost))

    def test_agb_nonstem_sensitivity(self) -> None:
        agb = self.artifacts.agb
        self.assertEqual(agb.nonstem.age, AGB_AGE)
        self.assertEqual(agb.nonstem.au_count, 16)
        self.assertAlmostEqual(agb.nonstem.median, 0.24683673, places=8)
        self.assertLess(agb.nonstem.minimum, agb.nonstem.median)
        self.assertGreater(agb.nonstem.maximum, agb.nonstem.median)
        zones = {row.zone_id: row for row in agb.zones}
        self.assertAlmostEqual(zones["B1"].ratio_to_taper, 3.067493529, places=6)
        self.assertAlmostEqual(zones["B2"].ratio_to_taper, 1.170142634, places=6)
        self.assertTrue(agb.rsyc_agb_available)
        self.assertGreater(zones["B1"].annual_gross_slash_odt_2021, 0)
        self.assertGreater(zones["B2"].annual_gross_slash_odt_2021, 0)

    def test_summary_contract(self) -> None:
        summary = summary_payload(self.artifacts)
        self.assertEqual(summary["status"], "fresh_side_provisional_not_c2070_official")
        self.assertEqual(summary["time_path"]["basis"], "aac_2028_hold")
        self.assertEqual(summary["time_path"]["horizon"], [2021, 2070])
        self.assertEqual(summary["comparison"]["placeholder_pj"], PLACEHOLDER_PJ)
        self.assertEqual(
            summary["comparison"]["placeholder_cost_usd_per_gj"],
            PLACEHOLDER_COST_USD_PER_GJ,
        )
        self.assertAlmostEqual(
            summary["comparison"]["previous_gross_pj"], PREVIOUS_GROSS_PJ, places=9
        )
        for case_name in ("low", "base", "high"):
            self.assertIn(case_name, summary["cases"])
            self.assertIn("y2021", summary["cases"][case_name])
            self.assertIn("y2028", summary["cases"][case_name])
            self.assertIn("y2070", summary["cases"][case_name])

    def test_output_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            out_dir = Path(temporary)
            written = write_artifacts(self.artifacts, REPO_ROOT, out_dir)
            self.assertEqual(len(written), 25)
            self.assertTrue(
                (out_dir / "audit" / "bc_aac_trajectory_2011_2028.csv").exists()
            )
            for path in written:
                self.assertTrue(path.exists(), path)
            for case in self.artifacts.cases:
                for technology in ("SUPBCWB1", "SUPBCWB2"):
                    for parameter in ("activity_limits", "variable_cost"):
                        path = out_dir / f"{technology}_{parameter}_{case.name}.csv"
                        with path.open(newline="", encoding="utf-8") as handle:
                            rows = list(csv.DictReader(handle))
                        self.assertEqual(len(rows), 50)
                        self.assertEqual(
                            list(rows[0]),
                            ["REGION", "TECHNOLOGY", "YEAR", "VALUE"],
                        )
                national = (
                    out_dir / "comparison" / (f"SUPBCW_activity_limits_{case.name}.csv")
                )
                self.assertTrue(national.exists())
            recovery_path = out_dir / "bc_residue_recovery.csv"
            with recovery_path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual([row["zone_id"] for row in rows], ["B1", "B2", "CAN"])
            time_path = out_dir / "bc_residue_time_path.csv"
            with time_path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 150)

    def test_placeholder_and_previous_comparison(self) -> None:
        cases = {case.name: case for case in self.artifacts.cases}
        base_2021 = cases["base"].row(2021, "CAN").residue_pj
        base_2028 = cases["base"].row(2028, "CAN").residue_pj
        self.assertTrue(math.isclose(base_2021, 50.472521396, rel_tol=1e-9))
        self.assertTrue(math.isclose(base_2028, 42.958794801, rel_tol=1e-9))
        self.assertLess(base_2021, PREVIOUS_GROSS_PJ)
        self.assertLess(base_2028, PREVIOUS_GROSS_PJ)


if __name__ == "__main__":
    unittest.main()
