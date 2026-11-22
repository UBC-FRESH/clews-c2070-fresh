"""Contract tests for the corrected economics sensitivity in the static UI."""

from __future__ import annotations

import csv
import hashlib
import json
import statistics
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "ui/public/data/economics"
SOURCE = ROOT / "data/derived/national/harvest_basis/open_data_net_value"
V2D = ROOT / "data/derived/national/harvest_basis/forest_economics_v2d"


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _load_economics() -> dict:
    return json.loads((BUNDLE / "summary.json").read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_economics_headline_matches_corrected_detail_csv() -> None:
    econ = _load_economics()
    rows = _read_csv(SOURCE / "bc_open_net_value.csv")
    values = [float(row["net_model_base_cad_m3"]) for row in rows]
    headline = econ["headline"]

    assert econ["schema_version"] == "economics/1"
    assert econ["status"] == "model_estimate"
    assert econ["role"] == "reported_sensitivity"
    assert econ["labels"]["forest_lp_input"] is False
    assert econ["labels"]["public_osecan_input"] is False
    assert headline["rows"] == len(rows) == 945
    assert headline["zone_system_groups"] == 21
    assert headline["median_base_net_model_cad_m3"] == pytest.approx(
        statistics.median(values)
    )
    assert headline["min_base_net_model_cad_m3"] == pytest.approx(min(values))
    assert headline["max_base_net_model_cad_m3"] == pytest.approx(max(values))
    assert headline["negative_net_base_rows"] == sum(1 for value in values if value < 0) == 168
    assert headline["median_base_net_model_cad_m3"] == pytest.approx(48.84)
    assert headline["min_base_net_model_cad_m3"] == pytest.approx(-23.38)
    assert headline["max_base_net_model_cad_m3"] == pytest.approx(86.85)


def test_economics_zone_system_table_matches_summary_csv() -> None:
    econ = _load_economics()
    summary_rows = _read_csv(SOURCE / "bc_open_net_value_summary.csv")
    expected = {
        (row["zone"], row["system"]): row for row in summary_rows
    }
    actual = {
        (zone["zone"], group["system"]): group
        for zone in econ["zones"]
        for group in zone["systems"]
    }

    assert len(actual) == 21
    assert len(econ["zones"]) == 7
    assert all(len(zone["systems"]) == 3 for zone in econ["zones"])
    for key, group in actual.items():
        source = expected[key]
        assert group["low_cad_m3"] == pytest.approx(
            float(source["net_model_low_median_cad_m3"])
        )
        assert group["base_cad_m3"] == pytest.approx(
            float(source["net_model_base_median_cad_m3"])
        )
        assert group["high_cad_m3"] == pytest.approx(
            float(source["net_model_high_median_cad_m3"])
        )
        assert group["base_min_cad_m3"] == pytest.approx(
            float(source["net_model_base_min_cad_m3"])
        )
        assert group["base_max_cad_m3"] == pytest.approx(
            float(source["net_model_base_max_cad_m3"])
        )
        assert group["negative_rows"] == int(source["negative_net_base_count"])
        assert group["negative"] is (
            group["base_cad_m3"] < 0 or group["negative_rows"] > 0
        )

    assert actual[("South West Zone", "ground")]["base_cad_m3"] == pytest.approx(64.40)
    assert actual[("South West Zone", "cable")]["base_cad_m3"] == pytest.approx(50.48)
    assert actual[("South West Zone", "heli")]["base_cad_m3"] == pytest.approx(-1.78)
    assert actual[("North West Zone", "ground")]["base_cad_m3"] == pytest.approx(57.76)
    assert actual[("North West Zone", "cable")]["base_cad_m3"] == pytest.approx(45.77)
    assert actual[("North West Zone", "heli")]["base_cad_m3"] == pytest.approx(-6.49)


def test_economics_qc_and_evidence_tiers_stay_separated() -> None:
    econ = _load_economics()
    qc = econ["qc_reference"]
    assert qc["cost_spread_after_transfers_cad_m3"] == pytest.approx(0.81)
    assert qc["net_value_status"] == "separate_reference_not_net_value"
    assert qc["observed"] is False
    assert qc["policy_grade"] is False

    v2d_rows = _read_csv(V2D / "forest_economics_v2d_summary.csv")
    assert len(econ["evidence_rows"]) == len(v2d_rows) == 14
    assert econ["evidence_tiers"]
    for row in econ["evidence_rows"]:
        assert row["net_value_status"] == "not_estimated"
        assert row["policy_grade"] is False
    assert econ["labels"]["qc_net_value_status"] == "separate_reference_not_net_value"
    assert econ["labels"]["combined_bc_qc_national_total"] is False
    assert econ["labels"]["source_licensing"] == "public_reference_copyrighted"


def test_economics_manifest_hashes_generated_files_and_sources() -> None:
    manifest = json.loads((BUNDLE / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["validation"]["ok"] is True
    assert manifest["schema_version"] == "economics/1"

    generated = {item["path"]: item for item in manifest["files"]}
    assert set(generated) == {
        "economics/summary.json",
        "economics/detail.csv",
        "economics/model_inputs.zip",
    }
    for item in manifest["files"]:
        path = ROOT / "ui/public/data" / item["path"]
        assert path.stat().st_size == item["bytes"]
        assert _sha256(path) == item["sha256"]

    detail = generated["economics/detail.csv"]
    source_detail = SOURCE / "bc_open_net_value.csv"
    assert detail["rows"] == 945
    assert detail["sha256"] == _sha256(source_detail)
    assert (BUNDLE / "detail.csv").read_bytes() == source_detail.read_bytes()

    bundle = generated["economics/model_inputs.zip"]
    with zipfile.ZipFile(BUNDLE / "model_inputs.zip") as archive:
        names = set(archive.namelist())
    assert bundle["bytes"] == (BUNDLE / "model_inputs.zip").stat().st_size
    assert {
        "README.md",
        "RUN.md",
        "input_manifest.json",
        "bc_cost_inputs_used.csv",
        "bc_price_panel_used.csv",
        "sources.csv",
    } <= names

    source_hashes = {item["path"]: item["sha256"] for item in manifest["sources"]}
    for relative in (
        "data/derived/national/harvest_basis/open_data_net_value/bc_open_net_value_summary.csv",
        "data/derived/national/harvest_basis/open_data_net_value/bc_open_net_value.csv",
        "data/derived/national/harvest_basis/open_data_net_value/qc_open_reference.csv",
        "data/derived/national/harvest_basis/forest_economics_v2d/forest_economics_v2d_summary.csv",
    ):
        assert source_hashes[relative] == _sha256(ROOT / relative)


def test_economics_bundle_contains_no_stale_headline_numbers() -> None:
    econ = _load_economics()
    displayed = {
        "headline": econ["headline"],
        "zone_system_medians": [
            {
                "zone": zone["zone"],
                "system": group["system"],
                "low": group["low_cad_m3"],
                "base": group["base_cad_m3"],
                "high": group["high_cad_m3"],
            }
            for zone in econ["zones"]
            for group in zone["systems"]
        ],
    }
    text = json.dumps(displayed)
    assert "45.81" not in text
    assert "-32.28" not in text
    assert "182" not in text

    summary_text = (BUNDLE / "summary.json").read_text(encoding="utf-8")
    assert "45.81" not in summary_text
    assert "-32.28" not in summary_text
    assert "182" not in summary_text


def test_ui_exposes_read_only_economics_panel_without_run_controls() -> None:
    html = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
    app_js = (ROOT / "ui" / "app.js").read_text(encoding="utf-8")

    assert 'data-tab="economics"' in html
    assert 'id="panel-economics"' in html
    assert 'id="economics-chart"' in html
    assert 'id="economics-table"' in html
    assert 'id="economics-downloads"' in html
    assert "loadJSON('economics/summary.json')" in app_js
    assert "function validateEconomics" in app_js
    assert "function renderEconomics" in app_js
    assert "badge--econ-public" in app_js
    assert "model_estimate" in app_js
    assert "policy_grade=" in app_js
    assert "not net value" in html.lower() or "not net value" in app_js.lower()
    assert "no BC+QC total" in app_js

    panel = html.split('id="panel-economics"', 1)[1].split('id="panel-osecan"', 1)[0]
    assert "<button" not in panel
