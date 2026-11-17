"""Tests for the all-BC woody-residue supply curve builder."""

from __future__ import annotations

import csv
import unittest
from pathlib import Path

from workflows.national.clews.bc_residue_supply import (
    ALL_BC_AAC_M3,
    ALL_BC_APPORTIONMENT_M3,
    BATCH_BC_ALL_SPECIES_RATIO,
    HORIZON_END,
    HORIZON_START,
    LHV_GJ_PER_ODT,
    MISSING_APPORTIONMENT_M3,
    PARSED_AAC_M3,
    PARSED_APPORTIONMENT_M3,
    PRINCE_GEORGE_AAC_M3,
    STAGE_B_TOTAL_RATIO,
    BatchRatios,
    BcResidueSupplyError,
    Scenario,
    TsaArea,
    ZoneCost,
    ZoneSupply,
    allocate_harvest,
    annual_residue_rows,
    build_activity_limit_rows,
    build_tsa_crosswalk,
    build_variable_cost_rows,
    build_zone_crosswalk,
    canonical_tsa_name,
    zone_for_ecoprovince,
)

OUTPUT_DIR = Path("data/derived/national/clews_supply/bc_woody_residue")


def _area(
    name: str,
    number: str,
    forest: dict[str, float],
    thlb: dict[str, float],
) -> TsaArea:
    return TsaArea(
        tsa_name=name,
        tsa_number=number,
        forest_area_ha=forest,
        thlb_area_ha=thlb,
    )


def _ratios() -> BatchRatios:
    return BatchRatios(
        by_ecoprovince={
            "Georgia Depression": 0.05,
            "Southern Coastal Mountains": 0.04,
            "Northern Montane Cordillera": 0.10,
        },
        bc_all_species=0.08,
        log_m3=4000.0,
        residue_odt=320.0,
        matched_cells=3,
        no_area_cells=1,
    )


def _scenario() -> Scenario:
    zones = {
        "B1": ZoneSupply(
            zone_id="B1",
            annual_log_m3=2000.0,
            residue_odt=90.0,
            residue_pj=90.0 * LHV_GJ_PER_ODT / 1_000_000.0,
            ratio_odt_per_m3=0.045,
            ratio_basis="test",
            ecoprovince_fallback_count=0,
        ),
        "B2": ZoneSupply(
            zone_id="B2",
            annual_log_m3=2000.0,
            residue_odt=200.0,
            residue_pj=200.0 * LHV_GJ_PER_ODT / 1_000_000.0,
            ratio_odt_per_m3=0.10,
            ratio_basis="test",
            ecoprovince_fallback_count=0,
        ),
        "CAN": ZoneSupply(
            zone_id="CAN",
            annual_log_m3=4000.0,
            residue_odt=290.0,
            residue_pj=290.0 * LHV_GJ_PER_ODT / 1_000_000.0,
            ratio_odt_per_m3=0.0725,
            ratio_basis="test",
            ecoprovince_fallback_count=0,
        ),
    }
    return Scenario(
        label="test",
        volume_basis="test",
        ratio_mode="ecoprovince",
        zones=zones,
        rows=(),
        audit_rows=(),
    )


class ZoneCrosswalkTest(unittest.TestCase):
    def test_every_ecoprovince_has_one_zone(self) -> None:
        rows = build_zone_crosswalk()
        ecoprovinces = [row["ecoprovince"] for row in rows]
        self.assertEqual(len(ecoprovinces), len(set(ecoprovinces)))
        self.assertEqual(len(rows), 14)
        for row in rows:
            self.assertIn(row["zone_id"], {"B1", "B2"})
            self.assertEqual(row["crosswalk_status"], "fresh_provisional")
            self.assertEqual(row["c2070_boundary"], "no_published_boundary")

    def test_b1_is_exactly_the_coastal_default(self) -> None:
        b1 = {row["ecoprovince"] for row in build_zone_crosswalk() if row["zone_id"] == "B1"}
        self.assertEqual(
            b1,
            {"Georgia Depression", "Southern Coastal Mountains"},
        )

    def test_unknown_ecoprovince_fails_loud(self) -> None:
        with self.assertRaises(BcResidueSupplyError):
            zone_for_ecoprovince("Not An Ecoprovince")

    def test_tsa_name_aliases(self) -> None:
        self.assertEqual(canonical_tsa_name("100 Mile"), "100 Mile House")
        self.assertEqual(canonical_tsa_name("Great Bear North TSA"), "GBR North")
        self.assertEqual(canonical_tsa_name("Haida Gwaii"), "Queen Charlotte")
        self.assertEqual(canonical_tsa_name("Robson"), "Robson Valley")
        self.assertEqual(canonical_tsa_name("Prince George TSA"), "Prince George")

    def test_unknown_tsa_fails_loud(self) -> None:
        with self.assertRaises(BcResidueSupplyError):
            canonical_tsa_name("Not A TSA")


class TsaCrosswalkTest(unittest.TestCase):
    def test_split_tsa_and_dominant_zone(self) -> None:
        areas = {
            "Split": _area(
                "Split",
                "99",
                {"Georgia Depression": 40.0, "Northern Montane Cordillera": 60.0},
                {"Georgia Depression": 30.0, "Northern Montane Cordillera": 70.0},
            ),
            "Single": _area(
                "Single",
                "98",
                {"Georgia Depression": 100.0},
                {"Georgia Depression": 100.0},
            ),
        }
        rows = build_tsa_crosswalk(areas, {"Split": 1000.0, "Single": 500.0}, {})
        by_name = {row["tsa_name"]: row for row in rows}
        self.assertEqual(by_name["Split"]["zone_id"], "B2")
        self.assertEqual(by_name["Split"]["split_tsa"], "true")
        self.assertAlmostEqual(float(by_name["Split"]["b1_thlb_share"]), 0.3, places=9)
        self.assertEqual(by_name["Single"]["zone_id"], "B1")
        self.assertEqual(by_name["Single"]["split_tsa"], "false")

    def test_tsa_split_requires_positive_volume(self) -> None:
        areas = {
            "Split": _area(
                "Split",
                "99",
                {"Georgia Depression": 50.0, "Northern Montane Cordillera": 50.0},
                {"Georgia Depression": 50.0, "Northern Montane Cordillera": 50.0},
            )
        }
        rows = build_tsa_crosswalk(areas, {}, {})
        self.assertEqual(rows[0]["split_tsa"], "false")


class HarvestAllocationTest(unittest.TestCase):
    def test_allocation_conserves_volume(self) -> None:
        areas = {
            "A": _area(
                "A",
                "01",
                {"Georgia Depression": 10.0, "Northern Montane Cordillera": 90.0},
                {"Georgia Depression": 10.0, "Northern Montane Cordillera": 90.0},
            )
        }
        allocated, audit = allocate_harvest({"A": 1000.0}, areas)
        self.assertAlmostEqual(sum(allocated.values()), 1000.0, places=9)
        self.assertAlmostEqual(allocated["Georgia Depression"], 100.0, places=9)
        self.assertAlmostEqual(allocated["Northern Montane Cordillera"], 900.0, places=9)
        self.assertEqual(len(audit), 2)
        self.assertEqual({row["zone_id"] for row in audit}, {"B1", "B2"})

    def test_missing_tsa_fails_loud(self) -> None:
        with self.assertRaises(BcResidueSupplyError):
            allocate_harvest({"Missing": 1.0}, {})


class AnnualResidueTest(unittest.TestCase):
    def test_annualization_is_flat_and_conserves(self) -> None:
        allocated = {
            "Georgia Depression": 1000.0,
            "Southern Coastal Mountains": 1000.0,
            "Northern Montane Cordillera": 2000.0,
        }
        zones, rows = annual_residue_rows(
            allocated,
            _ratios(),
            ratio_mode="ecoprovince",
            volume_basis="test",
        )
        self.assertEqual(len(rows), 3 * (HORIZON_END - HORIZON_START + 1))
        self.assertAlmostEqual(zones["B1"].annual_log_m3, 2000.0, places=9)
        self.assertAlmostEqual(zones["B1"].residue_odt, 90.0, places=9)
        self.assertAlmostEqual(zones["B2"].annual_log_m3, 2000.0, places=9)
        self.assertAlmostEqual(zones["B2"].residue_odt, 200.0, places=9)
        self.assertAlmostEqual(zones["CAN"].residue_odt, 290.0, places=9)
        for year in (HORIZON_START, HORIZON_END):
            year_rows = [row for row in rows if int(row["year"]) == year]
            by_zone = {row["zone_id"]: row for row in year_rows}
            self.assertAlmostEqual(
                float(by_zone["B1"]["residue_pj"])
                + float(by_zone["B2"]["residue_pj"]),
                float(by_zone["CAN"]["residue_pj"]),
                places=9,
            )
            for row in year_rows:
                expected = (
                    float(row["residue_odt"]) * LHV_GJ_PER_ODT / 1_000_000.0
                )
                self.assertAlmostEqual(float(row["residue_pj"]), expected, places=9)
                self.assertEqual(row["basis"], "static_aac")
                self.assertEqual(row["profile"], "flat")

    def test_bc_all_species_mode_uses_one_ratio(self) -> None:
        allocated = {"Georgia Depression": 1000.0, "Northern Montane Cordillera": 1000.0}
        zones, _ = annual_residue_rows(
            allocated,
            _ratios(),
            ratio_mode="bc_all_species",
            volume_basis="test",
        )
        self.assertAlmostEqual(
            zones["CAN"].residue_odt, 2000.0 * 0.08, places=9
        )

    def test_unknown_ratio_mode_fails_loud(self) -> None:
        with self.assertRaises(BcResidueSupplyError):
            annual_residue_rows(
                {"Georgia Depression": 1.0},
                _ratios(),
                ratio_mode="not_a_mode",
                volume_basis="test",
            )


class ParameterRowTest(unittest.TestCase):
    def test_activity_limit_shape_and_value(self) -> None:
        rows = build_activity_limit_rows(_scenario(), "SUPBCWB1", "B1")
        self.assertEqual(len(rows), 50)
        self.assertEqual(
            set(rows[0]),
            {"REGION", "TECHNOLOGY", "YEAR", "VALUE"},
        )
        self.assertEqual(rows[0]["REGION"], "CAN")
        self.assertEqual(rows[0]["TECHNOLOGY"], "SUPBCWB1")
        self.assertEqual([int(row["YEAR"]) for row in rows], list(range(2021, 2071)))
        self.assertTrue(all(float(row["VALUE"]) > 0 for row in rows))
        self.assertAlmostEqual(float(rows[0]["VALUE"]), _scenario().zones["B1"].residue_pj)

    def test_variable_cost_shape_and_units(self) -> None:
        costs = {
            "B1": ZoneCost("B1", 10.0, 5.0, 15.0, 15.0 / 18.5, 1, 100.0),
            "B2": ZoneCost("B2", 5.0, 5.0, 10.0, 10.0 / 18.5, 1, 100.0),
            "CAN": ZoneCost("CAN", 7.5, 5.0, 12.5, 12.5 / 18.5, 2, 200.0),
        }
        rows = build_variable_cost_rows(costs, "SUPBCWB1", "B1")
        self.assertEqual(len(rows), 50)
        self.assertEqual(rows[0]["TECHNOLOGY"], "SUPBCWB1")
        self.assertAlmostEqual(float(rows[0]["VALUE"]), 15.0 / 18.5, places=9)
        self.assertTrue(all(float(row["VALUE"]) > 0 for row in rows))

    def test_missing_zone_fails_loud(self) -> None:
        with self.assertRaises(BcResidueSupplyError):
            build_activity_limit_rows(_scenario(), "SUPBCWB1", "B9")
        with self.assertRaises(BcResidueSupplyError):
            build_variable_cost_rows({}, "SUPBCWB1", "B1")


class BasisConstantsTest(unittest.TestCase):
    def test_documented_totals(self) -> None:
        self.assertAlmostEqual(PARSED_AAC_M3, 44_399_801.0)
        self.assertAlmostEqual(PARSED_APPORTIONMENT_M3, 38_726_439.0)
        self.assertAlmostEqual(PRINCE_GEORGE_AAC_M3, 8_375_000.0)
        self.assertAlmostEqual(ALL_BC_AAC_M3, 52_774_801.0)
        self.assertAlmostEqual(
            ALL_BC_APPORTIONMENT_M3,
            38_726_439.0 + 1_860_000.0 + 1_625_000.0 + 2_115_010.0
            + 3_078_406.0 + 11_019_167.5,
        )
        self.assertEqual(len(MISSING_APPORTIONMENT_M3), 5)

    def test_ratios_are_documented(self) -> None:
        self.assertAlmostEqual(BATCH_BC_ALL_SPECIES_RATIO, 0.0808719103574986, places=12)
        self.assertAlmostEqual(STAGE_B_TOTAL_RATIO, 0.2595527054957869, places=12)


class PublishedArtifactTest(unittest.TestCase):
    """Integration checks against the committed derived CSVs."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.out = Path(__file__).resolve().parents[4] / OUTPUT_DIR
        if not cls.out.exists():
            raise unittest.SkipTest(f"Published artifact not present: {cls.out}")

    def _rows(self, name: str) -> list[dict[str, str]]:
        with (self.out / name).open(newline="", encoding="utf-8") as handle:
            return list(csv.DictReader(handle))

    def test_primary_annual_table(self) -> None:
        rows = self._rows("bc_annual_residue.csv")
        self.assertEqual(len(rows), 150)
        by_year: dict[int, dict[str, dict[str, str]]] = {}
        for row in rows:
            by_year.setdefault(int(row["year"]), {})[row["zone_id"]] = row
        for year in range(2021, 2071):
            self.assertIn(year, by_year)
            b1, b2, can = by_year[year]["B1"], by_year[year]["B2"], by_year[year]["CAN"]
            self.assertAlmostEqual(
                float(b1["annual_log_m3"]) + float(b2["annual_log_m3"]),
                float(can["annual_log_m3"]),
                places=6,
            )
            self.assertAlmostEqual(
                float(b1["residue_odt"]) + float(b2["residue_odt"]),
                float(can["residue_odt"]),
                places=3,
            )
            self.assertAlmostEqual(
                float(b1["residue_pj"]) + float(b2["residue_pj"]),
                float(can["residue_pj"]),
                places=9,
            )
            for row in (b1, b2, can):
                self.assertAlmostEqual(
                    float(row["residue_pj"]),
                    float(row["residue_odt"]) * 18.5 / 1_000_000.0,
                    places=9,
                )
                self.assertEqual(row["basis"], "static_aac")
                self.assertEqual(row["profile"], "flat")

    def test_primary_cap_and_cost_rows(self) -> None:
        for technology, zone in (("SUPBCWB1", "B1"), ("SUPBCWB2", "B2")):
            caps = self._rows(f"{technology}_activity_limits.csv")
            costs = self._rows(f"{technology}_variable_cost.csv")
            self.assertEqual(len(caps), 50)
            self.assertEqual(len(costs), 50)
            self.assertEqual(set(caps[0]), {"REGION", "TECHNOLOGY", "YEAR", "VALUE"})
            self.assertEqual(caps[0]["REGION"], "CAN")
            self.assertEqual(caps[0]["TECHNOLOGY"], technology)
            self.assertEqual(costs[0]["TECHNOLOGY"], technology)
            self.assertEqual([int(row["YEAR"]) for row in caps], list(range(2021, 2071)))
            self.assertEqual([int(row["YEAR"]) for row in costs], list(range(2021, 2071)))
            self.assertTrue(all(float(row["VALUE"]) > 0 for row in caps))
            self.assertTrue(all(float(row["VALUE"]) > 0 for row in costs))
        annual = self._rows("bc_annual_residue.csv")
        b1 = next(row for row in annual if row["year"] == "2021" and row["zone_id"] == "B1")
        b2 = next(row for row in annual if row["year"] == "2021" and row["zone_id"] == "B2")
        cap1 = self._rows("SUPBCWB1_activity_limits.csv")[0]
        cap2 = self._rows("SUPBCWB2_activity_limits.csv")[0]
        self.assertAlmostEqual(float(cap1["VALUE"]), float(b1["residue_pj"]), places=9)
        self.assertAlmostEqual(float(cap2["VALUE"]), float(b2["residue_pj"]), places=9)

    def test_national_rollup_is_sum(self) -> None:
        caps = self._rows("comparison/SUPBCW_activity_limits.csv")
        cap1 = self._rows("SUPBCWB1_activity_limits.csv")
        cap2 = self._rows("SUPBCWB2_activity_limits.csv")
        self.assertEqual(len(caps), 50)
        for national, one, two in zip(caps, cap1, cap2):
            self.assertAlmostEqual(
                float(national["VALUE"]),
                float(one["VALUE"]) + float(two["VALUE"]),
                places=6,
            )

    def test_national_cost_is_pj_weighted(self) -> None:
        national = self._rows("comparison/SUPBCW_variable_cost.csv")[0]
        cost1 = self._rows("SUPBCWB1_variable_cost.csv")[0]
        cost2 = self._rows("SUPBCWB2_variable_cost.csv")[0]
        cap1 = self._rows("SUPBCWB1_activity_limits.csv")[0]
        cap2 = self._rows("SUPBCWB2_activity_limits.csv")[0]
        pj1, pj2 = float(cap1["VALUE"]), float(cap2["VALUE"])
        expected = (
            pj1 * float(cost1["VALUE"]) + pj2 * float(cost2["VALUE"])
        ) / (pj1 + pj2)
        self.assertAlmostEqual(float(national["VALUE"]), expected, places=6)

    def test_crosswalks_cover_published_zones(self) -> None:
        zones = self._rows("bc_zone_crosswalk.csv")
        self.assertEqual({row["zone_id"] for row in zones}, {"B1", "B2"})
        self.assertEqual(len(zones), 14)
        tsas = self._rows("bc_tsa_zone_crosswalk.csv")
        self.assertEqual(len(tsas), 38)
        split = [row for row in tsas if row["split_tsa"] == "true"]
        self.assertEqual(len(split), 7)
        self.assertEqual(
            sum(float(row["aac_m3"]) for row in tsas), ALL_BC_AAC_M3
        )


if __name__ == "__main__":
    unittest.main()
