"""Contract tests for the measured four-province bucking pilot.

The integration tests read the committed artifact instead of re-bucking 591
measured stems.  They enforce the pilot's fail-closed labels: only BC, QC, NB
and YT; measured tree lists only; local or labelled donor tapers; no national
claim; and per-tree volume/residue conservation.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from workflows.national.bucking.four_province_bucking_pilot import (
    DEFAULT_OUTPUT_DIR,
    PILOT_PROVINCES,
    _weighted_median,
)

REQUIRED_ARTIFACTS = (
    "tree_list.csv",
    "sample_selection.csv",
    "sample_audit.csv",
    "bucked_logs.csv",
    "residue_ledger.csv",
    "tree_summary.csv",
    "province_summary.csv",
    "taper_dispatch.csv",
    "taper_coverage.csv",
    "coefficient_comparison.csv",
    "sort_rules.csv",
    "summary.json",
    "report.md",
    "manifest.json",
)

requires_artifacts = pytest.mark.skipif(
    not all((DEFAULT_OUTPUT_DIR / name).exists() for name in REQUIRED_ARTIFACTS),
    reason="four-province bucking pilot artifact has not been generated",
)


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
        "selection": pd.read_csv(DEFAULT_OUTPUT_DIR / "sample_selection.csv"),
        "audit": pd.read_csv(DEFAULT_OUTPUT_DIR / "sample_audit.csv"),
        "logs": pd.read_csv(DEFAULT_OUTPUT_DIR / "bucked_logs.csv"),
        "residue": pd.read_csv(DEFAULT_OUTPUT_DIR / "residue_ledger.csv"),
        "tree_summary": pd.read_csv(DEFAULT_OUTPUT_DIR / "tree_summary.csv"),
        "province": pd.read_csv(DEFAULT_OUTPUT_DIR / "province_summary.csv"),
        "dispatch": pd.read_csv(DEFAULT_OUTPUT_DIR / "taper_dispatch.csv"),
        "coverage": pd.read_csv(DEFAULT_OUTPUT_DIR / "taper_coverage.csv"),
        "comparison": pd.read_csv(
            DEFAULT_OUTPUT_DIR / "coefficient_comparison.csv"
        ),
    }


def test_weighted_median_is_deterministic() -> None:
    values = np.array([1.0, 2.0, 10.0, 20.0])
    weights = np.array([1.0, 1.0, 1.0, 1.0])
    # The helper returns the lower weighted median for an even, balanced set.
    assert _weighted_median(values, weights) == pytest.approx(2.0)
    assert _weighted_median(values, weights) == _weighted_median(values, weights)


@requires_artifacts
def test_exactly_four_measured_provinces(artifact) -> None:
    trees = artifact["trees"]
    logs = artifact["logs"]
    residue = artifact["residue"]
    summary = artifact["summary"]

    assert set(trees["jurisdiction"]) == set(PILOT_PROVINCES)
    assert set(PILOT_PROVINCES) == {"BC", "QC", "NB", "YT"}
    assert trees["tree_id"].is_unique
    for column in ("source_site_id", "source_plot_id", "source_tree_record_id"):
        assert trees[column].astype(str).str.strip().ne("").all()
        assert trees[column].notna().all()
    assert trees["measured_tree_list"].all()
    assert not trees["synthetic"].any()
    assert not trees["national_claim"].any()
    assert summary["counts"]["trees"] == len(trees)
    for frame in (logs, residue):
        assert frame["measured_tree_list"].all()
        assert not frame["synthetic"].any()
        assert frame["model_estimate"].all()
        assert not frame["national_claim"].any()
    assert set(summary["excluded_provinces"]).isdisjoint(PILOT_PROVINCES)


@requires_artifacts
def test_sample_is_manageable_and_expansion_preserved(artifact) -> None:
    trees = artifact["trees"]
    audit = artifact["audit"]
    selection = artifact["selection"]

    counts = trees.groupby("jurisdiction").size()
    assert counts.to_dict() == {
        "BC": 347,
        "NB": 106,
        "QC": 85,
        "YT": 53,
    }
    assert len(trees) <= 600
    for province in PILOT_PROVINCES:
        row = audit[audit["province"] == province].iloc[0]
        selected = trees[trees["jurisdiction"] == province]
        assert len(selected) == int(row["selected_trees"])
        assert selected["expansion_factor"].sum() == pytest.approx(
            float(row["selected_expansion_stems_ha"]), rel=1e-12, abs=1e-6
        )
        assert float(row["selected_expansion_stems_ha"]) == pytest.approx(
            float(row["frame_expansion_stems_ha"]), rel=1e-12, abs=1e-6
        )
        assert float(row["expansion_conservation_error"]) <= 1e-6
        strata = selection[selection["province"] == province]
        assert strata["source_expansion_factor"].sum() == pytest.approx(
            float(row["frame_expansion_stems_ha"]), rel=1e-12, abs=1e-6
        )
        assert strata["selected_tree_id"].is_unique


@requires_artifacts
def test_volume_and_residue_balance(artifact) -> None:
    logs = artifact["logs"]
    residue = artifact["residue"]

    log_totals = logs.groupby("tree_id")["volume_m3"].sum()
    joined = residue.set_index("tree_id").join(
        log_totals.rename("log_volume_m3"), how="left"
    )
    # Small stems can produce zero recovered logs; their recovered volume is 0.
    joined["log_volume_m3"] = joined["log_volume_m3"].fillna(0.0)
    assert np.allclose(
        joined["log_volume_m3"],
        joined["recovered_log_m3"],
        rtol=1e-10,
        atol=1e-9,
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


@requires_artifacts
def test_taper_evidence_and_donor_labels(artifact) -> None:
    dispatch = artifact["dispatch"]
    coverage = artifact["coverage"]

    by_province = dispatch.groupby("jurisdiction")["taper_status"].agg(set)
    assert by_province["BC"] == {"local_published"}
    assert by_province["QC"] == {"local_published"}
    assert by_province["NB"] == {"donor_published"}
    assert by_province["YT"] == {"donor_published"}

    donor_source = {
        "NB": "QC",
        "YT": "BC",
    }
    for province, source in donor_source.items():
        subset = dispatch[dispatch["jurisdiction"] == province]
        assert set(subset["taper_jurisdiction"]) == {source}
    assert set(dispatch[dispatch["jurisdiction"] == "BC"]["taper_jurisdiction"]) == {
        "BC"
    }
    assert set(dispatch[dispatch["jurisdiction"] == "QC"]["taper_jurisdiction"]) == {
        "QC"
    }
    assert (dispatch["taper_evidence"] == "published_table").all()
    assert coverage["n_trees"].sum() == len(dispatch)
    assert coverage["measured_tree_list"].all()
    assert not coverage["synthetic"].any()


@requires_artifacts
def test_no_nan_or_negative_numeric_outputs(artifact) -> None:
    for name in ("trees", "logs", "residue", "tree_summary", "province"):
        frame = artifact[name]
        numeric = frame.select_dtypes(include=[np.number])
        assert not numeric.isna().any().any(), f"{name} contains NaN"
        assert not (numeric < 0.0).any().any(), f"{name} contains a negative value"


@requires_artifacts
def test_facility_coefficient_comparison(artifact) -> None:
    comparison = artifact["comparison"]
    assert len(comparison) == len(PILOT_PROVINCES)
    assert set(comparison["province"]) == set(PILOT_PROVINCES)
    assert (comparison["model_n_trees"] > 0).all()
    assert (comparison["model_recovered_m3_expanded"] > 0.0).all()
    assert (
        comparison[
            [
                "model_sawlog_share",
                "model_pulpwood_share",
                "model_other_share",
                "model_solid_share",
            ]
        ]
        >= 0.0
    ).all().all()
    sums = (
        comparison["model_sawlog_share"]
        + comparison["model_pulpwood_share"]
        + comparison["model_other_share"]
    )
    assert np.allclose(sums, 1.0, rtol=1e-9, atol=1e-9)
    assert set(comparison["comparison_status"]) <= {
        "within_20pct",
        "outside_20pct",
    }
    assert (comparison["model_expansion_share_of_eligible_measured_frame"] > 0.0).all()
    assert (comparison["model_expansion_share_of_live_measured_frame"] > 0.0).all()


@requires_artifacts
def test_manifest_and_summary_checks(artifact) -> None:
    summary = artifact["summary"]
    manifest = artifact["manifest"]

    assert summary["checks"] == {
        "all_measured": True,
        "all_measured_trees_have_allometry": True,
        "all_residue_balances_within_1e8_m3": True,
        "all_taper_evidence_published_table": True,
        "allometry_conservation": True,
        "allometry_recovery_scenarios": True,
        "exactly_four_provinces": True,
        "expansion_conservation": True,
        "no_national_claim": True,
        "no_negative_or_nan_outputs": True,
        "no_synthetic": True,
    }
    assert manifest["unexpected_git_deletions"] == "none"
    assert manifest["counts"]["trees"] == len(artifact["trees"])
    assert manifest["counts"]["logs"] == len(artifact["logs"])
    assert set(manifest["outputs"]) == {
        "tree_list",
        "sample_selection",
        "sample_audit",
        "bucked_logs",
        "residue_ledger",
        "tree_summary",
        "province_summary",
        "taper_dispatch",
        "taper_coverage",
        "coefficient_comparison",
        "allometry_coverage",
        "residue_recovery_scenarios",
        "sort_rules",
    }
