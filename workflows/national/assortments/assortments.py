"""Assortment matrix construction and residue / AGB ledger.

The matrix is a reporting aggregation over ``BuckedLog`` rows and
``ResidueLedger`` rows from the existing BC interior bucking layer.  It does
not assign official BC grades: every row carries ``grade_status`` from the
rule set and an evidence tier from the taper adapter.

Volume conservation is explicit: for every species, recovered log volume plus
residue volume equals the taper total.  The grand-total row is the sum of the
two streams, not a separately modelled volume.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

from workflows.national.bucking.validate_tsa24_btc import (
    load_basic_density_table,
    resolve_species_density,
)

__all__ = [
    "AssortmentError",
    "DEFAULT_CHIP_YIELD",
    "DEFAULT_MOISTURE_WET_BASIS",
    "build_assortment_matrix",
    "conservation_summary",
]

DEFAULT_MOISTURE_WET_BASIS = 0.45
DEFAULT_CHIP_YIELD = 0.90
DEFAULT_DENSITY_PATH = (
    Path(__file__).resolve().parents[1] / "bucking" / "rules" / "bc_basic_density.csv"
)

_REQUIRED_LOG_COLUMNS = {
    "tree_id",
    "species",
    "length_m",
    "large_end_dib_cm",
    "small_end_dib_cm",
    "volume_m3",
    "sort_code",
    "product",
    "evidence",
    "grade_status",
}
_REQUIRED_RESIDUE_COLUMNS = {
    "tree_id",
    "species",
    "residue_volume_m3",
    "recovered_log_m3",
    "taper_total_m3",
}
_REQUIRED_TREE_COLUMNS = {"tree_id", "species", "expansion_factor"}


class AssortmentError(ValueError):
    """Raised when the assortment aggregation would hide a volume error."""


def _top_class(value: float) -> str:
    if value < 12.5:
        return "lt12.5"
    if value < 20.0:
        return "12.5-19.9"
    if value < 30.0:
        return "20-29.9"
    if value < 38.0:
        return "30-37.9"
    return "38+"


def _length_class(value: float) -> str:
    if value < 5.1:
        return "2.4-5.0"
    if value < 6.3:
        return "5.1-6.2"
    if value < 8.0:
        return "6.3-7.9"
    if value < 11.0:
        return "8.0-10.9"
    if value < 13.0:
        return "11.0-12.9"
    return "13.0+"


def _require_columns(frame: pd.DataFrame, required: set[str], label: str) -> None:
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise AssortmentError(f"{label} missing columns: {', '.join(missing)}")


def _ledger_by_species(
    logs: pd.DataFrame,
    residue: pd.DataFrame,
    trees: pd.DataFrame,
    *,
    area_ha: float,
    density_table: Mapping[str, Mapping[str, Any]],
    moisture_wet_basis: float,
    chip_yield_fraction: float,
    btc_agb_odt_ha: float | None,
) -> dict[str, dict[str, Any]]:
    expansion = trees.set_index("tree_id")["expansion_factor"].astype(float)
    if expansion.isna().any() or (expansion <= 0.0).any():
        raise AssortmentError("tree expansion factors must be finite and positive")
    if not expansion.index.is_unique:
        raise AssortmentError("tree_id values must be unique in the tree list")

    res = residue.copy()
    res["expansion_factor"] = res["tree_id"].map(expansion)
    if res["expansion_factor"].isna().any():
        missing = res.loc[res["expansion_factor"].isna(), "tree_id"].head(5).tolist()
        raise AssortmentError(f"residue rows without matching trees: {missing}")
    for column in ("residue_volume_m3", "recovered_log_m3", "taper_total_m3"):
        res[column] = pd.to_numeric(res[column], errors="coerce") * res["expansion_factor"]
    res = res.dropna(subset=["species"])

    log_totals = (
        logs.assign(
            volume_m3=pd.to_numeric(logs["volume_m3"], errors="coerce")
            * logs["tree_id"].map(expansion)
        )
        .groupby("species", dropna=False)["volume_m3"]
        .sum()
    )

    species_totals = res.groupby("species", dropna=False).agg(
        taper_total_m3=("taper_total_m3", "sum"),
        recovered_ledger_m3=("recovered_log_m3", "sum"),
        residue_volume_m3=("residue_volume_m3", "sum"),
    )
    total_taper = float(species_totals["taper_total_m3"].sum())
    if total_taper <= 0.0:
        raise AssortmentError("species ledger has non-positive taper total")

    ledger: dict[str, dict[str, Any]] = {}
    for species, row in species_totals.iterrows():
        code = str(species).strip().upper()
        density = resolve_species_density(code, density_table)
        recovered = float(log_totals.get(code, 0.0))
        ledger_mismatch = abs(recovered - float(row["recovered_ledger_m3"]))
        tolerance = max(1e-7, 1e-8 * total_taper)
        if ledger_mismatch > tolerance:
            raise AssortmentError(
                f"{code}: recovered logs {recovered:.9f} m3 do not match residue "
                f"ledger {row['recovered_ledger_m3']:.9f} m3 (error {ledger_mismatch:.3g})"
            )
        taper = float(row["taper_total_m3"])
        residue_m3 = float(row["residue_volume_m3"])
        residue_odt = residue_m3 * density
        green = residue_odt / (1.0 - moisture_wet_basis)
        ledger[code] = {
            "density_odt_m3": density,
            "density_source": density_table[code]["source"],
            "density_evidence": density_table[code]["evidence"],
            "taper_total_m3": taper,
            "recovered_log_m3": recovered,
            "residue_m3": residue_m3,
            "recovered_log_odt": recovered * density,
            "residue_odt": residue_odt,
            "residue_green_tonnes": green,
            "residue_chip_green_tonnes": green * chip_yield_fraction,
            "agb_wood_odt": taper * density,
            "species_share": taper / total_taper,
            "target_agb_odt": (
                float(btc_agb_odt_ha) * area_ha * (taper / total_taper)
                if btc_agb_odt_ha is not None
                else math.nan
            ),
        }
        ledger[code]["wood_vs_total_agb_gap_odt"] = (
            ledger[code]["agb_wood_odt"] - ledger[code]["target_agb_odt"]
            if btc_agb_odt_ha is not None
            else math.nan
        )
    return ledger


def build_assortment_matrix(
    logs: pd.DataFrame,
    residue: pd.DataFrame,
    trees: pd.DataFrame,
    *,
    area_ha: float = 1.0,
    density_path: Path | None = None,
    moisture_wet_basis: float = DEFAULT_MOISTURE_WET_BASIS,
    chip_yield_fraction: float = DEFAULT_CHIP_YIELD,
    btc_agb_odt_ha: float | None = None,
) -> pd.DataFrame:
    """Aggregate logs and residue into a species x sort x dimension matrix.

    The returned frame has three record types:

    ``log``
        One row per species/product/sort/top-class/length-class.  ``volume_m3``
        is recovered log volume, scaled by expansion factor.
    ``residue``
        One row per species.  ``volume_m3`` is residue volume; ledger columns
        carry ODT, green tonnes, chip tonnes, taper total, and wood AGB proxy.
    ``total``
        One stand-level row.  ``volume_m3`` is recovered + residue = taper total.
    """
    if area_ha <= 0.0 or not math.isfinite(area_ha):
        raise AssortmentError("area_ha must be finite and positive")
    if not 0.0 <= moisture_wet_basis < 1.0:
        raise AssortmentError("moisture_wet_basis must be in [0, 1)")
    if not 0.0 <= chip_yield_fraction <= 1.0:
        raise AssortmentError("chip_yield_fraction must be in [0, 1]")

    logs = logs.copy()
    residue = residue.copy()
    trees = trees.copy()
    _require_columns(logs, _REQUIRED_LOG_COLUMNS, "logs")
    _require_columns(residue, _REQUIRED_RESIDUE_COLUMNS, "residue")
    _require_columns(trees, _REQUIRED_TREE_COLUMNS, "trees")

    density_table = load_basic_density_table(density_path or DEFAULT_DENSITY_PATH)
    expansion = trees.set_index("tree_id")["expansion_factor"].astype(float)
    ledger = _ledger_by_species(
        logs,
        residue,
        trees,
        area_ha=area_ha,
        density_table=density_table,
        moisture_wet_basis=moisture_wet_basis,
        chip_yield_fraction=chip_yield_fraction,
        btc_agb_odt_ha=btc_agb_odt_ha,
    )

    logs["expansion_factor"] = logs["tree_id"].map(expansion)
    if logs["expansion_factor"].isna().any():
        missing = logs.loc[logs["expansion_factor"].isna(), "tree_id"].head(5).tolist()
        raise AssortmentError(f"log rows without matching trees: {missing}")
    logs["species"] = logs["species"].astype(str).str.upper()
    for column in ("length_m", "large_end_dib_cm", "small_end_dib_cm", "volume_m3"):
        logs[column] = pd.to_numeric(logs[column], errors="coerce")
    logs["volume_m3"] = logs["volume_m3"] * logs["expansion_factor"]
    logs["value_cad"] = pd.to_numeric(logs["value_cad"], errors="coerce") * logs[
        "expansion_factor"
    ]
    logs["top_class_cm"] = logs["small_end_dib_cm"].map(_top_class)
    logs["length_class_m"] = logs["length_m"].map(_length_class)
    logs["record_type"] = "log"
    logs["product"] = logs["product"].astype(str).str.lower()
    logs["sort_code"] = logs["sort_code"].astype(str).str.upper()

    group_columns = [
        "species",
        "product",
        "sort_code",
        "top_class_cm",
        "length_class_m",
    ]
    aggregated = (
        logs.groupby(group_columns, dropna=False)
        .agg(
            n_logs=("tree_id", "size"),
            volume_m3=("volume_m3", "sum"),
            value_cad=("value_cad", "sum"),
            mean_length_m=("length_m", "mean"),
            mean_top_cm=("small_end_dib_cm", "mean"),
            price_cad_m3=("price_cad_m3", "mean"),
            evidence=("evidence", "first"),
            grade_status=("grade_status", "first"),
        )
        .reset_index()
    )
    aggregated["record_type"] = "log"
    aggregated["top_class_cm"] = aggregated["top_class_cm"].astype(str)
    aggregated["length_class_m"] = aggregated["length_class_m"].astype(str)

    residue_rows: list[dict[str, Any]] = []
    for species, values in sorted(ledger.items()):
        residue_rows.append(
            {
                "species": species,
                "product": "residue",
                "sort_code": "RESIDUE",
                "top_class_cm": "na",
                "length_class_m": "na",
                "n_logs": 0,
                "volume_m3": values["residue_m3"],
                "value_cad": 0.0,
                "mean_length_m": math.nan,
                "mean_top_cm": math.nan,
                "price_cad_m3": math.nan,
                "evidence": "assumption",
                "grade_status": "not_assigned",
                "record_type": "residue",
                "density_odt_m3": values["density_odt_m3"],
                "density_source": values["density_source"],
                "density_evidence": values["density_evidence"],
                "taper_total_m3": values["taper_total_m3"],
                "recovered_log_m3": values["recovered_log_m3"],
                "recovered_log_odt": values["recovered_log_odt"],
                "residue_m3": values["residue_m3"],
                "residue_odt": values["residue_odt"],
                "residue_green_tonnes": values["residue_green_tonnes"],
                "residue_chip_green_tonnes": values["residue_chip_green_tonnes"],
                "agb_wood_odt": values["agb_wood_odt"],
                "target_agb_odt": values["target_agb_odt"],
                "wood_vs_total_agb_gap_odt": values["wood_vs_total_agb_gap_odt"],
                "species_share": values["species_share"],
                "volume_share_of_species": 1.0,
                "volume_share_of_stand": values["residue_m3"]
                / max(values["taper_total_m3"], 1e-12),
            }
        )
    residue_frame = pd.DataFrame(residue_rows)

    ledger_columns = [
        "density_odt_m3",
        "density_source",
        "density_evidence",
        "taper_total_m3",
        "recovered_log_m3",
        "recovered_log_odt",
        "residue_m3",
        "residue_odt",
        "residue_green_tonnes",
        "residue_chip_green_tonnes",
        "agb_wood_odt",
        "target_agb_odt",
        "wood_vs_total_agb_gap_odt",
        "species_share",
    ]
    for column in ledger_columns:
        aggregated[column] = aggregated["species"].map(
            {species: values.get(column) for species, values in ledger.items()}
        )
    aggregated["volume_share_of_species"] = aggregated.apply(
        lambda row: row["volume_m3"]
        / max(float(ledger[str(row["species"])]["recovered_log_m3"]), 1e-12),
        axis=1,
    )
    aggregated["volume_share_of_stand"] = aggregated["volume_m3"] / max(
        float(sum(values["taper_total_m3"] for values in ledger.values())), 1e-12
    )

    matrix = pd.concat([aggregated, residue_frame], ignore_index=True, sort=False)
    matrix["is_total"] = False

    total_taper = float(sum(values["taper_total_m3"] for values in ledger.values()))
    total_recovered = float(sum(values["recovered_log_m3"] for values in ledger.values()))
    total_residue = float(sum(values["residue_m3"] for values in ledger.values()))
    total_values = {
        "density_odt_m3": math.nan,
        "density_source": "mixed",
        "density_evidence": "mixed",
        "taper_total_m3": total_taper,
        "recovered_log_m3": total_recovered,
        "recovered_log_odt": float(sum(v["recovered_log_odt"] for v in ledger.values())),
        "residue_m3": total_residue,
        "residue_odt": float(sum(v["residue_odt"] for v in ledger.values())),
        "residue_green_tonnes": float(
            sum(v["residue_green_tonnes"] for v in ledger.values())
        ),
        "residue_chip_green_tonnes": float(
            sum(v["residue_chip_green_tonnes"] for v in ledger.values())
        ),
        "agb_wood_odt": float(sum(v["agb_wood_odt"] for v in ledger.values())),
        "target_agb_odt": float(sum(v["target_agb_odt"] for v in ledger.values())),
        "wood_vs_total_agb_gap_odt": float(
            sum(v["wood_vs_total_agb_gap_odt"] for v in ledger.values())
        ),
        "species_share": 1.0,
        "volume_share_of_species": math.nan,
        "volume_share_of_stand": 1.0,
    }
    total_row = {
        "species": "ALL",
        "product": "all",
        "sort_code": "ALL",
        "top_class_cm": "na",
        "length_class_m": "na",
        "n_logs": int(aggregated["n_logs"].sum()),
        "volume_m3": total_recovered + total_residue,
        "value_cad": float(aggregated["value_cad"].sum()),
        "mean_length_m": math.nan,
        "mean_top_cm": math.nan,
        "price_cad_m3": math.nan,
        "evidence": "mixed",
        "grade_status": "not_assigned",
        "record_type": "total",
        "is_total": True,
        **total_values,
    }
    matrix = pd.concat([matrix, pd.DataFrame([total_row])], ignore_index=True, sort=False)
    matrix["is_total"] = matrix["is_total"].fillna(False).astype(bool)

    for column in (
        "n_logs",
        "volume_m3",
        "value_cad",
        "taper_total_m3",
        "recovered_log_m3",
        "residue_m3",
        "residue_odt",
        "residue_green_tonnes",
        "residue_chip_green_tonnes",
        "agb_wood_odt",
        "target_agb_odt",
        "density_odt_m3",
    ):
        if column in matrix.columns:
            numeric = pd.to_numeric(matrix[column], errors="coerce")
            if (numeric.dropna() < 0.0).any():
                raise AssortmentError(f"negative values in assortment column {column!r}")

    record_order = {"log": 0, "residue": 1, "total": 2}
    matrix["_record_order"] = matrix["record_type"].map(record_order).fillna(9).astype(int)
    matrix = matrix.sort_values(
        [
            "_record_order",
            "species",
            "product",
            "sort_code",
            "top_class_cm",
            "length_class_m",
        ],
        kind="stable",
    ).drop(columns="_record_order")
    matrix = matrix.reset_index(drop=True)
    matrix["area_ha"] = float(area_ha)
    matrix["volume_m3ha"] = matrix["volume_m3"] / float(area_ha)
    return matrix


def conservation_summary(matrix: pd.DataFrame) -> dict[str, Any]:
    """Check recovered + residue = taper total and report the ledger."""
    required = {"record_type", "volume_m3", "taper_total_m3", "residue_m3"}
    missing = sorted(required.difference(matrix.columns))
    if missing:
        raise AssortmentError(f"matrix missing columns: {', '.join(missing)}")
    logs = float(matrix.loc[matrix["record_type"].eq("log"), "volume_m3"].sum())
    residue = float(matrix.loc[matrix["record_type"].eq("residue"), "volume_m3"].sum())
    taper = float(matrix.loc[matrix["record_type"].eq("residue"), "taper_total_m3"].sum())
    total_row = float(matrix.loc[matrix["record_type"].eq("total"), "volume_m3"].sum())
    balance = logs + residue
    absolute_error = abs(balance - taper)
    tolerance = max(1e-7, 1e-8 * abs(taper))
    total_error = abs(total_row - balance)
    return {
        "recovered_log_m3": logs,
        "residue_m3": residue,
        "recovered_plus_residue_m3": balance,
        "taper_total_m3": taper,
        "absolute_error_m3": absolute_error,
        "relative_error": absolute_error / max(abs(taper), 1e-12),
        "tolerance_m3": tolerance,
        "pass": bool(absolute_error <= tolerance),
        "total_row_m3": total_row,
        "total_row_error_m3": total_error,
        "total_row_pass": bool(total_error <= tolerance),
        "residue_share_of_taper": residue / max(taper, 1e-12),
        "recovered_share_of_taper": logs / max(taper, 1e-12),
        "residue_odt": float(matrix.loc[matrix["record_type"].eq("residue"), "residue_odt"].sum()),
        "agb_wood_odt": float(
            matrix.loc[matrix["record_type"].eq("residue"), "agb_wood_odt"].sum()
        ),
        "target_agb_odt": float(
            matrix.loc[matrix["record_type"].eq("residue"), "target_agb_odt"].sum()
        ),
    }
