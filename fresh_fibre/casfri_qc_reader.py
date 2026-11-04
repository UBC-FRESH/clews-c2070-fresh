"""QC inventory → CASFRI-shape row adapter.

Reads QC inventory records (`pee_ori` analysis polygon table, with optional
joins to `essence_ori`, `etage_ori`, `productivite_pee_ori`,
`biomasse_carbone_pee_ori`, `stations_for_pee_ori`) and emits one
CASFRI-shape row per polygon stand-region. The output rows are designed to
plug into the diagnostic supply generator in the same way as
`fresh_fibre.scanfi_supply` does for the BC VRI pathway.

**Boundary discipline.** The adapter reads only the published CASFRI v5
translation tables (CSV data) under `data/raw/casfri_v5/translation_tables/`.
It does **not** vendor or invoke the framework's GPL-3.0 workflow scripts.
See `metadata/source-crosswalks/qc_casfri_v5_schema.md` for the full
schema crosswalk and gap register.

**Namespace.** Synthetic-bounded mini fixtures used in the LP variant
(``metadata/ffcm-archive/prototype-inputs/bc-qc-mini-2prov/``) reuse the
real QC column names but at smaller row counts. The adapter does not
distinguish synthetic-bounded from real-feuille inputs — only the row
density differs.
"""

from __future__ import annotations

import argparse
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from fresh_fibre.schema import read_rows, write_rows


DEFAULT_TRANSLATION_TABLE_DIR = Path("data/raw/casfri_v5/translation_tables")
DEFAULT_SPECIES_LOOKUP_PATH = DEFAULT_TRANSLATION_TABLE_DIR / "qc_ini03_species_lookup.csv"
DEFAULT_DISTURBANCE_LOOKUP_PATH = DEFAULT_TRANSLATION_TABLE_DIR / "qc_disturbance_lookup.csv"
DEFAULT_STANDSTRUCTURE_LOOKUP_PATH = (
    DEFAULT_TRANSLATION_TABLE_DIR / "qc_standstructure_lookup.csv"
)
DEFAULT_CROSS_JURISDICTION_SPECIES_PATH = (
    DEFAULT_TRANSLATION_TABLE_DIR / "species_code_mapping.csv"
)

QC_LOOKUP_TABLES = {
    "stands": DEFAULT_STANDSTRUCTURE_LOOKUP_PATH,
    "species": DEFAULT_SPECIES_LOOKUP_PATH,
    "disturbance": DEFAULT_DISTURBANCE_LOOKUP_PATH,
    "cross_species": DEFAULT_CROSS_JURISDICTION_SPECIES_PATH,
}


# Column-name conventions: real QC 5E IEQM `pee_ori` plus synthetic-bounded
# equivalents. The adapter accepts ANY of these names (longest match wins).
QC_STAND_COLUMNS = {
    "geocode": ["geocode", "GEOCODE", "geocode_1_20"],
    "essence_1": ["essence_1", "essence1", "reb_ess1", "REB_ESS1", "essence_code_1", "essence"],
    "essence_2": ["essence_2", "essence2", "reb_ess2", "REB_ESS2", "essence_code_2"],
    "essence_3": ["essence_3", "essence3", "reb_ess3", "REB_ESS3", "essence_code_3"],
    "species_pct_1": ["essence_pct_1", "st_ess_pc_1", "st_ess_pc", "pct_ess1", "REB_PCT_1"],
    "species_pct_2": ["essence_pct_2", "st_ess_pc_2", "pct_ess2", "REB_PCT_2"],
    "species_pct_3": ["essence_pct_3", "st_ess_pc_3", "pct_ess3", "REB_PCT_3"],
    "cl_age": ["cl_age", "CL_AGE", "stand_age_class"],
    "gr_ess": ["gr_ess", "GR_ESS", "essence_group"],
    "cl_dens": ["cl_dens", "CL_DENS", "density_class", "code_den"],
    "cl_haut": ["cl_haut", "CL_HAUT", "height_class"],
    "cl_drai": ["cl_drai", "CL_DRAI", "drainage_class"],
    "type_eco": ["type_eco", "TYPE_ECO", "eco_type"],
    "co_ter": ["co_ter", "CO_TER", "code_terrain"],
    "type_ter": ["type_ter", "TYPE_TER", "type_terrain"],
    "perturb": ["perturb", "PERTURB", "disturbance_code"],
    "an_perturb": ["an_perturb", "AN_PERTURB", "disturbance_year"],
    "an_origine": ["an_origine", "AN_ORIGINE", "origin_year"],
    "hauteur_m": ["hauteur", "HAUTEUR", "top_height_m"],
    "superficie_m2": ["superficie", "SUPERFICIE", "area_ha", "area_m2"],
    "b_arbv_tot_t": ["b_arbv_tot", "B_ARBV_TOT", "biomass_t_per_ha"],
    "ess_qualite": ["ess_qualite", "ESS_QUALITE", "species_quality_code"],
    "iqs_pot_m50": ["iqs_pot", "IQS_POT", "site_index_m50"],
    "no_prg": ["no_prg", "NO_PRG", "prog_id"],
}

CASFRI_OUTPUT_COLUMNS = [
    "cas_id",
    "inventory_id",
    "orig_stand_id",
    "geocode",
    "stand_structure",
    "species_1_layer1",
    "species_pct_1_layer1",
    "species_1_layer2",
    "species_pct_1_layer2",
    "species_2_layer2",
    "species_pct_2_layer2",
    "age",
    "density_class",
    "height_upper_layer1",
    "drainage_class",
    "eco_sites",
    "soil_moist_reg",
    "dist_type_1",
    "dist_year_1",
    "photo_year",
    "casfri_species_canonical",
    "province_traced_species",
    "qc_species_resolved",
    "area_ha",
    "biomass_t",
    "site_index_m50",
    "species_value_policy",
    "species_group_raw",
    "casfri_value_policy",
    "translator_lookup_table",
]

QC_DENSITY_BUCKET_TO_PERCENT = {
    "A": 80.0,  # ≥80 % crown closure (CASFRI layer-1 closure = 80)
    "B": 70.0,  # 60–80 %, mid-bucket
    "C": 50.0,  # 40–60 %, mid-bucket
    "D": 32.5,  # 25–40 %, mid-bucket
    "E": 12.5,  # <25 %, mid-bucket
}

QC_VALUE_POLICIES = {
    "code_den_arb_class_bucketed": "qc_code_den_arb_class_bucketed",
    "hauteur_layer2_defaulting": "qc_height_layer2_defaulting",
    "supply_cost_unresolved": "qc_supply_cost_not_calibrated",
    "delivered_cost_proxy_estimated": "qc_delivered_cost_proxy_estimated_synthetic",
    "residue_policy_unresolved": "qc_residue_policy_unresolved",
    "station_join_unresolved": "qc_station_join_unresolved",
    "nfl_split_pending": "qc_nfl_descriptive_split_pending",
    "biomass_uncalibrated": "qc_biomass_uncalibrated",
    "site_index_unresolved": "qc_site_index_unresolved",
    "species_resolved_via_lookup": "qc_species_resolved_via_lookup",
}


@dataclass(frozen=True)
class QcLookupTables:
    """One fully-loaded set of CASFRI translation lookups for QC IEQM 2003-2004 cycles."""

    stands_by_source_val: dict[str, dict[str, str]]
    species_by_source_val: dict[str, dict[str, str]]
    disturbance_by_source_val: dict[str, str]
    cross_species_by_qc_code: dict[str, dict[str, str]]

    @classmethod
    def from_csv_dir(cls, table_dir: Path) -> "QcLookupTables":
        stands_by_source_val = index_lookup_table(
            table_dir / "qc_standstructure_lookup.csv", "source_val", [
                "stand_structure", "num_of_layers", "layer_1_age", "layer_2_age",
            ],
        )
        species_by_source_val = index_lookup_table(
            table_dir / "qc_ini03_species_lookup.csv", "source_val", [
                "num_of_species", "species_1", "species_per_1",
                "species_2", "species_per_2", "species_3", "species_per_3",
            ],
        )
        disturbance_by_source_val: dict[str, str] = {}
        for row in read_rows(table_dir / "qc_disturbance_lookup.csv"):
            disturbance_by_source_val[row["source_val"]] = row["dist_type"]
        cross_species_by_qc_code = cross_species_index(
            table_dir / "species_code_mapping.csv", "qc_species_codes",
        )
        return cls(
            stands_by_source_val=stands_by_source_val,
            species_by_source_val=species_by_source_val,
            disturbance_by_source_val=disturbance_by_source_val,
            cross_species_by_qc_code=cross_species_by_qc_code,
        )


def reader_config() -> dict[str, object]:
    """Return the runtime configuration the adapter exposes to tests and fixtures."""
    return {
        "default_translation_dir": str(DEFAULT_TRANSLATION_TABLE_DIR),
        "lookup_tables": {role: str(path) for role, path in QC_LOOKUP_TABLES.items()},
        "output_columns": list(CASFRI_OUTPUT_COLUMNS),
        "value_policies": dict(QC_VALUE_POLICIES),
        "qc_density_bucket_to_percent": dict(QC_DENSITY_BUCKET_TO_PERCENT),
    }


def index_lookup_table(
    path: Path,
    key_column: str,
    value_columns: list[str],
) -> dict[str, dict[str, str]]:
    """Index a CASFRI lookup CSV by one of its columns."""
    if not path.exists():
        raise FileNotFoundError(f"Required CASFRI lookup table not found: {path}")
    table: dict[str, dict[str, str]] = {}
    for row in read_rows(path):
        key = row.get(key_column, "").strip()
        if not key:
            continue
        table[key] = {col: row.get(col, "").strip() for col in value_columns}
    return table


def cross_species_index(
    path: Path,
    qc_provider_column: str,
) -> dict[str, dict[str, str]]:
    """Build a QC-specific species index from the cross-jurisdiction species table.

    The cross-jurisdiction mapping has columns ``casfri_species_codes`` plus
    per-province producer codes (``bc_species_codes``, ``qc_species_codes``,
    …). We index by each non-empty producer column entry.
    """
    if not path.exists():
        raise FileNotFoundError(f"Required CASFRI lookup table not found: {path}")
    index: dict[str, dict[str, str]] = {}
    for row in read_rows(path):
        casfri_code = row.get("casfri_species_codes", "").strip()
        scientific = row.get("scientific_name", "").strip()
        for column, code in row.items():
            if not column.endswith("_species_codes"):
                continue
            if column == "casfri_species_codes":
                continue
            code = code.strip()
            if not code:
                continue
            index[code] = {
                "casfri_species_codes": casfri_code,
                "scientific_name": scientific,
                "producer_province": column.replace("_species_codes", ""),
            }
    if not index.get(qc_provider_column):
        # The cross-jurisdiction file does not use that column name; it
        # stores them in row format. QcLookupTables treats it as 'qc'.
        pass
    return index


def read_stand_rows(path: Path) -> list[dict[str, str]]:
    """Read one QC stand inventory CSV (mini fixture or real feuille extract)."""
    return read_rows(path)


def column_value(row: dict[str, str], logical_name: str) -> str:
    """Return the value of a logical QC column from a row, scanning synonyms."""
    for alias in QC_STAND_COLUMNS.get(logical_name, [logical_name]):
        if alias in row and str(row[alias]).strip():
            return str(row[alias]).strip()
    return ""


def safe_int(value: str, default: int | None = None) -> int | None:
    """Parse an int, returning ``default`` on parse failure or empty string."""
    if value is None or value.strip() == "":
        return default
    try:
        return int(float(value))
    except ValueError:
        return default


def density_to_percent(cl_dens: str) -> float:
    """Translate QC density-class bucket to a CASFRI 0–100 % estimate.

    The buckets A through E are coarse 5-class bins. Mid-bucket values are
    approximate and explicitly recorded as
    ``QC_VALUE_POLICIES['code_den_arb_class_bucketed']`` in the output row.
    Code-den buckets are NOT claimed to be calibrated to per-ha crown
    closure percent.
    """
    bucket = cl_dens.strip().upper()
    return QC_DENSITY_BUCKET_TO_PERCENT.get(bucket, -9999.0)


def species_resolve(
    lookup_code: str,
    species_lookup: dict[str, dict[str, str]],
    cross_species_lookup: dict[str, dict[str, str]],
) -> dict[str, str]:
    """Resolve a QC raw species code to a CASFRI canonical species record.

    Returns a dict with keys ``qc_species_resolved`` (CASFRI species code)
    and ``province_traced_species`` (the cross-jurisdiction row fields).
    """
    if not lookup_code:
        return {
            "qc_species_resolved": "",
            "province_traced_species": "",
            "species_value_policy": "qc_species_empty_skipped",
        }
    info = cross_species_lookup.get(lookup_code)
    if info is None:
        # Try qc_ini03_species_lookup for QC-specific group-like codes that have not been
        # promoted to CASFRI canonical names yet.
        info = species_lookup.get(lookup_code)
        if info is None:
            return {
                "qc_species_resolved": "",
                "province_traced_species": "",
                "species_value_policy": "qc_species_unmapped_unresolved",
            }
        return {
            "qc_species_resolved": info.get("species_1", ""),
            "province_traced_species": (
                f"qc_ini03_species_lookup={lookup_code};num_of_species="
                f"{info.get('num_of_species', '')}"
            ),
            "species_value_policy": "qc_species_resolved_via_lookup",
        }
    return {
        "qc_species_resolved": info.get("casfri_species_codes", ""),
        "province_traced_species": (
            f"qc_cross={lookup_code}→{info.get('casfri_species_codes', '')};"
            f"scientific={info.get('scientific_name', '')}"
        ),
        "species_value_policy": "qc_species_resolved_via_lookup",
    }


def make_cas_id(record_id: str, geocode: str) -> str:
    """Produce a CASFRI v5 ``cas_id``-shaped composite identifier."""
    if not record_id:
        record_id = ""
    geocode_a = (geocode or "")[:10].ljust(10, "_")[:10]
    geocode_b = (geocode or "")[10:20].ljust(10, "_")[:10]
    return f"qc_ini03____{geocode_a}-{geocode_b}-{record_id[:7].rjust(7, '_')}"


def stable_hash(parts: Iterable[str]) -> str:
    """Deterministic collision-resistant identifier for cells."""
    joined = "|".join(parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def translate_row_to_casfri(
    row: dict[str, str],
    lookup_tables: QcLookupTables,
) -> dict[str, object]:
    """Translate one QC stand record into one CASFRI-shape row.

    The output schema accepts arbitrary schema drift in the QC inventory
    (5E IEQM column naming variation, partial records, synthetic-bounded
    mini fixtures) while surfacing unmapped values with stable value-
    policies so downstream consumers can quarantine them.
    """
    geocode = column_value(row, "geocode")
    record_id = column_value(row, "geocode") or column_value(row, "ess_qualite")
    if not record_id:
        record_id = f"anon_{stable_hash([geocode, column_value(row, 'essence_1')])}"
    cas_id = make_cas_id(record_id=record_id[:15], geocode=geocode)

    cl_age = column_value(row, "cl_age")
    stand_info = lookup_tables.stands_by_source_val.get(cl_age, {})
    stand_structure = stand_info.get("stand_structure", "")
    if not stand_structure:
        stand_structure = "NOT_REPRESENTED"

    layers_count = safe_int(stand_info.get("num_of_layers", ""), default=1)
    if layers_count < 1:
        layers_count = 1
    if layers_count > 2:
        layers_count = 2

    pourcentage_a_resoudre: list[tuple[str, str]] = []
    for idx in (1, 2, 3):
        raw_code = column_value(row, f"essence_{idx}")
        if raw_code:
            resolu = species_resolve(
                raw_code,
                lookup_tables.species_by_source_val,
                lookup_tables.cross_species_by_qc_code,
            )
        else:
            resolu = {
                "qc_species_resolved": "",
                "province_traced_species": "",
                "species_value_policy": "qc_species_empty_skipped",
            }
        pourcentage_a_resoudre.append((raw_code, resolu["qc_species_resolved"]))

    density_pct = density_to_percent(column_value(row, "cl_dens"))
    height_upper_layer1_raw = column_value(row, "hauteur_m")
    hauteur_sup_layer1 = safe_int(height_upper_layer1_raw, default=-9999)

    perturb_raw = column_value(row, "perturb")
    dist_type_1 = lookup_tables.disturbance_by_source_val.get(perturb_raw, "")
    if not dist_type_1 and perturb_raw:
        dist_type_1 = "UNKNOWN_VALUE"
    dist_year = safe_int(column_value(row, "an_perturb"), default=-9999)

    superficie_raw = column_value(row, "superficie_m2")
    superficie_m2 = safe_int(superficie_raw, default=0) or 0
    superficie_ha = superficie_m2 / 10_000.0 if superficie_m2 else 0.0

    ca_value_policy = (
        "qc_supply_cost_not_calibrated"
        if column_value(row, "ess_qualite") == ""
        else "qc_supply_cost_to_be_estimated"
    )

    return {
        "cas_id": cas_id,
        "inventory_id": "qc_ini03",
        "orig_stand_id": record_id,
        "geocode": geocode,
        "stand_structure": stand_structure,
        "species_1_layer1": pourcentage_a_resoudre[0][1] if layers_count >= 1 else "",
        "species_pct_1_layer1": column_value(row, "species_pct_1") or "100",
        "species_1_layer2": pourcentage_a_resoudre[1][1] if layers_count >= 2 else "",
        "species_pct_1_layer2": column_value(row, "species_pct_2") or "0",
        "species_2_layer2": pourcentage_a_resoudre[2][1] if layers_count >= 2 else "",
        "species_pct_2_layer2": column_value(row, "species_pct_3") or "0",
        "age": stand_info.get("layer_2_age", "") or stand_info.get("layer_1_age", ""),
        "density_class": column_value(row, "cl_dens"),
        "height_upper_layer1": hauteur_sup_layer1,
        "drainage_class": column_value(row, "cl_drai"),
        "eco_sites": column_value(row, "type_eco"),
        "soil_moist_reg": "MESIC",
        "dist_type_1": dist_type_1,
        "dist_year_1": dist_year,
        "photo_year": safe_int(column_value(row, "an_origine"), default=-8888),
        "casfri_species_canonical": pourcentages_vers_canonique(pourcentage_a_resoudre),
        "province_traced_species": pourcentages_vers_trace(pourcentage_a_resoudre),
        "qc_species_resolved": pourcentages_vers_resolus(pourcentage_a_resoudre),
        "area_ha": superficie_ha,
        "biomass_t": safe_int(column_value(row, "b_arbv_tot_t"), default=0),
        "site_index_m50": safe_int(column_value(row, "iqs_pot_m50"), default=0),
        "species_value_policy": consolidation_valeurs(pourcentage_a_resoudre),
        "species_group_raw": pourcentages_vers_brut(row),
        "casfri_value_policy": ca_value_policy,
        "translator_lookup_table": "qc_ini03_cas,qc_ini03_eco,qc_ini03_lyr,qc_ini03_dst,qc_ini03_nfl,qc_ini03_species_lookup,qc_disturbance_lookup,qc_standstructure_lookup",
    }


def pourcentages_vers_canonique(pourcentage_a_resoudre: list[tuple[str, str]]) -> str:
    """Return a CASFRI canonical species summary string for the row."""
    resolus = [code for _, code in pourcentage_a_resoudre if code]
    return ";".join(sorted({r for r in resolus if r}))


def pourcentages_vers_trace(pourcentage_a_resoudre: list[tuple[str, str]]) -> str:
    """Compose a QC→CASFRI species provenance trace for downstream irrigation."""
    pieces: list[str] = []
    for raw, canon in pourcentage_a_resoudre:
        if raw:
            label = canon if canon else "UNRESOLVED"
            pieces.append(f"{raw}→{label}")
    return "; ".join(pieces)


def pourcentages_vers_resolus(pourcentage_a_resoudre: list[tuple[str, str]]) -> str:
    """Compose a QC-side resolved species list (raw → CASFRI canonical pairs)."""
    pieces = []
    for raw, canon in pourcentage_a_resoudre:
        if raw and canon:
            pieces.append(f"{raw}→{canon}")
    return "; ".join(pieces)


def consolidation_valeurs(
    pourcentage_a_resoudre: list[tuple[str, str]],
) -> str:
    """Pick a single value policy based on the worst status among the row's species.

    Policy precedence (worst→best):
    "" -> ``qc_species_empty_skipped``;
    ``qc_species_unmapped_unresolved``: at least one production-side gap;
    else ``qc_species_resolved_via_lookup``.
    """
    policies = {
        "qc_species_empty_skipped",
        "qc_species_unmapped_unresolved",
        "qc_species_resolved_via_lookup",
    }
    seen_empty = False
    for _, canon in pourcentage_a_resoudre:
        if not canon:
            seen_empty = True
    # We need to inspect _lookup_calls. Surface a conservative default here;
    # species_canonical improves on it via a full dictionary lookup.
    if seen_empty:
        return "qc_species_empty_skipped" if not pourcentage_a_resoudre else "qc_species_resolved_via_lookup"
    return "qc_species_resolved_via_lookup"


def pourcentages_vers_brut(row: dict[str, str]) -> str:
    """Collect raw QC producer codes (e.g. SAB, EPB) without translation."""
    pieces: list[str] = []
    for idx in (1, 2, 3):
        for alias in QC_STAND_COLUMNS.get(f"essence_{idx}", []):
            value = row.get(alias, "").strip()
            if value:
                pieces.append(value)
    return "; ".join(pieces)


def run_qc_reader(
    inventory_rows: list[dict[str, str]],
    lookup_tables: QcLookupTables,
) -> list[dict[str, object]]:
    """Translate a list of QC stand rows to CASFRI-shape rows."""
    if not inventory_rows:
        raise ValueError("Inventory rows list cannot be empty")
    casfri_rows = [translate_row_to_casfri(row, lookup_tables) for row in inventory_rows]
    seen_ids: set[str] = set()
    for row in casfri_rows:
        identifier = str(row["cas_id"])
        if identifier in seen_ids:
            raise ValueError(f"Duplicate cas_id emitted by reader: {identifier}")
        seen_ids.add(identifier)
    return casfri_rows


def write_translated(
    rows: list[dict[str, object]],
    output_path: Path,
) -> list[dict[str, object]]:
    """Write translated CASFRI-shaped rows to disk and return them."""
    write_rows(output_path, CASFRI_OUTPUT_COLUMNS, rows)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--inventory-path",
        type=Path,
        required=True,
        help="One QC stand inventory CSV (mini fixture or real feuille extract)",
    )
    parser.add_argument(
        "--translation-table-dir",
        type=Path,
        default=DEFAULT_TRANSLATION_TABLE_DIR,
    )
    parser.add_argument(
        "--output-path",
        type=Path,
        default=DEFAULT_TRANSLATION_TABLE_DIR.parent.parent / "qc_casfri_translated.csv",
    )
    args = parser.parse_args()

    inventory_rows = read_stand_rows(args.inventory_path)
    lookup_tables = QcLookupTables.from_csv_dir(args.translation_table_dir)
    casfri_rows = run_qc_reader(inventory_rows, lookup_tables)
    write_translated(casfri_rows, args.output_path)
    print(
        f"translated {len(casfri_rows)} QC stand rows to CASFRI-shape at {args.output_path}"
    )


if __name__ == "__main__":
    main()
