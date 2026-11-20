"""Harvest feasibility gate: contract, runner, and CLI diagnostics."""

from __future__ import annotations

from pathlib import Path

import pytest

from workflows.ui.contracts import parse_scenario
from workflows.ui.feasibility import (
    DOCUMENTED_AAC_PARTIAL_SUM_M3,
    MAX_NORMAL_HARVEST_M3,
    NFD_WOOD_SUPPLY_SUM_M3,
    assess_harvest_feasibility,
)
from workflows.ui.osecan_runner import (
    CANONICAL_SET_PATH,
    RunnerError,
    evaluate_harvest_feasibility,
    load_canonical_set,
    run_scenario,
)
from workflows.ui.run_scenario import build_parser

REPO_ROOT = Path(__file__).resolve().parents[1]


def _scenario(level_m3: float, *, path: str = "custom"):
    return parse_scenario(
        {
            "scenario_id": "feasibility_fixture",
            "label": "Feasibility fixture",
            "description": "Harvest gate unit fixture.",
            "schema_version": "clews.scenario.v1",
            "harvest": {
                "level_m3_per_year": level_m3,
                "path": path,
                "recovery_fraction": 0.6,
                "cost_basis": "adopted_rest_of_canada_curve",
                "supply_curve": "tiered",
            },
            "energy": {
                "carbon_price_schedule": [
                    {"year": 2021, "price_usd_per_tco2e": 95.0},
                    {"year": 2026, "price_usd_per_tco2e": 200.0},
                ],
                "hydro_mode": "cap_2021",
                "pwr_bio_cap_fraction": 0.05,
                "nuclear_cap_gw": 0.0,
                "wind_cost_usd_per_gj": 3.0,
                "wind_cap_fraction": 0.2,
                "t3_cap_pj": 39.873714,
                "t4_cap_pj": 64.265464,
                "generic_bio_cost_usd_per_gj": 3.0,
            },
            "constraints": {"set_id": "screening_v0"},
            "provenance": {
                "base_data_commit": "test",
                "model_commit": "test-model",
                "created_utc": "2026-11-20T00:00:00+00:00",
            },
        }
    )


def test_documented_bounds_and_ladder_statuses() -> None:
    assert MAX_NORMAL_HARVEST_M3 == 150_000_000
    assert DOCUMENTED_AAC_PARTIAL_SUM_M3 == 157_343_387
    assert NFD_WOOD_SUPPLY_SUM_M3 == 207_680_142
    assert assess_harvest_feasibility(100_000_000).status == "pass"
    assert assess_harvest_feasibility(118_977_339).status == "pass"
    assert assess_harvest_feasibility(150_000_000).status == "conditional"
    assert assess_harvest_feasibility(200_000_000).status == "fail"
    assert assess_harvest_feasibility(200_000_000).pareto_eligible is False
    assert assess_harvest_feasibility(200_000_000).diagnostic is True
    assert assess_harvest_feasibility(118_977_339, pareto_eligible=True).pareto_eligible


def test_runner_rejects_200m_before_model_copy(tmp_path: Path) -> None:
    scenario = _scenario(200_000_000)
    with pytest.raises(RunnerError, match="feasibility"):
        run_scenario(scenario, tmp_path / "out", repo_root=REPO_ROOT)
    assert not (tmp_path / "out" / "model").exists()


def test_runner_allows_200m_with_explicit_diagnostic_flag(tmp_path: Path) -> None:
    scenario = _scenario(200_000_000)
    canonical = load_canonical_set(CANONICAL_SET_PATH)
    feasibility = evaluate_harvest_feasibility(
        scenario, canonical, allow_infeasible=True
    )
    assert feasibility.status == "fail"
    assert feasibility.diagnostic is True
    assert feasibility.pareto_eligible is False


def test_cli_exposes_allow_infeasible_flag() -> None:
    args = build_parser().parse_args(
        ["--scenario", "s.json", "--out", "out", "--allow-infeasible"]
    )
    assert args.allow_infeasible is True
    assert build_parser().parse_args(["--scenario", "s.json", "--out", "out"]).allow_infeasible is False


def test_150m_is_conditional_and_100_119_pass() -> None:
    canonical = load_canonical_set(CANONICAL_SET_PATH)
    for level, expected in (
        (100_000_000, "pass"),
        (118_977_339, "pass"),
        (150_000_000, "conditional"),
    ):
        feasibility = evaluate_harvest_feasibility(_scenario(level), canonical)
        assert feasibility.status == expected
        assert feasibility.pareto_eligible is False
