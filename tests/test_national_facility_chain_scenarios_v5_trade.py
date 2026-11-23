"""Contract tests for the trade-enabled measured facility chain v5."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from workflows.national.facilities import (
    national_facility_chain_scenarios_v5 as local,
)
from workflows.national.facilities import (
    national_facility_chain_scenarios_v5_trade as trade,
)

REQUIRED = (
    "scenario_product_residue.csv",
    "scenario_summary.csv",
    "summary.json",
    "manifest.json",
)
requires_artifacts = pytest.mark.skipif(
    not all((trade.OUTPUT_DIR / name).exists() for name in REQUIRED),
    reason="trade chain v5 has not been generated",
)


@requires_artifacts
def test_trade_chain_contract() -> None:
    summary = json.loads(
        (trade.OUTPUT_DIR / "summary.json").read_text(encoding="utf-8")
    )
    assert summary["artifact"] == "national_facility_chain_scenarios_v5_trade"
    assert summary["status"] == "pass"
    assert summary["validation"]["passed"] is True
    assert summary["capacity_case"] == "published_plus_proxy"
    assert summary["trade_sensitivity"]["enabled"] is True
    assert summary["product_chain"]["measured_provinces"] == [
        "BC",
        "NB",
        "QC",
        "YT",
    ]
    row = summary["scenarios"]["119m"]
    assert row["cross_border_m3"] == pytest.approx(21_901_937.631, abs=1.0)
    assert row["proxy_share_of_assigned"] == pytest.approx(
        0.3388796, abs=1e-6
    )
    assert row["process_residue_pj"] == pytest.approx(514.614015, abs=1e-5)


@requires_artifacts
def test_trade_matches_local_national_products_and_residue() -> None:
    local_summary = json.loads(
        (local.OUTPUT_DIR / "summary.json").read_text(encoding="utf-8")
    )
    trade_summary = json.loads(
        (trade.OUTPUT_DIR / "summary.json").read_text(encoding="utf-8")
    )
    fields = (
        "sawlog_product_m3",
        "panel_product_m3",
        "pulp_product_odt",
        "pellet_product_odt",
        "bio_product_mwh",
        "process_residue_pj",
        "harvest_residue_gross_pj",
        "harvest_residue_recoverable_pj",
        "total_residue_gross_pj",
        "total_residue_recoverable_pj",
    )
    for scenario in ("100m", "119m", "150m"):
        for field in fields:
            assert trade_summary["scenarios"][scenario][field] == pytest.approx(
                local_summary["scenarios"][scenario][field], rel=1e-12
            )
    product = pd.read_csv(trade.OUTPUT_DIR / "scenario_product_residue.csv")
    numeric = product.select_dtypes(include=["number"])
    assert not numeric.isna().any().any()
    assert not (numeric < 0.0).any().any()
