"""Tests for the milestone-1b canonical set and OSeCAN parameter generation."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from workflows.ui.contracts import parse_scenario
from workflows.ui.osecan_runner import (
    CANONICAL_SET_PATH,
    compute_supply_caps,
    load_canonical_set,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
MODEL_ROOT = Path(
    os.environ.get("CLEWS_OSECAN_MODEL_ROOT", REPO_ROOT / "runtime/osecan_model")
)
MODEL_AVAILABLE = (MODEL_ROOT / "model_manifest.json").is_file()


def scenario_payload(*, scenario_id: str, level_m3: float, path: str = "custom") -> dict:
    return {
        "scenario_id": scenario_id,
        "label": f"Screening {level_m3:.0f} m3",
        "description": "Milestone 1b test scenario.",
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
            "model_commit": "40d5b43bb3f8b3f69c16e953144d35eeed5f0f44",
            "created_utc": "2026-11-20T00:00:00+00:00",
        },
    }


def test_canonical_set_is_screening_only() -> None:
    canonical = load_canonical_set(CANONICAL_SET_PATH)
    assert canonical.set_id == "screening_v0"
    assert canonical.status == "screening_only"
    assert canonical.policy_grade is False
    assert canonical.gas_output_factor_mt_per_pj == pytest.approx(0.1377)
    assert canonical.coal_output_factor_mt_per_pj == pytest.approx(0.261)
    joined = " ".join(canonical.notes).lower()
    for phrase in ("reserve-margin", "transmission", "curtailment", "build-rate"):
        assert phrase in joined


@pytest.mark.parametrize(
    ("level", "national_pj", "tier_caps"),
    [
            (100_000_000.0, 128.530929, [85.007113, 0.0, 0.0, 0.0]),
            (118_977_339.0, 152.922679, [85.007113, 24.39175, 0.0, 0.0]),
            (150_000_000.0, 192.796393, [85.007113, 24.39175, 39.873714, 0.0]),
            (200_000_000.0, 257.061857, [85.007113, 24.39175, 39.873714, 64.265464]),
    ],
)
def test_supply_caps_tile_national_total(
    level: float,
    national_pj: float,
    tier_caps: list[float],
) -> None:
    canonical = load_canonical_set(CANONICAL_SET_PATH)
    scenario = parse_scenario(
        scenario_payload(scenario_id=f"cap_{int(level / 1e6)}m", level_m3=level)
    )
    caps = compute_supply_caps(scenario, canonical)
    year = 2050
    assert caps.national_pj_by_year[year] == pytest.approx(national_pj, abs=1e-6)
    assert caps.bc_zonal_pj_by_year[year] == pytest.approx(43.523816, abs=1e-6)
    assert caps.rest_of_canada_pj_by_year[year] == pytest.approx(
        national_pj - 43.523816, abs=1e-6
    )
    observed_tiers = [caps.tier_caps_pj_by_year[tech][year] for tech in (
        "SUPBCWT1",
        "SUPBCWT2",
        "SUPBCWT3",
        "SUPBCWT4",
    )]
    for actual, expected in zip(observed_tiers, tier_caps):
        assert actual == pytest.approx(expected, abs=1e-6)
    assert sum(observed_tiers) == pytest.approx(caps.rest_of_canada_pj_by_year[year], abs=1e-9)
    assert caps.checks["tier_sum_equals_rest_all_years"] is True
    assert caps.checks["national_equals_tiers_plus_bc_all_years"] is True


def test_harvest_reference_paths_are_used_not_scenario_level() -> None:
    canonical = load_canonical_set(CANONICAL_SET_PATH)
    scenario = parse_scenario(
        scenario_payload(scenario_id="hold_path", level_m3=200_000_000.0, path="hold")
    )
    caps = compute_supply_caps(scenario, canonical)
    assert caps.log_m3_by_year[2050] == pytest.approx(118_977_339.0)
    assert caps.national_pj_by_year[2050] == pytest.approx(152.922679, abs=1e-6)


def test_custom_harvest_uses_explicit_level() -> None:
    canonical = load_canonical_set(CANONICAL_SET_PATH)
    scenario = parse_scenario(scenario_payload(scenario_id="custom", level_m3=123_456_789.0))
    caps = compute_supply_caps(scenario, canonical)
    assert set(caps.log_m3_by_year.values()) == {123_456_789.0}


def test_tier_shortfall_fails_closed() -> None:
    canonical = load_canonical_set(CANONICAL_SET_PATH)
    payload = scenario_payload(scenario_id="short_t4", level_m3=200_000_000.0)
    payload["energy"]["t4_cap_pj"] = 0.0
    scenario = parse_scenario(payload)
    with pytest.raises(RuntimeError, match="tier caps do not tile"):
        compute_supply_caps(scenario, canonical)


@pytest.mark.skipif(not MODEL_AVAILABLE, reason="disposable OSeCAN model copy is unavailable")
def test_dry_run_generates_harmonized_params(tmp_path: Path) -> None:
    scenario_path = tmp_path / "scenario.json"
    scenario_path.write_text(
        json.dumps(scenario_payload(scenario_id="dry_run", level_m3=118_977_339.0)),
        encoding="utf-8",
    )
    output_dir = tmp_path / "output"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "workflows.ui.run_scenario",
            "--scenario",
            str(scenario_path),
            "--out",
            str(output_dir),
            "--dry-run",
            "--model-root",
            str(MODEL_ROOT),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    summary = json.loads((output_dir / "run_summaries.json").read_text("utf-8"))
    assert summary["status"] == "completed"
    assert summary["harmonize"]["exit_code"] == 0
    assert summary["supply"]["checks"]["tier_sum_equals_rest_all_years"] is True
    assert (output_dir / "run/params/TotalTechnologyAnnualActivityUpperLimit.csv").is_file()
    assert (output_dir / "manifest.json").is_file()
