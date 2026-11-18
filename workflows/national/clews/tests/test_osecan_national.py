"""Tests for the national RSYC 150M log/yr OSeCAN experiment."""

from __future__ import annotations

import csv
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from workflows.national.clews.osecan_national import (
    BC_ZONAL_TOTAL_PJ,
    COST_BASE_USD_PER_GJ,
    LOG_SHARE,
    OUTPUT_DIR,
    RECOVERY,
    RUN_SPECS,
    TILE_FACTOR_ODT_PER_M3,
    TILE_SUPPLY_DIR,
    compute_supply_basis,
    harmonize_evidence_is_complete,
    national_cap_series,
    reconcile_pwr_bio_dispatch,
    sum_pwr_bio,
    write_parameter_files,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
ARTIFACT_DIR = REPO_ROOT / OUTPUT_DIR
ZONAL_DIR = REPO_ROOT / "data/derived/national/clews"


def _series(path: Path, technology: str) -> dict[int, float]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    return {
        int(row["YEAR"]): float(row["VALUE"])
        for row in rows
        if row["TECHNOLOGY"] == technology
    }


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class SupplyMathTest(unittest.TestCase):
    """Guard the 150M log -> total m3 -> ODT -> PJ conversion."""

    def test_conversion_matches_expected_values(self) -> None:
        basis = compute_supply_basis()
        self.assertAlmostEqual(basis.total_volume_m3_per_year, 196_665_353.682, places=3)
        self.assertAlmostEqual(basis.gross_odt_per_year, 17_369_044.3976, places=4)
        self.assertAlmostEqual(basis.pj_by_recovery["base"], 192.796392813, places=9)
        self.assertAlmostEqual(basis.pj_by_recovery["low"], 112.464562474, places=9)
        self.assertAlmostEqual(basis.pj_by_recovery["high"], 240.995491016, places=9)

    def test_conversion_reproduces_fixed_150m_total_row(self) -> None:
        # The fixed harvest table states its rows on a total-volume basis.  The
        # 150M *log* basis therefore corresponds to 150/log_share total volume,
        # and the resulting 150M-total row is the fixed 147.049078 PJ value.
        total_volume = 150_000_000.0
        pj = total_volume * TILE_FACTOR_ODT_PER_M3 * RECOVERY["base"] * 18.5 / 1e6
        self.assertAlmostEqual(pj, 147.049077941, places=9)

    def test_fixed_output_factor_and_log_share_are_unchanged(self) -> None:
        summary = _read_json(
            REPO_ROOT / TILE_SUPPLY_DIR / "rsyc_tile_supply_summary.json"
        )
        factors = {
            row["scenario"]: float(row["residue_factor_odt_per_m3"])
            for row in summary["harvest_scenarios"]
        }
        self.assertAlmostEqual(factors["tile_mix"], TILE_FACTOR_ODT_PER_M3, places=15)
        factor_total_over_log = summary["comparison"]["factor_total_over_log"]
        self.assertIsInstance(factor_total_over_log, (int, float))
        self.assertAlmostEqual(factor_total_over_log, LOG_SHARE, places=9)

    def test_invalid_inputs_fail_loudly(self) -> None:
        with self.assertRaises(ValueError):
            compute_supply_basis(log_volume_m3_per_year=0.0)
        with self.assertRaises(ValueError):
            compute_supply_basis(log_share=1.0)
        with self.assertRaises(ValueError):
            compute_supply_basis(tile_factor_odt_per_m3=0.0)


class CapSplitTest(unittest.TestCase):
    """Guard the rest-of-Canada cap split against double counting BC."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.supply = _read_json(ARTIFACT_DIR / "supply_basis.json")
        cls.zonal = {
            technology: _series(
                ZONAL_DIR / f"{technology}_activity_limits_base.csv", technology
            )
            for technology in ("SUPBCWB1", "SUPBCWB2")
        }

    def _national_cap(self, case: str) -> dict[int, float]:
        suffix = "" if case == "base" else f"_{case}"
        return _series(
            ARTIFACT_DIR / f"SUPBCW_activity_limits_national_150m{suffix}.csv",
            "SUPBCW",
        )

    def test_three_caps_sum_to_national_total_every_year(self) -> None:
        for case in RECOVERY:
            national = self.supply["national_total_pj"][case]
            cap = self._national_cap(case)
            self.assertEqual(sorted(cap), list(range(2021, 2071)))
            for year, value in cap.items():
                total = value + self.zonal["SUPBCWB1"][year] + self.zonal["SUPBCWB2"][year]
                self.assertAlmostEqual(
                    total,
                    national,
                    places=9,
                    msg=f"{case} {year}: {total} != {national}",
                )

    def test_bc_is_not_double_counted(self) -> None:
        self.assertAlmostEqual(
            self.zonal["SUPBCWB1"][2028] + self.zonal["SUPBCWB2"][2028],
            BC_ZONAL_TOTAL_PJ,
            places=9,
        )
        base = self._national_cap("base")
        self.assertAlmostEqual(
            base[2028],
            self.supply["national_total_pj"]["base"] - BC_ZONAL_TOTAL_PJ,
            places=9,
        )
        self.assertAlmostEqual(base[2028], 149.272576781, places=9)
        # The 2021 zonal caps are above their steady state, so the national pool
        # is correspondingly smaller in the phase-down years.
        self.assertLess(base[2021], base[2028])

    def test_sensitivity_caps_and_costs_are_ordered(self) -> None:
        low = self._national_cap("low")
        high = self._national_cap("high")
        self.assertLess(low[2028], high[2028])
        costs = self.supply["costs_usd_per_gj"]
        self.assertAlmostEqual(costs["base"], COST_BASE_USD_PER_GJ, places=9)
        self.assertAlmostEqual(costs["low"], COST_BASE_USD_PER_GJ * 1.30, places=9)
        self.assertAlmostEqual(costs["high"], COST_BASE_USD_PER_GJ * 0.80, places=9)

    def test_national_cap_rejects_negative_balance(self) -> None:
        zonal = {technology: {year: 100.0 for year in range(2021, 2071)}
                 for technology in ("SUPBCWB1", "SUPBCWB2")}
        with self.assertRaises(ValueError):
            national_cap_series(150.0, zonal)


class ArtifactTest(unittest.TestCase):
    """Check the committed run summaries, dispatch table and manifest."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.summaries = _read_json(ARTIFACT_DIR / "run_summaries.json")
        with (ARTIFACT_DIR / "dispatch_by_zone.csv").open(
            newline="", encoding="utf-8"
        ) as handle:
            cls.dispatch = list(csv.DictReader(handle))

    def test_all_runs_are_optimal(self) -> None:
        for spec in RUN_SPECS:
            run = self.summaries["runs"][spec.name]
            self.assertEqual(run["solver_status"], "optimal")
        self.assertAlmostEqual(
            self.summaries["runs"]["baseline"]["objective_musd"], 1_331_289.52, places=2
        )
        self.assertAlmostEqual(
            self.summaries["runs"]["stress"]["objective_musd"], 1_370_983.13, places=2
        )

    def test_stress_binds_all_three_caps(self) -> None:
        stress = self.summaries["runs"]["stress"]["cap_binding"]
        self.assertTrue(stress["SUPBCW"]["binding"])
        self.assertEqual(stress["SUPBCW"]["binding_years"], "2028-2070")
        self.assertTrue(stress["SUPBCWB1"]["binding"])
        self.assertEqual(stress["SUPBCWB1"]["binding_years"], "2026-2070")
        self.assertTrue(stress["SUPBCWB2"]["binding"])
        self.assertEqual(stress["SUPBCWB2"]["binding_years"], "2032-2070")

    def test_baseline_has_no_biomass_dispatch(self) -> None:
        baseline = self.summaries["runs"]["baseline"]
        for key in ("supbcw_pj", "supbcwb1_pj", "supbcwb2_pj"):
            self.assertEqual(baseline[key]["total"], 0.0)
        for key in ("SUPBCW", "SUPBCWB1", "SUPBCWB2"):
            self.assertFalse(baseline["cap_binding"][key]["binding"])

    def test_dispatch_table_covers_all_runs_and_zones(self) -> None:
        self.assertEqual(len(self.dispatch), 3 * 29 * 50)
        indexed = {
            (row["run"], row["technology"], int(row["year"])): float(row["value_pj"])
            for row in self.dispatch
        }
        self.assertAlmostEqual(
            indexed[("stress", "SUPBCW", 2050)], 149.273, places=3
        )
        self.assertAlmostEqual(
            indexed[("stress", "SUPBCWB2", 2050)], 39.9183, places=4
        )
        self.assertEqual(indexed[("baseline", "PWRBCWA1", 2050)], 0.0)

    def test_manifest_hashes_match_files(self) -> None:
        manifest = _read_json(ARTIFACT_DIR / "manifest.json")
        self.assertFalse(manifest["run_evidence"]["public_repo_modified"])
        for entry in manifest["outputs"]:
            path = REPO_ROOT / entry["path"]
            self.assertTrue(path.exists(), entry["path"])
            self.assertEqual(
                hashlib.sha256(path.read_bytes()).hexdigest(), entry["sha256"]
            )
            self.assertEqual(path.stat().st_size, entry["bytes"])

    def test_harmonize_evidence_supports_report_claim(self) -> None:
        evidence = self.summaries["harmonize"]
        self.assertTrue(evidence["all_passed"])
        self.assertTrue(harmonize_evidence_is_complete(evidence))
        self.assertEqual(len(evidence["checks"]), 4)
        self.assertEqual(
            {check["parameter_set"] for check in evidence["checks"]},
            {"base", "baseline", "stress", "baseline_bio"},
        )
        for check in evidence["checks"]:
            self.assertEqual(check["exit_code"], 0, check["parameter_set"])
            self.assertRegex(check["parameter_set_sha256"], r"^[0-9a-f]{64}$")

        text = (ARTIFACT_DIR / "NATIONAL_150M_OSECAN_REPORT.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("`osecan harmonize` evidence recorded", text)
        self.assertIn("HARMONIZED", text)
        for check in evidence["checks"]:
            self.assertIn(check["parameter_set"], text)
            self.assertIn(str(check["parameter_set_sha256"])[:12], text)

    def test_harmonize_report_does_not_claim_success_without_evidence(self) -> None:
        self.assertFalse(harmonize_evidence_is_complete(None))
        self.assertFalse(
            harmonize_evidence_is_complete(
                {"all_passed": True, "checks": [{"parameter_set": "base", "exit_code": 0}]}
            )
        )

    def test_pwr_bio_total_reconciles_to_dispatch_table(self) -> None:
        dispatch_totals = {
            spec.name: sum(
                float(row["value_pj"])
                for row in self.dispatch
                if row["run"] == spec.name
                and row["technology"].startswith("PWRBIO")
            )
            for spec in RUN_SPECS
        }
        for spec in RUN_SPECS:
            self.assertAlmostEqual(
                dispatch_totals[spec.name],
                float(self.summaries["runs"][spec.name]["pwr_bio_total_pj"]),
                places=6,
            )

    def test_report_contains_required_sections(self) -> None:
        text = (ARTIFACT_DIR / "NATIONAL_150M_OSECAN_REPORT.md").read_text(
            encoding="utf-8"
        )
        for heading in (
            "## Supply math (150M log -> total m3 -> ODT -> PJ; BC/rest split)",
            "## Parameter files/harmonize",
            "## Baseline (objective, dispatch, binding, emissions)",
            "## Stress $200/hydro-capped",
            "## Generic-BIO sensitivity",
            "## Tests/commits/publish/status",
            "## Caveats (RSYC-only, no AAC/THLB, B25, group curves)",
        ):
            self.assertIn(heading, text)
        self.assertIn("192.796", text)
        self.assertIn("149.273", text)
        self.assertIn("HARMONIZED", text)
        self.assertIn("1,331,289.52", text)
        self.assertIn("cap at the 2021 activity, not fixed dispatch", text)
        self.assertIn("62.410055", text)
        self.assertIn("62.479153", text)
        self.assertIn("12.829929", text)
        self.assertIn("not the national baseline", text)
        self.assertNotIn("small `SUPBCW` cost difference", text)


class PwrBioZonalTest(unittest.TestCase):
    """Guard that PWRBIO is summed across zonal keys, not read as PWRBIO."""

    def test_sum_pwr_bio_ignores_aggregate_and_sums_zones(self) -> None:
        production = {
            "PWRBIOA1": {2021: 1.0, 2022: 2.0},
            "PWRBIOB2": {2021: 0.25},
            "PWRBIO": {2021: 99.0},
            "PWRBCWA1": {2021: 9.0},
        }
        self.assertAlmostEqual(sum_pwr_bio(production), 3.25)

    def test_reconcile_rejects_mismatched_zonal_dispatch(self) -> None:
        summaries = {"runs": {"baseline": {"pwr_bio_total_pj": 3.25}}}
        rows = [
            {"run": "baseline", "technology": "PWRBIOA1", "value_pj": 3.0},
            {"run": "baseline", "technology": "PWRBIOB2", "value_pj": 0.25},
        ]
        reconcile_pwr_bio_dispatch(summaries, rows)
        rows[0]["value_pj"] = 2.0
        with self.assertRaises(ValueError):
            reconcile_pwr_bio_dispatch(summaries, rows)


class ParameterWriterTest(unittest.TestCase):
    """Check that the parameter writer is reproducible and idempotent."""

    def test_writer_reproduces_committed_parameter_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "data/derived/national/clews_supply").mkdir(parents=True)
            (root / "data/derived/national/clews").mkdir(parents=True)
            # The writer verifies the fixed tile output before writing.
            source_supply = REPO_ROOT / TILE_SUPPLY_DIR
            target_supply = root / TILE_SUPPLY_DIR
            target_supply.mkdir(parents=True)
            for name in ("rsyc_tile_supply_summary.json", "RSYC_TILE_SUPPLY_REPORT.md"):
                (target_supply / name).write_bytes((source_supply / name).read_bytes())
            for technology in ("SUPBCWB1", "SUPBCWB2"):
                for kind in ("activity_limits_base", "variable_cost_base"):
                    name = f"{technology}_{kind}.csv"
                    (root / "data/derived/national/clews" / name).write_bytes(
                        (ZONAL_DIR / name).read_bytes()
                    )
            write_parameter_files(root)
            for path in (ARTIFACT_DIR).glob("SUPBCW_*.csv"):
                self.assertEqual(
                    (root / OUTPUT_DIR / path.name).read_text(encoding="utf-8"),
                    path.read_text(encoding="utf-8"),
                )


if __name__ == "__main__":
    unittest.main()
