"""Regression tests for the national facility capacity v1 artifact."""

from __future__ import annotations

import csv
import hashlib
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

import pytest

from workflows.national.facilities import (
    national_facility_capacity as capacity,
)

OUTPUT_DIR = capacity.OUTPUT_DIR
CAPACITY_CSV = OUTPUT_DIR / "national_facility_capacity_v1.csv"
CLASS_MEDIANS_CSV = OUTPUT_DIR / "class_medians.csv"
PROVINCE_CLASS_CSV = OUTPUT_DIR / "province_class_capacity.csv"
SOURCES_CSV = OUTPUT_DIR / "sources.csv"
SUMMARY_JSON = OUTPUT_DIR / "summary.json"
MANIFEST_JSON = OUTPUT_DIR / "manifest.json"
REPORT_MD = OUTPUT_DIR / "NATIONAL_FACILITY_CAPACITY_V1.md"


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


@pytest.fixture(scope="module")
def rows() -> list[dict[str, str]]:
    return _read_csv(CAPACITY_CSV)


@pytest.fixture(scope="module")
def class_medians() -> list[dict[str, str]]:
    return _read_csv(CLASS_MEDIANS_CSV)


@pytest.fixture(scope="module")
def province_class() -> list[dict[str, str]]:
    return _read_csv(PROVINCE_CLASS_CSV)


@pytest.fixture(scope="module")
def summary() -> dict:
    with SUMMARY_JSON.open("r", encoding="utf-8") as handle:
        return json.load(handle)


@pytest.fixture(scope="module")
def inventory() -> list[dict[str, str]]:
    return _read_csv(capacity.INVENTORY_CSV)


def test_outputs_exist_and_schema(
    rows, class_medians, province_class, summary
):
    for path in (
        CAPACITY_CSV,
        CLASS_MEDIANS_CSV,
        PROVINCE_CLASS_CSV,
        SOURCES_CSV,
        SUMMARY_JSON,
        MANIFEST_JSON,
        REPORT_MD,
    ):
        assert path.exists(), path
    assert rows
    assert class_medians
    assert province_class
    assert list(rows[0]) == capacity.CAPACITY_FIELDS
    assert list(class_medians[0]) == capacity.CLASS_MEDIAN_FIELDS
    assert list(province_class[0]) == capacity.PROVINCE_CLASS_FIELDS
    assert summary["artifact"] == capacity.ARTIFACT
    assert summary["artifact_version"] == capacity.ARTIFACT_VERSION


def test_one_row_per_inventory_record(rows, inventory):
    assert len(rows) == len(inventory)
    assert len({row["facility_id"] for row in rows}) == len(rows)
    assert {row["facility_id"] for row in rows} == {
        row["facility_id"] for row in inventory
    }


def test_published_values_are_carried_unchanged(rows, inventory):
    inventory_by_id = {row["facility_id"]: row for row in inventory}
    published = [row for row in rows if row["capacity_status"] == "published"]
    assert len(published) == 179
    for row in published:
        source = inventory_by_id[row["facility_id"]]
        assert source["capacity_status"] == "published"
        assert float(row["capacity_m3_y"]) == pytest.approx(
            float(source["capacity_m3_y"]), abs=1e-6
        )
        assert row["capacity_evidence"] == "published_mills_db_2015_infeed_proxy"
        assert row["proxy_low_m3_y"] == ""
        assert row["proxy_high_m3_y"] == ""
        assert row["source_id"] == source["source_id"]
        assert row["licence"] == source["licence"]
        assert float(row["capacity_m3_y"]) > 0.0


def test_proxy_matches_bc_class_median(rows, inventory, class_medians):
    anchor_values: dict[str, list[float]] = defaultdict(list)
    for row in inventory:
        if (
            row["province"] == "BC"
            and row["capacity_status"] == "published"
            and row["facility_class"] in capacity.RELEVANT_CLASSES
        ):
            anchor_values[row["facility_class"]].append(
                float(row["capacity_m3_y"])
            )

    medians = {
        row["facility_class"]: float(row["median_m3_y"])
        for row in class_medians
    }
    for facility_class, values in anchor_values.items():
        assert medians[facility_class] == pytest.approx(
            round(capacity._quantile(sorted(values), 0.50)), abs=1e-6
        )

    proxies = [row for row in rows if row["capacity_status"] == "proxy"]
    assert len(proxies) == 292
    for row in proxies:
        facility_class = row["facility_class"]
        anchor = medians[facility_class]
        low = float(row["proxy_low_m3_y"])
        high = float(row["proxy_high_m3_y"])
        assert float(row["capacity_m3_y"]) == pytest.approx(anchor, abs=1e-6)
        assert 0.0 < low <= anchor <= high
        assert row["capacity_evidence"] == "class_median_proxy"
        assert row["anchor_class"] == facility_class
        assert int(row["anchor_records"]) == len(anchor_values[facility_class])
        assert row["province_scale"] == "1"
        assert "class-median proxy" in row["notes"].lower()


def test_proxy_ranges_are_positive_and_ordered(class_medians):
    assert {row["facility_class"] for row in class_medians} == set(
        capacity.RELEVANT_CLASSES
    )
    for row in class_medians:
        low = float(row["p25_m3_y"])
        median = float(row["median_m3_y"])
        high = float(row["p75_m3_y"])
        assert 0.0 < low < median < high
        assert float(row["min_m3_y"]) <= low
        assert high <= float(row["max_m3_y"])
        assert int(row["anchor_records"]) >= capacity.MIN_ANCHOR_RECORDS
        assert row["anchor_province"] == "BC"
        assert row["quantile_method"] == capacity.ANCHOR_QUANTILE_METHOD


def test_no_relevant_record_is_left_without_capacity(rows, summary):
    missing = [
        row
        for row in rows
        if row["facility_class"] in capacity.RELEVANT_CLASSES
        and row["capacity_status"] == "not_published"
    ]
    assert missing == []
    unavailable = [
        row for row in rows if row["capacity_status"] == "not_published"
    ]
    assert len(unavailable) == 15
    assert all(row["facility_class"] == "OTHER" for row in unavailable)
    assert all(row["capacity_m3_y"] == "" for row in unavailable)
    assert all(
        row["capacity_evidence"] == "unavailable_no_class_anchor"
        for row in unavailable
    )
    assert summary["validation"]["relevant_records_without_capacity"] == 0


def test_no_unbounded_or_fabricated_capacity(rows, summary):
    for row in rows:
        if row["capacity_status"] in {"published", "proxy"}:
            value = float(row["capacity_m3_y"])
            assert value > 0.0
            assert math.isfinite(value)
            assert value < 1e9
    assert summary["validation"]["proxy_range_violations"] == 0
    assert summary["validation"]["unique_facility_ids"] is True
    assert summary["validation"]["records_match_inventory"] is True
    assert summary["validation"]["anchor_minimum_met"] is True
    assert summary["capacity"]["proxy_share_of_total"] == pytest.approx(
        summary["capacity"]["proxy_capacity_m3_y"]
        / summary["capacity"]["total_capacity_m3_y"],
        abs=1e-12,
    )


def test_province_class_totals_reconcile(rows, province_class, summary):
    rows_by_key: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        rows_by_key[(row["province"], row["facility_class"])].append(row)
    assert len(province_class) == len(rows_by_key)
    for row in province_class:
        group = rows_by_key[(row["province"], row["facility_class"])]
        published = [
            item for item in group if item["capacity_status"] == "published"
        ]
        proxies = [
            item for item in group if item["capacity_status"] == "proxy"
        ]
        assert int(row["records"]) == len(group)
        assert int(row["published_records"]) == len(published)
        assert int(row["proxy_records"]) == len(proxies)
        assert float(row["published_capacity_m3_y"]) == pytest.approx(
            sum(float(item["capacity_m3_y"]) for item in published), abs=1e-6
        )
        assert float(row["proxy_capacity_m3_y"]) == pytest.approx(
            sum(float(item["capacity_m3_y"]) for item in proxies), abs=1e-6
        )
        assert float(row["total_capacity_m3_y"]) == pytest.approx(
            float(row["published_capacity_m3_y"])
            + float(row["proxy_capacity_m3_y"]),
            abs=1e-6,
        )
    assert sum(
        float(row["total_capacity_m3_y"]) for row in province_class
    ) == pytest.approx(summary["capacity"]["total_capacity_m3_y"], abs=1e-6)


def test_labels_and_report(summary):
    assert summary["labels"] == {
        "evidence_label": "model_estimate",
        "observed": False,
        "policy_grade": False,
    }
    text = " ".join(summary["caveats"])
    assert "legal/AAC" in text
    assert "class-median" in text.lower()
    assert "No UI or public OSeCAN" in text
    report = REPORT_MD.read_text(encoding="utf-8")
    for phrase in (
        "Published versus proxy capacity",
        "BC anchor distribution",
        "Province and class totals",
        "Public sources screened",
        "model_estimate",
        "observed=false",
        "policy_grade=false",
        "legal/AAC",
    ):
        assert phrase in report


def test_public_sources_record_licence_and_use():
    sources = _read_csv(SOURCES_CSV)
    assert sources
    by_id = {row["source_id"]: row for row in sources}
    assert capacity.BC_SOURCE_ID in by_id
    assert capacity.PROXY_SOURCE_ID in by_id
    pellet = by_id["canadian_biomass_pellet_map_2025"]
    assert pellet["used_for_capacity"] == "no"
    assert pellet["licence"]
    assert "tonnes" in pellet["notes"].lower()
    assert by_id["fpac_member_mills"]["access_status"] == "http_404"
    for row in sources:
        assert row["source_id"]
        assert row["licence"]
        assert row["access_status"]
        assert row["used_for_capacity"] in {"yes_published_anchor", "yes_proxy", "no"}


def test_manifest_hashes_and_inputs(summary):
    with MANIFEST_JSON.open("r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    assert manifest["artifact"] == capacity.ARTIFACT
    assert manifest["artifact_version"] == capacity.ARTIFACT_VERSION
    for name, metadata in manifest["outputs"].items():
        path = OUTPUT_DIR / name
        assert path.exists(), name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == metadata["sha256"]
        assert path.stat().st_size == metadata["bytes"]
    assert manifest["inputs"] == summary["inputs"]
    assert any(
        "national_facilities.csv" in name for name in manifest["inputs"]
    )


def test_anchor_distribution_is_bc_only(class_medians, summary):
    for row in class_medians:
        assert row["anchor_province"] == "BC"
    for anchor in summary["anchors"].values():
        assert anchor["anchor_province"] == "BC"
        assert anchor["count"] >= capacity.MIN_ANCHOR_RECORDS
        assert anchor["p25_m3_y"] < anchor["median_m3_y"] < anchor["p75_m3_y"]


def test_counts_by_status_and_class(rows, summary):
    counts = Counter(row["capacity_status"] for row in rows)
    assert counts["published"] == summary["counts"]["published_records"]
    assert counts["proxy"] == summary["counts"]["proxy_records"]
    assert counts["not_published"] == summary["counts"]["not_published_records"]
    assert counts["published"] + counts["proxy"] + counts["not_published"] == len(
        rows
    )
