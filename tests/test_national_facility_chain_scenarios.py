"""Regression tests for the multi-scenario national facility chain v1."""

from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path

import pytest

from workflows.national.facilities import (
    national_facility_chain_scenarios as scenario,
)

TOTAL_TOLERANCE_M3 = 1e-3
REL_TOL = 1e-9


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> tuple[dict, Path]:
    target = tmp_path_factory.mktemp("national_facility_chain_scenarios")
    summary = scenario.build(output_dir=target)
    return summary, target


@pytest.fixture(scope="module")
def assignments(built) -> list[dict[str, str]]:
    _, target = built
    return _read_csv(target / "scenario_assignment.csv")


@pytest.fixture(scope="module")
def product_residue(built) -> list[dict[str, str]]:
    _, target = built
    return _read_csv(target / "scenario_product_residue.csv")


@pytest.fixture(scope="module")
def summary_rows(built) -> list[dict[str, str]]:
    _, target = built
    return _read_csv(target / "scenario_summary.csv")


@pytest.fixture(scope="module")
def comparison_rows(built) -> list[dict[str, str]]:
    _, target = built
    return _read_csv(target / "scenario_comparison.csv")


def _rows_for(rows: list[dict[str, str]], scenario_name: str) -> list[dict[str, str]]:
    return [row for row in rows if row["scenario"] == scenario_name]


def _reference_rows() -> dict[str, dict[str, str]]:
    rows = _read_csv(
        scenario.PRODUCT_CHAIN_DIR / "product_chain_by_province.csv"
    )
    return {
        row["province"].strip().upper(): row for row in rows
    }


def _expected_totals(scenario_name: str) -> dict[str, float]:
    references = _reference_rows()
    case = {
        item.scenario: item for item in scenario.harvest_cases()
    }[scenario_name]
    harvest = scenario.read_harvest_case(case)
    totals = {
        "feedstock": 0.0,
        "residue_odt": 0.0,
        "harvest_gross_odt": 0.0,
        "harvest_recoverable_odt": 0.0,
        "harvest_unrecovered_odt": 0.0,
    }
    for facility_class in scenario.CLASS_ORDER:
        totals[f"feedstock_{facility_class}"] = 0.0
        totals[f"product_{facility_class}"] = 0.0
    for province, harvest_m3 in harvest.items():
        reference = references[province]
        reference_harvest = float(reference["harvest_m3"])
        ratio = harvest_m3 / reference_harvest if reference_harvest > 0 else 0.0
        for facility_class in scenario.CLASS_ORDER:
            class_feedstock = (
                float(
                    reference[
                        scenario.CLASS_FEEDSTOCK_COLUMN[facility_class]
                    ]
                )
                * ratio
            )
            totals["feedstock"] += class_feedstock
            totals[f"feedstock_{facility_class}"] += class_feedstock
            totals[f"product_{facility_class}"] += (
                float(
                    reference[
                        scenario.CLASS_PRODUCT_COLUMN[facility_class]
                    ]
                )
                * ratio
            )
            totals["residue_odt"] += (
                float(
                    reference[
                        scenario.CLASS_RESIDUE_COLUMN[facility_class]
                    ]
                )
                * ratio
            )
        totals["harvest_gross_odt"] += (
            float(reference["harvest_residue_gross_odt"]) * ratio
        )
        totals["harvest_recoverable_odt"] += (
            float(reference["harvest_residue_recoverable_odt"]) * ratio
        )
        totals["harvest_unrecovered_odt"] += (
            float(reference["harvest_residue_unrecovered_odt"]) * ratio
        )
    return totals


def test_build_writes_all_deliverables(built):
    summary, target = built
    assert summary["status"] == "pass"
    assert summary["validation"]["passed"] is True
    assert summary["validation"]["failed_check_count"] == 0
    for name in (
        "scenario_assignment.csv",
        "scenario_product_residue.csv",
        "scenario_summary.csv",
        "scenario_comparison.csv",
        "summary.json",
        "manifest.json",
        "NATIONAL_FACILITY_CHAIN_SCENARIOS_V1.md",
    ):
        path = target / name
        assert path.is_file(), name
        assert path.stat().st_size > 0, name
    assert summary["artifact"] == scenario.ARTIFACT
    assert summary["artifact_version"] == scenario.ARTIFACT_VERSION
    assert summary["scenario_order"] == list(scenario.SCENARIO_ORDER)


def test_output_schemas(built, assignments, product_residue, summary_rows, comparison_rows):
    assert list(assignments[0]) == scenario.SCENARIO_ASSIGNMENT_FIELDS
    assert list(product_residue[0]) == scenario.SCENARIO_PRODUCT_RESIDUE_FIELDS
    assert list(summary_rows[0]) == scenario.SCENARIO_SUMMARY_FIELDS
    assert list(comparison_rows[0]) == scenario.SCENARIO_COMPARISON_FIELDS
    assert {row["scenario"] for row in assignments} == set(
        scenario.SCENARIO_ORDER
    )
    assert {row["scenario"] for row in product_residue} == set(
        scenario.SCENARIO_ORDER
    )


def test_labels(built, assignments, product_residue):
    summary, _ = built
    assert summary["labels"] == {
        "evidence_label": "model_estimate",
        "observed": False,
        "policy_grade": False,
        "facility_assignment": True,
    }
    for rows in (assignments, product_residue):
        for row in rows:
            assert row["evidence_label"] == "model_estimate"
            assert row["observed"] == "false"
            assert row["policy_grade"] == "false"
    text = " ".join(summary["caveats"])
    assert "legal/AAC" in text
    assert "proxy capacity" in text or "Proxy capacity" in text
    assert "No UI or public OSeCAN" in text
    assert "observed=false" in text


def test_assignment_conservation_by_scenario(built, assignments, summary_rows):
    summary, _ = built
    cases = {case.scenario: case for case in scenario.harvest_cases()}
    for scenario_name in scenario.SCENARIO_ORDER:
        rows = _rows_for(assignments, scenario_name)
        harvest = scenario.read_harvest_case(cases[scenario_name])
        expected = math.fsum(harvest.values())
        assigned = math.fsum(float(row["assigned_m3"]) for row in rows)
        assert assigned == pytest.approx(expected, abs=TOTAL_TOLERANCE_M3)
        csv_summary = _rows_for(summary_rows, scenario_name)
        assert len(csv_summary) == 1
        summary_row = csv_summary[0]
        assert float(summary_row["harvest_m3"]) == pytest.approx(
            expected, abs=TOTAL_TOLERANCE_M3
        )
        assert float(summary_row["assigned_m3"]) + float(
            summary_row["unassigned_m3"]
        ) == pytest.approx(expected, abs=TOTAL_TOLERANCE_M3)
        scenario_summary = summary["scenarios"][scenario_name]
        assert scenario_summary["assigned_m3"] == pytest.approx(
            expected, abs=TOTAL_TOLERANCE_M3
        )
        assert scenario_summary["unassigned_m3"] == 0.0


def test_product_residue_conservation_by_scenario(
    product_residue, summary_rows
):
    for scenario_name in scenario.SCENARIO_ORDER:
        expected = _expected_totals(scenario_name)
        rows = [
            row
            for row in product_residue
            if row["scenario"] == scenario_name
            and row["record_type"] == "facility_process"
        ]
        harvest_rows = [
            row
            for row in product_residue
            if row["scenario"] == scenario_name
            and row["record_type"] == "harvest_residue"
        ]
        assert len(harvest_rows) == len(scenario.PROVINCES)
        feedstock = math.fsum(float(row["feedstock_m3"]) for row in rows)
        residue = math.fsum(float(row["process_residue_odt"]) for row in rows)
        assert feedstock == pytest.approx(
            expected["feedstock"], rel=REL_TOL, abs=1e-3
        )
        assert residue == pytest.approx(
            expected["residue_odt"], rel=REL_TOL, abs=1e-3
        )
        for facility_class in scenario.CLASS_ORDER:
            class_rows = [
                row for row in rows if row["facility_class"] == facility_class
            ]
            class_feedstock = math.fsum(
                float(row["feedstock_m3"]) for row in class_rows
            )
            class_product = math.fsum(
                float(row["product_quantity"]) for row in class_rows
            )
            assert class_feedstock == pytest.approx(
                expected[f"feedstock_{facility_class}"],
                rel=REL_TOL,
                abs=1e-3,
            )
            assert class_product == pytest.approx(
                expected[f"product_{facility_class}"],
                rel=REL_TOL,
                abs=1e-3,
            )
        harvest_gross = math.fsum(
            float(row["harvest_residue_gross_odt"]) for row in harvest_rows
        )
        harvest_recoverable = math.fsum(
            float(row["harvest_residue_recoverable_odt"])
            for row in harvest_rows
        )
        assert harvest_gross == pytest.approx(
            expected["harvest_gross_odt"],
            rel=REL_TOL,
            abs=1e-3,
        )
        assert harvest_recoverable == pytest.approx(
            expected["harvest_recoverable_odt"],
            rel=REL_TOL,
            abs=1e-3,
        )
        summary_row = _rows_for(summary_rows, scenario_name)[0]
        assert float(summary_row["process_residue_pj"]) == pytest.approx(
            residue * scenario.read_lhv()[0] / scenario.PJ_GJ,
            rel=REL_TOL,
            abs=1e-6,
        )
        assert float(summary_row["total_residue_gross_odt"]) == pytest.approx(
            residue + harvest_gross, rel=REL_TOL, abs=1e-3
        )


def test_capacity_bounds_and_no_unbounded_sink(assignments, summary_rows):
    for row in assignments:
        capacity = float(row["capacity_m3_y"])
        assigned = float(row["assigned_m3"])
        assert math.isfinite(capacity) and capacity > 0.0
        assert math.isfinite(assigned) and assigned >= 0.0
        assert assigned <= capacity + 1e-6
        assert float(row["utilization"]) <= 1.0 + 1e-9
        assert row["capacity_status"] in {"published", "proxy"}
        assert row["capacity_evidence"] in {
            scenario.v4.PROXY_EVIDENCE,
            *scenario.v4.PUBLISHED_EVIDENCE,
        }
    by_facility: dict[tuple[str, str], dict[str, float]] = {}
    for row in assignments:
        key = (row["scenario"], row["facility_id"])
        entry = by_facility.setdefault(
            key,
            {"assigned": 0.0, "capacity": float(row["capacity_m3_y"])},
        )
        entry["assigned"] += float(row["assigned_m3"])
        entry["capacity"] = float(row["capacity_m3_y"])
    for (scenario_name, facility_id), entry in by_facility.items():
        assert entry["assigned"] <= entry["capacity"] + 1e-6, (
            scenario_name,
            facility_id,
        )
    for scenario_name in scenario.SCENARIO_ORDER:
        summary_row = _rows_for(summary_rows, scenario_name)[0]
        assert summary_row["global_capacity_binding"] == "false"
        assert float(summary_row["overflow_m3"]) == 0.0
        assert summary_row["overflow_flag"] == "false"


def test_scenario_ordering(summary_rows):
    rows = {row["scenario"]: row for row in summary_rows}
    metrics = (
        "harvest_m3",
        "assigned_m3",
        "capacity_utilization",
        "total_residue_gross_pj",
        "total_residue_recoverable_pj",
        "process_residue_pj",
        "sawlog_product_m3",
        "pulp_product_odt",
        "panel_product_m3",
        "pellet_product_odt",
        "bio_product_mwh",
    )
    for metric in metrics:
        values = [float(rows[name][metric]) for name in scenario.SCENARIO_ORDER]
        assert values[0] < values[1] < values[2], (metric, values)
    assert float(rows["150m"]["harvest_m3"]) == pytest.approx(
        150_000_000.0, abs=1.0
    )
    assert float(rows["100m"]["harvest_m3"]) == pytest.approx(
        100_000_000.0, abs=1.0
    )


def test_proxy_share_reconciles(assignments, summary_rows):
    for scenario_name in scenario.SCENARIO_ORDER:
        rows = _rows_for(assignments, scenario_name)
        assigned = math.fsum(float(row["assigned_m3"]) for row in rows)
        proxy = math.fsum(
            float(row["assigned_m3"])
            for row in rows
            if row["capacity_status"] == "proxy"
        )
        summary_row = _rows_for(summary_rows, scenario_name)[0]
        assert float(summary_row["proxy_m3"]) == pytest.approx(
            proxy, abs=1e-3
        )
        assert float(summary_row["proxy_share_of_assigned"]) == pytest.approx(
            proxy / assigned, abs=1e-9
        )
        share = float(summary_row["proxy_share_of_assigned"])
        assert 0.0 < share < 1.0
        assert float(summary_row["published_m3"]) + float(
            summary_row["proxy_m3"]
        ) == pytest.approx(assigned, abs=1e-3)


def test_no_overflow_unless_true(summary_rows):
    for scenario_name in scenario.SCENARIO_ORDER:
        summary_row = _rows_for(summary_rows, scenario_name)[0]
        unassigned = float(summary_row["unassigned_m3"])
        if unassigned > 1e-6:
            assert summary_row["global_capacity_binding"] == "true"
            assert float(summary_row["capacity_utilization"]) == pytest.approx(
                1.0, abs=1e-6
            )
        else:
            assert summary_row["global_capacity_binding"] == "false"
        assert float(summary_row["overflow_m3"]) == 0.0


def test_intensity_150m_is_separate(built, assignments):
    summary, _ = built
    intensity = summary["intensity_note"]
    statuses = {
        item["intensity_m3_per_ha_yr"]: item["status"]
        for item in intensity["intensity_screens"]
    }
    assert statuses == {1.0: "infeasible", 1.2: "infeasible"}
    assert intensity["documented_ceiling_scenario"] == "150m"
    assert "not an operable-proxy intensity screen" in intensity[
        "documented_ceiling_note"
    ]
    assert {row["scenario"] for row in assignments} == set(
        scenario.SCENARIO_ORDER
    )
    assert "150m_intensity_1.0" not in {
        row["scenario"] for row in assignments
    }


def test_manifest_hashes(built):
    summary, target = built
    manifest = json.loads(
        (target / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["artifact"] == scenario.ARTIFACT
    for name, expected in manifest["outputs"].items():
        path = target / name
        assert path.is_file(), name
        assert path.stat().st_size == expected["bytes"]
        assert _sha256(path) == expected["sha256"]
    for path_text, expected in summary["inputs"].items():
        assert _sha256(scenario.REPO_ROOT / path_text) == expected
