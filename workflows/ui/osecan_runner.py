"""Milestone-1b OSeCAN adapter for ``clews.scenario.v1``.

The adapter keeps the milestone-1a CLI contract: it reads a parsed scenario,
builds a disposable OSeCAN parameter set, runs ``osecan harmonize`` and the GLPK
solve, and writes normalized artifacts.  Nothing in this module writes to the
public C2070 repository or to the selected model source; a reduced source tree is
copied below the run output directory first.

The canonical ``screening_v0`` constraint set is the authority for defaults and
for the explicitly omitted constraints.  A separate, strict
``policy_test_v1`` loader adds a national reserve-margin UDC, capacity credits,
build rates, and explicit caps without weakening the screening gate.  Policy
test results are ``draft`` and never policy-grade in Stage 1.
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
from workflows.ui.feasibility import (
    MAX_NORMAL_HARVEST_M3,
    HarvestFeasibility,
    assess_harvest_feasibility,
)
from workflows.ui.model_tree import MODEL_COPY_ENTRIES, tree_sha256
from workflows.ui.storage import atomic_write_json

__all__ = [
    "CANONICAL_SET_PATH",
    "DEFAULT_MODEL_ROOT",
    "MODEL_DIR_NAME",
    "POLICY_SET_ID",
    "POLICY_SET_PATH",
    "PolicyConstraintSet",
    "RunnerError",
    "build_manifest",
    "build_parameter_set",
    "build_policy_parameter_set",
    "compute_supply_caps",
    "copy_model_source",
    "evaluate_harvest_feasibility",
    "load_canonical_set",
    "load_constraint_set",
    "load_policy_set",
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
POLICY_SET_ID = "policy_test_v1"
POLICY_SET_PATH = (
    REPO_ROOT
    / "data/derived/national/clews/osecan/policy_test_v1.json"
)
DEFAULT_MODEL_ROOT = REPO_ROOT / "runtime/osecan_model"
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
PWRTRN_TECHS = tuple(f"PWRTRN{zone}" for zone in ZONES)
CREDIT_ARCHETYPES = ("NGS", "COA", "URN", "HYD", "BIO", "BSW", "BCW", "WND", "SOL", "GEO")
POLICY_UDC = "RM_NATIONAL"
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

    @property
    def screening_only(self) -> bool:
        return True

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
# Policy-test v1 constraint set (schema v2)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TechCapSpec:
    """One explicit technology cap from ``tech_caps``."""

    metric: str
    parameter: str
    schedule: Mapping[int, float] | None
    note: str


@dataclass(frozen=True, slots=True)
class PolicyConstraintSet:
    """Parsed, trusted ``policy_test_v1`` constraint family.

    The set is deliberately separate from :class:`CanonicalConstraintSet`: the
    screening loader stays strict and unchanged, and policy labels are derived
    from this parsed payload rather than a hard-coded screening constant.
    """

    set_id: str
    status: str
    policy_grade: bool
    label: str
    structural_gaps: tuple[str, ...]
    notes: tuple[str, ...]
    reserve_margin: Mapping[int, float]
    peak_factor: Mapping[int, float]
    capacity_credits: Mapping[str, float]
    default_credit: float
    build_rates: Mapping[str, Mapping[int, float]]
    tech_caps: Mapping[str, TechCapSpec]
    hydro_mode: str
    hydro_caps_pj: Mapping[str, float]
    hydro_build_rate: Mapping[int, float]
    nuclear_mode: str
    nuclear_build_rate: Mapping[int, float]
    nuclear_total_cap_gw: Mapping[int, float] | None
    carbon: Mapping[str, Any]
    value_status: str
    source_status: str

    @property
    def screening_only(self) -> bool:
        return False

    def credit_for(self, technology: str) -> float:
        """Return the capacity credit for a generator technology."""

        if technology in self.capacity_credits:
            return self.capacity_credits[technology]
        archetype = technology[3:6] if technology.startswith("PWR") else ""
        return self.capacity_credits.get(archetype, self.default_credit)

    def summary(self) -> dict[str, Any]:
        return {
            "set_id": self.set_id,
            "status": self.status,
            "policy_grade": self.policy_grade,
            "label": self.label,
            "structural_gaps": list(self.structural_gaps),
            "notes": list(self.notes),
            "value_status": self.value_status,
            "source_status": self.source_status,
        }


def _block_status(block: Mapping[str, Any], label: str) -> tuple[str, str]:
    value_status = block.get("value_status")
    source_status = block.get("source_status")
    if value_status not in ("draft", "pending_source", "sourced"):
        raise RunnerError(f"{label}.value_status must be draft, pending_source, or sourced")
    if source_status not in ("pending", "pending_source", "reviewed", "sourced", "rejected"):
        raise RunnerError(
            f"{label}.source_status must be pending, reviewed, sourced, or rejected"
        )
    return str(value_status), str(source_status)


def _year_schedule(
    value: Any,
    label: str,
    *,
    minimum: float = 0.0,
) -> dict[int, float]:
    raw = _mapping(value, label)
    if not raw:
        raise RunnerError(f"{label} schedule must not be empty")
    parsed: dict[int, float] = {}
    for key, item in raw.items():
        try:
            year = int(str(key))
        except ValueError as exc:
            raise RunnerError(f"{label} has a non-integer year key: {key!r}") from exc
        if not 2021 <= year <= 2070:
            raise RunnerError(f"{label} year {year} is outside 2021-2070")
        if year in parsed:
            raise RunnerError(f"{label} has duplicate year {year}")
        number = _as_number(item, f"{label}[{year}]")
        if number < minimum:
            raise RunnerError(f"{label}[{year}] must be >= {minimum}, got {number}")
        parsed[year] = number
    if 2021 not in parsed or 2070 not in parsed:
        raise RunnerError(f"{label} must cover 2021 and 2070 endpoints")
    return dict(sorted(parsed.items()))


def _interpolate(schedule: Mapping[int, float], year: int) -> float:
    if year in schedule:
        return float(schedule[year])
    years = sorted(schedule)
    if year < years[0]:
        return float(schedule[years[0]])
    if year > years[-1]:
        return float(schedule[years[-1]])
    lower = max(item for item in years if item < year)
    upper = min(item for item in years if item > year)
    fraction = (year - lower) / (upper - lower)
    return float(schedule[lower]) + fraction * (float(schedule[upper]) - float(schedule[lower]))


def load_policy_set(path: Path = POLICY_SET_PATH) -> PolicyConstraintSet:
    """Load and strictly validate the ``clews.constraint_set.v2`` policy set.

    Args:
        path: Policy constraint-set JSON.

    Returns:
        Parsed policy set.

    Raises:
        RunnerError: If a required block is missing, a value is malformed, or a
            policy-grade claim is made while inputs are pending.
    """

    payload = _read_json(path)
    root = _mapping(payload, "policy constraint set")
    if root.get("schema_version") != "clews.constraint_set.v2":
        raise RunnerError("policy constraint set has an unsupported schema_version")
    if root.get("set_id") != POLICY_SET_ID:
        raise RunnerError(f"policy constraint set set_id must be {POLICY_SET_ID!r}")
    status = _as_string(root.get("status"), "policy.status")
    if status not in ("draft", "policy_test", "policy_grade"):
        raise RunnerError("policy.status must be draft, policy_test, or policy_grade")
    requested_grade = bool(root.get("policy_grade", False))
    if requested_grade and status != "policy_grade":
        raise RunnerError("policy_grade=true requires status=policy_grade")

    label = _as_string(root.get("label"), "policy.label")
    value_status, source_status = _block_status(root, "policy")
    notes_raw = root.get("notes")
    if not isinstance(notes_raw, list) or not notes_raw:
        raise RunnerError("policy notes must be a non-empty list")
    notes = tuple(_as_string(note, "policy.notes[]") for note in notes_raw)
    gaps_raw = root.get("structural_gaps")
    if not isinstance(gaps_raw, list):
        raise RunnerError("policy.structural_gaps must be a list")
    gaps = tuple(_as_string(gap, "policy.structural_gaps[]") for gap in gaps_raw)
    if status == "policy_grade" and gaps:
        raise RunnerError("policy_grade=true requires structural_gaps=[]")

    required = ("reserve_margin", "capacity_credits", "build_rates", "tech_caps", "hydro", "carbon")
    missing = [name for name in required if name not in root]
    if missing:
        raise RunnerError(f"policy constraint set is missing required block: {missing[0]}")

    # --- reserve margin -------------------------------------------------
    reserve = _mapping(root.get("reserve_margin"), "policy.reserve_margin")
    _block_status(reserve, "policy.reserve_margin")
    if reserve.get("method") != "udc_national_peak_proxy":
        raise RunnerError("reserve_margin.method must be udc_national_peak_proxy")
    if reserve.get("enforcement") != "udc":
        raise RunnerError("reserve_margin.enforcement must be udc")
    rm_schedule = _year_schedule(reserve.get("schedule"), "reserve_margin.schedule")
    peak_schedule = _year_schedule(
        reserve.get("peak_factor"), "reserve_margin.peak_factor", minimum=1.0
    )
    native = _mapping(reserve.get("native_rm3"), "reserve_margin.native_rm3")
    if native.get("authoritative") is not False:
        raise RunnerError("reserve_margin.native_rm3.authoritative must be false")

    # --- capacity credits ----------------------------------------------
    credits_block = _mapping(root.get("capacity_credits"), "policy.capacity_credits")
    _block_status(credits_block, "policy.capacity_credits")
    if credits_block.get("basis") != "fraction_of_nameplate":
        raise RunnerError("capacity_credits.basis must be fraction_of_nameplate")
    default_credit = _as_number(credits_block.get("default"), "capacity_credits.default")
    if not 0.0 <= default_credit <= 1.0:
        raise RunnerError("capacity_credits.default must be in [0,1]")
    by_archetype = _number_map(
        credits_block.get("by_archetype"),
        "capacity_credits.by_archetype",
        CREDIT_ARCHETYPES,
    )
    if any(not 0.0 <= value <= 1.0 for value in by_archetype.values()):
        raise RunnerError("capacity_credits.by_archetype values must be in [0,1]")
    by_technology = _number_map(
        credits_block.get("by_technology", {}), "capacity_credits.by_technology"
    )
    if any(not 0.0 <= value <= 1.0 for value in by_technology.values()):
        raise RunnerError("capacity_credits.by_technology values must be in [0,1]")
    credits = {**by_archetype, **by_technology}

    # --- build rates ----------------------------------------------------
    rates_block = _mapping(root.get("build_rates"), "policy.build_rates")
    _block_status(rates_block, "policy.build_rates")
    if rates_block.get("parameter") != "TotalAnnualMaxCapacityInvestment":
        raise RunnerError("build_rates.parameter must be TotalAnnualMaxCapacityInvestment")
    if rates_block.get("unit") != "GW/yr":
        raise RunnerError("build_rates.unit must be GW/yr")
    rates_raw = _mapping(rates_block.get("by_technology"), "build_rates.by_technology")
    if not rates_raw:
        raise RunnerError("build_rates.by_technology must not be empty")
    build_rates = {
        str(pattern): _year_schedule(value, f"build_rates.by_technology.{pattern}")
        for pattern, value in rates_raw.items()
    }

    # --- explicit technology caps --------------------------------------
    caps_block = _mapping(root.get("tech_caps"), "policy.tech_caps")
    _block_status(caps_block, "policy.tech_caps")
    caps_raw = _mapping(caps_block.get("by_technology"), "tech_caps.by_technology")
    tech_caps: dict[str, TechCapSpec] = {}
    for pattern, raw in caps_raw.items():
        block = _mapping(raw, f"tech_caps.by_technology.{pattern}")
        _block_status(block, f"tech_caps.by_technology.{pattern}")
        metric = _as_string(block.get("metric"), f"tech_caps.{pattern}.metric")
        if metric not in ("activity_pj", "capacity_gw", "build_rate_only"):
            raise RunnerError(f"tech_caps.{pattern}.metric is unsupported")
        parameter = _as_string(block.get("parameter"), f"tech_caps.{pattern}.parameter")
        expected = {
            "activity_pj": "TotalTechnologyAnnualActivityUpperLimit",
            "capacity_gw": "TotalAnnualMaxCapacity",
            "build_rate_only": "TotalAnnualMaxCapacityInvestment",
        }[metric]
        if parameter != expected:
            raise RunnerError(f"tech_caps.{pattern}.parameter must be {expected}")
        schedule = block.get("schedule")
        if metric == "build_rate_only":
            if schedule is not None:
                raise RunnerError(f"tech_caps.{pattern} build_rate_only must use schedule=null")
            parsed_schedule = None
        else:
            parsed_schedule = _year_schedule(schedule, f"tech_caps.{pattern}.schedule")
        tech_caps[str(pattern)] = TechCapSpec(
            metric=metric,
            parameter=parameter,
            schedule=parsed_schedule,
            note=str(block.get("note", "")),
        )

    # --- hydro ----------------------------------------------------------
    hydro_block = _mapping(root.get("hydro"), "policy.hydro")
    _block_status(hydro_block, "policy.hydro")
    hydro_mode = _as_string(hydro_block.get("mode"), "hydro.mode")
    if hydro_mode not in ("none", "cap_2021", "fixed_2021"):
        raise RunnerError("hydro.mode must be none, cap_2021, or fixed_2021")
    hydro_caps = _number_map(
        hydro_block.get("activity_caps_pj"), "hydro.activity_caps_pj", PWRHYD_TECHS
    )
    hydro_build = _year_schedule(
        hydro_block.get("build_rate_gw_per_year"), "hydro.build_rate_gw_per_year"
    )
    hydro_credit = _as_number(hydro_block.get("capacity_credit"), "hydro.capacity_credit")
    if abs(hydro_credit - credits["HYD"]) > 1e-9:
        raise RunnerError("hydro.capacity_credit must agree with capacity_credits.HYD")

    # --- nuclear --------------------------------------------------------
    nuclear = _mapping(root.get("nuclear"), "policy.nuclear")
    _block_status(nuclear, "policy.nuclear")
    nuclear_mode = _as_string(nuclear.get("mode"), "nuclear.mode")
    if nuclear_mode not in ("build_rate_only", "no_new_nuclear"):
        raise RunnerError("nuclear.mode must be build_rate_only or no_new_nuclear")
    nuclear_build = _year_schedule(
        nuclear.get("new_build_rate_gw_per_year"), "nuclear.new_build_rate_gw_per_year"
    )
    if nuclear_mode == "no_new_nuclear" and any(value != 0.0 for value in nuclear_build.values()):
        raise RunnerError("nuclear.mode=no_new_nuclear requires a zero build-rate schedule")
    nuclear_total_raw = nuclear.get("total_capacity_cap_gw")
    nuclear_total = (
        None
        if nuclear_total_raw is None
        else _year_schedule(nuclear_total_raw, "nuclear.total_capacity_cap_gw")
    )
    nuclear_credit = _as_number(nuclear.get("capacity_credit"), "nuclear.capacity_credit")
    if abs(nuclear_credit - credits["URN"]) > 1e-9:
        raise RunnerError("nuclear.capacity_credit must agree with capacity_credits.URN")

    # The hydro and nuclear blocks must not silently contradict the generic
    # tech_caps/build_rates tables.
    for technology in PWRHYD_TECHS:
        cap = tech_caps.get(technology)
        if cap is not None and cap.schedule is not None and any(
            abs(value - hydro_caps[technology]) > 1e-9
            for value in cap.schedule.values()
        ):
            raise RunnerError(
                f"tech_caps.{technology} disagrees with hydro.activity_caps_pj"
            )
    for pattern, dedicated_schedule in (
        ("PWRHYD<z>", hydro_build),
        ("PWRURN<z>", nuclear_build),
    ):
        configured = build_rates.get(pattern)
        if configured is not None and any(
            abs(configured[year] - dedicated_schedule[year]) > 1e-9
            for year in dedicated_schedule
        ):
            raise RunnerError(f"build_rates.{pattern} disagrees with its dedicated block")

    carbon = _mapping(root.get("carbon"), "policy.carbon")
    _block_status(carbon, "policy.carbon")

    pending = any(
        not (
            str(block.get("source_status")) == "sourced"
            and str(block.get("value_status")) == "sourced"
        )
        for block in (root, reserve, credits_block, rates_block, caps_block, hydro_block, nuclear, carbon)
    )
    if requested_grade and (pending or gaps):
        raise RunnerError(
            "policy_grade=true is not allowed while any required block or structural gap is pending"
        )
    policy_grade = requested_grade and not pending and not gaps

    return PolicyConstraintSet(
        set_id=POLICY_SET_ID,
        status=status,
        policy_grade=policy_grade,
        label=label,
        structural_gaps=gaps,
        notes=notes,
        reserve_margin=rm_schedule,
        peak_factor=peak_schedule,
        capacity_credits=credits,
        default_credit=default_credit,
        build_rates=build_rates,
        tech_caps=tech_caps,
        hydro_mode=hydro_mode,
        hydro_caps_pj=hydro_caps,
        hydro_build_rate=hydro_build,
        nuclear_mode=nuclear_mode,
        nuclear_build_rate=nuclear_build,
        nuclear_total_cap_gw=nuclear_total,
        carbon=carbon,
        value_status=value_status,
        source_status=source_status,
    )


def load_constraint_set(
    set_id: str,
    *,
    repo_root: Path = REPO_ROOT,
    policy_path: Path | None = None,
) -> CanonicalConstraintSet | PolicyConstraintSet:
    """Load the named constraint family without weakening the screening gate."""

    if set_id == "screening_v0":
        return load_canonical_set(
            repo_root / "data/derived/national/clews/osecan/canonical_constraint_set.json"
        )
    if set_id == POLICY_SET_ID:
        return load_policy_set(
            policy_path or repo_root / "data/derived/national/clews/osecan/policy_test_v1.json"
        )
    raise RunnerError(
        f"scenario constraints.set_id={set_id!r} is not supported; "
        f"this runner implements screening_v0 and {POLICY_SET_ID}"
    )


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


def _pick_python(root: Path, manifest: Mapping[str, Any] | None = None) -> Path:
    recorded = manifest.get("python") if manifest else None
    if isinstance(recorded, str) and recorded:
        candidate = Path(recorded)
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    candidate = root / ".venv/bin/python"
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return candidate
    if Path(sys.executable).is_file():
        return Path(sys.executable)
    raise RunnerError(f"no Python interpreter found for model source {root}")


def _pick_otoole(root: Path, manifest: Mapping[str, Any] | None = None) -> Path | None:
    recorded = manifest.get("otoole") if manifest else None
    if isinstance(recorded, str) and recorded:
        candidate = Path(recorded)
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    candidate = root / ".venv/bin/otoole"
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return candidate
    found = shutil.which("otoole")
    return Path(found) if found else None


def _verify_model_manifest(root: Path) -> dict[str, Any] | None:
    """Validate the setup manifest when one is present.

    Args:
        root: Candidate model source root.

    Returns:
        Parsed manifest, or ``None`` for a legacy source without one.

    Raises:
        RunnerError: If the manifest names a different commit or the serialized
            model tree no longer matches the recorded hash.
    """

    path = root / "model_manifest.json"
    if not path.is_file():
        return None
    manifest = _read_json(path)
    if not isinstance(manifest, dict):
        raise RunnerError(f"model manifest is not a JSON object: {path}")
    if manifest.get("schema_version") != "clews.osecan_model_manifest.v1":
        raise RunnerError(f"unsupported model manifest schema: {path}")
    commit = _git_value(root, "rev-parse", "HEAD")
    recorded_commit = manifest.get("source_commit")
    if commit and recorded_commit != commit:
        raise RunnerError(
            f"model manifest commit {recorded_commit!r} does not match "
            f"git HEAD {commit!r} at {root}"
        )
    recorded_tree = manifest.get("model_tree_sha256")
    if not isinstance(recorded_tree, str) or len(recorded_tree) != 64:
        raise RunnerError(f"model manifest has no valid tree hash: {path}")
    actual_tree = tree_sha256(root)
    if actual_tree != recorded_tree:
        raise RunnerError(
            f"model tree hash {actual_tree} does not match manifest "
            f"{recorded_tree}; rebuild with workflows.ui.setup_osecan_model"
        )
    return manifest


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
        candidates = (DEFAULT_MODEL_ROOT,)
        origin = str(DEFAULT_MODEL_ROOT.relative_to(REPO_ROOT))
    for candidate in candidates:
        root = candidate.resolve()
        if not root.is_dir():
            continue
        manifest = _verify_model_manifest(root)
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
            python=_pick_python(root, manifest),
            otoole=_pick_otoole(root, manifest),
        )
    raise RunnerError(
        f"no OSeCAN model copy found ({origin}). Run "
        "`.venv/bin/python -m workflows.ui.setup_osecan_model` to rebuild it from "
        "deltae/C2070@40d5b43, or pass --model-root PATH; refusing to fabricate outputs."
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

def copy_model_source(source: ModelSource, destination: Path) -> Path:
    """Copy the reduced model tree below the run output directory.

    The source tree is never modified.  Only sets, parameters, configuration,
    resources, and source code are copied; virtual environments and prior
    results are excluded.
    """

    if destination.exists():
        raise RunnerError(f"model copy destination already exists: {destination}")
    destination.mkdir(parents=True)
    for entry in MODEL_COPY_ENTRIES:
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


def _residual_capacity(params_dir: Path) -> dict[str, dict[int, float]]:
    """Read exogenous residual capacity as technology -> year -> GW."""

    _, rows = _read_rows(params_dir / "ResidualCapacity.csv")
    residual: dict[str, dict[int, float]] = defaultdict(dict)
    for row in rows:
        residual[row["TECHNOLOGY"]][int(row["YEAR"])] = float(row["VALUE"])
    return dict(residual)


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


def evaluate_harvest_feasibility(
    scenario: Scenario,
    canonical: CanonicalConstraintSet,
    *,
    allow_infeasible: bool = False,
) -> HarvestFeasibility:
    """Resolve the actual scenario harvest path and apply the national gate.

    The gate runs before the disposable model copy.  A normal run fails closed
    above 150 Mm3/yr; ``allow_infeasible`` is an explicit diagnostic override
    and never makes the case Pareto eligible.
    """

    logs = resolve_harvest_log_m3(scenario, canonical)
    resolved_max = max(logs.values())
    feasibility = assess_harvest_feasibility(resolved_max)
    if feasibility.status == "fail" and not allow_infeasible:
        raise RunnerError(
            "feasibility: national harvest "
            f"{resolved_max:,.0f} m3/yr exceeds the documented "
            f"{MAX_NORMAL_HARVEST_M3:,.0f} m3/yr normal-run bound. "
            "200 Mm3 is not admissible under documented AAC/NFD; 150 Mm3 is "
            "conditional (national bound only; no provincial allocation). "
            "Re-run with --allow-infeasible only for a labelled historical "
            "diagnostic; such a run is not Pareto eligible."
        )
    return feasibility


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

    tiered = scenario.harvest.supply_curve == SupplyCurveMode.TIERED
    all_tier_caps: dict[str, dict[int, float]] = {tech: {} for tech in TIER_TECHS}
    for year in YEARS:
        allocation = _tier_allocation(
            rest[year],
            t1_cap=canonical.tier_caps_pj["T1"],
            t2_cap=canonical.tier_caps_pj["T2"],
            t3_cap=canonical.tier_caps_pj["T3"],
            t3_limit=float(scenario.energy.t3_cap_pj) if tiered else math.inf,
            t4_limit=float(scenario.energy.t4_cap_pj) if tiered else math.inf,
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
    # The cap governs new build; the observed fleet is exogenous ResidualCapacity,
    # so TotalAnnualMaxCapacity must never sit below it (OSeMOSYS treats that as
    # infeasible). A zero new-build cap therefore retires nuclear by attrition.
    nuclear_cap = float(scenario.energy.nuclear_cap_gw)
    residual = _residual_capacity(params_dir)
    max_capacity_path = params_dir / "TotalAnnualMaxCapacity.csv"
    investment_path = params_dir / "TotalAnnualMaxCapacityInvestment.csv"
    for technology in PWRURN_TECHS:
        value = nuclear_cap * canonical.nuclear_zone_shares[technology]
        _upsert_technology(
            max_capacity_path,
            technology,
            [
                {
                    "REGION": "CAN",
                    "TECHNOLOGY": technology,
                    "YEAR": year,
                    "VALUE": repr(max(value, residual.get(technology, {}).get(year, 0.0))),
                }
                for year in YEARS
            ],
        )
        _upsert_technology(
            investment_path,
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
# Policy parameter generation
# ---------------------------------------------------------------------------


def _expand_pattern(pattern: str, technologies: Sequence[str]) -> tuple[str, ...]:
    """Expand a ``<z>`` technology pattern, failing closed on zero matches."""

    if "<z>" in pattern:
        matches = tuple(
            pattern.replace("<z>", zone) for zone in ZONES if pattern.replace("<z>", zone) in technologies
        )
    else:
        matches = (pattern,) if pattern in technologies else ()
    if not matches:
        raise RunnerError(f"technology pattern {pattern!r} matched no model technologies")
    return matches


def _capacity_to_activity(params_dir: Path) -> dict[str, float]:
    _, rows = _read_rows(params_dir / "CapacityToActivityUnit.csv")
    values = {row["TECHNOLOGY"]: float(row["VALUE"]) for row in rows}
    if not values:
        raise RunnerError("CapacityToActivityUnit.csv is empty")
    return values


def _distribution_gross_up(model_root: Path) -> float:
    """Return the gross-generation multiplier from the PWRTRN input ratio.

    ``PWRTRN<z>`` consumes ``1.11`` PJ at the generation bus per PJ delivered
    to the demand bus, so gross generation is ``1.11 * delivered activity``.
    Read the ratio from the model instead of duplicating it: a missing or
    non-uniform value would silently shrink the reserve-margin denominator.
    """

    _, rows = _read_rows(model_root / "SETs/InputActivityRatio.csv")
    ratios = {
        round(float(row["VALUE"]), 9)
        for row in rows
        if row.get("TECHNOLOGY") in PWRTRN_TECHS
    }
    if not ratios:
        raise RunnerError(
            "reserve-margin gross-up needs PWRTRN rows in SETs/InputActivityRatio.csv"
        )
    if len(ratios) != 1:
        raise RunnerError(
            f"PWRTRN input activity ratios are not uniform: {sorted(ratios)}"
        )
    gross_up = ratios.pop()
    if gross_up <= 0.0:
        raise RunnerError(f"PWRTRN input activity ratio must be > 0, got {gross_up}")
    return gross_up


def _write_policy_rows(
    path: Path,
    fields: Sequence[str],
    rows: Sequence[Mapping[str, Any]],
) -> None:
    """Write rows, replacing any previous rows for the same technology."""

    by_technology: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        by_technology[str(row["TECHNOLOGY"])].append(row)
    for technology, technology_rows in by_technology.items():
        _upsert_technology(path, technology, technology_rows, create_fields=fields)


def _filter_udc_rows(path: Path, fields: Sequence[str], rows: Sequence[Mapping[str, Any]]) -> None:
    """Replace the policy UDC's rows while preserving any other UDC rows."""

    if path.exists():
        existing_fields, existing = _read_rows(path)
        kept = [row for row in existing if row.get("UDC") != POLICY_UDC]
        output_fields = existing_fields or list(fields)
    else:
        kept = []
        output_fields = list(fields)
    _write_rows(path, output_fields, kept + list(rows))


def _add_udc_set_value(model_root: Path, udc: str = POLICY_UDC) -> None:
    path = model_root / "SETs/UDC.csv"
    if path.exists():
        fields, rows = _read_rows(path)
        values = [row.get("VALUE", "") for row in rows]
        output_fields = fields or ["VALUE"]
    else:
        values = []
        output_fields = ["VALUE"]
    if udc not in values:
        values.append(udc)
    _write_rows(path, output_fields, [{"VALUE": value} for value in sorted(set(values))])


def _write_reserve_margin(
    model_root: Path,
    params_dir: Path,
    policy: PolicyConstraintSet,
) -> dict[str, Any]:
    """Write native RM3 tags and the binding national ``RM_NATIONAL`` UDC."""

    technologies = _read_technology_set(model_root)
    c2a = _capacity_to_activity(params_dir)
    gross_up = _distribution_gross_up(model_root)
    generators = tuple(
        technology
        for technology in sorted(technologies)
        if technology.startswith("PWR")
        and not technology.startswith("PWRTRN")
        and technology[3:6] in CREDIT_ARCHETYPES
    )
    if not generators:
        raise RunnerError("no PWR generator technologies found for reserve-margin credits")
    missing_pwtrn = [tech for tech in PWRTRN_TECHS if tech not in technologies]
    if missing_pwtrn:
        raise RunnerError(f"reserve-margin activity tags need {missing_pwtrn[0]}")

    _write_rows(
        params_dir / "ReserveMargin.csv",
        ("REGION", "YEAR", "VALUE"),
        [{"REGION": "CAN", "YEAR": year, "VALUE": "1.0"} for year in YEARS],
    )
    _write_rows(
        params_dir / "ReserveMarginTagFuel.csv",
        ("REGION", "FUEL", "YEAR", "VALUE"),
        [
            {"REGION": "CAN", "FUEL": f"ELC{zone}02", "YEAR": year, "VALUE": "1.0"}
            for zone in ZONES
            for year in YEARS
        ],
    )
    credit_rows: list[dict[str, Any]] = []
    capacity_rows: list[dict[str, Any]] = []
    for technology in generators:
        credit = policy.credit_for(technology)
        if not math.isfinite(credit) or not 0.0 <= credit <= 1.0:
            raise RunnerError(f"{technology} capacity credit is outside [0,1]")
        for year in YEARS:
            credit_rows.append(
                {
                    "REGION": "CAN",
                    "TECHNOLOGY": technology,
                    "YEAR": year,
                    "VALUE": repr(credit),
                }
            )
            if credit > 0.0:
                capacity_rows.append(
                    {
                        "REGION": "CAN",
                        "TECHNOLOGY": technology,
                        "UDC": POLICY_UDC,
                        "YEAR": year,
                        # UDC1 is <= 0.  A reserve margin requires
                        # RM*peak*gross <= credited capacity, so the
                        # capacity side carries the negative sign.
                        "VALUE": repr(-float(c2a[technology]) * credit),
                    }
                )
    if not capacity_rows:
        raise RunnerError("reserve-margin UDC has no positive capacity multipliers")
    _write_rows(
        params_dir / "ReserveMarginTagTechnology.csv",
        ("REGION", "TECHNOLOGY", "YEAR", "VALUE"),
        credit_rows,
    )

    activity_rows = []
    for technology in PWRTRN_TECHS:
        for year in YEARS:
            # PWRTRN activity is delivered energy; gross generation at the
            # generation bus is larger by the distribution input ratio.
            multiplier = (
                _interpolate(policy.reserve_margin, year)
                * _interpolate(policy.peak_factor, year)
                * gross_up
            )
            activity_rows.append(
                {
                    "REGION": "CAN",
                    "TECHNOLOGY": technology,
                    "UDC": POLICY_UDC,
                    "YEAR": year,
                    "VALUE": repr(multiplier),
                }
            )

    _filter_udc_rows(
        params_dir / "UDCConstant.csv",
        ("REGION", "UDC", "YEAR", "VALUE"),
        [
            {"REGION": "CAN", "UDC": POLICY_UDC, "YEAR": year, "VALUE": "0.0"}
            for year in YEARS
        ],
    )
    _filter_udc_rows(
        params_dir / "UDCTag.csv",
        ("REGION", "UDC", "VALUE"),
        [{"REGION": "CAN", "UDC": POLICY_UDC, "VALUE": "0"}],
    )
    _filter_udc_rows(
        params_dir / "UDCMultiplierTotalCapacity.csv",
        ("REGION", "TECHNOLOGY", "UDC", "YEAR", "VALUE"),
        capacity_rows,
    )
    _filter_udc_rows(
        params_dir / "UDCMultiplierActivity.csv",
        ("REGION", "TECHNOLOGY", "UDC", "YEAR", "VALUE"),
        activity_rows,
    )
    _filter_udc_rows(
        params_dir / "UDCMultiplierNewCapacity.csv",
        ("REGION", "TECHNOLOGY", "UDC", "YEAR", "VALUE"),
        [],
    )
    _add_udc_set_value(model_root)

    return {
        "udc": POLICY_UDC,
        "set_row": "SETs/UDC.csv",
        "generator_count": len(generators),
        "capacity_multiplier_rows": len(capacity_rows),
        "activity_multiplier_rows": len(activity_rows),
        "distribution_gross_up": gross_up,
        "reserve_margin_2021": _interpolate(policy.reserve_margin, 2021),
        "reserve_margin_2050": _interpolate(policy.reserve_margin, 2050),
        "peak_factor_2021": _interpolate(policy.peak_factor, 2021),
        "peak_factor_2050": _interpolate(policy.peak_factor, 2050),
        "native_rm3_enabled": True,
        "native_rm3_authoritative": False,
    }


def _apply_policy_parameters(
    model_root: Path,
    params_dir: Path,
    canonical: CanonicalConstraintSet,
    policy: PolicyConstraintSet,
) -> dict[str, Any]:
    """Overlay policy caps, build rates, and the reserve-margin UDC."""

    technologies = _read_technology_set(model_root)
    residual = _residual_capacity(params_dir)
    rates_path = params_dir / "TotalAnnualMaxCapacityInvestment.csv"
    max_path = params_dir / "TotalAnnualMaxCapacity.csv"
    activity_path = params_dir / "TotalTechnologyAnnualActivityUpperLimit.csv"
    lower_path = params_dir / "TotalTechnologyAnnualActivityLowerLimit.csv"

    # --- build rates ----------------------------------------------------
    rate_rows: list[dict[str, Any]] = []
    expanded_rates: dict[str, Mapping[int, float]] = {}
    for pattern, schedule in policy.build_rates.items():
        for technology in _expand_pattern(pattern, tuple(technologies)):
            expanded_rates.setdefault(technology, schedule)
            rate_rows.extend(
                {
                    "REGION": "CAN",
                    "TECHNOLOGY": technology,
                    "YEAR": year,
                    "VALUE": repr(_interpolate(schedule, year)),
                }
                for year in YEARS
            )
    _write_policy_rows(
        rates_path, ("REGION", "TECHNOLOGY", "YEAR", "VALUE"), rate_rows
    )

    # --- explicit activity and capacity caps ---------------------------
    activity_rows: list[dict[str, Any]] = []
    capacity_rows: list[dict[str, Any]] = []
    for pattern, cap in policy.tech_caps.items():
        for technology in _expand_pattern(pattern, tuple(technologies)):
            if cap.metric == "build_rate_only":
                if technology not in expanded_rates:
                    raise RunnerError(
                        f"{technology} is build_rate_only but has no build-rate schedule"
                    )
                continue
            if cap.schedule is None:
                raise RunnerError(f"{technology} cap has no schedule")
            if cap.metric == "activity_pj":
                activity_rows.extend(
                    {
                        "REGION": "CAN",
                        "TECHNOLOGY": technology,
                        "YEAR": year,
                        "VALUE": repr(_interpolate(cap.schedule, year)),
                    }
                    for year in YEARS
                )
            elif cap.metric == "capacity_gw":
                for year in YEARS:
                    value = _interpolate(cap.schedule, year)
                    floor = residual.get(technology, {}).get(year, 0.0)
                    capacity_rows.append(
                        {
                            "REGION": "CAN",
                            "TECHNOLOGY": technology,
                            "YEAR": year,
                            "VALUE": repr(max(value, floor)),
                        }
                    )
    _write_policy_rows(
        activity_path,
        ("REGION", "TECHNOLOGY", "YEAR", "VALUE"),
        activity_rows,
    )
    _write_policy_rows(
        max_path, ("REGION", "TECHNOLOGY", "YEAR", "VALUE"), capacity_rows
    )

    # --- hydro block ----------------------------------------------------
    if policy.hydro_mode == "none":
        for technology in PWRHYD_TECHS:
            _upsert_technology(activity_path, technology, [])
            _upsert_technology(lower_path, technology, [])
    else:
        hydro_rows = [
            {
                "REGION": "CAN",
                "TECHNOLOGY": technology,
                "YEAR": year,
                "VALUE": repr(policy.hydro_caps_pj[technology]),
            }
            for technology in PWRHYD_TECHS
            for year in YEARS
        ]
        _write_policy_rows(
            activity_path,
            ("REGION", "TECHNOLOGY", "YEAR", "VALUE"),
            hydro_rows,
        )
        _write_policy_rows(
            rates_path,
            ("REGION", "TECHNOLOGY", "YEAR", "VALUE"),
            [
                {
                    "REGION": "CAN",
                    "TECHNOLOGY": technology,
                    "YEAR": year,
                    "VALUE": repr(_interpolate(policy.hydro_build_rate, year)),
                }
                for technology in PWRHYD_TECHS
                for year in YEARS
            ],
        )
        if policy.hydro_mode == "cap_2021":
            for technology in PWRHYD_TECHS:
                _upsert_technology(lower_path, technology, [])
        else:
            _write_policy_rows(
                lower_path,
                ("REGION", "TECHNOLOGY", "YEAR", "VALUE"),
                [
                    {
                        "REGION": "CAN",
                        "TECHNOLOGY": technology,
                        "YEAR": year,
                        "VALUE": repr(policy.hydro_caps_pj[technology]),
                    }
                    for technology in PWRHYD_TECHS
                    for year in YEARS
                ],
            )

    # --- nuclear block --------------------------------------------------
    _write_policy_rows(
        rates_path,
        ("REGION", "TECHNOLOGY", "YEAR", "VALUE"),
        [
            {
                "REGION": "CAN",
                "TECHNOLOGY": technology,
                "YEAR": year,
                "VALUE": repr(_interpolate(policy.nuclear_build_rate, year)),
            }
            for technology in PWRURN_TECHS
            for year in YEARS
        ],
    )
    if policy.nuclear_total_cap_gw is None:
        for technology in PWRURN_TECHS:
            _upsert_technology(max_path, technology, [])
    else:
        nuclear_rows = []
        for technology in PWRURN_TECHS:
            for year in YEARS:
                value = _interpolate(policy.nuclear_total_cap_gw, year) * canonical.nuclear_zone_shares[technology]
                floor = residual.get(technology, {}).get(year, 0.0)
                nuclear_rows.append(
                    {
                        "REGION": "CAN",
                        "TECHNOLOGY": technology,
                        "YEAR": year,
                        "VALUE": repr(max(value, floor)),
                    }
                )
        _write_policy_rows(
            max_path, ("REGION", "TECHNOLOGY", "YEAR", "VALUE"), nuclear_rows
        )

    reserve = _write_reserve_margin(model_root, params_dir, policy)

    # --- preflight ------------------------------------------------------
    failures = _preflight_policy_parameters(params_dir, residual)
    if failures:
        raise RunnerError("policy parameter preflight failed: " + "; ".join(failures[:5]))

    return {
        "reserve_margin_udc": reserve,
        "build_rate_technologies": sorted(expanded_rates),
        "activity_cap_rows": len(activity_rows) + len(PWRHYD_TECHS) * len(YEARS),
        "capacity_cap_rows": len(capacity_rows),
        "nuclear_mode": policy.nuclear_mode,
        "nuclear_total_cap_gw": (
            None
            if policy.nuclear_total_cap_gw is None
            else _interpolate(policy.nuclear_total_cap_gw, 2050)
        ),
        "hydro_mode": policy.hydro_mode,
    }


def _preflight_policy_parameters(
    params_dir: Path,
    residual: Mapping[str, Mapping[int, float]],
) -> list[str]:
    """Return human-readable violations of the policy input invariants."""

    failures: list[str] = []
    _, max_rows = _read_rows(params_dir / "TotalAnnualMaxCapacity.csv")
    for row in max_rows:
        technology = row["TECHNOLOGY"]
        year = int(row["YEAR"])
        value = float(row["VALUE"])
        if not math.isfinite(value) or value < 0.0:
            failures.append(f"TotalAnnualMaxCapacity {technology} {year} is negative or non-finite")
            continue
        floor = residual.get(technology, {}).get(year, 0.0)
        if value + 1e-9 < floor:
            failures.append(
                f"TotalAnnualMaxCapacity {technology} {year} = {value} is below residual {floor}"
            )

    for name, minimum in (
        ("TotalAnnualMaxCapacityInvestment.csv", 0.0),
        ("TotalTechnologyAnnualActivityUpperLimit.csv", 0.0),
    ):
        _, rows = _read_rows(params_dir / name)
        for row in rows:
            value = float(row["VALUE"])
            if not math.isfinite(value) or value < minimum:
                failures.append(
                    f"{name} {row['TECHNOLOGY']} {row['YEAR']} is negative or non-finite"
                )
    return failures


def build_policy_parameter_set(
    model_root: Path,
    scenario: Scenario,
    canonical: CanonicalConstraintSet,
    policy: PolicyConstraintSet,
    output_dir: Path,
    *,
    repo_root: Path = REPO_ROOT,
) -> tuple[Path, SupplyCaps, dict[str, Any]]:
    """Build screening-compatible parameters, then apply the policy overlay."""

    params_dir, supply, generation = build_parameter_set(
        model_root, scenario, canonical, output_dir, repo_root=repo_root
    )
    policy_evidence = _apply_policy_parameters(model_root, params_dir, canonical, policy)
    generation = dict(generation)
    generation["policy"] = policy_evidence
    if policy.nuclear_total_cap_gw is not None:
        generation["nuclear_caps_gw"] = {
            technology: round(
                _interpolate(policy.nuclear_total_cap_gw, 2050)
                * canonical.nuclear_zone_shares[technology],
                6,
            )
            for technology in PWRURN_TECHS
        }
    generation["hydro_mode"] = policy.hydro_mode
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
    constraint_set: CanonicalConstraintSet | PolicyConstraintSet,
    feasibility: HarvestFeasibility,
    source: ModelSource,
    model_copy: Path,
    output_dir: Path,
    supply: SupplyCaps,
    generation: Mapping[str, Any],
    solve: Mapping[str, Any] | None,
    artifacts: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build the run manifest with provenance and parsed constraint labels."""

    return {
        "schema_version": "clews.run_manifest.v1",
        "generated_utc": _utc_now(),
        "status": constraint_set.status,
        "screening_only": constraint_set.screening_only,
        "policy_grade": constraint_set.policy_grade,
        "feasibility_status": feasibility.status,
        "feasibility_notes": list(feasibility.notes),
        "pareto_eligible": feasibility.pareto_eligible,
        "diagnostic": feasibility.diagnostic,
        "harvest_exogenous": True,
        "scenario": scenario.model_dump(mode="json"),
        "constraint_set": constraint_set.summary(),
        "harvest_feasibility": feasibility.summary(),
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
    allow_infeasible: bool = False,
    repo_root: Path = REPO_ROOT,
) -> dict[str, Any]:
    """Build, validate, and optionally solve one scenario.

    Args:
        scenario: Parsed scenario.
        output_dir: Writable run output directory.
        model_root: Optional model source override.
        dry_run: Build and harmonize parameters but do not solve.
        allow_infeasible: Explicit diagnostic override for harvest > 150 Mm3/yr.
        repo_root: Repository root holding reference data.

    Returns:
        Normalized run summary.

    Raises:
        RunnerError: On any operational, validation, or feasibility failure.
    """

    canonical = load_canonical_set(repo_root / "data/derived/national/clews/osecan/canonical_constraint_set.json")
    constraint_set = load_constraint_set(
        scenario.constraints.set_id,
        repo_root=repo_root,
        policy_path=repo_root / "data/derived/national/clews/osecan/policy_test_v1.json",
    )
    feasibility = evaluate_harvest_feasibility(
        scenario,
        canonical,
        allow_infeasible=allow_infeasible,
    )
    source = resolve_model_source(model_root)
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    model_copy = output_dir / MODEL_DIR_NAME
    if model_copy.exists():
        if not (model_copy / ".osecan-model-copy").is_file():
            raise RunnerError(
                f"run output contains an unmarked model copy; refusing to replace it: {model_copy}"
            )
        shutil.rmtree(model_copy)
    copy_model_source(source, model_copy)
    if isinstance(constraint_set, PolicyConstraintSet):
        params_dir, supply, generation = build_policy_parameter_set(
            model_copy,
            scenario,
            canonical,
            constraint_set,
            output_dir,
            repo_root=repo_root,
        )
    else:
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
            "constraint_set": constraint_set.summary(),
            "feasibility_status": feasibility.status,
            "feasibility_notes": list(feasibility.notes),
            "pareto_eligible": feasibility.pareto_eligible,
            "diagnostic": feasibility.diagnostic,
            "harvest_exogenous": True,
            "supply": supply.summary(),
            "generation": generation,
        },
    )
    policy_parameters_path = output_dir / "policy_parameters.json"
    if isinstance(constraint_set, PolicyConstraintSet):
        atomic_write_json(
            policy_parameters_path,
            {
                "schema_version": "clews.policy_parameters.v1",
                "constraint_set": constraint_set.summary(),
                "generation": generation,
                "policy": generation.get("policy", {}),
            },
        )

    artifacts: list[dict[str, Any]] = [
        _artifact(supply_caps_path, output_dir, "supply_caps"),
        _artifact(params_dir / "TotalTechnologyAnnualActivityUpperLimit.csv", output_dir, "parameter"),
        _artifact(params_dir / "VariableCost.csv", output_dir, "parameter"),
    ]
    if isinstance(constraint_set, PolicyConstraintSet):
        artifacts.extend(
            _artifact(path, output_dir, "policy_parameter")
            for path in (
                params_dir / "ReserveMargin.csv",
                params_dir / "ReserveMarginTagFuel.csv",
                params_dir / "ReserveMarginTagTechnology.csv",
                params_dir / "UDCConstant.csv",
                params_dir / "UDCTag.csv",
                params_dir / "UDCMultiplierTotalCapacity.csv",
                params_dir / "UDCMultiplierActivity.csv",
                params_dir / "TotalAnnualMaxCapacityInvestment.csv",
                params_dir / "TotalAnnualMaxCapacity.csv",
                policy_parameters_path,
            )
        )
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
        constraint_set=constraint_set,
        feasibility=feasibility,
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
            "Draft policy-test dry run: parameters generated and harmonized; no OSeCAN solve was run."
            if dry_run and isinstance(constraint_set, PolicyConstraintSet)
            else "Dry run: parameters generated and harmonized; no OSeCAN solve was run."
            if dry_run
            else "Draft policy-test OSeCAN solve completed; not policy-grade."
            if isinstance(constraint_set, PolicyConstraintSet)
            else "Screening-only OSeCAN solve completed."
        ),
        "dry_run": dry_run,
        "constraint_status": constraint_set.status,
        "screening_only": constraint_set.screening_only,
        "policy_grade": constraint_set.policy_grade,
        "feasibility_status": feasibility.status,
        "feasibility_notes": list(feasibility.notes),
        "pareto_eligible": feasibility.pareto_eligible,
        "diagnostic": feasibility.diagnostic,
        "harvest_exogenous": True,
        "constraints": constraint_set.summary(),
        "harvest_feasibility": feasibility.summary(),
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
