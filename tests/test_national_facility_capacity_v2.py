"""Regression tests for national facility capacity v2."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from workflows.national.facilities import (
    national_facility_capacity_v2 as capacity,
)

OUTPUT_DIR = capacity.OUTPUT_DIR
CAPACITY_CSV = OUTPUT_DIR / "national_facility_capacity_v2.csv"
CLASS_MEDIANS_CSV = OUTPUT_DIR / "class_medians.csv"
PROVINCE_CLASS_CSV = OUTPUT_DIR / "province_class_capacity.csv"
SOURCES_CSV = OUTPUT_DIR / "sources.csv"
SUMMARY_JSON = OUTPUT_DIR / "summary.json"
MANIFEST_JSON = OUTPUT_DIR / "manifest.json"
REPORT_MD = OUTPUT_DIR / "NATIONAL_FACILITY_CAPACITY_V2.md"


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


@pytest.fixture(scope="module")
def rows() -> list[dict[str, str]]:
    return _read_csv(CAPACITY_CSV)


@pytest.fixture(scope="module")
def summary() -> dict:
    with SUMMARY_JSON.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def test_outputs_exist_and_schema(rows, summary) -> None:
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
    assert list(rows[0]) == capacity.CAPACITY_FIELDS
    assert summary["artifact"] == "national_facility_capacity_v2"
    assert summary["artifact_version"] == "2.0.0"


def test_counts_and_evidence_mix(rows, summary) -> None:
    assert len(rows) == 519
    assert len({row["facility_id"] for row in rows}) == len(rows)
    assert summary["counts"]["published_records"] == 212
    assert summary["counts"]["proxy_records"] == 292
    assert summary["counts"]["not_published_records"] == 15
    evidence = summary["capacity"]["evidence"]
    assert evidence["published_infeed_proxy_records"] == 179
    assert evidence["published_converted_records"] == 33
    assert evidence["proxy_records"] == 292
    assert evidence["not_published_records"] == 15


def test_published_capacity_matches_inventory(rows) -> None:
    inventory_rows = _read_csv(capacity.INVENTORY_CSV)
    inventory_published = sum(
        float(row["capacity_m3_y"])
        for row in inventory_rows
        if row["capacity_status"] == "published"
    )
    capacity_published = sum(
        float(row["capacity_m3_y"])
        for row in rows
        if row["capacity_status"] == "published"
    )
    assert capacity_published == pytest.approx(inventory_published)


def test_converted_capacity_bounds_and_basis(rows) -> None:
    converted = [
        row
        for row in rows
        if row["capacity_evidence"] == "published_capacity_converted"
    ]
    assert len(converted) == 33
    for row in converted:
        value = float(row["capacity_m3_y"])
        assert value > 0.0
        assert row["capacity_basis"] == (
            "published_finished_pellets_converted_650kg_m3"
        )
        assert float(row["conversion_density_t_per_m3"]) == 0.65
        assert "Not infeed-equivalent" in row["notes"]


def test_proxy_capacity_bounds(rows) -> None:
    proxies = [row for row in rows if row["capacity_status"] == "proxy"]
    assert len(proxies) == 292
    for row in proxies:
        value = float(row["capacity_m3_y"])
        low = float(row["proxy_low_m3_y"])
        high = float(row["proxy_high_m3_y"])
        assert 0.0 < low <= value <= high
        assert row["capacity_evidence"] == "class_median_proxy"
        assert row["capacity_basis"] == "class_median_proxy_bc_anchor"


def test_pellet_anchor_not_contaminated_by_converted_values() -> None:
    medians = {
        row["facility_class"]: row for row in _read_csv(CLASS_MEDIANS_CSV)
    }
    pellet = medians["PELLET"]
    assert pellet["anchor_records"] == "13"
    assert float(pellet["median_m3_y"]) == 574_000
    assert pellet["proxy_applied_records"] == "1"
    assert float(pellet["proxy_capacity_m3_y"]) == 574_000
    assert "converted pellet volumes are excluded" in pellet["notes"]


def test_coverage_improves_published_share(summary) -> None:
    coverage = summary["coverage"]
    before = coverage["before"]
    after = coverage["after"]
    assert before["published_records"] == 179
    assert after["published_records"] == 212
    assert before["proxy_records"] == 292
    assert after["proxy_records"] == 292
    assert after["published_share_capacity"] > before["published_share_capacity"]
    assert after["proxy_share_capacity"] < before["proxy_share_capacity"]


def test_no_relevant_record_without_capacity(summary) -> None:
    assert summary["validation"]["relevant_records_without_capacity"] == 0
    assert summary["validation"]["proxy_range_violations"] == 0
    assert summary["validation"]["unique_facility_ids"] is True
    assert summary["validation"]["all_published_rows_have_evidence"] is True
    assert summary["validation"]["converted_rows_use_documented_density"] is True


def test_sources_and_no_restricted_data(summary) -> None:
    sources = _read_csv(SOURCES_CSV)
    ids = {row["source_id"] for row in sources}
    assert "canadian_biomass_pellet_map_2025" in ids
    assert "nrcan_graded_wood_pellets_bulk_density" in ids
    assert summary["validation"]["no_restricted_sources"] is True
    for row in sources:
        text = (row["licence"] + " " + row["notes"]).lower()
        assert "proprietary" not in text
        assert "confidential" not in text
        assert "restricted" not in text


def test_class_totals_reconcile_with_rows(rows) -> None:
    province_class = _read_csv(PROVINCE_CLASS_CSV)
    for province in {row["province"] for row in rows}:
        group = [row for row in rows if row["province"] == province]
        expected = sum(
            float(row["capacity_m3_y"])
            for row in group
            if row["capacity_status"] in {"published", "proxy"}
        )
        observed = sum(
            float(row["total_capacity_m3_y"])
            for row in province_class
            if row["province"] == province
        )
        assert observed == pytest.approx(expected)


def test_labels_and_caveats(summary) -> None:
    assert summary["labels"] == {
        "evidence_label": "model_estimate",
        "observed": False,
        "policy_grade": False,
    }
    caveats = " ".join(summary["caveats"]).lower()
    assert "legal/aac" in caveats or "legal" in caveats
    assert "no ui or public osecan" in caveats
