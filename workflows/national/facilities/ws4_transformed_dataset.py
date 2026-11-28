"""Build the national 990-m WS4 dataset with transformed RSYC curves.

The original ``scanfi-national-990m`` dataset is read-only.  This builder
copies its twelve tables into ``scanfi-national-990m-transformed`` and replaces
only the yield values in ``curves.parquet``:

* age 0 -> 0 m3/ha;
* ages 1..200 -> the transformed RSYC volume for the curve's model;
* ages 201..600 -> the age-200 value, held flat.

A WS4 model can be represented by several transformed cell curves because the
transform donor depends on province and species.  The model-level curve is the
forest-area-weighted mean of those cell curves.  This is a national group
curve: every cell under a model shares one original RSYC coefficient set, and
the weighted mean keeps V100 exact for the group.

All values are model estimates.  The builder does not change the original
dataset, chain v5.1, official OSeCAN, or any UI surface.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_ROOT = REPO_ROOT / "data"

ORIGINAL_ROOT = DATA_ROOT / "derived/ws4/scanfi-national-990m"
ORIGINAL_DATASET = ORIGINAL_ROOT / "dataset"
TRANSFORMED_ROOT = DATA_ROOT / "derived/ws4/scanfi-national-990m-transformed"
TRANSFORMED_DATASET = TRANSFORMED_ROOT / "dataset"

TRANSFORM_DIR = DATA_ROOT / "derived/national/rsyc_transform_v1"
TRANSFORMED_CURVES = TRANSFORM_DIR / "transformed_curves.csv"
CURVE_METADATA = TRANSFORM_DIR / "curve_metadata.csv"
RSYC_LIBRARY = (
    DATA_ROOT
    / "derived/national/assortments/rsyc_tile_production/rsyc_tile_production_library.csv"
)

WS4_BIN = REPO_ROOT / ".venv-ws4/bin/ws4"
DATASET_TABLES = (
    "accounts.parquet",
    "blocks.parquet",
    "curves.parquet",
    "features.parquet",
    "fragments.parquet",
    "groups.parquet",
    "messages.parquet",
    "products.parquet",
    "protoaccounts.parquet",
    "strata.parquet",
    "tracks.parquet",
    "treatments.parquet",
)
ARTIFACT = "ws4_transformed_dataset_v1"
ARTIFACT_VERSION = "1.0.0"
TRANSFORM_AGE_MIN = 1
TRANSFORM_AGE_MAX = 200
CURVE_AGE_MAX = 600
V100_TOLERANCE = 0.01

LABELS: dict[str, bool] = {
    "model_estimate": True,
    "observed": False,
    "policy_grade": False,
    "national_claim": False,
    "ws4_derived": True,
    "transformed_curves": True,
    "original_dataset_unchanged": True,
    "official_osecan_changed": False,
    "chain_v51_unchanged": True,
}


class Ws4TransformedDatasetError(RuntimeError):
    """Raised when the transformed WS4 dataset fails a guardrail."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative(path: Path) -> str:
    return str(path.relative_to(REPO_ROOT))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _finite_nonnegative(values: np.ndarray, label: str) -> None:
    if not np.isfinite(values).all():
        raise Ws4TransformedDatasetError(f"{label} has non-finite values")
    if (values < -1e-12).any():
        raise Ws4TransformedDatasetError(f"{label} has negative values")


@dataclass(frozen=True)
class TransformInputs:
    """Validated transformed-curve inputs and curve_uid weights."""

    metadata: pd.DataFrame
    weights: pd.Series


def load_transform_inputs(
    transformed_curves: Path = TRANSFORMED_CURVES,
    curve_metadata: Path = CURVE_METADATA,
    library: Path = RSYC_LIBRARY,
) -> TransformInputs:
    """Load metadata and cell weights, checking the curve_uid row contract."""
    metadata = pd.read_csv(
        curve_metadata,
        usecols=[
            "curve_uid",
            "model_id",
            "province",
            "species",
            "usable",
            "raw_v100",
        ],
    )
    if metadata["curve_uid"].duplicated().any():
        raise Ws4TransformedDatasetError("duplicate curve_uid in curve metadata")
    if not metadata["usable"].astype(bool).all():
        unusable = int((~metadata["usable"].astype(bool)).sum())
        raise Ws4TransformedDatasetError(
            f"transformed library has {unusable} unusable curves"
        )
    metadata["raw_v100"] = pd.to_numeric(metadata["raw_v100"], errors="raise")
    _finite_nonnegative(metadata["raw_v100"].to_numpy(dtype=float), "raw V100")

    library_frame = pd.read_csv(
        library,
        usecols=[
            "cell_key",
            "model_id",
            "province",
            "species",
            "area_ha",
            "status",
        ],
    )
    if len(library_frame) != len(metadata):
        raise Ws4TransformedDatasetError(
            "RSYC library row count does not match transformed curve count"
        )
    if not library_frame["status"].astype(str).eq("ok").all():
        raise Ws4TransformedDatasetError("RSYC library contains non-ok rows")
    library_frame = library_frame.copy()
    library_frame["curve_uid"] = np.arange(len(library_frame), dtype=np.int64)
    merged = metadata.merge(
        library_frame[
            ["curve_uid", "cell_key", "model_id", "province", "species", "area_ha"]
        ],
        on="curve_uid",
        how="inner",
        validate="one_to_one",
        suffixes=("", "_library"),
    )
    if len(merged) != len(metadata):
        raise Ws4TransformedDatasetError(
            "curve_uid is not aligned between metadata and RSYC library"
        )
    for column in ("model_id", "province", "species"):
        if (
            not merged[column]
            .astype(str)
            .eq(merged[f"{column}_library"].astype(str))
            .all()
        ):
            raise Ws4TransformedDatasetError(
                f"curve_uid {column} differs between metadata and RSYC library"
            )
    area = pd.to_numeric(merged["area_ha"], errors="raise").to_numpy(dtype=float)
    _finite_nonnegative(area, "RSYC cell area weight")
    if not (area > 0.0).all():
        raise Ws4TransformedDatasetError("RSYC library has non-positive area weight")
    weights = pd.Series(area, index=merged["curve_uid"].to_numpy(dtype=np.int64))
    return TransformInputs(
        metadata=metadata.set_index("curve_uid")[
            ["model_id", "province", "species", "raw_v100"]
        ],
        weights=weights,
    )


def build_model_curves(
    transformed_curves: Path = TRANSFORMED_CURVES,
    inputs: TransformInputs | None = None,
) -> pd.DataFrame:
    """Return model x age (1..200) volume from area-weighted cell curves."""
    inputs = inputs or load_transform_inputs()
    curves = pd.read_csv(
        transformed_curves,
        usecols=["model_id", "curve_uid", "age", "volume", "usable"],
    )
    curves["age"] = pd.to_numeric(curves["age"], errors="raise").astype(np.int64)
    curves["volume"] = pd.to_numeric(curves["volume"], errors="raise")
    if not curves["usable"].astype(bool).all():
        raise Ws4TransformedDatasetError("transformed curve file has unusable rows")
    expected_ages = np.arange(TRANSFORM_AGE_MIN, TRANSFORM_AGE_MAX + 1)
    per_curve = curves.groupby("curve_uid")["age"].agg(["min", "max", "size"])
    if not (
        per_curve["min"].eq(TRANSFORM_AGE_MIN).all()
        and per_curve["max"].eq(TRANSFORM_AGE_MAX).all()
        and per_curve["size"].eq(expected_ages.size).all()
    ):
        raise Ws4TransformedDatasetError(
            "transformed curves do not cover ages 1..200 exactly"
        )
    if curves.duplicated(["curve_uid", "age"]).any():
        raise Ws4TransformedDatasetError("duplicate transformed curve age row")
    _finite_nonnegative(curves["volume"].to_numpy(dtype=float), "transformed volume")
    if not set(curves["curve_uid"].unique()) <= set(inputs.weights.index):
        raise Ws4TransformedDatasetError(
            "transformed curve_uid is missing from the weight table"
        )

    weights = inputs.weights.reindex(curves["curve_uid"]).to_numpy(dtype=float)
    if not np.isfinite(weights).all():
        raise Ws4TransformedDatasetError(
            "curve weight lookup produced non-finite values"
        )
    curves["weight"] = weights
    curves["weighted_volume"] = curves["volume"] * curves["weight"]
    grouped = curves.groupby(["model_id", "age"], as_index=False).agg(
        weighted_volume=("weighted_volume", "sum"),
        weight=("weight", "sum"),
    )
    if (grouped["weight"] <= 0.0).any():
        raise Ws4TransformedDatasetError("model curve has zero total area weight")
    grouped["volume"] = grouped["weighted_volume"] / grouped["weight"]
    model_curves = grouped.pivot(
        index="model_id", columns="age", values="volume"
    ).sort_index()
    if model_curves.shape[1] != expected_ages.size:
        raise Ws4TransformedDatasetError("model curve age coverage is incomplete")
    if model_curves.isna().any().any():
        raise Ws4TransformedDatasetError("model curve has NaN volumes")
    _finite_nonnegative(model_curves.to_numpy(dtype=float), "model curve volume")

    reference = inputs.metadata.groupby("model_id")["raw_v100"].agg(["min", "max"])
    if not np.allclose(reference["min"], reference["max"], rtol=0.0, atol=1e-9):
        raise Ws4TransformedDatasetError(
            "model has more than one original V100 in the transformed metadata"
        )
    v100 = model_curves[100].reindex(reference.index)
    error = np.abs(v100.to_numpy(dtype=float) - reference["min"].to_numpy(dtype=float))
    if not np.isfinite(error).all() or float(error.max()) > V100_TOLERANCE:
        raise Ws4TransformedDatasetError(
            f"model V100 is not preserved; max error {float(np.nanmax(error))!r}"
        )
    return model_curves


def map_ws4_curves(dataset_dir: Path, model_curves: pd.DataFrame) -> pd.DataFrame:
    """Map each referenced WS4 curve_id to one RSYC model_id."""
    tracks = pd.read_parquet(
        dataset_dir / "tracks.parquet", columns=["track_id", "name"]
    )
    tracks["model_id"] = tracks["name"].str.extract(r"CURVE\(([^)]+)\)")[0]
    tracks["province"] = tracks["name"].str.extract(r"AU\(([A-Z]{2})\)")[0]
    if tracks["model_id"].isna().any() or tracks["province"].isna().any():
        raise Ws4TransformedDatasetError("unparseable WS4 track name")
    if tracks["track_id"].duplicated().any():
        raise Ws4TransformedDatasetError("duplicate WS4 track_id")

    features = pd.read_parquet(
        dataset_dir / "features.parquet", columns=["track_id", "label", "curve_id"]
    )
    yield_features = features[
        features["label"].str.fullmatch(
            r"feature\.Yield\.[A-Z]{2}\.(unmanaged|thinned|managed)\.Total"
        )
    ].copy()
    if yield_features.empty:
        raise Ws4TransformedDatasetError("no WS4 Yield.Total features")
    if yield_features.duplicated("track_id").any():
        raise Ws4TransformedDatasetError("track has more than one Yield.Total feature")
    mapped = tracks.merge(
        yield_features[["track_id", "curve_id"]],
        on="track_id",
        how="inner",
        validate="one_to_one",
    )
    if len(mapped) != len(tracks):
        raise Ws4TransformedDatasetError("track is missing a Yield.Total curve")

    by_curve = mapped.groupby("curve_id").agg(
        model_ids=("model_id", lambda values: tuple(sorted(set(values)))),
        provinces=("province", lambda values: tuple(sorted(set(values)))),
        track_count=("track_id", "size"),
    )
    multi = by_curve[by_curve["model_ids"].map(len) != 1]
    if not multi.empty:
        raise Ws4TransformedDatasetError(
            "a WS4 curve_id maps to multiple model_ids: "
            + ", ".join(str(value) for value in multi.index[:5])
        )
    by_curve["model_id"] = by_curve["model_ids"].map(lambda values: values[0])
    missing = sorted(set(by_curve["model_id"]) - set(model_curves.index))
    if missing:
        raise Ws4TransformedDatasetError(
            "model_id is missing from the transformed library: "
            + ", ".join(missing[:5])
        )
    if by_curve.index.duplicated().any():
        raise Ws4TransformedDatasetError("duplicate WS4 curve_id mapping")
    return by_curve.reset_index()


def check_original_v100(
    original_curves: pd.DataFrame,
    mapping: pd.DataFrame,
    model_curves: pd.DataFrame,
) -> pd.DataFrame:
    """Return curve-level V100 checks against the original dataset."""
    v100 = original_curves.loc[
        original_curves["x"] == 100.0, ["curve_id", "y"]
    ].set_index("curve_id")["y"]
    rows: list[dict[str, Any]] = []
    for row in mapping.itertuples(index=False):
        if int(row.curve_id) not in v100.index:
            raise Ws4TransformedDatasetError(
                f"original curve {int(row.curve_id)} has no age-100 value"
            )
        original = float(v100.loc[int(row.curve_id)])
        transformed = float(model_curves.loc[row.model_id, 100])
        rows.append(
            {
                "curve_id": int(row.curve_id),
                "model_id": str(row.model_id),
                "original_v100_m3_ha": original,
                "transformed_v100_m3_ha": transformed,
                "abs_error_m3_ha": abs(original - transformed),
            }
        )
    checks = pd.DataFrame(rows)
    if float(checks["abs_error_m3_ha"].max()) > V100_TOLERANCE:
        worst = checks.loc[checks["abs_error_m3_ha"].idxmax()]
        raise Ws4TransformedDatasetError(
            "original WS4 V100 is not preserved for curve "
            f"{int(worst['curve_id'])}: {float(worst['abs_error_m3_ha'])!r}"
        )
    return checks


def patch_curves(
    original_curves: pd.DataFrame,
    mapping: pd.DataFrame,
    model_curves: pd.DataFrame,
) -> pd.DataFrame:
    """Return curves with transformed values and held ages 201..600."""
    patched = original_curves.copy()
    patched["age"] = patched["x"].round().astype(np.int64)
    if not np.isclose(
        patched["x"].to_numpy(dtype=float),
        patched["age"].to_numpy(dtype=float),
        rtol=0.0,
        atol=1e-9,
    ).all():
        raise Ws4TransformedDatasetError("original curve x is not integral age")
    ages = np.arange(0, CURVE_AGE_MAX + 1)
    model_series = model_curves.reindex(
        columns=ages[TRANSFORM_AGE_MIN : TRANSFORM_AGE_MAX + 1]
    )
    if model_series.isna().any().any():
        raise Ws4TransformedDatasetError("model curve is missing ages 1..200")
    held = model_series.to_numpy(dtype=float)
    hold_200 = np.repeat(held[:, -1:], CURVE_AGE_MAX - TRANSFORM_AGE_MAX, axis=1)
    full = np.concatenate([np.zeros((held.shape[0], 1)), held, hold_200], axis=1)

    position_by_model = {
        str(model_id): index
        for index, model_id in enumerate(model_curves.index.to_numpy())
    }
    row_age = patched["age"].to_numpy(dtype=np.int64)
    for row in mapping.itertuples(index=False):
        mask = patched["curve_id"].to_numpy(dtype=np.int64) == int(row.curve_id)
        if not mask.any():
            raise Ws4TransformedDatasetError(
                f"mapped curve {int(row.curve_id)} is missing from curves.parquet"
            )
        values = full[position_by_model[str(row.model_id)]][row_age[mask]]
        patched.loc[mask, "y"] = values
    patched = patched.drop(columns=["age"])
    _finite_nonnegative(patched["y"].to_numpy(dtype=float), "patched curve value")
    grouped = patched.groupby("curve_id")["x"]
    if not grouped.apply(lambda values: bool(values.is_monotonic_increasing)).all():
        raise Ws4TransformedDatasetError("patched curve x is not strictly increasing")
    if not grouped.apply(lambda values: bool(values.is_unique)).all():
        raise Ws4TransformedDatasetError("patched curve x has duplicate ages")
    return patched


def _copy_dataset(source: Path, target: Path) -> None:
    if target.exists():
        raise Ws4TransformedDatasetError(
            f"transformed dataset already exists: {target}; remove it or use --force"
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target, symlinks=False)
    missing = [name for name in DATASET_TABLES if not (target / name).is_file()]
    if missing:
        raise Ws4TransformedDatasetError(
            "copied dataset is missing tables: " + ", ".join(missing)
        )


def _update_manifest(
    dataset_dir: Path,
    *,
    mapping: pd.DataFrame,
    inputs: TransformInputs,
    checks: pd.DataFrame,
    source_manifest_sha256: str,
) -> None:
    path = dataset_dir / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    model_count = int(mapping["model_id"].nunique())
    source_series = len(inputs.metadata)
    manifest.setdefault("metadata", {})
    manifest["metadata"]["transformed_curves"] = {
        "artifact": "national_rsyc_transform_v1",
        "transformed_curves_path": _relative(TRANSFORMED_CURVES),
        "transformed_curves_sha256": _sha256(TRANSFORMED_CURVES),
        "curve_metadata_path": _relative(CURVE_METADATA),
        "curve_metadata_sha256": _sha256(CURVE_METADATA),
        "method": "forest-area-weighted mean of transformed cell curves per model",
        "ws4_curves_mapped": len(mapping),
        "models_mapped": model_count,
        "transformed_cell_curves": source_series,
        "v100_max_abs_error_m3_ha": float(checks["abs_error_m3_ha"].max()),
        "ages": {
            "0": "0 m3/ha",
            "1_200": "transformed RSYC volume",
            "201_600": "held at the age-200 transformed volume",
        },
        "source_manifest_sha256": source_manifest_sha256,
    }
    options = manifest.setdefault("options", {})
    options["curve_basis"] = (
        "RSYC transformed v1; forest-area-weighted model group curve"
    )
    manifest["metadata"]["original_dataset_unchanged"] = True
    manifest["metadata"]["official_osecan_changed"] = False
    manifest["metadata"]["chain_v51_unchanged"] = True
    _write_json(path, manifest)


def build(
    *,
    source_dataset: Path = ORIGINAL_DATASET,
    target_dataset: Path = TRANSFORMED_DATASET,
    force: bool = False,
) -> dict[str, Any]:
    """Build the transformed dataset and write the patch manifest."""
    if not source_dataset.is_dir():
        raise Ws4TransformedDatasetError(
            f"original dataset is missing: {source_dataset}"
        )
    if target_dataset.exists():
        if not force:
            raise Ws4TransformedDatasetError(
                f"target dataset already exists: {target_dataset}"
            )
        shutil.rmtree(target_dataset)

    source_hashes = {
        name: _sha256(source_dataset / name)
        for name in (*DATASET_TABLES, "manifest.json")
    }
    inputs = load_transform_inputs()
    model_curves = build_model_curves(inputs=inputs)
    mapping = map_ws4_curves(source_dataset, model_curves)

    original_curves = pd.read_parquet(
        source_dataset / "curves.parquet", columns=["curve_id", "x", "y"]
    )
    checks = check_original_v100(original_curves, mapping, model_curves)
    patched = patch_curves(original_curves, mapping, model_curves)

    _copy_dataset(source_dataset, target_dataset)
    patched.to_parquet(target_dataset / "curves.parquet", index=False)
    _update_manifest(
        target_dataset,
        mapping=mapping,
        inputs=inputs,
        checks=checks,
        source_manifest_sha256=source_hashes["manifest.json"],
    )

    validate = subprocess.run(
        [str(WS4_BIN), "validate", "--dataset", str(target_dataset)],
        capture_output=True,
        text=True,
        check=False,
    )
    if validate.returncode != 0:
        raise Ws4TransformedDatasetError(
            "ws4 validate failed on the transformed dataset: "
            f"{validate.stdout.strip()} {validate.stderr.strip()}"
        )

    mapping_out = mapping.copy()
    mapping_out["provinces"] = mapping_out["provinces"].map(
        lambda values: ";".join(values)
    )
    mapping_out = mapping_out.merge(
        checks, on=["curve_id", "model_id"], how="left", validate="one_to_one"
    )
    mapping_out["source_series"] = mapping_out["model_id"].map(
        inputs.metadata.groupby("model_id").size()
    )
    mapping_out["model_total_area_ha"] = mapping_out["model_id"].map(
        inputs.weights.groupby(inputs.metadata["model_id"]).sum()
    )
    mapping_out.to_csv(TRANSFORMED_ROOT / "curve_mapping.csv", index=False)

    output_hashes = {
        name: _sha256(target_dataset / name)
        for name in (*DATASET_TABLES, "manifest.json")
    }
    unreferenced = sorted(
        set(original_curves["curve_id"].unique())
        - set(mapping["curve_id"].astype(np.int64))
    )
    summary = {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "status": "pass",
        "labels": LABELS,
        "source_dataset": _relative(source_dataset),
        "target_dataset": _relative(target_dataset),
        "source_hashes": source_hashes,
        "output_hashes": output_hashes,
        "transform_inputs": {
            "transformed_curves": _relative(TRANSFORMED_CURVES),
            "transformed_curves_sha256": _sha256(TRANSFORMED_CURVES),
            "curve_metadata": _relative(CURVE_METADATA),
            "curve_metadata_sha256": _sha256(CURVE_METADATA),
            "rsyc_library": _relative(RSYC_LIBRARY),
            "rsyc_library_sha256": _sha256(RSYC_LIBRARY),
        },
        "mapping": {
            "ws4_curves_in_dataset": int(original_curves["curve_id"].nunique()),
            "ws4_curves_mapped": len(mapping),
            "models_mapped": int(mapping["model_id"].nunique()),
            "tracks_mapped": int(mapping["track_count"].sum()),
            "curve_model_conflicts": 0,
            "missing_models": 0,
            "unreferenced_curves": [
                {"curve_id": int(value), "note": "unreferenced; left unchanged"}
                for value in unreferenced
            ],
            "transformed_cell_curves": len(inputs.metadata),
            "aggregation": "forest-area-weighted mean of transformed cell curves",
        },
        "v100": {
            "tolerance_m3_ha": V100_TOLERANCE,
            "max_abs_error_m3_ha": float(checks["abs_error_m3_ha"].max()),
            "curves_checked": len(checks),
        },
        "ws4_validate": validate.stdout.strip(),
        "unreferenced_curve_policy": (
            "Curve 1 is not referenced by any Yield.Total feature; it remains "
            "the original constant 1.0 m3/ha curve so its V100 is trivially preserved."
        ),
    }
    _write_json(TRANSFORMED_ROOT / "build_summary.json", summary)
    patch_manifest = {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": summary["generated_utc"],
        "labels": LABELS,
        "source": {
            "dataset": _relative(source_dataset),
            "hashes_before": source_hashes,
        },
        "target": {
            "dataset": _relative(target_dataset),
            "hashes_after": output_hashes,
        },
        "mapping": summary["mapping"],
        "v100": summary["v100"],
        "ws4_validate": summary["ws4_validate"],
    }
    _write_json(TRANSFORMED_ROOT / "patch_manifest.json", patch_manifest)
    return summary


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="replace the target")
    args = parser.parse_args()
    summary = build(force=args.force)
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
