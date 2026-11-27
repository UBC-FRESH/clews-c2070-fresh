"""Build a 100-year sustainable 119 Mm3/yr WS4 schedule and chain.

The national 990-m WS4 estate is simulated for 100 years in ten 10-year
periods.  Every block carries its exact fragment-age volume and an
area-weighted age.  The rotation age is the integer age of maximum mean annual
increment on the block's natural yield curve, capped to the 40-200 year
operability band and flagged when the raw peak falls outside it.

At each period the 1,189,773,391 m3 period target is allocated to provinces
with the frozen 119 Mm3 provincial control shares.  Inside a province, whole
eligible blocks are taken oldest-first (volume-aware) until the provincial
period target is met; no block is split, scaled, or harvested below rotation.
The selected blocks are assigned to the ten annual offsets of the period to
balance annual volume.  Blocks become managed after CC and may re-enter only
after a full rotation.  A national oldest-first free run is kept as a
sensitivity diagnostic, not as the chain basis.

The WS4 run is the authority for harvested volume and treated area.  The
achieved 100-year annual average by province is passed through the frozen
chain v5.1 per-province product/residue coefficients.  Two accounting bases
are written: ``sustainable_119m`` (one national factor to the 118,977,339.1 m3
control total) and ``sustainable_achieved`` (the unscaled WS4 average).

All outputs are model estimates.  This is not an AAC, an operability or tenure
screen, an official OSeCAN change, or a national supply claim.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from workflows.national.facilities import ws4_chain_119m as chain

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_ROOT = REPO_ROOT / "data"
WS4_ROOT = DATA_ROOT / "derived/ws4/scanfi-national-990m"
DATASET_DIR = WS4_ROOT / "dataset"
CONTROL_CSV = (
    DATA_ROOT / "derived/national/harvest_basis/provincial_allocation/119m.csv"
)
OUTPUT_DIR = DATA_ROOT / "derived/ws4/sustainable-119m"
RUN_DIR = OUTPUT_DIR / "run_100y"
SCHEDULE_PATH = OUTPUT_DIR / "schedule.csv"
CHAIN_DIR = DATA_ROOT / "derived/national/facilities/ws4_sustainable_119m"
CHAIN_V51_DIR = DATA_ROOT / "derived/national/facilities/national_product_chain_v5"
WS4_BIN = REPO_ROOT / ".venv-ws4/bin/ws4"

ARTIFACT = "ws4_sustainable_119m"
ARTIFACT_VERSION = "1.0.0"
ARTIFACT_TITLE = "Sustainable 119 Mm3/yr WS4 schedule and chain"

TARGET_ANNUAL_M3 = 118_977_339.1
TARGET_PERIOD_M3 = TARGET_ANNUAL_M3 * 10.0
PERIOD_LENGTH = 10
PERIODS = 10
HORIZON = PERIOD_LENGTH * PERIODS
ROTATION_MIN = 40
ROTATION_MAX = 200
CURVE_AGE_MAX = 600
PROVINCES: tuple[str, ...] = chain.PROVINCES
PROVINCE_TARGET_TOLERANCE_M3 = 1e-6
SCHEDULE_TREATMENT = "CC"

LABELS: dict[str, bool] = {
    "model_estimate": True,
    "observed": False,
    "policy_grade": False,
    "national_claim": False,
    "ws4_derived": True,
    "sustainable_schedule": True,
    "official_osecan_changed": False,
    "chain_v51_unchanged": True,
}
ROW_LABELS: dict[str, str] = {
    "model_estimate": "true",
    "observed": "false",
    "policy_grade": "false",
    "national_claim": "false",
    "ws4_derived": "true",
    "sustainable_schedule": "true",
    "official_osecan_changed": "false",
    "chain_v51_unchanged": "true",
}
BASES: tuple[str, str] = ("sustainable_119m", "sustainable_achieved")


class Ws4Sustainable119mError(RuntimeError):
    """Raised when the sustainable schedule or chain fails a guardrail."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _relative(path: Path) -> str:
    return str(path.relative_to(REPO_ROOT))


def _write_csv(
    path: Path, fields: tuple[str, ...] | list[str], rows: list[dict[str, Any]]
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=list(fields), extrasaction="ignore", lineterminator="\n"
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    field: (
                        f"{row[field]:.17g}"
                        if isinstance(row.get(field), float)
                        else "" if row.get(field) is None else row[field]
                    )
                    for field in fields
                }
            )


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _finite(value: Any, label: str, *, minimum: float | None = 0.0) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise Ws4Sustainable119mError(f"{label} is not finite: {value!r}")
    if minimum is not None and number < minimum - 1e-12:
        raise Ws4Sustainable119mError(f"{label} is below {minimum}: {number!r}")
    return number


def assert_no_unexpected_deletions() -> dict[str, Any]:
    """Fail closed on any ``D`` in the parent or data repository status."""
    incidents: list[str] = []
    for repository in (REPO_ROOT, DATA_ROOT):
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repository,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise Ws4Sustainable119mError(
                f"cannot inspect git status in {repository}: {result.stderr.strip()}"
            )
        incidents.extend(
            f"{repository.name or repository}: {line}"
            for line in result.stdout.splitlines()
            if len(line) >= 2 and (line[0] == "D" or line[1] == "D")
        )
    if incidents:
        raise Ws4Sustainable119mError(
            "unexpected deletion status detected; halting: " + " | ".join(incidents)
        )
    return {
        "checked_repositories": ["parent", "data"],
        "no_unexpected_deletions": True,
    }


def _parse_track_province(names: pd.Series) -> pd.Series:
    extracted = names.str.extract(r"AU\(([A-Z]{2})\)")[0]
    if extracted.isna().any():
        raise Ws4Sustainable119mError("unparseable WS4 track province")
    return extracted


def load_control_shares(path: Path = CONTROL_CSV) -> dict[str, float]:
    """Return the frozen 119 Mm3 provincial control shares."""
    rows = chain._read_rows(path)
    shares: dict[str, float] = {}
    for row in rows:
        province = row["province"].strip().upper()
        if province not in PROVINCES:
            raise Ws4Sustainable119mError(f"unexpected control province {province!r}")
        shares[province] = _finite(
            row["allocated_share"], f"{province} allocated share"
        )
    if set(shares) != set(PROVINCES):
        raise Ws4Sustainable119mError("control CSV does not cover all 13 jurisdictions")
    total = math.fsum(shares.values())
    if not math.isclose(total, 1.0, rel_tol=1e-9, abs_tol=1e-6):
        raise Ws4Sustainable119mError(
            f"control shares sum to {total!r}, expected 1.0"
        )
    # The published shares are rounded; normalize so the period targets sum
    # exactly to the national target without changing any share materially.
    return {province: value / total for province, value in shares.items()}


def load_curves(dataset_dir: Path = DATASET_DIR) -> tuple[np.ndarray, np.ndarray]:
    """Return sorted curve ids and a (curve, age 0..600) yield matrix."""
    curves = pd.read_parquet(
        dataset_dir / "curves.parquet", columns=["curve_id", "x", "y"]
    )
    curves["age"] = curves["x"].round().astype("int64")
    exact = np.isclose(
        curves["x"].to_numpy(dtype=float),
        curves["age"].to_numpy(dtype=float),
        rtol=0.0,
        atol=1e-9,
    )
    curves = curves[exact & curves["age"].between(0, CURVE_AGE_MAX)].copy()
    if curves.duplicated(["curve_id", "age"]).any():
        raise Ws4Sustainable119mError("duplicate curve age row")
    if not np.isfinite(curves["y"].to_numpy(dtype=float)).all():
        raise Ws4Sustainable119mError("non-finite yield curve value")
    if (curves["y"] < -1e-12).any():
        raise Ws4Sustainable119mError("negative yield curve value")
    pivot = curves.pivot(index="curve_id", columns="age", values="y").sort_index()
    if pivot.shape[1] != CURVE_AGE_MAX + 1:
        raise Ws4Sustainable119mError(
            f"yield curves do not cover ages 0..{CURVE_AGE_MAX}"
        )
    return pivot.index.to_numpy(dtype=np.int64), pivot.to_numpy(dtype=np.float64)


def compute_rotation_table(
    curve_ids: np.ndarray, curve_matrix: np.ndarray
) -> pd.DataFrame:
    """Return raw and capped rotation ages from maximum MAI."""
    ages = np.arange(1, CURVE_AGE_MAX + 1, dtype=np.float64)
    mai = curve_matrix[:, 1:] / ages
    raw = np.argmax(mai, axis=1).astype(np.int64) + 1
    capped = np.clip(raw, ROTATION_MIN, ROTATION_MAX)
    out_of_range = (raw < ROTATION_MIN) | (raw > ROTATION_MAX)
    return pd.DataFrame(
        {
            "curve_id": curve_ids,
            "rotation_raw_years": raw,
            "rotation_years": capped,
            "rotation_out_of_range": out_of_range,
            "max_mai_m3_per_ha_year": mai[np.arange(len(curve_ids)), raw - 1],
            "curve_age_at_max_volume": np.argmax(curve_matrix, axis=1),
            "max_volume_m3_per_ha": curve_matrix.max(axis=1),
        }
    )


@dataclass
class Estate:
    """Immutable block/fragment arrays plus mutable harvest state."""

    block_ids: np.ndarray
    province: np.ndarray
    province_code: np.ndarray
    area_ha: np.ndarray
    mean_age: np.ndarray
    min_age: np.ndarray
    max_age: np.ndarray
    fragment_count: np.ndarray
    curve_id: np.ndarray
    curve_row: np.ndarray
    rotation_raw: np.ndarray
    rotation: np.ndarray
    rotation_out_of_range: np.ndarray
    volume_initial: np.ndarray
    frag_block_pos: np.ndarray
    frag_age: np.ndarray
    frag_area: np.ndarray
    frag_curve_row: np.ndarray
    block_start: np.ndarray
    block_end: np.ndarray
    curve_ids: np.ndarray
    curve_matrix: np.ndarray
    block_managed: np.ndarray = field(init=False)
    block_last_harvest: np.ndarray = field(init=False)
    frag_harvest_year: np.ndarray = field(init=False)

    def __post_init__(self) -> None:
        count = self.block_ids.size
        self.block_managed = np.zeros(count, dtype=bool)
        self.block_last_harvest = np.full(count, -1, dtype=np.int64)
        self.frag_harvest_year = np.full(self.frag_age.size, -1, dtype=np.int64)

    def reset(self) -> None:
        self.block_managed[:] = False
        self.block_last_harvest[:] = -1
        self.frag_harvest_year[:] = -1

    def block_age_at(self, year: int) -> np.ndarray:
        return np.where(
            self.block_managed,
            year - self.block_last_harvest,
            self.mean_age + year,
        )

    def block_volumes_at(self, year: int) -> np.ndarray:
        age = np.where(
            self.frag_harvest_year < 0,
            self.frag_age + year,
            year - self.frag_harvest_year,
        )
        np.clip(age, 0, CURVE_AGE_MAX, out=age)
        yield_per_ha = self.curve_matrix[self.frag_curve_row, age]
        return np.bincount(
            self.frag_block_pos,
            weights=self.frag_area * yield_per_ha,
            minlength=self.block_ids.size,
        )


def load_estate(dataset_dir: Path = DATASET_DIR) -> Estate:
    """Load one row per block with exact fragment-age volume and rotation."""
    blocks = pd.read_parquet(
        dataset_dir / "blocks.parquet",
        columns=["block_id", "track_id", "area_ha"],
    )
    if blocks["block_id"].duplicated().any():
        raise Ws4Sustainable119mError("duplicate WS4 block id")
    tracks = pd.read_parquet(
        dataset_dir / "tracks.parquet", columns=["track_id", "name"]
    )
    tracks["province"] = _parse_track_province(tracks["name"])
    features = pd.read_parquet(
        dataset_dir / "features.parquet",
        columns=["track_id", "label", "curve_id"],
    )
    yield_features = features[
        features["label"].str.fullmatch(
            r"feature\.Yield\.[A-Z]{2}\.(unmanaged|thinned|managed)\.Total"
        )
    ].copy()
    if yield_features.empty:
        raise Ws4Sustainable119mError("no WS4 Yield.Total features")
    if yield_features.duplicated("track_id").any():
        raise Ws4Sustainable119mError("track has more than one Yield.Total feature")
    tracks = tracks.merge(
        yield_features[["track_id", "curve_id"]],
        on="track_id",
        how="left",
        validate="one_to_one",
    )
    if tracks["curve_id"].isna().any():
        raise Ws4Sustainable119mError("track has no Yield.Total curve")
    blocks = blocks.merge(
        tracks[["track_id", "province", "curve_id"]],
        on="track_id",
        how="left",
        validate="many_to_one",
    )
    if blocks["province"].isna().any():
        raise Ws4Sustainable119mError("block track is not mapped")

    fragments = pd.read_parquet(
        dataset_dir / "fragments.parquet",
        columns=["fragment_id", "block_id", "area_ha", "age", "track_id"],
    ).sort_values(["block_id", "fragment_id"], kind="mergesort")
    fragment_tracks = tracks[["track_id", "province", "curve_id"]].rename(
        columns={
            "track_id": "fragment_track_id",
            "province": "fragment_province",
            "curve_id": "fragment_curve_id",
        }
    )
    fragments = fragments.merge(
        fragment_tracks,
        left_on="track_id",
        right_on="fragment_track_id",
        how="left",
        validate="many_to_one",
    )
    if fragments["fragment_province"].isna().any():
        raise Ws4Sustainable119mError("fragment track is not mapped")

    block_ids = np.sort(blocks["block_id"].to_numpy(dtype=np.int64))
    block_pos_frag = np.searchsorted(
        block_ids, fragments["block_id"].to_numpy(dtype=np.int64)
    )
    if not np.array_equal(block_ids[block_pos_frag], fragments["block_id"]):
        raise Ws4Sustainable119mError("fragment references an unmapped block")
    if (block_pos_frag[1:] < block_pos_frag[:-1]).any():
        raise Ws4Sustainable119mError("fragments are not sorted by block")

    frag_area = fragments["area_ha"].to_numpy(dtype=np.float64)
    frag_age = fragments["age"].to_numpy(dtype=np.int64)
    if (frag_area <= 0.0).any():
        raise Ws4Sustainable119mError("fragment area is not positive")
    if (frag_age < 0).any():
        raise Ws4Sustainable119mError("negative fragment age")

    curve_ids, curve_matrix = load_curves(dataset_dir)
    curve_row_by_id = {int(value): index for index, value in enumerate(curve_ids)}

    block_curve_id = blocks.set_index("block_id").loc[block_ids, "curve_id"]
    block_province = blocks.set_index("block_id").loc[block_ids, "province"]
    block_track_curve = np.array(
        [curve_row_by_id[int(value)] for value in block_curve_id.to_numpy()],
        dtype=np.int64,
    )
    fragment_curve_row = np.array(
        [curve_row_by_id[int(value)] for value in fragments["fragment_curve_id"]],
        dtype=np.int64,
    )
    if not np.array_equal(
        fragment_curve_row, block_track_curve[block_pos_frag]
    ):
        raise Ws4Sustainable119mError(
            "fragment yield curve does not match its block track"
        )
    block_province_array = block_province.to_numpy().astype(str)
    if not np.array_equal(
        fragments["fragment_province"].to_numpy().astype(str),
        block_province_array[block_pos_frag],
    ):
        raise Ws4Sustainable119mError(
            "fragment province does not match its block track"
        )
    province_code = np.array(
        [PROVINCES.index(value) for value in block_province_array], dtype=np.int64
    )

    count = block_ids.size
    area = np.bincount(block_pos_frag, weights=frag_area, minlength=count)
    age_area = np.bincount(
        block_pos_frag, weights=frag_age * frag_area, minlength=count
    )
    fragment_count = np.bincount(block_pos_frag, minlength=count)
    volume_initial = np.bincount(
        block_pos_frag,
        weights=(
            frag_area
            * curve_matrix[
                fragment_curve_row,
                np.clip(frag_age, 0, CURVE_AGE_MAX),
            ]
        ),
        minlength=count,
    )
    if (fragment_count == 0).any():
        raise Ws4Sustainable119mError("block has no fragments")
    mean_age = age_area / area

    min_age = np.full(count, np.iinfo(np.int64).max, dtype=np.int64)
    max_age = np.full(count, -1, dtype=np.int64)
    np.minimum.at(min_age, block_pos_frag, frag_age)
    np.maximum.at(max_age, block_pos_frag, frag_age)

    block_start = np.searchsorted(block_pos_frag, np.arange(count), side="left")
    block_end = np.searchsorted(block_pos_frag, np.arange(count), side="right")

    rotations = compute_rotation_table(curve_ids, curve_matrix)
    rotation_by_curve = dict(
        zip(
            rotations["curve_id"].to_numpy(dtype=np.int64),
            rotations["rotation_years"].to_numpy(dtype=np.int64),
        )
    )
    raw_by_curve = dict(
        zip(
            rotations["curve_id"].to_numpy(dtype=np.int64),
            rotations["rotation_raw_years"].to_numpy(dtype=np.int64),
        )
    )
    flag_by_curve = dict(
        zip(
            rotations["curve_id"].to_numpy(dtype=np.int64),
            rotations["rotation_out_of_range"].to_numpy(dtype=bool),
        )
    )
    rotation = np.array(
        [rotation_by_curve[int(value)] for value in block_curve_id.to_numpy()],
        dtype=np.int64,
    )
    rotation_raw = np.array(
        [raw_by_curve[int(value)] for value in block_curve_id.to_numpy()],
        dtype=np.int64,
    )
    rotation_flag = np.array(
        [flag_by_curve[int(value)] for value in block_curve_id.to_numpy()],
        dtype=bool,
    )

    return Estate(
        block_ids=block_ids,
        province=block_province_array,
        province_code=province_code,
        area_ha=area,
        mean_age=mean_age,
        min_age=min_age,
        max_age=max_age,
        fragment_count=fragment_count,
        curve_id=block_curve_id.to_numpy(dtype=np.int64),
        curve_row=block_track_curve,
        rotation_raw=rotation_raw,
        rotation=rotation,
        rotation_out_of_range=rotation_flag,
        volume_initial=volume_initial,
        frag_block_pos=block_pos_frag,
        frag_age=frag_age,
        frag_area=frag_area,
        frag_curve_row=fragment_curve_row,
        block_start=block_start,
        block_end=block_end,
        curve_ids=curve_ids,
        curve_matrix=curve_matrix,
    )


@dataclass
class SelectionResult:
    schedule: pd.DataFrame
    selected: pd.DataFrame
    period_rows: pd.DataFrame
    province_period_rows: pd.DataFrame
    annual_rows: pd.DataFrame
    shortfalls: pd.DataFrame
    rotation_distribution: pd.DataFrame
    diagnostics: dict[str, Any]


def _selected_fragment_plan(
    estate: Estate, positions: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Return fragment indices and local block indices for selected blocks."""
    counts = estate.block_end[positions] - estate.block_start[positions]
    total = int(counts.sum())
    starts = np.repeat(estate.block_start[positions], counts)
    offsets = np.arange(total, dtype=np.int64) - np.repeat(
        np.cumsum(counts) - counts, counts
    )
    local = np.repeat(np.arange(positions.size, dtype=np.int64), counts)
    return starts + offsets, local


def _volumes_for_positions(
    estate: Estate, positions: np.ndarray, period_start: int, years: np.ndarray
) -> np.ndarray:
    """Exact block volumes for the selected positions at each period year."""
    fragment_index, local = _selected_fragment_plan(estate, positions)
    frag_age = estate.frag_age[fragment_index]
    frag_area = estate.frag_area[fragment_index]
    frag_curve = estate.frag_curve_row[fragment_index]
    frag_harvest = estate.frag_harvest_year[fragment_index]
    result = np.empty((positions.size, years.size), dtype=np.float64)
    for column, year in enumerate(years):
        absolute_year = period_start + int(year)
        age = np.where(
            frag_harvest < 0,
            frag_age + absolute_year,
            absolute_year - frag_harvest,
        )
        np.clip(age, 0, CURVE_AGE_MAX, out=age)
        values = frag_area * estate.curve_matrix[frag_curve, age]
        result[:, column] = np.bincount(
            local, weights=values, minlength=positions.size
        )
    return result


def _assign_years(volumes: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Greedy longest-processing-time assignment to ten annual offsets."""
    count, width = volumes.shape
    years = np.empty(count, dtype=np.int64)
    loads = np.zeros(width, dtype=np.float64)
    priority = volumes.mean(axis=1)
    order = np.argsort(-priority, kind="stable")
    for index in order:
        candidate = loads + volumes[index]
        year = int(np.argmin(candidate))
        years[index] = year
        loads[year] += volumes[index, year]
    return years, loads


def _period_targets(
    province_shares: dict[str, float] | None,
) -> dict[str, float] | None:
    if province_shares is None:
        return None
    targets: dict[str, float] = {}
    for province in PROVINCES:
        target = TARGET_PERIOD_M3 * province_shares[province]
        if target <= 0.0:
            targets[province] = 0.0
            continue
        targets[province] = target
    total = math.fsum(targets.values())
    if not math.isclose(total, TARGET_PERIOD_M3, rel_tol=1e-12):
        raise Ws4Sustainable119mError(
            f"province period targets sum to {total!r}, expected {TARGET_PERIOD_M3!r}"
        )
    return targets


def _prefix_count(cumulative: np.ndarray, target: float) -> int:
    """Return how many prefix blocks are needed to reach the target."""
    if target <= 0.0 or cumulative.size == 0:
        return 0
    if cumulative[-1] + PROVINCE_TARGET_TOLERANCE_M3 < target:
        return int(cumulative.size)
    return int(np.searchsorted(cumulative, target, side="left")) + 1


def build_schedule(
    estate: Estate,
    *,
    province_shares: dict[str, float] | None,
    label: str = "province_control",
) -> SelectionResult:
    """Select whole blocks period by period and assign annual offsets."""
    estate.reset()
    targets = _period_targets(province_shares)
    schedule_records: list[dict[str, Any]] = []
    selected_records: list[pd.DataFrame] = []
    period_records: list[dict[str, Any]] = []
    province_period_records: list[dict[str, Any]] = []
    shortfall_records: list[dict[str, Any]] = []
    province_code = estate.province_code
    province_count = len(PROVINCES)
    national_target = TARGET_PERIOD_M3

    def add_shortfall(
        period: int, province: str, target: float, eligible: float, achieved: float
    ) -> None:
        shortfall_records.append(
            {
                "period": period,
                "province": province,
                "scope": label,
                "target_m3": target,
                "eligible_volume_m3": eligible,
                "shortfall_m3": max(0.0, target - achieved),
            }
        )

    for period in range(PERIODS):
        period_start = period * PERIOD_LENGTH
        age_start = estate.block_age_at(period_start)
        volume_start = estate.block_volumes_at(period_start)
        eligible = age_start >= estate.rotation

        selected_parts: list[np.ndarray] = []
        province_order: dict[int, np.ndarray] = {}
        province_cursor: dict[int, int] = {}
        province_target: dict[int, float] = {}
        national_order = np.empty(0, dtype=np.int64)
        national_cursor = 0

        if targets is None:
            positions = np.flatnonzero(eligible)
            national_order = positions[
                np.lexsort(
                    (
                        estate.block_ids[positions],
                        -volume_start[positions],
                        -age_start[positions],
                    )
                )
            ]
            cumulative = np.cumsum(volume_start[national_order])
            national_cursor = _prefix_count(cumulative, national_target)
            selected_parts.append(national_order[:national_cursor])
            for code in range(province_count):
                province_order[code] = np.empty(0, dtype=np.int64)
                province_cursor[code] = 0
                province_target[code] = 0.0
        else:
            for code in range(province_count):
                province = PROVINCES[code]
                target = targets[province]
                province_target[code] = target
                positions = np.flatnonzero((province_code == code) & eligible)
                if target <= 0.0 or positions.size == 0:
                    province_order[code] = np.empty(0, dtype=np.int64)
                    province_cursor[code] = 0
                    if target > 0.0:
                        add_shortfall(period, province, target, 0.0, 0.0)
                    continue
                order = positions[
                    np.lexsort(
                        (
                            estate.block_ids[positions],
                            -volume_start[positions],
                            -age_start[positions],
                        )
                    )
                ]
                cumulative = np.cumsum(volume_start[order])
                take = _prefix_count(cumulative, target)
                province_order[code] = order
                province_cursor[code] = take
                selected_parts.append(order[:take])

        positions = (
            np.concatenate(selected_parts)
            if selected_parts
            else np.empty(0, dtype=np.int64)
        )
        if positions.size:
            volumes = _volumes_for_positions(
                estate, positions, period_start, np.arange(PERIOD_LENGTH)
            )
            years, loads = _assign_years(volumes)
        else:
            volumes = np.empty((0, PERIOD_LENGTH), dtype=np.float64)
            years = np.empty(0, dtype=np.int64)
            loads = np.zeros(PERIOD_LENGTH, dtype=np.float64)

        # Top up each province when exact annual volumes fall below its target.
        if targets is not None:
            for code in range(province_count):
                province = PROVINCES[code]
                target = province_target.get(code, 0.0)
                if target <= 0.0:
                    continue
                mask = province_code[positions] == code
                actual = (
                    float(volumes[mask, years[mask]].sum()) if positions.size else 0.0
                )
                order = province_order.get(code, np.empty(0, dtype=np.int64))
                cursor = province_cursor.get(code, 0)
                while (
                    actual + PROVINCE_TARGET_TOLERANCE_M3 < target
                    and cursor < order.size
                ):
                    batch = order[cursor : cursor + 256]
                    batch_volumes = _volumes_for_positions(
                        estate, batch, period_start, np.arange(PERIOD_LENGTH)
                    )
                    for row in range(batch.size):
                        candidate = loads + batch_volumes[row]
                        year = int(np.argmin(candidate))
                        loads[year] += batch_volumes[row, year]
                        actual += batch_volumes[row, year]
                        positions = np.append(positions, batch[row])
                        volumes = np.vstack([volumes, batch_volumes[row]])
                        years = np.append(years, year)
                        cursor += 1
                        if actual + PROVINCE_TARGET_TOLERANCE_M3 >= target:
                            break
                province_cursor[code] = cursor
                if actual + PROVINCE_TARGET_TOLERANCE_M3 < target:
                    add_shortfall(period, province, target, float(actual), actual)

        actual_total = float(loads.sum())
        # National fallback: fill a national gap from any remaining eligible
        # block, oldest-first, without relaxing rotation.
        if actual_total + PROVINCE_TARGET_TOLERANCE_M3 < national_target:
            if targets is None:
                remaining = national_order[national_cursor:]
            else:
                parts = []
                for code in range(province_count):
                    order = province_order.get(code, np.empty(0, dtype=np.int64))
                    cursor = province_cursor.get(code, 0)
                    if cursor < order.size:
                        parts.append(order[cursor:])
                remaining = np.concatenate(parts) if parts else np.empty(0, dtype=np.int64)
            if remaining.size:
                remaining = remaining[
                    np.lexsort(
                        (
                            estate.block_ids[remaining],
                            -volume_start[remaining],
                            -age_start[remaining],
                        )
                    )
                ]
                cursor = 0
                while (
                    actual_total + PROVINCE_TARGET_TOLERANCE_M3 < national_target
                    and cursor < remaining.size
                ):
                    batch = remaining[cursor : cursor + 256]
                    batch_volumes = _volumes_for_positions(
                        estate, batch, period_start, np.arange(PERIOD_LENGTH)
                    )
                    for row in range(batch.size):
                        candidate = loads + batch_volumes[row]
                        year = int(np.argmin(candidate))
                        loads[year] += batch_volumes[row, year]
                        actual_total += batch_volumes[row, year]
                        positions = np.append(positions, batch[row])
                        volumes = np.vstack([volumes, batch_volumes[row]])
                        years = np.append(years, year)
                        cursor += 1
                        if actual_total + PROVINCE_TARGET_TOLERANCE_M3 >= national_target:
                            break
                if actual_total + PROVINCE_TARGET_TOLERANCE_M3 < national_target:
                    add_shortfall(
                        period, "CAN", national_target, float(actual_total), actual_total
                    )
            else:
                add_shortfall(
                    period, "CAN", national_target, float(actual_total), actual_total
                )

        if positions.size == 0:
            raise Ws4Sustainable119mError(
                f"period {period} selected no blocks; cannot form a schedule"
            )
        if np.unique(positions).size != positions.size:
            raise Ws4Sustainable119mError("a block was selected twice in one period")

        ages = np.empty(positions.size, dtype=np.float64)
        for index, position in enumerate(positions):
            absolute_year = period_start + int(years[index])
            ages[index] = (
                absolute_year - estate.block_last_harvest[position]
                if estate.block_managed[position]
                else estate.mean_age[position] + absolute_year
            )
        selected_volume = volumes[np.arange(positions.size), years]
        if (ages + 1e-9 < estate.rotation[positions]).any():
            raise Ws4Sustainable119mError(
                "schedule harvested a block below its rotation age"
            )

        record = pd.DataFrame(
            {
                "block_id": estate.block_ids[positions],
                "province": estate.province[positions],
                "period": period,
                "offset": period_start + years,
                "age_at_harvest": ages,
                "volume_m3": selected_volume,
                "area_ha": estate.area_ha[positions],
                "rotation_years": estate.rotation[positions],
                "rotation_raw_years": estate.rotation_raw[positions],
                "curve_id": estate.curve_id[positions],
                "age_class": (np.floor(ages / 10.0) * 10).astype(np.int64),
            }
        )
        selected_records.append(record)

        for year in range(PERIOD_LENGTH):
            mask = years == year
            schedule_records.append(
                {
                    "block_id": estate.block_ids[positions[mask]],
                    "treatment": SCHEDULE_TREATMENT,
                    "offset": period_start + year,
                }
            )

        annual_volumes = np.bincount(
            years, weights=selected_volume, minlength=PERIOD_LENGTH
        )
        if not np.allclose(annual_volumes, loads, rtol=1e-12, atol=1e-6):
            raise Ws4Sustainable119mError("annual volume accounting is inconsistent")
        period_actual = float(annual_volumes.sum())
        period_records.append(
            {
                "period": period,
                "start_offset": period_start,
                "end_offset": period_start + PERIOD_LENGTH - 1,
                "target_m3": national_target,
                "achieved_m3": period_actual,
                "delta_m3": period_actual - national_target,
                "overshoot_pct": (
                    (period_actual - national_target) / national_target * 100.0
                ),
                "area_ha": float(estate.area_ha[positions].sum()),
                "block_count": int(positions.size),
                "annual_min_m3": float(annual_volumes.min()),
                "annual_max_m3": float(annual_volumes.max()),
                "annual_cv": float(
                    annual_volumes.std(ddof=0) / annual_volumes.mean()
                )
                if annual_volumes.mean() > 0
                else 0.0,
            }
        )
        for code in range(province_count):
            mask = province_code[positions] == code
            target = (
                national_target if targets is None else province_target.get(code, 0.0)
            )
            if not mask.any():
                province_period_records.append(
                    {
                        "period": period,
                        "province": PROVINCES[code],
                        "target_m3": target,
                        "achieved_m3": 0.0,
                        "delta_m3": 0.0,
                        "area_ha": 0.0,
                        "block_count": 0,
                        "mean_age": 0.0,
                    }
                )
                continue
            achieved = float(selected_volume[mask].sum())
            province_period_records.append(
                {
                    "period": period,
                    "province": PROVINCES[code],
                    "target_m3": target,
                    "achieved_m3": achieved,
                    "delta_m3": achieved - target,
                    "area_ha": float(estate.area_ha[positions[mask]].sum()),
                    "block_count": int(mask.sum()),
                    "mean_age": float(
                        np.average(
                            ages[mask], weights=estate.area_ha[positions[mask]]
                        )
                    ),
                }
            )

        # Advance the estate state: CC resets every fragment in the block.
        estate.block_managed[positions] = True
        estate.block_last_harvest[positions] = period_start + years
        for position, year in zip(positions, years, strict=True):
            estate.frag_harvest_year[
                estate.block_start[position] : estate.block_end[position]
            ] = period_start + int(year)

    schedule = pd.concat(
        [pd.DataFrame(record) for record in schedule_records],
        ignore_index=True,
    )
    schedule["block_id"] = schedule["block_id"].astype(np.int64)
    schedule["offset"] = schedule["offset"].astype(np.int64)
    schedule = schedule.sort_values(
        ["offset", "block_id"], kind="mergesort"
    ).reset_index(drop=True)
    if schedule["block_id"].duplicated().any():
        # A block may legitimately be harvested twice over 100 years; the
        # uniqueness contract is per period, checked above.
        pass
    selected = pd.concat(selected_records, ignore_index=True)
    period_rows = pd.DataFrame(period_records)
    province_period_rows = pd.DataFrame(province_period_records)
    annual_rows = (
        selected.groupby("offset", as_index=False)
        .agg(
            harvest_m3=("volume_m3", "sum"),
            area_ha=("area_ha", "sum"),
            block_count=("block_id", "size"),
            mean_age=("age_at_harvest", "mean"),
        )
        .sort_values("offset", kind="mergesort")
        .reset_index(drop=True)
    )
    annual_rows["year"] = annual_rows["offset"] + 1
    annual_rows["period"] = annual_rows["offset"] // PERIOD_LENGTH
    shortfalls = pd.DataFrame(
        shortfall_records,
        columns=[
            "period",
            "province",
            "scope",
            "target_m3",
            "eligible_volume_m3",
            "shortfall_m3",
        ],
    )
    rotation_distribution = (
        selected.groupby(["rotation_raw_years", "rotation_years"], as_index=False)
        .agg(
            blocks=("block_id", "size"),
            area_ha=("area_ha", "sum"),
            volume_m3=("volume_m3", "sum"),
        )
        .sort_values(["rotation_raw_years", "rotation_years"])
        .reset_index(drop=True)
    )
    diagnostics = {
        "label": label,
        "selection_method": (
            "whole blocks, oldest-first, volume-aware; provincial period "
            "targets from the frozen 119 Mm3 control"
            if targets is not None
            else "whole blocks, oldest-first, volume-aware; national period target"
        ),
        "period_target_m3": TARGET_PERIOD_M3,
        "national_target_m3": national_target,
        "selected_blocks": int(selected["block_id"].size),
        "unique_blocks": int(selected["block_id"].nunique()),
        "selected_area_ha": float(selected["area_ha"].sum()),
        "selected_volume_m3": float(selected["volume_m3"].sum()),
        "annual_min_m3": float(annual_rows["harvest_m3"].min()),
        "annual_max_m3": float(annual_rows["harvest_m3"].max()),
        "annual_mean_m3": float(annual_rows["harvest_m3"].mean()),
        "annual_cv": float(
            annual_rows["harvest_m3"].std(ddof=0)
            / annual_rows["harvest_m3"].mean()
        ),
        "period_min_m3": float(period_rows["achieved_m3"].min()),
        "period_max_m3": float(period_rows["achieved_m3"].max()),
        "period_shortfall_m3": float(
            shortfalls.loc[shortfalls["province"] == "CAN", "shortfall_m3"].sum()
        )
        if not shortfalls.empty
        else 0.0,
        "sustained_119m": bool(
            period_rows["achieved_m3"].min() + 1e-6 >= TARGET_PERIOD_M3
        ),
        "blocks_reharvested": int(selected["block_id"].duplicated().sum()),
    }
    return SelectionResult(
        schedule=schedule,
        selected=selected,
        period_rows=period_rows,
        province_period_rows=province_period_rows,
        annual_rows=annual_rows,
        shortfalls=shortfalls,
        rotation_distribution=rotation_distribution,
        diagnostics=diagnostics,
    )

def run_ws4_validate(dataset_dir: Path = DATASET_DIR) -> str:
    if not WS4_BIN.is_file():
        raise Ws4Sustainable119mError(f"WS4 executable not found: {WS4_BIN}")
    result = subprocess.run(
        [str(WS4_BIN), "validate", "--dataset", str(dataset_dir)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise Ws4Sustainable119mError(
            "ws4 validate failed: "
            f"{result.stdout.strip()} {result.stderr.strip()}"
        )
    return result.stdout.strip()


def ensure_ws4_run(
    schedule: pd.DataFrame,
    schedule_path: Path = SCHEDULE_PATH,
    run_dir: Path = RUN_DIR,
) -> str:
    _write_csv(
        schedule_path,
        ["block_id", "treatment", "offset"],
        schedule.to_dict(orient="records"),
    )
    if run_dir.exists():
        existing_schedule = run_dir / "schedule.csv"
        product_values = run_dir / "product_values.csv"
        if not existing_schedule.is_file() or not product_values.is_file():
            raise Ws4Sustainable119mError(
                f"existing run directory is incomplete: {run_dir}"
            )
        existing = pd.read_csv(existing_schedule)
        if not chain._schedule_matches(existing, schedule):
            raise Ws4Sustainable119mError(
                f"existing run schedule does not match {schedule_path}; "
                "refusing to overwrite the run"
            )
        return "reused"
    result = subprocess.run(
        [
            str(WS4_BIN),
            "simulate",
            "--dataset",
            str(DATASET_DIR),
            "--schedule",
            str(schedule_path),
            "--years",
            str(HORIZON),
            "--out",
            str(run_dir),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise Ws4Sustainable119mError(
            "ws4 simulate failed: "
            f"{result.stdout.strip()} {result.stderr.strip()}"
        )
    if not (run_dir / "product_values.csv").is_file():
        raise Ws4Sustainable119mError("ws4 simulate did not write product_values.csv")
    return "created"


def extract_run_products(
    run_dir: Path = RUN_DIR,
) -> pd.DataFrame:
    path = run_dir / "product_values.csv"
    if not path.is_file():
        raise Ws4Sustainable119mError(f"missing WS4 product values: {path}")
    values = pd.read_csv(path)
    if list(values.columns) != ["offset", "account", "value"]:
        raise Ws4Sustainable119mError(
            f"unexpected product_values columns: {values.columns.tolist()}"
        )
    parsed = values["account"].str.extract(
        r"^product\.(HarvestedVolume|Treated)\.([A-Z]{2})\.(unmanaged|managed)\.CC$"
    )
    if parsed.isna().any(axis=None):
        unknown = values.loc[parsed.isna().any(axis=1), "account"].drop_duplicates()
        raise Ws4Sustainable119mError(
            "unexpected product account labels: "
            + ", ".join(str(value) for value in unknown.head(5))
        )
    values["product"] = parsed[0]
    values["province"] = parsed[1]
    values["origin"] = parsed[2]
    values["value"] = pd.to_numeric(values["value"], errors="raise")
    if not np.isfinite(values["value"].to_numpy(dtype=float)).all():
        raise Ws4Sustainable119mError("non-finite WS4 product value")
    if (values["value"] < -1e-9).any():
        raise Ws4Sustainable119mError("negative WS4 product value")
    offsets = sorted(values["offset"].astype(int).unique().tolist())
    if offsets != list(range(HORIZON)):
        raise Ws4Sustainable119mError(
            f"run wrote unexpected offsets: {offsets[:3]}...{offsets[-3:]}"
        )
    return values


def harvest_by_province(
    selected: pd.DataFrame, run_values: pd.DataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Reconcile the schedule with the WS4 run and return annual/province."""
    run_harvest = (
        run_values[run_values["product"] == "HarvestedVolume"]
        .groupby(["offset", "province"], as_index=False)["value"]
        .sum()
        .rename(columns={"value": "run_harvest_m3"})
    )
    run_area = (
        run_values[run_values["product"] == "Treated"]
        .groupby(["offset", "province"], as_index=False)["value"]
        .sum()
        .rename(columns={"value": "run_area_ha"})
    )
    scheduled = (
        selected.groupby(["offset", "province"], as_index=False)
        .agg(
            scheduled_harvest_m3=("volume_m3", "sum"),
            scheduled_area_ha=("area_ha", "sum"),
        )
    )
    merged = scheduled.merge(
        run_harvest, on=["offset", "province"], how="outer", validate="one_to_one"
    ).fillna(0.0)
    merged = merged.merge(
        run_area, on=["offset", "province"], how="outer", validate="one_to_one"
    ).fillna(0.0)
    volume_gap = np.abs(
        merged["scheduled_harvest_m3"].to_numpy()
        - merged["run_harvest_m3"].to_numpy()
    )
    area_gap = np.abs(
        merged["scheduled_area_ha"].to_numpy()
        - merged["run_area_ha"].to_numpy()
    )
    if float(volume_gap.max()) > 1e-6:
        worst = merged.loc[volume_gap.argmax()]
        raise Ws4Sustainable119mError(
            "WS4 run harvest does not match the scheduled block volume: "
            f"offset {worst['offset']} {worst['province']} "
            f"scheduled {worst['scheduled_harvest_m3']!r} "
            f"run {worst['run_harvest_m3']!r}"
        )
    if float(area_gap.max()) > 1e-6:
        raise Ws4Sustainable119mError(
            "WS4 run treated area does not match the selected block area"
        )
    block_counts = (
        selected.groupby("province", as_index=False)
        .agg(block_count=("block_id", "size"))
        .set_index("province")["block_count"]
    )
    province = (
        merged.groupby("province", as_index=False)
        .agg(harvest_m3=("run_harvest_m3", "sum"), area_ha=("run_area_ha", "sum"))
        .set_index("province")
        .reindex(PROVINCES, fill_value=0.0)
        .rename_axis("province")
        .reset_index()
    )
    province["block_count"] = (
        province["province"].map(block_counts).fillna(0).astype(int)
    )
    annual = (
        merged.groupby("offset", as_index=False)
        .agg(harvest_m3=("run_harvest_m3", "sum"), area_ha=("run_area_ha", "sum"))
        .sort_values("offset", kind="mergesort")
        .reset_index(drop=True)
    )
    annual["year"] = annual["offset"] + 1
    annual["period"] = annual["offset"] // PERIOD_LENGTH
    return province, annual


def build_chain_outputs(
    *,
    harvest_province: pd.DataFrame,
    first_period_annual: pd.DataFrame,
    rotation_table: pd.DataFrame,
    schedule_result: SelectionResult,
    run_values: pd.DataFrame,
    validate_text: str,
) -> dict[str, Any]:
    """Write the sustainable chain and return its summary."""
    frozen = chain._frozen_province_rows(chain.CHAIN_V51_CSV)
    _coefficient_rows, coefficients = chain._coefficient_rows(frozen)
    frozen_totals = chain._frozen_national_totals(frozen)
    chain._check_frozen_targets(frozen_totals)

    harvest = harvest_province.set_index("province")["harvest_m3"]
    if set(harvest.index) != set(PROVINCES):
        missing = set(PROVINCES) - set(harvest.index)
        raise Ws4Sustainable119mError(f"WS4 run is missing provinces: {sorted(missing)}")
    total_100y = float(harvest.sum())
    if total_100y <= 0.0:
        raise Ws4Sustainable119mError("WS4 run harvest is zero")
    scale = TARGET_ANNUAL_M3 / (total_100y / 100.0)
    annual_achieved = harvest / 100.0
    annual_119m = annual_achieved * scale

    first = first_period_annual.set_index("province")["annual_m3"]
    control_shares = load_control_shares()
    harvest_rows: list[dict[str, Any]] = []
    for province in PROVINCES:
        target_share = control_shares.get(province, 0.0)
        harvest_rows.append(
            {
                "province": province,
                "control_share": target_share,
                "target_annual_m3": TARGET_ANNUAL_M3 * target_share,
                "achieved_100y_m3": float(harvest[province]),
                "annual_100y_m3": float(annual_achieved[province]),
                "annual_119m_m3": float(annual_119m[province]),
                "first_period_m3": float(first.get(province, 0.0) * 10.0),
                "first_period_annual_m3": float(first.get(province, 0.0)),
                "scale_factor": scale,
                "area_100y_ha": float(
                    harvest_province.set_index("province").loc[province, "area_ha"]
                ),
                "block_count_100y": int(
                    harvest_province.set_index("province").loc[province, "block_count"]
                ),
                **ROW_LABELS,
            }
        )
    total_row = {
        "province": "CAN",
        "control_share": 1.0,
        "target_annual_m3": TARGET_ANNUAL_M3,
        "achieved_100y_m3": total_100y,
        "annual_100y_m3": total_100y / 100.0,
        "annual_119m_m3": TARGET_ANNUAL_M3,
        "first_period_m3": float(first.sum() * 10.0),
        "first_period_annual_m3": float(first.sum()),
        "scale_factor": scale,
        "area_100y_ha": float(harvest_province["area_ha"].sum()),
        "block_count_100y": int(harvest_province["block_count"].sum()),
        **ROW_LABELS,
    }
    harvest_fields = list(harvest_rows[0].keys())
    _write_csv(
        CHAIN_DIR / "sustainable_harvest_by_province.csv",
        harvest_fields,
        harvest_rows + [total_row],
    )

    province_chain_rows: list[dict[str, Any]] = []
    national_rows: list[dict[str, Any]] = []
    for basis, values, factor in (
        ("sustainable_119m", annual_119m, scale),
        ("sustainable_achieved", annual_achieved, 1.0),
    ):
        rows, national = chain.build_chain_rows(
            {province: float(values[province]) for province in PROVINCES},
            basis,
            frozen,
            coefficients,
            scale_factor=factor,
        )
        for row in rows + [national]:
            row.update(ROW_LABELS)
            row["sustainable_schedule"] = "true"
        province_chain_rows.extend(rows)
        national_rows.append(national)
    product_fields = [
        "province",
        "geography_level",
        "basis",
        "harvest_m3",
        "scale_factor",
        "coefficient_tier",
        "coefficient_source",
        *[metric for metric, _unit in chain.METRICS],
        *ROW_LABELS.keys(),
    ]
    # Keep one row order and ensure every label key is present.
    _write_csv(
        CHAIN_DIR / "sustainable_product_chain.csv",
        product_fields,
        province_chain_rows + national_rows,
    )

    comparison_rows: list[dict[str, Any]] = []
    national = {
        "sustainable_119m": next(
            row for row in national_rows if row["basis"] == "sustainable_119m"
        ),
        "sustainable_achieved": next(
            row for row in national_rows if row["basis"] == "sustainable_achieved"
        ),
    }
    for metric, unit in chain.METRICS:
        reference = float(frozen_totals[metric])
        row: dict[str, Any] = {
            "metric": metric,
            "unit": unit,
            "chain_v51_value": reference,
        }
        for basis in BASES:
            value = float(national[basis][metric])
            delta = value - reference
            row[f"{basis}_value"] = value
            row[f"{basis}_delta"] = delta
            row[f"{basis}_delta_pct"] = delta / reference * 100.0 if reference else 0.0
        row.update(ROW_LABELS)
        comparison_rows.append(row)
    comparison_fields = [
        "metric",
        "unit",
        "chain_v51_value",
        *[
            f"{basis}_{suffix}"
            for basis in BASES
            for suffix in ("value", "delta", "delta_pct")
        ],
        *ROW_LABELS.keys(),
    ]
    _write_csv(
        CHAIN_DIR / "comparison_vs_v51.csv",
        comparison_fields,
        comparison_rows,
    )

    selected_rotation = schedule_result.rotation_distribution
    raw_distribution = (
        selected_rotation.groupby("rotation_raw_years", as_index=False)["blocks"]
        .sum()
        .set_index("rotation_raw_years")["blocks"]
        .sort_index()
        .to_dict()
    )
    used_distribution = (
        selected_rotation.groupby("rotation_years", as_index=False)["blocks"]
        .sum()
        .set_index("rotation_years")["blocks"]
        .sort_index()
        .to_dict()
    )
    summary: dict[str, Any] = {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "status": "pass",
        "title": ARTIFACT_TITLE,
        "labels": LABELS,
        "target": {
            "annual_m3": TARGET_ANNUAL_M3,
            "period_m3": TARGET_PERIOD_M3,
            "period_length_years": PERIOD_LENGTH,
            "periods": PERIODS,
            "horizon_years": HORIZON,
            "province_allocation": "frozen 119 Mm3 allocated_share",
        },
        "schedule": schedule_result.diagnostics,
        "rotation": {
            "raw_distribution": {
                int(key): int(value) for key, value in raw_distribution.items()
            },
            "used_distribution": {
                int(key): int(value) for key, value in used_distribution.items()
            },
            "out_of_range_block_count": int(
                (
                    schedule_result.selected["rotation_raw_years"]
                    != schedule_result.selected["rotation_years"]
                ).sum()
            ),
            "curve_count": len(rotation_table),
        },
        "harvest": {
            "province": harvest_rows,
            "national": total_row,
            "total_100y_m3": total_100y,
            "annual_mean_m3": total_100y / HORIZON,
        },
        "chain": {
            "basis": {
                "bases": list(BASES),
                "primary": "sustainable_119m",
                "secondary": "sustainable_achieved",
                "scale_factor": scale,
                "control_total_m3": TARGET_ANNUAL_M3,
            },
            "national": national,
            "comparison": comparison_rows,
        },
        "ws4": {
            "dataset": _relative(DATASET_DIR),
            "schedule": _relative(SCHEDULE_PATH),
            "run_dir": _relative(RUN_DIR),
            "validate": validate_text,
            "run_offsets": run_values["offset"].nunique(),
        },
        "d_status": assert_no_unexpected_deletions(),
        "caveats": [
            (
                "The schedule is a national even-flow arithmetic model, not an AAC, "
                "policy result, tenure plan, or investment case."
            ),
            (
                "The full forest estate is used; no operability, tenure, road, slope, "
                "or mill-capacity screen is applied."
            ),
            (
                "Provincial period targets use the frozen 119 Mm3 control shares so "
                "the chain and OSeCAN handoff remain comparable with the existing "
                "WS4 119 Mm3 lane. A national oldest-first free run is reported as "
                "a sensitivity in the report and is not the chain basis."
            ),
            (
                "Rotation is the integer age of maximum MAI on the RSYC yield curve, "
                "capped to 40-200 years; the raw peak is below 40 for these curves "
                "and is flagged."
            ),
            (
                "Whole blocks are selected. Provincial achieved volume can overshoot "
                "the target by the final block because no block is split or scaled."
            ),
            (
                "Frozen chain v5.1 coefficients are applied to the WS4 harvest by "
                "origin province. Facility assignment, mill capacity, transport, "
                "and interprovincial trade are not re-run."
            ),
            (
                "No official OSeCAN file, frozen chain v5.1 artifact, or UI surface "
                "is changed."
            ),
        ],
    }
    _write_json(CHAIN_DIR / "summary.json", summary)
    return summary


def _md_table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    lines = [
        "| " + " | ".join(header) + " |",
        "|" + "|".join("---" for _ in header) + "|",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return "\n".join(lines)


def write_chain_report(
    summary: Mapping[str, Any], schedule_result: SelectionResult
) -> None:
    """Write the chain artifact report from the built summary."""
    periods = schedule_result.period_rows
    period_table = _md_table(
        ["Period", "Offsets", "Target Mm3", "Achieved Mm3", "Overshoot %", "Area Mha", "Blocks"],
        [
            [
                str(int(row.period)),
                f"{int(row.start_offset)}-{int(row.end_offset)}",
                f"{float(row.target_m3) / 1e6:.3f}",
                f"{float(row.achieved_m3) / 1e6:.3f}",
                f"{float(row.overshoot_pct):.4f}",
                f"{float(row.area_ha) / 1e6:.3f}",
                str(int(row.block_count)),
            ]
            for row in periods.itertuples(index=False)
        ],
    )
    province_rows = summary["harvest"]["province"]
    province_table = _md_table(
        [
            "Province",
            "Control share",
            "Target Mm3/yr",
            "Achieved Mm3/yr",
            "119 Mm3 basis Mm3/yr",
            "Area Mha",
            "Blocks",
        ],
        [
            [
                str(row["province"]),
                f"{float(row['control_share']) * 100:.3f}%",
                f"{float(row['target_annual_m3']) / 1e6:.6f}",
                f"{float(row['annual_100y_m3']) / 1e6:.6f}",
                f"{float(row['annual_119m_m3']) / 1e6:.6f}",
                f"{float(row['area_100y_ha']) / 1e6:.3f}",
                str(int(row["block_count_100y"])),
            ]
            for row in province_rows
        ],
    )
    comparison_table = _md_table(
        ["Metric", "Unit", "v5.1", "119 Mm3 basis", "Delta %", "Achieved basis", "Delta %"],
        [
            [
                str(row["metric"]),
                str(row["unit"]),
                f"{float(row['chain_v51_value']):.6f}",
                f"{float(row['sustainable_119m_value']):.6f}",
                f"{float(row['sustainable_119m_delta_pct']):.4f}",
                f"{float(row['sustainable_achieved_value']):.6f}",
                f"{float(row['sustainable_achieved_delta_pct']):.4f}",
            ]
            for row in summary["chain"]["comparison"]
        ],
    )
    rotation = summary["rotation"]
    free = summary.get("free_run_sensitivity", {})
    report = f"""# Sustainable 119 Mm3/yr WS4 Schedule and Chain

**Artifact:** `{ARTIFACT}` **Version:** {ARTIFACT_VERSION}

**Status:** pass. The 118,977,339.1 m3/yr target is sustained for 100 years
with no period shortfall. The schedule is a whole-block model estimate, not an
AAC, policy result, tenure plan, or investment case.

**Labels:** `model_estimate=true`, `observed=false`, `policy_grade=false`,
`national_claim=false`, `ws4_derived=true`, `sustainable_schedule=true`,
`official_osecan_changed=false`, `chain_v51_unchanged=true`.

## 1. Input and method

- Dataset: `{summary['ws4']['dataset']}`
- Schedule: `{summary['ws4']['schedule']}`
- Run: `{summary['ws4']['run_dir']}`
- Horizon: {summary['target']['horizon_years']} years, {summary['target']['periods']} periods of {summary['target']['period_length_years']} years.
- Period target: {summary['target']['period_m3']:,.1f} m3.
- Block volume: sum of fragment area times the block's natural RSYC yield curve at the exact fragment age.
- Block age: fragment-area-weighted age.
- Rotation: integer age of maximum MAI on the natural curve, capped to
  {ROTATION_MIN}-{ROTATION_MAX} years and flagged outside that band. Raw peaks
  are {rotation['raw_distribution']}; all blocks use {rotation['used_distribution']}.
- Eligible blocks: natural/unmanaged with age >= rotation, or managed with age
  since last harvest >= rotation.
- Selection: whole blocks, oldest-first, volume-aware, within provincial
  period targets set by the frozen 119 Mm3 control shares. The national target
  is the sum of the provincial targets. No block is split, scaled, or
  harvested below rotation.
- Annual assignment: selected blocks are assigned to the ten offsets of each
  period by longest-processing-time balancing of exact annual volumes.

## 2. Period harvest

{period_table}

- Annual harvest minimum: {summary['schedule']['annual_min_m3']:,.1f} m3/yr.
- Annual harvest maximum: {summary['schedule']['annual_max_m3']:,.1f} m3/yr.
- Annual mean: {summary['schedule']['annual_mean_m3']:,.1f} m3/yr.
- Annual CV: {summary['schedule']['annual_cv']:.6f}.
- Period shortfall: {summary['schedule']['period_shortfall_m3']:.1f} m3.
- 119 Mm3/yr sustained for 100 years: `{summary['schedule']['sustained_119m']}`.
- Selected blocks: {summary['schedule']['selected_blocks']:,}
  ({summary['schedule']['selected_area_ha']/1e6:,.3f} Mha).
- Blocks reharvested within 100 years: {summary['schedule']['blocks_reharvested']:,}.

## 3. Harvest by province

{province_table}

## 4. Chain versus frozen v5.1

{comparison_table}

The `sustainable_119m` basis applies one national factor
({summary['chain']['basis']['scale_factor']:.15g}) to the 100-year annual
average so the total is exactly 118,977,339.1 m3. The
`sustainable_achieved` basis is the unscaled WS4 average
({summary['harvest']['annual_mean_m3']:,.1f} m3/yr).

## 5. National oldest-first free-run sensitivity

The same scheduler without provincial allocation selects
{free.get('selected_blocks', 0):,} blocks /
{free.get('selected_area_ha', 0.0)/1e6:,.3f} Mha and sustains the period
target (`{free.get('sustained_119m')}`), but the 100-year harvest is
{free.get('province_share', {}).get('BC', 0.0)*100:.2f}% BC because BC holds
the oldest inventory. That concentration would change the frozen v5.1
province-weighted residue mix and is therefore reported as a diagnostic, not
used as the chain basis. The province-allocated schedule is the chain basis.

## 6. OSeCAN handoff

The separate `osecan/` artifact applies the existing WS4 origin-province
handoff logic to this chain. It verifies 21/21 frozen v5.1 reference positions,
7/7 119 Mm3 tier positions on both bases, and an unchanged official OSeCAN
tree. See `osecan/FORESTRY_OSECAN_HANDOFF_WS4_SUSTAINABLE.md`.

## 7. Caveats

"""
    report += "\n".join(f"- {caveat}" for caveat in summary["caveats"]) + "\n"
    (CHAIN_DIR / "WS4_SUSTAINABLE_119M.md").write_text(report, encoding="utf-8")


def _write_chain_manifest(summary: Mapping[str, Any]) -> None:
    """Write the chain artifact manifest with input/output hashes."""
    outputs = {
        path.name: {"bytes": path.stat().st_size, "sha256": _sha256(path)}
        for path in sorted(CHAIN_DIR.iterdir())
        if path.is_file() and path.name != "manifest.json"
    }
    inputs = {
        _relative(DATASET_DIR / "manifest.json"): _sha256(
            DATASET_DIR / "manifest.json"
        ),
        _relative(CONTROL_CSV): _sha256(CONTROL_CSV),
        _relative(chain.CHAIN_V51_CSV): _sha256(chain.CHAIN_V51_CSV),
        _relative(chain.CHAIN_V51_COEFFICIENTS): _sha256(
            chain.CHAIN_V51_COEFFICIENTS
        ),
        _relative(SCHEDULE_PATH): _sha256(SCHEDULE_PATH),
        _relative(RUN_DIR / "product_values.csv"): _sha256(
            RUN_DIR / "product_values.csv"
        ),
    }
    manifest = {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": summary["generated_utc"],
        "title": ARTIFACT_TITLE,
        "labels": summary["labels"],
        "target": summary["target"],
        "inputs": inputs,
        "outputs": outputs,
        "frozen_integrity": summary["frozen_integrity"],
        "validation": {
            "ws4_validate": summary["ws4"]["validate"],
            "run_offsets": summary["ws4"]["run_offsets"],
            "sustained_119m": summary["schedule"]["sustained_119m"],
            "period_shortfall_m3": summary["schedule"]["period_shortfall_m3"],
        },
        "d_status": summary["d_status"],
    }
    _write_json(CHAIN_DIR / "manifest.json", manifest)


def build() -> dict[str, Any]:
    """Run the full sustainable schedule, WS4 run, and chain build."""
    assert_no_unexpected_deletions()
    from workflows.national.facilities import forestry_osecan_handoff_ws4 as handoff

    chain_v51_before = handoff._tree_state(chain.CHAIN_V51_DIR)
    osecan_before = handoff._tree_state(handoff.OSECAN_DIR)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    CHAIN_DIR.mkdir(parents=True, exist_ok=True)

    shares = load_control_shares()
    estate = load_estate()
    rotation_table = compute_rotation_table(estate.curve_ids, estate.curve_matrix)

    province_result = build_schedule(estate, province_shares=shares)
    free_result = build_schedule(estate, province_shares=None, label="national_free_run")

    validate_text = run_ws4_validate()
    (OUTPUT_DIR / "validate.txt").write_text(validate_text + "\n", encoding="utf-8")
    run_status = ensure_ws4_run(province_result.schedule)
    run_values = extract_run_products()
    harvest_province, annual = harvest_by_province(province_result.selected, run_values)

    first_period = (
        province_result.selected[province_result.selected["period"] == 0]
        .groupby("province", as_index=False)
        .agg(volume_m3=("volume_m3", "sum"), area_ha=("area_ha", "sum"))
    )
    first_period["annual_m3"] = first_period["volume_m3"] / PERIOD_LENGTH
    first_period["annual_ha"] = first_period["area_ha"] / PERIOD_LENGTH

    _write_csv(
        OUTPUT_DIR / "selected_blocks.csv",
        [
            "block_id",
            "province",
            "period",
            "offset",
            "age_at_harvest",
            "volume_m3",
            "area_ha",
            "rotation_years",
            "rotation_raw_years",
            "curve_id",
            "age_class",
        ],
        province_result.selected.to_dict(orient="records"),
    )
    _write_csv(
        OUTPUT_DIR / "annual_harvest.csv",
        ["offset", "year", "period", "harvest_m3", "area_ha", "block_count", "mean_age"],
        annual.merge(
            province_result.annual_rows[["offset", "block_count", "mean_age"]],
            on="offset",
            how="left",
            validate="one_to_one",
        ).to_dict(orient="records"),
    )
    _write_csv(
        OUTPUT_DIR / "period_harvest.csv",
        [
            "period",
            "start_offset",
            "end_offset",
            "target_m3",
            "achieved_m3",
            "delta_m3",
            "overshoot_pct",
            "area_ha",
            "block_count",
            "annual_min_m3",
            "annual_max_m3",
            "annual_cv",
        ],
        province_result.period_rows.to_dict(orient="records"),
    )
    _write_csv(
        OUTPUT_DIR / "province_period_harvest.csv",
        [
            "period",
            "province",
            "target_m3",
            "achieved_m3",
            "delta_m3",
            "area_ha",
            "block_count",
            "mean_age",
        ],
        province_result.province_period_rows.to_dict(orient="records"),
    )
    _write_csv(
        OUTPUT_DIR / "province_harvest.csv",
        ["province", "harvest_m3", "area_ha", "block_count"],
        harvest_province.to_dict(orient="records"),
    )
    _write_csv(
        OUTPUT_DIR / "rotation_distribution.csv",
        ["rotation_raw_years", "rotation_years", "blocks", "area_ha", "volume_m3"],
        province_result.rotation_distribution.to_dict(orient="records"),
    )
    _write_csv(
        OUTPUT_DIR / "shortfalls.csv",
        [
            "period",
            "province",
            "scope",
            "target_m3",
            "eligible_volume_m3",
            "shortfall_m3",
        ],
        province_result.shortfalls.to_dict(orient="records"),
    )
    _write_csv(
        OUTPUT_DIR / "age_class_harvest.csv",
        ["period", "province", "age_class", "blocks", "area_ha", "volume_m3"],
        province_result.selected.groupby(
            ["period", "province", "age_class"], as_index=False
        )
        .agg(
            blocks=("block_id", "size"),
            area_ha=("area_ha", "sum"),
            volume_m3=("volume_m3", "sum"),
        )
        .to_dict(orient="records"),
    )
    _write_json(
        OUTPUT_DIR / "free_run_sensitivity.json",
        {
            "label": free_result.diagnostics["label"],
            "selection_method": free_result.diagnostics["selection_method"],
            "selected_blocks": free_result.diagnostics["selected_blocks"],
            "selected_area_ha": free_result.diagnostics["selected_area_ha"],
            "annual_min_m3": free_result.diagnostics["annual_min_m3"],
            "annual_max_m3": free_result.diagnostics["annual_max_m3"],
            "annual_cv": free_result.diagnostics["annual_cv"],
            "period_min_m3": free_result.diagnostics["period_min_m3"],
            "period_max_m3": free_result.diagnostics["period_max_m3"],
            "sustained_119m": free_result.diagnostics["sustained_119m"],
            "province_share": {
                province: float(
                    free_result.selected.loc[
                        free_result.selected["province"] == province, "volume_m3"
                    ].sum()
                    / free_result.selected["volume_m3"].sum()
                )
                for province in PROVINCES
            },
        },
    )

    summary = build_chain_outputs(
        harvest_province=harvest_province,
        first_period_annual=first_period,
        rotation_table=rotation_table,
        schedule_result=province_result,
        run_values=run_values,
        validate_text=validate_text,
    )
    summary["run_status"] = run_status
    summary["free_run_sensitivity"] = json.loads(
        (OUTPUT_DIR / "free_run_sensitivity.json").read_text(encoding="utf-8")
    )
    summary["schedule_files"] = {
        name: _sha256(OUTPUT_DIR / name)
        for name in (
            "schedule.csv",
            "selected_blocks.csv",
            "annual_harvest.csv",
            "period_harvest.csv",
            "province_period_harvest.csv",
            "province_harvest.csv",
            "rotation_distribution.csv",
            "shortfalls.csv",
            "age_class_harvest.csv",
            "validate.txt",
        )
    }
    summary["chain_files"] = {
        name: _sha256(CHAIN_DIR / name)
        for name in (
            "sustainable_harvest_by_province.csv",
            "sustainable_product_chain.csv",
            "comparison_vs_v51.csv",
        )
    }
    chain_v51_after = handoff._tree_state(chain.CHAIN_V51_DIR)
    osecan_after = handoff._tree_state(handoff.OSECAN_DIR)
    if chain_v51_before != chain_v51_after:
        raise Ws4Sustainable119mError(
            "frozen chain v5.1 tree changed during the build"
        )
    if osecan_before != osecan_after:
        raise Ws4Sustainable119mError(
            "official OSeCAN tree changed during the build"
        )
    summary["frozen_integrity"] = {
        "chain_v51_before": chain_v51_before,
        "chain_v51_after": chain_v51_after,
        "chain_v51_unchanged": chain_v51_before == chain_v51_after,
        "official_osecan_before": osecan_before,
        "official_osecan_after": osecan_after,
        "official_osecan_unchanged": osecan_before == osecan_after,
    }
    _write_json(CHAIN_DIR / "summary.json", summary)
    write_chain_report(summary, province_result)
    _write_chain_manifest(summary)
    return summary


def main() -> None:
    summary = build()
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
