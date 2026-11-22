"""Build the all-zone forestry residue handoff to OSeCAN (v2).

Version 1 kept British Columbia, Alberta, Ontario, and Quebec in an explicit
``UNMAPPED`` bucket because the OSeCAN crosswalk publishes no sub-provincial
residue split.  Version 2 allocates those four provinces across their OSeCAN
sub-zones using an explicit, documented proxy hierarchy, while keeping every
other province on its existing crosswalk zone.

Allocation hierarchy for a multi-zone province
----------------------------------------------
1. OSeCAN zonal supply dispatch (BC ``SUPBCWB1``/``SUPBCWB2``).
2. OSeCAN zonal dispatch activity for provinces without zonal supply rows
   (AB/ON/QC ``PWRBCW<zone>``).
3. Operable-area weights where a defensible ecoprovince-to-zone mapping
   exists (BC only, because the crosswalk names B1 ecoprovinces).
4. Explicit equal split.

The selected method and evidence tier are written on every weight and handoff
row.  Proxy shares are labelled ``proxy_share=true``; they are model estimates,
not observed sub-provincial shares and not policy.

Conservation and boundary
--------------------------
* Province totals are preserved exactly by allocating each province/case PJ
  total across its zones and assigning the floating-point residual to the last
  zone.
* National totals are recomputed from the allocated rows and reconciled to the
  national residue curve.
* Every row carries ``model_estimate=true``, ``observed=false``,
  ``policy_grade=false``, and ``official_osecan_changed=false``.
* The OSeCAN tree is hashed before and after the build; hashes must match.
* Any unexpected ``D`` in parent or ``data`` repository status halts the build.
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

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_ROOT = REPO_ROOT / "data"
OUTPUT_DIR = DATA_ROOT / "derived/national/facilities/forestry_osecan_handoff_v2"

CURVE_DIR = v1.CURVE_DIR
PROVINCE_CURVE = v1.PROVINCE_CURVE
NATIONAL_CURVE = v1.NATIONAL_CURVE
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
OPERABLE_BY_ECOPROVINCE = (
    DATA_ROOT
    / "derived/national/managed_landbase/operable_proxy_v3"
    / "operable_v3_by_ecoprovince.csv"
)
RSYC_CURVE_LIBRARY = (
    DATA_ROOT
    / "derived/national/assortments/rsyc_backbone"
    / "rsyc_curve_library_tile_first.csv"
)

ARTIFACT = "forestry_osecan_handoff_v2"
ARTIFACT_VERSION = "2.0.0"
PJ_GJ = v1.PJ_GJ
EXPECTED_LHV_GJ_PER_ODT = v1.EXPECTED_LHV_GJ_PER_ODT
LABEL_TRUE = v1.LABEL_TRUE
LABEL_FALSE = v1.LABEL_FALSE
UNMAPPED_ZONE = "UNMAPPED"
DISPATCH_RUN = "stress_119m"
DISPATCH_YEAR = "2050"

PROVINCES = v1.PROVINCES
PROVINCE_ORDER = v1.PROVINCE_ORDER
ZONE_ORDER = v1.ZONE_ORDER
PROCESS_STREAM = v1.PROCESS_STREAM
HARVEST_STREAM = v1.HARVEST_STREAM
PROCESS_CASES = v1.PROCESS_CASES
HARVEST_CASES = v1.HARVEST_CASES
CASE_FRACTIONS = v1.CASE_FRACTIONS
CASE_ORDER = v1.CASE_ORDER
BC_ZONES = v1.BC_ZONES

MULTI_ZONE_PROVINCES: tuple[str, ...] = ("BC", "AB", "ON", "QC")
SINGLE_ZONE_PROVINCES: tuple[str, ...] = tuple(
    province for province in PROVINCE_ORDER if province not in MULTI_ZONE_PROVINCES
)

# The crosswalk identifies B1 as Georgia Depression + Southern Coastal
# Mountains and B2 as the remaining BC ecoprovinces.  This is the only
# province with an explicit ecoprovince-to-zone mapping in the crosswalk.
BC_B1_ECOPROVINCES: frozenset[str] = frozenset(
    {"Georgia Depression", "Southern Coastal Mountains"}
)

# A dispatch technology is evidence of zonal activity, not a harvest
# observation.  These labels keep that distinction visible on every row.
DISPATCH_METHODS: dict[str, tuple[str, str, str]] = {
    "BC": (
        "osecan_zonal_supply_dispatch",
        "tier_1_osecan_zonal_supply_model",
        "supply",
    ),
    "AB": (
        "osecan_zonal_dispatch_proxy",
        "tier_2_osecan_zonal_dispatch_model",
        "dispatch",
    ),
    "ON": (
        "osecan_zonal_dispatch_proxy",
        "tier_2_osecan_zonal_dispatch_model",
        "dispatch",
    ),
    "QC": (
        "osecan_zonal_dispatch_proxy",
        "tier_2_osecan_zonal_dispatch_model",
        "dispatch",
    ),
}

DISPATCH_TECHNOLOGY: dict[str, dict[str, str]] = {
    "BC": {"B1": "SUPBCWB1", "B2": "SUPBCWB2"},
    "AB": {"A1": "PWRBCWA1", "A2": "PWRBCWA2"},
    "ON": {"O1": "PWRBCWO1", "O2": "PWRBCWO2"},
    "QC": {"Q1": "PWRBCWQ1", "Q2": "PWRBCWQ2"},
}


class ForestryOsecanHandoffV2Error(v1.ForestryOsecanHandoffError):
    """Raised when the all-zone forestry-to-OSeCAN handoff fails a guard."""


@dataclass(frozen=True)
class ZoneWeight:
    """Trusted province-to-zone allocation weight and its provenance."""

    province: str
    zone_id: str
    zone_name: str
    weight: float
    method: str
    evidence_tier: str
    proxy_share: bool
    source: str
    source_value: float
    source_unit: str
    source_run: str
    source_year: str
    source_technology: str
    mapping_basis: str
    note: str


@dataclass(frozen=True)
class DispatchEvidence:
    """Zonal 2050 dispatch/supply values used for allocation weights."""

    weights: Mapping[str, Mapping[str, float]]
    zone_dispatch_context: Mapping[str, float]
    dispatch_sha256: str


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _finite_nonnegative(value: Any, label: str) -> float:
    return v1._finite_nonnegative(value, label)


def _require_close(
    left: float, right: float, label: str, *, abs_tol: float = v1.ABS_TOL
) -> None:
    v1._require_close(left, right, label, abs_tol=abs_tol)


def _read_csv(path: Path, required: Sequence[str], label: str) -> pd.DataFrame:
    return v1._read_csv(path, required, label)


def _parse_bool(value: Any, label: str) -> bool:
    return v1._parse_bool(value, label)


def assert_no_unexpected_deletions() -> None:
    """Fail closed on any ``D`` in parent or data repository status."""
    v1.assert_no_unexpected_deletions()


def _hash_osecan_tree() -> tuple[dict[str, str], str]:
    return v1._hash_tree(OSECAN_DIR)


def _parse_dispatch_evidence() -> DispatchEvidence:
    frame = _read_csv(
        DISPATCH,
        ["run", "technology", "category", "zone", "year", "value_pj"],
        "OSeCAN dispatch",
    )
    frame["run"] = frame["run"].astype(str).str.strip()
    frame["technology"] = frame["technology"].astype(str).str.strip().str.upper()
    frame["category"] = frame["category"].astype(str).str.strip().str.lower()
    frame["zone"] = frame["zone"].astype(str).str.strip().str.upper()
    frame["year"] = frame["year"].astype(str).str.strip()

    selected = frame[(frame["run"] == DISPATCH_RUN) & (frame["year"] == DISPATCH_YEAR)]
    if selected.empty:
        raise ForestryOsecanHandoffV2Error(
            f"OSeCAN dispatch lacks {DISPATCH_RUN}/{DISPATCH_YEAR} rows"
        )

    weights: dict[str, dict[str, float]] = {}
    for province, zone_technologies in DISPATCH_TECHNOLOGY.items():
        category = DISPATCH_METHODS[province][2]
        province_values: dict[str, float] = {}
        for zone_id, technology in zone_technologies.items():
            rows = selected[
                (selected["zone"] == zone_id)
                & (selected["technology"] == technology)
                & (selected["category"] == category)
            ]
            if len(rows) != 1:
                raise ForestryOsecanHandoffV2Error(
                    f"expected one {DISPATCH_RUN} {category} row for "
                    f"{province}/{zone_id}/{technology}, found {len(rows)}"
                )
            value = _finite_nonnegative(
                rows.iloc[0]["value_pj"],
                f"OSeCAN {province}/{zone_id}/{technology} value",
            )
            province_values[zone_id] = value
        if math.fsum(province_values.values()) <= 0.0:
            raise ForestryOsecanHandoffV2Error(
                f"OSeCAN dispatch weights for {province} are all zero"
            )
        weights[province] = province_values

    dispatch_rows = selected[selected["category"] == "dispatch"]
    zone_dispatch_context: dict[str, float] = {}
    for zone_id in ZONE_ORDER:
        values = [
            _finite_nonnegative(value, f"OSeCAN {zone_id} dispatch context")
            for value in dispatch_rows[dispatch_rows["zone"] == zone_id]["value_pj"]
        ]
        if not values:
            raise ForestryOsecanHandoffV2Error(
                f"OSeCAN dispatch context lacks zone {zone_id}"
            )
        zone_dispatch_context[zone_id] = math.fsum(values)

    return DispatchEvidence(
        weights=weights,
        zone_dispatch_context=zone_dispatch_context,
        dispatch_sha256=_sha256(DISPATCH),
    )


def _operable_area_values(province: str) -> dict[str, float] | None:
    """Return BC operable-area B1/B2 values, or ``None`` when not defensible."""
    if province != "BC":
        return None
    frame = _read_csv(
        OPERABLE_BY_ECOPROVINCE,
        [
            "province",
            "ecoprovince",
            "v2_case",
            "slope_case",
            "remaining_operable_ha",
        ],
        "operable-area proxy",
    )
    selected = frame[
        (frame["province"].astype(str).str.strip().str.upper() == "BC")
        & (frame["v2_case"].astype(str).str.strip() == "base")
        & (frame["slope_case"].astype(str).str.strip() == "none")
    ]
    if selected.empty:
        return None
    values: dict[str, float] = {"B1": 0.0, "B2": 0.0}
    for _, record in selected.iterrows():
        ecoprovince = str(record["ecoprovince"]).strip()
        zone_id = "B1" if ecoprovince in BC_B1_ECOPROVINCES else "B2"
        values[zone_id] += _finite_nonnegative(
            record["remaining_operable_ha"],
            f"operable area {province}/{ecoprovince}",
        )
    if math.fsum(values.values()) <= 0.0:
        return None
    return values


def _candidate_zones(
    province_cases: Sequence[v1.ProvinceCase],
) -> dict[str, tuple[str, ...]]:
    candidates: dict[str, tuple[str, ...]] = {}
    for item in province_cases:
        current = candidates.get(item.province)
        zones = item.assignment.candidates or (item.assignment.zone_id,)
        normalized = tuple(zone for zone in zones if zone)
        if not normalized:
            raise ForestryOsecanHandoffV2Error(
                f"{item.province} has no OSeCAN zone candidate"
            )
        if current is not None and current != normalized:
            raise ForestryOsecanHandoffV2Error(
                f"{item.province} has inconsistent zone candidates"
            )
        candidates[item.province] = normalized
    if set(candidates) != set(PROVINCES):
        raise ForestryOsecanHandoffV2Error(
            "province cases do not cover all 13 jurisdictions"
        )
    return candidates


def _normalized_weights(
    province: str,
    zone_values: Mapping[str, float],
    candidates: Sequence[str],
) -> dict[str, float]:
    if tuple(zone_values) != tuple(candidates):
        raise ForestryOsecanHandoffV2Error(
            f"{province} weight zones do not match candidate zones"
        )
    total = math.fsum(zone_values.values())
    if total <= 0.0:
        raise ForestryOsecanHandoffV2Error(f"{province} weight total is not positive")
    weights = {zone_id: zone_values[zone_id] / total for zone_id in candidates}
    last_zone = candidates[-1]
    weights[last_zone] = 1.0 - math.fsum(
        weights[zone_id] for zone_id in candidates[:-1]
    )
    if any(weight <= 0.0 for weight in weights.values()):
        raise ForestryOsecanHandoffV2Error(
            f"{province} has a non-positive normalized weight"
        )
    _require_close(
        math.fsum(weights.values()), 1.0, f"{province} weight sum", abs_tol=1e-12
    )
    return weights


def _single_zone_weight(
    province: str, zone_id: str, zones: Mapping[str, v1.Zone]
) -> ZoneWeight:
    return ZoneWeight(
        province=province,
        zone_id=zone_id,
        zone_name=zones[zone_id].zone_name,
        weight=1.0,
        method="single_zone_crosswalk",
        evidence_tier="tier_0_crosswalk_membership",
        proxy_share=False,
        source=str(CROSSWALK.relative_to(REPO_ROOT)),
        source_value=1.0,
        source_unit="unit_weight",
        source_run="",
        source_year="",
        source_technology="",
        mapping_basis=f"zone_crosswalk:{zones[zone_id].mapping_basis}",
        note=(
            "Province is a member of exactly one OSeCAN zone; no allocation "
            "proxy is used."
        ),
    )


def _multi_zone_weights(
    province: str,
    zone_ids: Sequence[str],
    zones: Mapping[str, v1.Zone],
    dispatch: DispatchEvidence,
) -> list[ZoneWeight]:
    method, evidence_tier, category = DISPATCH_METHODS[province]
    dispatch_values = dispatch.weights.get(province)
    if dispatch_values is not None:
        values = {zone_id: dispatch_values[zone_id] for zone_id in zone_ids}
        source = str(DISPATCH.relative_to(REPO_ROOT))
        source_unit = "PJ_2050_supply" if category == "supply" else "PJ_2050_dispatch"
        source_run = DISPATCH_RUN
        source_year = DISPATCH_YEAR
        mapping_basis = (
            "osecan_zonal_supply_dispatch"
            if category == "supply"
            else "osecan_zonal_dispatch_proxy"
        )
        note = (
            "Zone shares come from the OSeCAN "
            f"{DISPATCH_RUN} {DISPATCH_YEAR} {category} table. "
            "This is a model-based zonal activity proxy, not an observed "
            "sub-provincial residue split."
        )
        technologies = DISPATCH_TECHNOLOGY[province]
    else:
        operable = _operable_area_values(province)
        if operable is not None:
            method = "operable_area_ecoprovince_proxy"
            evidence_tier = "tier_3_operable_area_proxy"
            values = {zone_id: operable[zone_id] for zone_id in zone_ids}
            source = str(OPERABLE_BY_ECOPROVINCE.relative_to(REPO_ROOT))
            source_unit = "remaining_operable_ha"
            source_run = "operable_proxy_v3_base_none"
            source_year = ""
            mapping_basis = "ecoprovince_grouping_to_osecan_zone"
            note = (
                "Zone shares come from operable-area weights grouped to the "
                "zone names; this is a screening proxy, not a legal THLB."
            )
            technologies = {}
        else:
            method = "explicit_equal_split_proxy"
            evidence_tier = "tier_4_equal_split_proxy"
            values = {zone_id: 1.0 for zone_id in zone_ids}
            source = "explicit_assumption"
            source_unit = "unit_weight"
            source_run = ""
            source_year = ""
            mapping_basis = "equal_split_no_zone_weight_source"
            note = (
                "No defensible zonal supply, dispatch, or operable-area "
                "mapping exists; shares are an explicit equal split proxy."
            )
            technologies = {}

    weights = _normalized_weights(province, values, zone_ids)
    rows: list[ZoneWeight] = []
    for zone_id in zone_ids:
        source_value = float(values[zone_id])
        rows.append(
            ZoneWeight(
                province=province,
                zone_id=zone_id,
                zone_name=zones[zone_id].zone_name,
                weight=weights[zone_id],
                method=method,
                evidence_tier=evidence_tier,
                proxy_share=True,
                source=source,
                source_value=source_value,
                source_unit=source_unit,
                source_run=source_run,
                source_year=source_year,
                source_technology=technologies.get(zone_id, ""),
                mapping_basis=mapping_basis,
                note=note,
            )
        )
    return rows


def _allocation_weights(
    province_cases: Sequence[v1.ProvinceCase],
    zones: Mapping[str, v1.Zone],
    dispatch: DispatchEvidence,
) -> dict[str, tuple[ZoneWeight, ...]]:
    candidates = _candidate_zones(province_cases)
    weights_by_province: dict[str, tuple[ZoneWeight, ...]] = {}
    for province in PROVINCE_ORDER:
        zone_ids = candidates[province]
        if len(zone_ids) == 1:
            weights_by_province[province] = (
                _single_zone_weight(province, zone_ids[0], zones),
            )
            continue
        weights_by_province[province] = tuple(
            _multi_zone_weights(province, zone_ids, zones, dispatch)
        )
    return weights_by_province


def _zone_status(province: str, zone_id: str, weights: Sequence[ZoneWeight]) -> str:
    if len(weights) > 1:
        return "allocated_multi_zone_province"
    if province in SINGLE_ZONE_PROVINCES:
        return v1._zone_assignment(
            province,
            {
                zone_id: v1.Zone(
                    zone_id=zone_id,
                    zone_name=weights[0].zone_name,
                    provinces=(province,),
                    mapping_basis="code",
                    official_status="code_defined",
                    geometry_available=False,
                    notes="",
                )
            },
            {province: (zone_id,)},
        ).status
    return "mapped_single_zone"


def _allocate_total(total: float, weights: Sequence[ZoneWeight]) -> list[float]:
    if total < 0.0:
        raise ForestryOsecanHandoffV2Error("cannot allocate a negative total")
    allocated = 0.0
    parts: list[float] = []
    for weight in weights[:-1]:
        part = total * weight.weight
        parts.append(part)
        allocated += part
    parts.append(total - allocated)
    if any(part < 0.0 for part in parts):
        raise ForestryOsecanHandoffV2Error("allocation produced a negative part")
    _require_close(math.fsum(parts), total, "allocation conservation", abs_tol=1e-9)
    return parts


def _allocation_caveat(
    item: v1.ProvinceCase, weight: ZoneWeight, weight_count: int
) -> str:
    if weight.proxy_share:
        mapping = (
            f"Province residue is split across {weight_count} OSeCAN zones by "
            f"an explicit screening proxy (method={weight.method}, "
            f"evidence_tier={weight.evidence_tier}); zone share "
            f"{weight.weight:.6f}. No observed sub-provincial residue split "
            "exists."
        )
    else:
        mapping = (
            f"Province maps to one OSeCAN zone ({weight.zone_id}) by "
            "code-defined crosswalk membership; no split is used."
        )
    return (
        f"{mapping} Proxy shares are explicit model estimates, not policy or "
        "operating assignments. No official OSeCAN file is changed. "
        f"Source caveat: {item.caveat}"
    )


def _handoff_rows(
    province_cases: Sequence[v1.ProvinceCase],
    national: Mapping[tuple[str, str], v1.NationalCase],
    tiers: Mapping[str, v1.Tier],
    weights_by_province: Mapping[str, tuple[ZoneWeight, ...]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in province_cases:
        weights = weights_by_province[item.province]
        pj_parts = _allocate_total(item.pj, weights)
        national_case = national[(item.stream, item.availability_case)]
        tier_id = v1._tier_for_pj(national_case.pj, tiers)
        for weight, pj in zip(weights, pj_parts, strict=True):
            odt = pj * PJ_GJ / EXPECTED_LHV_GJ_PER_ODT
            assignment = v1.ZoneAssignment(
                zone_id=weight.zone_id,
                zone_name=weight.zone_name,
                status=_zone_status(item.province, weight.zone_id, weights),
                candidates=tuple(entry.zone_id for entry in weights),
                basis=weight.mapping_basis,
                note=weight.note,
            )
            technology, mapping_kind, mapping_note = v1._supbcw_mapping(
                assignment, item.province
            )
            rows.append(
                {
                    "province": item.province,
                    "province_name": item.province_name,
                    "zone_id": weight.zone_id,
                    "zone_name": weight.zone_name,
                    "zone_mapped": LABEL_TRUE,
                    "zone_mapping_status": assignment.status,
                    "zone_candidates": "|".join(assignment.candidates),
                    "zone_mapping_basis": assignment.basis,
                    "zone_weight": weight.weight,
                    "zone_weight_method": weight.method,
                    "zone_evidence_tier": weight.evidence_tier,
                    "proxy_share": (LABEL_TRUE if weight.proxy_share else LABEL_FALSE),
                    "allocation_status": (
                        "proxy_share" if weight.proxy_share else "single_zone"
                    ),
                    "stream": item.stream,
                    "residue_class": item.residue_class,
                    "province_basis": item.province_basis,
                    "availability_case": item.availability_case,
                    "availability_fraction": item.availability_fraction,
                    "odt": odt,
                    "pj": pj,
                    "lhv_gj_per_odt": EXPECTED_LHV_GJ_PER_ODT,
                    "supbcw_technology_suggestion": technology,
                    "supbcw_mapping": mapping_kind,
                    "supbcw_mapping_note": mapping_note,
                    "supply_curve_tier_at_national_case": tier_id,
                    "supply_curve_tier_upper_pj": v1._tier_upper(
                        national_case.pj, tiers
                    ),
                    "evidence_tier": item.evidence_tier,
                    "rsyc_comparable": (
                        LABEL_TRUE if item.rsyc_comparable else LABEL_FALSE
                    ),
                    "model_estimate": LABEL_TRUE,
                    "observed": LABEL_FALSE,
                    "policy_grade": LABEL_FALSE,
                    "official_osecan_changed": LABEL_FALSE,
                    "caveat": _allocation_caveat(item, weight, len(weights)),
                    "notes": (
                        "Source: national_residue_curve_v1/"
                        "province_residue_curve.csv; "
                        f"stream={item.stream}; "
                        f"residue_class={item.residue_class}; "
                        f"province_basis={item.province_basis}; "
                        f"zone_weight_source={weight.source}. Process and "
                        "harvest streams are separate and are not summed."
                    ),
                }
            )
    return rows


def _zone_summary_rows(
    handoff_rows: Sequence[Mapping[str, Any]],
    national: Mapping[tuple[str, str], v1.NationalCase],
    zones: Mapping[str, v1.Zone],
    weights_by_province: Mapping[str, tuple[ZoneWeight, ...]],
) -> list[dict[str, Any]]:
    by_key: dict[tuple[str, str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in handoff_rows:
        by_key[(row["zone_id"], row["stream"], row["availability_case"])].append(row)

    rows: list[dict[str, Any]] = []
    for zone_id in ZONE_ORDER:
        zone = zones[zone_id]
        zone_weights = [
            weight
            for weights in weights_by_province.values()
            for weight in weights
            if weight.zone_id == zone_id
        ]
        members = [
            province
            for province in PROVINCE_ORDER
            if any(weight.province == province for weight in zone_weights)
        ]
        if not members:
            raise ForestryOsecanHandoffV2Error(
                f"zone {zone_id} has no allocated province"
            )
        is_aggregate = len(members) > 1
        is_allocated = any(
            len(weights_by_province[province]) > 1 for province in members
        )
        for stream, cases in (
            (PROCESS_STREAM, PROCESS_CASES),
            (HARVEST_STREAM, HARVEST_CASES),
        ):
            for case, _ in cases:
                selected = by_key[(zone_id, stream, case)]
                national_case = national[(stream, case)]
                pj = math.fsum(float(row["pj"]) for row in selected)
                odt = math.fsum(float(row["odt"]) for row in selected)
                methods = sorted({str(row["zone_weight_method"]) for row in selected})
                tiers = sorted({str(row["zone_evidence_tier"]) for row in selected})
                technology, mapping_kind, _ = v1._supbcw_mapping(
                    v1.ZoneAssignment(
                        zone_id=zone_id,
                        zone_name=zone.zone_name,
                        status=(
                            "mapped_aggregate_zone"
                            if is_aggregate
                            else (
                                "allocated_multi_zone_province"
                                if is_allocated
                                else "mapped_single_zone"
                            )
                        ),
                        candidates=(zone_id,),
                        basis=f"zone_crosswalk:{zone.mapping_basis}",
                        note="",
                    ),
                    members[0],
                )
                rows.append(
                    {
                        "zone_id": zone_id,
                        "zone_name": zone.zone_name,
                        "zone_mapping_status": (
                            "mapped_aggregate_zone"
                            if is_aggregate
                            else (
                                "allocated_multi_zone_province"
                                if is_allocated
                                else "mapped_single_zone"
                            )
                        ),
                        "provinces_included": "|".join(members),
                        "province_count": len(members),
                        "stream": stream,
                        "availability_case": case,
                        "odt": odt,
                        "pj": pj,
                        "national_case_pj": national_case.pj,
                        "share_of_national_case": (
                            pj / national_case.pj if national_case.pj > 0.0 else 0.0
                        ),
                        "share_status": (
                            "defined"
                            if national_case.pj > 0.0
                            else "national_case_zero"
                        ),
                        "zone_weight_methods": "|".join(methods),
                        "zone_evidence_tiers": "|".join(tiers),
                        "proxy_share": (
                            LABEL_TRUE
                            if any(row["proxy_share"] == LABEL_TRUE for row in selected)
                            else LABEL_FALSE
                        ),
                        "allocation_status": (
                            "proxy_share"
                            if any(
                                row["allocation_status"] == "proxy_share"
                                for row in selected
                            )
                            else "single_zone"
                        ),
                        "supbcw_technology_suggestion": technology,
                        "supbcw_mapping": mapping_kind,
                        "model_estimate": LABEL_TRUE,
                        "observed": LABEL_FALSE,
                        "policy_grade": LABEL_FALSE,
                        "official_osecan_changed": LABEL_FALSE,
                        "caveat": (
                            f"Zone total sums allocated province rows once. "
                            f"Zone members: {', '.join(members)}. "
                            "Process and harvest streams are not combined. "
                            "Allocated sub-zone shares are explicit model "
                            "proxies where proxy_share=true."
                        ),
                    }
                )
    return rows


def _comparison_rows(
    national: Mapping[tuple[str, str], v1.NationalCase],
    zone_rows: Sequence[Mapping[str, Any]],
    tiers: Mapping[str, v1.Tier],
    context: v1.OsecanContext,
    dispatch: DispatchEvidence,
) -> list[dict[str, Any]]:
    zone_totals = {
        (row["zone_id"], row["stream"], row["availability_case"]): row
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
    for stream, cases in (
        (PROCESS_STREAM, PROCESS_CASES),
        (HARVEST_STREAM, HARVEST_CASES),
    ):
        for case, _ in cases:
            national_case = national[(stream, case)]
            for reference, kind, value, note in national_refs:
                rows.append(
                    _comparison_row(
                        scope="national",
                        stream=stream,
                        case=case,
                        case_odt=national_case.odt,
                        case_pj=national_case.pj,
                        case_tier=v1._tier_for_pj(national_case.pj, tiers),
                        reference=reference,
                        kind=kind,
                        value=value,
                        note=note,
                    )
                )

            bc_pj = math.fsum(
                float(zone_totals[(zone_id, stream, case)]["pj"])
                for zone_id in BC_ZONES
            )
            bc_odt = math.fsum(
                float(zone_totals[(zone_id, stream, case)]["odt"])
                for zone_id in BC_ZONES
            )
            for reference, kind, value, note in bc_refs:
                rows.append(
                    _comparison_row(
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
                zone_row = zone_totals[(zone_id, stream, case)]
                rows.append(
                    _comparison_row(
                        scope=zone_id,
                        stream=stream,
                        case=case,
                        case_odt=float(zone_row["odt"]),
                        case_pj=float(zone_row["pj"]),
                        case_tier="",
                        reference="osecan_2050_zone_dispatch_context",
                        kind="osecan_2050_zone_context",
                        value=dispatch.zone_dispatch_context[zone_id],
                        note=(
                            "2050 OSeCAN stress_119m zone dispatch activity; "
                            "context only, not a policy target."
                        ),
                    )
                )
    return rows


def _comparison_row(
    *,
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


def _check(checks: list[dict[str, Any]], name: str, passed: bool, detail: str) -> None:
    checks.append({"check": name, "passed": bool(passed), "detail": detail})


def _validate(
    *,
    province_cases: Sequence[v1.ProvinceCase],
    national: Mapping[tuple[str, str], v1.NationalCase],
    weights_by_province: Mapping[str, tuple[ZoneWeight, ...]],
    handoff_rows: Sequence[Mapping[str, Any]],
    zone_rows: Sequence[Mapping[str, Any]],
    comparison_rows: Sequence[Mapping[str, Any]],
    tiers: Mapping[str, v1.Tier],
    context: v1.OsecanContext,
    dispatch: DispatchEvidence,
    osecan_tree_before: str,
    osecan_tree_after: str,
    osecan_changed_files: Sequence[str],
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    _check(
        checks,
        "OSeCAN tree hash unchanged",
        osecan_tree_before == osecan_tree_after and not osecan_changed_files,
        (
            f"before={osecan_tree_before[:16]}... "
            f"after={osecan_tree_after[:16]}... "
            f"changed_files={len(osecan_changed_files)}"
        ),
    )
    _check(
        checks,
        "no combined residue stream",
        not any(str(row["stream"]) == "combined" for row in handoff_rows),
        "process and harvest remain separate",
    )

    expected_handoff_rows = sum(
        len(weights) for weights in weights_by_province.values()
    ) * (len(PROCESS_CASES) + len(HARVEST_CASES))
    handoff_keys = {
        (
            row["province"],
            row["zone_id"],
            row["stream"],
            row["availability_case"],
        )
        for row in handoff_rows
    }
    _check(
        checks,
        "handoff row count and uniqueness",
        len(handoff_rows) == expected_handoff_rows
        and len(handoff_keys) == len(handoff_rows),
        f"rows={len(handoff_rows)} expected={expected_handoff_rows}",
    )
    _check(
        checks,
        "every handoff row has a zone and no UNMAPPED",
        all(
            str(row["zone_id"]) in ZONE_ORDER
            and str(row["zone_id"]) != UNMAPPED_ZONE
            and row["zone_mapped"] == LABEL_TRUE
            for row in handoff_rows
        ),
        "all rows mapped to one of the 13 OSeCAN zones",
    )
    _check(
        checks,
        "handoff labels",
        all(
            row["model_estimate"] == LABEL_TRUE
            and row["observed"] == LABEL_FALSE
            and row["policy_grade"] == LABEL_FALSE
            and row["official_osecan_changed"] == LABEL_FALSE
            for row in handoff_rows
        ),
        "model_estimate=true, observed=false, policy_grade=false, official_osecan_changed=false",
    )
    _check(
        checks,
        "proxy share labels",
        all(
            (row["proxy_share"] == LABEL_TRUE)
            == (row["zone_weight_method"] != "single_zone_crosswalk")
            for row in handoff_rows
        ),
        "multi-zone allocations carry proxy_share=true",
    )

    arithmetic_ok = True
    arithmetic_detail = ""
    for row in handoff_rows:
        expected = float(row["odt"]) * EXPECTED_LHV_GJ_PER_ODT / PJ_GJ
        if abs(float(row["pj"]) - expected) > 1e-6 + 1e-8 * abs(expected):
            arithmetic_ok = False
            arithmetic_detail = (
                f"{row['province']}/{row['zone_id']}/"
                f"{row['stream']}/{row['availability_case']}: "
                f"{row['pj']} != {expected}"
            )
            break
    _check(
        checks,
        "handoff PJ arithmetic",
        arithmetic_ok,
        arithmetic_detail or "all rows",
    )

    weight_ok = True
    weight_detail = ""
    for province, weights in weights_by_province.items():
        total = math.fsum(weight.weight for weight in weights)
        if abs(total - 1.0) > 1e-12:
            weight_ok = False
            weight_detail = f"{province} weight sum {total!r}"
            break
        if any(weight.weight <= 0.0 for weight in weights):
            weight_ok = False
            weight_detail = f"{province} has non-positive weight"
            break
    _check(
        checks,
        "province zone weights sum to one",
        weight_ok,
        weight_detail or "all 13 provinces sum to one",
    )

    conservation_ok = True
    conservation_detail = ""
    for item in province_cases:
        rows = [
            row
            for row in handoff_rows
            if row["province"] == item.province
            and row["stream"] == item.stream
            and row["availability_case"] == item.availability_case
        ]
        total_pj = math.fsum(float(row["pj"]) for row in rows)
        total_odt = math.fsum(float(row["odt"]) for row in rows)
        if abs(total_pj - item.pj) > 1e-6 or abs(total_odt - item.odt) > 1e-3:
            conservation_ok = False
            conservation_detail = (
                f"{item.province}/{item.stream}/{item.availability_case} "
                "does not reconcile"
            )
            break
    _check(
        checks,
        "province residue conservation",
        conservation_ok,
        conservation_detail or "allocated zone rows sum to each province total",
    )

    national_ok = True
    national_detail = ""
    for stream, cases in (
        (PROCESS_STREAM, PROCESS_CASES),
        (HARVEST_STREAM, HARVEST_CASES),
    ):
        for case, _ in cases:
            national_case = national[(stream, case)]
            selected = [
                row
                for row in handoff_rows
                if row["stream"] == stream and row["availability_case"] == case
            ]
            total_pj = math.fsum(float(row["pj"]) for row in selected)
            total_odt = math.fsum(float(row["odt"]) for row in selected)
            if (
                abs(total_pj - national_case.pj) > 1e-5
                or abs(total_odt - national_case.odt) > 1e-3
            ):
                national_ok = False
                national_detail = f"{stream}/{case} does not reconcile"
                break
        if not national_ok:
            break
    _check(
        checks,
        "handoff reconciles to national curve",
        national_ok,
        national_detail or "zone rows sum once to national totals",
    )

    zone_keys = {
        (row["zone_id"], row["stream"], row["availability_case"]) for row in zone_rows
    }
    _check(
        checks,
        "zone summary covers every zone and case",
        len(zone_rows) == len(ZONE_ORDER) * (len(PROCESS_CASES) + len(HARVEST_CASES))
        and len(zone_keys) == len(zone_rows)
        and {row["zone_id"] for row in zone_rows} == set(ZONE_ORDER),
        f"rows={len(zone_rows)} zones={len({row['zone_id'] for row in zone_rows})}",
    )
    _check(
        checks,
        "no UNMAPPED bucket",
        all(str(row["zone_id"]) != UNMAPPED_ZONE for row in (*handoff_rows, *zone_rows))
        and all(
            str(row["comparison_scope"]) != UNMAPPED_ZONE for row in comparison_rows
        ),
        "UNMAPPED is absent from handoff, summary, and comparisons",
    )

    zone_reconciliation_ok = True
    zone_detail = ""
    for stream, cases in (
        (PROCESS_STREAM, PROCESS_CASES),
        (HARVEST_STREAM, HARVEST_CASES),
    ):
        for case, _ in cases:
            national_case = national[(stream, case)]
            selected = [
                row
                for row in zone_rows
                if row["stream"] == stream and row["availability_case"] == case
            ]
            if (
                abs(math.fsum(float(row["pj"]) for row in selected) - national_case.pj)
                > 1e-5
                or abs(
                    math.fsum(float(row["odt"]) for row in selected) - national_case.odt
                )
                > 1e-3
            ):
                zone_reconciliation_ok = False
                zone_detail = f"{stream}/{case} zone rows do not reconcile"
                break
        if not zone_reconciliation_ok:
            break
    _check(
        checks,
        "zone summary reconciles without double count",
        zone_reconciliation_ok,
        zone_detail or "13 zone rows equal national totals",
    )

    expected_tiers = {
        ("process", "process_availability_000"): "T1",
        ("process", "process_availability_025"): "T3",
        ("process", "process_availability_050"): "above_T4",
        ("process", "process_availability_075"): "above_T4",
        ("harvest", "harvest_recovery_035"): "T1",
        ("harvest", "harvest_recovery_060"): "T2",
        ("harvest", "harvest_recovery_075"): "T3",
    }
    tier_ok = True
    tier_detail = ""
    for (stream, case), expected in expected_tiers.items():
        actual = v1._tier_for_pj(national[(stream, case)].pj, tiers)
        if actual != expected:
            tier_ok = False
            tier_detail = f"{stream}/{case}: {actual} != {expected}"
            break
    _check(
        checks,
        "national case tier positions",
        tier_ok,
        tier_detail or "as expected",
    )

    comparison_ok = True
    comparison_detail = ""
    expected_comparison_rows = (len(PROCESS_CASES) + len(HARVEST_CASES)) * (
        5 + 2 + len(ZONE_ORDER)
    )
    for row in comparison_rows:
        if row["reference_status"] != "positive":
            continue
        reference = float(row["reference_pj"])
        ratio = float(row["ratio"])
        if abs(ratio - float(row["case_pj"]) / reference) > 1e-9:
            comparison_ok = False
            comparison_detail = f"{row['comparison_scope']} ratio mismatch"
            break
        if abs(float(row["headroom_pj"]) - (reference - float(row["case_pj"]))) > 1e-6:
            comparison_ok = False
            comparison_detail = f"{row['comparison_scope']} headroom mismatch"
            break
        if abs(float(row["headroom_ratio"]) - (1.0 - ratio)) > 1e-9:
            comparison_ok = False
            comparison_detail = f"{row['comparison_scope']} headroom ratio mismatch"
            break
    _check(
        checks,
        "comparison rows and ratios",
        comparison_ok and len(comparison_rows) == expected_comparison_rows,
        comparison_detail or f"rows={len(comparison_rows)}",
    )
    _check(
        checks,
        "comparison labels",
        all(
            row["reference_is_policy_target"] == LABEL_FALSE
            and row["model_estimate"] == LABEL_TRUE
            and row["observed"] == LABEL_FALSE
            and row["policy_grade"] == LABEL_FALSE
            and row["official_osecan_changed"] == LABEL_FALSE
            for row in comparison_rows
        ),
        "context references are not policy targets",
    )
    _check(
        checks,
        "2050 context values",
        abs(context.national_2050_supply_pj - v1.EXPECTED_NATIONAL_2050_CONTEXT_PJ)
        <= 1e-5
        and abs(context.bc_2050_supply_pj - v1.EXPECTED_BC_2050_CONTEXT_PJ) <= 1e-5
        and abs(context.bc_zonal_upper_pj - v1.EXPECTED_BC_ZONAL_UPPER_PJ) <= 1e-6,
        (
            f"national={context.national_2050_supply_pj:.5f} "
            f"BC={context.bc_2050_supply_pj:.5f} "
            f"BC_zonal={context.bc_zonal_upper_pj:.6f}"
        ),
    )
    _check(
        checks,
        "supply-curve upper bound",
        abs(tiers["T4"].upper_pj - v1.EXPECTED_TIER_UPPER_PJ) <= 1e-6,
        f"T4 upper={tiers['T4'].upper_pj:.6f}",
    )
    _check(
        checks,
        "2050 zone dispatch context",
        set(dispatch.zone_dispatch_context) == set(ZONE_ORDER)
        and all(
            math.isfinite(value) and value >= 0.0
            for value in dispatch.zone_dispatch_context.values()
        ),
        (
            f"zones={len(dispatch.zone_dispatch_context)} "
            f"zero={sum(1 for value in dispatch.zone_dispatch_context.values() if value == 0.0)}"
        ),
    )
    _check(
        checks,
        "zero values only where source is genuinely zero",
        all(
            float(row["pj"]) > 0.0
            for row in handoff_rows
            if row["availability_case"] != "process_availability_000"
            and any(
                item.province == row["province"]
                and item.stream == row["stream"]
                and item.availability_case == row["availability_case"]
                and item.pj > 0.0
                for item in province_cases
            )
        ),
        "positive province cases allocate positive zone values",
    )
    return checks


def _write_csv(
    path: Path, rows: Sequence[Mapping[str, Any]], fieldnames: Sequence[str]
) -> None:
    if not rows:
        raise ForestryOsecanHandoffV2Error(f"refusing to write empty CSV: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fieldnames))
        writer.writeheader()
        for row in rows:
            rendered: dict[str, Any] = {}
            for key, value in row.items():
                if isinstance(value, float):
                    if not math.isfinite(value):
                        raise ForestryOsecanHandoffV2Error(
                            f"refusing to write non-finite {key}: {value!r}"
                        )
                    rendered[key] = f"{value:.12g}"
                else:
                    rendered[key] = value
            writer.writerow(rendered)


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")


def _weight_rows(
    weights_by_province: Mapping[str, tuple[ZoneWeight, ...]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for province in PROVINCE_ORDER:
        weights = weights_by_province[province]
        for weight in weights:
            rows.append(
                {
                    "province": weight.province,
                    "zone_id": weight.zone_id,
                    "zone_name": weight.zone_name,
                    "weight": weight.weight,
                    "weight_method": weight.method,
                    "evidence_tier": weight.evidence_tier,
                    "proxy_share": (LABEL_TRUE if weight.proxy_share else LABEL_FALSE),
                    "source": weight.source,
                    "source_value": weight.source_value,
                    "source_unit": weight.source_unit,
                    "source_run": weight.source_run,
                    "source_year": weight.source_year,
                    "source_technology": weight.source_technology,
                    "mapping_basis": weight.mapping_basis,
                    "model_estimate": LABEL_TRUE,
                    "observed": LABEL_FALSE,
                    "policy_grade": LABEL_FALSE,
                    "official_osecan_changed": LABEL_FALSE,
                    "caveat": (
                        "Allocation weight is a screening model proxy, not an "
                        "observed sub-provincial residue share and not policy."
                    ),
                    "notes": weight.note,
                }
            )
    return rows


def _zone_mapping_summary(
    zones: Mapping[str, v1.Zone],
    weights_by_province: Mapping[str, tuple[ZoneWeight, ...]],
) -> dict[str, Any]:
    zone_provinces: dict[str, list[str]] = {zone_id: [] for zone_id in ZONE_ORDER}
    for province in PROVINCE_ORDER:
        for weight in weights_by_province[province]:
            zone_provinces[weight.zone_id].append(province)
    return {
        "province_count": len(PROVINCES),
        "zone_count": len(ZONE_ORDER),
        "allocated_province_count": len(MULTI_ZONE_PROVINCES),
        "single_zone_province_count": len(SINGLE_ZONE_PROVINCES),
        "unmapped_province_count": 0,
        "unmapped_provinces": [],
        "multi_zone_provinces": list(MULTI_ZONE_PROVINCES),
        "single_zone_provinces": list(SINGLE_ZONE_PROVINCES),
        "proxy_share_provinces": list(MULTI_ZONE_PROVINCES),
        "zones": {
            zone_id: {
                "zone_name": zones[zone_id].zone_name,
                "official_status": zones[zone_id].official_status,
                "mapping_basis": zones[zone_id].mapping_basis,
                "geometry_available": zones[zone_id].geometry_available,
                "allocated_provinces": zone_provinces[zone_id],
                "status": (
                    "mapped_aggregate_zone"
                    if len(zone_provinces[zone_id]) > 1
                    else (
                        "allocated_multi_zone_province"
                        if len(weights_by_province[zone_provinces[zone_id][0]]) > 1
                        else "mapped_single_zone"
                    )
                ),
            }
            for zone_id in ZONE_ORDER
        },
    }


def _weight_summary(
    weights_by_province: Mapping[str, tuple[ZoneWeight, ...]],
) -> dict[str, Any]:
    methods: dict[str, int] = defaultdict(int)
    tiers: dict[str, int] = defaultdict(int)
    per_province: dict[str, Any] = {}
    for province, weights in weights_by_province.items():
        for weight in weights:
            methods[weight.method] += 1
            tiers[weight.evidence_tier] += 1
        per_province[province] = {
            "method": weights[0].method,
            "evidence_tier": weights[0].evidence_tier,
            "proxy_share": weights[0].proxy_share,
            "zones": {
                weight.zone_id: {
                    "weight": weight.weight,
                    "source_value": weight.source_value,
                    "source_unit": weight.source_unit,
                    "source_technology": weight.source_technology,
                }
                for weight in weights
            },
        }
    return {
        "methods": dict(sorted(methods.items())),
        "evidence_tiers": dict(sorted(tiers.items())),
        "provinces": per_province,
    }


def _format_number(value: float) -> str:
    if abs(value) >= 1_000_000:
        return f"{value:,.0f}"
    if abs(value) >= 1_000:
        return f"{value:,.1f}"
    if abs(value) >= 1:
        return f"{value:,.3f}"
    return f"{value:.6f}"


def _report(
    summary: Mapping[str, Any],
    weight_rows: Sequence[Mapping[str, Any]],
    zone_rows: Sequence[Mapping[str, Any]],
    comparison_rows: Sequence[Mapping[str, Any]],
) -> str:
    allocation = summary["zone_allocation"]
    lines = [
        "# Forestry Residue Handoff to OSeCAN v2",
        "",
        (
            "This is a read-only forestry-side screening handoff from "
            "`national_residue_curve_v1` province/case totals to every OSeCAN "
            "zone. Version 2 removes the `UNMAPPED` bucket by allocating "
            "BC, AB, ON, and QC across their sub-zones with explicit proxy "
            "weights. It is not an official OSeCAN result, an operating plan, "
            "or a policy target."
        ),
        "",
        (
            "All values are model estimates: `model_estimate=true`, "
            "`observed=false`, `policy_grade=false`, and "
            "`official_osecan_changed=false`. Process and harvest residue "
            "remain separate streams and are never summed into a combined row."
        ),
        "",
        "## Zone weight method",
        "",
        (
            "The allocation hierarchy is: OSeCAN zonal supply dispatch; "
            "OSeCAN zonal dispatch activity; operable-area weights where a "
            "defensible ecoprovince-to-zone mapping exists; otherwise an "
            "explicit equal split. No observed sub-provincial residue split "
            "is available, so every multi-zone allocation is labelled "
            "`proxy_share=true`."
        ),
        "",
        "| Province | Zone | Weight | Method | Evidence tier | Source value | Unit |",
        "|---|---|---:|---|---|---:|---|",
    ]
    for row in weight_rows:
        lines.append(
            f"| {row['province']} | {row['zone_id']} | "
            f"{float(row['weight']):.6f} | {row['weight_method']} | "
            f"{row['evidence_tier']} | {float(row['source_value']):,.6f} | "
            f"{row['source_unit']} |"
        )
    lines.extend(
        [
            "",
            (
                "Single-zone jurisdictions use their code-defined crosswalk "
                "membership with weight 1.0. BC uses OSeCAN `SUPBCWB1`/"
                "`SUPBCWB2` 2050 supply. AB, ON, and QC use OSeCAN "
                "`PWRBCW<zone>` 2050 dispatch because OSeCAN has no "
                "zone-specific supply row for those provinces. Those dispatch "
                "shares are zonal activity proxies, not harvest observations."
            ),
            "",
            "## Handoff by zone",
            "",
            "| Zone | Process 0% | Process 25% | Process 50% | Process 75% | Harvest 35% | Harvest 60% | Harvest 75% |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for zone_id in ZONE_ORDER:
        values: list[str] = []
        for stream, cases in (
            (PROCESS_STREAM, PROCESS_CASES),
            (HARVEST_STREAM, HARVEST_CASES),
        ):
            for case, _ in cases:
                record = next(
                    row
                    for row in zone_rows
                    if row["zone_id"] == zone_id
                    and row["stream"] == stream
                    and row["availability_case"] == case
                )
                values.append(f"{float(record['pj']):,.3f}")
        lines.append(f"| {zone_id} | " + " | ".join(values) + " |")
    lines.extend(
        [
            "",
            "National totals (PJ):",
            "",
            "| Stream | Case | PJ |",
            "|---|---|---:|",
        ]
    )
    for stream in (PROCESS_STREAM, HARVEST_STREAM):
        for case, pj in summary["handoff_totals"][stream].items():
            lines.append(f"| {stream} | `{case}` | {float(pj):,.6f} |")
    lines.extend(
        [
            "",
            "## Supply-curve comparison",
            "",
            (
                "Ratios are case PJ divided by the reference. Headroom is "
                "reference minus case; a negative headroom means the case "
                "exceeds the reference. The 2050 values are context, not "
                "policy targets. Zone 2050 context uses OSeCAN zone dispatch "
                "activity and is undefined where that activity is zero."
            ),
            "",
            "### National",
            "",
            "| Case | PJ | Tier | T1 headroom | T2 headroom | T3 headroom | T4 headroom | 2050 context ratio | 2050 context headroom |",
            "|---|---:|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for stream in (PROCESS_STREAM, HARVEST_STREAM):
        cases = (
            [name for name, _ in PROCESS_CASES]
            if stream == PROCESS_STREAM
            else [name for name, _ in HARVEST_CASES]
        )
        for case in cases:
            selected = [
                row
                for row in comparison_rows
                if row["comparison_scope"] == "national"
                and row["stream"] == stream
                and row["availability_case"] == case
            ]
            by_ref = {row["reference"]: row for row in selected}
            case_row = selected[0]
            lines.append(
                f"| `{case}` | {float(case_row['case_pj']):,.3f} | "
                f"{case_row['case_tier']} | "
                f"{float(by_ref['national_supply_curve_t1_upper']['headroom_pj']):,.3f} | "
                f"{float(by_ref['national_supply_curve_t2_upper']['headroom_pj']):,.3f} | "
                f"{float(by_ref['national_supply_curve_t3_upper']['headroom_pj']):,.3f} | "
                f"{float(by_ref['national_supply_curve_t4_upper']['headroom_pj']):,.3f} | "
                f"{float(by_ref['osecan_2050_national_supply_context']['ratio']):.3f} | "
                f"{float(by_ref['osecan_2050_national_supply_context']['headroom_pj']):,.3f} |"
            )
    lines.extend(
        [
            "",
            "### BC",
            "",
            "| Case | PJ | BC zonal cap ratio | BC zonal headroom | 2050 BC context ratio | 2050 BC context headroom |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for stream in (PROCESS_STREAM, HARVEST_STREAM):
        cases = (
            [name for name, _ in PROCESS_CASES]
            if stream == PROCESS_STREAM
            else [name for name, _ in HARVEST_CASES]
        )
        for case in cases:
            selected = [
                row
                for row in comparison_rows
                if row["comparison_scope"] == "BC"
                and row["stream"] == stream
                and row["availability_case"] == case
            ]
            by_ref = {row["reference"]: row for row in selected}
            case_row = selected[0]
            lines.append(
                f"| `{case}` | {float(case_row['case_pj']):,.3f} | "
                f"{float(by_ref['bc_zonal_supply_upper_2050']['ratio']):.3f} | "
                f"{float(by_ref['bc_zonal_supply_upper_2050']['headroom_pj']):,.3f} | "
                f"{float(by_ref['osecan_2050_bc_supply_context']['ratio']):.3f} | "
                f"{float(by_ref['osecan_2050_bc_supply_context']['headroom_pj']):,.3f} |"
            )
    lines.extend(
        [
            "",
            "### Zone 2050 context",
            "",
            (
                "This table is context only. The reference is 2050 OSeCAN "
                "zone dispatch activity, not a zone supply cap and not a "
                "policy target. A blank ratio means the reference is zero."
            ),
            "",
            "| Zone | Reference PJ | Process 50% ratio | Harvest 60% ratio |",
            "|---|---:|---:|---:|",
        ]
    )
    for zone_id in ZONE_ORDER:
        process_row = next(
            row
            for row in comparison_rows
            if row["comparison_scope"] == zone_id
            and row["stream"] == PROCESS_STREAM
            and row["availability_case"] == "process_availability_050"
        )
        harvest_row = next(
            row
            for row in comparison_rows
            if row["comparison_scope"] == zone_id
            and row["stream"] == HARVEST_STREAM
            and row["availability_case"] == "harvest_recovery_060"
        )
        process_ratio = (
            f"{float(process_row['ratio']):.3f}" if process_row["ratio"] != "" else "—"
        )
        harvest_ratio = (
            f"{float(harvest_row['ratio']):.3f}" if harvest_row["ratio"] != "" else "—"
        )
        lines.append(
            f"| {zone_id} | {float(process_row['reference_pj']):,.3f} | "
            f"{process_ratio} | {harvest_ratio} |"
        )
    lines.extend(
        [
            "",
            "## Proxy and uncertainty",
            "",
            (
                f"{allocation['allocated_province_count']} provinces are "
                "allocated across two zones each and "
                f"{allocation['single_zone_province_count']} provinces use a "
                "single zone. Every multi-zone weight is a proxy, not an "
                "observed share. The same province-level shares are applied "
                "to process and harvest residue; no stream-specific "
                "sub-provincial evidence is available."
            ),
            "",
            "- BC shares use 2050 OSeCAN zonal supply dispatch (`SUPBCWB1`/`SUPBCWB2`).",
            "- AB, ON, and QC shares use 2050 OSeCAN zonal dispatch (`PWRBCW<zone>`); this is zonal energy activity, not harvest volume.",
            "- Operable-area weights were considered for BC using the crosswalk B1 ecoprovince definition; they were not activated because zonal supply dispatch is available.",
            "- No defensible ecoprovince-to-zone mapping exists for A1/A2, O1/O2, or Q1/Q2 in the published crosswalk, so operable-area weights were not applied there.",
            "- Equal split was implemented as the final fallback but was not activated because dispatch weights are positive.",
            "- Zone shares are stable across all process and harvest cases; only the province total changes by case.",
            "",
            "## Caveats",
            "",
            "- Zone allocation is screening-only; no published sub-provincial residue polygon or observed split is used.",
            "- Process residue is facility-province based; harvest residue is origin-province based. They are separate and not summed.",
            "- Process rows use the `TOTAL` class row only; harvest rows use the `ALL` row only.",
            "- SUPBCW technology strings are candidate mappings, not operating assignments or official OSeCAN technology choices.",
            "- The 2050 OSeCAN national (152.923 PJ) and BC (43.524 PJ) values are model context, not policy targets.",
            "- The 213.54 PJ national supply-curve top is an adopted model bound, not a policy target.",
            "- No transport, cost, revenue, capacity-expansion, tenure, AAC, or policy claim is made.",
            "- No UI or public OSeCAN surface is changed.",
            "",
            "## Validation and status",
            "",
            (
                f"Build status: `{summary['status']}`. Validation checks: "
                f"{len(summary['validation']['checks'])}, failed: "
                f"{summary['validation']['failed_check_count']}."
            ),
            "",
            (
                "Run `.venv/bin/python -m "
                "workflows.national.facilities.forestry_osecan_handoff_v2` to "
                "reproduce the artifact and `.venv/bin/python -m pytest "
                "tests/test_forestry_osecan_handoff_v2.py -q` to run the "
                "regression tests."
            ),
            "",
            "No policy/AAC claim is made.",
            "",
        ]
    )
    return "\n".join(lines)


def build(output_dir: Path | None = None) -> dict[str, Any]:
    """Build the all-zone forestry residue handoff and return its summary."""
    target_dir = Path(output_dir) if output_dir is not None else OUTPUT_DIR
    generated_utc = datetime.now(UTC).isoformat(timespec="seconds")
    assert_no_unexpected_deletions()

    osecan_hashes_before, osecan_tree_before = _hash_osecan_tree()

    zones, _province_zones = v1._parse_zone_crosswalk()
    province_cases = v1._parse_province_cases(zones, _province_zones)
    national = v1._parse_national_cases(province_cases)
    tiers = v1._parse_tiers()
    context = v1._parse_osecan_context(tiers)
    dispatch = _parse_dispatch_evidence()

    weights_by_province = _allocation_weights(province_cases, zones, dispatch)
    handoff_rows = _handoff_rows(province_cases, national, tiers, weights_by_province)
    zone_rows = _zone_summary_rows(handoff_rows, national, zones, weights_by_province)
    comparison_rows = _comparison_rows(national, zone_rows, tiers, context, dispatch)
    weight_rows = _weight_rows(weights_by_province)

    osecan_hashes_after, osecan_tree_after = _hash_osecan_tree()
    changed_files = sorted(
        path
        for path in set(osecan_hashes_before) | set(osecan_hashes_after)
        if osecan_hashes_before.get(path) != osecan_hashes_after.get(path)
    )

    checks = _validate(
        province_cases=province_cases,
        national=national,
        weights_by_province=weights_by_province,
        handoff_rows=handoff_rows,
        zone_rows=zone_rows,
        comparison_rows=comparison_rows,
        tiers=tiers,
        context=context,
        dispatch=dispatch,
        osecan_tree_before=osecan_tree_before,
        osecan_tree_after=osecan_tree_after,
        osecan_changed_files=changed_files,
    )
    failed = sum(1 for check in checks if not check["passed"])
    if failed:
        raise ForestryOsecanHandoffV2Error(
            f"{failed} validation checks failed: "
            + "; ".join(check["check"] for check in checks if not check["passed"])
        )
    if changed_files:
        raise ForestryOsecanHandoffV2Error(
            "official OSeCAN files changed during the build: "
            + ", ".join(changed_files)
        )

    handoff_totals: dict[str, dict[str, float]] = {}
    for stream, cases in (
        (PROCESS_STREAM, PROCESS_CASES),
        (HARVEST_STREAM, HARVEST_CASES),
    ):
        handoff_totals[stream] = {}
        for case, _ in cases:
            handoff_totals[stream][case] = math.fsum(
                float(row["pj"])
                for row in handoff_rows
                if row["stream"] == stream and row["availability_case"] == case
            )

    zone_mapping = _zone_mapping_summary(zones, weights_by_province)
    weight_summary = _weight_summary(weights_by_province)

    comparison_summary = {
        (
            row["reference"]
            + "|"
            + row["comparison_scope"]
            + "|"
            + row["stream"]
            + "|"
            + row["availability_case"]
        ): {
            "case_pj": row["case_pj"],
            "reference_pj": row["reference_pj"],
            "ratio": row["ratio"],
            "headroom_pj": row["headroom_pj"],
            "reference_status": row["reference_status"],
        }
        for row in comparison_rows
        if (
            row["reference"]
            in {
                "national_supply_curve_t4_upper",
                "osecan_2050_national_supply_context",
                "bc_zonal_supply_upper_2050",
                "osecan_2050_bc_supply_context",
            }
            or row["reference"] == "osecan_2050_zone_dispatch_context"
        )
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
            "proxy_shares_explicit": True,
        },
        "zone_mapping": zone_mapping,
        "zone_allocation": {
            "province_count": len(PROVINCES),
            "zone_count": len(ZONE_ORDER),
            "allocated_province_count": len(MULTI_ZONE_PROVINCES),
            "single_zone_province_count": len(SINGLE_ZONE_PROVINCES),
            "unmapped_province_count": 0,
            "proxy_share_provinces": list(MULTI_ZONE_PROVINCES),
            "methods": weight_summary["methods"],
            "evidence_tiers": weight_summary["evidence_tiers"],
            "provinces": weight_summary["provinces"],
        },
        "record_counts": {
            "handoff_rows": len(handoff_rows),
            "zone_summary_rows": len(zone_rows),
            "zone_allocation_weight_rows": len(weight_rows),
            "comparison_rows": len(comparison_rows),
            "provinces": len(PROVINCES),
            "zones": len(ZONE_ORDER),
            "multi_zone_provinces": len(MULTI_ZONE_PROVINCES),
            "unmapped_provinces": 0,
            "process_cases": len(PROCESS_CASES),
            "harvest_cases": len(HARVEST_CASES),
        },
        "handoff_totals": handoff_totals,
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
            "case_tier": {
                f"{stream}/{case}": v1._tier_for_pj(national[(stream, case)].pj, tiers)
                for stream, cases in (
                    (PROCESS_STREAM, PROCESS_CASES),
                    (HARVEST_STREAM, HARVEST_CASES),
                )
                for case, _ in cases
            },
        },
        "osecan_context": {
            "run": DISPATCH_RUN,
            "year": int(DISPATCH_YEAR),
            "national_supply_pj": context.national_2050_supply_pj,
            "bc_supply_pj": context.bc_2050_supply_pj,
            "national_supply_pj_rounded": 152.923,
            "bc_supply_pj_rounded": 43.524,
            "bc_zonal_upper_pj": context.bc_zonal_upper_pj,
            "dispatch_source": context.dispatch_source,
            "dispatch_sha256": context.dispatch_sha256,
            "manifest_status": context.manifest_status,
            "context_is_policy_target": False,
            "zone_dispatch_context_pj": dict(dispatch.zone_dispatch_context),
        },
        "comparison": comparison_summary,
        "inputs": {
            str(path.relative_to(REPO_ROOT)): _sha256(path)
            for path in (
                PROVINCE_CURVE,
                NATIONAL_CURVE,
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
        "fallback_inputs": {
            str(OPERABLE_BY_ECOPROVINCE.relative_to(REPO_ROOT)): {
                "sha256": _sha256(OPERABLE_BY_ECOPROVINCE),
                "status": "considered_not_activated_dispatch_available",
                "used_for": "BC operable-area fallback only",
            },
            str(RSYC_CURVE_LIBRARY.relative_to(REPO_ROOT)): {
                "sha256": _sha256(RSYC_CURVE_LIBRARY),
                "status": "considered_not_activated_dispatch_available",
                "used_for": "RSYC zonal harvest/area context; no published zone crosswalk",
            },
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
            "Every multi-zone province allocation is a proxy share; no observed sub-provincial residue split exists.",
            "BC uses OSeCAN zonal supply dispatch; AB, ON, and QC use OSeCAN zonal energy dispatch as an activity proxy.",
            "Process and harvest residue are separate streams and are never summed.",
            "Process rows use the TOTAL class row only; harvest rows use the ALL row only.",
            "SUPBCW technology strings are candidate mappings, not operating assignments.",
            "The 2050 OSeCAN and supply-curve values are context, not policy targets.",
            "No official OSeCAN file or public OSeCAN surface is changed.",
            "No transport, cost, tenure, AAC, or policy claim is made.",
        ],
        "reproduce": (
            ".venv/bin/python -m "
            "workflows.national.facilities.forestry_osecan_handoff_v2"
        ),
    }

    handoff_fields = [
        "province",
        "province_name",
        "zone_id",
        "zone_name",
        "zone_mapped",
        "zone_mapping_status",
        "zone_candidates",
        "zone_mapping_basis",
        "zone_weight",
        "zone_weight_method",
        "zone_evidence_tier",
        "proxy_share",
        "allocation_status",
        "stream",
        "residue_class",
        "province_basis",
        "availability_case",
        "availability_fraction",
        "odt",
        "pj",
        "lhv_gj_per_odt",
        "supbcw_technology_suggestion",
        "supbcw_mapping",
        "supbcw_mapping_note",
        "supply_curve_tier_at_national_case",
        "supply_curve_tier_upper_pj",
        "evidence_tier",
        "rsyc_comparable",
        "model_estimate",
        "observed",
        "policy_grade",
        "official_osecan_changed",
        "caveat",
        "notes",
    ]
    weight_fields = [
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
    ]
    zone_fields = [
        "zone_id",
        "zone_name",
        "zone_mapping_status",
        "provinces_included",
        "province_count",
        "stream",
        "availability_case",
        "odt",
        "pj",
        "national_case_pj",
        "share_of_national_case",
        "share_status",
        "zone_weight_methods",
        "zone_evidence_tiers",
        "proxy_share",
        "allocation_status",
        "supbcw_technology_suggestion",
        "supbcw_mapping",
        "model_estimate",
        "observed",
        "policy_grade",
        "official_osecan_changed",
        "caveat",
    ]
    comparison_fields = [
        "comparison_scope",
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
        target_dir / "forestry_residue_handoff.csv",
        handoff_rows,
        handoff_fields,
    )
    _write_csv(
        target_dir / "zone_allocation_weights.csv",
        weight_rows,
        weight_fields,
    )
    _write_csv(target_dir / "zone_summary.csv", zone_rows, zone_fields)
    _write_csv(
        target_dir / "supply_curve_comparison.csv",
        comparison_rows,
        comparison_fields,
    )
    _write_json(target_dir / "summary.json", summary)
    (target_dir / "FORESTRY_OSECAN_HANDOFF_V2.md").write_text(
        _report(summary, weight_rows, zone_rows, comparison_rows),
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
        "fallback_inputs": summary["fallback_inputs"],
        "outputs": outputs,
        "record_counts": summary["record_counts"],
        "zone_mapping": summary["zone_mapping"],
        "zone_allocation": summary["zone_allocation"],
        "handoff_totals": summary["handoff_totals"],
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
                "handoff_rows": summary["record_counts"]["handoff_rows"],
                "zone_summary_rows": summary["record_counts"]["zone_summary_rows"],
                "process_50pct_pj": summary["handoff_totals"][PROCESS_STREAM][
                    "process_availability_050"
                ],
                "harvest_60pct_pj": summary["handoff_totals"][HARVEST_STREAM][
                    "harvest_recovery_060"
                ],
                "zones": summary["record_counts"]["zones"],
                "unmapped_provinces": summary["zone_mapping"]["unmapped_provinces"],
                "osecan_unchanged": summary["osecan_integrity"]["unchanged"],
                "validation_failures": summary["validation"]["failed_check_count"],
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
