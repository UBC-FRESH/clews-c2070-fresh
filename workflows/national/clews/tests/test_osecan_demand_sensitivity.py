"""Tests for the demand-side sensitivity of the tiered BCW supply curve."""

from __future__ import annotations

import csv
import hashlib
import json
import unittest
from pathlib import Path

from workflows.national.clews.osecan_demand_sensitivity import (
    CARBON_CASES,
    _case_spec,
    _load_tier_caps,
    _read_series,
    validate_case_params,
)
from workflows.national.clews.osecan_national_supply_curve import (
    BC_ZONAL_TOTAL_PJ,
    NATIONAL_TOTAL_PJ,
    REST_OF_CANADA_PJ,
    TIERS,
    validate_tier_structure,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
ARTIFACT_DIR = (
    REPO_ROOT
    / "data/derived/national/clews/osecan/national_supply_curve/demand_sensitivity"
)
MODEL_ROOT = Path("/tmp/osecan_demand_sens")


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class TierParameterTest(unittest.TestCase):
    """Guard the tier caps used by every demand-side case."""

    def test_tier_structure_is_valid(self) -> None:
        validate_tier_structure()

    def test_tier_caps_sum_to_rest_of_canada(self) -> None:
        caps = _load_tier_caps()
        zonal = {
            technology: _read_series(
                REPO_ROOT
                / "data/derived/national/clews"
                / f"{technology}_activity_limits_base.csv",
                technology,
            )
            for technology in ("SUPBCWB1", "SUPBCWB2")
        }
        for year in range(2021, 2071):
            rest = NATIONAL_TOTAL_PJ - (
                zonal["SUPBCWB1"][year] + zonal["SUPBCWB2"][year]
            )
            self.assertAlmostEqual(
                sum(caps[tier.technology][year] for tier in TIERS),
                rest,
                places=6,
            )

    def test_rest_plus_bc_equals_national(self) -> None:
        self.assertAlmostEqual(
            REST_OF_CANADA_PJ + BC_ZONAL_TOTAL_PJ, NATIONAL_TOTAL_PJ, places=9
        )


class ArtifactTest(unittest.TestCase):
    """Guard the committed demand-side summaries, dispatch, and manifest."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.summaries = _json(ARTIFACT_DIR / "run_summaries.json")
        cls.manifest = _json(ARTIFACT_DIR / "manifest.json")

    def test_all_cases_are_optimal(self) -> None:
        for name, run in self.summaries["runs"].items():
            self.assertEqual(run["status"]["status"], "optimal", msg=name)
            self.assertIsInstance(run["objective"], (int, float), msg=name)

    def test_reference_matches_committed_curve(self) -> None:
        reference = self.summaries["reference"]
        self.assertAlmostEqual(reference["objective"], 1371124.50, places=2)
        self.assertAlmostEqual(reference["emissions_total_mt"], 38.457287, places=6)

    def test_bcw_balance_closes(self) -> None:
        for name, run in self.summaries["runs"].items():
            self.assertLessEqual(
                run["bcw_balance"]["max_abs_residual_pj"],
                1e-4,
                msg=name,
            )

    def test_carbon_prices_do_not_activate_t3_t4(self) -> None:
        for name, run in self.summaries["runs"].items():
            if not name.startswith("carbon_"):
                continue
            self.assertAlmostEqual(
                run["tier_activity_pj"]["SUPBCWT3"]["total"], 0.0, places=6
            )
            self.assertAlmostEqual(
                run["tier_activity_pj"]["SUPBCWT4"]["total"], 0.0, places=6
            )
            self.assertAlmostEqual(
                run["pwrbcw_total_pj"]["2050"], 41.108219, places=5
            )

    def test_mandate_activates_t3_and_t4(self) -> None:
        run = self.summaries["runs"]["mandate_saturation"]
        self.assertGreater(run["tier_activity_pj"]["SUPBCWT3"]["2050"], 39.0)
        self.assertGreater(run["tier_activity_pj"]["SUPBCWT4"]["2050"], 64.0)
        self.assertAlmostEqual(run["pwrbcw_total_pj"]["2050"], 69.033469, places=5)
        self.assertAlmostEqual(
            run["national_bcw_supply_pj"]["2050"], 256.804505, places=5
        )

    def test_manifest_hashes_match(self) -> None:
        for entry in self.manifest["outputs"]:
            path = ARTIFACT_DIR / entry["path"]
            self.assertTrue(path.exists(), msg=entry["path"])
            self.assertEqual(
                hashlib.sha256(path.read_bytes()).hexdigest(),
                entry["sha256"],
                msg=entry["path"],
            )

    def test_harmonize_passed_for_every_case(self) -> None:
        self.assertEqual(len(self.manifest["harmonize"]), 4)
        for evidence in self.manifest["harmonize"]:
            self.assertEqual(evidence["exit_code"], 0, msg=evidence["params"])
            self.assertIn("HARMONIZED", evidence["message"])

    def test_dispatch_csv_covers_all_cases(self) -> None:
        with (ARTIFACT_DIR / "dispatch_by_zone.csv").open(
            newline="", encoding="utf-8"
        ) as handle:
            rows = list(csv.DictReader(handle))
        names = {row["run"] for row in rows}
        self.assertEqual(
            names,
            {"reference_200", "carbon_250", "carbon_300", "carbon_400", "mandate_saturation"},
        )


@unittest.skipUnless(
    (MODEL_ROOT / "results/carbon_250/params").exists(),
    "disposable model copy with case parameters is not present",
)
class CaseParameterTest(unittest.TestCase):
    """Reload each case parameter set and check caps and carbon prices."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.caps = _load_tier_caps()

    def test_carbon_case_parameters(self) -> None:
        # The hydro cap is checked per technology; use the 2021 activity from
        # the reference production table rather than a single zone series.
        import workflows.national.clews.osecan_national_supply_curve as curve

        hydro = curve._hydro_2021(MODEL_ROOT / "results/stress")
        for spec in CARBON_CASES:
            params = MODEL_ROOT / "results" / str(spec["name"]) / "params"
            if not params.exists():
                self.skipTest(f"{params} not present")
            validate_case_params(
                params,
                _case_spec(str(spec["name"])),
                caps=self.caps,
                hydro_2021=hydro,
            )


if __name__ == "__main__":
    unittest.main()
