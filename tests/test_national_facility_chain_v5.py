"""Contract tests for the assignment-v10 national facility chain v5.

Covers the facility residue v8, chain scenarios v8, residue curve v8, OSeCAN
handoff v9 and OSeCAN scenarios v8 contracts: assignment v10 integration,
products/residue invariance, transport cost separation, distance bands,
facility/province distribution change, unchanged OSeCAN tiers and official
OSeCAN hash, manifests, labels and no unexpected ``D``.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA = REPO_ROOT / "data/derived/national/facilities"
FACILITY_DIR = DATA / "facility_residue_supply_v8"
CURVE_DIR = DATA / "national_residue_curve_v8"
CHAIN_DIR = DATA / "national_facility_chain_scenarios_v8"
HANDOFF_DIR = DATA / "forestry_osecan_handoff_v9"
SCENARIO_DIR = DATA / "forestry_osecan_scenarios_v8"
V4_FACILITY_DIR = DATA / "facility_residue_supply_v7"
V7_CHAIN_DIR = DATA / "national_facility_chain_scenarios_v7"
ASSIGNMENT_DIR = DATA / "national_facility_assignment_v10"


def _require(path: Path) -> Path:
    if not path.is_file():
        pytest.skip(f"artifact not generated: {path}")
    return path


def _frame(directory: Path, name: str) -> pd.DataFrame:
    return pd.read_csv(_require(directory / name))


def _summary(directory: Path) -> dict:
    return json.loads(_require(directory / "summary.json").read_text(encoding="utf-8"))


def _verify_manifest(directory: Path) -> None:
    manifest = json.loads(
        _require(directory / "manifest.json").read_text(encoding="utf-8")
    )
    for name, entry in manifest["outputs"].items():
        path = directory / name
        assert path.is_file(), name
        assert path.stat().st_size == entry["bytes"], name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"], name


@pytest.fixture(scope="module")
def facility_summary() -> dict:
    return _summary(FACILITY_DIR)


@pytest.fixture(scope="module")
def chain_summary() -> dict:
    return _summary(CHAIN_DIR)


def test_manifests_and_labels(facility_summary, chain_summary) -> None:
    for directory in (
        FACILITY_DIR,
        CURVE_DIR,
        CHAIN_DIR,
        HANDOFF_DIR,
        SCENARIO_DIR,
        ASSIGNMENT_DIR,
    ):
        _verify_manifest(directory)
    assert facility_summary["artifact"] == "facility_residue_supply_v8"
    assert facility_summary["scenario"] == "node_transport_aware"
    assert facility_summary["validation"]["failed_check_count"] == 0
    assert chain_summary["artifact"] == "national_facility_chain_scenarios_v8"
    assert chain_summary["validation"]["failed_check_count"] == 0
    for summary in (
        facility_summary,
        chain_summary,
        _summary(CURVE_DIR),
        _summary(HANDOFF_DIR),
        _summary(SCENARIO_DIR),
    ):
        labels = summary["labels"]
        assert labels["model_estimate"] is True
        assert labels["observed"] is False
        assert labels["policy_grade"] is False
        assert labels["national_claim"] is False
        assert labels["official_osecan_changed"] is False


def test_products_and_residue_invariant(
    facility_summary: dict, chain_summary: dict
) -> None:
    v4_facility = _summary(V4_FACILITY_DIR)
    for key in (
        "process_residue_odt",
        "process_residue_pj",
        "harvest_residue_gross_pj",
        "harvest_residue_recoverable_pj",
        "total_feedstock_m3",
    ):
        assert facility_summary["national_totals"][key] == pytest.approx(
            v4_facility["national_totals"][key], rel=1e-12, abs=1e-6
        )
    v7_chain = _summary(V7_CHAIN_DIR)
    v5 = chain_summary["scenarios"]["119m"]
    v4 = v7_chain["scenarios"]["119m"]
    for key in (
        "sawlog_product_m3",
        "panel_product_m3",
        "pulp_product_odt",
        "pellet_product_odt",
        "bio_product_mwh",
        "process_residue_pj",
        "harvest_residue_gross_pj",
        "harvest_residue_recoverable_pj",
        "total_residue_gross_pj",
        "total_residue_recoverable_pj",
    ):
        assert v5[key] == pytest.approx(v4[key], rel=1e-12, abs=1e-6)
    comparison = _frame(CHAIN_DIR, "v4_v5_chain_comparison.csv")
    product_rows = comparison[comparison["scope"] == "national_product_residue"]
    assert len(product_rows) == 10
    assert (product_rows["delta"].astype(float).abs() < 1e-6).all()


def test_assignment_v10_integration(chain_summary: dict) -> None:
    assignment = _frame(CHAIN_DIR, "scenario_assignment.csv")
    rows = assignment[assignment["scenario"] == "119m"]
    assert len(rows) == 319
    assert rows["assigned_m3"].sum() == pytest.approx(118_977_339.1, abs=1e-3)
    assert rows["facility_id"].nunique() == 296
    cross_border_rows = rows[
        rows["cross_border"].astype(str).str.lower().eq("true")
    ]
    assert len(cross_border_rows) == 25
    assert rows["assigned_m3"].sum() == pytest.approx(
        float(chain_summary["assignment"]["v10_primary"]["assigned_m3"]),
        abs=1e-3,
    )
    scenario = chain_summary["scenarios"]["119m"]
    assert scenario["facilities_used"] == 296
    assert scenario["cross_border_m3"] == pytest.approx(
        2_325_349.7654, abs=1e-3
    )
    assert scenario["local_capacity_binding_provinces"] == [
        "BC",
        "MB",
        "NL",
        "NT",
        "ON",
        "QC",
    ]


def test_transport_cost_and_distance_bands() -> None:
    summary = _summary(FACILITY_DIR)
    transport = summary["transport"]
    v4 = transport["v4"]
    v5 = transport["v5"]
    assert v5["transport_cost_cad"] < v4["transport_cost_cad"]
    assert v5["transport_cost_cad"] == pytest.approx(3_392_104_138.709, rel=1e-9)
    assert v4["transport_cost_cad"] == pytest.approx(4_958_493_704.09, rel=1e-9)
    assert v5["mean_road_distance_km"] < v4["mean_road_distance_km"]
    assert v5["share_ge_150_km"] < v4["share_ge_150_km"]
    assert v5["share_ge_300_km"] < v4["share_ge_300_km"]
    bands = _frame(FACILITY_DIR, "transport_cost_summary.csv")
    band_rows = bands[bands["scope"] == "distance_band"]
    assert set(band_rows["scope_id"]) == {
        "le_50_km",
        "gt_50_le_100_km",
        "gt_100_le_150_km",
        "gt_150_le_300_km",
        "gt_300_km",
    }
    for scenario in ("v4_centroid_local_first", "v5_node_transport_aware"):
        shares = band_rows[
            (band_rows["scenario"] == scenario)
            & (band_rows["metric"] == "share")
        ]["value"].astype(float)
        assert shares.sum() == pytest.approx(1.0, abs=1e-6)
    facility_transport = _frame(FACILITY_DIR, "facility_transport.csv")
    assert set(facility_transport["record_type"]) == {
        "facility_total",
        "origin_facility",
    }
    origin = facility_transport[
        facility_transport["record_type"] == "origin_facility"
    ]
    assert origin["assigned_m3"].astype(float).sum() == pytest.approx(
        118_977_339.1, abs=1e-3
    )


def test_facility_and_province_distribution_change() -> None:
    comparison = _frame(FACILITY_DIR, "v4_v5_facility_comparison.csv").set_index(
        "metric"
    )
    assert (
        comparison.loc["facilities_with_positive_residue", "v5_value"]
        > comparison.loc["facilities_with_positive_residue", "v4_value"]
    )
    assert (
        comparison.loc["cross_border_residue_share", "v5_value"]
        > comparison.loc["cross_border_residue_share", "v4_value"]
    )
    facility_distribution = _frame(
        FACILITY_DIR, "v4_v5_facility_distribution.csv"
    )
    assert (facility_distribution["delta_assigned_m3"].astype(float) != 0).any()
    provinces = _frame(FACILITY_DIR, "v4_v5_province_comparison.csv")
    assert len(provinces) == 13
    assert (provinces["delta_assigned_m3"].astype(float) != 0).any()


def test_osecan_tiers_unchanged_and_zone_shift() -> None:
    handoff = _summary(HANDOFF_DIR)
    assert handoff["artifact"] == "forestry_osecan_handoff_v9"
    assert handoff["status"] == "pass"
    assert handoff["validation"]["failed_check_count"] == 0
    integrity = handoff["osecan_integrity"]
    assert integrity["unchanged"] is True
    assert integrity["changed_files"] == []
    assert integrity["file_count_before"] == 146
    assert integrity["tree_sha256_before"] == (
        "80caa91c3e91c89006b8d5e3ad78232eade70a298a83657510fffa26924bc64a"
    )
    tiers = _frame(HANDOFF_DIR, "tier_positions_v4_v5.csv")
    assert len(tiers) == 7
    assert not tiers["tier_changed"].astype(str).str.lower().eq("true").any()
    zones = _frame(HANDOFF_DIR, "v4_v5_zone_comparison.csv")
    process = zones[zones["stream"] == "process"]
    assert process["delta_pj"].astype(float).abs().max() > 1.0
    assert process["delta_pj"].astype(float).sum() == pytest.approx(0.0, abs=1e-6)
    harvest = zones[zones["stream"] == "harvest"]
    assert harvest["delta_pj"].astype(float).abs().max() == pytest.approx(
        0.0, abs=1e-9
    )


def test_osecan_scenarios_and_no_transport_cost() -> None:
    summary = _summary(SCENARIO_DIR)
    assert summary["artifact"] == "forestry_osecan_scenarios_v8"
    assert summary["status"] == "pass"
    assert summary["validation"]["failed_check_count"] == 0
    assert summary["scenario_order"] == ["100m", "119m", "150m"]
    assert summary["transport_cost_in_osecan"] is False
    comparison = summary["v4_v5_tier_comparison"]
    assert len(comparison) == 21
    assert not any(
        row["tier_changed"] == "true" for row in comparison
    )
    for row in comparison:
        assert float(row["delta_pj"]) == pytest.approx(0.0, abs=1e-6)
    residue = _frame(SCENARIO_DIR, "forestry_residue_scenarios.csv")
    assert residue["zone_id"].nunique() == 13
    assert "UNMAPPED" not in set(residue["zone_id"])
    for column in ("odt", "pj"):
        values = residue[column].astype(float)
        assert values.notna().all()
        assert np.isfinite(values.to_numpy(dtype=float)).all()
        assert (values >= 0.0).all()


def test_no_unexpected_deletions() -> None:
    for repository in (REPO_ROOT, REPO_ROOT / "data"):
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repository,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        for line in status.splitlines():
            assert not line.startswith("D "), line
            assert not line.startswith(" D"), line
