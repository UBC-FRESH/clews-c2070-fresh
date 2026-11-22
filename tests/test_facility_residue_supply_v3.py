"""Regression tests for the assignment-v6 facility residue supply v3."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd
import pytest

from workflows.national.facilities import facility_residue_supply_v2 as supply_v2
from workflows.national.facilities import facility_residue_supply_v3 as supply


def _read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, keep_default_na=False, dtype=str)


@pytest.fixture(scope="module")
def built() -> tuple[dict, Path]:
    summary = json.loads((supply.OUTPUT_DIR / "summary.json").read_text())
    return summary, supply.OUTPUT_DIR


@pytest.fixture(scope="module")
def facility_rows(built) -> pd.DataFrame:
    _, target = built
    frame = _read_csv(target / "facility_residue_supply.csv")
    for column in (
        "assigned_m3",
        "capacity_m3_y",
        "assignment_utilization_v6",
        "feedstock_m3",
        "product_quantity",
        "residue_odt",
        "residue_pj",
        "facility_process_residue_odt",
        "facility_share_of_process_residue",
    ):
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    return frame


@pytest.fixture(scope="module")
def province_rows(built) -> pd.DataFrame:
    _, target = built
    frame = _read_csv(target / "province_residue_supply.csv")
    for column in (
        "residue_odt",
        "residue_pj",
        "facility_count",
        "facility_count_positive_residue",
        "published_residue_share",
        "proxy_residue_share",
        "scaled_proxy_residue_share",
        "hhi_facility_residue",
    ):
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    return frame


def test_build_deliverables_and_labels(built):
    summary, target = built
    assert summary["artifact"] == "facility_residue_supply_v3"
    assert summary["status"] == "pass"
    assert summary["scenario"] == "province_capacity_floor"
    assert summary["model_estimate"] is True
    assert summary["observed"] is False
    assert summary["policy_grade"] is False
    assert summary["ui_or_public_osecan_changed"] is False
    assert summary["validation"]["failed_check_count"] == 0
    for name in (
        "facility_residue_supply.csv",
        "province_residue_supply.csv",
        "national_residue_summary.csv",
        "summary.json",
        "FACILITY_RESIDUE_SUPPLY_V3.md",
        "manifest.json",
    ):
        assert (target / name).is_file(), name


def test_class_matching_and_assignment_basis(built, facility_rows):
    assert (facility_rows["feedstock_class"] == facility_rows["facility_class"]).all()
    assert facility_rows["facility_class"].isin(supply.CLASS_ORDER).all()
    assert (facility_rows["allocation_basis"] == supply.ALLOCATION_BASIS).all()
    assert facility_rows["assignment_utilization_v6"].between(0.0, 1.0).all()
    per_facility = facility_rows.groupby("facility_id")["facility_class"].nunique()
    assert (per_facility == 1).all()


def test_process_total_is_invariant_and_distribution_moves(built, province_rows):
    summary, _ = built
    v2 = json.loads((supply_v2.OUTPUT_DIR / "summary.json").read_text())
    assert math.isclose(
        summary["national_totals"]["process_residue_odt"],
        v2["national_totals"]["process_residue_odt"],
        rel_tol=1e-9,
    )
    process = province_rows[province_rows["residue_type"] == "process_residue"]
    v2_rows = _read_csv(supply_v2.OUTPUT_DIR / "province_residue_supply.csv")
    v2_process = v2_rows[v2_rows["residue_type"] == "process_residue"]
    v2_process = v2_process.assign(
        residue_odt=pd.to_numeric(v2_process["residue_odt"], errors="raise")
    )
    old = dict(zip(v2_process["province"], v2_process["residue_odt"]))
    new = dict(zip(process["province"], process["residue_odt"]))
    assert new["BC"] < old["BC"] - 1_000_000.0
    assert new["AB"] > old["AB"] + 1_000_000.0
    assert math.isclose(
        sum(new.values()),
        sum(old.values()),
        rel_tol=1e-9,
    )


def test_scaled_proxy_residue_is_labelled(built, province_rows):
    summary, _ = built
    assert summary["concentration"]["scaled_proxy_residue_share"] > 0.30
    process = province_rows[province_rows["residue_type"] == "process_residue"]
    weighted = (
        process["proxy_residue_share"] * process["residue_odt"]
    ).sum() / process["residue_odt"].sum()
    assert math.isclose(
        weighted,
        summary["concentration"]["proxy_residue_share"],
        rel_tol=1e-9,
    )
    assert process["scaled_proxy_residue_share"].sum() > 0.30
    for column in ("model_estimate", "observed", "policy_grade"):
        expected = "true" if column == "model_estimate" else "false"
        assert (process[column].astype(str).str.lower() == expected).all()
