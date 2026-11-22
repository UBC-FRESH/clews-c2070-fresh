"""Regression tests for national facility capacity v3."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from workflows.national.facilities import (
    national_facility_capacity_v3 as capacity,
)

OUTPUT_DIR = capacity.OUTPUT_DIR
CAPACITY_CSV = OUTPUT_DIR / "national_facility_capacity_v3.csv"
CLASS_MEDIANS_CSV = OUTPUT_DIR / "class_medians.csv"
PROVINCE_CLASS_CSV = OUTPUT_DIR / "province_class_capacity.csv"
SOURCES_CSV = OUTPUT_DIR / "sources.csv"
SUMMARY_JSON = OUTPUT_DIR / "summary.json"
MANIFEST_JSON = OUTPUT_DIR / "manifest.json"
REPORT_MD = OUTPUT_DIR / "NATIONAL_FACILITY_CAPACITY_V3.md"
V2_CSV = capacity.CAPACITY_V2_CSV


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
    assert summary["artifact"] == "national_facility_capacity_v3"
    assert summary["artifact_version"] == "3.0.0"


def test_counts_match_and_conserve_records(rows, summary) -> None:
    assert len(rows) == 689
    assert len({row["facility_id"] for row in rows}) == len(rows)
    assert summary["counts"]["published_records"] == 433
    assert summary["counts"]["proxy_records"] == 241
    assert summary["counts"]["not_published_records"] == 15
    assert summary["match"] == {
        "qc_source_rows": 223,
        "qc_excluded_no_volume": 19,
        "qc_matched_existing": 49,
        "qc_new_records": 167,
        "company_source_rows": 5,
        "company_matched_existing": 2,
        "company_new_records": 3,
        "matched_total": 51,
        "new_total": 170,
    }
    assert len(rows) == 519 + 167 + 3
    assert summary["validation"]["record_conservation"] is True


def test_quebec_volumes_are_carried_exactly(rows, summary) -> None:
    evidence = summary["capacity"]["evidence"]["published_authorized_consumption"]
    assert evidence["records"] == 216
    assert evidence["capacity_m3_y"] == pytest.approx(64_086_333.0)
    qc = [
        row
        for row in rows
        if row["capacity_evidence"] == "published_authorized_consumption"
    ]
    assert len(qc) == 216
    assert all(row["province"] == "QC" for row in qc)
    assert all(float(row["capacity_m3_y"]) > 0.0 for row in qc)
    assert all(row["capacity_year"] == "2026" for row in qc)
    assert all(row["capacity_basis"] == "published_authorized_consumption_m3_y" for row in qc)


def test_company_volumes_are_carried_exactly(rows, summary) -> None:
    evidence = summary["capacity"]["evidence"]["published_sawn_output_converted"]
    assert evidence["records"] == 5
    assert evidence["capacity_m3_y"] == pytest.approx(2_237_280.0)
    company = [
        row
        for row in rows
        if row["capacity_evidence"] == "published_sawn_output_converted"
    ]
    assert len(company) == 5
    assert {row["province"] for row in company} == {"ON", "AB"}
    assert all(row["capacity_basis"].startswith("published_sawn_output") for row in company)


def test_existing_published_capacity_is_preserved(rows) -> None:
    v2_rows = {row["facility_id"]: row for row in _read_csv(V2_CSV)}
    for row in rows:
        old = v2_rows.get(row["facility_id"])
        if old is None or old["capacity_status"] != "published":
            continue
        assert row["capacity_status"] == "published"
        assert float(row["capacity_m3_y"]) == pytest.approx(
            float(old["capacity_m3_y"])
        )
        assert row["capacity_evidence"] == old["capacity_evidence"]
    assert set(v2_rows) <= {row["facility_id"] for row in rows}


def test_proxies_and_labels_are_valid(rows, summary) -> None:
    assert summary["validation"]["all_proxy_rows_have_class_median_evidence"] is True
    assert summary["validation"]["all_published_rows_have_accepted_evidence"] is True
    assert summary["validation"]["all_capacities_non_negative"] is True
    assert summary["validation"]["all_new_rows_are_published"] is True
    assert summary["labels"] == {
        "evidence_label": "model_estimate",
        "observed": False,
        "policy_grade": False,
    }
    new_rows = [row for row in rows if row["facility_id"].startswith("PUB26-")]
    assert len(new_rows) == 170
    assert all(row["capacity_status"] == "published" for row in new_rows)


def test_capacity_shares_reconcile(rows, summary) -> None:
    total = sum(float(row["capacity_m3_y"] or 0.0) for row in rows)
    published = sum(
        float(row["capacity_m3_y"] or 0.0)
        for row in rows
        if row["capacity_status"] == "published"
    )
    proxy = sum(
        float(row["capacity_m3_y"] or 0.0)
        for row in rows
        if row["capacity_status"] == "proxy"
    )
    assert published + proxy == pytest.approx(total)
    assert published == pytest.approx(
        summary["capacity"]["published_capacity_m3_y"]
    )
    assert proxy == pytest.approx(summary["capacity"]["proxy_capacity_m3_y"])
    assert summary["coverage"]["after"]["published_records"] == 433
    assert summary["coverage"]["before"]["published_records"] == 212
