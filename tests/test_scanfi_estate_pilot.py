"""Focused tests for the SCANFI/RSYC WS4 estate pilot."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from workflows.ws4_instances.build_scanfi_estate_pilot import (
    AREA_CURVE_ID,
    CURVE_DOMAIN,
    PIXEL_AREA_HA,
    WS4_BIN,
    CurveResolution,
    CurveSpec,
    ScanfiEstateError,
    _table_schema,
    build_inventory,
    build_schedule,
    build_ws4_tables,
    resolve_curves,
    select_pilot_node,
    subcell_polygon,
    write_dataset,
)


def _spec(key: str, model_id: str, *, b1: float = 100.0, b2: float = 0.03) -> CurveSpec:
    return CurveSpec(
        key=key,
        model_id=model_id,
        species="CONIFEROUS",
        species_kind="group",
        selected_level="tile",
        source_species="coniferous",
        b1=b1,
        b2=b2,
        b3=1.0,
        b4=0.001,
    )


def _resolution() -> CurveResolution:
    return CurveResolution(
        specs=(_spec("curve_a", "model_a"), _spec("curve_b", "model_b", b1=200.0)),
        species_spec_index={"blackSpruce": 0, "lodgepolePine": 1},
        landcover_spec_index={5: 1, 6: 0, 7: 1},
        diagnostics={},
    )


def _grid_inventory():
    age = np.array(
        [
            [45, 45, 0, 60, 60],
            [45, 45, 0, 60, 60],
            [50, 50, 0, 0, 0],
            [50, 50, 0, 0, 0],
        ],
        dtype=np.int16,
    )
    landcover = np.where(age > 0, 6, 3).astype(np.uint8)
    max_closure = np.where(age > 0, 10, 0).astype(np.uint8)
    species = np.zeros(age.shape, dtype=np.int8)
    # Separate the two 45-year cliques by species, and keep the 50-year pixels
    # as one 8-neighbour clique.
    species[0:2, 0:2] = 0
    species[2:4, 0:2] = 1
    species[0:2, 3:5] = 1
    return age, landcover, max_closure, species


def test_select_pilot_node_is_highest_bc_harvest() -> None:
    node = select_pilot_node()
    assert node.node_id == "G2|BC|S32"
    assert node.tile_id == "G2"
    assert node.row == 3
    assert node.col == 2
    assert node.harvest_m3 == pytest.approx(228408.176800181)
    assert node.subcell_area_ha == pytest.approx(224737.99803133)


def test_subcell_polygon_is_50km_square() -> None:
    from shapely.geometry import box

    tile = box(0.0, 0.0, 150_000.0, 150_000.0)
    polygon = subcell_polygon(tile, row=3, col=2)
    assert polygon.bounds == (50_000.0, 100_000.0, 100_000.0, 150_000.0)
    assert polygon.area / 10_000.0 == pytest.approx(250_000.0)


def test_resolve_curves_exact_group_generic_and_ambiguity() -> None:
    rows = [
        {
            "tile_id": "T1",
            "province": "BC",
            "species": "BROADLEAF",
            "species_kind": "group",
            "model_id": "generic_t1",
            "selected_level": "tile",
            "source_species": "generic",
            "b1": 100.0,
            "b2": 0.03,
            "b3": 1.0,
            "b4": 0.001,
        },
        {
            "tile_id": "T1",
            "province": "BC",
            "species": "CONIFEROUS",
            "species_kind": "group",
            "model_id": "coniferous_t1",
            "selected_level": "tile",
            "source_species": "coniferous",
            "b1": 150.0,
            "b2": 0.03,
            "b3": 1.0,
            "b4": 0.001,
        },
        {
            "tile_id": "T1",
            "province": "BC",
            "species": "PICE.MAR",
            "species_kind": "species",
            "model_id": "dup_model",
            "selected_level": "tile",
            "source_species": "PICE.MAR",
            "b1": 170.0,
            "b2": 0.03,
            "b3": 1.0,
            "b4": 0.001,
        },
        {
            "tile_id": "T1",
            "province": "BC",
            "species": "PINU.CON",
            "species_kind": "species",
            "model_id": "dup_model",
            "selected_level": "tile",
            "source_species": "PINU.CON",
            "b1": 180.0,
            "b2": 0.03,
            "b3": 1.0,
            "b4": 0.001,
        },
    ]
    resolution = resolve_curves(pd.DataFrame(rows), tile_id="T1", province="BC")
    assert resolution.diagnostics["ambiguous_model_ids"] == ["dup_model"]
    keys = {spec.key for spec in resolution.specs}
    assert any(key.startswith("dup_model#") for key in keys)
    assert (
        resolution.diagnostics["species_resolution"]["blackSpruce"]["resolution"]
        == "exact"
    )
    assert (
        resolution.diagnostics["species_resolution"]["balsamFir"]["resolution"]
        == "group"
    )
    assert (
        resolution.diagnostics["landcover_resolution"]["7"]["resolution"] == "generic"
    )


def test_inventory_area_block_and_clique_structure() -> None:
    age, landcover, max_closure, species = _grid_inventory()
    inventory = build_inventory(
        age=age,
        landcover=landcover,
        max_closure=max_closure,
        dominant_species=species,
        species_layers=["blackSpruce", "lodgepolePine"],
        resolution=_resolution(),
        tile_id="T1",
        inside_pixels=age.size,
        inside_mask=np.ones(age.shape, dtype=bool),
    )
    fragments = inventory.fragments
    blocks = inventory.blocks
    assert len(fragments) == 12
    assert fragments["area_ha"].sum() == pytest.approx(12 * PIXEL_AREA_HA)
    assert blocks["area_ha"].sum() == pytest.approx(12 * PIXEL_AREA_HA)
    assert set(fragments["block_id"]) == set(blocks["block_id"])
    assert set(fragments["track_id"]) <= set(blocks["track_id"])
    # 45-year species 0, 50-year species 1, and the separated 60-year
    # species 1 clique.
    assert inventory.counts["blocks"] == 3
    assert inventory.clique_summary["pixels_max"] == 4
    # Age class 40 for the 45-year pixels and 50/60 for the older cliques.
    offsets = dict(zip(blocks["block_id"], blocks["offset"], strict=True))
    assert sorted(offsets.values()) == [40, 50, 60]


def test_ws4_tables_and_curve_domain() -> None:
    age, landcover, max_closure, species = _grid_inventory()
    resolution = _resolution()
    inventory = build_inventory(
        age=age,
        landcover=landcover,
        max_closure=max_closure,
        dominant_species=species,
        species_layers=["blackSpruce", "lodgepolePine"],
        resolution=resolution,
        tile_id="T1",
        inside_pixels=age.size,
        inside_mask=np.ones(age.shape, dtype=bool),
    )
    frames = build_ws4_tables(inventory, tile_id="T1", resolution=resolution)

    curves = frames["curves"]
    for curve_id, group in curves.groupby("curve_id"):
        assert group["x"].is_monotonic_increasing
        assert not group["x"].duplicated().any()
        assert group["x"].iloc[0] == 0.0
        assert group["x"].iloc[-1] == 600.0
        assert len(group) == len(CURVE_DOMAIN)
        if curve_id != AREA_CURVE_ID:
            assert group.loc[group["x"] == 0.0, "y"].iloc[0] == 0.0

    assert set(frames["tracks"]["track_id"]) == {1, 2, 3, 4}
    assert set(frames["blocks"]["track_id"]) <= set(frames["tracks"]["track_id"])
    assert set(frames["fragments"]["block_id"]) == set(frames["blocks"]["block_id"])
    assert set(frames["fragments"]["track_id"]) <= set(frames["tracks"]["track_id"])
    assert set(frames["features"]["curve_id"]) <= set(curves["curve_id"])
    assert set(frames["products"]["curve_id"]) <= set(curves["curve_id"])

    natural = frames["treatments"][frames["treatments"]["track_id"] == 1].iloc[0]
    managed = frames["treatments"][frames["treatments"]["track_id"] == 2].iloc[0]
    assert natural["new_track"] == 2
    assert managed["new_track"] == 2
    assert natural["begin"] == managed["begin"] == 40
    assert set(frames["treatments"]["treatment"]) == {"CC"}

    for table_name in ("accounts", "protoaccounts"):
        accounts = frames[table_name]
        assert len(accounts) == 6
        assert set(accounts["group"]) == {"_ALL_"}
        assert set(accounts["sum"]) == {1.0}
        assert (accounts["attribute"] == accounts["account"]).all()
    assert frames["groups"]["group"].str.startswith("curve:").all()


def test_schedule_only_eligible_blocks() -> None:
    blocks = pd.DataFrame(
        {
            "block_id": [1, 2, 3, 4, 5],
            "track_id": [1, 1, 1, 1, 1],
            "offset": [0, 10, 40, 45, 50],
            "area_ha": [1.0, 1.0, 1.0, 1.0, 1.0],
        }
    )
    schedule = build_schedule(blocks)
    assert schedule["block_id"].tolist() == [3, 4, 5]
    assert set(schedule["treatment"]) == {"CC"}
    assert set(schedule["offset"]) == {0}


def test_missing_curve_fails_closed() -> None:
    resolution = CurveResolution(
        specs=(_spec("only_curve", "model_only"),),
        species_spec_index={},
        landcover_spec_index={},
        diagnostics={},
    )
    age = np.array([[45]], dtype=np.int16)
    landcover = np.array([[6]], dtype=np.uint8)
    with pytest.raises(ScanfiEstateError, match="no RSYC curve"):
        build_inventory(
            age=age,
            landcover=landcover,
            max_closure=np.array([[10]], dtype=np.uint8),
            dominant_species=np.array([[0]], dtype=np.int8),
            species_layers=["blackSpruce"],
            resolution=resolution,
            tile_id="T1",
            inside_pixels=1,
            inside_mask=np.ones(age.shape, dtype=bool),
        )


@pytest.mark.slow
def test_ws4_validate_and_simulate_smoke(tmp_path: Path) -> None:
    if not WS4_BIN.is_file():
        pytest.skip("ws4 executable is not available")
    age, landcover, max_closure, species = _grid_inventory()
    resolution = _resolution()
    inventory = build_inventory(
        age=age,
        landcover=landcover,
        max_closure=max_closure,
        dominant_species=species,
        species_layers=["blackSpruce", "lodgepolePine"],
        resolution=resolution,
        tile_id="T1",
        inside_pixels=age.size,
        inside_mask=np.ones(age.shape, dtype=bool),
    )
    frames = build_ws4_tables(inventory, tile_id="T1", resolution=resolution)
    dataset = tmp_path / "dataset"
    write_dataset(frames, dataset)
    manifest = {
        "schema_version": "0.1",
        "tables": {name: f"{name}.parquet" for name in _table_schema()},
        "sources": [],
        "options": {"period_length": 1, "recipe": "test", "model": "II"},
        "metadata": {},
    }
    (dataset / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    validation = subprocess.run(
        [str(WS4_BIN), "validate", "--dataset", str(dataset)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert validation.returncode == 0, validation.stdout + validation.stderr

    schedule = build_schedule(frames["blocks"])
    schedule_path = tmp_path / "schedule.csv"
    schedule.to_csv(schedule_path, index=False)
    run_dir = tmp_path / "run"
    simulation = subprocess.run(
        [
            str(WS4_BIN),
            "simulate",
            "--dataset",
            str(dataset),
            "--schedule",
            str(schedule_path),
            "--years",
            "5",
            "--out",
            str(run_dir),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert simulation.returncode == 0, simulation.stdout + simulation.stderr
    products = pd.read_csv(run_dir / "product_values.csv")
    harvested = products.loc[
        products["account"].str.startswith("product.HarvestedVolume"), "value"
    ].sum()
    treated = products.loc[
        products["account"].str.startswith("product.Treated"), "value"
    ].sum()
    assert harvested > 0.0
    assert treated > 0.0
