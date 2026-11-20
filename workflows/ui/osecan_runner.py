"""Milestone-1b OSeCAN adapter for ``clews.scenario.v1``.

The adapter keeps the milestone-1a CLI contract: it reads a parsed scenario,
builds a disposable OSeCAN parameter set, runs ``osecan harmonize`` and the GLPK
solve, and writes normalized artifacts.  Nothing in this module writes to the
public C2070 repository or to the selected model source; a reduced source tree is
copied below the run output directory first.

The canonical ``screening_v0`` constraint set is the authority for defaults and
for the explicitly omitted constraints.  Results produced here are
``screening_only`` and never policy-grade.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from workflows.ui.contracts import HarvestPath, HydroMode, Scenario, SupplyCurveMode
from workflows.ui.storage import atomic_write_json

__all__ = [
    "CANONICAL_SET_PATH",
    "DEFAULT_MODEL_ROOT",
    "MODEL_DIR_NAME",
    "RunnerError",
    "build_manifest",
    "build_parameter_set",
    "compute_supply_caps",
    "copy_model_source",
    "load_canonical_set",
    "normalize_dispatch",
    "parse_solver_status",
    "resolve_model_source",
    "run_harmonize",
    "run_scenario",
    "solve_model",
]

REPO_ROOT = Path(__file__).resolve().parents[2]
CANONICAL_SET_PATH = (
    REPO_ROOT
    / "data/derived/national/clews/osecan/canonical_constraint_set.json"
)
DEFAULT_MODEL_ROOT = Path("/tmp/osecan_emfix")
MODEL_DIR_NAME = "model"
RUN_DIR_NAME = "run"
PARAMS_DIR_NAME = "run/params"
SOLVER_SNIPPET = (
    "import json, sys; from pathlib import Path; "
    "from osecan.pipeline import solve; "
    "run = Path(sys.argv[1]); "
    "result = solve.solve(str(run), str(run / 'params')); "
    "(run / 'status.json').write_text(json.dumps(result, indent=2) + chr(10)); "
    "print(json.dumps(result))"
)

YEARS = tuple(range(2021, 2071))
ZONES = ("A1", "A2", "B1", "B2", "L1", "M1", "N1", "O1", "O2", "Q1", "Q2", "S1", "T1")
TIER_TECHS = ("SUPBCWT1", "SUPBCWT2", "SUPBCWT3", "SUPBCWT4")
BC_ZONAL_TECHS = ("SUPBCWB1", "SUPBCWB2")
SUPPLY_TECHS = ("SUPBCW", *TIER_TECHS, *BC_ZONAL_TECHS)
PWRBIO_TECHS = tuple(f"PWRBIO{zone}" for zone in ZONES)
PWRWND_TECHS = tuple(f"PWRWND{zone}" for zone in ZONES)
PWRURN_TECHS = tuple(f"PWRURN{zone}" for zone in ZONES)
PWRHYD_TECHS = tuple(f"PWRHYD{zone}" for zone in ZONES)
REQUIRED_MODEL_PATHS = (
    "SETs/TECHNOLOGY.csv",
    "SETs/OutputActivityRatio.csv",
    "data/params/CapacityToActivityUnit.csv",
    "resources/osemosys_fast_osecan.txt",
    "resources/preprocess_data.py",
    "src/osecan/pipeline/solve.py",
    "config/otoole.yaml",
)


class RunnerError(RuntimeError):
    """Operational failure with a message safe to surface in the run summary."""


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RunnerError(message)


def _as_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RunnerError(f"{label} must be numeric, got {type(value).__name__}")
    number = float(value)
    if not math.isfinite(number):
        raise RunnerError(f"{label} must be finite, got {value!r}")
    return number


def _as_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RunnerError(f"{label} must be a non-empty string")
    return value


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RunnerError(f"required file is missing: {path}") from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RunnerError(f"cannot read JSON {path}: {exc}") from exc


# ---------------------------------------------------------------------------
# Canonical constraint set
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CanonicalConstraintSet:
    """Parsed, trusted ``screening_v0`` constraint family."""

    set_id: str
    status: str
    policy_grade: bool
    notes: tuple[str, ...]
    gas_output_factor_mt_per_pj: float
    coal_output_factor_mt_per_pj: float
    log_share: float
    tile_factor_odt_per_m3: float
    lhv_gj_per_odt: float
    bc_zonal_total_pj: float
    tier_caps_pj: Mapping[str, float]
    tier_costs_usd_per_gj: Mapping[str, float]
    supply_cost_usd_per_gj: Mapping[str, float]
    energy_defaults: Mapping[str, float | str]
    hydro_cap_2021_pj: Mapping[str, float]
    pwrbio_reference_2050_pj: Mapping[str, float]
    wind_reference_2050_pj: Mapping[str, float]
    nuclear_zone_shares: Mapping[str, float]
    bc_zonal_caps_paths: Mapping[str, str]
    bc_zonal_costs_paths: Mapping[str, str]
    reference_inputs: Mapping[str, str]

    def summary(self) -> dict[str, Any]:
        return {
            "set_id": self.set_id,
            "status": self.status,
            "policy_grade": self.policy_grade,
            "notes": list(self.notes),
        }


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RunnerError(f"{label} must be a JSON object")
    return value


def _number_map(value: Any, label: str, expected_keys: Sequence[str] | None = None) -> dict[str, float]:
    raw = _mapping(value, label)
    parsed = {str(key): _as_number(item, f"{label}.{key}") for key, item in raw.items()}
    if expected_keys is not None and set(parsed) != set(expected_keys):
        missing = sorted(set(expected_keys) - set(parsed))
        extra = sorted(set(parsed) - set(expected_keys))
        raise RunnerError(f"{label} keys differ; missing={missing}, extra={extra}")
    return parsed


def _path_map(value: Any, label: str, expected_keys: Sequence[str]) -> dict[str, str]:
    raw = _mapping(value, label)
    parsed = {str(key): _as_string(item, f"{label}.{key}") for key, item in raw.items()}
    if set(parsed) != set(expected_keys):
        raise RunnerError(f"{label} keys must be {list(expected_keys)}")
    return parsed


def load_canonical_set(path: Path = CANONICAL_SET_PATH) -> CanonicalConstraintSet:
    """Load and validate the canonical screening constraint set.

    Args:
        path: Canonical JSON file.

    Returns:
        Parsed constraint set.

    Raises:
        RunnerError: If the file is missing, malformed, or contradicts the
            documented screening-v0 values.
    """

    payload = _read_json(path)
    root = _mapping(payload, "canonical constraint set")
    if root.get("schema_version") != "clews.constraint_set.v1":
        raise RunnerError("canonical constraint set has an unsupported schema_version")
    if root.get("set_id") != "screening_v0":
        raise RunnerError("runner only accepts canonical set_id screening_v0")
    if root.get("status") != "screening_only":
        raise RunnerError("canonical constraint set must be screening_only")

    notes_raw = root.get("notes")
    if not isinstance(notes_raw, list) or not notes_raw:
        raise RunnerError("canonical constraint set notes must be a non-empty list")
    notes = tuple(_as_string(note, "canonical.notes[]") for note in notes_raw)
    joined = " ".join(notes).lower()
    for phrase in ("reserve-margin", "transmission", "curtailment", "build-rate"):
        if phrase not in joined:
            raise RunnerError(f"canonical notes must explicitly mention {phrase}")

    emission = _mapping(root.get("emission_factors"), "emission_factors")
    gas = _mapping(emission.get("gas"), "emission_factors.gas")
    coal = _mapping(emission.get("coal"), "emission_factors.coal")
    gas_factor = _as_number(gas.get("output_basis_mt_per_pj"), "gas factor")
    coal_factor = _as_number(coal.get("output_basis_mt_per_pj"), "coal factor")
    if abs(gas_factor - 0.1377) > 1e-9 or abs(coal_factor - 0.261) > 1e-9:
        raise RunnerError("canonical corrected output-basis emission factors drifted")

    supply = _mapping(root.get("supply"), "supply")
    energy = _mapping(root.get("energy_defaults"), "energy_defaults")
    references = _mapping(root.get("reference_values"), "reference_values")
    reference_inputs = _mapping(root.get("reference_inputs"), "reference_inputs")

    canonical = CanonicalConstraintSet(
        set_id="screening_v0",
        status="screening_only",
        policy_grade=bool(root.get("policy_grade", False)),
        notes=notes,
        gas_output_factor_mt_per_pj=gas_factor,
        coal_output_factor_mt_per_pj=coal_factor,
        log_share=_as_number(supply.get("log_share"), "supply.log_share"),
        tile_factor_odt_per_m3=_as_number(
            supply.get("tile_factor_odt_per_m3"), "supply.tile_factor_odt_per_m3"
        ),
        lhv_gj_per_odt=_as_number(supply.get("lhv_gj_per_odt"), "supply.lhv_gj_per_odt"),
        bc_zonal_total_pj=_as_number(
            supply.get("bc_zonal_total_pj"), "supply.bc_zonal_total_pj"
        ),
        tier_caps_pj=_number_map(supply.get("tier_caps_pj"), "supply.tier_caps_pj", ("T1", "T2", "T3", "T4")),
        tier_costs_usd_per_gj=_number_map(
            supply.get("tier_variable_costs_usd_per_gj"),
            "supply.tier_variable_costs_usd_per_gj",
            ("T1", "T2", "T3", "T4"),
        ),
        supply_cost_usd_per_gj=_number_map(
            supply.get("cost_usd_per_gj"),
            "supply.cost_usd_per_gj",
            ("adopted_rest_of_canada_curve", "base", "low", "high"),
        ),
        energy_defaults={
            "pwr_bio_cap_fraction": _as_number(
                energy.get("pwr_bio_cap_fraction"), "energy_defaults.pwr_bio_cap_fraction"
            ),
            "nuclear_cap_gw": _as_number(
                energy.get("nuclear_cap_gw"), "energy_defaults.nuclear_cap_gw"
            ),
            "wind_cost_usd_per_gj": _as_number(
                energy.get("wind_cost_usd_per_gj"), "energy_defaults.wind_cost_usd_per_gj"
            ),
            "wind_cap_fraction": _as_number(
                energy.get("wind_cap_fraction"), "energy_defaults.wind_cap_fraction"
            ),
            "generic_bio_cost_usd_per_gj": _as_number(
                energy.get("generic_bio_cost_usd_per_gj"),
                "energy_defaults.generic_bio_cost_usd_per_gj",
            ),
            "t3_cap_pj": _as_number(energy.get("t3_cap_pj"), "energy_defaults.t3_cap_pj"),
            "t4_cap_pj": _as_number(energy.get("t4_cap_pj"), "energy_defaults.t4_cap_pj"),
            "hydro_mode": _as_string(energy.get("hydro_mode"), "energy_defaults.hydro_mode"),
        },
        hydro_cap_2021_pj=_number_map(
            references.get("hydro_cap_2021_pj"),
            "reference_values.hydro_cap_2021_pj",
            PWRHYD_TECHS,
        ),
        pwrbio_reference_2050_pj=_number_map(
            references.get("pwrbio_reference_2050_generation_pj"),
            "reference_values.pwrbio_reference_2050_generation_pj",
            ZONES,
        ),
        wind_reference_2050_pj=_number_map(
            references.get("wind_reference_2050_activity_pj"),
            "reference_values.wind_reference_2050_activity_pj",
            PWRWND_TECHS,
        ),
        nuclear_zone_shares=_number_map(
            references.get("nuclear_zone_shares"),
            "reference_values.nuclear_zone_shares",
            PWRURN_TECHS,
        ),
        bc_zonal_caps_paths=_path_map(
            reference_inputs.get("bc_zonal_caps"),
            "reference_inputs.bc_zonal_caps",
            BC_ZONAL_TECHS,
        ),
        bc_zonal_costs_paths=_path_map(
            reference_inputs.get("bc_zonal_costs"),
            "reference_inputs.bc_zonal_costs",
            BC_ZONAL_TECHS,
        ),
        reference_inputs={
            str(key): _as_string(value, f"reference_inputs.{key}")
            for key, value in reference_inputs.items()
            if isinstance(value, str)
        },
    )
    if abs(sum(canonical.nuclear_zone_shares.values()) - 1.0) > 1e-6:
        raise RunnerError("canonical nuclear zone shares must sum to 1.0")
    if abs(canonical.log_share - 0.762716956451988) > 1e-12:
        raise RunnerError("canonical log_share drifted")
    if abs(canonical.tile_factor_odt_per_m3 - 0.08831776452936206) > 1e-15:
        raise RunnerError("canonical tile factor drifted")
    if abs(canonical.lhv_gj_per_odt - 18.5) > 1e-12:
        raise RunnerError("canonical LHV drifted")
    if abs(canonical.bc_zonal_total_pj - 43.523816) > 1e-9:
        raise RunnerError("canonical BC zonal total drifted")
    return canonical


# ---------------------------------------------------------------------------
# Model source and disposable copy
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ModelSource:
    """Selected disposable OSeCAN source tree."""

    root: Path
    commit: str
    dirty: bool
    python: Path
    otoole: Path | None

    def summary(self) -> dict[str, Any]:
        return {
            "source_root": str(self.root),
            "source_commit": self.commit,
            "source_dirty": self.dirty,
            "python": str(self.python),
            "otoole": str(self.otoole) if self.otoole else None,
        }


def _git_value(root: Path, *arguments: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *arguments],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def _pick_python(root: Path) -> Path:
    candidate = root / ".venv/bin/python"
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return candidate
    if Path(sys.executable).is_file():
        return Path(sys.executable)
    raise RunnerError(f"no Python interpreter found for model source {root}")


def _pick_otoole(root: Path) -> Path | None:
    candidate = root / ".venv/bin/otoole"
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return candidate
    found = shutil.which("otoole")
    return Path(found) if found else None


def resolve_model_source(explicit: Path | None = None) -> ModelSource:
    """Select the disposable model copy, failing closed if none is usable.

    Args:
        explicit: Optional ``--model-root`` override.

    Returns:
        A validated model source.

    Raises:
        RunnerError: If the source is missing or lacks the corrected zonal/tier
            model structure.
    """

    if explicit is not None:
        candidates = (Path(explicit).expanduser(),)
        origin = "--model-root"
    else:
        candidates = (REPO_ROOT / "runtime/osecan_model", DEFAULT_MODEL_ROOT)
        origin = "runtime/osecan_model or /tmp/osecan_emfix"
    for candidate in candidates:
        root = candidate.resolve()
        if not root.is_dir():
            continue
        missing = [name for name in REQUIRED_MODEL_PATHS if not (root / name).is_file()]
        if missing:
            raise RunnerError(
                f"model source {root} is incomplete; missing {missing[0]}"
                + (f" and {len(missing) - 1} more" if len(missing) > 1 else "")
            )
        technologies = _read_technology_set(root)
        missing_tiers = [tech for tech in (*TIER_TECHS, *BC_ZONAL_TECHS) if tech not in technologies]
        if missing_tiers:
            raise RunnerError(
                f"model source {root} lacks the zonal/tier structure; missing {missing_tiers[0]}"
            )
        missing_power = [
            tech
            for tech in (PWRBIO_TECHS[0], PWRWND_TECHS[0], PWRURN_TECHS[0], PWRHYD_TECHS[0])
            if tech not in technologies
        ]
        if missing_power:
            raise RunnerError(
                f"model source {root} lacks {missing_power[0]}; zonal power structure is required"
            )
        dirty_text = _git_value(root, "status", "--porcelain") or ""
        return ModelSource(
            root=root,
            commit=_git_value(root, "rev-parse", "HEAD") or "unknown",
            dirty=bool(dirty_text.strip()),
            python=_pick_python(root),
            otoole=_pick_otoole(root),
        )
    raise RunnerError(
        f"no OSeCAN model copy found ({origin}). Expected a disposable copy with "
        "corrected output-basis emission factors and zonal/tier structure. Set "
        "--model-root or prepare runtime/osecan_model; refusing to fabricate outputs."
    )


def _read_technology_set(model_root: Path) -> set[str]:
    path = model_root / "SETs/TECHNOLOGY.csv"
    with path.open(newline="", encoding="utf-8") as handle:
        return {row["VALUE"] for row in csv.DictReader(handle)}


_COPY_IGNORE = shutil.ignore_patterns(
    ".git",
    ".venv",
    "results",
    "site",
    "docs",
    "notebooks",
    "vis",
    "models",
    "data/external",
    "data/interim",
    "data/mapped",
    "__pycache__",
    "*.pyc",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
)

_COPY_ENTRIES = (
    "src",
    "SETs",
    "config",
    "data/params",
    "resources",
    "pyproject.toml",
    "README.md",
    "LICENSE",
)


def copy_model_source(source: ModelSource, destination: Path) -> Path:
    """Copy the reduced model tree below the run output directory.

    The source tree is never modified.  Only sets, parameters, configuration,
    resources, and source code are copied; virtual environments and prior
    results are excluded.
    """

    if destination.exists():
        raise RunnerError(f"model copy destination already exists: {destination}")
    destination.mkdir(parents=True)
    for entry in _COPY_ENTRIES:
        source_path = source.root / entry
        if not source_path.exists():
            continue
        target = destination / entry
        if source_path.is_dir():
            shutil.copytree(source_path, target, ignore=_COPY_IGNORE)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_path, target)
    (destination / ".osecan-model-copy").write_text(
        json.dumps(
            {
                "source_root": str(source.root),
                "source_commit": source.commit,
                "copied_utc": _utc_now(),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return destination


def tree_sha256(root: Path) -> str:
    """Hash file names and bytes of the copied reduced model tree."""

    digest = hashlib.sha256()
    ignored_parts = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
    files = sorted(
        path
        for path in root.rglob("*")
        if path.is_file()
        and ".osecan-model-copy" != path.name
        and path.suffix != ".pyc"
        and not ignored_parts.intersection(path.parts)
    )
    for path in files:
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# CSV helpers
# ---------------------------------------------------------------------------


def _read_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    if not path.is_file():
        raise RunnerError(f"required CSV is missing: {path}")
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def _write_rows(path: Path, fields: Sequence[str], rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields))
        writer.writeheader()
        writer.writerows(rows)


def _upsert_technology(
    path: Path,
    technology: str,
    rows: Sequence[Mapping[str, Any]],
    *,
    create_fields: Sequence[str] | None = None,
) -> None:
    if path.exists():
        fields, existing = _read_rows(path)
    else:
        fields = list(create_fields or ("REGION", "TECHNOLOGY", "YEAR", "VALUE"))
        existing = []
    kept = [row for row in existing if row.get("TECHNOLOGY") != technology]
    _write_rows(path, fields, kept + list(rows))


def _read_series(path: Path, technology: str) -> dict[int, float]:
    _, rows = _read_rows(path)
    series = {
        int(row["YEAR"]): float(row["VALUE"])
        for row in rows
        if row.get("TECHNOLOGY") == technology
    }
    if not series:
        raise RunnerError(f"{technology} has no rows in {path}")
    return dict(sorted(series.items()))


def _read_tier_definitions(path: Path) -> tuple[dict[str, float], dict[str, float]]:
    _, rows = _read_rows(path)
    caps: dict[str, float] = {}
    costs: dict[str, float] = {}
    labels = {"T1": "SUPBCWT1", "T2": "SUPBCWT2", "T3": "SUPBCWT3", "T4": "SUPBCWT4"}
    for row in rows:
        tier = row.get("tier")
        if tier not in labels:
            continue
        caps[tier] = float(row["incremental_cap_pj"])
        costs[tier] = float(row["variable_cost_usd_per_gj"])
    if set(caps) != set(labels) or set(costs) != set(labels):
        raise RunnerError(f"{path}: tier definitions must contain T1-T4")
    return caps, costs


# ---------------------------------------------------------------------------
# Harvest and supply caps
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SupplyCaps:
    """Parsed supply-cap arithmetic for one scenario."""

    harvest_path: str
    log_m3_by_year: Mapping[int, float]
    national_pj_by_year: Mapping[int, float]
    bc_zonal_pj_by_year: Mapping[int, float]
    rest_of_canada_pj_by_year: Mapping[int, float]
    tier_caps_pj_by_year: Mapping[str, Mapping[int, float]]
    bc_zonal_caps_pj_by_year: Mapping[str, Mapping[int, float]]
    bc_zonal_costs_usd_per_gj_by_year: Mapping[str, Mapping[int, float]]
    checks: Mapping[str, Any]

    def summary(self) -> dict[str, Any]:
        return {
            "harvest_path": self.harvest_path,
            "log_m3_by_year": {str(y): round(v, 6) for y, v in self.log_m3_by_year.items()},
            "national_pj_by_year": {str(y): round(v, 9) for y, v in self.national_pj_by_year.items()},
            "bc_zonal_pj_by_year": {str(y): round(v, 9) for y, v in self.bc_zonal_pj_by_year.items()},
            "rest_of_canada_pj_by_year": {
                str(y): round(v, 9) for y, v in self.rest_of_canada_pj_by_year.items()
            },
            "tier_caps_pj_by_year": {
                tech: {str(y): round(v, 9) for y, v in series.items()}
                for tech, series in self.tier_caps_pj_by_year.items()
            },
            "checks": dict(self.checks),
        }


def resolve_harvest_log_m3(scenario: Scenario, canonical: CanonicalConstraintSet) -> dict[int, float]:
    """Return the scenario's log-volume path in m3/year."""

    harvest = scenario.harvest
    if harvest.path == HarvestPath.CUSTOM:
        return {year: float(harvest.level_m3_per_year) for year in YEARS}

    reference_path = REPO_ROOT / canonical.reference_inputs["harvest_reference_paths"]
    _, rows = _read_rows(reference_path)
    by_year = {int(row["year"]): row for row in rows}
    if set(by_year) != set(YEARS):
        raise RunnerError(f"{reference_path}: expected years 2021-2070")
    column = {
        HarvestPath.OBSERVED: "observed_2021_2024_m3",
        HarvestPath.HOLD: "base_hold_2024_m3",
        HarvestPath.LOW: "low_100m_m3",
        HarvestPath.HIGH: "high_150m_m3",
    }[harvest.path]
    series: dict[int, float] = {}
    for year in YEARS:
        raw = by_year[year].get(column, "").strip()
        if not raw and harvest.path == HarvestPath.OBSERVED:
            raw = by_year[year].get("base_hold_2024_m3", "").strip()
        if not raw:
            raise RunnerError(
                f"{reference_path}: {column} has no value for {year}; the observed path "
                "only covers 2021-2024 and is extended with the 2024 hold"
            )
        series[year] = float(raw)
    return series


def _tier_allocation(
    rest_pj: float,
    *,
    t1_cap: float,
    t2_cap: float,
    t3_cap: float,
    t3_limit: float,
    t4_limit: float,
    year: int,
) -> dict[str, float]:
    t1 = min(t1_cap, rest_pj)
    t2 = min(t2_cap, rest_pj - t1)
    t3 = min(t3_cap, t3_limit, rest_pj - t1 - t2)
    t4 = rest_pj - t1 - t2 - t3
    if t4 < -1e-9:
        raise RunnerError(f"{year}: negative T4 residual {t4}")
    if t4 > t4_limit + 1e-6:
        raise RunnerError(
            f"{year}: tier caps do not tile the rest-of-Canada cap; T4 residual "
            f"{t4:.9f} PJ exceeds scenario t4_cap_pj {t4_limit:.9f}. Raise t4_cap_pj "
            "or use a smaller harvest level."
        )
    return {"T1": t1, "T2": t2, "T3": t3, "T4": t4}


def compute_supply_caps(
    scenario: Scenario,
    canonical: CanonicalConstraintSet,
    *,
    repo_root: Path = REPO_ROOT,
) -> SupplyCaps:
    """Convert harvest to PJ and split it into BC zonal and T1-T4 caps.

    The arithmetic is intentionally simple and fail-closed: a cap set that does
    not tile the national total raises rather than silently dropping biomass.
    """

    if scenario.harvest.recovery_fraction <= 0:
        raise RunnerError("harvest.recovery_fraction must be positive")
    logs = resolve_harvest_log_m3(scenario, canonical)
    pj: dict[int, float] = {}
    for year, log_m3 in logs.items():
        total_m3 = log_m3 / canonical.log_share
        odt = total_m3 * canonical.tile_factor_odt_per_m3
        pj[year] = odt * float(scenario.harvest.recovery_fraction) * canonical.lhv_gj_per_odt / 1_000_000

    tier_caps, tier_costs = _read_tier_definitions(
        repo_root / canonical.reference_inputs["tier_definitions"]
    )
    for tier in ("T1", "T2", "T3", "T4"):
        if abs(tier_caps[tier] - canonical.tier_caps_pj[tier]) > 1e-6:
            raise RunnerError(f"tier definition {tier} drifted from canonical set")
        if abs(tier_costs[tier] - canonical.tier_costs_usd_per_gj[tier]) > 1e-6:
            raise RunnerError(f"tier cost {tier} drifted from canonical set")

    bc_caps: dict[str, dict[int, float]] = {}
    bc_costs: dict[str, dict[int, float]] = {}
    for technology in BC_ZONAL_TECHS:
        bc_caps[technology] = _read_series(
            repo_root / canonical.bc_zonal_caps_paths[technology], technology
        )
        bc_costs[technology] = _read_series(
            repo_root / canonical.bc_zonal_costs_paths[technology], technology
        )
        if set(bc_caps[technology]) != set(YEARS):
            raise RunnerError(f"{technology} caps do not cover 2021-2070")

    bc_total = {
        year: sum(bc_caps[technology][year] for technology in BC_ZONAL_TECHS)
        for year in YEARS
    }
    if abs(bc_total[2028] - canonical.bc_zonal_total_pj) > 1e-6:
        raise RunnerError(
            f"BC zonal 2028 total {bc_total[2028]} drifted from "
            f"{canonical.bc_zonal_total_pj}"
        )

    rest = {year: pj[year] - bc_total[year] for year in YEARS}
    negative = sorted(year for year, value in rest.items() if value < -1e-9)
    if negative:
        raise RunnerError(f"BC zonal caps exceed the national total in {negative[:3]}")

    all_tier_caps: dict[str, dict[int, float]] = {tech: {} for tech in TIER_TECHS}
    for year in YEARS:
        allocation = _tier_allocation(
            rest[year],
            t1_cap=canonical.tier_caps_pj["T1"],
            t2_cap=canonical.tier_caps_pj["T2"],
            t3_cap=canonical.tier_caps_pj["T3"],
            t3_limit=float(scenario.energy.t3_cap_pj),
            t4_limit=float(scenario.energy.t4_cap_pj),
            year=year,
        )
        for tier, technology in zip(("T1", "T2", "T3", "T4"), TIER_TECHS):
            all_tier_caps[technology][year] = allocation[tier]
        tier_sum = sum(allocation.values())
        if abs(tier_sum - rest[year]) > 1e-7:
            raise RunnerError(f"{year}: tier sum {tier_sum} != rest {rest[year]}")

    checks = {
        "bc_zonal_2028_pj": round(bc_total[2028], 9),
        "bc_zonal_matches_canonical": abs(bc_total[2028] - canonical.bc_zonal_total_pj) < 1e-6,
        "tier_sum_equals_rest_all_years": all(
            abs(sum(all_tier_caps[tech][year] for tech in TIER_TECHS) - rest[year]) < 1e-7
            for year in YEARS
        ),
        "national_equals_tiers_plus_bc_all_years": all(
            abs(
                sum(all_tier_caps[tech][year] for tech in TIER_TECHS)
                + bc_total[year]
                - pj[year]
            )
            < 1e-7
            for year in YEARS
        ),
        "tier_cap_sum_pj": round(sum(canonical.tier_caps_pj.values()), 9),
    }
    if not checks["tier_sum_equals_rest_all_years"]:
        raise RunnerError("tier-sum check failed")
    if not checks["national_equals_tiers_plus_bc_all_years"]:
        raise RunnerError("national cap reconciliation failed")

    return SupplyCaps(
        harvest_path=str(scenario.harvest.path),
        log_m3_by_year=logs,
        national_pj_by_year=pj,
        bc_zonal_pj_by_year=bc_total,
        rest_of_canada_pj_by_year=rest,
        tier_caps_pj_by_year=all_tier_caps,
        bc_zonal_caps_pj_by_year=bc_caps,
        bc_zonal_costs_usd_per_gj_by_year=bc_costs,
        checks=checks,
    )


# ---------------------------------------------------------------------------
# Parameter-set generation
# ---------------------------------------------------------------------------


def _cost_multiplier(scenario: Scenario, canonical: CanonicalConstraintSet) -> float:
    basis = str(scenario.harvest.cost_basis)
    base = canonical.supply_cost_usd_per_gj["base"]
    if basis in ("adopted_rest_of_canada_curve", "base"):
        return 1.0
    if basis == "low":
        return canonical.supply_cost_usd_per_gj["low"] / base
    if basis == "high":
        return canonical.supply_cost_usd_per_gj["high"] / base
    if basis == "custom":
        raise RunnerError(
            "harvest.cost_basis=custom is not representable in clews.scenario.v1: "
            "the contract carries no custom cost value"
        )
    raise RunnerError(f"unsupported cost basis {basis!r}")


def _carbon_price_series(scenario: Scenario) -> dict[int, float]:
    points = sorted(scenario.energy.carbon_price_schedule, key=lambda point: point.year)
    series: dict[int, float] = {}
    for year in YEARS:
        applicable = 0.0
        for point in points:
            if point.year <= year:
                applicable = float(point.price_usd_per_tco2e)
            else:
                break
        series[year] = applicable
    return series


def _write_carbon_price(path: Path, scenario: Scenario) -> dict[int, float]:
    series = _carbon_price_series(scenario)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_rows(
        path,
        ["REGION", "EMISSION", "YEAR", "VALUE"],
        [
            {"REGION": "CAN", "EMISSION": "CO2", "YEAR": year, "VALUE": repr(value)}
            for year, value in series.items()
        ],
    )
    return series


def build_parameter_set(
    model_root: Path,
    scenario: Scenario,
    canonical: CanonicalConstraintSet,
    output_dir: Path,
    *,
    repo_root: Path = REPO_ROOT,
) -> tuple[Path, SupplyCaps, dict[str, Any]]:
    """Write one run's OSeCAN parameter directory and return its accounting.

    Args:
        model_root: Disposable model copy below the run output directory.
        scenario: Parsed scenario.
        canonical: Parsed canonical constraint set.
        output_dir: Run output directory.
        repo_root: Repository root holding the reference CSVs.

    Returns:
        ``(params_dir, supply_caps, generation_summary)``.

    Raises:
        RunnerError: If the model structure or cap arithmetic is invalid.
    """

    supply = compute_supply_caps(scenario, canonical, repo_root=repo_root)
    params_dir = output_dir / PARAMS_DIR_NAME
    if params_dir.exists():
        shutil.rmtree(params_dir)
    params_dir.mkdir(parents=True)
    base_params = model_root / "data/params"
    for source in sorted(base_params.glob("*.csv")):
        shutil.copyfile(source, params_dir / source.name)

    # Required parameter files absent from a minimal placeholder set.
    for name, fields in (
        ("TotalAnnualMaxCapacity.csv", ("REGION", "TECHNOLOGY", "YEAR", "VALUE")),
        ("TotalAnnualMaxCapacityInvestment.csv", ("REGION", "TECHNOLOGY", "YEAR", "VALUE")),
        ("TotalTechnologyAnnualActivityLowerLimit.csv", ("REGION", "TECHNOLOGY", "YEAR", "VALUE")),
    ):
        target = params_dir / name
        if not target.exists():
            _write_rows(target, fields, [])

    multiplier = _cost_multiplier(scenario, canonical)
    upper_path = params_dir / "TotalTechnologyAnnualActivityUpperLimit.csv"
    cost_path = params_dir / "VariableCost.csv"

    tiered = scenario.harvest.supply_curve == SupplyCurveMode.TIERED
    if tiered:
        for tier, technology in zip(("T1", "T2", "T3", "T4"), TIER_TECHS):
            cost = canonical.tier_costs_usd_per_gj[tier] * multiplier
            _upsert_technology(
                upper_path,
                technology,
                [
                    {"REGION": "CAN", "TECHNOLOGY": technology, "YEAR": year, "VALUE": repr(value)}
                    for year, value in supply.tier_caps_pj_by_year[technology].items()
                ],
            )
            _upsert_technology(
                cost_path,
                technology,
                [
                    {
                        "REGION": "CAN",
                        "TECHNOLOGY": technology,
                        "MODE_OF_OPERATION": "1",
                        "YEAR": year,
                        "VALUE": repr(cost),
                    }
                    for year in YEARS
                ],
                create_fields=("REGION", "TECHNOLOGY", "MODE_OF_OPERATION", "YEAR", "VALUE"),
            )
        _upsert_technology(
            upper_path,
            "SUPBCW",
            [
                {"REGION": "CAN", "TECHNOLOGY": "SUPBCW", "YEAR": year, "VALUE": "0.0"}
                for year in YEARS
            ],
        )
    else:
        national_cost = canonical.supply_cost_usd_per_gj["base"] * multiplier
        for technology in ("SUPBCW", *TIER_TECHS):
            _upsert_technology(
                upper_path,
                technology,
                [
                    {"REGION": "CAN", "TECHNOLOGY": technology, "YEAR": year, "VALUE": "0.0"}
                    for year in YEARS
                ],
            )
        _upsert_technology(
            upper_path,
            "SUPBCW",
            [
                {
                    "REGION": "CAN",
                    "TECHNOLOGY": "SUPBCW",
                    "YEAR": year,
                    "VALUE": repr(supply.rest_of_canada_pj_by_year[year]),
                }
                for year in YEARS
            ],
        )
        _upsert_technology(
            cost_path,
            "SUPBCW",
            [
                {
                    "REGION": "CAN",
                    "TECHNOLOGY": "SUPBCW",
                    "MODE_OF_OPERATION": "1",
                    "YEAR": year,
                    "VALUE": repr(national_cost),
                }
                for year in YEARS
            ],
            create_fields=("REGION", "TECHNOLOGY", "MODE_OF_OPERATION", "YEAR", "VALUE"),
        )

    for technology in BC_ZONAL_TECHS:
        _upsert_technology(
            upper_path,
            technology,
            [
                {
                    "REGION": "CAN",
                    "TECHNOLOGY": technology,
                    "YEAR": year,
                    "VALUE": repr(supply.bc_zonal_caps_pj_by_year[technology][year]),
                }
                for year in YEARS
            ],
        )
        _upsert_technology(
            cost_path,
            technology,
            [
                {
                    "REGION": "CAN",
                    "TECHNOLOGY": technology,
                    "MODE_OF_OPERATION": "1",
                    "YEAR": year,
                    "VALUE": repr(supply.bc_zonal_costs_usd_per_gj_by_year[technology][year] * multiplier),
                }
                for year in YEARS
            ],
            create_fields=("REGION", "TECHNOLOGY", "MODE_OF_OPERATION", "YEAR", "VALUE"),
        )

    # PWRBIO: fraction of each zone's reference 2050 generation, constant.
    pwrbio_fraction = float(scenario.energy.pwr_bio_cap_fraction)
    for zone, technology in zip(ZONES, PWRBIO_TECHS):
        cap = pwrbio_fraction * canonical.pwrbio_reference_2050_pj[zone]
        _upsert_technology(
            upper_path,
            technology,
            [
                {"REGION": "CAN", "TECHNOLOGY": technology, "YEAR": year, "VALUE": repr(cap)}
                for year in YEARS
            ],
        )

    # Wind: hard zonal cap and integration cost.
    wind_fraction = float(scenario.energy.wind_cap_fraction)
    for technology in PWRWND_TECHS:
        cap = wind_fraction * canonical.wind_reference_2050_pj[technology]
        _upsert_technology(
            upper_path,
            technology,
            [
                {"REGION": "CAN", "TECHNOLOGY": technology, "YEAR": year, "VALUE": repr(cap)}
                for year in YEARS
            ],
        )
        _upsert_technology(
            cost_path,
            technology,
            [
                {
                    "REGION": "CAN",
                    "TECHNOLOGY": technology,
                    "MODE_OF_OPERATION": "1",
                    "YEAR": year,
                    "VALUE": repr(float(scenario.energy.wind_cost_usd_per_gj)),
                }
                for year in YEARS
            ],
            create_fields=("REGION", "TECHNOLOGY", "MODE_OF_OPERATION", "YEAR", "VALUE"),
        )

    # Nuclear: national GW cap allocated by the documented reference shares.
    nuclear_cap = float(scenario.energy.nuclear_cap_gw)
    for capacity_file in ("TotalAnnualMaxCapacity.csv", "TotalAnnualMaxCapacityInvestment.csv"):
        path = params_dir / capacity_file
        for technology in PWRURN_TECHS:
            value = nuclear_cap * canonical.nuclear_zone_shares[technology]
            _upsert_technology(
                path,
                technology,
                [
                    {"REGION": "CAN", "TECHNOLOGY": technology, "YEAR": year, "VALUE": repr(value)}
                    for year in YEARS
                ],
            )

    # Generic non-power bioenergy cost.
    _upsert_technology(
        cost_path,
        "RNWBIO",
        [
            {
                "REGION": "CAN",
                "TECHNOLOGY": "RNWBIO",
                "MODE_OF_OPERATION": "1",
                "YEAR": year,
                "VALUE": repr(float(scenario.energy.generic_bio_cost_usd_per_gj)),
            }
            for year in YEARS
        ],
        create_fields=("REGION", "TECHNOLOGY", "MODE_OF_OPERATION", "YEAR", "VALUE"),
    )

    # Hydro treatment.
    hydro_mode = scenario.energy.hydro_mode
    lower_path = params_dir / "TotalTechnologyAnnualActivityLowerLimit.csv"
    if hydro_mode == HydroMode.NONE:
        for technology in PWRHYD_TECHS:
            _upsert_technology(upper_path, technology, [])
            _upsert_technology(lower_path, technology, [])
    else:
        for technology in PWRHYD_TECHS:
            value = canonical.hydro_cap_2021_pj[technology]
            _upsert_technology(
                upper_path,
                technology,
                [
                    {"REGION": "CAN", "TECHNOLOGY": technology, "YEAR": year, "VALUE": repr(value)}
                    for year in YEARS
                ],
            )
            if hydro_mode == HydroMode.FIXED_2021:
                _upsert_technology(
                    lower_path,
                    technology,
                    [
                        {"REGION": "CAN", "TECHNOLOGY": technology, "YEAR": year, "VALUE": repr(value)}
                        for year in YEARS
                    ],
                )

    carbon_series = _write_carbon_price(params_dir / "EmissionsPenalty.csv", scenario)

    generation = {
        "supply_curve": str(scenario.harvest.supply_curve),
        "cost_multiplier": multiplier,
        "carbon_price_usd_per_tco2e": {str(y): value for y, value in carbon_series.items()},
        "pwr_bio_caps_pj": {
            technology: round(pwrbio_fraction * canonical.pwrbio_reference_2050_pj[zone], 6)
            for zone, technology in zip(ZONES, PWRBIO_TECHS)
        },
        "wind_caps_pj": {
            technology: round(wind_fraction * canonical.wind_reference_2050_pj[technology], 6)
            for technology in PWRWND_TECHS
        },
        "nuclear_caps_gw": {
            technology: round(nuclear_cap * canonical.nuclear_zone_shares[technology], 6)
            for technology in PWRURN_TECHS
        },
        "hydro_mode": str(hydro_mode),
        "hydro_cap_2021_pj": {
            technology: value for technology, value in canonical.hydro_cap_2021_pj.items()
        },
        "generic_bio_cost_usd_per_gj": float(scenario.energy.generic_bio_cost_usd_per_gj),
        "t3_cap_pj": float(scenario.energy.t3_cap_pj),
        "t4_cap_pj": float(scenario.energy.t4_cap_pj),
    }
    return params_dir, supply, generation


# ---------------------------------------------------------------------------
# Solve and normalize
# ---------------------------------------------------------------------------


def _subprocess_env(model_root: Path) -> dict[str, str]:
    env = os.environ.copy()
    existing = env.get("PYTHONPATH")
    parts = [str(model_root / "src"), str(REPO_ROOT)]
    if existing:
        parts.append(existing)
    env["PYTHONPATH"] = os.pathsep.join(parts)
    return env


def _run_checked(command: Sequence[str], *, cwd: Path, log, env: Mapping[str, str] | None = None) -> None:
    log.write("$ " + " ".join(str(part) for part in command) + "\n")
    log.flush()
    completed = subprocess.run(
        [str(part) for part in command],
        cwd=cwd,
        stdout=log,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
        env=dict(env) if env else None,
    )
    if completed.returncode != 0:
        raise RunnerError(
            f"command failed with exit code {completed.returncode}: "
            + " ".join(str(part) for part in command)
        )


def run_harmonize(model_root: Path, params_dir: Path, source: ModelSource) -> dict[str, Any]:
    """Run ``osecan harmonize`` and return its evidence payload."""

    log_path = params_dir.parent / "harmonize.log"
    command = [
        str(source.python),
        "-m",
        "osecan.cli",
        "harmonize",
        "--sets",
        "SETs",
        "--params",
        str(params_dir),
    ]
    with log_path.open("w", encoding="utf-8") as log:
        _run_checked(command, cwd=model_root, log=log, env=_subprocess_env(model_root))
    message = ""
    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    if lines:
        message = lines[-1]
    return {
        "tool": "python -m osecan.cli harmonize",
        "exit_code": 0,
        "message": message,
        "log": str(log_path),
    }


def solve_model(model_root: Path, params_dir: Path, source: ModelSource) -> dict[str, Any]:
    """Assemble, convert, and solve one parameter set in the disposable copy."""

    run_dir = params_dir.parent
    input_dir = run_dir / "input"
    for stale in ("input", "osecan.txt", "osecan_pp.txt", "results_csv", "status.json", "solution.sol", "model.lp"):
        target = run_dir / stale
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
    if source.otoole is None:
        raise RunnerError(
            "otoole executable not found; use a model copy with .venv/bin/otoole "
            "or install otoole in the selected model environment"
        )

    log_path = run_dir / "solve.log"
    env = _subprocess_env(model_root)
    with log_path.open("w", encoding="utf-8") as log:
        _run_checked(
            [
                str(source.python),
                "-m",
                "osecan.pipeline.assemble",
                "--sets",
                "SETs",
                "--params",
                str(params_dir),
                "--config",
                "config/otoole.yaml",
                "--out",
                str(input_dir),
            ],
            cwd=model_root,
            log=log,
            env=env,
        )
        _run_checked(
            [
                str(source.otoole),
                "convert",
                "csv",
                "datafile",
                str(input_dir),
                str(run_dir / "osecan.txt"),
                str(model_root / "config/otoole.yaml"),
            ],
            cwd=model_root,
            log=log,
            env=env,
        )
        _run_checked(
            [str(source.python), "-c", SOLVER_SNIPPET, str(run_dir)],
            cwd=model_root,
            log=log,
            env=env,
        )
    status = parse_solver_status(run_dir / "status.json")
    status["log"] = str(log_path)
    return status


def parse_solver_status(path: Path) -> dict[str, Any]:
    """Read and validate the solver status file."""

    payload = _read_json(path)
    if not isinstance(payload, Mapping):
        raise RunnerError(f"{path}: solver status must be a JSON object")
    status = payload.get("status")
    if status not in ("optimal", "feasible"):
        raise RunnerError(f"{path}: solver did not find a usable solution (status={status!r})")
    objective = payload.get("objective")
    if not isinstance(objective, (int, float)) or isinstance(objective, bool):
        raise RunnerError(f"{path}: solver objective is missing or non-numeric")
    return {
        "status": str(status),
        "objective_musd": float(objective),
        "rows": payload.get("rows"),
        "cols": payload.get("cols"),
        "results_dir": payload.get("results_dir"),
    }


def _series_from_csv(
    path: Path,
    *,
    technology_column: str = "TECHNOLOGY",
    value_column: str = "VALUE",
) -> dict[str, dict[int, float]]:
    _, rows = _read_rows(path)
    series: dict[str, dict[int, float]] = defaultdict(dict)
    for row in rows:
        series[row[technology_column]][int(row["YEAR"])] = float(row[value_column])
    return {technology: dict(sorted(values.items())) for technology, values in series.items()}


def normalize_dispatch(run_dir: Path, scenario_id: str) -> tuple[list[dict[str, Any]], dict[str, dict[int, float]], dict[int, float]]:
    """Return long-format dispatch rows, production series, and emissions."""

    production = _series_from_csv(run_dir / "results_csv/ProductionByTechnologyAnnual.csv")
    emissions = _series_from_csv(
        run_dir / "results_csv/AnnualEmissions.csv", technology_column="EMISSION"
    )
    emissions_by_year: dict[int, float] = defaultdict(float)
    for series in emissions.values():
        for year, value in series.items():
            emissions_by_year[year] += value

    rows: list[dict[str, Any]] = []
    for technology, series in sorted(production.items()):
        if technology in SUPPLY_TECHS:
            category = "supply"
            zone = "CAN" if technology in ("SUPBCW", *TIER_TECHS) else technology[-2:]
        elif technology == "RNWBIO":
            category = "supply"
            zone = "CAN"
        elif technology.startswith(("PWR", "TRN")):
            category = "dispatch"
            zone = technology[-2:] if technology[-2:] in ZONES else "CAN"
        else:
            category = "other"
            zone = "CAN"
        for year, value in sorted(series.items()):
            rows.append(
                {
                    "run": scenario_id,
                    "technology": technology,
                    "category": category,
                    "zone": zone,
                    "year": year,
                    "value_pj": round(value, 6),
                }
            )
    return rows, production, dict(sorted(emissions_by_year.items()))


def write_dispatch(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    _write_rows(path, ["run", "technology", "category", "zone", "year", "value_pj"], rows)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact(path: Path, output_dir: Path, kind: str) -> dict[str, Any]:
    return {
        "path": path.relative_to(output_dir).as_posix(),
        "kind": kind,
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def build_manifest(
    *,
    scenario: Scenario,
    canonical: CanonicalConstraintSet,
    source: ModelSource,
    model_copy: Path,
    output_dir: Path,
    supply: SupplyCaps,
    generation: Mapping[str, Any],
    solve: Mapping[str, Any] | None,
    artifacts: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build the run manifest with provenance and screening labels."""

    return {
        "schema_version": "clews.run_manifest.v1",
        "generated_utc": _utc_now(),
        "status": "screening_only",
        "policy_grade": False,
        "scenario": scenario.model_dump(mode="json"),
        "constraint_set": canonical.summary(),
        "model": {
            **source.summary(),
            "copy_root": str(model_copy),
            "copy_tree_sha256": tree_sha256(model_copy),
            "emission_factor_basis": "Mt CO2 per PJ output activity",
            "gas_output_factor_mt_per_pj": canonical.gas_output_factor_mt_per_pj,
            "coal_output_factor_mt_per_pj": canonical.coal_output_factor_mt_per_pj,
            "solver": "GLPK via swiglpk",
        },
        "supply": supply.summary(),
        "generation": dict(generation),
        "solve": dict(solve) if solve else None,
        "artifacts": list(artifacts),
    }


def run_scenario(
    scenario: Scenario,
    output_dir: Path,
    *,
    model_root: Path | None = None,
    dry_run: bool = False,
    repo_root: Path = REPO_ROOT,
) -> dict[str, Any]:
    """Build, validate, and optionally solve one scenario.

    Args:
        scenario: Parsed scenario.
        output_dir: Writable run output directory.
        model_root: Optional model source override.
        dry_run: Build and harmonize parameters but do not solve.
        repo_root: Repository root holding reference data.

    Returns:
        Normalized run summary.

    Raises:
        RunnerError: On any operational or validation failure.
    """

    canonical = load_canonical_set(repo_root / "data/derived/national/clews/osecan/canonical_constraint_set.json")
    if scenario.constraints.set_id != canonical.set_id:
        raise RunnerError(
            f"scenario constraints.set_id={scenario.constraints.set_id!r} is not supported; "
            f"this runner implements {canonical.set_id!r}"
        )
    source = resolve_model_source(model_root)
    output_dir.mkdir(parents=True, exist_ok=True)
    model_copy = output_dir / MODEL_DIR_NAME
    if model_copy.exists():
        if not (model_copy / ".osecan-model-copy").is_file():
            raise RunnerError(
                f"run output contains an unmarked model copy; refusing to replace it: {model_copy}"
            )
        shutil.rmtree(model_copy)
    copy_model_source(source, model_copy)
    params_dir, supply, generation = build_parameter_set(
        model_copy, scenario, canonical, output_dir, repo_root=repo_root
    )

    harmonize = run_harmonize(model_copy, params_dir, source)
    solve: dict[str, Any] | None = None
    dispatch_rows: list[dict[str, Any]] = []
    emissions_by_year: dict[int, float] = {}
    if not dry_run:
        solve = solve_model(model_copy, params_dir, source)
        dispatch_rows, _production, emissions_by_year = normalize_dispatch(
            output_dir / RUN_DIR_NAME, scenario.scenario_id
        )
        write_dispatch(output_dir / "dispatch_by_zone.csv", dispatch_rows)

    supply_caps_path = output_dir / "supply_caps.json"
    atomic_write_json(
        supply_caps_path,
        {
            "schema_version": "clews.supply_caps.v1",
            "constraint_set": canonical.summary(),
            "supply": supply.summary(),
            "generation": generation,
        },
    )

    artifacts: list[dict[str, Any]] = [
        _artifact(supply_caps_path, output_dir, "supply_caps"),
        _artifact(params_dir / "TotalTechnologyAnnualActivityUpperLimit.csv", output_dir, "parameter"),
        _artifact(params_dir / "VariableCost.csv", output_dir, "parameter"),
    ]
    if not dry_run:
        artifacts.extend(
            (
                _artifact(output_dir / "dispatch_by_zone.csv", output_dir, "dispatch"),
                _artifact(output_dir / RUN_DIR_NAME / "status.json", output_dir, "solver_status"),
                _artifact(output_dir / RUN_DIR_NAME / "results_csv/AnnualEmissions.csv", output_dir, "emissions"),
            )
        )

    manifest = build_manifest(
        scenario=scenario,
        canonical=canonical,
        source=source,
        model_copy=model_copy,
        output_dir=output_dir,
        supply=supply,
        generation=generation,
        solve=solve,
        artifacts=artifacts,
    )
    manifest_path = output_dir / "manifest.json"
    atomic_write_json(manifest_path, manifest)
    artifacts.append(_artifact(manifest_path, output_dir, "manifest"))

    emissions_total = round(sum(emissions_by_year.values()), 6)
    summary: dict[str, Any] = {
        "schema_version": "clews.scenario.v1",
        "status": "completed",
        "scenario_id": scenario.scenario_id,
        "scenario_label": scenario.label,
        "scenario_path": None,
        "generated_utc": _utc_now(),
        "message": (
            "Dry run: parameters generated and harmonized; no OSeCAN solve was run."
            if dry_run
            else "Screening-only OSeCAN solve completed."
        ),
        "dry_run": dry_run,
        "screening_only": True,
        "policy_grade": False,
        "constraints": canonical.summary(),
        "model": {
            "source_root": str(source.root),
            "source_commit": source.commit,
            "source_dirty": source.dirty,
            "copy_tree_sha256": manifest["model"]["copy_tree_sha256"],
        },
        "solver_status": solve["status"] if solve else None,
        "objective_musd": solve["objective_musd"] if solve else None,
        "emissions_total_mt": emissions_total if solve else None,
        "emissions_2050_mt": round(emissions_by_year.get(2050, 0.0), 6) if solve else None,
        "emissions_mt_by_year": {str(y): round(v, 6) for y, v in emissions_by_year.items()},
        "supply": {
            "harvest_path": supply.harvest_path,
            "harvest_level_m3_per_year": float(scenario.harvest.level_m3_per_year),
            "national_total_pj_2050": round(supply.national_pj_by_year[2050], 6),
            "bc_zonal_pj_2050": round(supply.bc_zonal_pj_by_year[2050], 6),
            "rest_of_canada_pj_2050": round(supply.rest_of_canada_pj_by_year[2050], 6),
            "tier_caps_pj_2050": {
                technology: round(series[2050], 6)
                for technology, series in supply.tier_caps_pj_by_year.items()
            },
            "checks": dict(supply.checks),
        },
        "harmonize": harmonize,
        "artifacts": artifacts,
        "manifest": "manifest.json",
        "dispatch_by_zone": None if dry_run else "dispatch_by_zone.csv",
    }
    if not dry_run:
        summary["dispatch_rows"] = len(dispatch_rows)
    return summary
