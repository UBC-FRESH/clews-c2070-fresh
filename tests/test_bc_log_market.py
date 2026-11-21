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


def coastal_shifted_footer_fixture():
    meta = log_market.ReportMeta(
        report_id="coast_1_month_second_growth_2023-09",
        region="coast",
        report_basis="1_month",
        log_class="second_growth",
        period_start=date(2023, 9, 1),
        period_end=date(2023, 9, 30),
        filename="1m2gc_sept_23.pdf",
        url="https://example.test/1m2gc_sept_23.pdf",
        sha256="0" * 64,
        retrieved_utc="2026-11-21T20:31:26+00:00",
    )
    rows = log_market.group_rows(
        log_market.read_tsv_words(FIXTURE_DIR / "coast_1m2gc_sept_23.tsv")
    )
    text = (FIXTURE_DIR / "coast_1m2gc_sept_23.txt").read_text(encoding="utf-8")
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


def test_coast_shifted_footer_does_not_overwrite_volume_share() -> None:
    cells, totals = coastal_shifted_footer_fixture()
    by_cell = {(cell["grade"], cell["species"]): cell for cell in cells}
    assert by_cell[("Y", "Fir")]["price_cad_m3"] == pytest.approx(51.83)
    assert by_cell[("Y", "Fir")]["volume_share_pct"] == pytest.approx(0.14)
    assert all(
        cell["volume_share_pct"] is None or 0 <= cell["volume_share_pct"] <= 100
        for cell in cells
    )
    assert totals["report_totals_status"] == "not_reported_or_unparseable"


def test_validate_coast_totals_accepts_consistent_values() -> None:
    totals = log_market.validate_coast_totals(
        {
            "report_total_volume_m3": 239219.72,
            "report_total_value_cad": 32313291.60,
            "report_avg_price_cad_m3": 135.08,
        }
    )
    assert totals["report_totals_status"] == "consistent"


def test_validate_coast_totals_rejects_inconsistent_values() -> None:
    totals = log_market.validate_coast_totals(
        {
            "report_total_volume_m3": 100.0,
            "report_total_value_cad": 99999.0,
            "report_avg_price_cad_m3": 50.0,
        }
    )
    assert totals["report_totals_status"] == "inconsistent"
    assert totals["report_total_volume_m3"] is None


def test_validate_coast_totals_drops_missing_volume() -> None:
    totals = log_market.validate_coast_totals(
        {"report_total_volume_m3": None, "report_avg_price_cad_m3": 135.08}
    )
    assert totals["report_totals_status"] == "not_reported_or_unparseable"
    assert totals["report_avg_price_cad_m3"] is None


def test_is_aggregate_row_only_marks_interior_weighted_average() -> None:
    assert log_market.is_aggregate_row("interior", "weighted_average")
    assert not log_market.is_aggregate_row("interior", "sawlog")
    assert not log_market.is_aggregate_row("coast", "weighted_average")


def test_format_bool_serializes_lowercase_tokens() -> None:
    assert log_market.format_bool(True) == "true"
    assert log_market.format_bool(False) == "false"


def test_status_helpers_distinguish_not_applicable_fields() -> None:
    assert log_market.volume_status_for("coast", None, None) == "not_reported_by_grade"
    assert (
        log_market.volume_status_for("interior", "weighted_average", None)
        == "not_applicable"
    )
    assert log_market.volume_status_for("interior", "sawlog", None) == "blank_cell"
    assert log_market.volume_share_status_for("interior", None) == "not_applicable"
    assert log_market.volume_share_status_for("coast", None) == "blank_cell"


def test_write_csv_uses_lf_line_endings(tmp_path: Path) -> None:
    path = tmp_path / "rows.csv"
    log_market.write_csv(path, [{"a": 1, "b": "x"}], ["a", "b"])
    assert b"\r\n" not in path.read_bytes()


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
