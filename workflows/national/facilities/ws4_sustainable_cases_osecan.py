"""Build the OSeCAN handoff from the sustainable 100/150 WS4 chains.

This applies the existing WS4 origin-province handoff logic to the two
sustainable cases.  It reuses the frozen handoff-v10 province-to-zone weights,
the frozen v9 process-availability / harvest-recovery definitions, and the
frozen v5.1 supply-curve tier thresholds.  Only the residue totals differ:
they come from the sustainable 100-year WS4 schedules rather than the one-year
119 Mm3 chain.

The official OSeCAN tree is hashed before and after the build and must be
unchanged.  Handoff v10, the frozen residue curve v9, chain v5.1, and the 119
case artifacts are read-only.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from workflows.national.facilities import forestry_osecan_handoff_ws4 as handoff
from workflows.national.facilities import ws4_sustainable_cases as cases

REPO_ROOT = cases.REPO_ROOT
ARTIFACT = "forestry_osecan_handoff_ws4_sustainable_cases"
ARTIFACT_VERSION = "1.0.0"
ARTIFACT_TITLE = "Sustainable 100/150 Mm3 WS4 Forestry Residue Handoff to OSeCAN"
REPORT_NAME = "FORESTRY_OSECAN_HANDOFF_WS4_SUSTAINABLE_CASES.md"

PROVINCES = handoff.PROVINCES
ZONE_ORDER = handoff.ZONE_ORDER
PROCESS_CASES = handoff.PROCESS_CASES
HARVEST_CASES = handoff.HARVEST_CASES
CASE_FRACTIONS = handoff.CASE_FRACTIONS
ABS_TOL_ODT = handoff.ABS_TOL_ODT
ABS_TOL_PJ = handoff.ABS_TOL_PJ
ABS_TOL_WEIGHT = handoff.ABS_TOL_WEIGHT
SECONDARY_BASIS = cases.SECONDARY_BASIS
SCENARIOS = tuple(spec.key for spec in cases.CASES)


class Ws4SustainableCasesOsecanError(RuntimeError):
    """Raised when a sustainable case OSeCAN handoff fails a guard."""


def _relative(path: Path) -> str:
    return handoff._relative(path)


def _chain_files(chain_dir: Path) -> tuple[dict[str, str], str]:
    hashes: dict[str, str] = {}
    for name in (
        "sustainable_harvest_by_province.csv",
        "sustainable_product_chain.csv",
        "comparison_vs_v51.csv",
        "summary.json",
    ):
        path = chain_dir / name
        if not path.is_file():
            raise Ws4SustainableCasesOsecanError(f"case chain input is missing: {path}")
        hashes[_relative(path)] = handoff._sha256(path)
    digest = hashlib.sha256()
    for relative, value in sorted(hashes.items()):
        digest.update(f"{relative}:{value}\n".encode())
    return hashes, digest.hexdigest()


def _load_case_chain(
    spec: cases.CaseSpec, chain_dir: Path
) -> tuple[
    dict[str, dict[str, handoff.ProvinceResidue]],
    dict[str, dict[str, Any]],
    dict[str, dict[str, float]],
]:
    product_chain = chain_dir / "sustainable_product_chain.csv"
    harvest_path = chain_dir / "sustainable_harvest_by_province.csv"
    scaled_column = f"annual_{spec.key}_m3"
    harvest_rows = handoff._read_rows(
        harvest_path,
        [
            "province",
            "target_annual_m3",
            "annual_100y_m3",
            "first_period_annual_m3",
            scaled_column,
        ],
        f"sustainable {spec.key} harvest",
    )
    schedule: dict[str, dict[str, float]] = {}
    national: dict[str, float] = {}
    for row in harvest_rows:
        province = row["province"].strip().upper()
        values = {
            "achieved_m3": handoff._finite_nonnegative(
                row["annual_100y_m3"], f"{spec.key} {province} annual_100y"
            ),
            "scaled_m3": handoff._finite_nonnegative(
                row[scaled_column], f"{spec.key} {province} {scaled_column}"
            ),
            "first_period_m3": handoff._finite_nonnegative(
                row["first_period_annual_m3"],
                f"{spec.key} {province} first_period_annual",
            ),
            "target_m3": handoff._finite_nonnegative(
                row["target_annual_m3"], f"{spec.key} {province} target"
            ),
        }
        if province == "CAN":
            national = values
            continue
        if province not in PROVINCES:
            raise Ws4SustainableCasesOsecanError(
                f"{spec.key} harvest has unknown province: {province!r}"
            )
        if province in schedule:
            raise Ws4SustainableCasesOsecanError(
                f"{spec.key} harvest has duplicate province: {province!r}"
            )
        schedule[province] = values
    if set(schedule) != set(PROVINCES):
        raise Ws4SustainableCasesOsecanError(
            f"{spec.key} harvest does not cover all 13 jurisdictions"
        )
    if not national:
        raise Ws4SustainableCasesOsecanError(f"{spec.key} harvest has no CAN row")
    for column in ("achieved_m3", "scaled_m3", "first_period_m3", "target_m3"):
        expected = handoff._finite_nonnegative(
            national[column], f"{spec.key} CAN {column}"
        )
        observed = math.fsum(item[column] for item in schedule.values())
        handoff._require_close(
            observed,
            expected,
            f"{spec.key} provincial/national {column}",
            abs_tol=ABS_TOL_ODT,
        )
    by_basis: dict[str, dict[str, handoff.ProvinceResidue]] = {}
    national_rows: dict[str, dict[str, Any]] = {}
    for basis in (spec.primary_basis, SECONDARY_BASIS):
        parsed, national_row = handoff._parse_province_residues(basis, product_chain)
        by_basis[basis] = parsed
        national_rows[basis] = national_row
    return by_basis, national_rows, schedule


def _reconcile_case_chain(
    spec: cases.CaseSpec,
    by_basis: Mapping[str, Mapping[str, handoff.ProvinceResidue]],
    national_rows: Mapping[str, Mapping[str, Any]],
    schedule: Mapping[str, Mapping[str, float]],
) -> None:
    expected_columns = {
        spec.primary_basis: "scaled_m3",
        SECONDARY_BASIS: "achieved_m3",
    }
    for basis, column in expected_columns.items():
        factors = {item.scale_factor for item in by_basis[basis].values()}
        if len(factors) != 1:
            raise Ws4SustainableCasesOsecanError(
                f"{spec.key}/{basis}: expected one scale factor, got {sorted(factors)}"
            )
        factor = factors.pop()
        if not math.isclose(factor, 1.0, rel_tol=0.0, abs_tol=1e-12):
            raise Ws4SustainableCasesOsecanError(
                f"{spec.key}/{basis}: sustainable case basis must be unscaled"
            )
        for province in PROVINCES:
            expected = float(schedule[province][column]) * factor
            handoff._require_close(
                by_basis[basis][province].harvest_m3,
                expected,
                f"{spec.key}/{basis}/{province} harvest reconciliation",
                abs_tol=ABS_TOL_ODT,
            )
        total = math.fsum(item.harvest_m3 for item in by_basis[basis].values())
        expected_total = math.fsum(float(item[column]) for item in schedule.values())
        handoff._require_close(
            total,
            expected_total,
            f"{spec.key}/{basis} national harvest reconciliation",
            abs_tol=ABS_TOL_ODT,
        )
        handoff._require_close(
            total,
            float(national_rows[basis]["harvest_m3"]),
            f"{spec.key}/{basis} national row reconciliation",
            abs_tol=ABS_TOL_ODT,
        )


def _allocate_zones(
    spec: cases.CaseSpec,
    provinces_by_basis: Mapping[str, Mapping[str, handoff.ProvinceResidue]],
    weights_by_province: Mapping[str, Sequence[handoff.ZoneWeight]],
    metadata: Mapping[str, Mapping[str, Any]],
) -> tuple[list[handoff.ZoneAllocation], dict[str, float]]:
    allocations: list[handoff.ZoneAllocation] = []
    max_province_zone_gap = 0.0
    for basis in (spec.primary_basis, SECONDARY_BASIS):
        basis_provinces = provinces_by_basis[basis]
        if len(basis_provinces) != len(PROVINCES):
            raise Ws4SustainableCasesOsecanError(
                f"{spec.key}/{basis} does not have 13 province rows"
            )
        for stream, stream_cases in (
            ("process", PROCESS_CASES),
            ("harvest", HARVEST_CASES),
        ):
            for case, fraction in stream_cases:
                national_odt = math.fsum(
                    handoff._province_case(item, stream, case)[0]
                    for item in basis_provinces.values()
                )
                national_pj = math.fsum(
                    handoff._province_case(item, stream, case)[1]
                    for item in basis_provinces.values()
                )
                for zone_id in ZONE_ORDER:
                    zone_odt = 0.0
                    zone_pj = 0.0
                    for province in PROVINCES:
                        item = basis_provinces[province]
                        province_odt, province_pj = handoff._province_case(
                            item, stream, case
                        )
                        for weight in weights_by_province[province]:
                            if weight.zone_id != zone_id:
                                continue
                            zone_odt += province_odt * weight.weight
                            zone_pj += province_pj * weight.weight
                    allocations.append(
                        handoff.ZoneAllocation(
                            basis=basis,
                            zone_id=zone_id,
                            zone_name=metadata[zone_id]["zone_name"],
                            stream=stream,
                            availability_case=case,
                            availability_fraction=fraction,
                            odt=zone_odt,
                            pj=zone_pj,
                            national_odt=national_odt,
                            national_pj=national_pj,
                            provinces=metadata[zone_id]["provinces"],
                        )
                    )
        for stream, stream_cases in (
            ("process", PROCESS_CASES),
            ("harvest", HARVEST_CASES),
        ):
            for case, _fraction in stream_cases:
                by_zone: dict[str, float] = {zone_id: 0.0 for zone_id in ZONE_ORDER}
                for allocation in allocations:
                    if (
                        allocation.basis == basis
                        and allocation.stream == stream
                        and allocation.availability_case == case
                    ):
                        by_zone[allocation.zone_id] += allocation.pj
                for province in PROVINCES:
                    item = basis_provinces[province]
                    province_pj = handoff._province_case(item, stream, case)[1]
                    allocated = math.fsum(
                        province_pj * weight.weight
                        for weight in weights_by_province[province]
                    )
                    max_province_zone_gap = max(
                        max_province_zone_gap, abs(allocated - province_pj)
                    )
                handoff._require_close(
                    math.fsum(by_zone.values()),
                    math.fsum(
                        handoff._province_case(item, stream, case)[1]
                        for item in basis_provinces.values()
                    ),
                    f"{spec.key}/{basis}/{stream}/{case} zone total conservation",
                )
    return allocations, {"max_province_zone_gap_pj": max_province_zone_gap}


def _zone_fields() -> list[str]:
    return [
        "basis",
        "scenario",
        "zone_id",
        "zone_name",
        "zone_mapping_status",
        "allocation_status",
        "zone_weight_methods",
        "zone_evidence_tiers",
        "proxy_share",
        "provinces_included",
        "province_count",
        "province_basis",
        "stream",
        "availability_case",
        "availability_fraction",
        "residue_odt",
        "pj",
        "national_case_odt",
        "national_case_pj",
        "share_of_national_case",
        "supply_curve_tier_at_national_case",
        "model_estimate",
        "observed",
        "policy_grade",
        "national_claim",
        "ws4_derived",
        "sustainable_schedule",
        "official_osecan_changed",
        "chain_v51_unchanged",
        "caveat",
    ]


def _zone_rows(
    spec: cases.CaseSpec,
    allocations: Sequence[handoff.ZoneAllocation],
    metadata: Mapping[str, Mapping[str, Any]],
    tiers: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for allocation in sorted(
        allocations,
        key=lambda item: (
            (0 if item.basis == spec.primary_basis else 1),
            0 if item.stream == "process" else 1,
            CASE_FRACTIONS[item.availability_case],
            ZONE_ORDER.index(item.zone_id),
        ),
    ):
        case_tier = handoff._tier_for_pj(allocation.national_pj, tiers)
        row = handoff._zone_row(allocation, metadata, case_tier=case_tier)
        row["scenario"] = spec.key
        row["sustainable_schedule"] = "true"
        rows.append(row)
    return rows


def _tier_rows(
    spec: cases.CaseSpec,
    national_cases: Mapping[str, Mapping[tuple[str, str], tuple[float, float]]],
    v10_rows: Sequence[Mapping[str, str]],
    tiers: Mapping[str, Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    rows: list[dict[str, Any]] = []
    primary_matches = 0
    secondary_matches = 0
    positions = 0
    for basis in (spec.primary_basis, SECONDARY_BASIS):
        for stream, stream_cases in (
            ("process", PROCESS_CASES),
            ("harvest", HARVEST_CASES),
        ):
            for case, _fraction in stream_cases:
                national_odt, national_pj = national_cases[basis][(stream, case)]
                v10_row = next(
                    row
                    for row in v10_rows
                    if row["scenario"].strip() == spec.key
                    and row["stream"].strip() == stream
                    and row["case"].strip() == case
                )
                v10_pj = float(v10_row["national_pj"])
                v10_tier = v10_row["tier"].strip()
                tier = handoff._tier_for_pj(national_pj, tiers)
                frozen_tier = handoff.FROZEN_V51_TIERS[(spec.key, stream, case)]
                if tier == v10_tier == frozen_tier:
                    if basis == spec.primary_basis:
                        primary_matches += 1
                    else:
                        secondary_matches += 1
                if basis == spec.primary_basis:
                    positions += 1
                rows.append(
                    {
                        "scenario": spec.key,
                        "basis": basis,
                        "stream": stream,
                        "case": case,
                        "province_basis": "origin_province",
                        "national_odt": national_odt,
                        "national_pj": national_pj,
                        "tier": tier,
                        "v10_national_pj": v10_pj,
                        "v10_tier": v10_tier,
                        "frozen_v5_1_tier": frozen_tier,
                        "ws4_minus_v10_pj": national_pj - v10_pj,
                        "ws4_abs_delta_pj": abs(national_pj - v10_pj),
                        "tier_changed": tier != v10_tier,
                        "model_estimate": "true",
                        "observed": "false",
                        "policy_grade": "false",
                        "national_claim": "false",
                        "ws4_derived": "true",
                        "sustainable_schedule": "true",
                        "official_osecan_changed": "false",
                        "chain_v51_unchanged": "true",
                    }
                )
    return rows, {
        "positions": positions,
        "primary_matches": primary_matches,
        "secondary_matches": secondary_matches,
    }


def _combined_tier_comparison(
    national_cases_by_case: Mapping[
        str, Mapping[str, Mapping[tuple[str, str], tuple[float, float]]]
    ],
    v10_rows: Sequence[Mapping[str, str]],
    tiers: Mapping[str, Mapping[str, Any]],
    spec_by_key: Mapping[str, cases.CaseSpec],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for v10 in v10_rows:
        scenario = v10["scenario"].strip()
        stream = v10["stream"].strip()
        case = v10["case"].strip()
        v10_pj = handoff._finite_nonnegative(
            v10["national_pj"], f"v10 {scenario}/{stream}/{case}"
        )
        v10_tier = v10["tier"].strip()
        frozen_tier = handoff.FROZEN_V51_TIERS[(scenario, stream, case)]
        row: dict[str, Any] = {
            "scenario": scenario,
            "stream": stream,
            "case": case,
            "frozen_v5_1_tier": frozen_tier,
            "v10_national_pj": v10_pj,
            "v10_tier": v10_tier,
            "v10_tier_matches_frozen": v10_tier == frozen_tier,
            "ws4_basis_primary": "not_available",
            "ws4_national_pj_primary": "not_available",
            "ws4_tier_primary": "not_available",
            "ws4_minus_v10_pj_primary": "not_available",
            "ws4_abs_delta_pj_primary": "not_available",
            "ws4_tier_changed_primary": "not_available",
            "ws4_basis_secondary": "not_available",
            "ws4_national_pj_secondary": "not_available",
            "ws4_tier_secondary": "not_available",
            "ws4_minus_v10_pj_secondary": "not_available",
            "ws4_abs_delta_pj_secondary": "not_available",
            "ws4_tier_changed_secondary": "not_available",
            "comparison_status": "not_available_sustainable_ws4_119m_only",
            "v10_province_basis": "facility_province",
            "ws4_province_basis": "origin_province",
            "model_estimate": "true",
            "observed": "false",
            "policy_grade": "false",
            "national_claim": "false",
            "ws4_derived": "true",
            "sustainable_schedule": "true",
            "official_osecan_changed": "false",
            "chain_v51_unchanged": "true",
        }
        if scenario in national_cases_by_case:
            spec = spec_by_key[scenario]
            national_cases = national_cases_by_case[scenario]
            row["comparison_status"] = "compared_national_totals_origin_province"
            for basis, suffix in (
                (spec.primary_basis, "primary"),
                (SECONDARY_BASIS, "secondary"),
            ):
                _odt, ws4_pj = national_cases[basis][(stream, case)]
                ws4_tier = handoff._tier_for_pj(ws4_pj, tiers)
                delta = ws4_pj - v10_pj
                row[f"ws4_basis_{suffix}"] = basis
                row[f"ws4_national_pj_{suffix}"] = ws4_pj
                row[f"ws4_tier_{suffix}"] = ws4_tier
                row[f"ws4_minus_v10_pj_{suffix}"] = delta
                row[f"ws4_abs_delta_pj_{suffix}"] = abs(delta)
                row[f"ws4_tier_changed_{suffix}"] = ws4_tier != v10_tier
        rows.append(row)
    return rows


def _validate_outputs(
    *,
    zone_rows: Sequence[Mapping[str, Any]],
    tier_rows: Sequence[Mapping[str, Any]],
    comparison_rows: Sequence[Mapping[str, Any]],
    case_checks: Mapping[str, Mapping[str, int]],
    zone_summary: Mapping[str, Any],
    v10_check: Mapping[str, Any],
    osecan_before: str,
    osecan_after: str,
    expected_zone_rows: int,
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    handoff._check(
        checks,
        "official OSeCAN tree hash unchanged",
        osecan_before == osecan_after
        and osecan_before == handoff.EXPECTED_OSECAN_TREE_SHA256,
        (f"before={osecan_before[:16]}... after={osecan_after[:16]}..."),
    )
    handoff._check(
        checks,
        "zone weights sum to one per province",
        zone_summary["province_weight_sum_min"] >= 1.0 - ABS_TOL_WEIGHT
        and zone_summary["province_weight_sum_max"] <= 1.0 + ABS_TOL_WEIGHT,
        (
            f"min={zone_summary['province_weight_sum_min']:.15g} "
            f"max={zone_summary['province_weight_sum_max']:.15g}"
        ),
    )
    handoff._check(
        checks,
        "13 official zones and no fabricated zones",
        zone_summary["zone_count"] == 13
        and set(zone_summary["zone_ids"]) == set(ZONE_ORDER),
        f"zones={zone_summary['zone_count']}",
    )
    handoff._check(
        checks,
        "zone totals conserve national totals",
        zone_summary["max_zone_national_gap_pj"] <= ABS_TOL_PJ,
        f"max PJ gap={zone_summary['max_zone_national_gap_pj']:.12g}",
    )
    handoff._check(
        checks,
        "province zone allocations conserve province totals",
        zone_summary["max_province_zone_gap_pj"] <= ABS_TOL_PJ,
        f"max PJ gap={zone_summary['max_province_zone_gap_pj']:.12g}",
    )
    handoff._check(
        checks,
        "v10 reference 21/21 tier positions match frozen v5.1",
        v10_check["matches"] == 21 and v10_check["positions"] == 21,
        f"matches={v10_check['matches']}/{v10_check['positions']}",
    )
    for key, counts in case_checks.items():
        handoff._check(
            checks,
            f"{key} primary tier positions match v10",
            counts["primary_matches"] == counts["positions"] == 7,
            f"matches={counts['primary_matches']}/{counts['positions']}",
        )
        handoff._check(
            checks,
            f"{key} achieved tier positions match v10",
            counts["secondary_matches"] == counts["positions"] == 7,
            f"matches={counts['secondary_matches']}/{counts['positions']}",
        )
    expected_labels = {
        "model_estimate": "true",
        "observed": "false",
        "policy_grade": "false",
        "national_claim": "false",
        "ws4_derived": "true",
        "sustainable_schedule": "true",
        "official_osecan_changed": "false",
        "chain_v51_unchanged": "true",
    }
    label_ok = True
    label_detail = ""
    for name, rows in (
        ("zone rows", zone_rows),
        ("tier rows", tier_rows),
        ("tier comparison rows", comparison_rows),
    ):
        for row in rows:
            for field, expected in expected_labels.items():
                if str(row.get(field, "")).strip().lower() != expected:
                    label_ok = False
                    label_detail = f"{name}: {field}={row.get(field)!r}"
                    break
            if not label_ok:
                break
        if not label_ok:
            break
    handoff._check(checks, "row labels", label_ok, label_detail or "all labels set")
    finite_ok = True
    finite_detail = ""
    for name, rows in (
        ("zone rows", zone_rows),
        ("tier rows", tier_rows),
        ("tier comparison rows", comparison_rows),
    ):
        for row in rows:
            for field, value in row.items():
                if not isinstance(value, float):
                    continue
                if not math.isfinite(value):
                    finite_ok = False
                    finite_detail = f"{name}: non-finite {field}={value!r}"
                    break
                if field.startswith("ws4_minus_v10_pj"):
                    continue
                if value < 0.0:
                    finite_ok = False
                    finite_detail = f"{name}: negative {field}={value!r}"
                    break
            if not finite_ok:
                break
        if not finite_ok:
            break
    handoff._check(
        checks,
        "no NaN or negative residue values",
        finite_ok,
        finite_detail or "all residue values finite and non-negative",
    )
    handoff._check(
        checks,
        "zone rows complete for each basis/stream/case",
        len(zone_rows) == expected_zone_rows,
        f"rows={len(zone_rows)} expected={expected_zone_rows}",
    )
    return checks


def _zone_summary(
    zone_rows: Sequence[Mapping[str, Any]],
    allocations: Sequence[handoff.ZoneAllocation],
    weights_by_province: Mapping[str, Sequence[handoff.ZoneWeight]],
    flat_weights: Sequence[handoff.ZoneWeight],
    max_province_zone_gap_pj: float,
) -> dict[str, Any]:
    max_zone_national_gap_pj = 0.0
    max_zone_national_gap_odt = 0.0
    for allocation in allocations:
        selected = [
            item
            for item in allocations
            if item.basis == allocation.basis
            and item.stream == allocation.stream
            and item.availability_case == allocation.availability_case
        ]
        if len(selected) != len(ZONE_ORDER):
            raise Ws4SustainableCasesOsecanError(
                f"{allocation.basis}/{allocation.stream}/"
                f"{allocation.availability_case}: expected 13 zone rows"
            )
        max_zone_national_gap_pj = max(
            max_zone_national_gap_pj,
            abs(math.fsum(item.pj for item in selected) - selected[0].national_pj),
        )
        max_zone_national_gap_odt = max(
            max_zone_national_gap_odt,
            abs(math.fsum(item.odt for item in selected) - selected[0].national_odt),
        )
    return {
        "zone_ids": list(ZONE_ORDER),
        "zone_count": len(ZONE_ORDER),
        "province_count": len(PROVINCES),
        "province_weight_sum_min": min(
            math.fsum(item.weight for item in items)
            for items in weights_by_province.values()
        ),
        "province_weight_sum_max": max(
            math.fsum(item.weight for item in items)
            for items in weights_by_province.values()
        ),
        "global_weight_sum": math.fsum(item.weight for item in flat_weights),
        "multi_zone_provinces": list(handoff.MULTI_ZONE_PROVINCES),
        "zone_rows": len(zone_rows),
        "max_zone_national_gap_pj": max_zone_national_gap_pj,
        "max_zone_national_gap_odt": max_zone_national_gap_odt,
        "max_province_zone_gap_pj": max_province_zone_gap_pj,
    }


def _report(summary: Mapping[str, Any], case_dirs: Mapping[str, Path]) -> str:
    case_table = "\n".join(
        [
            "| Case | Basis | Stream | Case | National PJ | Tier | v10 PJ | v10 tier |",
            "|---|---|---|---|---:|---|---:|---|",
            *[
                "| "
                + " | ".join(
                    [
                        str(row["scenario"]),
                        str(row["basis"]),
                        str(row["stream"]),
                        str(row["case"]),
                        f"{float(row['national_pj']):.6f}",
                        str(row["tier"]),
                        f"{float(row['v10_national_pj']):.6f}",
                        str(row["v10_tier"]),
                    ]
                )
                + " |"
                for row in summary["tier_rows"]
            ],
        ]
    )
    comparison_table = "\n".join(
        [
            "| Scenario | Stream | Case | Frozen v5.1 | v10 | WS4 primary | WS4 achieved |",
            "|---|---|---|---|---|---|---|",
            *[
                "| "
                + " | ".join(
                    [
                        str(row["scenario"]),
                        str(row["stream"]),
                        str(row["case"]),
                        str(row["frozen_v5_1_tier"]),
                        str(row["v10_tier"]),
                        str(row["ws4_tier_primary"]),
                        str(row["ws4_tier_secondary"]),
                    ]
                )
                + " |"
                for row in summary["tier_comparison"]
            ],
        ]
    )
    matches = summary["cases"]
    return f"""# Sustainable 100/150 Mm3 WS4 Forestry Residue Handoff to OSeCAN

**Artifact:** `{ARTIFACT}` **Version:** {ARTIFACT_VERSION}

**Status:** {summary["status"]}. Built from the sustainable 100-year WS4 case
chains. The official OSeCAN tree, handoff v10, residue curve v9, chain v5.1,
and the 119 Mm3 case artifacts are unchanged.

**Labels:** `model_estimate=true`, `observed=false`, `policy_grade=false`,
`national_claim=false`, `ws4_derived=true`, `sustainable_schedule=true`,
`official_osecan_changed=false`, `chain_v51_unchanged=true`.

## 1. Source

- Case chains: `{_relative(Path(case_dirs["100m"]))}` and
  `{_relative(Path(case_dirs["150m"]))}`
- Zone weights: frozen handoff v10 `zone_allocation_weights.csv`
- Tier thresholds: official OSeCAN `national_supply_curve/tier_definitions.csv`
- Process cases: 0%, 25%, 50%, 75% of process residue
- Harvest cases: 0.35, 0.60, 0.75 recovery

The sustainable WS4 chains are origin-province based. Handoff v10 process
residue is facility-province based, so zone rows are not compared directly
across the two bases; only national totals and tier positions are compared.

## 2. Tier positions

{case_table}

## 3. Tier comparison

{comparison_table}

- Frozen v5.1 21/21 reference positions match handoff v10:
  `{summary["frozen_matches"]}/21`.
- 100 Mm3 primary/achieved positions match:
  `{matches["100m"]["primary_matches"]}/7`,
  `{matches["100m"]["secondary_matches"]}/7`.
- 150 Mm3 primary/achieved positions match:
  `{matches["150m"]["primary_matches"]}/7`,
  `{matches["150m"]["secondary_matches"]}/7`.
- Official OSeCAN tree unchanged: `{summary["official_unchanged"]}`
  ({summary["official_detail"]}).

## 4. Caveats

- The sustainable WS4 chains are whole-block model estimates, not AACs,
  policy results, or investment cases.
- Zone weights are frozen handoff-v10 screening proxies, not observed
  sub-provincial residue shares.
- Process and harvest residues remain separate streams; 0.60 harvest recovery
  is a reference case, not a calibrated factor.
- No official OSeCAN file, frozen v5.1 artifact, or candidate activity limit is
  changed.
"""


def build(
    *,
    case_dirs: Mapping[str, Path],
    output_dir: Path,
    case_specs: Sequence[cases.CaseSpec] | None = None,
) -> dict[str, Any]:
    specs = tuple(case_specs) if case_specs is not None else cases.CASES
    spec_by_key = {spec.key: spec for spec in specs}
    target = Path(output_dir).resolve()
    if handoff.OSECAN_DIR in target.parents or target == handoff.OSECAN_DIR:
        raise Ws4SustainableCasesOsecanError(
            "refusing to write inside the official OSeCAN tree"
        )
    for frozen_dir in (
        handoff.HANDOFF_V10_DIR,
        handoff.CURVE_V9_DIR,
        cases.chain.CHAIN_V51_DIR,
    ):
        resolved = frozen_dir.resolve()
        if target == resolved or resolved in target.parents:
            raise Ws4SustainableCasesOsecanError(
                f"refusing to write inside a frozen input tree: {frozen_dir}"
            )
    handoff.assert_no_unexpected_deletions()
    osecan_before = handoff._tree_state(handoff.OSECAN_DIR)
    if osecan_before["file_count"] != handoff.EXPECTED_OSECAN_FILE_COUNT:
        raise Ws4SustainableCasesOsecanError(
            f"official OSeCAN file count changed: {osecan_before['file_count']}"
        )
    if osecan_before["tree_sha256"] != handoff.EXPECTED_OSECAN_TREE_SHA256:
        raise Ws4SustainableCasesOsecanError(
            f"official OSeCAN tree hash changed: {osecan_before['tree_sha256']}"
        )
    frozen_before = {
        "handoff_v10": handoff._tree_state(handoff.HANDOFF_V10_DIR),
        "curve_v9": handoff._tree_state(handoff.CURVE_V9_DIR),
        "chain_v51": handoff._tree_state(cases.chain.CHAIN_V51_DIR),
    }

    crosswalk = handoff._load_crosswalk()
    weights_by_province, flat_weights = handoff._load_zone_weights(crosswalk)
    tiers = handoff._load_tiers()
    v10_rows = handoff._load_v10_tier_rows()
    v10_check = handoff._validate_v10_against_frozen(v10_rows, tiers)
    v9_totals = handoff._load_v9_case_totals()
    handoff._validate_v10_totals_against_v9(v10_rows, v9_totals)
    metadata = handoff._zone_metadata(weights_by_province)

    national_cases_by_case: dict[
        str, dict[str, dict[tuple[str, str], tuple[float, float]]]
    ] = {}
    tier_rows: list[dict[str, Any]] = []
    case_checks: dict[str, dict[str, int]] = {}
    zone_rows: list[dict[str, Any]] = []
    case_zone_summaries: list[dict[str, Any]] = []
    chain_hashes: dict[str, str] = {}
    chain_hash: str | None = None
    max_province_zone_gap_pj = 0.0
    for spec in specs:
        chain_dir = Path(case_dirs[spec.key]).resolve()
        hashes, digest = _chain_files(chain_dir)
        chain_hashes.update(hashes)
        chain_hash = digest if chain_hash is None else chain_hash
        by_basis, national_rows, schedule = _load_case_chain(spec, chain_dir)
        _reconcile_case_chain(spec, by_basis, national_rows, schedule)
        national_cases: dict[str, dict[tuple[str, str], tuple[float, float]]] = {}
        for basis in (spec.primary_basis, SECONDARY_BASIS):
            national_cases[basis] = handoff._national_cases(by_basis[basis])
            for (stream, case), (odt, pj) in national_cases[basis].items():
                expected = national_rows[basis]
                if stream == "process":
                    expected_odt = expected["process_odt"] * CASE_FRACTIONS[case]
                    expected_pj = expected["process_pj"] * CASE_FRACTIONS[case]
                else:
                    expected_odt = expected["harvest_odt"][case]
                    expected_pj = expected["harvest_pj"][case]
                handoff._require_close(
                    odt, expected_odt, f"{spec.key}/{basis}/{stream}/{case} odt"
                )
                handoff._require_close(
                    pj, expected_pj, f"{spec.key}/{basis}/{stream}/{case} pj"
                )
        national_cases_by_case[spec.key] = national_cases
        case_tier_rows, counts = _tier_rows(spec, national_cases, v10_rows, tiers)
        tier_rows.extend(case_tier_rows)
        case_checks[spec.key] = counts
        case_allocations, allocation_check = _allocate_zones(
            spec, by_basis, weights_by_province, metadata
        )
        max_province_zone_gap_pj = max(
            max_province_zone_gap_pj,
            float(allocation_check["max_province_zone_gap_pj"]),
        )
        case_zone_rows = _zone_rows(spec, case_allocations, metadata, tiers)
        zone_rows.extend(case_zone_rows)
        case_zone_summaries.append(
            _zone_summary(
                case_zone_rows,
                case_allocations,
                weights_by_province,
                flat_weights,
                float(allocation_check["max_province_zone_gap_pj"]),
            )
        )
        (target / spec.key).mkdir(parents=True, exist_ok=True)
        zone_fields = _zone_fields()
        handoff._write_csv(
            target / spec.key / "zone_process_residue.csv",
            zone_fields,
            [row for row in case_zone_rows if row["stream"] == "process"],
        )
        handoff._write_csv(
            target / spec.key / "zone_harvest_residue.csv",
            zone_fields,
            [row for row in case_zone_rows if row["stream"] == "harvest"],
        )
        handoff._write_csv(
            target / spec.key / "case_zone_summary.csv",
            zone_fields,
            case_zone_rows,
        )
        handoff._write_csv(
            target / spec.key / "case_tier_positions.csv",
            [
                "scenario",
                "basis",
                "stream",
                "case",
                "province_basis",
                "national_odt",
                "national_pj",
                "tier",
                "v10_national_pj",
                "v10_tier",
                "frozen_v5_1_tier",
                "ws4_minus_v10_pj",
                "ws4_abs_delta_pj",
                "tier_changed",
                "model_estimate",
                "observed",
                "policy_grade",
                "national_claim",
                "ws4_derived",
                "sustainable_schedule",
                "official_osecan_changed",
                "chain_v51_unchanged",
            ],
            case_tier_rows,
        )

    comparison_rows = _combined_tier_comparison(
        national_cases_by_case, v10_rows, tiers, spec_by_key
    )
    tier_fields = [
        "scenario",
        "basis",
        "stream",
        "case",
        "province_basis",
        "national_odt",
        "national_pj",
        "tier",
        "v10_national_pj",
        "v10_tier",
        "frozen_v5_1_tier",
        "ws4_minus_v10_pj",
        "ws4_abs_delta_pj",
        "tier_changed",
        "model_estimate",
        "observed",
        "policy_grade",
        "national_claim",
        "ws4_derived",
        "sustainable_schedule",
        "official_osecan_changed",
        "chain_v51_unchanged",
    ]
    handoff._write_csv(target / "case_tier_positions.csv", tier_fields, tier_rows)
    comparison_fields = [
        "scenario",
        "stream",
        "case",
        "frozen_v5_1_tier",
        "v10_national_pj",
        "v10_tier",
        "v10_tier_matches_frozen",
        "ws4_basis_primary",
        "ws4_national_pj_primary",
        "ws4_tier_primary",
        "ws4_minus_v10_pj_primary",
        "ws4_abs_delta_pj_primary",
        "ws4_tier_changed_primary",
        "ws4_basis_secondary",
        "ws4_national_pj_secondary",
        "ws4_tier_secondary",
        "ws4_minus_v10_pj_secondary",
        "ws4_abs_delta_pj_secondary",
        "ws4_tier_changed_secondary",
        "comparison_status",
        "v10_province_basis",
        "ws4_province_basis",
        "model_estimate",
        "observed",
        "policy_grade",
        "national_claim",
        "ws4_derived",
        "sustainable_schedule",
        "official_osecan_changed",
        "chain_v51_unchanged",
    ]
    handoff._write_csv(
        target / "tier_comparison_vs_v10.csv",
        comparison_fields,
        comparison_rows,
    )
    zone_fields = _zone_fields()
    handoff._write_csv(
        target / "zone_process_residue.csv",
        zone_fields,
        [row for row in zone_rows if row["stream"] == "process"],
    )
    handoff._write_csv(
        target / "zone_harvest_residue.csv",
        zone_fields,
        [row for row in zone_rows if row["stream"] == "harvest"],
    )
    handoff._write_csv(target / "case_zone_summary.csv", zone_fields, zone_rows)

    if len(case_zone_summaries) != len(specs):
        raise Ws4SustainableCasesOsecanError("zone summary is missing a case")
    zone_summary: dict[str, Any] = {
        "zone_ids": list(ZONE_ORDER),
        "zone_count": len(ZONE_ORDER),
        "province_count": len(PROVINCES),
        "province_weight_sum_min": min(
            item["province_weight_sum_min"] for item in case_zone_summaries
        ),
        "province_weight_sum_max": max(
            item["province_weight_sum_max"] for item in case_zone_summaries
        ),
        "global_weight_sum": math.fsum(item.weight for item in flat_weights),
        "multi_zone_provinces": list(handoff.MULTI_ZONE_PROVINCES),
        "zone_rows": len(zone_rows),
        "max_zone_national_gap_pj": max(
            item["max_zone_national_gap_pj"] for item in case_zone_summaries
        ),
        "max_zone_national_gap_odt": max(
            item["max_zone_national_gap_odt"] for item in case_zone_summaries
        ),
        "max_province_zone_gap_pj": max_province_zone_gap_pj,
        "cases": {
            spec.key: case_zone_summaries[index]
            for index, spec in enumerate(specs)
        },
    }
    expected_zone_rows = (
        len(specs)
        * 2
        * len(ZONE_ORDER)
        * (len(PROCESS_CASES) + len(HARVEST_CASES))
    )

    osecan_after = handoff._tree_state(handoff.OSECAN_DIR)
    frozen_after = {
        "handoff_v10": handoff._tree_state(handoff.HANDOFF_V10_DIR),
        "curve_v9": handoff._tree_state(handoff.CURVE_V9_DIR),
        "chain_v51": handoff._tree_state(cases.chain.CHAIN_V51_DIR),
    }
    for name, value in frozen_before.items():
        if value != frozen_after[name]:
            raise Ws4SustainableCasesOsecanError(
                f"frozen input tree changed during build: {name}"
            )
    checks = _validate_outputs(
        zone_rows=zone_rows,
        tier_rows=tier_rows,
        comparison_rows=comparison_rows,
        case_checks=case_checks,
        zone_summary=zone_summary,
        v10_check=v10_check,
        osecan_before=osecan_before["tree_sha256"],
        osecan_after=osecan_after["tree_sha256"],
        expected_zone_rows=expected_zone_rows,
    )
    failed = [check for check in checks if not check["passed"]]
    if failed:
        raise Ws4SustainableCasesOsecanError(
            "validation checks failed: " + ", ".join(check["check"] for check in failed)
        )
    official_unchanged = (
        osecan_before["tree_sha256"] == osecan_after["tree_sha256"]
        and osecan_after["tree_sha256"] == handoff.EXPECTED_OSECAN_TREE_SHA256
    )
    status = (
        "pass"
        if all(
            counts["primary_matches"] == counts["positions"] == 7
            and counts["secondary_matches"] == counts["positions"] == 7
            for counts in case_checks.values()
        )
        else "fail"
    )
    status_text = (
        "Rebuilt from the sustainable 100/150 WS4 case chains on the "
        "origin-province basis using the frozen handoff-v10 zone weights. "
        f"Frozen v5.1 {v10_check['matches']}/21 positions match handoff v10; "
        "100 Mm3 primary/achieved "
        f"{case_checks['100m']['primary_matches']}/7, "
        f"{case_checks['100m']['secondary_matches']}/7; 150 Mm3 "
        f"{case_checks['150m']['primary_matches']}/7, "
        f"{case_checks['150m']['secondary_matches']}/7. Official OSeCAN is "
        f"{'unchanged' if official_unchanged else 'CHANGED'}."
    )
    generated = datetime.now(UTC).isoformat(timespec="seconds")
    summary: dict[str, Any] = {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": generated,
        "status": status,
        "title": ARTIFACT_TITLE,
        "labels": handoff.SUMMARY_LABELS | {"sustainable_schedule": True},
        "chain_hashes": chain_hashes,
        "frozen_matches": int(v10_check["matches"]),
        "frozen_21_of_21": v10_check["matches"] == 21,
        "official_unchanged": official_unchanged,
        "official_detail": (
            f"{osecan_after['file_count']} files, "
            f"{osecan_after['tree_sha256'][:16]}..., changed_files=0"
        ),
        "cases": {
            spec.key: {
                "positions": case_checks[spec.key]["positions"],
                "primary_basis": spec.primary_basis,
                "primary_matches": case_checks[spec.key]["primary_matches"],
                "secondary_matches": case_checks[spec.key]["secondary_matches"],
                "tier_positions": [row for row in tier_rows if row["case"] == spec.key],
            }
            for spec in specs
        },
        "tier_rows": tier_rows,
        "tier_comparison": comparison_rows,
        "zone_summary": zone_summary,
        "national_totals": [
            {
                "case_key": spec.key,
                "basis": basis,
                "stream": stream,
                "availability_case": case,
                "odt": national_cases_by_case[spec.key][basis][(stream, case)][0],
                "pj": national_cases_by_case[spec.key][basis][(stream, case)][1],
            }
            for spec in specs
            for basis in (spec.primary_basis, SECONDARY_BASIS)
            for stream, stream_cases in (
                ("process", PROCESS_CASES),
                ("harvest", HARVEST_CASES),
            )
            for case, _fraction in stream_cases
        ],
        "validation": {"checks": checks},
        "status_text": status_text,
        "d_status": {
            "checked_repositories": ["parent", "data"],
            "no_unexpected_deletions": True,
        },
    }
    handoff._write_json(target / "summary.json", summary)
    (target / REPORT_NAME).write_text(
        _report(
            {**summary, "tier_rows": tier_rows, "tier_comparison": comparison_rows},
            case_dirs,
        ),
        encoding="utf-8",
    )
    outputs = {
        path.name: {"bytes": path.stat().st_size, "sha256": handoff._sha256(path)}
        for path in sorted(target.iterdir())
        if path.is_file() and path.name != "manifest.json"
    }
    manifest = {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": generated,
        "title": ARTIFACT_TITLE,
        "labels": summary["labels"],
        "cases": {
            spec.key: {
                "chain_dir": _relative(Path(case_dirs[spec.key])),
                "primary_basis": spec.primary_basis,
            }
            for spec in specs
        },
        "chain_hashes": chain_hashes,
        "inputs": {
            handoff._relative(handoff.ZONE_WEIGHTS): handoff._sha256(
                handoff.ZONE_WEIGHTS
            ),
            handoff._relative(handoff.V10_TIER_POSITIONS): handoff._sha256(
                handoff.V10_TIER_POSITIONS
            ),
            handoff._relative(handoff.OSECAN_TIER_DEFINITIONS): handoff._sha256(
                handoff.OSECAN_TIER_DEFINITIONS
            ),
        },
        "outputs": outputs,
        "validation": {"checks": checks},
        "d_status": summary["d_status"],
    }
    handoff._write_json(target / "manifest.json", manifest)
    return summary


def main() -> None:
    import json

    summary = build(
        case_dirs={spec.key: spec.chain_dir for spec in cases.CASES},
        output_dir=cases.CASES_ROOT / "osecan",
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
