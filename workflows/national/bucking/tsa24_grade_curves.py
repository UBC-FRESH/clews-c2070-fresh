"""Historical TSA24 grade compiler from TIPSY ``.taf`` tables.

.. deprecated::
   The active compiler is
   :mod:`workflows.national.bucking.tsa24_grade_curves_btc`, which reads the
   refreshed BTC ``/TSR`` output.  This module is retained for provenance and
   tests of the earlier nominal-site-index path; it is no longer called by
   :mod:`workflows.national.bucking.compile_tsa24_grade_curves`.

The earlier path read the TIPSY database directly, selected the untreated run
block closest to each AU's planted density, and rescaled grade shares onto the
FEMIC managed volume.

The TIPSY ``.taf`` tables are nominal-site-index surfaces.  The AU-specific
grade volumes here are therefore **share-preserving rescales**: the raw TAF
grade shares are kept, while the grade total is scaled to the FEMIC managed
merchantable volume for the same AU and age.  The same volume ratio is applied
to residual fibre.  Every row records the raw TAF volume, the scale factors,
and whether the age is beyond the TAF age range, so the transformation is
auditable rather than silent.

The residual-fibre columns are TIPSY/SYLVER **mill-processing** residues
(chips, sawdust, shavings, trim, bark), not harvest slash.  They are not a
like-for-like benchmark for the bucking pilot's ``taper total - recovered
logs`` residue ledger.  The compilation carries them because the model
surface exists; the validation module states the semantic gap explicitly.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping, Sequence

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from fresh_tipsy.tables.parsers import RunBlock, TIPSYTable

__all__ = [
    "DEFAULT_AGE_MAX",
    "DEFAULT_AGE_STEP",
    "GRADE_NAMES",
    "INDUSTRIAL_NAMES",
    "RESIDUE_NAMES",
    "SPECIES_TABLE_STEMS",
    "compile_grade_curves",
    "resolve_species_table",
]

DEFAULT_AGE_MAX = 350
DEFAULT_AGE_STEP = 10
DEFAULT_SPECIES_DENSITY = 1600.0

GRADE_NAMES: tuple[str, ...] = (
    "Logs_Grade_D",
    "Logs_Grade_F",
    "Logs_Grade_H",
    "Logs_Grade_I",
    "Logs_Grade_J",
    "Logs_Grade_U",
    "Logs_Grade_X",
    "Logs_Grade_Y",
)

RESIDUE_NAMES: tuple[str, ...] = (
    "Residual_Chips",
    "Residual_Sawdust",
    "Residual_Shavings",
    "Residual_Trim",
    "Residual_Bark",
)

INDUSTRIAL_NAMES: tuple[str, ...] = (
    "Industrial_Logs_D38L13",
    "Industrial_Logs_D38L11",
    "Industrial_Logs_D38L8",
    "Industrial_Logs_D30L13",
    "Industrial_Logs_D30L11",
    "Industrial_Logs_D30L8",
    "Industrial_Logs_D20L13",
    "Industrial_Logs_D20L11",
    "Industrial_Logs_D20L8",
    "Industrial_Logs_D125L13",
    "Industrial_Logs_D125L11",
    "Industrial_Logs_D125L8",
    "Industrial_Logs_D125L63",
    "Industrial_Logs_D125L51",
    "Industrial_Logs_D125L5",
    "Industrial_Logs_D305",
    "Industrial_Logs_D254",
    "Industrial_Logs_D203",
    "Industrial_Logs_D178",
    "Industrial_Logs_D152",
)

#: TIPSY species table used for each TSA24 input species.  ``SE`` and ``BL``
#: have no dedicated table in this TIPSY database; ``vriSpecies.txt`` maps
#: their interior volume surrogate to ``SW``.  The surrogate is recorded in
#: the output rather than hidden.
SPECIES_TABLE_STEMS: Mapping[str, str] = {
    "SW": "sw 0530P00",
    "SE": "sw 0530P00",
    "BL": "sw 0530P00",
    "S": "sw 0530P00",
    "SB": "sw 0530P00",
    "SX": "sw 0530P00",
    "PL": "pli0530P00",
    "PLI": "pli0530P00",
    "PLC": "pli0530P00",
    "FD": "fdi0535P00",
    "FDI": "fdi0535P00",
    "AT": "at 1032P00",
    "CW": "cw 1040P00",
    "HW": "hwi1030P00",
    "HWI": "hwi1030P00",
}

_SPECIES_SI_COLUMNS: Mapping[str, str] = {
    "SW": "sw_si",
    "SE": "se_si",
    "BL": "bl_si",
    "PL": "pl_si",
    "PLI": "pl_si",
    "FD": "fd_si",
    "FDI": "fd_si",
    "AT": "at_si",
    "CW": "cw_si",
    "HW": "hw_si",
    "HWI": "hw_si",
}


@dataclass(frozen=True)
class SpeciesTable:
    """Resolved TIPSY species table for one TSA24 species code."""

    requested_species: str
    table_species: str
    stem: str
    path: Path
    table: TIPSYTable
    nominal_site_index: int
    surrogate: bool


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@lru_cache(maxsize=16)
def _load_table(path: Path) -> TIPSYTable:
    try:
        from fresh_tipsy.tables.parsers import parse_taf_file
    except ImportError as exc:  # pragma: no cover - environment guard
        raise RuntimeError(
            "fresh-tipsy is required to parse TIPSY .taf tables; install it "
            "editable or set PYTHONPATH to the fresh-tipsy src directory"
        ) from exc
    return parse_taf_file(path)


@lru_cache(maxsize=16)
def _taf_columns() -> Mapping[str, int]:
    try:
        from fresh_tipsy.tables.columns import load_columns
    except ImportError as exc:  # pragma: no cover - environment guard
        raise RuntimeError(
            "fresh-tipsy is required to read the TAF column map"
        ) from exc
    return {spec.name: spec.position for spec in load_columns("taf")}


def resolve_species_table(
    species: str,
    database_root: Path,
) -> SpeciesTable:
    """Resolve a TSA24 species code to a parsed TIPSY ``.taf`` table.

    Parameters
    ----------
    species : str
        TSA24 species code, for example ``SW`` or ``FDI``.
    database_root : Path
        Directory containing the TIPSY ``.tab``/``.taf`` pairs.

    Returns
    -------
    SpeciesTable
        Parsed table plus the nominal site index and surrogate flag.

    Raises
    ------
    ValueError
        When the species has no mapped TIPSY table or the table is missing.
    """
    code = str(species).strip().upper()
    stem = SPECIES_TABLE_STEMS.get(code)
    if stem is None:
        raise ValueError(
            f"no TIPSY .taf table mapping for TSA24 species {code!r}; "
            f"mapped species: {', '.join(sorted(SPECIES_TABLE_STEMS))}"
        )
    path = Path(database_root) / f"{stem}.taf"
    if not path.exists():
        raise FileNotFoundError(f"TIPSY .taf table not found: {path}")
    table = _load_table(path)
    table_species = table.header.code.upper()
    nominal = int(table.header.tass_site_index or 0)
    return SpeciesTable(
        requested_species=code,
        table_species=table_species,
        stem=stem,
        path=path,
        table=table,
        nominal_site_index=nominal,
        surrogate=code != table_species,
    )


def select_untreated_block(table: TIPSYTable, target_density: float) -> RunBlock:
    """Select the untreated run block closest to ``target_density``.

    Parameters
    ----------
    table : TIPSYTable
        Parsed TAF table.
    target_density : float
        TSA24 planted or observed stems per hectare.

    Returns
    -------
    RunBlock
        Untreated block with the closest establishment density.

    Raises
    ------
    ValueError
        When the table has no untreated block.
    """
    candidates = [
        block
        for block in table.blocks
        if block.marker.pct_density == 0 and block.marker.ct_density == 0
    ]
    if not candidates:
        raise ValueError(
            f"TIPSY table {table.header.file!r} has no untreated run block"
        )
    return min(
        candidates,
        key=lambda block: (
            abs(block.marker.initial_density - float(target_density)),
            block.marker.initial_density,
        ),
    )


def _interpolate(
    block: RunBlock,
    position: int,
    target_ages: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Interpolate one TAF column onto the target age grid.

    Parameters
    ----------
    block : RunBlock
        Run block to read.
    position : int
        Zero-based TAF field position.
    target_ages : numpy.ndarray
        Increasing target ages.

    Returns
    -------
    values : numpy.ndarray
        Interpolated values.  Missing columns are all zero.
    extrapolated : numpy.ndarray
        Boolean mask for target ages beyond the last populated source age.
    """
    rows = [row for row in block.rows if row.age is not None]
    ages = np.array([row.age for row in rows], dtype=float)
    values = np.array(
        [
            float(row.fields[position]) if row.fields[position].strip() else np.nan
            for row in rows
        ],
        dtype=float,
    )
    finite = np.isfinite(values)
    if not finite.any():
        return np.zeros_like(target_ages, dtype=float), np.ones_like(
            target_ages, dtype=bool
        )
    source_ages = ages[finite]
    source_values = values[finite]
    interpolated = np.interp(
        target_ages,
        source_ages,
        source_values,
        left=0.0,
        right=float(source_values[-1]),
    )
    extrapolated = target_ages > float(source_ages[-1])
    return interpolated, extrapolated


def _taf_curve(
    block: RunBlock,
    target_ages: np.ndarray,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Extract grade, residue, and industrial columns from one run block."""
    positions = _taf_columns()
    frame = pd.DataFrame({"age": target_ages})
    for name in (*GRADE_NAMES, *RESIDUE_NAMES, *INDUSTRIAL_NAMES):
        values, _ = _interpolate(block, positions[name], target_ages)
        frame[name] = values
    volume_columns = [name for name in positions if name.startswith("Volume_Diameter_Class_")]
    frame["taf_volume125_m3ha"] = 0.0
    for name in volume_columns:
        values, _ = _interpolate(block, positions[name], target_ages)
        frame["taf_volume125_m3ha"] += values
    frame["taf_grade_sum_m3ha"] = frame[list(GRADE_NAMES)].sum(axis=1)
    frame["taf_residual_fibre_odt_ha"] = frame[list(RESIDUE_NAMES)].sum(axis=1)
    source_volume_ages: list[float] = []
    for row in block.rows:
        if row.age is None:
            continue
        total = 0.0
        for name in volume_columns:
            raw = row.fields[positions[name]]
            if raw.strip():
                total += float(raw)
        if total > 0:
            source_volume_ages.append(float(row.age))
    last_source_age = max(source_volume_ages) if source_volume_ages else float(target_ages.min())
    frame["taf_extrapolated"] = target_ages > last_source_age
    frame = frame.rename(
        columns={name: f"{name}_taf_nominal" for name in RESIDUE_NAMES}
    )
    metadata = {
        "taf_run_initial_density": int(block.marker.initial_density),
        "taf_run_pct_density": int(block.marker.pct_density),
        "taf_run_ct_density": int(block.marker.ct_density),
        "taf_first_age": float(min(row.age for row in block.rows if row.age is not None)),
        "taf_last_age": float(max(row.age for row in block.rows if row.age is not None)),
    }
    return frame, metadata


def _base_curve(
    curves: pd.DataFrame,
    au_id: int,
    target_ages: np.ndarray,
) -> pd.DataFrame:
    """Interpolate the FEMIC managed curve onto the target age grid."""
    group = curves[curves["AU"].astype(int) == int(au_id)].sort_values("Age")
    if group.empty:
        raise ValueError(f"no FEMIC managed curve for AU {au_id}")
    frame = pd.DataFrame({"age": target_ages})
    for source, target in (
        ("Yield", "volume"),
        ("Height", "height"),
        ("DBHq", "dbhq"),
        ("TPH", "tph"),
        ("GrossYield", "gross_yield"),
        ("CrownCover", "crown_cover"),
    ):
        values = pd.to_numeric(group[source], errors="coerce").to_numpy(dtype=float)
        ages = pd.to_numeric(group["Age"], errors="coerce").to_numpy(dtype=float)
        finite = np.isfinite(values) & np.isfinite(ages)
        if finite.any():
            frame[target] = np.interp(
                target_ages,
                ages[finite],
                values[finite],
                left=0.0,
                right=float(values[finite][-1]),
            )
        else:
            frame[target] = np.nan
    return frame


def _feature_row(inputs: pd.DataFrame, feature_id: int) -> pd.Series:
    matches = inputs[inputs["feature_id"].astype(int) == int(feature_id)]
    if matches.empty:
        raise ValueError(f"feature_id {feature_id} not found in 03_input")
    return matches.iloc[0]


def _au_row(au_table: pd.DataFrame, feature_id: int) -> pd.Series:
    managed = pd.to_numeric(au_table["source_managed_local_au_id"], errors="coerce")
    local = pd.to_numeric(au_table["source_local_au_id"], errors="coerce")
    matches = au_table[(managed == feature_id) | (local == feature_id)]
    if matches.empty:
        raise ValueError(f"feature_id {feature_id} not found in au_table")
    return matches.iloc[0]


def _planted_density(row: pd.Series) -> float:
    total = 0.0
    for index in range(1, 6):
        value = row.get(f"planted_density{index}")
        if pd.notna(value):
            total += float(value)
    return total


def _au_site_index(row: pd.Series, species: str) -> float | None:
    column = _SPECIES_SI_COLUMNS.get(str(species).upper())
    if column is None:
        return None
    value = row.get(column)
    if pd.notna(value) and float(value) > 0:
        return float(value)
    return None


def _age_grid(age_max: int, age_step: int) -> np.ndarray:
    if age_max <= 0 or age_step <= 0:
        raise ValueError("age_max and age_step must be positive")
    return np.arange(0, int(age_max) + 1, int(age_step), dtype=float)


def _check_base_curve_matches_output(
    curves: pd.DataFrame,
    output: pd.DataFrame,
    ages: Sequence[int],
) -> None:
    """Fail fast if the managed curve disagrees with ``04_output``."""
    output_by_au = output.set_index("feature_id")
    for au_id, group in curves.groupby("AU"):
        if int(au_id) not in output_by_au.index:
            raise ValueError(f"AU {au_id} is missing from 04_output")
        row = output_by_au.loc[int(au_id)]
        for age in ages:
            column = f"MVcon_{int(age)}"
            if column not in output.columns:
                continue
            expected = row[column]
            actual = group.loc[group["Age"].astype(int) == int(age), "Yield"]
            if actual.empty:
                continue
            if pd.isna(expected) and pd.isna(actual.iloc[0]):
                continue
            if not np.isclose(float(actual.iloc[0]), float(expected), atol=1e-6):
                raise ValueError(
                    f"managed curve mismatch for AU {au_id} age {age}: "
                    f"tipsy_curves={actual.iloc[0]!r} 04_output={expected!r}"
                )


def compile_grade_curves(
    *,
    femic_root: Path,
    tipsy_database: Path,
    out_dir: Path,
    age_max: int = DEFAULT_AGE_MAX,
    age_step: int = DEFAULT_AGE_STEP,
) -> dict[str, Any]:
    """Compile the AU grade/residue table and provenance manifest.

    Parameters
    ----------
    femic_root : Path
        FEMIC TSA24 instance root containing ``data/03_input-tsa24.csv``,
        ``data/04_output-tsa24.csv``, ``data/tipsy_curves_tsa24.csv``, and
        ``data/model_input_bundle/au_table.csv``.
    tipsy_database : Path
        Directory containing the TIPSY ``.tab``/``.taf`` pairs.
    out_dir : Path
        Output directory for the compiled tables and manifest.
    age_max : int, optional
        Maximum age in the standard grid.
    age_step : int, optional
        Age step in years.

    Returns
    -------
    dict
        Manifest payload with output paths and SHA-256 hashes.

    Raises
    ------
    FileNotFoundError
        When an input file or TIPSY table is missing.
    ValueError
        When the input schema or a density/age guard fails.
    """
    femic_root = Path(femic_root)
    tipsy_database = Path(tipsy_database)
    out_dir = Path(out_dir)
    data_dir = femic_root / "data"
    input_path = data_dir / "03_input-tsa24.csv"
    output_path = data_dir / "04_output-tsa24.csv"
    curves_path = data_dir / "tipsy_curves_tsa24.csv"
    au_path = data_dir / "model_input_bundle" / "au_table.csv"
    for path in (input_path, output_path, curves_path, au_path):
        if not path.exists():
            raise FileNotFoundError(f"TSA24 input not found: {path}")

    inputs = pd.read_csv(input_path)
    output = pd.read_csv(output_path)
    curves = pd.read_csv(curves_path)
    au_table = pd.read_csv(au_path)
    age_grid = _age_grid(age_max, age_step)
    integer_ages = [int(age) for age in age_grid]
    _check_base_curve_matches_output(curves, output, integer_ages)

    managed_rows: list[pd.DataFrame] = []
    industrial_rows: list[pd.DataFrame] = []
    coverage_rows: list[dict[str, Any]] = []
    table_records: dict[str, dict[str, Any]] = {}

    for feature_id in sorted(inputs["feature_id"].astype(int).unique()):
        feature = _feature_row(inputs, feature_id)
        au_row = _au_row(au_table, feature_id)
        leading_species = str(feature["planted_species1"]).strip().upper()
        species_table = resolve_species_table(leading_species, tipsy_database)
        density = _planted_density(feature)
        block = select_untreated_block(species_table.table, density)
        taf_frame, taf_meta = _taf_curve(block, age_grid)
        base_frame = _base_curve(curves, feature_id, age_grid)

        merged = pd.concat([base_frame, taf_frame.drop(columns=["age"])], axis=1)
        volume = merged["volume"].astype(float)
        grade_sum = merged["taf_grade_sum_m3ha"].astype(float)
        taf_volume = merged["taf_volume125_m3ha"].astype(float)
        merged["taf_grade_scale"] = np.where(
            grade_sum > 0,
            volume / grade_sum,
            0.0,
        )
        merged["taf_residue_scale"] = np.where(
            taf_volume > 0,
            volume / taf_volume,
            0.0,
        )
        for name in GRADE_NAMES:
            merged[name] = merged[name].astype(float) * merged["taf_grade_scale"]
        for name in RESIDUE_NAMES:
            merged[f"{name}_ODT_ha"] = (
                merged[f"{name}_taf_nominal"].astype(float)
                * merged["taf_residue_scale"]
            )
        merged["residual_fibre_odt_ha"] = merged[
            [f"{name}_ODT_ha" for name in RESIDUE_NAMES]
        ].sum(axis=1)
        merged["grade_unallocated_m3ha"] = (
            volume - merged[list(GRADE_NAMES)].sum(axis=1)
        ).clip(lower=0.0)
        merged["grade_coverage_fraction"] = np.where(
            volume > 0,
            merged[list(GRADE_NAMES)].sum(axis=1) / volume,
            np.nan,
        )
        merged["residue_odt_per_m3"] = np.where(
            volume > 0,
            merged["residual_fibre_odt_ha"] / volume,
            np.nan,
        )
        merged.insert(0, "au_id", feature_id)
        merged.insert(1, "au_table_au_id", int(au_row["au_id"]))
        merged.insert(2, "managed_curve_id", int(au_row["managed_curve_id"]))
        merged.insert(3, "stratum_code", str(au_row["stratum_code"]))
        merged.insert(4, "si_level", str(au_row["si_level"]))
        merged.insert(5, "leading_species", leading_species)
        merged.insert(6, "taf_table", species_table.stem)
        merged.insert(7, "taf_table_species", species_table.table_species)
        merged.insert(8, "taf_nominal_site_index", species_table.nominal_site_index)
        merged.insert(9, "au_site_index", _au_site_index(feature, leading_species))
        merged.insert(10, "planted_density_tph", density)
        merged = merged.rename(columns={"age": "age"})
        managed_rows.append(merged)

        industrial = taf_frame[
            ["age", *INDUSTRIAL_NAMES, "taf_volume125_m3ha", "taf_extrapolated"]
        ].copy()
        industrial.insert(0, "au_id", feature_id)
        industrial.insert(1, "taf_table", species_table.stem)
        industrial.insert(2, "taf_nominal_site_index", species_table.nominal_site_index)
        industrial_rows.append(industrial)

        site_index = _au_site_index(feature, leading_species)
        coverage_rows.append(
            {
                "au_id": feature_id,
                "au_table_au_id": int(au_row["au_id"]),
                "managed_curve_id": int(au_row["managed_curve_id"]),
                "stratum_code": str(au_row["stratum_code"]),
                "si_level": str(au_row["si_level"]),
                "bec_zone": str(feature.get("bec_zone", "")),
                "leading_species": leading_species,
                "taf_table_species": species_table.table_species,
                "taf_table": species_table.stem,
                "taf_nominal_site_index": species_table.nominal_site_index,
                "au_site_index": site_index,
                "site_index_delta": (
                    None if site_index is None else site_index - species_table.nominal_site_index
                ),
                "planted_density_tph": density,
                **taf_meta,
                "extrapolated_ages": bool(taf_frame["taf_extrapolated"].any()),
                "grade_scale_basis": "taf_grade_shares_x_femic_managed_volume",
                "residue_scale_basis": "taf_residue_per_taf_volume_x_femic_managed_volume",
                "species_surrogate": species_table.surrogate,
            }
        )
        table_records[species_table.stem] = {
            "stem": species_table.stem,
            "path": f"Database/{species_table.stem}.taf",
            "sha256": _sha256(species_table.path),
            "header_code": species_table.table_species,
            "nominal_site_index": species_table.nominal_site_index,
            "first_age": float(min(
                row.age for row in species_table.table.blocks[0].rows if row.age is not None
            )),
            "last_age": float(max(
                row.age
                for block in species_table.table.blocks
                for row in block.rows
                if row.age is not None
            )),
        }

    managed = pd.concat(managed_rows, ignore_index=True)
    industrial = pd.concat(industrial_rows, ignore_index=True)
    coverage = pd.DataFrame(coverage_rows)

    species_rows: list[pd.DataFrame] = []
    for requested, stem in {
        "SW": "sw 0530P00",
        "PLI": "pli0530P00",
        "FDI": "fdi0535P00",
        "AT": "at 1032P00",
        "CW": "cw 1040P00",
        "HWI": "hwi1030P00",
    }.items():
        species_table = resolve_species_table(requested, tipsy_database)
        block = select_untreated_block(species_table.table, DEFAULT_SPECIES_DENSITY)
        frame, meta = _taf_curve(block, age_grid)
        frame.insert(0, "species_code", requested)
        frame.insert(1, "taf_table", stem)
        frame.insert(2, "taf_nominal_site_index", species_table.nominal_site_index)
        frame.insert(3, "taf_run_initial_density", meta["taf_run_initial_density"])
        species_rows.append(frame)
    species_curves = pd.concat(species_rows, ignore_index=True)

    out_dir.mkdir(parents=True, exist_ok=True)
    managed_path = out_dir / "managed_au_curves.csv"
    industrial_path = out_dir / "managed_au_industrial_sorts.csv"
    coverage_path = out_dir / "au_grade_coverage.csv"
    species_path = out_dir / "species_grade_curves.csv"
    managed.to_csv(managed_path, index=False, float_format="%.6f")
    industrial.to_csv(industrial_path, index=False, float_format="%.6f")
    coverage.to_csv(coverage_path, index=False, float_format="%.6f")
    species_curves.to_csv(species_path, index=False, float_format="%.6f")

    manifest = {
        "created_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "status": "compiled_from_tipsy_taf",
        "provenance": {
            "source_model": "TIPSY 4.5 / SYLVER",
            "database_title": "Ministry Standard Database, February 2022",
            "database_root": "TIPSY_DATABASE (host-local path; not recorded)",
            "citation": (
                "Province of British Columbia, TIPSY 4.5, "
                "Database/<file>; extracted by fresh-tipsy."
            ),
            "evidence_tier": "model_output",
            "grade_status": "model_output_not_field_grade",
        },
        "inputs": [
            {
                "label": label,
                "path": logical_path,
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
            for label, path, logical_path in (
                ("03_input", input_path, "femic-tsa24-instance/data/03_input-tsa24.csv"),
                ("04_output", output_path, "femic-tsa24-instance/data/04_output-tsa24.csv"),
                (
                    "tipsy_curves",
                    curves_path,
                    "femic-tsa24-instance/data/tipsy_curves_tsa24.csv",
                ),
                (
                    "au_table",
                    au_path,
                    "femic-tsa24-instance/data/model_input_bundle/au_table.csv",
                ),
            )
        ],
        "taf_tables": sorted(table_records.values(), key=lambda row: row["stem"]),
        "outputs": [
            {
                "label": label,
                "path": path.name,
                "rows": int(len(frame)),
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
            for label, path, frame in (
                ("managed_au_curves", managed_path, managed),
                ("managed_au_industrial_sorts", industrial_path, industrial),
                ("au_grade_coverage", coverage_path, coverage),
                ("species_grade_curves", species_path, species_curves),
            )
        ],
        "coverage": {
            "au_count": int(managed["au_id"].nunique()),
            "age_count": int(managed["age"].nunique()),
            "rows": int(len(managed)),
            "species_tables": sorted({str(value) for value in managed["taf_table"]}),
            "nominal_site_indices": {
                str(key): int(value)
                for key, value in managed.groupby("taf_table")["taf_nominal_site_index"]
                .first()
                .items()
            },
        },
        "transformations": {
            "grade_volumes": (
                "Raw TAF Logs_Grade_* shares multiplied by the FEMIC managed "
                "AU volume for the same age; grade columns sum to volume."
            ),
            "residual_fibre": (
                "Raw TAF Residual_* ODT/ha multiplied by "
                "FEMIC managed volume / TAF Volume_Diameter_Class sum. "
                "TIPSY residuals are mill-processing residues, not harvest slash."
            ),
            "age_extrapolation": (
                "Ages beyond the last populated TAF age are held flat and "
                "flagged in taf_extrapolated."
            ),
        },
        "limitations": [
            "The TAF tables are nominal-site-index surfaces (SW 20, PLI 23, FDI 25).",
            "SE and BL use the SW table as the interior volume surrogate.",
            "Grade volumes are share-preserving rescales, not independent AU grade runs.",
            "Residual fibre is mill processing residue, not harvest residue.",
        ],
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest
