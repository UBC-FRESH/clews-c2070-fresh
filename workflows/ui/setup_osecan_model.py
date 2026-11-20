"""Rebuild the disposable OSeCAN model used by the screening runner.

The parent repository does not vendor ``deltae/C2070``.  This script creates
``runtime/osecan_model`` (or ``--out``) by checking out the public base commit,
applying the three committed UI patches in ``workflows/ui/patches``, rebuilding
the OSeMOSYS sets and placeholder parameters, and verifying the structure the
runner depends on.  It never pushes or commits to the public OSeCAN repository.

Example::

    .venv/bin/python -m workflows.ui.setup_osecan_model \
        --base /tmp/osecan_src \
        --out runtime/osecan_model \
        --python /tmp/osecan_src/.venv/bin/python \
        --external-data /tmp/osecan_src/data/external

The pinned CER Energy Futures 2026 demand file is copied from
``--external-data`` when available and otherwise downloaded and SHA-256
verified.  ``--skip-demand`` keeps the public base's 10 PJ placeholder demand
for structure-only checks.  The rebuilt tree is deliberately left dirty
relative to the base commit: the
patches are applied to the working tree and recorded by SHA-256 in
``model_manifest.json``.  A run's manifest therefore identifies both the public
base commit and the exact patch bytes that produced the model.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from workflows.ui.model_tree import tree_sha256

__all__ = [
    "DEFAULT_COMMIT",
    "DEFAULT_OUT",
    "DEFAULT_SOURCE",
    "PATCHES",
    "PATCH_DIR",
    "SetupError",
    "main",
    "setup_model",
]

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE = "https://github.com/deltae/C2070.git"
DEFAULT_COMMIT = "40d5b43bb3f8b3f69c16e953144d35eeed5f0f44"
DEFAULT_OUT = REPO_ROOT / "runtime/osecan_model"
PATCH_DIR = Path(__file__).resolve().parent / "patches"
MANIFEST_NAME = "model_manifest.json"


@dataclass(frozen=True, slots=True)
class Patch:
    """A committed source patch and its expected SHA-256 digest."""

    file_name: str
    sha256: str


PATCHES = (
    Patch(
        "01_emission_factors.patch",
        "f89dc52214ae40c4d8567192e15e1228852cc85d684d543a71466b120240ed91",
    ),
    Patch(
        "02_zonal_bc.patch",
        "2fa35791d2cd82fbf67ca81a4f5c6f03c37c3df33a3a80debeff1d51bf19f26b",
    ),
    Patch(
        "03_tiered_supply.patch",
        "54b466ae9c803808a6541a8cdc17946c9674541945f1aacc7b6b4a6fdb4a8cdb",
    ),
)

PATCHED_FILES = frozenset(
    {
        "src/osecan/data/build_params.py",
        "src/osecan/model_structure.py",
        "src/osecan/sets_n_ratios.py",
    }
)
DEMAND_SCENARIO = "Current Measures"
DEMAND_URL = "https://www.cer-rec.gc.ca/open/energy/energyfutures2026/end-use-demand-2026.csv"
DEMAND_SHA256 = "ec178ec02c3f5560b98056232d1fc9e705f243a31744d557e8a2442a290d70f0"
DEMAND_RELATIVE = Path("data/external/cer_ef2026_end_use_demand/end-use-demand-2026.csv")
TIER_TECHS = ("SUPBCWT1", "SUPBCWT2", "SUPBCWT3", "SUPBCWT4")
BC_ZONAL_TECHS = ("SUPBCWB1", "SUPBCWB2")
BC_ZONAL_FUELS = ("BCWB1", "BCWB2")
GAS_TECHS = ("PWRNGSN1", "PWRNGSQ1", "PWRNGSQ2", "PWRNGST1")
GAS_OUTPUT_FACTOR = 0.1377
COAL_OUTPUT_FACTOR = 0.261
RAW_FUEL_FACTORS = (0.051, 0.09)


class SetupError(RuntimeError):
    """A rebuild step failed and the output tree must not be trusted."""


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run(
    command: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str] | None = None,
) -> str:
    """Run a command, returning combined output and failing loudly."""

    completed = subprocess.run(
        list(command),
        cwd=cwd,
        env=dict(env) if env else None,
        capture_output=True,
        text=True,
        check=False,
    )
    output = f"{completed.stdout}{completed.stderr}"
    if completed.returncode != 0:
        tail = "\n".join(output.strip().splitlines()[-20:])
        raise SetupError(
            f"command failed with exit code {completed.returncode}: "
            + " ".join(str(part) for part in command)
            + (f"\n{tail}" if tail else "")
        )
    return output


def _git(root: Path, *arguments: str) -> str:
    return _run(["git", "-C", str(root), *arguments], cwd=root).strip()


def _clone_base(base: str, commit: str, staging: Path) -> Path:
    """Clone ``base`` and detach at ``commit`` below ``staging``."""

    if shutil.which("git") is None:
        raise SetupError("git is required to rebuild the OSeCAN model")
    repository = staging / "repo"
    _run(
        ["git", "clone", "--quiet", "--no-checkout", "--", base, str(repository)],
        cwd=staging,
    )
    _run(["git", "-C", str(repository), "checkout", "--quiet", "--detach", commit], cwd=repository)
    actual = _git(repository, "rev-parse", "HEAD")
    if actual != commit:
        raise SetupError(f"checked out {actual}, expected base commit {commit}")
    _git(repository, "diff", "--quiet")
    return repository


def _apply_patches(repository: Path) -> list[dict[str, str]]:
    """Apply the committed patches in order and verify their hashes."""

    applied: list[dict[str, str]] = []
    for patch in PATCHES:
        path = PATCH_DIR / patch.file_name
        if not path.is_file():
            raise SetupError(f"patch is missing: {path}")
        digest = _sha256_file(path)
        if digest != patch.sha256:
            raise SetupError(
                f"patch hash mismatch for {path.name}: got {digest}, "
                f"expected {patch.sha256}"
            )
        _run(["git", "-C", str(repository), "apply", "--check", str(path)], cwd=repository)
        _run(["git", "-C", str(repository), "apply", str(path)], cwd=repository)
        applied.append({"file": path.name, "sha256": digest})

    changed = frozenset(
        line for line in _git(repository, "diff", "--name-only").splitlines() if line
    )
    unexpected = sorted(changed - PATCHED_FILES)
    if unexpected:
        raise SetupError(f"patches changed unexpected files: {unexpected}")
    missing = sorted(PATCHED_FILES - changed)
    if missing:
        raise SetupError(f"patches did not change expected files: {missing}")
    _git(repository, "diff", "--check")
    return applied


def _select_python(explicit: Path | None, repository: Path) -> Path:
    """Choose an interpreter that can run the OSeCAN build commands."""

    candidates: list[Path] = []
    if explicit is not None:
        # Keep the venv path: resolving the python symlink would discard the
        # venv's site-packages and silently fall back to the parent interpreter.
        candidates.append(explicit.expanduser().absolute())
    candidates.append(repository / ".venv/bin/python")
    if os.environ.get("OSECAN_PYTHON"):
        candidates.append(Path(os.environ["OSECAN_PYTHON"]).expanduser())
    candidates.append(Path(sys.executable))

    for candidate in candidates:
        if not candidate.is_file() or not os.access(candidate, os.X_OK):
            continue
        probe = subprocess.run(
            [str(candidate), "-c", "import pandas, typer"],
            capture_output=True,
            text=True,
            check=False,
        )
        if probe.returncode == 0:
            return candidate
    raise SetupError(
        "no Python interpreter with pandas and typer was found; pass "
        "--python /path/to/osecan/.venv/bin/python"
    )


def _has_module(python: Path, module: str) -> bool:
    completed = subprocess.run(
        [str(python), "-c", f"import {module}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.returncode == 0


def _download(url: str, destination: Path) -> None:
    """Download one pinned dataset, failing closed on network errors."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "clews-osecan-setup/1"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response, destination.open(
            "wb"
        ) as handle:
            shutil.copyfileobj(response, handle)
    except OSError as exc:
        raise SetupError(f"cannot download {url}: {exc}") from exc


def _local_demand_source(external_data: Path | None, base: str) -> Path | None:
    """Find a cached CER demand CSV without touching the network."""

    candidates: list[Path] = []
    if external_data is not None:
        external_data = external_data.expanduser()
        candidates.extend(
            (
                external_data / DEMAND_RELATIVE,
                external_data / DEMAND_RELATIVE.relative_to("data/external"),
                external_data,
            )
        )
    base_path = Path(base).expanduser()
    if base_path.is_dir():
        candidates.append(base_path / DEMAND_RELATIVE)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _provision_demand(
    repository: Path,
    python: Path,
    *,
    external_data: Path | None,
    base: str,
) -> dict[str, Any]:
    """Install the pinned CER demand file and run ``osecan clean-demand``."""

    target = repository / DEMAND_RELATIVE
    local = _local_demand_source(external_data, base)
    if local is not None:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(local, target)
        origin = str(local)
    else:
        _download(DEMAND_URL, target)
        origin = DEMAND_URL
    digest = _sha256_file(target)
    if digest != DEMAND_SHA256:
        raise SetupError(
            f"demand source hash mismatch: got {digest}, expected {DEMAND_SHA256}"
        )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(repository / "src")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONHASHSEED"] = "0"
    _run(
        [str(python), "-m", "osecan.cli", "clean-demand", DEMAND_SCENARIO],
        cwd=repository,
        env=env,
    )
    return {
        "basis": f"CER EF2026 {DEMAND_SCENARIO}",
        "source": origin,
        "sha256": digest,
        "scenario": DEMAND_SCENARIO,
    }


def _build(
    repository: Path,
    python: Path,
    *,
    external_data: Path | None,
    base: str,
    skip_demand: bool,
) -> tuple[list[str], dict[str, Any]]:
    """Regenerate SETs, parameters, and demand, then harmonize."""

    env = dict(os.environ)
    env["PYTHONPATH"] = str(repository / "src")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONHASHSEED"] = "0"
    commands = [
        [str(python), "-m", "osecan.cli", "build-sets", "--out", "SETs"],
        [str(python), "-m", "osecan.cli", "build-params", "--out", "data/params"],
    ]
    for command in commands:
        _run(command, cwd=repository, env=env)

    if skip_demand:
        demand = {"basis": "placeholder (10 PJ per fuel)", "source": None, "sha256": None}
    else:
        demand = _provision_demand(
            repository,
            python,
            external_data=external_data,
            base=base,
        )
        commands.append(
            [str(python), "-m", "osecan.cli", "clean-demand", DEMAND_SCENARIO]
        )

    harmonize = [
        str(python),
        "-m",
        "osecan.cli",
        "harmonize",
        "--sets",
        "SETs",
        "--params",
        "data/params",
    ]
    output = _run(harmonize, cwd=repository, env=env)
    if "HARMONIZED" not in output:
        raise SetupError("osecan harmonize did not report HARMONIZED")
    commands.append(harmonize)
    return [" ".join(command) for command in commands], demand


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise SetupError(f"expected generated file is missing: {path}")
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _values(path: Path, column: str = "VALUE") -> set[str]:
    return {row[column] for row in _read_rows(path)}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise SetupError(message)


def _verify_structure(
    repository: Path,
    *,
    expect_cleaned_demand: bool,
) -> dict[str, Any]:
    """Verify the zonal/tier structure and corrected emission factors."""

    technologies = _values(repository / "SETs/TECHNOLOGY.csv")
    for technology in (*TIER_TECHS, *BC_ZONAL_TECHS, "PWRBCWB1", "PWRBCWB2"):
        _require(technology in technologies, f"TECHNOLOGY is missing {technology}")

    fuels = _values(repository / "SETs/FUEL.csv")
    for fuel in BC_ZONAL_FUELS:
        _require(fuel in fuels, f"FUEL is missing {fuel}")

    output_ratios = _read_rows(repository / "SETs/OutputActivityRatio.csv")
    output_by_tech: dict[str, set[str]] = {}
    for row in output_ratios:
        output_by_tech.setdefault(row["TECHNOLOGY"], set()).add(row["FUEL"])
    for tier in TIER_TECHS:
        _require(output_by_tech.get(tier) == {"BCW"}, f"{tier} must output BCW")
    for technology, fuel in zip(BC_ZONAL_TECHS, BC_ZONAL_FUELS):
        _require(output_by_tech.get(technology) == {fuel}, f"{technology} must output {fuel}")

    input_ratios = _read_rows(repository / "SETs/InputActivityRatio.csv")
    input_by_tech: dict[str, set[str]] = {}
    for row in input_ratios:
        input_by_tech.setdefault(row["TECHNOLOGY"], set()).add(row["FUEL"])
    for technology, fuel in (("PWRBCWB1", "BCWB1"), ("PWRBCWB2", "BCWB2")):
        _require(input_by_tech.get(technology) == {fuel}, f"{technology} must consume {fuel}")
    _require(input_by_tech.get("PWRBCWA1") == {"BCW"}, "PWRBCWA1 must keep the national BCW fuel")

    ear = _read_rows(repository / "data/params/EmissionActivityRatio.csv")
    gas_values = {
        float(row["VALUE"]) for row in ear if row["TECHNOLOGY"] in GAS_TECHS
    }
    _require(gas_values == {GAS_OUTPUT_FACTOR}, f"gas EAR must be {GAS_OUTPUT_FACTOR}")
    coal_values = {
        float(row["VALUE"])
        for row in ear
        if row["TECHNOLOGY"].startswith("PWRCOA")
    }
    _require(coal_values == {COAL_OUTPUT_FACTOR}, f"coal EAR must be {COAL_OUTPUT_FACTOR}")
    raw = {float(row["VALUE"]) for row in ear}.intersection(RAW_FUEL_FACTORS)
    _require(not raw, f"fuel-basis emission factors remain in EAR: {sorted(raw)}")

    udc_path = repository / "data/params/UDCMultiplierActivity.csv"
    if udc_path.is_file():
        raw_udc = {
            float(row["VALUE"]) for row in _read_rows(udc_path)
        }.intersection(RAW_FUEL_FACTORS)
        _require(
            not raw_udc,
            f"fuel-basis emission factors remain in UDC multipliers: {sorted(raw_udc)}",
        )

    upper = _read_rows(repository / "data/params/TotalTechnologyAnnualActivityUpperLimit.csv")
    _require(
        not any(row["TECHNOLOGY"] in TIER_TECHS for row in upper),
        "base parameters must not cap tier technologies",
    )
    variable = _read_rows(repository / "data/params/VariableCost.csv")
    _require(
        not any(row["TECHNOLOGY"] in TIER_TECHS for row in variable),
        "base parameters must not price tier technologies",
    )

    demand_rows = _read_rows(repository / "data/params/SpecifiedAnnualDemand.csv")
    demand_values = {float(row["VALUE"]) for row in demand_rows}
    demand_2050_pj = round(
        sum(float(row["VALUE"]) for row in demand_rows if row["YEAR"] == "2050"), 6
    )
    if expect_cleaned_demand:
        _require(
            demand_values != {10.0},
            "demand is still the 10 PJ placeholder; rerun without --skip-demand",
        )
        _require(demand_2050_pj > 0.0, "cleaned demand has no 2050 value")
    else:
        _require(demand_values == {10.0}, "placeholder demand missing under --skip-demand")

    return {
        "technologies": len(technologies),
        "fuels": len(fuels),
        "gas_output_factor_mt_per_pj": GAS_OUTPUT_FACTOR,
        "coal_output_factor_mt_per_pj": COAL_OUTPUT_FACTOR,
        "raw_fuel_factors_absent": True,
        "zonal_bc_fuels": list(BC_ZONAL_FUELS),
        "tier_technologies": list(TIER_TECHS),
        "tier_base_caps_absent": True,
        "tier_base_costs_absent": True,
        "demand_2050_pj": demand_2050_pj,
        "demand_basis": "CER EF2026 Current Measures"
        if expect_cleaned_demand
        else "placeholder",
    }


def _parameter_inventory(repository: Path) -> dict[str, dict[str, Any]]:
    inventory: dict[str, dict[str, Any]] = {}
    for path in sorted((repository / "data/params").glob("*.csv")):
        with path.open(encoding="utf-8") as handle:
            rows = sum(1 for _ in handle) - 1
        inventory[path.name] = {"sha256": _sha256_file(path), "rows": rows}
    return inventory


def _write_manifest(
    repository: Path,
    *,
    base: str,
    commit: str,
    python: Path,
    otoole: Path | None,
    solver_ready: bool,
    patches: list[dict[str, str]],
    commands: list[str],
    checks: dict[str, Any],
    demand: dict[str, Any],
) -> dict[str, Any]:
    manifest = {
        "schema_version": "clews.osecan_model_manifest.v1",
        "generated_utc": _utc_now(),
        "source_repository": base,
        "source_commit": commit,
        "patches": patches,
        "patched_files": sorted(PATCHED_FILES),
        "python": str(python),
        "otoole": str(otoole) if otoole else None,
        "solver_ready": solver_ready,
        "model_tree_sha256": tree_sha256(repository),
        "build_commands": commands,
        "demand": demand,
        "checks": checks,
        "parameter_files": _parameter_inventory(repository),
    }
    (repository / MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def _prepare_output(out: Path, force: bool) -> None:
    if not out.exists():
        return
    if not force:
        raise SetupError(f"output already exists: {out}; pass --force to rebuild")
    if out.is_dir() and not any(out.iterdir()):
        out.rmdir()
        return
    if not (out / MANIFEST_NAME).is_file():
        raise SetupError(
            f"refusing to replace unmarked directory {out}; remove it manually "
            "or choose a different --out"
        )
    shutil.rmtree(out)


def setup_model(
    *,
    base: str,
    commit: str,
    out: Path,
    python: Path | None = None,
    force: bool = False,
    external_data: Path | None = None,
    skip_demand: bool = False,
) -> dict[str, Any]:
    """Rebuild one model tree and return its manifest.

    Args:
        base: Local git repository path or public clone URL.
        commit: Public C2070 commit to check out.
        out: Destination model root.
        python: Optional interpreter with OSeCAN dependencies.
        force: Replace an existing setup tree.
        external_data: Optional cached ``data/external`` directory holding the
            pinned CER demand file.
        skip_demand: Leave the 10 PJ placeholder demand instead of cleaning the
            CER Current Measures path.

    Returns:
        The manifest written to ``out/model_manifest.json``.

    Raises:
        SetupError: On any checkout, patch, build, or verification mismatch.
    """

    out = out.expanduser().resolve()
    _prepare_output(out, force)
    out.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{out.name}.setup-", dir=out.parent))
    try:
        repository = _clone_base(base, commit, staging)
        patches = _apply_patches(repository)
        interpreter = _select_python(python, repository)
        commands, demand = _build(
            repository,
            interpreter,
            external_data=external_data,
            base=base,
            skip_demand=skip_demand,
        )
        checks = _verify_structure(
            repository,
            expect_cleaned_demand=not skip_demand,
        )
        otoole = interpreter.parent / "otoole"
        otoole_path = otoole if otoole.is_file() and os.access(otoole, os.X_OK) else None
        solver_ready = _has_module(interpreter, "swiglpk") and _has_module(interpreter, "otoole")
        manifest = _write_manifest(
            repository,
            base=base,
            commit=commit,
            python=interpreter,
            otoole=otoole_path,
            solver_ready=solver_ready,
            patches=patches,
            commands=commands,
            checks=checks,
            demand=demand,
        )
        os.replace(repository, out)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return manifest


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m workflows.ui.setup_osecan_model",
        description="Rebuild runtime/osecan_model from deltae/C2070 plus UI patches.",
    )
    parser.add_argument(
        "--base",
        default=DEFAULT_SOURCE,
        help="local C2070 git repository or clone URL (default: public origin)",
    )
    parser.add_argument(
        "--commit",
        default=DEFAULT_COMMIT,
        help="public base commit (default: 40d5b43)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help="destination model root (default: runtime/osecan_model)",
    )
    parser.add_argument(
        "--python",
        type=Path,
        default=None,
        help="interpreter with pandas and typer (default: auto-detect)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="replace an existing setup tree",
    )
    parser.add_argument(
        "--external-data",
        type=Path,
        default=None,
        help=(
            "cached OSeCAN data/external directory (or demand CSV); "
            "downloaded when omitted"
        ),
    )
    parser.add_argument(
        "--skip-demand",
        action="store_true",
        help="keep the 10 PJ placeholder demand instead of CER Current Measures",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        manifest = setup_model(
            base=args.base,
            commit=args.commit,
            out=args.out,
            python=args.python,
            force=args.force,
            external_data=args.external_data,
            skip_demand=args.skip_demand,
        )
    except SetupError as exc:
        print(f"setup failed: {exc}", file=sys.stderr)
        return 1
    print(
        f"model ready: {args.out.resolve()} "
        f"(commit {manifest['source_commit'][:12]}, "
        f"tree {manifest['model_tree_sha256'][:12]})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
