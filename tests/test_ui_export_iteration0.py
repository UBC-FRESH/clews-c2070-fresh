"""Contract tests for the iteration-0 static UI data bundle."""

from __future__ import annotations

import csv
import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from workflows.ui import export_iteration0 as exporter

BUNDLE = ROOT / "ui/public/data"


@pytest.fixture(scope="module")
def scenarios() -> dict:
    return json.loads((BUNDLE / "scenarios.json").read_text())


def _load_output(scenario_id: str) -> dict:
    return json.loads((BUNDLE / "outputs" / f"{scenario_id}.json").read_text())


def test_normalize_status_shapes_and_unknown() -> None:
    assert exporter.normalize_status("Optimal", label="x") == "optimal"
    assert exporter.normalize_status({"status": "optimal"}, label="x") == "optimal"
    assert exporter.normalize_status("timeout", label="x") == "not_solved"
    with pytest.raises(exporter.ExportError, match="unknown solver status"):
        exporter.normalize_status("mystery", label="x")


def test_normalize_metrics_fallbacks() -> None:
    entry = {
        "objective": 10.5,
        "emissions_mt": {"total": 2.5, "2050": 0.25, "by_year": {"2050": 0.25}},
        "national_bcw_supply_pj": {"2050": 12.0},
        "pwrbcw_total_pj": {"2050": 3.0},
        "bio_share_2050": 0.05,
        "status": {"status": "optimal"},
    }
    metrics = exporter.normalize_metrics(entry, label="fixture")
    assert metrics["objective"] == pytest.approx(10.5)
    assert metrics["emissions_total"] == pytest.approx(2.5)
    assert metrics["emissions_2050"] == pytest.approx(0.25)
    assert metrics["national_bcw_2050"] == pytest.approx(12.0)
    assert metrics["pwrbcw_2050"] == pytest.approx(3.0)
    assert metrics["bio_share_2050"] == pytest.approx(0.05)


def test_normalize_metrics_requires_objective() -> None:
    with pytest.raises(exporter.ExportError, match="objective"):
        exporter.normalize_metrics(
            {"emissions_mt": {"total": 1.0, "2050": 0.0}}, label="fixture"
        )


def test_scenario_contract_and_metrics(scenarios: dict) -> None:
    assert scenarios["schema_version"] == exporter.SCHEMA_VERSION
    assert len(scenarios["scenarios"]) == 47
    assert len({item["scenario_id"] for item in scenarios["scenarios"]}) == 47
    for scenario in scenarios["scenarios"]:
        for field in ("objective", "emissions_total", "emissions_2050"):
            assert scenario["metrics"][field] is not None, scenario["scenario_id"]
        assert scenario["flags"]["solver"] in set(exporter.SOLVER_STATUSES.values())
        assert isinstance(scenario["sources"], list) and scenario["sources"]


def test_superseded_filtering(scenarios: dict) -> None:
    by_id = {item["scenario_id"]: item for item in scenarios["scenarios"]}
    tradeoff_ids = set(scenarios["tradeoff"]["scenario_ids"])
    assert tradeoff_ids, "tradeoff set must not be empty"
    for scenario_id in tradeoff_ids:
        assert not by_id[scenario_id]["flags"]["superseded_for_emissions"]
        assert by_id[scenario_id]["flags"]["publishable"]
    for family in (
        "national_150m",
        "national_base_119m",
        "national_supply_curve",
        "national_supply_curve_demand_sensitivity",
        "harvest_sensitivity_100_200",
        "zonal",
        "reconcile",
        "reconcile_ffcm",
    ):
        members = [item for item in scenarios["scenarios"] if item["family"] == family]
        assert members, family
        assert all(item["flags"]["superseded_for_emissions"] for item in members)
        assert all(not item["flags"]["publishable"] for item in members)
        assert tradeoff_ids.isdisjoint(item["scenario_id"] for item in members)
    assert len(scenarios["tradeoff"]["metrics"]) == len(tradeoff_ids)


def test_unit_separation_across_categories(scenarios: dict) -> None:
    for scenario in scenarios["scenarios"]:
        output = _load_output(scenario["scenario_id"])
        assert set(output["category"]) <= set(exporter.CATEGORY_UNITS)
        for category in set(output["category"]):
            assert output["units_by_category"][category] == exporter.CATEGORY_UNITS[category]
        assert set(output["units_by_category"].values()) <= {"PJ electricity", "PJ fuel"}

    full = _load_output("full_constraint__fullconstraint_t3t4zero")
    assert full["units_by_category"]["dispatch"] == "PJ electricity"
    assert full["units_by_category"]["supply"] == "PJ fuel"
    assert len(set(full["units_by_category"].values())) == 2


def test_export_row_counts_match_dispatch_csv(scenarios: dict) -> None:
    by_id = {item["scenario_id"]: item for item in scenarios["scenarios"]}
    for scenario_id in (
        "emission_factor_fix__efix_baseline_119m",
        "pwrbio_tight__pwrbio_cap3572_tier200",
        "harvest_sensitivity_100_200__100m",
        "national_supply_curve_demand_sensitivity__carbon_400",
    ):
        scenario = by_id[scenario_id]
        output = _load_output(scenario_id)
        dispatch_path = ROOT / scenario["sources"][2]
        with dispatch_path.open(newline="") as handle:
            expected = sum(
                1
                for row in csv.DictReader(handle)
                if row["run"] == scenario["dispatch_run"]
            )
        assert output["row_count"] == expected
        assert len(output["value_pj"]) == expected
        assert len(output["technology"]) == expected
        assert len(output["category"]) == expected
        assert len(output["zone"]) == expected
        assert len(output["year"]) == expected
        assert set(output["year"]) == set(range(2021, 2071))


def test_spatial_join_and_zone_gaps() -> None:
    tiles = json.loads((BUNDLE / "spatial/rsyc_tiles.geojson").read_text())
    assert len(tiles["features"]) == 385
    properties = [feature["properties"] for feature in tiles["features"]]
    assert all(prop["recoverable_pj_base"] is not None for prop in properties)
    assert sum(prop["recoverable_pj_base"] for prop in properties) > 0
    assert len({prop["tile_id"] for prop in properties}) == 385

    zones = json.loads((BUNDLE / "spatial/zone_nodes.json").read_text())
    assert zones["zone_count"] == 13
    assert zones["anchor_count"] == 2
    assert zones["gap_count"] == 11
    assert {zone["zone_id"] for zone in zones["zones"] if zone["anchor"]} == {"B1", "B2"}
    assert all(zone["gap"] and not zone["anchor"] for zone in zones["zones"] if zone["zone_id"] not in {"B1", "B2"})

    provinces = json.loads((BUNDLE / "spatial/provinces.geojson").read_text())
    ecoprovinces = json.loads((BUNDLE / "spatial/ecoprovinces.geojson").read_text())
    assert len(provinces["features"]) == 13
    assert len(ecoprovinces["features"]) == 36


def test_manifest_sha256() -> None:
    manifest = json.loads((BUNDLE / "manifest.json").read_text())
    assert manifest["validation"]["ok"] is True
    assert manifest["scenario_count"] == 47
    assert manifest["total_bytes"] == sum(item["bytes"] for item in manifest["files"])
    for item in manifest["files"]:
        path = BUNDLE / item["path"]
        assert path.stat().st_size == item["bytes"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"]
    assert manifest["spatial"]["total_bytes"] < 5 * 1024 * 1024


def test_inputs_contract() -> None:
    harvest = json.loads((BUNDLE / "inputs/harvest_basis.json").read_text())
    assert harvest["template"]["rows"] == 750
    assert len(harvest["reference_paths"]["years"]) == 50
    supply = json.loads((BUNDLE / "inputs/supply_curve.json").read_text())
    assert supply["tier_sum_pj"] == pytest.approx(213.538041)
    assert supply["reconciliation"]["all_tier_sum_equals_rest"] is True
    assert len(supply["reconciliation"]["tier_sum_pj"]) == 50
    landbase = json.loads((BUNDLE / "inputs/managed_landbase.json").read_text())
    assert landbase["rows"]["by_province"] == 42
    links = json.loads((BUNDLE / "inputs/osecan_links.json").read_text())
    assert len(links["families"]) == 15
    assert links["validation"]["national_cap_reconciliation"]["all_tier_sum_equals_rest"] is True
