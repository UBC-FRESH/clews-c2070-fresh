"""Regression tests for the national facility residue supply v1 artifact."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import pandas as pd
import pytest

from workflows.national.facilities import facility_residue_supply as supply

ARTIFACT_DIR = supply.OUTPUT_DIR
FACILITY_CSV = ARTIFACT_DIR / "facility_residue_supply.csv"
PROVINCE_CSV = ARTIFACT_DIR / "province_residue_supply.csv"
NATIONAL_CSV = ARTIFACT_DIR / "national_residue_summary.csv"
SUMMARY_JSON = ARTIFACT_DIR / "summary.json"
MANIFEST_JSON = ARTIFACT_DIR / "manifest.json"
REPORT_MD = ARTIFACT_DIR / "FACILITY_RESIDUE_SUPPLY_V1.md"
PRODUCT_CHAIN_CSV = supply.PRODUCT_CHAIN_DIR / "product_chain_by_province.csv"
PRODUCT_CHAIN_SUMMARY = supply.PRODUCT_CHAIN_DIR / "summary.json"
ASSIGNMENT_CSV = supply.ASSIGNMENT_DIR / "facility_assignment.csv"
ASSIGNMENT_SUMMARY = supply.ASSIGNMENT_DIR / "summary.json"
RSYC_SUMMARY = supply.RSYC_SUMMARY

FACILITY_NUMERIC = [
    "assignment_weight_m3",
    "assignment_share",
    "assigned_m3",
    "floor_scale_factor",
    "capacity_m3_y",
    "assignment_utilization_v3",
    "feedstock_m3",
    "share_of_origin_class_feedstock",
    "origin_class_reference_feedstock_m3",
    "product_quantity",
    "product_per_m3",
    "residue_odt",
    "residue_pj",
    "residue_odt_per_m3",
    "lhv_gj_per_odt",
    "facility_process_residue_odt",
    "facility_share_of_process_residue",
]
PROVINCE_NUMERIC = [
    "residue_odt",
    "residue_pj",
    "lhv_gj_per_odt",
    "facility_count",
    "facility_count_positive_residue",
    "origin_process_residue_odt",
    "origin_process_residue_pj",
    "published_residue_share",
    "proxy_residue_share",
    "scaled_proxy_residue_share",
    "top1_facility_residue_share",
    "hhi_facility_residue",
    "rsyc_base_recoverable_odt",
    "model_recoverable_odt_base",
    "rsyc_ratio",
]


def _read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, keep_default_na=False, dtype=str)


def _numeric(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    for column in columns:
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    return frame


def _reference_product_chain() -> pd.DataFrame:
    frame = _read_csv(PRODUCT_CHAIN_CSV)
    frame["province"] = frame["province"].astype(str).str.strip().str.upper()
    return _numeric(
        frame,
        [
            *supply.CLASS_FEEDSTOCK_COLUMN.values(),
            *supply.CLASS_PRODUCT_COLUMN.values(),
            *supply.CLASS_RESIDUE_COLUMN.values(),
            "harvest_m3",
            "harvest_residue_gross_odt",
            "harvest_residue_recoverable_odt",
            "harvest_residue_unrecovered_odt",
            "rsyc_base_recoverable_odt",
            "model_recoverable_odt_base",
            "rsyc_ratio",
        ],
    )


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> tuple[dict, Path]:
    target = tmp_path_factory.mktemp("facility_residue_supply")
    summary = supply.build(output_dir=target)
    return summary, target


@pytest.fixture(scope="module")
def facility_rows(built) -> pd.DataFrame:
    _, target = built
    return _numeric(
        _read_csv(target / "facility_residue_supply.csv"), FACILITY_NUMERIC
    )


@pytest.fixture(scope="module")
def province_rows(built) -> pd.DataFrame:
    _, target = built
    return _numeric(
        _read_csv(target / "province_residue_supply.csv"), PROVINCE_NUMERIC
    )


@pytest.fixture(scope="module")
def national_rows(built) -> pd.DataFrame:
    _, target = built
    frame = _read_csv(target / "national_residue_summary.csv")
    frame["value"] = pd.to_numeric(frame["value"], errors="raise")
    return frame


def _label(value) -> str:
    return str(value).strip().lower()


def test_build_writes_all_deliverables(built):
    summary, target = built
    assert summary["status"] == "pass"
    assert summary["validation"]["passed"] is True
    assert summary["validation"]["failed_check_count"] == 0
    assert summary["scenario"] == supply.SCENARIO
    for name in (
        "facility_residue_supply.csv",
        "province_residue_supply.csv",
        "national_residue_summary.csv",
        "summary.json",
        "FACILITY_RESIDUE_SUPPLY_V1.md",
        "manifest.json",
    ):
        assert (target / name).is_file(), name
    assert (target / "facility_residue_supply.csv").stat().st_size > 0
    assert summary["concentration"]["facilities_used"] > 0
    assert summary["national_totals"]["process_residue_odt"] > 0.0


def test_class_matching(facility_rows, province_rows):
    assert set(facility_rows["facility_class"]).issubset(set(supply.CLASS_ORDER))
    assert (
        facility_rows["feedstock_class"] == facility_rows["facility_class"]
    ).all()
    per_facility = facility_rows.groupby("facility_id")["facility_class"].nunique()
    assert (per_facility == 1).all()
    assert set(facility_rows["allocation_scope"]).issubset(
        {"province_assignment", "national_class_fallback"}
    )
    assert set(facility_rows["allocation_basis"]) == {
        "assignment_v3_province_capacity_floor_assigned_m3"
    }
    assert set(facility_rows["conversion_basis"]) == {
        "product_chain_effective_province_class_ratio"
    }
    assert set(facility_rows["residue_type"]) == {"process_residue"}
    process = province_rows[province_rows["residue_type"] == "process_residue"]
    assert (process["facility_assigned"] == "true").all()
    harvest = province_rows[province_rows["residue_type"] != "process_residue"]
    assert (harvest["facility_assigned"] == "false").all()
    assert set(harvest["residue_province_basis"]) == {"origin_province"}


def test_province_class_conservation(facility_rows):
    reference = _reference_product_chain().set_index("province")
    grouped_feedstock = (
        facility_rows.groupby(["origin_province", "facility_class"])["feedstock_m3"]
        .agg(lambda values: math.fsum(float(value) for value in values))
        .to_dict()
    )
    grouped_product = (
        facility_rows.groupby(["origin_province", "facility_class"])[
            "product_quantity"
        ]
        .agg(lambda values: math.fsum(float(value) for value in values))
        .to_dict()
    )
    grouped_residue = (
        facility_rows.groupby(["origin_province", "facility_class"])["residue_odt"]
        .agg(lambda values: math.fsum(float(value) for value in values))
        .to_dict()
    )
    for province in supply.PROVINCES:
        for facility_class in supply.CLASS_ORDER:
            expected_feedstock = float(
                reference.loc[
                    province, supply.CLASS_FEEDSTOCK_COLUMN[facility_class]
                ]
            )
            expected_product = float(
                reference.loc[
                    province, supply.CLASS_PRODUCT_COLUMN[facility_class]
                ]
            )
            expected_residue = float(
                reference.loc[
                    province, supply.CLASS_RESIDUE_COLUMN[facility_class]
                ]
            )
            assert grouped_feedstock.get(
                (province, facility_class), 0.0
            ) == pytest.approx(expected_feedstock, rel=1e-9, abs=1e-6)
            assert grouped_product.get(
                (province, facility_class), 0.0
            ) == pytest.approx(expected_product, rel=1e-9, abs=1e-6)
            assert grouped_residue.get(
                (province, facility_class), 0.0
            ) == pytest.approx(expected_residue, rel=1e-9, abs=1e-6)


def test_national_totals_match_product_chain(facility_rows, province_rows):
    reference = json.loads(PRODUCT_CHAIN_SUMMARY.read_text(encoding="utf-8"))[
        "national_totals"
    ]
    assert facility_rows["feedstock_m3"].sum() == pytest.approx(
        float(reference["total_feedstock_m3"]), rel=1e-9, abs=1e-6
    )
    assert facility_rows["residue_odt"].sum() == pytest.approx(
        float(reference["process_residue_odt"]), rel=1e-9, abs=1e-6
    )
    process = province_rows[province_rows["residue_type"] == "process_residue"]
    assert process["residue_odt"].sum() == pytest.approx(
        float(reference["process_residue_odt"]), rel=1e-9, abs=1e-6
    )
    assert process["residue_pj"].sum() == pytest.approx(
        float(reference["process_residue_odt"])
        * supply._parse_rsyc_basis().lhv_gj_per_odt
        / supply.PJ_GJ,
        rel=1e-9,
        abs=1e-6,
    )
    harvest_gross = province_rows[
        province_rows["residue_type"] == "harvest_residue_gross"
    ]
    harvest_recoverable = province_rows[
        province_rows["residue_type"] == "harvest_residue_recoverable"
    ]
    assert harvest_gross["residue_odt"].sum() == pytest.approx(
        float(reference["harvest_residue_gross_odt"]), rel=1e-9, abs=1e-6
    )
    assert harvest_recoverable["residue_odt"].sum() == pytest.approx(
        float(reference["harvest_residue_recoverable_odt"]),
        rel=1e-9,
        abs=1e-6,
    )


def test_odt_pj_conversion(facility_rows, province_rows, national_rows):
    rsvc = json.loads(RSYC_SUMMARY.read_text(encoding="utf-8"))
    lhv = float(rsvc["definitions"]["lhv_gj_per_odt"])
    assert lhv == pytest.approx(18.5)
    for _, row in facility_rows.iterrows():
        assert row["residue_pj"] == pytest.approx(
            row["residue_odt"] * lhv / supply.PJ_GJ, rel=1e-9, abs=1e-9
        )
        assert row["lhv_gj_per_odt"] == pytest.approx(lhv)
    for _, row in province_rows.iterrows():
        assert row["residue_pj"] == pytest.approx(
            row["residue_odt"] * lhv / supply.PJ_GJ, rel=1e-9, abs=1e-9
        )
        assert row["lhv_gj_per_odt"] == pytest.approx(lhv)
    total_lookup = dict(zip(national_rows["metric"], national_rows["value"]))
    assert total_lookup["process_residue_pj"] == pytest.approx(
        total_lookup["process_residue_odt"] * lhv / supply.PJ_GJ,
        rel=1e-9,
    )
    assert total_lookup["harvest_residue_recoverable_pj"] == pytest.approx(
        total_lookup["harvest_residue_recoverable_odt"]
        * lhv
        / supply.PJ_GJ,
        rel=1e-9,
    )


def test_rsyc_comparison(province_rows, national_rows):
    reference = _reference_product_chain()
    province_lookup = reference.set_index("province")
    recoverable = province_rows[
        province_rows["residue_type"] == "harvest_residue_recoverable"
    ].set_index("province")
    for province in supply.PROVINCES:
        assert recoverable.loc[province, "rsyc_base_recoverable_odt"] == pytest.approx(
            province_lookup.loc[province, "rsyc_base_recoverable_odt"],
            rel=1e-12,
            abs=1e-6,
        )
        assert recoverable.loc[province, "rsyc_ratio"] == pytest.approx(
            province_lookup.loc[province, "rsyc_ratio"], rel=1e-12
        )
        assert recoverable.loc[province, "rsyc_status"] == province_lookup.loc[
            province, "rsyc_status"
        ]
    total_lookup = dict(zip(national_rows["metric"], national_rows["value"]))
    reference_summary = json.loads(
        PRODUCT_CHAIN_SUMMARY.read_text(encoding="utf-8")
    )
    assert total_lookup["rsyc_base_119m_recoverable_odt"] == pytest.approx(
        7_080_062.08603234, rel=1e-9
    )
    assert total_lookup["rsyc_base_119m_recoverable_pj"] == pytest.approx(
        7_080_062.08603234 * 18.5 / supply.PJ_GJ, rel=1e-9
    )
    assert total_lookup["rsyc_base_100m_recoverable_pj"] == pytest.approx(
        129.6709066255944, rel=1e-9
    )
    assert total_lookup["rsyc_ratio"] == pytest.approx(
        float(reference_summary["rsyc_comparison"]["ratio"]), rel=1e-12
    )
    assert total_lookup["rsyc_ratio"] == pytest.approx(0.7685618064703, rel=1e-9)
    assert total_lookup["process_residue_odt"] > total_lookup[
        "harvest_residue_recoverable_odt"
    ]
    assert total_lookup["combined_process_plus_harvest_recoverable_odt"] == pytest.approx(
        total_lookup["process_residue_odt"]
        + total_lookup["harvest_residue_recoverable_odt"],
        rel=1e-12,
    )


def test_harvest_residue_is_separate(facility_rows, province_rows):
    assert set(facility_rows["residue_type"]) == {"process_residue"}
    harvest = province_rows[province_rows["residue_type"].str.startswith("harvest")]
    assert set(harvest["facility_count"]) == {0}
    assert set(harvest["facility_assigned"]) == {"false"}
    for province in supply.PROVINCES:
        gross = float(
            province_rows.loc[
                (province_rows["province"] == province)
                & (
                    province_rows["residue_type"]
                    == "harvest_residue_gross"
                ),
                "residue_odt",
            ].iloc[0]
        )
        recoverable = float(
            province_rows.loc[
                (province_rows["province"] == province)
                & (
                    province_rows["residue_type"]
                    == "harvest_residue_recoverable"
                ),
                "residue_odt",
            ].iloc[0]
        )
        unrecovered = float(
            province_rows.loc[
                (province_rows["province"] == province)
                & (
                    province_rows["residue_type"]
                    == "harvest_residue_unrecovered"
                ),
                "residue_odt",
            ].iloc[0]
        )
        assert gross == pytest.approx(recoverable + unrecovered, rel=1e-9)
    recoverable_rows = province_rows[
        province_rows["residue_type"] == "harvest_residue_recoverable"
    ]
    assert (recoverable_rows["rsyc_comparable"] == "true").all()
    non_recoverable = province_rows[
        province_rows["residue_type"] != "harvest_residue_recoverable"
    ]
    assert (non_recoverable["rsyc_comparable"] == "false").all()


def test_no_nan_or_negative(facility_rows, province_rows, national_rows):
    for name, frame, columns in (
        ("facility", facility_rows, FACILITY_NUMERIC),
        ("province", province_rows, PROVINCE_NUMERIC),
    ):
        numeric = frame[columns]
        assert not numeric.isna().any().any(), name
        assert (numeric >= 0.0).all().all(), name
        assert all(
            math.isfinite(float(value)) for value in numeric.to_numpy().ravel()
        )
    assert all(math.isfinite(float(value)) for value in national_rows["value"])
    assert (national_rows["value"] >= 0.0).all()
    assert not facility_rows["residue_odt"].isna().any()


def test_proxy_share(facility_rows, province_rows, national_rows, built):
    summary, _ = built
    total = math.fsum(float(value) for value in facility_rows["residue_odt"])
    assert total > 0.0
    proxy = math.fsum(
        float(value)
        for value in facility_rows.loc[
            facility_rows["capacity_status"] != "published", "residue_odt"
        ]
    )
    scaled = math.fsum(
        float(value)
        for value in facility_rows.loc[
            facility_rows["capacity_status"] == "proxy_harvest_scaled",
            "residue_odt",
        ]
    )
    proxy_share = proxy / total
    scaled_share = scaled / total
    assert 0.0 < proxy_share < 1.0
    assert proxy_share == pytest.approx(
        summary["concentration"]["proxy_residue_share"], rel=1e-12
    )
    assert scaled_share == pytest.approx(
        summary["concentration"]["scaled_proxy_residue_share"], rel=1e-12
    )
    assert set(facility_rows["capacity_status"]).issubset(
        {"published", "proxy", "proxy_harvest_scaled"}
    )
    assert (facility_rows["capacity_m3_y"] > 0.0).all()
    assert (facility_rows["assigned_m3"] <= facility_rows["capacity_m3_y"] * 1.000000001).all()
    process = province_rows[
        (province_rows["residue_type"] == "process_residue")
        & (province_rows["residue_odt"] > 0.0)
    ]
    assert (
        process["published_residue_share"]
        + process["proxy_residue_share"]
    ).sub(1.0).abs().max() < 1e-12
    # Province proxy shares use the physical facility stream; national must match.
    total_lookup = dict(zip(national_rows["metric"], national_rows["value"]))
    assert total_lookup["proxy_residue_share"] == pytest.approx(
        proxy_share, rel=1e-12
    )
    assert total_lookup["scaled_proxy_residue_share"] == pytest.approx(
        scaled_share, rel=1e-12
    )
    assignment_summary = json.loads(
        ASSIGNMENT_SUMMARY.read_text(encoding="utf-8")
    )
    assert summary["assignment_v3"]["assigned_m3"] == pytest.approx(
        float(assignment_summary["scenarios"][supply.SCENARIO]["assigned_m3"]),
        rel=1e-12,
    )


def test_summary_manifest_labels_and_report(built):
    summary, target = built
    assert summary["model_estimate"] is True
    assert summary["observed"] is False
    assert summary["policy_grade"] is False
    assert summary["facility_assignment"] is True
    assert summary["ui_or_public_osecan_changed"] is False
    assert summary["rsyc_comparison"]["lhv_gj_per_odt"] == pytest.approx(18.5)
    saved = json.loads((target / "summary.json").read_text(encoding="utf-8"))
    assert saved["artifact"] == "facility_residue_supply_v1"
    assert saved["status"] == "pass"
    manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["labels"]["model_estimate"] is True
    assert manifest["labels"]["observed"] is False
    assert manifest["labels"]["policy_grade"] is False
    assert manifest["labels"]["facility_assignment"] is True
    assert manifest["labels"]["ui_or_public_osecan_changed"] is False
    for name, metadata in manifest["outputs"].items():
        path = target / name
        assert path.is_file(), name
        assert path.stat().st_size == metadata["bytes"], name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == metadata["sha256"]
    text = (target / "FACILITY_RESIDUE_SUPPLY_V1.md").read_text(encoding="utf-8")
    for phrase in (
        "National Facility Residue Supply v1",
        "## Facility residue",
        "## Province/national totals",
        "## RSYC comparison",
        "## Proxy/concentration",
        "## Labels and caveats",
        "## Tests and status",
        "No policy/AAC claim is made",
        "observed=false",
        "policy_grade=false",
        "129.6709",
    ):
        assert phrase in text, phrase
    for _, row in _read_csv(target / "facility_residue_supply.csv").iterrows():
        assert _label(row["model_estimate"]) == "true"
        assert _label(row["observed"]) == "false"
        assert _label(row["policy_grade"]) == "false"
        assert _label(row["facility_assignment"]) == "true"


def test_no_unexpected_deletions_gate():
    supply.assert_no_unexpected_deletions()
