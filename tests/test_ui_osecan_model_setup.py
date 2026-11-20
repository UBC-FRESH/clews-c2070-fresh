"""Tests for the reproducible OSeCAN model rebuild."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from workflows.ui.model_tree import MODEL_COPY_ENTRIES, tree_sha256
from workflows.ui.osecan_runner import RunnerError, resolve_model_source
from workflows.ui.setup_osecan_model import (
    DEFAULT_COMMIT,
    PATCHED_FILES,
    PATCHES,
    SetupError,
    setup_model,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
BASE_SOURCE = Path(os.environ.get("CLEWS_OSECAN_BASE", "/tmp/osecan_src"))
BASE_PYTHON = Path(
    os.environ.get("CLEWS_OSECAN_BASE_PYTHON", BASE_SOURCE / ".venv/bin/python")
)
BASE_AVAILABLE = (
    (BASE_SOURCE / ".git").is_dir()
    and BASE_PYTHON.is_file()
    and shutil.which("git") is not None
)

pytestmark = pytest.mark.skipif(
    not BASE_AVAILABLE,
    reason="local C2070 checkout with a Python environment is unavailable",
)


@pytest.fixture(scope="module")
def built_model(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("osecan-model") / "model"
    setup_model(
        base=str(BASE_SOURCE),
        commit=DEFAULT_COMMIT,
        out=out,
        python=BASE_PYTHON,
    )
    return out


def _copy_model(source: Path, target: Path) -> Path:
    for entry in MODEL_COPY_ENTRIES:
        source_path = source / entry
        if not source_path.exists():
            continue
        target_path = target / entry
        if source_path.is_dir():
            shutil.copytree(source_path, target_path)
        else:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_path, target_path)
    shutil.copyfile(source / "model_manifest.json", target / "model_manifest.json")
    return target


def test_setup_applies_patches_and_writes_manifest(built_model: Path) -> None:
    manifest = json.loads((built_model / "model_manifest.json").read_text("utf-8"))
    assert manifest["source_commit"] == DEFAULT_COMMIT
    assert [patch["file"] for patch in manifest["patches"]] == [
        patch.file_name for patch in PATCHES
    ]
    assert [patch["sha256"] for patch in manifest["patches"]] == [
        patch.sha256 for patch in PATCHES
    ]
    assert manifest["checks"]["tier_technologies"] == [
        "SUPBCWT1",
        "SUPBCWT2",
        "SUPBCWT3",
        "SUPBCWT4",
    ]
    assert manifest["checks"]["zonal_bc_fuels"] == ["BCWB1", "BCWB2"]
    assert manifest["checks"]["gas_output_factor_mt_per_pj"] == 0.1377
    assert manifest["checks"]["coal_output_factor_mt_per_pj"] == 0.261
    assert manifest["model_tree_sha256"] == tree_sha256(built_model)


def test_patch_application_is_minimal(built_model: Path) -> None:
    commit = subprocess.run(
        ["git", "-C", str(built_model), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert commit == DEFAULT_COMMIT
    changed = set(
        subprocess.run(
            ["git", "-C", str(built_model), "diff", "--name-only"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()
    )
    assert changed == PATCHED_FILES


def test_manifest_tree_hash_fails_closed(tmp_path: Path, built_model: Path) -> None:
    copy_root = _copy_model(built_model, tmp_path / "model")
    manifest_path = copy_root / "model_manifest.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    manifest["model_tree_sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(RunnerError, match="tree hash"):
        resolve_model_source(copy_root)


def test_missing_default_model_instructs_setup(tmp_path: Path) -> None:
    with pytest.raises(RunnerError, match="setup_osecan_model"):
        resolve_model_source(tmp_path / "missing")


def test_patch_hash_mismatch_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from workflows.ui import setup_osecan_model as setup

    tampered = (
        setup.Patch("01_emission_factors.patch", "0" * 64),
    )
    monkeypatch.setattr(setup, "PATCHES", tampered)
    with pytest.raises(SetupError, match="patch hash mismatch"):
        setup.setup_model(
            base=str(BASE_SOURCE),
            commit=DEFAULT_COMMIT,
            out=tmp_path / "model",
            python=BASE_PYTHON,
        )


def test_setup_refuses_to_replace_unmarked_tree(tmp_path: Path) -> None:
    out = tmp_path / "model"
    out.mkdir()
    (out / "not-a-model.txt").write_text("x", encoding="utf-8")
    with pytest.raises(SetupError, match="refusing to replace"):
        setup_model(
            base=str(BASE_SOURCE),
            commit=DEFAULT_COMMIT,
            out=out,
            python=BASE_PYTHON,
            force=True,
        )


def test_dry_run_119m(tmp_path: Path, built_model: Path) -> None:
    scenario_path = tmp_path / "scenario.json"
    scenario_path.write_text(
        json.dumps(
            {
                "scenario_id": "dry_run_setup",
                "label": "Dry run from a rebuilt model",
                "schema_version": "clews.scenario.v1",
                "harvest": {
                    "level_m3_per_year": 118_977_339.0,
                    "path": "hold",
                    "recovery_fraction": 0.6,
                    "cost_basis": "adopted_rest_of_canada_curve",
                    "supply_curve": "tiered",
                },
                "energy": {
                    "carbon_price_schedule": [
                        {"year": 2021, "price_usd_per_tco2e": 95.0},
                        {"year": 2026, "price_usd_per_tco2e": 200.0},
                    ],
                    "hydro_mode": "cap_2021",
                    "pwr_bio_cap_fraction": 0.05,
                    "nuclear_cap_gw": 0.0,
                    "wind_cost_usd_per_gj": 3.0,
                    "wind_cap_fraction": 0.2,
                    "t3_cap_pj": 39.873714,
                    "t4_cap_pj": 64.265464,
                    "generic_bio_cost_usd_per_gj": 3.0,
                },
                "constraints": {"set_id": "screening_v0"},
                "provenance": {
                    "base_data_commit": "test",
                    "model_commit": DEFAULT_COMMIT,
                    "created_utc": "2026-11-20T00:00:00+00:00",
                },
            }
        ),
        encoding="utf-8",
    )
    output_dir = tmp_path / "output"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "workflows.ui.run_scenario",
            "--scenario",
            str(scenario_path),
            "--out",
            str(output_dir),
            "--dry-run",
            "--model-root",
            str(built_model),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    summary = json.loads((output_dir / "run_summaries.json").read_text("utf-8"))
    assert summary["status"] == "completed"
    assert summary["model"]["source_commit"] == DEFAULT_COMMIT
    assert summary["harmonize"]["exit_code"] == 0
