"""Contract tests for the E1+E2 13-province facility chain v6.

The tests read the committed artifacts and enforce:

* 13 provinces with E1 measured / E2 imputed coefficient sources;
* E2 branch/foliage allometry and total residue conservation;
* product, feedstock and residue conservation for the 119 Mm3 chain;
* 100/119/150 Mm3 scenarios and 0.35/0.60/0.75 recovery values;
* OSeCAN handoff and zone scenarios remain model estimates with an unchanged
  official OSeCAN tree;
* no NaN, infinity or negative numeric values.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from workflows.national.facilities import e2_imputed_coefficients as coefficients
from workflows.national.facilities import measured_bucking_coefficients as measured

REPO_ROOT = Path(__file__).resolve().parents[1]
E2_DIR = REPO_ROOT / "data/derived/national/bucking/open_data_imputation_v1"
PRODUCT_DIR = (
    REPO_ROOT / "data/derived/national/facilities/national_product_chain_v4"
)
V3_DIR = REPO_ROOT / "data/derived/national/facilities/national_product_chain_v3"
CHAIN_DIR = (
    REPO_ROOT
    / "data/derived/national/facilities/national_facility_chain_scenarios_v6"
)
CURVE_DIR = (
    REPO_ROOT / "data/derived/national/facilities/national_residue_curve_v6"
)
HANDOFF_DIR = (
    REPO_ROOT / "data/derived/national/facilities/forestry_osecan_handoff_v7"
)
SCENARIO_DIR = (
    REPO_ROOT / "data/derived/national/facilities/forestry_osecan_scenarios_v6"
)

PROVINCES = set(coefficients.PROVINCES)
MEASURED = set(coefficients.MEASURED_PROVINCES)
IMPUTED = set(coefficients.IMPUTED_PROVINCES)


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
def product() -> pd.DataFrame:
    return _frame(PRODUCT_DIR, "product_chain_by_province.csv")


@pytest.fixture(scope="module")
def coefficient_table() -> pd.DataFrame:
    return _frame(PRODUCT_DIR, "coefficients.csv")


def test_e2_coefficients_and_allometry_labels() -> None:
    frame = _frame(E2_DIR, "product_coefficients.csv")
    assert set(frame["province"]) == PROVINCES
    assert len(frame) == 13
    e1 = frame[frame["province"].isin(MEASURED)]
    e2 = frame[frame["province"].isin(IMPUTED)]
    assert set(e1["coefficient_tier"]) == {"measured_bucking_pilot"}
    assert set(e2["coefficient_tier"]) == {"e2_open_data_imputation"}
    assert set(e1["product_coefficient_source"]) == {
        "E1_four_province_bucking_pilot_measured"
    }
    assert set(e2["product_coefficient_source"]) == {"E2_open_data_imputation"}
    assert (e2["branch_foliage_odt_per_m3"] > 0.0).all()
    assert (e2["total_residue_odt_per_m3"] > 0.0).all()
    assert np.allclose(
        e2["total_residue_odt_per_m3"],
        e2["stem_residue_odt_per_m3"] + e2["branch_foliage_odt_per_m3"],
        rtol=1e-10,
        atol=1e-12,
    )
    for factor, column in (
        (0.35, "recoverable_odt_per_m3_r035"),
        (0.60, "recoverable_odt_per_m3_r060"),
        (0.75, "recoverable_odt_per_m3_r075"),
    ):
        assert np.allclose(
            e2[column],
            e2["total_residue_odt_per_m3"] * factor,
            rtol=1e-10,
            atol=1e-12,
        )
    assert e2["allometry_equation_species"].str.len().gt(0).all()
    assert not e2["allometry_equation_species"].str.contains("not_computed").any()
    assert (e2["n_allometry_proxy_trees"] > 0).any()


def test_e1_matches_measured_pilot_and_e2_reader(product) -> None:
    measured_rows = measured.load_measured_coefficients()
    parsed = coefficients.load_coefficients()
    assert set(parsed) == PROVINCES
    for province, row in measured_rows.items():
        assert parsed[province].tier == coefficients.MEASURED_TIER
        assert parsed[province].solid_share == pytest.approx(
            row.solid_share, abs=1e-12
        )
        assert parsed[province].pulp_share == pytest.approx(
            row.pulp_share, abs=1e-12
        )
        assert parsed[province].other_share == pytest.approx(
            row.other_share, abs=1e-12
        )
        assert parsed[province].total_residue_odt_per_m3 == pytest.approx(
            row.harvest_residue_total_odt_per_m3, abs=1e-12
        )
    v3 = pd.read_csv(V3_DIR / "product_chain_by_province.csv").set_index("province")
    for province in MEASURED:
        for field in (
            "harvest_m3",
            "sawlog_product_m3",
            "pulp_product_odt",
            "panel_product_m3",
            "pellet_product_odt",
            "bio_product_mwh",
            "process_residue_odt",
            "harvest_residue_gross_odt",
            "total_residue_gross_odt",
        ):
            assert float(product.loc[product.province == province, field].iloc[0]) == pytest.approx(
                float(v3.loc[province, field]), abs=1e-6
            )


def test_product_chain_conservation_and_coefficient_source(product) -> None:
    assert set(product["province"]) == PROVINCES
    assert len(product) == 13
    assert (
        product["coefficient_tier"]
        .map({"measured_bucking_pilot": 4, "e2_open_data_imputation": 9})
        .notna()
        .all()
    )
    assert product[product.province.isin(MEASURED)]["coefficient_tier"].eq(
        "measured_bucking_pilot"
    ).all()
    assert product[product.province.isin(IMPUTED)]["coefficient_tier"].eq(
        "e2_open_data_imputation"
    ).all()
    assert not product["coefficient_source"].str.contains(
        "donor_coefficient", case=False
    ).any()
    assert np.allclose(
        product["sawlog_m3"] + product["pulpwood_m3"] + product["other_log_m3"],
        product["harvest_m3"],
        rtol=1e-10,
        atol=1e-3,
    )
    class_feed = (
        product["sawlog_feedstock_m3"]
        + product["pulp_feedstock_m3"]
        + product["panel_feedstock_m3"]
        + product["pellet_feedstock_m3"]
        + product["bio_feedstock_m3"]
    )
    assert np.allclose(
        class_feed, product["total_feedstock_m3"], rtol=1e-10, atol=1e-3
    )
    assert np.allclose(
        product["harvest_residue_stem_gross_odt"]
        + product["harvest_residue_branch_foliage_odt"],
        product["harvest_residue_total_gross_odt"],
        rtol=1e-10,
        atol=1e-6,
    )
    assert np.allclose(
        product["process_residue_odt"]
        + product["harvest_residue_total_gross_odt"],
        product["total_residue_gross_total_odt"],
        rtol=1e-10,
        atol=1e-6,
    )
    for factor, column in (
        (0.35, "harvest_residue_recoverable_total_odt_r035"),
        (0.60, "harvest_residue_recoverable_total_odt_r060"),
        (0.75, "harvest_residue_recoverable_total_odt_r075"),
    ):
        assert np.allclose(
            product[column],
            product["harvest_residue_total_gross_odt"] * factor,
            rtol=1e-10,
            atol=1e-6,
        )
    assert (product["harvest_residue_component_scope"] == "stem_plus_branch_foliage").all()


def test_coefficient_table_has_13_provinces(coefficient_table) -> None:
    assert set(coefficient_table["province"]) == PROVINCES
    assert set(coefficient_table["tier"]) == {
        "measured_bucking_pilot",
        "e2_open_data_imputation",
    }
    counts = coefficient_table.groupby("tier")["province"].nunique()
    assert counts["measured_bucking_pilot"] == 4
    assert counts["e2_open_data_imputation"] == 9
    assert not coefficient_table["source"].str.contains(
        "donor_coefficient", case=False
    ).any()
    for province in PROVINCES:
        rows = coefficient_table[coefficient_table.province == province]
        shares = {
            row.coefficient_name: float(row.applied_value)
            for row in rows.itertuples(index=False)
        }
        if set(shares) == {
            "solid_share",
            "pulp_share",
            "other_share",
            "total_residue_odt_per_m3",
        }:
            assert shares["solid_share"] + shares["pulp_share"] + shares[
                "other_share"
            ] == pytest.approx(1.0, abs=1e-9)


def test_facility_chain_100_119_150_scenarios(product) -> None:
    summary = json.loads((_require(CHAIN_DIR / "summary.json")).read_text())
    assert summary["artifact"] == "national_facility_chain_scenarios_v6"
    assert summary["labels"]["observed"] is False
    assert summary["labels"]["policy_grade"] is False
    assert summary["labels"]["national_claim"] is False
    assert summary["coefficient_tiers"] == {
        "measured_bucking_pilot": 4,
        "e2_open_data_imputation": 9,
        "donor_product_coefficients": 0,
    }
    assert set(summary["scenarios"]) == {"100m", "119m", "150m"}
    expected = {"100m": 100_000_000.1, "119m": 118_977_339.1, "150m": 150_000_000.2}
    for scenario, harvest in expected.items():
        assert summary["scenarios"][scenario]["harvest_m3"] == pytest.approx(
            harvest, abs=1.0
        )
    residue = _frame(CHAIN_DIR, "scenario_product_residue.csv")
    assert set(residue["scenario"]) == {"100m", "119m", "150m"}
    assert set(residue["province"]) == PROVINCES
    assert not residue["coefficient_tier"].str.contains(
        "donor_coefficient", case=False
    ).any()
    comparison = _frame(CHAIN_DIR, "donor_vs_e2_comparison.csv")
    assert len(comparison) == 3 * (1 + 13) * 11
    assert set(comparison["scope"]) == {"national", "province"}
    assert not comparison["observed"].any()
    _assert_finite_nonnegative(
        residue,
        [
            "feedstock_m3",
            "product_quantity",
            "process_residue_odt",
            "harvest_residue_gross_pj",
            "harvest_residue_recoverable_pj",
        ],
        "scenario_product_residue",
    )


def test_osecan_handoff_and_zone_scenarios() -> None:
    handoff = json.loads((_require(HANDOFF_DIR / "summary.json")).read_text())
    assert handoff["artifact"] == "forestry_osecan_handoff_v7"
    assert handoff["labels"]["official_osecan_changed"] is False
    assert handoff["labels"]["observed"] is False
    assert handoff["labels"]["policy_grade"] is False
    weights = _frame(HANDOFF_DIR, "zone_allocation_weights.csv")
    assert weights["province"].nunique() == 13
    assert len(handoff["tier_positions_donor_vs_e2"]) == 7

    scenarios = json.loads((_require(SCENARIO_DIR / "summary.json")).read_text())
    assert scenarios["artifact"] == "forestry_osecan_scenarios_v6"
    assert scenarios["record_counts"]["provinces"] == 13
    assert scenarios["record_counts"]["provinces"] == len(PROVINCES)
    assert scenarios["record_counts"]["scenarios"] == 3
    assert scenarios["labels"]["official_osecan_changed"] is False
    assert scenarios["labels"]["national_claim"] is False
    assert scenarios["osecan_integrity"]["unchanged"] is True
    tiers = _frame(SCENARIO_DIR, "tier_positions_donor_vs_e2.csv")
    assert len(tiers) == 3 * 7
    assert not tiers["national_claim"].any()
    residue = _frame(SCENARIO_DIR, "forestry_residue_scenarios.csv")
    assert set(residue["scenario"]) == {"100m", "119m", "150m"}
    assert set(residue["province"]) == PROVINCES
    _assert_finite_nonnegative(
        residue,
        ["odt", "pj", "zone_weight"],
        "forestry_residue_scenarios",
    )


def test_curve_v6_scenarios_and_no_nan() -> None:
    summary = json.loads((_require(CURVE_DIR / "summary.json")).read_text())
    assert summary["artifact"] == "national_residue_curve_v6"
    assert summary["status"] == "pass"
    assert summary["validation"]["passed"] is True
    province = _frame(CURVE_DIR, "province_residue_curve.csv")
    national = _frame(CURVE_DIR, "national_residue_curve.csv")
    assert set(province["province"]) == PROVINCES
    assert set(province["availability_case"]) == {
        "process_availability_000",
        "process_availability_025",
        "process_availability_050",
        "process_availability_075",
        "harvest_recovery_035",
        "harvest_recovery_060",
        "harvest_recovery_075",
    }
    assert np.allclose(
        national["residue_odt"],
        national["base_residue_odt"] * national["availability_fraction"],
        rtol=1e-9,
        atol=1e-6,
    )
    for frame, columns in (
        (
            province,
            ["base_residue_odt", "residue_odt", "residue_pj", "availability_fraction"],
        ),
        (national, ["base_residue_odt", "residue_odt", "residue_pj"]),
    ):
        _assert_finite_nonnegative(frame, columns, "residue_curve")
