"""Contract tests for national facility chain v5 total residue."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from workflows.national.facilities import national_facility_chain_scenarios_v5 as v5

REQUIRED = (
    "scenario_product_residue.csv",
    "scenario_summary.csv",
    "stem_vs_total_comparison.csv",
    "summary.json",
    "manifest.json",
)
requires_artifacts = pytest.mark.skipif(
    not all((v5.OUTPUT_DIR / name).exists() for name in REQUIRED),
    reason="facility chain v5 has not been generated",
)


@pytest.fixture(scope="module")
def product() -> pd.DataFrame:
    return pd.read_csv(v5.OUTPUT_DIR / "scenario_product_residue.csv")


@pytest.fixture(scope="module")
def summary() -> dict[str, object]:
    return json.loads(
        (v5.OUTPUT_DIR / "summary.json").read_text(encoding="utf-8")
    )


@requires_artifacts
def test_three_harvest_cases(product) -> None:
    assert set(product["scenario"]) == {"100m", "119m", "150m"}
    assert set(product["record_type"]) == {"facility_process", "harvest_residue"}


@requires_artifacts
def test_products_and_process_unchanged(product) -> None:
    stem = pd.read_csv(v5.STEM_DIR / "scenario_product_residue.csv")
    fields = ["feedstock_m3", "product_quantity", "process_residue_odt"]
    current = product[product["record_type"] == "facility_process"].reset_index(
        drop=True
    )
    old = stem[stem["record_type"] == "facility_process"].reset_index(drop=True)
    assert len(current) == len(old)
    for field in fields:
        assert np.allclose(
            current[field].astype(float),
            old[field].astype(float),
            rtol=1e-12,
            atol=1e-9,
        )


@requires_artifacts
def test_harvest_residue_increases(product) -> None:
    stem = pd.read_csv(v5.STEM_DIR / "scenario_summary.csv")
    current = pd.read_csv(v5.OUTPUT_DIR / "scenario_summary.csv")
    merged = current.merge(stem, on="scenario", suffixes=("", "_stem"))
    for scenario in ("100m", "119m", "150m"):
        row = merged[merged["scenario"] == scenario].iloc[0]
        assert row["harvest_residue_gross_pj"] > row["harvest_residue_gross_pj_stem"]
        assert (
            row["harvest_residue_recoverable_pj"]
            > row["harvest_residue_recoverable_pj_stem"]
        )
        assert row["total_residue_gross_pj"] == pytest.approx(
            row["process_residue_pj"] + row["harvest_residue_gross_pj"],
            rel=1e-9,
            abs=1e-6,
        )
    harvest = product[product["record_type"] == "harvest_residue"]
    assert (
        harvest["harvest_residue_gross_odt"].astype(float) >= 0.0
    ).all()
    assert harvest["harvest_residue_gross_odt"].astype(float).sum() > 0.0
    assert (
        harvest["harvest_residue_recoverable_odt"].astype(float)
        <= harvest["harvest_residue_gross_odt"].astype(float)
        + 1e-6
    ).all()


@requires_artifacts
def test_stem_vs_total_comparison() -> None:
    comparison = pd.read_csv(v5.OUTPUT_DIR / "stem_vs_total_comparison.csv")
    national = comparison[comparison["scope"] == "national"]
    products = national[
        national["metric"].isin(
            [
                "solid_product_m3",
                "sawlog_product_m3",
                "panel_product_m3",
                "pulp_product_odt",
                "pellet_product_odt",
                "bio_product_mwh",
                "process_residue_pj",
            ]
        )
    ]
    assert np.allclose(
        products["delta"].astype(float), 0.0, rtol=0.0, atol=1e-6
    )
    harvest = national[
        national["metric"].isin(
            ["harvest_residue_gross_pj", "harvest_residue_recoverable_pj"]
        )
    ]
    assert (harvest["delta"].astype(float) > 0.0).all()


@requires_artifacts
def test_labels_and_finite(summary, product) -> None:
    assert summary["labels"] == {
        "model_estimate": True,
        "observed": False,
        "policy_grade": False,
        "national_claim": False,
    }
    assert summary["validation"]["passed"] is True
    numeric = product.select_dtypes(include=["number"])
    assert not numeric.isna().any().any()
    assert not (numeric < 0.0).any().any()
