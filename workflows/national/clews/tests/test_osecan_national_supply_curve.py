"""Tests for the national rest-of-Canada OSeCAN supply curve."""

from __future__ import annotations

import csv
import hashlib
import json
import unittest
from pathlib import Path

from workflows.national.clews.osecan_national import compute_supply_basis
from workflows.national.clews.osecan_national_supply_curve import (
    BC_ZONAL_TOTAL_PJ,
    NATIONAL_TOTAL_PJ,
    REST_OF_CANADA_PJ,
    TIERS,
    tier_caps,
    validate_tier_structure,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
ARTIFACT_DIR = (
    REPO_ROOT / "data/derived/national/clews/osecan/national_supply_curve"
)
ZONAL_DIR = REPO_ROOT / "data/derived/national/clews"


def _series(path: Path, technology: str) -> dict[int, float]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    return {
        int(row["YEAR"]): float(row["VALUE"])
        for row in rows
        if row["TECHNOLOGY"] == technology
    }


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class TierStructureTest(unittest.TestCase):
    """Guard the adopted tier boundaries, caps, and costs."""

    def test_tier_structure_is_valid(self) -> None:
        validate_tier_structure()

    def test_caps_equal_interval_widths_and_tile_the_curve(self) -> None:
        previous = 0.0
        for tier in TIERS:
            self.assertAlmostEqual(tier.lower_pj, previous, places=9)
            self.assertAlmostEqual(
                tier.upper_pj - tier.lower_pj, tier.incremental_cap_pj, places=9
            )
            previous = tier.upper_pj
        self.assertAlmostEqual(
            sum(tier.incremental_cap_pj for tier in TIERS),
            REST_OF_CANADA_PJ,
            places=9,
        )
        self.assertAlmostEqual(
            REST_OF_CANADA_PJ + BC_ZONAL_TOTAL_PJ,
            NATIONAL_TOTAL_PJ,
            places=9,
        )

    def test_costs_are_non_decreasing_and_t4_is_flagged(self) -> None:
        costs = [tier.variable_cost_usd_per_gj for tier in TIERS]
        self.assertEqual(costs, sorted(costs))
        self.assertAlmostEqual(costs[0], 2.456978, places=9)
        self.assertAlmostEqual(costs[2], 3.194071, places=9)
        self.assertAlmostEqual(costs[3], 3.500000, places=9)
        self.assertEqual(TIERS[-1].scenario_flag, "scenario_high_cost_marginal_tier")

    def test_supply_basis_reproduces_200m_total(self) -> None:
        basis = compute_supply_basis(200_000_000.0)
        self.assertAlmostEqual(
            basis.pj_by_recovery["base"], 257.061857084, places=6
        )


class CapReconciliationTest(unittest.TestCase):
    """Guard the cap sum and the no-double-count invariant."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.zonal = {
            technology: _series(
                ZONAL_DIR / f"{technology}_activity_limits_base.csv", technology
            )
            for technology in ("SUPBCWB1", "SUPBCWB2")
        }
        cls.caps = {
            tier.technology: _series(
                ARTIFACT_DIR / "tier_activity_limits.csv", tier.technology
            )
            for tier in TIERS
        }
        cls.supbcw = _series(
            ARTIFACT_DIR / "tier_activity_limits.csv", "SUPBCW"
        )

    def test_tier_caps_sum_to_rest_of_canada_every_year(self) -> None:
        for year in range(2021, 2071):
            tier_sum = sum(self.caps[tier.technology][year] for tier in TIERS)
            rest = NATIONAL_TOTAL_PJ - (
                self.zonal["SUPBCWB1"][year] + self.zonal["SUPBCWB2"][year]
            )
            self.assertAlmostEqual(
                tier_sum, rest, places=6, msg=f"{year}: {tier_sum} != {rest}"
            )

    def test_tiers_plus_bc_zones_equal_national_total_every_year(self) -> None:
        for year in range(2021, 2071):
            total = (
                sum(self.caps[tier.technology][year] for tier in TIERS)
                + self.zonal["SUPBCWB1"][year]
                + self.zonal["SUPBCWB2"][year]
            )
            self.assertAlmostEqual(
                total,
                NATIONAL_TOTAL_PJ,
                places=5,
                msg=f"{year}: national sum {total}",
            )

    def test_single_supbcw_cap_is_disabled(self) -> None:
        self.assertEqual(sorted(self.supbcw), list(range(2021, 2071)))
        for year, value in self.supbcw.items():
            self.assertEqual(value, 0.0, msg=f"SUPBCW cap nonzero in {year}")

    def test_tier_caps_match_the_pure_split(self) -> None:
        expected = tier_caps(self.zonal)
        for tier in TIERS:
            for year in range(2021, 2071):
                self.assertAlmostEqual(
                    self.caps[tier.technology][year],
                    expected[tier.technology][year],
                    places=6,
                )


class RunSummaryTest(unittest.TestCase):
    """Guard the committed baseline and stress run summaries."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.summary = _json(ARTIFACT_DIR / "run_summaries.json")
        cls.manifest = _json(ARTIFACT_DIR / "manifest.json")

    def test_runs_are_optimal(self) -> None:
        for name in ("baseline", "stress", "stress_fixed_hydro"):
            run = self.summary["runs"][name]
            self.assertEqual(run["status"]["status"], "optimal")
            self.assertIsInstance(run["objective"], (int, float))

    def test_bcw_balance_closes(self) -> None:
        for name, run in self.summary["runs"].items():
            self.assertLessEqual(
                run["bcw_balance"]["max_abs_residual_pj"],
                1e-4,
                msg=f"{name}: BCW supply does not equal PWRBCW fuel input",
            )

    def test_stress_binds_t1_t2_and_not_t3_t4(self) -> None:
        binding = self.summary["runs"]["stress"]["cap_binding"]
        self.assertTrue(binding["SUPBCWT1"]["binding"])
        self.assertTrue(binding["SUPBCWT2"]["binding"])
        self.assertFalse(binding["SUPBCWT3"]["binding"])
        self.assertFalse(binding["SUPBCWT4"]["binding"])

    def test_manifest_hashes_exist_and_match(self) -> None:
        for entry in self.manifest["outputs"]:
            path = ARTIFACT_DIR / entry["path"]
            self.assertTrue(path.exists(), msg=entry["path"])
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertEqual(digest, entry["sha256"], msg=entry["path"])


if __name__ == "__main__":
    unittest.main()
