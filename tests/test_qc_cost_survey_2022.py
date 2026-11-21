"""Tests for the Quebec MRNF 2022 cost/revenue survey parser."""

from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "scripts/national/extract_qc_cost_survey_2022.py"
FIXTURE_PATH = REPO_ROOT / "tests/fixtures/qc_cost_survey/regional_2022_rows.json"


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
        sample_n_by_region={"R09": 3},
    )
    flagged = next(
        row
        for row in flagged_rows
        if row.region_code == "R09" and row.component == first_region_row.component
    )
    assert flagged.confidentiality_flag == "three_respondents"
    assert flagged_coverage.values_flagged_three_respondents == 1


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
