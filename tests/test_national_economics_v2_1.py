"""Tests for national economics v2.1 base/sensitivity correction."""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path

import pytest

from workflows.national.harvest import national_economics_v2_1 as model

REPO_ROOT = Path(__file__).resolve().parents[1]
V1_MARGIN_CSV = REPO_ROOT / "data/derived/national/harvest_basis/national_economics_v1/national_margin_estimates.csv"


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict]:
    out_dir = tmp_path_factory.mktemp("national_economics_v2_1")
    summary = model.build(out_dir)
    return out_dir, summary


def test_coverage_and_fixed_scenario_decision(built):
    _, summary = built
    assert summary["coverage"]["province_count"] == 13
    assert summary["coverage"]["missing"] == []
    assert summary["coverage"]["not_estimated_count"] == 0
    assert summary["coverage"]["margin_rows"] == 78
    assert summary["coverage"]["summary_rows"] == 28
    assert summary["coverage"]["price_rows"] == 26
    assert summary["coverage"]["scenarios"] == [
        "donor_flat",
        "stumpage_scaled",
    ]
    assert summary["decision"]["base_scenario"] == "donor_flat"
    assert summary["decision"]["sensitivity_scenario"] == "stumpage_scaled"
    assert summary["decision"]["stumpage_scaled_preferred"] is False
    assert summary["decision"]["stumpage_scaled_observed"] is False
    assert summary["decision"]["stumpage_scaled_policy_grade"] is False


def test_all_rows_declare_base_scenario_and_labels(built):
    out_dir, _ = built
    for filename in (
        model.MARGIN_CSV,
        model.SUMMARY_CSV,
        model.PRICE_CSV,
        model.STUMPAGE_CSV,
        model.COMPARISON_CSV,
        model.TIER_CSV,
        model.ASSUMPTIONS_CSV,
        model.SOURCES_CSV,
    ):
        rows = _read_csv(out_dir / filename)
        assert rows, filename
        for row in rows:
            assert row["base_scenario"] == "donor_flat"
            assert row["evidence"] == "model_estimate"
            assert row["observed"] == "false"
            assert row["policy_grade"] == "false"
            assert row["combined_national_net_value"] == "false"
            assert "not_estimated" not in " ".join(row.values()).lower()
            assert "observed=true" not in " ".join(row.values()).lower()


def test_low_base_high_ordered_for_every_province_and_national(built):
    out_dir, _ = built
    margins = _read_csv(out_dir / model.MARGIN_CSV)
    by_key = {
        (row["province"], row["price_scenario"], row["scenario_id"]): row
        for row in margins
    }
    for scenario in model.SCENARIOS:
        seen = {
            row["province"]
            for row in margins
            if row["price_scenario"] == scenario
        }
        assert seen == set(model.PROVINCES)
        for province in model.PROVINCES:
            low = float(by_key[(province, scenario, "low")]["margin_cad_m3"])
            base = float(by_key[(province, scenario, "base")]["margin_cad_m3"])
            high = float(by_key[(province, scenario, "high")]["margin_cad_m3"])
            assert math.isfinite(low)
            assert low <= base <= high
            price_low = float(
                by_key[(province, scenario, "low")]["price_low_cad_m3"]
            )
            price_base = float(
                by_key[(province, scenario, "base")]["price_base_cad_m3"]
            )
            price_high = float(
                by_key[(province, scenario, "high")]["price_high_cad_m3"]
            )
            assert 0.0 < price_low <= price_base <= price_high
    summary = _read_csv(out_dir / model.SUMMARY_CSV)
    assert len(summary) == 28
    national = [
        row for row in summary if row["row_type"] == "national"
    ]
    assert len(national) == 2
    for row in national:
        assert (
            float(row["margin_low_cad_m3"])
            <= float(row["margin_base_cad_m3"])
            <= float(row["margin_high_cad_m3"])
        )
        assert row["national_diagnostic_label"] == "model_estimate_not_policy"


def test_donor_flat_reproduces_v1_flat_donor(built):
    out_dir, _ = built
    rows = _read_csv(out_dir / model.MARGIN_CSV)
    donor = {
        (row["province"], row["scenario_id"]): row
        for row in rows
        if row["price_scenario"] == "donor_flat"
    }
    v1_rows = {
        (row["province"], row["scenario_id"]): row
        for row in _read_csv(V1_MARGIN_CSV)
    }
    for province in model.PROVINCES:
        for case in ("low", "base", "high"):
            assert float(donor[(province, case)]["margin_cad_m3"]) == pytest.approx(
                float(v1_rows[(province, case)]["margin_cad_m3"]), abs=1e-3
            )
    comparison = _read_csv(out_dir / model.COMPARISON_CSV)
    for row in comparison:
        assert float(row["donor_flat_minus_v1_flat_cad_m3"]) == pytest.approx(
            0.0, abs=1e-3
        )


def test_national_diagnostics_are_expected_and_labelled(built):
    _, summary = built
    diagnostic = summary["national_diagnostic"]
    assert diagnostic["base_scenario"] == "donor_flat"
    assert diagnostic["label"] == "model_estimate_not_policy"
    assert diagnostic["combined_national_net_value"] is False
    assert diagnostic["donor_flat"]["margin_low_cad_m3"] == pytest.approx(
        -94.7046, abs=1e-3
    )
    assert diagnostic["donor_flat"]["margin_base_cad_m3"] == pytest.approx(
        33.1365, abs=1e-3
    )
    assert diagnostic["donor_flat"]["margin_high_cad_m3"] == pytest.approx(
        81.3168, abs=1e-3
    )
    assert diagnostic["stumpage_scaled_sensitivity"][
        "margin_low_cad_m3"
    ] == pytest.approx(-130.0389, abs=1e-3)
    assert diagnostic["stumpage_scaled_sensitivity"][
        "margin_base_cad_m3"
    ] == pytest.approx(-16.1220, abs=1e-3)
    assert diagnostic["stumpage_scaled_sensitivity"][
        "margin_high_cad_m3"
    ] == pytest.approx(38.4085, abs=1e-3)
    assert float(diagnostic["allocation_volume_m3"]) == pytest.approx(
        118_977_339.1, abs=1.0
    )


def test_negative_margins_are_unclipped_and_counted(built):
    out_dir, summary = built
    rows = _read_csv(out_dir / model.MARGIN_CSV)
    negatives = [row for row in rows if float(row["margin_cad_m3"]) < 0.0]
    assert len(negatives) == 34
    assert summary["margins"]["negative_row_count"] == 34
    assert summary["margins"]["negative_documented"] is True
    assert summary["margins"]["negative_margin_clipped"] is False
    assert summary["margins"]["negative_by_scenario"] == {
        "donor_flat": 13,
        "stumpage_scaled": 21,
    }
    assert summary["margins"]["negative_central_case_count"] == {
        "donor_flat": 0,
        "stumpage_scaled": 5,
    }
    for row in rows:
        assert row["negative_margin_clipped"] == "false"
        margin = float(row["margin_cad_m3"])
        assert math.isfinite(margin)
        assert row["margin_status"] == (
            "negative_documented" if margin < 0.0 else "non_negative"
        )
        assert margin == pytest.approx(
            float(row["price_cad_m3"])
            - float(row["logging_haul_cad_m3"])
            - float(row["road_cad_m3"])
            - float(row["silviculture_cad_m3"]),
            abs=0.002,
        )


def test_tier_coverage_counts_match_price_rows(built):
    out_dir, summary = built
    counts = summary["tier_coverage"]["price_tier_counts"]
    assert counts["donor_flat"] == {
        "donor_proxy": 12,
        "reported_average": 1,
    }
    assert counts["stumpage_scaled"] == {
        "donor_proxy": 5,
        "reported_average": 1,
        "stumpage_scaled_proxy": 7,
    }
    rows = _read_csv(out_dir / model.TIER_CSV)
    price_counts = {
        (row["price_scenario"], row["price_tier"]): int(row["tier_count"])
        for row in rows
        if row["row_type"] == "price_tier_count"
    }
    assert price_counts[("donor_flat", "donor_proxy")] == 12
    assert price_counts[("donor_flat", "reported_average")] == 1
    assert price_counts[("stumpage_scaled", "stumpage_scaled_proxy")] == 7
    cost_counts = {
        (row["price_scenario"], row["cost_tier"]): int(row["tier_count"])
        for row in rows
        if row["row_type"] == "cost_tier_count"
        and row["price_scenario"] == "donor_flat"
    }
    assert cost_counts == {
        ("donor_flat", "donor_proxy"): 11,
        ("donor_flat", "donor_proxy_with_reported_road"): 1,
        ("donor_flat", "proxy_model"): 1,
    }
    for row in rows:
        if row["row_type"] == "scenario_total":
            assert row["province_count"] == "13"
            assert row["tier_count"] == "13"


def test_price_notes_do_not_label_donor_flat_as_sensitivity(built):
    out_dir, _ = built
    rows = _read_csv(out_dir / model.PRICE_CSV)
    for row in rows:
        note = row["notes"].lower()
        if row["price_scenario"] == "donor_flat":
            assert "base scenario" in note or row["province"] == "BC"
            assert "sensitivity" not in note
        else:
            assert "sensitivity" in note or row["province"] == "BC"


def test_scenario_comparison_declares_dues_not_comparable(built):
    out_dir, _ = built
    rows = _read_csv(out_dir / model.COMPARISON_CSV)
    assert len(rows) == 14
    assert {row["province"] for row in rows} == set(model.PROVINCES) | {
        "NATIONAL"
    }
    for row in rows:
        assert row["base_scenario"] == "donor_flat"
        assert row["sensitivity_scenario"] == "stumpage_scaled"
        assert row["dues_comparable_to_delivered_price"] == "false"
        assert (
            row["comparison_status"]
            == "method_change_not_observed_market_change"
        )
        assert "delivered log price" in row["notes"].lower()
    bc = next(row for row in rows if row["province"] == "BC")
    assert float(bc["scaled_minus_donor_flat_cad_m3"]) == pytest.approx(0.0)
    national = next(row for row in rows if row["province"] == "NATIONAL")
    assert float(national["scaled_minus_donor_flat_cad_m3"]) == pytest.approx(
        -49.2585, abs=1e-3
    )


def test_manifest_hashes_outputs_and_no_restricted_inputs(built):
    out_dir, summary = built
    manifest = json.loads((out_dir / model.MANIFEST_JSON).read_text(encoding="utf-8"))
    assert manifest["model_id"] == "national_economics_v2_1"
    assert manifest["artifact_version"] == "2.1.0"
    assert manifest["base_scenario"] == "donor_flat"
    assert manifest["sensitivity_scenario"] == "stumpage_scaled"
    assert manifest["stumpage_scaled_preferred"] is False
    assert manifest["stumpage_scaled_observed"] is False
    assert manifest["combined_national_net_value"] is False
    assert manifest["restricted_inputs_used"] == []
    assert manifest["observation_type"] == "model_estimate"
    assert manifest["labels"]["observed"] is False
    assert manifest["labels"]["policy_grade"] is False
    assert manifest["labels"]["combined_national_net_value"] is False
    assert len(manifest["outputs"]) == 10
    for item in manifest["outputs"]:
        path = out_dir / item["path"]
        assert path.is_file()
        assert len(item["sha256"]) == 64
    for item in manifest["inputs"]:
        assert not any(
            marker in item["path"].lower()
            for marker in ("confidential", "restricted", "microdata")
        )
    assert summary["labels"]["national_diagnostic_label"] == (
        "model_estimate_not_policy"
    )


def test_report_does_not_present_scaled_as_preferred_or_observed(built):
    out_dir, _ = built
    report = (out_dir / model.REPORT_MD).read_text(encoding="utf-8")
    assert "Base scenario: `donor_flat`" in report
    assert "Sensitivity: `stumpage_scaled`" in report
    assert "not preferred, not observed and not policy grade" in report
    assert "preferred scenario produces" not in report.lower()
    assert "Why dues are not comparable" in report
    assert "model_estimate_not_policy" in report
    assert "unclipped" in report.lower()
    assert "No combined national net value" in report
