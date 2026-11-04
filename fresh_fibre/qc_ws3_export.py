"""QC inventory → ws3-shaped harvest-output bridge.

Mirrors ``fresh_fibre.ws3_export`` but reads inventory rows from the QC
pathway (``fresh_fibre.casfri_qc_reader`` output + QC-specific yield
rules) instead of BC VRI / SCANFI diagnostic cells. The downstream LP
``supply_offer`` row shape stays identical, so the existing graph, lp,
and contract modules do not need to know which province fed the
``supply_offer`` table.

**Boundary discipline.** Synthetic-bounded QC mini fixtures live in
``metadata/ffcm-archive/prototype-inputs/bc-qc-mini-2prov/``. Real QC
GeoPackages live in ``data/raw/qc_inventory/`` (data submodule). The
adapter does not import framework code; it reads the published
CASFRI v5 translation tables only.
"""

from __future__ import annotations

import argparse
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from fresh_fibre import graph
from fresh_fibre.casfri_qc_reader import (
    CASFRI_OUTPUT_COLUMNS,
    QC_VALUE_POLICIES,
    QcLookupTables,
    column_value,
    pourcentages_vers_resolus,
    translate_row_to_casfri,
)
from fresh_fibre.contract import VALIDATION_COLUMNS, assert_contract_ok, validate_contract
from fresh_fibre.lp import build_and_solve
from fresh_fibre.scanfi_supply import (
    SCANFI_HARVEST_FIELDNAMES,
    aggregate_cell_rows_for_lp,
    validate_harvest_rows,
)
from fresh_fibre.schema import number, read_rows, require_columns, write_rows
from fresh_fibre.species_canonical import (
    VALUE_POLICIES as SPECIES_VALUE_POLICIES,
    build_index,
    canonicalize,
    decision_identifier,
    load_cross_jurisdiction_map,
    load_qc_lookup,
    merge_decisions,
)
from fresh_fibre.ws3_export import WS3_HARVEST_FIELDNAMES, export_supply_offer


DEFAULT_INPUT_DIR = Path("metadata/ffcm-archive/prototype-inputs/bc-qc-mini-2prov")
DEFAULT_BASE_INPUT_DIR = Path("metadata/ffcm-archive/prototype-inputs/bc-mini")
DEFAULT_QC_FIXTURE_DIR = Path("metadata/ffcm-archive/prototype-inputs/bc-qc-mini-2prov/qc-diagnostic")
DEFAULT_BASE_DIR_FOR_SCANFI = Path("metadata/ffcm-archive/prototype-inputs/scanfi-diagnostic")
DEFAULT_TRANSLATION_TABLE_DIR = Path("data/raw/casfri_v5/translation_tables")
DEFAULT_RUN_ID = "bc_qc_mini_2prov_lp_001"
DEFAULT_WS3_RUN_ID = "bc_qc_mini_2prov_ws3_supply_001"
DEFAULT_SOURCE_MODEL = "qc_5e_ieqm_casfri_v5_diagnostic_mini"

QC_YIELD_RULE_COLUMNS = [
    "yield_rule_id",
    "diagnostic_region",
    "commodity_id",
    "quantity_unit",
    "period_available_quantity_per_ha",
    "supply_cost",
    "supply_cost_unit",
    "roadside_cost",
    "delivered_cost_proxy",
    "species_group",
    "assortment_class",
    "moisture_basis",
    "value_policy",
    "province",
    "notes",
]


QC_YIELD_RULES_RAW: list[dict[str, object]] = [
    {
        "yield_rule_id": "qc_diag_yield_boreal_softwood_sawlog",
        "diagnostic_region": "qc_boreal_softwood_sawlog",
        "commodity_id": "log",
        "quantity_unit": "m3",
        "period_available_quantity_per_ha": 0.85,
        "supply_cost": 50,
        "supply_cost_unit": "CAD_per_m3",
        "roadside_cost": 36,
        "delivered_cost_proxy": 67,
        "species_group": "softwood",
        "assortment_class": "sawlog",
        "moisture_basis": "green",
        "province": "QC",
        "notes": (
            "Synthetic period-available log yield for QC southern-boreal sawlog proxy "
            "cells; values are not calibrated and are diagnostic only. Deliberately "
            "NOT identical to BC's interior_dry_forest rule to surface jurisdictional "
            "spread in the variant comparison."
        ),
        "value_policy": QC_VALUE_POLICIES["residue_policy_unresolved"],
    },
    {
        "yield_rule_id": "qc_diag_yield_mixedwood_sawlog",
        "diagnostic_region": "qc_mixedwood_sawlog",
        "commodity_id": "log",
        "quantity_unit": "m3",
        "period_available_quantity_per_ha": 0.62,
        "supply_cost": 54,
        "supply_cost_unit": "CAD_per_m3",
        "roadside_cost": 38,
        "delivered_cost_proxy": 70,
        "species_group": "mixed",
        "assortment_class": "sawlog",
        "moisture_basis": "green",
        "province": "QC",
        "notes": (
            "Synthetic QC mixed-wood sawlog yield (lower than pure softwood); not "
            "calibrated production data; deliberately differentiated from BC sawlog rule."
        ),
        "value_policy": QC_VALUE_POLICIES["residue_policy_unresolved"],
    },
    {
        "yield_rule_id": "qc_diag_yield_disturbance_mix_salvage",
        "diagnostic_region": "qc_disturbance_mix",
        "commodity_id": "log",
        "quantity_unit": "m3",
        "period_available_quantity_per_ha": 0.55,
        "supply_cost": 46,
        "supply_cost_unit": "CAD_per_m3",
        "roadside_cost": 32,
        "delivered_cost_proxy": 64,
        "species_group": "mixed",
        "assortment_class": "smallwood",
        "moisture_basis": "green",
        "province": "QC",
        "notes": (
            "Synthetic QC disturbance-mix salvage-style yield; cheaper than "
            "fully-interior sawlog because of debris assumptions but not calibrated."
        ),
        "value_policy": QC_VALUE_POLICIES["residue_policy_unresolved"],
    },
    {
        "yield_rule_id": "qc_diag_yield_interior_residue",
        "diagnostic_region": "qc_residue_candidate",
        "commodity_id": "logging_residue",
        "quantity_unit": "odt",
        "period_available_quantity_per_ha": 0.60,
        "supply_cost": 22,
        "supply_cost_unit": "CAD_per_odt",
        "roadside_cost": 16,
        "delivered_cost_proxy": 32,
        "species_group": "mixed",
        "assortment_class": "residue",
        "moisture_basis": "dry",
        "province": "QC",
        "notes": (
            "Synthetic QC logging-residue yield; raw diagnostic, no provincial "
            "residue-policy calibration yet; placeholder until residue_yield_policy "
            "gate resolves."
        ),
        "value_policy": "qc_residue_policy_unresolved",
    },
]
# Apply default value_policy aggregate
for _row in QC_YIELD_RULES_RAW:
    if "value_policy" not in _row:
        _row["value_policy"] = "qc_residue_policy_unresolved"


@dataclass(frozen=True)
class QcDiagnosticCell:
    """One synthetic-bounded QC stand-region cell bridged to LP-compatible supply."""

    inventory_cell_id: str
    scenario_id: str
    period: str
    province: str
    diagnostic_region: str
    spatial_unit_id: str
    supply_node_id: str
    area_ha: float
    inventory_year: int
    landcover_class: str
    casfri_row: dict[str, object]


def format_number(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".")


def safe_cell_superficie(row: dict[str, str]) -> float:
    """Read one cell's stand area in square metres from a tolerant column lookup.

    The synthetic-bounded mini fixture uses ``superficie`` (m²). The real
    QC `pee_ori` table also uses ``superficie`` (m²). Some legacy
    fixtures might use ``area_ha``; we accept either.
    """
    for alias in ("superficie", "SUPERFICIE", "area_m2", "AREA_M2"):
        raw = row.get(alias, "").strip()
        if raw:
            try:
                return float(raw)
            except ValueError:
                continue
    raw = row.get("area_ha", "").strip()
    if raw:
        try:
            return float(raw) * 10_000.0
        except ValueError:
            pass
    return 0.0


def safe_cell_year(row: dict[str, str]) -> int:
    """Read one cell's inventory year from a tolerant column lookup.

    The real QC `pee_ori` does not carry a per-stand inventory year —
    it carries a polygon-level ``an_origine`` (origin_year) and
    ``an_perturb`` (perturbation year). The synthetic fixture uses a
    fixed ``inventory_year``; we default to 2016 (5E IEQM photo vintage
    range tail) when absent.
    """
    for alias in ("inventory_year", "an_origine", "AN_ORIGINE"):
        raw = row.get(alias, "").strip()
        if raw:
            try:
                return int(raw)
            except ValueError:
                continue
    return 2016


def diagnostic_inventory_cells_csv_path(fixture_dir: Path) -> Path:
    return fixture_dir / "qc_diagnostic_inventory_cells.csv"


def yield_rules_csv_path(fixture_dir: Path) -> Path:
    return fixture_dir / "qc_diagnostic_yield_rules.csv"


def ws3_supply_crosswalk_csv_path(fixture_dir: Path) -> Path:
    return fixture_dir / "qc_diagnostic_supply_crosswalk.csv"


def write_qc_diagnostic_inventory_cells(
    output_path: Path,
    cells: list[QcDiagnosticCell],
) -> list[QcDiagnosticCell]:
    fieldnames = [
        "inventory_cell_id",
        "scenario_id",
        "period",
        "province",
        "diagnostic_region",
        "spatial_unit_id",
        "supply_node_id",
        "geometry_ref",
        "representative_area_ha",
        "inventory_year",
        "landcover_class",
        "cas_id",
        "casfri_species_canonical",
        "qc_species_resolved",
        "species_group_raw",
        "value_policy",
        "source_manifest",
    ]
    rows: list[dict[str, object]] = []
    for cell in cells:
        casfri_row = cell.casfri_row
        rows.append(
            {
                "inventory_cell_id": cell.inventory_cell_id,
                "scenario_id": cell.scenario_id,
                "period": cell.period,
                "province": cell.province,
                "diagnostic_region": cell.diagnostic_region,
                "spatial_unit_id": cell.spatial_unit_id,
                "supply_node_id": cell.supply_node_id,
                "geometry_ref": f"data:diagnostic/{cell.inventory_cell_id}",
                "representative_area_ha": format_number(cell.area_ha),
                "inventory_year": cell.inventory_year,
                "landcover_class": cell.landcover_class,
                "cas_id": str(casfri_row.get("cas_id", "")),
                "casfri_species_canonical": str(casfri_row.get("casfri_species_canonical", "")),
                "qc_species_resolved": str(casfri_row.get("qc_species_resolved", "")),
                "species_group_raw": str(casfri_row.get("species_group_raw", "")),
                "value_policy": str(casfri_row.get("species_value_policy", "qc_species_resolved_via_lookup")),
                "source_manifest": (
                    "metadata/source-crosswalks/qc_casfri_v5_schema.md"
                ),
            }
        )
    write_rows(output_path, fieldnames, rows)
    return cells


def write_qc_yield_rules(
    output_path: Path,
    rules: list[dict[str, object]] | None = None,
) -> list[dict[str, object]]:
    rules_to_write = rules if rules is not None else QC_YIELD_RULES_RAW
    write_rows(output_path, QC_YIELD_RULE_COLUMNS, rules_to_write)
    return rules_to_write


def build_inventory_rows_from_mini_fixture(
    cells_csv_path: Path,
    lookup_tables: QcLookupTables,
) -> list[dict[str, object]]:
    """Translate each QC mini-cell row through the CASFRI adapter."""
    source_rows = read_rows(cells_csv_path)
    require_columns(
        cells_csv_path,
        source_rows,
        {
            "inventory_cell_id",
            "geocode",
            "essence_1",
            "essence_pct_1",
            "essence_2",
            "essence_pct_2",
            "cl_age",
            "cl_dens",
            "cl_haut",
            "cl_drai",
            "type_eco",
            "hauteur",
            "superficie",
            "essence_qualite",
            "iqs_pot",
            "b_arbv_tot",
        },
    )
    casfri_rows: list[dict[str, object]] = []
    for row in source_rows:
        # Re-shape the source row to match the casfri reader's contract: the reader
        # reads logical column names. If the source row already uses the QC `essence_*`
        # names, that is a no-op.
        casfri = translate_row_to_casfri(row, lookup_tables)
        casfri_rows.append(casfri)
    return casfri_rows


def build_harvest_rows_for_qc(
    cells: list[QcDiagnosticCell],
    qc_diag_dir: Path,
    ws3_run_id: str = DEFAULT_WS3_RUN_ID,
    source_model: str = DEFAULT_SOURCE_MODEL,
    grade: str = "qc_casfri_diagnostic",
    resolved_species: dict[str, str] | None = None,
) -> list[dict[str, object]]:
    """Emit ws3-shaped harvest rows from the QC mini cells + yield rules."""
    yield_path = yield_rules_csv_path(qc_diag_dir)
    yield_rows = read_rows(yield_path)
    yield_by_region = {row["diagnostic_region"]: row for row in yield_rows}

    cell_rows: list[dict[str, object]] = []
    for index, cell in enumerate(cells, start=1):
        rule = yield_by_region.get(cell.diagnostic_region)
        if rule is None:
            raise ValueError(
                f"No QC diagnostic yield rule for region {cell.diagnostic_region}",
            )
        rule_period_quantity = number(rule["period_available_quantity_per_ha"])
        rule_quantity_unit = str(rule["quantity_unit"])
        cell_quantity = cell.area_ha * rule_period_quantity
        cell_rows.append(
            {
                "ws3_run_id": ws3_run_id,
                "scenario_id": cell.scenario_id,
                "period": cell.period,
                "spatial_unit_id": cell.spatial_unit_id,
                "supply_node_id": cell.supply_node_id,
                "commodity_id": rule["commodity_id"],
                "quantity": format_number(cell_quantity),
                "quantity_unit": rule_quantity_unit,
                "supply_cost": rule["supply_cost"],
                "supply_cost_unit": rule["supply_cost_unit"],
                "species_group": rule["species_group"],
                "assortment_class": rule["assortment_class"],
                "grade": grade,
                "moisture_basis": rule["moisture_basis"],
                "carbon_factor": "",
                "harvest_action_id": f"qc_diag_harvest_{index:03d}",
                "roadside_cost": rule["roadside_cost"],
                "delivered_cost_proxy": rule["delivered_cost_proxy"],
                "source_model": source_model,
                "source_record_id": f"{ws3_run_id}:{cell.inventory_cell_id}",
                "notes": (
                    "Aggregated synthetic QC diagnostic supply row; quantity is "
                    "representative_area_ha times period_available_quantity_per_ha; "
                    "QC yield rules deliberately differ from BC to surface "
                    "inter-provincial model signal; not calibrated production data."
                ),
                "inventory_cell_id": cell.inventory_cell_id,
                "diagnostic_region": cell.diagnostic_region,
                "inventory_year": cell.inventory_year,
                "landcover_class": cell.landcover_class,
                "inventory_value_policy": (
                    str(cell.casfri_row.get("species_value_policy", "qc_species_resolved_via_lookup"))
                ),
                "yield_rule_id": rule["yield_rule_id"],
                "yield_value_per_ha": rule["period_available_quantity_per_ha"],
                "yield_value_unit": f"{rule['quantity_unit']}_per_ha",
                "yield_value_policy": rule["value_policy"],
                "biomass_source_file": "",
                "height_source_file": "",
                "closure_source_file": "",
                "landcover_source_file": "",
                "age_source_file": "",
                "canlabs_severity_source_path": "",
                "canlabs_severity_mean_dnbr": "",
                "canlabs_severity_valid_count": "",
                "canlabs_severity_nodata_count": "",
                "canlabs_severity_min_dnbr": "",
                "canlabs_severity_max_dnbr": "",
                "canlabs_severity_value_policy": "",
                "canlabs_severity_manifest_ref": "",
            }
        )
    harvest_rows = aggregate_cell_rows_for_lp(cell_rows, ws3_run_id, source_model, grade)
    validate_harvest_rows(harvest_rows, allowed_yield_policies={"qc_residue_policy_unresolved"})
    return harvest_rows


def resolved_lookup_table_for_qc_mini(
    inventory_csv_path: Path,
    cross_path: Path,
    qc_lookup_path: Path,
) -> dict[str, str]:
    """Build a resolve-codes lookup keyed by inventory cell id → resolved spec.

    The lookup is informational; downstream code can use it to assert
    cross-jurisdiction species resolution at fixture time.
    """
    stand_rows = list(read_rows(inventory_csv_path))
    codes: list[tuple[str, str]] = []
    for row in stand_rows:
        raw = column_value(row, "essence_1") or column_value(row, "ess_qualite")
        province = row.get("province", "QC").upper()
        if raw:
            codes.append((raw, province))
    cross_map = load_cross_jurisdiction_map(cross_path)
    qc_lookup = load_qc_lookup(qc_lookup_path)
    cross_index = build_index(cross_map)
    qc_index = build_index(qc_lookup)
    tables = canonicalize(codes, cross_index, qc_index)
    tables = merge_decisions(tables)
    return {
        decision_identifier(
            raw_code=t.raw_code,
            province=t.province,
            casfri=t.casfri_canonical,
            policy=t.value_policy,
        ): f"{t.raw_code}→{t.fresh_canonical}"
        for t in tables
    }


def run_qc_ws3_export_bridge(
    qc_diag_dir: Path = DEFAULT_QC_FIXTURE_DIR,
    output_path: Path = DEFAULT_QC_FIXTURE_DIR / "qc_ws3_harvest_output.csv",
    lookup_tables: QcLookupTables | None = None,
    grade: str = "qc_casfri_diagnostic",
    ws3_run_id: str = DEFAULT_WS3_RUN_ID,
    source_model: str = DEFAULT_SOURCE_MODEL,
) -> list[dict[str, object]]:
    """Translate QC synthetic-bounded mini cells into ws3-shaped harvest output."""
    if lookup_tables is None:
        lookup_tables = QcLookupTables.from_csv_dir(DEFAULT_TRANSLATION_TABLE_DIR)
    yield_path = yield_rules_csv_path(qc_diag_dir)
    write_qc_yield_rules(yield_path)

    cells_csv_path = diagnostic_inventory_cells_csv_path(qc_diag_dir)
    cells_source_rows = read_rows(cells_csv_path)
    cells: list[QcDiagnosticCell] = []
    translated_rows: list[dict[str, object]] = []
    for index, row in enumerate(cells_source_rows, start=1):
        casfri = translate_row_to_casfri(row, lookup_tables)
        superficie_m2 = safe_cell_superficie(row)
        cells.append(
            QcDiagnosticCell(
                inventory_cell_id=row["inventory_cell_id"],
                scenario_id=row["scenario_id"],
                period=row["period"],
                province=row["province"],
                diagnostic_region=row["diagnostic_region"],
                spatial_unit_id=row["spatial_unit_id"],
                supply_node_id=row["supply_node_id"],
                area_ha=superficie_m2 / 10_000.0,
                inventory_year=safe_cell_year(row),
                landcover_class=row.get("landcover_class", "nfi_forest_proxy"),
                casfri_row=casfri,
            )
        )
        translated_rows.append(casfri)
    translated_path = qc_diag_dir / "qc_diagnostic_inventory_cells_casfri_translated.csv"
    write_rows(translated_path, CASFRI_OUTPUT_COLUMNS, translated_rows)

    harvest_rows = build_harvest_rows_for_qc(
        cells, qc_diag_dir, ws3_run_id=ws3_run_id, source_model=source_model, grade=grade,
    )
    write_rows(output_path, SCANFI_HARVEST_FIELDNAMES, harvest_rows)
    return harvest_rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qc-diag-dir", type=Path, default=DEFAULT_QC_FIXTURE_DIR)
    parser.add_argument(
        "--output-path",
        type=Path,
        default=DEFAULT_QC_FIXTURE_DIR / "qc_ws3_harvest_output.csv",
    )
    parser.add_argument(
        "--translation-table-dir",
        type=Path,
        default=DEFAULT_TRANSLATION_TABLE_DIR,
    )
    parser.add_argument("--grade", default="qc_casfri_diagnostic")
    args = parser.parse_args()
    lookup_tables = QcLookupTables.from_csv_dir(args.translation_table_dir)
    rows = run_qc_ws3_export_bridge(
        qc_diag_dir=args.qc_diag_dir,
        output_path=args.output_path,
        lookup_tables=lookup_tables,
        grade=args.grade,
    )
    print(f"wrote {len(rows)} QC ws3 harvest rows to {args.output_path}")


if __name__ == "__main__":
    main()
