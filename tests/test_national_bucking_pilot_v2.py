"""Contract and provenance tests for national bucking pilot v2.

The integration tests read the committed v2 artifact rather than re-bucking
347 measured BC stems.  The selection test is a pure unit test on a small
synthetic frame.
"""

from __future__ import annotations

import hashlib
import json

import numpy as np
import pandas as pd
import pytest

from workflows.national.bucking.national_bucking_pilot_v1 import (
    N_TREES_PER_PROVINCE,
    PROVINCES,
    assert_no_unexpected_deletions,
)
from workflows.national.bucking.national_bucking_pilot_v2 import (
    BC_MEASURED_LABEL,
    BC_SELECTED_TREES_PATH,
    DEFAULT_OUTPUT_DIR,
    SYNTHETIC_LABEL,
    select_bc_stratum_representatives,
)

REQUIRED_ARTIFACTS = (
    "tree_list.csv",
    "bc_tree_list_selection.csv",
    "bucked_logs.csv",
    "residue_ledger.csv",
    "reconciliation.csv",
    "bc_tipsy_logs.csv",
    "bc_tipsy_residue.csv",
    "bc_tipsy_reconciliation.csv",
    "province_summary.csv",
    "coefficient_comparison.csv",
    "v1_v2_comparison.csv",
    "bc_facility_comparison.csv",
    "bc_population_reconciliation.csv",
    "summary.json",
    "report.md",
    "manifest.json",
)

requires_artifacts = pytest.mark.skipif(
    not all((DEFAULT_OUTPUT_DIR / name).exists() for name in REQUIRED_ARTIFACTS),
    reason="national bucking pilot v2 artifacts have not been generated",
)


@pytest.fixture(scope="module")
def artifact() -> dict[str, object]:
    summary = json.loads(
        (DEFAULT_OUTPUT_DIR / "summary.json").read_text(encoding="utf-8")
    )
    manifest = json.loads(
        (DEFAULT_OUTPUT_DIR / "manifest.json").read_text(encoding="utf-8")
    )
    return {
        "dir": DEFAULT_OUTPUT_DIR,
        "summary": summary,
        "manifest": manifest,
        "trees": pd.read_csv(DEFAULT_OUTPUT_DIR / "tree_list.csv"),
        "selection": pd.read_csv(DEFAULT_OUTPUT_DIR / "bc_tree_list_selection.csv"),
        "logs": pd.read_csv(DEFAULT_OUTPUT_DIR / "bucked_logs.csv"),
        "residue": pd.read_csv(DEFAULT_OUTPUT_DIR / "residue_ledger.csv"),
        "reconciliation": pd.read_csv(DEFAULT_OUTPUT_DIR / "reconciliation.csv"),
        "province": pd.read_csv(DEFAULT_OUTPUT_DIR / "province_summary.csv"),
        "comparison": pd.read_csv(DEFAULT_OUTPUT_DIR / "coefficient_comparison.csv"),
        "v1_v2": pd.read_csv(DEFAULT_OUTPUT_DIR / "v1_v2_comparison.csv"),
        "bc_facility": pd.read_csv(DEFAULT_OUTPUT_DIR / "bc_facility_comparison.csv"),
        "population": pd.read_csv(
            DEFAULT_OUTPUT_DIR / "bc_population_reconciliation.csv"
        ),
        "tipsy_logs": pd.read_csv(DEFAULT_OUTPUT_DIR / "bc_tipsy_logs.csv"),
        "tipsy_residue": pd.read_csv(DEFAULT_OUTPUT_DIR / "bc_tipsy_residue.csv"),
        "tipsy_reconciliation": pd.read_csv(
            DEFAULT_OUTPUT_DIR / "bc_tipsy_reconciliation.csv"
        ),
    }


def _source_population() -> pd.DataFrame:
    return pd.read_csv(
        BC_SELECTED_TREES_PATH,
        usecols=[
            "tree_id",
            "site_id",
            "plot_id",
            "species",
            "bec_zone",
            "dbh_cm",
            "height_m",
            "expansion_factor",
            "bucked_status",
            "height_source",
        ],
        dtype={"tree_id": str, "site_id": str, "plot_id": str},
    )


def test_selection_is_pure_and_preserves_phf() -> None:
    frame = pd.DataFrame(
        {
            "tree_id": [f"T{i:03d}" for i in range(12)],
            "species": ["SW"] * 6 + ["PL"] * 6,
            "bec_zone": ["SBS"] * 12,
            "dbh_cm": [8.0, 8.2, 8.4, 9.0, 9.2, 9.4, 12.0, 12.2, 12.4, 13.0, 13.2, 13.4],
            "height_m": [9.0, 9.2, 9.4, 10.0, 10.2, 10.4, 14.0, 14.2, 14.4, 15.0, 15.2, 15.4],
            "source_expansion_factor": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0,
                                        1.5, 2.5, 3.5, 4.5, 5.5, 6.5],
        }
    )
    original = frame.copy(deep=True)
    first = select_bc_stratum_representatives(frame)
    second = select_bc_stratum_representatives(frame)

    assert frame.equals(original)
    assert first.equals(second)
    assert first["tree_id"].is_unique
    assert first["expansion_factor"].sum() == pytest.approx(
        frame["source_expansion_factor"].sum()
    )
    assert set(first["tree_id"]).issubset(set(frame["tree_id"]))
    assert len(first) == 3
    assert set(first["dbh_stratum"]) == {"7.5-10", "10-12.5", "12.5-15"}


@requires_artifacts
def test_bc_empirical_provenance(artifact) -> None:
    trees = artifact["trees"]
    selection = artifact["selection"]
    summary = artifact["summary"]
    bc = trees[trees["jurisdiction"] == "BC"].copy()
    source = _source_population()
    source_bucked = source[source["bucked_status"] == "bucked"].copy()

    assert len(bc) == summary["counts"]["measured_bc_trees"]
    assert (bc["tree_list_label"] == BC_MEASURED_LABEL).all()
    assert bc["measured_tree_list"].all()
    assert (~bc["synthetic"]).all()
    assert (~bc["model_estimate"]).all()
    assert (~bc["observed"]).all()
    assert (~bc["policy_grade"]).all()
    assert (~bc["national_claim"]).all()
    assert bc["source_tree_id"].is_unique
    assert set(bc["source_tree_id"]).issubset(set(source["tree_id"]))
    assert bc["source_expansion_factor"].gt(0.0).all()
    assert bc["stratum_n_trees"].gt(0).all()
    assert bc["stratum_expansion_factor"].gt(0.0).all()
    assert bc["stratum_id"].str.contains(r"\|").all()

    joined = bc.merge(
        source,
        left_on="source_tree_id",
        right_on="tree_id",
        suffixes=("", "_source"),
        validate="one_to_one",
    )
    assert len(joined) == len(bc)
    assert np.allclose(joined["dbh_cm"], joined["dbh_cm_source"], rtol=0, atol=1e-12)
    assert np.allclose(
        joined["height_m"], joined["height_m_source"], rtol=0, atol=1e-12
    )
    assert np.allclose(
        joined["source_expansion_factor"],
        joined["expansion_factor_source"],
        rtol=0,
        atol=1e-12,
    )

    assert bc["expansion_factor"].sum() == pytest.approx(
        source_bucked["expansion_factor"].sum(), rel=1e-12, abs=1e-6
    )
    assert bc["expansion_factor"].sum() == pytest.approx(
        selection["pooled_expansion_factor"].sum(), rel=1e-12, abs=1e-6
    )
    assert summary["bc_tree_list"]["phf_relative_error"] == pytest.approx(
        0.0, abs=1e-12
    )
    assert summary["bc_tree_list"]["excluded_not_bucked_rows"] > 0
    assert summary["bc_tree_list"]["selection_method"].startswith(
        "one measured PHF-weighted median"
    )


@requires_artifacts
def test_thirteen_provinces_and_labels(artifact) -> None:
    trees = artifact["trees"]
    summary = artifact["summary"]

    assert set(trees["jurisdiction"]) == set(PROVINCES)
    counts = trees.groupby("jurisdiction").size()
    assert set(counts.index) == set(PROVINCES)
    assert counts["BC"] == summary["counts"]["measured_bc_trees"]
    for province in PROVINCES:
        if province != "BC":
            assert counts[province] == N_TREES_PER_PROVINCE
    assert trees["tree_id"].is_unique

    other = trees[trees["jurisdiction"] != "BC"]
    assert (other["tree_list_label"] == SYNTHETIC_LABEL).all()
    assert (~other["measured_tree_list"]).all()
    assert other["synthetic"].all()
    assert other["model_estimate"].all()
    assert (~other["observed"]).all()
    assert (~other["policy_grade"]).all()
    assert (~other["national_claim"]).all()


@requires_artifacts
def test_volume_and_residue_conservation(artifact) -> None:
    logs = artifact["logs"]
    residue = artifact["residue"]
    reconciliation = artifact["reconciliation"]
    province = artifact["province"]
    summary = artifact["summary"]
    tipsy_logs = artifact["tipsy_logs"]
    tipsy_residue = artifact["tipsy_residue"]
    tipsy_reconciliation = artifact["tipsy_reconciliation"]

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

    assert abs(province["recovered_gross_m3_expanded"].sum()
               - logs["volume_m3_expanded"].sum()) < 1e-8
    assert abs(province["residue_m3_expanded"].sum()
               - residue["residue_volume_m3_expanded"].sum()) < 1e-8
    assert abs(province["taper_total_m3_expanded"].sum()
               - residue["taper_total_m3_expanded"].sum()) < 1e-8

    totals = summary["totals"]
    assert totals["balance_error_m3"] < 1e-8
    assert totals["net_plus_defect_error_m3"] < 1e-8
    assert totals["max_balance_error_m3"] < 1e-8

    for row in tipsy_reconciliation.itertuples(index=False):
        assert abs(row.recovered_log_m3 + row.residue_m3 - row.taper_total_m3) < 1e-8
    assert (
        abs(tipsy_logs["volume_m3"].sum()
            - tipsy_reconciliation["recovered_log_m3"].sum())
        < 1e-8
    )
    assert (
        abs(tipsy_residue["residue_volume_m3"].sum()
            - tipsy_reconciliation["residue_m3"].sum())
        < 1e-8
    )


@requires_artifacts
def test_no_nan_or_negative_core_columns(artifact) -> None:
    optional = {
        "stem_residue_odt",
        "branch_odt",
        "foliage_odt",
        "branch_foliage_odt",
        "total_residue_odt",
        "recovery_factor",
        "recoverable_residue_odt",
        "branch_foliage_equation_id",
        "branch_foliage_equation_species",
        "branch_foliage_reference",
        "branch_foliage_source",
        "branch_foliage_source_licence",
        "stem_residue_odt_expanded",
        "branch_odt_expanded",
        "foliage_odt_expanded",
        "branch_foliage_odt_expanded",
        "total_residue_odt_expanded",
        "recoverable_residue_odt_expanded",
    }
    frames = {
        "tree_list": artifact["trees"],
        "bucked_logs": artifact["logs"],
        "residue_ledger": artifact["residue"],
        "reconciliation": artifact["reconciliation"],
        "province_summary": artifact["province"],
        "coefficient_comparison": artifact["comparison"],
        "bc_facility": artifact["bc_facility"],
        "bc_tipsy_logs": artifact["tipsy_logs"],
        "bc_tipsy_residue": artifact["tipsy_residue"],
        "bc_tipsy_reconciliation": artifact["tipsy_reconciliation"],
    }
    for name, frame in frames.items():
        numeric = frame.select_dtypes(include="number")
        core = numeric.drop(
            columns=[column for column in optional if column in numeric.columns]
        )
        assert not core.isna().any().any(), name
        assert (core >= 0.0).all().all(), name
        required = frame.drop(
            columns=[column for column in optional if column in frame.columns]
        )
        assert required.notna().all().all(), name

    for frame_name in ("residue_ledger", "bc_tipsy_residue"):
        frame = frames[frame_name]
        bc = frame[frame["jurisdiction"] == "BC"]
        assert bc["branch_foliage_estimated"].all()
        for column in (
            "stem_residue_odt",
            "branch_odt",
            "foliage_odt",
            "branch_foliage_odt",
            "total_residue_odt",
            "recoverable_residue_odt",
        ):
            assert bc[column].notna().all(), (frame_name, column)
            assert (bc[column] >= 0.0).all(), (frame_name, column)

    delta = artifact["v1_v2"]
    for column in ("solid_share_delta", "residue_odt_per_recovered_delta"):
        assert column in delta.columns
        assert delta[column].notna().all()


@requires_artifacts
def test_grade_evidence_and_dimension_only(artifact) -> None:
    logs = artifact["logs"]
    tipsy_logs = artifact["tipsy_logs"]
    residue = artifact["residue"]
    trees = artifact["trees"]

    assert (logs["grade"] == "not_assigned").all()
    assert (logs["grade_status"] == "not_assigned").all()
    assert (logs["sort_basis"] == "dimension_only").all()
    assert (logs["sort_rule_set"] == "national_pilot_sorts").all()
    assert (logs["price_deck_id"] == "NAT-PILOT-2026").all()
    assert (logs["small_end_dib_cm"] <= logs["large_end_dib_cm"] + 1e-6).all()

    assert (tipsy_logs["grade"] == "not_assigned").all()
    assert (tipsy_logs["grade_status"] == "not_assigned").all()
    assert (tipsy_logs["sort_basis"] == "dimension_only").all()
    assert (tipsy_logs["sort_rule_set"] == "bc_interior_tipsy_sorts").all()

    assert (logs["taper_evidence"] == "published_table").all()
    assert (residue["taper_evidence"] == "published_table").all()
    bc = trees[trees["jurisdiction"] == "BC"]
    assert (bc["evidence"] == "published_table").all()
    assert (bc["taper_status"] == "local_published").all()
    assert bc["taper_bec_zone"].isin({"SBS", "ICH", "ESSF", "SBPS"}).all()


@requires_artifacts
def test_comparison_tables(artifact) -> None:
    province = artifact["province"]
    comparison = artifact["comparison"]
    v1_v2 = artifact["v1_v2"]
    bc_facility = artifact["bc_facility"]
    population = artifact["population"]
    summary = artifact["summary"]

    assert len(province) == 13
    assert len(comparison) == 13
    assert set(comparison["province"]) == set(PROVINCES)
    assert (province["volume_balance_m3"] < 1e-8).all()
    assert set(comparison["comparison_status"]).issubset(
        {"within_20pct", "outside_20pct", "not_comparable_zero_harvest"}
    )
    assert comparison[comparison["province"] == "NU"]["comparison_status"].iloc[0] == (
        "not_comparable_zero_harvest"
    )
    assert (comparison["model_residue_odt_per_recovered_m3"] >= 0.0).all()

    assert set(v1_v2["comparison_scope"]) == {
        "province",
        "national_province_macro",
        "national_mixed_expanded_aggregate",
    }
    assert len(v1_v2) == 15
    bc_row = v1_v2[v1_v2["province"] == "BC"].iloc[0]
    assert bc_row["v1_n_trees"] == 10
    assert bc_row["v2_n_trees"] == province[province["province"] == "BC"][
        "n_trees"
    ].iloc[0]
    assert bc_row["solid_share_delta"] < 0.0
    unchanged = v1_v2[
        (v1_v2["comparison_scope"] == "province") & (v1_v2["province"] != "BC")
    ]
    assert np.allclose(unchanged["solid_share_delta"], 0.0, atol=1e-12)
    macro = v1_v2[v1_v2["province"] == "NATIONAL_MACRO"].iloc[0]
    assert macro["v1_solid_share"] > 0.0
    assert macro["v2_solid_share"] > 0.0
    assert macro["solid_share_delta"] == pytest.approx(
        macro["v2_solid_share"] - macro["v1_solid_share"], abs=1e-12
    )

    assert len(bc_facility) == 4
    assert set(bc_facility["variant"]) == {
        "BC_V1_SYNTHETIC_NATIONAL_SORTS",
        "BC_V2_MEASURED_TIPSY_SORTS",
        "BC_V2_MEASURED_NATIONAL_SORTS",
        "BC_FACILITY_FEEDSTOCK",
    }
    reference = bc_facility[
        bc_facility["variant"] == "BC_FACILITY_FEEDSTOCK"
    ].iloc[0]
    national = bc_facility[
        bc_facility["variant"] == "BC_V2_MEASURED_NATIONAL_SORTS"
    ].iloc[0]
    assert national["solid_share_model_over_facility"] == pytest.approx(
        national["solid_share"] / reference["solid_share"]
    )
    assert 0.8 <= national["solid_share_model_over_facility"] <= 1.2

    assert len(population) == 4
    tipsy = population[population["metric"] == "recovered_gross_m3_expanded"].iloc[0]
    assert 0.95 <= tipsy["tipsy_ratio_to_v0"] <= 1.05
    assert summary["comparison"]["bc_population_reconciliation"][0]["metric"] == (
        "taper_total_m3_expanded"
    )


@requires_artifacts
def test_manifest_checks_labels_and_scope(artifact) -> None:
    manifest = artifact["manifest"]
    summary = artifact["summary"]

    assert manifest["scope"] == (
        "pilot_measured_bc_plus_representative_synthetic_others"
    )
    assert manifest["national_claim"] is False
    assert summary["scope"] == manifest["scope"]
    assert summary["labels"]["bc_tree_list"] == BC_MEASURED_LABEL
    assert summary["labels"]["other_tree_lists"] == SYNTHETIC_LABEL
    assert summary["labels"]["bc_tree_geometry_model_estimate"] is False
    assert summary["labels"]["bc_tree_geometry_synthetic"] is False
    assert summary["labels"]["bc_product_model_estimate"] is True
    assert summary["labels"]["observed"] is False
    assert summary["labels"]["policy_grade"] is False
    assert summary["labels"]["national_claim"] is False
    assert summary["labels"]["ui_or_public_osecan_changed"] is False

    assert manifest["checks"]
    assert all(check["status"] == "PASS" for check in manifest["checks"])
    for record in manifest["outputs"]:
        path = DEFAULT_OUTPUT_DIR / record["path"]
        assert path.exists(), record["path"]
        assert record["bytes"] == path.stat().st_size
        assert len(record["sha256"]) == 64
    assert manifest["inputs"]["bc_psp_selected_trees"]["sha256"]
    assert manifest["inputs"]["bc_psp_tree_ledger"]["sha256"]
    assert manifest["inputs"]["facility_product_chain"]["sha256"]
    snapshot = manifest["inputs"]["bc_tipsy_sort_rules_snapshot"]
    snapshot_path = DEFAULT_OUTPUT_DIR.parents[4] / snapshot["path"]
    assert snapshot_path.exists()
    assert snapshot["sha256"] == hashlib.sha256(snapshot_path.read_bytes()).hexdigest()
    assert (DEFAULT_OUTPUT_DIR / "bc_tipsy_sort_rules.csv").exists()
    assert manifest["counts"]["measured_bc_trees"] > 100
    assert manifest["counts"]["synthetic_other_trees"] == 12 * N_TREES_PER_PROVINCE


@requires_artifacts
def test_committed_output_counts_match_manifest(artifact) -> None:
    manifest = artifact["manifest"]
    counts = manifest["counts"]
    assert len(artifact["trees"]) == counts["trees"]
    assert len(artifact["logs"]) == counts["logs"]
    assert len(artifact["residue"]) == counts["residue_rows"]
    assert len(artifact["reconciliation"]) == counts["reconciliation_rows"]
    assert len(artifact["province"]) == counts["province_summary_rows"]
    assert len(artifact["comparison"]) == counts["coefficient_comparison_rows"]
    assert len(artifact["v1_v2"]) == counts["v1_v2_comparison_rows"]


def test_no_unexpected_deletions() -> None:
    assert assert_no_unexpected_deletions() == []
