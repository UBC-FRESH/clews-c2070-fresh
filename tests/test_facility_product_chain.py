"""Regression tests for the national facility product/residue chain v1."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import pandas as pd
import pytest

from workflows.national.facilities import facility_product_chain as chain

ARTIFACT_DIR = chain.OUTPUT_DIR
FACILITY_CSV = ARTIFACT_DIR / "facility_product_chain.csv"
PROVINCE_CSV = ARTIFACT_DIR / "province_product_chain.csv"
CLASS_CSV = ARTIFACT_DIR / "province_class_product_chain.csv"
NATIONAL_CSV = ARTIFACT_DIR / "national_summary.csv"
SUMMARY_JSON = ARTIFACT_DIR / "summary.json"
MANIFEST_JSON = ARTIFACT_DIR / "manifest.json"
REPORT_MD = ARTIFACT_DIR / "FACILITY_PRODUCT_CHAIN_V1.md"
PRODUCT_CHAIN_CSV = (
    chain.PRODUCT_CHAIN_DIR / "product_chain_by_province.csv"
)
PRODUCT_CHAIN_SUMMARY = chain.PRODUCT_CHAIN_DIR / "summary.json"
ASSIGNMENT_CSV = chain.ASSIGNMENT_DIR / "facility_assignment.csv"
ASSIGNMENT_SUMMARY = chain.ASSIGNMENT_DIR / "summary.json"

FACILITY_NUMERIC = [
    "assignment_weight_m3",
    "assignment_share",
    "capacity_m3_y",
    "assignment_utilization_v2",
    "feedstock_m3",
    "share_of_origin_class_feedstock",
    "origin_class_reference_feedstock_m3",
    "product_quantity",
    "product_per_m3",
    "process_residue_odt",
    "process_residue_m3",
    "residue_odt_per_m3",
    "facility_total_feedstock_m3",
    "facility_share_of_national_feedstock",
    "facility_share_of_class_feedstock",
]
PROVINCE_NUMERIC = [
    "harvest_m3",
    "primary_feedstock_m3",
    "residue_feedstock_m3",
    "total_feedstock_m3",
    *chain.CLASS_FEEDSTOCK_COLUMN.values(),
    "facility_feedstock_m3",
    "max_abs_class_feedstock_gap_m3",
    "local_feedstock_m3",
    "cross_border_feedstock_m3",
    "cross_border_feedstock_share",
    "proxy_feedstock_m3",
    "proxy_feedstock_share",
    "fallback_feedstock_m3",
    "fallback_feedstock_share",
    "facility_count_origin",
    "hhi_origin_feedstock",
    "top1_facility_share_origin",
    *chain.CLASS_PRODUCT_COLUMN.values(),
    *chain.CLASS_RESIDUE_COLUMN.values(),
    "process_residue_odt",
    "process_residue_m3",
    "process_residue_odt_per_m3_feedstock",
    "harvest_residue_gross_m3",
    "harvest_residue_gross_odt",
    "harvest_residue_recoverable_m3",
    "harvest_residue_recoverable_odt",
    "harvest_residue_unrecovered_m3",
    "harvest_residue_unrecovered_odt",
    "total_residue_gross_odt",
    "total_residue_recoverable_odt",
    "rsyc_base_recoverable_odt",
    "model_recoverable_odt_base",
    "rsyc_ratio",
    "max_relative_product_gap",
    "max_relative_residue_gap",
]
CLASS_NUMERIC = [
    "feedstock_m3",
    "facility_feedstock_m3",
    "feedstock_gap_m3",
    "product_quantity",
    "reference_product_quantity",
    "product_gap",
    "process_residue_odt",
    "reference_process_residue_odt",
    "process_residue_gap_odt",
    "facility_count",
    "local_feedstock_m3",
    "cross_border_feedstock_m3",
    "proxy_feedstock_m3",
    "fallback_feedstock_m3",
    "product_per_m3",
    "residue_odt_per_m3",
]


def _read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, keep_default_na=False)


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
            *chain.CLASS_FEEDSTOCK_COLUMN.values(),
            *chain.CLASS_PRODUCT_COLUMN.values(),
            *chain.CLASS_RESIDUE_COLUMN.values(),
            "harvest_m3",
        ],
    )


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> tuple[dict, Path]:
    target = tmp_path_factory.mktemp("facility_product_chain")
    summary = chain.build(output_dir=target)
    return summary, target


@pytest.fixture(scope="module")
def facility_rows(built) -> pd.DataFrame:
    _, target = built
    return _numeric(_read_csv(target / "facility_product_chain.csv"), FACILITY_NUMERIC)


@pytest.fixture(scope="module")
def province_rows(built) -> pd.DataFrame:
    _, target = built
    return _numeric(_read_csv(target / "province_product_chain.csv"), PROVINCE_NUMERIC)


@pytest.fixture(scope="module")
def class_rows(built) -> pd.DataFrame:
    _, target = built
    return _numeric(_read_csv(target / "province_class_product_chain.csv"), CLASS_NUMERIC)


@pytest.fixture(scope="module")
def national_rows(built) -> pd.DataFrame:
    _, target = built
    frame = _read_csv(target / "national_summary.csv")
    frame["value"] = pd.to_numeric(frame["value"], errors="raise")
    return frame


def _label(value) -> str:
    return str(value).strip().lower()


def test_build_writes_all_deliverables(built):
    summary, target = built
    assert summary["status"] == "pass"
    assert summary["validation"]["passed"] is True
    assert summary["validation"]["failed_check_count"] == 0
    assert summary["scenario"] == chain.SCENARIO
    for name in (
        "facility_product_chain.csv",
        "province_product_chain.csv",
        "province_class_product_chain.csv",
        "national_summary.csv",
        "summary.json",
        "FACILITY_PRODUCT_CHAIN_V1.md",
        "manifest.json",
    ):
        assert (target / name).is_file(), name
    assert summary["class_join"]["allocation_rows"] > 0
    assert summary["class_join"]["facilities_used"] > 0


def test_class_matching(facility_rows, class_rows):
    assert set(facility_rows["facility_class"]).issubset(set(chain.CLASS_ORDER))
    assert (facility_rows["feedstock_class"] == facility_rows["facility_class"]).all()
    per_facility = facility_rows.groupby("facility_id")["facility_class"].nunique()
    assert (per_facility == 1).all()
    assert set(facility_rows["allocation_scope"]).issubset(
        {"province_assignment", "national_class_fallback"}
    )
    assert set(facility_rows["allocation_basis"]) == {"assignment_v2_assigned_m3"}
    assert set(facility_rows["conversion_basis"]) == {
        "product_chain_effective_province_class_ratio"
    }
    assert set(class_rows["allocation_scopes"]).issubset(
        {"province_assignment", "national_class_fallback", "zero"}
    )
    for _, row in class_rows.iterrows():
        if row["facility_feedstock_m3"] > 0.0:
            assert row["facility_count"] > 0
            assert row["allocation_scopes"] in {
                "province_assignment",
                "national_class_fallback",
            }


def test_province_class_feedstock_conservation(facility_rows, class_rows):
    reference = _reference_product_chain().set_index("province")
    grouped = (
        facility_rows.groupby(["origin_province", "facility_class"])["feedstock_m3"]
        .agg(lambda values: math.fsum(float(value) for value in values))
        .to_dict()
    )
    for province in chain.PROVINCES:
        for facility_class in chain.CLASS_ORDER:
            expected = float(
                reference.loc[province, chain.CLASS_FEEDSTOCK_COLUMN[facility_class]]
            )
            actual = float(grouped.get((province, facility_class), 0.0))
            assert actual == pytest.approx(expected, rel=1e-9, abs=1e-6), (
                province,
                facility_class,
            )
    for _, row in class_rows.iterrows():
        assert row["feedstock_gap_m3"] == pytest.approx(
            0.0, abs=1e-6
        ), (row["province"], row["facility_class"])


def test_national_products_and_residue_conservation(
    facility_rows, province_rows, national_rows
):
    summary = json.loads(PRODUCT_CHAIN_SUMMARY.read_text(encoding="utf-8"))
    reference = summary["national_totals"]
    for facility_class in chain.CLASS_ORDER:
        product = math.fsum(
            float(value)
            for value in facility_rows.loc[
                facility_rows["facility_class"] == facility_class, "product_quantity"
            ]
        )
        expected = float(reference[chain.CLASS_PRODUCT_COLUMN[facility_class]])
        assert product == pytest.approx(expected, rel=1e-8, abs=1e-6)
        residue = math.fsum(
            float(value)
            for value in facility_rows.loc[
                facility_rows["facility_class"] == facility_class,
                "process_residue_odt",
            ]
        )
        expected_residue = float(reference[chain.CLASS_RESIDUE_COLUMN[facility_class]])
        assert residue == pytest.approx(expected_residue, rel=1e-8, abs=1e-6)
    total_lookup = dict(zip(national_rows["metric"], national_rows["value"]))
    for metric in (
        "total_feedstock_m3",
        "sawlog_product_m3",
        "pulp_product_odt",
        "panel_product_m3",
        "pellet_product_odt",
        "bio_product_mwh",
        "process_residue_odt",
        "harvest_residue_gross_odt",
        "harvest_residue_recoverable_odt",
    ):
        assert total_lookup[metric] == pytest.approx(
            float(reference[metric]), rel=1e-8, abs=1e-6
        ), metric
    assert province_rows["facility_feedstock_m3"].sum() == pytest.approx(
        float(reference["total_feedstock_m3"]), rel=1e-9, abs=1e-6
    )


def test_allocation_is_proportional_to_assigned_volume(facility_rows):
    assignment = _read_csv(ASSIGNMENT_CSV)
    assignment = assignment[assignment["scenario"] == chain.SCENARIO].copy()
    assignment["assigned_m3"] = pd.to_numeric(
        assignment["assigned_m3"], errors="raise"
    )
    local_pairs = set(
        zip(
            assignment["province"].astype(str).str.strip().str.upper(),
            assignment["facility_class"].astype(str).str.strip().str.upper(),
        )
    )
    for (province, facility_class), group in facility_rows.groupby(
        ["origin_province", "facility_class"]
    ):
        assert group["assignment_share"].sum() == pytest.approx(1.0, rel=1e-9)
        assert group["allocation_scope"].nunique() == 1
        scope = group["allocation_scope"].iloc[0]
        if scope == "province_assignment":
            assert (province, facility_class) in local_pairs
        else:
            assert (province, facility_class) not in local_pairs
        weight_total = math.fsum(float(value) for value in group["assignment_weight_m3"])
        reference = float(group["origin_class_reference_feedstock_m3"].iloc[0])
        for _, row in group.iterrows():
            assert row["assignment_share"] == pytest.approx(
                row["assignment_weight_m3"] / weight_total, rel=1e-9
            )
            assert row["feedstock_m3"] == pytest.approx(
                reference * row["assignment_share"], rel=1e-9, abs=1e-9
            )
        assert group["feedstock_m3"].sum() == pytest.approx(
            reference, rel=1e-9, abs=1e-6
        )


def test_proxy_capacity_share(facility_rows, province_rows, national_rows, built):
    summary, _ = built
    facilities = facility_rows.drop_duplicates("facility_id")
    total = math.fsum(
        float(value) for value in facilities["facility_total_feedstock_m3"]
    )
    proxy = math.fsum(
        float(value)
        for value in facilities.loc[
            facilities["capacity_status"] == "proxy",
            "facility_total_feedstock_m3",
        ]
    )
    assert total > 0.0
    proxy_share = proxy / total
    assert 0.0 < proxy_share < 1.0
    assert proxy_share == pytest.approx(
        summary["concentration"]["proxy_feedstock_share_of_total"], rel=1e-12
    )
    assert proxy_share == pytest.approx(
        summary["proxy_capacity"]["facility_feedstock_proxy_share"], rel=1e-12
    )
    assert set(facilities["capacity_status"]).issubset({"published", "proxy"})
    assert (facilities["capacity_m3_y"] > 0.0).all()
    assignment_summary = json.loads(
        ASSIGNMENT_SUMMARY.read_text(encoding="utf-8")
    )
    assignment_proxy = float(
        assignment_summary["scenarios"][chain.SCENARIO]["proxy_share_of_assigned"]
    )
    assert summary["assignment_v2"]["proxy_share_of_assigned"] == pytest.approx(
        assignment_proxy, rel=1e-12
    )
    assert 0.0 < assignment_proxy < 1.0
    assert summary["proxy_capacity"]["caveat"]
    total_lookup = dict(zip(national_rows["metric"], national_rows["value"]))
    assert total_lookup["proxy_feedstock_share_of_total"] == pytest.approx(
        proxy_share, rel=1e-12
    )
    assert province_rows["proxy_feedstock_m3"].sum() == pytest.approx(
        proxy, rel=1e-9, abs=1e-6
    )


def test_no_nan_or_negative(facility_rows, province_rows, class_rows, national_rows):
    for name, frame, columns in (
        ("facility", facility_rows, FACILITY_NUMERIC),
        ("province", province_rows, PROVINCE_NUMERIC),
        ("class", class_rows, ["feedstock_m3", "facility_feedstock_m3", "product_quantity", "process_residue_odt"]),
    ):
        numeric = frame[columns]
        assert not numeric.isna().any().any(), name
        assert (numeric >= 0.0).all().all(), name
        assert all(math.isfinite(float(value)) for value in numeric.to_numpy().ravel())
    assert all(math.isfinite(float(value)) for value in national_rows["value"])
    assert (national_rows["value"] >= 0.0).all()
    assert not facility_rows["feedstock_m3"].isna().any()


def test_rsyc_comparison(province_rows, national_rows, built):
    summary, _ = built
    reference = _numeric(
        _read_csv(PRODUCT_CHAIN_CSV),
        ["harvest_m3", "harvest_residue_gross_odt", "rsyc_base_recoverable_odt"],
    )
    reference_status = dict(
        zip(reference["province"], reference["rsyc_status"])
    )
    for _, row in province_rows.iterrows():
        assert row["rsyc_status"] == reference_status[row["province"]]
        if row["harvest_m3"] > 0.0:
            assert row["rsyc_base_recoverable_odt"] > 0.0
            assert row["model_recoverable_odt_base"] == pytest.approx(
                row["harvest_residue_gross_odt"] * chain.RSYC_BASE_RECOVERY,
                rel=1e-9,
                abs=1e-9,
            )
            assert row["rsyc_ratio"] == pytest.approx(
                row["model_recoverable_odt_base"]
                / row["rsyc_base_recoverable_odt"],
                rel=1e-9,
            )
    total_lookup = dict(zip(national_rows["metric"], national_rows["value"]))
    assert total_lookup["model_recoverable_odt_base"] == pytest.approx(
        total_lookup["harvest_residue_gross_odt"] * chain.RSYC_BASE_RECOVERY,
        rel=1e-9,
        abs=1e-6,
    )
    assert total_lookup["rsyc_ratio"] == pytest.approx(
        total_lookup["model_recoverable_odt_base"]
        / total_lookup["rsyc_base_recoverable_odt"],
        rel=1e-9,
    )
    upstream = json.loads(PRODUCT_CHAIN_SUMMARY.read_text(encoding="utf-8"))
    assert summary["rsyc_comparison"]["ratio"] == pytest.approx(
        float(upstream["rsyc_comparison"]["ratio"]), rel=1e-9
    )
    assert summary["rsyc_comparison"]["status"] == "model_below_rsyc"


def test_harvest_residue_is_separate(province_rows, national_rows):
    assert set(province_rows["harvest_residue_scope"]) == {"province_only"}
    for _, row in province_rows.iterrows():
        assert row["total_residue_gross_odt"] == pytest.approx(
            row["process_residue_odt"] + row["harvest_residue_gross_odt"],
            rel=1e-9,
            abs=1e-6,
        )
        assert row["total_residue_recoverable_odt"] == pytest.approx(
            row["process_residue_odt"] + row["harvest_residue_recoverable_odt"],
            rel=1e-9,
            abs=1e-6,
        )
        assert row["harvest_residue_gross_odt"] == pytest.approx(
            row["harvest_residue_recoverable_odt"]
            + row["harvest_residue_unrecovered_odt"],
            rel=1e-9,
            abs=1e-6,
        )
    total_lookup = dict(zip(national_rows["metric"], national_rows["value"]))
    assert total_lookup["total_residue_gross_odt"] == pytest.approx(
        total_lookup["process_residue_odt"]
        + total_lookup["harvest_residue_gross_odt"],
        rel=1e-9,
        abs=1e-6,
    )


def test_concentration_and_top_facilities(facility_rows, built):
    summary, _ = built
    facilities = facility_rows.drop_duplicates("facility_id").set_index("facility_id")
    total = math.fsum(
        float(value) for value in facilities["facility_total_feedstock_m3"]
    )
    shares = sorted(
        (
            float(value) / total
            for value in facilities["facility_total_feedstock_m3"]
        ),
        reverse=True,
    )
    hhi = math.fsum(share * share for share in shares)
    assert summary["concentration"]["hhi"] == pytest.approx(hhi, rel=1e-12)
    assert summary["concentration"]["top1_share"] == pytest.approx(shares[0], rel=1e-12)
    assert summary["concentration"]["top5_share"] == pytest.approx(
        math.fsum(shares[:5]), rel=1e-12
    )
    top = summary["top_facilities"]
    assert 0 < len(top) <= 20
    assert [row["rank"] for row in top] == list(range(1, len(top) + 1))
    assert [row["facility_id"] for row in top] == sorted(
        facilities.index,
        key=lambda facility_id: float(
            facilities.loc[facility_id, "facility_total_feedstock_m3"]
        ),
        reverse=True,
    )[: len(top)]
    for row in top:
        assert row["share_of_facility_feedstock"] == pytest.approx(
            float(
                facilities.loc[
                    row["facility_id"], "facility_total_feedstock_m3"
                ]
            )
            / total,
            rel=1e-12,
        )


def test_summary_manifest_labels_and_report(built):
    summary, target = built
    assert summary["model_estimate"] is True
    assert summary["observed"] is False
    assert summary["policy_grade"] is False
    assert summary["facility_assignment"] is True
    assert summary["ui_or_public_osecan_changed"] is False
    assert summary["rsyc_comparison"]["base_recovery_fraction"] == pytest.approx(
        chain.RSYC_BASE_RECOVERY
    )
    saved = json.loads((target / "summary.json").read_text(encoding="utf-8"))
    assert saved["artifact"] == "facility_product_chain_v1"
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
    text = REPORT_MD.read_text(encoding="utf-8")
    for phrase in (
        "National Facility Product/Residue Chain v1",
        "## Method and class joining",
        "## National facility products and process residue",
        "## Provincial products and residue",
        "## Conservation and RSYC",
        "## Concentration and proxy capacity",
        "## Labels and caveats",
        "## Tests and status",
        "No policy/AAC claim is made",
        "observed=false",
        "policy_grade=false",
    ):
        assert phrase in text, phrase
    for _, row in _read_csv(FACILITY_CSV).iterrows():
        assert _label(row["model_estimate"]) == "true"
        assert _label(row["observed"]) == "false"
        assert _label(row["policy_grade"]) == "false"
        assert _label(row["facility_assignment"]) == "true"
        assert _label(row["harvest_residue_included"]) == "false"


def test_repository_hygiene_gate():
    chain.assert_no_unexpected_deletions()
