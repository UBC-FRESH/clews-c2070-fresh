"""Tests for the open-data BC logging and truck-haul cost model."""

from __future__ import annotations

import csv
import math
from pathlib import Path

import pytest

from workflows.national.harvest import bc_open_cost_model as model

REPO_ROOT = Path(__file__).resolve().parents[1]


def make_scenario(**overrides):
    base = model.Scenario(
        zone="South Central Zone",
        rate_group=model.RATE_GROUP_INTERIOR,
        region="Interior",
        slope_class="moderate",
        slope_pct=28.0,
        system="ground",
        piece_class="medium",
        piece_size_m3=0.60,
        volume_class="medium_sale",
        sale_volume_m3=20_000.0,
        distance_class="medium_haul",
        haul_distance_km=100.0,
    )
    return model.Scenario(**{**base.__dict__, **overrides})


def test_equipment_rate_ranges_are_ordered():
    for rate_group, machines in model.EQUIPMENT_RATES.items():
        for machine, (low, base, high) in machines.items():
            assert 0.0 < low <= base <= high, (rate_group, machine)


def test_published_swing_yarder_worked_example():
    # FPInnovations ADV5N19: 335.51 CAD/SMH, 117 SMH, 3,640 m3.
    computed = 335.51 / (3640.0 / 117.0)
    assert computed == pytest.approx(10.78, abs=0.01)
    assert model.phase_cost(335.51, 3640.0 / 117.0) == pytest.approx(10.78, abs=0.01)


def test_published_iam_haul_worked_example():
    assert model.published_interior_haul_from_cycle(3.0, cpif=1.0) == pytest.approx(
        7.221, abs=1e-3
    )


def test_cycle_time_increases_with_distance():
    distances = [10.0, 50.0, 100.0, 150.0]
    cycles = [model.cycle_hours(distance) for distance in distances]
    assert cycles == sorted(cycles)
    assert all(cycle > 0.0 for cycle in cycles)


def test_haul_cost_increases_with_distance():
    costs = [
        model.haul_cost(make_scenario(haul_distance_km=distance)).base
        for distance in (50.0, 100.0, 150.0)
    ]
    assert costs == sorted(costs)


def test_logging_cost_increases_with_slope_on_ground():
    gentle = model.logging_cost(make_scenario(slope_pct=10.0, system="ground"))
    moderate = model.logging_cost(make_scenario(slope_pct=28.0, system="ground"))
    assert moderate.base > gentle.base
    assert model.slope_adjustment(10.0, "ground") == 0.0
    assert model.slope_adjustment(28.0, "ground") > 0.0


def test_logging_cost_falls_with_larger_pieces():
    small = model.logging_cost(make_scenario(piece_size_m3=0.30))
    medium = model.logging_cost(make_scenario(piece_size_m3=0.60))
    large = model.logging_cost(make_scenario(piece_size_m3=1.00))
    assert small.base > medium.base > large.base


def test_logging_cost_falls_with_larger_sales():
    small = model.logging_cost(make_scenario(sale_volume_m3=5_000.0))
    medium = model.logging_cost(make_scenario(sale_volume_m3=20_000.0))
    large = model.logging_cost(make_scenario(sale_volume_m3=100_000.0))
    assert small.base > medium.base > large.base


def test_helicopter_scenario_uses_published_allowance():
    scenario = make_scenario(slope_pct=70.0, system="heli")
    logging = model.logging_cost(scenario)
    assert logging.base == pytest.approx(model.HELI_LOGGING_BASE_CAD_M3, abs=1e-9)
    assert logging.low < logging.base < logging.high


def test_all_scenario_costs_are_ordered_finite_and_non_negative():
    for scenario in model.build_scenarios():
        logging = model.logging_cost(scenario)
        haul = model.haul_cost(scenario)
        assert math.isfinite(logging.low)
        assert 0.0 <= logging.low <= logging.base <= logging.high
        assert 0.0 <= haul.low <= haul.base <= haul.high
        total_low = logging.low + haul.low
        total_high = logging.high + haul.high
        assert total_low <= total_high


def test_full_grid_has_expected_coverage():
    scenarios = model.build_scenarios()
    assert len(scenarios) == 945
    assert {scenario.zone for scenario in scenarios} == {zone for zone, _, _ in model.ZONES}
    assert {scenario.rate_group for scenario in scenarios} == {
        model.RATE_GROUP_COAST,
        model.RATE_GROUP_INTERIOR,
    }
    assert {scenario.system for scenario in scenarios} == {"ground", "cable", "heli"}


def test_unknown_system_fails_loudly():
    with pytest.raises(model.BcOpenCostModelError):
        model.system_phases(model.RATE_GROUP_INTERIOR, "balloon")


def test_invalid_phase_inputs_fail_loudly():
    with pytest.raises(model.BcOpenCostModelError):
        model.phase_cost(0.0, 30.0)
    with pytest.raises(model.BcOpenCostModelError):
        model.phase_cost(300.0, 0.0)
    with pytest.raises(model.BcOpenCostModelError):
        model.cycle_hours(0.0)


def test_build_writes_all_artifacts(tmp_path):
    out_dir = tmp_path / "bc_open_cost_model"
    manifest = model.build(out_dir)

    estimates_path = out_dir / "bc_open_cost_estimates.csv"
    parameters_path = out_dir / "bc_open_cost_parameters.csv"
    sources_path = out_dir / "bc_open_cost_sources.csv"
    sensitivity_path = out_dir / "bc_open_cost_sensitivity.csv"
    for path in (estimates_path, parameters_path, sources_path, sensitivity_path):
        assert path.is_file()

    with estimates_path.open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 945
    assert all(float(row["total_low_cad_m3"]) >= 0.0 for row in rows)
    assert all(
        float(row["total_low_cad_m3"])
        <= float(row["total_base_cad_m3"])
        <= float(row["total_high_cad_m3"])
        for row in rows
    )

    with parameters_path.open(encoding="utf-8") as handle:
        parameters = list(csv.DictReader(handle))
    required = {"component", "formula", "coefficient", "unit", "applicability", "source", "licence"}
    assert required <= set(parameters[0])

    with sources_path.open(encoding="utf-8") as handle:
        sources = list(csv.DictReader(handle))
    assert len(sources) == len(model.SOURCES)

    with sensitivity_path.open(encoding="utf-8") as handle:
        sensitivity = list(csv.DictReader(handle))
    assert sensitivity[0]["case_id"] == "base"
    assert sensitivity[0]["delta_total_cad_m3"] == "+0.00"

    assert manifest["validation"]["estimates_non_negative_finite"] is True
    assert manifest["validation"]["total_low_base_high_ordered"] is True
    assert manifest["validation"]["all_zones_present"] is True
    assert manifest["validation"]["all_systems_present"] is True


def test_parameter_rows_cite_every_source_used_in_the_model():
    rows = model.parameter_rows()
    source_keys = {row["source_key"] for row in rows}
    assert "tla_rate_guide_2026_08" in source_keys
    assert "fpinnovations_adv2n54" in source_keys
    assert "fpinnovations_adv5n19" in source_keys
    assert "bc_iam_2026" in source_keys
    assert all(row["licence"] for row in rows)
    assert all(row["source_url"] for row in rows)
