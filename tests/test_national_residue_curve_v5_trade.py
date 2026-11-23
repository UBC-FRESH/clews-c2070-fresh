"""Contract tests for the trade-enabled national residue curve v5."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from workflows.national.facilities import national_residue_curve_v5 as local
from workflows.national.facilities import (
    national_residue_curve_v5_trade as trade,
)

REQUIRED = (
    "national_residue_curve.csv",
    "province_residue_curve.csv",
    "summary.json",
    "manifest.json",
)
requires_artifacts = pytest.mark.skipif(
    not all((trade.OUTPUT_DIR / name).exists() for name in REQUIRED),
    reason="trade national residue curve v5 has not been generated",
)


@requires_artifacts
def test_national_totals_match_local() -> None:
    local_summary = json.loads(
        (local.OUTPUT_DIR / "summary.json").read_text(encoding="utf-8")
    )
    trade_summary = json.loads(
        (trade.OUTPUT_DIR / "summary.json").read_text(encoding="utf-8")
    )
    assert trade_summary["artifact"] == "national_residue_curve_v5_trade"
    assert trade_summary["validation"]["passed"] is True
    assert trade_summary["trade_sensitivity"]["enabled"] is True
    for case in (
        "process_availability_025",
        "process_availability_050",
        "process_availability_075",
    ):
        assert trade_summary["scenario_totals"]["process"][case][
            "residue_pj"
        ] == pytest.approx(
            local_summary["scenario_totals"]["process"][case]["residue_pj"],
            rel=1e-12,
        )
    for case in (
        "harvest_recovery_035",
        "harvest_recovery_060",
        "harvest_recovery_075",
    ):
        assert trade_summary["rsyc_comparison"]["harvest_cases"][case][
            "recoverable_pj"
        ] == pytest.approx(
            local_summary["rsyc_comparison"]["harvest_cases"][case][
                "recoverable_pj"
            ],
            rel=1e-12,
        )


@requires_artifacts
def test_bc_process_rises() -> None:
    local_summary = json.loads(
        (local.OUTPUT_DIR / "summary.json").read_text(encoding="utf-8")
    )
    trade_summary = json.loads(
        (trade.OUTPUT_DIR / "summary.json").read_text(encoding="utf-8")
    )
    assert (
        trade_summary["province_totals"]["BC"]["process_availability_050_pj"]
        > local_summary["province_totals"]["BC"][
            "process_availability_050_pj"
        ]
        + 20.0
    )
    frame = pd.read_csv(trade.OUTPUT_DIR / "national_residue_curve.csv")
    numeric = frame.select_dtypes(include=["number"])
    assert not numeric.isna().any().any()
    assert not (numeric < 0.0).any().any()
