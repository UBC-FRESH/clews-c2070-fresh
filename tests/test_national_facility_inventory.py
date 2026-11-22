"""Regression tests for the national facility inventory v0 artifact."""

from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

import pytest

from workflows.national.facilities import national_facility_inventory as inventory

OUTPUT_DIR = inventory.OUTPUT_DIR
FACILITIES_CSV = OUTPUT_DIR / "national_facilities.csv"
COVERAGE_CSV = OUTPUT_DIR / "province_coverage.csv"
SOURCES_CSV = OUTPUT_DIR / "sources.csv"
CROSSWALK_CSV = OUTPUT_DIR / "dedup_crosswalk.csv"
SUMMARY_JSON = OUTPUT_DIR / "summary.json"
MANIFEST_JSON = OUTPUT_DIR / "manifest.json"
REPORT_MD = OUTPUT_DIR / "NATIONAL_FACILITY_INVENTORY_V0.md"

EXPECTED_FIELDS = [
    "facility_id",
    "name",
    "facility_class",
    "province",
    "lat",
    "lon",
    "capacity_m3_y",
    "capacity_status",
    "status",
    "source_id",
    "source_url",
    "licence",
    "evidence_tier",
    "notes",
]
OSM_ID_RE = re.compile(r"^OSM-([A-Z]{2})-[NWR]\d+$")


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


@pytest.fixture(scope="module")
def facilities() -> list[dict[str, str]]:
    return _read_csv(FACILITIES_CSV)


@pytest.fixture(scope="module")
def coverage() -> list[dict[str, str]]:
    return _read_csv(COVERAGE_CSV)


@pytest.fixture(scope="module")
def summary() -> dict:
    with SUMMARY_JSON.open("r", encoding="utf-8") as handle:
        return json.load(handle)


@pytest.fixture(scope="module")
def boundaries():
    return inventory.load_boundaries()


def test_schema_and_unique_ids(facilities):
    assert facilities
    assert list(facilities[0]) == EXPECTED_FIELDS
    ids = [row["facility_id"] for row in facilities]
    assert len(ids) == len(set(ids))
    assert all(row["facility_id"].strip() for row in facilities)


def test_all_thirteen_jurisdictions_are_explicit(coverage, facilities, summary):
    assert len(coverage) == 13
    assert [row["province"] for row in coverage] == list(inventory.PROVINCES)
    assert summary["jurisdictions_expected"] == 13
    assert summary["records_total"] == len(facilities)
    counts = Counter(row["province"] for row in facilities)
    for row in coverage:
        assert row["province_name"] == inventory.PROVINCES[row["province"]]
        assert int(row["records"]) == counts.get(row["province"], 0)
        assert int(row["capacity_known_count"]) + int(
            row["capacity_unknown_count"]
        ) == int(row["records"])
        assert int(row["coordinate_count"]) + int(
            row["coordinate_missing_count"]
        ) == int(row["records"])


def test_class_and_source_domains(facilities):
    assert {row["facility_class"] for row in facilities} <= set(inventory.CLASS_ORDER)
    assert {row["source_id"] for row in facilities} == {
        inventory.BC_SOURCE_ID,
        inventory.OSM_SOURCE_ID,
    }
    assert all(row["evidence_tier"] in {"A", "B", "C", "D"} for row in facilities)


def test_no_invented_capacity(facilities):
    known = [row for row in facilities if row["capacity_m3_y"]]
    assert known
    assert all(row["source_id"] == inventory.BC_SOURCE_ID for row in known)
    assert all(row["capacity_status"] == "published" for row in known)
    assert all(float(row["capacity_m3_y"]) >= 0 for row in known)
    for row in facilities:
        if row["capacity_m3_y"]:
            continue
        assert row["capacity_status"] == "not_published"
        assert row["capacity_m3_y"] == ""
    assert not any(row["capacity_m3_y"] == "0" for row in facilities)


def test_osm_ids_and_licences_are_preserved(facilities):
    sources = {row["source_id"]: row for row in _read_csv(SOURCES_CSV)}
    assert inventory.BC_SOURCE_ID in sources
    assert inventory.OSM_SOURCE_ID in sources
    for row in facilities:
        assert row["source_id"] in sources
        assert row["source_url"].startswith("http")
        assert row["licence"].strip()
        if row["source_id"] != inventory.OSM_SOURCE_ID:
            continue
        match = OSM_ID_RE.match(row["facility_id"])
        assert match, row["facility_id"]
        assert match.group(1) == row["province"]
        assert row["capacity_m3_y"] == ""
        assert row["capacity_status"] == "not_published"
        assert row["licence"] == "ODbL-1.0"
        assert f"/{row['facility_id'].split('-')[-1][1:]}" in row["source_url"]


def test_coordinates_validate_inside_named_jurisdiction(facilities, boundaries):
    for row in facilities:
        if not row["lat"]:
            assert row["lon"] == ""
            continue
        assert row["lon"]
        lat, lon = float(row["lat"]), float(row["lon"])
        assert 41.0 <= lat <= 84.0
        assert -142.0 <= lon <= -52.0
        allow_buffer = row["source_id"] == inventory.BC_SOURCE_ID
        province, _ = inventory.province_for_point(
            lat, lon, boundaries, allow_buffer=allow_buffer
        )
        assert province == row["province"], row["facility_id"]
    for row in facilities:
        if row["source_id"] == inventory.OSM_SOURCE_ID:
            assert row["lat"] and row["lon"]


def test_dedup_crosswalk_preserves_both_ids(facilities):
    crosswalk = _read_csv(CROSSWALK_CSV)
    assert crosswalk
    by_id = {row["facility_id"]: row for row in facilities}
    for row in crosswalk:
        bc = by_id[row["bc_facility_id"]]
        assert f"dedup_osm={row['osm_type']}/{row['osm_id']}" in bc["notes"]
        assert row["osm_facility_id"] not in by_id
        assert float(row["distance_km"]) >= 0


def test_coverage_class_string_totals_match_rows(coverage):
    for row in coverage:
        if row["classes"] == "none":
            assert int(row["records"]) == 0
            continue
        total = 0
        for part in row["classes"].split(";"):
            facility_class, count = part.split(":")
            assert facility_class in inventory.CLASS_ORDER
            total += int(count)
        assert total == int(row["records"]), row["province"]


def test_summary_and_manifest_are_consistent(facilities, summary):
    assert summary["records_total"] == len(facilities)
    assert sum(summary["records_by_province"].values()) == len(facilities)
    assert sum(summary["records_by_class"].values()) == len(facilities)
    assert summary["capacity_known_count"] == sum(
        bool(row["capacity_m3_y"]) for row in facilities
    )
    assert summary["dedup"]["osm_merged_into_bc"] == len(_read_csv(CROSSWALK_CSV))
    with MANIFEST_JSON.open("r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    for name, metadata in manifest["outputs"].items():
        path = OUTPUT_DIR / name
        assert path.exists(), name
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert digest == metadata["sha256"], name
        assert path.stat().st_size == metadata["bytes"], name


def test_report_exists_and_covers_all_jurisdictions(coverage):
    text = REPORT_MD.read_text(encoding="utf-8")
    assert "National Facility Inventory v0" in text
    for row in coverage:
        assert f"{row['province']} ({row['province_name']})" in text
