"""Tests for the QC procurement-cost benchmark and BMMB historical table."""

from __future__ import annotations

import csv
import hashlib
import json
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from workflows.national.harvest import procurement_benchmark_v1 as benchmark

REPO_ROOT = Path(__file__).resolve().parents[1]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> benchmark.BuildResult:
    output_dir = tmp_path_factory.mktemp("procurement_benchmark")
    return benchmark.build(output_dir=output_dir)


@pytest.fixture(scope="module")
def survey_rows() -> tuple[benchmark.SurveyRow, ...]:
    return benchmark.parse_survey_rows(benchmark.SURVEY_PATH)


@pytest.fixture()
def bmmb_dir(tmp_path: Path) -> Path:
    target = tmp_path / "bmmb_delivered"
    target.mkdir()
    shutil.copy(benchmark.BMMB_SALES_PATH, target / "bmmb_delivered_sales.csv")
    shutil.copy(
        benchmark.BMMB_SALES_PATH.with_name("manifest.json"),
        target / "manifest.json",
    )
    return target


def test_build_writes_required_outputs_and_passes_checks(
    built: benchmark.BuildResult,
) -> None:
    expected = {
        "qc_procurement_cost_benchmark.csv",
        "qc_procurement_regional_range.csv",
        "bmmb_historical_benchmark.csv",
        "summary.json",
        "PROCUREMENT_BENCHMARK_REPORT.md",
        "manifest.json",
    }
    assert {path.name for path in built.output_dir.iterdir()} == expected
    assert len(built.checks) == 20
    assert all(check["status"] == "pass" for check in built.checks)


def test_every_output_is_labelled_procurement_cost_not_net_value(
    built: benchmark.BuildResult,
) -> None:
    provincial = read_csv(built.output_dir / "qc_procurement_cost_benchmark.csv")
    regional = read_csv(built.output_dir / "qc_procurement_regional_range.csv")
    bmmb = read_csv(built.output_dir / "bmmb_historical_benchmark.csv")

    for rows, evidence in (
        (provincial, "survey_procurement_cost"),
        (regional, "survey_regional_procurement_cost"),
        (bmmb, "benchmark_historical"),
    ):
        assert {row["evidence"] for row in rows} == {evidence}
        assert {row["policy_grade"] for row in rows} == {"false"}
        assert {row["net_value_status"] for row in rows} == {"not_estimated"}

    for row in provincial + regional:
        assert row["interpretation"] == "purchased_vs_bgad_procurement_cost_spread"
        assert row["unit"] == "CAD/m3"
        assert row["price_year"] == "2022"
        assert row["currency_basis"] == "CAD_nominal_2022"
        assert "net value" not in row["interpretation"]

    for row in bmmb:
        assert row["currency_basis"] == "CAD_nominal"
        assert row["deflator_applied"] == "false"

    summary = json.loads((built.output_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["status"] == "not_estimated"
    assert summary["interpretation"] == "purchased_vs_bgad_procurement_cost_spread"
    assert summary["volume_scaled_diagnostic"]["produced"] is False
    assert summary["labels"]["policy_grade"] is False
    assert "cost_spread_before_transfers_cad_m3" in provincial[0]


def test_combine_confidentiality_flags_materializes_generator_inputs() -> None:
    flags = (flag for flag in ("none", "three_respondents"))
    assert (
        benchmark.combine_confidentiality_flags(flags, context="generator")
        == "three_respondents"
    )

    unknown = (flag for flag in ("none", "unexpected"))
    with pytest.raises(benchmark.BenchmarkError, match="unexpected confidentiality"):
        benchmark.combine_confidentiality_flags(unknown, context="generator")


def test_provincial_cost_spreads_are_recomputed(
    built: benchmark.BuildResult,
) -> None:
    rows = {
        row["benchmark_id"]: row
        for row in read_csv(built.output_dir / "qc_procurement_cost_benchmark.csv")
    }
    assert len(rows) == 9

    sep = rows["qc_2022_sepm_total"]
    assert float(sep["purchase_cost_cad_m3"]) == pytest.approx(107.22)
    assert float(sep["bgad_procurement_before_transfers_cad_m3"]) == pytest.approx(81.40)
    assert float(sep["transfers_total_cad_m3"]) == pytest.approx(25.01)
    assert float(sep["bgad_procurement_after_transfers_cad_m3"]) == pytest.approx(106.41)
    assert float(sep["cost_spread_before_transfers_cad_m3"]) == pytest.approx(25.82)
    assert float(sep["cost_spread_after_transfers_cad_m3"]) == pytest.approx(0.81)
    assert sep["spread_status"] == "computed"
    assert sep["purchase_semantics"] == "buyer_side_procurement_cost_not_log_sale_revenue"

    hardwood = rows["qc_2022_feuillus_durs_sciage"]
    assert float(hardwood["cost_spread_before_transfers_cad_m3"]) == pytest.approx(5.69)
    assert float(hardwood["cost_spread_after_transfers_cad_m3"]) == pytest.approx(-14.53)

    poplar = rows["qc_2022_peupliers_trituration"]
    assert float(poplar["cost_spread_before_transfers_cad_m3"]) == pytest.approx(-7.27)
    assert float(poplar["cost_spread_after_transfers_cad_m3"]) == pytest.approx(-9.45)
    assert poplar["confidentiality_flag"] == "three_respondents"
    assert poplar["three_respondent_flag"] == "true"
    assert "Three-respondent mean" in poplar["notes"]

    for scope in ("pins_total", "thuyas_total", "feuillus_durs_total"):
        row = rows[f"qc_2022_{scope}"]
        assert row["spread_status"] == "not_computable_missing_bgad_synthesis"
        assert row["cost_spread_before_transfers_cad_m3"] == ""
        assert row["cost_spread_after_transfers_cad_m3"] == ""


def test_regional_range_flags_r09_and_independent_bounds(
    built: benchmark.BuildResult,
) -> None:
    rows = read_csv(built.output_dir / "qc_procurement_regional_range.csv")
    values = [row for row in rows if row["statistic"] == "region_value"]
    assert len(values) == 7
    assert all(row["bgad_source_table"] == "REG" for row in rows)
    assert all(row["survey_source_id"] == "regional_2022" for row in rows)
    assert all(
        "Compilation_donnees_regionalisees_2022.pdf" in row["source_document"]
        for row in rows
    )

    by_region = {row["region_code"]: row for row in values}
    assert by_region["R09"]["sample_n"] == "3"
    assert by_region["R09"]["confidentiality_flag"] == "three_respondents"
    assert by_region["R09"]["three_respondent_flag"] == "true"
    assert "Three-respondent mean" in by_region["R09"]["notes"]
    assert float(by_region["R09"]["conditional_cost_spread_after_transfers_cad_m3"]) == pytest.approx(
        -10.88
    )

    before = [float(row["bgad_procurement_before_transfers_cad_m3"]) for row in values]
    after = [float(row["bgad_procurement_after_transfers_cad_m3"]) for row in values]
    spread_before = [
        float(row["conditional_cost_spread_before_transfers_cad_m3"]) for row in values
    ]
    spread_after = [
        float(row["conditional_cost_spread_after_transfers_cad_m3"]) for row in values
    ]
    assert (min(before), max(before)) == pytest.approx((58.73, 97.07))
    assert (min(after), max(after)) == pytest.approx((78.78, 118.10))
    assert (min(spread_before), max(spread_before)) == pytest.approx((10.15, 48.49))
    assert (min(spread_after), max(spread_after)) == pytest.approx((-10.88, 28.44))

    bound_rows = {row["statistic"]: row for row in rows if row["statistic"] != "region_value"}
    assert set(bound_rows) == {"min", "max"}
    for bound in bound_rows.values():
        assert bound["three_respondent_flag"] == "true"
        assert bound["columns_are_independent_extrema"] == "true"
        assert "horizontal identities do not hold" in bound["notes"]
    assert "R09" in bound_rows["max"]["three_respondent_extreme_columns"]

    max_bound = bound_rows["max"]
    horizontal_after = float(max_bound["bgad_procurement_before_transfers_cad_m3"]) + float(
        max_bound["transfers_total_cad_m3"]
    )
    assert horizontal_after != pytest.approx(
        float(max_bound["bgad_procurement_after_transfers_cad_m3"])
    )


def test_bmmb_crown_primary_teaching_and_all_in(
    built: benchmark.BuildResult,
) -> None:
    rows = read_csv(built.output_dir / "bmmb_historical_benchmark.csv")
    overall = {
        row["scope_id"]: row
        for row in rows
        if row["aggregation_level"] == "delivered_all_years"
    }
    crown = overall["crown_public_forest"]
    teaching = overall["teaching_research_forest"]
    all_in = overall["all_delivered"]

    assert crown["is_primary_benchmark"] == "true"
    assert teaching["is_primary_benchmark"] == "false"
    assert all_in["is_primary_benchmark"] == "false"
    assert crown["full_rate_sale_count"] == "9"
    assert float(crown["rate_computable_volume_m3"]) == pytest.approx(74_550.0)
    assert float(crown["weighted_rate_cad_m3"]) == pytest.approx(35.7547)
    assert teaching["full_rate_sale_count"] == "2"
    assert float(teaching["rate_computable_volume_m3"]) == pytest.approx(15_350.0)
    assert float(teaching["weighted_rate_cad_m3"]) == pytest.approx(50.6266)
    assert all_in["sale_count"] == "18"
    assert all_in["full_rate_sale_count"] == "11"
    assert float(all_in["weighted_rate_cad_m3"]) == pytest.approx(38.2940)
    assert float(all_in["median_rate_cad_m3"]) == pytest.approx(36.6668)
    assert all_in["reoffer_sale_count"] == "2"
    assert float(all_in["reoffer_overlap_m3"]) == pytest.approx(22_200.0)
    assert "Re-offer overlap" in all_in["notes"]
    assert "22,200" in all_in["notes"]


def test_bmmb_per_year_rows_use_dynamic_counts_and_price_year_basis(
    built: benchmark.BuildResult,
) -> None:
    rows = read_csv(built.output_dir / "bmmb_historical_benchmark.csv")
    years = {
        row["sale_year"]: row
        for row in rows
        if row["aggregation_level"] == "delivered_year"
    }
    assert set(years) == {"2011", "2012", "2014", "2015"}

    year_2012 = years["2012"]
    assert year_2012["sale_count"] == "4"
    assert year_2012["full_rate_sale_count"] == "0"
    assert year_2012["partial_award_sale_count"] == "2"
    assert year_2012["price_year_basis"] == "nominal_2012"
    assert year_2012["currency_basis"] == "CAD_nominal"
    assert "4 delivered sales" in year_2012["notes"]
    assert "0 full rates" in year_2012["notes"]
    assert "2 partial awards" in year_2012["notes"]
    assert "18 delivered sales" not in year_2012["notes"]

    year_2014 = years["2014"]
    assert year_2014["sale_count"] == "1"
    assert "1 delivered sale;" in year_2014["notes"]
    assert year_2014["price_year_basis"] == "nominal_2014"

    year_2015 = years["2015"]
    assert year_2015["sale_count"] == "7"
    assert "7 delivered sales" in year_2015["notes"]


def test_manifest_hashes_and_no_nonfinite_text(built: benchmark.BuildResult) -> None:
    manifest = json.loads((built.output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "not_estimated"
    assert manifest["interpretation"] == "purchased_vs_bgad_procurement_cost_spread"
    assert manifest["validation"]["status"] == "pass"
    for record in manifest["outputs"]:
        path = Path(record["path"])
        if not path.is_absolute():
            path = REPO_ROOT / path
        assert hashlib.sha256(path.read_bytes()).hexdigest() == record["sha256"]

    for path in built.output_dir.glob("*.csv"):
        for row in read_csv(path):
            for value in row.values():
                assert value.lower() not in {"nan", "inf", "-inf"}


def test_parse_finite_float_rejects_blank_and_nonfinite() -> None:
    for text in ("", "nan", "inf", "-inf", "n/a"):
        with pytest.raises(benchmark.BenchmarkError):
            benchmark.parse_finite_float(text, context="test")


def test_parse_strict_bool_rejects_non_binary() -> None:
    assert benchmark.parse_strict_bool("1", context="test") is True
    assert benchmark.parse_strict_bool("0", context="test") is False
    for text in ("true", "false", "yes", "2", ""):
        with pytest.raises(benchmark.BenchmarkError):
            benchmark.parse_strict_bool(text, context="test")


def test_require_columns_rejects_empty_and_missing(tmp_path: Path) -> None:
    path = tmp_path / "empty.csv"
    path.write_text("a,b\n", encoding="utf-8")
    with pytest.raises(benchmark.BenchmarkError):
        benchmark.require_columns(path, [], {"a"})

    rows = [{"a": "1"}]
    with pytest.raises(benchmark.BenchmarkError):
        benchmark.require_columns(path, rows, {"a", "b"})


def test_select_purchase_costs_rejects_duplicate_keys(
    survey_rows: tuple[benchmark.SurveyRow, ...],
) -> None:
    source = next(
        row
        for row in survey_rows
        if row.source_table == "T2" and row.species_group == "sepm"
    )
    feuillus = next(
        row
        for row in survey_rows
        if row.source_table == "T2" and row.species_group == "feuillus_durs"
    )
    duplicate = replace(
        feuillus,
        species_group=source.species_group,
        product_or_grade=source.product_or_grade,
    )
    rows = tuple(duplicate if row is feuillus else row for row in survey_rows)
    with pytest.raises(benchmark.BenchmarkError, match="duplicate T2"):
        benchmark.select_purchase_costs(rows)


def test_select_bgad_rejects_component_sum_and_unit(
    survey_rows: tuple[benchmark.SurveyRow, ...],
) -> None:
    target = next(
        row
        for row in survey_rows
        if row.source_table == "T20"
        and row.species_group == "sepm"
        and row.component == "procurement_total_before_transfers"
    )
    rows = tuple(
        replace(row, value=row.value + 1.0) if row is target else row
        for row in survey_rows
    )
    with pytest.raises(benchmark.BenchmarkError, match="cost components sum"):
        benchmark.select_bgad_synthesis(rows)

    bad_unit = replace(target, unit="CAD/ha")
    rows = tuple(bad_unit if row is target else row for row in survey_rows)
    with pytest.raises(benchmark.BenchmarkError, match="unexpected T20 unit"):
        benchmark.select_bgad_synthesis(rows)


def test_select_regional_rows_rejects_inconsistent_sample_n(
    survey_rows: tuple[benchmark.SurveyRow, ...],
) -> None:
    target = next(
        row
        for row in survey_rows
        if row.source_id == "regional_2022"
        and row.region_code == "R09"
        and row.component == "harvest"
    )
    bad = replace(target, sample_n=4)
    rows = tuple(bad if row is target else row for row in survey_rows)
    with pytest.raises(benchmark.BenchmarkError, match="inconsistent sample_n"):
        benchmark.select_regional_rows(rows)


@pytest.mark.parametrize(
    ("sale_id", "field", "value"),
    [
        ("SE201111009", "awarded", "true"),
        ("SE201111009", "volume_m3", "0"),
        ("SE201111009", "total_cad", ""),
        ("SE201111009", "price_cad_m3", "99.0000"),
        ("SE201111009", "currency_basis", "USD"),
        ("SE201111009", "species_scope", "SPRUCE"),
        ("SE201111009", "aggregation_level", "species"),
        ("SE201111009", "observed", "yes"),
        ("SE201111009", "partial_award", "1"),
        ("SE201111009", "reoffer_of_sale_id", "UNKNOWN"),
        ("SO201111011", "total_cad", "1"),
    ],
)
def test_parse_bmmb_rejects_invalid_rows(
    bmmb_dir: Path, sale_id: str, field: str, value: str
) -> None:
    path = bmmb_dir / "bmmb_delivered_sales.csv"
    rows = read_csv(path)
    target = next(row for row in rows if row["sale_id"] == sale_id)
    target[field] = value
    write_csv(path, rows)
    with pytest.raises(benchmark.BenchmarkError):
        benchmark.parse_bmmb_sales(path)


def test_parse_bmmb_rejects_duplicate_sale_id(bmmb_dir: Path) -> None:
    path = bmmb_dir / "bmmb_delivered_sales.csv"
    rows = read_csv(path)
    rows[1]["sale_id"] = rows[0]["sale_id"]
    write_csv(path, rows)
    with pytest.raises(benchmark.BenchmarkError, match="duplicate BMMB sale_id"):
        benchmark.parse_bmmb_sales(path)


def test_parse_bmmb_rejects_manifest_sale_count(bmmb_dir: Path) -> None:
    path = bmmb_dir / "bmmb_delivered_sales.csv"
    rows = read_csv(path)
    write_csv(path, rows[:-1])
    with pytest.raises(benchmark.BenchmarkError, match="does not match manifest"):
        benchmark.parse_bmmb_sales(path)


def test_parse_survey_rejects_manifest_row_count(tmp_path: Path) -> None:
    target = tmp_path / "qc_cost_survey_2022"
    target.mkdir()
    shutil.copy(benchmark.SURVEY_PATH, target / "qc_cost_revenue_survey.csv")
    shutil.copy(benchmark.SURVEY_PATH.with_name("manifest.json"), target / "manifest.json")
    path = target / "qc_cost_revenue_survey.csv"
    rows = read_csv(path)
    write_csv(path, rows[:-1])
    with pytest.raises(benchmark.BenchmarkError, match="does not match manifest"):
        benchmark.parse_survey_rows(path)
