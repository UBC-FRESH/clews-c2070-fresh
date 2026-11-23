"""Regression tests for the facility road-distance audit v1."""

from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import pytest

from workflows.national.facilities import (
    facility_road_distance_audit as audit,
)
from workflows.national.facilities.national_facility_assignment import (
    haversine_km,
)

OUTPUT_DIR = audit.OUTPUT_DIR
SAMPLE_CSV = OUTPUT_DIR / "facility_road_distance_sample.csv"
PROVINCE_CSV = OUTPUT_DIR / "province_road_distance_summary.csv"
SUMMARY_JSON = OUTPUT_DIR / "summary.json"
MANIFEST_JSON = OUTPUT_DIR / "manifest.json"
ROUTES_JSON = OUTPUT_DIR / "osrm_routes.json"
PROVENANCE_CSV = OUTPUT_DIR / "provenance.csv"
REPORT_MD = OUTPUT_DIR / "FACILITY_ROAD_DISTANCE_AUDIT_V1.md"


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


@pytest.fixture(scope="module")
def summary() -> dict[str, Any]:
    with SUMMARY_JSON.open("r", encoding="utf-8") as handle:
        return json.load(handle)


@pytest.fixture(scope="module")
def sample_rows() -> list[dict[str, str]]:
    return _read_csv(SAMPLE_CSV)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _walk_numbers(value: Any) -> list[float]:
    if isinstance(value, bool):
        return []
    if isinstance(value, (int, float)):
        return [float(value)]
    if isinstance(value, dict):
        numbers: list[float] = []
        for item in value.values():
            numbers.extend(_walk_numbers(item))
        return numbers
    if isinstance(value, list):
        numbers = []
        for item in value:
            numbers.extend(_walk_numbers(item))
        return numbers
    return []


def test_sample_counts_and_provinces(
    sample_rows: list[dict[str, str]],
) -> None:
    assert len(sample_rows) == 24
    assert {row["province"] for row in sample_rows} == set(audit.SAMPLE_PROVINCES)
    for province in audit.SAMPLE_PROVINCES:
        subset = [row for row in sample_rows if row["province"] == province]
        assert len(subset) == audit.SAMPLE_PER_PROVINCE
    assert len({row["pair_id"] for row in sample_rows}) == len(sample_rows)
    assert len({row["facility_id"] for row in sample_rows}) == len(sample_rows)


def test_sample_selection_is_reproducible(
    sample_rows: list[dict[str, str]],
) -> None:
    census = audit.load_facilities()
    centroids, _ = audit.load_province_centroids(audit.BOUNDARY_GEOJSON)
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


def test_haversine_and_ratio_arithmetic(
    sample_rows: list[dict[str, str]],
) -> None:
    for row in sample_rows:
        haversine = float(row["haversine_km"])
        recomputed = haversine_km(
            float(row["origin_lat"]),
            float(row["origin_lon"]),
            float(row["facility_lat"]),
            float(row["facility_lon"]),
        )
        assert haversine == pytest.approx(recomputed, abs=1e-4)
        assert float(row["haversine_road_factor_km"]) == pytest.approx(
            haversine * audit.ROAD_FACTOR, abs=1e-5
        )
        if row["osrm_status"] != "ok":
            assert row["osrm_road_km"] == ""
            assert row["error"]
            continue
        road = float(row["osrm_road_km"])
        duration = float(row["osrm_duration_h"])
        proxy = float(row["haversine_road_factor_km"])
        assert float(row["road_haversine_ratio"]) == pytest.approx(
            road / haversine, abs=1e-6
        )
        assert float(row["proxy_road_ratio"]) == pytest.approx(
            proxy / road, abs=1e-6
        )
        assert float(row["proxy_error_pct"]) == pytest.approx(
            100.0 * (proxy - road) / road, abs=1e-5
        )
        assert float(row["abs_proxy_error_pct"]) == pytest.approx(
            abs(100.0 * (proxy - road) / road), abs=1e-5
        )
        assert float(row["osrm_speed_kmh"]) == pytest.approx(
            road / duration, abs=1e-4
        )


def test_no_nan_or_negative(
    summary: dict[str, Any], sample_rows: list[dict[str, str]]
) -> None:
    numeric_fields = [
        "origin_lat",
        "origin_lon",
        "facility_lat",
        "facility_lon",
        "haversine_km",
        "haversine_road_factor_km",
    ]
    for row in sample_rows:
        for field in numeric_fields:
            value = float(row[field])
            assert math.isfinite(value)
        haversine = float(row["haversine_km"])
        assert haversine > 0.0
        if row["osrm_status"] != "ok":
            continue
        for field in (
            "osrm_road_km",
            "osrm_duration_h",
            "osrm_speed_kmh",
            "road_haversine_ratio",
            "proxy_road_ratio",
            "proxy_error_km",
            "proxy_error_pct",
            "abs_proxy_error_pct",
            "origin_snap_km",
            "destination_snap_km",
        ):
            value = float(row[field])
            assert math.isfinite(value)
        assert float(row["osrm_road_km"]) > 0.0
        assert float(row["osrm_duration_h"]) > 0.0
        assert float(row["origin_snap_km"]) >= 0.0
        assert float(row["destination_snap_km"]) >= 0.0
    for value in _walk_numbers(summary):
        assert math.isfinite(value)


def test_failures_and_counts(
    summary: dict[str, Any], sample_rows: list[dict[str, str]]
) -> None:
    ok_rows = [row for row in sample_rows if row["osrm_status"] == "ok"]
    failed_rows = [row for row in sample_rows if row["osrm_status"] != "ok"]
    assert len(ok_rows) + len(failed_rows) == len(sample_rows)
    assert summary["comparison"]["overall"]["n_ok"] == len(ok_rows)
    assert summary["comparison"]["overall"]["n_failed"] == len(failed_rows)
    assert summary["routing"]["requests_ok"] == len(ok_rows)
    assert summary["routing"]["requests_failed"] == len(failed_rows)
    assert summary["routing"]["requests_attempted"] == len(sample_rows)
    assert len(summary["routing"]["failures"]) == len(failed_rows)
    assert len(ok_rows) >= 1


def test_province_summary_arithmetic(
    summary: dict[str, Any], sample_rows: list[dict[str, str]]
) -> None:
    province_rows = _read_csv(PROVINCE_CSV)
    assert len(province_rows) == len(audit.SAMPLE_PROVINCES)
    for row in province_rows:
        subset = [sample for sample in sample_rows if sample["province"] == row["province"]]
        assert int(row["n_pairs"]) == len(subset) == audit.SAMPLE_PER_PROVINCE
        assert int(row["n_ok"]) == sum(
            1 for sample in subset if sample["osrm_status"] == "ok"
        )
        assert int(row["n_ok"]) + int(row["n_failed"]) == int(row["n_pairs"])
        haversine_total = math.fsum(float(sample["haversine_km"]) for sample in subset)
        road_total = math.fsum(
            float(sample["osrm_road_km"])
            for sample in subset
            if sample["osrm_status"] == "ok"
        )
        assert float(row["haversine_km_total"]) == pytest.approx(
            haversine_total, abs=1e-4
        )
        assert float(row["road_km_total"]) == pytest.approx(road_total, abs=1e-4)
        assert float(row["sample_fitted_road_factor"]) == pytest.approx(
            road_total / haversine_total, abs=1e-5
        )
    overall = summary["comparison"]["overall"]
    assert overall["n_ok"] == sum(
        1 for row in sample_rows if row["osrm_status"] == "ok"
    )
    assert overall["road_km_total"] == pytest.approx(
        math.fsum(
            float(row["osrm_road_km"])
            for row in sample_rows
            if row["osrm_status"] == "ok"
        ),
        abs=1e-4,
    )
    assert overall["sample_fitted_road_factor"] == pytest.approx(
        overall["road_km_total"] / overall["haversine_km_total"], abs=1e-9
    )


def test_provenance(
    summary: dict[str, Any], sample_rows: list[dict[str, str]]
) -> None:
    provenance = _read_csv(PROVENANCE_CSV)
    by_id = {row["source_id"]: row for row in provenance}
    assert by_id["national_facility_capacity_v3"]["sha256"] == _sha256(
        audit.CAPACITY_CSV
    )
    assert by_id["canada_adm1_geoboundaries_simplified"]["licence"] == "CC BY 4.0"
    assert by_id["canada_adm1_geoboundaries_simplified"]["sha256"] == _sha256(
        audit.BOUNDARY_GEOJSON
    )
    assert by_id["osrm_demo"]["url"].startswith(audit.OSRM_BASE_URL)
    assert by_id["osrm_demo"]["licence"] == "ODbL-1.0"
    assert "OpenStreetMap" in by_id["openstreetmap"]["name"]
    assert by_id["openstreetmap"]["licence"] == "ODbL-1.0"
    assert summary["routing"]["provider"] == "OSRM public demo server"
    assert summary["routing"]["licence"] == "ODbL-1.0"
    assert "OpenStreetMap" in summary["routing"]["attribution"]

    routes = json.loads(ROUTES_JSON.read_text(encoding="utf-8"))["routes"]
    assert len(routes) == len(sample_rows)
    for route in routes:
        assert route["request_url"].startswith(
            f"{audit.OSRM_BASE_URL}/route/v1/{audit.OSRM_PROFILE}/"
        )
        assert route["response_sha256"] == hashlib.sha256(
            route["response_raw"].encode("utf-8")
        ).hexdigest()
        if route["status"] == "ok":
            payload = json.loads(route["response_raw"])
            assert payload["code"] == "Ok"
            assert payload["routes"]


def test_manifest_hashes(summary: dict[str, Any]) -> None:
    manifest = json.loads(MANIFEST_JSON.read_text(encoding="utf-8"))
    assert manifest["artifact"] == audit.ARTIFACT
    assert manifest["labels"] == summary["labels"]
    assert manifest["validation"]["all_passed"] is True
    assert manifest["routing"]["requests_attempted"] == 24
    assert manifest["sample"]["n_pairs"] == 24
    expected_files = {
        path.name: path
        for path in OUTPUT_DIR.iterdir()
        if path.is_file() and path.name != "manifest.json"
    }
    assert set(manifest["outputs"]) == set(expected_files)
    for name, metadata in manifest["outputs"].items():
        path = expected_files[name]
        assert metadata["bytes"] == path.stat().st_size
        assert metadata["sha256"] == _sha256(path)
    for metadata in manifest["inputs"].values():
        path = audit.REPO_ROOT / metadata["path"]
        assert metadata["sha256"] == _sha256(path)


def test_labels_and_scope(summary: dict[str, Any]) -> None:
    assert summary["artifact"] == "facility_road_distance_audit_v1"
    assert summary["status"] in {"complete", "partial"}
    assert summary["labels"] == {
        "evidence_label": "model_estimate",
        "observed": False,
        "policy_grade": False,
        "assignment_change": False,
        "national_claim": False,
    }
    assert summary["validation"]["all_passed"] is True
    for caveat in summary["caveats"]:
        assert "national" not in caveat.lower() or "not" in caveat.lower()


def test_expected_facility_census() -> None:
    census = audit.load_facilities()
    assert census.total_records == 689
    assert census.mapped_records == 608
    assert census.unmapped_records == 81
    assert len(census.facilities) == census.mapped_records
    assert len({facility.facility_id for facility in census.facilities}) == 608


def test_deletion_gate_parses_both_columns() -> None:
    status_text = " M keep.csv\nD  deleted.csv\n D staged-deleted.csv\nA  added.csv\n"
    assert audit.parse_deletion_lines(status_text) == [
        "D  deleted.csv",
        " D staged-deleted.csv",
    ]


def test_deletion_gate_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(audit.RoadDistanceAuditError, match="deletion"):
        audit.assert_no_unexpected_deletions(
            [tmp_path],
            status_reader=lambda repository: " M file.csv\nD  gone.csv\n",
        )
    assert (
        audit.assert_no_unexpected_deletions(
            [tmp_path],
            status_reader=lambda repository: " M file.csv\n?? new.csv\n",
        )
        is None
    )


def test_repository_has_no_unexpected_deletions() -> None:
    audit.assert_no_unexpected_deletions()


def test_report_documents_source_and_limits() -> None:
    report = REPORT_MD.read_text(encoding="utf-8")
    assert "OSRM public demo server" in report
    assert "OpenStreetMap" in report
    assert "haversine x 1.3" in report
    assert "off-road connector" in report
    assert "No assignment change" in report
    assert "model_estimate" in report
