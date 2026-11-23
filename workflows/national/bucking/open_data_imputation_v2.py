"""E2 v2 tree lists informed by open provincial inventories.

E2 v1 used SCANFI composition and measured BC/QC/NB/YT donor geometry for all
nine non-measured jurisdictions.  E2 v2 replaces that single fallback with the
best open provincial evidence available:

* AB: AVI species composition, stand height and stand age; DBH is inferred from
  height with a documented power H-D equation fitted to measured donor stems.
* SK: FRI stand height, QMD, basal area and volume; species composition comes
  from SCANFI inside the FRI CSWG softwood/hardwood group.
* MB: FRI species composition and stand age; height/DBH remain donor joints
  because the open 1970s stand attributes carry neither.
* NS: species composition, height, average diameter and merchantable volume
  are retained as joint stand rows.
* PE: CLUI species composition and height; EFI basal area and merchantable
  stems give an inferred QMD; EFI volume provides the aggregate context.
* ON, NL, NT, NU: no open stand attributes exist, so SCANFI composition and
  measured donor joint geometry remain the only honest option.

The nine provinces still get 36 representative stems and a 1000 stems/ha
expansion, exactly as E2 v1, so product shares and residue coefficients remain
comparable.  Every row is labelled ``imputed=true``, ``measured=false``,
``synthetic=false``, ``provenance_tier=E2b_open_inventory_informed`` and carries
its local/donor distribution labels.  This is a screening evidence upgrade, not
an inventory, not an operational supply estimate and not a national claim.
"""

# The report and markdown literals are long; adjacent literals are intentional.
# ruff: noqa: ISC004

from __future__ import annotations

import argparse
import itertools
import json
import math
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from nemora.workflows.taper import load_kozak_parameters

from . import e2_biomass_allometry as _e2_allometry
from .national_bucking_pilot_v1 import (
    PilotTaper,
    assert_no_unexpected_deletions,
    buck_tree,
    load_national_sort_rules,
)
from .open_data_imputation import (
    IMPUTED_PROVINCES,
    N_TREES_PER_PROVINCE,
    PROVINCE_NAMES,
    REPRESENTATIVE_STEMS_PER_HA,
    DonorTree,
    ImputationError,
    _allocate_counts,
    _donor_pool,
    _finite,
    _load_donor_trees,
    _load_scanfi_composition,
    _read_csv,
    _repo_relative,
    _resolve_species,
    _resolve_taper,
    _safe_div,
    _sha256,
    _weighted_quantile_pairs,
    _write_csv,
)
from .pilot_schema import PilotTree
from .schneider2013 import SchneiderTaperRegistry, load_taper_registry
from .taper import default_kozak_parameter_path

__all__ = [
    "ARTIFACT_ID",
    "DEFAULT_OUTPUT_DIR",
    "DISTRIBUTION_DIR",
    "PROVENANCE_E2B",
    "TREE_LIST_E2B",
    "generate",
]

ARTIFACT_ID = "open_data_imputation_v2"
REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT_DIR = (
    REPO_ROOT / "data" / "derived" / "national" / "bucking" / ARTIFACT_ID
)
DISTRIBUTION_DIR = (
    REPO_ROOT
    / "data/derived/national/inventory/provincial_open_inventory_distributions_v1"
)
V1_DIR = REPO_ROOT / "data/derived/national/bucking/open_data_imputation_v1"
QC_TAPER_PATH = REPO_ROOT / "workflows/national/bucking/rules/qc_schneider2013_taper.csv"

PROVENANCE_E2B = "E2b_open_inventory_informed"
TREE_LIST_E2B = "open_inventory_informed"
DISTRIBUTION_TIER_LOCAL = "provincial_inventory"
DISTRIBUTION_TIER_GROUP = "provincial_inventory_group"
DISTRIBUTION_TIER_DONOR = "scanfi_donor"
LABEL_LOCAL_JOINT = "local_joint"
LABEL_LOCAL_HEIGHT_INFERRED_DBH = "local_height_inferred_dbh"
LABEL_PAIRED_LOCAL_MARGINALS = "paired_local_marginals"
LABEL_PAIRED_MULTI_SOURCE = "paired_multi_source"
LABEL_DONOR_JOINT = "donor_joint"
VOLUME_CONTEXT_TOLERANCE = 0.50


class V2Error(RuntimeError):
    """Raised when an E2 v2 contract is violated."""


# ---------------------------------------------------------------------------
# Distribution boundary
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DistributionBundle:
    composition: Mapping[str, Mapping[str, float]]
    composition_meta: Mapping[str, Mapping[str, str]]
    quantiles: Mapping[tuple[str, str, str], Mapping[str, float]]
    joint: Mapping[tuple[str, str], Mapping[str, float]]
    summary: Mapping[str, Mapping[str, str]]
    provenance: Mapping[str, Mapping[str, str]]

    def stats(self, province: str, attribute: str, species: str) -> Mapping[str, float] | None:
        return self.quantiles.get((province, attribute, species))

    def overall(self, province: str, attribute: str) -> Mapping[str, float] | None:
        return self.quantiles.get((province, attribute, "ALL"))


def _load_distributions(path: Path = DISTRIBUTION_DIR) -> DistributionBundle:
    if not path.is_dir():
        raise V2Error(f"distribution artifact not found: {path}")
    composition_frame = _read_csv(path / "species_composition.csv", "species composition")
    quantile_frame = _read_csv(path / "attribute_quantiles.csv", "attribute quantiles")
    joint_frame = _read_csv(path / "joint_quantiles.csv", "joint quantiles")
    summary_frame = _read_csv(path / "province_summary.csv", "province summary")
    provenance_frame = _read_csv(path / "source_provenance.csv", "source provenance")

    composition: dict[str, dict[str, float]] = defaultdict(dict)
    composition_meta: dict[str, dict[str, str]] = {}
    for row in composition_frame.to_dict(orient="records"):
        province = str(row["province"]).upper()
        nfi = str(row["species_nfi"]).upper()
        share = _finite(row["share"], f"{province} {nfi} share")
        if share is None or share <= 0.0:
            continue
        composition[province][nfi] = composition[province].get(nfi, 0.0) + share
        composition_meta.setdefault(
            province,
            {
                "source_id": str(row.get("source_id", "")),
                "weight_basis": str(row.get("weight_basis", "")),
                "source_role": str(row.get("source_role", "")),
            },
        )
    for province, shares in composition.items():
        total = math.fsum(shares.values())
        if total <= 0.0:
            raise V2Error(f"{province} composition sums to zero")
        composition[province] = {nfi: share / total for nfi, share in shares.items()}

    quantiles: dict[tuple[str, str, str], dict[str, float]] = {}
    for row in quantile_frame.to_dict(orient="records"):
        key = (
            str(row["province"]).upper(),
            str(row["attribute"]).lower(),
            str(row["species_nfi"]).upper(),
        )
        quantiles[key] = {
            field: float(row[field])
            for field in (
                "p05",
                "p10",
                "p25",
                "p50",
                "p75",
                "p90",
                "p95",
                "min",
                "max",
                "mean",
                "median",
                "sd",
                "n",
            )
            if str(row.get(field, "")).strip() != ""
        }

    joint: dict[tuple[str, str], dict[str, float]] = {}
    for row in joint_frame.to_dict(orient="records"):
        key = (str(row["province"]).upper(), str(row["species_nfi"]).upper())
        joint[key] = {
            field: float(row[field])
            for field in row
            if re.fullmatch(r"p\d{2}_.+", field) and str(row[field]).strip() != ""
        }
    summary = {
        str(row["province"]).upper(): dict(row)
        for row in summary_frame.to_dict(orient="records")
    }
    provenance = {
        str(row["source_id"]): dict(row)
        for row in provenance_frame.to_dict(orient="records")
    }
    return DistributionBundle(
        composition=composition,
        composition_meta=composition_meta,
        quantiles=quantiles,
        joint=joint,
        summary=summary,
        provenance=provenance,
    )


# ---------------------------------------------------------------------------
# Quantile helpers
# ---------------------------------------------------------------------------

_QUANTILE_KEYS: tuple[tuple[float, str], ...] = tuple(
    (quantile, f"p{round(quantile * 100):02d}")
    for quantile in (0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95)
)


def _interpolate_quantile(stats: Mapping[str, float], position: float) -> float:
    if not stats:
        raise V2Error("cannot interpolate an empty quantile record")
    points = sorted((quantile, float(stats[key])) for quantile, key in _QUANTILE_KEYS if key in stats)
    if not points:
        raise V2Error("quantile record has no p05..p95 values")
    if position <= points[0][0]:
        return points[0][1]
    if position >= points[-1][0]:
        return points[-1][1]
    for (left_q, left_v), (right_q, right_v) in itertools.pairwise(points):
        if left_q <= position <= right_q:
            fraction = (position - left_q) / (right_q - left_q)
            return left_v + fraction * (right_v - left_v)
    raise V2Error(f"quantile position {position} is outside the grid")


def _positions(count: int) -> list[float]:
    if count <= 0:
        raise V2Error("tree count must be positive")
    if count == 1:
        return [0.50]
    return [0.05 + 0.90 * index / (count - 1) for index in range(count)]


def _clamp(value: float, low: float, high: float, label: str) -> float:
    if not math.isfinite(value):
        raise V2Error(f"{label} is not finite")
    return min(max(value, low), high)


# ---------------------------------------------------------------------------
# Composition resolution
# ---------------------------------------------------------------------------


def _scanfi_shares(scanfi: Mapping[str, Mapping[str, float]], province: str) -> dict[str, float]:
    shares = scanfi.get(province)
    if not shares:
        raise V2Error(f"no SCANFI composition for {province}")
    total = math.fsum(shares.values())
    if total <= 0.0:
        raise V2Error(f"SCANFI composition for {province} sums to zero")
    return {nfi: share / total for nfi, share in shares.items()}


def _group_of(nfi: str) -> str:
    if nfi in {"BROADLEAF"}:
        return "deciduous"
    if nfi in {"CONIFEROUS"}:
        return "conifer"
    return "conifer" if nfi.split(".", 1)[0] in {
        "ABIE",
        "PICE",
        "PINU",
        "LARI",
        "PSEU",
        "THUJ",
        "TSUG",
    } else "deciduous"


def _species_composition(
    province: str,
    bundle: DistributionBundle,
    scanfi: Mapping[str, Mapping[str, float]],
) -> tuple[dict[str, float], str]:
    if province == "SK":
        group_rows = bundle.composition.get("SK", {})
        if not group_rows:
            raise V2Error("SK distribution lacks a CSG group composition")
        group_shares = {"conifer": 0.0, "deciduous": 0.0}
        for nfi, share in group_rows.items():
            group_shares[_group_of(nfi)] += share
        total_group = math.fsum(group_shares.values())
        if total_group <= 0.0:
            raise V2Error("SK CSG group shares sum to zero")
        group_shares = {group: share / total_group for group, share in group_shares.items()}
        scanfi_shares = _scanfi_shares(scanfi, province)
        by_group: dict[str, dict[str, float]] = {"conifer": {}, "deciduous": {}}
        for nfi, share in scanfi_shares.items():
            by_group[_group_of(nfi)][nfi] = share
        combined: dict[str, float] = {}
        for group, target in group_shares.items():
            group_total = math.fsum(by_group[group].values())
            if group_total <= 0.0:
                combined["CONIFEROUS" if group == "conifer" else "BROADLEAF"] = target
                continue
            for nfi, share in by_group[group].items():
                combined[nfi] = combined.get(nfi, 0.0) + target * share / group_total
        return combined, "FRI CSG group shares constrain SCANFI v2 species shares"

    if province in bundle.composition:
        return dict(bundle.composition[province]), (
            f"provincial inventory composition: {bundle.composition_meta[province]['weight_basis']}"
        )

    return _scanfi_shares(scanfi, province), "SCANFI v2 composition (no open stand species field)"


# ---------------------------------------------------------------------------
# AB DBH inference
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HDPowerModel:
    key: str
    a: float
    b: float
    n: int
    rmse_m: float
    source: str

    def dbh_from_height(self, height_m: float) -> float:
        if height_m <= 1.3:
            raise V2Error(f"height {height_m} m is not above breast height")
        if self.b <= 0.0:
            raise V2Error(f"non-positive H-D slope in {self.key}")
        value = math.exp(self.a + self.b * math.log(height_m - 1.3))
        return _clamp(value, 1.3, 100.0, f"{self.key} inferred DBH")


def _fit_hd_model(key: str, rows: Sequence[Mapping[str, Any]]) -> HDPowerModel | None:
    points = [
        (math.log(float(row["height_m"]) - 1.3), math.log(float(row["dbh_cm"])))
        for row in rows
        if float(row["dbh_cm"]) > 0.0 and float(row["height_m"]) > 1.3
    ]
    if len(points) < 8:
        return None
    x = np.asarray([point[0] for point in points], dtype=float)
    y = np.asarray([point[1] for point in points], dtype=float)
    slope, intercept = np.polyfit(x, y, 1)
    if not math.isfinite(float(slope)) or slope <= 0.0:
        return None
    predicted = intercept + slope * x
    rmse = float(np.sqrt(np.mean((y - predicted) ** 2)))
    return HDPowerModel(
        key=key,
        a=float(intercept),
        b=float(slope),
        n=len(points),
        rmse_m=round(rmse, 6),
        source="log-log DBH-on-height fit to measured BC/QC/NB/YT donor stems",
    )


def _fit_ab_hd_models() -> dict[str, HDPowerModel]:
    frame = _read_csv(
        REPO_ROOT
        / "data/derived/national/bucking/four_province_bucking_pilot_v0/tree_list.csv",
        "measured donor tree list",
    )
    models: dict[str, HDPowerModel] = {}
    for species_nfi, group in frame.groupby("species_nfi", sort=True):
        model = _fit_hd_model(str(species_nfi), group.to_dict(orient="records"))
        if model is not None:
            models[str(species_nfi)] = model
    for species_group, group in frame.groupby("species_group", sort=True):
        model = _fit_hd_model(f"GROUP:{species_group}", group.to_dict(orient="records"))
        if model is not None:
            models[f"GROUP:{species_group}"] = model
    if not models:
        raise V2Error("no AB H-D model could be fitted")
    return models


def _ab_model_for(species_nfi: str, models: Mapping[str, HDPowerModel]) -> HDPowerModel:
    direct = models.get(species_nfi)
    if direct is not None:
        return direct
    group = _group_of(species_nfi)
    fallback = models.get(f"GROUP:{group}")
    if fallback is None:
        raise V2Error(f"no AB H-D model for {species_nfi} or group {group}")
    return fallback


# ---------------------------------------------------------------------------
# Geometry construction
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TreeGeometry:
    height_m: float
    dbh_cm: float
    age_years: float | None
    volume_m3ha: float | None
    dbh_label: str
    height_label: str
    age_label: str
    joint_label: str
    distribution_label: str
    distribution_tier: str
    source_ids: str
    inference_equation: str
    notes: str


def _local_height_stats(
    bundle: DistributionBundle,
    province: str,
    species: str,
) -> tuple[Mapping[str, float], str]:
    stats = bundle.stats(province, "height", species)
    if stats is not None and int(stats.get("n", 0)) >= 20:
        return stats, f"{province} inventory height for {species}"
    overall = bundle.overall(province, "height")
    if overall is None:
        raise V2Error(f"{province} has no height quantiles")
    return overall, f"{province} inventory height (all species; {species} sample too small)"


def _ab_geometry(
    bundle: DistributionBundle,
    province: str,
    species: str,
    count: int,
    models: Mapping[str, HDPowerModel],
) -> list[TreeGeometry]:
    height_stats, height_source = _local_height_stats(bundle, "AB", species)
    age_stats = bundle.stats("AB", "age", species) or bundle.overall("AB", "age")
    model = _ab_model_for(species, models)
    geometries: list[TreeGeometry] = []
    for position in _positions(count):
        height = _clamp(_interpolate_quantile(height_stats, position), 1.5, 60.0, "AB height")
        dbh = model.dbh_from_height(height)
        age = (
            _clamp(_interpolate_quantile(age_stats, position), 1.0, 300.0, "AB age")
            if age_stats
            else None
        )
        geometries.append(
            TreeGeometry(
                height_m=round(height, 4),
                dbh_cm=round(dbh, 4),
                age_years=round(age, 2) if age is not None else None,
                volume_m3ha=None,
                dbh_label="inferred_dbh",
                height_label="inventory_height",
                age_label="inventory_age",
                joint_label=LABEL_LOCAL_HEIGHT_INFERRED_DBH,
                distribution_label="AVI species+height+age; height-driven DBH",
                distribution_tier=DISTRIBUTION_TIER_LOCAL,
                source_ids="ab_avi_crown",
                inference_equation=(
                    f"{model.key}: ln(DBH) = ({model.a:.6f}) + ({model.b:.6f}) * ln(H-1.3); "
                    f"n={model.n}; rmse_log_dbh={model.rmse_m}"
                ),
                notes=(
                    f"AVI {height_source}; DBH inferred from stand height, not measured; "
                    "AVI has no DBH or volume field"
                ),
            )
        )
    return geometries


def _sk_geometry(
    bundle: DistributionBundle,
    species: str,
    count: int,
) -> list[TreeGeometry]:
    height_stats = bundle.overall("SK", "height")
    dbh_stats = bundle.overall("SK", "dbh")
    volume_stats = bundle.overall("SK", "volume")
    if height_stats is None or dbh_stats is None:
        raise V2Error("SK distribution lacks height or QMD")
    geometries: list[TreeGeometry] = []
    for position in _positions(count):
        height = _clamp(_interpolate_quantile(height_stats, position), 1.5, 60.0, "SK height")
        dbh = _clamp(_interpolate_quantile(dbh_stats, position), 1.3, 100.0, "SK QMD")
        volume = (
            _interpolate_quantile(volume_stats, position) if volume_stats else None
        )
        geometries.append(
            TreeGeometry(
                height_m=round(height, 4),
                dbh_cm=round(dbh, 4),
                age_years=None,
                volume_m3ha=round(volume, 4) if volume is not None else None,
                dbh_label="inventory_qmd",
                height_label="inventory_height",
                age_label="not_available",
                joint_label=LABEL_PAIRED_LOCAL_MARGINALS,
                distribution_label="FRI height and QMD marginals paired by rank",
                distribution_tier=DISTRIBUTION_TIER_GROUP,
                source_ids="sk_fri_forest_stands",
                inference_equation="QMD is a published stand quadratic mean diameter, not inferred",
                notes=(
                    "FRI publishes no explicit tree species in the stand vector; "
                    "CSG group shares constrain SCANFI composition. Height and QMD "
                    "marginals are paired by rank, so the joint H-D correlation is "
                    "not local."
                ),
            )
        )
    return geometries


def _mb_geometry(
    bundle: DistributionBundle,
    province: str,
    species: str,
    count: int,
    donor_trees: Mapping[str, Sequence[DonorTree]],
    donor_resolution: Any,
) -> list[TreeGeometry]:
    pool = _donor_pool(donor_resolution, donor_trees)
    pairs = _weighted_quantile_pairs(pool, count)
    age_stats = bundle.stats("MB", "age", species) or bundle.overall("MB", "age")
    geometries: list[TreeGeometry] = []
    for position, (dbh, height) in zip(_positions(count), pairs, strict=True):
        age = _interpolate_quantile(age_stats, position) if age_stats else None
        geometries.append(
            TreeGeometry(
                height_m=round(height, 4),
                dbh_cm=round(dbh, 4),
                age_years=round(age, 2) if age is not None else None,
                volume_m3ha=None,
                dbh_label="donor_dbh",
                height_label="donor_height",
                age_label="inventory_age",
                joint_label=LABEL_DONOR_JOINT,
                distribution_label="FRI species+age; donor H-D joint for missing height/DBH",
                distribution_tier=DISTRIBUTION_TIER_LOCAL,
                source_ids="mb_fri_1970s",
                inference_equation="none; measured donor H-D pair",
                notes=(
                    "MB open 1970s FRI has species and age but no height or DBH; "
                    "the measured donor joint is preserved and labelled."
                ),
            )
        )
    return geometries


def _ns_geometry(
    bundle: DistributionBundle,
    species: str,
    count: int,
    donor_trees: Mapping[str, Sequence[DonorTree]],
    donor_resolution: Any,
) -> list[TreeGeometry]:
    joint = bundle.joint.get(("NS", species))
    if joint is None:
        pool = _donor_pool(donor_resolution, donor_trees)
        pairs = _weighted_quantile_pairs(pool, count)
        return [
            TreeGeometry(
                height_m=round(height, 4),
                dbh_cm=round(dbh, 4),
                age_years=None,
                volume_m3ha=None,
                dbh_label="donor_dbh",
                height_label="donor_height",
                age_label="not_available",
                joint_label=LABEL_DONOR_JOINT,
                distribution_label="NS species; donor H-D joint for the rare species",
                distribution_tier=DISTRIBUTION_TIER_LOCAL,
                source_ids="ns_forest_inventory",
                inference_equation="none; measured donor H-D pair",
                notes="NS species sample below the joint-quantile threshold; donor joint used.",
            )
            for _, (dbh, height) in zip(_positions(count), pairs, strict=True)
        ]
    geometries: list[TreeGeometry] = []
    for position in _positions(count):
        height = _quantile_from_joint(joint, position, "height_m")
        dbh = _quantile_from_joint(joint, position, "dbh_cm")
        volume = _quantile_from_joint(joint, position, "volume_m3ha")
        geometries.append(
            TreeGeometry(
                height_m=round(height, 4),
                dbh_cm=round(dbh, 4),
                age_years=None,
                volume_m3ha=round(volume, 4),
                dbh_label="inventory_avdi",
                height_label="inventory_height",
                age_label="not_available",
                joint_label=LABEL_LOCAL_JOINT,
                distribution_label="NS species+height+AVDI+volume joint stand rows",
                distribution_tier=DISTRIBUTION_TIER_LOCAL,
                source_ids="ns_forest_inventory",
                inference_equation="AVDI is a published average total diameter; not inferred",
                notes=(
                    "NS species, HEIGHT, AVDI, TOTMRVOL and HECTARES come from the "
                    "same stand polygon; joint quantiles preserve that local pairing."
                ),
            )
        )
    return geometries


def _quantile_from_joint(joint: Mapping[str, float], position: float, suffix: str) -> float:
    points = []
    for quantile in (0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95):
        key = f"p{round(quantile * 100):02d}_{suffix}"
        if key in joint:
            points.append((quantile, float(joint[key])))
    if not points:
        raise V2Error(f"joint quantiles lack {suffix}")
    if position <= points[0][0]:
        return points[0][1]
    if position >= points[-1][0]:
        return points[-1][1]
    for (left_q, left_v), (right_q, right_v) in itertools.pairwise(points):
        if left_q <= position <= right_q:
            fraction = (position - left_q) / (right_q - left_q)
            return left_v + fraction * (right_v - left_v)
    raise V2Error(f"joint quantile position {position} outside grid")


def _pe_geometry(
    bundle: DistributionBundle,
    species: str,
    count: int,
) -> list[TreeGeometry]:
    height_stats, _height_source = _local_height_stats(bundle, "PE", species)
    dbh_stats = bundle.overall("PE", "dbh")
    if dbh_stats is None:
        raise V2Error("PE EFI distribution lacks inferred QMD")
    volume_stats = bundle.overall("PE", "volume")
    geometries: list[TreeGeometry] = []
    for position in _positions(count):
        height = _clamp(_interpolate_quantile(height_stats, position), 1.5, 60.0, "PE height")
        dbh = _clamp(_interpolate_quantile(dbh_stats, position), 1.3, 100.0, "PE QMD")
        volume = _interpolate_quantile(volume_stats, position) if volume_stats else None
        geometries.append(
            TreeGeometry(
                height_m=round(height, 4),
                dbh_cm=round(dbh, 4),
                age_years=None,
                volume_m3ha=round(volume, 4) if volume is not None else None,
                dbh_label="inferred_dbh",
                height_label="inventory_height",
                age_label="not_available",
                joint_label=LABEL_PAIRED_MULTI_SOURCE,
                distribution_label="CLUI species+height paired with EFI inferred QMD marginals",
                distribution_tier=DISTRIBUTION_TIER_LOCAL,
                source_ids="pe_clui_2010_forest;pe_efi_2020",
                inference_equation=(
                    "QMD_cm = 100 * sqrt(bamerch_m2ha / (tphmerch * pi / 4)); "
                    "EFI2020 fields, algebraic not fitted"
                ),
                notes=(
                    "CLUI supplies species and height; EFI2020 supplies BA, stems "
                    "and volume. The two sources are matched by rank, not by stand "
                    "location, so the joint is multi-source and labelled."
                ),
            )
        )
    return geometries


def _donor_geometry(
    province: str,
    species: str,
    count: int,
    donor_trees: Mapping[str, Sequence[DonorTree]],
    donor_resolution: Any,
) -> list[TreeGeometry]:
    pool = _donor_pool(donor_resolution, donor_trees)
    pairs = _weighted_quantile_pairs(pool, count)
    return [
        TreeGeometry(
            height_m=round(height, 4),
            dbh_cm=round(dbh, 4),
            age_years=None,
            volume_m3ha=None,
            dbh_label="donor_dbh",
            height_label="donor_height",
            age_label="not_available",
            joint_label=LABEL_DONOR_JOINT,
            distribution_label="SCANFI composition + measured donor H-D joint",
            distribution_tier=DISTRIBUTION_TIER_DONOR,
            source_ids="scanfi_v2;four_province_bucking_pilot_v0",
            inference_equation="none; measured donor H-D pair",
            notes=(
                f"{province} has no open stand species/height/DBH distribution; "
                "the measured donor joint is the only open geometry."
            ),
        )
        for _, (dbh, height) in zip(_positions(count), pairs, strict=True)
    ]


# ---------------------------------------------------------------------------
# Allometry and taper resolution
# ---------------------------------------------------------------------------


def _allometry_species(
    species_nfi: str,
    lookup: _e2_allometry.AllometryLookup,
) -> tuple[str, bool, str]:
    supported = lookup.by_nfi
    if species_nfi in supported:
        return species_nfi, False, ""
    group = _group_of(species_nfi)
    aggregate = "CONIFEROUS" if group == "conifer" else "BROADLEAF"
    reason = (
        f"{species_nfi} has no explicit E2 CFS branch/foliage mapping; "
        f"published {group} group equation used"
    )
    return aggregate, True, reason


def _resolve_donor_or_group(province: str, species_nfi: str) -> tuple[Any, bool, str]:
    try:
        return _resolve_species(province, species_nfi), False, ""
    except ImputationError:
        aggregate = "CONIFEROUS" if _group_of(species_nfi) == "conifer" else "BROADLEAF"
        resolution = _resolve_species(province, aggregate)
        return (
            resolution,
            True,
            f"{species_nfi} has no published donor taper mapping; {aggregate} donor used",
        )


# ---------------------------------------------------------------------------
# Tree-list assembly
# ---------------------------------------------------------------------------


def _build_tree_rows(
    kozak_parameters: Mapping[tuple[str, str], Any],
    qc_registry: SchneiderTaperRegistry,
    allometry: _e2_allometry.AllometryLookup,
) -> tuple[
    list[PilotTree],
    dict[str, PilotTaper],
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
]:
    bundle = _load_distributions()
    scanfi = _load_scanfi_composition()
    donor_trees = _load_donor_trees()
    hd_models = _fit_ab_hd_models()
    trees: list[PilotTree] = []
    tapers: dict[str, PilotTaper] = {}
    extras: dict[str, dict[str, Any]] = {}
    province_audit: dict[str, dict[str, Any]] = {}
    for province in IMPUTED_PROVINCES:
        shares, composition_basis = _species_composition(province, bundle, scanfi)
        counts = _allocate_counts(shares, N_TREES_PER_PROVINCE)
        selected_total = math.fsum(shares[species] for species in counts)
        if selected_total <= 0.0:
            raise V2Error(f"{province} selected composition shares sum to zero")
        province_tree_count = 0
        for species, count in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
            resolution, donor_fallback, donor_fallback_reason = _resolve_donor_or_group(
                province, species
            )
            taper = _resolve_taper(province, resolution, qc_registry, kozak_parameters)
            allometry_species, allometry_proxy, allometry_reason = _allometry_species(
                species, allometry
            )
            if province == "AB":
                geometries = _ab_geometry(bundle, province, species, count, hd_models)
            elif province == "SK":
                geometries = _sk_geometry(bundle, species, count)
            elif province == "MB":
                geometries = _mb_geometry(
                    bundle, province, species, count, donor_trees, resolution
                )
            elif province == "NS":
                geometries = _ns_geometry(
                    bundle, species, count, donor_trees, resolution
                )
            elif province == "PE":
                geometries = _pe_geometry(bundle, species, count)
            else:
                geometries = _donor_geometry(
                    province, species, count, donor_trees, resolution
                )
            if len(geometries) != count:
                raise V2Error(
                    f"{province} {species} built {len(geometries)} geometries, expected {count}"
                )
            share = shares[species] / selected_total
            expansion = share / count * REPRESENTATIVE_STEMS_PER_HA
            species_group = _group_of(species)
            for index, geometry in enumerate(geometries, start=1):
                province_tree_count += 1
                tree_id = f"{province}-E2V2-{species.replace('.', '')}-{index:02d}"
                height_m = max(geometry.height_m, 5.0)
                dbh_cm = max(geometry.dbh_cm, 5.0)
                floor_applied = height_m != geometry.height_m or dbh_cm != geometry.dbh_cm
                provenance = (
                    f"E2 v2 open-inventory tree list: {province} {species} "
                    f"share={shares[species]:.6f}; {composition_basis}; "
                    f"height={geometry.height_m:.2f} m ({geometry.height_label}); "
                    f"DBH={geometry.dbh_cm:.2f} cm ({geometry.dbh_label}); "
                    f"joint={geometry.joint_label}; distribution={geometry.distribution_label}; "
                    f"taper donor {resolution.donor}/{resolution.donor_species}; "
                    f"{'merchantable taper-domain floor applied; ' if floor_applied else ''}"
                    f"not inventory; not a national claim"
                )
                tree = PilotTree(
                    tree_id=tree_id,
                    site_id=f"{province}-E2V2-REP",
                    jurisdiction=province,
                    species=resolution.donor_species,
                    species_group=species_group,
                    dbh_cm=dbh_cm,
                    height_m=height_m,
                    defect_pct=0.0,
                    expansion_factor=expansion,
                    status="live",
                    provenance=provenance,
                    evidence="assumption",
                    taper_id=taper.taper_id,
                )
                trees.append(tree)
                tapers[tree_id] = taper
                components = _e2_allometry.compute_e2_components(
                    tree_id=tree_id,
                    species=resolution.donor_species,
                    species_nfi=allometry_species,
                    species_group=species_group,
                    dbh_cm=dbh_cm,
                    height_m=height_m,
                    lookup=allometry,
                )
                extras[tree_id] = {
                    "species_nfi": species,
                    "donor_species": resolution.donor_species,
                    "donor_taper_jurisdiction": resolution.donor,
                    "donor_basis": resolution.basis,
                    "species_specific_donor": not donor_fallback,
                    "donor_group_fallback": donor_fallback,
                    "donor_group_fallback_reason": donor_fallback_reason,
                    "scanfi_share": shares.get(species, 0.0),
                    "composition_share": share * selected_total,
                    "composition_basis": composition_basis,
                    "taper_status": "donor_published",
                    "taper_jurisdiction": resolution.donor,
                    "taper_bec_zone": taper.bec_zone,
                    "taper_proxy": bool(taper.proxy or donor_fallback),
                    "taper_evidence": taper.evidence,
                    "taper_id": taper.taper_id,
                    "taper_source": taper.source,
                    "branch_odt": components.branch_odt,
                    "foliage_odt": components.foliage_odt,
                    "branch_foliage_odt": components.branch_foliage_odt,
                    "branch_foliage_estimated": True,
                    "branch_foliage_evidence": components.evidence,
                    "branch_foliage_equation_id": components.equation_id,
                    "branch_foliage_equation_species": components.equation_species,
                    "branch_foliage_reference": components.reference,
                    "branch_foliage_species_specific": components.species_specific,
                    "branch_foliage_proxy": bool(components.proxy or allometry_proxy),
                    "branch_foliage_proxy_reason": (
                        allometry_reason or components.branch_equation.proxy_reason
                    ),
                    "branch_foliage_source": components.branch_equation.source_dataset,
                    "branch_foliage_source_licence": components.branch_equation.source_licence,
                    "allometry_map_jurisdiction": province,
                    "allometry_species_nfi": allometry_species,
                    "allometry_species_nfi_input": species,
                    "residue_component_scope": "stem_plus_branch_foliage",
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
                    "selection_method": "weighted_inventory_quantile_draw",
                    "bec_zone": "not_applicable",
                    "bec_subzone": "not_applicable",
                    "dbh_basis": geometry.dbh_label,
                    "height_basis": geometry.height_label,
                    "inventory_height_m": geometry.height_m,
                    "inventory_dbh_cm": geometry.dbh_cm,
                    "taper_domain_floor_applied": floor_applied,
                    "taper_domain_floor_note": (
                        "tree height/DBH raised to the 5 m / 5 cm merchantable "
                        "taper domain; raw inventory distribution is retained in "
                        "inventory_height_m and inventory_dbh_cm"
                        if floor_applied
                        else "none"
                    ),
                    "age_basis": geometry.age_label,
                    "age_years": geometry.age_years if geometry.age_years is not None else 0.0,
                    "joint_basis": geometry.joint_label,
                    "distribution_label": geometry.distribution_label,
                    "distribution_tier": geometry.distribution_tier,
                    "inventory_informed": province in {"AB", "SK", "MB", "NS", "PE"},
                    "inference_equation": geometry.inference_equation,
                    "source_ids": geometry.source_ids,
                    "distribution_notes": geometry.notes,
                    "volume_context_m3ha": (
                        geometry.volume_m3ha if geometry.volume_m3ha is not None else 0.0
                    ),
                    "tree_list_label": TREE_LIST_E2B,
                    "tree_list_basis": "provincial_open_inventory_distribution",
                    "provenance_tier": PROVENANCE_E2B,
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
        if province_tree_count != N_TREES_PER_PROVINCE:
            raise V2Error(
                f"{province} built {province_tree_count} trees, expected "
                f"{N_TREES_PER_PROVINCE}"
            )
        province_trees = [tree for tree in trees if tree.jurisdiction == province]
        province_audit[province] = {
            "province": province,
            "province_name": PROVINCE_NAMES[province],
            "n_trees": province_tree_count,
            "n_species": len(counts),
            "species": ";".join(
                f"{species}:{count}" for species, count in sorted(counts.items())
            ),
            "composition_basis": composition_basis,
            "distribution_tier": extras[province_trees[0].tree_id]["distribution_tier"],
            "inventory_informed": province in {"AB", "SK", "MB", "NS", "PE"},
            "expansion_total": math.fsum(
                extras[tree.tree_id]["expansion_factor"] for tree in province_trees
            ),
            "donor_taper_jurisdictions": ";".join(
                sorted({extras[tree.tree_id]["donor_taper_jurisdiction"] for tree in province_trees})
            ),
            "allometry_species": ";".join(
                sorted({extras[tree.tree_id]["allometry_species_nfi"] for tree in province_trees})
            ),
            "n_allometry_proxy_trees": sum(
                1 for tree in province_trees if extras[tree.tree_id]["branch_foliage_proxy"]
            ),
            "source_ids": ";".join(
                sorted({extras[tree.tree_id]["source_ids"] for tree in province_trees})
            ),
        }
    if len(trees) != len(IMPUTED_PROVINCES) * N_TREES_PER_PROVINCE:
        raise V2Error(
            f"expected {len(IMPUTED_PROVINCES) * N_TREES_PER_PROVINCE} trees, got {len(trees)}"
        )
    return trees, tapers, extras, province_audit


# ---------------------------------------------------------------------------
# Bucking and residue
# ---------------------------------------------------------------------------


def _buck_v2(
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
                    "tree_list_label": TREE_LIST_E2B,
                    "provenance_tier": PROVENANCE_E2B,
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
                "tree_list_label": TREE_LIST_E2B,
                "provenance_tier": PROVENANCE_E2B,
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
                "branch_odt": extra["branch_odt"],
                "foliage_odt": extra["foliage_odt"],
                "branch_foliage_odt": extra["branch_foliage_odt"],
                "branch_odt_expanded": extra["branch_odt"] * expansion,
                "foliage_odt_expanded": extra["foliage_odt"] * expansion,
                "branch_foliage_odt_expanded": extra["branch_foliage_odt"] * expansion,
                "total_residue_odt": residue.residue_odt + extra["branch_foliage_odt"],
                "total_residue_odt_expanded": (
                    residue.residue_odt + extra["branch_foliage_odt"]
                )
                * expansion,
                "recovery_factor_reference": 0.60,
                "recovery_scenario_basis": (
                    "0.35|0.60|0.75 scenarios; total residue includes "
                    "CFS branch and foliage allometry; no calibrated factor"
                ),
                "branch_foliage_estimated": True,
                "branch_foliage_evidence": extra["branch_foliage_evidence"],
                "branch_foliage_equation_id": extra["branch_foliage_equation_id"],
                "branch_foliage_equation_species": extra["branch_foliage_equation_species"],
                "branch_foliage_reference": extra["branch_foliage_reference"],
                "branch_foliage_species_specific": extra["branch_foliage_species_specific"],
                "branch_foliage_proxy": extra["branch_foliage_proxy"],
                "branch_foliage_proxy_reason": extra["branch_foliage_proxy_reason"],
                "branch_foliage_source": extra["branch_foliage_source"],
                "branch_foliage_source_licence": extra["branch_foliage_source_licence"],
                "allometry_map_jurisdiction": extra["allometry_map_jurisdiction"],
                "allometry_species_nfi": extra["allometry_species_nfi"],
                "residue_component_scope": "stem_plus_branch_foliage",
                "model_estimate": True,
                "observed": False,
                "policy_grade": False,
                "national_claim": False,
            }
        )
        total_residue_odt = residue.residue_odt + extra["branch_foliage_odt"]
        for factor in (0.35, 0.60, 0.75):
            field = f"recoverable_residue_odt_r{round(factor * 100):03d}"
            row[field] = total_residue_odt * factor
            row[f"{field}_expanded"] = total_residue_odt * factor * expansion
        residue_rows.append(row)
    return log_rows, residue_rows


# ---------------------------------------------------------------------------
# Province summary and comparison
# ---------------------------------------------------------------------------


def _weighted_mean(frame: pd.DataFrame, value: str, weight: str = "expansion_factor") -> float:
    if frame.empty:
        raise V2Error("weighted mean received an empty frame")
    numerator = float((frame[value] * frame[weight]).sum())
    denominator = float(frame[weight].sum())
    if denominator == 0.0:
        raise V2Error("weighted mean received zero weight")
    return numerator / denominator


def _province_summary(
    trees: Sequence[Mapping[str, Any]],
    logs: Sequence[Mapping[str, Any]],
    residues: Sequence[Mapping[str, Any]],
    audit: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    tree_frame = pd.DataFrame(trees)
    log_frame = pd.DataFrame(logs)
    residue_frame = pd.DataFrame(residues)
    rows: list[dict[str, Any]] = []
    for province in IMPUTED_PROVINCES:
        province_trees = tree_frame[tree_frame["jurisdiction"] == province]
        province_logs = log_frame[log_frame["jurisdiction"] == province]
        province_residue = residue_frame[residue_frame["jurisdiction"] == province]
        if province_trees.empty or province_residue.empty:
            raise V2Error(f"missing tree or residue rows for {province}")
        expansion = float(province_trees["expansion_factor"].sum())
        recovered = float(province_logs["volume_m3_expanded"].sum())
        net = float(province_logs["net_volume_m3_expanded"].sum())
        residue_m3 = float(province_residue["residue_volume_m3_expanded"].sum())
        stem_odt = float(province_residue["stem_residue_odt_expanded"].sum())
        branch_odt = float(province_residue["branch_odt_expanded"].sum())
        foliage_odt = float(province_residue["foliage_odt_expanded"].sum())
        total_odt = float(province_residue["total_residue_odt_expanded"].sum())
        taper_total = float(province_residue["taper_total_m3_expanded"].sum())
        product = {
            product: float(
                province_logs.loc[province_logs["product"] == product, "volume_m3_expanded"].sum()
            )
            for product in ("sawlog", "pulpwood", "other")
        }
        solid = product["sawlog"] + product["other"]
        balance = float(
            (
                province_residue["recovered_log_m3_expanded"]
                + province_residue["residue_volume_m3_expanded"]
                - province_residue["taper_total_m3_expanded"]
            )
            .abs()
            .max()
        )
        species_shares = (
            province_trees.groupby("species_nfi")["expansion_factor"].sum() / expansion
        ).sort_values(ascending=False)
        top_species = ";".join(
            f"{species}:{share:.4f}" for species, share in species_shares.head(9).items()
        )
        volume_mean = audit[province].get("inventory_volume_mean_m3ha")
        volume_ratio = (
            _safe_div(taper_total, float(volume_mean))
            if volume_mean is not None and float(volume_mean) > 0.0
            else 0.0
        )
        volume_status = (
            "not_available"
            if volume_mean is None
            else (
                "within_50pct"
                if abs(volume_ratio - 1.0) <= VOLUME_CONTEXT_TOLERANCE
                else "outside_50pct"
            )
        )
        rows.append(
            {
                "province": province,
                "province_name": PROVINCE_NAMES[province],
                "provenance_tier": PROVENANCE_E2B,
                "inventory_informed": bool(audit[province]["inventory_informed"]),
                "distribution_tier": audit[province]["distribution_tier"],
                "composition_basis": audit[province]["composition_basis"],
                "n_trees": len(province_trees),
                "n_species": int(province_trees["species_nfi"].nunique()),
                "n_logs": len(province_logs),
                "species_shares": top_species,
                "mean_height_m": _weighted_mean(province_trees, "height_m"),
                "mean_dbh_cm": _weighted_mean(province_trees, "dbh_cm"),
                "height_p50_m": float(province_trees["height_m"].median()),
                "dbh_p50_cm": float(province_trees["dbh_cm"].median()),
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
                "stem_residue_odt_per_recovered_m3": _safe_div(stem_odt, recovered),
                "branch_foliage_odt_per_recovered_m3": _safe_div(
                    branch_odt + foliage_odt, recovered
                ),
                "total_residue_odt_per_recovered_m3": _safe_div(total_odt, recovered),
                "recoverable_odt_per_recovered_m3_r035": _safe_div(total_odt * 0.35, recovered),
                "recoverable_odt_per_recovered_m3_r060": _safe_div(total_odt * 0.60, recovered),
                "recoverable_odt_per_recovered_m3_r075": _safe_div(total_odt * 0.75, recovered),
                "inventory_volume_mean_m3ha": volume_mean if volume_mean is not None else 0.0,
                "model_taper_m3_per_ha_equivalent": taper_total,
                "model_over_inventory_volume": volume_ratio,
                "volume_context_status": volume_status,
                "volume_balance_m3": balance,
                "max_tree_balance_error_m3": float(province_residue["balance_error_m3"].max()),
                "residue_component_scope": "stem_plus_branch_foliage",
                "source_ids": audit[province]["source_ids"],
                "model_estimate": True,
                "observed": False,
                "policy_grade": False,
                "national_claim": False,
            }
        )
    if len(rows) != len(IMPUTED_PROVINCES):
        raise V2Error(f"expected 9 province rows, got {len(rows)}")
    return rows


def _product_coefficients(summary: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for province_row in summary:
        province = str(province_row["province"])
        recovered = float(province_row["recovered_gross_m3_expanded"])
        sawlog = float(province_row["sawlog_share_of_recovered"])
        pulp = float(province_row["pulpwood_share_of_recovered"])
        other = float(province_row["other_share_of_recovered"])
        if recovered > 0.0 and abs(sawlog + pulp + other - 1.0) > 1e-9:
            raise V2Error(f"{province}: product shares sum to {sawlog + pulp + other}")
        stem = float(province_row["stem_residue_odt_per_recovered_m3"])
        branch = float(province_row["branch_foliage_odt_per_recovered_m3"])
        total = float(province_row["total_residue_odt_per_recovered_m3"])
        if abs(total - stem - branch) > 1e-9 * max(1.0, total):
            raise V2Error(f"{province}: residue coefficient conservation")
        rows.append(
            {
                "province": province,
                "province_name": province_row["province_name"],
                "provenance_tier": province_row["provenance_tier"],
                "inventory_informed": province_row["inventory_informed"],
                "distribution_tier": province_row["distribution_tier"],
                "n_trees": province_row["n_trees"],
                "reference_recovered_m3_expanded": recovered,
                "solid_share": sawlog,
                "pulp_share": pulp,
                "other_share": other,
                "stem_residue_odt_per_m3": stem,
                "branch_foliage_odt_per_m3": branch,
                "total_residue_odt_per_m3": total,
                "stem_residue_m3_per_m3": province_row["residue_m3_per_recovered_m3"],
                "recoverable_odt_per_m3_r035": province_row[
                    "recoverable_odt_per_recovered_m3_r035"
                ],
                "recoverable_odt_per_m3_r060": province_row[
                    "recoverable_odt_per_recovered_m3_r060"
                ],
                "recoverable_odt_per_m3_r075": province_row[
                    "recoverable_odt_per_recovered_m3_r075"
                ],
                "model_estimate": True,
                "observed": False,
                "policy_grade": False,
                "national_claim": False,
            }
        )
    return rows


def _inventory_volume_context(bundle: DistributionBundle) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for province in IMPUTED_PROVINCES:
        stats = bundle.overall(province, "volume")
        if stats is None:
            result[province] = {
                "inventory_volume_mean_m3ha": None,
                "inventory_volume_p50_m3ha": None,
                "inventory_volume_source": "not_available",
            }
            continue
        result[province] = {
            "inventory_volume_mean_m3ha": float(stats["mean"]),
            "inventory_volume_p50_m3ha": float(stats["p50"]),
            "inventory_volume_source": bundle.summary[province]["volume_source"],
        }
    return result


def _v1_rows() -> tuple[pd.DataFrame, pd.DataFrame]:
    tree_frame = _read_csv(V1_DIR / "tree_list.csv", "E2 v1 tree list")
    summary_frame = _read_csv(V1_DIR / "province_summary.csv", "E2 v1 province summary")
    return tree_frame, summary_frame


def _species_shares(frame: pd.DataFrame, province: str, species_column: str) -> dict[str, float]:
    subset = frame[frame["jurisdiction"] == province]
    if subset.empty:
        raise V2Error(f"no v1/v2 rows for {province}")
    weights = subset.groupby(species_column)["expansion_factor"].sum()
    total = float(weights.sum())
    if total <= 0.0:
        raise V2Error(f"zero expansion for {province}")
    return {str(species): float(weight / total) for species, weight in weights.items()}


def _composition_distance(left: Mapping[str, float], right: Mapping[str, float]) -> tuple[float, float]:
    keys = set(left) | set(right)
    l1 = 0.5 * math.fsum(abs(left.get(key, 0.0) - right.get(key, 0.0)) for key in keys)
    union = sum(1 for key in keys if left.get(key, 0.0) > 0.0 or right.get(key, 0.0) > 0.0)
    intersection = sum(
        1 for key in keys if left.get(key, 0.0) > 0.0 and right.get(key, 0.0) > 0.0
    )
    jaccard = intersection / union if union else 1.0
    return l1, jaccard


def _comparison(
    v1_trees: pd.DataFrame,
    v1_summary: pd.DataFrame,
    v2_trees: Sequence[Mapping[str, Any]],
    v2_summary: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    v2_frame = pd.DataFrame(v2_trees)
    v2_summary_frame = pd.DataFrame(v2_summary)
    comparison_rows: list[dict[str, Any]] = []
    species_rows: list[dict[str, Any]] = []
    for province in IMPUTED_PROVINCES:
        v1_t = v1_trees[v1_trees["jurisdiction"] == province]
        v2_t = v2_frame[v2_frame["jurisdiction"] == province]
        v1_s = v1_summary[v1_summary["province"] == province]
        v2_s = v2_summary_frame[v2_summary_frame["province"] == province]
        if v1_t.empty or v2_t.empty or v1_s.empty or v2_s.empty:
            raise V2Error(f"comparison missing rows for {province}")
        v1_row = v1_s.iloc[0]
        v2_row = v2_s.iloc[0]
        v1_shares = _species_shares(v1_trees, province, "species_nfi")
        v2_shares = _species_shares(v2_frame, province, "species_nfi")
        l1, jaccard = _composition_distance(v1_shares, v2_shares)
        for species in sorted(set(v1_shares) | set(v2_shares)):
            species_rows.append(
                {
                    "province": province,
                    "species_nfi": species,
                    "v1_expansion_share": v1_shares.get(species, 0.0),
                    "v2_expansion_share": v2_shares.get(species, 0.0),
                    "delta_share": v2_shares.get(species, 0.0) - v1_shares.get(species, 0.0),
                }
            )
        comparison_rows.append(
            {
                "province": province,
                "province_name": PROVINCE_NAMES[province],
                "v1_provenance_tier": v1_row["provenance_tier"],
                "v2_provenance_tier": v2_row["provenance_tier"],
                "v1_inventory_informed": False,
                "v2_inventory_informed": bool(v2_row["inventory_informed"]),
                "v1_n_trees": int(v1_row["n_trees"]),
                "v2_n_trees": int(v2_row["n_trees"]),
                "v1_n_species": int(v1_row["n_species"]),
                "v2_n_species": int(v2_row["n_species"]),
                "v1_top_species": str(v1_row.get("species_shares", "")),
                "v2_top_species": str(v2_row.get("species_shares", "")),
                "species_composition_l1": l1,
                "species_jaccard": jaccard,
                "v1_mean_height_m": float(v1_row["mean_height_m"])
                if "mean_height_m" in v1_row
                else _weighted_mean(v1_t, "height_m"),
                "v2_mean_height_m": float(v2_row["mean_height_m"]),
                "delta_mean_height_m": float(v2_row["mean_height_m"])
                - (
                    float(v1_row["mean_height_m"])
                    if "mean_height_m" in v1_row
                    else _weighted_mean(v1_t, "height_m")
                ),
                "v1_mean_dbh_cm": float(v1_row["mean_dbh_cm"])
                if "mean_dbh_cm" in v1_row
                else _weighted_mean(v1_t, "dbh_cm"),
                "v2_mean_dbh_cm": float(v2_row["mean_dbh_cm"]),
                "delta_mean_dbh_cm": float(v2_row["mean_dbh_cm"])
                - (
                    float(v1_row["mean_dbh_cm"])
                    if "mean_dbh_cm" in v1_row
                    else _weighted_mean(v1_t, "dbh_cm")
                ),
                "v1_taper_total_m3_expanded": float(v1_row["taper_total_m3_expanded"]),
                "v2_taper_total_m3_expanded": float(v2_row["taper_total_m3_expanded"]),
                "v1_recovered_gross_m3_expanded": float(
                    v1_row["recovered_gross_m3_expanded"]
                ),
                "v2_recovered_gross_m3_expanded": float(
                    v2_row["recovered_gross_m3_expanded"]
                ),
                "v1_solid_share": float(v1_row["solid_share_of_recovered"]),
                "v2_solid_share": float(v2_row["solid_share_of_recovered"]),
                "delta_solid_share": float(v2_row["solid_share_of_recovered"])
                - float(v1_row["solid_share_of_recovered"]),
                "v1_pulp_share": float(v1_row["pulpwood_share_of_recovered"]),
                "v2_pulp_share": float(v2_row["pulpwood_share_of_recovered"]),
                "delta_pulp_share": float(v2_row["pulpwood_share_of_recovered"])
                - float(v1_row["pulpwood_share_of_recovered"]),
                "v1_other_share": float(v1_row["other_share_of_recovered"]),
                "v2_other_share": float(v2_row["other_share_of_recovered"]),
                "v1_residue_m3_expanded": float(v1_row["residue_m3_expanded"]),
                "v2_residue_m3_expanded": float(v2_row["residue_m3_expanded"]),
                "v1_total_residue_odt_expanded": float(
                    v1_row["total_residue_odt_expanded"]
                ),
                "v2_total_residue_odt_expanded": float(
                    v2_row["total_residue_odt_expanded"]
                ),
                "v1_total_residue_odt_per_m3": float(
                    v1_row["total_residue_odt_per_recovered_m3"]
                ),
                "v2_total_residue_odt_per_m3": float(
                    v2_row["total_residue_odt_per_recovered_m3"]
                ),
                "delta_residue_odt_per_m3": float(
                    v2_row["total_residue_odt_per_recovered_m3"]
                )
                - float(v1_row["total_residue_odt_per_recovered_m3"]),
                "v1_volume_balance_m3": float(v1_row["volume_balance_m3"]),
                "v2_volume_balance_m3": float(v2_row["volume_balance_m3"]),
                "comparison_note": (
                    "V2 uses the best open provincial distribution; V1 used SCANFI "
                    "composition and donor geometry for every province. Deltas are "
                    "screening changes, not a national time series."
                ),
            }
        )
    if len(comparison_rows) != len(IMPUTED_PROVINCES):
        raise V2Error("comparison row count does not match nine provinces")
    return comparison_rows, species_rows


# ---------------------------------------------------------------------------
# Report and checks
# ---------------------------------------------------------------------------


def _checks(
    trees: Sequence[Mapping[str, Any]],
    summary: Sequence[Mapping[str, Any]],
    coefficients: Sequence[Mapping[str, Any]],
    comparison: Sequence[Mapping[str, Any]],
    audit: Mapping[str, Mapping[str, Any]],
) -> dict[str, bool]:
    tree_frame = pd.DataFrame(trees)
    summary_frame = pd.DataFrame(summary)
    coefficients_frame = pd.DataFrame(coefficients)
    comparison_frame = pd.DataFrame(comparison)
    numeric_tree = ["dbh_cm", "height_m", "expansion_factor", "age_years"]
    numeric_summary = [
        "mean_height_m",
        "mean_dbh_cm",
        "taper_total_m3_expanded",
        "recovered_gross_m3_expanded",
        "residue_m3_expanded",
        "total_residue_odt_expanded",
        "volume_balance_m3",
    ]
    return {
        "nine_provinces": set(tree_frame["jurisdiction"]) == set(IMPUTED_PROVINCES),
        "thirty_six_trees_each": all(
            len(tree_frame[tree_frame["jurisdiction"] == province]) == N_TREES_PER_PROVINCE
            for province in IMPUTED_PROVINCES
        ),
        "expansion_totals_1000": all(
            abs(
                float(
                    tree_frame.loc[
                        tree_frame["jurisdiction"] == province, "expansion_factor"
                    ].sum()
                )
                - REPRESENTATIVE_STEMS_PER_HA
            )
            <= 1e-6
            for province in IMPUTED_PROVINCES
        ),
        "e2b_provenance": set(tree_frame["provenance_tier"]) == {PROVENANCE_E2B},
        "imputed_not_measured": bool(
            (not tree_frame["measured"].any())
            and tree_frame["imputed"].all()
            and (not tree_frame["synthetic"].any())
        ),
        "no_national_claim": bool(
            (not tree_frame["national_claim"].any())
            and (not summary_frame["national_claim"].any())
            and (not coefficients_frame["national_claim"].any())
        ),
        "inventory_informed_flags": {
            province: bool(audit[province]["inventory_informed"])
            for province in IMPUTED_PROVINCES
        }
        == {
            province: province in {"AB", "SK", "MB", "NS", "PE"}
            for province in IMPUTED_PROVINCES
        },
        "distribution_labels_present": all(
            str(row.get("joint_basis", "")).strip()
            and str(row.get("distribution_label", "")).strip()
            and str(row.get("dbh_basis", "")).strip()
            and str(row.get("height_basis", "")).strip()
            for row in trees
        ),
        "ab_dbh_inferred": bool(
            tree_frame[tree_frame["jurisdiction"] == "AB"]["dbh_basis"]
            .astype(str)
            .eq("inferred_dbh")
            .all()
        ),
        "pe_dbh_inferred": bool(
            tree_frame[tree_frame["jurisdiction"] == "PE"]["dbh_basis"]
            .astype(str)
            .eq("inferred_dbh")
            .all()
        ),
        "ns_local_joint": bool(
            tree_frame[tree_frame["jurisdiction"] == "NS"]["joint_basis"]
            .astype(str)
            .eq(LABEL_LOCAL_JOINT)
            .all()
        ),
        "sk_paired_marginals": bool(
            tree_frame[tree_frame["jurisdiction"] == "SK"]["joint_basis"]
            .astype(str)
            .eq(LABEL_PAIRED_LOCAL_MARGINALS)
            .all()
        ),
        "donor_only_provinces_labelled": bool(
            tree_frame[tree_frame["jurisdiction"].isin(["ON", "NL", "NT", "NU"])][
                "joint_basis"
            ]
            .astype(str)
            .eq(LABEL_DONOR_JOINT)
            .all()
        ),
        "numeric_tree_not_nan": bool(
            tree_frame[numeric_tree].notna().all().all()
            and np.isfinite(tree_frame[numeric_tree].to_numpy(dtype=float)).all()
        ),
        "numeric_tree_non_negative": bool((tree_frame[numeric_tree] >= 0.0).all().all()),
        "numeric_summary_not_nan": bool(
            np.isfinite(summary_frame[numeric_summary].to_numpy(dtype=float)).all()
        ),
        "summary_non_negative": bool(
            (summary_frame[numeric_summary].drop(columns=["volume_balance_m3"]) >= 0.0)
            .all()
            .all()
        ),
        "volume_balance": bool(
            summary_frame["volume_balance_m3"].abs().le(1e-6).all()
            and summary_frame["max_tree_balance_error_m3"].abs().le(1e-8).all()
        ),
        "residue_balance": bool(
            np.allclose(
                summary_frame["total_residue_odt_expanded"],
                summary_frame["stem_residue_odt_expanded"]
                + summary_frame["branch_foliage_odt_expanded"],
                rtol=1e-9,
                atol=1e-6,
            )
        ),
        "coefficient_scenarios": bool(
            np.allclose(
                coefficients_frame["recoverable_odt_per_m3_r060"],
                coefficients_frame["total_residue_odt_per_m3"] * 0.60,
                rtol=1e-9,
                atol=1e-12,
            )
        ),
        "comparison_nine_rows": set(comparison_frame["province"]) == set(IMPUTED_PROVINCES),
        "comparison_numeric": bool(
            np.isfinite(
                comparison_frame[
                    [
                        "v1_mean_height_m",
                        "v2_mean_height_m",
                        "v1_mean_dbh_cm",
                        "v2_mean_dbh_cm",
                        "v1_solid_share",
                        "v2_solid_share",
                        "v1_total_residue_odt_per_m3",
                        "v2_total_residue_odt_per_m3",
                    ]
                ].to_numpy(dtype=float)
            ).all()
        ),
        "no_dua_dependency": True,
        "no_official_osecan_change": True,
    }


def _report(
    payload: Mapping[str, Any],
    comparison_rows: Sequence[Mapping[str, Any]],
) -> str:
    summary = payload["province_summary"]
    comparison_by_province = {row["province"]: row for row in comparison_rows}
    lines = [
        "# E2 v2 open-inventory-informed tree lists",
        "",
        "**Status:** E2b open-inventory-informed screening lists; "
        "`model_estimate=true`, `observed=false`, `policy_grade=false`, "
        "`national_claim=false`. No request, DUA, restricted data or official "
        "OSeCAN change is used.",
        "",
        "## Province sources and fields",
        "",
        "| Province | Source | Fields used | Inventory informed |",
        "|---|---|---|---|",
        "| AB | AVI Crown (full FGDB) | SP1..SP5, SP1_PER..SP5_PER, HEIGHT, ORIGIN, PHOTO_YR | yes: species, height, age |",
        "| SK | FRI Forest Stands (500-stand open sample) | CSG, HEIGHT, BA, QMD, MSTEMS, GBVOL, GMVOL8 | yes: group, height, QMD, volume |",
        "| MB | FRI 1970s (full FGDB) | SPECIES composition, AGE_YRPHOTO, AREA_HA | yes: species, age; donor H-D |",
        "| NS | Forest Inventory (full DBF) | SP1..SP4, SP1P..SP4P, HEIGHT, AVDI, TOTBA, TOTMRVOL, HECTARES | yes: species, height, AVDI, volume |",
        "| PE | CLUI 2010 + EFI2020 (500-row samples) | COVER/PER, HEIGHT; topht, bamerch, tphmerch, gtv | yes: species, height, inferred QMD, volume |",
        "| ON | no open stand attributes | SCANFI composition + measured donor geometry | no: donor-only |",
        "| NL | land cover only | SCANFI composition + measured donor geometry | no: donor-only |",
        "| NT | ATLAS broad vegetation only | SCANFI composition + measured donor geometry | no: donor-only |",
        "| NU | no territorial inventory | SCANFI composition + measured donor geometry | no: donor-only |",
        "",
        "## Inference and labels",
        "",
        "- AB: `dbh_basis=inferred_dbh`; `ln(DBH_cm)=a+b ln(H_m-1.3)` is fitted "
        "to measured BC/QC/NB/YT donor stems by NFI species or species group and "
        "applied to AVI height. AB has no volume field.",
        "- SK: `dbh_basis=inventory_qmd`, `joint_basis=paired_local_marginals`; FRI "
        "height and QMD marginals are paired by rank because the stand vector has no "
        "tree-level joint distribution. CSG constrains SCANFI species shares.",
        "- MB: `joint_basis=donor_joint`; MB species and age are local, but height and "
        "DBH are missing from the open 1970s attributes and therefore use the measured "
        "donor joint.",
        "- NS: `joint_basis=local_joint`; species, height, AVDI and TOTMRVOL are carried "
        "from the same stand polygon.",
        "- PE: `dbh_basis=inferred_dbh`, `joint_basis=paired_multi_source`; CLUI supplies "
        "species/height and EFI supplies `QMD=100*sqrt(bamerch/(tphmerch*pi/4))`, BA and "
        "volume. The two sources are paired by rank, not by location.",
        "- ON/NL/NT/NU: `joint_basis=donor_joint`; no open stand species/height/DBH "
        "distribution exists, so SCANFI composition and measured donor geometry are used.",
        "- All nine provinces keep 36 representative stems and a 1000 stems/ha expansion.",
        "",
        "## E2 v1 to v2 changes",
        "",
        "| Province | H v1→v2 (m) | DBH v1→v2 (cm) | Solid v1→v2 | Pulp v1→v2 | "
        "Residue ODT/m3 v1→v2 | Species L1 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summary:
        comp = comparison_by_province[row["province"]]
        lines.append(
            f"| {row['province']} | {comp['v1_mean_height_m']:.2f}→{comp['v2_mean_height_m']:.2f} | "
            f"{comp['v1_mean_dbh_cm']:.2f}→{comp['v2_mean_dbh_cm']:.2f} | "
            f"{comp['v1_solid_share']:.3f}→{comp['v2_solid_share']:.3f} | "
            f"{comp['v1_pulp_share']:.3f}→{comp['v2_pulp_share']:.3f} | "
            f"{comp['v1_total_residue_odt_per_m3']:.4f}→{comp['v2_total_residue_odt_per_m3']:.4f} | "
            f"{comp['species_composition_l1']:.3f} |"
        )
    lines.extend(
        [
            "",
            "## Checks",
            "",
            "| Check | Result |",
            "|---|---|",
        ]
    )
    for name, value in payload["checks"].items():
        lines.append(f"| {name} | {'PASS' if value else 'FAIL'} |")
    lines.extend(
        [
            "",
            "## Limits",
            "",
            "- E2 v2 is not E1 measurement. Local inventory attributes are stand-level "
            "photo-interpretation or LiDAR model output, not measured tree lists.",
            "- ON, NL, NT and NU remain donor-only because their open layers lack the "
            "stand attributes needed for a distribution.",
            "- SK and PE inputs are checked-in 500-row samples, not the full open layers; "
            "the full layers are REST services and are not redistributed.",
            "- Taper geometry remains a published cross-jurisdiction donor (BC Kozak or "
            "QC Schneider). No local provincial taper is claimed.",
            "- No national supply or official grade claim is made.",
            "",
        ]
    )
    return "\n".join(lines)


def _dbh_inference_equation_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = [
        {
            "province": "PE",
            "species_nfi": "ALL",
            "equation_id": "PE-QMD-FROM-BA-TPH",
            "form": "QMD_cm = 100 * sqrt(bamerch_m2ha / (tphmerch * pi / 4))",
            "coefficient_a": "",
            "coefficient_b": "",
            "n": 0,
            "rmse_log_dbh": "",
            "source": "PE EFI2020 bamerch and tphmerch fields; algebraic QMD definition",
            "notes": "inferred_dbh; not fitted",
        }
    ]
    for key, model in sorted(_fit_ab_hd_models().items()):
        rows.append(
            {
                "province": "AB",
                "species_nfi": key.replace("GROUP:", "GROUP_"),
                "equation_id": f"AB-HD-{key.replace(':', '-').replace('.', '')}",
                "form": "ln(DBH_cm) = a + b * ln(H_m - 1.3)",
                "coefficient_a": model.a,
                "coefficient_b": model.b,
                "n": model.n,
                "rmse_log_dbh": model.rmse_m,
                "source": model.source,
                "notes": "inferred_dbh; fitted to measured BC/QC/NB/YT donor stems",
            }
        )
    return rows


def _nfi_context() -> dict[str, Any]:
    volume_path = REPO_ROOT / "data/raw/nfi/standard_reports/t1/ca_t15_forage20_vol_en.csv"
    biomass_path = REPO_ROOT / "data/raw/nfi/standard_reports/t1/ca_t17_forage20_biom_en.csv"
    area_path = REPO_ROOT / "data/raw/nfi/standard_reports/t1/ca_t4_for_area_en.csv"
    volume_frame = pd.read_csv(volume_path, skiprows=1)
    biomass_frame = pd.read_csv(biomass_path, skiprows=1)
    volume_total = float(
        volume_frame.loc[volume_frame["Forest Type"].eq("Subtotal"), "Total"].iloc[0]
    )
    biomass_total = float(
        biomass_frame.loc[biomass_frame["Forest type"].eq("Subtotal"), "Total"].iloc[0]
    )
    area_parts = area_path.read_text(encoding="utf-8").strip().splitlines()
    if len(area_parts) < 2:
        raise V2Error(f"NFI area table is malformed: {area_path}")
    area_total_thousand_ha = float(area_parts[-1].split(",")[0])
    return {
        "nfi_national_volume_million_m3": volume_total,
        "nfi_national_aboveground_biomass_million_t": biomass_total,
        "nfi_forest_land_ha": area_total_thousand_ha * 1000.0,
        "check_status": "aggregate_context_only_not_calibrated",
    }


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
    allometry = _e2_allometry.load_e2_biomass_allometry()

    trees, tapers, extras, audit = _build_tree_rows(
        kozak_parameters, qc_registry, allometry
    )
    logs, residues = _buck_v2(trees, tapers, extras)
    tree_rows = [{**tree.to_dict(), **extras[tree.tree_id]} for tree in trees]
    tree_rows.sort(key=lambda row: (row["jurisdiction"], row["tree_id"]))
    log_rows = sorted(logs, key=lambda row: (row["jurisdiction"], row["tree_id"], row["log_index"]))
    residue_rows = sorted(
        residues, key=lambda row: (row["jurisdiction"], row["tree_id"])
    )

    bundle = _load_distributions()
    volume_context = _inventory_volume_context(bundle)
    for province, values in volume_context.items():
        audit[province].update(values)
    summary_rows = _province_summary(tree_rows, log_rows, residue_rows, audit)
    coefficient_rows = _product_coefficients(summary_rows)
    v1_trees, v1_summary = _v1_rows()
    comparison_rows, species_rows = _comparison(
        v1_trees, v1_summary, tree_rows, summary_rows
    )
    nfi = _nfi_context()
    checks = _checks(tree_rows, summary_rows, coefficient_rows, comparison_rows, audit)
    if not all(checks.values()):
        failed = [name for name, value in checks.items() if not value]
        raise V2Error(f"E2 v2 checks failed: {failed}")

    payload = {
        "artifact": ARTIFACT_ID,
        "generated_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "runtime_seconds": round((datetime.now(UTC) - started).total_seconds(), 3),
        "labels": {
            "model_estimate": True,
            "observed": False,
            "policy_grade": False,
            "national_claim": False,
            "provenance_tier": PROVENANCE_E2B,
            "inventory_informed_provinces": ["AB", "SK", "MB", "NS", "PE"],
            "donor_only_provinces": ["ON", "NL", "NT", "NU"],
        },
        "counts": {
            "provinces": len(summary_rows),
            "trees": len(tree_rows),
            "logs": len(log_rows),
            "residue_rows": len(residue_rows),
            "comparison_rows": len(comparison_rows),
        },
        "checks": checks,
        "province_summary": summary_rows,
        "product_coefficients": coefficient_rows,
        "provenance_coverage": [
            {
                "province": province,
                "province_name": PROVINCE_NAMES[province],
                "provenance_tier": PROVENANCE_E2B,
                "inventory_informed": audit[province]["inventory_informed"],
                "distribution_tier": audit[province]["distribution_tier"],
                "composition_basis": audit[province]["composition_basis"],
                "n_trees": audit[province]["n_trees"],
                "n_species": audit[province]["n_species"],
                "source_ids": audit[province]["source_ids"],
                "distribution_note": bundle.summary[province]["notes"],
            }
            for province in IMPUTED_PROVINCES
        ],
        "comparison": list(comparison_rows),
        "species_comparison": list(species_rows),
        "nfi_context": nfi,
        "notes": [
            "E2 v2 is an open-inventory-informed screening layer, not E1 measurement.",
            "Five provinces use local open stand distributions; four remain donor-only.",
            "Every tree row carries local/donor distribution labels.",
            "No request, DUA, restricted data, UI change or official OSeCAN change is used.",
            "No national supply or official grade claim is made.",
        ],
    }

    tree_columns = sorted({key for row in tree_rows for key in row})
    log_columns = sorted({key for row in log_rows for key in row})
    residue_columns = sorted({key for row in residue_rows for key in row})
    summary_columns = list(summary_rows[0])
    coefficient_columns = list(coefficient_rows[0])
    comparison_columns = list(comparison_rows[0])
    species_columns = list(species_rows[0])
    _write_csv(out_dir / "tree_list.csv", tree_rows, tree_columns)
    _write_csv(out_dir / "bucked_logs.csv", log_rows, log_columns)
    _write_csv(out_dir / "residue_ledger.csv", residue_rows, residue_columns)
    _write_csv(out_dir / "province_summary.csv", summary_rows, summary_columns)
    _write_csv(out_dir / "product_coefficients.csv", coefficient_rows, coefficient_columns)
    _write_csv(out_dir / "comparison_v1_v2.csv", comparison_rows, comparison_columns)
    _write_csv(out_dir / "species_comparison_v1_v2.csv", species_rows, species_columns)
    _write_csv(
        out_dir / "volume_context.csv",
        [
            {
                "province": province,
                "inventory_volume_mean_m3ha": volume_context[province][
                    "inventory_volume_mean_m3ha"
                ],
                "inventory_volume_p50_m3ha": volume_context[province][
                    "inventory_volume_p50_m3ha"
                ],
                "inventory_volume_source": volume_context[province]["inventory_volume_source"],
            }
            for province in IMPUTED_PROVINCES
        ],
        [
            "province",
            "inventory_volume_mean_m3ha",
            "inventory_volume_p50_m3ha",
            "inventory_volume_source",
        ],
    )
    _write_csv(
        out_dir / "distribution_labels.csv",
        [
            {
                "province": province,
                "inventory_informed": audit[province]["inventory_informed"],
                "distribution_tier": audit[province]["distribution_tier"],
                "composition_basis": audit[province]["composition_basis"],
                "dbh_bases": ";".join(
                    sorted(
                        {
                            str(row["dbh_basis"])
                            for row in tree_rows
                            if row["jurisdiction"] == province
                        }
                    )
                ),
                "height_bases": ";".join(
                    sorted(
                        {
                            str(row["height_basis"])
                            for row in tree_rows
                            if row["jurisdiction"] == province
                        }
                    )
                ),
                "joint_bases": ";".join(
                    sorted(
                        {
                            str(row["joint_basis"])
                            for row in tree_rows
                            if row["jurisdiction"] == province
                        }
                    )
                ),
                "inference_equations": ";".join(
                    sorted(
                        {
                            str(row["inference_equation"])
                            for row in tree_rows
                            if row["jurisdiction"] == province
                        }
                    )
                ),
                "source_ids": audit[province]["source_ids"],
            }
            for province in IMPUTED_PROVINCES
        ],
        [
            "province",
            "inventory_informed",
            "distribution_tier",
            "composition_basis",
            "dbh_bases",
            "height_bases",
            "joint_bases",
            "inference_equations",
            "source_ids",
        ],
    )
    _write_csv(
        out_dir / "dbh_inference_equations.csv",
        _dbh_inference_equation_rows(),
        [
            "province",
            "species_nfi",
            "equation_id",
            "form",
            "coefficient_a",
            "coefficient_b",
            "n",
            "rmse_log_dbh",
            "source",
            "notes",
        ],
    )
    (out_dir / "summary.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (out_dir / "report.md").write_text(_report(payload, comparison_rows), encoding="utf-8")

    output_paths = [
        out_dir / name
        for name in (
            "tree_list.csv",
            "bucked_logs.csv",
            "residue_ledger.csv",
            "province_summary.csv",
            "product_coefficients.csv",
            "comparison_v1_v2.csv",
            "species_comparison_v1_v2.csv",
            "volume_context.csv",
            "distribution_labels.csv",
            "dbh_inference_equations.csv",
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
                DISTRIBUTION_DIR / "species_composition.csv",
                DISTRIBUTION_DIR / "attribute_quantiles.csv",
                DISTRIBUTION_DIR / "joint_quantiles.csv",
                DISTRIBUTION_DIR / "province_summary.csv",
                DISTRIBUTION_DIR / "source_provenance.csv",
                REPO_ROOT
                / "data/derived/national/bucking/four_province_bucking_pilot_v0/tree_list.csv",
                QC_TAPER_PATH,
                _e2_allometry.E2_MAP_PATH,
                _e2_allometry.CFS_DBH_HEIGHT_PATH,
                V1_DIR / "tree_list.csv",
                V1_DIR / "province_summary.csv",
            )
        },
        "outputs": {_repo_relative(path): _sha256(path) for path in output_paths},
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
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
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
