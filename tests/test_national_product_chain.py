"""Regression tests for the national product/residue chain v1 artifact."""

from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path

import pandas as pd
import pytest

from workflows.national.facilities import national_product_chain as chain

ARTIFACT_DIR = chain.OUTPUT_DIR
PROVINCE_CSV = ARTIFACT_DIR / "product_chain_by_province.csv"
TOTALS_CSV = ARTIFACT_DIR / "product_chain_totals.csv"
COEFFICIENTS_CSV = ARTIFACT_DIR / "coefficients.csv"
SUMMARY_JSON = ARTIFACT_DIR / "summary.json"
MANIFEST_JSON = ARTIFACT_DIR / "manifest.json"
REPORT_MD = ARTIFACT_DIR / "NATIONAL_PRODUCT_CHAIN_V1.md"

NUMERIC_COLUMNS = [
    "harvest_m3",
    "sawlog_m3",
    "pulpwood_m3",
    "other_log_m3",
    "primary_feedstock_m3",
    "residue_feedstock_m3",
    "total_feedstock_m3",
    "sawlog_feedstock_m3",
    "pulp_feedstock_m3",
    "panel_feedstock_m3",
    "pellet_feedstock_m3",
    "bio_feedstock_m3",
    "sawlog_product_m3",
    "pulp_product_odt",
    "panel_product_m3",
    "pellet_product_odt",
    "bio_product_mwh",
    "sawlog_residue_m3",
    "sawlog_residue_odt",
    "pulp_residue_m3",
    "pulp_residue_odt",
    "panel_residue_m3",
    "panel_residue_odt",
    "pellet_residue_m3",
    "pellet_residue_odt",
    "bio_residue_m3",
    "bio_residue_odt",
    "process_residue_m3",
    "process_residue_odt",
    "harvest_residue_gross_m3",
    "harvest_residue_gross_odt",
    "harvest_residue_recoverable_m3",
    "harvest_residue_recoverable_odt",
    "harvest_residue_unrecovered_m3",
    "harvest_residue_unrecovered_odt",
    "total_residue_gross_m3",
    "total_residue_gross_odt",
    "total_residue_recoverable_m3",
    "total_residue_recoverable_odt",
    "process_residue_odt_per_m3_feedstock",
    "harvest_residue_gross_odt_per_m3_harvest",
    "total_residue_gross_odt_per_m3_harvest",
    "rsyc_base_recoverable_odt",
    "model_recoverable_odt_base",
    "rsyc_ratio",
]


def _read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, keep_default_na=False)


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> tuple[dict, Path]:
    target = tmp_path_factory.mktemp("national_product_chain")
    summary = chain.build(output_dir=target)
    return summary, target


@pytest.fixture(scope="module")
def province_rows(built) -> pd.DataFrame:
    _, target = built
    frame = _read_csv(target / "product_chain_by_province.csv")
    for column in NUMERIC_COLUMNS:
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    return frame


@pytest.fixture(scope="module")
def coefficient_rows(built) -> pd.DataFrame:
    _, target = built
    return _read_csv(target / "coefficients.csv")


@pytest.fixture(scope="module")
def totals(built) -> pd.DataFrame:
    _, target = built
    frame = _read_csv(target / "product_chain_totals.csv")
    frame["value"] = pd.to_numeric(frame["value"], errors="raise")
    return frame


def test_build_writes_all_deliverables(built):
    summary, target = built
    assert summary["status"] == "pass"
    assert summary["validation"]["passed"] is True
    for name in (
        "product_chain_by_province.csv",
        "product_chain_totals.csv",
        "coefficients.csv",
        "summary.json",
        "NATIONAL_PRODUCT_CHAIN_V1.md",
        "manifest.json",
    ):
        assert (target / name).is_file(), name
    assert summary["national_totals"]["harvest_m3"] == pytest.approx(
        118_977_339.1, rel=1e-9
    )
    assert summary["province_count"] == 13


def test_grade_and_feedstock_conservation(province_rows):
    assert len(province_rows) == 13
    for _, row in province_rows.iterrows():
        harvest = row["harvest_m3"]
        assert row["sawlog_m3"] + row["pulpwood_m3"] + row["other_log_m3"] == (
            pytest.approx(harvest, rel=1e-9, abs=1e-6)
        )
        assert row["primary_feedstock_m3"] + row["residue_feedstock_m3"] == (
            pytest.approx(row["total_feedstock_m3"], rel=1e-9, abs=1e-6)
        )
        assert row["primary_feedstock_m3"] == pytest.approx(
            harvest, rel=1e-9, abs=1e-6
        )
        class_sum = (
            row["sawlog_feedstock_m3"]
            + row["pulp_feedstock_m3"]
            + row["panel_feedstock_m3"]
            + row["pellet_feedstock_m3"]
            + row["bio_feedstock_m3"]
        )
        assert class_sum == pytest.approx(
            row["total_feedstock_m3"], rel=1e-9, abs=1e-6
        )


def test_residue_ratios_and_totals(province_rows, totals):
    for _, row in province_rows.iterrows():
        process = (
            row["sawlog_residue_odt"]
            + row["pulp_residue_odt"]
            + row["panel_residue_odt"]
            + row["pellet_residue_odt"]
            + row["bio_residue_odt"]
        )
        assert process == pytest.approx(row["process_residue_odt"], rel=1e-9, abs=1e-6)
        assert row["process_residue_m3"] == pytest.approx(
            row["process_residue_odt"] / chain.RESIDUE_DENSITY_ODT_M3,
            rel=1e-9,
            abs=1e-6,
        )
        assert row["harvest_residue_gross_odt"] == pytest.approx(
            row["harvest_residue_recoverable_odt"]
            + row["harvest_residue_unrecovered_odt"],
            rel=1e-9,
            abs=1e-6,
        )
        assert row["total_residue_gross_odt"] == pytest.approx(
            row["process_residue_odt"] + row["harvest_residue_gross_odt"],
            rel=1e-9,
            abs=1e-6,
        )
        if row["harvest_m3"] > 0:
            assert row["harvest_residue_gross_odt_per_m3_harvest"] == pytest.approx(
                row["harvest_residue_gross_odt"] / row["harvest_m3"],
                rel=1e-9,
                abs=1e-12,
            )
        assert row["process_residue_odt_per_m3_feedstock"] >= 0.0

    total_lookup = dict(zip(totals["metric"], totals["value"]))
    for column in (
        "harvest_m3",
        "total_feedstock_m3",
        "process_residue_odt",
        "harvest_residue_gross_odt",
        "total_residue_gross_odt",
    ):
        assert total_lookup[column] == pytest.approx(
            float(province_rows[column].sum()), rel=1e-9, abs=1e-6
        )


def test_no_nan_or_negative(province_rows, coefficient_rows):
    numeric = province_rows[NUMERIC_COLUMNS]
    assert not numeric.isna().any().any()
    assert (numeric >= 0.0).all().all()
    assert math.isfinite(float(numeric.to_numpy().sum()))
    coefficient_values = pd.to_numeric(coefficient_rows["value"], errors="raise")
    assert not coefficient_values.isna().any()
    assert (coefficient_values >= 0.0).all()


def _label(value) -> str:
    return str(value).strip().lower()


def test_donor_and_bc_anchor_labels(province_rows):
    for _, row in province_rows.iterrows():
        if row["province"] == "BC":
            assert row["coefficient_tier"] == "bc_anchor_species_group"
            assert _label(row["province_species_split_available"]) == "true"
        else:
            assert row["coefficient_tier"] == "donor_coefficient"
            assert _label(row["province_species_split_available"]) == "false"
        assert _label(row["model_estimate"]) == "true"
        assert _label(row["observed"]) == "false"
        assert _label(row["policy_grade"]) == "false"
        assert _label(row["facility_assignment"]) == "false"
        assert row["coefficient_source"].strip()
        assert row["species_group_basis"].strip()


def test_coefficient_tiers_and_shares(coefficient_rows):
    assert set(coefficient_rows["tier"]) == {
        "bc_anchor_species_group",
        "donor_coefficient",
    }
    grade_mix = coefficient_rows[
        coefficient_rows["coefficient_name"].isin(
            ["sawlog_share", "pulpwood_share", "other_share"]
        )
    ]
    for group, subset in grade_mix.groupby("species_group"):
        assert subset["value"].sum() == pytest.approx(1.0, rel=1e-9), group
    class_shares = coefficient_rows[
        coefficient_rows["coefficient_name"] == "class_share"
    ]
    for (group, grade), subset in class_shares.groupby(["species_group", "grade"]):
        assert subset["value"].sum() == pytest.approx(1.0, rel=1e-9), (group, grade)
    assert "panel_share_of_sawlog" in set(coefficient_rows["coefficient_name"])


def test_sawmill_residue_crosscheck(coefficient_rows):
    saw = coefficient_rows[
        (coefficient_rows["species_group"] == "aggregate")
        & (coefficient_rows["facility_class"] == "SAW")
        & (
            coefficient_rows["coefficient_name"]
            == "residue_total_odt_per_m3"
        )
    ]
    assert len(saw) == 1
    assert float(saw.iloc[0]["value"]) == pytest.approx(
        0.2595527054957869, rel=1e-12
    )


def test_rsyc_comparison(province_rows, totals):
    allowed = {
        "within_rsyc_range",
        "model_below_rsyc",
        "model_above_rsyc",
        "not_available",
        "zero_harvest",
    }
    assert set(province_rows["rsyc_status"]).issubset(allowed)
    for _, row in province_rows.iterrows():
        if row["harvest_m3"] > 0:
            assert math.isfinite(row["rsyc_base_recoverable_odt"])
            assert row["rsyc_base_recoverable_odt"] > 0.0
            assert math.isfinite(row["rsyc_ratio"])
            assert row["rsyc_ratio"] > 0.0
    assert (province_rows["rsyc_status"] == "within_rsyc_range").any()
    assert (province_rows["rsyc_status"] == "model_below_rsyc").any()
    total_lookup = dict(zip(totals["metric"], totals["value"]))
    expected_ratio = (
        total_lookup["model_recoverable_odt_base"]
        / total_lookup["rsyc_base_recoverable_odt"]
    )
    assert total_lookup["rsyc_ratio"] == pytest.approx(expected_ratio, rel=1e-9)


def test_summary_and_manifest_are_consistent(built):
    summary, target = built
    with (target / "summary.json").open(encoding="utf-8") as handle:
        saved_summary = json.load(handle)
    assert saved_summary["artifact"] == "national_product_chain_v1"
    assert saved_summary["validation"]["passed"] is True
    assert saved_summary["model_estimate"] is True
    assert saved_summary["observed"] is False
    assert saved_summary["policy_grade"] is False
    assert saved_summary["facility_assignment"] is False
    assert saved_summary["ui_or_public_osecan_changed"] is False
    with (target / "manifest.json").open(encoding="utf-8") as handle:
        manifest = json.load(handle)
    for name, metadata in manifest["outputs"].items():
        path = target / name
        assert path.is_file(), name
        assert path.stat().st_size == metadata["bytes"], name
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert digest == metadata["sha256"], name


def test_report_covers_required_sections():
    text = REPORT_MD.read_text(encoding="utf-8")
    for phrase in (
        "National Product/Residue Chain v1",
        "## Coefficient tiers",
        "## Provincial products/residue",
        "## National totals and RSYC comparison",
        "## Tests and status",
        "No policy/AAC claim is made",
    ):
        assert phrase in text
    assert "observed=false" in text
    assert "policy_grade=false" in text
