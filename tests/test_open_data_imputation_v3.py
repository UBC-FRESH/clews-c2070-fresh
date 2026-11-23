"""Contract tests for the E2 v3 density-calibrated tree lists.

The tests enforce the nine-province scope, E2c provenance, province-specific
density/expansion, open-volume comparison, volume and residue conservation,
numeric sanity and deterministic regeneration.  They read only committed
artifacts; the full provincial layers are not required.
"""

from __future__ import annotations

import hashlib
import json

import numpy as np
import pandas as pd
import pytest

from workflows.national.bucking.open_data_imputation import IMPUTED_PROVINCES
from workflows.national.bucking.open_data_imputation_v3 import (
    ARTIFACT_ID,
    DEFAULT_OUTPUT_DIR,
    DENSITY_DIR,
    PROVENANCE_E2C,
    TREE_LIST_E2C,
    V2_DIR,
    generate,
)

REQUIRED_ARTIFACTS = (
    "tree_list.csv",
    "bucked_logs.csv",
    "residue_ledger.csv",
    "province_summary.csv",
    "product_coefficients.csv",
    "comparison_v2_v3.csv",
    "density_calibration.csv",
    "volume_context.csv",
    "distribution_labels.csv",
    "summary.json",
    "manifest.json",
    "report.md",
)

requires_artifacts = pytest.mark.skipif(
    not all((DEFAULT_OUTPUT_DIR / name).exists() for name in REQUIRED_ARTIFACTS),
    reason="E2 v3 artifact has not been generated",
)
requires_density = pytest.mark.skipif(
    not (DENSITY_DIR / "density_targets.csv").exists(),
    reason="provincial density evidence artifact has not been generated",
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
        "comparison": pd.read_csv(DEFAULT_OUTPUT_DIR / "comparison_v2_v3.csv"),
        "density": pd.read_csv(DEFAULT_OUTPUT_DIR / "density_calibration.csv"),
        "labels": pd.read_csv(DEFAULT_OUTPUT_DIR / "distribution_labels.csv"),
        "volume": pd.read_csv(DEFAULT_OUTPUT_DIR / "volume_context.csv"),
        "targets": pd.read_csv(DENSITY_DIR / "density_targets.csv"),
        "v2_trees": pd.read_csv(V2_DIR / "tree_list.csv"),
        "v2_province": pd.read_csv(V2_DIR / "province_summary.csv"),
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
    assert set(artifact["density"]["province"]) == set(IMPUTED_PROVINCES)


@requires_artifacts
def test_e2c_provenance_and_no_measured_or_national_claim(artifact) -> None:
    trees = artifact["trees"]
    assert ARTIFACT_ID == "open_data_imputation_v3"
    assert set(trees["provenance_tier"]) == {PROVENANCE_E2C}
    assert set(trees["tree_list_label"]) == {TREE_LIST_E2C}
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
@requires_density
def test_density_targets_and_expansion(artifact) -> None:
    trees = artifact["trees"]
    targets = artifact["targets"].set_index("province")
    density = artifact["density"].set_index("province")
    for province in IMPUTED_PROVINCES:
        subset = trees[trees["jurisdiction"] == province]
        target = float(targets.loc[province, "density_stems_ha"])
        assert target > 0.0
        assert subset["expansion_factor"].sum() == pytest.approx(target, abs=1e-6)
        assert density.loc[province, "density_stems_ha"] == pytest.approx(target)
        assert subset["density_stems_ha"].nunique() == 1
        assert float(subset["density_stems_ha"].iloc[0]) == pytest.approx(target)
    assert (
        density.loc["SK", "density_stems_ha"] != density.loc["NS", "density_stems_ha"]
    )
    assert density.loc["SK", "density_evidence_class"] == "open_inventory"
    assert density.loc["NS", "density_evidence_class"] == "open_inventory"
    assert density.loc["PE", "density_evidence_class"] == "open_inventory"


@requires_artifacts
def test_density_evidence_labels_are_explicit(artifact) -> None:
    density = artifact["density"].set_index("province")
    labels = artifact["labels"].set_index("province")
    assert set(density["density_evidence_class"]) == {
        "open_inventory",
        "donor",
        "proxy",
    }
    for province in ("SK", "NS", "PE"):
        assert density.loc[province, "density_evidence_class"] == "open_inventory"
        assert density.loc[province, "density_source_ids"].strip()
        assert density.loc[province, "density_basis"].strip()
    assert density.loc["AB", "density_evidence_class"] == "proxy"
    assert density.loc["MB", "density_evidence_class"] == "donor"
    for province in ("ON", "NL", "NT", "NU"):
        assert density.loc[province, "density_evidence_class"] == "proxy"
        assert "1000" in str(density.loc[province, "expansion_label"])
    for province in IMPUTED_PROVINCES:
        assert str(labels.loc[province, "proxy_or_donor_gap"]).strip()
        assert str(labels.loc[province, "expansion_label"]).strip()
    assert labels.loc["SK", "proxy_or_donor_gap"] == "open_inventory_density"
    assert labels.loc["MB", "proxy_or_donor_gap"] == "donor_density_gap"
    assert labels.loc["AB", "proxy_or_donor_gap"] == "no_open_density_proxy"


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
def test_volume_scales_with_density_from_v2(artifact) -> None:
    trees = artifact["trees"]
    v2_trees = artifact["v2_trees"]
    comparison = artifact["comparison"].set_index("province")
    for province in IMPUTED_PROVINCES:
        v3 = trees[trees["jurisdiction"] == province].sort_values("tree_id")
        v2 = v2_trees[v2_trees["jurisdiction"] == province].sort_values("tree_id")
        assert len(v3) == len(v2) == 36
        assert np.allclose(v3["height_m"].to_numpy(), v2["height_m"].to_numpy())
        assert np.allclose(v3["dbh_cm"].to_numpy(), v2["dbh_cm"].to_numpy())
        assert np.allclose(
            v3["expansion_factor"].to_numpy(),
            v2["expansion_factor"].to_numpy()
            * comparison.loc[province, "density_ratio_v3_to_v2"],
        )
    province = artifact["province"].set_index("province")
    v2_summary = artifact["v2_province"].set_index("province")
    for province_code in IMPUTED_PROVINCES:
        assert province.loc[province_code, "taper_total_m3_expanded"] == pytest.approx(
            v2_summary.loc[province_code, "taper_total_m3_expanded"]
            * comparison.loc[province_code, "density_ratio_v3_to_v2"],
            rel=1e-12,
            abs=1e-9,
        )


@requires_artifacts
def test_open_volume_comparison_sk_ns_pe(artifact) -> None:
    comparison = artifact["comparison"].set_index("province")
    for province in ("SK", "NS", "PE"):
        open_volume = float(comparison.loc[province, "inventory_volume_mean_m3ha"])
        assert open_volume > 0.0
        ratio = float(comparison.loc[province, "open_volume_ratio_v3"])
        assert ratio > 0.0
        assert abs(ratio - 1.0) <= 0.50, (province, ratio)
        assert comparison.loc[province, "volume_context_status"] == "within_50pct"
        assert comparison.loc[province, "density_evidence_class"] == "open_inventory"
    for province in ("AB", "MB", "ON", "NL", "NT", "NU"):
        assert (
            comparison.loc[province, "inventory_volume_mean_m3ha"] == 0.0
            or pd.isna(comparison.loc[province, "inventory_volume_mean_m3ha"])
        )


@requires_artifacts
def test_numeric_sanity(artifact) -> None:
    trees = artifact["trees"]
    numeric_tree = [
        "dbh_cm",
        "height_m",
        "expansion_factor",
        "age_years",
        "density_stems_ha",
    ]
    for column in numeric_tree:
        assert trees[column].notna().all(), column
        assert np.isfinite(trees[column].to_numpy(dtype=float)).all(), column
        assert (trees[column] >= 0.0).all(), column
    province = artifact["province"]
    numeric_province = [
        "n_trees",
        "mean_height_m",
        "mean_dbh_cm",
        "density_stems_ha",
        "taper_total_m3_expanded",
        "recovered_gross_m3_expanded",
        "residue_m3_expanded",
        "total_residue_odt_expanded",
        "volume_balance_m3",
    ]
    for column in numeric_province:
        assert province[column].notna().all(), column
        assert np.isfinite(province[column].to_numpy(dtype=float)).all(), column
        assert (province[column] >= 0.0).all(), column
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
    for column in ("density_calibration.csv",):
        frame = pd.read_csv(DEFAULT_OUTPUT_DIR / column)
        assert frame["density_stems_ha"].notna().all()
        assert (frame["density_stems_ha"] > 0.0).all()


@requires_artifacts
@requires_density
def test_density_evidence_artifact_scope() -> None:
    targets = pd.read_csv(DENSITY_DIR / "density_targets.csv")
    quantiles = pd.read_csv(DENSITY_DIR / "density_quantiles.csv")
    ab_classes = pd.read_csv(DENSITY_DIR / "ab_density_structure.csv")
    assert set(targets["province"]) == set(IMPUTED_PROVINCES)
    assert (targets["density_stems_ha"] > 0.0).all()
    assert set(targets["density_evidence_class"]) == {
        "open_inventory",
        "donor",
        "proxy",
    }
    assert set(quantiles["province"]) == {"SK", "NS", "PE", "MB"}
    assert set(ab_classes["class_type"]) == {"density", "structure"}
    for class_type in ("density", "structure"):
        shares = ab_classes[ab_classes["class_type"] == class_type]["area_share"].sum()
        assert shares == pytest.approx(1.0, abs=1e-6)


@requires_artifacts
def test_artifact_is_deterministic(tmp_path) -> None:
    generated = generate(tmp_path)
    assert set(generated["checks"].values()) == {True}
    for name in (
        "tree_list.csv",
        "bucked_logs.csv",
        "residue_ledger.csv",
        "province_summary.csv",
        "product_coefficients.csv",
        "comparison_v2_v3.csv",
        "density_calibration.csv",
    ):
        assert _sha256(tmp_path / name) == _sha256(DEFAULT_OUTPUT_DIR / name)
