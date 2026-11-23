"""Validation tests for the Quebec PEP measured-tree bucking product.

The first test covers the per-tree expansion identity as a pure function.
The remaining tests read the generated v0 artifacts and fail on schema,
expansion, volume/residue balance, evidence, label, NaN or negative-value
defects.  They are skipped when the data repository has not produced the
artifacts yet.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from workflows.national.bucking.pilot_schema import EVIDENCE_TIERS
from workflows.national.bucking.qc_pep_validation import (
    EXPECTED_GPKG_SHA256,
    EXPECTED_ZIP_SHA256,
    REQUIRED_ARTIFACTS,
    VALIDATION_NAME,
    QCValidationError,
    inclusion_area_ha,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = REPO_ROOT / "data" / "derived" / "national" / "bucking" / "qc_pep_validation_v0"

requires_artifacts = pytest.mark.skipif(
    not all((OUT_DIR / name).exists() for name in REQUIRED_ARTIFACTS),
    reason="Quebec PEP validation artifacts have not been generated",
)


def _read(name: str) -> pd.DataFrame:
    return pd.read_csv(OUT_DIR / name)


def _summary() -> dict:
    return json.loads((OUT_DIR / "summary.json").read_text(encoding="utf-8"))


def _manifest() -> dict:
    return json.loads((OUT_DIR / "manifest.json").read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _assert_numeric_clean(frame: pd.DataFrame, name: str) -> None:
    numeric = frame.select_dtypes(include=[np.number])
    values = numeric.to_numpy(dtype=float)
    assert not np.isnan(values).any(), f"{name} contains NaN"
    assert (values >= -1e-12).all(), f"{name} contains a negative value"


def test_inclusion_area_is_pure_and_guards_invalid_expansion() -> None:
    assert inclusion_area_ha(25.0) == pytest.approx(0.04)
    assert inclusion_area_ha(16.0) == pytest.approx(0.0625)
    with pytest.raises(QCValidationError):
        inclusion_area_ha(0.0)
    with pytest.raises(QCValidationError):
        inclusion_area_ha(-1.0)
    with pytest.raises(QCValidationError):
        inclusion_area_ha(float("nan"))


@requires_artifacts
def test_source_schema_and_selection_contract() -> None:
    summary = _summary()
    selection = summary["selection"]
    schema = summary["schema"]

    assert summary["validation"] == VALIDATION_NAME
    assert summary["source"]["licence"] == "CC BY 4.0"
    assert summary["source"]["zip_sha256"] == EXPECTED_ZIP_SHA256
    assert summary["source"]["gpkg_sha256"] == EXPECTED_GPKG_SHA256
    assert summary["source"]["zip_hash_matches_record"] is True
    assert summary["source"]["gpkg_hash_matches_record"] is True

    assert schema["counts"]["trees"] == 2_210_417
    assert schema["counts"]["plots"] == 12_834
    assert schema["counts"]["visits"] == 53_632
    assert schema["counts"]["vmb_tige_non_null"] == 0
    assert schema["counts"]["vmb_ha_non_null"] == 0
    assert schema["counts"]["haut_arbre_non_null"] > 0

    assert selection["selected_plots"] == 1000
    assert selection["selected_trees"] == 7767
    assert selection["selected_visits"] == 1000
    assert selection["cap_applied"] is False
    assert selection["year_min"] == 2024
    assert selection["year_max"] == 2025
    assert selection["funnel"]["candidate_trees"] == 66_359
    assert selection["funnel"]["candidate_plots"] == 9_545

    comparison = summary["comparison"]
    assert comparison["pep_compiled_volume_fields"] == ["vmb_tige", "vmb_ha"]
    assert comparison["pep_compiled_volume_non_null_full"] == 0
    assert comparison["pep_compiled_volume_non_null_selected"] == 0
    assert comparison["status"] == "taper_only_no_pep_compiled_volume"


@requires_artifacts
def test_tree_list_schema_labels_and_evidence() -> None:
    trees = _read("tree_list.csv")

    assert trees["tree_id"].is_unique
    assert len(trees) == 7767
    assert trees["site_id"].nunique() == 1000
    assert trees["stem_id"].nunique() == 7767
    assert set(trees["species"]) == {
        "BOP",
        "EPB",
        "EPN",
        "EPR",
        "PEG",
        "PET",
        "SAB",
        "THO",
    }

    labels = {
        "measured_tree_list": True,
        "open_data": True,
        "model_estimate": True,
        "observed": False,
        "policy_grade": False,
        "national_claim": False,
        "synthetic": False,
    }
    for column, expected in labels.items():
        assert column in trees.columns
        assert trees[column].eq(expected).all(), column

    assert trees["tree_list_evidence"].eq("measured_tree_list").all()
    assert trees["taper_evidence"].eq("published_table").all()
    assert trees["evidence"].isin(EVIDENCE_TIERS).all()
    assert trees["taper_id"].str.startswith("QC-SCHNEIDER2013-").all()
    assert trees["pep_compiled_volume_available"].eq(False).all()
    assert trees["defect_pct"].eq(0.0).all()
    assert trees["height_m"].between(5.0, 40.0).all()
    assert trees["dbh_cm"].between(5.0, 100.0).all()


@requires_artifacts
def test_expansion_and_plot_area_reconcile() -> None:
    trees = _read("tree_list.csv")
    plots = _read("plot_summary.csv")

    assert np.allclose(
        trees["expansion_factor_stems_ha"] * trees["inclusion_area_ha"], 1.0
    )
    assert set(trees["expansion_factor_stems_ha"].round(6)) == {16.0, 25.0}
    assert set(trees["inclusion_area_ha"].round(6)) == {0.04, 0.0625}
    assert trees["nominal_plot_area_ha"].eq(0.04).all()

    grouped = (
        trees.groupby("site_id")
        .agg(
            n_trees=("tree_id", "size"),
            taper_total_m3_per_ha=("taper_total_m3_per_ha", "sum"),
            recovered_gross_m3_per_ha=("recovered_gross_m3_per_ha", "sum"),
            residue_m3_per_ha=("residue_m3_per_ha", "sum"),
        )
        .reset_index()
    )
    merged = plots.merge(grouped, on="site_id", suffixes=("", "_tree"))
    assert len(merged) == 1000
    for column in ("n_trees", "taper_total_m3_per_ha", "recovered_gross_m3_per_ha", "residue_m3_per_ha"):
        assert np.allclose(merged[column], merged[f"{column}_tree"], atol=1e-9)


@requires_artifacts
def test_volume_and_residue_balance_reconcile() -> None:
    trees = _read("tree_list.csv")
    logs = _read("bucked_logs.csv")
    residue = _read("residue.csv")
    plots = _read("plot_summary.csv")
    species = _read("species_summary.csv")

    tree_balance = (
        trees["recovered_gross_m3"] + trees["residue_m3"] - trees["taper_total_m3"]
    ).abs()
    assert tree_balance.max() <= 1e-9

    ledger_balance = (
        residue["recovered_log_m3"]
        + residue["residue_volume_m3"]
        - residue["taper_total_m3"]
    ).abs()
    assert ledger_balance.max() <= 1e-9
    assert set(residue["tree_id"]) == set(trees["tree_id"])

    log_sums = logs.groupby("tree_id")["volume_m3"].sum()
    joined = trees.set_index("tree_id")[["recovered_gross_m3"]].join(
        log_sums.rename("log_total")
    )
    joined["log_total"] = joined["log_total"].fillna(0.0)
    assert np.allclose(joined["log_total"], joined["recovered_gross_m3"], atol=1e-9)
    assert set(logs["tree_id"]).issubset(set(trees["tree_id"]))
    assert len(logs) == 12_599

    plot_totals = trees.groupby("site_id").agg(
        taper_total_m3=("taper_total_m3", "sum"),
        recovered_gross_m3=("recovered_gross_m3", "sum"),
        residue_m3=("residue_m3", "sum"),
    )
    plot_joined = plots.set_index("site_id").join(plot_totals, rsuffix="_tree")
    for column in ("taper_total_m3", "recovered_gross_m3", "residue_m3"):
        assert np.allclose(plot_joined[column], plot_joined[f"{column}_tree"], atol=1e-9)

    species_totals = trees.groupby("species").agg(
        taper_total_m3=("taper_total_m3", "sum"),
        recovered_gross_m3=("recovered_gross_m3", "sum"),
        residue_m3=("residue_m3", "sum"),
    )
    species_joined = species.set_index("species").join(species_totals, rsuffix="_tree")
    for column in ("taper_total_m3", "recovered_gross_m3", "residue_m3"):
        assert np.allclose(species_joined[column], species_joined[f"{column}_tree"], atol=1e-9)

    assert math.isclose(
        trees["taper_total_m3"].sum(),
        trees["recovered_gross_m3"].sum() + trees["residue_m3"].sum(),
        abs_tol=1e-8,
    )


@requires_artifacts
def test_comparison_evidence_and_no_policy_grade() -> None:
    logs = _read("bucked_logs.csv")
    residue = _read("residue.csv")
    comparison = _read("comparison.csv")
    summary = _summary()

    assert logs["grade_status"].eq("not_assigned").all()
    assert logs["grade"].eq("not_assigned").all()
    assert logs["policy_grade"].eq(False).all()
    assert logs["sort_basis"].eq("dimension_only").all()
    assert logs["taper_evidence"].eq("published_table").all()
    assert logs["evidence"].isin(EVIDENCE_TIERS).all()
    assert residue["evidence"].isin(EVIDENCE_TIERS).all()
    assert residue["taper_evidence"].eq("published_table").all()
    assert logs["national_claim"].eq(False).all()
    assert residue["national_claim"].eq(False).all()

    all_row = comparison[comparison["scope"] == "ALL"]
    assert len(all_row) == 1
    row = all_row.iloc[0]
    assert int(row["n_trees"]) == 7767
    assert int(row["n_logs"]) == 12_599
    assert int(row["pep_vmb_tige_non_null"]) == 0
    assert int(row["pep_vmb_ha_non_null"]) == 0
    assert row["comparison_status"] == "taper_only_no_pep_compiled_volume"
    assert not bool(row["pep_compiled_volume_fields_populated"])

    assert summary["comparison"]["pep_biomass_fields"] == []
    assert summary["comparison"]["pep_compiled_volume_non_null_selected"] == 0


@requires_artifacts
def test_no_nan_or_negative_values() -> None:
    for name in (
        "tree_list",
        "bucked_logs",
        "residue",
        "plot_summary",
        "species_summary",
        "comparison",
    ):
        _assert_numeric_clean(_read(f"{name}.csv"), name)


@requires_artifacts
def test_manifest_hashes_every_artifact() -> None:
    manifest = _manifest()
    assert manifest["validation"] == VALIDATION_NAME
    assert manifest["artifact_count"] == len(REQUIRED_ARTIFACTS) - 1
    assert manifest["labels"]["national_claim"] is False

    seen = set()
    for record in manifest["artifacts"]:
        name = record["label"]
        seen.add(name)
        path = OUT_DIR / name
        assert path.exists(), name
        assert path.stat().st_size == record["bytes"]
        assert _sha256(path) == record["sha256"]

    assert seen == set(REQUIRED_ARTIFACTS) - {"manifest.json"}
    for name in REQUIRED_ARTIFACTS:
        assert (OUT_DIR / name).exists(), name
