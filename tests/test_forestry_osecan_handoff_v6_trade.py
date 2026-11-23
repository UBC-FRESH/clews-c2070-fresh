"""Contract tests for the trade-enabled OSeCAN handoff v6."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from workflows.national.facilities import forestry_osecan_handoff_v6 as local
from workflows.national.facilities import (
    forestry_osecan_handoff_v6_trade as trade,
)

REQUIRED = (
    "forestry_residue_handoff.csv",
    "summary.json",
    "manifest.json",
)
requires_artifacts = pytest.mark.skipif(
    not all((trade.OUTPUT_DIR / name).exists() for name in REQUIRED),
    reason="trade OSeCAN handoff v6 has not been generated",
)


@requires_artifacts
def test_trade_handoff_contract() -> None:
    summary = json.loads(
        (trade.OUTPUT_DIR / "summary.json").read_text(encoding="utf-8")
    )
    assert summary["artifact"] == "forestry_osecan_handoff_v6_trade"
    assert summary["validation"]["passed"] is True
    assert summary["osecan_integrity"]["unchanged"] is True
    assert summary["trade_sensitivity"]["enabled"] is True
    assert summary["supply_curve"]["case_tier"][
        "harvest/harvest_recovery_060"
    ] == "T4"
    assert summary["supply_curve"]["case_tier"][
        "process/process_availability_050"
    ] == "above_T4"


@requires_artifacts
def test_harvest_identical_and_process_shifts() -> None:
    local_frame = pd.read_csv(
        local.OUTPUT_DIR / "forestry_residue_handoff.csv"
    )
    trade_frame = pd.read_csv(
        trade.OUTPUT_DIR / "forestry_residue_handoff.csv"
    )
    total = lambda frame: frame[
        frame["residue_class"].isin(["TOTAL", "ALL"])
    ]
    local_total = total(local_frame)
    trade_total = total(trade_frame)
    keys = ["zone_id", "stream", "availability_case"]
    local_agg = local_total.groupby(keys, as_index=False)["pj"].sum()
    trade_agg = trade_total.groupby(keys, as_index=False)["pj"].sum()
    merge = local_agg.merge(
        trade_agg,
        on=keys,
        suffixes=("_local", "_trade"),
    )
    assert len(merge) == len(local_agg) == len(trade_agg)
    harvest = merge[merge["stream"] == "harvest"]
    assert (
        harvest["pj_local"] - harvest["pj_trade"]
    ).abs().max() < 1e-9
    process = merge[merge["stream"] == "process"]
    bc = process[process["zone_id"] == "B2"].set_index("availability_case")
    assert (
        bc.loc["process_availability_025", "pj_trade"]
        > bc.loc["process_availability_025", "pj_local"]
    )
