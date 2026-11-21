"""Audit-only tests for the deprecated QC 2022 net-value benchmark.

The artifact and its ``benchmark_only``/M CAD sensitivity framing are withdrawn.
These tests exist only to preserve the historical audit trail. Current evidence
is built by ``procurement_benchmark_v1.py`` and ``forest_economics_v2d.py``.
"""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pytest

from workflows.national.harvest import net_value_benchmark_v1 as benchmark

REPO_ROOT = Path(__file__).resolve().parents[1]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> benchmark.BuildResult:
    output_dir = tmp_path_factory.mktemp("net_value_benchmark")
    return benchmark.build(output_dir=output_dir)


def test_builder_carries_deprecation_banner() -> None:
    source = (
        REPO_ROOT / "workflows/national/harvest/net_value_benchmark_v1.py"
    ).read_text(encoding="utf-8")
    assert source.startswith('"""DEPRECATED')
    assert "procurement_benchmark_v1.py" in source
    assert "forest_economics_v2d.py" in source

    pointer = (
        REPO_ROOT
        / "data/derived/national/harvest_basis/net_value_benchmark/DEPRECATED.md"
    )
    assert pointer.is_file()
    assert "procurement_benchmark" in pointer.read_text(encoding="utf-8")


def test_build_writes_required_outputs_and_passes_checks(
    built: benchmark.BuildResult,
) -> None:
    expected = {
        "qc_net_value_benchmark.csv",
        "qc_net_value_regional_range.csv",
        "bmmb_historical_benchmark.csv",
        "qc_net_value_sensitivity.csv",
        "summary.json",
        "NET_VALUE_BENCHMARK_REPORT.md",
        "manifest.json",
    }
    assert {path.name for path in built.output_dir.iterdir()} == expected
    assert built.checks
    assert all(check["status"] == "pass" for check in built.checks)


def test_provincial_residuals_are_recomputed_before_and_after_transfers(
    built: benchmark.BuildResult,
) -> None:
    rows = {
        row["benchmark_id"]: row
        for row in read_csv(built.output_dir / "qc_net_value_benchmark.csv")
    }

    sep = rows["qc_2022_qc_sepm_total"]
    assert float(sep["residual_before_transfers_cad_m3"]) == pytest.approx(25.82)
    assert float(sep["residual_after_transfers_cad_m3"]) == pytest.approx(0.81)
    assert float(sep["procurement_before_transfers_cad_m3"]) + float(
        sep["transfers_total_cad_m3"]
    ) == pytest.approx(float(sep["procurement_after_transfers_cad_m3"]))

    hardwood = rows["qc_2022_qc_feuillus_durs_sciage"]
    assert float(hardwood["residual_before_transfers_cad_m3"]) == pytest.approx(5.69)
    assert float(hardwood["residual_after_transfers_cad_m3"]) == pytest.approx(-14.53)

    poplar = rows["qc_2022_qc_peupliers_trituration"]
    assert float(poplar["residual_before_transfers_cad_m3"]) == pytest.approx(-7.27)
    assert float(poplar["residual_after_transfers_cad_m3"]) == pytest.approx(-9.45)

    missing = rows["qc_2022_qc_pins_total"]
    assert missing["residual_status"] == "not_computable_missing_synthesis_procurement"
    assert missing["residual_before_transfers_cad_m3"] == ""
    assert missing["residual_after_transfers_cad_m3"] == ""
    assert missing["three_respondent_flag"] == "true"


def test_every_row_is_labelled_benchmark_only(built: benchmark.BuildResult) -> None:
    provincial = read_csv(built.output_dir / "qc_net_value_benchmark.csv")
    regional = read_csv(built.output_dir / "qc_net_value_regional_range.csv")
    bmmb = read_csv(built.output_dir / "bmmb_historical_benchmark.csv")
    sensitivity = read_csv(built.output_dir / "qc_net_value_sensitivity.csv")

    assert {row["evidence"] for row in provincial} == {"survey_benchmark"}
    assert {row["evidence"] for row in regional} == {"survey_benchmark"}
    assert {row["evidence"] for row in sensitivity} == {"survey_benchmark"}
    assert {row["evidence"] for row in bmmb} == {"benchmark_historical"}

    for rows in (provincial, regional, bmmb, sensitivity):
        assert {row["policy_grade"] for row in rows} == {"false"}
        assert {row["net_value_status"] for row in rows} == {"benchmark_only"}


def test_regional_range_uses_provincial_reference_price_and_flags_r09(
    built: benchmark.BuildResult,
) -> None:
    rows = read_csv(built.output_dir / "qc_net_value_regional_range.csv")
    values = [row for row in rows if row["statistic"] == "region_value"]
    assert len(values) == 7

    by_region = {row["region_code"]: row for row in values}
    assert by_region["R09"]["sample_n"] == "3"
    assert by_region["R09"]["three_respondent_flag"] == "true"
    assert float(by_region["R09"]["residual_after_transfers_cad_m3"]) == pytest.approx(
        -10.88
    )

    before = [float(row["procurement_before_transfers_cad_m3"]) for row in values]
    after = [float(row["procurement_after_transfers_cad_m3"]) for row in values]
    residual_before = [float(row["residual_before_transfers_cad_m3"]) for row in values]
    residual_after = [float(row["residual_after_transfers_cad_m3"]) for row in values]
    assert (min(before), max(before)) == pytest.approx((58.73, 97.07))
    assert (min(after), max(after)) == pytest.approx((78.78, 118.10))
    assert (min(residual_before), max(residual_before)) == pytest.approx((10.15, 48.49))
    assert (min(residual_after), max(residual_after)) == pytest.approx((-10.88, 28.44))

    bound_rows = {row["statistic"]: row for row in rows if row["statistic"] != "region_value"}
    assert float(bound_rows["min"]["procurement_before_transfers_cad_m3"]) == pytest.approx(
        58.73
    )
    assert float(bound_rows["max"]["procurement_after_transfers_cad_m3"]) == pytest.approx(
        118.10
    )


def test_bmmb_is_a_separate_nominal_historical_benchmark(
    built: benchmark.BuildResult,
) -> None:
    rows = read_csv(built.output_dir / "bmmb_historical_benchmark.csv")
    overall = next(row for row in rows if row["aggregation_level"] == "delivered_all_years")
    year_2012 = next(row for row in rows if row["sale_year"] == "2012")

    assert overall["sale_count"] == "18"
    assert overall["full_rate_sale_count"] == "11"
    assert overall["partial_award_sale_count"] == "2"
    assert float(overall["weighted_rate_cad_m3"]) == pytest.approx(38.2940)
    assert float(overall["median_rate_cad_m3"]) == pytest.approx(36.6668)
    assert overall["currency_basis"] == "CAD_nominal_2011_2015"
    assert overall["deflator_applied"] == "false"
    assert overall["grade_split"] == "false"
    assert overall["species_allocation"] == "false"
    assert "no species or grade split" in overall["notes"]

    assert year_2012["full_rate_sale_count"] == "0"
    assert year_2012["weighted_rate_cad_m3"] == ""
    assert year_2012["median_rate_cad_m3"] == ""
    assert float(year_2012["partial_lower_bound_min_cad_m3"]) == pytest.approx(10.6588)
    assert float(year_2012["partial_lower_bound_max_cad_m3"]) == pytest.approx(39.0211)


def test_qc_sensitivity_is_computed_from_mapped_allocation_volumes(
    built: benchmark.BuildResult,
) -> None:
    rows = {
        row["case_id"]: row
        for row in read_csv(built.output_dir / "qc_net_value_sensitivity.csv")
    }
    expected = {
        "100m": (22_184_137.0, 572.7944, 17.9692),
        "119m": (26_674_334.0, 688.7313, 21.6062),
        "150m": (34_115_200.0, 880.8545, 27.6333),
    }
    for case_id, (volume, before, after) in expected.items():
        row = rows[case_id]
        assert float(row["qc_harvest_m3"]) == pytest.approx(volume)
        assert float(row["qc_net_value_before_transfers_mcad"]) == pytest.approx(before)
        assert float(row["qc_net_value_after_transfers_mcad"]) == pytest.approx(after)
        assert row["evidence"] == "survey_benchmark"
        assert row["policy_grade"] == "false"
        assert row["net_value_status"] == "benchmark_only"


def test_manifest_hashes_and_no_nonfinite_text(built: benchmark.BuildResult) -> None:
    manifest = json.loads((built.output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "benchmark_only"
    assert manifest["validation"]["status"] == "pass"
    for record in manifest["outputs"]:
        path = Path(record["path"])
        if not path.is_absolute():
            path = REPO_ROOT / path
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert digest == record["sha256"]

    for path in built.output_dir.glob("*.csv"):
        for row in read_csv(path):
            for value in row.values():
                assert value.lower() not in {"nan", "inf", "-inf"}


def test_format_cell_rejects_nonfinite_values() -> None:
    with pytest.raises(benchmark.BenchmarkError):
        benchmark.format_cell(float("nan"))
    with pytest.raises(benchmark.BenchmarkError):
        benchmark.format_cell(float("inf"))
