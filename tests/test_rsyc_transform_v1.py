"""Acceptance tests for the national RSYC transformed curve library v1."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[1]
ARTIFACT = REPO / "data/derived/national/rsyc_transform_v1"
SCRIPT = REPO / "scripts/national/build_rsyc_transform_v1.py"


def load_pipeline():
    spec = importlib.util.spec_from_file_location("build_rsyc_transform_v1", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def artifact():
    if not (ARTIFACT / "summary.json").exists():
        pytest.skip("rsyc_transform_v1 artifact not built")
    summary = json.loads((ARTIFACT / "summary.json").read_text())
    metadata = pd.read_csv(ARTIFACT / "curve_metadata.csv")
    parameters = pd.read_csv(ARTIFACT / "transform_parameters.csv")
    curves = pd.read_csv(
        ARTIFACT / "transformed_curves.csv",
        usecols=["model_id", "curve_uid", "province", "species", "species_group",
                 "age", "volume", "transform_class", "parameters", "qc_status", "usable"],
    )
    return summary, metadata, parameters, curves


def test_schema_and_status(artifact):
    summary, metadata, _, curves = artifact
    required = {
        "model_id", "curve_uid", "province", "species", "species_group", "age",
        "volume", "transform_class", "parameters", "qc_status", "usable",
    }
    assert required.issubset(curves.columns)
    assert set(curves["qc_status"]).issubset({"pass", "flag", "fail"})
    assert {bool(v) for v in curves["usable"]} == {True}
    assert int((curves["qc_status"] == "fail").sum()) == 0
    assert len(metadata) == summary["counts"]["curves"]
    assert len(curves) == summary["counts"]["rows"]
    assert metadata["usable"].all()
    assert summary["labels"]["observed"] is False
    assert summary["labels"]["national_claim"] is False


def test_parameter_bounds(artifact):
    _, _, parameters, _ = artifact
    assert (parameters["s"].between(-50.0, 150.0)).all()
    assert (parameters["T"].between(0.0, 100.0)).all()
    assert (parameters["alpha"].between(0.1, 5.0)).all()
    assert (parameters["r"].between(1.0e-5, 1.0)).all()
    assert (parameters["decline_d"].between(0.0, 0.85)).all()
    managed = parameters[parameters["style"] == "managed_extended"]
    natural = parameters[parameters["style"] == "natural_5param"]
    assert (managed["decline_kind"] != "none").all()
    assert (natural["decline_kind"] == "none").all()
    assert managed["decline_flat_age"].between(25.0, 200.0).all()


def test_v100_preserved(artifact):
    _, metadata, _, curves = artifact
    assert (metadata["level_basis"] == "v100").all()
    assert np.allclose(metadata["v100_transformed"], metadata["raw_v100"], rtol=1.0e-6, atol=1.0e-4)
    sample = metadata.sample(min(250, len(metadata)), random_state=7)
    curve_index = curves.set_index(["curve_uid", "age"])["volume"]
    for row in sample.itertuples():
        assert curve_index.loc[(row.curve_uid, 100)] == pytest.approx(row.raw_v100, rel=1.0e-4, abs=1.0e-2)


def test_no_nan_or_negative(artifact):
    _, metadata, _, curves = artifact
    numeric = metadata.select_dtypes(include=[np.number])
    assert not numeric.isna().any().any()
    assert np.isfinite(curves["volume"]).all()
    assert (curves["volume"] >= 0.0).all()
    assert curves["age"].between(1, 200).all()
    assert curves.groupby("curve_uid")["age"].nunique().eq(200).all()


def test_no_2_or_24_cmai_artifact(artifact):
    summary, metadata, _, _ = artifact
    assert int((metadata["cmai_age_transformed"] == 2).sum()) == 0
    assert int((metadata["cmai_age_transformed"] == 24).sum()) == 0
    assert summary["artifacts"]["raw_cmai_age_2"] > 0
    assert summary["artifacts"]["raw_cmai_age_24"] > 0
    assert summary["artifacts"]["transformed_cmai_age_2"] == 0
    assert summary["artifacts"]["transformed_cmai_age_24"] == 0


def test_monotone_and_decline_rules(artifact):
    _, metadata, _, curves = artifact
    meta = metadata.set_index("curve_uid")
    tolerance = 1.0e-3
    for curve_uid, group in curves.groupby("curve_uid", sort=False):
        volume = group.sort_values("age")["volume"].to_numpy(dtype=float)
        peak_age = int(meta.loc[curve_uid, "peak_age_transformed"])
        if peak_age > 1:
            pre = volume[:peak_age]
            scale = max(float(np.max(volume)), 1.0)
            assert np.all(np.diff(pre) >= -tolerance * scale)
        if peak_age < 200:
            post = volume[peak_age - 1:]
            scale = max(float(np.max(volume)), 1.0)
            assert np.all(np.diff(post) <= tolerance * scale)
        style = meta.loc[curve_uid, "transform_style"]
        if style == "managed_extended":
            assert peak_age <= 80
            flat = int(round(meta.loc[curve_uid, "decline_flat_age"]))
            if flat < 199:
                plateau = volume[flat - 1:]
                assert np.max(np.abs(plateau - plateau[-1])) <= 0.01 * max(float(np.max(volume)), 1.0)


def test_reproduce_bc_fit(artifact):
    _, _, _, _ = artifact
    module = load_pipeline()
    library = module.load_library()
    similarity = pd.read_csv(module.SIMILARITY)
    row = similarity[similarity["tile_id"] == "F4"].iloc[0]
    fit = module.fit_bc_tile((row.tile_id, row.dominant_tsa, row.dominant_species), library)
    assert fit["status"] == "ok"
    stored = pd.read_csv(ARTIFACT / "fit_metrics.csv")
    reference = stored[
        (stored["donor_id"] == "F4") & (stored["window"] == "fit10_150")
    ].iloc[0]
    assert fit["fit10_150"]["r2"] == pytest.approx(reference["r2"], abs=1.0e-6)
    assert fit["fit10_150"]["alpha"] == pytest.approx(reference["alpha"], abs=1.0e-5)
    assert fit["fit10_150"]["rmse"] == pytest.approx(reference["rmse"], abs=1.0e-4)


def test_reproduce_qc_fit(artifact):
    _, _, _, _ = artifact
    module = load_pipeline()
    units, meta, _ = module.load_qc_units()
    record = module.fit_qc_unit("01171", units, meta)
    stored = pd.read_csv(ARTIFACT / "fit_metrics.csv")
    reference = stored[
        (stored["donor_id"] == "01171") & (stored["window"] == "full_with_linear")
    ].iloc[0]
    assert record["linear_r2"] == pytest.approx(reference["r2"], abs=1.0e-6)
    assert record["base_r2"] == pytest.approx(reference["base_r2"], abs=1.0e-6)
    assert record["applied"]["alpha"] == pytest.approx(reference["alpha"], abs=1.0e-6)
    assert record["decline_d"] == pytest.approx(reference["decline_d"], abs=1.0e-6)
