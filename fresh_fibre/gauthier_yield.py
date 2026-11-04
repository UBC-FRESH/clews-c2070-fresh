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

REAL_COEFFICIENT_ROWS: list[dict[str, object]] = [
    # --- QC species (Pothier & Savard 1998 / Auger 2017 seed band) -------------
    # Seeds target ~age 80 mid-rotation proxy values; Phase 4 calibration
    # replaces these with direct Pothier & Savard RN98-3054 lookups.
    {
        "species_code": "SAB",
        "species_group": "QC_softwood_balsam_fir",
        "region": "QC",
        "vol80_proxy_m3_per_ha": 110,
        "source_method": "pothier_savard_1998_rn98_3054_table_proxy",
        "value_policy": "real_pothier_savard_auger_seeded_vol80_proxy",
        "notes": "Balsam fir QC mid-rotation; PMS RN98-3054.",
    },
    {
        "species_code": "EPN",
        "species_group": "QC_softwood_black_spruce",
        "region": "QC",
        "vol80_proxy_m3_per_ha": 120,
        "source_method": "pothier_savard_1998_rn98_3054_table_proxy",
        "value_policy": "real_pothier_savard_auger_seeded_vol80_proxy",
        "notes": "Black spruce; PMS RN98-3054 (38.1 % QC merchantable share).",
    },
    {
        "species_code": "EPB",
        "species_group": "QC_softwood_white_spruce",
        "region": "QC",
        "vol80_proxy_m3_per_ha": 95,
        "source_method": "pothier_savard_1998_rn98_3054_table_proxy",
        "value_policy": "real_pothier_savard_auger_seeded_vol80_proxy",
        "notes": "White spruce; PMS RN98-3054; Memoire 118 planted supplement.",
    },
    {
        "species_code": "BOP",
        "species_group": "QC_hardwood_paper_birch",
        "region": "QC",
        "vol80_proxy_m3_per_ha": 110,
        "source_method": "pothier_savard_1998_rn98_3054_table_proxy",
        "value_policy": "real_pothier_savard_auger_seeded_vol80_proxy",
        "notes": "Paper birch; PMS RN98-3054.",
    },
    {
        "species_code": "BOJ",
        "species_group": "QC_hardwood_yellow_birch",
        "region": "QC",
        "vol80_proxy_m3_per_ha": 115,
        "source_method": "pothier_savard_1998_rn98_3054_table_proxy",
        "value_policy": "real_pothier_savard_auger_seeded_vol80_proxy",
        "notes": "Yellow birch; PMS RN98-3054.",
    },
    {
        "species_code": "PET",
        "species_group": "QC_hardwood_trembling_aspen",
        "region": "QC",
        "vol80_proxy_m3_per_ha": 110,
        "source_method": "pothier_savard_1998_rn98_3054_table_proxy",
        "value_policy": "real_pothier_savard_auger_seeded_vol80_proxy",
        "notes": "Trembling aspen; PMS RN98-3054.",
    },
    {
        "species_code": "ERS",
        "species_group": "QC_hardwood_sugar_maple",
        "region": "QC",
        "vol80_proxy_m3_per_ha": 120,
        "source_method": "pothier_savard_1998_rn98_3054_ontario_conversion",
        "value_policy": "real_pothier_savard_auger_seeded_vol80_proxy",
        "notes": "Sugar maple; PMS RN98-3054 has no ERS row; ON-table conversion proxy.",
    },
    {
        "species_code": "ERR",
        "species_group": "QC_hardwood_red_maple",
        "region": "QC",
        "vol80_proxy_m3_per_ha": 110,
        "source_method": "pothier_savard_1998_rn98_3054_ontario_conversion",
        "value_policy": "real_pothier_savard_auger_seeded_vol80_proxy",
        "notes": "Red maple; ON-table conversion proxy.",
    },
    {
        "species_code": "THO",
        "species_group": "QC_softwood_eastern_white_cedar",
        "region": "QC",
        "vol80_proxy_m3_per_ha": 85,
        "source_method": "pothier_savard_1998_rn98_3054_table_proxy",
        "value_policy": "real_pothier_savard_auger_seeded_vol80_proxy",
        "notes": "Eastern white cedar; PMS RN98-3054.",
    },
    {
        "species_code": "PIG",
        "species_group": "QC_softwood_jack_pine",
        "region": "QC",
        "vol80_proxy_m3_per_ha": 100,
        "source_method": "pothier_savard_1998_rn98_3054_table_proxy",
        "value_policy": "real_pothier_savard_auger_seeded_vol80_proxy",
        "notes": "Jack pine; PMS RN98-3054.",
    },
    {
        "species_code": "MEH",
        "species_group": "QC_softwood_tamarack_ari_subgroup",
        "region": "QC",
        "vol80_proxy_m3_per_ha": 95,
        "source_method": "pothier_savard_1998_rn98_3054_ari_subgroup_proxy",
        "value_policy": "real_pothier_savard_auger_seeded_vol80_proxy",
        "notes": "Tamarack; PMS RN98-3054 ARi softwood subgroup proxy.",
    },
    # --- BC species (BCMFR / Philip / TIPSY / VDYP seed band) -----------------
    # Seeds target ~age 80 mid-rotation proxy values for BC interior BEC
    # groups (SBS, IDF, ICH); Phase 4 calibration replaces these with direct
    # BCMFR / TIPSY lookups.
    {
        "species_code": "FDC",
        "species_group": "BC_coniferous_doug_fir",
        "region": "BC_coast",
        "vol80_proxy_m3_per_ha": 145,
        "source_method": "bcmfr_philip_tipsy_coastal_table_proxy",
        "value_policy": "real_bcmfr_philip_tipsy_seeded_vol80_proxy",
        "notes": "Douglas-fir coastal BEC proxy at rotation.",
    },
    {
        "species_code": "FDC",
        "species_group": "BC_coniferous_doug_fir",
        "region": "BC_interior",
        "vol80_proxy_m3_per_ha": 105,
        "source_method": "bcmfr_philip_tipsy_interior_idf_table_proxy",
        "value_policy": "real_bcmfr_philip_tipsy_seeded_vol80_proxy",
        "notes": "Douglas-fir interior BEC (IDF dk typical SI 22).",
    },
    {
        "species_code": "AT",
        "species_group": "BC_deciduous_aspen",
        "region": "BC",
        "vol80_proxy_m3_per_ha": 110,
        "source_method": "bcmfr_philip_nigh_sibec_table_proxy",
        "value_policy": "real_bcmfr_philip_tipsy_seeded_vol80_proxy",
        "notes": "Trembling aspen; Nigh interior yield extension proxy.",
    },
    {
        "species_code": "BL",
        "species_group": "BC_coniferous_subalpine_fir",
        "region": "BC",
        "vol80_proxy_m3_per_ha": 95,
        "source_method": "bcmfr_philip_essf_table_proxy",
        "value_policy": "real_bcmfr_philip_tipsy_seeded_vol80_proxy",
        "notes": "Subalpine fir; ESSF mp CW analog proxy.",
    },
    {
        "species_code": "Pl",
        "species_group": "BC_coniferous_lodgepole_pine",
        "region": "BC_interior",
        "vol80_proxy_m3_per_ha": 90,
        "source_method": "bcmfr_philip_tipsy_sbs_mk_table_proxy",
        "value_policy": "real_bcmfr_philip_tipsy_seeded_vol80_proxy",
        "notes": "Lodgepole pine; SBS mk typical proxy.",
    },
    {
        "species_code": "Sx",
        "species_group": "BC_coniferous_spruce_hybrid",
        "region": "BC_interior",
        "vol80_proxy_m3_per_ha": 115,
        "source_method": "bcmfr_philip_tipsy_ich_dk_table_proxy",
        "value_policy": "real_bcmfr_philip_tipsy_seeded_vol80_proxy",
        "notes": "Interior spruce (Sx = Sxw + Sxs hybrid); ICH dk typical proxy.",
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


def real_coefficient_rows() -> list[dict[str, object]]:
    """Return a deep-ish copy of ``REAL_COEFFICIENT_ROWS``.

    The shallow ``return COEFFICIENT_ROWS`` pattern is preserved for
    ``COEFFICIENT_ROWS``; here we return a list copy so callers cannot
    accidentally mutate the module-level constant when extending it
    downstream.
    """
    return [dict(row) for row in REAL_COEFFICIENT_ROWS]


def species_lookup(species_code: str, region: str) -> dict[str, object] | None:
    """Return the per-species real-coefficient row for ``(species_code, region)``.

    The lookup is exact on both keys; ``None`` is returned when no
    matching row exists so callers can fall back explicitly. The exact
    match policy keeps the diagnostic deterministic: a caller with an
    unknown (species, region) pair must surface it via ``None`` rather
    than receiving an unsupported default.

    Args:
        species_code: producer-native species code (e.g. ``"EPN"``, ``"FDC"``).
        region: jurisdiction × sub-region key (e.g. ``"QC"``, ``"BC"``,
            ``"BC_coast"``, ``"BC_interior"``).

    Returns:
        The matching ``REAL_COEFFICIENT_ROWS[i]`` dict, or ``None``.
    """
    if not species_code or not region:
        return None
    for row in REAL_COEFFICIENT_ROWS:
        if row["species_code"] == species_code and row["region"] == region:
            return dict(row)
    return None


def volume_at_age_for_species(
    species_code: str,
    region: str,
    age: float,
    *,
    age_curve_k: float = 0.022,
) -> float:
    """Per-species proxy volume in m³/ha at ``age`` via a Chapman-Richards
    saturating curve.

    The volume is computed as ``vol80 * (1 - exp(-k * age))`` against the
    per-species ``vol80_proxy_m3_per_ha`` in ``species_lookup``. The
    functional form saturates asymptotically at ``vol80_proxy_m3_per_ha``;
    the default ``k=0.022`` reaches ~63 % of the asymptote by age 50
    and ~80 % by age 100.

    This is a **proxy** — the real implementation will use age-indexed
    yield tables from Pothier & Savard (QC) or BCMFR / Philip / TIPSY
    (BC) directly. The current behaviour is sufficient for the BC + QC
    fixture substitution and explicitly disclaims calibrated accuracy.

    Args:
        species_code: producer-native species code (e.g. ``"EPN"``).
        region: jurisdiction × sub-region key.
        age: stand age in years (years).
        age_curve_k: optional Chapman-Richards rate; default ``0.022``.

    Returns:
        Per-ha volume in m³/ha at the requested age, or ``0.0`` if the
        species/region pair is not in ``REAL_COEFFICIENT_ROWS``.

    Raises:
        ValueError: if ``age`` is negative or ``age_curve_k`` is non-positive.
    """
    if age < 0:
        raise ValueError(f"Age must be non-negative for species-yield evaluation: {age}")
    if age_curve_k <= 0:
        raise ValueError(f"age_curve_k must be positive for species-yield: {age_curve_k}")
    coef = species_lookup(species_code, region)
    if coef is None:
        return 0.0
    vol80 = number(coef["vol80_proxy_m3_per_ha"])
    return vol80 * (1.0 - math.exp(-age_curve_k * age))


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
