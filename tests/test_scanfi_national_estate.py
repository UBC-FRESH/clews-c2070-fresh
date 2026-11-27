"""Focused tests for the national SCANFI/RSYC 990-m WS4 estate."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from rasterio.crs import CRS
from rasterio.transform import Affine
from shapely.geometry import box

from workflows.ws4_instances.build_scanfi_estate_pilot import (
    AREA_CURVE_ID,
    CURVE_DOMAIN,
    PIXEL_AREA_HA,
    WS4_BIN,
    CurveSpec,
    ScanfiEstateError,
    run_ws4_validate,
)
from workflows.ws4_instances.build_scanfi_national_estate import (
    BLOCK_PIXELS,
    WS4_FRAGMENT_AREA_HA,
    BlockGrid,
    ForestScan,
    _table_schema,
    aggregate_chunk,
    assign_blocks,
    build_node_grid,
    build_schedule,
    build_ws4_tables,
    label_blocks,
    resolve_national_curves,
    run_national_simulation,
)


def _spec(key: str, *, group: str = "CONIFEROUS") -> CurveSpec:
    return CurveSpec(
        key=key,
        model_id=key,
        species=group,
        species_kind="group",
        selected_level="tile",
        source_species=group.lower(),
        b1=100.0,
        b2=0.03,
        b3=1.0,
        b4=0.001,
    )


def _tiny_block_grid() -> BlockGrid:
    return BlockGrid(
        crs=CRS.from_epsg(3978),
        transform=Affine(990.0, 0.0, 0.0, 0.0, -990.0, 0.0),
        raster_width=4 * BLOCK_PIXELS,
        raster_height=4 * BLOCK_PIXELS,
        block_cols=4,
        block_rows=4,
        pixel_width=4 * BLOCK_PIXELS,
        pixel_height=4 * BLOCK_PIXELS,
        ignored_edge_columns=0,
        ignored_edge_rows=0,
    )


def _synthetic_scan() -> ForestScan:
    rows = np.array([0, 0, 1, 1, 3], dtype=np.int32)
    cols = np.array([0, 1, 0, 1, 3], dtype=np.int32)
    forest_pixels = np.array([500, 600, 700, 800, 400], dtype=np.int32)
    conifer = np.array([500, 600, 100, 100, 400], dtype=np.int32)
    broadleaf = np.array([0, 0, 600, 700, 0], dtype=np.int32)
    mixed = forest_pixels - conifer - broadleaf
    age = np.array([45, 45, 45, 45, 65], dtype=np.int32)
    age_class = (age // 10) * 10
    province = np.array([1, 1, 1, 1, 2], dtype=np.int32)
    curve = np.array([0, 0, 1, 1, 0], dtype=np.int32)
    group = np.where(conifer >= broadleaf, "CONIFEROUS", "BROADLEAF")
    fraction = ((np.maximum(conifer, broadleaf) + mixed) / forest_pixels).astype(
        np.float32
    )
    return ForestScan(
        cell_rows=rows,
        cell_cols=cols,
        forest_pixels=forest_pixels,
        conifer_pixels=conifer,
        broadleaf_pixels=broadleaf,
        mixed_pixels=mixed,
        age=age,
        age_class=age_class,
        node_index=np.zeros(5, dtype=np.int32),
        curve_index=curve,
        province_index=province,
        landcover_group=group,
        group_fraction=fraction,
        totals={},
        timings={},
    )


def _library_row(
    tile_id: str,
    province: str,
    species: str,
    source_species: str,
    model_id: str,
) -> dict[str, object]:
    return {
        "tile_id": tile_id,
        "province": province,
        "species": species,
        "species_kind": "group" if species.isupper() else "species",
        "model_id": model_id,
        "selected_level": "tile",
        "source_species": source_species,
        "status": "ok",
        "b1": 100.0,
        "b2": 0.03,
        "b3": 1.0,
        "b4": 0.001,
    }


def test_aggregate_chunk_area_age_class_and_group() -> None:
    age = np.zeros((2 * BLOCK_PIXELS, 2 * BLOCK_PIXELS), dtype=np.int16)
    landcover = np.full(age.shape, 3, dtype=np.uint8)
    # Block (0, 0): conifer, age 40.
    age[:BLOCK_PIXELS, :BLOCK_PIXELS] = 40
    landcover[:BLOCK_PIXELS, :BLOCK_PIXELS] = 6
    # Block (0, 1): 600 conifer age 40 and 489 broadleaf age 80.
    age[:BLOCK_PIXELS, BLOCK_PIXELS:] = 80
    flat_age = age[:BLOCK_PIXELS, BLOCK_PIXELS:].reshape(-1)
    flat_landcover = landcover[:BLOCK_PIXELS, BLOCK_PIXELS:].reshape(-1)
    flat_age[:600] = 40
    flat_landcover[:600] = 6
    flat_landcover[600:] = 5
    age[:BLOCK_PIXELS, BLOCK_PIXELS:] = flat_age.reshape(BLOCK_PIXELS, BLOCK_PIXELS)
    landcover[:BLOCK_PIXELS, BLOCK_PIXELS:] = flat_landcover.reshape(
        BLOCK_PIXELS, BLOCK_PIXELS
    )
    # Block (1, 0): mixed and broadleaf only (broadleaf group).
    age[BLOCK_PIXELS:, :BLOCK_PIXELS] = 100
    landcover[BLOCK_PIXELS:, :BLOCK_PIXELS] = 7
    landcover[BLOCK_PIXELS, :BLOCK_PIXELS] = 5
    # Block (1, 1) stays non-forest.

    aggregated = aggregate_chunk(age, landcover)
    counts = aggregated["forest_pixels"]
    assert counts[0, 0] == BLOCK_PIXELS * BLOCK_PIXELS
    assert counts[0, 1] == BLOCK_PIXELS * BLOCK_PIXELS
    assert counts[1, 0] == BLOCK_PIXELS * BLOCK_PIXELS
    assert counts[1, 1] == 0
    assert aggregated["age"][0, 0] == 40
    assert aggregated["age"][0, 1] == 58  # 600 x 40 + 489 x 80, rounded
    assert aggregated["age_class"][0, 1] == 50
    assert aggregated["landcover_group"][0, 1] == "CONIFEROUS"
    assert aggregated["group_fraction"][0, 1] == pytest.approx(
        600 / (BLOCK_PIXELS * BLOCK_PIXELS)
    )
    assert aggregated["landcover_group"][1, 0] == "BROADLEAF"
    assert aggregated["group_fraction"][1, 0] == pytest.approx(1.0)
    assert aggregated["landcover_group"][1, 1] == "CONIFEROUS"
    assert aggregated["forest_pixels"].sum() * PIXEL_AREA_HA == pytest.approx(
        3 * WS4_FRAGMENT_AREA_HA
    )


def test_label_blocks_eight_neighbour_and_key_separation() -> None:
    rows = np.array([0, 1, 2, 0, 3], dtype=np.int32)
    cols = np.array([0, 1, 1, 3, 3], dtype=np.int32)
    # (0,0) and (1,1) connect diagonally; (2,1) connects to (1,1).
    code = np.array([7, 7, 7, 8, 8], dtype=np.int64)
    block_ids, representative, count = label_blocks(rows, cols, code, (4, 4))
    assert count == 3
    assert block_ids.tolist() == [1, 1, 1, 2, 3]
    assert representative.tolist() == [0, 3, 4]
    # The same positions with different codes must split every cell.
    split, _, split_count = label_blocks(rows, cols, np.arange(5), (4, 4))
    assert split_count == 5
    assert sorted(split.tolist()) == [1, 2, 3, 4, 5]


def test_assign_blocks_conserves_area_and_tracks() -> None:
    scan = _synthetic_scan()
    assignment = assign_blocks(scan, _tiny_block_grid(), (_spec("a"), _spec("b")))
    assert assignment.diagnostics["fragments"] == len(scan.cell_rows)
    assert assignment.diagnostics["blocks"] == 3
    assert assignment.fragments["area_ha"].sum() == pytest.approx(
        scan.forest_pixels.sum() * PIXEL_AREA_HA
    )
    assert assignment.blocks["area_ha"].sum() == pytest.approx(
        assignment.fragments["area_ha"].sum()
    )
    assert set(assignment.fragments["block_id"]) == set(assignment.blocks["block_id"])
    assert set(assignment.fragments["track_id"]) <= set(assignment.blocks["track_id"])
    assert len(assignment.groups) == len(assignment.blocks)
    assert assignment.groups["group"].str.startswith("province:").all()
    assert assignment.groups["group"].str.contains("|curve:").all()
    # Blocks in different provinces use different natural track pairs.
    assert assignment.blocks["track_id"].nunique() == 3


def test_resolve_national_curves_group_generic_and_fail_closed() -> None:
    nodes = pd.DataFrame(
        {
            "node_id": ["T1|BC|S11", "T2|BC|S11"],
            "tile_id": ["T1", "T2"],
            "province": ["BC", "BC"],
            "row": [1, 1],
            "col": [1, 1],
            "subcell_geometry_area_ha": [250000.0, 250000.0],
        }
    )
    library = pd.DataFrame(
        [
            _library_row("T1", "BC", "CONIFEROUS", "coniferous", "conifer_t1"),
            _library_row("T1", "BC", "BROADLEAF", "broadleaf", "broadleaf_t1"),
            _library_row("T2", "BC", "CONIFEROUS", "coniferous", "conifer_t2"),
            _library_row("T2", "BC", "BROADLEAF", "generic", "generic_t2"),
        ]
    )
    registry = resolve_national_curves(library, nodes)
    assert len(registry.specs) == 4
    assert registry.node_conifer_index.tolist() == [1, 2]
    assert registry.node_broadleaf_index.tolist() == [0, 3]
    assert registry.diagnostics["node_group_resolution"] == {"group": 1, "generic": 1}

    # Remove the literal broadleaf row: the generic row must be the fallback.
    fallback_library = library[
        (library["tile_id"] != "T2") | (library["source_species"] != "broadleaf")
    ].copy()
    fallback = resolve_national_curves(fallback_library, nodes)
    fallback_key = fallback.specs[fallback.node_broadleaf_index[1]].key
    assert fallback_key == "generic_t2"
    assert fallback.diagnostics["node_group_resolution"] == {"group": 1, "generic": 1}

    # A node with no conifer and no generic curve fails closed.
    missing = library[library["tile_id"] != "T2"].copy()
    with pytest.raises(ScanfiEstateError, match="no RSYC group or generic curve"):
        resolve_national_curves(missing, nodes)


def test_build_node_grid_clips_to_tile_province_allocation() -> None:
    # 150-km tile split into a left and a right province.  Only the right
    # province is allocated; left-province cells must stay unmapped.
    tile = box(0.0, 0.0, 150_000.0, 150_000.0)
    left = box(0.0, 0.0, 75_000.0, 150_000.0)
    right = box(75_000.0, 0.0, 150_000.0, 150_000.0)
    nodes = pd.DataFrame(
        {
            "node_id": [f"T|BC|S{row}{col}" for row in (1, 2, 3) for col in (2, 3)],
            "tile_id": ["T"] * 6,
            "province": ["BC"] * 6,
            "row": [row for row in (1, 2, 3) for _ in (2, 3)],
            "col": [col for _ in (1, 2, 3) for col in (2, 3)],
            "subcell_geometry_area_ha": [
                125_000.0 if col == 2 else 250_000.0
                for row in (1, 2, 3)
                for col in (2, 3)
            ],
        }
    )
    grid = BlockGrid(
        crs=CRS.from_epsg(3978),
        transform=Affine(990.0, 0.0, 0.0, 0.0, -990.0, 150_000.0),
        raster_width=150_000,
        raster_height=150_000,
        block_cols=152,
        block_rows=152,
        pixel_width=150_000,
        pixel_height=150_000,
        ignored_edge_columns=0,
        ignored_edge_rows=0,
    )
    node_grid = build_node_grid(
        nodes,
        grid,
        tile_geometries={"T": tile},
        province_geometries={"AB": left, "BC": right},
    )
    mapped = node_grid.node_grid >= 0
    assert mapped.any()
    assert (node_grid.node_grid[:, 20] < 0).all()  # left province is unallocated
    assert (node_grid.node_grid[:, 90] >= 0).all()  # BC col 2 is allocated
    assert (node_grid.node_grid[:, 130] >= 0).all()  # BC col 3 is allocated
    assert len(np.unique(node_grid.node_grid[mapped])) == 6
    assert node_grid.diagnostics["node_cells"] == int(mapped.sum())
    assert node_grid.diagnostics["relative_area_error"] < 0.01


def test_ws4_tables_have_complete_references_and_no_extra_tracks() -> None:
    scan = _synthetic_scan()
    specs = (_spec("a"), _spec("b"))
    assignment = assign_blocks(scan, _tiny_block_grid(), specs)
    frames = build_ws4_tables(assignment, specs)
    fragments = frames["fragments"]
    blocks = frames["blocks"]
    tracks = frames["tracks"]
    curves = frames["curves"]
    track_ids = set(tracks["track_id"])
    assert set(fragments["block_id"]) == set(blocks["block_id"])
    assert set(fragments["track_id"]) <= track_ids
    assert set(blocks["track_id"]) <= track_ids
    assert set(frames["strata"]["track_id"]) == track_ids
    assert set(frames["treatments"]["track_id"]) == track_ids
    assert set(frames["treatments"]["new_track"]) <= track_ids
    assert len(frames["features"]) == len(tracks)
    assert len(frames["products"]) == 4 * len(tracks)
    assert len(frames["groups"]) == len(blocks)
    assert frames["groups"]["block_id"].tolist() == blocks["block_id"].tolist()
    assert set(frames["features"]["curve_id"]) <= set(curves["curve_id"])
    assert set(frames["products"]["curve_id"]) <= set(curves["curve_id"])
    for curve_id, group in curves.groupby("curve_id", sort=True):
        assert group["x"].is_monotonic_increasing
        assert not group["x"].duplicated().any()
        assert group["x"].iloc[0] == 0.0
        assert group["x"].iloc[-1] == 600.0
        assert len(group) == len(CURVE_DOMAIN)
        if curve_id != AREA_CURVE_ID:
            assert group.loc[group["x"] == 0.0, "y"].iloc[0] == 0.0
    assert set(
        curves.loc[curves["curve_id"] == AREA_CURVE_ID, "y"].unique()
    ) == {1.0}
    natural_thin = frames["treatments"][
        (frames["treatments"]["track_id"] == 1)
        & (frames["treatments"]["treatment"] == "THIN")
    ].iloc[0]
    natural_cc = frames["treatments"][
        (frames["treatments"]["track_id"] == 1)
        & (frames["treatments"]["treatment"] == "CC")
    ].iloc[0]
    thinned_id = int(natural_thin["new_track"])
    thinned_thin = frames["treatments"][
        (frames["treatments"]["track_id"] == thinned_id)
        & (frames["treatments"]["treatment"] == "THIN")
    ].iloc[0]
    thinned_cc = frames["treatments"][
        (frames["treatments"]["track_id"] == thinned_id)
        & (frames["treatments"]["treatment"] == "CC")
    ].iloc[0]
    assert natural_thin["offset"] == -1
    assert natural_thin["begin"] == 20
    assert natural_thin["end"] == 40
    assert thinned_thin["new_track"] == thinned_id
    assert thinned_thin["offset"] == -1
    assert thinned_cc["new_track"] == 2
    assert thinned_cc["offset"] == 0
    assert natural_cc["new_track"] == 2
    track_names = tracks.set_index("track_id")["name"]
    assert "+SILV_STATE(thinned)" in track_names[thinned_id]
    assert "+SILV_STATE(baseline)" in track_names[1]
    assert "+SILV_STATE(cc_pl)" in track_names[2]
    feature_labels = set(frames["features"]["label"].astype(str))
    assert any(label.endswith(".unmanaged.Total") for label in feature_labels)
    assert any(label.endswith(".thinned.Total") for label in feature_labels)
    assert any(label.endswith(".managed.Total") for label in feature_labels)
    product_labels = set(frames["products"]["label"].astype(str))
    for state in ("unmanaged", "thinned", "managed"):
        for treatment in ("THIN", "CC"):
            assert any(
                label.endswith(f".{state}.{treatment}")
                for label in product_labels
            )
    for name in ("tracks", "strata", "treatments", "features", "products",
                 "accounts", "protoaccounts"):
        assert not frames[name].isna().any(axis=None)
    assert set(frames["treatments"]["treatment"]) == {"THIN", "CC"}
    assert (frames["treatments"]["retain"] == 0).all()
    assert (frames["strata"]["next_age"] == 300).all()
    assert (frames["strata"]["cycle"] == 0).all()
    for name in ("accounts", "protoaccounts"):
        accounts = frames[name]
        labels = set(frames["features"]["label"]) | set(frames["products"]["label"])
        assert set(accounts["attribute"]) == labels
        assert set(accounts["account"]) == labels
        assert set(accounts["group"]) == {"_ALL_"}
        assert (accounts["sum"] == 1.0).all()
    assert len(tracks) == 3 * len(np.unique(
        assignment.block_province.astype(np.int64) * len(specs)
        + assignment.block_curve_index
    ))


def test_schedule_is_eligible_and_province_stratified() -> None:
    blocks = pd.DataFrame(
        {
            "block_id": np.arange(1, 121, dtype=np.int64),
            "track_id": [1] * 120,
            "offset": np.tile([30, 40, 50, 60, 70], 24),
            "area_ha": [10.0] * 120,
        }
    )
    province = np.array([0] * 40 + [1] * 40 + [2] * 40)
    schedule, stats = build_schedule(
        blocks, province, sample_fraction=0.1, max_blocks=1000
    )
    merged = schedule.merge(
        blocks, on="block_id", suffixes=("_schedule", "_block")
    )
    assert (merged["offset_block"] >= 40).all()
    assert stats["rows"] == len(schedule)
    assert stats["by_province"] == {"AB": 3, "BC": 3, "SK": 3}
    assert schedule["treatment"].eq("CC").all()
    assert schedule["offset"].eq(0).all()


@pytest.mark.slow
def test_national_ws4_validate_and_simulate_smoke(tmp_path: Path) -> None:
    if not WS4_BIN.is_file():
        pytest.skip("ws4 executable is not available")
    scan = _synthetic_scan()
    specs = (_spec("a"), _spec("b"))
    assignment = assign_blocks(scan, _tiny_block_grid(), specs)
    frames = build_ws4_tables(assignment, specs)
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    for name, columns in _table_schema().items():
        assert list(frames[name].columns) == columns
        frames[name].to_parquet(dataset / f"{name}.parquet", index=False)
    manifest = {
        "schema_version": "0.1",
        "tables": {name: f"{name}.parquet" for name in _table_schema()},
        "sources": [],
        "options": {"period_length": 1, "recipe": "test", "model": "II"},
        "metadata": {},
    }
    (dataset / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert run_ws4_validate(dataset)["exit_code"] == 0
    schedule = pd.DataFrame(
        {
            "block_id": assignment.blocks["block_id"],
            "treatment": ["CC"] * len(assignment.blocks),
            "offset": [0] * len(assignment.blocks),
        }
    )
    schedule_path = tmp_path / "schedule.csv"
    schedule.to_csv(schedule_path, index=False)
    run_dir = tmp_path / "run"
    payload = run_national_simulation(
        dataset, schedule_path, run_dir, years=5
    )
    assert payload["exit_code"] == 0
    assert payload["harvested_volume_m3"] > 0.0
    assert payload["treated_area_ha"] > 0.0
    assert payload["treated_area_ha"] == pytest.approx(
        assignment.blocks["area_ha"].sum()
    )


@pytest.mark.slow
def test_committed_national_dataset_contract_if_present() -> None:
    dataset = (
        Path(__file__).resolve().parents[1]
        / "data/derived/ws4/scanfi-national-990m/dataset"
    )
    if not dataset.is_dir():
        pytest.skip("national dataset has not been built")
    manifest = json.loads((dataset / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "0.1"
    metadata = manifest["metadata"]
    assert metadata["inventory_resolution"] == "30m"
    assert metadata["ws4_fragment_resolution"] == "990m"
    assert metadata["resolution_change"]
    summary = json.loads(
        (dataset.parent / "inventory_summary.json").read_text(encoding="utf-8")
    )
    assert summary["counts"]["fragments"] == metadata["fragments"]
    assert summary["counts"]["blocks"] == metadata["blocks"]
    fragments = pd.read_parquet(dataset / "fragments.parquet")
    blocks = pd.read_parquet(dataset / "blocks.parquet")
    groups = pd.read_parquet(dataset / "groups.parquet")
    tracks = pd.read_parquet(dataset / "tracks.parquet")
    treatments = pd.read_parquet(dataset / "treatments.parquet")
    features = pd.read_parquet(dataset / "features.parquet")
    products = pd.read_parquet(dataset / "products.parquet")
    accounts = pd.read_parquet(dataset / "accounts.parquet")
    assert len(fragments) == metadata["fragments"]
    assert len(blocks) == metadata["blocks"]
    assert fragments["area_ha"].sum() == pytest.approx(blocks["area_ha"].sum())
    assert set(fragments["block_id"]) == set(blocks["block_id"])
    assert len(groups) == len(blocks)
    assert groups["block_id"].tolist() == blocks["block_id"].tolist()
    assert fragments["age"].min() > 0
    assert fragments["area_ha"].max() <= WS4_FRAGMENT_AREA_HA + 1e-9
    assert len(tracks) == summary["counts"]["tracks"]
    assert tracks["name"].str.contains(r"SILV_STATE\(thinned\)").sum() == (
        summary["counts"]["province_curve_pairs"]
    )
    thin = treatments[treatments["treatment"].eq("THIN")]
    assert len(thin) == 3 * summary["counts"]["province_curve_pairs"]
    assert thin["offset"].eq(-1).all()
    assert thin["begin"].eq(20).all() and thin["end"].eq(40).all()
    feature_labels = features["label"].astype(str)
    assert feature_labels.str.endswith(".unmanaged.Total").any()
    assert feature_labels.str.endswith(".thinned.Total").any()
    assert feature_labels.str.endswith(".managed.Total").any()
    labels = set(features["label"].astype(str)) | set(
        products["label"].astype(str)
    )
    assert set(accounts["account"].astype(str)) == labels
    assert accounts["sum"].eq(1.0).all()
    for frame in (tracks, treatments, features, products, accounts):
        assert not frame.isna().any(axis=None)
