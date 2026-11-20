"""Stage-1 policy-test constraint-set and parameter-generation tests."""

from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from workflows.ui.osecan_runner import (
    POLICY_SET_PATH,
    PolicyConstraintSet,
    _preflight_policy_parameters,
    load_constraint_set,
    load_policy_set,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
MODEL_ROOT = Path(
    os.environ.get("CLEWS_OSECAN_MODEL_ROOT", REPO_ROOT / "runtime/osecan_model")
)
MODEL_AVAILABLE = (MODEL_ROOT / "model_manifest.json").is_file()


def policy_scenario_payload(*, scenario_id: str = "policy_dry") -> dict:
    return {
        "scenario_id": scenario_id,
        "label": "Policy test dry run",
        "description": "Stage-1 draft policy test; not policy-grade.",
        "schema_version": "clews.scenario.v1",
        "harvest": {
            "level_m3_per_year": 118_977_339.0,
            "path": "hold",
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
        "constraints": {"set_id": "policy_test_v1"},
        "provenance": {
            "base_data_commit": "test",
            "model_commit": "40d5b43bb3f8b3f69c16e953144d35eeed5f0f44",
            "created_utc": "2026-11-20T00:00:00+00:00",
        },
    }


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_policy_set_loads_as_draft() -> None:
    policy = load_policy_set(POLICY_SET_PATH)
    assert isinstance(policy, PolicyConstraintSet)
    assert policy.set_id == "policy_test_v1"
    assert policy.status == "draft"
    assert policy.policy_grade is False
    assert policy.screening_only is False
    assert "transmission" in policy.structural_gaps
    assert "curtailment/timeslices" in policy.structural_gaps
    assert "unit_commitment" in policy.structural_gaps
    assert policy.credit_for("PWRWNDA1") == pytest.approx(0.20)
    assert policy.credit_for("PWRSOLA1") == pytest.approx(0.15)
    assert policy.credit_for("PWRHYDA1") == pytest.approx(0.65)
    assert policy.credit_for("PWRURNA1") == pytest.approx(0.90)
    assert policy.credit_for("PWRNGSB1") == pytest.approx(0.95)
    assert policy.credit_for("PWRTRNA1") == pytest.approx(0.0)
    assert policy.source_status == "reviewed"
    assert policy.value_status == "draft"
    assert "PWRWND<z>" in policy.build_rates
    assert policy.hydro_mode == "cap_2021"
    assert policy.nuclear_mode == "build_rate_only"


def test_policy_set_loader_dispatches_by_set_id() -> None:
    screening = load_constraint_set("screening_v0")
    policy = load_constraint_set("policy_test_v1")
    assert screening.set_id == "screening_v0"
    assert screening.screening_only is True
    assert policy.set_id == "policy_test_v1"
    assert policy.screening_only is False
    with pytest.raises(RuntimeError, match="not supported"):
        load_constraint_set("unknown_set")


def test_policy_loader_fails_closed_on_missing_reserve_margin(tmp_path: Path) -> None:
    payload = json.loads(POLICY_SET_PATH.read_text("utf-8"))
    del payload["reserve_margin"]
    path = tmp_path / "policy_test_v1.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(RuntimeError, match="missing required block: reserve_margin"):
        load_policy_set(path)


def test_policy_loader_rejects_policy_grade_while_pending(tmp_path: Path) -> None:
    payload = json.loads(POLICY_SET_PATH.read_text("utf-8"))
    payload["status"] = "policy_grade"
    payload["policy_grade"] = True
    payload["structural_gaps"] = []
    path = tmp_path / "policy_test_v1.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(RuntimeError, match="not allowed while any required block"):
        load_policy_set(path)


def test_policy_loader_rejects_capacity_cap_below_residual_value(tmp_path: Path) -> None:
    """The preflight helper is the last line of defence before the solve."""

    params = tmp_path / "params"
    params.mkdir()
    (params / "ResidualCapacity.csv").write_text(
        "REGION,TECHNOLOGY,YEAR,VALUE\nCAN,PWRWNDA1,2021,2.0\n", encoding="utf-8"
    )
    (params / "TotalAnnualMaxCapacity.csv").write_text(
        "REGION,TECHNOLOGY,YEAR,VALUE\nCAN,PWRWNDA1,2021,1.5\n", encoding="utf-8"
    )
    (params / "TotalAnnualMaxCapacityInvestment.csv").write_text(
        "REGION,TECHNOLOGY,YEAR,VALUE\n", encoding="utf-8"
    )
    (params / "TotalTechnologyAnnualActivityUpperLimit.csv").write_text(
        "REGION,TECHNOLOGY,YEAR,VALUE\n", encoding="utf-8"
    )
    failures = _preflight_policy_parameters(
        params, {"PWRWNDA1": {2021: 2.0}}
    )
    assert failures
    assert "below residual" in failures[0]


@pytest.mark.skipif(not MODEL_AVAILABLE, reason="disposable OSeCAN model copy is unavailable")
def test_policy_dry_run_generates_udc_and_caps(tmp_path: Path) -> None:
    scenario_path = tmp_path / "scenario.json"
    scenario_path.write_text(
        json.dumps(policy_scenario_payload()), encoding="utf-8"
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
    assert summary["constraint_status"] == "draft"
    assert summary["screening_only"] is False
    assert summary["policy_grade"] is False
    assert summary["harmonize"]["exit_code"] == 0
    assert "HARMONIZED" in Path(summary["harmonize"]["log"]).read_text("utf-8")

    params = output_dir / "run/params"
    udc_set = (output_dir / "model/SETs/UDC.csv").read_text("utf-8")
    assert "RM_NATIONAL" in udc_set

    capacity_udc = _rows(params / "UDCMultiplierTotalCapacity.csv")
    activity_udc = _rows(params / "UDCMultiplierActivity.csv")
    assert len(capacity_udc) == 6500
    assert len(activity_udc) == 650
    assert {row["UDC"] for row in capacity_udc} == {"RM_NATIONAL"}
    assert {row["UDC"] for row in activity_udc} == {"RM_NATIONAL"}
    assert all(float(row["VALUE"]) < 0 for row in capacity_udc)
    assert all(float(row["VALUE"]) > 0 for row in activity_udc)
    assert any(row["TECHNOLOGY"] == "PWRWNDA1" for row in capacity_udc)
    assert any(row["TECHNOLOGY"] == "PWRTRNA1" for row in activity_udc)
    # RM=1.15, peak_factor=1.25, PWRTRN gross-up=1.11.
    udc_2026 = next(
        float(row["VALUE"])
        for row in activity_udc
        if row["TECHNOLOGY"] == "PWRTRNA1" and row["YEAR"] == "2026"
    )
    assert udc_2026 == pytest.approx(1.15 * 1.25 * 1.11)

    assert len(_rows(params / "ReserveMargin.csv")) == 50
    assert len(_rows(params / "ReserveMarginTagFuel.csv")) == 650
    assert len(_rows(params / "ReserveMarginTagTechnology.csv")) == 6500
    assert len(_rows(params / "UDCConstant.csv")) == 50
    assert _rows(params / "UDCTag.csv")[0]["VALUE"] == "0"

    investment = _rows(params / "TotalAnnualMaxCapacityInvestment.csv")
    wind_caps = {
        (row["TECHNOLOGY"], int(row["YEAR"])): float(row["VALUE"])
        for row in investment
        if row["TECHNOLOGY"].startswith("PWRWND")
    }
    assert wind_caps[("PWRWNDA1", 2030)] == pytest.approx(2.0)
    assert wind_caps[("PWRWNDA1", 2031)] == pytest.approx(3.0)
    assert wind_caps[("PWRWNDA1", 2070)] == pytest.approx(2.0)
    solar_caps = {
        (row["TECHNOLOGY"], int(row["YEAR"])): float(row["VALUE"])
        for row in investment
        if row["TECHNOLOGY"].startswith("PWRSOL")
    }
    assert solar_caps[("PWRSOLA1", 2031)] == pytest.approx(0.5)

    activity = {
        (row["TECHNOLOGY"], int(row["YEAR"])): float(row["VALUE"])
        for row in _rows(params / "TotalTechnologyAnnualActivityUpperLimit.csv")
    }
    assert activity[("PWRWNDA1", 2050)] == pytest.approx(26.882148, abs=1e-6)
    assert activity[("PWRBIOA1", 2050)] == pytest.approx(16.657653, abs=1e-6)
    assert activity[("PWRHYDA1", 2050)] == pytest.approx(197.204587, abs=1e-6)
    assert ("PWRBCWB1", 2050) in activity
    assert ("PWRBCWB2", 2050) in activity

    residual = {
        (row["TECHNOLOGY"], int(row["YEAR"])): float(row["VALUE"])
        for row in _rows(params / "ResidualCapacity.csv")
    }
    for row in _rows(params / "TotalAnnualMaxCapacity.csv"):
        key = (row["TECHNOLOGY"], int(row["YEAR"]))
        assert float(row["VALUE"]) + 1e-9 >= residual.get(key, 0.0)

    policy_evidence = json.loads(
        (output_dir / "policy_parameters.json").read_text("utf-8")
    )
    assert policy_evidence["policy"]["reserve_margin_udc"]["udc"] == "RM_NATIONAL"
    assert policy_evidence["policy"]["nuclear_mode"] == "build_rate_only"
