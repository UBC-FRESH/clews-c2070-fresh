"""Regression tests for the assignment-v5 multi-scenario chain v2."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd
import pytest

from workflows.national.facilities import national_facility_chain_scenarios_v2 as chain


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> tuple[dict, Path]:
    target = tmp_path_factory.mktemp("national_facility_chain_scenarios_v2")
    summary = chain.build(output_dir=target)
    return summary, target


@pytest.fixture(scope="module")
def product_rows(built) -> pd.DataFrame:
    _, target = built
    return pd.read_csv(target / "scenario_product_residue.csv", dtype=str)


def test_build_deliverables(built):
    summary, target = built
    assert summary["artifact"] == "national_facility_chain_scenarios_v2"
    assert summary["status"] == "pass"
    assert summary["validation"]["passed"] is True
    assert summary["capacity_case"] == "published_plus_proxy"
    for name in (
        "scenario_assignment.csv",
        "scenario_product_residue.csv",
        "scenario_summary.csv",
        "scenario_comparison.csv",
        "summary.json",
        "NATIONAL_FACILITY_CHAIN_SCENARIOS_V2.md",
        "manifest.json",
    ):
        assert (target / name).is_file(), name


def test_three_scenarios_are_fully_assigned(built):
    summary, _ = built
    assert summary["scenario_order"] == ["100m", "119m", "150m"]
    for scenario, target in (
        ("100m", 100_000_000.0),
        ("119m", 118_977_339.1),
        ("150m", 150_000_000.0),
    ):
        block = summary["scenarios"][scenario]
        assert block["assigned_m3"] == pytest.approx(target, abs=1.0)
        assert abs(block["unassigned_m3"]) <= 1e-6
        assert block["proxy_share_of_assigned"] > 0.0
        assert block["process_residue_pj"] > 0.0


def test_119m_matches_facility_residue_v2(built):
    summary, _ = built
    block = summary["scenarios"]["119m"]
    facility = json.loads(
        (
            chain.DATA_ROOT
            / "derived/national/facilities/facility_residue_supply_v2/summary.json"
        ).read_text()
    )
    assert block["process_residue_pj"] == pytest.approx(
        facility["national_totals"]["process_residue_pj"], rel=1e-9
    )
    assert block["process_residue_odt"] == pytest.approx(
        facility["national_totals"]["process_residue_odt"], rel=1e-9
    )


def test_product_residue_labels(product_rows):
    assert (product_rows["evidence_label"].str.lower() == "model_estimate").all()
    assert (product_rows["observed"].str.lower() == "false").all()
    assert (product_rows["policy_grade"].str.lower() == "false").all()
    process = product_rows[product_rows["record_type"] == "facility_process"]
    harvest = product_rows[product_rows["record_type"] == "harvest_residue"]
    assert not process.empty and not harvest.empty
    assert math.isfinite(
        pd.to_numeric(product_rows["process_residue_pj"], errors="raise").sum()
    )
