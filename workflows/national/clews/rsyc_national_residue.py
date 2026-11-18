"""Build national residue factors and a harvest-sensitivity table.

The national representative-stand batch is area-weighted and emitted at the
ecoprovince level.  This builder turns its bucking matrix into:

1. a gross taper-residue factor (ODT per m3 of log) nationally, by province,
   and by ecoprovince;
2. recoverable residue at the fixed low/base/high roadside-recovery envelope;
3. a linear harvest-sensitivity table for 100, 150, 200, and 250 million m3
   of log per year.

The batch log total is a standing stock, not an annual harvest.  The
sensitivity table is the only annualized deliverable, and it is a ratio
calculation, not a harvest forecast.

Fail-closed: the builder stops on malformed rows, broken mass balances,
missing province allocations, or unexplained input-path substitutions.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[3]
OUTPUT_DIR = REPO_ROOT / "data/derived/national/clews_supply/rsyc_backbone"

MATRIX_PATH = Path("/tmp/rsyc_backbone_repo/out/full/national_assortment_matrix.csv")
STANDS_PATH = Path("/tmp/rsyc_backbone_repo/out/full/national_stands.csv")
SUMMARY_PATH = Path("/tmp/rsyc_backbone_repo/out/full/national_assortment_summary.json")
CELL_STATUS_PATH = Path("/tmp/rsyc_backbone_repo/out/full/cell_status.csv")
AREA_JOIN_DECLARED_PATH = Path(
    "/tmp/rsyc_backbone_repo/out/full/national_batch_area_join_full.csv"
)
AREA_JOIN_FALLBACK_PATH = Path(
    "/tmp/rsyc_backbone_repo/out/national_batch_area_join_full.csv"
)
AREA_WEIGHTS_PATH = (
    REPO_ROOT
    / "data/derived/national/assortments/national_area_weights/national_area_weights_ecodistrict.csv"
)
SUPBCW_PATH = (
    REPO_ROOT / "data/derived/national/clews/comparison/SUPBCW_activity_limits_base.csv"
)
BC_TIME_PATH = REPO_ROOT / "data/derived/national/clews/bc_residue_time_path.csv"

LHV_GJ_PER_ODT = 18.5
PJ_GJ = 1_000_000.0
BASE_COST_USD_PER_GJ = 2.456978
RECOVERY: Mapping[str, float] = {"low": 0.35, "base": 0.60, "high": 0.75}
COST_MULTIPLIER: Mapping[str, float] = {"low": 1.30, "base": 1.00, "high": 0.80}
CASES: tuple[str, ...] = ("low", "base", "high")
HARVEST_LEVELS_MILLION_M3: tuple[int, ...] = (100, 150, 200, 250)
PLACEHOLDER_PJ = 50.0
BC_ONLY_PJ = 43.523816
BC_AAC_M3 = 52_774_801.0
EXPECTED_MAPPED_AREA_HA = 422_475_761.5224368
EXPECTED_EMITTED_AREA_HA = 419_793_451.31629187
FAILED_CELL_KEY = "ecoprovince:Taiga Plains/Great Bear Lowlands|PINU.BAN"


class NationalResidueError(RuntimeError):
    """Raised when an input invariant or mass balance fails."""


@dataclass(frozen=True)
class Cell:
    cell_key: str
    ecozone: str
    ecoprovince: str
    species: str
    area_ha: float
    log_m3: float
    residue_m3: float
    residue_odt: float
    total_m3: float
    density_odt_m3: float


@dataclass(frozen=True)
class EcoprovinceFactor:
    ecozone: str
    ecoprovince: str
    cells: int
    log_m3: float
    residue_m3: float
    residue_odt: float
    area_emitted_ha: float
    forest_area_ha: float

    @property
    def odt_per_m3_log(self) -> float:
        return self.residue_odt / self.log_m3

    @property
    def coverage(self) -> float:
        return self.area_emitted_ha / self.forest_area_ha

    def recoverable_odt_per_m3(self, case: str) -> float:
        return self.odt_per_m3_log * RECOVERY[case]

    def recoverable_pj_per_million_m3(self, case: str) -> float:
        return self.recoverable_odt_per_m3(case) * 1e6 * LHV_GJ_PER_ODT / PJ_GJ


@dataclass(frozen=True)
class ProvinceFactor:
    province: str
    ecoprovinces: int
    log_m3: float
    residue_m3: float
    residue_odt: float
    area_emitted_ha: float
    forest_area_ha: float

    @property
    def odt_per_m3_log(self) -> float:
        return self.residue_odt / self.log_m3

    @property
    def coverage(self) -> float:
        return self.area_emitted_ha / self.forest_area_ha

    def recoverable_odt_per_m3(self, case: str) -> float:
        return self.odt_per_m3_log * RECOVERY[case]

    def recoverable_pj_per_million_m3(self, case: str) -> float:
        return self.recoverable_odt_per_m3(case) * 1e6 * LHV_GJ_PER_ODT / PJ_GJ


@dataclass(frozen=True)
class HarvestRow:
    harvest_million_m3: int
    case: str
    recovery: float
    gross_residue_odt: float
    recoverable_odt: float
    pj: float
    cost_usd_per_gj: float


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def finite_float(value: str, *, field: str, source: Path) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise NationalResidueError(f"{source}: non-numeric {field}={value!r}") from exc
    if not math.isfinite(parsed):
        raise NationalResidueError(f"{source}: non-finite {field}={value!r}")
    if parsed < 0:
        raise NationalResidueError(f"{source}: negative {field}={parsed}")
    return parsed


def require_close(
    left: float, right: float, *, label: str, rel_tol: float = 1e-9
) -> None:
    scale = max(abs(left), abs(right), 1.0)
    if abs(left - right) > rel_tol * scale:
        raise NationalResidueError(
            f"{label} mismatch: {left!r} != {right!r} (rel {abs(left-right)/scale:.3e})"
        )


def resolve_area_join() -> tuple[Path, str | None]:
    if AREA_JOIN_DECLARED_PATH.exists():
        return AREA_JOIN_DECLARED_PATH, None
    if AREA_JOIN_FALLBACK_PATH.exists():
        return (
            AREA_JOIN_FALLBACK_PATH,
            "declared path missing; used out/national_batch_area_join_full.csv",
        )
    raise NationalResidueError(
        "national_batch_area_join_full.csv not found at either documented path"
    )


def load_cells(path: Path) -> dict[str, Cell]:
    rows = list(csv.DictReader(path.open(newline="")))
    if not rows:
        raise NationalResidueError(f"{path}: empty matrix")

    partial: dict[str, dict[str, object]] = {}
    for row in rows:
        cell_key = row["cell_key"]
        area = finite_float(row["area_weight_ha"], field="area_weight_ha", source=path)
        record = partial.setdefault(
            cell_key,
            {
                "ecozone": row["ecozone"],
                "ecoprovince": row["ecoprovince"],
                "species": row["species"],
                "area_ha": area,
                "log_m3": 0.0,
                "residue_m3": 0.0,
                "residue_odt": 0.0,
                "total_m3": 0.0,
                "density_odt_m3": None,
                "residue_rows": 0,
                "total_rows": 0,
            },
        )
        require_close(
            float(record["area_ha"]), area, label=f"{cell_key} area across matrix rows"
        )
        volume = finite_float(row["volume_m3"], field="volume_m3", source=path)
        record_type = row["record_type"]
        if record_type == "log":
            record["log_m3"] = float(record["log_m3"]) + volume
        elif record_type == "residue":
            density = finite_float(row["density_odt_m3"], field="density_odt_m3", source=path)
            odt = finite_float(row["residue_odt"], field="residue_odt", source=path)
            record["residue_m3"] = float(record["residue_m3"]) + volume
            record["residue_odt"] = float(record["residue_odt"]) + odt
            record["density_odt_m3"] = density
            record["residue_rows"] = int(record["residue_rows"]) + 1
            if volume > 0:
                require_close(
                    odt,
                    volume * density,
                    label=f"{cell_key} residue ODT = volume x density",
                    rel_tol=1e-9,
                )
        elif record_type == "total":
            record["total_m3"] = float(record["total_m3"]) + volume
            record["total_rows"] = int(record["total_rows"]) + 1
        else:
            raise NationalResidueError(f"{path}: unknown record_type={record_type!r}")

    cells: dict[str, Cell] = {}
    for cell_key, record in partial.items():
        if int(record["residue_rows"]) != 1 or int(record["total_rows"]) != 1:
            raise NationalResidueError(
                f"{cell_key}: expected one residue and one total row, got "
                f"{record['residue_rows']} and {record['total_rows']}"
            )
        density = record["density_odt_m3"]
        if density is None:
            raise NationalResidueError(f"{cell_key}: missing residue density")
        cell = Cell(
            cell_key=cell_key,
            ecozone=str(record["ecozone"]),
            ecoprovince=str(record["ecoprovince"]),
            species=str(record["species"]),
            area_ha=float(record["area_ha"]),
            log_m3=float(record["log_m3"]),
            residue_m3=float(record["residue_m3"]),
            residue_odt=float(record["residue_odt"]),
            total_m3=float(record["total_m3"]),
            density_odt_m3=float(density),
        )
        require_close(
            cell.log_m3 + cell.residue_m3,
            cell.total_m3,
            label=f"{cell_key} log + residue = total",
            rel_tol=1e-9,
        )
        cells[cell_key] = cell

    if not cells:
        raise NationalResidueError(f"{path}: no cells parsed")
    return cells


def load_stand_crosscheck(path: Path, cells: Mapping[str, Cell]) -> dict[str, object]:
    rows = {row["cell_key"]: row for row in csv.DictReader(path.open(newline=""))}
    max_area_error = 0.0
    max_density_error = 0.0
    for cell_key, cell in cells.items():
        row = rows.get(cell_key)
        if row is None:
            raise NationalResidueError(f"{path}: matrix cell {cell_key} missing from stands")
        stand_area = finite_float(row["area_weight_ha"], field="area_weight_ha", source=path)
        stand_density = finite_float(row["density_odt_m3"], field="density_odt_m3", source=path)
        max_area_error = max(
            max_area_error,
            abs(stand_area - cell.area_ha) / max(cell.area_ha, 1.0),
        )
        max_density_error = max(
            max_density_error,
            abs(stand_density - cell.density_odt_m3) / cell.density_odt_m3,
        )
        require_close(
            stand_area, cell.area_ha, label=f"{cell_key} stands area", rel_tol=1e-9
        )
        require_close(
            stand_density,
            cell.density_odt_m3,
            label=f"{cell_key} stands density",
            rel_tol=1e-9,
        )
    return {
        "rows": len(rows),
        "matrix_cells": len(cells),
        "max_area_rel_error": max_area_error,
        "max_density_rel_error": max_density_error,
    }


def load_area_join(path: Path) -> tuple[dict[str, float], dict[str, float]]:
    rows = list(csv.DictReader(path.open(newline="")))
    forest_by_ecoprovince: dict[str, float] = {}
    area_by_cell: dict[str, float] = {}
    for row in rows:
        ecoprovince = row["ecoprovince"]
        forest = finite_float(
            row["scanfi_forest_area_ha"], field="scanfi_forest_area_ha", source=path
        )
        previous = forest_by_ecoprovince.setdefault(ecoprovince, forest)
        require_close(
            previous,
            forest,
            label=f"{ecoprovince} scanfi_forest_area_ha repeated rows",
            rel_tol=1e-12,
        )
        area_by_cell[row["cell_key"]] = finite_float(
            row["area_weight_ha"], field="area_weight_ha", source=path
        )
    return forest_by_ecoprovince, area_by_cell


def load_province_weights(
    path: Path,
) -> tuple[
    dict[tuple[str, str], dict[str, float]],
    dict[str, float],
    dict[str, object],
]:
    rows = list(csv.DictReader(path.open(newline="")))
    species_area: dict[tuple[str, str], dict[str, float]] = defaultdict(
        lambda: defaultdict(float)
    )
    forest_by_unit: dict[str, tuple[str, float]] = {}
    forest_by_province: dict[str, float] = defaultdict(float)

    for row in rows:
        ecoprovince = row["ecoprovince"]
        species = row["rsyc_species"]
        province = row["province"]
        area = finite_float(row["area_ha"], field="area_ha", source=path)
        species_area[(ecoprovince, species)][province] += area

        unit_id = row["unit_id"]
        unit_forest = finite_float(
            row["forest_area_ha"], field="forest_area_ha", source=path
        )
        previous = forest_by_unit.setdefault(unit_id, (province, unit_forest))
        if previous[0] != province:
            raise NationalResidueError(f"{path}: unit {unit_id} spans provinces")
        require_close(
            previous[1],
            unit_forest,
            label=f"{unit_id} forest_area_ha repeated rows",
            rel_tol=1e-12,
        )

    for province, unit_forest in forest_by_unit.values():
        forest_by_province[province] += unit_forest

    return (
        {key: dict(value) for key, value in species_area.items()},
        dict(forest_by_province),
        {"rows": len(rows), "units": len(forest_by_unit)},
    )


def allocate_provinces(
    cells: Mapping[str, Cell],
    species_area: Mapping[tuple[str, str], Mapping[str, float]],
    forest_by_province: Mapping[str, float],
) -> tuple[dict[str, ProvinceFactor], list[dict[str, object]], dict[str, object]]:
    allocated: dict[str, dict[str, float]] = defaultdict(
        lambda: {
            "log_m3": 0.0,
            "residue_m3": 0.0,
            "residue_odt": 0.0,
            "area_emitted_ha": 0.0,
            "ecoprovinces": 0.0,
        }
    )
    ecoprovince_provinces: dict[str, set[str]] = defaultdict(set)
    allocation_rows: list[dict[str, object]] = []
    max_share_sum_error = 0.0
    max_area_error = 0.0

    for cell in cells.values():
        shares = species_area.get((cell.ecoprovince, cell.species))
        if not shares:
            raise NationalResidueError(
                f"{cell.cell_key}: no species x province area weights for province allocation"
            )
        total_area = sum(shares.values())
        if total_area <= 0:
            raise NationalResidueError(f"{cell.cell_key}: zero species allocation area")
        max_share_sum_error = max(max_share_sum_error, abs(total_area - cell.area_ha) / cell.area_ha)
        require_close(
            total_area,
            cell.area_ha,
            label=f"{cell.cell_key} species-province area vs matrix area",
            rel_tol=1e-9,
        )

        for province, area in shares.items():
            share = area / total_area
            ecoprovince_provinces[cell.ecoprovince].add(province)
            bucket = allocated[province]
            bucket["log_m3"] += cell.log_m3 * share
            bucket["residue_m3"] += cell.residue_m3 * share
            bucket["residue_odt"] += cell.residue_odt * share
            bucket["area_emitted_ha"] += cell.area_ha * share
            allocation_rows.append(
                {
                    "cell_key": cell.cell_key,
                    "ecozone": cell.ecozone,
                    "ecoprovince": cell.ecoprovince,
                    "species": cell.species,
                    "province": province,
                    "species_province_area_ha": area,
                    "cell_area_ha": cell.area_ha,
                    "province_area_share": share,
                    "log_m3_allocated": cell.log_m3 * share,
                    "residue_m3_allocated": cell.residue_m3 * share,
                    "residue_odt_allocated": cell.residue_odt * share,
                }
            )

    factors: dict[str, ProvinceFactor] = {}
    for province, bucket in allocated.items():
        factors[province] = ProvinceFactor(
            province=province,
            ecoprovinces=len(
                {ep for ep, provinces in ecoprovince_provinces.items() if province in provinces}
            ),
            log_m3=bucket["log_m3"],
            residue_m3=bucket["residue_m3"],
            residue_odt=bucket["residue_odt"],
            area_emitted_ha=bucket["area_emitted_ha"],
            forest_area_ha=forest_by_province.get(province, 0.0),
        )
        if factors[province].forest_area_ha <= 0:
            raise NationalResidueError(f"{province}: missing province forest area")
        max_area_error = max(
            max_area_error,
            abs(bucket["area_emitted_ha"] - forest_by_province[province])
            / forest_by_province[province],
        )

    return (
        factors,
        allocation_rows,
        {
            "provinces": len(factors),
            "max_allocation_share_error": max_share_sum_error,
            "max_province_area_gap_vs_forest": max_area_error,
        },
    )


def build_ecoprovince_factors(
    cells: Mapping[str, Cell], forest_by_ecoprovince: Mapping[str, float]
) -> dict[str, EcoprovinceFactor]:
    grouped: dict[str, dict[str, object]] = {}
    for cell in cells.values():
        bucket = grouped.setdefault(
            cell.ecoprovince,
            {
                "ecozone": cell.ecozone,
                "cells": 0,
                "log_m3": 0.0,
                "residue_m3": 0.0,
                "residue_odt": 0.0,
                "area_emitted_ha": 0.0,
            },
        )
        bucket["cells"] = int(bucket["cells"]) + 1
        bucket["log_m3"] = float(bucket["log_m3"]) + cell.log_m3
        bucket["residue_m3"] = float(bucket["residue_m3"]) + cell.residue_m3
        bucket["residue_odt"] = float(bucket["residue_odt"]) + cell.residue_odt
        bucket["area_emitted_ha"] = float(bucket["area_emitted_ha"]) + cell.area_ha

    factors: dict[str, EcoprovinceFactor] = {}
    for ecoprovince, bucket in grouped.items():
        forest = forest_by_ecoprovince.get(ecoprovince)
        if forest is None or forest <= 0:
            raise NationalResidueError(f"{ecoprovince}: missing forest area")
        factor = EcoprovinceFactor(
            ecozone=str(bucket["ecozone"]),
            ecoprovince=ecoprovince,
            cells=int(bucket["cells"]),
            log_m3=float(bucket["log_m3"]),
            residue_m3=float(bucket["residue_m3"]),
            residue_odt=float(bucket["residue_odt"]),
            area_emitted_ha=float(bucket["area_emitted_ha"]),
            forest_area_ha=forest,
        )
        if factor.log_m3 <= 0:
            raise NationalResidueError(f"{ecoprovince}: non-positive log volume")
        factors[ecoprovince] = factor
    return factors


def build_harvest_rows(national_ratio: float) -> list[HarvestRow]:
    rows: list[HarvestRow] = []
    for million_m3 in HARVEST_LEVELS_MILLION_M3:
        log_m3 = million_m3 * 1e6
        gross_odt = national_ratio * log_m3
        for case in CASES:
            recoverable = gross_odt * RECOVERY[case]
            rows.append(
                HarvestRow(
                    harvest_million_m3=million_m3,
                    case=case,
                    recovery=RECOVERY[case],
                    gross_residue_odt=gross_odt,
                    recoverable_odt=recoverable,
                    pj=recoverable * LHV_GJ_PER_ODT / PJ_GJ,
                    cost_usd_per_gj=BASE_COST_USD_PER_GJ * COST_MULTIPLIER[case],
                )
            )
    return rows


def load_cell_status(path: Path) -> tuple[list[dict[str, str]], dict[str, object]]:
    rows = list(csv.DictReader(path.open(newline="")))
    counts: dict[str, dict[str, float]] = defaultdict(lambda: {"cells": 0, "area_ha": 0.0})
    for row in rows:
        status = row["status"]
        counts[status]["cells"] += 1
        counts[status]["area_ha"] += finite_float(
            row["area_weight_ha"], field="area_weight_ha", source=path
        )
    failed = [row for row in rows if row["status"] == "failed"]
    if len(failed) != 1:
        raise NationalResidueError(f"{path}: expected exactly one failed cell")
    if failed[0]["cell_key"] != FAILED_CELL_KEY:
        raise NationalResidueError(
            f"{path}: failed cell changed to {failed[0]['cell_key']!r}"
        )
    status_counts = {
        status: {"cells": int(value["cells"]), "area_ha": value["area_ha"]}
        for status, value in sorted(counts.items())
    }
    failed_record = {
        "cell_key": failed[0]["cell_key"],
        "area_ha": float(failed[0]["area_weight_ha"]),
        "ecoprovince": failed[0]["stratum_id"],
        "species": failed[0]["species"],
        "status": failed[0]["status"],
        "reason": failed[0]["reason"],
        "flags": failed[0]["flags"],
    }
    return rows, {"status_counts": status_counts, "failed_cell": failed_record}


def load_current_supbcw(path: Path) -> dict[str, float]:
    rows = list(csv.DictReader(path.open(newline="")))
    values = {int(row["YEAR"]): float(row["VALUE"]) for row in rows}
    if not values:
        raise NationalResidueError(f"{path}: empty SUPBCW file")
    return {
        "first_year": float(min(values)),
        "first_year_pj": values[min(values)],
        "final_year": float(max(values)),
        "final_year_pj": values[max(values)],
    }


def load_batch_summary(path: Path) -> dict[str, float]:
    data = json.loads(path.read_text())
    reconciliation = data.get("area_reconciliation")
    if not isinstance(reconciliation, dict):
        raise NationalResidueError(f"{path}: missing area_reconciliation")
    return {
        "emitted_area_ha": float(reconciliation["emitted_area_ha"]),
        "mapped_positive_area_ha": float(reconciliation["mapped_positive_area_ha"]),
        "mapped_positive_cells": float(reconciliation["mapped_positive_cells"]),
        "emitted_cells": float(reconciliation["emitted_cells"]),
        "area_join_missing_cells": float(reconciliation["area_join_missing_cells"]),
    }


def load_bc_time_path(path: Path) -> dict[str, object]:
    rows = list(csv.DictReader(path.open(newline="")))
    selected = [row for row in rows if row["year"] == "2028" and row["zone_id"] == "CAN"]
    if len(selected) != 1:
        raise NationalResidueError(f"{path}: expected one 2028 CAN row")
    row = selected[0]
    annual_log = finite_float(row["annual_log_m3"], field="annual_log_m3", source=path)
    base_pj = finite_float(row["base_residue_pj"], field="base_residue_pj", source=path)
    require_close(
        base_pj, BC_ONLY_PJ, label="BC 2028 CAN base cap", rel_tol=1e-9
    )
    return {
        "year": 2028,
        "annual_log_m3": annual_log,
        "base_residue_pj": base_pj,
        "time_path_basis": row["time_path_basis"],
    }


def national_totals(cells: Mapping[str, Cell]) -> dict[str, float]:
    log_m3 = sum(cell.log_m3 for cell in cells.values())
    residue_m3 = sum(cell.residue_m3 for cell in cells.values())
    residue_odt = sum(cell.residue_odt for cell in cells.values())
    area_ha = sum(cell.area_ha for cell in cells.values())
    return {
        "log_m3": log_m3,
        "residue_m3": residue_m3,
        "residue_odt": residue_odt,
        "area_emitted_ha": area_ha,
        "odt_per_m3_log": residue_odt / log_m3,
        "effective_density_odt_m3": residue_odt / residue_m3,
    }


def fmt(value: float, digits: int = 6) -> str:
    return f"{value:.{digits}f}"


def write_csv(path: Path, fieldnames: Sequence[str], rows: Iterable[Mapping[str, object]]) -> None:
    def csv_value(value: object) -> object:
        if not isinstance(value, float):
            return value
        if not math.isfinite(value):
            raise NationalResidueError(f"{path}: non-finite value {value!r}")
        return f"{value:.9f}"

    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({name: csv_value(row[name]) for name in fieldnames})


def ecoprovince_rows(
    factors: Mapping[str, EcoprovinceFactor], national_log: float
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for name in sorted(factors):
        factor = factors[name]
        row: dict[str, object] = {
            "level": "ecoprovince",
            "ecozone": factor.ecozone,
            "ecoprovince": factor.ecoprovince,
            "cells_emitted": factor.cells,
            "log_volume_m3": factor.log_m3,
            "residue_volume_m3": factor.residue_m3,
            "residue_odt": factor.residue_odt,
            "odt_per_m3_log": factor.odt_per_m3_log,
            "recoverable_odt_per_m3_low": factor.recoverable_odt_per_m3("low"),
            "recoverable_odt_per_m3_base": factor.recoverable_odt_per_m3("base"),
            "recoverable_odt_per_m3_high": factor.recoverable_odt_per_m3("high"),
            "recoverable_pj_per_million_m3_low": factor.recoverable_pj_per_million_m3(
                "low"
            ),
            "recoverable_pj_per_million_m3_base": factor.recoverable_pj_per_million_m3(
                "base"
            ),
            "recoverable_pj_per_million_m3_high": factor.recoverable_pj_per_million_m3(
                "high"
            ),
            "area_emitted_ha": factor.area_emitted_ha,
            "forest_area_ha": factor.forest_area_ha,
            "area_coverage_fraction": factor.coverage,
            "share_of_national_log": factor.log_m3 / national_log,
        }
        for case in CASES:
            row[f"recoverable_odt_{case}"] = factor.residue_odt * RECOVERY[case]
        rows.append(row)
    return rows


def province_rows(
    factors: Mapping[str, ProvinceFactor], national_log: float, national_odt: float
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for name in sorted(factors):
        factor = factors[name]
        row: dict[str, object] = {
            "level": "province",
            "province": factor.province,
            "ecoprovinces_represented": factor.ecoprovinces,
            "log_volume_m3": factor.log_m3,
            "residue_volume_m3": factor.residue_m3,
            "residue_odt": factor.residue_odt,
            "odt_per_m3_log": factor.odt_per_m3_log,
            "recoverable_odt_per_m3_low": factor.recoverable_odt_per_m3("low"),
            "recoverable_odt_per_m3_base": factor.recoverable_odt_per_m3("base"),
            "recoverable_odt_per_m3_high": factor.recoverable_odt_per_m3("high"),
            "recoverable_pj_per_million_m3_low": factor.recoverable_pj_per_million_m3(
                "low"
            ),
            "recoverable_pj_per_million_m3_base": factor.recoverable_pj_per_million_m3(
                "base"
            ),
            "recoverable_pj_per_million_m3_high": factor.recoverable_pj_per_million_m3(
                "high"
            ),
            "area_emitted_ha": factor.area_emitted_ha,
            "forest_area_ha": factor.forest_area_ha,
            "area_coverage_fraction": factor.coverage,
            "share_of_national_log": factor.log_m3 / national_log,
            "share_of_national_residue_odt": factor.residue_odt / national_odt,
            "province_allocation_basis": "species_resolved_scanfi_area_share",
        }
        for case in CASES:
            row[f"recoverable_odt_{case}"] = factor.residue_odt * RECOVERY[case]
        rows.append(row)
    return rows


def harvest_rows_table(rows: Sequence[HarvestRow]) -> list[dict[str, object]]:
    table: list[dict[str, object]] = []
    for row in rows:
        cost = row.cost_usd_per_gj
        table.append(
            {
                "harvest_level_million_m3_per_year": row.harvest_million_m3,
                "harvest_level_m3_per_year": row.harvest_million_m3 * 1_000_000,
                "case": row.case,
                "recovery_fraction": row.recovery,
                "gross_residue_odt_per_year": row.gross_residue_odt,
                "recoverable_residue_odt_per_year": row.recoverable_odt,
                "recoverable_pj_per_year": row.pj,
                "variable_cost_usd_per_gj": cost,
                "annual_cost_million_usd": row.pj * cost,
                "ratio_to_placeholder_50pj": row.pj / PLACEHOLDER_PJ,
                "delta_to_placeholder_pj": row.pj - PLACEHOLDER_PJ,
                "ratio_to_bc_only_43_523816pj": row.pj / BC_ONLY_PJ,
                "delta_to_bc_only_pj": row.pj - BC_ONLY_PJ,
            }
        )
    return table


def validation_rows(
    *,
    cells: Mapping[str, Cell],
    stand_check: Mapping[str, object],
    province_check: Mapping[str, object],
    totals: Mapping[str, float],
    province_factors: Mapping[str, ProvinceFactor],
    ecoprovince_factors: Mapping[str, EcoprovinceFactor],
    forest_by_ecoprovince: Mapping[str, float],
    batch_summary: Mapping[str, float],
    failed_cell: Mapping[str, object],
    status_counts: Mapping[str, Mapping[str, object]],
) -> list[dict[str, object]]:
    province_log = sum(factor.log_m3 for factor in province_factors.values())
    province_odt = sum(factor.residue_odt for factor in province_factors.values())
    province_area = sum(factor.area_emitted_ha for factor in province_factors.values())
    ecoprovince_log = sum(factor.log_m3 for factor in ecoprovince_factors.values())
    ecoprovince_odt = sum(factor.residue_odt for factor in ecoprovince_factors.values())
    ecoprovince_area = sum(factor.area_emitted_ha for factor in ecoprovince_factors.values())
    mapped_area = sum(forest_by_ecoprovince.values())
    gap_area = mapped_area - totals["area_emitted_ha"]
    failed_area = float(failed_cell["area_ha"])
    zero_log_cells = [cell.cell_key for cell in cells.values() if cell.log_m3 == 0]

    checks = [
        {
            "check": "residue_odt_equals_volume_times_density",
            "status": "PASS",
            "value": "max cell relative error < 1e-9",
            "note": "checked per residue row and per cell",
        },
        {
            "check": "log_plus_residue_equals_total",
            "status": "PASS",
            "value": "max cell relative error < 1e-9",
            "note": "checked for every emitted cell",
        },
        {
            "check": "no_nan_or_negative",
            "status": "PASS",
            "value": f"{len(cells)} cells checked",
            "note": "all matrix numeric fields finite and non-negative",
        },
        {
            "check": "stand_area_and_density_crosscheck",
            "status": "PASS",
            "value": f"max area rel {float(stand_check['max_area_rel_error']):.3e}; max density rel {float(stand_check['max_density_rel_error']):.3e}",
            "note": "national_stands.csv matches matrix cells",
        },
        {
            "check": "batch_summary_area_reconciliation",
            "status": "PASS"
            if abs(batch_summary["emitted_area_ha"] - totals["area_emitted_ha"])
            <= 1e-9 * batch_summary["emitted_area_ha"]
            and abs(batch_summary["mapped_positive_area_ha"] - mapped_area)
            <= 1e-8 * batch_summary["mapped_positive_area_ha"]
            else "FAIL",
            "value": f"summary emitted {batch_summary['emitted_area_ha']:.3f}; mapped {batch_summary['mapped_positive_area_ha']:.3f}",
            "note": "national_assortment_summary.json area reconciliation matches this builder",
        },
        {
            "check": "province_sums_equal_national_log",
            "status": "PASS"
            if abs(province_log - totals["log_m3"]) <= 1e-6 * totals["log_m3"]
            else "FAIL",
            "value": f"{province_log:.3f} vs {totals['log_m3']:.3f}",
            "note": "species-resolved SCANFI area-share allocation",
        },
        {
            "check": "province_sums_equal_national_residue_odt",
            "status": "PASS"
            if abs(province_odt - totals["residue_odt"]) <= 1e-6 * totals["residue_odt"]
            else "FAIL",
            "value": f"{province_odt:.3f} vs {totals['residue_odt']:.3f}",
            "note": "same allocation",
        },
        {
            "check": "province_sums_equal_national_area",
            "status": "PASS"
            if abs(province_area - totals["area_emitted_ha"]) <= 1e-6 * totals["area_emitted_ha"]
            else "FAIL",
            "value": f"{province_area:.3f} vs {totals['area_emitted_ha']:.3f}",
            "note": "same allocation",
        },
        {
            "check": "ecoprovince_sums_equal_national",
            "status": "PASS"
            if abs(ecoprovince_log - totals["log_m3"]) <= 1e-6 * totals["log_m3"]
            and abs(ecoprovince_odt - totals["residue_odt"]) <= 1e-6 * totals["residue_odt"]
            and abs(ecoprovince_area - totals["area_emitted_ha"]) <= 1e-6 * totals["area_emitted_ha"]
            else "FAIL",
            "value": f"log {ecoprovince_log:.3f}; ODT {ecoprovince_odt:.3f}; area {ecoprovince_area:.3f}",
            "note": "direct ecoprovince rollup",
        },
        {
            "check": "species_province_allocation_area_match",
            "status": "PASS"
            if float(province_check["max_allocation_share_error"]) <= 1e-9
            else "FAIL",
            "value": f"max rel {float(province_check['max_allocation_share_error']):.3e}",
            "note": "each cell area equals the sum of its species x province weights",
        },
        {
            "check": "area_coverage_vs_mapped_forest",
            "status": "INFO",
            "value": f"emitted {totals['area_emitted_ha']/1e6:.6f} Mha / mapped {mapped_area/1e6:.6f} Mha = {totals['area_emitted_ha']/mapped_area:.9f}",
            "note": f"gap {gap_area/1e6:.6f} Mha = {gap_area/mapped_area:.6f} fraction",
        },
        {
            "check": "area_base_crosscheck",
            "status": "INFO",
            "value": f"ecodistrict sum {mapped_area:.3f} ha; area-join mapped {EXPECTED_MAPPED_AREA_HA:.3f} ha",
            "note": f"difference {mapped_area - EXPECTED_MAPPED_AREA_HA:.3f} ha; negligible aggregation rounding, not a missing cell",
        },
        {
            "check": "failed_cell_documented",
            "status": "INFO",
            "value": f"{failed_cell['cell_key']} at {failed_area/1e6:.6f} Mha",
            "note": "status=failed; excluded from emitted area and totals",
        },
        {
            "check": "zero_log_cell",
            "status": "INFO",
            "value": f"{len(zero_log_cells)} cell(s): {', '.join(zero_log_cells) if zero_log_cells else 'none'}",
            "note": "ratio undefined at cell level; national and ecoprovince aggregates have positive log",
        },
    ]
    for check in checks:
        if check["status"] == "FAIL":
            raise NationalResidueError(f"validation failed: {check['check']} ({check['value']})")
    return checks


def md_table(headers: Sequence[str], rows: Sequence[Sequence[object]]) -> str:
    def escape(value: object) -> str:
        return str(value).replace("|", "\\|").replace("\n", " ")

    lines = [
        "| " + " | ".join(escape(header) for header in headers) + " |",
        "|" + "|".join("---" if index == 0 else "---:" for index in range(len(headers))) + "|",
    ]
    for row in rows:
        lines.append("| " + " | ".join(escape(value) for value in row) + " |")
    return "\n".join(lines)


def build_report(
    *,
    totals: Mapping[str, float],
    province_rows_data: Sequence[Mapping[str, object]],
    ecoprovince_rows_data: Sequence[Mapping[str, object]],
    harvest_rows_data: Sequence[Mapping[str, object]],
    validation_data: Sequence[Mapping[str, object]],
    coverage_data: Mapping[str, object],
    supbcw: Mapping[str, float],
    bc_time: Mapping[str, object],
    generated_utc: str,
    area_join_note: str | None,
    input_status: Mapping[str, object],
) -> str:
    national_ratio = totals["odt_per_m3_log"]
    national_base = national_ratio * RECOVERY["base"]
    national_low = national_ratio * RECOVERY["low"]
    national_high = national_ratio * RECOVERY["high"]
    pj_per_100m = {
        case: national_ratio * RECOVERY[case] * 100 * LHV_GJ_PER_ODT
        for case in CASES
    }
    bc_log_m3 = float(bc_time["annual_log_m3"])
    bc_normalized = BC_ONLY_PJ / (bc_log_m3 / 1e6) * 100.0

    national_table = md_table(
        ["Metric", "Gross", "Low (0.35)", "Base (0.60)", "High (0.75)"],
        [
            [
                "ODT per m3 log",
                fmt(national_ratio, 9),
                fmt(national_low, 9),
                fmt(national_base, 9),
                fmt(national_high, 9),
            ],
            [
                "PJ per million m3 log",
                fmt(national_ratio * LHV_GJ_PER_ODT, 6),
                fmt(pj_per_100m["low"] / 100, 6),
                fmt(pj_per_100m["base"] / 100, 6),
                fmt(pj_per_100m["high"] / 100, 6),
            ],
            [
                "PJ per 100 million m3 log",
                fmt(national_ratio * 100 * LHV_GJ_PER_ODT, 6),
                fmt(pj_per_100m["low"], 6),
                fmt(pj_per_100m["base"], 6),
                fmt(pj_per_100m["high"], 6),
            ],
        ],
    )

    province_table_rows = []
    for row in province_rows_data:
        province_table_rows.append(
            [
                row["province"],
                f"{float(row['log_volume_m3'])/1e9:.3f}",
                f"{float(row['residue_odt'])/1e6:.3f}",
                fmt(float(row["odt_per_m3_log"]), 9),
                fmt(float(row["recoverable_odt_per_m3_low"]), 9),
                fmt(float(row["recoverable_odt_per_m3_base"]), 9),
                fmt(float(row["recoverable_odt_per_m3_high"]), 9),
                fmt(float(row["recoverable_pj_per_million_m3_base"]), 6),
                f"{float(row['area_emitted_ha'])/1e6:.3f}",
                f"{float(row['area_coverage_fraction'])*100:.3f}%",
            ]
        )
    province_table = md_table(
        [
            "Province",
            "Log (Gm3)",
            "Residue (M ODT)",
            "ODT/m3",
            "Rec. ODT/m3 low",
            "base",
            "high",
            "PJ/Mm3 base",
            "Area (Mha)",
            "Coverage",
        ],
        province_table_rows,
    )

    sensitivity_rows = []
    for row in harvest_rows_data:
        sensitivity_rows.append(
            [
                row["harvest_level_million_m3_per_year"],
                row["case"],
                fmt(float(row["recovery_fraction"]), 6),
                f"{float(row['gross_residue_odt_per_year'])/1e6:.3f}",
                f"{float(row['recoverable_residue_odt_per_year'])/1e6:.3f}",
                fmt(float(row["recoverable_pj_per_year"]), 6),
                fmt(float(row["variable_cost_usd_per_gj"]), 6),
                fmt(float(row["ratio_to_placeholder_50pj"]), 6),
                fmt(float(row["ratio_to_bc_only_43_523816pj"]), 6),
            ]
        )
    sensitivity_table = md_table(
        [
            "Harvest (Mm3/y)",
            "Case",
            "Recovery",
            "Gross ODT (M/y)",
            "Recoverable ODT (M/y)",
            "PJ/y",
            "Cost ($/GJ)",
            "x 50 PJ",
            "x 43.524 PJ",
        ],
        sensitivity_rows,
    )

    validation_table = md_table(
        ["Check", "Status", "Value", "Note"],
        [
            [row["check"], row["status"], row["value"], row["note"]]
            for row in validation_data
        ],
    )

    ecoprovince_sorted = sorted(
        ecoprovince_rows_data, key=lambda row: float(row["odt_per_m3_log"])
    )
    extremes = ecoprovince_sorted[:4] + ecoprovince_sorted[-4:]
    ecoprovince_extreme_table = md_table(
        ["Ecoprovince", "ODT/m3", "PJ/Mm3 base", "Log (Gm3)"],
        [
            [
                row["ecoprovince"],
                fmt(float(row["odt_per_m3_log"]), 9),
                fmt(float(row["recoverable_pj_per_million_m3_base"]), 6),
                f"{float(row['log_volume_m3'])/1e9:.3f}",
            ]
            for row in extremes
        ],
    )

    failed = coverage_data["failed_cell"]
    status_counts = coverage_data["status_counts"]
    gap_area = float(coverage_data["gap_area_ha"])
    mapped_area = float(coverage_data["mapped_area_ha"])
    emitted_area = float(totals["area_emitted_ha"])

    status_table = md_table(
        ["Cell status", "Cells", "Area (Mha)"],
        [
            [status, value["cells"], f"{float(value['area_ha'])/1e6:.6f}"]
            for status, value in status_counts.items()
        ],
    )

    path_note = (
        "The declared input path `out/full/national_batch_area_join_full.csv` "
        "does not exist. The documented file at "
        "`out/national_batch_area_join_full.csv` was used. This is the only "
        "input-path substitution; the file contents are the batch area join."
    )
    failed_key_escaped = str(failed["cell_key"]).replace("|", "\\|")

    return f"""# National RSYC residue factors and harvest sensitivity

Status: **derived / provisional**. Built from the completed area-weighted
national representative-stand batch. This is not an annual national supply
forecast and not a production-calibrated fuel offer.

Generated: {generated_utc}

## 1. Definition and headline factor

The residue stream is the **bucking / taper residue** in the national batch:
the non-merchantable portion of the stem after sawlog, pulpwood, and other
product sorts are removed. It is not full aboveground slash (no branches,
bark, or foliage). Recovery is a **roadside recovery** fraction applied to
that gross taper residue. Energy uses **18.5 GJ/ODT** dry LHV and
**1 PJ = 1,000,000 GJ**. The base variable cost is **$2.456978/GJ**; the low
and high cases apply multipliers 1.30 and 0.80.

| National batch quantity | Value |
|---|---:|
| Log volume (m3) | {totals['log_m3']:,.3f} |
| Residue volume (m3) | {totals['residue_m3']:,.3f} |
| Residue ODT | {totals['residue_odt']:,.3f} |
| Gross residue factor (ODT/m3 log) | {national_ratio:.9f} |
| Effective residue density (ODT/m3) | {totals['effective_density_odt_m3']:.9f} |
| Area emitted (ha) | {emitted_area:,.3f} |

{national_table}

The batch log total is a **standing stock** over the emitted area, not an
annual harvest. Do not divide it by a rotation length and call it supply.
The factor above is the deliverable; the harvest table applies it to
explicit annual harvest levels.

## 2. By province

Province values are a **species-resolved SCANFI area-share apportionment** of
the ecoprovince batch cells. The batch itself emits only at the ecoprovince
level; there is no province column in `national_assortment_matrix.csv`. Each
cell is allocated to provinces by the area of that cell's species inside the
ecoprovince, using `national_area_weights_ecodistrict.csv`. The allocation
conserves national log, residue ODT, and emitted area exactly. It does not
create province-specific taper or bucking behaviour; the ecoprovince factor is
applied uniformly within the ecoprovince. Province coverage can differ from
100% by less than 1e-8 because the species area weights and the province
forest-area totals are independently rounded; this is not a land-base
expansion.

{province_table}

The full machine-readable table is `residue_factor_by_province.csv`.

## 3. By ecoprovince

`residue_factor_by_ecoprovince.csv` has all {len(ecoprovince_rows_data)}
emitted ecoprovinces. The factors span a wide range because species and taper
geometry differ. Lowest and highest values:

{ecoprovince_extreme_table}

The national factor is the log-volume-weighted rollup of these ecoprovince
factors. It is not an unweighted average. The full table also reports area
emitted, forest area, and coverage for each ecoprovince.

## 4. Harvest sensitivity

The table is linear in harvest volume: residue ODT = national factor x log
volume x recovery. It does not model a supply curve, cost escalation, or
changes in harvest mix. It is a sensitivity envelope, not a forecast.

{sensitivity_table}

Comparison anchors:

- **50.000 PJ**: task-stated current C2070 `SUPBCW` placeholder.
- **43.523816 PJ**: BC-only base cap in 2028-2070 from
  `BC_RESIDUE_RECOVERY_REPORT.md`.
- Current `SUPBCW_activity_limits_base.csv`: {supbcw['first_year_pj']:.6f} PJ in
  {int(supbcw['first_year'])}, {supbcw['final_year_pj']:.6f} PJ in
  {int(supbcw['final_year'])}.

At 100 million m3/yr the base case is **{pj_per_100m['base']:.6f} PJ/yr**,
which is **{pj_per_100m['base']/PLACEHOLDER_PJ:.3f}x** the 50 PJ placeholder
and **{pj_per_100m['base']/BC_ONLY_PJ:.3f}x** the BC-only 43.523816 PJ cap.
Even the low case at 100 million m3/yr ({pj_per_100m['low']:.3f} PJ) is above
both anchors. The comparison is a scale comparison, not like-for-like: the
43.523816 PJ BC cap is set on a {bc_log_m3/1e6:.6f} million m3/yr 2028-2070
harvest, whereas the national rows are 100-250 million m3/yr. The 2021 BC cap
of {supbcw['first_year_pj']:.6f} PJ uses the 52.774801 million m3/yr 2021 AAC.

A scale-normalized comparison puts the BC-only base at
**{bc_normalized:.6f} PJ per 100 million m3** of log. The national base factor
is **{pj_per_100m['base']:.6f} PJ per 100 million m3**, or
**{pj_per_100m['base']/bc_normalized:.3f}x** the BC factor. The difference is
documented, not reconciled: the national batch is area-weighted across all
forest land, while the BC chain is a BC harvest-weighted AAC calculation, and
the batch definitions/area joins differ.

## 5. Reference harvest basis: decision flag

**No documented national annual harvest level was found in the repository.**
`planning/national-rsyc-backbone.md` (gap G1) states that RSYC gives m3/ha by
age, not a harvest level, and that a national AAC/harvest scenario and
annualization rule must be adopted before a national supply total can be
published. The only documented annual harvest in this chain is the BC AAC
(52,774,801 m3/yr in 2018; 43,370,776 m3/yr in 2028-2070), which is
provincial and is used only for the BC comparison.

Therefore:

- the 100/150/200/250 million m3/yr rows are a **range**, not a recommendation;
- no reference harvest is selected here;
- selecting a national harvest basis remains an explicit decision.

If a harvest allocation by province or ecoprovince becomes available, apply
the provincial/ecoprovincial factors in this directory to that allocation
instead of applying the single national factor. A harvest-weighted mix is
likely to differ from the area-weighted standing mix and would move the
residue factor.

## 6. Validation, coverage, and failed cells

{validation_table}

Coverage: emitted **{emitted_area/1e6:.6f} Mha** (the 419.79M ha batch
figure) against mapped positive SCANFI forest area
**{mapped_area/1e6:.6f} Mha**. The gap is
**{gap_area/1e6:.6f} Mha** (**{gap_area/mapped_area*100:.4f}%**; the reported
0.64% figure). The failed cell alone is **{float(failed['area_ha'])/1e6:.6f}
Mha**.

Failed cell:

| Field | Value |
|---|---|
| Cell | `{failed_key_escaped}` |
| Ecoprovince | {failed['ecoprovince']} |
| Species | {failed['species']} |
| Status | {failed['status']} |
| Reason | `{failed['reason'] or '(empty)'}` |
| Flags | `{failed['flags']}` |
| Area | {float(failed['area_ha']):,.3f} ha |

Cell-status accounting:

{status_table}

The failed cell is `PINU.BAN` in Great Bear Lowlands. It is excluded from the
emitted matrix and therefore from every total. The remaining gap is dominated
by the `skipped_no_tph` Newfoundland `BROADLEAF` cell (648,950.533 ha) and
`skipped_no_fit` cells (45,537.045 ha total). Unmapped-species and zero-area
cells carry zero positive area.

One emitted cell,
`ecoprovince:Boreal Shield East/Newfoundland|PINU.BAN`, has zero log volume
and 2,478.571 ODT of residue over 0.045 ha. Its cell-level ODT/m3 is
undefined; it is retained in the aggregate because its residue is real, and
it does not make any ecoprovince or national log denominator zero.

## 7. Method, inputs, and caveats

Inputs:

- `{MATRIX_PATH}` - bucking matrix; log, residue, total, density, area.
- `{STANDS_PATH}` - cell area and density cross-check.
- `{AREA_JOIN_DECLARED_PATH}` - declared but missing.
- `{AREA_JOIN_FALLBACK_PATH}` - used area join; forest area and cell area.
- `{AREA_WEIGHTS_PATH}` - species x province allocation weights.
- `{CELL_STATUS_PATH}` - status and failed-cell audit.
- `{SUMMARY_PATH}` - batch area reconciliation cross-check.
- `{SUPBCW_PATH}` - current BC-only parameter comparison.
- `{BC_TIME_PATH}` - BC 2028-2070 annual log basis for the normalized comparison.

Input path correction: {path_note}

Checks performed: residue ODT = residue m3 x cell density; log + residue =
total; no NaN, infinity, or negative values; national stands area and density
match the matrix; per-province and per-ecoprovince sums equal national;
species x province allocation areas match each cell; area coverage and failed
cell documented. All hard checks pass.

Caveats:

1. The batch is a representative-stand, area-weighted standing-stock
   calculation. It is not an observed harvest census.
2. The residue definition is taper/bucking residue, not full aboveground
   slash. It excludes branches, bark, and foliage.
3. Recovery 0.35/0.60/0.75 is applied uniformly to every ecoprovince. No
   regional recovery evidence is available in this batch.
4. The province split is an area-share apportionment, not a province-specific
   yield or taper model. Only the ecoprovince values are direct batch outputs.
5. Cost is a flat $/GJ proxy with case multipliers. It is not a delivered-cost
   curve and does not vary with harvest level, distance, or scale.
6. The national factor is higher than the BC-only harvest-weighted factor
   because the area-weighted mix includes high-residue boreal and taiga
   ecoprovinces. Do not substitute one for the other without a harvest
   allocation.
7. The area base is gross SCANFI treed area (422.475761 Mha), with no THLB or
   operability filter (`planning/national-rsyc-backbone.md`, gap G2).

## 8. Outputs and status

All outputs are in `data/derived/national/clews_supply/rsyc_backbone/`:

- `residue_factor_by_province.csv`
- `residue_factor_by_ecoprovince.csv`
- `harvest_sensitivity.csv`
- `national_residue_factor_summary.json`
- `province_allocation.csv`
- `validation_checks.csv`
- `manifest.json`
- `NATIONAL_RESIDUE_FACTOR_REPORT.md`

Status: **complete for the requested factor and sensitivity deliverables**.
Not committed. The reference-harvest decision and any THLB/operability filter
remain open.
"""


def main() -> None:
    generated_utc = datetime.now(UTC).isoformat(timespec="seconds")
    area_join_path, area_join_note = resolve_area_join()

    cells = load_cells(MATRIX_PATH)
    stand_check = load_stand_crosscheck(STANDS_PATH, cells)
    forest_by_ecoprovince, area_by_cell = load_area_join(area_join_path)
    for cell_key, cell in cells.items():
        joined_area = area_by_cell.get(cell_key)
        if joined_area is None:
            raise NationalResidueError(f"{cell_key}: missing from area join")
        require_close(
            joined_area, cell.area_ha, label=f"{cell_key} area join vs matrix", rel_tol=1e-9
        )

    species_area, forest_by_province, weight_meta = load_province_weights(
        AREA_WEIGHTS_PATH
    )
    province_factors, allocation_rows, province_check = allocate_provinces(
        cells, species_area, forest_by_province
    )
    ecoprovince_factors = build_ecoprovince_factors(cells, forest_by_ecoprovince)
    totals = national_totals(cells)
    harvest_rows = build_harvest_rows(totals["odt_per_m3_log"])

    mapped_area = sum(forest_by_ecoprovince.values())
    require_close(
        mapped_area,
        EXPECTED_MAPPED_AREA_HA,
        label="mapped forest area",
        rel_tol=1e-8,
    )
    require_close(
        totals["area_emitted_ha"],
        EXPECTED_EMITTED_AREA_HA,
        label="emitted area",
        rel_tol=1e-9,
    )
    for ecoprovince, factor in ecoprovince_factors.items():
        require_close(
            factor.forest_area_ha,
            forest_by_ecoprovince[ecoprovince],
            label=f"{ecoprovince} forest area",
            rel_tol=1e-12,
        )

    batch_summary = load_batch_summary(SUMMARY_PATH)
    _, status_meta = load_cell_status(CELL_STATUS_PATH)
    failed_cell = status_meta["failed_cell"]
    gap_area = mapped_area - totals["area_emitted_ha"]
    coverage_data = {
        "mapped_area_ha": mapped_area,
        "gap_area_ha": gap_area,
        "gap_fraction": gap_area / mapped_area,
        "failed_cell": failed_cell,
        "status_counts": status_meta["status_counts"],
    }

    validation_data = validation_rows(
        cells=cells,
        stand_check=stand_check,
        province_check=province_check,
        totals=totals,
        province_factors=province_factors,
        ecoprovince_factors=ecoprovince_factors,
        forest_by_ecoprovince=forest_by_ecoprovince,
        batch_summary=batch_summary,
        failed_cell=failed_cell,
        status_counts=status_meta["status_counts"],
    )
    supbcw = load_current_supbcw(SUPBCW_PATH)
    bc_time = load_bc_time_path(BC_TIME_PATH)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    province_data = province_rows(
        province_factors, totals["log_m3"], totals["residue_odt"]
    )
    ecoprovince_data = ecoprovince_rows(ecoprovince_factors, totals["log_m3"])
    harvest_data = harvest_rows_table(harvest_rows)

    province_fields = list(province_data[0].keys())
    ecoprovince_fields = list(ecoprovince_data[0].keys())
    harvest_fields = list(harvest_data[0].keys())
    allocation_fields = list(allocation_rows[0].keys())
    validation_fields = ["check", "status", "value", "note"]

    write_csv(
        OUTPUT_DIR / "residue_factor_by_province.csv", province_fields, province_data
    )
    write_csv(
        OUTPUT_DIR / "residue_factor_by_ecoprovince.csv",
        ecoprovince_fields,
        ecoprovince_data,
    )
    write_csv(OUTPUT_DIR / "harvest_sensitivity.csv", harvest_fields, harvest_data)
    write_csv(
        OUTPUT_DIR / "province_allocation.csv", allocation_fields, allocation_rows
    )
    write_csv(
        OUTPUT_DIR / "validation_checks.csv", validation_fields, validation_data
    )

    inputs = [
        ("national_assortment_matrix.csv", MATRIX_PATH, "bucking matrix"),
        ("national_stands.csv", STANDS_PATH, "stand area and density cross-check"),
        ("national_batch_area_join_full.csv", area_join_path, "area join"),
        ("national_assortment_summary.json", SUMMARY_PATH, "batch summary"),
        ("national_area_weights_ecodistrict.csv", AREA_WEIGHTS_PATH, "province allocation"),
        ("cell_status.csv", CELL_STATUS_PATH, "cell status and failed-cell audit"),
        ("SUPBCW_activity_limits_base.csv", SUPBCW_PATH, "current BC comparison"),
        ("bc_residue_time_path.csv", BC_TIME_PATH, "BC 2028-2070 harvest basis for normalization"),
    ]

    national = {
        "log_m3": totals["log_m3"],
        "residue_m3": totals["residue_m3"],
        "residue_odt": totals["residue_odt"],
        "odt_per_m3_log": totals["odt_per_m3_log"],
        "effective_density_odt_m3": totals["effective_density_odt_m3"],
        "recoverable_odt_per_m3": {
            case: totals["odt_per_m3_log"] * RECOVERY[case] for case in CASES
        },
        "recoverable_pj_per_million_m3": {
            case: totals["odt_per_m3_log"] * RECOVERY[case] * LHV_GJ_PER_ODT
            for case in CASES
        },
        "recoverable_pj_per_100_million_m3": {
            case: totals["odt_per_m3_log"] * RECOVERY[case] * 100 * LHV_GJ_PER_ODT
            for case in CASES
        },
        "recoverable_odt_at_batch_standing_stock": {
            case: totals["residue_odt"] * RECOVERY[case] for case in CASES
        },
        "area_emitted_ha": totals["area_emitted_ha"],
        "forest_area_ha": mapped_area,
        "area_coverage_fraction": totals["area_emitted_ha"] / mapped_area,
    }

    summary = {
        "artifact_version": "0.1.0",
        "generated_utc": generated_utc,
        "status": "derived_provisional",
        "definitions": {
            "residue_stream": "bucking/taper residue, not full aboveground slash",
            "recovery": dict(RECOVERY),
            "recovery_basis": "roadside recovery fraction applied to gross taper residue",
            "lhv_gj_per_odt": LHV_GJ_PER_ODT,
            "pj_gj": PJ_GJ,
            "base_cost_usd_per_gj": BASE_COST_USD_PER_GJ,
            "cost_multiplier": dict(COST_MULTIPLIER),
            "province_allocation": "species-resolved SCANFI area share within ecoprovince",
        },
        "inputs": [
            {"name": name, "path": str(path), "role": role, "sha256": sha256(path), "bytes": path.stat().st_size}
            for name, path, role in inputs
        ],
        "input_path_correction": {
            "declared_area_join": str(AREA_JOIN_DECLARED_PATH),
            "used_area_join": str(area_join_path),
            "note": area_join_note,
        },
        "national": national,
        "province_factors": province_data,
        "ecoprovince_factors": ecoprovince_data,
        "harvest_sensitivity": harvest_data,
        "comparison": {
            "placeholder_50pj": PLACEHOLDER_PJ,
            "bc_only_43_523816pj": BC_ONLY_PJ,
            "current_supbcw": supbcw,
            "bc_aac_2018_m3_per_year": BC_AAC_M3,
            "bc_2028_2070_log_m3_per_year": float(bc_time["annual_log_m3"]),
            "bc_only_pj_per_100_million_m3_equivalent": BC_ONLY_PJ
            / (float(bc_time["annual_log_m3"]) / 1e6)
            * 100.0,
            "national_base_pj_per_100_million_m3": national[
                "recoverable_pj_per_100_million_m3"
            ]["base"],
            "national_to_bc_normalized_ratio": national[
                "recoverable_pj_per_100_million_m3"
            ]["base"]
            / (
                BC_ONLY_PJ
                / (float(bc_time["annual_log_m3"]) / 1e6)
                * 100.0
            ),
        },
        "coverage": {
            **coverage_data,
            "emitted_area_ha": totals["area_emitted_ha"],
            "mapped_area_ha": mapped_area,
            "failed_area_fraction_of_mapped": float(failed_cell["area_ha"]) / mapped_area,
            "other_gap_area_ha": gap_area - float(failed_cell["area_ha"]),
        },
        "validation": {
            "checks": validation_data,
            "stand_crosscheck": stand_check,
            "batch_summary_area_reconciliation": batch_summary,
            "province_allocation": province_check,
            "province_weight_rows": weight_meta,
        },
        "reference_harvest": {
            "selected": None,
            "status": "decision_required",
            "reason": "No documented national annual harvest/AAC basis in the repository; planning/national-rsyc-backbone.md gap G1 requires an explicit national AAC/harvest scenario and annualization rule.",
            "levels_presented_million_m3_per_year": list(HARVEST_LEVELS_MILLION_M3),
            "documented_provincial_basis": {
                "bc_aac_2018_m3_per_year": BC_AAC_M3,
                "note": "provincial; used only for the BC comparison",
            },
        },
        "caveats": [
            "Batch log total is a standing stock, not annual harvest.",
            "Residue is taper/bucking residue, not full aboveground slash.",
            "Province factors are area-share apportionments, not province-specific taper models.",
            "Recovery and cost are uniform envelopes with no regional evidence.",
            "Gross SCANFI treed area has no THLB or operability filter.",
            "Area-weighted standing mix may differ from a future harvest mix.",
        ],
    }

    summary_path = OUTPUT_DIR / "national_residue_factor_summary.json"
    with summary_path.open("w") as handle:
        json.dump(summary, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")

    report = build_report(
        totals=totals,
        province_rows_data=province_data,
        ecoprovince_rows_data=ecoprovince_data,
        harvest_rows_data=harvest_data,
        validation_data=validation_data,
        coverage_data=coverage_data,
        supbcw=supbcw,
        bc_time=bc_time,
        generated_utc=generated_utc,
        area_join_note=area_join_note,
        input_status={"cells": len(cells)},
    )
    report_path = OUTPUT_DIR / "NATIONAL_RESIDUE_FACTOR_REPORT.md"
    report_path.write_text(report)

    output_paths = [
        OUTPUT_DIR / "residue_factor_by_province.csv",
        OUTPUT_DIR / "residue_factor_by_ecoprovince.csv",
        OUTPUT_DIR / "harvest_sensitivity.csv",
        OUTPUT_DIR / "province_allocation.csv",
        OUTPUT_DIR / "validation_checks.csv",
        summary_path,
        report_path,
    ]
    manifest = {
        "artifact_version": "0.1.0",
        "generated_utc": generated_utc,
        "status": "derived_provisional",
        "builder": str(Path(__file__).resolve().relative_to(REPO_ROOT)),
        "outputs": [
            {
                "path": str(path.relative_to(REPO_ROOT)),
                "sha256": sha256(path),
                "bytes": path.stat().st_size,
            }
            for path in output_paths
        ],
        "inputs": summary["inputs"],
        "key_totals": {
            "log_m3": totals["log_m3"],
            "residue_odt": totals["residue_odt"],
            "odt_per_m3_log": totals["odt_per_m3_log"],
            "area_emitted_ha": totals["area_emitted_ha"],
            "mapped_area_ha": mapped_area,
        },
        "assumptions": [
            "The batch is area-weighted and emitted at ecoprovince level.",
            "Province shares use species-resolved SCANFI area within ecoprovince.",
            "Recovery envelope is low 0.35, base 0.60, high 0.75.",
            "Cost is $2.456978/GJ base with low/high multipliers 1.30/0.80.",
            "No national reference harvest is selected.",
        ],
        "notes": [
            area_join_note or "declared area-join path present",
            "The failed PINU.BAN Great Bear Lowlands cell is excluded from all totals.",
        ],
    }
    manifest_path = OUTPUT_DIR / "manifest.json"
    with manifest_path.open("w") as handle:
        json.dump(manifest, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")

    print(f"Wrote {len(output_paths) + 1} files under {OUTPUT_DIR}")
    print(f"national ODT/m3 log: {totals['odt_per_m3_log']:.9f}")
    print(
        "PJ per 100 million m3: "
        + ", ".join(
            f"{case}={national['recoverable_pj_per_100_million_m3'][case]:.6f}"
            for case in CASES
        )
    )


if __name__ == "__main__":
    main()
