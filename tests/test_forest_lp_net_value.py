"""Contract tests for the Forest LP net-value sensitivity."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from workflows.national.harvest import forest_lp_net_value as model

ROOT = Path(__file__).resolve().parents[1]
COMMITTED = ROOT / "data/derived/national/harvest_basis/forest_lp_net_value_sensitivity"


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict, list, list, list]:
    out_dir = tmp_path_factory.mktemp("forest_lp_net_value_sensitivity")
    payload = model.build(out_dir, check_deletions=False)
    province_rows = _read_csv(out_dir / model.ALLOCATION_NET_VALUE_CSV)
    summary_rows = _read_csv(out_dir / model.ALLOCATION_SUMMARY_CSV)
    national_rows = _read_csv(out_dir / model.NATIONAL_SUMMARY_CSV)
    return out_dir, payload, province_rows, summary_rows, national_rows


def test_allocation_rows_are_preserved(built) -> None:
    _, payload, province_rows, _, _ = built
    assert len(province_rows) == model.EXPECTED_ALLOCATION_ROWS == 117
    keys = {
        (row["cap_case"], row["allocation_case"], row["province"])
        for row in province_rows
    }
    assert len(keys) == model.EXPECTED_ALLOCATION_GRID
    assert {row["province"] for row in province_rows} == set(model.PROVINCES)
    assert {row["cap_case"] for row in province_rows} == set(model.CAP_CASES)
    assert {row["allocation_case"] for row in province_rows} == set(model.LEVELS)
    assert payload["coverage"]["allocation_rows"] == 117
    assert payload["coverage"]["margin_rows"] == 26
    assert payload["coverage"]["joined_province_scenario_rows"] == 234


def test_every_row_carries_model_estimate_labels(built) -> None:
    _, payload, province_rows, summary_rows, national_rows = built
    for row in province_rows:
        assert row["net_value_label"] == "model_estimate"
        assert row["evidence_tier"] == "model_estimate"
        assert row["net_value_status"] == "model_estimate_not_policy"
        assert row["lp_objective"] == "not_used"
        assert row["observed"] == "false"
        assert row["policy_grade"] == "false"
    for row in summary_rows:
        assert row["net_value_status"] == "model_estimate_not_policy"
        assert row["lp_objective"] == "not_used"
        assert row["observed"] == "false"
        assert row["policy_grade"] == "false"
    for row in national_rows:
        assert row["net_value_status"] == "model_estimate_not_policy"
        assert row["lp_objective"] == "not_used"
        assert row["observed"] == "false"
        assert row["policy_grade"] == "false"
    assert payload["labels"]["forest_lp_objective_changed"] is False
    assert payload["labels"]["v1_outputs_changed"] is False
    assert payload["labels"]["public_osecan_input"] is False
    assert payload["labels"]["restricted_data_used"] is False


def test_net_value_arithmetic(built) -> None:
    _, _, province_rows, _, _ = built
    checked = 0
    for row in province_rows:
        volume = row["allocated_harvest_m3"]
        for scenario in model.SCENARIOS:
            for case in model.CASES:
                margin = row[f"{scenario}_margin_{case}_cad_m3"]
                value = row[f"{scenario}_net_value_{case}_m_cad"]
                assert margin != ""
                if volume == "":
                    assert value == ""
                    continue
                assert value != ""
                expected = (
                    float(volume) * float(margin) / 1_000_000.0
                )
                assert float(value) == pytest.approx(expected, abs=1e-5)
                checked += 1
        if volume != "":
            assert (
                float(row[f"{scenario}_margin_low_cad_m3"])
                <= float(row[f"{scenario}_margin_base_cad_m3"])
                <= float(row[f"{scenario}_margin_high_cad_m3"])
            )
    assert checked == 91 * 2 * 3


def test_infeasible_rows_are_blank_and_feasible_rows_are_complete(built) -> None:
    _, _, province_rows, _, _ = built
    infeasible = [row for row in province_rows if row["case_feasible"] == "false"]
    feasible = [row for row in province_rows if row["case_feasible"] == "true"]
    assert len(infeasible) == 26
    assert len(feasible) == 91
    for row in infeasible:
        assert row["allocated_harvest_m3"] == ""
        assert row["binding"] == ""
        for scenario in model.SCENARIOS:
            for case in model.CASES:
                assert row[f"{scenario}_net_value_{case}_m_cad"] == ""
                assert row[f"{scenario}_negative_{case}"] == ""
    for row in feasible:
        assert row["allocated_harvest_m3"] != ""
        for scenario in model.SCENARIOS:
            for case in model.CASES:
                assert row[f"{scenario}_net_value_{case}_m_cad"] != ""
                assert row[f"{scenario}_negative_{case}"] in {"true", "false"}


def test_summary_totals_equal_province_sums(built) -> None:
    _, _, province_rows, summary_rows, _ = built
    assert len(summary_rows) == 18
    for summary in summary_rows:
        group_rows = [
            row
            for row in province_rows
            if row["cap_case"] == summary["cap_case"]
            and row["allocation_case"] == summary["allocation_case"]
        ]
        assert len(group_rows) == 13
        assert summary["province_rows"] == "13"
        scenario = summary["scenario"]
        if summary["case_feasible"] == "true":
            assert summary["allocation_m3"] != ""
            for case in model.CASES:
                computed = sum(
                    float(row[f"{scenario}_net_value_{case}_m_cad"])
                    for row in group_rows
                )
                assert float(summary[f"net_value_{case}_m_cad"]) == pytest.approx(
                    computed, abs=1e-5
                )
        else:
            assert summary["allocation_m3"] == ""
            for case in model.CASES:
                assert summary[f"net_value_{case}_m_cad"] == ""
                assert summary[f"negative_net_value_{case}_count"] == ""


def test_national_totals_equal_summary_totals(built) -> None:
    _, _, _, summary_rows, national_rows = built
    assert len(national_rows) == 54
    summary_lookup = {
        (row["cap_case"], row["allocation_case"], row["scenario"]): row
        for row in summary_rows
    }
    for national in national_rows:
        summary = summary_lookup[
            (
                national["cap_case"],
                national["allocation_case"],
                national["scenario"],
            )
        ]
        case = national["case"]
        if summary["case_feasible"] == "true":
            assert float(national["net_value_m_cad"]) == pytest.approx(
                float(summary[f"net_value_{case}_m_cad"]), abs=1e-6
            )
            assert national["negative_province_count"] == summary[
                f"negative_net_value_{case}_count"
            ]
        else:
            assert national["net_value_m_cad"] == ""
            assert national["negative_province_count"] == ""


def test_negative_values_are_retained_unclipped(built) -> None:
    _, payload, province_rows, _, _ = built
    negative_rows = [
        row
        for row in province_rows
        if row["case_feasible"] == "true"
        and float(row["stumpage_scaled_net_value_base_m_cad"]) < 0.0
    ]
    assert negative_rows
    for row in negative_rows:
        assert float(row["stumpage_scaled_margin_base_cad_m3"]) < 0.0
        assert float(row["stumpage_scaled_net_value_base_m_cad"]) == pytest.approx(
            float(row["allocated_harvest_m3"])
            * float(row["stumpage_scaled_margin_base_cad_m3"])
            / 1_000_000.0,
            abs=1e-5,
        )
    canonical = payload["canonical"]["scenarios"]
    assert canonical["donor_flat"]["net_value_base_m_cad"] == pytest.approx(
        5205.328748, abs=1e-6
    )
    assert canonical["stumpage_scaled"]["net_value_base_m_cad"] == pytest.approx(
        -655.319123, abs=1e-5
    )
    assert canonical["stumpage_scaled"]["negative_net_value_base_count"] == 5
    assert canonical["stumpage_scaled"]["negative_provinces_base"] == [
        "AB",
        "MB",
        "ON",
        "QC",
        "NB",
    ]


def test_tiers_come_from_v2_3(built) -> None:
    _, _, province_rows, _, _ = built
    bc = next(
        row
        for row in province_rows
        if row["cap_case"] == "documented_ceiling"
        and row["allocation_case"] == "119m"
        and row["province"] == "BC"
    )
    ab = next(
        row
        for row in province_rows
        if row["cap_case"] == "documented_ceiling"
        and row["allocation_case"] == "119m"
        and row["province"] == "AB"
    )
    assert bc["donor_flat_price_tier"] == "reported_average"
    assert bc["stumpage_scaled_price_tier"] == "reported_average"
    assert bc["cost_tier"] == "component_mix_partial_direct"
    assert ab["donor_flat_price_tier"] == "donor_proxy"
    assert ab["stumpage_scaled_price_tier"] == "stumpage_scaled_proxy"
    assert ab["cost_tier"] == "industry_aggregate_proxy"


def test_nunavut_zero_volume_is_not_observed(built) -> None:
    _, _, province_rows, _, _ = built
    nu = next(
        row
        for row in province_rows
        if row["cap_case"] == "documented_ceiling"
        and row["allocation_case"] == "119m"
        and row["province"] == "NU"
    )
    assert float(nu["allocated_harvest_m3"]) == 0.0
    assert float(nu["donor_flat_net_value_base_m_cad"]) == 0.0
    assert float(nu["donor_flat_margin_base_cad_m3"]) != 0.0
    assert nu["observed"] == "false"
    assert nu["policy_grade"] == "false"


def test_manifest_hashes_and_validation(built) -> None:
    out_dir, payload, _, _, _ = built
    manifest = json.loads((out_dir / model.MANIFEST_JSON).read_text(encoding="utf-8"))
    assert manifest["model_id"] == model.MODEL_ID
    assert manifest["observation_type"] == "model_estimate"
    assert manifest["status"] == "model_estimate_not_policy"
    assert manifest["objective_used"] is False
    assert manifest["forest_lp_objective_changed"] is False
    assert manifest["v1_outputs_changed"] is False
    assert manifest["public_osecan_input"] is False
    assert manifest["restricted_data_used"] is False
    assert manifest["validation"]["net_value_arithmetic"] is True
    assert manifest["validation"]["infeasible_blanks"] is True
    assert manifest["validation"]["v1_outputs_unchanged"] is True
    assert manifest["validation"]["unexpected_d_cells"] == 0
    assert {item["path"] for item in manifest["outputs"]} == {
        model.ALLOCATION_NET_VALUE_CSV,
        model.ALLOCATION_SUMMARY_CSV,
        model.NATIONAL_SUMMARY_CSV,
        model.SUMMARY_JSON,
        model.FOREST_LP_NET_VALUE_REPORT,
    }
    for item in manifest["outputs"]:
        path = out_dir / item["path"]
        assert item["sha256"] == model._sha256(path)
        assert item["bytes"] == path.stat().st_size
    assert payload["validation"]["forest_lp_objective_changed"] is False
    assert payload["validation"]["lp_objective"] == "not_used"
    assert payload["validation"]["unexpected_d_cells"] == 0


def test_committed_artifact_is_not_stale(built) -> None:
    _, payload, province_rows, summary_rows, national_rows = built
    assert _read_csv(COMMITTED / model.ALLOCATION_NET_VALUE_CSV) == province_rows
    assert _read_csv(COMMITTED / model.ALLOCATION_SUMMARY_CSV) == summary_rows
    assert _read_csv(COMMITTED / model.NATIONAL_SUMMARY_CSV) == national_rows
    committed_payload = json.loads(
        (COMMITTED / model.SUMMARY_JSON).read_text(encoding="utf-8")
    )
    for key in (
        "schema_version",
        "model_id",
        "artifact_version",
        "status",
        "labels",
        "coverage",
        "scenarios",
        "groups",
        "national_totals",
        "canonical",
        "statcan_d",
        "caveats",
        "non_claims",
    ):
        assert committed_payload[key] == payload[key]
    committed_manifest = json.loads(
        (COMMITTED / model.MANIFEST_JSON).read_text(encoding="utf-8")
    )
    for item in committed_manifest["outputs"]:
        path = COMMITTED / item["path"]
        assert item["sha256"] == model._sha256(path)
        assert item["bytes"] == path.stat().st_size
    assert committed_manifest["labels"] == payload["labels"]


def test_unexpected_d_fails_closed() -> None:
    rows = _read_csv(model.MARGIN_CSV)
    checked = model.assert_no_unexpected_d({model.MARGIN_CSV: rows})
    assert checked["unexpected_cells"] == []
    mutated = [dict(row) for row in rows]
    mutated[0]["unexpected_quality_status"] = "D"
    with pytest.raises(model.ForestLPNetValueError, match="unexpected D"):
        model.assert_no_unexpected_d({model.MARGIN_CSV: mutated})


def test_no_restricted_or_public_ui_paths() -> None:
    model._assert_allowed_input_paths(
        (
            model.ALLOCATION_CSV,
            model.MARGIN_CSV,
            model.V23_SUMMARY_JSON,
        )
    )
    with pytest.raises(model.ForestLPNetValueError, match="restricted"):
        model._assert_allowed_input_paths((ROOT / "data/raw/restricted.csv",))
    with pytest.raises(model.ForestLPNetValueError, match="outside"):
        model._assert_allowed_input_paths((ROOT / "planning/notes.md",))
    model._assert_output_path_allowed(ROOT / "tmp" / "net_value")
    with pytest.raises(model.ForestLPNetValueError, match="UI/publication"):
        model._assert_output_path_allowed(ROOT / "ui_publications" / "net_value")
