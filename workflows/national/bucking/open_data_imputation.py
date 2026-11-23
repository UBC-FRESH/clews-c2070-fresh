"""Open-data tree-list imputation for the nine non-measured jurisdictions.

Scope
-----
The four measured provinces (BC, QC, NB, YT) are read from the frozen
four-province bucking pilot.  The other nine jurisdictions receive an explicit
E2 ``open_data_imputation`` tree list built from:

* SCANFI v2 species composition by province (open area weights);
* donor DBH/height pairs measured in BC, QC, NB and YT;
* the published BC Kozak (2004) and QC Schneider (2013) tapers used as
  cross-jurisdiction donors;
* the national pilot dimension-only sort set.

No restricted, DUA, request-only or official OSeCAN input is read.  Every
imputed row is labelled ``measured=false``, ``synthetic=false``,
``imputed=true`` and ``provenance_tier=E2_open_data_imputation``.  The label
``synthetic=false`` is deliberate: the geometry is an explicitly documented
imputation from open observations, not fabricated facility or harvest data.

This module is a screening evidence upgrade, not inventory, not an operational
supply estimate and not a national claim.  It does not calibrate to RSYC; RSYC
is used only as an aggregate stand-volume check.
"""

# The report body is a long markdown block; adjacent literals are intentional.
# ruff: noqa: ISC004

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from nemora.workflows.taper import load_kozak_parameters

from .national_bucking_pilot_v1 import (
    DECIDUOUS_CODES,
    NFI_TO_BC,
    NFI_TO_QC,
    PilotTaper,
    assert_no_unexpected_deletions,
    buck_tree,
    build_taper,
    load_national_sort_rules,
)
from .pilot_schema import PilotTree
from .schneider2013 import SchneiderTaperRegistry, load_taper_registry
from .taper import default_kozak_parameter_path

__all__ = [
    "ARTIFACT_ID",
    "DEFAULT_OUTPUT_DIR",
    "IMPUTED_PROVINCES",
    "MEASURED_PROVINCES",
    "PROVINCES",
    "assert_no_unexpected_deletions",
    "generate",
]

ARTIFACT_ID = "open_data_imputation_v1"
DEFAULT_OUTPUT_DIR = (
    Path(__file__).resolve().parents[3]
    / "data"
    / "derived"
    / "national"
    / "bucking"
    / ARTIFACT_ID
)
REPO_ROOT = Path(__file__).resolve().parents[3]

SCANFI_PROVINCE_PATH = (
    REPO_ROOT
    / "data/derived/national/assortments/national_area_weights"
    / "national_area_weights_province.csv"
)
MEASURED_DIR = (
    REPO_ROOT / "data/derived/national/bucking/four_province_bucking_pilot_v0"
)
MEASURED_TREE_PATH = MEASURED_DIR / "tree_list.csv"
MEASURED_LOG_PATH = MEASURED_DIR / "bucked_logs.csv"
MEASURED_RESIDUE_PATH = MEASURED_DIR / "residue_ledger.csv"
QC_TAPER_PATH = (
    REPO_ROOT / "workflows/national/bucking/rules/qc_schneider2013_taper.csv"
)
FACILITY_PATH = (
    REPO_ROOT
    / "data/derived/national/facilities/national_product_chain_v3"
    / "product_chain_by_province.csv"
)
RSYC_LIBRARY_PATH = (
    REPO_ROOT
    / "data/derived/national/assortments/rsyc_tile_production"
    / "rsyc_tile_production_library.csv"
)
NFI_VOLUME_PATH = (
    REPO_ROOT / "data/raw/nfi/standard_reports/t1/ca_t15_forage20_vol_en.csv"
)
NFI_BIOMASS_PATH = (
    REPO_ROOT / "data/raw/nfi/standard_reports/t1/ca_t17_forage20_biom_en.csv"
)
NFI_AREA_PATH = (
    REPO_ROOT / "data/raw/nfi/standard_reports/t1/ca_t4_for_area_en.csv"
)

PROVINCES: tuple[str, ...] = (
    "BC",
    "AB",
    "SK",
    "MB",
    "ON",
    "QC",
    "NB",
    "NS",
    "PE",
    "NL",
    "YT",
    "NT",
    "NU",
)
MEASURED_PROVINCES: tuple[str, ...] = ("BC", "QC", "NB", "YT")
IMPUTED_PROVINCES: tuple[str, ...] = (
    "AB",
    "SK",
    "MB",
    "ON",
    "NS",
    "PE",
    "NL",
    "NT",
    "NU",
)
PROVINCE_NAMES: dict[str, str] = {
    "BC": "British Columbia",
    "AB": "Alberta",
    "SK": "Saskatchewan",
    "MB": "Manitoba",
    "ON": "Ontario",
    "QC": "Quebec",
    "NB": "New Brunswick",
    "NS": "Nova Scotia",
    "PE": "Prince Edward Island",
    "NL": "Newfoundland and Labrador",
    "YT": "Yukon",
    "NT": "Northwest Territories",
    "NU": "Nunavut",
}

N_TREES_PER_PROVINCE = 36
#: Round representative stand density for the imputed lists.  This makes the
#: expanded volume a per-hectare quantity without calibrating to RSYC or any
#: inventory total.
REPRESENTATIVE_STEMS_PER_HA = 1000.0
MIN_SPECIES_SHARE = 0.005
MAX_SPECIES = 9
TARGET_SPECIES_COVERAGE = 0.99
SUM_TOLERANCE = 1e-9
PROVENANCE_E1 = "E1_measured_tree_list"
PROVENANCE_E2 = "E2_open_data_imputation"
TREE_LIST_E1 = "measured_tree_list"
TREE_LIST_E2 = "open_data_imputation"
MEASURED_TAPER_STATUS = {"local_published", "donor_published"}

#: Primary taper donor.  BC Kozak is used for the prairie/boreal/territorial
#: jurisdictions because its species key covers the pine, spruce, aspen, birch
#: and tamarack codes present in SCANFI.  QC Schneider is used for the Acadian
#: jurisdictions, with a per-species BC fallback for tamarack, hemlock and
#: Douglas-fir, which the QC table does not carry.
DONOR_PRIMARY: dict[str, str] = {
    "AB": "BC",
    "SK": "BC",
    "MB": "BC",
    "ON": "BC",
    "NS": "QC",
    "PE": "QC",
    "NL": "QC",
    "NT": "BC",
    "NU": "BC",
}
DONOR_SECONDARY: dict[str, str] = {"BC": "QC", "QC": "BC"}

#: SCANFI classes and area-weight targets.  ``BROADLEAF`` and ``CONIFEROUS``
#: are aggregates and are resolved to a documented donor species below.
SCANFI_TO_NFI: dict[str, str] = {
    "balsamFir": "ABIE.BAL",
    "blackSpruce": "PICE.MAR",
    "broadleaf": "BROADLEAF",
    "douglasFir": "PSEU.MEN",
    "jackPine": "PINU.BAN",
    "lodgepolePine": "PINU.CON",
    "otherConiferous": "CONIFEROUS",
    "ponderosaPine": "PINU.PON",
    "tamarack": "LARI.LAR",
    "whiteRedPine": "PINU.STR",
}

#: QC donor species supported by the Schneider table plus documented proxies.
QC_EXTRA: dict[str, tuple[str, bool, str]] = {
    "BROADLEAF": ("BOP", True, "SCANFI broadleaf aggregate -> QC paper birch donor"),
    "CONIFEROUS": ("EPB", True, "SCANFI other-coniferous aggregate -> QC white spruce donor"),
}
BC_EXTRA: dict[str, tuple[str, bool, str]] = {
    "BROADLEAF": ("AT", True, "SCANFI broadleaf aggregate -> BC trembling aspen donor"),
    "CONIFEROUS": ("SW", True, "SCANFI other-coniferous aggregate -> BC white spruce donor"),
    "PINU.PON": ("PY", False, "ponderosa pine -> BC ponderosa pine donor"),
}

class ImputationError(RuntimeError):
    """Raised when an open-data imputation contract is violated."""


@dataclass(frozen=True, slots=True)
class DonorResolution:
    """One target SCANFI species resolved to a donor species and taper."""

    nfi_species: str
    donor_species: str
    donor: str
    basis: str
    proxy: bool


@dataclass(frozen=True, slots=True)
class DonorTree:
    """One measured donor stem, retained as an empirical distribution point."""

    donor: str
    species: str
    dbh_cm: float
    height_m: float
    weight: float
    source_tree_id: str


def _repo_relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path.resolve())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_csv(path: Path, label: str) -> pd.DataFrame:
    if not path.is_file():
        raise ImputationError(f"{label} does not exist: {path}")
    frame = pd.read_csv(path, low_memory=False)
    if frame.empty:
        raise ImputationError(f"{label} is empty: {path}")
    return frame


def _write_csv(path: Path, rows: Iterable[Mapping[str, Any]], columns: list[str]) -> int:
    frame = pd.DataFrame(list(rows), columns=columns)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, lineterminator="\n")
    return len(frame)


def _safe_div(numerator: float, denominator: float) -> float:
    if denominator == 0.0 or not math.isfinite(denominator):
        return 0.0
    value = numerator / denominator
    return value if math.isfinite(value) else 0.0


def _finite(value: Any, label: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as error:
        raise ImputationError(f"{label} is not numeric: {value!r}") from error
    if not math.isfinite(parsed):
        raise ImputationError(f"{label} is not finite: {value!r}")
    return parsed


def _load_scanfi_composition() -> dict[str, dict[str, float]]:
    frame = _read_csv(SCANFI_PROVINCE_PATH, "SCANFI province area weights")
    needed = {"province", "scanfi_class", "area_fraction_of_forest"}
    if not needed.issubset(frame.columns):
        raise ImputationError(f"SCANFI province table lacks columns {sorted(needed)}")
    frame = frame[frame["province"].isin(IMPUTED_PROVINCES)].copy()
    frame["nfi_species"] = frame["scanfi_class"].map(SCANFI_TO_NFI)
    if frame["nfi_species"].isna().any():
        missing = sorted(frame.loc[frame["nfi_species"].isna(), "scanfi_class"].unique())
        raise ImputationError(f"unmapped SCANFI classes: {missing}")
    grouped = (
        frame.groupby(["province", "nfi_species"], as_index=False)["area_fraction_of_forest"]
        .sum()
        .sort_values(["province", "area_fraction_of_forest"], ascending=[True, False])
    )
    result: dict[str, dict[str, float]] = {}
    for province in IMPUTED_PROVINCES:
        rows = grouped[grouped["province"] == province]
        if rows.empty:
            raise ImputationError(f"no SCANFI composition for {province}")
        shares = {
            str(row.nfi_species): float(row.area_fraction_of_forest)
            for row in rows.itertuples(index=False)
        }
        total = math.fsum(shares.values())
        if abs(total - 1.0) > 1e-6:
            raise ImputationError(
                f"{province} SCANFI shares sum to {total!r}, not 1"
            )
        result[province] = shares
    return result


def _load_donor_trees() -> dict[str, list[DonorTree]]:
    frame = _read_csv(MEASURED_TREE_PATH, "measured donor tree list")
    needed = {
        "tree_id",
        "jurisdiction",
        "species",
        "dbh_cm",
        "height_m",
        "expansion_factor",
    }
    if not needed.issubset(frame.columns):
        raise ImputationError(f"measured tree list lacks columns {sorted(needed)}")
    frame = frame[frame["jurisdiction"].isin(MEASURED_PROVINCES)].copy()
    result: dict[str, list[DonorTree]] = {province: [] for province in MEASURED_PROVINCES}
    for row in frame.itertuples(index=False):
        province = str(row.jurisdiction).upper()
        result[province].append(
            DonorTree(
                donor=province,
                species=str(row.species).upper(),
                dbh_cm=_finite(row.dbh_cm, f"{row.tree_id} dbh"),
                height_m=_finite(row.height_m, f"{row.tree_id} height"),
                weight=_finite(row.expansion_factor, f"{row.tree_id} expansion"),
                source_tree_id=str(row.tree_id),
            )
        )
    for province, trees in result.items():
        if not trees:
            raise ImputationError(f"donor province {province} has no measured trees")
    return result


def _resolve_species(
    province: str,
    nfi_species: str,
) -> DonorResolution:
    primary = DONOR_PRIMARY[province]
    secondary = DONOR_SECONDARY[primary]
    attempts = [primary, secondary]
    for donor in attempts:
        if donor == "BC":
            if nfi_species in BC_EXTRA:
                code, proxy, basis = BC_EXTRA[nfi_species]
                return DonorResolution(
                    nfi_species, code, "BC", basis, proxy
                )
            code = NFI_TO_BC.get(nfi_species)
            if code is not None:
                return DonorResolution(
                    nfi_species,
                    code,
                    "BC",
                    f"NFI {nfi_species} -> BC Kozak species {code}",
                    False,
                )
        else:
            if nfi_species in QC_EXTRA:
                code, proxy, basis = QC_EXTRA[nfi_species]
                return DonorResolution(
                    nfi_species, code, "QC", basis, proxy
                )
            code = NFI_TO_QC.get(nfi_species)
            if code is not None:
                return DonorResolution(
                    nfi_species,
                    code,
                    "QC",
                    f"NFI {nfi_species} -> QC Schneider species {code}",
                    False,
                )
    raise ImputationError(
        f"no published donor taper for {province} species {nfi_species}"
    )


def _donor_pool(
    resolution: DonorResolution,
    donor_trees: Mapping[str, Sequence[DonorTree]],
) -> list[DonorTree]:
    pools = [resolution.donor]
    if resolution.donor == "BC":
        pools.append("YT")
    else:
        pools.append("NB")
    matched: list[DonorTree] = []
    for donor in pools:
        matched.extend(
            tree
            for tree in donor_trees[donor]
            if tree.species == resolution.donor_species
        )
        if len(matched) >= 4:
            break
    if len(matched) >= 4:
        return matched
    # Fall back to the same donor species group.  The fallback is documented in
    # the tree provenance and never presented as a species-specific donor.
    group = "deciduous" if resolution.donor_species in DECIDUOUS_CODES else "conifer"
    for donor in pools:
        matched.extend(
            tree
            for tree in donor_trees[donor]
            if (
                ("deciduous" if tree.species in DECIDUOUS_CODES else "conifer")
                == group
            )
        )
        if len(matched) >= 4:
            break
    deduped = {tree.source_tree_id: tree for tree in matched}
    if not deduped:
        raise ImputationError(
            f"no donor geometry for {resolution.donor}/{resolution.donor_species}"
        )
    return list(deduped.values())


def _weighted_quantile_pairs(pool: Sequence[DonorTree], count: int) -> list[tuple[float, float]]:
    if count <= 0:
        raise ImputationError("donor sample count must be positive")
    ordered = sorted(pool, key=lambda tree: (tree.dbh_cm, tree.height_m, tree.source_tree_id))
    weights = np.asarray([tree.weight for tree in ordered], dtype=float)
    if not np.isfinite(weights).all() or (weights <= 0.0).any():
        raise ImputationError("donor expansion weights must be finite and positive")
    cumulative = np.cumsum(weights)
    cumulative /= cumulative[-1]
    if count == 1:
        positions = [0.5]
    else:
        positions = [0.05 + 0.90 * index / (count - 1) for index in range(count)]
    pairs: list[tuple[float, float]] = []
    for position in positions:
        index = int(np.searchsorted(cumulative, position, side="left"))
        tree = ordered[min(index, len(ordered) - 1)]
        pairs.append((tree.dbh_cm, tree.height_m))
    return pairs


def _allocate_counts(shares: Mapping[str, float], total: int) -> dict[str, int]:
    selected = [
        (species, share)
        for species, share in sorted(shares.items(), key=lambda item: (-item[1], item[0]))
        if share >= MIN_SPECIES_SHARE
    ][:MAX_SPECIES]
    if not selected:
        raise ImputationError("no species above the minimum SCANFI share")
    selected_total = math.fsum(share for _, share in selected)
    if selected_total <= 0.0:
        raise ImputationError("selected SCANFI shares sum to zero")
    if len(selected) > total:
        selected = selected[:total]
        selected_total = math.fsum(share for _, share in selected)
    target = [(species, share / selected_total * total) for species, share in selected]
    counts = {species: 1 for species, _ in target}
    remaining = total - len(counts)
    if remaining < 0:
        raise ImputationError("more selected species than representative trees")
    remainders: list[tuple[float, str]] = []
    for species, quota in target:
        excess = max(0.0, quota - 1.0)
        whole = math.floor(excess)
        add = min(whole, remaining)
        counts[species] += add
        remaining -= add
        remainders.append((excess - whole, species))
    remainders.sort(key=lambda item: (-item[0], item[1]))
    cursor = 0
    while remaining > 0:
        if cursor >= len(remainders):
            cursor = 0
        counts[remainders[cursor][1]] += 1
        remaining -= 1
        cursor += 1
    if sum(counts.values()) != total:
        raise ImputationError(
            f"species allocation sums to {sum(counts.values())}, expected {total}"
        )
    return counts


def _resolve_taper(
    province: str,
    resolution: DonorResolution,
    qc_registry: SchneiderTaperRegistry,
    kozak_parameters: Mapping[tuple[str, str], Any],
) -> PilotTaper:
    base = build_taper(
        resolution.donor, resolution.donor_species, qc_registry, kozak_parameters
    )
    donor = resolution.donor
    return replace(
        base,
        taper_id=f"{province}-DONOR-{donor}-{base.taper_id}",
        jurisdiction=province,
        source_jurisdiction=donor,
        taper_status="donor_published",
        proxy=bool(base.proxy or resolution.proxy),
        source=(
            f"Cross-jurisdiction donor taper for {province}: {donor} published "
            f"{base.taper_id.split('-')[1] if '-' in base.taper_id else base.taper_id}. "
            f"{resolution.basis}. {base.source}"
        ),
    )


def _imputed_tree_rows(
    kozak_parameters: Mapping[tuple[str, str], Any],
    qc_registry: SchneiderTaperRegistry,
) -> tuple[
    list[PilotTree],
    dict[str, PilotTaper],
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
]:
    compositions = _load_scanfi_composition()
    donor_trees = _load_donor_trees()
    trees: list[PilotTree] = []
    tapers: dict[str, PilotTaper] = {}
    extras: dict[str, dict[str, Any]] = {}
    province_audit: dict[str, dict[str, Any]] = {}
    for province in IMPUTED_PROVINCES:
        shares = compositions[province]
        counts = _allocate_counts(shares, N_TREES_PER_PROVINCE)
        selected_total = math.fsum(
            shares[species] for species in counts
        )
        if selected_total <= 0.0:
            raise ImputationError(f"{province} selected SCANFI shares are zero")
        province_tree_count = 0
        donor_jurisdictions: set[str] = set()
        for species, count in sorted(counts.items(), key=lambda item: (-counts[item[0]], item[0])):
            resolution = _resolve_species(province, species)
            taper = _resolve_taper(
                province, resolution, qc_registry, kozak_parameters
            )
            pool = _donor_pool(resolution, donor_trees)
            species_specific = any(
                tree.species == resolution.donor_species for tree in pool
            )
            pairs = _weighted_quantile_pairs(pool, count)
            share = shares[species] / selected_total
            expansion = share / count * REPRESENTATIVE_STEMS_PER_HA
            for index, (dbh, height) in enumerate(pairs, start=1):
                province_tree_count += 1
                tree_id = f"{province}-E2-{species.replace('.', '')}-{index:02d}"
                provenance = (
                    f"open_data_imputation: SCANFI v2 province composition "
                    f"{province} {species} share={shares[species]:.6f}; "
                    f"donor {resolution.donor}/{resolution.donor_species} "
                    f"DBH={dbh:.2f} cm height={height:.2f} m from measured "
                    f"BC/QC/NB/YT tree list; "
                    f"{'species-specific' if species_specific else 'species-group fallback'} "
                    f"donor geometry; deterministic weighted-quantile draw; "
                    f"{resolution.basis}; not inventory; no PSP link"
                )
                tree = PilotTree(
                    tree_id=tree_id,
                    site_id=f"{province}-E2-REP",
                    jurisdiction=province,
                    species=resolution.donor_species,
                    species_group=(
                        "deciduous"
                        if resolution.donor_species in DECIDUOUS_CODES
                        else "conifer"
                    ),
                    dbh_cm=dbh,
                    height_m=height,
                    defect_pct=0.0,
                    expansion_factor=expansion,
                    status="live",
                    provenance=provenance,
                    evidence="assumption",
                    taper_id=taper.taper_id,
                )
                trees.append(tree)
                tapers[tree_id] = taper
                extras[tree_id] = {
                    "species_nfi": species,
                    "donor_species": resolution.donor_species,
                    "donor_taper_jurisdiction": resolution.donor,
                    "donor_basis": resolution.basis,
                    "species_specific_donor": species_specific,
                    "scanfi_share": shares[species],
                    "taper_status": "donor_published",
                    "taper_jurisdiction": resolution.donor,
                    "taper_bec_zone": taper.bec_zone,
                    "taper_proxy": bool(taper.proxy),
                    "taper_evidence": taper.evidence,
                    "taper_id": taper.taper_id,
                    "taper_source": taper.source,
                    "expansion_factor": expansion,
                    "expansion_factor_basis": (
                        f"representative_{REPRESENTATIVE_STEMS_PER_HA:.0f}_stems_per_ha"
                    ),
                    "source_tree_record_id": "not_applicable",
                    "source_site_id": "not_applicable",
                    "source_plot_id": "not_applicable",
                    "source_expansion_factor": 0.0,
                    "stratum_id": "not_applicable",
                    "dbh_stratum": "not_applicable",
                    "stratum_n_trees": 0,
                    "stratum_expansion_factor": 0.0,
                    "selection_method": "scanfi_share_donor_quantile_draw",
                    "bec_zone": "not_applicable",
                    "bec_subzone": "not_applicable",
                    "dbh_basis": "measured donor DBH, deterministic weighted quantile",
                    "height_basis": "measured donor height paired with donor DBH",
                    "tree_list_label": TREE_LIST_E2,
                    "tree_list_basis": "scanfi_v2_composition_plus_measured_donor_geometry",
                    "provenance_tier": PROVENANCE_E2,
                    "measured": False,
                    "imputed": True,
                    "synthetic": False,
                    "measured_tree_list": False,
                    "model_estimate": True,
                    "observed": False,
                    "policy_grade": False,
                    "national_claim": False,
                    "included_in_119m_case": province != "NU",
                }
                donor_jurisdictions.add(resolution.donor)
        if province_tree_count != N_TREES_PER_PROVINCE:
            raise ImputationError(
                f"{province} built {province_tree_count} trees, expected "
                f"{N_TREES_PER_PROVINCE}"
            )
        province_audit[province] = {
            "province": province,
            "province_name": PROVINCE_NAMES[province],
            "n_trees": province_tree_count,
            "n_species": len(counts),
            "species": ";".join(
                f"{species}:{count}" for species, count in sorted(counts.items())
            ),
            "scanfi_share_selected": selected_total,
            "expansion_total": math.fsum(
                extras[tree.tree_id]["expansion_factor"] for tree in trees
                if tree.jurisdiction == province
            ),
            "donor_taper_jurisdictions": ";".join(sorted(donor_jurisdictions)),
            "tree_list_basis": "scanfi_v2_composition_plus_measured_donor_geometry",
        }
    if len(trees) != len(IMPUTED_PROVINCES) * N_TREES_PER_PROVINCE:
        raise ImputationError(
            f"expected {len(IMPUTED_PROVINCES) * N_TREES_PER_PROVINCE} imputed "
            f"trees, got {len(trees)}"
        )
    return trees, tapers, extras, province_audit


def _buck_imputed_trees(
    trees: Sequence[PilotTree],
    tapers: Mapping[str, PilotTaper],
    extras: Mapping[str, Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rules = tuple(load_national_sort_rules())
    log_rows: list[dict[str, Any]] = []
    residue_rows: list[dict[str, Any]] = []
    for tree in trees:
        taper = tapers[tree.tree_id]
        tree_logs, residue = buck_tree(tree, taper, rules)
        extra = extras[tree.tree_id]
        expansion = tree.expansion_factor
        for log in tree_logs:
            row = log.to_dict()
            row.update(
                {
                    "species": tree.species,
                    "taper_status": taper.taper_status,
                    "taper_jurisdiction": taper.source_jurisdiction,
                    "taper_bec_zone": taper.bec_zone,
                    "taper_proxy": bool(taper.proxy),
                    "species_nfi": extra["species_nfi"],
                    "tree_list_basis": extra["tree_list_basis"],
                    "tree_list_label": TREE_LIST_E2,
                    "provenance_tier": PROVENANCE_E2,
                    "measured": False,
                    "imputed": True,
                    "synthetic": False,
                    "measured_tree_list": False,
                    "sort_rule_set": "national_pilot_sorts",
                    "expansion_factor": expansion,
                    "volume_m3_expanded": log.volume_m3 * expansion,
                    "net_volume_m3_expanded": log.net_volume_m3 * expansion,
                    "defect_deduction_m3_expanded": log.defect_deduction_m3 * expansion,
                    "value_cad_expanded": log.value_cad * expansion,
                    "model_estimate": True,
                    "observed": False,
                    "policy_grade": False,
                    "national_claim": False,
                }
            )
            log_rows.append(row)
        row = residue.to_dict()
        row.update(
            {
                "taper_status": taper.taper_status,
                "taper_jurisdiction": taper.source_jurisdiction,
                "taper_bec_zone": taper.bec_zone,
                "taper_proxy": bool(taper.proxy),
                "species_nfi": extra["species_nfi"],
                "tree_list_basis": extra["tree_list_basis"],
                "tree_list_label": TREE_LIST_E2,
                "provenance_tier": PROVENANCE_E2,
                "measured": False,
                "imputed": True,
                "synthetic": False,
                "measured_tree_list": False,
                "sort_rule_set": "national_pilot_sorts",
                "expansion_factor": expansion,
                "residue_volume_m3_expanded": residue.residue_volume_m3 * expansion,
                "residue_odt_expanded": residue.residue_odt * expansion,
                "green_tonnes_expanded": residue.green_tonnes * expansion,
                "chip_green_tonnes_expanded": residue.chip_green_tonnes * expansion,
                "hog_green_tonnes_expanded": residue.hog_green_tonnes * expansion,
                "recovered_log_m3_expanded": residue.recovered_log_m3 * expansion,
                "taper_total_m3_expanded": residue.taper_total_m3 * expansion,
                "stem_residue_odt": residue.residue_odt,
                "stem_residue_odt_expanded": residue.residue_odt * expansion,
                "branch_odt": 0.0,
                "foliage_odt": 0.0,
                "branch_foliage_odt": 0.0,
                "branch_odt_expanded": 0.0,
                "foliage_odt_expanded": 0.0,
                "branch_foliage_odt_expanded": 0.0,
                "total_residue_odt": residue.residue_odt,
                "total_residue_odt_expanded": residue.residue_odt * expansion,
                "recovery_factor_reference": 0.60,
                "recovery_scenario_basis": (
                    "0.35|0.60|0.75 scenarios; stem-only imputed residue; "
                    "no calibrated factor"
                ),
                "branch_foliage_estimated": False,
                "branch_foliage_evidence": "not_computed",
                "branch_foliage_equation_id": "not_computed",
                "branch_foliage_equation_species": "not_computed",
                "branch_foliage_reference": "not_computed",
                "branch_foliage_species_specific": False,
                "branch_foliage_proxy": False,
                "branch_foliage_proxy_reason": "stem-only imputed residue",
                "branch_foliage_source": "not_computed",
                "branch_foliage_source_licence": "not_computed",
                "allometry_map_jurisdiction": "not_applicable",
                "allometry_species_nfi": extra["species_nfi"],
                "residue_component_scope": "stem_only",
                "model_estimate": True,
                "observed": False,
                "policy_grade": False,
                "national_claim": False,
            }
        )
        for factor in (0.35, 0.60, 0.75):
            field = f"recoverable_residue_odt_r{round(factor * 100):03d}"
            row[field] = residue.residue_odt * factor
            row[f"{field}_expanded"] = residue.residue_odt * factor * expansion
        residue_rows.append(row)
    return log_rows, residue_rows


def _load_measured_rows() -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    tree_frame = _read_csv(MEASURED_TREE_PATH, "measured tree list")
    log_frame = _read_csv(MEASURED_LOG_PATH, "measured bucked logs")
    residue_frame = _read_csv(MEASURED_RESIDUE_PATH, "measured residue ledger")
    tree_rows: list[dict[str, Any]] = []
    for row in tree_frame.to_dict(orient="records"):
        row.update(
            {
                "provenance_tier": PROVENANCE_E1,
                "measured": True,
                "imputed": False,
                "synthetic": False,
                "tree_list_label": TREE_LIST_E1,
                "included_in_119m_case": True,
                "scanfi_share": 0.0,
                "donor_species": "not_applicable",
                "donor_taper_jurisdiction": "not_applicable",
                "donor_basis": "not_applicable",
                "species_specific_donor": False,
                "site_id": row.get("source_site_id", "not_applicable"),
                "taper_source": "measured pilot local/donor published taper",
            }
        )
        for text_field in (
            "bec_zone",
            "bec_subzone",
            "stratum_id",
            "dbh_stratum",
            "source_plot_id",
            "source_tree_record_id",
        ):
            if pd.isna(row.get(text_field)):
                row[text_field] = "not_applicable"
        if pd.isna(row.get("source_site_id")):
            row["source_site_id"] = "not_applicable"
            row["site_id"] = "not_applicable"
        tree_rows.append(row)
    log_rows: list[dict[str, Any]] = []
    for row in log_frame.to_dict(orient="records"):
        row.update(
            {
                "provenance_tier": PROVENANCE_E1,
                "measured": True,
                "imputed": False,
                "synthetic": False,
                "tree_list_label": TREE_LIST_E1,
            }
        )
        log_rows.append(row)
    residue_rows: list[dict[str, Any]] = []
    for row in residue_frame.to_dict(orient="records"):
        row.update(
            {
                "provenance_tier": PROVENANCE_E1,
                "measured": True,
                "imputed": False,
                "synthetic": False,
                "tree_list_label": TREE_LIST_E1,
            }
        )
        residue_rows.append(row)
    if set(tree_frame["jurisdiction"]) != set(MEASURED_PROVINCES):
        raise ImputationError("measured artifact does not contain exactly BC/QC/NB/YT")
    return tree_rows, log_rows, residue_rows


def _combine_rows(
    measured: Sequence[Mapping[str, Any]],
    imputed: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows = [dict(row) for row in measured] + [dict(row) for row in imputed]
    return sorted(rows, key=lambda row: (row["jurisdiction"], row["tree_id"]))


def _province_summary(
    trees: Sequence[Mapping[str, Any]],
    logs: Sequence[Mapping[str, Any]],
    residues: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    tree_frame = pd.DataFrame(trees)
    log_frame = pd.DataFrame(logs)
    residue_frame = pd.DataFrame(residues)
    rows: list[dict[str, Any]] = []
    for province in PROVINCES:
        province_trees = tree_frame[tree_frame["jurisdiction"] == province]
        province_logs = log_frame[log_frame["jurisdiction"] == province]
        province_residue = residue_frame[residue_frame["jurisdiction"] == province]
        if province_trees.empty:
            raise ImputationError(f"no tree rows for {province}")
        expansion = float(province_trees["expansion_factor"].sum())
        recovered = float(province_logs["volume_m3_expanded"].sum()) if not province_logs.empty else 0.0
        net = float(province_logs["net_volume_m3_expanded"].sum()) if not province_logs.empty else 0.0
        residue_m3 = float(province_residue["residue_volume_m3_expanded"].sum())
        stem_odt = float(province_residue["stem_residue_odt_expanded"].sum())
        branch_odt = float(province_residue["branch_odt_expanded"].sum()) if "branch_odt_expanded" in province_residue else 0.0
        foliage_odt = float(province_residue["foliage_odt_expanded"].sum()) if "foliage_odt_expanded" in province_residue else 0.0
        total_odt = float(province_residue["total_residue_odt_expanded"].sum())
        taper_total = float(province_residue["taper_total_m3_expanded"].sum())
        product = {
            product: float(province_logs.loc[province_logs["product"] == product, "volume_m3_expanded"].sum())
            if not province_logs.empty
            else 0.0
            for product in ("sawlog", "pulpwood", "other")
        }
        solid = product["sawlog"] + product["other"]
        tier = str(province_trees["provenance_tier"].iloc[0])
        measured = bool(province_trees["measured"].all())
        imputed = bool(province_trees["imputed"].all())
        donor_trees = int((province_trees["taper_status"] == "donor_published").sum())
        local_trees = int((province_trees["taper_status"] == "local_published").sum())
        balance = float(
            (province_residue["recovered_log_m3_expanded"] + province_residue["residue_volume_m3_expanded"] - province_residue["taper_total_m3_expanded"]).abs().max()
        )
        rows.append(
            {
                "province": province,
                "province_name": PROVINCE_NAMES[province],
                "provenance_tier": tier,
                "measured": measured,
                "imputed": imputed,
                "synthetic": False,
                "n_trees": len(province_trees),
                "n_measured_trees": len(province_trees) if measured else 0,
                "n_imputed_trees": len(province_trees) if imputed else 0,
                "n_logs": len(province_logs),
                "n_species": int(province_trees["species"].nunique()),
                "expansion_factor_total": expansion,
                "taper_total_m3_expanded": taper_total,
                "recovered_gross_m3_expanded": recovered,
                "recovered_net_m3_expanded": net,
                "residue_m3_expanded": residue_m3,
                "stem_residue_odt_expanded": stem_odt,
                "branch_odt_expanded": branch_odt,
                "foliage_odt_expanded": foliage_odt,
                "branch_foliage_odt_expanded": branch_odt + foliage_odt,
                "total_residue_odt_expanded": total_odt,
                "sawlog_m3_expanded": product["sawlog"],
                "pulpwood_m3_expanded": product["pulpwood"],
                "other_m3_expanded": product["other"],
                "sawlog_share_of_recovered": _safe_div(product["sawlog"], recovered),
                "pulpwood_share_of_recovered": _safe_div(product["pulpwood"], recovered),
                "other_share_of_recovered": _safe_div(product["other"], recovered),
                "solid_share_of_recovered": _safe_div(solid, recovered),
                "residue_m3_per_recovered_m3": _safe_div(residue_m3, recovered),
                "residue_odt_per_recovered_m3": _safe_div(stem_odt, recovered),
                "total_residue_odt_per_recovered_m3": _safe_div(total_odt, recovered),
                "local_taper_trees": local_trees,
                "donor_taper_trees": donor_trees,
                "local_taper_expansion_share": _safe_div(
                    float(province_trees.loc[province_trees["taper_status"] == "local_published", "expansion_factor"].sum()),
                    expansion,
                ),
                "donor_taper_expansion_share": _safe_div(
                    float(province_trees.loc[province_trees["taper_status"] == "donor_published", "expansion_factor"].sum()),
                    expansion,
                ),
                "volume_balance_m3": balance,
                "max_tree_balance_error_m3": float(province_residue["balance_error_m3"].max()),
                "residue_component_scope": (
                    "stem_plus_branch_foliage" if measured else "stem_only"
                ),
                "included_in_119m_case": bool(province_trees["included_in_119m_case"].all()),
                "model_estimate": True,
                "observed": False,
                "policy_grade": False,
                "national_claim": False,
            }
        )
    if len(rows) != len(PROVINCES):
        raise ImputationError(f"expected 13 province rows, got {len(rows)}")
    return rows


def _facility_comparison(
    summary: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    facility = _read_csv(FACILITY_PATH, "facility product-chain province table")
    required = {
        "province",
        "harvest_m3",
        "sawlog_m3",
        "pulpwood_m3",
        "other_log_m3",
        "harvest_residue_gross_odt",
        "coefficient_tier",
    }
    if not required.issubset(facility.columns):
        raise ImputationError(f"facility table lacks {sorted(required)}")
    facility_by_province = {
        str(row.province).upper(): row for row in facility.itertuples(index=False)
    }
    rows: list[dict[str, Any]] = []
    for province_row in summary:
        province = str(province_row["province"])
        row = facility_by_province.get(province)
        if row is None:
            raise ImputationError(f"facility table has no row for {province}")
        harvest = float(row.harvest_m3)
        facility_sawlog = _safe_div(float(row.sawlog_m3), harvest)
        facility_pulp = _safe_div(float(row.pulpwood_m3), harvest)
        facility_other = _safe_div(float(row.other_log_m3), harvest)
        facility_solid = facility_sawlog + facility_other
        facility_residue = _safe_div(float(row.harvest_residue_gross_odt), harvest)
        model_sawlog = float(province_row["sawlog_share_of_recovered"])
        model_pulp = float(province_row["pulpwood_share_of_recovered"])
        model_other = float(province_row["other_share_of_recovered"])
        model_solid = float(province_row["solid_share_of_recovered"])
        model_residue = float(province_row["total_residue_odt_per_recovered_m3"])
        sawlog_ratio = _safe_div(model_sawlog, facility_sawlog)
        solid_ratio = _safe_div(model_solid, facility_solid)
        residue_ratio = _safe_div(model_residue, facility_residue)
        sawlog_ok = abs(sawlog_ratio - 1.0) <= 0.20 if facility_sawlog > 0 else False
        residue_ok = abs(residue_ratio - 1.0) <= 0.20 if facility_residue > 0 else False
        if harvest == 0.0:
            fit_status = "no_modelled_harvest"
        elif sawlog_ok and residue_ok:
            fit_status = "within_20pct"
        elif sawlog_ok or residue_ok:
            fit_status = "partial"
        else:
            fit_status = "outside_20pct"
        if harvest == 0.0:
            dominance = "not_applicable_no_harvest"
        elif str(row.coefficient_tier) == "measured_bucking_pilot":
            dominance = "measured_facility_coefficient"
        elif fit_status == "within_20pct":
            dominance = "imputation_and_donor_aligned"
        else:
            dominance = "donor_coefficient_vs_imputation_diverge"
        rows.append(
            {
                "province": province,
                "province_name": PROVINCE_NAMES[province],
                "provenance_tier": province_row["provenance_tier"],
                "model_n_trees": province_row["n_trees"],
                "model_recovered_m3_expanded": province_row["recovered_gross_m3_expanded"],
                "model_sawlog_share": model_sawlog,
                "model_pulpwood_share": model_pulp,
                "model_other_share": model_other,
                "model_solid_share": model_solid,
                "model_residue_odt_per_m3": model_residue,
                "model_residue_component_scope": province_row["residue_component_scope"],
                "facility_harvest_m3": harvest,
                "facility_coefficient_tier": str(row.coefficient_tier),
                "facility_sawlog_share": facility_sawlog,
                "facility_pulpwood_share": facility_pulp,
                "facility_other_share": facility_other,
                "facility_solid_share": facility_solid,
                "facility_residue_odt_per_m3": facility_residue,
                "sawlog_share_model_over_facility": sawlog_ratio,
                "solid_share_model_over_facility": solid_ratio,
                "residue_odt_model_over_facility": residue_ratio,
                "sawlog_within_20pct": sawlog_ok,
                "residue_within_20pct": residue_ok,
                "fit_status": fit_status,
                "dominance_flag": dominance,
                "model_estimate": True,
                "observed": False,
                "policy_grade": False,
                "national_claim": False,
                "notes": (
                    "Facility shares are feedstock coefficients; harvest residue "
                    "and process residue are different streams. E2 provinces use "
                    "stem-only residue, so the residue comparison is a lower-bound "
                    "screening check. No calibration or national claim."
                ),
            }
        )
    return rows


def _rsyc_check(summary: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    frame = _read_csv(RSYC_LIBRARY_PATH, "RSYC tile production library")
    required = {"province", "area_ha", "volume_m3ha_age100", "response_units"}
    if not required.issubset(frame.columns):
        raise ImputationError(f"RSYC library lacks {sorted(required)}")
    frame = frame[
        frame["response_units"].astype(str).str.strip().eq("m3/ha")
        & frame["volume_m3ha_age100"].notna()
        & (frame["area_ha"] > 0.0)
    ].copy()
    rows: list[dict[str, Any]] = []
    for province_row in summary:
        province = str(province_row["province"])
        subset = frame[frame["province"].astype(str).str.upper() == province]
        if subset.empty:
            rsyc_mean = math.nan
            area = 0.0
        else:
            area = float(subset["area_ha"].sum())
            rsyc_mean = float(
                (subset["area_ha"] * subset["volume_m3ha_age100"]).sum() / area
            )
        model_mean = (
            float(province_row["taper_total_m3_expanded"])
            if province_row["imputed"]
            else 0.0
        )
        ratio = (
            _safe_div(model_mean, rsyc_mean)
            if province_row["imputed"]
            and math.isfinite(rsyc_mean)
            and rsyc_mean > 0.0
            else 0.0
        )
        rows.append(
            {
                "province": province,
                "province_name": PROVINCE_NAMES[province],
                "provenance_tier": province_row["provenance_tier"],
                "rsyc_area_ha": area,
                "rsyc_mean_volume_m3ha_age100": rsyc_mean,
                "imputed_taper_m3_per_ha_equivalent": model_mean,
                "imputed_over_rsyc": ratio,
                "check_status": (
                    "aggregate_context_only"
                    if province_row["imputed"]
                    else "measured_list_not_compared"
                ),
                "notes": (
                    "RSYC is a stand-volume aggregate check, not a calibration "
                    "target. Imputed expansion totals 1000 stems/ha."
                ),
            }
        )
    return rows


def _nfi_context() -> dict[str, Any]:
    volume_frame = pd.read_csv(NFI_VOLUME_PATH, skiprows=1)
    biomass_frame = pd.read_csv(NFI_BIOMASS_PATH, skiprows=1)
    volume_total = float(
        volume_frame.loc[volume_frame["Forest Type"].eq("Subtotal"), "Total"].iloc[0]
    )
    biomass_total = float(
        biomass_frame.loc[biomass_frame["Forest type"].eq("Subtotal"), "Total"].iloc[0]
    )
    area_parts = NFI_AREA_PATH.read_text(encoding="utf-8").strip().splitlines()
    if len(area_parts) < 2:
        raise ImputationError(f"NFI area table is malformed: {NFI_AREA_PATH}")
    area_total_thousand_ha = float(area_parts[-1].split(",")[0])
    return {
        "nfi_national_volume_million_m3": volume_total,
        "nfi_national_aboveground_biomass_million_t": biomass_total,
        "nfi_forest_land_ha": area_total_thousand_ha * 1000.0,
        "source_files": [
            str(NFI_VOLUME_PATH.relative_to(REPO_ROOT)),
            str(NFI_BIOMASS_PATH.relative_to(REPO_ROOT)),
            str(NFI_AREA_PATH.relative_to(REPO_ROOT)),
        ],
        "check_status": "aggregate_context_only_not_calibrated",
    }


def _provenance_coverage(
    summary: Sequence[Mapping[str, Any]],
    imputed_audit: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for province_row in summary:
        province = str(province_row["province"])
        if province_row["measured"]:
            rows.append(
                {
                    "province": province,
                    "province_name": PROVINCE_NAMES[province],
                    "provenance_tier": PROVENANCE_E1,
                    "measured": True,
                    "imputed": False,
                    "synthetic": False,
                    "n_trees": province_row["n_trees"],
                    "n_measured_trees": province_row["n_trees"],
                    "n_imputed_trees": 0,
                    "tree_list_basis": "four_province_bucking_pilot_v0_measured",
                    "species_source": "measured FAIB PSP / MAGPlot Tier 1 tree lists",
                    "taper_source": "local BC/QC or donor QC/BC published taper",
                    "included_in_119m_case": province_row["included_in_119m_case"],
                    "notes": "E1 measured donor tree list; no imputation used.",
                }
            )
            continue
        audit = imputed_audit[province]
        rows.append(
            {
                "province": province,
                "province_name": PROVINCE_NAMES[province],
                "provenance_tier": PROVENANCE_E2,
                "measured": False,
                "imputed": True,
                "synthetic": False,
                "n_trees": audit["n_trees"],
                "n_measured_trees": 0,
                "n_imputed_trees": audit["n_trees"],
                "tree_list_basis": audit["tree_list_basis"],
                "species_source": "SCANFI v2 province area weights",
                "taper_source": (
                    "donor published tapers: " + audit["donor_taper_jurisdictions"]
                ),
                "included_in_119m_case": province != "NU",
                "notes": (
                    "Open-data imputation from SCANFI composition and measured "
                    "BC/QC/NB/YT donor DBH/height pairs; 1000 stems/ha "
                    "representative expansion; no DUA, request or restricted data. "
                    "NU has no modelled harvest in the 119 Mm3 case."
                ),
            }
        )
    if len(rows) != len(PROVINCES):
        raise ImputationError(f"expected 13 provenance rows, got {len(rows)}")
    return rows


def _summary_payload(
    trees: Sequence[Mapping[str, Any]],
    logs: Sequence[Mapping[str, Any]],
    residues: Sequence[Mapping[str, Any]],
    summary: Sequence[Mapping[str, Any]],
    facility: Sequence[Mapping[str, Any]],
    provenance: Sequence[Mapping[str, Any]],
    rsyc: Sequence[Mapping[str, Any]],
    nfi: Mapping[str, Any],
    runtime_seconds: float,
) -> dict[str, Any]:
    checks = {
        "thirteen_provinces": {row["province"] for row in summary} == set(PROVINCES),
        "four_measured_provinces": {
            row["province"]
            for row in provenance
            if row["provenance_tier"] == PROVENANCE_E1
        }
        == set(MEASURED_PROVINCES),
        "nine_imputed_provinces": {
            row["province"]
            for row in provenance
            if row["provenance_tier"] == PROVENANCE_E2
        }
        == set(IMPUTED_PROVINCES),
        "imputed_rows_not_measured": all(
            not row["measured"] and row["imputed"] and not row["synthetic"]
            for row in provenance
            if row["provenance_tier"] == PROVENANCE_E2
        ),
        "measured_rows_not_imputed": all(
            row["measured"] and not row["imputed"] and not row["synthetic"]
            for row in provenance
            if row["provenance_tier"] == PROVENANCE_E1
        ),
        "taper_labels_present": all(
            row.get("taper_status") in MEASURED_TAPER_STATUS
            for row in trees
        ),
        "imputed_expansion_is_1000_stems_per_ha": all(
            abs(
                math.fsum(
                    float(row["expansion_factor"])
                    for row in trees
                    if row["jurisdiction"] == province
                )
                - REPRESENTATIVE_STEMS_PER_HA
            )
            <= 1e-6
            for province in IMPUTED_PROVINCES
        ),
        "volume_balance": all(
            abs(float(row["volume_balance_m3"])) <= 1e-6 for row in summary
        ),
        "no_national_claim": all(not row["national_claim"] for row in trees),
        "no_dua_dependency": True,
    }
    if not all(checks.values()):
        failed = [name for name, value in checks.items() if not value]
        raise ImputationError(f"summary checks failed: {failed}")
    return {
        "artifact": ARTIFACT_ID,
        "generated_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "runtime_seconds": round(runtime_seconds, 3),
        "labels": {
            "model_estimate": True,
            "observed": False,
            "policy_grade": False,
            "national_claim": False,
            "measured_provinces": list(MEASURED_PROVINCES),
            "imputed_provinces": list(IMPUTED_PROVINCES),
        },
        "counts": {
            "provinces": len(summary),
            "trees": len(trees),
            "measured_trees": sum(1 for row in trees if row["measured"]),
            "imputed_trees": sum(1 for row in trees if row["imputed"]),
            "logs": len(logs),
            "residue_rows": len(residues),
        },
        "checks": checks,
        "province_summary": list(summary),
        "facility_comparison": list(facility),
        "provenance_coverage": list(provenance),
        "rsyc_aggregate_check": list(rsyc),
        "nfi_context": dict(nfi),
        "notes": [
            "E2 is an open-data evidence upgrade, not E1 measurement.",
            "No restricted, DUA, request-only or official OSeCAN input is used.",
            "Imputed residue is stem-only; measured residue includes branch/foliage.",
            "RSYC is an aggregate context check, never a calibration target.",
            "No national supply or official grade claim is made.",
        ],
    }


def _report(payload: Mapping[str, Any]) -> str:
    rows = payload["province_summary"]
    facility = {row["province"]: row for row in payload["facility_comparison"]}
    lines = [
        "# Open-data tree-list imputation for the nine non-measured provinces",
        "",
        "**Status:** E2 open-data imputation; `model_estimate=true`, "
        "`observed=false`, `policy_grade=false`, `national_claim=false`. "
        "No DUA, request or restricted data used.",
        "",
        "## Method",
        "",
        "SCANFI v2 province species composition sets species shares. Donor "
        "DBH/height pairs are drawn deterministically from measured BC/QC/NB/YT "
        "stems. Published BC Kozak (2004) or QC Schneider (2013) tapers are "
        "used as cross-jurisdiction donors; the national pilot dimension-only "
        "sorts and the existing bucking/residue engine are applied unchanged.",
        "Each imputed province has 36 representative stems. Expansion totals "
        "1000 stems/ha, a round representative density, so expanded volume is "
        "a per-hectare quantity without calibration. Product shares and residue "
        "per recovered m3 are scale-free. Imputed residue is stem-only.",
        "",
        "## Coverage",
        "",
        "| Province | Tier | Trees | Basis | Taper donor |",
        "|---|---|---:|---|---|",
    ]
    for row in payload["provenance_coverage"]:
        lines.append(
            f"| {row['province']} | {row['provenance_tier']} | {row['n_trees']} | "
            f"{row['tree_list_basis']} | {row['taper_source']} |"
        )
    lines.extend(
        [
            "",
            "## Logs and residue",
            "",
            "| Province | Logs | Recovered m3/ha-eq | Residue m3 | Residue ODT | "
            "Sawlog | Pulp | Other | Solid |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in rows:
        lines.append(
            f"| {row['province']} | {row['n_logs']} | "
            f"{row['recovered_gross_m3_expanded']:.2f} | "
            f"{row['residue_m3_expanded']:.2f} | "
            f"{row['total_residue_odt_expanded']:.2f} | "
            f"{row['sawlog_share_of_recovered']:.3f} | "
            f"{row['pulpwood_share_of_recovered']:.3f} | "
            f"{row['other_share_of_recovered']:.3f} | "
            f"{row['solid_share_of_recovered']:.3f} |"
        )
    lines.extend(
        [
            "",
            "## Facility coefficient fit",
            "",
            "| Province | Model solid | Facility solid | Solid ratio | Model residue "
            "ODT/m3 | Facility residue ODT/m3 | Fit | Dominance flag |",
            "|---|---:|---:|---:|---:|---:|---|---|",
        ]
    )
    for row in rows:
        match = facility[row["province"]]
        lines.append(
            f"| {row['province']} | {row['solid_share_of_recovered']:.3f} | "
            f"{match['facility_solid_share']:.3f} | "
            f"{match['solid_share_model_over_facility']:.3f} | "
            f"{row['total_residue_odt_per_recovered_m3']:.4f} | "
            f"{match['facility_residue_odt_per_m3']:.4f} | "
            f"{match['fit_status']} | {match['dominance_flag']} |"
        )
    lines.extend(
        [
            "",
            "Facility shares are feedstock coefficients; harvest and process "
            "residue are different streams. E2 residue is stem-only, so the "
            "residue comparison is a lower-bound screening check. Divergence is "
            "reported, not tuned away.",
            "",
            "## Aggregate checks",
            "",
            f"- NFI national tree volume: {payload['nfi_context']['nfi_national_volume_million_m3']:.1f} million m3.",
            f"- NFI national above-ground biomass: {payload['nfi_context']['nfi_national_aboveground_biomass_million_t']:.1f} million t.",
            f"- NFI forest land: {payload['nfi_context']['nfi_forest_land_ha']:.0f} ha.",
            "- RSYC age-100 mean volume is an aggregate context check, never a calibration target.",
            "",
            "## Limits",
            "",
            "- E2 is not E1. Province composition is SCANFI-modelled, donor geometry is not local.",
            "- No local taper, local DBH distribution, defect, grade, sweep, knot or rot measurement exists for the nine imputed jurisdictions.",
            "- The facility comparison is a screening check, not a supply estimate or official grade assignment.",
            "- NU is represented for 13-province coverage but has no modelled harvest in the 119 Mm3 case.",
        ]
    )
    return "\n".join(lines) + "\n"


def generate(
    out_dir: Path = DEFAULT_OUTPUT_DIR,
    *,
    kozak_parameters_path: Path | None = None,
) -> dict[str, Any]:
    started = datetime.now(UTC)
    assert_no_unexpected_deletions()
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    kozak_path = Path(kozak_parameters_path or default_kozak_parameter_path())
    kozak_parameters = load_kozak_parameters(kozak_path)
    qc_registry = load_taper_registry(QC_TAPER_PATH)

    measured_trees, measured_logs, measured_residue = _load_measured_rows()
    imputed_trees, tapers, extras, imputed_audit = _imputed_tree_rows(
        kozak_parameters, qc_registry
    )
    imputed_logs, imputed_residue = _buck_imputed_trees(imputed_trees, tapers, extras)
    imputed_tree_rows = [
        {**tree.to_dict(), **extras[tree.tree_id]} for tree in imputed_trees
    ]
    tree_rows = _combine_rows(measured_trees, imputed_tree_rows)
    log_rows = _combine_rows(measured_logs, imputed_logs)
    residue_rows = _combine_rows(measured_residue, imputed_residue)
    summary_rows = _province_summary(tree_rows, log_rows, residue_rows)
    facility_rows = _facility_comparison(summary_rows)
    provenance_rows = _provenance_coverage(summary_rows, imputed_audit)
    rsyc_rows = _rsyc_check(summary_rows)
    nfi = _nfi_context()
    runtime = (datetime.now(UTC) - started).total_seconds()
    payload = _summary_payload(
        tree_rows,
        log_rows,
        residue_rows,
        summary_rows,
        facility_rows,
        provenance_rows,
        rsyc_rows,
        nfi,
        runtime,
    )

    tree_columns = sorted({key for row in tree_rows for key in row})
    log_columns = sorted({key for row in log_rows for key in row})
    residue_columns = sorted({key for row in residue_rows for key in row})
    summary_columns = list(summary_rows[0])
    facility_columns = list(facility_rows[0])
    provenance_columns = list(provenance_rows[0])
    rsyc_columns = list(rsyc_rows[0])

    _write_csv(out_dir / "tree_list.csv", tree_rows, tree_columns)
    _write_csv(out_dir / "bucked_logs.csv", log_rows, log_columns)
    _write_csv(out_dir / "residue_ledger.csv", residue_rows, residue_columns)
    _write_csv(out_dir / "province_summary.csv", summary_rows, summary_columns)
    _write_csv(out_dir / "facility_coefficient_comparison.csv", facility_rows, facility_columns)
    _write_csv(out_dir / "provenance_coverage.csv", provenance_rows, provenance_columns)
    _write_csv(out_dir / "rsyc_aggregate_check.csv", rsyc_rows, rsyc_columns)
    _write_csv(
        out_dir / "imputation_audit.csv",
        [imputed_audit[province] for province in IMPUTED_PROVINCES],
        list(imputed_audit[IMPUTED_PROVINCES[0]]),
    )
    (out_dir / "summary.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (out_dir / "report.md").write_text(_report(payload), encoding="utf-8")

    output_paths = [
        out_dir / name
        for name in (
            "tree_list.csv",
            "bucked_logs.csv",
            "residue_ledger.csv",
            "province_summary.csv",
            "facility_coefficient_comparison.csv",
            "provenance_coverage.csv",
            "rsyc_aggregate_check.csv",
            "imputation_audit.csv",
            "summary.json",
            "report.md",
        )
    ]
    manifest = {
        "artifact": ARTIFACT_ID,
        "generated_utc": payload["generated_utc"],
        "runtime_seconds": payload["runtime_seconds"],
        "inputs": {
            _repo_relative(path): _sha256(path)
            for path in (
                SCANFI_PROVINCE_PATH,
                MEASURED_TREE_PATH,
                MEASURED_LOG_PATH,
                MEASURED_RESIDUE_PATH,
                QC_TAPER_PATH,
                FACILITY_PATH,
                RSYC_LIBRARY_PATH,
                NFI_VOLUME_PATH,
                NFI_BIOMASS_PATH,
                NFI_AREA_PATH,
            )
        },
        "outputs": {
            _repo_relative(path): _sha256(path) for path in output_paths
        },
        "checks": payload["checks"],
        "labels": payload["labels"],
        "no_dua_dependency": True,
        "no_official_osecan_change": True,
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="output directory (default: data/derived/national/bucking/open_data_imputation_v1)",
    )
    parser.add_argument(
        "--kozak-parameters",
        type=Path,
        default=None,
        help="override the Kozak parameter CSV",
    )
    args = parser.parse_args(argv)
    payload = generate(args.out_dir, kozak_parameters_path=args.kozak_parameters)
    print(json.dumps(payload["counts"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
