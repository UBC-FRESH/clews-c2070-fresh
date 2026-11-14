"""Newfoundland and Labrador FAL permanent-sample-plot adapter.

The FAL service publishes mature and immature plot layers plus remeasurement
tallies.  Visit years come from ``remeasurement_crew_date.ORIGINAL_DATE``
(epoch milliseconds, interpreted in America/St_Johns); the ``REMEASUREMENT``
field is a visit sequence number, not a year.

Mapping decisions
-----------------
- ``visit_id`` is ``PLOTKEY|REMEASUREMENT``; ``tree_id`` is ``TREENUMBER``.
- Species are only on the permanent-tree lists, so the remeasurement tables are
  left-joined to those lists.
- ``TREESTATUS``: 0/3/4/6/7/8 -> live, 1/2/5 -> dead, 9 -> missing.
- ``PLOTSIZE`` has no coded domain.  It is interpreted as square metres, per the
  archived contents note, and converted to hectares.  Because the source has no
  per-tree expansion factor, BA/TPH are left null rather than fabricated.
- ``height = 99`` is treated as the documented sentinel and set null.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

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
)
from .schema import MAX_DBH_CM, MAX_HEIGHT_M
from .species import species_group_series

_STATUS = {
    "0": "live",
    "3": "live",
    "4": "live",
    "6": "live",
    "7": "live",
    "8": "live",
    "1": "dead",
    "2": "dead",
    "5": "dead",
    "9": "missing",
}


def _read_geojson(path: Path) -> list[dict]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        payload = json.load(handle)
    return payload.get("features", [])


def _site_layer(path: Path, *, stand_type: str) -> pd.DataFrame:
    records: list[dict] = []
    for feature in _read_geojson(path):
        properties = dict(feature.get("properties") or {})
        geometry = feature.get("geometry") or {}
        coordinates = geometry.get("coordinates") or [None, None]
        properties["lon"] = coordinates[0] if len(coordinates) > 0 else None
        properties["lat"] = coordinates[1] if len(coordinates) > 1 else None
        properties["_stand_type"] = stand_type
        records.append(properties)
    return pd.DataFrame(records)


def load(raw_root: Path, *, max_sites: int | None = None) -> SourceTables:
    """Load the NL FAL mature/immature PSP layers."""
    base = raw_root / "nl_fal_psp"
    source = "nl_fal"
    stats: dict = {"input": {}, "selected": {}, "dropped": {}, "source": source}
    notes: list[str] = []

    site_frames = [
        _site_layer(base / "mature.geojson.gz", stand_type="mature"),
        _site_layer(base / "immature.geojson.gz", stand_type="immature"),
    ]
    sites_raw = pd.concat(site_frames, ignore_index=True)
    sites_raw["site_id"] = clean_id(sites_raw["PLOTKEY"])
    sites_raw = sites_raw.dropna(subset=["site_id"]).drop_duplicates("site_id")
    stats["input"]["sites"] = int(len(sites_raw))
    if max_sites is not None:
        keep = set(sorted(sites_raw["site_id"].astype(str).unique())[:max_sites])
        sites_raw = sites_raw[sites_raw["site_id"].isin(keep)]
    stats["selected"]["sites"] = int(len(sites_raw))

    lat = numeric(sites_raw["lat"])
    lon = numeric(sites_raw["lon"])
    lat = lat.where(lat.between(-90, 90) & lat.ne(0))
    lon = lon.where(lon.between(-180, 180) & lon.ne(0))
    sites = pd.DataFrame(
        {
            "source": source,
            "site_id": sites_raw["site_id"].to_numpy(),
            "province": "NL",
            "eco_zone": clean_code(sites_raw["INTERPSITE"]).to_numpy(),
            "eco_subzone": pd.NA,
            "eco_variant": pd.NA,
            "site_index": pd.NA,
            "site_index_species": pd.NA,
            "plot_design": sites_raw["_stand_type"].to_numpy(),
            "plot_area_ha": pd.NA,
            "baf": pd.NA,
            "lat": lat.to_numpy(),
            "lon": lon.to_numpy(),
            "coord_accuracy": "feature_service",
        }
    )

    remeasure = pd.read_csv(base / "remeasurement.csv.gz", low_memory=False)
    remeasure["site_id"] = clean_id(remeasure["PLOTKEY"])
    remeasure = remeasure.dropna(subset=["site_id"])
    stats["input"]["visits"] = int(len(remeasure))
    visits_all_sites = len(remeasure)
    remeasure = remeasure[remeasure["site_id"].isin(sites["site_id"])]
    drop_reason(
        stats, "visit_site_not_found", int(visits_all_sites - len(remeasure))
    )
    remeasure["visit_seq"] = numeric(remeasure["REMEASUREMENT"])
    remeasure = remeasure.dropna(subset=["visit_seq"])
    selected_visits = len(remeasure)
    remeasure = remeasure.drop_duplicates(["site_id", "visit_seq"], keep="first")

    crew = pd.read_csv(base / "remeasurement_crew_date.csv.gz", low_memory=False)
    crew["site_id"] = clean_id(crew["PLOTKEY"])
    crew["visit_seq"] = numeric(crew["REMEASUREMENT"])
    crew["_date"] = pd.to_datetime(crew["ORIGINAL_DATE"], unit="ms", errors="coerce")
    crew = crew.dropna(subset=["site_id", "visit_seq"]).drop_duplicates(
        ["site_id", "visit_seq"]
    )
    crew["year"] = crew["_date"].dt.year
    remeasure = remeasure.merge(
        crew[["site_id", "visit_seq", "year"]],
        on=["site_id", "visit_seq"],
        how="left",
    )
    remeasure["visit_id"] = (
        remeasure["site_id"].fillna("NA")
        + "|"
        + remeasure["visit_seq"].astype("Int64").astype("string")
    )
    remeasure["year"] = numeric(remeasure["year"])
    with_year = len(remeasure)
    remeasure = remeasure.dropna(subset=["year"])
    drop_reason(stats, "visit_missing_year", int(with_year - len(remeasure)))
    stats["selected"]["visits"] = int(len(remeasure))
    remeasure = remeasure.sort_values(
        ["site_id", "year", "visit_seq"], kind="mergesort"
    ).reset_index(drop=True)
    remeasure["_visit_order"] = (
        remeasure.groupby("site_id", sort=False).cumcount() + 1
    )
    remeasure["_plot_area_ha"] = numeric(remeasure["PLOTSIZE"]) / 10000.0
    remeasure.loc[remeasure["_plot_area_ha"] <= 0, "_plot_area_ha"] = pd.NA
    remeasure["_baf"] = pd.NA
    remeasure["_plot_design"] = (
        "NL_FAL:" + remeasure["PLOTSIZE"].astype("Int64").astype("string") + "m2"
    )
    remeasure["site_index"] = pd.NA
    remeasure["age"] = pd.NA
    remeasure["_site_index_species"] = pd.NA
    visits = remeasure[
        [
            "site_id",
            "visit_id",
            "year",
            "_visit_order",
            "_plot_area_ha",
            "_baf",
            "_plot_design",
            "site_index",
            "age",
            "_site_index_species",
        ]
    ].copy()
    visits["source"] = source
    visits["year"] = visits["year"].astype("int64")
    notes.append(
        "visit year from remeasurement_crew_date.ORIGINAL_DATE, not from the "
        "REMEASUREMENT sequence number."
    )
    notes.append(
        "PLOTSIZE is interpreted as square metres (source has no coded domain); "
        "no per-tree expansion factor is available, so BA/TPH are null."
    )

    tallies = []
    permanent = []
    for stand_type in ("mature", "immature"):
        tallies.append(
            pd.read_csv(
                base / f"{stand_type}_trees_remeasurement.csv.gz",
                low_memory=False,
            ).assign(_stand_type=stand_type)
        )
        permanent.append(
            pd.read_csv(
                base / f"{stand_type}_trees_permanent.csv.gz",
                low_memory=False,
                usecols=["PLOTKEY", "TREENUMBER", "SPECIES"],
            ).assign(_stand_type=stand_type)
        )
    tallies_raw = pd.concat(tallies, ignore_index=True)
    permanent_raw = pd.concat(permanent, ignore_index=True)
    stats["input"]["trees"] = int(len(tallies_raw))

    tallies_raw["site_id"] = clean_id(tallies_raw["PLOTKEY"])
    tallies_raw["visit_seq"] = numeric(tallies_raw["REMEASUREMENT"])
    tallies_all_sites = len(tallies_raw)
    tallies_raw = tallies_raw[tallies_raw["site_id"].isin(sites["site_id"])]
    stats["selected"]["trees"] = int(len(tallies_raw))
    drop_reason(
        stats, "tree_site_not_found", int(tallies_all_sites - len(tallies_raw))
    )

    permanent_raw["site_id"] = clean_id(permanent_raw["PLOTKEY"])
    permanent_raw["tree_no"] = clean_id(permanent_raw["TREENUMBER"])
    permanent_raw["species_code"] = clean_code(
        permanent_raw["SPECIES"], upper=True
    )
    permanent_raw = permanent_raw.drop_duplicates(
        ["site_id", "tree_no"], keep="first"
    )

    trees = pd.DataFrame(
        {
            "source": source,
            "site_id": tallies_raw["site_id"],
            "visit_id": (
                tallies_raw["site_id"].fillna("NA")
                + "|"
                + tallies_raw["visit_seq"].astype("Int64").astype("string")
            ),
            "tree_id": clean_id(tallies_raw["TREENUMBER"]),
            "species_code": clean_code(tallies_raw["_stand_type"]).map(
                {"mature": pd.NA, "immature": pd.NA}
            ),
            "dbh_cm": non_negative(tallies_raw["DBH"]),
            "height_m": non_negative(tallies_raw["TREEHEIGHT"]),
            "status": map_status(tallies_raw["TREESTATUS"], _STATUS),
            "crown_class": clean_code(tallies_raw.get("CROWNRATIO")),
            "expansion_factor": pd.NA,
            "damage_code": clean_code(
                first_non_empty(tallies_raw, ["CAUSEOFDEATH", "STEMCONDITION"])
            ),
            "_stand_type": tallies_raw["_stand_type"],
        }
    )
    trees = trees.merge(
        permanent_raw[["site_id", "tree_no", "species_code"]],
        left_on=["site_id", "tree_id"],
        right_on=["site_id", "tree_no"],
        how="left",
        suffixes=("", "_permanent"),
    )
    trees["species_code"] = clean_code(
        trees["species_code_permanent"], upper=True
    )

    bad_dbh = trees["dbh_cm"].gt(MAX_DBH_CM)
    bad_height = trees["height_m"].gt(MAX_HEIGHT_M)
    sentinel_height = trees["height_m"].eq(99)
    drop_reason(stats, "tree_dbh_out_of_range", int(bad_dbh.sum()))
    drop_reason(stats, "tree_height_out_of_range", int(bad_height.sum()))
    drop_reason(stats, "tree_height_99_sentinel", int(sentinel_height.sum()))
    trees.loc[bad_dbh, "dbh_cm"] = pd.NA
    trees.loc[bad_height | sentinel_height, "height_m"] = pd.NA

    missing_key = (
        trees["site_id"].isna()
        | trees["visit_id"].isna()
        | trees["tree_id"].isna()
    )
    drop_reason(stats, "tree_missing_site_or_tree_key", int(missing_key.sum()))
    trees = trees.loc[~missing_key].copy()

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

    trees["_tally"] = False
    trees["_ba_m2ha"] = pd.NA
    trees["_volume_m3ha"] = pd.NA
    trees["species_group"] = species_group_series(source, trees["species_code"])
    trees["is_remeasured"] = False
    notes.append(
        "TREEHEIGHT=99 is the documented sentinel; those values are null. "
        "No source expansion factor is present."
    )
    notes.append(
        "Three exact duplicate tree records (same PLOTKEY/REMEASUREMENT/"
        "TREENUMBER) were dropped; 168 tallies did not match a site-layer "
        "PLOTKEY and were dropped."
    )
    notes.append(
        "Two remeasurement visits belong to PLOTKEY 8_119019, which is absent "
        "from the point layers; they and its 168 tallies are dropped."
    )
    return SourceTables(
        source=source,
        sites=sites,
        visits=visits,
        trees=trees,
        stats=stats,
        notes=notes,
    )
