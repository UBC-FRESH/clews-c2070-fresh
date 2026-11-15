"""RSYC ecological-stratum and tile mapping for plot observations.

The archived RSYC package supplies model parameters only; the GeoPackages
supply the boundaries.  This module joins plot coordinates to the nested
ecozone/ecoprovince/ecoregion/ecodistrict hierarchy and to the 150 km tile
grid, and evaluates the published volume curves.  It does not refit RSYC.
"""

from __future__ import annotations

import math
import zipfile
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np
import pandas as pd

__all__ = [
    "assign_sites_to_ecostrata",
    "evaluate_curve",
    "load_rsyc_volume_models",
    "rsyc_curve_table",
    "rsyc_species_code",
]

RSYC_SPECIES = {
    "SW": "PICE.GLA",
    "SE": "PICE.ENG",
    "SB": "PICE.MAR",
    "S": "PICE.GLA",
    "PL": "PINU.CON",
    "BL": "ABIE.BAL",
    "FD": "PSEU.MEN",
    "AT": "POPU.TRE",
    "EP": "POPU.BAL",
    "HW": "TSUG.HET",
    "CW": "THUJ.PLI",
}

_ECO_COLUMNS = ("ecozone", "ecoprovince", "ecoregion", "ecodistrict")


def load_rsyc_volume_models(path: Path) -> pd.DataFrame:
    """Load published, national-scale RSYC volume models.

    The catalogue contains several scales and both AGB and volume responses.
    Only national-scale, publishable volume fits are returned.  Duplicate
    keys are resolved by the largest ``n_obs`` so the most supported curve is
    used.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"RSYC model archive not found: {path}")
    with zipfile.ZipFile(path) as archive:
        with archive.open("RSYC_models.csv") as handle:
            models = pd.read_csv(handle, low_memory=False)

    required = {
        "model_id",
        "response",
        "strata_type",
        "strata_id",
        "species",
        "b1",
        "b2",
        "b3",
        "b4",
        "publish_ok",
        "fit_ok",
        "scale",
        "n_obs",
    }
    missing = sorted(required.difference(models.columns))
    if missing:
        raise ValueError(f"RSYC model catalogue missing columns: {', '.join(missing)}")

    models = models[models["response"].astype(str).str.lower().eq("volume")].copy()
    for flag in ("publish_ok", "fit_ok"):
        models = models[
            models[flag].astype(str).str.upper().isin({"TRUE", "1", "T"})
        ]
    models = models[models["scale"].astype(str).str.lower().eq("national")].copy()
    if models.empty:
        raise ValueError("no national-scale, publishable RSYC volume models found")
    models["n_obs"] = pd.to_numeric(models["n_obs"], errors="coerce").fillna(0)
    models = models.sort_values("n_obs", ascending=False).drop_duplicates(
        ["strata_type", "strata_id", "species"], keep="first"
    )
    for column in ("b1", "b2", "b3", "b4"):
        models[column] = pd.to_numeric(models[column], errors="coerce")
    return models.reset_index(drop=True)


def assign_sites_to_ecostrata(
    sites: pd.DataFrame,
    *,
    ecostrat_path: Path,
    tiles_path: Path,
    site_column: str = "site_id",
    latitude_column: str = "latitude",
    longitude_column: str = "longitude",
) -> pd.DataFrame:
    """Join sites to the RSYC ecodistrict hierarchy and tile grid."""
    import geopandas as gpd

    required = {site_column, latitude_column, longitude_column}
    missing = sorted(required.difference(sites.columns))
    if missing:
        raise ValueError(f"sites missing coordinate columns: {', '.join(missing)}")
    ecostrat_path = Path(ecostrat_path)
    tiles_path = Path(tiles_path)
    for path in (ecostrat_path, tiles_path):
        if not path.exists():
            raise FileNotFoundError(f"RSYC spatial layer not found: {path}")

    data = sites.copy()
    data[site_column] = data[site_column].astype(str)
    data[latitude_column] = pd.to_numeric(data[latitude_column], errors="coerce")
    data[longitude_column] = pd.to_numeric(data[longitude_column], errors="coerce")
    mapped_mask = data[[latitude_column, longitude_column]].notna().all(axis=1)
    mapped = data.loc[mapped_mask].copy()
    if mapped.empty:
        unmapped = data[[site_column]].copy()
        unmapped["mapped"] = False
        return unmapped

    points = gpd.GeoDataFrame(
        mapped,
        geometry=gpd.points_from_xy(mapped[longitude_column], mapped[latitude_column]),
        crs=4326,
    ).to_crs(3978)
    ecodistricts = gpd.read_file(ecostrat_path, layer="ecodistricts")
    tiles = gpd.read_file(tiles_path, layer="RSYC_tiles")
    eco_columns = [
        "join_key",
        "ecozone",
        "ecoprovince",
        "ecoregion",
        "ecodistrict",
        "ecodistrict_id",
        "geometry",
    ]
    missing_eco = [column for column in eco_columns if column not in ecodistricts.columns]
    if missing_eco:
        raise ValueError(
            f"ecodistrict layer missing columns: {', '.join(missing_eco)}"
        )
    joined = gpd.sjoin(
        points,
        ecodistricts[eco_columns],
        how="left",
        predicate="within",
    )
    joined = joined.drop(columns=["index_right"], errors="ignore")
    joined = joined.drop_duplicates(site_column, keep="first")
    joined = gpd.sjoin(
        joined,
        tiles[["TileID", "geometry"]],
        how="left",
        predicate="within",
    )
    joined = joined.drop(columns=["index_right"], errors="ignore")
    joined = joined.drop_duplicates(site_column, keep="first")
    joined["mapped"] = joined["join_key"].notna()

    result_columns = [
        site_column,
        "mapped",
        "join_key",
        "ecozone",
        "ecoprovince",
        "ecoregion",
        "ecodistrict",
        "ecodistrict_id",
        "TileID",
        latitude_column,
        longitude_column,
    ]
    result = pd.DataFrame(joined[result_columns]).rename(
        columns={"TileID": "tile_id"}
    )
    if len(result) < len(mapped):
        missing_sites = set(mapped[site_column]).difference(result[site_column])
        fallback = data[data[site_column].isin(missing_sites)][[site_column]].copy()
        fallback["mapped"] = False
        result = pd.concat([result, fallback], ignore_index=True, sort=False)
    return result.reset_index(drop=True)


def rsyc_species_code(species: str) -> str | None:
    return RSYC_SPECIES.get(str(species).strip().upper())


def evaluate_curve(model: pd.Series | dict[str, Any], age: float) -> float | None:
    """Evaluate the published RSYC Chapman-Richards curve."""
    try:
        b1 = float(model["b1"])
        b2 = float(model["b2"])
        b3 = float(model["b3"])
        b4 = float(model["b4"])
        age_value = float(age)
    except (KeyError, TypeError, ValueError):
        return None
    if age_value <= 0.0 or not all(
        math.isfinite(value) for value in (b1, b2, b3, b4)
    ):
        return None
    value = b1 * math.exp(-b4 * age_value) * (1.0 - math.exp(-b2 * age_value)) ** b3
    return float(value) if math.isfinite(value) else None


def rsyc_curve_table(
    models: pd.DataFrame,
    *,
    strata_type: str,
    strata_ids: Iterable[str],
    species: Sequence[str],
    ages: Iterable[float],
) -> pd.DataFrame:
    """Evaluate one or more RSYC strata/species combinations on an age grid."""
    rows: list[dict[str, Any]] = []
    ids = {str(value) for value in strata_ids}
    wanted_species = {str(value) for value in species}
    subset = models[
        models["strata_type"].astype(str).eq(strata_type)
        & models["strata_id"].astype(str).isin(ids)
        & models["species"].astype(str).isin(wanted_species)
    ]
    for model in subset.itertuples(index=False):
        for age in ages:
            value = evaluate_curve(model._asdict(), age)
            if value is None:
                continue
            rows.append(
                {
                    "strata_type": strata_type,
                    "strata_id": str(model.strata_id),
                    "species": str(model.species),
                    "age": float(age),
                    "volume_m3ha": value,
                    "model_id": getattr(model, "model_id", None),
                }
            )
    return pd.DataFrame(rows)
