"""Contract tests for the tiled 30-m national WS4 sample and staged build."""

from __future__ import annotations

import argparse
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
    completed = manifest["completed_tiles"]
    assert manifest["artifact"] == "ws4_tiled_30m_national_v1"
    assert manifest["status"] == "pass"
    assert manifest["scope"]["scope"] == "staged"
    assert manifest["scope"]["full_national_30m_run"] is False
    assert manifest["full_run"]["status"] == "staged_in_progress"
    assert "--all" in manifest["full_run"]["command"]
    assert manifest["full_run"]["datasets"] == 3562
    assert "staged national coverage" in manifest["scope"][
        "full_national_statement"
    ].lower()

    assert manifest["tile_grid"]["candidate_tile_datasets"] == len(frame) == 3562
    assert manifest["tile_grid"]["unique_rectangles"] == 3164
    assert manifest["tile_grid"]["multi_province_rectangles"] == 398
    assert len(candidates) == len(frame) == 3562
    assert {candidate["node_id"] for candidate in candidates} == set(
        frame["node_id"].astype(str)
    )
    assert all(candidate["stage"] in tiled.STAGES for candidate in candidates)
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
            "stage",
            "zero_harvest",
        }
        assert candidate["tile_slug"] == tiled.tile_slug(candidate["node_id"])
        assert candidate["stage"] == tiled.PROVINCE_STAGE[candidate["province"]]

    plan = manifest["stage_plan"]
    assert plan["candidate_tiles"] == 3562
    assert plan["stage_order"] == list(tiled.STAGE_ORDER)
    assert {
        stage: item["candidate_tiles"] for stage, item in plan["stages"].items()
    } == {
        "stage1": 1254,
        "stage2": 713,
        "stage3": 494,
        "stage4": 1101,
    }
    assert plan["completed_tiles"] + plan["remaining_tiles"] + plan[
        "skipped_zero_harvest_tiles"
    ] + plan["skipped_no_forest_tiles"] == 3562
    assert plan["completed_tiles"] == len(completed)
    assert "stage1" in plan["completed_stages"]
    assert plan["remaining_stages"] == [
        stage
        for stage in tiled.STAGE_ORDER
        if stage not in plan["completed_stages"]
    ]

    assert completed
    assert len(completed) == manifest["totals"]["selected_tiles"]
    for row in completed:
        assert row["validation_status"] == "pass"
        assert row["validation_exit_code"] == 0
        assert row["forest_fragments"] > 0
        assert row["blocks"] > 0
        assert row["forest_area_ha"] > 0.0
        # Tiny clipped slivers can gain a few whole 30-m pixels at the polygon
        # boundary; allow that discretisation edge effect but not a material
        # area overshoot.
        assert row["forest_area_ha"] <= row["geometry_area_ha"] + max(
            10.0, 0.01 * row["geometry_area_ha"]
        )
        assert row["stage"] == tiled.PROVINCE_STAGE[row["province"]]
        dataset_dir = tiled.REPO_ROOT / row["dataset_path"]
        tile_manifest = dataset_dir / "manifest.json"
        assert tile_manifest.is_file()
        assert row["dataset_manifest_sha256"] == tiled.sha256_file(
            tile_manifest
        )
        tile_payload = json.loads(tile_manifest.read_text(encoding="utf-8"))
        assert tile_payload["metadata"]["node_id"] == row["node_id"]
        assert tile_payload["metadata"]["tiled_30m"] is True
        assert tile_payload["metadata"]["inventory_resolution"] == "30m"
        assert tile_payload["metadata"]["national_claim"] is False

    totals = manifest["totals"]
    assert totals["selected_fragments"] == sum(
        row["forest_fragments"] for row in completed
    )
    assert totals["selected_blocks"] == sum(row["blocks"] for row in completed)
    assert totals["selected_forest_area_ha"] == pytest.approx(
        sum(row["forest_area_ha"] for row in completed)
    )
    assert manifest["extrapolation"]["candidate_tile_datasets"] == 3562
    area_scaled = manifest["extrapolation"][
        "area_scaled_to_national_forest_area"
    ]
    assert area_scaled is not None
    assert area_scaled["forest_area_ha"] == pytest.approx(411250630.32)
    assert area_scaled["forest_fragments"] > totals["selected_fragments"]

    sample = manifest["representative_sample"]
    assert sample["built_tiles"] == sample["expected_tiles"] == 24
    simulation = manifest["sample_simulation"]
    assert simulation["status"] == "pass"
    assert simulation["tiles_simulated"] > 0
    assert simulation["harvested_volume_m3"] > 0.0
    assert simulation["treated_area_ha"] > 0.0


@requires_artifacts
def test_stage1_per_tile_validation_and_explicit_skips() -> None:
    manifest = _manifest()
    stage = manifest["stage_plan"]["stages"]["stage1"]
    assert stage["candidate_tiles"] == 1254
    assert stage["positive_harvest_tiles"] == 1051
    assert stage["zero_harvest_tiles"] == 203
    assert stage["skipped_zero_harvest_tiles"] == 203
    assert stage["built_tiles"] >= 1042
    assert stage["validated_tiles"] == stage["built_tiles"]
    assert stage["remaining_tiles"] == 0
    assert stage["complete"] is True
    built = [
        row for row in stage["tiles"] if row["validation_status"] == "pass"
    ]
    no_forest = [
        row
        for row in stage["tiles"]
        if row["validation_status"] == "skipped_no_forest"
    ]
    assert len(built) == stage["built_tiles"]
    assert len(no_forest) == stage["skipped_no_forest_tiles"]
    for row in built:
        assert row["validation_exit_code"] == 0
        assert row["dataset_manifest_sha256"] == tiled.sha256_file(
            tiled.TILED_DIR / row["tile_slug"] / "dataset" / "manifest.json"
        )
    for row in no_forest:
        assert row["skip_reason"]
        assert "no forest" in row["skip_reason"].lower() or "no pixels" in row[
            "skip_reason"
        ].lower()
        assert not (tiled.TILED_DIR / row["tile_slug"] / "dataset").exists()

    if WS4_BIN.is_file() and built:
        for row in built[:: max(1, len(built) // 3)][:3]:
            result = subprocess.run(
                [
                    str(WS4_BIN),
                    "validate",
                    "--dataset",
                    str(tiled.TILED_DIR / row["tile_slug"] / "dataset"),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            assert result.returncode == 0, result.stdout + result.stderr


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
    expected = {row["tile_slug"] for row in manifest["completed_tiles"]}
    actual = {
        path.name
        for path in tiled.TILED_DIR.iterdir()
        if path.is_dir() and not path.name.startswith(".")
    }
    assert actual == expected
    assert len(actual) == manifest["stage_plan"]["completed_tiles"]


def test_stage_plan_covers_every_candidate_exactly_once() -> None:
    frame = tiled.load_candidates()
    counts = tiled.stage_counts(frame)
    assert set(counts) == {"stage1", "stage2", "stage3", "stage4"}
    assert counts["stage1"]["candidate_tiles"] == 1254
    assert counts["stage1"]["positive_harvest_tiles"] == 1051
    assert counts["stage1"]["zero_harvest_tiles"] == 203
    assert counts["stage1"]["zero_harvest_provinces"] == ["NU"]
    assert counts["stage2"]["candidate_tiles"] == 713
    assert counts["stage3"]["candidate_tiles"] == 494
    assert counts["stage4"]["candidate_tiles"] == 1101
    assert sum(item["candidate_tiles"] for item in counts.values()) == 3562
    seen: set[str] = set()
    for stage, item in counts.items():
        selected = tiled.stage_candidate_frame(frame, stage)
        assert len(selected) == item["candidate_tiles"]
        ids = set(selected["node_id"].astype(str))
        assert seen.isdisjoint(ids), f"{stage} overlaps an earlier stage"
        seen |= ids
        assert set(selected["province"].astype(str)) == set(item["provinces"])
    assert seen == set(frame["node_id"].astype(str))
    assert tiled.PROVINCE_STAGE.keys() == set(tiled.PROVINCES)


def test_stage_scope_and_zero_harvest_accounting() -> None:
    frame = tiled.load_candidates()
    args = argparse.Namespace(
        stage="stage1", all=False, only_node=None
    )
    selected, diagnostics, scope = tiled._selected_frame_for_scope(frame, args)
    assert scope == "stage:stage1"
    assert len(selected) == 1254
    assert diagnostics["candidate_tiles"] == 1254
    assert diagnostics["zero_harvest_tiles"] == 203
    zero = selected[selected["harvest_m3"] <= 0.0]
    assert len(zero) == 203
    assert set(zero["province"].astype(str)) == {"NU"}


def test_resume_decision_skips_valid_and_rejects_invalid(
    tmp_path: Path,
) -> None:
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    manifest = dataset / "manifest.json"
    manifest.write_text('{"schema_version": "0.1"}\n', encoding="utf-8")
    expected = tiled.sha256_file(manifest)
    validated: list[Path] = []

    def validator(path: Path) -> dict:
        validated.append(path)
        return {"exit_code": 0}

    reusable, reason = tiled.resume_decision(
        dataset, expected, validator=validator
    )
    assert reusable is True
    assert reason == "hash_match"
    assert validated == [dataset]

    reusable, reason = tiled.resume_decision(
        dataset, "0" * 64, validator=validator
    )
    assert reusable is False
    assert reason == "manifest_hash_mismatch"
    assert validated == [dataset]

    def invalid(path: Path) -> dict:
        raise RuntimeError("invalid dataset")

    reusable, reason = tiled.resume_decision(
        dataset, expected, validator=invalid
    )
    assert reusable is False
    assert reason == "validation_failed"

    reusable, reason = tiled.resume_decision(
        tmp_path / "missing", None, validator=validator
    )
    assert reusable is False
    assert reason == "missing_dataset"

    reusable, reason = tiled.resume_decision(
        dataset, None, validator=validator
    )
    assert reusable is True
    assert reason == "bootstrapped_hash"


def test_storage_projection_covers_stages() -> None:
    manifest = _manifest()
    frame = tiled.load_candidates()
    projection = tiled.storage_projection(
        frame, existing_manifest=manifest
    )
    assert projection["full_national_total_bytes"] > 0
    assert sum(
        item["candidate_tiles"] for item in projection["stages"].values()
    ) == 3562
    for stage in tiled.STAGE_ORDER:
        item = projection["stages"][stage]
        assert item["projected_total_bytes"] >= item["projected_dataset_bytes"]
        assert 0.0 < item["forest_area_share"] < 1.0


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
