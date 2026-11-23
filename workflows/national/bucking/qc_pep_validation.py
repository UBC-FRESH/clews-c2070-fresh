"""Quebec PEP measured-tree bucking validation v0.

This module applies the published Quebec Schneider (2013) fixed-effect stem
taper and the existing Quebec pilot dimension-only sort set to a recent,
manageable sample of the open Quebec PEP permanent-sample-plot database.  It
is a validation product, not a provincial inventory, growth model, or supply
estimate.

Source and sample
-----------------
The source is the MRNF/Donnees Quebec ``PEP_GPKG.zip`` (CC BY 4.0).  The
sample is the 1,000 most recent live PEP plot visits with at least one
eligible measured tree.  The latest visit per plot is selected, and trees are
included only when they are live, have a positive DBH, a positive per-tree
expansion factor (``tige_ha``), a measured total height
(``dendro_arbres_etudes.haut_arbre``), and a species covered by the published
Schneider taper registry.  ``dendro_arbres.hauteurdef`` is *defect height*,
not total tree height, and is deliberately not used.

Product boundary
----------------
The tree list is measured open data.  Every log, residue, volume, biomass and
value row is a model estimate.  No official Quebec grade is assigned, no
observed harvest volume is compared, and no national claim is made.  PEP
contains ``vmb_tige``/``vmb_ha`` compiled merchantable-volume columns, but
they are null for all 2,210,417 tree rows; no PEP biomass field exists.  The
comparison is therefore a reported taper total and residue ledger, not a
model-versus-observed comparison.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sqlite3
import subprocess
import sys
import time
import urllib.request
import zipfile
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .national_bucking_pilot_v1 import (
    DECIDUOUS_CODES,
    STUMP_HEIGHT_M,
    buck_tree,
    build_taper,
    load_national_sort_rules,
)
from .pilot_schema import EVIDENCE_TIERS, PilotTree
from .schneider2013 import SchneiderTaperRegistry, load_taper_registry

__all__ = [
    "DEFAULT_OUTPUT_DIR",
    "REQUIRED_ARTIFACTS",
    "VALIDATION_NAME",
    "QCValidationError",
    "assert_no_unexpected_deletions",
    "build_parser",
    "inclusion_area_ha",
    "main",
    "run",
    "select_sample",
]

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT_DIR = (
    REPO_ROOT / "data" / "derived" / "national" / "bucking" / "qc_pep_validation_v0"
)
DEFAULT_WORK_DIR = Path(
    os.environ.get("QC_PEP_WORK_DIR", "/tmp/gep/qc_pep_validation")
)
TAPER_REGISTRY_PATH = (
    REPO_ROOT / "workflows" / "national" / "bucking" / "rules" / "qc_schneider2013_taper.csv"
)
SORT_RULES_PATH = (
    REPO_ROOT / "workflows" / "national" / "bucking" / "rules" / "qc_pilot_sorts.csv"
)
DEFAULT_ZIP_PATH = REPO_ROOT / "data" / "raw" / "qc_pep" / "PEP_GPKG.zip"
ACQUISITION_RECORD_PATH = REPO_ROOT / "data" / "raw" / "qc_pep" / "ACQUISITION.md"
PROVENANCE_RECORD_PATH = REPO_ROOT / "data" / "raw" / "qc_pep" / "PROVENANCE.csv"

SOURCE_URL = (
    "https://diffusion.mffp.gouv.qc.ca/Diffusion/DonneeGratuite/Foret/"
    "DONNEES_FOR_ECO_SUD/Placettes_permanentes/02-Donnees/PROV/PEP_GPKG.zip"
)
SOURCE_DATASET_URL = (
    "https://www.donneesquebec.ca/recherche/dataset/"
    "placettes-echantillons-permanentes-1970-a-aujourd-hui"
)
SOURCE_PUBLISHER = (
    "Ministere des Ressources naturelles et des Forets du Quebec (MRNF), "
    "Direction des inventaires forestiers"
)
SOURCE_LICENCE = "CC BY 4.0"
SOURCE_DATASET_ID = "placettes-echantillons-permanentes-1970-a-aujourd-hui"
ZIP_MEMBER = "PEP.gpkg"

#: Hash recorded in data/raw/qc_pep/PROVENANCE.csv on 2026-11-14.
EXPECTED_ZIP_SHA256 = (
    "a5deb82b8e05c3226f83eb8daa4842900b6564e31b30915d9215f8a031faa5e5"
)
EXPECTED_GPKG_SHA256 = (
    "6d53e90a23799d681d050e13a433bc6e7ede6549b0733c8cc9c01fc8f94348c9"
)

VALIDATION_NAME = "qc_pep_bucking_validation_v0"
DEFAULT_MAX_PLOTS = 1_000
DEFAULT_MAX_TREES = 12_000
DEFAULT_TIMEBOX_SECONDS = 1_200.0
NOMINAL_PLOT_AREA_HA = 0.04

#: Documented live codes in the PEP ETAT code list.  GA/GM/GV are sapling
#: states and are not merchantable-tree measurements.
LIVE_CODES: tuple[str, ...] = ("10", "12", "30", "40", "42", "50", "52", "58")
TAPER_SPECIES: tuple[str, ...] = ("BOP", "EPB", "EPN", "EPR", "PEG", "PET", "SAB", "THO")
SPECIES_COMMON: dict[str, str] = {
    "BOP": "paper birch",
    "EPB": "white spruce",
    "EPN": "black spruce",
    "EPR": "red spruce",
    "PEG": "large-tooth aspen",
    "PET": "trembling aspen",
    "SAB": "balsam fir",
    "THO": "eastern white cedar",
}

TAPER_DBH_MIN_CM = 5.0
TAPER_DBH_MAX_CM = 100.0
TAPER_HEIGHT_MIN_M = 5.0
TAPER_HEIGHT_MAX_M = 40.0

REQUIRED_ARTIFACTS: tuple[str, ...] = (
    "tree_list.csv",
    "bucked_logs.csv",
    "residue.csv",
    "plot_summary.csv",
    "species_summary.csv",
    "comparison.csv",
    "source_schema.csv",
    "summary.json",
    "report.md",
    "manifest.json",
)

LABELS: dict[str, bool] = {
    "measured_tree_list": True,
    "open_data": True,
    "model_estimate": True,
    "observed": False,
    "policy_grade": False,
    "national_claim": False,
    "synthetic": False,
}

_TREE_REQUIRED_COLUMNS = (
    "tree_id",
    "stem_id",
    "site_id",
    "visit_id",
    "species",
    "species_group",
    "dbh_cm",
    "height_m",
    "expansion_factor_stems_ha",
    "inclusion_area_ha",
    "taper_id",
    "taper_total_m3",
    "recovered_gross_m3",
    "residue_m3",
    "n_logs",
    "balance_error_m3",
    *LABELS,
)
_LOG_REQUIRED_COLUMNS = (
    "tree_id",
    "site_id",
    "species",
    "log_index",
    "length_m",
    "volume_m3",
    "net_volume_m3",
    "defect_deduction_m3",
    "sort_code",
    "product",
    "grade_status",
    "taper_id",
    "evidence",
    "taper_evidence",
    *LABELS,
)
_RESIDUE_REQUIRED_COLUMNS = (
    "tree_id",
    "site_id",
    "species",
    "residue_volume_m3",
    "recovered_log_m3",
    "taper_total_m3",
    "balance_error_m3",
    "evidence",
    "taper_evidence",
    *LABELS,
)


class QCValidationError(RuntimeError):
    """Raised when the PEP validation contract or a source invariant fails."""


class TimeboxExceeded(QCValidationError):
    """Raised when the run exceeds its wall-clock budget."""


class Deadline:
    def __init__(self, seconds: float | None) -> None:
        self.started = time.monotonic()
        self.deadline = self.started + seconds if seconds and seconds > 0 else None

    def check(self, label: str) -> None:
        if self.deadline is not None and time.monotonic() > self.deadline:
            raise TimeboxExceeded(f"timebox exceeded before {label}")

    def remaining(self) -> float | None:
        if self.deadline is None:
            return None
        return max(0.0, self.deadline - time.monotonic())


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _hash_record(label: str, path: Path, logical_path: str | None = None) -> dict[str, Any]:
    if not path.exists():
        return {"label": label, "path": logical_path or str(path), "status": "missing"}
    return {
        "label": label,
        "path": logical_path or str(path),
        "bytes": int(path.stat().st_size),
        "sha256": _sha256(path),
    }


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, lineterminator="\n")


def _fmt(value: Any, digits: int = 2) -> str:
    if value is None:
        return "n/a"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if not math.isfinite(number):
        return "n/a"
    return f"{number:,.{digits}f}"


def _markdown_table(frame: pd.DataFrame, digits: int = 2) -> str:
    if frame.empty:
        return "_No rows._"
    columns = list(frame.columns)
    lines = [
        "| " + " | ".join(str(column) for column in columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for row in frame.itertuples(index=False):
        cells = []
        for value in row:
            if isinstance(value, (float, np.floating)):
                cells.append(_fmt(value, digits))
            elif value is None or (isinstance(value, float) and math.isnan(value)):
                cells.append("")
            else:
                cells.append(str(value))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _safe_div(numerator: float, denominator: float) -> float:
    if not math.isfinite(denominator) or denominator == 0.0:
        return 0.0
    return numerator / denominator


def inclusion_area_ha(expansion_factor_stems_ha: float) -> float:
    """Return the per-tree inclusion area in hectares.

    PEP stores stems per hectare, so one tree represents ``1 / tige_ha``
    hectares.  A 25 stems/ha factor is a 0.04 ha main plot; a 16 factor is the
    0.0625 ha large-tree annulus used by the nested PEP design.
    """
    factor = float(expansion_factor_stems_ha)
    if not math.isfinite(factor) or factor <= 0.0:
        raise QCValidationError(
            f"expansion factor must be finite and positive, got {expansion_factor_stems_ha!r}"
        )
    return 1.0 / factor


def _git_status_lines(directory: Path) -> list[str]:
    if not (directory / ".git").exists() and not (directory / ".git").is_file():
        return []
    result = subprocess.run(
        ["git", "-C", str(directory), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise QCValidationError(
            f"git status failed in {directory}: {result.stderr.strip()}"
        )
    return [line for line in result.stdout.splitlines() if line.strip()]


def assert_no_unexpected_deletions(repo_root: Path = REPO_ROOT) -> list[str]:
    """Fail closed when the parent or data repository shows a ``D`` status."""
    deletions: list[str] = []
    for root in (repo_root, repo_root / "data"):
        for line in _git_status_lines(root):
            if len(line) >= 2 and (line[0] == "D" or line[1] == "D"):
                deletions.append(f"{root.name or 'parent'}: {line}")
    if deletions:
        raise QCValidationError(
            "unexpected git deletion(s); refusing to run Quebec PEP validation: "
            + "; ".join(deletions)
        )
    return deletions


def _download_zip(url: str, destination: Path, deadline: Deadline) -> str:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "CLEWS-C2070/1.0 (open-data validation)"},
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response, partial.open("wb") as handle:
            while True:
                deadline.check("download")
                chunk = response.read(1 << 20)
                if not chunk:
                    break
                handle.write(chunk)
    except Exception as error:
        partial.unlink(missing_ok=True)
        raise QCValidationError(
            f"failed to download {url}: {error}. Use --zip with a local copy."
        ) from error
    partial.replace(destination)
    return _utc_now()


def _extract_gpkg(zip_path: Path, member: str, destination: Path, deadline: Deadline) -> None:
    with zipfile.ZipFile(zip_path) as archive:
        if member not in archive.namelist():
            raise QCValidationError(f"{zip_path} does not contain {member!r}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".part")
        with archive.open(member) as source, temporary.open("wb") as target:
            while True:
                deadline.check("extract")
                chunk = source.read(1 << 20)
                if not chunk:
                    break
                target.write(chunk)
        temporary.replace(destination)


def _connect_readonly(gpkg_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{gpkg_path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _query_frame(
    connection: sqlite3.Connection, sql: str, params: Mapping[str, Any] | None = None
) -> pd.DataFrame:
    return pd.read_sql_query(sql, connection, params=dict(params or {}))


def _table_record(connection: sqlite3.Connection, name: str) -> dict[str, Any]:
    columns = [
        row[1] for row in connection.execute(f'PRAGMA table_info("{name}")').fetchall()
    ]
    rows = int(connection.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0])
    return {"table": name, "rows": rows, "columns": columns}


def _inspect_schema(connection: sqlite3.Connection, deadline: Deadline) -> dict[str, Any]:
    deadline.check("schema inspection")
    table_rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view') ORDER BY name"
    ).fetchall()
    tables = [_table_record(connection, str(row[0])) for row in table_rows]
    by_name = {record["table"]: record for record in tables}
    required_tables = {
        "dendro_arbres",
        "dendro_arbres_etudes",
        "placette",
        "placette_mes",
    }
    missing_tables = sorted(required_tables - set(by_name))
    if missing_tables:
        raise QCValidationError(f"PEP is missing required tables: {missing_tables}")

    required_columns = {
        "dendro_arbres": {
            "id_pe",
            "id_pe_mes",
            "id_arbre",
            "id_arb_mes",
            "etat",
            "essence",
            "dhp",
            "tige_ha",
            "st_tige",
            "st_ha",
            "hauteurdef",
            "vmb_tige",
            "vmb_ha",
            "dic",
            "hauteurdic",
        },
        "dendro_arbres_etudes": {"id_arb_mes", "haut_arbre", "hauteurdef"},
        "placette": {"id_pe", "reseau", "type_pe"},
        "placette_mes": {"id_pe", "id_pe_mes", "no_mes", "date_sond", "dimension", "statut_mes"},
    }
    missing_columns: dict[str, list[str]] = {}
    for table, expected in required_columns.items():
        absent = sorted(expected - set(by_name[table]["columns"]))
        if absent:
            missing_columns[table] = absent
    if missing_columns:
        raise QCValidationError(f"PEP is missing required columns: {missing_columns}")

    stats = connection.execute(
        """
        SELECT
            COUNT(*) AS trees,
            COUNT(vmb_tige) AS vmb_tige_non_null,
            COUNT(vmb_ha) AS vmb_ha_non_null,
            COUNT(dic) AS dic_non_null,
            COUNT(hauteurdic) AS hauteurdic_non_null,
            COUNT(hauteurdef) AS hauteurdef_non_null,
            SUM(CASE WHEN etat IN ('10','12','30','40','42','50','52','58') THEN 1 ELSE 0 END)
                AS live_trees
        FROM dendro_arbres
        """
    ).fetchone()
    study_stats = connection.execute(
        "SELECT COUNT(*) AS rows, COUNT(haut_arbre) AS haut_arbre_non_null "
        "FROM dendro_arbres_etudes"
    ).fetchone()
    visit_stats = connection.execute(
        "SELECT COUNT(*) AS visits, MIN(date_sond) AS first_visit, "
        "MAX(date_sond) AS last_visit FROM placette_mes"
    ).fetchone()
    plot_stats = connection.execute(
        "SELECT COUNT(*) AS plots, COUNT(DISTINCT reseau) AS networks FROM placette"
    ).fetchone()

    schema: dict[str, Any] = {
        "tables": tables,
        "row_counts": {record["table"]: record["rows"] for record in tables},
        "required_tables": sorted(required_tables),
        "required_columns": {key: sorted(value) for key, value in required_columns.items()},
        "key_columns": {
            table: [
                column
                for column in by_name[table]["columns"]
                if column
                in {
                    "id_pe",
                    "id_pe_mes",
                    "no_mes",
                    "id_arbre",
                    "id_arb_mes",
                    "etat",
                    "essence",
                    "dhp",
                    "tige_ha",
                    "st_tige",
                    "st_ha",
                    "hauteurdef",
                    "haut_arbre",
                    "vmb_tige",
                    "vmb_ha",
                    "dic",
                    "hauteurdic",
                    "date_sond",
                    "dimension",
                    "statut_mes",
                    "reseau",
                }
            ]
            for table in required_tables
        },
        "units": {
            "dhp": "millimetres; converted to centimetres as dhp/10",
            "haut_arbre": "decimetres; converted to metres as haut_arbre/10",
            "hauteurdef": "defect height, not total tree height; not used",
            "tige_ha": "stems per hectare expansion factor; inclusion area = 1/tige_ha",
            "st_tige": "tree basal area, square metres",
            "st_ha": "tree basal area per hectare, square metres per hectare",
            "vmb_tige": "gross merchantable stem volume, cubic metres",
            "vmb_ha": "gross merchantable volume per hectare, cubic metres per hectare",
        },
        "counts": {
            "plots": int(plot_stats["plots"]),
            "networks": int(plot_stats["networks"]),
            "visits": int(visit_stats["visits"]),
            "trees": int(stats["trees"]),
            "live_trees": int(stats["live_trees"] or 0),
            "study_rows": int(study_stats["rows"]),
            "haut_arbre_non_null": int(study_stats["haut_arbre_non_null"] or 0),
            "vmb_tige_non_null": int(stats["vmb_tige_non_null"] or 0),
            "vmb_ha_non_null": int(stats["vmb_ha_non_null"] or 0),
            "dic_non_null": int(stats["dic_non_null"] or 0),
            "hauteurdic_non_null": int(stats["hauteurdic_non_null"] or 0),
            "hauteurdef_non_null": int(stats["hauteurdef_non_null"] or 0),
            "first_visit": str(visit_stats["first_visit"]),
            "last_visit": str(visit_stats["last_visit"]),
        },
    }
    if schema["counts"]["trees"] <= 0:
        raise QCValidationError("PEP dendro_arbres is empty")
    return schema


def _selection_funnel(connection: sqlite3.Connection, deadline: Deadline) -> dict[str, int]:
    deadline.check("selection funnel")
    live = ", ".join(f"'{code}'" for code in LIVE_CODES)
    species = ", ".join(f"'{code}'" for code in TAPER_SPECIES)
    frame = _query_frame(
        connection,
        f"""
        WITH latest_visit AS (
            SELECT m.id_pe, m.id_pe_mes, m.no_mes, m.date_sond,
                   ROW_NUMBER() OVER (
                       PARTITION BY m.id_pe
                       ORDER BY m.date_sond DESC, m.no_mes DESC
                   ) AS visit_rank
            FROM placette_mes AS m
            WHERE m.date_sond IS NOT NULL
              AND COALESCE(m.statut_mes, '') NOT IN ('AB', 'DE', 'NT')
        ),
        base AS (
            SELECT a.id_pe, a.etat, a.essence, a.dhp, a.tige_ha, e.haut_arbre
            FROM dendro_arbres AS a
            JOIN latest_visit AS l
              ON l.id_pe_mes = a.id_pe_mes AND l.visit_rank = 1
            LEFT JOIN dendro_arbres_etudes AS e
              ON e.id_arb_mes = a.id_arb_mes
        ),
        flags AS (
            SELECT *,
                CASE WHEN etat IN ({live}) THEN 1 ELSE 0 END AS is_live,
                CASE WHEN dhp > 0 AND tige_ha > 0 THEN 1 ELSE 0 END AS has_dbh_expansion,
                CASE WHEN essence IS NOT NULL AND essence <> '' THEN 1 ELSE 0 END AS has_species,
                CASE WHEN haut_arbre > 0 THEN 1 ELSE 0 END AS has_height,
                CASE WHEN haut_arbre >= 50 AND haut_arbre <= 400 THEN 1 ELSE 0 END
                    AS height_in_taper_domain,
                CASE WHEN dhp >= 50 AND dhp <= 1000 THEN 1 ELSE 0 END
                    AS dbh_in_taper_domain,
                CASE WHEN essence IN ({species}) THEN 1 ELSE 0 END AS has_taper_species
            FROM base
        )
        SELECT
            COUNT(*) AS latest_visit_trees,
            SUM(is_live) AS live_trees,
            SUM(is_live * has_dbh_expansion) AS live_with_dbh_expansion,
            SUM(is_live * has_dbh_expansion * has_species) AS live_with_species,
            SUM(is_live * has_dbh_expansion * has_species * has_height) AS live_with_height,
            SUM(is_live * has_dbh_expansion * has_species * has_height
                * height_in_taper_domain) AS live_with_height_domain,
            SUM(is_live * has_dbh_expansion * has_species * has_height
                * height_in_taper_domain * has_taper_species) AS live_with_taper_species,
            SUM(is_live * has_dbh_expansion * has_species * has_height
                * height_in_taper_domain * has_taper_species
                * dbh_in_taper_domain) AS candidate_trees,
            COUNT(DISTINCT CASE WHEN is_live * has_dbh_expansion * has_species
                * has_height * height_in_taper_domain * has_taper_species
                * dbh_in_taper_domain = 1 THEN id_pe END) AS candidate_plots
        FROM flags
        """,
    )
    return {key: int(value or 0) for key, value in frame.iloc[0].to_dict().items()}


def select_sample(
    connection: sqlite3.Connection,
    max_plots: int = DEFAULT_MAX_PLOTS,
    max_trees: int = DEFAULT_MAX_TREES,
    deadline: Deadline | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Select the most recent eligible live PEP plots and their trees.

    The selection is deterministic: plots are ordered by measurement date
    descending and ``id_pe`` ascending; the first ``max_plots`` plots are
    retained.  Every eligible tree in those plots is used, capped by a stable
    hash of ``id_arb_mes`` when ``max_trees`` is smaller than the candidate
    count.
    """
    if max_plots <= 0 or max_trees <= 0:
        raise QCValidationError("max_plots and max_trees must be positive")
    if deadline is not None:
        deadline.check("sample selection")
    live = ", ".join(f"'{code}'" for code in LIVE_CODES)
    species = ", ".join(f"'{code}'" for code in TAPER_SPECIES)
    frame = _query_frame(
        connection,
        f"""
        WITH latest_visit AS (
            SELECT m.id_pe, m.id_pe_mes, m.no_mes, m.date_sond, m.dimension,
                   m.statut_mes,
                   ROW_NUMBER() OVER (
                       PARTITION BY m.id_pe
                       ORDER BY m.date_sond DESC, m.no_mes DESC
                   ) AS visit_rank
            FROM placette_mes AS m
            WHERE m.date_sond IS NOT NULL
              AND COALESCE(m.statut_mes, '') NOT IN ('AB', 'DE', 'NT')
        ),
        candidate AS (
            SELECT
                a.id_pe AS site_id,
                a.id_pe_mes AS visit_id,
                a.id_arbre AS stem_id,
                a.id_arb_mes AS tree_id,
                a.essence AS species,
                a.dhp AS dbh_mm,
                a.tige_ha AS expansion_factor_stems_ha,
                a.st_tige AS basal_area_m2,
                a.st_ha AS basal_area_m2_ha,
                a.vmb_tige AS pep_vmb_tige,
                a.vmb_ha AS pep_vmb_ha,
                a.dic AS pep_dic,
                a.hauteurdic AS pep_hauteurdic,
                e.haut_arbre AS height_dm,
                l.no_mes AS no_mes,
                l.date_sond AS date_sond,
                l.dimension AS plot_design,
                COALESCE(l.statut_mes, '') AS statut_mes,
                p.reseau AS network
            FROM dendro_arbres AS a
            JOIN latest_visit AS l
              ON l.id_pe_mes = a.id_pe_mes AND l.visit_rank = 1
            JOIN placette AS p ON p.id_pe = a.id_pe
            LEFT JOIN dendro_arbres_etudes AS e ON e.id_arb_mes = a.id_arb_mes
            WHERE a.etat IN ({live})
              AND a.dhp BETWEEN 50 AND 1000
              AND a.tige_ha > 0
              AND a.essence IN ({species})
              AND e.haut_arbre BETWEEN 50 AND 400
        ),
        plot_candidate AS (
            SELECT site_id, visit_id, date_sond, plot_design, network,
                   COUNT(*) AS n_candidate_trees
            FROM candidate
            GROUP BY site_id, visit_id, date_sond, plot_design, network
        ),
        ranked_plot AS (
            SELECT *,
                   ROW_NUMBER() OVER (
                       ORDER BY date_sond DESC, site_id ASC
                   ) AS plot_rank
            FROM plot_candidate
        )
        SELECT c.*, r.plot_rank
        FROM candidate AS c
        JOIN ranked_plot AS r
          ON r.site_id = c.site_id AND r.visit_id = c.visit_id
        WHERE r.plot_rank <= :max_plots
        ORDER BY r.plot_rank, c.tree_id
        """,
        {"max_plots": int(max_plots)},
    )
    if frame.empty:
        raise QCValidationError("no eligible PEP trees were selected")

    frame["measurement_date"] = frame["date_sond"].astype(str).str.slice(0, 10)
    frame["measurement_year"] = pd.to_numeric(
        frame["measurement_date"].str.slice(0, 4), errors="coerce"
    ).astype(int)
    frame["dbh_cm"] = frame["dbh_mm"].astype(float) / 10.0
    frame["height_m"] = frame["height_dm"].astype(float) / 10.0
    frame["inclusion_area_ha"] = frame["expansion_factor_stems_ha"].map(inclusion_area_ha)
    frame["species_group"] = np.where(
        frame["species"].isin(DECIDUOUS_CODES), "deciduous", "conifer"
    )
    frame["network"] = frame["network"].fillna("unknown").astype(str)
    frame["plot_design"] = frame["plot_design"].fillna("unknown").astype(str)
    frame["statut_mes"] = (
        frame["statut_mes"].fillna("").astype(str).replace("", "not_recorded")
    )
    frame["pep_vmb_tige_present"] = frame["pep_vmb_tige"].notna()
    frame["pep_vmb_ha_present"] = frame["pep_vmb_ha"].notna()
    frame["pep_dic_present"] = frame["pep_dic"].notna() & (
        frame["pep_dic"].astype(str).str.strip() != ""
    )
    frame["pep_hauteurdic_present"] = frame["pep_hauteurdic"].notna()
    frame["pep_compiled_volume_available"] = (
        frame["pep_vmb_tige_present"] | frame["pep_vmb_ha_present"]
    )

    cap_applied = len(frame) > max_trees
    if cap_applied:
        frame = frame.assign(
            _stable_rank=frame["tree_id"].map(
                lambda value: hashlib.sha256(str(value).encode("utf-8")).hexdigest()
            )
        ).sort_values(["_stable_rank", "tree_id"], kind="mergesort")
        frame = frame.head(max_trees).drop(columns=["_stable_rank"])
        frame = frame.sort_values(["plot_rank", "tree_id"], kind="mergesort")

    frame = frame.reset_index(drop=True)
    selection: dict[str, Any] = {
        "rule": (
            "Latest eligible visit per PEP plot (exclude visit status AB, DE, NT), "
            f"ordered by measurement date descending and id_pe ascending; first "
            f"{max_plots} plots; every eligible tree in those plots; deterministic "
            f"hash cap at {max_trees} trees if needed."
        ),
        "max_plots": int(max_plots),
        "max_trees": int(max_trees),
        "cap_applied": bool(cap_applied),
        "selected_plots": int(frame["site_id"].nunique()),
        "selected_visits": int(frame["visit_id"].nunique()),
        "selected_trees": len(frame),
        "selected_stems": int(frame["stem_id"].nunique()),
        "year_min": int(frame["measurement_year"].min()),
        "year_max": int(frame["measurement_year"].max()),
        "date_min": str(frame["measurement_date"].min()),
        "date_max": str(frame["measurement_date"].max()),
        "live_codes": list(LIVE_CODES),
        "taper_species": list(TAPER_SPECIES),
        "dbh_domain_cm": [TAPER_DBH_MIN_CM, TAPER_DBH_MAX_CM],
        "height_domain_m": [TAPER_HEIGHT_MIN_M, TAPER_HEIGHT_MAX_M],
        "height_field": "dendro_arbres_etudes.haut_arbre (decimetres)",
        "excluded_height_field": "dendro_arbres.hauteurdef (defect height, not total height)",
        "expansion_field": "dendro_arbres.tige_ha (stems per hectare)",
        "defect_pct": 0.0,
        "defect_note": (
            "PEP has defect type codes but no per-tree defect severity percentage; "
            "defect_pct is set to zero for the dimension-only pilot and is a stated "
            "limitation, not an observation that the trees are defect-free."
        ),
        "plot_area_note": (
            "PEP plots are nested fixed-area designs.  The main plot is 400 m2 "
            "(0.04 ha); large trees on dimension 10/20 plots have a 625 m2 "
            "(0.0625 ha) inclusion area.  The per-tree inclusion_area_ha = 1/tige_ha "
            "is used for expansion; nominal_plot_area_ha is the main 0.04 ha plot."
        ),
        "funnel": _selection_funnel(connection, deadline) if deadline else _selection_funnel(connection, Deadline(None)),
        "year_counts": {
            str(int(year)): int(count)
            for year, count in frame["measurement_year"].value_counts().sort_index().items()
        },
        "species_counts": {
            str(species_code): int(count)
            for species_code, count in frame["species"].value_counts().sort_index().items()
        },
        "plot_design_counts": {
            str(design): int(count)
            for design, count in frame["plot_design"].value_counts().sort_index().items()
        },
        "network_counts": {
            str(network): int(count)
            for network, count in frame["network"].value_counts().sort_index().items()
        },
        "expansion_factor_counts": {
            str(float(factor)): int(count)
            for factor, count in frame["expansion_factor_stems_ha"]
            .value_counts()
            .sort_index()
            .items()
        },
    }
    if selection["selected_plots"] != min(max_plots, selection["funnel"]["candidate_plots"]):
        raise QCValidationError(
            "selected plot count does not equal min(max_plots, candidate_plots): "
            f"{selection['selected_plots']} vs {min(max_plots, selection['funnel']['candidate_plots'])}"
        )
    return frame, selection


def _resolve_tapers(
    frame: pd.DataFrame, registry: SchneiderTaperRegistry
) -> dict[str, Any]:
    tapers: dict[str, Any] = {}
    for species in sorted(frame["species"].unique()):
        taper = build_taper("QC", str(species), registry, {})
        tapers[str(species)] = taper
    return tapers


def _build_pilot_trees(
    frame: pd.DataFrame, tapers: Mapping[str, Any]
) -> list[PilotTree]:
    trees: list[PilotTree] = []
    for record in frame.to_dict("records"):
        species = str(record["species"])
        taper = tapers[species]
        trees.append(
            PilotTree(
                tree_id=str(record["tree_id"]),
                site_id=str(record["site_id"]),
                jurisdiction="QC",
                species=species,
                species_group=str(record["species_group"]),
                dbh_cm=float(record["dbh_cm"]),
                height_m=float(record["height_m"]),
                defect_pct=0.0,
                expansion_factor=float(record["expansion_factor_stems_ha"]),
                status="live",
                provenance="qc_pep_measured_latest_visit",
                evidence="published_table",
                taper_id=taper.taper_id,
            )
        )
    if not trees:
        raise QCValidationError("no PilotTree records were built")
    return trees


def _label_row(row: dict[str, Any]) -> dict[str, Any]:
    row.update(LABELS)
    return row


def _base_context(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "tree_id": str(record["tree_id"]),
        "stem_id": str(record["stem_id"]),
        "site_id": str(record["site_id"]),
        "visit_id": str(record["visit_id"]),
        "species": str(record["species"]),
        "species_group": str(record["species_group"]),
        "dbh_cm": float(record["dbh_cm"]),
        "height_m": float(record["height_m"]),
        "expansion_factor_stems_ha": float(record["expansion_factor_stems_ha"]),
        "inclusion_area_ha": float(record["inclusion_area_ha"]),
    }


def _buck_population(
    frame: pd.DataFrame,
    trees: Sequence[PilotTree],
    tapers: Mapping[str, Any],
    rules: Sequence[Any],
    deadline: Deadline,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    records = frame.to_dict("records")
    if len(records) != len(trees):
        raise QCValidationError("tree frame and PilotTree list are not aligned")
    tree_rows: list[dict[str, Any]] = []
    log_rows: list[dict[str, Any]] = []
    residue_rows: list[dict[str, Any]] = []
    for index, (record, tree) in enumerate(zip(records, trees, strict=True), start=1):
        if index % 100 == 0:
            deadline.check("bucking")
        if str(record["tree_id"]) != tree.tree_id:
            raise QCValidationError("tree frame and PilotTree identity mismatch")
        taper = tapers[tree.species]
        logs, residue = buck_tree(tree, taper, tuple(rules))
        context = _base_context(record)
        recovered_gross = sum(log.volume_m3 for log in logs)
        recovered_net = sum(log.net_volume_m3 for log in logs)
        defect = sum(log.defect_deduction_m3 for log in logs)
        value = sum(log.value_cad for log in logs)
        expansion = context["expansion_factor_stems_ha"]

        tree_rows.append(
            _label_row(
                {
                    **context,
                    "no_mes": float(record["no_mes"]),
                    "measurement_date": str(record["measurement_date"]),
                    "measurement_year": int(record["measurement_year"]),
                    "network": str(record["network"]),
                    "plot_design": str(record["plot_design"]),
                    "statut_mes": str(record["statut_mes"]),
                    "dbh_mm": float(record["dbh_mm"]),
                    "height_dm": float(record["height_dm"]),
                    "nominal_plot_area_ha": NOMINAL_PLOT_AREA_HA,
                    "basal_area_m2": float(record["basal_area_m2"]),
                    "basal_area_m2_ha": float(record["basal_area_m2_ha"]),
                    "defect_pct": 0.0,
                    "defect_source": "not_available_pep_defect_code_only",
                    "taper_id": taper.taper_id,
                    "taper_evidence": "published_table",
                    "tree_list_evidence": "measured_tree_list",
                    "provenance": tree.provenance,
                    "evidence": "published_table",
                    "taper_total_m3": residue.taper_total_m3,
                    "recovered_gross_m3": recovered_gross,
                    "recovered_net_m3": recovered_net,
                    "defect_deduction_m3": defect,
                    "residue_m3": residue.residue_volume_m3,
                    "residue_odt": residue.residue_odt,
                    "residue_green_tonnes": residue.green_tonnes,
                    "chip_green_tonnes": residue.chip_green_tonnes,
                    "hog_green_tonnes": residue.hog_green_tonnes,
                    "value_cad": value,
                    "n_logs": len(logs),
                    "bucked_status": "bucked" if logs else "no_merchantable_logs",
                    "balance_error_m3": residue.balance_error_m3,
                    "taper_total_m3_per_ha": residue.taper_total_m3 * expansion,
                    "recovered_gross_m3_per_ha": recovered_gross * expansion,
                    "recovered_net_m3_per_ha": recovered_net * expansion,
                    "residue_m3_per_ha": residue.residue_volume_m3 * expansion,
                    "residue_odt_per_ha": residue.residue_odt * expansion,
                    "residue_green_tonnes_per_ha": residue.green_tonnes * expansion,
                    "value_cad_per_ha": value * expansion,
                    "pep_vmb_tige_present": bool(record["pep_vmb_tige_present"]),
                    "pep_vmb_ha_present": bool(record["pep_vmb_ha_present"]),
                    "pep_dic_present": bool(record["pep_dic_present"]),
                    "pep_hauteurdic_present": bool(record["pep_hauteurdic_present"]),
                    "pep_compiled_volume_available": bool(
                        record["pep_compiled_volume_available"]
                    ),
                }
            )
        )

        for log in logs:
            log_rows.append(
                _label_row(
                    {
                        **context,
                        **log.to_dict(),
                        "site_id": context["site_id"],
                        "visit_id": context["visit_id"],
                        "species": context["species"],
                        "species_group": context["species_group"],
                        "dbh_cm": context["dbh_cm"],
                        "height_m": context["height_m"],
                        "expansion_factor_stems_ha": expansion,
                        "inclusion_area_ha": context["inclusion_area_ha"],
                        "volume_m3_per_ha": log.volume_m3 * expansion,
                        "net_volume_m3_per_ha": log.net_volume_m3 * expansion,
                        "value_cad_per_ha": log.value_cad * expansion,
                        "tree_list_evidence": "measured_tree_list",
                        "price_evidence": "assumption",
                    }
                )
            )

        residue_rows.append(
            _label_row(
                {
                    **context,
                    **residue.to_dict(),
                    "site_id": context["site_id"],
                    "visit_id": context["visit_id"],
                    "species": context["species"],
                    "species_group": context["species_group"],
                    "dbh_cm": context["dbh_cm"],
                    "height_m": context["height_m"],
                    "expansion_factor_stems_ha": expansion,
                    "inclusion_area_ha": context["inclusion_area_ha"],
                    "n_logs": len(logs),
                    "bucked_status": "bucked" if logs else "no_merchantable_logs",
                    "residue_volume_m3_per_ha": residue.residue_volume_m3 * expansion,
                    "residue_odt_per_ha": residue.residue_odt * expansion,
                    "green_tonnes_per_ha": residue.green_tonnes * expansion,
                    "chip_green_tonnes_per_ha": residue.chip_green_tonnes * expansion,
                    "hog_green_tonnes_per_ha": residue.hog_green_tonnes * expansion,
                    "tree_list_evidence": "measured_tree_list",
                }
            )
        )

    tree_frame = pd.DataFrame(tree_rows)
    log_frame = pd.DataFrame(log_rows)
    residue_frame = pd.DataFrame(residue_rows)
    if tree_frame.empty:
        raise QCValidationError("bucking produced no tree rows")
    if log_frame.empty:
        raise QCValidationError("bucking produced no logs")
    if len(residue_frame) != len(tree_frame):
        raise QCValidationError("residue ledger does not cover every tree")
    return tree_frame, log_frame, residue_frame


def _plot_summary(tree_frame: pd.DataFrame) -> pd.DataFrame:
    grouped = tree_frame.groupby(
        [
            "site_id",
            "visit_id",
            "measurement_date",
            "measurement_year",
            "network",
            "plot_design",
        ],
        dropna=False,
        sort=True,
    )
    rows: list[dict[str, Any]] = []
    for keys, group in grouped:
        site_id, visit_id, date, year, network, design = keys
        dominant = group["species"].value_counts().idxmax()
        rows.append(
            _label_row(
                {
                    "site_id": site_id,
                    "visit_id": visit_id,
                    "measurement_date": date,
                    "measurement_year": int(year),
                    "network": network,
                    "plot_design": design,
                    "n_trees": len(group),
                    "n_trees_with_logs": int((group["n_logs"] > 0).sum()),
                    "n_logs": int(group["n_logs"].sum()),
                    "n_species": int(group["species"].nunique()),
                    "dominant_species": dominant,
                    "mean_dbh_cm": float(group["dbh_cm"].mean()),
                    "mean_height_m": float(group["height_m"].mean()),
                    "sum_expansion_factor_stems_ha": float(
                        group["expansion_factor_stems_ha"].sum()
                    ),
                    "taper_total_m3": float(group["taper_total_m3"].sum()),
                    "recovered_gross_m3": float(group["recovered_gross_m3"].sum()),
                    "recovered_net_m3": float(group["recovered_net_m3"].sum()),
                    "residue_m3": float(group["residue_m3"].sum()),
                    "residue_odt": float(group["residue_odt"].sum()),
                    "value_cad": float(group["value_cad"].sum()),
                    "taper_total_m3_per_ha": float(group["taper_total_m3_per_ha"].sum()),
                    "recovered_gross_m3_per_ha": float(
                        group["recovered_gross_m3_per_ha"].sum()
                    ),
                    "recovered_net_m3_per_ha": float(
                        group["recovered_net_m3_per_ha"].sum()
                    ),
                    "residue_m3_per_ha": float(group["residue_m3_per_ha"].sum()),
                    "residue_odt_per_ha": float(group["residue_odt_per_ha"].sum()),
                    "value_cad_per_ha": float(group["value_cad_per_ha"].sum()),
                    "balance_error_m3": float(group["balance_error_m3"].sum()),
                    "expanded_balance_error_m3": float(
                        (
                            group["recovered_gross_m3_per_ha"]
                            + group["residue_m3_per_ha"]
                            - group["taper_total_m3_per_ha"]
                        ).abs().sum()
                    ),
                    "nominal_plot_area_ha": NOMINAL_PLOT_AREA_HA,
                    "evidence": "assumption",
                    "taper_evidence": "published_table",
                    "tree_list_evidence": "measured_tree_list",
                }
            )
        )
    return pd.DataFrame(rows).sort_values(["measurement_date", "site_id"], ascending=[False, True])


def _species_summary(tree_frame: pd.DataFrame, n_plots: int) -> pd.DataFrame:
    if n_plots <= 0:
        raise QCValidationError("n_plots must be positive")
    total_taper = float(tree_frame["taper_total_m3"].sum())
    total_recovered = float(tree_frame["recovered_gross_m3"].sum())
    total_residue = float(tree_frame["residue_m3"].sum())
    rows: list[dict[str, Any]] = []
    for species, group in tree_frame.groupby("species", sort=True):
        rows.append(
            _label_row(
                {
                    "species": species,
                    "common_name": SPECIES_COMMON.get(species, "unknown"),
                    "species_group": group["species_group"].iloc[0],
                    "taper_id": group["taper_id"].iloc[0],
                    "n_trees": len(group),
                    "n_plots": int(group["site_id"].nunique()),
                    "n_logs": int(group["n_logs"].sum()),
                    "dbh_cm_min": float(group["dbh_cm"].min()),
                    "dbh_cm_mean": float(group["dbh_cm"].mean()),
                    "dbh_cm_max": float(group["dbh_cm"].max()),
                    "height_m_min": float(group["height_m"].min()),
                    "height_m_mean": float(group["height_m"].mean()),
                    "height_m_max": float(group["height_m"].max()),
                    "mean_expansion_factor_stems_ha": float(
                        group["expansion_factor_stems_ha"].mean()
                    ),
                    "taper_total_m3": float(group["taper_total_m3"].sum()),
                    "recovered_gross_m3": float(group["recovered_gross_m3"].sum()),
                    "recovered_net_m3": float(group["recovered_net_m3"].sum()),
                    "residue_m3": float(group["residue_m3"].sum()),
                    "residue_odt": float(group["residue_odt"].sum()),
                    "value_cad": float(group["value_cad"].sum()),
                    "taper_total_m3_per_ha": float(
                        group["taper_total_m3_per_ha"].sum() / n_plots
                    ),
                    "recovered_gross_m3_per_ha": float(
                        group["recovered_gross_m3_per_ha"].sum() / n_plots
                    ),
                    "residue_m3_per_ha": float(
                        group["residue_m3_per_ha"].sum() / n_plots
                    ),
                    "residue_odt_per_ha": float(
                        group["residue_odt_per_ha"].sum() / n_plots
                    ),
                    "value_cad_per_ha": float(group["value_cad_per_ha"].sum() / n_plots),
                    "share_of_taper_total": _safe_div(
                        float(group["taper_total_m3"].sum()), total_taper
                    ),
                    "share_of_recovered": _safe_div(
                        float(group["recovered_gross_m3"].sum()), total_recovered
                    ),
                    "share_of_residue": _safe_div(
                        float(group["residue_m3"].sum()), total_residue
                    ),
                    "balance_error_m3": float(group["balance_error_m3"].sum()),
                    "evidence": "assumption",
                    "taper_evidence": "published_table",
                    "tree_list_evidence": "measured_tree_list",
                }
            )
        )
    return pd.DataFrame(rows).sort_values("species")


def _comparison(
    tree_frame: pd.DataFrame,
    n_plots: int,
    full_vmb_counts: Mapping[str, int],
) -> pd.DataFrame:
    total_taper = float(tree_frame["taper_total_m3"].sum())
    total_recovered = float(tree_frame["recovered_gross_m3"].sum())
    total_residue = float(tree_frame["residue_m3"].sum())
    rows: list[dict[str, Any]] = []
    scopes: list[tuple[str, str, pd.DataFrame]] = [("ALL", "ALL", tree_frame)]
    for species, group in tree_frame.groupby("species", sort=True):
        scopes.append(("species", str(species), group))
    for scope, label, group in scopes:
        taper = float(group["taper_total_m3"].sum())
        recovered = float(group["recovered_gross_m3"].sum())
        residue = float(group["residue_m3"].sum())
        rows.append(
            _label_row(
                {
                    "scope": scope,
                    "species": label,
                    "common_name": "all species" if label == "ALL" else SPECIES_COMMON.get(label, "unknown"),
                    "n_trees": len(group),
                    "n_plots": int(group["site_id"].nunique()) if label != "ALL" else int(n_plots),
                    "n_logs": int(group["n_logs"].sum()),
                    "taper_total_m3": taper,
                    "recovered_gross_m3": recovered,
                    "residue_m3": residue,
                    "residue_odt": float(group["residue_odt"].sum()),
                    "taper_total_m3_per_ha": float(
                        group["taper_total_m3_per_ha"].sum() / n_plots
                    ),
                    "recovered_gross_m3_per_ha": float(
                        group["recovered_gross_m3_per_ha"].sum() / n_plots
                    ),
                    "residue_m3_per_ha": float(
                        group["residue_m3_per_ha"].sum() / n_plots
                    ),
                    "pep_vmb_tige_non_null": int(group["pep_vmb_tige_present"].sum()),
                    "pep_vmb_ha_non_null": int(group["pep_vmb_ha_present"].sum()),
                    "pep_vmb_tige_non_null_full_table": int(full_vmb_counts["vmb_tige_non_null"]),
                    "pep_vmb_ha_non_null_full_table": int(full_vmb_counts["vmb_ha_non_null"]),
                    "pep_compiled_volume_fields_populated": bool(
                        group["pep_compiled_volume_available"].any()
                    ),
                    "pep_biomass_fields_present": False,
                    "comparison_status": "taper_only_no_pep_compiled_volume",
                    "comparison_note": (
                        "PEP dendro_arbres.vmb_tige and vmb_ha exist in the schema "
                        "but are null for every tree row; no PEP biomass field exists. "
                        "Taper total and residue are reported without a compiled-volume "
                        "comparison. Values are not an observed harvest or a national claim."
                    ),
                    "share_of_total_taper": _safe_div(taper, total_taper),
                    "share_of_total_recovered": _safe_div(recovered, total_recovered),
                    "share_of_total_residue": _safe_div(residue, total_residue),
                    "evidence": "published_table",
                    "taper_evidence": "published_table",
                    "tree_list_evidence": "measured_tree_list",
                }
            )
        )
    return pd.DataFrame(rows)


def _totals(tree_frame: pd.DataFrame, log_frame: pd.DataFrame, n_plots: int) -> dict[str, Any]:
    if n_plots <= 0:
        raise QCValidationError("n_plots must be positive")
    taper = float(tree_frame["taper_total_m3"].sum())
    recovered = float(tree_frame["recovered_gross_m3"].sum())
    recovered_net = float(tree_frame["recovered_net_m3"].sum())
    residue = float(tree_frame["residue_m3"].sum())
    residue_odt = float(tree_frame["residue_odt"].sum())
    value = float(tree_frame["value_cad"].sum())
    product_volumes = (
        log_frame.groupby("product")["volume_m3"].sum().to_dict() if not log_frame.empty else {}
    )
    sawlog = float(product_volumes.get("sawlog", 0.0))
    pulpwood = float(product_volumes.get("pulpwood", 0.0))
    other = float(product_volumes.get("other", 0.0))
    peeler = float(product_volumes.get("peeler", 0.0))
    return {
        "n_trees": len(tree_frame),
        "n_plots": int(n_plots),
        "n_logs": len(log_frame),
        "n_trees_with_logs": int((tree_frame["n_logs"] > 0).sum()),
        "n_trees_without_logs": int((tree_frame["n_logs"] == 0).sum()),
        "taper_total_m3": taper,
        "recovered_gross_m3": recovered,
        "recovered_net_m3": recovered_net,
        "defect_deduction_m3": float(tree_frame["defect_deduction_m3"].sum()),
        "residue_m3": residue,
        "residue_odt": residue_odt,
        "residue_green_tonnes": float(tree_frame["residue_green_tonnes"].sum()),
        "chip_green_tonnes": float(tree_frame["chip_green_tonnes"].sum()),
        "hog_green_tonnes": float(tree_frame["hog_green_tonnes"].sum()),
        "value_cad": value,
        "taper_total_m3_per_ha": float(tree_frame["taper_total_m3_per_ha"].sum() / n_plots),
        "recovered_gross_m3_per_ha": float(
            tree_frame["recovered_gross_m3_per_ha"].sum() / n_plots
        ),
        "recovered_net_m3_per_ha": float(
            tree_frame["recovered_net_m3_per_ha"].sum() / n_plots
        ),
        "residue_m3_per_ha": float(tree_frame["residue_m3_per_ha"].sum() / n_plots),
        "residue_odt_per_ha": float(tree_frame["residue_odt_per_ha"].sum() / n_plots),
        "value_cad_per_ha": float(tree_frame["value_cad_per_ha"].sum() / n_plots),
        "mean_taper_total_m3_per_tree": _safe_div(taper, len(tree_frame)),
        "mean_logs_per_tree": _safe_div(len(log_frame), len(tree_frame)),
        "mean_log_length_m": float(log_frame["length_m"].mean()) if not log_frame.empty else 0.0,
        "sawlog_m3": sawlog,
        "peeler_m3": peeler,
        "pulpwood_m3": pulpwood,
        "other_m3": other,
        "sawlog_share": _safe_div(sawlog, recovered),
        "pulpwood_share": _safe_div(pulpwood, recovered),
        "other_share": _safe_div(other, recovered),
        "balance_error_m3": abs(recovered + residue - taper),
        "max_tree_balance_error_m3": float(tree_frame["balance_error_m3"].max()),
        "expanded_balance_error_m3": float(
            (
                tree_frame["recovered_gross_m3_per_ha"]
                + tree_frame["residue_m3_per_ha"]
                - tree_frame["taper_total_m3_per_ha"]
            ).abs().sum()
        ),
    }


def _product_summary(log_frame: pd.DataFrame) -> pd.DataFrame:
    if log_frame.empty:
        return pd.DataFrame()
    grouped = (
        log_frame.groupby(["product", "sort_code", "grade", "grade_status"], dropna=False)
        .agg(
            n_logs=("tree_id", "size"),
            n_trees=("tree_id", "nunique"),
            volume_m3=("volume_m3", "sum"),
            net_volume_m3=("net_volume_m3", "sum"),
            value_cad=("value_cad", "sum"),
            mean_length_m=("length_m", "mean"),
        )
        .reset_index()
        .sort_values(["product", "sort_code"])
    )
    return grouped


def _check_result(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "detail": detail}


def _numeric_violations(frame: pd.DataFrame, name: str) -> dict[str, Any]:
    numeric = frame.select_dtypes(include=[np.number])
    if numeric.empty:
        return _check_result(name, True, "no numeric columns")
    values = numeric.to_numpy(dtype=float)
    nan_count = int(np.isnan(values).sum())
    negative_count = int((values < -1e-12).sum())
    passed = nan_count == 0 and negative_count == 0
    return _check_result(
        name,
        passed,
        f"nan={nan_count}, negative={negative_count}, rows={len(frame)}",
    )


def _validate_frames(
    tree_frame: pd.DataFrame,
    log_frame: pd.DataFrame,
    residue_frame: pd.DataFrame,
    plot_summary: pd.DataFrame,
    species_summary: pd.DataFrame,
    comparison: pd.DataFrame,
    summary: Mapping[str, Any],
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    missing_tree = sorted(set(_TREE_REQUIRED_COLUMNS) - set(tree_frame.columns))
    missing_log = sorted(set(_LOG_REQUIRED_COLUMNS) - set(log_frame.columns))
    missing_residue = sorted(set(_RESIDUE_REQUIRED_COLUMNS) - set(residue_frame.columns))
    checks.append(
        _check_result(
            "schema_columns",
            not missing_tree and not missing_log and not missing_residue,
            f"tree_missing={missing_tree}, log_missing={missing_log}, residue_missing={missing_residue}",
        )
    )
    checks.append(
        _check_result(
            "unique_tree_ids",
            bool(tree_frame["tree_id"].is_unique),
            f"trees={len(tree_frame)}, unique={tree_frame['tree_id'].nunique()}",
        )
    )
    checks.append(
        _check_result(
            "residue_covers_every_tree",
            set(residue_frame["tree_id"]) == set(tree_frame["tree_id"]),
            f"residue={residue_frame['tree_id'].nunique()}, trees={tree_frame['tree_id'].nunique()}",
        )
    )
    checks.append(
        _check_result(
            "logs_are_subset_of_trees",
            set(log_frame["tree_id"]).issubset(set(tree_frame["tree_id"])),
            f"log_trees={log_frame['tree_id'].nunique()}",
        )
    )

    expansion_error = (
        tree_frame["expansion_factor_stems_ha"] * tree_frame["inclusion_area_ha"] - 1.0
    ).abs().max()
    checks.append(
        _check_result(
            "expansion_identity",
            float(expansion_error) <= 1e-9,
            f"max |expansion * inclusion_area - 1| = {float(expansion_error):.3e}",
        )
    )
    tree_balance = (
        tree_frame["recovered_gross_m3"]
        + tree_frame["residue_m3"]
        - tree_frame["taper_total_m3"]
    ).abs().max()
    checks.append(
        _check_result(
            "tree_volume_residue_balance",
            float(tree_balance) <= 1e-9,
            f"max tree balance error = {float(tree_balance):.3e} m3",
        )
    )
    residue_balance = (
        residue_frame["recovered_log_m3"]
        + residue_frame["residue_volume_m3"]
        - residue_frame["taper_total_m3"]
    ).abs().max()
    checks.append(
        _check_result(
            "residue_ledger_balance",
            float(residue_balance) <= 1e-9,
            f"max ledger balance error = {float(residue_balance):.3e} m3",
        )
    )
    log_sums = log_frame.groupby("tree_id")["volume_m3"].sum().rename("log_total")
    joined = tree_frame.set_index("tree_id")[["recovered_gross_m3"]].join(log_sums)
    joined["log_total"] = joined["log_total"].fillna(0.0)
    log_error = float((joined["log_total"] - joined["recovered_gross_m3"]).abs().max())
    checks.append(
        _check_result(
            "log_sum_matches_tree_recovered",
            log_error <= 1e-9,
            f"max |sum logs - recovered| = {log_error:.3e} m3",
        )
    )

    plot_tree = (
        tree_frame.groupby("site_id")
        .agg(
            n_trees=("tree_id", "size"),
            taper_total_m3=("taper_total_m3", "sum"),
            recovered_gross_m3=("recovered_gross_m3", "sum"),
            residue_m3=("residue_m3", "sum"),
            taper_total_m3_per_ha=("taper_total_m3_per_ha", "sum"),
        )
        .reset_index()
    )
    plot_check = plot_summary.merge(plot_tree, on="site_id", suffixes=("", "_tree"))
    plot_errors = {
        column: float(
            (plot_check[column] - plot_check[f"{column}_tree"]).abs().max()
        )
        for column in ("n_trees", "taper_total_m3", "recovered_gross_m3", "residue_m3", "taper_total_m3_per_ha")
    }
    checks.append(
        _check_result(
            "plot_summary_reconciles",
            max(plot_errors.values()) <= 1e-9,
            f"max errors={plot_errors}",
        )
    )
    species_check = species_summary.merge(
        tree_frame.groupby("species")
        .agg(
            n_trees=("tree_id", "size"),
            taper_total_m3=("taper_total_m3", "sum"),
            recovered_gross_m3=("recovered_gross_m3", "sum"),
            residue_m3=("residue_m3", "sum"),
        )
        .reset_index(),
        on="species",
        suffixes=("", "_tree"),
    )
    species_errors = {
        column: float((species_check[column] - species_check[f"{column}_tree"]).abs().max())
        for column in ("n_trees", "taper_total_m3", "recovered_gross_m3", "residue_m3")
    }
    checks.append(
        _check_result(
            "species_summary_reconciles",
            max(species_errors.values()) <= 1e-9,
            f"max errors={species_errors}",
        )
    )
    all_row = comparison[comparison["scope"] == "ALL"]
    checks.append(
        _check_result(
            "comparison_all_row",
            len(all_row) == 1
            and int(all_row.iloc[0]["n_trees"]) == len(tree_frame)
            and abs(float(all_row.iloc[0]["taper_total_m3"]) - float(tree_frame["taper_total_m3"].sum())) <= 1e-8,
            f"all_rows={len(all_row)}",
        )
    )

    evidence_ok = bool(
        tree_frame["taper_evidence"].eq("published_table").all()
        and tree_frame["tree_list_evidence"].eq("measured_tree_list").all()
        and tree_frame["evidence"].isin(EVIDENCE_TIERS).all()
        and log_frame["taper_evidence"].eq("published_table").all()
        and log_frame["evidence"].isin(EVIDENCE_TIERS).all()
        and residue_frame["taper_evidence"].eq("published_table").all()
        and residue_frame["evidence"].isin(EVIDENCE_TIERS).all()
    )
    checks.append(_check_result("evidence_tiers", evidence_ok, "published_table taper; assumption logs/residue"))
    grade_ok = bool(
        log_frame["grade_status"].eq("not_assigned").all()
        and log_frame["grade"].eq("not_assigned").all()
        and log_frame["policy_grade"].eq(False).all()
    )
    checks.append(_check_result("no_policy_grade", grade_ok, "grade=not_assigned and policy_grade=false"))
    labels_ok = True
    for frame in (tree_frame, log_frame, residue_frame, plot_summary, species_summary):
        labels_ok = labels_ok and all(
            bool(frame[column].eq(value).all()) for column, value in LABELS.items()
        )
    checks.append(_check_result("labels", labels_ok, str(LABELS)))

    checks.append(_numeric_violations(tree_frame, "tree_numeric_finite_non_negative"))
    checks.append(_numeric_violations(log_frame, "log_numeric_finite_non_negative"))
    checks.append(_numeric_violations(residue_frame, "residue_numeric_finite_non_negative"))
    checks.append(_numeric_violations(plot_summary, "plot_numeric_finite_non_negative"))
    checks.append(_numeric_violations(species_summary, "species_numeric_finite_non_negative"))
    checks.append(_numeric_violations(comparison, "comparison_numeric_finite_non_negative"))

    compiled_ok = bool(
        not tree_frame["pep_compiled_volume_available"].any()
        and (comparison["comparison_status"] == "taper_only_no_pep_compiled_volume").all()
    )
    checks.append(
        _check_result(
            "compiled_volume_fields_absent",
            compiled_ok,
            "no populated PEP vmb_tige/vmb_ha or biomass fields; taper-only comparison",
        )
    )
    checks.append(
        _check_result(
            "no_national_claim",
            bool(tree_frame["national_claim"].eq(False).all()),
            "national_claim=false on every row",
        )
    )
    checks.append(
        _check_result(
            "summary_counts",
            int(summary["totals"]["n_trees"]) == len(tree_frame)
            and int(summary["totals"]["n_logs"]) == len(log_frame)
            and int(summary["selection"]["selected_plots"]) == plot_summary["site_id"].nunique(),
            f"trees={len(tree_frame)}, logs={len(log_frame)}, plots={plot_summary['site_id'].nunique()}",
        )
    )
    failed = [check["name"] for check in checks if not check["passed"]]
    if failed:
        raise QCValidationError(f"validation checks failed: {failed}")
    return {"passed": True, "checks": checks}


def _source_schema_frame(schema: Mapping[str, Any]) -> pd.DataFrame:
    rows = [
        {
            "table": record["table"],
            "rows": record["rows"],
            "role": (
                "used: tree measurements"
                if record["table"] == "dendro_arbres"
                else "used: measured heights"
                if record["table"] == "dendro_arbres_etudes"
                else "used: plot visits"
                if record["table"] == "placette_mes"
                else "used: plots"
                if record["table"] == "placette"
                else "supporting"
            ),
        }
        for record in schema["tables"]
    ]
    return pd.DataFrame(rows)


def _report_markdown(
    source: Mapping[str, Any],
    schema: Mapping[str, Any],
    selection: Mapping[str, Any],
    totals: Mapping[str, Any],
    species_summary: pd.DataFrame,
    comparison: pd.DataFrame,
    product_summary: pd.DataFrame,
    checks: Mapping[str, Any],
) -> str:
    species_table = species_summary[
        [
            "species",
            "common_name",
            "n_trees",
            "n_plots",
            "dbh_cm_mean",
            "height_m_mean",
            "taper_total_m3",
            "recovered_gross_m3",
            "residue_m3",
            "taper_total_m3_per_ha",
        ]
    ].copy()
    species_table.columns = [
        "species",
        "common",
        "trees",
        "plots",
        "mean DBH cm",
        "mean H m",
        "taper m3",
        "logs m3",
        "residue m3",
        "mean taper m3/ha",
    ]
    comparison_table = comparison[
        ["scope", "species", "n_trees", "taper_total_m3", "recovered_gross_m3", "residue_m3"]
    ].copy()
    comparison_table.columns = ["scope", "species", "trees", "taper m3", "logs m3", "residue m3"]
    product_table = (
        product_summary[
            [
                "product",
                "sort_code",
                "n_logs",
                "n_trees",
                "volume_m3",
                "net_volume_m3",
                "value_cad",
            ]
        ].copy()
        if not product_summary.empty
        else pd.DataFrame()
    )
    if not product_table.empty:
        product_table.columns = [
            "product",
            "sort",
            "logs",
            "trees",
            "gross m3",
            "net m3",
            "value CAD",
        ]
    funnel = selection["funnel"]
    funnel_table = pd.DataFrame(
        [
            ("Latest-visit trees in PEP", funnel["latest_visit_trees"]),
            ("Live trees", funnel["live_trees"]),
            ("Live, positive DBH and expansion", funnel["live_with_dbh_expansion"]),
            ("Live, species identified", funnel["live_with_species"]),
            ("Live, measured height", funnel["live_with_height"]),
            ("Live, height in taper domain", funnel["live_with_height_domain"]),
            ("Live, species with Schneider taper", funnel["live_with_taper_species"]),
            ("Candidate trees in taper DBH domain", funnel["candidate_trees"]),
            ("Candidate plots", funnel["candidate_plots"]),
            ("Selected plots", selection["selected_plots"]),
            ("Selected trees", selection["selected_trees"]),
        ],
        columns=["stage", "count"],
    )
    check_table = pd.DataFrame(checks["checks"])[["name", "passed", "detail"]]
    return f"""# Quebec PEP measured-tree bucking validation v0

**Status:** model validation only.  Tree geometry is measured open data; every
log, residue, volume and value row is a model estimate.  `observed=false`,
`policy_grade=false`, `national_claim=false`.  This is not a provincial
inventory, an official Quebec grade, or a national supply estimate.

## Download/schema

| Field | Value |
|---|---|
| Dataset | {source['dataset_id']} |
| Publisher | {source['publisher']} |
| Catalogue | {source['dataset_url']} |
| Direct URL | {source['source_url']} |
| Retrieval UTC | {source['retrieved_utc']} |
| Licence | {source['licence']} |
| ZIP bytes / SHA-256 | {source['zip_bytes']:,} / `{source['zip_sha256']}` |
| GPKG member / bytes / SHA-256 | `{source['gpkg_member']}` / {source['gpkg_bytes']:,} / `{source['gpkg_sha256']}` |
| ZIP hash matches acquisition record | {source['zip_hash_matches_record']} |
| GPKG hash matches acquisition record | {source['gpkg_hash_matches_record']} |

PEP tables inspected: {len(schema['tables'])}.  Core measurement chain:
{schema['row_counts']['placette']:,} plots, {schema['row_counts']['placette_mes']:,} visits,
{schema['row_counts']['dendro_arbres']:,} tree measurements, and
{schema['row_counts']['dendro_arbres_etudes']:,} intensive-study rows.

| PEP field | Use |
|---|---|
| `dendro_arbres.dhp` | DBH in millimetres; converted to centimetres. |
| `dendro_arbres_etudes.haut_arbre` | Total tree height in decimetres; converted to metres. |
| `dendro_arbres.hauteurdef` | **Defect height, not total height**; not used. |
| `dendro_arbres.tige_ha` | Stems-per-hectare expansion; inclusion area = `1/tige_ha`. |
| `dendro_arbres.etat` | Live codes {", ".join(selection['live_codes'])}. |
| `dendro_arbres.essence` | Species code; only the published Schneider taper species are used. |
| `dendro_arbres.vmb_tige`, `vmb_ha` | Compiled gross merchantable volume fields; present but null for all {schema['counts']['trees']:,} tree rows. |

The full PEP tree table has {schema['counts']['vmb_tige_non_null']:,} non-null
`vmb_tige` values and {schema['counts']['vmb_ha_non_null']:,} non-null
`vmb_ha` values.  There is no PEP biomass field in the tree table.

## Sample

Selection is deterministic and recent, not random: {selection['rule']}
The sample contains **{selection['selected_plots']:,} plots / visits and
{selection['selected_trees']:,} trees**, measured {selection['date_min']} to
{selection['date_max']}.  {len(selection['taper_species'])} species with a
published Schneider taper are included.  Trees outside the taper domain
(DBH {selection['dbh_domain_cm'][0]:.0f}-{selection['dbh_domain_cm'][1]:.0f} cm,
height {selection['height_domain_m'][0]:.0f}-{selection['height_domain_m'][1]:.0f} m)
are excluded rather than extrapolated.  Plot design counts:
{", ".join(f"`{key}`={value:,}" for key, value in selection['plot_design_counts'].items())};
network tree counts:
{", ".join(f"`{key}`={value:,}" for key, value in selection['network_counts'].items())}.

{_markdown_table(funnel_table)}

The sample is **not** a provincial inventory estimate.  It over-represents
recently remeasured networks and the eight species with a published Quebec
taper; the many PEP species without a taper row are excluded.  `defect_pct`
is set to zero because PEP records defect type codes but not per-tree defect
severity; dimension-only sorting does not use defect severity, but net volume
would be overstated for defective stems if interpreted as a merchantable
recovery estimate.

## Logs/residue

The Schneider (2013) fixed-effect tree taper is integrated inside bark from
the {STUMP_HEIGHT_M:.1f} m stump to total height.  The existing Quebec pilot
dimension-only sort set is used: sawlog top diameters 24/20/16 cm and
pulpwood 10 cm, with the pilot lengths and prices.  `grade=not_assigned` and
`policy_grade=false`; the sort set is a dimension contract, not an official
Quebec grade.

| Metric | Value |
|---|---:|
| Trees bucked | {totals['n_trees']:,} |
| Trees with at least one log | {totals['n_trees_with_logs']:,} |
| Trees with no merchantable log | {totals['n_trees_without_logs']:,} |
| Logs | {totals['n_logs']:,} |
| Taper total (inside bark) | {_fmt(totals['taper_total_m3'])} m3 |
| Recovered gross logs | {_fmt(totals['recovered_gross_m3'])} m3 |
| Recovered net logs | {_fmt(totals['recovered_net_m3'])} m3 |
| Stem residue | {_fmt(totals['residue_m3'])} m3 |
| Residue | {_fmt(totals['residue_odt'])} ODT |
| Sawlog share of recovered | {_fmt(totals['sawlog_share'] * 100, 1)}% |
| Pulpwood share of recovered | {_fmt(totals['pulpwood_share'] * 100, 1)}% |
| Mean taper total per tree | {_fmt(totals['mean_taper_total_m3_per_tree'], 4)} m3 |
| Mean taper total per ha | {_fmt(totals['taper_total_m3_per_ha'])} m3/ha |
| Mean residue per ha | {_fmt(totals['residue_m3_per_ha'])} m3/ha |
| Mean residue | {_fmt(totals['residue_odt_per_ha'])} ODT/ha |
| Balance error | {totals['balance_error_m3']:.3e} m3 |

Per-tree residue is `taper total - recovered gross logs`; it includes the
stump section above {STUMP_HEIGHT_M:.1f} m, top, and unmerchantable sections.
Density, moisture, chip and hog fractions are planning assumptions:
basic density from the national pilot species table, {_fmt(0.45 * 100, 0)}%
wet-basis moisture, 85% chip yield and 10% hog yield.  Volume below the
{STUMP_HEIGHT_M:.1f} m taper base is outside the taper total.  Bark is not
modelled: the taper is inside bark.

Species summary:

{_markdown_table(species_table)}

Product summary:

{_markdown_table(product_table) if not product_table.empty else "_No logs._"}

The value column uses the Quebec pilot price deck (`QC-PILOT-2026`) carried by
the dimension-only sort rules.  Prices are planning assumptions, not observed
transactions; the deliverable's primary quantities are volume and residue.

## Comparison

PEP's compiled volume fields `vmb_tige` and `vmb_ha` are present in
`dendro_arbres` but have zero non-null values across the full
{schema['counts']['trees']:,}-row tree table.  No PEP biomass field exists in
that table.  `dic` is a categorical code in this release, not a numeric
diameter, and `hauteurdic` is populated for only
{schema['counts']['hauteurdic_non_null']:,} rows; neither is a compiled
volume bank.  The comparison is therefore **taper total and residue only**;
there is no observed-versus-model volume comparison and no national claim.

{_markdown_table(comparison_table)}

The per-ha columns are design-based means across the sampled plots, with
per-tree expansion by `tige_ha`; they are not a provincial total.

## Tests/commits/status

All internal checks passed before artifacts were written:

{_markdown_table(check_table)}

Unexpected git deletions: none.  The source ZIP and member hashes match the
recorded acquisition values.  The output directory is
`data/derived/national/bucking/qc_pep_validation_v0/`.  Commit hashes and push
status are recorded in the final handoff after the data and parent commits.
"""


def _manifest(
    output_dir: Path,
    source: Mapping[str, Any],
    schema: Mapping[str, Any],
    selection: Mapping[str, Any],
    checks: Mapping[str, Any],
    script_path: Path,
    elapsed_seconds: float,
) -> dict[str, Any]:
    artifacts = []
    for name in REQUIRED_ARTIFACTS:
        if name == "manifest.json":
            continue
        path = output_dir / name
        artifacts.append(_hash_record(name, path, f"data/derived/national/bucking/qc_pep_validation_v0/{name}"))
    return {
        "validation": VALIDATION_NAME,
        "generated_utc": _utc_now(),
        "elapsed_seconds": elapsed_seconds,
        "source": dict(source),
        "schema_counts": dict(schema["counts"]),
        "selection": {
            "rule": selection["rule"],
            "selected_plots": selection["selected_plots"],
            "selected_trees": selection["selected_trees"],
            "date_min": selection["date_min"],
            "date_max": selection["date_max"],
        },
        "labels": LABELS,
        "checks": checks,
        "inputs": {
            "script": _hash_record(
                "qc_pep_validation.py",
                script_path,
                "workflows/national/bucking/qc_pep_validation.py",
            ),
            "taper_registry": _hash_record(
                "qc_schneider2013_taper.csv",
                TAPER_REGISTRY_PATH,
                "workflows/national/bucking/rules/qc_schneider2013_taper.csv",
            ),
            "sort_rules": _hash_record(
                "qc_pilot_sorts.csv",
                SORT_RULES_PATH,
                "workflows/national/bucking/rules/qc_pilot_sorts.csv",
            ),
            "acquisition_record": _hash_record(
                "ACQUISITION.md",
                ACQUISITION_RECORD_PATH,
                "data/raw/qc_pep/ACQUISITION.md",
            ),
            "provenance_record": _hash_record(
                "PROVENANCE.csv",
                PROVENANCE_RECORD_PATH,
                "data/raw/qc_pep/PROVENANCE.csv",
            ),
        },
        "artifacts": artifacts,
        "artifact_count": len(artifacts),
        "environment": {
            "python": sys.version.split()[0],
            "pandas": pd.__version__,
            "numpy": np.__version__,
        },
    }


def _resolve_source(
    args: argparse.Namespace, deadline: Deadline
) -> tuple[Path, dict[str, Any]]:
    work_dir = Path(args.work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    zip_path: Path | None = None
    retrieved_utc = args.retrieved_utc
    zip_source = "local"

    if args.gpkg is not None:
        gpkg_path = Path(args.gpkg)
        if not gpkg_path.exists():
            raise QCValidationError(f"GPKG not found: {gpkg_path}")
    else:
        if args.zip is not None:
            zip_path = Path(args.zip)
            if not zip_path.exists():
                raise QCValidationError(f"ZIP not found: {zip_path}")
            zip_source = "local"
        elif args.download:
            zip_path = work_dir / "PEP_GPKG.zip"
            retrieved_utc = _download_zip(args.source_url, zip_path, deadline)
            zip_source = "download"
        elif DEFAULT_ZIP_PATH.exists():
            zip_path = DEFAULT_ZIP_PATH
            zip_source = "local"
        else:
            zip_path = work_dir / "PEP_GPKG.zip"
            retrieved_utc = _download_zip(args.source_url, zip_path, deadline)
            zip_source = "download"

        gpkg_path = work_dir / ZIP_MEMBER
        if not gpkg_path.exists():
            _extract_gpkg(zip_path, ZIP_MEMBER, gpkg_path, deadline)

    zip_bytes = int(zip_path.stat().st_size) if zip_path is not None else 0
    zip_sha = _sha256(zip_path) if zip_path is not None else ""
    gpkg_bytes = int(gpkg_path.stat().st_size)
    gpkg_sha = _sha256(gpkg_path)
    if zip_sha:
        zip_matches = zip_sha == EXPECTED_ZIP_SHA256
        if not zip_matches and not args.allow_source_change:
            raise QCValidationError(
                "source ZIP hash does not match the recorded acquisition hash: "
                f"{zip_sha} != {EXPECTED_ZIP_SHA256}. Use --allow-source-change "
                "explicitly if the published dataset has changed."
            )
    else:
        zip_matches = False
    gpkg_matches = gpkg_sha == EXPECTED_GPKG_SHA256
    if zip_matches and not gpkg_matches:
        raise QCValidationError(
            "ZIP matches the acquisition record but the extracted GPKG hash does "
            f"not: {gpkg_sha} != {EXPECTED_GPKG_SHA256}"
        )
    if retrieved_utc is None:
        retrieved_utc = datetime.fromtimestamp(
            (zip_path or gpkg_path).stat().st_mtime, tz=UTC
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
    def _logical_path(path: Path) -> str:
        try:
            return str(path.resolve().relative_to(REPO_ROOT))
        except ValueError:
            return path.name

    source = {
        "dataset_id": SOURCE_DATASET_ID,
        "dataset_url": SOURCE_DATASET_URL,
        "source_url": args.source_url,
        "publisher": SOURCE_PUBLISHER,
        "licence": SOURCE_LICENCE,
        "retrieved_utc": retrieved_utc,
        "zip_source": zip_source,
        "zip_path": _logical_path(zip_path) if zip_path is not None else "",
        "zip_bytes": zip_bytes,
        "zip_sha256": zip_sha,
        "zip_hash_matches_record": bool(zip_matches),
        "gpkg_member": ZIP_MEMBER,
        "gpkg_path": _logical_path(gpkg_path),
        "gpkg_bytes": gpkg_bytes,
        "gpkg_sha256": gpkg_sha,
        "gpkg_hash_matches_record": bool(gpkg_matches),
        "expected_zip_sha256": EXPECTED_ZIP_SHA256,
        "expected_gpkg_sha256": EXPECTED_GPKG_SHA256,
        "acquisition_record": "data/raw/qc_pep/ACQUISITION.md",
        "provenance_record": "data/raw/qc_pep/PROVENANCE.csv",
    }
    return gpkg_path, source


def run(args: argparse.Namespace) -> dict[str, Any]:
    started = time.monotonic()
    deadline = Deadline(args.timebox_seconds)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    assert_no_unexpected_deletions(REPO_ROOT)
    deadline.check("source resolution")
    gpkg_path, source = _resolve_source(args, deadline)
    zip_ok = not source["zip_sha256"] or source["zip_hash_matches_record"]
    if not (zip_ok and source["gpkg_hash_matches_record"]):
        raise QCValidationError(
            "source hashes do not match the recorded acquisition; refusing to "
            "label the result as the v0 PEP validation"
        )

    connection = _connect_readonly(gpkg_path)
    try:
        schema = _inspect_schema(connection, deadline)
        registry = load_taper_registry(TAPER_REGISTRY_PATH)
        rules = load_national_sort_rules(SORT_RULES_PATH)
        if not rules:
            raise QCValidationError("no Quebec pilot sort rules loaded")
        frame, selection = select_sample(
            connection,
            max_plots=int(args.max_plots),
            max_trees=int(args.max_trees),
            deadline=deadline,
        )
    finally:
        connection.close()

    tapers = _resolve_tapers(frame, registry)
    trees = _build_pilot_trees(frame, tapers)
    tree_frame, log_frame, residue_frame = _buck_population(
        frame, trees, tapers, rules, deadline
    )

    plot_summary = _plot_summary(tree_frame)
    species_summary = _species_summary(tree_frame, int(selection["selected_plots"]))
    comparison = _comparison(
        tree_frame,
        int(selection["selected_plots"]),
        {
            "vmb_tige_non_null": schema["counts"]["vmb_tige_non_null"],
            "vmb_ha_non_null": schema["counts"]["vmb_ha_non_null"],
        },
    )
    product_summary = _product_summary(log_frame)
    totals = _totals(tree_frame, log_frame, int(selection["selected_plots"]))

    summary: dict[str, Any] = {
        "validation": VALIDATION_NAME,
        "generated_utc": _utc_now(),
        "source": source,
        "schema": schema,
        "selection": selection,
        "sample": {
            "n_plots": int(selection["selected_plots"]),
            "n_visits": int(selection["selected_visits"]),
            "n_trees": len(tree_frame),
            "n_stems": int(tree_frame["stem_id"].nunique()),
            "year_min": int(tree_frame["measurement_year"].min()),
            "year_max": int(tree_frame["measurement_year"].max()),
            "species": sorted(tree_frame["species"].unique().tolist()),
            "species_counts": {
                str(key): int(value)
                for key, value in tree_frame["species"].value_counts().sort_index().items()
            },
            "mean_dbh_cm": float(tree_frame["dbh_cm"].mean()),
            "mean_height_m": float(tree_frame["height_m"].mean()),
            "dbh_cm_min": float(tree_frame["dbh_cm"].min()),
            "dbh_cm_max": float(tree_frame["dbh_cm"].max()),
            "height_m_min": float(tree_frame["height_m"].min()),
            "height_m_max": float(tree_frame["height_m"].max()),
        },
        "totals": totals,
        "products": product_summary.to_dict("records") if not product_summary.empty else [],
        "comparison": {
            "pep_compiled_volume_fields": ["vmb_tige", "vmb_ha"],
            "pep_compiled_volume_non_null_full": int(schema["counts"]["vmb_tige_non_null"]),
            "pep_compiled_volume_non_null_full_ha": int(schema["counts"]["vmb_ha_non_null"]),
            "pep_compiled_volume_non_null_selected": int(
                tree_frame["pep_compiled_volume_available"].sum()
            ),
            "pep_dic_non_null_selected": int(tree_frame["pep_dic_present"].sum()),
            "pep_hauteurdic_non_null_selected": int(
                tree_frame["pep_hauteurdic_present"].sum()
            ),
            "pep_biomass_fields": [],
            "status": "taper_only_no_pep_compiled_volume",
            "note": (
                "PEP vmb_tige/vmb_ha are present but null; no biomass field exists. "
                "Reported taper totals and residue are model estimates, not observed "
                "compiled volume."
            ),
        },
        "labels": LABELS,
    }
    checks = _validate_frames(
        tree_frame,
        log_frame,
        residue_frame,
        plot_summary,
        species_summary,
        comparison,
        summary,
    )
    summary["validation_checks"] = checks

    source_schema_frame = _source_schema_frame(schema)
    _write_csv(tree_frame, output_dir / "tree_list.csv")
    _write_csv(log_frame, output_dir / "bucked_logs.csv")
    _write_csv(residue_frame, output_dir / "residue.csv")
    _write_csv(plot_summary, output_dir / "plot_summary.csv")
    _write_csv(species_summary, output_dir / "species_summary.csv")
    _write_csv(comparison, output_dir / "comparison.csv")
    _write_csv(source_schema_frame, output_dir / "source_schema.csv")

    report = _report_markdown(
        source,
        schema,
        selection,
        totals,
        species_summary,
        comparison,
        product_summary,
        checks,
    )
    (output_dir / "report.md").write_text(report, encoding="utf-8")
    summary["artifacts"] = {
        name: _hash_record(name, output_dir / name)
        for name in REQUIRED_ARTIFACTS
        if name != "manifest.json"
    }
    (output_dir / "summary.json").write_text(
        json.dumps(_jsonable(summary), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    # Re-check deletions immediately before the manifest is written.
    assert_no_unexpected_deletions(REPO_ROOT)
    manifest = _manifest(
        output_dir,
        source,
        schema,
        selection,
        checks,
        Path(__file__),
        time.monotonic() - started,
    )
    (output_dir / "manifest.json").write_text(
        json.dumps(_jsonable(manifest), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    for record in manifest["artifacts"]:
        artifact_path = output_dir / str(record["label"])
        if not artifact_path.exists() or _sha256(artifact_path) != record["sha256"]:
            raise QCValidationError(
                f"manifest hash mismatch for {record['label']} in {output_dir}"
            )
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the Quebec PEP measured tree list with the Schneider (2013) "
            "taper and the Quebec pilot dimension-only bucking rules."
        )
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--work-dir", type=Path, default=DEFAULT_WORK_DIR)
    parser.add_argument(
        "--zip",
        type=Path,
        default=None,
        help="Local PEP_GPKG.zip. Defaults to the archived data/raw copy when present.",
    )
    parser.add_argument(
        "--gpkg",
        type=Path,
        default=None,
        help="Pre-extracted PEP.gpkg; skips ZIP handling.",
    )
    parser.add_argument(
        "--download",
        action="store_true",
        help="Download the source ZIP even when a local copy exists.",
    )
    parser.add_argument("--source-url", default=SOURCE_URL)
    parser.add_argument(
        "--retrieved-utc",
        default=None,
        help="Override retrieval timestamp, e.g. 2026-11-23T00:03:12Z.",
    )
    parser.add_argument("--max-plots", type=int, default=DEFAULT_MAX_PLOTS)
    parser.add_argument("--max-trees", type=int, default=DEFAULT_MAX_TREES)
    parser.add_argument(
        "--timebox-seconds", type=float, default=DEFAULT_TIMEBOX_SECONDS
    )
    parser.add_argument(
        "--allow-source-change",
        action="store_true",
        help="Allow a ZIP hash different from the recorded acquisition hash.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        summary = run(args)
    except (QCValidationError, TimeboxExceeded) as error:
        parser.exit(2, f"qc_pep_validation: {error}\n")
    totals = summary["totals"]
    selection = summary["selection"]
    print(
        f"{VALIDATION_NAME}: {selection['selected_trees']:,} trees / "
        f"{selection['selected_plots']:,} plots / {totals['n_logs']:,} logs; "
        f"taper {totals['taper_total_m3']:,.2f} m3, residue "
        f"{totals['residue_m3']:,.2f} m3; checks passed"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
