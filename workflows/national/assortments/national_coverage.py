"""National plot-to-RSYC-stratum coverage audit.

This module inventories the harmonized national plot extract against the RSYC
ecodistrict / ecoregion / ecoprovince / ecozone hierarchy.  It does **not** fit
diameter distributions; it answers a narrower question: for every RSYC stratum
and species, how much independent plot support exists, and is that support
enough for a direct fit, a pooled fit, or no fit at all?

Run from the repository root::

    .venv/bin/python -m workflows.national.assortments.national_coverage

Fail-closed rule: any unexpected ``D`` in the parent or ``data`` repository
status halts the run immediately.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
import time
import zipfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

__all__ = ["main", "run"]

ARTIFACT_VERSION = "0.1.0"
ECO_LEVELS = ("ecodistrict", "ecoregion", "ecoprovince", "ecozone")
ECO_LAYERS = {
    "ecodistrict": "ecodistricts",
    "ecoregion": "ecoregions",
    "ecoprovince": "ecoprovinces",
    "ecozone": "ecozones",
}
LEVEL_ORDER = ("national",) + ECO_LEVELS
COVERAGE_CLASSES = ("direct_fit", "pooled_fit", "no_data")
THRESHOLDS = {
    "direct_min_trees": 100,
    "direct_min_plots": 5,
    "pooled_min_trees": 30,
    "pooled_max_trees": 99,
    "pooled_min_plots": 2,
    "pooled_max_plots": 4,
}
PROVINCES = (
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

#: The 32 scientific species in the RSYC model table, plus its three model
#: groups.  ``generic`` is the RSYC group label for unresolved taxa, not a
#: taxonomic species.
RSYC_SPECIES = (
    "ABIE.AMA",
    "ABIE.BAL",
    "ABIE.LAS",
    "ACER.MAC",
    "ACER.RUB",
    "ACER.SAH",
    "ALNU.RUB",
    "BETU.ALL",
    "BETU.PAP",
    "FRAX.NIG",
    "LARI.LAR",
    "LARI.OCC",
    "PICE.ABI",
    "PICE.ENG",
    "PICE.GLA",
    "PICE.MAR",
    "PICE.RUB",
    "PICE.SIT",
    "PINU.BAN",
    "PINU.CON",
    "PINU.PON",
    "PINU.RES",
    "PINU.STR",
    "POPU.BAL",
    "POPU.GRA",
    "POPU.TRE",
    "PSEU.MEN",
    "QUER.RUB",
    "THUJ.OCC",
    "THUJ.PLI",
    "TSUG.HET",
    "TSUG.MER",
)
RSYC_GROUPS = ("coniferous", "broadleaf", "generic")
SPECIES_ORDER = tuple(sorted(RSYC_SPECIES)) + RSYC_GROUPS

CONIFER_GENERA = {
    "ABIE",
    "CHAM",
    "JUNI",
    "LARI",
    "PICE",
    "PINU",
    "PSEU",
    "TAXU",
    "THUJ",
    "TSUG",
    "GENC",
}
BROADLEAF_GENERA = {
    "ACER",
    "ALNU",
    "AMEL",
    "BETU",
    "CARP",
    "CARY",
    "CELT",
    "CORN",
    "CORY",
    "CRAT",
    "DIRC",
    "FAGU",
    "FRAX",
    "GLED",
    "HAMA",
    "GENH",
    "ILEX",
    "JUGL",
    "LONI",
    "MALU",
    "OSTR",
    "POPU",
    "PRUN",
    "QUER",
    "RHAM",
    "RHUS",
    "ROSA",
    "RUBU",
    "SALI",
    "SAMB",
    "SHEP",
    "SORB",
    "TILI",
    "ULMU",
    "VIBU",
}

#: BC FAIB codes with a confident one-to-one RSYC species match.
BC_SPECIES: dict[str, str] = {
    "PL": "PINU.CON",
    "BL": "ABIE.LAS",
    "SW": "PICE.GLA",
    "SE": "PICE.ENG",
    "SB": "PICE.MAR",
    "SS": "PICE.SIT",
    "HW": "TSUG.HET",
    "HM": "TSUG.MER",
    "FD": "PSEU.MEN",
    "CW": "THUJ.PLI",
    "LW": "LARI.OCC",
    "PY": "PINU.PON",
    "BA": "ABIE.AMA",
    "AT": "POPU.TRE",
    "EP": "BETU.PAP",
    "DR": "ALNU.RUB",
    "MB": "ACER.MAC",
}

#: BC codes that need a documented nearest-taxon alias.
BC_ALIASES: dict[str, tuple[str, str]] = {
    "AC": (
        "POPU.BAL",
        "black cottonwood; nearest RSYC taxon is balsam poplar (POPU.BAL)",
    ),
}

#: Québec PEP French codes with a confident one-to-one RSYC species match.
QC_SPECIES: dict[str, str] = {
    "SAB": "ABIE.BAL",
    "EPN": "PICE.MAR",
    "EPB": "PICE.GLA",
    "EPR": "PICE.RUB",
    "PIG": "PINU.BAN",
    "PIB": "PINU.STR",
    "PIR": "PINU.RES",
    "THO": "THUJ.OCC",
    "MEL": "LARI.LAR",
    "CHR": "QUER.RUB",
    "BOP": "BETU.PAP",
    "BOJ": "BETU.ALL",
    "ERS": "ACER.SAH",
    "ERR": "ACER.RUB",
    "PET": "POPU.TRE",
    "PEG": "POPU.GRA",
    "FRN": "FRAX.NIG",
}

#: Newfoundland FAL simplified codes with a confident RSYC species match.
NL_SPECIES: dict[str, str] = {
    "BF": "ABIE.BAL",
    "BS": "PICE.MAR",
    "WS": "PICE.GLA",
    "TL": "LARI.LAR",
    "JP": "PINU.BAN",
    "WP": "PINU.STR",
    "WB": "BETU.PAP",
    "TA": "POPU.TRE",
    "RM": "ACER.RUB",
    "YB": "BETU.ALL",
}

SOURCE_LABELS = (
    "faib_psp",
    "faib_non_psp",
    "magplot",
    "magplot_wfs",
    "qc_pep",
    "nl_fal",
)

#: One known spelling mismatch between the RSYC GeoPackage and model table.
DISTRICT_SPELLING_FIX = ("Betsiamiles Plateau", "Betsiamites Plateau")


@dataclass(frozen=True)
class CoverageConfig:
    """Filesystem and threshold configuration for one audit run."""

    repo_root: Path
    data_root: Path
    out_dir: Path
    metadata_path: Path
    top_n_gaps: int = 20
    verbose: bool = False

    @property
    def sites_path(self) -> Path:
        return self.data_root / "derived" / "fvsca_calibration" / "v0" / "sites.csv"

    @property
    def visits_path(self) -> Path:
        return self.data_root / "derived" / "fvsca_calibration" / "v0" / "visits.csv"

    @property
    def trees_path(self) -> Path:
        return self.data_root / "derived" / "fvsca_calibration" / "v0" / "trees.csv.gz"

    @property
    def ecostrat_path(self) -> Path:
        return self.data_root / "raw" / "national" / "rsyc" / "RSYC_ecostrat.gpkg"

    @property
    def models_path(self) -> Path:
        return self.data_root / "raw" / "national" / "rsyc" / "RSYC_models.zip"


def _normalize_code(value: object) -> str:
    """Return a stripped, non-sentinel string code."""
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    if text.lower() in {"", "nan", "none", "<na>"}:
        return ""
    return text


def _canonical_strata_id(strata_id: str) -> str:
    """Apply the one documented RSYC GeoPackage/model-table spelling fix."""
    original, corrected = DISTRICT_SPELLING_FIX
    return str(strata_id).replace(original, corrected)


def _rsyc_model_group(species: str) -> str:
    if species in RSYC_SPECIES:
        return "species"
    return species


def _species_group(species: str) -> str:
    if species in {"coniferous", "broadleaf", "generic"}:
        return species
    if species in {
        "ABIE.AMA",
        "ABIE.BAL",
        "ABIE.LAS",
        "LARI.LAR",
        "LARI.OCC",
        "PICE.ABI",
        "PICE.ENG",
        "PICE.GLA",
        "PICE.MAR",
        "PICE.RUB",
        "PICE.SIT",
        "PINU.BAN",
        "PINU.CON",
        "PINU.PON",
        "PINU.RES",
        "PINU.STR",
        "PSEU.MEN",
        "THUJ.OCC",
        "THUJ.PLI",
        "TSUG.HET",
        "TSUG.MER",
    }:
        return "coniferous"
    return "broadleaf"


def _fallback_group(source_group: object) -> tuple[str, str]:
    """Map a coarse harmonized group to an RSYC group label."""
    group = _normalize_code(source_group).lower()
    if group == "conifer":
        return "coniferous", "functional_group"
    if group == "deciduous":
        return "broadleaf", "functional_group"
    return "generic", "generic"


def _validate_species_maps() -> None:
    """Fail fast if a manual map targets a species absent from RSYC."""
    for name, mapping in (
        ("BC", BC_SPECIES),
        ("QC", QC_SPECIES),
        ("NL", NL_SPECIES),
    ):
        invalid = {
            code: species
            for code, species in mapping.items()
            if species not in RSYC_SPECIES
        }
        if invalid:
            raise ValueError(f"{name} species map targets absent RSYC codes: {invalid}")


def map_species_code(
    source: str,
    code: object,
    source_group: object = None,
) -> tuple[str, str, str]:
    """Map one source species code to an RSYC species or model group.

    Returns
    -------
    (rsyc_species, mapping_basis, note)
        ``mapping_basis`` is one of ``exact_species``, ``alias``,
        ``functional_group``, ``generic``, or ``unmapped``.
    """
    source_name = _normalize_code(source).lower()
    normalized = _normalize_code(code).upper()

    if source_name in {"magplot", "magplot_wfs"}:
        if not normalized:
            return "generic", "generic", "missing MAGPlot species code"
        parts = normalized.split(".")
        genus = parts[0]
        if len(parts) >= 2 and parts[1]:
            latin = f"{genus}.{parts[1]}"
            if latin in RSYC_SPECIES:
                return latin, "exact_species", "MAGPlot Latin binomial"
        if genus == "UNKN":
            return "generic", "generic", "MAGPlot unknown species"
        if genus in CONIFER_GENERA:
            return "coniferous", "functional_group", f"genus {genus} is coniferous"
        if genus in BROADLEAF_GENERA:
            return "broadleaf", "functional_group", f"genus {genus} is broadleaf"
        return "generic", "generic", f"genus {genus} not in RSYC or group map"

    if source_name in {"faib_psp", "faib_non_psp"}:
        if normalized in BC_SPECIES:
            return BC_SPECIES[normalized], "exact_species", "BC FAIB code"
        if normalized in BC_ALIASES:
            species, note = BC_ALIASES[normalized]
            return species, "alias", note
        species, basis = _fallback_group(source_group)
        return (
            species,
            basis,
            f"BC code {normalized or '<missing>'} has no RSYC species match",
        )

    if source_name == "qc_pep":
        if normalized in QC_SPECIES:
            return QC_SPECIES[normalized], "exact_species", "Québec PEP code"
        species, basis = _fallback_group(source_group)
        return (
            species,
            basis,
            f"QC code {normalized or '<missing>'} has no RSYC species match",
        )

    if source_name == "nl_fal":
        if normalized in NL_SPECIES:
            return NL_SPECIES[normalized], "exact_species", "Newfoundland FAL code"
        species, basis = _fallback_group(source_group)
        return (
            species,
            basis,
            f"NL code {normalized or '<missing>'} has no RSYC species match",
        )

    return "", "unmapped", f"unrecognised source {source!r}"


def classify_coverage(n_trees: int, n_plots: int) -> str:
    """Classify a stratum × species record with the pre-registered thresholds.

    The pooled rule is applied literally: a record is pooled when it is not
    direct and either tree count is in [30, 100) or plot count is in [2, 5).
    This conservatively leaves single-plot high-tree records and many-plot
    low-tree records in ``no_data``.
    """
    if (
        n_trees >= THRESHOLDS["direct_min_trees"]
        and n_plots >= THRESHOLDS["direct_min_plots"]
    ):
        return "direct_fit"
    if (
        THRESHOLDS["pooled_min_trees"] <= n_trees <= THRESHOLDS["pooled_max_trees"]
        or THRESHOLDS["pooled_min_plots"] <= n_plots <= THRESHOLDS["pooled_max_plots"]
    ):
        return "pooled_fit"
    return "no_data"


def assert_no_unexpected_deletions(repo_root: Path) -> None:
    """Halt immediately on any worktree or index deletion."""
    repositories = [repo_root]
    data_root = repo_root / "data"
    if (data_root / ".git").exists():
        repositories.append(data_root)
    for repository in repositories:
        result = subprocess.run(
            ["git", "-C", str(repository), "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"git status failed in {repository}: {result.stderr.strip()}"
            )
        for line in result.stdout.splitlines():
            if len(line) >= 2 and (line[0] == "D" or line[1] == "D"):
                raise RuntimeError(
                    f"unexpected deletion in {repository}: {line!r}; halting fail-closed"
                )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _display_path(path: Path, repo_root: Path) -> str:
    """Return a repository-relative path when possible."""
    try:
        return str(path.absolute().relative_to(repo_root.absolute()))
    except ValueError:
        return str(path)


def _git_commit(repository: Path) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if not math.isfinite(float(value)) else float(value)
    if isinstance(value, float):
        return None if not math.isfinite(value) else value
    if value is pd.NA or value is pd.NaT:
        return None
    return value


def load_sites(path: Path) -> pd.DataFrame:
    """Load and normalise the harmonized site table."""
    required = {
        "source",
        "site_id",
        "province",
        "lat",
        "lon",
        "n_visits",
        "n_trees",
    }
    sites = pd.read_csv(path, low_memory=False, dtype={"site_id": str})
    missing = sorted(required.difference(sites.columns))
    if missing:
        raise ValueError(f"sites table missing columns: {', '.join(missing)}")
    if sites["site_id"].duplicated().any():
        duplicate_count = int(sites["site_id"].duplicated().sum())
        raise ValueError(f"sites table has {duplicate_count} duplicate site_id values")
    sites = sites.copy()
    sites["source"] = sites["source"].astype(str).str.strip().str.lower()
    sites["site_id"] = sites["site_id"].astype(str).str.strip()
    sites["province"] = sites["province"].astype(str).str.strip().str.upper()
    sites["lat"] = pd.to_numeric(sites["lat"], errors="coerce")
    sites["lon"] = pd.to_numeric(sites["lon"], errors="coerce")
    sites["n_visits"] = (
        pd.to_numeric(sites["n_visits"], errors="coerce").fillna(0).astype(int)
    )
    sites["n_trees"] = (
        pd.to_numeric(sites["n_trees"], errors="coerce").fillna(0).astype(int)
    )
    sites["site_remeasured"] = sites["n_visits"] > 1

    missing_coord = sites[["lat", "lon"]].isna().any(axis=1)
    sentinel = sites["lat"].eq(-1.0) & sites["lon"].eq(-1.0)
    in_bounds = sites["lat"].between(40.0, 84.0) & sites["lon"].between(-142.0, -52.0)
    sites["coordinate_status"] = np.select(
        [missing_coord, sentinel, ~in_bounds],
        ["missing", "sentinel", "out_of_bounds"],
        default="valid",
    )
    sites["coordinate_status"] = pd.Categorical(
        sites["coordinate_status"],
        categories=("valid", "missing", "sentinel", "out_of_bounds"),
    )
    return sites


def _read_ecostrat_layers(path: Path) -> dict[str, pd.DataFrame]:
    import geopandas as gpd

    layers: dict[str, pd.DataFrame] = {}
    for level, layer_name in ECO_LAYERS.items():
        frame = gpd.read_file(path, layer=layer_name)
        if frame.crs is None or frame.crs.to_epsg() != 3978:
            raise ValueError(f"RSYC layer {layer_name} is not EPSG:3978")
        layers[level] = frame
    return layers


def _stratum_reference(layers: Mapping[str, pd.DataFrame]) -> pd.DataFrame:
    """Build the complete RSYC stratum reference grid."""
    eco = layers["ecodistrict"].copy()
    required = {
        "ecozone",
        "ecoprovince",
        "ecoregion",
        "ecodistrict",
        "ecozone_id",
        "ecoprovince_id",
        "ecoregion_id",
        "ecodistrict_id",
        "join_key",
    }
    missing = sorted(required.difference(eco.columns))
    if missing:
        raise ValueError(f"ecodistricts layer missing columns: {', '.join(missing)}")

    eco["ecozone_key"] = eco["ecozone"].astype(str)
    eco["ecoprovince_key"] = (
        eco["ecozone"].astype(str) + "/" + eco["ecoprovince"].astype(str)
    )
    eco["ecoregion_key"] = (
        eco["ecozone"].astype(str)
        + "/"
        + eco["ecoprovince"].astype(str)
        + "/"
        + eco["ecoregion"].astype(str)
    )
    eco["ecodistrict_key"] = eco["join_key"].astype(str)

    # Validate that constructed keys reproduce the published layer keys.
    for level in ECO_LEVELS:
        layer_keys = set(layers[level]["join_key"].astype(str))
        constructed = set(eco[f"{level}_key"].astype(str))
        if constructed != layer_keys:
            missing_keys = sorted(layer_keys.difference(constructed))[:3]
            extra_keys = sorted(constructed.difference(layer_keys))[:3]
            raise ValueError(
                f"{level} join_key reconstruction mismatch; "
                f"missing={missing_keys}, extra={extra_keys}"
            )

    rows: list[pd.DataFrame] = []
    for level in ECO_LEVELS:
        name_column = level
        key_column = f"{level}_key"
        reference = eco[
            [
                "ecozone",
                "ecoprovince",
                "ecoregion",
                "ecodistrict",
                "ecozone_id",
                "ecoprovince_id",
                "ecoregion_id",
                "ecodistrict_id",
                key_column,
            ]
        ].copy()
        reference["stratum_name"] = eco[name_column].to_numpy()
        reference = reference.rename(columns={key_column: "stratum_id"})
        reference["level"] = level
        reference["rsyc_strata_id"] = reference["stratum_id"].map(_canonical_strata_id)
        if level == "ecozone":
            reference["ecoprovince"] = pd.NA
            reference["ecoregion"] = pd.NA
            reference["ecodistrict"] = pd.NA
            reference["ecoprovince_id"] = pd.NA
            reference["ecoregion_id"] = pd.NA
            reference["ecodistrict_id"] = pd.NA
        elif level == "ecoprovince":
            reference["ecoregion"] = pd.NA
            reference["ecodistrict"] = pd.NA
            reference["ecoregion_id"] = pd.NA
            reference["ecodistrict_id"] = pd.NA
        elif level == "ecoregion":
            reference["ecodistrict"] = pd.NA
            reference["ecodistrict_id"] = pd.NA
        reference = reference.drop_duplicates("stratum_id")
        rows.append(reference)

    national = pd.DataFrame(
        {
            "level": ["national"],
            "stratum_id": ["Canada"],
            "stratum_name": ["Canada"],
            "rsyc_strata_id": ["Canada"],
            "ecozone": [pd.NA],
            "ecoprovince": [pd.NA],
            "ecoregion": [pd.NA],
            "ecodistrict": [pd.NA],
            "ecozone_id": [pd.NA],
            "ecoprovince_id": [pd.NA],
            "ecoregion_id": [pd.NA],
            "ecodistrict_id": [pd.NA],
        }
    )
    rows.append(national)
    reference = pd.concat(rows, ignore_index=True, sort=False)
    district_counts = eco.groupby("ecozone_key").size().rename("n_ecodistricts_total")
    province_counts = (
        eco.groupby("ecoprovince_key").size().rename("n_ecodistricts_total")
    )
    region_counts = eco.groupby("ecoregion_key").size().rename("n_ecodistricts_total")
    reference["n_ecodistricts_total"] = 1
    reference.loc[reference["level"].eq("national"), "n_ecodistricts_total"] = len(eco)
    reference.loc[reference["level"].eq("ecozone"), "n_ecodistricts_total"] = (
        reference.loc[reference["level"].eq("ecozone"), "stratum_id"].map(
            district_counts
        )
    )
    reference.loc[reference["level"].eq("ecoprovince"), "n_ecodistricts_total"] = (
        reference.loc[reference["level"].eq("ecoprovince"), "stratum_id"].map(
            province_counts
        )
    )
    reference.loc[reference["level"].eq("ecoregion"), "n_ecodistricts_total"] = (
        reference.loc[reference["level"].eq("ecoregion"), "stratum_id"].map(
            region_counts
        )
    )
    return reference.reset_index(drop=True)


def assign_sites_to_ecostrata(
    sites: pd.DataFrame,
    layers: Mapping[str, pd.DataFrame],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Spatially join valid site coordinates to RSYC ecodistricts."""
    import geopandas as gpd

    started = time.time()
    valid = sites["coordinate_status"].eq("valid")
    mapped = sites.loc[valid].copy()
    eco = layers["ecodistrict"][
        [
            "join_key",
            "ecozone",
            "ecoprovince",
            "ecoregion",
            "ecodistrict",
            "ecozone_id",
            "ecoprovince_id",
            "ecoregion_id",
            "ecodistrict_id",
            "geometry",
        ]
    ].copy()
    points = gpd.GeoDataFrame(
        mapped,
        geometry=gpd.points_from_xy(mapped["lon"], mapped["lat"]),
        crs=4326,
    ).to_crs(3978)
    joined = gpd.sjoin(points, eco, how="left", predicate="within")
    duplicate_sites = int(joined["site_id"].duplicated().sum())
    joined = joined.drop(columns=["index_right"], errors="ignore")
    joined = joined.drop_duplicates("site_id", keep="first")

    for column in (
        "join_key",
        "ecozone",
        "ecoprovince",
        "ecoregion",
        "ecodistrict",
        "ecozone_id",
        "ecoprovince_id",
        "ecoregion_id",
        "ecodistrict_id",
    ):
        sites[column] = sites["site_id"].map(joined.set_index("site_id")[column])
    sites["ecodistrict_key"] = sites["join_key"]
    sites["ecozone_key"] = sites["ecozone"].astype("string")
    sites["ecoprovince_key"] = (
        sites["ecozone"].astype("string") + "/" + sites["ecoprovince"].astype("string")
    )
    sites["ecoregion_key"] = (
        sites["ecozone"].astype("string")
        + "/"
        + sites["ecoprovince"].astype("string")
        + "/"
        + sites["ecoregion"].astype("string")
    )
    for level in ECO_LEVELS:
        sites[f"rsyc_{level}_key"] = sites[f"{level}_key"].map(_canonical_strata_id)
    sites["mapped"] = sites["ecodistrict_key"].notna()
    sites["coordinate_status"] = sites["coordinate_status"].astype(str)
    mapping = {
        "runtime_seconds": time.time() - started,
        "sites_with_valid_coordinates": int(valid.sum()),
        "sites_mapped_to_ecodistrict": int(sites["mapped"].sum()),
        "sites_outside_rsyc_ecostrata": int((valid & ~sites["mapped"]).sum()),
        "duplicate_boundary_matches": duplicate_sites,
    }
    return sites, mapping


def load_trees(path: Path, sites: pd.DataFrame) -> pd.DataFrame:
    """Load trees, attach geography/year, and derive physical plot ids."""
    required = {
        "source",
        "site_id",
        "visit_id",
        "tree_id",
        "species_code",
        "species_group",
        "dbh_cm",
        "status",
    }
    trees = pd.read_csv(
        path,
        low_memory=False,
        usecols=sorted(required),
        dtype={
            "source": str,
            "site_id": str,
            "visit_id": str,
            "tree_id": str,
            "species_code": str,
            "species_group": str,
            "status": str,
        },
    )
    missing = sorted(required.difference(trees.columns))
    if missing:
        raise ValueError(f"trees table missing columns: {', '.join(missing)}")
    trees["source"] = trees["source"].astype(str).str.strip().str.lower()
    trees["site_id"] = trees["site_id"].astype(str).str.strip()
    trees["visit_id"] = trees["visit_id"].astype(str).str.strip()
    trees["tree_id"] = trees["tree_id"].astype(str).str.strip()
    trees["species_code"] = trees["species_code"].map(_normalize_code)
    trees["species_group"] = trees["species_group"].map(_normalize_code)
    trees["dbh_cm"] = pd.to_numeric(trees["dbh_cm"], errors="coerce")
    trees["status"] = trees["status"].astype(str).str.strip().str.lower()

    trees["live_valid"] = (
        trees["status"].eq("live")
        & trees["dbh_cm"].notna()
        & trees["dbh_cm"].gt(0.0)
        & trees["dbh_cm"].le(500.0)
    )
    trees["dbh_lt_5cm"] = trees["dbh_cm"].lt(5.0) & trees["live_valid"]

    visit = pd.read_csv(
        path.parent / "visits.csv",
        low_memory=False,
        usecols=["source", "site_id", "visit_id", "year"],
        dtype={"source": str, "site_id": str, "visit_id": str},
    )
    visit["source"] = visit["source"].astype(str).str.strip().str.lower()
    visit["visit_id"] = visit["visit_id"].astype(str).str.strip()
    visit["visit_key"] = visit["source"] + "|" + visit["visit_id"]
    conflicting = visit.groupby("visit_key")["year"].nunique(dropna=False)
    if (conflicting > 1).any():
        bad = conflicting[conflicting > 1].index[:3].tolist()
        raise ValueError(f"visit keys map to multiple years: {bad}")
    visit = visit.drop_duplicates("visit_key", keep="first")
    trees["year"] = (
        trees["source"]
        .str.cat(trees["visit_id"], sep="|")
        .map(visit.set_index("visit_key")["year"])
    )

    site_columns = [
        "site_id",
        "province",
        "n_visits",
        "site_remeasured",
        "mapped",
        "coordinate_status",
        "ecodistrict_key",
        "ecozone_key",
        "ecoprovince_key",
        "ecoregion_key",
        "join_key",
        "ecozone",
        "ecoprovince",
        "ecoregion",
        "ecodistrict",
        "ecozone_id",
        "ecoprovince_id",
        "ecoregion_id",
        "ecodistrict_id",
    ]
    site_frame = sites[site_columns].set_index("site_id")
    trees = trees.join(site_frame, on="site_id")

    faib = trees["source"].isin(["faib_psp", "faib_non_psp"])
    magplot = trees["source"].isin(["magplot", "magplot_wfs"])
    plot_id = np.empty(len(trees), dtype=object)
    plot_id[faib.to_numpy()] = (
        trees.loc[faib, "source"].to_numpy()
        + "|"
        + trees.loc[faib, "site_id"].to_numpy()
        + "|"
        + trees.loc[faib, "tree_id"].str.split("-", n=1).str[0].to_numpy()
    )
    plot_id[magplot.to_numpy()] = (
        trees.loc[magplot, "source"].to_numpy()
        + "|"
        + trees.loc[magplot, "visit_id"].str.split("|", n=1).str[0].to_numpy()
    )
    remainder = ~(faib | magplot)
    plot_id[remainder.to_numpy()] = (
        trees.loc[remainder, "source"].to_numpy()
        + "|"
        + trees.loc[remainder, "site_id"].to_numpy()
    )
    trees["plot_id"] = plot_id
    for level in ECO_LEVELS:
        trees[f"rsyc_{level}_key"] = trees[f"{level}_key"].map(_canonical_strata_id)
    trees["rsyc_strata_id"] = trees["rsyc_ecodistrict_key"]
    coordinate_status = trees["coordinate_status"].astype(str)
    mapped_mask = trees["mapped"].fillna(False).astype(bool)
    trees["site_mapping_status"] = np.select(
        [
            mapped_mask,
            coordinate_status.eq("valid") & ~mapped_mask,
            coordinate_status.eq("missing"),
            coordinate_status.eq("sentinel"),
            coordinate_status.eq("out_of_bounds"),
        ],
        [
            "mapped",
            "outside_rsyc",
            "missing_coordinates",
            "sentinel_coordinates",
            "out_of_bounds",
        ],
        default="unknown",
    )
    return trees


def build_species_crosswalk(
    trees: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[tuple[str, str], tuple[str, str, str]]]:
    """Summarise observed source codes and build the tree-mapping lookup."""
    grouped = (
        trees.groupby(
            ["source", "species_code", "species_group"],
            dropna=False,
            observed=True,
        )
        .agg(
            n_tree_rows=("dbh_cm", "size"),
            n_live_trees=("live_valid", "sum"),
        )
        .reset_index()
    )
    mapped = [
        map_species_code(source, code, group)
        for source, code, group in zip(
            grouped["source"], grouped["species_code"], grouped["species_group"]
        )
    ]
    grouped["rsyc_species"] = [value[0] for value in mapped]
    grouped["mapping_basis"] = [value[1] for value in mapped]
    grouped["note"] = [value[2] for value in mapped]
    grouped["rsyc_model_group"] = grouped["rsyc_species"].map(_rsyc_model_group)
    grouped = grouped.sort_values(
        ["source", "n_tree_rows"], ascending=[True, False]
    ).reset_index(drop=True)
    crosswalk = grouped[
        [
            "source",
            "species_code",
            "species_group",
            "n_tree_rows",
            "n_live_trees",
            "rsyc_species",
            "rsyc_model_group",
            "mapping_basis",
            "note",
        ]
    ].rename(columns={"species_group": "source_species_group"})
    lookup = {
        (row.source, row.species_code): (
            row.rsyc_species,
            row.mapping_basis,
            row.note,
        )
        for row in grouped.itertuples(index=False)
    }
    return crosswalk, lookup


def attach_species_mapping(
    trees: pd.DataFrame,
    lookup: Mapping[tuple[str, str], tuple[str, str, str]],
) -> pd.DataFrame:
    """Map tree rows to RSYC species using the documented lookup."""
    trees = trees.copy()
    keys = trees["source"].astype(str) + "|" + trees["species_code"].astype(str)
    translated = {f"{source}|{code}": value for (source, code), value in lookup.items()}
    mapped = keys.map(translated)
    trees["rsyc_species"] = [value[0] if value else "" for value in mapped]
    trees["mapping_basis"] = [value[1] if value else "unmapped" for value in mapped]
    trees["rsyc_species"] = trees["rsyc_species"].replace("", pd.NA)
    return trees


def _aggregate_level(
    trees: pd.DataFrame, reference: pd.DataFrame, level: str
) -> pd.DataFrame:
    """Aggregate live-tree support and DBH limits for one RSYC level."""
    if level == "national":
        key = pd.Series("Canada", index=trees.index, dtype="object")
        name = pd.Series("Canada", index=trees.index, dtype="object")
    else:
        key = trees[f"{level}_key"].astype("object")
        name = trees[level].astype("object")
    frame = pd.DataFrame(
        {
            "stratum_id": key,
            "stratum_name": name,
            "species": trees["rsyc_species"].astype("object"),
            "site_id": trees["site_id"].astype("object"),
            "plot_id": trees["plot_id"].astype("object"),
            "dbh_cm": trees["dbh_cm"],
            "dbh_lt_5cm": trees["dbh_lt_5cm"].astype(bool),
            "year": trees["year"],
            "live_valid": trees["live_valid"].astype(bool),
            "mapped": trees["mapped"].fillna(False).astype(bool),
            "site_remeasured": trees["site_remeasured"].fillna(False).astype(bool),
            "province": trees["province"].astype("object"),
            "source": trees["source"].astype("object"),
        }
    )
    frame = frame[
        frame["mapped"]
        & frame["stratum_id"].notna()
        & frame["species"].notna()
        & frame["species"].ne("")
    ].copy()
    if frame.empty:
        return pd.DataFrame()

    keys = ["stratum_id", "species"]
    all_rows = (
        frame.groupby(keys, observed=True)
        .agg(
            n_tree_rows=("dbh_cm", "size"),
            n_sites_all=("site_id", "nunique"),
            n_plots_all=("plot_id", "nunique"),
        )
        .reset_index()
    )
    source_lists = (
        frame[keys + ["source"]]
        .drop_duplicates()
        .groupby(keys, observed=True)["source"]
        .agg(
            lambda values: ";".join(
                sorted(str(value) for value in values if str(value))
            )
        )
        .reset_index(name="sources")
    )

    live = frame[frame["live_valid"]].copy()
    if live.empty:
        live_agg = pd.DataFrame(columns=keys + ["n_trees"])
    else:
        live_agg = (
            live.groupby(keys, observed=True)
            .agg(
                n_sites=("site_id", "nunique"),
                n_plots=("plot_id", "nunique"),
                n_trees=("dbh_cm", "size"),
                dbh_min_cm=("dbh_cm", "min"),
                dbh_max_cm=("dbh_cm", "max"),
                year_min=("year", "min"),
                year_max=("year", "max"),
                n_years=("year", "nunique"),
                n_trees_lt_5cm=("dbh_lt_5cm", "sum"),
            )
            .reset_index()
        )
        remeasured = (
            live[live["site_remeasured"]]
            .groupby(keys, observed=True)["site_id"]
            .nunique()
            .reset_index(name="n_remeasured_sites")
        )
        live_agg = live_agg.merge(remeasured, on=keys, how="left")
        quantiles = (
            live.groupby(keys, observed=True)["dbh_cm"]
            .quantile([0.05, 0.50, 0.95])
            .unstack()
            .rename(
                columns={0.05: "dbh_p05_cm", 0.50: "dbh_p50_cm", 0.95: "dbh_p95_cm"}
            )
            .reset_index()
        )
        live_agg = live_agg.merge(quantiles, on=keys, how="left")
        province_counts = (
            live.groupby(keys + ["province"], observed=True, dropna=False)
            .size()
            .reset_index(name="n")
        )
        province_primary = (
            province_counts.sort_values(keys + ["n"], ascending=[True, True, False])
            .drop_duplicates(keys)
            .rename(columns={"province": "province_primary"})[
                keys + ["province_primary"]
            ]
        )
        province_lists = (
            live[keys + ["province"]]
            .drop_duplicates()
            .groupby(keys, observed=True)["province"]
            .agg(
                lambda values: ";".join(
                    sorted(str(value) for value in values if str(value))
                )
            )
            .reset_index(name="provinces")
        )
        live_agg = live_agg.merge(province_primary, on=keys, how="left")
        live_agg = live_agg.merge(province_lists, on=keys, how="left")

    result = all_rows.merge(source_lists, on=keys, how="left")
    result = result.merge(live_agg, on=keys, how="left")
    for column in (
        "n_sites",
        "n_plots",
        "n_trees",
        "n_remeasured_sites",
        "n_trees_lt_5cm",
        "n_years",
    ):
        result[column] = (
            pd.to_numeric(result[column], errors="coerce").fillna(0).astype(int)
        )
    result["share_dbh_lt_5cm"] = np.where(
        result["n_trees"] > 0,
        result["n_trees_lt_5cm"] / result["n_trees"],
        np.nan,
    )
    result = result.drop(columns=["n_trees_lt_5cm"])
    result["n_sources"] = (
        result["sources"]
        .fillna("")
        .map(lambda value: 0 if not value else len(str(value).split(";")))
    )

    reference_slice = reference[reference["level"].eq(level)].copy()
    reference_slice = reference_slice.drop(columns=["level"], errors="ignore")
    grid = reference_slice.merge(
        pd.DataFrame({"species": list(SPECIES_ORDER)}), how="cross"
    )
    result = grid.merge(result, on=["stratum_id", "species"], how="left")
    result["level"] = level
    for column in (
        "n_sites",
        "n_plots",
        "n_sites_all",
        "n_plots_all",
        "n_tree_rows",
        "n_trees",
        "n_remeasured_sites",
        "n_years",
        "n_sources",
    ):
        result[column] = (
            pd.to_numeric(result[column], errors="coerce").fillna(0).astype(int)
        )
    result["sources"] = result["sources"].fillna("")
    result["provinces"] = result["provinces"].fillna("")
    result["province_primary"] = result["province_primary"].fillna("")
    result["coverage_class"] = [
        classify_coverage(int(trees_count), int(plots_count))
        for trees_count, plots_count in zip(result["n_trees"], result["n_plots"])
    ]
    result["species_group"] = result["species"].map(_species_group)
    result["rsyc_model_group"] = result["species"].map(_rsyc_model_group)
    return result


def _load_rsyc_model_keys(path: Path) -> set[tuple[str, str, str]]:
    """Return national-scale, publishable volume model keys by level."""
    required = {
        "response",
        "strata_type",
        "strata_level",
        "strata_id",
        "species",
        "scale",
        "publish_ok",
        "fit_ok",
    }
    with zipfile.ZipFile(path) as archive, archive.open("RSYC_models.csv") as handle:
        models = pd.read_csv(handle, low_memory=False, usecols=sorted(required))
    missing = sorted(required.difference(models.columns))
    if missing:
        raise ValueError(f"RSYC model table missing columns: {', '.join(missing)}")
    models = models[
        models["response"].astype(str).str.lower().eq("volume")
        & models["scale"].astype(str).str.lower().eq("national")
        & models["publish_ok"].astype(str).str.upper().isin({"TRUE", "1", "T"})
        & models["fit_ok"].astype(str).str.upper().isin({"TRUE", "1", "T"})
    ].copy()
    keys: set[tuple[str, str, str]] = set()
    eco = models[models["strata_type"].astype(str).eq("eco_strata")]
    for row in eco.itertuples(index=False):
        keys.add((str(row.strata_level), str(row.strata_id), str(row.species)))
    district = models[models["strata_type"].astype(str).eq("ecodistrict")]
    for row in district.itertuples(index=False):
        keys.add(("ecodistrict", str(row.strata_id), str(row.species)))
    return keys


def _add_model_availability(
    coverage: pd.DataFrame,
    model_keys: set[tuple[str, str, str]],
) -> pd.DataFrame:
    coverage = coverage.copy()
    availability: list[bool | None] = []
    for row in coverage.itertuples(index=False):
        if row.level == "national":
            availability.append(None)
        else:
            availability.append(
                (row.level, str(row.rsyc_strata_id), str(row.species)) in model_keys
            )
    coverage["rsyc_volume_model_available"] = pd.Series(
        availability, index=coverage.index, dtype="boolean"
    )
    return coverage


def _mapping_summary(
    sites: pd.DataFrame,
    trees: pd.DataFrame,
    crosswalk: pd.DataFrame,
) -> dict[str, Any]:
    status_counts = (
        sites.groupby(["source", "coordinate_status"], observed=True)
        .size()
        .unstack(fill_value=0)
        .reset_index()
    )
    for column in ("valid", "missing", "sentinel", "out_of_bounds"):
        if column not in status_counts.columns:
            status_counts[column] = 0
    by_source = []
    for row in status_counts.itertuples(index=False):
        source_sites = sites[sites["source"].eq(row.source)]
        source_trees = trees[trees["source"].eq(row.source)]
        by_source.append(
            {
                "source": row.source,
                "sites_total": len(source_sites),
                "sites_valid_coordinates": int(row.valid),
                "sites_missing_coordinates": int(row.missing),
                "sites_sentinel_coordinates": int(row.sentinel),
                "sites_out_of_bounds": int(row.out_of_bounds),
                "sites_mapped": int(source_sites["mapped"].sum()),
                "sites_outside_rsyc": int(
                    (
                        source_sites["coordinate_status"].eq("valid")
                        & ~source_sites["mapped"]
                    ).sum()
                ),
                "sites_remeasured": int(source_sites["site_remeasured"].sum()),
                "plots_total": int(source_trees["plot_id"].nunique()),
                "tree_rows": len(source_trees),
                "live_valid_trees": int(source_trees["live_valid"].sum()),
            }
        )

    tree_status = []
    for status in (
        "mapped",
        "outside_rsyc",
        "missing_coordinates",
        "sentinel_coordinates",
        "out_of_bounds",
    ):
        if status == "outside_rsyc":
            subset = trees[
                trees["coordinate_status"].astype(str).eq("valid")
                & ~trees["mapped"].fillna(False)
            ]
        else:
            subset = trees[trees["site_mapping_status"].astype(str).eq(status)]
        tree_status.append(
            {
                "site_mapping_status": status,
                "sites": int(subset["site_id"].nunique()),
                "plots": int(subset["plot_id"].nunique()),
                "tree_rows": len(subset),
                "live_valid_trees": int(subset["live_valid"].sum()),
            }
        )

    basis = (
        crosswalk.groupby("mapping_basis", observed=True)
        .agg(
            source_codes=("species_code", "size"),
            tree_rows=("n_tree_rows", "sum"),
            live_trees=("n_live_trees", "sum"),
        )
        .reset_index()
        .sort_values("tree_rows", ascending=False)
    )
    unresolved = crosswalk[
        crosswalk["mapping_basis"].isin(["functional_group", "generic"])
    ]
    unmapped = crosswalk[crosswalk["mapping_basis"].eq("unmapped")]

    return {
        "sites_total": len(sites),
        "sites_with_valid_coordinates": int(
            sites["coordinate_status"].eq("valid").sum()
        ),
        "sites_missing_coordinates": int(
            sites["coordinate_status"].eq("missing").sum()
        ),
        "sites_sentinel_coordinates": int(
            sites["coordinate_status"].eq("sentinel").sum()
        ),
        "sites_out_of_bounds": int(
            sites["coordinate_status"].eq("out_of_bounds").sum()
        ),
        "sites_mapped": int(sites["mapped"].sum()),
        "sites_outside_rsyc": int(
            (sites["coordinate_status"].eq("valid") & ~sites["mapped"]).sum()
        ),
        "coordinate_coverage_pct": float(
            100.0 * sites["coordinate_status"].eq("valid").sum() / max(len(sites), 1)
        ),
        "rsyc_join_coverage_pct": float(
            100.0 * sites["mapped"].sum() / max(len(sites), 1)
        ),
        "by_source": by_source,
        "tree_mapping_status": tree_status,
        "species_crosswalk": {
            "source_codes_observed": len(crosswalk),
            "tree_rows_observed": int(crosswalk["n_tree_rows"].sum()),
            "live_trees_observed": int(crosswalk["n_live_trees"].sum()),
            "basis_counts": basis.to_dict(orient="records"),
            "unmapped_source_codes": len(unmapped),
            "unmapped_tree_rows": int(unmapped["n_tree_rows"].sum()),
            "unresolved_source_codes": len(unresolved),
            "unresolved_tree_rows": int(unresolved["n_tree_rows"].sum()),
            "unresolved_codes": unresolved[
                [
                    "source",
                    "species_code",
                    "n_tree_rows",
                    "n_live_trees",
                    "rsyc_species",
                    "mapping_basis",
                    "note",
                ]
            ].to_dict(orient="records"),
        },
    }


def _province_summary(
    sites: pd.DataFrame,
    trees: pd.DataFrame,
    coverage: pd.DataFrame,
    level: str = "ecodistrict",
) -> list[dict[str, Any]]:
    mapped_sites = sites[sites["mapped"]].copy()
    district_province = (
        mapped_sites.groupby("ecodistrict_key")["province"]
        .agg(lambda values: values.value_counts().index[0])
        .rename("province_primary")
    )
    district_coverage = coverage[coverage["level"].eq(level)].copy()
    district_coverage["province_primary"] = district_coverage["stratum_id"].map(
        district_province
    )
    class_counts = (
        district_coverage.dropna(subset=["province_primary"])
        .groupby(["province_primary", "coverage_class"], observed=True)
        .size()
        .unstack(fill_value=0)
    )
    for column in COVERAGE_CLASSES:
        if column not in class_counts.columns:
            class_counts[column] = 0
    rows = []
    for province in PROVINCES:
        province_sites = mapped_sites[mapped_sites["province"].eq(province)]
        province_trees = trees[trees["province"].eq(province) & trees["mapped"]]
        classes = (
            class_counts.loc[province]
            if province in class_counts.index
            else pd.Series({name: 0 for name in COVERAGE_CLASSES})
        )
        rows.append(
            {
                "province": province,
                "sites_total": len(sites[sites["province"].eq(province)]),
                "sites_mapped": len(province_sites),
                "sites_remeasured": int(province_sites["site_remeasured"].sum()),
                "ecodistricts_with_sites": int(
                    province_sites["ecodistrict_key"].nunique()
                ),
                "plots": int(province_trees["plot_id"].nunique()),
                "tree_rows": len(province_trees),
                "live_valid_trees": int(province_trees["live_valid"].sum()),
                "direct_fit": int(classes.get("direct_fit", 0)),
                "pooled_fit": int(classes.get("pooled_fit", 0)),
                "no_data": int(classes.get("no_data", 0)),
                "sources": ";".join(
                    sorted(province_sites["source"].dropna().unique().tolist())
                ),
            }
        )
    return rows


def _level_summary(coverage: pd.DataFrame) -> list[dict[str, Any]]:
    rows = []
    for level in LEVEL_ORDER:
        subset = coverage[coverage["level"].eq(level)]
        if subset.empty:
            continue
        classes = subset["coverage_class"].value_counts()
        species_with_data = subset.loc[subset["n_sites"].gt(0), "species"].nunique()
        species_with_direct = subset.loc[
            subset["coverage_class"].eq("direct_fit"), "species"
        ].nunique()
        species_with_pooled = subset.loc[
            subset["coverage_class"].eq("pooled_fit"), "species"
        ].nunique()
        strata_with_data = subset.loc[subset["n_sites"].gt(0), "stratum_id"].nunique()
        strata_with_direct = subset.loc[
            subset["coverage_class"].eq("direct_fit"), "stratum_id"
        ].nunique()
        strata_with_pooled = subset.loc[
            subset["coverage_class"].eq("pooled_fit"), "stratum_id"
        ].nunique()
        strata_with_sites = set(
            subset.loc[subset["n_sites_all"].gt(0), "stratum_id"].astype(str)
        )
        data_rows = subset[subset["stratum_id"].astype(str).isin(strata_with_sites)]
        data_classes = data_rows["coverage_class"].value_counts()
        direct_rows = subset["coverage_class"].eq("direct_fit")
        model_available = subset["rsyc_volume_model_available"].eq(True)
        rows.append(
            {
                "level": level,
                "strata_total": int(subset["stratum_id"].nunique()),
                "strata_with_data": int(strata_with_data),
                "strata_with_sites": len(strata_with_sites),
                "strata_with_direct": int(strata_with_direct),
                "strata_with_pooled": int(strata_with_pooled),
                "rows": len(subset),
                "rows_in_strata_with_sites": len(data_rows),
                "direct_fit": int(classes.get("direct_fit", 0)),
                "pooled_fit": int(classes.get("pooled_fit", 0)),
                "no_data": int(classes.get("no_data", 0)),
                "direct_fit_in_strata_with_sites": int(
                    data_classes.get("direct_fit", 0)
                ),
                "pooled_fit_in_strata_with_sites": int(
                    data_classes.get("pooled_fit", 0)
                ),
                "no_data_in_strata_with_sites": int(data_classes.get("no_data", 0)),
                "species_total": int(subset["species"].nunique()),
                "species_with_data": int(species_with_data),
                "species_with_direct": int(species_with_direct),
                "species_with_pooled": int(species_with_pooled),
                "direct_with_rsyc_model": (
                    int((direct_rows & model_available).sum())
                    if level != "national"
                    else None
                ),
                "direct_without_rsyc_model": (
                    int((direct_rows & ~model_available).sum())
                    if level != "national"
                    else None
                ),
                "no_data_with_rsyc_model": (
                    int(
                        (subset["coverage_class"].eq("no_data") & model_available).sum()
                    )
                    if level != "national"
                    else None
                ),
                "tree_rows_mapped_species": int(subset["n_tree_rows"].sum()),
                "live_trees_mapped_species": int(subset["n_trees"].sum()),
            }
        )
    return rows


def _gap_summary(
    coverage: pd.DataFrame,
    sites: pd.DataFrame,
    crosswalk: pd.DataFrame,
    top_n: int,
) -> dict[str, Any]:
    mapped_sites = sites[sites["mapped"]]
    ecozones_with_sites = set(mapped_sites["ecozone"].dropna().astype(str))
    all_ecozones = set(
        coverage.loc[coverage["level"].eq("ecozone"), "stratum_id"].astype(str)
    )
    districts = coverage[coverage["level"].eq("ecodistrict")]
    districts_with_sites = set(
        districts.loc[districts["n_sites_all"].gt(0), "stratum_id"].astype(str)
    )
    all_districts = set(districts["stratum_id"].astype(str))

    species_totals = (
        coverage[coverage["level"].eq("national")]
        .groupby("species", observed=True)
        .agg(n_trees=("n_trees", "sum"), n_plots=("n_plots", "sum"))
        .reset_index()
    )
    species_without_trees = species_totals.loc[
        species_totals["n_trees"].eq(0), "species"
    ].tolist()

    district_species = districts.copy()
    district_species["has_data"] = district_species["n_sites"].gt(0)
    district_species["is_direct"] = district_species["coverage_class"].eq("direct_fit")
    district_species["is_pooled"] = district_species["coverage_class"].eq("pooled_fit")
    district_species["is_no_data"] = district_species["coverage_class"].eq("no_data")
    district_species["is_no_data_with_data"] = (
        district_species["is_no_data"] & district_species["has_data"]
    )
    species_gap = (
        district_species.groupby("species", observed=True)
        .agg(
            n_districts_total=("stratum_id", "nunique"),
            n_districts_with_data=("has_data", "sum"),
            n_direct=("is_direct", "sum"),
            n_pooled=("is_pooled", "sum"),
            n_no_data=("is_no_data", "sum"),
            n_no_data_with_data=("is_no_data_with_data", "sum"),
            n_trees=("n_trees", "sum"),
        )
        .reset_index()
        .sort_values(["n_no_data_with_data", "n_trees"], ascending=[False, True])
    )
    stratum_gap = (
        district_species.groupby(["stratum_id", "ecozone"], observed=True)
        .agg(
            n_species_with_data=("has_data", "sum"),
            n_direct=("is_direct", "sum"),
            n_pooled=("is_pooled", "sum"),
            n_no_data=("is_no_data", "sum"),
            n_no_data_with_data=("is_no_data_with_data", "sum"),
            n_sites=("n_sites", "max"),
            n_trees=("n_trees", "sum"),
        )
        .reset_index()
        .sort_values(["n_no_data", "n_sites"], ascending=[False, True])
    )
    data_bearing_strata = set(
        district_species.loc[
            district_species["n_sites_all"].gt(0), "stratum_id"
        ].astype(str)
    )
    data_bearing_gap = stratum_gap[
        stratum_gap["stratum_id"].astype(str).isin(data_bearing_strata)
    ].copy()
    data_bearing_gap = data_bearing_gap.sort_values(
        ["n_no_data_with_data", "n_species_with_data"],
        ascending=[False, False],
    )
    no_direct_strata = stratum_gap[
        stratum_gap["stratum_id"].astype(str).isin(data_bearing_strata)
        & stratum_gap["n_direct"].eq(0)
    ]
    present_provinces = sorted(mapped_sites["province"].dropna().unique().tolist())
    all_species = set(district_species["species"].dropna().astype(str))
    direct_species = set(
        district_species.loc[district_species["is_direct"], "species"].astype(str)
    )
    species_with_no_direct = sorted(all_species.difference(direct_species))
    return {
        "provinces_present": present_provinces,
        "provinces_missing": [p for p in PROVINCES if p not in present_provinces],
        "ecozones_total": len(all_ecozones),
        "ecozones_with_sites": len(ecozones_with_sites),
        "ecozones_without_sites": sorted(all_ecozones.difference(ecozones_with_sites)),
        "ecodistricts_total": len(all_districts),
        "ecodistricts_with_sites": len(districts_with_sites),
        "ecodistricts_without_sites": len(
            all_districts.difference(districts_with_sites)
        ),
        "ecodistricts_without_sites_sample": sorted(
            all_districts.difference(districts_with_sites)
        )[:top_n],
        "ecodistricts_with_sites_but_no_direct": len(no_direct_strata),
        "ecodistricts_with_sites_but_no_direct_sample": sorted(
            no_direct_strata["stratum_id"].astype(str).tolist()
        )[:top_n],
        "species_without_trees": species_without_trees,
        "species_without_direct_ecodistrict": species_with_no_direct,
        "top_species_no_data_ecodistricts": species_gap.head(top_n).to_dict(
            orient="records"
        ),
        "top_ecodistricts_no_data_species": stratum_gap.head(top_n).to_dict(
            orient="records"
        ),
        "top_data_bearing_ecodistricts_no_data_species": data_bearing_gap.head(
            top_n
        ).to_dict(orient="records"),
    }


def _dbh_limit_summary(trees: pd.DataFrame) -> list[dict[str, Any]]:
    rows = []
    for source, frame in trees.groupby("source", observed=True):
        live = frame.loc[frame["live_valid"], "dbh_cm"].dropna()
        if live.empty:
            continue
        rows.append(
            {
                "source": source,
                "live_trees": len(live),
                "dbh_min_cm": float(live.min()),
                "dbh_p05_cm": float(live.quantile(0.05)),
                "dbh_p50_cm": float(live.quantile(0.50)),
                "dbh_p95_cm": float(live.quantile(0.95)),
                "dbh_max_cm": float(live.max()),
                "share_dbh_lt_5cm": float((live < 5.0).mean()),
            }
        )
    return rows


def _stage(config: CoverageConfig, message: str, started: float) -> None:
    if config.verbose:
        print(f"[coverage] {message} ({time.time() - started:.1f}s)", file=sys.stderr)


def run(config: CoverageConfig) -> dict[str, Any]:
    """Execute the coverage audit and write all artifacts."""
    started = time.time()
    assert_no_unexpected_deletions(config.repo_root)
    _validate_species_maps()
    config.out_dir.mkdir(parents=True, exist_ok=True)

    sites = load_sites(config.sites_path)
    _stage(config, f"loaded {len(sites):,} sites", started)
    layers = _read_ecostrat_layers(config.ecostrat_path)
    reference = _stratum_reference(layers)
    sites, join_stats = assign_sites_to_ecostrata(sites, layers)
    _stage(config, f"mapped {int(sites['mapped'].sum()):,} sites to RSYC", started)

    trees = load_trees(config.trees_path, sites)
    _stage(config, f"loaded {len(trees):,} tree rows", started)
    crosswalk, species_lookup = build_species_crosswalk(trees)
    trees = attach_species_mapping(trees, species_lookup)
    _stage(config, f"mapped {len(crosswalk):,} source species codes", started)
    if crosswalk["mapping_basis"].eq("unmapped").any():
        unmapped = crosswalk[crosswalk["mapping_basis"].eq("unmapped")]
        raise ValueError(
            "unmapped source species codes: "
            + ", ".join(
                f"{row.source}:{row.species_code}" for row in unmapped.itertuples()
            )
        )

    coverage_frames = []
    for level in LEVEL_ORDER:
        coverage_frames.append(_aggregate_level(trees, reference, level))
        _stage(config, f"aggregated {level}", started)
    coverage = pd.concat(coverage_frames, ignore_index=True, sort=False)
    tree_totals = {
        level: int(coverage.loc[coverage["level"].eq(level), "n_trees"].sum())
        for level in LEVEL_ORDER
    }
    row_totals = {
        level: int(coverage.loc[coverage["level"].eq(level), "n_tree_rows"].sum())
        for level in LEVEL_ORDER
    }
    if len(set(tree_totals.values())) != 1 or len(set(row_totals.values())) != 1:
        raise RuntimeError(
            "level rollups are not additive: "
            f"live trees={tree_totals}, tree rows={row_totals}"
        )
    model_keys = _load_rsyc_model_keys(config.models_path)
    coverage = _add_model_availability(coverage, model_keys)

    coverage = coverage[
        [
            "level",
            "stratum_id",
            "stratum_name",
            "rsyc_strata_id",
            "ecozone",
            "ecoprovince",
            "ecoregion",
            "ecodistrict",
            "ecozone_id",
            "ecoprovince_id",
            "ecoregion_id",
            "ecodistrict_id",
            "n_ecodistricts_total",
            "species",
            "species_group",
            "rsyc_model_group",
            "n_sites",
            "n_plots",
            "n_sites_all",
            "n_plots_all",
            "n_tree_rows",
            "n_trees",
            "n_remeasured_sites",
            "dbh_min_cm",
            "dbh_p05_cm",
            "dbh_p50_cm",
            "dbh_p95_cm",
            "dbh_max_cm",
            "share_dbh_lt_5cm",
            "year_min",
            "year_max",
            "n_years",
            "province_primary",
            "provinces",
            "sources",
            "n_sources",
            "coverage_class",
            "rsyc_volume_model_available",
        ]
    ].copy()
    level_rank = {level: index for index, level in enumerate(LEVEL_ORDER)}
    coverage["_level_rank"] = coverage["level"].map(level_rank)
    coverage = coverage.sort_values(
        ["_level_rank", "stratum_id", "species"], kind="stable"
    ).drop(columns=["_level_rank"])
    for column in ("ecozone_id", "ecoregion_id", "ecodistrict_id"):
        coverage[column] = (
            pd.to_numeric(coverage[column], errors="coerce")
            .astype("Float64")
            .astype("Int64")
        )
    coverage["ecoprovince_id"] = pd.to_numeric(
        coverage["ecoprovince_id"], errors="coerce"
    ).astype("Float64")
    for column in ("year_min", "year_max"):
        coverage[column] = (
            pd.to_numeric(coverage[column], errors="coerce")
            .astype("Float64")
            .astype("Int64")
        )

    coverage_path = config.out_dir / "coverage.csv"
    coverage.to_csv(coverage_path, index=False, lineterminator="\n")
    crosswalk_path = config.out_dir / "species_crosswalk_national.csv"
    crosswalk.to_csv(crosswalk_path, index=False, lineterminator="\n")

    mapping = _mapping_summary(sites, trees, crosswalk)
    mapping.update(join_stats)
    level_summary = _level_summary(coverage)
    province_summary = _province_summary(sites, trees, coverage)
    gaps = _gap_summary(coverage, sites, crosswalk, config.top_n_gaps)
    dbh_by_source = _dbh_limit_summary(trees)

    by_source_province = (
        trees.groupby(["source", "province"], observed=True)
        .agg(
            sites=("site_id", "nunique"),
            plots=("plot_id", "nunique"),
            tree_rows=("dbh_cm", "size"),
            live_valid_trees=("live_valid", "sum"),
        )
        .reset_index()
    )
    for column in ("sites", "plots", "tree_rows", "live_valid_trees"):
        by_source_province[column] = by_source_province[column].astype(int)

    runtime = time.time() - started
    report_path = config.out_dir / "COVERAGE_REPORT.md"
    heatmap_path = config.out_dir / "coverage_heatmap.png"
    summary_path = config.out_dir / "coverage_summary.json"
    manifest_path = config.out_dir / "manifest.json"
    summary = {
        "artifact": "national_plot_rsyc_coverage",
        "artifact_version": ARTIFACT_VERSION,
        "generated_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "runtime_seconds": runtime,
        "thresholds": THRESHOLDS,
        "method": {
            "plot_definition": (
                "physical plot unit: FAIB plot prefix from tree_id, MAGPlot/WFS "
                "ground-plot prefix from visit_id, QC PEP and NL FAL site_id"
            ),
            "tree_support_definition": (
                "live status with finite DBH in (0, 500] cm; n_tree_rows retains "
                "all tree records for the species"
            ),
            "coordinate_validity": (
                "finite latitude 40-84 N and longitude 142-52 W; the -1/-1 "
                "sentinel is reported separately"
            ),
            "spatial_join": "EPSG:4326 points transformed to EPSG:3978; predicate=within",
            "coverage_rule": (
                "direct_fit if n_trees>=100 and n_plots>=5; pooled_fit if not direct "
                "and (30<=n_trees<100 or 2<=n_plots<5); otherwise no_data"
            ),
        },
        "inputs": {
            "sites": {
                "path": _display_path(config.sites_path, config.repo_root),
                "sha256": _sha256(config.sites_path),
                "rows": len(sites),
            },
            "visits": {
                "path": _display_path(config.visits_path, config.repo_root),
                "sha256": _sha256(config.visits_path),
            },
            "trees": {
                "path": _display_path(config.trees_path, config.repo_root),
                "sha256": _sha256(config.trees_path),
                "rows": len(trees),
            },
            "rsyc_ecostrat": {
                "path": _display_path(config.ecostrat_path, config.repo_root),
                "sha256": _sha256(config.ecostrat_path),
            },
            "rsyc_models": {
                "path": _display_path(config.models_path, config.repo_root),
                "sha256": _sha256(config.models_path),
            },
        },
        "mapping": mapping,
        "coverage_by_level": level_summary,
        "province_coverage": province_summary,
        "source_province_coverage": by_source_province.to_dict(orient="records"),
        "dbh_limits_by_source": dbh_by_source,
        "gaps": gaps,
        "outputs": {
            "coverage_csv": _display_path(coverage_path, config.repo_root),
            "species_crosswalk_csv": _display_path(crosswalk_path, config.repo_root),
            "coverage_summary_json": _display_path(summary_path, config.repo_root),
            "coverage_report_md": _display_path(report_path, config.repo_root),
            "coverage_heatmap_png": _display_path(heatmap_path, config.repo_root),
            "manifest_json": _display_path(manifest_path, config.repo_root),
            "metadata_csv": _display_path(config.metadata_path, config.repo_root),
        },
    }
    write_report(summary, coverage, crosswalk, report_path)
    _stage(config, f"wrote {report_path.name}", started)
    write_heatmap(coverage, heatmap_path)
    _stage(config, f"wrote {heatmap_path.name}", started)
    write_metadata(summary, config.metadata_path)
    summary_path.write_text(
        json.dumps(_jsonable(summary), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    write_manifest(
        config,
        summary,
        [
            coverage_path,
            crosswalk_path,
            summary_path,
            report_path,
            heatmap_path,
            config.metadata_path,
        ],
    )
    _stage(config, f"wrote {manifest_path.name}", started)
    return summary


def _format_value(value: Any, digits: int = 1) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    if isinstance(value, (int, np.integer)):
        return f"{int(value):,}"
    if isinstance(value, (float, np.floating)):
        return f"{float(value):,.{digits}f}"
    return str(value)


def _md_table(
    rows: Iterable[Mapping[str, Any]],
    columns: Sequence[tuple[str, str, int]],
) -> str:
    header = "| " + " | ".join(header for _, header, _ in columns) + " |"
    separator = "|" + "|".join("---" for _ in columns) + "|"
    lines = [header, separator]
    for row in rows:
        cells = [_format_value(row.get(key), digits) for key, _, digits in columns]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _pct(numerator: float, denominator: float) -> str:
    if not denominator:
        return "n/a"
    return f"{100.0 * numerator / denominator:.1f}%"


def write_heatmap(coverage: pd.DataFrame, path: Path) -> None:
    """Write the compact ecodistrict-support heatmap."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    district = coverage[coverage["level"].eq("ecodistrict")].copy()
    national = (
        coverage[coverage["level"].eq("national")]
        .set_index("species")["n_trees"]
        .to_dict()
    )
    species_order = sorted(
        SPECIES_ORDER,
        key=lambda code: (-int(national.get(code, 0)), code),
    )
    class_specs = (
        ("direct_fit", "Direct fit", "Greens"),
        ("pooled_fit", "Pooled fit", "Oranges"),
        ("no_data", "No data", "Greys"),
    )
    groupings = (("ecozone", "Ecozone"), ("ecoprovince", "Ecoprovince"))
    fig = plt.figure(figsize=(24, 15))
    grid = fig.add_gridspec(
        2,
        3,
        height_ratios=(12, 36),
        left=0.12,
        right=0.99,
        top=0.90,
        bottom=0.17,
        wspace=0.10,
        hspace=0.10,
    )
    images: dict[str, Any] = {}
    for row_index, (grouping, grouping_label) in enumerate(groupings):
        counts = (
            district.groupby(grouping, observed=True)["stratum_id"]
            .nunique()
            .sort_values(ascending=False)
        )
        row_order = counts.index.astype(str).tolist()
        if grouping == "ecoprovince":
            ecozone_lookup = (
                district.dropna(subset=[grouping])
                .groupby(grouping, observed=True)["ecozone"]
                .agg(lambda values: values.value_counts().index[0])
            )
            duplicates = {name for name in row_order if row_order.count(name) > 1}
            row_labels = [
                f"{str(ecozone_lookup.get(name, ''))[:4]}: {name}"
                if name in duplicates
                else name
                for name in row_order
            ]
        else:
            row_labels = row_order
        for column_index, (coverage_class, label, cmap) in enumerate(class_specs):
            axis = fig.add_subplot(grid[row_index, column_index])
            subset = district[district["coverage_class"].eq(coverage_class)]
            matrix = (
                subset.groupby([grouping, "species"], observed=True)
                .size()
                .unstack(fill_value=0)
                .reindex(index=row_order, columns=list(species_order), fill_value=0)
            )
            values = matrix.to_numpy(dtype=float)
            vmax = max(1.0, float(values.max()) if values.size else 1.0)
            image = axis.imshow(
                values,
                aspect="auto",
                interpolation="nearest",
                cmap=cmap,
                vmin=0.0,
                vmax=vmax,
            )
            images[f"{grouping}:{coverage_class}"] = image
            axis.set_yticks(range(len(row_order)))
            if column_index == 0:
                axis.set_yticklabels(row_labels, fontsize=7.5)
            else:
                axis.set_yticklabels([])
            axis.set_xticks(range(len(species_order)))
            if row_index == len(groupings) - 1:
                axis.set_xticklabels(
                    species_order, rotation=90, fontsize=7.5, ha="center"
                )
            else:
                axis.set_xticklabels([])
            axis.tick_params(axis="both", length=2, pad=2)
            axis.set_title(label, fontsize=13, pad=6)
            if column_index == 0:
                axis.set_ylabel(grouping_label, fontsize=11)
    for column_index, (coverage_class, label, _) in enumerate(class_specs):
        axis = fig.add_axes([0.14 + 0.285 * column_index, 0.075, 0.18, 0.012])
        image = images[f"ecoprovince:{coverage_class}"]
        colorbar = fig.colorbar(image, cax=axis, orientation="horizontal")
        colorbar.set_label("Ecodistricts" if column_index == 0 else "", fontsize=8)
        colorbar.ax.tick_params(labelsize=7)
        fig.text(
            0.14 + 0.285 * column_index,
            0.095,
            label,
            fontsize=9,
            weight="bold",
        )
    fig.suptitle(
        "RSYC ecodistrict × species coverage classes",
        fontsize=18,
        y=0.965,
    )
    fig.text(
        0.5,
        0.935,
        "Cell = number of RSYC ecodistricts in the row unit whose species record "
        "is classified direct_fit, pooled_fit, or no_data",
        ha="center",
        fontsize=10,
    )
    fig.text(
        0.12,
        0.025,
        "Direct: n_trees >= 100 and n_plots >= 5. Pooled: not direct and "
        "(30 <= n_trees < 100 or 2 <= n_plots < 5). Plot = physical plot unit.",
        fontsize=8.5,
    )
    fig.savefig(
        path,
        dpi=200,
        facecolor="white",
        bbox_inches="tight",
        pad_inches=0.15,
    )
    plt.close(fig)


def write_report(
    summary: Mapping[str, Any],
    coverage: pd.DataFrame,
    crosswalk: pd.DataFrame,
    path: Path,
) -> None:
    """Write the narrative coverage report with dynamically computed tables."""
    mapping = summary["mapping"]
    levels = summary["coverage_by_level"]
    provinces = summary["province_coverage"]
    gaps = summary["gaps"]
    dbh_rows = summary["dbh_limits_by_source"]
    thresholds = summary["thresholds"]

    by_source_rows = []
    for row in mapping["by_source"]:
        by_source_rows.append(
            {
                "source": row["source"],
                "sites_total": row["sites_total"],
                "sites_valid_coordinates": row["sites_valid_coordinates"],
                "sites_missing_coordinates": row["sites_missing_coordinates"],
                "sites_sentinel_coordinates": row["sites_sentinel_coordinates"],
                "sites_outside_rsyc": row["sites_outside_rsyc"],
                "sites_mapped": row["sites_mapped"],
                "sites_remeasured": row["sites_remeasured"],
                "plots_total": row["plots_total"],
                "tree_rows": row["tree_rows"],
                "live_valid_trees": row["live_valid_trees"],
            }
        )

    status_rows = [
        {
            "status": row["site_mapping_status"],
            "sites": row["sites"],
            "plots": row["plots"],
            "tree_rows": row["tree_rows"],
            "live_trees": row["live_valid_trees"],
        }
        for row in mapping["tree_mapping_status"]
    ]

    crosswalk_rows = []
    for source, frame in crosswalk.groupby("source", observed=True):
        basis = frame["mapping_basis"].value_counts()
        tree_rows = frame.groupby("mapping_basis", observed=True)["n_tree_rows"].sum()
        crosswalk_rows.append(
            {
                "source": source,
                "codes": len(frame),
                "exact": int(basis.get("exact_species", 0)),
                "alias": int(basis.get("alias", 0)),
                "group": int(basis.get("functional_group", 0)),
                "generic": int(basis.get("generic", 0)),
                "unmapped": int(basis.get("unmapped", 0)),
                "tree_rows": int(frame["n_tree_rows"].sum()),
                "exact_tree_rows": int(tree_rows.get("exact_species", 0)),
                "unresolved_tree_rows": int(
                    tree_rows.get("functional_group", 0) + tree_rows.get("generic", 0)
                ),
            }
        )
    crosswalk_rows.sort(key=lambda row: row["tree_rows"], reverse=True)

    unresolved = crosswalk[
        crosswalk["mapping_basis"].isin(["functional_group", "generic"])
    ].sort_values(["n_live_trees", "n_tree_rows"], ascending=False)
    unresolved_rows = [
        {
            "source": row.source,
            "code": row.species_code if row.species_code else "<missing>",
            "group": row.source_species_group,
            "rsyc": row.rsyc_species,
            "basis": row.mapping_basis,
            "tree_rows": row.n_tree_rows,
            "live_trees": row.n_live_trees,
        }
        for row in unresolved.head(15).itertuples(index=False)
    ]

    level_rows = [
        {
            "level": row["level"],
            "strata": row["strata_total"],
            "with_sites": row["strata_with_sites"],
            "rows": row["rows"],
            "direct": row["direct_fit"],
            "pooled": row["pooled_fit"],
            "no_data": row["no_data"],
            "direct_data": row["direct_fit_in_strata_with_sites"],
            "pooled_data": row["pooled_fit_in_strata_with_sites"],
            "no_data_data": row["no_data_in_strata_with_sites"],
            "direct_model": row["direct_with_rsyc_model"],
            "direct_no_model": row["direct_without_rsyc_model"],
            "no_data_model": row["no_data_with_rsyc_model"],
        }
        for row in levels
    ]

    province_rows = [
        {
            "province": row["province"],
            "sites_mapped": row["sites_mapped"],
            "sites_remeasured": row["sites_remeasured"],
            "ecodistricts": row["ecodistricts_with_sites"],
            "plots": row["plots"],
            "tree_rows": row["tree_rows"],
            "live_trees": row["live_valid_trees"],
            "direct": row["direct_fit"],
            "pooled": row["pooled_fit"],
            "no_data": row["no_data"],
            "sources": row["sources"],
        }
        for row in provinces
        if row["sites_mapped"] or row["tree_rows"]
    ]

    dbh_table_rows = [
        {
            "source": row["source"],
            "live_trees": row["live_trees"],
            "dbh_min_cm": row["dbh_min_cm"],
            "dbh_p05_cm": row["dbh_p05_cm"],
            "dbh_p50_cm": row["dbh_p50_cm"],
            "dbh_p95_cm": row["dbh_p95_cm"],
            "dbh_max_cm": row["dbh_max_cm"],
            "share_dbh_lt_5cm": 100.0 * row["share_dbh_lt_5cm"],
        }
        for row in dbh_rows
    ]

    species_gap_rows = [
        {
            "species": row["species"],
            "districts_with_data": row["n_districts_with_data"],
            "direct": row["n_direct"],
            "pooled": row["n_pooled"],
            "no_data_with_data": row["n_no_data_with_data"],
            "no_data": row["n_no_data"],
            "trees": row["n_trees"],
        }
        for row in gaps["top_species_no_data_ecodistricts"][:10]
    ]
    stratum_gap_rows = [
        {
            "ecodistrict": row["stratum_id"].split("/")[-1],
            "ecozone": row["ecozone"],
            "species_with_data": row["n_species_with_data"],
            "direct": row["n_direct"],
            "pooled": row["n_pooled"],
            "no_data_with_data": row["n_no_data_with_data"],
            "no_data": row["n_no_data"],
            "sites": row["n_sites"],
            "trees": row["n_trees"],
        }
        for row in gaps["top_data_bearing_ecodistricts_no_data_species"][:10]
    ]

    pooled_by_species = (
        coverage[
            coverage["level"].eq("ecodistrict")
            & coverage["coverage_class"].eq("pooled_fit")
        ]
        .groupby("species", observed=True)
        .size()
        .sort_values(ascending=False)
        .head(5)
    )
    pooled_species_text = ", ".join(
        f"`{species}` ({count})" for species, count in pooled_by_species.items()
    )
    national = next(row for row in levels if row["level"] == "national")
    ecodistrict = next(row for row in levels if row["level"] == "ecodistrict")
    ecoregion = next(row for row in levels if row["level"] == "ecoregion")
    ecoprovince = next(row for row in levels if row["level"] == "ecoprovince")
    ecozone = next(row for row in levels if row["level"] == "ecozone")
    crosswalk_summary = mapping["species_crosswalk"]
    exact_basis = next(
        row
        for row in crosswalk_summary["basis_counts"]
        if row["mapping_basis"] == "exact_species"
    )
    alias_basis = next(
        (
            row
            for row in crosswalk_summary["basis_counts"]
            if row["mapping_basis"] == "alias"
        ),
        {"source_codes": 0, "tree_rows": 0},
    )
    group_basis = next(
        row
        for row in crosswalk_summary["basis_counts"]
        if row["mapping_basis"] == "functional_group"
    )
    generic_basis = next(
        row
        for row in crosswalk_summary["basis_counts"]
        if row["mapping_basis"] == "generic"
    )
    unresolved_live = int(group_basis["live_trees"] + generic_basis["live_trees"])
    dbh_share_min = min(row["share_dbh_lt_5cm"] for row in dbh_rows)
    dbh_share_max = max(row["share_dbh_lt_5cm"] for row in dbh_rows)
    qc_dbh = next((row for row in dbh_rows if row["source"] == "qc_pep"), None)
    nl_dbh = next((row for row in dbh_rows if row["source"] == "nl_fal"), None)

    lines: list[str] = []
    lines.append("# National plot-to-RSYC-stratum coverage audit")
    lines.append("")
    lines.append(f"**Generated:** {summary['generated_at_utc']}  ")
    lines.append(f"**Artifact version:** `{summary['artifact_version']}`  ")
    lines.append("**Code:** `workflows/national/assortments/national_coverage.py`  ")
    lines.append(
        "**Inputs:** `data/derived/fvsca_calibration/v0/`; "
        "`data/raw/national/rsyc/RSYC_ecostrat.gpkg`; "
        "`data/raw/national/rsyc/RSYC_models.zip`  "
    )
    lines.append(f"**Runtime:** {summary['runtime_seconds']:.1f} s")
    lines.append("")
    lines.append("## Scope")
    lines.append("")
    lines.append(
        "This is a coverage inventory, not a distribution fit. It maps every "
        "harmonized plot site with a usable coordinate to the RSYC ecodistrict "
        "hierarchy, maps source species codes to RSYC species or model groups, "
        "and counts independent site, plot, and tree support for every "
        "stratum × species cell. The thresholds are pre-registered:"
    )
    lines.append("")
    lines.append(
        f"- `direct_fit`: `n_trees >= {thresholds['direct_min_trees']}` and "
        f"`n_plots >= {thresholds['direct_min_plots']}`."
    )
    lines.append(
        f"- `pooled_fit`: not direct and either "
        f"`{thresholds['pooled_min_trees']} <= n_trees < {thresholds['pooled_max_trees'] + 1}` "
        f"or `{thresholds['pooled_min_plots']} <= n_plots < {thresholds['pooled_max_plots'] + 1}`."
    )
    lines.append("- `no_data`: everything else.")
    lines.append("")
    lines.append(
        "`n_trees` is live trees with finite DBH in (0, 500] cm. `n_tree_rows` "
        "retains every tree record, including dead, missing, and non-positive "
        "DBH rows. `n_sites` and `n_plots` count independent support for live "
        "trees; `n_sites_all` and `n_plots_all` count any tree row. A plot is a "
        "physical plot unit: the FAIB plot prefix from `tree_id`, the MAGPlot / "
        "WFS ground-plot prefix from `visit_id`, and the QC PEP or NL FAL "
        "`site_id`. The pooled rule is applied literally, so a record with a "
        "single plot and 100+ trees, or 20 trees on six plots, remains "
        "`no_data` rather than being promoted on one dimension alone. "
        "`year_min`/`year_max`/`n_years` use the measurement year of live "
        "trees. `rsyc_strata_id` applies the single documented GeoPackage → "
        "model-table spelling correction (Betsiamiles Plateau → Betsiamites "
        "Plateau, ecodistrict 438) for RSYC model joins; `stratum_id` keeps "
        "the GeoPackage spelling."
    )
    lines.append("")
    lines.append("## Mapping")
    lines.append("")
    lines.append(
        f"Of **{mapping['sites_total']:,}** harmonized sites, "
        f"**{mapping['sites_with_valid_coordinates']:,}** "
        f"({mapping['coordinate_coverage_pct']:.1f}%) have a usable coordinate, "
        f"and **{mapping['sites_mapped']:,}** "
        f"({mapping['rsyc_join_coverage_pct']:.1f}%) fall inside an RSYC "
        f"ecodistrict. The remaining sites are reported, not dropped: "
        f"**{mapping['sites_missing_coordinates']:,}** have no coordinate, "
        f"**{mapping['sites_sentinel_coordinates']:,}** carry the `-1/-1` "
        f"sentinel, and **{mapping['sites_outside_rsyc']:,}** have a valid "
        f"coordinate but fall outside the forested RSYC ecozones. The spatial "
        f"join is a point-in-polygon operation in EPSG:3978; it took "
        f"{mapping['runtime_seconds']:.2f} s for the valid sites and produced "
        f"{mapping['duplicate_boundary_matches']} duplicate boundary matches."
    )
    lines.append("")
    lines.append("### Sites and trees by source")
    lines.append("")
    lines.append(
        _md_table(
            by_source_rows,
            [
                ("source", "Source", 0),
                ("sites_total", "Sites", 0),
                ("sites_valid_coordinates", "Valid coords", 0),
                ("sites_missing_coordinates", "Missing", 0),
                ("sites_sentinel_coordinates", "Sentinel", 0),
                ("sites_outside_rsyc", "Outside RSYC", 0),
                ("sites_mapped", "Mapped", 0),
                ("sites_remeasured", "Remeasured", 0),
                ("plots_total", "Plots", 0),
                ("tree_rows", "Tree rows", 0),
                ("live_valid_trees", "Live valid trees", 0),
            ],
        )
    )
    lines.append("")
    lines.append("### Tree rows by mapping status")
    lines.append("")
    lines.append(
        _md_table(
            status_rows,
            [
                ("status", "Status", 0),
                ("sites", "Sites", 0),
                ("plots", "Plots", 0),
                ("tree_rows", "Tree rows", 0),
                ("live_trees", "Live valid trees", 0),
            ],
        )
    )
    lines.append("")
    lines.append(
        f"The mapped count covers **{mapping['tree_mapping_status'][0]['live_valid_trees']:,}** "
        f"live valid trees; the excluded strata retain "
        f"**{sum(row['live_valid_trees'] for row in mapping['tree_mapping_status'][1:]):,}** "
        f"live valid trees that cannot enter an RSYC-stratified fit without a "
        f"coordinate repair or a non-RSYC pooling decision."
    )
    lines.append("")
    lines.append("## Species crosswalk")
    lines.append("")
    lines.append(
        f"The audit observed **{crosswalk_summary['source_codes_observed']}** "
        f"distinct source species codes. **{exact_basis['source_codes']}** map "
        f"directly to an RSYC scientific species "
        f"({exact_basis['tree_rows']:,} tree rows), "
        f"**{alias_basis['source_codes']}** use a documented nearest-taxon "
        f"alias (BC black cottonwood → `POPU.BAL`), "
        f"**{group_basis['source_codes']}** map only to the RSYC "
        f"`coniferous`/`broadleaf` model groups "
        f"({group_basis['tree_rows']:,} tree rows), and "
        f"**{generic_basis['source_codes']}** map to `generic` "
        f"({generic_basis['tree_rows']:,} tree rows). "
        f"**{crosswalk_summary['unmapped_source_codes']}** codes are unmapped."
    )
    lines.append("")
    lines.append(
        _md_table(
            crosswalk_rows,
            [
                ("source", "Source", 0),
                ("codes", "Codes", 0),
                ("exact", "Exact species", 0),
                ("alias", "Alias", 0),
                ("group", "Group fallback", 0),
                ("generic", "Generic", 0),
                ("unmapped", "Unmapped", 0),
                ("tree_rows", "Tree rows", 0),
                ("exact_tree_rows", "Exact-species rows", 0),
                ("unresolved_tree_rows", "Unresolved rows", 0),
            ],
        )
    )
    lines.append("")
    lines.append(
        f"**{crosswalk_summary['unresolved_source_codes']}** codes "
        f"({crosswalk_summary['unresolved_tree_rows']:,} tree rows; "
        f"{unresolved_live:,} live valid trees) do not resolve to an RSYC "
        f"scientific species. They are retained at the functional-group or "
        f"generic level rather than discarded. The largest unresolved codes by "
        f"live support are:"
    )
    lines.append("")
    lines.append(
        _md_table(
            unresolved_rows,
            [
                ("source", "Source", 0),
                ("code", "Code", 0),
                ("group", "Source group", 0),
                ("rsyc", "RSYC destination", 0),
                ("basis", "Basis", 0),
                ("tree_rows", "Tree rows", 0),
                ("live_trees", "Live trees", 0),
            ],
        )
    )
    lines.append("")
    lines.append(
        "The QC PEP missing species code accounts for **359,645** tree rows but "
        "**0** live valid trees; it is a harmonization gap in the source, not a "
        "fitting-support gap. The next largest unresolved blocks are genuine "
        "species that lack an RSYC scientific code (for example `ACER.SPI`, "
        "`ALNU.RUG`, `FAGU.GRA`, and `PINU.MON`)."
    )
    lines.append("")
    lines.append("## Coverage by level")
    lines.append("")
    lines.append(
        "Counts are stratum × species rows. `All rows` includes RSYC strata "
        "with no plots at all; `In strata with sites` restricts to strata that "
        "contain at least one mapped site, which separates structural gaps "
        "from species gaps."
    )
    lines.append("")
    lines.append(
        _md_table(
            [
                {**row, "rows": row["rows"], "direct_data": row["direct_data"]}
                for row in level_rows
            ],
            [
                ("level", "Level", 0),
                ("strata", "Strata", 0),
                ("with_sites", "With sites", 0),
                ("rows", "Rows", 0),
                ("direct", "Direct", 0),
                ("pooled", "Pooled", 0),
                ("no_data", "No data", 0),
                ("direct_data", "Direct (with sites)", 0),
                ("pooled_data", "Pooled (with sites)", 0),
                ("no_data_data", "No data (with sites)", 0),
            ],
        )
    )
    lines.append("")
    lines.append(
        f"At ecodistrict level, **{ecodistrict['direct_fit']:,}** of "
        f"**{ecodistrict['rows']:,}** stratum × species cells are direct fits "
        f"({_pct(ecodistrict['direct_fit'], ecodistrict['rows'])}), "
        f"**{ecodistrict['pooled_fit']:,}** are pooled "
        f"({_pct(ecodistrict['pooled_fit'], ecodistrict['rows'])}), and "
        f"**{ecodistrict['no_data']:,}** are no_data "
        f"({_pct(ecodistrict['no_data'], ecodistrict['rows'])}). Restricting "
        f"to the **{ecodistrict['strata_with_sites']}** ecodistricts that "
        f"contain at least one site leaves "
        f"**{ecodistrict['direct_fit_in_strata_with_sites']:,}** direct "
        f"({_pct(ecodistrict['direct_fit_in_strata_with_sites'], ecodistrict['rows_in_strata_with_sites'])}), "
        f"**{ecodistrict['pooled_fit_in_strata_with_sites']:,}** pooled "
        f"({_pct(ecodistrict['pooled_fit_in_strata_with_sites'], ecodistrict['rows_in_strata_with_sites'])}), "
        f"and **{ecodistrict['no_data_in_strata_with_sites']:,}** no_data cells "
        f"({_pct(ecodistrict['no_data_in_strata_with_sites'], ecodistrict['rows_in_strata_with_sites'])}). "
        f"The same pattern at coarser levels is "
        f"{ecoregion['direct_fit']:,}/{ecoregion['pooled_fit']:,}/"
        f"{ecoregion['no_data']:,} at ecoregion, "
        f"{ecoprovince['direct_fit']:,}/{ecoprovince['pooled_fit']:,}/"
        f"{ecoprovince['no_data']:,} at ecoprovince, and "
        f"{ecozone['direct_fit']:,}/{ecozone['pooled_fit']:,}/"
        f"{ecozone['no_data']:,} at ecozone."
    )
    lines.append("")
    lines.append("### RSYC volume-model availability")
    lines.append("")
    lines.append(
        "The catalogue check uses national-scale, publishable volume models "
        "only. It shows where the inventory and the published curve library "
        "overlap, not whether a published curve is accurate for local use."
    )
    lines.append("")
    lines.append(
        _md_table(
            [
                {
                    "level": row["level"],
                    "direct": row["direct"],
                    "with_model": row["direct_model"],
                    "without_model": row["direct_no_model"],
                    "no_data_with_model": row["no_data_model"],
                }
                for row in level_rows
                if row["level"] != "national"
            ],
            [
                ("level", "Level", 0),
                ("direct", "Direct", 0),
                ("with_model", "Direct with model", 0),
                ("without_model", "Direct without model", 0),
                ("no_data_with_model", "No data with model", 0),
            ],
        )
    )
    lines.append("")
    lines.append("## Province and source coverage")
    lines.append("")
    lines.append(
        _md_table(
            province_rows,
            [
                ("province", "Province", 0),
                ("sites_mapped", "Mapped sites", 0),
                ("sites_remeasured", "Remeasured", 0),
                ("ecodistricts", "Ecodistricts", 0),
                ("plots", "Plots", 0),
                ("tree_rows", "Tree rows", 0),
                ("live_trees", "Live trees", 0),
                ("direct", "Direct", 0),
                ("pooled", "Pooled", 0),
                ("no_data", "No data (data-bearing)", 0),
                ("sources", "Sources", 0),
            ],
        )
    )
    lines.append("")
    lines.append(
        f"Province class counts cover data-bearing districts only; the "
        f"**{ecodistrict['no_data'] - ecodistrict['no_data_in_strata_with_sites']:,}** "
        f"no-data cells in the **{gaps['ecodistricts_without_sites']}** "
        f"districts with no sites are not assigned to a province."
    )
    lines.append("")
    lines.append(
        f"Provinces with no mapped site: **{', '.join(gaps['provinces_missing'])}**. "
        f"Manitoba is present only as **2** MAGPlot sites. The national extract "
        f"is therefore concentrated in BC, QC, Atlantic Canada, and parts of the "
        f"boreal and northern territories; it is not a representative national "
        f"plot network."
    )
    lines.append("")
    lines.append("## DBH limits")
    lines.append("")
    lines.append(
        "DBH statistics are unweighted over live, positive-DBH trees. "
        "`share < 5 cm` is the fraction of live trees below a common "
        "merchantability cut-off; it shows how much of the support sits in "
        "the small-tree tail that a left-truncated fit must handle explicitly. "
        f"Across sources the share ranges from {100.0 * dbh_share_min:.1f}% to "
        f"{100.0 * dbh_share_max:.1f}%. "
        + (
            f"QC PEP has a {float(qc_dbh['dbh_min_cm']):.1f} cm minimum and "
            f"no trees below 5 cm; "
            if qc_dbh is not None
            else ""
        )
        + (
            f"NL FAL has {100.0 * float(nl_dbh['share_dbh_lt_5cm']):.1f}% of "
            f"its live trees below 5 cm."
            if nl_dbh is not None
            else ""
        )
    )
    lines.append("")
    lines.append(
        _md_table(
            dbh_table_rows,
            [
                ("source", "Source", 0),
                ("live_trees", "Live trees", 0),
                ("dbh_min_cm", "Min cm", 2),
                ("dbh_p05_cm", "P05 cm", 2),
                ("dbh_p50_cm", "Median cm", 2),
                ("dbh_p95_cm", "P95 cm", 2),
                ("dbh_max_cm", "Max cm", 2),
                ("share_dbh_lt_5cm", "% < 5 cm", 1),
            ],
        )
    )
    lines.append("")
    lines.append("## Data gaps")
    lines.append("")
    lines.append(
        f"- **{gaps['ecodistricts_without_sites']} of {gaps['ecodistricts_total']}** "
        f"RSYC ecodistricts contain no mapped site."
    )
    lines.append(
        f"- **{len(gaps['ecozones_without_sites'])} of {gaps['ecozones_total']}** "
        f"ecozones contain no mapped site: "
        f"{', '.join(gaps['ecozones_without_sites'])}."
    )
    lines.append(
        f"- **{gaps['ecodistricts_with_sites_but_no_direct']}** of the "
        f"**{gaps['ecodistricts_with_sites']}** data-bearing ecodistricts "
        f"have no species with a direct fit."
    )
    lines.append(
        f"- Species with no direct fit in any ecodistrict: "
        f"**{', '.join(gaps['species_without_direct_ecodistrict']) or 'none'}**."
    )
    lines.append("")
    lines.append("### Largest species gaps at ecodistrict level")
    lines.append("")
    lines.append(
        _md_table(
            species_gap_rows,
            [
                ("species", "Species", 0),
                ("districts_with_data", "Districts with data", 0),
                ("direct", "Direct", 0),
                ("pooled", "Pooled", 0),
                ("no_data_with_data", "No data in data-bearing districts", 0),
                ("no_data", "No data (all districts)", 0),
                ("trees", "Live trees", 0),
            ],
        )
    )
    lines.append("")
    lines.append("### Data-bearing ecodistricts with the largest species gaps")
    lines.append("")
    lines.append(
        _md_table(
            stratum_gap_rows,
            [
                ("ecodistrict", "Ecodistrict", 0),
                ("ecozone", "Ecozone", 0),
                ("species_with_data", "Species with data", 0),
                ("direct", "Direct", 0),
                ("pooled", "Pooled", 0),
                ("no_data_with_data", "No data in data-bearing species", 0),
                ("no_data", "No data (all species)", 0),
                ("sites", "Sites", 0),
                ("trees", "Live trees", 0),
            ],
        )
    )
    lines.append("")
    lines.append("## What can be fit now")
    lines.append("")
    lines.append(
        f"- **National audit rollup:** all **{national['direct_fit']}** species "
        f"or model groups have at least {thresholds['direct_min_trees']} live "
        f"trees and {thresholds['direct_min_plots']} plots. This is not an RSYC "
        f"stratum and should not be used as a replacement for stratified fits."
    )
    lines.append(
        f"- **Ecodistrict:** {ecodistrict['direct_fit']:,} direct "
        f"stratum × species cells; these are the only cells that meet the "
        f"pre-registered direct threshold without pooling."
    )
    lines.append(
        f"- **Ecoregion:** {ecoregion['direct_fit']:,} direct cells; "
        f"**ecoprovince:** {ecoprovince['direct_fit']:,}; "
        f"**ecozone:** {ecozone['direct_fit']:,}."
    )
    lines.append(
        f"- **RSYC overlap:** {ecodistrict['direct_with_rsyc_model']:,} direct "
        f"ecodistrict cells also have a national RSYC volume model; "
        f"{ecodistrict['direct_without_rsyc_model']:,} have inventory support "
        f"but no national RSYC species curve, so they are calibration "
        f"opportunities rather than published-curve replacements."
    )
    lines.append("")
    lines.append("## What needs pooling")
    lines.append("")
    lines.append(
        f"- **{ecodistrict['pooled_fit']:,}** ecodistrict cells are pooled "
        f"({_pct(ecodistrict['pooled_fit'], ecodistrict['rows'])} of all cells; "
        f"{_pct(ecodistrict['pooled_fit'], ecodistrict['rows_in_strata_with_sites'])} "
        f"of data-bearing cells)."
    )
    lines.append(
        f"- Pooling should follow the nesting ecodistrict → ecoregion → "
        f"ecoprovince → ecozone, but the coarser level is not automatically "
        f"better: ecozone still has **{ecozone['no_data']:,}** no_data cells "
        f"and **{ecozone['pooled_fit']:,}** pooled cells."
    )
    lines.append(
        f"- The largest pooled species buckets at ecodistrict level are "
        f"{pooled_species_text}."
    )
    lines.append(
        f"- The largest pooled count is at ecodistrict level "
        f"(**{ecodistrict['pooled_fit']:,}** cells), which is where the "
        f"hierarchical pooling rule has the most work to do."
    )
    lines.append("")
    lines.append("## What is missing")
    lines.append("")
    lines.append(
        "- **Alberta, Ontario, Nova Scotia, and Nunavut have no plots** in the "
        "harmonized extract. Manitoba has two MAGPlot sites. The national "
        "coverage claim is therefore conditional on future AB/ON/NS/NU data."
    )
    lines.append(
        f"- **{gaps['ecodistricts_without_sites']}** ecodistricts have no "
        f"plots, including all of Nova Scotia and large parts of the "
        f"Atlantic Maritime, Boreal Shield, and Taiga ecozones."
    )
    lines.append(
        f"- **{crosswalk_summary['unresolved_source_codes']}** source codes "
        f"({crosswalk_summary['unresolved_tree_rows']:,} tree rows; "
        f"{unresolved_live:,} live valid trees) are not resolved to an RSYC "
        f"scientific species. Species-specific fits for those live trees are "
        f"not defensible; they can support functional-group fits only. The QC "
        f"PEP missing code is a source harmonization gap, not live support."
    )
    lines.append(
        "- The MAGPlot `approx_10km` coordinate class is adequate for "
        "ecodistrict joins but not for tile-level or fine-scale spatial "
        "claims."
    )
    lines.append("")
    lines.append("## Implications for the RSYC-stratified pipeline")
    lines.append("")
    lines.append(
        "1. **Use the hierarchy as a fallback ladder, not a single key.** "
        "Direct fits exist for only a minority of ecodistrict × species cells. "
        "A pipeline that refuses to pool will produce almost no national "
        "species coverage; a pipeline that pools blindly will mix ecologically "
        "different strata. The coverage table supplies the explicit ladder and "
        "the counts needed to justify each step."
    )
    lines.append(
        "2. **Keep species and functional-group fits separate.** The crosswalk "
        "resolves most tree rows to species, but a material tail is only "
        "`coniferous`, `broadleaf`, or `generic`. A group fit is not a species "
        "fit and should not be relabelled as one downstream."
    )
    lines.append(
        "3. **Treat RSYC curves as a prior, not a substitute.** "
        f"{ecodistrict['no_data_with_rsyc_model']:,} ecodistrict cells are "
        "`no_data` yet have a national RSYC volume model. They are the "
        "natural gap-fill surface, but the RSYC validation bias and RMSE "
        "ranges must travel with the curve."
    )
    lines.append(
        "4. **Carry DBH limits explicitly.** The share of live trees below "
        f"5 cm ranges from {100.0 * dbh_share_min:.1f}% to "
        f"{100.0 * dbh_share_max:.1f}% across sources. Unweighted fits that "
        "ignore the small-tree tail will not be comparable across sources"
        + (
            f", and the QC PEP minimum of {float(qc_dbh['dbh_min_cm']):.1f} cm "
            f"means QC PEP cannot contribute to the small-tree tail at all."
            if qc_dbh is not None
            else "."
        )
    )
    lines.append(
        "5. **Do not call this national yet.** BC and QC dominate the support; "
        "AB, ON, NS, and NU are absent. Any national supply result must carry "
        "a coverage flag per stratum and species, and must fail closed where "
        "the flag is `no_data`."
    )
    lines.append("")
    lines.append("## Artifacts")
    lines.append("")
    lines.append(
        "- `coverage.csv`: one row per level × stratum × species with counts, "
        "DBH limits, year range, province/source provenance, class, and RSYC "
        "model availability."
    )
    lines.append(
        "- `coverage_summary.json`: machine-readable mapping, level, province, "
        "source, DBH, and gap summaries."
    )
    lines.append(
        "- `species_crosswalk_national.csv`: every observed source species code "
        "and its RSYC destination, basis, and note."
    )
    lines.append(
        "- `coverage_heatmap.png`: 200 dpi heatmap of ecodistrict support classes."
    )
    lines.append("- `manifest.json`: input/output hashes, code commit, and runtime.")
    lines.append("")
    lines.append("## Limitations")
    lines.append("")
    lines.append(
        "- The join is a point-in-polygon assignment. A site on a boundary or "
        "with an approximate coordinate can be assigned to the wrong "
        "ecodistrict; the audit reports the unmatched count but does not "
        "impute a nearest stratum."
    )
    lines.append(
        "- `n_plots` depends on the physical-plot derivation. FAIB plot "
        "prefixes come from `tree_id`; if a source changes its tree-id "
        "convention, the plot count will change."
    )
    lines.append(
        "- The coverage class is a support threshold, not a goodness-of-fit "
        "or a representativeness statement. A direct cell can still fail a "
        "distribution fit for reasons outside this inventory."
    )
    lines.append(
        "- The species crosswalk uses a nearest-taxon alias for BC black "
        "cottonwood and functional-group fallbacks elsewhere. It is not a "
        "taxonomic revision."
    )
    lines.append("")
    lines.append(
        f"_Generated by `workflows/national/assortments/national_coverage.py` "
        f"at {summary['generated_at_utc']}._"
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_metadata(summary: Mapping[str, Any], path: Path) -> None:
    """Write the compact metadata registry row set."""
    columns = [
        "level",
        "strata_total",
        "strata_with_sites",
        "rows",
        "direct_fit",
        "pooled_fit",
        "no_data",
        "direct_fit_in_strata_with_sites",
        "pooled_fit_in_strata_with_sites",
        "no_data_in_strata_with_sites",
        "species_total",
        "species_with_direct",
        "live_trees_mapped_species",
        "artifact_path",
        "generated_at_utc",
    ]
    rows = []
    for row in summary["coverage_by_level"]:
        rows.append(
            {
                "level": row["level"],
                "strata_total": row["strata_total"],
                "strata_with_sites": row["strata_with_sites"],
                "rows": row["rows"],
                "direct_fit": row["direct_fit"],
                "pooled_fit": row["pooled_fit"],
                "no_data": row["no_data"],
                "direct_fit_in_strata_with_sites": row[
                    "direct_fit_in_strata_with_sites"
                ],
                "pooled_fit_in_strata_with_sites": row[
                    "pooled_fit_in_strata_with_sites"
                ],
                "no_data_in_strata_with_sites": row["no_data_in_strata_with_sites"],
                "species_total": row["species_total"],
                "species_with_direct": row["species_with_direct"],
                "live_trees_mapped_species": row["live_trees_mapped_species"],
                "artifact_path": summary["outputs"]["coverage_csv"],
                "generated_at_utc": summary["generated_at_utc"],
            }
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=columns).to_csv(path, index=False, lineterminator="\n")


def write_manifest(
    config: CoverageConfig,
    summary: Mapping[str, Any],
    output_paths: Sequence[Path],
) -> Path:
    """Write the artifact manifest with hashes and provenance."""
    outputs = {}
    for path in output_paths:
        if path.exists():
            outputs[path.name] = {
                "path": _display_path(path, config.repo_root),
                "bytes": int(path.stat().st_size),
                "sha256": _sha256(path),
            }
    manifest = {
        "artifact": summary["artifact"],
        "artifact_version": summary["artifact_version"],
        "generated_at_utc": summary["generated_at_utc"],
        "runtime_seconds": summary["runtime_seconds"],
        "thresholds": summary["thresholds"],
        "code": {
            "parent_commit": _git_commit(config.repo_root),
            "data_commit": _git_commit(config.data_root),
            "module": "workflows/national/assortments/national_coverage.py",
        },
        "inputs": summary["inputs"],
        "outputs": outputs,
        "coverage_by_level": summary["coverage_by_level"],
    }
    path = config.out_dir / "manifest.json"
    path.write_text(
        json.dumps(_jsonable(manifest), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path.cwd(),
        help="Parent repository root (default: current directory).",
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        default=None,
        help="Data submodule root (default: <repo-root>/data).",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Output directory (default: data/derived/national/assortments/national_coverage).",
    )
    parser.add_argument(
        "--metadata-path",
        type=Path,
        default=None,
        help="Metadata CSV path (default: metadata/national-coverage.csv).",
    )
    parser.add_argument(
        "--top-n-gaps",
        type=int,
        default=20,
        help="Number of gap rows retained in the summary (default: 20).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print stage timings to stderr.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    repo_root = Path(args.repo_root).resolve()
    data_root = (
        Path(args.data_root) if args.data_root else repo_root / "data"
    ).resolve()
    out_dir = (
        Path(args.out_dir)
        if args.out_dir
        else data_root / "derived" / "national" / "assortments" / "national_coverage"
    ).resolve()
    metadata_path = (
        Path(args.metadata_path)
        if args.metadata_path
        else repo_root / "metadata" / "national-coverage.csv"
    ).resolve()
    config = CoverageConfig(
        repo_root=repo_root,
        data_root=data_root,
        out_dir=out_dir,
        metadata_path=metadata_path,
        top_n_gaps=int(args.top_n_gaps),
        verbose=bool(args.verbose),
    )
    summary = run(config)
    total_rows = sum(row["rows"] for row in summary["coverage_by_level"])
    print(
        f"Wrote {summary['outputs']['coverage_csv']} "
        f"({total_rows:,} rows); runtime {summary['runtime_seconds']:.1f}s"
    )
    for level in summary["coverage_by_level"]:
        print(
            f"  {level['level']:<12} direct={level['direct_fit']:<5} "
            f"pooled={level['pooled_fit']:<5} no_data={level['no_data']:<5}"
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
