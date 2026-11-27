#!/usr/bin/env python3
"""Build the first WS4 forest-estate pilot from SCANFI pixels and RSYC curves.

The pilot selects the highest-harvest 50-km BC supply subnode, reconstructs its
geometry from the parent RSYC 150-km tile, reads the 30-m SCANFI inventory in a
single raster window, and compiles a canonical WS4 dataset.  Forest pixels are
fragments; connected cliques of equal ``(age_class, curve_key)`` pixels are WS4
blocks.  The dataset intentionally uses one RSYC yield curve for both the
natural and managed tracks: the pilot has no planting-response data, so the
managed track records the management-state transition only.

The build is fail-closed.  It refuses to write when either repository reports
an unexpected deletion, when any raster is not a 30-m aligned layer, when a
forest pixel has no RSYC curve, or when an area/count invariant does not close.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
import subprocess
import sys
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.features import rasterize
from rasterio.warp import transform_geom
from rasterio.windows import Window
from scipy import ndimage
from shapely.geometry import Polygon, shape
from shapely.validation import make_valid

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = REPO_ROOT / "data"

SUPPLY_NODES_CSV = (
    DATA_ROOT / "derived/national/facilities/supply_node_50km_v1/supply_nodes_50km.csv"
)
RSYC_LIBRARY_CSV = (
    DATA_ROOT
    / "derived/national/assortments/rsyc_tile_production"
    / "rsyc_tile_production_library.csv"
)
RSYC_TILES_GPKG = DATA_ROOT / "raw/national/rsyc/RSYC_tiles.gpkg"
BOUNDARY_GEOJSON = (
    DATA_ROOT / "raw/boundaries/canada_adm1_geoboundaries_simplified.geojson"
)
SCANFI_DIR = DATA_ROOT / "raw/scanfi/v2"
AGE_RASTER = SCANFI_DIR / "SCANFI_age_median_2025_v2_20260119.tif"
LANDCOVER_RASTER = SCANFI_DIR / "SCANFI_att_nfiLandcover_2025_v2_20260119.tif"

WS4_OUT_ROOT = DATA_ROOT / "derived/ws4"
WS4_BIN = REPO_ROOT / ".venv-ws4/bin/ws4"
WS4_SOURCE_TREE = Path("/srv/shared-data/gep/ws4/ws4-gpu")

PIXEL_SIZE_M = 30.0
PIXEL_AREA_HA = PIXEL_SIZE_M * PIXEL_SIZE_M / 10_000.0
GRID_SIZE = 3
FOREST_LANDCOVER = (5, 6, 7)
LANDCOVER_GROUP = {5: "BROADLEAF", 6: "CONIFEROUS", 7: "GENERIC"}
SPECIES_TO_CODES = {
    "blackSpruce": ("PICE.MAR",),
    "balsamFir": ("ABIE.BAL",),
    "jackPine": ("PINU.BAN",),
    "lodgepolePine": ("PINU.CON",),
    "ponderosaPine": ("PINU.PON",),
    "douglasFir": ("PSEU.MEN",),
    "tamarack": ("LARI.LAR",),
    "whiteRedPine": ("PINU.STR", "PINU.RES"),
    "broadleaf": ("BROADLEAF",),
    "otherConiferous": ("CONIFEROUS",),
}
CODE_TO_GROUP = {
    "PICE.MAR": "CONIFEROUS",
    "ABIE.BAL": "CONIFEROUS",
    "PINU.BAN": "CONIFEROUS",
    "PINU.CON": "CONIFEROUS",
    "PINU.PON": "CONIFEROUS",
    "PSEU.MEN": "CONIFEROUS",
    "LARI.LAR": "CONIFEROUS",
    "PINU.STR": "CONIFEROUS",
    "PINU.RES": "CONIFEROUS",
    "BROADLEAF": "BROADLEAF",
    "CONIFEROUS": "CONIFEROUS",
}
# The published RSYC curves run over ages 1-600.  WS4 clamps above the last
# point, so the pilot carries the full published range plus an explicit origin.
CURVE_DOMAIN = np.arange(0, 601, dtype=float)
AREA_CURVE_ID = 1
TREATMENT = "CC"
TREATMENT_BEGIN = 40
TREATMENT_END = 1000

LABELS = {
    "evidence_label": "model_estimate",
    "observed": False,
    "policy_grade": False,
    "national_claim": False,
    "official_osecan_changed": False,
    "ui_changed": False,
    "sensitivity_only": True,
    "chain_v51_unchanged": True,
}


class ScanfiEstateError(RuntimeError):
    """Raised when a pilot invariant or provenance check fails."""


@dataclass(frozen=True, slots=True)
class PilotNode:
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


@dataclass(frozen=True, slots=True)
class CurveSpec:
    key: str
    model_id: str
    species: str
    species_kind: str
    selected_level: str
    source_species: str
    b1: float
    b2: float
    b3: float
    b4: float

    def coefficients(self) -> tuple[float, float, float, float]:
        return (self.b1, self.b2, self.b3, self.b4)

    def curve_values(self) -> np.ndarray:
        x = CURVE_DOMAIN[1:]
        with np.errstate(over="raise", invalid="raise"):
            try:
                y = (
                    self.b1
                    * np.exp(-self.b4 * x)
                    * np.power(1.0 - np.exp(-self.b2 * x), self.b3)
                )
            except FloatingPointError as error:
                raise ScanfiEstateError(
                    f"curve {self.key!r} is not finite: {error}"
                ) from error
        if not np.all(np.isfinite(y)) or np.any(y < 0.0):
            raise ScanfiEstateError(
                f"curve {self.key!r} produced a non-finite or negative value"
            )
        if y[0] <= 0.0:
            raise ScanfiEstateError(f"curve {self.key!r} is zero at age 1")
        return np.concatenate([[0.0], y])


@dataclass(frozen=True, slots=True)
class CurveResolution:
    specs: tuple[CurveSpec, ...]
    species_spec_index: Mapping[str, int]
    landcover_spec_index: Mapping[int, int]
    diagnostics: Mapping[str, Any]


# ---------------------------------------------------------------------------
# Fail-closed and provenance helpers.
# ---------------------------------------------------------------------------


def _git_status(repository: Path) -> str:
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repository,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise ScanfiEstateError(
            f"cannot inspect git status in {repository}: {result.stderr.strip()}"
        )
    return result.stdout


def assert_no_unexpected_deletions(
    repositories: Sequence[Path] = (REPO_ROOT, DATA_ROOT),
) -> None:
    """Halt on any ``D`` in either porcelain status column."""
    incidents: list[str] = []
    for repository in repositories:
        for line in _git_status(repository).splitlines():
            if len(line) < 2:
                continue
            if line[0] == "D" or line[1] == "D":
                incidents.append(f"{repository}: {line}")
    if incidents:
        raise ScanfiEstateError(
            "unexpected deletion status detected; halting: " + " | ".join(incidents)
        )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _repo_path(path: Path) -> str:
    absolute = path if path.is_absolute() else REPO_ROOT / path
    try:
        return absolute.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(absolute)


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def ws4_provenance() -> dict[str, str]:
    """Return the WS4 executable, editable source tree, and version."""
    version = "unknown"
    init_path = WS4_SOURCE_TREE / "ws4/__init__.py"
    if init_path.is_file():
        match = re.search(
            r'__version__\s*=\s*["\']([^"\']+)["\']',
            init_path.read_text(encoding="utf-8"),
        )
        if match is not None:
            version = match.group(1)
    return {
        "executable": str(WS4_BIN),
        "editable_source_tree": str(WS4_SOURCE_TREE),
        "version": version,
    }


def _safe_reset_output(out_dir: Path) -> None:
    if not out_dir.exists():
        return
    resolved = out_dir.resolve()
    allowed_parent = WS4_OUT_ROOT.resolve()
    if resolved.parent != allowed_parent:
        raise ScanfiEstateError(
            f"refusing to reset unexpected output directory: {resolved}"
        )
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
# Supply node and geometry.
# ---------------------------------------------------------------------------


def select_pilot_node(path: Path = SUPPLY_NODES_CSV) -> PilotNode:
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
    }
    missing = required - set(frame.columns)
    if missing:
        raise ScanfiEstateError(f"{path}: missing columns {sorted(missing)}")
    british_columbia = frame[frame["province"].astype(str) == "BC"].copy()
    if british_columbia.empty:
        raise ScanfiEstateError(f"{path}: no BC supply nodes")
    british_columbia["harvest_m3"] = pd.to_numeric(
        british_columbia["harvest_m3"], errors="raise"
    )
    best = british_columbia.sort_values(
        ["harvest_m3", "node_id"], ascending=[False, True]
    ).iloc[0]
    row = int(best["row"])
    col = int(best["col"])
    if not (1 <= row <= GRID_SIZE and 1 <= col <= GRID_SIZE):
        raise ScanfiEstateError(
            f"{path}: selected row/col outside 3x3 grid: {row!r}/{col!r}"
        )
    node = PilotNode(
        node_id=str(best["node_id"]),
        parent_node_id=str(best["parent_node_id"]),
        tile_id=str(best["tile_id"]),
        province=str(best["province"]),
        row=row,
        col=col,
        lat=float(best["lat"]),
        lon=float(best["lon"]),
        harvest_m3=float(best["harvest_m3"]),
        subcell_area_ha=float(best["subcell_area_ha"]),
    )
    if node.node_id != f"{node.tile_id}|{node.province}|S{node.row}{node.col}":
        raise ScanfiEstateError(
            f"node_id {node.node_id!r} does not match tile/province/row/col"
        )
    if not math.isfinite(node.subcell_area_ha) or node.subcell_area_ha <= 0.0:
        raise ScanfiEstateError(
            f"supply node has invalid productive subcell area: {node.subcell_area_ha}"
        )
    return node


def load_tile_geometry(tile_id: str, path: Path = RSYC_TILES_GPKG) -> Any:
    tiles = gpd.read_file(path, layer="RSYC_tiles")
    if tiles.crs is None or tiles.crs.to_epsg() != 3978:
        raise ScanfiEstateError(f"{path}: expected EPSG:3978, got {tiles.crs}")
    matches = tiles[tiles["TileID"].astype(str) == tile_id]
    if len(matches) != 1:
        raise ScanfiEstateError(
            f"{path}: expected one tile {tile_id!r}, found {len(matches)}"
        )
    geometry = make_valid(matches.iloc[0].geometry)
    if geometry.is_empty or geometry.area <= 0.0:
        raise ScanfiEstateError(f"{path}: invalid tile geometry {tile_id!r}")
    return geometry


def subcell_polygon(tile_geometry: Any, row: int, col: int) -> Polygon:
    min_x, min_y, max_x, max_y = tile_geometry.bounds
    width = (max_x - min_x) / GRID_SIZE
    height = (max_y - min_y) / GRID_SIZE
    if width <= 0.0 or height <= 0.0:
        raise ScanfiEstateError("tile bounding box is degenerate")
    return Polygon(
        [
            (min_x + (col - 1) * width, min_y + (row - 1) * height),
            (min_x + col * width, min_y + (row - 1) * height),
            (min_x + col * width, min_y + row * height),
            (min_x + (col - 1) * width, min_y + row * height),
        ]
    )


def province_intersection_area(polygon_3978: Any, province: str = "BC") -> float:
    if not BOUNDARY_GEOJSON.is_file():
        return float("nan")
    provinces = gpd.read_file(BOUNDARY_GEOJSON).to_crs(3978)
    names = {"BC": "British Columbia"}
    match = provinces[
        provinces["shapeName"].astype(str) == names.get(province, province)
    ]
    if match.empty:
        return float("nan")
    geometry = make_valid(match.iloc[0].geometry)
    return float(make_valid(polygon_3978.intersection(geometry)).area / 10_000.0)


# ---------------------------------------------------------------------------
# Raster window and SCANFI inventory.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RasterWindow:
    window: Window
    transform: Any
    crs: Any
    polygon: Any
    polygon_area_ha: float
    inside_pixels: int
    inside_mask: np.ndarray


def _aligned_window(
    path: Path, polygon_3978: Any, *, expected_crs: Any, expected_transform: Any
) -> RasterWindow:
    with rasterio.open(path) as dataset:
        if dataset.crs is None:
            raise ScanfiEstateError(f"{path}: missing CRS")
        if expected_crs is not None and dataset.crs != expected_crs:
            raise ScanfiEstateError(f"{path}: CRS differs from the age raster")
        if expected_transform is not None and dataset.transform != expected_transform:
            raise ScanfiEstateError(f"{path}: transform differs from the age raster")
        x_res, y_res = dataset.res
        if not (
            math.isclose(x_res, PIXEL_SIZE_M, abs_tol=1e-9)
            and math.isclose(y_res, PIXEL_SIZE_M, abs_tol=1e-9)
        ):
            raise ScanfiEstateError(
                f"{path}: expected 30-m pixels, got {x_res!r} x {y_res!r}"
            )
        if dataset.transform.b != 0.0 or dataset.transform.d != 0.0:
            raise ScanfiEstateError(f"{path}: rotated transforms are not supported")
        if not math.isclose(
            abs(dataset.transform.a * dataset.transform.e), 900.0, abs_tol=1e-6
        ):
            raise ScanfiEstateError(f"{path}: pixel area is not 900 m2")
        geometry = shape(
            transform_geom("EPSG:3978", dataset.crs, polygon_3978.__geo_interface__)
        )
        min_x, min_y, max_x, max_y = geometry.bounds
        raw = rasterio.windows.from_bounds(
            min_x, min_y, max_x, max_y, transform=dataset.transform
        )
        col_off = max(0, math.floor(raw.col_off))
        row_off = max(0, math.floor(raw.row_off))
        col_end = min(dataset.width, math.ceil(raw.col_off + raw.width))
        row_end = min(dataset.height, math.ceil(raw.row_off + raw.height))
        window = Window(
            col_off=col_off,
            row_off=row_off,
            width=col_end - col_off,
            height=row_end - row_off,
        )
        if window.width <= 0 or window.height <= 0:
            raise ScanfiEstateError(f"{path}: empty raster window")
        window_transform = dataset.window_transform(window)
        mask = rasterize(
            [(geometry, 1)],
            out_shape=(int(window.height), int(window.width)),
            transform=window_transform,
            fill=0,
            dtype="uint8",
            all_touched=False,
        )
        inside_pixels = int(mask.sum())
        if inside_pixels == 0:
            raise ScanfiEstateError(f"{path}: 50-km rectangle selects no pixels")
        return RasterWindow(
            window=window,
            transform=window_transform,
            crs=dataset.crs,
            polygon=geometry,
            polygon_area_ha=float(geometry.area / 10_000.0),
            inside_pixels=inside_pixels,
            inside_mask=mask.astype(bool),
        )


def read_raster_window(path: Path, window: Window) -> np.ndarray:
    with rasterio.open(path) as dataset:
        return dataset.read(1, window=window)


def discover_species_layers(directory: Path = SCANFI_DIR) -> dict[str, Path]:
    pattern = re.compile(r"^SCANFI_spsCC_(?P<species>[A-Za-z]+)_2025_v2_20260119\.tif$")
    layers: dict[str, Path] = {}
    for path in sorted(directory.glob("SCANFI_spsCC_*_2025_v2_20260119.tif")):
        match = pattern.match(path.name)
        if match is None:
            raise ScanfiEstateError(f"unrecognised species raster name: {path.name}")
        species = match.group("species")
        if species not in SPECIES_TO_CODES:
            raise ScanfiEstateError(f"unrecognised species layer: {species!r}")
        if species in layers:
            raise ScanfiEstateError(f"duplicate species layer: {species!r}")
        layers[species] = path
    missing = set(SPECIES_TO_CODES) - set(layers)
    if missing:
        raise ScanfiEstateError(f"missing species rasters: {sorted(missing)}")
    return layers


def load_scanfi_window(
    polygon_3978: Any,
) -> tuple[
    np.ndarray,
    np.ndarray,
    np.ndarray,
    np.ndarray,
    RasterWindow,
    dict[str, Path],
]:
    species_layers = discover_species_layers()
    age_window = _aligned_window(
        AGE_RASTER,
        polygon_3978,
        expected_crs=None,
        expected_transform=None,
    )
    landcover_window = _aligned_window(
        LANDCOVER_RASTER,
        polygon_3978,
        expected_crs=age_window.crs,
        expected_transform=None,
    )
    if landcover_window.window != age_window.window:
        raise ScanfiEstateError("age and landcover windows differ")
    window = age_window.window
    age = read_raster_window(AGE_RASTER, window)
    landcover = read_raster_window(LANDCOVER_RASTER, window)
    if age.shape != landcover.shape:
        raise ScanfiEstateError("age and landcover arrays differ in shape")

    max_closure = np.zeros(age.shape, dtype=np.uint8)
    dominant_species = np.full(age.shape, -1, dtype=np.int8)
    for index, species in enumerate(SPECIES_TO_CODES):
        path = species_layers[species]
        layer_window = _aligned_window(
            path,
            polygon_3978,
            expected_crs=age_window.crs,
            expected_transform=None,
        )
        if layer_window.window != window:
            raise ScanfiEstateError(f"{path}: species window differs")
        closure = read_raster_window(path, window)
        if closure.shape != age.shape:
            raise ScanfiEstateError(f"{path}: species array differs in shape")
        better = closure > max_closure
        max_closure[better] = closure[better]
        dominant_species[better] = index
    return (
        age,
        landcover,
        max_closure,
        dominant_species,
        age_window,
        species_layers,
    )


# ---------------------------------------------------------------------------
# RSYC curve resolution.
# ---------------------------------------------------------------------------


def _coefficient_digest(spec: CurveSpec) -> str:
    payload = "|".join(f"{value:.17g}" for value in spec.coefficients())
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:8]


def curve_spec_records(resolution: CurveResolution) -> list[dict[str, Any]]:
    return [
        {
            "curve_id": index + 2,
            "curve_key": spec.key,
            "model_id": spec.model_id,
            "species": spec.species,
            "species_kind": spec.species_kind,
            "selected_level": spec.selected_level,
            "source_species": spec.source_species,
            "b1": spec.b1,
            "b2": spec.b2,
            "b3": spec.b3,
            "b4": spec.b4,
        }
        for index, spec in enumerate(resolution.specs)
    ]


def resolve_curves(
    library: pd.DataFrame, *, tile_id: str, province: str
) -> CurveResolution:
    required = {
        "tile_id",
        "province",
        "species",
        "species_kind",
        "model_id",
        "selected_level",
        "source_species",
        "b1",
        "b2",
        "b3",
        "b4",
    }
    missing = required - set(library.columns)
    if missing:
        raise ScanfiEstateError(f"RSYC library missing columns {sorted(missing)}")
    subset = library[
        (library["tile_id"].astype(str) == tile_id)
        & (library["province"].astype(str) == province)
    ].copy()
    if subset.empty:
        raise ScanfiEstateError(f"RSYC library has no rows for {tile_id}|{province}")
    for column in ("b1", "b2", "b3", "b4"):
        subset[column] = pd.to_numeric(subset[column], errors="raise")
    if (subset[["b1", "b2", "b3", "b4"]] <= 0.0).any().any():
        raise ScanfiEstateError(
            f"RSYC library for {tile_id}|{province} has non-positive coefficients"
        )

    coefficient_sets: dict[str, set[tuple[float, float, float, float]]] = defaultdict(
        set
    )
    for row in subset.itertuples(index=False):
        coefficient_sets[str(row.model_id)].add(
            (float(row.b1), float(row.b2), float(row.b3), float(row.b4))
        )
    ambiguous_models = {
        model_id for model_id, values in coefficient_sets.items() if len(values) > 1
    }

    def make_spec(row: Any) -> CurveSpec:
        provisional = CurveSpec(
            key=str(row.model_id),
            model_id=str(row.model_id),
            species=str(row.species),
            species_kind=str(row.species_kind),
            selected_level=str(row.selected_level),
            source_species=str(row.source_species),
            b1=float(row.b1),
            b2=float(row.b2),
            b3=float(row.b3),
            b4=float(row.b4),
        )
        if provisional.model_id in ambiguous_models:
            key = f"{provisional.model_id}#{_coefficient_digest(provisional)}"
            return CurveSpec(
                key=key,
                model_id=provisional.model_id,
                species=provisional.species,
                species_kind=provisional.species_kind,
                selected_level=provisional.selected_level,
                source_species=provisional.source_species,
                b1=provisional.b1,
                b2=provisional.b2,
                b3=provisional.b3,
                b4=provisional.b4,
            )
        return provisional

    by_species: dict[str, CurveSpec] = {}
    duplicate_rows: list[dict[str, Any]] = []
    for row in subset.itertuples(index=False):
        spec = make_spec(row)
        existing = by_species.get(spec.species)
        if existing is not None:
            if existing.key != spec.key:
                raise ScanfiEstateError(
                    f"RSYC species {spec.species!r} has two different curves"
                )
            duplicate_rows.append(
                {
                    "species": spec.species,
                    "model_id": spec.model_id,
                    "resolution": "same_curve_duplicate_row",
                }
            )
            continue
        by_species[spec.species] = spec

    generic_spec = next(
        (
            spec
            for spec in by_species.values()
            if spec.source_species.lower() == "generic"
        ),
        None,
    )

    def resolve_code(code: str) -> tuple[CurveSpec | None, str]:
        exact = by_species.get(code)
        if exact is not None:
            return exact, "exact"
        group = CODE_TO_GROUP.get(code)
        if group is not None and group in by_species:
            return by_species[group], "group"
        if generic_spec is not None:
            return generic_spec, "generic"
        return None, "missing"

    species_spec_index: dict[str, int] = {}
    species_resolution: dict[str, dict[str, Any]] = {}
    resolved_specs: dict[str, CurveSpec] = {}
    for layer, codes in SPECIES_TO_CODES.items():
        selected: CurveSpec | None = None
        selected_code = ""
        selected_level = "missing"
        for code in codes:
            spec, level = resolve_code(code)
            if spec is not None:
                selected = spec
                selected_code = code
                selected_level = level
                break
        if selected is None:
            species_resolution[layer] = {
                "rsyc_codes": list(codes),
                "curve_key": None,
                "resolution": "missing",
            }
            continue
        resolved_specs[selected.key] = selected
        species_resolution[layer] = {
            "rsyc_codes": list(codes),
            "matched_code": selected_code,
            "curve_key": selected.key,
            "resolution": selected_level,
        }

    landcover_spec_index: dict[int, int] = {}
    landcover_resolution: dict[str, dict[str, Any]] = {}
    for landcover_class, group_code in LANDCOVER_GROUP.items():
        if group_code == "GENERIC":
            spec = generic_spec
            level = "generic" if spec is not None else "missing"
        else:
            spec, level = resolve_code(group_code)
        if spec is None:
            landcover_resolution[str(landcover_class)] = {
                "group": group_code,
                "curve_key": None,
                "resolution": "missing",
            }
            continue
        resolved_specs[spec.key] = spec
        landcover_resolution[str(landcover_class)] = {
            "group": group_code,
            "curve_key": spec.key,
            "resolution": level,
        }

    ordered_specs = tuple(sorted(resolved_specs.values(), key=lambda spec: spec.key))
    for index, spec in enumerate(ordered_specs):
        for layer, info in species_resolution.items():
            if info.get("curve_key") == spec.key:
                species_spec_index[layer] = index
        for landcover_class, info in landcover_resolution.items():
            if info.get("curve_key") == spec.key:
                landcover_spec_index[int(landcover_class)] = index

    diagnostics = {
        "tile_id": tile_id,
        "province": province,
        "library_rows": len(subset),
        "model_ids": sorted(coefficient_sets),
        "ambiguous_model_ids": sorted(ambiguous_models),
        "duplicate_curve_rows": duplicate_rows,
        "duplicate_model_same_coefficients": sorted(
            model_id
            for model_id, values in coefficient_sets.items()
            if len(values) == 1
            and sum(
                1
                for row in subset.itertuples(index=False)
                if str(row.model_id) == model_id
            )
            > 1
        ),
        "generic_curve_key": generic_spec.key if generic_spec is not None else None,
        "species_resolution": species_resolution,
        "landcover_resolution": landcover_resolution,
    }
    return CurveResolution(
        specs=ordered_specs,
        species_spec_index=species_spec_index,
        landcover_spec_index=landcover_spec_index,
        diagnostics=diagnostics,
    )


# ---------------------------------------------------------------------------
# Pixel -> fragment/block inventory.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Inventory:
    fragments: pd.DataFrame
    blocks: pd.DataFrame
    groups: pd.DataFrame
    age_distribution: tuple[dict[str, int], ...]
    curve_usage: tuple[dict[str, Any], ...]
    clique_summary: Mapping[str, Any]
    exclusions: Mapping[str, int]
    counts: Mapping[str, Any]


def _label_blocks(code_grid: np.ndarray, forest: np.ndarray) -> tuple[np.ndarray, int]:
    if not forest.any():
        return np.zeros(code_grid.shape, dtype=np.int32), 0
    labels = np.zeros(code_grid.shape, dtype=np.int32)
    next_label = 1
    structure = np.ones((3, 3), dtype=np.uint8)
    for code in np.unique(code_grid[forest]):
        mask = code_grid == code
        components, count = ndimage.label(mask, structure=structure)
        if count == 0:
            raise ScanfiEstateError(
                f"block labelling produced no component for code {code}"
            )
        labels[mask] = components[mask] + next_label - 1
        next_label += count
    return labels, next_label - 1


def _relabel_by_first_pixel(
    labels: np.ndarray, positions: np.ndarray, block_count: int
) -> np.ndarray:
    flat_labels = labels.ravel()[positions]
    first_position = np.full(block_count + 1, positions.size, dtype=np.int64)
    np.minimum.at(
        first_position, flat_labels, np.arange(positions.size, dtype=np.int64)
    )
    old_ids = np.argsort(first_position[1:], kind="stable") + 1
    remap = np.zeros(block_count + 1, dtype=np.int64)
    remap[old_ids] = np.arange(1, block_count + 1, dtype=np.int64)
    return remap[flat_labels]


def build_inventory(
    *,
    age: np.ndarray,
    landcover: np.ndarray,
    max_closure: np.ndarray,
    dominant_species: np.ndarray,
    species_layers: Sequence[str],
    resolution: CurveResolution,
    tile_id: str,
    inside_pixels: int,
    inside_mask: np.ndarray,
    resolution_m: float = PIXEL_SIZE_M,
) -> Inventory:
    if not (
        age.shape
        == landcover.shape
        == max_closure.shape
        == dominant_species.shape
        == inside_mask.shape
    ):
        raise ScanfiEstateError(
            "SCANFI arrays and the 50-km mask do not share one shape"
        )
    if resolution_m != PIXEL_SIZE_M:
        raise ScanfiEstateError(
            "this pilot builder is fixed at 30-m pixels; aggregate input explicitly"
        )
    if int(inside_pixels) != int(np.count_nonzero(inside_mask)):
        raise ScanfiEstateError("raster window and polygon mask disagree")

    forest_class = np.isin(landcover, FOREST_LANDCOVER) & inside_mask
    landcover_nodata = (landcover == 255) & inside_mask
    age_nodata = (age == -32768) & inside_mask
    nonpositive_age = forest_class & ~age_nodata & (age <= 0)
    forest = forest_class & ~landcover_nodata & ~age_nodata & (age > 0)
    nonforest = inside_mask & ~forest_class & ~landcover_nodata

    excluded_outside = int(age.size - inside_pixels)
    excluded_landcover_nodata = int(np.count_nonzero(landcover_nodata))
    excluded_age_nodata = int(np.count_nonzero(forest_class & age_nodata))
    excluded_nonforest = int(np.count_nonzero(nonforest))
    excluded_nonpositive_age = int(np.count_nonzero(nonpositive_age))
    forest_pixels = int(np.count_nonzero(forest))
    if forest_pixels == 0:
        raise ScanfiEstateError("no forest pixels in the 50-km window")

    spec_index = np.full(age.shape, -1, dtype=np.int16)
    for layer_index, layer in enumerate(species_layers):
        target = resolution.species_spec_index.get(layer)
        if target is not None:
            spec_index[dominant_species == layer_index] = target
    for landcover_class, target in resolution.landcover_spec_index.items():
        mask = landcover == landcover_class
        spec_index[mask] = target
    # A pixel with a positive dominant-species closure uses that species; only a
    # zero/absent closure falls back to the landcover group.
    for layer_index, layer in enumerate(species_layers):
        target = resolution.species_spec_index.get(layer)
        if target is None:
            continue
        mask = (dominant_species == layer_index) & (max_closure > 0)
        spec_index[mask] = target
    missing = forest & (spec_index < 0)
    if missing.any():
        counts = {
            species_layers[index]: int(
                np.count_nonzero(missing & (dominant_species == index))
            )
            for index in range(len(species_layers))
        }
        counts["landcover_fallback"] = int(
            np.count_nonzero(missing & (max_closure == 0))
        )
        raise ScanfiEstateError(
            "forest pixels have no RSYC curve: " + json.dumps(counts, sort_keys=True)
        )

    curve_count = len(resolution.specs)
    if curve_count == 0:
        raise ScanfiEstateError("no RSYC curves resolved")
    age_class = (age // 10).astype(np.int16) * 10
    age_class_index = (age_class // 10).astype(np.int16)
    code_grid = np.full(age.shape, -1, dtype=np.int32)
    code_grid[forest] = age_class_index[forest].astype(
        np.int32
    ) * curve_count + spec_index[forest].astype(np.int32)
    labels, block_count = _label_blocks(code_grid, forest)
    if block_count == 0:
        raise ScanfiEstateError("block labelling produced no blocks")

    positions = np.flatnonzero(forest)
    pixel_spec_index = spec_index.ravel()[positions].astype(np.int64)
    pixel_age = age.ravel()[positions].astype(np.int64)
    pixel_age_class = age_class.ravel()[positions].astype(np.int64)
    fragment_block_ids = _relabel_by_first_pixel(labels, positions, block_count)
    if fragment_block_ids.min() != 1 or fragment_block_ids.max() != block_count:
        raise ScanfiEstateError("relabelled block ids are not contiguous")

    flat_code = code_grid.ravel()[positions]
    first_position = np.full(block_count + 1, positions.size, dtype=np.int64)
    np.minimum.at(
        first_position, fragment_block_ids, np.arange(positions.size, dtype=np.int64)
    )
    block_code = flat_code[first_position[1:]]
    block_spec_index = block_code % curve_count
    block_age_class = (block_code // curve_count) * 10
    natural_track_ids = np.array(
        [2 * index + 1 for index in range(curve_count)], dtype=np.int64
    )
    if np.any(block_spec_index < 0) or np.any(block_spec_index >= curve_count):
        raise ScanfiEstateError("block curve index outside the resolved curve domain")

    fragment_count = positions.size
    fragment_area = np.full(fragment_count, PIXEL_AREA_HA, dtype=float)
    fragments = pd.DataFrame(
        {
            "fragment_id": np.arange(1, fragment_count + 1, dtype=np.int64),
            "block_id": fragment_block_ids.astype(np.int64),
            "area_ha": fragment_area,
            "age": pixel_age.astype(np.int64),
            "track_id": natural_track_ids[pixel_spec_index].astype(np.int64),
        }
    )
    block_pixels = np.bincount(fragment_block_ids, minlength=block_count + 1)[
        1:
    ].astype(np.int64)
    if int(block_pixels.sum()) != fragment_count:
        raise ScanfiEstateError(
            "fragment and block pixel counts do not conserve: "
            f"{int(block_pixels.sum())} != {fragment_count}"
        )
    block_area = block_pixels.astype(float) * PIXEL_AREA_HA
    blocks = pd.DataFrame(
        {
            "block_id": np.arange(1, block_count + 1, dtype=np.int64),
            "track_id": natural_track_ids[block_spec_index].astype(np.int64),
            "offset": block_age_class.astype(np.int64),
            "area_ha": block_area,
        }
    )
    groups = pd.DataFrame(
        {
            "block_id": np.arange(1, block_count + 1, dtype=np.int64),
            "group": [
                f"curve:{resolution.specs[index].key}" for index in block_spec_index
            ],
        }
    )

    clique_pixel_sizes = np.bincount(fragment_block_ids, minlength=block_count + 1)[1:]
    clique_summary = {
        "count": int(block_count),
        "pixels_mean": float(clique_pixel_sizes.mean()),
        "pixels_median": float(np.median(clique_pixel_sizes)),
        "pixels_p90": float(np.percentile(clique_pixel_sizes, 90)),
        "pixels_p99": float(np.percentile(clique_pixel_sizes, 99)),
        "pixels_max": int(clique_pixel_sizes.max()),
        "area_ha_mean": float(block_area.mean()),
        "area_ha_median": float(np.median(block_area)),
        "area_ha_max": float(block_area.max()),
        "singleton_blocks": int(np.count_nonzero(clique_pixel_sizes == 1)),
    }

    curve_usage = []
    for index, spec in enumerate(resolution.specs):
        pixel_count = int(np.count_nonzero(pixel_spec_index == index))
        curve_usage.append(
            {
                "curve_id": index + 2,
                "curve_key": spec.key,
                "model_id": spec.model_id,
                "species": spec.species,
                "source_species": spec.source_species,
                "selected_level": spec.selected_level,
                "fragments": pixel_count,
                "area_ha": round(pixel_count * PIXEL_AREA_HA, 10),
            }
        )
    age_distribution = []
    for age_class_value, count in zip(
        *np.unique(pixel_age_class, return_counts=True), strict=True
    ):
        age_distribution.append(
            {"age_class": int(age_class_value), "fragments": int(count)}
        )
    exclusions = {
        "outside_50km_rectangle": excluded_outside,
        "landcover_nodata": excluded_landcover_nodata,
        "age_nodata_in_forest_class": excluded_age_nodata,
        "nonforest_landcover": excluded_nonforest,
        "forest_class_age_nonpositive": excluded_nonpositive_age,
    }
    counts = {
        "window_pixels": int(age.size),
        "forest_fragments": fragment_count,
        "blocks": int(block_count),
        "forest_area_ha": round(fragment_count * PIXEL_AREA_HA, 10),
        "forest_area_km2": round(fragment_count * PIXEL_AREA_HA / 100.0, 6),
        "pixels_with_zero_species_closure": int(
            np.count_nonzero(forest & (max_closure == 0))
        ),
        "pixels_with_positive_species_closure": int(
            np.count_nonzero(forest & (max_closure > 0))
        ),
    }
    return Inventory(
        fragments=fragments,
        blocks=blocks,
        groups=groups,
        age_distribution=tuple(age_distribution),
        curve_usage=tuple(curve_usage),
        clique_summary=clique_summary,
        exclusions=exclusions,
        counts=counts,
    )


# ---------------------------------------------------------------------------
# WS4 tables.
# ---------------------------------------------------------------------------


def _track_name(
    *, tile_id: str, ifm: str, origin: str, silv_state: str, curve_key: str
) -> str:
    return (
        f"AU({tile_id})+IFM({ifm})+ORIGIN({origin})"
        f"+SILV_STATE({silv_state})+CURVE({curve_key})+treatment()"
    )


def build_ws4_tables(
    inventory: Inventory,
    *,
    tile_id: str,
    resolution: CurveResolution,
) -> dict[str, pd.DataFrame]:
    specs = resolution.specs
    track_rows: list[dict[str, Any]] = []
    strata_rows: list[dict[str, Any]] = []
    treatment_rows: list[dict[str, Any]] = []
    feature_rows: list[dict[str, Any]] = []
    product_rows: list[dict[str, Any]] = []
    curve_rows: list[dict[str, Any]] = [
        {
            "curve_id": AREA_CURVE_ID,
            "x": float(x),
            "y": 1.0,
        }
        for x in CURVE_DOMAIN
    ]

    for index, spec in enumerate(specs):
        natural_track = 2 * index + 1
        managed_track = natural_track + 1
        yield_curve_id = index + 2
        for x, y in zip(CURVE_DOMAIN, spec.curve_values(), strict=True):
            curve_rows.append(
                {"curve_id": yield_curve_id, "x": float(x), "y": float(y)}
            )
        track_rows.append(
            {
                "track_id": natural_track,
                "name": _track_name(
                    tile_id=tile_id,
                    ifm="unmanaged",
                    origin="natural",
                    silv_state="baseline",
                    curve_key=spec.key,
                ),
            }
        )
        track_rows.append(
            {
                "track_id": managed_track,
                "name": _track_name(
                    tile_id=tile_id,
                    ifm="managed",
                    origin="planted",
                    silv_state="cc_pl",
                    curve_key=spec.key,
                ),
            }
        )
        for track in (natural_track, managed_track):
            strata_rows.append(
                {"track_id": track, "offset": 0, "next_age": 300, "cycle": 0}
            )
        treatment_rows.append(
            {
                "track_id": natural_track,
                "begin": TREATMENT_BEGIN,
                "end": TREATMENT_END,
                "treatment": TREATMENT,
                "new_track": managed_track,
                "offset": 0,
                "retain": 0,
            }
        )
        treatment_rows.append(
            {
                "track_id": managed_track,
                "begin": TREATMENT_BEGIN,
                "end": TREATMENT_END,
                "treatment": TREATMENT,
                "new_track": managed_track,
                "offset": 0,
                "retain": 0,
            }
        )
        feature_rows.append(
            {
                "track_id": natural_track,
                "label": "feature.Yield.unmanaged.Total",
                "curve_id": yield_curve_id,
            }
        )
        feature_rows.append(
            {
                "track_id": managed_track,
                "label": "feature.Yield.managed.Total",
                "curve_id": yield_curve_id,
            }
        )
        for track, ifm in ((natural_track, "unmanaged"), (managed_track, "managed")):
            product_rows.append(
                {
                    "track_id": track,
                    "treatment": TREATMENT,
                    "label": f"product.HarvestedVolume.{ifm}.CC",
                    "curve_id": yield_curve_id,
                }
            )
            product_rows.append(
                {
                    "track_id": track,
                    "treatment": TREATMENT,
                    "label": f"product.Treated.{ifm}.CC",
                    "curve_id": AREA_CURVE_ID,
                }
            )

    tracks = pd.DataFrame(track_rows, columns=["track_id", "name"]).astype(
        {"track_id": "int64", "name": "string"}
    )
    strata = pd.DataFrame(
        strata_rows, columns=["track_id", "offset", "next_age", "cycle"]
    ).astype("int64")
    treatments = pd.DataFrame(
        treatment_rows,
        columns=[
            "track_id",
            "begin",
            "end",
            "treatment",
            "new_track",
            "offset",
            "retain",
        ],
    )
    treatments["treatment"] = treatments["treatment"].astype("string")
    frames = {
        "fragments": inventory.fragments,
        "blocks": inventory.blocks,
        "tracks": tracks,
        "strata": strata,
        "treatments": treatments,
        "curves": pd.DataFrame(curve_rows, columns=["curve_id", "x", "y"]).astype(
            {"curve_id": "int64", "x": "float64", "y": "float64"}
        ),
        "features": pd.DataFrame(
            feature_rows, columns=["track_id", "label", "curve_id"]
        ).astype({"track_id": "int64", "label": "string", "curve_id": "int64"}),
        "products": pd.DataFrame(
            product_rows,
            columns=["track_id", "treatment", "label", "curve_id"],
        ).astype(
            {
                "track_id": "int64",
                "treatment": "string",
                "label": "string",
                "curve_id": "int64",
            }
        ),
        "groups": inventory.groups.astype({"block_id": "int64", "group": "string"}),
    }
    account_labels = sorted(
        set(frames["features"]["label"].astype(str))
        | set(frames["products"]["label"].astype(str))
    )
    accounts = pd.DataFrame(
        {
            "group": ["_ALL_"] * len(account_labels),
            "attribute": account_labels,
            "account": account_labels,
            "sum": [1.0] * len(account_labels),
        }
    ).astype(
        {
            "group": "string",
            "attribute": "string",
            "account": "string",
            "sum": "float64",
        }
    )
    frames["protoaccounts"] = accounts.copy()
    frames["accounts"] = accounts.copy()
    frames["messages"] = pd.DataFrame(
        {
            "severity": ["info"],
            "message": [
                (
                    f"compiled {len(inventory.fragments)} SCANFI forest fragments "
                    f"into {len(inventory.blocks)} 8-neighbour blocks across "
                    f"{len(specs)} RSYC curves; "
                    "managed track shares the natural RSYC curve (no planting response)"
                )
            ],
        }
    ).astype({"severity": "string", "message": "string"})
    return frames


def _table_schema() -> dict[str, list[str]]:
    return {
        "fragments": ["fragment_id", "block_id", "area_ha", "age", "track_id"],
        "blocks": ["block_id", "track_id", "offset", "area_ha"],
        "tracks": ["track_id", "name"],
        "strata": ["track_id", "offset", "next_age", "cycle"],
        "treatments": [
            "track_id",
            "begin",
            "end",
            "treatment",
            "new_track",
            "offset",
            "retain",
        ],
        "curves": ["curve_id", "x", "y"],
        "features": ["track_id", "label", "curve_id"],
        "products": ["track_id", "treatment", "label", "curve_id"],
        "protoaccounts": ["group", "attribute", "account", "sum"],
        "accounts": ["group", "attribute", "account", "sum"],
        "groups": ["block_id", "group"],
        "messages": ["severity", "message"],
    }


def write_dataset(
    frames: Mapping[str, pd.DataFrame], dataset_dir: Path
) -> dict[str, Any]:
    dataset_dir.mkdir(parents=True, exist_ok=True)
    expected = _table_schema()
    for name, columns in expected.items():
        if name not in frames:
            raise ScanfiEstateError(f"missing WS4 table {name!r}")
        frame = frames[name]
        if list(frame.columns) != columns:
            raise ScanfiEstateError(
                f"table {name!r} columns {list(frame.columns)} != {columns}"
            )
        frame.to_parquet(dataset_dir / f"{name}.parquet", index=False)
    return expected


# ---------------------------------------------------------------------------
# Schedule and manifest.
# ---------------------------------------------------------------------------


def build_schedule(
    blocks: pd.DataFrame,
    *,
    sample_fraction: float = 0.1,
) -> pd.DataFrame:
    eligible = blocks[blocks["offset"] >= TREATMENT_BEGIN].sort_values("block_id")
    if eligible.empty:
        raise ScanfiEstateError("no blocks are eligible for the CC treatment")
    if len(eligible) < 5_000:
        sample = eligible
    else:
        step = max(1, round(1.0 / sample_fraction))
        sample = eligible.iloc[::step]
    if sample.empty:
        raise ScanfiEstateError("schedule sample is empty")
    return pd.DataFrame(
        {
            "block_id": sample["block_id"].to_numpy(dtype=np.int64),
            "treatment": [TREATMENT] * len(sample),
            "offset": [0] * len(sample),
        }
    )


def _source_records(paths: Iterable[Path]) -> list[dict[str, str]]:
    records = []
    for path in paths:
        if not path.is_file():
            raise ScanfiEstateError(f"source file is missing: {path}")
        records.append({"path": _repo_path(path), "sha256": sha256_file(path)})
    return records


def write_manifest(
    dataset_dir: Path,
    *,
    node: PilotNode,
    inventory: Inventory,
    resolution: CurveResolution,
    source_paths: Sequence[Path],
) -> dict[str, Any]:
    tables = {name: f"{name}.parquet" for name in _table_schema()}
    manifest = {
        "schema_version": "0.1",
        "tables": tables,
        "sources": _source_records(source_paths),
        "options": {
            "period_length": 1,
            "recipe": "SCANFI_RSYC_pilot",
            "model": "II",
            "resolution_m": PIXEL_SIZE_M,
            "fragment_area_ha": PIXEL_AREA_HA,
            "forest_landcover_classes": list(FOREST_LANDCOVER),
            "age_class_width_years": 10,
            "connectivity": "8-neighbour",
            "curve_domain": [int(CURVE_DOMAIN[0]), int(CURVE_DOMAIN[-1])],
            "managed_curve_same_as_natural": True,
            "planting_yield_response": "none: no data in this pilot",
        },
        "metadata": {
            **LABELS,
            "pilot": "ws4-national-estate-pilot-v1",
            "node_id": node.node_id,
            "parent_node_id": node.parent_node_id,
            "tile_id": node.tile_id,
            "province": node.province,
            "row": node.row,
            "col": node.col,
            "lat": node.lat,
            "lon": node.lon,
            "harvest_m3": node.harvest_m3,
            "subcell_area_ha": node.subcell_area_ha,
            "forest_fragments": inventory.counts["forest_fragments"],
            "blocks": inventory.counts["blocks"],
            "forest_area_ha": inventory.counts["forest_area_ha"],
            "curve_keys": [spec.key for spec in resolution.specs],
            "curve_specs": curve_spec_records(resolution),
            "curve_diagnostics": resolution.diagnostics,
            "resource_unit": "30-m SCANFI pixel",
            "area_basis": "pixel count x 0.09 ha",
            "ws4": ws4_provenance(),
            "built_utc": _utc_now(),
        },
    }
    dataset_dir.joinpath("manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return manifest


# ---------------------------------------------------------------------------
# WS4 validate/simulate.
# ---------------------------------------------------------------------------


def run_ws4_validate(dataset_dir: Path) -> dict[str, Any]:
    if not WS4_BIN.is_file():
        raise ScanfiEstateError(f"ws4 executable is missing: {WS4_BIN}")
    result = subprocess.run(
        [str(WS4_BIN), "validate", "--dataset", str(dataset_dir)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    payload = {
        "command": [str(WS4_BIN), "validate", "--dataset", str(dataset_dir)],
        "exit_code": int(result.returncode),
        "stdout": result.stdout,
        "stderr": result.stderr,
    }
    if result.returncode != 0:
        raise ScanfiEstateError(
            "ws4 validate failed:\n" + result.stdout + result.stderr
        )
    return payload


def run_ws4_simulate(
    dataset_dir: Path, schedule_path: Path, run_dir: Path, *, years: int = 50
) -> dict[str, Any]:
    if not WS4_BIN.is_file():
        raise ScanfiEstateError(f"ws4 executable is missing: {WS4_BIN}")
    run_dir.mkdir(parents=True, exist_ok=True)
    command = [
        str(WS4_BIN),
        "simulate",
        "--dataset",
        str(dataset_dir),
        "--schedule",
        str(schedule_path),
        "--years",
        str(years),
        "--out",
        str(run_dir),
    ]
    result = subprocess.run(
        command,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    payload: dict[str, Any] = {
        "command": command,
        "exit_code": int(result.returncode),
        "stdout": result.stdout,
        "stderr": result.stderr,
        "years": years,
    }
    if result.returncode != 0:
        raise ScanfiEstateError(
            "ws4 simulate failed:\n" + result.stdout + result.stderr
        )
    product_path = run_dir / "product_values.csv"
    account_path = run_dir / "account_values.csv"
    if not product_path.is_file():
        raise ScanfiEstateError(f"simulation did not write {product_path}")
    products = pd.read_csv(product_path)
    product_totals = (
        products.groupby("account", as_index=False)["value"]
        .sum()
        .sort_values("account")
    )
    harvested = product_totals[
        product_totals["account"].str.startswith("product.HarvestedVolume")
    ]
    treated = product_totals[
        product_totals["account"].str.startswith("product.Treated")
    ]
    harvested_volume = float(harvested["value"].sum())
    treated_area = float(treated["value"].sum())
    if harvested_volume <= 0.0:
        raise ScanfiEstateError("simulation produced zero harvested volume")
    if treated_area <= 0.0:
        raise ScanfiEstateError("simulation produced zero managed/treated area")
    payload["product_totals"] = product_totals.to_dict(orient="records")
    payload["harvested_volume_m3"] = harvested_volume
    payload["treated_area_ha"] = treated_area
    if account_path.is_file():
        accounts = pd.read_csv(account_path)
        last_offset = int(accounts["offset"].max())
        final = accounts[accounts["offset"] == last_offset]
        payload["final_offset"] = last_offset
        payload["final_feature_totals"] = (
            final[final["account"].str.startswith("feature.")]
            .groupby("account", as_index=False)["value"]
            .sum()
            .sort_values("account")
            .to_dict(orient="records")
        )
        payload["final_managed_yield_m3"] = float(
            final.loc[final["account"] == "feature.Yield.managed.Total", "value"].sum()
        )
        payload["final_unmanaged_yield_m3"] = float(
            final.loc[
                final["account"] == "feature.Yield.unmanaged.Total", "value"
            ].sum()
        )
    return payload


# ---------------------------------------------------------------------------
# Orchestration.
# ---------------------------------------------------------------------------


def write_pilot_readme(out_dir: Path, summary: Mapping[str, Any]) -> None:
    node = summary["node"]
    counts = summary["counts"]
    schedule = summary["schedule"]
    ws4 = summary["ws4"]
    lines = [
        f"# WS4 SCANFI/RSYC pilot — {node['node_id']}",
        "",
        "This directory is a real, compiled WS4 pilot built from SCANFI 30-m",
        "pixels and the parent RSYC tile production library. It is a",
        "`model_estimate` / `sensitivity_only` pilot, not official OSeCAN and",
        "not a chain v5.1 change.",
        "",
        "## Identity",
        "",
        f"- Node: `{node['node_id']}` (parent `{node['parent_node_id']}`)",
        f"- Tile / row / col: `{node['tile_id']}` / {node['row']} / {node['col']}",
        f"- Centroid: {node['lat']:.6f}, {node['lon']:.6f}",
        f"- Allocated harvest: {node['harvest_m3']:.6f} m3",
        f"- Productive subcell area: {node['subcell_area_ha']:.6f} ha",
        f"- Forest fragments: {counts['forest_fragments']}",
        f"- Blocks: {counts['blocks']}",
        f"- Forest area: {counts['forest_area_ha']:.2f} ha",
        f"- Resolution: {summary['resolution_m']:.0f} m (0.09 ha per fragment)",
        "",
        "## WS4 provenance",
        "",
        f"- Executable: `{ws4['executable']}`",
        f"- Editable source tree: `{ws4['editable_source_tree']}`",
        f"- Version: `{ws4['version']}`",
        "",
        "## Schedule",
        "",
        f"- Rows: {schedule['rows']}",
        f"- Scheduled area: {schedule['scheduled_area_ha']:.2f} ha",
        f"- Eligible blocks (age class >= 40): {schedule['eligible_blocks']}",
        "",
        "The managed track uses the same RSYC curve as the natural track. This",
        "pilot has no planting yield-response data; the managed track records",
        "the management-state transition only.",
        "",
        "See `inventory_summary.json` for exclusions, age distribution, curve",
        "usage, clique sizes, validate output, and simulation totals.",
        "",
    ]
    if "simulation" in summary:
        simulation = summary["simulation"]
        lines.extend(
            [
                "## Simulation",
                "",
                f"- Harvested volume: {simulation['harvested_volume_m3']:.3f} m3",
                f"- Treated/managed area: {simulation['treated_area_ha']:.3f} ha",
                f"- Final managed yield: {simulation.get('final_managed_yield_m3', 0.0):.3f} m3",
                "",
            ]
        )
    out_dir.joinpath("README.md").write_text("\n".join(lines), encoding="utf-8")


def build_pilot(
    *,
    out_dir: Path,
    sample_fraction: float = 0.1,
    years: int = 50,
    run_ws4: bool = False,
) -> dict[str, Any]:
    assert_no_unexpected_deletions()
    node = select_pilot_node()
    tile_geometry = load_tile_geometry(node.tile_id)
    polygon = subcell_polygon(tile_geometry, node.row, node.col)
    if not math.isclose(polygon.area / 10_000.0, 250_000.0, abs_tol=1e-3):
        raise ScanfiEstateError("reconstructed subcell is not 50 km x 50 km")
    intersection_ha = province_intersection_area(polygon, node.province)

    library = pd.read_csv(RSYC_LIBRARY_CSV)
    resolution = resolve_curves(library, tile_id=node.tile_id, province=node.province)
    (
        age,
        landcover,
        max_closure,
        dominant_species,
        window,
        species_paths,
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
    frames = build_ws4_tables(inventory, tile_id=node.tile_id, resolution=resolution)

    _safe_reset_output(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_gitattributes(out_dir)
    dataset_dir = out_dir / "dataset"
    write_dataset(frames, dataset_dir)
    source_paths = [
        AGE_RASTER,
        LANDCOVER_RASTER,
        *[species_paths[name] for name in SPECIES_TO_CODES],
        RSYC_LIBRARY_CSV,
        RSYC_TILES_GPKG,
        SUPPLY_NODES_CSV,
    ]
    write_manifest(
        dataset_dir,
        node=node,
        inventory=inventory,
        resolution=resolution,
        source_paths=source_paths,
    )

    schedule = build_schedule(frames["blocks"], sample_fraction=sample_fraction)
    schedules_dir = out_dir / "schedules"
    schedules_dir.mkdir(parents=True, exist_ok=True)
    schedule_path = schedules_dir / "pilot_cc.csv"
    schedule.to_csv(schedule_path, index=False, lineterminator="\n")
    scheduled_blocks = frames["blocks"].merge(
        schedule[["block_id"]], on="block_id", how="inner"
    )
    scheduled_area = float(scheduled_blocks["area_ha"].sum())

    summary: dict[str, Any] = {
        "pilot": "ws4-national-estate-pilot-v1",
        "built_utc": _utc_now(),
        "node": {
            "node_id": node.node_id,
            "parent_node_id": node.parent_node_id,
            "tile_id": node.tile_id,
            "province": node.province,
            "row": node.row,
            "col": node.col,
            "lat": node.lat,
            "lon": node.lon,
            "harvest_m3": node.harvest_m3,
            "subcell_area_ha": node.subcell_area_ha,
        },
        "geometry": {
            "subcell_bounds_epsg3978": list(polygon.bounds),
            "subcell_area_ha": polygon.area / 10_000.0,
            "subcell_province_intersection_area_ha": intersection_ha,
            "raster_crs": str(window.crs),
            "raster_window": {
                "col_off": int(window.window.col_off),
                "row_off": int(window.window.row_off),
                "width": int(window.window.width),
                "height": int(window.window.height),
            },
            "rasterized_pixels_inside_rectangle": window.inside_pixels,
        },
        "counts": inventory.counts,
        "exclusions": inventory.exclusions,
        "age_distribution": list(inventory.age_distribution),
        "curve_usage": list(inventory.curve_usage),
        "curves": curve_spec_records(resolution),
        "curve_diagnostics": resolution.diagnostics,
        "clique_summary": inventory.clique_summary,
        "tracks": frames["tracks"].to_dict(orient="records"),
        "treatments": frames["treatments"].to_dict(orient="records"),
        "schedule": {
            "path": _repo_path(schedule_path),
            "rows": len(schedule),
            "scheduled_blocks": len(scheduled_blocks),
            "scheduled_area_ha": scheduled_area,
            "eligible_blocks": int(
                np.count_nonzero(frames["blocks"]["offset"] >= TREATMENT_BEGIN)
            ),
        },
        "labels": LABELS,
        "resolution_m": PIXEL_SIZE_M,
        "fragment_count": len(frames["fragments"]),
        "block_count": len(frames["blocks"]),
        "ws4": ws4_provenance(),
    }
    if run_ws4:
        validation = run_ws4_validate(dataset_dir)
        summary["validation"] = validation
        run_dir = out_dir / "runs" / f"pilot_cc_{years}y"
        simulation = run_ws4_simulate(dataset_dir, schedule_path, run_dir, years=years)
        summary["simulation"] = simulation
        summary["simulation"]["expected_scheduled_area_ha"] = scheduled_area
        if not math.isclose(
            float(simulation["treated_area_ha"]),
            scheduled_area,
            rel_tol=1e-9,
            abs_tol=1e-6,
        ):
            raise ScanfiEstateError(
                "simulated treated area does not equal scheduled block area: "
                f"{simulation['treated_area_ha']} != {scheduled_area}"
            )
        summary["simulation"]["transition_evidence"] = (
            "all scheduled natural-track fragments transitioned to the managed "
            "track; product.Treated area equals scheduled block area"
        )

    out_dir.joinpath("inventory_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    write_pilot_readme(out_dir, summary)
    assert_no_unexpected_deletions()
    return summary


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help=(
            "Pilot output root (default: data/derived/ws4/scanfi-pilot-<selected-node>)"
        ),
    )
    parser.add_argument(
        "--sample-fraction",
        type=float,
        default=0.1,
        help="Fraction of eligible blocks to schedule (default: 0.1)",
    )
    parser.add_argument(
        "--years", type=int, default=50, help="Simulation horizon (default: 50)"
    )
    parser.add_argument(
        "--run-ws4",
        action="store_true",
        help="Run ws4 validate and simulate after writing the dataset",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    out_dir = args.out
    if out_dir is None:
        node = select_pilot_node()
        out_dir = WS4_OUT_ROOT / f"scanfi-pilot-{node.node_id.replace('|', '-')}"
    try:
        summary = build_pilot(
            out_dir=out_dir.resolve(),
            sample_fraction=args.sample_fraction,
            years=args.years,
            run_ws4=args.run_ws4,
        )
    except ScanfiEstateError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(json.dumps(summary["counts"], indent=2, sort_keys=True))
    print(f"dataset: {out_dir.resolve() / 'dataset'}")
    print(f"schedule rows: {summary['schedule']['rows']}")
    if "simulation" in summary:
        print(f"harvested volume m3: {summary['simulation']['harvested_volume_m3']}")
        print(f"treated area ha: {summary['simulation']['treated_area_ha']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
