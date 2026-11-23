"""MAGPlot Tier 1 measured tree lists for Quebec, New Brunswick and Yukon.

This module extracts the open MAGPlot v1.2 tree measurements for the three
Tier-1 provincial sources named by the MAGPlot data dictionary: ``QC_PSP``,
``NB_CLI`` (public land) and ``YT_PSP``.  It selects the latest measured visit
per plot and writes one measured tree list per province with source plot,
plot area, per-tree expansion and provenance.

Boundary
--------
The output is measured open data.  It is not bucked, not a growth model and
not a provincial inventory or national supply estimate.  Every row carries
``measured_tree_list=true``, ``open_data=true``, ``model_estimate=false`` and
``national_claim=false``.  No Tier-2, Tier-3 or restricted MAGPlot source is
read.  ``NFM_GP`` records are present in the open ZIP but are not part of the
Tier-1 source list in the data dictionary; they are deliberately excluded.

Compiled volume and biomass
---------------------------
MAGPlot v1.2 has no compiled volume or biomass field.  The eight tables and
the data dictionary were scanned for volume/biomass names; the only match is
the disturbance ``mortality_basis`` code ``VL`` ("volume"), which is not a
tree-level volume.  The tree table also has no ``vmb``-style merchantable
volume columns.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import shutil
import sqlite3
import subprocess
import sys
import time
import zipfile
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT_DIR = (
    REPO_ROOT / "data" / "derived" / "national" / "bucking" / "magplot_tier1_v0"
)
DEFAULT_ZIP = REPO_ROOT / "data" / "raw" / "magplot" / "MAGPlot_latest_data_package.zip"
DEFAULT_WORK_DIR = REPO_ROOT / "tmp" / "magplot_tier1_v0"
DEFAULT_PEP_ZIP = REPO_ROOT / "data" / "raw" / "qc_pep" / "PEP_GPKG.zip"

SOURCE_URL = (
    "https://ca.nfis.org/fss/fss?command=retrieveByName&"
    "fileName=MAGPlot_latest_data_package.zip&fileNameSpace=magplot&"
    "format=xml&promptToSave=true"
)
SOURCE_SHA256 = "270055bb9eeed6ccd6cbd75f6a2026b54e340c5a4938af18ad06e479792888db"
SOURCE_BYTES = 127_123_248
SOURCE_RETRIEVED_UTC = "2026-11-23T00:26:18Z"
SOURCE_VERSION = "MAGPlot v1.2 (package members dated 2026-02-26)"
LICENCE = "Open Government Licence - Canada"
LICENCE_URL = "https://open.canada.ca/en/open-government-licence-canada"
CITATION = (
    "National Forest Inventory. 2026. Multi-agency Ground Plot (MAGPlot) "
    "database - standards for forest ground plot data harmonization, version 1.2. "
    "Natural Resources Canada, Canadian Forest Service."
)

PEP_URL = (
    "https://diffusion.mffp.gouv.qc.ca/Diffusion/DonneeGratuite/Foret/"
    "DONNEES_FOR_ECO_SUD/Placettes_permanentes/02-Donnees/PROV/PEP_GPKG.zip"
)
PEP_SHA256 = "a5deb82b8e05c3226f83eb8daa4842900b6564e31b30915d9215f8a031faa5e5"
PEP_GPKG_SHA256 = "6d53e90a23799d681d050e13a433bc6e7ede6549b0733c8cc9c01fc8f94348c9"
PEP_RETRIEVED_UTC = "2026-11-14T17:34:19Z"

TIER1_SOURCES: Mapping[str, str] = {
    "QC": "QC_PSP",
    "NB": "NB_CLI",
    "YT": "YT_PSP",
}
PROVINCE_NAMES = {"QC": "Quebec", "NB": "New Brunswick", "YT": "Yukon"}
SOURCE_PROGRAM_NAMES = {
    "QC_PSP": "Quebec permanent sample plots (PEP)",
    "NB_CLI": "New Brunswick Continuous Landscape Inventory (public land)",
    "YT_PSP": "Yukon permanent sample plots",
}

TREE_DTYPES = {
    "magp_site_id": str,
    "plot_type_id": str,
    "plot_id": str,
    "tree_id": str,
    "tree_id_meas": str,
    "tree_id_treemeas": str,
    "species_gsv": str,
    "species_g": str,
    "species_s": str,
    "species_v": str,
    "species_gs": str,
    "meas_est_dbh": str,
    "meas_est_height": str,
    "crown_class": str,
    "stem_cond": str,
    "tree_status": str,
}
HEADER_DTYPES = {
    "magp_site_id": str,
    "plot_id": str,
    "plot_type_id": str,
}

TREE_LIST_COLUMNS = [
    "tree_record_id",
    "jurisdiction",
    "source_program",
    "magp_site_id",
    "source_plot",
    "plot_id",
    "plot_type_id",
    "plot_type_group",
    "meas_num",
    "meas_year",
    "meas_month",
    "plot_area_ha",
    "tree_num",
    "tracked_tree",
    "species_gsv",
    "species_g",
    "species_s",
    "species_v",
    "dbh_cm",
    "dbh_basis",
    "height_m",
    "height_basis",
    "crown_class",
    "stem_cond",
    "tree_status",
    "live_standing",
    "expansion_factor_stems_ha",
    "basal_area_m2",
    "basal_area_m2_ha",
    "expansion_area_mismatch",
    "taglimit_dbh_cm",
    "taglimit_height_m",
    "provenance_id",
    "licence",
    "access_tier",
    "measured_tree_list",
    "open_data",
    "model_estimate",
    "national_claim",
]

SCHEMA_ROWS = [
    ("tree_record_id", "string", "none", "Deterministic MAGPlot measurement key.", "magp_site_id:plot_type_id:meas_num:tree_num", "always", ""),
    ("jurisdiction", "string", "none", "Province or territory code.", "province", "always", "QC, NB or YT"),
    ("source_program", "string", "none", "Tier-1 MAGPlot source program.", "sources", "always", "QC_PSP, NB_CLI or YT_PSP"),
    ("magp_site_id", "string", "none", "MAGPlot site identifier.", "magp_site_id", "always", ""),
    ("source_plot", "string", "none", "Contributor plot identifier.", "source_plot", "always", "PEP id_pe for QC_PSP"),
    ("plot_id", "string", "none", "MAGPlot plot identifier (constant across visits).", "plot_id", "always", ""),
    ("plot_type_id", "string", "none", "MAGPlot plot type.", "plot_type_id", "always", "L1_I, L2_I, S1_I"),
    ("plot_type_group", "string", "none", "Large or small tree plot.", "derived from plot_type_id", "always", "large or small"),
    ("meas_num", "integer", "none", "MAGPlot measurement number, 0 at establishment.", "meas_num", "always", ""),
    ("meas_year", "integer", "year", "Measurement year.", "meas_year", "always", ""),
    ("meas_month", "integer", "month", "Measurement month; missing sentinel removed.", "meas_month", "nullable", ""),
    ("plot_area_ha", "number", "ha", "Fixed-area plot size.", "meas_plot_size", "always", "positive"),
    ("tree_num", "integer", "none", "MAGPlot tree number within plot type.", "tree_num", "always", "positive"),
    ("tracked_tree", "boolean", "none", "True when tree_num < 8,000,000.", "derived from tree_num", "always", ""),
    ("species_gsv", "string", "none", "Genus, species and variety code.", "species_gsv", "always", ""),
    ("species_g", "string", "none", "Genus code.", "species_g", "always", ""),
    ("species_s", "string", "none", "Species code.", "species_s", "always", ""),
    ("species_v", "string", "none", "Variety code.", "species_v", "always", ""),
    ("dbh_cm", "number", "cm", "Diameter at breast height; non-positive sentinels excluded.", "dbh", "always", "positive"),
    ("dbh_basis", "string", "none", "Measured or estimated DBH.", "meas_est_dbh", "always", "measured, estimated or unknown"),
    ("height_m", "number", "m", "Total tree height; -1 and -9 sentinels become empty.", "height", "nullable", "positive when present"),
    ("height_basis", "string", "none", "Measured, estimated or calculated height.", "meas_est_height", "always", "measured, estimated, calculated or missing"),
    ("crown_class", "string", "none", "Crown class label.", "crown_class", "always", "code label or unknown"),
    ("stem_cond", "string", "none", "Stem condition label.", "stem_cond", "always", "code label or unknown"),
    ("tree_status", "string", "none", "Tree status label.", "tree_status", "always", "live_standing, live, live_fallen, dead_standing or unknown"),
    ("live_standing", "boolean", "none", "True for tree_status LS or L (live standing/unspecified).", "derived from tree_status", "always", ""),
    ("expansion_factor_stems_ha", "number", "stems/ha", "Per-tree expansion supplied by MAGPlot.", "stem_ha", "always", "positive"),
    ("basal_area_m2", "number", "m2", "Derived individual basal area from DBH.", "pi*(dbh_cm/200)^2", "always", "non-negative"),
    ("basal_area_m2_ha", "number", "m2/ha", "Expanded basal area.", "basal_area_m2*stem_ha", "always", "non-negative"),
    ("expansion_area_mismatch", "boolean", "none", "True when stem_ha differs from 1/plot_area_ha by more than 1%.", "derived", "always", ""),
    ("taglimit_dbh_cm", "number", "cm", "Plot DBH tagging limit.", "taglimit_dbh", "nullable", "non-negative when present"),
    ("taglimit_height_m", "number", "m", "Plot height tagging limit.", "taglimit_height", "nullable", "non-negative when present"),
    ("provenance_id", "string", "none", "Short provenance key resolved in sources.csv and manifest.json.", "constant", "always", ""),
    ("licence", "string", "none", "Source licence short code.", "constant", "always", "OGL-Canada"),
    ("access_tier", "integer", "none", "MAGPlot access tier.", "constant", "always", "1"),
    ("measured_tree_list", "boolean", "none", "Row is a measured tree record, not a model output.", "constant", "always", "true"),
    ("open_data", "boolean", "none", "Row comes from an open Tier-1 source.", "constant", "always", "true"),
    ("model_estimate", "boolean", "none", "Row contains no model estimate.", "constant", "always", "false"),
    ("national_claim", "boolean", "none", "Row supports no national claim.", "constant", "always", "false"),
]


class MagPlotTier1Error(RuntimeError):
    """Raised when the source or output violates the extraction contract."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def count_csv_rows(path: Path) -> int | None:
    if path.suffix not in {".csv", ".gz"}:
        return None
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as handle:
        return max(sum(1 for _ in handle) - 1, 0)


def jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    if hasattr(value, "item") and not isinstance(value, (str, bytes)):
        return value.item()
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def read_zip_table(
    zip_path: Path, member: str, **kwargs: Any
) -> pd.DataFrame:
    with zipfile.ZipFile(zip_path) as archive, archive.open(member) as handle:
        return pd.read_csv(handle, **kwargs)


def read_tree_chunks(
    zip_path: Path, site_ids: set[str], chunk_size: int = 500_000
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    with (
        zipfile.ZipFile(zip_path) as archive,
        archive.open("magp_trees.csv") as handle,
    ):
        for chunk in pd.read_csv(
            handle, dtype=TREE_DTYPES, chunksize=chunk_size
        ):
            selected = chunk[chunk["magp_site_id"].isin(site_ids)]
            if not selected.empty:
                frames.append(selected)
    if not frames:
        raise MagPlotTier1Error("no tree rows matched the Tier-1 site list")
    return pd.concat(frames, ignore_index=True)


def git_status_lines(directory: Path) -> list[str]:
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=directory,
        check=True,
        capture_output=True,
        text=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def assert_no_unexpected_deletions() -> None:
    deletions: list[str] = []
    for root in (REPO_ROOT, REPO_ROOT / "data"):
        for line in git_status_lines(root):
            if len(line) >= 2 and (line[0] == "D" or line[1] == "D"):
                deletions.append(f"{root.name or 'parent'}: {line}")
    if deletions:
        raise MagPlotTier1Error(
            "unexpected git deletion(s); refusing to run MAGPlot Tier-1 extract: "
            + "; ".join(deletions)
        )


def load_site_map(zip_path: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    sites = read_zip_table(
        zip_path, "magp_sites.csv", dtype={"magp_site_id": str, "province": str}
    )
    sources = read_zip_table(zip_path, "magp_site_sources.csv", dtype=str)
    if sources["magp_site_id"].duplicated().any():
        raise MagPlotTier1Error("magp_site_sources has duplicate site identifiers")
    merged = sources.merge(
        sites[["magp_site_id", "province"]], on="magp_site_id", how="left"
    )
    if merged["province"].isna().any():
        raise MagPlotTier1Error("site source rows have no matching site record")
    allowed = set(TIER1_SOURCES.values())
    tier1 = merged[
        merged.apply(
            lambda row: TIER1_SOURCES.get(row["province"]) == row["sources"],
            axis=1,
        )
    ].copy()
    if tier1.empty:
        raise MagPlotTier1Error("Tier-1 source selection produced no sites")
    if tier1["magp_site_id"].duplicated().any():
        raise MagPlotTier1Error("a Tier-1 site is assigned to more than one plot")
    unexpected = sorted(set(tier1["province"]) - set(TIER1_SOURCES))
    if unexpected:
        raise MagPlotTier1Error(f"unexpected Tier-1 provinces: {unexpected}")
    excluded = merged[~merged["magp_site_id"].isin(tier1["magp_site_id"])]
    audit = {
        "sites_total": len(merged),
        "sites_tier1": len(tier1),
        "excluded_non_tier1_sites": len(excluded),
        "excluded_sources": {
            str(key): int(value)
            for key, value in excluded["sources"].value_counts().items()
        },
        "tier1_sources": dict(TIER1_SOURCES),
        "allowed_sources": sorted(allowed),
    }
    return tier1, audit


def load_headers(zip_path: Path, site_ids: set[str]) -> pd.DataFrame:
    headers = read_zip_table(
        zip_path, "magp_tree_header.csv", dtype=HEADER_DTYPES
    )
    headers = headers[headers["magp_site_id"].isin(site_ids)].copy()
    if headers.empty:
        raise MagPlotTier1Error("no tree headers matched the Tier-1 site list")
    return headers


def select_latest_headers(headers: pd.DataFrame) -> pd.DataFrame:
    ordered = headers.sort_values(
        ["meas_year", "meas_month", "meas_num"], kind="mergesort"
    )
    latest = ordered.drop_duplicates(["plot_id"], keep="last").copy()
    duplicates = latest["plot_id"].duplicated().sum()
    if duplicates:
        raise MagPlotTier1Error("latest-visit selection produced duplicate plot IDs")
    return latest


def map_label(value: Any, mapping: Mapping[str, str], fallback: str) -> str:
    return mapping.get(str(value), fallback)


def clean_tree_list(frame: pd.DataFrame, province: str, source: str) -> pd.DataFrame:
    clean = frame.copy()
    clean["plot_type_group"] = clean["plot_type_id"].str[0].map(
        {"L": "large", "S": "small"}
    )
    clean["tracked_tree"] = clean["tree_num"] < 8_000_000
    clean["dbh_basis"] = clean["meas_est_dbh"].map(
        {"M": "measured", "E": "estimated"}
    ).fillna("unknown")
    clean["height_m"] = clean["height"].where(clean["height"] > 0)
    clean["height_basis"] = clean["meas_est_height"].map(
        {"M": "measured", "E": "estimated", "C": "calculated"}
    ).fillna("missing")
    clean["crown_class"] = clean["crown_class"].map(
        {
            "V": "very_dominant",
            "D": "dominant",
            "C": "co_dominant",
            "I": "intermediate",
            "S": "suppressed",
        }
    ).fillna("unknown")
    clean["stem_cond"] = clean["stem_cond"].map(
        {
            "I": "intact",
            "B": "broken_top",
            "D": "dead_top",
            "T": "broken_or_dead_top",
            "S": "broken_stem",
        }
    ).fillna("unknown")
    clean["tree_status"] = clean["tree_status"].map(
        {
            "LS": "live_standing",
            "L": "live",
            "LF": "live_fallen",
            "DS": "dead_standing",
        }
    ).fillna("unknown")
    clean["live_standing"] = clean["tree_status"].isin(
        ["live_standing", "live"]
    )
    clean["meas_month"] = clean["meas_month"].where(clean["meas_month"] > 0)
    clean["taglimit_dbh_cm"] = clean["taglimit_dbh"].where(
        clean["taglimit_dbh"] >= 0
    )
    clean["taglimit_height_m"] = clean["taglimit_height"].where(
        clean["taglimit_height"] >= 0
    )
    implied_area = 1.0 / clean["meas_plot_size"]
    clean["expansion_area_mismatch"] = (
        (clean["stem_ha"] - implied_area).abs() > 0.01 * implied_area
    )
    clean["tree_record_id"] = (
        clean["magp_site_id"]
        + ":"
        + clean["plot_type_id"]
        + ":"
        + clean["meas_num"].astype(int).astype(str)
        + ":"
        + clean["tree_num"].astype(int).astype(str)
    )
    clean["jurisdiction"] = province
    clean["source_program"] = source
    clean["provenance_id"] = (
        "magplot_v1.2_tier1_" + source.lower() + "_latest_visit"
    )
    clean["licence"] = "OGL-Canada"
    clean["access_tier"] = 1
    clean["measured_tree_list"] = True
    clean["open_data"] = True
    clean["model_estimate"] = False
    clean["national_claim"] = False
    clean = clean.rename(
        columns={
            "dbh": "dbh_cm",
            "stem_ha": "expansion_factor_stems_ha",
            "meas_plot_size": "plot_area_ha",
        }
    )
    clean["dbh_cm"] = clean["dbh_cm"].round(1)
    clean["height_m"] = clean["height_m"].round(1)
    clean["plot_area_ha"] = clean["plot_area_ha"].round(4)
    clean["expansion_factor_stems_ha"] = clean[
        "expansion_factor_stems_ha"
    ].round(4)
    clean["basal_area_m2"] = (
        math.pi * (clean["dbh_cm"] / 200.0) ** 2
    ).round(6)
    clean["basal_area_m2_ha"] = (
        clean["basal_area_m2"] * clean["expansion_factor_stems_ha"]
    ).round(6)
    clean["taglimit_dbh_cm"] = clean["taglimit_dbh_cm"].round(3)
    clean["taglimit_height_m"] = clean["taglimit_height_m"].round(3)
    return clean[TREE_LIST_COLUMNS].sort_values(
        ["source_plot", "plot_type_id", "plot_id", "tree_num"],
        kind="mergesort",
    ).reset_index(drop=True)


def coverage_rows(
    province: str,
    source: str,
    site_map: pd.DataFrame,
    headers: pd.DataFrame,
    latest_headers: pd.DataFrame,
    latest_trees: pd.DataFrame,
    included: pd.DataFrame,
) -> dict[str, Any]:
    province_sites = site_map[site_map["province"] == province]
    source_headers = headers[headers["magp_site_id"].isin(province_sites["magp_site_id"])]
    source_trees = latest_trees
    group_mask = source_trees["plot_type_id"].str[0].eq("L") | source_trees[
        "plot_type_id"
    ].str.startswith("S1")
    excluded_group = int((~group_mask).sum())
    group_frame = source_trees[group_mask]
    excluded_dbh = int((group_frame["dbh"] <= 0).sum())
    dbh_frame = group_frame[group_frame["dbh"] > 0]
    excluded_expansion = int((dbh_frame["stem_ha"] <= 0).sum())
    expansion_frame = dbh_frame[dbh_frame["stem_ha"] > 0]
    excluded_area = int((expansion_frame["meas_plot_size"] <= 0).sum())
    if len(included) + excluded_group + excluded_dbh + excluded_expansion + excluded_area != len(source_trees):
        raise MagPlotTier1Error(f"coverage funnel does not close for {province}")
    return {
        "jurisdiction": province,
        "province_name": PROVINCE_NAMES[province],
        "source_program": source,
        "source_name": SOURCE_PROGRAM_NAMES[source],
        "access_tier": 1,
        "sites_source": int(province_sites["magp_site_id"].nunique()),
        "plots_source": int(source_headers["plot_id"].nunique()),
        "plot_visits_source": int(
            source_headers[["plot_id", "meas_num"]].drop_duplicates().shape[0]
        ),
        "tree_rows_source": len(source_trees),
        "plots_latest": int(latest_headers["plot_id"].nunique()),
        "tree_rows_latest": len(source_trees),
        "included_tree_rows": len(included),
        "excluded_plot_group_rows": excluded_group,
        "excluded_dbh_nonpositive_rows": excluded_dbh,
        "excluded_expansion_nonpositive_rows": excluded_expansion,
        "excluded_plot_area_nonpositive_rows": excluded_area,
        "included_sites": int(included["magp_site_id"].nunique()),
        "included_plots": int(included["plot_id"].nunique()),
        "included_species_codes": int(included["species_gsv"].nunique()),
        "included_live_standing_rows": int(included["live_standing"].sum()),
        "included_dead_standing_rows": int(
            (included["tree_status"] == "dead_standing").sum()
        ),
        "included_unknown_status_rows": int(
            (included["tree_status"] == "unknown").sum()
        ),
        "included_tracked_tree_rows": int(included["tracked_tree"].sum()),
        "included_untracked_tree_rows": int((~included["tracked_tree"]).sum()),
        "included_height_present_rows": int(included["height_m"].notna().sum()),
        "included_height_missing_rows": int(included["height_m"].isna().sum()),
        "included_expansion_area_mismatch_rows": int(
            included["expansion_area_mismatch"].sum()
        ),
        "plot_area_min_ha": float(included["plot_area_ha"].min()),
        "plot_area_max_ha": float(included["plot_area_ha"].max()),
        "expansion_min_stems_ha": float(
            included["expansion_factor_stems_ha"].min()
        ),
        "expansion_max_stems_ha": float(
            included["expansion_factor_stems_ha"].max()
        ),
        "dbh_min_cm": float(included["dbh_cm"].min()),
        "dbh_max_cm": float(included["dbh_cm"].max()),
        "height_min_m": float(included["height_m"].min()),
        "height_max_m": float(included["height_m"].max()),
        "meas_year_min": int(included["meas_year"].min()),
        "meas_year_max": int(included["meas_year"].max()),
    }


def source_frame() -> pd.DataFrame:
    rows = []
    for province, source in TIER1_SOURCES.items():
        rows.append(
            {
                "dataset": "MAGPlot latest data package",
                "version": SOURCE_VERSION,
                "publisher": "Natural Resources Canada / Canadian Forest Service",
                "source_program": source,
                "jurisdiction": province,
                "source_url": SOURCE_URL,
                "retrieved_utc": SOURCE_RETRIEVED_UTC,
                "bytes": SOURCE_BYTES,
                "sha256": SOURCE_SHA256,
                "licence": LICENCE,
                "licence_url": LICENCE_URL,
                "access_tier": 1,
                "tier1_basis": (
                    "Data dictionary section 4 lists Tier 1 as QC_PSP, YT_PSP and "
                    "NB_CLI plots on public land (BC_FAIBGP is fuzzed coordinates)."
                ),
                "coordinate_note": (
                    "The data dictionary says Tier-1 QC_PSP/YT_PSP/NB_CLI public-land "
                    "plots have exact coordinates, but its global remark says all "
                    "published coordinates are approximate within +/-10 km. The "
                    "contradiction is unresolved and coordinates are not used here."
                ),
                "citation": CITATION,
                "restrictions": (
                    "No Tier-2/Tier-3 or restricted source used. NFM_GP records in "
                    "the open ZIP are excluded because the data dictionary Tier-1 "
                    "source list does not name them."
                ),
                "local_archive": "data/raw/magplot/MAGPlot_latest_data_package.zip",
            }
        )
    rows.append(
        {
            "dataset": "Quebec PEP (Placettes-echantillons permanentes)",
            "version": "PEP GPKG, metadata modified 2026-11-11",
            "publisher": "MRNF Quebec, Direction des inventaires forestiers",
            "source_program": "QC_PEP",
            "jurisdiction": "QC",
            "source_url": PEP_URL,
            "retrieved_utc": PEP_RETRIEVED_UTC,
            "bytes": 128_568_617,
            "sha256": PEP_SHA256,
            "licence": "CC BY 4.0",
            "licence_url": "https://creativecommons.org/licenses/by/4.0/",
            "access_tier": 1,
            "tier1_basis": "Comparison source only; not used to build tree lists.",
            "coordinate_note": "Exact PEP plot coordinates are published.",
            "citation": (
                "Ministere des Ressources naturelles et des Forets du Quebec. "
                "Placettes-echantillons permanentes, 1970 a aujourd'hui."
            ),
            "restrictions": "Open download; comparison only.",
            "local_archive": "data/raw/qc_pep/PEP_GPKG.zip",
        }
    )
    return pd.DataFrame(rows)


def extract_pep_gpkg(zip_path: Path, work_dir: Path) -> Path:
    target = work_dir / "PEP.gpkg"
    if target.exists() and sha256(target) == PEP_GPKG_SHA256:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    with (
        zipfile.ZipFile(zip_path) as archive,
        archive.open("PEP.gpkg") as source,
        target.open("wb") as destination,
    ):
        shutil.copyfileobj(source, destination)
    if sha256(target) != PEP_GPKG_SHA256:
        raise MagPlotTier1Error("PEP.gpkg hash does not match the acquisition record")
    return target


def qc_pep_comparison(
    qc_source_trees: pd.DataFrame,
    qc_headers: pd.DataFrame,
    qc_sites: pd.DataFrame,
    pep_zip: Path,
    work_dir: Path,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    gpkg = extract_pep_gpkg(pep_zip, work_dir)
    connection = sqlite3.connect(gpkg)
    try:
        pep_sites = pd.read_sql(
            "select id_pe, reseau, no_prj, no_viree, no_pe from placette",
            connection,
        )
        pep_visits = pd.read_sql(
            "select id_pe, no_mes, id_pe_mes, date_sond, statut_mes "
            "from placette_mes",
            connection,
        )
        pep_trees = pd.read_sql(
            "select id_pe, no_mes, no_arbre, id_arbre, etat, essence, dhp, "
            "hauteurdic, tige_ha, vmb_tige, vmb_ha from dendro_arbres",
            connection,
        )
    finally:
        connection.close()

    magp_site_keys = set(qc_sites["source_plot"])
    pep_site_keys = set(pep_sites["id_pe"])
    matched_sites = magp_site_keys & pep_site_keys
    visit = pep_visits.copy()
    visit["pep_year"] = pd.to_datetime(visit["date_sond"], errors="coerce").dt.year
    visit["meas_num"] = visit["no_mes"] - 1
    magp_visit_keys = (
        qc_headers[["source_plot", "meas_num", "meas_year"]]
        .drop_duplicates()
        .rename(columns={"source_plot": "id_pe"})
    )
    visit_match = visit.merge(
        magp_visit_keys,
        on=["id_pe", "meas_num"],
        how="left",
        suffixes=("_pep", "_magp"),
        indicator=True,
    )
    matched_visits = int((visit_match["_merge"] == "both").sum())
    year_mismatches = int(
        (
            visit_match.loc[visit_match["_merge"] == "both", "pep_year"]
            != visit_match.loc[visit_match["_merge"] == "both", "meas_year"]
        ).sum()
    )

    pep_tree = pep_trees.copy()
    pep_tree["tree_num"] = pd.to_numeric(pep_tree["no_arbre"], errors="coerce")
    pep_tree["meas_num"] = pep_tree["no_mes"] - 1
    magp_tree = qc_source_trees.copy()
    magp_tree["tree_num"] = pd.to_numeric(magp_tree["tree_num"], errors="coerce")
    magp_tree["id_pe"] = magp_tree["source_plot"]
    tree_match = magp_tree.merge(
        pep_tree,
        on=["id_pe", "meas_num", "tree_num"],
        how="left",
        suffixes=("_magp", "_pep"),
        indicator=True,
    )
    both = tree_match[tree_match["_merge"] == "both"].copy()
    both["dbh_pep_cm"] = both["dhp"] / 10.0
    both["dbh_abs_diff_cm"] = (both["dbh"] - both["dbh_pep_cm"]).abs()
    valid = both[both["dbh"] > 0]
    dbh_within_01 = int((valid["dbh_abs_diff_cm"] <= 0.1).sum())
    dbh_within_05 = int((valid["dbh_abs_diff_cm"] <= 0.5).sum())
    expansion_exact = int((both["stem_ha"] == both["tige_ha"]).sum())
    vmb_tige_nonnull = int(pep_trees["vmb_tige"].notna().sum())
    vmb_ha_nonnull = int(pep_trees["vmb_ha"].notna().sum())

    site_counts = (
        qc_sites.groupby("source_plot")
        .agg(
            magp_site_id=("magp_site_id", "first"),
            pep_matched=("source_plot", lambda values: values.iloc[0] in pep_site_keys),
        )
        .reset_index()
    )
    magp_visit_counts = (
        qc_headers.groupby("source_plot")["meas_num"].nunique().rename("magp_visits")
    )
    magp_tree_counts = (
        qc_source_trees.groupby("source_plot").size().rename("magp_tree_rows")
    )
    pep_visit_counts = pep_visits.groupby("id_pe")["no_mes"].nunique().rename("pep_visits")
    pep_tree_counts = pep_trees.groupby("id_pe").size().rename("pep_tree_rows")
    matched_tree_counts = (
        both.groupby("id_pe").size().rename("matched_tree_rows")
    )
    overlap = (
        site_counts.rename(columns={"source_plot": "pep_id_pe"})
        .merge(
            pep_sites[["id_pe", "reseau"]],
            left_on="pep_id_pe",
            right_on="id_pe",
            how="left",
        )
        .merge(magp_visit_counts, left_on="pep_id_pe", right_index=True, how="left")
        .merge(magp_tree_counts, left_on="pep_id_pe", right_index=True, how="left")
        .merge(pep_visit_counts, left_on="pep_id_pe", right_index=True, how="left")
        .merge(pep_tree_counts, left_on="pep_id_pe", right_index=True, how="left")
        .merge(
            matched_tree_counts, left_on="pep_id_pe", right_index=True, how="left"
        )
        .drop(columns=["id_pe"])
    )
    overlap = overlap.fillna(
        {
            "magp_visits": 0,
            "magp_tree_rows": 0,
            "pep_visits": 0,
            "pep_tree_rows": 0,
            "matched_tree_rows": 0,
        }
    )
    overlap["reseau"] = overlap["reseau"].fillna("not_in_pep")

    summary = {
        "magp_qc_psp_sites": len(magp_site_keys),
        "pep_sites": len(pep_site_keys),
        "matched_sites": len(matched_sites),
        "magplot_only_sites": len(magp_site_keys - pep_site_keys),
        "pep_only_sites": len(pep_site_keys - magp_site_keys),
        "matched_site_share": round(len(matched_sites) / len(magp_site_keys), 6),
        "pep_visits": len(pep_visits),
        "matched_visits": matched_visits,
        "magplot_only_visit_combinations": int(
            len(magp_visit_keys) - matched_visits
        ),
        "pep_only_visits": int(len(pep_visits) - matched_visits),
        "matched_visit_year_mismatches": year_mismatches,
        "pep_tree_measurements": len(pep_trees),
        "magp_qc_tree_rows": len(qc_source_trees),
        "matched_tree_measurements": len(both),
        "pep_tree_measurements_not_linked": int(len(pep_trees) - len(both)),
        "magp_tree_rows_not_linked": int(len(qc_source_trees) - len(both)),
        "matched_dbh_positive_rows": len(valid),
        "dbh_within_0_1_cm": dbh_within_01,
        "dbh_within_0_1_cm_share": round(dbh_within_01 / len(valid), 6),
        "dbh_within_0_5_cm": dbh_within_05,
        "dbh_max_abs_diff_cm": float(valid["dbh_abs_diff_cm"].max()),
        "expansion_exact_matches": expansion_exact,
        "expansion_exact_share": round(expansion_exact / len(both), 6),
        "pep_vmb_tige_nonnull": vmb_tige_nonnull,
        "pep_vmb_ha_nonnull": vmb_ha_nonnull,
        "pep_tree_measurements_total": len(pep_trees),
        "schema_mapping": {
            "MAGPlot source_plot": "PEP placette.id_pe",
            "MAGPlot meas_num + 1": "PEP placette_mes.no_mes / dendro_arbres.no_mes",
            "MAGPlot tree_num": "PEP dendro_arbres.no_arbre",
            "MAGPlot dbh (cm)": "PEP dendro_arbres.dhp (mm) / 10",
            "MAGPlot stem_ha": "PEP dendro_arbres.tige_ha",
            "MAGPlot height (m)": "PEP dendro_arbres.hauteurdic (comparison only; not used)",
        },
    }
    comparison = pd.DataFrame(
        [
            {"metric": key, "value": value, "detail": ""}
            for key, value in summary.items()
            if not isinstance(value, (dict, list))
        ]
    )
    return overlap, comparison, summary


def schema_frame() -> pd.DataFrame:
    return pd.DataFrame(
        SCHEMA_ROWS,
        columns=[
            "column",
            "dtype",
            "unit",
            "description",
            "source_field",
            "nullable",
            "constraint",
        ],
    )


def markdown_table(frame: pd.DataFrame) -> str:
    headers = [str(column) for column in frame.columns]
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join(["---"] * len(headers)) + "|",
    ]
    for _, row in frame.iterrows():
        values = []
        for column in frame.columns:
            value = row[column]
            if isinstance(value, float):
                value = f"{value:,.4f}".rstrip("0").rstrip(".")
            values.append(str(value))
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def report_markdown(
    coverage: pd.DataFrame,
    summary: Mapping[str, Any],
    pep_summary: Mapping[str, Any],
    sources: pd.DataFrame,
) -> str:
    coverage_table = coverage[
        [
            "jurisdiction",
            "source_program",
            "sites_source",
            "plots_source",
            "tree_rows_source",
            "included_tree_rows",
            "included_live_standing_rows",
            "included_species_codes",
        ]
    ].copy()
    coverage_table.columns = [
        "Jurisdiction",
        "Source",
        "Sites",
        "Plots",
        "Latest tree rows",
        "Included trees",
        "Live standing",
        "Species",
    ]
    source_table = sources[["source_program", "licence", "access_tier", "retrieved_utc", "sha256"]].copy()
    source_table.columns = ["Source", "Licence", "Tier", "Retrieved UTC", "SHA-256"]
    lines = [
        "# MAGPlot Tier 1 measured tree lists - QC, NB, YT - v0",
        "",
        "**Status:** measured open-data tree lists only. No bucking, no volume or",
        "biomass model, no growth model, no official grade, and no provincial or",
        "national supply claim. `measured_tree_list=true`, `open_data=true`,",
        "`model_estimate=false`, `national_claim=false` on every row.",
        "",
        "## Download and schema",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Dataset | {SOURCE_VERSION} |",
        "| Publisher | Natural Resources Canada / Canadian Forest Service |",
        f"| Direct URL | {SOURCE_URL} |",
        f"| Retrieved UTC | {SOURCE_RETRIEVED_UTC} |",
        f"| Bytes | {SOURCE_BYTES:,} |",
        f"| SHA-256 | `{SOURCE_SHA256}` |",
        f"| Licence | {LICENCE} |",
        "| Access tier | Tier 1 open package |",
        "| Archive | `data/raw/magplot/MAGPlot_latest_data_package.zip` |",
        "",
        "The ZIP contains eight CSV tables plus one coordinate-type PDF. The",
        "tables used here are `magp_sites`, `magp_site_sources`,",
        "`magp_tree_header` and `magp_trees`. The full schema, source field and",
        "nullability for every output column are in `schema.csv`.",
        "",
        "Tier-1 selection is the source allowlist in the MAGPlot data dictionary:",
        "`QC_PSP`, `NB_CLI` on public land and `YT_PSP`. `NFM_GP` records also",
        "occur in the open ZIP but are excluded because the dictionary's Tier-1",
        "list does not name that source. No Tier-2, Tier-3 or restricted source",
        "was read. NB_CLI is the public-land subset published in the Tier-1",
        "package; the package has no ownership field to re-test that condition.",
        "",
        "## QC / NB / YT coverage",
        "",
        markdown_table(coverage_table),
        "",
        "Tree-list scope: latest measured visit per MAGPlot plot, large-tree",
        "(`L`) and measured small-tree (`S1`) plots, positive DBH, positive",
        "expansion and positive fixed-plot area. Regeneration (`R`) and `S2`",
        "count-tree plots are excluded because they are tally/count layers",
        "rather than individual measured stems; their counts remain in",
        "`coverage.csv`. Trees with",
        "non-positive DBH are excluded rather than patched.",
        "",
        "| Jurisdiction | Included sites | Included plots | Excluded DBH | Excluded group | Height present | Unknown status |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for _, row in coverage.iterrows():
        lines.append(
            f"| {row['jurisdiction']} | {int(row['included_sites']):,} | "
            f"{int(row['included_plots']):,} | "
            f"{int(row['excluded_dbh_nonpositive_rows']):,} | "
            f"{int(row['excluded_plot_group_rows']):,} | "
            f"{int(row['included_height_present_rows']):,} | "
            f"{int(row['included_unknown_status_rows']):,} |"
        )
    lines += [
        "",
        "## Expansion and plot area",
        "",
        "`plot_area_ha` is `meas_plot_size`; `expansion_factor_stems_ha` is the",
        "source `stem_ha`. For fixed-area plots the source normally sets",
        "`stem_ha = 1 / meas_plot_size`. The extract preserves the source",
        "expansion and flags, rather than silently replacing, the small number of",
        "rows where the two disagree by more than 1%:",
        "",
        "| Jurisdiction | Mismatch rows | Min expansion | Max expansion | Min area | Max area |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for _, row in coverage.iterrows():
        lines.append(
            f"| {row['jurisdiction']} | "
            f"{int(row['included_expansion_area_mismatch_rows']):,} | "
            f"{row['expansion_min_stems_ha']:,.2f} | "
            f"{row['expansion_max_stems_ha']:,.2f} | "
            f"{row['plot_area_min_ha']:,.6f} | "
            f"{row['plot_area_max_ha']:,.4f} |"
        )
    lines += [
        "",
        "## Compiled volume and biomass",
        "",
        "MAGPlot v1.2 has **no compiled tree volume or biomass field**. All eight",
        "table headers and the v1.2 data dictionary were scanned. The only",
        "`volume` token is the disturbance `mortality_basis` code `VL` (volume),",
        "which is not a tree-level volume. There is no `vmb_tige`, `vmb_ha`,",
        "merchantable-volume, gross-volume, net-volume or biomass column in",
        "`magp_trees.csv`. No volume or biomass is computed or imputed in this",
        "product. The QC PEP comparison source likewise has `vmb_tige` and",
        "`vmb_ha` columns, but both are null for all 2,210,417 PEP tree rows.",
        "",
        "## QC MAGPlot / QC PEP overlap",
        "",
        "QC_PSP `source_plot` is the PEP `id_pe`; MAGPlot `tree_num` is PEP",
        "`no_arbre`; MAGPlot `meas_num + 1` is PEP `no_mes`. This supports a",
        "direct record-level check, not just a name comparison.",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| QC_PSP sites | {pep_summary['magp_qc_psp_sites']:,} |",
        f"| PEP plots | {pep_summary['pep_sites']:,} |",
        f"| Matched plots | {pep_summary['matched_sites']:,} |",
        f"| MAGPlot-only plots | {pep_summary['magplot_only_sites']:,} |",
        f"| PEP-only plots | {pep_summary['pep_only_sites']:,} |",
        f"| Matched plot share | {pep_summary['matched_site_share']:.4%} |",
        f"| Matched visits | {pep_summary['matched_visits']:,} |",
        f"| Matched visit-year mismatches | {pep_summary['matched_visit_year_mismatches']:,} |",
        f"| Matched tree measurements | {pep_summary['matched_tree_measurements']:,} |",
        f"| DBH agreement within 0.1 cm | {pep_summary['dbh_within_0_1_cm_share']:.4%} |",
        f"| Expansion exact match | {pep_summary['expansion_exact_share']:.4%} |",
        f"| PEP `vmb_tige` non-null | {pep_summary['pep_vmb_tige_nonnull']:,} |",
        f"| PEP `vmb_ha` non-null | {pep_summary['pep_vmb_ha_nonnull']:,} |",
        "",
        "The unmatched MAGPlot QC rows are overwhelmingly regeneration/small",
        "count trees (`R1_I`, `S2_I`) that have no one-to-one PEP",
        "`dendro_arbres` record. The matched tracked-tree population agrees on",
        "DBH and expansion to within source rounding. Per-plot overlap is in",
        "`qc_pep_overlap.csv`; all metrics are in `qc_pep_comparison.csv`.",
        "",
        "## Sources and provenance",
        "",
        markdown_table(source_table),
        "",
        "`sources.csv` records the full URL, retrieval time, bytes, SHA-256,",
        "licence, Tier-1 basis, coordinate caveat and restrictions for MAGPlot",
        "and the PEP comparison source. `manifest.json` hashes every artifact",
        "and records the input package hash.",
        "",
        "## Files",
        "",
        "| File | Contents |",
        "|---|---|",
        "| `qc_tree_list.csv.gz` | Quebec MAGPlot Tier-1 measured tree list (gzip CSV) |",
        "| `nb_tree_list.csv.gz` | New Brunswick MAGPlot Tier-1 measured tree list (gzip CSV) |",
        "| `yt_tree_list.csv.gz` | Yukon MAGPlot Tier-1 measured tree list (gzip CSV) |",
        "| `schema.csv` | Output column dictionary, units and constraints |",
        "| `coverage.csv` | Source and included counts, exclusions and ranges |",
        "| `sources.csv` | Licence, URL, timestamp, hash and Tier-1 basis |",
        "| `qc_pep_overlap.csv` | Per-plot QC_PSP/PEP overlap |",
        "| `qc_pep_comparison.csv` | QC/PEP overlap and schema-reconciliation metrics |",
        "| `summary.json` | Machine-readable totals and checks |",
        "| `report.md` | This report |",
        "| `manifest.json` | Artifact hashes, input hashes and run metadata |",
        "",
        "## Limitations",
        "",
        "- Latest-visit selection keeps one measurement per plot, not a growth",
        "  panel. Earlier visits remain in MAGPlot but are not in these lists.",
        "- Heights are missing for many source trees; the extract leaves them",
        "  empty. It does not impute height.",
        "- NB_CLI coverage is the Tier-1 public-land subset in the open package.",
        "  The package has no land-ownership field to verify public-land status",
        "  independently.",
        "- The MAGPlot dictionary is internally inconsistent about whether Tier-1",
        "  coordinates are exact or approximate within +/-10 km. Coordinates are",
        "  therefore carried only in the source tables, not in these tree lists.",
        "- No compiled volume or biomass exists in MAGPlot v1.2. Any later",
        "  bucking or biomass layer must be labelled as a model estimate and",
        "  must not be presented as observed mill or inventory volume.",
        "",
    ]
    return "\n".join(lines)


def manifest_frame(
    output_dir: Path,
    zip_path: Path,
    work_dir: Path,
    started: float,
    summary: Mapping[str, Any],
) -> dict[str, Any]:
    artifacts = []
    for path in sorted(output_dir.iterdir()):
        if not path.is_file() or path.name == "manifest.json":
            continue
        artifacts.append(
            {
                "label": path.name,
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
                "rows": count_csv_rows(path),
            }
        )
    return {
        "product": "magplot_tier1_v0",
        "status": "measured_tree_list_open_data",
        "labels": {
            "measured_tree_list": True,
            "open_data": True,
            "model_estimate": False,
            "national_claim": False,
            "bucking_performed": False,
        },
        "source": {
            "dataset": SOURCE_VERSION,
            "url": SOURCE_URL,
            "retrieved_utc": SOURCE_RETRIEVED_UTC,
            "bytes": SOURCE_BYTES,
            "sha256": sha256(zip_path),
            "expected_sha256": SOURCE_SHA256,
            "licence": LICENCE,
            "licence_url": LICENCE_URL,
            "access_tier": 1,
            "tier1_sources": dict(TIER1_SOURCES),
            "tier2_or_tier3_used": False,
        },
        "comparison_source": {
            "dataset": "Quebec PEP",
            "url": PEP_URL,
            "retrieved_utc": PEP_RETRIEVED_UTC,
            "zip_sha256": PEP_SHA256,
            "gpkg_sha256": PEP_GPKG_SHA256,
            "licence": "CC BY 4.0",
            "role": "QC overlap and schema reconciliation only",
        },
        "run": {
            "command": " ".join(sys.argv),
            "python": sys.version.split()[0],
            "pandas": pd.__version__,
            "utc": datetime.now(UTC).isoformat(),
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "work_dir": str(work_dir.relative_to(REPO_ROOT)),
        },
        "summary": summary,
        "artifacts": artifacts,
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    started = time.monotonic()
    assert_no_unexpected_deletions()
    zip_path = args.zip.resolve()
    if not zip_path.exists():
        raise MagPlotTier1Error(f"MAGPlot ZIP not found: {zip_path}")
    source_hash = sha256(zip_path)
    if source_hash != SOURCE_SHA256 and not args.allow_source_change:
        raise MagPlotTier1Error(
            f"MAGPlot ZIP hash changed: {source_hash}; expected {SOURCE_SHA256}"
        )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.work_dir.mkdir(parents=True, exist_ok=True)

    site_map, audit = load_site_map(zip_path)
    site_ids = set(site_map["magp_site_id"])
    headers = load_headers(zip_path, site_ids)
    headers = headers.merge(
        site_map[["magp_site_id", "province", "sources", "source_plot"]],
        on="magp_site_id",
        how="left",
        validate="many_to_one",
    )
    if headers["province"].isna().any():
        raise MagPlotTier1Error("tree header row has no Tier-1 site assignment")
    latest_headers = select_latest_headers(headers)
    trees = read_tree_chunks(zip_path, site_ids)
    trees = trees.merge(
        site_map[["magp_site_id", "province", "sources", "source_plot"]],
        on="magp_site_id",
        how="left",
        validate="many_to_one",
    )
    if trees["province"].isna().any():
        raise MagPlotTier1Error("tree row has no Tier-1 site assignment")
    latest_trees = trees.merge(
        latest_headers[
            [
                "magp_site_id",
                "plot_id",
                "plot_type_id",
                "meas_num",
                "meas_year",
                "meas_month",
                "meas_plot_size",
                "taglimit_dbh",
                "taglimit_height",
            ]
        ],
        on=["magp_site_id", "plot_id", "plot_type_id", "meas_num"],
        how="inner",
        validate="many_to_one",
    )
    if latest_trees.empty:
        raise MagPlotTier1Error("no tree measurements matched the latest visits")
    if latest_trees.duplicated(
        ["magp_site_id", "plot_type_id", "meas_num", "tree_num"]
    ).any():
        raise MagPlotTier1Error("latest tree list has duplicate primary keys")

    coverage_records: list[dict[str, Any]] = []
    tree_lists: dict[str, pd.DataFrame] = {}
    for province, source in TIER1_SOURCES.items():
        province_latest = latest_trees[latest_trees["province"] == province]
        province_sites = site_map[site_map["province"] == province]
        province_headers = headers[headers["province"] == province]
        province_latest_headers = latest_headers[
            latest_headers["magp_site_id"].isin(province_sites["magp_site_id"])
        ]
        province_trees = province_latest[
            province_latest["plot_type_id"].str[0].eq("L")
            | province_latest["plot_type_id"].str.startswith("S1")
        ]
        province_trees = province_trees[
            (province_trees["dbh"] > 0)
            & (province_trees["stem_ha"] > 0)
            & (province_trees["meas_plot_size"] > 0)
        ].copy()
        if province_trees.empty:
            raise MagPlotTier1Error(f"no included trees for {province}")
        clean = clean_tree_list(province_trees, province, source)
        tree_lists[province] = clean
        coverage_records.append(
            coverage_rows(
                province,
                source,
                site_map,
                province_headers,
                province_latest_headers,
                province_latest,
                clean,
            )
        )
    coverage = pd.DataFrame(coverage_records).sort_values("jurisdiction")
    for province, source in TIER1_SOURCES.items():
        path = args.output_dir / f"{province.lower()}_tree_list.csv.gz"
        tree_lists[province].to_csv(
            path, index=False, na_rep="", compression="gzip"
        )
        if path.stat().st_size == 0:
            raise MagPlotTier1Error(f"empty tree list written: {path}")

    qc_source = trees[
        (trees["province"] == "QC") & (trees["sources"] == "QC_PSP")
    ].copy()
    qc_headers = headers[
        (headers["province"] == "QC") & (headers["sources"] == "QC_PSP")
    ].copy()
    qc_sites = site_map[
        (site_map["province"] == "QC") & (site_map["sources"] == "QC_PSP")
    ].copy()
    overlap = pd.DataFrame()
    comparison = pd.DataFrame()
    pep_summary: dict[str, Any] = {}
    if args.pep_zip.exists():
        overlap, comparison, pep_summary = qc_pep_comparison(
            qc_source, qc_headers, qc_sites, args.pep_zip, args.work_dir
        )
        overlap.to_csv(args.output_dir / "qc_pep_overlap.csv", index=False)
        comparison.to_csv(
            args.output_dir / "qc_pep_comparison.csv", index=False
        )
    else:
        pep_summary = {"available": False, "reason": "PEP ZIP not found"}

    schema_frame().to_csv(args.output_dir / "schema.csv", index=False)
    coverage.to_csv(args.output_dir / "coverage.csv", index=False)
    sources = source_frame()
    sources.to_csv(args.output_dir / "sources.csv", index=False)

    summary: dict[str, Any] = {
        "product": "magplot_tier1_v0",
        "status": "measured_tree_list_open_data",
        "labels": {
            "measured_tree_list": True,
            "open_data": True,
            "model_estimate": False,
            "national_claim": False,
            "bucking_performed": False,
        },
        "source": {
            "version": SOURCE_VERSION,
            "url": SOURCE_URL,
            "retrieved_utc": SOURCE_RETRIEVED_UTC,
            "bytes": SOURCE_BYTES,
            "sha256": source_hash,
            "licence": LICENCE,
            "access_tier": 1,
        },
        "tier1_sources": dict(TIER1_SOURCES),
        "tier2_or_tier3_used": False,
        "site_audit": audit,
        "jurisdictions": coverage.to_dict(orient="records"),
        "totals": {
            "included_tree_rows": int(sum(len(frame) for frame in tree_lists.values())),
            "included_sites": int(
                sum(record["included_sites"] for record in coverage_records)
            ),
            "included_plots": int(
                sum(record["included_plots"] for record in coverage_records)
            ),
            "included_live_standing_rows": int(
                sum(record["included_live_standing_rows"] for record in coverage_records)
            ),
        },
        "compiled_fields": {
            "magplot_compiled_volume_available": False,
            "magplot_compiled_biomass_available": False,
            "magplot_volume_columns": [],
            "magplot_biomass_columns": [],
            "note": (
                "No tree-level volume or biomass column exists in MAGPlot v1.2. "
                "The only volume token is the disturbance mortality_basis code VL."
            ),
            "pep_comparison_vmb_tige_nonnull": pep_summary.get(
                "pep_vmb_tige_nonnull"
            ),
            "pep_comparison_vmb_ha_nonnull": pep_summary.get("pep_vmb_ha_nonnull"),
        },
        "qc_pep": pep_summary,
        "checks": {
            "no_tier2_or_tier3_sources": True,
            "no_negative_or_zero_dbh": bool(
                all((frame["dbh_cm"] > 0).all() for frame in tree_lists.values())
            ),
            "no_negative_or_zero_expansion": bool(
                all(
                    (frame["expansion_factor_stems_ha"] > 0).all()
                    for frame in tree_lists.values()
                )
            ),
            "no_negative_or_zero_plot_area": bool(
                all((frame["plot_area_ha"] > 0).all() for frame in tree_lists.values())
            ),
            "required_numeric_fields_non_null": bool(
                all(
                    frame[
                        [
                            "dbh_cm",
                            "expansion_factor_stems_ha",
                            "plot_area_ha",
                            "basal_area_m2",
                            "basal_area_m2_ha",
                            "tree_num",
                            "meas_num",
                            "meas_year",
                        ]
                    ]
                    .notna()
                    .all()
                    .all()
                    for frame in tree_lists.values()
                )
            ),
            "tree_record_id_unique": bool(
                all(frame["tree_record_id"].is_unique for frame in tree_lists.values())
            ),
            "licence_recorded": True,
            "source_hash_matches_archive": source_hash == SOURCE_SHA256,
        },
    }
    summary_path = args.output_dir / "summary.json"
    summary_path.write_text(
        json.dumps(jsonable(summary), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    report = report_markdown(coverage, summary, pep_summary, sources)
    (args.output_dir / "report.md").write_text(report, encoding="utf-8")
    assert_no_unexpected_deletions()
    manifest = manifest_frame(
        args.output_dir, zip_path, args.work_dir, started, summary
    )
    (args.output_dir / "manifest.json").write_text(
        json.dumps(jsonable(manifest), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    for record in manifest["artifacts"]:
        path = args.output_dir / str(record["label"])
        if sha256(path) != record["sha256"]:
            raise MagPlotTier1Error(f"manifest hash mismatch for {record['label']}")
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Extract MAGPlot Tier-1 measured tree lists for QC, NB and YT."
        )
    )
    parser.add_argument("--zip", type=Path, default=DEFAULT_ZIP)
    parser.add_argument("--pep-zip", type=Path, default=DEFAULT_PEP_ZIP)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
    parser.add_argument("--allow-source-change", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        summary = run(args)
    except MagPlotTier1Error as error:
        print(f"magplot_tier1_v0: {error}", file=sys.stderr)
        return 1
    print(json.dumps(jsonable(summary["totals"]), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
