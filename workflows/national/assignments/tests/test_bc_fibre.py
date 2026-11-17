"""Tests for BC coordinate recovery and diagnostic contract evidence."""

from __future__ import annotations

import csv
import math
import tempfile
import unittest
from pathlib import Path

from workflows.national.assignments.bc_fibre import (
    FfcmSite,
    MillsRow,
    build_crosswalk,
    city_matches,
    company_matches,
    normalize_city,
    normalize_code,
    normalize_company,
)
from workflows.national.assignments.fixtures import _load_diagnostic_contracts
from workflows.national.assignments.schema import FacilityCapacity, FacilityOffer

REPO_ROOT = Path(__file__).resolve().parents[4]
PILOT_DIR = REPO_ROOT / "data/derived/national/assignments/tsa24_pilot"
RECOVERED = PILOT_DIR / "facilities_bc_recovered.csv"
CONTRACTS = PILOT_DIR / "contracts_bc_diagnostic.csv"
TSA_LONG = PILOT_DIR / "tsa_tenure_apportionment_long.csv"
SAWMILL_LONG = PILOT_DIR / "sawmill_allocation_long.csv"
FFCM_FACILITIES = REPO_ROOT / "metadata/ffcm-archive/parsed/facilities.csv"

KNOWN_CODE_COLLISIONS = {
    "BC-SAW-308",
    "BC-SAW-309",
    "BC-SAW-383",
    "BC-SAW-384",
    "BC-SAW-389",
    "BC-SAW-418",
    "BC-SAW-488",
    "BC-PLY-410",
}


def _mills(
    code: str,
    operator: str,
    city: str,
    *,
    facility_class: str = "SAW",
    lat: float = 54.0,
    lon: float = -122.0,
) -> MillsRow:
    return MillsRow(
        facility_code=code,
        normalized_code=normalize_code(code),
        operator=operator,
        name=city,
        city=city,
        normalized_city=normalize_city(city),
        company=normalize_company(operator),
        facility_class=facility_class,
        total_capacity_m3_y=100000.0,
        lat=lat,
        lon=lon,
        source_row=1,
    )


def _site(
    code: str,
    name: str,
    owner: str,
    city: str,
    *,
    facility_class: str = "SAW",
    lat: float = 54.0,
    lon: float = -122.0,
) -> FfcmSite:
    return FfcmSite(
        facility_id=code,
        normalized_code=normalize_code(code),
        site_name=name,
        site_owner=owner,
        city=city,
        normalized_city=normalize_city(city),
        company=normalize_company(owner),
        facility_class=facility_class,
        lat=lat,
        lon=lon,
        source_row=1,
    )


class NormalizationTests(unittest.TestCase):
    def test_code_normalization_joins_separators(self) -> None:
        for raw in ("BC-SAW320", "BC-SAW-320", "bc_saw_320", " BC SAW 320 "):
            self.assertEqual(normalize_code(raw), "BCSAW320")

    def test_city_normalization_handles_fort_saint_and_typos(self) -> None:
        self.assertEqual(normalize_city("Fort Saint James"), "ft st james")
        self.assertTrue(
            city_matches(normalize_city("Castlegar"), normalize_city("Castledgar"))
        )
        self.assertTrue(
            city_matches(normalize_city("Williams Lake"), normalize_city("Williams Lake Tolko"))
        )

    def test_company_matching_requires_a_distinctive_token(self) -> None:
        self.assertTrue(company_matches("Canfor Corporation", "Canfor"))
        self.assertTrue(
            company_matches("Hampton Affiliates", "Hamton Affiliates")
        )
        self.assertFalse(company_matches("S & W Forest Products", "S & R Sawmills"))
        self.assertFalse(company_matches("Golden Ears", "Noble Cut"))


class CrosswalkTests(unittest.TestCase):
    def test_exact_code_collision_is_rejected(self) -> None:
        facilities = ({"facility": "BC-SAW-488", "facility_class": "SAW"},)
        mills = (_mills("BC-SAW488", "Canfor Corporation", "Prince George"),)
        site = _site(
            "BC-SAW-488",
            "Canfor Corporation-Houston Division",
            "Canfor Corporation",
            "Houston",
            lat=54.384856,
            lon=-126.724033,
        )
        result = build_crosswalk(facilities, mills, (site,))
        self.assertIsNone(result.matches[0].mills)
        self.assertEqual(result.matches[0].match_method, "unmatched")
        self.assertEqual(len(result.conflicts), 1)
        self.assertEqual(
            result.conflicts[0]["conflict_type"],
            "exact_code_name_or_city_conflict",
        )

    def test_mill_name_selects_isle_pierre_not_prince_george(self) -> None:
        facilities = ({"facility": "BC-SAW-1740", "facility_class": "SAW"},)
        mills = (
            _mills("BC-SAW488", "Canfor Corporation", "Prince George"),
            _mills("BC-SAW482", "Canfor Corporation", "Isle Pierre Sawmill"),
        )
        site = _site(
            "BC-SAW-1740",
            "Canfor Corporation-Isle Pierre Sawmill",
            "Canfor Corporation",
            "Prince George",
        )
        result = build_crosswalk(facilities, mills, (site,))
        self.assertIsNotNone(result.matches[0].mills)
        self.assertEqual(result.matches[0].mills.facility_code, "BC-SAW482")
        self.assertEqual(result.matches[0].match_method, "mill_name_class")

    def test_ambiguous_city_does_not_force_a_match(self) -> None:
        facilities = ({"facility": "BC-SAW-999", "facility_class": "SAW"},)
        mills = (
            _mills("BC-SAW901", "Alpha Lumber", "Quesnel"),
            _mills("BC-SAW902", "Beta Lumber", "Quesnel"),
        )
        site = _site("BC-SAW-999", "Gamma Lumber", "Gamma Lumber", "Quesnel")
        result = build_crosswalk(facilities, mills, (site,))
        self.assertIsNone(result.matches[0].mills)
        self.assertEqual(result.matches[0].match_method, "unmatched")


@unittest.skipUnless(RECOVERED.exists(), "recovered table has not been generated")
class RecoveredTableTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        with RECOVERED.open(newline="", encoding="utf-8") as handle:
            cls.rows = list(csv.DictReader(handle))
        with FFCM_FACILITIES.open(newline="", encoding="utf-8") as handle:
            cls.ffcm_ids = {
                row["facility"]
                for row in csv.DictReader(handle)
                if row["facility"].startswith("BC-")
            }

    def test_one_row_per_facility_and_known_methods(self) -> None:
        self.assertEqual(len(self.rows), 179)
        self.assertEqual(len({row["facility_id"] for row in self.rows}), 179)
        methods = {row["match_method"] for row in self.rows}
        self.assertTrue(
            methods
            <= {
                "exact_code_confirmed",
                "mill_name_class",
                "company_city_class",
                "coordinate_corroborated",
                "coordinate_named",
                "unmatched",
                "unmatched_no_registry",
            }
        )

    def test_recovered_coordinates_are_in_bc(self) -> None:
        for row in self.rows:
            if row["coord_source"] != "mills_db_2015":
                self.assertEqual(row["lat"], "")
                self.assertEqual(row["lon"], "")
                continue
            lat = float(row["lat"])
            lon = float(row["lon"])
            self.assertTrue(48.0 <= lat <= 60.1, row["facility_id"])
            self.assertTrue(-139.2 <= lon <= -113.9, row["facility_id"])
            self.assertIn(row["match_confidence"], {"high", "medium", "low"})

    def test_known_code_collisions_are_not_used_as_exact_matches(self) -> None:
        by_id = {row["facility_id"]: row for row in self.rows}
        for facility_id in KNOWN_CODE_COLLISIONS:
            row = by_id[facility_id]
            self.assertNotEqual(row["match_method"], "exact_code_confirmed")
            self.assertNotEqual(row["mills_db_facility_code"], facility_id.replace("-", ""))

    def test_matched_mills_classes_agree(self) -> None:
        for row in self.rows:
            if row["coord_source"] != "mills_db_2015":
                continue
            self.assertEqual(row["facility_class"], row["ffcm_class"])
            self.assertIn(row["facility_id"], self.ffcm_ids)


@unittest.skipUnless(CONTRACTS.exists(), "diagnostic contracts have not been generated")
class DiagnosticContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        with CONTRACTS.open(newline="", encoding="utf-8") as handle:
            cls.rows = list(csv.DictReader(handle))
        with FFCM_FACILITIES.open(newline="", encoding="utf-8") as handle:
            cls.ffcm_ids = {
                row["facility"]
                for row in csv.DictReader(handle)
                if row["facility"].startswith("BC-")
            }

    def test_evidence_tier_and_no_invented_facility_ids(self) -> None:
        for row in self.rows:
            self.assertEqual(row["evidence_tier"], "bc_fibre_db_2018_diagnostic")
            if row["facility_id"]:
                self.assertIn(row["facility_id"], self.ffcm_ids)

    def test_tsa_rows_have_volumes_and_no_facility(self) -> None:
        tsa_rows = [row for row in self.rows if row["tsa_tfl"]]
        self.assertGreater(len(tsa_rows), 100)
        for row in tsa_rows:
            self.assertEqual(row["facility_id"], "")
            self.assertGreater(float(row["allocated_volume"]), 0.0)
            self.assertEqual(row["unit"], "m3/y")
            self.assertEqual(row["crosswalk_status"], "tsa_only_no_facility_field")

    def test_residue_rows_have_shares_and_no_volume(self) -> None:
        share_rows = [row for row in self.rows if row["unit"] == "share_of_residue"]
        self.assertGreater(len(share_rows), 500)
        for row in share_rows:
            share = float(row["allocation_share"])
            self.assertTrue(0.0 <= share <= 1.0)
            self.assertEqual(row["allocated_volume"], "")
            self.assertIn(
                row["crosswalk_status"], {"matched", "ambiguous", "unmatched"}
            )


@unittest.skipUnless(
    TSA_LONG.exists() and SAWMILL_LONG.exists(),
    "parsed BC Fibre workbooks have not been generated",
)
class ParsedWorkbookTests(unittest.TestCase):
    def test_kamloops_2018_apportionment_matches_workbook_total(self) -> None:
        with TSA_LONG.open(newline="", encoding="utf-8") as handle:
            rows = [
                row
                for row in csv.DictReader(handle)
                if row["row_type"] == "apportionment_class"
                and row["period_year"] == "2018"
                and row["tsa"] == "Kamloops"
            ]
        self.assertGreater(len(rows), 4)
        self.assertAlmostEqual(
            sum(float(row["volume_m3"]) for row in rows), 4_000_000.0, places=6
        )

    def test_sawmill_shares_are_bounded_and_streams_are_complete(self) -> None:
        with SAWMILL_LONG.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        self.assertGreater(len(rows), 500)
        self.assertTrue(
            {"Chips", "SawShav", "Hog", "Other Biomass"}
            <= {row["residue_stream"] for row in rows}
        )
        for row in rows:
            share = float(row["allocation_share"])
            self.assertTrue(math.isfinite(share))
            self.assertTrue(0.0 <= share <= 1.0)


class DiagnosticContractLoaderTests(unittest.TestCase):
    def _facility(self) -> FacilityCapacity:
        return FacilityCapacity(
            facility_id="BC-PAP-511",
            facility_name="Domtar-Kamloops",
            facility_class="PAP",
            nameplate_m3_y=350000.0,
            offers=(
                FacilityOffer(
                    offer_id="BC-PAP-511::PULP",
                    period="2026",
                    facility_id="BC-PAP-511",
                    facility_name="Domtar-Kamloops",
                    facility_class="PAP",
                    grade_id="PULP",
                    primary_product="pulpwood",
                    capacity_m3_y=350000.0,
                    processing_cost_cad_m3=10.0,
                    product_revenue_cad_m3=20.0,
                    capacity_basis="test",
                    cost_basis="test",
                    revenue_basis="test",
                    evidence="test",
                ),
            ),
            evidence="test",
        )

    def _write(self, rows: list[dict[str, object]]) -> Path:
        fieldnames = [
            "contract_id",
            "facility_id",
            "allocated_volume",
            "unit",
            "period_year",
            "evidence_tier",
            "source",
        ]
        with tempfile.NamedTemporaryFile(
            "w", suffix=".csv", delete=False, newline="", encoding="utf-8"
        ) as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
            return Path(handle.name)

    def test_tsa_only_and_share_only_rows_do_not_become_contracts(self) -> None:
        path = self._write(
            [
                {
                    "contract_id": "TSA-ONLY",
                    "facility_id": "",
                    "allocated_volume": "1000",
                    "unit": "m3/y",
                    "period_year": "2018",
                    "evidence_tier": "bc_fibre_db_2018_diagnostic",
                    "source": "Kamloops:19",
                },
                {
                    "contract_id": "SHARE-ONLY",
                    "facility_id": "BC-PAP-511",
                    "allocated_volume": "",
                    "unit": "share_of_residue",
                    "period_year": "2018",
                    "evidence_tier": "bc_fibre_db_2018_diagnostic",
                    "source": "Chips:10",
                },
            ]
        )
        try:
            contracts = _load_diagnostic_contracts(
                path, (self._facility(),), period="2026"
            )
            self.assertEqual(contracts, ())
        finally:
            path.unlink(missing_ok=True)

    def test_facility_volume_row_becomes_a_diagnostic_contract(self) -> None:
        path = self._write(
            [
                {
                    "contract_id": "BCFB-TEST",
                    "facility_id": "BC-PAP-511",
                    "allocated_volume": "120000",
                    "unit": "m3/y",
                    "period_year": "2018",
                    "evidence_tier": "bc_fibre_db_2018_diagnostic",
                    "source": "test:1",
                }
            ]
        )
        try:
            contracts = _load_diagnostic_contracts(
                path, (self._facility(),), period="2026"
            )
            self.assertEqual(len(contracts), 1)
            self.assertEqual(contracts[0].max_volume_m3, 120000.0)
            self.assertEqual(
                contracts[0].contract_source, "bc_fibre_db_2018_diagnostic"
            )
            self.assertTrue(contracts[0].diagnostic)
        finally:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
