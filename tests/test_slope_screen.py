"""Slope-screen calculation and artifact contracts."""

from __future__ import annotations

import importlib.util
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "scripts/national/build_slope_screen.py"
OUTPUT_DIR = REPO_ROOT / "data/derived/national/managed_landbase/slope_screen"


def _load_module():
    spec = importlib.util.spec_from_file_location("build_slope_screen", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


slope_screen = _load_module()


def test_horn_slope_on_constant_gradient() -> None:
    rows, cols = 9, 11
    row_index, col_index = np.mgrid[0:rows, 0:cols]
    elevation = 0.1 * col_index + 0.2 * row_index
    slope = slope_screen.horn_slope_percent(elevation, xres=1.0, yres=1.0)
    expected = math.hypot(0.1, 0.2) * 100.0
    assert slope[4, 5] == pytest.approx(expected, rel=1e-12)
    assert np.isnan(slope[0, :]).all()
    assert np.isnan(slope[:, 0]).all()


def test_horn_slope_flat_surface_is_zero() -> None:
    slope = slope_screen.horn_slope_percent(np.zeros((7, 7)), 30.0, 30.0)
    assert slope[3, 3] == pytest.approx(0.0, abs=1e-12)


def test_horn_slope_nodata_propagates_to_nan() -> None:
    elevation = np.zeros((7, 7))
    elevation[3, 4] = -32767.0
    slope = slope_screen.horn_slope_percent(elevation, 30.0, 30.0, nodata=-32767.0)
    # The Horn kernel does not use the centre cell, but every cell that uses
    # the nodata neighbour must be NaN rather than silently zero.
    assert np.isnan(slope[3, 3])
    assert np.isnan(slope[3, 5])
    assert np.isfinite(slope[1, 1])


def test_validate_slope_rejects_negative_values() -> None:
    with pytest.raises(ValueError, match="negative"):
        slope_screen.validate_slope(np.array([[0.0, -1.0]]))


def test_validate_slope_rejects_implausible_values() -> None:
    with pytest.raises(ValueError, match="200 percent"):
        slope_screen.validate_slope(np.array([[0.0, 250.0]]))


def test_block_sum_conserves_partial_edges() -> None:
    mask = np.arange(25).reshape(5, 5) % 2 == 0
    blocks = slope_screen.block_sum(mask, block_pixels=2, block_cols=3)
    assert blocks.shape == (3, 3)
    assert int(blocks.sum()) == int(mask.sum())
    assert int(blocks[0, 0]) == int(mask[:2, :2].sum())
    assert int(blocks[2, 2]) == int(mask[4, 4])


def test_weighted_aggregate_respects_forest_weights() -> None:
    codes = np.array([[1, 1], [2, 2]])
    weights = np.array([[1.0, 0.5], [1.0, 0.0]])
    values = np.array([[10.0, 20.0], [40.0, 50.0]])
    result = slope_screen.weighted_aggregate(
        codes,
        weights,
        values,
        thresholds=(15.0, 30.0),
        n_codes=3,
        pixel_ha=1.0,
    )
    assert result["weight_sum"][1] == pytest.approx(1.5)
    assert result["value_sum"][1] / result["weight_sum"][1] == pytest.approx(
        13.3333333333
    )
    assert result["threshold_sums"][15.0][1] / result["weight_sum"][1] == pytest.approx(
        1.0 / 3.0
    )
    assert result["threshold_sums"][30.0][2] == pytest.approx(1.0)
    assert result["area_ha"][2] == pytest.approx(1.0)


def _summary() -> dict:
    path = OUTPUT_DIR / "slope_screen_summary.json"
    assert path.is_file(), f"missing slope summary: {path}"
    return json.loads(path.read_text(encoding="utf-8"))


def test_summary_slope_range_and_validation() -> None:
    summary = _summary()
    slope = summary["slope"]
    assert 0.0 <= slope["min_percent"] <= slope["max_percent"] <= 200.0
    assert slope["max_percent"] >= 50.0
    assert summary["validation"]["slope_min_nonnegative"] is True
    assert summary["validation"]["slope_max_below_200_percent"] is True
    assert summary["validation"]["no_nan_in_area_fields"] is True
    assert summary["validation"]["no_negative_area_fields"] is True
    assert summary["validation"]["core20_le_base"] is True


def test_overlay_area_conservation_and_columns() -> None:
    path = OUTPUT_DIR / "slope_operable_overlay.csv"
    assert path.is_file(), f"missing overlay: {path}"
    frame = pd.read_csv(path)
    required = {
        "province",
        "ecoprovince_id",
        "base_operable_ha",
        "core20_operable_ha",
        "base_lost_gt30_ha",
        "base_remaining_gt30_ha",
        "core20_lost_gt30_ha",
        "core20_remaining_gt30_ha",
        "share_gt15",
        "share_gt30",
    }
    assert required.issubset(frame.columns)
    assert not frame[list(required - {"province"})].isna().any().any()
    assert (frame["base_operable_ha"] >= 0).all()
    assert (frame["core20_operable_ha"] >= 0).all()
    assert (frame["core20_operable_ha"] <= frame["base_operable_ha"] + 1e-6).all()
    assert (frame["base_lost_gt30_ha"] <= frame["base_operable_ha"] + 1e-6).all()
    assert (frame["core20_lost_gt30_ha"] <= frame["core20_operable_ha"] + 1e-6).all()
    assert (frame["share_gt15"] >= frame["share_gt30"] - 1e-12).all()


def test_province_rollup_equals_national() -> None:
    summary = _summary()
    province_path = OUTPUT_DIR / "slope_operable_overlay_by_province.csv"
    assert province_path.is_file()
    province = pd.read_csv(province_path)
    national = summary["national"]
    assert len(province) == 13
    assert province["base_operable_ha"].sum() == pytest.approx(
        national["base_operable_ha"], rel=1e-12
    )
    assert province["core20_operable_ha"].sum() == pytest.approx(
        national["core20_operable_ha"], rel=1e-12
    )
    assert province["base_lost_gt30_ha"].sum() == pytest.approx(
        national["base_lost_gt30_ha"], rel=1e-12
    )


def test_slope_raster_metadata() -> None:
    rasterio = pytest.importorskip("rasterio")
    path = OUTPUT_DIR / "slope_960m_epsg3979.tif"
    assert path.is_file(), f"missing slope raster: {path}"
    with rasterio.open(path) as dataset:
        assert dataset.crs.to_epsg() == 3979
        assert dataset.res == pytest.approx((960.0, 960.0))
        assert dataset.nodata == 65535
        assert dataset.scales == (0.01,)
        tags = dataset.tags()
        assert "Horn" in tags["method"]
