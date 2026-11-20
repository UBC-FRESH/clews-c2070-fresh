"""Provincial harvest-allocation LP: artifact and model contracts."""

from __future__ import annotations

import csv
import json
import math
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from workflows.national.harvest.provincial_allocation import (
    DIAGNOSTIC_TOTAL_200M,
    NATIONAL_OBSERVED_2024_M3,
    load_province_basis,
    solve_allocation,
    with_managed_intensity_cap,
)

OUTPUT_DIR = REPO_ROOT / "data/derived/national/harvest_basis/provincial_allocation"
CASES = {"100m": 100_000_000.0, "119m": 118_977_339.0, "150m": 150_000_000.0}


def _rows(level: str) -> list[dict[str, str]]:
    path = OUTPUT_DIR / f"{level}.csv"
    assert path.is_file(), f"missing allocation artifact: {path}"
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _summary() -> dict[str, object]:
    path = OUTPUT_DIR / "provincial_allocation_summary.json"
    assert path.is_file(), f"missing summary artifact: {path}"
    return json.loads(path.read_text(encoding="utf-8"))


def test_total_conservation() -> None:
    for level, total in CASES.items():
        harvest_sum = sum(float(row["harvest_m3"]) for row in _rows(level))
        assert harvest_sum == pytest.approx(total, abs=2.0), level


def test_caps_respected_and_basis_recorded() -> None:
    for level in CASES:
        for row in _rows(level):
            harvest = float(row["harvest_m3"])
            upper_bound = float(row["upper_bound"])
            assert harvest >= -1.0
            assert harvest <= upper_bound + 2.0, (level, row["province"])
            assert row["basis"].strip(), (level, row["province"])


def test_observed_share_objective_is_optimal() -> None:
    summary = _summary()
    for level, total in CASES.items():
        case = summary["cases"][level]
        rows = _rows(level)
        targets = {
            row["province"]: float(row["observed_share"]) * total for row in rows
        }
        l1 = sum(abs(float(row["harvest_m3"]) - targets[row["province"]]) for row in rows)
        assert l1 == pytest.approx(case["achieved_l1_deviation_m3"], abs=2.0)
        assert case["achieved_l1_deviation_m3"] == pytest.approx(
            case["optimal_l1_deviation_m3"], abs=2.0
        )
        assert case["optimal_l1_deviation_m3"] == pytest.approx(
            2.0 * case["status_quo_exceedance_m3"], abs=5.0
        )


def test_150m_feasible_with_bc_nb_reallocation() -> None:
    case = _summary()["cases"]["150m"]
    assert case["status"] == "optimal"
    assert "BC" in case["binding_provinces"]
    assert "NB" in case["binding_provinces"]

    rows = {row["province"]: row for row in _rows("150m")}
    total = 150_000_000.0
    for province in ("BC", "NB"):
        row = rows[province]
        target = float(row["observed_share"]) * total
        assert float(row["harvest_m3"]) < target, province
        assert float(row["harvest_m3"]) == pytest.approx(
            float(row["upper_bound"]), abs=2.0
        ), province

    gains = sum(
        max(0.0, float(row["harvest_m3"]) - float(row["observed_share"]) * total)
        for row in rows.values()
    )
    losses = sum(
        max(0.0, float(row["observed_share"]) * total - float(row["harvest_m3"]))
        for row in rows.values()
    )
    assert gains == pytest.approx(losses, abs=5.0)
    assert case["status_quo_exceedance_m3"] == pytest.approx(9_341_000.0, abs=5_000.0)


def test_200m_is_infeasible_with_exact_deficit() -> None:
    summary = _summary()
    case = summary["cases"]["200m"]
    assert case["status"] == "infeasible"
    assert case["feasible"] is False
    assert case["allocation"] == []
    assert case["deficit_m3"] == pytest.approx(
        DIAGNOSTIC_TOTAL_200M - case["capacity_m3"], abs=1.0
    )
    assert 30_000_000.0 < case["deficit_m3"] < 31_000_000.0
    generous_deficit = DIAGNOSTIC_TOTAL_200M - summary["most_generous_documented_ceiling_m3"]
    assert generous_deficit == pytest.approx(case["deficit_vs_generous_m3"], abs=1.0)
    assert 15_000_000.0 < generous_deficit < 16_000_000.0


def test_no_negative_or_nan_values() -> None:
    observed_sum = 0.0
    share_sum = 0.0
    for level in CASES:
        for row in _rows(level):
            for field in ("harvest_m3", "observed_2024_m3", "upper_bound"):
                value = float(row[field])
                assert math.isfinite(value), (level, row["province"], field)
                assert value >= -1.0, (level, row["province"], field)
            share = float(row["allocated_share"])
            assert math.isfinite(share) and share >= 0.0
        observed_sum = sum(float(row["observed_2024_m3"]) for row in _rows(level))
        share_sum = sum(float(row["allocated_share"]) for row in _rows(level))
        assert observed_sum == pytest.approx(NATIONAL_OBSERVED_2024_M3, abs=1.0)
        assert share_sum == pytest.approx(1.0, abs=1e-6)


def test_managed_intensity_sensitivity() -> None:
    basis = load_province_basis()
    strict = solve_allocation(
        with_managed_intensity_cap(basis, 1.0), 150_000_000.0, label="test_intensity_1_0"
    )
    assert strict.status == "infeasible"
    assert strict.deficit_m3 == pytest.approx(6_113_051.0, abs=100.0)

    moderate = solve_allocation(
        with_managed_intensity_cap(basis, 1.2), 150_000_000.0, label="test_intensity_1_2"
    )
    assert moderate.status == "optimal"
    assert moderate.harvest_sum_m3 == pytest.approx(150_000_000.0, abs=2.0)
    assert all(row.harvest_m3 <= row.upper_bound_m3 + 2.0 for row in moderate.rows)


def test_direct_200m_solve_reports_deficit_without_forcing() -> None:
    basis = load_province_basis()
    result = solve_allocation(basis, DIAGNOSTIC_TOTAL_200M, label="test_200m")
    assert result.status == "infeasible"
    assert result.rows == ()
    assert result.deficit_m3 > 0.0
