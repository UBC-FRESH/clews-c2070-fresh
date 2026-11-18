"""Build the RSYC tile-production v0 national supply.

The tile production library holds 5,268 tile x province x species cells with
RSYC volume coefficients and SCANFI forest area.  This builder:

1. evaluates every RSYC curve at age 100 and multiplies by cell area to obtain
   total standing volume (this is the production library, not the diagnostic
   batch);
2. derives a province-level taper-residue factor expressed per m3 of *total*
   volume from the diagnostic national batch matrix;
3. applies the factor, the low/base/high roadside-recovery envelope, and the
   LHV/cost assumptions to obtain recoverable ODT, PJ, and cost;
4. aggregates nationally, by province, by tile, and by evidence tier, and
   builds the 100/150/200/250 million m3/yr harvest sensitivity.

Label: RSYC-only v0, no local corrections.  Benchmark correction factors are
withheld and deliberately not applied.

Fail-closed: malformed rows, non-finite coefficients, missing province
factors, or broken mass balances stop the build.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..rsyc_tile_contract import (
    EVIDENCE_TIER_LABELS,
    EVIDENCE_TIERS,
    require_known_allocation_basis,
    require_known_tier,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
OUTPUT_DIR = REPO_ROOT / "data/derived/national/clews_supply/rsyc_tile_production"

LIBRARY_PATH = (
    REPO_ROOT
    / "data/derived/national/assortments/rsyc_tile_production/rsyc_tile_production_library.csv"
)
LIBRARY_SUMMARY_PATH = (
    REPO_ROOT
    / "data/derived/national/assortments/rsyc_tile_production/rsyc_tile_production_summary.json"
)
TILE_WEIGHTS_PATH = (
    REPO_ROOT
    / "data/derived/national/assortments/rsyc_tile_production/tile_ecodistrict_weights.csv"
)
MATRIX_PATH = (
    REPO_ROOT
    / "data/derived/national/clews_supply/rsyc_backbone/matrix_v1"
    / "national_assortment_matrix.csv"
)
MATRIX_SHA256 = "1a805a995dea19754ffc78b610462d14aaf4b6fef0fa1cb7b5d785eff5780f09"
MATRIX_SHA256_PATH = MATRIX_PATH.with_suffix(".csv.sha256")
AREA_WEIGHTS_PATH = (
    REPO_ROOT
    / "data/derived/national/assortments/national_area_weights/national_area_weights_ecodistrict.csv"
)
BACKBONE_PROVINCE_PATH = (
    REPO_ROOT / "data/derived/national/clews_supply/rsyc_backbone/residue_factor_by_province.csv"
)
BACKBONE_HARVEST_PATH = (
    REPO_ROOT / "data/derived/national/clews_supply/rsyc_backbone/harvest_sensitivity.csv"
)

AGE_YEARS = 100.0
LHV_GJ_PER_ODT = 18.5
PJ_GJ = 1_000_000.0
BASE_COST_USD_PER_GJ = 2.456978
RECOVERY: Mapping[str, float] = {"low": 0.35, "base": 0.60, "high": 0.75}
COST_MULTIPLIER: Mapping[str, float] = {"low": 1.30, "base": 1.00, "high": 0.80}
CASES: tuple[str, ...] = ("low", "base", "high")
HARVEST_LEVELS_MILLION_M3: tuple[int, ...] = (100, 150, 200, 250)
BC_ONLY_PJ = 43.523816

EXPECTED_AREA_HA = 422_475_761.52243674
DIAGNOSTIC_TOTAL_VOLUME_M3 = 55_274_977_903.62324
DIAGNOSTIC_RESIDUE_VOLUME_M3 = 13_115_814_989.2
DIAGNOSTIC_RESIDUE_ODT = 4_925_060_250.174408
DIAGNOSTIC_EFFECTIVE_DENSITY_ODT_M3 = 0.375505468340474
DIAGNOSTIC_EMITTED_AREA_HA = 419_793_451.31629187
DIAGNOSTIC_MAPPED_AREA_HA = 422_475_761.06999743
DIAGNOSTIC_GAP_AREA_HA = 2_682_309.7537055016
DIAGNOSTIC_GAP_FRACTION = 0.0063490263841694955

EVIDENCE_ROWS: tuple[tuple[str, str], ...] = tuple(
    (tier, EVIDENCE_TIER_LABELS[tier]) for tier in EVIDENCE_TIERS
)


class TileSupplyError(RuntimeError):
    """Raised when an input invariant or mass balance fails."""


@dataclass(frozen=True)
class TileCell:
    tile_id: str
    province: str
    species: str
    species_kind: str
    evidence_tier: str
    area_ha: float
    v100_m3ha: float

    @property
    def volume_m3(self) -> float:
        return self.v100_m3ha * self.area_ha


@dataclass(frozen=True)
class MatrixCell:
    cell_key: str
    ecoprovince: str
    species: str
    area_ha: float
    total_volume_m3: float
    log_volume_m3: float
    residue_volume_m3: float
    residue_odt: float


@dataclass(frozen=True)
class ProvinceFactor:
    province: str
    total_volume_m3: float
    log_volume_m3: float
    residue_odt: float
    cells: int

    @property
    def odt_per_m3_total(self) -> float:
        return self.residue_odt / self.total_volume_m3

    @property
    def odt_per_m3_log(self) -> float:
        return self.residue_odt / self.log_volume_m3


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_float(value: str, *, field: str, source: Path) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise TileSupplyError(f"{source}: non-numeric {field}={value!r}") from exc
    if not math.isfinite(parsed):
        raise TileSupplyError(f"{source}: non-finite {field}={value!r}")
    return parsed


def require(condition: object, message: str) -> None:
    if not condition:
        raise TileSupplyError(message)


def rsyc_volume_at_age(b1: float, b2: float, b3: float, b4: float, age: float) -> float:
    """Published RSYC volume curve: b1*exp(-b4*age)*(1-exp(-b2*age))^b3."""
    volume = b1 * math.exp(-b4 * age) * (1.0 - math.exp(-b2 * age)) ** b3
    require(
        math.isfinite(volume) and volume >= 0.0,
        f"RSYC curve produced invalid volume {volume!r} at age {age}",
    )
    return volume


def load_library(path: Path) -> list[TileCell]:
    required_columns = {
        "cell_key",
        "tile_id",
        "province",
        "species",
        "species_kind",
        "evidence_tier",
        "allocation_basis",
        "area_ha",
        "no_province_intersection_area_ha",
        "b1",
        "b2",
        "b3",
        "b4",
        "volume_m3ha_age100",
    }
    cells: list[TileCell] = []
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        missing = sorted(required_columns.difference(reader.fieldnames or []))
        if missing:
            raise TileSupplyError(f"{path}: missing columns: {', '.join(missing)}")
        seen_cell_keys: set[str] = set()
        seen_logical_cells: set[tuple[str, str, str]] = set()
        for row in reader:
            cell_key = row["cell_key"].strip()
            tile_id = row["tile_id"].strip()
            province = row["province"].strip()
            species = row["species"].strip()
            require(
                cell_key and tile_id and province and species,
                f"{path}: blank source key fields in row {reader.line_num}",
            )
            require(
                cell_key == f"tile:{tile_id}|{province}|{species}",
                f"{path}: non-canonical cell_key {cell_key!r}",
            )
            require(
                cell_key not in seen_cell_keys,
                f"{path}: duplicate cell_key {cell_key!r}",
            )
            logical_cell = (tile_id, province, species)
            require(
                logical_cell not in seen_logical_cells,
                f"{path}: duplicate tile/province/species cell {logical_cell!r}",
            )
            seen_cell_keys.add(cell_key)
            seen_logical_cells.add(logical_cell)

            coefficients = {
                name: parse_float(row[name], field=name, source=path)
                for name in (
                    "b1",
                    "b2",
                    "b3",
                    "b4",
                    "area_ha",
                    "no_province_intersection_area_ha",
                )
            }
            require(
                coefficients["area_ha"] > 0.0,
                f"{path}: non-positive area at {cell_key}",
            )
            require(
                coefficients["b1"] > 0.0
                and coefficients["b2"] > 0.0
                and coefficients["b3"] > 0.0,
                f"{path}: non-positive b1/b2/b3 at {cell_key}",
            )
            require(
                coefficients["no_province_intersection_area_ha"] >= 0.0
                and coefficients["no_province_intersection_area_ha"]
                <= coefficients["area_ha"] + 1e-9,
                f"{path}: invalid no-province-intersection area at {cell_key}",
            )
            species_kind = row["species_kind"].strip()
            require(
                species_kind in {"species", "group"},
                f"{path}: unknown species_kind {species_kind!r} at {cell_key}",
            )
            require_known_tier(row["evidence_tier"].strip(), source=str(path))
            require_known_allocation_basis(
                row["allocation_basis"].strip(), source=str(path)
            )
            v100 = rsyc_volume_at_age(
                coefficients["b1"],
                coefficients["b2"],
                coefficients["b3"],
                coefficients["b4"],
                AGE_YEARS,
            )
            published = parse_float(
                row["volume_m3ha_age100"], field="volume_m3ha_age100", source=path
            )
            require(
                math.isclose(v100, published, rel_tol=1e-9, abs_tol=1e-9),
                f"{path}: V100 mismatch at {cell_key}: {v100!r} vs {published!r}",
            )
            cells.append(
                TileCell(
                    tile_id=tile_id,
                    province=province,
                    species=species,
                    species_kind=species_kind,
                    evidence_tier=row["evidence_tier"].strip(),
                    area_ha=coefficients["area_ha"],
                    v100_m3ha=v100,
                )
            )
    require(cells, f"{path}: no cells")
    return cells


def load_tile_areas(path: Path) -> dict[str, float]:
    """Load the published tile polygon areas used for capacity flags."""
    areas: dict[str, float] = {}
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        missing = sorted({"tile_id", "tile_area_ha"}.difference(reader.fieldnames or []))
        if missing:
            raise TileSupplyError(f"{path}: missing columns: {', '.join(missing)}")
        for row in reader:
            tile_id = row["tile_id"].strip()
            require(tile_id, f"{path}: blank tile_id")
            area = parse_float(row["tile_area_ha"], field="tile_area_ha", source=path)
            require(area > 0.0, f"{path}: non-positive tile area for {tile_id}")
            if tile_id in areas:
                require(
                    math.isclose(areas[tile_id], area, rel_tol=0.0, abs_tol=1e-6),
                    f"{path}: inconsistent tile_area_ha for {tile_id}",
                )
                continue
            areas[tile_id] = area
    require(areas, f"{path}: no tile areas")
    return areas


def load_matrix_cells(path: Path) -> list[MatrixCell]:
    required_columns = {
        "cell_key",
        "record_type",
        "level",
        "stratum_name",
        "species",
        "area_weight_ha",
        "volume_m3",
        "residue_volume_m3",
        "residue_odt",
    }
    totals: dict[str, MatrixCell] = {}
    log_volume: defaultdict[str, float] = defaultdict(float)
    log_rows: defaultdict[str, int] = defaultdict(int)
    residue_volume: defaultdict[str, float] = defaultdict(float)
    residue_odt: defaultdict[str, float] = defaultdict(float)
    residue_rows: defaultdict[str, int] = defaultdict(int)

    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        missing = sorted(required_columns.difference(reader.fieldnames or []))
        if missing:
            raise TileSupplyError(f"{path}: missing columns: {', '.join(missing)}")
        for row in reader:
            record_type = row["record_type"].strip()
            require(
                record_type in {"total", "log", "residue"},
                f"{path}: unknown record_type {record_type!r} at {row['cell_key']!r}",
            )
            cell_key = row["cell_key"].strip()
            require(cell_key, f"{path}: blank cell_key")
            volume_m3 = parse_float(row["volume_m3"], field="volume_m3", source=path)
            require(volume_m3 >= 0.0, f"{path}: negative volume_m3 at {cell_key}")
            if record_type == "log":
                log_volume[cell_key] += volume_m3
                log_rows[cell_key] += 1
                continue
            if record_type == "residue":
                component_odt = parse_float(
                    row["residue_odt"], field="residue_odt", source=path
                )
                require(
                    component_odt >= 0.0,
                    f"{path}: negative residue_odt at {cell_key}",
                )
                residue_volume[cell_key] += volume_m3
                residue_odt[cell_key] += component_odt
                residue_rows[cell_key] += 1
                continue

            require(
                cell_key not in totals,
                f"{path}: duplicate total cell_key {cell_key!r}",
            )
            require(
                row["level"].strip() == "ecoprovince",
                f"{path}: total cell {cell_key!r} is not ecoprovince level",
            )
            require(
                row["stratum_name"].strip() and row["species"].strip(),
                f"{path}: blank ecoprovince/species at {cell_key}",
            )
            area_ha = parse_float(
                row["area_weight_ha"], field="area_weight_ha", source=path
            )
            total_odt = parse_float(
                row["residue_odt"], field="residue_odt", source=path
            )
            total_residue_volume = parse_float(
                row["residue_volume_m3"], field="residue_volume_m3", source=path
            )
            require(area_ha > 0.0, f"{path}: non-positive area at {cell_key}")
            require(volume_m3 > 0.0, f"{path}: non-positive total volume at {cell_key}")
            require(total_odt >= 0.0, f"{path}: negative total residue_odt at {cell_key}")
            require(
                total_residue_volume >= 0.0,
                f"{path}: negative total residue_volume_m3 at {cell_key}",
            )
            totals[cell_key] = MatrixCell(
                cell_key=cell_key,
                ecoprovince=row["stratum_name"].strip(),
                species=row["species"].strip(),
                area_ha=area_ha,
                total_volume_m3=volume_m3,
                log_volume_m3=0.0,
                residue_volume_m3=total_residue_volume,
                residue_odt=total_odt,
            )

    require(totals, f"{path}: no total rows")
    orphan_components = sorted(
        (set(log_rows) | set(residue_rows)) - set(totals)
    )
    require(
        not orphan_components,
        f"{path}: {len(orphan_components)} component cell(s) have no total row",
    )

    cells: list[MatrixCell] = []
    for cell_key, total in totals.items():
        log_m3 = log_volume[cell_key]
        residue_m3 = residue_volume[cell_key]
        if log_rows[cell_key] == 0:
            require(
                math.isclose(
                    total.residue_volume_m3,
                    total.total_volume_m3,
                    rel_tol=1e-9,
                    abs_tol=1e-3,
                ),
                f"{path}: total cell {cell_key!r} has no log components and is "
                "not wholly residue (missing, not zero)",
            )
            log_m3 = 0.0
        if residue_rows[cell_key] == 0:
            require(
                math.isclose(total.residue_volume_m3, 0.0, abs_tol=1e-3)
                and math.isclose(total.residue_odt, 0.0, abs_tol=1e-6),
                f"{path}: total cell {cell_key!r} has no residue components and "
                "is not wholly log (missing, not zero)",
            )
            residue_m3 = 0.0
        require(
            log_m3 >= 0.0 and residue_m3 >= 0.0,
            f"{path}: negative component volume at {cell_key}",
        )
        require(
            math.isclose(
                total.total_volume_m3,
                log_m3 + residue_m3,
                rel_tol=1e-9,
                abs_tol=1e-3,
            ),
            f"{path}: total/log/residue volume does not close at {cell_key}: "
            f"{total.total_volume_m3} vs {log_m3 + residue_m3}",
        )
        require(
            math.isclose(
                total.residue_volume_m3,
                residue_m3,
                rel_tol=1e-9,
                abs_tol=1e-3,
            ),
            f"{path}: total residue volume does not match components at {cell_key}",
        )
        require(
            math.isclose(
                total.residue_odt,
                residue_odt[cell_key],
                rel_tol=1e-9,
                abs_tol=1e-6,
            ),
            f"{path}: total residue_odt does not match components at {cell_key}",
        )
        cells.append(
            MatrixCell(
                cell_key=cell_key,
                ecoprovince=total.ecoprovince,
                species=total.species,
                area_ha=total.area_ha,
                total_volume_m3=total.total_volume_m3,
                log_volume_m3=log_m3,
                residue_volume_m3=residue_m3,
                residue_odt=total.residue_odt,
            )
        )
    return cells


def load_province_species_area(path: Path) -> dict[tuple[str, str], dict[str, float]]:
    shares: dict[tuple[str, str], dict[str, float]] = defaultdict(lambda: defaultdict(float))
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        missing = sorted(
            {"ecoprovince", "rsyc_species", "province", "area_ha"}.difference(
                reader.fieldnames or []
            )
        )
        if missing:
            raise TileSupplyError(f"{path}: missing columns: {', '.join(missing)}")
        for row in reader:
            area = parse_float(row["area_ha"], field="area_ha", source=path)
            require(area >= 0.0, f"{path}: negative area_ha at {row}")
            province = row["province"].strip().upper()
            require(province, f"{path}: blank province")
            shares[(row["ecoprovince"].strip(), row["rsyc_species"].strip().upper())][
                province
            ] += area
    require(shares, f"{path}: no province species areas")
    empty_groups = sorted(
        key for key, values in shares.items() if sum(values.values()) <= 0.0
    )
    require(
        not empty_groups,
        f"{path}: province species groups with no positive area: {empty_groups[:3]}",
    )
    return shares


def allocate_province_factors(
    matrix_cells: Sequence[MatrixCell],
    species_area: Mapping[tuple[str, str], Mapping[str, float]],
) -> dict[str, ProvinceFactor]:
    buckets: dict[str, dict[str, float]] = defaultdict(
        lambda: {"total": 0.0, "log": 0.0, "residue": 0.0, "cells": 0.0}
    )
    for cell in matrix_cells:
        area_by_province = species_area.get((cell.ecoprovince, cell.species))
        if area_by_province is None:
            raise TileSupplyError(f"{cell.cell_key}: no species x province area weights")
        total_area = sum(area_by_province.values())
        require(
            math.isclose(total_area, cell.area_ha, rel_tol=1e-6, abs_tol=1e-3),
            f"{cell.cell_key}: species-province area {total_area} vs matrix area {cell.area_ha}",
        )
        require(
            cell.residue_odt >= 0.0,
            f"{cell.cell_key}: negative matrix residue",
        )
        for province, province_area in area_by_province.items():
            share = province_area / total_area
            bucket = buckets[province]
            bucket["total"] += cell.total_volume_m3 * share
            bucket["log"] += cell.log_volume_m3 * share
            bucket["residue"] += cell.residue_odt * share
            bucket["cells"] += 1

    factors: dict[str, ProvinceFactor] = {}
    for province, bucket in buckets.items():
        require(bucket["total"] > 0.0, f"{province}: non-positive allocated total volume")
        require(bucket["log"] > 0.0, f"{province}: non-positive allocated log volume")
        require(bucket["residue"] >= 0.0, f"{province}: negative allocated residue")
        require(
            math.isfinite(bucket["total"])
            and math.isfinite(bucket["log"])
            and math.isfinite(bucket["residue"]),
            f"{province}: non-finite allocated matrix quantity",
        )
        factors[province] = ProvinceFactor(
            province=province,
            total_volume_m3=bucket["total"],
            log_volume_m3=bucket["log"],
            residue_odt=bucket["residue"],
            cells=int(bucket["cells"]),
        )
    return factors


def load_backbone_province_factors(path: Path) -> dict[str, dict[str, float]]:
    factors: dict[str, dict[str, float]] = {}
    with path.open(newline="") as handle:
        for row in csv.DictReader(handle):
            factors[row["province"]] = {
                "residue_odt": parse_float(row["residue_odt"], field="residue_odt", source=path),
                "log_volume_m3": parse_float(
                    row["log_volume_m3"], field="log_volume_m3", source=path
                ),
                "odt_per_m3_log": parse_float(
                    row["odt_per_m3_log"], field="odt_per_m3_log", source=path
                ),
            }
    require(factors, f"{path}: no province factors")
    return factors


def load_backbone_harvest(path: Path) -> dict[tuple[int, str], dict[str, float]]:
    rows: dict[tuple[int, str], dict[str, float]] = {}
    with path.open(newline="") as handle:
        for line_number, row in enumerate(csv.DictReader(handle), start=2):
            key = (int(row["harvest_level_million_m3_per_year"]), row["case"])
            require(
                key not in rows,
                f"{path}:{line_number}: duplicate harvest comparison key {key}",
            )
            rows[key] = {
                "gross_residue_odt_per_year": parse_float(
                    row["gross_residue_odt_per_year"],
                    field="gross_residue_odt_per_year",
                    source=path,
                ),
                "recoverable_residue_odt_per_year": parse_float(
                    row["recoverable_residue_odt_per_year"],
                    field="recoverable_residue_odt_per_year",
                    source=path,
                ),
                "recoverable_pj_per_year": parse_float(
                    row["recoverable_pj_per_year"],
                    field="recoverable_pj_per_year",
                    source=path,
                ),
                "annual_cost_million_usd": parse_float(
                    row["annual_cost_million_usd"],
                    field="annual_cost_million_usd",
                    source=path,
                ),
            }
    require(rows, f"{path}: no harvest sensitivity rows")
    return rows


def recoverable(case: str, residue_odt: float) -> dict[str, float]:
    recovered_odt = residue_odt * RECOVERY[case]
    pj = recovered_odt * LHV_GJ_PER_ODT / PJ_GJ
    cost_per_gj = BASE_COST_USD_PER_GJ * COST_MULTIPLIER[case]
    return {
        "recovery_fraction": RECOVERY[case],
        "recoverable_odt": recovered_odt,
        "recoverable_pj": pj,
        "variable_cost_usd_per_gj": cost_per_gj,
        "annual_cost_million_usd": pj * cost_per_gj,
    }


def require_province_factors(
    cells: Sequence[TileCell], factors: Mapping[str, ProvinceFactor]
) -> None:
    missing = sorted({cell.province for cell in cells} - set(factors))
    require(not missing, f"library provinces missing residue factors: {missing}")


def aggregate_by_province(
    cells: Sequence[TileCell],
    factors: Mapping[str, ProvinceFactor],
    backbone: Mapping[str, Mapping[str, float]],
) -> list[dict[str, Any]]:
    require_province_factors(cells, factors)
    grouped: dict[str, list[TileCell]] = defaultdict(list)
    for cell in cells:
        grouped[cell.province].append(cell)

    national_volume = sum(cell.volume_m3 for cell in cells)
    national_residue = sum(
        cell.volume_m3 * factors[cell.province].odt_per_m3_total for cell in cells
    )
    rows: list[dict[str, Any]] = []
    for province in sorted(grouped):
        province_cells = grouped[province]
        area_ha = sum(cell.area_ha for cell in province_cells)
        volume_m3 = sum(cell.volume_m3 for cell in province_cells)
        factor = factors[province]
        residue_odt = volume_m3 * factor.odt_per_m3_total
        require(
            province in backbone,
            f"{province}: no diagnostic backbone province factor",
        )
        row: dict[str, Any] = {
            "province": province,
            "cells": len(province_cells),
            "area_ha": area_ha,
            "volume_m3": volume_m3,
            "volume_share": volume_m3 / national_volume,
            "mean_v100_m3ha": volume_m3 / area_ha if area_ha else 0.0,
            "residue_odt_per_m3_total": factor.odt_per_m3_total,
            "residue_odt_per_m3_log": factor.odt_per_m3_log,
            "factor_basis": "province",
            "residue_volume_m3": residue_odt / DIAGNOSTIC_EFFECTIVE_DENSITY_ODT_M3,
            "residue_odt": residue_odt,
            "residue_odt_share": residue_odt / national_residue if national_residue else 0.0,
            "diagnostic_residue_odt": backbone[province]["residue_odt"],
            "diagnostic_log_m3": backbone[province]["log_volume_m3"],
            "diagnostic_odt_per_m3_log": backbone[province]["odt_per_m3_log"],
        }
        row["residue_delta_pct_vs_diagnostic"] = (
            100.0
            * (residue_odt - backbone[province]["residue_odt"])
            / backbone[province]["residue_odt"]
        )
        for case in CASES:
            values = recoverable(case, residue_odt)
            for key, value in values.items():
                if key == "recovery_fraction":
                    continue
                if key == "annual_cost_million_usd":
                    row[f"standing_stock_cost_million_usd_{case}"] = value
                    continue
                row[f"{key}_{case}"] = value
        rows.append(row)
    return rows


def aggregate_by_tile(
    cells: Sequence[TileCell],
    factors: Mapping[str, ProvinceFactor],
    tile_areas: Mapping[str, float],
) -> list[dict[str, Any]]:
    require_province_factors(cells, factors)
    grouped: dict[str, list[TileCell]] = defaultdict(list)
    for cell in cells:
        grouped[cell.tile_id].append(cell)
    national_volume = sum(cell.volume_m3 for cell in cells)
    rows: list[dict[str, Any]] = []
    for tile_id in sorted(grouped):
        tile_cells = grouped[tile_id]
        area_ha = sum(cell.area_ha for cell in tile_cells)
        volume_m3 = sum(cell.volume_m3 for cell in tile_cells)
        residue_odt = sum(
            cell.volume_m3 * factors[cell.province].odt_per_m3_total
            for cell in tile_cells
        )
        require(tile_id in tile_areas, f"{tile_id}: no tile polygon area")
        polygon_area = tile_areas[tile_id]
        ratio = area_ha / polygon_area
        excess = max(area_ha - polygon_area, 0.0)
        rows.append(
            {
                "tile_id": tile_id,
                "provinces": ";".join(sorted({cell.province for cell in tile_cells})),
                "cells": len(tile_cells),
                "area_ha": area_ha,
                "tile_polygon_area_ha": polygon_area,
                "allocated_to_polygon_area_ratio": ratio,
                "excess_area_ha": excess,
                "over_capacity": ratio > 1.0 + 1e-4,
                "volume_m3": volume_m3,
                "volume_share": volume_m3 / national_volume,
                "mean_v100_m3ha": volume_m3 / area_ha if area_ha else 0.0,
                "residue_odt": residue_odt,
                "residue_odt_per_m3_total": residue_odt / volume_m3 if volume_m3 else 0.0,
                "recoverable_odt_base": residue_odt * RECOVERY["base"],
                "recoverable_pj_base": residue_odt * RECOVERY["base"] * LHV_GJ_PER_ODT / PJ_GJ,
            }
        )
    return rows


def aggregate_by_evidence(
    cells: Sequence[TileCell],
    factors: Mapping[str, ProvinceFactor],
) -> list[dict[str, Any]]:
    require_province_factors(cells, factors)
    national_area = sum(cell.area_ha for cell in cells)
    national_volume = sum(cell.volume_m3 for cell in cells)
    national_residue = sum(
        cell.volume_m3 * factors[cell.province].odt_per_m3_total for cell in cells
    )
    require(national_residue > 0.0, "national residue is non-positive")
    national_pj = national_residue * RECOVERY["base"] * LHV_GJ_PER_ODT / PJ_GJ
    grouped: dict[str, list[TileCell]] = defaultdict(list)
    for cell in cells:
        grouped[cell.evidence_tier].append(cell)
    rows: list[dict[str, Any]] = []
    for tier, label in EVIDENCE_ROWS:
        tier_cells = grouped.get(tier, [])
        area_ha = sum(cell.area_ha for cell in tier_cells)
        volume_m3 = sum(cell.volume_m3 for cell in tier_cells)
        residue_odt = sum(
            cell.volume_m3 * factors[cell.province].odt_per_m3_total
            for cell in tier_cells
        )
        pj = residue_odt * RECOVERY["base"] * LHV_GJ_PER_ODT / PJ_GJ
        rows.append(
            {
                "evidence_tier": tier,
                "tier_label": label,
                "cells": len(tier_cells),
                "area_ha": area_ha,
                "area_share": area_ha / national_area,
                "volume_m3": volume_m3,
                "volume_share": volume_m3 / national_volume,
                "residue_odt": residue_odt,
                "residue_odt_share": residue_odt / national_residue,
                "recoverable_odt_base": residue_odt * RECOVERY["base"],
                "recoverable_pj_base": pj,
                "recoverable_pj_share": pj / national_pj if national_pj else 0.0,
            }
        )
    return rows


HARVEST_SCENARIOS: tuple[tuple[str, str], ...] = (
    ("matrix_basis", "diagnostic_matrix_total_volume"),
    ("tile_mix", "tile_library_volume_weighted"),
)


def build_harvest_sensitivity(
    tile_mix_factor: float,
    matrix_factor: float,
    national_residue_odt: float,
    backbone_harvest: Mapping[tuple[int, str], Mapping[str, float]],
) -> list[dict[str, Any]]:
    """Build explicit matrix-basis and tile-mix harvest scenarios.

    Both factors are residue ODT per m3 of *total* volume.  Only the tile-mix
    row applies the factor to the tile library's own volume mix; the
    matrix-basis row is the diagnostic national factor and is published for
    comparison, not as the tile estimate.
    """
    national_pj = national_residue_odt * RECOVERY["base"] * LHV_GJ_PER_ODT / PJ_GJ
    scenario_factors = {
        "matrix_basis": matrix_factor,
        "tile_mix": tile_mix_factor,
    }
    rows: list[dict[str, Any]] = []
    for scenario, factor_source in HARVEST_SCENARIOS:
        factor = scenario_factors[scenario]
        require(
            math.isfinite(factor) and factor > 0.0,
            f"{scenario}: invalid residue factor {factor!r}",
        )
        for level in HARVEST_LEVELS_MILLION_M3:
            harvest_m3 = level * 1_000_000.0
            gross_residue = harvest_m3 * factor
            for case in CASES:
                values = recoverable(case, gross_residue)
                diagnostic = backbone_harvest.get((level, case))
                require(
                    diagnostic is not None,
                    f"{level} {case}: no diagnostic harvest row",
                )
                rows.append(
                    {
                        "scenario": scenario,
                        "harvest_volume_basis": "total_volume_m3_per_year",
                        "residue_factor_odt_per_m3": factor,
                        "factor_source": factor_source,
                        "harvest_level_million_m3_per_year": level,
                        "harvest_level_m3_per_year": harvest_m3,
                        "case": case,
                        "recovery_fraction": values["recovery_fraction"],
                        "gross_residue_odt_per_year": gross_residue,
                        "recoverable_residue_odt_per_year": values["recoverable_odt"],
                        "recoverable_pj_per_year": values["recoverable_pj"],
                        "variable_cost_usd_per_gj": values["variable_cost_usd_per_gj"],
                        "annual_cost_million_usd": values["annual_cost_million_usd"],
                        "diagnostic_recoverable_pj_per_year": diagnostic[
                            "recoverable_pj_per_year"
                        ],
                        "pj_delta_vs_diagnostic": values["recoverable_pj"]
                        - diagnostic["recoverable_pj_per_year"],
                        "pj_ratio_vs_diagnostic": values["recoverable_pj"]
                        / diagnostic["recoverable_pj_per_year"],
                        "ratio_to_bc_only_43_523816pj": values["recoverable_pj"]
                        / BC_ONLY_PJ,
                        "delta_to_bc_only_pj": values["recoverable_pj"] - BC_ONLY_PJ,
                        "hypothetical_all_hectares_age100_stock_pj_equivalent": (
                            national_pj
                        ),
                    }
                )
    return rows


def write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    require(rows, f"{path}: refusing to write an empty table")
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    key: repr(value) if isinstance(value, float) else value
                    for key, value in row.items()
                }
            )


def build_validation(
    cells: Sequence[TileCell],
    province_rows: Sequence[Mapping[str, Any]],
    tile_rows: Sequence[Mapping[str, Any]],
    evidence_rows: Sequence[Mapping[str, Any]],
    harvest_rows: Sequence[Mapping[str, Any]],
    factors: Mapping[str, ProvinceFactor],
    backbone: Mapping[str, Mapping[str, float]],
    tile_areas: Mapping[str, float],
) -> list[dict[str, str]]:
    area_ha = sum(cell.area_ha for cell in cells)
    volume_m3 = sum(cell.volume_m3 for cell in cells)
    residue_odt = sum(
        cell.volume_m3 * factors[cell.province].odt_per_m3_total for cell in cells
    )
    pj_base = residue_odt * RECOVERY["base"] * LHV_GJ_PER_ODT / PJ_GJ
    tile_weighted_factor = residue_odt / volume_m3

    national = {
        "area_ha": area_ha,
        "volume_m3": volume_m3,
        "residue_odt": residue_odt,
        "recoverable_pj_base": pj_base,
    }
    checks: list[dict[str, str]] = []

    def check(name: str, condition: bool, value: str, note: str = "") -> None:
        checks.append(
            {"check": name, "status": "PASS" if condition else "FAIL", "value": value, "note": note}
        )

    def rollup_error(
        rows: Sequence[Mapping[str, Any]], key: str
    ) -> float:
        return abs(sum(float(row[key]) for row in rows) - national[key])

    def check_rollup(
        name: str,
        rows: Sequence[Mapping[str, Any]],
        *,
        area_tolerance: float = 1e-3,
    ) -> None:
        errors = {
            "area_ha": rollup_error(rows, "area_ha"),
            "volume_m3": rollup_error(rows, "volume_m3"),
            "residue_odt": rollup_error(rows, "residue_odt"),
            "recoverable_pj_base": rollup_error(rows, "recoverable_pj_base"),
        }
        passed = (
            errors["area_ha"] <= area_tolerance
            and errors["volume_m3"] <= max(1e-3, volume_m3 * 1e-12)
            and errors["residue_odt"] <= max(1e-6, residue_odt * 1e-12)
            and errors["recoverable_pj_base"] <= max(1e-9, pj_base * 1e-12)
        )
        check(
            name,
            passed,
            (
                f"area {errors['area_ha']:.6g} ha, volume {errors['volume_m3']:.6g} m3, "
                f"ODT {errors['residue_odt']:.6g}, PJ {errors['recoverable_pj_base']:.6g}"
            ),
            "independent national totals from library cells",
        )

    def all_finite(rows: Sequence[Mapping[str, Any]]) -> bool:
        return all(
            math.isfinite(value)
            for row in rows
            for value in row.values()
            if isinstance(value, float)
        )

    def all_non_negative(
        rows: Sequence[Mapping[str, Any]], keys: Sequence[str]
    ) -> bool:
        return all(float(row[key]) >= 0.0 for row in rows for key in keys)

    check(
        "area_sum_matches_expected",
        math.isclose(area_ha, EXPECTED_AREA_HA, rel_tol=0, abs_tol=0.01),
        f"{area_ha:.6f} ha",
        f"expected {EXPECTED_AREA_HA:.6f} ha",
    )
    check(
        "v100_finite_non_negative",
        all(math.isfinite(cell.v100_m3ha) and cell.v100_m3ha >= 0.0 for cell in cells),
        f"{len(cells)} cells",
    )
    check("total_volume_positive", volume_m3 > 0.0, f"{volume_m3:.6f} m3")
    check(
        "residue_odt_finite_non_negative",
        math.isfinite(residue_odt) and residue_odt >= 0.0,
        f"{residue_odt:.6f} ODT",
    )
    check(
        "province_factors_complete",
        {cell.province for cell in cells} <= set(factors),
        f"{len(factors)} provinces",
    )
    check(
        "province_factors_match_backbone_keys",
        set(factors) == set(backbone),
        f"matrix {sorted(factors)} vs backbone {sorted(backbone)}",
    )
    check_rollup("province_rollup_reconciles_independently", province_rows)
    check_rollup("tile_rollup_reconciles_independently", tile_rows)
    check_rollup("evidence_rollup_reconciles_independently", evidence_rows)

    check(
        "rollup_values_finite_non_negative",
        all_finite(province_rows)
        and all_finite(tile_rows)
        and all_finite(evidence_rows)
        and all_non_negative(
            province_rows,
            ["area_ha", "volume_m3", "residue_odt", "recoverable_pj_base"],
        )
        and all_non_negative(
            tile_rows, ["area_ha", "volume_m3", "residue_odt", "recoverable_pj_base"]
        )
        and all_non_negative(
            evidence_rows,
            ["area_ha", "volume_m3", "residue_odt", "recoverable_pj_base"],
        ),
        "province, tile, evidence tables",
    )
    check(
        "evidence_shares_sum_to_one",
        math.isclose(sum(float(row["volume_share"]) for row in evidence_rows), 1.0, rel_tol=1e-9)
        and math.isclose(sum(float(row["area_share"]) for row in evidence_rows), 1.0, rel_tol=1e-9)
        and math.isclose(
            sum(float(row["residue_odt_share"]) for row in evidence_rows),
            1.0,
            rel_tol=1e-9,
        )
        and math.isclose(
            sum(float(row["recoverable_pj_share"]) for row in evidence_rows),
            1.0,
            rel_tol=1e-9,
        ),
        "volume, area, residue, PJ shares",
    )
    check(
        "four_evidence_tiers_present",
        [row["evidence_tier"] for row in evidence_rows] == list(EVIDENCE_TIERS),
        f"{len(evidence_rows)} tiers",
    )
    recomputed_log = {
        province: factor.residue_odt / factor.log_volume_m3 for province, factor in factors.items()
    }
    max_log_error = max(
        abs(recomputed_log[province] - backbone[province]["odt_per_m3_log"])
        / backbone[province]["odt_per_m3_log"]
        for province in recomputed_log
    )
    check(
        "province_log_factor_reproduces_backbone",
        max_log_error < 1e-7,
        f"max relative error {max_log_error:.3e}",
        "backbone CSV stores 9 decimals; agreement is rounding-level",
    )
    matrix_factor = sum(f.residue_odt for f in factors.values()) / sum(
        f.total_volume_m3 for f in factors.values()
    )
    check(
        "diagnostic_national_factor_reproduces_backbone",
        math.isclose(
            matrix_factor,
            DIAGNOSTIC_RESIDUE_ODT / DIAGNOSTIC_TOTAL_VOLUME_M3,
            rel_tol=1e-9,
        ),
        f"{matrix_factor:.12f} ODT/m3 total",
    )
    check(
        "no_nan_in_outputs",
        all_finite(province_rows)
        and all_finite(tile_rows)
        and all_finite(evidence_rows)
        and all_finite(harvest_rows),
        "all emitted aggregates finite",
        "province, tile, evidence, and harvest tables",
    )
    factor_values = [factor.odt_per_m3_total for factor in factors.values()]
    check(
        "tile_weighted_factor_within_province_range",
        min(factor_values) <= tile_weighted_factor <= max(factor_values),
        f"{tile_weighted_factor:.9f} ODT/m3 total",
        "mix-adjusted; differs from the matrix national factor through province volume composition",
    )

    b25 = next((row for row in tile_rows if row["tile_id"] == "B25"), None)
    check(
        "tile_capacity_flags_are_consistent",
        all(
            bool(row["over_capacity"])
            == (float(row["allocated_to_polygon_area_ratio"]) > 1.0 + 1e-4)
            and math.isclose(
                float(row["tile_polygon_area_ha"]),
                float(tile_areas[str(row["tile_id"])]),
                rel_tol=0.0,
                abs_tol=1e-6,
            )
            and float(row["excess_area_ha"])
            >= max(
                float(row["area_ha"]) - float(row["tile_polygon_area_ha"]),
                0.0,
            )
            - 1e-6
            for row in tile_rows
        ),
        f"{len(tile_rows)} tiles",
    )
    check(
        "b25_over_capacity_is_flagged_not_clipped",
        b25 is not None
        and bool(b25["over_capacity"])
        and float(b25["allocated_to_polygon_area_ratio"]) > 1.0
        and float(b25["excess_area_ha"]) > 0.0,
        (
            "B25 missing"
            if b25 is None
            else f"ratio {float(b25['allocated_to_polygon_area_ratio']):.6f}, "
            f"excess {float(b25['excess_area_ha']):.3f} ha"
        ),
    )

    harvest_by_key = {
        (
            row["scenario"],
            int(row["harvest_level_million_m3_per_year"]),
            row["case"],
        ): row
        for row in harvest_rows
    }
    harvest_expected = len(HARVEST_SCENARIOS) * len(HARVEST_LEVELS_MILLION_M3) * len(CASES)
    check(
        "harvest_scenarios_are_complete_and_basis_labelled",
        len(harvest_rows) == harvest_expected
        and len(harvest_by_key) == harvest_expected
        and all(
            row["harvest_volume_basis"] == "total_volume_m3_per_year"
            and row["scenario"] in {name for name, _ in HARVEST_SCENARIOS}
            for row in harvest_rows
        ),
        f"{len(harvest_rows)} rows",
    )
    harvest_recompute_errors: list[float] = []
    for row in harvest_rows:
        expected_pj = (
            float(row["gross_residue_odt_per_year"])
            * float(row["recovery_fraction"])
            * LHV_GJ_PER_ODT
            / PJ_GJ
        )
        harvest_recompute_errors.append(
            abs(float(row["recoverable_pj_per_year"]) - expected_pj)
        )
    check(
        "harvest_rows_recompute_from_factor_and_recovery",
        max(harvest_recompute_errors, default=0.0) < 1e-9,
        f"max error {max(harvest_recompute_errors, default=0.0):.3e} PJ",
    )
    tile_harvest = harvest_by_key.get(("tile_mix", 100, "base"))
    matrix_harvest = harvest_by_key.get(("matrix_basis", 100, "base"))
    check(
        "tile_mix_scenario_uses_tile_weighted_factor",
        tile_harvest is not None
        and math.isclose(
            float(tile_harvest["residue_factor_odt_per_m3"]),
            tile_weighted_factor,
            rel_tol=1e-12,
        ),
        (
            "missing"
            if tile_harvest is None
            else f"{float(tile_harvest['residue_factor_odt_per_m3']):.12f} ODT/m3"
        ),
    )
    check(
        "matrix_scenario_uses_matrix_factor",
        matrix_harvest is not None
        and math.isclose(
            float(matrix_harvest["residue_factor_odt_per_m3"]),
            matrix_factor,
            rel_tol=1e-12,
        ),
        (
            "missing"
            if matrix_harvest is None
            else f"{float(matrix_harvest['residue_factor_odt_per_m3']):.12f} ODT/m3"
        ),
    )
    return checks


def build_report(summary: Mapping[str, Any]) -> str:
    national = summary["national"]
    comparison = summary["comparison"]
    province_rows = summary["province_table"]
    evidence_rows = summary["evidence_table"]
    harvest_rows = summary["harvest_sensitivity"]
    validation = summary["validation"]
    factors = summary["province_factors"]
    caveats = summary["caveats"]
    b25 = comparison["b25_anomaly"]

    def pct(value: float) -> str:
        return f"{100.0 * value:.2f}%"

    province_lines = [
        "| Province | Area (Mha) | Volume (B m3) | ODT/m3 total | Residue (Mt ODT) | Recoverable PJ base | Diagnostic residue (Mt ODT) | Delta |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in province_rows:
        province = row["province"]
        province_lines.append(
            f"| {province} | {row['area_ha'] / 1e6:.2f} | {row['volume_m3'] / 1e9:.3f} | "
            f"{float(factors[province]):.6f} | {row['residue_odt'] / 1e6:.1f} | "
            f"{row['recoverable_pj_base']:.2f} | {row['diagnostic_residue_odt'] / 1e6:.1f} | "
            f"{row['residue_delta_pct_vs_diagnostic']:+.2f}% |"
        )
    province_lines.append(
        f"| **Total** | {national['area_ha'] / 1e6:.2f} | {national['volume_m3'] / 1e9:.3f} | "
        f"{comparison['tile_weighted_odt_per_m3_total']:.6f} | {national['residue_odt'] / 1e6:.1f} | "
        f"{national['recoverable_pj_base']:.2f} | {comparison['diagnostic_residue_odt'] / 1e6:.1f} | "
        f"{comparison['residue_vs_diagnostic_pct']:+.2f}% |"
    )

    evidence_lines = [
        "| Tier | Cells | Area share | Volume share | Residue share | PJ share |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in evidence_rows:
        evidence_lines.append(
            f"| {row['tier_label']} | {row['cells']} | {pct(row['area_share'])} | "
            f"{pct(row['volume_share'])} | {pct(row['residue_odt_share'])} | "
            f"{pct(row['recoverable_pj_share'])} |"
        )

    harvest_lines = [
        "| Scenario | Volume basis | Harvest (M m3/yr) | Case | Factor (ODT/m3) | Recoverable PJ/yr | Diagnostic PJ/yr | vs BC 43.52 PJ |",
        "|---|---|---:|---|---:|---:|---:|---:|",
    ]
    for row in harvest_rows:
        harvest_lines.append(
            f"| {row['scenario']} | {row['harvest_volume_basis']} | "
            f"{row['harvest_level_million_m3_per_year']} | {row['case']} | "
            f"{row['residue_factor_odt_per_m3']:.9f} | "
            f"{row['recoverable_pj_per_year']:.2f} | "
            f"{row['diagnostic_recoverable_pj_per_year']:.2f} | "
            f"{row['ratio_to_bc_only_43_523816pj']:.3f} |"
        )

    validation_lines = [
        "| Check | Status | Value |",
        "|---|---|---|",
    ]
    for row in validation:
        validation_lines.append(f"| {row['check']} | {row['status']} | {row['value']} |")

    caveat_lines = [f"- {caveat}" for caveat in caveats]
    if b25 is None:
        b25_text = "B25 is absent from the tile library."
    else:
        b25_text = (
            f"B25 carries {b25['allocated_area_ha']:,.3f} ha against a "
            f"{b25['tile_polygon_area_ha']:,.3f} ha polygon "
            f"(area/polygon_area {b25['area_over_polygon_area']:.6f}, "
            f"excess {b25['excess_area_ha']:,.3f} ha). It is flagged "
            "`over_capacity=true` in `rsyc_tile_supply_by_tile.csv` and the "
            "summary, not clipped."
        )

    return f"""# RSYC Tile-Production v0 National Supply

**Label: RSYC-only v0, no local corrections.** Benchmark correction factors are withheld and were not applied.

Generated {summary['generated_utc']} from the tile-level production library
(`{summary['inputs']['library']['path']}`), {national['cells']:,} tile x province x species cells
covering {national['area_ha'] / 1e6:.2f} Mha.

## Method

- V100 = b1*exp(-b4*100)*(1-exp(-b2*100))^b3 (m3/ha), evaluated per cell; total volume = V100 * area_ha.
- Province residue factors are derived from the diagnostic batch matrix on a **total-volume basis**:
  sum(residue_odt) / sum(volume_m3) after species x ecoprovince area apportionment to provinces.
- Species and group cells use the province factor; no species-specific factor is invented.
- The province factor is applied uniformly to species and group cells and is independent of evidence tier;
  tiers are reported for transparency, not used to weight residue.
- Recovery 0.35 / 0.60 / 0.75; LHV 18.5 GJ/ODT; cost $2.456978/GJ base, 1.30x low, 0.80x high.

## V100 and volume

- National standing volume: **{national['volume_m3'] / 1e9:.6f} B m3** on {national['area_ha'] / 1e6:.2f} Mha
  (mean {national['mean_v100_m3ha']:.1f} m3/ha).
- Diagnostic batch total volume: {comparison['diagnostic_total_volume_m3'] / 1e9:.6f} B m3.
  Tile library volume differs by {comparison['volume_vs_diagnostic_pct']:+.3f}% (production library, not the diagnostic batch).

### By province

{chr(10).join(province_lines)}

### By evidence tier

{chr(10).join(evidence_lines)}

## Residue factors (m3 total volume basis vs log basis)

- Matrix national total-basis factor: **{comparison['diagnostic_odt_per_m3_total']:.9f} ODT/m3**;
  diagnostic log-basis factor {comparison['diagnostic_odt_per_m3_log']:.9f} ODT/m3
  (total/log ratio {comparison['factor_total_over_log']:.4f}).
- Tile-weighted factor after applying province factors to tile-library volume:
  **{comparison['tile_weighted_odt_per_m3_total']:.9f} ODT/m3**. It is below the matrix
  national factor through province volume composition: the tile library holds a higher volume
  share in ON and a lower share in SK than the diagnostic matrix, and ON has a low factor
  while SK has a high factor.
- A total-basis factor is the correct multiplier for RSYC gross volume; the diagnostic batch splits
  {comparison['diagnostic_total_volume_m3'] / 1e9:.3f} B m3 into
  {comparison['diagnostic_log_volume_m3'] / 1e9:.3f} B m3 log and
  {comparison['diagnostic_residue_volume_m3'] / 1e9:.3f} B m3 residue.
- Province factors range {comparison['province_factor_min']:.6f} ({comparison['province_factor_min_province']}) to
  {comparison['province_factor_max']:.6f} ({comparison['province_factor_max_province']}) ODT/m3 total.
- Province factors are area-share apportionments of ecoprovince taper models, not province-specific fits.

## Supply (standing stock at age 100)

- Gross residue: **{national['residue_odt'] / 1e9:.6f} B ODT**
  ({national['residue_volume_m3'] / 1e9:.6f} B m3 at the diagnostic effective density
  {DIAGNOSTIC_EFFECTIVE_DENSITY_ODT_M3:.6f} ODT/m3).
- Recoverable ODT: low {national['recoverable_odt_low'] / 1e9:.6f} B, base
  {national['recoverable_odt_base'] / 1e9:.6f} B, high {national['recoverable_odt_high'] / 1e9:.6f} B.
- Recoverable energy (one-time standing stock): low {national['recoverable_pj_low']:.3f} PJ, base
  **{national['recoverable_pj_base']:.3f} PJ**, high {national['recoverable_pj_high']:.3f} PJ.
- The **{national['hypothetical_all_hectares_age100_stock_pj_equivalent']:.3f} PJ** figure is a
  hypothetical all-hectares-at-age-100 standing-stock equivalent, **not an annual cap** and not an
  adopted harvest level.
- Equivalent per 100 million m3 of total volume on the tile mix: low
  {national['recoverable_pj_per_100_million_m3_tile_mix_low']:.2f} PJ, base
  {national['recoverable_pj_per_100_million_m3_tile_mix_base']:.2f} PJ, high
  {national['recoverable_pj_per_100_million_m3_tile_mix_high']:.2f} PJ.
- Base variable cost: {national['variable_cost_usd_per_gj_base']:.6f} $/GJ; standing-stock cost
  equivalent {national['standing_stock_cost_million_usd_base']:.1f} M$ (one-time, not annual).
- Diagnostic batch residue: {comparison['diagnostic_residue_odt'] / 1e9:.6f} B ODT; tile library
  differs by {comparison['residue_vs_diagnostic_pct']:+.3f}%.

## Harvest sensitivity (matrix-basis vs tile-mix)

Both scenarios use residue ODT per m3 of **total** volume. The matrix-basis rows reproduce the
diagnostic national factor; the tile-mix rows apply the tile library's own volume-weighted factor
and are the tile production estimate. Every row states its volume basis and factor source in the CSV.

{chr(10).join(harvest_lines)}

The annual rows assume a harvested volume and mix on a total-volume basis; **no adopted national
AAC, THLB, or operability filter is applied**. The BC-only 43.523816 PJ comparison is a scale
comparison, not a like-for-like annual supply comparison.

## B25 and tile capacity

{b25_text}

## Validation

{chr(10).join(validation_lines)}

- Area sum: {national['area_ha']:.2f} ha (expected 422,475,761.52 ha).
- Province, tile, and evidence rollups are each reconciled to independent national area, volume,
  residue ODT, and PJ totals.

## Caveats

{chr(10).join(caveat_lines)}
"""


def verify_matrix_file(path: Path) -> str:
    """Verify the durable matrix copy against the pinned hash and sidecar."""
    require(path.exists(), f"durable diagnostic matrix not found: {path}")
    digest = sha256(path)
    require(
        digest == MATRIX_SHA256,
        f"{path}: sha256 {digest} does not match pinned {MATRIX_SHA256}",
    )
    sidecar = path.with_suffix(".csv.sha256")
    require(sidecar.exists(), f"matrix checksum sidecar not found: {sidecar}")
    recorded = sidecar.read_text(encoding="utf-8").split()[0]
    require(
        recorded == digest,
        f"{sidecar}: recorded sha256 {recorded} does not match {digest}",
    )
    return digest


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    generated_utc = datetime.now(UTC).isoformat(timespec="seconds")

    matrix_digest = verify_matrix_file(MATRIX_PATH)
    cells = load_library(LIBRARY_PATH)
    tile_areas = load_tile_areas(TILE_WEIGHTS_PATH)
    matrix_cells = load_matrix_cells(MATRIX_PATH)
    species_area = load_province_species_area(AREA_WEIGHTS_PATH)
    factors = allocate_province_factors(matrix_cells, species_area)
    backbone = load_backbone_province_factors(BACKBONE_PROVINCE_PATH)
    backbone_harvest = load_backbone_harvest(BACKBONE_HARVEST_PATH)

    require(
        set(backbone) == set(factors),
        f"province sets differ: matrix {sorted(factors)} vs backbone {sorted(backbone)}",
    )

    matrix_factor = sum(f.residue_odt for f in factors.values()) / sum(
        f.total_volume_m3 for f in factors.values()
    )

    province_rows = aggregate_by_province(cells, factors, backbone)
    tile_rows = aggregate_by_tile(cells, factors, tile_areas)
    evidence_rows = aggregate_by_evidence(cells, factors)

    national_volume = sum(cell.volume_m3 for cell in cells)
    national_area = sum(cell.area_ha for cell in cells)
    national_residue = sum(float(row["residue_odt"]) for row in province_rows)
    tile_mix_factor = national_residue / national_volume
    national_pj = {
        case: national_residue * RECOVERY[case] * LHV_GJ_PER_ODT / PJ_GJ for case in CASES
    }
    national_cost = {
        case: national_pj[case] * BASE_COST_USD_PER_GJ * COST_MULTIPLIER[case] for case in CASES
    }

    harvest_rows = build_harvest_sensitivity(
        tile_mix_factor, matrix_factor, national_residue, backbone_harvest
    )
    validation = build_validation(
        cells,
        province_rows,
        tile_rows,
        evidence_rows,
        harvest_rows,
        factors,
        backbone,
        tile_areas,
    )
    failed = [check for check in validation if check["status"] != "PASS"]
    require(not failed, f"validation failed: {failed}")

    diagnostic_log_volume = sum(f.log_volume_m3 for f in factors.values())
    diagnostic_total_volume = sum(f.total_volume_m3 for f in factors.values())
    diagnostic_residue = sum(f.residue_odt for f in factors.values())
    diagnostic_log_factor = diagnostic_residue / diagnostic_log_volume
    factor_min_province = min(factors, key=lambda p: factors[p].odt_per_m3_total)
    factor_max_province = max(factors, key=lambda p: factors[p].odt_per_m3_total)

    selected_tiers = sorted({cell.evidence_tier for cell in cells})
    tier_area = defaultdict(float)
    tier_cells = defaultdict(int)
    for cell in cells:
        tier_area[cell.evidence_tier] += cell.area_ha
        tier_cells[cell.evidence_tier] += 1
    selected_tier_rows = [
        {
            "evidence_tier": tier,
            "tier_label": EVIDENCE_TIER_LABELS[tier],
            "cells": tier_cells[tier],
            "area_ha": tier_area[tier],
            "area_share": tier_area[tier] / national_area,
        }
        for tier in selected_tiers
    ]

    b25_row = next((row for row in tile_rows if row["tile_id"] == "B25"), None)
    over_capacity_rows = [row for row in tile_rows if row["over_capacity"]]
    b25_anomaly = (
        None
        if b25_row is None
        else {
            "tile_id": "B25",
            "allocated_area_ha": b25_row["area_ha"],
            "tile_polygon_area_ha": b25_row["tile_polygon_area_ha"],
            "area_over_polygon_area": b25_row["allocated_to_polygon_area_ratio"],
            "excess_area_ha": b25_row["excess_area_ha"],
            "over_capacity": True,
            "note": (
                "inherited from SCANFI forest area exceeding ecodistrict "
                "polygon area; flagged, not clipped"
            ),
        }
    )

    caveats = [
        "RSYC-only v0: no local corrections, benchmark correction factors withheld.",
        (
            "Annual harvest rows assume a harvested volume and mix expressed on a "
            "total-volume basis; no adopted national AAC, THLB, or operability "
            "filter is applied."
        ),
        (
            "The BC-only 43.523816 PJ value is a scale comparison, not a "
            "like-for-like annual supply comparison."
        ),
        (
            "The national all-hectares-at-age-100 stock equivalent is a "
            "hypothetical one-time standing-stock figure, not an annual cap."
        ),
        (
            "The 0.64% diagnostic gap (419.79 of 422.48 Mha emitted; failed PINU.BAN Great Bear Lowlands "
            "cell plus 0.69 Mha other gap) does not apply to the tile library, which covers all area."
        ),
        "Residue is taper/bucking residue scaled from the diagnostic batch, not full aboveground slash.",
        "Province residue factors are area-share apportionments, not province-specific taper models.",
        "Recovery (0.35/0.60/0.75) and cost ($2.456978/GJ, 1.30x/0.80x) are uniform envelopes.",
    ]
    if "rsyc_tile_group" in tier_area:
        caveats.append(
            "Group cells (BROADLEAF, CONIFEROUS) use group RSYC curves; they are "
            "flagged by evidence_tier=rsyc_tile_group and are not species-exact fits."
        )
    if "rsyc_tile_generic" in tier_area:
        caveats.append(
            "Generic cells use the tile generic RSYC curve; they are flagged by "
            "evidence_tier=rsyc_tile_generic."
        )
    if "rsyc_tile_control_fallback" in tier_area:
        caveats.append(
            f"{tier_cells['rsyc_tile_control_fallback']:,} cells "
            f"({tier_area['rsyc_tile_control_fallback'] / 1e6:.2f} Mha) have no "
            "tile curve and borrow a dominant ecoregion/ecoprovince control "
            "curve; they are flagged by evidence_tier=rsyc_tile_control_fallback."
        )

    summary = {
        "artifact_version": "0.1.0",
        "label": "RSYC-only v0, no local corrections",
        "status": "derived_provisional",
        "generated_utc": generated_utc,
        "output_dir": str(OUTPUT_DIR.relative_to(REPO_ROOT)),
        "inputs": {
            "library": {
                "path": str(LIBRARY_PATH.relative_to(REPO_ROOT)),
                "sha256": sha256(LIBRARY_PATH),
                "bytes": LIBRARY_PATH.stat().st_size,
            },
            "library_summary": {
                "path": str(LIBRARY_SUMMARY_PATH.relative_to(REPO_ROOT)),
                "sha256": sha256(LIBRARY_SUMMARY_PATH),
            },
            "tile_weights": {
                "path": str(TILE_WEIGHTS_PATH.relative_to(REPO_ROOT)),
                "sha256": sha256(TILE_WEIGHTS_PATH),
            },
            "diagnostic_matrix": {
                "path": str(MATRIX_PATH.relative_to(REPO_ROOT)),
                "sha256": matrix_digest,
                "pinned_sha256": MATRIX_SHA256,
                "version": "matrix_v1",
            },
            "province_area_weights": {
                "path": str(AREA_WEIGHTS_PATH.relative_to(REPO_ROOT)),
                "sha256": sha256(AREA_WEIGHTS_PATH),
            },
            "backbone_province_factors": {
                "path": str(BACKBONE_PROVINCE_PATH.relative_to(REPO_ROOT)),
                "sha256": sha256(BACKBONE_PROVINCE_PATH),
            },
            "backbone_harvest": {
                "path": str(BACKBONE_HARVEST_PATH.relative_to(REPO_ROOT)),
                "sha256": sha256(BACKBONE_HARVEST_PATH),
            },
        },
        "method": {
            "age_years": AGE_YEARS,
            "volume_equation": "b1*exp(-b4*age)*(1-exp(-b2*age))^b3",
            "volume_source": "rsyc_tile_production_library coefficients (not diagnostic batch)",
            "residue_factor_basis": (
                "province sum(residue_odt)/sum(total volume_m3) from diagnostic matrix; "
                "separate matrix-basis and tile-mix harvest scenarios"
            ),
            "province_allocation": "species-resolved SCANFI area within ecoprovince",
            "group_cells": "province factor applied; no species-specific factor",
            "harvest_volume_basis": "total_volume_m3_per_year",
            "harvest_volume_assumption": (
                "annual rows assume a harvested total-volume mix; no adopted "
                "national AAC, THLB, or operability filter"
            ),
            "bc_comparison": "scale comparison only; not like-for-like",
            "recovery": RECOVERY,
            "lhv_gj_per_odt": LHV_GJ_PER_ODT,
            "base_cost_usd_per_gj": BASE_COST_USD_PER_GJ,
            "cost_multiplier": COST_MULTIPLIER,
            "benchmark_correction_applied": False,
        },
        "national": {
            "cells": len(cells),
            "tiles": len({cell.tile_id for cell in cells}),
            "provinces": len({cell.province for cell in cells}),
            "area_ha": national_area,
            "volume_m3": national_volume,
            "mean_v100_m3ha": national_volume / national_area,
            "residue_odt_per_m3_total": tile_mix_factor,
            "matrix_national_odt_per_m3_total": matrix_factor,
            "residue_volume_m3": national_residue / DIAGNOSTIC_EFFECTIVE_DENSITY_ODT_M3,
            "residue_odt": national_residue,
            "recoverable_odt_low": national_residue * RECOVERY["low"],
            "recoverable_odt_base": national_residue * RECOVERY["base"],
            "recoverable_odt_high": national_residue * RECOVERY["high"],
            "recoverable_pj_low": national_pj["low"],
            "recoverable_pj_base": national_pj["base"],
            "recoverable_pj_high": national_pj["high"],
            "recoverable_pj_per_100_million_m3_tile_mix_low": tile_mix_factor * 1e8 * RECOVERY["low"] * LHV_GJ_PER_ODT / PJ_GJ,
            "recoverable_pj_per_100_million_m3_tile_mix_base": tile_mix_factor * 1e8 * RECOVERY["base"] * LHV_GJ_PER_ODT / PJ_GJ,
            "recoverable_pj_per_100_million_m3_tile_mix_high": tile_mix_factor * 1e8 * RECOVERY["high"] * LHV_GJ_PER_ODT / PJ_GJ,
            "hypothetical_all_hectares_age100_stock_pj_equivalent": national_pj["base"],
            "variable_cost_usd_per_gj_base": BASE_COST_USD_PER_GJ,
            "standing_stock_cost_million_usd_low": national_cost["low"],
            "standing_stock_cost_million_usd_base": national_cost["base"],
            "standing_stock_cost_million_usd_high": national_cost["high"],
        },
        "province_factors": {
            province: factor.odt_per_m3_total for province, factor in sorted(factors.items())
        },
        "province_factor_detail": [
            {
                "province": province,
                "cells": factor.cells,
                "total_volume_m3": factor.total_volume_m3,
                "log_volume_m3": factor.log_volume_m3,
                "residue_odt": factor.residue_odt,
                "odt_per_m3_total": factor.odt_per_m3_total,
                "odt_per_m3_log": factor.odt_per_m3_log,
            }
            for province, factor in sorted(factors.items())
        ],
        "comparison": {
            "diagnostic_total_volume_m3": diagnostic_total_volume,
            "diagnostic_log_volume_m3": diagnostic_log_volume,
            "diagnostic_residue_volume_m3": DIAGNOSTIC_RESIDUE_VOLUME_M3,
            "diagnostic_residue_odt": diagnostic_residue,
            "diagnostic_odt_per_m3_log": diagnostic_log_factor,
            "diagnostic_odt_per_m3_total": matrix_factor,
            "tile_weighted_odt_per_m3_total": tile_mix_factor,
            "matrix_basis_odt_per_m3_total": matrix_factor,
            "diagnostic_effective_density_odt_m3": DIAGNOSTIC_EFFECTIVE_DENSITY_ODT_M3,
            "volume_vs_diagnostic_pct": 100.0 * (national_volume - diagnostic_total_volume) / diagnostic_total_volume,
            "residue_vs_diagnostic_pct": 100.0 * (national_residue - diagnostic_residue) / diagnostic_residue,
            "factor_total_over_log": matrix_factor / diagnostic_log_factor,
            "province_factor_min": factors[factor_min_province].odt_per_m3_total,
            "province_factor_min_province": factor_min_province,
            "province_factor_max": factors[factor_max_province].odt_per_m3_total,
            "province_factor_max_province": factor_max_province,
            "bc_only_43_523816pj": BC_ONLY_PJ,
            "base_pj_per_100_million_m3_tile_mix_over_bc_only": (
                tile_mix_factor * 1e8 * RECOVERY["base"] * LHV_GJ_PER_ODT / PJ_GJ / BC_ONLY_PJ
            ),
            "bc_comparison_is_scale_only": True,
            "diagnostic_gap": {
                "emitted_area_ha": DIAGNOSTIC_EMITTED_AREA_HA,
                "mapped_area_ha": DIAGNOSTIC_MAPPED_AREA_HA,
                "gap_area_ha": DIAGNOSTIC_GAP_AREA_HA,
                "gap_fraction": DIAGNOSTIC_GAP_FRACTION,
                "note": "failed PINU.BAN Great Bear Lowlands cell plus other gap; not relevant to the tile library, which covers all area",
            },
            "b25_anomaly": b25_anomaly,
        },
        "area_geometry_consistency": {
            "tiles_over_capacity": over_capacity_rows,
            "tiles_over_capacity_count": len(over_capacity_rows),
            "total_excess_area_ha": sum(
                float(row["excess_area_ha"]) for row in over_capacity_rows
            ),
            "b25": b25_anomaly,
            "note": "flagged, not clipped",
        },
        "selected_evidence_tiers": selected_tier_rows,
        "province_table": province_rows,
        "evidence_table": evidence_rows,
        "tile_table": tile_rows,
        "harvest_scenarios": [
            {
                "scenario": scenario,
                "factor_source": factor_source,
                "volume_basis": "total_volume_m3_per_year",
                "residue_factor_odt_per_m3": (
                    matrix_factor if scenario == "matrix_basis" else tile_mix_factor
                ),
            }
            for scenario, factor_source in HARVEST_SCENARIOS
        ],
        "harvest_sensitivity": harvest_rows,
        "validation": validation,
        "caveats": caveats,
    }

    write_csv(OUTPUT_DIR / "rsyc_tile_supply_by_province.csv", province_rows)
    write_csv(OUTPUT_DIR / "rsyc_tile_supply_by_tile.csv", tile_rows)
    write_csv(OUTPUT_DIR / "rsyc_tile_supply_by_evidence.csv", evidence_rows)
    write_csv(OUTPUT_DIR / "rsyc_tile_harvest_sensitivity.csv", harvest_rows)

    summary_path = OUTPUT_DIR / "rsyc_tile_supply_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n")
    report_path = OUTPUT_DIR / "RSYC_TILE_SUPPLY_REPORT.md"
    report_path.write_text(build_report(summary))

    outputs = [
        "rsyc_tile_supply_by_province.csv",
        "rsyc_tile_supply_by_tile.csv",
        "rsyc_tile_supply_by_evidence.csv",
        "rsyc_tile_harvest_sensitivity.csv",
        "rsyc_tile_supply_summary.json",
        "RSYC_TILE_SUPPLY_REPORT.md",
    ]
    manifest = {
        "artifact_version": "0.1.0",
        "label": "RSYC-only v0, no local corrections",
        "status": "derived_provisional",
        "generated_utc": generated_utc,
        "builder": str(Path(__file__).resolve().relative_to(REPO_ROOT)),
        "outputs": [
            {
                "path": str((OUTPUT_DIR / name).relative_to(REPO_ROOT)),
                "bytes": (OUTPUT_DIR / name).stat().st_size,
                "sha256": sha256(OUTPUT_DIR / name),
            }
            for name in outputs
        ],
        "validation_passed": all(
            check["status"] == "PASS" for check in validation
        ),
        "validation_checks": len(validation),
        "key_totals": {
            "area_ha": national_area,
            "volume_m3": national_volume,
            "residue_odt": national_residue,
            "tile_mix_odt_per_m3_total": tile_mix_factor,
            "matrix_national_odt_per_m3_total": matrix_factor,
            "recoverable_odt_base": national_residue * RECOVERY["base"],
            "recoverable_pj_base": national_pj["base"],
            "hypothetical_all_hectares_age100_stock_pj_equivalent": national_pj["base"],
        },
        "notes": caveats,
    }
    manifest_path = OUTPUT_DIR / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, allow_nan=False) + "\n")

    print(json.dumps(
        {
            "area_ha": national_area,
            "volume_m3": national_volume,
            "residue_odt": national_residue,
            "tile_mix_odt_per_m3_total": tile_mix_factor,
            "matrix_odt_per_m3_total": matrix_factor,
            "pj_base": national_pj["base"],
            "validation": "PASS",
            "outputs": len(outputs) + 1,
        },
        indent=2,
    ))


if __name__ == "__main__":
    main()
