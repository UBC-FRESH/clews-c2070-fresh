"""Contract tests for national margin totals at the 119 Mm3 allocation."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from workflows.national.harvest import national_margin_totals as model

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/derived/national/harvest_basis/national_economics_v2_3"
COMMITTED = ROOT / "data/derived/national/harvest_basis/national_margin_totals"
ALLOCATION = ROOT / "data/derived/national/harvest_basis/provincial_allocation/119m.csv"


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict, list]:
    out_dir = tmp_path_factory.mktemp("national_margin_totals")
    payload = model.build(out_dir, check_deletions=False)
    rows = _read_csv(out_dir / model.PROVINCE_CSV)
    return out_dir, payload, rows


def test_thirteen_provinces_two_scenarios_and_labels(built):
    _, payload, rows = built
    assert payload["model_id"] == "national_margin_totals_v1"
    assert payload["label"] == "model_estimate_not_policy"
    assert payload["observed"] is False
    assert payload["policy_grade"] is False
    assert payload["combined_national_net_value"] is False
    assert payload["not_policy_result"] is True
    assert payload["ranking_performed"] is False
    assert payload["volume_basis"] == "119m_allocation"
    assert payload["province_count"] == 13
    assert len(payload["provinces"]) == 13
    assert len(rows) == 26
    assert {row["scenario"] for row in rows} == set(model.SCENARIOS)
    assert {row["province"] for row in rows} == set(model.PROVINCES)
    for row in rows:
        assert row["evidence_tier"] == "model_estimate"
        assert row["observed"] == "false"
        assert row["policy_grade"] == "false"


def test_province_totals_are_volume_times_margin(built):
    _, _, rows = built
    for row in rows:
        allocation = float(row["allocation_m3"])
        for case in model.CASES:
            margin = float(row[f"margin_{case}_cad_m3"])
            total = float(row[f"total_{case}_m_cad"])
            assert total == pytest.approx(allocation * margin / 1_000_000.0, abs=1e-5)
        assert (
            float(row["margin_low_cad_m3"])
            <= float(row["margin_base_cad_m3"])
            <= float(row["margin_high_cad_m3"])
        )
        assert (
            float(row["total_low_m_cad"])
            <= float(row["total_base_m_cad"])
            <= float(row["total_high_m_cad"])
        )


def test_national_totals_equal_the_province_sum(built):
    _, payload, rows = built
    for scenario in model.SCENARIOS:
        scenario_rows = [row for row in rows if row["scenario"] == scenario]
        summary = payload["scenarios"][scenario]
        for case in model.CASES:
            computed = sum(float(row[f"total_{case}_m_cad"]) for row in scenario_rows)
            assert summary[f"national_total_{case}_m_cad"] == pytest.approx(
                computed, abs=1e-5
            )
        assert summary["national_margin_base_cad_m3"] == pytest.approx(
            summary["national_total_base_m_cad"]
            * 1_000_000.0
            / payload["allocation_total_m3"],
            abs=1e-4,
        )
        share_sum = sum(
            float(row["share_of_national_total_base"]) for row in scenario_rows
        )
        assert share_sum == pytest.approx(1.0, abs=1e-6)


def test_national_totals_match_the_v23_119m_diagnostic(built):
    _, payload, _ = built
    cross_check = payload["national_diagnostic"]["v2_3_119m_cross_check"]
    assert cross_check["checked"] is True
    assert cross_check["max_abs_delta_cad_m3"] == pytest.approx(0.0)
    assert payload["national_diagnostic"]["label"] == ("model_estimate_not_policy")
    assert payload["national_diagnostic"]["not_policy_result"] is True
    assert payload["national_diagnostic"]["combined_national_net_value"] is False


def test_negative_flags_and_positive_negative_counts(built):
    _, payload, rows = built
    donor = payload["scenarios"]["donor_flat"]
    scaled = payload["scenarios"]["stumpage_scaled"]
    assert donor["positive_province_count"] == 12
    assert donor["negative_province_count"] == 0
    assert donor["zero_total_province_count"] == 1
    assert scaled["positive_province_count"] == 7
    assert scaled["negative_province_count"] == 5
    assert scaled["zero_total_province_count"] == 1
    assert scaled["negative_provinces_by_case"]["base"] == [
        "AB",
        "MB",
        "ON",
        "QC",
        "NB",
    ]
    for row in rows:
        base_negative = float(row["margin_base_cad_m3"]) < 0.0
        assert row["negative_flag"] == str(base_negative).lower()
        for case in model.CASES:
            margin_negative = float(row[f"margin_{case}_cad_m3"]) < 0.0
            total_negative = float(row[f"total_{case}_m_cad"]) < 0.0
            assert row[f"negative_{case}"] == str(margin_negative).lower()
            assert row[f"total_negative_{case}"] == (str(total_negative).lower())
    # Negative values are retained, never clipped.
    assert any(float(row["total_low_m_cad"]) < 0.0 for row in rows)
    assert any(float(row["total_base_m_cad"]) < 0.0 for row in rows)


def test_nunavut_zero_volume_is_not_observed(built):
    _, payload, rows = built
    nu = next(row for row in rows if row["province"] == "NU")
    assert float(nu["allocation_m3"]) == 0.0
    assert nu["zero_volume"] == "true"
    assert float(nu["total_base_m_cad"]) == 0.0
    assert float(nu["margin_base_cad_m3"]) != 0.0
    assert nu["observed"] == "false"
    assert nu["policy_grade"] == "false"
    assert "rank" not in nu
    payload_nu = next(row for row in payload["provinces"] if row["province"] == "NU")
    assert payload_nu["zero_volume"] is True
    assert payload_nu["observed"] is False


def test_manifest_hashes_and_no_ranking(built):
    out_dir, payload, _ = built
    manifest = json.loads((out_dir / model.MANIFEST_JSON).read_text(encoding="utf-8"))
    assert manifest["model_id"] == model.MODEL_ID
    assert manifest["observation_type"] == "model_estimate"
    assert manifest["observed"] is False
    assert manifest["policy_grade"] is False
    assert manifest["combined_national_net_value"] is False
    assert manifest["national_diagnostic_label"] == ("model_estimate_not_policy")
    assert manifest["ranking_performed"] is False
    assert manifest["validation"]["national_equals_province_sum"] is True
    assert manifest["validation"]["nu_zero_volume"] is True
    assert manifest["validation"]["statcan_unexpected_d_cells"] == 0
    assert {item["path"] for item in manifest["outputs"]} == {
        model.PROVINCE_CSV,
        model.TOTAL_JSON,
        model.REPORT_MD,
    }
    for item in manifest["outputs"]:
        path = out_dir / item["path"]
        assert item["sha256"] == model._sha256(path)
    assert payload["national_diagnostic"]["ranking_performed"] is False


def test_committed_artifact_is_not_stale(built):
    _, payload, rows = built
    committed_rows = _read_csv(COMMITTED / model.PROVINCE_CSV)
    assert committed_rows == rows
    committed_payload = json.loads(
        (COMMITTED / model.TOTAL_JSON).read_text(encoding="utf-8")
    )
    for key in (
        "model_id",
        "label",
        "observed",
        "policy_grade",
        "combined_national_net_value",
        "allocation_total_m3",
        "province_count",
        "scenarios",
        "provinces",
        "national_diagnostic",
    ):
        assert committed_payload[key] == payload[key]


def test_unexpected_d_fails_closed():
    rows = _read_csv(
        ROOT
        / "data/derived/national/harvest_basis/statcan_logging_cost_proxy"
        / "provincial_logging_cost_proxy.csv"
    )
    summary = json.loads((SOURCE / "summary.json").read_text(encoding="utf-8"))
    checked = model._validate_statcan_d(rows, summary)
    assert checked["unexpected_cells"] == []
    assert checked["observed_d_cell_count"] == 16
    mutated = [dict(row) for row in rows]
    mutated[0]["unexpected_quality_status"] = "D"
    with pytest.raises(model.NationalMarginTotalsError, match="unexpected StatCan"):
        model._validate_statcan_d(mutated, summary)
