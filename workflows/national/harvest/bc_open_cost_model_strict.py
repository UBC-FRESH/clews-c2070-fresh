"""Strict-open-licence BC logging and truck-haul cost model.

This entrypoint is deliberately separate from ``bc_open_cost_model.py`` and
``open_data_net_value.py``.  It reads only Statistics Canada Open Licence
tables, OGL-BC/OGL-Canada derived aggregates, and CC-BY/public-domain methods.
The existing public-reference model is read only for a cross-check table.

Run::

    python workflows/national/harvest/bc_open_cost_model_strict.py \
        --out-dir data/derived/national/harvest_basis/bc_open_cost_model_strict

Every estimate row carries ``evidence=model_estimate``,
``cost_basis=strict_open_proxy``, ``licence_tier=strict_open``,
``policy_grade=false`` and ``observed=false``.  The model is not a BC empirical
calibration and must not be described as an observed contractor cost.
"""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in {None, ""}:  # pragma: no cover - direct script execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from workflows.national.harvest.strict_open.artifacts import (
    build,
    comparison_rows,
    comparison_summary,
    main,
)
from workflows.national.harvest.strict_open.machine_rate import (
    MachineRateError,
    MachineSpec,
    average_annual_investment,
    machine_rate,
    miyata_example_spec,
    sessions_example_spec,
)
from workflows.national.harvest.strict_open.model import (
    BASE_PARAMS,
    BOUND_PARAMS,
    COST_BASIS,
    DISTANCE_SCENARIOS,
    EVIDENCE,
    HIGH_PARAMS,
    LICENCE_TIER,
    LOW_PARAMS,
    MODEL_ID,
    OBSERVED,
    PIECE_SCENARIOS,
    POLICY_GRADE,
    REGIONS,
    SLOPE_SCENARIOS,
    SYSTEMS,
    VOLUME_SCENARIOS,
    ZONES,
    ParameterSet,
    Scenario,
    StrictCostModelError,
    build_scenarios,
    cycle_hours,
    estimate_scenario,
    haul_cost,
    logging_cost,
    machine_spec,
    phase_productivity,
    region_slope_context,
    region_slope_summary,
    scenario_costs,
    sensitivity_rows,
    validation_payload,
    worked_examples,
    zone_summary,
)
from workflows.national.harvest.strict_open.sources import (
    CALIBRATION_CLAIM,
    DIESEL_BASE,
    DIESEL_HIGH,
    DIESEL_LOW,
    HAUL_WAGE_BASE,
    HAUL_WAGE_HIGH,
    HAUL_WAGE_LOW,
    MODEL_VERSION,
    SOURCE_REGISTER,
    WAGE_BASE,
    WAGE_HIGH,
    WAGE_LOW,
    observations_payload,
    source_rows,
    validate_source_register,
)

__all__ = [
    "BASE_PARAMS",
    "BOUND_PARAMS",
    "CALIBRATION_CLAIM",
    "COST_BASIS",
    "DIESEL_BASE",
    "DIESEL_HIGH",
    "DIESEL_LOW",
    "DISTANCE_SCENARIOS",
    "EVIDENCE",
    "HAUL_WAGE_BASE",
    "HAUL_WAGE_HIGH",
    "HAUL_WAGE_LOW",
    "HIGH_PARAMS",
    "LICENCE_TIER",
    "LOW_PARAMS",
    "MODEL_ID",
    "MODEL_VERSION",
    "OBSERVED",
    "PIECE_SCENARIOS",
    "POLICY_GRADE",
    "REGIONS",
    "SLOPE_SCENARIOS",
    "SOURCE_REGISTER",
    "SYSTEMS",
    "VOLUME_SCENARIOS",
    "WAGE_BASE",
    "WAGE_HIGH",
    "WAGE_LOW",
    "ZONES",
    "MachineRateError",
    "MachineSpec",
    "ParameterSet",
    "Scenario",
    "StrictCostModelError",
    "average_annual_investment",
    "build",
    "build_scenarios",
    "comparison_rows",
    "comparison_summary",
    "cycle_hours",
    "estimate_scenario",
    "haul_cost",
    "logging_cost",
    "machine_rate",
    "machine_spec",
    "main",
    "miyata_example_spec",
    "observations_payload",
    "phase_productivity",
    "region_slope_context",
    "region_slope_summary",
    "scenario_costs",
    "sensitivity_rows",
    "sessions_example_spec",
    "source_rows",
    "validate_source_register",
    "validation_payload",
    "worked_examples",
    "zone_summary",
]

if __name__ == "__main__":
    raise SystemExit(main())
