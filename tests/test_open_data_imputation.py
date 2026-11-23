"""Contract tests for the nine-province open-data imputation artifact.

The tests read the committed artifact, enforce E1/E2 provenance labels,
volume/residue conservation, numeric sanity, facility comparison coverage and
deterministic regeneration of the text outputs.
"""

from __future__ import annotations

import hashlib
import json

import numpy as np
import pandas as pd
import pytest

from workflows.national.bucking.open_data_imputation import (
    DEFAULT_OUTPUT_DIR,
    IMPUTED_PROVINCES,
    MEASURED_PROVINCES,
    PROVINCES,
    generate,
)

REQUIRED_ARTIFACTS = (
    "tree_list.csv",
    "bucked_logs.csv",
    "residue_ledger.csv",
    "province_summary.csv",
    "facility_coefficient_comparison.csv",
    "provenance_coverage.csv",
    "rsyc_aggregate_check.csv",
    "imputation_audit.csv",
    "summary.json",
    "manifest.json",
    "report.md",
)

requires_artifacts = pytest.mark.skipif(
    not all((DEFAULT_OUTPUT_DIR / name).exists() for name in REQUIRED_ARTIFACTS),
    reason="open-data imputation artifact has not been generated",
)


def _sha256(path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


@pytest.fixture(scope="module")
def artifact() -> dict[str, object]:
    return {
        "dir": DEFAULT_OUTPUT_DIR,
        "summary": json.loads(
            (DEFAULT_OUTPUT_DIR / "summary.json").read_text(encoding="utf-8")
        ),
        "manifest": json.loads(
            (DEFAULT_OUTPUT_DIR / "manifest.json").read_text(encoding="utf-8")
        ),
        "trees": pd.read_csv(DEFAULT_OUTPUT_DIR / "tree_list.csv"),
        "logs": pd.read_csv(DEFAULT_OUTPUT_DIR / "bucked_logs.csv"),
        "residue": pd.read_csv(DEFAULT_OUTPUT_DIR / "residue_ledger.csv"),
        "province": pd.read_csv(DEFAULT_OUTPUT_DIR / "province_summary.csv"),
        "facility": pd.read_csv(
            DEFAULT_OUTPUT_DIR / "facility_coefficient_comparison.csv"
        ),
        "coverage": pd.read_csv(DEFAULT_OUTPUT_DIR / "provenance_coverage.csv"),
        "rsyc": pd.read_csv(DEFAULT_OUTPUT_DIR / "rsyc_aggregate_check.csv"),
        "audit": pd.read_csv(DEFAULT_OUTPUT_DIR / "imputation_audit.csv"),
    }


@requires_artifacts
def test_thirteen_provinces_and_provenance_tiers(artifact) -> None:
    coverage = artifact["coverage"]
    assert set(coverage["province"]) == set(PROVINCES)
    assert len(coverage) == 13
    e1 = coverage[coverage["provenance_tier"] == "E1_measured_tree_list"]
    e2 = coverage[coverage["provenance_tier"] == "E2_open_data_imputation"]
    assert set(e1["province"]) == set(MEASURED_PROVINCES)
    assert set(e2["province"]) == set(IMPUTED_PROVINCES)
    assert e1["measured"].all() and not e1["imputed"].any()
    assert e2["imputed"].all() and not e2["measured"].any()
    assert not coverage["synthetic"].any()
    assert set(artifact["trees"]["jurisdiction"]) == set(PROVINCES)


@requires_artifacts
def test_e2_labels_and_taper_donors(artifact) -> None:
    trees = artifact["trees"]
    e2_trees = trees[trees["provenance_tier"] == "E2_open_data_imputation"]
    e1_trees = trees[trees["provenance_tier"] == "E1_measured_tree_list"]

    assert len(e2_trees) == 324
    assert not e2_trees["measured"].any()
    assert e2_trees["imputed"].all()
    assert not e2_trees["synthetic"].any()
    assert e2_trees["measured_tree_list"].eq(False).all()
    assert set(e2_trees["taper_status"]) == {"donor_published"}
    assert set(e2_trees["taper_jurisdiction"]).issubset({"BC", "QC"})

    assert len(e1_trees) == 591
    assert e1_trees["measured"].all()
    assert not e1_trees["imputed"].any()
    assert e1_trees["measured_tree_list"].eq(True).all()
    assert set(e1_trees["taper_status"]).issubset(
        {"local_published", "donor_published"}
    )
    assert not trees["national_claim"].any()
    assert not trees["observed"].any()
    assert not trees["policy_grade"].any()


@requires_artifacts
def test_volume_and_residue_balance(artifact) -> None:
    logs = artifact["logs"]
    residue = artifact["residue"]
    log_totals = logs.groupby("tree_id")["volume_m3"].sum()
    joined = residue.set_index("tree_id").join(
        log_totals.rename("log_volume_m3"), how="left"
    )
    joined["log_volume_m3"] = joined["log_volume_m3"].fillna(0.0)
    assert np.allclose(
        joined["log_volume_m3"], joined["recovered_log_m3"], rtol=1e-10, atol=1e-9
    )
    assert np.allclose(
        joined["recovered_log_m3"] + joined["residue_volume_m3"],
        joined["taper_total_m3"],
        rtol=1e-9,
        atol=1e-8,
    )
    assert (joined["balance_error_m3"] <= 1e-8).all()
    assert (joined["residue_volume_m3"] >= 0.0).all()
    assert (joined["taper_total_m3"] > 0.0).all()
    assert (logs["volume_m3"] > 0.0).all()
    assert (logs["net_volume_m3"] >= 0.0).all()
    assert (logs["defect_deduction_m3"] >= 0.0).all()
    province = artifact["province"]
    assert (province["volume_balance_m3"].abs() <= 1e-6).all()


@requires_artifacts
def test_imputed_expansion_and_output_sanity(artifact) -> None:
    trees = artifact["trees"]
    for province in IMPUTED_PROVINCES:
        subset = trees[trees["jurisdiction"] == province]
        assert subset["expansion_factor"].sum() == pytest.approx(1000.0, abs=1e-6)
    numeric_core = {
        "trees": [
            "dbh_cm",
            "height_m",
            "expansion_factor",
            "defect_pct",
        ],
        "logs": [
            "length_m",
            "large_end_dib_cm",
            "small_end_dib_cm",
            "volume_m3",
            "net_volume_m3",
            "defect_deduction_m3",
            "value_cad",
            "volume_m3_expanded",
        ],
        "residue": [
            "residue_volume_m3",
            "residue_odt",
            "recovered_log_m3",
            "taper_total_m3",
            "balance_error_m3",
            "residue_volume_m3_expanded",
            "taper_total_m3_expanded",
        ],
        "province": [
            "n_trees",
            "recovered_gross_m3_expanded",
            "residue_m3_expanded",
            "total_residue_odt_expanded",
            "volume_balance_m3",
        ],
    }
    for name, columns in numeric_core.items():
        frame = artifact[name]
        for column in columns:
            assert frame[column].notna().all(), f"{name}.{column} has NaN"
            assert (frame[column] >= 0.0).all(), f"{name}.{column} is negative"
        assert np.isfinite(frame[columns].to_numpy(dtype=float)).all()


@requires_artifacts
def test_facility_and_rsyc_coverage(artifact) -> None:
    facility = artifact["facility"]
    assert set(facility["province"]) == set(PROVINCES)
    assert facility["dominance_flag"].notna().all()
    assert facility["fit_status"].notna().all()
    e2 = facility[
        (facility["provenance_tier"] == "E2_open_data_imputation")
        & (facility["province"] != "NU")
    ]
    assert (e2["dominance_flag"] == "donor_coefficient_vs_imputation_diverge").all()
    nu = facility[facility["province"] == "NU"].iloc[0]
    assert nu["fit_status"] == "no_modelled_harvest"
    rsyc = artifact["rsyc"]
    assert set(rsyc["province"]) == set(PROVINCES)
    imputed = rsyc[rsyc["check_status"] == "aggregate_context_only"]
    assert set(imputed["province"]) == set(IMPUTED_PROVINCES)
    assert (imputed["rsyc_mean_volume_m3ha_age100"] > 0.0).all()
    assert (imputed["imputed_taper_m3_per_ha_equivalent"] > 0.0).all()
    assert (imputed["imputed_over_rsyc"] > 0.0).all()
    assert not imputed["imputed_over_rsyc"].isna().any()


@requires_artifacts
def test_artifact_is_deterministic(tmp_path) -> None:
    generated = generate(tmp_path)
    assert set(generated["checks"].values()) == {True}
    for name in ("tree_list.csv", "bucked_logs.csv", "residue_ledger.csv"):
        assert _sha256(tmp_path / name) == _sha256(DEFAULT_OUTPUT_DIR / name)
