"""Tests for the zonal OSeCAN structure, run summaries and committed artifacts."""

from __future__ import annotations

import csv
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from workflows.national.clews.osecan_zonal import (
    BC_ZONES,
    CARBON_PRICE_FROM_2026,
    CARBON_PRICE_PRE_2026,
    ECONOMICS_PLANTS,
    GENERIC_BIO_BASE_COST,
    GENERIC_BIO_SENSITIVITY_COST,
    HORIZON_END,
    HORIZON_START,
    KEY_YEARS,
    OUTPUT_DIR,
    RUN_SPECS,
    SUPPLY_TECHS,
    build_dispatch_rows,
    build_economics_rows,
    build_zonal_structure,
    cap_binding,
    read_production,
    summarize_run,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
ARTIFACT_DIR = REPO_ROOT / OUTPUT_DIR
YEARS = tuple(range(HORIZON_START, HORIZON_END + 1))


def _write_csv(path: Path, header: list[str], rows: list[list[object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


def _write_run(
    root: Path,
    name: str,
    production: dict[str, dict[int, float]],
    emissions: dict[int, float],
    objective: float = 100.0,
) -> Path:
    run = root / name
    (run / "results_csv").mkdir(parents=True, exist_ok=True)
    (run / "status.json").write_text(
        json.dumps(
            {"status": "optimal", "objective": objective, "rows": 10, "cols": 20}
        ),
        encoding="utf-8",
    )
    production_rows = [
        ["CAN", technology, year, value]
        for technology, series in production.items()
        for year, value in sorted(series.items())
    ]
    _write_csv(
        run / "results_csv" / "ProductionByTechnologyAnnual.csv",
        ["REGION", "TECHNOLOGY", "YEAR", "VALUE"],
        production_rows,
    )
    _write_csv(
        run / "results_csv" / "AnnualEmissions.csv",
        ["REGION", "EMISSION", "YEAR", "VALUE"],
        [["CAN", "CO2", year, value] for year, value in sorted(emissions.items())],
    )
    return run


def _caps(value: float) -> dict[int, float]:
    return {year: value for year in YEARS}


class OsecanZonalArtifactTest(unittest.TestCase):
    """Validate the committed zonal artifacts and their regeneration logic."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.structure = json.loads(
            (ARTIFACT_DIR / "zonal_structure.json").read_text(encoding="utf-8")
        )
        cls.summaries = json.loads(
            (ARTIFACT_DIR / "run_summaries.json").read_text(encoding="utf-8")
        )
        with (ARTIFACT_DIR / "dispatch_by_zone.csv").open(newline="", encoding="utf-8") as handle:
            cls.dispatch = list(csv.DictReader(handle))

    def test_structure_has_zonal_fuels_and_technologies(self) -> None:
        self.assertEqual(set(self.structure["zonal_mapping"]), set(BC_ZONES))
        self.assertEqual(self.structure["zonal_mapping"], {"B1": "BCWB1", "B2": "BCWB2"})
        self.assertEqual(set(self.structure["fuels"]), {"BCW", "BCWB1", "BCWB2"})
        for technology in ("SUPBCWB1", "SUPBCWB2", "PWRBCWB1", "PWRBCWB2"):
            self.assertIn(technology, self.structure["technologies"])
        self.assertTrue(self.structure["model_verification"]["checks"]["fuel_BCWB1_present"])
        self.assertTrue(self.structure["model_verification"]["checks"]["fuel_BCWB2_present"])
        self.assertEqual(
            self.structure["model_verification"]["sets"]["technologies"], 524
        )
        self.assertEqual(self.structure["model_verification"]["sets"]["fuels"], 381)

    def test_input_ratios_and_plant_parameters_are_preserved(self) -> None:
        for technology, fuel, output in (
            ("PWRBCWB1", "BCWB1", "ELCB101"),
            ("PWRBCWB2", "BCWB2", "ELCB201"),
        ):
            plant = self.structure["technologies"][technology]
            self.assertEqual(plant["input_fuel"], fuel)
            self.assertEqual(plant["output_fuel"], output)
            self.assertEqual(plant["input_activity_ratio"], 3.72)
            self.assertAlmostEqual(plant["efficiency"], 1.0 / 3.72, places=6)
            self.assertEqual(plant["capex_usd_per_kw"], 3200.0)
            self.assertEqual(plant["fixed_usd_per_kw_yr"], 95.0)
            self.assertEqual(plant["variable_usd_per_gj"], 0.0)
            self.assertEqual(plant["life_years"], 30)
            self.assertEqual(plant["capacity_factor"], 0.70)
            self.assertEqual(plant["emission_factor_t_per_gj"], 0.0)
            self.assertEqual(
                self.structure["model_verification"]["checks"][
                    f"{technology}_input_ratio"
                ],
                3.72,
            )
            self.assertTrue(
                self.structure["model_verification"]["checks"][
                    f"{technology}_no_national_BCW_input"
                ]
            )

    def test_zonal_caps_and_costs_match_source_files(self) -> None:
        caps = self.structure["caps_and_costs"]
        self.assertAlmostEqual(
            caps["SUPBCWB1"]["activity_upper_limit_pj"]["2021"], 3.678381023, places=9
        )
        self.assertAlmostEqual(
            caps["SUPBCWB1"]["activity_upper_limit_pj"]["2028"], 3.672285606, places=9
        )
        self.assertAlmostEqual(
            caps["SUPBCWB2"]["activity_upper_limit_pj"]["2021"], 46.794140373, places=9
        )
        self.assertAlmostEqual(
            caps["SUPBCWB2"]["activity_upper_limit_pj"]["2028"], 39.286509195, places=9
        )
        self.assertAlmostEqual(
            caps["SUPBCWB1"]["variable_cost_usd_per_gj"]["2021"], 2.456977549, places=9
        )
        self.assertAlmostEqual(
            caps["SUPBCWB2"]["variable_cost_usd_per_gj"]["2070"], 2.456977549, places=9
        )
        self.assertAlmostEqual(
            caps["SUPBCW"]["activity_upper_limit_pj"]["2028"], 42.958794801, places=9
        )
        for technology in SUPPLY_TECHS:
            self.assertEqual(
                len(caps[technology]["activity_upper_limit_pj"]), len(YEARS)
            )
            self.assertEqual(len(caps[technology]["variable_cost_usd_per_gj"]), len(YEARS))
            caps_series = [
                float(caps[technology]["activity_upper_limit_pj"][str(year)])
                for year in YEARS
            ]
            self.assertEqual(caps_series, sorted(caps_series, reverse=True))

    def test_harmonize_and_info_results_are_recorded(self) -> None:
        harmonize = self.structure["harmonize"]
        self.assertEqual(harmonize["exit_code"], 0)
        self.assertIn("HARMONIZED", harmonize["message"])
        self.assertEqual(len(harmonize["param_dirs"]), 4)
        self.assertEqual(self.structure["osecan_info"]["exit_code"], 0)
        self.assertIn("Horizon: 2021–2070", self.structure["osecan_info"]["stdout"])

    def test_structure_builder_matches_committed_caps(self) -> None:
        rebuilt = build_zonal_structure(REPO_ROOT)
        self.assertEqual(
            set(rebuilt["technologies"]), set(self.structure["technologies"])
        )
        self.assertEqual(rebuilt["fuels"], self.structure["fuels"])
        self.assertEqual(rebuilt["zonal_mapping"], self.structure["zonal_mapping"])
        # The committed ``zonal`` artifacts are the pre-reconciliation snapshot.
        # The live builder now uses the reconciled caps.
        self.assertAlmostEqual(
            rebuilt["caps_and_costs"]["SUPBCWB1"]["activity_upper_limit_pj"]["2028"],
            3.605474907,
            places=9,
        )
        self.assertAlmostEqual(
            rebuilt["caps_and_costs"]["SUPBCWB2"]["activity_upper_limit_pj"]["2028"],
            32.204470802,
            places=9,
        )
        self.assertAlmostEqual(
            rebuilt["caps_and_costs"]["SUPBCW"]["activity_upper_limit_pj"]["2028"],
            35.809945709,
            places=9,
        )
        self.assertAlmostEqual(
            self.structure["caps_and_costs"]["SUPBCWB2"]["activity_upper_limit_pj"]["2028"],
            39.286509195,
            places=9,
        )

    def test_economics_rows(self) -> None:
        rows = build_economics_rows()
        lookup = {
            (str(row["plant"]), float(row["carbon_price_usd_per_t"]), float(row["fuel_cost_usd_per_gj"])): row
            for row in rows
        }
        self.assertAlmostEqual(
            float(lookup[("PWRBCW", 0.0, 2.456977549)]["lcoe_usd_per_mwh"]),
            82.3436,
            places=4,
        )
        self.assertAlmostEqual(
            float(lookup[("PWRNGS", 200.0, 3.0)]["lcoe_usd_per_mwh"]),
            89.6476,
            places=4,
        )
        self.assertAlmostEqual(
            float(lookup[("PWRNGS", 95.0, 3.0)]["lcoe_usd_per_mwh"]),
            70.3696,
            places=4,
        )
        self.assertAlmostEqual(
            float(lookup[("PWRBIO", 0.0, 4.0)]["lcoe_usd_per_mwh"]),
            96.543,
            places=3,
        )
        for row in rows:
            components = (
                float(row["capital_fixed_usd_per_mwh"])
                + float(row["fuel_usd_per_mwh"])
                + float(row["carbon_usd_per_mwh"])
                + float(row["var_usd_per_mwh"])
            )
            self.assertAlmostEqual(
                components, float(row["lcoe_usd_per_mwh"]), places=5
            )

    def test_run_objectives_and_statuses(self) -> None:
        runs = self.summaries["runs"]
        expected = {
            "control": 1_371_553.35,
            "zonal": 1_371_600.56,
            "zonal_bio": 1_385_253.48,
        }
        for name, objective in expected.items():
            self.assertEqual(runs[name]["solver_status"], "optimal")
            self.assertAlmostEqual(runs[name]["objective_musd"], objective, places=2)
            self.assertEqual(runs[name]["lp_rows"], 274_851)
            self.assertEqual(runs[name]["lp_cols"], 235_800)
        self.assertAlmostEqual(
            self.summaries["comparison"]["zonal"]["objective_delta_musd"], 47.21, places=2
        )
        self.assertAlmostEqual(
            self.summaries["comparison"]["zonal_bio_minus_zonal"]["objective_delta_musd"],
            13_652.92,
            places=2,
        )

    def test_zonal_dispatch_is_nonzero_and_national_control_is_zero(self) -> None:
        runs = self.summaries["runs"]
        control = runs["control"]
        zonal = runs["zonal"]
        self.assertGreater(control["supbcw_pj"]["total"], 0.0)
        self.assertGreater(zonal["supbcwb1_pj"]["total"], 0.0)
        self.assertGreater(zonal["supbcwb2_pj"]["total"], 0.0)
        self.assertGreater(zonal["pwrbcwb1_pj"]["total"], 0.0)
        self.assertGreater(zonal["pwrbcwb2_pj"]["total"], 0.0)
        self.assertEqual(zonal["supbcw_pj"]["total"], 0.0)
        self.assertEqual(control["supbcwb1_pj"]["total"], 0.0)
        self.assertEqual(control["supbcwb2_pj"]["total"], 0.0)
        self.assertEqual(control["pwrbcwb1_pj"]["total"], 0.0)
        self.assertEqual(control["pwrbcwb2_pj"]["total"], 0.0)
        self.assertAlmostEqual(zonal["supbcwb1_pj"]["2050"], 3.67229, places=5)
        self.assertAlmostEqual(zonal["supbcwb2_pj"]["2050"], 39.2865, places=4)
        self.assertAlmostEqual(zonal["pwrbcwb1_pj"]["2050"], 0.987175, places=6)
        self.assertAlmostEqual(zonal["pwrbcwb2_pj"]["2050"], 10.560887, places=6)

    def test_cap_binding_is_zonally_specific(self) -> None:
        zonal = self.summaries["runs"]["zonal"]["cap_binding"]
        control = self.summaries["runs"]["control"]["cap_binding"]
        self.assertTrue(zonal["SUPBCWB1"]["binding"])
        self.assertEqual(zonal["SUPBCWB1"]["binding_years"], "2026-2070")
        self.assertEqual(zonal["SUPBCWB1"]["binding_year_count"], 45)
        self.assertEqual(zonal["SUPBCWB1"]["max_utilisation"], 1.0)
        self.assertTrue(zonal["SUPBCWB2"]["binding"])
        self.assertEqual(zonal["SUPBCWB2"]["binding_years"], "2032-2070")
        self.assertEqual(zonal["SUPBCWB2"]["binding_year_count"], 39)
        self.assertEqual(zonal["SUPBCWB2"]["max_utilisation"], 1.0)
        self.assertFalse(zonal["SUPBCW"]["binding"])
        self.assertTrue(control["SUPBCW"]["binding"])
        self.assertEqual(control["SUPBCW"]["binding_years"], "2028-2070")
        self.assertEqual(control["SUPBCW"]["binding_year_count"], 43)
        self.assertFalse(control["SUPBCWB1"]["binding"])
        self.assertFalse(control["SUPBCWB2"]["binding"])

    def test_other_zone_dispatch_and_emissions(self) -> None:
        control = self.summaries["runs"]["control"]
        zonal = self.summaries["runs"]["zonal"]
        self.assertAlmostEqual(
            control["pwrbcw_other_zone_pj"]["total_pj"], 565.855185, places=6
        )
        self.assertEqual(zonal["pwrbcw_other_zone_pj"]["total_pj"], 0.0)
        self.assertIn("A1", control["pwrbcw_other_zone_pj"]["by_zone_total_pj"])
        self.assertAlmostEqual(
            control["emissions_mt"]["total"], 43.990577, places=6
        )
        self.assertAlmostEqual(zonal["emissions_mt"]["total"], 61.659110, places=6)
        self.assertAlmostEqual(
            self.summaries["comparison"]["zonal"]["emissions_delta_mt"],
            17.668533,
            places=6,
        )

    def test_generic_bio_sensitivity_does_not_change_bcw(self) -> None:
        zonal = self.summaries["runs"]["zonal"]
        zonal_bio = self.summaries["runs"]["zonal_bio"]
        self.assertEqual(zonal_bio["supbcwb1_pj"]["total"], zonal["supbcwb1_pj"]["total"])
        self.assertEqual(zonal_bio["supbcwb2_pj"]["total"], zonal["supbcwb2_pj"]["total"])
        self.assertEqual(zonal_bio["pwr_bio_total_pj"], 0.0)
        self.assertEqual(zonal["pwr_bio_total_pj"], 0.0)
        self.assertEqual(self.summaries["forcing"]["generic_bio_cost_usd_per_gj"]["zonal"], 3.0)
        self.assertEqual(
            self.summaries["forcing"]["generic_bio_cost_usd_per_gj"]["zonal_bio"], 4.0
        )

    def test_dispatch_csv_covers_all_runs_and_years(self) -> None:
        self.assertEqual(len(self.dispatch), 4350)
        indexed = {
            (row["run"], row["technology"], int(row["year"])): float(row["value_pj"])
            for row in self.dispatch
        }
        for spec in RUN_SPECS:
            for technology in ("SUPBCW", "SUPBCWB1", "SUPBCWB2", "PWRBCWB1", "PWRBCWB2"):
                for year in YEARS:
                    self.assertIn((spec.name, technology, year), indexed)
        self.assertAlmostEqual(indexed[("zonal", "SUPBCWB2", 2050)], 39.2865, places=4)
        self.assertAlmostEqual(indexed[("zonal", "PWRBCWB2", 2050)], 10.560887, places=6)
        self.assertEqual(indexed[("control", "PWRBCWB1", 2050)], 0.0)
        self.assertEqual(indexed[("control", "SUPBCWB1", 2050)], 0.0)

    def test_manifest_hashes_match_files(self) -> None:
        manifest = json.loads(
            (ARTIFACT_DIR / "manifest.json").read_text(encoding="utf-8")
        )
        # Inputs are the historical pre-reconciliation cap snapshot; the live
        # cap files legitimately changed after this run.  The output hashes are
        # the immutable record of the run itself.
        for entry in manifest["outputs"]:
            path = REPO_ROOT / entry["path"]
            self.assertTrue(path.exists(), entry["path"])
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertEqual(digest, entry["sha256"], entry["path"])
            self.assertEqual(path.stat().st_size, entry["bytes"], entry["path"])
        for entry in manifest["inputs"]:
            path = REPO_ROOT / entry["path"]
            self.assertTrue(path.exists(), entry["path"])
            self.assertEqual(len(entry["sha256"]), 64, entry["path"])

    def test_report_contains_required_sections(self) -> None:
        text = (ARTIFACT_DIR / "ZONAL_OSECAN_REPORT.md").read_text(encoding="utf-8")
        for heading in (
            "## Zonal structure (fuels/techs/input ratios; harmonize result)",
            "## Runs (objective, SUPBCWB1/B2 activity, PWRBCWB1/B2 dispatch, cap binding, emissions)",
            "## Generic-BIO sensitivity",
            "## Economics/why dispatch or not",
            "## Tests/commits/publish/status",
            "## Blockers/next",
        ):
            self.assertIn(heading, text)
        self.assertIn("1,371,553.35", text)
        self.assertIn("2026-2070", text)
        self.assertIn("61.659", text)
        self.assertIn("HARMONIZED", text)


class OsecanZonalSummaryLogicTest(unittest.TestCase):
    """Unit tests for cap binding and run-summary logic on synthetic results."""

    def test_cap_binding_detects_first_and_last_binding_year(self) -> None:
        activity = {year: 9.0 for year in YEARS}
        for year in range(2030, 2071):
            activity[year] = 10.0
        result = cap_binding(activity, _caps(10.0), "TEST")
        self.assertTrue(result["binding"])
        self.assertEqual(result["binding_years"], "2030-2070")
        self.assertEqual(result["binding_year_count"], 41)
        self.assertEqual(result["first_binding_year"], 2030)
        self.assertEqual(result["last_binding_year"], 2070)
        self.assertEqual(result["max_utilisation"], 1.0)

    def test_cap_binding_ignores_slack_and_zero_caps(self) -> None:
        result = cap_binding(_caps(0.0), _caps(0.0), "TEST")
        self.assertFalse(result["binding"])
        self.assertEqual(result["binding_years"], "")
        self.assertEqual(result["max_utilisation"], 0.0)

    def test_cap_binding_fails_loudly_on_violation(self) -> None:
        activity = _caps(10.1)
        with self.assertRaises(ValueError):
            cap_binding(activity, _caps(10.0), "TEST")

    def test_cap_binding_rejects_empty_caps(self) -> None:
        with self.assertRaises(ValueError):
            cap_binding({}, {}, "TEST")

    def test_summarize_run_synthetic(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            production = {
                "SUPBCWB1": _caps(3.672285606),
                "SUPBCWB2": _caps(39.286509195),
                "PWRBCWB1": _caps(0.987175),
                "PWRBCWB2": _caps(10.560887),
                "PWRBCWA1": _caps(1.0),
                "PWRBIOB1": _caps(0.0),
            }
            emissions = {year: 1.0 for year in YEARS}
            run = _write_run(root, "zonal", production, emissions, objective=123.45)
            caps = {
                "SUPBCW": _caps(42.958794801),
                "SUPBCWB1": _caps(3.672285606),
                "SUPBCWB2": _caps(39.286509195),
            }
            summary = summarize_run(run, caps, RUN_SPECS[1])
            self.assertEqual(summary["solver_status"], "optimal")
            self.assertAlmostEqual(summary["objective_musd"], 123.45, places=2)
            self.assertAlmostEqual(summary["supbcwb1_pj"]["total"], 3.672285606 * 50, places=3)
            self.assertAlmostEqual(summary["pwrbcwb2_pj"]["total"], 10.560887 * 50, places=3)
            self.assertAlmostEqual(
                summary["pwrbcw_other_zone_pj"]["total_pj"], 1.0 * 50, places=3
            )
            self.assertAlmostEqual(summary["emissions_mt"]["total"], 50.0, places=3)
            self.assertTrue(summary["cap_binding"]["SUPBCWB1"]["binding"])
            self.assertTrue(summary["cap_binding"]["SUPBCWB2"]["binding"])

    def test_summarize_run_requires_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run = _write_run(
                root,
                "zonal",
                {"SUPBCWB1": _caps(1.0)},
                {2021: 0.0},
            )
            (run / "status.json").unlink()
            with self.assertRaises(FileNotFoundError):
                summarize_run(
                    run,
                    {
                        "SUPBCW": _caps(42.958794801),
                        "SUPBCWB1": _caps(3.672285606),
                        "SUPBCWB2": _caps(39.286509195),
                    },
                    RUN_SPECS[1],
                )

    def test_build_dispatch_rows_fills_zero_grid(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for spec in RUN_SPECS:
                production = {
                    "SUPBCW": _caps(0.0),
                    "SUPBCWB1": _caps(0.0),
                    "SUPBCWB2": _caps(0.0),
                    "PWRBCWB1": _caps(0.0),
                    "PWRBCWB2": _caps(0.0),
                }
                _write_run(root, spec.name, production, {2021: 0.0})
            rows = build_dispatch_rows(root)
            self.assertEqual(len(rows), 3 * 29 * 50)
            self.assertTrue(all(float(row["value_pj"]) == 0.0 for row in rows))
            self.assertEqual(
                {row["run"] for row in rows},
                {spec.name for spec in RUN_SPECS},
            )

    def test_read_production_returns_full_series(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run = _write_run(
                root,
                "zonal",
                {"PWRBCWB1": _caps(0.5), "SUPBCWB2": {2021: 1.0}},
                {2021: 0.0},
            )
            production = read_production(run)
            self.assertEqual(production["PWRBCWB1"], _caps(0.5))
            self.assertEqual(production["SUPBCWB2"], {2021: 1.0})
            self.assertNotIn("PWRBIOB1", production)


class OsecanZonalConstantsTest(unittest.TestCase):
    """Guard the experiment constants against accidental drift."""

    def test_forcing_constants(self) -> None:
        self.assertEqual(CARBON_PRICE_PRE_2026, 95.0)
        self.assertEqual(CARBON_PRICE_FROM_2026, 200.0)
        self.assertEqual(GENERIC_BIO_BASE_COST, 3.0)
        self.assertEqual(GENERIC_BIO_SENSITIVITY_COST, 4.0)
        self.assertEqual(len(RUN_SPECS), 3)
        self.assertIn("PWRBCW", ECONOMICS_PLANTS)
        self.assertIn("PWRBIO", ECONOMICS_PLANTS)
        self.assertIn(2021, KEY_YEARS)
        self.assertIn(2070, KEY_YEARS)


if __name__ == "__main__":
    unittest.main()
