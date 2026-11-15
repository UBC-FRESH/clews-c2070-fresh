"""BC variant adapter: harmonized v0 tables to the fresh-fvsbc consumer schema.

The adapter deliberately stops at the calibration-table boundary.  It rebuilds
the three tables a BC calibration consumer reads:

- ``visits.csv``: one row per plot visit, with live BA, TPH, QMD, volume, site
  index, and age;
- ``intervals.csv``: one row per consecutive plot-visit pair, with start stand
  density and the dead basal area and mortality rate over the interval;
- ``tree_survival.csv``: one row per live tree at the start of an interval,
  with its end status, damage flag, and exposure.

It also writes ``species_crosswalk_fvsbc.csv``.  The crosswalk maps every
observed FAIB source code to a BC JSP code when the read-only ``fresh-fvsbc``
contract supplies one (fifteen JSP codes plus seven donor aliases).  A source
code outside that contract is left unmapped and flagged; it is never silently
folded into a nearby species.

Differences from ``fresh_fvsbc.calibration.build_dataset`` are intentional and
documented in ``PARITY_REPORT.md``.  The two largest are:

1. BA is rebuilt geometrically from DBH and the expansion factor, because the
   harmonized v0 tree table does not carry the source ``BA_TREE`` column.  The
   difference is a rounding-scale effect, not a model choice.
2. A dead endpoint with a null DBH is still a known death here.  The
   ``fresh-fvsbc`` builder drops that tree before the end-status join, so it
   reports the same tree as missing.  The v0 extract deliberately retains the
   status, and this adapter preserves it.

No FVS run happens here.  No model is fitted here.  The output is an input to
the calibration workstream, not a calibrated variant.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .profiles import (
    BC_CALIBRATION_GROUPS,
    BC_PROFILE,
    VariantProfile,
    derive_bec,
)
from .plot_sources import PlotSource

__all__ = [
    "BC_INTERVAL_COLUMNS",
    "BC_SPECIES_COLUMNS",
    "BC_TREE_SURVIVAL_COLUMNS",
    "BC_VISIT_COLUMNS",
    "BcAdapterError",
    "BcDataset",
    "BcTables",
    "RAW_HEADER_PATHS",
    "SpeciesResolution",
    "build_bc_dataset",
    "build_bc_tables",
    "classify_species",
    "load_tsa_desc",
    "species_crosswalk",
]

#: The BC consumer visit columns, required names first.
BC_VISIT_COLUMNS: tuple[str, ...] = (
    "BEC",
    "TSA_DESC",
    "SITE_IDENTIFIER",
    "CLSTR_ID",
    "PLOT",
    "year",
    "ba0",
    "tph0",
    "qmd",
    "volume",
    "site_index",
    "age",
)

#: The BC consumer interval columns, required names first.
BC_INTERVAL_COLUMNS: tuple[str, ...] = (
    "interval_id",
    "BEC",
    "TSA_DESC",
    "SITE_IDENTIFIER",
    "PLOT",
    "year0",
    "years",
    "ba0",
    "tph0",
    "qmd",
    "dead_ba",
    "tph_mortality",
)

#: The BC consumer tree-survival columns, required names first.
BC_TREE_SURVIVAL_COLUMNS: tuple[str, ...] = (
    "interval_id",
    "BEC",
    "TSA_DESC",
    "CLSTR_ID",
    "PLOT",
    "TREE_NO",
    "species",
    "group",
    "dbh",
    "HEIGHT",
    "phf",
    "ba",
    "years",
    "ba0",
    "tph0",
    "damage",
    "status",
    "dead",
    "known",
    "exposure",
)

#: The species crosswalk columns.
BC_SPECIES_COLUMNS: tuple[str, ...] = (
    "source_species",
    "fvs_jsp_code",
    "group",
    "resolution",
    "notes",
    "n_tree_rows",
    "n_live_rows",
    "n_calibration_rows",
    "ba_m2ha_sum",
)

#: The interval window, matching fresh-fvsbc's calibration defaults.
MIN_INTERVAL_YEARS: int = 2
MAX_INTERVAL_YEARS: int = 40

#: The tree columns the adapter asks a plot source for.
BC_TREE_INPUT_COLUMNS: tuple[str, ...] = (
    "source",
    "site_id",
    "visit_id",
    "tree_id",
    "species_code",
    "species_group",
    "dbh_cm",
    "height_m",
    "status",
    "crown_class",
    "expansion_factor",
    "damage_code",
)

#: Raw FAIB headers used only to enrich ``TSA_DESC``.  The adapter works
#: without them; the field is then null and the manifest says why.
RAW_HEADER_PATHS: Mapping[str, str] = {
    "faib_psp": "bc_faib_ground_plots/psp/faib_header.csv.gz",
    "faib_non_psp": "bc_faib_ground_plots/non_psp/publish_province/faib_header.csv.gz",
}

_FAIB_SOURCES = frozenset(RAW_HEADER_PATHS)


class BcAdapterError(ValueError):
    """A source table that cannot satisfy the BC consumer contract."""


@dataclass(frozen=True, slots=True)
class SpeciesResolution:
    """How one source species code resolves under the BC profile."""

    source_species: str
    fvs_jsp_code: str | None
    group: str
    resolution: str
    note: str

    @property
    def is_resolved(self) -> bool:
        return self.fvs_jsp_code is not None


@dataclass(frozen=True, slots=True)
class BcTables:
    """The consumer tables and the counts that explain them."""

    visits: pd.DataFrame
    intervals: pd.DataFrame
    tree_survival: pd.DataFrame
    species: pd.DataFrame
    stats: dict[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class BcDataset:
    """The written dataset, its manifest, and its in-memory tables."""

    directory: Path
    visits: pd.DataFrame
    intervals: pd.DataFrame
    tree_survival: pd.DataFrame
    species: pd.DataFrame
    manifest: dict[str, Any]


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _clean_id(values: pd.Series | Iterable[Any]) -> pd.Series:
    series = values if isinstance(values, pd.Series) else pd.Series(values)
    out = series.astype("string").str.strip()
    out = out.str.replace(r"\.0$", "", regex=True)
    return out.replace({"": pd.NA, "nan": pd.NA, "None": pd.NA, "<NA>": pd.NA})


def _clean_code(values: pd.Series | Iterable[Any], *, upper: bool = False) -> pd.Series:
    series = values if isinstance(values, pd.Series) else pd.Series(values)
    out = series.astype("string").str.strip()
    out = out.str.replace(r"\.0$", "", regex=True)
    out = out.replace({"": pd.NA, "nan": pd.NA, "None": pd.NA, "<NA>": pd.NA})
    return out.str.upper() if upper else out


def _numeric(values: pd.Series | Iterable[Any]) -> pd.Series:
    series = values if isinstance(values, pd.Series) else pd.Series(values)
    return pd.to_numeric(series, errors="coerce").astype("float64")


def _integer_key(values: pd.Series, name: str) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    if numeric.isna().any():
        bad = int(numeric.isna().sum())
        raise BcAdapterError(f"{name} has {bad} non-numeric or missing values")
    fractional = (numeric % 1).ne(0)
    if fractional.any():
        bad = int(fractional.sum())
        raise BcAdapterError(f"{name} has {bad} fractional values")
    return numeric.astype("int64")


def _require_columns(frame: pd.DataFrame, required: Sequence[str], name: str) -> None:
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise BcAdapterError(f"{name} is missing required columns: {', '.join(missing)}")


def classify_species(code: object, profile: VariantProfile = BC_PROFILE) -> SpeciesResolution:
    """Resolve one source species code under a variant profile.

    Direct JSP codes resolve to themselves.  The seven donor aliases resolve
    to their donor code.  Anything else is ``unmapped`` and keeps its source
    code in the crosswalk; the consumer ``species`` column is then null.

    Parameters
    ----------
    code : object
        A source species code, such as ``HW``, ``SS``, or ``SW``.
    profile : VariantProfile, optional
        The variant profile supplying the accepted list and donor aliases.

    Returns
    -------
    SpeciesResolution
        The source code, resolved JSP code (or ``None``), calibration group,
        resolution kind, and a human-readable note.
    """
    source = "" if code is None else str(code).strip().upper()
    if not source or source.lower() in {"nan", "none", "<na>"}:
        return SpeciesResolution(
            source_species="",
            fvs_jsp_code=None,
            group="UNKNOWN",
            resolution="unmapped",
            note="missing source species code",
        )
    if source in profile.species_codes:
        group = profile.calibration_groups.get(source, _group_of_jsp(source))
        return SpeciesResolution(
            source_species=source,
            fvs_jsp_code=source,
            group=group,
            resolution="direct",
            note="direct BC JSP code",
        )
    donor = profile.donor_species.get(source)
    if donor is not None:
        group = profile.calibration_groups.get(source, _group_of_jsp(donor))
        return SpeciesResolution(
            source_species=source,
            fvs_jsp_code=donor,
            group=group,
            resolution="donor",
            note=f"donor alias from fresh-fvsbc treelist.py: {source} -> {donor}",
        )
    group = profile.calibration_groups.get(source, "UNKNOWN")
    return SpeciesResolution(
        source_species=source,
        fvs_jsp_code=None,
        group=group,
        resolution="unmapped",
        note=(
            "not one of the 15 BC JSP codes or 7 donor aliases; "
            "no FVS species code assigned and no silent imputation"
        ),
    )


def _group_of_jsp(code: str) -> str:
    """Return the calibration group of a JSP code, or ``UNKNOWN``."""
    return BC_CALIBRATION_GROUPS.get(code, "UNKNOWN")


def load_tsa_desc(
    raw_root: str | Path,
    sources: Sequence[str],
) -> tuple[pd.Series, list[str]]:
    """Load ``TSA_DESC`` from the raw FAIB headers, if they are present.

    The harmonized v0 site table does not carry ``TSA_DESC``.  It is optional
    enrichment: a missing header is not an error, but the returned source list
    lets the manifest record whether the field was enriched.

    Parameters
    ----------
    raw_root : str or Path
        The raw data root, normally ``data/raw``.
    sources : sequence of str
        The selected source labels.  Only FAIB labels have a header path.

    Returns
    -------
    tuple of pandas.Series and list of str
        A site-id-indexed ``TSA_DESC`` series, and the relative header paths
        that were read.
    """
    root = Path(raw_root)
    frames: list[pd.DataFrame] = []
    used: list[str] = []
    for source in sources:
        relative = RAW_HEADER_PATHS.get(source)
        if relative is None:
            continue
        path = root / relative
        if not path.is_file():
            continue
        frame = pd.read_csv(
            path,
            usecols=["SITE_IDENTIFIER", "TSA_DESC"],
            dtype={"SITE_IDENTIFIER": "string"},
            low_memory=False,
        )
        frame = frame.rename(columns={"SITE_IDENTIFIER": "site_id"})
        frame["site_id"] = _clean_id(frame["site_id"])
        frame["TSA_DESC"] = _clean_code(frame["TSA_DESC"])
        frame = frame.dropna(subset=["site_id"]).drop_duplicates("site_id")
        frames.append(frame[["site_id", "TSA_DESC"]])
        used.append(relative)
    if not frames:
        return pd.Series(dtype="string", name="TSA_DESC"), used
    combined = pd.concat(frames, ignore_index=True).drop_duplicates("site_id")
    return combined.set_index("site_id")["TSA_DESC"], used


def _tsa_series(tsa_lookup: Mapping[str, object] | pd.Series | None) -> pd.Series:
    """Normalise a TSA lookup to a site-id-indexed string series."""
    if tsa_lookup is None:
        return pd.Series(dtype="string", name="TSA_DESC")
    if isinstance(tsa_lookup, pd.Series):
        series = tsa_lookup.copy()
        series.index = _clean_id(pd.Series(series.index))
    else:
        series = pd.Series(
            {_clean_id(pd.Series([key])).iloc[0]: value for key, value in tsa_lookup.items()},
            dtype="object",
        )
    series.index.name = "site_id"
    return series.rename("TSA_DESC").astype("string")


def _site_frame(
    sites: pd.DataFrame,
    profile: VariantProfile,
    *,
    include_unsupported: bool,
) -> tuple[pd.DataFrame, dict[str, int]]:
    """Derive BEC, apply the domain filter, and return site rows plus counts."""
    _require_columns(
        sites,
        ("source", "site_id", "eco_zone", "eco_subzone", "eco_variant", "plot_design"),
        "sites",
    )
    frame = sites.copy()
    frame["site_id"] = _clean_id(frame["site_id"])
    if frame["site_id"].isna().any():
        raise BcAdapterError("sites contains missing site_id values")
    duplicates = int(frame["site_id"].duplicated().sum())
    if duplicates:
        raise BcAdapterError(f"sites contains {duplicates} duplicate site_id values")
    frame["BEC"] = [
        derive_bec(zone, subzone, variant)
        for zone, subzone, variant in zip(
            frame["eco_zone"], frame["eco_subzone"], frame["eco_variant"], strict=True
        )
    ]
    frame["source_zone"] = _clean_code(frame["eco_zone"], upper=True)
    frame["supported"] = frame["source_zone"].isin(profile.supported_zones)
    frame["plot_design"] = _clean_code(frame["plot_design"])
    counts = {
        "sites_input": int(len(frame)),
        "sites_missing_bec": int(frame["BEC"].isna().sum()),
        "sites_supported": int(frame["supported"].sum()),
        "sites_dropped_unsupported": int((~frame["supported"]).sum()),
    }
    if not include_unsupported:
        frame = frame.loc[frame["supported"]].copy()
    counts["sites_selected"] = int(len(frame))
    return frame, counts


def _visit_frame(visits: pd.DataFrame, sites: pd.DataFrame) -> pd.DataFrame:
    """Normalise the v0 visit table and derive a per-site visit number."""
    _require_columns(
        visits,
        (
            "source",
            "site_id",
            "visit_id",
            "year",
            "n_trees",
            "ba_m2ha",
            "tph",
            "qmd_cm",
            "top_height_m",
            "volume_m3ha",
            "site_index",
            "age",
        ),
        "visits",
    )
    frame = visits.copy()
    frame["site_id"] = _clean_id(frame["site_id"])
    frame["visit_id"] = _clean_id(frame["visit_id"])
    frame["year"] = _numeric(frame["year"])
    if frame["year"].isna().any():
        raise BcAdapterError("visits contains missing measurement years")
    duplicates = int(frame["visit_id"].duplicated().sum())
    if duplicates:
        raise BcAdapterError(f"visits contains {duplicates} duplicate visit_id values")
    for column in ("volume_m3ha", "site_index", "age", "top_height_m", "ba_m2ha", "tph", "qmd_cm"):
        frame[column] = _numeric(frame[column])
    frame = frame.sort_values(
        ["site_id", "year", "visit_id"], kind="mergesort"
    ).reset_index(drop=True)
    frame["visit_number"] = (
        frame.groupby("site_id", sort=False).cumcount() + 1
    ).astype("int64")
    site_columns = sites[["site_id", "BEC", "plot_design", "supported"]]
    frame = frame.merge(site_columns, on="site_id", how="left")
    return frame


def _tree_frame(trees: pd.DataFrame, visits: pd.DataFrame) -> pd.DataFrame:
    """Normalise tree identifiers, status, and per-hectare basal area."""
    _require_columns(
        trees,
        (
            "source",
            "site_id",
            "visit_id",
            "tree_id",
            "species_code",
            "dbh_cm",
            "height_m",
            "status",
            "expansion_factor",
            "damage_code",
        ),
        "trees",
    )
    frame = trees.copy()
    for column in ("site_id", "visit_id", "tree_id"):
        frame[column] = _clean_id(frame[column])
    if frame[["site_id", "visit_id", "tree_id"]].isna().any().any():
        raise BcAdapterError("trees contains a missing site_id, visit_id, or tree_id")
    split = frame["tree_id"].str.split("-", n=1, expand=True)
    if split.shape[1] != 2 or split[0].isna().any() or split[1].isna().any():
        raise BcAdapterError(
            "tree_id must be the v0 'PLOT-TREE_NO' form; "
            f"{int(split.isna().any(axis=1).sum())} rows do not split"
        )
    frame["PLOT"] = _integer_key(split[0], "tree_id plot component")
    frame["TREE_NO"] = _integer_key(split[1], "tree_id tree component")
    frame["species_code"] = _clean_code(frame["species_code"], upper=True)
    frame["status"] = _clean_code(frame["status"])
    frame["crown_class"] = _clean_code(frame["crown_class"]) if "crown_class" in frame else pd.NA
    frame["dbh_cm"] = _numeric(frame["dbh_cm"])
    frame["height_m"] = _numeric(frame["height_m"])
    frame["expansion_factor"] = _numeric(frame["expansion_factor"])
    frame["damage"] = frame["damage_code"].notna()
    frame["ba"] = (
        math.pi * (frame["dbh_cm"] / 200.0) ** 2 * frame["expansion_factor"]
    )
    year_lookup = visits[["site_id", "visit_id", "year"]]
    frame = frame.merge(year_lookup, on=["site_id", "visit_id"], how="left", validate="many_to_one")
    if frame["year"].isna().any():
        missing = int(frame["year"].isna().sum())
        raise BcAdapterError(f"{missing} tree rows do not join to a selected visit")
    frame["year"] = frame["year"].astype("int64")
    duplicate_key = frame.duplicated(["site_id", "visit_id", "tree_id"])
    if duplicate_key.any():
        duplicates = int(duplicate_key.sum())
        raise BcAdapterError(f"trees contains {duplicates} duplicate site/visit/tree keys")
    return frame


def _plot_visits(live: pd.DataFrame, visits: pd.DataFrame) -> pd.DataFrame:
    """Aggregate live tally trees to one row per plot visit."""
    grouped = (
        live.groupby(["site_id", "visit_id", "PLOT"], as_index=False)
        .agg(
            ba0=("ba", "sum"),
            tph0=("expansion_factor", "sum"),
            n_trees=("tree_id", "size"),
            top_height_m=("height_m", "max"),
        )
        .reset_index(drop=True)
    )
    grouped["qmd"] = 100.0 * np.sqrt(
        grouped["ba0"] / grouped["tph0"].replace(0.0, np.nan) * (4.0 / np.pi)
    )
    visit_columns = [
        "source",
        "site_id",
        "visit_id",
        "year",
        "visit_number",
        "volume_m3ha",
        "site_index",
        "age",
        "BEC",
        "TSA_DESC",
        "plot_design",
    ]
    frame = grouped.merge(visits[visit_columns], on=["site_id", "visit_id"], how="left")
    frame = frame.sort_values(
        ["site_id", "PLOT", "year", "visit_id"], kind="mergesort"
    ).reset_index(drop=True)
    plots_in_visit = frame.groupby("visit_id", sort=False)["PLOT"].transform("nunique")
    frame["volume"] = frame["volume_m3ha"].where(plots_in_visit.eq(1))
    frame["volume_basis"] = np.select(
        [
            plots_in_visit.gt(1),
            frame["volume_m3ha"].isna(),
        ],
        [
            "cluster_multi_plot_not_apportioned",
            "source_missing",
        ],
        default="source_visit_single_plot",
    )
    frame["PLOT"] = frame["PLOT"].astype("int64")
    frame["year"] = frame["year"].astype("int64")
    frame["visit_number"] = frame["visit_number"].astype("int64")
    return frame


def _attach_species(frame: pd.DataFrame, profile: VariantProfile) -> pd.DataFrame:
    """Attach resolved JSP code, calibration group, and resolution kind."""
    observed = sorted(
        code for code in frame["species_code"].dropna().astype(str).unique() if code
    )
    resolutions = {code: classify_species(code, profile) for code in observed}
    fvs = {code: resolution.fvs_jsp_code for code, resolution in resolutions.items()}
    groups = {code: resolution.group for code, resolution in resolutions.items()}
    kinds = {code: resolution.resolution for code, resolution in resolutions.items()}
    frame = frame.copy()
    frame["species"] = frame["species_code"].map(fvs).astype("string")
    frame["group"] = frame["species_code"].map(groups).fillna("UNKNOWN").astype("string")
    frame["species_resolution"] = (
        frame["species_code"].map(kinds).fillna("unmapped").astype("string")
    )
    return frame


def _intervals_and_survival(
    plot_visits: pd.DataFrame,
    live_base: pd.DataFrame,
    all_trees: pd.DataFrame,
    visits: pd.DataFrame,
    profile: VariantProfile,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, int]]:
    """Build intervals and tree survival from consecutive plot visits."""
    visits = plot_visits.copy()
    grouped = visits.groupby(["site_id", "PLOT"], sort=False)
    for column, new in (
        ("year", "next_year"),
        ("visit_id", "next_visit"),
        ("ba0", "next_ba0"),
        ("tph0", "next_tph0"),
        ("qmd", "next_qmd"),
    ):
        visits[new] = grouped[column].shift(-1)
    intervals = visits.dropna(subset=["next_year", "next_visit"]).copy()
    intervals["years"] = intervals["next_year"] - intervals["year"]
    intervals = intervals[
        intervals["years"].between(MIN_INTERVAL_YEARS, MAX_INTERVAL_YEARS, inclusive="both")
    ].copy()
    intervals["next_year"] = intervals["next_year"].astype("int64")
    intervals["next_visit"] = intervals["next_visit"].astype("string")
    intervals["interval_id"] = (
        intervals["site_id"].astype("string")
        + "|"
        + intervals["PLOT"].astype("string")
        + "|"
        + intervals["year"].astype("string")
        + "|"
        + intervals["next_year"].astype("string")
    )
    duplicate_intervals = int(intervals["interval_id"].duplicated().sum())
    if duplicate_intervals:
        raise BcAdapterError(
            f"{duplicate_intervals} intervals share site/plot/year0/year1; "
            "the consumer interval_id would not be unique"
        )
    intervals = intervals.sort_values(
        ["site_id", "PLOT", "year", "next_year"], kind="mergesort"
    ).reset_index(drop=True)

    start_columns = [
        "site_id",
        "PLOT",
        "visit_id",
        "next_visit",
        "years",
        "next_year",
        "ba0",
        "tph0",
        "BEC",
        "TSA_DESC",
        "plot_design",
    ]
    start = live_base.merge(
        intervals[start_columns],
        left_on=["site_id", "PLOT", "visit_id"],
        right_on=["site_id", "PLOT", "visit_id"],
        how="inner",
        validate="many_to_one",
    )
    end = all_trees[
        ["site_id", "PLOT", "visit_id", "TREE_NO", "status", "expansion_factor", "dbh_cm"]
    ].rename(
        columns={
            "visit_id": "next_visit",
            "status": "end_status",
            "expansion_factor": "end_phf",
            "dbh_cm": "dbh_end",
        }
    )
    start = start.merge(
        end,
        on=["site_id", "PLOT", "next_visit", "TREE_NO"],
        how="left",
        validate="many_to_one",
    )
    start["status"] = start["end_status"].fillna("missing").astype("string")
    start.loc[~start["status"].isin(["live", "dead", "missing"]), "status"] = "missing"
    start["dead"] = start["status"].eq("dead").astype("int64")
    start["known"] = start["status"].ne("missing").astype("int64")
    start["dead_ba"] = np.where(start["dead"].eq(1), start["ba"], 0.0)
    start["dead_tph"] = np.where(
        start["dead"].eq(1),
        start["end_phf"].fillna(start["expansion_factor"]),
        0.0,
    )
    start["exposure"] = (start["years"] * start["known"]).astype("int64")

    history = live_base[
        ["site_id", "PLOT", "TREE_NO", "visit_id", "year", "dbh_cm"]
    ].sort_values(
        ["site_id", "PLOT", "TREE_NO", "year", "visit_id"], kind="mergesort"
    )
    history_group = history.groupby(["site_id", "PLOT", "TREE_NO"], sort=False)
    history["prior_dbh"] = history_group["dbh_cm"].shift(1)
    history["prior_year"] = history_group["year"].shift(1)
    start = start.merge(
        history[["site_id", "PLOT", "TREE_NO", "visit_id", "prior_dbh", "prior_year"]],
        on=["site_id", "PLOT", "TREE_NO", "visit_id"],
        how="left",
        validate="many_to_one",
    )
    elapsed = (start["year"] - start["prior_year"]).replace(0, np.nan)
    start["growth"] = (start["dbh_cm"] - start["prior_dbh"]) / elapsed

    start = _attach_species(start, profile)
    start = start.rename(
        columns={
            "dbh_cm": "dbh",
            "height_m": "HEIGHT",
            "expansion_factor": "phf",
            "ba": "ba",
            "species_code": "source_species",
            "crown_class": "crown_class",
        }
    )
    start["visit_id0"] = start["visit_id"]
    start["visit_id1"] = start["next_visit"]
    start["year0"] = start["year"]
    start["year1"] = start["next_year"]
    start["CLSTR_ID"] = start["visit_id0"]
    start["interval_id"] = (
        start["site_id"].astype("string")
        + "|"
        + start["PLOT"].astype("string")
        + "|"
        + start["year0"].astype("string")
        + "|"
        + start["year1"].astype("string")
    )
    start["phf_end"] = start["end_phf"]
    start["TREE_NO"] = start["TREE_NO"].astype("int64")
    start["PLOT"] = start["PLOT"].astype("int64")

    tree_survival = start[
        list(BC_TREE_SURVIVAL_COLUMNS)
        + [
            "source",
            "site_id",
            "visit_id0",
            "visit_id1",
            "year0",
            "year1",
            "source_species",
            "species_resolution",
            "crown_class",
            "dbh_end",
            "phf_end",
            "prior_dbh",
            "prior_year",
            "growth",
        ]
    ].copy()
    tree_survival = tree_survival.sort_values(
        ["interval_id", "TREE_NO"], kind="mergesort"
    ).reset_index(drop=True)

    interval_stats = (
        start.groupby("interval_id", as_index=False)
        .agg(
            dead_ba=("dead_ba", "sum"),
            dead_tph=("dead_tph", "sum"),
            n_start_trees=("TREE_NO", "size"),
            n_dead_trees=("dead", "sum"),
            n_missing_trees=("known", lambda values: int((values == 0).sum())),
        )
    )
    intervals = intervals.merge(interval_stats, on="interval_id", how="left", validate="one_to_one")
    for column in ("dead_ba", "dead_tph"):
        intervals[column] = intervals[column].fillna(0.0).astype("float64")
    for column in ("n_start_trees", "n_dead_trees", "n_missing_trees"):
        intervals[column] = intervals[column].fillna(0).astype("int64")
    ba0 = intervals["ba0"].replace(0.0, np.nan)
    tph0 = intervals["tph0"].replace(0.0, np.nan)
    intervals["ba_mortality"] = intervals["dead_ba"] / ba0 / intervals["years"]
    intervals["tph_mortality"] = intervals["dead_tph"] / tph0 / intervals["years"]
    intervals["net_ba_rate"] = (intervals["next_ba0"] - intervals["ba0"]) / intervals["years"]
    intervals["gross_ba_rate"] = (
        intervals["next_ba0"] - intervals["ba0"] + intervals["dead_ba"]
    ) / intervals["years"]
    intervals["CLSTR_ID"] = intervals["visit_id"]
    intervals["visit_id0"] = intervals["visit_id"]
    intervals["visit_id1"] = intervals["next_visit"]
    intervals["year0"] = intervals["year"].astype("int64")
    intervals["year1"] = intervals["next_year"].astype("int64")
    intervals["PLOT"] = intervals["PLOT"].astype("int64")
    intervals["source"] = intervals["source"].astype("string")
    intervals = intervals.rename(columns={"site_id": "SITE_IDENTIFIER"})
    intervals = intervals[
        list(BC_INTERVAL_COLUMNS)
        + [
            "source",
            "CLSTR_ID",
            "visit_id0",
            "visit_id1",
            "year1",
            "n_start_trees",
            "n_dead_trees",
            "n_missing_trees",
            "ba_mortality",
            "net_ba_rate",
            "gross_ba_rate",
        ]
    ]
    intervals = intervals.sort_values(
        ["SITE_IDENTIFIER", "PLOT", "year0", "year1"], kind="mergesort"
    ).reset_index(drop=True)

    stats = {
        "interval_count": int(len(intervals)),
        "tree_survival_count": int(len(tree_survival)),
        "tree_survival_dead": int(tree_survival["dead"].sum()),
        "tree_survival_known": int(tree_survival["known"].sum()),
        "tree_survival_missing": int((tree_survival["known"] == 0).sum()),
        "interval_years_min": int(intervals["years"].min()) if not intervals.empty else None,
        "interval_years_max": int(intervals["years"].max()) if not intervals.empty else None,
    }
    return intervals, tree_survival, stats


def species_crosswalk(trees: pd.DataFrame, profile: VariantProfile = BC_PROFILE) -> pd.DataFrame:
    """Build the observed source-code to BC JSP crosswalk.

    The counts are over every retained tree row, so a code used only by a dead
    tree is still visible.  ``n_calibration_rows`` counts rows that pass the
    v0 tree filter used for the consumer tables (known status, DBH, positive
    expansion factor).
    """
    if trees.empty:
        return pd.DataFrame(columns=BC_SPECIES_COLUMNS)
    frame = trees.copy()
    frame["species_code"] = _clean_code(frame["species_code"], upper=True)
    frame["_base"] = (
        frame["dbh_cm"].notna()
        & frame["expansion_factor"].gt(0)
        & frame["status"].isin(["live", "dead"])
    )
    frame["_ba"] = frame["ba"].fillna(0.0)
    summary = (
        frame.groupby("species_code", dropna=False, as_index=False)
        .agg(
            n_tree_rows=("tree_id", "size"),
            n_live_rows=("status", lambda values: int((values == "live").sum())),
            n_calibration_rows=("_base", "sum"),
            ba_m2ha_sum=("_ba", "sum"),
        )
        .rename(columns={"species_code": "source_species"})
    )
    rows: list[dict[str, object]] = []
    for record in summary.to_dict("records"):
        source = record["source_species"]
        resolution = classify_species(source, profile)
        rows.append(
            {
                "source_species": resolution.source_species or pd.NA,
                "fvs_jsp_code": resolution.fvs_jsp_code,
                "group": resolution.group,
                "resolution": resolution.resolution,
                "notes": resolution.note,
                "n_tree_rows": int(record["n_tree_rows"]),
                "n_live_rows": int(record["n_live_rows"]),
                "n_calibration_rows": int(record["n_calibration_rows"]),
                "ba_m2ha_sum": float(record["ba_m2ha_sum"]),
            }
        )
    out = pd.DataFrame(rows, columns=BC_SPECIES_COLUMNS)
    return out.sort_values("source_species", kind="mergesort", na_position="last").reset_index(
        drop=True
    )


def build_bc_tables(
    source: PlotSource,
    *,
    profile: VariantProfile = BC_PROFILE,
    tsa_lookup: Mapping[str, object] | pd.Series | None = None,
    include_unsupported: bool = False,
    min_dbh: float = 0.0,
    strict_species: bool = False,
) -> BcTables:
    """Convert one plot source to the BC consumer tables.

    Parameters
    ----------
    source : PlotSource
        The plot source.  It may expose several source labels; the BC adapter
        uses whichever labels it was constructed with.
    profile : VariantProfile, optional
        The variant profile.  Must be the implemented BC profile for now.
    tsa_lookup : mapping or pandas.Series, optional
        Site-id-indexed ``TSA_DESC`` enrichment.  Absent values stay null.
    include_unsupported : bool, optional
        Keep sites outside the twelve resolved BEC zones.  The default mirrors
        ``fresh_fvsbc.calibration.build_dataset`` and drops them.
    min_dbh : float, optional
        Lower DBH limit in cm.  Defaults to zero.
    strict_species : bool, optional
        Raise when a source species code used in the calibration tables has no
        BC JSP mapping.  The default keeps the unmapped rows explicit.

    Returns
    -------
    BcTables
        The three consumer tables, the crosswalk, counts, and warnings.

    Raises
    ------
    BcAdapterError
        If the profile is a stub, a required column is absent, a key is
        duplicated, or strict species mode encounters an unmapped code.
    """
    if profile.variant_id != "bc" or profile.implementation_status != "implemented":
        raise BcAdapterError(
            f"variant profile {profile.variant_id!r} is {profile.implementation_status!r}; "
            "only the BC profile has an adapter in this prototype"
        )
    if min_dbh < 0:
        raise BcAdapterError("min_dbh must not be negative")

    sites, site_counts = _site_frame(
        source.sites(), profile, include_unsupported=include_unsupported
    )
    selected_ids = set(sites["site_id"].astype(str))
    visits_raw = source.visits()
    visits_raw = visits_raw[
        _clean_id(visits_raw["site_id"]).isin(selected_ids)
    ].copy()
    visits = _visit_frame(visits_raw, sites)
    tsa = _tsa_series(tsa_lookup)
    if not tsa.empty:
        visits = visits.merge(
            tsa.rename("TSA_DESC"),
            left_on="site_id",
            right_index=True,
            how="left",
        )
    else:
        visits["TSA_DESC"] = pd.Series(pd.NA, index=visits.index, dtype="string")

    trees_raw = source.trees(columns=BC_TREE_INPUT_COLUMNS)
    trees_raw = trees_raw[
        _clean_id(trees_raw["site_id"]).isin(selected_ids)
    ].copy()
    trees = _tree_frame(trees_raw, visits)

    status_ok = trees["status"].isin(["live", "dead"])
    after_status = trees.loc[status_ok]
    missing_dbh = after_status["dbh_cm"].isna()
    after_dbh = after_status.loc[~missing_dbh]
    missing_weight = ~(after_dbh["expansion_factor"] > 0)
    after_weight = after_dbh.loc[~missing_weight]
    below_min = after_weight["dbh_cm"] < min_dbh
    base = after_weight.loc[~below_min].copy()
    live_base = base.loc[base["status"].eq("live")].copy()

    plot_visits = _plot_visits(live_base, visits)
    intervals, tree_survival, interval_counts = _intervals_and_survival(
        plot_visits, live_base, trees, visits, profile
    )
    crosswalk = species_crosswalk(trees, profile)

    unmapped = crosswalk.loc[crosswalk["resolution"].eq("unmapped")]
    unmapped_used = unmapped.loc[unmapped["n_calibration_rows"].gt(0)]
    if strict_species and not unmapped_used.empty:
        codes = ", ".join(unmapped_used["source_species"].astype(str))
        raise BcAdapterError(
            "strict_species is set and these source codes used in the "
            f"calibration rows have no BC JSP mapping: {codes}"
        )

    multi_plot_visits = int(
        plot_visits.groupby("visit_id", sort=False)["PLOT"].nunique().gt(1).sum()
    )
    warnings: list[str] = []
    if tsa.empty:
        warnings.append(
            "TSA_DESC is null: no raw FAIB header was supplied or found; "
            "the harmonized v0 site table does not carry it."
        )
    if (plot_visits["volume_basis"] == "cluster_multi_plot_not_apportioned").any():
        warnings.append(
            "Volume is null for visits that contain more than one plot; the "
            "harmonized v0 volume aggregate cannot be split without per-tree "
            "volume, and no allocation is invented."
        )
    if not unmapped_used.empty:
        warnings.append(
            f"{len(unmapped_used)} source species code(s) used in calibration "
            "rows have no BC JSP mapping; their consumer 'species' value is null."
        )
    if include_unsupported:
        warnings.append(
            "include_unsupported=True: sites outside the twelve resolved BEC "
            "zones are retained; downstream FVS runs must reject them."
        )

    stats: dict[str, Any] = {
        **site_counts,
        "visits_input": int(len(visits_raw)),
        "visits_selected": int(len(visits)),
        "visit_rows": int(len(plot_visits)),
        "visit_units": int(plot_visits["visit_id"].nunique()),
        "multi_plot_visit_units": multi_plot_visits,
        "trees_input": int(len(trees)),
        "trees_base": int(len(base)),
        "trees_live_base": int(len(live_base)),
        "trees_dropped_status": int((~status_ok).sum()),
        "trees_dropped_missing_dbh": int(missing_dbh.sum()),
        "trees_dropped_missing_weight": int(missing_weight.sum()),
        "trees_dropped_min_dbh": int(below_min.sum()),
        "species_crosswalk_rows": int(len(crosswalk)),
        "species_direct": int(crosswalk["resolution"].eq("direct").sum()),
        "species_donor": int(crosswalk["resolution"].eq("donor").sum()),
        "species_unmapped": int(crosswalk["resolution"].eq("unmapped").sum()),
        "species_unmapped_calibration_rows": int(unmapped_used["n_calibration_rows"].sum()),
        "volume_null_multi_plot_visit_units": multi_plot_visits,
        "tsa_sites_matched": int(
            visits.loc[visits["TSA_DESC"].notna(), "site_id"].nunique()
        ),
        **interval_counts,
    }
    return BcTables(
        visits=_ordered_visits(plot_visits),
        intervals=intervals,
        tree_survival=tree_survival,
        species=crosswalk,
        stats=stats,
        warnings=tuple(warnings),
    )


def _ordered_visits(frame: pd.DataFrame) -> pd.DataFrame:
    """Return the visit table in the declared consumer column order."""
    frame = frame.rename(columns={"site_id": "SITE_IDENTIFIER", "visit_id": "CLSTR_ID"})
    extra = [
        "source",
        "visit_number",
        "n_trees",
        "top_height_m",
        "plot_design",
        "volume_basis",
    ]
    return frame[list(BC_VISIT_COLUMNS) + extra]


def _write_frame(frame: pd.DataFrame, path: Path, *, gzip: bool = False) -> None:
    """Write a CSV with LF line endings and deterministic gzip metadata."""
    kwargs: dict[str, object] = {"index": False, "lineterminator": "\n"}
    if gzip:
        kwargs["compression"] = {"method": "gzip", "mtime": 0}
    frame.to_csv(path, **kwargs)


def build_bc_dataset(
    source: PlotSource,
    out_dir: str | Path,
    *,
    profile: VariantProfile = BC_PROFILE,
    tsa_lookup: Mapping[str, object] | pd.Series | None = None,
    include_unsupported: bool = False,
    min_dbh: float = 0.0,
    strict_species: bool = False,
    raw_inputs: Mapping[str, Path] | None = None,
) -> BcDataset:
    """Build and write the BC consumer dataset.

    Parameters are the same as :func:`build_bc_tables`, plus:
    out_dir : str or Path
        Destination directory.  ``visits.csv``, ``intervals.csv``,
        ``species_crosswalk_fvsbc.csv``, and ``manifest.json`` are plain text;
        ``tree_survival.csv.gz`` is gzipped.
    raw_inputs : mapping, optional
        Extra input records (for example the raw FAIB header) to include in the
        manifest.  Each value is a path; the manifest records bytes and SHA-256.
    """
    tables = build_bc_tables(
        source,
        profile=profile,
        tsa_lookup=tsa_lookup,
        include_unsupported=include_unsupported,
        min_dbh=min_dbh,
        strict_species=strict_species,
    )
    destination = Path(out_dir)
    destination.mkdir(parents=True, exist_ok=True)

    visits = tables.visits
    intervals = tables.intervals
    tree_survival = tables.tree_survival
    species = tables.species

    _write_frame(visits, destination / "visits.csv")
    _write_frame(intervals, destination / "intervals.csv")
    _write_frame(tree_survival, destination / "tree_survival.csv.gz", gzip=True)
    _write_frame(species, destination / "species_crosswalk_fvsbc.csv")

    outputs: dict[str, dict[str, object]] = {}
    for name in (
        "visits.csv",
        "intervals.csv",
        "tree_survival.csv.gz",
        "species_crosswalk_fvsbc.csv",
    ):
        path = destination / name
        outputs[name] = {
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }

    inputs: dict[str, dict[str, object]] = {}
    file_records = getattr(source, "file_records", None)
    if callable(file_records):
        inputs.update(file_records())
    if raw_inputs:
        for name, path in raw_inputs.items():
            resolved = Path(path)
            if resolved.is_file():
                inputs[name] = {
                    "bytes": resolved.stat().st_size,
                    "sha256": _sha256(resolved),
                }

    manifest: dict[str, Any] = {
        "variant": profile.variant_id,
        "variant_name": profile.display_name,
        "fvs_variant": profile.fvs_variant,
        "site_key_kind": profile.site_key_kind,
        "tree_list_format": profile.tree_list_format,
        "species_scheme": profile.species_scheme,
        "built_utc": _utc_now(),
        "inputs": inputs,
        "outputs": outputs,
        "rows": {
            "visits": int(len(visits)),
            "intervals": int(len(intervals)),
            "tree_survival": int(len(tree_survival)),
            "species_crosswalk": int(len(species)),
        },
        "parameters": {
            "include_unsupported": include_unsupported,
            "min_dbh": float(min_dbh),
            "strict_species": strict_species,
            "min_interval_years": MIN_INTERVAL_YEARS,
            "max_interval_years": MAX_INTERVAL_YEARS,
            "volume_policy": (
                "source visit volume on single-plot visits; null on multi-plot "
                "visits because no defensible allocation exists"
            ),
            "end_status_policy": (
                "all v0 tree statuses are used for the end lookup; a dead "
                "endpoint with null DBH remains a known death"
            ),
            "species_policy": (
                "15 BC JSP codes plus 7 donor aliases; unmapped codes stay "
                "explicit and consumer species is null"
            ),
        },
        "stats": tables.stats,
        "warnings": list(tables.warnings),
    }
    (destination / "manifest.json").write_text(
        json.dumps(manifest, indent=2, default=str) + "\n", encoding="utf-8"
    )
    return BcDataset(
        directory=destination,
        visits=visits,
        intervals=intervals,
        tree_survival=tree_survival,
        species=species,
        manifest=manifest,
    )
