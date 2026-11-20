"""Tests for the BC BCTS sale schedule, stumpage parsers, and ledger sample."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from workflows.national.harvest import bcts_bc
from workflows.national.harvest.cost_revenue import (
    Component,
    Perspective,
    load_ledger,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DERIVED = REPO_ROOT / "data/derived/national/harvest_basis/bcts_bc"
RAW = REPO_ROOT / "data/raw/forest_cost_revenue/bcts_bc"


def test_species_composition_handles_omitted_leading_zero() -> None:
    shares = bcts_bc.parse_species_composition("PL94.9; S4.6; B.5")
    assert shares == {"PL": 94.9, "S": 4.6, "B": 0.5}
    assert sum(shares.values()) == pytest.approx(100.0)


def test_species_composition_rejects_a_bad_sum() -> None:
    with pytest.raises(bcts_bc.BctsDataError, match="sums to"):
        bcts_bc.parse_species_composition("PL50; S20")


def test_pagination_pages_until_transfer_limit_is_false() -> None:
    calls: list[int] = []

    def fake_fetch(url: str, params: dict[str, str]) -> dict[str, object]:
        calls.append(int(params["resultOffset"]))
        if int(params["resultOffset"]) == 0:
            return {
                "features": [
                    {"attributes": {"OBJECTID": 1}},
                    {"attributes": {"OBJECTID": 2}},
                ],
                "exceededTransferLimit": True,
            }
        return {
            "features": [{"attributes": {"OBJECTID": 3}}],
            "exceededTransferLimit": False,
        }

    features = bcts_bc.paginate_features(
        "https://example.test/query", page_size=2, fetch=fake_fetch
    )
    assert [feature["attributes"]["OBJECTID"] for feature in features] == [1, 2, 3]
    assert calls == [0, 2]


def test_pagination_rejects_duplicate_object_ids() -> None:
    def fake_fetch(url: str, params: dict[str, str]) -> dict[str, object]:
        return {
            "features": [
                {"attributes": {"OBJECTID": 1}},
                {"attributes": {"OBJECTID": 1}},
            ],
            "exceededTransferLimit": False,
        }

    with pytest.raises(bcts_bc.BctsDataError, match="duplicate OBJECTID"):
        bcts_bc.paginate_features("https://example.test/query", fetch=fake_fetch)


def test_summary_rejects_a_count_mismatch() -> None:
    rows = [
        {
            "OBJECTID": 1,
            "BCTS_SSSV_SYSID": 1,
            "DIVI_SHORT_CODE": "TOC",
            "FIELD_TEAM": "Team",
            "LICENCE_ID": "TA1",
            "LICN_CATEGORY_ID": "A",
            "LINC_CERT_LEVEL_ID": "SFI",
            "GEOGRAPHIC_LOCATION": "Place",
            "BLOCK_ID": "B1",
            "BLAL_MERCH_HA_AREA": 1.0,
            "BLOCK_VOLUME": 100.0,
            "AUCTION_STATUS": "P",
            "AUCTION_DATE": "2026-01-01",
            "AUCTION_FISCAL": 2026,
            "AUCTION_QUARTER": "Q1",
            "AUC_QUART_MONTH": "Jan",
            "CURRENT_PERIOD_START": "Y",
            "CURRENT_PERIOD_END": "2026-12-31",
            "SILVICULTURE_SYSTEM": "CCRES",
            "YARDING_SYSTEM": "G",
            "PIECE_SIZE": 0.1,
            "TERM": 0,
            "SPECIES_COMPOSITION": "PL100",
            "species_primary": "PL",
            "species_primary_pct": 100.0,
            "species_count": 1,
            "species_sum_pct": 100.0,
            "FEATURE_AREA_SQM": 1000.0,
            "FEATURE_LENGTH_M": 100.0,
        }
    ]
    with pytest.raises(bcts_bc.BctsDataError, match="does not match source count"):
        bcts_bc.summarise_sales(rows, source_count=2, retrieved_utc="2026-11-20T00:00:00+00:00")


def test_nonappraised_parser_reads_five_tables() -> None:
    blocks = []
    for table in ("6-1", "6-2", "6-4", "6-4a", "6-5"):
        blocks.append(f"Table {table}: Test table")
        blocks.append("FOREST ZONE BA CE FI HE LA LO SP WH YE AVG")
        for zone in (
            "North Central",
            "North East",
            "North - Fort Nelson",
            "North West",
            "South Central",
            "South East",
            "South West",
        ):
            values = " ".join(f"{1.0 + index:.2f}" for index in range(10))
            blocks.append(f"{zone} {values}")
    text = "Effective Date: 2026-11-01\n" + "\n".join(blocks)
    rates = bcts_bc.parse_nonappraised_text(text)
    assert len(rates) == 350
    assert {rate.table for rate in rates} == {"6-1", "6-2", "6-4", "6-4a", "6-5"}
    assert rates[0].rate_cad_per_m3 == pytest.approx(1.0)


def test_derived_sales_snapshot_count_volume_and_status() -> None:
    summary_path = DERIVED / "bcts_sales_summary.json"
    sales_path = DERIVED / "bcts_sales.csv"
    if not summary_path.exists() or not sales_path.exists():
        pytest.skip("BCTS derived snapshot is not materialized")
    summary = json.loads(summary_path.read_text())
    with sales_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 162
    assert summary["counts"]["source_count"] == 162
    assert summary["counts"]["staged_rows"] == 162
    assert summary["counts"]["status_codes"] == {"D": 22, "P": 140}
    assert summary["volume_m3"]["total"] == pytest.approx(1_940_031.2286)
    assert sum(float(row["BLOCK_VOLUME"]) for row in rows) == pytest.approx(
        1_940_031.2286
    )
    assert len({row["LICENCE_ID"] for row in rows}) == 99
    assert all(float(row["BLOCK_VOLUME"]) > 0 for row in rows)
    assert all(abs(float(row["species_sum_pct"]) - 100.0) <= 1.0 for row in rows)


def test_ledger_sample_validates_and_is_crown_stumpage_only() -> None:
    ledger_path = DERIVED / "ledger_sample.csv"
    if not ledger_path.exists():
        pytest.skip("BCTS ledger sample is not materialized")
    ledger = load_ledger(ledger_path)
    assert len(ledger) == 63
    assert all(row.perspective is Perspective.CROWN for row in ledger)
    assert all(row.component is Component.STUMPAGE_BASE for row in ledger)
    assert all(row.unit.value == "CAD_per_m3_log" for row in ledger)
    assert all(row.value_basis.evidence.value == "administered_rate" for row in ledger)
    assert all(row.value_basis.price_point.value == "standing" for row in ledger)
    assert len(ledger.by_key()) == len(ledger)


def test_ledger_sample_has_no_cross_perspective_or_stumpage_double_count() -> None:
    ledger_path = DERIVED / "ledger_sample.csv"
    if not ledger_path.exists():
        pytest.skip("BCTS ledger sample is not materialized")
    ledger = load_ledger(ledger_path)
    components = {row.component for row in ledger}
    perspectives = {row.perspective for row in ledger}
    assert Component.STUMPAGE_TOTAL not in components
    assert Component.STUMPAGE_BID_PREMIUM not in components
    assert Component.DELIVERED_LOG_REVENUE not in components
    assert perspectives == {Perspective.CROWN}

    licence_component = [
        (row.spatial_unit, row.species, row.component) for row in ledger
    ]
    assert len(licence_component) == len(set(licence_component))
    assert all(row.notes_tokens.get("mirror") is None for row in ledger)
    assert all(row.notes_tokens.get("aggregated_from") == "1" for row in ledger)


def test_only_table_6_1_enters_the_ledger() -> None:
    rates_path = DERIVED / "stumpage_nonappraised_2026_11.csv"
    ledger_path = DERIVED / "ledger_sample.csv"
    if not rates_path.exists() or not ledger_path.exists():
        pytest.skip("BCTS derived snapshot is not materialized")
    with rates_path.open(newline="", encoding="utf-8") as handle:
        rate_rows = list(csv.DictReader(handle))
    assert {row["table"] for row in rate_rows} == {"6-1", "6-2", "6-4", "6-4a", "6-5"}
    ledger = load_ledger(ledger_path)
    assert len(ledger) == 63
    assert all("Table 6-1" in row.notes for row in ledger)


def test_mps_update_extracts_known_counts_and_operations() -> None:
    interior_path = RAW / "interior_mps_update_2025b.txt"
    coast_path = RAW / "coast_mps_update_2025_01.txt"
    if not interior_path.exists() or not coast_path.exists():
        pytest.skip("MPS extracts are not materialized")
    equations, values = bcts_bc.parse_mps_updates(
        interior_path.read_text(), coast_path.read_text()
    )
    assert any(
        item["equation"] == "interior_final_ewb_2025"
        and item["variable"] == "Constant"
        and item["coefficient"] == pytest.approx(67.4208)
        for item in equations
    )
    assert any(
        item.item == "sales in 2010-2024 auction dataset"
        and item.value_numeric == 3342
        for item in values
    )
    assert any(
        item.item == "sales in 2006-2024 auction dataset"
        and item.value_numeric == 925
        for item in values
    )
