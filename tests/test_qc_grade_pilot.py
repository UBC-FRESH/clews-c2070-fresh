"""Tests for the Quebec pilot grade-rule rerun."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from workflows.national.bucking.pilot_schema import EVIDENCE_TIERS, weakest_evidence
from workflows.national.bucking.qc_grade_pilot import (
    DEFAULT_OUTPUT_DIR,
    GRADE_RULES_PATH,
    QUALITY_PRODUCT,
    SCENARIOS,
    generate_pilot,
    load_qc_tree_list,
)

OUTPUT_FILES = (
    "tree_list.csv",
    "bucked_logs.csv",
    "residue_ledger.csv",
    "reconciliation.csv",
    "grade_distribution.csv",
    "product_summary.csv",
    "scenario_comparison.csv",
    "volume_reconciliation.csv",
    "grade_rules.csv",
    "dimension_rules.csv",
    "schema.md",
    "report.md",
    "summary.json",
    "manifest.json",
)


@pytest.fixture(scope="module")
def pilot(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict]:
    output = tmp_path_factory.mktemp("qc_grade_pilot_v1")
    manifest = generate_pilot(output)
    return output, manifest


def _read(output: Path, name: str) -> pd.DataFrame:
    return pd.read_csv(output / name)


def test_output_files_and_manifest(pilot):
    output, manifest = pilot
    for name in OUTPUT_FILES:
        assert (output / name).exists(), name
    assert manifest["scope"] == "quebec_representative_synthetic_grade_pilot"
    assert manifest["official_grade_claim"] is False
    assert manifest["national_claim"] is False
    assert manifest["counts"]["trees"] == 10
    assert manifest["counts"]["scenarios"] == 2
    assert set(manifest["scenario_totals"]) == set(SCENARIOS)
    assert all(check["status"] == "PASS" for check in manifest["checks"])
    for record in manifest["outputs"]:
        path = output / record["path"]
        assert path.exists(), record["path"]
        assert record["bytes"] == path.stat().st_size
        assert len(record["sha256"]) == 64


def test_grade_mapping_is_explicit_and_pilot(pilot):
    output, _ = pilot
    grade_rules = _read(output, "grade_rules.csv")
    dimension_rules = _read(output, "dimension_rules.csv")
    assert set(grade_rules["quality"]) == set(QUALITY_PRODUCT)
    assert (grade_rules["grade_status"] == "pilot_assumption").all()
    assert grade_rules["grade"].str.startswith("pilot_").all()
    assert (grade_rules["sort_basis"] == "grade_pilot_assumption").all()
    assert (grade_rules["product"] == grade_rules["quality"].map(QUALITY_PRODUCT)).all()
    assert (grade_rules["dimension_evidence"] == "published_table").all()
    assert (grade_rules["mapping_evidence"] == "assumption").all()
    assert (grade_rules["evidence"] == "assumption").all()
    assert (dimension_rules["grade"] == "not_assigned").all()
    assert (dimension_rules["grade_status"] == "not_assigned").all()
    assert (dimension_rules["quality"] == "not_classified").all()

    logs = _read(output, "bucked_logs.csv")
    grade_logs = logs[logs["scenario"] == "grade_rules"]
    assert (grade_logs["grade_status"] == "pilot_assumption").all()
    assert grade_logs["grade"].str.startswith("pilot_").all()
    assert (grade_logs["product"] == grade_logs["quality"].map(QUALITY_PRODUCT)).all()
    assert (grade_logs["sort_basis"] == "grade_pilot_assumption").all()

    dimension_logs = logs[logs["scenario"] == "dimension_only"]
    assert (dimension_logs["grade"] == "not_assigned").all()
    assert (dimension_logs["grade_status"] == "not_assigned").all()
    assert (dimension_logs["quality"] == "not_classified").all()
    assert (dimension_logs["sort_basis"] == "dimension_only").all()


def test_conservation_and_volume_reconciliation(pilot):
    output, manifest = pilot
    logs = _read(output, "bucked_logs.csv")
    residue = _read(output, "residue_ledger.csv")
    reconciliation = _read(output, "reconciliation.csv")
    volume = _read(output, "volume_reconciliation.csv")

    for scenario in SCENARIOS:
        scenario_residue = residue[residue["scenario"] == scenario]
        scenario_reconciliation = reconciliation[reconciliation["scenario"] == scenario]
        scenario_logs = logs[logs["scenario"] == scenario]
        for row in scenario_residue.itertuples(index=False):
            tree_logs = scenario_logs[scenario_logs["tree_id"] == row.tree_id]
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
        for row in scenario_reconciliation.itertuples(index=False):
            assert (
                abs(row.recovered_gross_m3 + row.residue_m3 - row.taper_total_m3) < 1e-8
            )
            assert (
                abs(row.recovered_net_m3 + row.defect_m3 - row.recovered_gross_m3)
                < 1e-8
            )
        row = volume[volume["scenario"] == scenario].iloc[0]
        assert abs(row.balance_error_m3) < 1e-8
        assert abs(row.net_defect_error_m3) < 1e-8

    dimension = reconciliation[reconciliation["scenario"] == "dimension_only"]
    grade = reconciliation[reconciliation["scenario"] == "grade_rules"]
    merged = dimension.merge(
        grade, on="tree_id", suffixes=("_dimension", "_grade"), validate="one_to_one"
    )
    assert (
        merged["taper_total_m3_dimension"] - merged["taper_total_m3_grade"]
    ).abs().max() < 1e-12
    assert manifest["scenario_totals"]["dimension_only"]["balance_error_m3"] < 1e-8
    assert manifest["scenario_totals"]["grade_rules"]["balance_error_m3"] < 1e-8


def test_no_nan_or_negative(pilot):
    output, _ = pilot
    for name in (
        "tree_list.csv",
        "bucked_logs.csv",
        "residue_ledger.csv",
        "reconciliation.csv",
        "grade_distribution.csv",
        "product_summary.csv",
        "volume_reconciliation.csv",
    ):
        frame = _read(output, name)
        numeric = frame.select_dtypes(include="number")
        assert not numeric.isna().any().any(), name
        assert (numeric >= 0.0).all().all(), name
        assert frame.notna().all().all(), name


def test_evidence_and_labels(pilot):
    output, manifest = pilot
    trees = _read(output, "tree_list.csv")
    logs = _read(output, "bucked_logs.csv")
    residue = _read(output, "residue_ledger.csv")
    reconciliation = _read(output, "reconciliation.csv")
    grade_rules = _read(output, "grade_rules.csv")

    assert set(logs["taper_evidence"]).issubset(set(EVIDENCE_TIERS))
    assert (logs["taper_evidence"] == "published_table").all()
    assert (residue["taper_evidence"] == "published_table").all()
    assert (logs["sort_evidence"] == "assumption").all()
    assert (grade_rules["evidence"] == "assumption").all()

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

    for frame in (trees, logs, residue, reconciliation):
        assert frame["model_estimate"].all()
        assert frame["synthetic"].all()
        assert (~frame["observed"]).all()
        assert (~frame["policy_grade"]).all()
        assert (~frame["national_claim"]).all()
    assert manifest["labels"]["policy_grade"] is False
    assert manifest["labels"]["ui_or_public_osecan_changed"] is False


def test_grade_effect_and_residue_direction(pilot):
    output, manifest = pilot
    comparison = _read(output, "scenario_comparison.csv").set_index("metric")
    grade_distribution = _read(output, "grade_distribution.csv")
    grade = grade_distribution[grade_distribution["scenario"] == "grade_rules"]

    assert set(grade["quality"]) <= set(QUALITY_PRODUCT)
    assert {"F1", "F2", "F3", "F4", "PATE"} & set(grade["quality"])
    assert (
        comparison.loc["value_cad", "grade_rules"]
        > comparison.loc["value_cad", "dimension_only"]
    )
    assert (
        comparison.loc["sawlog_plus_peeler_share", "grade_rules"]
        > comparison.loc["sawlog_plus_peeler_share", "dimension_only"]
    )
    assert (
        comparison.loc["residue_m3", "grade_rules"]
        < comparison.loc["residue_m3", "dimension_only"]
    )
    assert (
        comparison.loc["pulpwood_share", "grade_rules"]
        < comparison.loc["pulpwood_share", "dimension_only"]
    )
    assert manifest["scenario_totals"]["grade_rules"]["sawlog_plus_peeler_m3"] > 0.0
    assert manifest["scenario_totals"]["grade_rules"]["residue_m3"] >= 0.0


def test_reproducibility(pilot, tmp_path_factory):
    output, manifest = pilot
    second = tmp_path_factory.mktemp("qc_grade_pilot_v1_repeat")
    repeat = generate_pilot(second)
    for name in (
        "bucked_logs.csv",
        "residue_ledger.csv",
        "reconciliation.csv",
        "grade_distribution.csv",
        "product_summary.csv",
        "scenario_comparison.csv",
        "volume_reconciliation.csv",
        "summary.json",
    ):
        assert (output / name).read_bytes() == (second / name).read_bytes(), name
    assert repeat["scenario_totals"] == manifest["scenario_totals"]
    assert repeat["counts"] == manifest["counts"]


def test_tree_list_is_the_v1_qc_list(pilot):
    output, _ = pilot
    trees = _read(output, "tree_list.csv")
    expected = load_qc_tree_list()
    assert len(trees) == len(expected) == 10
    assert trees["tree_id"].is_unique
    assert set(trees["jurisdiction"]) == {"QC"}
    assert (trees["provenance"].str.startswith("representative_synthetic_v1")).all()
    assert (trees["evidence"] == "assumption").all()


def test_committed_output_matches_contract():
    if not DEFAULT_OUTPUT_DIR.exists():
        pytest.skip("committed QC grade pilot output is not present")
    logs = pd.read_csv(DEFAULT_OUTPUT_DIR / "bucked_logs.csv")
    residue = pd.read_csv(DEFAULT_OUTPUT_DIR / "residue_ledger.csv")
    summary = json.loads(
        (DEFAULT_OUTPUT_DIR / "summary.json").read_text(encoding="utf-8")
    )
    assert summary["official_grade_claim"] is False
    assert set(logs["scenario"]) == set(SCENARIOS)
    assert len(logs) == summary["counts"]["logs"]
    assert len(residue) == summary["counts"]["residue_rows"]
    assert (logs["policy_grade"] == False).all()
    assert GRADE_RULES_PATH.exists()
