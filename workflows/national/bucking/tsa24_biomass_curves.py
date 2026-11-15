"""Compile TSA24 managed-AU live-biomass curves from a FEMIC BTC run.

The documented AGB path is::

    python -m femic tipsy run-btc data/03_input-tsa24.csv \
        --indicator-bank biomass-live --indicator-bank carbon \
        --run-id tsa24_biomass --output-csv data/04_output-tsa24_biomass.csv

BTC returns the live biomass family in oven-dry tonnes per hectare.  The
TIPSY reference help (``Biomass_and_carbon_data.htm``) states that the biomass
and carbon families are reported in oven-dry units (tonnes/ha), and the
``fresh-tipsy`` metric contract names the same family ``biomass_live_odt_ha``.
``Biomass_Live_Above`` is stem wood + bark + foliage + branch; ``Roots`` and
``Total`` are reported separately.  This module copies those values, validates
the component additivity, and writes a narrow AU-age table.  It never
synthesises a biomass equation.
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

__all__ = [
    "BIOMASS_COMPONENTS",
    "DEFAULT_BIOMASS_OUTPUT",
    "compile_biomass_curves_from_btc",
]

DEFAULT_BIOMASS_OUTPUT = "04_output-tsa24_biomass.csv"
DEFAULT_AGE_MAX = 350
DEFAULT_AGE_STEP = 10
COMPONENT_TOLERANCE = 0.25  # ODT/ha; BTC CSV reports one decimal place

BIOMASS_COMPONENTS: Mapping[str, str] = {
    "wood": "Biomass_Live_Wood",
    "bark": "Biomass_Live_Bark",
    "foliar": "Biomass_Live_Foliar",
    "branch": "Biomass_Live_Branch",
    "roots": "Biomass_Live_Roots",
    "total": "Biomass_Live_Total",
    "above": "Biomass_Live_Above",
}
CARBON_COMPONENTS: Mapping[str, str] = {
    "carbon_wood": "Carbon_Live_Wood",
    "carbon_bark": "Carbon_Live_Bark",
    "carbon_foliar": "Carbon_Live_Foliar",
    "carbon_branch": "Carbon_Live_Branch",
    "carbon_roots": "Carbon_Live_Roots",
    "carbon_total": "Carbon_Live_Total",
    "carbon_above": "Carbon_Live_Above",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_required(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"TSA24 biomass artifact not found: {path}")
    return pd.read_csv(path)


def _age_suffixes(columns: Sequence[str], prefix: str) -> list[int]:
    """Return the age columns for one biomass family, sorted numerically."""
    suffix = f"{prefix}_"
    ages = []
    for column in columns:
        if not column.startswith(suffix):
            continue
        candidate = column[len(suffix) :]
        if candidate.isdigit():
            ages.append(int(candidate))
    if not ages:
        raise ValueError(f"no {prefix}_<age> columns in the BTC biomass output")
    return sorted(ages)


def _component_value(row: pd.Series, prefix: str, age: int) -> float:
    column = f"{prefix}_{age}"
    value = pd.to_numeric(row.get(column), errors="coerce")
    if pd.isna(value) or not np.isfinite(float(value)):
        raise ValueError(f"non-finite BTC biomass value in {column}")
    return float(value)


def _au_row(au_table: pd.DataFrame, feature_id: int) -> pd.Series:
    managed = pd.to_numeric(au_table["source_managed_local_au_id"], errors="coerce")
    local = pd.to_numeric(au_table["source_local_au_id"], errors="coerce")
    matches = au_table[(managed == feature_id) | (local == feature_id)]
    if matches.empty:
        raise ValueError(f"feature_id {feature_id} not found in au_table")
    managed_matches = matches[
        pd.to_numeric(matches["source_managed_local_au_id"], errors="coerce")
        == feature_id
    ]
    return (managed_matches if not managed_matches.empty else matches).iloc[0]


def _leading_species(stratum_code: str) -> str:
    _, _, species_part = str(stratum_code).partition("_")
    tokens = [token.strip() for token in species_part.split("+") if token.strip()]
    if not tokens:
        raise ValueError(f"AU stratum code has no species token: {stratum_code!r}")
    return tokens[0]


def _validate_additivity(
    *,
    feature_id: int,
    age: int,
    above: float,
    total: float,
    components: Mapping[str, float],
) -> None:
    component_sum = (
        components["wood"]
        + components["bark"]
        + components["foliar"]
        + components["branch"]
    )
    tolerance = COMPONENT_TOLERANCE + 1e-6 * max(abs(above), 1.0)
    if abs(component_sum - above) > tolerance:
        raise ValueError(
            f"AU {feature_id} age {age}: Biomass_Live_Above={above} does not equal "
            f"wood+bark+foliar+branch={component_sum}"
        )
    total_sum = above + components["roots"]
    if abs(total_sum - total) > tolerance:
        raise ValueError(
            f"AU {feature_id} age {age}: Biomass_Live_Total={total} does not equal "
            f"Above+Roots={total_sum}"
        )


def _long_rows(
    output: pd.DataFrame,
    inputs: pd.DataFrame,
    au_table: pd.DataFrame,
    *,
    age_max: int,
    age_step: int,
) -> pd.DataFrame:
    ages = [
        age
        for age in _age_suffixes(list(output.columns), "Biomass_Live_Above")
        if age <= age_max and age % age_step == 0
    ]
    if not ages:
        raise ValueError("no biomass age columns on the requested age grid")

    feature_ids = sorted(inputs["feature_id"].astype(int).unique())
    output_features = set(output["feature_id"].astype(int).unique())
    missing = [feature_id for feature_id in feature_ids if feature_id not in output_features]
    if missing:
        raise ValueError(
            "04_output biomass file is missing managed AUs: "
            + ", ".join(str(value) for value in missing)
        )

    rows: list[dict[str, Any]] = []
    for feature_id in feature_ids:
        au = _au_row(au_table, feature_id)
        stratum = str(au["stratum_code"])
        leading = _leading_species(stratum)
        feature_rows = output[output["feature_id"].astype(int) == feature_id]
        if len(feature_rows) != 1:
            raise ValueError(
                f"expected one BTC biomass row for AU {feature_id}, found {len(feature_rows)}"
            )
        row = feature_rows.iloc[0]
        for age in ages:
            components = {
                key: _component_value(row, prefix, age)
                for key, prefix in BIOMASS_COMPONENTS.items()
            }
            _validate_additivity(
                feature_id=feature_id,
                age=age,
                above=components["above"],
                total=components["total"],
                components=components,
            )
            record: dict[str, Any] = {
                "au_id": feature_id,
                "stratum_code": stratum,
                "si_level": str(au["si_level"]),
                "leading_species": leading,
                "age": int(age),
                "agb_odt_ha": components["above"],
                "biomass_live_wood_odt_ha": components["wood"],
                "biomass_live_bark_odt_ha": components["bark"],
                "biomass_live_foliar_odt_ha": components["foliar"],
                "biomass_live_branch_odt_ha": components["branch"],
                "biomass_live_roots_odt_ha": components["roots"],
                "biomass_live_total_odt_ha": components["total"],
            }
            for key, prefix in CARBON_COMPONENTS.items():
                record[f"{key}_t_ha"] = _component_value(row, prefix, age)
            record["source"] = "femic_btc_tsr_biomass_live"
            rows.append(record)

    frame = pd.DataFrame(rows).sort_values(["au_id", "age"]).reset_index(drop=True)
    if frame["agb_odt_ha"].le(0).all():
        raise ValueError("all compiled AGB values are non-positive")
    return frame


def compile_biomass_curves_from_btc(
    *,
    femic_root: Path,
    out_dir: Path,
    output_name: str = DEFAULT_BIOMASS_OUTPUT,
    age_max: int = DEFAULT_AGE_MAX,
    age_step: int = DEFAULT_AGE_STEP,
) -> dict[str, Any]:
    """Write ``biomass_au_curves.csv`` and its provenance manifest.

    Parameters
    ----------
    femic_root : Path
        FEMIC TSA24 instance root.
    out_dir : Path
        Destination directory for the compiled table and manifest.
    output_name : str, optional
        Biomass BTC output filename under ``<femic_root>/data``.
    age_max, age_step : int, optional
        Standard age grid; the BTC output is stored at 10-year steps from 0 to
        350 years.
    """
    femic_root = Path(femic_root)
    out_dir = Path(out_dir)
    data_dir = femic_root / "data"
    output_path = data_dir / output_name
    input_path = data_dir / "03_input-tsa24.csv"
    au_path = data_dir / "model_input_bundle" / "au_table.csv"

    output = _read_required(output_path)
    inputs = _read_required(input_path)
    au_table = _read_required(au_path)
    frame = _long_rows(
        output,
        inputs,
        au_table,
        age_max=age_max,
        age_step=age_step,
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    table_path = out_dir / "biomass_au_curves.csv"
    frame.to_csv(table_path, index=False, float_format="%.6f", lineterminator="\n")

    manifest = {
        "created_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "status": "compiled_from_femic_btc_biomass_live",
        "provenance": {
            "source_model": "TIPSY 4.7 BTC /TSR via FEMIC",
            "indicator_banks": ["biomass-live", "carbon"],
            "evidence_tier": "model_output",
            "btc_run_id": "tsa24_biomass",
            "units": {
                "agb_odt_ha": "ODT/ha",
                "biomass_live_*_odt_ha": "ODT/ha",
                "carbon_*_t_ha": "t C/ha",
            },
            "agb_definition": (
                "Biomass_Live_Above = stem wood + bark + foliage + branch; "
                "roots and total are reported separately."
            ),
        },
        "inputs": [
            {
                "label": "03_input",
                "path": "femic-tsa24-instance/data/03_input-tsa24.csv",
                "bytes": input_path.stat().st_size,
                "sha256": _sha256(input_path),
            },
            {
                "label": "biomass_output",
                "path": f"femic-tsa24-instance/data/{output_name}",
                "bytes": output_path.stat().st_size,
                "sha256": _sha256(output_path),
            },
            {
                "label": "au_table",
                "path": "femic-tsa24-instance/data/model_input_bundle/au_table.csv",
                "bytes": au_path.stat().st_size,
                "sha256": _sha256(au_path),
            },
        ],
        "outputs": [
            {
                "label": "biomass_au_curves",
                "path": table_path.name,
                "rows": len(frame),
                "bytes": table_path.stat().st_size,
                "sha256": _sha256(table_path),
            }
        ],
        "coverage": {
            "au_count": int(frame["au_id"].nunique()),
            "age_count": int(frame["age"].nunique()),
            "rows": len(frame),
            "age_min": int(frame["age"].min()),
            "age_max": int(frame["age"].max()),
            "age_step": int(age_step),
            "leading_species": sorted(
                str(value) for value in frame["leading_species"].dropna().unique()
            ),
            "agb_min_odt_ha": float(frame["agb_odt_ha"].min()),
            "agb_max_odt_ha": float(frame["agb_odt_ha"].max()),
        },
        "transformations": {
            "wide_to_long": (
                "One row per managed AU and BTC age column; Biomass_Live_Above is "
                "used as AGB. No interpolation, rescaling, or allometric synthesis."
            ),
            "additivity_checks": (
                "Above == wood+bark+foliar+branch and Total == Above+roots; the "
                "compile fails loudly if either identity breaks."
            ),
            "age_grid": (
                f"{int(frame['age'].min())}-{int(frame['age'].max())} by "
                f"{int(age_step)} years, matching the compiled grade/residue table."
            ),
        },
        "limitations": [
            "AGB is a stand-level BTC model output at the managed AU site index, not a plot measurement.",
            "Biomass_Live_Above excludes roots; do not add Roots when comparing to harvest slash.",
            "The biomass run did not include the grade or volume banks; volume is taken from managed_au_curves.csv.",
        ],
    }
    (out_dir / "biomass_curves_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest
