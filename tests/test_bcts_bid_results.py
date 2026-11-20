"""Tests for the BC BCTS public auction-result parser and derived snapshot."""

from __future__ import annotations

import csv
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

import pytest

from workflows.national.harvest import bcts_bids
from workflows.national.harvest.cost_revenue import (
    Component,
    Confidence,
    Perspective,
    load_ledger,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DERIVED = REPO_ROOT / "data/derived/national/harvest_basis/bcts_bc"
FIXTURES = Path(__file__).resolve().parent / "fixtures/bcts_bid_results"
SCHEDULE_PATH = DERIVED / "bcts_sales.csv"
RESULTS_PATH = DERIVED / "bcts_bid_results.csv"
LEDGER_PATH = DERIVED / "bcts_bid_ledger.csv"

REQUIRED_RESULT_FIELDS = {
    "licence_id",
    "tsl",
    "status",
    "auction_date",
    "winning_bid_cad_m3",
    "upset_cad_m3",
    "bonus_cad_m3",
    "bidders",
    "source_url",
    "confidence",
}


def parse_fixture(name: str, tsl: str) -> bcts_bids.ParsedSale:
    return bcts_bids.parse_bid_results_html(
        (FIXTURES / name).read_text(encoding="utf-8"), tsl
    )


def test_parser_reads_an_awarded_per_m3_sale() -> None:
    sale = parse_fixture("ta2089_awarded.html", "TA2089")
    assert sale.found is True
    assert sale.status == "AWARDED"
    assert sale.results_final is True
    assert sale.auction_type == "per_m3"
    assert sale.auction_date.isoformat() == "2026-03-10"
    assert sale.volume_m3 == Decimal(17573)
    assert sale.upset_rate_cad_m3 == Decimal("39.8800")
    assert sale.bonus_rate_cad_m3 == Decimal("13.0800")
    assert sale.winning_rate_cad_m3 == Decimal("52.9600")
    assert sale.bidders == 1
    assert sale.confidence == "high"


def test_parser_counts_eligible_bidders_not_bid_rows() -> None:
    multiple = parse_fixture("ta2510_multiple_bidders.html", "TA2510")
    assert multiple.bidders == 3
    assert multiple.bonus_rate_cad_m3 == Decimal("40.6500")

    ineligible = parse_fixture("ta1133_ineligible_bidder.html", "TA1133")
    assert len(ineligible.bids) == 5
    assert ineligible.bidders == 4
    assert ineligible.winning_rate_cad_m3 == Decimal("111.4800")


def test_parser_converts_a_lump_sum_award_to_per_m3() -> None:
    sale = parse_fixture("a95575_lump_sum_awarded.html", "A95575")
    assert sale.auction_type == "lump_sum"
    assert sale.volume_m3 == Decimal(14308)
    assert sale.upset_rate_cad_m3 == Decimal("8.8200")
    assert sale.bonus_rate_cad_m3 == Decimal("3.1104")
    assert sale.winning_rate_cad_m3 == Decimal("11.9304")
    assert sale.winning_rate_cad_m3 == (sale.upset_rate_cad_m3 + sale.bonus_rate_cad_m3)


def test_parser_keeps_missing_upset_blank_instead_of_zero() -> None:
    sale = parse_fixture("ta1945_missing_upset.html", "TA1945")
    assert sale.status == "AWARDED"
    assert sale.auction_type == "lump_sum"
    assert sale.upset_rate_cad_m3 is None
    assert sale.winning_rate_cad_m3 is None
    assert sale.bonus_rate_cad_m3 == Decimal("3.3285")


def test_parser_distinguishes_no_bids_pending_and_not_found() -> None:
    no_bids = parse_fixture("ta2671_no_bids_lump_sum.html", "TA2671")
    assert no_bids.status == "NO_BIDS"
    assert no_bids.bidders == 0
    assert no_bids.winning_rate_cad_m3 is None

    pending = parse_fixture("ta2491_pending.html", "TA2491")
    assert pending.status == "PENDING_APPROVAL"
    assert pending.results_final is False
    assert pending.winning_rate_cad_m3 == Decimal("22.4300")

    missing = parse_fixture("ta2583_not_found.html", "TA2583")
    assert missing.status == "NOT_FOUND"
    assert missing.bidders is None


def test_parser_rejects_a_bonus_label_without_a_bid() -> None:
    html = (
        "<html><body>Timber Sale Status for TA1 "
        "Auction Date. Tuesday, March 10, 2026 "
        "Location:.... Somewhere Volume:...... 1,000 /m3 "
        "Upset Rate:.. $10.00 /m3 "
        "Auction results have been approved "
        "Bonus Bid:.. $1.00/m3</body></html>"
    )
    with pytest.raises(bcts_bids.BctsBidDataError, match="bonus labels"):
        bcts_bids.parse_bid_results_html(html, "TA1")


def test_result_snapshot_schema_and_one_row_per_tsl() -> None:
    if not RESULTS_PATH.exists() or not SCHEDULE_PATH.exists():
        pytest.skip("BCTS bid-result snapshot is not materialized")

    with RESULTS_PATH.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames is not None
        assert REQUIRED_RESULT_FIELDS <= set(reader.fieldnames)
        rows = list(reader)

    schedule = list(csv.DictReader(SCHEDULE_PATH.open(newline="", encoding="utf-8")))
    schedule_licences = {row["LICENCE_ID"] for row in schedule}
    assert len(rows) == len(schedule_licences) == 99
    assert {row["tsl"] for row in rows} == schedule_licences
    assert len({row["tsl"] for row in rows}) == len(rows)
    assert sum(int(row["schedule_block_count"]) for row in rows) == len(schedule) == 162
    covered_blocks = {
        (row["tsl"], block_id)
        for row in rows
        for block_id in row["schedule_block_ids"].split("|")
        if block_id
    }
    schedule_blocks = {(row["LICENCE_ID"], row["BLOCK_ID"]) for row in schedule}
    assert covered_blocks == schedule_blocks
    assert {row["confidence"] for row in rows} <= {"high", "medium", "low"}
    assert all(row["source_url"].startswith(bcts_bids.RESULTS_URL) for row in rows)


def test_result_snapshot_has_consistent_bid_arithmetic() -> None:
    if not RESULTS_PATH.exists():
        pytest.skip("BCTS bid-result snapshot is not materialized")

    with RESULTS_PATH.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    for row in rows:
        if row["winning_bid_cad_m3"]:
            assert Decimal(row["winning_bid_cad_m3"]) == (
                Decimal(row["upset_cad_m3"]) + Decimal(row["bonus_cad_m3"])
            )
        if row["status"] in {"NO_BIDS", "NO_BIDS_PENDING"}:
            assert row["bidders"] == "0"
            assert row["winning_bid_cad_m3"] == ""
        if row["status"] == "NOT_FOUND":
            assert row["bidders"] == ""
            assert row["winning_bid_cad_m3"] == ""


def test_ledger_uses_one_weight_per_awarded_sale() -> None:
    if not LEDGER_PATH.exists() or not RESULTS_PATH.exists():
        pytest.skip("BCTS bid-result ledger is not materialized")

    ledger = load_ledger(LEDGER_PATH)
    assert len(ledger) == 200
    assert {row.component for row in ledger} == {
        Component.STUMPAGE_BASE,
        Component.STUMPAGE_BID_PREMIUM,
    }
    assert {row.perspective for row in ledger} == {
        Perspective.CROWN,
        Perspective.FOREST_SECTOR,
    }
    assert Component.STUMPAGE_TOTAL not in {row.component for row in ledger}
    assert all(row.value_basis.evidence.value == "observed_sale" for row in ledger)
    assert all(row.value_basis.price_point.value == "standing" for row in ledger)
    assert all(row.confidence is Confidence.MEDIUM for row in ledger)

    with RESULTS_PATH.open(newline="", encoding="utf-8") as handle:
        results = {row["tsl"]: row for row in csv.DictReader(handle)}
    awarded = [row for row in results.values() if row["status"] == "AWARDED"]
    assert len(awarded) == 51

    by_licence: dict[str, list] = defaultdict(list)
    for row in ledger:
        by_licence[row.licence.value].append(row)
    assert set(by_licence) == {row["tsl"] for row in awarded}

    for licence, rows in by_licence.items():
        crown = [row for row in rows if row.perspective is Perspective.CROWN]
        sector = [row for row in rows if row.perspective is Perspective.FOREST_SECTOR]
        assert {row.component for row in crown} == {row.component for row in sector}
        assert all(row.sign.value == 1 for row in crown)
        assert all(row.sign.value == -1 for row in sector)
        assert len({row.volume_m3 for row in rows}) == 1
        assert all(
            Decimal(str(row.volume_m3)) == Decimal(results[licence]["site_volume_m3"])
            for row in rows
        )

        premium = next(
            row for row in crown if row.component is Component.STUMPAGE_BID_PREMIUM
        )
        assert Decimal(str(premium.value_cad)) == Decimal(
            results[licence]["bonus_cad_m3"]
        )
        base_rows = [row for row in crown if row.component is Component.STUMPAGE_BASE]
        if results[licence]["upset_cad_m3"]:
            assert len(base_rows) == 1
            assert Decimal(str(base_rows[0].value_cad)) == Decimal(
                results[licence]["upset_cad_m3"]
            )
        else:
            assert base_rows == []

    assert sum(len(rows) for rows in by_licence.values()) == len(ledger)
    assert len(
        {(row.licence.value, row.perspective, row.component) for row in ledger}
    ) == len(ledger)


def test_ledger_mirrors_are_exact_and_not_double_counted() -> None:
    if not LEDGER_PATH.exists():
        pytest.skip("BCTS bid-result ledger is not materialized")

    ledger = load_ledger(LEDGER_PATH)
    mirrors = [row for row in ledger if row.mirror_requested]
    assert len(mirrors) == 100
    assert all(row.perspective is Perspective.FOREST_SECTOR for row in mirrors)

    crowns = {
        (row.licence.value, row.component): row
        for row in ledger
        if row.perspective is Perspective.CROWN
    }
    for mirror in mirrors:
        counterpart = crowns[(mirror.licence.value, mirror.component)]
        assert mirror.value_cad == counterpart.value_cad
        assert mirror.volume_m3 == counterpart.volume_m3
        assert mirror.unit is counterpart.unit
        assert mirror.value_basis == counterpart.value_basis

    assert len(crowns) == 100
    assert sum(1 for row in ledger if row.component is Component.STUMPAGE_TOTAL) == 0
    assert Counter(row.perspective for row in ledger) == {
        Perspective.CROWN: 100,
        Perspective.FOREST_SECTOR: 100,
    }
