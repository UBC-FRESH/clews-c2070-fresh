"""Tests for the published open-data net-value model-input bundle."""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BUNDLE_DIR = (
    REPO_ROOT / "data/derived/national/harvest_basis/open_data_net_value/model_inputs"
)


def _load_script(module_name: str, relative_path: str):
    path = REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


builder = _load_script(
    "build_open_data_model_inputs", "scripts/national/build_open_data_model_inputs.py"
)
reproducer = _load_script(
    "reproduce_open_data_net_value", "scripts/national/reproduce_open_data_net_value.py"
)
model = builder.net_value


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _manifest() -> dict:
    return json.loads((BUNDLE_DIR / "input_manifest.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def manifest() -> dict:
    return _manifest()


def test_manifest_hashes_and_rows(manifest: dict) -> None:
    assert manifest["labels"]["input_publication"] == "derived_model_inputs"
    assert manifest["labels"]["raw_source_redistribution"] is False
    assert manifest["headline"]["median_base_net_model_cad_m3"] == 48.84
    assert manifest["headline"]["min_base_net_model_cad_m3"] == -23.38
    assert manifest["headline"]["max_base_net_model_cad_m3"] == 86.85
    assert manifest["headline"]["negative_net_base_rows"] == 168
    assert (
        manifest["headline"]["south_west_ground_net_model_base_median_cad_m3"] == 64.4
    )
    assert (
        manifest["headline"]["north_west_ground_net_model_base_median_cad_m3"] == 57.76
    )
    assert manifest["headline"]["qc_cost_spread_after_transfers_cad_m3"] == 0.81
    for item in manifest["files"]:
        path = BUNDLE_DIR / item["path"]
        assert path.is_file()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"]
        assert path.stat().st_size == item["bytes"]
        if path.suffix == ".csv":
            assert len(_read_csv(path)) == item["rows"]


def test_no_raw_sources_are_bundled(manifest: dict) -> None:
    suffixes = {path.suffix.lower() for path in BUNDLE_DIR.iterdir() if path.is_file()}
    assert not suffixes & {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".zip"}
    sources = _read_csv(BUNDLE_DIR / "sources.csv")
    assert len(sources) == manifest["row_counts"]["sources.csv"]
    for row in sources:
        assert row["raw_redistributed"] == "false"
        assert row["url"].startswith("https://")
        assert row["retrieved_utc"]
        assert row["licence_terms"]
        assert row["citation"]


def test_price_panel_is_exactly_the_consumed_rows(manifest: dict) -> None:
    rows = _read_csv(BUNDLE_DIR / "bc_price_panel_used.csv")
    primary = [row for row in rows if row["panel_role"] == "primary_3_month_2025-12"]
    sensitivity = [
        row for row in rows if row["panel_role"] == "sensitivity_1_month_2025-12"
    ]
    assert len(primary) == 94
    assert len(sensitivity) == 96
    assert sum(row["used_in_model"] == "true" for row in primary) == 89
    assert sum(row["used_in_model"] == "true" for row in sensitivity) == 91
    assert all(
        row["exclusion_reason"] == "interior_weighted_average_aggregate"
        for row in rows
        if row["used_in_model"] == "false"
    )
    assert all(
        row["price_used_cad_m3"] != "" and row["volume_used_m3"] != ""
        for row in rows
        if row["used_in_model"] == "true"
    )
    assert manifest["row_counts"]["bc_price_panel_used.csv"] == 190
    panels = model.load_price_panels(BUNDLE_DIR / "bc_price_panel_used.csv")
    assert round(panels["Coast"].base_cad_m3, 2) == 122.59
    assert round(panels["Interior"].base_cad_m3, 2) == 113.38
    assert panels["Interior"].aggregates_excluded == 5
    assert panels["Coast"].cells_used == 79


def test_cost_grid_and_parameter_references(manifest: dict) -> None:
    rows = _read_csv(BUNDLE_DIR / "bc_cost_inputs_used.csv")
    parameters = {
        row["parameter_id"]: row
        for row in _read_csv(BUNDLE_DIR / "bc_cost_parameters_used.csv")
    }
    assert len(rows) == 945
    assert len(parameters) == manifest["row_counts"]["bc_cost_parameters_used.csv"]
    assert {row["zone"] for row in rows} == set(model.EXPECTED_ZONES)
    for row in rows:
        ids = {item for item in row["parameter_ids"].split("|") if item}
        assert ids <= set(parameters)
        assert {parameters[item]["source_key"] for item in ids} == {
            item for item in row["source_ids"].split("|") if item
        }
        assert row["logging_formula_id"]
        assert row["haul_formula_id"] == (
            "haul_cost_interior_iam_engineering"
            if row["appraisal_region"] == "Interior"
            else "haul_cost_coast_engineering"
        )
        low = float(row["total_low_cad_m3"])
        base = float(row["total_base_cad_m3"])
        high = float(row["total_high_cad_m3"])
        assert low <= base <= high


def test_silviculture_rows_are_interior_only(manifest: dict) -> None:
    rows = _read_csv(BUNDLE_DIR / "silviculture_inputs_used.csv")
    loaded = [row for row in rows if row["loaded_into_model"] == "true"]
    excluded = [row for row in rows if row["loaded_into_model"] == "false"]
    assert len(loaded) == 7
    assert len(excluded) == 15
    assert all(row["region"] == "Coast" for row in excluded)
    assert all(row["cost_basis"] == "CAD/ha" for row in excluded)
    assert all(row["exclusion_reason"] for row in excluded)
    assert all(row["source_id"] == "bc_cam_2026" for row in excluded)
    south_west = next(row for row in loaded if row["zone"] == "South West Zone")
    assert south_west["applied_as_deduction"] == "false"
    assert south_west["exclusion_reason"]
    allowances = model.load_silviculture_allowances(
        BUNDLE_DIR / "silviculture_inputs_used.csv"
    )
    assert set(allowances) == set(model.EXPECTED_ZONES)
    assert allowances["North West Zone"].value_cad_m3 == pytest.approx(5.84)
    assert allowances["South West Zone"].value_cad_m3 == pytest.approx(11.23)
    assert manifest["row_counts"]["silviculture_inputs_used.csv"] == 22


def test_zone_mapping_and_qc_reference(manifest: dict) -> None:
    mapping = _read_csv(BUNDLE_DIR / "zone_region_mapping.csv")
    assert len(mapping) == 7
    assert {row["zone"] for row in mapping} == set(model.EXPECTED_ZONES)
    for row in mapping:
        assert row["price_region"] == model.price_region_for_zone(row["zone"])
        assert row["appraisal_region"] == row["price_region"]
    qc = _read_csv(BUNDLE_DIR / "qc_reference_used.csv")
    assert len(qc) == 1
    assert qc[0]["benchmark_id"] == "qc_2022_sepm_total"
    assert float(qc[0]["cost_spread_after_transfers_cad_m3"]) == pytest.approx(0.81)
    assert qc[0]["source_id"] == "qc_mrnf_2022_main"
    assert manifest["row_counts"]["qc_reference_used.csv"] == 1


def test_reproducer_matches_headline_and_hashes() -> None:
    result = reproducer.reproduce()
    assert result["headline"] == _manifest()["headline"]
    assert (
        result["core_output_sha256"]
        == _manifest()["reproduction"]["core_output_sha256"]
    )


def test_reproducer_fails_closed_on_hash_mismatch(tmp_path: Path) -> None:
    damaged = tmp_path / "bundle"
    shutil.copytree(BUNDLE_DIR, damaged)
    with (damaged / "bc_price_panel_used.csv").open("a", encoding="utf-8") as handle:
        handle.write("\n")
    manifest = reproducer._load_manifest(damaged)
    with pytest.raises(reproducer.ReproductionError, match="sha256 mismatch"):
        reproducer.verify_files(damaged, manifest)


def test_builder_is_deterministic(tmp_path: Path) -> None:
    rebuilt = tmp_path / "bundle"
    rebuilt_manifest = builder.build_bundle(rebuilt)
    committed = _manifest()
    by_name = {item["path"]: item for item in committed["files"]}
    for item in rebuilt_manifest["files"]:
        assert item["sha256"] == by_name[item["path"]]["sha256"]
    assert rebuilt_manifest["headline"] == committed["headline"]
