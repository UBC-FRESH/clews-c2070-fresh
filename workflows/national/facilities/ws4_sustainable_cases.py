"""Build sustainable 100 and 150 Mm3/yr WS4 schedules and chains.

The scheduler is the same 100-year, ten-period whole-block model used for the
sustainable 119 Mm3 case.  Each case takes its provincial ``allocated_share``
from the frozen provincial-allocation control CSV, so the provincial period
targets sum exactly to that case's control total.  Blocks are selected
oldest-first and volume-aware, never split, never scaled, and never harvested
below rotation.  Selected blocks are spread over the ten annual offsets of
their period by longest-processing-time balancing.

The WS4 run is the authority for harvested volume and treated area.  The
first-period annual average and the 100-year achieved annual average are each
passed through the frozen chain v5.1 per-province product/residue
coefficients and compared with the frozen 100/150 Mm3 chain targets in the
frozen all-case scenario summary.

All outputs are model estimates.  This is not an AAC, an operability or tenure
screen, an official OSeCAN change, or a national supply claim.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from workflows.national.facilities import ws4_chain_119m as chain
from workflows.national.facilities import ws4_sustainable_119m as base

REPO_ROOT = base.REPO_ROOT
DATA_ROOT = base.DATA_ROOT
CASES_ROOT = DATA_ROOT / "derived/national/facilities/ws4_sustainable_cases"
SCENARIO_SUMMARY = (
    DATA_ROOT
    / "derived/national/facilities/national_facility_chain_scenarios_v9"
    / "scenario_summary.csv"
)
PERIODS = base.PERIODS
PERIOD_LENGTH = base.PERIOD_LENGTH
HORIZON = base.HORIZON
PROVINCES = base.PROVINCES

ARTIFACT = "ws4_sustainable_cases"
ARTIFACT_VERSION = "1.0.0"
ARTIFACT_TITLE = "Sustainable 100 and 150 Mm3/yr WS4 schedules and chains"

LABELS: dict[str, bool] = {
    "model_estimate": True,
    "observed": False,
    "policy_grade": False,
    "national_claim": False,
    "ws4_derived": True,
    "sustainable_schedule": True,
    "official_osecan_changed": False,
    "chain_v51_unchanged": True,
}
ROW_LABELS: dict[str, str] = {
    "model_estimate": "true",
    "observed": "false",
    "policy_grade": "false",
    "national_claim": "false",
    "ws4_derived": "true",
    "sustainable_schedule": "true",
    "official_osecan_changed": "false",
    "chain_v51_unchanged": "true",
}

# Frozen v5.1 100/150 expectations from the model card, used as an
# independent guard on the scenario summary read, not as the comparison
# values themselves.  (metric, documented value, absolute tolerance)
EXPECTED_REFERENCE: dict[str, tuple[tuple[str, float, float], ...]] = {
    "100m": (
        ("solid_product_m3", 31_693_000.0, 1_000.0),
        ("pulp_product_odt", 10_137_000.0, 1_000.0),
        ("process_residue_pj", 419.238, 0.001),
        ("harvest_residue_gross_pj", 373.244, 0.001),
        ("harvest_residue_recoverable_r060_pj", 223.946, 0.001),
        ("total_residue_gross_pj", 792.482, 0.001),
    ),
    "150m": (
        ("solid_product_m3", 46_137_000.0, 1_000.0),
        ("pulp_product_odt", 15_551_000.0, 1_000.0),
        ("process_residue_pj", 626.865, 0.001),
        ("harvest_residue_gross_pj", 573.125, 0.001),
        ("harvest_residue_recoverable_r060_pj", 343.875, 0.001),
        ("total_residue_gross_pj", 1_199.991, 0.001),
    ),
}


class Ws4SustainableCasesError(RuntimeError):
    """Raised when a case schedule or chain fails a guardrail."""


@dataclass(frozen=True)
class CaseSpec:
    """One sustainable harvest case and its frozen controls."""

    key: str
    target_annual_m3: float
    primary_basis: str
    control_csv: Path
    output_dir: Path
    chain_dir: Path
    artifact: str
    artifact_title: str
    report_name: str


def _case(
    key: str,
    target_annual_m3: float,
    *,
    artifact_title: str,
) -> CaseSpec:
    return CaseSpec(
        key=key,
        target_annual_m3=target_annual_m3,
        primary_basis=f"sustainable_{key}",
        control_csv=(
            DATA_ROOT
            / "derived/national/harvest_basis/provincial_allocation"
            / f"{key}.csv"
        ),
        output_dir=DATA_ROOT / "derived/ws4" / f"sustainable-{key}",
        chain_dir=CASES_ROOT / key,
        artifact=f"ws4_sustainable_{key}",
        artifact_title=artifact_title,
        report_name=f"WS4_SUSTAINABLE_{key.upper()}.md",
    )


CASES: tuple[CaseSpec, ...] = (
    _case(
        "100m",
        100_000_000.1,
        artifact_title="Sustainable 100 Mm3/yr WS4 schedule and chain",
    ),
    _case(
        "150m",
        150_000_000.2,
        artifact_title="Sustainable 150 Mm3/yr WS4 schedule and chain",
    ),
)
CASE_BY_KEY = {spec.key: spec for spec in CASES}
SECONDARY_BASIS = "sustainable_achieved"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _relative(path: Path) -> str:
    return str(path.relative_to(REPO_ROOT))


def _write_csv(
    path: Path, fields: tuple[str, ...] | list[str], rows: list[dict[str, Any]]
) -> None:
    base._write_csv(path, fields, rows)


def _write_json(path: Path, payload: Any) -> None:
    base._write_json(path, payload)


def assert_no_unexpected_deletions() -> dict[str, Any]:
    return base.assert_no_unexpected_deletions()


def load_case_control(spec: CaseSpec) -> tuple[dict[str, float], float]:
    """Return normalized provincial shares and the exact control total."""
    rows = chain._read_rows(spec.control_csv)
    shares: dict[str, float] = {}
    harvest: dict[str, float] = {}
    for row in rows:
        province = row["province"].strip().upper()
        if province not in PROVINCES:
            raise Ws4SustainableCasesError(
                f"unexpected {spec.key} control province {province!r}"
            )
        if province in shares:
            raise Ws4SustainableCasesError(
                f"duplicate {spec.key} control province {province!r}"
            )
        shares[province] = base._finite(
            row["allocated_share"], f"{spec.key} {province} allocated share"
        )
        harvest[province] = base._finite(
            row["harvest_m3"], f"{spec.key} {province} control harvest"
        )
    if set(shares) != set(PROVINCES):
        raise Ws4SustainableCasesError(
            f"{spec.key} control CSV does not cover all 13 jurisdictions"
        )
    share_total = math.fsum(shares.values())
    if not math.isclose(share_total, 1.0, rel_tol=1e-9, abs_tol=1e-6):
        raise Ws4SustainableCasesError(
            f"{spec.key} allocated shares sum to {share_total!r}, expected 1.0"
        )
    harvest_total = math.fsum(harvest.values())
    if not math.isclose(harvest_total, spec.target_annual_m3, rel_tol=0.0, abs_tol=0.5):
        raise Ws4SustainableCasesError(
            f"{spec.key} control harvest total {harvest_total!r} does not "
            f"match {spec.target_annual_m3!r}"
        )
    normalized = {province: value / share_total for province, value in shares.items()}
    return normalized, harvest_total


def build_case_schedule(
    spec: CaseSpec, estate: base.Estate
) -> tuple[base.SelectionResult, dict[str, float], float]:
    """Select whole blocks for one case with the shared 119 scheduler."""
    shares, target_annual = load_case_control(spec)
    result = base.build_schedule(
        estate,
        province_shares=shares,
        label=f"province_control_{spec.key}",
        target_period_m3=target_annual * PERIOD_LENGTH,
        control_label=f"frozen {spec.key} allocated_share control",
    )
    if not math.isclose(
        result.diagnostics["period_target_m3"],
        target_annual * PERIOD_LENGTH,
        rel_tol=1e-12,
    ):
        raise Ws4SustainableCasesError(
            f"{spec.key} period target is inconsistent with its control total"
        )
    annual_min = float(result.diagnostics["annual_min_m3"])
    result.diagnostics["annual_floor_met"] = bool(annual_min + 1e-6 >= target_annual)
    result.diagnostics["annual_floor_gap_m3"] = max(0.0, target_annual - annual_min)
    return result, shares, target_annual


def load_reference_totals(spec: CaseSpec) -> dict[str, float]:
    """Read one frozen 100/150 chain target row and derive all metrics."""
    rows = chain._read_rows(SCENARIO_SUMMARY)
    matches = [row for row in rows if row["scenario"].strip() == spec.key]
    if len(matches) != 1:
        raise Ws4SustainableCasesError(
            f"scenario summary has {len(matches)} rows for {spec.key}, expected 1"
        )
    row = matches[0]
    harvest = base._finite(row["harvest_m3"], f"{spec.key} reference harvest")
    if not math.isclose(harvest, spec.target_annual_m3, rel_tol=0.0, abs_tol=0.5):
        raise Ws4SustainableCasesError(
            f"{spec.key} reference harvest {harvest!r} does not match "
            f"{spec.target_annual_m3!r}"
        )
    totals: dict[str, float] = {"harvest_m3": harvest}
    totals["sawlog_product_m3"] = base._finite(
        row["sawlog_product_m3"], f"{spec.key} reference sawlog"
    )
    totals["panel_product_m3"] = base._finite(
        row["panel_product_m3"], f"{spec.key} reference panel"
    )
    totals["solid_product_m3"] = (
        totals["sawlog_product_m3"] + totals["panel_product_m3"]
    )
    for metric in (
        "pulp_product_odt",
        "pellet_product_odt",
        "bio_product_mwh",
        "process_residue_m3",
        "process_residue_odt",
        "process_residue_pj",
        "harvest_residue_gross_odt",
        "harvest_residue_gross_pj",
        "total_residue_gross_pj",
    ):
        totals[metric] = base._finite(row[metric], f"{spec.key} reference {metric}")
    gross_odt = totals["harvest_residue_gross_odt"]
    gross_pj = totals["harvest_residue_gross_pj"]
    for recovery, fraction in (("r035", 0.35), ("r060", 0.60), ("r075", 0.75)):
        totals[f"harvest_residue_recoverable_{recovery}_odt"] = gross_odt * fraction
        totals[f"harvest_residue_recoverable_{recovery}_pj"] = gross_pj * fraction
        totals[f"total_residue_recoverable_{recovery}_pj"] = (
            totals["process_residue_pj"] + gross_pj * fraction
        )
    published_r060_odt = base._finite(
        row["harvest_residue_recoverable_odt"],
        f"{spec.key} reference recoverable odt",
    )
    published_r060_pj = base._finite(
        row["harvest_residue_recoverable_pj"],
        f"{spec.key} reference recoverable pj",
    )
    if not math.isclose(
        totals["harvest_residue_recoverable_r060_odt"],
        published_r060_odt,
        rel_tol=0.0,
        abs_tol=1e-3,
    ) or not math.isclose(
        totals["harvest_residue_recoverable_r060_pj"],
        published_r060_pj,
        rel_tol=0.0,
        abs_tol=1e-6,
    ):
        raise Ws4SustainableCasesError(
            f"{spec.key} r060 recoverable residue is not gross x 0.60"
        )
    published_total_r060 = base._finite(
        row["total_residue_recoverable_pj"],
        f"{spec.key} reference total recoverable pj",
    )
    if not math.isclose(
        totals["total_residue_recoverable_r060_pj"],
        published_total_r060,
        rel_tol=0.0,
        abs_tol=1e-6,
    ):
        raise Ws4SustainableCasesError(
            f"{spec.key} total recoverable residue is inconsistent"
        )
    for metric, expected, tolerance in EXPECTED_REFERENCE[spec.key]:
        observed = totals[metric]
        if abs(observed - expected) > tolerance:
            raise Ws4SustainableCasesError(
                f"{spec.key} frozen reference {metric} is {observed!r}, "
                f"expected {expected!r} +/- {tolerance!r}"
            )
    return totals


def _write_schedule_outputs(
    spec: CaseSpec,
    result: base.SelectionResult,
    harvest_province: pd.DataFrame,
    annual: pd.DataFrame,
) -> None:
    spec.output_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(
        spec.output_dir / "selected_blocks.csv",
        [
            "block_id",
            "province",
            "period",
            "offset",
            "age_at_harvest",
            "volume_m3",
            "area_ha",
            "rotation_years",
            "rotation_raw_years",
            "curve_id",
            "age_class",
        ],
        result.selected.to_dict(orient="records"),
    )
    _write_csv(
        spec.output_dir / "annual_harvest.csv",
        [
            "offset",
            "year",
            "period",
            "harvest_m3",
            "area_ha",
            "block_count",
            "mean_age",
        ],
        annual.merge(
            result.annual_rows[["offset", "block_count", "mean_age"]],
            on="offset",
            how="left",
            validate="one_to_one",
        ).to_dict(orient="records"),
    )
    _write_csv(
        spec.output_dir / "period_harvest.csv",
        [
            "period",
            "start_offset",
            "end_offset",
            "target_m3",
            "achieved_m3",
            "delta_m3",
            "overshoot_pct",
            "area_ha",
            "block_count",
            "annual_min_m3",
            "annual_max_m3",
            "annual_cv",
        ],
        result.period_rows.to_dict(orient="records"),
    )
    _write_csv(
        spec.output_dir / "province_period_harvest.csv",
        [
            "period",
            "province",
            "target_m3",
            "achieved_m3",
            "delta_m3",
            "area_ha",
            "block_count",
            "mean_age",
        ],
        result.province_period_rows.to_dict(orient="records"),
    )
    _write_csv(
        spec.output_dir / "province_harvest.csv",
        ["province", "harvest_m3", "area_ha", "block_count"],
        harvest_province.to_dict(orient="records"),
    )
    _write_csv(
        spec.output_dir / "rotation_distribution.csv",
        [
            "rotation_raw_years",
            "rotation_years",
            "blocks",
            "area_ha",
            "volume_m3",
        ],
        result.rotation_distribution.to_dict(orient="records"),
    )
    _write_csv(
        spec.output_dir / "shortfalls.csv",
        [
            "period",
            "province",
            "scope",
            "target_m3",
            "eligible_volume_m3",
            "shortfall_m3",
        ],
        result.shortfalls.to_dict(orient="records"),
    )
    _write_csv(
        spec.output_dir / "age_class_harvest.csv",
        ["period", "province", "age_class", "blocks", "area_ha", "volume_m3"],
        result.selected.groupby(["period", "province", "age_class"], as_index=False)
        .agg(
            blocks=("block_id", "size"),
            area_ha=("area_ha", "sum"),
            volume_m3=("volume_m3", "sum"),
        )
        .to_dict(orient="records"),
    )


def _harvest_rows(
    spec: CaseSpec,
    harvest_province: pd.DataFrame,
    first_period_annual: pd.DataFrame,
    shares: dict[str, float],
    target_annual: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    harvest = harvest_province.set_index("province")["harvest_m3"]
    if set(harvest.index) != set(PROVINCES):
        raise Ws4SustainableCasesError(f"{spec.key} WS4 run is missing provinces")
    total_100y = float(harvest.sum())
    if total_100y <= 0.0:
        raise Ws4SustainableCasesError(f"{spec.key} WS4 run harvest is zero")
    first = first_period_annual.set_index("province")["annual_m3"]
    annual_100y = harvest / HORIZON
    rows: list[dict[str, Any]] = []
    for province in PROVINCES:
        rows.append(
            {
                "province": province,
                "control_share": shares[province],
                "target_annual_m3": target_annual * shares[province],
                "achieved_100y_m3": float(harvest[province]),
                "annual_100y_m3": float(annual_100y[province]),
                f"annual_{spec.key}_m3": float(first.get(province, 0.0)),
                "first_period_m3": float(first.get(province, 0.0) * 10.0),
                "first_period_annual_m3": float(first.get(province, 0.0)),
                "scale_factor": 1.0,
                "area_100y_ha": float(
                    harvest_province.set_index("province").loc[province, "area_ha"]
                ),
                "block_count_100y": int(
                    harvest_province.set_index("province").loc[province, "block_count"]
                ),
                **ROW_LABELS,
            }
        )
    first_total = float(first.sum())
    total_row = {
        "province": "CAN",
        "control_share": 1.0,
        "target_annual_m3": target_annual,
        "achieved_100y_m3": total_100y,
        "annual_100y_m3": total_100y / HORIZON,
        f"annual_{spec.key}_m3": first_total,
        "first_period_m3": first_total * 10.0,
        "first_period_annual_m3": first_total,
        "scale_factor": 1.0,
        "area_100y_ha": float(harvest_province["area_ha"].sum()),
        "block_count_100y": int(harvest_province["block_count"].sum()),
        **ROW_LABELS,
    }
    return rows, total_row


def build_case_chain(
    spec: CaseSpec,
    *,
    harvest_province: pd.DataFrame,
    first_period_annual: pd.DataFrame,
    rotation_table: pd.DataFrame,
    schedule_result: base.SelectionResult,
    run_values: pd.DataFrame,
    validate_text: str,
    shares: dict[str, float],
    target_annual: float,
) -> dict[str, Any]:
    """Write one case chain and return its summary."""
    spec.chain_dir.mkdir(parents=True, exist_ok=True)
    frozen = chain._frozen_province_rows(chain.CHAIN_V51_CSV)
    _coefficient_rows, coefficients = chain._coefficient_rows(frozen)
    reference_totals = load_reference_totals(spec)

    harvest_rows, total_row = _harvest_rows(
        spec, harvest_province, first_period_annual, shares, target_annual
    )
    harvest_fields = list(harvest_rows[0].keys())
    _write_csv(
        spec.chain_dir / "sustainable_harvest_by_province.csv",
        harvest_fields,
        harvest_rows + [total_row],
    )

    annual_achieved = harvest_province.set_index("province")["harvest_m3"] / HORIZON
    first_annual = first_period_annual.set_index("province")["annual_m3"].reindex(
        PROVINCES, fill_value=0.0
    )
    bases = (spec.primary_basis, SECONDARY_BASIS)
    province_chain_rows: list[dict[str, Any]] = []
    national_rows: list[dict[str, Any]] = []
    for basis, values in (
        (spec.primary_basis, first_annual),
        (SECONDARY_BASIS, annual_achieved),
    ):
        rows, national = chain.build_chain_rows(
            {province: float(values[province]) for province in PROVINCES},
            basis,
            frozen,
            coefficients,
            scale_factor=1.0,
        )
        for chain_row in rows + [national]:
            chain_row.update(ROW_LABELS)
        province_chain_rows.extend(rows)
        national_rows.append(national)
    product_fields = [
        "province",
        "geography_level",
        "basis",
        "harvest_m3",
        "scale_factor",
        "coefficient_tier",
        "coefficient_source",
        *[metric for metric, _unit in chain.METRICS],
        *ROW_LABELS.keys(),
    ]
    _write_csv(
        spec.chain_dir / "sustainable_product_chain.csv",
        product_fields,
        province_chain_rows + national_rows,
    )

    national = {
        spec.primary_basis: next(
            row for row in national_rows if row["basis"] == spec.primary_basis
        ),
        SECONDARY_BASIS: next(
            row for row in national_rows if row["basis"] == SECONDARY_BASIS
        ),
    }
    comparison_rows: list[dict[str, Any]] = []
    for metric, unit in chain.METRICS:
        reference = float(reference_totals[metric])
        row: dict[str, Any] = {
            "metric": metric,
            "unit": unit,
            "chain_v51_value": reference,
        }
        for basis in bases:
            value = float(national[basis][metric])
            delta = value - reference
            row[f"{basis}_value"] = value
            row[f"{basis}_delta"] = delta
            row[f"{basis}_delta_pct"] = delta / reference * 100.0 if reference else 0.0
        row.update(ROW_LABELS)
        comparison_rows.append(row)
    comparison_fields = [
        "metric",
        "unit",
        "chain_v51_value",
        *[
            f"{basis}_{suffix}"
            for basis in bases
            for suffix in ("value", "delta", "delta_pct")
        ],
        *ROW_LABELS.keys(),
    ]
    _write_csv(
        spec.chain_dir / "comparison_vs_v51.csv",
        comparison_fields,
        comparison_rows,
    )

    selected_rotation = schedule_result.rotation_distribution
    raw_distribution = (
        selected_rotation.groupby("rotation_raw_years", as_index=False)["blocks"]
        .sum()
        .set_index("rotation_raw_years")["blocks"]
        .sort_index()
        .to_dict()
    )
    used_distribution = (
        selected_rotation.groupby("rotation_years", as_index=False)["blocks"]
        .sum()
        .set_index("rotation_years")["blocks"]
        .sort_index()
        .to_dict()
    )
    summary: dict[str, Any] = {
        "artifact": spec.artifact,
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "status": "pass" if schedule_result.diagnostics["sustained"] else "fail",
        "title": spec.artifact_title,
        "labels": LABELS,
        "case": spec.key,
        "target": {
            "annual_m3": target_annual,
            "nominal_annual_m3": spec.target_annual_m3,
            "period_m3": target_annual * PERIOD_LENGTH,
            "period_length_years": PERIOD_LENGTH,
            "periods": PERIODS,
            "horizon_years": HORIZON,
            "province_allocation": f"frozen {spec.key} allocated_share",
        },
        "schedule": schedule_result.diagnostics,
        "rotation": {
            "raw_distribution": {
                int(key): int(value) for key, value in raw_distribution.items()
            },
            "used_distribution": {
                int(key): int(value) for key, value in used_distribution.items()
            },
            "out_of_range_block_count": int(
                (
                    schedule_result.selected["rotation_raw_years"]
                    != schedule_result.selected["rotation_years"]
                ).sum()
            ),
            "curve_count": len(rotation_table),
        },
        "harvest": {
            "province": harvest_rows,
            "national": total_row,
            "total_100y_m3": float(total_row["achieved_100y_m3"]),
            "annual_mean_m3": float(total_row["annual_100y_m3"]),
            "first_period_annual_mean_m3": float(total_row["first_period_annual_m3"]),
        },
        "chain": {
            "basis": {
                "bases": list(bases),
                "primary": spec.primary_basis,
                "secondary": SECONDARY_BASIS,
                "primary_definition": (
                    "first-period annual average (offsets 0-9) with frozen "
                    "v5.1 per-province coefficients"
                ),
                "secondary_definition": (
                    "100-year achieved annual average with frozen v5.1 "
                    "per-province coefficients"
                ),
            },
            "reference": {
                "source": _relative(SCENARIO_SUMMARY),
                "scenario": spec.key,
                "values": reference_totals,
            },
            "national": national,
            "comparison": comparison_rows,
        },
        "ws4": {
            "dataset": _relative(base.DATASET_DIR),
            "schedule": _relative(spec.output_dir / "schedule.csv"),
            "run_dir": _relative(spec.output_dir / "run_100y"),
            "validate": validate_text,
            "run_offsets": int(run_values["offset"].nunique()),
        },
        "d_status": assert_no_unexpected_deletions(),
        "caveats": [
            (
                "The schedule is a national even-flow arithmetic model, not an "
                "AAC, policy result, tenure plan, or investment case."
            ),
            (
                "The full forest estate is used; no operability, tenure, road, "
                "slope, or mill-capacity screen is applied."
            ),
            (
                f"Provincial period targets use the frozen {spec.key} "
                "allocated_share values. A final whole block may overshoot a "
                "provincial target because no block is split or scaled."
            ),
            (
                "Rotation is the integer age of maximum MAI on the RSYC yield "
                "curve, capped to 40-200 years; the raw peak is flagged."
            ),
            (
                "Frozen chain v5.1 coefficients are applied to WS4 harvest by "
                "origin province. Facility assignment, mill capacity, "
                "transport, and interprovincial trade are not re-run."
            ),
            (
                "The primary chain basis uses the first-period annual average; "
                "the secondary basis uses the 100-year achieved average. "
                "Neither is an official OSeCAN change."
            ),
        ],
    }
    _write_json(spec.chain_dir / "summary.json", summary)
    _write_case_report(spec, summary, schedule_result)
    return summary


def _md_table(header: list[str], rows: list[list[str]]) -> str:
    lines = [
        "| " + " | ".join(header) + " |",
        "|" + "|".join("---" for _ in header) + "|",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return "\n".join(lines)


def _write_case_report(
    spec: CaseSpec, summary: dict[str, Any], result: base.SelectionResult
) -> None:
    periods = result.period_rows
    period_table = _md_table(
        [
            "Period",
            "Offsets",
            "Target Mm3",
            "Achieved Mm3",
            "Overshoot %",
            "Area Mha",
            "Blocks",
        ],
        [
            [
                str(int(row.period)),
                f"{int(row.start_offset)}-{int(row.end_offset)}",
                f"{float(row.target_m3) / 1e6:.3f}",
                f"{float(row.achieved_m3) / 1e6:.3f}",
                f"{float(row.overshoot_pct):.4f}",
                f"{float(row.area_ha) / 1e6:.3f}",
                str(int(row.block_count)),
            ]
            for row in periods.itertuples(index=False)
        ],
    )
    province_table = _md_table(
        [
            "Province",
            "Control share",
            "Target Mm3/yr",
            "First period Mm3/yr",
            "100-year Mm3/yr",
            "Area Mha",
            "Blocks",
        ],
        [
            [
                str(row["province"]),
                f"{float(row['control_share']) * 100:.3f}%",
                f"{float(row['target_annual_m3']) / 1e6:.6f}",
                f"{float(row[f'annual_{spec.key}_m3']) / 1e6:.6f}",
                f"{float(row['annual_100y_m3']) / 1e6:.6f}",
                f"{float(row['area_100y_ha']) / 1e6:.3f}",
                str(int(row["block_count_100y"])),
            ]
            for row in summary["harvest"]["province"]
        ],
    )
    comparison_table = _md_table(
        [
            "Metric",
            "Unit",
            "v5.1",
            "First-period basis",
            "Delta %",
            "Achieved basis",
            "Delta %",
        ],
        [
            [
                str(row["metric"]),
                str(row["unit"]),
                f"{float(row['chain_v51_value']):.6f}",
                f"{float(row[f'{spec.primary_basis}_value']):.6f}",
                f"{float(row[f'{spec.primary_basis}_delta_pct']):.4f}",
                f"{float(row[f'{SECONDARY_BASIS}_value']):.6f}",
                f"{float(row[f'{SECONDARY_BASIS}_delta_pct']):.4f}",
            ]
            for row in summary["chain"]["comparison"]
        ],
    )
    schedule = summary["schedule"]
    case_label = spec.key.replace("m", " Mm3/yr")
    report = f"""# Sustainable {case_label} WS4 Schedule and Chain

**Artifact:** `{spec.artifact}` **Version:** {ARTIFACT_VERSION}

**Status:** {summary["status"]}. The {summary["target"]["annual_m3"]:,.1f} m3/yr case is {"sustained" if schedule["sustained"] else "not sustained"} for 100 years. This is a whole-block model estimate, not an AAC, policy result, tenure plan, or investment case.

**Labels:** `model_estimate=true`, `observed=false`, `policy_grade=false`,
`national_claim=false`, `ws4_derived=true`, `sustainable_schedule=true`,
`official_osecan_changed=false`, `chain_v51_unchanged=true`.

## 1. Input and method

- Dataset: `{summary["ws4"]["dataset"]}`
- Schedule: `{summary["ws4"]["schedule"]}`
- Run: `{summary["ws4"]["run_dir"]}`
- Control: `{_relative(spec.control_csv)}`
- Horizon: {HORIZON} years, {PERIODS} periods of {PERIOD_LENGTH} years.
- Period target: {summary["target"]["period_m3"]:,.1f} m3.
- Selection: whole blocks, oldest-first, volume-aware, within provincial
  period targets from the frozen {spec.key} allocated shares; no block is
  split, scaled, or harvested below rotation.
- Annual assignment: longest-processing-time balancing of exact annual
  volumes over the ten offsets of each period.

## 2. Period harvest

{period_table}

- Annual harvest minimum: {schedule["annual_min_m3"]:,.1f} m3/yr.
- Annual harvest maximum: {schedule["annual_max_m3"]:,.1f} m3/yr.
- Annual mean: {schedule["annual_mean_m3"]:,.1f} m3/yr.
- Annual CV: {schedule["annual_cv"]:.6f}.
- Annual floor met: `{schedule["annual_floor_met"]}`; gap to the nominal
  annual rate: {schedule["annual_floor_gap_m3"]:,.1f} m3.
- Period shortfall: {schedule["period_shortfall_m3"]:.1f} m3.
- Sustained: `{schedule["sustained"]}`.
- Selected blocks: {schedule["selected_blocks"]:,}
  ({schedule["selected_area_ha"] / 1e6:,.3f} Mha).
- Blocks reharvested within 100 years: {schedule["blocks_reharvested"]:,}.

## 3. Harvest by province

{province_table}

## 4. Chain versus frozen v5.1

{comparison_table}

The first-period basis applies the frozen v5.1 per-province coefficients to
the annual average of offsets 0-9. The achieved basis applies them to the
100-year annual average. The comparison values are the frozen 100/150 Mm3
targets in `{_relative(SCENARIO_SUMMARY)}`, guarded against the model-card
values.

## 5. OSeCAN handoff

The combined `osecan/` artifact applies the existing WS4 origin-province
handoff logic to this chain. It verifies 21/21 frozen v5.1 reference positions
across the three scenarios, 7/7 tier positions for this case on both bases,
and an unchanged official OSeCAN tree.

## 6. Caveats

"""
    report += "\n".join(f"- {caveat}" for caveat in summary["caveats"]) + "\n"
    (spec.chain_dir / spec.report_name).write_text(report, encoding="utf-8")


def _case_inputs(spec: CaseSpec) -> dict[str, str]:
    return {
        _relative(base.DATASET_DIR / "manifest.json"): _sha256(
            base.DATASET_DIR / "manifest.json"
        ),
        _relative(spec.control_csv): _sha256(spec.control_csv),
        _relative(SCENARIO_SUMMARY): _sha256(SCENARIO_SUMMARY),
        _relative(chain.CHAIN_V51_CSV): _sha256(chain.CHAIN_V51_CSV),
        _relative(chain.CHAIN_V51_COEFFICIENTS): _sha256(chain.CHAIN_V51_COEFFICIENTS),
        _relative(spec.output_dir / "schedule.csv"): _sha256(
            spec.output_dir / "schedule.csv"
        ),
        _relative(spec.output_dir / "run_100y" / "product_values.csv"): _sha256(
            spec.output_dir / "run_100y" / "product_values.csv"
        ),
    }


def _write_case_manifest(spec: CaseSpec, summary: dict[str, Any]) -> None:
    outputs = {
        path.name: {"bytes": path.stat().st_size, "sha256": _sha256(path)}
        for path in sorted(spec.chain_dir.iterdir())
        if path.is_file() and path.name != "manifest.json"
    }
    manifest = {
        "artifact": spec.artifact,
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": summary["generated_utc"],
        "title": spec.artifact_title,
        "labels": summary["labels"],
        "case": spec.key,
        "target": summary["target"],
        "inputs": _case_inputs(spec),
        "outputs": outputs,
        "validation": {
            "ws4_validate": summary["ws4"]["validate"],
            "run_offsets": summary["ws4"]["run_offsets"],
            "sustained": summary["schedule"]["sustained"],
            "period_shortfall_m3": summary["schedule"]["period_shortfall_m3"],
        },
        "d_status": summary["d_status"],
    }
    _write_json(spec.chain_dir / "manifest.json", manifest)


def _run_case(spec: CaseSpec, estate: base.Estate) -> dict[str, Any]:
    spec.output_dir.mkdir(parents=True, exist_ok=True)
    spec.chain_dir.mkdir(parents=True, exist_ok=True)
    schedule_result, shares, target_annual = build_case_schedule(spec, estate)
    validate_text = base.run_ws4_validate()
    (spec.output_dir / "validate.txt").write_text(
        validate_text + "\n", encoding="utf-8"
    )
    run_status = base.ensure_ws4_run(
        schedule_result.schedule,
        schedule_path=spec.output_dir / "schedule.csv",
        run_dir=spec.output_dir / "run_100y",
        horizon=HORIZON,
        dataset_dir=base.DATASET_DIR,
    )
    run_values = base.extract_run_products(
        spec.output_dir / "run_100y", horizon=HORIZON
    )
    harvest_province, annual = base.harvest_by_province(
        schedule_result.selected, run_values
    )
    first_period = (
        schedule_result.selected[schedule_result.selected["period"] == 0]
        .groupby("province", as_index=False)
        .agg(volume_m3=("volume_m3", "sum"), area_ha=("area_ha", "sum"))
    )
    first_period["annual_m3"] = first_period["volume_m3"] / PERIOD_LENGTH
    first_period["annual_ha"] = first_period["area_ha"] / PERIOD_LENGTH

    _write_schedule_outputs(spec, schedule_result, harvest_province, annual)
    rotation_table = base.compute_rotation_table(estate.curve_ids, estate.curve_matrix)
    summary = build_case_chain(
        spec,
        harvest_province=harvest_province,
        first_period_annual=first_period,
        rotation_table=rotation_table,
        schedule_result=schedule_result,
        run_values=run_values,
        validate_text=validate_text,
        shares=shares,
        target_annual=target_annual,
    )
    summary["run_status"] = run_status
    summary["schedule_files"] = {
        name: _sha256(spec.output_dir / name)
        for name in (
            "schedule.csv",
            "selected_blocks.csv",
            "annual_harvest.csv",
            "period_harvest.csv",
            "province_period_harvest.csv",
            "province_harvest.csv",
            "rotation_distribution.csv",
            "shortfalls.csv",
            "age_class_harvest.csv",
            "validate.txt",
        )
    }
    summary["chain_files"] = {
        name: _sha256(spec.chain_dir / name)
        for name in (
            "sustainable_harvest_by_province.csv",
            "sustainable_product_chain.csv",
            "comparison_vs_v51.csv",
        )
    }
    _write_json(spec.chain_dir / "summary.json", summary)
    _write_case_manifest(spec, summary)
    return summary


def _combined_comparison(
    case_summaries: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for spec in CASES:
        for row in case_summaries[spec.key]["chain"]["comparison"]:
            rows.append(
                {
                    "case": spec.key,
                    "metric": row["metric"],
                    "unit": row["unit"],
                    "chain_v51_value": row["chain_v51_value"],
                    "primary_basis": spec.primary_basis,
                    "primary_value": row[f"{spec.primary_basis}_value"],
                    "primary_delta": row[f"{spec.primary_basis}_delta"],
                    "primary_delta_pct": row[f"{spec.primary_basis}_delta_pct"],
                    "achieved_value": row[f"{SECONDARY_BASIS}_value"],
                    "achieved_delta": row[f"{SECONDARY_BASIS}_delta"],
                    "achieved_delta_pct": row[f"{SECONDARY_BASIS}_delta_pct"],
                    **ROW_LABELS,
                }
            )
    _write_csv(
        CASES_ROOT / "comparison_vs_v51.csv",
        [
            "case",
            "metric",
            "unit",
            "chain_v51_value",
            "primary_basis",
            "primary_value",
            "primary_delta",
            "primary_delta_pct",
            "achieved_value",
            "achieved_delta",
            "achieved_delta_pct",
            *ROW_LABELS.keys(),
        ],
        rows,
    )
    return rows


def _combined_manifest(summary: dict[str, Any]) -> None:
    outputs = {
        path.name: {"bytes": path.stat().st_size, "sha256": _sha256(path)}
        for path in sorted(CASES_ROOT.iterdir())
        if path.is_file() and path.name != "manifest.json"
    }
    inputs: dict[str, str] = {}
    for spec in CASES:
        inputs.update(_case_inputs(spec))
    manifest = {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": summary["generated_utc"],
        "title": ARTIFACT_TITLE,
        "labels": summary["labels"],
        "cases": {
            spec.key: {
                "chain_dir": _relative(spec.chain_dir),
                "schedule_dir": _relative(spec.output_dir),
                "target_annual_m3": summary["cases"][spec.key]["target"]["annual_m3"],
                "sustained": summary["cases"][spec.key]["schedule"]["sustained"],
            }
            for spec in CASES
        },
        "inputs": inputs,
        "outputs": outputs,
        "osecan": summary["osecan"],
        "validation": summary["validation"],
        "d_status": summary["d_status"],
    }
    _write_json(CASES_ROOT / "manifest.json", manifest)


def _write_combined_report(summary: dict[str, Any]) -> None:
    gap_case = max(
        CASES,
        key=lambda item: summary["cases"][item.key]["schedule"]["annual_floor_gap_m3"],
    )
    gap_case_schedule = summary["cases"][gap_case.key]["schedule"]
    gap_case_target = summary["cases"][gap_case.key]["target"]
    floor_detail = (
        "Both cases keep every annual offset at or above its nominal annual rate."
        if gap_case_schedule["annual_floor_gap_m3"] <= 1e-6
        else (
            f"The {gap_case.key} case has one annual offset "
            f"{gap_case_schedule['annual_min_m3']:,.1f} m3, "
            f"{gap_case_schedule['annual_floor_gap_m3']:,.1f} m3 "
            f"({gap_case_schedule['annual_floor_gap_m3'] / gap_case_target['annual_m3'] * 100:.4f}%) "
            "below the nominal annual rate, while its minimum period is "
            f"{gap_case_schedule['period_min_m3']:,.1f} m3, "
            f"{gap_case_schedule['period_min_m3'] - gap_case_target['period_m3']:,.1f} m3 "
            "above the period target; this is a longest-processing-time spread "
            "residual and is reported, not forced."
        )
    )
    schedule_table = _md_table(
        [
            "Case",
            "Target Mm3/yr",
            "Annual min Mm3/yr",
            "Annual max Mm3/yr",
            "Annual mean Mm3/yr",
            "Annual floor",
            "Period shortfall Mm3",
            "Sustained",
            "Blocks",
            "Area Mha",
            "Reharvested",
        ],
        [
            [
                spec.key,
                f"{summary['cases'][spec.key]['target']['annual_m3'] / 1e6:.6f}",
                f"{summary['cases'][spec.key]['schedule']['annual_min_m3'] / 1e6:.4f}",
                f"{summary['cases'][spec.key]['schedule']['annual_max_m3'] / 1e6:.4f}",
                f"{summary['cases'][spec.key]['schedule']['annual_mean_m3'] / 1e6:.4f}",
                str(summary["cases"][spec.key]["schedule"]["annual_floor_met"]),
                f"{summary['cases'][spec.key]['schedule']['period_shortfall_m3'] / 1e6:.6f}",
                str(summary["cases"][spec.key]["schedule"]["sustained"]),
                f"{summary['cases'][spec.key]['schedule']['selected_blocks']:,}",
                f"{summary['cases'][spec.key]['schedule']['selected_area_ha'] / 1e6:.3f}",
                f"{summary['cases'][spec.key]['schedule']['blocks_reharvested']:,}",
            ]
            for spec in CASES
        ],
    )
    key_metrics = (
        "harvest_m3",
        "solid_product_m3",
        "pulp_product_odt",
        "process_residue_pj",
        "harvest_residue_gross_pj",
        "harvest_residue_recoverable_r060_pj",
        "total_residue_gross_pj",
    )
    comparison_table = _md_table(
        ["Case", "Metric", "Unit", "v5.1", "Primary", "Delta %", "Achieved", "Delta %"],
        [
            [
                row["case"],
                row["metric"],
                row["unit"],
                f"{float(row['chain_v51_value']):,.6f}",
                f"{float(row['primary_value']):,.6f}",
                f"{float(row['primary_delta_pct']):.4f}",
                f"{float(row['achieved_value']):,.6f}",
                f"{float(row['achieved_delta_pct']):.4f}",
            ]
            for row in summary["comparison"]
            if row["metric"] in key_metrics
        ],
    )
    report = f"""# Sustainable 100 and 150 Mm3/yr WS4 Schedules and Chains

**Artifact:** `{ARTIFACT}` **Version:** {ARTIFACT_VERSION}

**Status:** {summary["status"]}. Both documented provincial ceiling cases are
simulated for 100 years on the national 990 m WS4 estate with the same
whole-block scheduler as the sustainable 119 Mm3 case. This is a model
estimate, not an AAC, policy result, tenure plan, or national supply claim.

**Labels:** `model_estimate=true`, `observed=false`, `policy_grade=false`,
`national_claim=false`, `ws4_derived=true`, `sustainable_schedule=true`,
`official_osecan_changed=false`, `chain_v51_unchanged=true`.

## 1. Schedule results

{schedule_table}

The control unit is the 10-year period. Both cases meet every period target
with zero period shortfall. {floor_detail}

## 2. Chain comparison against frozen v5.1

{comparison_table}

The primary basis applies frozen v5.1 per-province coefficients to the
first-period annual average; the achieved basis applies them to the 100-year
achieved annual average. Reference values are the frozen 100/150 Mm3 targets
in `{_relative(SCENARIO_SUMMARY)}`.

## 3. OSeCAN handoff

{summary["osecan"]["status_text"]}

## 4. Caveats

- The full forest estate is used; no operability, tenure, road, slope, or
  mill-capacity screen is applied.
- Whole blocks are selected; provincial overshoot is retained and reported.
- Rotation is capped to 40-200 years and never relaxed to meet a target.
- Frozen chain v5.1 and the official OSeCAN tree are read-only; no UI surface
  is changed.
"""
    (CASES_ROOT / "WS4_SUSTAINABLE_CASES.md").write_text(report, encoding="utf-8")


def build() -> dict[str, Any]:
    """Run both case schedules, WS4 runs, chains, and the combined handoff."""
    assert_no_unexpected_deletions()
    from workflows.national.facilities import forestry_osecan_handoff_ws4 as handoff
    from workflows.national.facilities import ws4_sustainable_cases_osecan as osecan

    chain_v51_before = handoff._tree_state(chain.CHAIN_V51_DIR)
    osecan_before = handoff._tree_state(handoff.OSECAN_DIR)
    CASES_ROOT.mkdir(parents=True, exist_ok=True)

    estate = base.load_estate()
    case_summaries = {spec.key: _run_case(spec, estate) for spec in CASES}
    comparison_rows = _combined_comparison(case_summaries)

    osecan_summary = osecan.build(
        case_dirs={spec.key: spec.chain_dir for spec in CASES},
        output_dir=CASES_ROOT / "osecan",
    )
    summary: dict[str, Any] = {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "status": (
            "pass"
            if all(case_summaries[spec.key]["schedule"]["sustained"] for spec in CASES)
            and osecan_summary["status"] == "pass"
            else "fail"
        ),
        "title": ARTIFACT_TITLE,
        "labels": LABELS,
        "cases": case_summaries,
        "comparison": comparison_rows,
        "osecan": {
            "status": osecan_summary["status"],
            "status_text": osecan_summary["status_text"],
            "dir": _relative(CASES_ROOT / "osecan"),
            "frozen_matches": osecan_summary["frozen_matches"],
            "frozen_21_of_21": osecan_summary["frozen_21_of_21"],
            "case_matches": {
                spec.key: {
                    "primary": osecan_summary["cases"][spec.key]["primary_matches"],
                    "secondary": osecan_summary["cases"][spec.key]["secondary_matches"],
                    "positions": osecan_summary["cases"][spec.key]["positions"],
                }
                for spec in CASES
            },
            "official_unchanged": osecan_summary["official_unchanged"],
            "official_detail": osecan_summary["official_detail"],
        },
        "validation": {
            "both_cases_sustained": all(
                case_summaries[spec.key]["schedule"]["sustained"] for spec in CASES
            ),
            "comparison_rows": sum(
                len(case_summaries[spec.key]["chain"]["comparison"]) for spec in CASES
            ),
            "osecan_checks": osecan_summary["validation"]["checks"],
        },
        "d_status": assert_no_unexpected_deletions(),
    }
    chain_v51_after = handoff._tree_state(chain.CHAIN_V51_DIR)
    osecan_after = handoff._tree_state(handoff.OSECAN_DIR)
    if chain_v51_before != chain_v51_after:
        raise Ws4SustainableCasesError(
            "frozen chain v5.1 tree changed during the build"
        )
    if osecan_before != osecan_after:
        raise Ws4SustainableCasesError("official OSeCAN tree changed during the build")
    summary["frozen_integrity"] = {
        "chain_v51_before": chain_v51_before,
        "chain_v51_after": chain_v51_after,
        "chain_v51_unchanged": chain_v51_before == chain_v51_after,
        "official_osecan_before": osecan_before,
        "official_osecan_after": osecan_after,
        "official_osecan_unchanged": osecan_before == osecan_after,
    }
    _write_json(CASES_ROOT / "summary.json", summary)
    _write_combined_report(summary)
    _combined_manifest(summary)
    return summary


def main() -> None:
    summary = build()
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
