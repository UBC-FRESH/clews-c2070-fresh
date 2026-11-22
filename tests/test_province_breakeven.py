"""Tests for the province break-even delivered-price artifact."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from workflows.national.harvest import province_breakeven as model

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_MARGIN_CSV = (
    REPO_ROOT
    / "data/derived/national/harvest_basis/national_economics_v2_3/"
    "national_margin_estimates.csv"
)
SOURCE_SUMMARY_CSV = (
    REPO_ROOT
    / "data/derived/national/harvest_basis/national_economics_v2_3/"
    "national_margin_summary.csv"
)
SOURCE_COST_CSV = (
    REPO_ROOT
    / "data/derived/national/harvest_basis/national_economics_v2_3/"
    "national_cost_inputs.csv"
)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


@pytest.fixture(scope="module")
def built(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[Path, dict]:
    out_dir = tmp_path_factory.mktemp("province_breakeven")
    payload = model.build(out_dir, check_deletions=False)
    return out_dir, payload


def _source_margins() -> dict[tuple[str, str], dict[str, str]]:
    return {
        (row["province"], row["price_scenario"]): row
        for row in _read_csv(SOURCE_MARGIN_CSV)
        if row["row_type"] == "margin" and row["scenario_id"] == "base"
    }


def _source_summary() -> dict[tuple[str, str], dict[str, str]]:
    return {
        (row["province"], row["price_scenario"]): row
        for row in _read_csv(SOURCE_SUMMARY_CSV)
        if row["row_type"] == "province"
    }


def test_coverage_and_labels(built):
    out_dir, payload = built
    rows = _read_csv(out_dir / model.PROVINCE_CSV)
    assert len(rows) == 13
    assert {row["province"] for row in rows} == set(model.PROVINCES)
    assert payload["province_count"] == 13
    assert payload["scenario_count"] == 2
    assert payload["observed"] is False
    assert payload["policy_grade"] is False
    assert payload["combined_national_net_value"] is False
    assert payload["labels"]["evidence"] == "model_estimate"
    assert payload["labels"]["observed"] is False
    assert payload["labels"]["policy_grade"] is False
    assert payload["labels"]["public_osecan_input"] is False
    for row in rows:
        assert row["evidence"] == "model_estimate"
        assert row["observed"] == "false"
        assert row["policy_grade"] == "false"
        assert "not_estimated" not in " ".join(row.values()).lower()
    for filename in (
        model.PROVINCE_CSV,
        model.SUMMARY_JSON,
        model.REPORT_MD,
        model.MANIFEST_JSON,
    ):
        text = (out_dir / filename).read_text(encoding="utf-8").lower()
        assert "not_estimated" not in text


def test_break_even_matches_v2_3_total_cost(built):
    out_dir, _ = built
    rows = {row["province"]: row for row in _read_csv(out_dir / model.PROVINCE_CSV)}
    margins = _source_margins()
    summary = _source_summary()
    for province in model.PROVINCES:
        row = rows[province]
        donor = margins[(province, model.BASE_SCENARIO)]
        scaled = margins[(province, model.SENSITIVITY_SCENARIO)]
        summary_row = summary[(province, model.BASE_SCENARIO)]
        break_even = float(row["break_even_price_cad_m3"])
        assert break_even == pytest.approx(
            float(donor["total_cost_cad_m3"]), abs=1e-4
        )
        assert break_even == pytest.approx(
            float(summary_row["total_cost_base_cad_m3"]), abs=1e-4
        )
        assert float(row["donor_price_cad_m3"]) == pytest.approx(
            float(donor["price_cad_m3"]), abs=1e-4
        )
        assert float(row["scaled_price_cad_m3"]) == pytest.approx(
            float(scaled["price_cad_m3"]), abs=1e-4
        )
        assert float(row["donor_margin_cad_m3"]) == pytest.approx(
            float(row["donor_price_cad_m3"]) - break_even, abs=1e-4
        )
        assert float(row["scaled_margin_cad_m3"]) == pytest.approx(
            float(row["scaled_price_cad_m3"]) - break_even, abs=1e-4
        )
        assert float(row["donor_margin_cad_m3"]) == pytest.approx(
            -float(row["donor_gap_to_breakeven_cad_m3"]), abs=1e-4
        )
        assert float(row["scaled_margin_cad_m3"]) == pytest.approx(
            -float(row["scaled_gap_to_breakeven_cad_m3"]), abs=1e-4
        )
        assert float(row["break_even_low_cad_m3"]) <= break_even
        assert break_even <= float(row["break_even_high_cad_m3"])


def test_cost_basis_and_no_double_count(built):
    out_dir, _ = built
    rows = {row["province"]: row for row in _read_csv(out_dir / model.PROVINCE_CSV)}
    source_rows = _read_csv(SOURCE_COST_CSV)
    assert len(source_rows) == 18
    for province in ("BC", "QC"):
        components = rows[province]["cost_components"].split(";")
        expected = model.EXPECTED_COMPONENTS[province]
        assert tuple(components) == expected
        assert rows[province]["component_count"] == str(len(expected))
        assert rows[province]["cost_basis"] == "component_sum"
        assert rows[province]["double_count_guard"] == "component_stack_summed_once"
        assert rows[province]["road_component_included"] == "true"
        assert rows[province]["silviculture_component_included"] == "true"
        component_sum = sum(
            float(row["base_cad_m3"])
            for row in source_rows
            if row["province"] == province
        )
        assert float(rows[province]["break_even_price_cad_m3"]) == pytest.approx(
            component_sum, abs=1e-4
        )
    for province in model.AGGREGATE_PROVINCES:
        row = rows[province]
        assert row["cost_components"] == "industry_aggregate_proxy"
        assert row["component_count"] == "1"
        assert row["cost_basis"] == "industry_aggregate_proxy"
        assert row["road_component_included"] == "false"
        assert row["silviculture_component_included"] == "false"
        assert row["aggregate_includes_overhead"] == "true"
        assert row["fallback_used"] == "false"
        assert (
            row["double_count_guard"]
            == "single_industry_aggregate_no_addons"
        )
        matches = [
            source
            for source in source_rows
            if source["province"] == province
        ]
        assert len(matches) == 1
        assert float(row["break_even_price_cad_m3"]) == pytest.approx(
            float(matches[0]["base_cad_m3"]), abs=1e-4
        )
    nu = rows["NU"]
    assert nu["cost_components"] == "total_cost_fallback"
    assert nu["cost_basis"] == "donor_fallback_total"
    assert nu["fallback_used"] == "true"
    assert nu["double_count_guard"] == "donor_total_fallback_no_addons"
    assert nu["aggregate_includes_overhead"] == "true"
    assert float(nu["break_even_price_cad_m3"]) == pytest.approx(
        86.9381, abs=1e-4
    )


def test_negative_margins_are_retained_and_classified(built):
    out_dir, payload = built
    rows = {row["province"]: row for row in _read_csv(out_dir / model.PROVINCE_CSV)}
    scaled_negative = {
        province
        for province, row in rows.items()
        if float(row["scaled_margin_cad_m3"]) < 0.0
    }
    assert scaled_negative == {"AB", "MB", "ON", "QC", "NB"}
    assert float(rows["AB"]["scaled_margin_cad_m3"]) == pytest.approx(
        -36.3258, abs=1e-4
    )
    assert float(rows["ON"]["scaled_margin_cad_m3"]) == pytest.approx(
        -60.9518, abs=1e-4
    )
    assert float(rows["NB"]["scaled_margin_cad_m3"]) == pytest.approx(
        -14.1365, abs=1e-4
    )
    assert rows["AB"]["negative_margin_scenarios"] == "stumpage_scaled"
    assert rows["NB"]["negative_margin_scenarios"] == "stumpage_scaled"
    assert rows["BC"]["negative_margin_scenarios"] == "none"
    assert payload["scenarios"]["donor_flat"]["negative_margin_count"] == 0
    assert payload["scenarios"]["stumpage_scaled"]["negative_margin_count"] == 5
    assert payload["classification_counts"] == {
        "positive_margin": 8,
        "negative_margin": 0,
        "price_limited": 1,
        "cost_limited": 4,
    }
    assert rows["NB"]["classification"] == "price_limited"
    assert rows["NB"]["price_limited_flag"] == "true"
    assert rows["NB"]["scaled_margin_class"] == "price_limited"
    for province in ("AB", "MB", "ON", "QC"):
        assert rows[province]["classification"] == "cost_limited"
        assert rows[province]["cost_limited_flag"] == "true"
    for province in ("BC", "SK", "NS", "NL", "PE", "YT", "NT", "NU"):
        assert rows[province]["classification"] == "positive_margin"


def test_price_sensitivity_is_diagnostic_not_ranking(built):
    _, payload = built
    sensitivity = payload["price_sensitivity"]
    assert sensitivity["diagnostic_order_not_policy_ranking"] is True
    closest = sensitivity["closest_to_break_even"]
    assert closest[0]["province"] == "NB"
    assert closest[0]["absolute_margin_pct_of_break_even"] == pytest.approx(
        14.3276, abs=1e-4
    )
    assert closest[1]["province"] == "NU"
    swing = sensitivity["largest_scaled_price_reduction"]
    assert swing[0]["province"] == "AB"
    assert swing[0]["scaled_price_change_cad_m3"] == pytest.approx(
        -97.1673, abs=1e-4
    )
    fallback_provinces = {
        item["province"]
        for item in swing
        if item["scaled_price_change_cad_m3"] == 0.0
    }
    assert fallback_provinces == {"BC", "NL", "PE", "YT", "NT", "NU"}


def test_unexpected_statcan_d_fails_closed():
    source_summary = {
        "statcan_d_status": {
            "allowed_cells": ["AB:salaries_wages_status"],
            "unexpected_cells": [],
        }
    }
    allowed = [{"province": "AB", "salaries_wages_status": "D"}]
    model._validate_statcan_d(allowed, source_summary)
    unexpected = [{"province": "AB", "energy_fuel_status": "D"}]
    with pytest.raises(model.ProvinceBreakevenError, match="unexpected StatCan"):
        model._validate_statcan_d(unexpected, source_summary)


def test_unexpected_derived_cost_d_fails_closed():
    source_summary = {
        "statcan_d_status": {
            "allowed_cells": ["AB:industry_worst_quality_status"],
            "unexpected_cells": [],
        }
    }
    allowed = [{"province": "AB", "statcan_status": "D"}]
    model._validate_derived_cost_d(allowed, source_summary)
    unexpected = [
        {"province": "AB", "statcan_status": "D"},
        {"province": "SK", "statcan_status": "D"},
    ]
    with pytest.raises(
        model.ProvinceBreakevenError, match="unexpected D status"
    ):
        model._validate_derived_cost_d(unexpected, source_summary)


def test_deletion_lines_detects_both_status_columns():
    assert model._deletion_lines(" M keep.txt\n D removed.txt\nD  staged.txt\n")
    assert model._deletion_lines("?? new.txt\n M keep.txt\n") == []


def test_manifest_hashes_and_labels(built):
    out_dir, payload = built
    manifest = json.loads(
        (out_dir / model.MANIFEST_JSON).read_text(encoding="utf-8")
    )
    assert manifest["model_id"] == "province_breakeven_v1"
    assert manifest["artifact_version"] == "1.0.0"
    assert manifest["source_model_id"] == "national_economics_v2_3"
    assert manifest["source_artifact_version"] == "2.3.0"
    assert manifest["observed"] is False
    assert manifest["policy_grade"] is False
    assert manifest["combined_national_net_value"] is False
    assert manifest["not_policy_result"] is True
    assert manifest["ranking_performed"] is False
    assert len(manifest["inputs"]) == 5
    assert len(manifest["outputs"]) == 3
    for item in manifest["outputs"]:
        path = out_dir / item["path"]
        assert path.is_file()
        assert len(item["sha256"]) == 64
    assert payload["cross_checks"]["v2_3_total_cost_match"] is True
    assert payload["cross_checks"]["max_abs_total_cost_delta_cad_m3"] == 0.0
    assert (
        payload["cross_checks"]["max_abs_weighted_margin_delta_cad_m3"]
        < 1e-3
    )


def test_report_states_method_and_non_claims(built):
    out_dir, _ = built
    report = (out_dir / model.REPORT_MD).read_text(encoding="utf-8")
    for phrase in (
        "Break-even and price comparison",
        "Donor versus scaled",
        "Price sensitivity",
        "Arithmetic and v2.3 cross-checks",
        "no separate road, silviculture or haul add-on",
        "observed=false",
        "policy_grade=false",
        "not a merit or policy ranking",
        "54.9865",
        "98.6665",
    ):
        assert phrase in report
    assert "not_estimated" not in report.lower()
