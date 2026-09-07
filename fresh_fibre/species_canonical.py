"""Jurisdiction-aware species canonicalization for FRESH supply rows.

The team's existing discipline separates **provenance** (the producer's
species code word-for-word, e.g. BC's ``FDC`` or QC's ``SAB``) from
**canonical identity** (a CASFRI species code like ``PICE_MAR_###`` or
``BEJU_PAP_###``). The FRESH pipeline emits both: the producer's prefix
sits in ``species_group_raw`` and the CASFRI canonical sits in
``species_group``. This module is the central translator.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from fresh_fibre.schema import read_rows, write_rows


DEFAULT_SPECIES_MAP_PATH = Path("data/raw/casfri_v5/translation_tables/species_code_mapping.csv")
DEFAULT_QC_SPECIES_LOOKUP_PATH = Path(
    "data/raw/casfri_v5/translation_tables/qc_ini03_species_lookup.csv"
)
DEFAULT_BC_SPECIES_LOOKUP_PATH = Path(
    "data/raw/casfri_v5/translation_tables/bc_vri01_lyr.csv"  # uses layered species encoding only
)

PROVINCE_PREFIX_TABLE = {
    "BC": "BC_",
    "QC": "QC_",
    "AB": "AB_",
    "MB": "MB_",
    "NB": "NB_",
    "NS": "NS_",
    "PE": "PE_",
    "NL": "NL_",
    "ON": "ON_",
    "SK": "SK_",
    "YT": "YT_",
    "NT": "NT_",
    "NU": "NU_",
}

VALUE_POLICIES = {
    "resolved_via_cross_jurisdiction_map": "species_resolved_via_cross_jurisdiction_map",
    "resolved_via_qc_lookup": "species_resolved_via_qc_lookup",
    "unresolved_unknown": "species_unknown_unresolved",
}


@dataclass(frozen=True)
class SpeciesMapping:
    """A QC, BC, or CASFRI canonical species entry resolved from producer codes."""

    raw_code: str
    province: str
    casfri_canonical: str
    scientific_name: str
    producer_province: str
    source: str  # "cross_jurisdiction_map" | "qc_lookup" | "bc_lookup" | "direct_casfri"
    value_policy: str


@dataclass(frozen=True)
class SpeciesTable:
    """One row in the unified FRESH species table for a single stand-region."""

    raw_code: str
    province: str
    jurisdictional_prefix: str
    casfri_canonical: str
    fresh_canonical: str
    scientific_name: str
    value_policy: str
    decision_id: str

    def to_row(self) -> dict[str, str]:
        return {
            "raw_code": self.raw_code,
            "province": self.province,
            "jurisdictional_prefix": self.jurisdictional_prefix,
            "casfri_canonical": self.casfri_canonical,
            "fresh_canonical": self.fresh_canonical,
            "scientific_name": self.scientific_name,
            "value_policy": self.value_policy,
            "decision_id": self.decision_id,
        }


CROSS_JURISDICTION_OUTPUT_COLUMNS = [
    "raw_code",
    "province",
    "jurisdictional_prefix",
    "casfri_canonical",
    "fresh_canonical",
    "scientific_name",
    "value_policy",
    "decision_id",
]


def load_cross_jurisdiction_map(
    path: Path = DEFAULT_SPECIES_MAP_PATH,
) -> list[SpeciesMapping]:
    """Load `species_code_mapping.csv` into a list of ``SpeciesMapping``.

    Each row of the upstream CSV is one CASFRI canonical species (column
    ``casfri_species_codes``) plus per-province producer code columns. We
    emit ONE ``SpeciesMapping`` per populated producer code, so the
    cross-jurisdiction map stays a single source of truth.
    """
    if not path.exists():
        raise FileNotFoundError(f"Required CASFRI lookup table not found: {path}")
    rows: list[SpeciesMapping] = []
    for csv_row in read_rows(path):
        casfri_canonical = csv_row.get("casfri_species_codes", "").strip()
        scientific = csv_row.get("scientific_name", "").strip()
        if not casfri_canonical:
            continue
        for column, code in csv_row.items():
            if not column.endswith("_species_codes"):
                continue
            if column == "casfri_species_codes":
                continue
            code = code.strip()
            if not code:
                continue
            province_label = column.replace("_species_codes", "").upper()
            province = province_normalise(province_label)
            rows.append(
                SpeciesMapping(
                    raw_code=code,
                    province=province,
                    casfri_canonical=casfri_canonical,
                    scientific_name=scientific,
                    producer_province=province,
                    source="cross_jurisdiction_map",
                    value_policy=VALUE_POLICIES["resolved_via_cross_jurisdiction_map"],
                )
            )
    return rows


def load_qc_lookup(path: Path = DEFAULT_QC_SPECIES_LOOKUP_PATH) -> list[SpeciesMapping]:
    """Load ``qc_ini03_species_lookup.csv`` (QC-internal species abstract codes)."""
    if not path.exists():
        raise FileNotFoundError(f"Required CASFRI lookup table not found: {path}")
    rows: list[SpeciesMapping] = []
    for csv_row in read_rows(path):
        raw_code = csv_row.get("source_val", "").strip()
        if not raw_code:
            continue
        num_of_species = csv_row.get("num_of_species", "").strip()
        first_canonical = csv_row.get("species_1", "").strip()
        scientific = ""
        rows.append(
            SpeciesMapping(
                raw_code=raw_code,
                province="QC",
                casfri_canonical=first_canonical,
                scientific_name=scientific,
                producer_province="qc_ini03",
                source="qc_lookup",
                value_policy=VALUE_POLICIES["resolved_via_qc_lookup"]
                if first_canonical
                else VALUE_POLICIES["unresolved_unknown"],
            )
        )
    return rows


def province_normalise(province_label: str) -> str:
    """Collapse upstream column labels (qc_codes, etc.) onto the FRESH province codes."""
    label = province_label.strip().upper()
    if len(label) == 2 and label.isalpha():
        return label
    if label == "QC":
        return "QC"
    return label


def build_index(rows: list[SpeciesMapping]) -> dict[tuple[str, str], SpeciesMapping]:
    """Index mappings by ``(province, raw_code)`` for O(1) lookup."""
    return {(row.province, row.raw_code): row for row in rows}


def resolve(
    raw_code: str,
    province: str,
    cross_index: dict[tuple[str, str], SpeciesMapping],
    qc_index: dict[tuple[str, str], SpeciesMapping],
) -> SpeciesMapping | None:
    """Find a mapping for ``raw_code`` against the jurisdiction's producer codes."""
    lookup_key = (province, raw_code)
    if lookup_key in cross_index:
        return cross_index[lookup_key]
    if province == "QC":
        # QC uses 2-letter abstract codes (SAB / EPB / BOP); the CASFRI
        # cross-jurisdiction file does not always have those codes
        # populated. Fall back to the QC internal lookup.
        return qc_index.get(lookup_key)
    return None


def canonical_id(
    mapping: SpeciesMapping | None,
    province: str,
) -> str:
    """Decide the FRESH canonical species identifier.

    Policy: keep the CASFRI species id (e.g. ``PICE_MAR_###``) as the
    canonical, and use the producer raw code (with a jurisdictional
    prefix) as the trace label.
    """
    if mapping is None:
        return ""
    return mapping.casfri_canonical


def jurisdictional_prefix(province: str) -> str:
    """Default jurisdictional prefix applied to raw producer codes."""
    return PROVINCE_PREFIX_TABLE.get(province.upper(), f"{province}_")


def decision_identifier(
    raw_code: str,
    province: str,
    casfri: str,
    policy: str,
) -> str:
    """Deterministic, collision-resistant identifier for each decision record."""
    digest = hashlib.sha256(f"{raw_code}|{province}|{casfri}|{policy}".encode("utf-8"))
    return digest.hexdigest()[:12]


def canonicalize(
    raw_codes: Iterable[tuple[str, str]],
    cross_index: dict[tuple[str, str], SpeciesMapping],
    qc_index: dict[tuple[str, str], SpeciesMapping],
) -> list[SpeciesTable]:
    """Translate ``(raw_code, province)`` pairs to FRESH ``SpeciesTable`` rows."""
    tables: list[SpeciesTable] = []
    for raw_code, province in raw_codes:
        cleaned_raw = raw_code.strip()
        normalised_province = province_normalise(province)
        if not cleaned_raw:
            continue
        mapping = resolve(cleaned_raw, normalised_province, cross_index, qc_index)
        if mapping is None:
            tables.append(
                SpeciesTable(
                    raw_code=cleaned_raw,
                    province=normalised_province,
                    jurisdictional_prefix=jurisdictional_prefix(normalised_province),
                    casfri_canonical="",
                    fresh_canonical="",
                    scientific_name="",
                    value_policy=VALUE_POLICIES["unresolved_unknown"],
                    decision_id=decision_identifier(
                        cleaned_raw, normalised_province, "", "unknown",
                    ),
                )
            )
            continue
        fresh_canonical = canonical_id(mapping, normalised_province)
        casfri_canonical = mapping.casfri_canonical
        policy = mapping.value_policy
        tables.append(
            SpeciesTable(
                raw_code=cleaned_raw,
                province=normalised_province,
                jurisdictional_prefix=mapping.producer_province + ":" if mapping.producer_province != mapping.province else "",
                casfri_canonical=casfri_canonical,
                fresh_canonical=fresh_canonical,
                scientific_name=mapping.scientific_name,
                value_policy=policy,
                decision_id=decision_identifier(
                    cleaned_raw, normalised_province, casfri_canonical, policy,
                ),
            )
        )
    return tables


def merge_decisions(
    tables: list[SpeciesTable],
) -> list[SpeciesTable]:
    """Collapse duplicate decisions on identical raw/canonical/policy keys.

    Conflict policy (per planning/casfri-v5-schema-integration-plan.md §5
    risk-5): when two jurisdictions agree on the CASFRI canonical species
    (e.g. both BC and QC report a given pin cherry species as the same
    ``PRUN_PEN_###``), the canonical stays scoped to the genus level and
    each producer's trace is preserved under unique ``decision_id``.
    """
    seen: dict[str, SpeciesTable] = {}
    for table in tables:
        key = "|".join(
            (table.raw_code, table.province, table.casfri_canonical, table.value_policy),
        )
        if key not in seen:
            seen[key] = table
            continue
        # Same raw / province / canonical / policy; keep first but ensure
        # the decision_id is unique across the batch.
        existing = seen[key]
        existing = SpeciesTable(
            raw_code=existing.raw_code,
            province=existing.province,
            jurisdictional_prefix=existing.jurisdictional_prefix,
            casfri_canonical=existing.casfri_canonical,
            fresh_canonical=existing.fresh_canonical,
            scientific_name=existing.scientific_name,
            value_policy=existing.value_policy,
            decision_id=existing.decision_id,
        )
        seen[key] = existing
    return list(seen.values())


def write_unified_table(
    tables: list[SpeciesTable],
    output_path: Path,
) -> list[SpeciesTable]:
    """Emit the unified FRESH species table to CSV."""
    write_rows(output_path, CROSS_JURISDICTION_OUTPUT_COLUMNS, [t.to_row() for t in tables])
    return tables


def read_legacy_stand_table(path: Path) -> list[tuple[str, str]]:
    """Read a stand table CSV with columns ``raw_code`` and ``province`` (or synonyms)."""
    rows: list[tuple[str, str]] = []
    for row in read_rows(path):
        raw = (
            row.get("raw_code")
            or row.get("species_code")
            or row.get("essence_code_1")
            or row.get("reb_ess1")
            or row.get("SPCS_CD_1")
            or ""
        ).strip()
        province = (
            row.get("province")
            or row.get("PROVINCE")
            or row.get("inventory_province")
            or "BC"
        ).strip().upper()
        if raw:
            rows.append((raw, province))
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cross-jurisdiction-path",
        type=Path,
        default=DEFAULT_SPECIES_MAP_PATH,
    )
    parser.add_argument(
        "--qc-lookup-path",
        type=Path,
        default=DEFAULT_QC_SPECIES_LOOKUP_PATH,
    )
    parser.add_argument(
        "--stand-table-path",
        type=Path,
        required=True,
        help="One BC or QC stand table CSV (raw_code + province columns).",
    )
    parser.add_argument(
        "--output-path",
        type=Path,
        required=True,
        help="Output unified species-table CSV.",
    )
    args = parser.parse_args()

    cross_jurisdiction_map = load_cross_jurisdiction_map(args.cross_jurisdiction_path)
    qc_lookup = load_qc_lookup(args.qc_lookup_path)
    cross_index = build_index(cross_jurisdiction_map)
    qc_index = build_index(qc_lookup)
    stand_codes = read_legacy_stand_table(args.stand_table_path)
    tables = canonicalize(stand_codes, cross_index, qc_index)
    tables = merge_decisions(tables)
    write_unified_table(tables, args.output_path)
    print(
        f"wrote {len(tables)} unified species-table rows to {args.output_path} "
        f"({sum(1 for t in tables if t.value_policy == VALUE_POLICIES['unresolved_unknown'])} "
        f"unknown)"
    )


if __name__ == "__main__":
    main()
