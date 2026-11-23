"""Contract tests for the E1+E2c trade facility residue supply v7."""

from __future__ import annotations

import json
import math

import pandas as pd
import pytest

from workflows.national.facilities import (
    facility_residue_supply_v7_trade as supply,
)

REQUIRED = (
    "province_residue_supply.csv",
    "facility_residue_supply.csv",
    "national_residue_summary.csv",
    "summary.json",
    "manifest.json",
    "FACILITY_RESIDUE_SUPPLY_V7_TRADE.md",
)
requires_artifacts = pytest.mark.skipif(
    not all((supply.OUTPUT_DIR / name).exists() for name in REQUIRED),
    reason="facility residue supply v7 trade has not been generated",
)


@requires_artifacts
def test_trade_supply_labels_and_invariance() -> None:
    summary = json.loads(
        (supply.OUTPUT_DIR / "summary.json").read_text(encoding="utf-8")
    )
    assert summary["artifact"] == supply.ARTIFACT
    assert summary["status"] == "pass"
    assert summary["validation"]["failed_check_count"] == 0
    assert summary["labels"]["model_estimate"] is True
    assert summary["labels"]["observed"] is False
    assert summary["labels"]["policy_grade"] is False
    assert summary["labels"]["national_claim"] is False
    assert summary["labels"]["official_osecan_changed"] is False
    assert summary["product_chain"]["artifact"] == "national_product_chain_v5"
    assert summary["trade_sensitivity"]["assignment_artifact"] == (
        "national_facility_assignment_v5"
    )

    local = json.loads(
        (
            supply.DATA_ROOT
            / "derived/national/facilities/facility_residue_supply_v7"
            / "summary.json"
        ).read_text(encoding="utf-8")
    )
    local_totals = local["national_totals"]
    trade_totals = summary["national_totals"]
    assert trade_totals["process_residue_pj"] == pytest.approx(
        local_totals["process_residue_pj"], rel=1e-12
    )
    assert trade_totals["harvest_residue_gross_pj"] == pytest.approx(
        local_totals["harvest_residue_gross_pj"], rel=1e-12
    )
    assert trade_totals["process_residue_odt"] == pytest.approx(
        local_totals["process_residue_odt"], rel=1e-12
    )


@requires_artifacts
def test_bc_process_reallocation_and_manifest() -> None:
    rows = pd.read_csv(supply.OUTPUT_DIR / "province_residue_supply.csv")
    bc = rows[
        (rows["province"] == "BC")
        & (rows["residue_type"] == "process_residue")
    ].iloc[0]
    local_rows = pd.read_csv(
        supply.DATA_ROOT
        / "derived/national/facilities/facility_residue_supply_v7"
        / "province_residue_supply.csv"
    )
    local_bc = local_rows[
        (local_rows["province"] == "BC")
        & (local_rows["residue_type"] == "process_residue")
    ].iloc[0]
    assert bc["residue_pj"] > local_bc["residue_pj"]
    assert bc["residue_pj"] == pytest.approx(253.214460183, rel=1e-9)
    assert bc["residue_province_basis"] == "facility_province"
    assert math.isfinite(float(bc["residue_pj"]))

    manifest = json.loads(
        (supply.OUTPUT_DIR / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["artifact"] == supply.ARTIFACT
    assert manifest["labels"] == json.loads(
        (supply.OUTPUT_DIR / "summary.json").read_text(encoding="utf-8")
    )["labels"]
    for name, info in manifest["outputs"].items():
        path = supply.OUTPUT_DIR / name
        assert path.is_file()
        assert path.stat().st_size == info["bytes"]
