"""Validate the TSA24 pilot industrial sorts against TIPSY industrial-log curves.

The full-population BC PSP TSA24 bucking product carries dimension-only
``sort_code`` values (``D125L63``, ``D125L51``, ...).  This module compares
those sort shares with the TIPSY 4.7 BTC /TSR ``Industrial_Logs_*`` curves
compiled for the same managed AUs and ages.  It is an **industrial sort
recovery** check, not an official BC log-grade validation: the bucker has no
defect, knot, sweep, rot, or product-quality input, and every plot result is
labelled ``grade_status=not_assigned``.

The join is the pilot benchmark join: BEC + supported leading species ->
managed FEMIC AU stratum -> FEMIC site-index class -> managed AU -> TIPSY
curve interpolated at the FAIB stand age.  A documented fallback to the
species-mean TIPSY curve is reported separately; it is never mixed into the
primary AU-matched metrics.

Fail-closed rules
-----------------
* Any pilot sort code outside the 20 expected TIPSY industrial codes (the
  ``D125L50`` -> ``D125L5`` alias is the only accepted spelling difference)
  raises :class:`SortValidationError`.
* Any unexpected ``Industrial_Logs_*`` column, missing expected column, or
  negative/non-finite curve value raises.
* TVD and overlap are only computed from normalised, non-negative shares.

No threshold is invented.  A pass/fail gate is reported only when a
pre-existing tolerance is supplied explicitly; otherwise the output is
``not_assessed_no_pre_registered_tolerance``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .tsa24_grade_curves import INDUSTRIAL_NAMES
from .validate_tsa24 import (
    _assign_si_level_from_join,
    _au_id_from_join,
    _best_au_stratum,
    _load_au_join,
)

__all__ = [
    "GRADE_STATUS",
    "INDUSTRIAL_ORDER",
    "LABEL",
    "SortValidationError",
    "build_plot_attributes",
    "build_plot_comparison",
    "compute_metrics",
    "generate_validation",
    "normalise_sort_code",
    "overlap",
    "tvd",
]

REPO_ROOT = Path(__file__).resolve().parents[3]
LABEL = "industrial_sort_validation"
GRADE_STATUS = "not_assigned"

DEFAULT_LOGS = (
    REPO_ROOT
    / "data"
    / "derived"
    / "national"
    / "bucking"
    / "bc_psp_validation_v0"
    / "bucked_logs.csv"
)
DEFAULT_TREES = (
    REPO_ROOT
    / "data"
    / "derived"
    / "national"
    / "bucking"
    / "bc_psp_validation_v0"
    / "selected_trees.csv"
)
DEFAULT_COVERAGE = (
    REPO_ROOT
    / "data"
    / "derived"
    / "national"
    / "bucking"
    / "tsa24_grade_curves"
    / "au_grade_coverage.csv"
)
DEFAULT_TIPSY_SORTS = (
    REPO_ROOT
    / "data"
    / "derived"
    / "national"
    / "bucking"
    / "tsa24_grade_curves"
    / "managed_au_industrial_sorts.csv"
)
FALLBACK_TIPSY_SORTS = (
    REPO_ROOT
    / "data"
    / "derived"
    / "national"
    / "bucking"
    / "tsa24_sort_validation_v0"
    / "tipsy_industrial_sorts.csv"
)
DEFAULT_PILOT_BENCHMARK = (
    REPO_ROOT
    / "data"
    / "derived"
    / "national"
    / "bucking"
    / "tsa24_pilot"
    / "grade_benchmark.csv"
)
DEFAULT_OUT_DIR = (
    REPO_ROOT / "data" / "derived" / "national" / "bucking" / "tsa24_sort_validation_v0"
)

INDUSTRIAL_ORDER: tuple[str, ...] = tuple(
    name.removeprefix("Industrial_Logs_") for name in INDUSTRIAL_NAMES
)

#: The pilot rule set spells the 5.0 m pulpwood sort ``D125L50``; TIPSY uses
#: ``D125L5``.  The mapping is explicit so a future rule rename fails visibly.
SORT_ALIASES: Mapping[str, str] = {"D125L50": "D125L5"}

#: Species without a nemora/Kozak taper in the source population.  They are
#: excluded from the leading-species token so the join does not follow a stem
#: that the bucker could not process.
UNSUPPORTED_SPECIES: frozenset[str] = frozenset(
    {"W", "EW", "DG", "DM", "MR", "XC", "M"}
)

#: Leading-species -> TIPSY species curve for the documented fallback.
SPECIES_TO_CURVE: Mapping[str, str] = {
    "SW": "SW",
    "SE": "SE",
    "BL": "BL",
    "S": "SW",
    "SB": "SW",
    "SX": "SW",
    "PL": "PLI",
    "PLI": "PLI",
    "PLC": "PLI",
    "FD": "FDI",
    "FDI": "FDI",
    "AT": "AT",
    "CW": "CW",
    "HW": "HWI",
    "HWI": "HWI",
}

_SHARE_TOL = 1e-6


class SortValidationError(RuntimeError):
    """Raised when an input violates the industrial-sort contract."""


# ---------------------------------------------------------------------------
# Small utilities


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, float_format="%.8f", lineterminator="\n")


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _finite_interp(
    x: float,
    xs: np.ndarray,
    ys: np.ndarray,
    *,
    left: float | None = None,
    right: float | None = None,
) -> float:
    finite = np.isfinite(xs) & np.isfinite(ys)
    if not finite.any():
        return float("nan")
    xs = xs[finite]
    ys = ys[finite]
    left_value = float(ys[0]) if left is None else float(left)
    right_value = float(ys[-1]) if right is None else float(right)
    return float(
        np.interp(
            float(x),
            xs,
            ys,
            left=left_value,
            right=right_value,
        )
    )


def normalise_sort_code(value: Any) -> str:
    """Normalise a pilot sort code to the TIPSY industrial code family."""
    code = str(value).strip().upper()
    return SORT_ALIASES.get(code, code)


def _assert_expected_codes(codes: Iterable[str], *, context: str) -> None:
    unexpected = sorted({str(code) for code in codes if str(code) not in INDUSTRIAL_ORDER})
    if unexpected:
        raise SortValidationError(
            f"{context}: unexpected industrial sort code(s) {', '.join(unexpected)}; "
            "refusing to drop or coerce them"
        )


def _normalise_shares(
    values: Mapping[str, float],
    order: Sequence[str] = INDUSTRIAL_ORDER,
) -> tuple[dict[str, float], float]:
    """Return non-negative shares and the positive total used to scale them."""
    numeric: dict[str, float] = {}
    for code in order:
        value = values.get(code, 0.0)
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise SortValidationError(f"non-numeric share for {code}: {value!r}") from exc
        if not math.isfinite(number):
            raise SortValidationError(f"non-finite share for {code}: {value!r}")
        if number < 0:
            raise SortValidationError(f"negative share for {code}: {number}")
        numeric[code] = number
    total = float(sum(numeric.values()))
    if total <= 0:
        return {code: 0.0 for code in order}, 0.0
    return {code: numeric[code] / total for code in order}, total


def _require_unit_shares(shares: Mapping[str, float], *, context: str) -> None:
    total = float(sum(float(shares.get(code, 0.0)) for code in INDUSTRIAL_ORDER))
    if not math.isfinite(total) or abs(total - 1.0) > _SHARE_TOL:
        raise SortValidationError(f"{context}: shares do not sum to 1 ({total!r})")


def tvd(
    pilot: Mapping[str, float],
    tipsy: Mapping[str, float],
    *,
    order: Sequence[str] = INDUSTRIAL_ORDER,
) -> float:
    """Total variation distance between two industrial-sort share vectors."""
    pilot_shares, pilot_total = _normalise_shares(pilot, order)
    tipsy_shares, tipsy_total = _normalise_shares(tipsy, order)
    if pilot_total <= 0 or tipsy_total <= 0:
        raise SortValidationError("TVD requires two non-empty share vectors")
    _require_unit_shares(pilot_shares, context="pilot")
    _require_unit_shares(tipsy_shares, context="tipsy")
    return 0.5 * float(
        sum(abs(pilot_shares[code] - tipsy_shares[code]) for code in order)
    )


def overlap(
    pilot: Mapping[str, float],
    tipsy: Mapping[str, float],
    *,
    order: Sequence[str] = INDUSTRIAL_ORDER,
) -> float:
    """Histogram overlap (1 - TVD) for two industrial-sort share vectors."""
    pilot_shares, pilot_total = _normalise_shares(pilot, order)
    tipsy_shares, tipsy_total = _normalise_shares(tipsy, order)
    if pilot_total <= 0 or tipsy_total <= 0:
        raise SortValidationError("overlap requires two non-empty share vectors")
    _require_unit_shares(pilot_shares, context="pilot")
    _require_unit_shares(tipsy_shares, context="tipsy")
    return float(sum(min(pilot_shares[code], tipsy_shares[code]) for code in order))


# ---------------------------------------------------------------------------
# Inputs


def _read_required_csv(path: Path, required: Iterable[str], *, label: str) -> pd.DataFrame:
    path = Path(path)
    if not path.exists():
        raise SortValidationError(f"{label} not found: {path}")
    frame = pd.read_csv(path)
    missing = sorted(set(required).difference(frame.columns))
    if missing:
        raise SortValidationError(
            f"{label} missing required columns: {', '.join(missing)} ({path})"
        )
    return frame


def resolve_tipsy_sorts_path(explicit: Path | None = None) -> tuple[Path, str]:
    """Resolve the TIPSY industrial-sorts table, with an explicit fallback.

    The canonical compiler output ``tsa24_grade_curves/managed_au_industrial_sorts.csv``
    is empty in the current repository because the refreshed BTC run requested
    only the ``log-grades`` and ``residual-fibre`` indicator banks.  When that
    file has no data rows, the validation uses the separately compiled
    ``tipsy_industrial_sorts.csv`` written by the same TSA24 BTC model with the
    ``industrial-logs`` bank.  The choice is recorded in the manifest.
    """
    if explicit is not None:
        path = Path(explicit)
        if not path.exists():
            raise SortValidationError(f"TIPSY industrial sorts not found: {path}")
        return path, "explicit"

    canonical = Path(DEFAULT_TIPSY_SORTS)
    if canonical.exists():
        try:
            probe = pd.read_csv(canonical)
        except pd.errors.EmptyDataError:
            probe = pd.DataFrame()
        if not probe.empty:
            return canonical, "canonical_tsa24_grade_curves"

    fallback = Path(FALLBACK_TIPSY_SORTS)
    if fallback.exists():
        return fallback, "regenerated_btc_industrial_logs"
    raise SortValidationError(
        "no TIPSY industrial-sort table found; expected "
        f"{canonical} to contain rows or {fallback} to exist"
    )


def load_tipsy_sorts(path: Path) -> pd.DataFrame:
    """Load and parse the TIPSY industrial-sort table.

    The returned frame uses the unprefixed TIPSY code names (``D125L5`` ...),
    keeps ``au_id`` and ``age`` as the join keys, and carries a positive
    ``industrial_logs_total_m3ha`` column.
    """
    frame = _read_required_csv(path, {"au_id", "age"}, label="TIPSY industrial sorts")
    if frame.empty:
        raise SortValidationError(f"TIPSY industrial-sort table has no data rows: {path}")

    prefixed = {
        column: str(column).removeprefix("Industrial_Logs_")
        for column in frame.columns
        if str(column).startswith("Industrial_Logs_")
    }
    unprefixed = {
        column: str(column) for column in frame.columns if str(column) in INDUSTRIAL_ORDER
    }
    code_columns: dict[str, str] = {}
    for source, code in {**prefixed, **unprefixed}.items():
        if code not in INDUSTRIAL_ORDER:
            raise SortValidationError(
                f"unexpected TIPSY industrial column {source!r} in {path}; "
                "refusing to drop it"
            )
        if code in code_columns:
            raise SortValidationError(
                f"duplicate TIPSY industrial column for code {code} in {path}"
            )
        code_columns[code] = source

    missing = sorted(set(INDUSTRIAL_ORDER).difference(code_columns))
    if missing:
        raise SortValidationError(
            "TIPSY industrial-sort table is missing expected code(s): "
            + ", ".join(missing)
            + f" ({path})"
        )

    parsed = pd.DataFrame(
        {
            "au_id": pd.to_numeric(frame["au_id"], errors="coerce").astype("Int64"),
            "age": pd.to_numeric(frame["age"], errors="coerce"),
        }
    )
    if parsed["au_id"].isna().any():
        raise SortValidationError(f"TIPSY industrial sorts contain invalid au_id: {path}")
    if parsed["age"].isna().any():
        raise SortValidationError(f"TIPSY industrial sorts contain invalid age: {path}")

    for code in INDUSTRIAL_ORDER:
        values = pd.to_numeric(frame[code_columns[code]], errors="coerce")
        if values.isna().any():
            raise SortValidationError(
                f"TIPSY industrial sorts contain NaN/non-numeric values for {code}: {path}"
            )
        if (values < 0).any():
            raise SortValidationError(
                f"TIPSY industrial sorts contain negative values for {code}: {path}"
            )
        parsed[code] = values.astype(float)

    parsed["industrial_logs_total_m3ha"] = parsed[list(INDUSTRIAL_ORDER)].sum(axis=1)
    if "age_extrapolated" in frame.columns:
        parsed["age_extrapolated"] = frame["age_extrapolated"].astype(bool)
    else:
        parsed["age_extrapolated"] = False

    parsed = parsed.sort_values(["au_id", "age"]).reset_index(drop=True)
    if parsed.duplicated(["au_id", "age"]).any():
        raise SortValidationError(f"duplicate AU/age rows in TIPSY industrial sorts: {path}")
    return parsed


def load_coverage(path: Path) -> pd.DataFrame:
    """Load the managed-AU coverage table used as the pilot join crosswalk."""
    frame = _read_required_csv(
        path,
        {"au_id", "stratum_code", "si_level", "au_site_index"},
        label="managed AU coverage",
    )
    frame = frame.copy()
    if "bec_zone" not in frame.columns:
        frame["bec_zone"] = frame["stratum_code"].astype(str).str.partition("_")[0]
    if "leading_species" not in frame.columns:
        frame["leading_species"] = frame["stratum_code"].astype(str).str.partition("_")[2]
    frame["au_id"] = pd.to_numeric(frame["au_id"], errors="coerce").astype("Int64")
    frame["au_site_index"] = pd.to_numeric(frame["au_site_index"], errors="coerce")
    if frame["au_id"].isna().any():
        raise SortValidationError(f"managed AU coverage has invalid au_id: {path}")
    return frame


def load_pilot_logs(path: Path) -> pd.DataFrame:
    """Load bucked logs, normalise sort codes, and fail on unexpected codes."""
    frame = _read_required_csv(
        path,
        {"tree_id", "plot_id", "volume_m3", "sort_code"},
        label="bucked logs",
    ).copy()
    if frame.empty:
        raise SortValidationError(f"bucked logs are empty: {path}")
    frame["sort_code"] = frame["sort_code"].map(normalise_sort_code)
    _assert_expected_codes(frame["sort_code"], context="bucked logs")
    frame["volume_m3"] = pd.to_numeric(frame["volume_m3"], errors="coerce")
    if frame["volume_m3"].isna().any():
        raise SortValidationError(f"bucked logs contain non-numeric volume_m3: {path}")
    if (frame["volume_m3"] < 0).any():
        raise SortValidationError(f"bucked logs contain negative volume_m3: {path}")
    return frame


def load_trees(path: Path) -> pd.DataFrame:
    """Load the tree list with plot attributes and expansion factors."""
    frame = _read_required_csv(
        path,
        {"tree_id", "plot_id", "species", "bec_zone", "stand_age", "site_index", "expansion_factor"},
        label="selected trees",
    ).copy()
    if frame.empty:
        raise SortValidationError(f"selected trees are empty: {path}")
    frame["expansion_factor"] = pd.to_numeric(frame["expansion_factor"], errors="coerce")
    if frame["expansion_factor"].isna().any() or (frame["expansion_factor"] <= 0).any():
        raise SortValidationError(
            f"selected trees contain non-positive/non-numeric expansion_factor: {path}"
        )
    frame["stand_age"] = pd.to_numeric(frame["stand_age"], errors="coerce")
    frame["site_index"] = pd.to_numeric(frame["site_index"], errors="coerce")
    frame["species"] = frame["species"].astype(str).str.strip().str.upper()
    if frame["species"].eq("NAN").any():
        raise SortValidationError(f"selected trees contain missing species: {path}")
    return frame


def load_pilot_benchmark(path: Path | None) -> pd.DataFrame | None:
    if path is None:
        return None
    path = Path(path)
    if not path.exists():
        return None
    frame = pd.read_csv(path)
    required = {"plot_id", "au_id", "stratum_code", "si_level", "stand_age", "leading_species"}
    if not required.issubset(frame.columns):
        return None
    keep = ["plot_id", "au_id", "stratum_code", "si_level", "stand_age", "leading_species"]
    if "match_tier" in frame.columns:
        keep.append("match_tier")
    return frame[keep].copy()


# ---------------------------------------------------------------------------
# Join and curves


def _leading_supported_species(species: pd.Series) -> str | None:
    supported = species[~species.isin(UNSUPPORTED_SPECIES)]
    if supported.empty:
        return None
    return str(supported.value_counts().index[0])


def _leading_all_species(species: pd.Series) -> str | None:
    if species.empty:
        return None
    return str(species.value_counts().index[0])


def _coverage_join(coverage: pd.DataFrame) -> pd.DataFrame:
    join = coverage[
        ["au_id", "stratum_code", "si_level", "bec_zone", "leading_species", "au_site_index"]
    ].copy()
    join["species_token"] = join["leading_species"].astype(str)
    join["feature_id"] = join["au_id"]
    return join


def build_plot_attributes(
    trees: pd.DataFrame,
    coverage: pd.DataFrame,
    *,
    pilot_benchmark: pd.DataFrame | None = None,
    femic_root: Path | None = None,
    available_species: Iterable[str] = (),
) -> pd.DataFrame:
    """Join each plot to a managed AU and age using the pilot benchmark basis.

    Primary join: FEMIC AU crosswalk (``au_table`` + ``03_input``) when a
    FEMIC root is available, otherwise the compiled ``au_grade_coverage.csv``
    crosswalk, which carries the same AU/stratum/SI fields.  Plot -> BEC +
    supported leading species -> managed stratum -> site-index class -> AU.

    Fallbacks, in order:
    * the pilot ``grade_benchmark.csv`` AU/age for a plot when the primary join
      does not resolve it;
    * a species-mean TIPSY curve when no AU is available but the leading
      species has a TIPSY curve.

    No age is imputed from a species mean or a global average.  A plot without
    a stand age remains ``no_age`` and is excluded from curve metrics.
    """
    plots = (
        trees.groupby("plot_id", sort=True)
        .agg(
            bec_zone=("bec_zone", "first"),
            stand_age=("stand_age", "first"),
            site_index=("site_index", "first"),
            n_trees=("tree_id", "nunique"),
            leading_species_all=("species", _leading_all_species),
        )
        .reset_index()
    )
    leading = (
        trees.groupby("plot_id", sort=True)["species"]
        .agg(_leading_supported_species)
        .rename("leading_species")
    )
    plots = plots.merge(leading, on="plot_id", how="left")

    join = _coverage_join(coverage)
    if femic_root is not None:
        femic_join = _load_au_join(Path(femic_root))
        if femic_join is not None and not femic_join.empty:
            join = femic_join.copy()
            join_basis = "femic_au_table_crosswalk"
        else:
            join_basis = "compiled_au_grade_coverage"
    else:
        join_basis = "compiled_au_grade_coverage"

    pilot = None
    if pilot_benchmark is not None and not pilot_benchmark.empty:
        pilot = pilot_benchmark.rename(
            columns={
                "au_id": "pilot_au_id",
                "stratum_code": "pilot_stratum_code",
                "si_level": "pilot_si_level",
                "stand_age": "pilot_stand_age",
                "leading_species": "pilot_leading_species",
                "match_tier": "pilot_match_tier",
            }
        )
        plots = plots.merge(pilot, on="plot_id", how="left")

    species_codes = {str(code) for code in available_species}
    rows: list[dict[str, Any]] = []
    for plot in plots.itertuples(index=False):
        bec = str(plot.bec_zone).strip().upper() if pd.notna(plot.bec_zone) else None
        leading_species = (
            str(plot.leading_species).strip().upper()
            if pd.notna(plot.leading_species)
            else None
        )
        if leading_species is None and hasattr(plot, "pilot_leading_species"):
            pilot_leading = plot.pilot_leading_species
            leading_species = (
                str(pilot_leading).strip().upper()
                if pd.notna(pilot_leading)
                else None
            )
        stand_age = float(plot.stand_age) if pd.notna(plot.stand_age) else None
        age_source = "faib_stand_age" if stand_age is not None else None
        if stand_age is None and hasattr(plot, "pilot_stand_age"):
            pilot_age = plot.pilot_stand_age
            if pd.notna(pilot_age):
                stand_age = float(pilot_age)
                age_source = "pilot_grade_benchmark"

        stratum = (
            _best_au_stratum(join, bec, leading_species)
            if bec is not None and leading_species is not None
            else None
        )
        si_level = (
            _assign_si_level_from_join(join, stratum, plot.site_index)
            if stratum is not None
            else None
        )
        au_id = (
            _au_id_from_join(join, stratum, si_level)
            if stratum is not None and si_level is not None
            else None
        )
        join_source = join_basis if au_id is not None else None

        if au_id is None and hasattr(plot, "pilot_au_id") and pd.notna(plot.pilot_au_id):
            au_id = int(plot.pilot_au_id)
            stratum = (
                str(plot.pilot_stratum_code)
                if pd.notna(plot.pilot_stratum_code)
                else stratum
            )
            si_level = (
                str(plot.pilot_si_level) if pd.notna(plot.pilot_si_level) else si_level
            )
            join_source = "pilot_grade_benchmark_fallback"

        species_curve_code = SPECIES_TO_CURVE.get(leading_species or "")
        species_available = species_curve_code in species_codes

        if stand_age is None:
            match_tier = "no_age"
            reason = "FAIB stand age missing and no pilot-benchmark age fallback"
        elif au_id is not None:
            match_tier = "au_curve"
            reason = "managed AU + FAIB stand age"
        elif species_available:
            match_tier = "species_curve"
            reason = "no managed AU for BEC + leading species; documented species-mean fallback"
        else:
            match_tier = "no_match"
            reason = "no managed AU and no TIPSY species curve for leading species"

        rows.append(
            {
                "plot_id": str(plot.plot_id),
                "bec_zone": bec,
                "leading_species": leading_species,
                "leading_species_all": (
                    str(plot.leading_species_all)
                    if pd.notna(plot.leading_species_all)
                    else None
                ),
                "stand_age": stand_age,
                "age_source": age_source,
                "site_index": (
                    float(plot.site_index) if pd.notna(plot.site_index) else None
                ),
                "stratum_code": stratum,
                "si_level": si_level,
                "au_id": int(au_id) if au_id is not None else None,
                "species_curve_code": species_curve_code if species_available else None,
                "match_tier": match_tier,
                "match_reason": reason,
                "join_source": join_source,
                "join_basis": join_basis,
                "n_trees": int(plot.n_trees),
            }
        )
    return pd.DataFrame(rows)


def build_tipsy_curves(
    tipsy: pd.DataFrame,
    coverage: pd.DataFrame,
) -> tuple[dict[int, pd.DataFrame], dict[str, pd.DataFrame]]:
    """Index TIPSY industrial curves by AU and by species-mean fallback."""
    by_au = {
        int(au_id): group.sort_values("age").reset_index(drop=True)
        for au_id, group in tipsy.groupby("au_id", sort=True)
    }
    leading = coverage[["au_id", "leading_species"]].copy()
    leading["au_id"] = pd.to_numeric(leading["au_id"], errors="coerce").astype("Int64")
    merged = tipsy.merge(leading, on="au_id", how="left")
    merged["species_code"] = merged["leading_species"].map(
        lambda value: SPECIES_TO_CURVE.get(str(value).strip().upper(), str(value).strip().upper())
    )
    value_columns = list(INDUSTRIAL_ORDER)
    rows: list[pd.DataFrame] = []
    for species_code, group in merged.groupby("species_code", dropna=True):
        if not species_code or species_code == "NAN":
            continue
        numeric = group.groupby("age", dropna=True)[value_columns].mean().reset_index()
        numeric["industrial_logs_total_m3ha"] = numeric[value_columns].sum(axis=1)
        numeric["species_code"] = str(species_code)
        numeric["au_count"] = int(group["au_id"].nunique())
        numeric["age_extrapolated"] = False
        rows.append(numeric)
    by_species = (
        {str(frame["species_code"].iloc[0]): frame.sort_values("age").reset_index(drop=True)
         for frame in rows}
        if rows
        else {}
    )
    return by_au, by_species


def _curve_at_age(frame: pd.DataFrame, age: float) -> dict[str, Any]:
    ordered = frame.sort_values("age")
    xs = pd.to_numeric(ordered["age"], errors="coerce").to_numpy(dtype=float)
    values: dict[str, Any] = {}
    for code in INDUSTRIAL_ORDER:
        ys = pd.to_numeric(ordered[code], errors="coerce").to_numpy(dtype=float)
        values[code] = _finite_interp(age, xs, ys)
    total = sum(float(values[code]) for code in INDUSTRIAL_ORDER)
    values["industrial_logs_total_m3ha"] = total
    max_age = float(np.nanmax(xs)) if np.isfinite(xs).any() else float("nan")
    min_age = float(np.nanmin(xs)) if np.isfinite(xs).any() else float("nan")
    nearest_index = int(np.nanargmin(np.abs(xs - float(age)))) if np.isfinite(xs).any() else 0
    values["age_extrapolated"] = bool(
        (math.isfinite(max_age) and float(age) > max_age)
        or (math.isfinite(min_age) and float(age) < min_age)
        or bool(ordered.iloc[nearest_index].get("age_extrapolated", False))
    )
    return values


# ---------------------------------------------------------------------------
# Comparison


def _pilot_shares_by_plot(logs: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    if logs.empty:
        empty = pd.DataFrame(columns=INDUSTRIAL_ORDER)
        return empty, empty
    grouped = (
        logs.groupby(["plot_id", "sort_code"], sort=False)["volume_m3"]
        .sum()
        .unstack(fill_value=0.0)
    )
    grouped = grouped.reindex(columns=list(INDUSTRIAL_ORDER), fill_value=0.0)
    totals = grouped.sum(axis=1)
    shares = grouped.div(totals.replace(0.0, np.nan), axis=0).fillna(0.0)
    return grouped, shares


def _expanded_logs(logs: pd.DataFrame, trees: pd.DataFrame) -> pd.DataFrame:
    expansion = trees[["tree_id", "expansion_factor"]].drop_duplicates("tree_id")
    expanded = logs.merge(expansion, on="tree_id", how="left", validate="many_to_one")
    if expanded["expansion_factor"].isna().any():
        missing = int(expanded["expansion_factor"].isna().sum())
        raise SortValidationError(
            f"{missing} bucked-log rows have no tree expansion factor"
        )
    expanded["volume_m3ha"] = expanded["volume_m3"] * expanded["expansion_factor"]
    return expanded


def build_plot_comparison(
    plots: pd.DataFrame,
    logs: pd.DataFrame,
    trees: pd.DataFrame,
    by_au: Mapping[int, pd.DataFrame],
    by_species: Mapping[str, pd.DataFrame],
) -> pd.DataFrame:
    """Build one industrial-sort comparison row per plot."""
    expanded = _expanded_logs(logs, trees)
    volume_by_plot = expanded.groupby("plot_id")["volume_m3ha"].sum()
    raw_by_plot = expanded.groupby("plot_id")["volume_m3"].sum()
    n_logs = expanded.groupby("plot_id").size()
    n_trees = expanded.groupby("plot_id")["tree_id"].nunique()
    _, shares_by_plot = _pilot_shares_by_plot(logs)

    rows: list[dict[str, Any]] = []
    for plot in plots.itertuples(index=False):
        plot_id = str(plot.plot_id)
        pilot_m3ha = float(volume_by_plot.get(plot_id, 0.0))
        raw_m3 = float(raw_by_plot.get(plot_id, 0.0))
        logs_count = int(n_logs.get(plot_id, 0))
        tree_count = int(n_trees.get(plot_id, 0))
        pilot_shares = (
            {code: float(shares_by_plot.loc[plot_id, code]) for code in INDUSTRIAL_ORDER}
            if plot_id in shares_by_plot.index
            else {code: 0.0 for code in INDUSTRIAL_ORDER}
        )

        tipsy_values = {code: 0.0 for code in INDUSTRIAL_ORDER}
        tipsy_total = 0.0
        tipsy_extrapolated = False
        tipsy_curve_id: Any = None
        if plot.match_tier == "au_curve" and pd.notna(plot.au_id):
            curve = by_au.get(int(plot.au_id))
            if curve is not None and pd.notna(plot.stand_age):
                values = _curve_at_age(curve, float(plot.stand_age))
                tipsy_values = {code: float(values[code]) for code in INDUSTRIAL_ORDER}
                tipsy_total = float(values["industrial_logs_total_m3ha"])
                tipsy_extrapolated = bool(values["age_extrapolated"])
                tipsy_curve_id = int(plot.au_id)
        elif plot.match_tier == "species_curve" and pd.notna(plot.species_curve_code):
            curve = by_species.get(str(plot.species_curve_code))
            if curve is not None and pd.notna(plot.stand_age):
                values = _curve_at_age(curve, float(plot.stand_age))
                tipsy_values = {code: float(values[code]) for code in INDUSTRIAL_ORDER}
                tipsy_total = float(values["industrial_logs_total_m3ha"])
                tipsy_extrapolated = bool(values["age_extrapolated"])
                tipsy_curve_id = str(plot.species_curve_code)

        tipsy_shares, _ = _normalise_shares(tipsy_values)
        if logs_count == 0:
            status = "no_logs"
        elif plot.match_tier == "no_age":
            status = "no_age"
        elif plot.match_tier == "no_match":
            status = "no_au_match"
        elif tipsy_total <= 0:
            status = "zero_tipsy_volume"
        elif pilot_m3ha <= 0:
            status = "zero_pilot_volume"
        elif plot.match_tier == "au_curve":
            status = "comparable_au"
        else:
            status = "comparable_species"

        comparable = status in {"comparable_au", "comparable_species"}
        sort_tvd = float("nan")
        sort_overlap = float("nan")
        if comparable:
            sort_tvd = tvd(pilot_shares, tipsy_shares)
            sort_overlap = overlap(pilot_shares, tipsy_shares)
            if abs((sort_tvd + sort_overlap) - 1.0) > 1e-9:
                raise SortValidationError(
                    f"TVD/overlap identity failed for plot {plot_id}: "
                    f"{sort_tvd} + {sort_overlap}"
                )

        row: dict[str, Any] = {
            "plot_id": plot_id,
            "bec_zone": plot.bec_zone,
            "leading_species": plot.leading_species,
            "leading_species_all": plot.leading_species_all,
            "stand_age": plot.stand_age,
            "age_source": plot.age_source,
            "site_index": plot.site_index,
            "stratum_code": plot.stratum_code,
            "si_level": plot.si_level,
            "au_id": plot.au_id,
            "species_curve_code": plot.species_curve_code,
            "match_tier": plot.match_tier,
            "match_reason": plot.match_reason,
            "join_source": plot.join_source,
            "join_basis": plot.join_basis,
            "comparison_status": status,
            "n_logs": logs_count,
            "n_trees": tree_count,
            "bucked_log_m3": raw_m3,
            "bucked_log_m3ha": pilot_m3ha,
            "tipsy_curve_id": tipsy_curve_id,
            "tipsy_industrial_total_m3ha": tipsy_total,
            "tipsy_age_extrapolated": tipsy_extrapolated,
            "pilot_sort_sum": float(sum(pilot_shares.values())),
            "tipsy_sort_sum": float(sum(tipsy_shares.values())),
            "sort_tvd": sort_tvd,
            "sort_overlap": sort_overlap,
            "sort_tvd_weight_m3ha": pilot_m3ha if comparable else 0.0,
            "volume_bias_m3ha": tipsy_total - pilot_m3ha if comparable else float("nan"),
            "pilot_evidence": "dimension_only",
            "tipsy_evidence": "model_output",
            "comparison_evidence": "derived",
            "grade_status": GRADE_STATUS,
            "label": LABEL,
        }
        for code in INDUSTRIAL_ORDER:
            row[f"pilot_sort_{code}"] = float(pilot_shares[code])
        for code in INDUSTRIAL_ORDER:
            row[f"tipsy_sort_{code}"] = float(tipsy_shares[code])
        rows.append(row)
    frame = pd.DataFrame(rows)
    return frame.sort_values("plot_id").reset_index(drop=True)


def _weighted_shares(
    frame: pd.DataFrame,
    prefix: str,
    weights: Sequence[float],
) -> dict[str, float]:
    total_weight = float(sum(max(0.0, float(weight)) for weight in weights))
    if total_weight <= 0:
        return {code: 0.0 for code in INDUSTRIAL_ORDER}
    records = frame.to_dict(orient="records")
    return {
        code: float(
            sum(
                float(row[f"{prefix}_sort_{code}"]) * max(0.0, float(weight))
                for row, weight in zip(records, weights)
            )
            / total_weight
        )
        for code in INDUSTRIAL_ORDER
    }


def _volume_metrics(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {"n": 0}
    observed = pd.to_numeric(frame["bucked_log_m3ha"], errors="coerce")
    predicted = pd.to_numeric(frame["tipsy_industrial_total_m3ha"], errors="coerce")
    valid = observed.notna() & predicted.notna()
    if not valid.any():
        return {"n": 0}
    observed = observed[valid].astype(float)
    predicted = predicted[valid].astype(float)
    bias = predicted - observed
    positive = observed > 0
    ratios = (predicted[positive] / observed[positive]).replace(
        [np.inf, -np.inf], np.nan
    ).dropna()
    return {
        "n": int(valid.sum()),
        "observed_sum_m3ha": float(observed.sum()),
        "predicted_sum_m3ha": float(predicted.sum()),
        "bias_sum_m3ha": float(bias.sum()),
        "mean_bias_m3ha": float(bias.mean()),
        "mae_m3ha": float(bias.abs().mean()),
        "rmse_m3ha": float(np.sqrt(np.mean(np.square(bias)))),
        "relative_bias": (
            float(bias.sum() / observed.sum()) if observed.sum() > 0 else None
        ),
        "ratio_predicted_observed": (
            float(predicted.sum() / observed.sum()) if observed.sum() > 0 else None
        ),
        "mean_ratio": float(ratios.mean()) if not ratios.empty else None,
        "median_ratio": float(ratios.median()) if not ratios.empty else None,
    }


def _tvd_summary(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {"n": 0}
    values = pd.to_numeric(frame["sort_tvd"], errors="coerce").dropna().to_numpy(dtype=float)
    overlaps = pd.to_numeric(frame["sort_overlap"], errors="coerce").dropna().to_numpy(dtype=float)
    weights = pd.to_numeric(frame["sort_tvd_weight_m3ha"], errors="coerce").fillna(0.0).to_numpy(dtype=float)
    if values.size == 0:
        return {"n": 0}
    total_weight = float(weights.sum())
    return {
        "n": int(values.size),
        "mean_tvd": float(values.mean()),
        "median_tvd": float(np.median(values)),
        "min_tvd": float(values.min()),
        "max_tvd": float(values.max()),
        "volume_weighted_tvd": (
            float(np.average(values, weights=weights)) if total_weight > 0 else None
        ),
        "mean_overlap": float(overlaps.mean()),
        "volume_weighted_overlap": (
            float(np.average(overlaps, weights=weights)) if total_weight > 0 else None
        ),
    }


def build_au_summary(plot_comparison: pd.DataFrame) -> pd.DataFrame:
    primary = plot_comparison[plot_comparison["comparison_status"] == "comparable_au"].copy()
    if primary.empty:
        return pd.DataFrame(
            columns=[
                "au_id",
                "n_plots",
                "n_logs",
                "n_trees",
                "bucked_log_m3",
                "bucked_log_m3ha",
                "tipsy_industrial_total_m3ha",
                "mean_plot_tvd",
                "volume_weighted_tvd",
                "pooled_tvd",
                "mean_plot_overlap",
                "pooled_overlap",
                "bias_m3ha",
                "rmse_m3ha",
                "relative_bias",
            ]
        )
    rows: list[dict[str, Any]] = []
    for au_id, group in primary.groupby("au_id", sort=True):
        weights = group["bucked_log_m3ha"].astype(float).tolist()
        pilot_pooled = _weighted_shares(group, "pilot", weights)
        tipsy_pooled = _weighted_shares(group, "tipsy", weights)
        pooled_tvd = tvd(pilot_pooled, tipsy_pooled)
        pooled_overlap = overlap(pilot_pooled, tipsy_pooled)
        volume = _volume_metrics(group)
        rows.append(
            {
                "au_id": int(au_id),
                "n_plots": len(group),
                "n_logs": int(group["n_logs"].sum()),
                "n_trees": int(group["n_trees"].sum()),
                "bucked_log_m3": float(group["bucked_log_m3"].sum()),
                "bucked_log_m3ha": float(group["bucked_log_m3ha"].sum()),
                "tipsy_industrial_total_m3ha": float(
                    group["tipsy_industrial_total_m3ha"].sum()
                ),
                "mean_plot_tvd": float(group["sort_tvd"].mean()),
                "volume_weighted_tvd": float(
                    np.average(group["sort_tvd"], weights=group["sort_tvd_weight_m3ha"])
                ),
                "pooled_tvd": pooled_tvd,
                "mean_plot_overlap": float(group["sort_overlap"].mean()),
                "pooled_overlap": pooled_overlap,
                "bias_m3ha": volume["mean_bias_m3ha"],
                "rmse_m3ha": volume["rmse_m3ha"],
                "relative_bias": volume["relative_bias"],
            }
        )
    return pd.DataFrame(rows).sort_values("au_id").reset_index(drop=True)


def _scope_metrics(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {"n_plots": 0}
    weights = frame["bucked_log_m3ha"].astype(float).tolist()
    pilot_pooled = _weighted_shares(frame, "pilot", weights)
    tipsy_pooled = _weighted_shares(frame, "tipsy", weights)
    pooled_tvd = tvd(pilot_pooled, tipsy_pooled)
    pooled_overlap = overlap(pilot_pooled, tipsy_pooled)
    return {
        "n_plots": len(frame),
        "n_au": int(frame["au_id"].nunique()),
        "n_logs": int(frame["n_logs"].sum()),
        "n_trees": int(frame["n_trees"].sum()),
        "bucked_log_m3": float(frame["bucked_log_m3"].sum()),
        "bucked_log_m3ha": float(frame["bucked_log_m3ha"].sum()),
        "tipsy_industrial_total_m3ha": float(
            frame["tipsy_industrial_total_m3ha"].sum()
        ),
        "age_extrapolated_plots": int(frame["tipsy_age_extrapolated"].sum()),
        "plot_tvd": _tvd_summary(frame),
        "pooled_pilot_shares": pilot_pooled,
        "pooled_tipsy_shares": tipsy_pooled,
        "pooled_tvd": pooled_tvd,
        "pooled_overlap": pooled_overlap,
        "volume": _volume_metrics(frame),
    }


def _status_counts(plot_comparison: pd.DataFrame) -> dict[str, int]:
    counts = {
        str(key): int(value)
        for key, value in plot_comparison["comparison_status"].value_counts().items()
    }
    for status in (
        "no_logs",
        "no_age",
        "no_au_match",
        "zero_tipsy_volume",
        "zero_pilot_volume",
        "comparable_au",
        "comparable_species",
    ):
        counts.setdefault(status, 0)
    return counts


def compute_metrics(
    plot_comparison: pd.DataFrame,
    au_summary: pd.DataFrame,
    *,
    tipsy_source_path: Path,
    tipsy_source_role: str,
    n_plots_total: int,
    n_logs_total: int,
    n_trees_total: int,
    n_au_tipsy: int | None = None,
) -> dict[str, Any]:
    """Compute the industrial-sort validation surface with no fitted threshold."""
    primary = plot_comparison[plot_comparison["comparison_status"] == "comparable_au"].copy()
    extended = plot_comparison[
        plot_comparison["comparison_status"].isin(["comparable_au", "comparable_species"])
    ].copy()
    statuses = _status_counts(plot_comparison)
    if sum(statuses.values()) != n_plots_total:
        raise SortValidationError(
            f"comparison status counts sum to {sum(statuses.values())}, expected {n_plots_total}"
        )

    primary_metrics = _scope_metrics(primary)
    extended_metrics = _scope_metrics(extended)
    inclusion = statuses.get("comparable_au", 0) + statuses.get("comparable_species", 0)

    return {
        "status": "reported",
        "label": LABEL,
        "grade_status": GRADE_STATUS,
        "official_grade_validation": False,
        "pass_fail": "not_assessed_no_pre_registered_tolerance",
        "tolerance": None,
        "labels": [LABEL],
        "evidence_tiers": {
            "pilot_sorts": "dimension_only",
            "tipsy_industrial_sorts": "model_output",
            "comparison": "derived",
        },
        "tipsy_source": {
            "path": str(tipsy_source_path),
            "role": tipsy_source_role,
        },
        "sample": {
            "n_plots_total": int(n_plots_total),
            "n_plots_with_logs": int((plot_comparison["n_logs"] > 0).sum()),
            "n_plots_comparable_au": int(statuses.get("comparable_au", 0)),
            "n_plots_comparable_species": int(statuses.get("comparable_species", 0)),
            "n_plots_comparable_total": int(inclusion),
            "n_plots_status": statuses,
            "n_logs_total": int(n_logs_total),
            "n_logs_comparable_au": int(primary["n_logs"].sum()),
            "n_logs_comparable_total": int(extended["n_logs"].sum()),
            "n_logs_excluded": int(n_logs_total - extended["n_logs"].sum()),
            "n_trees_total": int(n_trees_total),
            "n_trees_comparable_au": int(primary["n_trees"].sum()),
            "n_trees_comparable_total": int(extended["n_trees"].sum()),
            "n_trees_excluded": int(n_trees_total - extended["n_trees"].sum()),
            "n_au_total": int(plot_comparison["au_id"].nunique()),
            "n_au_tipsy_curves": int(n_au_tipsy) if n_au_tipsy is not None else None,
            "n_au_comparable": int(primary["au_id"].nunique()),
            "bucked_log_m3_total": float(plot_comparison["bucked_log_m3"].sum()),
            "bucked_log_m3_comparable_au": float(primary["bucked_log_m3"].sum()),
            "bucked_log_m3_comparable_total": float(extended["bucked_log_m3"].sum()),
            "bucked_log_m3_excluded": float(
                plot_comparison["bucked_log_m3"].sum() - extended["bucked_log_m3"].sum()
            ),
            "bucked_log_m3ha_comparable_au": float(primary["bucked_log_m3ha"].sum()),
            "tipsy_industrial_total_m3ha_comparable_au": float(
                primary["tipsy_industrial_total_m3ha"].sum()
            ),
        },
        "primary_au_match": primary_metrics,
        "extended_with_species_fallback": extended_metrics,
        "au_summary": au_summary.to_dict(orient="records"),
        "join": {
            "basis": (
                "FAIB plot -> BEC + supported leading species -> managed FEMIC AU "
                "stratum (including combined-species strata) -> FEMIC site-index "
                "class -> managed AU -> TIPSY industrial-log curve interpolated at "
                "the FAIB stand age. This is the pilot benchmark join, not an "
                "operational AU assignment."
            ),
            "fallbacks": [
                (
                    "pilot grade_benchmark.csv AU/stratum/SI/age when the primary "
                    "join does not resolve a plot"
                ),
                (
                    "species-mean TIPSY industrial curve when no AU is available "
                    "but the leading species has a TIPSY curve; reported separately"
                ),
            ],
            "no_age_imputation": True,
            "no_threshold_tuning": True,
            "sort_alias": dict(SORT_ALIASES),
            "fail_closed_on_unexpected_codes": True,
        },
        "caveats": [
            (
                "Industrial sort recovery is not official BC log grade. The bucker "
                "is dimension-only; defect, sweep, knot, rot, and product quality "
                "are absent."
            ),
            "TIPSY industrial-log curves are model output, not field measurements.",
            (
                "Multiple PSP plots can share one managed AU curve; per-plot RMSE "
                "is therefore not an independent model-error estimate."
            ),
            (
                "Bucked volume is expanded with FAIB PHF_TREE to m3/ha and compared "
                "with the TIPSY industrial total at the same AU/age. Plot sums are "
                "m3/ha sums, not absolute volume."
            ),
            (
                "The canonical managed_au_industrial_sorts.csv in tsa24_grade_curves "
                "is empty because the refreshed BTC run did not request the "
                "industrial-logs indicator bank; this validation records the "
                "separately compiled table it used and fails closed when no table "
                "is available."
            ),
        ],
    }


def build_sort_comparison(
    plot_comparison: pd.DataFrame,
    metrics: Mapping[str, Any],
) -> pd.DataFrame:
    """Long-form pilot-vs-TIPSY share table for aggregate and per-AU scopes."""
    rows: list[dict[str, Any]] = []
    primary = plot_comparison[plot_comparison["comparison_status"] == "comparable_au"]
    extended = plot_comparison[
        plot_comparison["comparison_status"].isin(["comparable_au", "comparable_species"])
    ]

    for scope, frame, scope_metrics in (
        ("aggregate_primary_au", primary, metrics["primary_au_match"]),
        ("aggregate_extended_species", extended, metrics["extended_with_species_fallback"]),
    ):
        if frame.empty or "pooled_pilot_shares" not in scope_metrics:
            continue
        pilot_shares = scope_metrics["pooled_pilot_shares"]
        tipsy_shares = scope_metrics["pooled_tipsy_shares"]
        records = frame.to_dict(orient="records")
        for code in INDUSTRIAL_ORDER:
            rows.append(
                {
                    "scope": scope,
                    "au_id": None,
                    "code": code,
                    "n_plots": int(scope_metrics["n_plots"]),
                    "n_logs": int(scope_metrics["n_logs"]),
                    "pilot_share": float(pilot_shares[code]),
                    "tipsy_share": float(tipsy_shares[code]),
                    "difference": float(pilot_shares[code] - tipsy_shares[code]),
                    "abs_difference": float(abs(pilot_shares[code] - tipsy_shares[code])),
                    "min_share": float(min(pilot_shares[code], tipsy_shares[code])),
                    "pilot_volume_m3": float(
                        sum(
                            float(row[f"pilot_sort_{code}"]) * float(row["bucked_log_m3"])
                            for row in records
                        )
                    ),
                    "pilot_volume_m3ha": float(
                        sum(
                            float(row[f"pilot_sort_{code}"]) * float(row["bucked_log_m3ha"])
                            for row in records
                        )
                    ),
                    "tipsy_volume_m3ha": float(
                        sum(
                            float(row[f"tipsy_sort_{code}"])
                            * float(row["tipsy_industrial_total_m3ha"])
                            for row in records
                        )
                    ),
                    "sort_tvd": float(scope_metrics["pooled_tvd"]),
                    "sort_overlap": float(scope_metrics["pooled_overlap"]),
                    "match_basis": (
                        "managed AU + age"
                        if scope == "aggregate_primary_au"
                        else "managed AU + age, plus species-mean fallback"
                    ),
                }
            )

    for au_id, group in primary.groupby("au_id", sort=True):
        weights = group["bucked_log_m3ha"].astype(float).tolist()
        pilot_shares = _weighted_shares(group, "pilot", weights)
        tipsy_shares = _weighted_shares(group, "tipsy", weights)
        group_tvd = tvd(pilot_shares, tipsy_shares)
        group_overlap = overlap(pilot_shares, tipsy_shares)
        records = group.to_dict(orient="records")
        for code in INDUSTRIAL_ORDER:
            rows.append(
                {
                    "scope": "au",
                    "au_id": int(au_id),
                    "code": code,
                    "n_plots": len(group),
                    "n_logs": int(group["n_logs"].sum()),
                    "pilot_share": float(pilot_shares[code]),
                    "tipsy_share": float(tipsy_shares[code]),
                    "difference": float(pilot_shares[code] - tipsy_shares[code]),
                    "abs_difference": float(abs(pilot_shares[code] - tipsy_shares[code])),
                    "min_share": float(min(pilot_shares[code], tipsy_shares[code])),
                    "pilot_volume_m3": float(
                        sum(
                            float(row[f"pilot_sort_{code}"]) * float(row["bucked_log_m3"])
                            for row in records
                        )
                    ),
                    "pilot_volume_m3ha": float(
                        sum(
                            float(row[f"pilot_sort_{code}"]) * float(row["bucked_log_m3ha"])
                            for row in records
                        )
                    ),
                    "tipsy_volume_m3ha": float(
                        sum(
                            float(row[f"tipsy_sort_{code}"])
                            * float(row["tipsy_industrial_total_m3ha"])
                            for row in records
                        )
                    ),
                    "sort_tvd": group_tvd,
                    "sort_overlap": group_overlap,
                    "match_basis": "managed AU + age",
                }
            )
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Output validation and report


def _validate_outputs(
    plot_comparison: pd.DataFrame,
    au_summary: pd.DataFrame,
    sort_comparison: pd.DataFrame,
    metrics: Mapping[str, Any],
) -> dict[str, Any]:
    """Fail closed on NaN, negative, or out-of-range output values."""
    share_columns = [f"pilot_sort_{code}" for code in INDUSTRIAL_ORDER] + [
        f"tipsy_sort_{code}" for code in INDUSTRIAL_ORDER
    ]
    for column in share_columns:
        values = pd.to_numeric(plot_comparison[column], errors="coerce")
        if values.isna().any():
            raise SortValidationError(f"output NaN in {column}")
        if (values < -1e-12).any() or (values > 1.0 + 1e-9).any():
            raise SortValidationError(f"output share out of [0,1] in {column}")
    comparable = plot_comparison[
        plot_comparison["comparison_status"].isin(["comparable_au", "comparable_species"])
    ]
    for column in ("sort_tvd", "sort_overlap"):
        values = pd.to_numeric(comparable[column], errors="coerce")
        if values.isna().any():
            raise SortValidationError(f"output NaN in comparable {column}")
        if (values < -1e-12).any() or (values > 1.0 + 1e-9).any():
            raise SortValidationError(f"output {column} out of [0,1]")
    for column in ("bucked_log_m3", "bucked_log_m3ha", "tipsy_industrial_total_m3ha"):
        values = pd.to_numeric(plot_comparison[column], errors="coerce")
        if values.isna().any():
            raise SortValidationError(f"output NaN in {column}")
        if (values < -1e-9).any():
            raise SortValidationError(f"output negative in {column}")
    for column in (
        "n_plots",
        "n_logs",
        "n_trees",
        "bucked_log_m3",
        "bucked_log_m3ha",
        "tipsy_industrial_total_m3ha",
        "mean_plot_tvd",
        "volume_weighted_tvd",
        "pooled_tvd",
        "mean_plot_overlap",
        "pooled_overlap",
        "rmse_m3ha",
    ):
        if column in au_summary.columns:
            values = pd.to_numeric(au_summary[column], errors="coerce")
            if values.isna().any():
                raise SortValidationError(f"AU summary NaN in {column}")
            if (values < -1e-9).any():
                raise SortValidationError(f"AU summary negative in {column}")
    if "bias_m3ha" in au_summary.columns:
        values = pd.to_numeric(au_summary["bias_m3ha"], errors="coerce")
        if values.isna().any():
            raise SortValidationError("AU summary NaN in bias_m3ha")
    for column in ("pilot_share", "tipsy_share", "sort_tvd", "sort_overlap"):
        values = pd.to_numeric(sort_comparison[column], errors="coerce")
        if values.isna().any():
            raise SortValidationError(f"sort comparison NaN in {column}")
        if (values < -1e-12).any() or (values > 1.0 + 1e-9).any():
            raise SortValidationError(f"sort comparison {column} out of [0,1]")

    n_plots = int(metrics["sample"]["n_plots_total"])
    status_sum = int(sum(metrics["sample"]["n_plots_status"].values()))
    if status_sum != n_plots:
        raise SortValidationError(
            f"status counts {status_sum} do not equal plot count {n_plots}"
        )
    comparable_count = int(
        metrics["sample"]["n_plots_comparable_au"]
        + metrics["sample"]["n_plots_comparable_species"]
    )
    if comparable_count != len(comparable):
        raise SortValidationError(
            f"comparable count {comparable_count} does not equal plot table {len(comparable)}"
        )
    return {
        "status": "pass",
        "n_plot_rows": len(plot_comparison),
        "n_au_rows": len(au_summary),
        "n_sort_rows": len(sort_comparison),
        "nan_or_negative_failures": 0,
    }


def _fmt(value: Any, digits: int = 3) -> str:
    if value is None:
        return "n/a"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if not math.isfinite(number):
        return "n/a"
    return f"{number:,.{digits}f}"


def _markdown_table(frame: pd.DataFrame, columns: Sequence[str], digits: int = 3) -> str:
    if frame.empty:
        return "_No rows._\n"
    header = "| " + " | ".join(columns) + " |"
    separator = "| " + " | ".join("---" for _ in columns) + " |"
    lines = [header, separator]
    for row in frame[list(columns)].itertuples(index=False):
        values = []
        for value in row:
            if isinstance(value, (int, np.integer)):
                values.append(str(int(value)))
            elif isinstance(value, (float, np.floating)):
                values.append(_fmt(value, digits))
            else:
                values.append(str(value))
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines) + "\n"


def build_report(
    *,
    generated_utc: str,
    summary: Mapping[str, Any],
    au_summary: pd.DataFrame,
    sort_comparison: pd.DataFrame,
    tipsy_source_role: str,
) -> str:
    sample = summary["sample"]
    primary = summary["primary_au_match"]
    extended = summary["extended_with_species_fallback"]
    volume = primary["volume"]
    statuses = sample["n_plots_status"]
    lines: list[str] = []
    lines.append("# TSA24 pilot industrial-sort validation v0\n")
    lines.append("| Field | Value |\n|---|---|\n")
    lines.append(f"| Validation | `{LABEL}` |\n")
    lines.append("| TSA | 24 — Prince George |\n")
    lines.append(f"| Generated (UTC) | {generated_utc} |\n")
    lines.append("| Grade status | `not_assigned` |\n")
    lines.append("| Evidence label | `industrial_sort_validation` |\n")
    lines.append(
        f"| Sample | {sample['n_plots_total']:,} plots, {sample['n_logs_total']:,} logs, "
        f"{sample['n_trees_total']:,} bucked trees |\n"
    )
    lines.append(
        "| Status | industrial sort recovery reported; no pre-registered tolerance |\n\n"
    )
    lines.append(
        "> This is an **industrial sort recovery** check, not an official BC log-grade "
        "validation. The bucker is dimension-only; defect, sweep, knot, rot, and product "
        "quality are not represented. Every output row is labelled "
        "`grade_status=not_assigned`.\n\n"
    )

    lines.append("## 1. Join and sample\n\n")
    lines.append(
        "Primary join: FAIB plot -> BEC + supported leading species -> managed FEMIC AU "
        "stratum (including combined-species strata) -> FEMIC site-index class -> managed "
        "AU -> TIPSY industrial-log curve interpolated at the FAIB stand age. This is the "
        "pilot benchmark join, not an operational AU assignment.\n\n"
    )
    lines.append("Documented fallbacks:\n\n")
    lines.append(
        "- `pilot grade_benchmark.csv` AU/stratum/SI/age when the primary join does not "
        "resolve a plot;\n"
    )
    lines.append(
        "- species-mean TIPSY industrial curve when no AU is available but the leading "
        "species has a TIPSY curve. This is reported separately and never mixed into the "
        "primary AU-matched metrics.\n\n"
    )
    lines.append(
        f"- TIPSY industrial-sort source: `{summary['tipsy_source']['path']}` "
        f"(`{tipsy_source_role}`).\n"
    )
    lines.append(
        "- Sort-code contract: 20 TIPSY industrial codes; the only accepted spelling "
        "difference is `D125L50` -> `D125L5`. Any other unexpected code fails closed.\n\n"
    )
    lines.append("### Plot status\n\n")
    status_labels = [
        ("comparable_au", "Comparable (managed AU + age)"),
        ("comparable_species", "Comparable (species fallback)"),
        ("no_logs", "Excluded: no bucked logs"),
        ("no_age", "Excluded: no stand age"),
        ("no_au_match", "Excluded: no AU/species match"),
        ("zero_tipsy_volume", "Excluded: TIPSY industrial total is zero"),
        ("zero_pilot_volume", "Excluded: pilot log volume is zero"),
    ]
    lines.append("| Status | Plots |\n|---|---|\n")
    for key, label in status_labels:
        lines.append(f"| {label} | {int(statuses.get(key, 0)):,} |\n")
    lines.append(f"| **Total** | **{sample['n_plots_total']:,}** |\n\n")
    lines.append(
        f"Comparable AU-matched plots: **{sample['n_plots_comparable_au']:,}**; "
        f"species-fallback plots: **{sample['n_plots_comparable_species']:,}**; "
        f"total comparable: **{sample['n_plots_comparable_total']:,}**.\n\n"
    )
    lines.append(
        f"Bucked logs in the comparable set: **{sample['n_logs_comparable_total']:,}** "
        f"of **{sample['n_logs_total']:,}**; bucked trees: "
        f"**{sample['n_trees_comparable_total']:,}** of "
        f"**{sample['n_trees_total']:,}**. Excluded bucked volume: "
        f"**{_fmt(sample['bucked_log_m3_excluded'], 2)} m³**.\n\n"
    )

    lines.append("## 2. Sort TVD and overlap\n\n")
    lines.append(
        "TVD is half the absolute difference between pilot and TIPSY industrial-sort "
        "share vectors; overlap is `sum(min(pilot, tipsy)) = 1 - TVD`. Shares are "
        "volume-weighted for the pilot and pilot-volume-weighted for TIPSY.\n\n"
    )
    lines.append("### Primary managed-AU match\n\n")
    lines.append("| Metric | Value |\n|---|---|\n")
    lines.append(f"| Comparable plots | {primary['n_plots']:,} |\n")
    lines.append(
        f"| Managed AUs with comparable plots | {primary['n_au']:,} of "
        f"{sample['n_au_tipsy_curves']:,} TIPSY AUs |\n"
    )
    lines.append(f"| Mean per-plot TVD | {_fmt(primary['plot_tvd']['mean_tvd'])} |\n")
    lines.append(f"| Median per-plot TVD | {_fmt(primary['plot_tvd']['median_tvd'])} |\n")
    lines.append(
        f"| Per-plot TVD range | {_fmt(primary['plot_tvd']['min_tvd'])} to "
        f"{_fmt(primary['plot_tvd']['max_tvd'])} |\n"
    )
    lines.append(
        f"| Volume-weighted per-plot TVD | {_fmt(primary['plot_tvd']['volume_weighted_tvd'])} |\n"
    )
    lines.append(f"| Pooled-share TVD | {_fmt(primary['pooled_tvd'])} |\n")
    lines.append(f"| Mean per-plot overlap | {_fmt(primary['plot_tvd']['mean_overlap'])} |\n")
    lines.append(f"| Pooled-share overlap | {_fmt(primary['pooled_overlap'])} |\n\n")
    lines.append(
        f"Extended species-fallback set: {extended['n_plots']:,} plots, "
        f"mean per-plot TVD {_fmt(extended['plot_tvd']['mean_tvd'])}, "
        f"pooled TVD {_fmt(extended['pooled_tvd'])}. "
        "These rows are secondary and are not the primary AU-matched result.\n\n"
    )

    aggregate = sort_comparison[sort_comparison["scope"] == "aggregate_primary_au"].copy()
    aggregate = aggregate.sort_values("pilot_share", ascending=False)
    top = aggregate.head(10)
    lines.append("### Largest pilot sort shares\n\n")
    lines.append(
        _markdown_table(
            top,
            ["code", "pilot_share", "tipsy_share", "difference", "abs_difference"],
        )
    )
    lines.append("\n")
    largest_differences = aggregate.sort_values("abs_difference", ascending=False).head(6)
    lines.append("### Largest absolute share differences\n\n")
    lines.append(
        _markdown_table(
            largest_differences,
            ["code", "pilot_share", "tipsy_share", "difference", "abs_difference"],
        )
    )
    lines.append(
        "\nThese are model-definition differences between the value-maximising "
        "dimension bucker and the TIPSY industrial-log model; they are not fitted "
        "corrections.\n\n"
    )

    au_table = au_summary.sort_values("n_plots", ascending=False).head(16).copy()
    lines.append("### Per-AU summary\n\n")
    lines.append(
        _markdown_table(
            au_table,
            [
                "au_id",
                "n_plots",
                "n_logs",
                "bucked_log_m3ha",
                "tipsy_industrial_total_m3ha",
                "mean_plot_tvd",
                "pooled_tvd",
                "mean_plot_overlap",
                "bias_m3ha",
                "rmse_m3ha",
                "relative_bias",
            ],
        )
    )
    lines.append("\n")

    lines.append("## 3. Volume comparison\n\n")
    lines.append(
        "Bucked log volume is expanded with `PHF_TREE` to m³/ha and compared with the "
        "TIPSY industrial-log total at the same AU and age. Sums are m³/ha summed across "
        "plots, not absolute cubic metres. This is industrial sort recovery, not a "
        "merchantable-volume validation.\n\n"
    )
    lines.append("| Metric | Value |\n|---|---|\n")
    lines.append(f"| n plots | {volume['n']:,} |\n")
    lines.append(f"| Bucked volume (m³/ha-sum) | {_fmt(volume['observed_sum_m3ha'])} |\n")
    lines.append(f"| TIPSY industrial total (m³/ha-sum) | {_fmt(volume['predicted_sum_m3ha'])} |\n")
    lines.append(f"| Mean bias (TIPSY - bucked, m³/ha) | {_fmt(volume['mean_bias_m3ha'])} |\n")
    lines.append(f"| MAE (m³/ha) | {_fmt(volume['mae_m3ha'])} |\n")
    lines.append(f"| RMSE (m³/ha) | {_fmt(volume['rmse_m3ha'])} |\n")
    lines.append(f"| Relative bias | {_fmt(volume['relative_bias'])} |\n")
    lines.append(f"| Ratio (TIPSY / bucked) | {_fmt(volume['ratio_predicted_observed'])} |\n")
    lines.append(f"| Raw bucked log volume (m³) | {_fmt(primary['bucked_log_m3'], 2)} |\n\n")
    lines.append(
        "The ratio is not interpreted as a recovery factor because the two surfaces "
        "use different minimum top diameters, length rules, and utilisation assumptions.\n\n"
    )

    lines.append("## 4. Evidence and caveats\n\n")
    lines.append("| Element | Evidence tier |\n|---|---|\n")
    lines.append("| Pilot sort codes | `dimension_only` |\n")
    lines.append("| TIPSY industrial-log curves | `model_output` |\n")
    lines.append("| Comparison | `derived` |\n\n")
    lines.append(
        "- No pre-existing industrial-sort TVD/overlap tolerance was found. The result is "
        "reported as metrics only; `pass_fail = not_assessed_no_pre_registered_tolerance`.\n"
    )
    lines.append(
        "- The TIPSY industrial-log curves are model output from the TSA24 BTC /TSR run, "
        "not field grade. They carry no defect, sweep, knot, rot, or value information.\n"
    )
    lines.append(
        "- Multiple PSP plots can share one managed AU curve, so per-plot RMSE is not an "
        "independent model-error estimate.\n"
    )
    lines.append(
        "- The canonical `tsa24_grade_curves/managed_au_industrial_sorts.csv` is empty in "
        "the current checkout because the refreshed BTC compile requested only log-grades "
        "and residual-fibre. The validation used the separately compiled industrial-logs "
        "table recorded in the manifest and fails closed if no table is available.\n"
    )
    lines.append(
        "- No age is imputed. Plots without a stand age remain excluded; the pilot "
        "benchmark age is used only where that plot is explicitly present in the pilot "
        "table.\n\n"
    )

    lines.append("## 5. Artifacts\n\n")
    for name in (
        "plot_sort_comparison.csv",
        "au_sort_summary.csv",
        "sort_comparison.csv",
        "metrics.json",
        "summary.json",
        "manifest.json",
        "VALIDATION_REPORT.md",
    ):
        lines.append(f"- `{name}`\n")
    lines.append("\n")
    lines.append("Inputs and hashes are recorded in `manifest.json`.\n")
    return "".join(lines)


# ---------------------------------------------------------------------------
# Orchestration


def _git_state() -> dict[str, Any]:
    def _run(*args: str) -> str | None:
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError:
            return None
        if result.returncode != 0:
            return None
        return result.stdout.strip()

    return {
        "head": _run("rev-parse", "HEAD"),
        "branch": _run("rev-parse", "--abbrev-ref", "HEAD"),
        "status_porcelain": _run("status", "--porcelain"),
    }


def generate_validation(
    *,
    logs_path: Path,
    trees_path: Path,
    tipsy_sorts_path: Path | None,
    coverage_path: Path,
    out_dir: Path,
    pilot_benchmark_path: Path | None = None,
    femic_root: Path | None = None,
    tolerance: float | None = None,
) -> dict[str, Any]:
    """Run the validation and write the v0 artifacts."""
    logs_path = Path(logs_path)
    trees_path = Path(trees_path)
    coverage_path = Path(coverage_path)
    out_dir = Path(out_dir)
    pilot_benchmark_path = Path(pilot_benchmark_path) if pilot_benchmark_path else None
    femic_root = Path(femic_root) if femic_root else None
    generated_utc = _utc_now()

    resolved_tipsy_path, tipsy_role = resolve_tipsy_sorts_path(tipsy_sorts_path)
    logs = load_pilot_logs(logs_path)
    trees = load_trees(trees_path)
    coverage = load_coverage(coverage_path)
    tipsy = load_tipsy_sorts(resolved_tipsy_path)
    pilot_benchmark = load_pilot_benchmark(pilot_benchmark_path)

    by_au, by_species = build_tipsy_curves(tipsy, coverage)
    plots = build_plot_attributes(
        trees,
        coverage,
        pilot_benchmark=pilot_benchmark,
        femic_root=femic_root,
        available_species=by_species.keys(),
    )
    plot_comparison = build_plot_comparison(plots, logs, trees, by_au, by_species)
    au_summary = build_au_summary(plot_comparison)
    metrics = compute_metrics(
        plot_comparison,
        au_summary,
        tipsy_source_path=resolved_tipsy_path,
        tipsy_source_role=tipsy_role,
        n_plots_total=len(plots),
        n_logs_total=len(logs),
        n_trees_total=int(logs["tree_id"].nunique()),
        n_au_tipsy=int(tipsy["au_id"].nunique()),
    )

    if tolerance is not None:
        observed = float(metrics["primary_au_match"]["pooled_tvd"])
        metrics["tolerance"] = float(tolerance)
        metrics["pass_fail"] = "pass" if observed <= float(tolerance) else "fail"
        metrics["tolerance_basis"] = "explicit_caller_supplied"
    else:
        metrics["pass_fail"] = "not_assessed_no_pre_registered_tolerance"

    sort_comparison = build_sort_comparison(plot_comparison, metrics)
    output_check = _validate_outputs(plot_comparison, au_summary, sort_comparison, metrics)
    metrics["output_check"] = output_check

    summary = {
        "validation": LABEL,
        "label": LABEL,
        "grade_status": GRADE_STATUS,
        "official_grade_validation": False,
        "generated_utc": generated_utc,
        "status": metrics["status"],
        "pass_fail": metrics["pass_fail"],
        "tolerance": metrics["tolerance"],
        "sample": metrics["sample"],
        "primary_au_match": {
            "n_plots": metrics["primary_au_match"]["n_plots"],
            "n_au": metrics["primary_au_match"]["n_au"],
            "pooled_tvd": metrics["primary_au_match"]["pooled_tvd"],
            "pooled_overlap": metrics["primary_au_match"]["pooled_overlap"],
            "plot_tvd": metrics["primary_au_match"]["plot_tvd"],
            "volume": metrics["primary_au_match"]["volume"],
        },
        "extended_with_species_fallback": {
            "n_plots": metrics["extended_with_species_fallback"].get("n_plots", 0),
            "pooled_tvd": metrics["extended_with_species_fallback"].get("pooled_tvd"),
            "pooled_overlap": metrics["extended_with_species_fallback"].get("pooled_overlap"),
            "plot_tvd": metrics["extended_with_species_fallback"].get("plot_tvd"),
        },
        "tipsy_source": metrics["tipsy_source"],
        "join": metrics["join"],
        "evidence_tiers": metrics["evidence_tiers"],
        "labels": metrics["labels"],
        "caveats": metrics["caveats"],
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(plot_comparison, out_dir / "plot_sort_comparison.csv")
    _write_csv(au_summary, out_dir / "au_sort_summary.csv")
    _write_csv(sort_comparison, out_dir / "sort_comparison.csv")
    (out_dir / "metrics.json").write_text(
        json.dumps(_jsonable(metrics), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (out_dir / "summary.json").write_text(
        json.dumps(_jsonable(summary), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    report = build_report(
        generated_utc=generated_utc,
        summary=metrics,
        au_summary=au_summary,
        sort_comparison=sort_comparison,
        tipsy_source_role=tipsy_role,
    )
    (out_dir / "VALIDATION_REPORT.md").write_text(report, encoding="utf-8")

    output_paths = [
        out_dir / "plot_sort_comparison.csv",
        out_dir / "au_sort_summary.csv",
        out_dir / "sort_comparison.csv",
        out_dir / "metrics.json",
        out_dir / "summary.json",
        out_dir / "VALIDATION_REPORT.md",
    ]
    missing_outputs = [str(path) for path in output_paths if not path.exists()]
    if missing_outputs:
        raise SortValidationError("output write check failed: " + ", ".join(missing_outputs))

    input_records = [
        {
            "label": label,
            "path": str(path.relative_to(REPO_ROOT)) if path.is_relative_to(REPO_ROOT) else str(path),
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }
        for label, path in (
            ("bucked_logs", logs_path),
            ("selected_trees", trees_path),
            ("au_grade_coverage", coverage_path),
            ("tipsy_industrial_sorts", resolved_tipsy_path),
        )
    ]
    if pilot_benchmark_path is not None and pilot_benchmark_path.exists():
        input_records.append(
            {
                "label": "pilot_grade_benchmark",
                "path": str(pilot_benchmark_path.relative_to(REPO_ROOT))
                if pilot_benchmark_path.is_relative_to(REPO_ROOT)
                else str(pilot_benchmark_path),
                "bytes": pilot_benchmark_path.stat().st_size,
                "sha256": _sha256(pilot_benchmark_path),
            }
        )
    if femic_root is not None:
        for label, path in (
            ("femic_03_input", femic_root / "data" / "03_input-tsa24.csv"),
            ("femic_au_table", femic_root / "data" / "model_input_bundle" / "au_table.csv"),
        ):
            if path.exists():
                input_records.append(
                    {
                        "label": label,
                        "path": str(path),
                        "bytes": path.stat().st_size,
                        "sha256": _sha256(path),
                    }
                )
    output_records = [
        {
            "label": path.stem,
            "path": path.name,
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
            "rows": len(pd.read_csv(path)) if path.suffix == ".csv" else None,
        }
        for path in output_paths
    ]
    manifest = {
        "validation": LABEL,
        "label": LABEL,
        "grade_status": GRADE_STATUS,
        "official_grade_validation": False,
        "created_utc": generated_utc,
        "status": "complete",
        "tipsy_sorts_source": {
            "path": str(resolved_tipsy_path),
            "role": tipsy_role,
            "canonical_expected_path": str(DEFAULT_TIPSY_SORTS),
            "canonical_note": (
                "The canonical managed_au_industrial_sorts.csv has no data rows in this "
                "checkout because the refreshed BTC compile requested only log-grades "
                "and residual-fibre. The regenerated table was produced from the same "
                "TSA24 BTC model with the industrial-logs bank."
            ),
        },
        "inputs": input_records,
        "outputs": output_records,
        "coverage": {
            "n_plots_total": len(plots),
            "n_plots_comparable_au": int(metrics["sample"]["n_plots_comparable_au"]),
            "n_plots_comparable_species": int(
                metrics["sample"]["n_plots_comparable_species"]
            ),
            "n_logs_total": len(logs),
            "n_trees_total": int(logs["tree_id"].nunique()),
            "n_au_curve": int(tipsy["au_id"].nunique()),
            "n_species_curve": len(by_species),
            "age_min": float(tipsy["age"].min()),
            "age_max": float(tipsy["age"].max()),
            "sort_codes": list(INDUSTRIAL_ORDER),
            "sort_aliases": dict(SORT_ALIASES),
        },
        "evidence": {
            "pilot_sorts": "dimension_only",
            "tipsy_industrial_sorts": "model_output",
            "comparison": "derived",
            "grade_status": GRADE_STATUS,
        },
        "output_check": output_check,
        "git": _git_state(),
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(_jsonable(manifest), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    if value is pd.NA:
        return None
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate TSA24 pilot industrial sort shares against TIPSY "
            "industrial-log curves. This is not an official grade validation."
        )
    )
    parser.add_argument("--logs", type=Path, default=DEFAULT_LOGS)
    parser.add_argument("--trees", type=Path, default=DEFAULT_TREES)
    parser.add_argument("--tipsy-sorts", type=Path, default=None)
    parser.add_argument("--coverage", type=Path, default=DEFAULT_COVERAGE)
    parser.add_argument(
        "--pilot-benchmark", type=Path, default=DEFAULT_PILOT_BENCHMARK
    )
    parser.add_argument(
        "--femic-root",
        type=Path,
        default=None,
        help=(
            "Optional FEMIC TSA24 instance root for the authoritative au_table join; "
            "the compiled coverage table is used when omitted."
        ),
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=None,
        help=(
            "Optional pre-existing pooled-TVD tolerance. Omit to report metrics only; "
            "do not tune this value."
        ),
    )
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        summary = generate_validation(
            logs_path=args.logs,
            trees_path=args.trees,
            tipsy_sorts_path=args.tipsy_sorts,
            coverage_path=args.coverage,
            out_dir=args.out_dir,
            pilot_benchmark_path=args.pilot_benchmark,
            femic_root=args.femic_root,
            tolerance=args.tolerance,
        )
    except SortValidationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(_jsonable(summary), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
