"""Regression tests for the all-zone forestry residue OSeCAN handoff v2."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd
import pytest

from workflows.national.facilities import forestry_osecan_handoff_v2 as handoff

ARTIFACT = handoff.ARTIFACT
HANDOFF_CSV = "forestry_residue_handoff.csv"
WEIGHT_CSV = "zone_allocation_weights.csv"
ZONE_CSV = "zone_summary.csv"
COMPARISON_CSV = "supply_curve_comparison.csv"
SUMMARY_JSON = "summary.json"
MANIFEST_JSON = "manifest.json"
REPORT_MD = "FORESTRY_OSECAN_HANDOFF_V2.md"

NUMERIC_HANDOFF = [
    "zone_weight",
    "availability_fraction",
    "odt",
    "pj",
    "lhv_gj_per_odt",
    "supply_curve_tier_upper_pj",
]
NUMERIC_WEIGHT = ["weight", "source_value"]
NUMERIC_ZONE = [
    "odt",
    "pj",
    "national_case_pj",
    "share_of_national_case",
]
NUMERIC_COMPARISON = [
    "case_odt",
    "case_pj",
    "reference_pj",
    "headroom_pj",
]


def _read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, keep_default_na=False, dtype=str)


def _numeric(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    for column in columns:
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    return frame


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> tuple[dict, Path]:
    target = tmp_path_factory.mktemp("forestry_osecan_handoff_v2")
    summary = handoff.build(output_dir=target)
    return summary, target


@pytest.fixture(scope="module")
def handoff_rows(built) -> pd.DataFrame:
    _, target = built
    return _numeric(_read_csv(target / HANDOFF_CSV), NUMERIC_HANDOFF)


@pytest.fixture(scope="module")
def weight_rows(built) -> pd.DataFrame:
    _, target = built
    return _numeric(_read_csv(target / WEIGHT_CSV), NUMERIC_WEIGHT)


@pytest.fixture(scope="module")
def zone_rows(built) -> pd.DataFrame:
    _, target = built
    return _numeric(_read_csv(target / ZONE_CSV), NUMERIC_ZONE)


@pytest.fixture(scope="module")
def comparison_rows(built) -> pd.DataFrame:
    _, target = built
    return _numeric(_read_csv(target / COMPARISON_CSV), NUMERIC_COMPARISON)


@pytest.fixture(scope="module")
def summary_json(built) -> dict:
    _, target = built
    return json.loads((target / SUMMARY_JSON).read_text(encoding="utf-8"))


def test_build_writes_all_deliverables(built):
    summary, target = built
    assert summary["artifact"] == ARTIFACT
    assert summary["status"] == "pass"
    assert summary["validation"]["passed"] is True
    assert summary["validation"]["failed_check_count"] == 0
    for name in (
        HANDOFF_CSV,
        WEIGHT_CSV,
        ZONE_CSV,
        COMPARISON_CSV,
        SUMMARY_JSON,
        MANIFEST_JSON,
        REPORT_MD,
    ):
        path = target / name
        assert path.is_file(), name
        assert path.stat().st_size > 0, name
    assert summary["labels"] == {
        "model_estimate": True,
        "observed": False,
        "policy_grade": False,
        "official_osecan_changed": False,
        "not_policy": True,
        "context_is_policy_target": False,
        "proxy_shares_explicit": True,
    }


def test_zone_weights_sum_and_are_explicit(weight_rows, summary_json):
    assert len(weight_rows) == 17
    assert set(weight_rows["province"]) == set(handoff.PROVINCES)
    assert set(weight_rows["zone_id"]) == set(handoff.ZONE_ORDER)
    assert not weight_rows.duplicated(["province", "zone_id"]).any()
    for province, group in weight_rows.groupby("province"):
        assert math.fsum(group["weight"]) == pytest.approx(1.0, rel=0, abs=1e-12), (
            province
        )
        assert (group["weight"] > 0.0).all()
        expected_proxy = province in handoff.MULTI_ZONE_PROVINCES
        assert set(group["proxy_share"]) == {"true" if expected_proxy else "false"}
    assert (weight_rows["model_estimate"] == "true").all()
    assert (weight_rows["observed"] == "false").all()
    assert (weight_rows["policy_grade"] == "false").all()
    assert (weight_rows["official_osecan_changed"] == "false").all()
    assert (weight_rows["weight_method"].str.len() > 0).all()
    assert (weight_rows["evidence_tier"].str.len() > 0).all()
    assert (weight_rows["notes"].str.len() > 0).all()

    bc = weight_rows[weight_rows["province"] == "BC"]
    assert set(bc["weight_method"]) == {"osecan_zonal_supply_dispatch"}
    assert set(bc["evidence_tier"]) == {"tier_1_osecan_zonal_supply_model"}
    for province in ("AB", "ON", "QC"):
        group = weight_rows[weight_rows["province"] == province]
        assert set(group["weight_method"]) == {"osecan_zonal_dispatch_proxy"}
        assert set(group["evidence_tier"]) == {"tier_2_osecan_zonal_dispatch_model"}
    single = weight_rows[weight_rows["province"].isin(handoff.SINGLE_ZONE_PROVINCES)]
    assert set(single["weight_method"]) == {"single_zone_crosswalk"}
    assert set(single["evidence_tier"]) == {"tier_0_crosswalk_membership"}

    allocation = summary_json["zone_allocation"]
    assert allocation["unmapped_province_count"] == 0
    assert allocation["proxy_share_provinces"] == ["BC", "AB", "ON", "QC"]
    assert allocation["zone_count"] == 13


def test_handoff_rows_labels_and_no_unmapped(handoff_rows, summary_json):
    required = {
        "province",
        "zone_id",
        "zone_mapped",
        "zone_weight",
        "zone_weight_method",
        "zone_evidence_tier",
        "proxy_share",
        "allocation_status",
        "stream",
        "availability_case",
        "odt",
        "pj",
        "model_estimate",
        "observed",
        "policy_grade",
        "official_osecan_changed",
    }
    assert required.issubset(set(handoff_rows.columns))
    assert len(handoff_rows) == 119
    assert not handoff_rows.duplicated(
        ["province", "zone_id", "stream", "availability_case"]
    ).any()
    assert set(handoff_rows["zone_id"]) == set(handoff.ZONE_ORDER)
    assert handoff.UNMAPPED_ZONE not in set(handoff_rows["zone_id"])
    assert (handoff_rows["zone_mapped"] == "true").all()
    assert set(handoff_rows["stream"]) == {"process", "harvest"}
    assert set(handoff_rows["residue_class"]) == {"TOTAL", "ALL"}
    assert (handoff_rows["model_estimate"] == "true").all()
    assert (handoff_rows["observed"] == "false").all()
    assert (handoff_rows["policy_grade"] == "false").all()
    assert (handoff_rows["official_osecan_changed"] == "false").all()
    assert (handoff_rows["evidence_tier"].str.len() > 0).all()
    assert (handoff_rows["zone_weight_method"].str.len() > 0).all()
    assert (handoff_rows["zone_evidence_tier"].str.len() > 0).all()
    assert (handoff_rows["caveat"].str.len() > 0).all()

    multi = handoff_rows[handoff_rows["province"].isin(handoff.MULTI_ZONE_PROVINCES)]
    assert set(multi["proxy_share"]) == {"true"}
    assert set(multi["allocation_status"]) == {"proxy_share"}
    single = handoff_rows[handoff_rows["province"].isin(handoff.SINGLE_ZONE_PROVINCES)]
    assert set(single["proxy_share"]) == {"false"}
    assert set(single["allocation_status"]) == {"single_zone"}

    for zone_id, members in {
        "T1": {"NB", "NS", "PE"},
        "N1": {"YT", "NT", "NU"},
    }.items():
        aggregate = handoff_rows[
            (handoff_rows["zone_id"] == zone_id)
            & (handoff_rows["province"].isin(members))
        ]
        assert set(aggregate["zone_mapping_status"]) == {"mapped_aggregate_zone"}

    for (province, stream, case), group in handoff_rows.groupby(
        ["province", "stream", "availability_case"]
    ):
        assert math.fsum(group["zone_weight"]) == pytest.approx(
            1.0, rel=0, abs=1e-12
        ), (province, stream, case)
    assert summary_json["zone_mapping"]["unmapped_provinces"] == []


def test_pj_arithmetic_and_province_conservation(handoff_rows):
    expected_pj = handoff_rows["odt"] * handoff.EXPECTED_LHV_GJ_PER_ODT / handoff.PJ_GJ
    assert all(
        abs(left - right) <= 1e-6 + 1e-8 * abs(right)
        for left, right in zip(handoff_rows["pj"], expected_pj)
    )
    assert (handoff_rows["lhv_gj_per_odt"] == 18.5).all()

    province_curve = _numeric(
        _read_csv(handoff.PROVINCE_CURVE),
        ["availability_fraction", "residue_odt", "residue_pj"],
    )
    selected = province_curve[
        (
            (province_curve["stream"] == "process")
            & (province_curve["residue_class"] == "TOTAL")
        )
        | (
            (province_curve["stream"] == "harvest")
            & (province_curve["residue_class"] == "ALL")
        )
    ]
    for _, record in selected.iterrows():
        rows = handoff_rows[
            (handoff_rows["province"] == record["province"])
            & (handoff_rows["stream"] == record["stream"])
            & (handoff_rows["availability_case"] == record["availability_case"])
        ]
        assert len(rows) >= 1
        assert math.fsum(rows["pj"]) == pytest.approx(
            float(record["residue_pj"]), rel=1e-9, abs=1e-6
        )
        assert math.fsum(rows["odt"]) == pytest.approx(
            float(record["residue_odt"]), rel=1e-9, abs=1e-3
        )


def test_national_reconciliation_and_zero_cases(handoff_rows, summary_json):
    national = _numeric(
        _read_csv(handoff.NATIONAL_CURVE),
        ["availability_fraction", "residue_odt", "residue_pj"],
    )
    selected = national[
        ((national["stream"] == "process") & (national["residue_class"] == "TOTAL"))
        | ((national["stream"] == "harvest") & (national["residue_class"] == "ALL"))
    ]
    for _, record in selected.iterrows():
        rows = handoff_rows[
            (handoff_rows["stream"] == record["stream"])
            & (handoff_rows["availability_case"] == record["availability_case"])
        ]
        assert len(rows) >= 13
        assert math.fsum(rows["pj"]) == pytest.approx(
            float(record["residue_pj"]), rel=1e-9, abs=1e-5
        )
        assert math.fsum(rows["odt"]) == pytest.approx(
            float(record["residue_odt"]), rel=1e-9, abs=1e-3
        )

    zero = handoff_rows[handoff_rows["availability_case"] == "process_availability_000"]
    assert len(zero) == 17
    assert (zero["pj"] == 0.0).all()
    assert (zero["odt"] == 0.0).all()
    positive = handoff_rows[
        handoff_rows["availability_case"] != "process_availability_000"
    ]
    assert (positive["pj"] > 0.0).any()
    assert summary_json["handoff_totals"]["process"][
        "process_availability_050"
    ] == pytest.approx(263.002783555, rel=1e-9)
    assert summary_json["handoff_totals"]["harvest"][
        "harvest_recovery_060"
    ] == pytest.approx(100.667108175, rel=1e-9)


def test_zone_summary_all_zones_and_conservation(zone_rows, summary_json):
    assert len(zone_rows) == 91
    assert set(zone_rows["zone_id"]) == set(handoff.ZONE_ORDER)
    assert handoff.UNMAPPED_ZONE not in set(zone_rows["zone_id"])
    assert not zone_rows.duplicated(["zone_id", "stream", "availability_case"]).any()
    totals = summary_json["handoff_totals"]
    for stream, cases in (
        ("process", [name for name, _ in handoff.PROCESS_CASES]),
        ("harvest", [name for name, _ in handoff.HARVEST_CASES]),
    ):
        for case in cases:
            selected = zone_rows[
                (zone_rows["stream"] == stream)
                & (zone_rows["availability_case"] == case)
            ]
            assert len(selected) == 13
            assert math.fsum(selected["pj"]) == pytest.approx(
                float(totals[stream][case]), rel=1e-9, abs=1e-5
            )
            assert math.fsum(selected["share_of_national_case"]) == (
                pytest.approx(1.0, rel=1e-9)
                if float(totals[stream][case]) > 0.0
                else pytest.approx(0.0, abs=0.0)
            )
    assert set(zone_rows["zone_mapping_status"]) == {
        "allocated_multi_zone_province",
        "mapped_aggregate_zone",
        "mapped_single_zone",
    }
    assert (zone_rows["official_osecan_changed"] == "false").all()


def test_comparison_tiers_ratios_and_context(comparison_rows, summary_json):
    assert len(comparison_rows) == 7 * (5 + 2 + 13)
    assert set(comparison_rows["comparison_scope"]) == {
        "national",
        "BC",
        *handoff.ZONE_ORDER,
    }
    assert (comparison_rows["reference_is_policy_target"] == "false").all()
    assert (comparison_rows["model_estimate"] == "true").all()
    assert (comparison_rows["observed"] == "false").all()
    assert (comparison_rows["policy_grade"] == "false").all()
    assert (comparison_rows["official_osecan_changed"] == "false").all()

    positive = comparison_rows[comparison_rows["reference_status"] == "positive"]
    zero = comparison_rows[comparison_rows["reference_status"] == "zero_reference"]
    assert len(positive) + len(zero) == len(comparison_rows)
    for _, row in positive.iterrows():
        reference = float(row["reference_pj"])
        assert reference > 0.0
        ratio = float(row["ratio"])
        assert ratio == pytest.approx(
            float(row["case_pj"]) / reference, rel=1e-9, abs=1e-9
        )
        assert float(row["headroom_pj"]) == pytest.approx(
            reference - float(row["case_pj"]), rel=1e-9, abs=1e-6
        )
        assert float(row["headroom_ratio"]) == pytest.approx(
            1.0 - ratio, rel=1e-9, abs=1e-9
        )
    assert set(zero["comparison_scope"]) == {"N1"}
    assert (zero["reference_pj"] == 0.0).all()
    assert set(zero["ratio"].astype(str)) == {""}

    national = comparison_rows[comparison_rows["comparison_scope"] == "national"]
    for case, expected in {
        "process_availability_000": "T1",
        "process_availability_025": "T3",
        "process_availability_050": "above_T4",
        "process_availability_075": "above_T4",
        "harvest_recovery_035": "T1",
        "harvest_recovery_060": "T2",
        "harvest_recovery_075": "T3",
    }.items():
        selected = national[national["availability_case"] == case]
        assert set(selected["case_tier"]) == {expected}

    context = comparison_rows[
        comparison_rows["reference"] == "osecan_2050_national_supply_context"
    ]
    assert set(context["reference_pj"].round(5)) == {152.92277}
    bc_context = comparison_rows[
        comparison_rows["reference"] == "osecan_2050_bc_supply_context"
    ]
    assert set(bc_context["reference_pj"].round(5)) == {43.52377}
    bc_cap = comparison_rows[
        comparison_rows["reference"] == "bc_zonal_supply_upper_2050"
    ]
    assert set(bc_cap["reference_pj"].round(6)) == {43.523816}
    zone_context = comparison_rows[
        comparison_rows["reference"] == "osecan_2050_zone_dispatch_context"
    ]
    assert len(zone_context) == 91
    assert set(zone_context["comparison_scope"]) == set(handoff.ZONE_ORDER)
    assert summary_json["supply_curve"]["upper_pj"] == pytest.approx(
        213.538041, rel=1e-12
    )


def test_osecan_integrity_and_no_public_surface(built, summary_json):
    summary, target = built
    integrity = summary["osecan_integrity"]
    assert integrity["unchanged"] is True
    assert integrity["changed_files"] == []
    assert integrity["file_count_before"] == integrity["file_count_after"]
    assert integrity["tree_sha256_before"] == integrity["tree_sha256_after"]
    assert summary_json["osecan_integrity"]["unchanged"] is True
    assert (
        summary_json["osecan_integrity"]["tree_sha256_before"]
        == (summary_json["osecan_integrity"]["tree_sha256_after"])
    )
    for name in summary["outputs"]:
        assert not name.startswith("ui/")
        assert "/ui/" not in name
    manifest = json.loads((target / MANIFEST_JSON).read_text(encoding="utf-8"))
    assert manifest["labels"]["official_osecan_changed"] is False
    assert manifest["osecan_integrity"]["unchanged"] is True
    for name, evidence in manifest["outputs"].items():
        assert (target / name).is_file(), name
        assert evidence["bytes"] == (target / name).stat().st_size


def test_report_contains_methods_boundary_and_comparisons(built):
    _, target = built
    text = (target / REPORT_MD).read_text(encoding="utf-8")
    assert "read-only forestry-side screening handoff" in text
    assert "official_osecan_changed=false" in text
    assert "UNMAPPED" in text
    assert "proxy_share=true" in text
    assert "osecan_zonal_supply_dispatch" in text
    assert "osecan_zonal_dispatch_proxy" in text
    assert "213.54 PJ" in text
    assert "152.923 PJ" in text
    assert "43.524 PJ" in text
    assert "not policy targets" in text
    assert "No policy/AAC claim is made." in text


def test_no_unexpected_deletions_guard():
    handoff.assert_no_unexpected_deletions()
