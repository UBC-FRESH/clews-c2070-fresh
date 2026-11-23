"""Contract tests for the TSA24 industrial-sort validation.

The unit tests cover the alias/fail-closed rule, TVD/overlap arithmetic, the
pilot join, and the documented fallbacks.  The integration tests read the v0
artifacts and fail on count, NaN, negative, share-normalisation, or
evidence-label defects.  They are skipped when the data repository has not
produced the artifacts yet.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from workflows.national.bucking.tsa24_sort_validation import (
    GRADE_STATUS,
    INDUSTRIAL_ORDER,
    LABEL,
    SortValidationError,
    _assert_expected_codes,
    _marginal_distribution,
    build_plot_attributes,
    generate_validation,
    normalise_sort_code,
    overlap,
    tvd,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = REPO_ROOT / "data" / "derived" / "national" / "bucking" / "tsa24_sort_validation_v0"
REQUIRED_ARTIFACTS = (
    "plot_sort_comparison.csv",
    "au_sort_summary.csv",
    "sort_comparison.csv",
    "metrics.json",
    "summary.json",
    "VALIDATION_REPORT.md",
    "manifest.json",
)

requires_artifacts = pytest.mark.skipif(
    not all((OUT_DIR / name).exists() for name in REQUIRED_ARTIFACTS),
    reason="TSA24 industrial-sort validation artifacts have not been generated",
)


def _share_map(**values: float) -> dict[str, float]:
    return {code: float(values.get(code, 0.0)) for code in INDUSTRIAL_ORDER}


def _coverage() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "au_id": 23000,
                "stratum_code": "SBS_SX",
                "si_level": "H",
                "bec_zone": "SBS",
                "leading_species": "SX",
                "au_site_index": 17.9,
            },
            {
                "au_id": 22000,
                "stratum_code": "SBS_SX",
                "si_level": "M",
                "bec_zone": "SBS",
                "leading_species": "SX",
                "au_site_index": 13.8,
            },
            {
                "au_id": 23002,
                "stratum_code": "SBS_PLI",
                "si_level": "H",
                "bec_zone": "SBS",
                "leading_species": "PLI",
                "au_site_index": 19.0,
            },
        ]
    )


def _trees() -> pd.DataFrame:
    rows = []

    def add(plot_id: str, tree_id: str, species: str, age: float | None, si: float | None) -> None:
        rows.append(
            {
                "plot_id": plot_id,
                "tree_id": tree_id,
                "species": species,
                "bec_zone": "SBS",
                "stand_age": age,
                "site_index": si,
                "expansion_factor": 10.0,
            }
        )

    add("P1", "P1:1", "SW", 80.0, 18.0)
    add("P1", "P1:2", "SW", 80.0, 18.0)
    add("P2", "P2:1", "W", 90.0, 18.0)
    add("P2", "P2:2", "SW", 90.0, 18.0)
    add("P3", "P3:1", "EP", 60.0, 18.0)
    add("P4", "P4:1", "SW", None, 18.0)
    return pd.DataFrame(rows)


def _tipsy_table() -> pd.DataFrame:
    rows = []
    for au_id in (23000, 23002):
        for age, first, second in ((0.0, 0.0, 0.0), (50.0, 50.0, 50.0), (100.0, 25.0, 75.0)):
            row = {"au_id": au_id, "age": age}
            for code in INDUSTRIAL_ORDER:
                row[code] = 0.0
            row["D125L63"] = first
            row["D125L51"] = second
            row["industrial_logs_total_m3ha"] = first + second
            row["age_extrapolated"] = False
            rows.append(row)
    return pd.DataFrame(rows)


def test_alias_and_fail_closed() -> None:
    assert normalise_sort_code("D125L50") == "D125L5"
    assert normalise_sort_code(" d125l63 ") == "D125L63"
    assert normalise_sort_code("D125L5") == "D125L5"

    _assert_expected_codes(["D125L63", "D125L5"], context="test")
    with pytest.raises(SortValidationError, match="unexpected industrial sort"):
        _assert_expected_codes(["D999L9"], context="test")
    with pytest.raises(SortValidationError, match="unexpected industrial sort"):
        _assert_expected_codes(["D125L50"], context="test")


def test_marginal_distribution_keeps_deciduous_length_separate() -> None:
    shares = _share_map(D125L63=0.4, D125L51=0.3, D20L8=0.2, D305=0.1)

    diameter = _marginal_distribution(shares, "diameter")
    assert diameter == pytest.approx({"D125": 0.7, "D20": 0.2, "D305": 0.1})

    length = _marginal_distribution(shares, "length")
    assert length == pytest.approx({"L63": 0.4, "L51": 0.3, "L8": 0.2, "none": 0.1})

    with pytest.raises(SortValidationError, match="unknown marginal"):
        _marginal_distribution(shares, "product")


def test_tvd_and_overlap_arithmetic() -> None:
    identical = _share_map(D125L63=0.5, D125L51=0.5)
    assert tvd(identical, identical) == pytest.approx(0.0)
    assert overlap(identical, identical) == pytest.approx(1.0)

    changed = _share_map(D125L63=0.75, D125L51=0.25)
    assert tvd(identical, changed) == pytest.approx(0.25)
    assert overlap(identical, changed) == pytest.approx(0.75)
    assert tvd(identical, changed) + overlap(identical, changed) == pytest.approx(1.0)

    disjoint = _share_map(D125L8=1.0)
    assert tvd(identical, disjoint) == pytest.approx(1.0)
    assert overlap(identical, disjoint) == pytest.approx(0.0)

    with pytest.raises(SortValidationError, match="negative share"):
        tvd(_share_map(D125L63=-0.1, D125L51=1.1), identical)
    with pytest.raises(SortValidationError, match="non-finite share"):
        tvd(_share_map(D125L63=float("nan")), identical)
    with pytest.raises(SortValidationError, match="non-empty"):
        tvd(_share_map(), identical)


def test_plot_join_and_fallbacks() -> None:
    coverage = _coverage()
    trees = _trees()
    attrs = build_plot_attributes(
        trees, coverage, available_species={"SW", "PLI"}
    )
    by_plot = attrs.set_index("plot_id")

    assert by_plot.loc["P1", "au_id"] == 23000
    assert by_plot.loc["P1", "stratum_code"] == "SBS_SX"
    assert by_plot.loc["P1", "si_level"] == "H"
    assert by_plot.loc["P1", "match_tier"] == "au_curve"

    # The unsupported W stem does not become the leading-species token.
    assert by_plot.loc["P2", "leading_species"] == "SW"
    assert by_plot.loc["P2", "au_id"] == 23000

    assert by_plot.loc["P3", "match_tier"] == "no_match"
    assert by_plot.loc["P3", "au_id"] is None or pd.isna(by_plot.loc["P3", "au_id"])

    assert by_plot.loc["P4", "match_tier"] == "no_age"

    # Documented pilot-benchmark fallback fills AU and age only where the
    # pilot table explicitly carries the plot.
    pilot = pd.DataFrame(
        [
            {
                "plot_id": "P3",
                "au_id": 23002,
                "stratum_code": "SBS_PLI",
                "si_level": "H",
                "stand_age": 60.0,
                "leading_species": "PL",
                "match_tier": "au_curve",
            },
            {
                "plot_id": "P4",
                "au_id": 23000,
                "stratum_code": "SBS_SX",
                "si_level": "H",
                "stand_age": 95.0,
                "leading_species": "SW",
                "match_tier": "au_curve",
            },
        ]
    )
    attrs_fallback = build_plot_attributes(
        trees,
        coverage,
        pilot_benchmark=pilot,
        available_species={"SW", "PLI"},
    ).set_index("plot_id")
    assert attrs_fallback.loc["P3", "au_id"] == 23002
    assert attrs_fallback.loc["P3", "join_source"] == "pilot_grade_benchmark_fallback"
    assert attrs_fallback.loc["P4", "stand_age"] == pytest.approx(95.0)
    assert attrs_fallback.loc["P4", "age_source"] == "pilot_grade_benchmark"
    assert attrs_fallback.loc["P4", "match_tier"] == "au_curve"


def test_generate_validation_counts_arithmetic_and_labels(tmp_path: Path) -> None:
    logs = pd.DataFrame(
        [
            {
                "tree_id": "P1:1",
                "plot_id": "P1",
                "volume_m3": 1.0,
                "sort_code": "D125L63",
            },
            {
                "tree_id": "P1:2",
                "plot_id": "P1",
                "volume_m3": 1.0,
                "sort_code": "D125L51",
            },
            {
                "tree_id": "P1:3",
                "plot_id": "P1",
                "volume_m3": 0.5,
                "sort_code": "D125L50",
            },
            {
                "tree_id": "P2:1",
                "plot_id": "P2",
                "volume_m3": 1.0,
                "sort_code": "D125L63",
            },
            {
                "tree_id": "P2:2",
                "plot_id": "P2",
                "volume_m3": 1.0,
                "sort_code": "D125L51",
            },
        ]
    )
    trees = pd.DataFrame(
        [
            {
                "tree_id": "P1:1",
                "plot_id": "P1",
                "species": "SW",
                "bec_zone": "SBS",
                "stand_age": 80.0,
                "site_index": 18.0,
                "expansion_factor": 10.0,
            },
            {
                "tree_id": "P1:2",
                "plot_id": "P1",
                "species": "SW",
                "bec_zone": "SBS",
                "stand_age": 80.0,
                "site_index": 18.0,
                "expansion_factor": 10.0,
            },
            {
                "tree_id": "P1:3",
                "plot_id": "P1",
                "species": "SW",
                "bec_zone": "SBS",
                "stand_age": 80.0,
                "site_index": 18.0,
                "expansion_factor": 10.0,
            },
            {
                "tree_id": "P2:1",
                "plot_id": "P2",
                "species": "SW",
                "bec_zone": "SBS",
                "stand_age": 90.0,
                "site_index": 18.0,
                "expansion_factor": 10.0,
            },
            {
                "tree_id": "P2:2",
                "plot_id": "P2",
                "species": "SW",
                "bec_zone": "SBS",
                "stand_age": 90.0,
                "site_index": 18.0,
                "expansion_factor": 10.0,
            },
        ]
    )
    coverage = _coverage()
    tipsy = _tipsy_table()

    logs_path = tmp_path / "logs.csv"
    trees_path = tmp_path / "trees.csv"
    coverage_path = tmp_path / "coverage.csv"
    tipsy_path = tmp_path / "tipsy.csv"
    out_dir = tmp_path / "out"
    logs.to_csv(logs_path, index=False)
    trees.to_csv(trees_path, index=False)
    coverage.to_csv(coverage_path, index=False)
    tipsy.to_csv(tipsy_path, index=False)

    summary = generate_validation(
        logs_path=logs_path,
        trees_path=trees_path,
        tipsy_sorts_path=tipsy_path,
        coverage_path=coverage_path,
        out_dir=out_dir,
        pilot_benchmark_path=None,
        femic_root=None,
    )

    assert summary["label"] == LABEL
    assert summary["grade_status"] == GRADE_STATUS
    assert summary["official_grade_validation"] is False
    assert summary["pass_fail"] == "not_assessed_no_pre_registered_tolerance"

    sample = summary["sample"]
    assert sample["n_plots_total"] == 2
    assert sum(sample["n_plots_status"].values()) == sample["n_plots_total"]
    assert (
        sample["n_plots_comparable_au"] + sample["n_plots_comparable_species"]
        == sample["n_plots_comparable_total"]
    )
    assert sample["n_logs_comparable_total"] + sample["n_logs_excluded"] == sample["n_logs_total"]
    assert sample["n_trees_comparable_total"] + sample["n_trees_excluded"] == sample["n_trees_total"]
    assert sample["bucked_log_m3_comparable_total"] + sample["bucked_log_m3_excluded"] == pytest.approx(
        sample["bucked_log_m3_total"]
    )

    plot = pd.read_csv(out_dir / "plot_sort_comparison.csv")
    comparable = plot[plot["comparison_status"].isin(["comparable_au", "comparable_species"])]
    assert not comparable.empty
    for code in INDUSTRIAL_ORDER:
        assert comparable[f"pilot_sort_{code}"].between(0.0, 1.0).all()
        assert comparable[f"tipsy_sort_{code}"].between(0.0, 1.0).all()
    assert np.allclose(
        comparable[[f"pilot_sort_{code}" for code in INDUSTRIAL_ORDER]].sum(axis=1),
        1.0,
    )
    assert np.allclose(
        comparable[[f"tipsy_sort_{code}" for code in INDUSTRIAL_ORDER]].sum(axis=1),
        1.0,
    )
    assert comparable["sort_tvd"].between(0.0, 1.0).all()
    assert comparable["sort_overlap"].between(0.0, 1.0).all()
    assert np.allclose(comparable["sort_tvd"] + comparable["sort_overlap"], 1.0)

    metrics = json.loads((out_dir / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["label"] == LABEL
    assert metrics["grade_status"] == GRADE_STATUS
    assert metrics["official_grade_validation"] is False
    assert metrics["pass_fail"] == "not_assessed_no_pre_registered_tolerance"
    assert metrics["output_check"]["status"] == "pass"

    summary_json = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary_json["labels"] == [LABEL]
    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["grade_status"] == GRADE_STATUS
    assert manifest["tipsy_sorts_source"]["path"] == str(tipsy_path)


@requires_artifacts
def test_artifacts_have_no_nan_or_negative_key_values() -> None:
    plot = pd.read_csv(OUT_DIR / "plot_sort_comparison.csv")
    au = pd.read_csv(OUT_DIR / "au_sort_summary.csv")
    comparison = pd.read_csv(OUT_DIR / "sort_comparison.csv")

    assert set(plot["comparison_status"]).issubset(
        {
            "comparable_au",
            "comparable_species",
            "no_logs",
            "no_age",
            "no_au_match",
            "zero_tipsy_volume",
            "zero_pilot_volume",
        }
    )
    comparable = plot[plot["comparison_status"].isin(["comparable_au", "comparable_species"])]
    for code in INDUSTRIAL_ORDER:
        for prefix in ("pilot_sort", "tipsy_sort"):
            values = plot[f"{prefix}_{code}"]
            assert values.notna().all(), f"{prefix}_{code} contains NaN"
            assert (values >= 0).all(), f"{prefix}_{code} contains negative values"
            assert (values <= 1.0 + 1e-9).all(), f"{prefix}_{code} exceeds 1"
    for column in ("bucked_log_m3", "bucked_log_m3ha", "tipsy_industrial_total_m3ha"):
        values = plot[column]
        assert values.notna().all(), f"plot.{column} contains NaN"
        assert (values >= 0).all(), f"plot.{column} contains negative values"
    for column in ("sort_tvd", "sort_overlap"):
        values = comparable[column]
        assert values.notna().all(), f"comparable.{column} contains NaN"
        assert (values >= 0).all(), f"comparable.{column} contains negative values"
        assert (values <= 1.0 + 1e-9).all(), f"comparable.{column} exceeds 1"
    assert np.allclose(comparable["sort_tvd"] + comparable["sort_overlap"], 1.0)
    assert np.allclose(
        comparable[[f"pilot_sort_{code}" for code in INDUSTRIAL_ORDER]].sum(axis=1),
        1.0,
    )
    assert np.allclose(
        comparable[[f"tipsy_sort_{code}" for code in INDUSTRIAL_ORDER]].sum(axis=1),
        1.0,
    )

    for column in (
        "n_plots",
        "n_logs",
        "n_trees",
        "bucked_log_m3",
        "bucked_log_m3ha",
        "tipsy_industrial_total_m3ha",
        "mean_plot_tvd",
        "volume_weighted_tvd",
        "pooled_tvd",
        "mean_plot_overlap",
        "pooled_overlap",
        "rmse_m3ha",
    ):
        values = au[column]
        assert values.notna().all(), f"au.{column} contains NaN"
        assert (values >= 0).all(), f"au.{column} contains negative values"
    assert au["mean_plot_tvd"].between(0.0, 1.0).all()
    assert au["pooled_tvd"].between(0.0, 1.0).all()
    assert au["mean_plot_overlap"].between(0.0, 1.0).all()
    assert au["pooled_overlap"].between(0.0, 1.0).all()

    for column in ("pilot_share", "tipsy_share", "sort_tvd", "sort_overlap"):
        values = comparison[column]
        assert values.notna().all(), f"sort_comparison.{column} contains NaN"
        assert (values >= 0).all(), f"sort_comparison.{column} contains negative values"
        assert (values <= 1.0 + 1e-9).all(), f"sort_comparison.{column} exceeds 1"

    metrics = json.loads((OUT_DIR / "metrics.json").read_text(encoding="utf-8"))
    summary = json.loads((OUT_DIR / "summary.json").read_text(encoding="utf-8"))
    manifest = json.loads((OUT_DIR / "manifest.json").read_text(encoding="utf-8"))
    assert metrics["output_check"]["status"] == "pass"
    assert metrics["label"] == LABEL
    assert metrics["grade_status"] == GRADE_STATUS
    assert metrics["official_grade_validation"] is False
    assert metrics["pass_fail"] == "not_assessed_no_pre_registered_tolerance"
    assert summary["labels"] == [LABEL]
    assert summary["grade_status"] == GRADE_STATUS
    assert manifest["label"] == LABEL
    assert manifest["grade_status"] == GRADE_STATUS
    assert manifest["output_check"]["status"] == "pass"

    sample = metrics["sample"]
    assert sum(sample["n_plots_status"].values()) == sample["n_plots_total"]
    assert (
        sample["n_plots_comparable_au"] + sample["n_plots_comparable_species"]
        == sample["n_plots_comparable_total"]
    )
    assert sample["n_logs_comparable_total"] + sample["n_logs_excluded"] == sample["n_logs_total"]
    assert sample["n_trees_comparable_total"] + sample["n_trees_excluded"] == sample["n_trees_total"]
    assert sample["bucked_log_m3_comparable_total"] + sample["bucked_log_m3_excluded"] == pytest.approx(
        sample["bucked_log_m3_total"]
    )

    # The validation is an industrial-sort recovery check, never an official
    # grade or a fitted threshold.
    report = (OUT_DIR / "VALIDATION_REPORT.md").read_text(encoding="utf-8").lower()
    assert "not an official bc log-grade validation" in report
    assert "not_assessed_no_pre_registered_tolerance" in report


@requires_artifacts
def test_manifest_hashes_exist() -> None:
    import hashlib

    manifest = json.loads((OUT_DIR / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["inputs"]
    assert manifest["outputs"]
    for record in manifest["inputs"]:
        path = Path(record["path"])
        if not path.is_absolute():
            path = REPO_ROOT / path
        assert path.exists(), record
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert digest == record["sha256"], record
    for record in manifest["outputs"]:
        path = OUT_DIR / record["path"]
        assert path.exists(), record
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert digest == record["sha256"], record
