"""Build a WS4-driven 119 Mm3 harvest and compare it with chain v5.1.

The national WS4 estate at 990-m fragments is used as an independent
block-volume realisation of the 119 Mm3 provincial control.  Eligible blocks
must be on a natural/unmanaged track with a block age offset of at least 40.
Blocks are selected whole; their volumes are never scaled or fabricated.  A
one-year ``CC`` schedule is simulated at offset 0 and the harvested volume and
treated area are read back from the WS4 run.

The achieved provincial harvest is then passed through the frozen chain v5.1
per-province product/residue coefficients.  Two bases are written:

* ``ws4_achieved``: the realised WS4 harvest;
* ``scaled_to_119m_control``: the WS4 harvest scaled by one national factor so
  that the provincial shares remain the WS4 shares and the total equals the
  118.9773391 Mm3 provincial-control total (the nominal 119 Mm3 case).

The frozen chain v5.1, the national WS4 dataset tables, and official OSeCAN
are read-only.  All values are model estimates; this is not a sustainable
harvest, policy, AAC, or national supply claim.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_ROOT = REPO_ROOT / "data"
WS4_ROOT = DATA_ROOT / "derived/ws4/scanfi-national-990m"
DATASET_DIR = WS4_ROOT / "dataset"
SCHEDULE_PATH = WS4_ROOT / "schedules/ws4_119m_cc.csv"
RUN_DIR = WS4_ROOT / "runs/ws4_119m_cc_1y"
CONTROL_CSV = (
    DATA_ROOT / "derived/national/harvest_basis/provincial_allocation/119m.csv"
)
CHAIN_V51_DIR = DATA_ROOT / "derived/national/facilities/national_product_chain_v5"
CHAIN_V51_CSV = CHAIN_V51_DIR / "product_chain_by_province.csv"
CHAIN_V51_COEFFICIENTS = CHAIN_V51_DIR / "coefficients.csv"
MODEL_CARD = REPO_ROOT / "docs/model-card-national-facility-chain.md"
OUTPUT_DIR = DATA_ROOT / "derived/national/facilities/ws4_chain_119m"
WS4_BIN = REPO_ROOT / ".venv-ws4/bin/ws4"

ARTIFACT = "ws4_chain_119m"
ARTIFACT_VERSION = "1.0.0"
ARTIFACT_TITLE = "WS4-driven 119 Mm3 harvest and chain v5.1 comparison"
REPORT_NAME = "WS4_CHAIN_119M.md"
MODULE_PATH = "workflows.national.facilities.ws4_chain_119m"
PROVINCES: tuple[str, ...] = (
    "BC",
    "AB",
    "SK",
    "MB",
    "ON",
    "QC",
    "NB",
    "NS",
    "PE",
    "NL",
    "YT",
    "NT",
    "NU",
)
BASES: tuple[str, ...] = ("ws4_achieved", "scaled_to_119m_control")
LHV_GJ_PER_ODT = 18.5
PJ_PER_GJ = 1_000_000.0
AGE_OFFSET_MIN = 40
CURVE_AGE_MAX = 600
CONTROL_TOTAL_M3 = 118_977_339.1
FROZEN_V51_TARGETS: tuple[tuple[str, float, float], ...] = (
    ("harvest_m3", 118_977_339.1, 0.1),
    ("solid_product_m3", 37_674_000.0, 1_000.0),
    ("pulp_product_odt", 12_066_000.0, 1_000.0),
    ("process_residue_pj", 498.751, 0.001),
    ("harvest_residue_gross_pj", 444.411, 0.001),
    ("harvest_residue_recoverable_r060_pj", 266.646, 0.001),
    ("total_residue_gross_pj", 943.162, 0.001),
)
METRICS: tuple[tuple[str, str], ...] = (
    ("harvest_m3", "m3"),
    ("solid_product_m3", "m3"),
    ("sawlog_product_m3", "m3"),
    ("panel_product_m3", "m3"),
    ("pulp_product_odt", "odt"),
    ("pellet_product_odt", "odt"),
    ("bio_product_mwh", "MWh"),
    ("process_residue_m3", "m3"),
    ("process_residue_odt", "odt"),
    ("process_residue_pj", "PJ"),
    ("harvest_residue_gross_odt", "odt"),
    ("harvest_residue_gross_pj", "PJ"),
    ("harvest_residue_recoverable_r035_odt", "odt"),
    ("harvest_residue_recoverable_r035_pj", "PJ"),
    ("harvest_residue_recoverable_r060_odt", "odt"),
    ("harvest_residue_recoverable_r060_pj", "PJ"),
    ("harvest_residue_recoverable_r075_odt", "odt"),
    ("harvest_residue_recoverable_r075_pj", "PJ"),
    ("total_residue_gross_pj", "PJ"),
    ("total_residue_recoverable_r035_pj", "PJ"),
    ("total_residue_recoverable_r060_pj", "PJ"),
    ("total_residue_recoverable_r075_pj", "PJ"),
)
COEFFICIENT_METRICS: tuple[str, ...] = (
    "sawlog_product_m3",
    "panel_product_m3",
    "pulp_product_odt",
    "pellet_product_odt",
    "bio_product_mwh",
    "process_residue_m3",
    "process_residue_odt",
    "harvest_residue_gross_odt",
    "harvest_residue_recoverable_r035_odt",
    "harvest_residue_recoverable_r060_odt",
    "harvest_residue_recoverable_r075_odt",
)
LABELS: dict[str, bool] = {
    "model_estimate": True,
    "observed": False,
    "policy_grade": False,
    "national_claim": False,
    "ws4_derived": True,
    "official_osecan_changed": False,
    "chain_v51_unchanged": True,
}
CAVEATS: tuple[str, ...] = (
    (
        "The 119 Mm3 schedule is a whole-block arithmetic verification, not a "
        "sustainable harvest, AAC, policy result, or investment case."
    ),
    (
        "Blocks are selected whole. Provincial achieved volume can overshoot "
        "the control target by the volume of the final block because no block "
        "is split, scaled, or fabricated."
    ),
    (
        "Fragment volumes are 990-m forest-area fragments multiplied by their "
        "RSYC group curve at the exact integer fragment age. They are not "
        "operable, tenure, or economically feasible volumes."
    ),
    (
        "The managed track shares the natural RSYC curve; the simulation "
        "applies one CC treatment at offset 0 and does not model planting "
        "response, silviculture, fertilization, or genetic gain."
    ),
    (
        "Product and residue values apply frozen chain v5.1 per-province "
        "coefficients to WS4 harvest by origin province. Facility assignment, "
        "mill capacity, transport, and interprovincial trade are not re-run."
    ),
    (
        "The scaled basis uses one national scale factor to preserve WS4 "
        "provincial shares and match the 118.9773391 Mm3 control total; it is "
        "not a per-province target-scaling exercise."
    ),
    (
        "Process residue and harvest residue are separate streams. PJ values "
        "use the frozen 18.5 GJ/ODT lower heating value; 0.60 harvest recovery "
        "is a reference case, not a calibrated recovery factor."
    ),
    "No official OSeCAN file or public OSeCAN surface is changed.",
)
_ACCOUNT_RE = re.compile(
    r"^product\.(HarvestedVolume|Treated)\.([A-Z]{2})\.(unmanaged|managed)\.CC$"
)
_TRACK_RE = re.compile(
    r"AU\(([A-Z]{2})\)\+IFM\(([^)]+)\)\+ORIGIN\(([^)]+)\)"
)
_FEATURE_RE = re.compile(r"^feature\.Yield\.([A-Z]{2})\.unmanaged\.Total$")


class Ws4Chain119mError(RuntimeError):
    """Raised when the WS4-driven chain build fails a guardrail."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise Ws4Chain119mError(f"missing input: {path}")
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(
    path: Path,
    fields: tuple[str, ...] | list[str],
    rows: list[dict[str, Any]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=list(fields), extrasaction="ignore",
            lineterminator="\n",
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


def _finite(
    value: Any, label: str, *, minimum: float | None = 0.0
) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise Ws4Chain119mError(f"{label} is not finite: {value!r}")
    if minimum is not None and number < minimum - 1e-12:
        raise Ws4Chain119mError(f"{label} is below {minimum}: {number!r}")
    return number


def _relative(path: Path) -> str:
    return str(path.relative_to(REPO_ROOT))


def _git_deletions(repository: Path) -> list[str]:
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repository,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise Ws4Chain119mError(
            f"cannot inspect git status in {repository}: "
            f"{result.stderr.strip()}"
        )
    return [
        line
        for line in result.stdout.splitlines()
        if len(line) >= 2 and (line[0] == "D" or line[1] == "D")
    ]


def assert_no_unexpected_deletions() -> None:
    """Fail closed on any ``D`` in the parent or data repository status."""
    incidents: list[str] = []
    for repository in (REPO_ROOT, DATA_ROOT):
        for line in _git_deletions(repository):
            incidents.append(f"{repository.name or repository}: {line}")
    if incidents:
        raise Ws4Chain119mError(
            "unexpected deletion status detected; halting: "
            + " | ".join(incidents)
        )


def _parse_tracks(dataset_dir: Path) -> pd.DataFrame:
    tracks = pd.read_parquet(
        dataset_dir / "tracks.parquet", columns=["track_id", "name"]
    )
    extracted = tracks["name"].str.extract(_TRACK_RE)
    if extracted.isna().any(axis=None):
        raise Ws4Chain119mError("unparseable WS4 track name")
    tracks["province"] = extracted[0]
    tracks["ifm"] = extracted[1]
    tracks["origin"] = extracted[2]
    return tracks


def _natural_tracks(dataset_dir: Path) -> pd.DataFrame:
    tracks = _parse_tracks(dataset_dir)
    natural = tracks[
        (tracks["ifm"] == "unmanaged") & (tracks["origin"] == "natural")
    ].copy()
    if natural.empty:
        raise Ws4Chain119mError("no natural/unmanaged WS4 tracks")
    if natural["track_id"].duplicated().any():
        raise Ws4Chain119mError("duplicate natural track id")

    features = pd.read_parquet(
        dataset_dir / "features.parquet",
        columns=["track_id", "label", "curve_id"],
    )
    mask = features["label"].str.fullmatch(
        r"feature\.Yield\.[A-Z]{2}\.unmanaged\.Total"
    )
    natural_features = features[mask].copy()
    labels = natural_features["label"].str.extract(_FEATURE_RE)
    if labels.isna().any(axis=None):
        raise Ws4Chain119mError("unparseable natural yield feature label")
    natural_features["feature_province"] = labels[0]
    natural_features = natural_features.merge(
        natural[["track_id", "province"]],
        on="track_id",
        how="inner",
        validate="one_to_one",
    )
    if natural_features["feature_province"].ne(natural_features["province"]).any():
        raise Ws4Chain119mError("feature province does not match track province")
    if len(natural_features) != len(natural):
        raise Ws4Chain119mError(
            "not every natural track has one unmanaged Total feature"
        )
    return natural_features[
        ["track_id", "province", "curve_id", "label"]
    ].rename(columns={"curve_id": "natural_curve_id"})


def build_block_volumes(dataset_dir: Path = DATASET_DIR) -> pd.DataFrame:
    """Return one row per block with its exact natural-track volume."""
    natural = _natural_tracks(dataset_dir)
    blocks = pd.read_parquet(
        dataset_dir / "blocks.parquet",
        columns=["block_id", "track_id", "offset", "area_ha"],
    )
    blocks = blocks.merge(
        natural,
        on="track_id",
        how="left",
        validate="many_to_one",
    )
    if blocks["province"].isna().any():
        raise Ws4Chain119mError("block track is not a natural/unmanaged track")
    if blocks["block_id"].duplicated().any():
        raise Ws4Chain119mError("duplicate WS4 block id")

    fragments = pd.read_parquet(
        dataset_dir / "fragments.parquet",
        columns=["fragment_id", "block_id", "area_ha", "age", "track_id"],
    )
    fragments = fragments.merge(
        blocks[
            ["block_id", "track_id", "province", "natural_curve_id"]
        ].rename(columns={"track_id": "block_track_id"}),
        on="block_id",
        how="left",
        validate="many_to_one",
    )
    if fragments["province"].isna().any():
        raise Ws4Chain119mError("fragment references an unmapped block")
    if fragments["track_id"].ne(fragments["block_track_id"]).any():
        raise Ws4Chain119mError("fragment track does not match block track")

    curves = pd.read_parquet(
        dataset_dir / "curves.parquet", columns=["curve_id", "x", "y"]
    )
    curves["age"] = curves["x"].round().astype("int64")
    is_exact_integer = np.isclose(
        curves["x"].to_numpy(dtype=float),
        curves["age"].to_numpy(dtype=float),
        rtol=0.0,
        atol=1e-9,
    )
    curves = curves[
        is_exact_integer & curves["age"].between(0, CURVE_AGE_MAX)
    ].copy()
    if curves.duplicated(["curve_id", "age"]).any():
        raise Ws4Chain119mError("duplicate curve age row")
    if not np.isfinite(curves["y"].to_numpy(dtype=float)).all():
        raise Ws4Chain119mError("non-finite yield curve value")
    if (curves["y"] < -1e-12).any():
        raise Ws4Chain119mError("negative yield curve value")

    fragments = fragments.merge(
        curves[["curve_id", "age", "y"]].rename(
            columns={"curve_id": "natural_curve_id"}
        ),
        on=["natural_curve_id", "age"],
        how="left",
        validate="many_to_one",
    )
    if fragments["y"].isna().any():
        raise Ws4Chain119mError("fragment age is missing from its yield curve")
    fragments["volume_m3"] = (
        fragments["area_ha"].to_numpy(dtype=float)
        * fragments["y"].to_numpy(dtype=float)
    )
    fragments["age_area"] = (
        fragments["age"].to_numpy(dtype=float)
        * fragments["area_ha"].to_numpy(dtype=float)
    )

    grouped = fragments.groupby("block_id", as_index=False).agg(
        volume_m3=("volume_m3", "sum"),
        fragment_area_ha=("area_ha", "sum"),
        fragment_count=("fragment_id", "size"),
        age_area=("age_area", "sum"),
    )
    grouped["mean_age"] = (
        grouped["age_area"] / grouped["fragment_area_ha"]
    )
    result = blocks.merge(
        grouped,
        on="block_id",
        how="left",
        validate="one_to_one",
    )
    if result["volume_m3"].isna().any():
        raise Ws4Chain119mError("block has no fragment volume")
    area_gap = np.abs(
        result["area_ha"].to_numpy(dtype=float)
        - result["fragment_area_ha"].to_numpy(dtype=float)
    )
    if float(area_gap.max()) > 1e-6:
        raise Ws4Chain119mError("block area does not equal fragment area sum")
    if (result["volume_m3"] < -1e-9).any():
        raise Ws4Chain119mError("negative block volume")
    return result.rename(
        columns={"natural_curve_id": "curve_id"}
    )


def read_targets(path: Path = CONTROL_CSV) -> dict[str, float]:
    rows = _read_rows(path)
    targets: dict[str, float] = {}
    for row in rows:
        province = row["province"].strip().upper()
        if province not in PROVINCES:
            raise Ws4Chain119mError(f"unexpected control province {province!r}")
        if province in targets:
            raise Ws4Chain119mError(f"duplicate control province {province!r}")
        targets[province] = _finite(
            row["harvest_m3"], f"{province} control harvest"
        )
    if set(targets) != set(PROVINCES):
        raise Ws4Chain119mError("control CSV does not cover all 13 jurisdictions")
    total = sum(targets.values())
    if not math.isclose(total, CONTROL_TOTAL_M3, rel_tol=1e-12):
        raise Ws4Chain119mError(
            f"control total {total!r} does not equal {CONTROL_TOTAL_M3!r}"
        )
    return targets


def select_blocks(
    block_volumes: pd.DataFrame,
    targets: dict[str, float],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Select whole eligible blocks and return schedule rows plus diagnostics."""
    eligible = block_volumes[
        (block_volumes["offset"] >= AGE_OFFSET_MIN)
        & (block_volumes["volume_m3"] > 0.0)
    ].copy()
    schedule_rows: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    tolerance_m3 = 1e-6

    for province in PROVINCES:
        target = _finite(targets[province], f"{province} target")
        province_blocks = eligible[eligible["province"] == province]
        available_m3 = float(province_blocks["volume_m3"].sum())
        if target > 0.0 and available_m3 + tolerance_m3 < target:
            raise Ws4Chain119mError(
                f"{province}: eligible volume {available_m3!r} m3 is below "
                f"target {target!r} m3; shortfall "
                f"{target - available_m3!r} m3"
            )
        if target <= 0.0:
            diagnostics.append(
                {
                    "province": province,
                    "target_m3": target,
                    "achieved_m3": 0.0,
                    "delta": 0.0,
                    "overshoot_pct": 0.0,
                    "area_ha": 0.0,
                    "block_count": 0,
                    "mean_age": 0.0,
                    "curve_count": 0,
                    "eligible_block_count": len(province_blocks),
                    "eligible_volume_m3": available_m3,
                    "selected_volume_m3": 0.0,
                }
            )
            continue

        ordered = province_blocks.sort_values(
            ["volume_m3", "block_id"],
            ascending=[False, True],
            kind="mergesort",
        )
        volumes = ordered["volume_m3"].to_numpy(dtype=float)
        block_ids = ordered["block_id"].to_numpy(dtype=np.int64)
        areas = ordered["area_ha"].to_numpy(dtype=float)
        mean_ages = ordered["mean_age"].to_numpy(dtype=float)

        remaining = target
        chosen: list[int] = []
        for position, volume in enumerate(volumes):
            if volume <= remaining + tolerance_m3:
                chosen.append(position)
                remaining -= float(volume)
                if remaining <= tolerance_m3:
                    break

        if remaining > tolerance_m3:
            used = np.zeros(len(volumes), dtype=bool)
            used[np.asarray(chosen, dtype=np.int64)] = True
            candidates = np.flatnonzero(~used)
            overshoot = volumes[candidates] - remaining
            positive = overshoot >= -tolerance_m3
            if not positive.any():
                raise Ws4Chain119mError(
                    f"{province}: no block can close remaining "
                    f"{remaining!r} m3"
                )
            positive_candidates = candidates[positive]
            positive_overshoot = overshoot[positive]
            order = np.lexsort(
                (
                    block_ids[positive_candidates],
                    volumes[positive_candidates],
                    positive_overshoot,
                )
            )
            chosen.append(int(positive_candidates[order[0]]))
            remaining -= float(volumes[chosen[-1]])

        chosen_positions = np.asarray(chosen, dtype=np.int64)
        achieved = float(volumes[chosen_positions].sum())
        if achieved + tolerance_m3 < target:
            raise Ws4Chain119mError(
                f"{province}: selected volume {achieved!r} is below target "
                f"{target!r}"
            )
        if len(set(chosen_positions.tolist())) != len(chosen_positions):
            raise Ws4Chain119mError(f"{province}: block selected twice")
        chosen_ids = block_ids[chosen_positions]
        selected_area = float(areas[chosen_positions].sum())
        selected_mean_age = float(
            (areas[chosen_positions] * mean_ages[chosen_positions]).sum()
            / selected_area
        )
        curve_count = int(
            ordered.iloc[chosen_positions]["curve_id"].nunique()
        )
        for block_id in sorted(int(value) for value in chosen_ids):
            schedule_rows.append(
                {"block_id": block_id, "treatment": "CC", "offset": 0}
            )
        diagnostics.append(
            {
                "province": province,
                "target_m3": target,
                "achieved_m3": achieved,
                "delta": achieved - target,
                "overshoot_pct": (achieved - target) / target * 100.0,
                "area_ha": selected_area,
                "block_count": len(chosen_positions),
                "mean_age": selected_mean_age,
                "curve_count": curve_count,
                "eligible_block_count": len(province_blocks),
                "eligible_volume_m3": available_m3,
                "selected_volume_m3": achieved,
            }
        )

    schedule = pd.DataFrame(
        schedule_rows, columns=["block_id", "treatment", "offset"]
    )
    if schedule["block_id"].duplicated().any():
        raise Ws4Chain119mError("schedule selects a block twice")
    if schedule.empty:
        raise Ws4Chain119mError("schedule is empty")
    return schedule, pd.DataFrame(diagnostics)


def _schedule_matches(existing: pd.DataFrame, schedule: pd.DataFrame) -> bool:
    columns = ["block_id", "treatment", "offset"]
    if list(existing.columns) != columns or len(existing) != len(schedule):
        return False
    left = existing.sort_values("block_id").reset_index(drop=True)
    right = schedule.sort_values("block_id").reset_index(drop=True)
    return bool(
        left["block_id"].astype("int64").equals(
            right["block_id"].astype("int64")
        )
        and left["treatment"].astype(str).equals(
            right["treatment"].astype(str)
        )
        and left["offset"].astype("int64").equals(
            right["offset"].astype("int64")
        )
    )


def run_ws4_validate(dataset_dir: Path = DATASET_DIR) -> str:
    if not WS4_BIN.is_file():
        raise Ws4Chain119mError(f"WS4 executable not found: {WS4_BIN}")
    result = subprocess.run(
        [str(WS4_BIN), "validate", "--dataset", str(dataset_dir)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise Ws4Chain119mError(
            "ws4 validate failed: "
            f"{result.stdout.strip()} {result.stderr.strip()}"
        )
    return result.stdout.strip()


def ensure_ws4_run(
    schedule: pd.DataFrame,
    schedule_path: Path = SCHEDULE_PATH,
    run_dir: Path = RUN_DIR,
) -> str:
    """Write the schedule and create the run, or reuse a matching run."""
    _write_csv(
        schedule_path,
        ["block_id", "treatment", "offset"],
        schedule.to_dict(orient="records"),
    )
    if run_dir.exists():
        existing_schedule = run_dir / "schedule.csv"
        product_values = run_dir / "product_values.csv"
        if not existing_schedule.is_file() or not product_values.is_file():
            raise Ws4Chain119mError(
                f"existing run directory is incomplete: {run_dir}"
            )
        existing = pd.read_csv(existing_schedule)
        if not _schedule_matches(existing, schedule):
            raise Ws4Chain119mError(
                f"existing run schedule does not match {schedule_path}; "
                "refusing to overwrite the run"
            )
        return "reused"

    if not WS4_BIN.is_file():
        raise Ws4Chain119mError(f"WS4 executable not found: {WS4_BIN}")
    result = subprocess.run(
        [
            str(WS4_BIN),
            "simulate",
            "--dataset",
            str(DATASET_DIR),
            "--schedule",
            str(schedule_path),
            "--years",
            "1",
            "--out",
            str(run_dir),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise Ws4Chain119mError(
            "ws4 simulate failed: "
            f"{result.stdout.strip()} {result.stderr.strip()}"
        )
    if not (run_dir / "product_values.csv").is_file():
        raise Ws4Chain119mError("ws4 simulate did not write product_values.csv")
    return "created"


def extract_run_products(
    run_dir: Path = RUN_DIR,
) -> pd.DataFrame:
    path = run_dir / "product_values.csv"
    if not path.is_file():
        raise Ws4Chain119mError(f"missing WS4 product values: {path}")
    values = pd.read_csv(path)
    if list(values.columns) != ["offset", "account", "value"]:
        raise Ws4Chain119mError(
            f"unexpected product_values columns: {values.columns.tolist()}"
        )
    offsets = sorted(values["offset"].dropna().unique().tolist())
    if offsets != [0]:
        raise Ws4Chain119mError(
            f"one-year run wrote unexpected offsets: {offsets!r}"
        )
    parsed = values["account"].str.extract(_ACCOUNT_RE)
    if parsed.isna().any(axis=None):
        unknown = values.loc[
            parsed.isna().any(axis=1), "account"
        ].drop_duplicates()
        raise Ws4Chain119mError(
            "unexpected product account labels: "
            + ", ".join(str(value) for value in unknown.head(5))
        )
    values["product"] = parsed[0]
    values["province"] = parsed[1]
    values["origin"] = parsed[2]
    values["value"] = pd.to_numeric(values["value"], errors="raise")
    if not np.isfinite(values["value"].to_numpy(dtype=float)).all():
        raise Ws4Chain119mError("non-finite WS4 product value")
    if (values["value"] < -1e-9).any():
        raise Ws4Chain119mError("negative WS4 product value")
    unmanaged = values[values["origin"] == "unmanaged"]
    if unmanaged.empty:
        raise Ws4Chain119mError("run has no unmanaged product rows")
    pivoted = (
        unmanaged.groupby(["province", "product"], as_index=False)["value"]
        .sum()
        .pivot(index="province", columns="product", values="value")
        .fillna(0.0)
        .rename(
            columns={
                "HarvestedVolume": "harvested_m3",
                "Treated": "treated_ha",
            }
        )
        .reset_index()
    )
    for column in ("harvested_m3", "treated_ha"):
        if column not in pivoted:
            raise Ws4Chain119mError(f"run is missing {column}")
        if (pivoted[column] < -1e-9).any():
            raise Ws4Chain119mError(f"run has negative {column}")
    if set(pivoted["province"]) - set(PROVINCES):
        raise Ws4Chain119mError("run has an unknown province label")
    return pivoted


def _frozen_province_rows(
    path: Path = CHAIN_V51_CSV,
) -> dict[str, dict[str, float]]:
    rows = _read_rows(path)
    by_province: dict[str, dict[str, float]] = {}
    for row in rows:
        province = row["province"].strip().upper()
        if province in by_province:
            raise Ws4Chain119mError(f"duplicate chain v5.1 province {province}")
        by_province[province] = {
            "harvest_m3": _finite(row["harvest_m3"], "v5.1 harvest"),
            "sawlog_product_m3": _finite(
                row["sawlog_product_m3"], "v5.1 sawlog product"
            ),
            "panel_product_m3": _finite(
                row["panel_product_m3"], "v5.1 panel product"
            ),
            "pulp_product_odt": _finite(
                row["pulp_product_odt"], "v5.1 pulp product"
            ),
            "pellet_product_odt": _finite(
                row["pellet_product_odt"], "v5.1 pellet product"
            ),
            "bio_product_mwh": _finite(
                row["bio_product_mwh"], "v5.1 bio product"
            ),
            "process_residue_m3": _finite(
                row["process_residue_m3"], "v5.1 process residue m3"
            ),
            "process_residue_odt": _finite(
                row["process_residue_odt"], "v5.1 process residue odt"
            ),
            "harvest_residue_gross_odt": _finite(
                row["harvest_residue_gross_odt"],
                "v5.1 harvest residue gross",
            ),
            "harvest_residue_recoverable_r035_odt": _finite(
                row["harvest_residue_recoverable_total_odt_r035"],
                "v5.1 harvest residue r035",
            ),
            "harvest_residue_recoverable_r060_odt": _finite(
                row["harvest_residue_recoverable_total_odt_r060"],
                "v5.1 harvest residue r060",
            ),
            "harvest_residue_recoverable_r075_odt": _finite(
                row["harvest_residue_recoverable_total_odt_r075"],
                "v5.1 harvest residue r075",
            ),
            "coefficient_tier": row.get("coefficient_tier", ""),
            "coefficient_source": row.get("coefficient_source", ""),
        }
    if set(by_province) != set(PROVINCES):
        raise Ws4Chain119mError("chain v5.1 does not cover all 13 jurisdictions")
    return by_province


def _coefficient_rows(
    frozen: dict[str, dict[str, float]],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, float]]]:
    coefficient_rows: list[dict[str, Any]] = []
    coefficients: dict[str, dict[str, float]] = {}
    for province in PROVINCES:
        source = frozen[province]
        harvest = source["harvest_m3"]
        if harvest <= 0.0:
            coefficients[province] = {
                metric: 0.0 for metric in COEFFICIENT_METRICS
            }
            coefficient_rows.append(
                {
                    "province": province,
                    "reference_harvest_m3": harvest,
                    **{f"{metric}_per_m3": 0.0 for metric in COEFFICIENT_METRICS},
                    "coefficient_tier": source["coefficient_tier"],
                    "coefficient_source": source["coefficient_source"],
                }
            )
            continue
        coefficients[province] = {
            metric: source[metric] / harvest
            for metric in COEFFICIENT_METRICS
        }
        coefficient_rows.append(
            {
                "province": province,
                "reference_harvest_m3": harvest,
                **{
                    f"{metric}_per_m3": coefficients[province][metric]
                    for metric in COEFFICIENT_METRICS
                },
                "coefficient_tier": source["coefficient_tier"],
                "coefficient_source": source["coefficient_source"],
            }
        )
    return coefficient_rows, coefficients


def _national_row(
    basis: str,
    province_rows: list[dict[str, Any]],
    numeric_fields: tuple[str, ...],
) -> dict[str, Any]:
    total: dict[str, Any] = {
        "province": "CAN",
        "geography_level": "national",
        "basis": basis,
    }
    for field in numeric_fields:
        total[field] = sum(float(row[field]) for row in province_rows)
    return total


def build_chain_rows(
    harvest_by_province: dict[str, float],
    basis: str,
    frozen: dict[str, dict[str, float]],
    coefficients: dict[str, dict[str, float]],
    *,
    scale_factor: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for province in PROVINCES:
        harvest = _finite(
            harvest_by_province[province], f"{basis} {province} harvest"
        )
        coefficient = coefficients[province]
        row: dict[str, Any] = {
            "province": province,
            "geography_level": "province",
            "basis": basis,
            "harvest_m3": harvest,
            "scale_factor": scale_factor,
            "coefficient_tier": frozen[province]["coefficient_tier"],
            "coefficient_source": frozen[province]["coefficient_source"],
        }
        for metric in COEFFICIENT_METRICS:
            row[metric] = harvest * coefficient[metric]
        row["solid_product_m3"] = (
            row["sawlog_product_m3"] + row["panel_product_m3"]
        )
        row["process_residue_pj"] = (
            row["process_residue_odt"] * LHV_GJ_PER_ODT / PJ_PER_GJ
        )
        row["harvest_residue_gross_pj"] = (
            row["harvest_residue_gross_odt"] * LHV_GJ_PER_ODT / PJ_PER_GJ
        )
        for recovery in ("r035", "r060", "r075"):
            odt_field = f"harvest_residue_recoverable_{recovery}_odt"
            row[f"harvest_residue_recoverable_{recovery}_pj"] = (
                row[odt_field] * LHV_GJ_PER_ODT / PJ_PER_GJ
            )
        row["total_residue_gross_pj"] = (
            row["process_residue_pj"] + row["harvest_residue_gross_pj"]
        )
        for recovery in ("r035", "r060", "r075"):
            row[f"total_residue_recoverable_{recovery}_pj"] = (
                row["process_residue_pj"]
                + row[f"harvest_residue_recoverable_{recovery}_pj"]
            )
        rows.append(row)

    numeric_fields = tuple(metric for metric, _unit in METRICS)
    national = _national_row(basis, rows, numeric_fields)
    national["scale_factor"] = scale_factor
    national["coefficient_tier"] = "national_total"
    national["coefficient_source"] = "sum_of_province_rows"
    return rows, national


def _frozen_national_totals(
    frozen: dict[str, dict[str, float]],
) -> dict[str, float]:
    totals: dict[str, float] = {}
    harvest = sum(row["harvest_m3"] for row in frozen.values())
    totals["harvest_m3"] = harvest
    for metric in COEFFICIENT_METRICS:
        totals[metric] = sum(row[metric] for row in frozen.values())
    totals["solid_product_m3"] = (
        totals["sawlog_product_m3"] + totals["panel_product_m3"]
    )
    totals["process_residue_pj"] = (
        totals["process_residue_odt"] * LHV_GJ_PER_ODT / PJ_PER_GJ
    )
    totals["harvest_residue_gross_pj"] = (
        totals["harvest_residue_gross_odt"] * LHV_GJ_PER_ODT / PJ_PER_GJ
    )
    for recovery in ("r035", "r060", "r075"):
        odt_field = f"harvest_residue_recoverable_{recovery}_odt"
        totals[f"harvest_residue_recoverable_{recovery}_pj"] = (
            totals[odt_field] * LHV_GJ_PER_ODT / PJ_PER_GJ
        )
    totals["total_residue_gross_pj"] = (
        totals["process_residue_pj"] + totals["harvest_residue_gross_pj"]
    )
    for recovery in ("r035", "r060", "r075"):
        totals[f"total_residue_recoverable_{recovery}_pj"] = (
            totals["process_residue_pj"]
            + totals[f"harvest_residue_recoverable_{recovery}_pj"]
        )
    return totals


def _check_frozen_targets(totals: dict[str, float]) -> None:
    for metric, expected, tolerance in FROZEN_V51_TARGETS:
        observed = totals[metric]
        if abs(observed - expected) > tolerance:
            raise Ws4Chain119mError(
                f"frozen v5.1 {metric} is {observed!r}, expected "
                f"{expected!r} +/- {tolerance!r}"
            )


def _comparison_rows(
    national: dict[str, dict[str, Any]],
    frozen_totals: dict[str, float],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for metric, unit in METRICS:
        reference = _finite(
            frozen_totals[metric], f"v5.1 national {metric}",
            minimum=None,
        )
        achieved = _finite(
            national["ws4_achieved"][metric],
            f"ws4_achieved national {metric}",
            minimum=None,
        )
        scaled = _finite(
            national["scaled_to_119m_control"][metric],
            f"scaled national {metric}",
            minimum=None,
        )
        achieved_delta = achieved - reference
        scaled_delta = scaled - reference
        rows.append(
            {
                "metric": metric,
                "unit": unit,
                "chain_v51_value": reference,
                "ws4_achieved_value": achieved,
                "ws4_achieved_delta": achieved_delta,
                "ws4_achieved_delta_pct": (
                    achieved_delta / reference * 100.0 if reference else 0.0
                ),
                "scaled_to_119m_control_value": scaled,
                "scaled_to_119m_control_delta": scaled_delta,
                "scaled_to_119m_control_delta_pct": (
                    scaled_delta / reference * 100.0 if reference else 0.0
                ),
            }
        )
    return rows


def _validation_checks(
    schedule: pd.DataFrame,
    harvest_rows: list[dict[str, Any]],
    national: dict[str, dict[str, Any]],
) -> list[dict[str, str]]:
    checks = [
        {
            "check_id": "schedule_nonempty",
            "detail": f"{len(schedule)} unique CC rows at offset 0",
            "status": "PASS" if len(schedule) > 0 else "FAIL",
        },
        {
            "check_id": "schedule_block_uniqueness",
            "detail": "no block id occurs twice",
            "status": (
                "PASS" if not schedule["block_id"].duplicated().any()
                else "FAIL"
            ),
        },
        {
            "check_id": "harvest_nonnegative",
            "detail": "achieved volume and treated area are finite/nonnegative",
            "status": "PASS",
        },
        {
            "check_id": "national_harvest_conservation",
            "detail": (
                "sum of provincial achieved harvest equals WS4 run total"
            ),
            "status": "PASS",
        },
    ]
    target = sum(float(row["target_m3"]) for row in harvest_rows)
    achieved = sum(float(row["achieved_m3"]) for row in harvest_rows)
    checks.append(
        {
            "check_id": "province_target_overshoot",
            "detail": (
                f"national target {target:.3f} m3; achieved "
                f"{achieved:.3f} m3; overshoot {achieved - target:.3f} m3"
            ),
            "status": "PASS" if achieved + 1e-6 >= target else "FAIL",
        }
    )
    scaled = national["scaled_to_119m_control"]["harvest_m3"]
    checks.append(
        {
            "check_id": "scaled_basis_control_total",
            "detail": f"scaled harvest {scaled:.6f} m3",
            "status": (
                "PASS"
                if math.isclose(scaled, CONTROL_TOTAL_M3, rel_tol=1e-12)
                else "FAIL"
            ),
        }
    )
    return checks


def _build_harvest_rows(
    diagnostics: pd.DataFrame,
    run_products: pd.DataFrame,
) -> list[dict[str, Any]]:
    run = run_products.set_index("province")
    rows: list[dict[str, Any]] = []
    for _, diagnostic in diagnostics.iterrows():
        province = str(diagnostic["province"])
        achieved = (
            float(run.loc[province, "harvested_m3"])
            if province in run.index
            else 0.0
        )
        treated = (
            float(run.loc[province, "treated_ha"])
            if province in run.index
            else 0.0
        )
        selected_volume = float(diagnostic["selected_volume_m3"])
        if not math.isclose(
            achieved, selected_volume, rel_tol=1e-9, abs_tol=1e-6
        ):
            raise Ws4Chain119mError(
                f"{province}: WS4 harvested {achieved!r} m3 but selected "
                f"block volume is {selected_volume!r} m3"
            )
        selected_area = float(diagnostic["area_ha"])
        if not math.isclose(
            treated, selected_area, rel_tol=1e-9, abs_tol=1e-6
        ):
            raise Ws4Chain119mError(
                f"{province}: WS4 treated {treated!r} ha but selected block "
                f"area is {selected_area!r} ha"
            )
        target = float(diagnostic["target_m3"])
        rows.append(
            {
                "province": province,
                "target_m3": target,
                "achieved_m3": achieved,
                "delta": achieved - target,
                "overshoot_pct": (
                    (achieved - target) / target * 100.0 if target else 0.0
                ),
                "area_ha": treated,
                "block_count": int(diagnostic["block_count"]),
                "mean_age": float(diagnostic["mean_age"]),
                "curve_count": int(diagnostic["curve_count"]),
                "selected_volume_m3": selected_volume,
                "eligible_block_count": int(
                    diagnostic["eligible_block_count"]
                ),
                "eligible_volume_m3": float(
                    diagnostic["eligible_volume_m3"]
                ),
            }
        )
    return rows


def _md_table(header: list[str], rows: list[list[str]]) -> str:
    lines = [
        "| " + " | ".join(header) + " |",
        "|" + "|".join("---" for _ in header) + "|",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return "\n".join(lines)


def _write_report(
    path: Path,
    harvest_rows: list[dict[str, Any]],
    national: dict[str, dict[str, Any]],
    comparison: list[dict[str, Any]],
    schedule: pd.DataFrame,
    run_status: str,
    validate_output: str,
    osecan: dict[str, Any],
) -> None:
    harvest_table = _md_table(
        [
            "Province",
            "Target (Mm3)",
            "Achieved (Mm3)",
            "Delta (m3)",
            "Area (ha)",
            "Blocks",
            "Mean age",
            "Curves",
        ],
        [
            [
                row["province"],
                f"{row['target_m3'] / 1e6:.6f}",
                f"{row['achieved_m3'] / 1e6:.6f}",
                f"{row['delta']:.3f}",
                f"{row['area_ha']:.2f}",
                str(row["block_count"]),
                f"{row['mean_age']:.2f}",
                str(row["curve_count"]),
            ]
            for row in harvest_rows
        ],
    )
    comparison_table = _md_table(
        [
            "Metric",
            "Unit",
            "v5.1",
            "WS4 achieved",
            "WS4 delta",
            "WS4 delta %",
            "Scaled",
            "Scaled delta",
            "Scaled delta %",
        ],
        [
            [
                row["metric"],
                row["unit"],
                f"{row['chain_v51_value']:.6g}",
                f"{row['ws4_achieved_value']:.6g}",
                f"{row['ws4_achieved_delta']:.6g}",
                f"{row['ws4_achieved_delta_pct']:.4f}",
                f"{row['scaled_to_119m_control_value']:.6g}",
                f"{row['scaled_to_119m_control_delta']:.6g}",
                f"{row['scaled_to_119m_control_delta_pct']:.4f}",
            ]
            for row in comparison
        ],
    )
    text = f"""# WS4-driven 119 Mm3 harvest and chain comparison

**Artifact:** `{ARTIFACT}` **Version:** {ARTIFACT_VERSION}

**Status:** pass. New sidecar artifact. The national WS4 dataset tables, frozen
chain v5.1, and official OSeCAN are unchanged.

**Labels:** `model_estimate=true`, `observed=false`, `policy_grade=false`,
`national_claim=false`, `ws4_derived=true`,
`official_osecan_changed=false`, `chain_v51_unchanged=true`.

## 1. Method

The national WS4 estate at 990-m fragments was read from
`data/derived/ws4/scanfi-national-990m/dataset/`. Each fragment belongs to a
natural/unmanaged track whose name carries `AU(<province>)`; the track's
`feature.Yield.<province>.unmanaged.Total` feature supplies the RSYC yield
curve. Fragment volume is `area_ha * curve(age)` at the exact integer age.
Block volume is the sum of its fragment volumes. No block volume is scaled or
fabricated.

Eligible blocks are natural/unmanaged blocks with `offset >= 40`. A
volume-aware greedy selects whole blocks in descending volume, fills while the
next block fits, then chooses the unselected block that minimises the absolute
overshoot. Provincial achieved volume can exceed the control target by at most
the final block volume. The same block is never selected twice. NU has a zero
control target and no schedule row.

The schedule has **{len(schedule)} rows** (`block_id,treatment,offset`) at
offset 0. `ws4 validate` returned `{validate_output}`. The one-year `CC`
simulation status is `{run_status}`. Harvested volume and treated area are read
from `product.HarvestedVolume.<province>.unmanaged.CC` and
`product.Treated.<province>.unmanaged.CC`.

## 2. Achieved provincial harvest

{harvest_table}

National target is **{sum(row['target_m3'] for row in harvest_rows) / 1e6:.6f}
Mm3** (the nominal 119 Mm3 control, whose exact provincial sum is
{CONTROL_TOTAL_M3 / 1e6:.7f} Mm3). National achieved harvest is
**{national['ws4_achieved']['harvest_m3'] / 1e6:.6f} Mm3**; treated area is
**{sum(row['area_ha'] for row in harvest_rows):,.2f} ha**.

## 3. Chain v5.1 coefficients and comparison

The frozen v5.1 per-province product/residue coefficients are the frozen
`product_chain_by_province.csv` outputs divided by the frozen provincial
harvest. The `ws4_achieved` basis applies them to the realised WS4 harvest.
The `scaled_to_119m_control` basis applies one national factor
`{national['scaled_to_119m_control']['scale_factor']:.12f}` to every province,
preserving WS4 provincial shares and matching the control total.

{comparison_table}

`solid_product_m3` is sawlog product plus panel product. PJ values use
18.5 GJ/ODT. Process residue and harvest residue are never summed into a single
stream; `total_residue_gross_pj` is reported only as the arithmetic national
total of the two separate streams.

## 4. Caveats

- The schedule is a whole-block arithmetic verification, not a sustainable
  harvest, AAC, policy result, or investment case.
- Blocks are selected whole. Provincial overshoot is block-granularity
  overshoot, not a scaled target.
- 990-m forest-area fragments and group yield curves are inventory
  realisations, not operable or tenure-feasible areas.
- One `CC` treatment at offset 0 is simulated. The managed track shares the
  natural curve; planting response, silviculture, fertilization, and genetic
  gain are not modelled.
- Facility assignment, mill capacity, transport, and interprovincial trade are
  not re-run. Products and residue are coefficient applications by origin
  province.
- The scaled basis is a national factor that preserves WS4 provincial shares;
  it is not per-province target scaling.
- 0.60 harvest recovery is a reference case, not a calibrated recovery factor.
- No official OSeCAN file or public OSeCAN surface is changed.

## 5. OSeCAN handoff status

{osecan['status_text']}

## 6. Reproduce

```bash
.venv/bin/python -m {MODULE_PATH}
```
"""
    path.write_text(text, encoding="utf-8")


def build() -> dict[str, Any]:
    assert_no_unexpected_deletions()
    targets = read_targets()
    block_volumes = build_block_volumes()
    schedule, diagnostics = select_blocks(block_volumes, targets)
    validate_output = run_ws4_validate()
    run_status = ensure_ws4_run(schedule)
    run_products = extract_run_products()
    harvest_rows = _build_harvest_rows(diagnostics, run_products)

    frozen = _frozen_province_rows()
    coefficient_rows, coefficients = _coefficient_rows(frozen)
    frozen_totals = _frozen_national_totals(frozen)
    _check_frozen_targets(frozen_totals)

    achieved_by_province = {
        row["province"]: float(row["achieved_m3"]) for row in harvest_rows
    }
    achieved_total = sum(achieved_by_province.values())
    if achieved_total <= 0.0:
        raise Ws4Chain119mError("achieved harvest is not positive")
    scale_factor = CONTROL_TOTAL_M3 / achieved_total
    scaled_by_province = {
        province: achieved_by_province[province] * scale_factor
        for province in PROVINCES
    }

    achieved_rows, achieved_national = build_chain_rows(
        achieved_by_province,
        "ws4_achieved",
        frozen,
        coefficients,
        scale_factor=1.0,
    )
    scaled_rows, scaled_national = build_chain_rows(
        scaled_by_province,
        "scaled_to_119m_control",
        frozen,
        coefficients,
        scale_factor=scale_factor,
    )
    national = {
        "ws4_achieved": achieved_national,
        "scaled_to_119m_control": scaled_national,
    }
    comparison = _comparison_rows(national, frozen_totals)
    checks = _validation_checks(schedule, harvest_rows, national)
    if any(check["status"] != "PASS" for check in checks):
        raise Ws4Chain119mError("one or more validation checks failed")

    osecan = {
        "status": "not_rebuilt",
        "rebuilt": False,
        "reason": (
            "The existing residue-curve and OSeCAN handoff workflows are "
            "frozen to the v5.1 facility assignment and zone-weight inputs. "
            "A WS4-basis rerun would require a facility assignment and "
            "province-to-zone reallocation outside this WS4 schedule scope. "
            "Only the chain totals were rebuilt; no tier position is claimed."
        ),
        "status_text": (
            "Not rebuilt. The existing residue-curve and OSeCAN handoff "
            "workflows are frozen to the v5.1 facility assignment and "
            "zone-weight inputs; a WS4-basis rerun would require a facility "
            "assignment and province-to-zone reallocation outside this "
            "schedule scope. Only chain totals were rebuilt; no tier position "
            "is claimed."
        ),
    }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    harvest_fields = [
        "province",
        "target_m3",
        "achieved_m3",
        "delta",
        "overshoot_pct",
        "area_ha",
        "block_count",
        "mean_age",
        "curve_count",
        "selected_volume_m3",
        "eligible_block_count",
        "eligible_volume_m3",
        *LABELS.keys(),
    ]
    for row in harvest_rows:
        row.update(LABELS)
    _write_csv(
        OUTPUT_DIR / "ws4_119m_harvest.csv",
        harvest_fields,
        harvest_rows,
    )

    coefficient_fields = [
        "province",
        "reference_harvest_m3",
        *[f"{metric}_per_m3" for metric in COEFFICIENT_METRICS],
        "coefficient_tier",
        "coefficient_source",
        *LABELS.keys(),
    ]
    for row in coefficient_rows:
        row.update(LABELS)
    _write_csv(
        OUTPUT_DIR / "ws4_119m_coefficients.csv",
        coefficient_fields,
        coefficient_rows,
    )

    chain_fields = [
        "province",
        "geography_level",
        "basis",
        "harvest_m3",
        "scale_factor",
        "coefficient_tier",
        "coefficient_source",
        "solid_product_m3",
        "sawlog_product_m3",
        "panel_product_m3",
        "pulp_product_odt",
        "pellet_product_odt",
        "bio_product_mwh",
        "process_residue_m3",
        "process_residue_odt",
        "process_residue_pj",
        "harvest_residue_gross_odt",
        "harvest_residue_gross_pj",
        "harvest_residue_recoverable_r035_odt",
        "harvest_residue_recoverable_r035_pj",
        "harvest_residue_recoverable_r060_odt",
        "harvest_residue_recoverable_r060_pj",
        "harvest_residue_recoverable_r075_odt",
        "harvest_residue_recoverable_r075_pj",
        "total_residue_gross_pj",
        "total_residue_recoverable_r035_pj",
        "total_residue_recoverable_r060_pj",
        "total_residue_recoverable_r075_pj",
        *LABELS.keys(),
    ]
    chain_rows = achieved_rows + scaled_rows + [achieved_national, scaled_national]
    for row in chain_rows:
        row.update(LABELS)
    _write_csv(
        OUTPUT_DIR / "ws4_119m_product_chain.csv",
        chain_fields,
        chain_rows,
    )

    comparison_fields = [
        "metric",
        "unit",
        "chain_v51_value",
        "ws4_achieved_value",
        "ws4_achieved_delta",
        "ws4_achieved_delta_pct",
        "scaled_to_119m_control_value",
        "scaled_to_119m_control_delta",
        "scaled_to_119m_control_delta_pct",
        *LABELS.keys(),
    ]
    for row in comparison:
        row.update(LABELS)
    _write_csv(
        OUTPUT_DIR / "ws4_vs_chain_v51_comparison.csv",
        comparison_fields,
        comparison,
    )

    generated = datetime.now(UTC).isoformat(timespec="seconds")
    inputs = {
        _relative(CONTROL_CSV): _sha256(CONTROL_CSV),
        _relative(CHAIN_V51_CSV): _sha256(CHAIN_V51_CSV),
        _relative(CHAIN_V51_COEFFICIENTS): _sha256(CHAIN_V51_COEFFICIENTS),
        _relative(MODEL_CARD): _sha256(MODEL_CARD),
        _relative(DATASET_DIR / "manifest.json"): _sha256(
            DATASET_DIR / "manifest.json"
        ),
        _relative(SCHEDULE_PATH): _sha256(SCHEDULE_PATH),
        _relative(RUN_DIR / "product_values.csv"): _sha256(
            RUN_DIR / "product_values.csv"
        ),
    }
    summary: dict[str, Any] = {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": generated,
        "status": "pass",
        "title": ARTIFACT_TITLE,
        "labels": LABELS,
        "inputs": inputs,
        "ws4": {
            "dataset": _relative(DATASET_DIR),
            "dataset_manifest_sha256": _sha256(
                DATASET_DIR / "manifest.json"
            ),
            "schedule": _relative(SCHEDULE_PATH),
            "schedule_rows": len(schedule),
            "run": _relative(RUN_DIR),
            "run_status": run_status,
            "years": 1,
            "offset": 0,
            "validate": {
                "exit_code": 0,
                "stdout": validate_output,
            },
            "simulation": {
                "harvested_m3": achieved_total,
                "treated_ha": sum(
                    float(row["area_ha"]) for row in harvest_rows
                ),
            },
            "selection_method": (
                "natural/unmanaged tracks; block offset >= 40; descending "
                "volume fill; final block minimises overshoot; whole blocks "
                "only"
            ),
        },
        "province_harvest": harvest_rows,
        "national_harvest": {
            "target_m3": sum(
                float(row["target_m3"]) for row in harvest_rows
            ),
            "achieved_m3": achieved_total,
            "delta_m3": achieved_total
            - sum(float(row["target_m3"]) for row in harvest_rows),
            "area_ha": sum(float(row["area_ha"]) for row in harvest_rows),
            "block_count": len(schedule),
        },
        "product_chain": {
            "lhv_gj_per_odt": LHV_GJ_PER_ODT,
            "bases": list(BASES),
            "coefficient_source": _relative(CHAIN_V51_CSV),
            "national_totals": {
                basis: {
                    metric: float(national[basis][metric])
                    for metric, _unit in METRICS
                }
                for basis in BASES
            },
            "scaled_harvest_shares": {
                province: (
                    scaled_by_province[province] / CONTROL_TOTAL_M3
                )
                for province in PROVINCES
            },
        },
        "comparison": {
            "reference": "national_product_chain_v5 (frozen v5.1)",
            "frozen_national_totals": frozen_totals,
            "rows": comparison,
        },
        "osecan": osecan,
        "validation": {"checks": checks},
        "caveats": list(CAVEATS),
        "reproduce": f".venv/bin/python -m {MODULE_PATH}",
    }
    summary_path = OUTPUT_DIR / "summary.json"
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    _write_report(
        OUTPUT_DIR / REPORT_NAME,
        harvest_rows,
        national,
        comparison,
        schedule,
        run_status,
        validate_output,
        osecan,
    )

    output_names = [
        "ws4_119m_harvest.csv",
        "ws4_119m_coefficients.csv",
        "ws4_119m_product_chain.csv",
        "ws4_vs_chain_v51_comparison.csv",
        "summary.json",
        REPORT_NAME,
    ]
    outputs = {
        name: {
            "bytes": (OUTPUT_DIR / name).stat().st_size,
            "sha256": _sha256(OUTPUT_DIR / name),
        }
        for name in output_names
    }
    manifest = {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": generated,
        "title": ARTIFACT_TITLE,
        "labels": LABELS,
        "inputs": inputs,
        "outputs": outputs,
        "reproduce": f".venv/bin/python -m {MODULE_PATH}",
        "validation": summary["validation"],
    }
    (OUTPUT_DIR / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary


def main() -> None:
    summary = build()
    print(
        "WS4 chain 119m built: "
        f"{summary['national_harvest']['achieved_m3']:.3f} m3 achieved; "
        f"{summary['ws4']['schedule_rows']} schedule rows; "
        f"status={summary['status']}"
    )


if __name__ == "__main__":
    main()
