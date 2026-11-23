"""Package the 12 D1/D2 OSeCAN candidate cases as read-only files.

The package crosses the two open energy-team decisions at the 119 Mm3
planning allocation:

* D1 harvest recovery: 0.35 / 0.60 / 0.75;
* D2 process-residue availability: 0 / 25 / 50 / 75%.

Each of the 12 cases keeps the process and harvest residue streams separate.
A case therefore carries one process candidate table and one harvest candidate
table; no combined or summed residue value is produced.

The candidate zone/technology activity limits are copied from the v7
scenario/case zone table at 119m, reconciled to ``forestry_osecan_handoff_v8``,
and compared with the adopted national supply curve and the B1/B2 official
2050 activity limits.  Every row is a model estimate:
``model_estimate=true``, ``observed=false``, ``policy_grade=false``, and
``official_osecan_changed=false``.  ``candidate_status`` is always
``read_only_candidate_not_applied``.  No official OSeCAN file is written.

Fail-closed rules
-----------------
* Any unexpected ``D`` in the parent or ``data`` repository status halts the
  builder before it writes output.
* Inputs are parsed once into trusted values; missing, duplicate, non-finite,
  negative, or mislabelled inputs halt the build with a descriptive error.
* The official derived OSeCAN tree and the three read-only official SUPBCW
  schema files are hashed before and after the build and must be unchanged.
* Case count, zone coverage, conservation, PJ conversion, labels, and
  comparison arithmetic are validated before output is accepted.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import subprocess
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_ROOT = REPO_ROOT / "data"
FACILITY_DIR = DATA_ROOT / "derived/national/facilities"

V7_DIR = FACILITY_DIR / "forestry_osecan_scenarios_v7"
V7_CANDIDATES = V7_DIR / "candidate_activity_limits.csv"
V7_SUMMARY = V7_DIR / "summary.json"
V7_MANIFEST = V7_DIR / "manifest.json"

HANDOFF_DIR = FACILITY_DIR / "forestry_osecan_handoff_v8"
HANDOFF_SUMMARY = HANDOFF_DIR / "summary.json"
HANDOFF_WEIGHTS = HANDOFF_DIR / "zone_allocation_weights.csv"
HANDOFF_MANIFEST = HANDOFF_DIR / "manifest.json"

DECISION_DIR = FACILITY_DIR / "energy_decision_matrix_v1"
DECISION_MATRIX = DECISION_DIR / "decision_matrix.csv"
DECISION_SUMMARY = DECISION_DIR / "summary.json"

OSECAN_DIR = DATA_ROOT / "derived/national/clews/osecan"
CROSSWALK = OSECAN_DIR / "zone_crosswalk.csv"
SUPPLY_CURVE_DIR = OSECAN_DIR / "national_supply_curve"
TIER_DEFINITIONS = SUPPLY_CURVE_DIR / "tier_definitions.csv"
TIER_LIMITS = SUPPLY_CURVE_DIR / "tier_activity_limits.csv"
BC_ZONAL_LIMITS = SUPPLY_CURVE_DIR / "bc_zonal_activity_limits.csv"

OFFICIAL_MODEL_DIR = REPO_ROOT / "runtime/osecan_model"
OFFICIAL_ACTIVITY_LIMIT_SCHEMA = (
    OFFICIAL_MODEL_DIR
    / "data/params/TotalTechnologyAnnualActivityUpperLimit.csv"
)
OFFICIAL_TECHNOLOGY = OFFICIAL_MODEL_DIR / "SETs/TECHNOLOGY.csv"
OFFICIAL_OUTPUT_ACTIVITY = OFFICIAL_MODEL_DIR / "SETs/OutputActivityRatio.csv"

OUTPUT_DIR = FACILITY_DIR / "osecan_candidate_cases_v1"
ARTIFACT = "osecan_candidate_cases_v1"
ARTIFACT_VERSION = "1.0.0"
ARTIFACT_TITLE = "OSeCAN Candidate Cases V1 (D1 x D2, 119 Mm3)"
REPORT_NAME = "OSECAN_CANDIDATE_CASES_V1.md"
MODULE_PATH = "workflows.national.facilities.osecan_candidate_cases_v1"
TEST_PATH = "tests/test_osecan_candidate_cases_v1.py"

PRIMARY_SCENARIO = "119m"
REFERENCE_LABEL = "119 Mm3 planning allocation"
CANDIDATE_STATUS = "read_only_candidate_not_applied"
CANDIDATE_BASIS = (
    "119m scenario residue total x stream fraction x handoff_v8 zone weight; "
    "read-only D1/D2 candidate"
)
PJ_GJ = 1_000_000.0
EXPECTED_LHV_GJ_PER_ODT = 18.5
ABS_TOL = 1e-6
REL_TOL = 1e-9
LABEL_TRUE = "true"
LABEL_FALSE = "false"
REST_OF_CANADA_TIERS = "SUPBCWT1|SUPBCWT2|SUPBCWT3|SUPBCWT4"
EXPECTED_T4_UPPER_PJ = 213.538041
EXPECTED_BC_ZONAL_UPPER_PJ = 43.523816032
EXPECTED_NATIONAL_2050_CONTEXT_PJ = 152.92277
EXPECTED_B1_LIMIT_PJ = 3.605474907
EXPECTED_B2_LIMIT_PJ = 39.918341125
EXPECTED_ZONE_WEIGHT_ROWS = 17
EXPECTED_ZONE_COUNT = 13
EXPECTED_PROVINCE_COUNT = 13
EXPECTED_CASE_COUNT = 12
EXPECTED_CANDIDATE_ROWS = EXPECTED_CASE_COUNT * 2 * EXPECTED_ZONE_COUNT
EXPECTED_COMPARISON_ROWS = EXPECTED_CASE_COUNT * 2

RECOVERY_OPTIONS: tuple[dict[str, Any], ...] = (
    {
        "fraction": 0.35,
        "case": "harvest_recovery_035",
        "suffix": "r035",
        "label": "35% recovery",
    },
    {
        "fraction": 0.60,
        "case": "harvest_recovery_060",
        "suffix": "r060",
        "label": "60% recovery",
    },
    {
        "fraction": 0.75,
        "case": "harvest_recovery_075",
        "suffix": "r075",
        "label": "75% recovery",
    },
)

AVAILABILITY_OPTIONS: tuple[dict[str, Any], ...] = (
    {
        "fraction": 0.0,
        "case": "process_availability_000",
        "suffix": "a000",
        "label": "0% availability",
    },
    {
        "fraction": 0.25,
        "case": "process_availability_025",
        "suffix": "a025",
        "label": "25% availability",
    },
    {
        "fraction": 0.50,
        "case": "process_availability_050",
        "suffix": "a050",
        "label": "50% availability",
    },
    {
        "fraction": 0.75,
        "case": "process_availability_075",
        "suffix": "a075",
        "label": "75% availability",
    },
)

STREAM_ORDER = ("process", "harvest")
ZONE_ORDER = (
    "B1",
    "B2",
    "A1",
    "A2",
    "S1",
    "M1",
    "O1",
    "O2",
    "Q1",
    "Q2",
    "T1",
    "L1",
    "N1",
)
BC_ZONE_TECHNOLOGY = {"B1": "SUPBCWB1", "B2": "SUPBCWB2"}
EXPECTED_LABELS = {
    "model_estimate": True,
    "observed": False,
    "policy_grade": False,
    "official_osecan_changed": False,
    "national_claim": False,
}
CANDIDATE_CAVEAT = (
    "Read-only D1/D2 screening candidate. It is not written to any OSeCAN "
    "input, not an operating constraint, and not policy. Process and harvest "
    "residue are separate streams and are never summed. Only B1 and B2 have "
    "an official zone-specific 2050 activity limit in the adopted curve; "
    "other zones have no official zone-specific limit."
)
COMPARISON_NOTE = (
    "Reference values are context, not policy targets. The national supply "
    "curve tiers are rest-of-Canada RSYC-only; comparisons that include BC "
    "are a context screen, not a capacity-adequacy or sustainability test."
)


class OsecanCandidateCasesError(RuntimeError):
    """Raised when the read-only OSeCAN candidate package fails a guard."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise OsecanCandidateCasesError(f"{label} is missing: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise OsecanCandidateCasesError(
            f"{label} is not valid JSON: {path}: {error}"
        ) from error
    if not isinstance(payload, dict):
        raise OsecanCandidateCasesError(
            f"{label} is not a JSON object: {path}"
        )
    return payload


def _read_csv_rows(
    path: Path, required: Sequence[str], label: str
) -> list[dict[str, str]]:
    if not path.is_file():
        raise OsecanCandidateCasesError(f"{label} is missing: {path}")
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames or []
        missing = [field for field in required if field not in fieldnames]
        if missing:
            raise OsecanCandidateCasesError(
                f"{label} is missing columns {missing}: {path}"
            )
        rows = list(reader)
    if not rows:
        raise OsecanCandidateCasesError(f"{label} is empty: {path}")
    return rows


def _parse_float(value: Any, label: str) -> float:
    text = "" if value is None else str(value).strip()
    if not text:
        raise OsecanCandidateCasesError(f"{label} is blank")
    try:
        parsed = float(text)
    except ValueError as error:
        raise OsecanCandidateCasesError(
            f"{label} is not numeric: {text!r}"
        ) from error
    if not math.isfinite(parsed):
        raise OsecanCandidateCasesError(f"{label} is not finite: {parsed!r}")
    return parsed


def _parse_optional_float(value: Any, label: str) -> float | None:
    text = "" if value is None else str(value).strip()
    if not text:
        return None
    return _parse_float(text, label)


def _parse_bool(value: Any, label: str) -> bool:
    text = "" if value is None else str(value).strip().lower()
    if text not in {"true", "false"}:
        raise OsecanCandidateCasesError(
            f"{label} is not true/false: {value!r}"
        )
    return text == LABEL_TRUE


def _require_nonnegative(value: float, label: str) -> float:
    if value < 0.0:
        raise OsecanCandidateCasesError(f"{label} is negative: {value}")
    return value


def _require_close(
    left: float, right: float, label: str, *, abs_tol: float = ABS_TOL
) -> None:
    if not math.isclose(left, right, rel_tol=REL_TOL, abs_tol=abs_tol):
        raise OsecanCandidateCasesError(
            f"{label} mismatch: {left!r} != {right!r}"
        )


def _git_deletions(repository: Path) -> list[str]:
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repository,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise OsecanCandidateCasesError(
            f"cannot inspect git status in {repository}: "
            f"{result.stderr.strip()}"
        )
    return [
        line
        for line in result.stdout.splitlines()
        if len(line) >= 2 and ("D" in {line[0], line[1]})
    ]


def assert_no_unexpected_deletions() -> None:
    """Fail closed on any ``D`` in the parent or data repository status."""
    incidents: list[str] = []
    for repository in (REPO_ROOT, DATA_ROOT):
        for line in _git_deletions(repository):
            incidents.append(f"{repository.name or repository}: {line}")
    if incidents:
        raise OsecanCandidateCasesError(
            "unexpected deletion status detected; halting: "
            + " | ".join(incidents)
        )


def _hash_tree(root: Path) -> tuple[dict[str, str], str]:
    if not root.is_dir():
        raise OsecanCandidateCasesError(f"OSeCAN directory is missing: {root}")
    hashes: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            hashes[str(path.relative_to(REPO_ROOT))] = _sha256(path)
    if not hashes:
        raise OsecanCandidateCasesError(f"OSeCAN directory is empty: {root}")
    digest = hashlib.sha256()
    for relative, value in sorted(hashes.items()):
        digest.update(f"{relative}:{value}\n".encode())
    return hashes, digest.hexdigest()


def _hash_official_schemas() -> dict[str, str]:
    return {
        str(path.relative_to(REPO_ROOT)): _sha256(path)
        for path in (
            OFFICIAL_ACTIVITY_LIMIT_SCHEMA,
            OFFICIAL_TECHNOLOGY,
            OFFICIAL_OUTPUT_ACTIVITY,
        )
    }


def _case_id(recovery: Mapping[str, Any], availability: Mapping[str, Any]) -> str:
    return f"{recovery['suffix']}_{availability['suffix']}"


def _case_label(
    recovery: Mapping[str, Any], availability: Mapping[str, Any]
) -> str:
    return (
        f"recovery {recovery['fraction']:.2f} + process availability "
        f"{availability['fraction']:.2f}"
    )


def _case_tier(pj: float, tiers: Mapping[str, Mapping[str, Any]]) -> str:
    for name in ("T1", "T2", "T3", "T4"):
        if pj <= float(tiers[name]["upper_pj"]) + 1e-9:
            return name
    return "above_T4"


def _case_combinations() -> list[dict[str, Any]]:
    combinations: list[dict[str, Any]] = []
    for recovery in RECOVERY_OPTIONS:
        for availability in AVAILABILITY_OPTIONS:
            combinations.append(
                {
                    "case_id": _case_id(recovery, availability),
                    "case_label": _case_label(recovery, availability),
                    "recovery": recovery,
                    "availability": availability,
                }
            )
    return combinations


def _parse_labels(payload: Mapping[str, Any], label: str) -> None:
    labels = payload.get("labels")
    if not isinstance(labels, Mapping):
        raise OsecanCandidateCasesError(f"{label} has no labels object")
    for key, expected in EXPECTED_LABELS.items():
        if labels.get(key) is not expected:
            raise OsecanCandidateCasesError(
                f"{label} label {key} is not {expected}: {labels.get(key)!r}"
            )


def _parse_tiers(summary: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    supply = summary.get("supply_curve")
    if not isinstance(supply, Mapping):
        raise OsecanCandidateCasesError(
            "v7 summary has no supply_curve object"
        )
    raw_tiers = supply.get("tiers")
    if not isinstance(raw_tiers, Mapping):
        raise OsecanCandidateCasesError("v7 summary has no supply tiers")
    tiers: dict[str, dict[str, Any]] = {}
    for name in ("T1", "T2", "T3", "T4"):
        payload = raw_tiers.get(name)
        if not isinstance(payload, Mapping):
            raise OsecanCandidateCasesError(f"v7 summary lacks tier {name}")
        lower = _parse_float(payload.get("lower_pj"), f"{name} lower_pj")
        upper = _parse_float(payload.get("upper_pj"), f"{name} upper_pj")
        if upper <= lower:
            raise OsecanCandidateCasesError(
                f"{name} upper bound is not above its lower bound"
            )
        tiers[name] = {
            "technology": str(payload.get("technology", "")).strip(),
            "lower_pj": lower,
            "upper_pj": upper,
        }
    _require_close(tiers["T4"]["upper_pj"], EXPECTED_T4_UPPER_PJ, "T4 upper")
    return tiers


def _parse_bc_limits(summary: Mapping[str, Any]) -> dict[str, float]:
    supply = summary.get("supply_curve")
    if not isinstance(supply, Mapping):
        raise OsecanCandidateCasesError("v7 summary has no supply_curve")
    raw_limits = supply.get("bc_zonal_limits_pj")
    if not isinstance(raw_limits, Mapping):
        raise OsecanCandidateCasesError("v7 summary has no BC zonal limits")
    limits = {
        technology: _parse_float(
            raw_limits.get(technology), f"{technology} v7 limit"
        )
        for technology in ("SUPBCWB1", "SUPBCWB2")
    }
    _require_close(limits["SUPBCWB1"], EXPECTED_B1_LIMIT_PJ, "B1 limit")
    _require_close(limits["SUPBCWB2"], EXPECTED_B2_LIMIT_PJ, "B2 limit")
    _require_close(
        math.fsum(limits.values()), EXPECTED_BC_ZONAL_UPPER_PJ, "BC cap sum"
    )
    return limits


def _parse_national_2050_context(summary: Mapping[str, Any]) -> float:
    context = summary.get("osecan_context")
    if not isinstance(context, Mapping):
        raise OsecanCandidateCasesError("v7 summary has no osecan_context")
    value = _parse_float(
        context.get("national_supply_pj"), "national 2050 context"
    )
    _require_close(value, EXPECTED_NATIONAL_2050_CONTEXT_PJ, "2050 context")
    return value


def _parse_handoff_totals(
    handoff: Mapping[str, Any],
) -> dict[str, dict[str, float]]:
    raw = handoff.get("handoff_totals")
    if not isinstance(raw, Mapping):
        raise OsecanCandidateCasesError("handoff summary has no totals")
    totals: dict[str, dict[str, float]] = {"process": {}, "harvest": {}}
    for stream in STREAM_ORDER:
        stream_totals = raw.get(stream)
        if not isinstance(stream_totals, Mapping):
            raise OsecanCandidateCasesError(
                f"handoff summary has no {stream} totals"
            )
        for case, value in stream_totals.items():
            totals[stream][str(case)] = _require_nonnegative(
                _parse_float(value, f"handoff {stream}/{case}"),
                f"handoff {stream}/{case}",
            )
    expected_process = {option["case"] for option in AVAILABILITY_OPTIONS}
    expected_harvest = {option["case"] for option in RECOVERY_OPTIONS}
    if set(totals["process"]) != expected_process:
        raise OsecanCandidateCasesError(
            "handoff process cases do not match 0/25/50/75"
        )
    if set(totals["harvest"]) != expected_harvest:
        raise OsecanCandidateCasesError(
            "handoff harvest cases do not match 0.35/0.60/0.75"
        )
    return totals


def _parse_zone_weights() -> dict[str, Any]:
    rows = _read_csv_rows(
        HANDOFF_WEIGHTS,
        ["province", "zone_id", "zone_name", "weight"],
        "handoff zone weights",
    )
    if len(rows) != EXPECTED_ZONE_WEIGHT_ROWS:
        raise OsecanCandidateCasesError(
            f"expected {EXPECTED_ZONE_WEIGHT_ROWS} zone weights, "
            f"found {len(rows)}"
        )
    by_province: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        province = str(row["province"]).strip().upper()
        zone_id = str(row["zone_id"]).strip().upper()
        if zone_id not in ZONE_ORDER:
            raise OsecanCandidateCasesError(
                f"unexpected zone in weights: {zone_id!r}"
            )
        weight = _require_nonnegative(
            _parse_float(row["weight"], f"{province}/{zone_id} weight"),
            f"{province}/{zone_id} weight",
        )
        if weight == 0.0:
            raise OsecanCandidateCasesError(
                f"{province}/{zone_id} weight is zero"
            )
        by_province.setdefault(province, []).append(
            {
                "zone_id": zone_id,
                "zone_name": str(row["zone_name"]).strip(),
                "weight": weight,
            }
        )
    if len(by_province) != EXPECTED_PROVINCE_COUNT:
        raise OsecanCandidateCasesError(
            f"expected {EXPECTED_PROVINCE_COUNT} province weights, "
            f"found {len(by_province)}"
        )
    for province, province_rows in by_province.items():
        _require_close(
            math.fsum(float(item["weight"]) for item in province_rows),
            1.0,
            f"{province} zone weight sum",
        )
    return {
        "by_province": by_province,
        "rows": [
            item
            for province in sorted(by_province)
            for item in by_province[province]
        ],
    }


def _parse_crosswalk() -> dict[str, str]:
    rows = _read_csv_rows(
        CROSSWALK,
        ["zone_id", "zone_name"],
        "OSeCAN zone crosswalk",
    )
    names = {
        str(row["zone_id"]).strip().upper(): str(row["zone_name"]).strip()
        for row in rows
    }
    if set(names) != set(ZONE_ORDER):
        raise OsecanCandidateCasesError(
            "crosswalk zones do not match the 13 expected zones"
        )
    if any(not name for name in names.values()):
        raise OsecanCandidateCasesError("crosswalk has an empty zone name")
    return names


def _parse_supply_curve_files() -> dict[str, dict[str, float]]:
    tier_rows = _read_csv_rows(
        TIER_DEFINITIONS,
        ["tier", "technology", "lower_pj", "upper_pj"],
        "tier definitions",
    )
    tier_upper = {
        str(row["tier"]).strip(): _parse_float(
            row["upper_pj"], f"tier {row['tier']} upper"
        )
        for row in tier_rows
    }
    if set(tier_upper) != {"T1", "T2", "T3", "T4"}:
        raise OsecanCandidateCasesError(
            "tier definitions do not contain T1-T4"
        )
    _require_close(tier_upper["T4"], EXPECTED_T4_UPPER_PJ, "tier T4 upper")
    limit_rows = _read_csv_rows(
        BC_ZONAL_LIMITS,
        ["REGION", "TECHNOLOGY", "YEAR", "VALUE"],
        "BC zonal activity limits",
    )
    selected = {
        str(row["TECHNOLOGY"]).strip(): _parse_float(
            row["VALUE"], f"BC limit {row['TECHNOLOGY']}"
        )
        for row in limit_rows
        if str(row["REGION"]).strip() == "CAN"
        and str(row["YEAR"]).strip() == "2050"
        and str(row["TECHNOLOGY"]).strip() in BC_ZONE_TECHNOLOGY.values()
    }
    if set(selected) != set(BC_ZONE_TECHNOLOGY.values()):
        raise OsecanCandidateCasesError(
            "BC zonal limits lack SUPBCWB1/SUPBCWB2 for 2050"
        )
    _require_close(selected["SUPBCWB1"], EXPECTED_B1_LIMIT_PJ, "B1 file")
    _require_close(selected["SUPBCWB2"], EXPECTED_B2_LIMIT_PJ, "B2 file")
    return {
        "tier_upper_pj": tier_upper,
        "bc_zonal_limits_pj": selected,
    }


def _parse_official_schema() -> dict[str, Any]:
    limit_rows = _read_csv_rows(
        OFFICIAL_ACTIVITY_LIMIT_SCHEMA,
        ["REGION", "TECHNOLOGY", "YEAR", "VALUE"],
        "official SUPBCW activity-limit schema",
    )
    technologies = {
        str(row["TECHNOLOGY"]).strip()
        for row in limit_rows
        if str(row["REGION"]).strip() == "CAN"
        and str(row["YEAR"]).strip() == "2050"
    }
    expected = {"SUPBCW", "SUPBCWB1", "SUPBCWB2"}
    missing = sorted(expected - technologies)
    if missing:
        raise OsecanCandidateCasesError(
            f"official activity-limit schema lacks {missing}"
        )
    technology_rows = _read_csv_rows(
        OFFICIAL_TECHNOLOGY, ["VALUE"], "official technology list"
    )
    technology_names = {
        str(row["VALUE"]).strip() for row in technology_rows
    }
    missing_technology = sorted(expected - technology_names)
    if missing_technology:
        raise OsecanCandidateCasesError(
            f"official technology list lacks {missing_technology}"
        )
    activity_rows = _read_csv_rows(
        OFFICIAL_OUTPUT_ACTIVITY,
        ["REGION", "TECHNOLOGY"],
        "official output activity ratio",
    )
    if not any(
        str(row["REGION"]).strip() == "CAN"
        and str(row["TECHNOLOGY"]).strip() == "SUPBCW"
        for row in activity_rows
    ):
        raise OsecanCandidateCasesError(
            "official output activity ratio lacks CAN/SUPBCW"
        )
    return {
        "schema_columns": ["REGION", "TECHNOLOGY", "YEAR", "VALUE"],
        "technologies_checked": sorted(expected),
        "official_model_rows_2050": {
            technology: _parse_float(
                next(
                    row["VALUE"]
                    for row in limit_rows
                    if str(row["REGION"]).strip() == "CAN"
                    and str(row["YEAR"]).strip() == "2050"
                    and str(row["TECHNOLOGY"]).strip() == technology
                ),
                f"official {technology} 2050 value",
            )
            for technology in sorted(expected)
        },
    }


def _parse_v7_candidate_rows(
    tiers: Mapping[str, Mapping[str, Any]],
) -> dict[tuple[str, str, str], dict[str, Any]]:
    rows = _read_csv_rows(
        V7_CANDIDATES,
        [
            "scenario",
            "stream",
            "availability_case",
            "availability_fraction",
            "zone_id",
            "zone_name",
            "candidate_activity_limit_odt",
            "candidate_activity_limit_pj",
            "technology_suggestion",
            "technology_mapping_kind",
            "technology_mapping_note",
            "official_limit_reference",
            "official_limit_technology",
            "official_limit_pj",
            "official_limit_status",
            "candidate_ratio_to_official_limit",
            "candidate_headroom_pj",
            "candidate_status",
            "model_estimate",
            "observed",
            "policy_grade",
            "official_osecan_changed",
        ],
        "v7 candidate activity limits",
    )
    selected: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        scenario = str(row["scenario"]).strip()
        if scenario != PRIMARY_SCENARIO:
            continue
        stream = str(row["stream"]).strip()
        if stream not in STREAM_ORDER:
            raise OsecanCandidateCasesError(
                f"unexpected v7 stream: {stream!r}"
            )
        case = str(row["availability_case"]).strip()
        zone_id = str(row["zone_id"]).strip().upper()
        if zone_id not in ZONE_ORDER:
            raise OsecanCandidateCasesError(
                f"unexpected v7 candidate zone: {zone_id!r}"
            )
        if _parse_bool(row["model_estimate"], "v7 model_estimate") is not True:
            raise OsecanCandidateCasesError(
                "v7 candidate row model_estimate is not true"
            )
        for label in ("observed", "policy_grade", "official_osecan_changed"):
            if _parse_bool(row[label], f"v7 {label}") is not False:
                raise OsecanCandidateCasesError(
                    f"v7 candidate row {label} is not false"
                )
        status = str(row["candidate_status"]).strip()
        if status != CANDIDATE_STATUS:
            raise OsecanCandidateCasesError(
                f"v7 candidate status is not read-only: {status!r}"
            )
        odt = _require_nonnegative(
            _parse_float(
                row["candidate_activity_limit_odt"],
                f"v7 {stream}/{case}/{zone_id} odt",
            ),
            f"v7 {stream}/{case}/{zone_id} odt",
        )
        pj = _require_nonnegative(
            _parse_float(
                row["candidate_activity_limit_pj"],
                f"v7 {stream}/{case}/{zone_id} pj",
            ),
            f"v7 {stream}/{case}/{zone_id} pj",
        )
        _require_close(
            pj,
            odt * EXPECTED_LHV_GJ_PER_ODT / PJ_GJ,
            f"v7 {stream}/{case}/{zone_id} PJ conversion",
        )
        technology = str(row["technology_suggestion"]).strip()
        if not technology:
            raise OsecanCandidateCasesError(
                f"v7 {stream}/{case}/{zone_id} has empty technology"
            )
        expected_technology = BC_ZONE_TECHNOLOGY.get(
            zone_id, REST_OF_CANADA_TIERS
        )
        if technology != expected_technology:
            raise OsecanCandidateCasesError(
                f"v7 {zone_id} technology {technology!r} does not match "
                f"{expected_technology!r}"
            )
        official_pj = _parse_optional_float(
            row["official_limit_pj"],
            f"v7 {stream}/{case}/{zone_id} official limit",
        )
        ratio = _parse_optional_float(
            row["candidate_ratio_to_official_limit"],
            f"v7 {stream}/{case}/{zone_id} ratio",
        )
        headroom = _parse_optional_float(
            row["candidate_headroom_pj"],
            f"v7 {stream}/{case}/{zone_id} headroom",
        )
        if zone_id in BC_ZONE_TECHNOLOGY:
            if official_pj is None or ratio is None or headroom is None:
                raise OsecanCandidateCasesError(
                    f"v7 {zone_id} is missing official comparison fields"
                )
            _require_close(
                official_pj,
                EXPECTED_B1_LIMIT_PJ
                if zone_id == "B1"
                else EXPECTED_B2_LIMIT_PJ,
                f"{zone_id} official limit",
            )
            _require_close(ratio, pj / official_pj, f"{zone_id} ratio")
            _require_close(
                headroom, official_pj - pj, f"{zone_id} headroom"
            )
        elif any(value is not None for value in (official_pj, ratio, headroom)):
            raise OsecanCandidateCasesError(
                f"v7 {zone_id} has an unexpected official comparison"
            )
        key = (stream, case, zone_id)
        if key in selected:
            raise OsecanCandidateCasesError(
                f"duplicate v7 candidate row: {key}"
            )
        selected[key] = {
            "zone_name": str(row["zone_name"]).strip(),
            "odt": odt,
            "pj": pj,
            "technology_suggestion": technology,
            "technology_mapping_kind": str(
                row["technology_mapping_kind"]
            ).strip(),
            "technology_mapping_note": str(
                row["technology_mapping_note"]
            ).strip(),
            "official_limit_reference": str(
                row["official_limit_reference"]
            ).strip(),
            "official_limit_technology": str(
                row["official_limit_technology"]
            ).strip(),
            "official_limit_pj": official_pj,
            "official_limit_status": str(
                row["official_limit_status"]
            ).strip(),
            "candidate_ratio_to_official_limit": ratio,
            "candidate_headroom_pj": headroom,
        }
    expected_cases = {
        "process": {option["case"] for option in AVAILABILITY_OPTIONS},
        "harvest": {option["case"] for option in RECOVERY_OPTIONS},
    }
    for stream in STREAM_ORDER:
        for case in expected_cases[stream]:
            for zone_id in ZONE_ORDER:
                if (stream, case, zone_id) not in selected:
                    raise OsecanCandidateCasesError(
                        f"v7 candidates lack {stream}/{case}/{zone_id}"
                    )
    if len(selected) != 7 * EXPECTED_ZONE_COUNT:
        raise OsecanCandidateCasesError(
            f"expected 91 v7 candidate rows, found {len(selected)}"
        )
    return selected


def _parse_decision_matrix() -> dict[str, list[dict[str, str]]]:
    rows = _read_csv_rows(
        DECISION_MATRIX,
        [
            "decision_id",
            "case_key",
            "stream",
            "value_pj",
            "osecan_tier",
            "model_estimate",
            "observed",
            "policy_grade",
            "official_osecan_changed",
        ],
        "energy decision matrix",
    )
    decisions: dict[str, list[dict[str, str]]] = {"D1": [], "D2": []}
    for row in rows:
        decision_id = str(row["decision_id"]).strip()
        if decision_id not in decisions:
            continue
        if _parse_bool(row["model_estimate"], "matrix model_estimate") is not True:
            raise OsecanCandidateCasesError(
                "decision matrix model_estimate is not true"
            )
        for label in ("observed", "policy_grade", "official_osecan_changed"):
            if _parse_bool(row[label], f"matrix {label}") is not False:
                raise OsecanCandidateCasesError(
                    f"decision matrix {label} is not false"
                )
        decisions[decision_id].append(row)
    if len(decisions["D1"]) != 3 or len(decisions["D2"]) != 4:
        raise OsecanCandidateCasesError(
            "decision matrix does not contain 3 D1 and 4 D2 cases"
        )
    return decisions


def _build_case_index_rows(
    combinations: Sequence[Mapping[str, Any]],
    totals: Mapping[str, Mapping[str, float]],
    tiers: Mapping[str, Mapping[str, Any]],
    crosswalk: Mapping[str, str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for order, combination in enumerate(combinations, start=1):
        recovery = combination["recovery"]
        availability = combination["availability"]
        harvest_pj = totals["harvest"][recovery["case"]]
        process_pj = totals["process"][availability["case"]]
        rows.append(
            {
                "case_id": combination["case_id"],
                "case_order": order,
                "case_label": combination["case_label"],
                "scenario": PRIMARY_SCENARIO,
                "scenario_label": REFERENCE_LABEL,
                "d1_decision_id": "D1",
                "d1_recovery_fraction": recovery["fraction"],
                "d1_recovery_case": recovery["case"],
                "d1_option_label": recovery["label"],
                "d2_decision_id": "D2",
                "d2_availability_fraction": availability["fraction"],
                "d2_availability_case": availability["case"],
                "d2_option_label": availability["label"],
                "harvest_national_pj": harvest_pj,
                "harvest_tier": _case_tier(harvest_pj, tiers),
                "harvest_t4_headroom_pj": float(tiers["T4"]["upper_pj"])
                - harvest_pj,
                "process_national_pj": process_pj,
                "process_tier": _case_tier(process_pj, tiers),
                "process_t4_headroom_pj": float(tiers["T4"]["upper_pj"])
                - process_pj,
                "zone_count_per_stream": len(crosswalk),
                "candidate_zone_rows": 2 * len(crosswalk),
                "candidate_status": CANDIDATE_STATUS,
                "model_estimate": LABEL_TRUE,
                "observed": LABEL_FALSE,
                "policy_grade": LABEL_FALSE,
                "official_osecan_changed": LABEL_FALSE,
                "caveat": CANDIDATE_CAVEAT,
            }
        )
    return rows


def _build_candidate_rows(
    combinations: Sequence[Mapping[str, Any]],
    v7_rows: Mapping[tuple[str, str, str], Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for order, combination in enumerate(combinations, start=1):
        recovery = combination["recovery"]
        availability = combination["availability"]
        for stream in STREAM_ORDER:
            if stream == "process":
                stream_case = availability["case"]
                stream_fraction = availability["fraction"]
            else:
                stream_case = recovery["case"]
                stream_fraction = recovery["fraction"]
            for zone_id in ZONE_ORDER:
                source = v7_rows[(stream, stream_case, zone_id)]
                rows.append(
                    {
                        "scenario": PRIMARY_SCENARIO,
                        "case_id": combination["case_id"],
                        "case_order": order,
                        "case_label": combination["case_label"],
                        "d1_recovery_fraction": recovery["fraction"],
                        "d2_availability_fraction": availability["fraction"],
                        "stream": stream,
                        "stream_case": stream_case,
                        "stream_fraction": stream_fraction,
                        "zone_id": zone_id,
                        "zone_name": source["zone_name"],
                        "candidate_activity_limit_odt": source["odt"],
                        "candidate_activity_limit_pj": source["pj"],
                        "technology_suggestion": source[
                            "technology_suggestion"
                        ],
                        "technology_mapping_kind": source[
                            "technology_mapping_kind"
                        ],
                        "technology_mapping_note": source[
                            "technology_mapping_note"
                        ],
                        "official_limit_reference": source[
                            "official_limit_reference"
                        ],
                        "official_limit_technology": source[
                            "official_limit_technology"
                        ],
                        "official_limit_pj": source["official_limit_pj"],
                        "official_limit_status": source[
                            "official_limit_status"
                        ],
                        "candidate_ratio_to_official_limit": source[
                            "candidate_ratio_to_official_limit"
                        ],
                        "candidate_headroom_pj": source[
                            "candidate_headroom_pj"
                        ],
                        "candidate_status": CANDIDATE_STATUS,
                        "candidate_basis": CANDIDATE_BASIS,
                        "model_estimate": LABEL_TRUE,
                        "observed": LABEL_FALSE,
                        "policy_grade": LABEL_FALSE,
                        "official_osecan_changed": LABEL_FALSE,
                        "caveat": CANDIDATE_CAVEAT,
                    }
                )
    return rows


def _zone_case_pj(
    candidate_rows: Sequence[Mapping[str, Any]],
    *,
    case_id: str,
    stream: str,
    zone_ids: Sequence[str],
) -> float:
    values = [
        float(row["candidate_activity_limit_pj"])
        for row in candidate_rows
        if row["case_id"] == case_id
        and row["stream"] == stream
        and row["zone_id"] in zone_ids
    ]
    if len(values) != len(zone_ids):
        raise OsecanCandidateCasesError(
            f"missing candidate rows for {case_id}/{stream}/{zone_ids}"
        )
    return math.fsum(values)


def _build_comparison_rows(
    combinations: Sequence[Mapping[str, Any]],
    totals: Mapping[str, Mapping[str, float]],
    candidate_rows: Sequence[Mapping[str, Any]],
    tiers: Mapping[str, Mapping[str, Any]],
    bc_limits: Mapping[str, float],
    national_2050_context_pj: float,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    t4_upper = float(tiers["T4"]["upper_pj"])
    bc_upper = float(math.fsum(bc_limits.values()))
    for order, combination in enumerate(combinations, start=1):
        recovery = combination["recovery"]
        availability = combination["availability"]
        for stream in STREAM_ORDER:
            stream_case = (
                availability["case"]
                if stream == "process"
                else recovery["case"]
            )
            stream_fraction = (
                availability["fraction"]
                if stream == "process"
                else recovery["fraction"]
            )
            case_pj = totals[stream][stream_case]
            b1_pj = _zone_case_pj(
                candidate_rows,
                case_id=combination["case_id"],
                stream=stream,
                zone_ids=("B1",),
            )
            b2_pj = _zone_case_pj(
                candidate_rows,
                case_id=combination["case_id"],
                stream=stream,
                zone_ids=("B2",),
            )
            bc_pj = math.fsum((b1_pj, b2_pj))
            rows.append(
                {
                    "scenario": PRIMARY_SCENARIO,
                    "case_id": combination["case_id"],
                    "case_order": order,
                    "case_label": combination["case_label"],
                    "stream": stream,
                    "stream_case": stream_case,
                    "stream_fraction": stream_fraction,
                    "case_pj": case_pj,
                    "case_tier": _case_tier(case_pj, tiers),
                    "national_t4_upper_pj": t4_upper,
                    "national_t4_ratio": case_pj / t4_upper,
                    "national_t4_headroom_pj": t4_upper - case_pj,
                    "national_2050_context_pj": national_2050_context_pj,
                    "national_2050_context_ratio": (
                        case_pj / national_2050_context_pj
                    ),
                    "national_2050_context_headroom_pj": (
                        national_2050_context_pj - case_pj
                    ),
                    "bc_case_pj": bc_pj,
                    "bc_official_zonal_upper_pj": bc_upper,
                    "bc_zonal_ratio": bc_pj / bc_upper,
                    "bc_zonal_headroom_pj": bc_upper - bc_pj,
                    "b1_candidate_pj": b1_pj,
                    "b1_official_limit_pj": bc_limits["SUPBCWB1"],
                    "b1_ratio": b1_pj / bc_limits["SUPBCWB1"],
                    "b1_headroom_pj": bc_limits["SUPBCWB1"] - b1_pj,
                    "b2_candidate_pj": b2_pj,
                    "b2_official_limit_pj": bc_limits["SUPBCWB2"],
                    "b2_ratio": b2_pj / bc_limits["SUPBCWB2"],
                    "b2_headroom_pj": bc_limits["SUPBCWB2"] - b2_pj,
                    "reference_is_policy_target": LABEL_FALSE,
                    "model_estimate": LABEL_TRUE,
                    "observed": LABEL_FALSE,
                    "policy_grade": LABEL_FALSE,
                    "official_osecan_changed": LABEL_FALSE,
                    "comparison_note": COMPARISON_NOTE,
                }
            )
    return rows


def _validate(
    *,
    combinations: Sequence[Mapping[str, Any]],
    case_rows: Sequence[Mapping[str, Any]],
    candidate_rows: Sequence[Mapping[str, Any]],
    comparison_rows: Sequence[Mapping[str, Any]],
    totals: Mapping[str, Mapping[str, float]],
    tiers: Mapping[str, Mapping[str, Any]],
    bc_limits: Mapping[str, float],
    national_2050_context_pj: float,
    weights: Mapping[str, Any],
    crosswalk: Mapping[str, str],
    supply_files: Mapping[str, Any],
    osecan_unchanged: bool,
    osecan_changed_files: Sequence[str],
    official_unchanged: bool,
    official_changed_files: Sequence[str],
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def check(name: str, passed: bool, detail: str) -> None:
        checks.append({"check": name, "detail": detail, "passed": bool(passed)})

    check(
        "case_count",
        len(case_rows) == EXPECTED_CASE_COUNT,
        f"{len(case_rows)} case index rows",
    )
    case_ids = [row["case_id"] for row in case_rows]
    check(
        "case_uniqueness",
        len(set(case_ids)) == len(case_ids),
        "case ids are unique",
    )
    expected_ids = [combination["case_id"] for combination in combinations]
    check(
        "case_order",
        case_ids == expected_ids,
        "cases ordered D1 recovery outer x D2 availability inner",
    )
    expected_pairs = {
        (round(float(recovery["fraction"]), 2), round(float(availability["fraction"]), 2))
        for recovery in RECOVERY_OPTIONS
        for availability in AVAILABILITY_OPTIONS
    }
    observed_pairs = {
        (
            round(float(row["d1_recovery_fraction"]), 2),
            round(float(row["d2_availability_fraction"]), 2),
        )
        for row in case_rows
    }
    check(
        "case_fraction_grid",
        observed_pairs == expected_pairs
        and len(observed_pairs) == EXPECTED_CASE_COUNT,
        "12 unique recovery x availability combinations",
    )
    check(
        "candidate_row_count",
        len(candidate_rows) == EXPECTED_CANDIDATE_ROWS,
        f"{len(candidate_rows)} candidate rows (expected 312)",
    )
    keys = {
        (row["case_id"], row["stream"], row["zone_id"])
        for row in candidate_rows
    }
    check(
        "candidate_uniqueness",
        len(keys) == len(candidate_rows),
        "case/stream/zone rows are unique",
    )
    coverage_ok = True
    conservation_ok = True
    conversion_ok = True
    zero_ok = True
    for combination in combinations:
        for stream in STREAM_ORDER:
            stream_case = (
                combination["availability"]["case"]
                if stream == "process"
                else combination["recovery"]["case"]
            )
            zone_rows = [
                row
                for row in candidate_rows
                if row["case_id"] == combination["case_id"]
                and row["stream"] == stream
            ]
            if {row["zone_id"] for row in zone_rows} != set(ZONE_ORDER):
                coverage_ok = False
            zone_sum = math.fsum(
                float(row["candidate_activity_limit_pj"])
                for row in zone_rows
            )
            if not math.isclose(
                zone_sum,
                totals[stream][stream_case],
                rel_tol=REL_TOL,
                abs_tol=ABS_TOL,
            ):
                conservation_ok = False
            if (
                stream == "process"
                and stream_case.endswith("_000")
                and any(
                    float(row["candidate_activity_limit_pj"]) != 0.0
                    for row in zone_rows
                )
            ):
                zero_ok = False
            for row in zone_rows:
                expected_pj = (
                    float(row["candidate_activity_limit_odt"])
                    * EXPECTED_LHV_GJ_PER_ODT
                    / PJ_GJ
                )
                if not math.isclose(
                    float(row["candidate_activity_limit_pj"]),
                    expected_pj,
                    rel_tol=REL_TOL,
                    abs_tol=ABS_TOL,
                ):
                    conversion_ok = False
    check("zone_coverage", coverage_ok, "all 13 zones per case/stream")
    check(
        "candidate_conservation",
        conservation_ok,
        "zone candidate PJ sum to 119m stream/case totals",
    )
    check(
        "candidate_pj_conversion",
        conversion_ok,
        "candidate PJ = ODT x 18.5 / 1e6",
    )
    check(
        "zero_process_case",
        zero_ok,
        "process_availability_000 candidate rows are exactly zero",
    )
    label_ok = all(
        row["model_estimate"] == LABEL_TRUE
        and row["observed"] == LABEL_FALSE
        and row["policy_grade"] == LABEL_FALSE
        and row["official_osecan_changed"] == LABEL_FALSE
        and row["candidate_status"] == CANDIDATE_STATUS
        for row in candidate_rows
    )
    check(
        "candidate_labels",
        label_ok,
        "every candidate row is read-only and carries the four labels",
    )
    stream_set = {row["stream"] for row in candidate_rows}
    combined_columns = [
        column
        for column in candidate_rows[0]
        if "combined" in column or column == "total_pj"
    ]
    check(
        "stream_separation",
        stream_set == set(STREAM_ORDER) and not combined_columns,
        "process and harvest are separate; no combined total column",
    )
    official_ok = True
    arithmetic_ok = True
    for row in candidate_rows:
        zone_id = row["zone_id"]
        if zone_id not in BC_ZONE_TECHNOLOGY:
            if any(
                row[field] is not None
                for field in (
                    "official_limit_pj",
                    "candidate_ratio_to_official_limit",
                    "candidate_headroom_pj",
                )
            ):
                official_ok = False
            continue
        limit = bc_limits[BC_ZONE_TECHNOLOGY[zone_id]]
        if not math.isclose(
            float(row["official_limit_pj"]), limit, abs_tol=ABS_TOL
        ):
            official_ok = False
        if not math.isclose(
            float(row["candidate_ratio_to_official_limit"]),
            float(row["candidate_activity_limit_pj"]) / limit,
            rel_tol=REL_TOL,
            abs_tol=ABS_TOL,
        ):
            arithmetic_ok = False
        if not math.isclose(
            float(row["candidate_headroom_pj"]),
            limit - float(row["candidate_activity_limit_pj"]),
            rel_tol=REL_TOL,
            abs_tol=ABS_TOL,
        ):
            arithmetic_ok = False
    check(
        "official_zone_limits",
        official_ok,
        "B1/B2 official limits apply only to their zones",
    )
    check(
        "candidate_limit_arithmetic",
        arithmetic_ok,
        "B1/B2 ratio and headroom arithmetic is consistent",
    )
    check(
        "comparison_row_count",
        len(comparison_rows) == EXPECTED_COMPARISON_ROWS,
        f"{len(comparison_rows)} comparison rows",
    )
    comparison_ok = True
    for row in comparison_rows:
        case_pj = float(row["case_pj"])
        checks_to_make = (
            (
                float(row["national_t4_upper_pj"]),
                EXPECTED_T4_UPPER_PJ,
            ),
            (
                float(row["national_2050_context_pj"]),
                national_2050_context_pj,
            ),
            (
                float(row["bc_official_zonal_upper_pj"]),
                math.fsum(bc_limits.values()),
            ),
            (float(row["b1_official_limit_pj"]), bc_limits["SUPBCWB1"]),
            (float(row["b2_official_limit_pj"]), bc_limits["SUPBCWB2"]),
        )
        if any(
            not math.isclose(actual, expected, abs_tol=ABS_TOL)
            for actual, expected in checks_to_make
        ):
            comparison_ok = False
        if not math.isclose(
            float(row["national_t4_ratio"]),
            case_pj / float(row["national_t4_upper_pj"]),
            rel_tol=REL_TOL,
            abs_tol=ABS_TOL,
        ):
            comparison_ok = False
        if not math.isclose(
            float(row["national_t4_headroom_pj"]),
            float(row["national_t4_upper_pj"]) - case_pj,
            rel_tol=REL_TOL,
            abs_tol=ABS_TOL,
        ):
            comparison_ok = False
        if not math.isclose(
            float(row["national_2050_context_ratio"]),
            case_pj / national_2050_context_pj,
            rel_tol=REL_TOL,
            abs_tol=ABS_TOL,
        ):
            comparison_ok = False
        if not math.isclose(
            float(row["national_2050_context_headroom_pj"]),
            national_2050_context_pj - case_pj,
            rel_tol=REL_TOL,
            abs_tol=ABS_TOL,
        ):
            comparison_ok = False
        for prefix in ("b1", "b2", "bc_zonal"):
            if prefix == "bc_zonal":
                candidate = float(row["bc_case_pj"])
                limit = float(row["bc_official_zonal_upper_pj"])
            else:
                candidate = float(row[f"{prefix}_candidate_pj"])
                limit = float(row[f"{prefix}_official_limit_pj"])
            if not math.isclose(
                float(row[f"{prefix}_ratio"]),
                candidate / limit,
                rel_tol=REL_TOL,
                abs_tol=ABS_TOL,
            ):
                comparison_ok = False
            if not math.isclose(
                float(row[f"{prefix}_headroom_pj"]),
                limit - candidate,
                rel_tol=REL_TOL,
                abs_tol=ABS_TOL,
            ):
                comparison_ok = False
    check(
        "comparison_arithmetic",
        comparison_ok,
        "national, B1, B2, and BC comparison arithmetic is consistent",
    )
    check(
        "supply_curve_references",
        all(
            math.isclose(
                supply_files["tier_upper_pj"][name],
                tiers[name]["upper_pj"],
                abs_tol=ABS_TOL,
            )
            for name in ("T1", "T2", "T3", "T4")
        ),
        "tier definitions match the v7 adopted curve",
    )
    check(
        "zone_mapping",
        len(weights["by_province"]) == EXPECTED_PROVINCE_COUNT
        and {row["zone_id"] for row in weights["rows"]} == set(ZONE_ORDER)
        and math.isclose(
            math.fsum(float(row["weight"]) for row in weights["rows"]),
            EXPECTED_PROVINCE_COUNT,
            abs_tol=ABS_TOL,
        ),
        "13 province weights cover 13 zones and each province sums to 1",
    )
    check(
        "zone_technology_mapping",
        all(
            row["technology_suggestion"]
            == BC_ZONE_TECHNOLOGY.get(row["zone_id"], REST_OF_CANADA_TIERS)
            for row in candidate_rows
        ),
        "zone technology suggestions are the documented SUPBCW mapping",
    )
    check(
        "crosswalk_coverage",
        set(crosswalk) == set(ZONE_ORDER),
        "crosswalk covers the 13 zones",
    )
    check(
        "national_reconciliation",
        all(
            math.isclose(
                math.fsum(
                    float(row["candidate_activity_limit_pj"])
                    for row in candidate_rows
                    if row["case_id"] == combination["case_id"]
                    and row["stream"] == stream
                ),
                totals[stream][
                    combination["availability"]["case"]
                    if stream == "process"
                    else combination["recovery"]["case"]
                ],
                rel_tol=REL_TOL,
                abs_tol=ABS_TOL,
            )
            for combination in combinations
            for stream in STREAM_ORDER
        ),
        "every candidate case/stream reconciles to handoff v8",
    )
    check(
        "labels_summary_contract",
        True,
        "summary labels are fixed in the build",
    )
    check(
        "official_osecan_unchanged",
        osecan_unchanged and not osecan_changed_files,
        "derived OSeCAN tree hash is unchanged",
    )
    check(
        "official_schema_unchanged",
        official_unchanged and not official_changed_files,
        "read-only official SUPBCW schema files are unchanged",
    )
    failed = sum(1 for check_item in checks if not check_item["passed"])
    return {
        "checks": checks,
        "passed": failed == 0,
        "failed_check_count": failed,
    }


def _write_csv(
    path: Path, rows: Sequence[Mapping[str, Any]], fields: Sequence[str]
) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields))
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row[field] for field in fields})


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _format_number(value: float, digits: int = 3) -> str:
    return f"{value:,.{digits}f}"


def _report(
    summary: Mapping[str, Any],
    case_rows: Sequence[Mapping[str, Any]],
    comparison_rows: Sequence[Mapping[str, Any]],
    candidate_rows: Sequence[Mapping[str, Any]],
) -> str:
    lines: list[str] = [
        f"# {ARTIFACT_TITLE}",
        "",
        (f"Status: **{summary['status']}**. Artifact version "
        f"`{ARTIFACT_VERSION}`; generated {summary['generated_utc']}."),
        "",
        ("Read-only candidate package. Every row is `model_estimate=true`, "
        "`observed=false`, `policy_grade=false`, and "
        "`official_osecan_changed=false`. No official OSeCAN input is written "
        "or modified. Process and harvest residue are separate streams and "
        "are never summed."),
        "",
        "## Scope and method",
        "",
        f"- Scenario: `{PRIMARY_SCENARIO}` ({REFERENCE_LABEL}).",
        "- D1 harvest recovery: 0.35 / 0.60 / 0.75.",
        "- D2 process-residue availability: 0 / 25 / 50 / 75%.",
        (f"- Cases: {EXPECTED_CASE_COUNT} D1 x D2 combinations. Each case "
        "carries both streams separately."),
        ("- Candidate zone limits are the v7 119m zone candidate limits "
        "(handoff v8 weights) regrouped under each case id."),
        ("- Case candidate zone PJ sums are reconciled to "
        "`forestry_osecan_handoff_v8` at 119m."),
        ("- Official references are the adopted national supply-curve tiers "
        "(T1-T4), the 2050 OSeCAN national context, and the B1/B2 official "
        "2050 activity limits."),
        "",
        "## Case index",
        "",
        ("| Case | D1 recovery | D2 availability | Harvest PJ | Harvest tier "
        "| Process PJ | Process tier |"),
        "|---|---:|---:|---:|---|---:|---|",
    ]
    for row in case_rows:
        lines.append(
            f"| `{row['case_id']}` | {float(row['d1_recovery_fraction']):.2f} "
            f"| {float(row['d2_availability_fraction']):.2f} "
            f"| {_format_number(float(row['harvest_national_pj']))} "
            f"| {row['harvest_tier']} "
            f"| {_format_number(float(row['process_national_pj']))} "
            f"| {row['process_tier']} |"
        )
    lines.extend(
        [
            "",
            ("Harvest and process columns are separate; no total column is "
            "defined."),
            "",
            "## Candidate activity-limit table",
            "",
            ("`candidate_activity_limits.csv` is the documented read-only "
            "format. Grain: `scenario`, `case_id`, `stream`, `zone_id`. It "
            "has "
            f"{len(candidate_rows)} rows "
            f"({EXPECTED_CASE_COUNT} cases x 2 streams x {EXPECTED_ZONE_COUNT} "
            "zones). Required fields: `scenario`, `case_id`, `stream`, "
            "`zone_id`, `candidate_activity_limit_pj`, and "
            "`technology_suggestion`. `candidate_status` is always "
            f"`{CANDIDATE_STATUS}`. `official_limit_pj`, "
            "`candidate_ratio_to_official_limit`, and "
            "`candidate_headroom_pj` are populated only for B1/B2; blank means "
            "no official zone-specific limit exists in the adopted curve."),
            "",
            "Column dictionary:",
            "",
            "| Column | Meaning |",
            "|---|---|",
            "| `scenario` | `119m` planning allocation; not an AAC. |",
            "| `case_id` | D1 x D2 case, e.g. `r060_a025`. |",
            "| `stream` | `process` or `harvest`; never combined. |",
            "| `stream_case` | Seven-case key for that stream. |",
            "| `stream_fraction` | D1 recovery or D2 availability fraction. |",
            "| `zone_id` / `zone_name` | One of the 13 OSeCAN zones. |",
            "| `candidate_activity_limit_odt` | Candidate zone residue, ODT. |",
            "| `candidate_activity_limit_pj` | Candidate zone residue, PJ. |",
            ("| `technology_suggestion` | SUPBCW candidate string; not an "
            "assignment. |"),
            ("| `official_limit_reference` | B1/B2 2050 limit reference or "
            "`none`. |"),
            "| `official_limit_pj` | Adopted B1/B2 2050 limit, PJ. |",
            ("| `candidate_ratio_to_official_limit` | Candidate PJ / official "
            "B1/B2 limit. |"),
            "| `candidate_headroom_pj` | Official B1/B2 limit - candidate PJ. |",
            ("| `candidate_status` | Always "
            f"`{CANDIDATE_STATUS}`. |"),
            ("| `model_estimate` / `observed` / `policy_grade` / "
            "`official_osecan_changed` | Fixed labels. |"),
            "",
            "## Comparison to official B1/B2 limits and national supply curve",
            "",
            ("Ratios above 1.0 mean the case exceeds the reference; negative "
            "headroom is the excess. References are context, not policy "
            "targets. The national supply curve is rest-of-Canada RSYC-only; "
            "comparisons that include BC are a context screen, not a "
            "capacity-adequacy or sustainability test."),
            "",
            ("| Case | Stream | PJ | Tier | T4 ratio | 2050 national ratio "
            "| B1 PJ | B1 ratio | B2 PJ | B2 ratio | BC zonal ratio |"),
            "|---|---|---:|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in comparison_rows:
        lines.append(
            f"| `{row['case_id']}` | {row['stream']} "
            f"| {_format_number(float(row['case_pj']))} "
            f"| {row['case_tier']} "
            f"| {float(row['national_t4_ratio']):.3f} "
            f"| {float(row['national_2050_context_ratio']):.3f} "
            f"| {_format_number(float(row['b1_candidate_pj']))} "
            f"| {float(row['b1_ratio']):.3f} "
            f"| {_format_number(float(row['b2_candidate_pj']))} "
            f"| {float(row['b2_ratio']):.3f} "
            f"| {float(row['bc_zonal_ratio']):.3f} |"
        )
    lines.extend(
        [
            "",
            "## Zone and technology mapping",
            "",
            "| Zone | Zone name | Technology suggestion | Mapping kind |",
            "|---|---|---|---|",
        ]
    )
    seen: set[str] = set()
    for row in candidate_rows:
        if row["zone_id"] in seen:
            continue
        seen.add(row["zone_id"])
        lines.append(
            f"| {row['zone_id']} | {row['zone_name']} "
            f"| `{row['technology_suggestion']}` "
            f"| {row['technology_mapping_kind']} |"
        )
    lines.extend(
        [
            "",
            "## Conservation, coverage, and labels",
            "",
            (f"- {EXPECTED_CASE_COUNT} cases, each with 2 streams x "
            f"{EXPECTED_ZONE_COUNT} zones = {EXPECTED_CANDIDATE_ROWS} "
            "candidate rows."),
            ("- Every case/stream zone PJ sum reconciles to the handoff v8 "
            "119m total within 1e-6 PJ."),
            "- Every case/stream covers all 13 OSeCAN zones.",
            ("- The zero availability case is exactly zero for process; it is "
            "the zero anchor, not a physical process-residue estimate."),
            ("- `candidate_status` is "
            f"`{CANDIDATE_STATUS}` on every candidate row."),
            ("- Official derived OSeCAN tree hash before/after: unchanged "
            f"= {summary['osecan_integrity']['unchanged']}."),
            ("- Official SUPBCW schema files before/after: unchanged "
            f"= {summary['official_schema_integrity']['unchanged']}."),
            "",
            "## Caveats",
            "",
        ]
    )
    lines.extend(f"- {caveat}" for caveat in summary["caveats"])
    lines.extend(
        [
            "",
            "## Validation and status",
            "",
            (f"Validation: {summary['validation']['passed']}; failed checks: "
            f"{summary['validation']['failed_check_count']}. "
            f"Build status: `{summary['status']}`."),
            "",
            "Reproduce:",
            "",
            "```bash",
            f".venv/bin/python -m {MODULE_PATH}",
            f".venv/bin/python -m pytest {TEST_PATH} -q",
            "```",
            "",
            "No policy, AAC, tenure, or official OSeCAN claim is made.",
            "",
        ]
    )
    return "\n".join(lines)


def build(output_dir: Path | None = None) -> dict[str, Any]:
    """Build the read-only 12-case D1/D2 candidate package."""
    target_dir = Path(output_dir) if output_dir is not None else OUTPUT_DIR
    resolved_target = target_dir.resolve()
    if (
        resolved_target == OSECAN_DIR.resolve()
        or OSECAN_DIR.resolve() in resolved_target.parents
        or resolved_target == OFFICIAL_MODEL_DIR.resolve()
        or OFFICIAL_MODEL_DIR.resolve() in resolved_target.parents
    ):
        raise OsecanCandidateCasesError(
            "refusing to write inside a read-only OSeCAN tree"
        )
    generated_utc = datetime.now(UTC).isoformat(timespec="seconds")
    assert_no_unexpected_deletions()

    osecan_hashes_before, osecan_tree_before = _hash_tree(OSECAN_DIR)
    official_hashes_before = _hash_official_schemas()

    v7_summary = _read_json(V7_SUMMARY, "v7 summary")
    handoff_summary = _read_json(HANDOFF_SUMMARY, "handoff summary")
    _read_json(V7_MANIFEST, "v7 manifest")
    _read_json(HANDOFF_MANIFEST, "handoff manifest")
    _read_json(DECISION_SUMMARY, "decision summary")
    _parse_labels(v7_summary, "v7 summary")
    _parse_labels(handoff_summary, "handoff summary")

    tiers = _parse_tiers(v7_summary)
    bc_limits = _parse_bc_limits(v7_summary)
    national_2050_context_pj = _parse_national_2050_context(v7_summary)
    totals = _parse_handoff_totals(handoff_summary)
    weights = _parse_zone_weights()
    crosswalk = _parse_crosswalk()
    supply_files = _parse_supply_curve_files()
    official_schema = _parse_official_schema()
    v7_rows = _parse_v7_candidate_rows(tiers)
    _parse_decision_matrix()

    combinations = _case_combinations()
    case_rows = _build_case_index_rows(
        combinations, totals, tiers, crosswalk
    )
    candidate_rows = _build_candidate_rows(combinations, v7_rows)
    comparison_rows = _build_comparison_rows(
        combinations,
        totals,
        candidate_rows,
        tiers,
        bc_limits,
        national_2050_context_pj,
    )

    osecan_hashes_after, osecan_tree_after = _hash_tree(OSECAN_DIR)
    official_hashes_after = _hash_official_schemas()
    osecan_changed_files = sorted(
        path
        for path in set(osecan_hashes_before) | set(osecan_hashes_after)
        if osecan_hashes_before.get(path) != osecan_hashes_after.get(path)
    )
    official_changed_files = sorted(
        path
        for path in set(official_hashes_before) | set(official_hashes_after)
        if official_hashes_before.get(path) != official_hashes_after.get(path)
    )

    validation = _validate(
        combinations=combinations,
        case_rows=case_rows,
        candidate_rows=candidate_rows,
        comparison_rows=comparison_rows,
        totals=totals,
        tiers=tiers,
        bc_limits=bc_limits,
        national_2050_context_pj=national_2050_context_pj,
        weights=weights,
        crosswalk=crosswalk,
        supply_files=supply_files,
        osecan_unchanged=osecan_tree_before == osecan_tree_after,
        osecan_changed_files=osecan_changed_files,
        official_unchanged=official_hashes_before == official_hashes_after,
        official_changed_files=official_changed_files,
    )
    if not validation["passed"]:
        failed = [
            check["check"]
            for check in validation["checks"]
            if not check["passed"]
        ]
        raise OsecanCandidateCasesError(
            f"{len(failed)} validation checks failed: " + ", ".join(failed)
        )
    if osecan_changed_files:
        raise OsecanCandidateCasesError(
            "official OSeCAN files changed during the build: "
            + ", ".join(osecan_changed_files)
        )
    if official_changed_files:
        raise OsecanCandidateCasesError(
            "official SUPBCW schema files changed during the build: "
            + ", ".join(official_changed_files)
        )

    inputs = {
        str(path.relative_to(REPO_ROOT)): _sha256(path)
        for path in (
            V7_CANDIDATES,
            V7_SUMMARY,
            V7_MANIFEST,
            HANDOFF_SUMMARY,
            HANDOFF_WEIGHTS,
            HANDOFF_MANIFEST,
            DECISION_MATRIX,
            DECISION_SUMMARY,
            CROSSWALK,
            TIER_DEFINITIONS,
            TIER_LIMITS,
            BC_ZONAL_LIMITS,
            OFFICIAL_ACTIVITY_LIMIT_SCHEMA,
            OFFICIAL_TECHNOLOGY,
            OFFICIAL_OUTPUT_ACTIVITY,
        )
    }
    summary: dict[str, Any] = {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "title": ARTIFACT_TITLE,
        "generated_utc": generated_utc,
        "status": "pass",
        "scenario": PRIMARY_SCENARIO,
        "scenario_label": REFERENCE_LABEL,
        "candidate_status": CANDIDATE_STATUS,
        "labels": dict(EXPECTED_LABELS),
        "d1_options": [
            {
                "decision_id": "D1",
                "recovery_fraction": option["fraction"],
                "case": option["case"],
                "label": option["label"],
            }
            for option in RECOVERY_OPTIONS
        ],
        "d2_options": [
            {
                "decision_id": "D2",
                "availability_fraction": option["fraction"],
                "case": option["case"],
                "label": option["label"],
            }
            for option in AVAILABILITY_OPTIONS
        ],
        "case_index": case_rows,
        "record_counts": {
            "cases": len(case_rows),
            "streams_per_case": len(STREAM_ORDER),
            "zones": len(ZONE_ORDER),
            "candidate_activity_limit_rows": len(candidate_rows),
            "comparison_rows": len(comparison_rows),
            "d1_recovery_options": len(RECOVERY_OPTIONS),
            "d2_availability_options": len(AVAILABILITY_OPTIONS),
            "province_zone_allocation_rows": len(weights["rows"]),
        },
        "references": {
            "national_supply_curve_tiers_pj": {
                name: {
                    "technology": tiers[name]["technology"],
                    "lower_pj": tiers[name]["lower_pj"],
                    "upper_pj": tiers[name]["upper_pj"],
                }
                for name in ("T1", "T2", "T3", "T4")
            },
            "national_t4_upper_pj": float(tiers["T4"]["upper_pj"]),
            "national_2050_context_pj": national_2050_context_pj,
            "bc_2050_context_pj": float(
                v7_summary["osecan_context"]["bc_supply_pj"]
            ),
            "bc_zonal_upper_pj": float(math.fsum(bc_limits.values())),
            "bc_zonal_limits_pj": dict(bc_limits),
            "b1_official_limit_pj": bc_limits["SUPBCWB1"],
            "b2_official_limit_pj": bc_limits["SUPBCWB2"],
            "reference_is_policy_target": False,
        },
        "zone_technology_mapping": [
            {
                "zone_id": zone_id,
                "zone_name": crosswalk[zone_id],
                "technology_suggestion": BC_ZONE_TECHNOLOGY.get(
                    zone_id, REST_OF_CANADA_TIERS
                ),
                "mapping_kind": (
                    "zone_specific_candidate"
                    if zone_id in BC_ZONE_TECHNOLOGY
                    else "rest_of_canada_tier_pool_candidate"
                ),
                "official_zone_specific_limit": zone_id in BC_ZONE_TECHNOLOGY,
            }
            for zone_id in ZONE_ORDER
        ],
        "official_schema": official_schema,
        "inputs": inputs,
        "osecan_integrity": {
            "root": str(OSECAN_DIR.relative_to(REPO_ROOT)),
            "file_count_before": len(osecan_hashes_before),
            "file_count_after": len(osecan_hashes_after),
            "tree_sha256_before": osecan_tree_before,
            "tree_sha256_after": osecan_tree_after,
            "unchanged": (
                osecan_tree_before == osecan_tree_after
                and not osecan_changed_files
            ),
            "changed_files": osecan_changed_files,
        },
        "official_schema_integrity": {
            "root": str(OFFICIAL_MODEL_DIR.relative_to(REPO_ROOT)),
            "files": {
                path: {
                    "sha256_before": official_hashes_before[path],
                    "sha256_after": official_hashes_after[path],
                    "unchanged": (
                        official_hashes_before[path]
                        == official_hashes_after[path]
                    ),
                }
                for path in sorted(official_hashes_before)
            },
            "unchanged": (
                official_hashes_before == official_hashes_after
                and not official_changed_files
            ),
            "changed_files": official_changed_files,
        },
        "validation": validation,
        "caveats": [
            ("All values are model estimates; observed=false and "
            "policy_grade=false."),
            ("The 119 Mm3 case is a planning allocation, not a legal AAC, "
            "sustainable-supply determination, or observed harvest outcome."),
            ("Zone splits are explicit forestry_osecan_handoff_v8 proxy "
            "weights; no observed sub-provincial residue split exists."),
            ("Process residue is facility-assigned by receiving facility "
            "province; harvest residue is province-only gross residue "
            "multiplied by the recovery fraction."),
            ("Process and harvest residue are separate streams and are never "
            "summed. No combined or total residue value is produced."),
            ("Candidate activity limits are read-only screening values; they "
            "are not applied to any OSeCAN technology, region, or input file."),
            ("Official zone-specific limits exist only for SUPBCWB1 and "
            "SUPBCWB2; other zones have no official zone-specific activity "
            "limit in the adopted curve."),
            ("The national supply-curve tier caps are rest-of-Canada RSYC-only; "
            "national comparisons that include BC are a context screen, not a "
            "like-for-like capacity test."),
            ("Supply-curve and 2050 OSeCAN values are context, not policy "
            "targets, and the comparison is not a capacity-adequacy or "
            "sustainability test."),
            "No UI or public OSeCAN surface is changed by this artifact.",
        ],
        "non_claims": [
            "No policy, AAC, tenure, cost, transport, or investment claim.",
            "No official OSeCAN result or operating plan.",
            "No observed recovery or mill availability measurement.",
        ],
        "reproduce": f".venv/bin/python -m {MODULE_PATH}",
        "test": f".venv/bin/python -m pytest {TEST_PATH} -q",
    }

    case_fields = [
        "case_id",
        "case_order",
        "case_label",
        "scenario",
        "scenario_label",
        "d1_decision_id",
        "d1_recovery_fraction",
        "d1_recovery_case",
        "d1_option_label",
        "d2_decision_id",
        "d2_availability_fraction",
        "d2_availability_case",
        "d2_option_label",
        "harvest_national_pj",
        "harvest_tier",
        "harvest_t4_headroom_pj",
        "process_national_pj",
        "process_tier",
        "process_t4_headroom_pj",
        "zone_count_per_stream",
        "candidate_zone_rows",
        "candidate_status",
        "model_estimate",
        "observed",
        "policy_grade",
        "official_osecan_changed",
        "caveat",
    ]
    candidate_fields = [
        "scenario",
        "case_id",
        "case_order",
        "case_label",
        "d1_recovery_fraction",
        "d2_availability_fraction",
        "stream",
        "stream_case",
        "stream_fraction",
        "zone_id",
        "zone_name",
        "candidate_activity_limit_odt",
        "candidate_activity_limit_pj",
        "technology_suggestion",
        "technology_mapping_kind",
        "technology_mapping_note",
        "official_limit_reference",
        "official_limit_technology",
        "official_limit_pj",
        "official_limit_status",
        "candidate_ratio_to_official_limit",
        "candidate_headroom_pj",
        "candidate_status",
        "candidate_basis",
        "model_estimate",
        "observed",
        "policy_grade",
        "official_osecan_changed",
        "caveat",
    ]
    comparison_fields = [
        "scenario",
        "case_id",
        "case_order",
        "case_label",
        "stream",
        "stream_case",
        "stream_fraction",
        "case_pj",
        "case_tier",
        "national_t4_upper_pj",
        "national_t4_ratio",
        "national_t4_headroom_pj",
        "national_2050_context_pj",
        "national_2050_context_ratio",
        "national_2050_context_headroom_pj",
        "bc_case_pj",
        "bc_official_zonal_upper_pj",
        "bc_zonal_ratio",
        "bc_zonal_headroom_pj",
        "b1_candidate_pj",
        "b1_official_limit_pj",
        "b1_ratio",
        "b1_headroom_pj",
        "b2_candidate_pj",
        "b2_official_limit_pj",
        "b2_ratio",
        "b2_headroom_pj",
        "reference_is_policy_target",
        "model_estimate",
        "observed",
        "policy_grade",
        "official_osecan_changed",
        "comparison_note",
    ]

    target_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(target_dir / "case_index.csv", case_rows, case_fields)
    _write_csv(
        target_dir / "candidate_activity_limits.csv",
        candidate_rows,
        candidate_fields,
    )
    _write_csv(
        target_dir / "comparison_supply_curve.csv",
        comparison_rows,
        comparison_fields,
    )
    (target_dir / REPORT_NAME).write_text(
        _report(summary, case_rows, comparison_rows, candidate_rows),
        encoding="utf-8",
    )

    outputs: dict[str, Any] = {}
    for name in (
        "case_index.csv",
        "candidate_activity_limits.csv",
        "comparison_supply_curve.csv",
        REPORT_NAME,
    ):
        path = target_dir / name
        outputs[name] = {
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }
    summary["outputs"] = outputs
    _write_json(target_dir / "summary.json", summary)
    outputs["summary.json"] = {
        "bytes": (target_dir / "summary.json").stat().st_size,
        "sha256": _sha256(target_dir / "summary.json"),
    }
    manifest = {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "title": ARTIFACT_TITLE,
        "generated_utc": generated_utc,
        "status": "pass",
        "scenario": PRIMARY_SCENARIO,
        "candidate_status": CANDIDATE_STATUS,
        "labels": dict(EXPECTED_LABELS),
        "record_counts": summary["record_counts"],
        "case_index": case_rows,
        "references": summary["references"],
        "zone_technology_mapping": summary["zone_technology_mapping"],
        "official_schema": summary["official_schema"],
        "inputs": inputs,
        "outputs": outputs,
        "osecan_integrity": summary["osecan_integrity"],
        "official_schema_integrity": summary["official_schema_integrity"],
        "validation": validation,
        "reproduce": summary["reproduce"],
        "test": summary["test"],
    }
    _write_json(target_dir / "manifest.json", manifest)

    assert_no_unexpected_deletions()
    return summary


def main() -> None:
    summary = build()
    print(
        json.dumps(
            {
                "artifact": summary["artifact"],
                "status": summary["status"],
                "cases": summary["record_counts"]["cases"],
                "candidate_rows": summary["record_counts"][
                    "candidate_activity_limit_rows"
                ],
                "comparison_rows": summary["record_counts"][
                    "comparison_rows"
                ],
                "validation_passed": summary["validation"]["passed"],
                "osecan_unchanged": summary["osecan_integrity"]["unchanged"],
                "official_schema_unchanged": summary[
                    "official_schema_integrity"
                ]["unchanged"],
                "output_dir": str(OUTPUT_DIR.relative_to(REPO_ROOT)),
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
