"""Regression tests for the assignment-v6 all-zone OSeCAN handoff v4."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd
import pytest

from workflows.national.facilities import forestry_osecan_handoff as v1
from workflows.national.facilities import forestry_osecan_handoff_v3 as v3
from workflows.national.facilities import forestry_osecan_handoff_v4 as v4


def _numeric(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    for column in columns:
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    return frame


@pytest.fixture(scope="module")
def built() -> tuple[dict, Path]:
    summary = json.loads((v4.OUTPUT_DIR / "summary.json").read_text())
    return summary, v4.OUTPUT_DIR


def test_build_deliverables_and_labels(built):
    summary, target = built
    assert summary["artifact"] == "forestry_osecan_handoff_v4"
    assert summary["status"] == "pass"
    assert summary["validation"]["passed"] is True
    assert summary["labels"]["model_estimate"] is True
    assert summary["labels"]["observed"] is False
    assert summary["labels"]["policy_grade"] is False
    assert summary["labels"]["official_osecan_changed"] is False
    assert summary["osecan_integrity"]["unchanged"] is True
    for name in (
        "forestry_residue_handoff.csv",
        "zone_allocation_weights.csv",
        "zone_summary.csv",
        "supply_curve_comparison.csv",
        "summary.json",
        "FORESTRY_OSECAN_HANDOFF_V4.md",
        "manifest.json",
    ):
        assert (target / name).is_file(), name


def test_weights_identical_to_v3(built):
    _, target = built
    current = _numeric(
        pd.read_csv(target / "zone_allocation_weights.csv", dtype=str),
        ["weight", "source_value"],
    )
    previous = _numeric(
        pd.read_csv(v3.OUTPUT_DIR / "zone_allocation_weights.csv", dtype=str),
        ["weight", "source_value"],
    )
    keys = ["province", "zone_id"]
    merged = current.merge(
        previous, on=keys, suffixes=("_v4", "_v3"), validate="one_to_one"
    )
    assert len(merged) == len(previous) == len(current)
    assert (merged["weight_v4"] - merged["weight_v3"]).abs().max() < 1e-12
    assert (merged["weight_method_v4"] == merged["weight_method_v3"]).all()


def test_national_totals_unchanged_and_zone_conservation(built):
    summary, target = built
    rows = _numeric(pd.read_csv(target / "zone_summary.csv", dtype=str), ["pj"])
    for stream in ("process", "harvest"):
        for case, pj in summary["handoff_totals"][stream].items():
            selected = rows[
                (rows["stream"] == stream) & (rows["availability_case"] == case)
            ]
            assert math.isclose(
                selected["pj"].sum(), pj, rel_tol=1e-9, abs_tol=1e-9
            )


def test_process_zone_shift_vs_v3(built):
    _, target = built
    current = _numeric(
        pd.read_csv(target / "zone_summary.csv", dtype=str), ["pj"]
    )
    previous = _numeric(
        pd.read_csv(v3.OUTPUT_DIR / "zone_summary.csv", dtype=str), ["pj"]
    )
    key = ["zone_id", "stream", "availability_case"]
    merged = current.merge(
        previous, on=key, suffixes=("_v4", "_v3"), validate="one_to_one"
    )
    process = merged[
        (merged["stream"] == "process")
        & (merged["availability_case"] == "process_availability_050")
    ]
    assert process["pj_v4"].sum() == pytest.approx(
        process["pj_v3"].sum(), rel=1e-9
    )
    assert abs(process["pj_v4"] - process["pj_v3"]).max() > 1.0
    harvest = merged[merged["stream"] == "harvest"]
    assert (harvest["pj_v4"] - harvest["pj_v3"]).abs().max() < 1e-12


def test_rows_are_model_estimates_and_zones_complete(built):
    _, target = built
    rows = pd.read_csv(target / "forestry_residue_handoff.csv", dtype=str)
    assert (rows["model_estimate"].str.lower() == "true").all()
    assert (rows["observed"].str.lower() == "false").all()
    assert (rows["policy_grade"].str.lower() == "false").all()
    assert (rows["official_osecan_changed"].str.lower() == "false").all()
    assert set(rows["stream"]) == {"process", "harvest"}
    assert "UNMAPPED" not in set(rows["zone_id"])


def test_official_tree_hash_unchanged(built):
    _, target = built
    manifest = json.loads((target / "manifest.json").read_text())
    hashes, digest = v1._hash_tree(v1.OSECAN_DIR)
    assert manifest["osecan_integrity"]["tree_sha256_after"] == digest
    assert manifest["osecan_integrity"]["file_count_after"] == len(hashes)
