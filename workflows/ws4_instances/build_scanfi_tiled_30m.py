#!/usr/bin/env python3
"""Build tiled 30-m WS4 datasets for national 50-km supply nodes.

Each selected node becomes its own canonical WS4 dataset:

* the inventory window is the 50-km subcell intersected with its province
  (the committed ``subcell_geometry_area_ha``), so the 3,562 node datasets
  partition the national land base instead of double-counting multi-province
  subcells;
* every 30-m SCANFI forest pixel is one WS4 fragment;
* 8-neighbour cliques of equal ``(age_class, curve_key)`` pixels are blocks;
* the twelve canonical WS4 tables and a manifest are written per tile;
* ``ws4 validate`` runs on every built tile;
* a 10% schedule of eligible blocks (age class >= 40) at offset 0 is simulated
  for 20 years.

This module is the orchestrator for a representative sample: the highest-
harvest node in every province with positive harvest, plus every PEI node.
The full national run is 3,562 datasets and is deliberately not attempted here.

The build is fail-closed.  It refuses to write when either repository reports an
unexpected deletion, when a selected geometry does not match the committed
supply-node geometry, when a 30-m raster is not aligned, when a forest pixel has
no RSYC curve, or when an area/count invariant does not close.
"""

from __future__ import annotations

import argparse
import json
import math
import multiprocessing
import os
import shutil
import sys
import time
from collections import defaultdict
from collections.abc import Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from shapely.geometry import Polygon

from workflows.national.facilities.national_facility_inventory import PROVINCES
from workflows.ws4_instances.build_scanfi_estate_pilot import (
    AGE_RASTER,
    BOUNDARY_GEOJSON,
    LABELS,
    LANDCOVER_RASTER,
    PIXEL_AREA_HA,
    REPO_ROOT,
    RSYC_LIBRARY_CSV,
    RSYC_TILES_GPKG,
    SPECIES_TO_CODES,
    SUPPLY_NODES_CSV,
    TREATMENT_BEGIN,
    WS4_OUT_ROOT,
    PilotNode,
    ScanfiEstateError,
    _repo_path,
    assert_no_unexpected_deletions,
    build_inventory,
    build_schedule,
    build_ws4_tables,
    discover_species_layers,
    load_scanfi_window,
    load_tile_geometry,
    province_intersection_polygon,
    resolve_curves,
    run_ws4_simulate,
    run_ws4_validate,
    sha256_file,
    subcell_polygon,
    write_dataset,
    write_manifest,
    ws4_provenance,
)

TILED_DIR = WS4_OUT_ROOT / "tiled-30m"
NATIONAL_MANIFEST_PATH = TILED_DIR / "national_tiled_manifest.json"
PROVENANCE_CACHE_PATH = REPO_ROOT / "tmp/ws4_tiled_30m_provenance_cache.json"
NATIONAL_990M_SUMMARY = WS4_OUT_ROOT / "scanfi-national-990m" / "inventory_summary.json"

TILE_RESOLUTION_M = 30_000.0
INVENTORY_RESOLUTION_M = 30.0
RECTANGLE_AREA_HA = 250_000.0
AGE_CLASS_WIDTH = 10
TREATMENT = "CC"
TREATMENT_END = 1000
CONNECTIVITY = "8-neighbour"
DEFAULT_SAMPLE_FRACTION = 0.1
DEFAULT_YEARS = 20
DEFAULT_WORKERS = min(8, os.cpu_count() or 1)
PROVINCE_ORDER = tuple(PROVINCES)

TILED_LABELS = {
    **LABELS,
    "tiled_30m": True,
    "tile_resolution_m": TILE_RESOLUTION_M,
    "inventory_resolution": "30m",
    "inventory_resolution_m": INVENTORY_RESOLUTION_M,
}

FULL_RUN_COMMAND = (
    ".venv/bin/python -m workflows.ws4_instances.build_scanfi_tiled_30m "
    "--all --workers 32 --years 20 --sample-fraction 0.1"
)


# ---------------------------------------------------------------------------
# Candidate table and selection.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TileNode:
    node_id: str
    parent_node_id: str
    tile_id: str
    province: str
    row: int
    col: int
    lat: float
    lon: float
    harvest_m3: float
    subcell_area_ha: float
    subcell_geometry_area_ha: float

    def as_pilot_node(self) -> PilotNode:
        return PilotNode(
            node_id=self.node_id,
            parent_node_id=self.parent_node_id,
            tile_id=self.tile_id,
            province=self.province,
            row=self.row,
            col=self.col,
            lat=self.lat,
            lon=self.lon,
            harvest_m3=self.harvest_m3,
            subcell_area_ha=self.subcell_area_ha,
        )


def tile_slug(node_id: str) -> str:
    slug = node_id.replace("|", "-")
    if not slug or "/" in slug or "\\" in slug or slug in {".", ".."}:
        raise ScanfiEstateError(f"unsafe tile slug derived from {node_id!r}")
    return slug


def load_candidates(path: Path = SUPPLY_NODES_CSV) -> pd.DataFrame:
    """Load the committed 50-km supply nodes and validate every identity."""
    if not path.is_file():
        raise ScanfiEstateError(f"supply-node table is missing: {path}")
    frame = pd.read_csv(path)
    required = {
        "node_id",
        "parent_node_id",
        "tile_id",
        "province",
        "row",
        "col",
        "lat",
        "lon",
        "harvest_m3",
        "subcell_area_ha",
        "subcell_geometry_area_ha",
    }
    missing = required - set(frame.columns)
    if missing:
        raise ScanfiEstateError(f"{path}: missing columns {sorted(missing)}")
    if not frame["node_id"].is_unique:
        raise ScanfiEstateError(f"{path}: duplicate node_id values")
    frame = frame.copy()
    for column in ("row", "col"):
        frame[column] = pd.to_numeric(frame[column], errors="raise").astype(int)
    for column in (
        "lat",
        "lon",
        "harvest_m3",
        "subcell_area_ha",
        "subcell_geometry_area_ha",
    ):
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    for row in frame.itertuples(index=False):
        expected = f"{row.tile_id}|{row.province}|S{row.row}{row.col}"
        if str(row.node_id) != expected:
            raise ScanfiEstateError(
                f"{path}: node_id {row.node_id!r} does not match {expected!r}"
            )
        if int(row.row) not in (1, 2, 3) or int(row.col) not in (1, 2, 3):
            raise ScanfiEstateError(
                f"{path}: row/col outside 3x3 grid for {row.node_id!r}"
            )
        if str(row.province) not in PROVINCES:
            raise ScanfiEstateError(
                f"{path}: unknown province {row.province!r} in {row.node_id!r}"
            )
        for field in (
            "lat",
            "lon",
            "harvest_m3",
            "subcell_area_ha",
            "subcell_geometry_area_ha",
        ):
            value = float(getattr(row, field))
            if not math.isfinite(value):
                raise ScanfiEstateError(
                    f"{path}: non-finite {field} in {row.node_id!r}"
                )
        if float(row.harvest_m3) < 0.0:
            raise ScanfiEstateError(
                f"{path}: negative harvest for {row.node_id!r}"
            )
        if float(row.subcell_geometry_area_ha) <= 0.0:
            raise ScanfiEstateError(
                f"{path}: non-positive geometry area for {row.node_id!r}"
            )
        if float(row.subcell_area_ha) <= 0.0:
            raise ScanfiEstateError(
                f"{path}: non-positive productive area for {row.node_id!r}"
            )
    return frame


def node_from_candidate(row: Any) -> TileNode:
    return TileNode(
        node_id=str(row.node_id),
        parent_node_id=str(row.parent_node_id),
        tile_id=str(row.tile_id),
        province=str(row.province),
        row=int(row.row),
        col=int(row.col),
        lat=float(row.lat),
        lon=float(row.lon),
        harvest_m3=float(row.harvest_m3),
        subcell_area_ha=float(row.subcell_area_ha),
        subcell_geometry_area_ha=float(row.subcell_geometry_area_ha),
    )


def select_representative_tiles(
    frame: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Select one highest-harvest node per positive province plus all PE nodes.

    The committed PEI grid contains 13 50-km subnodes across six unique RSYC
    parent tiles.  All 13 are selected so the small-province example covers the
    whole committed PEI node set; the manifest reports both counts.
    """
    positive = frame[frame["harvest_m3"] > 0.0].copy()
    if positive.empty:
        raise ScanfiEstateError("no positive-harvest supply nodes")
    totals = (
        positive.sort_values(
            ["province", "harvest_m3", "node_id"],
            ascending=[True, False, True],
        )
        .groupby("province", sort=True)
        .head(1)
    )
    pei = frame[frame["province"].astype(str) == "PE"].copy()
    if pei.empty:
        raise ScanfiEstateError("no PE supply nodes")
    reasons: dict[str, list[str]] = defaultdict(list)
    for node_id in totals["node_id"].astype(str):
        reasons[node_id].append("province_top")
    for node_id in pei["node_id"].astype(str):
        reasons[node_id].append("pei_complete")
    selected = frame[frame["node_id"].astype(str).isin(reasons)].copy()
    selected["selection_reasons"] = selected["node_id"].map(
        lambda value: reasons[str(value)]
    )
    selected = selected.sort_values(
        ["province", "harvest_m3", "node_id"],
        ascending=[True, False, True],
    ).reset_index(drop=True)
    if len(totals) != 12:
        raise ScanfiEstateError(
            f"expected 12 positive-harvest provinces, found {len(totals)}"
        )
    if "NU" in set(totals["province"].astype(str)):
        raise ScanfiEstateError("NU has no positive harvest and cannot be a top tile")
    selected_ids = set(selected["node_id"].astype(str))
    if set(pei["node_id"].astype(str)) - selected_ids:
        raise ScanfiEstateError("not every PE node was selected")
    pei_tiles = sorted(set(pei["tile_id"].astype(str)))
    diagnostics = {
        "positive_harvest_provinces": len(totals),
        "province_top_nodes": len(totals),
        "pei_nodes": len(pei),
        "pei_unique_parent_tiles": len(pei_tiles),
        "pei_parent_tile_ids": pei_tiles,
        "selected_nodes": len(selected),
        "zero_harvest_provinces": sorted(
            set(frame["province"].astype(str))
            - set(positive["province"].astype(str))
        ),
        "selected_rectangles": len(selected[["tile_id", "row", "col"]].drop_duplicates()),
    }
    return selected, diagnostics


# ---------------------------------------------------------------------------
# Geometry and safe output handling.
# ---------------------------------------------------------------------------


def node_geometries(node: TileNode) -> tuple[Polygon, Any]:
    """Return the full 50-km rectangle and the province-clipped node polygon."""
    tile_geometry = load_tile_geometry(node.tile_id)
    rectangle = subcell_polygon(tile_geometry, node.row, node.col)
    if not math.isclose(
        rectangle.area / 10_000.0, RECTANGLE_AREA_HA, abs_tol=1e-3
    ):
        raise ScanfiEstateError(
            f"{node.node_id}: reconstructed subcell is not 50 km x 50 km"
        )
    clipped = province_intersection_polygon(rectangle, node.province)
    area_ha = float(clipped.area / 10_000.0)
    tolerance_ha = max(0.01, 1e-6 * node.subcell_geometry_area_ha)
    if not math.isclose(
        area_ha, node.subcell_geometry_area_ha, abs_tol=tolerance_ha
    ):
        raise ScanfiEstateError(
            f"{node.node_id}: clipped geometry {area_ha:.6f} ha does not match "
            f"committed subcell_geometry_area_ha "
            f"{node.subcell_geometry_area_ha:.6f} ha"
        )
    return rectangle, clipped


def _safe_reset_tiled_output(out_dir: Path, root: Path = TILED_DIR) -> None:
    if not out_dir.exists():
        return
    resolved = out_dir.resolve()
    root_resolved = root.resolve()
    if resolved.parent != root_resolved or resolved.name in {"", ".", ".."}:
        raise ScanfiEstateError(
            f"refusing to reset unexpected output directory: {resolved}"
        )
    if out_dir.is_symlink():
        raise ScanfiEstateError(f"refusing to reset symlink output: {out_dir}")
    shutil.rmtree(resolved)


def _write_gitattributes(out_dir: Path) -> None:
    content = (
        "# Dataset tables are binary payloads; annex them explicitly.\n"
        "dataset/*.parquet annex.largefiles=anything\n"
        "\n"
        "# Large run outputs are text but should not bloat Git history.\n"
        "schedules/*.csv annex.largefiles=largerthan=1MB\n"
        "runs/*/*.csv annex.largefiles=largerthan=1MB\n"
    )
    out_dir.joinpath(".gitattributes").write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Provenance.
# ---------------------------------------------------------------------------


def provenance_source_paths() -> list[Path]:
    species = discover_species_layers()
    return [
        AGE_RASTER,
        LANDCOVER_RASTER,
        *[species[name] for name in SPECIES_TO_CODES],
        RSYC_LIBRARY_CSV,
        RSYC_TILES_GPKG,
        SUPPLY_NODES_CSV,
        BOUNDARY_GEOJSON,
    ]


def _load_provenance_cache(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


def build_provenance_records(
    paths: Sequence[Path], cache_path: Path = PROVENANCE_CACHE_PATH
) -> list[dict[str, Any]]:
    """Hash immutable inputs once; reuse cached hashes when size/mtime match."""
    cache = _load_provenance_cache(cache_path)
    records: list[dict[str, Any]] = []
    changed = False
    for path in paths:
        if not path.is_file():
            raise ScanfiEstateError(f"source file is missing: {path}")
        stat = path.stat()
        key = str(path.resolve())
        entry = cache.get(key)
        if (
            isinstance(entry, dict)
            and entry.get("size_bytes") == stat.st_size
            and entry.get("mtime_ns") == stat.st_mtime_ns
            and isinstance(entry.get("sha256"), str)
        ):
            digest = entry["sha256"]
        else:
            digest = sha256_file(path)
            cache[key] = {
                "path": _repo_path(path),
                "size_bytes": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
                "sha256": digest,
            }
            changed = True
        records.append(
            {
                "path": _repo_path(path),
                "sha256": digest,
                "size_bytes": int(stat.st_size),
            }
        )
    if changed:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = cache_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(cache, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        temporary.replace(cache_path)
    return records


# ---------------------------------------------------------------------------
# One tile.
# ---------------------------------------------------------------------------


def _storage_breakdown(out_dir: Path) -> dict[str, int]:
    def total(directory: Path) -> int:
        if not directory.exists():
            return 0
        return int(
            sum(
                path.stat().st_size
                for path in directory.rglob("*")
                if path.is_file()
            )
        )

    dataset = total(out_dir / "dataset")
    schedules = total(out_dir / "schedules")
    runs = total(out_dir / "runs")
    return {
        "dataset": dataset,
        "schedules": schedules,
        "runs": runs,
        "total": dataset + schedules + runs,
    }


def _summary_node(node: TileNode) -> dict[str, Any]:
    payload = asdict(node)
    payload["tile_slug"] = tile_slug(node.node_id)
    return payload


def _write_tile_readme(out_dir: Path, summary: Mapping[str, Any]) -> None:
    node = summary["node"]
    counts = summary["counts"]
    schedule = summary["schedule"]
    simulation = summary.get("simulation", {})
    storage = summary["storage_bytes"]
    lines = [
        f"# WS4 tiled 30-m dataset — {node['node_id']}",
        "",
        "This is a real compiled WS4 dataset built from 30-m SCANFI pixels for",
        "one 50-km supply node. It is a `model_estimate` / `sensitivity_only`",
        "artifact, not official OSeCAN and not a chain v5.1 change.",
        "",
        "## Identity",
        "",
        f"- Node: `{node['node_id']}` (parent `{node['parent_node_id']}`)",
        f"- Tile / row / col: `{node['tile_id']}` / {node['row']} / {node['col']}",
        f"- Province: `{node['province']}`",
        f"- Centroid: {node['lat']:.6f}, {node['lon']:.6f}",
        f"- Allocated harvest: {node['harvest_m3']:.6f} m3",
        f"- Productive subcell area: {node['subcell_area_ha']:.6f} ha",
        f"- Committed geometry area: {node['subcell_geometry_area_ha']:.6f} ha",
        f"- Clipped inventory geometry: {summary['geometry']['geometry_area_ha']:.6f} ha",
        "",
        "## Inventory",
        "",
        "- Inventory resolution: 30 m (0.09 ha per fragment)",
        "- Tile resolution: 30,000 m",
        "- Connectivity: 8-neighbour cliques of equal `(age_class, curve_key)`",
        f"- Forest fragments: {counts['forest_fragments']}",
        f"- Blocks: {counts['blocks']}",
        f"- Forest area: {counts['forest_area_ha']:.2f} ha",
        "",
        "## Validation and simulation",
        "",
        f"- `ws4 validate`: {summary.get('validation', {}).get('status', 'not_run')}",
        f"- Schedule rows: {schedule['rows']}",
        f"- Scheduled area: {schedule['scheduled_area_ha']:.2f} ha",
        f"- Eligible blocks (age class >= 40): {schedule['eligible_blocks']}",
    ]
    if simulation.get("status") == "pass":
        lines.extend(
            [
                f"- Simulated years: {simulation['years']}",
                f"- Harvested volume: {simulation['harvested_volume_m3']:.3f} m3",
                f"- Treated/managed area: {simulation['treated_area_ha']:.3f} ha",
            ]
        )
    else:
        lines.append(
            f"- Simulation: {simulation.get('status', 'not_run')}"
            f" ({simulation.get('reason', 'not run')})"
        )
    lines.extend(
        [
            "",
            "## Storage",
            "",
            f"- Dataset: {storage['dataset'] / 1e6:.2f} MB",
            f"- Schedules: {storage['schedules'] / 1e6:.2f} MB",
            f"- Runs: {storage['runs'] / 1e6:.2f} MB",
            f"- Total: {storage['total'] / 1e6:.2f} MB",
            "",
            "The managed track shares the natural RSYC curve: there is no",
            "planting yield-response data in this build. No operability, tenure,",
            "road, mill, or policy claim is made.",
            "",
            "See `inventory_summary.json` for exclusions, curve diagnostics,",
            "clique sizes, validation output, and simulation totals.",
            "",
        ]
    )
    out_dir.joinpath("README.md").write_text("\n".join(lines), encoding="utf-8")


def build_tile(
    node_payload: Mapping[str, Any],
    *,
    out_root: Path,
    provenance_records: Sequence[Mapping[str, Any]],
    sample_fraction: float = DEFAULT_SAMPLE_FRACTION,
    years: int = DEFAULT_YEARS,
    run_ws4: bool = True,
    verbose: bool = False,
) -> dict[str, Any]:
    """Build, validate, and simulate one tile.  Fail-closed on any invariant."""
    started = time.perf_counter()
    node = TileNode(**node_payload)
    rectangle, polygon = node_geometries(node)
    geometry_area_ha = float(polygon.area / 10_000.0)
    out_dir = out_root / tile_slug(node.node_id)

    library = pd.read_csv(RSYC_LIBRARY_CSV)
    resolution = resolve_curves(
        library, tile_id=node.tile_id, province=node.province
    )
    (
        age,
        landcover,
        max_closure,
        dominant_species,
        window,
        _species_paths,
    ) = load_scanfi_window(polygon)
    inventory = build_inventory(
        age=age,
        landcover=landcover,
        max_closure=max_closure,
        dominant_species=dominant_species,
        species_layers=list(SPECIES_TO_CODES),
        resolution=resolution,
        tile_id=node.tile_id,
        inside_pixels=window.inside_pixels,
        inside_mask=window.inside_mask,
    )
    frames = build_ws4_tables(
        inventory, tile_id=node.tile_id, resolution=resolution
    )
    if len(frames["fragments"]) != inventory.counts["forest_fragments"]:
        raise ScanfiEstateError(
            f"{node.node_id}: fragment table and count disagree"
        )
    if len(frames["blocks"]) != inventory.counts["blocks"]:
        raise ScanfiEstateError(f"{node.node_id}: block table and count disagree")
    if not math.isclose(
        float(frames["fragments"]["area_ha"].sum()),
        float(frames["blocks"]["area_ha"].sum()),
        rel_tol=1e-12,
        abs_tol=1e-6,
    ):
        raise ScanfiEstateError(f"{node.node_id}: fragment/block area mismatch")

    _safe_reset_tiled_output(out_dir, out_root)
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_gitattributes(out_dir)
    dataset_dir = out_dir / "dataset"
    write_dataset(frames, dataset_dir)
    manifest_sources = [
        {"path": record["path"], "sha256": record["sha256"]}
        for record in provenance_records
    ]
    manifest = write_manifest(
        dataset_dir,
        node=node,  # type: ignore[arg-type]
        inventory=inventory,
        resolution=resolution,
        source_records=manifest_sources,
        labels=TILED_LABELS,
        recipe="SCANFI_RSYC_tiled_30m",
        pilot_name="ws4-national-tiled-30m-v1",
        extra_metadata={
            "tile_dataset": "one_50km_supply_node",
            "province_geometry_clipped": True,
            "rectangle_area_ha": RECTANGLE_AREA_HA,
            "geometry_area_ha": geometry_area_ha,
            "subcell_geometry_area_ha": node.subcell_geometry_area_ha,
        },
    )

    schedule_path: Path | None = None
    schedule: pd.DataFrame | None = None
    scheduled_area = 0.0
    eligible_blocks = int(
        np.count_nonzero(frames["blocks"]["offset"] >= TREATMENT_BEGIN)
    )
    if eligible_blocks > 0:
        schedule = build_schedule(
            frames["blocks"], sample_fraction=sample_fraction
        )
        schedules_dir = out_dir / "schedules"
        schedules_dir.mkdir(parents=True, exist_ok=True)
        schedule_path = schedules_dir / "cc_10pct.csv"
        schedule.to_csv(schedule_path, index=False, lineterminator="\n")
        scheduled_blocks = frames["blocks"].merge(
            schedule[["block_id"]], on="block_id", how="inner"
        )
        scheduled_area = float(scheduled_blocks["area_ha"].sum())
        if len(schedule) == 0 or scheduled_area <= 0.0:
            raise ScanfiEstateError(f"{node.node_id}: empty schedule")
    else:
        (out_dir / "schedules").mkdir(parents=True, exist_ok=True)

    summary: dict[str, Any] = {
        "artifact": "ws4_tiled_30m_tile_v1",
        "built_utc": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "node": _summary_node(node),
        "geometry": {
            "rectangle_bounds_epsg3978": list(rectangle.bounds),
            "rectangle_area_ha": float(rectangle.area / 10_000.0),
            "geometry_area_ha": geometry_area_ha,
            "committed_subcell_geometry_area_ha": node.subcell_geometry_area_ha,
            "raster_crs": str(window.crs),
            "raster_window": {
                "col_off": int(window.window.col_off),
                "row_off": int(window.window.row_off),
                "width": int(window.window.width),
                "height": int(window.window.height),
            },
            "rasterized_pixels_inside_polygon": window.inside_pixels,
        },
        "counts": inventory.counts,
        "exclusions": inventory.exclusions,
        "age_distribution": list(inventory.age_distribution),
        "curve_usage": list(inventory.curve_usage),
        "curves": manifest["metadata"]["curve_specs"],
        "curve_diagnostics": resolution.diagnostics,
        "clique_summary": inventory.clique_summary,
        "tracks": frames["tracks"].to_dict(orient="records"),
        "treatments": frames["treatments"].to_dict(orient="records"),
        "schedule": {
            "path": _repo_path(schedule_path) if schedule_path else None,
            "rows": len(schedule) if schedule is not None else 0,
            "scheduled_area_ha": scheduled_area,
            "eligible_blocks": eligible_blocks,
            "sample_fraction": sample_fraction,
        },
        "labels": TILED_LABELS,
        "tiled_30m": True,
        "tile_resolution_m": TILE_RESOLUTION_M,
        "inventory_resolution": "30m",
        "inventory_resolution_m": INVENTORY_RESOLUTION_M,
        "fragment_count": len(frames["fragments"]),
        "block_count": len(frames["blocks"]),
        "ws4": ws4_provenance(),
    }

    if run_ws4:
        validation = run_ws4_validate(dataset_dir)
        summary["validation"] = {
            "status": "pass",
            "exit_code": int(validation["exit_code"]),
            "stdout": validation["stdout"].strip(),
            "stderr": validation["stderr"].strip(),
            "command": validation["command"],
        }
    else:
        summary["validation"] = {
            "status": "not_run",
            "reason": "run_ws4=false",
        }

    if run_ws4 and schedule_path is not None:
        run_dir = out_dir / "runs" / f"cc_{years}y"
        simulation = run_ws4_simulate(
            dataset_dir, schedule_path, run_dir, years=years
        )
        summary["simulation"] = {
            "status": "pass",
            "years": int(years),
            "run_dir": _repo_path(run_dir),
            "harvested_volume_m3": float(simulation["harvested_volume_m3"]),
            "treated_area_ha": float(simulation["treated_area_ha"]),
            "scheduled_area_ha": scheduled_area,
            "product_totals": simulation.get("product_totals", []),
            "final_managed_yield_m3": float(
                simulation.get("final_managed_yield_m3", 0.0)
            ),
            "final_unmanaged_yield_m3": float(
                simulation.get("final_unmanaged_yield_m3", 0.0)
            ),
        }
        if not math.isclose(
            float(simulation["treated_area_ha"]),
            scheduled_area,
            rel_tol=1e-9,
            abs_tol=1e-6,
        ):
            raise ScanfiEstateError(
                f"{node.node_id}: simulated treated area does not equal "
                f"scheduled block area: {simulation['treated_area_ha']} != "
                f"{scheduled_area}"
            )
    elif run_ws4:
        summary["simulation"] = {
            "status": "not_run_no_eligible_blocks",
            "reason": "no block has age class >= 40",
            "years": int(years),
        }
    else:
        summary["simulation"] = {
            "status": "not_run",
            "reason": "run_ws4=false",
        }

    summary["storage_bytes"] = _storage_breakdown(out_dir)
    summary["wall_seconds"] = round(time.perf_counter() - started, 3)
    out_dir.joinpath("inventory_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    _write_tile_readme(out_dir, summary)
    if verbose:
        print(
            f"built {node.node_id}: {summary['fragment_count']} fragments, "
            f"{summary['block_count']} blocks, "
            f"{summary['counts']['forest_area_ha']:.1f} ha, "
            f"validate={summary['validation']['status']}, "
            f"simulation={summary['simulation']['status']}, "
            f"{summary['wall_seconds']:.1f}s",
            flush=True,
        )
    return summary


def _build_tile_worker(
    node_payload: Mapping[str, Any],
    out_root_text: str,
    provenance_records: Sequence[Mapping[str, Any]],
    sample_fraction: float,
    years: int,
    run_ws4: bool,
) -> tuple[str, dict[str, Any] | None, str | None]:
    """Process-pool entry point; never raises across the process boundary."""
    node_id = str(node_payload["node_id"])
    try:
        summary = build_tile(
            node_payload,
            out_root=Path(out_root_text),
            provenance_records=provenance_records,
            sample_fraction=sample_fraction,
            years=years,
            run_ws4=run_ws4,
            verbose=True,
        )
        return node_id, summary, None
    except Exception as error:  # noqa: BLE001 - reported, then fail-closed
        return node_id, None, f"{type(error).__name__}: {error}"


# ---------------------------------------------------------------------------
# National manifest.
# ---------------------------------------------------------------------------


def _selected_manifest_row(summary: Mapping[str, Any]) -> dict[str, Any]:
    node = summary["node"]
    counts = summary["counts"]
    schedule = summary["schedule"]
    simulation = summary.get("simulation", {})
    validation = summary.get("validation", {})
    storage = summary["storage_bytes"]
    return {
        "node_id": node["node_id"],
        "tile_slug": node["tile_slug"],
        "province": node["province"],
        "tile_id": node["tile_id"],
        "row": node["row"],
        "col": node["col"],
        "harvest_m3": node["harvest_m3"],
        "subcell_area_ha": node["subcell_area_ha"],
        "subcell_geometry_area_ha": node["subcell_geometry_area_ha"],
        "geometry_area_ha": summary["geometry"]["geometry_area_ha"],
        "dataset_path": (
            f"{_repo_path(TILED_DIR / node['tile_slug'])}/dataset".replace(
                "//", "/"
            )
        ),
        "manifest_path": (
            f"{_repo_path(TILED_DIR / node['tile_slug'])}/dataset/manifest.json".replace(
                "//", "/"
            )
        ),
        "forest_fragments": counts["forest_fragments"],
        "blocks": counts["blocks"],
        "forest_area_ha": counts["forest_area_ha"],
        "curves_used": len(summary.get("curves", [])),
        "tracks": len(summary.get("tracks", [])),
        "validation_status": validation.get("status", "unknown"),
        "validation_exit_code": validation.get("exit_code"),
        "simulation_status": simulation.get("status", "unknown"),
        "simulation_years": simulation.get("years"),
        "harvested_volume_m3": simulation.get("harvested_volume_m3"),
        "treated_area_ha": simulation.get("treated_area_ha"),
        "scheduled_area_ha": schedule["scheduled_area_ha"],
        "schedule_rows": schedule["rows"],
        "eligible_blocks": schedule["eligible_blocks"],
        "storage_bytes": storage,
        "wall_seconds": summary["wall_seconds"],
        "built_utc": summary["built_utc"],
    }


def _national_990m_forest_area_ha() -> float | None:
    if not NATIONAL_990M_SUMMARY.is_file():
        return None
    try:
        payload = json.loads(NATIONAL_990M_SUMMARY.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    value = payload.get("counts", {}).get("included_forest_area_ha")
    if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        return None
    return float(value)


def _extrapolation(
    selected_rows: Sequence[Mapping[str, Any]],
    *,
    candidate_count: int,
) -> dict[str, Any]:
    if not selected_rows:
        raise ScanfiEstateError("cannot extrapolate from no selected tiles")
    fragments = np.array(
        [row["forest_fragments"] for row in selected_rows], dtype=float
    )
    blocks = np.array([row["blocks"] for row in selected_rows], dtype=float)
    area = np.array(
        [row["forest_area_ha"] for row in selected_rows], dtype=float
    )
    dataset_bytes = np.array(
        [row["storage_bytes"]["dataset"] for row in selected_rows], dtype=float
    )
    run_bytes = np.array(
        [row["storage_bytes"]["runs"] for row in selected_rows], dtype=float
    )
    total_bytes = np.array(
        [row["storage_bytes"]["total"] for row in selected_rows], dtype=float
    )
    selected_total = {
        "tiles": len(selected_rows),
        "forest_fragments": int(fragments.sum()),
        "blocks": int(blocks.sum()),
        "forest_area_ha": float(area.sum()),
        "dataset_bytes": int(dataset_bytes.sum()),
        "run_bytes": int(run_bytes.sum()),
        "total_bytes": int(total_bytes.sum()),
    }
    mean_per_tile = {
        "forest_fragments": float(fragments.mean()),
        "blocks": float(blocks.mean()),
        "forest_area_ha": float(area.mean()),
        "dataset_bytes": float(dataset_bytes.mean()),
        "run_bytes": float(run_bytes.mean()),
        "total_bytes": float(total_bytes.mean()),
    }
    median_per_tile = {
        "forest_fragments": float(np.median(fragments)),
        "blocks": float(np.median(blocks)),
        "forest_area_ha": float(np.median(area)),
        "dataset_bytes": float(np.median(dataset_bytes)),
        "run_bytes": float(np.median(run_bytes)),
        "total_bytes": float(np.median(total_bytes)),
    }
    mean_scaled = {
        key: value * candidate_count for key, value in mean_per_tile.items()
    }
    national_area = _national_990m_forest_area_ha()
    area_scaled: dict[str, Any] | None = None
    if national_area is not None and selected_total["forest_area_ha"] > 0.0:
        scale = float(national_area) / selected_total["forest_area_ha"]
        area_scaled = {
            "basis": (
                "national 990-m dataset included_forest_area_ha / "
                "selected-tile forest area"
            ),
            "national_990m_forest_area_ha": float(national_area),
            "scale_factor": scale,
            "forest_fragments": round(selected_total["forest_fragments"] * scale),
            "blocks": round(selected_total["blocks"] * scale),
            "forest_area_ha": float(national_area),
            "dataset_bytes": round(selected_total["dataset_bytes"] * scale),
            "run_bytes": round(selected_total["run_bytes"] * scale),
            "total_bytes": round(selected_total["total_bytes"] * scale),
        }
    return {
        "candidate_tile_datasets": int(candidate_count),
        "selected_total": selected_total,
        "mean_per_tile": mean_per_tile,
        "median_per_tile": median_per_tile,
        "mean_scaled_to_all_candidates": mean_scaled,
        "area_scaled_to_national_forest_area": area_scaled,
        "interpretation": (
            "Extrapolations are arithmetic projections from the representative "
            "sample, not a national build. The area-scaled row is preferred "
            "because the sample over-represents small PEI tiles."
        ),
    }


def _manifest_candidates(
    frame: pd.DataFrame,
    selected_ids: set[str],
    reasons: Mapping[str, Sequence[str]],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for row in frame.itertuples(index=False):
        node_id = str(row.node_id)
        records.append(
            {
                "node_id": node_id,
                "parent_node_id": str(row.parent_node_id),
                "province": str(row.province),
                "tile_id": str(row.tile_id),
                "row": int(row.row),
                "col": int(row.col),
                "harvest_m3": float(row.harvest_m3),
                "subcell_area_ha": float(row.subcell_area_ha),
                "subcell_geometry_area_ha": float(
                    row.subcell_geometry_area_ha
                ),
                "positive_harvest": bool(float(row.harvest_m3) > 0.0),
                "selected": node_id in selected_ids,
                "selection_reasons": list(reasons.get(node_id, [])),
                "tile_slug": tile_slug(node_id),
            }
        )
    return records


def build_national_manifest(
    *,
    frame: pd.DataFrame,
    selected_frame: pd.DataFrame,
    selection_diagnostics: Mapping[str, Any],
    selected_summaries: Sequence[Mapping[str, Any]],
    provenance_records: Sequence[Mapping[str, Any]],
    scope: str,
    failures: Sequence[Mapping[str, str]] = (),
    out_path: Path = NATIONAL_MANIFEST_PATH,
    out_root: Path = TILED_DIR,
) -> dict[str, Any]:
    selected_ids = set(selected_frame["node_id"].astype(str))
    reasons = {
        str(row.node_id): list(row.selection_reasons)
        for row in selected_frame.itertuples(index=False)
    }
    rows = [_selected_manifest_row(summary) for summary in selected_summaries]
    rows.sort(key=lambda item: (item["province"], -item["harvest_m3"], item["node_id"]))
    by_node = {row["node_id"]: row for row in rows}
    missing = sorted(selected_ids - set(by_node))
    if missing and scope != "all":
        raise ScanfiEstateError(
            f"national manifest is missing selected tiles: {missing}"
        )
    candidates = _manifest_candidates(frame, selected_ids, reasons)
    extrapolation = _extrapolation(
        rows, candidate_count=len(frame) if scope != "all" else len(selected_frame)
    )
    if scope == "all":
        extrapolation["candidate_tile_datasets"] = len(frame)
        extrapolation["note"] = "scope=all; extrapolation is not used"
    validation_failures = [
        row["node_id"] for row in rows if row["validation_status"] != "pass"
    ]
    simulation_failures = [
        row
        for row in rows
        if row["simulation_status"] not in {"pass", "not_run_no_eligible_blocks"}
    ]
    status = "pass"
    if failures or missing or validation_failures or simulation_failures:
        status = "failed"
    manifest = {
        "artifact": "ws4_tiled_30m_national_v1",
        "generated_utc": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "status": status,
        "scope": {
            "scope": scope,
            "full_national_30m_run": False,
            "full_national_statement": (
                "The full national 30-m tiled build is 3,562 per-node WS4 "
                "datasets (one per committed 50-km supply node, clipped to its "
                "province). It is a distributed/storage-scale operation and was "
                "not run in this task. Only the representative sample below was "
                "built, validated, and simulated."
            ),
            "selection": dict(selection_diagnostics),
        },
        "labels": TILED_LABELS,
        "tile_grid": {
            "tile_resolution_m": TILE_RESOLUTION_M,
            "inventory_resolution": "30m",
            "inventory_resolution_m": INVENTORY_RESOLUTION_M,
            "fragment_area_ha": PIXEL_AREA_HA,
            "fragment_definition": "one 30-m SCANFI forest pixel",
            "block_definition": (
                "8-neighbour connected clique of equal (age_class, curve_key)"
            ),
            "connectivity": CONNECTIVITY,
            "age_class_width_years": AGE_CLASS_WIDTH,
            "geometry_basis": (
                "50-km subcell (3x3 split of the parent RSYC tile bounding box) "
                "intersected with the province; clipped geometry is validated "
                "against subcell_geometry_area_ha"
            ),
            "candidate_tile_datasets": len(frame),
            "unique_rectangles": len(frame[["tile_id", "row", "col"]].drop_duplicates()),
            "multi_province_rectangles": int(
                len(frame) - len(frame[["tile_id", "row", "col"]].drop_duplicates())
            ),
        },
        "candidates": candidates,
        "selected_tiles": rows,
        "totals": {
            "selected_tiles": len(rows),
            "selected_fragments": int(
                sum(row["forest_fragments"] for row in rows)
            ),
            "selected_blocks": int(sum(row["blocks"] for row in rows)),
            "selected_forest_area_ha": float(
                sum(row["forest_area_ha"] for row in rows)
            ),
            "selected_geometry_area_ha": float(
                sum(row["geometry_area_ha"] for row in rows)
            ),
            "selected_storage_bytes": int(
                sum(row["storage_bytes"]["total"] for row in rows)
            ),
            "selected_dataset_bytes": int(
                sum(row["storage_bytes"]["dataset"] for row in rows)
            ),
            "selected_run_bytes": int(
                sum(row["storage_bytes"]["runs"] for row in rows)
            ),
            "selected_schedule_bytes": int(
                sum(row["storage_bytes"]["schedules"] for row in rows)
            ),
        },
        "extrapolation": extrapolation,
        "provenance": {
            "sources": list(provenance_records),
            "ws4": ws4_provenance(),
            "supply_nodes_csv": _repo_path(SUPPLY_NODES_CSV),
            "rsyc_library_csv": _repo_path(RSYC_LIBRARY_CSV),
            "rsyc_tiles_gpkg": _repo_path(RSYC_TILES_GPKG),
        },
        "full_run": {
            "status": "not_run",
            "command": FULL_RUN_COMMAND,
            "datasets": len(frame),
            "workers": 32,
            "years": DEFAULT_YEARS,
            "sample_fraction": DEFAULT_SAMPLE_FRACTION,
            "statement": (
                "Run this only as a distributed/storage-scale operation. It is "
                "not a single-workstation job and was not executed for this "
                "manifest."
            ),
        },
        "failures": list(failures),
        "missing_selected_tiles": missing,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = out_path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(out_path)
    write_national_readme(manifest, out_path.parent)
    return manifest


def write_national_readme(manifest: Mapping[str, Any], root: Path) -> None:
    scope = manifest["scope"]["selection"]
    totals = manifest["totals"]
    extrapolation = manifest["extrapolation"]
    area_scaled = extrapolation.get("area_scaled_to_national_forest_area")
    lines = [
        "# WS4 tiled 30-m national sample",
        "",
        "This directory holds per-50-km-node WS4 datasets built from 30-m SCANFI",
        "pixels. It is a `model_estimate` / `sensitivity_only` sample, not a",
        "national 30-m run, not official OSeCAN, and not a chain v5.1 change.",
        "",
        "## Scope",
        "",
        f"- Candidate tile datasets: {manifest['tile_grid']['candidate_tile_datasets']:,}",
        f"- Unique 50-km rectangles: {manifest['tile_grid']['unique_rectangles']:,}",
        f"- Multi-province rectangles: {manifest['tile_grid']['multi_province_rectangles']:,}",
        f"- Selected/built tiles: {totals['selected_tiles']}",
        f"- Province-top tiles: {scope['province_top_nodes']}",
        f"- PEI nodes: {scope['pei_nodes']} across {scope['pei_unique_parent_tiles']} parent tiles",
        f"- Zero-harvest provinces skipped: {', '.join(scope['zero_harvest_provinces']) or 'none'}",
        "",
        "## Selected totals",
        "",
        f"- Fragments: {totals['selected_fragments']:,}",
        f"- Blocks: {totals['selected_blocks']:,}",
        f"- Forest area: {totals['selected_forest_area_ha']:,.2f} ha",
        f"- Storage: {totals['selected_storage_bytes'] / 1e9:,.3f} GB",
        "",
        "## Full national scale",
        "",
    ]
    if area_scaled is not None:
        lines.extend(
            [
                f"- Area-scaled fragments: {area_scaled['forest_fragments']:,}",
                f"- Area-scaled blocks: {area_scaled['blocks']:,}",
                f"- Area-scaled storage: {area_scaled['total_bytes'] / 1e9:,.1f} GB",
                f"- Scale factor: {area_scaled['scale_factor']:,.1f}x",
            ]
        )
    lines.extend(
        [
            "",
            "The full 3,562-dataset build is a distributed/storage-scale",
            "operation. It was **not** run here.",
            "",
            "## Contents",
            "",
            "- `national_tiled_manifest.json` — candidates, selection, per-tile",
            "  counts, validation/simulation status, totals, extrapolation,",
            "  provenance hashes, and the full-run command.",
            "- `<tile_slug>/dataset/` — 12 canonical WS4 tables + `manifest.json`.",
            "- `<tile_slug>/schedules/` — 10% CC schedule.",
            "- `<tile_slug>/runs/` — 20-year simulation outputs.",
            "- `<tile_slug>/inventory_summary.json` — full tile evidence.",
            "",
            "## Full-run command",
            "",
            "```bash",
            manifest["full_run"]["command"],
            "```",
            "",
            "See `docs/ws4-tiled-30m-national.md` for architecture and limitations.",
            "",
        ]
    )
    root.joinpath("README.md").write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Orchestration.
# ---------------------------------------------------------------------------


def _selected_frame_for_scope(
    frame: pd.DataFrame,
    args: argparse.Namespace,
) -> tuple[pd.DataFrame, dict[str, Any], str]:
    if args.all:
        selected = frame.copy()
        selected["selection_reasons"] = [["all"]] * len(selected)
        diagnostics = {
            "positive_harvest_provinces": int(
                frame.loc[frame["harvest_m3"] > 0.0, "province"].nunique()
            ),
            "province_top_nodes": 0,
            "pei_nodes": int((frame["province"] == "PE").sum()),
            "pei_unique_parent_tiles": int(
                frame.loc[frame["province"] == "PE", "tile_id"].nunique()
            ),
            "selected_nodes": len(selected),
            "zero_harvest_provinces": sorted(
                set(frame["province"].astype(str))
                - set(frame.loc[frame["harvest_m3"] > 0.0, "province"].astype(str))
            ),
            "selected_rectangles": len(selected[["tile_id", "row", "col"]].drop_duplicates()),
            "scope_note": "all 3,562 candidate tile datasets",
        }
        return selected.reset_index(drop=True), diagnostics, "all"
    selected, diagnostics = select_representative_tiles(frame)
    scope = "representative"
    if args.only_node:
        wanted = set(args.only_node)
        missing = wanted - set(selected["node_id"].astype(str))
        if missing:
            raise ScanfiEstateError(
                f"--only-node values are not in the representative selection: "
                f"{sorted(missing)}"
            )
        selected = selected[selected["node_id"].astype(str).isin(wanted)].copy()
        diagnostics = {
            **diagnostics,
            "selected_nodes": len(selected),
            "only_node": sorted(wanted),
            "scope_note": "filtered representative subset",
        }
        scope = "subset"
    return selected.reset_index(drop=True), diagnostics, scope


def _load_existing_summaries(
    selected: pd.DataFrame, out_root: Path
) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    missing: list[str] = []
    for row in selected.itertuples(index=False):
        path = out_root / tile_slug(str(row.node_id)) / "inventory_summary.json"
        if not path.is_file():
            missing.append(str(row.node_id))
            continue
        summaries.append(json.loads(path.read_text(encoding="utf-8")))
    if missing:
        raise ScanfiEstateError(
            f"manifest-only mode is missing summaries for {missing}"
        )
    return summaries


def run(
    *,
    args: argparse.Namespace,
    out_root: Path,
) -> dict[str, Any]:
    started = time.perf_counter()
    assert_no_unexpected_deletions()
    frame = load_candidates()
    selected, diagnostics, scope = _selected_frame_for_scope(frame, args)
    print(
        f"scope={scope}: {len(selected)} selected of {len(frame)} candidates",
        flush=True,
    )
    provenance_records = build_provenance_records(provenance_source_paths())

    if args.manifest_only:
        summaries = _load_existing_summaries(selected, out_root)
        failures: list[dict[str, str]] = []
    else:
        summaries, failures = _run_tiles(
            selected,
            out_root=out_root,
            provenance_records=provenance_records,
            sample_fraction=args.sample_fraction,
            years=args.years,
            run_ws4=not args.skip_ws4,
            workers=args.workers,
        )
    manifest = build_national_manifest(
        frame=frame,
        selected_frame=selected,
        selection_diagnostics=diagnostics,
        selected_summaries=summaries,
        provenance_records=provenance_records,
        scope=scope,
        failures=failures,
        out_root=out_root,
    )
    elapsed = time.perf_counter() - started
    print(
        f"national manifest: {out_root / 'national_tiled_manifest.json'} "
        f"({manifest['status']}, {elapsed:.1f}s)",
        flush=True,
    )
    if manifest["status"] != "pass":
        raise ScanfiEstateError(
            "national tiled build did not pass: "
            f"failures={failures}, missing={manifest['missing_selected_tiles']}, "
            f"validation={[row['node_id'] for row in manifest['selected_tiles'] if row['validation_status'] != 'pass']}"
        )
    assert_no_unexpected_deletions()
    return manifest


def _run_tiles(
    selected: pd.DataFrame,
    *,
    out_root: Path,
    provenance_records: Sequence[Mapping[str, Any]],
    sample_fraction: float,
    years: int,
    run_ws4: bool,
    workers: int,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    if workers < 1:
        raise ScanfiEstateError("workers must be at least one")
    payloads = [
        asdict(node_from_candidate(row))
        for row in selected.itertuples(index=False)
    ]
    summaries: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    if workers == 1 or len(payloads) == 1:
        for payload in payloads:
            node_id, summary, error = _build_tile_worker(
                payload,
                str(out_root),
                provenance_records,
                sample_fraction,
                years,
                run_ws4,
            )
            if summary is not None:
                summaries.append(summary)
            if error is not None:
                failures.append({"node_id": node_id, "error": error})
                print(f"FAILED {node_id}: {error}", flush=True)
        return summaries, failures
    context = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(
        max_workers=min(workers, len(payloads)), mp_context=context
    ) as pool:
        futures = {
            pool.submit(
                _build_tile_worker,
                payload,
                str(out_root),
                provenance_records,
                sample_fraction,
                years,
                run_ws4,
            ): str(payload["node_id"])
            for payload in payloads
        }
        for future in as_completed(futures):
            node_id, summary, error = future.result()
            if summary is not None:
                summaries.append(summary)
            if error is not None:
                failures.append({"node_id": node_id, "error": error})
                print(f"FAILED {node_id}: {error}", flush=True)
    return summaries, failures


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--all",
        action="store_true",
        help="Build all 3,562 candidate tile datasets (distributed scale)",
    )
    parser.add_argument(
        "--only-node",
        action="append",
        default=None,
        help="Build only this representative-selection node_id (repeatable)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help=f"Parallel tile workers (default: {DEFAULT_WORKERS})",
    )
    parser.add_argument(
        "--years",
        type=int,
        default=DEFAULT_YEARS,
        help=f"Simulation horizon (default: {DEFAULT_YEARS})",
    )
    parser.add_argument(
        "--sample-fraction",
        type=float,
        default=DEFAULT_SAMPLE_FRACTION,
        help=f"Eligible-block schedule fraction (default: {DEFAULT_SAMPLE_FRACTION})",
    )
    parser.add_argument(
        "--skip-ws4",
        action="store_true",
        help="Write datasets without ws4 validate/simulate (not for evidence)",
    )
    parser.add_argument(
        "--manifest-only",
        action="store_true",
        help="Rebuild the national manifest from existing per-tile summaries",
    )
    parser.add_argument(
        "--out-root",
        type=Path,
        default=TILED_DIR,
        help=f"Tiled output root (default: {TILED_DIR})",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if not 0.0 < args.sample_fraction <= 1.0:
        print("ERROR: --sample-fraction must be in (0, 1]", file=sys.stderr)
        return 2
    if args.years < 1:
        print("ERROR: --years must be positive", file=sys.stderr)
        return 2
    out_root = args.out_root.resolve()
    if not out_root.is_relative_to(WS4_OUT_ROOT.resolve()):
        print(
            f"ERROR: --out-root must be inside {WS4_OUT_ROOT}",
            file=sys.stderr,
        )
        return 2
    try:
        manifest = run(args=args, out_root=out_root)
    except ScanfiEstateError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "selected_tiles": manifest["totals"]["selected_tiles"],
                "selected_fragments": manifest["totals"]["selected_fragments"],
                "selected_blocks": manifest["totals"]["selected_blocks"],
                "selected_forest_area_ha": manifest["totals"][
                    "selected_forest_area_ha"
                ],
                "selected_storage_bytes": manifest["totals"][
                    "selected_storage_bytes"
                ],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
