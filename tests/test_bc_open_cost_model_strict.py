"""Tests for the strict-open BC logging and truck-haul cost model."""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path

import pytest

from workflows.national.harvest import bc_open_cost_model_strict as model

REPO_ROOT = Path(__file__).resolve().parents[1]


def make_scenario(**overrides):
    base = model.Scenario(
        zone="South Central Zone",
        region="Interior",
        slope_class="moderate",
        slope_pct=25.0,
        system="ground",
        piece_class="medium",
        piece_size_m3=0.60,
        volume_class="medium_sale",
        sale_volume_m3=20_000.0,
        distance_class="medium_haul",
        haul_distance_km=100.0,
    )
    return model.Scenario(**{**base.__dict__, **overrides})


def test_sessions_2021_worked_examples_recomputed():
    assert model.machine_rate(
        model.sessions_example_spec(1.00), method="modified"
    ).total_cad_per_pmh == pytest.approx(140.60, abs=0.01)
    assert model.machine_rate(
        model.sessions_example_spec(0.50), method="traditional"
    ).total_cad_per_pmh == pytest.approx(269.20, abs=0.01)
    assert model.machine_rate(
        model.sessions_example_spec(0.50), method="modified"
    ).total_cad_per_pmh == pytest.approx(192.60, abs=0.01)


def test_miyata_1980_worked_example_recomputed():
    rate = model.machine_rate(model.miyata_example_spec(), method="modified")
    assert rate.total_cad_per_pmh == pytest.approx(35.61, abs=0.02)
    assert rate.depreciation_cad_per_pmh == pytest.approx(9.31, abs=0.01)
    assert (
        rate.depreciation_cad_per_pmh
        + rate.interest_insurance_tax_cad_per_pmh
    ) == pytest.approx(13.92, abs=0.01)


def test_machine_rate_components_sum_and_utilization_direction():
    rate = model.machine_rate(
        model.machine_spec("processor", "Interior", model.BASE_PARAMS)
    )
    components = (
        rate.depreciation_cad_per_pmh
        + rate.interest_insurance_tax_cad_per_pmh
        + rate.fuel_lube_cad_per_pmh
        + rate.repair_cad_per_pmh
        + rate.tire_cad_per_pmh
        + rate.labour_cad_per_pmh
        + rate.overhead_profit_cad_per_pmh
    )
    assert math.isclose(components, rate.total_cad_per_pmh, rel_tol=1e-9)
    assert rate.total_cad_per_smh == pytest.approx(
        rate.total_cad_per_pmh * rate.utilization, rel=1e-9
    )
    low_utilization = model.machine_rate(
        model.machine_spec(
            "processor",
            "Interior",
            model.ParameterSet(
                **{**model.BASE_PARAMS.__dict__, "utilization_delta": -0.10}
            ),
        )
    )
    high_utilization = model.machine_rate(
        model.machine_spec(
            "processor",
            "Interior",
            model.ParameterSet(
                **{**model.BASE_PARAMS.__dict__, "utilization_delta": 0.05}
            ),
        )
    )
    assert low_utilization.total_cad_per_pmh > high_utilization.total_cad_per_pmh


def test_productivity_increases_with_piece_size():
    for phase in ("feller_buncher", "grapple_skidder", "processor", "yarder"):
        values = [
            model.phase_productivity(
                phase, piece, 25.0, "ground" if phase != "yarder" else "cable",
                model.BASE_PARAMS,
            )
            for piece in (0.30, 0.60, 1.00)
        ]
        assert values[0] < values[1] < values[2], (phase, values)


def test_logging_cost_falls_with_piece_size():
    costs = [
        model.logging_cost(
            "Interior",
            25.0,
            piece,
            20_000.0,
            "ground",
            model.BASE_PARAMS,
        ).base
        for piece in (0.30, 0.60, 1.00)
    ]
    assert costs[0] > costs[1] > costs[2]


def test_haul_cost_increases_with_distance():
    costs = [
        model.haul_cost("Interior", distance, model.BASE_PARAMS).base
        for distance in (50.0, 100.0, 150.0)
    ]
    assert costs == sorted(costs)
    assert all(cost > 0.0 for cost in costs)


def test_full_grid_has_expected_coverage():
    scenarios = model.build_scenarios()
    assert len(scenarios) == 756
    assert {scenario.zone for scenario in scenarios} == {
        zone for zone, _ in model.ZONES
    }
    assert {scenario.region for scenario in scenarios} == set(model.REGIONS)
    assert {scenario.slope_class for scenario in scenarios} == {
        slope_class for slope_class, _, _ in model.SLOPE_SCENARIOS
    }
    assert {scenario.system for scenario in scenarios} == {"ground", "cable"}
    assert {scenario.piece_class for scenario in scenarios} == {
        piece_class for piece_class, _ in model.PIECE_SCENARIOS
    }
    assert {scenario.volume_class for scenario in scenarios} == {
        volume_class for volume_class, _ in model.VOLUME_SCENARIOS
    }
    assert {scenario.distance_class for scenario in scenarios} == {
        distance_class for distance_class, _ in model.DISTANCE_SCENARIOS
    }


def test_all_estimates_are_finite_ordered_and_labelled():
    for scenario in model.build_scenarios():
        row = model.estimate_scenario(scenario)
        for column in (
            "logging_low_cad_m3",
            "logging_base_cad_m3",
            "logging_high_cad_m3",
            "haul_low_cad_m3",
            "haul_base_cad_m3",
            "haul_high_cad_m3",
            "total_low_cad_m3",
            "total_base_cad_m3",
            "total_high_cad_m3",
        ):
            value = float(row[column])
            assert math.isfinite(value)
            assert value >= 0.0
        assert float(row["total_low_cad_m3"]) <= float(row["total_base_cad_m3"])
        assert float(row["total_base_cad_m3"]) <= float(row["total_high_cad_m3"])
        assert row["evidence"] == "model_estimate"
        assert row["cost_basis"] == "strict_open_proxy"
        assert row["licence_tier"] == "strict_open"
        assert row["policy_grade"] == "false"
        assert row["observed"] == "false"
        assert row["calibration_claim"] == "none_bc_empirical"
        assert row["bc_empirical_calibration"] == "false"


def test_source_register_is_strict_open_only():
    model.validate_source_register()
    allowed = {"statcan_open", "ogl_bc", "ogl_canada", "cc_by", "public_domain"}
    for source in model.SOURCE_REGISTER:
        assert source["licence_tier"] in allowed
        haystack = " ".join(source.values()).lower()
        for token in ("appraisal manual", "rate guide", "fpinnovations", "bcws"):
            assert token not in haystack, (source["source_key"], token)


def test_region_slope_context_uses_ogl_inputs_and_sums_to_one():
    context = model.region_slope_context()
    for region in model.REGIONS:
        shares = [
            context[region][slope_class]
            for slope_class, _, _ in model.SLOPE_SCENARIOS
        ]
        assert sum(shares) == pytest.approx(1.0, abs=1e-9)
        assert all(0.0 <= share <= 1.0 for share in shares)
    assert context["Coast"]["share_gt15"] > context["Interior"]["share_gt15"]
    assert context["BC_road_access"]["road_gt_20km"] > 0.0


def test_worked_examples_pass():
    examples = model.worked_examples()
    assert len(examples) == 4
    assert all(row["status"] == "pass" for row in examples)


def test_sensitivity_cases_cover_all_named_drivers():
    rows = model.sensitivity_rows()
    ids = {row["case_id"] for row in rows}
    for required in (
        "base",
        "piece_0p30",
        "piece_1p00",
        "volume_5k",
        "volume_100k",
        "haul_50km",
        "haul_150km",
        "wage_low",
        "wage_high",
        "diesel_low",
        "diesel_high",
        "capital_low",
        "capital_high",
        "productivity_low",
        "productivity_high",
        "utilization_low",
        "utilization_high",
    ):
        assert required in ids
    assert all(
        row["evidence"] == "model_estimate"
        and row["cost_basis"] == "strict_open_proxy"
        and row["licence_tier"] == "strict_open"
        and row["policy_grade"] == "false"
        and row["observed"] == "false"
        for row in rows
    )


def test_build_writes_all_artifacts_and_validates(tmp_path):
    out_dir = tmp_path / "strict"
    manifest = model.build(out_dir)
    expected = {
        "strict_estimates.csv",
        "strict_region_slope.csv",
        "strict_zone_summary.csv",
        "strict_sensitivity.csv",
        "strict_parameters.csv",
        "strict_sources.csv",
        "strict_statcan_observations.csv",
        "strict_worked_examples.csv",
        "strict_comparison.csv",
        "strict_comparison_summary.csv",
        "README.md",
        "manifest.json",
        "summary.json",
    }
    assert expected == {path.name for path in out_dir.iterdir()}
    assert manifest["validation"]["estimate_rows"] == 756
    assert manifest["validation"]["labels_strict_open"] is True
    assert manifest["restricted_inputs_used"] == []
    assert manifest["strict_open_price_series_needed"] is True
    assert manifest["strict_open_price_series_status"] == "gap"
    assert manifest["comparison"]["reference_used_as_model_input"] is False
    assert manifest["comparison"]["comparison_role"] == "cross_check_only_not_input"
    assert manifest["worked_examples"][0]["status"] == "pass"

    for key, digest in manifest["output_sha256"].items():
        path = out_dir / key
        assert path.is_file()
        import hashlib

        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest

    with (out_dir / "strict_estimates.csv").open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 756
    assert all(row["evidence"] == "model_estimate" for row in rows)
    assert all(row["licence_tier"] == "strict_open" for row in rows)

    with (out_dir / "strict_comparison.csv").open(encoding="utf-8") as handle:
        comparison = list(csv.DictReader(handle))
    assert len(comparison) == 756
    assert all(
        row["reference_used_as_model_input"] == "false" for row in comparison
    )
    assert all(
        row["comparison_status"] == "matched_cross_check_only"
        for row in comparison
    )
    assert all(
        row["reference_licence_tier"] == "public_reference_copyrighted"
        for row in comparison
    )

    with (out_dir / "summary.json").open(encoding="utf-8") as handle:
        summary = json.load(handle)
    assert summary["strict_open_only"] is True
    assert summary["strict_open_price_series_needed"] is True
    assert summary["calibration_claim"] == "none_bc_empirical"
    assert summary["restricted_inputs_used"] == []


def test_no_nan_or_infinity_in_summary_and_manifest(tmp_path):
    out_dir = tmp_path / "strict"
    model.build(out_dir)
    for filename in ("manifest.json", "summary.json"):
        text = (out_dir / filename).read_text(encoding="utf-8")
        assert "NaN" not in text
        assert "Infinity" not in text
