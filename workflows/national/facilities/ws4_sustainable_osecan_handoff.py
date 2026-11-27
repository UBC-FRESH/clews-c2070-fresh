"""Build the OSeCAN handoff from the sustainable WS4 chain.

This is the existing WS4 origin-province handoff logic applied to the
sustainable 119 Mm3 chain.  It reuses the frozen handoff-v10 province-to-zone
weights and the frozen v9 process-availability / harvest-recovery definitions.
Only the residue totals differ: they come from the sustainable 100-year WS4
schedule rather than the one-year 119 Mm3 chain.

The official OSeCAN tree is hashed before and after the build and must be
unchanged.  Handoff v10 and the frozen residue curve v9 are read-only.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from workflows.national.facilities import forestry_osecan_handoff_ws4 as handoff
from workflows.national.facilities import ws4_sustainable_119m as sustainable

REPO_ROOT = Path(__file__).resolve().parents[3]
CHAIN_DIR = sustainable.CHAIN_DIR
DEFAULT_OUTPUT_DIR = CHAIN_DIR / "osecan"

ARTIFACT = "forestry_osecan_handoff_ws4_sustainable"
ARTIFACT_VERSION = "1.0.0"
ARTIFACT_TITLE = "Sustainable 119 Mm3 WS4 Forestry Residue Handoff to OSeCAN"
REPORT_NAME = "FORESTRY_OSECAN_HANDOFF_WS4_SUSTAINABLE.md"

PROVINCES = handoff.PROVINCES
ZONE_ORDER = handoff.ZONE_ORDER
PROCESS_CASES = handoff.PROCESS_CASES
HARVEST_CASES = handoff.HARVEST_CASES
CASE_FRACTIONS = handoff.CASE_FRACTIONS
CONTROL_TOTAL_M3 = handoff.CONTROL_TOTAL_M3
ABS_TOL_ODT = handoff.ABS_TOL_ODT
ABS_TOL_PJ = handoff.ABS_TOL_PJ
ABS_TOL_WEIGHT = handoff.ABS_TOL_WEIGHT
BASES: tuple[str, str] = sustainable.BASES
PRIMARY_BASIS, SECONDARY_BASIS = BASES
SCENARIO = "119m"
CHAIN_INPUT_FILES = (
    "sustainable_harvest_by_province.csv",
    "sustainable_product_chain.csv",
    "comparison_vs_v51.csv",
    "summary.json",
)


class Ws4SustainableOsecanError(RuntimeError):
    """Raised when the sustainable WS4 OSeCAN handoff fails a guard."""


def _relative(path: Path) -> str:
    return handoff._relative(path)


def _load_chain(chain_dir: Path) -> tuple[
    dict[str, dict[str, handoff.ProvinceResidue]],
    dict[str, dict[str, Any]],
    dict[str, dict[str, float]],
]:
    product_chain = chain_dir / "sustainable_product_chain.csv"
    harvest_path = chain_dir / "sustainable_harvest_by_province.csv"
    harvest_rows = handoff._read_rows(
        harvest_path,
        ["province", "annual_100y_m3", "annual_119m_m3", "target_annual_m3"],
        "sustainable harvest",
    )
    schedule: dict[str, dict[str, float]] = {}
    for row in harvest_rows:
        province = row["province"].strip().upper()
        if province == "CAN":
            continue
        if province not in PROVINCES:
            raise Ws4SustainableOsecanError(
                f"sustainable harvest has unknown province: {province!r}"
            )
        schedule[province] = {
            "achieved_m3": float(row["annual_100y_m3"]),
            "scaled_m3": float(row["annual_119m_m3"]),
            "target_m3": float(row["target_annual_m3"]),
        }
    if set(schedule) != set(PROVINCES):
        raise Ws4SustainableOsecanError(
            "sustainable harvest does not cover all 13 jurisdictions"
        )
    by_basis: dict[str, dict[str, handoff.ProvinceResidue]] = {}
    national_rows: dict[str, dict[str, Any]] = {}
    for basis in BASES:
        parsed, national = handoff._parse_province_residues(basis, product_chain)
        by_basis[basis] = parsed
        national_rows[basis] = national
    return by_basis, national_rows, schedule


def _reconcile_chain(
    by_basis: Mapping[str, Mapping[str, handoff.ProvinceResidue]],
    schedule: Mapping[str, Mapping[str, float]],
) -> None:
    for basis in BASES:
        factors = {item.scale_factor for item in by_basis[basis].values()}
        if len(factors) != 1:
            raise Ws4SustainableOsecanError(
                f"{basis}: expected one national sustainable scale factor, got {sorted(factors)}"
            )
        factor = factors.pop()
        if basis == SECONDARY_BASIS:
            handoff._require_close(
                factor, 1.0, "sustainable achieved scale factor", abs_tol=1e-12
            )
        for province in PROVINCES:
            expected = float(schedule[province]["achieved_m3"]) * factor
            handoff._require_close(
                by_basis[basis][province].harvest_m3,
                expected,
                f"{basis}/{province} harvest schedule reconciliation",
                abs_tol=ABS_TOL_ODT,
            )
    scaled_total = math.fsum(
        item.harvest_m3 for item in by_basis[PRIMARY_BASIS].values()
    )
    achieved_total = math.fsum(
        item.harvest_m3 for item in by_basis[SECONDARY_BASIS].values()
    )
    handoff._require_close(
        scaled_total,
        CONTROL_TOTAL_M3,
        "sustainable scaled harvest control total",
        abs_tol=ABS_TOL_ODT,
    )
    handoff._require_close(
        achieved_total,
        math.fsum(float(item["achieved_m3"]) for item in schedule.values()),
        "sustainable achieved harvest total",
        abs_tol=ABS_TOL_ODT,
    )


def _chain_input_hashes(chain_dir: Path) -> tuple[dict[str, str], str]:
    hashes: dict[str, str] = {}
    for name in CHAIN_INPUT_FILES:
        path = chain_dir / name
        if not path.is_file():
            raise Ws4SustainableOsecanError(f"chain input is missing: {path}")
        hashes[_relative(path)] = handoff._sha256(path)
    digest = hashlib.sha256()
    for relative, value in sorted(hashes.items()):
        digest.update(f"{relative}:{value}\n".encode())
    return hashes, digest.hexdigest()


def _allocate_zones(
    provinces_by_basis: Mapping[str, Mapping[str, handoff.ProvinceResidue]],
    weights_by_province: Mapping[str, Sequence[handoff.ZoneWeight]],
    metadata: Mapping[str, Mapping[str, Any]],
) -> tuple[list[handoff.ZoneAllocation], dict[str, float]]:
    allocations: list[handoff.ZoneAllocation] = []
    max_province_zone_gap = 0.0
    for basis in BASES:
        basis_provinces = provinces_by_basis.get(basis, {})
        if len(basis_provinces) != len(PROVINCES):
            raise Ws4SustainableOsecanError(
                f"basis {basis} does not have 13 province rows"
            )
        for stream, cases in (
            ("process", PROCESS_CASES),
            ("harvest", HARVEST_CASES),
        ):
            for case, fraction in cases:
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
        for stream, cases in (
            ("process", PROCESS_CASES),
            ("harvest", HARVEST_CASES),
        ):
            for case, _fraction in cases:
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
                    f"{basis}/{stream}/{case} zone total conservation",
                )
    return allocations, {"max_province_zone_gap_pj": max_province_zone_gap}


def _zone_summary(
    zone_rows: Sequence[Mapping[str, Any]],
    allocations: Sequence[handoff.ZoneAllocation],
    weights_by_province: Mapping[str, Sequence[handoff.ZoneWeight]],
    flat_weights: Sequence[handoff.ZoneWeight],
    allocation_check: Mapping[str, float],
    national_cases: Mapping[str, Mapping[tuple[str, str], tuple[float, float]]],
) -> dict[str, Any]:
    max_zone_national_gap_pj = 0.0
    max_zone_national_gap_odt = 0.0
    for basis in BASES:
        for stream, cases in (
            ("process", PROCESS_CASES),
            ("harvest", HARVEST_CASES),
        ):
            for case, _fraction in cases:
                selected = [
                    allocation
                    for allocation in allocations
                    if allocation.basis == basis
                    and allocation.stream == stream
                    and allocation.availability_case == case
                ]
                if len(selected) != len(ZONE_ORDER):
                    raise Ws4SustainableOsecanError(
                        f"{basis}/{stream}/{case}: expected 13 zone rows, "
                        f"found {len(selected)}"
                    )
                max_zone_national_gap_pj = max(
                    max_zone_national_gap_pj,
                    abs(
                        math.fsum(item.pj for item in selected)
                        - selected[0].national_pj
                    ),
                )
                max_zone_national_gap_odt = max(
                    max_zone_national_gap_odt,
                    abs(
                        math.fsum(item.odt for item in selected)
                        - selected[0].national_odt
                    ),
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
        "zone_rows_expected": len(BASES)
        * len(ZONE_ORDER)
        * (len(PROCESS_CASES) + len(HARVEST_CASES)),
        "zone_rows_per_case": len(ZONE_ORDER),
        "max_zone_national_gap_pj": max_zone_national_gap_pj,
        "max_zone_national_gap_odt": max_zone_national_gap_odt,
        "max_province_zone_gap_pj": float(
            allocation_check["max_province_zone_gap_pj"]
        ),
    }


def _tier_comparison_rows(
    v10_rows: Sequence[Mapping[str, str]],
    tiers: Mapping[str, Mapping[str, Any]],
    national_cases: Mapping[
        str, Mapping[tuple[str, str], tuple[float, float]]
    ],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    ws4_tiers: dict[tuple[str, str, str], str] = {}
    for basis in BASES:
        for stream, cases in (
            ("process", PROCESS_CASES),
            ("harvest", HARVEST_CASES),
        ):
            for case, _fraction in cases:
                _odt, pj = national_cases[basis][(stream, case)]
                ws4_tiers[(basis, stream, case)] = handoff._tier_for_pj(pj, tiers)

    rows: list[dict[str, Any]] = []
    primary_matches = 0
    secondary_matches = 0
    compared = 0
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
            "ws4_basis_primary": PRIMARY_BASIS if scenario == SCENARIO else "not_available",
            "ws4_national_pj_primary": "not_available",
            "ws4_tier_primary": "not_available",
            "ws4_minus_v10_pj_primary": "not_available",
            "ws4_abs_delta_pj_primary": "not_available",
            "ws4_tier_changed_primary": "not_available",
            "ws4_basis_secondary": SECONDARY_BASIS if scenario == SCENARIO else "not_available",
            "ws4_national_pj_secondary": "not_available",
            "ws4_tier_secondary": "not_available",
            "ws4_minus_v10_pj_secondary": "not_available",
            "ws4_abs_delta_pj_secondary": "not_available",
            "ws4_tier_changed_secondary": "not_available",
            "comparison_status": (
                "compared_national_totals_origin_province"
                if scenario == SCENARIO
                else "not_available_sustainable_ws4_119m_only"
            ),
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
        if scenario == SCENARIO:
            compared += 1
            for basis, suffix, counter in (
                (PRIMARY_BASIS, "primary", "primary"),
                (SECONDARY_BASIS, "secondary", "secondary"),
            ):
                _odt, ws4_pj = national_cases[basis][(stream, case)]
                ws4_tier = ws4_tiers[(basis, stream, case)]
                delta = ws4_pj - v10_pj
                row[f"ws4_national_pj_{suffix}"] = ws4_pj
                row[f"ws4_tier_{suffix}"] = ws4_tier
                row[f"ws4_minus_v10_pj_{suffix}"] = delta
                row[f"ws4_abs_delta_pj_{suffix}"] = abs(delta)
                row[f"ws4_tier_changed_{suffix}"] = ws4_tier != v10_tier
                if ws4_tier == v10_tier:
                    if counter == "primary":
                        primary_matches += 1
                    else:
                        secondary_matches += 1
        rows.append(row)
    return rows, {
        "frozen_v5_1_positions": len(v10_rows),
        "v10_positions_matching_frozen_v5_1": sum(
            1 for row in rows if row["v10_tier_matches_frozen"]
        ),
        "ws4_positions_available": compared,
        "ws4_primary_tier_matches": primary_matches,
        "ws4_secondary_tier_matches": secondary_matches,
        "ws4_primary_tier_mismatches": compared - primary_matches,
        "ws4_secondary_tier_mismatches": compared - secondary_matches,
        "note": (
            "Frozen v5.1 has 21 tier positions across 100/119/150 Mm3. The "
            "sustainable WS4 chain is the 119 Mm3 control only, so only its 7 "
            "positions per basis are like-for-like; the 100/150 reference "
            "positions are reported as not available, not fabricated."
        ),
    }


def _validate_outputs(
    zone_rows: Sequence[Mapping[str, Any]],
    tier_rows: Sequence[Mapping[str, Any]],
    comparison_rows: Sequence[Mapping[str, Any]],
    zone_summary: Mapping[str, Any],
    v10_check: Mapping[str, Any],
    tier_check: Mapping[str, Any],
    osecan_before: str,
    osecan_after: str,
    osecan_changed_files: Sequence[str],
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    handoff._check(
        checks,
        "official OSeCAN tree hash unchanged",
        osecan_before == osecan_after
        and not osecan_changed_files
        and osecan_before == handoff.EXPECTED_OSECAN_TREE_SHA256,
        (
            f"before={osecan_before[:16]}... after={osecan_after[:16]}... "
            f"changed_files={len(osecan_changed_files)}"
        ),
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
        (
            f"max PJ gap={zone_summary['max_zone_national_gap_pj']:.12g}; "
            f"max ODT gap={zone_summary['max_zone_national_gap_odt']:.12g}"
        ),
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
    handoff._check(
        checks,
        "sustainable 119m primary tier positions match v10",
        tier_check["ws4_primary_tier_matches"]
        == tier_check["ws4_positions_available"]
        == 7,
        (
            f"matches={tier_check['ws4_primary_tier_matches']}/"
            f"{tier_check['ws4_positions_available']}"
        ),
    )
    handoff._check(
        checks,
        "sustainable 119m achieved tier positions match v10",
        tier_check["ws4_secondary_tier_matches"]
        == tier_check["ws4_positions_available"]
        == 7,
        (
            f"matches={tier_check['ws4_secondary_tier_matches']}/"
            f"{tier_check['ws4_positions_available']}"
        ),
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
        zone_summary["zone_rows_expected"] == len(zone_rows)
        and zone_summary["zone_rows_per_case"] == 13,
        (
            f"rows={len(zone_rows)} expected={zone_summary['zone_rows_expected']} "
            f"per_case={zone_summary['zone_rows_per_case']}"
        ),
    )
    return checks


def _report(
    summary: Mapping[str, Any],
    tier_rows: Sequence[Mapping[str, Any]],
    comparison_rows: Sequence[Mapping[str, Any]],
) -> str:
    tier_table = "\n".join(
        [
            "| Basis | Stream | Case | National PJ | Tier | v10 PJ | v10 tier |",
            "|---|---|---|---:|---|---:|---|",
            *[
                "| "
                + " | ".join(
                    [
                        str(row["basis"]),
                        str(row["stream"]),
                        str(row["case"]),
                        f"{float(row['national_pj']):.6f}",
                        str(row["tier"]),
                        f"{float(row['v10_119_national_pj']):.6f}",
                        str(row["v10_119_tier"]),
                    ]
                )
                + " |"
                for row in tier_rows
            ],
        ]
    )
    comparison_table = "\n".join(
        [
            "| Scenario | Stream | Case | Frozen v5.1 | v10 | Sustainable primary | Sustainable achieved |",
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
                for row in comparison_rows
            ],
        ]
    )
    return f"""# Sustainable 119 Mm3 WS4 Forestry Residue Handoff to OSeCAN

**Artifact:** `{ARTIFACT}` **Version:** {ARTIFACT_VERSION}

**Status:** pass. Built from the sustainable 100-year WS4 chain totals. The
official OSeCAN tree, handoff v10, residue curve v9 and frozen chain v5.1 are
unchanged.

**Labels:** `model_estimate=true`, `observed=false`, `policy_grade=false`,
`national_claim=false`, `ws4_derived=true`, `sustainable_schedule=true`,
`official_osecan_changed=false`, `chain_v51_unchanged=true`.

## 1. Source

- Chain: `{summary['chain_dir']}`
- Zone weights: frozen handoff v10 `zone_allocation_weights.csv`
- Tier thresholds: official OSeCAN `national_supply_curve/tier_definitions.csv`
- Process cases: 0%, 25%, 50%, 75% of process residue
- Harvest cases: 0.35, 0.60, 0.75 recovery

The sustainable WS4 chain is origin-province based. Handoff v10 process
residue is facility-province based, so zone rows are not compared directly
across the two bases; only national totals and tier positions are compared.

## 2. 119 Mm3 tier positions

{tier_table}

## 3. Tier comparison

{comparison_table}

- Frozen v5.1 21/21 reference positions match handoff v10:
  `{summary['frozen_matches']}/21`.
- Sustainable 119 Mm3 primary positions match v10:
  `{summary['primary_matches']}/7`.
- Sustainable 119 Mm3 achieved positions match v10:
  `{summary['secondary_matches']}/7`.
- Official OSeCAN tree unchanged: `{summary['official_unchanged']}`
  ({summary['official_detail']}).

## 4. Caveats

- The sustainable WS4 chain is a whole-block model estimate, not an AAC,
  policy result, or investment case.
- Zone weights are frozen handoff-v10 screening proxies, not observed
  sub-provincial residue shares.
- Process and harvest residues remain separate streams; 0.60 harvest recovery
  is a reference case, not a calibrated factor.
- No official OSeCAN file, frozen v5.1 artifact, or candidate activity limit is
  changed.
"""


def build(
    *,
    chain_dir: Path | None = None,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    source = (Path(chain_dir) if chain_dir else CHAIN_DIR).resolve()
    target = (Path(output_dir) if output_dir else DEFAULT_OUTPUT_DIR).resolve()
    if handoff.OSECAN_DIR in target.parents or target == handoff.OSECAN_DIR:
        raise Ws4SustainableOsecanError(
            "refusing to write inside the official OSeCAN tree"
        )
    for frozen_dir in (handoff.HANDOFF_V10_DIR, handoff.CURVE_V9_DIR):
        resolved = frozen_dir.resolve()
        if target == resolved or resolved in target.parents:
            raise Ws4SustainableOsecanError(
                f"refusing to write inside a frozen input tree: {frozen_dir}"
            )
    handoff.assert_no_unexpected_deletions()

    osecan_before = handoff._tree_state(handoff.OSECAN_DIR)
    if osecan_before["file_count"] != handoff.EXPECTED_OSECAN_FILE_COUNT:
        raise Ws4SustainableOsecanError(
            "official OSeCAN file count changed: "
            f"{osecan_before['file_count']}"
        )
    if osecan_before["tree_sha256"] != handoff.EXPECTED_OSECAN_TREE_SHA256:
        raise Ws4SustainableOsecanError(
            "official OSeCAN tree hash changed: "
            f"{osecan_before['tree_sha256']}"
        )
    frozen_before = {
        "handoff_v10": handoff._tree_state(handoff.HANDOFF_V10_DIR),
        "curve_v9": handoff._tree_state(handoff.CURVE_V9_DIR),
    }

    crosswalk = handoff._load_crosswalk()
    weights_by_province, flat_weights = handoff._load_zone_weights(crosswalk)
    by_basis, national_rows, schedule = _load_chain(source)
    chain_hashes, chain_hash = _chain_input_hashes(source)
    _reconcile_chain(by_basis, schedule)

    national_cases: dict[str, dict[tuple[str, str], tuple[float, float]]] = {}
    for basis in BASES:
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
                odt, expected_odt, f"{basis}/{stream}/{case} national odt"
            )
            handoff._require_close(
                pj, expected_pj, f"{basis}/{stream}/{case} national pj"
            )

    tiers = handoff._load_tiers()
    v10_rows = handoff._load_v10_tier_rows()
    v10_check = handoff._validate_v10_against_frozen(v10_rows, tiers)
    v9_totals = handoff._load_v9_case_totals()
    handoff._validate_v10_totals_against_v9(v10_rows, v9_totals)

    metadata = handoff._zone_metadata(weights_by_province)
    allocations, allocation_check = _allocate_zones(
        by_basis, weights_by_province, metadata
    )

    tier_rows: list[dict[str, Any]] = []
    for basis in BASES:
        for stream, cases in (
            ("process", PROCESS_CASES),
            ("harvest", HARVEST_CASES),
        ):
            for case, _fraction in cases:
                national_pj = national_cases[basis][(stream, case)][1]
                v10_row = next(
                    row
                    for row in v10_rows
                    if row["scenario"] == SCENARIO
                    and row["stream"] == stream
                    and row["case"] == case
                )
                v10_pj = float(v10_row["national_pj"])
                v10_tier = v10_row["tier"]
                tier = handoff._tier_for_pj(national_pj, tiers)
                tier_rows.append(
                    {
                        "basis": basis,
                        "scenario": SCENARIO,
                        "stream": stream,
                        "case": case,
                        "province_basis": "origin_province",
                        "national_odt": national_cases[basis][(stream, case)][0],
                        "national_pj": national_pj,
                        "tier": tier,
                        "v10_119_national_pj": v10_pj,
                        "v10_119_tier": v10_tier,
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

    comparison_rows, tier_check = _tier_comparison_rows(
        v10_rows, tiers, national_cases
    )

    zone_rows: list[dict[str, Any]] = []
    for basis in BASES:
        for allocation in sorted(
            allocations,
            key=lambda item: (
                BASES.index(item.basis),
                0 if item.stream == "process" else 1,
                CASE_FRACTIONS[item.availability_case],
                ZONE_ORDER.index(item.zone_id),
            ),
        ):
            if allocation.basis != basis:
                continue
            case_tier = handoff._tier_for_pj(allocation.national_pj, tiers)
            row = handoff._zone_row(allocation, metadata, case_tier=case_tier)
            row["sustainable_schedule"] = "true"
            zone_rows.append(row)

    zone_summary = _zone_summary(
        zone_rows,
        allocations,
        weights_by_province,
        flat_weights,
        allocation_check,
        national_cases,
    )

    target.mkdir(parents=True, exist_ok=True)
    zone_fields = [
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
    tier_fields = [
        "basis",
        "scenario",
        "stream",
        "case",
        "province_basis",
        "national_odt",
        "national_pj",
        "tier",
        "v10_119_national_pj",
        "v10_119_tier",
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
    handoff._write_csv(
        target / "case_tier_positions.csv", tier_fields, tier_rows
    )
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

    osecan_after = handoff._tree_state(handoff.OSECAN_DIR)
    changed_files = (
        []
        if osecan_before["tree_sha256"] == osecan_after["tree_sha256"]
        else ["<official OSeCAN tree changed>"]
    )
    frozen_after = {
        "handoff_v10": handoff._tree_state(handoff.HANDOFF_V10_DIR),
        "curve_v9": handoff._tree_state(handoff.CURVE_V9_DIR),
    }
    for name, value in frozen_before.items():
        if value != frozen_after[name]:
            raise Ws4SustainableOsecanError(
                f"frozen input tree changed during build: {name}"
            )

    checks = _validate_outputs(
        zone_rows,
        tier_rows,
        comparison_rows,
        zone_summary,
        v10_check,
        tier_check,
        osecan_before["tree_sha256"],
        osecan_after["tree_sha256"],
        changed_files,
    )
    failed = [check for check in checks if not check["passed"]]
    if failed:
        raise Ws4SustainableOsecanError(
            "validation checks failed: "
            + ", ".join(check["check"] for check in failed)
        )

    primary_matches = int(tier_check["ws4_primary_tier_matches"])
    secondary_matches = int(tier_check["ws4_secondary_tier_matches"])
    frozen_matches = int(v10_check["matches"])
    official_unchanged = (
        osecan_before["tree_sha256"] == osecan_after["tree_sha256"]
        and not changed_files
        and osecan_after["tree_sha256"] == handoff.EXPECTED_OSECAN_TREE_SHA256
    )
    status_text = (
        "Rebuilt from the sustainable 100-year WS4 chain on the origin-province "
        "basis using the frozen handoff-v10 zone weights. "
        f"Frozen v5.1 {frozen_matches}/21 positions match handoff v10; the "
        f"sustainable 119 Mm3 primary basis matches {primary_matches}/7 and the "
        f"achieved basis {secondary_matches}/7. Official OSeCAN is unchanged."
    )
    generated = datetime.now(UTC).isoformat(timespec="seconds")
    summary: dict[str, Any] = {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": generated,
        "status": "pass",
        "title": ARTIFACT_TITLE,
        "labels": handoff.SUMMARY_LABELS | {"sustainable_schedule": True},
        "chain_dir": str(source.relative_to(REPO_ROOT)),
        "chain_hash": chain_hash,
        "chain_files": chain_hashes,
        "basis": {
            "bases": list(BASES),
            "primary": PRIMARY_BASIS,
            "secondary": SECONDARY_BASIS,
            "origin_province": True,
            "facility_province_handoff": False,
            "control_total_m3": CONTROL_TOTAL_M3,
        },
        "frozen_matches": frozen_matches,
        "frozen_21_of_21": frozen_matches == 21,
        "primary_matches": primary_matches,
        "primary_7_of_7": primary_matches == 7,
        "secondary_matches": secondary_matches,
        "secondary_7_of_7": secondary_matches == 7,
        "official_unchanged": official_unchanged,
        "official_detail": (
            f"{osecan_after['file_count']} files, "
            f"{osecan_after['tree_sha256'][:16]}..., "
            f"changed_files={len(changed_files)}"
        ),
        "tier_check": tier_check,
        "v10_check": v10_check,
        "zone_summary": zone_summary,
        "national_totals": [
            {
                "basis": basis,
                "stream": stream,
                "case": case,
                "odt": national_cases[basis][(stream, case)][0],
                "pj": national_cases[basis][(stream, case)][1],
            }
            for basis in BASES
            for stream, cases in (
                ("process", PROCESS_CASES),
                ("harvest", HARVEST_CASES),
            )
            for case, _fraction in cases
        ],
        "validation": {"checks": checks},
        "status_text": status_text,
        "d_status": {
            "checked_repositories": ["parent", "data"],
            "no_unexpected_deletions": True,
        },
    }
    handoff._write_json(target / "summary.json", summary)
    report_path = target / REPORT_NAME
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        _report(summary, tier_rows, comparison_rows), encoding="utf-8"
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
        "chain_dir": summary["chain_dir"],
        "chain": {
            "dir": summary["chain_dir"],
            "hash": chain_hash,
            "files": chain_hashes,
        },
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

    summary = build()
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
