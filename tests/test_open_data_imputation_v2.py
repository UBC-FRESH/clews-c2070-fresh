"""Contract tests for the E2 v2 open-inventory-informed tree lists.

The tests enforce the nine-province scope, E2b provenance, local/donor
distribution labels, volume and residue conservation, numeric sanity and
deterministic regeneration.  They read only committed artifacts; the full
provincial layers are not required.
"""

from __future__ import annotations

import hashlib
import json

import numpy as np
import pandas as pd
import pytest

from workflows.national.bucking.open_data_imputation_v2 import (
    DEFAULT_OUTPUT_DIR,
    DISTRIBUTION_DIR,
    IMPUTED_PROVINCES,
    PROVENANCE_E2B,
    generate,
)

REQUIRED_ARTIFACTS = (
    "tree_list.csv",
    "bucked_logs.csv",
    "residue_ledger.csv",
    "province_summary.csv",
    "product_coefficients.csv",
    "comparison_v1_v2.csv",
    "species_comparison_v1_v2.csv",
    "volume_context.csv",
    "distribution_labels.csv",
    "dbh_inference_equations.csv",
    "summary.json",
    "manifest.json",
    "report.md",
)

requires_artifacts = pytest.mark.skipif(
    not all((DEFAULT_OUTPUT_DIR / name).exists() for name in REQUIRED_ARTIFACTS),
    reason="E2 v2 artifact has not been generated",
)
requires_distributions = pytest.mark.skipif(
    not (DISTRIBUTION_DIR / "species_composition.csv").exists(),
    reason="provincial distribution artifact has not been generated",
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
        "coefficients": pd.read_csv(DEFAULT_OUTPUT_DIR / "product_coefficients.csv"),
        "comparison": pd.read_csv(DEFAULT_OUTPUT_DIR / "comparison_v1_v2.csv"),
        "species_comparison": pd.read_csv(
            DEFAULT_OUTPUT_DIR / "species_comparison_v1_v2.csv"
        ),
        "labels": pd.read_csv(DEFAULT_OUTPUT_DIR / "distribution_labels.csv"),
        "volume": pd.read_csv(DEFAULT_OUTPUT_DIR / "volume_context.csv"),
        "equations": pd.read_csv(DEFAULT_OUTPUT_DIR / "dbh_inference_equations.csv"),
    }


@requires_artifacts
def test_nine_provinces_and_thirty_six_trees(artifact) -> None:
    trees = artifact["trees"]
    assert set(trees["jurisdiction"]) == set(IMPUTED_PROVINCES)
    for province in IMPUTED_PROVINCES:
        assert len(trees[trees["jurisdiction"] == province]) == 36
    assert len(trees) == 324
    assert set(artifact["province"]["province"]) == set(IMPUTED_PROVINCES)
    assert len(artifact["comparison"]) == 9


@requires_artifacts
def test_e2b_provenance_and_no_measured_or_national_claim(artifact) -> None:
    trees = artifact["trees"]
    assert set(trees["provenance_tier"]) == {PROVENANCE_E2B}
    assert not trees["measured"].any()
    assert trees["imputed"].all()
    assert not trees["synthetic"].any()
    assert trees["measured_tree_list"].eq(False).all()
    assert not trees["national_claim"].any()
    assert not trees["observed"].any()
    assert not trees["policy_grade"].any()
    assert set(trees["taper_status"]) == {"donor_published"}
    assert set(trees["taper_jurisdiction"]).issubset({"BC", "QC"})


@requires_artifacts
def test_inventory_informed_flags(artifact) -> None:
    labels = artifact["labels"].set_index("province")
    informed = {"AB", "SK", "MB", "NS", "PE"}
    for province in IMPUTED_PROVINCES:
        expected = province in informed
        assert bool(labels.loc[province, "inventory_informed"]) is expected
    assert labels.loc["AB", "distribution_tier"] == "provincial_inventory"
    assert labels.loc["SK", "distribution_tier"] == "provincial_inventory_group"
    for province in ("ON", "NL", "NT", "NU"):
        assert labels.loc[province, "distribution_tier"] == "scanfi_donor"


@requires_artifacts
def test_distribution_labels_are_explicit(artifact) -> None:
    trees = artifact["trees"]
    assert set(trees["joint_basis"]) == {
        "local_height_inferred_dbh",
        "paired_local_marginals",
        "donor_joint",
        "local_joint",
        "paired_multi_source",
    }
    assert (trees["dbh_basis"] == "inferred_dbh").any()
    assert (trees["dbh_basis"] == "inventory_qmd").any()
    assert (trees["dbh_basis"] == "inventory_avdi").any()
    assert (trees["dbh_basis"] == "donor_dbh").any()
    assert trees["distribution_label"].astype(str).str.strip().ne("").all()
    assert trees["inference_equation"].astype(str).str.strip().ne("").all()
    assert trees["source_ids"].astype(str).str.strip().ne("").all()


@requires_artifacts
def test_province_specific_distribution_contracts(artifact) -> None:
    trees = artifact["trees"]
    ab = trees[trees["jurisdiction"] == "AB"]
    assert ab["dbh_basis"].eq("inferred_dbh").all()
    assert ab["height_basis"].eq("inventory_height").all()
    assert ab["joint_basis"].eq("local_height_inferred_dbh").all()

    sk = trees[trees["jurisdiction"] == "SK"]
    assert sk["dbh_basis"].eq("inventory_qmd").all()
    assert sk["joint_basis"].eq("paired_local_marginals").all()

    mb = trees[trees["jurisdiction"] == "MB"]
    assert mb["joint_basis"].eq("donor_joint").all()
    assert (mb["age_basis"] == "inventory_age").any()

    ns = trees[trees["jurisdiction"] == "NS"]
    assert ns["dbh_basis"].eq("inventory_avdi").all()
    assert ns["joint_basis"].eq("local_joint").all()

    pe = trees[trees["jurisdiction"] == "PE"]
    assert pe["dbh_basis"].eq("inferred_dbh").all()
    assert pe["joint_basis"].eq("paired_multi_source").all()

    for province in ("ON", "NL", "NT", "NU"):
        subset = trees[trees["jurisdiction"] == province]
        assert subset["joint_basis"].eq("donor_joint").all()


@requires_artifacts
def test_expansion_and_taper_domain_floor(artifact) -> None:
    trees = artifact["trees"]
    for province in IMPUTED_PROVINCES:
        subset = trees[trees["jurisdiction"] == province]
        assert subset["expansion_factor"].sum() == pytest.approx(1000.0, abs=1e-6)
    assert (trees["height_m"] >= 5.0).all()
    assert (trees["dbh_cm"] >= 5.0).all()
    if trees["taper_domain_floor_applied"].any():
        assert trees["taper_domain_floor_note"].astype(str).str.contains("merchantable").any()


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
    province = artifact["province"]
    assert (province["volume_balance_m3"].abs() <= 1e-6).all()
    assert np.allclose(
        province["total_residue_odt_expanded"],
        province["stem_residue_odt_expanded"]
        + province["branch_foliage_odt_expanded"],
        rtol=1e-9,
        atol=1e-6,
    )


@requires_artifacts
def test_numeric_sanity(artifact) -> None:
    trees = artifact["trees"]
    numeric_tree = ["dbh_cm", "height_m", "expansion_factor", "age_years"]
    for column in numeric_tree:
        assert trees[column].notna().all(), column
        assert np.isfinite(trees[column].to_numpy(dtype=float)).all(), column
        assert (trees[column] >= 0.0).all(), column
    province = artifact["province"]
    numeric_province = [
        "n_trees",
        "mean_height_m",
        "mean_dbh_cm",
        "taper_total_m3_expanded",
        "recovered_gross_m3_expanded",
        "residue_m3_expanded",
        "total_residue_odt_expanded",
        "volume_balance_m3",
    ]
    for column in numeric_province:
        assert province[column].notna().all(), column
        assert np.isfinite(province[column].to_numpy(dtype=float)).all(), column
    for column in (
        "sawlog_share_of_recovered",
        "pulpwood_share_of_recovered",
        "solid_share_of_recovered",
        "total_residue_odt_per_recovered_m3",
    ):
        assert province[column].notna().all()
        assert (province[column] >= 0.0).all()
    assert np.allclose(
        province["sawlog_share_of_recovered"]
        + province["pulpwood_share_of_recovered"]
        + province["other_share_of_recovered"],
        1.0,
        rtol=1e-9,
        atol=1e-9,
    )
    coefficients = artifact["coefficients"]
    assert np.allclose(
        coefficients["total_residue_odt_per_m3"],
        coefficients["stem_residue_odt_per_m3"]
        + coefficients["branch_foliage_odt_per_m3"],
        rtol=1e-9,
        atol=1e-12,
    )
    assert np.allclose(
        coefficients["recoverable_odt_per_m3_r060"],
        coefficients["total_residue_odt_per_m3"] * 0.60,
        rtol=1e-9,
        atol=1e-12,
    )


@requires_artifacts
def test_comparison_and_species_labels(artifact) -> None:
    comparison = artifact["comparison"]
    assert set(comparison["province"]) == set(IMPUTED_PROVINCES)
    assert not comparison["v1_inventory_informed"].any()
    assert comparison["v2_inventory_informed"].isin([True, False]).all()
    assert comparison["species_composition_l1"].between(0.0, 1.0).all()
    species = artifact["species_comparison"]
    assert set(species["province"]) == set(IMPUTED_PROVINCES)
    assert species["v1_expansion_share"].between(0.0, 1.0).all()
    assert species["v2_expansion_share"].between(0.0, 1.0).all()


@requires_artifacts
def test_volume_context_is_labelled(artifact) -> None:
    volume = artifact["volume"].set_index("province")
    assert set(volume.index) == set(IMPUTED_PROVINCES)
    assert "SK" in volume.index and "NS" in volume.index and "PE" in volume.index
    for province in ("SK", "NS", "PE"):
        assert float(volume.loc[province, "inventory_volume_mean_m3ha"]) > 0.0
        assert str(volume.loc[province, "inventory_volume_source"]).strip()
    for province in ("AB", "MB", "ON", "NL", "NT", "NU"):
        assert str(volume.loc[province, "inventory_volume_source"]) == "not_available"


@requires_distributions
def test_distribution_artifact_scope_and_provenance() -> None:
    composition = pd.read_csv(DISTRIBUTION_DIR / "species_composition.csv")
    quantiles = pd.read_csv(DISTRIBUTION_DIR / "attribute_quantiles.csv")
    provenance = pd.read_csv(DISTRIBUTION_DIR / "source_provenance.csv")
    summary = pd.read_csv(DISTRIBUTION_DIR / "province_summary.csv")
    assert set(summary["province"]) == set(IMPUTED_PROVINCES)
    assert {"AB", "SK", "MB", "NS", "PE"} <= set(composition["province"])
    for province in ("AB", "SK", "MB", "NS", "PE"):
        shares = composition[composition["province"] == province]["share"].sum()
        assert shares == pytest.approx(1.0, abs=1e-6)
    assert {"height", "dbh", "volume", "age"} <= set(quantiles["attribute"])
    for row in provenance.to_dict(orient="records"):
        assert str(row["licence"]).strip()
        assert len(str(row["source_sha256"])) == 64
        assert str(row["source_url"]).startswith("http")


@requires_artifacts
def test_dbh_inference_equations_are_documented(artifact) -> None:
    equations = artifact["equations"]
    ab = equations[equations["province"] == "AB"]
    assert not ab.empty
    assert ab["coefficient_a"].notna().all()
    assert ab["coefficient_b"].notna().all()
    assert (ab["n"] >= 8).all()
    assert (ab["rmse_log_dbh"] >= 0.0).all()
    pe = equations[equations["province"] == "PE"]
    assert len(pe) == 1
    assert "sqrt" in pe.iloc[0]["form"]


@requires_artifacts
def test_artifact_is_deterministic(tmp_path) -> None:
    generated = generate(tmp_path)
    assert set(generated["checks"].values()) == {True}
    for name in (
        "tree_list.csv",
        "bucked_logs.csv",
        "residue_ledger.csv",
        "province_summary.csv",
        "comparison_v1_v2.csv",
    ):
        assert _sha256(tmp_path / name) == _sha256(DEFAULT_OUTPUT_DIR / name)
