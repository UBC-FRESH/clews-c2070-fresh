"""Tests for national economics v2.2 direct P1 cost inputs."""

from __future__ import annotations

import csv
import json
import subprocess
from pathlib import Path

import pytest

from workflows.national.harvest import national_economics_v2_2 as model

REPO_ROOT = Path(__file__).resolve().parents[1]
V21_MARGIN_CSV = (
    REPO_ROOT
    / "data/derived/national/harvest_basis/national_economics_v2_1/"
    "national_margin_estimates.csv"
)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict]:
    out_dir = tmp_path_factory.mktemp("national_economics_v2_2")
    summary = model.build(out_dir, check_deletions=False)
    return out_dir, summary


def _cost_row(
    rows: list[dict[str, str]], province: str, component: str
) -> dict[str, str]:
    matches = [
        row
        for row in rows
        if row["province"] == province and row["component"] == component
    ]
    assert len(matches) == 1
    return matches[0]


def test_coverage_and_labels(built):
    out_dir, summary = built
    assert summary["coverage"]["province_count"] == 13
    assert summary["coverage"]["missing"] == []
    assert summary["coverage"]["not_estimated_count"] == 0
    assert summary["coverage"]["margin_rows"] == 78
    assert summary["coverage"]["summary_rows"] == 28
    assert summary["coverage"]["comparison_rows"] == 28
    assert summary["coverage"]["cost_input_rows"] == 40
    for filename in (
        model.COST_INPUTS_CSV,
        model.MARGIN_CSV,
        model.SUMMARY_CSV,
        model.COMPARISON_CSV,
        model.TIER_CSV,
        model.ASSUMPTIONS_CSV,
        model.SOURCES_CSV,
    ):
        rows = _read_csv(out_dir / filename)
        assert rows, filename
        for row in rows:
            assert row["evidence"] == "model_estimate"
            assert row["observed"] == "false"
            assert row["policy_grade"] == "false"
            assert row["combined_national_net_value"] == "false"
            assert "not_estimated" not in " ".join(row.values()).lower()
            assert "observed=true" not in " ".join(row.values()).lower()


def test_qc_direct_costs_are_t20_survey_aggregates(built):
    out_dir, _ = built
    rows = _read_csv(out_dir / model.COST_INPUTS_CSV)
    expected = {
        "harvest": ("28.2700", "31.4200", "34.4800"),
        "haul": ("19.3200", "24.7600", "36.8600"),
        "road": ("7.4700", "16.5800", "22.1300"),
    }
    for component, values in expected.items():
        row = _cost_row(rows, "QC", component)
        assert (
            row["low_cad_m3"],
            row["base_cad_m3"],
            row["high_cad_m3"],
        ) == values
        assert row["tier"] == "reported_survey_aggregate"
        assert row["source_year"] == "2022"
        assert row["source_key"] == "qc_cost_survey_2022"
        assert "not observed" in row["notes"].lower()
    qc_road = _cost_row(rows, "QC", "road")
    assert float(qc_road["low_cad_m3"]) != pytest.approx(15.52)
    assert "before transfers" in qc_road["notes"].lower()
    assert "prccm" in qc_road["notes"].lower()


def test_bc_road_uses_published_appraisal_allowance(built):
    out_dir, summary = built
    rows = _read_csv(out_dir / model.COST_INPUTS_CSV)
    row = _cost_row(rows, "BC", "road")
    assert row["tier"] == "appraisal_allowance"
    assert row["source_key"] == "bc_appraisal_costs_2026"
    assert row["source_year"] == "2026"
    assert float(row["low_cad_m3"]) == pytest.approx(1.0)
    assert float(row["base_cad_m3"]) == pytest.approx(5.93)
    assert float(row["high_cad_m3"]) == pytest.approx(9.78)
    assert "maintenance" in row["notes"].lower()
    assert "not converted" in row["notes"].lower()
    assert summary["bc_road"]["partial"] is True
    assert summary["bc_road"]["construction_converted"] is False
    assert summary["bc_road"]["cam_road_management_crosscheck_cad_m3"] == pytest.approx(
        4.73
    )


def test_ab_on_keep_donor_and_have_no_invented_series(built):
    out_dir, summary = built
    rows = _read_csv(out_dir / model.COST_INPUTS_CSV)
    for province in ("AB", "ON"):
        for component in ("logging_haul", "road", "silviculture"):
            row = _cost_row(rows, province, component)
            assert row["tier"] == "donor_proxy"
            assert row["direct_series_status"] == "not_available_direct_cost_series"
            assert row["replacement_status"] == "no_direct_series_available"
            assert "no value invented" in row["notes"].lower()
    assert summary["ab_on"]["direct_series_status"] == (
        "not_available_direct_cost_series"
    )
    assert summary["ab_on"]["donor_retained"] is True
    assert summary["ab_on"]["values_invented"] is False


def test_margins_are_recomputed_ordered_and_unclipped(built):
    out_dir, summary = built
    rows = _read_csv(out_dir / model.MARGIN_CSV)
    assert len(rows) == 78
    by_key = {
        (row["province"], row["price_scenario"], row["scenario_id"]): row
        for row in rows
    }
    for scenario in model.SCENARIOS:
        seen = {
            row["province"]
            for row in rows
            if row["price_scenario"] == scenario
        }
        assert seen == set(model.PROVINCES)
        for province in model.PROVINCES:
            low = by_key[(province, scenario, "low")]
            base = by_key[(province, scenario, "base")]
            high = by_key[(province, scenario, "high")]
            assert float(low["margin_cad_m3"]) <= float(
                base["margin_cad_m3"]
            ) <= float(high["margin_cad_m3"])
            for row in (low, base, high):
                assert row["negative_margin_clipped"] == "false"
                assert float(row["margin_cad_m3"]) == pytest.approx(
                    float(row["price_cad_m3"])
                    - float(row["logging_haul_cad_m3"])
                    - float(row["road_cad_m3"])
                    - float(row["silviculture_cad_m3"]),
                    abs=0.002,
                )
                assert float(row["total_cost_cad_m3"]) == pytest.approx(
                    float(row["logging_haul_cad_m3"])
                    + float(row["road_cad_m3"])
                    + float(row["silviculture_cad_m3"]),
                    abs=0.002,
                )
    assert float(by_key[("QC", "donor_flat", "base")]["logging_haul_cad_m3"]) == (
        pytest.approx(31.42 + 24.76, abs=1e-6)
    )
    assert float(by_key[("QC", "donor_flat", "base")]["road_cad_m3"]) == (
        pytest.approx(16.58, abs=1e-6)
    )
    assert float(by_key[("BC", "donor_flat", "base")]["road_cad_m3"]) == (
        pytest.approx(5.93, abs=1e-6)
    )
    assert summary["margins"]["negative_row_count"] == 34
    assert summary["margins"]["negative_margin_clipped"] is False


def test_national_diagnostics_reflect_cost_upgrade(built):
    _, summary = built
    diagnostic = summary["national_diagnostic"]
    assert diagnostic["label"] == "model_estimate_not_policy"
    assert diagnostic["combined_national_net_value"] is False
    assert diagnostic["donor_flat"]["margin_low_cad_m3"] == pytest.approx(
        -76.2945, abs=1e-3
    )
    assert diagnostic["donor_flat"]["margin_base_cad_m3"] == pytest.approx(
        36.7405, abs=1e-3
    )
    assert diagnostic["donor_flat"]["margin_high_cad_m3"] == pytest.approx(
        83.8033, abs=1e-3
    )
    assert diagnostic["stumpage_scaled_sensitivity"][
        "margin_base_cad_m3"
    ] == pytest.approx(-12.5180, abs=1e-3)
    assert float(diagnostic["allocation_volume_m3"]) == pytest.approx(
        118_977_339.1, abs=1.0
    )


def test_comparison_reports_before_after_and_ab_on_unchanged(built):
    out_dir, _ = built
    rows = _read_csv(out_dir / model.COMPARISON_CSV)
    assert len(rows) == 28
    by_key = {
        (row["province"], row["price_scenario"]): row for row in rows
    }
    before = {
        (row["province"], row["price_scenario"], row["scenario_id"]): row
        for row in _read_csv(V21_MARGIN_CSV)
    }
    for scenario in model.SCENARIOS:
        for province in model.PROVINCES:
            row = by_key[(province, scenario)]
            assert float(row["v2_1_margin_base_cad_m3"]) == pytest.approx(
                float(before[(province, scenario, "base")]["margin_cad_m3"]),
                abs=1e-4,
            )
            if province in ("AB", "ON"):
                assert float(row["margin_base_delta_cad_m3"]) == pytest.approx(
                    0.0, abs=1e-6
                )
        assert float(by_key[("BC", scenario)]["margin_base_delta_cad_m3"]) == (
            pytest.approx(10.6500, abs=1e-3)
        )
        assert float(by_key[("QC", scenario)]["margin_base_delta_cad_m3"]) == (
            pytest.approx(0.1133, abs=1e-3)
        )
        assert float(
            by_key[("NATIONAL", scenario)]["margin_base_delta_cad_m3"]
        ) == pytest.approx(3.6040, abs=1e-3)


def test_tier_coverage_shows_p1_upgrade_without_full_coverage_claim(built):
    out_dir, summary = built
    rows = _read_csv(out_dir / model.TIER_CSV)
    upgrades = [row for row in rows if row["row_type"] == "p1_upgrade"]
    assert {(row["province"], row["component"]) for row in upgrades} == {
        ("BC", "road"),
        ("QC", "logging"),
        ("QC", "haul"),
    }
    assert all(row["p1_priority"] == "P1" for row in upgrades)
    before = next(
        row
        for row in rows
        if row["row_type"] == "coverage_summary"
        and row["price_scenario"] == "donor_flat"
        and row["version"] == "v2_1"
        and row["scope"] == "all_components"
    )
    after = next(
        row
        for row in rows
        if row["row_type"] == "coverage_summary"
        and row["price_scenario"] == "donor_flat"
        and row["version"] == "v2_2"
        and row["scope"] == "all_components"
    )
    assert before["direct_cell_count_before"] == "3"
    assert after["direct_cell_count_after"] == "6"
    assert float(before["volume_weighted_direct_share"]) == pytest.approx(
        0.1792, abs=5e-4
    )
    assert float(after["volume_weighted_direct_share"]) == pytest.approx(
        0.3360, abs=5e-4
    )
    assert summary["direct_cost_coverage"]["full_direct_coverage_claimed"] is False
    assert summary["direct_cost_coverage"]["after"]["all_components"][
        "direct_cells"
    ] == 6


def test_manifest_hashes_outputs_and_labels(built):
    out_dir, _ = built
    manifest = json.loads((out_dir / model.MANIFEST_JSON).read_text(encoding="utf-8"))
    assert manifest["model_id"] == "national_economics_v2_2"
    assert manifest["artifact_version"] == "2.2.0"
    assert manifest["base_scenario"] == "donor_flat"
    assert manifest["sensitivity_scenario"] == "stumpage_scaled"
    assert manifest["stumpage_scaled_observed"] is False
    assert manifest["combined_national_net_value"] is False
    assert manifest["restricted_inputs_used"] == []
    assert manifest["labels"]["observed"] is False
    assert manifest["labels"]["policy_grade"] is False
    assert len(manifest["outputs"]) == 9
    for item in manifest["outputs"]:
        path = out_dir / item["path"]
        assert path.is_file()
        assert len(item["sha256"]) == 64
    for item in manifest["inputs"]:
        assert not any(
            marker in item["path"].lower()
            for marker in ("confidential", "restricted", "microdata")
        )


def test_report_states_upgrade_and_non_claims(built):
    out_dir, _ = built
    report = (out_dir / model.REPORT_MD).read_text(encoding="utf-8")
    assert "QC cost upgrade" in report
    assert "31.42" in report and "24.76" in report and "16.58" in report
    assert "BC road result" in report
    assert "maintenance-only" in report
    assert "Alberta and Ontario keep the donor cost values" in report
    assert "Direct-cost coverage before and after" in report
    assert "not observed" in report.lower()
    assert "No combined national net value" in report
    assert "full direct coverage" in report.lower()


def test_deletion_preflight_detects_worktree_deletion(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo,
        check=True,
    )
    tracked = repo / "tracked.txt"
    tracked.write_text("value\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "add"], cwd=repo, check=True)
    tracked.unlink()
    deletions = model._deletion_lines(repo)
    assert any(line.strip().startswith("D") or " D " in line for line in deletions)
