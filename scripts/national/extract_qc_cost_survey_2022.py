#!/usr/bin/env python3
"""Extract observed Quebec MRNF 2022 forest cost/revenue survey aggregates.

The source documents are public, regional/provincial aggregates published by
Quebec's Bureau de mise en marche des bois (BMMB).  They are observed industry
survey results, not model inputs.  This extractor publishes them as a stand-
alone tidy table; it deliberately does not merge them into the forest
cost/revenue ledger and it never allocates a regional or species aggregate to a
mill, a species, or a grade that the source did not report.

Source report:
  Enquete quinquennale sur les couts d'approvisionnement, de transformation et
  les revenus de l'industrie forestiere 2022 (Groupe DDM, September 2025).

Source compilation:
  Compilation des donnees regionalisees du regroupement des essences resineuses
  sapin, epinettes, pin gris et melezes (November 2025).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = (
    REPO_ROOT / "data/derived/national/harvest_basis/qc_cost_survey_2022"
)
DEFAULT_RAW = REPO_ROOT / "tmp/qc_cost_survey/pdf"

SURVEY_YEAR = 2022
REFERENCE_PERIOD = "FY2022 (fiscal year ending on or before 2023-03-31)"
OBSERVATION_TYPE = "survey_aggregate"

SOURCES: dict[str, dict[str, Any]] = {
    "main_2022": {
        "filename": "RA_couts_approvisionnement_transformation_revenus_2022.pdf",
        "url": (
            "https://cdn-contenu.quebec.ca/cdn-contenu/forets/documents/"
            "entreprises/statistiques/"
            "RA_couts_approvisionnement_transformation_revenus_2022.pdf"
        ),
        "sha256": (
            "d9971b8228bf10159f2536d1f7690f7e84b833106c63e5fd9bad1014fb6b6871"
        ),
        "bytes": 1_248_519,
        "pages": 61,
        "retrieved_utc": "2026-11-21T20:33:38Z",
        "title": (
            "Enquete quinquennale sur les couts d'approvisionnement, de "
            "transformation et les revenus de l'industrie forestiere 2022"
        ),
    },
    "regional_2022": {
        "filename": "Compilation_donnees_regionalisees_2022.pdf",
        "url": (
            "https://cdn-contenu.quebec.ca/cdn-contenu/forets/documents/"
            "entreprises/statistiques/"
            "Compilation_donnees_regionalisees_2022.pdf"
        ),
        "sha256": (
            "5263e2c3af94b6cdd656826323ea21baced6d226429d2a9c297ebbe047021139"
        ),
        "bytes": 695_116,
        "pages": 1,
        "retrieved_utc": "2026-11-21T20:33:38Z",
        "title": (
            "Compilation des donnees regionalisees du regroupement des "
            "essences resineuses sapin, epinettes, pin gris et melezes"
        ),
    },
    "survey_2019": {
        "filename": "RA_enquete_couts_operation_2019.pdf",
        "url": (
            "https://cdn-contenu.quebec.ca/cdn-contenu/forets/documents/"
            "entreprises/statistiques/RA_enquete_couts_operation_2019.pdf"
        ),
        "sha256": (
            "870090018a51d12b0c78adb67741eb10c524fc5d5aafc4dce96d101612dc6a1f"
        ),
        "bytes": 2_853_156,
        "pages": 64,
        "retrieved_utc": "2026-11-21T20:33:39Z",
        "title": (
            "Enquete 2019 sur les couts d'operation forestiere dans les forets "
            "du domaine de l'Etat ainsi que sur les couts et revenus de "
            "l'industrie du sciage du Quebec"
        ),
    },
    "regional_2019": {
        "filename": "Compilation_donnees_regionalisees_2019.pdf",
        "url": (
            "https://cdn-contenu.quebec.ca/cdn-contenu/forets/documents/"
            "entreprises/statistiques/"
            "Compilation_donnees_regionalisees_2019.pdf"
        ),
        "sha256": (
            "dc13bfd249046d6accdc80aace0e90e6850a682394c96bd55e38c423acb44118"
        ),
        "bytes": 187_301,
        "pages": 3,
        "retrieved_utc": "2026-11-21T20:33:39Z",
        "title": (
            "Compilation des donnees regionalisees des resultats de l'Enquete "
            "2019 sur les couts d'operation forestiere"
        ),
    },
    "sylviculture_public_2021_2022": {
        "filename": "RA_couts_sylviculture_foret-publique_2021-2022.pdf",
        "url": (
            "https://cdn-contenu.quebec.ca/cdn-contenu/forets/documents/"
            "entreprises/statistiques/"
            "RA_couts_sylviculture_foret-publique_2021-2022.pdf"
        ),
        "sha256": (
            "743b71b52725762040139391d987f99b3c21cc70159a61af5faf560216101a3a"
        ),
        "bytes": 4_177_901,
        "pages": 138,
        "retrieved_utc": "2026-11-21T20:33:40Z",
        "title": (
            "Enquete sur les couts de la sylviculture en foret publique pour "
            "la saison 2020-2021"
        ),
    },
    "sylviculture_private_2021_2022": {
        "filename": "RA_couts_sylviculture_foret-privee_2021-2022.pdf",
        "url": (
            "https://cdn-contenu.quebec.ca/cdn-contenu/forets/documents/"
            "entreprises/statistiques/"
            "RA_couts_sylviculture_foret-privee_2021-2022.pdf"
        ),
        "sha256": (
            "50c378e3538aeb2408adea1c9504ee7fb83c66b18a54fbaa6ca795ca5ff9d90b"
        ),
        "bytes": 2_115_377,
        "pages": 59,
        "retrieved_utc": "2026-11-21T20:33:41Z",
        "title": (
            "Enquete sur les couts de la sylviculture en foret privee pour la "
            "saison 2021-2022"
        ),
    },
}

REGION_LABELS = {
    "QC": "Québec (agrégat provincial)",
    "R01-R03-R11-R12": (
        "Bas-Saint-Laurent, Capitale-Nationale, Gaspésie, "
        "Chaudière-Appalaches"
    ),
    "R09": "Côte-Nord",
    "R02": "Saguenay–Lac-Saint-Jean",
    "R04": "Mauricie",
    "R14-R15-R07-R08": (
        "Lanaudière, Laurentides, Outaouais, Témiscamingue (sans Abitibi)"
    ),
    "R08": "Abitibi (sans Témiscamingue)",
    "R10": "Nord-du-Québec",
}

SPECIES_LABELS = {
    "sepm": "Sapin, épinettes, pin gris et mélèzes (SEPM)",
    "feuillus_durs": "Feuillus durs",
    "peupliers": "Peupliers",
    "pins": "Pins",
    "thuyas": "Thuyas",
}

POSITIVE_REVENUE = "positive_revenue"
PHYSICAL_QUANTITY = "physical_quantity"

NEGATIVE_SIGN_CONVENTIONS = {
    "negative_credit_or_loss",
    "signed_gain_loss",
    "signed_net",
}


@dataclass(frozen=True)
class ColumnSpec:
    """One value column in a source table."""

    index: int
    species_group: str
    product_or_grade: str = "total"
    unit: str = ""
    period: str = ""


@dataclass(frozen=True)
class RowSpec:
    """Canonical meaning of one source row label."""

    component_group: str
    component: str
    unit: str
    price_basis: str
    sign_convention: str = "positive_cost"
    subcomponent: str = ""
    observation_kind: str = "component"
    notes: str = ""


@dataclass(frozen=True)
class TableSpec:
    """Location and column/row semantics for one PDF table."""

    key: str
    source_id: str
    table_number: str
    page: int
    table_index: int
    title_fr: str
    columns: tuple[ColumnSpec, ...]
    row_map: dict[str, RowSpec]
    default_unit: str
    default_price_basis: str
    dash_meaning: str = "suppressed_lt3"
    section_labels: dict[str, str] = field(default_factory=dict)
    section_value_rows: dict[str, RowSpec] = field(default_factory=dict)
    keep_footnote_suffix: bool = False
    notes: str = ""
    pdf_page: int = 0
    three_respondents_columns: frozenset[tuple[str, str]] = frozenset()
    three_respondents_note: str = ""


def _row(
    group: str,
    component: str,
    unit: str,
    basis: str,
    sign: str = "positive_cost",
    sub: str = "",
    kind: str = "component",
    note: str = "",
) -> RowSpec:
    return RowSpec(group, component, unit, basis, sign, sub, kind, note)


def _cols(*pairs: tuple[int, str, str]) -> tuple[ColumnSpec, ...]:
    return tuple(ColumnSpec(index, species, product) for index, species, product in pairs)


CAD_M3 = "CAD/m3"
CAD_HA = "CAD/ha"
CAD_MPMP = "CAD/MPMP"
CAD_TMA = "CAD/tma"
CAD_TMV = "CAD/tmv"
M3_HA = "m3/ha"
M3_MPMP = "m3/MPMP"
PERCENT = "percent"
KM = "km"

HARVEST = "harvest"
ROAD = "road"
HAUL = "haul"
HOSTING = "hosting"
OTHER = "other_costs"
TRANSFER = "transfers"
TRANSFORM = "transformation"
FINISHED = "finished_goods_transport"
OVERHEAD = "overhead"
REVENUE = "revenue"
BYPRODUCT = "byproduct"
PURCHASE = "purchase"
SYNTHESIS = "synthesis_procurement"
SYNTH_REVENUE = "synthesis_revenue"
COMPARISON = "historical_comparison"

PHYSICAL_UNITS = frozenset({M3_HA, M3_MPMP, PERCENT, KM})
REVENUE_COMPONENT_GROUPS = frozenset({REVENUE, BYPRODUCT, SYNTH_REVENUE})
SIGN_CONVENTIONS = frozenset(
    {"positive_cost", POSITIVE_REVENUE, PHYSICAL_QUANTITY}
) | NEGATIVE_SIGN_CONVENTIONS

TABLE_SPECS: tuple[TableSpec, ...] = (
    TableSpec(
        key="T2",
        source_id="main_2022",
        table_number="2",
        page=12,
        table_index=1,
        title_fr="Cout total d'achat de bois en foret publique livre usine",
        columns=_cols(
            (1, "sepm", "total"),
            (2, "feuillus_durs", "sciage"),
            (4, "feuillus_durs", "trituration"),
            (6, "feuillus_durs", "total"),
            (8, "peupliers", "sciage"),
            (10, "peupliers", "trituration"),
            (12, "peupliers", "total"),
            (14, "pins", "total"),
            (15, "thuyas", "total"),
        ),
        row_map={
            "Achat de bois": _row(
                PURCHASE,
                "wood_purchase_delivered",
                CAD_M3,
                "delivered_to_mill",
                note=(
                    "Purchased-wood procurement cost including purchase, "
                    "transfers to the State, transport/unloading and road costs "
                    "where applicable; not observed log-sale revenue."
                ),
            ),
        },
        default_unit=CAD_M3,
        default_price_basis="delivered_to_mill",
    ),
    TableSpec(
        key="T3",
        source_id="main_2022",
        table_number="3",
        page=13,
        table_index=1,
        title_fr="Couts moyens ponderes lies a la recolte ($/m3)",
        columns=_cols(
            (2, "sepm", "total"),
            (4, "feuillus_durs", "sciage"),
            (6, "peupliers", "trituration"),
        ),
        row_map={
            "Recolte": _row(HARVEST, "harvest", CAD_M3, "roadside"),
            "Transport d'equipement": _row(
                HARVEST, "equipment_transport", CAD_M3, "roadside"
            ),
            "Tronconnage en foret": _row(
                HARVEST, "bucking_in_forest", CAD_M3, "roadside"
            ),
            "Nettoyage": _row(HARVEST, "site_cleanup", CAD_M3, "roadside"),
            "Autres couts": _row(HARVEST, "other_harvest_costs", CAD_M3, "roadside"),
            "Cout total de recolte": _row(
                HARVEST, "harvest_total", CAD_M3, "roadside"
            ),
            "Gains/pertes sur essences": _row(
                HARVEST,
                "species_gain_loss",
                CAD_M3,
                "roadside",
                "signed_gain_loss",
            ),
        },
        default_unit=CAD_M3,
        default_price_basis="roadside",
    ),
    TableSpec(
        key="T4",
        source_id="main_2022",
        table_number="4",
        page=13,
        table_index=2,
        title_fr="Parametres lies a la recolte",
        columns=_cols(
            (1, "sepm", "total"),
            (2, "feuillus_durs", "sciage"),
        ),
        row_map={
            "Volume moyen recolte (m3/ha)": _row(
                HARVEST,
                "mean_volume_harvested",
                M3_HA,
                "physical_ratio",
                PHYSICAL_QUANTITY,
            ),
            "Coupe partielle (%)": _row(
                HARVEST,
                "partial_cut_share",
                PERCENT,
                "physical_ratio",
                PHYSICAL_QUANTITY,
            ),
        },
        default_unit=M3_HA,
        default_price_basis="physical_ratio",
    ),
    TableSpec(
        key="T5",
        source_id="main_2022",
        table_number="5",
        page=14,
        table_index=1,
        title_fr="Couts moyens ponderes lies aux chemins ($/m3)",
        columns=_cols(
            (1, "sepm", "total"),
            (2, "feuillus_durs", "sciage"),
            (4, "peupliers", "trituration"),
        ),
        row_map={
            "Construction": _row(ROAD, "road_construction", CAD_M3, "access_cost"),
            "PRCCM (chemins)": _row(
                ROAD,
                "prccm_roads",
                CAD_M3,
                "access_cost_rebate",
                "negative_credit_or_loss",
            ),
            "Amortissement (chemins)": _row(
                ROAD, "road_amortization", CAD_M3, "access_cost"
            ),
            "Refection et amelioration": _row(
                ROAD, "road_upgrade", CAD_M3, "access_cost"
            ),
            "PRCCM (refection)": _row(
                ROAD,
                "prccm_upgrade",
                CAD_M3,
                "access_cost_rebate",
                "negative_credit_or_loss",
            ),
            "Ponts": _row(ROAD, "bridges", CAD_M3, "access_cost"),
            "PRCCM (ponts)": _row(
                ROAD,
                "prccm_bridges",
                CAD_M3,
                "access_cost_rebate",
                "negative_credit_or_loss",
            ),
            "Amortissement (ponts)": _row(
                ROAD, "bridge_amortization", CAD_M3, "access_cost"
            ),
            "Deneigement et entretien": _row(
                ROAD, "snow_removal_maintenance", CAD_M3, "access_cost"
            ),
            "Contribution a un tiers": _row(
                ROAD, "third_party_contribution", CAD_M3, "access_cost"
            ),
            "Revenu": _row(
                ROAD,
                "road_revenue",
                CAD_M3,
                "access_cost_rebate",
                "negative_credit_or_loss",
            ),
            "Transport d'equipement": _row(
                ROAD, "equipment_transport", CAD_M3, "access_cost"
            ),
            "Cout total associe aux chemins (sans PRCCM)": _row(
                ROAD, "road_total_before_prccm", CAD_M3, "access_cost_gross"
            ),
            "Cout total associe aux chemins (avec PRCCM)": _row(
                ROAD, "road_total_after_prccm", CAD_M3, "access_cost_net"
            ),
            "Pourcentage extrait sur chemins d'ete (%)": _row(
                ROAD,
                "summer_road_share",
                PERCENT,
                "physical_ratio",
                PHYSICAL_QUANTITY,
            ),
        },
        default_unit=CAD_M3,
        default_price_basis="access_cost",
    ),
    TableSpec(
        key="T6",
        source_id="main_2022",
        table_number="6",
        page=15,
        table_index=1,
        title_fr="Couts moyens ponderes lies au transport ($/m3)",
        columns=_cols(
            (1, "sepm", "total"),
            (2, "feuillus_durs", "sciage"),
            (4, "peupliers", "trituration"),
        ),
        row_map={
            "Chargement": _row(HAUL, "loading", CAD_M3, "hauled_to_mill"),
            "Dechargement": _row(HAUL, "unloading", CAD_M3, "hauled_to_mill"),
            "Transport": _row(HAUL, "road_transport", CAD_M3, "hauled_to_mill"),
            "Cout total de transport": _row(
                HAUL, "haul_total", CAD_M3, "hauled_to_mill"
            ),
        },
        default_unit=CAD_M3,
        default_price_basis="hauled_to_mill",
    ),
    TableSpec(
        key="T7",
        source_id="main_2022",
        table_number="7",
        page=15,
        table_index=2,
        title_fr="Donnees techniques liees au transport",
        columns=_cols(
            (1, "sepm", "total"),
            (2, "feuillus_durs", "sciage"),
            (3, "peupliers", "trituration"),
        ),
        row_map={},
        default_unit=PERCENT,
        default_price_basis="physical_ratio",
        section_labels={
            "Transport « conventionnel »": "conventional",
            "Transport « hors norme »": "oversize",
            "Transport par train": "rail",
        },
        section_value_rows={
            "Proportion (%)": _row(
                HAUL,
                "transport_mode_share",
                PERCENT,
                "physical_ratio",
                PHYSICAL_QUANTITY,
            ),
            "Distance moyenne (km)": _row(
                HAUL,
                "transport_distance",
                KM,
                "physical_ratio",
                PHYSICAL_QUANTITY,
            ),
        },
    ),
    TableSpec(
        key="T8",
        source_id="main_2022",
        table_number="8",
        page=16,
        table_index=1,
        title_fr="Couts moyens ponderes lies a l'hebergement ($/m3)",
        columns=_cols(
            (1, "sepm", "total"),
            (2, "feuillus_durs", "sciage"),
            (3, "peupliers", "sciage"),
            (5, "peupliers", "trituration"),
            (7, "pins", "total"),
        ),
        row_map={
            "Cout d'hebergement et frais d'eloignement1": _row(
                HOSTING,
                "hosting_all_respondents",
                CAD_M3,
                "delivered_to_mill_support",
            ),
            "Cout d'hebergement et frais d'eloignement2": _row(
                HOSTING,
                "hosting_respondents_with_cost",
                CAD_M3,
                "delivered_to_mill_support",
            ),
        },
        default_unit=CAD_M3,
        default_price_basis="delivered_to_mill_support",
        keep_footnote_suffix=True,
    ),
    TableSpec(
        key="T9",
        source_id="main_2022",
        table_number="9",
        page=16,
        table_index=2,
        title_fr="Couts moyens ponderes des autres frais ($/m3)",
        columns=_cols(
            (1, "sepm", "total"),
            (2, "feuillus_durs", "sciage"),
            (3, "feuillus_durs", "trituration"),
            (5, "peupliers", "sciage"),
            (6, "peupliers", "trituration"),
            (8, "pins", "total"),
            (9, "thuyas", "total"),
        ),
        row_map={
            "Mesurage officiel": _row(OTHER, "official_scaling", CAD_M3, "cost_incurred"),
            "Administration des operations (inclut le mesurage interne)": _row(
                OTHER, "operations_administration", CAD_M3, "cost_incurred"
            ),
            "Communications": _row(OTHER, "communications", CAD_M3, "cost_incurred"),
            "Planification": _row(OTHER, "planning", CAD_M3, "cost_incurred"),
            "Sante et securite": _row(OTHER, "health_safety", CAD_M3, "cost_incurred"),
            "Harmonisation": _row(OTHER, "harmonization", CAD_M3, "cost_incurred"),
            "Certification": _row(OTHER, "certification", CAD_M3, "cost_incurred"),
            "Gestion environnementale": _row(
                OTHER, "environmental_management", CAD_M3, "cost_incurred"
            ),
            "Formation": _row(OTHER, "training", CAD_M3, "cost_incurred"),
            "Amortissements et frais financiers lies aux operations forestieres": _row(
                OTHER, "forest_operations_amortization", CAD_M3, "cost_incurred"
            ),
            "Autres": _row(OTHER, "other", CAD_M3, "cost_incurred"),
            "Autres revenus": _row(
                OTHER,
                "other_revenue",
                CAD_M3,
                "cost_rebate",
                "negative_credit_or_loss",
            ),
            "Total des autres couts et frais": _row(
                OTHER, "other_costs_total", CAD_M3, "cost_incurred"
            ),
        },
        default_unit=CAD_M3,
        default_price_basis="cost_incurred",
    ),
    TableSpec(
        key="T10",
        source_id="main_2022",
        table_number="10",
        page=17,
        table_index=1,
        title_fr="Couts moyens ponderes des transferts a l'Etat ($/m3)",
        columns=_cols(
            (2, "sepm", "total"),
            (4, "feuillus_durs", "sciage"),
            (6, "peupliers", "sciage"),
            (8, "peupliers", "trituration"),
            (10, "pins", "total"),
            (12, "thuyas", "total"),
        ),
        row_map={
            "Redevance": _row(TRANSFER, "annual_rent", CAD_M3, "per_m3_harvested"),
            "VMBSP": _row(TRANSFER, "vmbsp", CAD_M3, "per_m3_harvested"),
            "Encheres": _row(
                "market_reference",
                "auction_price",
                CAD_M3,
                "auction_reference",
                "positive_cost",
                note="Auction price shown as a reference; not part of the reported transfer total.",
            ),
            "Compensations": _row(
                TRANSFER,
                "compensations",
                CAD_M3,
                "per_m3_harvested",
                "negative_credit_or_loss",
            ),
            "Plan special": _row(
                TRANSFER,
                "special_plan",
                CAD_M3,
                "per_m3_harvested",
                "negative_credit_or_loss",
            ),
            "Travaux sylvicoles": _row(
                TRANSFER,
                "silvicultural_works",
                CAD_M3,
                "per_m3_harvested",
                "negative_credit_or_loss",
            ),
            "Mesures d'acces": _row(
                TRANSFER,
                "access_measures",
                CAD_M3,
                "per_m3_harvested",
                "negative_credit_or_loss",
            ),
            "Entente (Cris du Quebec)": _row(
                TRANSFER,
                "cree_agreement",
                CAD_M3,
                "per_m3_harvested",
                "negative_credit_or_loss",
            ),
            "Autres mesures gouvernementales": _row(
                TRANSFER,
                "other_government_measures",
                CAD_M3,
                "per_m3_harvested",
                "negative_credit_or_loss",
            ),
            "Autres": _row(
                TRANSFER,
                "other",
                CAD_M3,
                "per_m3_harvested",
                "negative_credit_or_loss",
            ),
            "Total des transferts a l'Etat": _row(
                TRANSFER,
                "transfers_total",
                CAD_M3,
                "per_m3_harvested",
                "signed_net",
            ),
        },
        default_unit=CAD_M3,
        default_price_basis="per_m3_harvested",
    ),
    TableSpec(
        key="T11",
        source_id="main_2022",
        table_number="11",
        page=18,
        table_index=1,
        title_fr="Couts moyens ponderes lies a la transformation ($/m3)",
        columns=_cols(
            (1, "sepm", "sciage"),
            (2, "feuillus_durs", "sciage"),
            (4, "peupliers", "sciage"),
            (5, "pins", "sciage"),
        ),
        row_map={
            "Manutention": _row(TRANSFORM, "handling", CAD_M3, "per_m3_input_mill"),
            "Balance": _row(TRANSFORM, "scaling", CAD_M3, "per_m3_input_mill"),
            "Tronconnage (usine)": _row(TRANSFORM, "bucking_mill", CAD_M3, "per_m3_input_mill"),
            "Sciage": _row(TRANSFORM, "sawing", CAD_M3, "per_m3_input_mill"),
            "Rabotage (usine)": _row(TRANSFORM, "planing_mill", CAD_M3, "per_m3_input_mill"),
            "Rabotage (externe)": _row(
                TRANSFORM, "planing_external", CAD_M3, "per_m3_input_mill"
            ),
            "Sechage interne et externe": _row(
                TRANSFORM, "drying_total", CAD_M3, "per_m3_input_mill"
            ),
            "Sapin": _row(
                TRANSFORM,
                "drying_by_species",
                CAD_M3,
                "per_m3_input_mill",
                sub="balsam_fir",
                kind="species_breakdown_non_additive",
            ),
            "Epinettes": _row(
                TRANSFORM,
                "drying_by_species",
                CAD_M3,
                "per_m3_input_mill",
                sub="spruce",
                kind="species_breakdown_non_additive",
            ),
            "Pin gris": _row(
                TRANSFORM,
                "drying_by_species",
                CAD_M3,
                "per_m3_input_mill",
                sub="jack_pine",
                kind="species_breakdown_non_additive",
            ),
            "Melezes": _row(
                TRANSFORM,
                "drying_by_species",
                CAD_M3,
                "per_m3_input_mill",
                sub="larch",
                kind="species_breakdown_non_additive",
            ),
            "Erables": _row(
                TRANSFORM,
                "drying_by_species",
                CAD_M3,
                "per_m3_input_mill",
                sub="maple",
                kind="species_breakdown_non_additive",
            ),
            "Bouleau jaune": _row(
                TRANSFORM,
                "drying_by_species",
                CAD_M3,
                "per_m3_input_mill",
                sub="yellow_birch",
                kind="species_breakdown_non_additive",
            ),
            "Bouleau a papier": _row(
                TRANSFORM,
                "drying_by_species",
                CAD_M3,
                "per_m3_input_mill",
                sub="paper_birch",
                kind="species_breakdown_non_additive",
            ),
            "Chenes": _row(
                TRANSFORM,
                "drying_by_species",
                CAD_M3,
                "per_m3_input_mill",
                sub="oak",
                kind="species_breakdown_non_additive",
            ),
            "Autres feuillus": _row(
                TRANSFORM,
                "drying_by_species",
                CAD_M3,
                "per_m3_input_mill",
                sub="other_hardwoods",
                kind="species_breakdown_non_additive",
            ),
            "Pins": _row(
                TRANSFORM,
                "drying_by_species",
                CAD_M3,
                "per_m3_input_mill",
                sub="pines",
                kind="species_breakdown_non_additive",
            ),
            "Thuyas": _row(
                TRANSFORM,
                "drying_by_species",
                CAD_M3,
                "per_m3_input_mill",
                sub="cedar",
                kind="species_breakdown_non_additive",
            ),
            "Chargement produits finis": _row(
                TRANSFORM, "finished_goods_loading", CAD_M3, "per_m3_input_mill"
            ),
            "Transport inter-usine": _row(
                TRANSFORM, "inter_mill_transport", CAD_M3, "per_m3_input_mill"
            ),
            "Amortissement et frais financiers lies a l'usine": _row(
                TRANSFORM, "mill_amortization", CAD_M3, "per_m3_input_mill"
            ),
            "Autres couts": _row(TRANSFORM, "other", CAD_M3, "per_m3_input_mill"),
            "Total transformation ($/m3)": _row(
                TRANSFORM, "transformation_total", CAD_M3, "per_m3_input_mill"
            ),
        },
        default_unit=CAD_M3,
        default_price_basis="per_m3_input_mill",
    ),
    TableSpec(
        key="T12",
        source_id="main_2022",
        table_number="12",
        page=18,
        table_index=2,
        title_fr="Rendement de sciage",
        columns=_cols(
            (1, "sepm", "sciage"),
            (2, "feuillus_durs", "total"),
            (4, "peupliers", "sciage"),
            (5, "pins", "sciage"),
            (6, "thuyas", "sciage"),
        ),
        row_map={
            "Rendement du sciage (m3/Mpmp)": _row(
                TRANSFORM,
                "sawmill_recovery",
                M3_MPMP,
                "physical_ratio",
                PHYSICAL_QUANTITY,
            ),
            "Volume non marchand (%)": _row(
                TRANSFORM,
                "non_merchant_volume",
                PERCENT,
                "physical_ratio",
                PHYSICAL_QUANTITY,
            ),
            "Mise en copeaux directe ($/m3)": _row(
                TRANSFORM, "direct_chipping", CAD_M3, "per_m3_input_mill"
            ),
        },
        default_unit=M3_MPMP,
        default_price_basis="physical_ratio",
    ),
    TableSpec(
        key="T13",
        source_id="main_2022",
        table_number="13",
        page=19,
        table_index=1,
        title_fr="Couts moyens ponderes du transport vers les marches ($/m3)",
        columns=_cols(
            (1, "sepm", "sciage"),
            (2, "feuillus_durs", "sciage"),
            (4, "peupliers", "total"),
            (6, "thuyas", "total"),
        ),
        row_map={
            "Transport aux marches (sciage)": _row(
                FINISHED, "market_transport", CAD_M3, "delivered_to_market"
            ),
            "Cout de transport des produits conjoints": _row(
                FINISHED, "co_product_transport", CAD_M3, "delivered_to_market"
            ),
            "Distance moyenne (km)": _row(
                FINISHED,
                "market_distance",
                KM,
                "physical_ratio",
                PHYSICAL_QUANTITY,
            ),
        },
        default_unit=CAD_M3,
        default_price_basis="delivered_to_market",
    ),
    TableSpec(
        key="T14",
        source_id="main_2022",
        table_number="14",
        page=19,
        table_index=2,
        title_fr="Frais generaux ($/m3)",
        columns=_cols(
            (2, "sepm", "total"),
            (4, "feuillus_durs", "sciage"),
            (6, "feuillus_durs", "trituration"),
            (8, "peupliers", "sciage"),
            (10, "pins", "total"),
            (12, "thuyas", "total"),
        ),
        row_map={
            "Service de ventes": _row(OVERHEAD, "sales", CAD_M3, "per_m3_input_mill"),
            "Administration generale (holding)": _row(
                OVERHEAD, "general_administration", CAD_M3, "per_m3_input_mill"
            ),
            "Recherche et developpement": _row(
                OVERHEAD, "research_development", CAD_M3, "per_m3_input_mill"
            ),
            "Autres": _row(OVERHEAD, "other", CAD_M3, "per_m3_input_mill"),
            "Compensations financieres": _row(
                OVERHEAD,
                "financial_compensations",
                CAD_M3,
                "cost_rebate",
                "negative_credit_or_loss",
            ),
            "Frais generaux": _row(
                OVERHEAD, "overhead_total", CAD_M3, "per_m3_input_mill"
            ),
        },
        default_unit=CAD_M3,
        default_price_basis="per_m3_input_mill",
        notes=(
            "Body-table peupliers-sciage components do not sum to the printed "
            "total (10.66 vs 13.87). Annex A1 reports 2.06, 16.51, 0.05, 0.12, "
            "-4.87 and total 13.87 for the same cells. Body values are retained "
            "as printed and the discrepancy is flagged in the manifest."
        ),
    ),
    TableSpec(
        key="T15",
        source_id="main_2022",
        table_number="15",
        page=20,
        table_index=1,
        title_fr="Revenus bruts SEPM moyens par trimestre",
        columns=(
            ColumnSpec(1, "sepm", "lumber", period="2022Q1"),
            ColumnSpec(3, "sepm", "lumber", period="2022Q2"),
            ColumnSpec(5, "sepm", "lumber", period="2022Q3"),
            ColumnSpec(7, "sepm", "lumber", period="2022Q4"),
            ColumnSpec(8, "sepm", "lumber", period="2023Q1"),
        ),
        row_map={
            "$/MPMP": _row(
                REVENUE,
                "gross_revenue",
                CAD_MPMP,
                "gross_revenue",
                POSITIVE_REVENUE,
            ),
            "$/m3": _row(
                REVENUE,
                "gross_revenue",
                CAD_M3,
                "gross_revenue",
                POSITIVE_REVENUE,
            ),
            "% E.-U.": _row(
                REVENUE,
                "us_export_share",
                PERCENT,
                "physical_ratio",
                PHYSICAL_QUANTITY,
            ),
        },
        default_unit=CAD_MPMP,
        default_price_basis="gross_revenue",
    ),
    TableSpec(
        key="T16",
        source_id="main_2022",
        table_number="16",
        page=20,
        table_index=3,
        title_fr="Revenus bruts et nets moyens SEPM",
        columns=(
            ColumnSpec(1, "sepm", "lumber", unit=CAD_MPMP),
            ColumnSpec(3, "sepm", "lumber", unit=CAD_M3),
        ),
        row_map={
            "Revenus bruts": _row(
                REVENUE,
                "gross_revenue",
                CAD_MPMP,
                "gross_revenue",
                POSITIVE_REVENUE,
            ),
            "Revenus nets": _row(
                REVENUE,
                "net_revenue",
                CAD_MPMP,
                "net_revenue",
                POSITIVE_REVENUE,
            ),
        },
        default_unit=CAD_MPMP,
        default_price_basis="gross_revenue",
    ),
    TableSpec(
        key="T17",
        source_id="main_2022",
        table_number="17",
        page=21,
        table_index=1,
        title_fr="Revenus bruts et nets des feuillus durs et des pins",
        columns=(
            ColumnSpec(1, "feuillus_durs", "lumber", unit=CAD_MPMP),
            ColumnSpec(3, "feuillus_durs", "lumber", unit=CAD_M3),
            ColumnSpec(5, "pins", "lumber", unit=CAD_MPMP),
            ColumnSpec(7, "pins", "lumber", unit=CAD_M3),
        ),
        row_map={
            "Revenus bruts": _row(
                REVENUE,
                "gross_revenue",
                CAD_MPMP,
                "gross_revenue",
                POSITIVE_REVENUE,
            ),
            "Revenus nets": _row(
                REVENUE,
                "net_revenue",
                CAD_MPMP,
                "net_revenue",
                POSITIVE_REVENUE,
            ),
        },
        default_unit=CAD_MPMP,
        default_price_basis="gross_revenue",
    ),
    TableSpec(
        key="T18",
        source_id="main_2022",
        table_number="18",
        page=21,
        table_index=2,
        title_fr="Revenus des sous-produits ($/tonne)",
        columns=_cols(
            (1, "sepm", "byproduct"),
            (3, "feuillus_durs", "byproduct"),
        ),
        row_map={
            "Copeaux": _row(
                BYPRODUCT,
                "chips",
                CAD_TMA,
                "gross_byproduct_revenue",
                POSITIVE_REVENUE,
            ),
            "Sciures": _row(
                BYPRODUCT,
                "sawdust",
                CAD_TMA,
                "gross_byproduct_revenue",
                POSITIVE_REVENUE,
            ),
            "Rabotures": _row(
                BYPRODUCT,
                "shavings",
                CAD_TMA,
                "gross_byproduct_revenue",
                POSITIVE_REVENUE,
            ),
            "Ecorces": _row(
                BYPRODUCT,
                "bark",
                CAD_TMV,
                "gross_byproduct_revenue",
                POSITIVE_REVENUE,
            ),
            "Residus de bois": _row(
                BYPRODUCT,
                "wood_residues",
                CAD_TMA,
                "gross_byproduct_revenue",
                POSITIVE_REVENUE,
            ),
        },
        default_unit=CAD_TMA,
        default_price_basis="gross_byproduct_revenue",
    ),
    TableSpec(
        key="T19",
        source_id="main_2022",
        table_number="19",
        page=21,
        table_index=3,
        title_fr="Revenus des sous-produits ($/m3)",
        columns=_cols(
            (2, "sepm", "byproduct"),
            (4, "feuillus_durs", "byproduct"),
        ),
        row_map={
            "Copeaux": _row(
                BYPRODUCT,
                "chips",
                CAD_M3,
                "gross_byproduct_revenue",
                POSITIVE_REVENUE,
            ),
            "Sciures": _row(
                BYPRODUCT,
                "sawdust",
                CAD_M3,
                "gross_byproduct_revenue",
                POSITIVE_REVENUE,
            ),
            "Rabotures": _row(
                BYPRODUCT,
                "shavings",
                CAD_M3,
                "gross_byproduct_revenue",
                POSITIVE_REVENUE,
            ),
            "Ecorces": _row(
                BYPRODUCT,
                "bark",
                CAD_M3,
                "gross_byproduct_revenue",
                POSITIVE_REVENUE,
            ),
            "Residus de bois": _row(
                BYPRODUCT,
                "wood_residues",
                CAD_M3,
                "gross_byproduct_revenue",
                POSITIVE_REVENUE,
            ),
            "Total": _row(
                BYPRODUCT,
                "byproduct_total",
                CAD_M3,
                "gross_byproduct_revenue",
                POSITIVE_REVENUE,
            ),
        },
        default_unit=CAD_M3,
        default_price_basis="gross_byproduct_revenue",
    ),
    TableSpec(
        key="T20",
        source_id="main_2022",
        table_number="20",
        page=22,
        table_index=1,
        title_fr="Synthese des couts d'approvisionnement ($/m3)",
        columns=_cols(
            (2, "sepm", "total"),
            (4, "feuillus_durs", "sciage"),
            (5, "peupliers", "trituration"),
        ),
        row_map={
            "Recolte": _row(
                SYNTHESIS, "harvest", CAD_M3, "roadside", kind="synthesis"
            ),
            "Chemins": _row(
                SYNTHESIS,
                "road",
                CAD_M3,
                "access_cost_gross",
                kind="synthesis",
            ),
            "Transport": _row(
                SYNTHESIS, "haul", CAD_M3, "hauled_to_mill", kind="synthesis"
            ),
            "Hebergement": _row(
                SYNTHESIS, "hosting", CAD_M3, "delivered_to_mill_support",
                kind="synthesis",
            ),
            "Autres couts": _row(
                SYNTHESIS, "other_costs", CAD_M3, "cost_incurred", kind="synthesis"
            ),
            "Total (sans transfert a l'Etat)": _row(
                SYNTHESIS, "procurement_total_before_transfers", CAD_M3,
                "delivered_to_mill", kind="synthesis",
            ),
            "Total des transferts a l'Etat": _row(
                SYNTHESIS, "transfers_total", CAD_M3, "per_m3_harvested",
                "signed_net", kind="synthesis",
            ),
            "Total (avec transfert a l'Etat)": _row(
                SYNTHESIS, "procurement_total_after_transfers", CAD_M3,
                "delivered_to_mill", kind="synthesis",
            ),
        },
        default_unit=CAD_M3,
        default_price_basis="delivered_to_mill",
        three_respondents_columns=frozenset({("peupliers", "trituration")}),
        three_respondents_note=(
            "T20 prints this cell in black, but the peupliers-trituration "
            "supply-cost basis is the three-respondent sample already red-flagged "
            "in T3/T5/T10/T21; interpret with caution."
        ),
    ),
    TableSpec(
        key="T21",
        source_id="main_2022",
        table_number="21",
        page=22,
        table_index=2,
        title_fr="Cout total d'achat de bois en foret publique ($/m3)",
        columns=_cols(
            (1, "sepm", "total"),
            (2, "feuillus_durs", "sciage"),
            (4, "feuillus_durs", "trituration"),
            (6, "peupliers", "sciage"),
            (7, "peupliers", "trituration"),
            (8, "peupliers", "total"),
            (9, "pins", "total"),
            (10, "thuyas", "total"),
        ),
        row_map={
            "Achats de bois (livre)": _row(
                PURCHASE,
                "wood_purchase_delivered",
                CAD_M3,
                "delivered_to_mill",
                kind="synthesis",
                note=(
                    "Purchased-wood procurement cost channel (purchase, transfers "
                    "to the State, transport/unloading and road costs where "
                    "applicable); not observed log-sale revenue."
                ),
            ),
            "Approvisionnement (BGAD)": _row(
                PURCHASE, "bgad_supply_cost", CAD_M3, "delivered_to_mill",
                kind="synthesis",
            ),
            "Cout moyen d'approvisionnement": _row(
                PURCHASE, "mean_supply_cost", CAD_M3, "delivered_to_mill",
                kind="synthesis",
            ),
        },
        default_unit=CAD_M3,
        default_price_basis="delivered_to_mill",
    ),
    TableSpec(
        key="T22",
        source_id="main_2022",
        table_number="22",
        page=23,
        table_index=1,
        title_fr="Synthese des revenus ($/m3)",
        columns=_cols(
            (2, "sepm", "total"),
            (4, "feuillus_durs", "total"),
        ),
        row_map={
            "Revenus nets": _row(
                SYNTH_REVENUE,
                "net_revenue",
                CAD_M3,
                "net_revenue",
                POSITIVE_REVENUE,
                kind="synthesis",
            ),
            "Revenus des sous-produits": _row(
                SYNTH_REVENUE,
                "byproduct_revenue",
                CAD_M3,
                "gross_byproduct_revenue",
                POSITIVE_REVENUE,
                kind="synthesis",
            ),
            "Gains/pertes": _row(
                SYNTH_REVENUE, "species_gain_loss", CAD_M3, "roadside",
                "signed_gain_loss", kind="synthesis",
            ),
            "Total": _row(
                SYNTH_REVENUE,
                "total_revenue",
                CAD_M3,
                "net_revenue",
                POSITIVE_REVENUE,
                kind="synthesis",
            ),
        },
        default_unit=CAD_M3,
        default_price_basis="net_revenue",
    ),
)

A2_COLUMNS = (
    ColumnSpec(2, "sepm", "total"),
    ColumnSpec(4, "sepm", "total"),
    ColumnSpec(6, "sepm", "total"),
    ColumnSpec(8, "feuillus_durs", "sciage"),
    ColumnSpec(10, "feuillus_durs", "sciage"),
    ColumnSpec(12, "feuillus_durs", "sciage"),
    ColumnSpec(14, "peupliers", "trituration"),
    ColumnSpec(16, "peupliers", "trituration"),
    ColumnSpec(18, "peupliers", "trituration"),
)

A2_YEARS = (2014, 2019, 2022, 2014, 2019, 2022, 2014, 2019, 2022)

A2_SAMPLE_N = {
    ("sepm", 2014): 33,
    ("sepm", 2019): 31,
    ("sepm", 2022): 37,
    ("feuillus_durs", 2014): 8,
    ("feuillus_durs", 2019): 11,
    ("feuillus_durs", 2022): 11,
    ("peupliers", 2014): 7,
    ("peupliers", 2019): 10,
    ("peupliers", 2022): 9,
}

A2_TABLE = TableSpec(
    key="A2",
    source_id="main_2022",
    table_number="A2",
    page=37,
    table_index=1,
    title_fr="Comparaison des resultats de 2014, 2019 et 2022 ($/m3)",
    pdf_page=37,
    columns=A2_COLUMNS,
    row_map={
        "Recolte": _row(
            COMPARISON, "harvest", CAD_M3, "roadside", kind="historical_comparison"
        ),
        "Chemins": _row(
            COMPARISON, "road", CAD_M3, "access_cost", kind="historical_comparison"
        ),
        "Transport": _row(
            COMPARISON, "haul", CAD_M3, "hauled_to_mill", kind="historical_comparison"
        ),
        "Hebergement": _row(
            COMPARISON, "hosting", CAD_M3, "delivered_to_mill_support",
            kind="historical_comparison",
        ),
        "Autres couts": _row(
            COMPARISON, "other_costs", CAD_M3, "cost_incurred",
            kind="historical_comparison",
        ),
        "Total (sans transfert a l'Etat)": _row(
            COMPARISON, "procurement_total_before_transfers", CAD_M3,
            "delivered_to_mill", kind="historical_comparison",
        ),
        "Redevance": _row(
            COMPARISON, "annual_rent", CAD_M3, "per_m3_harvested",
            kind="historical_comparison",
        ),
        "VMBSP": _row(
            COMPARISON, "vmbsp", CAD_M3, "per_m3_harvested",
            kind="historical_comparison",
        ),
        "SOPFIM": _row(
            COMPARISON, "sopfim", CAD_M3, "per_m3_harvested",
            kind="historical_comparison",
        ),
        "SOPFEU": _row(
            COMPARISON, "sopfeu", CAD_M3, "per_m3_harvested",
            kind="historical_comparison",
        ),
        "Compensations": _row(
            COMPARISON, "compensations", CAD_M3, "per_m3_harvested",
            "negative_credit_or_loss", kind="historical_comparison",
        ),
        "Total des transferts a l'Etat": _row(
            COMPARISON, "transfers_total", CAD_M3, "per_m3_harvested",
            "signed_net", kind="historical_comparison",
        ),
        "Total approvisionnement": _row(
            COMPARISON, "procurement_total_after_transfers", CAD_M3,
            "delivered_to_mill", kind="historical_comparison",
        ),
        "Total transformation ($/m3)": _row(
            COMPARISON, "transformation_total", CAD_M3, "per_m3_input_mill",
            kind="historical_comparison",
        ),
        "Transport aux marches": _row(
            COMPARISON, "market_transport", CAD_M3, "delivered_to_market",
            kind="historical_comparison",
        ),
    },
    default_unit=CAD_M3,
    default_price_basis="delivered_to_mill",
)

REGIONAL_TABLE = TableSpec(
    key="REG",
    source_id="regional_2022",
    table_number="1",
    page=1,
    table_index=0,
    title_fr="Resultats regionaux - SEPM ($/m3)",
    columns=(
        ColumnSpec(1, "sepm", "total"),
        ColumnSpec(3, "sepm", "total"),
        ColumnSpec(4, "sepm", "total"),
        ColumnSpec(5, "sepm", "total"),
        ColumnSpec(6, "sepm", "total"),
        ColumnSpec(7, "sepm", "total"),
        ColumnSpec(8, "sepm", "total"),
        ColumnSpec(9, "sepm", "total"),
    ),
    row_map={
        "Recolte": _row(
            SYNTHESIS, "harvest", CAD_M3, "roadside", kind="regional_survey_aggregate"
        ),
        "Chemins (PRCM non inclus)": _row(
            SYNTHESIS,
            "road",
            CAD_M3,
            "access_cost_gross",
            kind="regional_survey_aggregate",
        ),
        "Transport": _row(
            SYNTHESIS, "haul", CAD_M3, "hauled_to_mill",
            kind="regional_survey_aggregate",
        ),
        "Hebergement": _row(
            SYNTHESIS, "hosting", CAD_M3, "delivered_to_mill_support",
            kind="regional_survey_aggregate",
        ),
        "Autres couts": _row(
            SYNTHESIS, "other_costs", CAD_M3, "cost_incurred",
            kind="regional_survey_aggregate",
        ),
        "Total (sans transfert a l'Etat)": _row(
            SYNTHESIS, "procurement_total_before_transfers", CAD_M3,
            "delivered_to_mill", kind="regional_survey_aggregate",
        ),
        "Redevance annuelle": _row(
            SYNTHESIS, "annual_rent", CAD_M3, "per_m3_harvested",
            kind="regional_survey_aggregate",
        ),
        "VMBSP": _row(
            SYNTHESIS, "vmbsp", CAD_M3, "per_m3_harvested",
            kind="regional_survey_aggregate",
        ),
        "Compensations": _row(
            SYNTHESIS, "compensations", CAD_M3, "per_m3_harvested",
            "negative_credit_or_loss", kind="regional_survey_aggregate",
        ),
        "Total des transferts a l'Etat": _row(
            SYNTHESIS, "transfers_total", CAD_M3, "per_m3_harvested",
            "signed_net", kind="regional_survey_aggregate",
        ),
        "Total approvisionnement": _row(
            SYNTHESIS, "procurement_total_after_transfers", CAD_M3,
            "delivered_to_mill", kind="regional_survey_aggregate",
        ),
    },
    default_unit=CAD_M3,
    default_price_basis="delivered_to_mill",
)

REGIONAL_CODES = (
    "R01-R03-R11-R12",
    "R09",
    "R02",
    "R04",
    "R14-R15-R07-R08",
    "R08",
    "R10",
    "QC",
)

CSV_COLUMNS = (
    "source_id",
    "source_table",
    "source_page",
    "survey_year",
    "reference_period",
    "region_code",
    "region_label",
    "aggregation_level",
    "species_group",
    "species_label",
    "product_or_grade",
    "component_group",
    "component",
    "subcomponent",
    "source_label_fr",
    "value",
    "unit",
    "price_basis",
    "sample_n",
    "observation_type",
    "observation_kind",
    "confidentiality_flag",
    "sign_convention",
    "notes",
)

COVERAGE_COLUMNS = (
    "source_id",
    "source_table",
    "source_page",
    "title_fr",
    "parsed_status",
    "rows_emitted",
    "values_missing",
    "values_suppressed",
    "values_flagged_three_respondents",
    "notes",
)

_DASHES = {"–", "-", "—", "−"}
_MISSING = {
    "N.D.": "not_available",
    "N.A.": "not_available",
    "N.D": "not_available",
    "S. O.": "not_applicable",
    "S.O.": "not_applicable",
    "S.O": "not_applicable",
}
_FOOTNOTE_SUFFIX = re.compile(r"(?<=[A-Za-z\)])([12])$")
_NUMBER_CLEAN = re.compile(r"[ \u00a0\u202f\u2009$%*]")


def _strip_accents_for_match(text: str) -> str:
    """Return a lower-case ASCII key for matching PDF row labels.

    Accents are folded only for lookup; the published CSV keeps the original
    French label in ``source_label_fr``.
    """
    replacements = str.maketrans(
        "àâäéèêëîïôöùûüçÀÂÄÉÈÊËÎÏÔÖÙÛÜÇ’'",
        "aaaeeeeiioouuucAAAEEEEIIOOUUUC''",
    )
    return text.translate(replacements).lower().strip()


def canonical_row_label(text: str | None, *, keep_footnote: bool = False) -> str:
    """Normalize a PDF row label to the key used in table specs."""
    if text is None:
        return ""
    label = str(text).replace("\n", " ").replace("\u00a0", " ")
    label = re.sub(r"\s+", " ", label).strip()
    if not keep_footnote:
        label = _FOOTNOTE_SUFFIX.sub("", label)
    return _strip_accents_for_match(label)


def normalize_number_token(text: str | None) -> tuple[str, str]:
    """Return (numeric_text, unit_hint) for a PDF cell.

    ``unit_hint`` is ``""``, ``"percent"``, ``"CAD/tma"`` or ``"CAD/tmv"``.
    The caller chooses the table's default unit when the hint is empty.
    """
    if text is None:
        return "", ""
    raw = str(text).strip()
    if not raw:
        return "", ""
    unit_hint = ""
    if "$/tma" in raw.lower() or "/tma" in raw.lower():
        unit_hint = "CAD/tma"
    elif "$/tmv" in raw.lower() or "/tmv" in raw.lower():
        unit_hint = "CAD/tmv"
    elif "%" in raw:
        unit_hint = "percent"
    cleaned = _NUMBER_CLEAN.sub("", raw)
    cleaned = re.sub(r"/\s*t(?:ma|mv)", "", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.replace(",", ".")
    return cleaned, unit_hint


def parse_french_number(text: str | None, *, context: str = "value") -> float | None:
    """Parse a French-formatted survey number.

    Dashes and explicit non-availability tokens return ``None``; malformed or
    non-finite numbers raise ``ValueError`` so a bad parse cannot enter the
    dataset silently.
    """
    cleaned, _ = normalize_number_token(text)
    if cleaned == "":
        return None
    if cleaned in {"–", "-", "—", "−"}:
        return None
    try:
        value = float(cleaned)
    except ValueError as exc:
        raise ValueError(f"{context}: cannot parse {text!r}") from exc
    if not math.isfinite(value):
        raise ValueError(f"{context}: non-finite value {text!r}")
    return value


def _is_numeric_cell(text: str | None) -> bool:
    try:
        return parse_french_number(text) is not None
    except ValueError:
        return False


def parse_cell_value(
    raw: str | None,
    *,
    default_unit: str,
    dash_meaning: str,
    context: str,
) -> tuple[float | None, str, str, str]:
    """Return (value, unit, confidentiality_flag, notes) for one table cell."""
    if raw is None:
        return None, default_unit, "suppressed_lt3", ""
    text = str(raw).strip()
    if text == "":
        return None, default_unit, "suppressed_lt3", ""
    folded = _strip_accents_for_match(text).replace(" ", "")
    if text in _DASHES or folded in {"-", "–"}:
        return None, default_unit, dash_meaning, ""
    if folded in {k.replace(" ", "").lower() for k in _MISSING}:
        for token, meaning in _MISSING.items():
            if folded == token.replace(" ", "").lower():
                return None, default_unit, meaning, ""
    unit_hint = ""
    if "$/tma" in text.lower():
        unit_hint = CAD_TMA
    elif "$/tmv" in text.lower():
        unit_hint = CAD_TMV
    elif "%" in text:
        unit_hint = PERCENT
    asterisk_note = ""
    if "*" in text:
        asterisk_note = "Source value marked with an asterisk (partial coverage)."
    value = parse_french_number(text, context=context)
    if value is None:
        return None, unit_hint or default_unit, dash_meaning, asterisk_note
    return value, unit_hint or default_unit, "none", asterisk_note


def _red_char_spans(page: Any) -> list[tuple[float, float, float, float]]:
    spans: list[tuple[float, float, float, float]] = []
    for char in page.chars:
        colour = char.get("non_stroking_color")
        if colour == (0.933, 0.0, 0.0):
            spans.append((char["x0"], char["top"], char["x1"], char["bottom"]))
    return spans


def _cell_has_red(
    bbox: tuple[float, float, float, float] | None,
    red_spans: list[tuple[float, float, float, float]],
) -> bool:
    if bbox is None:
        return False
    x0, top, x1, bottom = bbox
    for rx0, rtop, rx1, rbottom in red_spans:
        cx = (rx0 + rx1) / 2
        cy = (rtop + rbottom) / 2
        if x0 <= cx <= x1 and top <= cy <= bottom:
            return True
    return False


@dataclass
class EmittedRow:
    source_id: str
    source_table: str
    source_page: int
    survey_year: int
    reference_period: str
    region_code: str
    region_label: str
    aggregation_level: str
    species_group: str
    species_label: str
    product_or_grade: str
    component_group: str
    component: str
    subcomponent: str
    source_label_fr: str
    value: float
    unit: str
    price_basis: str
    sample_n: int | None
    observation_type: str
    observation_kind: str
    confidentiality_flag: str
    sign_convention: str
    notes: str

    def as_csv(self) -> dict[str, str]:
        row: dict[str, str] = {}
        for column in CSV_COLUMNS:
            value = getattr(self, column)
            row[column] = "" if value is None else str(value)
        return row


@dataclass
class TableCoverage:
    source_id: str
    source_table: str
    source_page: int
    title_fr: str
    parsed_status: str
    rows_emitted: int = 0
    values_missing: int = 0
    values_suppressed: int = 0
    values_flagged_three_respondents: int = 0
    notes: str = ""

    def as_csv(self) -> dict[str, str]:
        return {column: str(getattr(self, column)) for column in COVERAGE_COLUMNS}


def _context(spec: TableSpec, label: str, column: ColumnSpec) -> str:
    return f"{spec.key} p.{spec.page} {label} [{column.species_group}/{column.product_or_grade}]"


def _combine_notes(*notes: str) -> str:
    """Join non-empty notes with a single space."""
    return " ".join(note.strip() for note in notes if note and note.strip())


def _region_context(
    column: ColumnSpec,
    region_by_column: dict[int, str] | None,
    sample_n_by_region: dict[str, int] | None,
) -> tuple[str, str, int | None]:
    if region_by_column is None:
        return "QC", "provincial", None
    region_code = region_by_column[column.index]
    aggregation = "provincial" if region_code == "QC" else "regional"
    sample_n = sample_n_by_region.get(region_code) if sample_n_by_region else None
    return region_code, aggregation, sample_n


def _three_respondent_basis(
    spec: TableSpec,
    column: ColumnSpec,
    row_index: int,
    red_cells: set[tuple[int, int]],
    sample_n: int | None,
) -> bool:
    """Return whether a cell is a three-respondent mean.

    Red PDF text is the direct signal.  Two derived signals are also treated as
    the same confidentiality basis: a published respondent count of exactly
    three, and a table/column combination whose source components are already
    red-flagged even though the synthesis table prints the cell in black.
    """
    if (row_index, column.index) in red_cells:
        return True
    if sample_n == 3:
        return True
    return (column.species_group, column.product_or_grade) in spec.three_respondents_columns


def _three_respondent_note(spec: TableSpec, column: ColumnSpec) -> str:
    if (
        (column.species_group, column.product_or_grade)
        in spec.three_respondents_columns
        and spec.three_respondents_note
    ):
        return spec.three_respondents_note
    return "Mean for three respondents; interpret with caution."


def rows_from_extracted_table(
    raw_rows: list[list[str | None]],
    spec: TableSpec,
    *,
    red_cells: set[tuple[int, int]] | None = None,
    region_by_column: dict[int, str] | None = None,
    sample_n_by_region: dict[str, int] | None = None,
) -> tuple[list[EmittedRow], TableCoverage]:
    """Parse already-extracted table rows into normalized observations.

    This is the pure boundary between PDF layout and trusted dataset rows.  It
    is separated from ``extract_table`` so the table semantics can be tested
    with a small fixture without a PDF dependency.
    """
    red_cells = red_cells or set()
    coverage = TableCoverage(
        source_id=spec.source_id,
        source_table=spec.key,
        source_page=spec.page,
        title_fr=spec.title_fr,
        parsed_status="parsed",
        notes=spec.notes,
    )
    emitted: list[EmittedRow] = []
    current_section = ""
    row_map = {
        canonical_row_label(key, keep_footnote=spec.keep_footnote_suffix): value
        for key, value in spec.row_map.items()
    }
    section_labels = {
        canonical_row_label(key): value for key, value in spec.section_labels.items()
    }
    section_value_rows = {
        canonical_row_label(key): value
        for key, value in spec.section_value_rows.items()
    }
    for row_index, raw_row in enumerate(raw_rows):
        label_fr = "" if not raw_row else (raw_row[0] or "")
        label_key = canonical_row_label(
            label_fr, keep_footnote=spec.keep_footnote_suffix
        )
        if not label_key:
            continue
        if label_key == "nombre de repondants":
            continue
        if label_key in section_labels:
            current_section = section_labels[label_key]
            continue
        row_spec = row_map.get(label_key)
        if row_spec is None and label_key in section_value_rows:
            row_spec = section_value_rows[label_key]
            if not current_section:
                raise ValueError(f"{spec.key}: section row {label_key!r} has no section")
            row_spec = RowSpec(
                component_group=row_spec.component_group,
                component=row_spec.component,
                unit=row_spec.unit,
                price_basis=row_spec.price_basis,
                sign_convention=row_spec.sign_convention,
                subcomponent=current_section,
                observation_kind=row_spec.observation_kind,
                notes=row_spec.notes,
            )
        if row_spec is None:
            numeric_cells = [
                (col.index, raw_row[col.index])
                for col in spec.columns
                if col.index < len(raw_row) and _is_numeric_cell(raw_row[col.index])
            ]
            if numeric_cells:
                raise ValueError(
                    f"{spec.key}: unmapped numeric row {label_fr!r} on page {spec.page}"
                )
            continue
        for column in spec.columns:
            if column.index >= len(raw_row):
                continue
            raw_value = raw_row[column.index]
            context = _context(spec, label_fr, column)
            value, unit, flag, cell_note = parse_cell_value(
                raw_value,
                default_unit=column.unit or row_spec.unit or spec.default_unit,
                dash_meaning=spec.dash_meaning,
                context=context,
            )
            if value is None:
                if flag in {"not_available", "not_applicable"}:
                    coverage.values_missing += 1
                else:
                    coverage.values_suppressed += 1
                continue
            region_code, aggregation, sample_n = _region_context(
                column, region_by_column, sample_n_by_region
            )
            if _three_respondent_basis(spec, column, row_index, red_cells, sample_n):
                flag = "three_respondents"
                coverage.values_flagged_three_respondents += 1
                cell_note = _combine_notes(
                    cell_note, _three_respondent_note(spec, column)
                )
            emitted.append(
                EmittedRow(
                    source_id=spec.source_id,
                    source_table=spec.key,
                    source_page=spec.page,
                    survey_year=SURVEY_YEAR,
                    reference_period=REFERENCE_PERIOD,
                    region_code=region_code,
                    region_label=REGION_LABELS[region_code],
                    aggregation_level=aggregation,
                    species_group=column.species_group,
                    species_label=SPECIES_LABELS[column.species_group],
                    product_or_grade=column.product_or_grade,
                    component_group=row_spec.component_group,
                    component=row_spec.component,
                    subcomponent=row_spec.subcomponent or column.period,
                    source_label_fr=re.sub(r"\s+", " ", label_fr).strip(),
                    value=value,
                    unit=unit,
                    price_basis=row_spec.price_basis,
                    sample_n=sample_n,
                    observation_type=OBSERVATION_TYPE,
                    observation_kind=row_spec.observation_kind,
                    confidentiality_flag=flag,
                    sign_convention=row_spec.sign_convention,
                    notes=_combine_notes(row_spec.notes, cell_note),
                )
            )
            coverage.rows_emitted += 1
    return emitted, coverage


def extract_table(
    page: Any,
    spec: TableSpec,
    *,
    region_by_column: dict[int, str] | None = None,
    sample_n_by_region: dict[str, int] | None = None,
) -> tuple[list[EmittedRow], TableCoverage]:
    """Extract one table from a PDF page.  Unknown numeric rows fail hard."""
    tables = page.find_tables()
    if spec.table_index >= len(tables):
        raise ValueError(
            f"{spec.key}: page {spec.page} has {len(tables)} tables, "
            f"wanted index {spec.table_index}"
        )
    table = tables[spec.table_index]
    raw_rows = table.extract()
    red_spans = _red_char_spans(page)
    red_cells = {
        (row_index, column.index)
        for row_index, row in enumerate(table.rows)
        for column in spec.columns
        if column.index < len(row.cells)
        and _cell_has_red(row.cells[column.index], red_spans)
    }
    return rows_from_extracted_table(
        raw_rows,
        spec,
        red_cells=red_cells,
        region_by_column=region_by_column,
        sample_n_by_region=sample_n_by_region,
    )


def extract_a2(
    page: Any,
    spec: TableSpec,
) -> tuple[list[EmittedRow], TableCoverage]:
    """Extract the comparative 2014/2019/2022 table with per-column years."""
    tables = page.find_tables()
    table = tables[spec.table_index]
    raw_rows = table.extract()
    red_spans = _red_char_spans(page)
    red_cells = {
        (row_index, column.index)
        for row_index, row in enumerate(table.rows)
        for column in spec.columns
        if column.index < len(row.cells)
        and _cell_has_red(row.cells[column.index], red_spans)
    }
    coverage = TableCoverage(
        source_id=spec.source_id,
        source_table=spec.key,
        source_page=spec.page,
        title_fr=spec.title_fr,
        parsed_status="parsed",
        notes=(
            "Historical comparison table. 2014 and 2019 are earlier survey "
            "results reproduced by the 2022 report; 2022 cells duplicate the "
            "provincial tables and are used as a cross-check."
        ),
    )
    emitted: list[EmittedRow] = []
    row_map = {canonical_row_label(key): value for key, value in spec.row_map.items()}
    for row_index, raw_row in enumerate(raw_rows):
        label_fr = "" if not raw_row else (raw_row[0] or "")
        label_key = canonical_row_label(label_fr)
        if not label_key:
            continue
        if label_key == "nombre de repondants":
            continue
        row_spec = row_map.get(label_key)
        if row_spec is None:
            numeric_cells = [
                raw_row[col.index]
                for col in spec.columns
                if col.index < len(raw_row)
                and _is_numeric_cell(raw_row[col.index])
            ]
            if numeric_cells:
                raise ValueError(f"A2: unmapped numeric row {label_fr!r}")
            continue
        for column, year in zip(spec.columns, A2_YEARS, strict=True):
            if column.index >= len(raw_row):
                continue
            raw_value = raw_row[column.index]
            value, unit, flag, cell_note = parse_cell_value(
                raw_value,
                default_unit=row_spec.unit,
                dash_meaning="not_available",
                context=_context(spec, label_fr, column),
            )
            if value is None:
                coverage.values_missing += 1
                continue
            if (row_index, column.index) in red_cells:
                flag = "three_respondents"
                coverage.values_flagged_three_respondents += 1
                cell_note = _combine_notes(
                    cell_note, "Mean for three respondents; interpret with caution."
                )
            emitted.append(
                EmittedRow(
                    source_id=spec.source_id,
                    source_table=spec.key,
                    source_page=spec.page,
                    survey_year=year,
                    reference_period=(
                        f"{year} survey (comparison reproduced in the 2022 report)"
                    ),
                    region_code="QC",
                    region_label=REGION_LABELS["QC"],
                    aggregation_level="provincial",
                    species_group=column.species_group,
                    species_label=SPECIES_LABELS[column.species_group],
                    product_or_grade=column.product_or_grade,
                    component_group=row_spec.component_group,
                    component=row_spec.component,
                    subcomponent="",
                    source_label_fr=re.sub(r"\s+", " ", label_fr).strip(),
                    value=value,
                    unit=unit,
                    price_basis=row_spec.price_basis,
                    sample_n=A2_SAMPLE_N.get((column.species_group, year)),
                    observation_type=OBSERVATION_TYPE,
                    observation_kind=row_spec.observation_kind,
                    confidentiality_flag=flag,
                    sign_convention=row_spec.sign_convention,
                    notes=_combine_notes(row_spec.notes, cell_note),
                )
            )
            coverage.rows_emitted += 1
    return emitted, coverage


def _regional_sample_counts(page: Any, spec: TableSpec) -> dict[str, int]:
    table = page.find_tables()[spec.table_index]
    for raw_row in table.extract():
        if not raw_row or not raw_row[0]:
            continue
        if canonical_row_label(raw_row[0]) != "nombre de repondants":
            continue
        counts: dict[str, int] = {}
        for column, code in zip(spec.columns, REGIONAL_CODES, strict=True):
            value = parse_french_number(raw_row[column.index])
            if value is not None:
                counts[code] = int(value)
        return counts
    raise ValueError("Regional table has no respondent-count row")


def extract_regional(
    page: Any,
    spec: TableSpec,
) -> tuple[list[EmittedRow], TableCoverage]:
    sample_counts = _regional_sample_counts(page, spec)
    emitted, coverage = extract_table(
        page,
        spec,
        region_by_column={
            column.index: code
            for column, code in zip(spec.columns, REGIONAL_CODES, strict=True)
        },
        sample_n_by_region=sample_counts,
    )
    coverage.notes = (
        "Seven regional groups plus the provincial total. Aggregated SEPM only; "
        "hardwoods and poplars are not regionalised in the 2022 compilation."
    )
    return emitted, coverage


def _check_close(
    left: float,
    right: float,
    *,
    label: str,
    tolerance: float = 0.02,
) -> dict[str, Any]:
    difference = round(left - right, 6)
    ok = abs(difference) <= tolerance
    return {
        "check": label,
        "computed": round(left, 4),
        "reported": round(right, 4),
        "difference": difference,
        "status": "pass" if ok else "fail",
    }


KNOWN_RECONCILIATION_ISSUES = {
    "T14/QC/peupliers/2022/sciage overhead components -> total": (
        "Table 14 body reports peupliers-sciage components 3.25, 12.28, 0.00, "
        "0.00, -4.87 (sum 10.66) but a total of 13.87. Annex A1 reports 2.06, "
        "16.51, 0.05, 0.12, -4.87 and total 13.87. The body row appears to "
        "repeat pins-column values. Values are kept as printed, not adjusted."
    ),
}


def reconcile_rows(rows: Iterable[EmittedRow]) -> list[dict[str, Any]]:
    """Check additive identities that the source tables actually support."""
    checks: list[dict[str, Any]] = []
    by_key: dict[tuple[str, str, str, int, str], dict[str, float]] = {}
    for row in rows:
        key = (
            row.source_table,
            row.region_code,
            row.species_group,
            row.survey_year,
            row.product_or_grade,
        )
        by_key.setdefault(key, {})[row.component] = row.value

    for key, values in by_key.items():
        table, region, species, year, product = key
        label = f"{table}/{region}/{species}/{year}/{product}"
        if table in {"T3"} and "harvest_total" in values:
            components = [
                "harvest",
                "equipment_transport",
                "bucking_in_forest",
                "site_cleanup",
                "other_harvest_costs",
            ]
            if all(name in values for name in components):
                checks.append(
                    _check_close(
                        sum(values[name] for name in components),
                        values["harvest_total"],
                        label=f"{label} harvest components -> harvest_total",
                    )
                )
        if table in {"T5"} and "road_total_after_prccm" in values:
            components = [
                "road_construction",
                "prccm_roads",
                "road_amortization",
                "road_upgrade",
                "prccm_upgrade",
                "bridges",
                "prccm_bridges",
                "bridge_amortization",
                "snow_removal_maintenance",
                "third_party_contribution",
                "road_revenue",
                "equipment_transport",
            ]
            if all(name in values for name in components):
                checks.append(
                    _check_close(
                        sum(values[name] for name in components),
                        values["road_total_after_prccm"],
                        label=f"{label} road components -> after-PRCCM total",
                    )
                )
                rebates = [
                    abs(values[name])
                    for name in ("prccm_roads", "prccm_upgrade", "prccm_bridges")
                ]
                checks.append(
                    _check_close(
                        values["road_total_after_prccm"] + sum(rebates),
                        values["road_total_before_prccm"],
                        label=f"{label} after-PRCCM + PRCCM rebates -> before-PRCCM total",
                    )
                )
        if table in {"T6"} and "haul_total" in values:
            components = ["loading", "unloading", "road_transport"]
            if all(name in values for name in components):
                checks.append(
                    _check_close(
                        sum(values[name] for name in components),
                        values["haul_total"],
                        label=f"{label} haul components -> haul_total",
                    )
                )
        if table in {"T9"} and "other_costs_total" in values:
            components = [
                "official_scaling",
                "operations_administration",
                "communications",
                "planning",
                "health_safety",
                "harmonization",
                "certification",
                "environmental_management",
                "training",
                "forest_operations_amortization",
                "other",
                "other_revenue",
            ]
            if all(name in values for name in components):
                checks.append(
                    _check_close(
                        sum(values[name] for name in components),
                        values["other_costs_total"],
                        label=f"{label} other-cost components -> total",
                    )
                )
        if table in {"T10"} and "transfers_total" in values:
            components = ["annual_rent", "vmbsp", "compensations"]
            if all(name in values for name in components):
                checks.append(
                    _check_close(
                        sum(values[name] for name in components),
                        values["transfers_total"],
                        label=f"{label} rent+vmbsp+compensations -> transfers_total",
                    )
                )
        if table in {"T11"} and "transformation_total" in values:
            components = [
                "handling",
                "scaling",
                "bucking_mill",
                "sawing",
                "planing_mill",
                "planing_external",
                "drying_total",
                "finished_goods_loading",
                "inter_mill_transport",
                "mill_amortization",
                "other",
            ]
            if all(name in values for name in components):
                checks.append(
                    _check_close(
                        sum(values[name] for name in components),
                        values["transformation_total"],
                        label=f"{label} transformation components -> total",
                    )
                )
        if table in {"T14"} and "overhead_total" in values:
            components = [
                "sales",
                "general_administration",
                "research_development",
                "other",
                "financial_compensations",
            ]
            if all(name in values for name in components):
                checks.append(
                    _check_close(
                        sum(values[name] for name in components),
                        values["overhead_total"],
                        label=f"{label} overhead components -> total",
                    )
                )
        if table in {"T19"} and "byproduct_total" in values:
            components = ["chips", "sawdust", "shavings", "bark", "wood_residues"]
            missing = [name for name in components if name not in values]
            if missing:
                checks.append(
                    {
                        "check": f"{label} byproduct components -> total",
                        "computed": None,
                        "reported": round(values["byproduct_total"], 4),
                        "difference": None,
                        "status": "skipped_suppressed_component",
                        "note": (
                            "Source suppresses "
                            + ", ".join(missing)
                            + "; the printed total is retained without "
                            "reconciliation over a partial component set."
                        ),
                    }
                )
            else:
                checks.append(
                    _check_close(
                        sum(values[name] for name in components),
                        values["byproduct_total"],
                        label=f"{label} byproduct components -> total",
                    )
                )
        if table in {"T20", "REG"} and "procurement_total_before_transfers" in values:
            components = ["harvest", "road", "haul", "hosting", "other_costs"]
            if all(name in values for name in components):
                checks.append(
                    _check_close(
                        sum(values[name] for name in components),
                        values["procurement_total_before_transfers"],
                        label=f"{label} procurement components -> before-transfer total",
                    )
                )
        if table in {"T20", "REG"} and "procurement_total_after_transfers" in values:
            parts = ["procurement_total_before_transfers", "transfers_total"]
            if all(name in values for name in parts):
                checks.append(
                    _check_close(
                        sum(values[name] for name in parts),
                        values["procurement_total_after_transfers"],
                        label=f"{label} before-transfer + transfers -> total",
                    )
                )
        if table in {"T22"} and "total_revenue" in values:
            parts = ["net_revenue", "byproduct_revenue", "species_gain_loss"]
            if all(name in values for name in parts):
                checks.append(
                    _check_close(
                        sum(values[name] for name in parts),
                        values["total_revenue"],
                        label=f"{label} revenue components -> total",
                    )
                )
    for check in checks:
        if check["status"] in {"pass", "skipped_suppressed_component"}:
            continue
        note = KNOWN_RECONCILIATION_ISSUES.get(check["check"])
        if note is None:
            raise ValueError(
                f"reconciliation failed for {check['check']}: "
                f"{check['computed']} vs {check['reported']}"
            )
        check["status"] = "known_source_inconsistency"
        check["note"] = note
    return checks


def validate_rows(rows: list[EmittedRow]) -> dict[str, Any]:
    if not rows:
        raise ValueError("No observations extracted")
    bad_values = 0
    bad_negatives: list[str] = []
    bad_units: list[str] = []
    bad_signs: list[str] = []
    allowed_units = {
        CAD_M3,
        CAD_HA,
        CAD_MPMP,
        CAD_TMA,
        CAD_TMV,
        M3_HA,
        M3_MPMP,
        PERCENT,
        KM,
    }
    for row in rows:
        if not math.isfinite(row.value):
            bad_values += 1
        if row.unit not in allowed_units:
            bad_units.append(f"{row.source_table}:{row.component}:{row.unit}")
        if row.sign_convention not in SIGN_CONVENTIONS:
            bad_signs.append(
                f"{row.source_table}:{row.component}:{row.sign_convention}"
            )
        is_physical = row.unit in PHYSICAL_UNITS
        is_revenue = (
            row.unit not in PHYSICAL_UNITS
            and row.component_group in REVENUE_COMPONENT_GROUPS
            and row.component != "species_gain_loss"
        )
        if is_physical and row.sign_convention != PHYSICAL_QUANTITY:
            bad_signs.append(
                f"{row.source_table}:{row.component}:{row.unit}:"
                f"{row.sign_convention}:expected_{PHYSICAL_QUANTITY}"
            )
        if not is_physical and row.sign_convention == PHYSICAL_QUANTITY:
            bad_signs.append(
                f"{row.source_table}:{row.component}:{row.unit}:"
                f"{row.sign_convention}:non_physical_unit"
            )
        if is_revenue and row.sign_convention != POSITIVE_REVENUE:
            bad_signs.append(
                f"{row.source_table}:{row.component}:"
                f"{row.sign_convention}:expected_{POSITIVE_REVENUE}"
            )
        if not is_revenue and row.sign_convention == POSITIVE_REVENUE:
            bad_signs.append(
                f"{row.source_table}:{row.component}:"
                f"{row.sign_convention}:non_revenue_component"
            )
        if row.component == "species_gain_loss" and row.sign_convention != (
            "signed_gain_loss"
        ):
            bad_signs.append(
                f"{row.source_table}:{row.component}:"
                f"{row.sign_convention}:expected_signed_gain_loss"
            )
        if row.value < 0 and row.sign_convention not in NEGATIVE_SIGN_CONVENTIONS:
            bad_negatives.append(
                f"{row.source_table}:{row.component}:{row.value}"
            )
    if bad_values:
        raise ValueError(f"{bad_values} non-finite values")
    if bad_units:
        raise ValueError(f"unexpected units: {sorted(set(bad_units))}")
    if bad_signs:
        raise ValueError(f"unexpected sign conventions: {bad_signs[:10]}")
    if bad_negatives:
        raise ValueError(f"unexpected negative values: {bad_negatives[:10]}")
    return {
        "row_count": len(rows),
        "non_finite_values": 0,
        "unexpected_negative_values": 0,
        "unexpected_units": 0,
        "unexpected_sign_conventions": 0,
        "negative_value_count": sum(1 for row in rows if row.value < 0),
        "three_respondent_value_count": sum(
            1 for row in rows if row.confidentiality_flag == "three_respondents"
        ),
        "sign_convention_counts": {
            convention: sum(
                1 for row in rows if row.sign_convention == convention
            )
            for convention in sorted(SIGN_CONVENTIONS)
        },
    }


def spot_checks(rows: list[EmittedRow]) -> list[dict[str, Any]]:
    """Hard-coded checks against values read directly from the PDF pages."""
    index = {
        (
            row.source_table,
            row.region_code,
            row.species_group,
            row.survey_year,
            row.product_or_grade,
            row.component,
            row.subcomponent,
            row.unit,
        ): row.value
        for row in rows
    }

    def expect(
        key: tuple[str, str, str, int, str, str, str, str], value: float
    ) -> dict[str, Any]:
        actual = index.get(key)
        if actual is None:
            raise ValueError(f"spot-check target missing: {key}")
        if abs(actual - value) > 0.005:
            raise ValueError(f"spot-check mismatch for {key}: {actual} != {value}")
        return {
            "source_table": key[0],
            "region_code": key[1],
            "species_group": key[2],
            "survey_year": key[3],
            "product_or_grade": key[4],
            "component": key[5],
            "subcomponent": key[6],
            "unit": key[7],
            "pdf_value": value,
            "parsed_value": actual,
            "status": "pass",
        }

    return [
        expect(
            (
                "REG",
                "R09",
                "sepm",
                2022,
                "total",
                "procurement_total_after_transfers",
                "",
                CAD_M3,
            ),
            118.10,
        ),
        expect(
            (
                "REG",
                "QC",
                "sepm",
                2022,
                "total",
                "procurement_total_before_transfers",
                "",
                CAD_M3,
            ),
            81.40,
        ),
        expect(("T3", "QC", "sepm", 2022, "total", "harvest_total", "", CAD_M3), 31.42),
        expect(
            (
                "T5",
                "QC",
                "sepm",
                2022,
                "total",
                "road_total_after_prccm",
                "",
                CAD_M3,
            ),
            15.52,
        ),
        expect(
            ("T16", "QC", "sepm", 2022, "lumber", "net_revenue", "", CAD_M3), 183.74
        ),
        expect(
            (
                "T19",
                "QC",
                "sepm",
                2022,
                "byproduct",
                "byproduct_total",
                "",
                CAD_M3,
            ),
            20.23,
        ),
        expect(
            ("T22", "QC", "sepm", 2022, "total", "total_revenue", "", CAD_M3), 201.22
        ),
        expect(("A2", "QC", "sepm", 2022, "total", "harvest", "", CAD_M3), 31.42),
    ]


def source_record(source_id: str) -> dict[str, Any]:
    source = dict(SOURCES[source_id])
    source["source_id"] = source_id
    return source


def coverage_for_unparsed_sources() -> list[TableCoverage]:
    return [
        TableCoverage(
            source_id="main_2022",
            source_table="T1",
            source_page=7,
            title_fr="Profil des repondants et volumes de GA",
            parsed_status="not_parsed_context_only",
            notes=(
                "Sampling profile, not a cost/revenue table. Questionnaire "
                "counts (SEPM 37, feuillus durs 18, peupliers 12, thuyas 4, "
                "pins 3, total 70) are recorded in the manifest."
            ),
        ),
        TableCoverage(
            source_id="main_2022",
            source_table="A1",
            source_page=32,
            title_fr="Tableau-synthese (Annexe 1)",
            parsed_status="not_parsed_duplicate",
            notes=(
                "Annexe 1 repeats Tables 2-22 as a single synthesis table. It "
                "was used as an independent cross-check for the Table 14 "
                "peupliers-sciage discrepancy, but is not emitted separately to "
                "avoid duplicate observations."
            ),
        ),
        TableCoverage(
            source_id="main_2022",
            source_table="A3",
            source_page=39,
            title_fr="Questionnaire SEPM (Annexe 3)",
            parsed_status="not_parsed_instrument",
            notes=(
                "Survey questionnaire. It defines the requested cost lines but "
                "contains no observed values."
            ),
        ),
        TableCoverage(
            source_id="main_2022",
            source_table="FIG3",
            source_page=26,
            title_fr="Evolution du prix du bois d'oeuvre selon les trimestres",
            parsed_status="not_parsed_duplicate",
            notes=(
                "Figure 3 plots the same quarterly SEPM gross revenue values "
                "already parsed from Table 15; no additional observations."
            ),
        ),
        TableCoverage(
            source_id="regional_2019",
            source_table="all",
            source_page=1,
            title_fr=SOURCES["regional_2019"]["title"],
            parsed_status="downloaded_not_parsed",
            notes=(
                "Tables 1-4 on pages 1-3 are raster images with no text layer; "
                "pdftotext and pdfplumber return only captions. Not OCRed. "
                "2019 provincial values are available only through Table A2 of "
                "the 2022 report."
            ),
        ),
        TableCoverage(
            source_id="survey_2019",
            source_table="all",
            source_page=1,
            title_fr=SOURCES["survey_2019"]["title"],
            parsed_status="downloaded_not_parsed",
            notes=(
                "Kept as provenance and context. Full 2019 extraction is out of "
                "scope for this 2022 dataset; the 2022 report's Table A2 supplies "
                "2019 comparison values."
            ),
        ),
        TableCoverage(
            source_id="sylviculture_public_2021_2022",
            source_table="all",
            source_page=1,
            title_fr=SOURCES["sylviculture_public_2021_2022"]["title"],
            parsed_status="downloaded_not_parsed",
            notes=(
                "Silviculture cost survey, not part of the 2022 procurement/"
                "transformation/revenue survey. Its per-hectare and per-1000-"
                "plant treatment costs are a separate dataset; do not merge into "
                "$/m3 harvest/road/haul components."
            ),
        ),
        TableCoverage(
            source_id="sylviculture_private_2021_2022",
            source_table="all",
            source_page=1,
            title_fr=SOURCES["sylviculture_private_2021_2022"]["title"],
            parsed_status="downloaded_not_parsed",
            notes=(
                "Silviculture cost survey, not part of the 2022 procurement/"
                "transformation/revenue survey. Separate treatment-cost dataset; "
                "do not merge into $/m3 harvest/road/haul components."
            ),
        ),
    ]


def _pdf_page(spec: TableSpec) -> int:
    """Return the 1-based PDF page for a table.

    Body pages of the 2022 report are offset by six from their printed page
    numbers (printed p. 12 is PDF p. 18). Annex tables and the one-page
    regional compilation carry an explicit ``pdf_page``.
    """
    if spec.pdf_page:
        return spec.pdf_page
    if spec.source_id == "main_2022":
        return spec.page + 6
    return spec.page


def build_dataset(raw_dir: Path) -> tuple[list[EmittedRow], list[TableCoverage], dict[str, Any]]:
    try:
        import pdfplumber
    except ImportError as exc:  # pragma: no cover - environment failure
        raise SystemExit(
            "pdfplumber is required: .venv/bin/python -m pip install pdfplumber"
        ) from exc

    main_path = raw_dir / SOURCES["main_2022"]["filename"]
    regional_path = raw_dir / SOURCES["regional_2022"]["filename"]
    for path, source in (
        (main_path, SOURCES["main_2022"]),
        (regional_path, SOURCES["regional_2022"]),
    ):
        if not path.exists():
            raise FileNotFoundError(f"missing source PDF: {path}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != source["sha256"]:
            raise ValueError(f"sha256 mismatch for {path}: {digest}")

    rows: list[EmittedRow] = []
    coverage: list[TableCoverage] = []
    with pdfplumber.open(main_path) as pdf:
        for spec in TABLE_SPECS:
            page = pdf.pages[_pdf_page(spec) - 1]
            table_rows, table_coverage = extract_table(page, spec)
            rows.extend(table_rows)
            coverage.append(table_coverage)
        a2_rows, a2_coverage = extract_a2(
            pdf.pages[_pdf_page(A2_TABLE) - 1], A2_TABLE
        )
        rows.extend(a2_rows)
        coverage.append(a2_coverage)
    with pdfplumber.open(regional_path) as pdf:
        regional_rows, regional_coverage = extract_regional(
            pdf.pages[_pdf_page(REGIONAL_TABLE) - 1], REGIONAL_TABLE
        )
        rows.extend(regional_rows)
        coverage.append(regional_coverage)
    coverage.extend(coverage_for_unparsed_sources())

    validation = validate_rows(rows)
    validation["reconciliations"] = reconcile_rows(rows)
    validation["reconciliation_count"] = len(validation["reconciliations"])
    validation["reconciliation_exceptions"] = [
        check
        for check in validation["reconciliations"]
        if check["status"] == "known_source_inconsistency"
    ]
    validation["reconciliation_failures"] = [
        check
        for check in validation["reconciliations"]
        if check["status"] == "fail"
    ]
    validation["reconciliation_skipped_suppressed"] = [
        check
        for check in validation["reconciliations"]
        if check["status"] == "skipped_suppressed_component"
    ]
    checks = spot_checks(rows)
    validation["spot_checks"] = checks
    validation["spot_check_count"] = len(checks)
    validation["status"] = (
        "pass_with_documented_source_inconsistency"
        if validation["reconciliation_exceptions"]
        else "pass"
    )
    return rows, coverage, validation


def write_csv(path: Path, rows: Iterable[dict[str, str]], columns: tuple[str, ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(columns), lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in columns})


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pdf_pages(path: Path) -> int:
    result = subprocess.run(
        ["pdfinfo", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    for line in result.stdout.splitlines():
        if line.startswith("Pages:"):
            return int(line.split(":", 1)[1].strip())
    raise ValueError(f"pdfinfo gave no page count for {path}")


def write_manifest(
    out_dir: Path,
    rows: list[EmittedRow],
    coverage: list[TableCoverage],
    validation: dict[str, Any],
    raw_dir: Path,
) -> dict[str, Any]:
    generated = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    files = []
    for name in (
        "qc_cost_revenue_survey.csv",
        "qc_cost_revenue_coverage.csv",
        "README.md",
        "PROVENANCE.md",
    ):
        path = out_dir / name
        if path.exists():
            files.append(
                {
                    "path": name,
                    "bytes": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
            )
    sources = []
    for source_id in SOURCES:
        record = source_record(source_id)
        path = raw_dir / record["filename"]
        try:
            record["local_path"] = str(path.resolve().relative_to(REPO_ROOT))
        except ValueError:
            record["local_path"] = str(path.resolve())
        record["observed_bytes"] = path.stat().st_size if path.exists() else None
        record["observed_pages"] = pdf_pages(path) if path.exists() else None
        record["used_in_dataset"] = source_id in {"main_2022", "regional_2022"}
        sources.append(record)
    manifest = {
        "artifact_version": "0.1.0",
        "dataset": "qc_cost_survey_2022",
        "generated_utc": generated,
        "observation_type": OBSERVATION_TYPE,
        "geography": "Quebec",
        "survey_year": SURVEY_YEAR,
        "reference_period": REFERENCE_PERIOD,
        "price_year": "survey_year",
        "price_year_detail": (
            "Nominal CAD of the survey year: 2022 for the main and regional "
            "tables; 2014, 2019, or 2022 for Table A2 comparison rows."
        ),
        "currency": "CAD",
        "currency_basis": "CAD_nominal",
        "parser": {
            "script": "scripts/national/extract_qc_cost_survey_2022.py",
            "engine": "pdfplumber table extraction plus pdftotext layout inspection",
            "command": (
                ".venv/bin/python scripts/national/extract_qc_cost_survey_2022.py "
                "--raw-dir tmp/qc_cost_survey/pdf"
            ),
        },
        "sources": sources,
        "files": files,
        "coverage": {
            "tables_parsed": [
                item.as_csv() for item in coverage if item.parsed_status == "parsed"
            ],
            "tables_not_parsed": [
                item.as_csv() for item in coverage if item.parsed_status != "parsed"
            ],
            "rows_emitted": len(rows),
            "source_tables_parsed": sum(
                1 for item in coverage if item.parsed_status == "parsed"
            ),
            "source_tables_not_parsed": sum(
                1 for item in coverage if item.parsed_status != "parsed"
            ),
            "regional_breakdown": "SEPM only, seven regional groups plus provincial total",
            "species_breakdown": (
                "Provincial by SEPM, feuillus durs (sciage/trituration), "
                "peupliers (sciage/trituration), pins, thuyas where reported."
            ),
            "mill_level_data": False,
            "grade_level_data": False,
            "questionnaire_counts": {
                "sepm": 37,
                "peupliers": 12,
                "feuillus_durs": 18,
                "thuyas": 4,
                "pins": 3,
                "total": 70,
            },
        },
        "validation": validation,
        "limitations": [
            "Official survey aggregates, not mill-level observations.",
            "No species- or grade-level value was allocated from a broader aggregate.",
            "Drying costs by species in Table 11 are non-additive species breakdown values and are labelled observation_kind=species_breakdown_non_additive.",
            "Regional values cover SEPM only; hardwoods and poplars are not regionalised in the 2022 compilation.",
            "Values flagged three_respondents are confidential-survey means for exactly three respondents and are not statistically robust.",
            "Dashes mean suppressed because fewer than three respondents, unless the table semantics say not applicable; missing-value counts are in the coverage CSV.",
            "Table A2's 2014 and 2019 values are earlier survey results reproduced for comparison, not re-estimated 2022 values.",
            "The 2019 regional compilation PDF has no text layer in its tables; it was not OCRed and contributes no rows.",
            "The 2021-22 silviculture surveys are downloaded for provenance only; they are a separate treatment-cost dataset and are not merged here.",
            "Table 10 compensation sub-lines are not a complete decomposition of the compensations total; do not replace the total with their sum.",
            "Forest-road component rows include PRCCM rebates. The after-PRCCM total equals their sum; the before-PRCCM total adds the rebates back.",
            "PRCCM rebates sit inside T10/T20 transfers_total; do not add T5 road_total_after_prccm to T10 transfers_total (double count). T20/REG road is gross.",
            "T2/T21 Achats de bois (livre) is purchased-wood procurement cost, not observed log-sale revenue; T2 minus T20 is a procurement-channel cost spread, not forest-sector net value.",
            "T20 excludes species gain/loss (SEPM -2.75 CAD/m3, hardwood sawlog -1.85, poplar trituration -6.80); compare it with T22 only when this exclusion is flagged.",
            "Table 19 by-product totals are not reconciled over a partial component set because the source suppresses at least one component; the skipped checks are recorded in validation.reconciliation_skipped_suppressed.",
            "Three-respondent flags include red PDF cells, all regional R09 rows (sample_n=3), and T20 peupliers-trituration rows whose basis is red-flagged in T3/T5/T10/T21 even though T20 prints them in black.",
            "Annex 1 repeats Tables 2-22 and is not emitted separately; it is used only as a cross-check.",
        ],
        "merge_policy": {
            "merged_into_forest_cost_revenue": False,
            "status": "standalone",
            "reason": (
                "Observed survey aggregates with explicit regional and species "
                "aggregation. The ledger is not modified by this extraction."
            ),
            "recommended_use": (
                "Benchmark or calibration evidence for Quebec procurement, "
                "transformation, delivered revenue, and by-product values; not a "
                "direct row-level model input."
            ),
        },
        "accounting_caveats": [
            {
                "id": "t2_t21_purchased_wood_procurement_cost",
                "tables": ["T2", "T21"],
                "statement": (
                    "T2/T21 Achats de bois (livre) is the buyer-side purchased-wood "
                    "procurement cost. It includes the wood purchase, transfers to "
                    "the State, transport/unloading, and road costs where applicable. "
                    "The 107.22 CAD/m3 SEPM value is therefore not observed log-sale "
                    "revenue and must not be treated as stumpage or forest-sector "
                    "revenue."
                ),
            },
            {
                "id": "t20_bgad_is_a_parallel_supply_channel",
                "tables": ["T20", "T21"],
                "statement": (
                    "T20's 106.41 CAD/m3 SEPM value is the parallel BGAD supply "
                    "channel (harvest, road, haul, hosting, other costs, plus "
                    "transfers). The 107.22 - 106.41 = 0.81 CAD/m3 difference is a "
                    "procurement-channel cost spread between different respondent "
                    "samples, not forest-sector net value. The same caution applies "
                    "to hardwood sawlog (-14.53) and poplar trituration (-9.45)."
                ),
            },
            {
                "id": "prccm_rebates_are_inside_transfers_total",
                "tables": ["T5", "T10", "T20", "REG"],
                "statement": (
                    "PRCCM road rebates are included in T10/T20 transfers_total "
                    "(SEPM -1.06 = -0.69 - 0.33 - 0.04), not in T20 road. T5 "
                    "road_total_after_prccm (15.52 SEPM) is already net of PRCCM and "
                    "must not be combined with T10 transfers_total (25.01), which "
                    "would double count the rebate. T20/REG road is gross "
                    "(access_cost_gross, 16.58 SEPM)."
                ),
            },
            {
                "id": "t20_excludes_species_gain_loss",
                "tables": ["T3", "T20", "T22"],
                "statement": (
                    "T20 excludes species gain/loss by design: SEPM -2.75, hardwood "
                    "sawlog -1.85, and poplar trituration -6.80 CAD/m3. Those values "
                    "appear in T3 and in the T22 revenue synthesis. Any comparison of "
                    "delivered wood value with T20 procurement costs must flag this "
                    "exclusion; the SEPM item is larger than the entire 0.81 CAD/m3 "
                    "purchased-vs-BGAD spread."
                ),
            },
        ],
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return manifest


def write_outputs(
    out_dir: Path,
    rows: list[EmittedRow],
    coverage: list[TableCoverage],
    validation: dict[str, Any],
    raw_dir: Path,
) -> dict[str, Any]:
    write_csv(
        out_dir / "qc_cost_revenue_survey.csv",
        (row.as_csv() for row in rows),
        CSV_COLUMNS,
    )
    write_csv(
        out_dir / "qc_cost_revenue_coverage.csv",
        (item.as_csv() for item in coverage),
        COVERAGE_COLUMNS,
    )
    return write_manifest(out_dir, rows, coverage, validation, raw_dir)


def download_sources(raw_dir: Path) -> None:
    raw_dir.mkdir(parents=True, exist_ok=True)
    for source in SOURCES.values():
        path = raw_dir / source["filename"]
        if path.exists() and sha256_file(path) == source["sha256"]:
            continue
        print(f"downloading {source['url']}", file=sys.stderr)
        subprocess.run(
            ["curl", "-sSL", "--fail", "--max-time", "120", source["url"], "-o", str(path)],
            check=True,
        )
        digest = sha256_file(path)
        if digest != source["sha256"]:
            raise ValueError(f"downloaded hash mismatch for {path}: {digest}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW)
    parser.add_argument(
        "--download",
        action="store_true",
        help="Download missing source PDFs before extraction.",
    )
    args = parser.parse_args(argv)
    if args.download:
        download_sources(args.raw_dir)
    rows, coverage, validation = build_dataset(args.raw_dir)
    manifest = write_outputs(args.out_dir, rows, coverage, validation, args.raw_dir)
    print(
        json.dumps(
            {
                "out_dir": str(args.out_dir),
                "rows": len(rows),
                "tables_parsed": manifest["coverage"]["source_tables_parsed"],
                "reconciliations": validation["reconciliation_count"],
                "spot_checks": validation["spot_check_count"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
