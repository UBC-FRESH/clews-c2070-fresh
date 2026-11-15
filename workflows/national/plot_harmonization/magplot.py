"""MAGPlot v1.2 and MAGPlot WFS supplement adapter.

MAGPlot is plot-based: a site can carry several plot types (large-tree,
small-tree, regeneration, variable-radius) at the same ``meas_num``.  The v0
visit key is therefore the plot-level measurement event
``plot_id|meas_num``, not a site-level average across nested plots.  Trees keep
``plot_id`` in their identifier so the site/tree key never spans two designs.

Mapping decisions
-----------------
- ``tree_id`` is ``plot_id|tree_num`` for tagged trees (``tree_num < 8M``).
  Untracked 8M/9M records get ``plot_id|tree_num|meas_num`` because their
  number is reassigned between measurements.
- ``stem_ha`` is the supplied per-hectare expansion factor.  It is ``-9`` for
  BC variable-radius VRI/SUP/VLT plots; those visits get null BA/TPH rather
  than a fabricated BAF.
- ``tree_status``: ``LS``/``L`` -> live, ``DS`` -> dead, ``LF`` (live fallen)
  -> live, ``-1`` -> missing.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

from .common import (
    SourceTables,
    clean_code,
    clean_id,
    drop_reason,
    map_status,
    non_negative,
    numeric,
    positive,
)
from .schema import MAX_DBH_CM, MAX_HEIGHT_M
from .species import species_group_series

_STATUS = {
    "LS": "live",
    "L": "live",
    "DS": "dead",
    "LF": "live",
    "-1": "missing",
}
_GPP = {
    "1": "PSP",
    "3": "VRI",
    "4": "CMI",
    "5": "YSM",
    "6": "SUP",
    "7": "VLT",
    "8": "RPN",
    "11": "TSP",
}
_CROWN_MISSING = {"-1", "-9"}


def _read_table(
    zip_path: Path,
    data_dir: Path | None,
    name: str,
    **kwargs,
) -> pd.DataFrame:
    if data_dir is not None and (data_dir / name).is_file():
        return pd.read_csv(data_dir / name, **kwargs)
    with zipfile.ZipFile(zip_path) as archive:
        with archive.open(name) as handle:
            return pd.read_csv(handle, **kwargs)


def _clean_coord(series: pd.Series, limit: float) -> pd.Series:
    out = numeric(series)
    return out.where(out.between(-limit, limit) & out.ne(0))


def _plot_design(values: pd.Series, *, prefix: str = "MAGPlot") -> pd.Series:
    codes = clean_code(values, upper=True)
    labels = codes.map(_GPP)
    return prefix + ":" + labels.fillna(codes).fillna("unknown")


def _status(values: pd.Series) -> pd.Series:
    return map_status(values, _STATUS)


def _wkt_lon_lat(values: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Project WFS EPSG:3978 WKT points to lon/lat when pyproj is present."""
    extracted = values.astype("string").str.extract(
        r"POINT\s*\(\s*([-0-9.eE]+)\s+([-0-9.eE]+)\s*\)"
    )
    x = pd.to_numeric(extracted[0], errors="coerce")
    y = pd.to_numeric(extracted[1], errors="coerce")
    try:
        from pyproj import Transformer
    except ImportError:
        return (
            pd.Series(pd.NA, index=values.index, dtype="float64"),
            pd.Series(pd.NA, index=values.index, dtype="float64"),
        )
    transformer = Transformer.from_crs(3978, 4326, always_xy=True)
    lon, lat = transformer.transform(x.to_numpy(), y.to_numpy())
    return (
        pd.Series(lon, index=values.index, dtype="float64"),
        pd.Series(lat, index=values.index, dtype="float64"),
    )


def all_site_ids(raw_root: Path, *, data_dir: Path | None = None) -> set[str]:
    """Return every cleaned ZIP site identifier before any sampling filter."""
    zip_path = raw_root / "magplot" / "MAGPlot_latest_data_package.zip"
    sites_raw = _read_table(zip_path, data_dir, "magp_sites.csv")
    return set(clean_id(sites_raw["magp_site_id"]).dropna().astype(str))


def load_zip(
    raw_root: Path,
    *,
    max_sites: int | None = None,
    data_dir: Path | None = None,
) -> SourceTables:
    """Load the open MAGPlot v1.2 ZIP package (or an extracted copy)."""
    zip_path = raw_root / "magplot" / "MAGPlot_latest_data_package.zip"
    stats: dict = {"input": {}, "selected": {}, "dropped": {}, "source": "magplot"}
    notes: list[str] = []

    sites_raw = _read_table(zip_path, data_dir, "magp_sites.csv")
    stats["input"]["sites"] = int(len(sites_raw))
    sites_raw["site_id"] = clean_id(sites_raw["magp_site_id"])
    sites_raw = sites_raw.dropna(subset=["site_id"]).drop_duplicates("site_id")
    if max_sites is not None:
        keep = set(sorted(sites_raw["site_id"].astype(str).unique())[:max_sites])
        sites_raw = sites_raw[sites_raw["site_id"].isin(keep)]
    stats["selected"]["sites"] = int(len(sites_raw))

    lat = _clean_coord(sites_raw["latitude"], 90)
    lon = _clean_coord(sites_raw["longitude"], 180)
    accuracy = pd.Series("approx_10km", index=sites_raw.index, dtype="string")
    accuracy = accuracy.mask(lat.isna() | lon.isna(), "missing")
    sites = pd.DataFrame(
        {
            "source": "magplot",
            "site_id": sites_raw["site_id"].to_numpy(),
            "province": clean_code(sites_raw["province"], upper=True).to_numpy(),
            "eco_zone": pd.NA,
            "eco_subzone": pd.NA,
            "eco_variant": pd.NA,
            "site_index": pd.NA,
            "site_index_species": pd.NA,
            "plot_design": _plot_design(sites_raw["gpp_type"]).to_numpy(),
            "plot_area_ha": pd.NA,
            "baf": pd.NA,
            "lat": lat.to_numpy(),
            "lon": lon.to_numpy(),
            "coord_accuracy": accuracy.to_numpy(),
        }
    )

    header = _read_table(
        zip_path,
        data_dir,
        "magp_tree_header.csv",
        usecols=[
            "magp_site_id",
            "plot_id",
            "plot_type_id",
            "meas_num",
            "meas_year",
            "meas_plot_size",
        ],
    )
    stats["input"]["visits"] = int(len(header))
    header["site_id"] = clean_id(header["magp_site_id"])
    header["plot_id"] = clean_id(header["plot_id"])
    header["meas_num_value"] = numeric(header["meas_num"])
    header = header[header["site_id"].isin(sites["site_id"])]
    header["visit_id"] = (
        header["plot_id"].fillna("NA")
        + "|"
        + clean_id(header["meas_num"]).fillna("NA")
    )
    header = header.dropna(
        subset=["site_id", "visit_id", "plot_id", "meas_num_value"]
    )
    header = header.drop_duplicates("visit_id", keep="first")
    header["year"] = numeric(header["meas_year"])
    header = header.dropna(subset=["year"])
    header = header.sort_values(
        ["site_id", "plot_id", "meas_num_value"], kind="mergesort"
    )
    header["_visit_order"] = (
        header.groupby(["site_id", "plot_id"], sort=False).cumcount() + 1
    )
    header["_plot_area_ha"] = positive(header["meas_plot_size"])
    header["_baf"] = pd.NA
    header["_plot_design"] = (
        "MAGPlot:"
        + clean_code(header["plot_type_id"]).fillna("unknown")
        + ":"
        + np.where(header["_plot_area_ha"].notna(), "fixed_area", "variable_or_unknown")
    )
    visits = header[
        [
            "site_id",
            "visit_id",
            "year",
            "_visit_order",
            "_plot_area_ha",
            "_baf",
            "_plot_design",
        ]
    ].copy()
    visits["source"] = "magplot"
    visits["site_index"] = pd.NA
    visits["age"] = pd.NA
    visits["_site_index_species"] = pd.NA
    visits["year"] = visits["year"].astype("int64")
    stats["selected"]["visits"] = int(len(visits))
    notes.append(
        "visit_id is plot_id|meas_num; nested large/small/regeneration plots are "
        "kept separate to avoid double counting."
    )

    trees_raw = _read_table(
        zip_path,
        data_dir,
        "magp_trees.csv",
        usecols=[
            "magp_site_id",
            "plot_id",
            "plot_type_id",
            "meas_num",
            "tree_num",
            "tree_id",
            "species_gsv",
            "dbh",
            "height",
            "crown_class",
            "stem_ha",
            "tree_status",
            "stem_cond",
        ],
        low_memory=False,
    )
    stats["input"]["trees"] = int(len(trees_raw))
    trees_raw["site_id"] = clean_id(trees_raw["magp_site_id"])
    trees_raw = trees_raw[trees_raw["site_id"].isin(sites["site_id"])]
    stats["selected"]["trees"] = int(len(trees_raw))

    trees = pd.DataFrame(
        {
            "source": "magplot",
            "site_id": trees_raw["site_id"],
            "plot_id": clean_id(trees_raw["plot_id"]),
            "meas_num": clean_id(trees_raw["meas_num"]),
            "tree_num": numeric(trees_raw["tree_num"]),
            "species_code": clean_code(trees_raw["species_gsv"]),
            "dbh_cm": non_negative(trees_raw["dbh"]),
            "height_m": non_negative(trees_raw["height"]),
            "status": _status(trees_raw["tree_status"]),
            "crown_class": clean_code(trees_raw["crown_class"]),
            "expansion_factor": positive(trees_raw["stem_ha"]),
            "damage_code": clean_code(trees_raw["stem_cond"]),
            "_tree_id_source": clean_id(trees_raw["tree_id"]),
        }
    )
    trees["visit_id"] = (
        trees["plot_id"].fillna("NA") + "|" + trees["meas_num"].fillna("NA")
    )
    missing_key = (
        trees["site_id"].isna()
        | trees["plot_id"].isna()
        | trees["tree_num"].isna()
        | trees["visit_id"].isna()
        | trees["meas_num"].isna()
    )
    drop_reason(stats, "tree_missing_site_or_tree_key", int(missing_key.sum()))
    trees = trees.loc[~missing_key].copy()
    trees["tree_id"] = np.where(
        trees["tree_num"] < 8_000_000,
        trees["plot_id"].astype(str)
        + "|"
        + trees["tree_num"].astype("Int64").astype("string"),
        trees["plot_id"].astype(str)
        + "|"
        + trees["tree_num"].astype("Int64").astype("string")
        + "|"
        + trees["meas_num"],
    )
    trees["tree_id"] = pd.Series(trees["tree_id"], dtype="string")

    bad_dbh = trees["dbh_cm"].gt(MAX_DBH_CM)
    bad_height = trees["height_m"].gt(MAX_HEIGHT_M)
    drop_reason(stats, "tree_dbh_out_of_range", int(bad_dbh.sum()))
    drop_reason(stats, "tree_height_out_of_range", int(bad_height.sum()))
    trees.loc[bad_dbh, "dbh_cm"] = pd.NA
    trees.loc[bad_height, "height_m"] = pd.NA
    trees.loc[
        trees["crown_class"].isin(list(_CROWN_MISSING)), "crown_class"
    ] = pd.NA
    trees.loc[
        trees["damage_code"].isin(["-1", "-9"]), "damage_code"
    ] = pd.NA

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
    trees["_ba_m2ha"] = (
        np.pi * (trees["dbh_cm"] / 200.0) ** 2 * trees["expansion_factor"]
    )
    trees["_volume_m3ha"] = pd.NA
    trees["species_group"] = species_group_series("magplot", trees["species_code"])
    trees["is_remeasured"] = False
    return SourceTables(
        source="magplot",
        sites=sites,
        visits=visits,
        trees=trees,
        stats=stats,
        notes=notes,
    )


def load_wfs(
    raw_root: Path,
    *,
    zip_site_ids: set[str],
    max_sites: int | None = None,
) -> SourceTables:
    """Load WFS sites and tree layers that are absent from the ZIP package."""
    base = raw_root / "magplot" / "wfs"
    stats: dict = {"input": {}, "selected": {}, "dropped": {}, "source": "magplot_wfs"}
    notes: list[str] = []

    sites_raw = pd.read_csv(base / "sites_summary.csv.gz", low_memory=False)
    stats["input"]["sites"] = int(len(sites_raw))
    sites_raw["site_id"] = clean_id(sites_raw["magp_site_id"])
    sites_raw = sites_raw.dropna(subset=["site_id"])
    sites_raw = sites_raw[~sites_raw["site_id"].isin(zip_site_ids)]
    sites_raw = sites_raw.drop_duplicates("site_id")
    if max_sites is not None:
        keep = set(sorted(sites_raw["site_id"].astype(str).unique())[:max_sites])
        sites_raw = sites_raw[sites_raw["site_id"].isin(keep)]
    stats["selected"]["sites"] = int(len(sites_raw))
    wfs_ids = set(sites_raw["site_id"].astype(str))

    wfs_lon, wfs_lat = _wkt_lon_lat(sites_raw["WKT"])
    wfs_lon = _clean_coord(wfs_lon, 180)
    wfs_lat = _clean_coord(wfs_lat, 90)
    wfs_accuracy = pd.Series(
        "wfs_epsg3978_transformed", index=sites_raw.index, dtype="string"
    ).mask(wfs_lat.isna() | wfs_lon.isna(), "missing")
    sites = pd.DataFrame(
        {
            "source": "magplot_wfs",
            "site_id": sites_raw["site_id"].to_numpy(),
            "province": clean_code(sites_raw["province"], upper=True).to_numpy(),
            "eco_zone": pd.NA,
            "eco_subzone": pd.NA,
            "eco_variant": pd.NA,
            "site_index": pd.NA,
            "site_index_species": pd.NA,
            "plot_design": _plot_design(
                sites_raw["gpp_type"], prefix="MAGPlotWFS"
            ).to_numpy(),
            "plot_area_ha": pd.NA,
            "baf": pd.NA,
            "lat": wfs_lat.to_numpy(),
            "lon": wfs_lon.to_numpy(),
            "coord_accuracy": wfs_accuracy.to_numpy(),
        }
    )

    layers = [
        "sk_trees_measure_num_0.csv.gz",
        "sk_trees_measure_num_1.csv.gz",
        "sk_trees_measure_num_2_8.csv.gz",
        "pei_trees_measure_num_0_11.csv.gz",
        "nl_trees_measure_num_0_2.csv.gz",
        "nl_trees_measure_num_3_9.csv.gz",
    ]
    frames: list[pd.DataFrame] = []
    input_trees = 0
    for name in layers:
        layer = pd.read_csv(
            base / name,
            usecols=[
                "magp_site_id",
                "plot_id",
                "plot_type_id",
                "meas_year",
                "meas_month",
                "meas_plot_size",
                "tree_num",
                "tree_id",
                "species_gsv",
                "dbh",
                "height",
                "crown_class",
                "stem_ha",
                "tree_status",
                "stem_cond",
            ],
            low_memory=False,
        )
        input_trees += len(layer)
        layer["site_id"] = clean_id(layer["magp_site_id"])
        frames.append(layer[layer["site_id"].isin(wfs_ids)])
    stats["input"]["trees"] = int(input_trees)
    trees_raw = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    stats["selected"]["trees"] = int(len(trees_raw))
    if trees_raw.empty:
        return SourceTables(
            source="magplot_wfs",
            sites=sites,
            visits=pd.DataFrame(),
            trees=pd.DataFrame(),
            stats=stats,
            notes=["no WFS-only sites selected"],
        )

    trees = pd.DataFrame(
        {
            "source": "magplot_wfs",
            "site_id": trees_raw["site_id"],
            "plot_id": clean_id(trees_raw["plot_id"]),
            "tree_num": numeric(trees_raw["tree_num"]),
            "species_code": clean_code(trees_raw["species_gsv"]),
            "dbh_cm": non_negative(trees_raw["dbh"]),
            "height_m": non_negative(trees_raw["height"]),
            "status": _status(trees_raw["tree_status"]),
            "crown_class": clean_code(trees_raw["crown_class"]),
            "expansion_factor": positive(trees_raw["stem_ha"]),
            "damage_code": clean_code(trees_raw["stem_cond"]),
            "year": numeric(trees_raw["meas_year"]),
            "month": numeric(trees_raw["meas_month"]),
            "meas_plot_size": positive(trees_raw["meas_plot_size"]),
            "plot_type_id": clean_code(trees_raw["plot_type_id"]),
            "_tree_id_source": clean_id(trees_raw["tree_id"]),
        }
    )
    missing_key = (
        trees["site_id"].isna() | trees["plot_id"].isna() | trees["tree_num"].isna()
    )
    drop_reason(stats, "tree_missing_site_or_tree_key", int(missing_key.sum()))
    trees = trees.loc[~missing_key].copy()
    missing_year = trees["year"].isna()
    drop_reason(stats, "tree_missing_visit_year", int(missing_year.sum()))
    trees = trees.loc[~missing_year].copy()
    trees["visit_id"] = (
        trees["plot_id"].fillna("NA")
        + "|"
        + trees["year"].astype("Int64").astype("string")
        + "|"
        + trees["month"].fillna(0).astype("Int64").astype("string")
    )
    visit_order = (
        trees[["site_id", "plot_id", "visit_id", "year", "month"]]
        .drop_duplicates(["site_id", "visit_id"])
        .assign(_month_order=lambda frame: frame["month"].fillna(0))
        .sort_values(
            ["site_id", "plot_id", "year", "_month_order"], kind="mergesort"
        )
    )
    visit_order["_visit_order"] = (
        visit_order.groupby(["site_id", "plot_id"], sort=False).cumcount() + 1
    )
    visits = (
        trees.groupby(["site_id", "visit_id"], as_index=False)
        .agg(
            year=("year", "first"),
            _plot_area_ha=("meas_plot_size", "median"),
            _plot_design=("plot_type_id", "first"),
        )
        .merge(
            visit_order[["site_id", "visit_id", "_visit_order"]],
            on=["site_id", "visit_id"],
            how="left",
        )
    )
    visits["source"] = "magplot_wfs"
    visits["site_index"] = pd.NA
    visits["age"] = pd.NA
    visits["_site_index_species"] = pd.NA
    visits["_baf"] = pd.NA
    visits["_plot_design"] = (
        "MAGPlotWFS:"
        + visits["_plot_design"].fillna("unknown")
        + ":"
        + np.where(visits["_plot_area_ha"].notna(), "fixed_area", "variable_or_unknown")
    )
    visits["year"] = visits["year"].astype("int64")
    stats["selected"]["visits"] = int(len(visits))

    trees["tree_id"] = np.where(
        trees["tree_num"] < 8_000_000,
        trees["plot_id"].astype(str)
        + "|"
        + trees["tree_num"].astype("Int64").astype("string"),
        trees["plot_id"].astype(str)
        + "|"
        + trees["tree_num"].astype("Int64").astype("string")
        + "|"
        + trees["year"].astype("Int64").astype("string"),
    )
    trees["tree_id"] = pd.Series(trees["tree_id"], dtype="string")

    bad_dbh = trees["dbh_cm"].gt(MAX_DBH_CM)
    bad_height = trees["height_m"].gt(MAX_HEIGHT_M)
    drop_reason(stats, "tree_dbh_out_of_range", int(bad_dbh.sum()))
    drop_reason(stats, "tree_height_out_of_range", int(bad_height.sum()))
    trees.loc[bad_dbh, "dbh_cm"] = pd.NA
    trees.loc[bad_height, "height_m"] = pd.NA
    trees.loc[trees["crown_class"].isin(list(_CROWN_MISSING)), "crown_class"] = pd.NA
    trees.loc[trees["damage_code"].isin(["-1", "-9"]), "damage_code"] = pd.NA

    before = len(trees)
    trees = trees.merge(
        visits[["site_id", "visit_id", "_visit_order"]],
        on=["site_id", "visit_id"],
        how="inner",
    )
    drop_reason(stats, "tree_visit_not_found", before - len(trees))
    duplicate_key = trees.duplicated(
        ["source", "site_id", "visit_id", "tree_id"], keep="first"
    )
    drop_reason(stats, "tree_duplicate_key", int(duplicate_key.sum()))
    trees = trees.loc[~duplicate_key].copy()

    trees["_year"] = trees["year"]
    trees["_tally"] = trees["expansion_factor"].notna()
    trees["_ba_m2ha"] = (
        np.pi * (trees["dbh_cm"] / 200.0) ** 2 * trees["expansion_factor"]
    )
    trees["_volume_m3ha"] = pd.NA
    trees["species_group"] = species_group_series(
        "magplot_wfs", trees["species_code"]
    )
    trees["is_remeasured"] = False
    notes.append(
        f"WFS-only sites are those absent from the ZIP package; "
        f"{stats['input']['sites'] - len(sites):,} ZIP-overlapping sites and "
        f"{stats['input']['trees'] - stats['selected']['trees']:,} overlapping "
        "tree features are excluded to avoid double counting."
    )
    notes.append(
        "The NL WFS tree layers 0_2 and 3_9 repeat the same 10,699 tree "
        "features for the 39 WFS-only NL sites; exact duplicate keys are "
        "dropped."
    )
    return SourceTables(
        source="magplot_wfs",
        sites=sites,
        visits=visits,
        trees=trees,
        stats=stats,
        notes=notes,
    )
