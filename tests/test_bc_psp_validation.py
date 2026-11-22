"""Validation tests for the full TSA24 BC PSP bucking product.

The first test covers pure comparison arithmetic.  The remaining tests read
the generated v0 artifacts and fail on count, balance, evidence, NaN, or
negative-value defects.  They are skipped when the data repository has not
produced the artifacts yet.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from workflows.national.bucking.bc_psp_validation import (
    UTILIZATIONS,
    VALIDATION_NAME,
    comparison_metrics,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = REPO_ROOT / "data" / "derived" / "national" / "bucking" / "bc_psp_validation_v0"
REQUIRED_ARTIFACTS = (
    "selected_trees.csv",
    "tree_ledger.csv",
    "bucked_logs.csv",
    "residue.csv",
    "skipped_trees.csv",
    "plot_comparison.csv",
    "plot_species_comparison.csv",
    "species_comparison.csv",
    "utilization_comparison.csv",
    "residue_summary.csv",
    "residue_components.csv",
    "validation_summary.json",
    "VALIDATION_REPORT.md",
    "manifest.json",
)

requires_artifacts = pytest.mark.skipif(
    not all((OUT_DIR / name).exists() for name in REQUIRED_ARTIFACTS),
    reason="TSA24 validation artifacts have not been generated",
)


def _read(name: str) -> pd.DataFrame:
    return pd.read_csv(OUT_DIR / name)


def _summary() -> dict:
    return json.loads((OUT_DIR / "validation_summary.json").read_text(encoding="utf-8"))


def test_comparison_metrics_is_pure_and_arithmetic() -> None:
    observed = pd.Series([10.0, 20.0, 30.0])
    predicted = pd.Series([9.0, 22.0, 30.0])
    result = comparison_metrics(observed, predicted)

    assert result["n"] == 3
    assert result["observed_sum"] == pytest.approx(60.0)
    assert result["predicted_sum"] == pytest.approx(61.0)
    assert result["bias_sum"] == pytest.approx(1.0)
    assert result["mean_bias"] == pytest.approx(1.0 / 3.0)
    assert result["mae"] == pytest.approx(1.0)
    assert result["rmse"] == pytest.approx(math.sqrt(5.0 / 3.0))
    assert result["relative_bias"] == pytest.approx(1.0 / 60.0)
    assert result["mean_ratio"] == pytest.approx((0.9 + 1.1 + 1.0) / 3.0)
    assert result["median_ratio"] == pytest.approx(1.0)
    assert result["ratio_n"] == 3


def test_comparison_metrics_handles_zero_observed() -> None:
    result = comparison_metrics(pd.Series([0.0, 0.0]), pd.Series([1.0, 2.0]))

    assert result["n"] == 2
    assert result["bias_sum"] == pytest.approx(3.0)
    assert result["relative_bias"] is None
    assert result["mean_ratio"] is None
    assert result["ratio_n"] == 0


@requires_artifacts
def test_population_counts_and_latest_visit_selection() -> None:
    selected = _read("selected_trees.csv")
    ledger = _read("tree_ledger.csv")
    summary = _summary()
    stats = summary["population"]

    assert stats["selected_sites"] == 558
    assert stats["selected_plots"] == 558
    assert stats["selected_visits"] == 558
    assert stats["selected_live_trees"] == len(selected) == len(ledger) == 60960
    assert stats["latest_live_visit_fallback_sites"] == 0
    assert selected["tree_id"].is_unique
    assert ledger["tree_id"].is_unique
    assert selected.groupby("site_id")["visit_number"].nunique().max() == 1
    assert selected.groupby("site_id")["plot_id"].nunique().max() == 1
    assert summary["validation"] == VALIDATION_NAME


@requires_artifacts
def test_bucked_and_skipped_partition_the_population() -> None:
    selected = _read("selected_trees.csv")
    skipped = _read("skipped_trees.csv")
    logs = _read("bucked_logs.csv")
    residue = _read("residue.csv")
    ledger = _read("tree_ledger.csv")

    bucked = selected[selected["bucked_status"] == "bucked"]
    assert len(bucked) == len(residue)
    assert set(bucked["tree_id"]) == set(residue["tree_id"])
    assert set(selected["tree_id"]) == set(ledger["tree_id"])
    assert set(skipped["tree_id"]).issubset(set(selected["tree_id"]))
    assert len(bucked) + len(skipped) == len(selected)
    assert len(logs) > 0
    assert set(logs["tree_id"]).issubset(set(bucked["tree_id"]))


@requires_artifacts
def test_tree_volume_balance_and_log_aggregation() -> None:
    ledger = _read("tree_ledger.csv")
    residue = _read("residue.csv")
    logs = _read("bucked_logs.csv")
    bucked = ledger[ledger["bucked_status"] == "bucked"]

    balance = (
        bucked["recovered_log_m3"]
        + bucked["stem_residue_m3"]
        - bucked["taper_total_m3"]
    ).abs()
    assert balance.max() <= 1e-8

    log_totals = logs.groupby("tree_id")["volume_m3"].sum()
    joined = bucked.set_index("tree_id").join(log_totals.rename("log_total"))
    with_logs = joined["log_total"].notna()
    assert np.allclose(
        joined.loc[with_logs, "recovered_log_m3"],
        joined.loc[with_logs, "log_total"],
        rtol=1e-10,
        atol=1e-10,
    )
    assert (joined.loc[~with_logs, "recovered_log_m3"] == 0).all()
    assert (bucked["n_logs"] >= 0).all()

    residue_check = residue.set_index("tree_id")
    assert np.allclose(
        residue_check["residue_odt"],
        residue_check["residue_volume_m3"] * residue_check["basic_density_odt_m3"],
        rtol=1e-10,
        atol=1e-10,
    )
    assert (residue_check["residue_volume_m3"] >= 0).all()


@requires_artifacts
def test_plot_comparison_arithmetic_and_utilization_subsets() -> None:
    ledger = _read("tree_ledger.csv")
    plot_comparison = _read("plot_comparison.csv")

    for utilization in UTILIZATIONS:
        subset = ledger[ledger["dbh_cm"] >= utilization]
        expected = (
            subset.groupby("plot_id")
            .agg(
                taper=("taper_total_m3_ha", "sum"),
                logs=("recovered_log_m3_ha", "sum"),
                residue=("stem_residue_m3_ha", "sum"),
            )
            .reset_index()
        )
        table = plot_comparison[
            plot_comparison["utilization"] == utilization
        ].merge(expected, on="plot_id", how="outer")
        for column in ("taper", "logs", "residue"):
            table[column] = table[column].fillna(0.0)
        assert len(table) == 558
        assert np.allclose(
            table["predicted_taper_m3ha"], table["taper"], rtol=1e-10, atol=1e-10
        )
        assert np.allclose(
            table["predicted_log_m3ha"], table["logs"], rtol=1e-10, atol=1e-10
        )
        assert np.allclose(
            table["predicted_residue_m3ha"], table["residue"], rtol=1e-10, atol=1e-10
        )
        assert np.allclose(
            table["taper_bias_m3ha"],
            table["predicted_taper_m3ha"] - table["observed_matched_wsv_m3ha"],
            rtol=1e-10,
            atol=1e-10,
        )
        positive = table["observed_matched_wsv_m3ha"] > 0
        assert np.allclose(
            table.loc[positive, "taper_ratio"],
            table.loc[positive, "predicted_taper_m3ha"]
            / table.loc[positive, "observed_matched_wsv_m3ha"],
            rtol=1e-10,
            atol=1e-10,
        )


@requires_artifacts
def test_species_comparison_arithmetic_and_totals() -> None:
    species = _read("species_comparison.csv")
    plot = _read("plot_comparison.csv")

    assert set(species["utilization"].unique()) == set(UTILIZATIONS)
    assert (species["bias_m3ha"] - (
        species["predicted_taper_m3ha"] - species["observed_wsv_m3ha"]
    )).abs().max() <= 1e-8
    for utilization in UTILIZATIONS:
        species_total = species.loc[
            species["utilization"] == utilization, "predicted_taper_m3ha"
        ].sum()
        plot_total = plot.loc[
            plot["utilization"] == utilization, "predicted_taper_m3ha"
        ].sum()
        assert species_total == pytest.approx(plot_total, rel=1e-12, abs=1e-9)
    assert set(species["bucking_status"].unique()).issubset(
        {"bucked", "not_bucked_unsupported_species"}
    )


@requires_artifacts
def test_evidence_tiers_and_dimension_only_status() -> None:
    selected = _read("selected_trees.csv")
    logs = _read("bucked_logs.csv")
    residue = _read("residue.csv")

    assert set(selected["taper_evidence"]) == {"published_table"}
    assert set(logs["evidence"]) == {"published_table"}
    assert set(logs["sort_basis"]) == {"dimension_only"}
    assert set(logs["grade_status"]) == {"not_assigned"}
    assert set(residue["evidence"]) == {"assumption"}
    assert set(residue["volume_evidence"]) == {"published_table"}
    assert set(residue["odt_evidence"]).issubset(
        {"species_table", "generic_assumption"}
    )
    assert set(selected["height_evidence"]).issubset(
        {"field_measured", "dbh_estimated", "unknown"}
    )
    assert set(logs["product"]).issubset({"sawlog", "pulpwood", "other"})


@requires_artifacts
def test_no_nan_or_negative_key_columns() -> None:
    selected = _read("selected_trees.csv")
    ledger = _read("tree_ledger.csv")
    logs = _read("bucked_logs.csv")
    residue = _read("residue.csv")
    plot = _read("plot_comparison.csv")
    species = _read("species_comparison.csv")

    checks = {
        "selected": (
            selected,
            ["dbh_cm", "height_m", "expansion_factor"],
        ),
        "ledger": (
            ledger,
            [
                "taper_total_m3",
                "recovered_log_m3",
                "stem_residue_m3",
                "residue_odt",
                "green_tonnes",
                "chip_green_tonnes",
            ],
        ),
        "logs": (
            logs,
            [
                "length_m",
                "large_end_dib_cm",
                "small_end_dib_cm",
                "volume_m3",
            ],
        ),
        "residue": (
            residue,
            [
                "residue_volume_m3",
                "basic_density_odt_m3",
                "residue_odt",
                "green_tonnes",
                "chip_green_tonnes",
            ],
        ),
        "plot": (
            plot,
            [
                "predicted_taper_m3ha",
                "predicted_log_m3ha",
                "predicted_residue_m3ha",
                "n_trees",
                "n_logs",
            ],
        ),
        "species": (
            species,
            [
                "observed_wsv_m3ha",
                "predicted_taper_m3ha",
                "predicted_log_m3ha",
                "predicted_residue_m3ha",
                "n_trees",
                "n_logs",
            ],
        ),
    }
    for label, (frame, columns) in checks.items():
        for column in columns:
            values = pd.to_numeric(frame[column], errors="coerce")
            assert values.notna().all(), f"{label}.{column} contains NaN"
            assert (values >= 0).all(), f"{label}.{column} contains negative values"


@requires_artifacts
def test_validation_checks_and_residue_components() -> None:
    summary = _summary()
    components = _read("residue_components.csv")
    residue_summary = _read("residue_summary.csv")

    assert summary["conservation"]["pass"] is True
    assert summary["conservation"]["max_abs_ledger_balance_m3"] <= 1e-8
    assert summary["compiled_arithmetic"]["pass"] is True
    assert summary["tree_expansion_arithmetic"]["pass"] is True
    assert summary["validation_checks"]["no_nan_or_negative_key_columns"] is True
    assert summary["status"] == "dimension_validation_complete_grade_not_assigned"

    branches = components[components["component"] == "branches_foliage"].iloc[0]
    assert bool(branches["estimated"]) is False
    stem = components[
        components["component"] == "stem_residue_recovered_log_gap"
    ].iloc[0]
    assert bool(stem["estimated"]) is True
    assert float(stem["volume_m3"]) > 0
    assert summary["residue"]["branch_foliage_included"] is False

    all_rows = residue_summary[residue_summary["species"] == "ALL"]
    assert set(all_rows["utilization_cm"]) == set(UTILIZATIONS)
    assert (all_rows["stem_residue_m3"] >= 0).all()


@requires_artifacts
def test_manifest_and_report_are_auditable() -> None:
    manifest = json.loads((OUT_DIR / "manifest.json").read_text(encoding="utf-8"))
    report = (OUT_DIR / "VALIDATION_REPORT.md").read_text(encoding="utf-8")

    output_names = {entry["path"] for entry in manifest["outputs"]}
    assert set(REQUIRED_ARTIFACTS).difference({"manifest.json"}).issubset(output_names)
    assert len(output_names) == len(manifest["outputs"])
    assert manifest["no_deletion_check"]["pass"] is True
    assert len(manifest["git"]["parent"]["head"]) == 40
    assert len(manifest["git"]["data"]["head"]) == 40
    assert all(entry.get("status") != "missing" for entry in manifest["inputs"])
    assert manifest["validation"] == VALIDATION_NAME
    assert "not official BC grade" in report
    assert "dimension-only" in report
    assert "RSYC" in report
    assert "grade_status = not_assigned" in report
