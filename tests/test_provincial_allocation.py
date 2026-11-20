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
    load_operable_proxy,
    load_province_basis,
    solve_allocation,
    with_managed_intensity_cap,
    with_operable_intensity_cap,
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


def test_operable_proxy_areas_and_missing_fallback() -> None:
    areas = load_operable_proxy()
    assert areas["PE"] is None
    assert areas["NU"] is None
    assert areas["BC"] == pytest.approx(49_257_580.364, abs=1.0)
    assert sum(value for value in areas.values() if value is not None) == pytest.approx(
        144_689_810.131, abs=1.0
    )

    basis = {row.province: row for row in load_province_basis()}
    assert basis["PE"].operable_missing is True
    assert basis["PE"].upper_bound_m3 == pytest.approx(460_000.0)
    assert basis["NU"].operable_missing is True
    assert basis["NU"].upper_bound_m3 == 0.0
    assert basis["BC"].operable_missing is False

    capped = {row.province: row for row in with_operable_intensity_cap(basis.values(), 1.0)}
    assert capped["PE"].upper_bound_m3 == pytest.approx(460_000.0)
    assert "operable_missing_fallback" in capped["PE"].basis
    assert capped["NU"].upper_bound_m3 == 0.0
    assert "operable_missing_fallback" in capped["NU"].basis
    assert capped["BC"].upper_bound_m3 == pytest.approx(45_224_000.0, abs=1_000.0)


def test_operable_intensity_sensitivity() -> None:
    basis = load_province_basis()
    strict = solve_allocation(
        with_operable_intensity_cap(basis, 1.0), 150_000_000.0, label="test_operable_1_0"
    )
    assert strict.status == "infeasible"
    assert strict.deficit_m3 == pytest.approx(16_296_351.859, abs=100.0)

    moderate = solve_allocation(
        with_operable_intensity_cap(basis, 1.2), 150_000_000.0, label="test_operable_1_2"
    )
    assert moderate.status == "infeasible"
    assert moderate.deficit_m3 == pytest.approx(4_491_872.990, abs=100.0)

    for intensity in (1.0, 1.2):
        for total in (100_000_000.0, 118_977_339.0):
            result = solve_allocation(
                with_operable_intensity_cap(basis, intensity),
                total,
                label=f"test_operable_{total}_{intensity}",
            )
            assert result.status == "optimal"
            assert result.deficit_m3 == 0.0
            assert all(row.harvest_m3 <= row.upper_bound_m3 + 2.0 for row in result.rows)


def test_national_operable_envelope_diagnostic() -> None:
    summary = _summary()["thlb_intensity_screen"]
    area = summary["operable_proxy"]["national_operable_proxy_ha"]
    assert area == pytest.approx(144_689_810.131, abs=1.0)
    assert summary["national_envelope"]["1.0"]["deficits_m3"]["150m"] == pytest.approx(
        5_310_189.869, abs=100.0
    )
    assert summary["national_envelope"]["1.2"]["deficits_m3"]["150m"] == 0.0
    assert summary["operable_proxy"]["missing_provinces"] == ["NU", "PE"]
    assert summary["managed_proxy_comparison"]["deficits_m3"]["1.0"]["150m"] == pytest.approx(
        6_113_051.149, abs=100.0
    )


def test_intensity_output_directories_and_summary() -> None:
    summary = _summary()
    screen = summary["thlb_intensity_screen"]
    assert screen["intensity_values_m3_per_ha_yr"] == [1.0, 1.2]
    deficits = screen["deficits_m3"]
    assert deficits["150m"]["1.0"] == pytest.approx(16_296_351.859, abs=100.0)
    assert deficits["150m"]["1.2"] == pytest.approx(4_491_872.990, abs=100.0)
    assert deficits["119m"] == {"1.0": 0.0, "1.2": 0.0}
    assert deficits["100m"] == {"1.0": 0.0, "1.2": 0.0}

    strict_dir = OUTPUT_DIR / "intensity_1.0"
    moderate_dir = OUTPUT_DIR / "intensity_1.2"
    for directory in (strict_dir, moderate_dir):
        assert (directory / "provincial_allocation_summary.json").is_file()
        payload = json.loads(
            (directory / "provincial_allocation_summary.json").read_text(encoding="utf-8")
        )
        assert payload["intensity_m3_per_ha_yr"] in (1.0, 1.2)
        assert payload["cases"]["150m"]["status"] == "infeasible"
        assert payload["national_operable_proxy_ha"] == pytest.approx(144_689_810.131, abs=1.0)
    assert (strict_dir / "100m.csv").is_file()
    assert (strict_dir / "119m.csv").is_file()
    assert not (strict_dir / "150m.csv").exists()
    assert (moderate_dir / "100m.csv").is_file()
    assert (moderate_dir / "119m.csv").is_file()
    assert not (moderate_dir / "150m.csv").exists()

    for path in (strict_dir / "119m.csv", moderate_dir / "119m.csv"):
        with path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        assert len(rows) == 13
        for row in rows:
            assert float(row["harvest_m3"]) <= float(row["upper_bound"]) + 2.0
            if row["province"] in {"PE", "NU"}:
                assert "operable_missing_fallback" in row["basis"]
                assert row["operable_proxy_ha"] == ""
                assert row["operable_missing"] == "true"
            else:
                assert "operable_intensity" in row["basis"]
                assert float(row["operable_proxy_ha"]) > 0.0
                assert row["operable_missing"] == "false"
        nu = next(row for row in rows if row["province"] == "NU")
        assert float(nu["harvest_m3"]) == 0.0


def test_direct_200m_solve_reports_deficit_without_forcing() -> None:
    basis = load_province_basis()
    result = solve_allocation(basis, DIAGNOSTIC_TOTAL_200M, label="test_200m")
    assert result.status == "infeasible"
    assert result.rows == ()
    assert result.deficit_m3 > 0.0
