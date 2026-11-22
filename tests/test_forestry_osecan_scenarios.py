"""Regression tests for the multi-scenario forestry residue OSeCAN handoff."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd
import pytest

from workflows.national.facilities import forestry_osecan_handoff as handoff
from workflows.national.facilities import forestry_osecan_scenarios as scenario

ARTIFACT = scenario.ARTIFACT
RESIDUE_CSV = "forestry_residue_scenarios.csv"
ZONE_CSV = "scenario_zone_summary.csv"
CANDIDATE_CSV = "candidate_activity_limits.csv"
COMPARISON_CSV = "supply_curve_comparison.csv"
SUMMARY_JSON = "summary.json"
MANIFEST_JSON = "manifest.json"
REPORT_MD = "FORESTRY_OSECAN_SCENARIOS_V1.md"

NUMERIC_RESIDUE = [
    "harvest_case_m3",
    "availability_fraction",
    "zone_weight",
    "odt",
    "pj",
    "lhv_gj_per_odt",
]
NUMERIC_ZONE = [
    "availability_fraction",
    "odt",
    "pj",
    "national_scenario_case_odt",
    "national_scenario_case_pj",
    "share_of_national_scenario_case",
    "supply_curve_tier_upper_pj",
]
NUMERIC_CANDIDATE = [
    "availability_fraction",
    "candidate_activity_limit_odt",
    "candidate_activity_limit_pj",
    "official_limit_pj",
    "candidate_ratio_to_official_limit",
    "candidate_headroom_pj",
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
    target = tmp_path_factory.mktemp("forestry_osecan_scenarios")
    summary = scenario.build(output_dir=target)
    return summary, target


@pytest.fixture(scope="module")
def residue_rows(built) -> pd.DataFrame:
    _, target = built
    return _numeric(_read_csv(target / RESIDUE_CSV), NUMERIC_RESIDUE)


@pytest.fixture(scope="module")
def zone_rows(built) -> pd.DataFrame:
    _, target = built
    return _numeric(_read_csv(target / ZONE_CSV), NUMERIC_ZONE)


@pytest.fixture(scope="module")
def candidate_rows(built) -> pd.DataFrame:
    _, target = built
    frame = _read_csv(target / CANDIDATE_CSV)
    for column in NUMERIC_CANDIDATE:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame


@pytest.fixture(scope="module")
def comparison_rows(built) -> pd.DataFrame:
    _, target = built
    frame = _read_csv(target / COMPARISON_CSV)
    for column in NUMERIC_COMPARISON:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame


@pytest.fixture(scope="module")
def summary_json(built) -> dict:
    _, target = built
    return json.loads((target / SUMMARY_JSON).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def scenario_summary() -> pd.DataFrame:
    return _read_csv(scenario.SCENARIO_SUMMARY)


def _expected_case_total(
    scenario_name: str, stream: str, case: str
) -> tuple[float, float]:
    cases = dict(
        scenario.PROCESS_CASES if stream == "process" else scenario.HARVEST_CASES
    )
    fraction = cases[case]
    definitions = {
        name: definition for name, definition in _scenario_definitions().items()
    }
    definition = definitions[scenario_name]
    if stream == "process":
        return definition["odt"] * fraction, definition["pj"] * fraction
    return definition["harvest_odt"] * fraction, definition["harvest_pj"] * fraction


def _scenario_definitions() -> dict[str, dict[str, float]]:
    frame = _read_csv(scenario.SCENARIO_SUMMARY)
    definitions: dict[str, dict[str, float]] = {}
    for _, row in frame.iterrows():
        definitions[str(row["scenario"])] = {
            "odt": float(row["process_residue_odt"]),
            "pj": float(row["process_residue_pj"]),
            "harvest_odt": float(row["harvest_residue_gross_odt"]),
            "harvest_pj": float(row["harvest_residue_gross_pj"]),
            "harvest_m3": float(row["harvest_case_m3"]),
        }
    return definitions


def test_build_writes_all_deliverables(built):
    summary, target = built
    assert summary["artifact"] == ARTIFACT
    assert summary["status"] == "pass"
    assert summary["validation"]["passed"] is True
    assert summary["validation"]["failed_check_count"] == 0
    for name in (
        RESIDUE_CSV,
        ZONE_CSV,
        CANDIDATE_CSV,
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
        "candidate_activity_limits_applied": False,
    }


def test_zone_mapping_covers_all_13_no_unmapped(residue_rows, zone_rows, summary_json):
    assert set(residue_rows["zone_id"]) == set(scenario.ZONE_ORDER)
    assert scenario.UNMAPPED_ZONE not in set(residue_rows["zone_id"])
    assert set(residue_rows["province"]) == set(scenario.PROVINCES)
    assert not residue_rows.duplicated(
        ["scenario", "stream", "availability_case", "province", "zone_id"]
    ).any()

    mapping = summary_json["zone_mapping"]
    assert mapping["zone_count"] == 13
    assert mapping["province_count"] == 13
    assert mapping["province_zone_allocation_rows"] == 17
    assert mapping["unmapped_province_count"] == 0
    assert mapping["unmapped_zone_present"] is False
    assert len(mapping["zones"]) == 13

    for province, group in residue_rows.groupby("province"):
        assert math.fsum(group["zone_weight"].iloc[:1]) >= 0.0
        weights = group.drop_duplicates("zone_id")
        assert math.fsum(weights["zone_weight"]) == pytest.approx(1.0, abs=1e-12)
        expected_proxy = province in scenario.MULTI_ZONE_PROVINCES
        assert set(weights["proxy_share"]) == {"true" if expected_proxy else "false"}
        assert set(weights["allocation_status"]) == {
            "proxy_share" if expected_proxy else "single_zone"
        }

    for scenario_name, stream, case in zone_rows.groupby(
        ["scenario", "stream", "availability_case"]
    ).groups:
        selected = zone_rows[
            (zone_rows["scenario"] == scenario_name)
            & (zone_rows["stream"] == stream)
            & (zone_rows["availability_case"] == case)
        ]
        assert set(selected["zone_id"]) == set(scenario.ZONE_ORDER)

    for zone_id, members in {
        "T1": {"NB", "NS", "PE"},
        "N1": {"YT", "NT", "NU"},
    }.items():
        selected = residue_rows[residue_rows["zone_id"] == zone_id]
        assert set(selected["province"]) == members
        assert set(selected["zone_mapping_status"]) == {"mapped_aggregate_zone"}


def test_zone_weights_match_published_handoff_v2(residue_rows):
    published = _read_csv(scenario.HANDOFF_V2_WEIGHTS)
    published["weight"] = pd.to_numeric(published["weight"], errors="raise")
    observed = (
        residue_rows[["province", "zone_id", "zone_weight"]]
        .drop_duplicates(["province", "zone_id"])
        .set_index(["province", "zone_id"])["zone_weight"]
    )
    expected = published.set_index(["province", "zone_id"])["weight"]
    assert set(observed.index) == set(expected.index)
    for key, value in expected.items():
        assert float(observed.loc[key]) == pytest.approx(float(value), abs=1e-12)


def test_conservation_of_scenario_and_zone_totals(
    residue_rows, zone_rows, summary_json
):
    for scenario_name in scenario.SCENARIO_ORDER:
        for stream, cases in (
            ("process", scenario.PROCESS_CASES),
            ("harvest", scenario.HARVEST_CASES),
        ):
            for case, fraction in cases:
                expected_odt, expected_pj = _expected_case_total(
                    scenario_name, stream, case
                )
                selected = residue_rows[
                    (residue_rows["scenario"] == scenario_name)
                    & (residue_rows["stream"] == stream)
                    & (residue_rows["availability_case"] == case)
                ]
                assert len(selected) == 17
                assert math.fsum(selected["odt"]) == pytest.approx(
                    expected_odt, rel=1e-9, abs=1e-3
                )
                assert math.fsum(selected["pj"]) == pytest.approx(
                    expected_pj, rel=1e-9, abs=1e-5
                )
                zone_selected = zone_rows[
                    (zone_rows["scenario"] == scenario_name)
                    & (zone_rows["stream"] == stream)
                    & (zone_rows["availability_case"] == case)
                ]
                assert len(zone_selected) == 13
                assert math.fsum(zone_selected["pj"]) == pytest.approx(
                    expected_pj, rel=1e-9, abs=1e-5
                )
                summary_entry = summary_json["national_case_totals"][scenario_name][
                    stream
                ][case]
                assert summary_entry["pj"] == pytest.approx(expected_pj, abs=1e-6)
                assert summary_entry["odt"] == pytest.approx(expected_odt, abs=1e-3)
                assert float(
                    zone_selected["share_of_national_scenario_case"].sum()
                ) == (pytest.approx(1.0, abs=1e-12) if expected_pj > 0.0 else 0.0)


def test_pj_arithmetic_and_candidate_conservation(
    residue_rows, zone_rows, candidate_rows
):
    expected_pj = (
        residue_rows["odt"] * scenario.EXPECTED_LHV_GJ_PER_ODT / scenario.PJ_GJ
    )
    assert all(
        abs(left - right) <= 1e-6 + 1e-9 * abs(right)
        for left, right in zip(residue_rows["pj"], expected_pj)
    )
    assert (residue_rows["lhv_gj_per_odt"] == 18.5).all()

    assert len(candidate_rows) == 273
    assert not candidate_rows.duplicated(
        ["scenario", "stream", "availability_case", "zone_id"]
    ).any()
    assert set(candidate_rows["candidate_status"]) == {
        "read_only_candidate_not_applied"
    }
    for scenario_name in scenario.SCENARIO_ORDER:
        for stream, cases in (
            ("process", scenario.PROCESS_CASES),
            ("harvest", scenario.HARVEST_CASES),
        ):
            for case, _ in cases:
                selected = candidate_rows[
                    (candidate_rows["scenario"] == scenario_name)
                    & (candidate_rows["stream"] == stream)
                    & (candidate_rows["availability_case"] == case)
                ]
                zone_selected = zone_rows[
                    (zone_rows["scenario"] == scenario_name)
                    & (zone_rows["stream"] == stream)
                    & (zone_rows["availability_case"] == case)
                ]
                assert len(selected) == 13
                assert math.fsum(
                    selected["candidate_activity_limit_pj"]
                ) == pytest.approx(math.fsum(zone_selected["pj"]), rel=1e-9, abs=1e-5)
                assert math.fsum(
                    selected["candidate_activity_limit_odt"]
                ) == pytest.approx(math.fsum(zone_selected["odt"]), rel=1e-9, abs=1e-3)

    for zone_id, technology in {"B1": "SUPBCWB1", "B2": "SUPBCWB2"}.items():
        selected = candidate_rows[candidate_rows["zone_id"] == zone_id]
        assert set(selected["official_limit_technology"]) == {technology}
        assert (selected["official_limit_pj"] > 0.0).all()
        assert set(selected["official_limit_status"]) == {
            "official_zone_specific_2050_limit"
        }
    other = candidate_rows[~candidate_rows["zone_id"].isin(["B1", "B2"])]
    assert set(other["official_limit_reference"]) == {"none"}
    assert set(other["official_limit_status"]) == {"no_official_zone_specific_limit"}
    assert (
        other["official_limit_pj"].isna() | (other["official_limit_pj"] == 0.0)
    ).all()


def test_119m_reproduces_national_residue_curve(residue_rows):
    curve = _read_csv(scenario.NATIONAL_CURVE)
    curve["residue_odt"] = pd.to_numeric(curve["residue_odt"], errors="raise")
    curve["residue_pj"] = pd.to_numeric(curve["residue_pj"], errors="raise")
    selected = curve[
        ((curve["stream"] == "process") & (curve["residue_class"] == "TOTAL"))
        | ((curve["stream"] == "harvest") & (curve["residue_class"] == "ALL"))
    ]
    assert len(selected) == 7
    for _, record in selected.iterrows():
        rows = residue_rows[
            (residue_rows["scenario"] == "119m")
            & (residue_rows["stream"] == record["stream"])
            & (residue_rows["availability_case"] == record["availability_case"])
        ]
        assert len(rows) == 17
        assert math.fsum(rows["pj"]) == pytest.approx(
            float(record["residue_pj"]), rel=1e-9, abs=1e-5
        )
        assert math.fsum(rows["odt"]) == pytest.approx(
            float(record["residue_odt"]), rel=1e-9, abs=1e-3
        )


def test_scenario_ordering(residue_rows, summary_json):
    definitions = _scenario_definitions()
    assert (
        definitions["100m"]["harvest_m3"]
        < definitions["119m"]["harvest_m3"]
        < definitions["150m"]["harvest_m3"]
    )
    assert (
        definitions["100m"]["pj"]
        < definitions["119m"]["pj"]
        < definitions["150m"]["pj"]
    )
    assert (
        definitions["100m"]["harvest_pj"]
        < definitions["119m"]["harvest_pj"]
        < definitions["150m"]["harvest_pj"]
    )
    assert summary_json["scenario_order"] == ["100m", "119m", "150m"]

    for province, group in residue_rows.groupby("province"):
        process = (
            group[
                (group["stream"] == "process")
                & (group["availability_case"] == "process_availability_075")
            ]
            .groupby("scenario")["pj"]
            .sum()
        )
        harvest = (
            group[
                (group["stream"] == "harvest")
                & (group["availability_case"] == "harvest_recovery_075")
            ]
            .groupby("scenario")["pj"]
            .sum()
        )
        assert process["100m"] <= process["119m"] + 1e-6
        assert process["119m"] <= process["150m"] + 1e-6
        assert harvest["100m"] <= harvest["119m"] + 1e-6
        assert harvest["119m"] <= harvest["150m"] + 1e-6


def test_supply_curve_comparison_arithmetic(comparison_rows, summary_json):
    assert len(comparison_rows) == 462
    positive = comparison_rows[comparison_rows["reference_status"] == "positive"]
    assert len(positive) > 0
    for _, row in positive.iterrows():
        assert row["ratio"] == pytest.approx(
            row["case_pj"] / row["reference_pj"], rel=0, abs=1e-12
        )
        assert row["headroom_pj"] == pytest.approx(
            row["reference_pj"] - row["case_pj"], rel=0, abs=1e-9
        )
        assert row["headroom_ratio"] == pytest.approx(
            1.0 - row["ratio"], rel=0, abs=1e-12
        )
    assert set(comparison_rows["reference_is_policy_target"]) == {"false"}

    national_t4 = comparison_rows[
        comparison_rows["reference"] == "national_supply_curve_t4_upper"
    ]
    assert set(national_t4["reference_pj"].round(6)) == {213.538041}
    national_context = comparison_rows[
        comparison_rows["reference"] == "osecan_2050_national_supply_context"
    ]
    assert set(national_context["reference_pj"].round(5)) == {152.92277}
    bc_zonal = comparison_rows[
        comparison_rows["reference"] == "bc_zonal_supply_upper_2050"
    ]
    assert set(bc_zonal["reference_pj"].round(6)) == {43.523816}
    assert set(comparison_rows["comparison_scope"]) == {
        "national",
        "BC",
        *scenario.ZONE_ORDER,
    }
    assert summary_json["supply_curve"]["upper_pj"] == pytest.approx(213.538041)
    assert summary_json["osecan_context"]["national_supply_pj"] == pytest.approx(
        152.92277
    )


def test_no_official_osecan_change(built):
    summary, target = built
    integrity = summary["osecan_integrity"]
    assert integrity["unchanged"] is True
    assert integrity["changed_files"] == []
    assert integrity["tree_sha256_before"] == integrity["tree_sha256_after"]
    _, current_tree = handoff._hash_tree(handoff.OSECAN_DIR)
    assert current_tree == integrity["tree_sha256_after"]

    manifest = json.loads((target / MANIFEST_JSON).read_text(encoding="utf-8"))
    assert manifest["osecan_integrity"]["unchanged"] is True
    assert manifest["labels"]["official_osecan_changed"] is False
    assert scenario.OSECAN_DIR not in target.resolve().parents
    assert scenario.OUTPUT_DIR != scenario.OSECAN_DIR
    for path in target.iterdir():
        assert scenario.OSECAN_DIR not in path.resolve().parents


def test_labels_everywhere(
    residue_rows, zone_rows, candidate_rows, comparison_rows, summary_json
):
    for frame in (residue_rows, zone_rows, candidate_rows, comparison_rows):
        assert (frame["model_estimate"] == "true").all()
        assert (frame["observed"] == "false").all()
        assert (frame["policy_grade"] == "false").all()
        assert (frame["official_osecan_changed"] == "false").all()
    assert summary_json["labels"]["model_estimate"] is True
    assert summary_json["labels"]["observed"] is False
    assert summary_json["labels"]["policy_grade"] is False
    assert summary_json["labels"]["official_osecan_changed"] is False
    assert summary_json["labels"]["not_policy"] is True
    assert summary_json["candidate_activity_limits"]["applied_to_osecan"] is False
    assert summary_json["candidate_activity_limits"]["status"] == (
        "read_only_candidate_not_applied"
    )
    assert summary_json["osecan_context"]["context_is_policy_target"] is False
