#!/usr/bin/env python3
"""Build operable landbase proxy v3: v2 cases combined with the slope screen.

v3 does not create a new GIS raster.  It combines two published tables:

* operable proxy v2 ``base``, ``screen_core20``, and ``screen_core30``
  operable areas by province/ecoprovince; and
* the national 960 m slope screen forest-weighted ``share_gt15`` and
  ``share_gt30``.

For every v2 case x slope case it reports the pre-slope area, the implied
lost area, the post-slope remaining area, and the lost share.  The 150M table
is target-volume / post-slope-area arithmetic for 100M, 119M (the v2
reference base path), and 150M m3/yr.

The slope combination preserves the published overlay assumption: a
forest-weighted treed slope share is applied to the v2 operable area inside a
province/ecoprovince.  It is not a pixel-exact intersection, not a 30 m
operability classification, and not a legal timber harvesting landbase
(THLB), AAC landbase, or yield model.

Run:
    .venv/bin/python scripts/national/build_operable_proxy_v3.py \
        --out-dir data/derived/national/managed_landbase/operable_proxy_v3
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANAGED_DIR = REPO_ROOT / "data/derived/national/managed_landbase"
DEFAULT_V2_DIR = DEFAULT_MANAGED_DIR / "operable_proxy_v2"
DEFAULT_SLOPE_DIR = DEFAULT_MANAGED_DIR / "slope_screen"
DEFAULT_OUT = DEFAULT_MANAGED_DIR / "operable_proxy_v3"

V2_CASES = {
    "base": "base",
    "core20": "screen_core20",
    "core30": "screen_core30",
}
V2_CASE_ORDER = ("base", "core20", "core30")
SLOPE_CASES = {
    "none": 0.0,
    "gt15": 15.0,
    "gt30": 30.0,
}
SLOPE_CASE_ORDER = ("none", "gt15", "gt30")
TARGET_VOLUMES_M3_YR = {
    "100m": 100_000_000.0,
    "119m": 118_977_339.0,
    "150m": 150_000_000.0,
}
ODT_PER_M3 = 0.08831776452936206
MAX_ABS_HA = 0.01
MAX_ABS_FRACTION = 1e-12
SLOPE_GAP_HA = 13_272_017.759958506

OVERLAY_BASIS = (
    "forest-weighted 960 m treed slope share applied to v2 operable area; "
    "assumes v2 screens are independent of slope within the "
    "province/ecoprovince; not pixel-exact; screening proxy, not legal THLB"
)
NONE_BASIS = "v2 operable area unchanged; slope screen not applied"
PROVINCE_BASIS = (
    "sum of province-ecoprovince rows; effective lost share = lost / v2 "
    "operable; screening proxy, not legal THLB"
)

NAN_POLICY = (
    "Area fields are blank only where v2 coverage is unavailable (NU, PE); "
    "they are not zero."
)


def sha256(path: Path) -> str:
    """Return the hex SHA-256 digest of a file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_commit() -> str | None:
    """Return the current commit hash, or None outside a git checkout."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip() or None


def describe_file(path: Path) -> dict[str, Any]:
    """Describe an input file with a repository-relative path and digest."""
    try:
        relative = str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        relative = str(path.resolve())
    return {
        "path": relative,
        "size_bytes": path.stat().st_size,
        "sha256": sha256(path),
    }


def parse_bool(values: pd.Series, field: str) -> pd.Series:
    """Parse a CSV boolean column at the boundary, rejecting unknown values."""
    if values.dtype == bool:
        return values
    normalized = values.astype(str).str.strip().str.lower()
    unknown = set(normalized.unique()) - {"true", "false"}
    if unknown:
        raise ValueError(f"{field} contains non-boolean values: {sorted(unknown)}")
    return normalized.eq("true")


def require_columns(frame: pd.DataFrame, required: set[str], label: str) -> None:
    """Fail loudly when an input table is missing required columns."""
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"{label} is missing columns: {sorted(missing)}")


def load_v2_cases(path: Path, *, by_ecoprovince: bool) -> pd.DataFrame:
    """Load and parse the three v2 cases into trusted long-form state."""
    frame = pd.read_csv(path)
    required = {
        "province",
        "screen_case",
        "operable_proxy_ha",
        "coverage_available",
        "scanfi_forest_ha",
    }
    if by_ecoprovince:
        required |= {"ecoprovince", "ecoprovince_id"}
    require_columns(frame, required, path.name)

    frame = frame[frame["screen_case"].isin(V2_CASES.values())].copy()
    frame["v2_case"] = frame["screen_case"].map(
        {source: label for label, source in V2_CASES.items()}
    )
    if frame["v2_case"].isna().any():
        raise ValueError("unexpected v2 screen_case after mapping")
    frame["coverage_available"] = parse_bool(
        frame["coverage_available"], "coverage_available"
    )
    frame["operable_proxy_ha"] = pd.to_numeric(
        frame["operable_proxy_ha"], errors="coerce"
    )

    keys = ["province", "v2_case"]
    if by_ecoprovince:
        keys = ["province", "ecoprovince", "v2_case"]
    if frame.duplicated(keys).any():
        raise ValueError(f"{path.name} has duplicate rows for {keys}")

    coverage_spread = frame.groupby(keys, dropna=False)["coverage_available"].nunique()
    if (coverage_spread != 1).any():
        raise ValueError(f"{path.name} has inconsistent coverage across cases")

    defined = frame["operable_proxy_ha"].notna()
    if (defined != frame["coverage_available"]).any():
        raise ValueError(
            f"{path.name}: operable area must be defined exactly where coverage exists"
        )
    if (frame["operable_proxy_ha"].dropna() < 0).any():
        raise ValueError(f"{path.name}: negative operable area")

    selected = ["province", "v2_case", "operable_proxy_ha", "coverage_available"]
    if by_ecoprovince:
        selected = [
            "province",
            "ecoprovince",
            "ecoprovince_id",
            *selected[1:],
        ]
    return frame[selected].sort_values(keys).reset_index(drop=True)


def load_slope_shares(
    overlay_path: Path,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load slope shares and the published overlay reconciliation table."""
    overlay = pd.read_csv(overlay_path)
    require_columns(
        overlay,
        {
            "province",
            "ecoprovince",
            "ecoprovince_id",
            "slope_mean_pct",
            "share_gt15",
            "share_gt30",
            "base_operable_ha",
            "core20_operable_ha",
            "base_lost_gt15_ha",
            "base_lost_gt30_ha",
            "core20_lost_gt15_ha",
            "core20_lost_gt30_ha",
            "slope_stats_available",
        },
        overlay_path.name,
    )
    keys = ["province", "ecoprovince"]
    if overlay.duplicated(keys).any():
        raise ValueError(f"{overlay_path.name} has duplicate province/ecoprovince rows")

    numeric = [
        "ecoprovince_id",
        "slope_mean_pct",
        "share_gt15",
        "share_gt30",
        "base_operable_ha",
        "core20_operable_ha",
        "base_lost_gt15_ha",
        "base_lost_gt30_ha",
        "core20_lost_gt15_ha",
        "core20_lost_gt30_ha",
    ]
    overlay[numeric] = overlay[numeric].apply(pd.to_numeric, errors="raise")
    overlay["slope_stats_available"] = parse_bool(
        overlay["slope_stats_available"], "slope_stats_available"
    )

    if (overlay[["share_gt15", "share_gt30"]] < 0).any().any():
        raise ValueError("negative slope share")
    if (overlay[["share_gt15", "share_gt30"]] > 1).any().any():
        raise ValueError("slope share exceeds one")
    if (overlay["share_gt15"] < overlay["share_gt30"]).any():
        raise ValueError("share_gt15 must be at least share_gt30")
    if (overlay["base_operable_ha"] < 0).any():
        raise ValueError("negative base operable area in slope overlay")
    if (overlay["core20_operable_ha"] < 0).any():
        raise ValueError("negative core20 operable area in slope overlay")
    if (
        overlay["core20_operable_ha"] > overlay["base_operable_ha"] + MAX_ABS_HA
    ).any():
        raise ValueError("core20 operable exceeds base in slope overlay")
    for threshold in (15, 30):
        if (
            overlay[f"base_lost_gt{threshold}_ha"]
            > overlay["base_operable_ha"] + MAX_ABS_HA
        ).any():
            raise ValueError(f"base gt{threshold} loss exceeds base operable")
        if (
            overlay[f"core20_lost_gt{threshold}_ha"]
            > overlay["core20_operable_ha"] + MAX_ABS_HA
        ).any():
            raise ValueError(f"core20 gt{threshold} loss exceeds core20 operable")

    # The published shares are rounded to six decimals.  Recovering the
    # applied share from the published loss/base pair reproduces the overlay
    # exactly; fall back to the published share where base operable is zero.
    shares = overlay[keys].copy()
    shares["slope_mean_pct"] = overlay["slope_mean_pct"]
    shares["slope_stats_available"] = overlay["slope_stats_available"]
    for threshold in (15, 30):
        base = overlay["base_operable_ha"].where(overlay["base_operable_ha"] > 0)
        derived = overlay[f"base_lost_gt{threshold}_ha"] / base
        applied = derived.fillna(overlay[f"share_gt{threshold}"])
        if (applied < -MAX_ABS_FRACTION).any() or (applied > 1).any():
            raise ValueError(f"derived gt{threshold} share is outside [0, 1]")
        shares[f"share_gt{threshold}"] = applied

    keep = [
        *keys,
        "slope_mean_pct",
        "share_gt15",
        "share_gt30",
        "slope_stats_available",
    ]
    return shares[keep].copy(), overlay


def combine_v3_cases(v2: pd.DataFrame, slope: pd.DataFrame) -> pd.DataFrame:
    """Cross v2 cases with slope cases, applying the published overlay logic."""
    keys = ["province", "ecoprovince"]
    merged = v2.merge(
        slope, on=keys, how="outer", validate="many_to_one", indicator=True
    )
    missing = merged[merged["_merge"] != "both"]
    if not missing.empty:
        detail = missing[keys + ["_merge"]].to_dict("records")
        raise ValueError(f"v2 and slope tables do not share identical pairs: {detail}")
    merged = merged.drop(columns="_merge").rename(
        columns={"operable_proxy_ha": "v2_operable_ha"}
    )

    frames: list[pd.DataFrame] = []
    for slope_case in SLOPE_CASE_ORDER:
        share = (
            np.zeros(len(merged), dtype=float)
            if slope_case == "none"
            else merged[f"share_gt{SLOPE_CASES[slope_case]:.0f}"].to_numpy(dtype=float)
        )
        area = merged["v2_operable_ha"].to_numpy(dtype=float)
        with np.errstate(invalid="ignore", divide="ignore"):
            lost = np.where(np.isnan(area), np.nan, area * share)
            remaining = area - lost
            lost_share = np.where(
                np.isnan(area),
                np.nan,
                np.where(area > 0, lost / area, 0.0),
            )
        case = merged[
            [
                "province",
                "ecoprovince",
                "ecoprovince_id",
                "v2_case",
                "coverage_available",
                "v2_operable_ha",
                "share_gt15",
                "share_gt30",
                "slope_mean_pct",
                "slope_stats_available",
            ]
        ].copy()
        case["v2_screen_case"] = case["v2_case"].map(V2_CASES)
        case["slope_case"] = slope_case
        case["slope_threshold_pct"] = SLOPE_CASES[slope_case]
        case["applied_share"] = share
        case["lost_ha"] = lost
        case["remaining_operable_ha"] = remaining
        case["lost_share_of_v2_operable"] = lost_share
        case["slope_stats_missing_on_operable"] = (
            (area > 0) & (~merged["slope_stats_available"].to_numpy(dtype=bool))
        )
        case["estimate_basis"] = (
            NONE_BASIS if slope_case == "none" else OVERLAY_BASIS
        )
        frames.append(case)

    combined = pd.concat(frames, ignore_index=True)
    combined["v2_case"] = pd.Categorical(
        combined["v2_case"], categories=V2_CASE_ORDER, ordered=True
    )
    combined["slope_case"] = pd.Categorical(
        combined["slope_case"], categories=SLOPE_CASE_ORDER, ordered=True
    )
    combined = combined.sort_values(
        ["province", "ecoprovince_id", "ecoprovince", "v2_case", "slope_case"]
    ).reset_index(drop=True)

    pair_count = v2[keys].drop_duplicates().shape[0]
    expected_rows = pair_count * len(V2_CASE_ORDER) * len(SLOPE_CASE_ORDER)
    if len(combined) != expected_rows:
        raise ValueError(
            f"expected {expected_rows} combined rows, found {len(combined)}"
        )
    if combined.duplicated(
        ["province", "ecoprovince", "v2_case", "slope_case"]
    ).any():
        raise ValueError("duplicate v3 province/ecoprovince/case rows")
    return combined


def build_province_table(combined: pd.DataFrame) -> pd.DataFrame:
    """Roll ecoprovince rows up to province without re-averaging shares."""
    grouped = combined.groupby(
        ["province", "v2_case", "slope_case"], observed=True
    ).agg(
        v2_operable_ha=("v2_operable_ha", lambda s: s.sum(min_count=1)),
        lost_ha=("lost_ha", lambda s: s.sum(min_count=1)),
        remaining_operable_ha=(
            "remaining_operable_ha",
            lambda s: s.sum(min_count=1),
        ),
        coverage_available=("coverage_available", "all"),
        slope_stats_available_for_operable_pairs=(
            "slope_stats_missing_on_operable",
            lambda s: not bool(s.any()),
        ),
        ecoprovince_pairs=("ecoprovince", "nunique"),
    ).reset_index()

    grouped["v2_screen_case"] = grouped["v2_case"].map(V2_CASES)
    grouped["slope_threshold_pct"] = grouped["slope_case"].map(SLOPE_CASES)
    grouped["lost_share_of_v2_operable"] = np.where(
        grouped["v2_operable_ha"].isna(),
        np.nan,
        np.where(
            grouped["v2_operable_ha"] > 0,
            grouped["lost_ha"] / grouped["v2_operable_ha"],
            0.0,
        ),
    )
    grouped["estimate_basis"] = PROVINCE_BASIS
    grouped["v2_case"] = pd.Categorical(
        grouped["v2_case"], categories=V2_CASE_ORDER, ordered=True
    )
    grouped["slope_case"] = pd.Categorical(
        grouped["slope_case"], categories=SLOPE_CASE_ORDER, ordered=True
    )
    return grouped.sort_values(["province", "v2_case", "slope_case"]).reset_index(
        drop=True
    )


def build_national_table(combined: pd.DataFrame) -> pd.DataFrame:
    """Sum defined province/ecoprovince rows to national totals by case."""
    national = (
        combined.groupby(["v2_case", "slope_case"], observed=True)[
            ["v2_operable_ha", "lost_ha", "remaining_operable_ha"]
        ]
        .sum(min_count=1)
        .reset_index()
    )
    national["lost_share_of_v2_operable"] = np.where(
        national["v2_operable_ha"] > 0,
        national["lost_ha"] / national["v2_operable_ha"],
        np.nan,
    )
    if national[["v2_operable_ha", "lost_ha", "remaining_operable_ha"]].isna().any().any():
        raise ValueError("national totals contain an undefined case")
    return national


def build_150m_table(national: pd.DataFrame) -> pd.DataFrame:
    """Compute pure target-volume / post-slope-area intensities."""
    table = national[
        ["v2_case", "slope_case", "v2_operable_ha", "lost_ha", "remaining_operable_ha"]
    ].copy()
    table = table.rename(columns={"remaining_operable_ha": "post_slope_operable_ha"})
    if (table["post_slope_operable_ha"] <= 0).any():
        raise ValueError("150M arithmetic requires positive post-slope area")

    for label, target in TARGET_VOLUMES_M3_YR.items():
        table[f"target_{label}_m3_yr"] = target
        table[f"intensity_{label}_m3_ha_yr"] = (
            target / table["post_slope_operable_ha"]
        )
    table["implied_odt_per_ha_yr_150m"] = (
        table["intensity_150m_m3_ha_yr"] * ODT_PER_M3
    )
    base_none = table.loc[
        (table["v2_case"] == "base") & (table["slope_case"] == "none"),
        "intensity_150m_m3_ha_yr",
    ].iloc[0]
    table["relative_to_v2_base_none_150m"] = (
        table["intensity_150m_m3_ha_yr"] / base_none
    )
    return table.sort_values(["v2_case", "slope_case"]).reset_index(drop=True)


def compare_series(
    actual: pd.Series,
    expected: pd.Series,
    label: str,
    *,
    atol_ha: float = MAX_ABS_HA,
) -> float:
    """Compare two series, preserving the NaN pattern, and return max error."""
    actual = actual.sort_index()
    expected = expected.sort_index()
    if not actual.index.equals(expected.index):
        raise ValueError(f"{label}: index mismatch")
    if not np.array_equal(actual.isna().to_numpy(), expected.isna().to_numpy()):
        raise ValueError(f"{label}: NaN pattern mismatch")
    defined = actual.notna()
    if not defined.any():
        return 0.0
    maximum = float((actual[defined] - expected[defined]).abs().max())
    if maximum > atol_ha:
        raise ValueError(f"{label}: max absolute difference {maximum} ha")
    return maximum


def validate_v3(
    combined: pd.DataFrame,
    province: pd.DataFrame,
    national: pd.DataFrame,
    table_150m: pd.DataFrame,
    v2_ecoprovince: pd.DataFrame,
    v2_province: pd.DataFrame,
    overlay: pd.DataFrame,
    overlay_province: pd.DataFrame,
    slope_summary: dict[str, Any],
) -> dict[str, Any]:
    """Run fail-closed reconciliation, arithmetic, and coverage checks."""
    checks: dict[str, Any] = {}
    keys = ["province", "ecoprovince"]

    # 1. v3 none must reproduce v2 exactly, including the coverage blanks.
    v3_none_eco = combined[combined["slope_case"] == "none"].set_index(
        [*keys, "v2_case"]
    )["v2_operable_ha"]
    v2_eco = v2_ecoprovince.set_index([*keys, "v2_case"])["operable_proxy_ha"]
    checks["v3_none_equals_v2_ecoprovince_max_abs_diff_ha"] = compare_series(
        v3_none_eco, v2_eco, "v3 none vs v2 ecoprovince"
    )
    v3_none_prov = province[province["slope_case"] == "none"].set_index(
        ["province", "v2_case"]
    )["v2_operable_ha"]
    v2_prov = v2_province.set_index(["province", "v2_case"])["operable_proxy_ha"]
    checks["v3_none_equals_v2_province_max_abs_diff_ha"] = compare_series(
        v3_none_prov, v2_prov, "v3 none vs v2 province"
    )
    checks["v3_none_equals_v2_all_cases"] = True

    # 2. Threshold losses must match the published slope overlay for base and
    #    core20.  core30 has no published overlay; it uses the same share.
    for v2_case, prefix in (("base", "base"), ("core20", "core20")):
        for threshold in (15, 30):
            actual_eco = combined[
                (combined["v2_case"] == v2_case)
                & (combined["slope_case"] == f"gt{threshold}")
            ].set_index(keys)["lost_ha"]
            expected_eco = overlay.set_index(keys)[
                f"{prefix}_lost_gt{threshold}_ha"
            ]
            # The overlay treats missing tenure coverage as zero operable area;
            # v3 keeps it blank.  Reconcile only where v3 is defined.
            expected_eco = expected_eco.reindex(actual_eco.index).where(
                actual_eco.notna()
            )
            checks[
                f"v3_gt{threshold}_{v2_case}_matches_overlay_ecoprovince_max_abs_diff_ha"
            ] = compare_series(
                actual_eco, expected_eco, f"overlay gt{threshold} {v2_case} eco"
            )
            actual_prov = province[
                (province["v2_case"] == v2_case)
                & (province["slope_case"] == f"gt{threshold}")
            ].set_index("province")["lost_ha"]
            expected_prov = overlay_province.set_index("province")[
                f"{prefix}_lost_gt{threshold}_ha"
            ]
            expected_prov = expected_prov.reindex(actual_prov.index).where(
                actual_prov.notna()
            )
            checks[
                f"v3_gt{threshold}_{v2_case}_matches_overlay_province_max_abs_diff_ha"
            ] = compare_series(
                actual_prov, expected_prov, f"overlay gt{threshold} {v2_case} prov"
            )
    checks["v3_gt15_gt30_match_overlay_base_and_core20"] = True

    # 3. Province rows must equal the sum of their ecoprovince rows.
    eco_rollup = combined.groupby(
        ["province", "v2_case", "slope_case"], observed=True
    )[["lost_ha", "remaining_operable_ha"]].sum(min_count=1)
    prov_indexed = province.set_index(["province", "v2_case", "slope_case"])
    checks["province_equals_ecoprovince_sum_lost_max_abs_diff_ha"] = compare_series(
        prov_indexed["lost_ha"], eco_rollup["lost_ha"], "province lost rollup"
    )
    checks[
        "province_equals_ecoprovince_sum_remaining_max_abs_diff_ha"
    ] = compare_series(
        prov_indexed["remaining_operable_ha"],
        eco_rollup["remaining_operable_ha"],
        "province remaining rollup",
    )
    checks["province_sum_equals_ecoprovince_sum_all_cases"] = True

    # 4. National totals must equal the slope-screen published totals.
    slope_national = slope_summary["national"]
    national_indexed = national.set_index(["v2_case", "slope_case"])
    for v2_case, slope_case, field in (
        ("base", "none", "base_operable_ha"),
        ("core20", "none", "core20_operable_ha"),
        ("base", "gt30", "base_remaining_gt30_ha"),
        ("base", "gt15", "base_remaining_gt15_ha"),
        ("core20", "gt30", "core20_remaining_gt30_ha"),
        ("core20", "gt15", "core20_remaining_gt15_ha"),
    ):
        actual = national_indexed.loc[(v2_case, slope_case), "remaining_operable_ha"]
        expected = float(slope_national[field])
        if abs(float(actual) - expected) > MAX_ABS_HA:
            raise ValueError(
                f"national {v2_case}/{slope_case} does not match slope summary"
            )
    checks["national_matches_slope_summary"] = True
    checks["national_base_none_operable_ha"] = float(
        national_indexed.loc[("base", "none"), "v2_operable_ha"]
    )

    # 5. No NaN outside the documented coverage gap, no negatives, and losses
    #    cannot exceed the pre-slope area.
    area_columns = [
        "v2_operable_ha",
        "lost_ha",
        "remaining_operable_ha",
        "lost_share_of_v2_operable",
    ]
    defined = combined["coverage_available"]
    missing = ~defined
    if combined.loc[defined, area_columns].isna().any().any():
        raise ValueError("defined v3 rows contain NaN in an area field")
    if combined.loc[missing, area_columns].notna().any().any():
        raise ValueError("coverage-missing v3 rows contain a non-NaN area field")
    if (combined.loc[defined, area_columns] < 0).any().any():
        raise ValueError("negative v3 area or lost share")
    if (combined.loc[defined, "lost_ha"] > combined.loc[defined, "v2_operable_ha"] + MAX_ABS_HA).any():
        raise ValueError("v3 lost area exceeds v2 operable area")
    if (
        combined.loc[defined, "remaining_operable_ha"]
        > combined.loc[defined, "v2_operable_ha"] + MAX_ABS_HA
    ).any():
        raise ValueError("v3 remaining area exceeds v2 operable area")
    if np.isnan(
        national[
            ["v2_operable_ha", "lost_ha", "remaining_operable_ha"]
        ].to_numpy(dtype=float)
    ).any():
        raise ValueError("national v3 totals contain NaN")
    checks["no_nan_outside_coverage_gap"] = True
    checks["no_negative_area_fields"] = True
    checks["lost_le_v2_operable"] = True
    checks["remaining_le_v2_operable"] = True

    # 6. Threshold and case ordering.
    wide = combined.pivot_table(
        index=[*keys, "v2_case"],
        columns="slope_case",
        values="remaining_operable_ha",
        observed=True,
    )
    if (wide["gt15"] > wide["gt30"] + MAX_ABS_HA).any():
        raise ValueError("gt15 remaining area exceeds gt30 remaining area")
    if (wide["gt30"] > wide["none"] + MAX_ABS_HA).any():
        raise ValueError("gt30 remaining area exceeds none remaining area")
    lost_wide = combined.pivot_table(
        index=[*keys, "v2_case"],
        columns="slope_case",
        values="lost_ha",
        observed=True,
    )
    if (lost_wide["gt30"] > lost_wide["gt15"] + MAX_ABS_HA).any():
        raise ValueError("gt30 loss exceeds gt15 loss")
    shares = combined[["share_gt15", "share_gt30"]].dropna()
    if (shares["share_gt15"] < shares["share_gt30"] - MAX_ABS_FRACTION).any():
        raise ValueError("share_gt15 is below share_gt30")
    case_wide = combined.pivot_table(
        index=[*keys, "slope_case"],
        columns="v2_case",
        values="remaining_operable_ha",
        observed=True,
    )
    if (case_wide["core30"] > case_wide["core20"] + MAX_ABS_HA).any():
        raise ValueError("core30 remaining area exceeds core20")
    if (case_wide["core20"] > case_wide["base"] + MAX_ABS_HA).any():
        raise ValueError("core20 remaining area exceeds base")
    checks["threshold_ordering_gt15_le_gt30_le_none"] = True
    checks["case_ordering_core30_le_core20_le_base"] = True

    # 7. 150M arithmetic must be exactly target / post-slope area.
    if len(table_150m) != len(V2_CASE_ORDER) * len(SLOPE_CASE_ORDER):
        raise ValueError("150M table does not have one row per v3 case")
    for label, target in TARGET_VOLUMES_M3_YR.items():
        expected = target / table_150m["post_slope_operable_ha"]
        maximum = float(
            (table_150m[f"intensity_{label}_m3_ha_yr"] - expected).abs().max()
        )
        if maximum > 1e-12:
            raise ValueError(f"150M arithmetic mismatch for {label}: {maximum}")
        checks[f"intensity_{label}_max_abs_diff_m3_ha_yr"] = maximum
    base_gt30 = table_150m.loc[
        (table_150m["v2_case"] == "base") & (table_150m["slope_case"] == "gt30"),
        "intensity_150m_m3_ha_yr",
    ].iloc[0]
    core20_gt30 = table_150m.loc[
        (table_150m["v2_case"] == "core20")
        & (table_150m["slope_case"] == "gt30"),
        "intensity_150m_m3_ha_yr",
    ].iloc[0]
    if not math.isclose(
        float(base_gt30),
        float(slope_summary["intensity_150m"]["base_intensity_m3_ha_yr"]),
        rel_tol=1e-12,
    ):
        raise ValueError("base gt30 150M intensity does not match slope summary")
    if not math.isclose(
        float(core20_gt30),
        float(slope_summary["intensity_150m"]["core20_intensity_m3_ha_yr"]),
        rel_tol=1e-12,
    ):
        raise ValueError("core20 gt30 150M intensity does not match slope summary")
    checks["150m_arithmetic_exact"] = True

    # 8. The 13.27 Mha treed area outside RSYC ecoprovince polygons remains an
    #    explicit, measured gap rather than a zero.
    gap = slope_summary["validation"]["treed_area_vs_v2_ha"]
    gap_ha = abs(float(gap["difference_ha"]))
    if not math.isclose(gap_ha, SLOPE_GAP_HA, rel_tol=1e-9, abs_tol=1e-3):
        raise ValueError(f"unexpected RSYC slope coverage gap: {gap_ha} ha")
    checks["slope_coverage_gap_ha"] = gap_ha
    checks["slope_coverage_gap_share"] = abs(float(gap["difference_share"]))
    checks["slope_coverage_gap_matches_13_27_mha"] = True

    boolean_checks = [
        value
        for value in checks.values()
        if isinstance(value, (bool, np.bool_))
    ]
    checks["all_checks_pass"] = all(bool(value) for value in boolean_checks)
    if not checks["all_checks_pass"]:
        raise ValueError(f"operable proxy v3 validation failed: {checks}")
    return checks


def national_summary(national: pd.DataFrame) -> dict[str, Any]:
    """Render national case totals as nested JSON."""
    by_case: dict[str, dict[str, Any]] = {}
    for v2_case in V2_CASE_ORDER:
        by_case[v2_case] = {}
        for slope_case in SLOPE_CASE_ORDER:
            row = national[
                (national["v2_case"] == v2_case)
                & (national["slope_case"] == slope_case)
            ].iloc[0]
            by_case[v2_case][slope_case] = {
                "v2_operable_ha": float(row["v2_operable_ha"]),
                "lost_ha": float(row["lost_ha"]),
                "remaining_operable_ha": float(row["remaining_operable_ha"]),
                "lost_share_of_v2_operable": float(
                    row["lost_share_of_v2_operable"]
                ),
            }
    return by_case


def intensity_summary(table: pd.DataFrame) -> dict[str, Any]:
    """Render 150M intensities as nested JSON."""
    by_case: dict[str, dict[str, Any]] = {}
    for v2_case in V2_CASE_ORDER:
        by_case[v2_case] = {}
        for slope_case in SLOPE_CASE_ORDER:
            row = table[
                (table["v2_case"] == v2_case)
                & (table["slope_case"] == slope_case)
            ].iloc[0]
            by_case[v2_case][slope_case] = {
                "post_slope_operable_ha": float(row["post_slope_operable_ha"]),
                "intensity_100m_m3_ha_yr": float(
                    row["intensity_100m_m3_ha_yr"]
                ),
                "intensity_119m_m3_ha_yr": float(
                    row["intensity_119m_m3_ha_yr"]
                ),
                "intensity_150m_m3_ha_yr": float(
                    row["intensity_150m_m3_ha_yr"]
                ),
                "implied_odt_per_ha_yr_150m": float(
                    row["implied_odt_per_ha_yr_150m"]
                ),
            }
    return by_case


def gaps(slope_summary: dict[str, Any]) -> list[dict[str, str]]:
    """Return the v3 gap register, preserving inherited and slope gaps."""
    gap_ha = abs(
        float(slope_summary["validation"]["treed_area_vs_v2_ha"]["difference_ha"])
    )
    return [
        {
            "gap_id": "legal_thlb_missing",
            "status": "open",
            "detail": (
                "No legal timber harvesting landbase boundary is used. Tenure "
                "is a rights footprint, not a harvest-landbase determination."
            ),
            "effect": (
                "All v3 areas are screening proxies and must not be labelled "
                "THLB or AAC."
            ),
        },
        {
            "gap_id": "slope_overlay_assumption",
            "status": "open",
            "detail": (
                "Forest-weighted treed slope shares from the 960 m screen are "
                "applied to v2 operable area inside a province/ecoprovince. "
                "The v2 screens are assumed independent of slope; this is not "
                "a pixel-exact intersection."
            ),
            "effect": (
                "Steep-area losses are overlay estimates, not spatially "
                "resolved exclusions."
            ),
        },
        {
            "gap_id": "coarse_slope_not_operability",
            "status": "open",
            "detail": (
                "Slope is computed from 960 m average elevation. Local 30 m "
                "slope can be much steeper; this layer cannot classify "
                "operability, harvest system, or site degradation."
            ),
            "effect": (
                ">15% and >30% losses are screening sensitivities, not "
                "operational or legal classifications."
            ),
        },
        {
            "gap_id": "dem_source_mix",
            "status": "open",
            "detail": (
                "MRDEM-30 is a DTM mosaic of HRDEM lidar and Copernicus "
                "GLO-30. Non-lidar areas are modelled and may have larger "
                "vertical error; the public STAC item has no source legend."
            ),
            "effect": "Slope accuracy is not uniform across the country.",
        },
        {
            "gap_id": "treed_outside_rsyc_ecoprovince",
            "status": "open",
            "detail": (
                f"{gap_ha:,.2f} ha of SCANFI treed area lies outside the RSYC "
                "ecoprovince polygons. v2 assigns those blocks to the nearest "
                "ecoprovince, but the slope screen has no independent "
                "ecoprovince slope statistics for them."
            ),
            "effect": (
                "v3 inherits the v2 nearest-ecoprovince attribution and "
                "applies the receiving ecoprovince's slope share to those "
                "blocks; the 13.27 Mha gap is not zero-filled or separately "
                "resolved."
            ),
        },
        {
            "gap_id": "core30_slope_no_published_overlay",
            "status": "documented",
            "detail": (
                "The published slope overlay contains base and core20 only. "
                "v3 applies the same ecoprovince share to core30; there is no "
                "independent core30 overlay to reconcile against."
            ),
            "effect": "core30 slope losses are internally consistent estimates.",
        },
        {
            "gap_id": "slope_dem_missing",
            "status": "resolved_in_v3",
            "detail": (
                "The v2 gap that no national DEM was present is superseded by "
                "the national 960 m MRDEM-30 slope screen used here."
            ),
            "effect": (
                "The residual limitations are captured in "
                "coarse_slope_not_operability, dem_source_mix, and "
                "slope_overlay_assumption."
            ),
        },
        {
            "gap_id": "nbac_2010_2015_missing",
            "status": "open",
            "detail": (
                "The NBAC bundle is the 2020 annual vintage only; 2010 and "
                "2015 fire-history screens cannot be computed."
            ),
            "effect": "Only the 2020 fire screen is represented in v2/v3.",
        },
        {
            "gap_id": "nrn_public_roads_only",
            "status": "documented",
            "detail": (
                "NRN is the public road network; resource, industrial, winter, "
                "and decommissioned roads are absent."
            ),
            "effect": "Road distance is an optimistic public-road access proxy.",
        },
        {
            "gap_id": "age_regeneration_assumption",
            "status": "documented",
            "detail": (
                "SCANFI median age is a stand-age prediction; excluding stands "
                "below 20 or 30 years is a screening convention."
            ),
            "effect": "Age screens reduce area beyond merchantability alone.",
        },
        {
            "gap_id": "managed_share_uniform",
            "status": "documented",
            "detail": (
                "The v1 managed share is uniform inside each "
                "province-ecoprovince; tenure is not intersected spatially."
            ),
            "effect": "Spatial tenure pattern is not represented.",
        },
        {
            "gap_id": "nfi_age_uncertainty",
            "status": "documented",
            "detail": (
                "SCANFI age is a 30 m prediction surface, not a provincial "
                "inventory or disturbance record."
            ),
            "effect": "Age thresholds are screening conventions.",
        },
        {
            "gap_id": "nu_pe_tenure_missing",
            "status": "documented",
            "detail": (
                "NU and PE managed and operable values are blank because the "
                "2016 tenure compilation has no coverage."
            ),
            "effect": "Blank is not zero; national totals cover 11 provinces.",
        },
    ]


def caveats() -> list[str]:
    """Return the v3 caveat list."""
    return [
        "Screening proxy only; not a legal THLB, AAC landbase, or harvest feasibility test.",
        "Slope losses use a forest-weighted ecoprovince share, not a pixel-exact intersection.",
        "The 960 m slope layer smooths local terrain and is not a 30 m operability model.",
        "The 13.27 Mha of SCANFI treed area outside RSYC ecoprovince polygons has no independent slope statistics; v3 inherits v2 nearest-ecoprovince attribution.",
        "Slope >15% and >30% are screening thresholds, not provincial or legal rules.",
        "NBAC 2020 is an annual fire vintage; 2010 and 2015 cases are gaps.",
        "NRN public roads only; resource and industrial roads are absent.",
        "NU and PE operable values remain blank, not zero.",
        "The 100M/119M/150M intensities are target-volume / area arithmetic, not growth forecasts or sustainability tests.",
    ]


def render_report(
    combined: pd.DataFrame,
    province: pd.DataFrame,
    national: pd.DataFrame,
    table_150m: pd.DataFrame,
    checks: dict[str, Any],
    gap_list: list[dict[str, str]],
    summary: dict[str, Any],
) -> str:
    """Render the human-readable v3 report."""
    lines = [
        "# National Operable Landbase Proxy v3",
        "",
        ("**Status: screening proxy. This is not a legal timber harvesting "
        "landbase (THLB), not an AAC landbase, and not an operability model.**"),
        "",
        f"Generated: {summary['generated_at_utc']}  ",
        f"Runtime: {summary['runtime_seconds']:.1f} s  ",
        ("Inputs: published operable proxy v2 tables and the national 960 m "
        "MRDEM-30 slope screen. No new GIS raster is produced."),
        "",
        "## Method and overlay assumption",
        "",
        ("v3 combines the three v2 cases (base, core20, core30) with three "
        "slope cases (none, >15%, >30%). For each province/ecoprovince the "
        "published forest-weighted treed slope share is applied to the v2 "
        "operable area:"),
        "",
        "```",
        "lost_ha = v2_operable_ha * slope_share",
        "remaining_ha = v2_operable_ha - lost_ha",
        "```",
        "",
        ("This preserves the slope-screen overlay assumption: the v2 screens "
        "are assumed independent of slope inside a province/ecoprovince. It "
        "is **not** a pixel-exact intersection. The >30% base and core20 "
        "losses reconcile to the published slope overlay; core30 uses the "
        "same ecoprovince share because no core30 overlay was published."),
        "",
        "## National areas by case",
        "",
        "| v2 case | slope case | v2 operable (Mha) | Lost (Mha) | Remaining (Mha) | Lost share |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for v2_case in V2_CASE_ORDER:
        for slope_case in SLOPE_CASE_ORDER:
            row = national[
                (national["v2_case"] == v2_case)
                & (national["slope_case"] == slope_case)
            ].iloc[0]
            lines.append(
                f"| {v2_case} | {slope_case} | "
                f"{row['v2_operable_ha'] / 1e6:.3f} | "
                f"{row['lost_ha'] / 1e6:.4f} | "
                f"{row['remaining_operable_ha'] / 1e6:.3f} | "
                f"{row['lost_share_of_v2_operable']:.3%} |"
            )

    lines += [
        "",
        "## 150M implications",
        "",
        ("The 150,000,000 m3/yr path is an upper scenario bound, not a forecast "
        "or AAC. 119M is the v2 reference base path (118,977,339 m3/yr)."),
        "",
        "| v2 case | slope case | Post-slope operable (Mha) | 100M (m3/ha/yr) | 119M (m3/ha/yr) | 150M (m3/ha/yr) |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for v2_case in V2_CASE_ORDER:
        for slope_case in SLOPE_CASE_ORDER:
            row = table_150m[
                (table_150m["v2_case"] == v2_case)
                & (table_150m["slope_case"] == slope_case)
            ].iloc[0]
            lines.append(
                f"| {v2_case} | {slope_case} | "
                f"{row['post_slope_operable_ha'] / 1e6:.3f} | "
                f"{row['intensity_100m_m3_ha_yr']:.3f} | "
                f"{row['intensity_119m_m3_ha_yr']:.3f} | "
                f"{row['intensity_150m_m3_ha_yr']:.3f} |"
            )

    lines += [
        "",
        ("These are target volume divided by post-slope operable area. They are "
        "not growth/yield estimates and do not demonstrate sustainability."),
        "",
        "## Province totals (remaining Mha)",
        "",
        "| Province | base none | base >15% | base >30% | core20 none | core20 >15% | core20 >30% | core30 none | core30 >15% | core30 >30% |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    pivot = province.pivot_table(
        index="province",
        columns=["v2_case", "slope_case"],
        values="remaining_operable_ha",
        observed=True,
        dropna=False,
    )
    for province_code in sorted(province["province"].unique()):
        row = pivot.loc[province_code]
        values = [
            row[(v2_case, slope_case)]
            for v2_case in V2_CASE_ORDER
            for slope_case in SLOPE_CASE_ORDER
        ]
        rendered = [
            "missing" if pd.isna(value) else f"{float(value) / 1e6:.3f}"
            for value in values
        ]
        lines.append(f"| {province_code} | " + " | ".join(rendered) + " |")

    top = (
        combined[
            (combined["v2_case"] == "base")
            & (combined["slope_case"] == "gt30")
        ]
        .sort_values("lost_ha", ascending=False)
        .head(10)
    )
    lines += [
        "",
        "### Largest base >30% ecoprovince losses",
        "",
        "| Province | Ecoprovince | Base operable (Mha) | Lost >30% (Mha) | Lost share |",
        "|---|---|---:|---:|---:|",
    ]
    for _, row in top.iterrows():
        lines.append(
            f"| {row['province']} | {row['ecoprovince']} | "
            f"{row['v2_operable_ha'] / 1e6:.3f} | "
            f"{row['lost_ha'] / 1e6:.4f} | "
            f"{row['lost_share_of_v2_operable']:.3%} |"
        )

    lines += [
        "",
        "## The 13.27 Mha ecoprovince gap",
        "",
        (f"The slope screen covers {summary['slope_coverage']['slope_screen_treed_area_ha'] / 1e6:.2f} "
        "Mha of SCANFI treed area. "
        f"{summary['slope_coverage']['treed_outside_rsyc_ecoprovince_ha'] / 1e6:.2f} "
        f"Mha ({summary['slope_coverage']['treed_outside_rsyc_ecoprovince_share']:.2%}) "
        "of SCANFI treed area lies outside the RSYC ecoprovince polygons and "
        "has no independent ecoprovince slope statistics. Operable proxy v2 "
        "assigns those blocks to the nearest ecoprovince; v3 inherits that "
        "attribution and applies the receiving ecoprovince's slope share. The "
        "gap remains explicit and is not zero-filled."),
        "",
        "## Validation",
        "",
        (f"- v3 none equals v2 for all three cases: **{checks['v3_none_equals_v2_all_cases']}** "
        f"(max province residual {checks['v3_none_equals_v2_province_max_abs_diff_ha']:.2e} ha)."),
        ("- v3 >15% and >30% losses for base and core20 match the published "
        f"slope overlay (max ecoprovince residual "
        f"{max(checks[key] for key in checks if key.startswith('v3_gt') and key.endswith('ecoprovince_max_abs_diff_ha')):.2e} ha)."),
        f"- Province rows equal the sum of ecoprovince rows: **{checks['province_sum_equals_ecoprovince_sum_all_cases']}**.",
        f"- No NaN outside the NU/PE coverage gap: **{checks['no_nan_outside_coverage_gap']}**.",
        (f"- No negative areas; losses never exceed pre-slope area: "
        f"**{checks['no_negative_area_fields'] and checks['lost_le_v2_operable']}**."),
        f"- Threshold ordering (remaining >15% <= >30% <= none): **{checks['threshold_ordering_gt15_le_gt30_le_none']}**.",
        f"- Case ordering (core30 <= core20 <= base): **{checks['case_ordering_core30_le_core20_le_base']}**.",
        f"- 150M arithmetic exact: **{checks['150m_arithmetic_exact']}**.",
        f"- 13.27 Mha slope coverage gap reconciled: **{checks['slope_coverage_gap_matches_13_27_mha']}**.",
        f"- All checks pass: **{checks['all_checks_pass']}**.",
        "",
        "## Gaps",
        "",
    ]
    for gap in gap_list:
        lines.append(
            f"- **{gap['gap_id']}** ({gap['status']}): {gap['detail']} "
            f"Effect: {gap['effect']}"
        )
    lines += [
        "",
        "## Outputs",
        "",
        "- `operable_v3_by_province.csv`: province x v2 case x slope case.",
        "- `operable_v3_by_ecoprovince.csv`: province x ecoprovince x v2 case x slope case.",
        "- `operable_v3_150m.csv`: 100M/119M/150M target-volume arithmetic.",
        "- `summary.json`: national totals, 150M implications, validation, coverage gap, gaps.",
        "- `manifest.json`: input/output hashes, runtime, software versions, validation.",
        "",
        "## Reproduction",
        "",
        "```bash",
        ".venv/bin/python scripts/national/build_operable_proxy_v3.py \\",
        "  --out-dir data/derived/national/managed_landbase/operable_proxy_v3",
        "```",
        "",
        ("Raw SCANFI, NBAC, NRN, tenure, CPCAD, boundary, and RSYC payloads "
        "remain in the `data` submodule and are not committed to the parent "
        "repository."),
        "",
    ]
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--v2-dir",
        type=Path,
        default=DEFAULT_V2_DIR,
        help="operable proxy v2 artifact directory",
    )
    parser.add_argument(
        "--slope-dir",
        type=Path,
        default=DEFAULT_SLOPE_DIR,
        help="slope screen artifact directory",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=DEFAULT_OUT,
        help="output directory",
    )
    return parser.parse_args()


def main() -> int:
    """Build, validate, and publish the v3 screening tables."""
    args = parse_args()
    started = time.monotonic()
    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    v2_province_path = args.v2_dir / "operable_proxy_v2_by_province.csv"
    v2_ecoprovince_path = args.v2_dir / "operable_proxy_v2_by_ecoprovince.csv"
    v2_summary_path = args.v2_dir / "operable_proxy_v2_summary.json"
    overlay_path = args.slope_dir / "slope_operable_overlay.csv"
    overlay_province_path = args.slope_dir / "slope_operable_overlay_by_province.csv"
    slope_summary_path = args.slope_dir / "slope_screen_summary.json"

    v2_province = load_v2_cases(v2_province_path, by_ecoprovince=False)
    v2_ecoprovince = load_v2_cases(v2_ecoprovince_path, by_ecoprovince=True)
    slope_shares, overlay = load_slope_shares(overlay_path)
    overlay_province = pd.read_csv(overlay_province_path)
    slope_summary = json.loads(slope_summary_path.read_text(encoding="utf-8"))

    combined = combine_v3_cases(v2_ecoprovince, slope_shares)
    province = build_province_table(combined)
    national = build_national_table(combined)
    table_150m = build_150m_table(national)

    checks = validate_v3(
        combined,
        province,
        national,
        table_150m,
        v2_ecoprovince,
        v2_province,
        overlay,
        overlay_province,
        slope_summary,
    )

    gap_list = gaps(slope_summary)
    runtime = time.monotonic() - started
    generated_at = datetime.now(UTC).isoformat(timespec="seconds")
    command = " ".join([Path(sys.executable).name, *sys.argv])

    summary = {
        "schema_version": "operable_proxy_v3_summary_v1",
        "product": "national operable merchantable-forest screening proxy v3",
        "status": (
            "screening proxy combining operable proxy v2 with the national "
            "960 m slope screen; NOT a legal THLB, AAC landbase, operability "
            "model, or yield forecast"
        ),
        "generated_at_utc": generated_at,
        "runtime_seconds": round(runtime, 3),
        "command": command,
        "method": {
            "combination": (
                "v2 base/core20/core30 operable area x published "
                "forest-weighted ecoprovince share_gt15/share_gt30"
            ),
            "overlay_assumption": OVERLAY_BASIS,
            "slope_resolution_m": 960,
            "no_new_raster": True,
            "published_overlay_cases": ["base", "core20"],
            "core30_slope_basis": (
                "same ecoprovince share as base/core20; no independent "
                "published core30 overlay"
            ),
        },
        "cases": {
            "v2_cases": list(V2_CASE_ORDER),
            "v2_screen_case_map": V2_CASES,
            "slope_cases": [
                {"slope_case": key, "threshold_pct": value}
                for key, value in SLOPE_CASES.items()
            ],
        },
        "national": {
            "by_case": national_summary(national),
            "v2_base_operable_ha": float(
                national.loc[
                    (national["v2_case"] == "base")
                    & (national["slope_case"] == "none"),
                    "v2_operable_ha",
                ].iloc[0]
            ),
            "v2_core20_operable_ha": float(
                national.loc[
                    (national["v2_case"] == "core20")
                    & (national["slope_case"] == "none"),
                    "v2_operable_ha",
                ].iloc[0]
            ),
            "v2_core30_operable_ha": float(
                national.loc[
                    (national["v2_case"] == "core30")
                    & (national["slope_case"] == "none"),
                    "v2_operable_ha",
                ].iloc[0]
            ),
            "coverage_provinces": sorted(
                province.loc[province["coverage_available"], "province"].unique()
            ),
            "coverage_missing_provinces": sorted(
                province.loc[~province["coverage_available"], "province"].unique()
            ),
        },
        "intensity": {
            "targets_m3_yr": TARGET_VOLUMES_M3_YR,
            "reference_note": (
                "119M is the v2/slope reference base path (118,977,339 "
                "m3/yr), rounded in the label."
            ),
            "by_case": intensity_summary(table_150m),
            "note": (
                "Target volume divided by post-slope operable area. This is "
                "arithmetic only: not a growth/yield forecast, sustainability "
                "test, or AAC."
            ),
        },
        "slope_coverage": {
            "slope_screen_treed_area_ha": float(
                slope_summary["national"]["treed_area_ha"]
            ),
            "v2_scanfi_treed_area_ha": float(
                slope_summary["validation"]["treed_area_vs_v2_ha"]["v2_scanfi_forest_ha"]
            ),
            "treed_outside_rsyc_ecoprovince_ha": abs(
                float(
                    slope_summary["validation"]["treed_area_vs_v2_ha"][
                        "difference_ha"
                    ]
                )
            ),
            "treed_outside_rsyc_ecoprovince_share": abs(
                float(
                    slope_summary["validation"]["treed_area_vs_v2_ha"][
                        "difference_share"
                    ]
                )
            ),
            "handling": (
                "v2 nearest-ecoprovince attribution is inherited; the "
                "receiving ecoprovince slope share is applied. The gap is not "
                "zero-filled, extrapolated, or assigned independent slope "
                "statistics."
            ),
        },
        "validation": checks,
        "gaps": gap_list,
        "caveats": caveats(),
        "nan_policy": NAN_POLICY,
        "inputs": {
            "v2_by_province": describe_file(v2_province_path),
            "v2_by_ecoprovince": describe_file(v2_ecoprovince_path),
            "v2_summary": describe_file(v2_summary_path),
            "slope_operable_overlay": describe_file(overlay_path),
            "slope_operable_overlay_by_province": describe_file(
                overlay_province_path
            ),
            "slope_screen_summary": describe_file(slope_summary_path),
        },
    }

    province_path = out_dir / "operable_v3_by_province.csv"
    ecoprovince_path = out_dir / "operable_v3_by_ecoprovince.csv"
    table_150m_path = out_dir / "operable_v3_150m.csv"
    summary_path = out_dir / "summary.json"
    report_path = out_dir / "OPERABLE_V3_REPORT.md"
    manifest_path = out_dir / "manifest.json"

    province.to_csv(province_path, index=False, float_format="%.10f")
    combined.to_csv(ecoprovince_path, index=False, float_format="%.10f")
    table_150m.to_csv(table_150m_path, index=False, float_format="%.10f")
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    report_path.write_text(
        render_report(
            combined,
            province,
            national,
            table_150m,
            checks,
            gap_list,
            summary,
        )
        + "\n",
        encoding="utf-8",
    )

    output_paths = [
        province_path,
        ecoprovince_path,
        table_150m_path,
        summary_path,
        report_path,
    ]
    manifest = {
        "schema_version": "operable_proxy_v3_manifest_v1",
        "product": summary["product"],
        "status": summary["status"],
        "generated_at_utc": summary["generated_at_utc"],
        "runtime_seconds": summary["runtime_seconds"],
        "command": summary["command"],
        "git_commit": git_commit(),
        "software": {
            "python": sys.version.split()[0],
            "numpy": np.__version__,
            "pandas": pd.__version__,
        },
        "inputs": summary["inputs"],
        "method": summary["method"],
        "outputs": [
            {
                "path": path.name,
                "size_bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
            for path in output_paths
        ],
        "validation": checks,
        "gaps": gap_list,
        "caveats": summary["caveats"],
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    print(
        f"operable proxy v3: {len(combined)} province/ecoprovince-case rows; "
        f"national base none {summary['national']['v2_base_operable_ha'] / 1e6:.3f} Mha; "
        f"all checks pass={checks['all_checks_pass']}; outputs in "
        f"{out_dir.relative_to(REPO_ROOT)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
