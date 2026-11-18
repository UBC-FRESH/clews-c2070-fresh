"""C2070 national OSeCAN base run at the observed 2024 harvest basis.

The base is 118,977,339 m3 of log volume per year, the FSD 2024 national
observed harvest.  It uses the same RSYC-only v0 conversion as the 150M
experiment and keeps the BC zonal ``SUPBCWB1``/``SUPBCWB2`` caps unchanged, so
``SUPBCW`` carries only the rest-of-Canada balance.

The module writes parameter files, builds scenario parameter sets in a
disposable OSeCAN copy, solves the baseline and stress runs, and writes the
committed summaries, dispatch table, report, and manifest.  It never edits the
public OSeCAN repository.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import subprocess
import sys
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

from workflows.national.clews import osecan_national as national
from workflows.national.clews.osecan_zonal import (
    BC_ZONES,
    HORIZON_END,
    HORIZON_START,
    OTHER_ZONES,
    SUPPLY_TECHS,
    cap_binding,
    read_csv_rows,
    read_emissions,
    read_production,
    read_status,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
SUPPLY_DIR = Path("data/derived/national/clews")
OUTPUT_DIR = SUPPLY_DIR / "osecan" / "national_base_119m"
HARVEST_BASIS = Path("data/derived/national/harvest_basis/harvest_basis_template.csv")

LOG_VOLUME_M3_PER_YEAR = 118_977_339.0
LOG_SHARE = national.LOG_SHARE
TILE_FACTOR_ODT_PER_M3 = national.TILE_FACTOR_ODT_PER_M3
LHV_GJ_PER_ODT = national.LHV_GJ_PER_ODT
RECOVERY = national.RECOVERY
COST_BASE_USD_PER_GJ = national.COST_BASE_USD_PER_GJ
COST_MULTIPLIER = national.COST_MULTIPLIER
BC_ZONAL_TOTAL_PJ = national.BC_ZONAL_TOTAL_PJ
CARBON_PRICE_PRE_2026 = national.CARBON_PRICE_PRE_2026
CARBON_PRICE_BASELINE_FROM_2026 = national.CARBON_PRICE_BASELINE_FROM_2026
CARBON_PRICE_STRESS_FROM_2026 = national.CARBON_PRICE_STRESS_FROM_2026
GENERIC_BIO_BASE_COST = national.GENERIC_BIO_BASE_COST
MODEL_COMMIT = national.MODEL_COMMIT
MODEL_REPOSITORY = national.MODEL_REPOSITORY
MODEL_PACKAGE = national.MODEL_PACKAGE
MODEL_SOLVER = national.MODEL_SOLVER
MODEL_SCENARIO = national.MODEL_SCENARIO
STATUS = national.STATUS
RunSpec = national.RunSpec
SupplyBasis = national.SupplyBasis
YEARS = tuple(range(HORIZON_START, HORIZON_END + 1))
KEY_YEARS = national.KEY_YEARS

RUN_SPECS = (
    RunSpec(
        name="baseline_119m",
        label="Current Measures baseline at 119M log/yr",
        description=(
            "National 118,977,339 m3 log/yr observed 2024 harvest basis, base "
            "recovery/cost; BC zonal SUPBCWB1/B2 unchanged; CO2 95 $/t to 2025 "
            "and 115 $/t from 2026; normal hydro."
        ),
        carbon_price_from_2026=CARBON_PRICE_BASELINE_FROM_2026,
        hydro_capped=False,
        supply_case="base",
        generic_bio_cost_usd_per_gj=GENERIC_BIO_BASE_COST,
    ),
    RunSpec(
        name="stress_119m",
        label="carbon $200/t + hydro capped at 2021 activity",
        description=(
            "The baseline 119M cap/cost with CO2 at 200 $/t from 2026 and every "
            "PWRHYD<z> capped at its 2021 base-run activity through "
            "TotalTechnologyAnnualActivityUpperLimit (an upper bound, not "
            "fixed dispatch)."
        ),
        carbon_price_from_2026=CARBON_PRICE_STRESS_FROM_2026,
        hydro_capped=True,
        supply_case="base",
        generic_bio_cost_usd_per_gj=GENERIC_BIO_BASE_COST,
    ),
)
PWR_BIO_TECHS = tuple(f"PWRBIO{zone}" for zone in (*BC_ZONES, *OTHER_ZONES))


def _write_csv(path: Path, fields: Sequence[str], rows: Sequence[Mapping[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields))
        writer.writeheader()
        writer.writerows(rows)


def _read_series(path: Path, technology: str) -> dict[int, float]:
    _, rows = read_csv_rows(path)
    series = {
        int(row["YEAR"]): float(row["VALUE"])
        for row in rows
        if row["TECHNOLOGY"] == technology
    }
    if not series:
        raise ValueError(f"{technology} has no rows in {path}")
    return dict(sorted(series.items()))


def national_cap_series(
    national_total_pj: float,
    zonal_caps: Mapping[str, Mapping[int, float]],
) -> dict[int, float]:
    """Return the rest-of-Canada cap as national minus the two BC zones."""
    if national_total_pj <= 0:
        raise ValueError(f"national total must be positive, got {national_total_pj}")
    capped_years = set.intersection(*(set(caps) for caps in zonal_caps.values()))
    if capped_years != set(YEARS):
        missing = sorted(set(YEARS) - capped_years)
        raise ValueError(f"zonal caps do not cover the horizon; missing {missing[:3]}")
    series = {
        year: national_total_pj - sum(float(caps[year]) for caps in zonal_caps.values())
        for year in YEARS
    }
    negative = sorted(year for year, value in series.items() if value < 0)
    if negative:
        raise ValueError(f"zonal caps exceed the national total in {negative}")
    return series


def write_parameter_files(root: Path = REPO_ROOT) -> dict[str, object]:
    """Write the 119M national and recovery-sensitivity parameter CSVs."""
    national._verify_fixed_supply_outputs(root)
    supply_dir = root / SUPPLY_DIR
    output_dir = root / OUTPUT_DIR

    zonal_caps = {
        technology: _read_series(
            supply_dir / f"{technology}_activity_limits_base.csv", technology
        )
        for technology in SUPPLY_TECHS
    }
    zonal_total = {
        year: sum(caps[year] for caps in zonal_caps.values()) for year in YEARS
    }
    steady_state = zonal_total[2028]
    if abs(steady_state - BC_ZONAL_TOTAL_PJ) > 1e-6:
        raise ValueError(
            f"BC zonal steady-state total drifted: {steady_state} != {BC_ZONAL_TOTAL_PJ}"
        )

    basis = national.compute_supply_basis(LOG_VOLUME_M3_PER_YEAR)
    pj = basis.pj_by_recovery
    cases = {
        case: {
            "national_total_pj": pj[case],
            "rest_of_canada_pj": pj[case] - steady_state,
            "cost_usd_per_gj": COST_BASE_USD_PER_GJ * COST_MULTIPLIER[case],
        }
        for case in RECOVERY
    }

    for case in RECOVERY:
        cap = national_cap_series(pj[case], zonal_caps)
        suffix = "" if case == "base" else f"_{case}"
        _write_csv(
            output_dir / f"SUPBCW_activity_limits_national_base_119m{suffix}.csv",
            ["REGION", "TECHNOLOGY", "YEAR", "VALUE"],
            [
                {
                    "REGION": "CAN",
                    "TECHNOLOGY": "SUPBCW",
                    "YEAR": year,
                    "VALUE": repr(cap[year]),
                }
                for year in YEARS
            ],
        )
        _write_csv(
            output_dir / f"SUPBCW_variable_cost_{case}.csv",
            ["REGION", "TECHNOLOGY", "YEAR", "VALUE"],
            [
                {
                    "REGION": "CAN",
                    "TECHNOLOGY": "SUPBCW",
                    "YEAR": year,
                    "VALUE": repr(cases[case]["cost_usd_per_gj"]),
                }
                for year in YEARS
            ],
        )

    payload = {
        "harvest_basis": {
            "observed_year": 2024,
            "observed_national_harvest_m3": LOG_VOLUME_M3_PER_YEAR,
            "source": (
                "NRCan Statistical Profiles FSD management volume, 2024 Canada "
                "total; cross-checked against 12 provincial values"
            ),
        },
        "log_volume_m3_per_year": basis.log_volume_m3_per_year,
        "log_share": basis.log_share,
        "total_volume_m3_per_year": basis.total_volume_m3_per_year,
        "tile_factor_odt_per_m3": basis.tile_factor_odt_per_m3,
        "gross_odt_per_year": basis.gross_odt_per_year,
        "recovery_fractions": RECOVERY,
        "national_total_pj": pj,
        "bc_zonal_total_pj": steady_state,
        "bc_zonal_total_by_year": {str(y): round(v, 9) for y, v in zonal_total.items()},
        "rest_of_canada_pj_steady_state": {
            case: cases[case]["rest_of_canada_pj"] for case in RECOVERY
        },
        "costs_usd_per_gj": {
            case: cases[case]["cost_usd_per_gj"] for case in RECOVERY
        },
        "national_cap_pj_by_year": {
            case: {
                str(y): round(value, 9)
                for y, value in national_cap_series(pj[case], zonal_caps).items()
            }
            for case in RECOVERY
        },
    }
    (output_dir / "supply_basis.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return payload


def _upsert_technology(
    path: Path,
    technology: str,
    rows: Sequence[Mapping[str, object]],
) -> None:
    fields, existing = read_csv_rows(path)
    kept = [row for row in existing if row["TECHNOLOGY"] != technology]
    _write_csv(path, fields, kept + list(rows))


def build_scenario_params(
    model_root: Path,
    artifact_dir: Path,
    spec: RunSpec,
) -> Path:
    """Build one scenario parameter directory in the disposable model copy."""
    if spec.supply_case not in RECOVERY:
        raise ValueError(f"unknown supply case {spec.supply_case!r}")

    target = model_root / "results" / spec.name / "params"
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    for source in (model_root / "data/params").glob("*.csv"):
        shutil.copyfile(source, target / source.name)

    suffix = "" if spec.supply_case == "base" else f"_{spec.supply_case}"
    national_cap = _read_series(
        artifact_dir / f"SUPBCW_activity_limits_national_base_119m{suffix}.csv",
        "SUPBCW",
    )
    national_cost = _read_series(
        artifact_dir / f"SUPBCW_variable_cost_{spec.supply_case}.csv", "SUPBCW"
    )
    _upsert_technology(
        target / "TotalTechnologyAnnualActivityUpperLimit.csv",
        "SUPBCW",
        [
            {"REGION": "CAN", "TECHNOLOGY": "SUPBCW", "YEAR": y, "VALUE": repr(v)}
            for y, v in national_cap.items()
        ],
    )
    _upsert_technology(
        target / "VariableCost.csv",
        "SUPBCW",
        [
            {
                "REGION": "CAN",
                "TECHNOLOGY": "SUPBCW",
                "MODE_OF_OPERATION": "1",
                "YEAR": y,
                "VALUE": repr(v),
            }
            for y, v in national_cost.items()
        ],
    )

    supply_dir = REPO_ROOT / SUPPLY_DIR
    for technology in SUPPLY_TECHS:
        caps = _read_series(
            supply_dir / f"{technology}_activity_limits_base.csv", technology
        )
        costs = _read_series(
            supply_dir / f"{technology}_variable_cost_base.csv", technology
        )
        _upsert_technology(
            target / "TotalTechnologyAnnualActivityUpperLimit.csv",
            technology,
            [
                {"REGION": "CAN", "TECHNOLOGY": technology, "YEAR": y, "VALUE": repr(v)}
                for y, v in caps.items()
            ],
        )
        _upsert_technology(
            target / "VariableCost.csv",
            technology,
            [
                {
                    "REGION": "CAN",
                    "TECHNOLOGY": technology,
                    "MODE_OF_OPERATION": "1",
                    "YEAR": y,
                    "VALUE": repr(v),
                }
                for y, v in costs.items()
            ],
        )

    national._set_carbon_price(
        target / "EmissionsPenalty.csv", spec.carbon_price_from_2026
    )
    if spec.hydro_capped:
        hydro = national._hydro_2021(model_root)
        cap_path = target / "TotalTechnologyAnnualActivityUpperLimit.csv"
        for technology, value in hydro.items():
            _upsert_technology(
                cap_path,
                technology,
                [
                    {"REGION": "CAN", "TECHNOLOGY": technology, "YEAR": y, "VALUE": repr(value)}
                    for y in YEARS
                ],
            )
    return target


def solve_runs(model_root: Path, specs: Sequence[RunSpec]) -> None:
    """Assemble, convert, and solve each run in the disposable copy."""
    executable = model_root / ".venv/bin/python"
    if not executable.exists():
        executable = Path(sys.executable)
    otoole = model_root / ".venv/bin/otoole"
    if not otoole.exists():
        raise FileNotFoundError(f"missing otoole executable: {otoole}")
    snippet = (
        "import json,sys\n"
        "from pathlib import Path\n"
        "from osecan.pipeline import solve\n"
        "name=sys.argv[1]\n"
        "run=Path('results')/name\n"
        "res=solve.solve(str(run), str(run/'params'))\n"
        "(run/'status.json').write_text(json.dumps(res, indent=2)+'\\n')\n"
        "print(name, res)\n"
    )
    for spec in specs:
        run_dir = model_root / "results" / spec.name
        input_dir = run_dir / "input"
        subprocess.run(
            [
                str(executable),
                "-m",
                "osecan.pipeline.assemble",
                "--sets",
                "SETs",
                "--params",
                str(run_dir / "params"),
                "--config",
                "config/otoole.yaml",
                "--out",
                str(input_dir),
            ],
            cwd=model_root,
            check=True,
        )
        subprocess.run(
            [
                str(otoole),
                "convert",
                "csv",
                "datafile",
                str(input_dir),
                str(run_dir / "osecan.txt"),
                "config/otoole.yaml",
            ],
            cwd=model_root,
            check=True,
        )
        completed = subprocess.run(
            [str(executable), "-c", snippet, spec.name],
            cwd=model_root,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(f"solver failed for {spec.name}")


def _parameter_set_sha256(path: Path) -> str:
    files = sorted(path.glob("*.csv"))
    if not files:
        raise ValueError(f"{path}: no parameter CSVs to hash")
    digest = hashlib.sha256()
    for file in files:
        digest.update(file.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(file.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def collect_harmonize_evidence(
    model_root: Path,
    runs_root: Path,
    *,
    verified_utc: str | None = None,
) -> dict[str, object]:
    """Run ``osecan harmonize`` for the base, baseline, and stress parameter sets."""
    sets_dir = model_root / "SETs"
    executable = model_root / ".venv/bin/python"
    if not executable.exists():
        executable = Path(sys.executable)
    parameter_sets = [
        ("base", model_root / "data/params"),
        *((spec.name, runs_root / spec.name / "params") for spec in RUN_SPECS),
    ]
    checks: list[dict[str, object]] = []
    for name, param_dir in parameter_sets:
        if not param_dir.is_dir():
            raise FileNotFoundError(f"missing parameter set {name}: {param_dir}")
        completed = subprocess.run(
            [
                str(executable),
                "-m",
                "osecan.cli",
                "harmonize",
                "--sets",
                str(sets_dir),
                "--params",
                str(param_dir),
            ],
            cwd=model_root,
            capture_output=True,
            text=True,
            check=False,
        )
        stdout = completed.stdout.strip()
        stderr = completed.stderr.strip()
        message = (
            stdout.splitlines()[-1]
            if stdout
            else (stderr.splitlines()[-1] if stderr else "")
        )
        checks.append(
            {
                "parameter_set": name,
                "params": str(param_dir),
                "exit_code": completed.returncode,
                "parameter_set_sha256": _parameter_set_sha256(param_dir),
                "message": message,
            }
        )
    all_passed = len(checks) == len(parameter_sets) and all(
        check["exit_code"] == 0 for check in checks
    )
    return {
        "tool": "python -m osecan.cli harmonize",
        "sets": str(sets_dir),
        "all_passed": all_passed,
        "checks": checks,
        "verified_utc": verified_utc or datetime.now(UTC).isoformat(timespec="seconds"),
        "public_repo_modified": False,
        "note": (
            "Recorded in the disposable model copy; the public "
            "github.com/deltae/C2070 clone was not modified."
        ),
    }


def harmonize_evidence_is_complete(evidence: Mapping[str, object] | None) -> bool:
    if not evidence or not evidence.get("all_passed"):
        return False
    checks = evidence.get("checks")
    if not isinstance(checks, Sequence) or isinstance(checks, (str, bytes)):
        return False
    expected_names = {"base", *(spec.name for spec in RUN_SPECS)}
    observed_names = {
        str(check.get("parameter_set"))
        for check in checks
        if isinstance(check, Mapping)
    }
    if len(checks) != len(expected_names) or observed_names != expected_names:
        return False
    for check in checks:
        if not isinstance(check, Mapping) or check.get("exit_code") != 0:
            return False
        digest = check.get("parameter_set_sha256")
        if not isinstance(digest, str) or len(digest) != 64:
            return False
        try:
            int(digest, 16)
        except ValueError:
            return False
    return True


def harmonize_report_line(evidence: Mapping[str, object] | None) -> str:
    suffix = (
        "  `PWRBCWB1`/`PWRBCWB2` consume `BCWB1`/`BCWB2`; the other eleven "
        "`PWRBCW<z>` plants consume the national `BCW` pool."
    )
    if not harmonize_evidence_is_complete(evidence):
        checks = evidence.get("checks", []) if isinstance(evidence, Mapping) else []
        observed = ", ".join(
            f"{check.get('parameter_set', '?')} exit {check.get('exit_code', '?')}"
            for check in checks
            if isinstance(check, Mapping)
        ) or "no recorded checks"
        return (
            "`osecan harmonize` success is **not asserted**: evidence is "
            f"missing or failed ({observed}).{suffix}"
        )
    details = ", ".join(
        f"{check['parameter_set']} exit 0 (SHA-256 {str(check['parameter_set_sha256'])[:12]}…)"
        for check in evidence["checks"]
    )
    return (
        f"`osecan harmonize` evidence recorded: {details}.  "
        "**HARMONIZED — all params reference valid SET codes.**"
        f"{suffix}"
    )


def _series_summary(series: Mapping[int, float]) -> dict[str, object]:
    return {
        "by_year": {str(y): round(float(v), 6) for y, v in sorted(series.items())},
        "total": round(sum(float(v) for v in series.values()), 6),
        **{str(y): round(float(series.get(y, 0.0)), 6) for y in KEY_YEARS},
    }


def _other_zone_bcw(production: Mapping[str, Mapping[int, float]]) -> dict[str, object]:
    by_zone: dict[str, float] = {}
    by_zone_2050: dict[str, float] = {}
    by_year: dict[int, float] = defaultdict(float)
    for technology, series in production.items():
        if not technology.startswith("PWRBCW") or technology in (
            "PWRBCWB1",
            "PWRBCWB2",
        ):
            continue
        zone = technology[-2:]
        by_zone[zone] = round(sum(float(v) for v in series.values()), 6)
        by_zone_2050[zone] = round(float(series.get(2050, 0.0)), 6)
        for year, value in series.items():
            by_year[year] += float(value)
    return {
        "by_year": {str(y): round(v, 6) for y, v in sorted(by_year.items())},
        "total_pj": round(sum(by_zone.values()), 6),
        "by_zone_total_pj": dict(sorted(by_zone.items())),
        "by_zone_2050_pj": dict(sorted(by_zone_2050.items())),
        **_series_summary(by_year),
    }


def sum_pwr_bio(production: Mapping[str, Mapping[int, float]]) -> float:
    return round(
        sum(
            float(value)
            for technology in PWR_BIO_TECHS
            for value in production.get(technology, {}).values()
        ),
        6,
    )


def summarize_run(
    run_dir: Path,
    caps: Mapping[str, Mapping[int, float]],
    spec: RunSpec,
) -> dict[str, object]:
    production = read_production(run_dir)
    emissions = read_emissions(run_dir)
    status = read_status(run_dir)
    summary: dict[str, object] = {
        "run": spec.name,
        "label": spec.label,
        "description": spec.description,
        "supply_case": spec.supply_case,
        "carbon_price_from_2026_usd_per_t": spec.carbon_price_from_2026,
        "hydro_capped_at_2021": spec.hydro_capped,
        "pwr_hyd_b2_2021_pj": round(
            float(production.get("PWRHYDB2", {}).get(2021, 0.0)), 6
        ),
        "solver_status": str(status["status"]),
        "objective_musd": round(float(status["objective"]), 2),
        "lp_rows": int(status["rows"]),
        "lp_cols": int(status["cols"]),
        "supbcw_pj": _series_summary(production.get("SUPBCW", {})),
        "supbcwb1_pj": _series_summary(production.get("SUPBCWB1", {})),
        "supbcwb2_pj": _series_summary(production.get("SUPBCWB2", {})),
        "pwrbcwb1_pj": _series_summary(production.get("PWRBCWB1", {})),
        "pwrbcwb2_pj": _series_summary(production.get("PWRBCWB2", {})),
        "pwrbcw_other_zone_pj": _other_zone_bcw(production),
        "pwr_bio_total_pj": sum_pwr_bio(production),
        "emissions_mt": {
            "by_year": {str(y): round(v, 6) for y, v in emissions.items()},
            "total": round(sum(emissions.values()), 6),
            **_series_summary(emissions),
        },
    }
    summary["cap_binding"] = {
        technology: cap_binding(
            production.get(technology, {}),
            caps[technology],
            technology,
            violation_tolerance=1e-3,
        )
        for technology in ("SUPBCW", "SUPBCWB1", "SUPBCWB2")
    }
    return summary


def build_dispatch_rows(runs_root: Path) -> list[dict[str, object]]:
    zones = (*BC_ZONES, *OTHER_ZONES)
    technologies = [
        "SUPBCW",
        "SUPBCWB1",
        "SUPBCWB2",
        *(f"PWRBCW{zone}" for zone in zones),
        *(f"PWRBIO{zone}" for zone in zones),
    ]
    rows: list[dict[str, object]] = []
    for spec in RUN_SPECS:
        production = read_production(runs_root / spec.name)
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
                        "run": spec.name,
                        "technology": technology,
                        "category": category,
                        "zone": zone,
                        "year": year,
                        "value_pj": round(float(series.get(year, 0.0)), 6),
                    }
                )
    return rows


def build_run_summaries(
    runs_root: Path,
    artifact_dir: Path,
    root: Path = REPO_ROOT,
    harmonize_evidence: Mapping[str, object] | None = None,
) -> dict[str, object]:
    caps = {
        "SUPBCW": _read_series(
            artifact_dir / "SUPBCW_activity_limits_national_base_119m.csv", "SUPBCW"
        )
    }
    for technology in SUPPLY_TECHS:
        caps[technology] = _read_series(
            root / SUPPLY_DIR / f"{technology}_activity_limits_base.csv", technology
        )
    runs = {
        spec.name: summarize_run(runs_root / spec.name, caps, spec)
        for spec in RUN_SPECS
    }
    baseline = runs["baseline_119m"]
    stress = runs["stress_119m"]
    comparison = {
        "stress": {
            "objective_delta_musd": round(
                float(stress["objective_musd"]) - float(baseline["objective_musd"]), 2
            ),
            "emissions_delta_mt": round(
                float(stress["emissions_mt"]["total"])
                - float(baseline["emissions_mt"]["total"]),
                6,
            ),
            "supbcw_delta_pj": round(
                float(stress["supbcw_pj"]["total"])
                - float(baseline["supbcw_pj"]["total"]),
                6,
            ),
            "bc_zonal_delta_pj": round(
                (
                    float(stress["supbcwb1_pj"]["total"])
                    + float(stress["supbcwb2_pj"]["total"])
                )
                - (
                    float(baseline["supbcwb1_pj"]["total"])
                    + float(baseline["supbcwb2_pj"]["total"])
                ),
                6,
            ),
        }
    }
    payload: dict[str, object] = {
        "schema_version": "1.0",
        "generated_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "status": STATUS,
        "scenario": MODEL_SCENARIO,
        "model": {
            "repository": MODEL_REPOSITORY,
            "commit": MODEL_COMMIT,
            "package": MODEL_PACKAGE,
            "solver": MODEL_SOLVER,
            "horizon": f"{HORIZON_START}-{HORIZON_END}",
            "region": "CAN",
        },
        "forcing": {
            "carbon_price_usd_per_t": {
                "2021-2025": CARBON_PRICE_PRE_2026,
                "baseline_2026-2070": CARBON_PRICE_BASELINE_FROM_2026,
                "stress_2026-2070": CARBON_PRICE_STRESS_FROM_2026,
            },
            "hydro": {
                "baseline": "normal (no cap)",
                "stress": (
                    "every PWRHYD<z> capped at 2021 base activity through "
                    "TotalTechnologyAnnualActivityUpperLimit (upper bound, not "
                    "fixed dispatch)"
                ),
            },
        },
        "caps": {
            technology: {str(y): round(v, 9) for y, v in series.items()}
            for technology, series in caps.items()
        },
        "runs": runs,
        "comparison": comparison,
        "harmonize": (
            dict(harmonize_evidence)
            if harmonize_evidence is not None
            else {"all_passed": False, "checks": [], "note": "No harmonize evidence."}
        ),
    }
    dispatch_totals: dict[str, float] = defaultdict(float)
    for row in build_dispatch_rows(runs_root):
        if row.get("technology") in PWR_BIO_TECHS:
            dispatch_totals[str(row["run"])] += float(row["value_pj"])
    for name, run in runs.items():
        observed = round(dispatch_totals.get(name, 0.0), 6)
        if abs(observed - float(run["pwr_bio_total_pj"])) > 1e-6:
            raise ValueError(
                f"{name}: PWRBIO dispatch total {observed} does not reconcile "
                f"to summary total {run['pwr_bio_total_pj']}"
            )
    return payload


def _load_comparison_cases(root: Path) -> list[dict[str, object]]:
    """Read the documented 100M, 150M, and 200M stress results."""
    cases = [
        {
            "case": "100M stress",
            "log_m3": 100_000_000,
            "objective_musd": 1_371_219.04,
            "emissions_mt": 39.685119,
            "bcw_supply_pj": 5_972.420,
            "supbcw_bind": "2024-2070 (47 yrs)",
        },
        {
            "case": "150M stress",
            "log_m3": 150_000_000,
            "objective_musd": 1_370_983.13,
            "emissions_mt": 38.544682,
            "bcw_supply_pj": 8_819.496,
            "supbcw_bind": "2028-2070 (43 yrs)",
        },
        {
            "case": "200M stress",
            "log_m3": 200_000_000,
            "objective_musd": 1_370_767.51,
            "emissions_mt": 38.027561,
            "bcw_supply_pj": 11_563.727,
            "supbcw_bind": "2029-2070 (42 yrs)",
        },
    ]
    for case, directory in (
        (100_000_000, "harvest_sensitivity_100_200"),
        (150_000_000, "national_150m"),
    ):
        path = root / SUPPLY_DIR / "osecan" / directory / "run_summaries.json"
        if not path.exists():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        name = "100m" if case == 100_000_000 else "stress"
        run = payload["runs"][name]
        for row in cases:
            if row["log_m3"] == case:
                row["objective_musd"] = run["objective_musd"]
                row["emissions_mt"] = run["emissions_mt"]["total"]
                row["bcw_supply_pj"] = round(
                    float(run["supbcw_pj"]["total"])
                    + float(run["supbcwb1_pj"]["total"])
                    + float(run["supbcwb2_pj"]["total"]),
                    3,
                )
    return cases


def _fmt(value: object, digits: int = 2) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    return f"{float(value):,.{digits}f}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def write_report(
    artifact_dir: Path,
    supply: Mapping[str, object],
    summaries: Mapping[str, object],
    root: Path = REPO_ROOT,
) -> Path:
    runs = summaries["runs"]
    baseline = runs["baseline_119m"]
    stress = runs["stress_119m"]
    comparison = summaries["comparison"]["stress"]
    caps = summaries["caps"]
    cases = _load_comparison_cases(root)

    def series_row(run: Mapping[str, object], key: str, label: str, total_key: str = "total") -> str:
        series = run[key]
        return (
            f"| {label} | {_fmt(series['2021'], 3)} | {_fmt(series['2025'], 3)} | "
            f"{_fmt(series['2028'], 3)} | {_fmt(series['2030'], 3)} | "
            f"{_fmt(series['2050'], 3)} | {_fmt(series['2070'], 3)} | "
            f"{_fmt(series[total_key], 3)} |"
        )

    lines = [
        "# National OSeCAN base at the 2024 observed harvest: 118,977,339 m3 log/yr",
        "",
        (
            "Status: **FRESH-side provisional** — not an official C2070 result. "
            f"The experiment uses a disposable copy of public `deltae/C2070` "
            f"commit `{MODEL_COMMIT[:12]}`; the public clone was not modified."
        ),
        "",
        "## Harvest basis",
        "",
        (
            "The base is the FSD 2024 national observed harvest of "
            "**118,977,339 m3/yr**, not the former 150M scenario assumption. "
            "FSD 2021-2024 provincial values fill the harvest template; 2024 is "
            "held flat from 2025 as a documented accounting path.  The 150M case "
            "is an upper bound, 26.1% above the observed 2024 level."
        ),
        "",
        "| Case | 2024 log basis (m3/yr) | Status |",
        "|---|---:|---|",
        f"| Observed 2024 | {_fmt(LOG_VOLUME_M3_PER_YEAR, 0)} | FSD observation, used here |",
        "| 100M | 100,000,000 | lower scenario bound |",
        "| 150M | 150,000,000 | upper scenario bound, not central |",
        "| 200M | 200,000,000 | stress bound |",
        "",
        "## Supply math (119M log -> total m3 -> ODT -> PJ; BC/rest split)",
        "",
        "| Step | Value |",
        "|---|---:|",
        f"| Annual log volume (m3/yr) | {_fmt(supply['log_volume_m3_per_year'], 0)} |",
        f"| Log share of total volume | {supply['log_share']:.9f} |",
        f"| Total volume (m3/yr) | {_fmt(supply['total_volume_m3_per_year'], 2)} |",
        f"| Tile-mix factor (ODT/m3 total) | {supply['tile_factor_odt_per_m3']:.12f} |",
        f"| Gross residue (ODT/yr) | {_fmt(supply['gross_odt_per_year'], 2)} |",
        "",
        "| Case | Recovery | National PJ/yr | BC zonal PJ/yr | Rest-of-Canada PJ/yr | Cost ($/GJ) |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for case in ("base", "low", "high"):
        lines.append(
            f"| {case} | {supply['recovery_fractions'][case]:.2f} | "
            f"{_fmt(supply['national_total_pj'][case], 3)} | "
            f"{_fmt(supply['bc_zonal_total_pj'], 3)} | "
            f"{_fmt(supply['rest_of_canada_pj_steady_state'][case], 3)} | "
            f"{_fmt(supply['costs_usd_per_gj'][case], 6)} |"
        )
    lines += [
        "",
        (
            "The BC zonal total is the 2028+ sum of the unchanged `SUPBCWB1` "
            f"({_fmt(caps['SUPBCWB1']['2028'], 6)} PJ) and `SUPBCWB2` "
            f"({_fmt(caps['SUPBCWB2']['2028'], 6)} PJ) caps.  The `SUPBCW` cap is "
            "recomputed year by year as national total minus the two zonal caps, "
            "so `SUPBCW + SUPBCWB1 + SUPBCWB2` equals the national total in every "
            "year without double counting BC."
        ),
        "",
        "## Parameter files / harmonize",
        "",
        "| File | Content |",
        "|---|---|",
        "| `SUPBCW_activity_limits_national_base_119m.csv` | base rest-of-Canada cap, 2021-2070 |",
        "| `SUPBCW_activity_limits_national_base_119m_low.csv` | low-recovery rest-of-Canada cap |",
        "| `SUPBCW_activity_limits_national_base_119m_high.csv` | high-recovery rest-of-Canada cap |",
        "| `SUPBCW_variable_cost_{base,low,high}.csv` | 2.456978, 3.194071, 1.965582 $/GJ |",
        "| `supply_basis.json` | full conversion and cap-split record |",
        "",
        harmonize_report_line(summaries.get("harmonize")),
        "",
        "## Baseline (objective, dispatch, binding, emissions)",
        "",
        (
            "`Current Measures`, 2021-2070, CO2 95 $/t to 2025 and 115 $/t "
            f"thereafter, normal hydro, national base cap "
            f"{_fmt(caps['SUPBCW']['2028'], 3)} PJ (2028+)."
        ),
        "",
        "| Metric | Baseline | Stress $200/hydro-capped | Δ stress |",
        "|---|---:|---:|---:|",
        f"| Solver status | {baseline['solver_status']} | {stress['solver_status']} | — |",
        (
            f"| Objective (M$) | {_fmt(baseline['objective_musd'], 2)} | "
            f"{_fmt(stress['objective_musd'], 2)} | "
            f"{_fmt(comparison['objective_delta_musd'], 2)} |"
        ),
        (
            f"| Emissions total (Mt) | {_fmt(baseline['emissions_mt']['total'], 3)} | "
            f"{_fmt(stress['emissions_mt']['total'], 3)} | "
            f"{_fmt(comparison['emissions_delta_mt'], 3)} |"
        ),
        (
            f"| `PWRHYDB2` 2021 dispatch (PJ) | "
            f"{_fmt(baseline['pwr_hyd_b2_2021_pj'], 6)} | "
            f"{_fmt(stress['pwr_hyd_b2_2021_pj'], 6)} | "
            f"{_fmt(float(stress['pwr_hyd_b2_2021_pj']) - float(baseline['pwr_hyd_b2_2021_pj']), 6)} |"
        ),
        "",
        "### SUP activity by year (PJ)",
        "",
        "| Run / technology | 2021 | 2025 | 2028 | 2030 | 2050 | 2070 | Total |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
        series_row(baseline, "supbcw_pj", "baseline / `SUPBCW`"),
        series_row(baseline, "supbcwb1_pj", "baseline / `SUPBCWB1`"),
        series_row(baseline, "supbcwb2_pj", "baseline / `SUPBCWB2`"),
        series_row(stress, "supbcw_pj", "stress / `SUPBCW`"),
        series_row(stress, "supbcwb1_pj", "stress / `SUPBCWB1`"),
        series_row(stress, "supbcwb2_pj", "stress / `SUPBCWB2`"),
        "",
        "### PWR dispatch by year (PJ electricity)",
        "",
        "| Run / technology | 2021 | 2025 | 2028 | 2030 | 2050 | 2070 | Total |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
        series_row(baseline, "pwrbcwb1_pj", "baseline / `PWRBCWB1`"),
        series_row(baseline, "pwrbcwb2_pj", "baseline / `PWRBCWB2`"),
        series_row(baseline, "pwrbcw_other_zone_pj", "baseline / `PWRBCW` other zones", "total_pj"),
        series_row(stress, "pwrbcwb1_pj", "stress / `PWRBCWB1`"),
        series_row(stress, "pwrbcwb2_pj", "stress / `PWRBCWB2`"),
        series_row(stress, "pwrbcw_other_zone_pj", "stress / `PWRBCW` other zones", "total_pj"),
        "",
        "### Cap binding",
        "",
        "| Run | Technology | Cap binds | Binding years | Count | Max utilisation |",
        "|---|---|---|---|---:|---:|",
    ]
    for run_name in ("baseline_119m", "stress_119m"):
        for technology in ("SUPBCW", "SUPBCWB1", "SUPBCWB2"):
            binding = runs[run_name]["cap_binding"][technology]
            lines.append(
                f"| {run_name} | `{technology}` | {_fmt(binding['binding'])} | "
                f"{binding['binding_years'] or '—'} | {binding['binding_year_count']} | "
                f"{_fmt(binding['max_utilisation'], 3)} |"
            )
    lines += [
        "",
        "### PWRBCW dispatch outside B1/B2 (PJ, total 2021-2070)",
        "",
        "| Zone | Baseline | Stress |",
        "|---|---:|---:|",
    ]
    zones = sorted(
        set(baseline["pwrbcw_other_zone_pj"]["by_zone_total_pj"])
        | set(stress["pwrbcw_other_zone_pj"]["by_zone_total_pj"])
        | set(OTHER_ZONES)
    )
    for zone in zones:
        lines.append(
            f"| {zone} | "
            f"{_fmt(baseline['pwrbcw_other_zone_pj']['by_zone_total_pj'].get(zone, 0.0), 3)} | "
            f"{_fmt(stress['pwrbcw_other_zone_pj']['by_zone_total_pj'].get(zone, 0.0), 3)} |"
        )
    lines += [
        "",
        "### Emissions",
        "",
        "| Run | 2021 | 2025 | 2030 | 2050 | 2070 | Total (Mt) |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for spec in RUN_SPECS:
        series = runs[spec.name]["emissions_mt"]
        lines.append(
            f"| {spec.name} | {_fmt(series['2021'], 3)} | {_fmt(series['2025'], 3)} | "
            f"{_fmt(series['2030'], 3)} | {_fmt(series['2050'], 3)} | "
            f"{_fmt(series['2070'], 3)} | {_fmt(series['total'], 3)} |"
        )
    lines += [
        "",
        "## Stress $200/hydro-capped",
        "",
        (
            "The stress run raises the 2026+ carbon price from 115 to 200 $/t.  "
            "Its only hydro constraint is a `TotalTechnologyAnnualActivityUpperLimit` "
            "at each `PWRHYD<z>`'s 2021 base activity; it is a **cap at the 2021 "
            "activity, not fixed dispatch**.  The run keeps the same national and "
            "zonal caps and costs as the baseline."
        ),
        "",
        (
            f"Objective {_fmt(stress['objective_musd'], 2)} M$ vs baseline "
            f"{_fmt(baseline['objective_musd'], 2)} M$ "
            f"({_fmt(comparison['objective_delta_musd'], 2)} M$); cumulative CO2 "
            f"{_fmt(stress['emissions_mt']['total'], 6)} Mt vs "
            f"{_fmt(baseline['emissions_mt']['total'], 6)} Mt "
            f"({_fmt(comparison['emissions_delta_mt'], 6)} Mt vs baseline).  "
            f"In the stress run `SUPBCW` binds in "
            f"{stress['cap_binding']['SUPBCW']['binding_years']} "
            f"({stress['cap_binding']['SUPBCW']['binding_year_count']} years), "
            f"`SUPBCWB1` in {stress['cap_binding']['SUPBCWB1']['binding_years']} "
            f"and `SUPBCWB2` in {stress['cap_binding']['SUPBCWB2']['binding_years']}."
        ),
        "",
        "## Comparison to the 100M / 150M / 200M stress cases",
        "",
        "| Case | Log basis (m3/yr) | Objective (M$) | Emissions (Mt) | BCW supply (PJ) | `SUPBCW` binds |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for case in cases:
        lines.append(
            f"| {case['case']} | {_fmt(case['log_m3'], 0)} | "
            f"{_fmt(case['objective_musd'], 2)} | {_fmt(case['emissions_mt'], 3)} | "
            f"{_fmt(case['bcw_supply_pj'], 3)} | {case['supbcw_bind']} |"
        )
    lines.append(
        f"| **119M base (this run)** | {_fmt(LOG_VOLUME_M3_PER_YEAR, 0)} | "
        f"{_fmt(stress['objective_musd'], 2)} | {_fmt(stress['emissions_mt']['total'], 3)} | "
        f"{_fmt(float(stress['supbcw_pj']['total']) + float(stress['supbcwb1_pj']['total']) + float(stress['supbcwb2_pj']['total']), 3)} | "
        f"{stress['cap_binding']['SUPBCW']['binding_years']} |"
    )
    lines += [
        "",
        (
            "The 119M case is the observed 2024 harvest basis.  The 150M case is "
            "26.1% above it and remains an upper bound; it is retained for "
            "comparison, not promoted to the central case.  The baseline at "
            "115 $/t is zero-biomass, so its objective and emissions are a "
            "controlled no-biomass reference; the stress run is the informative "
            "supply case."
        ),
        "",
        "## Tests/commits/publish/status",
        "",
        (
            "The cap split is checked against `SUPBCW + SUPBCWB1 + SUPBCWB2 = "
            "national total` in every year, and the conversion is checked against "
            "the fixed tile-supply outputs.  `osecan harmonize` evidence is "
            "recorded above from the disposable model copy."
        ),
        "",
        "## Caveats (observed hold, no forecast, managed landbase pending)",
        "",
        "- The 2024 observed harvest is held flat from 2025; this is not a harvest forecast.",
        "- No provincial AAC trajectory, managed land base (THLB), or operability filter is applied.",
        "- RSYC-only v0: no local corrections; benchmark correction factors withheld.",
        "- B25 is flagged over capacity in the tile library (1.216 ratio) and is not clipped.",
        "- Group cells (BROADLEAF, CONIFER) use group RSYC curves; generic cells use the tile generic curve.",
        "- The BC zonal total is the 2028+ steady state; 2021-2027 zonal caps phase down from historic levels.",
        "- The 119M basis is an observed harvest level, not a sustainable-supply estimate.",
        "",
    ]
    path = artifact_dir / "NATIONAL_BASE_119M_OSECAN_REPORT.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def build_manifest(
    root: Path,
    artifact_dir: Path,
    outputs: Sequence[Path],
    generated_utc: str,
) -> dict[str, object]:
    inputs = [
        root / HARVEST_BASIS,
        root / Path("data/derived/national/harvest_basis/harvest_reference_paths.csv"),
        root / national.TILE_SUPPLY_DIR / "rsyc_tile_supply_summary.json",
        root / national.TILE_SUPPLY_DIR / "rsyc_tile_harvest_sensitivity.csv",
        root / SUPPLY_DIR / "SUPBCWB1_activity_limits_base.csv",
        root / SUPPLY_DIR / "SUPBCWB1_variable_cost_base.csv",
        root / SUPPLY_DIR / "SUPBCWB2_activity_limits_base.csv",
        root / SUPPLY_DIR / "SUPBCWB2_variable_cost_base.csv",
        *(
            artifact_dir / name
            for name in (
                "SUPBCW_activity_limits_national_base_119m.csv",
                "SUPBCW_activity_limits_national_base_119m_low.csv",
                "SUPBCW_activity_limits_national_base_119m_high.csv",
                "SUPBCW_variable_cost_base.csv",
                "SUPBCW_variable_cost_low.csv",
                "SUPBCW_variable_cost_high.csv",
                "supply_basis.json",
            )
        ),
    ]
    input_rows = [
        {
            "path": _relative(path, root),
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }
        for path in inputs
        if path.exists()
    ]
    output_rows = [
        {
            "path": _relative(path, root),
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }
        for path in outputs
    ]
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
        "harvest_basis": {
            "observed_year": 2024,
            "national_log_m3": LOG_VOLUME_M3_PER_YEAR,
            "source": (
                "NRCan Statistical Profiles FSD management volume; 2024 Canada "
                "total and 12-province sum both 118,977,339 m3"
            ),
            "path_rule": "2024 observed held flat 2025-2070; not a forecast",
        },
        "assumptions": [
            "RSYC-only v0: no local corrections, benchmark correction factors withheld.",
            (
                "The 118,977,339 m3 log/yr basis is the observed 2024 national "
                f"harvest, converted through the fixed tile-mix factor "
                f"{TILE_FACTOR_ODT_PER_M3} ODT/m3 total and log share "
                f"{LOG_SHARE}; no adopted AAC, THLB, or operability filter is applied."
            ),
            (
                "The BC zonal SUPBCWB1/B2 caps and costs are unchanged; SUPBCW "
                "carries only the rest-of-Canada balance, so the three caps sum "
                "to the national total."
            ),
            (
                "The stress hydro constraint is a TotalTechnologyAnnualActivityUpperLimit "
                "cap at every PWRHYD<z>'s 2021 base-run activity; it is an upper "
                "bound, not fixed dispatch, and a sensitivity probe, not a forecast."
            ),
            "The 150M case is an upper scenario bound, not the central basis.",
        ],
        "inputs": input_rows,
        "outputs": output_rows,
        "run_evidence": {
            "runs_root": "/tmp/osecan_national/results",
            "public_repo_modified": False,
            "note": (
                "Run summaries are derived from the solved CSVs in the disposable "
                "copy; only the summaries, dispatch table, and report are committed."
            ),
        },
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model-root",
        type=Path,
        default=Path("/tmp/osecan_national"),
        help="Disposable OSeCAN copy containing SETs, data/params, and results.",
    )
    parser.add_argument("--skip-solve", action="store_true")
    parser.add_argument("--skip-harmonize", action="store_true")
    args = parser.parse_args(argv)

    root = REPO_ROOT
    artifact_dir = root / OUTPUT_DIR
    model_root = args.model_root.resolve()
    if not (model_root / "data/params").is_dir():
        raise FileNotFoundError(f"missing model params: {model_root / 'data/params'}")

    supply = write_parameter_files(root)
    for spec in RUN_SPECS:
        build_scenario_params(model_root, artifact_dir, spec)
    if not args.skip_solve:
        solve_runs(model_root, RUN_SPECS)
    evidence = (
        collect_harmonize_evidence(model_root, model_root / "results")
        if not args.skip_harmonize
        else {"all_passed": False, "checks": [], "note": "Skipped."}
    )
    summaries = build_run_summaries(
        model_root / "results", artifact_dir, root, evidence
    )
    dispatch = build_dispatch_rows(model_root / "results")
    _write_csv(
        artifact_dir / "dispatch_by_zone.csv",
        ["run", "technology", "category", "zone", "year", "value_pj"],
        dispatch,
    )
    summary_table = {
        "rows": [
            {
                "run": run["run"],
                "objective_musd": run["objective_musd"],
                "emissions_mt": run["emissions_mt"]["total"],
                "supbcw_total_pj": run["supbcw_pj"]["total"],
                "supbcwb1_total_pj": run["supbcwb1_pj"]["total"],
                "supbcwb2_total_pj": run["supbcwb2_pj"]["total"],
            }
            for run in summaries["runs"].values()
        ]
    }
    (artifact_dir / "run_summaries.json").write_text(
        json.dumps(summaries, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (artifact_dir / "run_summary_table.json").write_text(
        json.dumps(summary_table, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    report = write_report(artifact_dir, supply, summaries, root)
    generated = datetime.now(UTC).isoformat(timespec="seconds")
    outputs = [
        artifact_dir / "SUPBCW_activity_limits_national_base_119m.csv",
        artifact_dir / "SUPBCW_activity_limits_national_base_119m_low.csv",
        artifact_dir / "SUPBCW_activity_limits_national_base_119m_high.csv",
        artifact_dir / "SUPBCW_variable_cost_base.csv",
        artifact_dir / "SUPBCW_variable_cost_low.csv",
        artifact_dir / "SUPBCW_variable_cost_high.csv",
        artifact_dir / "supply_basis.json",
        artifact_dir / "run_summaries.json",
        artifact_dir / "run_summary_table.json",
        artifact_dir / "dispatch_by_zone.csv",
        report,
    ]
    manifest = build_manifest(root, artifact_dir, outputs, generated)
    (artifact_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"wrote {artifact_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
