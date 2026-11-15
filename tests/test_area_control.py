"""Tests for the minimal WS4 TSA area-control heuristic."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest
import yaml

from workflows.ws4_instances.area_control import (
    AreaControlError,
    run_area_control,
)


def _yield_curve(curve_id: int, rotation_age: int, peak_mai: float) -> pd.DataFrame:
    """A smooth curve whose MAI peaks exactly at ``rotation_age``."""
    ages = np.arange(0, 301)
    mai = peak_mai * (ages / rotation_age) * np.exp(1.0 - ages / rotation_age)
    return pd.DataFrame(
        {"curve_id": curve_id, "x": ages.astype(float), "y": ages * mai}
    )


def _base_tables() -> dict[str, pd.DataFrame]:
    curves = pd.concat(
        [
            _yield_curve(1, rotation_age=80, peak_mai=2.0),
            _yield_curve(2, rotation_age=120, peak_mai=3.0),
        ],
        ignore_index=True,
    )
    tracks = pd.DataFrame(
        {
            "track_id": [1, 2, 3],
            "name": [
                "AU(A1)+IFM(managed)+ORIGIN(natural)",
                "AU(A2)+IFM(managed)+ORIGIN(natural)",
                "AU(A1)+IFM(unmanaged)+ORIGIN(natural)",
            ],
        }
    )
    treatments = pd.DataFrame(
        {
            "track_id": [1, 2],
            "begin": [80, 120],
            "end": [1000, 1000],
            "treatment": ["CC", "CC"],
            "new_track": [1, 2],
            "offset": [0, 0],
            "retain": [0, 0],
        }
    )
    features = pd.DataFrame(
        {
            "track_id": [1, 2],
            "label": ["feature.Yield.managed.Total", "feature.Yield.managed.Total"],
            "curve_id": [1, 2],
        }
    )
    blocks = pd.DataFrame(
        {
            "block_id": [1, 2, 3, 4, 5, 6, 7, 8],
            "track_id": [1, 1, 1, 1, 2, 2, 2, 2],
            "offset": [100, 90, 70, 60, 130, 110, 100, 80],
            "area_ha": [40.0, 30.0, 20.0, 10.0, 50.0, 30.0, 20.0, 10.0],
        }
    )
    fragments = pd.DataFrame(
        {
            "fragment_id": [1, 2, 3, 4, 5, 6, 7, 8],
            "block_id": [1, 2, 3, 4, 5, 6, 7, 8],
            "area_ha": [40.0, 30.0, 20.0, 10.0, 50.0, 30.0, 20.0, 10.0],
            "age": [100, 90, 70, 60, 130, 110, 100, 80],
            "track_id": [1, 1, 1, 1, 2, 2, 2, 2],
        }
    )
    return {
        "curves": curves,
        "tracks": tracks,
        "treatments": treatments,
        "features": features,
        "blocks": blocks,
        "fragments": fragments,
    }


def _write_instance(
    tmp_path: Path,
    *,
    quota_scope: str = "per_au",
    eligibility: str = "operable",
    tables: dict[str, pd.DataFrame] | None = None,
) -> tuple[Path, Path]:
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    for name, frame in (tables or _base_tables()).items():
        frame.to_parquet(dataset / f"{name}.parquet", index=False)
    config = {
        "au_regex": r"AU\(([^)]+)\)",
        "volume_label": "feature.Yield.managed.Total",
        "treatment": "CC",
        "managed_token": "IFM(managed)",
        "period_width": 10,
        "horizon_years": 100,
        "quota_factor": 1.0,
        "min_age": None,
        "quota_scope": quota_scope,
        "eligibility": eligibility,
    }
    config_path = tmp_path / "area_control.yaml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return dataset, config_path


def _run(tmp_path: Path, **kwargs: Any) -> tuple[Path, dict[str, Any]]:
    dataset, config_path = _write_instance(tmp_path, **kwargs)
    out = tmp_path / "out"
    summary = run_area_control(dataset, config_path, out, periods=6)
    return out, summary


def test_au_metrics_oldest_first_quota_and_window(tmp_path: Path) -> None:
    out, summary = _run(tmp_path, quota_scope="per_au")
    diagnostics = pd.read_csv(out / "diagnostics.csv")
    schedule = pd.read_csv(out / "schedule.csv")

    assert summary["au_count"] == 2
    assert summary["total_managed_area_ha"] == pytest.approx(210.0)
    assert summary["weighted_rotation_age_years"] == pytest.approx(100.95238095)
    assert summary["total_quota_ha_per_period"] == pytest.approx(21.66666667)

    metrics = {row["au"]: row for row in summary["per_au"]}
    assert metrics["A1"]["A_ha"] == pytest.approx(100.0)
    assert metrics["A1"]["R_years"] == 80
    assert metrics["A1"]["Q_ha"] == pytest.approx(12.5)
    assert metrics["A1"]["MAI_R"] == pytest.approx(2.0)
    assert metrics["A2"]["A_ha"] == pytest.approx(110.0)
    assert metrics["A2"]["R_years"] == 120
    assert metrics["A2"]["Q_ha"] == pytest.approx(110 * 10 / 120)
    assert metrics["A2"]["MAI_R"] == pytest.approx(3.0)

    # AU1: the oldest block fills the whole quota, so the next block waits.
    first = schedule[schedule["block_id"] == 1].iloc[0]
    assert (first["treatment"], first["offset"]) == ("CC", 0)
    au1 = diagnostics[diagnostics["au"] == "A1"].set_index("period")
    assert au1.loc[1, "n_blocks"] == 1
    assert au1.loc[1, "selected_area_ha"] == pytest.approx(40.0)
    assert au1.loc[1, "n_eligible"] == 2

    # Oldest-first order and earliest in-window offsets.
    assert schedule[schedule["block_id"] == 2].iloc[0]["offset"] == 10
    assert schedule[schedule["block_id"] == 3].iloc[0]["offset"] == 20
    assert schedule[schedule["block_id"] == 4].iloc[0]["offset"] == 30

    # Every row falls inside its period window.
    merged = schedule.merge(
        pd.DataFrame(
            {
                "block_id": [1, 2, 3, 4, 5, 6, 7, 8],
                "period": [1, 2, 3, 4, 1, 2, 3, 5],
            }
        ),
        on="block_id",
    )
    for row in merged.itertuples():
        assert (row.period - 1) * 10 <= row.offset <= row.period * 10 - 1


def test_shortfall_carry_and_no_under_age(tmp_path: Path) -> None:
    out, _ = _run(tmp_path, quota_scope="per_au")
    diagnostics = pd.read_csv(out / "diagnostics.csv")
    schedule = pd.read_csv(out / "schedule.csv")

    au2 = diagnostics[diagnostics["au"] == "A2"].set_index("period")
    assert au2.loc[4, "selected_area_ha"] == pytest.approx(0.0)
    assert au2.loc[4, "shortfall_ha"] == pytest.approx(110 * 10 / 120)
    assert au2.loc[5, "target_ha"] == pytest.approx(
        2 * (110 * 10 / 120), rel=1e-9
    )
    assert au2.loc[5, "selected_area_ha"] == pytest.approx(10.0)

    blocks = _base_tables()["blocks"].set_index("block_id")
    for row in schedule.itertuples():
        harvest_age = int(blocks.loc[row.block_id, "offset"] + row.offset)
        begin = 80 if row.block_id <= 4 else 120
        assert harvest_age >= begin


def test_global_scope_uses_one_landbase_quota(tmp_path: Path) -> None:
    out, summary = _run(tmp_path, quota_scope="global")
    schedule = pd.read_csv(out / "schedule.csv")
    diagnostics = pd.read_csv(out / "diagnostics.csv")

    # The oldest block on the whole land base is A2's 130-year-old stand.
    first_period = schedule[schedule["offset"] == 0]
    assert set(first_period["block_id"]) == {5}
    assert summary["total_quota_ha_per_period"] == pytest.approx(
        210.0 * 10 / summary["landbase_rotation_age_years"]
    )
    assert summary["landbase_rotation_age_years"] != 80
    assert diagnostics[diagnostics["period"] == 1]["selected_area_ha"].sum() == pytest.approx(50.0)


def test_earliest_only_drops_blocks_that_miss_their_first_window(tmp_path: Path) -> None:
    out, _ = _run(tmp_path, quota_scope="per_au", eligibility="earliest_only")
    schedule = pd.read_csv(out / "schedule.csv")
    # Block 2 has y=0.  Period 1 fills its AU quota with block 1, so block 2
    # is no longer eligible in a later period under this reading.
    assert 2 not in set(schedule["block_id"])


def test_a_block_spanning_two_aus_fails(tmp_path: Path) -> None:
    tables = _base_tables()
    extra_block = pd.DataFrame(
        {"block_id": [9], "track_id": [1], "offset": [90], "area_ha": [5.0]}
    )
    extra_fragments = pd.DataFrame(
        {
            "fragment_id": [9, 10],
            "block_id": [9, 9],
            "area_ha": [2.5, 2.5],
            "age": [90, 90],
            "track_id": [1, 2],
        }
    )
    tables["blocks"] = pd.concat([tables["blocks"], extra_block], ignore_index=True)
    tables["fragments"] = pd.concat(
        [tables["fragments"], extra_fragments], ignore_index=True
    )
    dataset, config_path = _write_instance(tmp_path, tables=tables)
    with pytest.raises(AreaControlError, match="more than one AU"):
        run_area_control(dataset, config_path, tmp_path / "out", periods=2)


def test_period_one_priming_is_recorded(tmp_path: Path) -> None:
    tables = _base_tables()
    tables["treatments"].loc[:, "begin"] = 200
    dataset, config_path = _write_instance(
        tmp_path, quota_scope="global", tables=tables
    )
    out = tmp_path / "out"
    summary = run_area_control(dataset, config_path, out, periods=2)
    schedules = pd.read_csv(out / "schedule.csv")
    diagnostics = pd.read_csv(out / "diagnostics.csv")

    assert any("primed" in warning for warning in summary["warnings"])
    assert (diagnostics["primed"] == True).any()  # noqa: E712
    # Priming still writes a legal offset and the oldest block is chosen.
    assert schedules.iloc[0]["block_id"] == 5
    assert schedules.iloc[0]["offset"] == 0
    assert json.loads((out / "summary.json").read_text())["schedule_rows"] == len(schedules)
