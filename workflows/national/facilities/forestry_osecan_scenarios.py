"""Build multi-scenario forestry residue PJ for OSeCAN zone handoff.

The artifact crosses three documented inputs:

* ``national_facility_chain_scenarios_v1``: 100/119/150 Mm3 harvest cases with
  province-level process residue (facility-assigned) and gross harvest residue
  (province-only).
* ``forestry_osecan_handoff_v2``: explicit province-to-zone allocation weights
  covering all 13 OSeCAN zones with no ``UNMAPPED`` bucket.
* ``national_residue_curve_v1``: the seven availability/recovery cases
  (process 0/25/50/75%; harvest 35/60/75%).

Every scenario/stream/case province total is multiplied by the v2 zone weights
and then written by zone.  The 119m scenario must reproduce the national
residue curve at every case; that equality is a validation guard rather than an
assumption.

The official OSeCAN tree is read-only.  It is hashed before and after the
build, and the build fails if any byte changes.  The candidate activity-limit
table is a documented, read-only screening table; it is not applied to any
OSeCAN technology or region.  Every row carries ``model_estimate=true``,
``observed=false``, ``policy_grade=false``, and ``official_osecan_changed=false``.

Fail-closed rules
-----------------
* Any unexpected ``D`` in parent or ``data`` repository status halts the build.
* Inputs are parsed once at the boundary and trusted internally.
* Scenario order, zone coverage, weight conservation, PJ conversion,
  scenario/zone conservation, reference arithmetic, and labels are validated.
* The output directory must not live under the official OSeCAN tree.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from workflows.national.facilities import forestry_osecan_handoff as v1
from workflows.national.facilities import forestry_osecan_handoff_v2 as v2

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_ROOT = REPO_ROOT / "data"

SCENARIO_DIR = (
    DATA_ROOT / "derived/national/facilities/national_facility_chain_scenarios_v1"
)
SCENARIO_PRODUCT_RESIDUE = SCENARIO_DIR / "scenario_product_residue.csv"
SCENARIO_SUMMARY = SCENARIO_DIR / "scenario_summary.csv"
SCENARIO_SUMMARY_JSON = SCENARIO_DIR / "summary.json"
SCENARIO_MANIFEST = SCENARIO_DIR / "manifest.json"

HANDOFF_V2_DIR = DATA_ROOT / "derived/national/facilities/forestry_osecan_handoff_v2"
HANDOFF_V2_WEIGHTS = HANDOFF_V2_DIR / "zone_allocation_weights.csv"
HANDOFF_V2_SUMMARY = HANDOFF_V2_DIR / "summary.json"
HANDOFF_V2_MANIFEST = HANDOFF_V2_DIR / "manifest.json"

CURVE_DIR = v1.CURVE_DIR
NATIONAL_CURVE = v1.NATIONAL_CURVE
PROVINCE_CURVE = v1.PROVINCE_CURVE
CURVE_SUMMARY = v1.CURVE_SUMMARY

OSECAN_DIR = v1.OSECAN_DIR
CROSSWALK = v1.CROSSWALK
SUPPLY_CURVE_DIR = v1.SUPPLY_CURVE_DIR
TIER_DEFINITIONS = v1.TIER_DEFINITIONS
TIER_LIMITS = v1.TIER_LIMITS
BC_ZONAL_LIMITS = v1.BC_ZONAL_LIMITS
SUPPLY_DEFINITION = v1.SUPPLY_DEFINITION
DISPATCH = v1.DISPATCH
NATIONAL_MANIFEST = v1.NATIONAL_MANIFEST

OUTPUT_DIR = DATA_ROOT / "derived/national/facilities/forestry_osecan_scenarios_v1"

ARTIFACT = "forestry_osecan_scenarios_v1"
ARTIFACT_VERSION = "1.0.0"
ARTIFACT_TITLE = "Forestry Residue Scenarios to OSeCAN Zones v1"
REPORT_NAME = "FORESTRY_OSECAN_SCENARIOS_V1.md"
CHAIN_LABEL = "national_facility_chain_scenarios_v1"
HANDOFF_LABEL = "forestry_osecan_handoff_v2"
CURVE_LABEL = "national_residue_curve_v1"
MODULE_PATH = "workflows.national.facilities.forestry_osecan_scenarios"
TEST_PATH = "tests/test_forestry_osecan_scenarios.py"

SCENARIO_ORDER: tuple[str, ...] = ("100m", "119m", "150m")
PRIMARY_SCENARIO = "119m"

PROCESS_STREAM = v1.PROCESS_STREAM
HARVEST_STREAM = v1.HARVEST_STREAM
PROCESS_CLASS_TOTAL = v1.PROCESS_CLASS_TOTAL
HARVEST_CLASS_ALL = v1.HARVEST_CLASS_ALL
PROCESS_CASES = v1.PROCESS_CASES
HARVEST_CASES = v1.HARVEST_CASES
PROVINCES = v1.PROVINCES
PROVINCE_ORDER = v1.PROVINCE_ORDER
ZONE_ORDER = v1.ZONE_ORDER
BC_ZONES = v1.BC_ZONES
PJ_GJ = v1.PJ_GJ
EXPECTED_LHV_GJ_PER_ODT = v1.EXPECTED_LHV_GJ_PER_ODT
LABEL_TRUE = v1.LABEL_TRUE
LABEL_FALSE = v1.LABEL_FALSE
UNMAPPED_ZONE = v1.UNMAPPED_ZONE

MULTI_ZONE_PROVINCES = v2.MULTI_ZONE_PROVINCES
PROXY_SHARE_PROVINCES = frozenset(MULTI_ZONE_PROVINCES)
EXPECTED_ZONE_WEIGHT_ROWS = 17
EXPECTED_RESIDUE_ROWS = (
    len(SCENARIO_ORDER)
    * (len(PROCESS_CASES) + len(HARVEST_CASES))
    * EXPECTED_ZONE_WEIGHT_ROWS
)
EXPECTED_ZONE_SUMMARY_ROWS = (
    len(SCENARIO_ORDER) * len(ZONE_ORDER) * (len(PROCESS_CASES) + len(HARVEST_CASES))
)
EXPECTED_CANDIDATE_ROWS = EXPECTED_ZONE_SUMMARY_ROWS
EXPECTED_COMPARISON_ROWS = (
    len(SCENARIO_ORDER)
    * (len(PROCESS_CASES) + len(HARVEST_CASES))
    * (5 + 2 + len(ZONE_ORDER) + len(BC_ZONES))
)

B1_LIMIT_TECHNOLOGY = "SUPBCWB1"
B2_LIMIT_TECHNOLOGY = "SUPBCWB2"
ZONE_LIMIT_TECHNOLOGY = {"B1": B1_LIMIT_TECHNOLOGY, "B2": B2_LIMIT_TECHNOLOGY}
REST_OF_CANADA_TIERS = "SUPBCWT1|SUPBCWT2|SUPBCWT3|SUPBCWT4"

SCENARIO_EVIDENCE_TIER = "scenario_model_estimate|handoff_v2_zone_weight"
PROCESS_SCENARIO_CAVEAT = (
    "Process residue is facility-assigned at the province of the receiving "
    "facility, then split to OSeCAN zones by explicit handoff-v2 proxy "
    "weights. It is a model estimate, not observed mill output, and not "
    "policy. No official OSeCAN file is changed."
)
HARVEST_SCENARIO_CAVEAT = (
    "Harvest residue is province-only gross residue multiplied by the case "
    "recovery fraction, then split to OSeCAN zones by explicit handoff-v2 "
    "proxy weights. It is a model estimate, not observed recovery, and not "
    "policy. No official OSeCAN file is changed."
)


class ForestryOsecanScenariosError(v1.ForestryOsecanHandoffError):
    """Raised when the multi-scenario forestry residue handoff fails a guard."""


@dataclass(frozen=True)
class ScenarioDefinition:
    """Trusted national scenario totals from the chain-scenarios artifact."""

    scenario: str
    harvest_m3: float
    process_odt: float
    process_pj: float
    harvest_gross_odt: float
    harvest_gross_pj: float
    harvest_recoverable_odt: float
    harvest_recoverable_pj: float
    evidence_label: str


@dataclass(frozen=True)
class ProvinceResidue:
    """Trusted scenario/province residue totals."""

    scenario: str
    province: str
    province_name: str
    process_odt: float
    process_pj: float
    harvest_gross_odt: float
    harvest_gross_pj: float


@dataclass(frozen=True)
class CurveCase:
    """Trusted national residue-curve case total."""

    stream: str
    availability_case: str
    availability_fraction: float
    odt: float
    pj: float


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_csv(path: Path, required: Sequence[str], label: str) -> pd.DataFrame:
    return v1._read_csv(path, required, label)


def _parse_bool(value: Any, label: str) -> bool:
    return v1._parse_bool(value, label)


def _finite_nonnegative(value: Any, label: str) -> float:
    return v1._finite_nonnegative(value, label)


def _require_close(
    left: float, right: float, label: str, *, abs_tol: float = v1.ABS_TOL
) -> None:
    v1._require_close(left, right, label, abs_tol=abs_tol)


def _load_json(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise ForestryOsecanScenariosError(f"{label} is missing: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ForestryOsecanScenariosError(
            f"{label} is not valid JSON: {path}: {error}"
        ) from error
    if not isinstance(payload, dict):
        raise ForestryOsecanScenariosError(f"{label} is not a JSON object: {path}")
    return payload


def assert_no_unexpected_deletions() -> None:
    """Fail closed on any ``D`` in parent or data repository status."""
    v1.assert_no_unexpected_deletions()


def _hash_osecan_tree() -> tuple[dict[str, str], str]:
    return v1._hash_tree(OSECAN_DIR)


def _parse_scenario_inputs() -> tuple[
    dict[str, ScenarioDefinition],
    dict[tuple[str, str], ProvinceResidue],
]:
    """Parse and reconcile the scenario totals and province residue detail."""
    summary_frame = _read_csv(
        SCENARIO_SUMMARY,
        [
            "scenario",
            "harvest_case_m3",
            "process_residue_odt",
            "process_residue_pj",
            "harvest_residue_gross_odt",
            "harvest_residue_gross_pj",
            "harvest_residue_recoverable_odt",
            "harvest_residue_recoverable_pj",
            "evidence_label",
            "observed",
            "policy_grade",
        ],
        "scenario summary",
    )
    scenario_frame = summary_frame.copy()
    scenario_frame["scenario"] = scenario_frame["scenario"].astype(str).str.strip()
    observed_order = list(dict.fromkeys(scenario_frame["scenario"]))
    if observed_order != list(SCENARIO_ORDER):
        raise ForestryOsecanScenariosError(
            f"scenario summary order does not match 100m/119m/150m: {observed_order}"
        )

    definitions: dict[str, ScenarioDefinition] = {}
    for _, record in scenario_frame.iterrows():
        scenario = str(record["scenario"])
        harvest_m3 = _finite_nonnegative(
            record["harvest_case_m3"], f"{scenario} harvest case m3"
        )
        process_odt = _finite_nonnegative(
            record["process_residue_odt"], f"{scenario} process odt"
        )
        process_pj = _finite_nonnegative(
            record["process_residue_pj"], f"{scenario} process pj"
        )
        harvest_odt = _finite_nonnegative(
            record["harvest_residue_gross_odt"], f"{scenario} harvest gross odt"
        )
        harvest_pj = _finite_nonnegative(
            record["harvest_residue_gross_pj"], f"{scenario} harvest gross pj"
        )
        recoverable_odt = _finite_nonnegative(
            record["harvest_residue_recoverable_odt"],
            f"{scenario} harvest recoverable odt",
        )
        recoverable_pj = _finite_nonnegative(
            record["harvest_residue_recoverable_pj"],
            f"{scenario} harvest recoverable pj",
        )
        evidence_label = str(record["evidence_label"]).strip()
        if evidence_label != "model_estimate":
            raise ForestryOsecanScenariosError(
                f"{scenario}: unexpected evidence label {evidence_label!r}"
            )
        if _parse_bool(record["observed"], f"{scenario} observed"):
            raise ForestryOsecanScenariosError(f"{scenario}: observed is true")
        if _parse_bool(record["policy_grade"], f"{scenario} policy_grade"):
            raise ForestryOsecanScenariosError(f"{scenario}: policy_grade is true")
        if recoverable_odt > harvest_odt + 1e-3 or recoverable_pj > harvest_pj + 1e-5:
            raise ForestryOsecanScenariosError(
                f"{scenario}: recoverable harvest residue exceeds gross residue"
            )
        definitions[scenario] = ScenarioDefinition(
            scenario=scenario,
            harvest_m3=harvest_m3,
            process_odt=process_odt,
            process_pj=process_pj,
            harvest_gross_odt=harvest_odt,
            harvest_gross_pj=harvest_pj,
            harvest_recoverable_odt=recoverable_odt,
            harvest_recoverable_pj=recoverable_pj,
            evidence_label=evidence_label,
        )

    if set(definitions) != set(SCENARIO_ORDER):
        raise ForestryOsecanScenariosError("scenario summary lacks a scenario")
    if not (
        definitions["100m"].harvest_m3
        < definitions["119m"].harvest_m3
        < definitions["150m"].harvest_m3
    ):
        raise ForestryOsecanScenariosError(
            "scenario harvest m3 values are not strictly ordered"
        )

    frame = _read_csv(
        SCENARIO_PRODUCT_RESIDUE,
        [
            "scenario",
            "province",
            "province_name",
            "record_type",
            "facility_class",
            "process_residue_odt",
            "process_residue_pj",
            "harvest_residue_gross_odt",
            "harvest_residue_gross_pj",
            "harvest_residue_recoverable_odt",
            "harvest_residue_recoverable_pj",
            "facility_assigned",
            "evidence_label",
            "observed",
            "policy_grade",
        ],
        "scenario product residue",
    )
    for column in ("scenario", "province", "record_type", "facility_class"):
        frame[column] = frame[column].astype(str).str.strip()
    if set(frame["scenario"]) != set(SCENARIO_ORDER):
        raise ForestryOsecanScenariosError(
            "scenario product residue has an unexpected scenario set"
        )
    unknown_provinces = set(frame["province"]) - set(PROVINCES)
    if unknown_provinces:
        raise ForestryOsecanScenariosError(
            f"scenario product residue has unknown provinces: "
            f"{sorted(unknown_provinces)}"
        )

    process_accumulator: dict[tuple[str, str], dict[str, float]] = defaultdict(
        lambda: {"odt": 0.0, "pj": 0.0}
    )
    process_classes: dict[tuple[str, str], set[str]] = defaultdict(set)
    harvest_rows: dict[tuple[str, str], pd.Series] = {}

    for _, record in frame.iterrows():
        scenario = str(record["scenario"])
        province = str(record["province"])
        record_type = str(record["record_type"])
        if _parse_bool(record["observed"], f"{scenario}/{province} observed"):
            raise ForestryOsecanScenariosError(
                f"{scenario}/{province}: observed is true"
            )
        if _parse_bool(record["policy_grade"], f"{scenario}/{province} policy_grade"):
            raise ForestryOsecanScenariosError(
                f"{scenario}/{province}: policy_grade is true"
            )
        evidence_label = str(record["evidence_label"]).strip()
        if evidence_label != "model_estimate":
            raise ForestryOsecanScenariosError(
                f"{scenario}/{province}: unexpected evidence label {evidence_label!r}"
            )

        if record_type == "facility_process":
            facility_class = str(record["facility_class"]).strip()
            if facility_class not in v1.CLASS_ORDER:
                raise ForestryOsecanScenariosError(
                    f"{scenario}/{province}: unknown facility class {facility_class!r}"
                )
            key = (scenario, province)
            process_classes[key].add(facility_class)
            odt = _finite_nonnegative(
                record["process_residue_odt"],
                f"{scenario}/{province}/{facility_class} process odt",
            )
            pj = _finite_nonnegative(
                record["process_residue_pj"],
                f"{scenario}/{province}/{facility_class} process pj",
            )
            _require_close(
                pj,
                odt * EXPECTED_LHV_GJ_PER_ODT / PJ_GJ,
                f"{scenario}/{province}/{facility_class} process PJ",
                abs_tol=1e-6,
            )
            process_accumulator[key]["odt"] += odt
            process_accumulator[key]["pj"] += pj
            continue

        if record_type != "harvest_residue":
            raise ForestryOsecanScenariosError(
                f"{scenario}/{province}: unexpected record type {record_type!r}"
            )
        if str(record["facility_class"]).strip() != HARVEST_CLASS_ALL:
            raise ForestryOsecanScenariosError(
                f"{scenario}/{province}: harvest class is not ALL"
            )
        if _parse_bool(
            record["facility_assigned"], f"{scenario}/{province} harvest assigned"
        ):
            raise ForestryOsecanScenariosError(
                f"{scenario}/{province}: harvest residue is facility-assigned"
            )
        key = (scenario, province)
        if key in harvest_rows:
            raise ForestryOsecanScenariosError(f"duplicate harvest residue row: {key}")
        harvest_rows[key] = record

    process_provinces_by_scenario = {
        scenario: {
            province
            for (candidate_scenario, province) in process_accumulator
            if candidate_scenario == scenario
        }
        for scenario in SCENARIO_ORDER
    }
    first_process_provinces = process_provinces_by_scenario[SCENARIO_ORDER[0]]
    if any(
        provinces != first_process_provinces
        for provinces in process_provinces_by_scenario.values()
    ):
        raise ForestryOsecanScenariosError(
            "process province coverage differs across scenarios"
        )
    if len(first_process_provinces) != len(PROVINCES) - 1:
        raise ForestryOsecanScenariosError(
            "expected exactly one jurisdiction without facility process residue"
        )
    for key, classes in process_classes.items():
        if classes != set(v1.CLASS_ORDER):
            raise ForestryOsecanScenariosError(
                f"incomplete facility class coverage for {key}: {sorted(classes)}"
            )
    expected_harvest_rows = len(SCENARIO_ORDER) * len(PROVINCES)
    if len(harvest_rows) != expected_harvest_rows:
        raise ForestryOsecanScenariosError(
            f"expected {expected_harvest_rows} harvest province rows, "
            f"found {len(harvest_rows)}"
        )

    province_rows: dict[tuple[str, str], ProvinceResidue] = {}
    for scenario in SCENARIO_ORDER:
        definition = definitions[scenario]
        process_odt_total = 0.0
        process_pj_total = 0.0
        harvest_odt_total = 0.0
        harvest_pj_total = 0.0
        for province in PROVINCE_ORDER:
            key = (scenario, province)
            accumulated = process_accumulator.get(key, {"odt": 0.0, "pj": 0.0})
            process_odt = _finite_nonnegative(
                accumulated["odt"], f"{scenario}/{province} process odt total"
            )
            process_pj = _finite_nonnegative(
                accumulated["pj"], f"{scenario}/{province} process pj total"
            )
            _require_close(
                process_pj,
                process_odt * EXPECTED_LHV_GJ_PER_ODT / PJ_GJ,
                f"{scenario}/{province} process total PJ",
                abs_tol=1e-6,
            )
            harvest_record = harvest_rows[(scenario, province)]
            harvest_odt = _finite_nonnegative(
                harvest_record["harvest_residue_gross_odt"],
                f"{scenario}/{province} harvest gross odt",
            )
            harvest_pj = _finite_nonnegative(
                harvest_record["harvest_residue_gross_pj"],
                f"{scenario}/{province} harvest gross pj",
            )
            _require_close(
                harvest_pj,
                harvest_odt * EXPECTED_LHV_GJ_PER_ODT / PJ_GJ,
                f"{scenario}/{province} harvest gross PJ",
                abs_tol=1e-6,
            )
            province_rows[key] = ProvinceResidue(
                scenario=scenario,
                province=province,
                province_name=PROVINCES[province],
                process_odt=process_odt,
                process_pj=process_pj,
                harvest_gross_odt=harvest_odt,
                harvest_gross_pj=harvest_pj,
            )
            process_odt_total += process_odt
            process_pj_total += process_pj
            harvest_odt_total += harvest_odt
            harvest_pj_total += harvest_pj

        _require_close(
            process_odt_total,
            definition.process_odt,
            f"{scenario} national process odt reconciliation",
            abs_tol=1e-3,
        )
        _require_close(
            process_pj_total,
            definition.process_pj,
            f"{scenario} national process PJ reconciliation",
            abs_tol=1e-5,
        )
        _require_close(
            harvest_odt_total,
            definition.harvest_gross_odt,
            f"{scenario} national harvest gross odt reconciliation",
            abs_tol=1e-3,
        )
        _require_close(
            harvest_pj_total,
            definition.harvest_gross_pj,
            f"{scenario} national harvest gross PJ reconciliation",
            abs_tol=1e-5,
        )

    return definitions, province_rows


def _parse_zone_weights(
    zones: Mapping[str, v1.Zone],
) -> dict[str, tuple[v2.ZoneWeight, ...]]:
    """Parse the published handoff-v2 weights and verify their provenance."""
    frame = _read_csv(
        HANDOFF_V2_WEIGHTS,
        [
            "province",
            "zone_id",
            "zone_name",
            "weight",
            "weight_method",
            "evidence_tier",
            "proxy_share",
            "source",
            "source_value",
            "source_unit",
            "source_run",
            "source_year",
            "source_technology",
            "mapping_basis",
            "model_estimate",
            "observed",
            "policy_grade",
            "official_osecan_changed",
            "caveat",
            "notes",
        ],
        "handoff v2 zone weights",
    )
    if len(frame) != EXPECTED_ZONE_WEIGHT_ROWS:
        raise ForestryOsecanScenariosError(
            f"expected {EXPECTED_ZONE_WEIGHT_ROWS} zone weight rows, found {len(frame)}"
        )
    for column in (
        "province",
        "zone_id",
        "zone_name",
        "weight_method",
        "evidence_tier",
        "source",
        "source_unit",
        "source_run",
        "source_year",
        "source_technology",
        "mapping_basis",
        "caveat",
        "notes",
    ):
        frame[column] = frame[column].astype(str).str.strip()
    frame["zone_id"] = frame["zone_id"].str.upper()

    if frame.duplicated(["province", "zone_id"]).any():
        raise ForestryOsecanScenariosError("duplicate province/zone weight row")
    if set(frame["province"]) != set(PROVINCES):
        raise ForestryOsecanScenariosError(
            "handoff v2 weights do not cover exactly the 13 jurisdictions"
        )
    if set(frame["zone_id"]) != set(ZONE_ORDER):
        raise ForestryOsecanScenariosError(
            "handoff v2 weights do not cover exactly the 13 OSeCAN zones"
        )

    weights_by_province: dict[str, list[v2.ZoneWeight]] = defaultdict(list)
    for _, record in frame.iterrows():
        province = str(record["province"])
        zone_id = str(record["zone_id"])
        zone = zones[zone_id]
        if province not in zone.provinces:
            raise ForestryOsecanScenariosError(
                f"{province} is not a member of zone {zone_id} in the crosswalk"
            )
        zone_name = str(record["zone_name"])
        if zone_name != zone.zone_name:
            raise ForestryOsecanScenariosError(
                f"{province}/{zone_id}: zone name {zone_name!r} does not match "
                f"crosswalk {zone.zone_name!r}"
            )
        weight = _finite_nonnegative(record["weight"], f"{province}/{zone_id} weight")
        if weight <= 0.0 or weight > 1.0:
            raise ForestryOsecanScenariosError(
                f"{province}/{zone_id}: weight {weight} is outside (0, 1]"
            )
        proxy_share = _parse_bool(
            record["proxy_share"], f"{province}/{zone_id} proxy_share"
        )
        expected_proxy = province in PROXY_SHARE_PROVINCES
        if proxy_share != expected_proxy:
            raise ForestryOsecanScenariosError(
                f"{province}/{zone_id}: proxy_share mismatch"
            )
        method = str(record["weight_method"])
        tier = str(record["evidence_tier"])
        if province == "BC":
            expected_method, expected_tier = (
                "osecan_zonal_supply_dispatch",
                "tier_1_osecan_zonal_supply_model",
            )
        elif province in {"AB", "ON", "QC"}:
            expected_method, expected_tier = (
                "osecan_zonal_dispatch_proxy",
                "tier_2_osecan_zonal_dispatch_model",
            )
        else:
            expected_method, expected_tier = (
                "single_zone_crosswalk",
                "tier_0_crosswalk_membership",
            )
        if (method, tier) != (expected_method, expected_tier):
            raise ForestryOsecanScenariosError(
                f"{province}/{zone_id}: unexpected method/tier {method!r}/{tier!r}"
            )
        for label_name in (
            "model_estimate",
            "observed",
            "policy_grade",
            "official_osecan_changed",
        ):
            expected = LABEL_TRUE if label_name == "model_estimate" else LABEL_FALSE
            if str(record[label_name]).strip().lower() != expected:
                raise ForestryOsecanScenariosError(
                    f"{province}/{zone_id}: label {label_name} is not {expected}"
                )
        if not str(record["caveat"]) or not str(record["notes"]):
            raise ForestryOsecanScenariosError(
                f"{province}/{zone_id}: empty caveat or notes"
            )
        weights_by_province[province].append(
            v2.ZoneWeight(
                province=province,
                zone_id=zone_id,
                zone_name=zone_name,
                weight=weight,
                method=method,
                evidence_tier=tier,
                proxy_share=proxy_share,
                source=str(record["source"]),
                source_value=_finite_nonnegative(
                    record["source_value"],
                    f"{province}/{zone_id} source value",
                ),
                source_unit=str(record["source_unit"]),
                source_run=str(record["source_run"]),
                source_year=str(record["source_year"]),
                source_technology=str(record["source_technology"]),
                mapping_basis=str(record["mapping_basis"]),
                note=str(record["notes"]),
            )
        )

    frozen: dict[str, tuple[v2.ZoneWeight, ...]] = {}
    for province in PROVINCE_ORDER:
        province_weights = tuple(weights_by_province[province])
        if not province_weights:
            raise ForestryOsecanScenariosError(
                f"{province} has no zone allocation weight"
            )
        _require_close(
            math.fsum(weight.weight for weight in province_weights),
            1.0,
            f"{province} zone weight sum",
            abs_tol=1e-12,
        )
        if province in MULTI_ZONE_PROVINCES:
            if len(province_weights) < 2:
                raise ForestryOsecanScenariosError(
                    f"{province} spans multiple zones but has one weight"
                )
        elif len(province_weights) != 1 or province_weights[0].weight != 1.0:
            raise ForestryOsecanScenariosError(
                f"{province} is single-zone but does not have weight 1"
            )
        frozen[province] = province_weights
    return frozen


def _verify_weights_against_dispatch(
    weights_by_province: Mapping[str, tuple[v2.ZoneWeight, ...]],
    dispatch: v2.DispatchEvidence,
) -> dict[str, dict[str, float]]:
    """Confirm published weights equal the dispatch-derived screening proxies."""
    for province in MULTI_ZONE_PROVINCES:
        published = {
            weight.zone_id: weight.weight for weight in weights_by_province[province]
        }
        derived = dispatch.weights[province]
        if set(published) != set(derived):
            raise ForestryOsecanScenariosError(
                f"{province}: published zones do not match dispatch zones"
            )
        derived_total = math.fsum(derived.values())
        if derived_total <= 0.0:
            raise ForestryOsecanScenariosError(
                f"{province}: dispatch weight source sums to zero"
            )
        for zone_id, source_value in derived.items():
            _require_close(
                published[zone_id],
                source_value / derived_total,
                f"{province}/{zone_id} dispatch weight",
                abs_tol=1e-12,
            )
    if set(dispatch.zone_dispatch_context) != set(ZONE_ORDER):
        raise ForestryOsecanScenariosError(
            "OSeCAN dispatch context does not cover all 13 zones"
        )
    return dict(dispatch.zone_dispatch_context)


def _parse_national_curve() -> dict[tuple[str, str], CurveCase]:
    """Parse the national curve cases used as the 119m reconciliation guard."""
    frame = _read_csv(
        NATIONAL_CURVE,
        [
            "geography_level",
            "province",
            "residue_class",
            "stream",
            "availability_case",
            "availability_fraction",
            "residue_odt",
            "residue_pj",
            "lhv_gj_per_odt",
            "model_estimate",
            "observed",
            "policy_grade",
            "not_policy",
            "in_supply_curve",
        ],
        "national residue curve",
    )
    for column in ("geography_level", "stream", "residue_class", "availability_case"):
        frame[column] = frame[column].astype(str).str.strip()
    frame["stream"] = frame["stream"].str.lower()
    frame["residue_class"] = frame["residue_class"].str.upper()
    if set(frame["geography_level"]) != {"national"}:
        raise ForestryOsecanScenariosError(
            "national curve contains non-national geography rows"
        )
    selected = frame[
        (
            (frame["stream"] == PROCESS_STREAM)
            & (frame["residue_class"] == PROCESS_CLASS_TOTAL)
        )
        | (
            (frame["stream"] == HARVEST_STREAM)
            & (frame["residue_class"] == HARVEST_CLASS_ALL)
        )
    ]
    cases: dict[tuple[str, str], CurveCase] = {}
    for _, record in selected.iterrows():
        stream = str(record["stream"])
        case = str(record["availability_case"])
        fraction = _finite_nonnegative(
            record["availability_fraction"], f"curve {stream}/{case} fraction"
        )
        expected_fraction = dict((*PROCESS_CASES, *HARVEST_CASES)).get(case)
        if expected_fraction is None:
            raise ForestryOsecanScenariosError(
                f"unexpected national curve case: {case!r}"
            )
        _require_close(
            fraction,
            expected_fraction,
            f"curve {stream}/{case} fraction",
            abs_tol=1e-12,
        )
        lhv = _finite_nonnegative(
            record["lhv_gj_per_odt"], f"curve {stream}/{case} LHV"
        )
        _require_close(lhv, EXPECTED_LHV_GJ_PER_ODT, "national curve LHV")
        odt = _finite_nonnegative(record["residue_odt"], f"curve {stream}/{case} odt")
        pj = _finite_nonnegative(record["residue_pj"], f"curve {stream}/{case} pj")
        _require_close(
            pj,
            odt * lhv / PJ_GJ,
            f"curve {stream}/{case} PJ conversion",
            abs_tol=1e-6,
        )
        if _parse_bool(record["model_estimate"], "curve model_estimate") is not True:
            raise ForestryOsecanScenariosError(
                f"curve {stream}/{case}: model_estimate is not true"
            )
        if _parse_bool(record["observed"], "curve observed"):
            raise ForestryOsecanScenariosError(
                f"curve {stream}/{case}: observed is true"
            )
        if _parse_bool(record["policy_grade"], "curve policy_grade"):
            raise ForestryOsecanScenariosError(
                f"curve {stream}/{case}: policy_grade is true"
            )
        key = (stream, case)
        if key in cases:
            raise ForestryOsecanScenariosError(f"duplicate national curve row: {key}")
        cases[key] = CurveCase(
            stream=stream,
            availability_case=case,
            availability_fraction=fraction,
            odt=odt,
            pj=pj,
        )
    expected_keys = {
        *((PROCESS_STREAM, case) for case, _ in PROCESS_CASES),
        *((HARVEST_STREAM, case) for case, _ in HARVEST_CASES),
    }
    if set(cases) != expected_keys:
        raise ForestryOsecanScenariosError(
            "national curve does not contain the seven expected cases"
        )
    return cases


def _parse_bc_zonal_limits() -> dict[str, float]:
    frame = _read_csv(
        BC_ZONAL_LIMITS,
        ["REGION", "TECHNOLOGY", "YEAR", "VALUE"],
        "BC zonal activity limits",
    )
    selected = frame[
        (frame["REGION"].astype(str).str.strip() == "CAN")
        & (frame["YEAR"].astype(str).str.strip() == "2050")
        & (
            frame["TECHNOLOGY"]
            .astype(str)
            .str.strip()
            .isin({B1_LIMIT_TECHNOLOGY, B2_LIMIT_TECHNOLOGY})
        )
    ]
    limits = {
        str(record["TECHNOLOGY"]).strip(): _finite_nonnegative(
            record["VALUE"], f"BC zonal limit {record['TECHNOLOGY']}"
        )
        for _, record in selected.iterrows()
    }
    if set(limits) != {B1_LIMIT_TECHNOLOGY, B2_LIMIT_TECHNOLOGY}:
        raise ForestryOsecanScenariosError(
            "BC zonal limits lack SUPBCWB1 or SUPBCWB2 for 2050"
        )
    _require_close(
        math.fsum(limits.values()),
        43.523816,
        "BC zonal limit sum",
        abs_tol=1e-6,
    )
    return limits


def _technology_suggestion(zone_id: str) -> tuple[str, str, str]:
    if zone_id in BC_ZONES:
        return (
            f"SUPBCWB{zone_id[1]}",
            "zone_specific_candidate",
            "BC zonal supply technology candidate; not an operating assignment.",
        )
    return (
        REST_OF_CANADA_TIERS,
        "rest_of_canada_tier_pool_candidate",
        "Rest-of-Canada tier pool; the tiers are not zone-specific.",
    )


def _zone_mapping_status(
    weights: Sequence[v2.ZoneWeight],
    zones: Mapping[str, v1.Zone],
    weights_by_province: Mapping[str, tuple[v2.ZoneWeight, ...]],
) -> str:
    if len(weights) > 1:
        return "allocated_multi_zone_province"
    zone_id = weights[0].zone_id
    members = [
        province
        for province, candidate_weights in weights_by_province.items()
        if any(candidate.zone_id == zone_id for candidate in candidate_weights)
    ]
    return "mapped_aggregate_zone" if len(members) > 1 else "mapped_single_zone"


def _build_residue_rows(
    definitions: Mapping[str, ScenarioDefinition],
    province_rows: Mapping[tuple[str, str], ProvinceResidue],
    weights_by_province: Mapping[str, tuple[v2.ZoneWeight, ...]],
    zones: Mapping[str, v1.Zone],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for scenario in SCENARIO_ORDER:
        definition = definitions[scenario]
        for stream, cases in (
            (PROCESS_STREAM, PROCESS_CASES),
            (HARVEST_STREAM, HARVEST_CASES),
        ):
            for case, fraction in cases:
                for province in PROVINCE_ORDER:
                    item = province_rows[(scenario, province)]
                    if stream == PROCESS_STREAM:
                        base_odt = item.process_odt
                        base_pj = item.process_pj
                        residue_class = PROCESS_CLASS_TOTAL
                        caveat = PROCESS_SCENARIO_CAVEAT
                    else:
                        base_odt = item.harvest_gross_odt
                        base_pj = item.harvest_gross_pj
                        residue_class = HARVEST_CLASS_ALL
                        caveat = HARVEST_SCENARIO_CAVEAT
                    total_odt = base_odt * fraction
                    total_pj = base_pj * fraction
                    weights = weights_by_province[province]
                    odt_parts = v2._allocate_total(total_odt, weights)
                    pj_parts = v2._allocate_total(total_pj, weights)
                    status = _zone_mapping_status(weights, zones, weights_by_province)
                    for weight, zone_odt, zone_pj in zip(weights, odt_parts, pj_parts):
                        technology, mapping_kind, mapping_note = _technology_suggestion(
                            weight.zone_id
                        )
                        rows.append(
                            {
                                "scenario": scenario,
                                "harvest_case_m3": definition.harvest_m3,
                                "stream": stream,
                                "residue_class": residue_class,
                                "availability_case": case,
                                "availability_fraction": fraction,
                                "province": province,
                                "province_name": item.province_name,
                                "zone_id": weight.zone_id,
                                "zone_name": weight.zone_name,
                                "zone_mapped": LABEL_TRUE,
                                "zone_mapping_status": status,
                                "zone_weight": weight.weight,
                                "zone_weight_method": weight.method,
                                "zone_evidence_tier": weight.evidence_tier,
                                "proxy_share": (
                                    LABEL_TRUE if weight.proxy_share else LABEL_FALSE
                                ),
                                "allocation_status": (
                                    "proxy_share"
                                    if weight.proxy_share
                                    else "single_zone"
                                ),
                                "odt": zone_odt,
                                "pj": zone_pj,
                                "lhv_gj_per_odt": EXPECTED_LHV_GJ_PER_ODT,
                                "supbcw_technology_suggestion": technology,
                                "supbcw_mapping": mapping_kind,
                                "supbcw_mapping_note": mapping_note,
                                "evidence_tier": SCENARIO_EVIDENCE_TIER,
                                "model_estimate": LABEL_TRUE,
                                "observed": LABEL_FALSE,
                                "policy_grade": LABEL_FALSE,
                                "official_osecan_changed": LABEL_FALSE,
                                "caveat": caveat,
                                "notes": (
                                    f"{weight.note} Scenario base "
                                    f"{stream} residue is multiplied by "
                                    f"availability fraction {fraction:.2f}."
                                ),
                            }
                        )
    return rows


def _case_totals(
    residue_rows: Sequence[Mapping[str, Any]],
) -> dict[tuple[str, str, str], tuple[float, float]]:
    totals: dict[tuple[str, str, str], tuple[float, float]] = {}
    for scenario in SCENARIO_ORDER:
        for stream, cases in (
            (PROCESS_STREAM, PROCESS_CASES),
            (HARVEST_STREAM, HARVEST_CASES),
        ):
            for case, _ in cases:
                selected = [
                    row
                    for row in residue_rows
                    if row["scenario"] == scenario
                    and row["stream"] == stream
                    and row["availability_case"] == case
                ]
                totals[(scenario, stream, case)] = (
                    math.fsum(float(row["odt"]) for row in selected),
                    math.fsum(float(row["pj"]) for row in selected),
                )
    return totals


def _build_zone_summary_rows(
    residue_rows: Sequence[Mapping[str, Any]],
    weights_by_province: Mapping[str, tuple[v2.ZoneWeight, ...]],
    zones: Mapping[str, v1.Zone],
    tiers: Mapping[str, v1.Tier],
) -> list[dict[str, Any]]:
    totals = _case_totals(residue_rows)
    rows: list[dict[str, Any]] = []
    for scenario in SCENARIO_ORDER:
        for zone_id in ZONE_ORDER:
            members = [
                province
                for province in PROVINCE_ORDER
                if any(
                    weight.zone_id == zone_id
                    for weight in weights_by_province[province]
                )
            ]
            zone_weights = [
                weight
                for province in members
                for weight in weights_by_province[province]
                if weight.zone_id == zone_id
            ]
            methods = sorted({weight.method for weight in zone_weights})
            evidence_tiers = sorted({weight.evidence_tier for weight in zone_weights})
            proxy_share = any(weight.proxy_share for weight in zone_weights)
            technology, mapping_kind, _ = _technology_suggestion(zone_id)
            for stream, cases in (
                (PROCESS_STREAM, PROCESS_CASES),
                (HARVEST_STREAM, HARVEST_CASES),
            ):
                for case, fraction in cases:
                    selected = [
                        row
                        for row in residue_rows
                        if row["scenario"] == scenario
                        and row["zone_id"] == zone_id
                        and row["stream"] == stream
                        and row["availability_case"] == case
                    ]
                    if len(selected) != len(members):
                        raise ForestryOsecanScenariosError(
                            f"{scenario}/{zone_id}/{stream}/{case}: expected "
                            f"{len(members)} province rows, found {len(selected)}"
                        )
                    zone_odt = math.fsum(float(row["odt"]) for row in selected)
                    zone_pj = math.fsum(float(row["pj"]) for row in selected)
                    national_odt, national_pj = totals[(scenario, stream, case)]
                    share = zone_pj / national_pj if national_pj > 0.0 else 0.0
                    tier_id = (
                        v1._tier_for_pj(national_pj, tiers) if national_pj > 0.0 else ""
                    )
                    tier_upper = (
                        v1._tier_upper(national_pj, tiers) if national_pj > 0.0 else 0.0
                    )
                    rows.append(
                        {
                            "scenario": scenario,
                            "zone_id": zone_id,
                            "zone_name": zones[zone_id].zone_name,
                            "stream": stream,
                            "residue_class": (
                                PROCESS_CLASS_TOTAL
                                if stream == PROCESS_STREAM
                                else HARVEST_CLASS_ALL
                            ),
                            "availability_case": case,
                            "availability_fraction": fraction,
                            "provinces_included": ";".join(members),
                            "province_count": len(members),
                            "odt": zone_odt,
                            "pj": zone_pj,
                            "national_scenario_case_odt": national_odt,
                            "national_scenario_case_pj": national_pj,
                            "share_of_national_scenario_case": share,
                            "zone_weight_methods": "|".join(methods),
                            "zone_evidence_tiers": "|".join(evidence_tiers),
                            "proxy_share": LABEL_TRUE if proxy_share else LABEL_FALSE,
                            "supbcw_technology_suggestion": technology,
                            "supbcw_mapping": mapping_kind,
                            "supply_curve_tier_at_national_case": tier_id,
                            "supply_curve_tier_upper_pj": tier_upper,
                            "model_estimate": LABEL_TRUE,
                            "observed": LABEL_FALSE,
                            "policy_grade": LABEL_FALSE,
                            "official_osecan_changed": LABEL_FALSE,
                            "caveat": (
                                "Zone PJ is a model-estimate share of the "
                                "national scenario/case total; it is not an "
                                "observed zonal residue supply, not an "
                                "activity limit, and not policy."
                            ),
                        }
                    )
    return rows


def _build_candidate_limit_rows(
    zone_rows: Sequence[Mapping[str, Any]],
    bc_limits: Mapping[str, float],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for zone_row in zone_rows:
        zone_id = str(zone_row["zone_id"])
        pj = float(zone_row["pj"])
        technology, mapping_kind, mapping_note = _technology_suggestion(zone_id)
        limit_technology = ZONE_LIMIT_TECHNOLOGY.get(zone_id)
        official_limit_pj: float | str = ""
        official_limit_reference = "none"
        official_limit_status = "no_official_zone_specific_limit"
        ratio: float | str = ""
        headroom: float | str = ""
        if limit_technology is not None:
            official_limit_pj = bc_limits[limit_technology]
            official_limit_reference = f"{limit_technology}_2050_activity_limit"
            official_limit_status = "official_zone_specific_2050_limit"
            if float(official_limit_pj) > 0.0:
                ratio = pj / float(official_limit_pj)
                headroom = float(official_limit_pj) - pj
        rows.append(
            {
                "scenario": zone_row["scenario"],
                "stream": zone_row["stream"],
                "availability_case": zone_row["availability_case"],
                "availability_fraction": zone_row["availability_fraction"],
                "zone_id": zone_id,
                "zone_name": zone_row["zone_name"],
                "candidate_activity_limit_odt": zone_row["odt"],
                "candidate_activity_limit_pj": pj,
                "technology_suggestion": technology,
                "technology_mapping_kind": mapping_kind,
                "technology_mapping_note": mapping_note,
                "official_limit_reference": official_limit_reference,
                "official_limit_technology": limit_technology or "",
                "official_limit_pj": official_limit_pj,
                "official_limit_status": official_limit_status,
                "candidate_ratio_to_official_limit": ratio,
                "candidate_headroom_pj": headroom,
                "candidate_status": "read_only_candidate_not_applied",
                "candidate_basis": (
                    "scenario residue total x availability fraction x "
                    "handoff_v2 zone weight"
                ),
                "model_estimate": LABEL_TRUE,
                "observed": LABEL_FALSE,
                "policy_grade": LABEL_FALSE,
                "official_osecan_changed": LABEL_FALSE,
                "caveat": (
                    "Candidate activity limit for screening only. It is not "
                    "written to any OSeCAN input, not an operating constraint, "
                    "and not policy. Official limits exist only for SUPBCWB1 "
                    "and SUPBCWB2; other zones have no zone-specific official "
                    "limit in the adopted curve."
                ),
            }
        )
    return rows


def _comparison_row(
    *,
    scenario: str,
    scope: str,
    stream: str,
    case: str,
    case_odt: float,
    case_pj: float,
    case_tier: str,
    reference: str,
    kind: str,
    value: float,
    note: str,
) -> dict[str, Any]:
    if value > 0.0:
        ratio: float | str = case_pj / value
        headroom_ratio: float | str = 1.0 - ratio
        status = "positive"
    else:
        ratio = ""
        headroom_ratio = ""
        status = "zero_reference"
    return {
        "comparison_scope": scope,
        "scenario": scenario,
        "stream": stream,
        "availability_case": case,
        "case_odt": case_odt,
        "case_pj": case_pj,
        "case_tier": case_tier,
        "reference": reference,
        "reference_kind": kind,
        "reference_pj": value,
        "reference_status": status,
        "ratio": ratio,
        "headroom_pj": value - case_pj,
        "headroom_ratio": headroom_ratio,
        "reference_is_policy_target": LABEL_FALSE,
        "model_estimate": LABEL_TRUE,
        "observed": LABEL_FALSE,
        "policy_grade": LABEL_FALSE,
        "official_osecan_changed": LABEL_FALSE,
        "comparison_note": note,
    }


def _build_comparison_rows(
    zone_rows: Sequence[Mapping[str, Any]],
    tiers: Mapping[str, v1.Tier],
    context: v1.OsecanContext,
    zone_dispatch_context: Mapping[str, float],
    bc_limits: Mapping[str, float],
) -> list[dict[str, Any]]:
    totals = {
        (
            str(row["scenario"]),
            str(row["zone_id"]),
            str(row["stream"]),
            str(row["availability_case"]),
        ): row
        for row in zone_rows
    }
    national_refs = [
        (
            "national_supply_curve_t1_upper",
            "tier_cap",
            tiers["T1"].upper_pj,
            "Adopted T1 upper bound; not a policy target.",
        ),
        (
            "national_supply_curve_t2_upper",
            "tier_cap",
            tiers["T2"].upper_pj,
            "Adopted T2 upper bound; not a policy target.",
        ),
        (
            "national_supply_curve_t3_upper",
            "tier_cap",
            tiers["T3"].upper_pj,
            "Adopted T3 upper bound; not a policy target.",
        ),
        (
            "national_supply_curve_t4_upper",
            "tier_cap",
            tiers["T4"].upper_pj,
            "Adopted T4 upper bound and national supply-curve top; not a policy target.",
        ),
        (
            "osecan_2050_national_supply_context",
            "osecan_2050_context",
            context.national_2050_supply_pj,
            "2050 OSeCAN stress_119m national supply activity; context only, not a policy target.",
        ),
    ]
    bc_refs = [
        (
            "bc_zonal_supply_upper_2050",
            "bc_zonal_cap",
            context.bc_zonal_upper_pj,
            "2050 SUPBCWB1+SUPBCWB2 zonal upper bound; not a policy target.",
        ),
        (
            "osecan_2050_bc_supply_context",
            "osecan_2050_context",
            context.bc_2050_supply_pj,
            "2050 OSeCAN stress_119m BC supply activity; context only, not a policy target.",
        ),
    ]

    rows: list[dict[str, Any]] = []
    for scenario in SCENARIO_ORDER:
        for stream, cases in (
            (PROCESS_STREAM, PROCESS_CASES),
            (HARVEST_STREAM, HARVEST_CASES),
        ):
            for case, _ in cases:
                national_row = totals[(scenario, "B1", stream, case)]
                national_pj = float(national_row["national_scenario_case_pj"])
                national_odt = float(national_row["national_scenario_case_odt"])
                case_tier = (
                    v1._tier_for_pj(national_pj, tiers) if national_pj > 0.0 else ""
                )
                for reference, kind, value, note in national_refs:
                    rows.append(
                        _comparison_row(
                            scenario=scenario,
                            scope="national",
                            stream=stream,
                            case=case,
                            case_odt=national_odt,
                            case_pj=national_pj,
                            case_tier=case_tier,
                            reference=reference,
                            kind=kind,
                            value=value,
                            note=note,
                        )
                    )

                bc_pj = math.fsum(
                    float(totals[(scenario, zone_id, stream, case)]["pj"])
                    for zone_id in BC_ZONES
                )
                bc_odt = math.fsum(
                    float(totals[(scenario, zone_id, stream, case)]["odt"])
                    for zone_id in BC_ZONES
                )
                for reference, kind, value, note in bc_refs:
                    rows.append(
                        _comparison_row(
                            scenario=scenario,
                            scope="BC",
                            stream=stream,
                            case=case,
                            case_odt=bc_odt,
                            case_pj=bc_pj,
                            case_tier="",
                            reference=reference,
                            kind=kind,
                            value=value,
                            note=note,
                        )
                    )

                for zone_id in ZONE_ORDER:
                    zone_row = totals[(scenario, zone_id, stream, case)]
                    zone_pj = float(zone_row["pj"])
                    zone_odt = float(zone_row["odt"])
                    if zone_id in ZONE_LIMIT_TECHNOLOGY:
                        technology = ZONE_LIMIT_TECHNOLOGY[zone_id]
                        rows.append(
                            _comparison_row(
                                scenario=scenario,
                                scope=zone_id,
                                stream=stream,
                                case=case,
                                case_odt=zone_odt,
                                case_pj=zone_pj,
                                case_tier="",
                                reference=f"bc_zonal_limit_{technology.lower()}_2050",
                                kind="zone_specific_activity_limit",
                                value=bc_limits[technology],
                                note=(
                                    f"2050 {technology} activity limit; not a "
                                    "policy target."
                                ),
                            )
                        )
                    rows.append(
                        _comparison_row(
                            scenario=scenario,
                            scope=zone_id,
                            stream=stream,
                            case=case,
                            case_odt=zone_odt,
                            case_pj=zone_pj,
                            case_tier="",
                            reference="osecan_2050_zone_dispatch_context",
                            kind="osecan_2050_zone_context",
                            value=zone_dispatch_context[zone_id],
                            note=(
                                "2050 OSeCAN stress_119m zone energy dispatch "
                                "activity; context only, not a policy target."
                            ),
                        )
                    )
    return rows


def _check(checks: list[dict[str, Any]], name: str, passed: bool, detail: str) -> None:
    checks.append({"check": name, "passed": bool(passed), "detail": detail})


def _labels_ok(row: Mapping[str, Any]) -> bool:
    return (
        str(row["model_estimate"]) == LABEL_TRUE
        and str(row["observed"]) == LABEL_FALSE
        and str(row["policy_grade"]) == LABEL_FALSE
        and str(row["official_osecan_changed"]) == LABEL_FALSE
    )


def _validate(
    *,
    definitions: Mapping[str, ScenarioDefinition],
    province_rows: Mapping[tuple[str, str], ProvinceResidue],
    weights_by_province: Mapping[str, tuple[v2.ZoneWeight, ...]],
    curve_cases: Mapping[tuple[str, str], CurveCase],
    residue_rows: Sequence[Mapping[str, Any]],
    zone_rows: Sequence[Mapping[str, Any]],
    candidate_rows: Sequence[Mapping[str, Any]],
    comparison_rows: Sequence[Mapping[str, Any]],
    tiers: Mapping[str, v1.Tier],
    context: v1.OsecanContext,
    zone_dispatch_context: Mapping[str, float],
    bc_limits: Mapping[str, float],
    osecan_tree_before: str,
    osecan_tree_after: str,
    osecan_changed_files: Sequence[str],
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []

    _check(
        checks,
        "scenario_order",
        list(definitions) == list(SCENARIO_ORDER)
        and all(definition.harvest_m3 > 0.0 for definition in definitions.values()),
        "scenario definitions are ordered 100m/119m/150m with positive harvest",
    )
    _check(
        checks,
        "scenario_national_ordering",
        (
            definitions["100m"].process_pj
            < definitions["119m"].process_pj
            < definitions["150m"].process_pj
            and definitions["100m"].harvest_gross_pj
            < definitions["119m"].harvest_gross_pj
            < definitions["150m"].harvest_gross_pj
        ),
        "national process and harvest gross PJ increase across scenarios",
    )
    province_monotonic = all(
        province_rows[("100m", province)].process_pj
        <= province_rows[("119m", province)].process_pj + 1e-6
        <= province_rows[("150m", province)].process_pj + 2e-6
        and province_rows[("100m", province)].harvest_gross_pj
        <= province_rows[("119m", province)].harvest_gross_pj + 1e-6
        <= province_rows[("150m", province)].harvest_gross_pj + 2e-6
        for province in PROVINCE_ORDER
    )
    _check(
        checks,
        "scenario_province_nondecreasing",
        province_monotonic,
        "province process and harvest gross PJ are non-decreasing across scenarios",
    )
    _check(
        checks,
        "zone_coverage",
        {
            weight.zone_id
            for weights in weights_by_province.values()
            for weight in weights
        }
        == set(ZONE_ORDER),
        "all 13 OSeCAN zones receive at least one province allocation",
    )
    _check(
        checks,
        "province_zone_weight_sum",
        all(
            abs(math.fsum(weight.weight for weight in weights) - 1.0) <= 1e-12
            for weights in weights_by_province.values()
        ),
        "every province zone weight sums to 1",
    )
    _check(
        checks,
        "weight_row_count",
        sum(len(weights) for weights in weights_by_province.values())
        == EXPECTED_ZONE_WEIGHT_ROWS,
        f"{EXPECTED_ZONE_WEIGHT_ROWS} province/zone weight rows",
    )
    _check(
        checks,
        "no_unmapped",
        all(
            str(row["zone_id"]).strip()
            and str(row["zone_id"]).strip().upper() != UNMAPPED_ZONE
            for row in residue_rows
        ),
        "no empty or UNMAPPED zone id",
    )
    _check(
        checks,
        "residue_row_count",
        len(residue_rows) == EXPECTED_RESIDUE_ROWS,
        f"{len(residue_rows)} residue rows (expected {EXPECTED_RESIDUE_ROWS})",
    )
    _check(
        checks,
        "residue_row_uniqueness",
        len(
            {
                (
                    row["scenario"],
                    row["stream"],
                    row["availability_case"],
                    row["province"],
                    row["zone_id"],
                )
                for row in residue_rows
            }
        )
        == len(residue_rows),
        "scenario/stream/case/province/zone rows are unique",
    )
    _check(
        checks,
        "residue_labels",
        all(_labels_ok(row) for row in residue_rows),
        "every residue row carries the four required labels",
    )

    case_totals = _case_totals(residue_rows)
    conversion_ok = all(
        abs(float(row["pj"]) - float(row["odt"]) * EXPECTED_LHV_GJ_PER_ODT / PJ_GJ)
        <= 1e-6
        for row in residue_rows
    )
    _check(
        checks,
        "pj_conversion",
        conversion_ok,
        "every row satisfies pj = odt * 18.5 / 1e6 within 1e-6",
    )
    zero_ok = all(
        float(row["odt"]) == 0.0 and float(row["pj"]) == 0.0
        for row in residue_rows
        if row["availability_case"] == "process_availability_000"
    )
    _check(
        checks,
        "zero_process_case",
        zero_ok,
        "process_availability_000 rows are exactly zero",
    )
    conservation_ok = True
    conservation_detail = "scenario/stream/case totals conserved"
    for scenario in SCENARIO_ORDER:
        definition = definitions[scenario]
        for stream, cases in (
            (PROCESS_STREAM, PROCESS_CASES),
            (HARVEST_STREAM, HARVEST_CASES),
        ):
            base_pj = (
                definition.process_pj
                if stream == PROCESS_STREAM
                else definition.harvest_gross_pj
            )
            base_odt = (
                definition.process_odt
                if stream == PROCESS_STREAM
                else definition.harvest_gross_odt
            )
            for case, fraction in cases:
                total_odt, total_pj = case_totals[(scenario, stream, case)]
                if (
                    abs(total_pj - base_pj * fraction) > 1e-5
                    or abs(total_odt - base_odt * fraction) > 1e-3
                ):
                    conservation_ok = False
                    conservation_detail = (
                        f"conservation failed for {scenario}/{stream}/{case}: "
                        f"{total_pj} vs {base_pj * fraction}"
                    )
    _check(checks, "scenario_conservation", conservation_ok, conservation_detail)

    zone_conservation_ok = True
    for scenario in SCENARIO_ORDER:
        for stream, cases in (
            (PROCESS_STREAM, PROCESS_CASES),
            (HARVEST_STREAM, HARVEST_CASES),
        ):
            for case, _ in cases:
                selected = [
                    row
                    for row in zone_rows
                    if row["scenario"] == scenario
                    and row["stream"] == stream
                    and row["availability_case"] == case
                ]
                if len(selected) != len(ZONE_ORDER):
                    zone_conservation_ok = False
                    continue
                zone_pj = math.fsum(float(row["pj"]) for row in selected)
                national_pj = case_totals[(scenario, stream, case)][1]
                if abs(zone_pj - national_pj) > 1e-5:
                    zone_conservation_ok = False
    _check(
        checks,
        "zone_summary_conservation",
        zone_conservation_ok,
        "zone summary sums match scenario/case totals",
    )
    _check(
        checks,
        "zone_summary_row_count",
        len(zone_rows) == EXPECTED_ZONE_SUMMARY_ROWS,
        f"{len(zone_rows)} zone summary rows",
    )
    _check(
        checks,
        "candidate_row_count",
        len(candidate_rows) == EXPECTED_CANDIDATE_ROWS,
        f"{len(candidate_rows)} candidate rows",
    )
    candidate_conservation_ok = True
    for scenario in SCENARIO_ORDER:
        for stream, cases in (
            (PROCESS_STREAM, PROCESS_CASES),
            (HARVEST_STREAM, HARVEST_CASES),
        ):
            for case, _ in cases:
                candidate_pj = math.fsum(
                    float(row["candidate_activity_limit_pj"])
                    for row in candidate_rows
                    if row["scenario"] == scenario
                    and row["stream"] == stream
                    and row["availability_case"] == case
                )
                if abs(candidate_pj - case_totals[(scenario, stream, case)][1]) > 1e-5:
                    candidate_conservation_ok = False
    _check(
        checks,
        "candidate_conservation",
        candidate_conservation_ok,
        "candidate activity limits sum to scenario/case totals",
    )
    _check(
        checks,
        "candidate_labels",
        all(_labels_ok(row) for row in candidate_rows)
        and all(
            row["candidate_status"] == "read_only_candidate_not_applied"
            for row in candidate_rows
        ),
        "candidate rows are labelled read-only and not applied",
    )

    curve_119_ok = True
    curve_119_detail = "119m reproduces the national residue curve at all cases"
    for stream, cases in (
        (PROCESS_STREAM, PROCESS_CASES),
        (HARVEST_STREAM, HARVEST_CASES),
    ):
        for case, _ in cases:
            curve = curve_cases[(stream, case)]
            total_odt, total_pj = case_totals[("119m", stream, case)]
            if abs(total_pj - curve.pj) > 1e-5 or abs(total_odt - curve.odt) > 1e-3:
                curve_119_ok = False
                curve_119_detail = (
                    f"119m {stream}/{case} differs from curve: {total_pj} vs {curve.pj}"
                )
    _check(checks, "curve_119_reconciliation", curve_119_ok, curve_119_detail)

    _check(
        checks,
        "comparison_row_count",
        len(comparison_rows) == EXPECTED_COMPARISON_ROWS,
        f"{len(comparison_rows)} comparison rows",
    )
    comparison_arithmetic_ok = True
    for row in comparison_rows:
        if row["reference_status"] == "positive":
            expected_ratio = float(row["case_pj"]) / float(row["reference_pj"])
            if abs(float(row["ratio"]) - expected_ratio) > 1e-12:
                comparison_arithmetic_ok = False
            if (
                abs(
                    float(row["headroom_pj"])
                    - (float(row["reference_pj"]) - float(row["case_pj"]))
                )
                > 1e-9
            ):
                comparison_arithmetic_ok = False
        if str(row["reference_is_policy_target"]) != LABEL_FALSE:
            comparison_arithmetic_ok = False
    _check(
        checks,
        "comparison_arithmetic",
        comparison_arithmetic_ok,
        "ratio and headroom arithmetic is consistent and not policy",
    )
    _check(
        checks,
        "comparison_labels",
        all(_labels_ok(row) for row in comparison_rows),
        "every comparison row carries the four required labels",
    )
    _check(
        checks,
        "tier_references",
        abs(tiers["T4"].upper_pj - 213.538041) <= 1e-6
        and abs(context.national_2050_supply_pj - 152.92277) <= 1e-5
        and abs(context.bc_2050_supply_pj - 43.52377) <= 1e-5
        and abs(context.bc_zonal_upper_pj - 43.523816) <= 1e-6,
        "adopted tier and 2050 context references match the official curve",
    )
    _check(
        checks,
        "bc_zonal_limits",
        abs(bc_limits[B1_LIMIT_TECHNOLOGY] - 3.605474907) <= 1e-9
        and abs(bc_limits[B2_LIMIT_TECHNOLOGY] - 39.918341125) <= 1e-9,
        "SUPBCWB1/SUPBCWB2 2050 activity limits are read exactly",
    )
    _check(
        checks,
        "zone_dispatch_context",
        set(zone_dispatch_context) == set(ZONE_ORDER)
        and all(value >= 0.0 for value in zone_dispatch_context.values()),
        "2050 zone dispatch context covers all 13 zones",
    )
    _check(
        checks,
        "labels_summary_contract",
        all(
            str(row["model_estimate"]) == LABEL_TRUE
            and str(row["observed"]) == LABEL_FALSE
            and str(row["policy_grade"]) == LABEL_FALSE
            and str(row["official_osecan_changed"]) == LABEL_FALSE
            for row in (*residue_rows, *zone_rows, *candidate_rows, *comparison_rows)
        ),
        "all output families carry model_estimate/observed/policy_grade/"
        "official_osecan_changed labels",
    )
    _check(
        checks,
        "official_osecan_unchanged",
        osecan_tree_before == osecan_tree_after and not osecan_changed_files,
        "official OSeCAN tree hash is unchanged",
    )
    _check(
        checks,
        "output_outside_osecan",
        OSECAN_DIR not in OUTPUT_DIR.parents and OUTPUT_DIR != OSECAN_DIR,
        "artifact output directory is outside the official OSeCAN tree",
    )
    _check(
        checks,
        "nonnegative_values",
        all(
            float(row["odt"]) >= 0.0 and float(row["pj"]) >= 0.0
            for row in (*residue_rows, *zone_rows)
        )
        and all(
            float(row["candidate_activity_limit_odt"]) >= 0.0
            and float(row["candidate_activity_limit_pj"]) >= 0.0
            for row in candidate_rows
        ),
        "residue, zone, and candidate values are non-negative",
    )
    return checks


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
    residue_rows: Sequence[Mapping[str, Any]],
    zone_rows: Sequence[Mapping[str, Any]],
    candidate_rows: Sequence[Mapping[str, Any]],
    comparison_rows: Sequence[Mapping[str, Any]],
) -> str:
    process_cases = [case for case, _ in PROCESS_CASES]
    harvest_cases = [case for case, _ in HARVEST_CASES]

    lines: list[str] = []
    lines.append(f"# {ARTIFACT_TITLE}")
    lines.append("")
    lines.append(
        f"Status: **{summary['status']}**. Artifact version "
        f"`{summary['artifact_version']}`; generated {summary['generated_utc']}."
    )
    lines.append("")
    lines.append(
        "This is a read-only, model-estimate screening handoff. Every row is "
        "`model_estimate=true`, `observed=false`, `policy_grade=false`, and "
        "`official_osecan_changed=false`. No official OSeCAN input is written "
        "or modified."
    )
    lines.append("")
    lines.append("## Method")
    lines.append("")
    lines.append(
        "1. Read province process residue (sum of facility classes) and gross "
        f"harvest residue from `{CHAIN_LABEL}`."
    )
    lines.append(
        "2. Apply the seven availability/recovery fractions from "
        f"`{CURVE_LABEL}` to each scenario total."
    )
    lines.append(
        "3. Split each province/case PJ total across all 13 OSeCAN zones with "
        f"the published `{HANDOFF_LABEL}` weights; the floating "
        "residual is assigned to the last zone so province totals are exactly "
        "conserved."
    )
    lines.append(
        "4. Compare national, BC, and zone totals with the adopted SUPBCW "
        "tier caps, the SUPBCWB1/SUPBCWB2 2050 zonal activity limits, and the "
        "2050 OSeCAN stress_119m context. These references are context, not "
        "policy targets."
    )
    lines.append(
        "5. Emit a candidate activity-limit table for screening only. It is "
        "not applied to OSeCAN."
    )
    lines.append("")

    lines.append("## National scenario/case PJ")
    lines.append("")
    lines.append("| Scenario | Stream | Availability case | PJ | ODT |")
    lines.append("|---|---|---|---|---|")
    for scenario in SCENARIO_ORDER:
        for stream, cases in (
            (PROCESS_STREAM, process_cases),
            (HARVEST_STREAM, harvest_cases),
        ):
            for case in cases:
                entry = summary["national_case_totals"][scenario][stream][case]
                lines.append(
                    f"| {scenario} | {stream} | {case} | "
                    f"{_format_number(float(entry['pj']), 2)} | "
                    f"{_format_number(float(entry['odt']), 0)} |"
                )
    lines.append("")
    lines.append(
        "Process and harvest streams are separate and are never summed. The "
        f"119m rows reproduce `{CURVE_LABEL}` exactly at the "
        "national level."
    )
    lines.append("")

    lines.append("## Zone summary at the primary 119m scenario")
    lines.append("")
    lines.append(
        "| Zone | Zone name | Process 50% PJ | Process share | Harvest 60% PJ | "
        "Harvest share | Technology suggestion |"
    )
    lines.append("|---|---|---|---|---|---|---|")

    def _zone_value(zone_id: str, stream: str, case: str) -> Mapping[str, Any]:
        return next(
            row
            for row in zone_rows
            if row["scenario"] == PRIMARY_SCENARIO
            and row["zone_id"] == zone_id
            and row["stream"] == stream
            and row["availability_case"] == case
        )

    for zone_id in ZONE_ORDER:
        process_row = _zone_value(zone_id, PROCESS_STREAM, "process_availability_050")
        harvest_row = _zone_value(zone_id, HARVEST_STREAM, "harvest_recovery_060")
        lines.append(
            f"| {zone_id} | {process_row['zone_name']} | "
            f"{_format_number(float(process_row['pj']), 2)} | "
            f"{float(process_row['share_of_national_scenario_case']) * 100:.1f}% | "
            f"{_format_number(float(harvest_row['pj']), 2)} | "
            f"{float(harvest_row['share_of_national_scenario_case']) * 100:.1f}% | "
            f"{process_row['supbcw_technology_suggestion']} |"
        )
    lines.append("")

    lines.append("## Candidate activity-limit table")
    lines.append("")
    lines.append(
        "`candidate_activity_limits.csv` is read-only and not applied. Grain: "
        "scenario x stream x availability case x zone. Columns include "
        "`candidate_activity_limit_pj`, `candidate_activity_limit_odt`, "
        "`technology_suggestion`, `official_limit_reference`, "
        "`official_limit_pj`, `candidate_ratio_to_official_limit`, and "
        "`candidate_headroom_pj`. `candidate_status` is always "
        "`read_only_candidate_not_applied`."
    )
    lines.append("")
    lines.append(
        "| Zone | Scenario | Stream | Case | Candidate PJ | Technology suggestion |"
    )
    lines.append("|---|---|---|---|---|---|")
    excerpt = [
        row
        for row in candidate_rows
        if row["scenario"] == PRIMARY_SCENARIO
        and (
            (
                row["stream"] == PROCESS_STREAM
                and row["availability_case"] == "process_availability_050"
            )
            or (
                row["stream"] == HARVEST_STREAM
                and row["availability_case"] == "harvest_recovery_060"
            )
        )
    ]
    for row in excerpt:
        lines.append(
            f"| {row['zone_id']} | {row['scenario']} | {row['stream']} | "
            f"{row['availability_case']} | "
            f"{_format_number(float(row['candidate_activity_limit_pj']), 2)} | "
            f"{row['technology_suggestion']} |"
        )
    lines.append("")

    lines.append("## Supply-curve and 2050 context comparison")
    lines.append("")
    lines.append(
        "Ratios above 1.0 mean the scenario/case PJ exceeds the reference; "
        "negative headroom is the excess. References are context only."
    )
    lines.append("")
    lines.append(
        "| Scenario | Stream | Case | National PJ | Tier | T4 ratio | "
        "2050 national ratio | BC PJ | BC zonal ratio | BC 2050 ratio |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    comparison_index = {
        (
            row["scenario"],
            row["stream"],
            row["availability_case"],
            row["reference"],
        ): row
        for row in comparison_rows
    }
    for scenario in SCENARIO_ORDER:
        for stream, cases in (
            (PROCESS_STREAM, process_cases),
            (HARVEST_STREAM, harvest_cases),
        ):
            for case in cases:
                t4 = comparison_index[
                    (scenario, stream, case, "national_supply_curve_t4_upper")
                ]
                national_context = comparison_index[
                    (
                        scenario,
                        stream,
                        case,
                        "osecan_2050_national_supply_context",
                    )
                ]
                bc_zonal = comparison_index[
                    (scenario, stream, case, "bc_zonal_supply_upper_2050")
                ]
                bc_context = comparison_index[
                    (scenario, stream, case, "osecan_2050_bc_supply_context")
                ]
                lines.append(
                    f"| {scenario} | {stream} | {case} | "
                    f"{_format_number(float(t4['case_pj']), 2)} | "
                    f"{t4['case_tier'] or '-'} | "
                    f"{float(t4['ratio']):.3f} | "
                    f"{float(national_context['ratio']):.3f} | "
                    f"{_format_number(float(bc_zonal['case_pj']), 2)} | "
                    f"{float(bc_zonal['ratio']):.3f} | "
                    f"{float(bc_context['ratio']):.3f} |"
                )
    lines.append("")

    lines.append("## Caveats")
    lines.append("")
    for caveat in summary["caveats"]:
        lines.append(f"- {caveat}")
    lines.append("")

    lines.append("## Reproduction and verification")
    lines.append("")
    lines.append("```bash")
    lines.append(f".venv/bin/python -m {MODULE_PATH}")
    lines.append(f".venv/bin/python -m pytest {TEST_PATH} -q")
    lines.append("```")
    lines.append("")
    lines.append(
        f"Validation: {summary['validation']['passed']}; failed checks: "
        f"{summary['validation']['failed_check_count']}. OSeCAN tree hash "
        f"unchanged: {summary['osecan_integrity']['unchanged']}."
    )
    lines.append("")
    return "\n".join(lines)


def build(output_dir: Path | None = None) -> dict[str, Any]:
    """Build the multi-scenario forestry residue handoff."""
    target_dir = Path(output_dir) if output_dir is not None else OUTPUT_DIR
    if OSECAN_DIR in target_dir.resolve().parents or target_dir.resolve() == OSECAN_DIR:
        raise ForestryOsecanScenariosError(
            "refusing to write inside the official OSeCAN tree"
        )
    generated_utc = datetime.now(UTC).isoformat(timespec="seconds")
    assert_no_unexpected_deletions()
    osecan_hashes_before, osecan_tree_before = _hash_osecan_tree()

    zones, _province_zones = v1._parse_zone_crosswalk()
    definitions, province_rows = _parse_scenario_inputs()
    weights_by_province = _parse_zone_weights(zones)
    dispatch = v2._parse_dispatch_evidence()
    zone_dispatch_context = _verify_weights_against_dispatch(
        weights_by_province, dispatch
    )
    curve_cases = _parse_national_curve()
    tiers = v1._parse_tiers()
    context = v1._parse_osecan_context(tiers)
    bc_limits = _parse_bc_zonal_limits()

    residue_rows = _build_residue_rows(
        definitions, province_rows, weights_by_province, zones
    )
    zone_rows = _build_zone_summary_rows(
        residue_rows, weights_by_province, zones, tiers
    )
    candidate_rows = _build_candidate_limit_rows(zone_rows, bc_limits)
    comparison_rows = _build_comparison_rows(
        zone_rows, tiers, context, zone_dispatch_context, bc_limits
    )

    osecan_hashes_after, osecan_tree_after = _hash_osecan_tree()
    changed_files = sorted(
        path
        for path in set(osecan_hashes_before) | set(osecan_hashes_after)
        if osecan_hashes_before.get(path) != osecan_hashes_after.get(path)
    )
    checks = _validate(
        definitions=definitions,
        province_rows=province_rows,
        weights_by_province=weights_by_province,
        curve_cases=curve_cases,
        residue_rows=residue_rows,
        zone_rows=zone_rows,
        candidate_rows=candidate_rows,
        comparison_rows=comparison_rows,
        tiers=tiers,
        context=context,
        zone_dispatch_context=zone_dispatch_context,
        bc_limits=bc_limits,
        osecan_tree_before=osecan_tree_before,
        osecan_tree_after=osecan_tree_after,
        osecan_changed_files=changed_files,
    )
    failed = sum(1 for check in checks if not check["passed"])
    if failed:
        raise ForestryOsecanScenariosError(
            f"{failed} validation checks failed: "
            + "; ".join(check["check"] for check in checks if not check["passed"])
        )
    if changed_files:
        raise ForestryOsecanScenariosError(
            "official OSeCAN files changed during the build: "
            + ", ".join(changed_files)
        )

    case_totals = _case_totals(residue_rows)
    national_case_totals: dict[str, dict[str, dict[str, dict[str, float]]]] = {}
    for scenario in SCENARIO_ORDER:
        national_case_totals[scenario] = {}
        for stream, cases in (
            (PROCESS_STREAM, PROCESS_CASES),
            (HARVEST_STREAM, HARVEST_CASES),
        ):
            national_case_totals[scenario][stream] = {}
            for case, fraction in cases:
                odt, pj = case_totals[(scenario, stream, case)]
                national_case_totals[scenario][stream][case] = {
                    "availability_fraction": fraction,
                    "odt": odt,
                    "pj": pj,
                }

    comparison_summary: dict[str, Any] = {"national": {}, "bc": {}, "zone": {}}
    for row in comparison_rows:
        key = f"{row['scenario']}|{row['stream']}|{row['availability_case']}"
        if row["comparison_scope"] == "national":
            entry = comparison_summary["national"].setdefault(key, {})
            if row["reference"] == "national_supply_curve_t4_upper":
                entry["case_pj"] = row["case_pj"]
                entry["case_tier"] = row["case_tier"]
                entry["t4_ratio"] = row["ratio"]
                entry["t4_headroom_pj"] = row["headroom_pj"]
            elif row["reference"] == "osecan_2050_national_supply_context":
                entry["osecan_2050_national_ratio"] = row["ratio"]
                entry["osecan_2050_national_headroom_pj"] = row["headroom_pj"]
        elif row["comparison_scope"] == "BC":
            entry = comparison_summary["bc"].setdefault(key, {})
            if row["reference"] == "bc_zonal_supply_upper_2050":
                entry["case_pj"] = row["case_pj"]
                entry["bc_zonal_ratio"] = row["ratio"]
                entry["bc_zonal_headroom_pj"] = row["headroom_pj"]
            elif row["reference"] == "osecan_2050_bc_supply_context":
                entry["osecan_2050_bc_ratio"] = row["ratio"]
                entry["osecan_2050_bc_headroom_pj"] = row["headroom_pj"]
        else:
            zone_id = str(row["comparison_scope"])
            entry = comparison_summary["zone"].setdefault(
                f"{key}|{zone_id}|{row['reference']}", {}
            )
            entry["case_pj"] = row["case_pj"]
            entry["reference"] = row["reference"]
            entry["reference_pj"] = row["reference_pj"]
            entry["ratio"] = row["ratio"]
            entry["headroom_pj"] = row["headroom_pj"]

    scenario_summary = {
        scenario: {
            "harvest_m3": definitions[scenario].harvest_m3,
            "process_base_odt": definitions[scenario].process_odt,
            "process_base_pj": definitions[scenario].process_pj,
            "harvest_gross_odt": definitions[scenario].harvest_gross_odt,
            "harvest_gross_pj": definitions[scenario].harvest_gross_pj,
            "harvest_recoverable_odt": definitions[scenario].harvest_recoverable_odt,
            "harvest_recoverable_pj": definitions[scenario].harvest_recoverable_pj,
            "evidence_label": definitions[scenario].evidence_label,
        }
        for scenario in SCENARIO_ORDER
    }

    zone_mapping = {
        "province_count": len(PROVINCES),
        "zone_count": len(ZONE_ORDER),
        "province_zone_allocation_rows": EXPECTED_ZONE_WEIGHT_ROWS,
        "unmapped_province_count": 0,
        "unmapped_zone_present": False,
        "zones": [
            {
                "zone_id": zone_id,
                "zone_name": zones[zone_id].zone_name,
                "provinces": list(zones[zone_id].provinces),
            }
            for zone_id in ZONE_ORDER
        ],
        "multi_zone_provinces": list(MULTI_ZONE_PROVINCES),
        "proxy_share_provinces": sorted(PROXY_SHARE_PROVINCES),
        "weight_methods": sorted(
            {
                weight.method
                for weights in weights_by_province.values()
                for weight in weights
            }
        ),
        "weight_evidence_tiers": sorted(
            {
                weight.evidence_tier
                for weights in weights_by_province.values()
                for weight in weights
            }
        ),
    }

    summary: dict[str, Any] = {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": generated_utc,
        "status": "pass",
        "labels": {
            "model_estimate": True,
            "observed": False,
            "policy_grade": False,
            "official_osecan_changed": False,
            "not_policy": True,
            "context_is_policy_target": False,
            "candidate_activity_limits_applied": False,
        },
        "scenario_order": list(SCENARIO_ORDER),
        "primary_scenario": PRIMARY_SCENARIO,
        "scenarios": scenario_summary,
        "national_case_totals": national_case_totals,
        "zone_mapping": zone_mapping,
        "candidate_activity_limits": {
            "row_count": len(candidate_rows),
            "status": "read_only_candidate_not_applied",
            "grain": ["scenario", "stream", "availability_case", "zone_id"],
            "columns": {
                "candidate_activity_limit_pj": "model-estimate zone PJ",
                "technology_suggestion": "candidate SUPBCW string, not an assignment",
                "official_limit_reference": "official 2050 limit reference or none",
                "candidate_ratio_to_official_limit": "candidate PJ / official limit",
                "candidate_headroom_pj": "official limit - candidate PJ",
            },
            "official_zone_limits": {
                "B1": {
                    "technology": B1_LIMIT_TECHNOLOGY,
                    "limit_pj": bc_limits[B1_LIMIT_TECHNOLOGY],
                },
                "B2": {
                    "technology": B2_LIMIT_TECHNOLOGY,
                    "limit_pj": bc_limits[B2_LIMIT_TECHNOLOGY],
                },
            },
            "applied_to_osecan": False,
        },
        "comparison": comparison_summary,
        "supply_curve": {
            "upper_pj": tiers["T4"].upper_pj,
            "tiers": {
                tier_id: {
                    "technology": tier.technology,
                    "lower_pj": tier.lower_pj,
                    "upper_pj": tier.upper_pj,
                    "incremental_cap_pj": tier.incremental_cap_pj,
                }
                for tier_id, tier in tiers.items()
            },
            "bc_zonal_upper_pj": context.bc_zonal_upper_pj,
            "bc_zonal_limits_pj": dict(bc_limits),
        },
        "osecan_context": {
            "run": v2.DISPATCH_RUN,
            "year": int(v2.DISPATCH_YEAR),
            "national_supply_pj": context.national_2050_supply_pj,
            "bc_supply_pj": context.bc_2050_supply_pj,
            "bc_zonal_upper_pj": context.bc_zonal_upper_pj,
            "dispatch_source": context.dispatch_source,
            "dispatch_sha256": context.dispatch_sha256,
            "manifest_status": context.manifest_status,
            "context_is_policy_target": False,
            "zone_dispatch_context_pj": dict(zone_dispatch_context),
        },
        "record_counts": {
            "residue_rows": len(residue_rows),
            "zone_summary_rows": len(zone_rows),
            "candidate_activity_limit_rows": len(candidate_rows),
            "comparison_rows": len(comparison_rows),
            "provinces": len(PROVINCES),
            "zones": len(ZONE_ORDER),
            "scenarios": len(SCENARIO_ORDER),
            "process_cases": len(PROCESS_CASES),
            "harvest_cases": len(HARVEST_CASES),
            "province_zone_allocation_rows": EXPECTED_ZONE_WEIGHT_ROWS,
        },
        "inputs": {
            str(path.relative_to(REPO_ROOT)): _sha256(path)
            for path in (
                SCENARIO_PRODUCT_RESIDUE,
                SCENARIO_SUMMARY,
                SCENARIO_SUMMARY_JSON,
                SCENARIO_MANIFEST,
                HANDOFF_V2_WEIGHTS,
                HANDOFF_V2_SUMMARY,
                HANDOFF_V2_MANIFEST,
                NATIONAL_CURVE,
                PROVINCE_CURVE,
                CURVE_SUMMARY,
                CROSSWALK,
                TIER_DEFINITIONS,
                TIER_LIMITS,
                BC_ZONAL_LIMITS,
                SUPPLY_DEFINITION,
                DISPATCH,
                NATIONAL_MANIFEST,
            )
        },
        "osecan_integrity": {
            "root": str(OSECAN_DIR.relative_to(REPO_ROOT)),
            "file_count_before": len(osecan_hashes_before),
            "file_count_after": len(osecan_hashes_after),
            "tree_sha256_before": osecan_tree_before,
            "tree_sha256_after": osecan_tree_after,
            "unchanged": (
                osecan_tree_before == osecan_tree_after and not changed_files
            ),
            "changed_files": changed_files,
        },
        "validation": {
            "checks": checks,
            "passed": failed == 0,
            "failed_check_count": failed,
        },
        "caveats": [
            "All values are model estimates; observed=false and policy_grade=false.",
            "The 100/119/150 Mm3 cases are planning allocations, not legal AACs, sustainable-supply determinations, or observed harvest outcomes.",
            f"Zone splits are explicit {HANDOFF_LABEL} proxy weights; no observed sub-provincial residue split exists.",
            "BC zone weights are proportional to the 2050 SUPBCWB1/SUPBCWB2 zonal supply limits, so B1 and B2 candidate ratios to their own limits are identical by construction.",
            "The national supply-curve tier caps are rest-of-Canada RSYC-only; national comparisons that include BC are a context screen, not a like-for-like capacity test.",
            "Process residue is facility-assigned by receiving facility province; its provincial distribution differs from the province-basis national residue curve even though the national 119m totals reconcile.",
            "Harvest residue is province-only gross residue multiplied by a recovery fraction; it is never facility-assigned.",
            "Process and harvest residue are separate streams and are never summed.",
            "Candidate activity limits are read-only screening values; they are not applied to any OSeCAN technology, region, or input file.",
            "Official zone-specific limits exist only for SUPBCWB1 and SUPBCWB2; other zones have no official zone-specific activity limit in the adopted curve.",
            "Supply-curve and 2050 OSeCAN values are context, not policy targets, and the comparison is not a capacity-adequacy or sustainability test.",
            "No UI or public OSeCAN surface is changed by this artifact.",
        ],
        "reproduce": f".venv/bin/python -m {MODULE_PATH}",
    }

    residue_fields = [
        "scenario",
        "harvest_case_m3",
        "stream",
        "residue_class",
        "availability_case",
        "availability_fraction",
        "province",
        "province_name",
        "zone_id",
        "zone_name",
        "zone_mapped",
        "zone_mapping_status",
        "zone_weight",
        "zone_weight_method",
        "zone_evidence_tier",
        "proxy_share",
        "allocation_status",
        "odt",
        "pj",
        "lhv_gj_per_odt",
        "supbcw_technology_suggestion",
        "supbcw_mapping",
        "supbcw_mapping_note",
        "evidence_tier",
        "model_estimate",
        "observed",
        "policy_grade",
        "official_osecan_changed",
        "caveat",
        "notes",
    ]
    zone_fields = [
        "scenario",
        "zone_id",
        "zone_name",
        "stream",
        "residue_class",
        "availability_case",
        "availability_fraction",
        "provinces_included",
        "province_count",
        "odt",
        "pj",
        "national_scenario_case_odt",
        "national_scenario_case_pj",
        "share_of_national_scenario_case",
        "zone_weight_methods",
        "zone_evidence_tiers",
        "proxy_share",
        "supbcw_technology_suggestion",
        "supbcw_mapping",
        "supply_curve_tier_at_national_case",
        "supply_curve_tier_upper_pj",
        "model_estimate",
        "observed",
        "policy_grade",
        "official_osecan_changed",
        "caveat",
    ]
    candidate_fields = [
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
        "candidate_basis",
        "model_estimate",
        "observed",
        "policy_grade",
        "official_osecan_changed",
        "caveat",
    ]
    comparison_fields = [
        "comparison_scope",
        "scenario",
        "stream",
        "availability_case",
        "case_odt",
        "case_pj",
        "case_tier",
        "reference",
        "reference_kind",
        "reference_pj",
        "reference_status",
        "ratio",
        "headroom_pj",
        "headroom_ratio",
        "reference_is_policy_target",
        "model_estimate",
        "observed",
        "policy_grade",
        "official_osecan_changed",
        "comparison_note",
    ]

    target_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(
        target_dir / "forestry_residue_scenarios.csv",
        residue_rows,
        residue_fields,
    )
    _write_csv(
        target_dir / "scenario_zone_summary.csv",
        zone_rows,
        zone_fields,
    )
    _write_csv(
        target_dir / "candidate_activity_limits.csv",
        candidate_rows,
        candidate_fields,
    )
    _write_csv(
        target_dir / "supply_curve_comparison.csv",
        comparison_rows,
        comparison_fields,
    )
    _write_json(target_dir / "summary.json", summary)
    (target_dir / REPORT_NAME).write_text(
        _report(summary, residue_rows, zone_rows, candidate_rows, comparison_rows),
        encoding="utf-8",
    )

    outputs: dict[str, Any] = {}
    for path in sorted(target_dir.iterdir()):
        if not path.is_file() or path.name == "manifest.json":
            continue
        outputs[path.name] = {
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }
    manifest = {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": generated_utc,
        "status": "pass",
        "labels": summary["labels"],
        "inputs": summary["inputs"],
        "outputs": outputs,
        "record_counts": summary["record_counts"],
        "scenarios": scenario_summary,
        "national_case_totals": national_case_totals,
        "zone_mapping": zone_mapping,
        "candidate_activity_limits": summary["candidate_activity_limits"],
        "supply_curve": summary["supply_curve"],
        "osecan_context": summary["osecan_context"],
        "osecan_integrity": summary["osecan_integrity"],
        "validation": summary["validation"],
        "reproduce": summary["reproduce"],
    }
    _write_json(target_dir / "manifest.json", manifest)
    summary["outputs"] = outputs

    assert_no_unexpected_deletions()
    return summary


def main() -> None:
    summary = build()
    print(
        json.dumps(
            {
                "artifact": summary["artifact"],
                "status": summary["status"],
                "scenarios": summary["scenario_order"],
                "residue_rows": summary["record_counts"]["residue_rows"],
                "candidate_rows": summary["record_counts"][
                    "candidate_activity_limit_rows"
                ],
                "validation_passed": summary["validation"]["passed"],
                "osecan_unchanged": summary["osecan_integrity"]["unchanged"],
                "output_dir": str(OUTPUT_DIR.relative_to(REPO_ROOT)),
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
