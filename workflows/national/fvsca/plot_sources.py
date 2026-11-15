"""Plot-source adapters for the FVS-CA orchestrator.

A :class:`PlotSource` hides where a plot came from.  The rest of the pipeline
asks for sites, visits, trees, and intervals and receives the harmonized v0
column contract.  The first implementation, :class:`HarmonizedV0Source`, reads
``data/derived/fvsca_calibration/v0/`` and filters by source label.

The v0 schema is defined in
``workflows/national/plot_harmonization/schema.py``.  This module does not
reinterpret it: it normalises identifier dtypes and applies the requested
source filter, nothing else.

Raw adapters (FAIB directly, MAGPlot directly, and so on) can implement the
same protocol later.  The BC consumer adapter is written against the protocol,
not against the v0 files, so a raw adapter can be dropped in without changing
the consumer.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Protocol, runtime_checkable

import pandas as pd

__all__ = [
    "HarmonizedV0Source",
    "PlotSource",
    "PlotSourceError",
    "SOURCE_LABELS",
    "V0_REQUIRED_FILES",
    "normalise_sources",
]

#: The four tables the harmonized v0 source needs.
V0_REQUIRED_FILES: tuple[str, ...] = (
    "sites.csv",
    "visits.csv",
    "trees.csv.gz",
    "intervals.csv.gz",
)

#: The v0 source labels.  Kept here so a source filter can be validated before
#: any file is opened.
SOURCE_LABELS: tuple[str, ...] = (
    "faib_psp",
    "faib_non_psp",
    "magplot",
    "magplot_wfs",
    "qc_pep",
    "nl_fal",
)

_ID_COLUMNS: tuple[str, ...] = ("source", "site_id", "visit_id", "tree_id")


class PlotSourceError(ValueError):
    """A plot source that cannot satisfy the protocol."""


def normalise_sources(sources: str | Iterable[str] | None) -> tuple[str, ...]:
    """Return a validated, de-duplicated tuple of source labels.

    ``None`` means every v0 source.  A comma-separated string is accepted so
    the CLI can pass ``--source faib_psp,faib_non_psp`` without extra parsing.
    Unknown labels fail immediately and name the accepted set.
    """
    if sources is None:
        return SOURCE_LABELS
    items = [sources] if isinstance(sources, str) else list(sources)
    labels: list[str] = []
    for item in items:
        for part in str(item).split(","):
            label = part.strip().lower()
            if not label:
                continue
            if label not in SOURCE_LABELS:
                raise PlotSourceError(
                    f"unknown plot source {label!r}; expected one of "
                    f"{', '.join(SOURCE_LABELS)}"
                )
            if label not in labels:
                labels.append(label)
    if not labels:
        raise PlotSourceError("at least one plot source label is required")
    return tuple(labels)


def _clean_id(series: pd.Series) -> pd.Series:
    """Normalise an identifier to a stripped nullable string."""
    out = series.astype("string").str.strip()
    out = out.str.replace(r"\.0$", "", regex=True)
    return out.replace({"": pd.NA, "nan": pd.NA, "None": pd.NA, "<NA>": pd.NA})


@runtime_checkable
class PlotSource(Protocol):
    """The contract every plot source implements.

    Implementations return the harmonized v0 columns.  ``trees`` may read a
    subset of columns, which keeps a national call from materialising every
    source at once.
    """

    source_labels: tuple[str, ...]

    def sites(self) -> pd.DataFrame:
        """One row per source site."""

    def visits(self) -> pd.DataFrame:
        """One row per source measurement event."""

    def trees(self, columns: Sequence[str] | None = None) -> pd.DataFrame:
        """One row per tree measurement."""

    def intervals(self) -> pd.DataFrame:
        """One row per consecutive tree observation."""


class HarmonizedV0Source:
    """Read the harmonized v0 calibration extract.

    Parameters
    ----------
    root : str or Path
        The directory that holds ``sites.csv``, ``visits.csv``,
        ``trees.csv.gz``, and ``intervals.csv.gz``.
    sources : str or iterable of str, optional
        Source labels to retain.  ``None`` keeps all six labels.
    chunksize : int, optional
        Rows per chunk when reading the tree table.  The default keeps peak
        memory bounded on the full national file.

    Raises
    ------
    PlotSourceError
        If the directory or a required table is missing, or a requested source
        label is not part of the v0 schema.
    """

    def __init__(
        self,
        root: str | Path,
        sources: str | Iterable[str] | None = None,
        *,
        chunksize: int = 1_000_000,
    ) -> None:
        self.root = Path(root)
        if not self.root.is_dir():
            raise PlotSourceError(f"plot source directory does not exist: {self.root}")
        missing = [name for name in V0_REQUIRED_FILES if not (self.root / name).is_file()]
        if missing:
            raise PlotSourceError(
                f"{self.root} is missing {', '.join(missing)}; "
                "the harmonized v0 extract is required"
            )
        if chunksize <= 0:
            raise PlotSourceError("chunksize must be positive")
        self.source_labels = normalise_sources(sources)
        self.chunksize = chunksize

    def _read(self, name: str, **kwargs: object) -> pd.DataFrame:
        """Read one non-tree table, normalise identifiers, and filter sources."""
        frame = pd.read_csv(self.root / name, low_memory=False, **kwargs)
        for column in _ID_COLUMNS:
            if column in frame.columns:
                frame[column] = _clean_id(frame[column])
        return self._filter(frame)

    def _filter(self, frame: pd.DataFrame) -> pd.DataFrame:
        """Keep the selected source rows, failing if the label column is absent."""
        if "source" not in frame.columns:
            raise PlotSourceError("plot table has no 'source' column")
        keep = frame["source"].astype("string").isin(self.source_labels)
        return frame.loc[keep].reset_index(drop=True)

    def sites(self) -> pd.DataFrame:
        return self._read("sites.csv")

    def visits(self) -> pd.DataFrame:
        return self._read("visits.csv")

    def intervals(self) -> pd.DataFrame:
        return self._read("intervals.csv.gz")

    def trees(self, columns: Sequence[str] | None = None) -> pd.DataFrame:
        """Read the tree table in chunks and return the selected sources.

        ``source`` is always read because it is the filter.  If ``columns`` is
        supplied, the returned frame also carries ``source`` even when the
        caller did not list it.
        """
        usecols = None
        if columns is not None:
            requested = list(dict.fromkeys(columns))
            if "source" not in requested:
                requested.insert(0, "source")
            usecols = requested
        chunks: list[pd.DataFrame] = []
        for chunk in pd.read_csv(
            self.root / "trees.csv.gz",
            usecols=usecols,
            low_memory=False,
            chunksize=self.chunksize,
        ):
            for column in _ID_COLUMNS:
                if column in chunk.columns:
                    chunk[column] = _clean_id(chunk[column])
            selected = self._filter(chunk)
            if not selected.empty:
                chunks.append(selected)
        if not chunks:
            return pd.DataFrame(columns=usecols or [])
        return pd.concat(chunks, ignore_index=True)

    def file_records(self) -> dict[str, dict[str, object]]:
        """Return byte size and SHA-256 for each v0 input file."""
        records: dict[str, dict[str, object]] = {}
        for name in V0_REQUIRED_FILES:
            path = self.root / name
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(block)
            records[name] = {"bytes": path.stat().st_size, "sha256": digest.hexdigest()}
        return records
