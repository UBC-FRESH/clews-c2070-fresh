"""Regression tests for the assignment-v5 facility residue supply v2."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd
import pytest

from workflows.national.facilities import facility_residue_supply as supply_v1
from workflows.national.facilities import facility_residue_supply_v2 as supply


def _read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, keep_default_na=False, dtype=str)


def _numeric(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    for column in columns:
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    return frame


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> tuple[dict, Path]:
    target = tmp_path_factory.mktemp("facility_residue_supply_v2")
    summary = supply.build(output_dir=target)
    return summary, target


@pytest.fixture(scope="module")
def facility_rows(built) -> pd.DataFrame:
    _, target = built
    return _numeric(
        _read_csv(target / "facility_residue_supply.csv"),
        [
            "assigned_m3",
            "capacity_m3_y",
            "assignment_utilization_v5",
            "feedstock_m3",
            "product_quantity",
            "residue_odt",
            "residue_pj",
            "facility_process_residue_odt",
            "facility_share_of_process_residue",
        ],
    )


@pytest.fixture(scope="module")
def province_rows(built) -> pd.DataFrame:
    _, target = built
    return _numeric(
        _read_csv(target / "province_residue_supply.csv"),
        [
            "residue_odt",
            "residue_pj",
            "facility_count",
            "facility_count_positive_residue",
            "published_residue_share",
            "proxy_residue_share",
            "scaled_proxy_residue_share",
            "hhi_facility_residue",
        ],
    )


def test_build_writes_deliverables_and_labels(built):
    summary, target = built
    assert summary["artifact"] == "facility_residue_supply_v2"
    assert summary["status"] == "pass"
    assert summary["scenario"] == "published_plus_proxy"
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
        "FACILITY_RESIDUE_SUPPLY_V2.md",
        "manifest.json",
    ):
        assert (target / name).is_file(), name
    manifest = json.loads((target / "manifest.json").read_text())
    assert manifest["artifact"] == "facility_residue_supply_v2"
    assert manifest["labels"]["ui_or_public_osecan_changed"] is False


def test_class_matching_and_assignment_basis(built, facility_rows):
    assert (facility_rows["feedstock_class"] == facility_rows["facility_class"]).all()
    assert facility_rows["facility_class"].isin(supply.CLASS_ORDER).all()
    assert (facility_rows["allocation_basis"] == supply.ALLOCATION_BASIS).all()
    assert facility_rows["assignment_utilization_v5"].between(0.0, 1.0).all()
    per_facility = facility_rows.groupby("facility_id")["facility_class"].nunique()
    assert (per_facility == 1).all()


def test_province_conservation(built, facility_rows, province_rows):
    summary, _ = built
    process = province_rows[province_rows["residue_type"] == "process_residue"]
    total = process["residue_odt"].sum()
    assert math.isclose(
        total,
        summary["national_totals"]["process_residue_odt"],
        rel_tol=1e-9,
    )
    assert math.isclose(
        facility_rows["residue_odt"].sum(),
        total,
        rel_tol=1e-9,
    )
    assert math.isclose(
        process["residue_pj"].sum(),
        summary["national_totals"]["process_residue_pj"],
        rel_tol=1e-9,
    )
    recoverable = province_rows[
        province_rows["residue_type"] == "harvest_residue_recoverable"
    ]
    assert math.isclose(
        recoverable["residue_odt"].sum(),
        summary["national_totals"]["harvest_residue_recoverable_odt"],
        rel_tol=1e-9,
    )


def test_v1_to_v2_total_is_conserved_and_distribution_changes(built):
    summary, _ = built
    v1 = json.loads(
        (supply_v1.OUTPUT_DIR.parent / "facility_residue_supply_v1" / "summary.json")
        .read_text()
    )
    assert math.isclose(
        summary["national_totals"]["process_residue_odt"],
        v1["national_totals"]["process_residue_odt"],
        rel_tol=1e-9,
    )
    assert summary["concentration"]["facilities_used"] > v1["concentration"][
        "facilities_used"
    ]
    assert summary["concentration"]["hhi"] < v1["concentration"]["hhi"]
    assert summary["concentration"]["proxy_residue_share"] < 0.5


def test_proxy_share_and_labels(built, province_rows):
    summary, _ = built
    process = province_rows[province_rows["residue_type"] == "process_residue"]
    weighted = (
        process["proxy_residue_share"] * process["residue_odt"]
    ).sum() / process["residue_odt"].sum()
    assert math.isclose(
        weighted,
        summary["concentration"]["proxy_residue_share"],
        rel_tol=1e-9,
    )
    for column in ("model_estimate", "observed", "policy_grade"):
        expected = "true" if column == "model_estimate" else "false"
        assert (province_rows[column].astype(str).str.lower() == expected).all()
    scaled = process["scaled_proxy_residue_share"].sum()
    assert scaled == pytest.approx(0.0, abs=1e-12)
