"""Regression tests for the measured-coefficient facility chain v4."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from workflows.national.facilities import (
    national_facility_chain_scenarios_v3 as donor,
)
from workflows.national.facilities import (
    national_facility_chain_scenarios_v4 as chain,
)


def _read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, keep_default_na=False, dtype=str)


@pytest.fixture(scope="module")
def built() -> tuple[dict, Path]:
    summary = json.loads((chain.OUTPUT_DIR / "summary.json").read_text())
    return summary, chain.OUTPUT_DIR


@pytest.fixture(scope="module")
def product_rows(built) -> pd.DataFrame:
    _, target = built
    return _read_csv(target / "scenario_product_residue.csv")


def test_build_deliverables_and_labels(built, product_rows):
    summary, target = built
    assert summary["artifact"] == "national_facility_chain_scenarios_v4"
    assert summary["status"] == "pass"
    assert summary["validation"]["passed"] is True
    assert summary["capacity_case"] == "province_capacity_floor"
    assert summary["scenario_order"] == ["100m", "119m", "150m"]
    for name in (
        "scenario_assignment.csv",
        "scenario_product_residue.csv",
        "scenario_summary.csv",
        "scenario_comparison.csv",
        "measured_vs_donor_comparison.csv",
        "summary.json",
        "NATIONAL_FACILITY_CHAIN_SCENARIOS_V4.md",
        "manifest.json",
    ):
        assert (target / name).is_file(), name
    assert (product_rows["observed"].str.lower() == "false").all()
    assert (product_rows["policy_grade"].str.lower() == "false").all()
    assert (product_rows["evidence_label"].str.lower() == "model_estimate").all()


def test_measured_and_donor_split(built, product_rows):
    summary, _ = built
    assert summary["coefficient_replacement"]["measured_provinces"] == [
        "BC",
        "NB",
        "QC",
        "YT",
    ]
    assert set(summary["coefficient_replacement"]["donor_provinces"]) == {
        "AB",
        "SK",
        "MB",
        "ON",
        "NS",
        "PE",
        "NL",
        "NT",
        "NU",
    }
    process = product_rows[product_rows["record_type"] == "facility_process"]
    tiers = process.groupby("province")["coefficient_tier"].agg(set)
    for province in ("BC", "QC", "NB", "YT"):
        assert tiers[province] == {"measured_bucking_pilot"}
    for province in summary["coefficient_replacement"]["donor_provinces"]:
        if province == "NU":
            assert province not in tiers
        else:
            assert tiers[province] == {"donor_coefficient"}


def test_119m_matches_measured_facility_residue(built):
    summary, _ = built
    facility = json.loads(
        (
            chain.DATA_ROOT
            / "derived/national/facilities/facility_residue_supply_v4/summary.json"
        ).read_text()
    )
    block = summary["scenarios"]["119m"]
    assert block["process_residue_pj"] == pytest.approx(
        facility["national_totals"]["process_residue_pj"], rel=1e-9
    )
    assert block["harvest_residue_gross_odt"] == pytest.approx(
        facility["national_totals"]["harvest_residue_gross_odt"], rel=1e-9
    )


def test_conservation_and_scenario_ordering(built, product_rows):
    summary, _ = built
    for scenario in ("100m", "119m", "150m"):
        block = summary["scenarios"][scenario]
        assert block["assigned_m3"] + block["unassigned_m3"] == pytest.approx(
            block["harvest_m3"], abs=1.0
        )
        process = product_rows[
            (product_rows["scenario"] == scenario)
            & (product_rows["record_type"] == "facility_process")
        ]
        harvest = product_rows[
            (product_rows["scenario"] == scenario)
            & (product_rows["record_type"] == "harvest_residue")
        ]
        assert pd.to_numeric(process["feedstock_m3"]).sum() == pytest.approx(
            block["facility_feedstock_m3"], rel=1e-9
        )
        assert pd.to_numeric(process["process_residue_odt"]).sum() == pytest.approx(
            block["process_residue_odt"], rel=1e-9
        )
        assert pd.to_numeric(harvest["harvest_residue_gross_odt"]).sum() == pytest.approx(
            block["harvest_residue_gross_odt"], rel=1e-9
        )
    values = [
        summary["scenarios"][scenario]["process_residue_pj"]
        for scenario in ("100m", "119m", "150m")
    ]
    assert values[0] < values[1] < values[2]


def test_comparison_is_coefficient_only(built):
    summary, target = built
    comparison = _read_csv(target / "measured_vs_donor_comparison.csv")
    assert len(comparison) == 3 * (1 + 13) * len(chain.PRODUCT_METRICS)
    donor_summary = json.loads((donor.OUTPUT_DIR / "summary.json").read_text())
    measured = summary["scenarios"]["119m"]
    baseline = donor_summary["scenarios"]["119m"]
    assert measured["assigned_m3"] == pytest.approx(baseline["assigned_m3"])
    assert measured["harvest_m3"] == pytest.approx(baseline["harvest_m3"])
    assert measured["pulp_product_odt"] > baseline["pulp_product_odt"]
    assert measured["sawlog_product_m3"] < baseline["sawlog_product_m3"]
    assert measured["harvest_residue_gross_pj"] < baseline["harvest_residue_gross_pj"]

    national = comparison[
        (comparison["scenario"] == "119m") & (comparison["scope"] == "national")
    ].set_index("metric")
    assert float(national.loc["process_residue_pj", "delta"]) < 0.0
    assert float(national.loc["harvest_residue_gross_pj", "delta"]) < 0.0
    for province in ("AB", "SK", "MB", "ON", "NS", "PE", "NL", "NT", "NU"):
        selected = comparison[
            (comparison["scenario"] == "119m")
            & (comparison["scope"] == "province")
            & (comparison["province"] == province)
        ]
        assert (selected["delta"].astype(float).abs() < 1e-6).all()
