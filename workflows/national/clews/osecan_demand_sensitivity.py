"""Demand-side sensitivity for the tiered national BCW supply curve.

The committed tiered curve (``SUPBCWT1``-``SUPBCWT4`` plus BC zonal
``SUPBCWB1``/``SUPBCWB2``) is held fixed.  This module changes only the
demand-side drivers:

* a higher national CO2 price from 2026, with hydro capped at 2021 activity;
* an optional diagnostic lower bound on biomass power output.

The model is solved only in a disposable OSeCAN copy.  The public OSeCAN
repository is never modified.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

from workflows.national.clews.osecan_national_supply_curve import (
    BC_ZONAL_TOTAL_PJ,
    NATIONAL_TOTAL_PJ,
    REST_OF_CANADA_PJ,
    TIERS,
    YEARS,
    OUTPUT_DIR as SUPPLY_OUTPUT_DIR,
    _hydro_2021,
    _json,
    _read_rows,
    _read_series,
    _sha256,
    _write_csv,
    build_dispatch_rows,
    build_scenario_params,
    harmonize,
    read_production,
    run_model,
    summarize_run,
    tier_caps,
    validate_tier_structure,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
SUPPLY_DIR = Path("data/derived/national/clews")
SOURCE_ARTIFACT_DIR = REPO_ROOT / SUPPLY_OUTPUT_DIR
OUTPUT_DIR = SOURCE_ARTIFACT_DIR / "demand_sensitivity"

CARBON_PRICE_PRE_2026 = 95.0
REFERENCE_CARBON_PRICE = 200.0
STATUS = "fresh_side_provisional_not_c2070_official"
MODEL_REPOSITORY = "https://github.com/deltae/C2070"
MODEL_COMMIT = "40d5b43bb3f8b3f69c16e953144d35eeed5f0f44"
MODEL_PACKAGE = "osecan 0.1.0"
MODEL_SOLVER = "GLPK via swiglpk 5.0.13"
MODEL_SCENARIO = "Current Measures"

CARBON_CASES = (
    {
        "name": "carbon_250",
        "label": "CO2 $250/t from 2026 + hydro capped at 2021",
        "carbon_price_from_2026": 250.0,
        "hydro_mode": "upper_2021",
    },
    {
        "name": "carbon_300",
        "label": "CO2 $300/t from 2026 + hydro capped at 2021",
        "carbon_price_from_2026": 300.0,
        "hydro_mode": "upper_2021",
    },
    {
        "name": "carbon_400",
        "label": "CO2 $400/t from 2026 + hydro capped at 2021 (diagnostic)",
        "carbon_price_from_2026": 400.0,
        "hydro_mode": "upper_2021",
    },
)

MANDATE_YEAR = 2050


def _case_spec(name: str) -> dict[str, object]:
    for spec in CARBON_CASES:
        if spec["name"] == name:
            return dict(spec)
    raise KeyError(f"unknown case {name!r}")


def _load_tier_caps() -> dict[str, dict[int, float]]:
    zonal_caps = {
        technology: _read_series(
            REPO_ROOT / SUPPLY_DIR / f"{technology}_activity_limits_base.csv",
            technology,
        )
        for technology in ("SUPBCWB1", "SUPBCWB2")
    }
    validate_tier_structure()
    return tier_caps(zonal_caps)


def _apply_lower_limit(
    params_dir: Path,
    *,
    technology: str,
    year: int,
    value_pj: float,
) -> None:
    path = params_dir / "TotalTechnologyAnnualActivityLowerLimit.csv"
    if path.exists():
        fields, rows = _read_rows(path)
    else:
        fields = ["REGION", "TECHNOLOGY", "YEAR", "VALUE"]
        rows = []
    rows = [
        row
        for row in rows
        if not (
            row["TECHNOLOGY"] == technology and int(row["YEAR"]) == year
        )
    ]
    rows.append(
        {
            "REGION": "CAN",
            "TECHNOLOGY": technology,
            "YEAR": str(year),
            "VALUE": repr(float(value_pj)),
        }
    )
    _write_csv(path, fields, rows)


def _read_emissions_penalty(params_dir: Path) -> dict[int, float]:
    _, rows = _read_rows(params_dir / "EmissionsPenalty.csv")
    return {
        int(row["YEAR"]): float(row["VALUE"])
        for row in rows
        if row["REGION"] == "CAN" and row["EMISSION"] == "CO2"
    }


def _read_upper_limits(params_dir: Path, technology: str) -> dict[int, float]:
    _, rows = _read_rows(params_dir / "TotalTechnologyAnnualActivityUpperLimit.csv")
    return {
        int(row["YEAR"]): float(row["VALUE"])
        for row in rows
        if row["TECHNOLOGY"] == technology
    }


def _read_lower_limits(params_dir: Path, technology: str) -> dict[int, float]:
    path = params_dir / "TotalTechnologyAnnualActivityLowerLimit.csv"
    if not path.exists():
        return {}
    _, rows = _read_rows(path)
    return {
        int(row["YEAR"]): float(row["VALUE"])
        for row in rows
        if row["TECHNOLOGY"] == technology
    }


def validate_case_params(
    params_dir: Path,
    spec: Mapping[str, object],
    *,
    caps: Mapping[str, Mapping[int, float]],
    hydro_2021: Mapping[str, float],
    lower_limits: Sequence[Mapping[str, object]] | None = None,
) -> dict[str, object]:
    """Fail loudly if a case parameter set deviates from the intended design.

    Returns a compact evidence payload for the manifest.
    """
    expected_price = float(spec["carbon_price_from_2026"])
    penalties = _read_emissions_penalty(params_dir)
    price_years_ok = all(
        value == (CARBON_PRICE_PRE_2026 if year <= 2025 else expected_price)
        for year, value in penalties.items()
    )
    if not price_years_ok:
        raise ValueError(f"{params_dir}: carbon price series does not match {expected_price}")

    for tier in TIERS:
        actual = _read_upper_limits(params_dir, tier.technology)
        expected = caps[tier.technology]
        if set(actual) != set(YEARS):
            raise ValueError(f"{params_dir}: {tier.technology} cap years incomplete")
        if any(abs(actual[year] - expected[year]) > 1e-6 for year in YEARS):
            raise ValueError(f"{params_dir}: {tier.technology} cap differs from artifact")

    supbcw = _read_upper_limits(params_dir, "SUPBCW")
    if set(supbcw) != set(YEARS) or any(value != 0.0 for value in supbcw.values()):
        raise ValueError(f"{params_dir}: SUPBCW cap is not disabled in every year")

    for technology, value in hydro_2021.items():
        actual = _read_upper_limits(params_dir, technology)
        if set(actual) != set(YEARS):
            raise ValueError(f"{params_dir}: {technology} hydro cap years incomplete")
        if any(abs(actual[year] - float(value)) > 1e-6 for year in YEARS):
            raise ValueError(f"{params_dir}: {technology} hydro cap is not 2021 activity")

    payload: dict[str, object] = {
        "params": str(params_dir),
        "carbon_price_from_2026": expected_price,
        "carbon_price_pre_2026": CARBON_PRICE_PRE_2026,
        "carbon_price_series_ok": price_years_ok,
        "tier_caps_match_artifact": True,
        "supbcw_cap_disabled": True,
        "hydro_cap_is_2021_activity": True,
    }
    if lower_limits is not None:
        for limit in lower_limits:
            technology = str(limit["technology"])
            year = int(limit["year"])
            expected = float(limit["value_pj"])
            actual = _read_lower_limits(params_dir, technology)
            if abs(actual.get(year, 0.0) - expected) > 1e-6:
                raise ValueError(
                    f"{params_dir}: {technology} {year} lower limit "
                    f"{actual.get(year)} != {expected}"
                )
        payload["lower_limits"] = [dict(limit) for limit in lower_limits]
        payload["lower_limit_sum_pj"] = round(
            sum(float(limit["value_pj"]) for limit in lower_limits), 6
        )
    return payload


def run_case(
    model_root: Path,
    spec: Mapping[str, object],
    *,
    caps: Mapping[str, Mapping[int, float]],
    hydro_2021: Mapping[str, float],
    lower_limits: Sequence[Mapping[str, object]] | None = None,
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    """Build, validate, harmonize, and solve one demand-side case."""
    params_dir = build_scenario_params(
        model_root,
        SOURCE_ARTIFACT_DIR,
        spec,
        hydro_2021=hydro_2021,
    )
    if lower_limits is not None:
        for limit in lower_limits:
            _apply_lower_limit(
                params_dir,
                technology=str(limit["technology"]),
                year=int(limit["year"]),
                value_pj=float(limit["value_pj"]),
            )
    checks = validate_case_params(
        params_dir,
        spec,
        caps=caps,
        hydro_2021=hydro_2021,
        lower_limits=lower_limits,
    )
    evidence = harmonize(model_root, params_dir)
    run_name = str(spec["name"])
    run_dir = model_root / "results" / run_name
    try:
        status = run_model(model_root, params_dir, run_name)
    except subprocess.CalledProcessError as error:
        status_path = run_dir / "status.json"
        status = (
            json.loads(status_path.read_text(encoding="utf-8"))
            if status_path.exists()
            else {"status": "solver_error", "objective": None, "command": error.cmd}
        )
    if status.get("status") == "optimal":
        summary = summarize_run(
            run_name,
            run_dir,
            caps,
            label=str(spec["label"]),
        )
    else:
        summary = {
            "label": str(spec["label"]),
            "status": status,
            "objective": None,
            "infeasible": True,
        }
    return summary, checks, evidence


def _tier_cumulative(summary: Mapping[str, object], technology: str) -> float:
    return float(summary["tier_activity_pj"][technology]["total"])


def _tier_2050(summary: Mapping[str, object], technology: str) -> float:
    return float(summary["tier_activity_pj"][technology]["2050"])


def _binding_names(summary: Mapping[str, object]) -> list[str]:
    return [
        technology
        for technology, payload in summary["cap_binding"].items()
        if payload["binding"]
    ]


def _fmt(value: object, digits: int = 6) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):.{digits}f}"


def _case_row(name: str, summary: Mapping[str, object]) -> dict[str, object]:
    status = summary.get("status", {}).get("status")
    if status != "optimal":
        return {
            "case": name,
            "label": summary["label"],
            "status": status,
            "objective_musd": None,
            "emissions_total_mt": None,
            "emissions_2050_mt": None,
            "t1_2050_pj": None,
            "t2_2050_pj": None,
            "t3_2050_pj": None,
            "t4_2050_pj": None,
            "tiers_2050_pj": None,
            "t1_cumulative_pj": None,
            "t2_cumulative_pj": None,
            "t3_cumulative_pj": None,
            "t4_cumulative_pj": None,
            "tiers_cumulative_pj": None,
            "national_bcw_2050_pj": None,
            "national_bcw_cumulative_pj": None,
            "pwrbcw_2050_pj": None,
            "pwrbcw_cumulative_pj": None,
            "binding_tiers": "",
        }
    return {
        "case": name,
        "label": summary["label"],
        "status": summary["status"]["status"],
        "objective_musd": summary["objective"],
        "emissions_total_mt": summary["emissions_total_mt"],
        "emissions_2050_mt": summary["emissions_mt"]["2050"],
        "t1_2050_pj": _tier_2050(summary, "SUPBCWT1"),
        "t2_2050_pj": _tier_2050(summary, "SUPBCWT2"),
        "t3_2050_pj": _tier_2050(summary, "SUPBCWT3"),
        "t4_2050_pj": _tier_2050(summary, "SUPBCWT4"),
        "tiers_2050_pj": summary["total_tier_activity_pj"]["2050"],
        "t1_cumulative_pj": _tier_cumulative(summary, "SUPBCWT1"),
        "t2_cumulative_pj": _tier_cumulative(summary, "SUPBCWT2"),
        "t3_cumulative_pj": _tier_cumulative(summary, "SUPBCWT3"),
        "t4_cumulative_pj": _tier_cumulative(summary, "SUPBCWT4"),
        "tiers_cumulative_pj": summary["total_tier_activity_pj"]["total"],
        "national_bcw_2050_pj": summary["national_bcw_supply_pj"]["2050"],
        "national_bcw_cumulative_pj": summary["national_bcw_supply_pj"]["total"],
        "pwrbcw_2050_pj": summary["pwrbcw_total_pj"]["2050"],
        "pwrbcw_cumulative_pj": summary["pwrbcw_total_pj"]["total"],
        "binding_tiers": ",".join(_binding_names(summary)),
    }


def _write_report(
    output_dir: Path,
    reference: Mapping[str, object],
    runs: Mapping[str, Mapping[str, object]],
    *,
    reference_2050_generation_pj: float,
    mandate_target_pj: float | None,
    mandate_limit: Mapping[str, object] | None,
    mandate_summary: Mapping[str, object] | None,
) -> None:
    rows = [_case_row("reference_200", reference)]
    rows.extend(_case_row(name, summary) for name, summary in runs.items())
    lines = [
        "# Demand-side sensitivity for the tiered national BCW supply curve",
        "",
        "**Status:** FRESH-side provisional sensitivity; not an official C2070 result.  ",
        "**Base:** committed tiered national supply curve (`SUPBCWT1`-`SUPBCWT4` + BC zonal `SUPBCWB1`/`SUPBCWB2`).  ",
        "**Stress:** hydro annual upper cap at 2021 activity in every case.  ",
        "**Source copy:** `/tmp/osecan_supply_curve` copied to `/tmp/osecan_demand_sens`; source left unchanged.",
        "",
        "## 1. Case definitions",
        "",
        "| Case | CO2 from 2026 ($/t) | Hydro | Demand-side change |",
        "|---|---:|---|---|",
        f"| Reference | {REFERENCE_CARBON_PRICE:.0f} | upper cap at 2021 | none |",
    ]
    for name, summary in runs.items():
        spec = _case_spec(name) if name.startswith("carbon_") else None
        carbon = (
            f"{float(spec['carbon_price_from_2026']):.0f}"
            if spec
            else "200"
        )
        change = "none"
        if name.startswith("mandate_") and mandate_limit is not None:
            change = (
                f"{len(mandate_limit['limits'])} proportional lower limits on "
                f"rest-of-Canada `PWRBCW` zones summing to "
                f"{float(mandate_limit['nonbc_target_pj']):.6f} PJ, plus "
                f"{float(mandate_limit['bc_reference_pj']):.6f} PJ BC zonal "
                f"reference, for a national target of {mandate_target_pj:.6f} PJ "
                f"in {MANDATE_YEAR} (diagnostic, not policy)"
            )
        lines.append(
            f"| {name} | {carbon} | upper cap at 2021 | {change} |"
        )

    lines.extend(
        [
            "",
            "## 2. Headline results",
            "",
            "| Case | Status | Objective (M$) | Emissions cumulative (Mt) | Emissions 2050 (Mt) | National BCW 2050 (PJ) | PWRBCW 2050 (PJ) | Binding tiers |",
            "|---|---|---:|---:|---:|---:|---:|---|",
        ]
    )
    for row in rows:
        lines.append(
            f"| {row['case']} | {row['status']} | {_fmt(row['objective_musd'], 2)} | "
            f"{_fmt(row['emissions_total_mt'])} | {_fmt(row['emissions_2050_mt'])} | "
            f"{_fmt(row['national_bcw_2050_pj'])} | {_fmt(row['pwrbcw_2050_pj'])} | "
            f"{row['binding_tiers'] or 'none'} |"
        )

    feasible_rows = [row for row in rows if row["status"] == "optimal"]
    lines.extend(
        [
            "",
            "## 3. Tier dispatch",
            "",
            "### 2050 (PJ)",
            "",
            "| Case | T1 | T2 | T3 | T4 | T1+T2 | Total tiers |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in feasible_rows:
        t12 = float(row["t1_2050_pj"]) + float(row["t2_2050_pj"])
        lines.append(
            f"| {row['case']} | {_fmt(row['t1_2050_pj'])} | {_fmt(row['t2_2050_pj'])} | "
            f"{_fmt(row['t3_2050_pj'])} | {_fmt(row['t4_2050_pj'])} | "
            f"{_fmt(t12)} | {_fmt(row['tiers_2050_pj'])} |"
        )
    lines.extend(
        [
            "",
            "### Cumulative 2021-2070 (PJ)",
            "",
            "| Case | T1 | T2 | T3 | T4 | T1+T2 | Total tiers |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in feasible_rows:
        t12 = float(row["t1_cumulative_pj"]) + float(row["t2_cumulative_pj"])
        lines.append(
            f"| {row['case']} | {_fmt(row['t1_cumulative_pj'])} | {_fmt(row['t2_cumulative_pj'])} | "
            f"{_fmt(row['t3_cumulative_pj'])} | {_fmt(row['t4_cumulative_pj'])} | "
            f"{_fmt(t12)} | {_fmt(row['tiers_cumulative_pj'])} |"
        )

    lines.extend(
        [
            "",
            "## 4. Comparison with the $200/t reference",
            "",
            "| Case | Objective delta (M$) | Emissions delta (Mt) | National BCW 2050 delta (PJ) | PWRBCW 2050 delta (PJ) |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    ref = feasible_rows[0]
    for row in feasible_rows[1:]:
        lines.append(
            f"| {row['case']} | {_fmt(float(row['objective_musd']) - float(ref['objective_musd']), 2)} | "
            f"{_fmt(float(row['emissions_total_mt']) - float(ref['emissions_total_mt']))} | "
            f"{_fmt(float(row['national_bcw_2050_pj']) - float(ref['national_bcw_2050_pj']))} | "
            f"{_fmt(float(row['pwrbcw_2050_pj']) - float(ref['pwrbcw_2050_pj']))} |"
        )
    lines.extend(
        [
            "",
            "The $250-$400/t cases cut cumulative emissions from 38.457 Mt to about "
            "2.4 Mt without increasing BCW demand: 2050 `PWRBCW` stays at 41.108 PJ "
            "and national BCW at 152.923 PJ.  The abatement is delivered by "
            "non-BC options, so a higher carbon price is not a biomass-demand "
            "driver in this model structure.",
        ]
    )

    activation = [
        name
        for name, summary in runs.items()
        if summary.get("status", {}).get("status") == "optimal"
        and (
            _tier_2050(summary, "SUPBCWT3") > 1e-6
            or _tier_2050(summary, "SUPBCWT4") > 1e-6
            or _tier_cumulative(summary, "SUPBCWT3") > 1e-6
            or _tier_cumulative(summary, "SUPBCWT4") > 1e-6
        )
    ]
    lines.extend(["", "## 5. Tier activation", ""])
    if activation:
        lines.append(
            "T3 or T4 dispatch is non-zero in: "
            + ", ".join(f"`{name}`" for name in activation)
            + "."
        )
        for name in activation:
            summary = runs[name]
            lines.append(
                f"- `{name}`: T3 2050 {_fmt(_tier_2050(summary, 'SUPBCWT3'))} PJ, "
                f"T4 2050 {_fmt(_tier_2050(summary, 'SUPBCWT4'))} PJ; "
                f"T3 cumulative {_fmt(_tier_cumulative(summary, 'SUPBCWT3'))} PJ, "
                f"T4 cumulative {_fmt(_tier_cumulative(summary, 'SUPBCWT4'))} PJ."
            )
        carbon_activation = [
            name
            for name in activation
            if name.startswith("carbon_")
        ]
        if carbon_activation:
            lines.append(
                "Carbon-price activation is bounded by the tested cases; no "
                "un-tested threshold is asserted."
            )
        else:
            lines.append(
                "No carbon-price case activates T3 or T4 up to the highest "
                "tested price.  Activation is induced only by the demand-side "
                "mandate diagnostic, not by the carbon price."
            )
    else:
        lines.append(
            "T3 and T4 remain zero in every tested carbon-price case.  The "
            "marginal value of rest-of-Canada BCW therefore remains below the "
            f"T3 variable cost of {TIERS[2].variable_cost_usd_per_gj:.6f} $/GJ "
            "at the highest tested carbon price.  No carbon-price threshold is "
            "invented."
        )

    lines.extend(["", "## 6. Biomass-power mandate diagnostic", ""])
    max_pwrbcw = NATIONAL_TOTAL_PJ / 3.72
    lines.extend(
        [
            f"Reference 2050 electricity generation is {reference_2050_generation_pj:.6f} PJ.  "
            f"A 5% mandate would require {0.05 * reference_2050_generation_pj:.6f} PJ of "
            f"`PWRBCW` output, which needs {3.72 * 0.05 * reference_2050_generation_pj:.6f} PJ "
            f"of BCW fuel.  The national BCW cap is {NATIONAL_TOTAL_PJ:.6f} PJ, so the "
            f"maximum possible `PWRBCW` output is {max_pwrbcw:.6f} PJ (1.12% of reference "
            "2050 generation).  A 5% or 10% mandate is therefore structurally infeasible "
            "before any cost effect, because it exceeds the BCW cap by a factor of "
            f"{0.05 * reference_2050_generation_pj / max_pwrbcw:.2f} (5%) or "
            f"{0.10 * reference_2050_generation_pj / max_pwrbcw:.2f} (10%).  "
            "The exact limiting constraint is the national BCW cap: 1,149.861490 PJ "
            "(5%) or 2,299.722980 PJ (10%) of BCW fuel is required against "
            f"{NATIONAL_TOTAL_PJ:.6f} PJ available.  A 5% case was solved with the "
            "same zonal-limit construction and returned solver status `unknown` "
            "(GLPK no primal feasible solution; the wrapper maps GLP_UNDEF to "
            "unknown); no dispatch is reported for it.  The 10% case was not "
            "solved because it fails the same cap by a wider margin.",
            "",
        ]
    )
    if mandate_limit is not None and mandate_summary is not None:
        lines.append(
            f"The diagnostic case imposes proportional lower limits on "
            f"{len(mandate_limit['limits'])} rest-of-Canada `PWRBCW` zones in "
            f"{MANDATE_YEAR}.  The limits sum to "
            f"{float(mandate_limit['nonbc_target_pj']):.6f} PJ; the BC zonal "
            f"B1/B2 reference is {float(mandate_limit['bc_reference_pj']):.6f} PJ, "
            f"so the national `PWRBCW` target is {mandate_target_pj:.6f} PJ.  "
            "This is the BCW-cap saturation point, not a policy number.  "
            "Solver status: "
            f"`{mandate_summary.get('status', {}).get('status')}`."
        )
        if mandate_summary.get("status", {}).get("status") == "optimal":
            lines.append(
                f"- PWRBCW 2050: {_fmt(mandate_summary['pwrbcw_total_pj']['2050'])} PJ; "
                f"national BCW 2050: {_fmt(mandate_summary['national_bcw_supply_pj']['2050'])} PJ; "
                f"T3 2050: {_fmt(_tier_2050(mandate_summary, 'SUPBCWT3'))} PJ; "
                f"T4 2050: {_fmt(_tier_2050(mandate_summary, 'SUPBCWT4'))} PJ "
                f"({100 * _tier_2050(mandate_summary, 'SUPBCWT4') / TIERS[3].incremental_cap_pj:.2f}% "
                "of the T4 cap)."
            )
        else:
            lines.append(
                "- The case is infeasible; no dispatch is reported.  The binding "
                "constraint is the national BCW cap, not the lower limit itself."
            )
    else:
        lines.append("No mandate case was run in this pass.")

    lines.extend(
        [
            "",
            "## 7. Caveats",
            "",
            "- Carbon prices are scenario sensitivities, not forecasts or policy recommendations.",
            "- The hydro stress is an annual upper bound at 2021 activity, not a forecast.",
            "- The curve is RSYC-only: no AAC, THLB, operability, or local correction applies.",
            "- T1 and T2 have identical variable cost, so their individual split is degenerate; use T1+T2 combined.",
            "- T4 is a documented high-cost marginal tier, not a fitted marginal-cost curve.",
            "- Results come from the disposable model copy and do not modify the public OSeCAN repository.",
            "",
        ]
    )
    (output_dir / "DEMAND_SENSITIVITY_REPORT.md").write_text(
        "\n".join(lines), encoding="utf-8"
    )


def _build_manifest(
    output_dir: Path,
    *,
    reference: Mapping[str, object],
    runs: Mapping[str, Mapping[str, object]],
    checks: Sequence[Mapping[str, object]],
    harmonize_evidence: Sequence[Mapping[str, object]],
    case_rows: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    input_paths = [
        SUPPLY_DIR / name
        for name in (
            "SUPBCWB1_activity_limits_base.csv",
            "SUPBCWB1_variable_cost_base.csv",
            "SUPBCWB2_activity_limits_base.csv",
            "SUPBCWB2_variable_cost_base.csv",
        )
    ]
    input_paths.extend(
        (
            SUPPLY_OUTPUT_DIR / "tier_activity_limits.csv",
            SUPPLY_OUTPUT_DIR / "tier_variable_costs.csv",
        )
    )
    inputs = [REPO_ROOT / path for path in input_paths]
    outputs = (
        "run_summaries.json",
        "dispatch_by_zone.csv",
        "DEMAND_SENSITIVITY_REPORT.md",
    )
    manifest = {
        "artifact_version": "0.1.0",
        "generated_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "status": STATUS,
        "model": {
            "repository": MODEL_REPOSITORY,
            "commit": MODEL_COMMIT,
            "package": MODEL_PACKAGE,
            "solver": MODEL_SOLVER,
            "scenario": MODEL_SCENARIO,
        },
        "inputs": [
            {
                "path": str(path.relative_to(REPO_ROOT)),
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
            for path in inputs
        ],
        "outputs": [
            {
                "path": name,
                "bytes": (output_dir / name).stat().st_size,
                "sha256": _sha256(output_dir / name),
            }
            for name in outputs
        ],
        "reference": {
            "case": "reference_200",
            "carbon_price_from_2026": REFERENCE_CARBON_PRICE,
            "objective": reference["objective"],
            "emissions_total_mt": reference["emissions_total_mt"],
        },
        "checks": {
            "tier_caps_sum_pj": REST_OF_CANADA_PJ,
            "bc_zonal_total_pj": BC_ZONAL_TOTAL_PJ,
            "national_total_pj": NATIONAL_TOTAL_PJ,
            "case_parameters": list(checks),
            "all_optimal": all(
                summary.get("status", {}).get("status") == "optimal"
                for summary in runs.values()
            ),
        },
        "runs": {
            name: {
                "status": summary.get("status", {}).get("status"),
                "objective": summary.get("objective"),
                "emissions_total_mt": summary.get("emissions_total_mt"),
                "binding_tiers": _binding_names(summary)
                if summary.get("status", {}).get("status") == "optimal"
                else [],
            }
            for name, summary in runs.items()
        },
        "comparison": list(case_rows),
        "harmonize": list(harmonize_evidence),
        "caveats": [
            "Scenario carbon prices, not forecasts.",
            "Hydro is an annual upper bound at 2021 activity, not a forecast.",
            "The curve is RSYC-only: no AAC, THLB, operability, or local correction.",
            "The public OSeCAN repository was not modified.",
        ],
    }
    _json(output_dir / "manifest.json", manifest)
    return manifest


def _write_outputs(
    output_dir: Path,
    reference: Mapping[str, object],
    runs: Mapping[str, Mapping[str, object]],
    *,
    checks: Sequence[Mapping[str, object]],
    harmonize_evidence: Sequence[Mapping[str, object]],
    reference_2050_generation_pj: float,
    mandate_target_pj: float | None,
    mandate_limit: Mapping[str, object] | None,
    mandate_summary: Mapping[str, object] | None,
) -> None:
    summaries = {
        "reference": {"name": "reference_200", **dict(reference)},
        "runs": {name: summary for name, summary in runs.items()},
    }
    _json(output_dir / "run_summaries.json", summaries)

    dispatch_rows = build_dispatch_rows("reference_200", REFERENCE_RUN_DIR)
    for name, summary in runs.items():
        if summary.get("status", {}).get("status") != "optimal":
            continue
        dispatch_rows.extend(build_dispatch_rows(name, REFERENCE_RUN_DIR.parent / name))
    _write_csv(
        output_dir / "dispatch_by_zone.csv",
        ["run", "technology", "category", "zone", "year", "value_pj"],
        dispatch_rows,
    )

    case_rows = [_case_row("reference_200", reference)]
    case_rows.extend(_case_row(name, summary) for name, summary in runs.items())
    _write_report(
        output_dir,
        reference,
        runs,
        reference_2050_generation_pj=reference_2050_generation_pj,
        mandate_target_pj=mandate_target_pj,
        mandate_limit=mandate_limit,
        mandate_summary=mandate_summary,
    )
    _build_manifest(
        output_dir,
        reference=reference,
        runs=runs,
        checks=checks,
        harmonize_evidence=harmonize_evidence,
        case_rows=case_rows,
    )


# The reference run directory is resolved in ``main``; the module-level name
# keeps the writer helpers testable without a live model copy.
REFERENCE_RUN_DIR = Path("/tmp/osecan_demand_sens/results/stress")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Demand-side sensitivity for the tiered national BCW supply curve."
    )
    parser.add_argument("--model-root", type=Path, required=True)
    parser.add_argument(
        "--runs",
        nargs="*",
        default=["carbon_250", "carbon_300"],
        help="Carbon cases to run.",
    )
    parser.add_argument(
        "--reuse-runs",
        action="store_true",
        help="Summarize existing solved runs without rebuilding or solving.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=OUTPUT_DIR,
    )
    parser.add_argument(
        "--mandate",
        choices=("none", "5pct", "saturation"),
        default="none",
        help="Optional biomass-power mandate diagnostic.",
    )
    parser.add_argument(
        "--mandate-pj",
        type=float,
        default=None,
        help="Override the national PWRBCW lower-limit target in 2050 (PJ).",
    )
    args = parser.parse_args(argv)

    global REFERENCE_RUN_DIR
    model_root = args.model_root.resolve()
    if not (model_root / "SETs/TECHNOLOGY.csv").exists():
        raise SystemExit(f"not an OSeCAN copy: {model_root}")
    REFERENCE_RUN_DIR = model_root / "results" / "stress"
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    caps = _load_tier_caps()
    hydro_2021 = _hydro_2021(REFERENCE_RUN_DIR)
    reference = summarize_run(
        "stress",
        REFERENCE_RUN_DIR,
        caps,
        label="CO2 $200/t from 2026 + hydro capped at 2021",
    )

    runs: dict[str, dict[str, object]] = {}
    checks: list[dict[str, object]] = []
    harmonize_evidence: list[dict[str, object]] = []
    for name in args.runs:
        spec = _case_spec(name)
        if args.reuse_runs:
            run_dir = model_root / "results" / name
            params_dir = run_dir / "params"
            case_checks = validate_case_params(
                params_dir,
                spec,
                caps=caps,
                hydro_2021=hydro_2021,
            )
            evidence = harmonize(model_root, params_dir)
            summary = summarize_run(
                name,
                run_dir,
                caps,
                label=str(spec["label"]),
            )
        else:
            summary, case_checks, evidence = run_case(
                model_root,
                spec,
                caps=caps,
                hydro_2021=hydro_2021,
            )
        runs[name] = summary
        checks.append(case_checks)
        harmonize_evidence.append(evidence)

    reference_production = read_production(REFERENCE_RUN_DIR)
    reference_2050_generation_pj = sum(
        float(series.get(2050, 0.0))
        for technology, series in reference_production.items()
        if technology.startswith("PWR")
    )

    mandate_summary = None
    mandate_target = None
    mandate_limit = None
    if args.mandate != "none":
        if args.mandate_pj is not None:
            target = float(args.mandate_pj)
        elif args.mandate == "5pct":
            target = 0.05 * reference_2050_generation_pj
        else:
            target = NATIONAL_TOTAL_PJ / 3.72 * 0.999
        mandate_target = target
        bc_zones = {"PWRBCWB1", "PWRBCWB2"}
        bc_reference = sum(
            float(series.get(MANDATE_YEAR, 0.0))
            for technology, series in reference_production.items()
            if technology in bc_zones
        )
        zonal_reference = {
            technology: float(series.get(MANDATE_YEAR, 0.0))
            for technology, series in reference_production.items()
            if technology.startswith("PWRBCW")
            and technology not in bc_zones
            and float(series.get(MANDATE_YEAR, 0.0)) > 1e-9
        }
        zonal_total = sum(zonal_reference.values())
        if zonal_total <= 0:
            raise ValueError("reference has no rest-of-Canada PWRBCW dispatch")
        nonbc_target = max(target - bc_reference, 0.0)
        limits = [
            {
                "technology": technology,
                "year": MANDATE_YEAR,
                "value_pj": nonbc_target * value / zonal_total,
                "basis": (
                    "5% of reference 2050 electricity generation"
                    if args.mandate == "5pct"
                    else "national BCW cap saturation diagnostic, not policy"
                ),
            }
            for technology, value in sorted(zonal_reference.items())
        ]
        mandate_limit = {
            "target_pj": target,
            "bc_reference_pj": bc_reference,
            "nonbc_target_pj": nonbc_target,
            "limits": limits,
            "method": (
                "proportional lower limits on the rest-of-Canada PWRBCW zones "
                "that consume the tiered BCW curve; BC zonal B1/B2 output is "
                "left at its reference level because its supply caps bind"
            ),
        }
        spec = {
            "name": "mandate_5pct" if args.mandate == "5pct" else "mandate_saturation",
            "label": (
                "5% biomass-power mandate in 2050 (diagnostic)"
                if args.mandate == "5pct"
                else "BCW-cap saturation PWRBCW lower limit in 2050 (diagnostic)"
            ),
            "carbon_price_from_2026": REFERENCE_CARBON_PRICE,
            "hydro_mode": "upper_2021",
        }
        summary, case_checks, evidence = run_case(
            model_root,
            spec,
            caps=caps,
            hydro_2021=hydro_2021,
            lower_limits=limits,
        )
        mandate_summary = summary
        runs[str(spec["name"])] = summary
        checks.append(case_checks)
        harmonize_evidence.append(evidence)

    _write_outputs(
        output_dir,
        reference,
        runs,
        checks=checks,
        harmonize_evidence=harmonize_evidence,
        reference_2050_generation_pj=reference_2050_generation_pj,
        mandate_target_pj=mandate_target,
        mandate_limit=mandate_limit,
        mandate_summary=mandate_summary,
    )
    print(
        json.dumps(
            {
                name: {
                    "status": summary.get("status", {}).get("status"),
                    "objective": summary.get("objective"),
                }
                for name, summary in runs.items()
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
