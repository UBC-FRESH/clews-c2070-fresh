"""Tests for the v2d net-value evidence layer."""

from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path

import pytest

from workflows.national.harvest import net_value_v2d as v2d

REPO_ROOT = Path(__file__).resolve().parents[1]
HARVEST_BASIS_DIR = REPO_ROOT / "data/derived/national/harvest_basis"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> v2d.BuildResult:
    output_dir = tmp_path_factory.mktemp("net_value_v2d")
    return v2d.build(output_dir=output_dir)


def test_build_writes_required_outputs_and_passes_checks(
    built: v2d.BuildResult,
) -> None:
    expected = {
        "v2d_net_value_summary.csv",
        "bc_revenue_benchmark.csv",
        "bc_observed_stumpage.csv",
        "bc_cost_coverage.csv",
        "qc_reference.csv",
        "summary.json",
        "NET_VALUE_V2D_REPORT.md",
        "manifest.json",
    }
    assert {path.name for path in built.output_dir.iterdir()} == expected
    assert len(built.checks) == 29
    assert all(check["status"] == "pass" for check in built.checks)


def test_summary_keeps_evidence_separate_and_bc_not_estimated(
    built: v2d.BuildResult,
) -> None:
    rows = read_csv(built.output_dir / "v2d_net_value_summary.csv")
    assert len(rows) == 13
    assert {row["jurisdiction"] for row in rows} == {"QC", "BC"}
    assert all(row["policy_grade"] == "false" for row in rows)
    assert all(row["net_value_is_combined_national"] == "false" for row in rows)

    qc = [row for row in rows if row["jurisdiction"] == "QC"]
    bc = [row for row in rows if row["jurisdiction"] == "BC"]
    assert len(qc) == 5
    assert len(bc) == 8
    assert {row["evidence_tier"] for row in qc} == {
        "survey_benchmark",
        "benchmark_historical",
    }
    assert {row["evidence_tier"] for row in bc} == {
        "observed_price",
        "observed_stumpage",
        "appraisal_allowance",
    }
    assert all(
        row["net_value_status"] == "not_estimated"
        and row["net_value_reason"] == "missing_logging_and_haul_cost"
        for row in bc
    )
    assert {
        row["summary_id"] for row in rows if row["evidence_tier"] == "observed_stumpage"
    } == {"bc_bcts_observed_stumpage"}


def test_bc_price_benchmark_keeps_vintages_and_product_cells_separate(
    built: v2d.BuildResult,
) -> None:
    rows = read_csv(built.output_dir / "bc_revenue_benchmark.csv")
    product = [row for row in rows if row["cell_role"] == "product_cell"]
    aggregate = [row for row in rows if row["cell_role"] == "reported_weighted_average"]
    assert len(rows) == 1852
    assert len(product) == 1748
    assert len(aggregate) == 104
    assert all(row["is_aggregate"] == "false" for row in product)
    assert all(row["is_aggregate"] == "true" for row in aggregate)
    assert all(row["end_use"] == "weighted_average" for row in aggregate)
    assert {row["report_basis"] for row in rows} == {"1_month", "3_month"}
    assert all("not additive" in row["vintage_handling"] for row in rows)

    annual = [row for row in rows if row["aggregation_level"] == "annual"]
    assert all(int(row["report_count"]) <= 12 for row in annual)
    assert sum(int(row["cell_count"]) for row in annual if row["report_basis"] == "1_month") == 7277
    assert sum(int(row["cell_count"]) for row in annual if row["report_basis"] == "3_month") == 8428

    for row in rows:
        if row["range_status"] != "ok":
            assert row["price_min_cad_m3"] == ""
            continue
        minimum = float(row["price_min_cad_m3"])
        p25 = float(row["price_p25_cad_m3"])
        median = float(row["price_median_cad_m3"])
        p75 = float(row["price_p75_cad_m3"])
        maximum = float(row["price_max_cad_m3"])
        assert minimum > 0
        assert minimum <= p25 <= median <= p75 <= maximum
        assert maximum <= 1000

    assert sum(int(row["zero_cell_count"]) for row in rows if row["aggregation_level"] == "series_period") == 16
    assert sum(int(row["below_one_cell_count"]) for row in rows if row["aggregation_level"] == "series_period") == 7
    assert sum(
        int(row["above_one_thousand_cell_count"])
        for row in rows
        if row["aggregation_level"] == "series_period"
    ) == 17


def test_interior_volume_reconciliation_excludes_aggregate_rows() -> None:
    price_rows = read_csv(HARVEST_BASIS_DIR / "bc_log_market/bc_log_market_prices.csv")
    volume_by_report: dict[str, float] = {}
    printed: dict[str, float] = {}
    for row in price_rows:
        if row["region"] != "interior":
            continue
        if row["report_total_volume_m3"]:
            printed[row["report_id"]] = float(row["report_total_volume_m3"])
        if row["is_aggregate"] == "False" and row["volume_m3"]:
            volume_by_report[row["report_id"]] = (
                volume_by_report.get(row["report_id"], 0.0) + float(row["volume_m3"])
            )
    assert len(printed) == 169
    assert all(
        math.isclose(volume_by_report.get(report_id, 0.0), total, abs_tol=2.0)
        for report_id, total in printed.items()
    )
    assert sum(1 for row in price_rows if row["is_aggregate"] == "True") == 890


def test_qc_reference_is_a_validated_exact_copy(built: v2d.BuildResult) -> None:
    references = read_csv(built.output_dir / "qc_reference.csv")
    assert len(references) == 14
    assert all(row["validation_status"] == "validated_copy" for row in references)
    assert all(row["policy_grade"] == "false" for row in references)

    provincial = read_csv(
        HARVEST_BASIS_DIR / "net_value_benchmark/qc_net_value_benchmark.csv"
    )
    provincial_by_id = {row["benchmark_id"]: row for row in provincial}
    for reference in references:
        if reference["reference_type"] != "provincial_residual":
            continue
        source = provincial_by_id[reference["source_record_key"]]
        for field in (
            "delivered_purchase_cad_m3",
            "procurement_before_transfers_cad_m3",
            "transfers_total_cad_m3",
            "procurement_after_transfers_cad_m3",
            "residual_before_transfers_cad_m3",
            "residual_after_transfers_cad_m3",
        ):
            assert reference[field] == source[field]

    regional = read_csv(
        HARVEST_BASIS_DIR / "net_value_benchmark/qc_net_value_regional_range.csv"
    )
    regional_by_id = {row["regional_benchmark_id"]: row for row in regional}
    for reference in references:
        if reference["reference_type"] != "regional_conditional_range":
            continue
        source = regional_by_id[reference["source_record_key"]]
        assert reference["delivered_purchase_cad_m3"] == source["reference_purchase_cad_m3"]
        for field in (
            "procurement_before_transfers_cad_m3",
            "transfers_total_cad_m3",
            "procurement_after_transfers_cad_m3",
            "residual_before_transfers_cad_m3",
            "residual_after_transfers_cad_m3",
        ):
            assert reference[field] == source[field]

    sensitivity = read_csv(
        HARVEST_BASIS_DIR / "net_value_benchmark/qc_net_value_sensitivity.csv"
    )
    sensitivity_by_id = {row["case_id"]: row for row in sensitivity}
    for reference in references:
        if reference["reference_type"] != "screening_sensitivity":
            continue
        source = sensitivity_by_id[reference["source_record_key"]]
        assert reference["residual_before_transfers_cad_m3"] == source[
            "residual_before_transfers_cad_m3"
        ]
        assert reference["value_before_transfers_mcad"] == source[
            "qc_net_value_before_transfers_mcad"
        ]
        assert reference["value_after_transfers_mcad"] == source[
            "qc_net_value_after_transfers_mcad"
        ]

    sep = next(
        reference
        for reference in references
        if reference["source_record_key"] == "qc_2022_qc_sepm_total"
    )
    assert sep["delivered_purchase_cad_m3"] == "107.2200"
    assert sep["residual_before_transfers_cad_m3"] == "25.8200"
    assert sep["residual_after_transfers_cad_m3"] == "0.8100"

    bmmb_source = read_csv(
        HARVEST_BASIS_DIR / "net_value_benchmark/bmmb_historical_benchmark.csv"
    )
    bmmb_overall = next(
        row for row in bmmb_source if row["aggregation_level"] == "delivered_all_years"
    )
    bmmb_reference = next(
        reference
        for reference in references
        if reference["reference_type"] == "historical_revenue_benchmark"
    )
    for field in (
        "sale_count",
        "weighted_rate_cad_m3",
        "median_rate_cad_m3",
        "min_rate_cad_m3",
        "max_rate_cad_m3",
    ):
        assert bmmb_reference[field] == bmmb_overall[field]


def test_bc_cost_coverage_labels_allowances_and_documents_the_gap(
    built: v2d.BuildResult,
) -> None:
    rows = read_csv(built.output_dir / "bc_cost_coverage.csv")
    by_component = {row["component"]: row for row in rows}
    assert set(by_component) == {
        "road",
        "bridge",
        "culvert",
        "silviculture",
        "specified_operations",
        "tenure_obligation",
        "logging",
        "truck_haul",
        "estimated_winning_bid",
        "full_bc_net_value",
    }
    for component in (
        "road",
        "bridge",
        "culvert",
        "silviculture",
        "specified_operations",
        "tenure_obligation",
    ):
        assert by_component[component]["covered"] == "true"
        assert by_component[component]["evidence_tier"] == "appraisal_allowance"
        assert by_component[component]["appraisal_allowance"] == "true"
        assert by_component[component]["observed_cost"] == "false"

    assert by_component["logging"]["coverage_status"] == "not_published"
    assert by_component["truck_haul"]["coverage_status"] == "partial_appraisal_allowance"
    assert by_component["truck_haul"]["covered"] == "false"
    assert by_component["estimated_winning_bid"]["coverage_status"] == "not_published_as_cost"
    assert by_component["full_bc_net_value"]["coverage_status"] == "not_estimated"
    assert by_component["full_bc_net_value"]["missing_reason"] == "missing_logging_and_haul_cost"
    assert all(row["observed_cost"] == "false" for row in rows)
    assert "total_cost_cad_m3" not in rows[0]
    assert "net_value_cad_m3" not in rows[0]


def test_bcts_stumpage_is_observed_standing_timber(built: v2d.BuildResult) -> None:
    rows = read_csv(built.output_dir / "bc_observed_stumpage.csv")
    assert len(rows) == 51
    assert sum(1 for row in rows if row["total_basis"] == "full_winning_bid") == 49
    assert sum(1 for row in rows if row["total_basis"] == "premium_only_lower_bound") == 2
    assert all(row["evidence_tier"] == "observed_stumpage" for row in rows)
    assert all(row["price_basis"] == "standing_timber" for row in rows)
    assert all(row["net_value_status"] == "not_estimated" for row in rows)
    assert all(float(row["stumpage_total_cad_m3"]) >= 0 for row in rows)
    for row in rows:
        if row["total_basis"] == "premium_only_lower_bound":
            assert row["stumpage_base_cad_m3"] == ""
            assert float(row["stumpage_total_cad_m3"]) == pytest.approx(
                float(row["reported_bonus_cad_m3"])
            )

    summary = json.loads((built.output_dir / "summary.json").read_text(encoding="utf-8"))
    stumpage = summary["bc_observed_stumpage"]
    assert stumpage["sale_count"] == 51
    assert stumpage["full_bid_count"] == 49
    assert stumpage["lower_bound_count"] == 2
    assert stumpage["volume_weighted_stumpage_cad_m3"] == pytest.approx(42.7913, abs=0.001)
    assert stumpage["bid_is_not_delivered_revenue"] is True


def test_manifest_hashes_and_no_nonfinite_text(built: v2d.BuildResult) -> None:
    manifest = json.loads((built.output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["validation"]["status"] == "pass"
    assert manifest["net_value"]["bc_status"] == "not_estimated"
    assert manifest["net_value"]["bc_reason"] == "missing_logging_and_haul_cost"
    assert manifest["net_value"]["combined_national"] is False

    for record in manifest["outputs"]:
        path = Path(record["path"])
        if not path.is_absolute():
            path = (built.output_dir / path).resolve()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == record["sha256"]

    for path in built.output_dir.glob("*.csv"):
        for row in read_csv(path):
            for value in row.values():
                assert value.lower() not in {"nan", "inf", "-inf"}


def test_format_cell_rejects_nonfinite_values() -> None:
    with pytest.raises(v2d.NetValueV2DError):
        v2d.format_cell(float("nan"))
    with pytest.raises(v2d.NetValueV2DError):
        v2d.format_cell(float("inf"))


def test_percentile_is_deterministic_for_small_samples() -> None:
    assert v2d.percentile([5.0], 0.25) == 5.0
    assert v2d.percentile([1.0, 3.0], 0.5) == 2.0
    assert v2d.percentile([1.0, 2.0, 3.0, 4.0], 0.25) == 1.75
