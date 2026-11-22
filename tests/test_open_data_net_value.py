"""Tests for the open-data BC modeled-margin layer."""

from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path

import pytest

from workflows.national.harvest import open_data_net_value as model

REPO_ROOT = Path(__file__).resolve().parents[1]


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict]:
    out_dir = tmp_path_factory.mktemp("open_data_net_value")
    manifest = model.build(out_dir)
    return out_dir, manifest


def test_primary_price_panels_use_one_vintage():
    panels = model.load_price_panels()
    assert set(panels) == {"Coast", "Interior"}
    for panel in panels.values():
        assert panel.report_basis == model.PRIMARY_PRICE_BASIS
        assert panel.period_end == model.PRIMARY_PRICE_PERIOD_END
        assert panel.low_cad_m3 <= panel.base_cad_m3 <= panel.high_cad_m3
        assert panel.outliers_excluded == 0
    assert panels["Coast"].base_cad_m3 == pytest.approx(122.59, abs=0.01)
    assert panels["Interior"].base_cad_m3 == pytest.approx(113.38, abs=0.01)


def test_sensitivity_price_panel_is_separate_vintage():
    primary = model.load_price_panels()
    sensitivity = model.load_price_panels(
        report_basis=model.SENSITIVITY_PRICE_BASIS,
        period_end=model.SENSITIVITY_PRICE_PERIOD_END,
    )
    for region, panel in sensitivity.items():
        assert panel.report_basis == model.SENSITIVITY_PRICE_BASIS
        assert panel.period_end == model.SENSITIVITY_PRICE_PERIOD_END
        assert panel.report_basis != primary[region].report_basis


def test_interior_aggregates_are_excluded():
    panel = model.load_price_panels()["Interior"]
    assert panel.aggregates_excluded == 5
    assert panel.cells_used == 10

    rows = _read_csv(model.LOG_MARKET_PRICES_CSV)
    selected = [
        row
        for row in rows
        if row["report_basis"] == model.PRIMARY_PRICE_BASIS
        and row["period_end"] == model.PRIMARY_PRICE_PERIOD_END
        and row["region"] == "interior"
        and row["price_plausibility"] == "ok"
    ]
    products = [row for row in selected if row["is_aggregate"] != "true"]
    assert len(products) == panel.cells_used
    weighted = sum(
        float(row["price_cad_m3"]) * float(row["volume_m3"]) for row in products
    ) / sum(float(row["volume_m3"]) for row in products)
    assert panel.base_cad_m3 == pytest.approx(weighted, abs=0.001)
    assert all(row["is_aggregate"] != "true" for row in products)


def test_zone_price_mapping_is_explicit():
    assert model.price_region_for_zone("South West Zone") == "Coast"
    assert model.price_region_for_zone("North West Zone") == "Interior"
    assert model.price_region_for_zone("South Central Zone") == "Interior"
    assert set(model.ZONE_PRICE_REGION) == set(model.EXPECTED_ZONES)


def test_build_writes_945_labelled_rows(built):
    out_dir, _ = built
    rows = _read_csv(out_dir / model.NET_VALUE_CSV)
    assert len(rows) == 945
    assert {row["zone"] for row in rows} == set(model.EXPECTED_ZONES)
    assert {row["system"] for row in rows} == set(model.EXPECTED_SYSTEMS)
    for row in rows:
        assert row["evidence"] == "model_estimate"
        assert row["observed"] == "false"
        assert row["policy_grade"] == "false"
        assert row["price_basis"] == "mill_delivered_reported_average"
        assert row["cost_basis"] == "open_proxy_model"
        assert row["net_value_status"] == "model_estimate"
        assert row["road_silviculture_included"] == "true"
        assert row["price_report_basis"] == model.PRIMARY_PRICE_BASIS
        assert row["price_period_end"] == model.PRIMARY_PRICE_PERIOD_END


def test_outputs_are_finite_ordered_and_nonnegative(built):
    out_dir, _ = built
    rows = _read_csv(out_dir / model.NET_VALUE_CSV)
    for row in rows:
        for prefix in ("price", "logging", "haul", "gross_margin", "net_model"):
            low = float(row[f"{prefix}_low_cad_m3"])
            base = float(row[f"{prefix}_base_cad_m3"])
            high = float(row[f"{prefix}_high_cad_m3"])
            assert math.isfinite(low) and math.isfinite(base) and math.isfinite(high)
            assert low <= base <= high
        for column in (
            "price_low_cad_m3",
            "price_base_cad_m3",
            "price_high_cad_m3",
            "logging_low_cad_m3",
            "logging_base_cad_m3",
            "logging_high_cad_m3",
            "haul_low_cad_m3",
            "haul_base_cad_m3",
            "haul_high_cad_m3",
            "road_silviculture_allowance_cad_m3",
        ):
            assert float(row[column]) >= 0.0
        assert float(row["net_model_base_cad_m3"]) == pytest.approx(
            float(row["gross_margin_base_cad_m3"])
            - float(row["road_silviculture_allowance_cad_m3"]),
            abs=0.011,
        )


def test_summary_groups_and_losses(built):
    out_dir, _ = built
    rows = _read_csv(out_dir / model.NET_VALUE_CSV)
    summary = _read_csv(out_dir / model.SUMMARY_CSV)
    assert len(summary) == 21
    assert sum(int(row["n_rows"]) for row in summary) == 945
    negative_rows = sum(
        1 for row in rows if float(row["net_model_base_cad_m3"]) < 0.0
    )
    assert sum(int(row["negative_net_base_count"]) for row in summary) == negative_rows
    for row in summary:
        assert float(row["net_model_base_min_cad_m3"]) <= float(
            row["net_model_base_median_cad_m3"]
        ) <= float(row["net_model_base_max_cad_m3"])
        assert row["interpretation"] == "model_estimate_not_observed_profit"


def test_silviculture_allowance_is_published_by_zone():
    allowances = model.load_silviculture_allowances()
    assert set(allowances) == set(model.EXPECTED_ZONES)
    assert allowances["North West Zone"].value_cad_m3 == pytest.approx(5.84)
    assert allowances["South West Zone"].value_cad_m3 == pytest.approx(11.23)
    assert all(
        allowance.source_series == model.SILVICULTURE_SERIES
        for allowance in allowances.values()
    )


def test_qc_reference_is_separate_and_has_point_81_margin(built):
    out_dir, _ = built
    rows = _read_csv(out_dir / model.QC_REFERENCE_CSV)
    assert len(rows) == 1
    row = rows[0]
    assert row["geography"] == "QC"
    assert float(row["qc_channel_margin_after_transfers_cad_m3"]) == pytest.approx(0.81)
    assert row["net_value_status"] == "separate_reference_not_net_value"
    assert row["evidence"] == "survey_benchmark"
    assert row["observed"] == "false"
    assert row["policy_grade"] == "false"


def test_sensitivity_cases_cover_required_axes(built):
    out_dir, _ = built
    rows = _read_csv(out_dir / model.SENSITIVITY_CSV)
    by_id = {row["case_id"]: row for row in rows}
    assert {
        "price_low",
        "price_high",
        "cost_low",
        "cost_high",
        "gross_margin_road_silviculture_excluded",
        "net_model_road_silviculture_included",
        "envelope_low",
        "envelope_high",
        "price_1month_base",
    } <= set(by_id)
    assert float(by_id["envelope_low"]["median_cad_m3"]) <= float(
        by_id["base"]["median_cad_m3"]
    ) <= float(by_id["envelope_high"]["median_cad_m3"])
    assert by_id["gross_margin_road_silviculture_excluded"][
        "case_group"
    ] == "road_silviculture"


def test_price_outlier_register_is_not_used(built):
    out_dir, _ = built
    rows = _read_csv(out_dir / model.PRICE_OUTLIER_CSV)
    assert rows
    assert all(row["used_in_model"] == "false" for row in rows)
    assert all(row["price_plausibility"] != "ok" for row in rows)


def test_summary_json_labels_and_no_combined_total(built):
    out_dir, manifest = built
    summary = json.loads((out_dir / model.SUMMARY_JSON).read_text(encoding="utf-8"))
    assert summary["labels"]["evidence"] == "model_estimate"
    assert summary["labels"]["observed"] is False
    assert summary["labels"]["policy_grade"] is False
    assert summary["labels"]["combined_bc_qc_national_total"] is False
    assert summary["scope"]["combined_national_net_value"] is False
    assert summary["scope"]["restricted_inputs_used"] == []
    assert summary["price_panel"]["vintages_pooled"] is False
    assert summary["price_panel"]["interior_aggregate_rows_excluded"] == 5
    assert summary["validation"]["negative_net_values_documented"] is True
    assert manifest["restricted_inputs_used"] == []


def test_manifest_output_hashes_match(built):
    out_dir, manifest = built
    for name, entry in manifest["outputs"].items():
        path = out_dir / name
        assert path.is_file(), name
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert digest == entry["sha256"], name


def test_manifest_inputs_exist():
    for path in (
        model.COST_ESTIMATES_CSV,
        model.COST_MANIFEST_JSON,
        model.LOG_MARKET_PRICES_CSV,
        model.LOG_MARKET_MANIFEST_JSON,
        model.APPRAISAL_CSV,
        model.APPRAISAL_MANIFEST_JSON,
        model.PROCUREMENT_CSV,
        model.PROCUREMENT_MANIFEST_JSON,
    ):
        assert path.is_file(), path
