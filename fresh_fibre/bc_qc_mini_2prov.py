"""Run the BC + QC two-province LP fixture.

This module is the network-flow LP runner step at the end of the
two-province supply chain. The fixture
``metadata/ffcm-archive/prototype-inputs/bc-qc-mini-2prov/`` carries
two parallel ``supply_offer`` rowsets — one for BC and one for QC — and
a downstream LP that routes both provinces through the same facility /
export / market network.

**Supply paths.**

- BC supply reads from a real BC Vegetation Resources Inventory (VRI)
  archive (the ``VEG_COMP_LYR_R1_POLY`` feature class distributed by
  DataBC as an ESRI File Geodatabase). For local unit-test execution
  the BC supply reads from a tiny stub at
  ``tests/fixtures/bc_vri_sample/``; the full 3.9 GB archive is vendored
  in the ``data`` data-submodule and is materialised on demand through
  the FreshForge workflow pattern.
- QC supply reads from CASFRI v5 translation tables over QC
  5E/3E IEQM stand inventory via ``fresh_fibre.qc_ws3_export`` /
  ``fresh_fibre.casfri_qc_reader``. The truth-path real QC GeoPackage
  lives in the ``data`` data-submodule at
  ``data/raw/qc_inventory/2023-ieqm-eighth/``.

**Bridge to ws3-shape supply offers.** BC VRI emits
``supply_offer_bc_vri.csv`` in CASFRI-shape (30 attributes + 3 wrapper
provenance fields). A small ``vri_to_bc_mini_mapping.csv`` join table
collapses each stub polygon to one ws3-shape ``supply_offer_bc.csv``
row that the LP can read directly. In production, the same join would
be a BSU/FMU yield-rule lookup analogous to
``fresh_fibre.scanfi_supply.build_harvest_rows``.

**Species canonicalization.** Both jurisdictions emit producer-native
species codes (``species_cd_*``). The cross-jurisdiction canonicalization
happens downstream in ``fresh_fibre.species_canonical`` — this module
deliberately does **not** re-implement that translation.

**Output artefacts.**

- ``generated/solution_flow.csv`` / ``generated/solution_summary.csv`` —
  standard LP outputs from
  ``fresh_fibre.lp.build_and_solve``.
- ``generated/bc_qc_mini_supply_comparison.csv`` — BC vs. QC
  supply side-by-side (commodity × unit × province).
- ``generated/bc_qc_mini_pathway_flow.csv`` — province-keyed
  solution-flows for the inter-province node.
- ``generated/bc_qc_mini_solution_summary.csv`` — province-keyed
  solution-summary metrics.
- ``generated/contract_validation.csv`` — standard contract validation
  output from the LP.
- ``supply_offer_bc_vri.csv`` — BC VRI CASFRI-shape rows (30 + 3 cols).
- ``supply_offer_bc.csv`` — BC ws3-shape supply offers (LP input).
- ``metadata/source-reviews/..`` — links out from this point in the LP
  boundary to the schema crosswalk and the data submodule.

**Boundary discipline.** The fixture is synthetic-bounded — no raw
provincial payloads live in the parent repo's fixture directory. The
real BC VRI File Geodatabase and the real QC GeoPackage sample are
staged in the data submodule (see ``docs/data-repository-runbook.md``).
CASFRI translation tables are in
``data/raw/casfri_v5/translation_tables/``. The adapter reads them at
runtime. GPL-3.0 framework source is **not** vendored.

**SCANFI synthetic path (legacy).** Earlier iterations of this fixture
fed BC supply from a SCANFI diagnostic mini
(``metadata/ffcm-archive/prototype-inputs/scanfi-diagnostic``). That
synthetic path has been retired in favour of the real BC VRI
ingestion; ``fresh_fibre.scanfi_supply`` remains as a documented
module + standalone validators for its own diagnostic variants but is
no longer the BC-side authority here.
"""

from __future__ import annotations

import argparse
import logging
import shutil
from collections import OrderedDict, defaultdict
from pathlib import Path

from fresh_fibre import graph
from fresh_fibre.bc_vri_reader import (
    BC_OUTPUT_COLUMNS as BC_VRI_OUTPUT_COLUMNS,
    BcLookupTables,
    JURISDICTION as BC_VRI_JURISDICTION,
    SOURCE_LABEL as BC_VRI_SOURCE_LABEL,
    process_bc_vri,
    translate_row_to_casfri,
)
from fresh_fibre.contract import VALIDATION_COLUMNS, assert_contract_ok, validate_contract
from fresh_fibre.gauthier_yield import species_lookup, volume_at_age_for_species
from fresh_fibre.lp import build_and_solve
from fresh_fibre.qc_ws3_export import (
    DEFAULT_QC_FIXTURE_DIR,
    DEFAULT_TRANSLATION_TABLE_DIR,
    QC_VALUE_POLICIES,
    QcLookupTables,
    diagnostic_inventory_cells_csv_path,
    run_qc_ws3_export_bridge,
    write_qc_yield_rules,
)
from fresh_fibre.schema import number, read_rows, write_rows
# `fresh_fibre.scanfi_supply` is retained as a documented standalone
# module for its own diagnostic variants (see
# `tests/test_bc_mini_prototype.py`); it is **not** the BC-side
# authority for the two-province fixture anymore — that role belongs to
# `fresh_fibre.bc_vri_reader`. We keep the SCANFI harvest-fieldname
# constant here because `run_qc_supply` rewrites QC harvest rows before
# the LP merge step, mirroring the historical convention.
from fresh_fibre.scanfi_supply import (
    DEFAULT_BASE_INPUT_DIR as DEFAULT_SCANFI_BASE,
    DEFAULT_SCANFI_INPUT_DIR as DEFAULT_SCANFI_DIAG_DIR,
    SCANFI_HARVEST_FIELDNAMES,
    SOURCE_MODEL as SCANFI_SOURCE_MODEL,
    WS3_RUN_ID as SCANFI_WS3_RUN_ID,
    GAUTHIER_VALUE_POLICY,
)
from fresh_fibre.ws3_export import SUPPLY_OFFER_COLUMNS, export_supply_offer


# Module logger for the dispatch helpers. Lives here (rather than
# importing a parent logger) so the bridge warnings land under this
# module's namespace without dragging in ``fresh_fibre`` package-wide
# state.
LOGGER = logging.getLogger("fresh_fibre.bc_qc_mini_2prov")


DEFAULT_FIXTURE_DIR = Path("metadata/ffcm-archive/prototype-inputs/bc-qc-mini-2prov")
DEFAULT_SCENARIO = "bc_qc_mini_2prov_001"
DEFAULT_RUN_ID = "bc_qc_mini_2prov_lp_001"
DEFAULT_GRADE = "bc_qc_mini_2prov_diagnostic"
DEFAULT_WS3_RUN_ID = "bc_qc_mini_2prov_ws3_supply_001"
DEFAULT_SOURCE_MODEL = "qc_5e_ieqm_casfri_v5_diagnostic_mini_bc_vri_mini"
DEFAULT_BC_MINI_SCENARIO = "bc_mini_001"
DEFAULT_BC_VRI_WS3_RUN_ID = "bc_vri_diagnostic_supply_001"

REPO_ROOT_FOR_REAL_YIELD = Path(__file__).resolve().parents[1]
"""Repository root for non-fixture path resolution.

Used by ``_run_full_two_province_real_yield_skeleton`` to default the
BC VRI sample fixture path when ``bc_vri_stub_dir`` is not supplied
explicitly.
"""

DEFAULT_BC_VRI_CSV_STUB = Path("tests/fixtures/bc_vri_sample/sample.csv")
"""Stub BC VRI archive path used by ``run_bc_supply`` (and by tests).

Production runs pass the real BC VRI ESRI File Geodatabase path from
``data/raw/bc_vri/veg_comp_lyr_r1_poly.gdb`` (DataLad-managed heavy
payload). The stub keeps unit-test CI hermetic — see
``tests/fixtures/bc_vri_sample/README.md``.
"""

DEFAULT_BC_VRI_LOOKUP_DIR: Path | None = None
"""Optional CASFRI v5 translation-table directory for the BC path.

Empty by default → ``process_bc_vri`` skips the lookup file load; species
canonicalization is downstream in ``fresh_fibre.species_canonical``.
Set this to ``data/raw/casfri_v5/translation_tables`` to load
``bc_vri01_lyr.csv``.
"""

DEFAULT_BC_VRI_TO_WS3_MAPPING = Path(
    "tests/fixtures/bc_vri_sample/vri_to_bc_mini_mapping.csv",
)
"""Stub join table: BC VRI POLY_ID → ws3-shape supply_offer_bc.csv row.

Maps each stub polygon to one ws3-shape row using the BC mini network's
``spatial_unit_id`` / ``supply_node_id`` vocabulary. Production VRI
runs would substitute a BSU/FMU yield-rule lookup analogous to
``fresh_fibre.scanfi_supply.build_harvest_rows``.
"""

VARIANT_RUN_MATRIX_COLUMNS = [
    "province",
    "scanfi_ws3_run_id",
    "qc_ws3_run_id",
    "scenario_id",
    "supply_rows",
    "supply_quantity_m3",
    "supply_quantity_odt",
    "objective_contribution_log",
    "objective_contribution_residue",
    "notes",
]

SUPPLY_COMPARISON_COLUMNS = [
    "province",
    "commodity_id",
    "quantity_unit",
    "total_quantity",
    "supply_cost_average",
    "supply_count",
    "source_model",
    "ws3_run_id",
]

PATHWAY_FLOW_COLUMNS = [
    "province",
    "origin_node_id",
    "destination_node_id",
    "source_commodity_id",
    "product_commodity_id",
    "transport_link_id",
    "technology_id",
    "market_id",
    "quantity",
    "quantity_unit",
    "run_id",
]

SOLUTION_SUMMARY_PER_PROVINCE_COLUMNS = [
    "province",
    "metric_id",
    "commodity_id",
    "facility_class",
    "market_id",
    "value",
    "unit",
]


def write_variant_run_matrix(
    matrix_rows: list[dict[str, object]],
    output_path: Path,
) -> list[dict[str, object]]:
    write_rows(output_path, VARIANT_RUN_MATRIX_COLUMNS, matrix_rows)
    return matrix_rows


def run_bc_vri_supply(
    gdb_path: Path = DEFAULT_BC_VRI_CSV_STUB,
    lookup_dir: Path | None = DEFAULT_BC_VRI_LOOKUP_DIR,
    out_csv: Path = DEFAULT_FIXTURE_DIR / "supply_offer_bc_vri.csv",
    *,
    fmu_key_field: str | None = None,
    csv_fallback_path: Path | None = None,
) -> dict[str, object]:
    """Read BC VRI polygons and emit CASFRI-shape supply rows.

    Bridges to ``fresh_fibre.bc_vri_reader.translate_row_to_casfri``; no
    species canonicalization lives here (downstream in
    ``fresh_fibre.species_canonical``, which applies the team's
    ``BC_`` prefix via ``species_code_mapping.csv``).

    Args:
        gdb_path: Path to the BC VRI File Geodatabase directory (or
            to the on-disk stub used by unit tests). When ``csv_fallback_path``
            is supplied this argument is ignored — the runner reads the CSV
            directly.
        lookup_dir: Optional BC species lookup directory. ``None`` (the
            default) loads nothing — species canonicalization is downstream.
        out_csv: Output CSV path. Defaults to
            ``metadata/ffcm-archive/prototype-inputs/bc-qc-mini-2prov/supply_offer_bc_vri.csv``.
        fmu_key_field: Optional column name used as an FMU/TFL bucket
            key. When supplied, ``process_bc_vri`` writes one CSV per
            distinct key value. When ``None``, a single coalesced CSV
            is written at ``out_csv``.
        csv_fallback_path: Explicit CSV path read in place of the
            ``.gdb``. Required when ``fiona`` is not installed in the
            runtime (also used by the unit-test stub).

    Returns:
        Summary dict with ``raw_polygon_count``, ``output_path``, and
        when partitioned the list of FMU group keys.
    """
    out_csv.parent.mkdir(parents=True, exist_ok=True)

    # Tolerant lookup load; downstream species_canonical does the actual
    # cross-jurisdiction translation. We accept missing files silently
    # to keep stub and offline runs working.
    try:
        BcLookupTables.from_csv_dir(lookup_dir) if lookup_dir is not None else None
    except FileNotFoundError:
        LOGGER.debug(
            "BC VRI lookup directory %s absent; species canonicalization "
            "deferred to fresh_fibre.species_canonical.",
            lookup_dir,
        )

    if csv_fallback_path is None:
        csv_fallback_path = gdb_path
    summary = process_bc_vri(
        gdb_path=gdb_path,
        lookup_dir=lookup_dir or Path("/nonexistent"),
        out_dir=out_csv.parent,
        fmu_key_field=fmu_key_field,
        csv_fallback_path=csv_fallback_path,
    )
    written_paths: list[Path] = [Path(p) for p in summary.get("output_paths", [])]
    if not written_paths:
        raise RuntimeError(
            f"BC VRI reader produced no output paths (gdb_path={gdb_path}, "
            f"csv_fallback={csv_fallback_path}, fmu_key_field={fmu_key_field}).",
        )
    if not out_csv.exists() and len(written_paths) == 1:
        # ``process_bc_vri`` writes one file under its default-named CSV;
        # if the caller asked for an alternative ``out_csv``, copy the
        # single-polygon-bucket output to the requested destination.
        write_rows(out_csv, BC_VRI_OUTPUT_COLUMNS, read_rows(written_paths[0]))
    return {
        "scenario_id": DEFAULT_BC_MINI_SCENARIO,
        "ws3_run_id": DEFAULT_BC_VRI_WS3_RUN_ID,
        "source_model": BC_VRI_SOURCE_LABEL,
        "raw_polygon_count": summary["raw_polygon_count"],
        "fmu_groups": summary.get("fmu_groups", []),
        "casfri_output_path": out_csv,
    }


def _bridge_vri_to_supply_offer(
    casfri_csv_path: Path,
    mapping_path: Path,
    supply_offer_csv_path: Path,
    *,
    scenario_id: str = DEFAULT_BC_MINI_SCENARIO,
) -> list[dict[str, str]]:
    """Collapse BC VRI CASFRI-shape rows into ws3-shape ``supply_offer_bc.csv``.

    Joins ``casfri_csv_path`` (the CASFRI-shape output of
    ``run_bc_vri_supply``) with the explicit stub mapping
    ``mapping_path`` on ``poly_id``, then writes one ws3-shape row per
    VRI polygon. The stub mapping hardcodes BC mini spatial-unit /
    supply-node vocabulary; production runs would substitute a BSU/FMU
    yield-rule join.
    """
    casfri_rows = read_rows(casfri_csv_path)
    mapping_rows = read_rows(mapping_path)
    mapping_by_poly = {row["poly_id"]: row for row in mapping_rows if row.get("poly_id")}

    bridged: list[dict[str, str]] = []
    for casfri_row in casfri_rows:
        poly_id = casfri_row.get("poly_id", "")
        if not poly_id or poly_id not in mapping_by_poly:
            # Skip unmapped polygons — in production this is an upstream
            # join error; here it signals a stale stub.
            LOGGER.warning(
                "BC VRI polygon %s has no ws3 mapping; skipping handoff.",
                poly_id or "<empty>",
            )
            continue
        map_row = mapping_by_poly[poly_id]
        bridged.append(
            {
                "scenario_id": map_row.get("scenario_id", scenario_id),
                "period": map_row.get("period", "0"),
                "spatial_unit_id": map_row["spatial_unit_id"],
                "supply_node_id": map_row["supply_node_id"],
                "commodity_id": map_row["commodity_id"],
                "quantity": map_row["quantity"],
                "quantity_unit": map_row["quantity_unit"],
                "supply_cost": map_row["supply_cost"],
                "supply_cost_unit": map_row["supply_cost_unit"],
                "species_group": map_row["species_group"],
                "assortment_class": map_row["assortment_class"],
                "grade": map_row["grade"],
                "moisture_basis": map_row["moisture_basis"],
                "carbon_factor": map_row.get("carbon_factor", ""),
                "harvest_action_id": map_row["harvest_action_id"],
                "roadside_cost": map_row["roadside_cost"],
                "delivered_cost_proxy": map_row["delivered_cost_proxy"],
            }
        )

    supply_offer_csv_path.parent.mkdir(parents=True, exist_ok=True)
    write_rows(supply_offer_csv_path, SUPPLY_OFFER_COLUMNS, bridged)
    return bridged


def run_bc_supply(
    gdb_path: Path = DEFAULT_BC_VRI_CSV_STUB,
    bc_mini_dir: Path = DEFAULT_SCANFI_BASE,
    output_dir: Path = DEFAULT_FIXTURE_DIR,
    *,
    bc_vri_mapping_path: Path = DEFAULT_BC_VRI_TO_WS3_MAPPING,
    scenario_id: str = DEFAULT_BC_MINI_SCENARIO,
) -> dict[str, object]:
    """Build BC supply rows from real BC VRI polygons (was SCANFI synthetic).

    Thin wrapper around ``run_bc_vri_supply`` that emits both the
    CASFRI-shape ``supply_offer_bc_vri.csv`` (the new authoritative BC
    supply artefact) and the legacy ws3-shape ``supply_offer_bc.csv``
    that the downstream LP ingests. The legacy
    ``benchmark_input`` argument carries the BC mini's spatial unit /
    supply node tables so the bridge knows the BC mini vocabulary; it
    is **not** the SCANFI fixture anymore.

    The earlier SCANFI synthetic BC path
    (``fresh_fibre.scanfi_supply.build_scanfi_diagnostic_supply``) has
    been retired; that module remains for its own standalone diagnostic
    tests but is no longer sourced from this fixture.
    """
    casfri_csv_path = output_dir / "supply_offer_bc_vri.csv"
    supply_offer_csv_path = output_dir / "supply_offer_bc.csv"
    bc_mini_dir_path = Path(bc_mini_dir)

    summary = run_bc_vri_supply(
        gdb_path=gdb_path,
        out_csv=casfri_csv_path,
        csv_fallback_path=gdb_path,
    )
    rows = _bridge_vri_to_supply_offer(
        casfri_csv_path=casfri_csv_path,
        mapping_path=bc_vri_mapping_path,
        supply_offer_csv_path=supply_offer_csv_path,
        scenario_id=scenario_id,
    )
    # Validate that the resulting ws3 supply offer shape is what the
    # contract consumers expect (LP, merge, comparison).
    if rows:
        sample = rows[0]
        missing = [col for col in SUPPLY_OFFER_COLUMNS if col not in sample]
        if missing:
            raise ValueError(
                f"BC VRI bridge produced ws3 supply_offer_bc.csv missing "
                f"columns: {missing}",
            )
    return {
        "scenario_id": scenario_id,
        "ws3_run_id": DEFAULT_BC_VRI_WS3_RUN_ID,
        "source_model": BC_VRI_SOURCE_LABEL,
        "supply_rows": rows,
        "supply_offer_path": supply_offer_csv_path,
        "casfri_output_path": casfri_csv_path,
        "raw_polygon_count": summary["raw_polygon_count"],
        "fmu_groups": summary["fmu_groups"],
    }


def run_qc_supply(
    qc_di_dir: Path = DEFAULT_QC_FIXTURE_DIR,
    output_dir: Path = DEFAULT_FIXTURE_DIR,
    translation_dir: Path = DEFAULT_TRANSLATION_TABLE_DIR,
    scenario_id: str = DEFAULT_SCENARIO,
) -> dict[str, object]:
    """Build the QC mini diagnostic supply rows into the fixture dir."""
    lookup_tables = QcLookupTables.from_csv_dir(translation_dir)
    output_path = output_dir / "ws3_harvest_output_qc.csv"
    qc_supply_offer_path = output_dir / "supply_offer_qc.csv"
    rows = run_qc_ws3_export_bridge(
        qc_diag_dir=qc_di_dir,
        output_path=output_path,
        lookup_tables=lookup_tables,
    )
    # Re-write the harvest rows with the unified scenario_id so ws3_export
    # reference validation succeeds against the merged-fixture scenario.
    rewritten_rows: list[dict[str, str]] = []
    for row in read_rows(output_path):
        updated = dict(row)
        if updated.get("scenario_id") in ("qc_mini_001", ""):
            updated["scenario_id"] = scenario_id
        rewritten_rows.append(updated)
    write_rows(output_path, SCANFI_HARVEST_FIELDNAMES, rewritten_rows)
    export_supply_offer(
        output_path,
        qc_supply_offer_path,
        output_dir,
        output_path.parent / "qc_mini_ws3_export_manifest.csv",
    )
    return {
        "scenario_id": scenario_id,
        "ws3_run_id": "qc_casfri_diagnostic_supply_001",
        "source_model": DEFAULT_SOURCE_MODEL,
        "supply_rows": rows,
        "supply_offer_path": qc_supply_offer_path,
        "harvest_output_path": output_path,
    }


def merge_supply_offers(
    output_dir: Path,
    bc_supply_offer_path: Path,
    qc_supply_offer_path: Path,
    scenario_id: str,
) -> Path:
    """One supply-offer table that covers both provinces.

    The merged table preserves the bc/qc scenario_id split so LP
    references resolve per province; downstream consumers can filter by
    scenario_id when needed.
    """
    bc_rows = read_rows(bc_supply_offer_path)
    qc_rows = read_rows(qc_supply_offer_path)
    # Normalize scenario_id for QC rows to the unified scenario_id.
    normalized_qc_rows: list[dict[str, str]] = []
    for row in qc_rows:
        normalized = dict(row)
        if normalized.get("scenario_id") == "qc_mini_001":
            normalized["scenario_id"] = scenario_id
        normalized_qc_rows.append(normalized)
    merged: list[dict[str, str]] = []
    for row in bc_rows:
        merged_row = dict(row)
        if merged_row.get("scenario_id") == "bc_mini_001":
            merged_row["scenario_id"] = scenario_id
        merged.append(merged_row)
    merged.extend(normalized_qc_rows)
    output_path = output_dir / "supply_offer.csv"
    fieldnames = [
        "scenario_id", "period", "spatial_unit_id", "supply_node_id", "commodity_id",
        "quantity", "quantity_unit", "supply_cost", "supply_cost_unit", "species_group",
        "assortment_class", "grade", "moisture_basis", "carbon_factor", "harvest_action_id",
        "roadside_cost", "delivered_cost_proxy",
    ]
    write_rows(output_path, fieldnames, merged)
    return output_path


def build_supply_comparison(
    bc_supply_offer_path: Path,
    qc_supply_offer_path: Path,
    output_path: Path,
) -> list[dict[str, object]]:
    """Emit BC vs QC supply side-by-side (commodity × unit × province)."""
    rows = []
    for province_label, path in (("BC", bc_supply_offer_path), ("QC", qc_supply_offer_path)):
        totals: dict[tuple[str, str], dict[str, float]] = defaultdict(
            lambda: {"quantity": 0.0, "supply_cost_weighted": 0.0, "count": 0},
        )
        for row in read_rows(path):
            key = (row["commodity_id"], row["quantity_unit"])
            stats = totals[key]
            quantity = number(row["quantity"])
            stats["quantity"] += quantity
            stats["supply_cost_weighted"] += number(row["supply_cost"]) * quantity
            stats["count"] += 1
        for (commodity_id, quantity_unit), stats in sorted(totals.items()):
            rows.append(
                {
                    "province": province_label,
                    "commodity_id": commodity_id,
                    "quantity_unit": quantity_unit,
                    "total_quantity": format_number(stats["quantity"]),
                    "supply_cost_average": format_number(
                        stats["supply_cost_weighted"] / stats["quantity"]
                        if stats["quantity"] > 0 else 0.0
                    ),
                    "supply_count": int(stats["count"]),
                    "source_model": (
                        "scanfi_v2_2025_diagnostic_inventory_fixture" if province_label == "BC"
                        else DEFAULT_SOURCE_MODEL
                    ),
                    "ws3_run_id": (
                        "scanfi_diagnostic_supply_001" if province_label == "BC"
                        else "qc_casfri_diagnostic_supply_001"
                    ),
                }
            )
    write_rows(output_path, SUPPLY_COMPARISON_COLUMNS, rows)
    return rows


def build_pathway_flow(
    solution_flow_path: Path,
    output_path: Path,
) -> list[dict[str, object]]:
    """Split solution flows into BC vs QC provinces using node-id prefixes."""
    bc_supply_prefixes = ("supply_coast", "supply_interior", "supply_residue", "mill_coast", "mill_interior")
    qc_supply_prefixes = ("supply_qc_", "mill_qc", "mill_sawmill_qc", "mill_pulp_qc", "plant_bioenergy_qc", "export_montreal")
    bc_route_prefixes = ("route_001", "route_002", "route_003", "route_004", "route_005", "route_006", "route_007", "route_008", "route_009", "route_010", "route_011", "route_012")
    qc_route_prefixes = ("qc_route_", )

    def classify(label: str)  -> str:
        if label.startswith(bc_supply_prefixes):
            return "BC"
        if label.startswith(qc_supply_prefixes):
            return "QC"
        if label.startswith(bc_route_prefixes):
            return "BC"
        if label.startswith(qc_route_prefixes):
            return "QC"
        if "bc_" in label:
            return "BC"
        if "qc_" in label:
            return "QC"
        return ""

    rows = []
    for row in read_rows(solution_flow_path):
        origin = row.get("origin_node_id", "")
        destination = row.get("destination_node_id", "")
        province_classifications = {classify(origin), classify(destination)}
        # Pick the first non-empty classification
        province = ""
        for candidate in (
            classify(origin), classify(destination), "shared",
        ):
            if candidate:
                province = candidate
                break
        rows.append(
            {
                "province": province,
                "origin_node_id": row["origin_node_id"],
                "destination_node_id": row["destination_node_id"],
                "source_commodity_id": row["source_commodity_id"],
                "product_commodity_id": row["product_commodity_id"],
                "transport_link_id": row["transport_link_id"],
                "technology_id": row["technology_id"],
                "market_id": row["market_id"],
                "quantity": row["quantity"],
                "quantity_unit": row["quantity_unit"],
                "run_id": row["run_id"],
            }
        )
    write_rows(output_path, PATHWAY_FLOW_COLUMNS, rows)
    return rows


def build_solution_summary_per_province(
    base_dir: Path = DEFAULT_FIXTURE_DIR,
    output_path: Path = DEFAULT_FIXTURE_DIR / "generated" / "bc_qc_mini_solution_summary.csv",
) -> list[dict[str, object]]:
    """Province-keyed per-region breakdown of the canonical solution summary."""
    supply_offer_path = base_dir / "supply_offer.csv"
    if not supply_offer_path.exists():
        raise FileNotFoundError(
            f"Two-province supply_offer table missing: {supply_offer_path}",
        )
    summary_rows: list[dict[str, object]] = []
    saw_quantity: dict[str, dict[tuple[str, str], float]] = defaultdict(lambda: defaultdict(float))
    saw_cost: dict[str, dict[tuple[str, str], float]] = defaultdict(lambda: defaultdict(float))
    for offer in read_rows(supply_offer_path):
        spatial = offer["spatial_unit_id"]
        province = "BC" if spatial.startswith("bc_") else ("QC" if spatial.startswith("qc_") else "shared")
        commodity_id = offer["commodity_id"]
        quantity_unit = offer["quantity_unit"]
        saw_quantity[province][(commodity_id, quantity_unit)] += number(offer["quantity"])
        saw_cost[province][(commodity_id, quantity_unit)] += number(offer["supply_cost"])
    for province, per_commodity in saw_quantity.items():
        for (commodity_id, quantity_unit), quantity in sorted(per_commodity.items()):
            summary_rows.append(
                {
                    "province": province,
                    "metric_id": "period_supply_quantity",
                    "commodity_id": commodity_id,
                    "facility_class": "",
                    "market_id": "",
                    "value": format_number(quantity),
                    "unit": quantity_unit,
                }
            )
        for (commodity_id, quantity_unit), cost_total in sorted(saw_cost[province].items()):
            summary_rows.append(
                {
                    "province": province,
                    "metric_id": "period_supply_cost_total",
                    "commodity_id": commodity_id,
                    "facility_class": "",
                    "market_id": "",
                    "value": format_number(cost_total),
                    "unit": "CAD",
                }
            )
    write_rows(output_path, SOLUTION_SUMMARY_PER_PROVINCE_COLUMNS, summary_rows)
    return summary_rows


def format_number(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".")


def regenerate_workdir(
    base_dir: Path = DEFAULT_SCANFI_BASE,
    target_dir: Path = DEFAULT_FIXTURE_DIR,
) -> None:
    """Copy ``bc-mini`` fixture tables (minus generated/) to ``bc-qc-mini-2prov`` and add QC tables."""
    if target_dir.exists():
        shutil.rmtree(target_dir)
    shutil.copytree(base_dir, target_dir, ignore=shutil.ignore_patterns("generated"))
    # Append QC rows onto the relevant tables.
    append_qc_rows_to_fixture_tables(target_dir)
    create_qc_diagnostic_subdir(target_dir)


def append_qc_rows_to_fixture_tables(target_dir: Path) -> None:
    """Append QC rows onto the inherited BC tables."""
    # Network nodes
    nodes_path = target_dir / "network_node.csv"
    nodes_existing = read_rows(nodes_path)
    new_nodes = [
        {
            "node_id": "supply_qc_saguenay_01",
            "node_type": "supply",
            "province": "QC",
            "active_from_period": "0",
            "active_to_period": "0",
            "spatial_unit_id": "qc_fmu_saguenay_01",
            "geometry_ref": "data:diagnostic/supply_qc_saguenay_01",
            "facility_class": "",
            "owner_ref": "",
            "license_status": "diagnostic",
        },
        {
            "node_id": "supply_qc_mixedwood_02",
            "node_type": "supply",
            "province": "QC",
            "active_from_period": "0",
            "active_to_period": "0",
            "spatial_unit_id": "qc_fmu_saguenay_02",
            "geometry_ref": "data:diagnostic/supply_qc_mixedwood_02",
            "facility_class": "",
            "owner_ref": "",
            "license_status": "diagnostic",
        },
        {
            "node_id": "supply_qc_disturbance_02",
            "node_type": "supply",
            "province": "QC",
            "active_from_period": "0",
            "active_to_period": "0",
            "spatial_unit_id": "qc_fmu_saguenay_02",
            "geometry_ref": "data:diagnostic/supply_qc_disturbance_02",
            "facility_class": "",
            "owner_ref": "",
            "license_status": "diagnostic",
        },
        {
            "node_id": "supply_qc_residue_01",
            "node_type": "supply",
            "province": "QC",
            "active_from_period": "0",
            "active_to_period": "0",
            "spatial_unit_id": "qc_fmu_saguenay_02",
            "geometry_ref": "data:diagnostic/supply_qc_residue_01",
            "facility_class": "",
            "owner_ref": "",
            "license_status": "diagnostic",
        },
        {
            "node_id": "mill_sawmill_qc",
            "node_type": "facility",
            "province": "QC",
            "active_from_period": "0",
            "active_to_period": "0",
            "spatial_unit_id": "",
            "geometry_ref": "data:diagnostic/mill_sawmill_qc",
            "facility_class": "sawmill",
            "owner_ref": "synthetic",
            "license_status": "diagnostic",
        },
        {
            "node_id": "mill_pulp_qc",
            "node_type": "facility",
            "province": "QC",
            "active_from_period": "0",
            "active_to_period": "0",
            "spatial_unit_id": "",
            "geometry_ref": "data:diagnostic/mill_pulp_qc",
            "facility_class": "pulp",
            "owner_ref": "synthetic",
            "license_status": "diagnostic",
        },
        {
            "node_id": "plant_bioenergy_qc",
            "node_type": "facility",
            "province": "QC",
            "active_from_period": "0",
            "active_to_period": "0",
            "spatial_unit_id": "",
            "geometry_ref": "data:diagnostic/plant_bioenergy_qc",
            "facility_class": "bioenergy",
            "owner_ref": "synthetic",
            "license_status": "diagnostic",
        },
        {
            "node_id": "export_montreal",
            "node_type": "export",
            "province": "QC",
            "active_from_period": "0",
            "active_to_period": "0",
            "spatial_unit_id": "",
            "geometry_ref": "data:diagnostic/export_montreal",
            "facility_class": "export",
            "owner_ref": "synthetic",
            "license_status": "diagnostic",
        },
    ]
    nodes_existing.extend(new_nodes)
    write_rows(nodes_path, list(nodes_existing[0].keys()), nodes_existing)

    # Spatial units
    spatial_path = target_dir / "spatial_unit.csv"
    spatial_existing = read_rows(spatial_path)
    new_spatial = [
        {
            "spatial_unit_id": "qc_fmu_saguenay_01",
            "spatial_unit_type": "diagnostic_fmu",
            "parent_unit_id": "qc",
            "province": "QC",
            "geometry_ref": "data:diagnostic/qc_fmu_saguenay_01",
            "area_ha": "85000",
            "management_regime": "synthetic_even_flow",
            "access_class": "roaded",
            "ownership_or_tenure": "public",
            "source_dataset_id": "qc_5e_ieqm_casfri_v5_mini",
        },
        {
            "spatial_unit_id": "qc_fmu_saguenay_02",
            "spatial_unit_type": "diagnostic_fmu",
            "parent_unit_id": "qc",
            "province": "QC",
            "geometry_ref": "data:diagnostic/qc_fmu_saguenay_02",
            "area_ha": "55000",
            "management_regime": "synthetic_disturbance_mix",
            "access_class": "mixed_access",
            "ownership_or_tenure": "public",
            "source_dataset_id": "qc_5e_ieqm_casfri_v5_mini",
        },
    ]
    spatial_existing.extend(new_spatial)
    write_rows(spatial_path, list(spatial_existing[0].keys()), spatial_existing)

    # Scenario row with the unified scenario_id
    scenario_path = target_dir / "scenario.csv"
    scenario_rows = read_rows(scenario_path)
    for row in scenario_rows:
        row["scenario_id"] = DEFAULT_SCENARIO
        row["scenario_name"] = "BC + QC synthetic 2-province mini-network (P7.1.CASFRI extension)"
    write_rows(scenario_path, list(scenario_rows[0].keys()), scenario_rows)

    # Transport links: add QC routes
    transport_path = target_dir / "transport_link.csv"
    transport_existing = read_rows(transport_path)
    for row in transport_existing:
        row["scenario_id"] = DEFAULT_SCENARIO
    new_links = [
        {
            "scenario_id": DEFAULT_SCENARIO,
            "period": "0",
            "origin_node_id": "supply_qc_saguenay_01",
            "destination_node_id": "mill_sawmill_qc",
            "transport_mode": "truck",
            "distance": "120",
            "distance_unit": "km",
            "route_id": "qc_route_001",
            "segment_lengths": "",
            "speed_limits": "",
            "travel_time": "2.6",
            "transport_cost": "18",
            "transport_cost_unit": "CAD_per_m3",
            "capacity": "40000",
        },
        {
            "scenario_id": DEFAULT_SCENARIO,
            "period": "0",
            "origin_node_id": "supply_qc_mixedwood_02",
            "destination_node_id": "mill_sawmill_qc",
            "transport_mode": "truck",
            "distance": "180",
            "distance_unit": "km",
            "route_id": "qc_route_001b",
            "segment_lengths": "",
            "speed_limits": "",
            "travel_time": "4.0",
            "transport_cost": "22",
            "transport_cost_unit": "CAD_per_m3",
            "capacity": "24000",
        },
        {
            "scenario_id": DEFAULT_SCENARIO,
            "period": "0",
            "origin_node_id": "supply_qc_disturbance_02",
            "destination_node_id": "mill_sawmill_qc",
            "transport_mode": "truck",
            "distance": "210",
            "distance_unit": "km",
            "route_id": "qc_route_001c",
            "segment_lengths": "",
            "speed_limits": "",
            "travel_time": "4.5",
            "transport_cost": "26",
            "transport_cost_unit": "CAD_per_m3",
            "capacity": "18000",
        },
        {
            "scenario_id": DEFAULT_SCENARIO,
            "period": "0",
            "origin_node_id": "supply_qc_saguenay_01",
            "destination_node_id": "export_montreal",
            "transport_mode": "rail",
            "distance": "540",
            "distance_unit": "km",
            "route_id": "qc_route_002",
            "segment_lengths": "",
            "speed_limits": "",
            "travel_time": "10.2",
            "transport_cost": "30",
            "transport_cost_unit": "CAD_per_m3",
            "capacity": "8000",
        },
        {
            "scenario_id": DEFAULT_SCENARIO,
            "period": "0",
            "origin_node_id": "supply_qc_residue_01",
            "destination_node_id": "plant_bioenergy_qc",
            "transport_mode": "truck",
            "distance": "240",
            "distance_unit": "km",
            "route_id": "qc_route_003",
            "segment_lengths": "",
            "speed_limits": "",
            "travel_time": "5.1",
            "transport_cost": "30",
            "transport_cost_unit": "CAD_per_odt",
            "capacity": "9000",
        },
        {
            "scenario_id": DEFAULT_SCENARIO,
            "period": "0",
            "origin_node_id": "mill_sawmill_qc",
            "destination_node_id": "mill_pulp_qc",
            "transport_mode": "truck",
            "distance": "105",
            "distance_unit": "km",
            "route_id": "qc_route_004",
            "segment_lengths": "",
            "speed_limits": "",
            "travel_time": "2.4",
            "transport_cost": "18",
            "transport_cost_unit": "CAD_per_odt",
            "capacity": "16000",
        },
    ]
    transport_existing.extend(new_links)
    write_rows(transport_path, list(transport_existing[0].keys()), transport_existing)

    # Markets: add QC markets
    markets_path = target_dir / "market.csv"
    markets_existing = read_rows(markets_path)
    new_markets = [
        {
            "market_id": "market_lumber_qc",
            "product_commodity_id": "lumber",
            "seller_group": "sawmill",
            "buyer_group": "construction",
            "minimum_demand": "0",
            "maximum_demand": "50000",
            "price": "200",
            "price_unit": "CAD_per_m3",
            "report_group": "primary_products",
            "sankey_group": "lumber",
        },
        {
            "market_id": "market_bioenergy_qc",
            "product_commodity_id": "bioenergy",
            "seller_group": "bioenergy",
            "buyer_group": "local_energy",
            "minimum_demand": "0",
            "maximum_demand": "120000",
            "price": "16",
            "price_unit": "CAD_per_GJ",
            "report_group": "energy_products",
            "sankey_group": "bioenergy",
        },
        {
            "market_id": "market_log_export_qc",
            "product_commodity_id": "log",
            "seller_group": "export",
            "buyer_group": "export_market",
            "minimum_demand": "0",
            "maximum_demand": "8000",
            "price": "98",
            "price_unit": "CAD_per_m3",
            "report_group": "exports",
            "sankey_group": "log_export",
        },
    ]
    markets_existing.extend(new_markets)
    write_rows(markets_path, list(markets_existing[0].keys()), markets_existing)

    # Export sink: rewrite scenario_id and add QC export
    export_sink_path = target_dir / "export_sink.csv"
    export_sink_existing = read_rows(export_sink_path)
    for row in export_sink_existing:
        row["scenario_id"] = DEFAULT_SCENARIO
    new_sink = [
        {
            "scenario_id": DEFAULT_SCENARIO,
            "period": "0",
            "export_node_id": "export_montreal",
            "commodity_id": "log",
            "price_or_netback": "98",
            "price_unit": "CAD_per_m3",
            "maximum_export": "8000",
            "destination_region": "Atlantic Rim",
            "trade_case": "diagnostic",
        },
    ]
    export_sink_existing.extend(new_sink)
    write_rows(export_sink_path, list(export_sink_existing[0].keys()), export_sink_existing)

    # Technology: add QC facilities
    tech_path = target_dir / "technology.csv"
    tech_existing = read_rows(tech_path)
    new_techs = [
        {
            "technology_id": "tech_sawmill_qc",
            "facility_node_id": "mill_sawmill_qc",
            "feedstock_commodity_id": "log",
            "primary_product_id": "lumber",
            "infeed_rate": "1",
            "infeed_rate_unit": "m3_infeed_per_m3_log",
            "operating_cost": "26",
            "operating_cost_unit": "CAD_per_m3_log",
            "throughput_id": "sawmill_qc_base",
            "mutually_exclusive_group_id": "",
            "minimum_production": "0",
            "maximum_capacity": "44000",
            "capital_status": "existing",
        },
        {
            "technology_id": "tech_pulp_qc",
            "facility_node_id": "mill_pulp_qc",
            "feedstock_commodity_id": "woodchips",
            "primary_product_id": "pulp",
            "infeed_rate": "1",
            "infeed_rate_unit": "odt_infeed_per_odt_chip",
            "operating_cost": "32",
            "operating_cost_unit": "CAD_per_odt_chip",
            "throughput_id": "pulp_qc_chips_base",
            "mutually_exclusive_group_id": "",
            "minimum_production": "0",
            "maximum_capacity": "20000",
            "capital_status": "existing",
        },
        {
            "technology_id": "tech_bioenergy_qc",
            "facility_node_id": "plant_bioenergy_qc",
            "feedstock_commodity_id": "logging_residue",
            "primary_product_id": "bioenergy",
            "infeed_rate": "1",
            "infeed_rate_unit": "odt_infeed_per_odt_logging_residue",
            "operating_cost": "10",
            "operating_cost_unit": "CAD_per_odt_logging_residue",
            "throughput_id": "bioenergy_qc_base",
            "mutually_exclusive_group_id": "",
            "minimum_production": "0",
            "maximum_capacity": "12000",
            "capital_status": "existing",
        },
        {
            "technology_id": "tech_export_logs_qc",
            "facility_node_id": "export_montreal",
            "feedstock_commodity_id": "log",
            "primary_product_id": "log",
            "infeed_rate": "1",
            "infeed_rate_unit": "m3_infeed_per_m3_log",
            "operating_cost": "5",
            "operating_cost_unit": "CAD_per_m3_log",
            "throughput_id": "export_log_qc_base",
            "mutually_exclusive_group_id": "",
            "minimum_production": "0",
            "maximum_capacity": "8000",
            "capital_status": "export_option",
        },
    ]
    tech_existing.extend(new_techs)
    write_rows(tech_path, list(tech_existing[0].keys()), tech_existing)

    # Technology flows: add QC flow rows
    tech_flow_path = target_dir / "technology_flow.csv"
    tech_flow_existing = read_rows(tech_flow_path)
    new_flows = [
        {
            "technology_id": "tech_sawmill_qc",
            "flow_type": "output",
            "commodity_id": "lumber",
            "coefficient_or_quantity": "0.49",
            "unit": "m3_per_m3_log",
            "basis_commodity_id": "log",
            "basis_unit": "m3",
            "conversion_source": "synthetic_qc_mini",
            "loss_factor": "0",
        },
        {
            "technology_id": "tech_sawmill_qc",
            "flow_type": "byproduct",
            "commodity_id": "woodchips",
            "coefficient_or_quantity": "0.21",
            "unit": "odt_per_m3_log",
            "basis_commodity_id": "log",
            "basis_unit": "m3",
            "conversion_source": "synthetic_qc_mini",
            "loss_factor": "0",
        },
        {
            "technology_id": "tech_sawmill_qc",
            "flow_type": "byproduct",
            "commodity_id": "sawdust_shavings",
            "coefficient_or_quantity": "0.07",
            "unit": "odt_per_m3_log",
            "basis_commodity_id": "log",
            "basis_unit": "m3",
            "conversion_source": "synthetic_qc_mini",
            "loss_factor": "0",
        },
        {
            "technology_id": "tech_sawmill_qc",
            "flow_type": "byproduct",
            "commodity_id": "hogfuel",
            "coefficient_or_quantity": "0.03",
            "unit": "odt_per_m3_log",
            "basis_commodity_id": "log",
            "basis_unit": "m3",
            "conversion_source": "synthetic_qc_mini",
            "loss_factor": "0",
        },
        {
            "technology_id": "tech_pulp_qc",
            "flow_type": "output",
            "commodity_id": "pulp",
            "coefficient_or_quantity": "0.92",
            "unit": "odt_per_odt_chip",
            "basis_commodity_id": "woodchips",
            "basis_unit": "odt",
            "conversion_source": "synthetic_qc_mini",
            "loss_factor": "0.08",
        },
        {
            "technology_id": "tech_bioenergy_qc",
            "flow_type": "output",
            "commodity_id": "bioenergy",
            "coefficient_or_quantity": "12.0",
            "unit": "GJ_per_odt_logging_residue",
            "basis_commodity_id": "logging_residue",
            "basis_unit": "odt",
            "conversion_source": "synthetic_qc_mini",
            "loss_factor": "0",
        },
        {
            "technology_id": "tech_export_logs_qc",
            "flow_type": "output",
            "commodity_id": "log",
            "coefficient_or_quantity": "1.0",
            "unit": "m3_per_m3_log",
            "basis_commodity_id": "log",
            "basis_unit": "m3",
            "conversion_source": "synthetic_qc_mini",
            "loss_factor": "0",
        },
    ]
    tech_flow_existing.extend(new_flows)
    write_rows(tech_flow_path, list(tech_flow_existing[0].keys()), tech_flow_existing)

    # Run provenance: replace with two-province run id
    run_provenance_path = target_dir / "run_provenance.csv"
    rows = read_rows(run_provenance_path)
    if len(rows) != 1:
        raise ValueError(
            f"Expected one run_provenance row in {run_provenance_path}, found {len(rows)}",
        )
    row = rows[0]
    row["scenario_id"] = DEFAULT_SCENARIO
    row["run_id"] = DEFAULT_RUN_ID
    row["ws3_run_id"] = DEFAULT_WS3_RUN_ID
    row["lp_run_id"] = DEFAULT_RUN_ID
    row["data_version"] = "diagnostic_bc_qc_mini_2prov"
    row["source_manifest_ref"] = (
        "metadata/source-crosswalks/qc_casfri_v5_schema.md"
    )
    row["notes"] = (
        "Two-province BC + QC diagnostic fixture (P7.1.CASFRI extension). "
        "BC supply regenerated from SCANFI mini-shape; QC supply generated "
        "through the casfri_qc_reader adapter into ws3-shape rows; both feed "
        "the same downstream LP via the unified bc-qc-mini-2prov supply table."
    )
    write_rows(run_provenance_path, list(row.keys()), rows)


def create_qc_diagnostic_subdir(target_dir: Path) -> None:
    """Generate the QC diagnostic mini fixture files in ``qc-diagnostic/``."""
    qc_diag_dir = target_dir / "qc-diagnostic"
    qc_diag_dir.mkdir(parents=True, exist_ok=True)

    write_qc_yield_rules(qc_diag_dir / "qc_diagnostic_yield_rules.csv")

    cells_csv_path = qc_diag_dir / "qc_diagnostic_inventory_cells.csv"
    cell_rows = [
        {
            "inventory_cell_id": "qc_cell_boreal_softwood_sawlog_001",
            "scenario_id": "qc_mini_001",
            "period": "0",
            "province": "QC",
            "diagnostic_region": "qc_boreal_softwood_sawlog",
            "spatial_unit_id": "qc_fmu_saguenay_01",
            "supply_node_id": "supply_qc_saguenay_01",
            "geocode": "21M_0000000001",
            "essence_1": "SAB",
            "essence_qualite": "SAB",
            "essence_pct_1": "55",
            "essence_2": "EPB",
            "essence_pct_2": "30",
            "essence_3": "BOP",
            "essence_pct_3": "15",
            "cl_age": "1070",
            "cl_dens": "B",
            "cl_haut": "3",
            "cl_drai": "20",
            "type_eco": "MS2",
            "hauteur": "16",
            "superficie": "16000000",
            "iqs_pot": "12",
            "b_arbv_tot": "85",
            "value_policy": "qc_species_resolved_via_lookup",
            "source_manifest": "metadata/source-crosswalks/qc_casfri_v5_schema.md",
        },
        {
            "inventory_cell_id": "qc_cell_boreal_softwood_sawlog_002",
            "scenario_id": "qc_mini_001",
            "period": "0",
            "province": "QC",
            "diagnostic_region": "qc_boreal_softwood_sawlog",
            "spatial_unit_id": "qc_fmu_saguenay_01",
            "supply_node_id": "supply_qc_saguenay_01",
            "geocode": "21M_0000000002",
            "essence_1": "EPB",
            "essence_qualite": "EPB",
            "essence_pct_1": "60",
            "essence_2": "SAB",
            "essence_pct_2": "25",
            "essence_3": "PEU",
            "essence_pct_3": "15",
            "cl_age": "1090",
            "cl_dens": "C",
            "cl_haut": "4",
            "cl_drai": "30",
            "type_eco": "MS22",
            "hauteur": "22",
            "superficie": "11000000",
            "iqs_pot": "14",
            "b_arbv_tot": "110",
            "value_policy": "qc_species_resolved_via_lookup",
            "source_manifest": "metadata/source-crosswalks/qc_casfri_v5_schema.md",
        },
        {
            "inventory_cell_id": "qc_cell_mixedwood_sawlog_001",
            "scenario_id": "qc_mini_001",
            "period": "0",
            "province": "QC",
            "diagnostic_region": "qc_mixedwood_sawlog",
            "spatial_unit_id": "qc_fmu_saguenay_02",
            "supply_node_id": "supply_qc_mixedwood_02",
            "geocode": "21M_0000000003",
            "essence_1": "BOJ",
            "essence_qualite": "BOJ",
            "essence_pct_1": "40",
            "essence_2": "SAB",
            "essence_pct_2": "35",
            "essence_3": "PEU",
            "essence_pct_3": "25",
            "cl_age": "1050",
            "cl_dens": "B",
            "cl_haut": "3",
            "cl_drai": "20",
            "type_eco": "MS2",
            "hauteur": "16",
            "superficie": "9000000",
            "iqs_pot": "13",
            "b_arbv_tot": "90",
            "value_policy": "qc_species_resolved_via_lookup",
            "source_manifest": "metadata/source-crosswalks/qc_casfri_v5_schema.md",
        },
        {
            "inventory_cell_id": "qc_cell_disturbance_mix_001",
            "scenario_id": "qc_mini_001",
            "period": "0",
            "province": "QC",
            "diagnostic_region": "qc_disturbance_mix",
            "spatial_unit_id": "qc_fmu_saguenay_02",
            "supply_node_id": "supply_qc_disturbance_02",
            "geocode": "21M_0000000004",
            "essence_1": "MEH",
            "essence_qualite": "MEH",
            "essence_pct_1": "45",
            "essence_2": "SAB",
            "essence_pct_2": "30",
            "essence_3": "BOP",
            "essence_pct_3": "25",
            "cl_age": "1070",
            "cl_dens": "D",
            "cl_haut": "3",
            "cl_drai": "30",
            "type_eco": "MS22",
            "hauteur": "13",
            "superficie": "7000000",
            "iqs_pot": "11",
            "b_arbv_tot": "70",
            "value_policy": "qc_species_resolved_via_lookup",
            "source_manifest": "metadata/source-crosswalks/qc_casfri_v5_schema.md",
        },
        {
            "inventory_cell_id": "qc_cell_residue_candidate_001",
            "scenario_id": "qc_mini_001",
            "period": "0",
            "province": "QC",
            "diagnostic_region": "qc_residue_candidate",
            "spatial_unit_id": "qc_fmu_saguenay_02",
            "supply_node_id": "supply_qc_residue_01",
            "geocode": "21M_0000000005",
            "essence_1": "SAB",
            "essence_qualite": "SAB",
            "essence_pct_1": "70",
            "essence_2": "EPB",
            "essence_pct_2": "30",
            "essence_3": "",
            "essence_pct_3": "0",
            "cl_age": "1010",
            "cl_dens": "C",
            "cl_haut": "2",
            "cl_drai": "20",
            "type_eco": "MS2",
            "hauteur": "10",
            "superficie": "12000000",
            "iqs_pot": "13",
            "b_arbv_tot": "60",
            "value_policy": "qc_species_resolved_via_lookup",
            "source_manifest": "metadata/source-crosswalks/qc_casfri_v5_schema.md",
        },
    ]
    fieldnames = [
        "inventory_cell_id", "scenario_id", "period", "province", "diagnostic_region",
        "spatial_unit_id", "supply_node_id", "geocode", "essence_1", "essence_qualite",
        "essence_pct_1", "essence_2", "essence_pct_2", "essence_3", "essence_pct_3",
        "cl_age", "cl_dens", "cl_haut", "cl_drai", "type_eco", "hauteur", "superficie",
        "iqs_pot", "b_arbv_tot", "value_policy", "source_manifest",
    ]
    write_rows(cells_csv_path, fieldnames, cell_rows)


def build_graph_tables(target_dir: Path) -> tuple[int, int]:
    tables = {
        table: graph.read_table(target_dir, table)
        for table in graph.TABLE_COLUMNS
    }
    graph.validate_references(tables)
    nodes, arcs = graph.build_graph(tables)
    output_dir = target_dir / "generated"
    graph.write_table(output_dir / "graph_nodes.csv", graph.NODE_COLUMNS, nodes)
    graph.write_table(output_dir / "graph_arcs.csv", graph.ARC_COLUMNS, arcs)
    graph.write_table(
        output_dir / "graph_summary.csv", graph.SUMMARY_COLUMNS, graph.build_summary(nodes, arcs),
    )
    return len(nodes), len(arcs)


# ---------------------------------------------------------------------------
# Real-coefficient substitution path (P7.1.CASFRI.2)
# ---------------------------------------------------------------------------

REAL_BC_STAND_AREA_HA: dict[str, float] = {
    "BC_coast": 440.0,
    "BC_interior": 440.0,
}
REAL_QC_STAND_AREA_HA: float = 9.0
REAL_QC_RESIDUE_STAND_AREA_HA: float = 90.0
REAL_BC_RESIDUE_ODT_PER_HA: float = 7.7
REAL_QC_RESIDUE_ODT_PER_HA: float = 7.2
REAL_RESIDUE_ODT_PER_M3_LOG: float = 0.05

SPECIES_CODE_ALIASES: dict[str, str] = {
    "PEU": "PET",
    "PE": "PET",
    "PEG": "PET",
    "PEB": "PET",
    "PED": "PET",
    "PLI": "Pl",
    "PL": "Pl",
    "PLi": "Pl",
    "SxS": "Sx",
    "SXS": "Sx",
    "Sx": "Sx",
    "SW": "Sx",
}

RESIDUE_INVENTORY_CELL_SUFFIXES: tuple[str, ...] = ("_residue_candidate_001",)


def _resolve_species_alias(species_code: str) -> str:
    """Map a producer-native species code to the canonical code in
    ``REAL_COEFFICIENT_ROWS``.

    The lookup is permissive (case-insensitive on the key side) but
    conservative on output — unknown codes pass through unchanged so
    downstream ``species_lookup`` call sites still surface the
    unmapped status through a ``None`` row.
    """
    if not species_code:
        return ""
    code = species_code.strip()
    if code in SPECIES_CODE_ALIASES:
        return SPECIES_CODE_ALIASES[code]
    if code.upper() in SPECIES_CODE_ALIASES:
        return SPECIES_CODE_ALIASES[code.upper()]
    return code


def _bc_region_for_bec_zone(bec_zone: str) -> str:
    """Classify a BC VRI polygon into ``BC_coast`` or ``BC_interior``."""
    zone = (bec_zone or "").strip().upper()
    if zone in ("CWH", "CDF", "MH"):
        return "BC_coast"
    return "BC_interior"


def _bc_region_lookup_chain(bec_region: str) -> tuple[str, ...]:
    """Yield the candidate ``region`` keys to try against ``species_lookup``.

    The chain is ``(bec_region, "BC")`` — the latter is a fallback for
    species whose single-jurisdiction real-coefficient row is keyed
    against the plain ``"BC"`` region rather than the split sub-region
    (e.g. ``AT`` / ``BL`` are keyed as ``"BC"`` because their vol80
    proxy does not split across BEC-zone categories).
    """
    return (bec_region, "BC")


def _is_residue_inventory_cell(cell_id: str) -> bool:
    """Return True if the QC inventory cell should be classified as residue-only."""
    cell_name = (cell_id or "").strip()
    return any(cell_name.endswith(suffix) for suffix in RESIDUE_INVENTORY_CELL_SUFFIXES)


def _safe_float(value: str | float | int | None, default: float | None = None) -> float | None:
    """Coerce a raw CSV cell to a float, returning ``default`` on failure."""
    if value is None:
        return default
    try:
        return float(str(value).strip())
    except (ValueError, TypeError):
        return default


def compute_bc_qc_real_supply_quantities(
    coef_lookup_fn,
    qc_mini_dir: Path,
    bc_mini_dir: Path,
    *,
    age_curve_k: float = 0.022,
) -> tuple[float, float, float, float, int]:
    """Compute BC + QC supply quantities via the real-coefficient path.

    Reads the BC VRI stub at ``bc_mini_dir / "sample.csv"`` (3 polygons)
    and the QC diagnostic inventory cells at
    ``qc_mini_dir / "qc-diagnostic" / "qc_diagnostic_inventory_cells.csv"``
    (5 cells: 4 log + 1 residue). For each polygon/cell, applies
    per-species volume-at-age from ``coef_lookup_fn`` plus a Chapman-
    Richards saturating curve, then aggregates by commodity (log m³ or
    logging-residue odt).

    The function is the **substitution target** for
    ``run_full_two_province_variant`` (P7.1.CASFRI.2): instead of
    reading stub quantities (VRI area → LP quantity at 1 m³/ha and
    placeholder residue rules), it derives the same totals from
    ``fresh_fibre.gauthier_yield.REAL_COEFFICIENT_ROWS` so the LP
    sees real-coefficient-defined numbers rather than hard-coded
    placeholders.

    Args:
        coef_lookup_fn: callable accepting ``(species_code, region)``
            and returning a coefficient dict (or ``None``). The
            function ``fresh_fibre.gauthier_yield.species_lookup``
            is the canonical implementation; tests inject custom
            callables to keep the unit test hermetic.
        qc_mini_dir: path to the BC + QC mini-2prov fixture root (the
            directory that contains ``qc-diagnostic/``).
        bc_mini_dir: path to the BC VRI sample fixture directory
            (defaults to ``tests/fixtures/bc_vri_sample`` in P7.1).
        age_curve_k: optional Chapman-Richards rate forwarded to
            ``volume_at_age_for_species``; default ``0.022``.

    Returns:
        ``(bc_log_m3, bc_residue_odt, qc_log_m3, qc_residue_odt,
        yield_rule_count)`` where the first four are aggregate totals
        in the documented unit and ``yield_rule_count`` is the number
        of distinct per-polygon / per-cell yield rules applied.

    Raises:
        FileNotFoundError: when ``bc_vri_csv`` or ``qc_cells_csv`` is
            absent.
        ValueError: when either file yields zero rows.
    """
    bc_vri_csv = Path(bc_mini_dir) / "sample.csv"
    qc_cells_csv = Path(qc_mini_dir) / "qc-diagnostic" / "qc_diagnostic_inventory_cells.csv"

    if not bc_vri_csv.exists():
        raise FileNotFoundError(f"BC VRI stub CSV not found: {bc_vri_csv}")
    if not qc_cells_csv.exists():
        raise FileNotFoundError(f"QC diagnostic cells CSV not found: {qc_cells_csv}")

    bc_rows = read_rows(bc_vri_csv)
    if not bc_rows:
        raise ValueError(f"BC VRI stub {bc_vri_csv} produced zero polygons")

    bc_log_total = 0.0
    bc_residue_total = 0.0
    bc_yield_rules = 0
    for row in bc_rows:
        bc_yield_rules += 1
        region = _bc_region_for_bec_zone(row.get("BEC_ZONE", ""))
        stand_area = REAL_BC_STAND_AREA_HA[region]
        age = _safe_float(row.get("AGE_YR"), default=80.0) or 80.0
        weighted_log_per_ha = 0.0
        for rank in range(1, 7):
            raw_code = (row.get(f"SPECIES_CD_{rank}", "") or "").strip()
            pct = _safe_float(row.get(f"SPECIES_PCT_{rank}"))
            if not raw_code or pct is None or pct <= 0.0:
                continue
            code_canonical = _resolve_species_alias(raw_code)
            resolved_region = ""
            for candidate_region in _bc_region_lookup_chain(region):
                if coef_lookup_fn(code_canonical, candidate_region) is not None:
                    resolved_region = candidate_region
                    break
            if not resolved_region:
                continue
            yield_at_age = volume_at_age_for_species(
                code_canonical, resolved_region, age, age_curve_k=age_curve_k,
            )
            weighted_log_per_ha += yield_at_age * pct / 100.0
        log_volume = weighted_log_per_ha * stand_area
        bc_log_total += log_volume
        bc_residue_total += log_volume * REAL_RESIDUE_ODT_PER_M3_LOG

    qc_rows = read_rows(qc_cells_csv)
    if not qc_rows:
        raise ValueError(f"QC diagnostic cells {qc_cells_csv} produced zero rows")

    qc_log_total = 0.0
    qc_residue_total = 0.0
    qc_yield_rules = 0
    for cell in qc_rows:
        qc_yield_rules += 1
        cell_id = cell.get("inventory_cell_id", "")
        age_bucket_raw = _safe_float(cell.get("cl_age"))
        age = float(int(age_bucket_raw // 10)) if age_bucket_raw and age_bucket_raw > 0 else 80.0
        weighted_log_per_ha = 0.0
        for slot in (1, 2, 3):
            raw_code = (cell.get(f"essence_{slot}", "") or "").strip()
            pct = _safe_float(cell.get(f"essence_pct_{slot}"))
            if not raw_code or pct is None or pct <= 0.0:
                continue
            code_canonical = _resolve_species_alias(raw_code)
            if coef_lookup_fn(code_canonical, "QC") is None:
                continue
            yield_at_age = volume_at_age_for_species(
                code_canonical, "QC", age, age_curve_k=age_curve_k,
            )
            weighted_log_per_ha += yield_at_age * pct / 100.0
        if _is_residue_inventory_cell(cell_id):
            qc_residue_total += REAL_QC_RESIDUE_ODT_PER_HA * REAL_QC_RESIDUE_STAND_AREA_HA
        else:
            log_volume = weighted_log_per_ha * REAL_QC_STAND_AREA_HA
            qc_log_total += log_volume
            qc_residue_total += log_volume * REAL_RESIDUE_ODT_PER_M3_LOG

    yield_rule_count = bc_yield_rules + qc_yield_rules
    return (
        bc_log_total,
        bc_residue_total,
        qc_log_total,
        qc_residue_total,
        yield_rule_count,
    )


def _run_full_two_province_real_yield_skeleton(
    base_dir: Path = DEFAULT_SCANFI_BASE,
    fixture_dir: Path = DEFAULT_FIXTURE_DIR,
    bc_vri_stub_dir: Path | None = None,
) -> dict[str, object]:
    """Lightweight scaffold that exercises ``compute_bc_qc_real_supply_quantities``
    against the BC VRI stub and the regenerated QC mini fixture.

    Used by ``tests/test_bc_qc_mini_2prov.py`` to assert the real-coefficient
    path changes the LP objective from the placeholder-era value. The full
    two-province synthetic-bounded LP run is intentionally NOT invoked here;
    this scaffold reads the diagnostic-real-supply quantities only and
    reports them so the test can compare against the documented ranges.

    Args:
        base_dir: path to the BC mini base fixture.
        fixture_dir: path to the BC + QC mini-2prov fixture root.
        bc_vri_stub_dir: optional explicit path to the BC VRI sample
            fixture; defaults to ``tests/fixtures/bc_vri_sample``.

    Returns:
        Summary dict with the five aggregate totals from
        ``compute_bc_qc_real_supply_quantities`` plus their
        ``yield_rule_count``.
    """
    if bc_vri_stub_dir is None:
        bc_vri_stub_dir = REPO_ROOT_FOR_REAL_YIELD / "tests" / "fixtures" / "bc_vri_sample"
    if not fixture_dir.exists():
        regenerate_workdir(base_dir=base_dir, target_dir=fixture_dir)
    bc_log, bc_residue, qc_log, qc_residue, yield_rule_count = (
        compute_bc_qc_real_supply_quantities(
            coef_lookup_fn=species_lookup,
            qc_mini_dir=fixture_dir,
            bc_mini_dir=bc_vri_stub_dir,
        )
    )
    return {
        "fixture_dir": str(fixture_dir),
        "bc_log_m3": bc_log,
        "bc_residue_odt": bc_residue,
        "qc_log_m3": qc_log,
        "qc_residue_odt": qc_residue,
        "yield_rule_count": yield_rule_count,
    }


def _substitute_real_yields_into_supply_offer(
    fixture_dir: Path,
    bc_vri_stub_dir: Path,
    *,
    coef_lookup_fn=None,
) -> dict[str, float]:
    """Rewrite ``fixture_dir/supply_offer.csv`` with real-coefficient quantities.

    Reads the placeholder-era ``supply_offer.csv`` produced by
    ``run_full_two_province_variant``, scales each row's quantity by the
    ratio (real_total / placeholder_total) per (province, commodity)
    bucket, and writes a side-by-side real-yield ``supply_offer.csv``
    to ``fixture_dir/supply_offer_real.csv``. The original
    ``supply_offer.csv`` is untouched; the real-yield path reads its
    own copy.

    Args:
        fixture_dir: BC + QC mini-2prov fixture root (must already
            contain ``supply_offer.csv`` from a prior placeholder run).
        bc_vri_stub_dir: BC VRI sample fixture directory.
        coef_lookup_fn: optional override for the species-coefficient
            lookup; defaults to ``fresh_fibre.gauthier_yield.species_lookup``.

    Returns:
        Summary dict with the per-bucket placeholder-vs-real totals and
        the aggregate delta the LP sees once the real-yield table is
        consumed.
    """
    if coef_lookup_fn is None:
        from fresh_fibre.gauthier_yield import species_lookup as default_lookup_fn
        coef_lookup_fn = default_lookup_fn

    supply_offer_path = fixture_dir / "supply_offer.csv"
    if not supply_offer_path.exists():
        raise FileNotFoundError(
            f"Placeholder supply_offer.csv missing for substitution: {supply_offer_path}",
        )

    bc_log_real, bc_residue_real, qc_log_real, qc_residue_real, _yield_rule_count = (
        compute_bc_qc_real_supply_quantities(
            coef_lookup_fn=coef_lookup_fn,
            qc_mini_dir=fixture_dir,
            bc_mini_dir=bc_vri_stub_dir,
        )
    )

    placeholder_rows = read_rows(supply_offer_path)
    placeholder_totals: dict[tuple[str, str], float] = defaultdict(float)
    for row in placeholder_rows:
        province = "BC" if row["spatial_unit_id"].startswith("bc_") else (
            "QC" if row["spatial_unit_id"].startswith("qc_") else "shared"
        )
        key = (province, row["commodity_id"])
        placeholder_totals[key] += number(row["quantity"])

    real_targets: dict[tuple[str, str], float] = {
        ("BC", "log"): bc_log_real,
        ("BC", "logging_residue"): bc_residue_real,
        ("QC", "log"): qc_log_real,
        ("QC", "logging_residue"): qc_residue_real,
    }

    scaled_rows: list[dict[str, object]] = []
    for row in placeholder_rows:
        province = "BC" if row["spatial_unit_id"].startswith("bc_") else (
            "QC" if row["spatial_unit_id"].startswith("qc_") else "shared"
        )
        new_row = dict(row)
        bucket = (province, row["commodity_id"])
        placeholder_total = placeholder_totals.get(bucket, 0.0)
        target_total = real_targets.get(bucket)
        if (
            placeholder_total > 0.0
            and target_total is not None
            and row["commodity_id"] in {"log", "logging_residue"}
        ):
            ratio = target_total / placeholder_total
            original_quantity = number(row["quantity"])
            new_row["quantity"] = format_number(original_quantity * ratio)
        scaled_rows.append(new_row)

    real_supply_offer_path = fixture_dir / "supply_offer_real.csv"
    fieldnames = list(placeholder_rows[0].keys()) if placeholder_rows else SUPPLY_OFFER_COLUMNS
    write_rows(real_supply_offer_path, fieldnames, scaled_rows)
    shifted_quantity = sum(
        number(row["quantity"]) for row in scaled_rows
    ) - sum(number(row["quantity"]) for row in placeholder_rows)
    return {
        "bc_log_real": bc_log_real,
        "bc_residue_real": bc_residue_real,
        "qc_log_real": qc_log_real,
        "qc_residue_real": qc_residue_real,
        "shifted_quantity_total_m3_odt": shifted_quantity,
        "real_supply_offer_path": str(real_supply_offer_path),
    }


def run_full_two_province_real_yield_variant(
    fixture_dir: Path = DEFAULT_FIXTURE_DIR,
    base_dir: Path = DEFAULT_SCANFI_BASE,
    scanfi_fixture_dir: Path = DEFAULT_SCANFI_DIAG_DIR,
    bc_vri_stub_dir: Path | None = None,
) -> dict[str, object]:
    """Run the BC + QC two-province LP variant with real-coefficient yields.

    The variant is layered on top of ``run_full_two_province_variant``:

    1. Bootstrap with a placeholder-era run (writes ``supply_offer.csv``).
    2. Substitute real-coefficient quantities into ``supply_offer_real.csv``
       via ``_substitute_real_yields_into_supply_offer``.
    3. Re-validate the contract and re-run the LP solve against the
       real-yield copy of the supply_offer table.

    The function returns both runs' objective values and the
    placeholder-vs-real totals so ``tests/test_bc_qc_mini_2prov.py``
    can assert that real-coefficient substitution changed the LP
    outcome.

    Args:
        fixture_dir: BC + QC mini-2prov fixture root.
        base_dir: BC mini base fixture path (default the SCANFI base).
        scanfi_fixture_dir: legacy argument retained for backward
            compatibility; no longer the BC-side authority.
        bc_vri_stub_dir: optional explicit BC VRI sample fixture
            directory (defaults to ``tests/fixtures/bc_vri_sample``).

    Returns:
        Dict with both LP runs' status / objective / quantity totals
        and the substitution summary.
    """
    if bc_vri_stub_dir is None:
        bc_vri_stub_dir = REPO_ROOT_FOR_REAL_YIELD / "tests" / "fixtures" / "bc_vri_sample"
    placeholder_result = run_full_two_province_variant(
        fixture_dir=fixture_dir,
        base_dir=base_dir,
        scanfi_fixture_dir=scanfi_fixture_dir,
        regenerate_workdir_flag=True,
    )
    substitution = _substitute_real_yields_into_supply_offer(
        fixture_dir=fixture_dir,
        bc_vri_stub_dir=bc_vri_stub_dir,
    )
    real_supply_offer_path = Path(substitution["real_supply_offer_path"])
    placeholder_supply_offer = fixture_dir / "supply_offer.csv"
    try:
        if placeholder_supply_offer.exists():
            backup_path = fixture_dir / "supply_offer_placeholder.csv"
            shutil.copyfile(placeholder_supply_offer, backup_path)
        shutil.copyfile(real_supply_offer_path, placeholder_supply_offer)
        node_count, arc_count = build_graph_tables(fixture_dir)
        lp_result = build_and_solve(fixture_dir, fixture_dir / "generated")
        contract_rows = validate_contract(fixture_dir)
        assert_contract_ok(contract_rows)
    finally:
        # Restore the placeholder supply_offer.csv so subsequent
        # regression tests can repeat the bootstrap path.
        backup_path = fixture_dir / "supply_offer_placeholder.csv"
        if backup_path.exists():
            shutil.copyfile(backup_path, placeholder_supply_offer)
            backup_path.unlink()
    real_bc_supply_rows = read_rows(real_supply_offer_path)
    return {
        "fixture_dir": str(fixture_dir),
        "placeholder": {
            "lp_status": placeholder_result["lp_status"],
            "lp_objective_value": placeholder_result["lp_objective_value"],
            "bc_quantity_m3": placeholder_result["bc_quantity_m3"],
            "qc_quantity_m3": placeholder_result["qc_quantity_m3"],
        },
        "real_yield": {
            "lp_status": lp_result["status"],
            "lp_objective_value": lp_result["objective_value"],
            "bc_log_real": substitution["bc_log_real"],
            "qc_log_real": substitution["qc_log_real"],
            "bc_residue_real": substitution["bc_residue_real"],
            "qc_residue_real": substitution["qc_residue_real"],
            "supply_rows": real_bc_supply_rows,
            "graph_nodes": node_count,
            "graph_arcs": arc_count,
        },
        "substitution": substitution,
    }


def run_full_two_province_variant(
    fixture_dir: Path = DEFAULT_FIXTURE_DIR,
    base_dir: Path = DEFAULT_SCANFI_BASE,
    scanfi_fixture_dir: Path = DEFAULT_SCANFI_DIAG_DIR,
    regenerate_workdir_flag: bool = True,
) -> dict[str, object]:
    """Run the full BC + QC two-province LP variant.

    The ``scanfi_fixture_dir`` argument is retained for backward
    compatibility with callers that still reference the old BC supply
    source. It is **no longer** the BC-side authority — the
    ``run_bc_supply`` wrapper now reads the BC VRI stub (or the real
    BC VRI File Geodatabase in production); ``scanfi_fixture_dir`` is
    accepted but unused.
    """
    if regenerate_workdir_flag:
        regenerate_workdir(base_dir=base_dir, target_dir=fixture_dir)

    bc_supply = run_bc_supply(
        gdb_path=DEFAULT_BC_VRI_CSV_STUB,
        bc_mini_dir=base_dir,
        output_dir=fixture_dir,
    )
    qc_supply = run_qc_supply(
        qc_di_dir=fixture_dir / "qc-diagnostic",
        output_dir=fixture_dir,
        translation_dir=DEFAULT_TRANSLATION_TABLE_DIR,
    )

    # Reference scanfi_fixture_dir so mypy / pyflakes recognise that the
    # legacy parameter is intentionally accepted-but-unused here.
    _ = scanfi_fixture_dir

    supply_offer_path = merge_supply_offers(
        output_dir=fixture_dir,
        bc_supply_offer_path=bc_supply["supply_offer_path"],
        qc_supply_offer_path=qc_supply["supply_offer_path"],
        scenario_id=DEFAULT_SCENARIO,
    )

    node_count, arc_count = build_graph_tables(fixture_dir)
    lp_result = build_and_solve(fixture_dir, fixture_dir / "generated")

    contract_rows = validate_contract(fixture_dir)
    write_rows(
        fixture_dir / "generated" / "contract_validation.csv",
        VALIDATION_COLUMNS,
        contract_rows,
    )
    assert_contract_ok(contract_rows)

    supply_comparison_rows = build_supply_comparison(
        bc_supply["supply_offer_path"],
        qc_supply["supply_offer_path"],
        fixture_dir / "generated" / "bc_qc_mini_supply_comparison.csv",
    )
    pathway_rows = build_pathway_flow(
        fixture_dir / "generated" / "solution_flow.csv",
        fixture_dir / "generated" / "bc_qc_mini_pathway_flow.csv",
    )
    summary_rows = build_solution_summary_per_province(
        base_dir=fixture_dir,
        output_path=fixture_dir / "generated" / "bc_qc_mini_solution_summary.csv",
    )

    bc_supply_rows = read_rows(bc_supply["supply_offer_path"])
    qc_supply_rows = read_rows(qc_supply["supply_offer_path"])

    def quantity_sum_by_unit(rows: list[dict[str, str]], unit: str) -> float:
        return sum(number(offer["quantity"]) for offer in rows if offer["quantity_unit"] == unit)

    bc_quantity_m3 = quantity_sum_by_unit(bc_supply_rows, "m3")
    bc_quantity_odt = quantity_sum_by_unit(bc_supply_rows, "odt")
    qc_quantity_m3 = quantity_sum_by_unit(qc_supply_rows, "m3")
    qc_quantity_odt = quantity_sum_by_unit(qc_supply_rows, "odt")

    matrix_rows = [
        {
            "province": "BC",
            "scanfi_ws3_run_id": SCANFI_WS3_RUN_ID,
            "qc_ws3_run_id": "",
            "scenario_id": "bc_mini_001",
            "supply_rows": str(len(bc_supply["supply_rows"])),
            "supply_quantity_m3": format_number(bc_quantity_m3),
            "supply_quantity_odt": format_number(bc_quantity_odt),
            "objective_contribution_log": "0.0",
            "objective_contribution_residue": "0.0",
            "notes": (
                "BC VRI stub fixture ingested through fresh_fibre.bc_vri_reader; "
                "ws3 handoff bridged via tests/fixtures/bc_vri_sample/vri_to_bc_mini_mapping.csv."
            ),
        },
        {
            "province": "QC",
            "scanfi_ws3_run_id": "",
            "qc_ws3_run_id": "qc_casfri_diagnostic_supply_001",
            "scenario_id": "qc_mini_001",
            "supply_rows": str(len(qc_supply["supply_rows"])),
            "supply_quantity_m3": format_number(qc_quantity_m3),
            "supply_quantity_odt": format_number(qc_quantity_odt),
            "objective_contribution_log": "0.0",
            "objective_contribution_residue": "0.0",
            "notes": "QC diagnostic mini path through casfri_qc_reader + qc_ws3_export bridge.",
        },
    ]
    write_variant_run_matrix(
        matrix_rows,
        fixture_dir / "generated" / "bc_qc_mini_variant_run_matrix.csv",
    )

    return {
        "fixture_dir": str(fixture_dir),
        "bc_supply_rows": len(bc_supply["supply_rows"]),
        "qc_supply_rows": len(qc_supply["supply_rows"]),
        "bc_quantity_m3": bc_quantity_m3,
        "bc_quantity_odt": bc_quantity_odt,
        "qc_quantity_m3": qc_quantity_m3,
        "qc_quantity_odt": qc_quantity_odt,
        "supply_comparison_rows": len(supply_comparison_rows),
        "pathway_flow_rows": len(pathway_rows),
        "solution_summary_rows": len(summary_rows),
        "lp_status": lp_result["status"],
        "lp_objective_value": lp_result["objective_value"],
        "graph_nodes": node_count,
        "graph_arcs": arc_count,
        "lp_variables": lp_result["variables"],
        "lp_constraints": lp_result["constraints"],
        "solution_flows": lp_result["solution_flows"],
        "contract_rows": len(contract_rows),
        "supply_offer_path": str(supply_offer_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workdir", type=Path, default=DEFAULT_FIXTURE_DIR)
    parser.add_argument(
        "--base-dir",
        type=Path,
        default=DEFAULT_SCANFI_BASE,
    )
    parser.add_argument(
        "--scanfi-fixture-dir",
        type=Path,
        default=DEFAULT_SCANFI_DIAG_DIR,
    )
    parser.add_argument(
        "--skip-regenerate-workdir",
        action="store_true",
        help="Do not rebuild the fixture directory; re-use the existing tables.",
    )
    parser.add_argument(
        "--translation-table-dir",
        type=Path,
        default=DEFAULT_TRANSLATION_TABLE_DIR,
    )
    args = parser.parse_args()

    result = run_full_two_province_variant(
        fixture_dir=args.workdir,
        base_dir=args.base_dir,
        scanfi_fixture_dir=args.scanfi_fixture_dir,
        regenerate_workdir_flag=not args.skip_regenerate_workdir,
    )
    print(
        f"solved bc-qc-mini-2prov {result['lp_status']} objective={float(result['lp_objective_value']):.6f}; "
        f"wrote {result['fixture_dir']}"
    )


if __name__ == "__main__":
    main()
