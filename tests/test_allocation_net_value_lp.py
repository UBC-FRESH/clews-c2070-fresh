"""Tests for the provincial allocation net-value LP sensitivity."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from workflows.national.harvest import allocation_net_value_lp as model

OUTPUT_DIR = REPO_ROOT / "data/derived/national/harvest_basis/allocation_net_value_lp"
CANONICAL_DIR = REPO_ROOT / "data/derived/national/harvest_basis/provincial_allocation"
CASES = {
    "100m": 100_000_000.0,
    "119m": 118_977_339.0,
    "150m": 150_000_000.0,
}
FEASIBLE_CAP_LEVELS = (
    ("documented_ceiling", "100m"),
    ("documented_ceiling", "119m"),
    ("documented_ceiling", "150m"),
    ("intensity_1.0", "100m"),
    ("intensity_1.0", "119m"),
    ("intensity_1.2", "100m"),
    ("intensity_1.2", "119m"),
)
INFEASIBLE_CAP_LEVELS = (
    ("intensity_1.0", "150m"),
    ("intensity_1.2", "150m"),
)


def _read_csv(name: str) -> list[dict[str, str]]:
    path = OUTPUT_DIR / name
    assert path.is_file(), f"missing artifact: {path}"
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _summary() -> dict:
    path = OUTPUT_DIR / "summary.json"
    assert path.is_file(), f"missing summary: {path}"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def allocation_rows() -> list[dict[str, str]]:
    rows = _read_csv(model.ALLOCATION_CSV)
    assert len(rows) == 14 * 13
    return rows


@pytest.fixture(scope="module")
def delta_rows() -> list[dict[str, str]]:
    rows = _read_csv(model.ALLOCATION_DELTA_CSV)
    assert len(rows) == 14 * 13
    return rows


def _group(
    rows: list[dict[str, str]], scenario: str, cap_case: str, level: str
) -> list[dict[str, str]]:
    selected = [
        row
        for row in rows
        if row["scenario"] == scenario
        and row["cap_case"] == cap_case
        and row["level"] == level
    ]
    assert len(selected) == 13
    return selected


def test_case_grid_statuses_and_labels() -> None:
    summary = _summary()
    assert summary["coverage"]["case_count"] == 18
    assert summary["coverage"]["feasible_case_count"] == 14
    assert summary["coverage"]["infeasible_case_count"] == 4
    assert summary["coverage"]["documented_ceiling_case_count"] == 6
    assert summary["labels"]["evidence"] == "model_estimate"
    assert summary["labels"]["observed"] is False
    assert summary["labels"]["policy_grade"] is False
    assert summary["labels"]["lp_objective"] == "net_value_sensitivity"
    assert summary["labels"]["canonical_changed"] is False
    assert summary["labels"]["combined_national_net_value"] is False
    assert summary["labels"]["public_osecan_input"] is False

    for scenario in model.SCENARIOS:
        for cap_case in model.CAP_CASES:
            for level in model.LEVELS:
                case = next(
                    item
                    for item in summary["cases"]
                    if item["scenario"] == scenario
                    and item["cap_case"] == cap_case
                    and item["level"] == level
                )
                expected = (
                    "optimal"
                    if (cap_case, level) in FEASIBLE_CAP_LEVELS
                    else "infeasible"
                )
                assert case["status"] == expected


def test_total_conservation_and_shares(
    allocation_rows: list[dict[str, str]],
) -> None:
    for scenario in model.SCENARIOS:
        for cap_case, level in FEASIBLE_CAP_LEVELS:
            rows = _group(allocation_rows, scenario, cap_case, level)
            total = CASES[level]
            harvest_sum = sum(float(row["optimized_harvest_m3"]) for row in rows)
            share_sum = sum(float(row["optimized_share"]) for row in rows)
            baseline_sum = sum(float(row["baseline_harvest_m3"]) for row in rows)
            assert harvest_sum == pytest.approx(total, abs=2.0)
            assert baseline_sum == pytest.approx(total, abs=2.0)
            assert share_sum == pytest.approx(1.0, abs=1e-6)
            assert sum(float(row["baseline_share"]) for row in rows) == pytest.approx(
                1.0, abs=1e-6
            )


def test_caps_and_binding_status(
    allocation_rows: list[dict[str, str]],
) -> None:
    summary = _summary()
    by_key = {
        (case["scenario"], case["cap_case"], case["level"]): case
        for case in summary["cases"]
    }
    for scenario in model.SCENARIOS:
        for cap_case, level in FEASIBLE_CAP_LEVELS:
            rows = _group(allocation_rows, scenario, cap_case, level)
            binding = []
            for row in rows:
                harvest = float(row["optimized_harvest_m3"])
                cap = float(row["upper_bound_m3"])
                assert harvest >= -0.001
                assert harvest <= cap + 2.0
                is_binding = cap > 0.0 and cap - harvest <= 1.0001
                assert (row["binding"] == "true") == is_binding
                if is_binding:
                    binding.append(row["province"])
            assert binding == by_key[(scenario, cap_case, level)]["binding_provinces"]


def test_net_value_arithmetic(
    allocation_rows: list[dict[str, str]],
) -> None:
    summary = _summary()
    by_key = {
        (case["scenario"], case["cap_case"], case["level"]): case
        for case in summary["cases"]
    }
    for scenario in model.SCENARIOS:
        for cap_case, level in FEASIBLE_CAP_LEVELS:
            rows = _group(allocation_rows, scenario, cap_case, level)
            case = by_key[(scenario, cap_case, level)]
            totals = {"low": 0.0, "base": 0.0, "high": 0.0}
            baseline_totals = {"low": 0.0, "base": 0.0, "high": 0.0}
            for row in rows:
                harvest = float(row["optimized_harvest_m3"])
                baseline = float(row["baseline_harvest_m3"])
                for margin_case in ("low", "base", "high"):
                    margin = float(row[f"margin_{margin_case}_cad_m3"])
                    assert float(
                        row[f"net_value_{margin_case}_m_cad"]
                    ) == pytest.approx(harvest * margin / 1e6, abs=0.01)
                    assert float(
                        row[f"baseline_net_value_{margin_case}_m_cad"]
                    ) == pytest.approx(baseline * margin / 1e6, abs=0.01)
                    totals[margin_case] += float(row[f"net_value_{margin_case}_m_cad"])
                    baseline_totals[margin_case] += float(
                        row[f"baseline_net_value_{margin_case}_m_cad"]
                    )
                assert float(row["delta_m3"]) == pytest.approx(
                    harvest - baseline, abs=0.01
                )
                assert float(row["delta_net_value_base_m_cad"]) == pytest.approx(
                    float(row["net_value_base_m_cad"])
                    - float(row["baseline_net_value_base_m_cad"]),
                    abs=0.01,
                )
            for margin_case in ("low", "base", "high"):
                assert totals[margin_case] == pytest.approx(
                    case[f"net_value_{margin_case}_m_cad"], abs=0.02
                )
                assert baseline_totals[margin_case] == pytest.approx(
                    case[f"baseline_net_value_{margin_case}_m_cad"], abs=0.02
                )
            assert case["delta_net_value_base_m_cad"] == pytest.approx(
                case["net_value_base_m_cad"] - case["baseline_net_value_base_m_cad"],
                abs=0.02,
            )
            assert case["achieved_m_cad"] == pytest.approx(
                case["net_value_base_m_cad"], abs=0.02
            )
            assert case["optimality_gap_m_cad"] <= 0.01


def test_baseline_preserved_and_canonical_unchanged(
    allocation_rows: list[dict[str, str]],
) -> None:
    summary = _summary()
    assert summary["baseline"]["canonical_changed"] is False
    assert summary["baseline"]["checks"]
    assert all(check["status"] == "passed" for check in summary["baseline"]["checks"])
    for level in model.LEVELS:
        canonical_path = CANONICAL_DIR / f"{level}.csv"
        canonical = {
            row["province"]: float(row["harvest_m3"])
            for row in csv.DictReader(canonical_path.open(newline="", encoding="utf-8"))
        }
        rows = _group(allocation_rows, "donor_flat", "documented_ceiling", level)
        for row in rows:
            province = row["province"]
            assert float(row["baseline_harvest_m3"]) == pytest.approx(
                canonical[province], abs=2.0
            )


def test_negative_margins_are_unclipped_and_forced(
    allocation_rows: list[dict[str, str]],
) -> None:
    summary = _summary()
    assert summary["validation"]["negative_margins_clipped"] is False
    assert summary["validation"]["negative_base_margin_rows"] > 0
    assert (
        summary["validation"]["negative_base_margin_rows_with_positive_allocation"] > 0
    )

    rows = _group(allocation_rows, "stumpage_scaled", "documented_ceiling", "150m")
    negative = {
        row["province"]: row for row in rows if float(row["margin_base_cad_m3"]) < 0.0
    }
    assert {"AB", "MB", "ON", "QC", "NB"} <= set(negative)
    for province in ("AB", "MB", "QC", "NB"):
        row = negative[province]
        assert float(row["optimized_harvest_m3"]) > 0.0
        assert float(row["net_value_base_m_cad"]) < 0.0
    qc = negative["QC"]
    assert float(qc["margin_base_cad_m3"]) == pytest.approx(-35.8577, abs=1e-4)
    mb = negative["MB"]
    assert float(mb["margin_base_cad_m3"]) == pytest.approx(-30.6666, abs=1e-4)


def test_infeasible_cases_are_reported_without_allocation(
    allocation_rows: list[dict[str, str]],
) -> None:
    summary = _summary()
    by_key = {
        (case["scenario"], case["cap_case"], case["level"]): case
        for case in summary["cases"]
    }
    for scenario in model.SCENARIOS:
        for cap_case, level in INFEASIBLE_CAP_LEVELS:
            case = by_key[(scenario, cap_case, level)]
            assert case["status"] == "infeasible"
            assert case["feasible"] is False
            assert case["harvest_sum_m3"] is None
            assert case["net_value_base_m_cad"] is None
            assert case["deficit_m3"] == pytest.approx(
                CASES[level] - case["capacity_m3"], abs=1.0
            )
            assert not _group_or_empty(allocation_rows, scenario, cap_case, level)


def _group_or_empty(
    rows: list[dict[str, str]], scenario: str, cap_case: str, level: str
) -> list[dict[str, str]]:
    return [
        row
        for row in rows
        if row["scenario"] == scenario
        and row["cap_case"] == cap_case
        and row["level"] == level
    ]


def test_solver_handles_all_negative_margins() -> None:
    solved = model.solve_net_value_harvest(
        {"A": 10_000_000.0, "B": 5_000_000.0},
        {"A": -1.0, "B": -3.0},
        12_000_000.0,
        label="toy_all_negative",
    )
    assert solved.status == "optimal"
    assert solved.harvest_m3["A"] == pytest.approx(10_000_000.0, abs=1.0)
    assert solved.harvest_m3["B"] == pytest.approx(2_000_000.0, abs=1.0)
    assert solved.achieved_m_cad == pytest.approx(-16.0, abs=1e-6)
    assert solved.optimality_gap_m_cad == pytest.approx(0.0, abs=1e-6)


def test_solver_infeasible_deficit_is_exact() -> None:
    solved = model.solve_net_value_harvest(
        {"A": 10_000_000.0, "B": 5_000_000.0},
        {"A": 5.0, "B": 1.0},
        20_000_000.0,
        label="toy_infeasible",
    )
    assert solved.status == "infeasible"
    assert solved.harvest_m3 == {}
    assert solved.deficit_m3 == pytest.approx(5_000_000.0)


def test_fail_closed_deletion_parser() -> None:
    status = " M file.py\n D removed.csv\nD  staged.csv\n?? new.txt\n"
    assert model._deletion_lines(status) == [" D removed.csv", "D  staged.csv"]
    assert model._deletion_lines(" M file.py\n?? new.txt\n") == []


def test_manifest_hashes_and_labels() -> None:
    manifest = json.loads(
        (OUTPUT_DIR / model.MANIFEST_JSON).read_text(encoding="utf-8")
    )
    assert manifest["observation_type"] == "model_estimate"
    assert manifest["labels"]["observed"] is False
    assert manifest["labels"]["policy_grade"] is False
    assert manifest["labels"]["lp_objective"] == "net_value_sensitivity"
    assert manifest["labels"]["canonical_changed"] is False
    assert manifest["validation"]["unexpected_d_cells"] == 0
    assert manifest["validation"]["canonical_snapshot_unchanged"] is True
    assert manifest["validation"]["donor_price_caveat_present"] is True
    assert manifest["inputs"] and manifest["outputs"]
    for record in manifest["outputs"]:
        path = OUTPUT_DIR / record["path"]
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert digest == record["sha256"]
        assert path.stat().st_size == record["bytes"]


def test_no_non_finite_values(
    allocation_rows: list[dict[str, str]],
) -> None:
    for row in allocation_rows:
        for field, value in row.items():
            if field in {
                "province",
                "province_name",
                "scenario",
                "scenario_role",
                "cap_case",
                "cap_case_role",
                "level",
                "margin_case_optimized",
                "margin_status",
                "price_basis",
                "cost_basis",
                "direction",
                "binding",
                "baseline_binding",
                "evidence",
                "observed",
                "policy_grade",
                "lp_objective",
                "canonical_changed",
                "donor_price_caveat",
            }:
                continue
            number = float(value)
            assert math.isfinite(number), (row["province"], field)
