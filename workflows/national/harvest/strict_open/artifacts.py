"""Artifact writer for the strict-open BC logging and haul cost model."""

from __future__ import annotations

import csv
import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from statistics import fmean, median
from typing import Any

from workflows.national.harvest.strict_open.model import (
    COST_BASIS,
    DISTANCE_SCENARIOS,
    EVIDENCE,
    LICENCE_TIER,
    MODEL_ID,
    OBSERVED,
    PIECE_SCENARIOS,
    POLICY_GRADE,
    REGIONS,
    SLOPE_SCENARIOS,
    SYSTEMS,
    VOLUME_SCENARIOS,
    ZONES,
    StrictCostModelError,
    build_scenarios,
    estimate_scenario,
    parameter_rows,
    region_slope_context,
    region_slope_summary,
    sensitivity_rows,
    validation_payload,
    worked_examples,
    zone_summary,
)
from workflows.national.harvest.strict_open.sources import (
    CALIBRATION_CLAIM,
    DEFAULT_OUT_DIR,
    EXCLUDED_SOURCE_FAMILIES,
    MODEL_VERSION,
    REFERENCE_ESTIMATES_PATH,
    SOURCE_REGISTER,
    derived_constants_payload,
    generated_utc,
    input_hashes,
    observations_payload,
    source_rows,
    validate_source_register,
)

COMPARISON_COLUMNS = (
    "scenario_id",
    "zone",
    "region",
    "slope_class",
    "slope_pct",
    "system",
    "piece_class",
    "piece_size_m3",
    "volume_class",
    "sale_volume_m3",
    "distance_class",
    "haul_distance_km",
    "strict_logging_base_cad_m3",
    "strict_haul_base_cad_m3",
    "strict_total_base_cad_m3",
    "reference_logging_base_cad_m3",
    "reference_haul_base_cad_m3",
    "reference_total_base_cad_m3",
    "delta_total_cad_m3",
    "ratio_strict_to_reference",
    "reference_source_key",
    "reference_licence_tier",
    "reference_used_as_model_input",
    "comparison_status",
    "evidence",
    "cost_basis",
    "licence_tier",
    "policy_grade",
    "observed",
    "calibration_claim",
)


def _write_csv(
    path: Path, rows: Sequence[Mapping[str, Any]], fieldnames: Sequence[str]
) -> None:
    if not rows:
        raise StrictCostModelError(f"refusing to write empty table: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _reference_index() -> dict[tuple[str, ...], dict[str, str]] | None:
    """Read the public-reference model output for cross-checking only."""
    if not REFERENCE_ESTIMATES_PATH.is_file():
        return None
    required = {
        "zone",
        "slope_class",
        "piece_class",
        "volume_class",
        "distance_class",
        "logging_base_cad_m3",
        "haul_base_cad_m3",
        "total_base_cad_m3",
    }
    index: dict[tuple[str, ...], dict[str, str]] = {}
    with REFERENCE_ESTIMATES_PATH.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            raise StrictCostModelError(
                f"reference estimates missing columns: {required - set(reader.fieldnames or [])}"
            )
        for row in reader:
            key = (
                row["zone"],
                row["slope_class"],
                row["piece_class"],
                row["volume_class"],
                row["distance_class"],
            )
            if key in index:
                raise StrictCostModelError(f"duplicate reference key: {key}")
            index[key] = row
    return index


def comparison_rows(estimates: list[dict[str, str]]) -> list[dict[str, str]]:
    """Row-level cross-check against the existing public-reference model."""
    reference = _reference_index()
    rows: list[dict[str, str]] = []
    for estimate in estimates:
        base = {
            "scenario_id": estimate["scenario_id"],
            "zone": estimate["zone"],
            "region": estimate["region"],
            "slope_class": estimate["slope_class"],
            "slope_pct": estimate["slope_pct"],
            "system": estimate["system"],
            "piece_class": estimate["piece_class"],
            "piece_size_m3": estimate["piece_size_m3"],
            "volume_class": estimate["volume_class"],
            "sale_volume_m3": estimate["sale_volume_m3"],
            "distance_class": estimate["distance_class"],
            "haul_distance_km": estimate["haul_distance_km"],
            "strict_logging_base_cad_m3": estimate["logging_base_cad_m3"],
            "strict_haul_base_cad_m3": estimate["haul_base_cad_m3"],
            "strict_total_base_cad_m3": estimate["total_base_cad_m3"],
            "reference_source_key": "bc_open_cost_model_public_reference",
            "reference_licence_tier": "public_reference_copyrighted",
            "reference_used_as_model_input": "false",
            "evidence": EVIDENCE,
            "cost_basis": COST_BASIS,
            "licence_tier": LICENCE_TIER,
            "policy_grade": POLICY_GRADE,
            "observed": OBSERVED,
            "calibration_claim": CALIBRATION_CLAIM,
        }
        if reference is None:
            base.update(
                {
                    "reference_logging_base_cad_m3": "",
                    "reference_haul_base_cad_m3": "",
                    "reference_total_base_cad_m3": "",
                    "delta_total_cad_m3": "",
                    "ratio_strict_to_reference": "",
                    "comparison_status": "reference_unavailable",
                }
            )
            rows.append(base)
            continue
        key = (
            estimate["zone"],
            estimate["slope_class"],
            estimate["piece_class"],
            estimate["volume_class"],
            estimate["distance_class"],
        )
        match = reference.get(key)
        if match is None:
            base.update(
                {
                    "reference_logging_base_cad_m3": "",
                    "reference_haul_base_cad_m3": "",
                    "reference_total_base_cad_m3": "",
                    "delta_total_cad_m3": "",
                    "ratio_strict_to_reference": "",
                    "comparison_status": "reference_missing",
                }
            )
            rows.append(base)
            continue
        strict_total = float(estimate["total_base_cad_m3"])
        reference_total = float(match["total_base_cad_m3"])
        if reference_total <= 0.0:
            raise StrictCostModelError(f"non-positive reference total: {key}")
        base.update(
            {
                "reference_logging_base_cad_m3": match["logging_base_cad_m3"],
                "reference_haul_base_cad_m3": match["haul_base_cad_m3"],
                "reference_total_base_cad_m3": match["total_base_cad_m3"],
                "delta_total_cad_m3": f"{strict_total - reference_total:+.2f}",
                "ratio_strict_to_reference": f"{strict_total / reference_total:.3f}",
                "comparison_status": "matched_cross_check_only",
            }
        )
        rows.append(base)
    return rows


def comparison_summary(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Aggregate the row-level cross-check by zone and system."""
    matched = [
        row for row in rows if row["comparison_status"] == "matched_cross_check_only"
    ]
    if not matched:
        return [
            {
                "group": "all",
                "rows": str(len(rows)),
                "mean_delta_cad_m3": "",
                "median_delta_cad_m3": "",
                "median_ratio": "",
                "comparison_status": "reference_unavailable",
                "evidence": EVIDENCE,
                "cost_basis": COST_BASIS,
                "licence_tier": LICENCE_TIER,
                "policy_grade": POLICY_GRADE,
                "observed": OBSERVED,
                "calibration_claim": CALIBRATION_CLAIM,
            }
        ]
    groups: dict[tuple[str, str], list[dict[str, str]]] = {}
    for row in matched:
        groups.setdefault((row["zone"], row["system"]), []).append(row)
    result: list[dict[str, str]] = []
    for (zone, system), group in sorted(groups.items()):
        deltas = [float(row["delta_total_cad_m3"]) for row in group]
        ratios = [float(row["ratio_strict_to_reference"]) for row in group]
        result.append(
            {
                "group": f"{zone}|{system}",
                "rows": str(len(group)),
                "mean_delta_cad_m3": f"{fmean(deltas):+.2f}",
                "median_delta_cad_m3": f"{median(deltas):+.2f}",
                "median_ratio": f"{median(ratios):.3f}",
                "comparison_status": "cross_check_only_not_input",
                "evidence": EVIDENCE,
                "cost_basis": COST_BASIS,
                "licence_tier": LICENCE_TIER,
                "policy_grade": POLICY_GRADE,
                "observed": OBSERVED,
                "calibration_claim": CALIBRATION_CLAIM,
            }
        )
    return result


def _summary_statistics(estimates: list[dict[str, str]]) -> dict[str, Any]:
    by_region: dict[str, list[float]] = {region: [] for region in REGIONS}
    by_system: dict[str, list[float]] = {system: [] for system in SYSTEMS}
    for row in estimates:
        value = float(row["total_base_cad_m3"])
        by_region[row["region"]].append(value)
        by_system[row["system"]].append(value)
    return {
        "total_base_median_cad_m3": round(median([float(r["total_base_cad_m3"]) for r in estimates]), 2),
        "total_base_min_cad_m3": round(min(float(r["total_base_cad_m3"]) for r in estimates), 2),
        "total_base_max_cad_m3": round(max(float(r["total_base_cad_m3"]) for r in estimates), 2),
        "by_region": {
            region: {
                "median_total_base_cad_m3": round(median(values), 2),
                "min_total_base_cad_m3": round(min(values), 2),
                "max_total_base_cad_m3": round(max(values), 2),
            }
            for region, values in by_region.items()
        },
        "by_system": {
            system: {
                "median_total_base_cad_m3": round(median(values), 2),
                "min_total_base_cad_m3": round(min(values), 2),
                "max_total_base_cad_m3": round(max(values), 2),
            }
            for system, values in by_system.items()
        },
    }


def _readme_text(
    outputs: Mapping[str, int],
    stats: Mapping[str, Any],
    comparison_stats: Mapping[str, Any],
) -> str:
    source_lines = [
        "| Source key | Title | Licence | Tier |",
        "|---|---|---|---|",
    ]
    for source in SOURCE_REGISTER:
        source_lines.append(
            f"| `{source['source_key']}` | {source['title']} | {source['licence']} | "
            f"`{source['licence_tier']}` |"
        )

    return f"""# Strict-open BC logging and truck-haul cost model

**Status:** strict-open proxy model. Every estimate, comparison, sensitivity
and summary row carries `evidence=model_estimate`,
`cost_basis=strict_open_proxy`, `licence_tier=strict_open`,
`policy_grade=false`, `observed=false`. The source register also carries
`licence_tier=strict_open` at row level and the actual source licence in
`source_licence_tier`; that column is the proof of the strict-open claim. This
is **not** an observed contractor cost, a mill settlement, or a BC empirically
calibrated model.

## Licence rule

Only four input families are used:

1. Statistics Canada Open Licence aggregate tables (wages, diesel, CPI,
   machinery/equipment price index, trucking freight index, logging-industry
   aggregates).
2. Open Government Licence - Canada derived terrain and road aggregates
   (NRCan MRDEM-30 slope screen, National Road Network).
3. Open Government Licence - British Columbia derived forest-inventory
   aggregates (BC VRI 2025 area/species/THLB table).
4. CC BY 4.0 or public-domain methods: Sessions, Berry & Han (2021) and
   Miyata (1980). OSM ODbL was not needed and is not used.

Excluded and **not read**: IAM/CAM appraisal manuals, the TLA/ILA/NWLA/MNP
rate guide, FPInnovations/FERIC reports, BCWS rates, any Crown-copyright PDF,
and proprietary or restricted data/microdata. The only non-strict-open file
read is the existing public-reference model's output CSV, and that is used
solely for the cross-check table; it is not a model input.

## Equations

Machine rate per productive machine hour (PMH), following Miyata (1980) and
the utilization-adjusted life of Sessions, Berry & Han (2021):

```
D        = (P - S) / (life_years * annual_PMH)
AVI      = P - (P - S) * (life_years - 1) / (2 * life_years)
interest = (i + insurance_tax_storage) * AVI / annual_PMH
fuel     = litres_per_PMH * diesel_CAD_per_litre * (1 + lube_fraction)
repair   = repair_fraction * D
tires    = tire_fraction * D
labour   = wage_CAD_per_SMH * (1 + burden) / utilization
PMH_cost = (D + interest + fuel + repair + tires + labour) * (1 + overhead)
SMH_cost = PMH_cost * utilization
```

`modified` method: `life_years = economic_life_hours / (SMH_per_year * U)`.
`traditional` method: `life_years = economic_life_hours / SMH_per_year`.
The base case uses `modified`; the `traditional` method is retained only for
the Sessions et al. worked-example check.

Logging:

```
phase_cost = machine_rate_CAD_per_PMH / productivity_m3_per_PMH
logging    = sum(phase costs) * (1 + support) + mobilization / sale_volume + planning
```

Productivity is an explicit engineering cycle-time estimate:

```
felling    = piece * 3600 / (14 + 26 * piece) * 0.80
skidder    = min(7, 4 + 4 * piece) / (0.24 - 0.06 * min(piece,1)) * 0.85
processor  = piece * 3600 / (16 + 30 * piece) * 0.80
yarder     = min(6, 3.5 + 3 * piece) / (0.16 - 0.03 * min(piece,1)) * 0.75
loader     = 2.2 / 0.025 * 0.90
```

Ground slope factor `1/(1 + 0.018 * max(0, slope-10))`; cable slope factor
`1/(1 + 0.010 * max(0, slope-35))`. These are engineering proxies, not BC
cost elasticities.

Truck haul:

```
CYCLE = 1 km in-block loaded/empty + road km loaded/empty
        + delay hours + load/unload hours
haul  = (truck_PMH + trailer_PMH) * CYCLE / payload * (1 + haul_support)
```

## Explicit assumptions

| Item | Base assumption | Range |
|---|---|---|
| Capital price | 2017 replacement-price assumption x MEPI 2026/100 | -15% / +20% |
| Machine utilization | 65-85% by machine, region-adjusted | -10 / +5 percentage points |
| Machine life | 12,000-15,000 PMH | -15% / +20% |
| Diesel | StatCan Vancouver 12-month mean | 12-month min/max |
| Wage | midpoint of StatCan logging production-worker and BC forestry anchors | StatCan natural-resource production / BC forestry |
| Payload | 50 m3 per truck trip | 42-55 m3 |
| Road speed | 55 km/h loaded, 65 km/h empty | 45-60 / 55-70 km/h |
| Delay/load | 1.30 h delay + 0.50 h load/unload | 1.00-1.50 h / 0.40-0.60 h |
| Overhead/profit | 15% | 10-20% |

The capital price is a stated 2017 assumption escalated by the Statistics
Canada MEPI for logging/construction/mining machinery. It is not a dealer
quote. The 2,000 paid hours per year used to convert annual logging wages to
an hourly rate is the Miyata (1980) scheduled-time convention, not a BC payroll
observation.

The low and high columns stack favourable or unfavourable parameter draws
(capital, utilization, productivity, diesel, wage, overhead and haul cycle).
They are a **stress envelope**, not a statistical confidence interval and not a
probability statement.

## Coverage

- 7 BC forest zones: 1 Coast, 6 Interior.
- 4 slope classes: 10%, 25%, 40%, 55%; ground for 10/25, cable for 40/55.
- 3 piece sizes: 0.30, 0.60, 1.00 m3/tree.
- 3 sale volumes: 5,000, 20,000, 100,000 m3.
- 3 haul distances: 50, 100, 150 km.
- {outputs['estimates']:,} estimate rows.

Regional slope weights are computed from the OGL-Canada MRDEM-30 derived
slope screen and OGL-BC VRI forest area by ecoprovince. The >30% slope area is
split 90/10 between the steep and very-steep classes; that split is an
assumption because the open aggregate publishes only the >15% and >30%
thresholds.

Zone is an administrative label in this model. No strict-open BCTS-zone cost
or inventory crosswalk was identified, so the six Interior zones share the
same regional parameters and differ only by label; the meaningful cost split
is Coast versus Interior. The row-level grid still covers every zone label so
it can be joined to other administrative tables without inventing a crosswalk.

## Worked examples (recomputed in the build)

| Check | Expected CAD/PMH | Computed CAD/PMH |
|---|---:|---:|
{chr(10).join(f"| {row['check_id']} | {row['expected_cad_per_pmh']} | {row['computed_cad_per_pmh']} |" for row in worked_examples())}

The Sessions et al. examples reproduce 140.60 at 100% utilization, 269.20 for
the traditional method at 50%, and 192.60 for the modified method at 50%. The
Miyata Example 11 reproduces 35.61/PMH.

## Cost estimates

{json.dumps(stats, indent=2)}

## Comparison to the public-reference model

The existing public-reference model is used **only** as a cross-check. Its
rates and coefficients are not inputs to this model. {comparison_stats['statement']}

The comparison file records the delta and ratio for every overlapping
scenario. A positive delta means the strict-open model is higher. Differences
are expected because the reference model uses different machine rates,
productivity sources, and appraisal-manual coefficients.

## Is a strict-open BC price series still needed?

**Yes.** This model supplies only the cost side. The existing BC log-market
price reports are public-reference Crown-copyright material, not strict-open
data. Statistics Canada does not publish a BC delivered-log price series. A
strict-open net-value calculation therefore still needs an OGL-BC or CC-BY
released BC delivered-log price series (or a public-domain equivalent). Until
that exists, this model can support a strict-open **cost** estimate only, not a
strict-open net value or margin.

## Files

| File | Rows | Purpose |
|---|---:|---|
| `strict_estimates.csv` | {outputs['estimates']:,} | Zone x slope x piece x volume x distance low/base/high |
| `strict_region_slope.csv` | {outputs['region_slope']:,} | Region x slope representative costs and OGL slope weights |
| `strict_zone_summary.csv` | {outputs['zone_summary']:,} | Area-weighted slope expectation by zone |
| `strict_sensitivity.csv` | {outputs['sensitivity']:,} | One-at-a-time sensitivity cases |
| `strict_parameters.csv` | {outputs['parameters']:,} | Equations, assumptions, units, ranges, sources |
| `strict_sources.csv` | {outputs['sources']:,} | Source and licence register |
| `strict_statcan_observations.csv` | {outputs['observations']:,} | Frozen public statistics used by the build |
| `strict_worked_examples.csv` | {outputs['worked_examples']:,} | Recomputed Miyata/Sessions examples |
| `strict_comparison.csv` | {outputs['comparison']:,} | Row-level cross-check versus the public-reference model |
| `strict_comparison_summary.csv` | {outputs['comparison_summary']:,} | Cross-check summary by zone/system |
| `manifest.json` | - | Inputs, hashes, validation, limitations, non-claims |
| `summary.json` | - | Compact summary and strict-open price-series gap |

## Source register

{chr(10).join(source_lines)}

Full URLs, retrieval timestamps, licence URLs and the exact use of every
source are in `strict_sources.csv` and `manifest.json`.

## Validation

- {outputs['estimates']:,} estimate rows; no NaN, infinite or negative values.
- Every row satisfies `total_low <= total_base <= total_high`.
- All zones, regions, slope classes, piece classes, volume classes and
  distance classes are present.
- Every estimate row carries the five required labels and
  `bc_empirical_calibration=false`.
- The source register is scanned for forbidden restricted-source tokens.
- `manifest.json` records SHA-256 hashes for every derived input and output.
- The build fails closed on a missing input, a bad source licence tier, a
  non-finite or unordered cost, or a failed worked example.

## Non-claims and limitations

- Not an observed contractor cost, invoice, mill settlement, or profit.
- Not a BC empirical calibration; productivity and utilization are explicit
  engineering assumptions.
- Not policy grade; not a stumpage rate, AAC, tenure, or legal determination.
- No IAM/CAM, TLA/ILA/NWLA, FPInnovations, BCWS, Crown-copyright, proprietary
  or restricted input is used.
- The model covers tree-to-truck logging and general truck haul only. Road
  construction, bridges, silviculture, sorting, barge/rail and tenure costs are
  outside the boundary.
- Diesel is a retail pump price including taxes; this is a conservative fuel
  input.
"""


def build(out_dir: Path = DEFAULT_OUT_DIR) -> dict[str, Any]:
    """Build all strict-open artifacts and return the manifest payload."""
    validate_source_register()
    if out_dir.exists() and not out_dir.is_dir():
        raise StrictCostModelError(f"output path is not a directory: {out_dir}")

    scenarios = build_scenarios()
    estimates = [estimate_scenario(scenario) for scenario in scenarios]
    validation = validation_payload(estimates)
    parameters = parameter_rows()
    sources = source_rows()
    observations = observations_payload()
    examples = worked_examples()
    sensitivity = sensitivity_rows()
    region_slope = region_slope_summary()
    zones = zone_summary()
    context = region_slope_context()
    comparison = comparison_rows(estimates)
    comparison_stats = comparison_summary(comparison)
    stats = _summary_statistics(estimates)

    matched = [
        row for row in comparison if row["comparison_status"] == "matched_cross_check_only"
    ]
    if matched:
        deltas = [float(row["delta_total_cad_m3"]) for row in matched]
        ratios = [float(row["ratio_strict_to_reference"]) for row in matched]
        comparison_public = {
            "reference_source_key": "bc_open_cost_model_public_reference",
            "reference_path": "data/derived/national/harvest_basis/bc_open_cost_model/bc_open_cost_estimates.csv",
            "reference_licence_tier": "public_reference_copyrighted",
            "reference_used_as_model_input": False,
            "comparison_role": "cross_check_only_not_input",
            "matched_rows": len(matched),
            "unmatched_rows": len(comparison) - len(matched),
            "mean_delta_total_cad_m3": round(fmean(deltas), 2),
            "median_delta_total_cad_m3": round(median(deltas), 2),
            "median_ratio_strict_to_reference": round(median(ratios), 3),
            "statement": (
                f"{len(matched)} overlapping scenarios were matched; the median "
                f"strict/reference ratio is {median(ratios):.3f} and the median "
                f"delta is {median(deltas):+.2f} CAD/m3. This is a cross-check, "
                "not calibration and not an input."
            ),
        }
    else:
        comparison_public = {
            "reference_source_key": "bc_open_cost_model_public_reference",
            "reference_path": "data/derived/national/harvest_basis/bc_open_cost_model/bc_open_cost_estimates.csv",
            "reference_licence_tier": "public_reference_copyrighted",
            "reference_used_as_model_input": False,
            "comparison_role": "cross_check_only_not_input",
            "matched_rows": 0,
            "unmatched_rows": len(comparison),
            "mean_delta_total_cad_m3": None,
            "median_delta_total_cad_m3": None,
            "median_ratio_strict_to_reference": None,
            "statement": "The public-reference output was not available; the comparison table records reference_unavailable.",
        }
        # JSON null is preferred to a blank string in summary.json.
        comparison_stats = comparison_summary(comparison)

    outputs = {
        "estimates": len(estimates),
        "region_slope": len(region_slope),
        "zone_summary": len(zones),
        "sensitivity": len(sensitivity),
        "parameters": len(parameters),
        "sources": len(sources),
        "observations": len(observations),
        "worked_examples": len(examples),
        "comparison": len(comparison),
        "comparison_summary": len(comparison_stats),
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "estimates": out_dir / "strict_estimates.csv",
        "region_slope": out_dir / "strict_region_slope.csv",
        "zone_summary": out_dir / "strict_zone_summary.csv",
        "sensitivity": out_dir / "strict_sensitivity.csv",
        "parameters": out_dir / "strict_parameters.csv",
        "sources": out_dir / "strict_sources.csv",
        "observations": out_dir / "strict_statcan_observations.csv",
        "worked_examples": out_dir / "strict_worked_examples.csv",
        "comparison": out_dir / "strict_comparison.csv",
        "comparison_summary": out_dir / "strict_comparison_summary.csv",
        "readme": out_dir / "README.md",
        "manifest": out_dir / "manifest.json",
        "summary": out_dir / "summary.json",
    }

    _write_csv(paths["estimates"], estimates, tuple(estimates[0].keys()))
    _write_csv(paths["region_slope"], region_slope, tuple(region_slope[0].keys()))
    _write_csv(paths["zone_summary"], zones, tuple(zones[0].keys()))
    _write_csv(paths["sensitivity"], sensitivity, tuple(sensitivity[0].keys()))
    _write_csv(paths["parameters"], parameters, tuple(parameters[0].keys()))
    _write_csv(paths["sources"], sources, tuple(sources[0].keys()))
    _write_csv(paths["observations"], observations, tuple(observations[0].keys()))
    _write_csv(paths["worked_examples"], examples, tuple(examples[0].keys()))
    _write_csv(
        paths["comparison"],
        comparison,
        COMPARISON_COLUMNS,
    )
    _write_csv(
        paths["comparison_summary"],
        comparison_stats,
        tuple(comparison_stats[0].keys()),
    )

    paths["readme"].write_text(
        _readme_text(outputs, stats, comparison_public), encoding="utf-8"
    )

    summary_payload: dict[str, Any] = {
        "model_id": MODEL_ID,
        "artifact_version": MODEL_VERSION,
        "generated_utc": generated_utc(),
        "observation_type": EVIDENCE,
        "cost_basis": COST_BASIS,
        "licence_tier": LICENCE_TIER,
        "policy_grade": False,
        "observed": False,
        "calibration_claim": CALIBRATION_CLAIM,
        "bc_empirical_calibration": False,
        "strict_open_only": True,
        "restricted_inputs_used": [],
        "excluded_source_families": list(EXCLUDED_SOURCE_FAMILIES),
        "scenario_grid": {
            "zones": len(ZONES),
            "regions": list(REGIONS),
            "slope_scenarios": len(SLOPE_SCENARIOS),
            "piece_scenarios": len(PIECE_SCENARIOS),
            "volume_scenarios": len(VOLUME_SCENARIOS),
            "distance_scenarios": len(DISTANCE_SCENARIOS),
            "systems": list(SYSTEMS),
            "estimates": len(estimates),
        },
        "statistics": stats,
        "region_slope_context": {
            key: {k: round(v, 6) for k, v in value.items()}
            for key, value in context.items()
        },
        "comparison_to_public_reference": comparison_public,
        "worked_examples": examples,
        "derived_constants": derived_constants_payload(),
        "strict_open_price_series_needed": True,
        "strict_open_price_series_status": "gap",
        "strict_open_price_series_statement": (
            "Costs are strict-open, but a strict-open BC delivered-log price "
            "series does not exist in the sources used here. The existing BC "
            "log-market reports are public-reference Crown copyright. A "
            "strict-open net value therefore still requires an OGL-BC or CC-BY "
            "price series."
        ),
        "validation": validation,
        "outputs": outputs,
        "limitations": [
            "Engineering proxy, not an observed contractor cost.",
            "No BC empirical calibration of productivity, utilization or capital price.",
            "2017 capital prices are assumptions escalated with StatCan MEPI.",
            "Wage base is a midpoint of two broad StatCan wage anchors, not a BC logging wage survey.",
            "Diesel is a retail pump price including taxes.",
            "Zone labels are administrative; the six Interior zones share regional parameters because no strict-open BCTS-zone crosswalk was identified.",
            "Helicopter logging is outside the model boundary.",
            "Road, bridge, silviculture, sorting, barge/rail and tenure costs are excluded.",
            "Low/high columns stack favourable or unfavourable parameter draws; they are a stress envelope, not a confidence interval.",
            "The comparison model is public-reference copyrighted and is used only as a cross-check.",
        ],
        "non_claims": [
            "Not observed cost.",
            "Not a mill settlement or profit.",
            "Not a BC empirical calibration.",
            "Not policy grade.",
            "Not a stumpage rate, AAC, tenure, or legal determination.",
            "Not a strict-open net value.",
        ],
    }
    paths["summary"].write_text(
        json.dumps(summary_payload, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    output_hashes = {
        path.name: _sha256(path)
        for key, path in paths.items()
        if key not in {"manifest"}
    }
    manifest: dict[str, Any] = {
        "model_id": MODEL_ID,
        "artifact_version": MODEL_VERSION,
        "generated_utc": generated_utc(),
        "description": (
            "Strict-open BC tree-to-truck logging and truck-haul cost proxy "
            "using StatCan Open Licence tables, OGL-BC/OGL-Canada derived "
            "inputs, and CC-BY/public-domain machine-rate methods."
        ),
        "observation_type": EVIDENCE,
        "cost_basis": COST_BASIS,
        "licence_tier": LICENCE_TIER,
        "policy_grade": False,
        "observed": False,
        "calibration_claim": CALIBRATION_CLAIM,
        "strict_open_only": True,
        "restricted_inputs_used": [],
        "excluded_source_families": list(EXCLUDED_SOURCE_FAMILIES),
        "sources": sources,
        "source_hash_checks": input_hashes(),
        "statcan_observations": observations,
        "derived_constants": derived_constants_payload(),
        "parameters": parameters,
        "scenario_grid": summary_payload["scenario_grid"],
        "worked_examples": examples,
        "validation": validation,
        "comparison": comparison_public,
        "outputs": outputs,
        "output_sha256": output_hashes,
        "limitations": summary_payload["limitations"],
        "non_claims": summary_payload["non_claims"],
        "strict_open_price_series_needed": True,
        "strict_open_price_series_status": "gap",
    }
    # Fail closed on any non-finite JSON value before writing.
    json.dumps(manifest, allow_nan=False)
    paths["manifest"].write_text(
        json.dumps(manifest, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=DEFAULT_OUT_DIR,
        help="Directory for the CSV, README, manifest and summary artifacts.",
    )
    args = parser.parse_args(argv)
    manifest = build(args.out_dir)
    print(
        "wrote {estimates} estimate rows, {parameters} parameters, {sources} sources to {out}".format(
            estimates=manifest["outputs"]["estimates"],
            parameters=manifest["outputs"]["parameters"],
            sources=manifest["outputs"]["sources"],
            out=args.out_dir,
        )
    )
    return 0
