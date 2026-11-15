"""Area-control harvest scheduling for a compiled WS4 TSA dataset.

The rule is the one documented in
``femic-tsa29-instance/docs/AREA_CONTROL_HEURISTIC.md``.  It is deliberately
small: compute a managed area, derive rotation age from the model's own
area-weighted yield curve, set an area quota, and build an oldest-first
schedule period by period.  It is not a volume target and it is not an
optimizer.

Two implementation choices are explicit in the YAML contract because the
reference instance and the generic factory need different readings:

``quota_scope``
    ``global`` (default) uses one quota for the whole managed land base, which
    is the documented TSA24/TSA29 reference construction.  ``per_au`` applies
    ``Q_AU`` separately to each analysis unit.

``eligibility``
    ``operable`` (default) lets a block remain a candidate after its earliest
    operable year, which is required to carry an unfilled quota into later
    periods.  ``earliest_only`` accepts a block only in the period containing
    its earliest operable year.

The schedule is single-entry: one row per block.  Multi-entry chains are an
optimizer concern and are intentionally outside this minimal lane.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

REQUIRED_TABLES = ("fragments", "blocks", "tracks", "treatments", "features", "curves")
REQUIRED_COLUMNS = {
    "fragments": ("fragment_id", "block_id", "area_ha", "age", "track_id"),
    "blocks": ("block_id", "track_id", "offset", "area_ha"),
    "tracks": ("track_id", "name"),
    "treatments": ("track_id", "begin", "end", "treatment"),
    "features": ("track_id", "label", "curve_id"),
    "curves": ("curve_id", "x", "y"),
}
ALLOWED_CONFIG_KEYS = {
    "au_regex",
    "volume_label",
    "treatment",
    "managed_token",
    "period_width",
    "horizon_years",
    "quota_factor",
    "min_age",
    "quota_scope",
    "eligibility",
}


class AreaControlError(RuntimeError):
    """Raised when the dataset or configuration cannot produce a defensible schedule."""


@dataclass(frozen=True)
class AreaControlConfig:
    """Parsed and validated area-control configuration."""

    au_regex: str
    volume_label: str
    treatment: str
    managed_token: str
    period_width: int = 10
    horizon_years: int = 100
    quota_factor: float = 1.0
    min_age: int | None = None
    quota_scope: str = "global"
    eligibility: str = "operable"

    @classmethod
    def from_yaml(cls, path: Path) -> AreaControlConfig:
        """Load a configuration file and fail loudly on typos or bad values."""
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise AreaControlError(f"{path}: configuration root must be a mapping")

        unknown = sorted(set(raw) - ALLOWED_CONFIG_KEYS)
        if unknown:
            raise AreaControlError(
                f"{path}: unknown key(s) {', '.join(unknown)}; "
                f"allowed keys are {', '.join(sorted(ALLOWED_CONFIG_KEYS))}"
            )

        required = ("au_regex", "volume_label", "treatment", "managed_token")
        missing = [key for key in required if key not in raw]
        if missing:
            raise AreaControlError(f"{path}: missing required key(s): {', '.join(missing)}")

        period_width = int(raw.get("period_width", 10))
        horizon_years = int(raw.get("horizon_years", 100))
        quota_factor = float(raw.get("quota_factor", 1.0))
        min_age_raw = raw.get("min_age")
        min_age = None if min_age_raw is None else int(min_age_raw)
        quota_scope = str(raw.get("quota_scope", "global"))
        eligibility = str(raw.get("eligibility", "operable"))

        if period_width <= 0:
            raise AreaControlError(f"{path}: period_width must be positive")
        if horizon_years <= 0:
            raise AreaControlError(f"{path}: horizon_years must be positive")
        if quota_factor <= 0:
            raise AreaControlError(f"{path}: quota_factor must be positive")
        if min_age is not None and min_age < 0:
            raise AreaControlError(f"{path}: min_age must be non-negative")
        if quota_scope not in {"global", "per_au"}:
            raise AreaControlError(f"{path}: quota_scope must be 'global' or 'per_au'")
        if eligibility not in {"operable", "earliest_only"}:
            raise AreaControlError(
                f"{path}: eligibility must be 'operable' or 'earliest_only'"
            )

        regex = str(raw["au_regex"])
        try:
            compiled = re.compile(regex)
        except re.error as error:
            raise AreaControlError(f"{path}: invalid au_regex: {error}") from error
        if compiled.groups != 1:
            raise AreaControlError(
                f"{path}: au_regex must contain exactly one capture group, "
                f"found {compiled.groups}"
            )

        return cls(
            au_regex=regex,
            volume_label=str(raw["volume_label"]),
            treatment=str(raw["treatment"]),
            managed_token=str(raw["managed_token"]),
            period_width=period_width,
            horizon_years=horizon_years,
            quota_factor=quota_factor,
            min_age=min_age,
            quota_scope=quota_scope,
            eligibility=eligibility,
        )


@dataclass(frozen=True)
class DatasetTables:
    """The six tables the heuristic needs, already checked for required columns."""

    fragments: pd.DataFrame
    blocks: pd.DataFrame
    tracks: pd.DataFrame
    treatments: pd.DataFrame
    features: pd.DataFrame
    curves: pd.DataFrame


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_columns(frame: pd.DataFrame, table: str, columns: Sequence[str]) -> None:
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise AreaControlError(f"{table}.parquet is missing column(s): {', '.join(missing)}")


def load_dataset(dataset: Path) -> DatasetTables:
    """Read the six required parquet tables and validate their shape."""
    if not dataset.is_dir():
        raise AreaControlError(f"dataset directory does not exist: {dataset}")

    frames: dict[str, pd.DataFrame] = {}
    for table in REQUIRED_TABLES:
        path = dataset / f"{table}.parquet"
        if not path.is_file():
            raise AreaControlError(f"missing required dataset table: {path}")
        frame = pd.read_parquet(path)
        _require_columns(frame, table, REQUIRED_COLUMNS[table])
        frames[table] = frame
    return DatasetTables(**frames)


def resolve_tracks(tracks: pd.DataFrame, config: AreaControlConfig) -> pd.DataFrame:
    """Attach ``au`` and ``managed`` to every track; fail on unparseable names."""
    names = tracks["name"].astype(str)
    au = names.str.extract(config.au_regex, expand=False)
    if au.isna().any():
        bad = tracks.loc[au.isna(), ["track_id", "name"]].head(10)
        raise AreaControlError(
            "track name(s) do not match au_regex "
            f"{config.au_regex!r}:\n{bad.to_string(index=False)}"
        )
    if (au.astype(str).str.strip() == "").any():
        raise AreaControlError("au_regex produced an empty analysis unit")

    managed = names.str.contains(config.managed_token, regex=False)
    result = tracks.copy()
    result["au"] = au.astype(str)
    result["managed"] = managed
    return result


def resolve_treatments(
    tracks: pd.DataFrame,
    treatments: pd.DataFrame,
    config: AreaControlConfig,
) -> pd.DataFrame:
    """Attach one consistent treatment window to every managed track."""
    selected = treatments[treatments["treatment"] == config.treatment].copy()
    if selected.empty:
        raise AreaControlError(
            f"no treatment rows with treatment={config.treatment!r}"
        )

    counts = selected.groupby("track_id").agg(
        begin_count=("begin", "nunique"),
        end_count=("end", "nunique"),
    )
    conflicting = counts[(counts > 1).any(axis=1)]
    if not conflicting.empty:
        raise AreaControlError(
            f"track(s) have conflicting {config.treatment!r} windows: "
            f"{', '.join(map(str, conflicting.index[:10]))}"
        )

    selected = selected.drop_duplicates(["track_id", "begin", "end"])
    merged = tracks.merge(selected[["track_id", "begin", "end"]], on="track_id", how="left")
    managed = merged[merged["managed"]]
    if managed[["begin", "end"]].isna().any().any():
        missing = managed.loc[managed[["begin", "end"]].isna().any(axis=1), "track_id"]
        raise AreaControlError(
            f"managed track(s) have no {config.treatment!r} treatment: "
            f"{', '.join(map(str, missing.head(10)))}"
        )
    return merged


def resolve_curves(
    features: pd.DataFrame,
    curves: pd.DataFrame,
    tracks: pd.DataFrame,
    config: AreaControlConfig,
) -> tuple[dict[int, int], dict[int, tuple[np.ndarray, np.ndarray]]]:
    """Map every managed track to its volume curve and validate the curve table."""
    rows = features[features["label"] == config.volume_label]
    if rows.empty:
        raise AreaControlError(f"no feature rows with label={config.volume_label!r}")

    per_track = rows.groupby("track_id")["curve_id"].nunique()
    if (per_track > 1).any():
        raise AreaControlError(
            f"feature label {config.volume_label!r} maps to multiple curves for track(s): "
            f"{', '.join(map(str, per_track[per_track > 1].index[:10]))}"
        )
    curve_by_track = {
        int(row.track_id): int(row.curve_id)
        for row in rows.drop_duplicates("track_id").itertuples(index=False)
    }

    managed_tracks = tracks.loc[tracks["managed"], "track_id"]
    missing = sorted(set(managed_tracks.astype(int)) - set(curve_by_track))
    if missing:
        raise AreaControlError(
            f"managed track(s) have no {config.volume_label!r} curve: "
            f"{', '.join(map(str, missing[:10]))}"
        )

    curve_map: dict[int, tuple[np.ndarray, np.ndarray]] = {}
    needed = set(curve_by_track.values())
    for curve_id, group in curves[curves["curve_id"].isin(needed)].groupby("curve_id"):
        ordered = group.sort_values("x")
        if len(ordered) < 2:
            raise AreaControlError(f"curve {curve_id} has fewer than two points")
        if ordered["x"].duplicated().any():
            raise AreaControlError(f"curve {curve_id} has duplicate x values")
        if not np.isfinite(ordered[["x", "y"]].to_numpy(dtype=float)).all():
            raise AreaControlError(f"curve {curve_id} has non-finite values")
        curve_map[int(curve_id)] = (
            ordered["x"].to_numpy(dtype=float),
            ordered["y"].to_numpy(dtype=float),
        )

    missing_curves = sorted(set(curve_by_track.values()) - set(curve_map))
    if missing_curves:
        raise AreaControlError(
            f"feature reference(s) point at missing curve(s): "
            f"{', '.join(map(str, missing_curves[:10]))}"
        )
    return curve_by_track, curve_map


def build_managed_blocks(
    fragments: pd.DataFrame,
    blocks: pd.DataFrame,
    tracks: pd.DataFrame,
    curve_by_track: Mapping[int, int],
    config: AreaControlConfig,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Resolve per-block AU, managed area, treatment window, and initial age."""
    fragment_tracks = fragments.merge(
        tracks[["track_id", "au", "managed", "begin", "end"]],
        on="track_id",
        how="left",
        validate="many_to_one",
    )
    if fragment_tracks[["au", "managed"]].isna().any().any():
        raise AreaControlError("fragment references a track with no parsed AU")

    managed = fragment_tracks[fragment_tracks["managed"]].copy()
    if managed.empty:
        raise AreaControlError(f"no fragments on tracks containing {config.managed_token!r}")
    managed["curve_id"] = managed["track_id"].map(curve_by_track)
    if managed["curve_id"].isna().any():
        raise AreaControlError("managed fragment has no volume curve")

    au_counts = managed.groupby("block_id")["au"].nunique()
    multi_au = au_counts[au_counts > 1]
    if not multi_au.empty:
        raise AreaControlError(
            "block(s) map to more than one AU: "
            f"{', '.join(map(str, multi_au.index[:10]))}"
        )

    windows = managed.groupby("block_id").agg(
        begin_count=("begin", "nunique"),
        end_count=("end", "nunique"),
        area_ha=("area_ha", "sum"),
        au=("au", "first"),
        n_fragments=("fragment_id", "size"),
        begin=("begin", "first"),
        end=("end", "first"),
    )
    conflicting = windows[(windows["begin_count"] > 1) | (windows["end_count"] > 1)]
    if not conflicting.empty:
        raise AreaControlError(
            "managed block(s) have conflicting treatment windows across tracks: "
            f"{', '.join(map(str, conflicting.index[:10]))}"
        )

    block_ages = blocks[["block_id", "offset", "area_ha"]].rename(
        columns={"offset": "age0", "area_ha": "block_area_ha"}
    )
    if block_ages["block_id"].duplicated().any():
        raise AreaControlError("blocks.parquet has duplicate block_id values")

    block_frame = windows.join(block_ages.set_index("block_id"), how="left")
    if block_frame["age0"].isna().any():
        missing = block_frame.index[block_frame["age0"].isna()]
        raise AreaControlError(
            f"managed block(s) are absent from blocks.parquet: "
            f"{', '.join(map(str, missing[:10]))}"
        )

    ages = managed.merge(block_ages[["block_id", "age0"]], on="block_id", how="left")
    bad_age = ages[ages["age"] != ages["age0"]]
    if not bad_age.empty:
        sample = bad_age[["block_id", "age", "age0"]].head(5)
        raise AreaControlError(
            "fragment age and block offset disagree; the heuristic needs one canonical age:\n"
            f"{sample.to_string(index=False)}"
        )

    block_frame["age0"] = block_frame["age0"].astype(int)
    effective_begin = block_frame["begin"].astype(float)
    if config.min_age is not None:
        effective_begin = np.maximum(effective_begin, float(config.min_age))
    block_frame["y"] = np.maximum(0.0, effective_begin - block_frame["age0"]).astype(int)
    block_frame["end_op"] = np.where(
        block_frame["end"].astype(float) <= 0,
        np.inf,
        block_frame["end"].astype(float) - block_frame["age0"],
    )
    if (block_frame["end_op"] < block_frame["y"]).any():
        bad = block_frame.index[block_frame["end_op"] < block_frame["y"]]
        raise AreaControlError(
            "treatment window closes before it opens for block(s): "
            f"{', '.join(map(str, bad[:10]))}"
        )

    block_frame = block_frame.reset_index()
    block_frame = block_frame[
        ["block_id", "au", "area_ha", "age0", "begin", "end", "y", "end_op", "n_fragments"]
    ].copy()
    block_frame["block_id"] = block_frame["block_id"].astype(int)
    block_frame["area_ha"] = block_frame["area_ha"].astype(float)
    block_frame["scheduled"] = False

    fragment_frame = managed[
        ["fragment_id", "block_id", "area_ha", "age", "curve_id", "au", "track_id"]
    ].copy()
    return block_frame, fragment_frame


def _area_weighted_curve(
    fragments: pd.DataFrame,
    curve_map: Mapping[int, tuple[np.ndarray, np.ndarray]],
    ages: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    weights = fragments.groupby("curve_id")["area_ha"].sum()
    total_area = float(weights.sum())
    if total_area <= 0:
        raise AreaControlError("a managed curve group has no area")
    volume = np.zeros_like(ages)
    for curve_id, weight in weights.items():
        x, y = curve_map[int(curve_id)]
        volume += float(weight) * np.interp(ages, x, y, left=0.0, right=float(y[-1]))
    volume /= total_area
    return volume, volume / ages


def build_au_metrics(
    blocks: pd.DataFrame,
    fragments: pd.DataFrame,
    curve_map: Mapping[int, tuple[np.ndarray, np.ndarray]],
    config: AreaControlConfig,
) -> tuple[pd.DataFrame, dict[str, float]]:
    """Compute per-AU metrics plus the land-base area-weighted rotation."""
    max_age = max(int(x.max()) for x, _ in curve_map.values())
    if max_age < 1:
        raise AreaControlError("volume curves have no positive ages")
    ages = np.arange(1, max_age + 1, dtype=float)

    records: list[dict[str, Any]] = []
    for au, group in fragments.groupby("au", sort=True):
        volume, mai = _area_weighted_curve(group, curve_map, ages)
        rotation_index = int(np.argmax(mai))
        rotation_age = int(ages[rotation_index])
        mai_at_rotation = float(mai[rotation_index])
        if not np.isfinite(mai_at_rotation) or mai_at_rotation <= 0:
            raise AreaControlError(f"AU {au} has a non-positive maximum MAI")

        area = float(blocks.loc[blocks["au"] == au, "area_ha"].sum())
        quota = area * config.period_width / rotation_age * config.quota_factor
        records.append(
            {
                "au": str(au),
                "A_ha": area,
                "R_years": rotation_age,
                "Q_ha": quota,
                "MAI_R": mai_at_rotation,
                "expected_volume_m3_per_period": area
                * config.period_width
                * mai_at_rotation,
            }
        )

    metrics = pd.DataFrame.from_records(records)
    if metrics.empty:
        raise AreaControlError("no analysis units with managed area")

    total_area = float(metrics["A_ha"].sum())
    _, landbase_mai = _area_weighted_curve(fragments, curve_map, ages)
    landbase_rotation = int(ages[int(np.argmax(landbase_mai))])
    landbase_mai_r = float(landbase_mai[landbase_rotation - 1])
    landbase = {
        "A_ha": total_area,
        "R_years": float(landbase_rotation),
        "Q_ha": total_area
        * config.period_width
        / landbase_rotation
        * config.quota_factor,
        "MAI_R": landbase_mai_r,
        "expected_volume_m3_per_period": total_area
        * config.period_width
        * landbase_mai_r,
    }
    return metrics, landbase

def _eligible_mask(
    blocks: pd.DataFrame,
    start: int,
    end: int,
    config: AreaControlConfig,
) -> pd.Series:
    if config.eligibility == "earliest_only":
        return (
            (blocks["y"] >= start)
            & (blocks["y"] <= end)
            & (blocks["y"] < config.horizon_years)
        )
    return (
        (blocks["y"] <= end)
        & (blocks["end_op"] >= start)
        & (blocks["y"] < config.horizon_years)
    )


def _oldest_first(blocks: pd.DataFrame) -> pd.DataFrame:
    return blocks.sort_values(
        ["age0", "block_id"], ascending=[False, True], kind="mergesort"
    )


def _take_until(candidates: pd.DataFrame, target_ha: float) -> tuple[pd.DataFrame, float]:
    if target_ha <= 0 or candidates.empty:
        return candidates.iloc[0:0].copy(), 0.0
    areas = candidates["area_ha"].to_numpy(dtype=float)
    cumulative = np.cumsum(areas)
    stop = int(np.searchsorted(cumulative, target_ha, side="left")) + 1
    stop = min(stop, len(candidates))
    selected = candidates.iloc[:stop].copy()
    return selected, float(selected["area_ha"].sum())


def _prime_oldest(
    blocks: pd.DataFrame,
    config: AreaControlConfig,
    target_ha: float,
) -> pd.DataFrame:
    """Choose the oldest still-unscheduled blocks, ignoring the period window."""
    candidates = blocks[blocks["y"] < config.horizon_years]
    selected, _ = _take_until(_oldest_first(candidates), target_ha)
    return selected


def _assign_offsets(
    selected: pd.DataFrame,
    start: int,
    config: AreaControlConfig,
    primed: bool,
) -> pd.DataFrame:
    """Attach the in-period offset and the resulting harvest age."""
    if selected.empty:
        return selected.copy()
    if primed:
        offsets = np.zeros(len(selected), dtype=int)
    elif config.eligibility == "earliest_only":
        offsets = selected["y"].to_numpy(dtype=int)
    else:
        offsets = np.maximum(start, selected["y"].to_numpy(dtype=int))
    result = selected.copy()
    result["offset"] = offsets.astype(int)
    result["age_at_harvest"] = result["age0"].astype(int) + result["offset"]
    return result


def _schedule_records(
    selected: pd.DataFrame, period: int, config: AreaControlConfig
) -> list[dict[str, Any]]:
    return [
        {
            "block_id": int(row.block_id),
            "treatment": config.treatment,
            "offset": int(row.offset),
            "period": period,
            "au": row.au,
            "area_ha": float(row.area_ha),
            "age_at_harvest": int(row.age_at_harvest),
        }
        for row in selected.itertuples(index=False)
    ]


def _diagnostic_record(
    period: int,
    au: str,
    metrics: pd.DataFrame,
    quota: float,
    target: float,
    selected: pd.DataFrame,
    n_eligible: int,
    carry: float,
    primed: bool,
) -> dict[str, Any]:
    metric = metrics.loc[metrics["au"] == au].iloc[0]
    selected = selected[selected["au"] == au]
    return {
        "period": period,
        "au": au,
        "A_ha": float(metric["A_ha"]),
        "R_years": int(metric["R_years"]),
        "Q_ha": float(quota),
        "MAI_R": float(metric["MAI_R"]),
        "target_ha": float(target),
        "selected_area_ha": float(selected["area_ha"].sum()),
        "shortfall_ha": max(0.0, float(target) - float(selected["area_ha"].sum())),
        "surplus_ha": max(0.0, float(selected["area_ha"].sum()) - float(target)),
        "carry_ha": float(carry),
        "n_eligible": int(n_eligible),
        "n_blocks": int(len(selected)),
        "oldest_age": (
            int(selected["age_at_harvest"].max()) if not selected.empty else np.nan
        ),
        "youngest_age": (
            int(selected["age_at_harvest"].min()) if not selected.empty else np.nan
        ),
        "primed": bool(primed),
    }


def build_schedule(
    blocks: pd.DataFrame,
    metrics: pd.DataFrame,
    config: AreaControlConfig,
    periods: int,
    *,
    landbase_quota: float | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    """Build the schedule and the per-AU/period diagnostics."""
    scheduled = blocks["scheduled"].to_numpy(copy=True)
    quota_by_au = dict(zip(metrics["au"], metrics["Q_ha"], strict=True))
    sum_au_quota = float(metrics["Q_ha"].sum())
    total_quota = (
        float(landbase_quota)
        if config.quota_scope == "global" and landbase_quota is not None
        else sum_au_quota
    )
    warnings: list[str] = []
    schedule_rows: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []

    if config.quota_scope == "global":
        carry = 0.0
        for period in range(1, periods + 1):
            start = (period - 1) * config.period_width
            end = period * config.period_width - 1
            available = blocks.loc[~scheduled]
            candidates = _oldest_first(
                available[_eligible_mask(available, start, end, config)]
            )
            target = total_quota + carry
            selected, _ = _take_until(candidates, target)
            primed = False
            if period == 1 and selected.empty and not scheduled.any():
                selected = _prime_oldest(available, config, target)
                primed = not selected.empty
                if primed:
                    warnings.append(
                        "period 1 had no normally eligible area; the oldest managed "
                        "block(s) were primed as an explicit exception"
                    )
            selected = _assign_offsets(selected, start, config, primed)
            if not selected.empty:
                schedule_rows.extend(_schedule_records(selected, period, config))
                scheduled[selected.index.to_numpy()] = True

            selected_area = float(selected["area_ha"].sum())
            carry = max(0.0, target - selected_area)
            eligible_counts = (
                candidates.groupby("au").size().to_dict() if not candidates.empty else {}
            )
            for au in sorted(quota_by_au):
                allocated = (
                    quota_by_au[au] / sum_au_quota * target if sum_au_quota > 0 else 0.0
                )
                diagnostics.append(
                    _diagnostic_record(
                        period,
                        au,
                        metrics,
                        quota_by_au[au],
                        allocated,
                        selected,
                        int(eligible_counts.get(au, 0)),
                        carry,
                        primed,
                    )
                )
    else:
        carry_by_au = {au: 0.0 for au in quota_by_au}
        au_is_scheduled = {
            au: (blocks["au"] == au).to_numpy() for au in quota_by_au
        }
        for period in range(1, periods + 1):
            start = (period - 1) * config.period_width
            end = period * config.period_width - 1
            for au in sorted(quota_by_au):
                available = blocks.loc[(blocks["au"] == au) & (~scheduled)]
                candidates = _oldest_first(
                    available[_eligible_mask(available, start, end, config)]
                )
                target = quota_by_au[au] + carry_by_au[au]
                selected, _ = _take_until(candidates, target)
                primed = False
                if period == 1 and selected.empty and not scheduled[au_is_scheduled[au]].any():
                    selected = _prime_oldest(available, config, target)
                    primed = not selected.empty
                    if primed:
                        warnings.append(
                            f"AU {au}: period 1 had no normally eligible area; primed"
                        )
                selected = _assign_offsets(selected, start, config, primed)
                if not selected.empty:
                    schedule_rows.extend(_schedule_records(selected, period, config))
                    scheduled[selected.index.to_numpy()] = True

                selected_area = float(selected["area_ha"].sum())
                carry_by_au[au] = max(0.0, target - selected_area)
                diagnostics.append(
                    _diagnostic_record(
                        period,
                        au,
                        metrics,
                        quota_by_au[au],
                        target,
                        selected,
                        int(len(candidates)),
                        carry_by_au[au],
                        primed,
                    )
                )

    schedule = pd.DataFrame.from_records(
        schedule_rows,
        columns=[
            "block_id",
            "treatment",
            "offset",
            "period",
            "au",
            "area_ha",
            "age_at_harvest",
        ],
    )
    diagnostics_frame = pd.DataFrame.from_records(diagnostics)
    if schedule.empty:
        warnings.append("no block was scheduled")
    return schedule, diagnostics_frame, warnings

def add_selected_volumes(
    diagnostics: pd.DataFrame,
    schedule: pd.DataFrame,
    fragments: pd.DataFrame,
    curve_map: Mapping[int, tuple[np.ndarray, np.ndarray]],
) -> pd.DataFrame:
    """Estimate harvested volume from the model's own managed volume curves."""
    if schedule.empty:
        diagnostics["selected_volume_m3"] = 0.0
        return diagnostics

    joined = fragments.merge(
        schedule[["block_id", "period", "offset"]], on="block_id", how="inner"
    )
    joined["harvest_age"] = joined["age"].astype(float) + joined["offset"].astype(float)
    joined["volume_m3"] = 0.0
    for curve_id, group in joined.groupby("curve_id"):
        x, y = curve_map[int(curve_id)]
        joined.loc[group.index, "volume_m3"] = group["area_ha"].to_numpy(
            dtype=float
        ) * np.interp(
            group["harvest_age"].to_numpy(dtype=float),
            x,
            y,
            left=0.0,
            right=float(y[-1]),
        )
    volumes = (
        joined.groupby(["period", "au"])["volume_m3"].sum().rename("selected_volume_m3")
    )
    return diagnostics.merge(volumes, on=["period", "au"], how="left").fillna(
        {"selected_volume_m3": 0.0}
    )


def _python_number(value: Any) -> Any:
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    return value


def _summary(
    dataset: Path,
    config_path: Path,
    config: AreaControlConfig,
    metrics: pd.DataFrame,
    landbase: Mapping[str, float],
    schedule: pd.DataFrame,
    diagnostics: pd.DataFrame,
    warnings: Sequence[str],
    periods: int,
) -> dict[str, Any]:
    total_area = float(metrics["A_ha"].sum())
    if config.quota_scope == "global":
        total_quota = float(landbase["Q_ha"])
        weighted_rotation = float(landbase["R_years"])
        expected_volume = float(landbase["expected_volume_m3_per_period"])
    else:
        total_quota = float(metrics["Q_ha"].sum())
        weighted_rotation = float((metrics["A_ha"] * metrics["R_years"]).sum() / total_area)
        expected_volume = float(metrics["expected_volume_m3_per_period"].sum())
    scheduled_area = float(schedule["area_ha"].sum()) if not schedule.empty else 0.0
    selected_volume = (
        float(diagnostics["selected_volume_m3"].sum()) if not diagnostics.empty else 0.0
    )

    if diagnostics.empty:
        final_carry = 0.0
    elif config.quota_scope == "global":
        final_carry = float(diagnostics.loc[diagnostics["period"].idxmax(), "carry_ha"])
    else:
        last = diagnostics.sort_values(["period", "au"]).groupby("au").tail(1)
        final_carry = float(last["carry_ha"].sum())

    per_au = []
    for row in metrics.itertuples(index=False):
        au_rows = diagnostics[diagnostics["au"] == row.au]
        scheduled_au = float(au_rows["selected_area_ha"].sum())
        per_au.append(
            {
                "au": row.au,
                "A_ha": float(row.A_ha),
                "R_years": int(row.R_years),
                "Q_ha": float(row.Q_ha),
                "MAI_R": float(row.MAI_R),
                "expected_volume_m3_per_period": float(row.expected_volume_m3_per_period),
                "scheduled_area_ha": scheduled_au,
                "scheduled_volume_m3": float(au_rows["selected_volume_m3"].sum()),
                "quota_area_over_horizon_ha": float(row.Q_ha * periods),
                "area_gap_ha": max(0.0, float(row.Q_ha * periods) - scheduled_au),
            }
        )

    period_detail = []
    for period, group in diagnostics.groupby("period", sort=True):
        selected_period = schedule[schedule["period"] == period]
        target_period = float(group["target_ha"].sum())
        selected_period_area = float(selected_period["area_ha"].sum())
        period_detail.append(
            {
                "period": int(period),
                "target_ha": target_period,
                "selected_area_ha": selected_period_area,
                "shortfall_ha": max(0.0, target_period - selected_period_area),
                "carry_ha": (
                    float(group["carry_ha"].iloc[0])
                    if config.quota_scope == "global"
                    else float(group["carry_ha"].sum())
                ),
                "n_blocks": int(len(selected_period)),
                "volume_m3": float(group["selected_volume_m3"].sum()),
            }
        )

    manifest = dataset / "manifest.json"
    summary = {
        "dataset": str(dataset),
        "dataset_manifest_sha256": _sha256(manifest) if manifest.is_file() else None,
        "config": str(config_path),
        "config_sha256": _sha256(config_path),
        "generated_utc": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "period_width": config.period_width,
        "horizon_years": config.horizon_years,
        "periods": periods,
        "quota_scope": config.quota_scope,
        "eligibility": config.eligibility,
        "treatment": config.treatment,
        "volume_label": config.volume_label,
        "managed_token": config.managed_token,
        "au_count": int(len(metrics)),
        "total_managed_area_ha": total_area,
        "weighted_rotation_age_years": weighted_rotation,
        "landbase_rotation_age_years": float(landbase["R_years"]),
        "landbase_MAI_R": float(landbase["MAI_R"]),
        "landbase_quota_ha_per_period": float(landbase["Q_ha"]),
        "total_quota_ha_per_period": total_quota,
        "expected_volume_m3_per_period": expected_volume,
        "schedule_rows": int(len(schedule)),
        "scheduled_area_ha": scheduled_area,
        "scheduled_volume_m3": selected_volume,
        "unscheduled_area_ha": max(0.0, total_area - scheduled_area),
        "final_carry_ha": final_carry,
        "warnings": list(warnings),
        "periods_detail": period_detail,
        "per_au": per_au,
    }
    return {key: _python_number(value) for key, value in summary.items()}

def run_area_control(
    dataset: Path,
    config_path: Path,
    out_dir: Path,
    periods: int | None = None,
    schedule_name: str = "schedule.csv",
) -> dict[str, Any]:
    """Run the heuristic end to end and write schedule, diagnostics, and summary."""
    dataset = Path(dataset)
    config_path = Path(config_path)
    out_dir = Path(out_dir)
    config = AreaControlConfig.from_yaml(config_path)
    tables = load_dataset(dataset)

    tracks = resolve_tracks(tables.tracks, config)
    tracks = resolve_treatments(tracks, tables.treatments, config)
    curve_by_track, curve_map = resolve_curves(
        tables.features, tables.curves, tracks, config
    )
    blocks, fragments = build_managed_blocks(
        tables.fragments, tables.blocks, tracks, curve_by_track, config
    )
    metrics, landbase = build_au_metrics(blocks, fragments, curve_map, config)

    period_count = (
        int(periods)
        if periods is not None
        else max(1, math.ceil(config.horizon_years / config.period_width))
    )
    if period_count <= 0:
        raise AreaControlError("periods must be positive")

    schedule, diagnostics, warnings = build_schedule(
        blocks,
        metrics,
        config,
        period_count,
        landbase_quota=landbase["Q_ha"],
    )
    diagnostics = add_selected_volumes(diagnostics, schedule, fragments, curve_map)
    summary = _summary(
        dataset,
        config_path,
        config,
        metrics,
        landbase,
        schedule,
        diagnostics,
        warnings,
        period_count,
    )
    summary["managed_block_count"] = int(len(blocks))

    out_dir.mkdir(parents=True, exist_ok=True)
    schedule_path = out_dir / schedule_name
    diagnostics_path = out_dir / "diagnostics.csv"
    summary_path = out_dir / "summary.json"

    output = schedule[["block_id", "treatment", "offset"]].sort_values(
        ["block_id", "offset"]
    )
    output.to_csv(schedule_path, index=False, lineterminator="\n")
    diagnostics.sort_values(["period", "au"]).to_csv(
        diagnostics_path, index=False, lineterminator="\n"
    )
    summary["schedule_sha256"] = _sha256(schedule_path)
    summary["diagnostics_sha256"] = _sha256(diagnostics_path)
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return summary


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, type=Path, help="compiled WS4 dataset")
    parser.add_argument("--config", required=True, type=Path, help="area-control YAML")
    parser.add_argument("--out", required=True, type=Path, help="output directory")
    parser.add_argument(
        "--periods",
        type=int,
        default=None,
        help="number of periods (default: horizon_years / period_width)",
    )
    parser.add_argument(
        "--schedule-name",
        default="schedule.csv",
        help="file name for the schedule inside --out (default: schedule.csv)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        summary = run_area_control(
            dataset=args.dataset,
            config_path=args.config,
            out_dir=args.out,
            periods=args.periods,
            schedule_name=args.schedule_name,
        )
    except AreaControlError as error:
        print(f"area_control: {error}")
        return 2
    print(
        "area_control: "
        f"{summary['au_count']} AUs, "
        f"A={summary['total_managed_area_ha']:.1f} ha, "
        f"R={summary['weighted_rotation_age_years']:.1f} yr, "
        f"Q={summary['total_quota_ha_per_period']:.1f} ha/period, "
        f"{summary['schedule_rows']} rows, "
        f"{summary['scheduled_area_ha']:.1f} ha scheduled"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
