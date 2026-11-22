"""Tests for national economics v2 stumpage-scaled price proxies."""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path

import pytest

from workflows.national.harvest import national_economics_v2 as model

REPO_ROOT = Path(__file__).resolve().parents[1]


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict]:
    out_dir = tmp_path_factory.mktemp("national_economics_v2")
    summary = model.build(out_dir)
    return out_dir, summary


def test_coverage_is_thirteen_jurisdictions_and_two_price_scenarios(built):
    _, summary = built
    assert summary["coverage"]["province_count"] == 13
    assert summary["coverage"]["missing"] == []
    assert summary["coverage"]["not_estimated_count"] == 0
    assert summary["coverage"]["price_scenarios"] == [
        "stumpage_scaled",
        "donor_flat",
    ]
    assert summary["coverage"]["margin_rows"] == 78
    assert summary["coverage"]["price_rows"] == 26
    assert summary["coverage"]["stumpage_extracted_count"] == 8
    assert summary["coverage"]["fallback_count"] == 5
    assert set(summary["coverage"]["provinces"]) == set(model.PROVINCES)


def test_stumpage_references_have_sources_hashes_and_semantics(built):
    out_dir, _ = built
    rows = _read_csv(out_dir / model.STUMPAGE_CSV)
    assert len(rows) == 13
    extracted = [row for row in rows if row["is_stumpage_or_dues_rate"] == "true"]
    fallback = [row for row in rows if row["is_stumpage_or_dues_rate"] == "false"]
    assert {row["province"] for row in extracted} == {
        "BC",
        "AB",
        "SK",
        "MB",
        "ON",
        "QC",
        "NB",
        "NS",
    }
    assert {row["province"] for row in fallback} == {"PE", "NL", "YT", "NT", "NU"}
    for row in rows:
        assert row["is_delivered_price"] == "false"
        assert row["is_observed_revenue"] == "false"
        assert row["evidence"] == "model_estimate"
        assert row["observed"] == "false"
        assert row["policy_grade"] == "false"
        assert row["combined_national_net_value"] == "false"
        if row["is_stumpage_or_dues_rate"] == "true":
            low = float(row["rate_low_cad_m3"])
            base = float(row["rate_base_cad_m3"])
            high = float(row["rate_high_cad_m3"])
            assert 0.0 < low <= base <= high
            assert len(row["source_sha256"]) == 64
            assert row["source_url"].startswith("http")
            assert row["source_publication_date"]
            assert row["fallback_donor"] == "false"
        else:
            assert row["rate_base_cad_m3"] == ""
            assert row["fallback_donor"] == "BC"


def test_published_ranges_are_not_exceeded(built):
    out_dir, _ = built
    rows = {row["province"]: row for row in _read_csv(out_dir / model.STUMPAGE_CSV)}
    published = {
        "AB": (2.66, 12.68),
        "SK": (2.60, 15.65),
        "MB": (4.58, 6.58),
        "ON": (6.06, 6.75),
        "QC": (12.88, 22.49),
        "NB": (24.03, 27.06),
        "NS": (29.87, 36.40),
    }
    for province, (low_bound, high_bound) in published.items():
        low = float(rows[province]["rate_low_cad_m3"])
        high = float(rows[province]["rate_high_cad_m3"])
        assert low == pytest.approx(low_bound)
        assert high == pytest.approx(high_bound)
        assert low_bound <= high_bound
    bc = rows["BC"]
    assert float(bc["rate_base_cad_m3"]) == pytest.approx(34.325)


def test_price_proxies_use_scaled_rates_and_flat_donor(built):
    out_dir, summary = built
    rows = _read_csv(out_dir / model.PRICE_CSV)
    assert len(rows) == 26
    ratio = summary["stumpage_ratio"]["base"]
    assert ratio == pytest.approx(3.3745, abs=1e-4)
    by_key = {(row["province"], row["price_scenario"]): row for row in rows}
    for province in model.PROVINCES:
        scaled = by_key[(province, "stumpage_scaled")]
        flat = by_key[(province, "donor_flat")]
        for row in (scaled, flat):
            low = float(row["price_low_cad_m3"])
            base = float(row["price_base_cad_m3"])
            high = float(row["price_high_cad_m3"])
            assert 0.0 < low <= base <= high
            assert row["evidence"] == "model_estimate"
            assert row["observed"] == "false"
            assert row["policy_grade"] == "false"
            assert row["combined_national_net_value"] == "false"
            assert row["strict_open"] == "false"
            assert row["stumpage_rate_unit"] == "CAD_nominal_per_m3"
            assert row["rate_basis"]
            if row["stumpage_source_key"]:
                assert row["source_publication_date"]
            else:
                assert row["source_publication_date"] == ""
            if "proxy" in row["price_basis"]:
                assert "delivered" not in row["price_basis"]
        if province == "BC":
            assert scaled["price_tier"] == "reported_average"
            assert flat["price_tier"] == "reported_average"
            assert scaled["source_publication_date"] == "2025"
            assert float(scaled["price_base_cad_m3"]) == pytest.approx(
                summary["bc_price_blend"]["base_cad_m3"], abs=1e-3
            )
        elif province in summary["stumpage_references"]["extracted"]:
            assert scaled["price_tier"] == "stumpage_scaled_proxy"
            assert scaled["price_basis"] == (
                "stumpage_scaled_price_proxy_not_observed"
            )
            expected = float(scaled["stumpage_rate_base_cad_m3"]) * ratio
            assert float(scaled["price_base_cad_m3"]) == pytest.approx(
                expected, abs=1e-3
            )
        else:
            assert scaled["price_tier"] == "donor_proxy"
            assert scaled["price_basis"] == "donor_flat_price_proxy_not_observed"
            assert scaled["proxy_method"] == "bc_flat_donor"
        assert flat["price_tier"] in {"reported_average", "donor_proxy"}
        if flat["price_tier"] == "donor_proxy":
            assert flat["price_basis"] == "donor_flat_price_proxy_not_observed"


def test_margins_are_unclipped_and_labelled(built):
    out_dir, summary = built
    rows = _read_csv(out_dir / model.MARGIN_CSV)
    assert len(rows) == 78
    negatives = [row for row in rows if float(row["margin_cad_m3"]) < 0.0]
    assert negatives
    assert summary["margins"]["negative_count"] == len(negatives)
    assert summary["margins"]["clipped"] is False
    for row in rows:
        assert row["negative_margin_clipped"] == "false"
        assert row["evidence"] == "model_estimate"
        assert row["observed"] == "false"
        assert row["policy_grade"] == "false"
        assert row["net_value_status"] == "model_estimate"
        assert row["combined_national_net_value"] == "false"
        margin = float(row["margin_cad_m3"])
        assert math.isfinite(margin)
        assert row["margin_status"] == (
            "negative_documented" if margin < 0.0 else "non_negative"
        )
        assert margin == pytest.approx(
            float(row["price_cad_m3"])
            - float(row["logging_haul_cad_m3"])
            - float(row["road_cad_m3"])
            - float(row["silviculture_cad_m3"]),
            abs=0.002,
        )
    assert all(row["margin_status"] == "negative_documented" for row in negatives)


def test_summary_has_national_diagnostic_on_119mt_allocation(built):
    out_dir, summary = built
    rows = _read_csv(out_dir / model.SUMMARY_CSV)
    assert len(rows) == 14
    provinces = {row["province"] for row in rows if row["row_type"] == "province"}
    assert provinces == set(model.PROVINCES)
    national = next(row for row in rows if row["row_type"] == "national")
    assert float(national["allocation_volume_m3"]) == pytest.approx(
        118_977_339.1, abs=1.0
    )
    assert national["national_diagnostic_label"] == "model_estimate_not_policy"
    assert national["combined_national_net_value"] == "false"
    assert (
        float(national["margin_low_cad_m3"])
        <= float(national["margin_base_cad_m3"])
        <= float(national["margin_high_cad_m3"])
    )
    assert float(national["donor_flat_margin_base_cad_m3"]) == pytest.approx(
        33.1365, abs=0.5
    )
    assert summary["national_diagnostic"][
        "stumpage_scaled_margin_base_cad_m3"
    ] == pytest.approx(-16.1220, abs=1e-3)
    assert summary["stumpage_ratio"]["base"] == pytest.approx(3.3745, abs=1e-4)
    assert summary["national_diagnostic"]["label"] == "model_estimate_not_policy"


def test_v1_comparison_covers_all_provinces_and_bc_is_unchanged(built):
    out_dir, _ = built
    rows = _read_csv(out_dir / model.COMPARISON_CSV)
    assert len(rows) == 13
    assert {row["province"] for row in rows} == set(model.PROVINCES)
    bc = next(row for row in rows if row["province"] == "BC")
    assert float(bc["margin_delta_cad_m3"]) == pytest.approx(0.0, abs=1e-3)
    for row in rows:
        assert row["evidence"] == "model_estimate"
        assert row["observed"] == "false"
        assert row["policy_grade"] == "false"
        assert row["combined_national_net_value"] == "false"


def test_no_not_estimated_and_no_strict_open_claim(built):
    out_dir, summary = built
    for filename in (
        model.STUMPAGE_CSV,
        model.PRICE_CSV,
        model.MARGIN_CSV,
        model.SUMMARY_CSV,
        model.COMPARISON_CSV,
        model.ASSUMPTIONS_CSV,
        model.SOURCES_CSV,
    ):
        for row in _read_csv(out_dir / filename):
            assert "not_estimated" not in " ".join(row.values()).lower()
    assert summary["licensing"]["strict_open_licence"] is False
    assert summary["validation"]["strict_open_claim"] is False
    assert summary["validation"]["observed_claim"] is False
    for row in _read_csv(out_dir / model.SOURCES_CSV):
        assert row["strict_open"] == "false"
        assert len(row["sha256"]) == 64


def test_manifest_hashes_outputs_and_rejects_restricted_inputs(built):
    out_dir, _ = built
    manifest = json.loads((out_dir / model.MANIFEST_JSON).read_text(encoding="utf-8"))
    assert manifest["restricted_inputs_used"] == []
    assert manifest["observation_type"] == "model_estimate"
    assert manifest["labels"]["policy_grade"] is False
    assert manifest["labels"]["combined_national_net_value"] is False
    assert len(manifest["inputs"]) >= 10
    assert len(manifest["outputs"]) == 9
    for item in manifest["outputs"]:
        path = out_dir / item["path"]
        assert path.is_file()
        assert len(item["sha256"]) == 64
    for item in manifest["inputs"]:
        assert not any(
            marker in item["path"].lower()
            for marker in ("confidential", "restricted", "microdata")
        )


def test_report_states_non_claims(built):
    out_dir, _ = built
    report = (out_dir / model.REPORT_MD).read_text(encoding="utf-8")
    assert "not a delivered price" in report
    assert "not observed revenue" in report
    assert "No strict-open licence claim" in report
    assert "model_estimate_not_policy" in report
    assert "unclipped" in report.lower()
    assert "preferred" not in report.lower()
