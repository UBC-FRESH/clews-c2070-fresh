"""Deprecation checks for the former v2d net-value layer."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
HARVEST_BASIS_DIR = REPO_ROOT / "data/derived/national/harvest_basis"


def test_old_v2d_layer_is_marked_deprecated() -> None:
    pointer = HARVEST_BASIS_DIR / "net_value_v2d/DEPRECATED.md"
    assert pointer.is_file()
    text = pointer.read_text(encoding="utf-8")
    assert "forest_economics_v2d" in text
    assert "deprecated" in text.lower()


def test_old_qc_benchmark_is_marked_deprecated() -> None:
    pointer = HARVEST_BASIS_DIR / "net_value_benchmark/DEPRECATED.md"
    assert pointer.is_file()
    text = pointer.read_text(encoding="utf-8")
    assert "procurement_benchmark" in text
    assert "forest_economics_v2d" in text


def test_replacement_builder_exists() -> None:
    assert (
        REPO_ROOT / "workflows/national/harvest/forest_economics_v2d.py"
    ).is_file()
    assert (REPO_ROOT / "tests/test_forest_economics_v2d.py").is_file()
