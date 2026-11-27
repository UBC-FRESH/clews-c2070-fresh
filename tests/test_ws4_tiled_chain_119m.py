"""Contract tests for the tiled 30-m WS4 119 Mm3 chain."""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from workflows.national.facilities import ws4_tiled_chain_119m as chain
from workflows.national.facilities import ws4_tiled_osecan_handoff as osecan

REQUIRED = (
    "ws4_tiled_harvest.csv",
    "ws4_tiled_coefficients.csv",
    "ws4_tiled_product_chain.csv",
    "ws4_tiled_vs_chain_v51_comparison.csv",
    "ws4_tiled_selected_blocks.csv",
    "ws4_tiled_candidate_pool.csv",
    "ws4_tiled_sample_simulation.csv",
    "thin_refresh_comparison.csv",
    "summary.json",
    "manifest.json",
    "WS4_TILED_CHAIN_119M.md",
)
requires_artifacts = pytest.mark.skipif(
    not all((chain.OUTPUT_DIR / name).exists() for name in REQUIRED),
    reason="tiled WS4 119 Mm3 chain has not been generated",
)


def _harvest() -> pd.DataFrame:
    return pd.read_csv(chain.OUTPUT_DIR / "ws4_tiled_harvest.csv")


def _selected() -> pd.DataFrame:
    return pd.read_csv(chain.OUTPUT_DIR / "ws4_tiled_selected_blocks.csv")


def _chain_rows() -> pd.DataFrame:
    return pd.read_csv(chain.OUTPUT_DIR / "ws4_tiled_product_chain.csv")


def _summary() -> dict:
    return json.loads((chain.OUTPUT_DIR / "summary.json").read_text())


def _tiny_tile(path: Path, *, au_code: str = "T1") -> Path:
    dataset = path / "dataset"
    dataset.mkdir(parents=True)
    tracks = pd.DataFrame(
        {
            "track_id": [1, 2],
            "name": [
                f"AU({au_code})+IFM(unmanaged)+ORIGIN(natural)+CURVE(c)+treatment()",
                f"AU({au_code})+IFM(managed)+ORIGIN(planted)+CURVE(c)+treatment()",
            ],
        }
    )
    features = pd.DataFrame(
        {
            "track_id": [1, 2],
            "label": [
                "feature.Yield.unmanaged.Total",
                "feature.Yield.managed.Total",
            ],
            "curve_id": [1, 1],
        }
    )
    ages = np.arange(0, chain.CURVE_AGE_MAX + 1)
    curves = pd.DataFrame(
        {
            "curve_id": np.repeat(1, len(ages)),
            "x": ages.astype(float),
            "y": ages.astype(float) * 2.0,
        }
    )
    blocks = pd.DataFrame(
        {
            "block_id": [10, 20, 30, 40],
            "track_id": [1, 1, 1, 2],
            "offset": [50, 30, 60, 50],
            "area_ha": [1.0, 1.0, 2.0, 10.0],
        }
    )
    tracks.to_parquet(dataset / "tracks.parquet", index=False)
    features.to_parquet(dataset / "features.parquet", index=False)
    curves.to_parquet(dataset / "curves.parquet", index=False)
    blocks.to_parquet(dataset / "blocks.parquet", index=False)
    return dataset


def test_candidate_scan_filters_age_and_uses_curve_offset(tmp_path: Path) -> None:
    dataset = _tiny_tile(tmp_path)
    candidate_dir = tmp_path / "candidates"
    stats = chain._scan_tile_worker(
        {
            "tile_slug": "T1-ON-S11",
            "province": "ON",
            "au_code": "T1",
            "dataset_dir": str(dataset),
            "candidate_dir": str(candidate_dir),
            "per_tile_limit": 10,
        }
    )
    assert stats["eligible_blocks"] == 2
    assert stats["candidate_blocks"] == 2
    candidates = pd.read_parquet(candidate_dir / "T1-ON-S11.parquet")
    assert set(candidates["block_id"]) == {10, 30}
    assert candidates["offset"].min() >= chain.AGE_OFFSET_MIN
    values = candidates.set_index("block_id")["approx_volume_m3"]
    assert values.loc[10] == pytest.approx(100.0)
    assert values.loc[30] == pytest.approx(240.0)


def test_candidate_scan_fails_on_au_mismatch(tmp_path: Path) -> None:
    dataset = _tiny_tile(tmp_path, au_code="T2")
    with pytest.raises(chain.Ws4TiledChainError, match="AU codes"):
        chain._scan_tile_worker(
            {
                "tile_slug": "T2-ON-S11",
                "province": "ON",
                "au_code": "T1",
                "dataset_dir": str(dataset),
                "candidate_dir": str(tmp_path / "candidates"),
                "per_tile_limit": 10,
            }
        )


def test_thin_tracks_are_excluded_from_natural_selection(
    tmp_path: Path,
) -> None:
    dataset = _tiny_tile(tmp_path)
    tracks = pd.read_parquet(dataset / "tracks.parquet")
    tracks = pd.concat(
        [
            tracks,
            pd.DataFrame(
                {
                    "track_id": [3],
                    "name": [
                        (
                            "AU(T1)+IFM(unmanaged)+ORIGIN(natural)"
                            "+SILV_STATE(thinned)+CURVE(c)+treatment()"
                        )
                    ],
                }
            ),
        ],
        ignore_index=True,
    )
    tracks.to_parquet(dataset / "tracks.parquet", index=False)
    blocks = pd.read_parquet(dataset / "blocks.parquet")
    blocks = pd.concat(
        [
            blocks,
            pd.DataFrame(
                {
                    "block_id": [50],
                    "track_id": [3],
                    "offset": [80],
                    "area_ha": [10_000.0],
                }
            ),
        ],
        ignore_index=True,
    )
    blocks.to_parquet(dataset / "blocks.parquet", index=False)

    stats = chain._scan_tile_worker(
        {
            "tile_slug": "T1-ON-S11",
            "province": "ON",
            "au_code": "T1",
            "dataset_dir": str(dataset),
            "candidate_dir": str(tmp_path / "candidates"),
            "per_tile_limit": 10,
        }
    )
    assert stats["natural_tracks"] == 1
    assert stats["thinned_tracks_excluded"] == 1
    candidates = pd.read_parquet(tmp_path / "candidates/T1-ON-S11.parquet")
    assert set(candidates["block_id"]) == {10, 30}
    assert 50 not in set(candidates["block_id"])


def test_coverage_manifest_hash_is_pinned() -> None:
    verification = chain.verify_coverage_manifest()
    assert (
        verification["coverage_manifest_sha256"]
        == chain.COVERAGE_MANIFEST_SHA256
    )
    assert verification["thin_propagated"] is True
    assert verification["thin_tiles"] == 3345
    assert verification["thin_tracks_added"] == 12892
    payload = json.loads(chain.TILED_MANIFEST.read_text())
    assert payload["thin_propagated"] is True
    assert payload["thin_failed_tiles"] == 0
    assert payload["thin_skipped_tiles"] == 0


def test_greedy_select_is_unique_and_meets_target() -> None:
    frame = pd.DataFrame(
        {
            "tile_slug": ["A", "A", "B"],
            "block_id": [1, 2, 3],
            "volume_m3": [10.0, 6.0, 4.0],
        }
    )
    selected = chain._greedy_select(frame, "volume_m3", 12.0)
    assert not selected.duplicated(["tile_slug", "block_id"]).any()
    assert selected["volume_m3"].sum() >= 12.0
    empty = chain._greedy_select(frame, "volume_m3", 0.0)
    assert empty.empty


@requires_artifacts
def test_coverage_and_candidate_pool() -> None:
    manifest = json.loads(chain.TILED_MANIFEST.read_text())
    summary = _summary()
    assert summary["coverage"]["tiles_scanned"] == 3345
    assert summary["coverage"]["built_validated_tiles"] == 3345
    assert summary["coverage"]["zero_harvest_skips"] == 203
    assert summary["coverage"]["no_forest_skips"] == 14
    assert summary["coverage"]["full_built_tile_coverage"] is True
    assert summary["scan"]["blocks_read"] == 1_678_150_739
    assert summary["scan"]["eligible_blocks"] == 1_431_297_895
    assert summary["scan"]["zero_approx_blocks"] == 0
    tile_stats = pd.read_csv(chain.OUTPUT_DIR / "ws4_tiled_tile_scan_summary.csv")
    assert len(tile_stats) == 3345
    assert tile_stats["tile_slug"].is_unique
    assert tile_stats["candidate_blocks"].sum() == summary["scan"]["candidate_blocks_written"]
    assert set(tile_stats["province"]) == set(chain.PROVINCES) - {"NU"}
    pools = pd.read_csv(chain.OUTPUT_DIR / "ws4_tiled_candidate_pool.csv")
    assert len(pools) == len(chain.PROVINCES)
    for row in pools.itertuples(index=False):
        if row.target_m3 > 0:
            assert row.pool_volume_m3 >= row.target_m3 * chain.POOL_MARGIN
            assert row.pool_margin >= chain.POOL_MARGIN
    assert len(manifest["completed_tiles"]) == 3345


@requires_artifacts
def test_thin_refresh_io_contract() -> None:
    summary = _summary()
    manifest = json.loads((chain.OUTPUT_DIR / "manifest.json").read_text())
    for record in (summary, manifest):
        assert (
            record["coverage_manifest_sha256"]
            == chain.COVERAGE_MANIFEST_SHA256
        )
        assert record["thin_propagated"] is True
        assert record["cc_contract_hashes_unchanged"] is True
        assert record["pre_thin_results_unchanged"] is True
        assert record["rerun_utc"]
        assert record["thin_refresh"]["mode"] == "full_rescan"
    thin = summary["thin_refresh"]
    assert thin["cc_contract_hash_count"] == 3345
    assert thin["tiles_verified"] == 3345
    assert thin["natural_fragments_blocks_unchanged"] is True
    assert thin["thinned_tracks_excluded"] == thin["thin_tracks_added"] == 12892
    assert summary["scan"]["thinned_tracks_excluded"] == 12892
    assert summary["scan"]["natural_tracks"] > 0
    tile_stats = pd.read_csv(
        chain.OUTPUT_DIR / "ws4_tiled_tile_scan_summary.csv"
    )
    assert len(tile_stats) == 3345
    assert tile_stats["thinned_tracks_excluded"].sum() == 12892
    assert (tile_stats["thinned_tracks_excluded"] > 0).all()

    comparison = pd.read_csv(chain.OUTPUT_DIR / chain.THIN_REFRESH_COMPARISON_CSV)
    assert len(comparison) == thin["compared_rows"]
    assert comparison["matches"].all()
    assert thin["mismatch_count"] == 0
    assert thin["max_abs_delta"] < chain.SELECTION_TOLERANCE_M3
    selection = comparison[
        (comparison["scope"] == "selection")
        & (comparison["metric"] == "selected_block_keys_sha256")
    ]
    assert len(selection) == len(chain.PROVINCES) + 1
    assert selection["matches"].all()
    assert chain.THIN_REFRESH_COMPARISON_CSV in manifest["outputs"]
    numeric_delta = pd.to_numeric(comparison["delta"], errors="coerce").dropna()
    assert np.isfinite(numeric_delta.to_numpy()).all()


@requires_artifacts
def test_selected_blocks_unique_eligible_and_conserved() -> None:
    selected = _selected()
    assert not selected.duplicated(["tile_slug", "block_id"]).any()
    assert (selected["offset"] >= chain.AGE_OFFSET_MIN).all()
    assert (selected["exact_volume_m3"] > 0.0).all()
    assert (selected["fragment_area_ha"] > 0.0).all()
    assert np.allclose(
        selected["area_ha"], selected["fragment_area_ha"], rtol=1e-12, atol=1e-6
    )
    gap = (selected["area_ha"] - selected["fragment_area_ha"]).abs().max()
    assert gap <= chain.AREA_TOLERANCE_HA
    for field in (
        "model_estimate",
        "observed",
        "policy_grade",
        "national_claim",
        "ws4_derived",
        "tiled_30m",
        "official_osecan_changed",
        "chain_v51_unchanged",
    ):
        assert field in selected.columns
    labels = selected[
        [
            "model_estimate",
            "observed",
            "policy_grade",
            "national_claim",
            "ws4_derived",
            "tiled_30m",
            "official_osecan_changed",
            "chain_v51_unchanged",
        ]
    ].drop_duplicates()
    assert len(labels) == 1


@requires_artifacts
def test_exact_harvest_by_province_conserves_selected_blocks() -> None:
    harvest = _harvest().set_index("province")
    selected = _selected()
    for province in chain.PROVINCES:
        expected = float(
            selected.loc[selected["province"] == province, "exact_volume_m3"].sum()
        )
        area = float(
            selected.loc[selected["province"] == province, "fragment_area_ha"].sum()
        )
        assert harvest.loc[province, "achieved_m3"] == pytest.approx(
            expected, rel=1e-12, abs=1e-6
        )
        assert harvest.loc[province, "area_ha"] == pytest.approx(
            area, rel=1e-12, abs=1e-6
        )
        assert harvest.loc[province, "block_count"] == int(
            (selected["province"] == province).sum()
        )
    assert harvest["achieved_m3"].sum() == pytest.approx(
        selected["exact_volume_m3"].sum(), rel=1e-12, abs=1e-6
    )


@requires_artifacts
def test_target_and_overshoot_arithmetic() -> None:
    harvest = _harvest()
    assert set(harvest["province"]) == set(chain.PROVINCES)
    assert harvest["target_m3"].sum() == pytest.approx(
        chain.CONTROL_TOTAL_M3, rel=1e-12
    )
    for row in harvest.itertuples(index=False):
        assert row.delta == pytest.approx(
            row.achieved_m3 - row.target_m3, rel=1e-12, abs=1e-6
        )
        assert row.achieved_m3 + 1e-6 >= row.target_m3
        if row.target_m3 > 0:
            assert row.overshoot_pct == pytest.approx(
                row.delta / row.target_m3 * 100.0, rel=1e-12, abs=1e-9
            )
            assert row.block_count > 0
            assert row.tile_count > 0
            assert row.pool_margin >= chain.POOL_MARGIN
        else:
            assert row.province == "NU"
            assert row.achieved_m3 == 0.0
            assert row.block_count == 0
    national_overshoot = harvest["achieved_m3"].sum() - harvest["target_m3"].sum()
    assert national_overshoot >= 0.0
    assert national_overshoot / chain.CONTROL_TOTAL_M3 < 0.001


@requires_artifacts
def test_chain_coefficients_apply_to_both_bases() -> None:
    frozen = chain.chain._frozen_province_rows()
    product = _chain_rows()
    assert set(product["basis"]) == set(chain.BASES)
    assert product["geography_level"].eq("province").sum() == 2 * len(chain.PROVINCES)
    for province in chain.PROVINCES:
        reference = float(frozen[province]["harvest_m3"])
        if reference <= 0:
            continue
        for basis in chain.BASES:
            row = product[
                (product["province"] == province) & (product["basis"] == basis)
            ].iloc[0]
            harvest = float(row["harvest_m3"])
            for metric in chain.COEFFICIENT_METRICS:
                coefficient = float(frozen[province][metric]) / reference
                assert float(row[metric]) == pytest.approx(
                    harvest * coefficient, rel=1e-9, abs=1e-6
                )
            assert float(row["solid_product_m3"]) == pytest.approx(
                float(row["sawlog_product_m3"]) + float(row["panel_product_m3"]),
                rel=1e-12,
                abs=1e-6,
            )
            assert float(row["process_residue_pj"]) == pytest.approx(
                float(row["process_residue_odt"])
                * chain.LHV_GJ_PER_ODT
                / chain.PJ_PER_GJ,
                rel=1e-12,
                abs=1e-9,
            )


@requires_artifacts
def test_scaled_basis_matches_control_total() -> None:
    product = _chain_rows()
    national = product[
        (product["geography_level"] == "national")
        & (product["basis"] == "scaled_to_119m_control")
    ].iloc[0]
    assert float(national["harvest_m3"]) == pytest.approx(
        chain.CONTROL_TOTAL_M3, rel=1e-12
    )
    achieved = product[
        (product["geography_level"] == "national")
        & (product["basis"] == "ws4_achieved")
    ].iloc[0]
    assert float(achieved["harvest_m3"]) == pytest.approx(
        _harvest()["achieved_m3"].sum(), rel=1e-12, abs=1e-6
    )


@requires_artifacts
def test_comparison_arithmetic() -> None:
    comparison = pd.read_csv(
        chain.OUTPUT_DIR / "ws4_tiled_vs_chain_v51_comparison.csv"
    )
    assert len(comparison) == len(chain.METRICS)
    for row in comparison.itertuples(index=False):
        assert row.ws4_achieved_delta == pytest.approx(
            row.ws4_achieved_value - row.chain_v51_value, rel=1e-12, abs=1e-6
        )
        assert row.scaled_to_119m_control_delta == pytest.approx(
            row.scaled_to_119m_control_value - row.chain_v51_value,
            rel=1e-12,
            abs=1e-6,
        )
    harvest = comparison[comparison["metric"] == "harvest_m3"].iloc[0]
    assert float(harvest["scaled_to_119m_control_value"]) == pytest.approx(
        chain.CONTROL_TOTAL_M3, rel=1e-12
    )


@requires_artifacts
def test_osecan_tiers_and_official_tree() -> None:
    summary = _summary()
    assert summary["osecan"]["primary_7_of_7"] is True
    assert summary["osecan"]["secondary_7_of_7"] is True
    assert summary["osecan"]["frozen_21_of_21"] is True
    assert summary["osecan"]["official_unchanged"] is True
    assert summary["osecan"]["official_detail"].startswith(
        "146 files, 80caa91c"
    )
    assert len(summary["osecan"]["chain_hash"]) == 64
    nested_manifest = json.loads(
        (chain.OUTPUT_DIR / "osecan" / "manifest.json").read_text()
    )
    assert nested_manifest["chain"]["hash"] == summary["osecan"]["chain_hash"]
    assert nested_manifest["chain"]["dir"] == summary["osecan"]["chain_dir"]
    assert set(nested_manifest["chain"]["files"]) == {
        chain._relative(chain.OUTPUT_DIR / name)
        for name in osecan.CHAIN_INPUT_FILES
    }
    tier_rows = pd.read_csv(
        chain.OUTPUT_DIR / "osecan" / "case_tier_positions.csv"
    )
    assert len(tier_rows) == 14
    assert not tier_rows["tier_changed"].any()
    assert (tier_rows["tier"] == tier_rows["v10_119_tier"]).all()
    comparison = pd.read_csv(
        chain.OUTPUT_DIR / "osecan" / "tier_comparison_vs_v10.csv"
    )
    assert len(comparison) == 21
    assert comparison["v10_tier_matches_frozen"].all()
    zone_rows = pd.read_csv(
        chain.OUTPUT_DIR / "osecan" / "case_zone_summary.csv"
    )
    assert len(zone_rows) == 182
    assert set(zone_rows["zone_id"]) == set(osecan.ZONE_ORDER)
    for keys, group in zone_rows.groupby(["basis", "stream", "availability_case"]):
        assert len(group) == 13
        assert float(group["pj"].sum()) == pytest.approx(
            float(group["national_case_pj"].iloc[0]), rel=1e-9, abs=1e-6
        )


@requires_artifacts
def test_sample_ws4_simulations_reproduce_exact_volume() -> None:
    sample = pd.read_csv(chain.OUTPUT_DIR / "ws4_tiled_sample_simulation.csv")
    assert len(sample) == chain.SAMPLE_TILE_COUNT
    assert sample["validate"].eq("pass").all()
    assert sample["simulation"].eq("pass").all()
    for row in sample.itertuples(index=False):
        assert row.simulated_harvest_m3 == pytest.approx(
            row.exact_volume_m3, rel=1e-9, abs=1e-6
        )
        assert row.simulated_treated_ha == pytest.approx(
            row.exact_area_ha, rel=1e-9, abs=1e-6
        )


@requires_artifacts
def test_no_nan_or_negative_artifact_values() -> None:
    frames = {
        "harvest": _harvest(),
        "selected": _selected(),
        "product": _chain_rows(),
        "comparison": pd.read_csv(
            chain.OUTPUT_DIR / "ws4_tiled_vs_chain_v51_comparison.csv"
        ),
        "tiers": pd.read_csv(
            chain.OUTPUT_DIR / "osecan" / "case_tier_positions.csv"
        ),
        "zones": pd.read_csv(
            chain.OUTPUT_DIR / "osecan" / "case_zone_summary.csv"
        ),
    }
    for name, frame in frames.items():
        numeric = frame.select_dtypes(include=[np.number])
        assert np.isfinite(numeric.to_numpy()).all(), name
    for column in ("achieved_m3", "area_ha", "exact_volume_m3", "fragment_area_ha"):
        assert (frames["harvest" if column in frames["harvest"] else "selected"][column] >= 0).all()
    for column in ("residue_odt", "pj"):
        assert (frames["zones"][column] >= 0).all()
    assert math.isfinite(float(frames["comparison"]["chain_v51_value"].sum()))
