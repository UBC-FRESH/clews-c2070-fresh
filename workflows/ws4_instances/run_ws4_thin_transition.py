#!/usr/bin/env python3
"""Schedule and verify a THIN -> CC transition on a WS4 dataset.

The schedule selects whole blocks whose inventory age class lies in the THIN
window (20-40 years), applies ``THIN`` at offset 0 (the negative-offset
partial-cut sentinel retains the current age), and applies ``CC`` on the same
blocks at offset 10.  The run is verified from the WS4 account and product
values:

* THIN produces harvested volume and treated area;
* the fragments are on the thinned track at offset 0 with their pre-treatment
  age, not age zero;
* the later CC harvest is evaluated at age + 10, proving the age was retained;
* the same area moves to the managed track and the final managed value is
  positive.

No thinning or planting yield response is claimed: all three tracks share the
same RSYC curve.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = REPO_ROOT / "data"
NATIONAL_DATASET = (
    DATA_ROOT / "derived/ws4/scanfi-national-990m/dataset"
)
DEFAULT_OUT_DIR = DATA_ROOT / "derived/ws4/thin-transition-national-990m"
WS4_BIN = REPO_ROOT / ".venv-ws4/bin/ws4"

THIN_TREATMENT = "THIN"
THIN_BEGIN = 20
THIN_END = 40
THIN_OFFSET = -1
CC_TREATMENT = "CC"
CC_OFFSET = 10
DEFAULT_YEARS = 20
DEFAULT_MAX_BLOCKS = 40


class ThinTransitionError(RuntimeError):
    """Raised when the THIN -> CC evidence does not close."""


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _safe_reset(out_dir: Path) -> None:
    if not out_dir.exists():
        return
    resolved = out_dir.resolve()
    if not resolved.is_relative_to((DATA_ROOT / "derived/ws4").resolve()):
        raise ThinTransitionError(f"refusing to reset unexpected path: {resolved}")
    shutil.rmtree(resolved)


def _natural_track_province(track_name: str) -> str:
    prefix = str(track_name).split(")", 1)[0]
    if not prefix.startswith("AU("):
        raise ThinTransitionError(f"unparseable track name: {track_name!r}")
    province = prefix.removeprefix("AU(")
    if not province:
        raise ThinTransitionError(f"empty AU code in track name: {track_name!r}")
    return province


def select_transition_blocks(
    dataset_dir: Path, *, max_blocks: int = DEFAULT_MAX_BLOCKS
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Select deterministic, province-spread blocks in the THIN age window."""
    if max_blocks <= 0:
        raise ThinTransitionError("max_blocks must be positive")
    tracks = pd.read_parquet(dataset_dir / "tracks.parquet")
    natural = tracks[
        tracks["name"].str.contains(
            r"\+IFM\(unmanaged\)\+ORIGIN\(natural\)\+SILV_STATE\(baseline\)",
            regex=True,
        )
    ].copy()
    if natural.empty:
        raise ThinTransitionError("dataset has no natural/baseline tracks")
    natural["province"] = natural["name"].map(_natural_track_province)

    blocks = pd.read_parquet(
        dataset_dir / "blocks.parquet",
        columns=["block_id", "track_id", "offset", "area_ha"],
    )
    eligible = blocks[
        blocks["offset"].between(THIN_BEGIN, THIN_END, inclusive="both")
    ].copy()
    eligible = eligible.merge(
        natural[["track_id", "province"]],
        on="track_id",
        how="inner",
        validate="many_to_one",
    )
    if eligible.empty:
        raise ThinTransitionError("no blocks lie in the THIN age window")
    eligible = eligible.sort_values(["province", "block_id"], kind="stable")

    # One block per province first, then stride the remainder deterministically.
    chosen_ids: list[int] = []
    chosen_rows: list[int] = []
    for _province, group in eligible.groupby("province", sort=True):
        row = group.iloc[len(group) // 2]
        chosen_ids.append(int(row["block_id"]))
        chosen_rows.append(int(row.name))
    remaining = eligible.drop(index=chosen_rows)
    slots = max_blocks - len(chosen_ids)
    if slots > 0 and not remaining.empty:
        stride = max(1, len(remaining) // slots)
        chosen_ids.extend(
            int(value)
            for value in remaining.iloc[::stride].head(slots)["block_id"]
        )
    selected = eligible[eligible["block_id"].isin(chosen_ids)].drop_duplicates(
        "block_id"
    )
    selected = selected.sort_values("block_id", kind="stable").reset_index(drop=True)
    if selected.empty:
        raise ThinTransitionError("transition selection is empty")
    if len(selected) > max_blocks:
        selected = selected.head(max_blocks)
    return selected, natural


def _curve_lookup(curves: pd.DataFrame) -> dict[int, pd.Series]:
    lookup: dict[int, pd.Series] = {}
    for curve_id, group in curves.groupby("curve_id", sort=True):
        lookup[int(curve_id)] = group.set_index("x")["y"].sort_index()
    return lookup


def expected_transition_values(
    dataset_dir: Path, selected_blocks: pd.DataFrame
) -> dict[str, float]:
    """Return pre-treatment THIN and age+10 CC volumes for the selected blocks."""
    blocks = pd.read_parquet(
        dataset_dir / "blocks.parquet",
        columns=["block_id", "track_id", "offset", "area_ha"],
    )
    tracks = pd.read_parquet(dataset_dir / "tracks.parquet")
    tracks["province"] = tracks["name"].map(_natural_track_province)
    features = pd.read_parquet(
        dataset_dir / "features.parquet",
        columns=["track_id", "label", "curve_id"],
    )
    natural = tracks[
        tracks["name"].str.contains(
            r"\+IFM\(unmanaged\)\+ORIGIN\(natural\)\+SILV_STATE\(baseline\)",
            regex=True,
        )
    ]
    natural_features = features[
        features["label"].str.endswith(".unmanaged.Total")
    ].merge(
        natural[["track_id"]],
        on="track_id",
        how="inner",
        validate="one_to_one",
    )
    curve_by_track = dict(
        zip(natural_features["track_id"], natural_features["curve_id"], strict=True)
    )
    curves = _curve_lookup(
        pd.read_parquet(dataset_dir / "curves.parquet", columns=["curve_id", "x", "y"])
    )
    selected = blocks.merge(
        selected_blocks[["block_id"]], on="block_id", how="inner", validate="one_to_one"
    )
    fragments = pd.read_parquet(
        dataset_dir / "fragments.parquet",
        columns=["fragment_id", "block_id", "area_ha", "age", "track_id"],
    ).rename(columns={"track_id": "fragment_track_id"})
    selected_fragments = fragments.merge(
        selected[["block_id", "track_id"]].rename(
            columns={"track_id": "natural_track_id"}
        ),
        on="block_id",
        how="inner",
        validate="many_to_one",
    )
    if not selected_fragments["fragment_track_id"].eq(
        selected_fragments["natural_track_id"]
    ).all():
        raise ThinTransitionError("fragment is not on the block's natural track")
    if not set(selected_fragments["natural_track_id"]).issubset(curve_by_track):
        raise ThinTransitionError("selected block has no natural yield curve")

    thin_volume = 0.0
    cc_volume = 0.0
    for row in selected_fragments.itertuples(index=False):
        curve_id = int(curve_by_track[int(row.natural_track_id)])
        curve = curves[curve_id]
        thin_volume += float(row.area_ha) * float(curve.loc[float(row.age)])
        cc_volume += float(row.area_ha) * float(
            curve.loc[float(row.age) + float(CC_OFFSET)]
        )
    return {
        "expected_thin_volume_m3": thin_volume,
        "expected_cc_volume_m3": cc_volume,
        "scheduled_area_ha": float(selected_fragments["area_ha"].sum()),
        "selected_blocks": len(selected_blocks),
        "selected_fragments": len(selected_fragments),
    }


def run_simulation(
    dataset_dir: Path, schedule_path: Path, run_dir: Path, *, years: int
) -> dict[str, Any]:
    if not WS4_BIN.is_file():
        raise ThinTransitionError(f"ws4 executable is missing: {WS4_BIN}")
    if run_dir.exists():
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True)
    command = [
        str(WS4_BIN),
        "simulate",
        "--dataset",
        str(dataset_dir),
        "--schedule",
        str(schedule_path),
        "--years",
        str(years),
        "--out",
        str(run_dir),
    ]
    result = subprocess.run(
        command, cwd=REPO_ROOT, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise ThinTransitionError(
            "ws4 simulate failed:\n" + result.stdout + result.stderr
        )
    return {
        "command": command,
        "exit_code": int(result.returncode),
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }


def _sum_account(accounts: pd.DataFrame, pattern: str) -> float:
    mask = accounts["account"].str.match(pattern)
    return float(accounts.loc[mask, "value"].sum())


def _sum_product(products: pd.DataFrame, pattern: str) -> float:
    mask = products["account"].str.match(pattern)
    return float(products.loc[mask, "value"].sum())


def verify_transition(
    accounts: pd.DataFrame,
    products: pd.DataFrame,
    *,
    expected: Mapping[str, float],
    years: int,
) -> dict[str, Any]:
    """Fail closed unless THIN retains age and the later CC reaches managed."""
    if accounts["value"].isna().any() or products["value"].isna().any():
        raise ThinTransitionError("simulation wrote NaN account or product values")
    thin_harvest = _sum_product(
        products, r"^product\.HarvestedVolume\.(?:.+\.)?unmanaged\.THIN$"
    )
    thin_treated = _sum_product(products, r"^product\.Treated\.(?:.+\.)?unmanaged\.THIN$")
    thin_cc_harvest = _sum_product(
        products, r"^product\.HarvestedVolume\.(?:.+\.)?thinned\.CC$"
    )
    thin_cc_treated = _sum_product(
        products, r"^product\.Treated\.(?:.+\.)?thinned\.CC$"
    )
    if thin_harvest <= 0.0:
        raise ThinTransitionError("THIN produced no harvested volume")
    if thin_treated <= 0.0:
        raise ThinTransitionError("THIN produced no treated area")
    if thin_cc_harvest <= 0.0:
        raise ThinTransitionError("the THIN->CC transition produced no volume")
    if thin_cc_treated <= 0.0:
        raise ThinTransitionError("the THIN->CC transition moved no area")

    retained = _sum_account(accounts[accounts["offset"] == 0], r"^feature\.Yield\.(?:.+\.)?thinned\.Total$")
    expected_thin = float(expected["expected_thin_volume_m3"])
    if not np.isclose(retained, expected_thin, rtol=1e-9, atol=1e-6):
        raise ThinTransitionError(
            "thinned feature at offset 0 does not equal the pre-treatment "
            f"volume (retained={retained!r}, expected={expected_thin!r}); "
            "the age was reset instead of retained"
        )
    if not np.isclose(
        thin_cc_harvest,
        float(expected["expected_cc_volume_m3"]),
        rtol=1e-9,
        atol=1e-6,
    ):
        raise ThinTransitionError(
            "thinned CC volume does not equal the age+10 curve value "
            f"(achieved={thin_cc_harvest!r}, "
            f"expected={expected['expected_cc_volume_m3']!r})"
        )
    if not np.isclose(
        thin_cc_treated,
        float(expected["scheduled_area_ha"]),
        rtol=1e-9,
        atol=1e-6,
    ):
        raise ThinTransitionError(
            "CC treated area does not equal the scheduled block area"
        )

    final_offset = int(accounts["offset"].max())
    final = accounts[accounts["offset"] == final_offset]
    final_managed = _sum_account(final, r"^feature\.Yield\.(?:.+\.)?managed\.Total$")
    final_thinned = _sum_account(final, r"^feature\.Yield\.(?:.+\.)?thinned\.Total$")
    if final_managed <= 0.0:
        raise ThinTransitionError("final managed account value is not positive")
    if not np.isclose(final_thinned, 0.0, rtol=0.0, atol=1e-9):
        raise ThinTransitionError("final thinned account value is not zero")

    return {
        "thin_harvested_volume_m3": thin_harvest,
        "thin_treated_area_ha": thin_treated,
        "thinned_cc_harvested_volume_m3": thin_cc_harvest,
        "thinned_cc_treated_area_ha": thin_cc_treated,
        "retained_thinned_value_at_offset_0": retained,
        "expected_thin_volume_m3": expected_thin,
        "expected_cc_volume_m3": float(expected["expected_cc_volume_m3"]),
        "scheduled_area_ha": float(expected["scheduled_area_ha"]),
        "final_offset": final_offset,
        "final_managed_value": final_managed,
        "final_thinned_value": final_thinned,
        "years": years,
    }


def run(
    *,
    dataset_dir: Path = NATIONAL_DATASET,
    out_dir: Path = DEFAULT_OUT_DIR,
    max_blocks: int = DEFAULT_MAX_BLOCKS,
    years: int = DEFAULT_YEARS,
) -> dict[str, Any]:
    dataset_dir = dataset_dir.resolve()
    out_dir = out_dir.resolve()
    if years <= CC_OFFSET:
        raise ThinTransitionError("years must be greater than the CC offset")
    if not (dataset_dir / "manifest.json").is_file():
        raise ThinTransitionError(f"dataset is missing: {dataset_dir}")
    for table in ("blocks", "tracks", "features", "products", "treatments"):
        if not (dataset_dir / f"{table}.parquet").is_file():
            raise ThinTransitionError(f"dataset table is missing: {table}.parquet")

    treatments = pd.read_parquet(dataset_dir / "treatments.parquet")
    thin_rows = treatments[treatments["treatment"].eq(THIN_TREATMENT)]
    if thin_rows.empty:
        raise ThinTransitionError("dataset has no THIN treatments")
    if not (
        (thin_rows["offset"] == THIN_OFFSET)
        & (thin_rows["begin"] == THIN_BEGIN)
        & (thin_rows["end"] == THIN_END)
    ).all():
        raise ThinTransitionError("THIN treatment rows do not use the retain sentinel")

    selected, _natural = select_transition_blocks(
        dataset_dir, max_blocks=max_blocks
    )
    schedule = pd.concat(
        [
            pd.DataFrame(
                {
                    "block_id": selected["block_id"].astype("int64"),
                    "treatment": THIN_TREATMENT,
                    "offset": 0,
                }
            ),
            pd.DataFrame(
                {
                    "block_id": selected["block_id"].astype("int64"),
                    "treatment": CC_TREATMENT,
                    "offset": CC_OFFSET,
                }
            ),
        ],
        ignore_index=True,
    ).sort_values(["offset", "block_id"], kind="stable", ignore_index=True)
    if schedule.duplicated(["block_id", "treatment"]).any():
        raise ThinTransitionError("schedule repeats a block/treatment pair")
    expected = expected_transition_values(dataset_dir, selected)

    _safe_reset(out_dir)
    out_dir.mkdir(parents=True)
    schedule_path = out_dir / "schedule_thin_then_cc.csv"
    schedule.to_csv(schedule_path, index=False, lineterminator="\n")
    run_dir = out_dir / "run"
    simulation = run_simulation(
        dataset_dir, schedule_path, run_dir, years=years
    )
    accounts = pd.read_csv(run_dir / "account_values.csv")
    products = pd.read_csv(run_dir / "product_values.csv")
    evidence = verify_transition(
        accounts, products, expected=expected, years=years
    )
    summary = {
        "artifact": "ws4_thin_transition",
        "status": "pass",
        "dataset": _display_path(dataset_dir),
        "schedule": _display_path(schedule_path),
        "run": _display_path(run_dir),
        "summary": _display_path(out_dir / "summary.json"),
        "selection": {
            "thin_window": [THIN_BEGIN, THIN_END],
            "thin_offset": THIN_OFFSET,
            "cc_offset": CC_OFFSET,
            "max_blocks": max_blocks,
            "blocks": len(selected),
            "provinces": sorted(selected["province"].astype(str).unique()),
            "area_ha": float(selected["area_ha"].sum()),
        },
        "expected": dict(expected),
        "evidence": evidence,
        "simulation": simulation,
        "labels": {
            "evidence_label": "model_estimate",
            "observed": False,
            "policy_grade": False,
            "national_claim": False,
            "thinning_yield_response": False,
            "planting_yield_response": False,
            "official_osecan_changed": False,
            "chain_v51_unchanged": True,
        },
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return summary


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=NATIONAL_DATASET)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--max-blocks", type=int, default=DEFAULT_MAX_BLOCKS)
    parser.add_argument("--years", type=int, default=DEFAULT_YEARS)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        summary = run(
            dataset_dir=args.dataset,
            out_dir=args.out,
            max_blocks=args.max_blocks,
            years=args.years,
        )
    except ThinTransitionError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(json.dumps(summary["evidence"], indent=2, sort_keys=True))
    print(f"summary: {summary['summary']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
