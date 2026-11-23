"""Plackett-Burman global sensitivity v2 for the national facility chain.

This refreshes the seven-factor, 16-run screening design from sensitivity v1 to
the frozen v4 E1 + E2c chain:

1. capacity/assignment: local v6 ``province_capacity_floor`` vs trade v5
   ``published_plus_proxy``;
2. product coefficients: donor v1 vs E1 measured + E2c (product chain v5);
3. residue basis: stem-only vs stem + branch/foliage;
4. harvest recovery: 0.35 / 0.60 / 0.75;
5. process availability: 0 / 25 / 50 / 75%;
6. price scenario: ``donor_flat`` vs ``stumpage_scaled`` (economics v2.3);
7. zone weights: OSeCAN handoff v8 supply/dispatch vs equal split.

The design, response arithmetic, alias structure, and screening labels are the
same as v1.  The response values change because the E2c coefficient level
replaces the E1 four-province measured level and because the v4 chain, OSeCAN
handoff v8 weights, and E2c branch/foliage evidence are used.

All values are ``model_estimate`` / screening results: ``observed=false``,
``policy_grade=false``, ``national_claim=false``.  No policy, AAC, investment,
or national-supply claim is made.  No UI or official OSeCAN surface is changed.
"""

from __future__ import annotations

import csv
import hashlib
import itertools
import json
import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from workflows.national.facilities import (
    national_facility_chain_scenarios as _base,
)

REPO_ROOT = _base.REPO_ROOT
DATA_ROOT = _base.DATA_ROOT
FACILITY_DIR = DATA_ROOT / "derived/national/facilities"
OUTPUT_DIR = FACILITY_DIR / "national_chain_global_sensitivity_v2"
MARGIN_JSON = (
    DATA_ROOT
    / "derived/national/harvest_basis/national_margin_totals/"
    "national_margin_totals.json"
)
V1_DIR = FACILITY_DIR / "national_chain_global_sensitivity_v1"
V1_SUMMARY = V1_DIR / "summary.json"
V1_OVERALL = V1_DIR / "overall_ranking.csv"
V1_EFFECTS = V1_DIR / "main_effects.csv"

ARTIFACT = "national_chain_global_sensitivity_v2"
ARTIFACT_VERSION = "1.0.0"
ARTIFACT_TITLE = "National Facility Chain Global Sensitivity V2 (E1+E2c)"
REPORT_NAME = "NATIONAL_CHAIN_GLOBAL_SENSITIVITY_V2.md"
MODULE_PATH = (
    "workflows.national.facilities.national_chain_global_sensitivity_v2"
)
TEST_PATH = "tests/test_national_chain_global_sensitivity_v2.py"

EVIDENCE_LABEL = "model_estimate"
OBSERVED = False
POLICY_GRADE = False
NATIONAL_CLAIM = False
SCREENING_ONLY = True
OFFICIAL_OSECAN_CHANGED = False
NOT_POLICY = True

LHV_GJ_PER_ODT = 18.5
PJ_GJ = 1_000_000.0
REL_TOL = 1e-8
ABS_TOL = 1e-6
HARVEST_CASE = "119m"
EXPECTED_HARVEST_M3 = 118_977_339.1
DESIGN_TYPE = "plackett_burman_hadamard_16_run"
DESIGN_RUNS = 16
PB_COLUMNS = (1, 2, 4, 7, 9, 10, 12)
FACTOR_COUNT = 7
MATERIAL_EFFECT_THRESHOLD = 1e-4
MEASURED_PROVINCES = ("BC", "NB", "QC", "YT")
E2C_PROVINCES = (
    "AB",
    "MB",
    "NL",
    "NS",
    "NT",
    "NU",
    "ON",
    "PE",
    "SK",
)


class GlobalSensitivityV2Error(RuntimeError):
    """Raised when a source artifact or design invariant is not trusted."""


@dataclass(frozen=True)
class Factor:
    """One screening factor and its documented levels."""

    key: str
    label: str
    short_label: str
    low: Any
    high: Any
    documented_levels: tuple[Any, ...]
    unit: str
    source_note: str


@dataclass(frozen=True)
class ResponseDefinition:
    """One response column and its documented arithmetic."""

    key: str
    label: str
    unit: str
    role: str
    definition: str


@dataclass(frozen=True)
class ChainSourceSpec:
    """One precomputed chain/supply artifact pair."""

    key: str
    chain_version: str
    supply_version: str
    assignment: str
    coefficients: str
    description: str


FACTORS: tuple[Factor, ...] = (
    Factor(
        key="capacity_assignment",
        label="Capacity / assignment",
        short_label="assignment",
        low="local_v6",
        high="trade_v5",
        documented_levels=("local_v6", "trade_v5"),
        unit="case",
        source_note=(
            "local v6 province_capacity_floor vs trade v5 published_plus_proxy; "
            "both levels use the same E2c or donor coefficient source"
        ),
    ),
    Factor(
        key="product_coefficients",
        label="Product coefficients",
        short_label="coefficients",
        low="donor_v1",
        high="e2c",
        documented_levels=("donor_v1", "e2c"),
        unit="case",
        source_note=(
            "donor product chain v1 vs E1 measured + E2c product chain v5 "
            "(nine density-calibrated provinces, four measured provinces)"
        ),
    ),
    Factor(
        key="residue_basis",
        label="Residue basis",
        short_label="residue",
        low="stem_only",
        high="stem_plus_branch_foliage",
        documented_levels=("stem_only", "stem_plus_branch_foliage"),
        unit="case",
        source_note=(
            "stem-only harvest residue vs stem + branch/foliage; donor-total "
            "adds only the measured four-province branch/foliage increment"
        ),
    ),
    Factor(
        key="harvest_recovery",
        label="Harvest recovery",
        short_label="recovery",
        low=0.35,
        high=0.75,
        documented_levels=(0.35, 0.60, 0.75),
        unit="fraction",
        source_note=(
            "documented 0.35/0.60/0.75 recovery scenarios; 0.60 is a "
            "reference value, not a calibrated factor"
        ),
    ),
    Factor(
        key="process_availability",
        label="Process availability",
        short_label="availability",
        low=0.0,
        high=0.75,
        documented_levels=(0.0, 0.25, 0.50, 0.75),
        unit="fraction",
        source_note="documented 0/25/50/75% process-availability screening cases",
    ),
    Factor(
        key="price_scenario",
        label="Price scenario",
        short_label="price",
        low="donor_flat",
        high="stumpage_scaled",
        documented_levels=("donor_flat", "stumpage_scaled"),
        unit="case",
        source_note=(
            "national_economics_v2_3 donor_flat base vs stumpage_scaled "
            "sensitivity (same v2.3 margin totals as v1)"
        ),
    ),
    Factor(
        key="zone_weights",
        label="Zone weights",
        short_label="zone",
        low="osecan_supply_dispatch",
        high="equal_split",
        documented_levels=("osecan_supply_dispatch", "equal_split"),
        unit="case",
        source_note=(
            "OSeCAN handoff v8 supply/dispatch weights vs equal split for "
            "multi-zone provinces; sub-provincial allocation only"
        ),
    ),
)

RESPONSES: tuple[ResponseDefinition, ...] = (
    ResponseDefinition(
        key="national_process_residue_pj",
        label="National process residue",
        unit="PJ",
        role="required",
        definition=("facility process residue base x process availability fraction"),
    ),
    ResponseDefinition(
        key="harvest_gross_pj",
        label="Harvest gross residue",
        unit="PJ",
        role="required",
        definition=("gross harvest residue before recovery; residue-basis dependent"),
    ),
    ResponseDefinition(
        key="harvest_recoverable_pj",
        label="Harvest recoverable residue",
        unit="PJ",
        role="required",
        definition="harvest gross residue x harvest recovery fraction",
    ),
    ResponseDefinition(
        key="total_gross_pj",
        label="Total gross residue",
        unit="PJ",
        role="required",
        definition=(
            "national process residue at availability + harvest gross residue"
        ),
    ),
    ResponseDefinition(
        key="national_margin_m_cad",
        label="National modeled margin",
        unit="M CAD",
        role="required",
        definition=(
            "national_margin_totals national_total_base_m_cad for the "
            "selected price scenario at the 119 Mm3 allocation"
        ),
    ),
    ResponseDefinition(
        key="bc_process_pj",
        label="BC process residue",
        unit="PJ",
        role="required",
        definition=("BC facility process residue base x process availability fraction"),
    ),
    ResponseDefinition(
        key="bc_b2_process_pj",
        label="BC B2 process residue (auxiliary)",
        unit="PJ",
        role="auxiliary",
        definition=(
            "BC process residue x BC zone-B2 allocation weight; diagnostic "
            "for the zone-weight factor"
        ),
    ),
    ResponseDefinition(
        key="national_total_recoverable_pj",
        label="Total recoverable residue (auxiliary)",
        unit="PJ",
        role="auxiliary",
        definition=(
            "national process residue at availability + harvest recoverable residue"
        ),
    ),
)

REQUIRED_RESPONSE_KEYS: tuple[str, ...] = tuple(
    response.key for response in RESPONSES if response.role == "required"
)
RESPONSE_BY_KEY: dict[str, ResponseDefinition] = {
    response.key: response for response in RESPONSES
}

PROCESS_SOURCE_SPECS: tuple[ChainSourceSpec, ...] = (
    ChainSourceSpec(
        key="local_donor",
        chain_version="3",
        supply_version="3",
        assignment="local_v6",
        coefficients="donor_v1",
        description=(
            "assignment-v6 local chain v3: donor product coefficients with "
            "stem-only donor harvest residue"
        ),
    ),
    ChainSourceSpec(
        key="trade_donor",
        chain_version="2",
        supply_version="2",
        assignment="trade_v5",
        coefficients="donor_v1",
        description=(
            "assignment-v5 trade chain v2: donor product coefficients with "
            "stem-only donor harvest residue"
        ),
    ),
    ChainSourceSpec(
        key="local_e2c",
        chain_version="7",
        supply_version="7",
        assignment="local_v6",
        coefficients="e2c",
        description=(
            "assignment-v6 local chain v7: E1 measured + E2c product "
            "coefficients with E2c branch/foliage total residue"
        ),
    ),
    ChainSourceSpec(
        key="trade_e2c",
        chain_version="7",
        supply_version="7_trade",
        assignment="trade_v5",
        coefficients="e2c",
        description=(
            "derived: assignment-v5 trade facility residue supply v7_trade "
            "with E1 measured + E2c coefficients; national process and "
            "harvest totals are assignment-invariant"
        ),
    ),
)

BASELINE: dict[str, Any] = {
    "capacity_assignment": "local_v6",
    "product_coefficients": "e2c",
    "residue_basis": "stem_plus_branch_foliage",
    "harvest_recovery": 0.60,
    "process_availability": 0.50,
    "price_scenario": "donor_flat",
    "zone_weights": "osecan_supply_dispatch",
}


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _relative(path: Path) -> str:
    return str(path.relative_to(REPO_ROOT))


def _read_json(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise GlobalSensitivityV2Error(f"{label} is missing: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise GlobalSensitivityV2Error(f"{label} is not a JSON object: {path}")
    return payload


def _read_csv(path: Path, label: str) -> list[dict[str, str]]:
    if not path.is_file():
        raise GlobalSensitivityV2Error(f"{label} is missing: {path}")
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise GlobalSensitivityV2Error(f"{label} is empty: {path}")
    return rows


def _finite(value: Any, label: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as error:
        raise GlobalSensitivityV2Error(
            f"{label} is not numeric: {value!r}"
        ) from error
    if not math.isfinite(parsed):
        raise GlobalSensitivityV2Error(f"{label} is not finite: {parsed!r}")
    return parsed


def _finite_nonnegative(value: Any, label: str) -> float:
    parsed = _finite(value, label)
    if parsed < 0.0:
        raise GlobalSensitivityV2Error(f"{label} is negative: {parsed!r}")
    return parsed


def _require_close(left: float, right: float, label: str) -> None:
    scale = max(abs(left), abs(right), 1.0)
    if abs(left - right) > REL_TOL * scale:
        raise GlobalSensitivityV2Error(
            f"{label} mismatch: {left!r} != {right!r} "
            f"(relative {abs(left - right) / scale:.3e})"
        )


def _bool_text(value: bool) -> str:
    return "true" if value else "false"


def _number(value: float | None, digits: int = 12) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return _bool_text(value)
    if isinstance(value, int):
        return str(value)
    parsed = float(value)
    if not math.isfinite(parsed):
        raise GlobalSensitivityV2Error(
            f"refusing to write non-finite value: {parsed!r}"
        )
    return f"{parsed:.{digits}g}"


def _format(value: float, digits: int = 3) -> str:
    return f"{value:,.{digits}f}"


def _mean(values: Sequence[float]) -> float:
    if not values:
        raise GlobalSensitivityV2Error("cannot average an empty sequence")
    return math.fsum(values) / len(values)


def assert_no_unexpected_deletions() -> None:
    """Fail closed on any ``D`` in the parent or data repository status."""

    _base.assert_no_unexpected_deletions()


def _source_labels(summary: Mapping[str, Any], path: Path) -> dict[str, Any]:
    labels = summary.get("labels")
    if not isinstance(labels, dict):
        labels = {}
    model_estimate = labels.get("model_estimate")
    if model_estimate is None:
        model_estimate = summary.get("model_estimate")
    if model_estimate is None:
        model_estimate = labels.get("evidence_label") == EVIDENCE_LABEL
    observed = labels.get("observed")
    if observed is None:
        observed = summary.get("observed")
    policy_grade = labels.get("policy_grade")
    if policy_grade is None:
        policy_grade = summary.get("policy_grade")
    if model_estimate is not True:
        raise GlobalSensitivityV2Error(f"{path}: source is not model_estimate")
    if observed is not False:
        raise GlobalSensitivityV2Error(f"{path}: source observed is not false")
    if policy_grade is not False:
        raise GlobalSensitivityV2Error(
            f"{path}: source policy_grade is not false"
        )
    return labels


def _read_bc_process(path: Path, lhv: float) -> dict[str, float]:
    rows = _read_csv(path, "province residue supply")
    matches = [
        row
        for row in rows
        if row.get("province") == "BC"
        and row.get("residue_type") == "process_residue"
    ]
    if len(matches) != 1:
        raise GlobalSensitivityV2Error(
            f"{path}: expected one BC process_residue row, got {len(matches)}"
        )
    row = matches[0]
    if row.get("residue_province_basis") != "facility_province":
        raise GlobalSensitivityV2Error(
            f"{path}: BC process residue is not facility-province based"
        )
    residue_odt = _finite_nonnegative(
        row.get("residue_odt"), f"{path}: BC process residue_odt"
    )
    residue_pj = _finite_nonnegative(
        row.get("residue_pj"), f"{path}: BC process residue_pj"
    )
    row_lhv = _finite_nonnegative(
        row.get("lhv_gj_per_odt"), f"{path}: BC process LHV"
    )
    _require_close(row_lhv, lhv, f"{path}: BC process LHV")
    _require_close(
        residue_pj,
        residue_odt * lhv / PJ_GJ,
        f"{path}: BC process PJ conversion",
    )
    return {"bc_process_base_odt": residue_odt, "bc_process_base_pj": residue_pj}


def _load_chain_source(spec: ChainSourceSpec) -> dict[str, Any]:
    chain_path = (
        FACILITY_DIR
        / f"national_facility_chain_scenarios_v{spec.chain_version}"
        / "summary.json"
    )
    supply_dir = FACILITY_DIR / f"facility_residue_supply_v{spec.supply_version}"
    supply_summary_path = supply_dir / "summary.json"
    supply_province_path = supply_dir / "province_residue_supply.csv"

    summary = _read_json(chain_path, f"chain {spec.chain_version} summary")
    expected_artifact = (
        f"national_facility_chain_scenarios_v{spec.chain_version}"
    )
    if summary.get("artifact") != expected_artifact:
        raise GlobalSensitivityV2Error(
            f"{chain_path}: unexpected artifact {summary.get('artifact')!r}"
        )
    _source_labels(summary, chain_path)
    scenarios = summary.get("scenarios")
    if not isinstance(scenarios, dict) or HARVEST_CASE not in scenarios:
        raise GlobalSensitivityV2Error(
            f"{chain_path}: missing {HARVEST_CASE} scenario"
        )
    row = scenarios[HARVEST_CASE]
    harvest_m3 = _finite_nonnegative(
        row.get("harvest_m3"), f"{chain_path}: harvest_m3"
    )
    if abs(harvest_m3 - EXPECTED_HARVEST_M3) > 1.0:
        raise GlobalSensitivityV2Error(
            f"{chain_path}: harvest {harvest_m3!r} is not the 119 Mm3 case"
        )
    lhv = _finite_nonnegative(row.get("lhv_gj_per_odt"), f"{chain_path}: LHV")
    _require_close(lhv, LHV_GJ_PER_ODT, f"{chain_path}: LHV")
    process_odt = _finite_nonnegative(
        row.get("process_residue_odt"), f"{chain_path}: process residue odt"
    )
    process_pj = _finite_nonnegative(
        row.get("process_residue_pj"), f"{chain_path}: process residue PJ"
    )
    gross_odt = _finite_nonnegative(
        row.get("harvest_residue_gross_odt"),
        f"{chain_path}: harvest gross odt",
    )
    gross_pj = _finite_nonnegative(
        row.get("harvest_residue_gross_pj"),
        f"{chain_path}: harvest gross PJ",
    )
    total_pj = _finite_nonnegative(
        row.get("total_residue_gross_pj"), f"{chain_path}: total gross PJ"
    )
    _require_close(
        process_pj,
        process_odt * lhv / PJ_GJ,
        f"{chain_path}: process PJ conversion",
    )
    _require_close(
        gross_pj,
        gross_odt * lhv / PJ_GJ,
        f"{chain_path}: harvest gross PJ conversion",
    )
    _require_close(
        total_pj,
        process_pj + gross_pj,
        f"{chain_path}: total gross arithmetic",
    )

    supply_summary = _read_json(
        supply_summary_path, f"supply v{spec.supply_version} summary"
    )
    expected_supply = f"facility_residue_supply_v{spec.supply_version}"
    if supply_summary.get("artifact") != expected_supply:
        raise GlobalSensitivityV2Error(
            f"{supply_summary_path}: unexpected artifact "
            f"{supply_summary.get('artifact')!r}"
        )
    _source_labels(supply_summary, supply_summary_path)
    national = supply_summary.get("national_totals")
    if not isinstance(national, dict):
        raise GlobalSensitivityV2Error(
            f"{supply_summary_path}: missing national_totals"
        )
    _require_close(
        _finite_nonnegative(
            national.get("process_residue_pj"),
            f"{supply_summary_path}: national process PJ",
        ),
        process_pj,
        f"{supply_summary_path}: process residue matches chain",
    )
    _require_close(
        _finite_nonnegative(
            national.get("harvest_residue_gross_pj"),
            f"{supply_summary_path}: national harvest gross PJ",
        ),
        gross_pj,
        f"{supply_summary_path}: harvest gross matches chain",
    )
    bc = _read_bc_process(supply_province_path, lhv)
    return {
        "key": spec.key,
        "chain_version": spec.chain_version,
        "supply_version": spec.supply_version,
        "assignment": spec.assignment,
        "coefficients": spec.coefficients,
        "description": spec.description,
        "chain_path": chain_path,
        "supply_summary_path": supply_summary_path,
        "supply_province_path": supply_province_path,
        "harvest_m3": harvest_m3,
        "lhv_gj_per_odt": lhv,
        "process_base_odt": process_odt,
        "process_base_pj": process_pj,
        "harvest_gross_odt": gross_odt,
        "harvest_gross_pj": gross_pj,
        "total_full_process_plus_harvest_pj": total_pj,
        "bc_process_base_odt": bc["bc_process_base_odt"],
        "bc_process_base_pj": bc["bc_process_base_pj"],
        "derived": False,
        "derivation": "",
    }


def _load_trade_e2c_source(local_e2c: Mapping[str, Any]) -> dict[str, Any]:
    """Derive trade/v5 process placement from supply v7_trade."""

    supply_dir = FACILITY_DIR / "facility_residue_supply_v7_trade"
    supply_summary_path = supply_dir / "summary.json"
    supply_province_path = supply_dir / "province_residue_supply.csv"
    summary = _read_json(supply_summary_path, "supply v7_trade summary")
    if summary.get("artifact") != "facility_residue_supply_v7_trade":
        raise GlobalSensitivityV2Error(
            f"{supply_summary_path}: unexpected artifact "
            f"{summary.get('artifact')!r}"
        )
    _source_labels(summary, supply_summary_path)
    trade = summary.get("trade_sensitivity")
    if not isinstance(trade, dict) or trade.get("enabled") is not True:
        raise GlobalSensitivityV2Error(
            f"{supply_summary_path}: trade sensitivity is not enabled"
        )
    if trade.get("assignment_artifact") != "national_facility_assignment_v5":
        raise GlobalSensitivityV2Error(
            f"{supply_summary_path}: unexpected assignment artifact"
        )
    product_chain = summary.get("product_chain")
    if not isinstance(product_chain, dict) or product_chain.get(
        "artifact"
    ) != "national_product_chain_v5":
        raise GlobalSensitivityV2Error(
            f"{supply_summary_path}: product chain is not national_product_chain_v5"
        )
    national = summary.get("national_totals")
    if not isinstance(national, dict):
        raise GlobalSensitivityV2Error(
            f"{supply_summary_path}: missing national_totals"
        )
    process_pj = _finite_nonnegative(
        national.get("process_residue_pj"),
        f"{supply_summary_path}: national process PJ",
    )
    gross_pj = _finite_nonnegative(
        national.get("harvest_residue_gross_pj"),
        f"{supply_summary_path}: national harvest gross PJ",
    )
    _require_close(
        process_pj,
        float(local_e2c["process_base_pj"]),
        "trade E2c process residue is assignment-invariant",
    )
    _require_close(
        gross_pj,
        float(local_e2c["harvest_gross_pj"]),
        "trade E2c harvest gross is assignment-invariant",
    )
    bc = _read_bc_process(supply_province_path, LHV_GJ_PER_ODT)
    return {
        **local_e2c,
        "key": "trade_e2c",
        "chain_version": "7_trade_derived",
        "supply_version": "7_trade",
        "assignment": "trade_v5",
        "coefficients": "e2c",
        "description": (
            "derived: assignment-v5 trade facility residue supply v7_trade "
            "with E1 measured + E2c coefficients; national process and "
            "harvest totals are assignment-invariant"
        ),
        "supply_summary_path": supply_summary_path,
        "supply_province_path": supply_province_path,
        "bc_process_base_odt": bc["bc_process_base_odt"],
        "bc_process_base_pj": bc["bc_process_base_pj"],
        "derived": True,
        "derivation": (
            "process and harvest national totals from local chain v7; BC "
            "process and facility-province placement from supply v7_trade"
        ),
    }


def _load_product_chain_components() -> dict[str, Any]:
    """Load donor, E1 measured, and E2c harvest-residue components."""

    v1_path = FACILITY_DIR / "national_product_chain_v1/product_chain_by_province.csv"
    v2_path = FACILITY_DIR / "national_product_chain_v2/product_chain_by_province.csv"
    v3_path = FACILITY_DIR / "national_product_chain_v3/product_chain_by_province.csv"
    v3_summary_path = FACILITY_DIR / "national_product_chain_v3/summary.json"
    v5_path = FACILITY_DIR / "national_product_chain_v5/product_chain_by_province.csv"
    v5_summary_path = FACILITY_DIR / "national_product_chain_v5/summary.json"
    v1_rows = _read_csv(v1_path, "product chain v1 province table")
    v2_rows = _read_csv(v2_path, "product chain v2 province table")
    v3_rows = _read_csv(v3_path, "product chain v3 province table")
    v5_rows = _read_csv(v5_path, "product chain v5 province table")

    def gross_by_province(
        rows: Sequence[Mapping[str, str]], column: str, label: str
    ) -> dict[str, float]:
        return {
            row["province"]: _finite_nonnegative(
                row[column], f"{label}: {row['province']} {column}"
            )
            for row in rows
        }

    v1 = gross_by_province(v1_rows, "harvest_residue_gross_odt", str(v1_path))
    v2 = gross_by_province(v2_rows, "harvest_residue_gross_odt", str(v2_path))
    v3 = gross_by_province(v3_rows, "harvest_residue_gross_odt", str(v3_path))
    v5_stem = gross_by_province(
        v5_rows, "harvest_residue_stem_gross_odt", str(v5_path)
    )
    v5_bf = gross_by_province(
        v5_rows, "harvest_residue_branch_foliage_odt", str(v5_path)
    )
    v5_total = gross_by_province(
        v5_rows, "harvest_residue_total_gross_odt", str(v5_path)
    )
    if set(v1) != set(v2) or set(v2) != set(v3) or set(v3) != set(v5_stem):
        raise GlobalSensitivityV2Error(
            "product chain v1/v2/v3/v5 province sets differ"
        )
    if not set(MEASURED_PROVINCES).issubset(set(v3)):
        raise GlobalSensitivityV2Error(
            "measured province set is not present in product chain v3"
        )
    measured_summary = _read_json(v3_summary_path, "product chain v3 summary")
    measured = {
        str(province)
        for province in measured_summary.get("measured_provinces", [])
    }
    if measured != set(MEASURED_PROVINCES):
        raise GlobalSensitivityV2Error(
            f"{v3_summary_path}: measured provinces {sorted(measured)} do not "
            f"match {sorted(MEASURED_PROVINCES)}"
        )
    v5_summary = _read_json(v5_summary_path, "product chain v5 summary")
    _source_labels(v5_summary, v5_summary_path)
    if v5_summary.get("artifact") != "national_product_chain_v5":
        raise GlobalSensitivityV2Error(
            f"{v5_summary_path}: unexpected artifact"
        )
    measured_v5 = {
        str(province) for province in v5_summary.get("measured_provinces", [])
    }
    density_v5 = {
        str(province)
        for province in v5_summary.get("imputed_provinces", [])
    }
    if measured_v5 != set(MEASURED_PROVINCES):
        raise GlobalSensitivityV2Error(
            f"{v5_summary_path}: measured provinces are not the four E1 provinces"
        )
    if density_v5 != set(E2C_PROVINCES):
        raise GlobalSensitivityV2Error(
            f"{v5_summary_path}: density-calibrated provinces are not the nine E2c"
        )

    rows: list[dict[str, Any]] = []
    measured_bf_odt = 0.0
    e2c_bf_odt = 0.0
    for province in sorted(v3):
        _require_close(
            v5_stem[province] + v5_bf[province],
            v5_total[province],
            f"{province}: E2c stem + branch/foliage equals total",
        )
        measured_increment = v3[province] - v2[province]
        if measured_increment < -ABS_TOL:
            raise GlobalSensitivityV2Error(
                f"{province}: product chain v3 gross is below v2"
            )
        if measured_increment > ABS_TOL and province not in measured:
            raise GlobalSensitivityV2Error(
                f"{province}: measured branch/foliage increment exists outside "
                "the measured province set"
            )
        measured_bf_odt += max(measured_increment, 0.0)
        e2c_bf_odt += v5_bf[province]
        rows.append(
            {
                "province": province,
                "donor_stem_gross_odt": v2[province],
                "measured_total_gross_odt": v3[province],
                "measured_branch_foliage_increment_odt": measured_increment,
                "e2c_stem_gross_odt": v5_stem[province],
                "e2c_branch_foliage_odt": v5_bf[province],
                "e2c_total_gross_odt": v5_total[province],
                "measured_branch_foliage": province in measured,
                "e2c_density_calibrated": province in density_v5,
            }
        )
    if measured_bf_odt <= 0.0:
        raise GlobalSensitivityV2Error(
            "measured branch/foliage increment is not positive"
        )
    if e2c_bf_odt <= 0.0:
        raise GlobalSensitivityV2Error("E2c branch/foliage is not positive")
    return {
        "rows": rows,
        "donor_stem_odt": math.fsum(v1.values()),
        "measured_bf_odt": measured_bf_odt,
        "e2c_stem_odt": math.fsum(v5_stem.values()),
        "e2c_bf_odt": e2c_bf_odt,
        "e2c_total_odt": math.fsum(v5_total.values()),
        "donor_stem_pj": math.fsum(v1.values()) * LHV_GJ_PER_ODT / PJ_GJ,
        "measured_bf_pj": measured_bf_odt * LHV_GJ_PER_ODT / PJ_GJ,
        "e2c_stem_pj": math.fsum(v5_stem.values()) * LHV_GJ_PER_ODT / PJ_GJ,
        "e2c_bf_pj": e2c_bf_odt * LHV_GJ_PER_ODT / PJ_GJ,
        "e2c_total_pj": math.fsum(v5_total.values()) * LHV_GJ_PER_ODT / PJ_GJ,
        "v1_path": v1_path,
        "v2_path": v2_path,
        "v3_path": v3_path,
        "v3_summary_path": v3_summary_path,
        "v5_path": v5_path,
        "v5_summary_path": v5_summary_path,
    }


def _load_margin_values() -> dict[str, Any]:
    payload = _read_json(MARGIN_JSON, "national margin totals")
    scenarios = payload.get("scenarios")
    if not isinstance(scenarios, dict):
        raise GlobalSensitivityV2Error(f"{MARGIN_JSON}: missing scenarios")
    values: dict[str, float] = {}
    for scenario in ("donor_flat", "stumpage_scaled"):
        row = scenarios.get(scenario)
        if not isinstance(row, dict):
            raise GlobalSensitivityV2Error(
                f"{MARGIN_JSON}: missing scenario {scenario}"
            )
        if row.get("evidence") != EVIDENCE_LABEL:
            raise GlobalSensitivityV2Error(
                f"{MARGIN_JSON}: {scenario} evidence is not {EVIDENCE_LABEL}"
            )
        if row.get("observed") is not False:
            raise GlobalSensitivityV2Error(
                f"{MARGIN_JSON}: {scenario} observed is not false"
            )
        if row.get("policy_grade") is not False:
            raise GlobalSensitivityV2Error(
                f"{MARGIN_JSON}: {scenario} policy_grade is not false"
            )
        values[scenario] = _finite(
            row.get("national_total_base_m_cad"),
            f"{MARGIN_JSON}: {scenario} national_total_base_m_cad",
        )
    allocation_total = _finite_nonnegative(
        payload.get("allocation_total_m3"),
        f"{MARGIN_JSON}: allocation_total_m3",
    )
    if abs(allocation_total - EXPECTED_HARVEST_M3) > 1.0:
        raise GlobalSensitivityV2Error(
            f"{MARGIN_JSON}: allocation total is not the 119 Mm3 case"
        )
    if payload.get("source_model_id") != "national_economics_v2_3":
        raise GlobalSensitivityV2Error(
            f"{MARGIN_JSON}: source model is not national_economics_v2_3"
        )
    return {"values": values, "payload": payload, "path": MARGIN_JSON}


def _load_zone_weights() -> dict[str, Any]:
    path = FACILITY_DIR / "forestry_osecan_handoff_v8/zone_allocation_weights.csv"
    rows = _read_csv(path, "zone allocation weights")
    bc_rows = [row for row in rows if row.get("province") == "BC"]
    if len(bc_rows) != 2:
        raise GlobalSensitivityV2Error(
            f"{path}: expected two BC zone rows, got {len(bc_rows)}"
        )
    zones = {row["zone_id"]: row for row in bc_rows}
    if set(zones) != {"B1", "B2"}:
        raise GlobalSensitivityV2Error(
            f"{path}: unexpected BC zone ids {sorted(zones)}"
        )
    weights: dict[str, float] = {}
    for zone_id, row in zones.items():
        weight = _finite_nonnegative(
            row.get("weight"), f"{path}: BC {zone_id} weight"
        )
        if weight <= 0.0 or weight > 1.0:
            raise GlobalSensitivityV2Error(
                f"{path}: BC {zone_id} weight is outside (0,1)"
            )
        weights[zone_id] = weight
    _require_close(
        math.fsum(weights.values()), 1.0, f"{path}: BC weights sum"
    )
    if zones["B1"].get("model_estimate") != "true":
        raise GlobalSensitivityV2Error(
            f"{path}: zone weights are not model estimates"
        )
    if zones["B1"].get("observed") != "false":
        raise GlobalSensitivityV2Error(f"{path}: zone weights are not unobserved")
    if zones["B1"].get("policy_grade") != "false":
        raise GlobalSensitivityV2Error(
            f"{path}: zone weights are not non-policy"
        )
    equal = 1.0 / len(weights)
    return {
        "path": path,
        "osecan_supply_dispatch": {
            "BC_B1": weights["B1"],
            "BC_B2": weights["B2"],
            "method": zones["B1"].get("weight_method", ""),
            "source_value_B1_pj": _finite_nonnegative(
                zones["B1"].get("source_value"),
                f"{path}: BC B1 source_value",
            ),
            "source_value_B2_pj": _finite_nonnegative(
                zones["B2"].get("source_value"),
                f"{path}: BC B2 source_value",
            ),
        },
        "equal_split": {
            "BC_B1": equal,
            "BC_B2": equal,
            "method": "equal_split",
            "source_value_B1_pj": None,
            "source_value_B2_pj": None,
        },
    }


def _load_v1_comparison() -> dict[str, Any]:
    summary = _read_json(V1_SUMMARY, "sensitivity v1 summary")
    if summary.get("artifact") != "national_chain_global_sensitivity_v1":
        raise GlobalSensitivityV2Error(
            f"{V1_SUMMARY}: unexpected artifact"
        )
    overall_rows = _read_csv(V1_OVERALL, "sensitivity v1 overall ranking")
    effect_rows = _read_csv(V1_EFFECTS, "sensitivity v1 main effects")
    overall = {
        row["factor_key"]: {
            "rank": int(row["rank"]),
            "mean_normalized_abs_effect": _finite(
                row["mean_normalized_abs_effect"],
                f"{V1_OVERALL}: {row['factor_key']} score",
            ),
        }
        for row in overall_rows
    }
    if len(overall) != FACTOR_COUNT:
        raise GlobalSensitivityV2Error(
            f"{V1_OVERALL}: expected {FACTOR_COUNT} factors"
        )
    effects: dict[tuple[str, str], dict[str, float]] = {}
    for row in effect_rows:
        key = (row["response_key"], row["factor_key"])
        effects[key] = {
            "rank_for_response": float(row["rank_for_response"]),
            "effect_high_minus_low": _finite(
                row["effect_high_minus_low"],
                f"{V1_EFFECTS}: {key} effect",
            ),
        }
    expected = len(RESPONSES) * FACTOR_COUNT
    if len(effects) != expected:
        raise GlobalSensitivityV2Error(
            f"{V1_EFFECTS}: expected {expected} effect rows, got {len(effects)}"
        )
    return {
        "summary": summary,
        "overall": overall,
        "effects": effects,
        "summary_path": V1_SUMMARY,
        "overall_path": V1_OVERALL,
        "effects_path": V1_EFFECTS,
    }


def build_sources() -> tuple[
    dict[str, dict[str, Any]],
    dict[str, Any],
    dict[str, dict[str, Any]],
]:
    """Load v4 chain sources and build the eight documented combinations."""

    process_sources: dict[str, dict[str, Any]] = {}
    for spec in PROCESS_SOURCE_SPECS:
        if spec.key == "trade_e2c":
            continue
        process_sources[spec.key] = _load_chain_source(spec)
    local_e2c = process_sources["local_e2c"]
    process_sources["trade_e2c"] = _load_trade_e2c_source(local_e2c)

    components = _load_product_chain_components()
    _require_close(
        float(local_e2c["harvest_gross_pj"]),
        components["e2c_total_pj"],
        "chain v7 harvest gross equals product chain v5 total",
    )
    _require_close(
        components["e2c_stem_pj"] + components["e2c_bf_pj"],
        components["e2c_total_pj"],
        "E2c stem + branch/foliage equals E2c total",
    )
    donor_stem_pj = float(process_sources["local_donor"]["harvest_gross_pj"])
    _require_close(
        donor_stem_pj,
        float(process_sources["trade_donor"]["harvest_gross_pj"]),
        "donor harvest gross is assignment-invariant",
    )
    _require_close(
        donor_stem_pj,
        components["donor_stem_pj"],
        "donor harvest gross equals product chain v1",
    )

    harvest_sources = {
        "donor_stem": {
            "harvest_gross_pj": donor_stem_pj,
            "bf_increment_pj": 0.0,
            "residue_basis": "stem_only",
            "harvest_gross_basis": "stem_only",
            "residue_basis_complete": True,
            "description": "donor stem-only harvest residue",
            "derived": False,
            "derivation": "",
        },
        "donor_total": {
            "harvest_gross_pj": donor_stem_pj + components["measured_bf_pj"],
            "bf_increment_pj": components["measured_bf_pj"],
            "residue_basis": "stem_plus_branch_foliage_partial_donor",
            "harvest_gross_basis": "stem_plus_measured_branch_foliage",
            "residue_basis_complete": False,
            "description": (
                "donor stem residue plus the measured four-province "
                "branch/foliage increment"
            ),
            "derived": True,
            "derivation": (
                "donor stem residue plus measured branch/foliage only where a "
                "measured coefficient exists; donor provinces have no donor "
                "branch/foliage coefficient and are held at stem-only"
            ),
        },
        "e2c_stem": {
            "harvest_gross_pj": components["e2c_stem_pj"],
            "bf_increment_pj": 0.0,
            "residue_basis": "stem_only",
            "harvest_gross_basis": "stem_only",
            "residue_basis_complete": True,
            "description": "E1 measured + E2c stem-only harvest residue",
            "derived": True,
            "derivation": (
                "product chain v5 harvest_residue_stem_gross_odt sum at the "
                "documented LHV"
            ),
        },
        "e2c_total": {
            "harvest_gross_pj": components["e2c_total_pj"],
            "bf_increment_pj": components["e2c_bf_pj"],
            "residue_basis": "stem_plus_branch_foliage",
            "harvest_gross_basis": "stem_plus_branch_foliage",
            "residue_basis_complete": True,
            "description": (
                "E1 measured + E2c stem plus branch/foliage harvest residue"
            ),
            "derived": False,
            "derivation": "",
        },
    }

    sources: dict[str, dict[str, Any]] = {}
    for assignment, assignment_short in (
        ("local_v6", "local"),
        ("trade_v5", "trade"),
    ):
        for coefficients, coefficient_short in (
            ("donor_v1", "donor"),
            ("e2c", "e2c"),
        ):
            process = process_sources[f"{assignment_short}_{coefficient_short}"]
            for residue, residue_short in (
                ("stem", "stem"),
                ("total", "total"),
            ):
                harvest = harvest_sources[f"{coefficient_short}_{residue_short}"]
                key = f"{assignment_short}_{coefficient_short}_{residue_short}"
                sources[key] = {
                    "key": key,
                    "chain_version": process["chain_version"],
                    "supply_version": process["supply_version"],
                    "assignment": assignment,
                    "coefficients": coefficients,
                    "residue_basis": harvest["residue_basis"],
                    "description": (
                        f"{process['description']}; {harvest['description']}"
                    ),
                    "chain_path": process["chain_path"],
                    "supply_summary_path": process["supply_summary_path"],
                    "supply_province_path": process["supply_province_path"],
                    "harvest_m3": process["harvest_m3"],
                    "lhv_gj_per_odt": process["lhv_gj_per_odt"],
                    "process_base_odt": process["process_base_odt"],
                    "process_base_pj": process["process_base_pj"],
                    "harvest_gross_pj": harvest["harvest_gross_pj"],
                    "bc_process_base_odt": process["bc_process_base_odt"],
                    "bc_process_base_pj": process["bc_process_base_pj"],
                    "bf_increment_pj": harvest["bf_increment_pj"],
                    "harvest_gross_basis": harvest["harvest_gross_basis"],
                    "residue_basis_complete": harvest[
                        "residue_basis_complete"
                    ],
                    "total_full_process_plus_harvest_pj": (
                        float(process["process_base_pj"])
                        + float(harvest["harvest_gross_pj"])
                    ),
                    "derived": bool(process["derived"] or harvest["derived"]),
                    "derivation": "; ".join(
                        part
                        for part in (
                            process.get("derivation", ""),
                            harvest.get("derivation", ""),
                        )
                        if part
                    ),
                }
    expected_keys = {
        f"{assignment}_{coefficient}_{residue}"
        for assignment in ("local", "trade")
        for coefficient in ("donor", "e2c")
        for residue in ("stem", "total")
    }
    if set(sources) != expected_keys:
        raise GlobalSensitivityV2Error(
            f"source combinations are incomplete: {sorted(set(sources) ^ expected_keys)}"
        )
    return sources, components, process_sources


def select_source(
    sources: Mapping[str, Mapping[str, Any]],
    config: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Return the source row for one factor configuration."""

    assignment = str(config["capacity_assignment"])
    coefficients = str(config["product_coefficients"])
    residue = str(config["residue_basis"])
    if assignment not in {"local_v6", "trade_v5"}:
        raise GlobalSensitivityV2Error(
            f"unexpected assignment level {assignment!r}"
        )
    if coefficients not in {"donor_v1", "e2c"}:
        raise GlobalSensitivityV2Error(
            f"unexpected coefficient level {coefficients!r}"
        )
    if residue not in {"stem_only", "stem_plus_branch_foliage"}:
        raise GlobalSensitivityV2Error(
            f"unexpected residue basis {residue!r}"
        )
    assignment_key = "local" if assignment == "local_v6" else "trade"
    coefficient_key = "donor" if coefficients == "donor_v1" else "e2c"
    residue_key = "stem" if residue == "stem_only" else "total"
    source_key = f"{assignment_key}_{coefficient_key}_{residue_key}"
    source = sources.get(source_key)
    if source is None:
        raise GlobalSensitivityV2Error(
            f"no source for {source_key!r}; documented combinations are "
            f"{sorted(sources)}"
        )
    return source


def _design_config(row: Mapping[str, Any]) -> dict[str, Any]:
    return {factor.key: row[factor.key] for factor in FACTORS}


def _response_row(
    run_id: str,
    config: Mapping[str, Any],
    sources: Mapping[str, Mapping[str, Any]],
    margin_values: Mapping[str, float],
    zone_weights: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    source = select_source(sources, config)
    recovery = _finite_nonnegative(
        config["harvest_recovery"], f"{run_id}: harvest recovery"
    )
    availability = _finite_nonnegative(
        config["process_availability"], f"{run_id}: process availability"
    )
    if recovery > 1.0 or availability > 1.0:
        raise GlobalSensitivityV2Error(
            f"{run_id}: recovery and availability must be fractions"
        )
    price = str(config["price_scenario"])
    if price not in margin_values:
        raise GlobalSensitivityV2Error(
            f"{run_id}: unexpected price scenario {price!r}"
        )
    zone = str(config["zone_weights"])
    zone_row = zone_weights.get(zone)
    if not isinstance(zone_row, Mapping):
        raise GlobalSensitivityV2Error(
            f"{run_id}: unexpected zone-weight case {zone!r}"
        )
    b2_weight = _finite_nonnegative(
        zone_row.get("BC_B2"), f"{run_id}: BC B2 weight"
    )

    process_base_pj = float(source["process_base_pj"])
    harvest_gross_pj = float(source["harvest_gross_pj"])
    bc_process_base_pj = float(source["bc_process_base_pj"])
    process_pj = process_base_pj * availability
    harvest_recoverable_pj = harvest_gross_pj * recovery
    total_gross_pj = process_pj + harvest_gross_pj
    bc_process_pj = bc_process_base_pj * availability
    bc_b2_process_pj = bc_process_pj * b2_weight
    total_recoverable_pj = process_pj + harvest_recoverable_pj
    margin_m_cad = float(margin_values[price])

    values = (
        process_base_pj,
        harvest_gross_pj,
        bc_process_base_pj,
        process_pj,
        harvest_recoverable_pj,
        total_gross_pj,
        bc_process_pj,
        bc_b2_process_pj,
        total_recoverable_pj,
        margin_m_cad,
    )
    if not all(math.isfinite(value) for value in values):
        raise GlobalSensitivityV2Error(f"{run_id}: non-finite response")
    if any(value < 0.0 for value in values[:-1]):
        raise GlobalSensitivityV2Error(f"{run_id}: negative physical response")

    row: dict[str, Any] = {
        "run_id": run_id,
        "source_key": source["key"],
        "source_description": source["description"],
        "source_derived": _bool_text(bool(source["derived"])),
        "capacity_assignment": config["capacity_assignment"],
        "product_coefficients": config["product_coefficients"],
        "residue_basis": config["residue_basis"],
        "source_residue_basis": source["residue_basis"],
        "harvest_recovery": recovery,
        "process_availability": availability,
        "price_scenario": price,
        "zone_weights": zone,
        "process_base_pj": process_base_pj,
        "harvest_gross_pj": harvest_gross_pj,
        "bc_process_base_pj": bc_process_base_pj,
        "bf_increment_pj": float(source.get("bf_increment_pj", 0.0)),
        "b2_weight": b2_weight,
        "national_process_residue_pj": process_pj,
        "harvest_recoverable_pj": harvest_recoverable_pj,
        "total_gross_pj": total_gross_pj,
        "national_margin_m_cad": margin_m_cad,
        "bc_process_pj": bc_process_pj,
        "bc_b2_process_pj": bc_b2_process_pj,
        "national_total_recoverable_pj": total_recoverable_pj,
        "residue_basis_complete": _bool_text(
            bool(source.get("residue_basis_complete", True))
        ),
        "harvest_gross_basis": source["harvest_gross_basis"],
        "derivation": source.get("derivation", ""),
        "response_basis": (
            "process=facility_process_base_x_availability; "
            "gross=residue_basis_gross; "
            "recoverable=gross_x_recovery; "
            "total_gross=process_available_plus_harvest_gross; "
            "margin=119m_national_economics_v2_3_price_scenario; "
            "bc_process=bc_facility_process_base_x_availability; "
            "bc_b2=bc_process_x_zone_weight"
        ),
        "evidence_label": EVIDENCE_LABEL,
        "observed": _bool_text(OBSERVED),
        "policy_grade": _bool_text(POLICY_GRADE),
        "national_claim": _bool_text(NATIONAL_CLAIM),
        "screening_only": _bool_text(SCREENING_ONLY),
        "official_osecan_changed": _bool_text(OFFICIAL_OSECAN_CHANGED),
        "not_policy": _bool_text(NOT_POLICY),
    }
    return row


def _hadamard_16() -> list[list[int]]:
    matrix = [[1]]
    while len(matrix) < DESIGN_RUNS:
        expanded: list[list[int]] = []
        for row in matrix:
            expanded.append(row + row)
            expanded.append(row + [-value for value in row])
        matrix = expanded
    if len(matrix) != DESIGN_RUNS:
        raise GlobalSensitivityV2Error("Hadamard matrix is not 16 x 16")
    return matrix


def build_design() -> list[dict[str, Any]]:
    matrix = _hadamard_16()
    rows: list[dict[str, Any]] = []
    for index in range(DESIGN_RUNS):
        codes = [matrix[index][column] for column in PB_COLUMNS]
        row: dict[str, Any] = {
            "run_id": f"PB{index + 1:02d}",
            "design_type": DESIGN_TYPE,
        }
        for factor, code in zip(FACTORS, codes):
            if code not in (-1, 1):
                raise GlobalSensitivityV2Error(
                    f"{row['run_id']}: non-PB code {code!r}"
                )
            row[f"{factor.key}_code"] = code
            row[factor.key] = factor.low if code < 0 else factor.high
        rows.append(row)
    return rows


def _design_matrix(rows: Sequence[Mapping[str, Any]]) -> list[list[int]]:
    return [
        [int(row[f"{factor.key}_code"]) for factor in FACTORS] for row in rows
    ]


def _column(matrix: Sequence[Sequence[int]], index: int) -> tuple[int, ...]:
    return tuple(int(row[index]) for row in matrix)


def _product(left: Sequence[int], right: Sequence[int]) -> tuple[int, ...]:
    return tuple(int(a) * int(b) for a, b in zip(left, right))


def _find_hadamard_column(
    vector: Sequence[int],
    columns: Sequence[tuple[int, ...]],
) -> tuple[int, int]:
    for position, column in enumerate(columns, start=1):
        if tuple(vector) == column:
            return position, 1
        if tuple(vector) == tuple(-value for value in column):
            return position, -1
    raise GlobalSensitivityV2Error(
        "two-factor product does not map to a Hadamard column"
    )


def design_checks(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    checks: list[dict[str, Any]] = []

    def add(name: str, passed: bool, detail: str) -> None:
        checks.append(
            {
                "name": name,
                "status": "PASS" if passed else "FAIL",
                "detail": detail,
            }
        )

    add(
        "design_run_count",
        len(rows) == DESIGN_RUNS,
        f"runs={len(rows)}",
    )
    matrix = _design_matrix(rows)
    balanced = all(
        sum(1 for row in matrix if row[index] > 0) == DESIGN_RUNS // 2
        and sum(1 for row in matrix if row[index] < 0) == DESIGN_RUNS // 2
        for index in range(FACTOR_COUNT)
    )
    add(
        "design_balance",
        balanced,
        "every factor has eight low and eight high runs",
    )
    pairwise = []
    for left, right in itertools.combinations(range(FACTOR_COUNT), 2):
        dot = sum(row[left] * row[right] for row in matrix)
        pairwise.append(dot)
    add(
        "design_orthogonality",
        all(dot == 0 for dot in pairwise),
        "all factor-column pairwise dot products are zero",
    )

    hadamard = _hadamard_16()
    columns = [
        tuple(hadamard[row][column] for row in range(DESIGN_RUNS))
        for column in range(1, len(hadamard[0]))
    ]
    factor_columns = {
        FACTORS[index].key: _column(matrix, index)
        for index in range(FACTOR_COUNT)
    }
    main_aliases: list[str] = []
    alias_groups: dict[tuple[int, int], list[tuple[str, str]]] = defaultdict(
        list
    )
    pair_count = 0
    for left_factor, right_factor in itertools.combinations(FACTORS, 2):
        pair_count += 1
        product = _product(
            factor_columns[left_factor.key], factor_columns[right_factor.key]
        )
        position, sign = _find_hadamard_column(product, columns)
        if position in PB_COLUMNS:
            main_aliases.append(
                f"{left_factor.short_label}*{right_factor.short_label} -> "
                f"{FACTORS[PB_COLUMNS.index(position)].short_label}"
            )
        alias_groups[(position, sign)].append(
            (left_factor.short_label, right_factor.short_label)
        )
    add(
        "no_two_factor_main_effect_alias",
        not main_aliases,
        (
            "no two-factor interaction among the seven factors equals a main effect"
            if not main_aliases
            else "; ".join(main_aliases)
        ),
    )
    alias_rows: list[dict[str, Any]] = []
    covered = sum(len(members) for members in alias_groups.values())
    add(
        "two_factor_alias_coverage",
        covered == pair_count == (FACTOR_COUNT * (FACTOR_COUNT - 1)) // 2,
        f"paired={pair_count}, covered={covered}",
    )
    for group_index, ((position, sign), members) in enumerate(
        sorted(alias_groups.items()), start=1
    ):
        aliased_with = (
            f"dummy_column_{position}"
            if position not in PB_COLUMNS
            else f"main_effect_{FACTORS[PB_COLUMNS.index(position)].key}"
        )
        label = " = ".join(f"{left}*{right}" for left, right in members)
        if sign < 0:
            label = f"-({label})"
        alias_rows.append(
            {
                "alias_group": f"A{group_index}",
                "members": label,
                "aliased_with": aliased_with,
                "hadamard_column": position,
                "sign": sign,
                "note": (
                    "Two-factor interactions are not individually estimable "
                    "without a foldover; main effects are clear."
                ),
            }
        )
    add(
        "main_effect_count",
        len(FACTORS) == FACTOR_COUNT,
        f"factors={len(FACTORS)}",
    )
    add(
        "design_resolution",
        not main_aliases,
        (
            "resolution IV for the seven factors: main effects are clear of "
            "all two-factor interactions; 2FIs alias in triplets"
        ),
    )
    return checks, alias_rows


def _main_effects(
    design: Sequence[Mapping[str, Any]],
    responses: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    response_by_run = {row["run_id"]: row for row in responses}
    if len(response_by_run) != len(responses):
        raise GlobalSensitivityV2Error("duplicate response run_id")
    rows: list[dict[str, Any]] = []
    for response in RESPONSES:
        values = [
            float(response_by_run[row["run_id"]][response.key])
            for row in design
        ]
        grand_mean = _mean(values)
        effects: list[dict[str, Any]] = []
        for factor_index, factor in enumerate(FACTORS):
            low_values = [
                float(response_by_run[row["run_id"]][response.key])
                for row in design
                if int(row[f"{factor.key}_code"]) < 0
            ]
            high_values = [
                float(response_by_run[row["run_id"]][response.key])
                for row in design
                if int(row[f"{factor.key}_code"]) > 0
            ]
            low_mean = _mean(low_values)
            high_mean = _mean(high_values)
            effect = high_mean - low_mean
            effects.append(
                {
                    "response_key": response.key,
                    "response_label": response.label,
                    "unit": response.unit,
                    "response_role": response.role,
                    "factor_key": factor.key,
                    "factor_label": factor.label,
                    "factor_short_label": factor.short_label,
                    "factor_index": factor_index,
                    "low_level": factor.low,
                    "high_level": factor.high,
                    "mean_low": low_mean,
                    "mean_high": high_mean,
                    "effect_high_minus_low": effect,
                    "abs_effect": abs(effect),
                    "effect_pct_of_grand_mean": (
                        effect / grand_mean * 100.0
                        if abs(grand_mean) > 1e-12
                        else None
                    ),
                    "grand_mean": grand_mean,
                }
            )
        max_abs = max(item["abs_effect"] for item in effects)
        for item in effects:
            item["normalized_abs_effect"] = (
                item["abs_effect"] / max_abs if max_abs > 0.0 else 0.0
            )
            item["material_effect"] = (
                item["normalized_abs_effect"] >= MATERIAL_EFFECT_THRESHOLD
            )
        ranked = sorted(
            effects,
            key=lambda item: (-item["abs_effect"], item["factor_index"]),
        )
        for rank, item in enumerate(ranked, start=1):
            item["rank_for_response"] = rank
            item["tie_note"] = (
                "zero or near-zero effect; rank order is a tie-breaker"
                if item["abs_effect"] <= 1e-9
                else ""
            )
        rows.extend(ranked)

    overall: list[dict[str, Any]] = []
    for factor_index, factor in enumerate(FACTORS):
        factor_rows = [
            row
            for row in rows
            if row["factor_key"] == factor.key
            and row["response_key"] in REQUIRED_RESPONSE_KEYS
        ]
        score = _mean(
            [float(row["normalized_abs_effect"]) for row in factor_rows]
        )
        nonzero = [
            row["response_key"]
            for row in factor_rows
            if row["abs_effect"] > 1e-9
        ]
        material = [
            row["response_key"]
            for row in factor_rows
            if bool(row["material_effect"])
        ]
        material_rows = [
            row for row in factor_rows if bool(row["material_effect"])
        ]
        if material_rows:
            top_response = max(
                material_rows, key=lambda row: row["abs_effect"]
            )["response_label"]
        else:
            top_response = "none"
        overall.append(
            {
                "factor_key": factor.key,
                "factor_label": factor.label,
                "factor_short_label": factor.short_label,
                "factor_index": factor_index,
                "mean_normalized_abs_effect": score,
                "max_normalized_abs_effect": max(
                    float(row["normalized_abs_effect"])
                    for row in factor_rows
                ),
                "responses_with_nonzero_effect": len(nonzero),
                "responses_with_material_effect": len(material),
                "nonzero_responses": ";".join(nonzero) if nonzero else "",
                "affected_responses": ";".join(material) if material else "",
                "top_response": top_response,
            }
        )
    overall.sort(
        key=lambda item: (
            -item["mean_normalized_abs_effect"],
            item["factor_index"],
        )
    )
    for rank, item in enumerate(overall, start=1):
        item["rank"] = rank
    return rows, overall


def build_level_sweep(
    sources: Mapping[str, Mapping[str, Any]],
    margin_values: Mapping[str, float],
    zone_weights: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    counter = 0
    for factor in FACTORS:
        for level in factor.documented_levels:
            counter += 1
            config = dict(BASELINE)
            config[factor.key] = level
            row = _response_row(
                f"SW{counter:02d}",
                config,
                sources,
                margin_values,
                zone_weights,
            )
            row["sweep_id"] = f"SW{counter:02d}"
            row["varied_factor"] = factor.key
            row["varied_factor_label"] = factor.label
            row["varied_level"] = level
            row["is_baseline_level"] = _bool_text(
                level == BASELINE[factor.key]
            )
            row["sweep_role"] = (
                "descriptive_one_at_a_time; not part of the PB array"
            )
            rows.append(row)
    return rows


def build_v1_comparison(
    overall: Sequence[Mapping[str, Any]],
    effects: Sequence[Mapping[str, Any]],
    v1: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    v1_overall = v1["overall"]
    if not isinstance(v1_overall, Mapping):
        raise GlobalSensitivityV2Error("v1 overall ranking is not a mapping")
    v2_by_factor = {row["factor_key"]: row for row in overall}
    comparison: list[dict[str, Any]] = []
    for factor in FACTORS:
        v1_row = v1_overall.get(factor.key)
        v2_row = v2_by_factor.get(factor.key)
        if not isinstance(v1_row, Mapping) or v2_row is None:
            raise GlobalSensitivityV2Error(
                f"v1/v2 ranking is missing factor {factor.key}"
            )
        v1_rank = int(v1_row["rank"])
        v2_rank = int(v2_row["rank"])
        v1_score = float(v1_row["mean_normalized_abs_effect"])
        v2_score = float(v2_row["mean_normalized_abs_effect"])
        note = ""
        if factor.key == "product_coefficients":
            note = (
                "level definition changed: v1 measured_v4 was the E1 "
                "four-province chain v4; v2 e2c is E1 measured + E2c for 13 "
                "provinces"
            )
        elif factor.key == "zone_weights":
            note = "same OSeCAN BC weights in handoff v6 and v8"
        comparison.append(
            {
                "factor_key": factor.key,
                "factor_label": factor.label,
                "v1_rank": v1_rank,
                "v2_rank": v2_rank,
                "rank_delta_v2_minus_v1": v2_rank - v1_rank,
                "v1_mean_normalized_abs_effect": v1_score,
                "v2_mean_normalized_abs_effect": v2_score,
                "score_delta_v2_minus_v1": v2_score - v1_score,
                "comparison_note": note,
            }
        )
    comparison.sort(key=lambda row: row["v2_rank"])
    v1_effects = v1["effects"]
    if not isinstance(v1_effects, Mapping):
        raise GlobalSensitivityV2Error("v1 main effects are not a mapping")
    response_comparison: list[dict[str, Any]] = []
    for response in RESPONSES:
        for factor in FACTORS:
            key = (response.key, factor.key)
            v1_row = v1_effects.get(key)
            v2_row = next(
                row
                for row in effects
                if row["response_key"] == response.key
                and row["factor_key"] == factor.key
            )
            if not isinstance(v1_row, Mapping):
                raise GlobalSensitivityV2Error(
                    f"v1 effects are missing {key}"
                )
            response_comparison.append(
                {
                    "response_key": response.key,
                    "response_label": response.label,
                    "factor_key": factor.key,
                    "factor_label": factor.label,
                    "v1_rank": int(v1_row["rank_for_response"]),
                    "v2_rank": int(v2_row["rank_for_response"]),
                    "rank_delta_v2_minus_v1": (
                        int(v2_row["rank_for_response"])
                        - int(v1_row["rank_for_response"])
                    ),
                    "v1_effect_high_minus_low": float(
                        v1_row["effect_high_minus_low"]
                    ),
                    "v2_effect_high_minus_low": float(
                        v2_row["effect_high_minus_low"]
                    ),
                    "effect_delta_v2_minus_v1": (
                        float(v2_row["effect_high_minus_low"])
                        - float(v1_row["effect_high_minus_low"])
                    ),
                }
            )
    return comparison, response_comparison


def _source_checks(
    sources: Mapping[str, Mapping[str, Any]],
    components: Mapping[str, Any],
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []

    def add(name: str, passed: bool, detail: str) -> None:
        checks.append(
            {
                "name": name,
                "status": "PASS" if passed else "FAIL",
                "detail": detail,
            }
        )

    expected = 8
    add(
        "source_combination_count",
        len(sources) == expected,
        f"sources={len(sources)}",
    )
    e2c_stem = sources["local_e2c_stem"]
    e2c_total = sources["local_e2c_total"]
    _require_close(
        float(e2c_stem["process_base_pj"]),
        float(e2c_total["process_base_pj"]),
        "E2c process residue is residue-basis invariant",
    )
    _require_close(
        float(e2c_stem["harvest_gross_pj"])
        + float(e2c_total["bf_increment_pj"]),
        float(e2c_total["harvest_gross_pj"]),
        "E2c stem + branch/foliage equals total",
    )
    add(
        "e2c_residue_basis_arithmetic",
        True,
        (
            f"stem={components['e2c_stem_pj']:.6f} PJ, "
            f"branch/foliage={components['e2c_bf_pj']:.6f} PJ, "
            f"total={components['e2c_total_pj']:.6f} PJ"
        ),
    )
    donor_stem = sources["local_donor_stem"]
    donor_total = sources["local_donor_total"]
    _require_close(
        float(donor_total["harvest_gross_pj"]),
        float(donor_stem["harvest_gross_pj"])
        + float(donor_total["bf_increment_pj"]),
        "donor stem + measured branch/foliage equals donor total",
    )
    add(
        "donor_total_hybrid",
        donor_total["residue_basis_complete"] is False,
        (
            "donor-total adds the measured four-province branch/foliage "
            "increment and is flagged incomplete"
        ),
    )
    local_e2c_total = sources["local_e2c_total"]
    trade_e2c_total = sources["trade_e2c_total"]
    _require_close(
        float(local_e2c_total["process_base_pj"]),
        float(trade_e2c_total["process_base_pj"]),
        "trade E2c national process residue matches local",
    )
    _require_close(
        float(local_e2c_total["harvest_gross_pj"]),
        float(trade_e2c_total["harvest_gross_pj"]),
        "trade E2c national harvest residue matches local",
    )
    add(
        "trade_e2c_national_invariance",
        True,
        "trade E2c national process and harvest totals equal local v4",
    )
    add(
        "trade_e2c_bc_reallocation",
        float(trade_e2c_total["bc_process_base_pj"])
        > float(local_e2c_total["bc_process_base_pj"]),
        (
            f"BC process {float(local_e2c_total['bc_process_base_pj']):.6f} "
            f"-> {float(trade_e2c_total['bc_process_base_pj']):.6f} PJ"
        ),
    )
    return checks


def _response_checks(
    responses: Sequence[Mapping[str, Any]],
    margin_values: Mapping[str, float],
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []

    def add(name: str, passed: bool, detail: str) -> None:
        checks.append(
            {
                "name": name,
                "status": "PASS" if passed else "FAIL",
                "detail": detail,
            }
        )

    arithmetic_ok = True
    margin_ok = True
    physical_ok = True
    basis_ok = True
    for row in responses:
        run_id = row["run_id"]
        if not math.isclose(
            float(row["national_process_residue_pj"]),
            float(row["process_base_pj"])
            * float(row["process_availability"]),
            rel_tol=REL_TOL,
            abs_tol=ABS_TOL,
        ):
            arithmetic_ok = False
        if not math.isclose(
            float(row["harvest_recoverable_pj"]),
            float(row["harvest_gross_pj"]) * float(row["harvest_recovery"]),
            rel_tol=REL_TOL,
            abs_tol=ABS_TOL,
        ):
            arithmetic_ok = False
        if not math.isclose(
            float(row["total_gross_pj"]),
            float(row["national_process_residue_pj"])
            + float(row["harvest_gross_pj"]),
            rel_tol=REL_TOL,
            abs_tol=ABS_TOL,
        ):
            arithmetic_ok = False
        if not math.isclose(
            float(row["bc_process_pj"]),
            float(row["bc_process_base_pj"])
            * float(row["process_availability"]),
            rel_tol=REL_TOL,
            abs_tol=ABS_TOL,
        ):
            arithmetic_ok = False
        if not math.isclose(
            float(row["bc_b2_process_pj"]),
            float(row["bc_process_pj"]) * float(row["b2_weight"]),
            rel_tol=REL_TOL,
            abs_tol=ABS_TOL,
        ):
            arithmetic_ok = False
        if not math.isclose(
            float(row["national_total_recoverable_pj"]),
            float(row["national_process_residue_pj"])
            + float(row["harvest_recoverable_pj"]),
            rel_tol=REL_TOL,
            abs_tol=ABS_TOL,
        ):
            arithmetic_ok = False
        expected_margin = float(margin_values[str(row["price_scenario"])])
        if not math.isclose(
            float(row["national_margin_m_cad"]),
            expected_margin,
            rel_tol=1e-12,
            abs_tol=1e-9,
        ):
            margin_ok = False
        for key in (
            "national_process_residue_pj",
            "harvest_gross_pj",
            "harvest_recoverable_pj",
            "total_gross_pj",
            "bc_process_pj",
            "bc_b2_process_pj",
            "national_total_recoverable_pj",
            "process_base_pj",
            "bc_process_base_pj",
            "b2_weight",
        ):
            value = float(row[key])
            if not math.isfinite(value) or value < 0.0:
                physical_ok = False
        if not math.isfinite(float(row["national_margin_m_cad"])):
            physical_ok = False
        recovery = float(row["harvest_recovery"])
        availability = float(row["process_availability"])
        b2_weight = float(row["b2_weight"])
        if not (0.0 <= recovery <= 1.0):
            basis_ok = False
        if not (0.0 <= availability <= 1.0):
            basis_ok = False
        if not (0.0 <= b2_weight <= 1.0):
            basis_ok = False
        source_residue = str(row["source_residue_basis"])
        if source_residue == "stem_only":
            if abs(float(row["bf_increment_pj"])) > ABS_TOL:
                basis_ok = False
        elif source_residue == "stem_plus_branch_foliage" or source_residue.startswith(
            "stem_plus_branch_foliage_partial_donor"
        ):
            if float(row["bf_increment_pj"]) <= 0.0:
                basis_ok = False
        else:
            basis_ok = False
        if source_residue == "stem_only" and row["residue_basis_complete"] != "true":
            basis_ok = False
        if (
            source_residue.startswith("stem_plus_branch_foliage_partial_donor")
            and row["residue_basis_complete"] != "false"
        ):
            basis_ok = False
        if not run_id:
            arithmetic_ok = False

    add(
        "response_arithmetic",
        arithmetic_ok,
        (
            "process = base x availability; recoverable = gross x recovery; "
            "total = process + gross; BC and B2 arithmetic match"
        ),
    )
    add(
        "margin_matches_source",
        margin_ok,
        "margin equals the national_margin_totals v2.3 scenario value",
    )
    add(
        "physical_nonnegative",
        physical_ok,
        (
            "all physical responses are finite and non-negative; margin is "
            "finite and may be negative by documented scenario"
        ),
    )
    add(
        "residue_basis_flags",
        basis_ok,
        (
            "stem rows have zero BF increment; total rows have positive BF; "
            "donor-total rows are flagged partial"
        ),
    )
    return checks


def _ranking_checks(
    effects: Sequence[Mapping[str, Any]],
    overall: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []

    def add(name: str, passed: bool, detail: str) -> None:
        checks.append(
            {
                "name": name,
                "status": "PASS" if passed else "FAIL",
                "detail": detail,
            }
        )

    per_response_ok = True
    for response in RESPONSES:
        ranks = sorted(
            int(row["rank_for_response"])
            for row in effects
            if row["response_key"] == response.key
        )
        if ranks != list(range(1, FACTOR_COUNT + 1)):
            per_response_ok = False
        for row in effects:
            if row["response_key"] != response.key:
                continue
            value = float(row["effect_high_minus_low"])
            if not math.isfinite(value):
                per_response_ok = False
            normalized = float(row["normalized_abs_effect"])
            if not (0.0 <= normalized <= 1.0):
                per_response_ok = False
    add(
        "response_ranking_permutation",
        per_response_ok,
        (
            "each response has ranks 1..7 exactly once; effects and "
            "normalized effects are finite"
        ),
    )
    overall_ranks = sorted(int(row["rank"]) for row in overall)
    add(
        "overall_ranking_permutation",
        overall_ranks == list(range(1, FACTOR_COUNT + 1)),
        f"ranks={overall_ranks}",
    )
    add(
        "overall_score_bounds",
        all(
            0.0 <= float(row["mean_normalized_abs_effect"]) <= 1.0
            for row in overall
        ),
        "mean normalized effects are in [0,1]",
    )
    return checks


def _label_checks(
    responses: Sequence[Mapping[str, Any]],
    sweep: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []

    def add(name: str, passed: bool, detail: str) -> None:
        checks.append(
            {
                "name": name,
                "status": "PASS" if passed else "FAIL",
                "detail": detail,
            }
        )

    def labels_ok(row: Mapping[str, Any]) -> bool:
        return (
            row.get("evidence_label") == EVIDENCE_LABEL
            and row.get("observed") == _bool_text(OBSERVED)
            and row.get("policy_grade") == _bool_text(POLICY_GRADE)
            and row.get("national_claim") == _bool_text(NATIONAL_CLAIM)
            and row.get("screening_only") == _bool_text(SCREENING_ONLY)
            and row.get("official_osecan_changed")
            == _bool_text(OFFICIAL_OSECAN_CHANGED)
            and row.get("not_policy") == _bool_text(NOT_POLICY)
        )

    add(
        "response_labels",
        all(labels_ok(row) for row in responses),
        "all PB response rows carry screening labels",
    )
    add(
        "sweep_labels",
        all(labels_ok(row) for row in sweep),
        "all level-sweep rows carry screening labels",
    )
    return checks


def _comparison_checks(
    comparison: Sequence[Mapping[str, Any]],
    response_comparison: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []

    def add(name: str, passed: bool, detail: str) -> None:
        checks.append(
            {
                "name": name,
                "status": "PASS" if passed else "FAIL",
                "detail": detail,
            }
        )

    add(
        "v1_v2_overall_coverage",
        len(comparison) == FACTOR_COUNT
        and sorted(int(row["v2_rank"]) for row in comparison)
        == list(range(1, FACTOR_COUNT + 1))
        and sorted(int(row["v1_rank"]) for row in comparison)
        == list(range(1, FACTOR_COUNT + 1)),
        "v1 and v2 overall ranks are complete permutations",
    )
    expected = len(RESPONSES) * FACTOR_COUNT
    add(
        "v1_v2_response_coverage",
        len(response_comparison) == expected,
        f"response-factor rows={len(response_comparison)}",
    )
    return checks


def _validation_checks(
    design_checks_list: Sequence[Mapping[str, Any]],
    source_checks_list: Sequence[Mapping[str, Any]],
    response_checks_list: Sequence[Mapping[str, Any]],
    ranking_checks_list: Sequence[Mapping[str, Any]],
    label_checks_list: Sequence[Mapping[str, Any]],
    comparison_checks_list: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    checks = [
        *design_checks_list,
        *source_checks_list,
        *response_checks_list,
        *ranking_checks_list,
        *label_checks_list,
        *comparison_checks_list,
    ]
    failed = [check for check in checks if check["status"] != "PASS"]
    return {
        "passed": not failed,
        "checks": checks,
        "failed": failed,
    }


def _input_paths(
    sources: Mapping[str, Mapping[str, Any]],
    components: Mapping[str, Any],
    margin: Mapping[str, Any],
    zones: Mapping[str, Any],
    v1: Mapping[str, Any],
) -> list[Path]:
    paths: list[Path] = []
    for source in sources.values():
        paths.extend(
            [
                Path(source["chain_path"]),
                Path(source["supply_summary_path"]),
                Path(source["supply_province_path"]),
            ]
        )
    paths.extend(
        [
            Path(components["v1_path"]),
            Path(components["v2_path"]),
            Path(components["v3_path"]),
            Path(components["v3_summary_path"]),
            Path(components["v5_path"]),
            Path(components["v5_summary_path"]),
            Path(margin["path"]),
            Path(zones["path"]),
            Path(v1["summary_path"]),
            Path(v1["overall_path"]),
            Path(v1["effects_path"]),
        ]
    )
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if not path.is_file():
            raise GlobalSensitivityV2Error(f"input path is missing: {path}")
        unique.append(path)
    return sorted(unique)


def _write_csv(
    path: Path, rows: Sequence[Mapping[str, Any]], fields: Sequence[str]
) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields))
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    field: (
                        _number(row[field])
                        if isinstance(row[field], (int, float))
                        and not isinstance(row[field], bool)
                        else row[field]
                    )
                    for field in fields
                }
            )


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _md_table(
    headers: Sequence[str], rows: Sequence[Sequence[Any]]
) -> list[str]:
    lines = [
        "| " + " | ".join(str(header) for header in headers) + " |",
        "|" + "|".join("---" for _ in headers) + "|",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(value) for value in row) + " |")
    return lines


def _report(
    summary: Mapping[str, Any],
    design: Sequence[Mapping[str, Any]],
    responses: Sequence[Mapping[str, Any]],
    effects: Sequence[Mapping[str, Any]],
    overall: Sequence[Mapping[str, Any]],
    sweep: Sequence[Mapping[str, Any]],
    aliases: Sequence[Mapping[str, Any]],
    comparison: Sequence[Mapping[str, Any]],
) -> str:
    lines = [
        f"# {ARTIFACT_TITLE}",
        "",
        (
            "Global screening of the v4 E1 + E2c national facility chain at "
            "the 119 Mm3 provincial allocation. All values are model "
            "estimates: `model_estimate=true`, `observed=false`, "
            "`policy_grade=false`, `national_claim=false`. No policy, AAC, "
            "investment, or national-supply claim is made. No UI or official "
            "OSeCAN surface is changed."
        ),
        "",
        "## v4 design/responses",
        "",
        (
            "A 16-run Plackett-Burman/Hadamard array is used. The seven "
            "factor columns are a resolution-IV subset: every main effect is "
            "orthogonal to every other main effect and to every two-factor "
            "interaction among the seven factors. Two-factor interactions "
            "are aliased in triplets; they are not individually estimable "
            "without a foldover."
        ),
        "",
    ]
    lines += _md_table(
        ("Factor", "PB low", "PB high", "Documented levels", "Source"),
        [
            (
                factor.label,
                factor.low,
                factor.high,
                ", ".join(str(level) for level in factor.documented_levels),
                factor.source_note,
            )
            for factor in FACTORS
        ],
    )
    lines += [
        "",
        (
            "Factors 4 and 5 are multi-level in the source artifacts. The PB "
            "array screens their extreme levels (0.35 vs 0.75 recovery; 0 vs "
            "75% availability). The intermediate 0.60 recovery and 25/50% "
            "availability cases are reported in the descriptive level sweep, "
            "not as PB main-effect contrasts."
        ),
        "",
        "### Design matrix",
        "",
    ]
    lines += _md_table(
        (
            "Run",
            "Assignment",
            "Coefficients",
            "Residue",
            "Recovery",
            "Availability",
            "Price",
            "Zone",
        ),
        [
            (
                row["run_id"],
                row["capacity_assignment"],
                row["product_coefficients"],
                row["residue_basis"],
                _number(row["harvest_recovery"]),
                _number(row["process_availability"]),
                row["price_scenario"],
                row["zone_weights"],
            )
            for row in design
        ],
    )
    lines += [
        "",
        "### Aliasing",
        "",
    ]
    lines += _md_table(
        ("Alias group", "Members", "Aliased with"),
        [
            (row["alias_group"], row["members"], row["aliased_with"])
            for row in aliases
        ],
    )
    lines += [
        "",
        "## Main effects ranking",
        "",
        (
            "Main effect = mean response at the PB high level minus mean "
            "response at the PB low level. Normalized effect = |effect| / "
            "largest |effect| for that response. The overall score is the "
            "mean normalized effect across the six required responses; it is "
            "a screening rank, not a variance decomposition. Effects below "
            f"{MATERIAL_EFFECT_THRESHOLD:g} of the largest |effect| for a "
            "response are reported but treated as numerically negligible."
        ),
        "",
        "### Effect matrix",
        "",
    ]
    effect_lookup = {
        (row["response_key"], row["factor_key"]): row for row in effects
    }
    lines += _md_table(
        (
            "Factor",
            *[RESPONSE_BY_KEY[key].label for key in REQUIRED_RESPONSE_KEYS],
        ),
        [
            (
                factor.label,
                *[
                    (
                        f"{effect_lookup[(key, factor.key)]['effect_high_minus_low']:+,.3f}"
                    )
                    for key in REQUIRED_RESPONSE_KEYS
                ],
            )
            for factor in FACTORS
        ],
    )
    lines += [
        "",
        "### Per-response top factors",
        "",
    ]
    top_rows = []
    for key in REQUIRED_RESPONSE_KEYS:
        ranked = [row for row in effects if row["response_key"] == key]
        ranked = sorted(
            ranked, key=lambda row: row["rank_for_response"]
        )
        top_rows.append(
            (
                RESPONSE_BY_KEY[key].label,
                RESPONSE_BY_KEY[key].unit,
                "; ".join(
                    f"{row['factor_short_label']} "
                    f"({row['effect_high_minus_low']:+,.3f})"
                    for row in ranked[:3]
                ),
            )
        )
    lines += _md_table(
        ("Response", "Unit", "Top three factors (effect)"),
        top_rows,
    )
    lines += [
        "",
        "### Overall ranking",
        "",
    ]
    lines += _md_table(
        (
            "Rank",
            "Factor",
            "Mean normalized |effect|",
            "Material responses",
            "Largest response",
        ),
        [
            (
                row["rank"],
                row["factor_label"],
                f"{float(row['mean_normalized_abs_effect']):.4f}",
                row["responses_with_material_effect"],
                row["top_response"],
            )
            for row in overall
        ],
    )
    lines += [
        "",
        "## Ranking v1 vs v2",
        "",
        (
            "v1 was built on the pre-E2 chain: its `measured_v4` level was "
            "the E1 four-province measured chain v4, not E2 v1. This refresh "
            "replaces that level with E1 measured + E2c. Ranks below compare "
            "the committed v1 artifact with this v4 artifact."
        ),
        "",
    ]
    lines += _md_table(
        (
            "Factor",
            "v1 rank",
            "v2 rank",
            "Rank change",
            "v1 score",
            "v2 score",
            "Note",
        ),
        [
            (
                row["factor_label"],
                row["v1_rank"],
                row["v2_rank"],
                f"{int(row['rank_delta_v2_minus_v1']):+d}",
                f"{float(row['v1_mean_normalized_abs_effect']):.4f}",
                f"{float(row['v2_mean_normalized_abs_effect']):.4f}",
                row["comparison_note"],
            )
            for row in comparison
        ],
    )
    lines += [
        "",
        (
            "The overall order is unchanged, but the scores move: product "
            "coefficients and harvest recovery fall, while residue basis and "
            "capacity/assignment rise. The coefficient-level definition "
            "changed from E1 four-province measured to E1 + E2c, so the "
            "coefficient score is not a like-for-like evidence comparison."
        ),
        "",
        "## Which factors matter",
        "",
    ]

    def ranked_for(response_key: str) -> list[Mapping[str, Any]]:
        return sorted(
            [row for row in effects if row["response_key"] == response_key],
            key=lambda row: row["rank_for_response"],
        )

    process_top = ranked_for("national_process_residue_pj")[0]
    harvest_top = ranked_for("harvest_gross_pj")[0]
    recoverable_top = ranked_for("harvest_recoverable_pj")[0]
    recoverable_second = ranked_for("harvest_recoverable_pj")[1]
    total_top = ranked_for("total_gross_pj")[0]
    margin_top = ranked_for("national_margin_m_cad")[0]
    bc_top = ranked_for("bc_process_pj")[0]
    bc_second = ranked_for("bc_process_pj")[1]
    lines += [
        (
            f"- **National process residue**: `{process_top['factor_short_label']}` "
            f"is the dominant screen (effect "
            f"{process_top['effect_high_minus_low']:+,.3f} PJ for the 0 vs "
            "75% availability contrast). Assignment and residue basis do not "
            "change the national process total."
        ),
        (
            f"- **Harvest gross residue**: `{harvest_top['factor_short_label']}` "
            f"leads (effect "
            f"{harvest_top['effect_high_minus_low']:+,.3f} PJ), followed by "
            "product coefficients. Assignment, recovery, availability, price "
            "and zone weights do not change gross harvest residue."
        ),
        (
            f"- **Harvest recoverable residue**: `{recoverable_top['factor_short_label']}` "
            f"and `{recoverable_second['factor_short_label']}` are the "
            "leading screens; recoverable residue is gross residue times "
            "recovery. The capacity/assignment main effect "
            f"({float(effect_lookup[('harvest_recoverable_pj', 'capacity_assignment')]['effect_high_minus_low']):+,.3f} "
            "PJ) is a three-factor aliasing artifact, not a physical "
            "assignment effect."
        ),
        (
            f"- **Total gross residue**: `{total_top['factor_short_label']}` "
            f"leads (effect "
            f"{total_top['effect_high_minus_low']:+,.3f} PJ), followed by "
            "residue basis and product coefficients. Total gross inherits the "
            "availability-adjusted process stream plus harvest gross."
        ),
        (
            f"- **National modeled margin**: `{margin_top['factor_short_label']}` "
            f"is the only material screen (effect "
            f"{margin_top['effect_high_minus_low']:+,.3f} M CAD). The margin "
            "file is the economics v2.3 119 Mm3 diagnostic; physical chain "
            "factors do not re-price it."
        ),
        (
            f"- **BC process residue**: `{bc_top['factor_short_label']}` "
            f"leads (effect "
            f"{bc_top['effect_high_minus_low']:+,.3f} PJ), with "
            f"`{bc_second['factor_short_label']}` second. Trade-v5 "
            "concentrates more process residue in BC than local-v6; national "
            "process totals are unchanged. The small price-scenario BC "
            f"effect ({float(effect_lookup[('bc_process_pj', 'price_scenario')]['effect_high_minus_low']):+,.3f} "
            "PJ) is a three-factor aliasing artifact, not a physical price "
            "effect."
        ),
        (
            "- **Zone weights**: requested provincial/national responses are "
            "invariant by construction. The auxiliary `bc_b2_process_pj` "
            "diagnostic changes because the B2 weight moves from the OSeCAN "
            "supply share to an equal split; this is a sub-provincial "
            "dispatch effect."
        ),
        "",
        "## Level sweep",
        "",
        (
            "One-at-a-time descriptive sweep from the baseline configuration "
            "(`local_v6`, `e2c`, stem + branch/foliage, 0.60 recovery, 50% "
            "availability, `donor_flat`, OSeCAN v8 weights). It is not part "
            "of the PB array and does not estimate curvature."
        ),
        "",
    ]
    lines += _md_table(
        (
            "Varied factor",
            "Level",
            "Process PJ",
            "Harvest gross PJ",
            "Harvest recoverable PJ",
            "Total gross PJ",
            "BC process PJ",
        ),
        [
            (
                row["varied_factor_label"],
                row["varied_level"],
                f"{float(row['national_process_residue_pj']):,.3f}",
                f"{float(row['harvest_gross_pj']):,.3f}",
                f"{float(row['harvest_recoverable_pj']):,.3f}",
                f"{float(row['total_gross_pj']):,.3f}",
                f"{float(row['bc_process_pj']):,.3f}",
            )
            for row in sweep
        ],
    )
    lines += [
        "",
        "## What changed",
        "",
        (
            "- The coefficient level moved from v1 `measured_v4` (E1 "
            "four-province chain v4) to `e2c` (E1 measured + E2c product "
            "chain v5). Nine jurisdictions now use E2c density-calibrated "
            "product and total-residue coefficients; measured BC/QC/NB/YT "
            "are unchanged."
        ),
        (
            "- The local chain moved from v4/v5 to chain v7 and facility "
            "residue supply v7. Process residue falls from 514.614 to "
            f"{float(summary['source_values']['process_sources']['local_e2c']['process_base_pj']):.3f} "
            "PJ and total harvest gross rises from 313.640 to "
            f"{float(summary['source_values']['process_sources']['local_e2c']['harvest_gross_pj']):.3f} "
            "PJ at 119 Mm3, before availability and recovery."
        ),
        (
            "- E2c branch/foliage is now available for all 13 provinces. The "
            "E2c-total level uses the full E2c branch/foliage component "
            f"({float(summary['source_values']['components']['e2c_bf_pj']):.3f} PJ). "
            "The donor-total level remains a partial lower bound: it adds "
            "only the measured four-province branch/foliage increment "
            f"({float(summary['source_values']['components']['measured_bf_pj']):.3f} PJ)."
        ),
        (
            "- The trade E2c source is derived from facility residue supply "
            "v7_trade. National process and harvest totals are "
            "assignment-invariant; BC process residue moves from "
            f"{float(summary['source_values']['process_sources']['local_e2c']['bc_process_base_pj']):.3f} "
            "to "
            f"{float(summary['source_values']['process_sources']['trade_e2c']['bc_process_base_pj']):.3f} "
            "PJ."
        ),
        (
            "- Zone weights are read from OSeCAN handoff v8 instead of v6. "
            "The BC B1/B2 weights are unchanged, so the zone-weight screen "
            "remains zero for provincial/national responses."
        ),
        (
            "- Economics v2.3 margin values are unchanged from v1 "
            f"(`donor_flat` {float(summary['source_values']['margin_values_m_cad']['donor_flat']):,.3f} "
            "M CAD; `stumpage_scaled` "
            f"{float(summary['source_values']['margin_values_m_cad']['stumpage_scaled']):,.3f} "
            "M CAD)."
        ),
        (
            "- The v1 artifact used the E1 four-province measured level, not "
            "E2 v1. The task brief's 'E2 v1 baseline' label does not match "
            "the committed v1 inputs; this is recorded explicitly."
        ),
        "",
        "## Caveats",
        "",
        *[f"- {caveat}" for caveat in summary["caveats"]],
        "",
        (
            f"Labels: `{EVIDENCE_LABEL}`, "
            f"`observed={_bool_text(OBSERVED).lower()}`, "
            f"`policy_grade={_bool_text(POLICY_GRADE).lower()}`, "
            f"`national_claim={_bool_text(NATIONAL_CLAIM).lower()}`, "
            f"`screening_only={_bool_text(SCREENING_ONLY).lower()}`."
        ),
        "",
        "## Tests/commits/tag/status",
        "",
        (
            "Validation checks are listed in `summary.json` and the manifest. "
            "Run `.venv/bin/python -m pytest "
            f"{TEST_PATH}` for the contract tests. The artifact builder does "
            "not create a commit; the orchestrator owns git operations."
        ),
        "",
        f"Reproduce: `.venv/bin/python -m {MODULE_PATH}`",
        "",
    ]
    return "\n".join(lines)


def build(output_dir: Path | None = None) -> dict[str, Any]:
    """Build the v4 E1+E2c Plackett-Burman screening artifact."""

    assert_no_unexpected_deletions()
    deletion_checks = [
        {
            "name": "no_unexpected_deletions",
            "status": "PASS",
            "detail": "no D in parent or data repository git status at build start",
        }
    ]
    generated_utc = _utc_now()
    target = Path(output_dir) if output_dir is not None else OUTPUT_DIR

    sources, components, process_sources = build_sources()
    margin = _load_margin_values()
    zones = _load_zone_weights()
    v1 = _load_v1_comparison()
    design = build_design()
    design_checks_list, aliases = design_checks(design)

    responses = [
        _response_row(
            row["run_id"],
            _design_config(row),
            sources,
            margin["values"],
            zones,
        )
        for row in design
    ]
    sweep = build_level_sweep(sources, margin["values"], zones)
    effects, overall = _main_effects(design, responses)
    comparison, response_comparison = build_v1_comparison(
        overall, effects, v1
    )
    source_checks_list = _source_checks(sources, components)
    response_checks_list = _response_checks(responses, margin["values"])
    ranking_checks_list = _ranking_checks(effects, overall)
    label_checks_list = _label_checks(responses, sweep)
    comparison_checks_list = _comparison_checks(
        comparison, response_comparison
    )
    validation = _validation_checks(
        [*deletion_checks, *design_checks_list],
        source_checks_list,
        response_checks_list,
        ranking_checks_list,
        label_checks_list,
        comparison_checks_list,
    )
    if not validation["passed"]:
        failed = ", ".join(check["name"] for check in validation["failed"])
        raise GlobalSensitivityV2Error(
            f"validation failed; refusing to write artifact: {failed}"
        )

    input_paths = _input_paths(sources, components, margin, zones, v1)
    inputs = {_relative(path): _sha256(path) for path in input_paths}
    source_values = {
        "harvest_case": HARVEST_CASE,
        "harvest_m3": EXPECTED_HARVEST_M3,
        "lhv_gj_per_odt": LHV_GJ_PER_ODT,
        "margin_values_m_cad": margin["values"],
        "zone_weights": {
            key: {
                "BC_B1": value["BC_B1"],
                "BC_B2": value["BC_B2"],
                "method": value["method"],
            }
            for key, value in zones.items()
            if key in {"osecan_supply_dispatch", "equal_split"}
        },
        "components": {
            "donor_stem_odt": components["donor_stem_odt"],
            "donor_stem_pj": components["donor_stem_pj"],
            "measured_bf_odt": components["measured_bf_odt"],
            "measured_bf_pj": components["measured_bf_pj"],
            "e2c_stem_odt": components["e2c_stem_odt"],
            "e2c_bf_odt": components["e2c_bf_odt"],
            "e2c_total_odt": components["e2c_total_odt"],
            "e2c_stem_pj": components["e2c_stem_pj"],
            "e2c_bf_pj": components["e2c_bf_pj"],
            "e2c_total_pj": components["e2c_total_pj"],
            "measured_provinces": list(MEASURED_PROVINCES),
            "e2c_provinces": list(E2C_PROVINCES),
        },
        "process_sources": {
            key: {
                "chain_version": source["chain_version"],
                "supply_version": source["supply_version"],
                "assignment": source["assignment"],
                "coefficients": source["coefficients"],
                "process_base_pj": source["process_base_pj"],
                "harvest_gross_pj": source["harvest_gross_pj"],
                "bc_process_base_pj": source["bc_process_base_pj"],
                "derived": source["derived"],
            }
            for key, source in sorted(process_sources.items())
        },
        "chain_sources": {
            key: {
                "assignment": source["assignment"],
                "coefficients": source["coefficients"],
                "residue_basis": source["residue_basis"],
                "process_base_pj": source["process_base_pj"],
                "harvest_gross_pj": source["harvest_gross_pj"],
                "bc_process_base_pj": source["bc_process_base_pj"],
                "bf_increment_pj": source["bf_increment_pj"],
                "residue_basis_complete": source["residue_basis_complete"],
                "derived": source["derived"],
            }
            for key, source in sorted(sources.items())
        },
        "v1": {
            "artifact": v1["summary"].get("artifact"),
            "overall_ranking": {
                key: {
                    "rank": value["rank"],
                    "mean_normalized_abs_effect": value[
                        "mean_normalized_abs_effect"
                    ],
                }
                for key, value in sorted(v1["overall"].items())
            },
        },
    }
    effect_lookup = {
        (row["response_key"], row["factor_key"]): row for row in effects
    }
    assignment_recoverable_effect = float(
        effect_lookup[("harvest_recoverable_pj", "capacity_assignment")][
            "effect_high_minus_low"
        ]
    )
    price_bc_effect = float(
        effect_lookup[("bc_process_pj", "price_scenario")][
            "effect_high_minus_low"
        ]
    )
    summary: dict[str, Any] = {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "status": "pass",
        "generated_utc": generated_utc,
        "title": ARTIFACT_TITLE,
        "labels": {
            "evidence_label": EVIDENCE_LABEL,
            "model_estimate": True,
            "observed": OBSERVED,
            "policy_grade": POLICY_GRADE,
            "national_claim": NATIONAL_CLAIM,
            "screening_only": SCREENING_ONLY,
            "official_osecan_changed": OFFICIAL_OSECAN_CHANGED,
            "not_policy": NOT_POLICY,
        },
        "design": {
            "type": DESIGN_TYPE,
            "runs": DESIGN_RUNS,
            "factors": len(FACTORS),
            "factor_columns_hadamard": list(PB_COLUMNS),
            "resolution": "IV_for_the_seven_factors",
            "main_effect_aliasing": "none with two-factor interactions",
            "two_factor_aliasing": "triplets; see alias_structure.csv",
            "foldover": False,
            "material_effect_threshold_normalized": MATERIAL_EFFECT_THRESHOLD,
            "multi_level_handling": (
                "PB uses extreme levels; intermediate documented levels are "
                "reported in the one-at-a-time level sweep"
            ),
            "aliases": aliases,
        },
        "factors": [
            {
                "key": factor.key,
                "label": factor.label,
                "short_label": factor.short_label,
                "pb_low": factor.low,
                "pb_high": factor.high,
                "documented_levels": list(factor.documented_levels),
                "unit": factor.unit,
                "source_note": factor.source_note,
            }
            for factor in FACTORS
        ],
        "responses": [
            {
                "key": response.key,
                "label": response.label,
                "unit": response.unit,
                "role": response.role,
                "definition": response.definition,
            }
            for response in RESPONSES
        ],
        "inputs": inputs,
        "source_values": source_values,
        "main_effects": effects,
        "overall_ranking": overall,
        "level_sweep": sweep,
        "v1_v2_comparison": comparison,
        "v1_v2_response_comparison": response_comparison,
        "validation": validation,
        "caveats": [
            (
                "Screening artifact at the 119 Mm3 provincial allocation; "
                "the 100 and 150 Mm3 cases are not screened here."
            ),
            (
                "PB is a two-level screening design. The 0.60 recovery and "
                "25/50% process-availability levels are descriptive sweep "
                "points, not PB contrasts."
            ),
            (
                "Main effects are clear of two-factor interactions among the "
                "seven factors, but 2FIs are aliased in triplets and "
                "higher-order interactions are not resolved. No foldover was "
                "run."
            ),
            (
                "Three-factor interactions are aliased with main effects in "
                "this 16-run resolution-IV design. The price-scenario effect "
                f"on BC process residue ({price_bc_effect:+.3f} PJ) and the "
                "assignment effect on harvest recoverable residue "
                f"({assignment_recoverable_effect:+.3f} PJ) are three-factor "
                "aliasing artifacts, not physical price or assignment "
                "effects."
            ),
            (
                "The donor + total-basis combination is a partial lower "
                "bound: donor provinces have no donor branch/foliage "
                "coefficient, so only the measured four-province "
                "branch/foliage increment is added. It is flagged "
                "`residue_basis_complete=false`."
            ),
            (
                "E2c-total includes E2c branch/foliage for all 13 provinces. "
                "The E2c-vs-donor coefficient effect at total basis "
                "therefore includes both the stem-residue coefficient change "
                "and the E2c branch/foliage coverage change."
            ),
            (
                "The v1 artifact used the E1 four-province chain v4 as its "
                "`measured_v4` level, not E2 v1. This refresh compares v4 "
                "E1+E2c against that committed v1 baseline."
            ),
            (
                "The trade E2c source is derived from facility residue "
                "supply v7_trade. National process and harvest totals are "
                "assignment-invariant by construction; only the "
                "facility-province distribution, including BC process "
                "residue, changes."
            ),
            (
                "Process residue is facility-assigned and harvest residue is "
                "origin-province only. Total gross is the "
                "availability-adjusted process stream plus harvest gross; it "
                "is not a combined OSeCAN row."
            ),
            (
                "National margin is the existing economics v2.3 119 Mm3 "
                "diagnostic. Physical chain factors do not re-price it; only "
                "the price scenario changes the margin response."
            ),
            (
                "A price-scenario effect can appear on BC process residue "
                "through higher-order aliasing in the 16-run design; it is "
                "not a physical price effect."
            ),
            (
                "Zone weights change sub-provincial dispatch only. Requested "
                "provincial and national responses are invariant by "
                "construction; the BC B2 diagnostic is auxiliary."
            ),
            (
                "Assignment local-v6 vs trade-v5 changes the facility "
                "distribution, not national products or process residue; BC "
                "process residue changes because facility location changes. "
                "The trade case is a screening counterfactual, not an "
                "interprovincial trade forecast."
            ),
            (
                "Product coefficients donor-v1 vs E2c replace all 13 "
                "jurisdictions' product and residue coefficients; BC/QC/NB/YT "
                "remain measured, the other nine are E2c density-calibrated "
                "open-data estimates."
            ),
            (
                "All values are model estimates; observed=false and "
                "policy_grade=false. No policy, AAC, tenure, investment, "
                "interprovincial-trade, or national-supply claim is made."
            ),
            (
                "No UI or official OSeCAN surface is changed. OSeCAN "
                "handoff v8 zone weights are read only."
            ),
        ],
        "reproduce": f".venv/bin/python -m {MODULE_PATH}",
        "test_command": f".venv/bin/python -m pytest {TEST_PATH}",
    }

    target.mkdir(parents=True, exist_ok=True)
    design_fields = [
        "run_id",
        "design_type",
        *[
            field
            for factor in FACTORS
            for field in (f"{factor.key}_code", factor.key)
        ],
    ]
    _write_csv(target / "design_matrix.csv", design, design_fields)

    response_fields = [
        "run_id",
        "source_key",
        "source_description",
        "source_derived",
        "capacity_assignment",
        "product_coefficients",
        "residue_basis",
        "source_residue_basis",
        "harvest_recovery",
        "process_availability",
        "price_scenario",
        "zone_weights",
        "process_base_pj",
        "harvest_gross_pj",
        "bc_process_base_pj",
        "bf_increment_pj",
        "b2_weight",
        "national_process_residue_pj",
        "harvest_recoverable_pj",
        "total_gross_pj",
        "national_margin_m_cad",
        "bc_process_pj",
        "bc_b2_process_pj",
        "national_total_recoverable_pj",
        "residue_basis_complete",
        "harvest_gross_basis",
        "derivation",
        "response_basis",
        "evidence_label",
        "observed",
        "policy_grade",
        "national_claim",
        "screening_only",
        "official_osecan_changed",
        "not_policy",
    ]
    _write_csv(target / "responses.csv", responses, response_fields)

    effect_fields = [
        "response_key",
        "response_label",
        "unit",
        "response_role",
        "factor_key",
        "factor_label",
        "factor_short_label",
        "low_level",
        "high_level",
        "mean_low",
        "mean_high",
        "effect_high_minus_low",
        "abs_effect",
        "effect_pct_of_grand_mean",
        "grand_mean",
        "normalized_abs_effect",
        "material_effect",
        "rank_for_response",
        "tie_note",
    ]
    _write_csv(target / "main_effects.csv", effects, effect_fields)

    overall_fields = [
        "rank",
        "factor_key",
        "factor_label",
        "factor_short_label",
        "mean_normalized_abs_effect",
        "max_normalized_abs_effect",
        "responses_with_nonzero_effect",
        "responses_with_material_effect",
        "nonzero_responses",
        "affected_responses",
        "top_response",
    ]
    _write_csv(target / "overall_ranking.csv", overall, overall_fields)

    sweep_fields = [
        "sweep_id",
        "varied_factor",
        "varied_factor_label",
        "varied_level",
        "is_baseline_level",
        "sweep_role",
        *response_fields,
    ]
    _write_csv(target / "level_sweep.csv", sweep, sweep_fields)
    _write_csv(
        target / "alias_structure.csv",
        aliases,
        (
            "alias_group",
            "members",
            "aliased_with",
            "hadamard_column",
            "sign",
            "note",
        ),
    )
    _write_csv(
        target / "factor_levels.csv",
        [
            {
                "factor_key": factor.key,
                "factor_label": factor.label,
                "pb_low": factor.low,
                "pb_high": factor.high,
                "documented_levels": ";".join(
                    str(level) for level in factor.documented_levels
                ),
                "unit": factor.unit,
                "source_note": factor.source_note,
            }
            for factor in FACTORS
        ],
        (
            "factor_key",
            "factor_label",
            "pb_low",
            "pb_high",
            "documented_levels",
            "unit",
            "source_note",
        ),
    )
    _write_csv(
        target / "bf_component_by_province.csv",
        components["rows"],
        (
            "province",
            "donor_stem_gross_odt",
            "measured_total_gross_odt",
            "measured_branch_foliage_increment_odt",
            "e2c_stem_gross_odt",
            "e2c_branch_foliage_odt",
            "e2c_total_gross_odt",
            "measured_branch_foliage",
            "e2c_density_calibrated",
        ),
    )
    _write_csv(
        target / "v1_v2_comparison.csv",
        comparison,
        (
            "factor_key",
            "factor_label",
            "v1_rank",
            "v2_rank",
            "rank_delta_v2_minus_v1",
            "v1_mean_normalized_abs_effect",
            "v2_mean_normalized_abs_effect",
            "score_delta_v2_minus_v1",
            "comparison_note",
        ),
    )
    _write_csv(
        target / "v1_v2_response_comparison.csv",
        response_comparison,
        (
            "response_key",
            "response_label",
            "factor_key",
            "factor_label",
            "v1_rank",
            "v2_rank",
            "rank_delta_v2_minus_v1",
            "v1_effect_high_minus_low",
            "v2_effect_high_minus_low",
            "effect_delta_v2_minus_v1",
        ),
    )

    report_text = _report(
        summary,
        design,
        responses,
        effects,
        overall,
        sweep,
        aliases,
        comparison,
    )
    (target / REPORT_NAME).write_text(report_text, encoding="utf-8")
    summary["outputs"] = {
        path.name: {
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }
        for path in sorted(target.iterdir())
        if path.is_file() and path.name not in {"summary.json", "manifest.json"}
    }
    _write_json(target / "summary.json", summary)
    manifest = {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": generated_utc,
        "labels": summary["labels"],
        "design": {
            key: value
            for key, value in summary["design"].items()
            if key != "aliases"
        },
        "factors": summary["factors"],
        "responses": summary["responses"],
        "inputs": inputs,
        "outputs": summary["outputs"],
        "source_values": source_values,
        "validation": validation,
        "v1_v2_comparison": comparison,
        "reproduce": summary["reproduce"],
        "test_command": summary["test_command"],
    }
    _write_json(target / "manifest.json", manifest)

    assert_no_unexpected_deletions()
    return summary


def main() -> None:
    summary = build()
    print(
        json.dumps(
            {
                "artifact": summary["artifact"],
                "status": summary["status"],
                "validation_passed": summary["validation"]["passed"],
                "design": {
                    "type": summary["design"]["type"],
                    "runs": summary["design"]["runs"],
                    "resolution": summary["design"]["resolution"],
                },
                "overall_ranking": [
                    {
                        "rank": row["rank"],
                        "factor": row["factor_key"],
                        "mean_normalized_abs_effect": round(
                            float(row["mean_normalized_abs_effect"]), 6
                        ),
                    }
                    for row in summary["overall_ranking"]
                ],
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
