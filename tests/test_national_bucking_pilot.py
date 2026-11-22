"""Contract tests for the national bucking Quebec pilot."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from workflows.national.bucking.national_bucking_pilot import (
    DEFAULT_OUTPUT_DIR,
    REPO_ROOT,
    build_synthetic_tree_list,
    generate_pilot,
)
from workflows.national.bucking.pilot_schema import EVIDENCE_TIERS, weakest_evidence
from workflows.national.bucking.schneider2013 import (
    TaperRegistryError,
    load_taper_registry,
)

TAPER_PATH = REPO_ROOT / "workflows" / "national" / "bucking" / "rules" / "qc_schneider2013_taper.csv"


@pytest.fixture(scope="module")
def pilot(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict]:
    output = tmp_path_factory.mktemp("national_bucking_pilot")
    manifest = generate_pilot(output)
    return output, manifest


def _read(output: Path, name: str) -> pd.DataFrame:
    return pd.read_csv(output / name)


def test_output_files_exist(pilot):
    output, manifest = pilot
    for name in (
        "README.md",
        "schema.md",
        "tree_list.csv",
        "bucked_logs.csv",
        "residue_ledger.csv",
        "reconciliation.csv",
        "reconciliation_summary.json",
        "taper_registry.csv",
        "grade_rules.csv",
        "manifest.json",
    ):
        assert (output / name).exists(), name
    assert manifest["scope"] == "pilot"
    assert manifest["national_claim"] is False
    assert manifest["jurisdiction"] == "QC"


def test_schema_fields(pilot):
    output, _ = pilot
    trees = _read(output, "tree_list.csv")
    logs = _read(output, "bucked_logs.csv")
    residue = _read(output, "residue_ledger.csv")

    assert {
        "tree_id",
        "site_id",
        "jurisdiction",
        "species",
        "species_group",
        "dbh_cm",
        "height_m",
        "defect_pct",
        "expansion_factor",
        "status",
        "provenance",
        "evidence",
        "taper_id",
    }.issubset(trees.columns)
    assert {
        "tree_id",
        "log_index",
        "length_m",
        "large_end_dib_cm",
        "small_end_dib_cm",
        "volume_m3",
        "sort_code",
        "product",
        "grade",
        "value_cad",
        "evidence",
    }.issubset(logs.columns)
    assert {
        "tree_id",
        "residue_volume_m3",
        "residue_odt",
        "moisture_wet_basis",
        "chip_yield_fraction",
        "chip_green_tonnes",
        "hog_yield_fraction",
        "hog_green_tonnes",
        "evidence",
    }.issubset(residue.columns)
    assert len(trees) == 24
    assert len(logs) > 0
    assert len(residue) == len(trees)


def test_no_nan_or_negative(pilot):
    output, _ = pilot
    for name in ("tree_list.csv", "bucked_logs.csv", "residue_ledger.csv", "reconciliation.csv"):
        frame = _read(output, name)
        numeric = frame.select_dtypes(include="number")
        assert not numeric.isna().any().any(), name
        assert (numeric >= 0.0).all().all(), name
        assert frame.notna().all().all(), name


def test_evidence_tiers(pilot):
    output, _ = pilot
    trees = _read(output, "tree_list.csv")
    logs = _read(output, "bucked_logs.csv")
    residue = _read(output, "residue_ledger.csv")
    reconciliation = _read(output, "reconciliation.csv")

    for frame, column in (
        (trees, "evidence"),
        (logs, "evidence"),
        (logs, "taper_evidence"),
        (logs, "sort_evidence"),
        (logs, "value_evidence"),
        (logs, "tree_evidence"),
        (residue, "evidence"),
        (residue, "taper_evidence"),
        (residue, "density_evidence"),
        (residue, "moisture_evidence"),
        (residue, "chip_evidence"),
        (reconciliation, "evidence"),
        (reconciliation, "taper_evidence"),
    ):
        assert set(frame[column]).issubset(set(EVIDENCE_TIERS)), column

    assert (logs["taper_evidence"] == "published_table").all()
    assert (residue["taper_evidence"] == "published_table").all()
    assert (trees["evidence"] == "assumption").all()
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


def test_volume_and_residue_balance(pilot):
    output, manifest = pilot
    logs = _read(output, "bucked_logs.csv")
    residue = _read(output, "residue_ledger.csv")
    reconciliation = _read(output, "reconciliation.csv")

    for row in residue.itertuples(index=False):
        tree_logs = logs[logs["tree_id"] == row.tree_id]
        assert abs(tree_logs["volume_m3"].sum() - row.recovered_log_m3) < 1e-8
        assert abs(tree_logs["net_volume_m3"].sum() + tree_logs["defect_deduction_m3"].sum() - row.recovered_log_m3) < 1e-8
        assert abs(row.recovered_log_m3 + row.residue_volume_m3 - row.taper_total_m3) < 1e-8
        assert abs(row.balance_error_m3) < 1e-8

    for row in reconciliation.itertuples(index=False):
        assert abs(row.recovered_gross_m3 + row.residue_m3 - row.taper_total_m3) < 1e-8
        assert abs(row.recovered_net_m3 + row.defect_m3 - row.recovered_gross_m3) < 1e-8

    totals = manifest["totals"]
    assert abs(totals["recovered_gross_m3"] - logs["volume_m3"].sum()) < 1e-8
    assert abs(totals["recovered_net_m3"] - logs["net_volume_m3"].sum()) < 1e-8
    assert abs(totals["residue_m3"] - residue["residue_volume_m3"].sum()) < 1e-8
    assert abs(totals["taper_total_m3"] - reconciliation["taper_total_m3"].sum()) < 1e-8
    assert totals["balance_error_m3"] < 1e-8
    assert totals["net_plus_defect_error_m3"] < 1e-8


def test_geometry_is_labelled(pilot):
    output, _ = pilot
    logs = _read(output, "bucked_logs.csv")
    trees = _read(output, "tree_list.csv")

    assert (logs["taper_id"].str.len() > 0).all()
    assert logs["taper_source"].str.contains("Fortin|Schneider", regex=True).all()
    assert (logs["grade"] == "not_assigned").all()
    assert (logs["grade_status"] == "not_assigned").all()
    assert (logs["sort_basis"] == "dimension_only").all()
    assert (logs["price_deck_id"] == "QC-PILOT-2026").all()
    assert (logs["small_end_dib_cm"] <= logs["large_end_dib_cm"] + 1e-6).all()
    for row in logs.itertuples(index=False):
        assert row.grade_rule_id
        assert row.provenance.startswith("synthetic_pilot_v0")

    assert trees["provenance"].str.startswith("synthetic_pilot_v0").all()
    assert trees["taper_id"].str.startswith("QC-SCHNEIDER2013-").all()


def test_manifest_hashes_and_scope(pilot):
    output, manifest = pilot
    assert manifest["phase"] == "Phase 0/2 pilot"
    assert manifest["national_claim"] is False
    assert manifest["scope"] == "pilot"
    assert "not" in " ".join(manifest["limitations"]).lower()
    for record in manifest["outputs"]:
        path = output / record["path"]
        assert path.exists()
        assert record["bytes"] == path.stat().st_size
        assert len(record["sha256"]) == 64


def test_taper_registry_and_domain():
    registry = load_taper_registry(TAPER_PATH)
    keys = {species for _, species in registry.keys}
    assert {"BOP", "EPB", "EPN", "EPR", "PEG", "PET", "SAB", "THO"} == keys
    model = registry.resolve("QC", "EPN")
    diameter = model.diameter_inside_bark_cm(20.0, 18.0, 1.3)
    assert 0.0 < diameter < 20.0
    assert model.total_volume_m3(20.0, 18.0) > 0.0
    with pytest.raises(TaperRegistryError):
        registry.resolve("QC", "PIB")
    with pytest.raises(TaperRegistryError):
        model.diameter_inside_bark_cm(2.0, 18.0, 1.3)


def test_synthetic_tree_list_contract():
    trees = build_synthetic_tree_list()
    assert len(trees) == 24
    assert {tree.jurisdiction for tree in trees} == {"QC"}
    assert all(tree.provenance.startswith("synthetic_pilot_v0") for tree in trees)
    assert all(tree.evidence == "assumption" for tree in trees)
    assert all(tree.dbh_cm > 0 and tree.height_m > 1.3 for tree in trees)


def test_committed_output_is_reproducible():
    """The checked-in pilot directory must use the same schema and pass the
    same balance checks, so a reader can inspect the data without rerunning."""
    if not DEFAULT_OUTPUT_DIR.exists():
        pytest.skip("committed pilot output directory is not present")
    logs = pd.read_csv(DEFAULT_OUTPUT_DIR / "bucked_logs.csv")
    residue = pd.read_csv(DEFAULT_OUTPUT_DIR / "residue_ledger.csv")
    summary = json.loads(
        (DEFAULT_OUTPUT_DIR / "reconciliation_summary.json").read_text(encoding="utf-8")
    )
    assert summary["scope"] == "pilot"
    assert summary["national_claim"] is False
    assert len(logs) == summary["counts"]["logs"]
    assert abs(logs["volume_m3"].sum() - summary["totals"]["recovered_gross_m3"]) < 1e-8
    assert abs(residue["residue_volume_m3"].sum() - summary["totals"]["residue_m3"]) < 1e-8
