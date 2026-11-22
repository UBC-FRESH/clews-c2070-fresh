"""Regression tests for national facility inventory v1."""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

import pytest

from workflows.national.facilities import (
    national_facility_inventory_v1 as inventory,
)
from workflows.national.facilities import (
    pellet_map_2025,
)

OUTPUT_DIR = inventory.OUTPUT_DIR
INVENTORY_CSV = OUTPUT_DIR / "national_facilities.csv"
CROSSWALK_CSV = OUTPUT_DIR / "dedup_crosswalk.csv"
COVERAGE_CSV = OUTPUT_DIR / "province_coverage.csv"
SOURCES_CSV = OUTPUT_DIR / "sources.csv"
SUMMARY_JSON = OUTPUT_DIR / "summary.json"
MANIFEST_JSON = OUTPUT_DIR / "manifest.json"
REPORT_MD = OUTPUT_DIR / "NATIONAL_FACILITY_INVENTORY_V1.md"


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


@pytest.fixture(scope="module")
def rows() -> list[dict[str, str]]:
    return _read_csv(INVENTORY_CSV)


@pytest.fixture(scope="module")
def crosswalk() -> list[dict[str, str]]:
    return _read_csv(CROSSWALK_CSV)


@pytest.fixture(scope="module")
def summary() -> dict:
    with SUMMARY_JSON.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def test_outputs_exist_and_schema(rows, crosswalk, summary) -> None:
    for path in (
        INVENTORY_CSV,
        CROSSWALK_CSV,
        COVERAGE_CSV,
        SOURCES_CSV,
        SUMMARY_JSON,
        MANIFEST_JSON,
        REPORT_MD,
    ):
        assert path.exists(), path
    assert list(rows[0]) == list(inventory._read_csv(inventory.INVENTORY_V0_CSV)[0]) + inventory.V1_EXTRA_FIELDS
    assert list(crosswalk[0]) == inventory.DEDUP_FIELDS
    assert summary["artifact"] == "national_facility_inventory_v1"


def test_counts_and_unique_ids(rows, summary) -> None:
    assert len(rows) == 519
    assert len({row["facility_id"] for row in rows}) == len(rows)
    assert summary["counts"]["records_total"] == 519
    assert summary["counts"]["base_records"] == 486
    assert summary["counts"]["new_records"] == 33
    assert summary["counts"]["published_records"] == 212
    assert summary["counts"]["matched_existing_records"] == 10
    counts = Counter(row["province"] for row in rows)
    assert counts["BC"] == 276
    assert counts["AB"] == 20
    assert counts["QC"] == 121


def test_pellet_rows_and_crosswalk_accounting(rows, crosswalk, summary) -> None:
    assert len(crosswalk) == 43
    assert [int(row["map_row"]) for row in crosswalk] == list(range(1, 44))
    assert summary["dedupe"]["new_pellet"] == 33
    assert summary["dedupe"]["matched_existing_pellet"] == 10
    assert summary["dedupe"]["preserved_existing_published"] == 9
    assert summary["dedupe"]["upgraded_existing_proxy"] == 1
    new_ids = {
        row["matched_facility_id"]
        for row in crosswalk
        if row["match_status"] == "new_pellet"
    }
    assert len(new_ids) == 33
    assert all(facility_id.startswith("CBM25-") for facility_id in new_ids)
    pellet_rows = [row for row in rows if row["facility_class"] == "PELLET"]
    assert len(pellet_rows) == 47


def test_dedupe_no_duplicate_pellet_identity(rows, summary) -> None:
    seen: dict[tuple[str, str], str] = {}
    for row in rows:
        if row["facility_class"] != "PELLET":
            continue
        key = (
            inventory._normalise_name(row["name"]),
            row["province"],
        )
        assert key not in seen, (row["name"], seen.get(key))
        seen[key] = row["name"]
    assert summary["dedupe"]["duplicate_normalised_pellet_names"] == {}


def test_published_capacity_preserved_and_upgraded(rows, crosswalk) -> None:
    v0 = {
        row["facility_id"]: row
        for row in _read_csv(inventory.INVENTORY_V0_CSV)
    }
    by_id = {row["facility_id"]: row for row in rows}
    for item in crosswalk:
        if item["action"] != "preserve_existing_published_capacity":
            continue
        facility_id = item["matched_facility_id"]
        assert by_id[facility_id]["capacity_m3_y"] == v0[facility_id]["capacity_m3_y"]
        assert by_id[facility_id]["capacity_status"] == "published"
    gds = by_id["OSM-QC-N10743724326"]
    assert gds["capacity_status"] == "published"
    assert gds["capacity_evidence"] == "published_capacity_converted"
    assert float(gds["capacity_m3_y"]) == pytest.approx(100_000 / 0.65)


def test_new_converted_capacities_match_density(rows, crosswalk) -> None:
    by_id = {row["facility_id"]: row for row in rows}
    for item in crosswalk:
        if item["match_status"] != "new_pellet":
            continue
        row = by_id[item["matched_facility_id"]]
        tonnes = item["capacity_tonnes_y"]
        if not tonnes:
            assert row["capacity_m3_y"] == ""
            assert row["capacity_status"] == "not_published"
            continue
        assert row["capacity_status"] == "published"
        assert row["capacity_evidence"] == "published_capacity_converted"
        assert float(row["capacity_m3_y"]) == pytest.approx(
            float(tonnes) / pellet_map_2025.DENSITY_T_PER_M3
        )


def test_skeena_temporarily_closed_without_capacity(rows) -> None:
    row = next(
        item
        for item in rows
        if item["facility_id"] == "CBM25-BC-11"
    )
    assert row["status"] == "temporarily_closed"
    assert row["capacity_status"] == "not_published"
    assert row["capacity_m3_y"] == ""
    assert row["location_precision"] == "co_located_facility"
    assert "Skeena Sawmills" in row["notes"]


def test_co_located_locations_use_existing_facility(rows) -> None:
    by_id = {row["facility_id"]: row for row in rows}
    pairs = {
        "CBM25-AB-15": "OSM-AB-W334796375",
        "CBM25-ON-20": "OSM-ON-W299165594",
        "CBM25-NB-36": "OSM-NB-W499738346",
        "CBM25-NS-42": "OSM-NS-R15024787",
    }
    for new_id, existing_id in pairs.items():
        new = by_id[new_id]
        existing = by_id[existing_id]
        assert new["lat"] == existing["lat"]
        assert new["lon"] == existing["lon"]
        assert new["location_precision"] == "co_located_facility"


def test_sources_and_no_restricted_data(summary) -> None:
    sources = _read_csv(SOURCES_CSV)
    ids = {row["source_id"] for row in sources}
    assert pellet_map_2025.SOURCE_ID in ids
    assert pellet_map_2025.DENSITY_SOURCE_ID in ids
    assert "alberta_forest_products_members" in ids
    assert "ofia_members" in ids
    assert summary["validation"]["no_restricted_sources"] is True
    for row in sources:
        text = (row["licence"] + " " + row["notes"]).lower()
        assert "proprietary" not in text
        assert "confidential" not in text
        assert "restricted" not in text


def test_coverage_rows_sum_to_inventory(rows) -> None:
    coverage = _read_csv(COVERAGE_CSV)
    assert sum(int(row["records"]) for row in coverage) == len(rows)
    assert sum(int(row["published_records"]) for row in coverage) == sum(
        1 for row in rows if row["capacity_status"] == "published"
    )
    assert sum(int(row["pellet_new_records"]) for row in coverage) == 33


def test_conversion_basis_is_preserved(summary) -> None:
    assert summary["method"]["conversion"] == (
        "capacity_m3_y = capacity_tonnes_y / 0.65"
    )
    assert "650 kg/m3" in summary["method"]["conversion_basis"]
    assert "no pellet yield" in summary["method"]["conversion_basis"]
    assert summary["labels"] == {
        "evidence_label": "model_estimate",
        "observed": False,
        "policy_grade": False,
    }
