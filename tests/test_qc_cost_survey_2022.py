"""Tests for the Quebec MRNF 2022 cost/revenue survey parser."""

from __future__ import annotations

import csv
import importlib.util
import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "scripts/national/extract_qc_cost_survey_2022.py"
FIXTURE_PATH = REPO_ROOT / "tests/fixtures/qc_cost_survey/regional_2022_rows.json"
SURVEY_CSV = (
    REPO_ROOT
    / "data/derived/national/harvest_basis/qc_cost_survey_2022"
    / "qc_cost_revenue_survey.csv"
)
MANIFEST_JSON = SURVEY_CSV.with_name("manifest.json")


def load_module():
    spec = importlib.util.spec_from_file_location(
        "extract_qc_cost_survey_2022", MODULE_PATH
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


qc = load_module()


def fixture_rows() -> list[list[str | None]]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))["rows"]


def regional_rows():
    data = fixture_rows()
    return qc.rows_from_extracted_table(
        data,
        qc.REGIONAL_TABLE,
        region_by_column={
            column.index: code
            for column, code in zip(
                qc.REGIONAL_TABLE.columns, qc.REGIONAL_CODES, strict=True
            )
        },
        sample_n_by_region={
            "R01-R03-R11-R12": 11,
            "R09": 3,
            "R02": 6,
            "R04": 4,
            "R14-R15-R07-R08": 5,
            "R08": 4,
            "R10": 4,
            "QC": 37,
        },
    )


def test_parse_french_number_accepts_survey_formatting():
    assert qc.parse_french_number("28,27") == pytest.approx(28.27)
    assert qc.parse_french_number("1 264,61") == pytest.approx(1264.61)
    assert qc.parse_french_number("\u00a01\u202f234,50") == pytest.approx(1234.50)
    assert qc.parse_french_number("-5,49") == pytest.approx(-5.49)
    assert qc.parse_french_number("90,66 $/tma") == pytest.approx(90.66)
    assert qc.parse_french_number("54,4 %") == pytest.approx(54.4)


def test_parse_french_number_rejects_bad_and_non_finite_values():
    for bad in ("abc", "nan", "inf", "-inf"):
        with pytest.raises(ValueError):
            qc.parse_french_number(bad)


def test_parse_cell_value_preserves_missing_and_suppressed_states():
    value, unit, flag, _ = qc.parse_cell_value(
        "N.D.", default_unit=qc.CAD_M3, dash_meaning="suppressed_lt3", context="t"
    )
    assert value is None
    assert flag == "not_available"
    assert unit == qc.CAD_M3

    value, _, flag, _ = qc.parse_cell_value(
        "S. O.", default_unit=qc.CAD_M3, dash_meaning="suppressed_lt3", context="t"
    )
    assert value is None
    assert flag == "not_applicable"

    value, _, flag, _ = qc.parse_cell_value(
        "–", default_unit=qc.CAD_M3, dash_meaning="suppressed_lt3", context="t"
    )
    assert value is None
    assert flag == "suppressed_lt3"


def test_canonical_row_label_strips_footnote_and_accents():
    assert qc.canonical_row_label("Coût d’hébergement et frais d’éloignement2") == (
        "cout d'hebergement et frais d'eloignement"
    )
    assert qc.canonical_row_label(
        "Coût d’hébergement et frais d’éloignement2", keep_footnote=True
    ) == "cout d'hebergement et frais d'eloignement2"


def test_regional_fixture_emits_all_published_values():
    rows, coverage = regional_rows()
    assert coverage.parsed_status == "parsed"
    assert coverage.rows_emitted == 88
    assert coverage.values_missing == 0
    assert coverage.values_suppressed == 0
    assert len(rows) == 88
    assert {row.region_code for row in rows} == set(qc.REGIONAL_CODES)
    assert all(row.observation_type == "survey_aggregate" for row in rows)
    assert all(row.aggregation_level in {"regional", "provincial"} for row in rows)
    assert all(
        row.confidentiality_flag == "three_respondents"
        for row in rows
        if row.region_code == "R09"
    )

    r09_total = next(
        row
        for row in rows
        if row.region_code == "R09"
        and row.component == "procurement_total_after_transfers"
    )
    assert r09_total.value == pytest.approx(118.10)
    assert r09_total.sample_n == 3


def test_regional_fixture_reconciles_without_source_inconsistency():
    rows, _ = regional_rows()
    checks = qc.reconcile_rows(rows)
    assert checks
    assert all(check["status"] == "pass" for check in checks)


def test_red_cell_sets_three_respondent_flag():
    rows, _ = regional_rows()
    first_region_row = next(row for row in rows if row.region_code == "R09")
    flagged_rows, flagged_coverage = qc.rows_from_extracted_table(
        fixture_rows(),
        qc.REGIONAL_TABLE,
        red_cells={(2, 3)},
        region_by_column={
            column.index: code
            for column, code in zip(
                qc.REGIONAL_TABLE.columns, qc.REGIONAL_CODES, strict=True
            )
        },
        sample_n_by_region={"R09": 4},
    )
    flagged = next(
        row
        for row in flagged_rows
        if row.region_code == "R09" and row.component == first_region_row.component
    )
    assert flagged.confidentiality_flag == "three_respondents"
    assert flagged_coverage.values_flagged_three_respondents == 1


def test_sample_n_three_flags_every_r09_row():
    rows, _ = regional_rows()
    r09 = [row for row in rows if row.region_code == "R09"]
    assert len(r09) == 11
    assert all(row.sample_n == 3 for row in r09)
    assert all(row.confidentiality_flag == "three_respondents" for row in r09)
    assert all("three respondents" in row.notes.lower() for row in r09)


def test_validate_rejects_unexpected_negative_value():
    rows, _ = regional_rows()
    bad = replace(
        rows[0],
        value=-1.0,
        sign_convention="positive_cost",
        unit=qc.CAD_M3,
    )
    with pytest.raises(ValueError, match="unexpected negative"):
        qc.validate_rows([bad])


def test_pdf_page_mapping_matches_report_offset():
    t2 = next(spec for spec in qc.TABLE_SPECS if spec.key == "T2")
    assert t2.page == 12
    assert qc._pdf_page(t2) == 18
    assert qc._pdf_page(qc.A2_TABLE) == 37
    assert qc._pdf_page(qc.REGIONAL_TABLE) == 1


def test_a2_respondent_counts_are_explicit():
    assert qc.A2_SAMPLE_N[("sepm", 2022)] == 37
    assert qc.A2_SAMPLE_N[("feuillus_durs", 2022)] == 11
    assert qc.A2_SAMPLE_N[("peupliers", 2022)] == 9


def test_t20_peupliers_trituration_inherits_three_respondent_flag():
    t20 = next(spec for spec in qc.TABLE_SPECS if spec.key == "T20")
    raw_rows = [["Recolte", None, "31,42", None, "31,95", "26,30"]]
    rows, coverage = qc.rows_from_extracted_table(raw_rows, t20)
    poplar = next(
        row
        for row in rows
        if row.species_group == "peupliers" and row.product_or_grade == "trituration"
    )
    assert poplar.confidentiality_flag == "three_respondents"
    assert "three-respondent" in poplar.notes
    assert coverage.values_flagged_three_respondents == 1
    assert poplar.value == pytest.approx(26.30)


def test_t21_purchase_is_a_synthesis_row_and_t2_stays_component():
    t2 = next(spec for spec in qc.TABLE_SPECS if spec.key == "T2")
    t21 = next(spec for spec in qc.TABLE_SPECS if spec.key == "T21")
    assert t2.row_map["Achat de bois"].observation_kind == "component"
    purchase = t21.row_map["Achats de bois (livre)"]
    assert purchase.observation_kind == "synthesis"
    assert "procurement cost" in purchase.notes


def test_row_spec_notes_survive_cell_parsing():
    t10 = next(spec for spec in qc.TABLE_SPECS if spec.key == "T10")
    raw_rows = [["Encheres", None, "32,96"]]
    rows, _ = qc.rows_from_extracted_table(raw_rows, t10)
    assert len(rows) == 1
    assert rows[0].component == "auction_price"
    assert "Auction price shown as a reference" in rows[0].notes


def test_validate_enforces_sign_conventions():
    rows, _ = regional_rows()
    valid_physical = replace(
        rows[0], unit=qc.PERCENT, sign_convention=qc.PHYSICAL_QUANTITY, value=50.0
    )
    valid_revenue = replace(
        rows[0],
        component_group=qc.REVENUE,
        component="gross_revenue",
        unit=qc.CAD_M3,
        sign_convention=qc.POSITIVE_REVENUE,
        value=50.0,
    )
    assert qc.validate_rows([valid_physical])["row_count"] == 1
    assert qc.validate_rows([valid_revenue])["row_count"] == 1

    bad_physical = replace(
        rows[0], unit=qc.PERCENT, sign_convention="positive_cost", value=50.0
    )
    bad_revenue = replace(
        rows[0],
        component_group=qc.REVENUE,
        component="gross_revenue",
        unit=qc.CAD_M3,
        sign_convention="positive_cost",
        value=50.0,
    )
    with pytest.raises(ValueError, match="unexpected sign conventions"):
        qc.validate_rows([bad_physical])
    with pytest.raises(ValueError, match="unexpected sign conventions"):
        qc.validate_rows([bad_revenue])


def test_published_csv_component_keys_are_unique_and_notes_are_preserved():
    assert SURVEY_CSV.exists()
    with SURVEY_CSV.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 660
    assert all("\n" not in row["source_label_fr"] for row in rows)

    key = (
        "survey_year",
        "region_code",
        "species_group",
        "product_or_grade",
        "component_group",
        "component",
        "subcomponent",
        "unit",
        "observation_kind",
    )
    component_keys = [
        tuple(row[column] for column in key)
        for row in rows
        if row["observation_kind"] == "component"
    ]
    assert len(component_keys) == len(set(component_keys))

    t21_purchases = [
        row
        for row in rows
        if row["source_table"] == "T21" and row["component"] == "wood_purchase_delivered"
    ]
    assert t21_purchases
    assert all(row["observation_kind"] == "synthesis" for row in t21_purchases)

    auction_rows = [
        row
        for row in rows
        if row["source_table"] == "T10" and row["component"] == "auction_price"
    ]
    assert auction_rows
    assert all(
        "Auction price shown as a reference" in row["notes"] for row in auction_rows
    )
    assert sum(
        1 for row in rows if row["confidentiality_flag"] == "three_respondents"
    ) == 128


def test_manifest_records_accounting_caveats_and_t1_metadata():
    manifest = json.loads(MANIFEST_JSON.read_text(encoding="utf-8"))
    assert manifest["price_year"] == "survey_year"
    assert manifest["currency_basis"] == "CAD_nominal"
    assert manifest["coverage"]["questionnaire_counts"]["peupliers"] == 12
    assert manifest["validation"]["three_respondent_value_count"] == 128
    assert manifest["validation"]["spot_check_count"] == 8
    assert len(manifest["validation"]["reconciliation_skipped_suppressed"]) == 2
    assert {caveat["id"] for caveat in manifest["accounting_caveats"]} == {
        "t2_t21_purchased_wood_procurement_cost",
        "t20_bgad_is_a_parallel_supply_channel",
        "prccm_rebates_are_inside_transfers_total",
        "t20_excludes_species_gain_loss",
    }
    t1 = next(
        item
        for item in manifest["coverage"]["tables_not_parsed"]
        if item["source_table"] == "T1"
    )
    assert t1["source_page"] == "7"
    assert "peupliers 12" in t1["notes"]
