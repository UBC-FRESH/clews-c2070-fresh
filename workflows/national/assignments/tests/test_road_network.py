"""Unit tests for the real NRN BC road graph, snapping, and road costs."""

from __future__ import annotations

import unittest
from pathlib import Path

import numpy as np
import pandas as pd
from pyproj import Transformer
from scipy.sparse import csr_matrix

from workflows.national.assignments import bc_chain, stage_b
from workflows.national.assignments.build_road_network import (
    FERRY_FARE_CAD_M3_KM,
    RoadGraph,
    assemble_graph,
    augment_graph_with_edges,
    ferry_pair_lookup,
    ferry_path_metrics,
    load_ferry_segments,
    road_ferry_cost,
    snap_facilities,
)
from workflows.national.assignments.transport import (
    load_road_rate_inputs,
    road_rate_for_grade,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
REAL_LINKS = (
    REPO_ROOT
    / "data/derived/national/assignments/tsa24_pilot/transport_links_bc_real.csv"
)


def _test_graph(node_xy: np.ndarray) -> RoadGraph:
    matrix = csr_matrix(
        (np.array([1000.0]), (np.array([0]), np.array([1]))),
        shape=(len(node_xy), len(node_xy)),
    )
    return RoadGraph(
        node_xy_m=node_xy,
        edge_u=np.array([0]),
        edge_v=np.array([1]),
        edge_length_m=np.array([1000.0]),
        matrix=matrix,
        component_labels=np.zeros(len(node_xy), dtype=int),
        component_sizes=np.array([len(node_xy)]),
        total_features=1,
        kept_features=1,
        class_counts={"Arterial": 1},
        excluded_counts={},
        source_crs="EPSG:4617",
        bounds_wgs84=(-139.0, 48.0, -114.0, 60.0),
        traffic_direction_counts={"Unknown": 1},
        timings_s={"total_s": 0.0},
        peak_rss_mb=0.0,
        gpkg_member="test.gpkg",
    )


class GraphAssemblyTests(unittest.TestCase):
    def test_endpoint_grid_merges_nearby_nodes_and_deduplicates_edges(self) -> None:
        first = np.array([[0.0, 0.0], [100.0, 0.0], [100.0, 0.0], [0.0, 0.4]])
        last = np.array([[100.0, 0.0], [100.0, 100.0], [100.0, 100.0], [100.0, 0.4]])
        lengths = np.array([100.0, 100.0, 50.0, 100.0])
        node_xy, u, v, edge_length, matrix, labels, sizes = assemble_graph(
            first, last, lengths
        )
        self.assertEqual(len(node_xy), 3)
        self.assertEqual(len(edge_length), 2)
        self.assertEqual(set(edge_length.tolist()), {50.0, 100.0})
        self.assertEqual(matrix.shape, (3, 3))
        self.assertEqual(len(sizes), 1)
        self.assertTrue(np.all(labels == labels[0]))
        self.assertEqual(u.tolist(), [0, 1])
        self.assertEqual(v.tolist(), [1, 2])

    def test_empty_graph_fails_closed(self) -> None:
        with self.assertRaisesRegex(Exception, "empty road graph"):
            assemble_graph(
                np.empty((0, 2)), np.empty((0, 2)), np.empty((0,))
            )

    def test_augment_graph_with_ferry_edge_merges_components(self) -> None:
        node_xy = np.array(
            [[0.0, 0.0], [100.0, 0.0], [1000.0, 0.0], [1100.0, 0.0]]
        )
        matrix = csr_matrix(
            (
                np.array([100.0, 100.0]),
                (np.array([0, 2]), np.array([1, 3])),
            ),
            shape=(4, 4),
        )
        graph = RoadGraph(
            node_xy_m=node_xy,
            edge_u=np.array([0, 2]),
            edge_v=np.array([1, 3]),
            edge_length_m=np.array([100.0, 100.0]),
            matrix=matrix,
            component_labels=np.array([0, 0, 1, 1]),
            component_sizes=np.array([2, 2]),
            total_features=2,
            kept_features=2,
            class_counts={"Arterial": 2},
            excluded_counts={},
            source_crs="EPSG:4617",
            bounds_wgs84=(-139.0, 48.0, -114.0, 60.0),
            traffic_direction_counts={"Unknown": 2},
            timings_s={"total_s": 0.0},
            peak_rss_mb=0.0,
            gpkg_member="test.gpkg",
        )
        augmented = augment_graph_with_edges(graph, [1], [2], [900.0])
        self.assertEqual(len(augmented.component_sizes), 1)
        self.assertEqual(int(augmented.component_sizes[0]), 4)
        self.assertEqual(len(augmented.edge_length_m), 3)
        self.assertEqual(len(augmented.ferry_edge_length_m), 0)

    def test_nrn_ferry_layer_has_major_routes_and_positive_placeholder(self) -> None:
        segments = load_ferry_segments(REPO_ROOT / "data/raw/nrn/nrn_bc_gpkg.zip")
        self.assertGreaterEqual(len(segments), 50)
        self.assertGreater(sum(segment.length_m for segment in segments), 1_500_000.0)
        self.assertGreater(FERRY_FARE_CAD_M3_KM, 0.0)


class SnapTests(unittest.TestCase):
    def test_snap_statuses_and_missing_coord(self) -> None:
        transformer = Transformer.from_crs("EPSG:4326", "EPSG:3005", always_xy=True)
        x, y = transformer.transform(-122.69, 53.99)
        node_xy = np.array([[x, y], [x + 1000.0, y]])
        graph = _test_graph(node_xy)
        facilities = pd.DataFrame(
            [
                {
                    "facility_id": "near",
                    "lat": 53.99,
                    "lon": -122.69,
                    "coord_source": "mills_db",
                },
                {
                    "facility_id": "far",
                    "lat": 53.99,
                    "lon": -122.49,
                    "coord_source": "tl_anchor_proxy",
                },
                {
                    "facility_id": "missing",
                    "lat": None,
                    "lon": None,
                    "coord_source": "missing",
                },
            ]
        )
        snapped = snap_facilities(graph, facilities, tolerance_m=5000.0)
        by_id = snapped.set_index("facility_id")
        self.assertEqual(by_id.loc["near", "snap_status"], "snapped")
        self.assertAlmostEqual(float(by_id.loc["near", "snap_m"]), 0.0, places=3)
        self.assertEqual(by_id.loc["far", "snap_status"], "too_far")
        self.assertGreater(float(by_id.loc["far", "snap_m"]), 5000.0)
        self.assertEqual(by_id.loc["missing", "snap_status"], "missing_coord")
        self.assertTrue(pd.isna(by_id.loc["missing", "snap_m"]))

    def test_missing_source_with_coordinates_fails(self) -> None:
        transformer = Transformer.from_crs("EPSG:4326", "EPSG:3005", always_xy=True)
        x, y = transformer.transform(-122.69, 53.99)
        graph = _test_graph(np.array([[x, y], [x + 1000.0, y]]))
        facilities = pd.DataFrame(
            [
                {
                    "facility_id": "bad",
                    "lat": 53.99,
                    "lon": -122.69,
                    "coord_source": "missing",
                }
            ]
        )
        with self.assertRaisesRegex(Exception, "declares coord_source=missing"):
            snap_facilities(graph, facilities)


class FerryEdgeSelectionTests(unittest.TestCase):
    def _two_node_graph(self, road_length_m: float = 1000.0) -> RoadGraph:
        node_xy = np.array([[0.0, 0.0], [100.0, 0.0]])
        return RoadGraph(
            node_xy_m=node_xy,
            edge_u=np.array([0]),
            edge_v=np.array([1]),
            edge_length_m=np.array([road_length_m]),
            matrix=csr_matrix(
                (np.array([road_length_m]), (np.array([0]), np.array([1]))),
                shape=(2, 2),
            ),
            component_labels=np.array([0, 0]),
            component_sizes=np.array([2]),
            total_features=1,
            kept_features=1,
            class_counts={"Arterial": 1},
            excluded_counts={},
            source_crs="EPSG:4617",
            bounds_wgs84=(-139.0, 48.0, -114.0, 60.0),
            traffic_direction_counts={"Unknown": 1},
            timings_s={"total_s": 0.0},
            peak_rss_mb=0.0,
            gpkg_member="test.gpkg",
            edge_mode=np.array(["road"], dtype=object),
            edge_provenance=np.array(["nrn_roadseg"], dtype=object),
        )

    def test_shorter_road_edge_keeps_road_metadata(self) -> None:
        graph = self._two_node_graph(road_length_m=1000.0)
        augmented = augment_graph_with_edges(
            graph,
            [0],
            [1],
            [5000.0],
            edge_mode=["ferry"],
            edge_provenance=["nrn_ferryseg"],
        )
        self.assertEqual(augmented.edge_length_m.tolist(), [1000.0])
        self.assertEqual(augmented.edge_mode.tolist(), ["road"])
        self.assertEqual(augmented.edge_provenance.tolist(), ["nrn_roadseg"])

    def test_shorter_ferry_edge_wins_with_ferry_metadata(self) -> None:
        graph = self._two_node_graph(road_length_m=5000.0)
        augmented = augment_graph_with_edges(
            graph,
            [0],
            [1],
            [1000.0],
            edge_mode=["ferry"],
            edge_provenance=["published_fallback_ferry"],
        )
        self.assertEqual(augmented.edge_length_m.tolist(), [1000.0])
        self.assertEqual(augmented.edge_mode.tolist(), ["ferry"])
        self.assertEqual(
            augmented.edge_provenance.tolist(), ["published_fallback_ferry"]
        )

    def test_ferry_lookup_rejects_ambiguous_pair(self) -> None:
        graph = self._two_node_graph()
        from dataclasses import replace

        ambiguous = replace(
            graph,
            ferry_edge_u=np.array([0, 0]),
            ferry_edge_v=np.array([1, 1]),
            ferry_edge_length_m=np.array([1000.0, 1200.0]),
        )
        with self.assertRaisesRegex(Exception, "appears more than once"):
            ferry_pair_lookup(ambiguous)

    def test_path_metrics_and_cost_split_match_stage_a_and_b(self) -> None:
        predecessors = np.array([-1, 0, 1])
        ferry_pairs = {(1, 2): 300.0}
        ferry_m, crossings = ferry_path_metrics(
            predecessors, 0, 2, ferry_pairs
        )
        self.assertEqual(ferry_m, 300.0)
        self.assertEqual(crossings, 1)
        road_km, ferry_km, cost = road_ferry_cost(100.0, 30.0, 0.05)
        self.assertAlmostEqual(road_km, 70.0)
        self.assertAlmostEqual(ferry_km, 30.0)
        self.assertAlmostEqual(
            cost, 70.0 * 0.05 + 30.0 * FERRY_FARE_CAD_M3_KM
        )
        # Both stages must call the same decomposition helpers.
        self.assertIs(stage_b.road_ferry_cost, bc_chain.road_ferry_cost)
        self.assertIs(stage_b.ferry_path_metrics, bc_chain._ferry_path_metrics)


class RoadCostTests(unittest.TestCase):
    def test_log_and_residue_rate_math(self) -> None:
        rates, capacities = load_road_rate_inputs(REPO_ROOT)
        log = road_rate_for_grade(rates, capacities, grade_id="SAWLOG")
        residue = road_rate_for_grade(rates, capacities, grade_id="RESIDUE")
        self.assertAlmostEqual(log.rate_per_km, 1.25730374 + 120.0 / 90.0)
        self.assertAlmostEqual(
            log.cost_per_m3_km(0.36442761688429814),
            (1.25730374 + 120.0 / 90.0) / 46.1,
        )
        self.assertAlmostEqual(
            residue.cost_per_m3_km(0.45),
            (1.25730374 + 120.0 / 90.0) / (25.0 / 0.45),
        )
        self.assertGreater(
            log.cost_per_m3_km(0.36442761688429814),
            residue.cost_per_m3_km(0.45),
        )

    def test_generated_links_reconcile_distance_and_cost(self) -> None:
        if not REAL_LINKS.exists():
            self.skipTest("real road links have not been generated")
        links = pd.read_csv(REAL_LINKS)
        self.assertFalse(links.empty)
        self.assertEqual(set(links["path_confidence"]), {"real_road_network"})
        self.assertTrue(links["capacity_m3"].isna().all())
        self.assertEqual(set(links["capacity_source"]), {"absent"})
        rates, capacities = load_road_rate_inputs(REPO_ROOT)
        grades = pd.read_csv(
            REPO_ROOT
            / "data/derived/national/assignments/tsa24_pilot/grades.csv"
        ).set_index("grade_id")
        for row in links.itertuples(index=False):
            rate = road_rate_for_grade(rates, capacities, grade_id=row.grade_id)
            density = float(grades.loc[row.grade_id, "density_odt_m3"])
            expected = row.distance_km * rate.cost_per_m3_km(density)
            self.assertAlmostEqual(row.cost_cad_m3, expected, places=4)
            self.assertEqual(row.mode, "road")
            self.assertGreater(row.distance_km, 0.0)
            self.assertGreaterEqual(row.snap_m, 0.0)


if __name__ == "__main__":
    unittest.main()
