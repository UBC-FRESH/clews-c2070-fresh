"""Tests for the BMMB delivered-sale parser and normalizer."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "scripts/national/extract_bmmb_delivered.py"
FIXTURE_PATH = REPO_ROOT / "tests/fixtures/bmmb/delivered_rows.json"


def load_module():
    spec = importlib.util.spec_from_file_location("extract_bmmb_delivered", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bmmb = load_module()


def fixture_rows() -> list[dict]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def parse_fixture_row(row: dict, pdf_results=None, pdf_urls=None):
    return bmmb.parse_sale(
        row,
        "https://example.test/detail",
        pdf_results or {},
        pdf_urls or {},
    )


def test_parse_number_accepts_french_formatting_and_rejects_negative():
    assert bmmb.parse_number("1 234,50", "price") == pytest.approx(1234.50)
    assert bmmb.parse_number("\u00a01\u00a0234,50", "price") == pytest.approx(1234.50)
    assert bmmb.parse_number("", "price") is None
    with pytest.raises(ValueError, match="negative"):
        bmmb.parse_non_negative("-1", "price")


def test_parse_number_rejects_nan_and_infinity():
    with pytest.raises(ValueError, match="non-finite"):
        bmmb.parse_number("nan", "price")
    with pytest.raises(ValueError, match="non-finite"):
        bmmb.parse_number("inf", "price")


def test_awarded_delivered_sale_gets_aggregate_rate():
    sale = parse_fixture_row(fixture_rows()[0])
    assert sale.sale_type_family == "delivered_to_destination"
    assert sale.awarded is True
    assert sale.volume_m3 == pytest.approx(1000.0)
    assert sale.total_cad == pytest.approx(50000.0)
    assert sale.price_cad_m3 == pytest.approx(50.0)
    assert sale.partial_award is False
    assert sum(line["volume_m3"] for line in sale.species_lines) == 1000.0


def test_unsold_delivered_sale_keeps_offer_estimate_only():
    sale = parse_fixture_row(fixture_rows()[1])
    assert sale.awarded is False
    assert sale.total_cad is None
    assert sale.price_cad_m3 is None
    assert sale.estimated_total_cad == pytest.approx(30000.0)
    row = bmmb.sale_row(sale)
    assert row["observed"] == 0
    assert row["observation_type"] == "offer_estimate_only"
    assert row["price_cad_m3"] == ""


def test_partial_award_has_no_full_rate_but_has_lower_bound():
    row = fixture_rows()[3]
    sale = parse_fixture_row(
        row,
        pdf_results={"CS202001001": bmmb.PdfResult(price=40000.0, partial_award=True)},
        pdf_urls={"CS202001001": "https://example.test/result.pdf"},
    )
    assert sale.awarded is True
    assert sale.partial_award is True
    assert sale.total_cad == pytest.approx(40000.0)
    assert sale.price_cad_m3 is None
    assert sale.price_lower_bound_cad_m3 == pytest.approx(50.0)
    rendered = bmmb.sale_row(sale)
    assert rendered["price_cad_m3"] == ""
    assert rendered["price_cad_m3_lower_bound"] == "50"
    assert rendered["price_unit_basis"] == "not_computable_partial_award_volume"
    assert "partial_award_noted=1" in rendered["notes"]


def test_partial_award_requires_the_pdf_phrase_not_pdf_presence():
    row = fixture_rows()[3]
    sale = parse_fixture_row(
        row,
        pdf_results={"CS202001001": bmmb.PdfResult(price=40000.0, partial_award=False)},
        pdf_urls={"CS202001001": "https://example.test/result.pdf"},
    )
    assert sale.partial_award is False
    assert sale.price_cad_m3 == pytest.approx(50.0)
    assert sale.price_lower_bound_cad_m3 is None


def test_pdf_text_partial_award_phrase_is_case_insensitive():
    assert bmmb.pdf_text_reports_partial_award(
        "*Certains volumes de bois offerts sont non-adjugés."
    )
    assert not bmmb.pdf_text_reports_partial_award("Prix de vente : 1 000 $")


def test_sale_type_summary_separates_delivered_from_standing():
    rows = fixture_rows()
    sales = [parse_fixture_row(row) for row in rows]
    sales[3] = parse_fixture_row(
        rows[3],
        pdf_results={"CS202001001": bmmb.PdfResult(price=40000.0, partial_award=True)},
        pdf_urls={"CS202001001": "https://example.test/result.pdf"},
    )
    summary = bmmb.summarise_sales(sales)
    delivered = [row for row in summary if row["sale_type"] == bmmb.DELIVERED]
    standing = [row for row in summary if row["sale_type"] == bmmb.STANDING_MEASURED]
    assert len(delivered) == 1
    assert len(standing) == 1
    assert delivered[0]["sale_count"] == 3
    assert delivered[0]["awarded_count"] == 2
    assert delivered[0]["observed_price_count"] == 2
    assert delivered[0]["partial_award_count"] == 1
    assert float(delivered[0]["reported_sale_value_cad"]) == pytest.approx(90000.0)
    assert float(delivered[0]["rate_computable_value_cad"]) == pytest.approx(50000.0)
    assert float(delivered[0]["rate_cad_m3"]) == pytest.approx(50.0)
    assert delivered[0]["is_delivered"] == 1
    assert standing[0]["is_delivered"] == 0
    assert standing[0]["value_semantics"] == "standing_sale_total_cad"
    assert standing[0]["rate_unit_basis"] == "CAD_per_m3_standing"
    assert {row["currency_basis"] for row in summary} == {"CAD_nominal"}
    assert all(
        "result_pdf_checks_delivered_sales_only=1" in row["notes"] for row in summary
    )


def test_rate_breakdown_separates_crown_from_non_crown():
    rows = fixture_rows()
    crown = parse_fixture_row(rows[0])
    non_crown_row = json.loads(json.dumps(rows[0]))
    non_crown_row["Numéro d'appel d'offres"] = "SE202001003"
    non_crown_row["Provenance"] = "Forêt d'enseignement et recherche"
    non_crown_row["Volume obligatoire total m³"] = 2000.0
    non_crown_row["Prix de vente"] = 120000.0
    non_crown_row["Épinettes - Volume de bois d'oeuvre obligatoire"] = 2000.0
    non_crown_row["Épinettes - Volume de trituration obligatoire"] = 0.0
    non_crown = parse_fixture_row(non_crown_row)

    breakdown = {row["segment"]: row for row in bmmb.rate_breakdown([crown, non_crown])}
    assert breakdown["crown_public_forest"]["sale_count"] == 1
    assert breakdown["crown_public_forest"]["volume_m3"] == pytest.approx(1000.0)
    assert breakdown["crown_public_forest"]["weighted_rate_cad_m3"] == pytest.approx(
        50.0
    )
    assert breakdown["non_crown_teaching_research"][
        "weighted_rate_cad_m3"
    ] == pytest.approx(60.0)
    assert breakdown["non_crown_teaching_research"][
        "sawlog_share_pct"
    ] == pytest.approx(100.0)


def test_reoffer_sale_records_prior_sale_id_and_overlap():
    row = json.loads(json.dumps(fixture_rows()[0]))
    row["Numéro d'appel d'offres"] = "CS201207001"
    row["Date de fin enchère"] = "2012-07-20 12:00:00"
    row["Volume obligatoire total m³"] = 8300.0
    row["Épinettes - Volume de bois d'oeuvre obligatoire"] = 0.0
    row["Épinettes - Volume de trituration obligatoire"] = 8300.0
    rendered = bmmb.sale_row(parse_fixture_row(row))
    assert rendered["reoffer_of_sale_id"] == "CS201206004"
    assert "reoffer_overlap_m3=8300" in rendered["notes"]
    assert bmmb.REOFFER_OVERLAP_M3 == pytest.approx(22200.0)


def test_detail_price_check_fails_when_a_required_page_is_missing(tmp_path):
    sale = parse_fixture_row(fixture_rows()[0])
    with pytest.raises(ValueError, match="missing detail page"):
        bmmb.validate_detail_prices([sale], tmp_path)


def test_detail_price_check_verifies_every_export_price(tmp_path):
    sale = parse_fixture_row(fixture_rows()[0])
    (tmp_path / f"{sale.sale_id}.html").write_text(
        "<p>La vente a été adjugée à Test pour 50 000 $.</p>", encoding="utf-8"
    )
    checks = bmmb.validate_detail_prices([sale], tmp_path)
    assert checks == [
        {
            "sale_id": sale.sale_id,
            "export_total_cad": 50000.0,
            "detail_total_cad": 50000.0,
            "status": "pass",
        }
    ]


def test_unexpected_sale_type_fails_loudly():
    row = fixture_rows()[0]
    row["Type de vente"] = "Livré ailleurs"
    with pytest.raises(ValueError, match="unexpected sale type"):
        parse_fixture_row(row)


def test_species_volume_mismatch_fails_loudly():
    row = fixture_rows()[0]
    row["Volume obligatoire total m³"] = 999.0
    with pytest.raises(ValueError, match="species mandatory volume"):
        parse_fixture_row(row)


def test_format_number_strips_trailing_zeroes():
    assert bmmb.format_number(1000.0, 3) == "1000"
    assert bmmb.format_number(50.0, 4) == "50"
    assert bmmb.format_number(43.6385, 4) == "43.6385"
    assert bmmb.format_number(None, 2) == ""
