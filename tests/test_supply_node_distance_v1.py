"""Regression tests for the supply-node distance v1 artifact."""

from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import pytest

from workflows.national.facilities import supply_node_distance_v1 as artifact

OUTPUT_DIR = artifact.OUTPUT_DIR
NODES_CSV = OUTPUT_DIR / "supply_nodes.csv"
PROVINCE_WEIGHTS_CSV = OUTPUT_DIR / "province_node_weights.csv"
SAMPLE_CSV = OUTPUT_DIR / "distance_sample.csv"
SAMPLE_ROUTES_JSON = OUTPUT_DIR / "distance_sample_routes.json"
DISTRIBUTIONS_CSV = OUTPUT_DIR / "distance_distribution_comparison.csv"
BANDS_CSV = OUTPUT_DIR / "band_comparison.csv"
GAPS_CSV = OUTPUT_DIR / "transport_gap_comparison.csv"
PROVINCE_GAPS_CSV = OUTPUT_DIR / "province_transport_gap.csv"
SENSITIVITY_CSV = OUTPUT_DIR / "weight_sensitivity.csv"
PROVENANCE_CSV = OUTPUT_DIR / "provenance.csv"
SUMMARY_JSON = OUTPUT_DIR / "summary.json"
MANIFEST_JSON = OUTPUT_DIR / "manifest.json"
REPORT_MD = OUTPUT_DIR / "SUPPLY_NODE_DISTANCE_V1.md"


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@pytest.fixture(scope="module")
def summary() -> dict[str, Any]:
    with SUMMARY_JSON.open("r", encoding="utf-8") as handle:
        return json.load(handle)


@pytest.fixture(scope="module")
def nodes() -> list[dict[str, str]]:
    return _read_csv(NODES_CSV)


@pytest.fixture(scope="module")
def bands() -> list[dict[str, str]]:
    return _read_csv(BANDS_CSV)


@pytest.fixture(scope="module")
def gaps() -> list[dict[str, str]]:
    return _read_csv(GAPS_CSV)


@pytest.fixture(scope="module")
def province_gaps() -> list[dict[str, str]]:
    return _read_csv(PROVINCE_GAPS_CSV)


@pytest.fixture(scope="module")
def samples() -> list[dict[str, str]]:
    return _read_csv(SAMPLE_CSV)


@pytest.fixture(scope="module")
def sensitivity() -> list[dict[str, str]]:
    return _read_csv(SENSITIVITY_CSV)


def test_outputs_exist_manifest_hashes_and_labels(summary) -> None:
    for path in (
        NODES_CSV,
        PROVINCE_WEIGHTS_CSV,
        SAMPLE_CSV,
        SAMPLE_ROUTES_JSON,
        DISTRIBUTIONS_CSV,
        BANDS_CSV,
        GAPS_CSV,
        PROVINCE_GAPS_CSV,
        SENSITIVITY_CSV,
        PROVENANCE_CSV,
        SUMMARY_JSON,
        MANIFEST_JSON,
        REPORT_MD,
    ):
        assert path.is_file(), path
    assert list(_read_csv(NODES_CSV)[0]) == artifact.NODE_FIELDS
    assert list(_read_csv(BANDS_CSV)[0]) == artifact.BAND_FIELDS
    assert list(_read_csv(GAPS_CSV)[0]) == artifact.GAP_FIELDS
    assert list(_read_csv(PROVINCE_GAPS_CSV)[0]) == artifact.PROVINCE_GAP_FIELDS
    assert list(_read_csv(SENSITIVITY_CSV)[0]) == artifact.SENSITIVITY_FIELDS

    manifest = json.loads(MANIFEST_JSON.read_text(encoding="utf-8"))
    assert manifest["artifact"] == "supply_node_distance_v1"
    assert manifest["artifact_version"] == "1.0.0"
    assert manifest["status"] == "complete"
    for name, entry in manifest["outputs"].items():
        path = OUTPUT_DIR / name
        assert path.is_file(), name
        assert path.stat().st_size == entry["bytes"], name
        assert _sha256(path) == entry["sha256"], name

    labels = summary["labels"]
    assert labels["evidence_label"] == "model_estimate"
    assert labels["observed"] is False
    assert labels["policy_grade"] is False
    assert labels["national_claim"] is False
    assert labels["assignment_change"] is False
    assert labels["official_osecan_changed"] is False
    assert labels["ui_changed"] is False
    assert labels["siting_optimization"] is False
    assert labels["greenfield_optimization"] is False
    assert labels["policy_claim"] is False

    report = REPORT_MD.read_text(encoding="utf-8")
    assert "observed=false" in report
    assert "policy_grade=false" in report
    assert "not a national statistic" in report
    assert "no policy" in report.lower()
    assert "assignment is unchanged" in report


def test_node_weight_and_harvest_conservation(
    summary, nodes, province_gaps
) -> None:
    for province in artifact.PROVINCE_ORDER:
        province_nodes = [row for row in nodes if row["province"] == province]
        if not province_nodes:
            continue
        weight_sum = math.fsum(float(row["weight"]) for row in province_nodes)
        area_weight_sum = math.fsum(
            float(row["area_weight"]) for row in province_nodes
        )
        assert weight_sum == pytest.approx(1.0, abs=1e-8)
        assert area_weight_sum == pytest.approx(1.0, abs=1e-7)
        harvest_sum = math.fsum(
            float(row["harvest_m3"]) for row in province_nodes
        )
        province_harvest = float(province_nodes[0]["province_harvest_m3"])
        assert harvest_sum == pytest.approx(province_harvest, abs=1e-3)

    total_harvest = math.fsum(float(row["harvest_m3"]) for row in nodes)
    assert total_harvest == pytest.approx(118_977_339.1, abs=1e-3)
    assert len({row["node_id"] for row in nodes}) == len(nodes)
    assert all(float(row["production_m3"]) > 0.0 for row in nodes)
    assert all(float(row["weight"]) > 0.0 for row in nodes)
    assert sum(int(row["node_count"]) for row in _read_csv(
        PROVINCE_WEIGHTS_CSV
    )) == len(nodes)
    assert math.fsum(float(row["assigned_m3"]) for row in province_gaps) == (
        pytest.approx(118_977_339.1, abs=1e-3)
    )


def test_node_coordinates_and_fallback_diagnostics(summary, nodes) -> None:
    for row in nodes:
        latitude = float(row["lat"])
        longitude = float(row["lon"])
        assert math.isfinite(latitude) and -90.0 <= latitude <= 90.0
        assert math.isfinite(longitude) and -180.0 <= longitude <= 180.0
        assert row["centroid_method"].startswith("tile_province_intersection")
        assert float(row["fallback_area_ha"]) >= 0.0
        assert float(row["raw_area_ha"]) >= float(row["located_area_ha"]) - 1e-6
    diagnostics = summary["nodes"]["rsyc_diagnostics"]
    assert diagnostics["fallback_production_share"] >= 0.0
    assert diagnostics["fallback_production_share"] < 0.01
    assert diagnostics["fallback_production_m3"] > 0.0


def test_distance_distributions_are_finite_and_ordered(summary) -> None:
    distributions = summary["distance_distributions"]
    assert len(distributions) == 36
    assert any(
        row["scope"] == "national" and row["origin_basis"] == "node_weighted"
        for row in distributions
    )
    for row in distributions:
        for key in (
            "mean_km",
            "minimum_km",
            "p10_km",
            "p25_km",
            "median_km",
            "p75_km",
            "p90_km",
            "maximum_km",
        ):
            value = float(row[key])
            assert math.isfinite(value) and value >= 0.0
        assert (
            row["minimum_km"]
            <= row["p10_km"]
            <= row["p25_km"]
            <= row["median_km"]
            <= row["p75_km"]
            <= row["p90_km"]
            <= row["maximum_km"]
        )
        assert row["minimum_km"] <= row["mean_km"] <= row["maximum_km"]
    for row in summary["centroid_percentiles"]:
        percentile = float(
            row["centroid_median_percentile_in_node_distribution"]
        )
        assert 0.0 <= percentile <= 100.0


def test_band_volume_conservation_and_partitions(
    summary, bands, gaps
) -> None:
    total = math.fsum(float(row["assigned_m3"]) for row in gaps)
    assert total == pytest.approx(118_977_339.1, abs=1e-3)
    for basis in ("centroid", "node"):
        basis_total = math.fsum(
            float(row[f"{basis}_assigned_m3"]) for row in bands
        )
        assert basis_total == pytest.approx(total, abs=1e-3)

    by_cell: dict[tuple[str, str], dict[str, float]] = {}
    for row in bands:
        key = (row["province"], row["facility_class"])
        values = by_cell.setdefault(
            key, {"centroid": 0.0, "node": 0.0, "shares": 0.0, "node_shares": 0.0}
        )
        values["centroid"] += float(row["centroid_assigned_m3"])
        values["node"] += float(row["node_assigned_m3"])
        values["shares"] += float(row["centroid_share"])
        values["node_shares"] += float(row["node_share"])
    for row in gaps:
        key = (row["province"], row["facility_class"])
        values = by_cell[key]
        assigned = float(row["assigned_m3"])
        assert values["centroid"] == pytest.approx(assigned, abs=1e-3)
        assert values["node"] == pytest.approx(assigned, abs=1e-3)
        assert values["shares"] == pytest.approx(1.0, abs=1e-6)
        assert values["node_shares"] == pytest.approx(1.0, abs=1e-6)
        assert float(row["node_within_150_m3_y"]) + float(
            row["node_gt150_m3_y"]
        ) == pytest.approx(assigned, abs=1e-3)
        assert float(row["node_within_300_m3_y"]) + float(
            row["node_gt300_m3_y"]
        ) == pytest.approx(assigned, abs=1e-3)
        assert float(row["node_gt300_m3_y"]) <= float(
            row["node_gt150_m3_y"]
        ) + 1e-3
        assert float(row["centroid_gt300_m3_y"]) <= float(
            row["centroid_gt150_m3_y"]
        ) + 1e-3


def test_capacity_partitions_and_gap_identity(gaps) -> None:
    for row in gaps:
        assert float(row["centroid_local_capacity_within_50_m3_y"]) <= (
            float(row["centroid_local_capacity_within_150_m3_y"]) + 1e-3
        )
        assert float(row["centroid_local_capacity_within_150_m3_y"]) <= (
            float(row["centroid_local_capacity_within_300_m3_y"]) + 1e-3
        )
        assert float(row["centroid_local_capacity_within_300_m3_y"]) <= (
            float(row["local_capacity_total_m3_y"]) + 1e-3
        )
        assert float(row["node_weighted_local_capacity_within_50_m3_y"]) <= (
            float(row["node_weighted_local_capacity_within_150_m3_y"]) + 1e-3
        )
        assert float(row["node_weighted_local_capacity_within_150_m3_y"]) <= (
            float(row["node_weighted_local_capacity_within_300_m3_y"]) + 1e-3
        )
        assigned = float(row["assigned_m3"])
        assert float(row["centroid_local_access_gap_within_150_m3_y"]) == (
            pytest.approx(
                max(
                    0.0,
                    assigned
                    - float(row["centroid_local_capacity_within_150_m3_y"]),
                ),
                abs=1e-3,
            )
        )
        assert float(row["node_local_access_gap_within_150_m3_y"]) == (
            pytest.approx(
                max(
                    0.0,
                    assigned
                    - float(
                        row["node_weighted_local_capacity_within_150_m3_y"]
                    ),
                ),
                abs=1e-3,
            )
        )


def test_sensitivity_conservation(summary, sensitivity) -> None:
    total = 118_977_339.1
    for basis in ("rsyc_production", "scanfi_area"):
        rows = [row for row in sensitivity if row["weight_basis"] == basis]
        assert rows
        national = [row for row in rows if row["level"] == "national"]
        assert len(national) == 1
        assert float(national[0]["assigned_m3"]) == pytest.approx(
            total, abs=1e-3
        )
        province_rows = [row for row in rows if row["level"] == "province"]
        assert math.fsum(
            float(row["assigned_m3"]) for row in province_rows
        ) == pytest.approx(total, abs=1e-3)
        for row in rows:
            assert float(row["within_150_m3_y"]) + float(
                row["gt150_m3_y"]
            ) == pytest.approx(float(row["assigned_m3"]), abs=1e-3)
            assert float(row["within_300_m3_y"]) + float(
                row["gt300_m3_y"]
            ) == pytest.approx(float(row["assigned_m3"]), abs=1e-3)
            assert float(row["gt300_m3_y"]) <= float(
                row["gt150_m3_y"]
            ) + 1e-3


def test_osrm_sample_arithmetic_and_pairs(summary, samples) -> None:
    osrm = summary["osrm_sample"]
    assert len(samples) == int(osrm["requests_attempted"])
    assert int(osrm["requests_ok"]) + int(osrm["requests_failed"]) == len(
        samples
    )
    assert osrm["paired_ok_count"] == sum(
        1
        for row in samples
        if row["osrm_status"] == "ok"
        and float(row["centroid_osrm_road_km"])
    )
    for row in samples:
        assert row["node_id"]
        assert row["province"]
        if row["osrm_status"] != "ok":
            assert row["osrm_road_km"] == ""
            assert row["error"]
            continue
        road = float(row["osrm_road_km"])
        duration = float(row["osrm_duration_h"])
        haversine = float(row["node_haversine_km"])
        assert road > 0.0 and duration > 0.0 and haversine >= 0.0
        assert float(row["node_road_haversine_ratio"]) == pytest.approx(
            road / haversine, abs=1e-5
        )
        assert float(row["node_factor_error_pct"]) == pytest.approx(
            100.0
            * (float(row["node_factor_km"]) - road)
            / road,
            abs=1e-5,
        )
        assert float(row["origin_snap_km"]) >= 0.0
        assert float(row["destination_snap_km"]) >= 0.0


def test_no_nan_or_negative_values(bands, gaps, samples, sensitivity) -> None:
    rows: list[dict[str, str]] = [*bands, *gaps, *samples, *sensitivity]
    for row in rows:
        for key, value in row.items():
            if value == "":
                continue
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            assert math.isfinite(number), f"{key}={value}"
            if key.endswith(("lat", "lon")):
                continue
            signed = (
                "delta" in key
                or "minus" in key
                or "error_pct" in key
                or key == "factor_delta_pct"
            )
            if not signed:
                assert number >= 0.0, f"{key}={value}"


def test_summary_validation_and_no_assignment_change(summary) -> None:
    assert summary["validation"]["all_passed"] is True
    assert all(summary["validation"]["checks"].values())
    assert summary["counts"]["nodes"] == 525
    assert summary["counts"]["facilities"] == 591
    assert summary["counts"]["assignments"] == 203
    assert summary["counts"]["band_rows"] == 165
    assert summary["counts"]["gap_rows"] == 33
    assert summary["counts"]["province_rows"] == 12
    assert summary["labels"]["assignment_change"] is False
    assert summary["labels"]["capacity_change"] is False
    assert summary["labels"]["national_claim"] is False


def test_band_boundaries_and_weighted_quantile() -> None:
    assert artifact.band_for_distance(0.0) == "0-50"
    assert artifact.band_for_distance(49.999) == "0-50"
    assert artifact.band_for_distance(50.0) == "50-100"
    assert artifact.band_for_distance(150.0) == "150-300"
    assert artifact.band_for_distance(300.0) == ">300"
    with pytest.raises(artifact.SupplyNodeDistanceError):
        artifact.band_for_distance(-1.0)
    with pytest.raises(artifact.SupplyNodeDistanceError):
        artifact.band_for_distance(math.nan)
    assert artifact.weighted_quantile([1.0, 2.0, 3.0], [1.0, 1.0, 1.0], 0.5) == 2.0
    assert artifact.weighted_quantile([1.0, 2.0], [3.0, 1.0], 0.5) == 1.0
