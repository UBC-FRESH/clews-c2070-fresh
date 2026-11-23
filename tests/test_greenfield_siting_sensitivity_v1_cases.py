"""Regression tests for the 100/119/150 Mm3 greenfield siting case screen."""

from __future__ import annotations

import csv
import json
import math
from collections import defaultdict
from pathlib import Path

import pytest

from workflows.national.facilities import (
    greenfield_siting_sensitivity_v1 as v1,
)
from workflows.national.facilities import (
    greenfield_siting_sensitivity_v1_cases as cases,
)

OUTPUT_DIR = cases.OUTPUT_DIR
SUMMARY_JSON = OUTPUT_DIR / "summary.json"
MANIFEST_JSON = OUTPUT_DIR / "manifest.json"
TRADEOFF_CSV = OUTPUT_DIR / "k_tradeoff.csv"
MODULE_CSV = OUTPUT_DIR / "module_locations.csv"
K0_CSV = OUTPUT_DIR / "node_assignment_k0.csv"
K20_CSV = OUTPUT_DIR / "node_assignment_k20.csv"
EXISTING_CSV = OUTPUT_DIR / "existing_capacity_service_k0_k20.csv"
COMPARISON_CSV = OUTPUT_DIR / "case_comparison.csv"
HEADLINE_CSV = OUTPUT_DIR / "case_headline.csv"
REPORT_MD = OUTPUT_DIR / "GREENFIELD_SITING_SENSITIVITY_V1_CASES.md"


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
def comparison() -> list[dict[str, str]]:
    return _read_csv(COMPARISON_CSV)


@pytest.fixture(scope="module")
def headline() -> list[dict[str, str]]:
    return _read_csv(HEADLINE_CSV)


def _by_case(rows: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[row["case"]].append(row)
    return dict(grouped)


def test_outputs_fields_manifest_and_labels(summary) -> None:
    for path in (
        SUMMARY_JSON,
        MANIFEST_JSON,
        TRADEOFF_CSV,
        MODULE_CSV,
        K0_CSV,
        K20_CSV,
        EXISTING_CSV,
        COMPARISON_CSV,
        HEADLINE_CSV,
        REPORT_MD,
    ):
        assert path.is_file(), path
    assert list(_read_csv(TRADEOFF_CSV)[0]) == cases.TRADEOFF_FIELDS
    assert list(_read_csv(MODULE_CSV)[0]) == cases.MODULE_FIELDS
    assert list(_read_csv(K0_CSV)[0]) == cases.NODE_ASSIGNMENT_FIELDS
    assert list(_read_csv(K20_CSV)[0]) == cases.NODE_ASSIGNMENT_FIELDS
    assert list(_read_csv(EXISTING_CSV)[0]) == cases.EXISTING_SERVICE_FIELDS
    assert list(_read_csv(COMPARISON_CSV)[0]) == cases.COMPARISON_FIELDS
    assert list(_read_csv(HEADLINE_CSV)[0]) == cases.HEADLINE_FIELDS
    manifest = json.loads(MANIFEST_JSON.read_text(encoding="utf-8"))
    assert manifest["artifact"] == cases.ARTIFACT
    assert manifest["artifact_version"] == cases.ARTIFACT_VERSION
    assert manifest["status"] == "complete"
    assert manifest["labels"] == summary["labels"]
    for name, entry in manifest["outputs"].items():
        path = OUTPUT_DIR / name
        assert path.is_file(), name
        assert path.stat().st_size == entry["bytes"], name
        assert v1.v9._sha256(path) == entry["sha256"], name
    for relative, expected_hash in manifest["inputs"].items():
        path = cases.REPO_ROOT / relative
        assert path.is_file(), relative
        assert v1.v9._sha256(path) == expected_hash, relative
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
    assert labels["class_specific_capacity_claim"] is False
    assert labels["module_class"] == "GENERIC_MODULE"
    assert labels["module_capacity"] == "unlimited_screen"
    assert summary["validation"]["all_passed"] is True
    assert summary["validation"]["primary_parity_with_v1"] is True
    assert set(summary["cases"]) == set(cases.CASE_ORDER)
    assert set(summary["cases"]) == {"100m", "119m", "150m"}


def test_three_scenarios_and_conservation(
    summary, tradeoff, k0_rows, k20_rows
) -> None:
    tradeoff_by_case = _by_case(tradeoff)
    k0_by_case = _by_case(k0_rows)
    k20_by_case = _by_case(k20_rows)
    for case, expected in cases.CASES:
        rows = tradeoff_by_case[case]
        assert [int(row["k"]) for row in rows] == list(range(21))
        assert abs(float(summary["cases"][case]["harvest_case_m3"]) - expected) <= 1e-6
        for row in rows:
            assigned = float(row["assigned_m3"])
            assert abs(assigned - expected) <= cases.TOTAL_TOLERANCE_M3
            assert (
                abs(
                    float(row["existing_served_m3"])
                    + float(row["module_served_m3"])
                    - assigned
                )
                <= v1.CONSERVATION_TOLERANCE_M3
            )
            assert float(row["existing_served_m3"]) <= float(
                row["existing_capacity_m3_y"]
            ) + v1.CAPACITY_TOLERANCE_M3
        expected_nodes, _, _ = cases._scaled_positive_nodes(case)
        expected_harvest = {
            node.node_id: float(node.harvest_m3) for node in expected_nodes
        }
        for label, rows_for_case in (
            ("k0", k0_by_case[case]),
            ("k20", k20_by_case[case]),
        ):
            assert abs(
                math.fsum(float(row["assigned_m3"]) for row in rows_for_case)
                - expected
            ) <= cases.TOTAL_TOLERANCE_M3, (case, label)
            grouped: dict[str, float] = defaultdict(float)
            for row in rows_for_case:
                grouped[row["node_id"]] += float(row["assigned_m3"])
            assert set(grouped) == set(expected_harvest), (case, label)
            assert all(
                abs(grouped[node_id] - value)
                <= cases.TOTAL_TOLERANCE_M3
                for node_id, value in expected_harvest.items()
            ), (case, label)
        assert abs(
            math.fsum(
                float(row["assigned_m3"])
                for row in k0_by_case[case]
            )
            - float(rows[0]["assigned_m3"])
        ) <= cases.TOTAL_TOLERANCE_M3
        assert abs(
            math.fsum(
                float(row["assigned_m3"])
                for row in k20_by_case[case]
            )
            - float(rows[-1]["assigned_m3"])
        ) <= cases.TOTAL_TOLERANCE_M3


def test_k_monotonicity_and_module_counts(
    summary, tradeoff, modules, existing_rows
) -> None:
    tradeoff_by_case = _by_case(tradeoff)
    modules_by_case = _by_case(modules)
    existing_by_case = _by_case(existing_rows)
    for case in cases.CASE_ORDER:
        rows = sorted(tradeoff_by_case[case], key=lambda row: int(row["k"]))
        costs = [float(row["transport_cost_cad"]) for row in rows]
        means = [float(row["mean_road_distance_km"]) for row in rows]
        share_150 = [float(row["share_ge_150_km"]) for row in rows]
        share_300 = [float(row["share_ge_300_km"]) for row in rows]
        assert all(
            costs[index + 1] <= costs[index] + 1e-6
            for index in range(len(costs) - 1)
        ), case
        assert all(
            means[index + 1] <= means[index] + 1e-9
            for index in range(len(means) - 1)
        ), case
        assert all(
            share_150[index + 1] <= share_150[index] + 1e-9
            for index in range(len(share_150) - 1)
        ), case
        assert all(
            share_300[index + 1] <= share_300[index] + 1e-9
            for index in range(len(share_300) - 1)
        ), case
        selected_sets = {}
        for row in rows:
            k = int(row["k"])
            selected = {
                item
                for item in row["selected_module_ids"].split(";")
                if item
            }
            selected_sets[k] = selected
            assert int(row["selected_module_count"]) == k
            assert len(selected) == k
            assert int(row["modules_serving"]) <= k
            if k == 0:
                assert float(row["module_served_m3"]) == 0.0
                assert float(row["existing_served_m3"]) == float(
                    row["assigned_m3"]
                )
        assert all(
            selected_sets[k - 1] <= selected_sets[k] for k in range(1, 21)
        ), case
        assert len(selected_sets[20]) == 20
        assert int(rows[-1]["modules_serving"]) == 20
        case_modules = modules_by_case[case]
        assert len(case_modules) == 210
        assert len(
            [row for row in case_modules if int(row["k"]) == 20]
        ) == 20
        for k in range(21):
            assert abs(
                math.fsum(
                    float(row["module_served_m3"])
                    for row in case_modules
                    if int(row["k"]) == k
                )
                - float(rows[k]["module_served_m3"])
            ) <= v1.CONSERVATION_TOLERANCE_M3
        for row in case_modules:
            assert row["province"] != "NT"
            assert row["capacity_status"] == "unlimited_screen"
            assert row["capacity_m3_y"] == ""
            assert row["module_class"] == "GENERIC_MODULE"
            assert row["road_factor_basis"] == "candidate_province_measured_factor"
        if case == "119m":
            assert len(existing_by_case[case]) == 1182
        else:
            assert len(existing_by_case[case]) == 1182
    assert summary["counts"]["candidate_modules"] == 421
    assert summary["counts"]["eligible_facilities"] == 591
    assert summary["counts"]["module_location_rows"] == 630


def test_comparison_and_119_parity(
    summary, comparison, headline, modules
) -> None:
    assert len(comparison) == 15
    assert {row["case"] for row in comparison} == {"100m", "119m", "150m"}
    assert {int(row["k"]) for row in comparison} == {0, 5, 10, 15, 20}
    for row in comparison:
        assert row["evidence_label"] == "model_estimate"
        assert row["observed"] == "false"
        assert row["policy_grade"] == "false"
        if row["case"] == "119m":
            assert abs(float(row["delta_transport_cost_vs_119_cad"])) <= 1e-6
            assert abs(float(row["delta_mean_road_distance_vs_119_km"])) <= 1e-12
    assert len(headline) == 3
    headline_by_case = {row["case"]: row for row in headline}
    assert set(headline_by_case) == {"100m", "119m", "150m"}
    for row in headline_by_case.values():
        assert 0.0 <= float(row["k20_module_served_share"]) <= 1.0
        assert int(row["k20_modules_serving"]) == 20
        assert float(row["k20_share_ge_150_km"]) >= 0.0
        assert float(row["k20_share_ge_300_km"]) >= 0.0
    assert abs(
        float(headline_by_case["119m"]["delta_transport_cost_vs_119_cad"])
    ) <= 1e-6
    assert abs(
        float(headline_by_case["150m"]["delta_transport_cost_vs_119_cad"])
    ) > 1.0

    # The 119 Mm3 case is the committed v1 artifact, not a re-run.
    v1_summary = json.loads(
        (v1.OUTPUT_DIR / "summary.json").read_text(encoding="utf-8")
    )
    v1_tradeoff = {
        int(row["k"]): row for row in v1_summary["k_tradeoff"]
    }
    for row in summary["cases"]["119m"]["k_tradeoff"]:
        expected = v1_tradeoff[int(row["k"])]
        for key, value in expected.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                assert math.isclose(
                    float(row[key]),
                    float(value),
                    rel_tol=1e-12,
                    abs_tol=1e-9,
                ), (key, row["k"])
    v1_modules = {
        (int(row["k"]), row["module_id"]): row
        for row in v1_summary["module_locations"]
    }
    for row in _by_case(modules)["119m"]:
        expected = v1_modules[(int(row["k"]), row["module_id"])]
        for key, value in expected.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                assert math.isclose(
                    float(row[key]),
                    float(value),
                    rel_tol=1e-12,
                    abs_tol=1e-9,
                ), (key, row["module_id"])
    report = REPORT_MD.read_text(encoding="utf-8")
    assert "transport_only_siting_screen" in report
    assert "not_investment_advice" in report
    assert "model_estimate" in report
    assert "No national claim" in report
    assert "## 2. k tradeoff by case" in report
    assert "## 5. Module locations at k=20" in report
