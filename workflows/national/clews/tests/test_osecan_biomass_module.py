"""Tests for the OSeCAN availability and reduced biomass-module artifacts."""

from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from workflows.national.clews.osecan_biomass_module import (
    BCW_EFFICIENCY,
    CASES,
    DISCOUNT_RATE,
    HORIZON_END,
    HORIZON_START,
    MODEL_COMMIT,
    OUTPUT_DIR,
    PLANTS,
    build_all,
    build_cap_metrics,
    capital_recovery_factor,
    load_case_supply,
    plant_lcoe,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
OUTPUT_DIR_PATH = REPO_ROOT / OUTPUT_DIR


def _write_parameter(path: Path, technology: str, values: dict[int, float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["REGION", "TECHNOLOGY", "YEAR", "VALUE"])
        for year in range(HORIZON_START, HORIZON_END + 1):
            writer.writerow(["CAN", technology, year, values[year]])


class OsecanBiomassModuleTest(unittest.TestCase):
    """Unit and integration tests over the committed BC supply artifacts."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.built = build_all(REPO_ROOT)
        cls.lcoe = {
            (str(row["case"]), str(row["plant"])): row for row in cls.built["lcoe_rows"]
        }
        cls.comparison = {str(row["case"]): row for row in cls.built["comparison_rows"]}

    def test_capital_recovery_factor(self) -> None:
        crf_30 = capital_recovery_factor(DISCOUNT_RATE, 30)
        crf_60 = capital_recovery_factor(DISCOUNT_RATE, 60)
        self.assertAlmostEqual(crf_30, 0.065051, places=6)
        self.assertAlmostEqual(crf_60, 0.052828, places=6)
        self.assertGreater(crf_30, crf_60)
        self.assertAlmostEqual(capital_recovery_factor(0.0, 30), 1.0 / 30.0, places=12)

    def test_crf_rejects_invalid_inputs(self) -> None:
        with self.assertRaises(ValueError):
            capital_recovery_factor(-0.01, 30)
        with self.assertRaises(ValueError):
            capital_recovery_factor(0.05, 0)

    def test_lcoe_components_sum_to_total(self) -> None:
        for row in self.built["lcoe_rows"]:
            components = (
                float(row["capital_fixed_usd_per_mwh"])
                + float(row["fuel_usd_per_mwh"])
                + float(row["carbon_usd_per_mwh"])
                + float(row["var_usd_per_mwh"])
            )
            self.assertAlmostEqual(components, float(row["lcoe_usd_per_mwh"]), places=9)

    def test_bcw_lcoe_by_case(self) -> None:
        self.assertAlmostEqual(
            float(self.lcoe[("low", "PWRBCW")]["lcoe_usd_per_mwh"]),
            92.21475372153239,
            places=6,
        )
        self.assertAlmostEqual(
            float(self.lcoe[("base", "PWRBCW")]["lcoe_usd_per_mwh"]),
            82.34360071665239,
            places=6,
        )
        self.assertAlmostEqual(
            float(self.lcoe[("high", "PWRBCW")]["lcoe_usd_per_mwh"]),
            75.7628320467324,
            places=6,
        )

    def test_bio_and_gas_lcoe(self) -> None:
        self.assertAlmostEqual(
            float(self.lcoe[("generic", "PWRBIO")]["lcoe_usd_per_mwh"]),
            86.282659,
            places=5,
        )
        self.assertAlmostEqual(
            float(self.lcoe[("carbon_2026", "PWRNGS")]["lcoe_usd_per_mwh"]),
            70.369577,
            places=5,
        )
        self.assertAlmostEqual(
            float(self.lcoe[("carbon_2030", "PWRNGS")]["lcoe_usd_per_mwh"]),
            74.041577,
            places=5,
        )

    def test_bcw_fuel_cost_formula(self) -> None:
        base = self.comparison["base"]
        expected = 3.6 * 3.72 * float(base["bcw_cost_usd_per_gj"])
        self.assertAlmostEqual(float(base["bcw_fuel_usd_per_mwh"]), expected, places=9)
        expected_bio = 3.6 * 2.85 * 3.0
        self.assertAlmostEqual(
            float(base["bio_fuel_usd_per_mwh"]), expected_bio, places=9
        )

    def test_break_even_against_bio(self) -> None:
        for case in CASES:
            self.assertAlmostEqual(
                float(self.comparison[case]["breakeven_bcw_vs_bio_usd_per_gj"]),
                2.751112728553375,
                places=9,
            )
        self.assertFalse(self.comparison["low"]["cheaper_than_bio"])
        self.assertTrue(self.comparison["base"]["cheaper_than_bio"])
        self.assertTrue(self.comparison["high"]["cheaper_than_bio"])

    def test_break_even_against_gas(self) -> None:
        self.assertAlmostEqual(
            float(self.comparison["base"]["breakeven_bcw_vs_gas95_usd_per_gj"]),
            1.5628598904296849,
            places=9,
        )
        self.assertAlmostEqual(
            float(self.comparison["base"]["breakeven_bcw_vs_gas115_usd_per_gj"]),
            1.8370534388167814,
            places=9,
        )
        self.assertAlmostEqual(
            float(
                self.comparison["high"]["carbon_price_for_parity_with_gas_usd_per_t"]
            ),
            124.375027,
            places=5,
        )
        self.assertAlmostEqual(
            float(
                self.comparison["base"]["carbon_price_for_parity_with_gas_usd_per_t"]
            ),
            160.217994,
            places=5,
        )

    def test_case_rollup_is_exact(self) -> None:
        cases = self.built["cases"]
        for case in CASES:
            for year in (HORIZON_START, 2028, HORIZON_END):
                self.assertAlmostEqual(
                    cases[case].b1_cap_pj[year] + cases[case].b2_cap_pj[year],
                    cases[case].national_cap_pj[year],
                    delta=1e-6,
                )

    def test_cap_metrics(self) -> None:
        metrics = build_cap_metrics(self.built["cases"])
        self.assertAlmostEqual(
            metrics["placeholder"]["max_electricity_pj"],
            50.0 * BCW_EFFICIENCY,
            places=9,
        )
        self.assertAlmostEqual(
            metrics["base"]["max_electricity_2028_pj"],
            42.958794801 * BCW_EFFICIENCY,
            places=9,
        )
        self.assertGreater(
            metrics["high"]["max_electricity_2028_pj"],
            metrics["base"]["max_electricity_2028_pj"],
        )

    def test_cap_does_not_bind(self) -> None:
        cap_binding = self.built["cap_binding"]
        self.assertFalse(cap_binding["runs"]["baseline_placeholder"]["cap_binding"])
        self.assertFalse(cap_binding["runs"]["bc_base_national_rollup"]["cap_binding"])
        for run in cap_binding["runs"].values():
            self.assertEqual(run["supbcw_pj"]["total"], 0.0)
            self.assertEqual(run["pwrbcw_pj"]["total"], 0.0)
            self.assertEqual(run["pwr_bio_pj"]["total"], 0.0)
        self.assertIn("do not bind", cap_binding["conclusion"])

    def test_policy_sensitivity(self) -> None:
        sensitivity = self.built["sensitivity"]
        runs = sensitivity["runs"]
        carbon = runs["carbon_200"]
        self.assertFalse(carbon["cap_binding"])
        self.assertEqual(carbon["supbcw_pj"]["total"], 0.0)
        self.assertEqual(carbon["pwrbcw_pj"]["total"], 0.0)
        frozen = runs["carbon_200_hydro_frozen"]
        self.assertTrue(frozen["cap_binding"])
        self.assertEqual(frozen["cap_binding_years"], "2028-2070")
        self.assertAlmostEqual(frozen["supbcw_pj"]["2030"], 42.958794801, places=4)
        self.assertAlmostEqual(frozen["supbcw_pj"]["total"], 2104.9812, places=3)
        self.assertAlmostEqual(frozen["pwrbcw_pj"]["2050"], 11.548065, places=6)
        self.assertEqual(frozen["pwr_bio_pj"]["total"], 0.0)
        self.assertEqual(frozen["emissions_mt"]["total"], 43.990577)
        self.assertIn("160 $/t", sensitivity["conditions_for_bcw_dispatch"][0])
        self.assertGreater(frozen["objective_musd"], carbon["objective_musd"])

    def test_zonal_files_are_rejected(self) -> None:
        compatibility = self.built["cap_binding"]["zonal_compatibility"]
        self.assertEqual(compatibility["status"], "rejected")
        self.assertEqual(compatibility["technologies"], ["SUPBCWB1", "SUPBCWB2"])
        self.assertEqual(compatibility["harmonize_exit"], 1)

    def test_availability_records_no_pypi(self) -> None:
        availability = self.built["availability"]
        self.assertEqual(availability["pypi"]["json_http_status"], 404)
        self.assertFalse(availability["pypi"]["available"])
        self.assertEqual(
            availability["github"]["repositories"][0]["head"], MODEL_COMMIT
        )
        self.assertEqual(availability["site"]["pages"]["site/api.html"]["status"], 200)

    def test_artifacts_exist_and_manifest_hashes_match(self) -> None:
        expected = {
            "availability.json",
            "lcoe_by_plant.csv",
            "bcw_vs_bio.csv",
            "cap_binding_analysis.json",
            "OSECAN_RUN_REPORT.md",
            "model_runs.json",
            "manifest.json",
        }
        found = {path.name for path in OUTPUT_DIR_PATH.iterdir() if path.is_file()}
        self.assertEqual(found, expected)
        manifest = json.loads((OUTPUT_DIR_PATH / "manifest.json").read_text())
        self.assertEqual(
            manifest["status"], "fresh_side_provisional_not_c2070_official"
        )
        self.assertGreaterEqual(len(manifest["inputs"]), 14)
        self.assertGreaterEqual(len(manifest["outputs"]), 8)
        for name in ("sensitivity_runs.json", "sensitivity_summary.csv"):
            path = OUTPUT_DIR_PATH / "sensitivity" / name
            self.assertTrue(path.exists(), path)
        for row in manifest["outputs"]:
            path = REPO_ROOT / row["path"]
            self.assertTrue(path.exists(), row["path"])
            self.assertEqual(len(row["sha256"]), 64)
            self.assertEqual(path.stat().st_size, row["bytes"])

    def test_missing_case_file_fails_closed(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temporary,
            self.assertRaises(FileNotFoundError),
        ):
            load_case_supply(Path(temporary), "base")

    def test_rollup_mismatch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            zeros = {year: 0.0 for year in range(HORIZON_START, HORIZON_END + 1)}
            national = {year: 10.0 for year in range(HORIZON_START, HORIZON_END + 1)}
            _write_parameter(
                root / "comparison/SUPBCW_activity_limits_base.csv",
                "SUPBCW",
                national,
            )
            _write_parameter(
                root / "comparison/SUPBCW_variable_cost_base.csv",
                "SUPBCW",
                {year: 2.5 for year in national},
            )
            _write_parameter(
                root / "SUPBCWB1_activity_limits_base.csv", "SUPBCWB1", zeros
            )
            _write_parameter(
                root / "SUPBCWB2_activity_limits_base.csv", "SUPBCWB2", zeros
            )
            _write_parameter(
                root / "SUPBCWB1_variable_cost_base.csv",
                "SUPBCWB1",
                {year: 2.5 for year in national},
            )
            _write_parameter(
                root / "SUPBCWB2_variable_cost_base.csv",
                "SUPBCWB2",
                {year: 2.5 for year in national},
            )
            with self.assertRaises(ValueError):
                load_case_supply(root, "base")

    def test_plant_lcoe_rejects_bad_capacity_factor(self) -> None:
        plant = PLANTS["PWRBCW"]
        bad = type(plant)(
            name=plant.name,
            fuel=plant.fuel,
            input_activity_ratio=plant.input_activity_ratio,
            capex_usd_per_kw=plant.capex_usd_per_kw,
            fixed_usd_per_kw_yr=plant.fixed_usd_per_kw_yr,
            var_usd_per_gj=plant.var_usd_per_gj,
            life_years=plant.life_years,
            capacity_factor=0.0,
        )
        with self.assertRaises(ValueError):
            plant_lcoe(bad, 2.0)


if __name__ == "__main__":
    unittest.main()
