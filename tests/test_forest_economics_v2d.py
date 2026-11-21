"""Tests for the v2d forest-economics evidence layer."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import shutil
from pathlib import Path

import pytest

from workflows.national.harvest import forest_economics_v2d as v2d

REPO_ROOT = Path(__file__).resolve().parents[1]
HARVEST_BASIS_DIR = REPO_ROOT / "data/derived/national/harvest_basis"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def mutated_price_path(tmp_path: Path, mutate) -> Path:
    rows = read_csv(HARVEST_BASIS_DIR / "bc_log_market/bc_log_market_prices.csv")
    mutate(rows)
    path = tmp_path / "bc_log_market_prices.csv"
    write_csv(path, rows)
    return path


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> v2d.BuildResult:
    output_dir = tmp_path_factory.mktemp("forest_economics_v2d")
    return v2d.build(output_dir=output_dir)


def test_build_writes_required_outputs_and_passes_checks(
    built: v2d.BuildResult,
) -> None:
    expected = {
        "forest_economics_v2d_summary.csv",
        "qc_procurement_reference.csv",
        "bc_revenue_benchmark.csv",
        "bc_cost_coverage.csv",
        "bmmb_reference.csv",
        "summary.json",
        "FOREST_ECONOMICS_V2D_REPORT.md",
        "manifest.json",
    }
    assert {path.name for path in built.output_dir.iterdir()} == expected
    assert len(built.checks) == 37
    assert all(check["status"] == "pass" for check in built.checks)


def test_summary_keeps_evidence_separate_and_no_net_value(
    built: v2d.BuildResult,
) -> None:
    rows = read_csv(built.output_dir / "forest_economics_v2d_summary.csv")
    assert len(rows) == 14
    assert {row["jurisdiction"] for row in rows} == {"QC", "BC"}
    assert {row["net_value_status"] for row in rows} == {"not_estimated"}
    assert {row["policy_grade"] for row in rows} == {"false"}
    assert {row["net_value_is_combined_national"] for row in rows} == {"false"}
    assert all(row["net_value_reason"] for row in rows)
    assert "net_value_cad_m3" not in rows[0]
    assert "net_value_before_transfers_min_cad_m3" not in rows[0]

    qc = [row for row in rows if row["jurisdiction"] == "QC"]
    bc = [row for row in rows if row["jurisdiction"] == "BC"]
    assert len(qc) == 5
    assert len(bc) == 9
    assert {row["evidence_tier"] for row in qc} == {
        "survey_procurement_cost",
        "survey_regional_procurement_cost",
        "benchmark_historical",
    }
    assert {
        row["evidence_tier"] for row in bc
    } == {
        "observed_price",
        "observed_stumpage",
        "appraisal_allowance",
        "not_available",
    }
    assert all(
        row["net_value_status"] == "not_estimated"
        and row["net_value_reason"] == "missing_logging_and_haul_cost"
        for row in bc
    )
    assert {
        row["summary_id"] for row in rows if row["evidence_tier"] == "observed_stumpage"
    } == {"bc_bcts_observed_stumpage"}

    teaching = next(row for row in rows if row["summary_id"] == "qc_bmmb_teaching")
    assert "Nominal 2014-2015" in teaching["notes"]
    assert "Nominal 2011-2015" not in teaching["notes"]


def test_qc_procurement_rows_are_cost_references_not_revenue(
    built: v2d.BuildResult,
) -> None:
    rows = read_csv(built.output_dir / "qc_procurement_reference.csv")
    assert len(rows) == 18
    assert {row["validation_status"] for row in rows} == {"validated_copy"}
    assert {row["policy_grade"] for row in rows} == {"false"}
    assert {row["net_value_status"] for row in rows} == {"not_estimated"}
    assert {row["interpretation"] for row in rows} == {
        "purchased_vs_bgad_procurement_cost_spread"
    }

    by_key = {row["source_record_key"]: row for row in rows}
    sep = by_key["qc_2022_sepm_total"]
    assert float(sep["purchase_cost_cad_m3"]) == pytest.approx(107.22)
    assert float(sep["bgad_procurement_before_transfers_cad_m3"]) == pytest.approx(81.40)
    assert float(sep["transfers_total_cad_m3"]) == pytest.approx(25.01)
    assert float(sep["bgad_procurement_after_transfers_cad_m3"]) == pytest.approx(106.41)
    assert float(sep["cost_spread_before_transfers_cad_m3"]) == pytest.approx(25.82)
    assert float(sep["cost_spread_after_transfers_cad_m3"]) == pytest.approx(0.81)
    assert sep["purchase_semantics"] == "buyer_side_procurement_cost_not_log_sale_revenue"

    poplar = by_key["qc_2022_peupliers_trituration"]
    assert poplar["confidentiality_flag"] == "three_respondents"
    assert poplar["three_respondent_flag"] == "true"
    assert "Three-respondent mean" in poplar["notes"]
    r09 = by_key["qc_2022_regional_sepm_R09"]
    assert r09["confidentiality_flag"] == "three_respondents"
    assert r09["three_respondent_flag"] == "true"
    assert "Three-respondent mean" in r09["notes"]

    regional_values = [
        row for row in rows if row["reference_type"] == "regional_conditional_cost_spread"
    ]
    assert len(regional_values) == 9
    for row in regional_values:
        assert float(row["reference_purchase_cad_m3"]) == pytest.approx(107.22)
        assert "net value" not in row["interpretation"]
        assert "residual" not in json.dumps(row, ensure_ascii=False).lower()

    for row in rows:
        for value in row.values():
            assert value.lower() not in {"nan", "inf", "-inf"}


def test_qc_and_bmmb_references_are_exact_source_copies(
    built: v2d.BuildResult,
) -> None:
    procurement_dir = HARVEST_BASIS_DIR / "procurement_benchmark"
    sources: dict[str, dict[str, str]] = {}
    for name, key in (
        ("qc_procurement_cost_benchmark.csv", "benchmark_id"),
        ("qc_procurement_regional_range.csv", "regional_benchmark_id"),
        ("bmmb_historical_benchmark.csv", "benchmark_id"),
    ):
        for row in read_csv(procurement_dir / name):
            sources[row[key]] = row

    for row in read_csv(built.output_dir / "qc_procurement_reference.csv"):
        source = sources[row["source_record_key"]]
        assert {
            field: source.get(field, "") for field in v2d.QC_PROCUREMENT_SOURCE_FIELDS
        } == {field: row[field] for field in v2d.QC_PROCUREMENT_SOURCE_FIELDS}

    for row in read_csv(built.output_dir / "bmmb_reference.csv"):
        source = sources[row["source_record_key"]]
        assert {
            field: source.get(field, "") for field in v2d.BMMB_SOURCE_FIELDS
        } == {field: row[field] for field in v2d.BMMB_SOURCE_FIELDS}


def test_bmmb_reference_reports_primary_teaching_and_secondary(
    built: v2d.BuildResult,
) -> None:
    rows = read_csv(built.output_dir / "bmmb_reference.csv")
    assert len(rows) == 3
    by_key = {row["source_record_key"]: row for row in rows}
    crown = by_key["bmmb_crown_public_forest_2011_2015"]
    teaching = by_key["bmmb_teaching_research_forest_2014_2015"]
    all_in = by_key["bmmb_all_delivered_2011_2015"]
    assert float(crown["weighted_rate_cad_m3"]) == pytest.approx(35.7547)
    assert float(teaching["weighted_rate_cad_m3"]) == pytest.approx(50.6266)
    assert float(all_in["weighted_rate_cad_m3"]) == pytest.approx(38.2940)
    assert crown["is_primary_benchmark"] == "true"
    assert teaching["is_primary_benchmark"] == "false"
    assert all_in["is_primary_benchmark"] == "false"
    for row in rows:
        assert row["currency_basis"] == "CAD_nominal"
        assert row["deflator_applied"] == "false"
        assert row["policy_grade"] == "false"
        assert row["net_value_status"] == "not_estimated"


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
    assert (
        sum(int(row["cell_count"]) for row in annual if row["report_basis"] == "1_month")
        == 7277
    )
    assert (
        sum(int(row["cell_count"]) for row in annual if row["report_basis"] == "3_month")
        == 8428
    )

    for row in rows:
        if row["range_status"] != "ok":
            assert row["price_min_cad_m3"] == ""
            continue
        minimum = float(row["price_min_cad_m3"])
        p25 = float(row["price_p25_cad_m3"])
        median = float(row["price_median_cad_m3"])
        p75 = float(row["price_p75_cad_m3"])
        maximum = float(row["price_max_cad_m3"])
        assert 1.0 <= minimum
        assert minimum <= p25 <= median <= p75 <= maximum
        assert maximum <= 1000.0

    period = [row for row in rows if row["aggregation_level"] == "series_period"]
    assert sum(int(row["zero_cell_count"]) for row in period) == 16
    assert sum(int(row["below_one_cell_count"]) for row in period) == 7
    assert sum(int(row["above_one_thousand_cell_count"]) for row in period) == 17

    benchmark_ids = [row["benchmark_id"] for row in rows]
    assert len(benchmark_ids) == len(set(benchmark_ids))
    assert all(
        f"_{row['period_year']}_" in row["benchmark_id"] for row in rows
    )


def test_interior_volume_reconciliation_excludes_aggregate_rows() -> None:
    price_rows = read_csv(HARVEST_BASIS_DIR / "bc_log_market/bc_log_market_prices.csv")
    volume_by_report: dict[str, float] = {}
    printed: dict[str, float] = {}
    for row in price_rows:
        if row["region"] != "interior":
            continue
        if row["report_total_volume_m3"]:
            printed[row["report_id"]] = float(row["report_total_volume_m3"])
        if row["is_aggregate"] == "false" and row["volume_m3"]:
            volume_by_report[row["report_id"]] = (
                volume_by_report.get(row["report_id"], 0.0) + float(row["volume_m3"])
            )
    assert len(printed) == 169
    assert all(
        math.isclose(volume_by_report.get(report_id, 0.0), total, abs_tol=2.0)
        for report_id, total in printed.items()
    )
    assert sum(1 for row in price_rows if row["is_aggregate"] == "true") == 890


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


def test_manifest_hashes_and_label_contract(built: v2d.BuildResult) -> None:
    manifest = json.loads((built.output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["dataset"] == "forest_economics_v2d"
    assert manifest["status"] == "not_estimated"
    assert manifest["validation"]["status"] == "pass"
    assert manifest["net_value"]["qc_status"] == "not_estimated"
    assert manifest["net_value"]["bc_status"] == "not_estimated"
    assert manifest["net_value"]["bc_reason"] == "missing_logging_and_haul_cost"
    assert manifest["net_value"]["combined_national"] is False
    assert manifest["net_value"]["partial_bc_computed"] is False

    for record in manifest["outputs"]:
        path = Path(record["path"])
        if not path.is_absolute():
            path = REPO_ROOT / path
        assert hashlib.sha256(path.read_bytes()).hexdigest() == record["sha256"]

    committed = json.loads(
        (HARVEST_BASIS_DIR / "forest_economics_v2d/manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert committed["validation"]["status"] == "pass"
    for record in committed["outputs"]:
        path = Path(record["path"])
        if not path.is_absolute():
            path = REPO_ROOT / path
        assert hashlib.sha256(path.read_bytes()).hexdigest() == record["sha256"]

    for path in built.output_dir.glob("*.csv"):
        for row in read_csv(path):
            for value in row.values():
                assert value.lower() not in {"nan", "inf", "-inf"}


def test_format_cell_rejects_nonfinite_values() -> None:
    with pytest.raises(v2d.ForestEconomicsV2DError):
        v2d.format_cell(float("nan"))
    with pytest.raises(v2d.ForestEconomicsV2DError):
        v2d.format_cell(float("inf"))


def test_percentile_is_deterministic_for_small_samples() -> None:
    assert v2d.percentile([5.0], 0.25) == 5.0
    assert v2d.percentile([1.0, 3.0], 0.5) == 2.0
    assert v2d.percentile([1.0, 2.0, 3.0, 4.0], 0.25) == 1.75


@pytest.fixture()
def procurement_copy(tmp_path: Path) -> Path:
    target = tmp_path / "procurement_benchmark"
    shutil.copytree(v2d.PROCUREMENT_DIR, target)
    return target


def test_parse_price_cells_rejects_uppercase_aggregate_flag(tmp_path: Path) -> None:
    def mutate(rows: list[dict[str, str]]) -> None:
        rows[0]["is_aggregate"] = "True"

    with pytest.raises(v2d.ForestEconomicsV2DError, match="invalid is_aggregate"):
        v2d.parse_price_cells(mutated_price_path(tmp_path, mutate))


def test_parse_price_cells_rejects_aggregate_with_volume(tmp_path: Path) -> None:
    def mutate(rows: list[dict[str, str]]) -> None:
        aggregate = next(row for row in rows if row["is_aggregate"] == "true")
        aggregate["volume_m3"] = "10.0"

    with pytest.raises(v2d.ForestEconomicsV2DError, match="aggregate row carries product volume"):
        v2d.parse_price_cells(mutated_price_path(tmp_path, mutate))


def test_parse_price_cells_rejects_aggregate_outside_interior_average(
    tmp_path: Path,
) -> None:
    def mutate(rows: list[dict[str, str]]) -> None:
        aggregate = next(row for row in rows if row["is_aggregate"] == "true")
        aggregate["end_use"] = "sawlog"

    with pytest.raises(v2d.ForestEconomicsV2DError, match="aggregate flag outside"):
        v2d.parse_price_cells(mutated_price_path(tmp_path, mutate))


def test_parse_price_cells_rejects_plausibility_mismatch(tmp_path: Path) -> None:
    def mutate(rows: list[dict[str, str]]) -> None:
        zero = next(row for row in rows if row["price_plausibility"] == "zero_cad_m3")
        zero["price_plausibility"] = "ok"

    with pytest.raises(v2d.ForestEconomicsV2DError, match="plausibility flag mismatch"):
        v2d.parse_price_cells(mutated_price_path(tmp_path, mutate))


def test_parse_price_cells_rejects_duplicate_key(tmp_path: Path) -> None:
    def mutate(rows: list[dict[str, str]]) -> None:
        for field in ("report_id", "species", "grade", "end_use"):
            rows[1][field] = rows[0][field]

    with pytest.raises(v2d.ForestEconomicsV2DError, match="duplicate price cell key"):
        v2d.parse_price_cells(mutated_price_path(tmp_path, mutate))


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("policy_grade", True, "not policy_grade=false"),
        ("net_value_status", "estimated", "net-value status changed"),
    ],
)
def test_build_qc_reference_rejects_manifest_label_tampering(
    procurement_copy: Path, field: str, value: object, match: str
) -> None:
    manifest_path = procurement_copy / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["labels"][field] = value
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(v2d.ForestEconomicsV2DError, match=match):
        v2d.build_qc_reference(procurement_copy)


def test_build_qc_reference_rejects_tampered_source_hash(
    procurement_copy: Path,
) -> None:
    source = procurement_copy / "qc_procurement_cost_benchmark.csv"
    source.write_text(
        source.read_text(encoding="utf-8").replace("107.2200", "107.2300", 1),
        encoding="utf-8",
    )
    with pytest.raises(v2d.ForestEconomicsV2DError, match="manifest hash mismatch"):
        v2d.build_qc_reference(procurement_copy)


def test_build_cost_coverage_rejects_missing_logging_gap() -> None:
    costs = v2d.parse_appraisal_costs(v2d.BC_APPRAISAL_DIR / "bc_appraisal_costs.csv")
    coverage = tuple(
        entry
        for entry in v2d.parse_appraisal_coverage(
            v2d.BC_APPRAISAL_DIR / "bc_appraisal_coverage.csv"
        )
        if not (
            entry.component_group == "logging"
            and entry.extraction_status == "not_published"
        )
    )
    manifest = json.loads(
        (v2d.BC_APPRAISAL_DIR / "manifest.json").read_text(encoding="utf-8")
    )
    with pytest.raises(v2d.ForestEconomicsV2DError, match="logging=not_published"):
        v2d.build_cost_coverage(costs, coverage, manifest)


def test_build_bcts_sales_rejects_mirror_sign_mismatch() -> None:
    crown, forest_sector, _ = v2d.parse_bcts_ledger(
        v2d.BCTS_DIR / "bcts_bid_ledger.csv"
    )
    results = v2d.parse_bcts_results(v2d.BCTS_DIR / "bcts_bid_results.csv")
    sale_id = min(crown)
    mirror = [dict(row) for row in forest_sector[sale_id]]
    mirror[0]["sign"] = "1" if mirror[0]["sign"] == "-1" else "-1"
    tampered = dict(forest_sector)
    tampered[sale_id] = mirror
    with pytest.raises(v2d.ForestEconomicsV2DError, match="mirror sign mismatch"):
        v2d.build_bcts_sales(crown, tampered, results)
