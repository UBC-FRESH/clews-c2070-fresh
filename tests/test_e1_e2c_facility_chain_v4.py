"""Contract tests for the E1 + E2c 13-jurisdiction facility chain v4.

Covered contracts:

* 13 jurisdictions with E1 measured / E2c density-calibrated coefficients and
  zero donor product coefficients;
* product-share, class-feedstock and residue conservation;
* the 100/119/150 Mm3 scenarios and 0.35/0.60/0.75 recovery cases;
* the frozen v3.2 (E2 v1) versus v4 (E2c) product/residue comparison;
* the E2 v2 versus E2c per-m3 coefficient equality;
* open-volume screening for SK/NS/PE and explicit ``not_available`` elsewhere;
* OSeCAN handoff v8 / scenarios v7 labels and zone coverage;
* no NaN, infinity or negative numeric values.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from workflows.national.facilities import e1_e2c_coefficients as e2c

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA = REPO_ROOT / "data/derived/national"
COEFFICIENT_DIR = DATA / "facilities/e1_e2c_product_coefficients_v1"
PRODUCT_DIR = DATA / "facilities/national_product_chain_v5"
V32_PRODUCT_DIR = DATA / "facilities/national_product_chain_v4"
CHAIN_DIR = DATA / "facilities/national_facility_chain_scenarios_v7"
CURVE_DIR = DATA / "facilities/national_residue_curve_v7"
HANDOFF_DIR = DATA / "facilities/forestry_osecan_handoff_v8"
SCENARIO_DIR = DATA / "facilities/forestry_osecan_scenarios_v7"

PROVINCES = set(e2c.PROVINCES)
MEASURED = set(e2c.MEASURED_PROVINCES)
IMPUTED = set(e2c.IMPUTED_PROVINCES)
E2C_TIER = e2c.E2_TIER


def _require(path: Path) -> Path:
    if not path.is_file():
        pytest.skip(f"artifact not generated: {path}")
    return path


def _frame(directory: Path, name: str) -> pd.DataFrame:
    return pd.read_csv(_require(directory / name))


def _assert_finite_nonnegative(
    frame: pd.DataFrame, columns: list[str], label: str
) -> None:
    for column in columns:
        assert frame[column].notna().all(), f"{label}.{column} has NaN"
        values = frame[column].to_numpy(dtype=float)
        assert np.isfinite(values).all(), f"{label}.{column} is not finite"
        assert (values >= 0.0).all(), f"{label}.{column} is negative"


@pytest.fixture(scope="module")
def coefficients() -> pd.DataFrame:
    return _frame(COEFFICIENT_DIR, "product_coefficients.csv")


@pytest.fixture(scope="module")
def product() -> pd.DataFrame:
    return _frame(PRODUCT_DIR, "product_chain_by_province.csv")


def test_thirteen_jurisdictions_and_coefficient_source(
    coefficients: pd.DataFrame,
) -> None:
    assert len(coefficients) == 13
    assert set(coefficients["province"]) == PROVINCES
    measured = coefficients[coefficients["province"].isin(MEASURED)]
    imputed = coefficients[coefficients["province"].isin(IMPUTED)]
    assert set(measured["coefficient_tier"]) == {e2c.MEASURED_TIER}
    assert set(imputed["coefficient_tier"]) == {E2C_TIER}
    assert set(measured["product_coefficient_source"]) == {e2c.E1_SOURCE}
    assert set(imputed["product_coefficient_source"]) == {e2c.E2_SOURCE}
    assert not coefficients["product_coefficient_source"].str.contains(
        "donor", case=False
    ).any()
    assert (imputed["density_calibrated"].astype(str).str.lower() == "true").all()
    assert (imputed["density_stems_ha"] > 0.0).all()
    assert set(imputed["density_evidence_class"]) <= {
        "open_inventory",
        "donor",
        "proxy",
    }
    assert coefficients["model_estimate"].astype(str).str.lower().eq(
        "true"
    ).all()
    for field in ("observed", "policy_grade", "national_claim"):
        assert coefficients[field].astype(str).str.lower().eq("false").all()


def test_coefficient_conservation(coefficients: pd.DataFrame) -> None:
    assert np.allclose(
        coefficients[["solid_share", "pulp_share", "other_share"]].sum(axis=1),
        1.0,
        rtol=1e-10,
        atol=1e-12,
    )
    assert np.allclose(
        coefficients["total_residue_odt_per_m3"],
        coefficients["stem_residue_odt_per_m3"]
        + coefficients["branch_foliage_odt_per_m3"],
        rtol=1e-10,
        atol=1e-12,
    )
    for factor, column in (
        (0.35, "recoverable_odt_per_m3_r035"),
        (0.60, "recoverable_odt_per_m3_r060"),
        (0.75, "recoverable_odt_per_m3_r075"),
    ):
        assert np.allclose(
            coefficients[column],
            coefficients["total_residue_odt_per_m3"] * factor,
            rtol=1e-10,
            atol=1e-12,
        )


def test_open_volume_checks(coefficients: pd.DataFrame) -> None:
    open_rows = coefficients[
        coefficients["province"].isin(e2c.OPEN_INVENTORY_PROVINCES)
    ]
    assert set(open_rows["province"]) == set(e2c.OPEN_INVENTORY_PROVINCES)
    assert (open_rows["open_volume_status"] == "within_50pct").all()
    ratio = open_rows["open_volume_ratio"].to_numpy(dtype=float)
    assert ((ratio >= 0.5) & (ratio <= 1.5)).all()
    other = coefficients[
        coefficients["province"].isin(IMPUTED - set(e2c.OPEN_INVENTORY_PROVINCES))
    ]
    assert (other["open_volume_status"] == "not_available").all()
    assert other["open_volume_ratio"].isna().all()


def test_product_chain_thirteen_and_conservation(product: pd.DataFrame) -> None:
    assert len(product) == 13
    assert set(product["province"]) == PROVINCES
    assert set(product.loc[product["province"].isin(MEASURED), "coefficient_tier"]) == {
        e2c.MEASURED_TIER
    }
    assert set(product.loc[product["province"].isin(IMPUTED), "coefficient_tier"]) == {
        E2C_TIER
    }
    assert np.allclose(
        product["sawlog_m3"] + product["pulpwood_m3"] + product["other_log_m3"],
        product["harvest_m3"],
        rtol=1e-9,
        atol=1e-3,
    )
    assert np.allclose(
        product["sawlog_feedstock_m3"]
        + product["pulp_feedstock_m3"]
        + product["panel_feedstock_m3"]
        + product["pellet_feedstock_m3"]
        + product["bio_feedstock_m3"],
        product["harvest_m3"] + product["residue_feedstock_m3"],
        rtol=1e-9,
        atol=1e-3,
    )
    assert np.allclose(
        product["harvest_residue_stem_gross_odt"]
        + product["harvest_residue_branch_foliage_odt"],
        product["harvest_residue_total_gross_odt"],
        rtol=1e-9,
        atol=1e-6,
    )
    assert np.allclose(
        product["process_residue_odt"] + product["harvest_residue_total_gross_odt"],
        product["total_residue_gross_total_odt"],
        rtol=1e-9,
        atol=1e-6,
    )


def test_e1_rows_match_frozen_v3_2(product: pd.DataFrame) -> None:
    v32 = _frame(V32_PRODUCT_DIR, "product_chain_by_province.csv").set_index(
        "province"
    )
    current = product.set_index("province")
    fields = (
        "sawlog_product_m3",
        "pulp_product_odt",
        "panel_product_m3",
        "pellet_product_odt",
        "bio_product_mwh",
        "process_residue_odt",
        "harvest_residue_total_gross_odt",
        "total_residue_gross_total_odt",
    )
    for province in e2c.MEASURED_PROVINCES:
        for field in fields:
            assert float(current.loc[province, field]) == pytest.approx(
                float(v32.loc[province, field]), abs=1e-6
            )


def test_v3_2_vs_v4_and_e2_v2_coefficient_comparison() -> None:
    comparison = _frame(PRODUCT_DIR, "v3_2_vs_v4_comparison.csv")
    assert set(comparison["province"]) == PROVINCES
    assert len(comparison) == 13 * 9
    assert np.allclose(
        comparison["v3_2_value"] + comparison["delta"],
        comparison["v4_value"],
        rtol=1e-10,
        atol=1e-6,
    )
    e2_changed = comparison[comparison["province"].isin(IMPUTED)]
    assert (e2_changed["delta"].abs() > 0.0).any()
    measured_changed = comparison[comparison["province"].isin(MEASURED)]
    assert np.allclose(measured_changed["delta"], 0.0, rtol=0.0, atol=1e-6)

    coefficient_comparison = _frame(
        PRODUCT_DIR, "e2_v2_vs_e2c_coefficients.csv"
    )
    assert len(coefficient_comparison) == len(IMPUTED) * 6
    assert not coefficient_comparison["changed"].any()
    assert np.allclose(
        coefficient_comparison["e2_v2_value"],
        coefficient_comparison["e2c_v3_value"],
        rtol=0.0,
        atol=1e-12,
    )


def test_chain_scenarios_and_recovery_cases() -> None:
    summary = json.loads((CHAIN_DIR / "summary.json").read_text(encoding="utf-8"))
    assert summary["artifact"] == "national_facility_chain_scenarios_v7"
    assert summary["scenario_order"] == ["100m", "119m", "150m"]
    assert summary["labels"]["model_estimate"] is True
    assert summary["labels"]["observed"] is False
    assert summary["labels"]["policy_grade"] is False
    assert summary["labels"]["national_claim"] is False
    assert summary["coefficient_tiers"][E2C_TIER] == 9
    assert summary["coefficient_tiers"]["donor_product_coefficients"] == 0
    scenarios = summary["scenarios"]
    for scenario in ("100m", "119m", "150m"):
        for field in (
            "sawlog_product_m3",
            "pulp_product_odt",
            "panel_product_m3",
            "process_residue_pj",
            "harvest_residue_gross_pj",
            "total_residue_gross_pj",
            "total_residue_recoverable_pj",
        ):
            value = float(scenarios[scenario][field])
            assert math.isfinite(value) and value >= 0.0
    assert (
        scenarios["100m"]["total_residue_gross_pj"]
        < scenarios["119m"]["total_residue_gross_pj"]
        < scenarios["150m"]["total_residue_gross_pj"]
    )
    products = _frame(CHAIN_DIR, "scenario_product_residue.csv")
    harvest = products[products["record_type"] == "harvest_residue"]
    assert len(harvest) == 13 * 3
    assert np.allclose(
        harvest["harvest_residue_recoverable_odt"],
        harvest["harvest_residue_gross_odt"] * 0.60,
        rtol=1e-9,
        atol=1e-6,
    )
    scenarios_df = _frame(CHAIN_DIR, "scenario_summary.csv")
    _assert_finite_nonnegative(
        scenarios_df,
        [
            "sawlog_product_m3",
            "pulp_product_odt",
            "panel_product_m3",
            "pellet_product_odt",
            "bio_product_mwh",
            "process_residue_pj",
            "harvest_residue_gross_pj",
            "harvest_residue_recoverable_pj",
            "total_residue_gross_pj",
            "total_residue_recoverable_pj",
        ],
        "scenario_summary",
    )


def test_residue_curve_and_osecan_handoff() -> None:
    curve = json.loads((CURVE_DIR / "summary.json").read_text(encoding="utf-8"))
    assert curve["artifact"] == "national_residue_curve_v7"
    assert curve["validation"]["passed"] is True
    assert curve["calibrated_recovery_factor"] is False
    assert curve["recovery_scenarios"] == [0.35, 0.60, 0.75]

    handoff = json.loads((HANDOFF_DIR / "summary.json").read_text(encoding="utf-8"))
    assert handoff["artifact"] == "forestry_osecan_handoff_v8"
    assert handoff["status"] == "pass"
    assert handoff["validation"]["failed_check_count"] == 0
    assert handoff["labels"]["official_osecan_changed"] is False
    assert handoff["labels"]["model_estimate"] is True
    assert handoff["record_counts"]["handoff_rows"] == 119
    assert handoff["record_counts"]["zone_summary_rows"] == 91
    zone_summary = _frame(HANDOFF_DIR, "zone_summary.csv")
    assert zone_summary["zone_id"].nunique() == 13
    assert "UNMAPPED" not in set(zone_summary["zone_id"])
    tiers = _frame(HANDOFF_DIR, "tier_positions_v3_2_vs_v4.csv")
    assert len(tiers) == 7
    assert set(tiers["v4_e2c_tier"]) == {
        "T1",
        "T3",
        "T4",
        "above_T4",
    }


def test_osecan_scenarios_labels_and_cases() -> None:
    summary = json.loads(
        (SCENARIO_DIR / "summary.json").read_text(encoding="utf-8")
    )
    assert summary["artifact"] == "forestry_osecan_scenarios_v7"
    assert summary["status"] == "pass"
    assert summary["validation"]["failed_check_count"] == 0
    assert summary["scenario_order"] == ["100m", "119m", "150m"]
    assert summary["labels"]["official_osecan_changed"] is False
    assert summary["labels"]["observed"] is False
    assert summary["labels"]["policy_grade"] is False
    assert summary["record_counts"]["provinces"] == 13
    assert summary["record_counts"]["zones"] == 13
    assert summary["recovery_scenarios"] == [0.35, 0.60, 0.75]
    case_names = set(
        summary["national_case_totals"]["119m"]["harvest"]
    )
    assert case_names == {
        "harvest_recovery_035",
        "harvest_recovery_060",
        "harvest_recovery_075",
    }
    process_names = set(
        summary["national_case_totals"]["119m"]["process"]
    )
    assert process_names == {
        "process_availability_000",
        "process_availability_025",
        "process_availability_050",
        "process_availability_075",
    }
    residues = _frame(SCENARIO_DIR, "forestry_residue_scenarios.csv")
    _assert_finite_nonnegative(
        residues,
        ["odt", "pj"],
        "forestry_residue_scenarios",
    )
    assert set(residues["zone_id"]) != {"UNMAPPED"}
    assert residues["zone_id"].nunique() == 13


def test_no_nan_or_negative_in_key_numeric_tables() -> None:
    checks = (
        (
            PRODUCT_DIR,
            "product_chain_by_province.csv",
            [
                "harvest_m3",
                "sawlog_product_m3",
                "pulp_product_odt",
                "panel_product_m3",
                "process_residue_odt",
                "harvest_residue_total_gross_odt",
                "total_residue_gross_total_odt",
            ],
        ),
        (
            PRODUCT_DIR,
            "residue_scenario_coefficients.csv",
            [
                "stem_residue_odt_per_m3",
                "branch_foliage_odt_per_m3",
                "total_residue_gross_odt_per_m3",
            ],
        ),
        (
            CURVE_DIR,
            "province_residue_curve.csv",
            [
                "residue_odt",
                "residue_pj",
            ],
        ),
    )
    for directory, name, columns in checks:
        frame = _frame(directory, name)
        _assert_finite_nonnegative(frame, columns, name)
