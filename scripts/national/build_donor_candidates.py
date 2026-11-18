#!/usr/bin/env python3
"""Inventory donor candidates for national batch cells with weak or missing plots.

The national assortment batch has 36 ecoprovince x species cells with a
placeholder area weight of 1.0 ha.  This script joins the new SCANFI area
weights, flags zero/unmapped cells, and ranks donor ecoprovinces/ecodistricts
using ecozone similarity, geographic proximity, and plot support from the
national coverage table.  MGM curves are deliberately not joined yet; every
candidate row carries ``mgm_join_pending=true``.
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_AREA_DIR = REPO_ROOT / "data/derived/national/assortments/national_area_weights"
DEFAULT_BATCH = REPO_ROOT / "data/derived/national/assortments/national_batch/national_stands.csv"
DEFAULT_COVERAGE = REPO_ROOT / "data/derived/national/assortments/national_coverage/coverage.csv"
DEFAULT_RSYC = REPO_ROOT / "data/raw/national/rsyc/RSYC_ecostrat.gpkg"
DEFAULT_BOUNDARIES = REPO_ROOT / "data/raw/boundaries/canada_adm1_geoboundaries_simplified.geojson"
DEFAULT_OUT = DEFAULT_AREA_DIR

TARGET_PROVINCES = {"AB", "ON", "NS", "NU", "MB"}
PROVINCE_ADJACENCY = {
    "AB": {"BC", "SK", "NT"},
    "BC": {"AB", "YT", "NT"},
    "MB": {"SK", "ON", "NU", "NT"},
    "NB": {"NS", "PE", "QC"},
    "NL": {"QC", "NS"},
    "NS": {"NB", "PE"},
    "NT": {"YT", "BC", "AB", "SK", "MB", "NU"},
    "NU": {"NT", "MB", "ON", "QC", "NL"},
    "ON": {"MB", "QC", "NU"},
    "PE": {"NB", "NS"},
    "QC": {"ON", "NB", "NL", "NU"},
    "SK": {"AB", "MB", "NT"},
    "YT": {"BC", "NT"},
}
SCANFI_TARGET_SPECIES = {
    "PICE.MAR",
    "ABIE.BAL",
    "PINU.BAN",
    "PINU.CON",
    "PINU.PON",
    "PSEU.MEN",
    "LARI.LAR",
    "PINU.STR",
    "PINU.RES",
    "BROADLEAF",
    "PICE.GLA",
    "CONIFEROUS",
}


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _relative_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def split_provinces(value: Any) -> list[str]:
    if value is None or pd.isna(value):
        return []
    return [part.strip() for part in str(value).split(";") if part.strip()]


def load_area_weights(path: Path) -> tuple[pd.DataFrame, dict[str, float]]:
    frame = pd.read_csv(path)
    if frame.empty:
        raise ValueError(f"empty area weights: {path}")
    if frame["area_ha"].lt(0).any():
        raise ValueError("negative area in area weights")
    ecoprovince_forest = (
        frame.drop_duplicates("ecoprovince")[["ecoprovince", "forest_area_ha"]]
        .set_index("ecoprovince")["forest_area_ha"]
        .to_dict()
    )
    return frame, ecoprovince_forest


def build_area_lookup(area: pd.DataFrame) -> dict[tuple[str, str], float]:
    grouped = area.groupby(["ecoprovince", "rsyc_species"], dropna=False)["area_ha"].sum()
    return {(str(ecoprovince), str(species)): float(value) for (ecoprovince, species), value in grouped.items()}


def build_primary_province_map(district_area: pd.DataFrame) -> dict[str, str]:
    district_rows = district_area[district_area["level"] == "ecodistrict"].copy()
    district_rows = district_rows[district_rows["province"].astype(str) != "UNASSIGNED"]
    weights = (
        district_rows.groupby(["ecoprovince", "province"])["forest_area_ha"]
        .sum()
        .reset_index()
        .sort_values(["ecoprovince", "forest_area_ha"], ascending=[True, False])
    )
    return weights.drop_duplicates("ecoprovince").set_index("ecoprovince")["province"].to_dict()


def build_province_map(
    coverage: pd.DataFrame,
    district_area: pd.DataFrame,
) -> dict[str, list[str]]:
    """Province overlap for each ecoprovince.

    The coverage table only lists provinces that contain plots; Alberta, for
    example, is absent from several boreal ecoprovinces because it has no plots.
    Spatial area weights are therefore the primary source, with coverage as a
    fallback.
    """
    mapping: dict[str, set[str]] = {}
    district_rows = district_area[district_area["level"] == "ecodistrict"]
    for _, row in district_rows.iterrows():
        ecoprovince = str(row["ecoprovince"])
        province = str(row["province"]).strip()
        if province and province != "UNASSIGNED":
            mapping.setdefault(ecoprovince, set()).add(province)

    rows = coverage[coverage["level"] == "ecoprovince"]
    for _, row in rows.iterrows():
        ecoprovince = str(row["ecoprovince"])
        mapping.setdefault(ecoprovince, set()).update(split_provinces(row.get("provinces")))
        primary = row.get("province_primary")
        if isinstance(primary, str) and primary.strip():
            mapping[ecoprovince].add(primary.strip())
    return {key: sorted(value) for key, value in mapping.items()}


def load_geometry(rsyc_path: Path) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    ecoprovinces = gpd.read_file(rsyc_path, layer="ecoprovinces").to_crs(3978)
    ecodistricts = gpd.read_file(rsyc_path, layer="ecodistricts").to_crs(3978)
    for column in ("ecoprovince", "ecodistrict"):
        frame = ecoprovinces if column == "ecoprovince" else ecodistricts
        if frame[column].isna().any():
            raise ValueError(f"missing {column} names in RSYC layer")
    return ecoprovinces, ecodistricts


def centroid_lookup(frame: gpd.GeoDataFrame, column: str) -> dict[str, tuple[float, float, float]]:
    lookup = {}
    for _, row in frame.iterrows():
        centroid = row.geometry.centroid
        lookup[str(row[column])] = (float(centroid.x), float(centroid.y), float(row.geometry.area / 1e6))
    return lookup


def distance_km(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1]) / 1000.0


def province_compatibility(target_provinces: list[str], candidate_provinces: list[str]) -> tuple[float, str]:
    if not target_provinces or not candidate_provinces:
        return 0.0, "province overlap unknown"
    if set(target_provinces) & set(candidate_provinces):
        return 0.10, "shared province"
    adjacent = any(
        candidate in PROVINCE_ADJACENCY.get(target, set())
        for target in target_provinces
        for candidate in candidate_provinces
    )
    if adjacent:
        return 0.05, "adjacent province"
    return -0.15, "non-adjacent province"


def score_candidate(
    target_ecozone: str,
    target_centroid: tuple[float, float, float],
    candidate_ecoprovince: str,
    candidate_ecozone: str,
    candidate_centroid: tuple[float, float, float],
    candidate_species: str,
    target_species: str,
    target_group: str,
    candidate_group: str,
    coverage_class: str,
    n_sites: int,
    donor_area_ha: float,
    province_bonus: float,
) -> tuple[float, str]:
    same_ecozone = target_ecozone == candidate_ecozone
    distance = distance_km(target_centroid, candidate_centroid)
    exact = candidate_species == target_species
    group_match = candidate_group == target_group

    score = 0.30 if same_ecozone else 0.0
    score += 0.20 * math.exp(-distance / 800.0)
    if exact and coverage_class == "direct_fit":
        score += 0.25
    elif exact:
        score += 0.15
    elif group_match:
        score += 0.05
    score += 0.15 * min(1.0, math.log10(1.0 + max(n_sites, 0)) / 4.0)
    score += 0.05 if group_match else 0.0
    score += 0.05 if donor_area_ha > 0 else 0.0
    score += province_bonus
    score = min(1.0, max(0.0, score))

    if score >= 0.75 and exact and coverage_class == "direct_fit" and n_sites >= 20:
        confidence = "high"
    elif score >= 0.55:
        confidence = "medium"
    else:
        confidence = "low"
    return score, confidence


def rationale(
    same_ecozone: bool,
    distance: float,
    candidate_species: str,
    target_species: str,
    coverage_class: str,
    n_sites: int,
    donor_area_ha: float,
) -> str:
    parts = []
    parts.append("same ecozone" if same_ecozone else "different ecozone")
    parts.append(f"{distance:.0f} km centroid distance")
    if candidate_species == target_species:
        parts.append(f"exact species {coverage_class} with {n_sites:,} sites")
    else:
        parts.append(f"proxy {candidate_species} with {n_sites:,} sites")
    if donor_area_ha > 0:
        parts.append(f"donor SCANFI area {donor_area_ha:,.0f} ha")
    else:
        parts.append("donor has no SCANFI area for this target")
    return "; ".join(parts)


def candidate_rows_for_cell(
    target: pd.Series,
    coverage_ecoprovince: pd.DataFrame,
    area_lookup: dict[tuple[str, str], float],
    ecoprovince_centroids: dict[str, tuple[float, float, float]],
    province_map: dict[str, list[str]],
    primary_province_map: dict[str, str],
    top_n: int = 3,
) -> list[dict[str, Any]]:
    target_ecoprovince = str(target["ecoprovince"])
    target_species = str(target["species"])
    target_group = str(target["species_group"])
    target_ecozone = str(target["ecozone"])
    target_centroid = ecoprovince_centroids[target_ecoprovince]
    target_area = area_lookup.get((target_ecoprovince, target_species), 0.0)

    eligible = coverage_ecoprovince[
        (coverage_ecoprovince["ecoprovince"] != target_ecoprovince)
        & (coverage_ecoprovince["n_sites"] > 0)
    ].copy()
    eligible["ecozone_match"] = eligible["ecozone"] == target_ecozone
    eligible["distance_km"] = eligible["ecoprovince"].map(
        lambda name: distance_km(target_centroid, ecoprovince_centroids[str(name)])
    )
    exact = eligible["species"] == target_species
    group = eligible["species"] == target_group

    tiers = [
        (eligible[exact & eligible["ecozone_match"]], "exact_species_same_ecozone"),
        (eligible[exact & (eligible["distance_km"] <= 1000)], "exact_species_within_1000km"),
        (eligible[group & eligible["ecozone_match"]], "species_group_same_ecozone"),
        (eligible[group & (eligible["distance_km"] <= 1000)], "species_group_within_1000km"),
        (eligible[eligible["ecozone_match"]], "general_plot_support_same_ecozone"),
        (eligible[exact], "exact_species_national"),
        (eligible[group], "species_group_national"),
    ]
    pool = pd.DataFrame()
    support_kind = "general_plot_support_national"
    for tier_pool, tier_name in tiers:
        if not tier_pool.empty:
            pool = tier_pool
            support_kind = tier_name
            break
    if pool.empty:
        pool = eligible.sort_values("n_sites", ascending=False).head(12)

    rows: list[dict[str, Any]] = []
    for _, candidate in pool.iterrows():
        candidate_ecoprovince = str(candidate["ecoprovince"])
        candidate_species = str(candidate["species"])
        candidate_area = area_lookup.get((candidate_ecoprovince, target_species), 0.0)
        target_provinces = province_map.get(target_ecoprovince, [])
        candidate_provinces = province_map.get(candidate_ecoprovince, [])
        target_primary = primary_province_map.get(target_ecoprovince)
        candidate_primary = primary_province_map.get(candidate_ecoprovince)
        province_bonus, province_relation = province_compatibility(
            [target_primary] if target_primary else target_provinces,
            [candidate_primary] if candidate_primary else candidate_provinces,
        )
        score, confidence = score_candidate(
            target_ecozone=target_ecozone,
            target_centroid=target_centroid,
            candidate_ecoprovince=candidate_ecoprovince,
            candidate_ecozone=str(candidate["ecozone"]),
            candidate_centroid=ecoprovince_centroids[candidate_ecoprovince],
            candidate_species=candidate_species,
            target_species=target_species,
            target_group=target_group,
            candidate_group=str(candidate["species_group"]),
            coverage_class=str(candidate["coverage_class"]),
            n_sites=int(candidate["n_sites"]),
            donor_area_ha=candidate_area,
            province_bonus=province_bonus,
        )
        rows.append(
            {
                "target_type": "national_batch_cell",
                "target_level": "ecoprovince",
                "target_id": str(target["cell_key"]),
                "target_ecozone": target_ecozone,
                "target_ecoprovince": target_ecoprovince,
                "target_ecodistrict": "",
                "target_species": target_species,
                "target_species_group": target_group,
                "target_coverage_class": str(target["coverage_class"]),
                "target_provinces": ";".join(province_map.get(target_ecoprovince, [])),
                "target_area_ha": target_area,
                "target_forest_area_ha": target["scanfi_forest_area_ha"],
                "target_area_match_status": target["area_match_status"],
                "candidate_type": "ecoprovince",
                "candidate_id": candidate_ecoprovince,
                "candidate_ecozone": str(candidate["ecozone"]),
                "candidate_ecoprovince": candidate_ecoprovince,
                "candidate_ecodistrict": "",
                "candidate_species": candidate_species,
                "candidate_species_group": str(candidate["species_group"]),
                "candidate_coverage_class": str(candidate["coverage_class"]),
                "candidate_n_sites": int(candidate["n_sites"]),
                "candidate_n_plots": int(candidate["n_plots"]),
                "candidate_provinces": ";".join(candidate_provinces),
                "support_kind": support_kind,
                "province_relation": province_relation,
                "similarity_score": round(score, 4),
                "rank": 0,
                "rationale": (
                    f"{province_relation}; "
                    + rationale(
                        same_ecozone=target_ecozone == str(candidate["ecozone"]),
                        distance=distance_km(target_centroid, ecoprovince_centroids[candidate_ecoprovince]),
                        candidate_species=candidate_species,
                        target_species=target_species,
                        coverage_class=str(candidate["coverage_class"]),
                        n_sites=int(candidate["n_sites"]),
                        donor_area_ha=candidate_area,
                    )
                ),
                "confidence": confidence,
                "mgm_join_pending": True,
            }
        )
    rows.sort(key=lambda row: (-row["similarity_score"], row["candidate_ecoprovince"]))
    for rank, row in enumerate(rows[:top_n], start=1):
        row["rank"] = rank
    return rows[:top_n]


def no_plot_ecodistrict_rows(
    support: pd.DataFrame,
    ecodistrict_centroids: dict[str, tuple[float, float, float]],
    top_n: int = 3,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    donors = support[support["n_sites_max"] > 0]
    for _, target in support[support["n_sites_max"] == 0].iterrows():
        target_district = str(target["ecodistrict"])
        target_ecoprovince = str(target["ecoprovince"])
        target_ecozone = str(target["ecozone"])
        target_centroid = ecodistrict_centroids[target_district]
        candidates = donors[donors["ecodistrict"] != target_district].copy()
        candidates["same_ecoprovince"] = candidates["ecoprovince"] == target_ecoprovince
        candidates["same_ecozone"] = candidates["ecozone"] == target_ecozone
        candidates["distance_km"] = candidates["ecodistrict"].map(
            lambda name, centroid=target_centroid: distance_km(centroid, ecodistrict_centroids[str(name)])
        )
        candidates["score"] = (
            0.35 * candidates["same_ecoprovince"].astype(float)
            + 0.25 * candidates["same_ecozone"].astype(float)
            + 0.20 * np.exp(-candidates["distance_km"] / 500.0)
            + 0.15 * np.minimum(1.0, np.log10(1.0 + candidates["n_sites_max"]) / 4.0)
            + 0.05 * np.minimum(1.0, candidates["n_species_supported"] / 10.0)
        )
        candidates = candidates.sort_values(["score", "distance_km"], ascending=[False, True]).head(top_n)
        for rank, (_, candidate) in enumerate(candidates.iterrows(), start=1):
            score = float(candidate["score"])
            confidence = "high" if score >= 0.75 else "medium" if score >= 0.55 else "low"
            rows.append(
                {
                    "target_type": "no_plot_ecodistrict",
                    "target_level": "ecodistrict",
                    "target_id": target_district,
                    "target_ecozone": target_ecozone,
                    "target_ecoprovince": target_ecoprovince,
                    "target_ecodistrict": target_district,
                    "target_species": "ALL",
                    "target_species_group": "ALL",
                    "target_coverage_class": "no_plot_ecodistrict",
                    "target_provinces": str(target.get("provinces", "")),
                    "target_area_ha": 0.0,
                    "target_forest_area_ha": float(target.get("forest_area_ha", 0.0)),
                    "target_area_match_status": "not_applicable",
                    "candidate_type": "ecodistrict",
                    "candidate_id": str(candidate["ecodistrict"]),
                    "candidate_ecozone": str(candidate["ecozone"]),
                    "candidate_ecoprovince": str(candidate["ecoprovince"]),
                    "candidate_ecodistrict": str(candidate["ecodistrict"]),
                    "candidate_species": "ALL",
                    "candidate_species_group": "ALL",
                    "candidate_coverage_class": "plot_supported",
                    "candidate_n_sites": int(candidate["n_sites_max"]),
                    "candidate_n_plots": int(candidate["n_plots_max"]),
                    "candidate_provinces": str(candidate.get("provinces", "")),
                    "support_kind": "ecodistrict_plot_support",
                    "province_relation": "same ecoprovince" if candidate["same_ecoprovince"] else "same ecozone" if candidate["same_ecozone"] else "different ecozone",
                    "similarity_score": round(score, 4),
                    "rank": rank,
                    "rationale": (
                        f"{'same ecoprovince' if candidate['same_ecoprovince'] else 'same ecozone' if candidate['same_ecozone'] else 'different ecozone'}; "
                        f"{candidate['distance_km']:.0f} km; {int(candidate['n_sites_max']):,} sites; "
                        f"{int(candidate['n_species_supported'])} species supported"
                    ),
                    "confidence": confidence,
                    "mgm_join_pending": True,
                }
            )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--area-dir", type=Path, default=DEFAULT_AREA_DIR)
    parser.add_argument("--batch", type=Path, default=DEFAULT_BATCH)
    parser.add_argument("--coverage", type=Path, default=DEFAULT_COVERAGE)
    parser.add_argument("--rsyc", type=Path, default=DEFAULT_RSYC)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    area, ecoprovince_forest = load_area_weights(args.area_dir / "national_area_weights.csv")
    area_lookup = build_area_lookup(area)
    district_area = pd.read_csv(args.area_dir / "national_area_weights_ecodistrict.csv", low_memory=False)
    district_forest = (
        district_area[district_area["level"] == "ecodistrict"]
        .groupby("ecodistrict")["forest_area_ha"]
        .sum()
        .to_dict()
    )
    batch = pd.read_csv(args.batch, low_memory=False)
    coverage = pd.read_csv(args.coverage, low_memory=False)
    if "level" not in coverage.columns:
        raise ValueError("coverage table is missing level column")
    coverage_ecoprovince = coverage[coverage["level"] == "ecoprovince"].copy()
    coverage_ecodistrict = coverage[coverage["level"] == "ecodistrict"].copy()
    for frame in (coverage_ecoprovince, coverage_ecodistrict):
        frame["n_sites"] = pd.to_numeric(frame["n_sites"], errors="coerce").fillna(0).astype(int)
        frame["n_plots"] = pd.to_numeric(frame["n_plots"], errors="coerce").fillna(0).astype(int)
    if coverage_ecoprovince.empty or coverage_ecodistrict.empty:
        raise ValueError("coverage table is missing ecoprovince or ecodistrict rows")

    province_map = build_province_map(coverage, district_area)
    primary_province_map = build_primary_province_map(district_area)
    ecoprovinces, ecodistricts = load_geometry(args.rsyc)
    ecoprovince_centroids = centroid_lookup(ecoprovinces, "ecoprovince")
    ecodistrict_centroids = centroid_lookup(ecodistricts, "ecodistrict")

    # Join SCANFI area to every batch cell and classify the match.
    batch["scanfi_area_ha"] = [
        area_lookup.get((str(row["ecoprovince"]), str(row["species"])), 0.0) for _, row in batch.iterrows()
    ]
    batch["scanfi_forest_area_ha"] = batch["ecoprovince"].astype(str).map(ecoprovince_forest).fillna(0.0)
    batch["scanfi_target_species"] = batch["species"].astype(str).isin(SCANFI_TARGET_SPECIES)
    batch["area_match_status"] = np.select(
        [
            ~batch["scanfi_target_species"],
            batch["scanfi_area_ha"] > 0,
            batch["scanfi_area_ha"].eq(0),
        ],
        ["unmapped_species", "mapped_positive", "mapped_zero"],
        default="mapped_zero",
    )
    batch["area_weight_status"] = np.select(
        [
            batch["area_match_status"].eq("mapped_positive"),
            batch["area_match_status"].eq("unmapped_species"),
        ],
        ["available", "not_available"],
        default="zero",
    )
    # A mapped species with zero SCANFI area must not receive supply.  An
    # unmapped RSYC species is not zero forest area; it simply has no separate
    # SCANFI layer, so it stays eligible for plot-based supply but is flagged.
    batch["supply_eligible"] = batch["scanfi_forest_area_ha"].gt(0) & ~batch["area_match_status"].eq("mapped_zero")
    batch["area_fraction_of_forest"] = np.where(
        batch["scanfi_forest_area_ha"] > 0,
        batch["scanfi_area_ha"] / batch["scanfi_forest_area_ha"],
        0.0,
    )
    batch["mgm_join_pending"] = True

    join_columns = [
        "cell_key",
        "level",
        "stratum_id",
        "stratum_name",
        "ecozone",
        "ecoprovince",
        "species",
        "species_group",
        "coverage_class",
        "distribution_borrowed",
        "area_weight_ha",
        "scanfi_area_ha",
        "scanfi_forest_area_ha",
        "area_fraction_of_forest",
        "area_match_status",
        "area_weight_status",
        "supply_eligible",
        "mgm_join_pending",
    ]
    batch[join_columns].to_csv(args.out_dir / "national_batch_area_join.csv", index=False)

    zero_area = batch[
        batch["area_match_status"].eq("mapped_zero") | batch["scanfi_forest_area_ha"].eq(0)
    ].copy()
    zero_area["reason"] = np.where(
        zero_area["scanfi_forest_area_ha"].eq(0),
        "no_forest_in_ecoprovince",
        "zero_scanfi_area_in_ecoprovince",
    )
    zero_area["notes"] = np.where(
        zero_area["scanfi_forest_area_ha"].eq(0),
        "The ecoprovince has zero SCANFI treed area. Do not assign supply.",
        "The mapped SCANFI target has zero area in this ecoprovince. Do not assign supply.",
    )
    zero_area[
        [
            "cell_key",
            "ecozone",
            "ecoprovince",
            "species",
            "species_group",
            "coverage_class",
            "scanfi_area_ha",
            "scanfi_forest_area_ha",
            "area_match_status",
            "area_weight_status",
            "supply_eligible",
            "reason",
            "notes",
        ]
    ].to_csv(args.out_dir / "zero_area_cells.csv", index=False)

    unmapped = batch[batch["area_match_status"].eq("unmapped_species")].copy()
    unmapped["reason"] = "no_scanfi_species_layer_for_target"
    unmapped["notes"] = (
        "SCANFI resolves ten species/classes; this RSYC species is not separately mapped. "
        "Use plot-based supply; do not infer SCANFI area for this species."
    )
    unmapped[
        [
            "cell_key",
            "ecozone",
            "ecoprovince",
            "species",
            "species_group",
            "coverage_class",
            "scanfi_forest_area_ha",
            "area_match_status",
            "area_weight_status",
            "supply_eligible",
            "reason",
            "notes",
        ]
    ].to_csv(args.out_dir / "unmapped_species_cells.csv", index=False)

    target_provinces = batch["ecoprovince"].astype(str).map(
        lambda name: bool(set(province_map.get(name, [])) & TARGET_PROVINCES)
    )
    borrowed = batch["distribution_borrowed"].astype(str).str.lower().isin(["true", "1"])
    strong_targets = batch[target_provinces & (batch["coverage_class"].eq("no_data") | borrowed)].copy()
    strong_targets = strong_targets.sort_values(["ecoprovince", "species"])

    donor_rows: list[dict[str, Any]] = []
    for _, target in strong_targets.iterrows():
        donor_rows.extend(
            candidate_rows_for_cell(
                target,
                coverage_ecoprovince,
                area_lookup,
                ecoprovince_centroids,
                province_map,
                primary_province_map,
            )
        )

    support = (
        coverage_ecodistrict.groupby(["ecodistrict", "ecoprovince", "ecozone"], dropna=False)
        .agg(
            n_sites_max=("n_sites", "max"),
            n_plots_max=("n_plots", "max"),
            n_species_supported=("n_sites", lambda values: int((values > 0).sum())),
        )
        .reset_index()
    )
    # Attach province labels from any coverage row for the ecodistrict.
    province_by_district = (
        coverage_ecodistrict.assign(province=coverage_ecodistrict["provinces"].astype(str))
        .groupby("ecodistrict")["province"]
        .first()
    )
    support["provinces"] = support["ecodistrict"].map(province_by_district).fillna("")
    support["forest_area_ha"] = support["ecodistrict"].map(district_forest).fillna(0.0)
    no_plot_rows = no_plot_ecodistrict_rows(support, ecodistrict_centroids)
    donor_rows.extend(no_plot_rows)

    donor_columns = [
        "target_type",
        "target_level",
        "target_id",
        "target_ecozone",
        "target_ecoprovince",
        "target_ecodistrict",
        "target_species",
        "target_species_group",
        "target_coverage_class",
        "target_provinces",
        "target_area_ha",
        "target_forest_area_ha",
        "target_area_match_status",
        "candidate_type",
        "candidate_id",
        "candidate_ecozone",
        "candidate_ecoprovince",
        "candidate_ecodistrict",
        "candidate_species",
        "candidate_species_group",
        "candidate_coverage_class",
        "candidate_n_sites",
        "candidate_n_plots",
        "candidate_provinces",
        "support_kind",
        "province_relation",
        "similarity_score",
        "rank",
        "rationale",
        "confidence",
        "mgm_join_pending",
    ]
    donor_frame = pd.DataFrame(donor_rows, columns=donor_columns)
    donor_frame.to_csv(args.out_dir / "donor_candidates.csv", index=False)
    donor_frame[donor_frame["target_type"] == "no_plot_ecodistrict"].to_csv(
        args.out_dir / "donor_candidates_no_plot_ecodistricts.csv", index=False
    )

    summary: dict[str, Any] = {
        "generated_utc": utc_now(),
        "inputs": {
            "area_weights": _relative_path(args.area_dir / "national_area_weights.csv"),
            "batch": _relative_path(args.batch),
            "coverage": _relative_path(args.coverage),
        },
        "counts": {
            "batch_cells": len(batch),
            "batch_cells_mapped_positive": int(batch["area_match_status"].eq("mapped_positive").sum()),
            "batch_cells_mapped_zero": int(batch["area_match_status"].eq("mapped_zero").sum()),
            "batch_cells_unmapped_species": int(batch["area_match_status"].eq("unmapped_species").sum()),
            "batch_cells_zero_forest_area": int(batch["scanfi_forest_area_ha"].eq(0).sum()),
            "batch_cells_supply_eligible": int(batch["supply_eligible"].sum()),
            "target_province_weak_cells": len(strong_targets),
            "donor_rows": len(donor_frame),
            "no_plot_ecodistricts": len(support[support["n_sites_max"] == 0]),
            "no_plot_donor_rows": len(no_plot_rows),
        },
        "target_provinces": sorted(TARGET_PROVINCES),
        "method": {
            "target_cells": "Batch cells whose ecoprovince intersects AB/ON/NS/NU/MB and whose coverage_class is no_data or distribution_borrowed is true.",
            "donor_score": "0.30 same ecozone + 0.20 distance decay (800 km) + 0.25 exact direct fit / 0.15 exact pooled / 0.05 species-group proxy + 0.15 log10 site support + 0.05 group match + 0.05 donor SCANFI area.",
            "confidence": "high >= 0.75 with exact direct fit and >= 20 sites; medium >= 0.55; otherwise low.",
            "no_plot_ecodistricts": "Ecodistricts with zero sites in every coverage row. Donor ecodistricts are ranked by same ecoprovince, same ecozone, distance (500 km), site support, and species richness.",
        },
        "caveats": [
            "MGM curves are not joined; all rows carry mgm_join_pending=true.",
            "Batch cells are ecoprovince-level and can span multiple provinces; target_provinces lists the overlap.",
            "Donor support is measured by plot counts, not by ecological representativeness or variance compatibility.",
            "SCANFI resolves ten species/classes; unmapped RSYC species cannot receive SCANFI area.",
        ],
    }
    (args.out_dir / "donor_candidates_summary.json").write_text(json.dumps(summary, indent=2))

    report_lines = [
        "# Donor Candidate Inventory",
        "",
        f"Generated: {summary['generated_utc']}",
        "",
        "## Counts",
        "",
        f"- Batch cells: {summary['counts']['batch_cells']:,}",
        f"- Mapped positive: {summary['counts']['batch_cells_mapped_positive']:,}",
        f"- Mapped zero: {summary['counts']['batch_cells_mapped_zero']:,}",
        f"- Unmapped RSYC species: {summary['counts']['batch_cells_unmapped_species']:,}",
        f"- Zero SCANFI forest area: {summary['counts']['batch_cells_zero_forest_area']:,}",
        f"- Supply eligible: {summary['counts']['batch_cells_supply_eligible']:,}",
        f"- Target-province weak cells (AB/ON/NS/NU/MB): {summary['counts']['target_province_weak_cells']:,}",
        f"- Donor rows: {summary['counts']['donor_rows']:,}",
        f"- No-plot ecodistricts: {summary['counts']['no_plot_ecodistricts']:,}",
        "",
        "## Method",
        "",
        summary["method"]["donor_score"],
        "",
        "## Caveats",
        "",
    ]
    report_lines.extend(f"- {item}" for item in summary["caveats"])
    report_lines.append("")
    (args.out_dir / "DONOR_CANDIDATES_REPORT.md").write_text("\n".join(report_lines))

    print(json.dumps(summary["counts"], indent=2))
    print(f"wrote {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
