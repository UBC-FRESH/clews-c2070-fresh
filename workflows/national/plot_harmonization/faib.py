"""BC FAIB PSP and non-PSP ground-plot adapter.

Both corpora use the same published tree/visit/header vocabulary, so one
adapter handles them.  The two source labels are kept separate in the output
because their site identifier ranges and sampling designs are disjoint.

Mapping decisions
-----------------
- ``visit_id`` is the FAIB ``CLSTR_ID`` (one site-level visit).
- ``tree_id`` is ``PLOT-TREE_NO``; the plot component is required because a
  site visit can contain several plots.
- ``PHF_TREE`` is the per-hectare expansion factor.  It already folds in the
  plot weight; ``TREE_WT`` is applied defensively only when present.
- ``BA_TREE`` and ``VOL_MER`` are per-tree values in square metres and cubic
  metres.  Multiplying by the expansion factor gives per-hectare values.
- ``LV_D`` maps ``L`` to live and ``D`` to dead; anything else is missing.
"""

from __future__ import annotations

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

_STATUS = {"L": "live", "D": "dead"}
_DAMAGE = ["DAM_AGNA", "DAM_AGNB", "DAM_AGNC", "DAM_AGND", "DAM_AGNE"]


def _bec(frame: pd.DataFrame) -> tuple[pd.Series, pd.Series, pd.Series]:
    zone = clean_code(frame.get("BEC_ZONE"), upper=True)
    subzone = clean_code(frame.get("BEC_SBZ"))
    variant = clean_code(frame.get("BEC_VAR"))
    if subzone is not None:
        subzone = subzone.str.lower()
    return zone, subzone, variant


def _siteage(path: Path, *, psp: bool) -> pd.DataFrame:
    """Return one site-index/age row per visit."""
    frame = pd.read_csv(path, low_memory=False)
    frame["visit_id"] = clean_id(frame["CLSTR_ID"])
    if psp:
        si_columns = ["SI1", "SI2", "SI3"]
        age_columns = ["AGE_TOT1", "AGE_TOT2", "AGE_TOT3"]
    else:
        si_columns = ["SI_M_TLSO"]
        age_columns = ["AGET_TLSO", "AGEB_TLSO"]
    for column in si_columns + age_columns:
        if column not in frame.columns:
            frame[column] = np.nan
    frame["_si"] = frame[si_columns].apply(pd.to_numeric, errors="coerce").max(axis=1)
    frame["_age"] = frame[age_columns].apply(pd.to_numeric, errors="coerce").max(axis=1)
    frame["_si_species"] = clean_code(frame.get("SPECIES"), upper=True)

    si = (
        frame.dropna(subset=["_si"])
        .sort_values(["visit_id", "_si"], ascending=[True, False])
        .drop_duplicates("visit_id", keep="first")
        .set_index("visit_id")[["_si", "_si_species"]]
        .rename(columns={"_si": "site_index", "_si_species": "_site_index_species"})
    )
    age = (
        frame.dropna(subset=["_age"])
        .groupby("visit_id")["_age"]
        .max()
        .rename("age")
    )
    return si.join(age, how="outer").reset_index()


def _plot_design(visits: pd.DataFrame) -> pd.Series:
    explicit = clean_code(visits.get("PSP_PLOT_DESIGN"))
    sample_type = clean_code(visits.get("SAMP_TYP"), upper=True).map(
        {"F": "fixed_area", "V": "variable_radius"}
    )
    establishment = clean_code(visits.get("SAMPLE_ESTABLISHMENT_TYPE"))
    design = establishment.fillna("FAIB") + ":" + sample_type.fillna("unknown")
    return explicit.where(explicit.notna(), design)


def load(
    raw_root: Path,
    *,
    source: str,
    max_sites: int | None = None,
) -> SourceTables:
    """Load one FAIB corpus (``faib_psp`` or ``faib_non_psp``)."""
    if source == "faib_psp":
        base = raw_root / "bc_faib_ground_plots" / "psp"
        tree_path = base / "faib_tree_detail.csv.gz"
        header_path = base / "faib_header.csv.gz"
        visit_path = base / "faib_sample_byvisit.csv.gz"
        plot_path = base / "faib_plot_header.csv.gz"
        siteage_path = base / "faib_compiled_spcsmries_siteage.csv.gz"
        psp = True
    elif source == "faib_non_psp":
        base = raw_root / "bc_faib_ground_plots" / "non_psp" / "publish_province"
        tree_path = base / "faib_tree_detail.csv.gz"
        header_path = base / "faib_header.csv.gz"
        visit_path = base / "faib_sample_byvisit.csv.gz"
        plot_path = base / "faib_plot_header.csv.gz"
        siteage_path = base / "faib_compiled_spcsmries_siteage.csv.gz"
        psp = False
    else:
        raise ValueError(f"unsupported FAIB source {source!r}")

    stats: dict = {"input": {}, "selected": {}, "dropped": {}, "source": source}
    notes: list[str] = []

    header = pd.read_csv(
        header_path,
        usecols=[
            "SITE_IDENTIFIER",
            "BEC_ZONE",
            "BEC_SBZ",
            "BEC_VAR",
            "Longitude",
            "Latitude",
            "UTM_SOURCE",
            "SAMPLE_ESTABLISHMENT_TYPE",
        ],
        low_memory=False,
    )
    stats["input"]["sites"] = int(len(header))
    header["site_id"] = clean_id(header["SITE_IDENTIFIER"])
    header = header.dropna(subset=["site_id"]).drop_duplicates("site_id")
    if max_sites is not None:
        keep = set(sorted(header["site_id"].astype(str).unique())[:max_sites])
        header = header[header["site_id"].isin(keep)]
    stats["selected"]["sites"] = int(len(header))

    zone, subzone, variant = _bec(header)
    lat = numeric(header.get("Latitude"))
    lon = numeric(header.get("Longitude"))
    lat = lat.where(lat.between(-90, 90) & lat.ne(0))
    lon = lon.where(lon.between(-180, 180) & lon.ne(0))
    coord_accuracy = clean_code(header.get("UTM_SOURCE")).fillna("missing")
    coord_accuracy = coord_accuracy.mask(lat.isna() | lon.isna(), "missing")

    sites = pd.DataFrame(
        {
            "source": source,
            "site_id": header["site_id"].to_numpy(),
            "province": "BC",
            "eco_zone": zone.to_numpy(),
            "eco_subzone": subzone.to_numpy(),
            "eco_variant": variant.to_numpy(),
            "site_index": pd.NA,
            "site_index_species": pd.NA,
            "plot_design": clean_code(
                header.get("SAMPLE_ESTABLISHMENT_TYPE")
            ).fillna("unknown").to_numpy(),
            "plot_area_ha": pd.NA,
            "baf": pd.NA,
            "lat": lat.to_numpy(),
            "lon": lon.to_numpy(),
            "coord_accuracy": coord_accuracy.to_numpy(),
        }
    )

    visits_raw = pd.read_csv(
        visit_path,
        usecols=[
            "SITE_IDENTIFIER",
            "CLSTR_ID",
            "VISIT_NUMBER",
            "MEAS_YR",
            "SAMP_TYP",
            "PSP_PLOT_DESIGN",
            "SAMPLE_ESTABLISHMENT_TYPE",
        ],
        low_memory=False,
    )
    stats["input"]["visits"] = int(len(visits_raw))
    visits_raw["site_id"] = clean_id(visits_raw["SITE_IDENTIFIER"])
    visits_raw["visit_id"] = clean_id(visits_raw["CLSTR_ID"])
    visits = pd.DataFrame(
        {
            "source": source,
            "site_id": visits_raw["site_id"],
            "visit_id": visits_raw["visit_id"],
            "year": numeric(visits_raw["MEAS_YR"]),
            "visit_number": numeric(visits_raw["VISIT_NUMBER"]),
        }
    )
    visits = visits.dropna(subset=["site_id", "visit_id", "year"])
    visits = visits[visits["site_id"].isin(header["site_id"])]
    selected_visits = len(visits)
    visits = visits.drop_duplicates("visit_id", keep="first")
    visits = visits.sort_values(
        ["site_id", "year", "visit_number"], kind="mergesort"
    ).reset_index(drop=True)
    visits["_visit_order"] = visits.groupby("site_id", sort=False).cumcount() + 1
    design_source = visits_raw[
        ["visit_id", "PSP_PLOT_DESIGN", "SAMP_TYP", "SAMPLE_ESTABLISHMENT_TYPE"]
    ].drop_duplicates("visit_id")
    visits = visits.merge(design_source, on="visit_id", how="left")
    visits["_plot_design"] = _plot_design(visits)
    visits["year"] = visits["year"].astype("int64")
    stats["selected"]["visits"] = int(len(visits))
    drop_reason(
        stats, "visit_missing_key_or_year", int(selected_visits) - len(visits)
    )
    notes.append(
        "visit_id is CLSTR_ID; a site may contain several plots but the visit "
        "table is site-level."
    )

    plot_header = pd.read_csv(
        plot_path,
        usecols=["CLSTR_ID", "PLOT", "PLOT_AREA_MAIN", "V_BAF"],
        low_memory=False,
    )
    plot_header["visit_id"] = clean_id(plot_header["CLSTR_ID"])
    plot_header["_area"] = positive(plot_header["PLOT_AREA_MAIN"])
    plot_header["_baf"] = positive(plot_header["V_BAF"])
    plot_agg = (
        plot_header.groupby("visit_id", sort=False)
        .agg(_plot_area_ha=("_area", "median"), _baf=("_baf", "median"))
        .reset_index()
    )
    visits = visits.merge(plot_agg, on="visit_id", how="left")

    siteage = _siteage(siteage_path, psp=psp)
    visits = visits.merge(siteage, on="visit_id", how="left")
    notes.append(
        "site_index is the maximum of the compiled species-level SI columns "
        "for the visit; age is the maximum compiled total age. A small number "
        "of compiled ages exceed 1000 years and are retained as source values."
    )

    tree_columns = [
        "SITE_IDENTIFIER",
        "CLSTR_ID",
        "VISIT_NUMBER",
        "PLOT",
        "TREE_NO",
        "SPECIES",
        "DBH",
        "HEIGHT",
        "LV_D",
        "CR_CL",
        "PHF_TREE",
        "TREE_WT",
        "BA_TREE",
        "VOL_MER",
    ] + _DAMAGE
    trees_raw = pd.read_csv(
        tree_path, usecols=tree_columns, low_memory=False
    )
    stats["input"]["trees"] = int(len(trees_raw))
    if max_sites is not None:
        trees_raw = trees_raw[
            clean_id(trees_raw["SITE_IDENTIFIER"]).isin(header["site_id"])
        ]
    stats["selected"]["trees"] = int(len(trees_raw))

    trees = pd.DataFrame(
        {
            "source": source,
            "site_id": clean_id(trees_raw["SITE_IDENTIFIER"]),
            "visit_id": clean_id(trees_raw["CLSTR_ID"]),
            "plot": clean_id(trees_raw["PLOT"]),
            "tree_no": clean_id(trees_raw["TREE_NO"]),
            "species_code": clean_code(trees_raw["SPECIES"], upper=True),
            "dbh_cm": non_negative(trees_raw["DBH"]),
            "height_m": non_negative(trees_raw["HEIGHT"]),
            "status": map_status(trees_raw["LV_D"], _STATUS),
            "crown_class": clean_code(trees_raw["CR_CL"]),
            "expansion_factor": positive(trees_raw["PHF_TREE"]),
            "damage_code": clean_code(
                trees_raw[_DAMAGE]
                .replace({"": pd.NA, "-1": pd.NA})
                .bfill(axis=1)
                .iloc[:, 0]
            ),
            "_ba_source": numeric(trees_raw["BA_TREE"]),
            "_volume_source": numeric(trees_raw["VOL_MER"]),
            "_tree_weight": numeric(trees_raw["TREE_WT"]).fillna(1.0),
        }
    )
    trees["tree_id"] = (
        trees["plot"].fillna("NA").astype("string")
        + "-"
        + trees["tree_no"].fillna("NA").astype("string")
    )
    missing_key = (
        trees["site_id"].isna()
        | trees["visit_id"].isna()
        | trees["plot"].isna()
        | trees["tree_no"].isna()
    )
    drop_reason(stats, "tree_missing_site_or_tree_key", int(missing_key.sum()))
    trees = trees.loc[~missing_key].copy()

    # DBH and height bounds are validation rules, not silent corrections.
    bad_dbh = trees["dbh_cm"].gt(MAX_DBH_CM)
    bad_height = trees["height_m"].gt(MAX_HEIGHT_M)
    drop_reason(stats, "tree_dbh_out_of_range", int(bad_dbh.sum()))
    drop_reason(stats, "tree_height_out_of_range", int(bad_height.sum()))
    trees.loc[bad_dbh, "dbh_cm"] = pd.NA
    trees.loc[bad_height, "height_m"] = pd.NA

    trees["expansion_factor"] = trees["expansion_factor"] * trees["_tree_weight"]

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
    trees["_ba_m2ha"] = (trees["_ba_source"] * trees["expansion_factor"]).where(
        trees["_ba_source"].notna(), geometric_ba
    )
    trees["_volume_m3ha"] = trees["_volume_source"] * trees["expansion_factor"]
    trees["species_group"] = species_group_series(
        source, trees["species_code"]
    )
    trees["is_remeasured"] = False
    dropped_keys = stats.get("dropped", {}).get("tree_missing_site_or_tree_key", 0)
    notes.append(
        f"{dropped_keys} tree rows with a blank PLOT/TREE_NO were dropped."
    )
    stats["notes"] = notes
    return SourceTables(
        source=source,
        sites=sites,
        visits=visits,
        trees=trees,
        stats=stats,
        notes=notes,
    )
