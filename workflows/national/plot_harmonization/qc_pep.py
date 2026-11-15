"""Québec PEP GeoPackage adapter.

The PEP stores tally trees in ``dendro_arbres`` and the intensively measured
height/age subsample in ``dendro_arbres_etudes``.  DBH is in millimetres and is
converted to centimetres here.  The source supplies ``tige_ha`` (stems per
hectare) and ``st_ha`` (basal area per hectare) per tree, so plot area is
derived as ``1 / median(tige_ha)`` hectares rather than guessed from
``dimension``.

Mapping decisions
-----------------
- ``visit_id`` is ``id_pe_mes``; ``tree_id`` is the persistent ``id_arbre``.
- ``etat`` live codes 10/12/30/40/42/50/52/58 -> live; the documented dead
  codes -> dead; removed/intruder/sapling/missing -> missing.
- ``dhp`` millimetres -> centimetres.
- ``haut_arbre`` is decimetres -> metres; ``hauteurdef`` is already metres.
- No numeric site index is stored; it is left null.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

from .common import (
    SourceTables,
    clean_code,
    clean_id,
    drop_reason,
    first_non_empty,
    map_status,
    non_negative,
    numeric,
    positive,
)
from .schema import MAX_DBH_CM, MAX_HEIGHT_M
from .species import species_group_series

_LIVE = {"10", "12", "30", "40", "42", "50", "52", "58"}
_DEAD = {
    "14", "15", "16", "17", "18", "20", "21", "22", "23", "24", "29",
    "34", "35", "36", "44", "45", "46", "54", "55", "56", "68", "78",
    "88", "98",
}
_STATUS = {code: "live" for code in _LIVE}
_STATUS.update({code: "dead" for code in _DEAD})


def _query(connection: sqlite3.Connection, sql: str) -> pd.DataFrame:
    return pd.read_sql_query(sql, connection)


def plot_area_ha(expansion_factor: pd.Series) -> pd.Series:
    """Convert a stems-per-hectare factor to plot area in hectares.

    ``tige_ha`` is stems per hectare, so one stem represents ``1 / tige_ha``
    hectares.  A 25 stems-per-hectare factor therefore represents a 0.04 ha
    plot.  Non-positive factors become null.
    """
    factor = positive(expansion_factor)
    return 1.0 / factor


def load(
    raw_root: Path,
    *,
    gpkg_path: Path | None = None,
    max_sites: int | None = None,
) -> SourceTables:
    """Load the PEP GeoPackage."""
    source = "qc_pep"
    stats: dict = {"input": {}, "selected": {}, "dropped": {}, "source": source}
    notes: list[str] = []

    if gpkg_path is None:
        raise ValueError(
            "qc_pep requires --qc-gpkg or a pre-extracted PEP.gpkg path"
        )
    connection = sqlite3.connect(str(gpkg_path))

    stats["input"]["sites"] = int(
        _query(connection, "SELECT COUNT(*) AS n FROM placette").iloc[0]["n"]
    )
    stats["input"]["visits"] = int(
        _query(connection, "SELECT COUNT(*) AS n FROM placette_mes").iloc[0]["n"]
    )
    stats["input"]["trees"] = int(
        _query(connection, "SELECT COUNT(*) AS n FROM dendro_arbres").iloc[0]["n"]
    )

    placette = _query(
        connection,
        """
        SELECT id_pe, type_pe, reseau, latitude, longitude, in_gps
        FROM placette
        """,
    )
    placette["site_id"] = clean_id(placette["id_pe"])
    placette = placette.dropna(subset=["site_id"]).drop_duplicates("site_id")
    if max_sites is not None:
        keep = set(sorted(placette["site_id"].astype(str).unique())[:max_sites])
        placette = placette[placette["site_id"].isin(keep)]
    stats["selected"]["sites"] = int(len(placette))

    classification = _query(
        connection,
        """
        SELECT id_pe, zone_veg, dom_bio, reg_eco, eta_veg
        FROM classi_eco_pe
        """,
    )
    classification["site_id"] = clean_id(classification["id_pe"])
    classification = classification.drop_duplicates("site_id").set_index("site_id")

    lat = numeric(placette["latitude"])
    lon = numeric(placette["longitude"])
    lat = lat.where(lat.between(-90, 90) & lat.ne(0))
    lon = lon.where(lon.between(-180, 180) & lon.ne(0))
    gps = clean_code(placette["in_gps"], upper=True).map({"O": "gps", "N": "map"})
    gps = gps.fillna("unknown").mask(lat.isna() | lon.isna(), "missing")

    sites = pd.DataFrame(
        {
            "source": source,
            "site_id": placette["site_id"].to_numpy(),
            "province": "QC",
            "eco_zone": classification.reindex(placette["site_id"])[
                "reg_eco"
            ].to_numpy(),
            "eco_subzone": pd.NA,
            "eco_variant": pd.NA,
            "site_index": pd.NA,
            "site_index_species": pd.NA,
            "plot_design": clean_code(placette["type_pe"]).fillna("PEP").to_numpy(),
            "plot_area_ha": pd.NA,
            "baf": pd.NA,
            "lat": lat.to_numpy(),
            "lon": lon.to_numpy(),
            "coord_accuracy": gps.to_numpy(),
        }
    )

    visits_raw = _query(
        connection,
        """
        SELECT m.id_pe, m.id_pe_mes, m.no_mes, m.date_sond, m.dimension,
               s.type_eco, s.cl_age, s.haut_domi
        FROM placette_mes AS m
        LEFT JOIN station_pe AS s ON s.id_pe_mes = m.id_pe_mes
        """,
    )
    visits_raw["site_id"] = clean_id(visits_raw["id_pe"])
    visits_raw["visit_id"] = clean_id(visits_raw["id_pe_mes"])
    visits_raw = visits_raw[visits_raw["site_id"].isin(sites["site_id"])]
    visits_raw["date"] = pd.to_datetime(
        visits_raw["date_sond"], errors="coerce", utc=True
    )
    visits_raw["year"] = visits_raw["date"].dt.year
    visits = pd.DataFrame(
        {
            "source": source,
            "site_id": visits_raw["site_id"],
            "visit_id": visits_raw["visit_id"],
            "year": visits_raw["year"],
            "no_mes": numeric(visits_raw["no_mes"]),
            "type_eco": clean_code(visits_raw["type_eco"]),
            "cl_age": clean_code(visits_raw["cl_age"]),
            "haut_domi": numeric(visits_raw["haut_domi"]),
        }
    )
    visits = visits.dropna(subset=["site_id", "visit_id", "year"])
    visits = visits.drop_duplicates("visit_id", keep="first")
    visits = visits.sort_values(
        ["site_id", "year", "no_mes"], kind="mergesort"
    ).reset_index(drop=True)
    visits["_visit_order"] = visits.groupby("site_id", sort=False).cumcount() + 1
    visits["year"] = visits["year"].astype("int64")
    visits["_plot_area_ha"] = pd.NA
    visits["_baf"] = pd.NA
    visits["_plot_design"] = (
        "PEP:"
        + clean_code(visits_raw.set_index("visit_id").reindex(visits["visit_id"])[
            "dimension"
        ]).fillna("unknown").to_numpy()
    )
    visits["site_index"] = pd.NA
    visits["age"] = pd.NA
    visits["_site_index_species"] = pd.NA
    stats["selected"]["visits"] = int(len(visits))
    notes.append(
        "eco_zone is classi_eco_pe.reg_eco; eco_subzone is the visit-level "
        "station_pe.type_eco mode. No numeric site index is stored."
    )
    notes.append(
        "plot_area_ha is derived as 1/median(tige_ha) hectares after tree load; "
        "no BAF is present because plots are fixed-area."
    )

    trees_raw = _query(
        connection,
        """
        SELECT a.id_pe, a.id_pe_mes, a.no_mes, a.id_arbre, a.id_arb_mes,
               a.etat, a.essence, a.dhp, a.tige_ha, a.st_ha,
               a.hauteurdef, a.etage_arb, a.defaut, a.defautbas, a.defauthaut,
               e.haut_arbre, e.age
        FROM dendro_arbres AS a
        LEFT JOIN dendro_arbres_etudes AS e ON e.id_arb_mes = a.id_arb_mes
        """,
    )
    trees_raw["site_id"] = clean_id(trees_raw["id_pe"])
    trees_raw = trees_raw[trees_raw["site_id"].isin(sites["site_id"])]
    stats["selected"]["trees"] = int(len(trees_raw))

    height_study = non_negative(trees_raw["haut_arbre"]) / 10.0
    height_tally = non_negative(trees_raw["hauteurdef"])
    height = height_study.where(height_study.notna(), height_tally)

    trees = pd.DataFrame(
        {
            "source": source,
            "site_id": trees_raw["site_id"],
            "visit_id": clean_id(trees_raw["id_pe_mes"]),
            "tree_id": clean_id(trees_raw["id_arbre"]),
            "species_code": clean_code(trees_raw["essence"], upper=True),
            "dbh_cm": non_negative(trees_raw["dhp"]) / 10.0,
            "height_m": height,
            "status": map_status(trees_raw["etat"], _STATUS),
            "crown_class": clean_code(trees_raw["etage_arb"]),
            "expansion_factor": positive(trees_raw["tige_ha"]),
            "damage_code": first_non_empty(
                trees_raw, ["defaut", "defautbas", "defauthaut"]
            ),
            "_ba_source": numeric(trees_raw["st_ha"]),
            "_age": non_negative(trees_raw["age"]),
        }
    )
    # etage_arb is the closest crown-position field in the dictionary; the
    # tally ``etat`` code remains the status source, not a crown class.

    missing_key = (
        trees["site_id"].isna()
        | trees["visit_id"].isna()
        | trees["tree_id"].isna()
    )
    drop_reason(stats, "tree_missing_site_or_tree_key", int(missing_key.sum()))
    trees = trees.loc[~missing_key].copy()

    bad_dbh = trees["dbh_cm"].gt(MAX_DBH_CM)
    bad_height = trees["height_m"].gt(MAX_HEIGHT_M)
    drop_reason(stats, "tree_dbh_out_of_range", int(bad_dbh.sum()))
    drop_reason(stats, "tree_height_out_of_range", int(bad_height.sum()))
    trees.loc[bad_dbh, "dbh_cm"] = pd.NA
    trees.loc[bad_height, "height_m"] = pd.NA

    visit_lookup = visits[["visit_id", "_visit_order", "year"]].rename(
        columns={"year": "_year"}
    )
    before = len(trees)
    trees = trees.merge(visit_lookup, on="visit_id", how="inner")
    drop_reason(stats, "tree_visit_not_found", before - len(trees))

    duplicate_key = trees.duplicated(
        ["source", "site_id", "visit_id", "tree_id"], keep="first"
    )
    drop_reason(stats, "tree_duplicate_key", int(duplicate_key.sum()))
    trees = trees.loc[~duplicate_key].copy()

    trees["_tally"] = trees["expansion_factor"].notna()
    geometric_ba = (
        np.pi * (trees["dbh_cm"] / 200.0) ** 2 * trees["expansion_factor"]
    )
    trees["_ba_m2ha"] = trees["_ba_source"].where(
        trees["_ba_source"].notna(), geometric_ba
    )
    trees["_volume_m3ha"] = pd.NA
    trees["species_group"] = species_group_series(source, trees["species_code"])
    trees["is_remeasured"] = False

    # Derive fixed-area plot size from the source expansion factor and attach
    # height/age from the study-tree subsample.
    tally = trees[trees["_tally"]]
    area = (
        tally.assign(_area=plot_area_ha(tally["expansion_factor"]))
        .groupby("visit_id")["_area"]
        .median()
        .rename("_plot_area_ha")
    )
    visits = visits.drop(columns=["_plot_area_ha"]).merge(
        area, left_on="visit_id", right_index=True, how="left"
    )
    age = (
        trees.dropna(subset=["_age"])
        .groupby("visit_id")["_age"]
        .median()
        .rename("age")
    )
    visits = visits.drop(columns=["age"]).merge(
        age, left_on="visit_id", right_index=True, how="left"
    )

    # Site-level eco_subzone from the visit type_eco mode.
    eco = (
        visits.dropna(subset=["type_eco"])
        .groupby("site_id")["type_eco"]
        .agg(lambda s: s.mode().iloc[0])
        .rename("eco_subzone")
    )
    sites = sites.drop(columns=["eco_subzone"]).merge(
        eco, left_on="site_id", right_index=True, how="left"
    )
    connection.close()
    return SourceTables(
        source=source,
        sites=sites,
        visits=visits,
        trees=trees,
        stats=stats,
        notes=notes,
    )
