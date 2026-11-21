"""Tests for the Forest-Sector LP v1 synthesis against the full ledger."""

from __future__ import annotations

import pytest

from workflows.national.harvest import forest_lp_v1 as lp


@pytest.fixture(scope="module")
def ledger_rows() -> list[dict[str, str]]:
    return lp._read_csv(lp.LEDGER_PATH)


@pytest.fixture(scope="module")
def ledger_validation(ledger_rows: list[dict[str, str]]) -> dict[str, object]:
    return lp.validate_ledger(ledger_rows)


def test_ledger_separates_observed_and_model_input(
    ledger_validation: dict[str, object],
) -> None:
    assert ledger_validation["rows"] == 2_888
    assert ledger_validation["mirror_pairs"] == 100
    assert ledger_validation["unpaired_mirror_keys"] == 0
    assert ledger_validation["observed_rows"] == 200
    assert ledger_validation["model_input_rows"] == 2_688
    assert ledger_validation["delivered_log_revenue_rows"] == 2_075
    assert ledger_validation["delivered_log_revenue_model_input_rows"] == 2_075
    assert ledger_validation["delivered_log_revenue_observed_rows"] == 0


def test_cost_revenue_coverage_is_not_estimated(
    ledger_rows: list[dict[str, str]],
) -> None:
    coverage = lp._read_json(lp.COVERAGE_PATH)
    cost_rows, cost_summary = lp.build_cost_revenue_rows(ledger_rows, coverage)

    by_jurisdiction = {row.jurisdiction: row for row in cost_rows}
    assert set(by_jurisdiction) == {"BC", "QC"}
    assert by_jurisdiction["BC"].observed_rows == 200
    assert by_jurisdiction["BC"].model_input_rows == 0
    assert by_jurisdiction["QC"].observed_rows == 0
    assert by_jurisdiction["QC"].model_input_rows == 2_688
    assert by_jurisdiction["QC"].observed_delivered_log_revenue_present is False
    assert by_jurisdiction["QC"].full_production_costs_present is False
    assert "delivered_log_revenue=model_input_only_not_usable" in (
        by_jurisdiction["QC"].model_input_flags
    )
    assert all(row.net_value_status == "not_estimated" for row in cost_rows)
    assert len(cost_summary) == 2


def test_allocation_and_residue_checks_pass(ledger_rows: list[dict[str, str]]) -> None:
    allocation_rows, case_summaries, residue_summaries = lp.build_allocation_rows(
        lp.load_factors(), lp.load_operable_areas()
    )
    assert len(allocation_rows) == 117

    baseline = {
        item["allocation_case"]: item
        for item in case_summaries
        if item["cap_case"] == "documented_ceiling"
    }
    for level, expected in (
        ("100m", 100_000_000.0),
        ("119m", 118_977_339.0),
        ("150m", 150_000_000.0),
    ):
        assert baseline[level]["harvest_sum_m3"] == pytest.approx(expected, abs=2.0)

    residue = {
        item["allocation_case"]: item
        for item in residue_summaries
        if item["cap_case"] == "documented_ceiling"
    }
    assert residue["100m"]["pj_base"] == pytest.approx(109.681, abs=1e-3)
    assert residue["119m"]["pj_base"] == pytest.approx(130.981, abs=1e-3)
    assert residue["150m"]["pj_base"] == pytest.approx(166.801, abs=1e-3)

    ledger_validation = lp.validate_ledger(ledger_rows)
    coverage = lp._read_json(lp.COVERAGE_PATH)
    cost_rows, _ = lp.build_cost_revenue_rows(ledger_rows, coverage)
    checks = lp.verify_synthesis(
        allocation_rows,
        case_summaries,
        residue_summaries,
        ledger_validation,
        cost_rows,
    )
    assert all(check["status"] == "pass" for check in checks)
