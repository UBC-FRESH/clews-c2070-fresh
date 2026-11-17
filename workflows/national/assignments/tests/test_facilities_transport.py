"""Integration tests for the BC facility table and FFCM TL transport links."""

from __future__ import annotations

import unittest
from pathlib import Path

from workflows.national.assignments.facilities import (
    COORD_SOURCES,
    build_facility_table,
)
from workflows.national.assignments.fixtures import build_tsa24_fixture
from workflows.national.assignments.transport import apply_real_road_links

REPO_ROOT = Path(__file__).resolve().parents[4]
REAL_LINKS = (
    REPO_ROOT
    / "data/derived/national/assignments/tsa24_pilot/transport_links_bc_real.csv"
)


class FacilityTableTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.table = build_facility_table(REPO_ROOT)

    def test_one_row_per_bc_facility(self) -> None:
        self.assertEqual(len(self.table.records), 179)
        self.assertEqual(
            sum(1 for record in self.table.records if record.stage_a_scope), 161
        )
        classes = {record.facility_class for record in self.table.records}
        self.assertEqual(
            classes, {"SAW", "PAP", "PEL", "BIO", "PLY", "OSB", "NSP"}
        )

    def test_coordinate_source_counts(self) -> None:
        self.assertEqual(self.table.coverage["mills_db"], 8)
        self.assertEqual(self.table.coverage["suppliers_dbf"], 0)
        self.assertEqual(self.table.coverage["tl_anchor_proxy"], 126)
        self.assertEqual(self.table.coverage["missing"], 45)
        self.assertEqual(self.table.coverage_in_scope["mills_db"], 7)
        self.assertEqual(self.table.coverage_in_scope["tl_anchor_proxy"], 113)
        self.assertEqual(self.table.coverage_in_scope["missing"], 41)
        self.assertEqual(len(self.table.missing_in_scope), 41)

    def test_coordinate_sources_are_known_and_in_bc(self) -> None:
        for record in self.table.records:
            self.assertIn(record.coord_source, COORD_SOURCES)
            if record.coord_source == "missing":
                self.assertIsNone(record.lat)
                self.assertIsNone(record.lon)
                continue
            assert record.lat is not None and record.lon is not None
            self.assertGreaterEqual(record.lat, 48.0)
            self.assertLessEqual(record.lat, 60.1)
            self.assertGreaterEqual(record.lon, -139.2)
            self.assertLessEqual(record.lon, -113.9)
            self.assertEqual(
                record.coord_confidence,
                {
                    "mills_db": "high",
                    "suppliers_dbf": "high",
                    "tl_anchor_proxy": "low",
                }[record.coord_source],
            )

    def test_all_bc_facilities_have_positive_nameplate(self) -> None:
        self.assertEqual(self.table.zero_capacity, ())
        for record in self.table.records:
            self.assertIsNotNone(record.nameplate_m3_y)
            assert record.nameplate_m3_y is not None
            self.assertGreater(record.nameplate_m3_y, 0.0)
            self.assertEqual(record.capacity_status, "positive")
            self.assertTrue(record.capacity_basis)


class TransportTableTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = build_tsa24_fixture(REPO_ROOT)
        cls.transport = cls.fixture.transport_table

    def test_transport_links_are_uncapped_and_declared(self) -> None:
        self.assertEqual(len(self.transport.links), 210)
        self.assertEqual(len({link.destination for link in self.transport.links}), 129)
        for link in self.transport.links:
            self.assertIsNone(link.capacity_m3)
            self.assertEqual(link.capacity_source, "absent")
            self.assertIn(
                link.path_confidence,
                {"graph_shortest_path", "direct_route"},
            )
            self.assertIn(link.mode, {"ROAD", "RAIL", "WATER", "COMBINATION"})
            self.assertGreater(link.distance_km, 0.0)
            self.assertGreater(link.cost_cad_m3, 0.0)
            self.assertTrue(link.path_nodes)
            self.assertTrue(link.path_legs)

    def test_anchor_facility_is_not_given_a_self_haul(self) -> None:
        self.assertNotIn("BC-SAW-488", {link.destination for link in self.transport.links})
        self.assertEqual(
            self.transport.status_by_facility["BC-SAW-488"],
            "missing_anchor_proxy_self",
        )

    def test_missing_facilities_are_not_fabricated(self) -> None:
        missing = {
            facility_id
            for facility_id, status in self.transport.status_by_facility.items()
            if status.startswith("missing")
        }
        self.assertEqual(len(missing), 32)
        self.assertEqual(
            sum(
                1
                for status in self.transport.status_by_facility.values()
                if status == "missing_no_tl_route"
            ),
            31,
        )

    def test_distance_increases_against_old_diagnostic(self) -> None:
        comparison = self.transport.summary["diagnostic_comparison"]
        new_median = comparison["new_distance_km"]["median"]
        old_median = comparison["old_diagnostic_distance_km"]["median"]
        self.assertGreater(new_median, old_median)
        self.assertGreater(comparison["delta_km"]["median"], 0.0)
        self.assertEqual(comparison["new_distance_km"]["count"], 129)

    def test_transport_modes_are_derived_from_route_legs(self) -> None:
        for link in self.transport.links:
            modes = {leg.mode for leg in link.path_legs}
            if len(modes) == 1:
                self.assertEqual(link.mode, modes.pop())
            else:
                self.assertEqual(link.mode, "COMBINATION")

    def test_contract_source_is_diagnostic_absent(self) -> None:
        for contract in self.fixture.inputs.contracts:
            self.assertEqual(contract.contract_source, "diagnostic_absent")
            self.assertTrue(contract.diagnostic)

    def test_real_road_links_replace_paths_and_flag_fallback(self) -> None:
        if not REAL_LINKS.exists():
            self.skipTest("real road links have not been generated")
        merged = apply_real_road_links(
            self.transport,
            REAL_LINKS,
            self.fixture.inputs.facilities,
            self.fixture.inputs.grades,
        )
        real_road = merged.summary["real_road"]
        fallback = merged.summary["ffcm_fallback"]
        self.assertGreater(real_road["facility_count"], 0)
        self.assertGreater(fallback["facility_count"], 0)
        real_destinations = {
            link.destination
            for link in merged.links
            if link.path_confidence == "real_road_network"
        }
        self.assertEqual(len(real_destinations), real_road["facility_count"])
        for link in merged.links:
            if link.destination in real_destinations:
                self.assertEqual(link.path_confidence, "real_road_network")
                self.assertIsNotNone(link.snap_m)
                self.assertTrue(link.network_source)
            else:
                self.assertIn(
                    link.path_confidence,
                    {"direct_route", "graph_shortest_path"},
                )
        self.assertEqual(
            merged.status_by_facility["BC-SAW-488"],
            "missing_anchor_proxy_self",
        )


if __name__ == "__main__":
    unittest.main()
