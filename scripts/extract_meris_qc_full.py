#!/usr/bin/env python3
"""Extract the full MERIS QC model-input cost/revenue decks.

The extractor is read-only against ``MERIS_BdTbl.accdb`` and publishes:

* long and wide species/product price decks;
* species x DBH revenue curves for the three shipped revenue profiles;
* the 191-zone economic value decomposition (Rdv/Bnf/Rsl/Aut1/Aut2/Tot);
* the 2,674 administered royalty rows;
* the 191-zone supply-cost parameters and the 94-row treatment catalogue;
* a canonical 18-field ledger block (the 16 core fields plus optional
  ``quality`` and ``dbh_cm``) containing only model-input revenue and
  production-cost rows (no observed transaction, no fabricated volume);
* provenance and per-file manifests.

The MERIS database is an analysis/simulation back-end, not a register of
actual harvest.  Every value published here is a model parameter or an
administered rate.  No value is imputed, zero-filled, or scaled to a
provincial volume.  All ledger rows therefore carry ``model_input=1`` and
``not_observed=1`` and use ``volume_m3=1`` as a rate denominator, not as a
volume weight from a harvest record.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import re
import subprocess
import sys
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = Path("/tmp/meris/extracted/MERIS_BdTbl.accdb")
DEFAULT_OUT = REPO_ROOT / "data/derived/national/harvest_basis/meris_qc_full"
DEFAULT_LEDGER = DEFAULT_OUT / "forest_cost_revenue_qc_model.csv"

PRICE_TABLE = "tbl_Essences_frm_inv_tiges"
CURVE_TABLES = (
    "tbl_essences_revenus_EssDhp_Profil_2",
    "tbl_essences_revenus_EssDhp_Profil_3",
    "tbl_essences_revenus_EssDhp_Profil_4",
)
ZONE_VALUE_TABLE = "tbl_Zones_RedevBenefRentSal_F1234"
ROYALTY_TABLE = "tbl_redevances_F1234"
COST_TABLE = "tbl_couts_zones"
TREATMENT_TABLE = "tbl_Liste_Traitements"
ZONE_LIST_TABLE = "tbl_Liste_ZonesRedev"
SPECIES_TABLE = "tbl_Essences_b"
PROFILE_TABLE = "tbl_Profil_Rentabilite"

EXPECTED_COUNTS = {
    PRICE_TABLE: 45,
    CURVE_TABLES[0]: 1_824,
    CURVE_TABLES[1]: 1_824,
    CURVE_TABLES[2]: 1_824,
    ZONE_VALUE_TABLE: 2_674,
    ROYALTY_TABLE: 2_674,
    COST_TABLE: 191,
    TREATMENT_TABLE: 94,
    ZONE_LIST_TABLE: 191,
    SPECIES_TABLE: 45,
    PROFILE_TABLE: 5,
}

PRODUCTS = ("DER", "F1", "F2", "F3", "F4", "PATE", "AUTRE")
PRICE_COLUMNS = {
    "DER": "Der",
    "F1": "F1",
    "F2": "F2",
    "F3": "F3",
    "F4": "F4",
    "PATE": "Pate",
    "AUTRE": "Autre",
}
ROYALTY_COLUMNS = {
    "DER": "Der",
    "F1": "f1",
    "F2": "f2",
    "F3": "f3",
    "F4": "f4",
    "PATE": "Pate",
    "AUTRE": "Autre",
}
SERIES = (("1", "tendance"), ("2", "courant"))
PROFILE_DESCRIPTIONS = {
    2: "Scieur Res",
    3: "Scieur FD et Res",
    4: "Scieur Res autre 1",
}
LEDGER_FIELDS = (
    "jurisdiction",
    "source",
    "year",
    "spatial_unit",
    "species",
    "product",
    "volume_m3",
    "unit",
    "value_cad",
    "value_basis",
    "perspective",
    "component",
    "sign",
    "confidence",
    "licence",
    "notes",
    "quality",
    "dbh_cm",
)

# QC quality -> ledger product.  The quality class is also written to the
# dedicated ``quality`` column; the licence suffix stays for human auditability.
QUALITY_TO_PRODUCT = {
    "DER": "peeler",
    "F1": "sawlog",
    "F2": "sawlog",
    "F3": "sawlog",
    "F4": "sawlog",
    "PATE": "pulpwood",
    "AUTRE": "mixed_log",
}

# The species/DBH curves are composite sawing + chips + by-product values for
# SEPM species; they are not a single grade.  ``mixed_log`` is the honest
# product class; the curve coordinate is carried by ``dbh_cm`` and the grade is
# explicitly absent (``quality=NA``).
CURVE_PRODUCT = "mixed_log"
CURVE_PROFILE = "Profil_2"
PRICE_PLACEHOLDER = 0.01


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def mdb_export(db: Path, table: str) -> list[dict[str, str]]:
    """Read one ACCDB table through mdbtools into memory."""
    result = subprocess.run(
        ["mdb-export", str(db), table],
        check=True,
        capture_output=True,
        text=True,
    )
    rows = list(csv.DictReader(io.StringIO(result.stdout)))
    expected = EXPECTED_COUNTS.get(table)
    if expected is not None and len(rows) != expected:
        raise SystemExit(
            f"{table}: expected {expected} rows from mdb-export, got {len(rows)}"
        )
    return rows


def number(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"non-finite value {value!r}")
    return parsed


def decimal(value: float, places: int = 4) -> str:
    return f"{value:.{places}f}"


def write_csv(
    path: Path, fields: Sequence[str], rows: Iterable[Mapping[str, object]]
) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    count = 0
    try:
        with temporary.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle, fieldnames=list(fields), lineterminator="\n"
            )
            writer.writeheader()
            for row in rows:
                writer.writerow({field: row[field] for field in fields})
                count += 1
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return count


def build_price_deck(
    rows: Sequence[Mapping[str, str]],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    long_rows: list[dict[str, object]] = []
    wide_rows: list[dict[str, object]] = []
    for row in rows:
        species = row["ESSENCE"].strip()
        wide: dict[str, object] = {
            "species": species,
            "species_group": row["CategorieEssence"].strip(),
            "type_essence": row["TypeEssence"].strip(),
            "type_essence_equat": row["TypeEssenceEquat"].strip(),
            "source": f"{PRICE_TABLE}:one_row_per_species",
        }
        for product in PRODUCTS:
            for code, series in SERIES:
                column = f"Prix_{PRICE_COLUMNS[product]}_{code}"
                inclusion_column = f"Redev_{PRICE_COLUMNS[product]}_{code}_Inclus"
                value = number(row[column])
                included = row[inclusion_column].strip() or "0"
                if value == 0.0:
                    status = "not_applicable_zero"
                elif abs(value - 0.01) < 1e-9:
                    status = "placeholder_0.0100"
                else:
                    status = "model_input"
                long_rows.append(
                    {
                        "species": species,
                        "species_group": row["CategorieEssence"].strip(),
                        "type_essence": row["TypeEssence"].strip(),
                        "series": series,
                        "series_code": code,
                        "product": product,
                        "value": decimal(value),
                        "unit": "CAD_per_m3_log",
                        "stumpage_included": included,
                        "status": status,
                        "source": f"{PRICE_TABLE}:{column}",
                    }
                )
                wide[f"Prix_{PRICE_COLUMNS[product]}_{code}"] = decimal(value)
                wide[f"Redev_{PRICE_COLUMNS[product]}_{code}_Inclus"] = included
        wide_rows.append(wide)
    return long_rows, wide_rows


def build_curves(
    profiles: Mapping[int, Sequence[Mapping[str, str]]],
    profile_rows: Sequence[Mapping[str, str]],
) -> list[dict[str, object]]:
    descriptions = {
        int(row["NoProfil"]): row["Description"].strip()
        for row in profile_rows
        if row["NoProfil"].strip().isdigit()
    }
    output: list[dict[str, object]] = []
    for profile in sorted(profiles):
        for row in profiles[profile]:
            sciage = number(row["SCIAGE"])
            pate = number(row["PATE"])
            if not math.isclose(sciage, pate, rel_tol=0.0, abs_tol=1e-9):
                raise SystemExit(
                    "SEPM revenue columns differ; a one-revenue curve row would "
                    f"lose information at profile {profile}, "
                    f"{row['ESSENCE']} DHP {row['DHP']}"
                )
            output.append(
                {
                    "profile": f"Profil_{profile}",
                    "profile_description": descriptions.get(
                        profile, PROFILE_DESCRIPTIONS.get(profile, "")
                    ),
                    "species": row["ESSENCE"].strip(),
                    "type": row["Type"].strip(),
                    "dbh": row["DHP"].strip(),
                    "revenue": decimal(sciage),
                    "sciage": decimal(sciage),
                    "pate": decimal(pate),
                    "unit": "CAD_per_m3_log",
                    "origin": row["ORIGINE"].strip(),
                    "stumpage_included": row["InclureRedev"].strip(),
                    "source": (
                        f"{CURVE_TABLES[profile - 2]}:SCIAGE+PATE"
                    ),
                }
            )
    return output


def build_zone_values(
    rows: Sequence[Mapping[str, str]],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    long_rows: list[dict[str, object]] = []
    wide_rows: list[dict[str, object]] = []
    max_residual = 0.0
    for row in rows:
        wide: dict[str, object] = {
            "zone": row["Zone_Redev"].strip(),
            "group": row["Regroupement"].strip(),
            "source": f"{ZONE_VALUE_TABLE}:Rdv+Bnf+Rsl+Aut1+Aut2",
        }
        for product in PRODUCTS:
            values = {
                "Rdv": number(row[f"Rdv_{product}"]),
                "Bnf": number(row[f"Bnf_{product}"]),
                "Rsl": number(row[f"Rsl_{product}"]),
                "Aut1": number(row[f"Aut1_{product}"]),
                "Aut2": number(row[f"Aut2_{product}"]),
                "Tot": number(row[f"Tot_{product}"]),
            }
            reconstructed = sum(
                values[key] for key in ("Rdv", "Bnf", "Rsl", "Aut1", "Aut2")
            )
            residual = values["Tot"] - reconstructed
            max_residual = max(max_residual, abs(residual))
            if abs(residual) > 0.005:
                raise SystemExit(
                    "zone value identity failed for "
                    f"zone {row['Zone_Redev']} group {row['Regroupement']} "
                    f"product {product}: Tot={values['Tot']} sum={reconstructed}"
                )
            long_rows.append(
                {
                    "zone": row["Zone_Redev"].strip(),
                    "group": row["Regroupement"].strip(),
                    "product": product,
                    "Rdv": decimal(values["Rdv"]),
                    "Bnf": decimal(values["Bnf"]),
                    "Rsl": decimal(values["Rsl"]),
                    "Aut1": decimal(values["Aut1"]),
                    "Aut2": decimal(values["Aut2"]),
                    "Tot": decimal(values["Tot"]),
                    "identity_residual": decimal(residual),
                    "source": f"{ZONE_VALUE_TABLE}:Rdv/Bnf/Rsl/Aut1/Aut2/Tot",
                }
            )
            for key, value in values.items():
                wide[f"{key}_{product}"] = decimal(value)
        wide_rows.append(wide)
    if max_residual > 0.005:
        raise SystemExit(f"zone identity max residual {max_residual} exceeds 0.005")
    return long_rows, wide_rows


def build_royalties(
    rows: Sequence[Mapping[str, str]],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    wide_rows: list[dict[str, object]] = []
    long_rows: list[dict[str, object]] = []
    for row in rows:
        wide: dict[str, object] = {
            "zone": row["Zone_Redev"].strip(),
            "group": row["CategorieEssence"].strip(),
            "rate_variable": row["RdvVariable"].strip(),
            "date_maj": row["DateMaj"].strip(),
            "unit": "CAD_per_m3_log",
            "source": f"{ROYALTY_TABLE}:administered_2025-09-19",
        }
        for product in PRODUCTS:
            column = f"Redev_{ROYALTY_COLUMNS[product]}"
            value = number(row[column])
            wide[f"Redev_{product}"] = decimal(value)
            long_rows.append(
                {
                    "zone": row["Zone_Redev"].strip(),
                    "group": row["CategorieEssence"].strip(),
                    "product": product,
                    "rate": decimal(value),
                    "rate_variable": row["RdvVariable"].strip(),
                    "date_maj": row["DateMaj"].strip(),
                    "unit": "CAD_per_m3_log",
                    "source": f"{ROYALTY_TABLE}:{column}",
                }
            )
        wide_rows.append(wide)
    return wide_rows, long_rows


def build_costs(rows: Sequence[Mapping[str, str]]) -> list[dict[str, object]]:
    output: list[dict[str, object]] = []
    for row in rows:
        road = number(row["chemin_entretien_dol_m3"]) + number(
            row["chemin_constr_prim_sec_dol_m3"]
        )
        roadside_haul = number(row["transport_fardier_dol_m3"]) + number(
            row["dechargement_dol_m3"]
        )
        overhead = number(row["admin_dol_m3"]) + number(row["mesurage_dol_m3"])
        loading = number(row["chargement_dol_m3"])
        record = dict(row)
        record.update(
            {
                "road_dol_m3": decimal(road),
                "roadside_haul_partial_dol_m3": decimal(roadside_haul),
                "overhead_dol_m3": decimal(overhead),
                "harvest_loading_only_dol_m3": decimal(loading),
                "unit": "CAD_per_m3_log except *_dol_ha",
                "source": COST_TABLE,
            }
        )
        output.append(record)
    return output


def build_treatments(rows: Sequence[Mapping[str, str]]) -> list[dict[str, object]]:
    output: list[dict[str, object]] = []
    for row in rows:
        unit = row["Unite_Mesure"].strip()
        usable = unit == "DolHa"
        output.append(
            {
                "treatment": row["Traitement"].strip(),
                "str_type_trait": row["StrTypeTrait"].strip(),
                "unit": unit,
                "category": row["Categorie"].strip(),
                "sub_category": row["Sous_Categorie"].strip(),
                "taux_exe": decimal(number(row["Taux_EXE"])),
                "taux_ps": decimal(number(row["Taux_PS"])),
                "autre_cout": decimal(number(row["Autre_cout"])),
                "valeur_tot": decimal(number(row["Valeur_Tot"])),
                "usable_in_ledger": "1" if usable else "0",
                "not_usable_reason": (
                    ""
                    if usable
                    else "unit is not CAD_per_ha; density is not in MERIS"
                ),
                "source": f"{TREATMENT_TABLE}:Valeur_Tot",
            }
        )
    return output


def ledger_note(tokens: Sequence[tuple[str, str]]) -> str:
    return "; ".join(f"{key}={value}" for key, value in tokens)


def build_ledger_rows(
    price_rows: Sequence[Mapping[str, str]],
    curves: Sequence[Mapping[str, object]],
    costs: Sequence[Mapping[str, object]],
    treatments: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    ledger: list[dict[str, object]] = []
    # Revenue, first layer: the species x product price deck.  Both shipped
    # series (1=tendance, 2=courant) are ingested because they are alternative
    # model scenarios; the series is part of the licence id and the notes, so
    # the rows are distinct ledger keys and are never summed blindly.  Values
    # at or below 0.01 are the MERIS "value not set" placeholder and are
    # excluded, not converted to revenue.  PATE has no usable value in this
    # release, so the pulpwood quality class is mapped but yields no row.
    for row in price_rows:
        species = row["ESSENCE"].strip()
        for product in PRODUCTS:
            ledger_product = QUALITY_TO_PRODUCT[product]
            for code, series in SERIES:
                column = f"Prix_{PRICE_COLUMNS[product]}_{code}"
                value = number(row[column])
                if value <= PRICE_PLACEHOLDER:
                    continue
                included = row[
                    f"Redev_{PRICE_COLUMNS[product]}_{code}_Inclus"
                ].strip()
                suffix = "C" if series == "courant" else "T"
                ledger.append(
                    {
                        "jurisdiction": "QC",
                        "source": "prov_qc_meris",
                        "year": "2025",
                        "spatial_unit": "QC-MERIS-PROV",
                        "species": species,
                        "product": ledger_product,
                        "volume_m3": "1.0",
                        "unit": "CAD_per_m3_log",
                        "value_cad": decimal(value),
                        "value_basis": "model_input:mill_gate",
                        "perspective": "forest_sector",
                        "component": "delivered_log_revenue",
                        "sign": "1",
                        "confidence": (
                            "low" if product == "AUTRE" else "medium"
                        ),
                        "licence": f"QC-MERIS-{species}-{product}-{suffix}",
                        "notes": ledger_note(
                            (
                                ("currency_basis", "CAD_nominal"),
                                (
                                    "source_table",
                                    f"{PRICE_TABLE}:{column}",
                                ),
                                ("series", series),
                                ("qc_quality", product),
                                ("stumpage_included", included or "0"),
                                ("not_observed", "1"),
                                ("model_input", "1"),
                                ("not_a_transaction", "1"),
                                ("price_point", "mill_gate"),
                                (
                                    "price_point_basis",
                                    "value_net_of_processing_cost",
                                ),
                                ("volume_basis", "m3_solid"),
                                ("rate_denominator", "1_m3"),
                                ("coverage", "partial"),
                                (
                                    "coverage_denominator",
                                    "45_species_x_7_products_x_2_series",
                                ),
                                ("price_year_basis", "db_release_2025-09-23"),
                            )
                        ),
                        "quality": product,
                        "dbh_cm": "",
                    }
                )

    # Revenue, second layer: the SEPM species x DBH curves.  The three shipped
    # profiles are byte-identical, so Profil_2 is the canonical representative;
    # using all three would triple-count the same curve.  The curve coordinate
    # is the optional ``dbh_cm`` key dimension; no quality class is attached.
    curve_rows = [row for row in curves if row["profile"] == CURVE_PROFILE]
    if not curve_rows:
        raise SystemExit(f"no {CURVE_PROFILE} rows in the revenue curves")
    for row in curve_rows:
        species = str(row["species"])
        series = str(row["type"])
        dbh = int(str(row["dbh"]))
        value = number(str(row["revenue"]))
        if value <= PRICE_PLACEHOLDER:
            # The release has no PATE/0.01 curve value; keep the exclusion
            # explicit so a future placeholder cannot become revenue.
            continue
        suffix = "C" if series == "courant" else "T"
        ledger.append(
            {
                "jurisdiction": "QC",
                "source": "prov_qc_meris",
                "year": "2025",
                "spatial_unit": "QC-MERIS-PROV",
                "species": species,
                "product": CURVE_PRODUCT,
                "volume_m3": "1.0",
                "unit": "CAD_per_m3_log",
                "value_cad": decimal(value),
                "value_basis": "model_input:mill_gate",
                "perspective": "forest_sector",
                "component": "delivered_log_revenue",
                "sign": "1",
                "confidence": "medium",
                "licence": f"QC-MERIS-CURVE-{species}-{dbh}-{suffix}",
                "notes": ledger_note(
                    (
                        ("currency_basis", "CAD_nominal"),
                        (
                            "source_table",
                            f"{CURVE_TABLES[0]}:SCIAGE+PATE",
                        ),
                        ("series", series),
                        ("profile", CURVE_PROFILE),
                        (
                            "profile_equivalence",
                            "Profil_2=Profil_3=Profil_4",
                        ),
                        (
                            "product_basis",
                            "SEPM_composite_sciage_chips_byproducts",
                        ),
                        ("not_observed", "1"),
                        ("model_input", "1"),
                        ("not_a_transaction", "1"),
                        ("price_point", "mill_gate"),
                        ("price_point_basis", "value_net_of_processing_cost"),
                        ("volume_basis", "m3_solid"),
                        ("rate_denominator", "1_m3"),
                        ("curve_coordinate", f"dbh_cm={dbh}"),
                        ("coverage", "partial"),
                        (
                            "coverage_denominator",
                            "12_species_x_76_dbh_x_2_series",
                        ),
                        ("price_year_basis", "db_release_2025-09-23"),
                    )
                ),
                "quality": "NA",
                "dbh_cm": str(dbh),
            }
        )

    for row in costs:
        zone = str(row["zone"]).strip()
        licence = f"QC-ZONE_{zone}"
        road = number(str(row["road_dol_m3"]))
        overhead = number(str(row["overhead_dol_m3"]))
        loading = number(str(row["harvest_loading_only_dol_m3"]))
        for component, value, confidence, extra in (
            ("road", road, "medium", "fields=chemin_entretien+chemin_constr_prim_sec"),
            ("overhead", overhead, "medium", "fields=admin+mesurage"),
            ("harvest", loading, "low", "fields=chargement; partial=loading_only"),
        ):
            ledger.append(
                {
                    "jurisdiction": "QC",
                    "source": "prov_qc_meris",
                    "year": "2025",
                    "spatial_unit": f"QC-ZONE_{zone}",
                    "species": "all",
                    "product": "all",
                    "volume_m3": "1.0",
                    "unit": "CAD_per_m3_log",
                    "value_cad": decimal(value),
                    "value_basis": "model_input:roadside",
                    "perspective": "forest_sector",
                    "component": component,
                    "sign": "-1",
                    "confidence": confidence,
                    "licence": licence,
                    "notes": ledger_note(
                        (
                            ("currency_basis", "CAD_nominal"),
                            ("source_table", f"{COST_TABLE}:{extra}"),
                            ("not_observed", "1"),
                            ("model_input", "1"),
                            ("not_a_transaction", "1"),
                            ("price_point", "roadside"),
                            ("volume_basis", "m3_solid"),
                            ("rate_denominator", "1_m3"),
                            ("coverage", "partial"),
                            ("coverage_denominator", "191_tarification_zones"),
                            ("cost_year_basis", "db_release_2025-09-23"),
                            ("ha_costs_excluded", "chemin_constr_tertiaire_dol_ha")
                            if component == "road"
                            else ("unit", "CAD_per_m3_log"),
                        )
                    ),
                    "quality": "NA",
                    "dbh_cm": "",
                }
            )

    for row in treatments:
        if row["usable_in_ledger"] != "1":
            continue
        treatment = str(row["treatment"])
        treatment_slug = re.sub(r"[^A-Za-z0-9_.:/-]+", "_", treatment)
        ledger.append(
            {
                "jurisdiction": "QC",
                "source": "prov_qc_meris",
                "year": "2025",
                "spatial_unit": "QC-MERIS-PROV",
                "species": "all",
                "product": "all",
                "volume_m3": "1.0",
                "unit": "CAD_per_ha",
                "value_cad": str(row["valeur_tot"]),
                "value_basis": "model_input:roadside",
                "perspective": "forest_sector",
                "component": "silviculture",
                "sign": "-1",
                "confidence": "medium",
                "licence": f"QC-TRT-{treatment_slug}",
                "notes": ledger_note(
                    (
                        ("currency_basis", "CAD_nominal"),
                        (
                            "source_table",
                            f"{TREATMENT_TABLE}:Valeur_Tot;treatment={treatment}",
                        ),
                        ("not_observed", "1"),
                        ("model_input", "1"),
                        ("not_a_transaction", "1"),
                        ("price_point", "roadside"),
                        ("unit", "CAD_per_ha"),
                        ("rate_denominator", "1_ha"),
                        ("coverage", "partial"),
                        ("coverage_denominator", "94_treatments"),
                        ("cost_year_basis", "db_release_2025-09-23"),
                    )
                ),
                "quality": "NA",
                "dbh_cm": "",
            }
        )
    return ledger


def check_ledger(path: Path, expected_rows: int) -> None:
    sys.path.insert(0, str(REPO_ROOT))
    from workflows.national.harvest.cost_revenue import load_ledger

    ledger = load_ledger(path)
    if len(ledger) != expected_rows:
        raise SystemExit(
            f"{path}: expected {expected_rows} validated rows, got {len(ledger)}"
        )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)

    if not args.db.exists():
        raise SystemExit(f"missing MERIS database: {args.db}")
    out = args.out
    out.mkdir(parents=True, exist_ok=True)

    tables = {
        table: mdb_export(args.db, table)
        for table in (
            PRICE_TABLE,
            *CURVE_TABLES,
            ZONE_VALUE_TABLE,
            ROYALTY_TABLE,
            COST_TABLE,
            TREATMENT_TABLE,
            ZONE_LIST_TABLE,
            SPECIES_TABLE,
            PROFILE_TABLE,
        )
    }

    price_long, price_wide = build_price_deck(tables[PRICE_TABLE])
    curves = build_curves(
        {profile: tables[table] for profile, table in enumerate(CURVE_TABLES, start=2)},
        tables[PROFILE_TABLE],
    )
    zone_long, zone_wide = build_zone_values(tables[ZONE_VALUE_TABLE])
    max_zone_residual = max(
        abs(float(row["identity_residual"])) for row in zone_long
    )
    royalties_wide, royalties_long = build_royalties(tables[ROYALTY_TABLE])
    costs = build_costs(tables[COST_TABLE])
    treatments = build_treatments(tables[TREATMENT_TABLE])

    # The three shipped SEPM profiles are byte-identical in this release.  Fail
    # closed if that changes, because the ledger ingests a single canonical
    # profile and must not silently drop a genuinely different curve.
    curve_values: dict[str, dict[tuple[str, str, str], str]] = {}
    for row in curves:
        key = (str(row["species"]), str(row["type"]), str(row["dbh"]))
        curve_values.setdefault(str(row["profile"]), {})[key] = str(row["revenue"])
    canonical_curve = curve_values[CURVE_PROFILE]
    for profile, values in curve_values.items():
        if values != canonical_curve:
            raise SystemExit(
                f"revenue profile {profile} differs from {CURVE_PROFILE}; "
                "the one-profile ledger ingest would lose information"
            )

    ledger_rows = build_ledger_rows(
        tables[PRICE_TABLE], curves, costs, treatments
    )

    expected_zone_long = EXPECTED_COUNTS[ZONE_VALUE_TABLE] * len(PRODUCTS)
    expected_royalty_long = EXPECTED_COUNTS[ROYALTY_TABLE] * len(PRODUCTS)
    if len(price_long) != len(tables[PRICE_TABLE]) * len(PRODUCTS) * len(SERIES):
        raise SystemExit("price deck row count mismatch")
    if len(curves) != sum(EXPECTED_COUNTS[table] for table in CURVE_TABLES):
        raise SystemExit("revenue curve row count mismatch")
    if len(zone_long) != expected_zone_long:
        raise SystemExit("zone value long row count mismatch")
    if len(royalties_long) != expected_royalty_long:
        raise SystemExit("royalty long row count mismatch")

    write_csv(
        out / "meris_price_deck.csv",
        (
            "species",
            "species_group",
            "type_essence",
            "series",
            "series_code",
            "product",
            "value",
            "unit",
            "stumpage_included",
            "status",
            "source",
        ),
        price_long,
    )
    write_csv(
        out / "meris_price_deck_wide.csv",
        (
            "species",
            "species_group",
            "type_essence",
            "type_essence_equat",
            *(f"Prix_{PRICE_COLUMNS[p]}_{code}" for p in PRODUCTS for code, _ in SERIES),
            *(f"Redev_{PRICE_COLUMNS[p]}_{code}_Inclus" for p in PRODUCTS for code, _ in SERIES),
            "source",
        ),
        price_wide,
    )
    write_csv(
        out / "meris_revenue_curves.csv",
        (
            "profile",
            "profile_description",
            "species",
            "type",
            "dbh",
            "revenue",
            "sciage",
            "pate",
            "unit",
            "origin",
            "stumpage_included",
            "source",
        ),
        curves,
    )
    write_csv(
        out / "meris_zone_values.csv",
        (
            "zone",
            "group",
            "product",
            "Rdv",
            "Bnf",
            "Rsl",
            "Aut1",
            "Aut2",
            "Tot",
            "identity_residual",
            "source",
        ),
        zone_long,
    )
    write_csv(
        out / "meris_zone_values_wide.csv",
        (
            "zone",
            "group",
            *(f"{key}_{p}" for p in PRODUCTS for key in ("Rdv", "Bnf", "Rsl", "Aut1", "Aut2", "Tot")),
            "source",
        ),
        zone_wide,
    )
    write_csv(
        out / "meris_royalties.csv",
        (
            "zone",
            "group",
            *(f"Redev_{p}" for p in PRODUCTS),
            "rate_variable",
            "date_maj",
            "unit",
            "source",
        ),
        royalties_wide,
    )
    write_csv(
        out / "meris_royalties_long.csv",
        (
            "zone",
            "group",
            "product",
            "rate",
            "rate_variable",
            "date_maj",
            "unit",
            "source",
        ),
        royalties_long,
    )
    cost_fields = list(tables[COST_TABLE][0].keys()) + [
        "road_dol_m3",
        "roadside_haul_partial_dol_m3",
        "overhead_dol_m3",
        "harvest_loading_only_dol_m3",
        "unit",
        "source",
    ]
    write_csv(out / "meris_costs.csv", cost_fields, costs)
    write_csv(
        out / "meris_silviculture.csv",
        (
            "treatment",
            "str_type_trait",
            "unit",
            "category",
            "sub_category",
            "taux_exe",
            "taux_ps",
            "autre_cout",
            "valeur_tot",
            "usable_in_ledger",
            "not_usable_reason",
            "source",
        ),
        treatments,
    )
    write_csv(out / "forest_cost_revenue_qc_model.csv", LEDGER_FIELDS, ledger_rows)
    check_ledger(out / "forest_cost_revenue_qc_model.csv", len(ledger_rows))

    outputs = [
        out / "meris_price_deck.csv",
        out / "meris_price_deck_wide.csv",
        out / "meris_revenue_curves.csv",
        out / "meris_zone_values.csv",
        out / "meris_zone_values_wide.csv",
        out / "meris_royalties.csv",
        out / "meris_royalties_long.csv",
        out / "meris_costs.csv",
        out / "meris_silviculture.csv",
        out / "forest_cost_revenue_qc_model.csv",
    ]
    mdbtools = subprocess.run(
        ["mdb-ver", "-M"],
        check=False,
        capture_output=True,
        text=True,
    )
    manifest = {
        "generated_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "database": {
            "path": str(args.db),
            "bytes": args.db.stat().st_size,
            "sha256": sha256_file(args.db),
            "release": "MERIS 3.3.0 (database release 2025-09-23)",
            "mdbtools": (mdbtools.stdout + mdbtools.stderr).strip()
            or "mdbtools version unavailable",
        },
        "source_counts": {table: len(rows) for table, rows in tables.items()},
        "outputs": [
            {
                "file": path.name,
                "rows": sum(1 for _ in path.open(encoding="utf-8")) - 1,
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
                "columns": next(csv.reader(path.open(encoding="utf-8"))),
            }
            for path in outputs
        ],
        "checks": {
            "price_species": len({row["species"] for row in price_long}),
            "price_rows": len(price_long),
            "price_status_counts": dict(Counter(str(row["status"]) for row in price_long)),
            "curve_rows_per_profile": 1_824,
            "curve_profiles": sorted({str(row["profile"]) for row in curves}),
            "curve_ledger_profile": CURVE_PROFILE,
            "zone_source_rows": len(tables[ZONE_VALUE_TABLE]),
            "zone_long_rows": len(zone_long),
            "zone_max_identity_residual": max_zone_residual,
            "royalty_source_rows": len(tables[ROYALTY_TABLE]),
            "cost_rows": len(costs),
            "treatment_rows": len(treatments),
            "ledger_rows": len(ledger_rows),
            "ledger_components": dict(
                Counter(str(row["component"]) for row in ledger_rows)
            ),
            "ledger_quality_counts": dict(
                Counter(
                    str(row["quality"])
                    for row in ledger_rows
                    if str(row["quality"]) != "NA"
                )
            ),
            "ledger_quality_rows": sum(
                1 for row in ledger_rows if str(row["quality"]) != "NA"
            ),
            "ledger_dbh_rows": sum(
                1 for row in ledger_rows if str(row["dbh_cm"])
            ),
            "ledger_dbh_species": len(
                {
                    str(row["species"])
                    for row in ledger_rows
                    if str(row["dbh_cm"])
                }
            ),
            "ledger_dbh_min": min(
                (
                    float(str(row["dbh_cm"]))
                    for row in ledger_rows
                    if str(row["dbh_cm"])
                ),
                default=None,
            ),
            "ledger_dbh_max": max(
                (
                    float(str(row["dbh_cm"]))
                    for row in ledger_rows
                    if str(row["dbh_cm"])
                ),
                default=None,
            ),
            "ledger_series_counts": dict(
                Counter(
                    str(row["notes"]).split("series=", 1)[1].split(";", 1)[0]
                    for row in ledger_rows
                    if "series=" in str(row["notes"])
                )
            ),
            "ledger_volume_weight": "1.0 rate denominator, not an actual harvest volume",
        },
        "labels": {
            "status": "screening_only",
            "policy_grade": False,
            "observed_transactions": False,
            "model_input": True,
        },
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print(json.dumps(manifest["checks"], indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
