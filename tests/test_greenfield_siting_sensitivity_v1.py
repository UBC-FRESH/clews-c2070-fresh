"""Regression tests for the transport-only greenfield siting sensitivity v1."""

from __future__ import annotations

import csv
import json
import math
from collections import defaultdict
from pathlib import Path

import pytest

from workflows.national.facilities import (
    greenfield_siting_sensitivity_v1 as siting,
)

OUTPUT_DIR = siting.OUTPUT_DIR
SUMMARY_JSON = OUTPUT_DIR / "summary.json"
MANIFEST_JSON = OUTPUT_DIR / "manifest.json"
TRADEOFF_CSV = OUTPUT_DIR / "k_tradeoff.csv"
MODULE_CSV = OUTPUT_DIR / "module_locations.csv"
K0_CSV = OUTPUT_DIR / "node_assignment_k0.csv"
K20_CSV = OUTPUT_DIR / "node_assignment_k20.csv"
EXISTING_CSV = OUTPUT_DIR / "existing_capacity_service_k0_k20.csv"
REPORT_MD = OUTPUT_DIR / "GREENFIELD_SITING_SENSITIVITY_V1.md"


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


@pytest.fixture(scope="module")
def summary() -> dict:
    with SUMMARY_JSON.open("r", encoding="utf-8") as handle:
        return json.load(handle)


@pytest.fixture(scope="module")
def tradeoff() -> list[dict[str, str]]:
    return _read_csv(TRADEOFF_CSV)


@pytest.fixture(scope="module")
def modules() -> list[dict[str, str]]:
    return _read_csv(MODULE_CSV)


@pytest.fixture(scope="module")
def k0_rows() -> list[dict[str, str]]:
    return _read_csv(K0_CSV)


@pytest.fixture(scope="module")
def k20_rows() -> list[dict[str, str]]:
    return _read_csv(K20_CSV)


@pytest.fixture(scope="module")
def existing_rows() -> list[dict[str, str]]:
    return _read_csv(EXISTING_CSV)


@pytest.fixture(scope="module")
def nodes() -> list[dict[str, str]]:
    return _read_csv(siting.SUPPLY_NODES_CSV)


def test_outputs_exist_fields_manifest_and_labels(summary) -> None:
    for path in (
        SUMMARY_JSON,
        MANIFEST_JSON,
        TRADEOFF_CSV,
        MODULE_CSV,
        K0_CSV,
        K20_CSV,
        EXISTING_CSV,
        REPORT_MD,
    ):
        assert path.is_file(), path
    assert list(_read_csv(TRADEOFF_CSV)[0]) == siting.TRADEOFF_FIELDS
    assert list(_read_csv(MODULE_CSV)[0]) == siting.MODULE_FIELDS
    assert list(_read_csv(K0_CSV)[0]) == siting.NODE_ASSIGNMENT_FIELDS
    assert list(_read_csv(K20_CSV)[0]) == siting.NODE_ASSIGNMENT_FIELDS
    assert list(_read_csv(EXISTING_CSV)[0]) == siting.EXISTING_SERVICE_FIELDS
    manifest = json.loads(MANIFEST_JSON.read_text(encoding="utf-8"))
    assert manifest["artifact"] == "greenfield_siting_sensitivity_v1"
    assert manifest["artifact_version"] == "1.0.0"
    assert manifest["status"] == "complete"
    assert manifest["labels"] == summary["labels"]
    for name, entry in manifest["outputs"].items():
        path = OUTPUT_DIR / name
        assert path.is_file(), name
        assert path.stat().st_size == entry["bytes"], name
        assert siting.v9._sha256(path) == entry["sha256"], name
    for relative, expected_hash in manifest["inputs"].items():
        path = siting.REPO_ROOT / relative
        assert path.is_file(), relative
        assert siting.v9._sha256(path) == expected_hash, relative
    labels = summary["labels"]
    assert labels["sensitivity_label"] == "transport_only_siting_screen"
    assert labels["investment_advice_label"] == "not_investment_advice"
    assert labels["evidence_label"] == "model_estimate"
    assert labels["observed"] is False
    assert labels["policy_grade"] is False
    assert labels["investment_advice"] is False
    assert labels["national_claim"] is False
    assert labels["official_osecan_changed"] is False
    assert labels["ui_changed"] is False
    assert labels["capital_cost_claim"] is False
    assert labels["permitting_claim"] is False
    assert labels["landbase_claim"] is False
    assert labels["class_specific_capacity_claim"] is False
    assert labels["module_class"] == "GENERIC_MODULE"
    assert labels["module_capacity"] == "unlimited_screen"
    assert summary["validation"]["all_passed"] is True
    assert not summary["validation"]["failures"]


def test_labels_on_every_row(
    tradeoff, modules, k0_rows, k20_rows, existing_rows
) -> None:
    all_rows = tradeoff + modules + k0_rows + k20_rows + existing_rows
    assert all_rows
    for row in all_rows:
        assert row["evidence_label"] == "model_estimate"
        assert row["sensitivity_label"] == "transport_only_siting_screen"
        assert row["investment_advice_label"] == "not_investment_advice"
        assert row["observed"] == "false"
        assert row["policy_grade"] == "false"
    for row in modules:
        assert row["module_class"] == "GENERIC_MODULE"
        assert row["class_agnostic"] == "true"
        assert row["capacity_status"] == "unlimited_screen"
        assert row["capacity_m3_y"] == ""


def test_conservation(nodes, tradeoff, k0_rows, k20_rows, summary) -> None:
    assert len(nodes) == 525
    active = [node for node in nodes if float(node["harvest_m3"]) > 0.0]
    assert len(active) == 486
    total_harvest = math.fsum(float(node["harvest_m3"]) for node in nodes)
    assert math.isclose(total_harvest, siting.EXPECTED_HARVEST_M3, abs_tol=1e-3)
    assert [int(row["k"]) for row in tradeoff] == list(siting.K_VALUES)
    for row in tradeoff:
        assigned = float(row["assigned_m3"])
        assert math.isclose(assigned, total_harvest, abs_tol=1e-3)
        assert math.isclose(
            float(row["existing_served_m3"])
            + float(row["module_served_m3"]),
            assigned,
            abs_tol=1e-3,
        )
        assert math.isclose(
            float(row["existing_served_share"])
            + float(row["module_served_share"]),
            1.0,
            abs_tol=1e-9,
        )
    assert float(tradeoff[0]["module_served_m3"]) == 0.0
    assert math.isclose(
        float(tradeoff[0]["existing_served_m3"]),
        total_harvest,
        abs_tol=1e-3,
    )
    harvest_by_node = {
        node["node_id"]: float(node["harvest_m3"]) for node in active
    }
    for label, rows in (("k0", k0_rows), ("k20", k20_rows)):
        assert math.isclose(
            math.fsum(float(row["assigned_m3"]) for row in rows),
            total_harvest,
            abs_tol=1e-3,
        )
        grouped: dict[str, float] = defaultdict(float)
        for row in rows:
            grouped[row["node_id"]] += float(row["assigned_m3"])
        assert set(grouped) == set(harvest_by_node)
        for node_id, expected in harvest_by_node.items():
            assert math.isclose(
                grouped[node_id], expected, abs_tol=1e-3
            ), (label, node_id)
    assert summary["counts"]["input_nodes"] == 525
    assert summary["counts"]["positive_nodes"] == 486
    assert summary["counts"]["zero_volume_nodes"] == 39


def test_k_monotonicity(tradeoff) -> None:
    costs = [float(row["transport_cost_cad"]) for row in tradeoff]
    means = [float(row["mean_road_distance_km"]) for row in tradeoff]
    share_150 = [float(row["share_ge_150_km"]) for row in tradeoff]
    share_300 = [float(row["share_ge_300_km"]) for row in tradeoff]
    for index in range(len(tradeoff) - 1):
        assert costs[index + 1] <= costs[index] + 1e-6
        assert means[index + 1] <= means[index] + 1e-9
        assert share_150[index + 1] <= share_150[index] + 1e-9
        assert share_300[index + 1] <= share_300[index] + 1e-9
        expected_marginal = costs[index] - costs[index + 1]
        assert math.isclose(
            float(tradeoff[index + 1]["marginal_transport_cost_reduction_cad"]),
            expected_marginal,
            abs_tol=1e-3,
        )
        expected_mean_marginal = means[index] - means[index + 1]
        assert math.isclose(
            float(tradeoff[index + 1]["marginal_mean_distance_reduction_km"]),
            expected_mean_marginal,
            abs_tol=1e-9,
        )
    assert float(tradeoff[-1]["module_served_share"]) > 0.0


def test_module_count_and_nesting(tradeoff, modules, nodes) -> None:
    positive = {
        node["node_id"]
        for node in nodes
        if float(node["harvest_m3"]) > 0.0
    }
    factor_provinces = {
        row["province"]
        for row in _read_csv(
            siting.DATA_ROOT
            / "derived/national/facilities/facility_road_distance_audit_v2"
            / "province_road_factors.csv"
        )
        if row["factor_applied"].strip()
    }
    selected_by_k: dict[int, set[str]] = {}
    for row in tradeoff:
        k = int(row["k"])
        assert int(row["selected_module_count"]) == k
        assert int(row["modules_serving"]) <= k
        module_ids = (
            set(row["selected_module_ids"].split(";"))
            if row["selected_module_ids"]
            else set()
        )
        assert len(module_ids) == k
        selected_by_k[k] = module_ids
        module_rows = [item for item in modules if int(item["k"]) == k]
        assert len(module_rows) == k
        assert {item["module_id"] for item in module_rows} == module_ids
        assert len({item["module_id"] for item in module_rows}) == k
        assert sum(
            float(item["module_served_m3"]) for item in module_rows
        ) == pytest.approx(
            float(row["module_served_m3"]), abs=1e-3
        )
        if k > 0:
            assert sum(
                float(item["module_served_share_of_module_total"])
                for item in module_rows
            ) == pytest.approx(1.0, abs=1e-7)
        for item in module_rows:
            assert item["node_id"] in positive
            assert item["province"] in factor_provinces
            assert int(item["selection_rank"]) <= k
            assert int(item["first_selected_k"]) <= k
            assert float(item["module_served_m3"]) > 0.0
            assert int(item["nodes_served"]) >= 1
    for k in range(len(tradeoff) - 1):
        assert selected_by_k[k] <= selected_by_k[k + 1]
    assert len({item["module_id"] for item in modules}) == 20


def test_cost_arithmetic(tradeoff, k20_rows, summary) -> None:
    line = summary["method"]["transport_cost_line"]
    intercept = float(line["intercept_cad_m3"])
    slope = float(line["slope_cad_m3_km"])
    energy_per_m3 = float(summary["method"]["transport_energy_gj_per_m3"])
    for row in tradeoff:
        assigned = float(row["assigned_m3"])
        volume_distance = float(row["road_distance_m3_km"])
        cost = float(row["transport_cost_cad"])
        assert math.isclose(
            cost,
            intercept * assigned + slope * volume_distance,
            abs_tol=max(1e-2, 1e-9 * abs(cost)),
        )
        assert math.isclose(
            float(row["transport_cost_cad_m3"]),
            cost / assigned,
            abs_tol=1e-9,
        )
        assert math.isclose(
            float(row["transport_energy_gj"]),
            assigned * energy_per_m3,
            abs_tol=1e-6,
        )
        assert math.isclose(
            float(row["transport_cost_cad_gj"]),
            cost / float(row["transport_energy_gj"]),
            abs_tol=1e-9,
        )
        assert math.isclose(
            float(row["mean_road_distance_km"]),
            volume_distance / assigned,
            abs_tol=1e-9,
        )
    row_costs = []
    row_volume_distance = []
    for row in k20_rows:
        assigned = float(row["assigned_m3"])
        distance = float(row["road_distance_km"])
        cost = float(row["transport_cost_cad"])
        assert math.isclose(
            cost,
            assigned * (intercept + slope * distance),
            abs_tol=max(1e-2, 1e-9 * abs(cost)),
        )
        assert math.isclose(
            float(row["transport_cost_cad_m3"]),
            intercept + slope * distance,
            abs_tol=1e-9,
        )
        assert math.isclose(
            float(row["transport_energy_gj"]),
            assigned * energy_per_m3,
            abs_tol=1e-6,
        )
        row_costs.append(cost)
        row_volume_distance.append(assigned * distance)
    k20 = tradeoff[-1]
    assert math.isclose(
        math.fsum(row_costs), float(k20["transport_cost_cad"]), abs_tol=1e-2
    )
    assert math.isclose(
        math.fsum(row_volume_distance),
        float(k20["road_distance_m3_km"]),
        abs_tol=1e-3,
    )


def test_v10_baseline_and_comparison(summary, tradeoff) -> None:
    with siting.V10_SUMMARY_JSON.open("r", encoding="utf-8") as handle:
        v10_summary = json.load(handle)
    v10_primary = v10_summary["v10_primary"]
    baseline = summary["baselines"]["v10_existing_only"]["metrics"]
    assert baseline["transport_cost_cad"] == pytest.approx(
        float(v10_primary["transport_cost_cad"]), abs=1e-6
    )
    assert baseline["mean_road_distance_km"] == pytest.approx(
        float(v10_primary["mean_road_distance_km"]), abs=1e-9
    )
    assert baseline["share_ge_150_km"] == pytest.approx(
        float(v10_primary["share_ge_150_km"]), abs=1e-9
    )
    assert baseline["share_ge_300_km"] == pytest.approx(
        float(v10_primary["share_ge_300_km"]), abs=1e-9
    )
    reproduction = summary["baselines"]["v10_reproduction"]
    assert reproduction["passed"] is True
    assert abs(float(reproduction["delta_cost_cad"])) <= 1.0
    assert abs(
        float(reproduction["delta_mean_road_distance_km"])
    ) <= 1e-6
    k0 = tradeoff[0]
    k20 = tradeoff[-1]
    assert float(k0["transport_cost_cad"]) <= float(
        v10_primary["transport_cost_cad"]
    ) + 1e-3
    assert float(k20["transport_cost_cad"]) < float(
        k0["transport_cost_cad"]
    )
    comparison = summary["baselines"]["comparison"]
    assert comparison["k20_vs_v10"]["transport_cost_delta_cad"] == pytest.approx(
        float(k20["transport_cost_cad"])
        - float(v10_primary["transport_cost_cad"]),
        abs=1e-6,
    )
    assert summary["headline"]["cost_reduction_vs_k0_cad"] > 0.0
    assert summary["headline"]["cost_reduction_vs_v10_cad"] > 0.0
    assert summary["headline"]["k20"]["module_served_share"] > 0.0
    assert (
        summary["headline"]["k20"]["mean_road_distance_km"]
        < summary["baselines"]["k0_existing_only_transport_only"]["metrics"][
            "mean_road_distance_km"
        ]
    )


def test_existing_capacity_service(existing_rows, tradeoff, summary) -> None:
    capacity = float(summary["method"]["existing_capacity_m3_y"])
    assert len(existing_rows) == 2 * summary["counts"]["eligible_facilities"]
    for k in (0, len(siting.K_VALUES) - 1):
        rows = [row for row in existing_rows if int(row["k"]) == k]
        assert len(rows) == summary["counts"]["eligible_facilities"]
        assert math.isclose(
            math.fsum(float(row["capacity_m3_y"]) for row in rows),
            capacity,
            abs_tol=1e-6,
        )
        for row in rows:
            served = float(row["served_m3"])
            row_capacity = float(row["capacity_m3_y"])
            assert served >= 0.0
            assert served <= row_capacity + 1e-6
            assert math.isclose(
                float(row["utilization"]),
                served / row_capacity,
                abs_tol=1e-9,
            )
            assert row["binding"] == (
                "true"
                if row_capacity - served <= max(
                    siting.VOLUME_EPSILON_M3, 2e-9 * row_capacity
                )
                else "false"
            )
        used = sum(1 for row in rows if row["used"] == "true")
        tradeoff_row = tradeoff[k]
        assert used == int(tradeoff_row["existing_facilities_used"])
    k0_served = math.fsum(
        float(row["served_m3"]) for row in existing_rows if int(row["k"]) == 0
    )
    k20_served = math.fsum(
        float(row["served_m3"])
        for row in existing_rows
        if int(row["k"]) == len(siting.K_VALUES) - 1
    )
    assert k0_served == pytest.approx(
        float(tradeoff[0]["existing_served_m3"]), abs=1e-3
    )
    assert k20_served == pytest.approx(
        float(tradeoff[-1]["existing_served_m3"]), abs=1e-3
    )


def test_report_labels_and_disclaimer() -> None:
    report = REPORT_MD.read_text(encoding="utf-8")
    assert "transport_only_siting_screen" in report
    assert "not_investment_advice" in report
    assert "not investment advice" in report.lower()
    assert "transport-only" in report.lower()
    assert "no national claim" in report.lower()
