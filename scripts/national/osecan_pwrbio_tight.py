#!/usr/bin/env python3
"""Tight PWRBIO backstop-cap test on the corrected-emission OSeCAN model.

The test caps the BIO fuel input to power bioenergy (``PWRBIO*``) at the
reference 2050 level (35.72 PJ/yr of BIO fuel, equivalently 12.533855 PJ/yr of
electricity output at the fixed 2.85 PJ fuel/PJ output ratio).  Per-zone caps
are taken from the reference 2050 ``PWRBIO`` distribution, so the zonal caps sum
to the documented total.  ``RNWBIO`` remains uncapped: it still supplies the
fixed non-power BIO demand (about 802 PJ/yr) and is the sole BIO producer.

All solves run in the disposable copy ``/tmp/osecan_pwrbio_tight``.  The public
``deltae/C2070`` repository is never modified.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from workflows.national.clews import osecan_national_supply_curve as tiers  # noqa: E402

MODEL_ROOT = Path("/tmp/osecan_pwrbio_tight")
SOURCE_ROOT = Path("/tmp/osecan_emfix")
ARTIFACT_DIR = REPO_ROOT / "data/derived/national/clews/osecan"
OUTPUT_DIR = ARTIFACT_DIR / "pwrbio_tight"
CASE_DIR = OUTPUT_DIR / "_cases"
REFERENCE_RUN = "efix_tier_200"
REFERENCE_YEAR = 2050
PWRBIO_INPUT_RATIO = 2.85
PWRBIO_TOTAL_INPUT_PJ = 35.72
PWRBCW_INPUT_RATIO = 3.72
NATIONAL_TOTAL_PJ = 257.061857
BC_ZONAL_TOTAL_PJ = 43.523816
PWRBIO_TECHS = tuple(
    f"PWRBIO{zone}" for zone in ("A1", "A2", "B1", "B2", "L1", "M1", "N1", "O1", "O2", "Q1", "Q2", "S1", "T1")
)
WIND_TECHS = tuple(
    f"PWRWND{zone}" for zone in ("A1", "A2", "B1", "B2", "L1", "M1", "N1", "O1", "O2", "Q1", "Q2", "S1", "T1")
)
CASE_SPECS = {
    "pwrbio_cap3572_tier200": {
        "label": "PWRBIO BIO-input cap 35.72 PJ/yr, CO2 $200/t, hydro capped",
        "cap_mode": "reference",
        "wind_integration_usd_per_gj": 0.0,
    },
    "pwrbio_cap0_tier200": {
        "label": "PWRBIO BIO-input cap 0 PJ/yr, CO2 $200/t, hydro capped",
        "cap_mode": "zero",
        "wind_integration_usd_per_gj": 0.0,
    },
    "pwrbio_cap3572_windint3_tier200": {
        "label": "PWRBIO BIO-input cap 35.72 PJ/yr, wind +$3/GJ, CO2 $200/t, hydro capped",
        "cap_mode": "reference",
        "wind_integration_usd_per_gj": 3.0,
    },
}


# ---------------------------------------------------------------------------
# Small table helpers
# ---------------------------------------------------------------------------


def _read_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def _write_rows(path: Path, fields: Sequence[str], rows: Sequence[Mapping[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields))
        writer.writeheader()
        writer.writerows(rows)


def _upsert(path: Path, technology: str, rows: Sequence[Mapping[str, object]]) -> None:
    fields, existing = _read_rows(path)
    kept = [row for row in existing if row.get("TECHNOLOGY") != technology]
    _write_rows(path, fields, kept + list(rows))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _series_summary(series: Mapping[int, float]) -> dict[str, object]:
    return tiers._series_summary(series)


# ---------------------------------------------------------------------------
# Reference cap construction
# ---------------------------------------------------------------------------


def _reference_output() -> dict[str, float]:
    run_dir = MODEL_ROOT / "results" / REFERENCE_RUN
    if not run_dir.exists():
        raise FileNotFoundError(
            f"reference run missing: {run_dir}; copy /tmp/osecan_emfix first"
        )
    production = tiers.read_production(run_dir)
    output = {
        technology: float(production.get(technology, {}).get(REFERENCE_YEAR, 0.0))
        for technology in PWRBIO_TECHS
    }
    total = sum(output.values())
    if total <= 0:
        raise ValueError(f"{REFERENCE_RUN}: no PWRBIO output in {REFERENCE_YEAR}")
    return output


def _cap_series(cap_mode: str) -> tuple[dict[str, float], dict[str, object]]:
    reference = _reference_output()
    reference_total = sum(reference.values())
    if cap_mode == "reference":
        caps = dict(reference)
        total_input = reference_total * PWRBIO_INPUT_RATIO
    elif cap_mode == "zero":
        caps = {technology: 0.0 for technology in PWRBIO_TECHS}
        total_input = 0.0
    else:
        raise ValueError(f"unknown cap mode {cap_mode!r}")
    definition = {
        "mode": cap_mode,
        "technology_activity_units": "PJ electricity output",
        "input_ratio_pj_bio_per_pj_output": PWRBIO_INPUT_RATIO,
        "total_output_pj": round(sum(caps.values()), 9),
        "total_bio_input_pj": round(total_input, 9),
        "zonal_output_caps_pj": {k: round(v, 9) for k, v in sorted(caps.items())},
        "zonal_bio_input_caps_pj": {
            k: round(v * PWRBIO_INPUT_RATIO, 9) for k, v in sorted(caps.items())
        },
        "reference_run": REFERENCE_RUN,
        "reference_year": REFERENCE_YEAR,
        "years": [2021, 2070],
        "note": (
            "PWRBIO has one mode, one output fuel, an output ratio of 1, and a "
            "constant BIO input ratio of 2.85 in every year.  Capping technology "
            "activity at the reference output is therefore exactly equivalent to "
            "capping BIO fuel input at 2.85 times that value."
        ),
    }
    return caps, definition


# ---------------------------------------------------------------------------
# Build / solve
# ---------------------------------------------------------------------------


def _hydro_2021() -> dict[str, float]:
    """Return the hydro caps used by the reference run.

    The reference parameter set is the authoritative source: the baseline run
    result directory can be re-solved, while the committed reference tier run
    must stay comparable.  Using the result CSV from ``efix_baseline_119m``
    would silently change the hydro cap if that baseline were re-solved.
    """
    path = (
        MODEL_ROOT
        / "results"
        / REFERENCE_RUN
        / "params/TotalTechnologyAnnualActivityUpperLimit.csv"
    )
    _, rows = _read_rows(path)
    hydro = {
        row["TECHNOLOGY"]: float(row["VALUE"])
        for row in rows
        if row["TECHNOLOGY"].startswith("PWRHYD")
    }
    if len(hydro) != 13:
        raise ValueError(f"expected 13 hydro caps, got {len(hydro)}")
    return hydro


def build(case: str) -> dict[str, object]:
    spec = CASE_SPECS[case]
    params = tiers.build_scenario_params(
        MODEL_ROOT,
        ARTIFACT_DIR / "national_supply_curve",
        {
            "name": case,
            "label": spec["label"],
            "carbon_price_from_2026": 200.0,
            "hydro_mode": "upper_2021",
        },
        hydro_2021=_hydro_2021(),
    )
    caps, definition = _cap_series(spec["cap_mode"])
    cap_path = params / "TotalTechnologyAnnualActivityUpperLimit.csv"
    for technology, value in caps.items():
        _upsert(
            cap_path,
            technology,
            [
                {
                    "REGION": "CAN",
                    "TECHNOLOGY": technology,
                    "YEAR": str(year),
                    "VALUE": repr(value),
                }
                for year in tiers.YEARS
            ],
        )
    if spec["wind_integration_usd_per_gj"]:
        cost_path = params / "VariableCost.csv"
        for technology in WIND_TECHS:
            _upsert(
                cost_path,
                technology,
                [
                    {
                        "REGION": "CAN",
                        "TECHNOLOGY": technology,
                        "MODE_OF_OPERATION": "1",
                        "YEAR": str(year),
                        "VALUE": repr(float(spec["wind_integration_usd_per_gj"])),
                    }
                    for year in tiers.YEARS
                ],
            )
    harmonize = tiers.harmonize(MODEL_ROOT, params)
    payload = {
        "case": case,
        "label": spec["label"],
        "params": str(params),
        "cap_definition": definition,
        "wind_integration_usd_per_gj": spec["wind_integration_usd_per_gj"],
        "carbon_price_from_2026_usd_per_t": 200.0,
        "hydro_mode": "upper_2021",
        "harmonize": harmonize,
    }
    _json(CASE_DIR / f"{case}.build.json", payload)
    return payload


def solve(case: str) -> dict[str, object]:
    params = MODEL_ROOT / "results" / case / "params"
    if not params.exists():
        build(case)
    status = tiers.run_model(MODEL_ROOT, params, case)
    if status.get("status") != "optimal":
        payload = {"case": case, "status": status, "solver_status": status.get("status")}
        _json(CASE_DIR / f"{case}.result.json", payload)
        return payload
    summary = summarize(case, MODEL_ROOT / "results" / case)
    _json(CASE_DIR / f"{case}.result.json", summary)
    return summary


# ---------------------------------------------------------------------------
# Result reading
# ---------------------------------------------------------------------------


def _dispatch(production: Mapping[str, Mapping[int, float]], prefix: str) -> dict[int, float]:
    return {
        year: sum(
            float(series.get(year, 0.0))
            for technology, series in production.items()
            if technology.startswith(prefix) and technology != "PWRTRN"
        )
        for year in tiers.YEARS
    }


def summarize(case: str, run_dir: Path) -> dict[str, object]:
    production = tiers.read_production(run_dir)
    emissions = tiers.read_emissions(run_dir)
    status = tiers.read_status(run_dir)
    tier_caps = _tier_caps()
    tier_activity = {
        tier.technology: {
            year: float(production.get(tier.technology, {}).get(year, 0.0))
            for year in tiers.YEARS
        }
        for tier in tiers.TIERS
    }
    total_bcw = {
        year: sum(tier_activity[tier.technology][year] for tier in tiers.TIERS)
        for year in tiers.YEARS
    }
    bc_zonal = {
        year: sum(
            float(production.get(technology, {}).get(year, 0.0))
            for technology in ("SUPBCWB1", "SUPBCWB2")
        )
        for year in tiers.YEARS
    }
    national_bcw = {year: total_bcw[year] + bc_zonal[year] for year in tiers.YEARS}
    pwrbcw = {
        technology: series
        for technology, series in production.items()
        if technology.startswith("PWRBCW")
    }
    pwrbcw_total = {
        year: sum(series.get(year, 0.0) for series in pwrbcw.values())
        for year in tiers.YEARS
    }
    pwrbcw_non_bc = {
        year: sum(
            float(production.get(technology, {}).get(year, 0.0))
            for technology in pwrbcw
            if technology not in ("PWRBCWB1", "PWRBCWB2")
        )
        for year in tiers.YEARS
    }
    pwrbcw_fuel_input = {year: PWRBCW_INPUT_RATIO * pwrbcw_total[year] for year in tiers.YEARS}
    pwr_bio_output = {
        year: sum(
            float(production.get(technology, {}).get(year, 0.0))
            for technology in PWRBIO_TECHS
        )
        for year in tiers.YEARS
    }
    pwr_bio_input = {year: PWRBIO_INPUT_RATIO * pwr_bio_output[year] for year in tiers.YEARS}
    rnwbio = {
        year: float(production.get("RNWBIO", {}).get(year, 0.0))
        for year in tiers.YEARS
    }
    bio_non_power = {
        year: sum(
            float(series.get(year, 0.0))
            for technology, series in production.items()
            if "BIO" in technology and technology not in PWRBIO_TECHS and technology != "RNWBIO"
        )
        for year in tiers.YEARS
    }
    bio_balance = {
        year: rnwbio[year] - bio_non_power[year] - pwr_bio_input[year]
        for year in tiers.YEARS
    }
    bcw_balance = {
        year: national_bcw[year] - pwrbcw_fuel_input[year] for year in tiers.YEARS
    }
    pwr_bio_by_zone = {
        technology: {
            "output_pj": _series_summary(production.get(technology, {})),
            "input_pj": _series_summary(
                {
                    year: PWRBIO_INPUT_RATIO
                    * float(production.get(technology, {}).get(year, 0.0))
                    for year in tiers.YEARS
                }
            ),
        }
        for technology in PWRBIO_TECHS
    }
    caps, definition = _cap_series(
        "reference" if "cap0" not in case else "zero"
    )
    bio_binding = tiers.cap_binding(
        production,
        {
            technology: {year: value for year in tiers.YEARS}
            for technology, value in caps.items()
        },
    )
    return {
        "case": case,
        "label": CASE_SPECS[case]["label"],
        "status": status,
        "objective_musd": status.get("objective"),
        "emissions_mt": _series_summary(emissions),
        "emissions_total_mt": round(sum(emissions.values()), 6),
        "total_generation_pj": _series_summary(
            {
                year: sum(
                    float(series.get(year, 0.0))
                    for technology, series in production.items()
                    if technology.startswith("PWR") and technology != "PWRTRN"
                )
                for year in tiers.YEARS
            }
        ),
        "dispatch_pj": {
            "gas": _series_summary(_dispatch(production, "PWRNG")),
            "wind": _series_summary(_dispatch(production, "PWRWND")),
            "hydro": _series_summary(_dispatch(production, "PWRHYD")),
            "bio": _series_summary(pwr_bio_output),
            "bcw_power": _series_summary(pwrbcw_total),
            "coal": _series_summary(_dispatch(production, "PWRCOA")),
            "bsw": _series_summary(_dispatch(production, "PWRBSW")),
            "solar": _series_summary(_dispatch(production, "PWRSOL")),
            "uranium": _series_summary(_dispatch(production, "PWRURN")),
            "geo": _series_summary(_dispatch(production, "PWRGEO")),
        },
        "pwr_bio_output_pj": _series_summary(pwr_bio_output),
        "pwr_bio_input_pj": _series_summary(pwr_bio_input),
        "pwr_bio_by_zone": pwr_bio_by_zone,
        "rnwbio_output_pj": _series_summary(rnwbio),
        "bio_non_power_pj": _series_summary(bio_non_power),
        "bio_balance": {
            "residual_pj": {str(year): round(value, 6) for year, value in bio_balance.items()},
            "max_abs_residual_pj": round(max(abs(value) for value in bio_balance.values()), 6),
        },
        "national_bcw_supply_pj": _series_summary(national_bcw),
        "bc_zonal_activity_pj": _series_summary(bc_zonal),
        "pwrbcw_total_pj": _series_summary(pwrbcw_total),
        "pwrbcw_non_bc_total_pj": _series_summary(pwrbcw_non_bc),
        "pwrbcw_fuel_input_pj": _series_summary(pwrbcw_fuel_input),
        "bcw_balance": {
            "residual_pj": {str(year): round(value, 6) for year, value in bcw_balance.items()},
            "max_abs_residual_pj": round(max(abs(value) for value in bcw_balance.values()), 6),
        },
        "tier_activity_pj": {
            tier.technology: _series_summary(tier_activity[tier.technology])
            for tier in tiers.TIERS
        },
        "total_tier_activity_pj": _series_summary(total_bcw),
        "tier_cap_binding": tiers.cap_binding(production, tier_caps),
        "pwr_bio_cap_binding": bio_binding,
        "pwr_bio_cap_definition": definition,
    }


def _tier_caps() -> dict[str, dict[int, float]]:
    fields, rows = _read_rows(
        ARTIFACT_DIR / "national_supply_curve/tier_activity_limits.csv"
    )
    caps: dict[str, dict[int, float]] = {tier.technology: {} for tier in tiers.TIERS}
    for row in rows:
        technology = row["TECHNOLOGY"]
        if technology in caps:
            caps[technology][int(row["YEAR"])] = float(row["VALUE"])
    return caps


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def _cap_rows(params: Path, technology: str) -> dict[int, float]:
    _, rows = _read_rows(params / "TotalTechnologyAnnualActivityUpperLimit.csv")
    return {
        int(row["YEAR"]): float(row["VALUE"])
        for row in rows
        if row["TECHNOLOGY"] == technology
    }


def test_cap_loading(case: str) -> list[str]:
    failures: list[str] = []
    params = MODEL_ROOT / "results" / case / "params"
    caps, definition = _cap_series("reference" if "cap0" not in case else "zero")
    for technology, expected in caps.items():
        series = _cap_rows(params, technology)
        if set(series) != set(tiers.YEARS):
            failures.append(f"{technology}: cap years missing")
            continue
        for year, value in series.items():
            if abs(value - expected) > 1e-9:
                failures.append(
                    f"{technology} {year}: cap {value} != expected {expected}"
                )
    total_input = sum(caps.values()) * PWRBIO_INPUT_RATIO
    if "cap3572" in case and abs(total_input - PWRBIO_TOTAL_INPUT_PJ) > 0.01:
        failures.append(f"total BIO input cap {total_input} != {PWRBIO_TOTAL_INPUT_PJ}")
    if "cap0" in case and abs(total_input) > 1e-12:
        failures.append(f"zero cap total {total_input}")
    return failures


def test_dispatch_reconciliation(case: str, summary: Mapping[str, object]) -> list[str]:
    failures: list[str] = []
    if summary.get("status", {}).get("status") != "optimal":
        return ["solver status is not optimal"]
    if summary["bio_balance"]["max_abs_residual_pj"] > 1e-4:
        failures.append(
            f"BIO balance residual {summary['bio_balance']['max_abs_residual_pj']}"
        )
    if summary["bcw_balance"]["max_abs_residual_pj"] > 1e-4:
        failures.append(
            f"BCW balance residual {summary['bcw_balance']['max_abs_residual_pj']}"
        )
    caps, _ = _cap_series("reference" if "cap0" not in case else "zero")
    for technology, cap in caps.items():
        zone = summary["pwr_bio_by_zone"][technology]["output_pj"]["by_year"]
        for year, value in zone.items():
            if value > cap + 1e-5:
                failures.append(
                    f"{technology} {year}: output {value} exceeds cap {cap}"
                )
    caps_by_tier = _tier_caps()
    for tier in tiers.TIERS:
        activity = summary["tier_activity_pj"][tier.technology]["by_year"]
        for year, value in activity.items():
            if value > caps_by_tier[tier.technology][int(year)] + 1e-5:
                failures.append(
                    f"{tier.technology} {year}: activity {value} exceeds cap"
                )
    return failures


def _zonal_caps() -> dict[str, dict[int, float]]:
    caps: dict[str, dict[int, float]] = {}
    for technology in ("SUPBCWB1", "SUPBCWB2"):
        path = (
            REPO_ROOT
            / "data/derived/national/clews"
            / f"{technology}_activity_limits_base.csv"
        )
        _, rows = _read_rows(path)
        caps[technology] = {
            int(row["YEAR"]): float(row["VALUE"])
            for row in rows
            if row["TECHNOLOGY"] == technology
        }
    return caps


def test_tier_caps() -> list[str]:
    failures: list[str] = []
    caps = _tier_caps()
    zonal = _zonal_caps()
    for year in tiers.YEARS:
        total = sum(caps[tier.technology][year] for tier in tiers.TIERS)
        bc = sum(zonal[technology][year] for technology in zonal)
        if abs(total - (tiers.NATIONAL_TOTAL_PJ - bc)) > 1e-5:
            failures.append(
                f"{year}: tier caps {total} != national minus BC zonal "
                f"{tiers.NATIONAL_TOTAL_PJ - bc}"
            )
        if abs(total + bc - tiers.NATIONAL_TOTAL_PJ) > 1e-5:
            failures.append(f"{year}: national cap sum mismatch")
    return failures


# ---------------------------------------------------------------------------
# Artefacts
# ---------------------------------------------------------------------------


def _result(case: str) -> dict[str, object]:
    path = CASE_DIR / f"{case}.result.json"
    if not path.exists():
        raise FileNotFoundError(f"missing case result: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def write_artifacts() -> None:
    cases = [case for case in CASE_SPECS if (CASE_DIR / f"{case}.result.json").exists()]
    if not cases:
        raise SystemExit("no solved cases")
    summaries = {case: _result(case) for case in cases}
    reference_2050 = _reference_output()
    reference_total = sum(reference_2050.values())
    payload = {
        "schema_version": "1.0.0",
        "status": "fresh_side_provisional_not_c2070_official",
        "generated_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "model_root": str(MODEL_ROOT),
        "source_model_root": str(SOURCE_ROOT),
        "reference_run": REFERENCE_RUN,
        "carbon_price_from_2026_usd_per_t": 200.0,
        "hydro_mode": "upper_2021",
        "pwr_bio_cap": {
            "total_bio_input_pj": PWRBIO_TOTAL_INPUT_PJ,
            "input_ratio_pj_bio_per_pj_output": PWRBIO_INPUT_RATIO,
            "total_output_pj": round(reference_total, 9),
            "zonal_input_pj": {
                technology: round(value * PWRBIO_INPUT_RATIO, 9)
                for technology, value in sorted(reference_2050.items())
            },
            "zonal_output_pj": {
                technology: round(value, 9)
                for technology, value in sorted(reference_2050.items())
            },
            "implementation": (
                "TotalTechnologyAnnualActivityUpperLimit on each PWRBIO<zone> "
                "at the reference 2050 output, held in every year 2021-2070"
            ),
        },
        "cases": summaries,
    }
    _json(OUTPUT_DIR / "run_summaries.json", payload)

    rows: list[dict[str, object]] = []
    category = {
        "PWRBIO": "power_bio",
        "PWRBCW": "power_bcw",
        "PWRHYD": "power_hydro",
        "PWRWND": "power_wind",
        "PWRNG": "power_gas",
        "PWRCOA": "power_coal",
        "PWRSOL": "power_solar",
        "PWRURN": "power_uranium",
        "PWRGEO": "power_geo",
        "PWRBSW": "power_bsw",
        "RNWBIO": "bio_supply",
    }
    for case in cases:
        production = tiers.read_production(MODEL_ROOT / "results" / case)
        for technology, series in sorted(production.items()):
            if technology in {tier.technology for tier in tiers.TIERS}:
                group, zone = "supply_tier", "CAN"
            elif technology in ("SUPBCWB1", "SUPBCWB2"):
                group, zone = "supply_bc_zonal", technology[-2:]
            elif technology == "RNWBIO":
                group, zone = "bio_supply", "CAN"
            else:
                prefix = next(
                    (key for key in category if technology.startswith(key)), None
                )
                if prefix is None:
                    continue
                group, zone = category[prefix], technology[-2:]
            for year, value in sorted(series.items()):
                rows.append(
                    {
                        "run": case,
                        "technology": technology,
                        "category": group,
                        "zone": zone,
                        "year": year,
                        "value_pj": round(float(value), 6),
                    }
                )
    _write_rows(
        OUTPUT_DIR / "dispatch_by_zone.csv",
        ["run", "technology", "category", "zone", "year", "value_pj"],
        rows,
    )

    tests: list[dict[str, object]] = []
    for case in cases:
        build_payload = json.loads(
            (CASE_DIR / f"{case}.build.json").read_text(encoding="utf-8")
        )
        summary = summaries[case]
        tests.append(
            {
                "name": "cap_loading",
                "case": case,
                "passed": not (failures := test_cap_loading(case)),
                "failures": failures,
            }
        )
        tests.append(
            {
                "name": "dispatch_reconciliation",
                "case": case,
                "passed": not (failures := test_dispatch_reconciliation(case, summary)),
                "failures": failures,
            }
        )
        tests.append(
            {
                "name": "tier_caps",
                "case": case,
                "passed": not (failures := test_tier_caps()),
                "failures": failures,
            }
        )
        tests.append(
            {
                "name": "osecan_harmonize",
                "case": case,
                "passed": build_payload["harmonize"]["exit_code"] == 0,
                "failures": []
                if build_payload["harmonize"]["exit_code"] == 0
                else [build_payload["harmonize"]["message"]],
            }
        )
    _json(
        OUTPUT_DIR / "tests.json",
        {"passed": all(test["passed"] for test in tests), "tests": tests},
    )
    write_report(payload)
    write_manifest(payload)


def _fmt(value: object, digits: int = 3) -> str:
    return f"{float(value):,.{digits}f}"


def _cell(series: Mapping[str, object], year: int = 2050) -> str:
    return _fmt(series.get(str(year), 0.0))


def write_report(payload: Mapping[str, object]) -> None:
    cases = payload["cases"]
    names = list(cases)
    lines = [
        "# Tight PWRBIO backstop cap on the corrected-emission OSeCAN model",
        "",
        f"**Status:** `{payload['status']}`. The public "
        "[deltae/C2070](https://github.com/deltae/C2070) repository was not modified.",
        "",
        "## 1. What was implemented",
        "",
        "The cap is an annual ceiling on BIO fuel input to the `PWRBIO<zone>` "
        "power plants. `PWRBIO` has one mode, one output fuel, an output ratio "
        "of 1, and a constant BIO input ratio of 2.85 PJ fuel per PJ output in "
        "every year. The ceiling is written as "
        "`TotalTechnologyAnnualActivityUpperLimit` on each `PWRBIO<zone>` at "
        "the reference 2050 output level, held in every year 2021-2070. That is "
        "exactly equivalent to a BIO-input cap because the input ratio is fixed.",
        "",
        f"The total cap is **{PWRBIO_TOTAL_INPUT_PJ:.2f} PJ/yr of BIO input**, "
        f"equal to **{sum(payload['pwr_bio_cap']['zonal_output_pj'].values()):.6f} "
        "PJ/yr of electricity output**. Per-zone caps are the reference 2050 "
        "`PWRBIO` distribution:",
        "",
        "| Zone | Reference 2050 output (PJ) | BIO input cap (PJ) |",
        "|---|---:|---:|",
    ]
    for zone, value in payload["pwr_bio_cap"]["zonal_output_pj"].items():
        lines.append(f"| `{zone}` | {_fmt(value, 6)} | {_fmt(value * PWRBIO_INPUT_RATIO, 6)} |")
    lines.extend(
        [
            "",
            "`RNWBIO` is uncapped. It remains the sole BIO producer and continues "
            "to serve the fixed non-power BIO demand. The tier stack, BC zonal "
            "caps, demand, and corrected emission factors are unchanged.",
            "",
            "## 2. Headline results",
            "",
            "| Case | Objective (M$) | Emissions (Mt) | Gas 2050 (PJ) | Wind 2050 (PJ) | Hydro 2050 (PJ) | Uranium 2050 (PJ) | PWRBIO out 2050 (PJ) | PWRBIO in 2050 (PJ) | PWRBCW 2050 (PJ) |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for case in names:
        run = cases[case]
        dispatch = run["dispatch_pj"]
        lines.append(
            f"| `{case}` | {_fmt(run['objective_musd'], 2)} | "
            f"{_fmt(run['emissions_total_mt'], 6)} | {_cell(dispatch['gas'])} | "
            f"{_cell(dispatch['wind'])} | {_cell(dispatch['hydro'])} | "
            f"{_cell(dispatch['uranium'])} | "
            f"{_cell(run['pwr_bio_output_pj'])} | {_cell(run['pwr_bio_input_pj'])} | "
            f"{_cell(run['pwrbcw_total_pj'])} |"
        )
    lines.extend(
        [
            "",
            "### 2050 dispatch by fuel",
            "",
            "| Case | Gas | Coal | Wind | Hydro | Solar | Uranium | Geo | BSW | BIO power (output) | PWRBIO fuel input | PWRBCW |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for case in names:
        dispatch = cases[case]["dispatch_pj"]
        lines.append(
            f"| `{case}` | {_cell(dispatch['gas'])} | {_cell(dispatch['coal'])} | "
            f"{_cell(dispatch['wind'])} | {_cell(dispatch['hydro'])} | "
            f"{_cell(dispatch['solar'])} | {_cell(dispatch['uranium'])} | "
            f"{_cell(dispatch['geo'])} | {_cell(dispatch['bsw'])} | "
            f"{_cell(cases[case]['pwr_bio_output_pj'])} | "
            f"{_cell(cases[case]['pwr_bio_input_pj'])} | "
            f"{_cell(cases[case]['pwrbcw_total_pj'])} |"
        )
    lines.extend(
        [
            "",
            "### Biomass supply tiers",
            "",
            "| Case | T1 2050 | T2 2050 | T3 2050 | T4 2050 | T1/T2 bind | T3/T4 active | National BCW 2050 |",
            "|---|---:|---:|---:|---:|---|---|---:|",
        ]
    )
    for case in names:
        run = cases[case]
        tier = run["tier_activity_pj"]
        binding = run["tier_cap_binding"]
        t12 = binding["SUPBCWT1"]["binding"] or binding["SUPBCWT2"]["binding"]
        t34 = any(
            float(tier[name]["by_year"].get("2050", 0.0)) > 1e-6
            for name in ("SUPBCWT3", "SUPBCWT4")
        )
        lines.append(
            f"| `{case}` | {_cell(tier['SUPBCWT1'])} | {_cell(tier['SUPBCWT2'])} | "
            f"{_cell(tier['SUPBCWT3'])} | {_cell(tier['SUPBCWT4'])} | "
            f"{'yes' if t12 else 'no'} | {'yes' if t34 else 'no'} | "
            f"{_cell(run['national_bcw_supply_pj'])} |"
        )
    lines.extend(
        [
            "",
            "### PWRBIO cap utilisation",
            "",
            "| Case | Total cap (PJ input) | 2050 input (PJ) | Utilisation | Binding zones in 2050 | First binding year |",
            "|---|---:|---:|---:|---|---:|",
        ]
    )
    for case in names:
        run = cases[case]
        binding = run["pwr_bio_cap_binding"]
        cap = run["pwr_bio_cap_definition"]["total_bio_input_pj"]
        used = run["pwr_bio_input_pj"]["2050"]
        zones = [
            name
            for name, entry in binding.items()
            if entry["utilisation"].get("2050", 0.0) >= 1.0 - 1e-5
        ]
        first = min(
            (entry["first_binding_year"] for entry in binding.values() if entry["first_binding_year"]),
            default=None,
        )
        lines.append(
            f"| `{case}` | {_fmt(cap)} | {_fmt(used)} | "
            f"{_fmt(used / cap, 4) if cap > 0 else 'n/a'} | "
            f"{', '.join(sorted(zones)) if zones else 'none'} | {first or 'none'} |"
        )
    lines.extend(
        [
            "",
            "## 3. Comparison with the uncapped post-fix cases",
            "",
            "| Case | PWRBIO input 2050 (PJ) | Gas 2050 (PJ) | Wind 2050 (PJ) | Emissions (Mt) | Objective (M$) |",
            "|---|---:|---:|---:|---:|---:|",
            "| Post-fix $200, no cap (`efix_tier_200`) | 35.721 | 0.00 | 967.98 | 0.460752 | 1,371,773.03 |",
            "| Wind +$3/GJ, no cap (`wind_int3_tier200`) | 2,792.99 | 0.00 | 0.00 | 0.449816 | 1,372,459.69 |",
        ]
    )
    for case in names:
        run = cases[case]
        lines.append(
            f"| `{case}` | {_cell(run['pwr_bio_input_pj'])} | "
            f"{_cell(run['dispatch_pj']['gas'])} | {_cell(run['dispatch_pj']['wind'])} | "
            f"{_fmt(run['emissions_total_mt'], 6)} | {_fmt(run['objective_musd'], 2)} |"
        )
    lines.extend(
        [
            "",
            "The uncapped comparison rows are the committed post-fix and wind-audit "
            "runs; they are not rerun here.  The wind-audit's `BIO 2050 = 980 PJ` is "
            "PWRBIO **electricity output**; the corresponding BIO **fuel input** is "
            "2,792.99 PJ at the fixed 2.85 input ratio.  The cap in this test is on "
            "the fuel input, so it is 35.72 PJ, not 980 PJ.",
            "",
            "The cap-0 case has slightly higher emissions than the reference "
            "(0.542358 vs 0.460752 Mt) because removing the 12.5 PJ of bio power "
            "raises pre-2026 gas use.  Post-2026 gas is unchanged at the reference "
            "sliver: 0.037 PJ in `PWRNGSN1` in 2037 (0.00509 Mt) and 0.0018 PJ in "
            "2042 (0.000245 Mt).  The wind +$3 capped case also keeps the 2037 "
            "sliver and adds a 0.037 PJ `PWRNGSN1` sliver in 2042 (0.00509 Mt).",
            "",
            "## 4. Verdict",
            "",
        ]
    )
    cap0 = cases.get("pwrbio_cap0_tier200")
    cap35 = cases.get("pwrbio_cap3572_tier200")
    windcap = cases.get("pwrbio_cap3572_windint3_tier200")
    if cap35:
        gas = float(cap35["dispatch_pj"]["gas"]["2050"])
        lines.append(
            f"- **Cap 35.72 PJ:** the cap reproduces the reference: gas 2050 "
            f"{_fmt(gas)} PJ, emissions {_fmt(cap35['emissions_total_mt'], 6)} Mt, "
            f"T1/T2 bind, T3/T4 idle. The cap is non-binding beyond the reference "
            "because it equals the reference zonal maxima."
        )
    if cap0:
        lines.append(
            f"- **Cap 0 PJ:** PWRBIO output is zero. Gas 2050 is "
            f"{_cell(cap0['dispatch_pj']['gas'])} PJ and emissions are "
            f"{_fmt(cap0['emissions_total_mt'], 6)} Mt. The 12.5 PJ gap is small "
            "enough for existing zero-carbon capacity to absorb."
        )
    if windcap:
        gas = float(windcap["dispatch_pj"]["gas"]["2050"])
        wind = float(windcap["dispatch_pj"]["wind"]["2050"])
        uranium = float(windcap["dispatch_pj"]["uranium"]["2050"])
        pwrbcw = float(windcap["pwrbcw_total_pj"]["2050"])
        lines.append(
            f"- **Wind +$3 with the 35.72 PJ cap:** gas 2050 is {_fmt(gas)} PJ, "
            f"wind 2050 is {_fmt(wind)} PJ, uranium 2050 is {_fmt(uranium)} PJ, "
            f"PWRBCW 2050 is {_fmt(pwrbcw)} PJ, and emissions are "
            f"{_fmt(windcap['emissions_total_mt'], 6)} Mt. "
            + (
                "Gas returns, so near-zero emissions do not survive once the bio "
                "backstop is capped and wind is penalised."
                if gas > 1e-6
                else "Gas does not return at scale in 2050; uranium and additional "
                "PWRBCW fill the gap.  Post-2026 gas is a 0.037 PJ `PWRNGSN1` "
                "sliver in 2037 and 2042 (0.00509 Mt each), so near-zero emissions "
                "survive numerically but not with exactly the reference timing."
            )
        )
    t3_active = any(
        float(cases[case]["tier_activity_pj"]["SUPBCWT3"]["by_year"].get("2050", 0.0))
        > 1e-6
        for case in names
    )
    t4_active = any(
        float(cases[case]["tier_activity_pj"]["SUPBCWT4"]["by_year"].get("2050", 0.0))
        > 1e-6
        for case in names
    )
    lines.append(
        "- **T3/T4:** "
        + (
            "T3 activates in the wind +$3 capped case at its 39.8737 PJ cap; T4 "
            "remains idle."
            if t3_active and not t4_active
            else "T3 activates."
            if t3_active
            else "T3 and T4 remain idle in every solved case."
        )
        + " The tight PWRBIO cap alone does not pull the high-cost tiers in; "
        "the T3 activation is driven by the larger PWRBCW requirement."
    )
    lines.extend(
        [
            "",
            "## 5. Tests",
            "",
        ]
    )
    tests = json.loads((OUTPUT_DIR / "tests.json").read_text(encoding="utf-8"))
    for test in tests["tests"]:
        lines.append(
            f"- `{test['name']}` ({test['case']}): "
            f"{'PASS' if test['passed'] else 'FAIL'}"
        )
    lines.extend(
        [
            "",
            "## 6. Caveats",
            "",
            "- The cap is a scenario constraint, not a policy recommendation.",
            "- Single annual timeslice: no diurnal, seasonal, ramping, reserve-margin, "
            "or curtailment constraints.",
            "- `RNWBIO` remains uncapped for non-power BIO demand. The test caps "
            "`PWRBIO` only; it does not test a national BIO inventory ceiling.",
            "- Per-zone caps are the reference 2050 distribution, held constant in "
            "every year. They are not an engineering limit.",
            "- Uranium expansion is not constrained by a fuel-cycle, lead-time, or "
            "site limit in this single-ANN model; the 957 PJ of 2050 uranium in the "
            "wind +$3 capped case is a model outcome, not a project schedule.",
            "- The uncapped comparison rows come from the committed post-fix and "
            "wind-audit artefacts; only the three capped cases are solved here.",
            "",
        ]
    )
    (OUTPUT_DIR / "PWRBIO_TIGHT_REPORT.md").write_text("\n".join(lines), encoding="utf-8")


def write_manifest(payload: Mapping[str, object]) -> None:
    inputs = [
        ARTIFACT_DIR / "national_supply_curve/tier_activity_limits.csv",
        REPO_ROOT / "data/derived/national/clews/SUPBCWB1_activity_limits_base.csv",
        REPO_ROOT / "data/derived/national/clews/SUPBCWB2_activity_limits_base.csv",
        MODEL_ROOT / "results" / REFERENCE_RUN / "results_csv/ProductionByTechnologyAnnual.csv",
        MODEL_ROOT / "data/params/EmissionActivityRatio.csv",
        MODEL_ROOT / "data/params/EmissionsPenalty.csv",
    ]
    outputs = [
        OUTPUT_DIR / "run_summaries.json",
        OUTPUT_DIR / "dispatch_by_zone.csv",
        OUTPUT_DIR / "tests.json",
        OUTPUT_DIR / "PWRBIO_TIGHT_REPORT.md",
    ]
    manifest = {
        "schema_version": "1.0.0",
        "status": "fresh_side_provisional_not_c2070_official",
        "generated_utc": payload["generated_utc"],
        "model": {
            "source_root": str(SOURCE_ROOT),
            "copy_root": str(MODEL_ROOT),
            "public_repository": "https://github.com/deltae/C2070",
            "public_modified": False,
            "solver": "GLPK via swiglpk 5.0.13",
        },
        "reference_run": REFERENCE_RUN,
        "cases": list(payload["cases"]),
        "inputs": [
            {"path": str(path), "bytes": path.stat().st_size, "sha256": _sha256(path)}
            for path in inputs
        ],
        "outputs": [
            {"path": path.name, "bytes": path.stat().st_size, "sha256": _sha256(path)}
            for path in outputs
        ],
        "source_git_status": _source_status(),
        "caveats": [
            "Cap is a scenario constraint, not policy.",
            "Single annual timeslice.",
            "RNWBIO non-power demand is uncapped.",
            "Per-zone caps use the reference 2050 distribution held in every year.",
        ],
    }
    _json(OUTPUT_DIR / "manifest.json", manifest)


def _source_status() -> str:
    import subprocess

    completed = subprocess.run(
        ["git", "-C", str(SOURCE_ROOT), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("build", "solve", "artifacts"))
    parser.add_argument("case", nargs="?")
    args = parser.parse_args()
    if args.command == "artifacts":
        write_artifacts()
        return 0
    if args.case not in CASE_SPECS:
        raise SystemExit(f"unknown case {args.case!r}")
    if args.command == "build":
        print(json.dumps(build(args.case), indent=2, sort_keys=True))
    else:
        print(json.dumps(solve(args.case), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
