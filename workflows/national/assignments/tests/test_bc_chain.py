"""Tests for the all-BC B1/B2 supply -> Stage A -> Stage B chain."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from workflows.national.assignments.bc_chain import (
    RESIDUE_DENSITY_ODT_M3,
    BcChainError,
    build_bc_supply,
    build_supply_offers,
    check_stage_a_balance,
    check_stage_b_balance,
    check_zone_supply,
)

REPO_ROOT = Path(__file__).resolve().parents[4]


def _supply_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "zone": "B1",
                "year": "2028",
                "aac_m3": 100.0,
                "harvest_area_ha": 1.0,
                "species": "PICE.MAR",
                "product": "sawlog",
                "sort_code": "D125L13",
                "volume_m3": 90.0,
                "residue_m3": 0.0,
                "residue_odt": 0.0,
                "residue_recoverable_odt": 0.0,
                "density_odt_m3": 0.40,
                "roadside_cost_cad_m3": 0.0,
                "delivered_cost_cad_m3": 0.0,
                "evidence_tier": "test",
                "mapped_status": "mapped",
                "excluded_forest_area_ha": 0.0,
            },
            {
                "zone": "B1",
                "year": "2028",
                "aac_m3": 100.0,
                "harvest_area_ha": 1.0,
                "species": "PICE.MAR",
                "product": "other",
                "sort_code": "D152",
                "volume_m3": 10.0,
                "residue_m3": 0.0,
                "residue_odt": 0.0,
                "residue_recoverable_odt": 0.0,
                "density_odt_m3": 0.40,
                "roadside_cost_cad_m3": 0.0,
                "delivered_cost_cad_m3": 0.0,
                "evidence_tier": "test",
                "mapped_status": "mapped",
                "excluded_forest_area_ha": 0.0,
            },
            {
                "zone": "B1",
                "year": "2028",
                "aac_m3": 100.0,
                "harvest_area_ha": 1.0,
                "species": "PICE.MAR",
                "product": "residue",
                "sort_code": "RESIDUE",
                "volume_m3": 20.0,
                "residue_m3": 20.0,
                "residue_odt": 9.0,
                "residue_recoverable_odt": 5.4,
                "density_odt_m3": RESIDUE_DENSITY_ODT_M3,
                "roadside_cost_cad_m3": 0.0,
                "delivered_cost_cad_m3": 0.0,
                "evidence_tier": "test",
                "mapped_status": "mapped",
                "excluded_forest_area_ha": 0.0,
            },
            {
                "zone": "B1",
                "year": "2028",
                "aac_m3": 100.0,
                "harvest_area_ha": 1.0,
                "species": "UNMAPPED_BC",
                "product": "unmapped",
                "sort_code": "UNMAPPED",
                "volume_m3": 0.0,
                "residue_m3": 0.0,
                "residue_odt": 0.0,
                "residue_recoverable_odt": 0.0,
                "density_odt_m3": 0.0,
                "roadside_cost_cad_m3": 0.0,
                "delivered_cost_cad_m3": 0.0,
                "evidence_tier": "unmapped",
                "mapped_status": "excluded_unmapped_area",
                "excluded_forest_area_ha": 50.0,
            },
        ]
    )


def test_zone_supply_conserves_aac() -> None:
    supply = _supply_frame()
    check_zone_supply(supply)
    broken = supply.copy()
    broken.loc[broken["product"] == "sawlog", "volume_m3"] = 89.0
    with pytest.raises(BcChainError, match="does not conserve AAC"):
        check_zone_supply(broken)


def test_supply_offers_preserve_grade_volume_and_residue_units() -> None:
    grades, offers = build_supply_offers(_supply_frame(), year="2028", period="2028")
    volume_by_grade = {}
    for offer in offers:
        volume_by_grade[offer.grade_id] = volume_by_grade.get(offer.grade_id, 0.0) + offer.volume_m3
    assert volume_by_grade["SAWLOG"] == pytest.approx(90.0)
    assert volume_by_grade["OTHER_LOG"] == pytest.approx(10.0)
    # 5.4 recoverable odt / 0.45 odt/m3
    assert volume_by_grade["RESIDUE"] == pytest.approx(12.0)
    residue_grade = next(grade for grade in grades if grade.grade_id == "RESIDUE")
    assert residue_grade.density_odt_m3 == pytest.approx(RESIDUE_DENSITY_ODT_M3)
    assert {"SAWLOG", "PULP", "OTHER_LOG", "RESIDUE", "HOGFUEL"} == {
        grade.grade_id for grade in grades
    }


def test_stage_a_balance_counts_recoverable_residue() -> None:
    ledger = _supply_frame()
    assignments = pd.DataFrame(
        [
            {"zone": "B1", "grade": "SAWLOG", "volume_m3": 90.0},
            {"zone": "B1", "grade": "OTHER_LOG", "volume_m3": 10.0},
            {"zone": "B1", "grade": "RESIDUE", "volume_m3": 12.0},
        ]
    )
    check_stage_a_balance(ledger, assignments, pd.DataFrame())
    broken = assignments.copy()
    broken.loc[broken["grade"] == "RESIDUE", "volume_m3"] = 11.0
    with pytest.raises(BcChainError, match="Stage A mass balance"):
        check_stage_a_balance(ledger, broken, pd.DataFrame())


def test_stage_b_balance() -> None:
    balance = pd.DataFrame(
        [
            {
                "source_facility": "BC-SAW-1",
                "residue_type": "chips",
                "generated_odt": 10.0,
                "assigned_odt": 8.0,
                "unassigned_odt": 2.0,
            }
        ]
    )
    check_stage_b_balance(balance)
    broken = balance.copy()
    broken.loc[0, "assigned_odt"] = 7.0
    with pytest.raises(BcChainError, match="Stage B mass balance"):
        check_stage_b_balance(broken)


def test_real_batch_reports_unmapped_row() -> None:
    supply = build_bc_supply(REPO_ROOT, year="2028")
    unmapped = supply[supply["species"] == "UNMAPPED_BC"]
    assert set(unmapped["zone"]) == {"B1", "B2"}
    assert (unmapped["volume_m3"] == 0.0).all()
    assert unmapped["excluded_forest_area_ha"].sum() > 0.0
    residue = supply[supply["product"] == "residue"]
    assert (residue["residue_recoverable_odt"] <= residue["residue_odt"] + 1e-6).all()
    # Unit consistency: residue green m3 x 0.45 odt/m3 = residue odt.
    assert (
        residue["residue_m3"] * RESIDUE_DENSITY_ODT_M3
        - residue["residue_odt"]
    ).abs().max() < 1e-3
    # Zone aggregation: each zone's product volumes sum to its AAC.
    for zone, group in supply.groupby("zone"):
        logs = group.loc[group["product"].isin(["sawlog", "pulpwood", "other"]), "volume_m3"].sum()
        assert logs == pytest.approx(float(group["aac_m3"].iloc[0]), rel=1e-9)
    check_zone_supply(supply)


def test_stage_a_rerun_outputs_conserve_volume_and_class_mix() -> None:
    out_dir = REPO_ROOT / "data/derived/national/bc_chain"
    summary_path = out_dir / "bc_stage_a_summary.json"
    assignments_path = out_dir / "bc_stage_a_assignment.csv"
    unassigned_path = out_dir / "bc_stage_a_unassigned.csv"
    if not (summary_path.exists() and assignments_path.exists() and unassigned_path.exists()):
        pytest.skip("BC chain Stage A outputs are not materialized")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assignments = pd.read_csv(assignments_path)
    unassigned = pd.read_csv(unassigned_path)
    assigned = float(assignments["volume_m3"].sum())
    unassigned_volume = float(unassigned["volume_m3"].sum())
    assert assigned + unassigned_volume == pytest.approx(summary["total_supply_m3"], rel=1e-6)
    assert summary["assigned_m3"] == pytest.approx(assigned, rel=1e-6)
    assert summary["unassigned_m3"] == pytest.approx(unassigned_volume, rel=1e-6)
    class_assigned = sum(
        float(row["assigned_m3"]) for row in summary["class_utilization"]
    )
    assert class_assigned == pytest.approx(assigned, rel=1e-6)
    assert summary["transport_summary"]["link_count"] > 0
