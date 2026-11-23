"""Contract tests for national product chain v3 total residue."""

from __future__ import annotations

import json
import math

import pandas as pd
import pytest

from workflows.national.facilities import national_product_chain_v3 as v3

REQUIRED = (
    "product_chain_by_province.csv",
    "residue_scenario_coefficients.csv",
    "measured_coefficient_replacement.csv",
    "summary.json",
    "manifest.json",
)
requires_artifacts = pytest.mark.skipif(
    not all((v3.OUTPUT_DIR / name).exists() for name in REQUIRED),
    reason="product chain v3 has not been generated",
)


@pytest.fixture(scope="module")
def rows() -> pd.DataFrame:
    return pd.read_csv(v3.OUTPUT_DIR / "product_chain_by_province.csv")


@pytest.fixture(scope="module")
def summary() -> dict[str, object]:
    return json.loads(
        (v3.OUTPUT_DIR / "summary.json").read_text(encoding="utf-8")
    )


@requires_artifacts
def test_products_equal_v2(rows) -> None:
    v2 = pd.read_csv(v3.V2_CSV).set_index("province")
    current = rows.set_index("province")
    assert set(current.index) == set(v2.index)
    for field in v3.PRODUCT_FIELDS:
        assert current[field].to_dict() == pytest.approx(
            v2[field].to_dict(), rel=1e-12, abs=1e-9
        )


@requires_artifacts
def test_measured_total_residue_conservation(rows) -> None:
    measured = rows[rows["coefficient_tier"] == "measured_bucking_pilot"]
    assert set(measured["province"]) == {"BC", "QC", "NB", "YT"}
    for row in measured.itertuples(index=False):
        assert row.harvest_residue_total_gross_odt == pytest.approx(
            row.harvest_residue_stem_gross_odt
            + row.harvest_residue_branch_foliage_odt,
            rel=1e-9,
            abs=1e-6,
        )
        assert row.harvest_residue_gross_odt == pytest.approx(
            row.harvest_residue_total_gross_odt, rel=1e-12, abs=1e-6
        )
        assert row.harvest_residue_recoverable_odt == pytest.approx(
            row.harvest_residue_total_gross_odt * 0.60,
            rel=1e-9,
            abs=1e-6,
        )
        assert row.harvest_residue_unrecovered_odt == pytest.approx(
            row.harvest_residue_total_gross_odt * 0.40,
            rel=1e-9,
            abs=1e-6,
        )
        assert row.harvest_residue_recoverable_total_odt_r035 == pytest.approx(
            row.harvest_residue_total_gross_odt * 0.35,
            rel=1e-9,
            abs=1e-6,
        )
        assert row.harvest_residue_recoverable_total_odt_r060 == pytest.approx(
            row.harvest_residue_total_gross_odt * 0.60,
            rel=1e-9,
            abs=1e-6,
        )
        assert row.harvest_residue_recoverable_total_odt_r075 == pytest.approx(
            row.harvest_residue_total_gross_odt * 0.75,
            rel=1e-9,
            abs=1e-6,
        )
        assert row.total_residue_gross_odt == pytest.approx(
            row.process_residue_odt + row.harvest_residue_total_gross_odt,
            rel=1e-9,
            abs=1e-6,
        )


@requires_artifacts
def test_donor_rows_keep_stem_residue(rows) -> None:
    donor = rows[rows["coefficient_tier"] == "donor_coefficient"]
    assert len(donor) == 9
    assert (donor["harvest_residue_branch_foliage_odt"] == 0.0).all()
    assert (
        donor["harvest_residue_total_gross_odt"]
        == donor["harvest_residue_stem_gross_odt"]
    ).all()
    assert (donor["harvest_residue_component_scope"] == "stem_only_donor").all()


@requires_artifacts
def test_scenario_coefficients(rows, summary) -> None:
    scenarios = pd.read_csv(v3.OUTPUT_DIR / "residue_scenario_coefficients.csv")
    assert set(scenarios["province"]) == {"BC", "QC", "NB", "YT"}
    assert list(scenarios["reference_recovery_factor"].unique()) == [0.60]
    for row in scenarios.itertuples(index=False):
        assert row.total_residue_gross_odt_per_m3 == pytest.approx(
            row.stem_residue_odt_per_m3 + row.branch_foliage_odt_per_m3,
            rel=1e-9,
            abs=1e-12,
        )
        assert row.recoverable_odt_per_m3_r035 < row.recoverable_odt_per_m3_r060
        assert row.recoverable_odt_per_m3_r060 < row.recoverable_odt_per_m3_r075
        for factor, suffix in ((0.35, "r035"), (0.60, "r060"), (0.75, "r075")):
            assert getattr(
                row, f"recoverable_odt_per_m3_{suffix}"
            ) == pytest.approx(
                row.total_residue_gross_odt_per_m3 * factor,
                rel=1e-9,
                abs=1e-12,
            )
    assert summary["residue_components"]["calibrated_recovery_factor"] is False
    assert summary["stem_vs_total_residue"]["products_unchanged"] is True
    assert summary["labels"] == {
        "model_estimate": True,
        "observed": False,
        "policy_grade": False,
        "national_claim": False,
    }


@requires_artifacts
def test_no_nan_or_negative(rows) -> None:
    numeric = rows.select_dtypes(include=["number"])
    assert not numeric.isna().any().any()
    assert not (numeric < 0.0).any().any()
    assert all(
        math.isfinite(float(value))
        for value in rows["harvest_residue_gross_odt"]
    )
