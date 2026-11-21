"""Tests for the BC public log-market PDF table parser."""

from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "scripts/national/build_bc_log_market.py"
FIXTURE_DIR = REPO_ROOT / "tests/fixtures/bc_log_market"


def load_module():
    spec = importlib.util.spec_from_file_location("build_bc_log_market", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


log_market = load_module()


def coastal_fixture():
    meta = log_market.ReportMeta(
        report_id="coast_1_month_old_growth_2026-01",
        region="coast",
        report_basis="1_month",
        log_class="old_growth",
        period_start=date(2026, 1, 1),
        period_end=date(2026, 1, 31),
        filename="1mc_jan_26.pdf",
        url="https://example.test/1mc_jan_26.pdf",
        sha256="0" * 64,
        retrieved_utc="2026-11-21T20:31:00+00:00",
    )
    rows = log_market.group_rows(
        log_market.read_tsv_words(FIXTURE_DIR / "coast_1mc_jan_26.tsv")
    )
    text = (FIXTURE_DIR / "coast_1mc_jan_26.txt").read_text(encoding="utf-8")
    return log_market.parse_coast(rows, text, meta)


def interior_fixture():
    meta = log_market.ReportMeta(
        report_id="interior_1_month_not_reported_2026-09",
        region="interior",
        report_basis="1_month",
        log_class="not_reported",
        period_start=date(2026, 9, 1),
        period_end=date(2026, 9, 30),
        filename="1mi_sept_26.pdf",
        url="https://example.test/1mi_sept_26.pdf",
        sha256="0" * 64,
        retrieved_utc="2026-11-21T20:31:00+00:00",
    )
    rows = log_market.group_rows(
        log_market.read_tsv_words(FIXTURE_DIR / "interior_1mi_sept_26.tsv")
    )
    text = (FIXTURE_DIR / "interior_1mi_sept_26.txt").read_text(encoding="utf-8")
    return log_market.parse_interior(rows, text, meta)


def test_parse_number_handles_published_formatting() -> None:
    assert log_market.parse_number("1,234.50") == pytest.approx(1234.5)
    assert log_market.parse_number("$90.45") == pytest.approx(90.45)
    assert log_market.parse_number("12.3%") == pytest.approx(12.3)
    assert log_market.parse_number("-") is None


def test_parse_number_rejects_non_numeric_text() -> None:
    with pytest.raises(log_market.ParseError, match="not a number"):
        log_market.parse_number("old growth")


def test_coast_fixture_keeps_prices_in_reported_columns() -> None:
    cells, totals = coastal_fixture()
    by_cell = {(cell["grade"], cell["species"]): cell for cell in cells}
    assert totals["report_total_volume_m3"] == pytest.approx(41778.90)
    assert len(cells) == 35
    assert by_cell[("H", "Cedar")]["price_cad_m3"] == pytest.approx(550.79)
    assert by_cell[("H", "Cedar")]["volume_share_pct"] == pytest.approx(16.57)
    assert by_cell[("H", "Pine")]["price_cad_m3"] == pytest.approx(92.85)
    assert ("H", "Spruce") not in by_cell


def test_coast_period_accepts_month_only_and_day_forms() -> None:
    assert log_market.parse_coast_period(
        "For the 1 month period ending: January 2026"
    ) == (date(2026, 1, 1), date(2026, 1, 31), "1_month")
    assert log_market.parse_coast_period(
        "For the 3 month period ending: December 31, 2019"
    ) == (date(2019, 10, 1), date(2019, 12, 31), "3_month")


def test_interior_fixture_reads_product_species_cells() -> None:
    cells, totals = interior_fixture()
    by_cell = {(cell["end_use"], cell["species"]): cell for cell in cells}
    assert len(cells) == 13
    assert totals["report_total_volume_m3"] == pytest.approx(830635.0)
    assert totals["report_avg_price_cad_m3"] == pytest.approx(119.03)
    assert by_cell[("sawlog", "SPF")]["price_cad_m3"] == pytest.approx(115.64)
    assert by_cell[("sawlog", "SPF")]["volume_m3"] == pytest.approx(577105.0)
    assert by_cell[("sawlog", "Cedar")]["price_cad_m3"] == pytest.approx(191.33)
