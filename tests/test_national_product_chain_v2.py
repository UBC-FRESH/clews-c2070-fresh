"""Regression tests for the measured-coefficient product chain v2."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from workflows.national.facilities import national_product_chain_v2 as chain


def _read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, keep_default_na=False, dtype=str)


@pytest.fixture(scope="module")
def built() -> tuple[dict, Path]:
    summary = json.loads((chain.OUTPUT_DIR / "summary.json").read_text())
    return summary, chain.OUTPUT_DIR


@pytest.fixture(scope="module")
def province_rows(built) -> pd.DataFrame:
    _, target = built
    frame = _read_csv(target / "product_chain_by_province.csv")
    numeric = [
        "harvest_m3",
        "sawlog_feedstock_m3",
        "pulp_feedstock_m3",
        "panel_feedstock_m3",
        "pellet_feedstock_m3",
        "bio_feedstock_m3",
        "total_feedstock_m3",
        "process_residue_odt",
        "harvest_residue_gross_odt",
        "harvest_residue_recoverable_odt",
        "total_residue_gross_odt",
    ]
    for column in numeric:
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    return frame


def test_build_deliverables_and_labels(built):
    summary, target = built
    assert summary["artifact"] == "national_product_chain_v2"
    assert summary["status"] == "pass"
    assert summary["model_estimate"] is True
    assert summary["observed"] is False
    assert summary["policy_grade"] is False
    assert summary["facility_assignment"] is False
    assert summary["validation"]["passed"] is True
    for name in (
        "product_chain_by_province.csv",
        "coefficients.csv",
        "measured_coefficient_replacement.csv",
        "summary.json",
        "NATIONAL_PRODUCT_CHAIN_V2.md",
        "manifest.json",
    ):
        assert (target / name).is_file(), name


def test_measured_and_donor_split(built, province_rows):
    summary, _ = built
    assert summary["measured_provinces"] == ["BC", "QC", "NB", "YT"]
    assert set(summary["donor_provinces"]) == {
        "AB",
        "SK",
        "MB",
        "ON",
        "NS",
        "PE",
        "NL",
        "NT",
        "NU",
    }
    tiers = dict(zip(province_rows["province"], province_rows["coefficient_tier"]))
    for province in ("BC", "QC", "NB", "YT"):
        assert tiers[province] == "measured_bucking_pilot"
    for province in summary["donor_provinces"]:
        assert tiers[province] == "donor_coefficient"


def test_measured_values_match_pilot(built):
    _summary, target = built
    pilot = json.loads(chain.measured.PILOT_SUMMARY.read_text())
    comparison = {row["province"]: row for row in pilot["coefficient_comparison"]}
    replacement = _read_csv(target / "measured_coefficient_replacement.csv")
    replacement = replacement.set_index(["province", "coefficient_name"])
    for province in ("BC", "QC", "NB", "YT"):
        expected = {
            "solid_share": float(comparison[province]["model_sawlog_share"]),
            "pulp_share": float(comparison[province]["model_pulpwood_share"]),
            "other_share": float(comparison[province]["model_other_share"]),
            "harvest_residue_odt_per_m3": float(
                comparison[province]["model_residue_odt_per_recovered_m3"]
            ),
        }
        for name, value in expected.items():
            actual = float(replacement.loc[(province, name), "measured_value"])
            assert actual == pytest.approx(value, rel=1e-9, abs=1e-15)


def test_conservation_and_donor_rows_unchanged(built, province_rows):
    summary, _ = built
    v1 = _read_csv(chain.V1_CSV).set_index("province")
    for _, row in province_rows.iterrows():
        province = row["province"]
        assert row["total_feedstock_m3"] == pytest.approx(
            row["sawlog_feedstock_m3"]
            + row["pulp_feedstock_m3"]
            + row["panel_feedstock_m3"]
            + row["pellet_feedstock_m3"]
            + row["bio_feedstock_m3"],
            rel=1e-10,
            abs=1e-3,
        )
        assert row["process_residue_odt"] + row["harvest_residue_gross_odt"] == pytest.approx(
            row["total_residue_gross_odt"], rel=1e-10, abs=1e-3
        )
        if province in summary["donor_provinces"]:
            for column in (
                "sawlog_feedstock_m3",
                "pulp_feedstock_m3",
                "panel_feedstock_m3",
                "pellet_feedstock_m3",
                "bio_feedstock_m3",
                "process_residue_odt",
                "harvest_residue_gross_odt",
            ):
                assert float(row[column]) == pytest.approx(
                    float(v1.loc[province, column]), rel=1e-12, abs=1e-6
                )
    assert summary["national_totals"]["harvest_m3"] == pytest.approx(
        float(v1["harvest_m3"].astype(float).sum()), rel=1e-12, abs=1e-3
    )


def test_measured_shares_sum_and_labels(built, province_rows):
    summary, _ = built
    replacement = _read_csv(
        chain.OUTPUT_DIR / "measured_coefficient_replacement.csv"
    )
    for province in ("BC", "QC", "NB", "YT"):
        subset = replacement[
            (replacement["province"] == province)
            & replacement["coefficient_name"].isin(
                ["solid_share", "pulp_share", "other_share"]
            )
        ]
        assert float(subset["measured_value"].astype(float).sum()) == pytest.approx(1.0)
    for column, expected in (
        ("model_estimate", "true"),
        ("observed", "false"),
        ("policy_grade", "false"),
        ("facility_assignment", "false"),
    ):
        assert (province_rows[column].str.lower() == expected).all()
    assert summary["national_totals"]["harvest_residue_gross_odt"] < 9_069_109.0
