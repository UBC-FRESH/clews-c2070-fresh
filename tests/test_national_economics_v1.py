"""Tests for the 13-jurisdiction national economics scaffold."""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path

import pytest

from workflows.national.harvest import national_economics_v1 as model

REPO_ROOT = Path(__file__).resolve().parents[1]


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict]:
    out_dir = tmp_path_factory.mktemp("national_economics_v1")
    summary = model.build(out_dir)
    return out_dir, summary


def test_all_thirteen_jurisdictions_and_scenarios(built):
    _, summary = built
    assert summary["coverage"]["province_count"] == 13
    assert summary["coverage"]["margin_rows"] == 39
    assert summary["coverage"]["missing"] == []
    assert summary["coverage"]["not_estimated_count"] == 0
    assert set(summary["coverage"]["provinces"]) == set(model.PROVINCES)


def test_price_proxies_cover_all_provinces_and_labels(built):
    out_dir, _ = built
    rows = _read_csv(out_dir / model.PRICE_CSV)
    assert len(rows) == 13
    assert {row["province"] for row in rows} == set(model.PROVINCES)
    for row in rows:
        low = float(row["price_low_cad_m3"])
        base = float(row["price_base_cad_m3"])
        high = float(row["price_high_cad_m3"])
        assert low <= base <= high
        assert row["price_tier"] in {
            "observed",
            "reported_average",
            "donor_proxy",
            "assumption",
        }
        assert row["evidence"] == "model_estimate"
        assert row["observed"] == "false"
        assert row["policy_grade"] == "false"
        assert row["net_value_status"] == "model_estimate"
        assert row["combined_national_net_value"] == "false"
        assert row["strict_open"] == "false"
    bc = next(row for row in rows if row["province"] == "BC")
    assert bc["price_tier"] == "reported_average"
    assert bc["donor_province"] == ""
    qc = next(row for row in rows if row["province"] == "QC")
    assert qc["price_tier"] == "donor_proxy"
    assert qc["donor_province"] == "BC"
    assert float(qc["qc_purchase_cost_reference_cad_m3"]) == pytest.approx(107.22)
    assert float(qc["qc_bmmb_crown_benchmark_cad_m3"]) == pytest.approx(35.7547)
    assert qc["qc_price_status"] == "reference_only_not_used"


def test_cost_proxies_cover_all_provinces_and_qc_crosscheck(built):
    out_dir, _ = built
    rows = _read_csv(out_dir / model.COST_CSV)
    assert len(rows) == 13
    for row in rows:
        low = float(row["logging_haul_low_cad_m3"])
        base = float(row["logging_haul_base_cad_m3"])
        high = float(row["logging_haul_high_cad_m3"])
        assert low <= base <= high
        total_low = float(row["total_cost_low_cad_m3"])
        total_base = float(row["total_cost_base_cad_m3"])
        total_high = float(row["total_cost_high_cad_m3"])
        assert total_low <= total_base <= total_high
        assert row["evidence"] == "model_estimate"
        assert row["observed"] == "false"
        assert row["policy_grade"] == "false"
        assert row["net_value_status"] == "model_estimate"
        assert row["combined_national_net_value"] == "false"
        assert row["strict_open"] == "false"
        assert row["cost_currency"] == model.COST_CURRENCY
    qc = next(row for row in rows if row["province"] == "QC")
    assert float(qc["qc_crosscheck_ratio"]) == pytest.approx(1.001557, abs=0.01)
    assert qc["qc_crosscheck_status"] == "within_2pct"
    assert qc["road_tier"] == "reported_average"
    bc = next(row for row in rows if row["province"] == "BC")
    assert bc["logging_haul_tier"] == "proxy_model"
    assert bc["silviculture_tier"] == "reported_average"


def test_margin_rows_are_ordered_and_labelled(built):
    out_dir, _ = built
    rows = _read_csv(out_dir / model.MARGIN_CSV)
    assert len(rows) == 39
    by_province: dict[str, dict[str, dict[str, str]]] = {}
    for row in rows:
        by_province.setdefault(row["province"], {})[row["scenario_id"]] = row
        assert row["evidence"] == "model_estimate"
        assert row["observed"] == "false"
        assert row["policy_grade"] == "false"
        assert row["net_value_status"] == "model_estimate"
        assert row["combined_national_net_value"] == "false"
        assert row["price_tier"] in {
            "observed",
            "reported_average",
            "donor_proxy",
            "assumption",
        }
    assert set(by_province) == set(model.PROVINCES)
    for province, scenarios in by_province.items():
        assert set(scenarios) == {"low", "base", "high"}
        low = float(scenarios["low"]["margin_cad_m3"])
        base = float(scenarios["base"]["margin_cad_m3"])
        high = float(scenarios["high"]["margin_cad_m3"])
        assert low <= base <= high, province
        assert math.isfinite(low) and math.isfinite(base) and math.isfinite(high)
        for scenario in scenarios.values():
            assert float(scenario["margin_cad_m3"]) == pytest.approx(
                float(scenario["price_cad_m3"])
                - float(scenario["logging_haul_cad_m3"])
                - float(scenario["road_cad_m3"])
                - float(scenario["silviculture_cad_m3"]),
                abs=0.002,
            )


def test_no_not_estimated_anywhere_in_coverage_outputs(built):
    out_dir, _ = built
    for filename in (
        model.PRICE_CSV,
        model.COST_CSV,
        model.MARGIN_CSV,
        model.SUMMARY_CSV,
        model.ASSUMPTIONS_CSV,
        model.SOURCES_CSV,
    ):
        for row in _read_csv(out_dir / filename):
            assert "not_estimated" not in " ".join(row.values()).lower()


def test_summary_has_all_provinces_and_national_diagnostic(built):
    out_dir, _ = built
    rows = _read_csv(out_dir / model.SUMMARY_CSV)
    assert len(rows) == 14
    provinces = {row["province"] for row in rows if row["row_type"] == "province"}
    assert provinces == set(model.PROVINCES)
    national = next(row for row in rows if row["row_type"] == "national")
    assert national["national_diagnostic_label"] == "model_estimate_not_policy"
    assert national["combined_national_net_value"] == "false"
    assert float(national["allocation_volume_m3"]) == pytest.approx(
        118_977_339.0, abs=1.0
    )
    assert (
        float(national["margin_low_cad_m3"])
        <= float(national["margin_base_cad_m3"])
        <= float(national["margin_high_cad_m3"])
    )
    assert float(national["margin_base_cad_m3"]) == pytest.approx(33.1365, abs=0.5)
    nu = next(row for row in rows if row["province"] == "NU")
    assert float(nu["allocation_volume_m3"]) == 0.0
    assert nu["notes"].startswith("Zero allocation volume")


def test_negative_margins_are_documented(built):
    out_dir, summary = built
    rows = _read_csv(out_dir / model.MARGIN_CSV)
    negatives = [row for row in rows if float(row["margin_cad_m3"]) < 0.0]
    assert summary["margins"]["negative_documented"] is True
    assert summary["margins"]["negative_count"] == len(negatives)
    assert all(row["margin_status"] == "negative_documented" for row in negatives)
    assert len(negatives) == 13
    assert all(row["scenario_id"] == "low" for row in negatives)
    for row in negatives:
        assert "conservative envelope" in row["notes"].lower()


def test_qc_evidence_is_reference_only_and_crosschecked(built):
    _, summary = built
    qc = summary["qc_evidence"]
    assert qc["purchase_cost_used_as_price"] is False
    assert qc["bmmb_used_as_price"] is False
    assert qc["price_status"] == "no_current_delivered_log_price"
    assert qc["direct_cost_total_cad_m3"] == pytest.approx(72.76)
    assert qc["model_crosscheck_status"] == "within_2pct"
    assert qc["model_crosscheck_ratio"] == pytest.approx(1.001557, abs=0.01)


def test_sources_and_licensing_do_not_claim_strict_open(built):
    out_dir, summary = built
    sources = _read_csv(out_dir / model.SOURCES_CSV)
    assert len(sources) == 10
    assert summary["licensing"]["strict_open_licence"] is False
    assert summary["validation"]["strict_open_claim"] is False
    for row in sources:
        assert row["strict_open"] in {"true", "false"}
        assert row["sha256"]
        assert row["path"]


def test_assumption_register_covers_donors_and_gaps(built):
    out_dir, _ = built
    rows = _read_csv(out_dir / model.ASSUMPTIONS_CSV)
    ids = {row["assumption_id"] for row in rows}
    for required in (
        "price_bc_blend",
        "price_non_bc_donor",
        "price_qc_gap",
        "cost_bc_distribution",
        "cost_wage_index",
        "cost_diesel_index",
        "cost_terrain_factor",
        "cost_road_factor",
        "road_allowance_qc_donor",
        "silviculture_allowance_bc_donor",
        "no_cad_per_km_conversion",
        "qc_cost_crosscheck",
        "national_weighting",
        "nu_zero_volume",
        "negative_margin_documentation",
        "strict_open",
    ):
        assert required in ids


def test_manifest_records_hashes_and_no_restricted_inputs(built):
    out_dir, _ = built
    manifest = json.loads((out_dir / model.MANIFEST_JSON).read_text(encoding="utf-8"))
    assert manifest["restricted_inputs_used"] == []
    assert manifest["observation_type"] == "model_estimate"
    assert manifest["labels"]["policy_grade"] is False
    assert manifest["labels"]["combined_national_net_value"] is False
    assert len(manifest["inputs"]) >= 10
    assert len(manifest["outputs"]) >= 8
    for item in manifest["inputs"]:
        assert len(item["sha256"]) == 64
        assert not any(
            marker in item["path"].lower()
            for marker in ("confidential", "restricted", "microdata")
        )


def test_report_exists_and_states_non_claims(built):
    out_dir, _ = built
    report = (out_dir / model.REPORT_MD).read_text(encoding="utf-8")
    assert "All **13 provinces and territories**" in report
    assert "No strict-open claim" in report
    assert "BC+QC-only total" in report
    assert "negative" in report.lower()
