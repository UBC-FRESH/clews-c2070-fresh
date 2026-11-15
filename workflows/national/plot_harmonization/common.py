"""Shared helpers for the national plot harmonization adapters."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import subprocess
import zipfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .schema import (
    INTERVAL_COLUMNS,
    MAX_INTERVAL_YEARS,
    MIN_INTERVAL_YEARS,
    SITE_COLUMNS,
    TREE_COLUMNS,
    VISIT_COLUMNS,
)


@dataclass
class SourceTables:
    """The three harmonized tables produced by one source adapter.

    Parameters
    ----------
    source : str
        The source label written to the ``source`` column.
    sites : pandas.DataFrame
        One row per site, in :data:`SITE_COLUMNS` order after finalization.
    visits : pandas.DataFrame
        One row per measurement event, in :data:`VISIT_COLUMNS` order after
        finalization.
    trees : pandas.DataFrame
        One row per tree measurement.  Carries the public ``TREE_COLUMNS``
        plus the internal columns needed for intervals.
    stats : dict
        Input and output counts, dropped-row reasons, and adapter settings.
    notes : list of str
        Human-readable mapping decisions and known issues.
    """

    source: str
    sites: pd.DataFrame
    visits: pd.DataFrame
    trees: pd.DataFrame
    stats: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def empty_frame(columns: Iterable[str]) -> pd.DataFrame:
    """Return an empty frame with the requested column order."""
    return pd.DataFrame({column: pd.Series(dtype="object") for column in columns})


def clean_id(values: pd.Series | Iterable[Any]) -> pd.Series:
    """Normalize a source identifier to a stripped nullable string.

    Numeric identifiers are converted without a trailing ``.0``; empty strings
    and literal sentinels become ``pandas.NA``.  The function is vectorized.
    """
    series = values if isinstance(values, pd.Series) else pd.Series(values)
    out = series.astype("string").str.strip()
    out = out.str.replace(r"\.0$", "", regex=True)
    return out.replace(
        {
            "": pd.NA,
            "nan": pd.NA,
            "NaN": pd.NA,
            "None": pd.NA,
            "<NA>": pd.NA,
            "-1": pd.NA,
        }
    )


def clean_code(values: pd.Series | Iterable[Any], *, upper: bool = False) -> pd.Series:
    """Normalize a code field without treating ``-1`` as missing."""
    series = values if isinstance(values, pd.Series) else pd.Series(values)
    out = series.astype("string").str.strip()
    out = out.str.replace(r"\.0$", "", regex=True)
    out = out.replace({"": pd.NA, "nan": pd.NA, "None": pd.NA, "<NA>": pd.NA})
    if upper:
        out = out.str.upper()
    return out


def numeric(values: pd.Series | Iterable[Any]) -> pd.Series:
    """Convert values to float, coercing non-numeric entries to NA."""
    series = values if isinstance(values, pd.Series) else pd.Series(values)
    return pd.to_numeric(series, errors="coerce")


def positive(values: pd.Series | Iterable[Any]) -> pd.Series:
    """Return numeric values greater than zero, otherwise NA."""
    out = numeric(values)
    return out.where(out > 0)


def non_negative(values: pd.Series | Iterable[Any]) -> pd.Series:
    """Return numeric values greater than or equal to zero, otherwise NA."""
    out = numeric(values)
    return out.where(out >= 0)


def map_status(values: pd.Series, mapping: Mapping[str, str]) -> pd.Series:
    """Map source status codes to ``live``/``dead``/``missing``.

    Unknown and null codes become ``missing``.  Matching is case-insensitive
    and whitespace-stripped.
    """
    normalized = values.astype("string").str.strip()
    normalized = normalized.str.replace(r"\.0$", "", regex=True).str.upper()
    lookup = {str(key).strip().upper(): value for key, value in mapping.items()}
    return normalized.map(lookup).fillna("missing").astype("string")


def first_non_empty(frame: pd.DataFrame, columns: list[str]) -> pd.Series:
    """Return the first non-empty value across ``columns`` for each row."""
    present = [column for column in columns if column in frame.columns]
    if not present:
        return pd.Series(pd.NA, index=frame.index, dtype="string")
    block = frame[present].replace({"": pd.NA, "nan": pd.NA, "-1": pd.NA})
    return block.bfill(axis=1).iloc[:, 0].astype("string")


def _mode_or_na(values: pd.Series) -> Any:
    values = values.dropna()
    if values.empty:
        return pd.NA
    return values.mode().iloc[0]


def mark_remeasured(trees: pd.DataFrame) -> pd.Series:
    """Mark trees observed at more than one measurement event."""
    if trees.empty:
        return pd.Series(dtype=bool)
    visits_per_tree = trees.groupby(
        ["source", "site_id", "tree_id"], sort=False
    )["visit_id"].transform("nunique")
    return visits_per_tree.gt(1)


def compute_visit_metrics(trees: pd.DataFrame) -> pd.DataFrame:
    """Aggregate tree rows to visit-level stand metrics.

    ``n_trees`` counts every retained tree record.  Basal area and trees per
    hectare use only live trees that carry a positive expansion factor.  A
    visit with live tally trees but a missing expansion factor gets a null
    BA/TPH rather than a silently low value.
    """
    if trees.empty:
        return empty_frame(
            [
                "source",
                "site_id",
                "visit_id",
                "n_trees",
                "ba_m2ha",
                "tph",
                "qmd_cm",
                "top_height_m",
                "volume_m3ha",
            ]
        )

    frame = trees.copy()
    frame["_live"] = frame["status"].eq("live")
    frame["_tally"] = frame["_tally"].fillna(False).astype(bool)
    frame["_live_tally"] = frame["_live"] & frame["_tally"]
    frame["_live_height"] = frame["height_m"].where(frame["_live"])
    frame["_live_ba"] = frame["_ba_m2ha"].where(frame["_live_tally"])
    frame["_live_tph"] = frame["expansion_factor"].where(frame["_live_tally"])
    frame["_live_vol"] = frame["_volume_m3ha"].where(frame["_live_tally"])
    frame["_missing_ba"] = frame["_live_tally"] & frame["_ba_m2ha"].isna()
    frame["_missing_tph"] = frame["_live_tally"] & frame["expansion_factor"].isna()

    grouped = frame.groupby(["source", "site_id", "visit_id"], sort=False)
    metrics = grouped.agg(
        n_trees=("tree_id", "size"),
        n_live=("_live", "sum"),
        n_live_tally=("_live_tally", "sum"),
        ba_sum=("_live_ba", "sum"),
        ba_count=("_live_ba", "count"),
        tph_sum=("_live_tph", "sum"),
        tph_count=("_live_tph", "count"),
        top_height_m=("_live_height", "max"),
        volume_sum=("_live_vol", "sum"),
        volume_count=("_live_vol", "count"),
        missing_ba=("_missing_ba", "sum"),
        missing_tph=("_missing_tph", "sum"),
    )
    metrics = metrics.reset_index()

    no_live = metrics["n_live"].eq(0)
    no_tally = metrics["n_live_tally"].eq(0)
    ba = metrics["ba_sum"].astype(float)
    tph = metrics["tph_sum"].astype(float)
    ba = ba.where(metrics["missing_ba"].eq(0) | no_tally)
    tph = tph.where(metrics["missing_tph"].eq(0) | no_tally)
    ba = ba.mask(no_tally & ~no_live)
    tph = tph.mask(no_tally & ~no_live)
    ba = ba.mask(no_live, 0.0)
    tph = tph.mask(no_live, 0.0)

    metrics["ba_m2ha"] = ba
    metrics["tph"] = tph
    with np.errstate(divide="ignore", invalid="ignore"):
        qmd = 100.0 * np.sqrt(ba / tph.replace(0.0, np.nan) * (4.0 / np.pi))
    metrics["qmd_cm"] = qmd
    metrics["volume_m3ha"] = metrics["volume_sum"].where(
        metrics["volume_count"] > 0
    )
    return metrics[
        [
            "source",
            "site_id",
            "visit_id",
            "n_trees",
            "ba_m2ha",
            "tph",
            "qmd_cm",
            "top_height_m",
            "volume_m3ha",
        ]
    ]


def compute_intervals(trees: pd.DataFrame) -> pd.DataFrame:
    """Build consecutive-tree-measurement intervals.

    An interval is retained only when the tree was live at the start, its start
    DBH is known, its end status is known, a live end has a measured DBH, the
    two observations are on consecutive source visits, and the elapsed time is
    within the fresh-fvsbc 2-40 year window.  A dead end may have a null DBH
    because downed or broken trees cannot always be measured.  Missing end
    status is not treated as dead.
    """
    if trees.empty:
        return empty_frame(INTERVAL_COLUMNS)

    frame = trees[
        [
            "source",
            "site_id",
            "tree_id",
            "visit_id",
            "_visit_order",
            "_year",
            "dbh_cm",
            "status",
        ]
    ].copy()
    frame = frame[
        frame["_visit_order"].notna()
        & frame["_year"].notna()
        & frame["visit_id"].notna()
        & frame["status"].isin(["live", "dead"])
    ]
    frame = frame.drop_duplicates(
        ["source", "site_id", "tree_id", "_visit_order"], keep="first"
    )
    frame = frame.sort_values(
        ["source", "site_id", "tree_id", "_visit_order"], kind="mergesort"
    )
    grouped = frame.groupby(["source", "site_id", "tree_id"], sort=False)
    frame["_prev_order"] = grouped["_visit_order"].shift(1)
    frame["_prev_year"] = grouped["_year"].shift(1)
    frame["_prev_dbh"] = grouped["dbh_cm"].shift(1)
    frame["_prev_status"] = grouped["status"].shift(1)
    frame["_prev_visit"] = grouped["visit_id"].shift(1)

    years = frame["_year"] - frame["_prev_year"]
    start_has_dbh = frame["_prev_dbh"].notna()
    end_has_dbh = frame["dbh_cm"].notna() | frame["status"].eq("dead")
    keep = (
        frame["_prev_status"].eq("live")
        & start_has_dbh
        & frame["status"].isin(["live", "dead"])
        & end_has_dbh
        & (frame["_visit_order"] - frame["_prev_order"]).eq(1)
        & years.between(MIN_INTERVAL_YEARS, MAX_INTERVAL_YEARS, inclusive="both")
    )
    frame = frame.loc[keep].copy()
    frame["visit_id0"] = frame["_prev_visit"].astype("string")
    frame["visit_id1"] = frame["visit_id"].astype("string")
    frame["year0"] = frame["_prev_year"].astype("int64")
    frame["year1"] = frame["_year"].astype("int64")
    frame["years"] = years.loc[keep].astype("int64")
    frame["dbh0_cm"] = frame["_prev_dbh"].astype(float)
    frame["dbh1_cm"] = frame["dbh_cm"].astype(float)
    frame["status_end"] = frame["status"].astype("string")
    frame["dead"] = frame["status_end"].eq("dead").astype("int64")
    frame["known"] = frame["status_end"].isin(["live", "dead"]).astype("int64")
    frame["exposure_years"] = (frame["years"] * frame["known"]).astype(float)
    return frame[list(INTERVAL_COLUMNS)].reset_index(drop=True)


def finalize_sites(
    sites: pd.DataFrame,
    visits: pd.DataFrame,
    trees: pd.DataFrame,
) -> pd.DataFrame:
    """Attach visit/tree summaries to the site metadata table."""
    if sites.empty:
        return empty_frame(SITE_COLUMNS)

    base = sites.copy()
    base["source"] = base["source"].astype("string")
    base["site_id"] = clean_id(base["site_id"])

    if not visits.empty:
        visit_stats = visits.copy()
        visit_stats["year"] = numeric(visit_stats["year"])
        grouped = visit_stats.groupby(["source", "site_id"], sort=False)
        summary = grouped.agg(
            n_visits=("visit_id", "nunique"),
            first_year=("year", "min"),
            last_year=("year", "max"),
            summary_site_index=("site_index", "median"),
            summary_site_index_species=("_site_index_species", _mode_or_na),
            summary_plot_area_ha=("_plot_area_ha", "median"),
            summary_baf=("_baf", "median"),
            summary_plot_design=("_plot_design", _mode_or_na),
        ).reset_index()
    else:
        summary = pd.DataFrame(
            columns=[
                "source",
                "site_id",
                "n_visits",
                "first_year",
                "last_year",
                "summary_site_index",
                "summary_site_index_species",
                "summary_plot_area_ha",
                "summary_baf",
                "summary_plot_design",
            ]
        )

    if not trees.empty:
        tree_counts = (
            trees.groupby(["source", "site_id"], sort=False)
            .size()
            .rename("n_trees")
            .reset_index()
        )
    else:
        tree_counts = pd.DataFrame(columns=["source", "site_id", "n_trees"])

    base = base.drop(
        columns=[
            "first_year",
            "last_year",
            "n_visits",
            "n_trees",
        ],
        errors="ignore",
    )
    base = base.merge(summary, on=["source", "site_id"], how="left")
    base = base.merge(tree_counts, on=["source", "site_id"], how="left")
    base["n_visits"] = base["n_visits"].fillna(0).astype("int64")
    base["n_trees"] = base["n_trees"].fillna(0).astype("int64")
    for target, summary_column in (
        ("site_index", "summary_site_index"),
        ("site_index_species", "summary_site_index_species"),
        ("plot_area_ha", "summary_plot_area_ha"),
        ("baf", "summary_baf"),
        ("plot_design", "summary_plot_design"),
    ):
        if target not in base.columns:
            base[target] = pd.NA
        if summary_column in base.columns:
            base[target] = base[summary_column].where(
                base[summary_column].notna(), base[target]
            )
            base = base.drop(columns=[summary_column])
    for column in SITE_COLUMNS:
        if column not in base.columns:
            base[column] = pd.NA
    return base[list(SITE_COLUMNS)].reset_index(drop=True)


def finalize_visits(visits: pd.DataFrame, trees: pd.DataFrame) -> pd.DataFrame:
    """Attach tree-derived stand metrics to the visit table."""
    if visits.empty:
        return empty_frame(VISIT_COLUMNS)

    base = visits.copy()
    metrics = compute_visit_metrics(trees)
    if not metrics.empty:
        base = base.merge(
            metrics, on=["source", "site_id", "visit_id"], how="left"
        )
    else:
        for column in (
            "n_trees",
            "ba_m2ha",
            "tph",
            "qmd_cm",
            "top_height_m",
            "volume_m3ha",
        ):
            base[column] = pd.NA

    base["n_trees"] = base["n_trees"].fillna(0).astype("int64")
    empty_visit = base["n_trees"].eq(0)
    base.loc[empty_visit, "ba_m2ha"] = base.loc[empty_visit, "ba_m2ha"].fillna(0.0)
    base.loc[empty_visit, "tph"] = base.loc[empty_visit, "tph"].fillna(0.0)
    for column in VISIT_COLUMNS:
        if column not in base.columns:
            base[column] = pd.NA
    return base[list(VISIT_COLUMNS)].reset_index(drop=True)


def sha256_file(path: Path) -> str:
    """Return the SHA-256 digest of a file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def file_record(path: Path, *, relative_to: Path | None = None) -> dict[str, Any]:
    """Return a manifest record for one file.

    Paths are stored relative to the repository or output directory; absolute
    machine paths are deliberately not written into the manifest.
    """
    display = path.name
    if relative_to is not None:
        try:
            display = str(path.relative_to(relative_to))
        except ValueError:
            try:
                display = str(path.resolve().relative_to(relative_to.resolve()))
            except ValueError:
                display = path.name
    return {
        "path": display,
        "bytes": int(path.stat().st_size),
        "sha256": sha256_file(path),
    }


def zip_member_record(zip_path: Path, member: str) -> dict[str, Any]:
    """Return a manifest record for one member of a ZIP archive."""
    with zipfile.ZipFile(zip_path) as archive:
        info = archive.getinfo(member)
        digest = hashlib.sha256()
        with archive.open(member) as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    return {
        "path": f"{zip_path.name}:{member}",
        "bytes": int(info.file_size),
        "sha256": digest.hexdigest(),
        "compressed_bytes": int(info.compress_size),
    }


def git_version(repo: Path) -> dict[str, Any]:
    """Return the current commit and dirty state, if git is available."""
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        return {"commit": commit, "dirty": bool(status.strip())}
    except (OSError, subprocess.CalledProcessError):
        return {"commit": None, "dirty": None}


def json_default(value: Any) -> Any:
    """JSON serializer for numpy/pandas scalars."""
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    return str(value)


def dump_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Write a manifest/JSON document with stable formatting."""
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=False, default=json_default) + "\n",
        encoding="utf-8",
    )


class _DeterministicGzipWriter:
    """Context manager returning a text handle over a pinned-mtime gzip file."""

    def __init__(self, path: Path):
        self._raw = path.open("wb")
        self._gzip = gzip.GzipFile(
            filename="", mode="wb", fileobj=self._raw, mtime=0
        )
        self._text = io.TextIOWrapper(
            self._gzip, encoding="utf-8", newline=""
        )

    def __enter__(self):
        return self._text

    def __exit__(self, exc_type, exc, tb):
        self._text.close()
        self._raw.close()
        return False


def open_gzip_writer(path: Path):
    """Open a deterministic gzip text handle for streaming CSV output.

    The gzip header mtime is pinned to zero so two builds from the same inputs
    produce byte-identical archives (the manifest hashes are then meaningful).
    """
    return _DeterministicGzipWriter(path)


def read_csv_from_zip(
    zip_path: Path, member: str, **kwargs: Any
) -> pd.DataFrame:
    """Read a CSV member from a ZIP without extracting it first."""
    with zipfile.ZipFile(zip_path) as archive:
        with archive.open(member) as handle:
            return pd.read_csv(handle, **kwargs)


def ensure_gpkg(
    zip_path: Path, work_dir: Path, member: str = "PEP.gpkg"
) -> Path:
    """Extract a GeoPackage member once, returning the extracted path."""
    target = work_dir / member
    if target.is_file() and target.stat().st_size > 0:
        return target
    work_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as archive:
        archive.extract(member, work_dir)
    return target


def drop_reason(stats: dict[str, Any], reason: str, count: int) -> None:
    """Increment a dropped-row counter under a stable reason key."""
    if count:
        stats.setdefault("dropped", {})
        stats["dropped"][reason] = int(stats["dropped"].get(reason, 0) + count)
