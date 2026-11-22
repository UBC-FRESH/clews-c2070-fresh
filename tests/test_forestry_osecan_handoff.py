"""Regression tests for the read-only forestry residue OSeCAN handoff v1."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd
import pytest

from workflows.national.facilities import forestry_osecan_handoff as handoff

ARTIFACT = handoff.ARTIFACT
HANDOFF_CSV = "forestry_residue_handoff.csv"
ZONE_CSV = "zone_summary.csv"
COMPARISON_CSV = "supply_curve_comparison.csv"
SUMMARY_JSON = "summary.json"
MANIFEST_JSON = "manifest.json"
REPORT_MD = "FORESTRY_OSECAN_HANDOFF_V1.md"

NUMERIC_HANDOFF = [
    "availability_fraction",
    "odt",
    "pj",
    "lhv_gj_per_odt",
    "supply_curve_tier_upper_pj",
]
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
    "ratio",
    "headroom_pj",
    "headroom_ratio",
]


def _read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, keep_default_na=False, dtype=str)


def _numeric(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    for column in columns:
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    return frame


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> tuple[dict, Path]:
    target = tmp_path_factory.mktemp("forestry_osecan_handoff")
    summary = handoff.build(output_dir=target)
    return summary, target


@pytest.fixture(scope="module")
def handoff_rows(built) -> pd.DataFrame:
    _, target = built
    frame = _numeric(_read_csv(target / HANDOFF_CSV), NUMERIC_HANDOFF)
    return frame


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
    }


def test_handoff_schema_rows_and_labels(handoff_rows):
    required = {
        "province",
        "zone_id",
        "stream",
        "availability_case",
        "odt",
        "pj",
        "supbcw_technology_suggestion",
        "supbcw_mapping",
        "evidence_tier",
        "caveat",
        "model_estimate",
        "observed",
        "policy_grade",
        "official_osecan_changed",
    }
    assert required.issubset(set(handoff_rows.columns))
    assert len(handoff_rows) == 13 * (4 + 3)
    assert not handoff_rows.duplicated(
        ["province", "stream", "availability_case"]
    ).any()
    assert set(handoff_rows["stream"]) == {"process", "harvest"}
    assert set(handoff_rows["residue_class"]) == {"TOTAL", "ALL"}
    assert (handoff_rows["model_estimate"] == "true").all()
    assert (handoff_rows["observed"] == "false").all()
    assert (handoff_rows["policy_grade"] == "false").all()
    assert (handoff_rows["official_osecan_changed"] == "false").all()
    assert (handoff_rows["evidence_tier"].str.len() > 0).all()
    assert (handoff_rows["caveat"].str.len() > 0).all()
    assert (handoff_rows["supbcw_technology_suggestion"].str.len() > 0).all()
    assert (handoff_rows["supbcw_mapping"].str.len() > 0).all()


def test_pj_arithmetic(handoff_rows, zone_rows):
    for frame in (handoff_rows, zone_rows):
        expected = frame["odt"] * handoff.EXPECTED_LHV_GJ_PER_ODT / handoff.PJ_GJ
        assert all(
            abs(left - right) <= 1e-6 + 1e-8 * abs(right)
            for left, right in zip(frame["pj"], expected)
        )
    assert (handoff_rows["lhv_gj_per_odt"] == 18.5).all()


def test_handoff_reconciles_to_national_curve(handoff_rows, summary_json):
    national = _read_csv(
        handoff.NATIONAL_CURVE
    )
    national = _numeric(
        national,
        ["availability_fraction", "residue_odt", "residue_pj"],
    )
    selected = national[
        (
            (national["stream"] == "process")
            & (national["residue_class"] == "TOTAL")
        )
        | (
            (national["stream"] == "harvest")
            & (national["residue_class"] == "ALL")
        )
    ]
    for _, record in selected.iterrows():
        stream = record["stream"]
        case = record["availability_case"]
        rows = handoff_rows[
            (handoff_rows["stream"] == stream)
            & (handoff_rows["availability_case"] == case)
        ]
        assert len(rows) == 13
        assert math.fsum(rows["odt"]) == pytest.approx(
            float(record["residue_odt"]), rel=1e-9, abs=1e-3
        )
        assert math.fsum(rows["pj"]) == pytest.approx(
            float(record["residue_pj"]), rel=1e-9, abs=1e-5
        )
    assert summary_json["handoff_totals"]["process"][
        "process_availability_050"
    ] == pytest.approx(263.002783555, rel=1e-9)
    assert summary_json["handoff_totals"]["harvest"][
        "harvest_recovery_060"
    ] == pytest.approx(100.667108175, rel=1e-9)


def test_zone_summary_no_double_count(handoff_rows, zone_rows, summary_json):
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
            province = handoff_rows[
                (handoff_rows["stream"] == stream)
                & (handoff_rows["availability_case"] == case)
            ]
            assert not selected["zone_id"].duplicated().any()
            assert math.fsum(selected["pj"]) == pytest.approx(
                float(totals[stream][case]), rel=1e-9, abs=1e-5
            )
            assert math.fsum(selected["odt"]) == pytest.approx(
                math.fsum(province["odt"]), rel=1e-9, abs=1e-3
            )
            mapped = selected[selected["zone_id"] != handoff.UNMAPPED_ZONE]
            unmapped = selected[selected["zone_id"] == handoff.UNMAPPED_ZONE]
            assert len(unmapped) == 1
            assert float(unmapped.iloc[0]["pj"]) == pytest.approx(
                float(totals[stream][case])
                - math.fsum(mapped["pj"]),
                rel=1e-9,
                abs=1e-5,
            )


def test_zone_mapping_coverage(handoff_rows, zone_rows, summary_json):
    coverage = summary_json["zone_mapping"]
    assert coverage["mapped_province_count"] == 9
    assert coverage["unmapped_province_count"] == 4
    assert coverage["unmapped_provinces"] == ["BC", "AB", "ON", "QC"]
    assert coverage["mapped_provinces"] == [
        "SK",
        "MB",
        "NB",
        "NS",
        "PE",
        "NL",
        "YT",
        "NT",
        "NU",
    ]
    unmapped = handoff_rows[handoff_rows["zone_id"] == ""]
    assert set(unmapped["province"]) == {"BC", "AB", "ON", "QC"}
    assert (unmapped["zone_mapping_status"] == "unmapped_multi_zone_province").all()
    mapped = handoff_rows[handoff_rows["zone_id"] != ""]
    assert set(mapped["province"]) == {
        "SK",
        "MB",
        "NB",
        "NS",
        "PE",
        "NL",
        "YT",
        "NT",
        "NU",
    }
    for zone_id, members in {
        "T1": {"NB", "NS", "PE"},
        "N1": {"YT", "NT", "NU"},
        "S1": {"SK"},
        "M1": {"MB"},
        "L1": {"NL"},
    }.items():
        selected = handoff_rows[handoff_rows["zone_id"] == zone_id]
        assert set(selected["province"]) == members
    zone_ids = set(zone_rows["zone_id"])
    assert zone_ids == {"S1", "M1", "T1", "L1", "N1", handoff.UNMAPPED_ZONE}


def test_t1_and_n1_are_exact_aggregates(handoff_rows, zone_rows, summary_json):
    for stream, cases in (
        ("process", [name for name, _ in handoff.PROCESS_CASES]),
        ("harvest", [name for name, _ in handoff.HARVEST_CASES]),
    ):
        for case in cases:
            for zone_id, members in (
                ("T1", {"NB", "NS", "PE"}),
                ("N1", {"YT", "NT", "NU"}),
            ):
                zone_row = zone_rows[
                    (zone_rows["zone_id"] == zone_id)
                    & (zone_rows["stream"] == stream)
                    & (zone_rows["availability_case"] == case)
                ]
                assert len(zone_row) == 1
                expected = math.fsum(
                    handoff_rows[
                        (handoff_rows["province"].isin(members))
                        & (handoff_rows["stream"] == stream)
                        & (handoff_rows["availability_case"] == case)
                    ]["pj"]
                )
                assert float(zone_row.iloc[0]["pj"]) == pytest.approx(
                    expected, rel=1e-9, abs=1e-5
                )


def test_comparison_tiers_ratios_and_context(comparison_rows, summary_json):
    assert len(comparison_rows) == 7 * 7
    assert set(comparison_rows["comparison_scope"]) == {"national", "BC"}
    assert (comparison_rows["reference_is_policy_target"] == "false").all()
    assert (comparison_rows["model_estimate"] == "true").all()
    assert (comparison_rows["observed"] == "false").all()
    assert (comparison_rows["policy_grade"] == "false").all()
    assert (comparison_rows["official_osecan_changed"] == "false").all()

    for _, row in comparison_rows.iterrows():
        reference = float(row["reference_pj"])
        assert reference > 0.0
        assert float(row["ratio"]) == pytest.approx(
            float(row["case_pj"]) / reference, rel=1e-9, abs=1e-9
        )
        assert float(row["headroom_pj"]) == pytest.approx(
            reference - float(row["case_pj"]), rel=1e-9, abs=1e-6
        )
        assert float(row["headroom_ratio"]) == pytest.approx(
            1.0 - float(row["ratio"]), rel=1e-9, abs=1e-9
        )

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
    assert summary_json["supply_curve"]["upper_pj"] == pytest.approx(
        213.538041, rel=1e-12
    )


def test_osecan_integrity(built, summary_json, handoff_rows, zone_rows, comparison_rows):
    summary, target = built
    integrity = summary["osecan_integrity"]
    assert integrity["unchanged"] is True
    assert integrity["changed_files"] == []
    assert integrity["file_count_before"] == integrity["file_count_after"]
    assert integrity["tree_sha256_before"] == integrity["tree_sha256_after"]
    assert summary_json["osecan_integrity"]["unchanged"] is True
    assert summary_json["osecan_integrity"]["tree_sha256_before"] == (
        summary_json["osecan_integrity"]["tree_sha256_after"]
    )
    for frame in (handoff_rows, zone_rows, comparison_rows):
        assert (frame["official_osecan_changed"] == "false").all()
    manifest = json.loads((target / MANIFEST_JSON).read_text(encoding="utf-8"))
    assert manifest["labels"]["official_osecan_changed"] is False
    assert manifest["osecan_integrity"]["unchanged"] is True
    for name, evidence in manifest["outputs"].items():
        assert (target / name).is_file(), name
        assert evidence["bytes"] == (target / name).stat().st_size


def test_report_contains_boundary_and_comparisons(built):
    _, target = built
    text = (target / REPORT_MD).read_text(encoding="utf-8")
    assert "read-only forestry-side screening handoff" in text
    assert "official_osecan_changed=false" in text
    assert "UNMAPPED" in text
    assert "213.54 PJ" in text
    assert "152.923 PJ" in text
    assert "43.524 PJ" in text
    assert "not policy targets" in text
    assert "No policy/AAC claim is made." in text


def test_no_unexpected_deletions_guard():
    handoff.assert_no_unexpected_deletions()
