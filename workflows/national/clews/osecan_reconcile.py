"""Summarise the reconciled zonal OSeCAN run.

This module records the second zonal experiment after the BC residue caps were
reconciled to the refined all-BC chain.  It reads the solved CSVs from a
disposable OSeCAN copy and writes only summaries; the public OSeCAN repository
is never modified.

Run from the repository root::

    python -m workflows.national.clews.osecan_reconcile \
        --runs-root /tmp/osecan_zonal/results
"""

from __future__ import annotations

import argparse
import csv
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from workflows.national.clews.osecan_zonal import (
    KEY_YEARS,
    MODEL_COMMIT,
    MODEL_PACKAGE,
    MODEL_REPOSITORY,
    MODEL_SOLVER,
    STATUS,
    SUPPLY_DIR,
    YEARS,
    RunSpec,
    _relative,
    _sha256,
    _write_csv,
    _write_json,
    read_production,
    summarize_run,
)

__all__ = [
    "OUTPUT_DIR",
    "RECONCILE_RUNS",
    "build_dispatch_rows",
    "build_manifest",
    "build_run_summaries",
    "markdown_report",
    "write_artifacts",
]

REPO_ROOT = Path(__file__).resolve().parents[3]
OUTPUT_DIR = SUPPLY_DIR / "osecan" / "reconcile_ffcm"

# Original published run (pre-refinement cap basis).
OLD_CONTROL_OBJECTIVE_MUSD = 1_371_553.35
OLD_ZONAL_OBJECTIVE_MUSD = 1_371_600.56
OLD_CONTROL_EMISSIONS_MT = 43.990577
OLD_ZONAL_EMISSIONS_MT = 61.65911
OLD_B2_CAP_PJ = 39.286509195
OLD_B1_CAP_PJ = 3.672285606
OLD_B2_BINDING_YEARS = "2032-2070"
OLD_B1_BINDING_YEARS = "2026-2070"
OLD_PWRBCWB1_2050_PJ = 0.987175
OLD_PWRBCWB2_2050_PJ = 10.560887

# Interim held-calibration run (2026-11-18, stale B2 recovery 0.583931).
INTERIM_CONTROL_OBJECTIVE_MUSD = 1_371_586.14
INTERIM_ZONAL_OBJECTIVE_MUSD = 1_371_621.94
INTERIM_CONTROL_EMISSIONS_MT = 46.930848
INTERIM_ZONAL_EMISSIONS_MT = 61.65911
INTERIM_B1_CAP_PJ = 3.605474907
INTERIM_B2_CAP_PJ = 32.204470802
INTERIM_B2_BINDING_YEARS = "2030-2070"
INTERIM_B1_BINDING_YEARS = "2026-2070"
INTERIM_PWRBCWB1_2050_PJ = 0.969212
INTERIM_PWRBCWB2_2050_PJ = 8.657124

RECONCILE_RUNS = (
    RunSpec(
        name="control",
        label="national control with FFCM-implied total cap",
        description=(
            "National SUPBCW with the adopted FFCM-implied B1+B2 total cap "
            "(43.524 PJ from 2028); zonal SUPBCWB1/B2 disabled; PWRHYD<z> "
            "fixed at 2021 activity; CO2 95 $/t to 2025 and 200 $/t from 2026."
        ),
        zonal_supply=False,
        generic_bio_cost_usd_per_gj=3.0,
    ),
    RunSpec(
        name="zonal",
        label="zonal BC supply with FFCM-implied caps",
        description=(
            "Zonal SUPBCWB1/B2 with the adopted FFCM-implied caps (B1 3.605 "
            "PJ, B2 39.918 PJ from 2028); national SUPBCW disabled; "
            "PWRHYD<z> fixed at 2021 activity; CO2 95 $/t to 2025 and 200 $/t "
            "from 2026."
        ),
        zonal_supply=True,
        generic_bio_cost_usd_per_gj=3.0,
    ),
)


@dataclass(frozen=True, slots=True)
class CapSet:
    """Annual caps and costs for the three BC supply technologies."""

    caps: Mapping[str, Mapping[int, float]]
    costs: Mapping[str, Mapping[int, float]]


def read_series_csv(path: Path, technology: str) -> dict[int, float]:
    """Read one technology's annual series from a parameter CSV."""
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    series = {
        int(row["YEAR"]): float(row["VALUE"])
        for row in rows
        if row.get("TECHNOLOGY") == technology
    }
    if not series:
        raise ValueError(f"{technology} has no rows in {path}")
    outside = sorted(set(series) - set(YEARS))
    if outside:
        raise ValueError(f"{technology} has years outside the horizon: {outside}")
    return dict(sorted(series.items()))


def load_caps(root: Path) -> CapSet:
    """Load the reconciled caps and costs from the published supply files."""
    supply_dir = root / SUPPLY_DIR
    caps = {
        "SUPBCWB1": read_series_csv(
            supply_dir / "SUPBCWB1_activity_limits_base.csv", "SUPBCWB1"
        ),
        "SUPBCWB2": read_series_csv(
            supply_dir / "SUPBCWB2_activity_limits_base.csv", "SUPBCWB2"
        ),
        "SUPBCW": read_series_csv(
            supply_dir / "comparison/SUPBCW_activity_limits_base.csv", "SUPBCW"
        ),
    }
    costs = {
        "SUPBCWB1": read_series_csv(
            supply_dir / "SUPBCWB1_variable_cost_base.csv", "SUPBCWB1"
        ),
        "SUPBCWB2": read_series_csv(
            supply_dir / "SUPBCWB2_variable_cost_base.csv", "SUPBCWB2"
        ),
        "SUPBCW": read_series_csv(
            supply_dir / "comparison/SUPBCW_variable_cost_base.csv", "SUPBCW"
        ),
    }
    return CapSet(caps=caps, costs=costs)


def _series_total(series: Mapping[int, float]) -> float:
    return round(sum(float(value) for value in series.values()), 6)


def _dispatch_rows_for(run: str, production: Mapping[str, Mapping[int, float]]) -> list[dict[str, object]]:
    technologies = [
        "SUPBCW",
        "SUPBCWB1",
        "SUPBCWB2",
        "PWRBCWB1",
        "PWRBCWB2",
        *(f"PWRBCW{zone}" for zone in ("A1", "A2", "S1", "M1", "O1", "O2", "Q1", "Q2", "T1", "L1", "N1")),
        *(f"PWRBIO{zone}" for zone in ("B1", "B2", "A1", "A2", "S1", "M1", "O1", "O2", "Q1", "Q2", "T1", "L1", "N1")),
    ]
    rows: list[dict[str, object]] = []
    for technology in technologies:
        if technology == "SUPBCW":
            category, zone = "supply", "CAN"
        elif technology.startswith("SUPBCW"):
            category, zone = "supply", technology[-2:]
        else:
            category, zone = "dispatch", technology[-2:]
        series = production.get(technology, {})
        for year in YEARS:
            rows.append(
                {
                    "run": run,
                    "technology": technology,
                    "category": category,
                    "zone": zone,
                    "year": year,
                    "value_pj": round(float(series.get(year, 0.0)), 6),
                }
            )
    return rows


def build_dispatch_rows(runs_root: Path) -> list[dict[str, object]]:
    """Build the long-format dispatch table for the two reconciled runs."""
    rows: list[dict[str, object]] = []
    for spec in RECONCILE_RUNS:
        rows.extend(_dispatch_rows_for(spec.name, read_production(runs_root / spec.name)))
    return rows


def build_run_summaries(
    runs_root: Path, root: Path = REPO_ROOT
) -> dict[str, object]:
    """Build the run summaries and the old-vs-new comparison block."""
    cap_set = load_caps(root)
    runs = {
        spec.name: summarize_run(runs_root / spec.name, cap_set.caps, spec)
        for spec in RECONCILE_RUNS
    }
    control = runs["control"]
    zonal = runs["zonal"]
    comparison = {
        "objective_delta_musd": round(
            float(zonal["objective_musd"]) - float(control["objective_musd"]), 2
        ),
        "emissions_delta_mt": round(
            float(zonal["emissions_mt"]["total"])
            - float(control["emissions_mt"]["total"]),
            6,
        ),
        "bcw_supply_delta_pj": round(
            (
                float(zonal["supbcwb1_pj"]["total"])
                + float(zonal["supbcwb2_pj"]["total"])
            )
            - float(control["supbcw_pj"]["total"]),
            6,
        ),
        "original_published_run": {
            "control_objective_musd": OLD_CONTROL_OBJECTIVE_MUSD,
            "zonal_objective_musd": OLD_ZONAL_OBJECTIVE_MUSD,
            "control_emissions_mt": OLD_CONTROL_EMISSIONS_MT,
            "zonal_emissions_mt": OLD_ZONAL_EMISSIONS_MT,
            "zonal_b1_cap_pj": OLD_B1_CAP_PJ,
            "zonal_b2_cap_pj": OLD_B2_CAP_PJ,
            "zonal_b1_binding_years": OLD_B1_BINDING_YEARS,
            "zonal_b2_binding_years": OLD_B2_BINDING_YEARS,
            "pwrbcwb1_2050_pj": OLD_PWRBCWB1_2050_PJ,
            "pwrbcwb2_2050_pj": OLD_PWRBCWB2_2050_PJ,
            "control_objective_delta_musd": round(
                float(control["objective_musd"]) - OLD_CONTROL_OBJECTIVE_MUSD, 2
            ),
            "zonal_objective_delta_musd": round(
                float(zonal["objective_musd"]) - OLD_ZONAL_OBJECTIVE_MUSD, 2
            ),
            "control_emissions_delta_mt": round(
                float(control["emissions_mt"]["total"]) - OLD_CONTROL_EMISSIONS_MT, 6
            ),
            "zonal_emissions_delta_mt": round(
                float(zonal["emissions_mt"]["total"]) - OLD_ZONAL_EMISSIONS_MT, 6
            ),
        },
        "interim_held_run": {
            "control_objective_musd": INTERIM_CONTROL_OBJECTIVE_MUSD,
            "zonal_objective_musd": INTERIM_ZONAL_OBJECTIVE_MUSD,
            "control_emissions_mt": INTERIM_CONTROL_EMISSIONS_MT,
            "zonal_emissions_mt": INTERIM_ZONAL_EMISSIONS_MT,
            "zonal_b1_cap_pj": INTERIM_B1_CAP_PJ,
            "zonal_b2_cap_pj": INTERIM_B2_CAP_PJ,
            "zonal_b1_binding_years": INTERIM_B1_BINDING_YEARS,
            "zonal_b2_binding_years": INTERIM_B2_BINDING_YEARS,
            "pwrbcwb1_2050_pj": INTERIM_PWRBCWB1_2050_PJ,
            "pwrbcwb2_2050_pj": INTERIM_PWRBCWB2_2050_PJ,
            "control_objective_delta_musd": round(
                float(control["objective_musd"]) - INTERIM_CONTROL_OBJECTIVE_MUSD, 2
            ),
            "zonal_objective_delta_musd": round(
                float(zonal["objective_musd"]) - INTERIM_ZONAL_OBJECTIVE_MUSD, 2
            ),
            "control_emissions_delta_mt": round(
                float(control["emissions_mt"]["total"]) - INTERIM_CONTROL_EMISSIONS_MT, 6
            ),
            "zonal_emissions_delta_mt": round(
                float(zonal["emissions_mt"]["total"]) - INTERIM_ZONAL_EMISSIONS_MT, 6
            ),
        },
    }
    return {
        "generated_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "status": STATUS,
        "model": {
            "repository": MODEL_REPOSITORY,
            "commit": MODEL_COMMIT,
            "package": MODEL_PACKAGE,
            "solver": MODEL_SOLVER,
        },
        "forcing": {
            "scenario": "Current Measures",
            "carbon_price_usd_per_t": {"2021-2025": 95.0, "2026-2070": 200.0},
            "hydro": "PWRHYD<z> fixed at 2021 base-run activity",
            "generic_bio_cost_usd_per_gj": 3.0,
        },
        "caps": {
            technology: {
                "by_year": {str(year): round(value, 9) for year, value in series.items()},
                **{str(year): round(series[year], 9) for year in KEY_YEARS if year in series},
            }
            for technology, series in cap_set.caps.items()
        },
        "costs_usd_per_gj": {
            technology: {
                "by_year": {str(year): round(value, 9) for year, value in series.items()},
            }
            for technology, series in cap_set.costs.items()
        },
        "runs": runs,
        "comparison": comparison,
    }


def build_manifest(
    root: Path,
    output_dir: Path,
    outputs: Sequence[Path],
    generated_utc: str,
) -> dict[str, object]:
    """Build the artifact manifest with input and output hashes."""
    supply_dir = root / SUPPLY_DIR
    inputs = [
        supply_dir / "SUPBCWB1_activity_limits_base.csv",
        supply_dir / "SUPBCWB1_variable_cost_base.csv",
        supply_dir / "SUPBCWB2_activity_limits_base.csv",
        supply_dir / "SUPBCWB2_variable_cost_base.csv",
        supply_dir / "comparison/SUPBCW_activity_limits_base.csv",
        supply_dir / "comparison/SUPBCW_variable_cost_base.csv",
        supply_dir / "bc_residue_cases_summary.json",
        supply_dir / "manifest.json",
    ]
    input_rows = []
    for path in inputs:
        if not path.exists():
            raise FileNotFoundError(f"missing manifest input: {path}")
        input_rows.append(
            {
                "path": _relative(path, root),
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
        )
    output_rows = []
    for path in outputs:
        if not path.exists():
            raise FileNotFoundError(f"missing manifest output: {path}")
        output_rows.append(
            {
                "path": _relative(path, root),
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
        )
    return {
        "artifact_version": "0.1.0",
        "generated_utc": generated_utc,
        "status": STATUS,
        "model": {
            "repository": MODEL_REPOSITORY,
            "commit": MODEL_COMMIT,
            "package": MODEL_PACKAGE,
            "solver": MODEL_SOLVER,
        },
        "inputs": input_rows,
        "outputs": output_rows,
        "run_evidence": {
            "runs_root": "/tmp/osecan_zonal/results",
            "public_repo_modified": False,
            "note": (
                "Only summaries are committed; the disposable OSeCAN copy and "
                "the public repository are not modified."
            ),
        },
    }


def _fmt(value: object, digits: int = 3) -> str:
    if value is None:
        return ""
    return f"{float(value):,.{digits}f}"


def _binding_range(block: Mapping[str, object]) -> str:
    return str(block.get("binding_years") or "none")


def markdown_report(summaries: Mapping[str, object]) -> str:
    """Render the FFCM-implied reconciliation run report."""
    runs = summaries["runs"]
    control = runs["control"]
    zonal = runs["zonal"]
    comparison = summaries["comparison"]
    original = comparison["original_published_run"]
    interim = comparison["interim_held_run"]
    caps = summaries["caps"]

    def series_rows(key: str, digits: int = 3) -> str:
        rows = []
        for year in KEY_YEARS:
            rows.append(
                f"| {year} | {_fmt(control[key][str(year)], digits)} | "
                f"{_fmt(zonal[key][str(year)], digits)} |"
            )
        return "\n".join(rows)

    cap_rows = []
    for technology in ("SUPBCW", "SUPBCWB1", "SUPBCWB2"):
        cap = caps[technology]
        cap_rows.append(
            f"| {technology} | {_fmt(cap.get('2028'), 6)} | "
            f"{_fmt(cap.get('2070'), 6)} |"
        )

    binding_rows = []
    for technology in ("SUPBCW", "SUPBCWB1", "SUPBCWB2"):
        cb = zonal["cap_binding"][technology]
        binding_rows.append(
            f"| {technology} | {_fmt(cb['max_utilisation'], 3)} | "
            f"{'yes' if cb['binding'] else 'no'} | {_binding_range(cb)} |"
        )

    return f"""# FFCM-implied zonal OSeCAN reconciliation

Status: **{STATUS}**.  The BC residue base was resolved to the recomputed
FFCM-implied B2 recovery 0.723799161 (not the stale published 0.583931303) and
the caps were rebuilt with the chain (B1 {_fmt(caps['SUPBCWB1'].get('2028'), 6)}
PJ, B2 {_fmt(caps['SUPBCWB2'].get('2028'), 6)} PJ, total
{_fmt(caps['SUPBCW'].get('2028'), 6)} PJ from 2028).  The `Current Measures`
experiment was rerun in a disposable OSeCAN copy at commit
`{MODEL_COMMIT[:7]}`.  The public `deltae/C2070` repository was not modified.

## Caps loaded

| Technology | 2028 (PJ) | 2070 (PJ) |
|---|---:|---:|
{chr(10).join(cap_rows)}

## Solver and objective

| Run | Status | Objective (M$) | LP rows | LP cols |
|---|---|---:|---:|---:|
| control | {control['solver_status']} | {_fmt(control['objective_musd'], 2)} | {control['lp_rows']:,} | {control['lp_cols']:,} |
| zonal | {zonal['solver_status']} | {_fmt(zonal['objective_musd'], 2)} | {zonal['lp_rows']:,} | {zonal['lp_cols']:,} |

Objective deltas against the two earlier bases:

| Basis | Control (M$) | Zonal (M$) | Control delta (M$) | Zonal delta (M$) |
|---|---:|---:|---:|---:|
| Original published (42.9588 PJ) | {_fmt(original['control_objective_musd'], 2)} | {_fmt(original['zonal_objective_musd'], 2)} | {_fmt(original['control_objective_delta_musd'], 2)} | {_fmt(original['zonal_objective_delta_musd'], 2)} |
| Interim held (35.8099 PJ) | {_fmt(interim['control_objective_musd'], 2)} | {_fmt(interim['zonal_objective_musd'], 2)} | {_fmt(interim['control_objective_delta_musd'], 2)} | {_fmt(interim['zonal_objective_delta_musd'], 2)} |

## SUP activity by year (PJ)

National `SUPBCW` (control only):

| Year | Control | Zonal |
|---:|---:|---:|
{series_rows('supbcw_pj')}

Zonal `SUPBCWB1`:

| Year | Control | Zonal |
|---:|---:|---:|
{series_rows('supbcwb1_pj')}

Zonal `SUPBCWB2`:

| Year | Control | Zonal |
|---:|---:|---:|
{series_rows('supbcwb2_pj')}

## PWRBCWB1/B2 dispatch (PJ)

| Year | PWRBCWB1 control | PWRBCWB1 zonal | PWRBCWB2 control | PWRBCWB2 zonal |
|---:|---:|---:|---:|---:|
{chr(10).join(
    f"| {year} | {_fmt(control['pwrbcwb1_pj'][str(year)])} | "
    f"{_fmt(zonal['pwrbcwb1_pj'][str(year)])} | "
    f"{_fmt(control['pwrbcwb2_pj'][str(year)])} | "
    f"{_fmt(zonal['pwrbcwb2_pj'][str(year)])} |"
    for year in KEY_YEARS
)}

## Cap binding (zonal run)

| Technology | Max utilisation | Binding | Binding years |
|---|---:|---|---|
{chr(10).join(binding_rows)}

## Other-zone PWRBCW dispatch (PJ)

| Year | Control | Zonal |
|---:|---:|---:|
{chr(10).join(
    f"| {year} | {_fmt(control['pwrbcw_other_zone_pj'][str(year)])} | "
    f"{_fmt(zonal['pwrbcw_other_zone_pj'][str(year)])} |"
    for year in KEY_YEARS
)}

Totals: control {_fmt(control['pwrbcw_other_zone_pj']['total_pj'], 3)} PJ;
zonal {_fmt(zonal['pwrbcw_other_zone_pj']['total_pj'], 3)} PJ.

## Emissions

| Year | Control (Mt CO2) | Zonal (Mt CO2) |
|---:|---:|---:|
{chr(10).join(
    f"| {year} | {_fmt(control['emissions_mt'][str(year)], 6)} | "
    f"{_fmt(zonal['emissions_mt'][str(year)], 6)} |"
    for year in KEY_YEARS
)}

Cumulative: control {_fmt(control['emissions_mt']['total'], 6)} Mt;
zonal {_fmt(zonal['emissions_mt']['total'], 6)} Mt;
delta {_fmt(comparison['emissions_delta_mt'], 6)} Mt.

## Three-basis comparison

| Metric | Original 42.9588 PJ | Interim 35.8099 PJ | FFCM-implied |
|---|---:|---:|---:|
| Zonal B1 cap 2028+ (PJ) | {_fmt(original['zonal_b1_cap_pj'], 6)} | {_fmt(interim['zonal_b1_cap_pj'], 6)} | {_fmt(caps['SUPBCWB1'].get('2028'), 6)} |
| Zonal B2 cap 2028+ (PJ) | {_fmt(original['zonal_b2_cap_pj'], 6)} | {_fmt(interim['zonal_b2_cap_pj'], 6)} | {_fmt(caps['SUPBCWB2'].get('2028'), 6)} |
| Zonal B1 binding years | {original['zonal_b1_binding_years']} | {interim['zonal_b1_binding_years']} | {_binding_range(zonal['cap_binding']['SUPBCWB1'])} |
| Zonal B2 binding years | {original['zonal_b2_binding_years']} | {interim['zonal_b2_binding_years']} | {_binding_range(zonal['cap_binding']['SUPBCWB2'])} |
| PWRBCWB1 2050 (PJ) | {_fmt(original['pwrbcwb1_2050_pj'], 6)} | {_fmt(interim['pwrbcwb1_2050_pj'], 6)} | {_fmt(zonal['pwrbcwb1_pj']['2050'], 6)} |
| PWRBCWB2 2050 (PJ) | {_fmt(original['pwrbcwb2_2050_pj'], 6)} | {_fmt(interim['pwrbcwb2_2050_pj'], 6)} | {_fmt(zonal['pwrbcwb2_pj']['2050'], 6)} |
| Zonal objective (M$) | {_fmt(original['zonal_objective_musd'], 2)} | {_fmt(interim['zonal_objective_musd'], 2)} | {_fmt(zonal['objective_musd'], 2)} |
| Control objective (M$) | {_fmt(original['control_objective_musd'], 2)} | {_fmt(interim['control_objective_musd'], 2)} | {_fmt(control['objective_musd'], 2)} |
| Zonal emissions (Mt) | {_fmt(original['zonal_emissions_mt'], 6)} | {_fmt(interim['zonal_emissions_mt'], 6)} | {_fmt(zonal['emissions_mt']['total'], 6)} |
| Control emissions (Mt) | {_fmt(original['control_emissions_mt'], 6)} | {_fmt(interim['control_emissions_mt'], 6)} | {_fmt(control['emissions_mt']['total'], 6)} |

The zonal run is the cap-constrained case; the control uses the same total
energy cap as one national technology.  All runs solved to the status shown
above.  The FFCM-implied B2 cap is 7.714 PJ/yr above the interim held cap and
0.632 PJ/yr above the original published cap; the dispatch response is
reported in the tables, not asserted as a forecast.
"""

def write_artifacts(
    root: Path = REPO_ROOT,
    runs_root: Path | None = None,
) -> dict[str, object]:
    """Build and write the reconciled run artifacts."""
    runs_root = runs_root or Path("/tmp/osecan_zonal/results")
    output_dir = root / OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    generated_utc = datetime.now(UTC).isoformat(timespec="seconds")

    summaries = build_run_summaries(runs_root, root)
    dispatch_rows = build_dispatch_rows(runs_root)

    summaries_path = output_dir / "run_summaries.json"
    dispatch_path = output_dir / "dispatch_by_zone.csv"
    report_path = output_dir / "RECONCILE_FFCM_REPORT.md"
    manifest_path = output_dir / "manifest.json"

    _write_json(summaries_path, summaries)
    _write_csv(dispatch_path, dispatch_rows)
    report_path.write_text(markdown_report(summaries), encoding="utf-8")
    manifest = build_manifest(
        root,
        output_dir,
        [summaries_path, dispatch_path, report_path],
        generated_utc,
    )
    _write_json(manifest_path, manifest)
    return {
        "summaries": summaries,
        "dispatch_rows": dispatch_rows,
        "paths": {
            "summaries": str(summaries_path),
            "dispatch": str(dispatch_path),
            "report": str(report_path),
            "manifest": str(manifest_path),
        },
    }


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Summarise the reconciled zonal OSeCAN run."
    )
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    parser.add_argument("--runs-root", type=Path, default=Path("/tmp/osecan_zonal/results"))
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    payload = write_artifacts(args.root.resolve(), args.runs_root.resolve())
    print(json.dumps(payload["paths"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
