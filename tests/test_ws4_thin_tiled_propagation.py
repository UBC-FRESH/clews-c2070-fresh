"""Contract tests for the tiled 30-m WS4 THIN metadata propagation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from workflows.ws4_instances import propagate_thin_tiled_30m as thin

TILED_DIR = thin.TILED_DIR
MANIFEST_PATH = thin.NATIONAL_MANIFEST_PATH
PROPAGATION_PATH = thin.PROPAGATION_PATH

requires_artifacts = pytest.mark.skipif(
    not MANIFEST_PATH.is_file() or not PROPAGATION_PATH.is_file(),
    reason="tiled THIN propagation artifacts have not been generated",
)


def _synthetic_frames() -> dict[str, pd.DataFrame]:
    tracks = pd.DataFrame(
        [
            {
                "track_id": 1,
                "name": "AU(T1)+IFM(unmanaged)+ORIGIN(natural)+SILV_STATE(baseline)+CURVE(curve_a)+treatment()",
            },
            {
                "track_id": 2,
                "name": "AU(T1)+IFM(managed)+ORIGIN(planted)+SILV_STATE(cc_pl)+CURVE(curve_a)+treatment()",
            },
        ],
        columns=thin.TRACK_COLUMNS,
    )
    strata = pd.DataFrame(
        [
            {"track_id": 1, "offset": 0, "next_age": 300, "cycle": 0},
            {"track_id": 2, "offset": 0, "next_age": 300, "cycle": 0},
        ],
        columns=thin.STRATA_COLUMNS,
    )
    treatments = pd.DataFrame(
        [
            {
                "track_id": 1,
                "begin": 40,
                "end": 1000,
                "treatment": "CC",
                "new_track": 2,
                "offset": 0,
                "retain": 0,
            },
            {
                "track_id": 2,
                "begin": 40,
                "end": 1000,
                "treatment": "CC",
                "new_track": 2,
                "offset": 0,
                "retain": 0,
            },
        ],
        columns=thin.TREATMENT_COLUMNS,
    )
    curves = pd.DataFrame(
        [
            {"curve_id": 1, "x": float(x), "y": 1.0}
            for x in range(0, 601)
        ]
        + [
            {"curve_id": 2, "x": float(x), "y": float(x) * 0.5}
            for x in range(0, 601)
        ],
        columns=["curve_id", "x", "y"],
    )
    features = pd.DataFrame(
        [
            {"track_id": 1, "label": "feature.Yield.unmanaged.Total", "curve_id": 2},
            {"track_id": 2, "label": "feature.Yield.managed.Total", "curve_id": 2},
        ],
        columns=thin.FEATURE_COLUMNS,
    )
    products = pd.DataFrame(
        [
            {
                "track_id": 1,
                "treatment": "CC",
                "label": "product.HarvestedVolume.unmanaged.CC",
                "curve_id": 2,
            },
            {
                "track_id": 1,
                "treatment": "CC",
                "label": "product.Treated.unmanaged.CC",
                "curve_id": 1,
            },
            {
                "track_id": 2,
                "treatment": "CC",
                "label": "product.HarvestedVolume.managed.CC",
                "curve_id": 2,
            },
            {
                "track_id": 2,
                "treatment": "CC",
                "label": "product.Treated.managed.CC",
                "curve_id": 1,
            },
        ],
        columns=thin.PRODUCT_COLUMNS,
    )
    accounts = thin.rebuild_accounts(features, products)
    return {
        "tracks": tracks,
        "strata": strata,
        "treatments": treatments,
        "curves": curves,
        "features": features,
        "products": products,
        "accounts": accounts,
        "protoaccounts": accounts.copy(),
    }


def _fingerprints() -> dict[str, dict[str, object]]:
    return {
        name: {"kind": "test", "key": f"{name}-unchanged", "bytes": 1}
        for name in thin.UNCHANGED_TABLES
    }


def _thin_frames() -> tuple[dict[str, pd.DataFrame], thin.PatchPlan]:
    frames = _synthetic_frames()
    plan = thin.plan_thin_patch(
        {name: frame.copy() for name, frame in frames.items()},
        table_fingerprints=_fingerprints(),
    )
    return frames, plan


def test_plan_appends_thin_rows_without_changing_cc_contract() -> None:
    frames, plan = _thin_frames()
    assert plan.status == "patched"
    assert plan.deltas == {
        "tracks": 1,
        "strata": 1,
        "treatments": 4,
        "features": 1,
        "products": 8,
        "accounts": 9,
        "protoaccounts": 9,
    }
    assert plan.after_counts == {
        "tracks": 3,
        "strata": 3,
        "treatments": 6,
        "features": 3,
        "products": 12,
        "accounts": 15,
        "protoaccounts": 15,
    }
    assert plan.cc_contract_before == thin._cc_contract(
        plan.frames, table_fingerprints=_fingerprints()
    )
    assert plan.frames["tracks"]["track_id"].tolist() == [1, 2, 3]
    assert not plan.frames["tracks"].duplicated("track_id").any()
    assert not plan.frames["tracks"].duplicated("name").any()
    assert not plan.frames["treatments"].duplicated(
        ["track_id", "treatment"]
    ).any()
    assert not plan.frames["products"].duplicated(
        ["track_id", "treatment", "label"]
    ).any()
    # Existing natural/managed rows are preserved in place.
    assert thin.coerce_frame(
        "tracks", plan.frames["tracks"].iloc[:2]
    ).equals(thin.coerce_frame("tracks", frames["tracks"]))
    assert thin.coerce_frame(
        "treatments", plan.frames["treatments"].iloc[:2]
    ).equals(thin.coerce_frame("treatments", frames["treatments"]))
    assert thin.coerce_frame(
        "products", plan.frames["products"].iloc[:4]
    ).equals(thin.coerce_frame("products", frames["products"]))


def test_plan_is_idempotent() -> None:
    _, first = _thin_frames()
    second = thin.plan_thin_patch(
        {name: frame.copy() for name, frame in first.frames.items()},
        table_fingerprints=_fingerprints(),
    )
    assert second.status == "skipped"
    assert all(
        thin.frames_equal(name, first.frames[name], second.frames[name])
        for name in thin.PATCHED_TABLES
    )
    assert second.deltas == {name: 0 for name in thin.PATCHED_TABLES}


def test_plan_repairs_partial_thin_state_without_duplicates() -> None:
    _, first = _thin_frames()
    partial = {name: frame.copy() for name, frame in first.frames.items()}
    partial["products"] = partial["products"].iloc[:-1]
    partial["treatments"] = partial["treatments"].iloc[:-1]
    repaired = thin.plan_thin_patch(partial, table_fingerprints=_fingerprints())
    assert repaired.status == "repaired"
    assert repaired.after_counts == first.after_counts
    assert repaired.pairs[0].thinned_id == 3
    assert not repaired.frames["tracks"].duplicated("track_id").any()
    assert not repaired.frames["treatments"].duplicated(
        ["track_id", "treatment"]
    ).any()


def test_no_nan_and_reference_integrity() -> None:
    _, plan = _thin_frames()
    thin._assert_tile_integrity(plan.frames)
    for table in thin.PATCHED_TABLES:
        assert not plan.frames[table].isna().any().any()
    track_ids = set(plan.frames["tracks"]["track_id"])
    assert set(plan.frames["strata"]["track_id"]) == track_ids
    assert set(plan.frames["treatments"]["new_track"]) <= track_ids
    curve_ids = set(plan.frames["curves"]["curve_id"])
    assert set(plan.frames["features"]["curve_id"]) <= curve_ids
    assert set(plan.frames["products"]["curve_id"]) <= curve_ids
    assert (plan.frames["accounts"]["sum"] == 1.0).all()
    assert plan.frames["accounts"].equals(plan.frames["protoaccounts"])


def _manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


@requires_artifacts
def test_national_manifest_records_full_thin_coverage() -> None:
    manifest = _manifest()
    assert manifest["thin_propagated"] is True
    assert manifest["thin_tiles"] == 3345
    assert manifest["thin_skipped_tiles"] == 0
    assert manifest["thin_failed_tiles"] == 0
    assert manifest["full_run"]["status"] == "complete"
    assert manifest["full_run"]["thin_propagated"] is True
    assert manifest["thin_totals"] == {
        "tracks_added": 12_892,
        "strata_added": 12_892,
        "treatments_added": 51_568,
        "features_added": 12_892,
        "products_added": 103_136,
        "account_rows_added": 30_105,
    }
    hashes = manifest["thin_patch_hashes"]
    assert len(hashes) == 3345
    canonical = json.dumps(
        hashes, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    assert manifest["thin_patch_hashes_sha256"] == hashlib.sha256(
        canonical
    ).hexdigest()
    assert manifest["thin_validation"] == {
        "command": ".venv-ws4/bin/ws4 validate --dataset <tile>/dataset",
        "workers": 32,
        "pass": 3345,
        "fail": 0,
        "tiles": 3345,
    }
    assert manifest["thin_smoke"]["status"] == "pass"
    for row in manifest["completed_tiles"]:
        assert row["thin_patch_sha256"] == hashes[row["tile_slug"]]
        assert row["thin_validation_status"] == "pass"
        assert row["thin_validation_exit_code"] == 0
        assert row["tracks"] % 3 == 0
        assert row["tracks"] >= 6
        assert row["treatments"] == 2 * row["tracks"]
        assert row["products"] == 4 * row["tracks"]
        assert row["features"] == row["tracks"]


@requires_artifacts
def test_national_propagation_counts_and_smoke() -> None:
    propagation = json.loads(PROPAGATION_PATH.read_text(encoding="utf-8"))
    assert propagation["status"] == "pass"
    assert propagation["counts"] == {
        "patched": 3345,
        "skipped": 0,
        "failed": 0,
        "tiles": 3345,
    }
    assert propagation["deltas"]["tracks"] == 12_892
    assert propagation["deltas"]["treatments"] == 51_568
    assert propagation["deltas"]["products"] == 103_136
    assert propagation["validation"] == {
        "command": ".venv-ws4/bin/ws4 validate --dataset <tile>/dataset",
        "workers": 32,
        "pass": 3345,
        "fail": 0,
        "tiles": 3345,
    }
    smoke = propagation["smoke"]
    assert smoke["status"] == "pass"
    evidence = smoke["evidence"]
    assert evidence["thin_harvested_volume_m3"] > 0.0
    assert evidence["retained_thinned_value_at_offset_0"] == pytest.approx(
        evidence["expected_thin_volume_m3"], rel=1e-9, abs=1e-6
    )
    assert evidence["thinned_cc_harvested_volume_m3"] > 0.0
    assert evidence["final_thinned_value"] == 0.0


@requires_artifacts
def test_every_tile_patch_record_and_unchanged_tables() -> None:
    manifest = _manifest()
    for row in manifest["completed_tiles"]:
        slug = row["tile_slug"]
        record_path = TILED_DIR / slug / thin.TILE_PATCH_FILENAME
        record = json.loads(record_path.read_text(encoding="utf-8"))
        assert record["patch_sha256"] == row["thin_patch_sha256"]
        assert record["status"] == "patched"
        assert record["natural_fragments_blocks_unchanged"] is True
        assert record["cc_contract_sha256_before"] == record[
            "cc_contract_sha256_after"
        ]
        assert record["validation"]["status"] == "pass"
        assert record["validation"]["exit_code"] == 0
        for table in thin.UNCHANGED_TABLES:
            current = thin.table_fingerprint(
                thin.REPO_ROOT / row["dataset_path"] / f"{table}.parquet"
            )
            assert record["table_fingerprints"][table] == current


@requires_artifacts
def test_sample_tiles_have_complete_thin_graph() -> None:
    manifest = _manifest()
    rows = manifest["completed_tiles"][:: max(1, len(manifest["completed_tiles"]) // 12)]
    for row in rows:
        dataset_dir = thin.REPO_ROOT / row["dataset_path"]
        tracks = pd.read_parquet(dataset_dir / "tracks.parquet")
        treatments = pd.read_parquet(dataset_dir / "treatments.parquet")
        features = pd.read_parquet(dataset_dir / "features.parquet")
        products = pd.read_parquet(dataset_dir / "products.parquet")
        strata = pd.read_parquet(dataset_dir / "strata.parquet")
        accounts = pd.read_parquet(dataset_dir / "accounts.parquet")
        protoaccounts = pd.read_parquet(dataset_dir / "protoaccounts.parquet")
        curves = pd.read_parquet(dataset_dir / "curves.parquet")
        assert len(tracks) == row["tracks"]
        assert len(treatments) == row["treatments"]
        assert len(features) == row["features"]
        assert len(products) == row["products"]
        assert not tracks.duplicated("track_id").any()
        assert not tracks.duplicated("name").any()
        assert set(strata["track_id"]) == set(tracks["track_id"])
        assert not treatments.duplicated(["track_id", "treatment"]).any()
        assert not products.duplicated(
            ["track_id", "treatment", "label"]
        ).any()
        assert not features.duplicated(["track_id", "label"]).any()
        assert set(treatments["track_id"]) <= set(tracks["track_id"])
        assert set(treatments["new_track"]) <= set(tracks["track_id"])
        assert set(features["curve_id"]) <= set(curves["curve_id"])
        assert set(products["curve_id"]) <= set(curves["curve_id"])
        labels = set(features["label"]) | set(products["label"])
        assert set(accounts["account"]) == labels
        assert accounts.equals(protoaccounts)
        assert not tracks.name.str.contains("THIN", regex=False).any()
        assert not features.isna().any().any()
        assert not products.isna().any().any()
        assert not accounts.isna().any().any()
