"""Diagnostic reproduction of the Gauthier et al. 2015 yield method."""

from __future__ import annotations

import argparse
import math
from pathlib import Path

from fresh_fibre.schema import number, read_rows, require_columns, write_rows


DEFAULT_OUTPUT_DIR = Path("metadata/ffcm-archive/prototype-inputs/gauthier-diagnostic")
DEFAULT_COEFFICIENTS_PATH = DEFAULT_OUTPUT_DIR / "gauthier_2015_coefficients.csv"
DEFAULT_INPUT_PATH = DEFAULT_OUTPUT_DIR / "gauthier_diagnostic_input_cells.csv"
DEFAULT_CURVE_POINTS_PATH = DEFAULT_OUTPUT_DIR / "gauthier_diagnostic_curve_points.csv"
DEFAULT_MATURITY_PATH = DEFAULT_OUTPUT_DIR / "gauthier_diagnostic_maturity.csv"
DEFAULT_YIELD_RULES_PATH = DEFAULT_OUTPUT_DIR / "gauthier_diagnostic_yield_rules_candidate.csv"

VALUE_POLICY = "synthetic_method_reproduction_not_calibrated"

COEFFICIENT_COLUMNS = [
    "source_id",
    "species_group",
    "beta0",
    "beta1",
    "beta2",
    "beta3",
    "beta4",
    "beta5",
    "duan_correction",
    "r2",
    "notes",
]

INPUT_COLUMNS = [
    "inventory_cell_id",
    "diagnostic_region",
    "spatial_unit_id",
    "supply_node_id",
    "representative_area_ha",
    "measured_age_years",
    "measured_volume_m3_per_ha",
    "conifer_fraction",
    "deciduous_fraction",
    "mean_annual_temp_c",
    "mean_annual_precip_mm",
    "vegetated_fraction",
    "source_policy",
    "notes",
]

CURVE_COLUMNS = [
    "inventory_cell_id",
    "age_years",
    "conifer_volume_m3_per_ha",
    "deciduous_volume_m3_per_ha",
    "composite_volume_m3_per_ha",
    "scaled_composite_volume_m3_per_ha",
    "scaling_factor",
    "value_policy",
]

MATURITY_COLUMNS = [
    "inventory_cell_id",
    "diagnostic_region",
    "spatial_unit_id",
    "supply_node_id",
    "representative_area_ha",
    "measured_age_years",
    "measured_volume_m3_per_ha",
    "modeled_volume_at_measured_age_m3_per_ha",
    "scaling_factor",
    "maturity_threshold_m3_per_ha",
    "maturity_age_years",
    "productive_flag",
    "theoretical_harvest_fraction_per_year",
    "period_available_quantity_per_ha",
    "quantity_unit",
    "value_policy",
    "notes",
]

YIELD_RULE_COLUMNS = [
    "yield_rule_id",
    "diagnostic_region",
    "commodity_id",
    "quantity_unit",
    "period_available_quantity_per_ha",
    "source_method",
    "value_policy",
    "replacement_status",
    "notes",
]

COEFFICIENT_ROWS = [
    {
        "source_id": "gauthier_2015_table_1",
        "species_group": "coniferous",
        "beta0": 5.5443,
        "beta1": 0.2063,
        "beta2": -0.0005,
        "beta3": -142.365,
        "beta4": -8.0154,
        "beta5": 0.0771,
        "duan_correction": 1.0745,
        "r2": 0.415,
        "notes": "Published Table 1 coefficients for gross merchantable volume.",
    },
    {
        "source_id": "gauthier_2015_table_1",
        "species_group": "deciduous",
        "beta0": 4.756,
        "beta1": 0.0,
        "beta2": 0.001,
        "beta3": -33.8352,
        "beta4": -1.8747,
        "beta5": -0.02,
        "duan_correction": 1.0117,
        "r2": 0.3741,
        "notes": "Published Table 1 coefficients; beta1 was not retained for deciduous.",
    },
]

SYNTHETIC_INPUT_ROWS = [
    {
        "inventory_cell_id": "gauthier_bc_coast_wet_forest_001",
        "diagnostic_region": "coast_wet_forest",
        "spatial_unit_id": "bc_fmu_coast_01",
        "supply_node_id": "supply_coast_01",
        "representative_area_ha": 35000,
        "measured_age_years": 95,
        "measured_volume_m3_per_ha": 175,
        "conifer_fraction": 0.85,
        "deciduous_fraction": 0.15,
        "mean_annual_temp_c": 6.5,
        "mean_annual_precip_mm": 1200,
        "vegetated_fraction": 0.92,
        "notes": "Synthetic coast proxy shaped for Gauthier method reproduction.",
    },
    {
        "inventory_cell_id": "gauthier_bc_coast_second_growth_002",
        "diagnostic_region": "coast_second_growth",
        "spatial_unit_id": "bc_fmu_coast_01",
        "supply_node_id": "supply_coast_01",
        "representative_area_ha": 28000,
        "measured_age_years": 55,
        "measured_volume_m3_per_ha": 120,
        "conifer_fraction": 0.75,
        "deciduous_fraction": 0.25,
        "mean_annual_temp_c": 6.2,
        "mean_annual_precip_mm": 1100,
        "vegetated_fraction": 0.9,
        "notes": "Synthetic second-growth proxy shaped for Gauthier method reproduction.",
    },
    {
        "inventory_cell_id": "gauthier_bc_int_dry_forest_001",
        "diagnostic_region": "interior_dry_forest",
        "spatial_unit_id": "bc_fmu_interior_01",
        "supply_node_id": "supply_interior_01",
        "representative_area_ha": 45000,
        "measured_age_years": 85,
        "measured_volume_m3_per_ha": 110,
        "conifer_fraction": 0.88,
        "deciduous_fraction": 0.12,
        "mean_annual_temp_c": 3.8,
        "mean_annual_precip_mm": 650,
        "vegetated_fraction": 0.86,
        "notes": "Synthetic interior dry proxy shaped for Gauthier method reproduction.",
    },
    {
        "inventory_cell_id": "gauthier_bc_int_cold_forest_002",
        "diagnostic_region": "interior_cold_forest",
        "spatial_unit_id": "bc_fmu_interior_01",
        "supply_node_id": "supply_interior_01",
        "representative_area_ha": 32000,
        "measured_age_years": 110,
        "measured_volume_m3_per_ha": 135,
        "conifer_fraction": 0.92,
        "deciduous_fraction": 0.08,
        "mean_annual_temp_c": 2.5,
        "mean_annual_precip_mm": 780,
        "vegetated_fraction": 0.88,
        "notes": "Synthetic older interior proxy shaped for Gauthier method reproduction.",
    },
    {
        "inventory_cell_id": "gauthier_bc_int_salvage_001",
        "diagnostic_region": "interior_disturbance_mix",
        "spatial_unit_id": "bc_fmu_interior_02",
        "supply_node_id": "supply_interior_02",
        "representative_area_ha": 26000,
        "measured_age_years": 130,
        "measured_volume_m3_per_ha": 85,
        "conifer_fraction": 0.65,
        "deciduous_fraction": 0.35,
        "mean_annual_temp_c": 3.0,
        "mean_annual_precip_mm": 620,
        "vegetated_fraction": 0.72,
        "notes": "Synthetic disturbance-mix proxy shaped for Gauthier method reproduction.",
    },
    {
        "inventory_cell_id": "gauthier_bc_int_residue_001",
        "diagnostic_region": "interior_residue_candidate",
        "spatial_unit_id": "bc_fmu_interior_02",
        "supply_node_id": "supply_residue_02",
        "representative_area_ha": 12000,
        "measured_age_years": 70,
        "measured_volume_m3_per_ha": 60,
        "conifer_fraction": 0.55,
        "deciduous_fraction": 0.45,
        "mean_annual_temp_c": 3.2,
        "mean_annual_precip_mm": 580,
        "vegetated_fraction": 0.68,
        "notes": "Synthetic residue proxy shaped for Gauthier method reproduction.",
    },
]


def format_number(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".")


def coefficient_rows() -> list[dict[str, object]]:
    return COEFFICIENT_ROWS


def input_rows() -> list[dict[str, object]]:
    return [{**row, "source_policy": VALUE_POLICY} for row in SYNTHETIC_INPUT_ROWS]


def volume_at_age(coefficients: dict[str, str], age: float, temp_c: float, precip_mm: float) -> float:
    if age <= 0:
        raise ValueError(f"Age must be positive for Gauthier yield evaluation: {age}")
    beta0 = number(coefficients["beta0"])
    beta1 = number(coefficients["beta1"])
    beta2 = number(coefficients["beta2"])
    beta3 = number(coefficients["beta3"])
    beta4 = number(coefficients["beta4"])
    beta5 = number(coefficients["beta5"])
    duan = number(coefficients["duan_correction"])
    log_volume = beta0 + beta1 * temp_c + beta2 * precip_mm
    log_volume += (beta3 + beta4 * temp_c + beta5 * precip_mm) / age
    return math.exp(log_volume) * duan


def composite_volume(
    coefficients_by_group: dict[str, dict[str, str]],
    age: float,
    temp_c: float,
    precip_mm: float,
    conifer_fraction: float,
    deciduous_fraction: float,
) -> tuple[float, float, float]:
    conifer = volume_at_age(coefficients_by_group["coniferous"], age, temp_c, precip_mm)
    deciduous = volume_at_age(coefficients_by_group["deciduous"], age, temp_c, precip_mm)
    composite = conifer_fraction * conifer + deciduous_fraction * deciduous
    return conifer, deciduous, composite


def scaling_factor_for_cell(
    cell: dict[str, str],
    coefficients_by_group: dict[str, dict[str, str]],
) -> tuple[float, float]:
    _, _, modeled = composite_volume(
        coefficients_by_group,
        number(cell["measured_age_years"]),
        number(cell["mean_annual_temp_c"]),
        number(cell["mean_annual_precip_mm"]),
        number(cell["conifer_fraction"]),
        number(cell["deciduous_fraction"]),
    )
    if modeled <= 0:
        raise ValueError(f"Modeled volume is not positive for {cell['inventory_cell_id']}: {modeled}")
    return modeled, number(cell["measured_volume_m3_per_ha"]) / modeled


def maturity_age(curve_rows: list[dict[str, object]], threshold: float = 100.0) -> int | None:
    for row in curve_rows:
        if number(row["scaled_composite_volume_m3_per_ha"]) >= threshold:
            return int(row["age_years"])
    return None


def build_outputs(
    coefficient_path: Path = DEFAULT_COEFFICIENTS_PATH,
    input_path: Path = DEFAULT_INPUT_PATH,
    age_min: int = 1,
    age_max: int = 200,
    maturity_threshold: float = 100.0,
) -> tuple[list[dict[str, object]], list[dict[str, object]], list[dict[str, object]]]:
    coefficient_data = read_rows(coefficient_path)
    input_data = read_rows(input_path)
    require_columns(coefficient_path, coefficient_data, set(COEFFICIENT_COLUMNS))
    require_columns(input_path, input_data, set(INPUT_COLUMNS))
    validate_coefficients(coefficient_data)
    validate_inputs(input_data)
    coefficients_by_group = {row["species_group"]: row for row in coefficient_data}

    curve_rows: list[dict[str, object]] = []
    maturity_rows: list[dict[str, object]] = []
    yield_rule_rows: list[dict[str, object]] = []
    for cell in input_data:
        modeled_at_age, scale = scaling_factor_for_cell(cell, coefficients_by_group)
        cell_curve_rows: list[dict[str, object]] = []
        for age in range(age_min, age_max + 1):
            conifer, deciduous, composite = composite_volume(
                coefficients_by_group,
                float(age),
                number(cell["mean_annual_temp_c"]),
                number(cell["mean_annual_precip_mm"]),
                number(cell["conifer_fraction"]),
                number(cell["deciduous_fraction"]),
            )
            row = {
                "inventory_cell_id": cell["inventory_cell_id"],
                "age_years": age,
                "conifer_volume_m3_per_ha": format_number(conifer),
                "deciduous_volume_m3_per_ha": format_number(deciduous),
                "composite_volume_m3_per_ha": format_number(composite),
                "scaled_composite_volume_m3_per_ha": format_number(composite * scale),
                "scaling_factor": format_number(scale),
                "value_policy": VALUE_POLICY,
            }
            curve_rows.append(row)
            cell_curve_rows.append(row)
        maturity = maturity_age(cell_curve_rows, maturity_threshold)
        productive = maturity is not None and maturity <= age_max
        harvest_fraction = 0.0 if not productive else 1.0 / float(maturity)
        period_available = 0.0 if not productive else maturity_threshold * harvest_fraction
        maturity_rows.append(
            {
                "inventory_cell_id": cell["inventory_cell_id"],
                "diagnostic_region": cell["diagnostic_region"],
                "spatial_unit_id": cell["spatial_unit_id"],
                "supply_node_id": cell["supply_node_id"],
                "representative_area_ha": cell["representative_area_ha"],
                "measured_age_years": cell["measured_age_years"],
                "measured_volume_m3_per_ha": cell["measured_volume_m3_per_ha"],
                "modeled_volume_at_measured_age_m3_per_ha": format_number(modeled_at_age),
                "scaling_factor": format_number(scale),
                "maturity_threshold_m3_per_ha": format_number(maturity_threshold),
                "maturity_age_years": maturity or "",
                "productive_flag": "true" if productive else "false",
                "theoretical_harvest_fraction_per_year": format_number(harvest_fraction),
                "period_available_quantity_per_ha": format_number(period_available),
                "quantity_unit": "m3",
                "value_policy": VALUE_POLICY,
                "notes": "Synthetic Gauthier method reproduction; not calibrated yield.",
            }
        )
        yield_rule_rows.append(
            {
                "yield_rule_id": f"gauthier_diag_yield_{cell['diagnostic_region']}",
                "diagnostic_region": cell["diagnostic_region"],
                "commodity_id": "log",
                "quantity_unit": "m3",
                "period_available_quantity_per_ha": format_number(period_available),
                "source_method": "gauthier_2015_method_reproduction",
                "value_policy": VALUE_POLICY,
                "replacement_status": "candidate_not_accepted",
                "notes": (
                    "Candidate rule derived from maturity threshold divided by synthetic "
                    "Gauthier maturity age; do not replace diagnostic_yield_rules.csv yet."
                ),
            }
        )
    validate_outputs(curve_rows, maturity_rows, yield_rule_rows)
    return curve_rows, maturity_rows, yield_rule_rows


def validate_coefficients(rows: list[dict[str, str]]) -> None:
    groups = {row["species_group"] for row in rows}
    if groups != {"coniferous", "deciduous"}:
        raise ValueError(f"Expected coniferous and deciduous coefficient rows, found {groups}")
    for row in rows:
        for field in ("beta0", "beta1", "beta2", "beta3", "beta4", "beta5", "duan_correction", "r2"):
            number(row[field])


def validate_inputs(rows: list[dict[str, str]]) -> None:
    ids: set[str] = set()
    for row in rows:
        cell_id = row["inventory_cell_id"]
        if cell_id in ids:
            raise ValueError(f"Duplicate Gauthier diagnostic input cell: {cell_id}")
        ids.add(cell_id)
        total_fraction = number(row["conifer_fraction"]) + number(row["deciduous_fraction"])
        if abs(total_fraction - 1.0) > 1e-6:
            raise ValueError(f"Species fractions do not sum to one for {cell_id}: {total_fraction}")
        for field in (
            "representative_area_ha",
            "measured_age_years",
            "measured_volume_m3_per_ha",
            "vegetated_fraction",
        ):
            if number(row[field]) <= 0:
                raise ValueError(f"{field} must be positive for {cell_id}: {row[field]}")
        if row["source_policy"] != VALUE_POLICY:
            raise ValueError(f"Unexpected source policy for {cell_id}: {row['source_policy']}")


def validate_outputs(
    curve_rows: list[dict[str, object]],
    maturity_rows: list[dict[str, object]],
    yield_rule_rows: list[dict[str, object]],
) -> None:
    if not curve_rows:
        raise ValueError("No Gauthier diagnostic curve rows generated")
    if len(maturity_rows) != len(yield_rule_rows):
        raise ValueError("Maturity and yield-rule row counts differ")
    if not any(row["productive_flag"] == "true" for row in maturity_rows):
        raise ValueError("Expected at least one productive Gauthier diagnostic cell")
    for row in curve_rows:
        if number(row["scaled_composite_volume_m3_per_ha"]) < 0:
            raise ValueError(f"Negative scaled volume for {row['inventory_cell_id']}")
    for row in maturity_rows:
        if row["productive_flag"] == "true" and number(row["maturity_age_years"]) > 200:
            raise ValueError(f"Accepted maturity age exceeds 200 years: {row}")
        if row["value_policy"] != VALUE_POLICY:
            raise ValueError(f"Unexpected maturity value policy: {row}")


def write_readme(output_dir: Path) -> None:
    text = """# Gauthier 2015 Diagnostic Yield Fixture

This directory contains a lightweight diagnostic reproduction of the yield
method described in Gauthier et al. (2015). It does not contain raw NFI
reference pixels, NFI/Beaudoin rasters, SCANFI rasters, private correspondence,
or calibrated production yield curves.

The fixture encodes the published Table 1 coefficients for coniferous and
deciduous gross merchantable volume equations, evaluates them against synthetic
diagnostic input cells, locally scales composite curves to synthetic observed
volume at observed age, and estimates the age at which each cell reaches
`100 m3/ha`.

Outputs are method-reproduction artifacts only. The generated candidate yield
rules must not replace `diagnostic_yield_rules.csv` until required NFI/SCANFI
inputs, reference-pixel access, and validation policy are accepted.
"""
    (output_dir / "README.md").write_text(text, encoding="utf-8")


def build_gauthier_diagnostic_fixture(output_dir: Path = DEFAULT_OUTPUT_DIR) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    coefficient_path = output_dir / DEFAULT_COEFFICIENTS_PATH.name
    input_path = output_dir / DEFAULT_INPUT_PATH.name
    curve_path = output_dir / DEFAULT_CURVE_POINTS_PATH.name
    maturity_path = output_dir / DEFAULT_MATURITY_PATH.name
    yield_rules_path = output_dir / DEFAULT_YIELD_RULES_PATH.name

    write_rows(coefficient_path, COEFFICIENT_COLUMNS, coefficient_rows())
    write_rows(input_path, INPUT_COLUMNS, input_rows())
    curve_rows, maturity_rows, yield_rule_rows = build_outputs(coefficient_path, input_path)
    write_rows(curve_path, CURVE_COLUMNS, curve_rows)
    write_rows(maturity_path, MATURITY_COLUMNS, maturity_rows)
    write_rows(yield_rules_path, YIELD_RULE_COLUMNS, yield_rule_rows)
    write_readme(output_dir)
    return {
        "output_dir": str(output_dir),
        "coefficient_rows": len(COEFFICIENT_ROWS),
        "input_rows": len(SYNTHETIC_INPUT_ROWS),
        "curve_rows": len(curve_rows),
        "maturity_rows": len(maturity_rows),
        "yield_rule_rows": len(yield_rule_rows),
        "productive_rows": sum(1 for row in maturity_rows if row["productive_flag"] == "true"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()
    result = build_gauthier_diagnostic_fixture(args.output_dir)
    print(
        f"wrote Gauthier diagnostic yield fixture to {result['output_dir']}: "
        f"{result['curve_rows']} curve rows, "
        f"{result['maturity_rows']} maturity rows, "
        f"{result['productive_rows']} productive rows"
    )
