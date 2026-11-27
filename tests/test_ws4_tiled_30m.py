"""Contract tests for the tiled 30-m national WS4 sample."""

from __future__ import annotations

import json
import subprocess
from collections import deque
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from workflows.ws4_instances import build_scanfi_tiled_30m as tiled
from workflows.ws4_instances.build_scanfi_estate_pilot import (
    PIXEL_AREA_HA,
    WS4_BIN,
    _label_blocks,
    resolve_curves,
)

MANIFEST_PATH = tiled.NATIONAL_MANIFEST_PATH
requires_artifacts = pytest.mark.skipif(
    not MANIFEST_PATH.is_file(),
    reason="tiled 30-m national sample has not been generated",
)


def _manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


@requires_artifacts
def test_selection_one_per_positive_province_and_all_pei() -> None:
    frame = tiled.load_candidates()
    selected, diagnostics = tiled.select_representative_tiles(frame)
    assert diagnostics["province_top_nodes"] == 12
    assert diagnostics["positive_harvest_provinces"] == 12
    assert diagnostics["pei_nodes"] == 13
    assert diagnostics["pei_unique_parent_tiles"] == 6
    assert diagnostics["selected_nodes"] == 24
    assert diagnostics["zero_harvest_provinces"] == ["NU"]
    assert len(selected) == 24
    tops = selected[
        selected["selection_reasons"].map(lambda value: "province_top" in value)
    ]
    assert len(tops) == 12
    for province, group in tops.groupby("province"):
        expected = (
            frame[
                (frame["province"] == province) & (frame["harvest_m3"] > 0.0)
            ]
            .sort_values(
                ["harvest_m3", "node_id"], ascending=[False, True]
            )
            .iloc[0]
        )
        assert group.iloc[0]["node_id"] == expected["node_id"]
        assert group.iloc[0]["harvest_m3"] == pytest.approx(
            float(expected["harvest_m3"])
        )
    assert "NU" not in set(tops["province"])
    pei = selected[selected["province"] == "PE"]
    assert len(pei) == 13
    assert set(pei["node_id"]) == set(frame.loc[frame["province"] == "PE", "node_id"])
    assert pei["selection_reasons"].map(
        lambda value: "pei_complete" in value
    ).all()


@requires_artifacts
def test_manifest_completeness_and_no_fabricated_tiles() -> None:
    frame = tiled.load_candidates()
    manifest = _manifest()
    candidates = manifest["candidates"]
    selected_tiles = manifest["selected_tiles"]
    assert manifest["artifact"] == "ws4_tiled_30m_national_v1"
    assert manifest["status"] == "pass"
    assert manifest["scope"]["scope"] == "representative"
    assert manifest["scope"]["full_national_30m_run"] is False
    assert manifest["full_run"]["status"] == "not_run"
    assert "--all" in manifest["full_run"]["command"]
    assert "not run" in manifest["scope"]["full_national_statement"].lower()

    assert manifest["tile_grid"]["candidate_tile_datasets"] == len(frame) == 3562
    assert manifest["tile_grid"]["unique_rectangles"] == 3164
    assert manifest["tile_grid"]["multi_province_rectangles"] == 398
    assert len(candidates) == len(frame) == 3562
    assert {candidate["node_id"] for candidate in candidates} == set(
        frame["node_id"].astype(str)
    )
    assert sum(int(candidate["selected"]) for candidate in candidates) == 24
    for candidate in candidates:
        assert set(candidate) >= {
            "node_id",
            "province",
            "tile_id",
            "row",
            "col",
            "harvest_m3",
            "selected",
            "selection_reasons",
        }
        expected_selected = bool(candidate["selection_reasons"])
        assert bool(candidate["selected"]) is expected_selected
        assert candidate["tile_slug"] == tiled.tile_slug(candidate["node_id"])

    assert len(selected_tiles) == 24
    assert {row["node_id"] for row in selected_tiles} == {
        candidate["node_id"]
        for candidate in candidates
        if candidate["selected"]
    }
    for row in selected_tiles:
        assert row["validation_status"] == "pass"
        assert row["validation_exit_code"] == 0
        assert row["simulation_status"] == "pass"
        assert row["forest_fragments"] > 0
        assert row["blocks"] > 0
        assert row["forest_area_ha"] > 0.0
        assert row["forest_area_ha"] <= row["geometry_area_ha"] + 1e-6
        dataset_dir = tiled.REPO_ROOT / row["dataset_path"]
        assert (dataset_dir / "manifest.json").is_file()
        tile_manifest = json.loads(
            (dataset_dir / "manifest.json").read_text(encoding="utf-8")
        )
        assert tile_manifest["metadata"]["node_id"] == row["node_id"]
        assert tile_manifest["metadata"]["tiled_30m"] is True
        assert (
            tile_manifest["metadata"]["inventory_resolution"]
            == "30m"
        )
        assert tile_manifest["metadata"]["national_claim"] is False

    totals = manifest["totals"]
    assert totals["selected_tiles"] == 24
    assert totals["selected_fragments"] == sum(
        row["forest_fragments"] for row in selected_tiles
    )
    assert totals["selected_blocks"] == sum(row["blocks"] for row in selected_tiles)
    assert totals["selected_forest_area_ha"] == pytest.approx(
        sum(row["forest_area_ha"] for row in selected_tiles)
    )
    assert manifest["extrapolation"]["candidate_tile_datasets"] == 3562
    area_scaled = manifest["extrapolation"][
        "area_scaled_to_national_forest_area"
    ]
    assert area_scaled is not None
    assert area_scaled["forest_area_ha"] == pytest.approx(411250630.32)
    assert area_scaled["forest_fragments"] > totals["selected_fragments"]


@requires_artifacts
def test_area_fragment_block_conservation_on_g2_tile() -> None:
    summary = json.loads(
        (
            tiled.TILED_DIR
            / "G2-BC-S32"
            / "inventory_summary.json"
        ).read_text(encoding="utf-8")
    )
    dataset = tiled.TILED_DIR / "G2-BC-S32" / "dataset"
    fragments = pd.read_parquet(
        dataset / "fragments.parquet",
        columns=["fragment_id", "block_id", "area_ha", "age", "track_id"],
    )
    blocks = pd.read_parquet(
        dataset / "blocks.parquet",
        columns=["block_id", "track_id", "offset", "area_ha"],
    )
    counts = summary["counts"]
    assert len(fragments) == counts["forest_fragments"] > 2_000_000
    assert len(blocks) == counts["blocks"] > 500_000
    assert fragments["fragment_id"].tolist() == list(range(1, len(fragments) + 1))
    assert blocks["block_id"].tolist() == list(range(1, len(blocks) + 1))
    assert fragments["block_id"].min() == 1
    assert fragments["block_id"].max() == len(blocks)
    assert set(fragments["block_id"]) == set(blocks["block_id"])
    assert fragments["age"].gt(0).all()
    assert blocks["offset"].ge(0).all()
    assert (blocks["area_ha"] > 0.0).all()

    fragment_area = float(fragments["area_ha"].sum())
    block_area = float(blocks["area_ha"].sum())
    assert fragment_area == pytest.approx(len(fragments) * PIXEL_AREA_HA)
    assert fragment_area == pytest.approx(block_area, rel=1e-12, abs=1e-6)
    assert fragment_area == pytest.approx(counts["forest_area_ha"], abs=1e-6)
    per_block_fragment_area = np.bincount(
        fragments["block_id"].to_numpy(dtype=np.int64),
        weights=fragments["area_ha"].to_numpy(),
        minlength=len(blocks) + 1,
    )[1:]
    assert np.allclose(
        per_block_fragment_area,
        blocks["area_ha"].to_numpy(),
        rtol=0.0,
        atol=1e-9,
    )
    assert not set(fragments["track_id"]) - set(blocks["track_id"])
    assert summary["geometry"]["geometry_area_ha"] == pytest.approx(
        summary["geometry"]["committed_subcell_geometry_area_ha"]
    )
    assert summary["geometry"]["rectangle_area_ha"] == pytest.approx(250000.0)
    assert summary["schedule"]["scheduled_area_ha"] == pytest.approx(
        summary["simulation"]["treated_area_ha"]
    )


@requires_artifacts
def test_every_selected_dataset_has_labels_and_geometry() -> None:
    manifest = _manifest()
    frame = tiled.load_candidates().set_index("node_id")
    for row in manifest["selected_tiles"]:
        summary = json.loads(
            (
                tiled.TILED_DIR
                / row["tile_slug"]
                / "inventory_summary.json"
            ).read_text(encoding="utf-8")
        )
        labels = summary["labels"]
        assert labels["evidence_label"] == "model_estimate"
        assert labels["observed"] is False
        assert labels["policy_grade"] is False
        assert labels["national_claim"] is False
        assert labels["tiled_30m"] is True
        assert labels["tile_resolution_m"] == 30000.0
        assert labels["inventory_resolution"] == "30m"
        assert labels["official_osecan_changed"] is False
        assert labels["chain_v51_unchanged"] is True
        expected_area = float(frame.loc[row["node_id"], "subcell_geometry_area_ha"])
        assert summary["geometry"]["geometry_area_ha"] == pytest.approx(
            expected_area, abs=0.01
        )
        assert summary["node"]["node_id"] == row["node_id"]
        assert Path(row["dataset_path"]).parts[:3] == (
            "data",
            "derived",
            "ws4",
        )


@requires_artifacts
def test_no_extra_tile_directories() -> None:
    manifest = _manifest()
    expected = {row["tile_slug"] for row in manifest["selected_tiles"]}
    actual = {
        path.name
        for path in tiled.TILED_DIR.iterdir()
        if path.is_dir() and not path.name.startswith(".")
    }
    assert actual == expected


def test_fast_block_labelling_matches_reference() -> None:
    rng = np.random.default_rng(20261127)
    code_grid = rng.integers(-1, 4, size=(12, 15), dtype=np.int64)
    forest = code_grid >= 0
    labels, count = _label_blocks(code_grid, forest)

    # Reference: breadth-first search over 8-neighbour equal-code pixels.
    seen = np.zeros(code_grid.shape, dtype=bool)
    components: list[list[tuple[int, int]]] = []
    for start_row in range(code_grid.shape[0]):
        for start_col in range(code_grid.shape[1]):
            if not forest[start_row, start_col] or seen[start_row, start_col]:
                continue
            code = code_grid[start_row, start_col]
            queue = deque([(start_row, start_col)])
            seen[start_row, start_col] = True
            component: list[tuple[int, int]] = []
            while queue:
                row, col = queue.popleft()
                component.append((row, col))
                for delta_row in (-1, 0, 1):
                    for delta_col in (-1, 0, 1):
                        if delta_row == 0 and delta_col == 0:
                            continue
                        next_row = row + delta_row
                        next_col = col + delta_col
                        if not (0 <= next_row < code_grid.shape[0]):
                            continue
                        if not (0 <= next_col < code_grid.shape[1]):
                            continue
                        if seen[next_row, next_col]:
                            continue
                        if not forest[next_row, next_col]:
                            continue
                        if code_grid[next_row, next_col] != code:
                            continue
                        seen[next_row, next_col] = True
                        queue.append((next_row, next_col))
            components.append(component)

    assert count == len(components)
    assert labels.shape == code_grid.shape
    assert np.array_equal(labels > 0, forest)
    for component in components:
        ids = {int(labels[row, col]) for row, col in component}
        assert len(ids) == 1
        label = ids.pop()
        assert sum(
            int(labels[row, col]) == label for row, col in component
        ) == len(component)


def test_group_curve_resolution_from_source_species() -> None:
    rows = [
        {
            "tile_id": "T1",
            "province": "AB",
            "species": "ABIE.BAL",
            "species_kind": "species",
            "model_id": "conifer_t1",
            "selected_level": "tile",
            "source_species": "coniferous",
            "b1": 200.0,
            "b2": 0.03,
            "b3": 1.0,
            "b4": 0.001,
        },
        {
            "tile_id": "T1",
            "province": "AB",
            "species": "PICE.MAR",
            "species_kind": "species",
            "model_id": "pice_mar_t1",
            "selected_level": "tile",
            "source_species": "PICE.MAR",
            "b1": 180.0,
            "b2": 0.03,
            "b3": 1.0,
            "b4": -0.0005,
        },
        {
            "tile_id": "T1",
            "province": "AB",
            "species": "BROADLEAF",
            "species_kind": "group",
            "model_id": "broadleaf_t1",
            "selected_level": "tile",
            "source_species": "broadleaf",
            "b1": 160.0,
            "b2": 0.04,
            "b3": 1.5,
            "b4": 0.001,
        },
    ]
    resolution = resolve_curves(pd.DataFrame(rows), tile_id="T1", province="AB")
    keys = [spec.key for spec in resolution.specs]
    assert keys[resolution.species_spec_index["otherConiferous"]] == "conifer_t1"
    assert keys[resolution.species_spec_index["blackSpruce"]] == "pice_mar_t1"
    assert keys[resolution.landcover_spec_index[6]] == "conifer_t1"
    assert keys[resolution.landcover_spec_index[5]] == "broadleaf_t1"
    assert resolution.diagnostics["group_curves"]["CONIFEROUS"] == "conifer_t1"


@pytest.mark.slow
@requires_artifacts
def test_ws4_validate_smoke() -> None:
    if not WS4_BIN.is_file():
        pytest.skip("ws4 executable is not available")
    dataset = tiled.TILED_DIR / "G2-BC-S32" / "dataset"
    result = subprocess.run(
        [str(WS4_BIN), "validate", "--dataset", str(dataset)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "valid" in (result.stdout + result.stderr).lower()
