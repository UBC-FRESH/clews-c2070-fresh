"""Compile TSA24 managed-AU grade and residue curves from BTC ``/TSR`` output.

The pre-BTC compiler read TIPSY 4.5 ``.taf`` tables at nominal site index and
rescaled grade shares onto the FEMIC managed volume.  That path is retained in
:mod:`workflows.national.bucking.tsa24_grade_curves` for historical context, but
it is not the benchmark source.

This module consumes the refreshed FEMIC Stage 01b artifacts produced by::

    python -m femic tipsy run-btc ... --indicator-bank log-grades \
        --indicator-bank residual-fibre
    python -m femic tsa btc-post-tipsy ... --indicator-bank log-grades \
        --indicator-bank residual-fibre

The BTC output is already AU-specific and age-specific.  No nominal-site-index
substitution, species surrogate, or grade-share rescaling is applied.  Values
are copied from ``Logs_Grade_*`` and ``Residual_*`` in
``data/tipsy_curves_tsa24.csv`` (which is itself parsed from
``data/04_output-tsa24.csv``).

Residual fibre remains TIPSY/SYLVER **mill-processing** residue
(chips, sawdust, shavings, trim, bark), not harvest slash.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .tsa24_grade_curves import (
    DEFAULT_AGE_MAX,
    DEFAULT_AGE_STEP,
    GRADE_NAMES,
    INDUSTRIAL_NAMES,
    RESIDUE_NAMES,
)

__all__ = ["compile_grade_curves_from_btc"]

_SPECIES_SI_COLUMNS: Mapping[str, str] = {
    "SW": "sw_si",
    "SX": "sw_si",
    "S": "sw_si",
    "SB": "sw_si",
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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_required(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"TSA24 BTC artifact not found: {path}")
    return pd.read_csv(path)


def _age_grid(age_max: int, age_step: int) -> np.ndarray:
    if age_max <= 0 or age_step <= 0:
        raise ValueError("age_max and age_step must be positive")
    return np.arange(0, int(age_max) + 1, int(age_step), dtype=float)


def _interpolate_curve(
    group: pd.DataFrame,
    target_ages: np.ndarray,
    columns: Sequence[str],
) -> tuple[pd.DataFrame, np.ndarray]:
    """Interpolate one AU curve onto the requested age grid."""
    ordered = group.sort_values("Age")
    source_ages = pd.to_numeric(ordered["Age"], errors="coerce").to_numpy(dtype=float)
    frame = pd.DataFrame({"age": target_ages})
    for column in columns:
        values = pd.to_numeric(ordered[column], errors="coerce").to_numpy(dtype=float)
        finite = np.isfinite(values) & np.isfinite(source_ages)
        if not finite.any():
            frame[column] = np.nan
            continue
        frame[column] = np.interp(
            target_ages,
            source_ages[finite],
            values[finite],
            left=float(values[finite][0]),
            right=float(values[finite][-1]),
        )
    last_age = float(np.nanmax(source_ages))
    return frame, target_ages > last_age


def _planted_density(row: pd.Series) -> float:
    total = 0.0
    for index in range(1, 6):
        value = row.get(f"planted_density{index}")
        if pd.notna(value):
            total += float(value)
    return total


def _site_index(row: pd.Series, species: str) -> float | None:
    column = _SPECIES_SI_COLUMNS.get(str(species).strip().upper())
    if column is None:
        return None
    value = row.get(column)
    if pd.notna(value) and float(value) > 0:
        return float(value)
    return None


def _au_row(au_table: pd.DataFrame, feature_id: int) -> pd.Series:
    """Resolve the au_table row for one managed BTC feature id."""
    managed = pd.to_numeric(au_table["source_managed_local_au_id"], errors="coerce")
    local = pd.to_numeric(au_table["source_local_au_id"], errors="coerce")
    matches = au_table[(managed == feature_id) | (local == feature_id)]
    if matches.empty:
        raise ValueError(f"feature_id {feature_id} not found in au_table")
    managed_matches = matches[pd.to_numeric(matches["source_managed_local_au_id"], errors="coerce") == feature_id]
    return (managed_matches if not managed_matches.empty else matches).iloc[0]


def _feature_row(inputs: pd.DataFrame, feature_id: int) -> pd.Series:
    matches = inputs[inputs["feature_id"].astype(int) == int(feature_id)]
    if matches.empty:
        raise ValueError(f"feature_id {feature_id} not found in 03_input")
    return matches.iloc[0]


def _grade_columns(curves: pd.DataFrame) -> list[str]:
    missing = [name for name in GRADE_NAMES if name not in curves.columns]
    if missing:
        raise ValueError(
            "BTC curves are missing requested grade columns: " + ", ".join(missing)
        )
    return list(GRADE_NAMES)


def _residue_columns(curves: pd.DataFrame) -> list[str]:
    missing = [name for name in RESIDUE_NAMES if name not in curves.columns]
    if missing:
        raise ValueError(
            "BTC curves are missing requested residual columns: " + ", ".join(missing)
        )
    return list(RESIDUE_NAMES)


def _industrial_columns(curves: pd.DataFrame) -> list[str]:
    return [name for name in INDUSTRIAL_NAMES if name in curves.columns]


def _coverage_row(
    frame: pd.DataFrame,
    *,
    feature_id: int,
    au_row: pd.Series,
    feature: pd.Series,
    leading_species: str,
    site_index: float | None,
    density: float,
) -> dict[str, Any]:
    grade_total = frame["grade_total_m3ha"].astype(float)
    grade_coverage = frame["grade_coverage_fraction"].astype(float)
    residue = frame["residual_fibre_odt_ha"].astype(float)
    zero_grades = [
        name.removeprefix("Logs_Grade_")
        for name in GRADE_NAMES
        if float(frame[name].abs().max()) == 0.0
    ]
    return {
        "au_id": feature_id,
        "au_table_au_id": int(au_row["au_id"]),
        "managed_curve_id": int(au_row["managed_curve_id"]),
        "stratum_code": str(au_row["stratum_code"]),
        "si_level": str(au_row["si_level"]),
        "bec_zone": str(au_row["stratum_code"]).partition("_")[0],
        "leading_species": leading_species,
        "planted_species1": str(feature.get("planted_species1", "")),
        "au_site_index": site_index,
        "planted_density_tph": density,
        "age_min": float(frame["age"].min()),
        "age_max": float(frame["age"].max()),
        "age_count": int(frame["age"].nunique()),
        "grade_total_min_m3ha": float(grade_total.min()),
        "grade_total_mean_m3ha": float(grade_total.mean()),
        "grade_total_max_m3ha": float(grade_total.max()),
        "grade_coverage_min": float(grade_coverage.min()),
        "grade_coverage_mean": float(grade_coverage.mean()),
        "grade_coverage_max": float(grade_coverage.max()),
        "residual_odt_ha_mean": float(residue.mean()),
        "residual_odt_ha_max": float(residue.max()),
        "residual_odt_per_m3_mean": float(
            frame["residue_odt_per_m3"].replace([np.inf, -np.inf], np.nan).mean()
        ),
        "zero_grade_columns": ",".join(zero_grades),
        "source": "btc_tsr",
    }


_SPECIES_CURVE_CODE: Mapping[str, str] = {
    "SW": "SW",
    "SE": "SE",
    "BL": "BL",
    "S": "SW",
    "SB": "SW",
    "SX": "SW",
    "PL": "PLI",
    "PLI": "PLI",
    "PLC": "PLI",
    "FD": "FDI",
    "FDI": "FDI",
    "AT": "AT",
    "CW": "CW",
    "HW": "HWI",
    "HWI": "HWI",
}


def _stratum_species(stratum_code: str) -> str:
    """Return the leading species token from an AU stratum code."""
    _, _, species_part = str(stratum_code).partition("_")
    tokens = [token.strip() for token in species_part.split("+") if token.strip()]
    if not tokens:
        raise ValueError(f"AU stratum code has no species token: {stratum_code!r}")
    return tokens[0]


def _species_curves(managed: pd.DataFrame) -> pd.DataFrame:
    """Average AU curves by leading species for unmatched-plot fallback."""
    value_columns = [
        "volume",
        "height",
        "dbhq",
        "tph",
        "gross_yield",
        "crown_cover",
        "grade_total_m3ha",
        "grade_coverage_fraction",
        "residual_fibre_odt_ha",
        "residue_odt_per_m3",
        *GRADE_NAMES,
        *RESIDUE_NAMES,
    ]
    managed = managed.copy()
    managed["species_code"] = managed["leading_species"].map(
        lambda value: _SPECIES_CURVE_CODE.get(str(value).strip().upper(), str(value).strip().upper())
    )
    rows: list[pd.DataFrame] = []
    for species_code, group in managed.groupby("species_code", dropna=True):
        numeric = group.groupby("age", dropna=True)[value_columns].mean().reset_index()
        numeric.insert(0, "species_code", str(species_code))
        numeric["au_count"] = int(group["au_id"].nunique())
        numeric["source"] = "btc_tsr_species_mean"
        rows.append(numeric)
    if not rows:
        return pd.DataFrame(
            columns=[
                "species_code",
                "age",
                *value_columns,
                "au_count",
                "source",
            ]
        )
    return pd.concat(rows, ignore_index=True)


def compile_grade_curves_from_btc(
    *,
    femic_root: Path,
    out_dir: Path,
    age_max: int = DEFAULT_AGE_MAX,
    age_step: int = DEFAULT_AGE_STEP,
) -> dict[str, Any]:
    """Compile AU grade/residue curves from refreshed BTC ``/TSR`` artifacts.

    Parameters
    ----------
    femic_root : Path
        FEMIC TSA24 instance root containing ``data/03_input-tsa24.csv``,
        ``data/04_output-tsa24.csv``, ``data/tipsy_curves_tsa24.csv``, and
        ``data/model_input_bundle/au_table.csv``.
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
    """
    femic_root = Path(femic_root)
    out_dir = Path(out_dir)
    data_dir = femic_root / "data"
    input_path = data_dir / "03_input-tsa24.csv"
    output_path = data_dir / "04_output-tsa24.csv"
    curves_path = data_dir / "tipsy_curves_tsa24.csv"
    au_path = data_dir / "model_input_bundle" / "au_table.csv"

    inputs = _read_required(input_path)
    output = _read_required(output_path)
    curves = _read_required(curves_path)
    au_table = _read_required(au_path)

    grade_columns = _grade_columns(curves)
    residue_columns = _residue_columns(curves)
    industrial_columns = _industrial_columns(curves)
    age_grid = _age_grid(age_max, age_step)
    numeric_columns = [
        "Yield",
        "Height",
        "DBHq",
        "TPH",
        "GrossYield",
        "CrownCover",
        *grade_columns,
        *residue_columns,
        *industrial_columns,
    ]

    feature_ids = sorted(inputs["feature_id"].astype(int).unique())
    output_features = set(output["feature_id"].astype(int).unique())
    missing_output = [feature_id for feature_id in feature_ids if feature_id not in output_features]
    if missing_output:
        raise ValueError(
            "04_output is missing managed AUs: "
            + ", ".join(str(value) for value in missing_output)
        )
    available_aus = set(curves["AU"].astype(int).unique())
    missing_aus = [feature_id for feature_id in feature_ids if feature_id not in available_aus]
    if missing_aus:
        raise ValueError(
            "BTC curves are missing managed AUs: "
            + ", ".join(str(value) for value in missing_aus)
        )

    managed_rows: list[pd.DataFrame] = []
    industrial_rows: list[pd.DataFrame] = []
    coverage_rows: list[dict[str, Any]] = []

    for feature_id in feature_ids:
        feature = _feature_row(inputs, feature_id)
        au_row = _au_row(au_table, feature_id)
        leading_species = _stratum_species(str(au_row["stratum_code"]))
        group = curves[curves["AU"].astype(int) == feature_id]
        if group.empty:
            raise ValueError(f"BTC curve group is empty for AU {feature_id}")
        frame, extrapolated = _interpolate_curve(group, age_grid, numeric_columns)
        frame.insert(0, "au_id", feature_id)
        frame.insert(1, "au_table_au_id", int(au_row["au_id"]))
        frame.insert(2, "managed_curve_id", int(au_row["managed_curve_id"]))
        frame.insert(3, "stratum_code", str(au_row["stratum_code"]))
        frame.insert(4, "si_level", str(au_row["si_level"]))
        frame.insert(5, "leading_species", leading_species)
        frame.insert(6, "age_extrapolated", extrapolated.astype(bool))
        frame = frame.rename(
            columns={
                "Yield": "volume",
                "Height": "height",
                "DBHq": "dbhq",
                "TPH": "tph",
                "GrossYield": "gross_yield",
                "CrownCover": "crown_cover",
            }
        )
        frame["grade_total_m3ha"] = frame[grade_columns].sum(axis=1)
        frame["grade_coverage_fraction"] = np.where(
            frame["volume"] > 0,
            frame["grade_total_m3ha"] / frame["volume"],
            np.nan,
        )
        frame["residual_fibre_odt_ha"] = frame[residue_columns].sum(axis=1)
        frame["residue_odt_per_m3"] = np.where(
            frame["volume"] > 0,
            frame["residual_fibre_odt_ha"] / frame["volume"],
            np.nan,
        )
        frame["source"] = "btc_tsr"
        managed_rows.append(frame)

        if industrial_columns:
            industrial = frame[["au_id", "age", *industrial_columns, "age_extrapolated"]].copy()
            industrial["industrial_logs_total_m3ha"] = industrial[industrial_columns].sum(axis=1)
            industrial_rows.append(industrial)

        coverage_rows.append(
            _coverage_row(
                frame,
                feature_id=feature_id,
                au_row=au_row,
                feature=feature,
                leading_species=leading_species,
                site_index=_site_index(feature, leading_species),
                density=_planted_density(feature),
            )
        )

    managed = pd.concat(managed_rows, ignore_index=True)
    coverage = pd.DataFrame(coverage_rows)
    if industrial_rows:
        industrial = pd.concat(industrial_rows, ignore_index=True)
    else:
        industrial = pd.DataFrame(
            columns=["au_id", "age", *INDUSTRIAL_NAMES, "industrial_logs_total_m3ha", "age_extrapolated"]
        )
    species = _species_curves(managed)

    out_dir.mkdir(parents=True, exist_ok=True)
    managed_path = out_dir / "managed_au_curves.csv"
    industrial_path = out_dir / "managed_au_industrial_sorts.csv"
    coverage_path = out_dir / "au_grade_coverage.csv"
    species_path = out_dir / "species_grade_curves.csv"
    managed.to_csv(managed_path, index=False, float_format="%.6f", lineterminator="\n")
    industrial.to_csv(industrial_path, index=False, float_format="%.6f", lineterminator="\n")
    coverage.to_csv(coverage_path, index=False, float_format="%.6f", lineterminator="\n")
    species.to_csv(species_path, index=False, float_format="%.6f", lineterminator="\n")

    btc_manifest_candidates = [
        femic_root / "tipsy_io" / "logs" / "btc_manifest-tsa24_grades_tsa24.json",
        femic_root / "vdyp_io" / "profile_logs" / "btc_manifest-tsa24_grades_tsa24.json",
        femic_root / "tipsy_io" / "logs" / "btc_manifest-tsa24_grades.json",
    ]
    btc_manifest = next(
        (path for path in btc_manifest_candidates if path.exists()),
        None,
    )
    input_records = [
        {
            "label": label,
            "path": logical_path,
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }
        for label, path, logical_path in (
            ("03_input", input_path, "femic-tsa24-instance/data/03_input-tsa24.csv"),
            ("04_output", output_path, "femic-tsa24-instance/data/04_output-tsa24.csv"),
            ("tipsy_curves", curves_path, "femic-tsa24-instance/data/tipsy_curves_tsa24.csv"),
            (
                "au_table",
                au_path,
                "femic-tsa24-instance/data/model_input_bundle/au_table.csv",
            ),
        )
    ]
    if btc_manifest is not None:
        input_records.append(
            {
                "label": "btc_manifest",
                "path": str(btc_manifest.relative_to(femic_root)),
                "bytes": btc_manifest.stat().st_size,
                "sha256": _sha256(btc_manifest),
            }
        )

    manifest = {
        "created_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "status": "compiled_from_btc_tsr",
        "supersedes": {
            "status": "compiled_from_tipsy_taf",
            "note": (
                "Replaces the previous nominal-site-index TAF-derived grade table "
                "with AU-specific BTC /TSR output from the refreshed TSA24 run."
            ),
        },
        "provenance": {
            "source_model": "TIPSY 4.7 BTC /TSR via FEMIC",
            "database_title": "Ministry Standard Database, February 2022",
            "indicator_banks": ["log-grades", "residual-fibre"],
            "evidence_tier": "model_output",
            "grade_status": "model_output_not_field_grade",
            "btc_run_id": "tsa24_grades",
        },
        "inputs": input_records,
        "outputs": [
            {
                "label": label,
                "path": path.name,
                "rows": len(frame),
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
            for label, path, frame in (
                ("managed_au_curves", managed_path, managed),
                ("managed_au_industrial_sorts", industrial_path, industrial),
                ("au_grade_coverage", coverage_path, coverage),
                ("species_grade_curves", species_path, species),
            )
        ],
        "coverage": {
            "au_count": int(managed["au_id"].nunique()),
            "age_count": int(managed["age"].nunique()),
            "rows": len(managed),
            "leading_species": sorted(
                str(value) for value in managed["leading_species"].dropna().unique()
            ),
            "zero_grade_columns_by_au": {
                str(row.au_id): row.zero_grade_columns
                for row in coverage.itertuples(index=False)
                if row.zero_grade_columns
            },
            "industrial_columns_present": bool(industrial_columns),
            "dbhq_available": bool(managed["dbhq"].notna().any()),
            "tph_available": bool(managed["tph"].notna().any()),
        },
        "transformations": {
            "grade_volumes": (
                "Direct copy of BTC Logs_Grade_* columns at the AU age grid; no "
                "nominal-site-index substitution or share rescaling."
            ),
            "residual_fibre": (
                "Direct copy of BTC Residual_* columns; summed to "
                "residual_fibre_odt_ha. TIPSY residuals are mill-processing "
                "residues, not harvest slash."
            ),
            "age_grid": (
                f"Linear interpolation onto {int(age_grid.min())}-"
                f"{int(age_grid.max())} by {int(age_grid[1] - age_grid[0])} years; "
                "ages beyond the BTC curve are held flat and flagged."
            ),
        },
        "limitations": [
            "Logs_Grade_D and Logs_Grade_F are zero for every TSA24 AU in this run.",
            "DBHq and TPH are absent because the stand-structure bank was not requested.",
            "Industrial log columns are absent because the industrial-logs bank was not requested.",
            "Residual fibre is mill processing residue, not harvest residue.",
            "Grade status is model output, not official BC log grade.",
        ],
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest
