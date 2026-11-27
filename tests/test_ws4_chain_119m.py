"""Contract tests for the WS4-driven 119 Mm3 chain comparison."""

from __future__ import annotations

import json
import math
import subprocess

import numpy as np
import pandas as pd
import pytest

from workflows.national.facilities import ws4_chain_119m as chain

REQUIRED = (
    "ws4_119m_harvest.csv",
    "ws4_119m_coefficients.csv",
    "ws4_119m_product_chain.csv",
    "ws4_vs_chain_v51_comparison.csv",
    "summary.json",
    "manifest.json",
    "WS4_CHAIN_119M.md",
)
requires_artifacts = pytest.mark.skipif(
    not all((chain.OUTPUT_DIR / name).exists() for name in REQUIRED),
    reason="WS4 119 Mm3 chain has not been generated",
)


def _schedule() -> pd.DataFrame:
    return pd.read_csv(chain.SCHEDULE_PATH)


@requires_artifacts
def test_schedule_uniqueness_and_eligibility() -> None:
    schedule = _schedule()
    assert list(schedule.columns) == ["block_id", "treatment", "offset"]
    assert len(schedule) == 45
    assert not schedule["block_id"].duplicated().any()
    assert schedule["treatment"].eq("CC").all()
    assert schedule["offset"].eq(0).all()

    blocks = pd.read_parquet(
        chain.DATASET_DIR / "blocks.parquet",
        columns=["block_id", "track_id", "offset"],
    ).rename(columns={"offset": "block_offset"})
    tracks = pd.read_parquet(
        chain.DATASET_DIR / "tracks.parquet", columns=["track_id", "name"]
    )
    merged = schedule.merge(blocks, on="block_id", validate="one_to_one")
    merged = merged.merge(tracks, on="track_id", validate="many_to_one")
    assert (merged["block_offset"] >= chain.AGE_OFFSET_MIN).all()
    assert merged["name"].str.contains(r"IFM\(unmanaged\)").all()
    assert merged["name"].str.contains(r"ORIGIN\(natural\)").all()
    assert "NU" not in merged["name"].str.extract(r"AU\(([A-Z]{2})\)")[0].tolist()


@requires_artifacts
def test_harvest_volume_conservation() -> None:
    schedule = _schedule()
    block_volumes = chain.build_block_volumes().set_index("block_id")
    selected = block_volumes.loc[schedule["block_id"]]
    products = chain.extract_run_products().set_index("province")
    harvest = pd.read_csv(
        chain.OUTPUT_DIR / "ws4_119m_harvest.csv"
    ).set_index("province")

    for province in chain.PROVINCES:
        expected_volume = float(
            selected.loc[
                selected["province"] == province, "volume_m3"
            ].sum()
        )
        expected_area = float(
            selected.loc[
                selected["province"] == province, "area_ha"
            ].sum()
        )
        achieved = float(harvest.loc[province, "achieved_m3"])
        area = float(harvest.loc[province, "area_ha"])
        assert achieved == pytest.approx(expected_volume, rel=1e-9, abs=1e-6)
        assert area == pytest.approx(expected_area, rel=1e-9, abs=1e-6)
        if province in products.index:
            assert achieved == pytest.approx(
                float(products.loc[province, "harvested_m3"]),
                rel=1e-9,
                abs=1e-6,
            )
            assert area == pytest.approx(
                float(products.loc[province, "treated_ha"]),
                rel=1e-9,
                abs=1e-6,
            )


@requires_artifacts
def test_province_target_overshoot_arithmetic() -> None:
    harvest = pd.read_csv(chain.OUTPUT_DIR / "ws4_119m_harvest.csv")
    assert set(harvest["province"]) == set(chain.PROVINCES)
    assert harvest["target_m3"].sum() == pytest.approx(
        chain.CONTROL_TOTAL_M3, rel=1e-12
    )
    for row in harvest.itertuples(index=False):
        expected_delta = row.achieved_m3 - row.target_m3
        assert row.delta == pytest.approx(expected_delta, rel=1e-12, abs=1e-6)
        if row.target_m3 > 0:
            expected_pct = expected_delta / row.target_m3 * 100.0
            assert row.overshoot_pct == pytest.approx(
                expected_pct, rel=1e-12, abs=1e-9
            )
            assert row.achieved_m3 + 1e-6 >= row.target_m3
            assert row.block_count > 0
            assert row.curve_count > 0
        else:
            assert row.province == "NU"
            assert row.achieved_m3 == 0.0
            assert row.area_ha == 0.0
            assert row.block_count == 0
            assert row.curve_count == 0
    assert harvest["achieved_m3"].sum() == pytest.approx(
        json.loads(
            (chain.OUTPUT_DIR / "summary.json").read_text(encoding="utf-8")
        )["national_harvest"]["achieved_m3"],
        rel=1e-12,
    )


@requires_artifacts
def test_coefficient_application_and_scaled_basis() -> None:
    chain_rows = pd.read_csv(
        chain.OUTPUT_DIR / "ws4_119m_product_chain.csv"
    )
    frozen = pd.read_csv(chain.CHAIN_V51_CSV).set_index("province")
    coefficients = pd.read_csv(
        chain.OUTPUT_DIR / "ws4_119m_coefficients.csv"
    ).set_index("province")
    metrics = list(chain.COEFFICIENT_METRICS)
    column_map = {
        "harvest_residue_recoverable_r035_odt": (
            "harvest_residue_recoverable_total_odt_r035"
        ),
        "harvest_residue_recoverable_r060_odt": (
            "harvest_residue_recoverable_total_odt_r060"
        ),
        "harvest_residue_recoverable_r075_odt": (
            "harvest_residue_recoverable_total_odt_r075"
        ),
    }
    for basis in chain.BASES:
        basis_rows = chain_rows[chain_rows["basis"] == basis]
        provinces = basis_rows[basis_rows["geography_level"] == "province"]
        assert set(provinces["province"]) == set(chain.PROVINCES)
        for row in provinces.itertuples(index=False):
            reference = frozen.loc[row.province]
            harvest = reference["harvest_m3"]
            for metric in metrics:
                expected = (
                    row.harvest_m3
                    * coefficients.loc[
                        row.province, f"{metric}_per_m3"
                    ]
                )
                assert getattr(row, metric) == pytest.approx(
                    expected, rel=1e-12, abs=1e-6
                )
                frozen_column = column_map.get(metric, metric)
                expected_coefficient = (
                    0.0
                    if harvest <= 0
                    else float(reference[frozen_column]) / harvest
                )
                assert coefficients.loc[
                    row.province, f"{metric}_per_m3"
                ] == pytest.approx(
                    expected_coefficient, rel=1e-12, abs=1e-15
                )
            assert row.solid_product_m3 == pytest.approx(
                row.sawlog_product_m3 + row.panel_product_m3,
                rel=1e-12,
                abs=1e-6,
            )
            assert row.process_residue_pj == pytest.approx(
                row.process_residue_odt * chain.LHV_GJ_PER_ODT / 1e6,
                rel=1e-12,
            )
            assert row.harvest_residue_gross_pj == pytest.approx(
                row.harvest_residue_gross_odt
                * chain.LHV_GJ_PER_ODT
                / 1e6,
                rel=1e-12,
            )
            for recovery in ("r035", "r060", "r075"):
                odt_field = f"harvest_residue_recoverable_{recovery}_odt"
                pj_field = f"harvest_residue_recoverable_{recovery}_pj"
                assert getattr(row, pj_field) == pytest.approx(
                    getattr(row, odt_field) * chain.LHV_GJ_PER_ODT / 1e6,
                    rel=1e-12,
                )
        national = basis_rows[basis_rows["geography_level"] == "national"]
        assert len(national) == 1
        national_row = national.iloc[0]
        assert national_row["province"] == "CAN"
        for metric in metrics:
            assert national_row[metric] == pytest.approx(
                provinces[metric].sum(), rel=1e-12, abs=1e-6
            )

    achieved = chain_rows[
        (chain_rows["basis"] == "ws4_achieved")
        & (chain_rows["geography_level"] == "province")
    ].set_index("province")
    scaled = chain_rows[
        (chain_rows["basis"] == "scaled_to_119m_control")
        & (chain_rows["geography_level"] == "province")
    ].set_index("province")
    assert scaled["harvest_m3"].sum() == pytest.approx(
        chain.CONTROL_TOTAL_M3, rel=1e-12
    )
    achieved_total = achieved["harvest_m3"].sum()
    for province in chain.PROVINCES:
        assert (
            scaled.loc[province, "harvest_m3"] / chain.CONTROL_TOTAL_M3
            == pytest.approx(
                achieved.loc[province, "harvest_m3"] / achieved_total,
                rel=1e-12,
            )
        )


@requires_artifacts
def test_comparison_arithmetic() -> None:
    comparison = pd.read_csv(
        chain.OUTPUT_DIR / "ws4_vs_chain_v51_comparison.csv"
    )
    chain_rows = pd.read_csv(
        chain.OUTPUT_DIR / "ws4_119m_product_chain.csv"
    )
    national = chain_rows[chain_rows["geography_level"] == "national"].set_index(
        "basis"
    )
    frozen = (
        pd.read_csv(chain.CHAIN_V51_CSV).set_index("province")
    )
    assert set(comparison["metric"]) == {metric for metric, _ in chain.METRICS}
    for row in comparison.itertuples(index=False):
        if row.metric == "harvest_m3":
            reference = float(frozen["harvest_m3"].sum())
        elif row.metric == "solid_product_m3":
            reference = float(
                frozen["sawlog_product_m3"].sum()
                + frozen["panel_product_m3"].sum()
            )
        elif row.metric.endswith("_pj"):
            base = row.metric.removesuffix("_pj")
            if base.startswith("total_residue_recoverable_"):
                recovery = base.removeprefix("total_residue_recoverable_")
                reference = float(
                    frozen["process_residue_odt"].sum()
                    * chain.LHV_GJ_PER_ODT
                    / 1e6
                    + frozen[
                        f"harvest_residue_recoverable_total_odt_{recovery}"
                    ].sum()
                    * chain.LHV_GJ_PER_ODT
                    / 1e6
                )
            elif base == "total_residue_gross":
                reference = float(
                    frozen["process_residue_odt"].sum()
                    * chain.LHV_GJ_PER_ODT
                    / 1e6
                    + frozen["harvest_residue_gross_odt"].sum()
                    * chain.LHV_GJ_PER_ODT
                    / 1e6
                )
            elif base == "process_residue":
                reference = float(
                    frozen["process_residue_odt"].sum()
                    * chain.LHV_GJ_PER_ODT
                    / 1e6
                )
            elif base == "harvest_residue_gross":
                reference = float(
                    frozen["harvest_residue_gross_odt"].sum()
                    * chain.LHV_GJ_PER_ODT
                    / 1e6
                )
            else:
                recovery = base.removeprefix(
                    "harvest_residue_recoverable_"
                )
                reference = float(
                    frozen[
                        f"harvest_residue_recoverable_total_odt_{recovery}"
                    ].sum()
                    * chain.LHV_GJ_PER_ODT
                    / 1e6
                )
        elif row.metric in frozen.columns:
            reference = float(frozen[row.metric].sum())
        elif row.metric in {
            "harvest_residue_recoverable_r035_odt",
            "harvest_residue_recoverable_r060_odt",
            "harvest_residue_recoverable_r075_odt",
        }:
            recovery = row.metric.removeprefix(
                "harvest_residue_recoverable_"
            ).removesuffix("_odt")
            reference = float(
                frozen[
                    f"harvest_residue_recoverable_total_odt_{recovery}"
                ].sum()
            )
        else:
            raise AssertionError(f"unhandled metric {row.metric}")
        assert row.chain_v51_value == pytest.approx(
            reference, rel=1e-12, abs=1e-6
        )
        achieved = float(national.loc["ws4_achieved", row.metric])
        scaled = float(national.loc["scaled_to_119m_control", row.metric])
        assert row.ws4_achieved_value == pytest.approx(
            achieved, rel=1e-12, abs=1e-6
        )
        assert row.scaled_to_119m_control_value == pytest.approx(
            scaled, rel=1e-12, abs=1e-6
        )
        assert row.ws4_achieved_delta == pytest.approx(
            achieved - reference, rel=1e-12, abs=1e-6
        )
        assert row.scaled_to_119m_control_delta == pytest.approx(
            scaled - reference, rel=1e-12, abs=1e-6
        )
        expected_pct = (
            (achieved - reference) / reference * 100.0 if reference else 0.0
        )
        assert row.ws4_achieved_delta_pct == pytest.approx(
            expected_pct, rel=1e-12, abs=1e-9
        )
        assert bool(row.model_estimate) is True
        assert bool(row.observed) is False
        assert bool(row.policy_grade) is False
        assert bool(row.national_claim) is False
        assert bool(row.ws4_derived) is True
        assert bool(row.official_osecan_changed) is False
        assert bool(row.chain_v51_unchanged) is True


@requires_artifacts
def test_no_nan_or_negative_values() -> None:
    harvest = pd.read_csv(chain.OUTPUT_DIR / "ws4_119m_harvest.csv")
    product_chain = pd.read_csv(
        chain.OUTPUT_DIR / "ws4_119m_product_chain.csv"
    )
    coefficients = pd.read_csv(
        chain.OUTPUT_DIR / "ws4_119m_coefficients.csv"
    )
    comparison = pd.read_csv(
        chain.OUTPUT_DIR / "ws4_vs_chain_v51_comparison.csv"
    )
    for frame in (harvest, product_chain, coefficients, comparison):
        assert not frame.isna().any(axis=None)
        numeric = frame.select_dtypes(include="number")
        assert finite_or_empty(numeric), "non-finite numeric value"
        value_columns = [
            column
            for column in numeric.columns
            if "delta" not in column
        ]
        if value_columns:
            assert (numeric[value_columns] >= -1e-9).all().all()

    summary = json.loads(
        (chain.OUTPUT_DIR / "summary.json").read_text(encoding="utf-8")
    )
    assert summary["status"] == "pass"
    assert summary["labels"] == chain.LABELS
    assert summary["validation"]["checks"]
    assert all(
        check["status"] == "PASS"
        for check in summary["validation"]["checks"]
    )


def finite_or_empty(frame: pd.DataFrame) -> bool:
    return bool(np.isfinite(frame.to_numpy(dtype=float)).all())


@requires_artifacts
def test_manifest_hashes_and_labels() -> None:
    manifest = json.loads(
        (chain.OUTPUT_DIR / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["artifact"] == chain.ARTIFACT
    assert manifest["labels"] == chain.LABELS
    for name, info in manifest["outputs"].items():
        path = chain.OUTPUT_DIR / name
        assert path.is_file()
        assert path.stat().st_size == info["bytes"]
        assert chain._sha256(path) == info["sha256"]
    for path_text, digest in manifest["inputs"].items():
        assert chain._sha256(chain.REPO_ROOT / path_text) == digest


@pytest.mark.slow
def test_ws4_validate_and_simulate_smoke(tmp_path) -> None:
    if not chain.WS4_BIN.is_file():
        pytest.skip("ws4 executable is not available")
    if not chain.DATASET_DIR.is_dir():
        pytest.skip("national WS4 dataset has not been built")
    assert "valid" in chain.run_ws4_validate().lower()

    schedule = _schedule().head(1)
    schedule_path = tmp_path / "schedule.csv"
    schedule.to_csv(schedule_path, index=False)
    run_dir = tmp_path / "run"
    result = subprocess.run(
        [
            str(chain.WS4_BIN),
            "simulate",
            "--dataset",
            str(chain.DATASET_DIR),
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
    assert result.returncode == 0, result.stderr
    products = chain.extract_run_products(run_dir)
    block_id = int(schedule.iloc[0]["block_id"])
    expected = chain.build_block_volumes()
    expected = expected[expected["block_id"] == block_id].iloc[0]
    province = str(expected["province"])
    observed = float(
        products.loc[products["province"] == province, "harvested_m3"].iloc[0]
    )
    assert observed == pytest.approx(
        float(expected["volume_m3"]), rel=1e-9, abs=1e-6
    )
    assert math.isfinite(observed) and observed > 0.0
