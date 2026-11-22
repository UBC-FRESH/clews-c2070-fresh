"""Tests for national economics v2.3 StatCan aggregate cost proxy."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from workflows.national.harvest import national_economics_v2_3 as model

REPO_ROOT = Path(__file__).resolve().parents[1]
V22_MARGIN_CSV = (
    REPO_ROOT
    / "data/derived/national/harvest_basis/national_economics_v2_2/"
    "national_margin_estimates.csv"
)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict]:
    out_dir = tmp_path_factory.mktemp("national_economics_v2_3")
    summary = model.build(out_dir, check_deletions=False)
    return out_dir, summary


def _cost_row(
    rows: list[dict[str, str]], province: str, component: str
) -> dict[str, str]:
    matches = [
        row
        for row in rows
        if row["province"] == province and row["component"] == component
    ]
    assert len(matches) == 1
    return matches[0]


def test_coverage_and_labels(built):
    out_dir, summary = built
    assert summary["coverage"]["province_count"] == 13
    assert summary["coverage"]["missing"] == []
    assert summary["coverage"]["not_estimated_count"] == 0
    assert summary["coverage"]["margin_rows"] == 78
    assert summary["coverage"]["summary_rows"] == 30
    assert summary["coverage"]["comparison_rows"] == 30
    assert summary["coverage"]["cost_input_rows"] == 18
    for filename in (
        model.COST_INPUTS_CSV,
        model.MARGIN_CSV,
        model.SUMMARY_CSV,
        model.COMPARISON_CSV,
        model.TIER_CSV,
    ):
        rows = _read_csv(out_dir / filename)
        assert rows, filename
        for row in rows:
            assert row["evidence"] == "model_estimate"
            assert row["observed"] == "false"
            assert row["policy_grade"] == "false"
            assert row["combined_national_net_value"] == "false"
            assert "not_estimated" not in " ".join(row.values()).lower()


def test_bc_qc_component_costs_are_retained(built):
    out_dir, _ = built
    rows = _read_csv(out_dir / model.COST_INPUTS_CSV)
    expected = {
        ("BC", "logging_haul"): ("29.5900", "57.7000", "144.0400"),
        ("BC", "road"): ("1.0000", "5.9300", "9.7800"),
        ("BC", "silviculture"): ("5.8400", "10.6100", "11.2300"),
        ("QC", "harvest"): ("28.2700", "31.4200", "34.4800"),
        ("QC", "haul"): ("19.3200", "24.7600", "36.8600"),
        ("QC", "road"): ("7.4700", "16.5800", "22.1300"),
        ("QC", "silviculture"): ("5.8400", "10.6100", "11.2300"),
    }
    for (province, component), values in expected.items():
        row = _cost_row(rows, province, component)
        assert (
            row["low_cad_m3"],
            row["base_cad_m3"],
            row["high_cad_m3"],
        ) == values
        assert row["cost_basis"] == "component_sum"
        assert row["includes_overhead_not_logging_haul_breakdown"] == "false"
        assert row["fallback_used"] == "false"


def test_ten_provinces_use_one_statcan_total_proxy(built):
    out_dir, summary = built
    rows = _read_csv(out_dir / model.COST_INPUTS_CSV)
    for province in model.AGGREGATE_PROVINCES:
        matches = [row for row in rows if row["province"] == province]
        assert len(matches) == 1
        row = matches[0]
        assert row["component"] == "industry_aggregate_proxy"
        assert row["tier"] == "industry_aggregate_proxy"
        assert row["cost_basis"] == "industry_aggregate_proxy"
        assert row["includes_overhead_not_logging_haul_breakdown"] == "true"
        assert row["fallback_used"] == "false"
        assert row["source_key"] == "statcan_16100114"
        assert row["harvest_denominator_basis"] == "observed_2024_m3"
        assert row["low_cad_m3"] == row["base_cad_m3"] == row["high_cad_m3"]
        assert row["statcan_119m_sensitivity_cad_m3"] != ""
    ab = _cost_row(rows, "AB", "industry_aggregate_proxy")
    on = _cost_row(rows, "ON", "industry_aggregate_proxy")
    assert float(ab["base_cad_m3"]) == pytest.approx(54.9865, abs=1e-4)
    assert float(on["base_cad_m3"]) == pytest.approx(83.7293, abs=1e-4)
    assert float(ab["statcan_119m_sensitivity_cad_m3"]) == pytest.approx(
        53.4708, abs=1e-4
    )
    assert summary["cost_mapping"][
        "separate_road_silviculture_haul_added"
    ] is False
    assert summary["cost_mapping"][
        "includes_overhead_not_logging_haul_breakdown"
    ] is True


def test_nu_uses_explicit_fallback_not_a_statcan_value(built):
    out_dir, _ = built
    rows = _read_csv(out_dir / model.COST_INPUTS_CSV)
    nu = _cost_row(rows, "NU", "total_cost_fallback")
    assert nu["tier"] == "donor_fallback_total"
    assert nu["cost_basis"] == "donor_fallback_total"
    assert nu["fallback_used"] == "true"
    assert nu["source_key"] == "national_economics_v2_2"
    assert nu["statcan_119m_sensitivity_cad_m3"] == ""
    assert "no statcan value is fabricated" in nu["notes"].lower()
    assert float(nu["base_cad_m3"]) == pytest.approx(86.9381, abs=1e-4)


def test_margins_are_recomputed_ordered_and_unclipped(built):
    out_dir, summary = built
    rows = _read_csv(out_dir / model.MARGIN_CSV)
    assert len(rows) == 78
    by_key = {
        (row["province"], row["price_scenario"], row["scenario_id"]): row
        for row in rows
    }
    for scenario in model.SCENARIOS:
        seen = {row["province"] for row in rows if row["price_scenario"] == scenario}
        assert seen == set(model.PROVINCES)
        for province in model.PROVINCES:
            low = by_key[(province, scenario, "low")]
            base = by_key[(province, scenario, "base")]
            high = by_key[(province, scenario, "high")]
            assert float(low["margin_cad_m3"]) <= float(
                base["margin_cad_m3"]
            ) <= float(high["margin_cad_m3"])
            for row in (low, base, high):
                assert row["negative_margin_clipped"] == "false"
                assert float(row["margin_cad_m3"]) == pytest.approx(
                    float(row["price_cad_m3"]) - float(row["total_cost_cad_m3"]),
                    abs=0.002,
                )
    ab = by_key[("AB", "donor_flat", "base")]
    on = by_key[("ON", "donor_flat", "base")]
    assert float(ab["industry_aggregate_proxy_cad_m3"]) == pytest.approx(
        54.9865, abs=1e-4
    )
    assert float(on["industry_aggregate_proxy_cad_m3"]) == pytest.approx(
        83.7293, abs=1e-4
    )
    assert float(ab["margin_cad_m3"]) == pytest.approx(60.8415, abs=1e-4)
    assert float(on["margin_cad_m3"]) == pytest.approx(32.0987, abs=1e-4)
    assert ab["logging_haul_cad_m3"] == ""
    assert ab["road_cad_m3"] == ""
    assert ab["silviculture_cad_m3"] == ""
    assert summary["margins"]["negative_row_count"] == 21
    assert summary["margins"]["negative_by_scenario"] == {
        "donor_flat": 5,
        "stumpage_scaled": 16,
    }
    assert summary["margins"]["negative_margin_clipped"] is False


def test_bc_qc_and_nu_have_zero_delta_and_ab_on_change(built):
    out_dir, _ = built
    rows = _read_csv(out_dir / model.COMPARISON_CSV)
    by_key = {
        (row["province"], row["price_scenario"], row["volume_basis"]): row
        for row in rows
    }
    before = {
        (row["province"], row["price_scenario"], row["scenario_id"]): row
        for row in _read_csv(V22_MARGIN_CSV)
    }
    for province in ("BC", "QC", "NU"):
        for scenario in model.SCENARIOS:
            row = by_key[(province, scenario, model.VOLUME_BASIS_OBSERVED)]
            assert float(row["margin_base_delta_cad_m3"]) == pytest.approx(
                0.0, abs=1e-6
            )
    for province in ("AB", "ON"):
        row = by_key[(province, "donor_flat", model.VOLUME_BASIS_OBSERVED)]
        expected = float(
            before[(province, "donor_flat", "base")]["margin_cad_m3"]
        )
        assert float(row["v2_2_margin_base_cad_m3"]) == pytest.approx(
            expected, abs=1e-4
        )
    ab = by_key[("AB", "donor_flat", model.VOLUME_BASIS_OBSERVED)]
    on = by_key[("ON", "donor_flat", model.VOLUME_BASIS_OBSERVED)]
    assert float(ab["margin_base_delta_cad_m3"]) == pytest.approx(
        25.8055, abs=1e-4
    )
    assert float(on["margin_base_delta_cad_m3"]) == pytest.approx(
        -3.1028, abs=1e-4
    )


def test_national_diagnostic_uses_observed_denominator_and_119m_sensitivity(built):
    _, summary = built
    diagnostic = summary["national_diagnostic"]
    assert diagnostic["primary_volume_basis"] == "observed_2024_m3"
    assert diagnostic["sensitivity_volume_basis"] == (
        "119m_allocation_sensitivity"
    )
    assert diagnostic["donor_flat"]["observed_2024_m3"][
        "margin_base_cad_m3"
    ] == pytest.approx(43.0187, abs=1e-3)
    assert diagnostic["donor_flat"]["119m_allocation_sensitivity"][
        "margin_base_cad_m3"
    ] == pytest.approx(43.7506, abs=1e-3)
    assert diagnostic["stumpage_scaled_sensitivity"]["observed_2024_m3"][
        "margin_base_cad_m3"
    ] == pytest.approx(-5.7444, abs=1e-3)
    assert diagnostic["combined_national_net_value"] is False
    assert diagnostic["label"] == "model_estimate_not_policy"


def test_cost_proxy_coverage_shows_ten_upgrades(built):
    out_dir, summary = built
    rows = _read_csv(out_dir / model.TIER_CSV)
    upgrades = [row for row in rows if row["row_type"] == "cost_proxy_upgrade"]
    assert len(upgrades) == 10
    assert {row["province"] for row in upgrades} == set(
        model.AGGREGATE_PROVINCES
    )
    assert all(
        row["tier_before"] == "donor_proxy"
        and row["tier_after"] == "industry_aggregate_proxy"
        for row in upgrades
    )
    before_observed = next(
        row
        for row in rows
        if row["row_type"] == "coverage_summary"
        and row["version"] == "v2_2"
        and row["volume_basis"] == model.VOLUME_BASIS_OBSERVED
    )
    after_observed = next(
        row
        for row in rows
        if row["row_type"] == "coverage_summary"
        and row["version"] == "v2_3"
        and row["volume_basis"] == model.VOLUME_BASIS_OBSERVED
    )
    assert before_observed["donor_proxy_cells"] == "11"
    assert before_observed["aggregate_proxy_cells"] == "0"
    assert after_observed["donor_proxy_cells"] == "0"
    assert after_observed["aggregate_proxy_cells"] == "10"
    assert after_observed["donor_fallback_cells"] == "1"
    assert float(before_observed["donor_proxy_volume_share"]) == pytest.approx(
        0.45522715, abs=5e-9
    )
    assert float(
        after_observed["aggregate_proxy_volume_share"]
    ) == pytest.approx(0.45522715, abs=5e-9)
    after_119m = next(
        row
        for row in rows
        if row["row_type"] == "coverage_summary"
        and row["version"] == "v2_3"
        and row["volume_basis"] == model.VOLUME_BASIS_119M
    )
    assert float(
        after_119m["aggregate_proxy_volume_share"]
    ) == pytest.approx(0.43978482, abs=5e-9)
    assert summary["cost_proxy_coverage"]["upgrade_count"] == 10
    assert summary["cost_proxy_coverage"]["full_direct_coverage_claimed"] is False


def test_unexpected_statcan_d_fails_closed():
    rows = {"AB": {"unexpected_quality_status": "D"}}
    with pytest.raises(model.NationalEconomicsV23Error, match="unexpected StatCan"):
        model._validate_statcan_d_statuses(rows)
    allowed = {
        "AB": {
            "industry_worst_quality_status": "D",
            "salaries_wages_status": "B",
        }
    }
    model._validate_statcan_d_statuses(allowed)


def test_manifest_hashes_outputs_and_labels(built):
    out_dir, _ = built
    manifest = json.loads(
        (out_dir / model.MANIFEST_JSON).read_text(encoding="utf-8")
    )
    assert manifest["model_id"] == "national_economics_v2_3"
    assert manifest["artifact_version"] == "2.3.0"
    assert manifest["base_scenario"] == "donor_flat"
    assert manifest["sensitivity_scenario"] == "stumpage_scaled"
    assert manifest["stumpage_scaled_observed"] is False
    assert manifest["combined_national_net_value"] is False
    assert manifest["restricted_inputs_used"] == []
    assert manifest["labels"]["observed"] is False
    assert manifest["labels"]["policy_grade"] is False
    assert len(manifest["outputs"]) == 7
    for item in manifest["outputs"]:
        path = out_dir / item["path"]
        assert path.is_file()
        assert len(item["sha256"]) == 64


def test_report_states_mapping_and_caveats(built):
    out_dir, _ = built
    report = (out_dir / model.REPORT_MD).read_text(encoding="utf-8")
    for phrase in (
        "Cost mapping",
        "ON and AB results",
        "Employer-establishment coverage",
        "2024 preliminary",
        "Materials double-count risk",
        "Not a logging/haul breakdown",
        "No separate haul add-on",
        "Cost-proxy coverage before and after",
        "not observed",
        "No combined national net value",
    ):
        assert phrase in report
    assert "54.9865" in report
    assert "83.7293" in report
