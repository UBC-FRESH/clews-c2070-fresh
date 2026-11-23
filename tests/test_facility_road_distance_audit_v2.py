"""Regression tests for the expanded facility road-distance audit v2."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import statistics
from pathlib import Path
from typing import Any

import pytest

from workflows.national.facilities import (
    facility_road_distance_audit as v1,
)
from workflows.national.facilities import (
    facility_road_distance_audit_v2 as audit,
)

OUTPUT_DIR = audit.OUTPUT_DIR
SAMPLE_CSV = OUTPUT_DIR / "facility_road_distance_sample.csv"
FACTORS_CSV = OUTPUT_DIR / "province_road_factors.csv"
SUMMARY_JSON = OUTPUT_DIR / "summary.json"
MANIFEST_JSON = OUTPUT_DIR / "manifest.json"
ROUTES_JSON = OUTPUT_DIR / "osrm_routes.json"
PROVENANCE_CSV = OUTPUT_DIR / "provenance.csv"
REPORT_MD = OUTPUT_DIR / "FACILITY_ROAD_DISTANCE_AUDIT_V2.md"


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
def sample_rows() -> list[dict[str, str]]:
    return _read_csv(SAMPLE_CSV)


@pytest.fixture(scope="module")
def factor_rows() -> list[dict[str, str]]:
    return _read_csv(FACTORS_CSV)


def test_outputs_exist_and_manifest_hashes() -> None:
    for path in (
        SAMPLE_CSV,
        FACTORS_CSV,
        SUMMARY_JSON,
        MANIFEST_JSON,
        ROUTES_JSON,
        PROVENANCE_CSV,
        REPORT_MD,
    ):
        assert path.is_file(), path
    with MANIFEST_JSON.open("r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    assert manifest["artifact"] == "facility_road_distance_audit_v2"
    for name, entry in manifest["outputs"].items():
        path = OUTPUT_DIR / name
        assert path.is_file(), name
        assert path.stat().st_size == entry["bytes"], name
        assert _sha256(path) == entry["sha256"], name


def test_sample_counts_and_provinces(
    summary: dict[str, Any],
    sample_rows: list[dict[str, str]],
    factor_rows: list[dict[str, str]],
) -> None:
    assert len(sample_rows) == 81
    assert len(sample_rows) == summary["sample"]["n_pairs"]
    assert {row["province"] for row in sample_rows} == {
        row["province"]
        for row in factor_rows
        if int(row["sampled_facilities"]) > 0
    }
    assert set(summary["sample"]["provinces"]) == set(audit.SAMPLE_PROVINCES)
    assert summary["sample"]["per_province_target"] == 10
    for row in factor_rows:
        province = row["province"]
        eligible = int(row["eligible_facilities"])
        sampled = int(row["sampled_facilities"])
        expected = min(audit.SAMPLE_PER_PROVINCE, eligible)
        assert sampled == expected, province
        assert summary["sample"]["per_province_counts"][province] == expected
        assert sampled == int(row["n_pairs"])
    assert summary["sample"]["per_province_counts"]["NT"] == 0
    assert summary["sample"]["per_province_counts"]["NU"] == 0
    assert len({row["pair_id"] for row in sample_rows}) == len(sample_rows)
    assert len({row["facility_id"] for row in sample_rows}) == len(sample_rows)


def test_sample_selection_is_reproducible(
    sample_rows: list[dict[str, str]],
) -> None:
    census = v1.load_facilities()
    centroids, _ = v1.load_province_centroids(v1.BOUNDARY_GEOJSON)
    selected = audit.select_sample(census, centroids)
    assert len(selected) == len(sample_rows)
    committed = {
        row["pair_id"]: (row["facility_id"], float(row["haversine_km"]))
        for row in sample_rows
    }
    for pair in selected:
        facility_id, distance = committed[pair.pair_id]
        assert pair.facility.facility_id == facility_id
        assert distance == pytest.approx(pair.haversine_km, abs=1e-5)


def test_sample_arithmetic(sample_rows: list[dict[str, str]]) -> None:
    for row in sample_rows:
        haversine = float(row["haversine_km"])
        recomputed = v1.haversine_km(
            float(row["origin_lat"]),
            float(row["origin_lon"]),
            float(row["facility_lat"]),
            float(row["facility_lon"]),
        )
        assert haversine == pytest.approx(recomputed, abs=1e-4)
        assert float(row["global_road_factor"]) == pytest.approx(
            audit.GLOBAL_ROAD_FACTOR
        )
        assert float(row["global_road_factor_km"]) == pytest.approx(
            haversine * audit.GLOBAL_ROAD_FACTOR, abs=1e-5
        )
        factor = float(row["province_road_factor"])
        assert float(row["province_road_factor_km"]) == pytest.approx(
            haversine * factor, abs=1e-5
        )
        if row["osrm_status"] != "ok":
            assert row["osrm_road_km"] == ""
            assert row["error"]
            continue
        road = float(row["osrm_road_km"])
        duration = float(row["osrm_duration_h"])
        assert road > 0.0 and duration > 0.0
        assert float(row["road_haversine_ratio"]) == pytest.approx(
            road / haversine, abs=1e-6
        )
        assert float(row["global_proxy_road_ratio"]) == pytest.approx(
            haversine * audit.GLOBAL_ROAD_FACTOR / road, abs=1e-6
        )
        assert float(row["province_proxy_road_ratio"]) == pytest.approx(
            haversine * factor / road, abs=1e-6
        )
        assert float(row["global_proxy_error_pct"]) == pytest.approx(
            100.0 * (haversine * audit.GLOBAL_ROAD_FACTOR - road) / road,
            abs=1e-5,
        )
        assert float(row["province_proxy_error_pct"]) == pytest.approx(
            100.0 * (haversine * factor - road) / road,
            abs=1e-5,
        )
        assert float(row["osrm_speed_kmh"]) == pytest.approx(
            road / duration, abs=1e-4
        )
        values = [haversine, road, duration, factor]
        assert all(math.isfinite(value) and value >= 0.0 for value in values)
        if row["outlier_flag"] == "true":
            assert row["ratio_used_in_fit"] == "false"
            assert row["outlier_reason"]
        else:
            assert row["ratio_used_in_fit"] == "true"
            assert row["outlier_reason"] == ""


def test_factor_arithmetic(
    sample_rows: list[dict[str, str]],
    factor_rows: list[dict[str, str]],
) -> None:
    for factor_row in factor_rows:
        province = factor_row["province"]
        fitted = [
            float(row["road_haversine_ratio"])
            for row in sample_rows
            if row["province"] == province
            and row["osrm_status"] == "ok"
            and row["ratio_used_in_fit"] == "true"
        ]
        n_ok = int(factor_row["n_ok"])
        n_outliers = int(factor_row["n_outliers"])
        assert int(factor_row["n_fit"]) == len(fitted)
        assert int(factor_row["n_fit"]) == n_ok - n_outliers
        if int(factor_row["eligible_facilities"]) == 0:
            assert factor_row["factor_applied"] == ""
            assert factor_row["fit_status"] == "no_eligible_facilities"
            assert factor_row["n_pairs"] == "0"
            continue
        assert fitted, province
        raw = float(factor_row["factor_raw"])
        applied = float(factor_row["factor_applied"])
        assert raw == pytest.approx(statistics.median(fitted), abs=1e-7)
        assert applied == pytest.approx(
            min(audit.MAX_FACTOR, max(audit.MIN_FACTOR, raw)), abs=1e-9
        )
        assert audit.MIN_FACTOR <= applied <= audit.MAX_FACTOR
        q25 = audit._percentile(fitted, 0.25)
        q75 = audit._percentile(fitted, 0.75)
        assert q25 is not None and q75 is not None
        assert float(factor_row["iqr"]) == pytest.approx(q75 - q25, abs=1e-6)
        low = float(factor_row["ci90_low"])
        high = float(factor_row["ci90_high"])
        assert math.isfinite(low) and math.isfinite(high)
        assert low <= high
        assert float(factor_row["factor_change_vs_global_pct"]) == pytest.approx(
            100.0
            * (applied - audit.GLOBAL_ROAD_FACTOR)
            / audit.GLOBAL_ROAD_FACTOR,
            abs=1e-6,
        )
        assert factor_row["flags"]
        assert factor_row["uncertainty_note"]


def test_labels_and_validation(summary: dict[str, Any]) -> None:
    assert summary["artifact"] == "facility_road_distance_audit_v2"
    assert summary["artifact_version"] == "2.0.0"
    assert summary["labels"] == {
        "evidence_label": "model_estimate",
        "observed": False,
        "policy_grade": False,
        "assignment_change": False,
        "national_claim": False,
    }
    assert summary["validation"]["all_passed"] is True
    assert all(summary["validation"]["checks"].values())
    assert summary["factors_fitted"] == 11
    assert summary["comparison"]["overall"]["n_ok"] == 81
    assert summary["comparison"]["overall"]["n_failed"] == 0
