"""Build a 119 Mm3 chain from the full national 30-m tiled WS4 coverage.

The 3,345 built and validated 30-m tiled WS4 datasets are scanned in two
passes:

1. Candidate scan: read only ``blocks``, ``tracks``, ``features`` and
   ``curves``.  Natural/unmanaged blocks with a block offset of at least 40
   are ranked by their approximate volume ``area_ha * curve(block offset)``.
   The top 20,000 eligible blocks per tile are kept as that tile's candidate
   contribution.
2. Exact volume: for selected blocks only, read ``fragments`` and compute
   ``sum(fragment area * curve(fragment age))`` at the exact fragment age.
   Block area and fragment area are conserved; no volume is scaled or
   fabricated.

Whole blocks are selected per province with a volume-aware greedy.  Blocks are
never split or counted twice.  The exact provincial harvest is then passed
through the frozen chain v5.1 per-province product/residue coefficients on two
bases: the realised tiled harvest and one national factor that matches the
118.9773391 Mm3 control total.

All outputs are model estimates.  The tiled datasets, the national 990-m
dataset, frozen chain v5.1 and official OSeCAN are read-only.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import re
import subprocess
from collections.abc import Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from workflows.national.facilities import ws4_chain_119m as chain

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_ROOT = REPO_ROOT / "data"
TILED_ROOT = DATA_ROOT / "derived/ws4/tiled-30m"
TILED_MANIFEST = TILED_ROOT / "national_tiled_manifest.json"
CONTROL_CSV = (
    DATA_ROOT / "derived/national/harvest_basis/provincial_allocation/119m.csv"
)
CHAIN_V51_DIR = DATA_ROOT / "derived/national/facilities/national_product_chain_v5"
CHAIN_V51_CSV = CHAIN_V51_DIR / "product_chain_by_province.csv"
CHAIN_V51_COEFFICIENTS = CHAIN_V51_DIR / "coefficients.csv"
NATIONAL_990M_MANIFEST = (
    DATA_ROOT / "derived/ws4/scanfi-national-990m/dataset/manifest.json"
)
OUTPUT_DIR = DATA_ROOT / "derived/national/facilities/ws4_tiled_chain_119m"
WORK_DIR = REPO_ROOT / "tmp/ws4_tiled_chain_119m"
WS4_BIN = REPO_ROOT / ".venv-ws4/bin/ws4"

ARTIFACT = "ws4_tiled_chain_119m"
ARTIFACT_VERSION = "1.0.0"
ARTIFACT_TITLE = "Tiled 30-m WS4 119 Mm3 chain and chain v5.1 comparison"
REPORT_NAME = "WS4_TILED_CHAIN_119M.md"
MODULE_PATH = "workflows.national.facilities.ws4_tiled_chain_119m"

PROVINCES: tuple[str, ...] = chain.PROVINCES
BASES: tuple[str, ...] = chain.BASES
CONTROL_TOTAL_M3 = chain.CONTROL_TOTAL_M3
LHV_GJ_PER_ODT = chain.LHV_GJ_PER_ODT
PJ_PER_GJ = chain.PJ_PER_GJ
AGE_OFFSET_MIN = chain.AGE_OFFSET_MIN
CURVE_AGE_MAX = chain.CURVE_AGE_MAX
COEFFICIENT_METRICS = chain.COEFFICIENT_METRICS
METRICS = chain.METRICS

PER_TILE_CANDIDATE_LIMIT = 20_000
PROVINCE_POOL_LIMIT = 2_000_000
POOL_MARGIN = 1.10
EXACT_CANDIDATE_LIMIT = 50_000
MAX_OVERSHOOT_FRACTION = 0.0005
MIN_OVERSHOOT_TOLERANCE_M3 = 1_000.0
SELECTION_TOLERANCE_M3 = 1e-6
AREA_TOLERANCE_HA = 1e-6
DEFAULT_WORKERS = 32
SAMPLE_TILE_COUNT = 3
MAX_TOP_UP_ROUNDS = 10

NATURAL_TRACK_RE = re.compile(
    r"^AU\(([^)]+)\)\+IFM\(unmanaged\)\+ORIGIN\(natural\)"
)
AU_RE = re.compile(r"^AU\(([^)]+)\)")
FEATURE_RE = re.compile(
    r"^feature\.Yield\.(?:[^.]+\.)?unmanaged\.Total$"
)

LABELS: dict[str, bool] = {
    "model_estimate": True,
    "observed": False,
    "policy_grade": False,
    "national_claim": False,
    "ws4_derived": True,
    "tiled_30m": True,
    "official_osecan_changed": False,
    "chain_v51_unchanged": True,
}
ROW_LABELS: dict[str, str] = {
    key: ("true" if value else "false") for key, value in LABELS.items()
}
CAVEATS: tuple[str, ...] = (
    (
        "The 119 Mm3 schedule is a whole-block arithmetic verification over "
        "the tiled 30-m WS4 coverage, not a sustainable harvest, AAC, policy "
        "result, or investment case."
    ),
    (
        "Pass 1 approximates block volume as area_ha * curve(block offset); "
        "the block offset is a 10-year age class. Pass 2 replaces that "
        "approximation with the exact sum of fragment area * curve(fragment "
        "age) for selected blocks only."
    ),
    (
        "Blocks are selected whole. Provincial achieved volume can overshoot "
        "the control target by the final selected block volume because no "
        "block is split, scaled, or fabricated."
    ),
    (
        "The candidate pool is the top 20,000 eligible blocks by approximate "
        "volume per tile. It is a documented selection pool, not a claim that "
        "smaller blocks cannot be harvested."
    ),
    (
        "Fragment volumes are 0.09-ha forest-area fragments multiplied by "
        "their tile RSYC curve at the exact integer fragment age. They are "
        "not operable, tenure, or economically feasible volumes."
    ),
    (
        "The managed track shares the natural RSYC curve; the sample "
        "simulation applies one CC treatment at offset 0 and does not model "
        "planting response, silviculture, fertilization, or genetic gain."
    ),
    (
        "Product and residue values apply frozen chain v5.1 per-province "
        "coefficients to exact tiled harvest by origin province. Facility "
        "assignment, mill capacity, transport, and interprovincial trade are "
        "not re-run."
    ),
    (
        "The scaled basis uses one national scale factor to preserve tiled "
        "provincial shares and match the 118.9773391 Mm3 control total; it is "
        "not per-province target scaling."
    ),
    (
        "Process residue and harvest residue are separate streams. PJ values "
        "use the frozen 18.5 GJ/ODT lower heating value; 0.60 harvest "
        "recovery is a reference case, not a calibrated recovery factor."
    ),
    "No official OSeCAN file, frozen chain v5.1 artifact, or tiled dataset is changed.",
)


class Ws4TiledChainError(RuntimeError):
    """Raised when the tiled WS4 chain build fails a guardrail."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _relative(path: Path) -> str:
    return str(path.relative_to(REPO_ROOT))


def _tree_state(root: Path) -> dict[str, Any]:
    if not root.is_dir():
        raise Ws4TiledChainError(f"directory is missing: {root}")
    hashes: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        hashes[_relative(path)] = _sha256(path)
    if not hashes:
        raise Ws4TiledChainError(f"directory is empty: {root}")
    digest = hashlib.sha256()
    for relative, value in sorted(hashes.items()):
        digest.update(f"{relative}:{value}\n".encode())
    return {
        "root": _relative(root),
        "file_count": len(hashes),
        "tree_sha256": digest.hexdigest(),
    }


def _git_deletions(repository: Path) -> list[str]:
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repository,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise Ws4TiledChainError(
            f"cannot inspect git status in {repository}: {result.stderr.strip()}"
        )
    return [
        line
        for line in result.stdout.splitlines()
        if len(line) >= 2 and (line[0] == "D" or line[1] == "D")
    ]


def assert_no_unexpected_deletions() -> dict[str, Any]:
    """Fail closed on any ``D`` in the parent or data repository status."""
    incidents: list[str] = []
    for repository in (REPO_ROOT, DATA_ROOT):
        for line in _git_deletions(repository):
            incidents.append(f"{repository.name or repository}: {line}")
    if incidents:
        raise Ws4TiledChainError(
            "unexpected deletion status detected; halting: "
            + " | ".join(incidents)
        )
    return {
        "checked_repositories": ["parent", "data"],
        "no_unexpected_deletions": True,
    }


def _read_rows(path: Path, required: Sequence[str], label: str) -> list[dict[str, str]]:
    if not path.is_file():
        raise Ws4TiledChainError(f"{label} does not exist: {path}")
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = [column for column in required if column not in (reader.fieldnames or [])]
        if missing:
            raise Ws4TiledChainError(
                f"{label} is missing columns {missing}: {path}"
            )
        rows = list(reader)
    if not rows:
        raise Ws4TiledChainError(f"{label} is empty: {path}")
    return rows


def _write_csv(
    path: Path, fields: Sequence[str], rows: Sequence[Mapping[str, Any]]
) -> None:
    if not rows:
        raise Ws4TiledChainError(f"refusing to write empty CSV: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=list(fields),
            extrasaction="ignore",
            lineterminator="\n",
        )
        writer.writeheader()
        for row in rows:
            rendered: dict[str, Any] = {}
            for field in fields:
                value = row.get(field)
                if value is None:
                    rendered[field] = ""
                elif isinstance(value, bool):
                    rendered[field] = "true" if value else "false"
                elif isinstance(value, float):
                    if not math.isfinite(value):
                        raise Ws4TiledChainError(
                            f"refusing to write non-finite {field}: {value!r}"
                        )
                    rendered[field] = f"{value:.17g}"
                else:
                    rendered[field] = value
            writer.writerow(rendered)


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")


def _finite(value: Any, label: str, *, minimum: float | None = 0.0) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise Ws4TiledChainError(f"{label} is not finite: {value!r}")
    if minimum is not None and number < minimum - 1e-12:
        raise Ws4TiledChainError(f"{label} is below {minimum}: {number!r}")
    return number


def _parse_bool(value: Any, label: str) -> bool:
    text = str(value).strip().lower()
    if text in {"true", "1", "yes"}:
        return True
    if text in {"false", "0", "no", ""}:
        return False
    raise Ws4TiledChainError(f"{label} is not a boolean: {value!r}")


def _require_close(
    left: float,
    right: float,
    label: str,
    *,
    abs_tol: float = SELECTION_TOLERANCE_M3,
    rel_tol: float = 1e-9,
) -> None:
    scale = max(abs(left), abs(right), 1.0)
    if abs(left - right) > rel_tol * scale + abs_tol:
        raise Ws4TiledChainError(
            f"{label} mismatch: {left!r} != {right!r} "
            f"(absolute {abs(left - right):.12g})"
        )


def load_manifest_tiles() -> pd.DataFrame:
    """Return the built+validated tile catalogue with province and AU code."""
    if not TILED_MANIFEST.is_file():
        raise Ws4TiledChainError(f"missing tiled manifest: {TILED_MANIFEST}")
    payload = json.loads(TILED_MANIFEST.read_text(encoding="utf-8"))
    completed = payload.get("completed_tiles")
    if not isinstance(completed, list) or not completed:
        raise Ws4TiledChainError("tiled manifest has no completed tiles")
    rows: list[dict[str, Any]] = []
    for item in completed:
        slug = str(item["tile_slug"]).strip()
        province = str(item["province"]).strip().upper()
        tile_id = str(item["tile_id"]).strip()
        dataset_path = str(item["dataset_path"]).strip()
        if province not in PROVINCES:
            raise Ws4TiledChainError(f"unknown province in tiled manifest: {province}")
        slug_parts = slug.split("-")
        if len(slug_parts) != 3 or slug_parts[1] != province:
            raise Ws4TiledChainError(f"tile slug/province mismatch: {slug}/{province}")
        if slug_parts[0] != tile_id:
            raise Ws4TiledChainError(f"tile slug/tile id mismatch: {slug}/{tile_id}")
        if item.get("validation_status") != "pass":
            raise Ws4TiledChainError(f"tile did not pass validation: {slug}")
        if int(item.get("validation_exit_code", -1)) != 0:
            raise Ws4TiledChainError(f"tile validation exit code is not zero: {slug}")
        dataset_dir = REPO_ROOT / dataset_path
        if not dataset_dir.is_dir():
            raise Ws4TiledChainError(f"tile dataset is missing: {dataset_dir}")
        rows.append(
            {
                "tile_slug": slug,
                "province": province,
                "tile_id": tile_id,
                "au_code": tile_id,
                "dataset_dir": str(dataset_dir),
                "dataset_manifest_sha256": str(
                    item.get("dataset_manifest_sha256", "")
                ),
                "eligible_blocks_manifest": int(item.get("eligible_blocks", 0)),
                "forest_area_ha_manifest": float(item.get("forest_area_ha", 0.0)),
                "fragments_manifest": int(item.get("forest_fragments", 0)),
            }
        )
    tiles = pd.DataFrame(rows)
    if tiles["tile_slug"].duplicated().any():
        raise Ws4TiledChainError("duplicate tile slug in tiled manifest")
    expected = int(payload.get("totals", {}).get("selected_tiles", 0))
    if expected and len(tiles) != expected:
        raise Ws4TiledChainError(
            f"tiled manifest catalogues {len(tiles)} tiles, expected {expected}"
        )
    return tiles.sort_values(["province", "tile_slug"]).reset_index(drop=True)


def _curve_matrix(
    curves: pd.DataFrame, curve_ids: Sequence[int]
) -> tuple[np.ndarray, dict[int, int], np.ndarray]:
    required = sorted({int(value) for value in curve_ids})
    if not required:
        raise Ws4TiledChainError("no curve ids supplied")
    curves = curves[curves["curve_id"].isin(required)].copy()
    if curves.empty:
        raise Ws4TiledChainError("no curve rows for the selected curve ids")
    ages = curves["x"].to_numpy(dtype=float)
    integer_age = np.rint(ages).astype(np.int64)
    is_exact = np.isclose(ages, integer_age, rtol=0.0, atol=1e-9)
    curves = curves[is_exact].copy()
    curves["age"] = integer_age[is_exact]
    curves = curves[curves["age"].between(0, CURVE_AGE_MAX)]
    if curves.duplicated(["curve_id", "age"]).any():
        raise Ws4TiledChainError("duplicate curve age row")
    values = curves["y"].to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise Ws4TiledChainError("non-finite yield curve value")
    if (values < -1e-12).any():
        raise Ws4TiledChainError("negative yield curve value")
    pivot = curves.pivot(index="age", columns="curve_id", values="y")
    full_index = np.arange(0, CURVE_AGE_MAX + 1, dtype=np.int64)
    pivot = pivot.reindex(full_index)
    missing = pivot.isna().any(axis=None)
    if missing:
        raise Ws4TiledChainError("yield curve is missing an integer age")
    order = {curve_id: position for position, curve_id in enumerate(required)}
    matrix = pivot.to_numpy(dtype=float)
    return pivot.index.to_numpy(dtype=np.int64), order, matrix


def _natural_track_map(dataset_dir: Path) -> tuple[pd.DataFrame, pd.Series]:
    tracks = pd.read_parquet(
        dataset_dir / "tracks.parquet", columns=["track_id", "name"]
    )
    names = tracks["name"].astype(str)
    natural_mask = (
        names.str.contains(r"IFM\(unmanaged\)", regex=True)
        & names.str.contains(r"ORIGIN\(natural\)", regex=True)
        & ~names.str.contains(r"SILV_STATE\(thinned\)", regex=True)
    )
    natural = tracks[natural_mask].copy()
    if natural.empty:
        raise Ws4TiledChainError(f"no natural/unmanaged tracks: {dataset_dir}")
    extracted = natural["name"].astype(str).str.extract(NATURAL_TRACK_RE)
    if extracted.isna().any(axis=None):
        raise Ws4TiledChainError(f"unparseable natural track name: {dataset_dir}")
    natural["au_code"] = extracted[0]
    if natural["track_id"].duplicated().any():
        raise Ws4TiledChainError(f"duplicate natural track id: {dataset_dir}")

    features = pd.read_parquet(
        dataset_dir / "features.parquet",
        columns=["track_id", "label", "curve_id"],
    )
    labels = features["label"].astype(str)
    mask = labels.str.match(FEATURE_RE)
    natural_features = features[mask].copy()
    natural_features = natural_features.merge(
        natural[["track_id", "au_code"]],
        on="track_id",
        how="inner",
        validate="one_to_one",
    )
    if len(natural_features) != len(natural):
        raise Ws4TiledChainError(
            f"not every natural track has one unmanaged Total feature: {dataset_dir}"
        )
    curve_map = natural_features.set_index("track_id")["curve_id"]
    if curve_map.index.duplicated().any():
        raise Ws4TiledChainError(f"duplicate feature track id: {dataset_dir}")
    return natural_features[["track_id", "au_code", "curve_id", "label"]], curve_map


def _scan_tile_worker(task: Mapping[str, Any]) -> dict[str, Any]:
    """Pass 1 worker: rank eligible natural blocks by approximate volume."""
    tile_slug = str(task["tile_slug"])
    province = str(task["province"])
    dataset_dir = Path(task["dataset_dir"])
    candidate_dir = Path(task["candidate_dir"])
    limit = int(task["per_tile_limit"])

    natural, curve_map = _natural_track_map(dataset_dir)
    au_codes = sorted({str(value) for value in natural["au_code"]})
    expected_au = str(task["au_code"])
    if au_codes != [expected_au]:
        raise Ws4TiledChainError(
            f"{tile_slug}: AU codes {au_codes} do not match tile id {expected_au}"
        )

    blocks = pd.read_parquet(
        dataset_dir / "blocks.parquet",
        columns=["block_id", "track_id", "offset", "area_ha"],
    )
    blocks = blocks[blocks["track_id"].isin(natural["track_id"])].copy()
    if blocks.empty:
        raise Ws4TiledChainError(f"{tile_slug}: no natural blocks")
    blocks["curve_id"] = blocks["track_id"].map(curve_map)
    if blocks["curve_id"].isna().any():
        raise Ws4TiledChainError(f"{tile_slug}: natural block lacks a curve")
    offsets = blocks["offset"].to_numpy(dtype=np.int64)
    areas = blocks["area_ha"].to_numpy(dtype=float)
    if (offsets < 0).any() or (offsets > CURVE_AGE_MAX).any():
        raise Ws4TiledChainError(f"{tile_slug}: block offset outside curve range")
    if not np.isfinite(areas).all() or (areas <= 0.0).any():
        raise Ws4TiledChainError(f"{tile_slug}: non-positive or non-finite block area")

    curves = pd.read_parquet(
        dataset_dir / "curves.parquet", columns=["curve_id", "x", "y"]
    )
    age_index, curve_order, matrix = _curve_matrix(
        curves, blocks["curve_id"].unique().tolist()
    )
    if not np.array_equal(age_index, np.arange(CURVE_AGE_MAX + 1)):
        raise Ws4TiledChainError(f"{tile_slug}: incomplete curve age grid")
    columns = np.fromiter(
        (curve_order[int(value)] for value in blocks["curve_id"]), dtype=np.int64
    )
    yields = matrix[offsets, columns]
    approx = areas * yields
    if not np.isfinite(approx).all():
        raise Ws4TiledChainError(f"{tile_slug}: non-finite approximate volume")
    eligible_mask = (offsets >= AGE_OFFSET_MIN) & (approx > 0.0)
    eligible_count = int(eligible_mask.sum())
    eligible_volume = float(approx[eligible_mask].sum())
    zero_approx = int(((offsets >= AGE_OFFSET_MIN) & (approx <= 0.0)).sum())

    frame = blocks.loc[eligible_mask, ["block_id", "track_id", "offset", "area_ha", "curve_id"]].copy()
    frame["approx_volume_m3"] = approx[eligible_mask]
    frame["tile_slug"] = tile_slug
    frame["province"] = province
    frame["au_code"] = expected_au
    if frame.empty:
        return {
            "tile_slug": tile_slug,
            "province": province,
            "au_code": expected_au,
            "blocks_total": len(blocks),
            "eligible_blocks": 0,
            "eligible_volume_m3": 0.0,
            "zero_approx_blocks": zero_approx,
            "candidate_blocks": 0,
            "candidate_volume_m3": 0.0,
            "max_approx_volume_m3": 0.0,
            "mean_approx_volume_m3": 0.0,
            "natural_tracks": len(natural),
            "curve_count": len(curve_order),
            "candidate_path": "",
        }

    if len(frame) > limit:
        values = frame["approx_volume_m3"].to_numpy(dtype=float)
        keep = np.argpartition(values, len(values) - limit)[-limit:]
        frame = frame.iloc[keep]
    frame = frame.sort_values(
        ["approx_volume_m3", "block_id"], ascending=[False, True], kind="mergesort"
    ).reset_index(drop=True)
    candidate_dir.mkdir(parents=True, exist_ok=True)
    candidate_path = candidate_dir / f"{tile_slug}.parquet"
    frame.to_parquet(candidate_path, index=False)
    return {
        "tile_slug": tile_slug,
        "province": province,
        "au_code": expected_au,
        "blocks_total": len(blocks),
        "eligible_blocks": eligible_count,
        "eligible_volume_m3": eligible_volume,
        "zero_approx_blocks": zero_approx,
        "candidate_blocks": len(frame),
        "candidate_volume_m3": float(frame["approx_volume_m3"].sum()),
        "max_approx_volume_m3": float(frame["approx_volume_m3"].max()),
        "mean_approx_volume_m3": float(frame["approx_volume_m3"].mean()),
        "natural_tracks": len(natural),
        "curve_count": len(curve_order),
        "candidate_path": str(candidate_path),
    }


def _top_k(frame: pd.DataFrame, column: str, limit: int | None) -> pd.DataFrame:
    if limit is None or len(frame) <= limit:
        return frame
    values = frame[column].to_numpy(dtype=float)
    keep = np.argpartition(values, len(values) - limit)[-limit:]
    return frame.iloc[keep]


def _merge_candidates(
    province: str,
    tile_stats: pd.DataFrame,
    *,
    limit: int | None,
) -> pd.DataFrame:
    paths = [
        Path(value)
        for value in tile_stats.loc[
            (tile_stats["province"] == province)
            & (tile_stats["candidate_blocks"] > 0),
            "candidate_path",
        ].tolist()
    ]
    if not paths:
        return pd.DataFrame(
            columns=[
                "tile_slug",
                "province",
                "au_code",
                "block_id",
                "track_id",
                "offset",
                "area_ha",
                "curve_id",
                "approx_volume_m3",
            ]
        )
    running: pd.DataFrame | None = None
    batch_size = 100
    for start in range(0, len(paths), batch_size):
        frames = [
            pd.read_parquet(path)
            for path in paths[start : start + batch_size]
        ]
        combined = (
            pd.concat([running, *frames], ignore_index=True)
            if running is not None
            else pd.concat(frames, ignore_index=True)
        )
        running = _top_k(combined, "approx_volume_m3", limit)
    assert running is not None
    return running.sort_values(
        ["approx_volume_m3", "tile_slug", "block_id"],
        ascending=[False, True, True],
        kind="mergesort",
    ).reset_index(drop=True)


def _greedy_select(
    frame: pd.DataFrame, volume_column: str, target_m3: float
) -> pd.DataFrame:
    """Select whole blocks to meet or exceed the target with small overshoot."""
    if target_m3 <= 0.0:
        return frame.iloc[0:0].copy()
    ordered = frame.sort_values(
        [volume_column, "tile_slug", "block_id"],
        ascending=[False, True, True],
        kind="mergesort",
    )
    volumes = ordered[volume_column].to_numpy(dtype=float)
    if not np.isfinite(volumes).all() or (volumes < 0.0).any():
        raise Ws4TiledChainError("selection pool has invalid volumes")
    remaining = float(target_m3)
    chosen: list[int] = []
    for position, volume in enumerate(volumes):
        if volume <= remaining + SELECTION_TOLERANCE_M3:
            chosen.append(position)
            remaining -= float(volume)
            if remaining <= SELECTION_TOLERANCE_M3:
                break
    if remaining > SELECTION_TOLERANCE_M3:
        used = np.zeros(len(volumes), dtype=bool)
        used[np.asarray(chosen, dtype=np.int64)] = True
        candidates = np.flatnonzero(~used)
        overshoot = volumes[candidates] - remaining
        positive = overshoot >= -SELECTION_TOLERANCE_M3
        if not positive.any():
            raise Ws4TiledChainError(
                f"no block can close remaining {remaining!r} m3"
            )
        positive_candidates = candidates[positive]
        positive_overshoot = overshoot[positive]
        order = np.lexsort((positive_candidates, positive_overshoot))
        chosen.append(int(positive_candidates[order[0]]))
        remaining -= float(volumes[chosen[-1]])
    selected = ordered.iloc[chosen].copy()
    achieved = float(selected[volume_column].sum())
    if achieved + SELECTION_TOLERANCE_M3 < target_m3:
        raise Ws4TiledChainError(
            f"selected volume {achieved!r} is below target {target_m3!r}"
        )
    return selected


def _exact_tile_worker(task: Mapping[str, Any]) -> pd.DataFrame:
    """Pass 2 worker: exact fragment-age volume for selected blocks."""
    tile_slug = str(task["tile_slug"])
    dataset_dir = Path(task["dataset_dir"])
    selected = pd.DataFrame(task["blocks"])
    if selected.empty:
        return pd.DataFrame()
    selected["block_id"] = selected["block_id"].astype("int64")

    natural, curve_map = _natural_track_map(dataset_dir)
    au_codes = sorted({str(value) for value in natural["au_code"]})
    if au_codes != [str(task["au_code"])]:
        raise Ws4TiledChainError(
            f"{tile_slug}: natural AU changed between passes"
        )
    if not set(selected["track_id"].astype("int64")).issubset(
        set(natural["track_id"].astype("int64"))
    ):
        raise Ws4TiledChainError(f"{tile_slug}: selected block track is not natural")
    expected_curves = selected.set_index("block_id")["curve_id"].to_dict()
    actual_curves = selected.set_index("block_id")["track_id"].map(curve_map).to_dict()
    for block_id, curve_id in expected_curves.items():
        if int(actual_curves[block_id]) != int(curve_id):
            raise Ws4TiledChainError(
                f"{tile_slug}: block {block_id} curve changed between passes"
            )

    blocks = pd.read_parquet(
        dataset_dir / "blocks.parquet",
        columns=["block_id", "track_id", "offset", "area_ha"],
    )
    blocks = blocks[blocks["block_id"].isin(selected["block_id"])].copy()
    if len(blocks) != len(selected):
        raise Ws4TiledChainError(f"{tile_slug}: selected block is missing")
    merged_blocks = selected.merge(
        blocks.rename(columns={"track_id": "block_track_id", "area_ha": "block_area_ha"}),
        on="block_id",
        how="left",
        validate="one_to_one",
    )
    if merged_blocks[["block_track_id", "block_area_ha"]].isna().any(axis=None):
        raise Ws4TiledChainError(f"{tile_slug}: selected block lookup failed")
    if (merged_blocks["track_id"].astype("int64") != merged_blocks["block_track_id"].astype("int64")).any():
        raise Ws4TiledChainError(f"{tile_slug}: block track mismatch")
    area_gap = np.abs(
        merged_blocks["area_ha"].to_numpy(dtype=float)
        - merged_blocks["block_area_ha"].to_numpy(dtype=float)
    )
    if float(area_gap.max()) > AREA_TOLERANCE_HA:
        raise Ws4TiledChainError(f"{tile_slug}: block area changed between passes")

    fragments = pd.read_parquet(
        dataset_dir / "fragments.parquet",
        columns=["fragment_id", "block_id", "area_ha", "age", "track_id"],
    )
    fragments = fragments[fragments["block_id"].isin(selected["block_id"])].copy()
    if fragments.empty:
        raise Ws4TiledChainError(f"{tile_slug}: selected blocks have no fragments")
    fragments = fragments.merge(
        selected[["block_id", "track_id", "curve_id"]].rename(
            columns={"track_id": "block_track_id", "curve_id": "block_curve_id"}
        ),
        on="block_id",
        how="left",
        validate="many_to_one",
    )
    if fragments["block_track_id"].isna().any():
        raise Ws4TiledChainError(f"{tile_slug}: fragment block lookup failed")
    if (
        fragments["track_id"].astype("int64")
        != fragments["block_track_id"].astype("int64")
    ).any():
        raise Ws4TiledChainError(f"{tile_slug}: fragment track mismatch")
    ages = fragments["age"].to_numpy(dtype=np.int64)
    if (ages < 0).any() or (ages > CURVE_AGE_MAX).any():
        raise Ws4TiledChainError(f"{tile_slug}: fragment age outside curve range")
    fragment_area = fragments["area_ha"].to_numpy(dtype=float)
    if not np.isfinite(fragment_area).all() or (fragment_area <= 0.0).any():
        raise Ws4TiledChainError(f"{tile_slug}: invalid fragment area")

    curves = pd.read_parquet(
        dataset_dir / "curves.parquet", columns=["curve_id", "x", "y"]
    )
    age_index, curve_order, matrix = _curve_matrix(
        curves, fragments["block_curve_id"].unique().tolist()
    )
    if not np.array_equal(age_index, np.arange(CURVE_AGE_MAX + 1)):
        raise Ws4TiledChainError(f"{tile_slug}: incomplete curve age grid")
    columns = np.fromiter(
        (curve_order[int(value)] for value in fragments["block_curve_id"]),
        dtype=np.int64,
    )
    yields = matrix[ages, columns]
    volumes = fragment_area * yields
    if not np.isfinite(volumes).all() or (volumes < -1e-12).any():
        raise Ws4TiledChainError(f"{tile_slug}: invalid fragment volume")
    fragments["volume_m3"] = volumes
    fragments["age_area"] = ages.astype(float) * fragment_area

    grouped = fragments.groupby("block_id", as_index=False).agg(
        exact_volume_m3=("volume_m3", "sum"),
        fragment_area_ha=("area_ha", "sum"),
        fragment_count=("fragment_id", "size"),
        age_area=("age_area", "sum"),
        min_fragment_age=("age", "min"),
        max_fragment_age=("age", "max"),
    )
    grouped["mean_fragment_age"] = (
        grouped["age_area"] / grouped["fragment_area_ha"]
    )
    result = selected.merge(grouped, on="block_id", how="left", validate="one_to_one")
    if result["exact_volume_m3"].isna().any():
        raise Ws4TiledChainError(f"{tile_slug}: selected block has no exact volume")
    gap = np.abs(
        result["area_ha"].to_numpy(dtype=float)
        - result["fragment_area_ha"].to_numpy(dtype=float)
    )
    if float(gap.max()) > AREA_TOLERANCE_HA:
        raise Ws4TiledChainError(f"{tile_slug}: fragment/block area non-conservation")
    if (result["exact_volume_m3"] < -1e-9).any():
        raise Ws4TiledChainError(f"{tile_slug}: negative exact block volume")
    return result.drop(columns=["age_area"])


def _exact_for_blocks(
    blocks: pd.DataFrame,
    tiles: pd.DataFrame,
    *,
    workers: int,
) -> pd.DataFrame:
    if blocks.empty:
        return blocks.assign(
            exact_volume_m3=[],
            fragment_area_ha=[],
            fragment_count=[],
            mean_fragment_age=[],
            min_fragment_age=[],
            max_fragment_age=[],
        )
    tile_lookup = tiles.set_index("tile_slug")[["dataset_dir", "au_code"]]
    tasks: list[dict[str, Any]] = []
    for tile_slug, group in blocks.groupby("tile_slug", sort=True):
        if tile_slug not in tile_lookup.index:
            raise Ws4TiledChainError(f"selected block tile is unknown: {tile_slug}")
        tasks.append(
            {
                "tile_slug": tile_slug,
                "au_code": tile_lookup.loc[tile_slug, "au_code"],
                "dataset_dir": tile_lookup.loc[tile_slug, "dataset_dir"],
                "blocks": group[
                    [
                        "province",
                        "tile_slug",
                        "au_code",
                        "block_id",
                        "track_id",
                        "offset",
                        "area_ha",
                        "curve_id",
                        "approx_volume_m3",
                    ]
                ].to_dict(orient="records"),
            }
        )
    frames: list[pd.DataFrame] = []
    with ProcessPoolExecutor(max_workers=max(1, min(workers, len(tasks)))) as pool:
        futures = {pool.submit(_exact_tile_worker, task): task for task in tasks}
        for future in as_completed(futures):
            task = futures[future]
            try:
                frames.append(future.result())
            except Exception as exc:
                raise Ws4TiledChainError(
                    f"exact volume failed for {task['tile_slug']}: {exc}"
                ) from exc
    exact = pd.concat(frames, ignore_index=True)
    if exact.duplicated(["tile_slug", "block_id"]).any():
        raise Ws4TiledChainError("exact volume returned a duplicate block")
    return exact


def _select_province(
    province: str,
    pool: pd.DataFrame,
    target_m3: float,
    tiles: pd.DataFrame,
    *,
    workers: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Select exact blocks for one province, expanding until overshoot is small.

    Exact fragment volumes are computed for the largest
    ``EXACT_CANDIDATE_LIMIT`` provincial candidates (by approximate volume),
    plus further batches if the target is not reached or the exact overshoot
    exceeds the tolerance.  The final schedule is a whole-block exact greedy
    subset of those candidates.
    """
    if target_m3 <= 0.0:
        return pool.iloc[0:0].copy(), pool.iloc[0:0].copy()
    if pool.empty:
        raise Ws4TiledChainError(f"{province}: candidate pool is empty")
    ordered = pool.sort_values(
        ["approx_volume_m3", "tile_slug", "block_id"],
        ascending=[False, True, True],
        kind="mergesort",
    ).reset_index(drop=True)
    cumulative = ordered["approx_volume_m3"].cumsum()
    target_index = int(cumulative.searchsorted(target_m3, side="left")) + 1
    computed_count = min(
        len(ordered), max(EXACT_CANDIDATE_LIMIT, target_index)
    )

    computed = _exact_for_blocks(ordered.iloc[:computed_count], tiles, workers=workers)
    tolerance_m3 = max(
        MIN_OVERSHOOT_TOLERANCE_M3, target_m3 * MAX_OVERSHOOT_FRACTION
    )
    rounds = 0
    while True:
        exact_total = float(computed["exact_volume_m3"].sum())
        if exact_total + SELECTION_TOLERANCE_M3 < target_m3:
            if computed_count >= len(ordered):
                raise Ws4TiledChainError(
                    f"{province}: candidate pool exhausted with exact shortfall "
                    f"{target_m3 - exact_total!r} m3"
                )
            next_count = min(len(ordered), computed_count + EXACT_CANDIDATE_LIMIT)
            extra = ordered.iloc[computed_count:next_count]
            computed = pd.concat(
                [computed, _exact_for_blocks(extra, tiles, workers=workers)],
                ignore_index=True,
            )
            computed_count = next_count
            rounds += 1
            if rounds > MAX_TOP_UP_ROUNDS:
                raise Ws4TiledChainError(
                    f"{province}: exact volume still below target after "
                    f"{rounds} expansion rounds"
                )
            continue
        final = _greedy_select(computed, "exact_volume_m3", target_m3)
        overshoot = float(final["exact_volume_m3"].sum()) - target_m3
        if overshoot <= tolerance_m3 or computed_count >= len(ordered):
            break
        next_count = min(len(ordered), computed_count + EXACT_CANDIDATE_LIMIT)
        extra = ordered.iloc[computed_count:next_count]
        computed = pd.concat(
            [computed, _exact_for_blocks(extra, tiles, workers=workers)],
            ignore_index=True,
        )
        computed_count = next_count
        rounds += 1
        if rounds > MAX_TOP_UP_ROUNDS:
            break
    if computed.duplicated(["tile_slug", "block_id"]).any():
        raise Ws4TiledChainError(f"{province}: block computed twice during selection")
    if final.duplicated(["tile_slug", "block_id"]).any():
        raise Ws4TiledChainError(f"{province}: block selected twice")
    return final, computed


def _coverage(payload: Mapping[str, Any], tiles: pd.DataFrame) -> dict[str, Any]:
    completed_counts = tiles.groupby("province")["tile_slug"].size().to_dict()
    stage_totals = payload.get("stage_totals", {})
    skipped_zero_count = sum(
        int(row.get("skipped_zero_harvest_tiles", 0))
        for row in stage_totals.values()
    )
    skipped_no_forest = payload.get("skipped_no_forest_tiles", [])
    return {
        "candidates_total": len(payload.get("candidates", [])),
        "built_validated_tiles": len(tiles),
        "tiles_scanned": len(tiles),
        "zero_harvest_skips": skipped_zero_count,
        "no_forest_skips": len(skipped_no_forest),
        "provinces_with_tiles": sorted(completed_counts),
        "provinces_without_tiles": sorted(set(PROVINCES) - set(completed_counts)),
        "built_tiles_by_province": {
            key: int(value) for key, value in sorted(completed_counts.items())
        },
        "full_built_tile_coverage": len(tiles) == 3345,
        "scope_note": (
            "All 3,345 built and validated tiled datasets are scanned. The "
            "203 NU zero-harvest candidates and 14 no-forest slivers are "
            "explicitly skipped by the upstream tiled build and carry no "
            "dataset; they are not fabricated here."
        ),
    }


def _harvest_rows(
    selection_by_province: Mapping[str, pd.DataFrame],
    tile_stats: pd.DataFrame,
    pool_stats: Mapping[str, dict[str, Any]],
    targets: Mapping[str, float],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for province in PROVINCES:
        target = _finite(targets[province], f"{province} target")
        selected = selection_by_province[province]
        stats = tile_stats[tile_stats["province"] == province]
        eligible_blocks = int(stats["eligible_blocks"].sum()) if len(stats) else 0
        eligible_volume = float(stats["eligible_volume_m3"].sum()) if len(stats) else 0.0
        if selected.empty:
            rows.append(
                {
                    "province": province,
                    "target_m3": target,
                    "achieved_m3": 0.0,
                    "delta": 0.0,
                    "overshoot_pct": 0.0,
                    "area_ha": 0.0,
                    "block_count": 0,
                    "tile_count": 0,
                    "mean_age": 0.0,
                    "curve_count": 0,
                    "approx_volume_m3": 0.0,
                    "exact_vs_approx_ratio": 0.0,
                    "eligible_block_count": eligible_blocks,
                    "eligible_volume_m3": eligible_volume,
                    "candidate_pool_count": int(pool_stats[province]["count"]),
                    "candidate_pool_volume_m3": float(
                        pool_stats[province]["volume_m3"]
                    ),
                    "pool_margin": float(pool_stats[province]["margin"]),
                    "status": "zero_target",
                    **ROW_LABELS,
                }
            )
            continue
        achieved = _finite(
            selected["exact_volume_m3"].sum(), f"{province} exact harvest"
        )
        area = _finite(selected["fragment_area_ha"].sum(), f"{province} area")
        mean_age = float(
            (selected["mean_fragment_age"] * selected["fragment_area_ha"]).sum()
            / area
        )
        approx = float(selected["approx_volume_m3"].sum())
        delta = achieved - target
        rows.append(
            {
                "province": province,
                "target_m3": target,
                "achieved_m3": achieved,
                "delta": delta,
                "overshoot_pct": (delta / target * 100.0 if target else 0.0),
                "area_ha": area,
                "block_count": len(selected),
                "tile_count": int(selected["tile_slug"].nunique()),
                "mean_age": mean_age,
                "curve_count": int(
                    selected[["tile_slug", "curve_id"]].drop_duplicates().shape[0]
                ),
                "approx_volume_m3": approx,
                "exact_vs_approx_ratio": achieved / approx if approx else 0.0,
                "candidate_pool_count": int(pool_stats[province]["count"]),
                "candidate_pool_volume_m3": float(
                    pool_stats[province]["volume_m3"]
                ),
                "pool_margin": float(pool_stats[province]["margin"]),
                "eligible_block_count": eligible_blocks,
                "eligible_volume_m3": eligible_volume,
                "status": "pass" if achieved + SELECTION_TOLERANCE_M3 >= target else "shortfall",
                **ROW_LABELS,
            }
        )
    return rows


def _check_frozen_integrity(
    before: Mapping[str, Any], after: Mapping[str, Any]
) -> None:
    if before != after:
        raise Ws4TiledChainError("a frozen input tree changed during the build")


def _run_command(command: Sequence[str], label: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        list(command), capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise Ws4TiledChainError(
            f"{label} failed ({result.returncode}): "
            f"{result.stdout.strip()} {result.stderr.strip()}"
        )
    return result


def _sample_simulations(
    selection_by_province: Mapping[str, pd.DataFrame],
    tiles: pd.DataFrame,
    *,
    work_dir: Path,
) -> list[dict[str, Any]]:
    selected = pd.concat(
        [frame for frame in selection_by_province.values() if not frame.empty],
        ignore_index=True,
    )
    if selected.empty:
        raise Ws4TiledChainError("no selected blocks for sample simulation")
    counts = (
        selected.groupby("tile_slug").size().sort_values(ascending=False)
    )
    if len(counts) < SAMPLE_TILE_COUNT:
        raise Ws4TiledChainError(
            f"only {len(counts)} selected tiles; need {SAMPLE_TILE_COUNT}"
        )
    candidates = [counts.index[0], counts.index[len(counts) // 2], counts.index[-1]]
    tile_lookup = tiles.set_index("tile_slug")
    simulation_rows: list[dict[str, Any]] = []
    for tile_slug in candidates:
        block_rows = selected[selected["tile_slug"] == tile_slug].sort_values("block_id")
        run_dir = work_dir / "simulation" / tile_slug
        run_dir.mkdir(parents=True, exist_ok=True)
        schedule_path = run_dir / "schedule.csv"
        schedule = pd.DataFrame(
            {
                "block_id": block_rows["block_id"].astype("int64"),
                "treatment": "CC",
                "offset": 0,
            }
        )
        schedule.to_csv(schedule_path, index=False, lineterminator="\n")
        dataset_dir = Path(tile_lookup.loc[tile_slug, "dataset_dir"])
        _run_command(
            [str(WS4_BIN), "validate", "--dataset", str(dataset_dir)],
            f"ws4 validate {tile_slug}",
        )
        _run_command(
            [
                str(WS4_BIN),
                "simulate",
                "--dataset",
                str(dataset_dir),
                "--schedule",
                str(schedule_path),
                "--years",
                "1",
                "--out",
                str(run_dir / "run"),
            ],
            f"ws4 simulate {tile_slug}",
        )
        product_path = run_dir / "run" / "product_values.csv"
        if not product_path.is_file():
            raise Ws4TiledChainError(f"missing product values: {product_path}")
        products = pd.read_csv(product_path)
        if list(products.columns) != ["offset", "account", "value"]:
            raise Ws4TiledChainError(
                f"unexpected product values columns: {products.columns.tolist()}"
            )
        products["value"] = pd.to_numeric(products["value"], errors="raise")
        if not np.isfinite(products["value"]).all() or (products["value"] < -1e-9).any():
            raise Ws4TiledChainError(f"{tile_slug}: invalid simulation value")
        harvest = float(
            products.loc[
                products["account"].str.endswith(".unmanaged.CC")
                & products["account"].str.startswith("product.HarvestedVolume."),
                "value",
            ].sum()
        )
        treated = float(
            products.loc[
                products["account"].str.endswith(".unmanaged.CC")
                & products["account"].str.startswith("product.Treated."),
                "value",
            ].sum()
        )
        expected_volume = float(block_rows["exact_volume_m3"].sum())
        expected_area = float(block_rows["fragment_area_ha"].sum())
        _require_close(harvest, expected_volume, f"{tile_slug} simulated harvest")
        _require_close(treated, expected_area, f"{tile_slug} simulated area")
        simulation_rows.append(
            {
                "tile_slug": tile_slug,
                "province": str(block_rows["province"].iloc[0]),
                "block_count": len(block_rows),
                "exact_volume_m3": expected_volume,
                "simulated_harvest_m3": harvest,
                "volume_delta_m3": harvest - expected_volume,
                "exact_area_ha": expected_area,
                "simulated_treated_ha": treated,
                "area_delta_ha": treated - expected_area,
                "years": 1,
                "validate": "pass",
                "simulation": "pass",
                **ROW_LABELS,
            }
        )
    return simulation_rows


def _comparison_rows_with_labels(
    national: Mapping[str, Mapping[str, Any]],
    frozen_totals: Mapping[str, float],
) -> list[dict[str, Any]]:
    rows = chain._comparison_rows(dict(national), dict(frozen_totals))
    for row in rows:
        row.update(ROW_LABELS)
    return rows


def _validation_checks(
    harvest_rows: Sequence[Mapping[str, Any]],
    selected: pd.DataFrame,
    comparison: Sequence[Mapping[str, Any]],
    sample_rows: Sequence[Mapping[str, Any]],
    osecan_status: Mapping[str, Any],
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []

    def check(name: str, passed: bool, detail: str) -> None:
        checks.append({"check": name, "passed": bool(passed), "detail": detail})

    if not selected.empty:
        duplicated = selected.duplicated(["tile_slug", "block_id"]).any()
        check(
            "block uniqueness",
            not duplicated,
            f"{len(selected)} selected rows; duplicate={bool(duplicated)}",
        )
        eligible = (selected["offset"] >= AGE_OFFSET_MIN) & (
            selected["exact_volume_m3"] > 0.0
        )
        check(
            "block eligibility",
            bool(eligible.all()),
            "all selected blocks are natural/unmanaged, offset >= 40, exact volume > 0",
        )
        gap = (
            selected["area_ha"] - selected["fragment_area_ha"]
        ).abs().max()
        check(
            "exact volume conservation",
            bool(float(gap) <= AREA_TOLERANCE_HA),
            f"max block/fragment area gap={float(gap):.12g} ha",
        )
    else:
        check("block uniqueness", True, "zero selected blocks")
        check("block eligibility", True, "zero selected blocks")
        check("exact volume conservation", True, "zero selected blocks")

    target = sum(float(row["target_m3"]) for row in harvest_rows)
    achieved = sum(float(row["achieved_m3"]) for row in harvest_rows)
    check(
        "target and overshoot arithmetic",
        all(
            float(row["achieved_m3"]) + SELECTION_TOLERANCE_M3
            >= float(row["target_m3"])
            for row in harvest_rows
        )
        and math.isclose(achieved - target, sum(float(row["delta"]) for row in harvest_rows), abs_tol=1e-6),
        f"target={target:.6f} m3 achieved={achieved:.6f} m3 delta={achieved - target:.6f} m3",
    )
    finite_ok = True
    detail = ""
    for row in harvest_rows:
        for field, value in row.items():
            if isinstance(value, float) and not math.isfinite(value):
                finite_ok = False
                detail = f"{row['province']}: non-finite {field}"
                break
            if isinstance(value, float) and value < 0.0 and field not in {"delta", "overshoot_pct"}:
                finite_ok = False
                detail = f"{row['province']}: negative {field}"
                break
        if not finite_ok:
            break
    check("no NaN or negative harvest", finite_ok, detail or "all harvest values finite")
    check(
        "comparison arithmetic",
        all(
            math.isclose(
                float(row["ws4_achieved_delta"]),
                float(row["ws4_achieved_value"]) - float(row["chain_v51_value"]),
                abs_tol=1e-6,
            )
            for row in comparison
        ),
        f"{len(comparison)} metrics compared with frozen v5.1",
    )
    check(
        "sample WS4 simulation",
        len(sample_rows) == SAMPLE_TILE_COUNT
        and all(row["simulation"] == "pass" for row in sample_rows),
        f"{len(sample_rows)} tiles simulated for one year",
    )
    check(
        "OSeCAN 119m tier positions",
        bool(osecan_status.get("primary_7_of_7", False))
        and bool(osecan_status.get("secondary_7_of_7", False)),
        (
            f"primary={osecan_status.get('primary_matches')}/7 "
            f"secondary={osecan_status.get('secondary_matches')}/7"
        ),
    )
    check(
        "OSeCAN frozen 21/21 reference",
        bool(osecan_status.get("frozen_21_of_21", False)),
        f"matches={osecan_status.get('frozen_matches')}/21",
    )
    check(
        "official OSeCAN unchanged",
        bool(osecan_status.get("official_unchanged", False)),
        str(osecan_status.get("official_detail", "")),
    )
    return checks


def _md_table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    lines = [
        "| " + " | ".join(header) + " |",
        "|" + "|".join("---" for _ in header) + "|",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return "\n".join(lines)


def _write_report(
    path: Path,
    summary: Mapping[str, Any],
    harvest_rows: Sequence[Mapping[str, Any]],
    comparison: Sequence[Mapping[str, Any]],
) -> None:
    coverage = summary["coverage"]
    harvest_table = _md_table(
        [
            "Province",
            "Target Mm3",
            "Exact Mm3",
            "Delta m3",
            "Area ha",
            "Blocks",
            "Tiles",
            "Mean age",
            "Exact/approx",
        ],
        [
            [
                row["province"],
                f"{float(row['target_m3']) / 1e6:.6f}",
                f"{float(row['achieved_m3']) / 1e6:.6f}",
                f"{float(row['delta']):.3f}",
                f"{float(row['area_ha']):.1f}",
                str(row["block_count"]),
                str(row["tile_count"]),
                f"{float(row['mean_age']):.2f}",
                f"{float(row['exact_vs_approx_ratio']):.6f}",
            ]
            for row in harvest_rows
        ],
    )
    comparison_table = _md_table(
        [
            "Metric",
            "Unit",
            "v5.1",
            "Tiled achieved",
            "Delta",
            "Scaled",
            "Delta",
        ],
        [
            [
                row["metric"],
                row["unit"],
                f"{float(row['chain_v51_value']):.6g}",
                f"{float(row['ws4_achieved_value']):.6g}",
                f"{float(row['ws4_achieved_delta']):.6g}",
                f"{float(row['scaled_to_119m_control_value']):.6g}",
                f"{float(row['scaled_to_119m_control_delta']):.6g}",
            ]
            for row in comparison
        ],
    )
    sample_table = _md_table(
        ["Tile", "Province", "Blocks", "Exact m3", "Simulated m3", "Delta m3"],
        [
            [
                row["tile_slug"],
                row["province"],
                str(row["block_count"]),
                f"{float(row['exact_volume_m3']):.6f}",
                f"{float(row['simulated_harvest_m3']):.6f}",
                f"{float(row['volume_delta_m3']):.3e}",
            ]
            for row in summary["sample_simulation"]
        ],
    )
    national = summary["national_harvest"]
    text = f"""# Tiled 30-m WS4 119 Mm3 chain

**Artifact:** `{ARTIFACT}` **Version:** {ARTIFACT_VERSION}

**Status:** pass. The 3,345 built and validated tiled WS4 datasets were
scanned. Frozen chain v5.1, the national 990-m dataset, the tiled datasets and
official OSeCAN are unchanged.

**Labels:** `model_estimate=true`, `observed=false`, `policy_grade=false`,
`national_claim=false`, `ws4_derived=true`, `tiled_30m=true`,
`official_osecan_changed=false`, `chain_v51_unchanged=true`.

## 1. Coverage

- Candidates in the upstream manifest: **{coverage['candidates_total']}**
- Built and validated tile datasets scanned: **{coverage['tiles_scanned']}**
- Explicit zero-harvest skips: **{coverage['zero_harvest_skips']}**
- Explicit no-forest skips: **{coverage['no_forest_skips']}**
- Provinces with tile datasets: {', '.join(coverage['provinces_with_tiles'])}
- Provinces without tile datasets: {', '.join(coverage['provinces_without_tiles']) or 'none'}

This is full coverage of the built+validated tiled estate, not a fabricated
dataset for the 217 explicitly skipped candidates. NU has a zero control target
and all 203 NU candidates were skipped upstream as zero-harvest, so no NU
dataset exists to scan.

## 2. Two-pass method

**Pass 1 - candidate scan.** For each tile, only `blocks`, `tracks`,
`features` and `curves` are read. Natural/unmanaged tracks are parsed from
`AU(<tile id>)+IFM(unmanaged)+ORIGIN(natural)`; the province is taken from the
tile slug/manifest and the AU code is checked against the tile id. Each natural
track is joined to its `feature.Yield.*.unmanaged.Total` curve. Block
approximate volume is `area_ha * curve(block offset)`. Blocks with
`offset >= 40` and positive approximate volume are ranked; the top
{PER_TILE_CANDIDATE_LIMIT:,} per tile are written to a candidate Parquet. The
per-province pool is the top {PROVINCE_POOL_LIMIT:,} of those candidates by
approximate volume (or all candidates when smaller), and must exceed the
provincial target by at least {(POOL_MARGIN - 1.0):.0%}.

**Selection.** Whole blocks are selected by a volume-aware greedy in
descending approximate volume. The greedy fills while the next block fits and
then picks the unselected block that minimises overshoot. No block is selected
twice.

**Pass 2 - exact volume.** For the largest
{EXACT_CANDIDATE_LIMIT:,} provincial candidates by approximate volume (and
further 50,000-block batches if needed), `fragments` are read and block volume
is `sum(fragment area * curve(fragment age))` at the exact integer fragment
age. Fragment area must equal block area within {AREA_TOLERANCE_HA:g} ha and
every fragment must belong to the block's natural track. After exact volumes
are known, the greedy is re-run on exact volumes so the final schedule is a
whole-block subset close to the provincial target.

## 3. Exact harvest by province

{harvest_table}

National target is **{float(national['target_m3']) / 1e6:.6f} Mm3**;
national exact harvest is **{float(national['achieved_m3']) / 1e6:.6f} Mm3**
(delta {float(national['delta_m3']):.3f} m3, {float(national['delta_m3']) / 1e6:.6f}
Mm3). Treated area is **{float(national['area_ha']):,.2f} ha** across
**{national['block_count']:,}** whole blocks in **{national['tile_count']:,}**
tiles.

## 4. Chain v5.1 comparison

{comparison_table}

The `ws4_achieved` basis applies the frozen v5.1 per-province coefficients to
the exact tiled harvest. The `scaled_to_119m_control` basis uses one national
factor `{float(summary['scale_factor']):.12f}` preserving tiled provincial
shares and matching the 118.9773391 Mm3 control total.

## 5. WS4 sample simulation

{sample_table}

Each sampled tile was independently validated and simulated for one year with
`CC` at offset 0. Simulated harvested volume equals exact selected block volume
and simulated treated area equals exact selected fragment area within
1e-9 relative tolerance.

## 6. OSeCAN

{summary['osecan']['status_text']}

## 7. Caveats

- The schedule is a whole-block arithmetic verification, not a sustainable
  harvest, AAC, policy result, or investment case.
- Pass 1 uses the block-offset curve value; pass 2 uses exact fragment ages.
  The exact/approximate ratio is reported per province.
- The candidate pool is a documented top-per-tile volume screen, not a claim
  that smaller blocks are inoperable.
- 30-m forest-area fragments and group RSYC curves are inventory
  realisations, not operable or tenure-feasible areas.
- One `CC` treatment at offset 0 is simulated on the sample tiles. Planting
  response, silviculture, fertilization, and genetic gain are not modelled.
- Product and residue values apply frozen v5.1 coefficients by origin
  province. Facility assignment, mill capacity, transport, and
  interprovincial trade are not re-run.
- The scaled basis is one national factor preserving tiled shares, not
  per-province target scaling.
- 0.60 harvest recovery is a reference case, not a calibrated recovery factor.
- No official OSeCAN file, frozen chain v5.1 artifact, national 990-m dataset,
  or tiled dataset is changed.

## 8. Reproduce

```bash
.venv/bin/python -m {MODULE_PATH} --workers {DEFAULT_WORKERS}
```
"""
    path.write_text(text, encoding="utf-8")


def build(
    *,
    workers: int = DEFAULT_WORKERS,
    provinces: Sequence[str] | None = None,
    skip_simulation: bool = False,
    reuse_candidates: bool = False,
) -> dict[str, Any]:
    assert_no_unexpected_deletions()
    targets = chain.read_targets(CONTROL_CSV)
    if provinces is not None:
        requested = {value.strip().upper() for value in provinces}
        if not requested.issubset(set(PROVINCES)):
            raise Ws4TiledChainError(f"unknown province filter: {sorted(requested)}")
    else:
        requested = set(PROVINCES)

    payload = json.loads(TILED_MANIFEST.read_text(encoding="utf-8"))
    tiles = load_manifest_tiles()
    tiles = tiles[tiles["province"].isin(requested)].reset_index(drop=True)
    if tiles.empty:
        raise Ws4TiledChainError("no tiled datasets selected")
    coverage = _coverage(payload, load_manifest_tiles())

    frozen_before = {
        "chain_v51": _tree_state(CHAIN_V51_DIR),
    }
    tiled_manifest_before = _sha256(TILED_MANIFEST)
    national_990m_before = (
        _sha256(NATIONAL_990M_MANIFEST) if NATIONAL_990M_MANIFEST.is_file() else ""
    )

    candidate_dir = WORK_DIR / "tile_candidates"
    candidate_dir.mkdir(parents=True, exist_ok=True)
    stats_path = WORK_DIR / "tile_scan_stats.parquet"
    if reuse_candidates and stats_path.is_file():
        tile_stats = pd.read_parquet(stats_path)
        expected_tiles = set(tiles["tile_slug"])
        if set(tile_stats["tile_slug"]) != expected_tiles:
            raise Ws4TiledChainError(
                "cached candidate scan does not match the selected tile set"
            )
    else:
        tasks = [
            {
                "tile_slug": row.tile_slug,
                "province": row.province,
                "au_code": row.au_code,
                "dataset_dir": row.dataset_dir,
                "candidate_dir": str(candidate_dir),
                "per_tile_limit": PER_TILE_CANDIDATE_LIMIT,
            }
            for row in tiles.itertuples(index=False)
        ]
        stats_rows: list[dict[str, Any]] = []
        with ProcessPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = {pool.submit(_scan_tile_worker, task): task for task in tasks}
            for future in as_completed(futures):
                task = futures[future]
                try:
                    stats_rows.append(future.result())
                except Exception as exc:
                    raise Ws4TiledChainError(
                        f"candidate scan failed for {task['tile_slug']}: {exc}"
                    ) from exc
        tile_stats = pd.DataFrame(stats_rows).sort_values(
            ["province", "tile_slug"]
        ).reset_index(drop=True)
        tile_stats.to_parquet(stats_path, index=False)

    # Per-province candidate pools.
    pools: dict[str, pd.DataFrame] = {}
    pool_stats: dict[str, dict[str, Any]] = {}
    for province in PROVINCES:
        if province not in requested:
            pools[province] = pd.DataFrame()
            pool_stats[province] = {"count": 0, "volume_m3": 0.0, "margin": 0.0}
            continue
        target = float(targets[province])
        pool = _merge_candidates(province, tile_stats, limit=PROVINCE_POOL_LIMIT)
        volume = float(pool["approx_volume_m3"].sum()) if len(pool) else 0.0
        if target > 0.0 and volume < target * POOL_MARGIN:
            all_candidates = _merge_candidates(province, tile_stats, limit=None)
            all_volume = (
                float(all_candidates["approx_volume_m3"].sum())
                if len(all_candidates)
                else 0.0
            )
            if all_volume < target * POOL_MARGIN:
                raise Ws4TiledChainError(
                    f"{province}: candidate pool volume {all_volume!r} m3 is "
                    f"below target {target!r} m3 x {POOL_MARGIN}; "
                    "top-20k-per-tile screen is insufficient"
                )
            pool, volume = all_candidates, all_volume
        if target > 0.0 and pool.empty:
            raise Ws4TiledChainError(f"{province}: no eligible candidate blocks")
        margin = volume / target if target > 0.0 else 0.0
        pools[province] = pool
        pool_stats[province] = {
            "count": len(pool),
            "volume_m3": volume,
            "margin": margin,
        }
        if len(pool):
            pool_dir = WORK_DIR / "province_pools"
            pool_dir.mkdir(parents=True, exist_ok=True)
            pool.to_parquet(pool_dir / f"{province}.parquet", index=False)

    selection_by_province: dict[str, pd.DataFrame] = {}
    for province in PROVINCES:
        if province not in requested:
            selection_by_province[province] = pd.DataFrame()
            continue
        selected, _computed = _select_province(
            province,
            pools[province],
            float(targets[province]),
            tiles,
            workers=workers,
        )
        selection_by_province[province] = selected

    selected_all = pd.concat(
        [frame for frame in selection_by_province.values() if not frame.empty],
        ignore_index=True,
    )
    if selected_all.empty:
        raise Ws4TiledChainError("no blocks selected")
    if selected_all.duplicated(["tile_slug", "block_id"]).any():
        raise Ws4TiledChainError("a block was selected twice")

    harvest_rows = _harvest_rows(
        selection_by_province, tile_stats, pool_stats, targets
    )

    frozen = chain._frozen_province_rows(CHAIN_V51_CSV)
    coefficient_rows, coefficients = chain._coefficient_rows(frozen)
    frozen_totals = chain._frozen_national_totals(frozen)
    chain._check_frozen_targets(frozen_totals)

    achieved_by_province = {
        row["province"]: float(row["achieved_m3"]) for row in harvest_rows
    }
    achieved_total = sum(achieved_by_province.values())
    if achieved_total <= 0.0:
        raise Ws4TiledChainError("achieved harvest is not positive")
    scale_factor = CONTROL_TOTAL_M3 / achieved_total
    scaled_by_province = {
        province: achieved_by_province[province] * scale_factor
        for province in PROVINCES
    }
    achieved_rows, achieved_national = chain.build_chain_rows(
        achieved_by_province,
        "ws4_achieved",
        frozen,
        coefficients,
        scale_factor=1.0,
    )
    scaled_rows, scaled_national = chain.build_chain_rows(
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
    comparison = _comparison_rows_with_labels(national, frozen_totals)

    sample_rows = (
        []
        if skip_simulation
        else _sample_simulations(selection_by_province, tiles, work_dir=WORK_DIR)
    )

    # Write chain data before the OSeCAN step.
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    harvest_fields = [
        "province",
        "target_m3",
        "achieved_m3",
        "delta",
        "overshoot_pct",
        "area_ha",
        "block_count",
        "tile_count",
        "mean_age",
        "curve_count",
        "approx_volume_m3",
        "exact_vs_approx_ratio",
        "eligible_block_count",
        "eligible_volume_m3",
        "candidate_pool_count",
        "candidate_pool_volume_m3",
        "pool_margin",
        "status",
        *LABELS.keys(),
    ]
    _write_csv(OUTPUT_DIR / "ws4_tiled_harvest.csv", harvest_fields, harvest_rows)
    coefficient_fields = [
        "province",
        "reference_harvest_m3",
        *[f"{metric}_per_m3" for metric in COEFFICIENT_METRICS],
        "coefficient_tier",
        "coefficient_source",
        *LABELS.keys(),
    ]
    for row in coefficient_rows:
        row.update(ROW_LABELS)
    _write_csv(
        OUTPUT_DIR / "ws4_tiled_coefficients.csv",
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
        row.update(ROW_LABELS)
    _write_csv(OUTPUT_DIR / "ws4_tiled_product_chain.csv", chain_fields, chain_rows)
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
    _write_csv(
        OUTPUT_DIR / "ws4_tiled_vs_chain_v51_comparison.csv",
        comparison_fields,
        comparison,
    )

    selected_fields = [
        "province",
        "tile_slug",
        "au_code",
        "block_id",
        "track_id",
        "offset",
        "curve_id",
        "area_ha",
        "approx_volume_m3",
        "exact_volume_m3",
        "fragment_area_ha",
        "fragment_count",
        "mean_fragment_age",
        "min_fragment_age",
        "max_fragment_age",
        *LABELS.keys(),
    ]
    selected_rows = selected_all.copy()
    for field, value in ROW_LABELS.items():
        selected_rows[field] = value
    _write_csv(
        OUTPUT_DIR / "ws4_tiled_selected_blocks.csv",
        selected_fields,
        selected_rows.to_dict(orient="records"),
    )

    # OSeCAN handoff is built from the chain totals written above.
    from workflows.national.facilities import ws4_tiled_osecan_handoff as osecan_builder

    osecan_result = osecan_builder.build(
        chain_dir=OUTPUT_DIR,
        output_dir=OUTPUT_DIR / "osecan",
    )

    checks = _validation_checks(
        harvest_rows, selected_all, comparison, sample_rows, osecan_result
    )
    failed = [check for check in checks if not check["passed"]]
    if failed:
        raise Ws4TiledChainError(
            "validation checks failed: "
            + ", ".join(check["check"] for check in failed)
        )

    frozen_after = {
        "chain_v51": _tree_state(CHAIN_V51_DIR),
    }
    _check_frozen_integrity(frozen_before, frozen_after)
    tiled_manifest_after = _sha256(TILED_MANIFEST)
    if tiled_manifest_after != tiled_manifest_before:
        raise Ws4TiledChainError("tiled manifest changed during the build")
    national_990m_after = (
        _sha256(NATIONAL_990M_MANIFEST) if NATIONAL_990M_MANIFEST.is_file() else ""
    )
    if national_990m_after != national_990m_before:
        raise Ws4TiledChainError("national 990-m manifest changed during the build")
    d_status = assert_no_unexpected_deletions()

    generated = datetime.now(UTC).isoformat(timespec="seconds")
    national_harvest = {
        "target_m3": sum(float(row["target_m3"]) for row in harvest_rows),
        "achieved_m3": achieved_total,
        "delta_m3": achieved_total
        - sum(float(row["target_m3"]) for row in harvest_rows),
        "area_ha": sum(float(row["area_ha"]) for row in harvest_rows),
        "block_count": len(selected_all),
        "tile_count": int(selected_all["tile_slug"].nunique()),
    }
    inputs = {
        _relative(CONTROL_CSV): _sha256(CONTROL_CSV),
        _relative(CHAIN_V51_CSV): _sha256(CHAIN_V51_CSV),
        _relative(CHAIN_V51_COEFFICIENTS): _sha256(CHAIN_V51_COEFFICIENTS),
        _relative(TILED_MANIFEST): _sha256(TILED_MANIFEST),
    }
    if national_990m_before:
        inputs[_relative(NATIONAL_990M_MANIFEST)] = national_990m_before
    summary: dict[str, Any] = {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": generated,
        "status": "pass",
        "title": ARTIFACT_TITLE,
        "labels": LABELS,
        "inputs": inputs,
        "coverage": coverage,
        "scan": {
            "per_tile_candidate_limit": PER_TILE_CANDIDATE_LIMIT,
            "province_pool_limit": PROVINCE_POOL_LIMIT,
            "pool_margin_required": POOL_MARGIN,
            "tiles_scanned": len(tile_stats),
            "blocks_read": int(tile_stats["blocks_total"].sum()),
            "eligible_blocks": int(tile_stats["eligible_blocks"].sum()),
            "eligible_volume_approx_m3": float(
                tile_stats["eligible_volume_m3"].sum()
            ),
            "candidate_blocks_written": int(
                tile_stats["candidate_blocks"].sum()
            ),
            "candidate_volume_approx_m3": float(
                tile_stats["candidate_volume_m3"].sum()
            ),
            "zero_approx_blocks": int(tile_stats["zero_approx_blocks"].sum()),
        },
        "province_pools": [
            {
                "province": province,
                **pool_stats[province],
                "target_m3": float(targets[province]),
            }
            for province in PROVINCES
        ],
        "scale_factor": scale_factor,
        "province_harvest": harvest_rows,
        "national_harvest": national_harvest,
        "product_chain": {
            "lhv_gj_per_odt": LHV_GJ_PER_ODT,
            "bases": list(BASES),
            "coefficient_source": _relative(CHAIN_V51_CSV),
            "national_totals": national,
        },
        "comparison": {
            "reference": "national_product_chain_v5 (frozen v5.1)",
            "rows": comparison,
            "frozen_national_totals": frozen_totals,
        },
        "sample_simulation": sample_rows,
        "osecan": osecan_result,
        "validation": {"checks": checks},
        "d_status": d_status,
        "reproduce": f".venv/bin/python -m {MODULE_PATH}",
    }
    _write_json(OUTPUT_DIR / "summary.json", summary)

    province_pool_rows = [
        {
            "province": row["province"],
            "target_m3": row["target_m3"],
            "pool_count": row["count"],
            "pool_volume_m3": row["volume_m3"],
            "pool_margin": row["margin"],
            **ROW_LABELS,
        }
        for row in summary["province_pools"]
    ]
    _write_csv(
        OUTPUT_DIR / "ws4_tiled_candidate_pool.csv",
        [
            "province",
            "target_m3",
            "pool_count",
            "pool_volume_m3",
            "pool_margin",
            *LABELS.keys(),
        ],
        province_pool_rows,
    )
    tile_stats_fields = [
        "tile_slug",
        "province",
        "au_code",
        "blocks_total",
        "eligible_blocks",
        "eligible_volume_m3",
        "zero_approx_blocks",
        "candidate_blocks",
        "candidate_volume_m3",
        "max_approx_volume_m3",
        "mean_approx_volume_m3",
        "natural_tracks",
        "curve_count",
        *LABELS.keys(),
    ]
    tile_scan_rows = tile_stats.copy()
    for field, value in ROW_LABELS.items():
        tile_scan_rows[field] = value
    _write_csv(
        OUTPUT_DIR / "ws4_tiled_tile_scan_summary.csv",
        tile_stats_fields,
        tile_scan_rows.to_dict(orient="records"),
    )
    if sample_rows:
        _write_csv(
            OUTPUT_DIR / "ws4_tiled_sample_simulation.csv",
            [
                "tile_slug",
                "province",
                "block_count",
                "exact_volume_m3",
                "simulated_harvest_m3",
                "volume_delta_m3",
                "exact_area_ha",
                "simulated_treated_ha",
                "area_delta_ha",
                "years",
                "validate",
                "simulation",
                *LABELS.keys(),
            ],
            sample_rows,
        )

    _write_report(OUTPUT_DIR / REPORT_NAME, summary, harvest_rows, comparison)

    manifest_outputs = {
        path.name: {"bytes": path.stat().st_size, "sha256": _sha256(path)}
        for path in sorted(OUTPUT_DIR.iterdir())
        if path.is_file() and path.name != "manifest.json"
    }
    osecan_dir = OUTPUT_DIR / "osecan"
    osecan_outputs = {
        path.name: {"bytes": path.stat().st_size, "sha256": _sha256(path)}
        for path in sorted(osecan_dir.iterdir())
        if path.is_file()
    }
    manifest = {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": generated,
        "title": ARTIFACT_TITLE,
        "labels": LABELS,
        "inputs": inputs,
        "outputs": manifest_outputs,
        "osecan": {
            "root": _relative(osecan_dir),
            "outputs": osecan_outputs,
            "primary_7_of_7": osecan_result["primary_7_of_7"],
            "secondary_7_of_7": osecan_result["secondary_7_of_7"],
            "frozen_21_of_21": osecan_result["frozen_21_of_21"],
            "official_unchanged": osecan_result["official_unchanged"],
        },
        "coverage": coverage,
        "validation": {"checks": checks},
        "d_status": d_status,
        "reproduce": f".venv/bin/python -m {MODULE_PATH}",
    }
    _write_json(OUTPUT_DIR / "manifest.json", manifest)
    return summary


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--provinces", nargs="*", default=None)
    parser.add_argument("--skip-simulation", action="store_true")
    parser.add_argument("--reuse-candidates", action="store_true")
    args = parser.parse_args()
    summary = build(
        workers=args.workers,
        provinces=args.provinces,
        skip_simulation=args.skip_simulation,
        reuse_candidates=args.reuse_candidates,
    )
    print(
        json.dumps(
            {
                "status": summary["status"],
                "tiles_scanned": summary["scan"]["tiles_scanned"],
                "blocks_selected": summary["national_harvest"]["block_count"],
                "harvest_m3": summary["national_harvest"]["achieved_m3"],
                "output": _relative(OUTPUT_DIR),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
