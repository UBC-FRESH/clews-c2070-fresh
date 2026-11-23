"""Regression tests for greenfield break-even capital thresholds v1."""

from __future__ import annotations

import csv
import itertools
import json
import math
from pathlib import Path

import pytest

from workflows.national.facilities import (
    greenfield_breakeven_capital_threshold_v1 as threshold,
)

OUTPUT_DIR = threshold.OUTPUT_DIR
SUMMARY_JSON = OUTPUT_DIR / "summary.json"
MANIFEST_JSON = OUTPUT_DIR / "manifest.json"
THRESHOLD_CSV = OUTPUT_DIR / "breakeven_thresholds.csv"
REPORT_MD = OUTPUT_DIR / "GREENFIELD_BREAKEVEN_CAPITAL_THRESHOLD_V1.md"


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


def test_outputs_fields_manifest_and_labels(summary, rows) -> None:
    for path in (SUMMARY_JSON, MANIFEST_JSON, THRESHOLD_CSV, REPORT_MD):
        assert path.is_file(), path
    assert list(rows[0]) == threshold.THRESHOLD_FIELDS
    assert [int(row["k"]) for row in rows] == list(threshold.K_VALUES)
    manifest = json.loads(MANIFEST_JSON.read_text(encoding="utf-8"))
    assert manifest["artifact"] == threshold.ARTIFACT
    assert manifest["artifact_version"] == threshold.ARTIFACT_VERSION
    assert manifest["status"] == "complete"
    assert manifest["labels"] == summary["labels"] == threshold.LABELS
    for name, entry in manifest["outputs"].items():
        path = OUTPUT_DIR / name
        assert path.is_file(), name
        assert path.stat().st_size == entry["bytes"], name
        assert threshold.v9._sha256(path) == entry["sha256"], name
    for relative, expected_hash in manifest["inputs"].items():
        path = threshold.REPO_ROOT / relative
        assert path.is_file(), relative
        assert threshold.v9._sha256(path) == expected_hash, relative
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


def test_k0_baseline(rows) -> None:
    k0 = rows[0]
    assert int(k0["k"]) == 0
    assert float(k0["transport_saving_vs_k0_cad_y"]) == 0.0
    assert float(k0["module_served_m3_y"]) == 0.0
    assert k0["breakeven_annualized_capital_cad_per_m3_y"] == ""
    assert k0["marginal_breakeven_capital_cad_per_m3_y"] == ""
    assert k0["marginal_threshold_status"] == "baseline"


def test_cumulative_arithmetic_and_monotonicity(rows) -> None:
    baseline_cost = float(rows[0]["transport_cost_cad_y"])
    previous_cost = baseline_cost
    previous_saving = 0.0
    previous_volume = 0.0
    for row in rows[1:]:
        k = int(row["k"])
        assert int(row["selected_module_count"]) == k
        assert row["added_module_id"]
        cost = float(row["transport_cost_cad_y"])
        volume = float(row["module_served_m3_y"])
        saving = float(row["transport_saving_vs_k0_cad_y"])
        threshold_value = float(
            row["breakeven_annualized_capital_cad_per_m3_y"]
        )
        assert cost <= previous_cost + 1e-6, k
        assert volume >= previous_volume - 1e-6, k
        assert saving >= previous_saving - 1e-6, k
        assert math.isclose(
            saving,
            baseline_cost - cost,
            abs_tol=1e-6,
        )
        assert math.isclose(
            float(row["transport_saving_vs_k0_b_cad_y"]),
            saving / 1e9,
            rel_tol=1e-8,
            abs_tol=1e-9,
        )
        assert math.isclose(
            float(row["module_served_mm3_y"]),
            volume / 1e6,
            rel_tol=1e-8,
            abs_tol=1e-9,
        )
        assert math.isclose(
            threshold_value,
            saving / volume,
            rel_tol=1e-9,
            abs_tol=1e-6,
        )
        assert threshold_value >= 0.0
        previous_cost = cost
        previous_saving = saving
        previous_volume = volume


def test_marginal_arithmetic_and_degenerate_flags(rows) -> None:
    source = _read_csv(threshold.SOURCE_TRADEOFF_CSV)
    thin_volume = threshold.THIN_MARGINAL_SHARE * float(
        source[0]["assigned_m3"]
    )
    zero_k = []
    thin_k = []
    defined_k = []
    for previous, row in itertools.pairwise(rows):
        marginal_saving = float(row["marginal_transport_saving_cad_y"])
        marginal_volume = float(row["marginal_module_volume_m3_y"])
        expected_saving = float(previous["transport_cost_cad_y"]) - float(
            row["transport_cost_cad_y"]
        )
        expected_volume = float(row["module_served_m3_y"]) - float(
            previous["module_served_m3_y"]
        )
        assert math.isclose(
            marginal_saving,
            expected_saving,
            abs_tol=1e-6,
        )
        assert math.isclose(
            marginal_volume,
            expected_volume,
            abs_tol=1e-6,
        )
        assert math.isclose(
            float(row["marginal_transport_saving_b_cad_y"]),
            marginal_saving / 1e9,
            rel_tol=1e-8,
            abs_tol=1e-9,
        )
        assert math.isclose(
            float(row["marginal_module_volume_mm3_y"]),
            marginal_volume / 1e6,
            rel_tol=1e-8,
            abs_tol=1e-9,
        )
        status = row["marginal_threshold_status"]
        value = _optional_float(
            row["marginal_breakeven_capital_cad_per_m3_y"]
        )
        if marginal_volume <= threshold.ZERO_VOLUME_M3:
            zero_k.append(int(row["k"]))
            assert status == "zero_marginal_module_volume"
            assert value is None
        elif marginal_volume < thin_volume:
            thin_k.append(int(row["k"]))
            assert status == "thin_marginal_module_volume"
            assert value is not None
            assert math.isclose(
                value,
                marginal_saving / marginal_volume,
                rel_tol=1e-9,
                abs_tol=1e-6,
            )
        else:
            defined_k.append(int(row["k"]))
            assert status == "defined"
            assert value is not None
            assert math.isclose(
                value,
                marginal_saving / marginal_volume,
                rel_tol=1e-9,
                abs_tol=1e-6,
            )
        if value is not None:
            assert value >= 0.0
    assert zero_k == [14, 19]
    assert thin_k == [18]
    assert defined_k == [
        k for k in range(1, 21) if k not in zero_k + thin_k
    ]


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
            assert math.isfinite(value), (row["k"], field)
            assert value >= 0.0, (row["k"], field)


def test_key_thresholds(summary, rows) -> None:
    by_k = {int(row["k"]): row for row in rows}
    assert float(
        by_k[1]["breakeven_annualized_capital_cad_per_m3_y"]
    ) == pytest.approx(59.474386045, abs=1e-9)
    assert float(
        by_k[20]["breakeven_annualized_capital_cad_per_m3_y"]
    ) == pytest.approx(40.867463448, abs=1e-9)
    assert float(
        by_k[20]["module_served_mm3_y"]
    ) == pytest.approx(36.005969784, abs=1e-9)
    assert float(
        by_k[20]["transport_saving_vs_k0_b_cad_y"]
    ) == pytest.approx(1.471472654, abs=1e-9)
    headline = summary["headline"]
    assert headline["minimum_cumulative_threshold"]["k"] == 4
    assert headline["minimum_cumulative_threshold"][
        "cad_per_m3_y"
    ] == pytest.approx(40.545062954, abs=1e-9)
    assert headline["zero_marginal_module_volume_k"] == [14, 19]
    assert headline["thin_marginal_module_volume_k"] == [18]
    assert summary["method"]["capital_cost_estimated"] is False


def test_report_disclaimers() -> None:
    report = REPORT_MD.read_text(encoding="utf-8")
    normalized = " ".join(report.lower().replace("*", "").split())
    assert "model_estimate" in report
    assert "transport_only" in report
    assert "not_investment_advice" in report
    assert "not a capital cost estimate" in normalized
    assert "break-even threshold" in normalized
    assert "no national claim" in normalized
