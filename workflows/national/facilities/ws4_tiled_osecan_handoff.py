"""Rebuild the origin-province OSeCAN handoff from tiled WS4 chain totals.

This module reuses the frozen handoff-v10 province-to-zone weights and the
frozen v9 process-availability / harvest-recovery definitions from
``forestry_osecan_handoff_ws4``.  Only the residue totals differ: they come from
the tiled 30-m WS4 chain rather than the national 990-m WS4 chain.

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

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_ROOT = REPO_ROOT / "data"
DEFAULT_CHAIN_DIR = (
    DATA_ROOT / "derived/national/facilities/ws4_tiled_chain_119m"
)
DEFAULT_OUTPUT_DIR = DEFAULT_CHAIN_DIR / "osecan"

ARTIFACT = "forestry_osecan_handoff_ws4_tiled"
ARTIFACT_VERSION = "1.0.0"
ARTIFACT_TITLE = "Tiled 30-m WS4 Forestry Residue Handoff to OSeCAN"
REPORT_NAME = "FORESTRY_OSECAN_HANDOFF_WS4_TILED.md"

PROVINCES = handoff.PROVINCES
BASES = handoff.BASES
PRIMARY_BASIS = handoff.PRIMARY_BASIS
SECONDARY_BASIS = handoff.SECONDARY_BASIS
ZONE_ORDER = handoff.ZONE_ORDER
PROCESS_CASES = handoff.PROCESS_CASES
HARVEST_CASES = handoff.HARVEST_CASES
CASE_FRACTIONS = handoff.CASE_FRACTIONS
CONTROL_TOTAL_M3 = handoff.CONTROL_TOTAL_M3
ABS_TOL_ODT = handoff.ABS_TOL_ODT
ABS_TOL_PJ = handoff.ABS_TOL_PJ
ABS_TOL_WEIGHT = handoff.ABS_TOL_WEIGHT
CHAIN_INPUT_FILES = (
    "ws4_tiled_harvest.csv",
    "ws4_tiled_coefficients.csv",
    "ws4_tiled_product_chain.csv",
    "ws4_tiled_vs_chain_v51_comparison.csv",
    "ws4_tiled_selected_blocks.csv",
)


class Ws4TiledOsecanError(RuntimeError):
    """Raised when the tiled WS4 OSeCAN handoff fails a guard."""


def _load_chain_residues(
    chain_dir: Path,
) -> tuple[
    dict[str, dict[str, handoff.ProvinceResidue]],
    dict[str, dict[str, Any]],
    dict[str, dict[str, float]],
]:
    product_chain = chain_dir / "ws4_tiled_product_chain.csv"
    harvest_path = chain_dir / "ws4_tiled_harvest.csv"
    schedule = handoff._load_harvest_schedule(harvest_path)
    by_basis: dict[str, dict[str, handoff.ProvinceResidue]] = {}
    national_rows: dict[str, dict[str, Any]] = {}
    for basis in BASES:
        parsed, national = handoff._parse_province_residues(
            basis, product_chain
        )
        by_basis[basis] = parsed
        national_rows[basis] = national
    return by_basis, national_rows, schedule


def _chain_input_hashes(chain_dir: Path) -> tuple[dict[str, str], str]:
    """Hash the refreshed chain files this handoff consumes."""
    hashes: dict[str, str] = {}
    for name in CHAIN_INPUT_FILES:
        path = chain_dir / name
        if not path.is_file():
            raise Ws4TiledOsecanError(f"refreshed chain input is missing: {path}")
        hashes[handoff._relative(path)] = handoff._sha256(path)
    digest = hashlib.sha256()
    for relative, value in sorted(hashes.items()):
        digest.update(f"{relative}:{value}\n".encode())
    return hashes, digest.hexdigest()


def _reconcile_chain(
    by_basis: Mapping[str, Mapping[str, handoff.ProvinceResidue]],
    schedule: Mapping[str, Mapping[str, float]],
) -> None:
    for basis in BASES:
        factors = {item.scale_factor for item in by_basis[basis].values()}
        if len(factors) != 1:
            raise Ws4TiledOsecanError(
                f"{basis}: expected one national tiled scale factor, got {sorted(factors)}"
            )
        factor = factors.pop()
        if basis == SECONDARY_BASIS:
            handoff._require_close(
                factor, 1.0, "tiled ws4_achieved scale factor", abs_tol=1e-12
            )
        for province in PROVINCES:
            handoff._require_close(
                by_basis[basis][province].harvest_m3,
                float(schedule[province]["achieved_m3"]) * factor,
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
        "scaled tiled harvest control total",
        abs_tol=ABS_TOL_ODT,
    )
    handoff._require_close(
        achieved_total,
        math.fsum(float(item["achieved_m3"]) for item in schedule.values()),
        "achieved tiled harvest total",
        abs_tol=ABS_TOL_ODT,
    )


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
                    raise Ws4TiledOsecanError(
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
            "| Scenario | Stream | Case | Frozen v5.1 | v10 | Tiled primary | Tiled achieved |",
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
    return f"""# Tiled 30-m WS4 Forestry Residue Handoff to OSeCAN

**Artifact:** `{ARTIFACT}` **Version:** {ARTIFACT_VERSION}

**Status:** pass. Built from the tiled 30-m WS4 chain totals. The official
OSeCAN tree, handoff v10, residue curve v9 and frozen chain v5.1 are unchanged.

**Labels:** `model_estimate=true`, `observed=false`, `policy_grade=false`,
`national_claim=false`, `ws4_derived=true`, `tiled_30m=true`,
`official_osecan_changed=false`, `chain_v51_unchanged=true`.

## 1. Source

- Chain: `{summary['chain_dir']}`
- Zone weights: frozen handoff v10 `zone_allocation_weights.csv`
- Tier thresholds: official OSeCAN `national_supply_curve/tier_definitions.csv`
- Process cases: 0%, 25%, 50%, 75% of process residue
- Harvest cases: 0.35, 0.60, 0.75 recovery

The tiled WS4 chain is origin-province based. Handoff v10 process residue is
facility-province based, so zone rows are not compared directly across the two
bases; only national totals and tier positions are compared.

## 2. 119 Mm3 tier positions

{tier_table}

## 3. Tier comparison

{comparison_table}

- Frozen v5.1 21/21 reference positions match handoff v10:
  `{summary['frozen_matches']}/21`.
- Tiled 119 Mm3 primary positions match v10:
  `{summary['primary_matches']}/7`.
- Tiled 119 Mm3 achieved positions match v10:
  `{summary['secondary_matches']}/7`.
- Official OSeCAN tree unchanged: `{summary['official_unchanged']}`
  ({summary['official_detail']}).

## 4. Caveats

- The tiled WS4 chain is a whole-block model estimate, not a sustainable
  harvest, AAC, policy result, or investment case.
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
    source = (Path(chain_dir) if chain_dir else DEFAULT_CHAIN_DIR).resolve()
    target = (Path(output_dir) if output_dir else DEFAULT_OUTPUT_DIR).resolve()
    if handoff.OSECAN_DIR in target.parents or target == handoff.OSECAN_DIR:
        raise Ws4TiledOsecanError("refusing to write inside the official OSeCAN tree")
    for frozen_dir in (handoff.HANDOFF_V10_DIR, handoff.CURVE_V9_DIR):
        resolved = frozen_dir.resolve()
        if target == resolved or resolved in target.parents:
            raise Ws4TiledOsecanError(
                f"refusing to write inside a frozen input tree: {frozen_dir}"
            )
    handoff.assert_no_unexpected_deletions()

    osecan_before = handoff._tree_state(handoff.OSECAN_DIR)
    if osecan_before["file_count"] != handoff.EXPECTED_OSECAN_FILE_COUNT:
        raise Ws4TiledOsecanError(
            "official OSeCAN file count changed: "
            f"{osecan_before['file_count']}"
        )
    if osecan_before["tree_sha256"] != handoff.EXPECTED_OSECAN_TREE_SHA256:
        raise Ws4TiledOsecanError(
            "official OSeCAN tree hash changed: "
            f"{osecan_before['tree_sha256']}"
        )
    frozen_before = {
        "handoff_v10": handoff._tree_state(handoff.HANDOFF_V10_DIR),
        "curve_v9": handoff._tree_state(handoff.CURVE_V9_DIR),
    }

    crosswalk = handoff._load_crosswalk()
    weights_by_province, flat_weights = handoff._load_zone_weights(crosswalk)
    by_basis, national_rows, schedule = _load_chain_residues(source)
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
    allocations, allocation_check = handoff._allocate_zones(
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
                    if row["scenario"] == handoff.SCENARIO
                    and row["stream"] == stream
                    and row["case"] == case
                )
                v10_pj = float(v10_row["national_pj"])
                v10_tier = v10_row["tier"]
                tier = handoff._tier_for_pj(national_pj, tiers)
                tier_rows.append(
                    {
                        "basis": basis,
                        "scenario": handoff.SCENARIO,
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
                        **handoff.ROW_LABELS,
                    }
                )

    comparison_rows, tier_check = handoff._tier_comparison_rows(
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
            row["tiled_30m"] = "true"
            zone_rows.append(row)
    for row in tier_rows:
        row["tiled_30m"] = "true"
    for row in comparison_rows:
        row["tiled_30m"] = "true"

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
        "tiled_30m",
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
    handoff._write_csv(
        target / "case_zone_summary.csv", zone_fields, zone_rows
    )
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
        "tiled_30m",
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
        "tiled_30m",
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
            raise Ws4TiledOsecanError(
                f"frozen input tree changed during build: {name}"
            )

    checks = handoff._validate_outputs(
        zone_rows,
        tier_rows,
        comparison_rows,
        national_cases,
        zone_summary,
        v10_check,
        tier_check,
        osecan_before["tree_sha256"],
        osecan_after["tree_sha256"],
        changed_files,
    )
    failed = [check for check in checks if not check["passed"]]
    if failed:
        raise Ws4TiledOsecanError(
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
        "Rebuilt from the tiled 30-m WS4 chain on the origin-province basis "
        "using the frozen handoff-v10 zone weights. "
        f"Frozen v5.1 {frozen_matches}/21 positions match handoff v10; the "
        f"tiled 119 Mm3 primary basis matches {primary_matches}/7 and the "
        f"achieved basis {secondary_matches}/7. Official OSeCAN is unchanged."
    )
    generated = datetime.now(UTC).isoformat(timespec="seconds")
    summary: dict[str, Any] = {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": generated,
        "status": "pass",
        "title": ARTIFACT_TITLE,
        "labels": handoff.SUMMARY_LABELS | {"tiled_30m": True},
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
            handoff._relative(handoff.ZONE_WEIGHTS): handoff._sha256(handoff.ZONE_WEIGHTS),
            handoff._relative(handoff.V10_TIER_POSITIONS): handoff._sha256(handoff.V10_TIER_POSITIONS),
            handoff._relative(handoff.OSECAN_TIER_DEFINITIONS): handoff._sha256(handoff.OSECAN_TIER_DEFINITIONS),
        },
        "outputs": outputs,
        "validation": {"checks": checks},
        "d_status": summary["d_status"],
    }
    handoff._write_json(target / "manifest.json", manifest)
    return summary
