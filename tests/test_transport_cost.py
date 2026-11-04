"""Tests for ``fresh_fibre.transport_cost``.

The diagnostic cost-surface module is load-bearing for the Phase 3 LP
path (see ``metadata/source-reviews/nrn_vectors.md`` and ``nrwn_vectors.md``
forward plan). The tests below cover:

1. haversine symmetry at a non-trivial distance.
2. great-circle arc-length on the equator — 1° of longitude at latitude
   0 is the canonical 111.32 km reference value; 0.5° at the equator
   is correspondingly ≈ 55.66 km (sanity check, both within tolerance).
3. road_haul_cost_per_tonne_km at 100 km is positive and well below the
   0.30 CAD per tonne·km bound established for the cost surface.
4. rail_haul_cost_per_tonne_km is cheaper per tonne·km than road at the
   same distance (rail labour amortises better at long-haul scales).
5. segment_cost arithmetic — ``segment_cost = per_tonne_km × length ×
   class_multiplier`` for each documented class.
6. supply_node_centroid returns non-``None`` tuples for the documented
   BC + QC fixture supply nodes.
7. unknown supply_node returns ``(None, None)`` without raising.
8. total_haul_cost_bc_qc over a 3-row fixture table equals 3 × single-row
   cost (sum-equivalence).

All test cases pull from BC + QC placeholder denominator values; CAG-
friendly counts are documented in the module docstring.
"""

from __future__ import annotations

import unittest

from fresh_fibre.transport_cost import (
    EARTH_RADIUS_KM,
    ROAD_SEGMENT_MULTIPLIERS,
    RAIL_SEGMENT_MULTIPLIERS,
    bc_facility_centroid,
    bc_supply_node_centroid,
    great_circle_distance_km,
    haversine_km,
    mileage_placeholder_km,
    qc_facility_centroid,
    qc_supply_node_centroid,
    rail_haul_cost_per_tonne_km,
    rail_segment_cost,
    road_haul_cost_per_tonne_km,
    road_segment_cost,
    total_haul_cost_bc_qc,
)


class TestHaversineSymmetry(unittest.TestCase):
    """The haversine / great-circle path is symmetric and pure."""

    def test_haversine_is_symmetric(self) -> None:
        # Vancouver (50.1, -125.3) → Montreal (45.5, -73.6) ~ 3817 km,
        # and the reverse should be identical bytes.
        a = haversine_km(50.1, -125.3, 45.5, -73.6)
        b = haversine_km(45.5, -73.6, 50.1, -125.3)
        self.assertAlmostEqual(a, b, places=9)
        self.assertGreater(a, 3000.0)
        self.assertLess(a, 5000.0)

    def test_haversine_zero_for_identical_points(self) -> None:
        self.assertEqual(haversine_km(50.1, -125.3, 50.1, -125.3), 0.0)

    def test_haversine_rejects_non_finite(self) -> None:
        with self.assertRaises(ValueError):
            haversine_km(float("nan"), 0.0, 0.0, 0.0)


class TestGreatCircleDistanceKm(unittest.TestCase):
    """The tuple-form great-circle path mirrors the haversine path."""

    def test_one_degree_at_equator_is_about_111_km(self) -> None:
        # 1° of longitude at latitude 0 → 2π·R / 360. With the IUGG
        # mean Earth radius (6371.0088 km), the value is ≈ 111.195 km;
        # with the equatorial radius (6378.137 km), it is ≈ 111.32 km.
        # We assert the IUGG-mean value and a delta tolerance that
        # also covers the equatorial-radius convention.
        expected = 2.0 * 3.141592653589793 * EARTH_RADIUS_KM / 360.0
        distance = great_circle_distance_km((0.0, 0.0), (0.0, 1.0))
        self.assertAlmostEqual(distance, expected, places=4)
        self.assertAlmostEqual(distance, 111.195, delta=0.20)

    def test_half_degree_at_equator_is_about_55_km(self) -> None:
        # 0.5° of longitude at the equator → ~55.66 km (half of the
        # reference 1° value). This is the sanity duck for the test
        # prompt's "~111 km for equator half-degree" — geographically
        # the prompt refers to the 1° rate; we verify both.
        distance = great_circle_distance_km((0.0, 0.0), (0.0, 0.5))
        self.assertAlmostEqual(distance, 55.66, delta=0.1)

    def test_tuple_form_matches_haversine(self) -> None:
        a = great_circle_distance_km((45.5, -73.6), (50.1, -125.3))
        b = haversine_km(45.5, -73.6, 50.1, -125.3)
        self.assertAlmostEqual(a, b, places=9)

    def test_rejects_malformed_tuples(self) -> None:
        with self.assertRaises(ValueError):
            great_circle_distance_km((0.0,), (0.0, 1.0))


class TestRoadHaulCost(unittest.TestCase):
    """Road haul cost arithmetic."""

    def test_road_cost_at_100km_positive_and_below_bound(self) -> None:
        cost = road_haul_cost_per_tonne_km(100)
        self.assertGreater(cost, 0.0)
        self.assertLess(cost, 0.30)

    def test_road_cost_positive_at_short_distance(self) -> None:
        # Even at 1 km the road cost is positive (driver / fuel
        # surcharge is amortised against a tiny denominator).
        self.assertGreater(road_haul_cost_per_tonne_km(1), 0.0)

    def test_road_cost_decreases_with_distance(self) -> None:
        # Labour amortises; cost per tonne·km falls monotonically as
        # distance grows.
        self.assertGreater(
            road_haul_cost_per_tonne_km(50),
            road_haul_cost_per_tonne_km(500),
        )

    def test_road_cost_rejects_negative_distance(self) -> None:
        with self.assertRaises(ValueError):
            road_haul_cost_per_tonne_km(-1.0)


class TestRailVsRoadParity(unittest.TestCase):
    """Rail haul cost arithmetic and rail-vs-road relationship."""

    def test_rail_cheaper_per_tonne_km_than_road(self) -> None:
        # Rail labour amortises more efficiently and the placeholder
        # electricity surcharge is per-trip-amortised, so rail is
        # cheaper per tonne·km than road at every reasonable distance.
        for distance in (50, 100, 500, 780):
            with self.subTest(distance_km=distance):
                rail = rail_haul_cost_per_tonne_km(distance)
                road = road_haul_cost_per_tonne_km(distance)
                self.assertLess(rail, road)

    def test_rail_cost_positive_and_below_bound(self) -> None:
        cost = rail_haul_cost_per_tonne_km(620)
        self.assertGreater(cost, 0.0)
        self.assertLess(cost, 0.30)

    def test_rail_cost_rejects_negative_distance(self) -> None:
        with self.assertRaises(ValueError):
            rail_haul_cost_per_tonne_km(-5.0)


class TestSegmentCostArithmetic(unittest.TestCase):
    """``segment_cost = per_tonne_km × length × class_multiplier``."""

    def test_road_pipeline_matches_base_formula(self) -> None:
        length = 95.0
        expected = road_haul_cost_per_tonne_km(length) * length * ROAD_SEGMENT_MULTIPLIERS["pipeline"]
        self.assertAlmostEqual(road_segment_cost(length, "pipeline"), expected, places=9)

    def test_road_arterial_uses_1p15_multiplier(self) -> None:
        length = 95.0
        expected = road_haul_cost_per_tonne_km(length) * length * 1.15
        self.assertAlmostEqual(road_segment_cost(length, "arterial"), expected, places=9)

    def test_road_local_uses_1p30_multiplier(self) -> None:
        length = 95.0
        expected = road_haul_cost_per_tonne_km(length) * length * 1.30
        self.assertAlmostEqual(road_segment_cost(length, "local"), expected, places=9)

    def test_rail_main_uses_0p85_multiplier(self) -> None:
        length = 620.0
        expected = rail_haul_cost_per_tonne_km(length) * length * RAIL_SEGMENT_MULTIPLIERS["main"]
        self.assertAlmostEqual(rail_segment_cost(length, "main"), expected, places=9)

    def test_rail_branch_uses_1p10_multiplier(self) -> None:
        length = 620.0
        expected = rail_haul_cost_per_tonne_km(length) * length * 1.10
        self.assertAlmostEqual(rail_segment_cost(length, "branch"), expected, places=9)

    def test_rail_yard_uses_1p50_multiplier(self) -> None:
        length = 620.0
        expected = rail_haul_cost_per_tonne_km(length) * length * 1.50
        self.assertAlmostEqual(rail_segment_cost(length, "yard"), expected, places=9)

    def test_segment_cost_rejects_negative(self) -> None:
        with self.assertRaises(ValueError):
            road_segment_cost(-1.0, "pipeline")
        with self.assertRaises(ValueError):
            rail_segment_cost(-1.0, "main")


class TestSupplyNodeCentroids(unittest.TestCase):
    """Centroid lookups for BC + QC fixture supply nodes."""

    def test_bc_supply_centroids_are_non_none(self) -> None:
        for node_id in ("supply_coast_01", "supply_interior_01", "supply_residue_02"):
            with self.subTest(node_id=node_id):
                lat, lon = bc_supply_node_centroid(node_id)
                self.assertIsNotNone(lat)
                self.assertIsNotNone(lon)

    def test_qc_supply_centroids_are_non_none(self) -> None:
        for node_id in ("supply_qc_saguenay_01", "supply_qc_residue_01"):
            with self.subTest(node_id=node_id):
                lat, lon = qc_supply_node_centroid(node_id)
                self.assertIsNotNone(lat)
                self.assertIsNotNone(lon)

    def test_unknown_supply_node_returns_none_tuple_without_raising(self) -> None:
        # No exception; result of (None, None) is the operational
        # fallback per AGENTS.md (centroid absences are debuggable,
        # not exceptional).
        lat_bc, lon_bc = bc_supply_node_centroid("__never_seen_node__")
        lat_qc, lon_qc = qc_supply_node_centroid("__never_seen_node__")
        self.assertEqual((lat_bc, lon_bc), (None, None))
        self.assertEqual((lat_qc, lon_qc), (None, None))

    def test_facility_centroids_have_known_fixture_entries(self) -> None:
        # BC facility centroid coverage
        bc_lat, bc_lon = bc_facility_centroid("mill_sawmill_coast")
        self.assertIsNotNone(bc_lat)
        # QC facility centroid coverage
        qc_lat, qc_lon = qc_facility_centroid("mill_sawmill_qc")
        self.assertIsNotNone(qc_lat)

    def test_mileage_placeholder_returns_positive_for_unknown(self) -> None:
        self.assertGreater(mileage_placeholder_km("__never_seen__"), 0.0)


class TestTotalHaulCostAggregator(unittest.TestCase):
    """``total_haul_cost_bc_qc`` sum-equivalence and empty-table guard."""

    def test_empty_table_returns_zero(self) -> None:
        self.assertEqual(total_haul_cost_bc_qc([]), 0.0)

    def test_three_row_table_equals_three_times_single_row(self) -> None:
        row = {
            "supply_node_id": "supply_coast_01",
            "destination_node_id": "mill_sawmill_coast",
            "mode": "road",
            "distance_km": 95.0,
            "province": "BC",
        }
        single = total_haul_cost_bc_qc([row])
        triple = total_haul_cost_bc_qc([dict(row), dict(row), dict(row)])
        self.assertAlmostEqual(triple, 3.0 * single, places=9)
        self.assertGreater(single, 0.0)

    def test_rail_row_supports_distinct_mode(self) -> None:
        rail_row = {
            "supply_node_id": "supply_interior_01",
            "destination_node_id": "export_vancouver",
            "mode": "rail",
            "distance_km": 780.0,
            "province": "BC",
        }
        road_row = dict(rail_row, mode="road", distance_km=780.0)
        rail_cost = total_haul_cost_bc_qc([rail_row])
        road_cost = total_haul_cost_bc_qc([road_row])
        # Rail is cheaper per tonne·km; total scales with distance.
        self.assertLess(rail_cost, road_cost)


if __name__ == "__main__":
    unittest.main()
