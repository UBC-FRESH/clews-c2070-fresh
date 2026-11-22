"""Regression tests for the public AB/ON/QC mill-capacity extracts."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from workflows.national.facilities import (
    national_facility_capacity_v3 as capacity,
)

EXTRACT_DIR = capacity.EXTRACT_DIR
QC_CSV = capacity.QC_CSV
ON_CSV = capacity.ON_MILLS_CSV
COMPANY_CSV = capacity.COMPANY_CSV
SOURCES_JSON = capacity.EXTRACT_SOURCES


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


@pytest.fixture(scope="module")
def qc_rows() -> list[dict[str, str]]:
    return _read_csv(QC_CSV)


def test_qc_extract_scope_and_identity(qc_rows) -> None:
    assert len(qc_rows) == 242
    assert len({row["nosequsi"] for row in qc_rows}) == 242
    assert len({row["nousi"] for row in qc_rows}) == 242
    assert all(float(row["latitude"]) >= 44.0 for row in qc_rows)
    assert all(float(row["longitude"]) <= -57.0 for row in qc_rows)
    zero = [
        row
        for row in qc_rows
        if float(row["volresper"] or 0.0) + float(row["volfeuper"] or 0.0) <= 0
    ]
    assert len(zero) == 19
    assert all("catégorie 8" in row["produits"] for row in zero)


def test_qc_volume_and_class_mapping(qc_rows) -> None:
    total = sum(
        float(row["volresper"] or 0.0) + float(row["volfeuper"] or 0.0)
        for row in qc_rows
    )
    assert total == pytest.approx(64_274_434.0)
    relevant = [
        row
        for row in qc_rows
        if capacity._queclass(row) != "OTHER"
        and float(row["volresper"] or 0.0) + float(row["volfeuper"] or 0.0)
        > 0
    ]
    assert len(relevant) == 216
    assert sum(
        float(row["volresper"] or 0.0) + float(row["volfeuper"] or 0.0)
        for row in relevant
    ) == pytest.approx(64_086_333.0)
    classes = {capacity._queclass(row) for row in qc_rows}
    assert classes <= {"SAW", "PULP", "PANEL", "PELLET", "BIO", "OTHER"}


def test_ontario_directory_scope() -> None:
    rows = _read_csv(ON_CSV)
    assert len(rows) == 94
    assert all(row["province"] == "ON" for row in rows)
    assert len({row["facility_code"] for row in rows}) == 94
    assert "capacity_m3_y" not in rows[0]


def test_company_disclosure_conversion() -> None:
    rows = _read_csv(COMPANY_CSV)
    assert len(rows) == 5
    assert {row["province"] for row in rows} == {"ON", "AB"}
    for row in rows:
        assert float(row["capacity_m3_y"]) == pytest.approx(
            float(row["capacity_value"]) * 2360.0
        )
        assert row["capacity_metric"] == "sawn_lumber_output"
    assert sum(float(row["capacity_m3_y"]) for row in rows) == pytest.approx(
        2_237_280.0
    )


def test_sources_are_public_and_labelled() -> None:
    sources = json.loads(SOURCES_JSON.read_text(encoding="utf-8"))
    by_id = {source["source_id"]: source for source in sources}
    assert "CC BY 4.0" in by_id["qc_mrnf_usines_2026"]["licence"]
    assert "Open Government Licence" in by_id["on_mnrf_mills_2026"]["licence"]
    assert by_id["on_mnrf_mills_2026"]["used_for_capacity"].startswith("no")
    assert EXTRACT_DIR.name == "public_mill_capacity_2026"
