"""Contract, conservation and provenance tests for the 13-jurisdiction pilot."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from workflows.national.bucking.national_bucking_pilot_v1 import (
    DEFAULT_OUTPUT_DIR,
    N_TREES_PER_PROVINCE,
    PROVINCES,
    assert_no_unexpected_deletions,
    generate_pilot,
)
from workflows.national.bucking.pilot_schema import EVIDENCE_TIERS, weakest_evidence

OUTPUT_FILES = (
    "README.md",
    "schema.md",
    "tree_list.csv",
    "bucked_logs.csv",
    "residue_ledger.csv",
    "reconciliation.csv",
    "province_summary.csv",
    "coefficient_comparison.csv",
    "summary.json",
    "report.md",
    "taper_dispatch.csv",
    "sort_rules.csv",
    "manifest.json",
)


@pytest.fixture(scope="module")
def pilot(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict]:
    output = tmp_path_factory.mktemp("national_bucking_pilot_v1")
    manifest = generate_pilot(output)
    return output, manifest


def _read(output: Path, name: str) -> pd.DataFrame:
    return pd.read_csv(output / name)


def test_output_files_exist(pilot):
    output, manifest = pilot
    for name in OUTPUT_FILES:
        assert (output / name).exists(), name
    for province in PROVINCES:
        assert (output / "tree_lists" / f"{province}.csv").exists(), province
    assert manifest["scope"] == "representative_synthetic_pilot"
    assert manifest["national_claim"] is False
    assert manifest["counts"]["jurisdictions"] == 13
    assert manifest["counts"]["trees"] == 13 * N_TREES_PER_PROVINCE


def test_thirteen_provinces_and_ten_trees_each(pilot):
    output, _ = pilot
    trees = _read(output, "tree_list.csv")
    assert set(trees["jurisdiction"]) == set(PROVINCES)
    counts = trees.groupby("jurisdiction").size()
    assert set(counts.index) == set(PROVINCES)
    assert (counts == N_TREES_PER_PROVINCE).all()
    assert trees["tree_id"].is_unique
    assert len(trees) == 13 * N_TREES_PER_PROVINCE


def test_volume_and_residue_balance(pilot):
    output, manifest = pilot
    logs = _read(output, "bucked_logs.csv")
    residue = _read(output, "residue_ledger.csv")
    reconciliation = _read(output, "reconciliation.csv")
    province = _read(output, "province_summary.csv")

    for row in residue.itertuples(index=False):
        tree_logs = logs[logs["tree_id"] == row.tree_id]
        assert abs(tree_logs["volume_m3"].sum() - row.recovered_log_m3) < 1e-9
        assert (
            abs(
                tree_logs["net_volume_m3"].sum()
                + tree_logs["defect_deduction_m3"].sum()
                - row.recovered_log_m3
            )
            < 1e-9
        )
        assert (
            abs(row.recovered_log_m3 + row.residue_volume_m3 - row.taper_total_m3)
            < 1e-8
        )
        assert abs(row.balance_error_m3) < 1e-8

    for row in reconciliation.itertuples(index=False):
        assert abs(row.recovered_gross_m3 + row.residue_m3 - row.taper_total_m3) < 1e-8
        assert abs(row.recovered_net_m3 + row.defect_m3 - row.recovered_gross_m3) < 1e-8

    assert abs(province["recovered_gross_m3"].sum() - logs["volume_m3"].sum()) < 1e-8
    assert abs(province["residue_m3"].sum() - residue["residue_volume_m3"].sum()) < 1e-8
    assert (
        abs(province["taper_total_m3"].sum() - residue["taper_total_m3"].sum()) < 1e-8
    )

    totals = manifest["totals"]
    assert abs(totals["recovered_gross_m3"] - logs["volume_m3"].sum()) < 1e-8
    assert abs(totals["residue_m3"] - residue["residue_volume_m3"].sum()) < 1e-8
    assert totals["balance_error_m3"] < 1e-8
    assert totals["net_plus_defect_error_m3"] < 1e-8


def test_taper_evidence_and_dispatch(pilot):
    output, _ = pilot
    trees = _read(output, "tree_list.csv")
    logs = _read(output, "bucked_logs.csv")
    residue = _read(output, "residue_ledger.csv")
    dispatch = _read(output, "taper_dispatch.csv")

    assert set(logs["taper_evidence"]).issubset(set(EVIDENCE_TIERS))
    assert (logs["taper_evidence"] == "published_table").all()
    assert (residue["taper_evidence"] == "published_table").all()
    assert (logs["taper_source"].str.len() > 0).all()
    assert (logs["taper_id"].str.len() > 0).all()
    assert (trees["taper_id"].str.len() > 0).all()

    local = trees[trees["jurisdiction"].isin(["BC", "QC"])]
    assert (local["taper_status"] == "local_published").all()
    assert (
        local[local["jurisdiction"] == "QC"]["taper_id"].str.startswith(
            "QC-SCHNEIDER2013-"
        )
    ).all()
    assert (
        local[local["jurisdiction"] == "BC"]["taper_id"].str.startswith("BC-KOZAK2004-")
    ).all()

    donor = trees[~trees["jurisdiction"].isin(["BC", "QC"])]
    assert (donor["taper_status"] == "donor_published").all()
    assert donor["taper_id"].str.contains("-DONOR-").all()
    assert set(donor["taper_jurisdiction"]) == {"BC", "QC"}
    assert (trees["taper_jurisdiction"].isin(["BC", "QC"])).all()

    assert set(dispatch["tree_id"]) == set(trees["tree_id"])
    assert (dispatch["taper_evidence"] == "published_table").all()
    assert dispatch["taper_status"].isin({"local_published", "donor_published"}).all()
    assert dispatch["taper_proxy"].isin([True, False]).all()
    assert set(dispatch["source_jurisdiction"]) == {"BC", "QC"}


def test_no_nan_or_negative(pilot):
    output, _ = pilot
    for name in (
        "tree_list.csv",
        "bucked_logs.csv",
        "residue_ledger.csv",
        "reconciliation.csv",
        "province_summary.csv",
        "coefficient_comparison.csv",
        "taper_dispatch.csv",
    ):
        frame = _read(output, name)
        numeric = frame.select_dtypes(include="number")
        assert not numeric.isna().any().any(), name
        assert (numeric >= 0.0).all().all(), name
        assert frame.notna().all().all(), name


def test_provenance_labels_and_no_official_grade(pilot):
    output, manifest = pilot
    trees = _read(output, "tree_list.csv")
    logs = _read(output, "bucked_logs.csv")
    residue = _read(output, "residue_ledger.csv")
    reconciliation = _read(output, "reconciliation.csv")
    province = _read(output, "province_summary.csv")
    comparison = _read(output, "coefficient_comparison.csv")

    assert trees["provenance"].str.startswith("representative_synthetic_v1").all()
    assert set(trees["tree_list_basis"]) == {
        "nfi_coverage_composition",
        "documented_assumption",
    }
    assert (
        trees["tree_list_basis"] == "documented_assumption"
    ).sum() == 4 * N_TREES_PER_PROVINCE

    assert (logs["grade"] == "not_assigned").all()
    assert (logs["grade_status"] == "not_assigned").all()
    assert (logs["sort_basis"] == "dimension_only").all()
    assert (logs["price_deck_id"] == "NAT-PILOT-2026").all()
    assert (logs["small_end_dib_cm"] <= logs["large_end_dib_cm"] + 1e-6).all()

    for frame in (trees, logs, residue, reconciliation, province, comparison):
        assert frame["model_estimate"].all()
        assert frame["synthetic"].all()
        assert (~frame["observed"]).all()
        assert (~frame["policy_grade"]).all()
        assert (~frame["national_claim"]).all()

    assert manifest["labels"]["model_estimate"] is True
    assert manifest["labels"]["synthetic"] is True
    assert manifest["labels"]["observed"] is False
    assert manifest["labels"]["policy_grade"] is False
    assert manifest["labels"]["national_claim"] is False


def test_evidence_rows_are_weakest_component(pilot):
    output, _ = pilot
    logs = _read(output, "bucked_logs.csv")
    residue = _read(output, "residue_ledger.csv")
    for row in logs.itertuples(index=False):
        expected = weakest_evidence(
            (
                row.taper_evidence,
                row.sort_evidence,
                row.value_evidence,
                row.tree_evidence,
            )
        )
        assert row.evidence == expected
    for row in residue.itertuples(index=False):
        expected = weakest_evidence(
            (
                row.taper_evidence,
                row.density_evidence,
                row.moisture_evidence,
                row.chip_evidence,
            )
        )
        assert row.evidence == expected


def test_province_summary_and_coefficient_comparison(pilot):
    output, manifest = pilot
    province = _read(output, "province_summary.csv")
    comparison = _read(output, "coefficient_comparison.csv")

    assert len(province) == 13
    assert len(comparison) == 13
    assert set(province["province"]) == set(PROVINCES)
    assert set(comparison["province"]) == set(PROVINCES)
    assert (province["n_trees"] == N_TREES_PER_PROVINCE).all()
    assert (province["volume_balance_m3"] < 1e-8).all()
    assert (comparison["donor_or_assumption_dominates"]).all()
    assert set(comparison["comparison_status"]).issubset(
        {"within_20pct", "outside_20pct", "not_comparable_zero_harvest"}
    )
    assert comparison[comparison["province"] == "NU"]["comparison_status"].iloc[0] == (
        "not_comparable_zero_harvest"
    )
    for required in ("model_sawlog_share", "model_residue_odt_per_recovered_m3"):
        assert required in comparison.columns
    assert (comparison["model_residue_odt_per_recovered_m3"] >= 0.0).all()

    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert summary["counts"]["coefficient_comparison_rows"] == 13
    assert summary["taper_coverage"]["local_jurisdictions"] == ["BC", "QC"]
    assert set(summary["taper_coverage"]["donor_jurisdictions"]) == (
        set(PROVINCES) - {"BC", "QC"}
    )
    assert summary["totals"]["n_trees"] == 130
    assert manifest["totals"]["n_trees"] == 130


def test_manifest_hashes_scope_and_checks(pilot):
    output, manifest = pilot
    assert manifest["checks"]
    assert all(check["status"] == "PASS" for check in manifest["checks"])
    for record in manifest["outputs"]:
        path = output / record["path"]
        assert path.exists(), record["path"]
        assert record["bytes"] == path.stat().st_size
        assert len(record["sha256"]) == 64
    assert manifest["inputs"]["nfi_coverage"]["sha256"]
    assert manifest["inputs"]["nfi_height_models"]["sha256"]
    assert manifest["inputs"]["facility_product_chain"]["sha256"]
    assert manifest["config"]["trees_per_province"] == N_TREES_PER_PROVINCE


def test_geometry_is_inside_documented_taper_domains(pilot):
    output, _ = pilot
    trees = _read(output, "tree_list.csv")
    assert (trees["dbh_cm"] > 0.0).all()
    assert (trees["height_m"] > 1.3).all()
    qc = trees[trees["taper_jurisdiction"] == "QC"]
    assert qc["dbh_cm"].between(5.0, 100.0).all()
    assert qc["height_m"].between(5.0, 40.0).all()
    other = trees[trees["taper_jurisdiction"] == "BC"]
    assert other["dbh_cm"].between(7.5, 150.0).all()
    assert other["height_m"].between(6.0, 60.0).all()


def test_no_unexpected_deletions():
    assert assert_no_unexpected_deletions() == []


def test_committed_output_is_reproducible():
    if not DEFAULT_OUTPUT_DIR.exists():
        pytest.skip("committed pilot v1 output directory is not present")
    logs = pd.read_csv(DEFAULT_OUTPUT_DIR / "bucked_logs.csv")
    residue = pd.read_csv(DEFAULT_OUTPUT_DIR / "residue_ledger.csv")
    summary = json.loads(
        (DEFAULT_OUTPUT_DIR / "summary.json").read_text(encoding="utf-8")
    )
    assert summary["scope"] == "representative_synthetic_pilot"
    assert summary["national_claim"] is False
    assert len(logs) == summary["counts"]["logs"]
    assert len(residue) == summary["counts"]["residue_rows"]
    assert len(logs) > 0
