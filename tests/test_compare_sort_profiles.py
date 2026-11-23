"""Contract tests for the default-vs-tipsy45 sort comparison."""

from __future__ import annotations

from pathlib import Path

import pytest

from workflows.national.bucking.compare_sort_profiles import (
    ProfileComparisonError,
    build_comparison,
    build_report,
    load_run,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
BASE = REPO_ROOT / "data" / "derived" / "national" / "bucking"
DEFAULT_DIR = BASE / "tsa24_sort_validation_v1"
TIPSY45_DIR = BASE / "tsa24_sort_validation_tipsy45_v1"

requires_artifacts = pytest.mark.skipif(
    not (
        (DEFAULT_DIR / "metrics.json").exists()
        and (TIPSY45_DIR / "metrics.json").exists()
    ),
    reason="profile comparison artifacts are not available",
)


@requires_artifacts
def test_comparison_reports_both_profiles_and_no_tuning() -> None:
    comparison = build_comparison(
        load_run(DEFAULT_DIR, label="default"),
        load_run(TIPSY45_DIR, label="tipsy45"),
    )
    assert comparison["compared_profiles"] == ["default", "tipsy45"]
    assert comparison["status"] == "descriptive_not_tuned"
    assert comparison["grade_status"] == "not_assigned"
    assert comparison["official_grade_validation"] is False
    assert comparison["verdict"] in {
        "gap_closes_on_both_pooled_and_mean_plot_tvd",
        "gap_does_not_close_on_both_pooled_and_mean_plot_tvd",
    }
    metrics = comparison["metrics"]
    assert metrics["default"]["pooled_tvd"] is not None
    assert metrics["tipsy45"]["pooled_tvd"] is not None
    assert metrics["delta"]["pooled_tvd"] == pytest.approx(
        metrics["tipsy45"]["pooled_tvd"] - metrics["default"]["pooled_tvd"]
    )
    assert comparison["sort_shares"]
    assert comparison["by_au"]


@requires_artifacts
def test_comparison_report_states_no_grade_claim() -> None:
    comparison = build_comparison(
        load_run(DEFAULT_DIR, label="default"),
        load_run(TIPSY45_DIR, label="tipsy45"),
    )
    report = build_report(comparison)
    assert "not_assigned" in report
    assert "No profile value was tuned" in report
    assert "FAN$IER 4.5 Help does not publish numeric default log prices" in report


def test_missing_run_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(ProfileComparisonError, match="not found"):
        load_run(tmp_path, label="missing")


MARKET_DIR = BASE / "tsa24_sort_validation_market_surface_v1"


@requires_artifacts
def test_three_way_comparison_reports_market_surface() -> None:
    market_dir = MARKET_DIR if (MARKET_DIR / "metrics.json").exists() else TIPSY45_DIR
    market_label = "market_surface"
    comparison = build_comparison(
        load_run(DEFAULT_DIR, label="default"),
        load_run(TIPSY45_DIR, label="tipsy45"),
        load_run(market_dir, label=market_label),
    )
    assert comparison["compared_profiles"] == ["default", "tipsy45", "market_surface"]
    assert "market_surface" in comparison["metrics"]
    assert "delta_market_vs_default" in comparison["metrics"]
    assert "delta_market_vs_tipsy45" in comparison["metrics"]
    assert comparison["market_gap"] is not None
    assert comparison["verdict"] in {
        "market_surface_closes_gap_vs_default",
        "market_surface_closes_gap_vs_tipsy45_but_not_default",
        "market_surface_does_not_close_gap",
    }
    report = build_report(comparison)
    assert "market_surface" in report
    assert "vs default" in report
    assert "No profile value was tuned" in report
