"""Regression tests for multi-case greenfield break-even thresholds v1."""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path

import pytest

from workflows.national.facilities import (
    greenfield_breakeven_capital_threshold_v1 as v1,
)
from workflows.national.facilities import (
    greenfield_breakeven_capital_threshold_v1_cases as threshold,
)

OUTPUT_DIR = threshold.OUTPUT_DIR
SUMMARY_JSON = OUTPUT_DIR / "summary.json"
MANIFEST_JSON = OUTPUT_DIR / "manifest.json"
THRESHOLD_CSV = OUTPUT_DIR / "breakeven_thresholds.csv"
REPORT_MD = OUTPUT_DIR / "GREENFIELD_BREAKEVEN_CAPITAL_THRESHOLD_V1_CASES.md"


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _optional_float(value: str) -> float | None:
    return None if value == "" else float(value)


@pytest.fixture(scope="module")
def summary() -> dict:
    with SUMMARY_JSON.open("r", encoding="utf-8") as handle:
        return json.load(handle)


@pytest.fixture(scope="module")
def rows() -> list[dict[str, str]]:
    return _read_csv(THRESHOLD_CSV)


def _by_case(rows: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = {
        case: [] for case in threshold.CASES
    }
    for row in rows:
        grouped[row["case"]].append(row)
    return grouped


def test_outputs_fields_manifest_and_labels(summary, rows) -> None:
    for path in (SUMMARY_JSON, MANIFEST_JSON, THRESHOLD_CSV, REPORT_MD):
        assert path.is_file(), path
    assert list(rows[0]) == threshold.THRESHOLD_FIELDS
    assert len(rows) == len(threshold.CASES) * len(threshold.K_VALUES)
    assert [row["case"] for row in rows] == [
        case for case in threshold.CASES for _ in threshold.K_VALUES
    ]
    manifest = json.loads(MANIFEST_JSON.read_text(encoding="utf-8"))
    assert manifest["artifact"] == threshold.ARTIFACT
    assert manifest["artifact_version"] == threshold.ARTIFACT_VERSION
    assert manifest["status"] == "complete"
    assert manifest["labels"] == summary["labels"] == threshold.LABELS
    for name, entry in manifest["outputs"].items():
        path = OUTPUT_DIR / name
        assert path.is_file(), name
        assert path.stat().st_size == entry["bytes"], name
        assert v1.v9._sha256(path) == entry["sha256"], name
    for relative, expected_hash in manifest["inputs"].items():
        path = threshold.REPO_ROOT / relative
        assert path.is_file(), relative
        assert v1.v9._sha256(path) == expected_hash, relative
    assert summary["validation"]["all_passed"] is True
    assert not summary["validation"]["failures"]


def test_labels_on_every_row(rows) -> None:
    assert rows
    for row in rows:
        assert row["evidence_label"] == "model_estimate"
        assert row["sensitivity_label"] == "transport_only"
        assert row["investment_advice_label"] == "not_investment_advice"
        assert row["capital_cost_claim"] == "false"
        assert row["observed"] == "false"
        assert row["policy_grade"] == "false"


def test_k0_baseline_per_case(rows) -> None:
    for case, case_rows in _by_case(rows).items():
        k0 = case_rows[0]
        assert int(k0["k"]) == 0, case
        assert float(k0["transport_saving_vs_k0_cad_y"]) == 0.0, case
        assert float(k0["module_served_m3_y"]) == 0.0, case
        assert k0["breakeven_annualized_capital_cad_per_m3_y"] == "", case
        assert k0["marginal_threshold_status"] == "baseline", case


def test_cumulative_and_marginal_arithmetic(rows) -> None:
    for case, case_rows in _by_case(rows).items():
        baseline_cost = float(case_rows[0]["transport_cost_cad_y"])
        previous_cost = baseline_cost
        previous_volume = 0.0
        previous_saving = 0.0
        for row in case_rows[1:]:
            k = int(row["k"])
            assert int(row["selected_module_count"]) == k
            assert row["added_module_id"]
            cost = float(row["transport_cost_cad_y"])
            volume = float(row["module_served_m3_y"])
            saving = float(row["transport_saving_vs_k0_cad_y"])
            threshold_value = float(
                row["breakeven_annualized_capital_cad_per_m3_y"]
            )
            assert cost <= previous_cost + 1e-6, (case, k)
            assert volume >= previous_volume - 1e-6, (case, k)
            assert saving >= previous_saving - 1e-6, (case, k)
            assert math.isclose(
                saving, baseline_cost - cost, abs_tol=1e-6
            ), (case, k)
            assert math.isclose(
                float(row["transport_saving_vs_k0_b_cad_y"]),
                saving / 1e9,
                rel_tol=1e-8,
                abs_tol=1e-9,
            ), (case, k)
            assert math.isclose(
                float(row["module_served_mm3_y"]),
                volume / 1e6,
                rel_tol=1e-8,
                abs_tol=1e-9,
            ), (case, k)
            assert math.isclose(
                threshold_value,
                saving / volume,
                rel_tol=1e-9,
                abs_tol=1e-6,
            ), (case, k)
            assert threshold_value >= 0.0

            marginal_saving = float(
                row["marginal_transport_saving_cad_y"]
            )
            marginal_volume = float(
                row["marginal_module_volume_m3_y"]
            )
            assert math.isclose(
                marginal_saving,
                previous_cost - cost,
                abs_tol=1e-6,
            ), (case, k)
            assert math.isclose(
                marginal_volume,
                volume - previous_volume,
                abs_tol=1e-6,
            ), (case, k)
            status = row["marginal_threshold_status"]
            marginal = _optional_float(
                row["marginal_breakeven_capital_cad_per_m3_y"]
            )
            if marginal_volume <= threshold.v1.ZERO_VOLUME_M3:
                assert status == "zero_marginal_module_volume", (case, k)
                assert marginal is None
            else:
                assert status in {
                    "defined",
                    "thin_marginal_module_volume",
                }, (case, k)
                assert marginal is not None
                assert math.isclose(
                    marginal,
                    marginal_saving / marginal_volume,
                    rel_tol=1e-9,
                    abs_tol=1e-6,
                ), (case, k)
            previous_cost = cost
            previous_volume = volume
            previous_saving = saving


def test_119_parity_with_single_case_artifact(rows) -> None:
    reference = {
        int(row["k"]): row
        for row in _read_csv(threshold.PARITY_CSV)
    }
    for row in _by_case(rows)[threshold.PRIMARY_CASE]:
        k = int(row["k"])
        expected = reference[k]
        for field in v1.THRESHOLD_FIELDS:
            actual = row[field]
            if actual == "":
                assert expected[field] == "", (k, field)
            elif field in {
                "k",
                "selected_module_count",
            }:
                assert int(actual) == int(expected[field]), (k, field)
            elif field in {
                "added_module_id",
                "marginal_threshold_status",
                "evidence_label",
                "sensitivity_label",
                "investment_advice_label",
            } or field in {"capital_cost_claim", "observed", "policy_grade"}:
                assert actual == expected[field], (k, field)
            else:
                assert math.isclose(
                    float(actual),
                    float(expected[field]),
                    abs_tol=1e-6,
                ), (k, field)


def test_key_thresholds(summary, rows) -> None:
    grouped = _by_case(rows)
    hundred = {int(row["k"]): row for row in grouped["100m"]}
    hundred_fifty = {int(row["k"]): row for row in grouped["150m"]}

    assert float(
        hundred[1]["breakeven_annualized_capital_cad_per_m3_y"]
    ) == pytest.approx(63.003023374, abs=1e-9)
    assert float(
        hundred[20]["breakeven_annualized_capital_cad_per_m3_y"]
    ) == pytest.approx(37.804199804, abs=1e-9)
    assert float(
        hundred[20]["transport_saving_vs_k0_b_cad_y"]
    ) == pytest.approx(1.144198638, abs=1e-9)
    assert float(
        hundred[20]["module_served_mm3_y"]
    ) == pytest.approx(30.266442458, abs=1e-9)

    assert float(
        hundred_fifty[1]["breakeven_annualized_capital_cad_per_m3_y"]
    ) == pytest.approx(74.088741349, abs=1e-9)
    assert float(
        hundred_fifty[20]["breakeven_annualized_capital_cad_per_m3_y"]
    ) == pytest.approx(46.472539703, abs=1e-9)
    assert float(
        hundred_fifty[20]["transport_saving_vs_k0_b_cad_y"]
    ) == pytest.approx(2.420858470, abs=1e-9)
    assert float(
        hundred_fifty[20]["module_served_mm3_y"]
    ) == pytest.approx(52.092235223, abs=1e-9)

    headlines = {item["case"]: item for item in summary["case_headline"]}
    assert headlines["100m"]["minimum_cumulative_threshold"]["k"] == 19
    assert headlines["100m"]["minimum_cumulative_threshold"][
        "cad_per_m3_y"
    ] == pytest.approx(37.136699426, abs=1e-9)
    assert headlines["100m"]["zero_marginal_module_volume_k"] == [17, 20]
    assert headlines["100m"]["thin_marginal_module_volume_k"] == [14]
    assert headlines["119m"]["minimum_cumulative_threshold"]["k"] == 4
    assert headlines["119m"]["zero_marginal_module_volume_k"] == [14, 19]
    assert headlines["119m"]["thin_marginal_module_volume_k"] == [18]
    assert headlines["150m"]["minimum_cumulative_threshold"]["k"] == 18
    assert headlines["150m"]["minimum_cumulative_threshold"][
        "cad_per_m3_y"
    ] == pytest.approx(45.306468855, abs=1e-9)
    assert headlines["150m"]["zero_marginal_module_volume_k"] == [15, 20]
    assert headlines["150m"]["thin_marginal_module_volume_k"] == []
    assert summary["method"]["capital_cost_estimated"] is False


def test_no_nan_or_negative(rows) -> None:
    numeric_fields = [
        "transport_cost_cad_y",
        "transport_saving_vs_k0_cad_y",
        "transport_saving_vs_k0_b_cad_y",
        "module_served_m3_y",
        "module_served_mm3_y",
        "breakeven_annualized_capital_cad_per_m3_y",
        "marginal_transport_saving_cad_y",
        "marginal_transport_saving_b_cad_y",
        "marginal_module_volume_m3_y",
        "marginal_module_volume_mm3_y",
        "marginal_breakeven_capital_cad_per_m3_y",
    ]
    for row in rows:
        for field in numeric_fields:
            value = _optional_float(row[field])
            if value is None:
                continue
            assert math.isfinite(value), (row["case"], row["k"], field)
            assert value >= 0.0, (row["case"], row["k"], field)


def test_report_disclaimers() -> None:
    report = REPORT_MD.read_text(encoding="utf-8")
    normalized = " ".join(report.lower().replace("*", "").split())
    assert "100/119/150" in report
    assert "model_estimate" in report
    assert "transport_only" in report
    assert "not_investment_advice" in report
    assert "not a capital cost estimate" in normalized
    assert "break-even threshold" in normalized
    assert "no national claim" in normalized
