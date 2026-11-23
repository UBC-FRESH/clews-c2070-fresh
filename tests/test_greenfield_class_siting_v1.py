"""Regression tests for the class-specific greenfield siting screen v1."""

from __future__ import annotations

import csv
import json
import math
from collections import defaultdict
from pathlib import Path

import pytest

from workflows.national.facilities import greenfield_class_siting_v1 as siting

OUTPUT_DIR = siting.OUTPUT_DIR
SUMMARY_JSON = OUTPUT_DIR / "summary.json"
MANIFEST_JSON = OUTPUT_DIR / "manifest.json"
CLASS_TRADEOFF_CSV = OUTPUT_DIR / "class_k_tradeoff.csv"
AGGREGATE_CSV = OUTPUT_DIR / "aggregate_k_tradeoff.csv"
MODULE_CSV = OUTPUT_DIR / "class_module_locations.csv"
SPLIT_CSV = OUTPUT_DIR / "class_feedstock_split.csv"
GENERIC_COMPARISON_CSV = OUTPUT_DIR / "generic_comparison.csv"
K0_CSV = OUTPUT_DIR / "node_assignment_k0.csv"
K5_CSV = OUTPUT_DIR / "node_assignment_k5.csv"
EXISTING_CSV = OUTPUT_DIR / "existing_capacity_service_k0_k5.csv"
REPORT_MD = OUTPUT_DIR / "GREENFIELD_CLASS_SITING_V1.md"


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


@pytest.fixture(scope="module")
def summary() -> dict:
    with SUMMARY_JSON.open("r", encoding="utf-8") as handle:
        return json.load(handle)


@pytest.fixture(scope="module")
def class_tradeoff() -> list[dict[str, str]]:
    return _read_csv(CLASS_TRADEOFF_CSV)


@pytest.fixture(scope="module")
def aggregate() -> list[dict[str, str]]:
    return _read_csv(AGGREGATE_CSV)


@pytest.fixture(scope="module")
def modules() -> list[dict[str, str]]:
    return _read_csv(MODULE_CSV)


@pytest.fixture(scope="module")
def split_rows() -> list[dict[str, str]]:
    return _read_csv(SPLIT_CSV)


@pytest.fixture(scope="module")
def generic_comparison() -> list[dict[str, str]]:
    return _read_csv(GENERIC_COMPARISON_CSV)


@pytest.fixture(scope="module")
def k0_rows() -> list[dict[str, str]]:
    return _read_csv(K0_CSV)


@pytest.fixture(scope="module")
def k5_rows() -> list[dict[str, str]]:
    return _read_csv(K5_CSV)


@pytest.fixture(scope="module")
def existing_rows() -> list[dict[str, str]]:
    return _read_csv(EXISTING_CSV)


@pytest.fixture(scope="module")
def nodes() -> list[dict[str, str]]:
    return _read_csv(siting.v10.SUPPLY_NODES_CSV)


def test_outputs_fields_manifest_and_labels(summary) -> None:
    for path in (
        SUMMARY_JSON,
        MANIFEST_JSON,
        CLASS_TRADEOFF_CSV,
        AGGREGATE_CSV,
        MODULE_CSV,
        SPLIT_CSV,
        GENERIC_COMPARISON_CSV,
        K0_CSV,
        K5_CSV,
        EXISTING_CSV,
        REPORT_MD,
    ):
        assert path.is_file(), path
    assert list(_read_csv(CLASS_TRADEOFF_CSV)[0]) == siting.CLASS_TRADEOFF_FIELDS
    assert list(_read_csv(AGGREGATE_CSV)[0]) == siting.AGGREGATE_FIELDS
    assert list(_read_csv(MODULE_CSV)[0]) == siting.MODULE_FIELDS
    assert list(_read_csv(SPLIT_CSV)[0]) == siting.SPLIT_FIELDS
    assert list(_read_csv(GENERIC_COMPARISON_CSV)[0]) == (
        siting.GENERIC_COMPARISON_FIELDS
    )
    assert list(_read_csv(K0_CSV)[0]) == siting.NODE_ASSIGNMENT_FIELDS
    assert list(_read_csv(K5_CSV)[0]) == siting.NODE_ASSIGNMENT_FIELDS
    assert list(_read_csv(EXISTING_CSV)[0]) == siting.EXISTING_SERVICE_FIELDS
    manifest = json.loads(MANIFEST_JSON.read_text(encoding="utf-8"))
    assert manifest["artifact"] == "greenfield_class_siting_v1"
    assert manifest["artifact_version"] == "1.0.0"
    assert manifest["status"] == "complete"
    assert manifest["labels"] == summary["labels"]
    for name, entry in manifest["outputs"].items():
        path = OUTPUT_DIR / name
        assert path.is_file(), name
        assert path.stat().st_size == entry["bytes"], name
        assert siting._sha256(path) == entry["sha256"], name
    for relative, expected_hash in manifest["inputs"].items():
        path = siting.REPO_ROOT / relative
        assert path.is_file(), relative
        assert siting._sha256(path) == expected_hash, relative
    labels = summary["labels"]
    assert labels["sensitivity_label"] == "transport_only_siting_screen"
    assert labels["investment_advice_label"] == "not_investment_advice"
    assert labels["evidence_label"] == "model_estimate"
    assert labels["observed"] is False
    assert labels["policy_grade"] is False
    assert labels["investment_advice"] is False
    assert labels["national_claim"] is False
    assert labels["policy_claim"] is False
    assert labels["official_osecan_changed"] is False
    assert labels["ui_changed"] is False
    assert labels["capital_cost_claim"] is False
    assert labels["permitting_claim"] is False
    assert labels["landbase_claim"] is False
    assert labels["technology_claim"] is False
    assert labels["class_specific_capacity_claim"] is False
    assert labels["class_specific_module"] is True
    assert labels["module_capacity"] == "unlimited_screen"
    assert summary["validation"]["all_passed"] is True
    assert not summary["validation"]["failures"]


def test_labels_on_every_row(
    class_tradeoff,
    aggregate,
    modules,
    split_rows,
    generic_comparison,
    k0_rows,
    k5_rows,
    existing_rows,
) -> None:
    all_rows = (
        class_tradeoff
        + aggregate
        + modules
        + split_rows
        + generic_comparison
        + k0_rows
        + k5_rows
        + existing_rows
    )
    assert all_rows
    for row in all_rows:
        assert row["evidence_label"] == "model_estimate"
        assert row["sensitivity_label"] == "transport_only_siting_screen"
        assert row["investment_advice_label"] == "not_investment_advice"
        assert row["observed"] == "false"
        assert row["policy_grade"] == "false"
    for row in modules:
        assert row["module_class"] == row["class"]
        assert row["class_specific"] == "true"
        assert row["capacity_status"] == "unlimited_screen"
        assert row["capacity_m3_y"] == ""


def test_class_conservation(nodes, split_rows, class_tradeoff, k0_rows, k5_rows, summary) -> None:
    assert len(nodes) == 525
    active = [node for node in nodes if float(node["harvest_m3"]) > 0.0]
    assert len(active) == 486
    total_harvest = math.fsum(float(node["harvest_m3"]) for node in nodes)
    assert math.isclose(total_harvest, siting.EXPECTED_HARVEST_M3, abs_tol=1e-3)

    shares: dict[str, dict[str, float]] = defaultdict(dict)
    harvest_by_province: dict[str, float] = defaultdict(float)
    for row in split_rows:
        province = row["province"]
        shares[province][row["facility_class"]] = float(row["class_share"])
        harvest_by_province[province] = float(row["province_harvest_m3"])
    for province, class_shares in shares.items():
        assert set(class_shares) == set(siting.CLASS_ORDER)
        if harvest_by_province[province] > 0.0:
            assert math.isclose(
                math.fsum(class_shares.values()), 1.0, abs_tol=1e-6
            )
        else:
            assert math.isclose(math.fsum(class_shares.values()), 0.0, abs_tol=1e-12)

    expected_by_class: dict[str, float] = {}
    for facility_class in siting.CLASS_ORDER:
        expected = math.fsum(
            float(row["class_harvest_split_m3"])
            for row in split_rows
            if row["facility_class"] == facility_class
        )
        expected_by_class[facility_class] = expected
        assert math.isclose(
            math.fsum(
                float(row["assigned_m3"])
                for row in class_tradeoff
                if row["class"] == facility_class
            )
            / len(siting.K_VALUES),
            expected,
            abs_tol=1e-3,
        )
    assert math.isclose(
        math.fsum(expected_by_class.values()), total_harvest, abs_tol=1e-3
    )
    for row in class_tradeoff:
        assigned = float(row["assigned_m3"])
        assert math.isclose(assigned, expected_by_class[row["class"]], abs_tol=1e-3)
        assert math.isclose(
            float(row["existing_served_m3"]) + float(row["module_served_m3"]),
            assigned,
            abs_tol=1e-3,
        )
        assert math.isclose(
            float(row["existing_served_share"])
            + float(row["module_served_share"]),
            1.0,
            abs_tol=1e-9,
        )
        if int(row["k"]) == 0:
            assert float(row["module_served_m3"]) == 0.0

    harvest_by_node = {
        node["node_id"]: float(node["harvest_m3"]) for node in active
    }
    class_node_demand = {
        (row["class"], row["node_id"]): float(row["node_class_feedstock_m3"])
        for row in k0_rows
    }
    for label, rows in (("k0", k0_rows), ("k5", k5_rows)):
        grouped: dict[tuple[str, str], float] = defaultdict(float)
        node_total: dict[str, float] = defaultdict(float)
        for row in rows:
            key = (row["class"], row["node_id"])
            grouped[key] += float(row["assigned_m3"])
            node_total[row["node_id"]] += float(row["assigned_m3"])
        assert set(grouped) == set(class_node_demand)
        for key, expected in class_node_demand.items():
            assert math.isclose(grouped[key], expected, abs_tol=1e-3), (label, key)
        assert set(node_total) == set(harvest_by_node)
        for node_id, expected in harvest_by_node.items():
            assert math.isclose(node_total[node_id], expected, abs_tol=1e-3), (
                label,
                node_id,
            )
    assert summary["counts"]["input_nodes"] == 525
    assert summary["counts"]["positive_nodes"] == 486
    assert summary["counts"]["class_demand_nodes"] == 2372


def test_k_monotonicity(class_tradeoff, aggregate) -> None:
    for facility_class in siting.CLASS_ORDER:
        rows = [
            row for row in class_tradeoff if row["class"] == facility_class
        ]
        assert [int(row["k"]) for row in rows] == list(siting.K_VALUES)
        costs = [float(row["transport_cost_cad"]) for row in rows]
        means = [float(row["mean_road_distance_km"]) for row in rows]
        share_150 = [float(row["share_ge_150_km"]) for row in rows]
        share_300 = [float(row["share_ge_300_km"]) for row in rows]
        for index in range(len(rows) - 1):
            assert costs[index + 1] <= costs[index] + 1e-6
            assert means[index + 1] <= means[index] + 1e-9
            assert share_150[index + 1] <= share_150[index] + 1e-9
            assert share_300[index + 1] <= share_300[index] + 1e-9
            assert math.isclose(
                float(
                    rows[index + 1]["marginal_transport_cost_reduction_cad"]
                ),
                costs[index] - costs[index + 1],
                abs_tol=1e-3,
            )
            assert math.isclose(
                float(rows[index + 1]["marginal_mean_distance_reduction_km"]),
                means[index] - means[index + 1],
                abs_tol=1e-9,
            )
        assert float(rows[-1]["module_served_share"]) > 0.0

    aggregate_costs = [float(row["transport_cost_cad"]) for row in aggregate]
    aggregate_means = [float(row["mean_road_distance_km"]) for row in aggregate]
    for index in range(len(aggregate) - 1):
        assert aggregate_costs[index + 1] <= aggregate_costs[index] + 1e-6
        assert aggregate_means[index + 1] <= aggregate_means[index] + 1e-9
        assert math.isclose(
            float(
                aggregate[index + 1]["marginal_transport_cost_reduction_cad"]
            ),
            aggregate_costs[index] - aggregate_costs[index + 1],
            abs_tol=1e-3,
        )


def test_module_counts(modules, class_tradeoff, aggregate, nodes) -> None:
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
    for facility_class in siting.CLASS_ORDER:
        rows = [
            row for row in class_tradeoff if row["class"] == facility_class
        ]
        selected_by_k: dict[int, set[str]] = {}
        for row in rows:
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
            module_rows = [
                item
                for item in modules
                if item["class"] == facility_class and int(item["k"]) == k
            ]
            assert len(module_rows) == k
            assert {item["module_id"] for item in module_rows} == module_ids
            assert len({item["module_id"] for item in module_rows}) == k
            assert sum(
                float(item["module_served_m3"]) for item in module_rows
            ) == pytest.approx(float(row["module_served_m3"]), abs=1e-3)
            for item in module_rows:
                assert item["node_id"] in positive
                assert item["province"] in factor_provinces
                assert item["module_id"].startswith(
                    f"GFC|{facility_class}|"
                )
                assert int(item["selection_rank"]) <= k
                assert int(item["first_selected_k"]) <= k
                assert float(item["module_served_m3"]) >= 0.0
                if item["serving"] == "true":
                    assert float(item["module_served_m3"]) > 0.0
                    assert int(item["nodes_served"]) >= 1
        for k in range(len(rows) - 1):
            assert selected_by_k[k] <= selected_by_k[k + 1]
        assert len(
            {item["module_id"] for item in modules if item["class"] == facility_class}
        ) == len(siting.K_VALUES) - 1
    for row in aggregate:
        k = int(row["k_per_class"])
        assert int(row["total_modules"]) == len(siting.CLASS_ORDER) * k
        assert int(row["modules_serving"]) <= int(row["total_modules"])
    assert summary_module_total(modules) == 25


def summary_module_total(modules: list[dict[str, str]]) -> int:
    return len(
        {
            item["module_id"]
            for item in modules
            if int(item["k"]) == max(siting.K_VALUES)
        }
    )


def test_cost_arithmetic(class_tradeoff, aggregate, k5_rows, summary) -> None:
    line = summary["method"]["transport_cost_line"]
    intercept = float(line["intercept_cad_m3"])
    slope = float(line["slope_cad_m3_km"])
    energy_per_m3 = float(summary["method"]["transport_energy_gj_per_m3"])
    for row in class_tradeoff:
        assigned = float(row["assigned_m3"])
        volume_distance = float(row["road_distance_m3_km"])
        cost = float(row["transport_cost_cad"])
        assert math.isclose(
            cost,
            intercept * assigned + slope * volume_distance,
            abs_tol=max(1e-2, 1e-9 * abs(cost)),
        )
        assert math.isclose(
            float(row["transport_cost_cad_m3"]), cost / assigned, abs_tol=1e-9
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
    for row in aggregate:
        k = int(row["k_per_class"])
        class_rows = [
            item for item in class_tradeoff if int(item["k"]) == k
        ]
        assert len(class_rows) == len(siting.CLASS_ORDER)
        assert math.isclose(
            float(row["transport_cost_cad"]),
            math.fsum(float(item["transport_cost_cad"]) for item in class_rows),
            abs_tol=1e-2,
        )
        assert math.isclose(
            float(row["assigned_m3"]),
            math.fsum(float(item["assigned_m3"]) for item in class_rows),
            abs_tol=1e-3,
        )
        assert math.isclose(
            float(row["module_served_m3"]),
            math.fsum(float(item["module_served_m3"]) for item in class_rows),
            abs_tol=1e-3,
        )
    row_costs = []
    for row in k5_rows:
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
        row_costs.append(cost)
    k5 = next(
        row
        for row in aggregate
        if int(row["k_per_class"]) == max(siting.K_VALUES)
    )
    assert math.isclose(
        math.fsum(row_costs), float(k5["transport_cost_cad"]), abs_tol=1e-2
    )


def test_generic_comparison(generic_comparison, aggregate, summary) -> None:
    generic = {
        int(row["k"]): row
        for row in _read_csv(siting.GENERIC_TRADEOFF_CSV)
    }
    assert sorted(generic) == list(range(21))
    for row in generic_comparison:
        generic_row = generic[int(row["generic_k"])]
        assert math.isclose(
            float(row["generic_transport_cost_cad"]),
            float(generic_row["transport_cost_cad"]),
            abs_tol=1e-6,
        )
        assert math.isclose(
            float(row["delta_transport_cost_cad"]),
            float(row["class_transport_cost_cad"])
            - float(generic_row["transport_cost_cad"]),
            abs_tol=1e-6,
        )
        assert math.isclose(
            float(row["delta_mean_road_distance_km"]),
            float(row["class_mean_road_distance_km"])
            - float(generic_row["mean_road_distance_km"]),
            abs_tol=1e-9,
        )
        assert int(row["class_total_modules"]) == (
            len(siting.CLASS_ORDER) * int(row["k_per_class"])
        )
        if row["comparison_basis"] == "same_k_per_class":
            assert int(row["generic_k"]) == int(row["k_per_class"])
        else:
            assert int(row["k_per_class"]) > 0
            assert int(row["generic_k"]) == min(
                int(row["class_total_modules"]), 20
            )
    k0 = next(
        row for row in generic_comparison if int(row["k_per_class"]) == 0
    )
    assert float(k0["class_transport_cost_cad"]) >= float(
        k0["generic_transport_cost_cad"]
    )
    exact = next(
        row
        for row in generic_comparison
        if row["comparison_basis"] == "module_count_matched"
        and int(row["k_per_class"]) == 4
    )
    assert int(exact["class_total_modules"]) == 20
    assert int(exact["generic_k"]) == 20
    assert exact["module_count_exact"] == "true"
    assert summary["generic_comparison"]["k4_exact_count_match"][
        "generic_k"
    ] == 20
    assert len(summary["generic_comparison"]["exact_module_count_matches"]) == 4
    for row in aggregate:
        k = int(row["k_per_class"])
        assert int(row["generic_same_k"]) == k
        assert int(row["generic_matched_k"]) == min(
            len(siting.CLASS_ORDER) * k, 20
        )


def test_existing_capacity_service(existing_rows, class_tradeoff, summary) -> None:
    capacity = float(summary["method"]["existing_capacity_m3_y"])
    assert len(existing_rows) == 2 * summary["counts"]["eligible_facilities"]
    for k in (0, max(siting.K_VALUES)):
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
            assert 0.0 <= served <= row_capacity + 1e-6
            assert math.isclose(
                float(row["utilization"]), served / row_capacity, abs_tol=1e-9
            )
        for facility_class in siting.CLASS_ORDER:
            class_rows = [
                row for row in rows if row["class"] == facility_class
            ]
            tradeoff = next(
                row
                for row in class_tradeoff
                if row["class"] == facility_class and int(row["k"]) == k
            )
            assert math.isclose(
                math.fsum(float(row["served_m3"]) for row in class_rows),
                float(tradeoff["existing_served_m3"]),
                abs_tol=1e-3,
            )


def test_report_labels_and_disclaimer() -> None:
    report = REPORT_MD.read_text(encoding="utf-8")
    assert "transport_only_siting_screen" in report
    assert "not_investment_advice" in report
    assert "not investment advice" in report.lower()
    assert "transport-only" in report.lower()
    assert "class-specific" in report.lower()
    assert "no national claim" in report.lower()
