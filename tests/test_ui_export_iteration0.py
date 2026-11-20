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
    assert len(scenarios["scenarios"]) == 51
    assert len({item["scenario_id"] for item in scenarios["scenarios"]}) == 51
    assert len(scenarios["families"]) == 17
    for scenario in scenarios["scenarios"]:
        for field in ("objective", "emissions_total", "emissions_2050"):
            assert scenario["metrics"][field] is not None, scenario["scenario_id"]
        assert scenario["flags"]["solver"] in set(exporter.SOLVER_STATUSES.values())
        assert isinstance(scenario["sources"], list) and scenario["sources"]
        assert isinstance(scenario["flags"]["fleet_corrected"], bool)
        assert isinstance(scenario["flags"]["superseded_for_fleet"], bool)
        assert isinstance(scenario["flags"]["canonical"], bool)
        assert isinstance(scenario["flags"]["screening_only"], bool)
        assert isinstance(scenario["flags"]["policy_grade"], bool)
        assert isinstance(scenario["flags"]["harvest_exogenous"], bool)
        assert isinstance(scenario["flags"]["tradeoff_eligible"], bool)
        assert isinstance(scenario["flags"]["pareto_eligible"], bool)
        assert scenario["feasibility_status"] in {"pass", "conditional", "fail"}
        assert isinstance(scenario["feasibility_notes"], list) and scenario["feasibility_notes"]
        assert isinstance(scenario["pareto_eligible"], bool)
        assert scenario["flags"]["feasibility_status"] == scenario["feasibility_status"]
        assert scenario["flags"]["pareto_eligible"] == scenario["pareto_eligible"]
        assert scenario["metrics"]["harvest_m3_per_year"] == pytest.approx(
            scenario["dimensions"]["harvest_m3_per_year"]
        )


def test_fleet_corrected_canonical_scenario(scenarios: dict) -> None:
    by_id = {item["scenario_id"]: item for item in scenarios["scenarios"]}
    fleet_id = "fleet_capacity__screening_119m_fleet"
    fleet = by_id[fleet_id]
    assert fleet["family"] == "fleet_capacity"
    assert fleet["metrics"]["objective"] == pytest.approx(1_445_655.44)
    assert fleet["metrics"]["emissions_total"] == pytest.approx(424.710127)
    assert fleet["metrics"]["emissions_2050"] == pytest.approx(0.0)
    assert fleet["metrics"]["pwrbcw_2050"] == pytest.approx(41.108218)
    assert fleet["feasibility_status"] == "pass"
    assert fleet["pareto_eligible"] is True
    assert fleet["harvest_exogenous"] is True
    assert fleet["flags"] == {
        "solver": "optimal",
        "publishable": True,
        "superseded_for_emissions": False,
        "superseded_for_fleet": False,
        "fleet_corrected": True,
        "canonical": True,
        "screening_only": True,
        "policy_grade": False,
        "harvest_exogenous": True,
        "feasibility_status": "pass",
        "pareto_eligible": True,
        "tradeoff_eligible": True,
        "diagnostic": False,
        "post_fix_emissions": True,
    }
    assert scenarios["tradeoff"]["canonical_scenario_id"] == fleet_id
    assert scenarios["tradeoff"]["canonical_role"] == "canonical/base"
    assert scenarios["tradeoff"]["scenario_ids"] == [fleet_id]
    assert scenarios["tradeoff"]["sensitivity_scenario_ids"] == [
        "fleet_screening_ladder__screening_100m",
        "fleet_screening_ladder__screening_150m",
        "fleet_screening_ladder__screening_200m",
    ]
    assert [item["scenario_id"] for item in scenarios["tradeoff"]["metrics"]] == scenarios[
        "tradeoff"
    ]["scenario_ids"]
    assert scenarios["tradeoff"]["harvest_exogenous_caveat"] == exporter.HARVEST_EXOGENOUS_CAVEAT
    assert "200 Mm3" in scenarios["tradeoff"]["feasibility_caveat"]
    assert "150 Mm3" in scenarios["tradeoff"]["feasibility_caveat"]

    ladder_ids = {
        "fleet_screening_ladder__screening_100m",
        "fleet_screening_ladder__screening_150m",
        "fleet_screening_ladder__screening_200m",
    }
    for scenario in scenarios["scenarios"]:
        if scenario["scenario_id"] == fleet_id or scenario["scenario_id"] in ladder_ids:
            continue
        assert scenario["flags"]["superseded_for_fleet"] is True, scenario["scenario_id"]
        assert scenario["flags"]["fleet_corrected"] is False, scenario["scenario_id"]
        assert scenario["flags"]["canonical"] is False, scenario["scenario_id"]
        assert scenario["flags"]["publishable"] is False, scenario["scenario_id"]
        assert scenario["flags"]["tradeoff_eligible"] is False, scenario["scenario_id"]


def test_fleet_harvest_ladder_contract(scenarios: dict) -> None:
    by_id = {item["scenario_id"]: item for item in scenarios["scenarios"]}
    expected = {
        "fleet_screening_ladder__screening_100m": (
            100_000_000,
            1_447_160.18,
            423.050600,
            "pass",
        ),
        "fleet_screening_ladder__screening_150m": (
            150_000_000,
            1_443_635.15,
            422.690612,
            "conditional",
        ),
        "fleet_screening_ladder__screening_200m": (
            200_000_000,
            1_440_837.42,
            421.029794,
            "fail",
        ),
    }
    for scenario_id, (harvest_m3, objective, emissions, status) in expected.items():
        scenario = by_id[scenario_id]
        assert scenario["family"] == "fleet_screening_ladder"
        assert "sensitivity" in scenario["label"].lower()
        assert scenario["dimensions"]["harvest_m3_per_year"] == pytest.approx(harvest_m3)
        assert scenario["metrics"]["harvest_m3_per_year"] == pytest.approx(harvest_m3)
        assert scenario["dimensions"]["harvest"]["m3_per_year"] == pytest.approx(harvest_m3)
        assert scenario["metrics"]["objective"] == pytest.approx(objective)
        assert scenario["metrics"]["emissions_total"] == pytest.approx(emissions)
        assert scenario["metrics"]["emissions_2050"] == pytest.approx(0.0)
        assert scenario["feasibility_status"] == status
        assert scenario["pareto_eligible"] is False
        assert scenario["harvest_exogenous"] is True
        assert scenario["flags"]["harvest_exogenous"] is True
        assert scenario["flags"]["feasibility_status"] == status
        assert scenario["flags"]["pareto_eligible"] is False
        assert scenario["flags"]["tradeoff_eligible"] is False
        assert scenario["flags"]["publishable"] is True
        assert scenario["flags"]["diagnostic"] is (status == "fail")
    ladder_family = next(
        item for item in scenarios["families"] if item["family"] == "fleet_screening_ladder"
    )
    assert ladder_family["scenario_count"] == 3
    assert ladder_family["fleet_corrected"] is True
    assert ladder_family["screening_only"] is True
    assert ladder_family["policy_grade"] is False
    assert ladder_family["harvest_exogenous"] is True
    assert ladder_family["pareto_eligible"] is False
    assert ladder_family["tradeoff_eligible"] is False
    assert by_id["fleet_capacity__screening_119m_fleet"]["flags"]["canonical"] is True
    assert by_id["fleet_capacity__screening_119m_fleet"]["flags"]["pareto_eligible"] is True


def test_fleet_output_excludes_other_rows(scenarios: dict) -> None:
    scenario = next(
        item
        for item in scenarios["scenarios"]
        if item["scenario_id"] == "fleet_capacity__screening_119m_fleet"
    )
    output = _load_output(scenario["scenario_id"])
    assert output["source_row_count"] == 17192
    assert output["row_count"] == 4215
    assert output["excluded_categories"] == {"other": 12977}
    assert set(output["category"]) == {"dispatch", "supply"}


def test_historical_ladder_levels_keep_feasibility_labels(scenarios: dict) -> None:
    by_id = {item["scenario_id"]: item for item in scenarios["scenarios"]}
    assert by_id["national_150m__baseline"]["feasibility_status"] == "conditional"
    assert by_id["national_150m__baseline"]["pareto_eligible"] is False
    for scenario_id in (
        "national_supply_curve__baseline",
        "national_supply_curve_demand_sensitivity__carbon_400",
        "harvest_sensitivity_100_200__200m",
    ):
        scenario = by_id[scenario_id]
        assert scenario["feasibility_status"] == "fail", scenario_id
        assert scenario["pareto_eligible"] is False
        assert scenario["flags"]["diagnostic"] is True


def test_superseded_filtering(scenarios: dict) -> None:
    by_id = {item["scenario_id"]: item for item in scenarios["scenarios"]}
    tradeoff_ids = set(scenarios["tradeoff"]["scenario_ids"])
    assert tradeoff_ids == {"fleet_capacity__screening_119m_fleet"}
    sensitivity_ids = set(scenarios["tradeoff"]["sensitivity_scenario_ids"])
    assert sensitivity_ids == {
        "fleet_screening_ladder__screening_100m",
        "fleet_screening_ladder__screening_150m",
        "fleet_screening_ladder__screening_200m",
    }
    for scenario_id in tradeoff_ids:
        assert not by_id[scenario_id]["flags"]["superseded_for_emissions"]
        assert not by_id[scenario_id]["flags"]["superseded_for_fleet"]
        assert by_id[scenario_id]["flags"]["fleet_corrected"]
        assert by_id[scenario_id]["flags"]["publishable"]
        assert by_id[scenario_id]["flags"]["tradeoff_eligible"]
        assert by_id[scenario_id]["flags"]["pareto_eligible"]
        assert by_id[scenario_id]["feasibility_status"] == "pass"
    for scenario_id in sensitivity_ids:
        assert by_id[scenario_id]["flags"]["publishable"]
        assert by_id[scenario_id]["flags"]["tradeoff_eligible"] is False
        assert by_id[scenario_id]["flags"]["pareto_eligible"] is False
        assert by_id[scenario_id]["flags"]["harvest_exogenous"] is True
    for family in (
        "emission_factor_fix",
        "pwrbio_cap",
        "pwrbio_tight",
        "nuclear_audit",
        "wind_integration_audit",
        "full_constraint",
        "full_windcap",
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
        assert all(item["flags"]["superseded_for_fleet"] for item in members)
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
    assert manifest["scenario_count"] == 51
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
    assert len(links["families"]) == 17
    fleet_link = next(item for item in links["families"] if item["family"] == "fleet_capacity")
    assert fleet_link["fleet_corrected"] is True
    assert fleet_link["superseded_for_fleet"] is False
    ladder_link = next(
        item for item in links["families"] if item["family"] == "fleet_screening_ladder"
    )
    assert ladder_link["scenario_count"] == 3
    assert len(ladder_link["scenario_dirs"]) == 3
    assert len(ladder_link["files"]) == 9
    assert ladder_link["harvest_exogenous"] is True
    assert ladder_link["pareto_eligible"] is False
    assert ladder_link["tradeoff_eligible"] is False
    assert links["validation"]["national_cap_reconciliation"]["all_tier_sum_equals_rest"] is True
