"""Contract tests for the trade-enabled facility residue supply v5."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from workflows.national.facilities import facility_residue_supply_v5 as local
from workflows.national.facilities import (
    facility_residue_supply_v5_trade as trade,
)

REQUIRED = (
    "facility_residue_supply.csv",
    "province_residue_supply.csv",
    "summary.json",
    "manifest.json",
)
requires_artifacts = pytest.mark.skipif(
    not all((trade.OUTPUT_DIR / name).exists() for name in REQUIRED),
    reason="trade facility residue v5 has not been generated",
)


@requires_artifacts
def test_trade_facility_contract() -> None:
    summary = json.loads(
        (trade.OUTPUT_DIR / "summary.json").read_text(encoding="utf-8")
    )
    assert summary["artifact"] == "facility_residue_supply_v5_trade"
    assert summary["scenario"] == "published_plus_proxy"
    assert summary["validation"]["failed_check_count"] == 0
    assert summary["trade_sensitivity"]["enabled"] is True
    assert summary["national_totals"]["process_residue_odt"] == pytest.approx(
        27_816_973.784, abs=1.0
    )


@requires_artifacts
def test_trade_process_shifts_to_bc_and_conserves() -> None:
    local_rows = pd.read_csv(local.OUTPUT_DIR / "province_residue_supply.csv")
    trade_rows = pd.read_csv(trade.OUTPUT_DIR / "province_residue_supply.csv")
    local_rows = local_rows[local_rows["residue_type"] == "process_residue"]
    trade_rows = trade_rows[trade_rows["residue_type"] == "process_residue"]
    local_pj = dict(zip(local_rows["province"], local_rows["residue_pj"]))
    trade_pj = dict(zip(trade_rows["province"], trade_rows["residue_pj"]))
    assert trade_pj["BC"] > local_pj["BC"] + 50.0
    assert trade_pj["AB"] < local_pj["AB"] - 50.0
    assert sum(trade_pj.values()) == pytest.approx(
        sum(local_pj.values()), rel=1e-9
    )
    assert (trade_rows["residue_pj"] >= 0.0).all()
