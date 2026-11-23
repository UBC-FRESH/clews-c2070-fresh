"""National bucking pilot v2: measured BC tree list and 12 synthetic lists.

Scope
-----
This is a **pilot**, not inventory and not a national supply estimate.  It
replaces the ten synthetic British Columbia trees in pilot v1 with a
post-stratified list of measured FAIB PSP TSA24 stems.  The other twelve
jurisdictions keep the v1 representative synthetic tree lists and donor/local
taper dispatch unchanged.

BC empirical tree list
----------------------
The source population is ``bc_psp_validation_v0/selected_trees.csv``: the
latest live visit per TSA24 PSP site, full measured tree list, including the
FAIB ``PHF_TREE`` expansion factor.  Trees that v0 could not buck (unsupported
taper species or a reference-bucker balance failure) are excluded and counted.

The list is aggregated, not invented.  Within each
``species x BEC zone x 2.5 cm DBH`` stratum, one measured stem at the
PHF-weighted median of the stratum is retained.  Its expansion factor is the
sum of ``PHF_TREE`` over the stratum, so the selected list preserves the
source expansion-factor total exactly.  DBH and height are the measured values
of the retained stem; no geometry is synthesised.  The selection is recorded
in ``bc_tree_list_selection.csv``.

Bucking
-------
* BC measured stems are bucked twice:
  - with the BC interior TIPSY 4.5 dimension sorts (the BC validation rule
    set), written as ``bc_tipsy_*``; this is the BC dimension-sort result.
  - with the national pilot dimension sort set inside the 13-jurisdiction
    pilot, so v1 and v2 national product shares are comparable.
* The other twelve jurisdictions use the v1 local/donor taper dispatch and the
  national pilot sort set.

``grade=not_assigned`` and ``sort_basis=dimension_only`` everywhere.  The
results are modelled log products, not observed mill receipts and not an
official federal or provincial grade.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
import time
import warnings
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from nemora.workflows.taper import (
    KOZAK_SOURCE,
    KOZAK_SPECIES,
    load_kozak_parameters,
    params_for,
)

from .bucking import (
    buck_tree as buck_bc_tree,
)
from .bucking import (
    load_grade_rules as load_bc_grade_rules,
)
from .bucking import (
    load_price_profile as load_bc_price_profile,
)
from .national_bucking_pilot_v1 import (
    BASIC_DENSITY_ODT_M3,
    BEC_ANCHOR,
    CHIP_YIELD_FRACTION,
    DECIDUOUS_CODES,
    DONOR_POLICY,
    FACILITY_CHAIN_PATH,
    HOG_YIELD_FRACTION,
    MOISTURE_WET_BASIS,
    N_TREES_PER_PROVINCE,
    NFI_COVERAGE_PATH,
    NFI_HEIGHT_PATH,
    PRICE_DECK_ID,
    PROVINCES,
    QC_TAPER_PATH,
    SORT_RULES_PATH,
    STUMP_HEIGHT_M,
    PilotSortRule,
    PilotTaper,
    ProvinceComposition,
    assert_no_unexpected_deletions,
    buck_tree,
    build_representative_tree_lists,
    load_national_sort_rules,
)
from .pilot_schema import (
    EVIDENCE_TIERS,
    PilotBuckedLog,
    PilotResidueLedger,
    PilotTree,
    SchemaError,
    weakest_evidence,
)
from .schema import TreeRecord
from .schneider2013 import SchneiderTaperRegistry, load_taper_registry
from .taper import (
    TaperRegistry,
    default_kozak_parameter_path,
    default_nemora_root,
)
from .validate_tsa24_btc import (
    DEFAULT_CHIP_YIELD,
    DEFAULT_MOISTURE_GREEN,
    load_basic_density_table,
)

__all__ = [
    "BC_DBH_BREAKS_CM",
    "BC_SELECTED_TREES_PATH",
    "DEFAULT_OUTPUT_DIR",
    "PROVINCES",
    "assert_no_unexpected_deletions",
    "build_bc_empirical_tree_list",
    "generate_pilot",
    "select_bc_stratum_representatives",
]

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT_DIR = (
    REPO_ROOT
    / "data"
    / "derived"
    / "national"
    / "bucking"
    / "national_bucking_pilot_v2"
)
BC_PSP_V0_DIR = (
    REPO_ROOT / "data" / "derived" / "national" / "bucking" / "bc_psp_validation_v0"
)
BC_SELECTED_TREES_PATH = BC_PSP_V0_DIR / "selected_trees.csv"
BC_TREE_LEDGER_PATH = BC_PSP_V0_DIR / "tree_ledger.csv"
BC_VALIDATION_SUMMARY_PATH = BC_PSP_V0_DIR / "validation_summary.json"
BC_RULES_PATH = (
    REPO_ROOT / "workflows" / "national" / "bucking" / "rules" / "bc_interior.yaml"
)
BC_DENSITY_PATH = (
    REPO_ROOT / "workflows" / "national" / "bucking" / "rules" / "bc_basic_density.csv"
)
BC_PRICE_PROFILE_PATH = (
    default_nemora_root() / "config" / "log_prices" / "bc_interior_2026q3.yaml"
)

BC_DBH_BREAKS_CM: tuple[float, ...] = (7.5, 10.0, 12.5, 15.0, 17.5, 20.0, 22.5,
                                      25.0, 27.5, 30.0, 32.5, 35.0, 37.5, 40.0,
                                      42.5, 45.0, 47.5, 50.0)
BC_MEASURED_LABEL = "measured_tree_list"
BC_BASIS = "bc_psp_tsa24_poststratified_measured"
SYNTHETIC_LABEL = "representative_synthetic"
NATIONAL_SORT_SET = "national_pilot_sorts"
TIPSY_SORT_SET = "bc_interior_tipsy_sorts"
CONSERVATION_TOL = 1e-8

_BC_REQUIRED_COLUMNS = frozenset(
    {
        "tree_id",
        "site_id",
        "plot_id",
        "species",
        "bec_zone",
        "dbh_cm",
        "height_m",
        "height_source",
        "height_evidence",
        "expansion_factor",
        "observed_merchantable_m3",
        "observed_whole_stem_m3",
        "bucked_status",
        "bec_subzone",
    }
)


# ---------------------------------------------------------------------------
# Strict helpers
# ---------------------------------------------------------------------------


def _finite(name: str, value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise SchemaError(f"{name} must be numeric, got {value!r}") from exc
    if not math.isfinite(number):
        raise SchemaError(f"{name} must be finite, got {value!r}")
    return number


def _positive(name: str, value: Any) -> float:
    number = _finite(name, value)
    if number <= 0.0:
        raise SchemaError(f"{name} must be positive, got {value!r}")
    return number


def _non_negative(name: str, value: Any) -> float:
    number = _finite(name, value)
    if number < 0.0:
        raise SchemaError(f"{name} must be non-negative, got {value!r}")
    return number


def _safe_div(numerator: float, denominator: float) -> float:
    if denominator <= 0.0:
        return 0.0
    value = numerator / denominator
    return value if math.isfinite(value) else 0.0


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative_to_repo(path: Path) -> str:
    try:
        return Path(path).resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(path)


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
        return []
    return [line for line in result.stdout.splitlines() if line.strip()]


def _write_csv(path: Path, rows: Iterable[Mapping[str, Any]], columns: list[str]) -> int:
    import csv

    materialized = [dict(row) for row in rows]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in materialized:
            writer.writerow(row)
    return len(materialized)


def _text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def _dbh_stratum(dbh_cm: float) -> str:
    """Return the 2.5 cm DBH stratum label for a measured stem."""
    dbh = _positive("dbh_cm", dbh_cm)
    if dbh < BC_DBH_BREAKS_CM[0]:
        return f"0-{BC_DBH_BREAKS_CM[0]:g}"
    upper_breaks = BC_DBH_BREAKS_CM[1:]
    for lower, upper in zip(BC_DBH_BREAKS_CM, upper_breaks, strict=False):
        if dbh < upper:
            return f"{lower:g}-{upper:g}"
    return f"{BC_DBH_BREAKS_CM[-1]:g}+"


def _bc_taper(
    species: str,
    bec_zone: str,
    kozak_parameters: Mapping[tuple[str, str], Any],
) -> PilotTaper:
    parameters = params_for(kozak_parameters, species, bec_zone)
    _kozak_code, proxy = KOZAK_SPECIES.get(species, (species, True))
    return PilotTaper(
        taper_id=f"BC-KOZAK2004-{species}-{bec_zone}",
        jurisdiction="BC",
        species=species,
        source_jurisdiction="BC",
        bec_zone=bec_zone,
        taper_status="local_published",
        proxy=proxy,
        evidence="published_table",
        source=KOZAK_SOURCE,
        model=_KozakHolder(parameters),
    )


class _KozakHolder:
    """Minimal model holder matching the v1 PilotTaper adapter contract."""

    __slots__ = ("parameters",)

    def __init__(self, parameters: Any) -> None:
        self.parameters = parameters


# ---------------------------------------------------------------------------
# BC empirical tree-list selection
# ---------------------------------------------------------------------------


def _load_bc_population(path: Path = BC_SELECTED_TREES_PATH) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"BC PSP selected tree list not found: {path}")
    frame = pd.read_csv(
        path,
        usecols=lambda name: name in _BC_REQUIRED_COLUMNS,
        dtype={
            "tree_id": str,
            "site_id": str,
            "plot_id": str,
            "species": str,
            "bec_zone": str,
            "bec_subzone": str,
            "height_source": str,
            "height_evidence": str,
            "bucked_status": str,
        },
        low_memory=False,
    )
    missing = _BC_REQUIRED_COLUMNS.difference(frame.columns)
    if missing:
        raise SchemaError(
            "BC PSP selected tree list is missing required columns: "
            + ", ".join(sorted(missing))
        )
    if frame.empty:
        raise SchemaError(f"BC PSP selected tree list is empty: {path}")

    frame = frame.copy()
    for column in (
        "dbh_cm",
        "height_m",
        "expansion_factor",
        "observed_merchantable_m3",
        "observed_whole_stem_m3",
    ):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["species"] = frame["species"].astype(str).str.strip().str.upper()
    frame["bec_zone"] = frame["bec_zone"].astype(str).str.strip().str.upper()
    frame["bucked_status"] = frame["bucked_status"].astype(str).str.strip()
    frame = frame.rename(columns={"expansion_factor": "source_expansion_factor"})
    invalid = (
        frame["dbh_cm"].isna()
        | frame["height_m"].isna()
        | frame["source_expansion_factor"].isna()
        | (frame["dbh_cm"] < 2.0)
        | (frame["height_m"] <= 1.3)
        | (frame["source_expansion_factor"] <= 0.0)
        | (frame["species"] == "")
        | (frame["bec_zone"] == "")
    )
    if invalid.any():
        sample = frame.loc[invalid, ["tree_id", "species", "dbh_cm", "height_m"]]
        raise SchemaError(
            "BC PSP selected tree list contains invalid geometry or expansion rows: "
            + sample.head(5).to_dict(orient="records").__repr__()
        )
    return frame.sort_values("tree_id", kind="mergesort").reset_index(drop=True)


def select_bc_stratum_representatives(frame: pd.DataFrame) -> pd.DataFrame:
    """Select one measured PHF-weighted median stem per species/BEC/DBH stratum.

    The input frame must contain ``species``, ``bec_zone``, ``dbh_cm``,
    ``height_m``, ``tree_id`` and ``source_expansion_factor``.  The output is
    the measured aggregate list with ``expansion_factor`` set to the stratum
    PHF total.  The function is pure: it does not mutate the input.
    """
    required = {
        "species",
        "bec_zone",
        "dbh_cm",
        "height_m",
        "tree_id",
        "source_expansion_factor",
    }
    missing = required.difference(frame.columns)
    if missing:
        raise SchemaError(
            "selection frame is missing columns: " + ", ".join(sorted(missing))
        )
    if frame.empty:
        raise SchemaError("selection frame is empty")

    working = frame.copy()
    working["dbh_stratum"] = working["dbh_cm"].map(_dbh_stratum)
    selected: list[dict[str, Any]] = []
    for (species, bec_zone, stratum), group in working.groupby(
        ["species", "bec_zone", "dbh_stratum"], sort=True, observed=True
    ):
        ordered = group.sort_values(
            ["dbh_cm", "height_m", "tree_id"], kind="mergesort"
        ).reset_index(drop=True)
        weights = ordered["source_expansion_factor"].to_numpy(dtype=float)
        total_phf = float(weights.sum())
        if not math.isfinite(total_phf) or total_phf <= 0.0:
            raise SchemaError(
                f"stratum {species}/{bec_zone}/{stratum} has invalid PHF total"
            )
        cumulative = np.cumsum(weights)
        midpoints = cumulative - weights / 2.0
        index = int(np.argmin(np.abs(midpoints - total_phf / 2.0)))
        record = ordered.iloc[index].to_dict()
        record.update(
            {
                "stratum_id": f"{species}|{bec_zone}|{stratum}",
                "dbh_stratum": stratum,
                "stratum_n_trees": len(ordered),
                "stratum_expansion_factor": total_phf,
                "expansion_factor": total_phf,
                "selection_method": (
                    "phf_weighted_median_within_species_bec_dbh_stratum"
                ),
            }
        )
        selected.append(record)
    result = pd.DataFrame(selected)
    if result["tree_id"].duplicated().any():
        raise SchemaError("BC empirical selection retained a duplicate tree_id")
    source_phf = float(frame["source_expansion_factor"].sum())
    selected_phf = float(result["expansion_factor"].sum())
    if not math.isclose(selected_phf, source_phf, rel_tol=1e-12, abs_tol=1e-6):
        raise SchemaError(
            f"BC empirical selection changes PHF total: {selected_phf} vs {source_phf}"
        )
    return result.sort_values(["species", "bec_zone", "dbh_stratum"], kind="mergesort") \
        .reset_index(drop=True)


def build_bc_empirical_tree_list(
    qc_registry: SchneiderTaperRegistry,
    kozak_parameters: Mapping[tuple[str, str], Any],
) -> tuple[
    tuple[PilotTree, ...],
    dict[str, PilotTaper],
    dict[str, dict[str, Any]],
    pd.DataFrame,
    dict[str, Any],
]:
    """Build the measured BC aggregate tree list and its selection audit."""
    del qc_registry  # QC is irrelevant to BC; retained for a symmetric signature.
    population = _load_bc_population()
    excluded_status = population[population["bucked_status"] != "bucked"].copy()
    bucked = population[population["bucked_status"] == "bucked"].copy()
    if bucked.empty:
        raise SchemaError("no bucked BC PSP trees are available for v2")

    registry = TaperRegistry(parameter_path=default_kozak_parameter_path())
    supported = set(registry.supported_species)
    unsupported = bucked[~bucked["species"].isin(supported)].copy()
    if not unsupported.empty:
        raise SchemaError(
            "v0 marked trees as bucked but their species is not taper-supported: "
            + ", ".join(sorted(unsupported["species"].unique()))
        )

    selected = select_bc_stratum_representatives(bucked)
    tapers: dict[str, PilotTaper] = {}
    trees: list[PilotTree] = []
    extras: dict[str, dict[str, Any]] = {}
    audit_rows: list[dict[str, Any]] = []

    for row in selected.itertuples(index=False):
        source_tree_id = _text(row.tree_id)
        species = _text(row.species).upper()
        bec_zone = _text(row.bec_zone).upper()
        taper = _bc_taper(species, bec_zone, kozak_parameters)
        height_source = _text(row.height_source) or "unknown"
        height_evidence = _text(row.height_evidence) or "unknown"
        expansion = _positive("expansion_factor", row.expansion_factor)
        provenance = (
            "bc_psp_tsa24_measured: FAIB PSP latest live visit; "
            f"source_tree_id={source_tree_id}; site={_text(row.site_id)}; "
            f"plot={_text(row.plot_id)}; stratum={_text(row.stratum_id)} "
            f"(n={int(row.stratum_n_trees)}, PHF={float(row.stratum_expansion_factor):.6f}); "
            f"DBH={float(row.dbh_cm):.1f} cm field measured; "
            f"height={float(row.height_m):.1f} m ({height_source}); "
            f"pooled expansion={expansion:.6f} trees/ha per selected stem from "
            "FAIB PHF_TREE; PHF-weighted median within stratum; no synthetic geometry"
        )
        tree = PilotTree(
            tree_id=source_tree_id,
            site_id=_text(row.site_id),
            jurisdiction="BC",
            species=species,
            species_group=(
                "deciduous" if species in DECIDUOUS_CODES else "conifer"
            ),
            dbh_cm=float(row.dbh_cm),
            height_m=float(row.height_m),
            defect_pct=0.0,
            expansion_factor=expansion,
            status="live",
            provenance=provenance,
            evidence="published_table",
            taper_id=taper.taper_id,
        )
        trees.append(tree)
        tapers[tree.tree_id] = taper
        extras[tree.tree_id] = {
            "tree_list_label": BC_MEASURED_LABEL,
            "tree_list_basis": BC_BASIS,
            "tree_list_source": (
                "bc_psp_validation_v0/selected_trees.csv; FAIB PSP TSA24 "
                "latest live visit per SITE_IDENTIFIER"
            ),
            "source_tree_id": source_tree_id,
            "source_site_id": _text(row.site_id),
            "source_plot_id": _text(row.plot_id),
            "source_expansion_factor": float(row.source_expansion_factor),
            "expansion_factor": expansion,
            "expansion_factor_basis": "pooled_stratum_phf",
            "stratum_id": _text(row.stratum_id),
            "dbh_stratum": _text(row.dbh_stratum),
            "stratum_n_trees": int(row.stratum_n_trees),
            "stratum_expansion_factor": float(row.stratum_expansion_factor),
            "selection_method": _text(row.selection_method),
            "dbh_basis": "field_measured",
            "height_basis": height_source,
            "height_evidence": height_evidence,
            "bec_subzone": _text(row.bec_subzone) or "not_applicable",
            "observed_merchantable_m3": float(row.observed_merchantable_m3),
            "observed_whole_stem_m3": float(row.observed_whole_stem_m3),
            "observed_basis": "bc_psp_compiled_volume_bank",
            "species_nfi": species,
            "taper_status": taper.taper_status,
            "taper_jurisdiction": taper.source_jurisdiction,
            "taper_bec_zone": taper.bec_zone,
            "taper_proxy": taper.proxy,
            "measured_tree_list": True,
            "model_estimate": False,
            "synthetic": False,
            "observed": False,
            "policy_grade": False,
            "national_claim": False,
        }
        audit_rows.append(
            {
                "tree_id": source_tree_id,
                "site_id": _text(row.site_id),
                "plot_id": _text(row.plot_id),
                "species": species,
                "bec_zone": bec_zone,
                "bec_subzone": _text(row.bec_subzone),
                "dbh_stratum": _text(row.dbh_stratum),
                "stratum_id": _text(row.stratum_id),
                "stratum_n_trees": int(row.stratum_n_trees),
                "stratum_expansion_factor": float(row.stratum_expansion_factor),
                "source_expansion_factor": float(row.source_expansion_factor),
                "pooled_expansion_factor": expansion,
                "dbh_cm": float(row.dbh_cm),
                "height_m": float(row.height_m),
                "height_source": height_source,
                "height_evidence": height_evidence,
                "selection_method": _text(row.selection_method),
                "taper_id": taper.taper_id,
                "taper_bec_zone": taper.bec_zone,
                "taper_proxy": taper.proxy,
            }
        )

    if not trees:
        raise SchemaError("BC empirical tree list is empty")
    source_phf = float(bucked["source_expansion_factor"].sum())
    selected_phf = sum(tree.expansion_factor for tree in trees)
    if not math.isclose(selected_phf, source_phf, rel_tol=1e-12, abs_tol=1e-6):
        raise SchemaError("BC empirical list does not preserve the source PHF total")
    stats = {
        "source_rows": len(population),
        "source_bucked_rows": len(bucked),
        "excluded_not_bucked_rows": len(excluded_status),
        "excluded_unsupported_rows": len(unsupported),
        "excluded_status_counts": (
            excluded_status["bucked_status"].value_counts().to_dict()
        ),
        "source_phf_total": source_phf,
        "selected_phf_total": selected_phf,
        "selected_trees": len(trees),
        "strata": int(selected["stratum_id"].nunique()),
        "species": (
            bucked.groupby("species").size().sort_values(ascending=False).to_dict()
        ),
        "selected_species": (
            selected.groupby("species").size().sort_values(ascending=False).to_dict()
        ),
        "bec_zones": sorted(bucked["bec_zone"].unique().tolist()),
        "height_evidence": (
            bucked["height_evidence"].value_counts().to_dict()
        ),
        "selection_method": (
            "one measured PHF-weighted median stem per species x BEC zone x "
            "2.5 cm DBH stratum; expansion_factor = stratum PHF total"
        ),
        "source": str(BC_SELECTED_TREES_PATH.relative_to(REPO_ROOT)),
    }
    return tuple(trees), tapers, extras, pd.DataFrame(audit_rows), stats


# ---------------------------------------------------------------------------
# BC TIPSY bucking
# ---------------------------------------------------------------------------


def _bc_density_map() -> dict[str, float]:
    table = load_basic_density_table(BC_DENSITY_PATH)
    densities = {
        str(code).upper(): float(entry["basic_density_odt_m3"])
        for code, entry in table.items()
    }
    # Mountain hemlock (HM) is present in the TSA24 PSP list but is not a
    # separate row in the FVS-BC density table.  Use the published western
    # hemlock species default rather than silently falling back to 0.45.
    densities.setdefault("HM", densities["HW"])
    return densities


def _tree_record_from_pilot(
    tree: PilotTree, extra: Mapping[str, Any]
) -> TreeRecord:
    return TreeRecord(
        tree_id=tree.tree_id,
        site_id=tree.site_id,
        species=tree.species,
        dbh_cm=tree.dbh_cm,
        height_m=tree.height_m,
        status=tree.status,
        expansion_factor=tree.expansion_factor,
        bec_zone=str(extra["taper_bec_zone"]),
        age=None,
        provenance=tree.provenance,
        plot_id=str(extra["source_plot_id"]),
        species_group=tree.species_group,
        bec_subzone=str(extra.get("bec_subzone") or "") or None,
        observed_merchantable_m3=float(extra["observed_merchantable_m3"]),
        observed_whole_stem_m3=float(extra["observed_whole_stem_m3"]),
    )


def buck_bc_tipsy(
    trees: Sequence[PilotTree],
    extras: Mapping[str, Mapping[str, Any]],
    *,
    rules_path: Path = BC_RULES_PATH,
) -> dict[str, Any]:
    """Rerun the BC measured list through the BC interior TIPSY sort set."""
    registry = TaperRegistry(parameter_path=default_kozak_parameter_path())
    rules = load_bc_grade_rules(Path(rules_path))
    prices = load_bc_price_profile(BC_PRICE_PROFILE_PATH)
    densities = _bc_density_map()
    logs: list[dict[str, Any]] = []
    residues: list[dict[str, Any]] = []
    reconciliations: list[dict[str, Any]] = []
    warnings_seen: set[str] = set()

    for tree in trees:
        extra = extras[tree.tree_id]
        model = registry.resolve("BC", tree.species, str(extra["taper_bec_zone"]))
        density = densities.get(tree.species)
        if density is None:
            raise SchemaError(f"no BC basic density for species {tree.species}")
        record = _tree_record_from_pilot(tree, extra)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            try:
                tree_logs, residue = buck_bc_tree(
                    record,
                    model,
                    rules,
                    prices,
                    basic_density_odt_m3=density,
                    moisture_wet_basis=DEFAULT_MOISTURE_GREEN,
                    chip_yield_fraction=DEFAULT_CHIP_YIELD,
                )
            except (ValueError, RuntimeError) as exc:
                raise SchemaError(
                    f"BC TIPSY bucking failed for measured tree {tree.tree_id}: {exc}"
                ) from exc
        warnings_seen.update(
            f"{warning.category.__name__}: {warning.message}" for warning in caught
        )
        weight = tree.expansion_factor
        product_totals: dict[str, float] = {}
        for log in tree_logs:
            row = log.to_dict()
            row.update(
                {
                    "jurisdiction": "BC",
                    "tree_list_label": BC_MEASURED_LABEL,
                    "measured_tree_list": True,
                    "synthetic": False,
                    "model_estimate": True,
                    "observed": False,
                    "policy_grade": False,
                    "national_claim": False,
                    "grade": "not_assigned",
                    "sort_rule_set": TIPSY_SORT_SET,
                    "expansion_factor": weight,
                    "volume_m3_expanded": log.volume_m3 * weight,
                    "value_cad_expanded": (
                        log.value_cad * weight if log.value_cad is not None else 0.0
                    ),
                }
            )
            logs.append(row)
            product_totals[log.product] = (
                product_totals.get(log.product, 0.0) + log.volume_m3
            )
        residue_row = residue.to_dict()
        residue_row.update(
            {
                "jurisdiction": "BC",
                "tree_list_label": BC_MEASURED_LABEL,
                "measured_tree_list": True,
                "synthetic": False,
                "model_estimate": True,
                "observed": False,
                "policy_grade": False,
                "national_claim": False,
                "grade": "not_assigned",
                "sort_rule_set": TIPSY_SORT_SET,
                "expansion_factor": weight,
                "residue_volume_m3_expanded": residue.residue_volume_m3 * weight,
                "residue_odt_expanded": residue.residue_odt * weight,
                "green_tonnes_expanded": residue.green_tonnes * weight,
                "chip_green_tonnes_expanded": residue.chip_green_tonnes * weight,
                "recovered_log_m3_expanded": (
                    residue.recovered_log_m3 or 0.0
                )
                * weight,
                "taper_total_m3_expanded": (residue.taper_total_m3 or 0.0) * weight,
                "species": tree.species,
                "dbh_cm": tree.dbh_cm,
                "height_m": tree.height_m,
            }
        )
        residues.append(residue_row)
        reconciliations.append(
            {
                "tree_id": tree.tree_id,
                "site_id": tree.site_id,
                "plot_id": extra["source_plot_id"],
                "species": tree.species,
                "bec_zone": extra["taper_bec_zone"],
                "dbh_cm": tree.dbh_cm,
                "height_m": tree.height_m,
                "expansion_factor": weight,
                "n_logs": len(tree_logs),
                "taper_total_m3": float(residue.taper_total_m3 or 0.0),
                "recovered_log_m3": float(residue.recovered_log_m3 or 0.0),
                "residue_m3": residue.residue_volume_m3,
                "residue_odt": residue.residue_odt,
                "sawlog_m3": product_totals.get("sawlog", 0.0),
                "pulpwood_m3": product_totals.get("pulpwood", 0.0),
                "other_m3": product_totals.get("other", 0.0),
                "balance_error_m3": abs(
                    float(residue.recovered_log_m3 or 0.0)
                    + residue.residue_volume_m3
                    - float(residue.taper_total_m3 or 0.0)
                ),
                "taper_id": f"BC-KOZAK2004-{tree.species}-{model.bec_zone}",
                "taper_bec_zone": model.bec_zone,
                "tree_list_label": BC_MEASURED_LABEL,
                "measured_tree_list": True,
                "synthetic": False,
                "model_estimate": True,
                "observed": False,
                "policy_grade": False,
                "national_claim": False,
            }
        )

    logs_frame = pd.DataFrame(logs)
    residue_frame = pd.DataFrame(residues)
    reconciliation_frame = pd.DataFrame(reconciliations)
    if logs_frame.empty:
        raise SchemaError("BC TIPSY bucking produced no logs")
    for row in reconciliation_frame.itertuples(index=False):
        if abs(
            row.recovered_log_m3 + row.residue_m3 - row.taper_total_m3
        ) > CONSERVATION_TOL:
            raise SchemaError(f"BC TIPSY conservation failed for {row.tree_id}")
    recovered = float(logs_frame["volume_m3_expanded"].sum())
    residue_m3 = float(residue_frame["residue_volume_m3_expanded"].sum())
    residue_odt = float(residue_frame["residue_odt_expanded"].sum())
    product_totals = (
        logs_frame.groupby("product")["volume_m3_expanded"].sum().to_dict()
    )
    solid = float(product_totals.get("sawlog", 0.0) + product_totals.get("other", 0.0))
    summary = {
        "sort_rule_set": TIPSY_SORT_SET,
        "sort_rules_path": str(Path(rules_path)),
        "sort_rules_version": rules.version,
        "taper": "BC Kozak (2004) Version 6, actual measured BEC zone",
        "n_trees": len(trees),
        "n_logs": len(logs_frame),
        "taper_total_m3_expanded": float(
            residue_frame["taper_total_m3_expanded"].sum()
        ),
        "recovered_gross_m3_expanded": recovered,
        "residue_m3_expanded": residue_m3,
        "residue_odt_expanded": residue_odt,
        "green_tonnes_expanded": float(
            residue_frame["green_tonnes_expanded"].sum()
        ),
        "chip_green_tonnes_expanded": float(
            residue_frame["chip_green_tonnes_expanded"].sum()
        ),
        "sawlog_m3_expanded": float(product_totals.get("sawlog", 0.0)),
        "pulpwood_m3_expanded": float(product_totals.get("pulpwood", 0.0)),
        "other_m3_expanded": float(product_totals.get("other", 0.0)),
        "sawlog_share_of_recovered": _safe_div(
            float(product_totals.get("sawlog", 0.0)), recovered
        ),
        "pulpwood_share_of_recovered": _safe_div(
            float(product_totals.get("pulpwood", 0.0)), recovered
        ),
        "other_share_of_recovered": _safe_div(
            float(product_totals.get("other", 0.0)), recovered
        ),
        "solid_share_of_recovered": _safe_div(solid, recovered),
        "residue_m3_per_recovered_m3": _safe_div(residue_m3, recovered),
        "residue_odt_per_recovered_m3": _safe_div(residue_odt, recovered),
        "max_balance_error_m3": float(
            reconciliation_frame["balance_error_m3"].max()
        ),
        "taper_warnings": sorted(warnings_seen),
        "labels": {
            "tree_list_label": BC_MEASURED_LABEL,
            "measured_tree_list": True,
            "synthetic": False,
            "model_estimate": True,
            "observed": False,
            "policy_grade": False,
            "national_claim": False,
        },
    }
    return {
        "logs": logs_frame,
        "residue": residue_frame,
        "reconciliation": reconciliation_frame,
        "rules": [rule.to_dict() for rule in rules.rules],
        "summary": summary,
    }


# ---------------------------------------------------------------------------
# National v2 tree list and bucking
# ---------------------------------------------------------------------------


def build_national_v2_tree_list(
    qc_registry: SchneiderTaperRegistry,
    kozak_parameters: Mapping[tuple[str, str], Any],
    bc_trees: Sequence[PilotTree],
    bc_tapers: Mapping[str, PilotTaper],
    bc_extras: Mapping[str, Mapping[str, Any]],
) -> tuple[
    tuple[PilotTree, ...],
    dict[str, PilotTaper],
    dict[str, dict[str, Any]],
    dict[str, ProvinceComposition],
]:
    """Combine the measured BC list with the v1 lists for the other 12."""
    v1_trees, v1_tapers, v1_extras, compositions = build_representative_tree_lists(
        qc_registry, kozak_parameters
    )
    trees: list[PilotTree] = list(bc_trees)
    tapers: dict[str, PilotTaper] = dict(bc_tapers)
    extras: dict[str, dict[str, Any]] = {
        tree_id: dict(extra) for tree_id, extra in bc_extras.items()
    }
    for tree in v1_trees:
        if tree.jurisdiction == "BC":
            continue
        extra = dict(v1_extras[tree.tree_id])
        extra.update(
            {
                "tree_list_label": SYNTHETIC_LABEL,
                "measured_tree_list": False,
                "expansion_factor": float(tree.expansion_factor),
                "expansion_factor_basis": "unit_representative_weight",
                "source_tree_id": tree.tree_id,
                "source_site_id": tree.site_id,
                "source_plot_id": "not_applicable",
                "source_expansion_factor": float(tree.expansion_factor),
                "stratum_id": "not_applicable",
                "dbh_stratum": "not_applicable",
                "stratum_n_trees": 1,
                "stratum_expansion_factor": float(tree.expansion_factor),
                "selection_method": "not_applicable",
                "height_evidence": "assumption",
                "bec_subzone": "not_applicable",
                "observed_merchantable_m3": 0.0,
                "observed_whole_stem_m3": 0.0,
                "observed_basis": "not_applicable_synthetic",
                "model_estimate": True,
                "synthetic": True,
                "observed": False,
                "policy_grade": False,
                "national_claim": False,
            }
        )
        trees.append(tree)
        tapers[tree.tree_id] = v1_tapers[tree.tree_id]
        extras[tree.tree_id] = extra
    if len({tree.jurisdiction for tree in trees}) != len(PROVINCES):
        raise SchemaError("national v2 list does not contain all 13 jurisdictions")
    if len({tree.tree_id for tree in trees}) != len(trees):
        raise SchemaError("national v2 tree_id values are not unique")
    return tuple(trees), tapers, extras, compositions


def buck_national_v2(
    trees: Sequence[PilotTree],
    tapers: Mapping[str, PilotTaper],
    rules: Sequence[PilotSortRule],
) -> tuple[list[PilotBuckedLog], list[PilotResidueLedger]]:
    logs: list[PilotBuckedLog] = []
    residues: list[PilotResidueLedger] = []
    for tree in trees:
        taper = tapers[tree.tree_id]
        tree_logs, residue = buck_tree(tree, taper, tuple(rules))
        logs.extend(tree_logs)
        residues.append(residue)
    if not logs:
        raise SchemaError("national v2 pilot produced no logs")
    return logs, residues


# ---------------------------------------------------------------------------
# Summaries and comparisons
# ---------------------------------------------------------------------------


def _weight(extras: Mapping[str, Mapping[str, Any]], tree_id: str) -> float:
    return float(extras[tree_id]["expansion_factor"])


def _province_summary_v2(
    trees: Sequence[PilotTree],
    logs: Sequence[PilotBuckedLog],
    residues: Sequence[PilotResidueLedger],
    extras: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    logs_by_province: dict[str, list[PilotBuckedLog]] = {}
    for log in logs:
        logs_by_province.setdefault(log.jurisdiction, []).append(log)
    residues_by_province: dict[str, list[PilotResidueLedger]] = {}
    for residue in residues:
        residues_by_province.setdefault(residue.jurisdiction, []).append(residue)
    rows: list[dict[str, Any]] = []
    for province in PROVINCES:
        province_trees = [tree for tree in trees if tree.jurisdiction == province]
        province_logs = logs_by_province.get(province, [])
        province_residues = residues_by_province.get(province, [])
        products: dict[str, float] = {}
        products_expanded: dict[str, float] = {}
        for log in province_logs:
            weight = _weight(extras, log.tree_id)
            products[log.product] = products.get(log.product, 0.0) + log.volume_m3
            products_expanded[log.product] = (
                products_expanded.get(log.product, 0.0) + log.volume_m3 * weight
            )
        taper_total = sum(row.taper_total_m3 for row in province_residues)
        recovered = sum(row.recovered_log_m3 for row in province_residues)
        residue_m3 = sum(row.residue_volume_m3 for row in province_residues)
        residue_odt = sum(row.residue_odt for row in province_residues)
        taper_total_expanded = sum(
            row.taper_total_m3 * _weight(extras, row.tree_id)
            for row in province_residues
        )
        recovered_expanded = sum(
            row.recovered_log_m3 * _weight(extras, row.tree_id)
            for row in province_residues
        )
        residue_m3_expanded = sum(
            row.residue_volume_m3 * _weight(extras, row.tree_id)
            for row in province_residues
        )
        residue_odt_expanded = sum(
            row.residue_odt * _weight(extras, row.tree_id)
            for row in province_residues
        )
        local_count = sum(
            1
            for tree in province_trees
            if extras[tree.tree_id]["taper_status"] == "local_published"
        )
        measured_count = sum(
            1 for tree in province_trees if extras[tree.tree_id]["measured_tree_list"]
        )
        labels = sorted(
            {str(extras[tree.tree_id]["tree_list_label"]) for tree in province_trees}
        )
        rows.append(
            {
                "province": province,
                "province_name": _province_name(province),
                "n_trees": len(province_trees),
                "n_logs": len(province_logs),
                "n_species": len({tree.species for tree in province_trees}),
                "n_measured_trees": measured_count,
                "n_synthetic_trees": len(province_trees) - measured_count,
                "tree_list_label": (
                    BC_MEASURED_LABEL
                    if measured_count == len(province_trees)
                    else SYNTHETIC_LABEL
                ),
                "taper_total_m3": taper_total,
                "recovered_gross_m3": recovered,
                "recovered_net_m3": sum(log.net_volume_m3 for log in province_logs),
                "defect_m3": sum(log.defect_deduction_m3 for log in province_logs),
                "residue_m3": residue_m3,
                "residue_odt": residue_odt,
                "green_tonnes": sum(row.green_tonnes for row in province_residues),
                "chip_green_tonnes": sum(
                    row.chip_green_tonnes for row in province_residues
                ),
                "hog_green_tonnes": sum(
                    row.hog_green_tonnes for row in province_residues
                ),
                "sawlog_m3": products.get("sawlog", 0.0),
                "pulpwood_m3": products.get("pulpwood", 0.0),
                "other_m3": products.get("other", 0.0),
                "value_cad": sum(log.value_cad for log in province_logs),
                "taper_total_m3_expanded": taper_total_expanded,
                "recovered_gross_m3_expanded": recovered_expanded,
                "residue_m3_expanded": residue_m3_expanded,
                "residue_odt_expanded": residue_odt_expanded,
                "sawlog_m3_expanded": products_expanded.get("sawlog", 0.0),
                "pulpwood_m3_expanded": products_expanded.get("pulpwood", 0.0),
                "other_m3_expanded": products_expanded.get("other", 0.0),
                "sawlog_share_of_recovered": _safe_div(
                    products_expanded.get("sawlog", 0.0), recovered_expanded
                ),
                "pulpwood_share_of_recovered": _safe_div(
                    products_expanded.get("pulpwood", 0.0), recovered_expanded
                ),
                "other_share_of_recovered": _safe_div(
                    products_expanded.get("other", 0.0), recovered_expanded
                ),
                "solid_share_of_recovered": _safe_div(
                    products_expanded.get("sawlog", 0.0)
                    + products_expanded.get("other", 0.0),
                    recovered_expanded,
                ),
                "residue_m3_per_recovered_m3": _safe_div(
                    residue_m3_expanded, recovered_expanded
                ),
                "residue_odt_per_recovered_m3": _safe_div(
                    residue_odt_expanded, recovered_expanded
                ),
                "product_residue_ratio_m3_per_odt": _safe_div(
                    recovered_expanded, residue_odt_expanded
                ),
                "sawlog_share_unweighted": _safe_div(
                    products.get("sawlog", 0.0), recovered
                ),
                "pulpwood_share_unweighted": _safe_div(
                    products.get("pulpwood", 0.0), recovered
                ),
                "other_share_unweighted": _safe_div(
                    products.get("other", 0.0), recovered
                ),
                "solid_share_unweighted": _safe_div(
                    products.get("sawlog", 0.0) + products.get("other", 0.0),
                    recovered,
                ),
                "residue_m3_per_recovered_m3_unweighted": _safe_div(
                    residue_m3, recovered
                ),
                "local_taper_trees": local_count,
                "donor_taper_trees": len(province_trees) - local_count,
                "volume_balance_m3": abs(
                    recovered_expanded + residue_m3_expanded - taper_total_expanded
                ),
                "max_tree_balance_error_m3": max(
                    (row.balance_error_m3 for row in province_residues), default=0.0
                ),
                "taper_status": (
                    "local_published" if local_count == len(province_trees)
                    else "donor_published"
                ),
                "tree_list_basis": (
                    BC_BASIS
                    if province == "BC"
                    else min(
                        {
                            str(extras[tree.tree_id]["tree_list_basis"])
                            for tree in province_trees
                        }
                    )
                ),
                "tree_list_labels": ";".join(labels),
                "model_estimate": True,
                "synthetic": province != "BC",
                "observed": False,
                "policy_grade": False,
                "national_claim": False,
            }
        )
    return rows


def _province_name(province: str) -> str:
    from .national_bucking_pilot_v1 import PROVINCE_NAMES

    return PROVINCE_NAMES[province]


def _coefficient_comparison_v2(
    province_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    facility = pd.read_csv(FACILITY_CHAIN_PATH)
    facility_by_province = {
        str(row["province"]).strip().upper(): row for _, row in facility.iterrows()
    }
    rows: list[dict[str, Any]] = []
    for province_row in province_rows:
        province = str(province_row["province"])
        record = facility_by_province.get(province)
        if record is None:
            raise SchemaError(f"facility product chain has no row for {province}")
        harvest = float(record["harvest_m3"])
        sawlog_facility = _safe_div(
            float(record["sawlog_feedstock_m3"]), harvest
        )
        pulp_facility = _safe_div(float(record["pulp_feedstock_m3"]), harvest)
        solid_facility = _safe_div(
            float(record["sawlog_feedstock_m3"])
            + float(record["panel_feedstock_m3"]),
            harvest,
        )
        residue_facility = _safe_div(
            float(record["harvest_residue_gross_odt"]), harvest
        )
        model_sawlog = float(province_row["sawlog_share_of_recovered"])
        model_pulp = float(province_row["pulpwood_share_of_recovered"])
        model_other = float(province_row["other_share_of_recovered"])
        model_solid = float(province_row["solid_share_of_recovered"])
        model_residue = float(province_row["residue_odt_per_recovered_m3"])
        is_comparable = (
            harvest > 0.0
            and float(province_row["recovered_gross_m3_expanded"]) > 0.0
        )
        solid_ratio = _safe_div(model_solid, solid_facility)
        residue_ratio = _safe_div(model_residue, residue_facility)
        if not is_comparable:
            status = "not_comparable_zero_harvest"
        elif 0.8 <= solid_ratio <= 1.2 and 0.8 <= residue_ratio <= 1.2:
            status = "within_20pct"
        else:
            status = "outside_20pct"
        is_bc = province == "BC"
        rows.append(
            {
                "province": province,
                "province_name": _province_name(province),
                "model_n_trees": int(province_row["n_trees"]),
                "model_recovered_m3_expanded": float(
                    province_row["recovered_gross_m3_expanded"]
                ),
                "model_sawlog_share": model_sawlog,
                "model_pulpwood_share": model_pulp,
                "model_other_share": model_other,
                "model_solid_share": model_solid,
                "model_residue_m3_per_recovered_m3": float(
                    province_row["residue_m3_per_recovered_m3"]
                ),
                "model_residue_odt_per_recovered_m3": model_residue,
                "facility_harvest_m3": harvest,
                "facility_sawlog_feedstock_share": sawlog_facility,
                "facility_pulp_feedstock_share": pulp_facility,
                "facility_solid_feedstock_share": solid_facility,
                "facility_harvest_residue_odt_per_m3": residue_facility,
                "sawlog_share_model_over_facility": _safe_div(
                    model_sawlog, sawlog_facility
                ),
                "solid_share_model_over_facility": solid_ratio,
                "residue_odt_model_over_facility": residue_ratio,
                "comparison_status": status,
                "facility_coefficient_tier": str(
                    record.get("coefficient_tier", "")
                ),
                "taper_status": province_row["taper_status"],
                "tree_list_basis": province_row["tree_list_basis"],
                "tree_list_label": province_row["tree_list_label"],
                "dominant_factor": (
                    "measured_tree_list;dimension_sort_assumption;local_taper"
                    if is_bc
                    else "synthetic_tree_list;dimension_sort_assumption;"
                    + (
                        "donor_taper"
                        if province_row["taper_status"] == "donor_published"
                        else "local_taper"
                    )
                ),
                "donor_or_assumption_dominates": not is_bc,
                "model_estimate": True,
                "synthetic": not is_bc,
                "observed": False,
                "policy_grade": False,
                "national_claim": False,
                "notes": (
                    "Model product shares use expansion-weighted recovered log "
                    "volume.  Facility feedstock shares are from "
                    "facility_product_chain_v1.  Harvest residue and facility "
                    "process residue are different streams; the process-residue "
                    "coefficient is not compared here."
                ),
            }
        )
    if len(rows) != len(PROVINCES):
        raise SchemaError(
            f"expected {len(PROVINCES)} coefficient rows, got {len(rows)}"
        )
    return rows


def _bc_facility_comparison(
    v1_province_rows: Mapping[str, Mapping[str, Any]],
    v2_province_rows: Mapping[str, Mapping[str, Any]],
    bc_tipsy_summary: Mapping[str, Any],
) -> list[dict[str, Any]]:
    facility = pd.read_csv(FACILITY_CHAIN_PATH)
    record = facility[facility["province"] == "BC"].iloc[0]
    harvest = float(record["harvest_m3"])
    facility_sawlog = _safe_div(float(record["sawlog_feedstock_m3"]), harvest)
    facility_pulp = _safe_div(float(record["pulp_feedstock_m3"]), harvest)
    facility_solid = _safe_div(
        float(record["sawlog_feedstock_m3"]) + float(record["panel_feedstock_m3"]),
        harvest,
    )
    facility_residue = _safe_div(
        float(record["harvest_residue_gross_odt"]), harvest
    )
    variants = [
        (
            "BC_V1_SYNTHETIC_NATIONAL_SORTS",
            v1_province_rows["BC"],
            "pilot v1 synthetic 10-tree list; national pilot sorts",
        ),
        (
            "BC_V2_MEASURED_TIPSY_SORTS",
            bc_tipsy_summary,
            "pilot v2 measured aggregate list; BC interior TIPSY sorts",
        ),
        (
            "BC_V2_MEASURED_NATIONAL_SORTS",
            v2_province_rows["BC"],
            "pilot v2 measured aggregate list; national pilot sorts",
        ),
    ]
    rows: list[dict[str, Any]] = []
    for variant, values, note in variants:
        if variant.startswith("BC_V2_MEASURED_TIPSY"):
            sawlog = float(values["sawlog_share_of_recovered"])
            pulp = float(values["pulpwood_share_of_recovered"])
            other = float(values["other_share_of_recovered"])
            solid = float(values["solid_share_of_recovered"])
            residue = float(values["residue_odt_per_recovered_m3"])
            recovered = float(values["recovered_gross_m3_expanded"])
            n_trees = int(values["n_trees"])
            n_logs = int(values["n_logs"])
        elif variant == "BC_V1_SYNTHETIC_NATIONAL_SORTS":
            sawlog = float(values["sawlog_share_of_recovered"])
            pulp = float(values["pulpwood_share_of_recovered"])
            other = float(values["other_share_of_recovered"])
            solid = sawlog + other
            residue = float(values["residue_odt"]) / max(
                float(values["recovered_gross_m3"]), 1e-12
            )
            recovered = float(values["recovered_gross_m3"])
            n_trees = int(values["n_trees"])
            n_logs = int(values["n_logs"])
        else:
            sawlog = float(values["sawlog_share_of_recovered"])
            pulp = float(values["pulpwood_share_of_recovered"])
            other = float(values["other_share_of_recovered"])
            solid = float(values["solid_share_of_recovered"])
            residue = float(values["residue_odt_per_recovered_m3"])
            recovered = float(values["recovered_gross_m3_expanded"])
            n_trees = int(values["n_trees"])
            n_logs = int(values["n_logs"])
        solid_ratio = _safe_div(solid, facility_solid)
        residue_ratio = _safe_div(residue, facility_residue)
        if 0.8 <= solid_ratio <= 1.2 and 0.8 <= residue_ratio <= 1.2:
            status = "within_20pct"
        else:
            status = "outside_20pct"
        rows.append(
            {
                "variant": variant,
                "description": note,
                "n_trees": n_trees,
                "n_logs": n_logs,
                "recovered_m3": recovered,
                "sawlog_share": sawlog,
                "pulpwood_share": pulp,
                "other_share": other,
                "solid_share": solid,
                "residue_odt_per_recovered_m3": residue,
                "facility_sawlog_feedstock_share": facility_sawlog,
                "facility_pulp_feedstock_share": facility_pulp,
                "facility_solid_feedstock_share": facility_solid,
                "facility_harvest_residue_odt_per_m3": facility_residue,
                "sawlog_share_model_over_facility": _safe_div(
                    sawlog, facility_sawlog
                ),
                "solid_share_model_over_facility": solid_ratio,
                "residue_odt_model_over_facility": residue_ratio,
                "comparison_status": status,
                "model_estimate": True,
                "synthetic": variant == "BC_V1_SYNTHETIC_NATIONAL_SORTS",
                "observed": False,
                "policy_grade": False,
                "national_claim": False,
                "notes": (
                    "Facility feedstock classes are mill receipts, not TIPSY "
                    "dimension sorts.  Solid share is sawlog + other for model "
                    "rows and sawlog + panel for the facility row.  Harvest "
                    "residue is a separate stream from mill process residue."
                ),
            }
        )
    rows.append(
        {
            "variant": "BC_FACILITY_FEEDSTOCK",
            "description": "facility_product_chain_v1 BC coefficients",
            "n_trees": 0,
            "n_logs": 0,
            "recovered_m3": harvest,
            "sawlog_share": facility_sawlog,
            "pulpwood_share": facility_pulp,
            "other_share": _safe_div(float(record["panel_feedstock_m3"]), harvest),
            "solid_share": facility_solid,
            "residue_odt_per_recovered_m3": facility_residue,
            "facility_sawlog_feedstock_share": facility_sawlog,
            "facility_pulp_feedstock_share": facility_pulp,
            "facility_solid_feedstock_share": facility_solid,
            "facility_harvest_residue_odt_per_m3": facility_residue,
            "sawlog_share_model_over_facility": 1.0,
            "solid_share_model_over_facility": 1.0,
            "residue_odt_model_over_facility": 1.0,
            "comparison_status": "reference",
            "model_estimate": True,
            "synthetic": False,
            "observed": False,
            "policy_grade": False,
            "national_claim": False,
            "notes": "Reference row.  Used for ratio denominators.",
        }
    )
    return rows


def _v1_v2_comparison(
    province_rows_v2: Sequence[Mapping[str, Any]],
    v1_province_csv: Path,
) -> list[dict[str, Any]]:
    v1 = pd.read_csv(v1_province_csv)
    v1_by_province = {
        str(row["province"]): row for _, row in v1.iterrows()
    }
    rows: list[dict[str, Any]] = []
    for row in province_rows_v2:
        province = str(row["province"])
        old = v1_by_province.get(province)
        if old is None:
            raise SchemaError(f"pilot v1 province summary has no row for {province}")
        v1_solid = float(old["sawlog_share_of_recovered"]) + float(
            old["other_share_of_recovered"]
        )
        v2_solid = float(row["solid_share_of_recovered"])
        v1_recovered = float(old["recovered_gross_m3"])
        v2_recovered = float(row["recovered_gross_m3_expanded"])
        rows.append(
            {
                "comparison_scope": "province",
                "province": province,
                "province_name": row["province_name"],
                "basis": (
                    "v1_unweighted_vs_v2_expanded"
                    if province == "BC"
                    else "v1_unweighted_vs_v2_unweighted_expansion_one"
                ),
                "v1_n_trees": int(old["n_trees"]),
                "v2_n_trees": int(row["n_trees"]),
                "v1_n_logs": int(old["n_logs"]),
                "v2_n_logs": int(row["n_logs"]),
                "v1_logs_per_tree": _safe_div(
                    float(old["n_logs"]), float(old["n_trees"])
                ),
                "v2_logs_per_tree": _safe_div(
                    float(row["n_logs"]), float(row["n_trees"])
                ),
                "v1_recovered_m3": v1_recovered,
                "v2_recovered_m3": v2_recovered,
                "v1_recovered_per_tree": _safe_div(
                    v1_recovered, float(old["n_trees"])
                ),
                "v2_recovered_per_tree": _safe_div(
                    v2_recovered, float(row["n_trees"])
                ),
                "v1_residue_m3": float(old["residue_m3"]),
                "v2_residue_m3": float(row["residue_m3_expanded"]),
                "v1_residue_m3_per_recovered_m3": float(
                    old["residue_m3_per_recovered_m3"]
                ),
                "v2_residue_m3_per_recovered_m3": float(
                    row["residue_m3_per_recovered_m3"]
                ),
                "v1_residue_odt_per_recovered_m3": _safe_div(
                    float(old["residue_odt"]), v1_recovered
                ),
                "v2_residue_odt_per_recovered_m3": float(
                    row["residue_odt_per_recovered_m3"]
                ),
                "v1_sawlog_share": float(old["sawlog_share_of_recovered"]),
                "v2_sawlog_share": float(row["sawlog_share_of_recovered"]),
                "v1_pulpwood_share": float(old["pulpwood_share_of_recovered"]),
                "v2_pulpwood_share": float(row["pulpwood_share_of_recovered"]),
                "v1_other_share": float(old["other_share_of_recovered"]),
                "v2_other_share": float(row["other_share_of_recovered"]),
                "v1_solid_share": v1_solid,
                "v2_solid_share": v2_solid,
                "solid_share_delta": v2_solid - v1_solid,
                "residue_odt_per_recovered_delta": float(
                    row["residue_odt_per_recovered_m3"]
                )
                - _safe_div(float(old["residue_odt"]), v1_recovered),
                "tree_list_label_v1": "representative_synthetic",
                "tree_list_label_v2": row["tree_list_label"],
                "model_estimate": True,
                "synthetic": province != "BC",
                "observed": False,
                "policy_grade": False,
                "national_claim": False,
            }
        )

    def mean_of(column: str) -> float:
        return float(np.mean([float(row[column]) for row in province_rows_v2]))

    macro = {
        "comparison_scope": "national_province_macro",
        "province": "NATIONAL_MACRO",
        "province_name": "13-province equal-weight macro",
        "basis": "mean_of_13_province_metrics",
        "v1_n_trees": int(v1["n_trees"].sum()),
        "v2_n_trees": int(sum(int(row["n_trees"]) for row in province_rows_v2)),
        "v1_n_logs": int(v1["n_logs"].sum()),
        "v2_n_logs": int(sum(int(row["n_logs"]) for row in province_rows_v2)),
        "v1_logs_per_tree": float(
            np.mean((v1["n_logs"] / v1["n_trees"]).to_numpy(dtype=float))
        ),
        "v2_logs_per_tree": mean_of("n_logs") / mean_of("n_trees"),
        "v1_recovered_m3": float(v1["recovered_gross_m3"].sum()),
        "v2_recovered_m3": float(
            sum(float(row["recovered_gross_m3_expanded"]) for row in province_rows_v2)
        ),
        "v1_recovered_per_tree": float(
            np.mean(
                (v1["recovered_gross_m3"] / v1["n_trees"]).to_numpy(dtype=float)
            )
        ),
        "v2_recovered_per_tree": mean_of("recovered_gross_m3_expanded")
        / mean_of("n_trees"),
        "v1_residue_m3": float(v1["residue_m3"].sum()),
        "v2_residue_m3": float(
            sum(float(row["residue_m3_expanded"]) for row in province_rows_v2)
        ),
        "v1_residue_m3_per_recovered_m3": float(
            np.mean(v1["residue_m3_per_recovered_m3"].to_numpy(dtype=float))
        ),
        "v2_residue_m3_per_recovered_m3": mean_of(
            "residue_m3_per_recovered_m3"
        ),
        "v1_residue_odt_per_recovered_m3": float(
            np.mean(
                (
                    v1["residue_odt"]
                    / v1["recovered_gross_m3"].where(
                        v1["recovered_gross_m3"] > 0, np.nan
                    )
                )
                .fillna(0.0)
                .to_numpy(dtype=float)
            )
        ),
        "v2_residue_odt_per_recovered_m3": mean_of(
            "residue_odt_per_recovered_m3"
        ),
        "v1_sawlog_share": float(
            np.mean(v1["sawlog_share_of_recovered"].to_numpy(dtype=float))
        ),
        "v2_sawlog_share": mean_of("sawlog_share_of_recovered"),
        "v1_pulpwood_share": float(
            np.mean(v1["pulpwood_share_of_recovered"].to_numpy(dtype=float))
        ),
        "v2_pulpwood_share": mean_of("pulpwood_share_of_recovered"),
        "v1_other_share": float(
            np.mean(v1["other_share_of_recovered"].to_numpy(dtype=float))
        ),
        "v2_other_share": mean_of("other_share_of_recovered"),
        "v1_solid_share": float(
            np.mean(
                (
                    v1["sawlog_share_of_recovered"]
                    + v1["other_share_of_recovered"]
                ).to_numpy(dtype=float)
            )
        ),
        "v2_solid_share": mean_of("solid_share_of_recovered"),
        "solid_share_delta": mean_of("solid_share_of_recovered")
        - float(
            np.mean(
                (
                    v1["sawlog_share_of_recovered"]
                    + v1["other_share_of_recovered"]
                ).to_numpy(dtype=float)
            )
        ),
        "residue_odt_per_recovered_delta": mean_of(
            "residue_odt_per_recovered_m3"
        )
        - float(
            np.mean(
                (
                    v1["residue_odt"]
                    / v1["recovered_gross_m3"].where(
                        v1["recovered_gross_m3"] > 0, np.nan
                    )
                )
                .fillna(0.0)
                .to_numpy(dtype=float)
            )
        ),
        "tree_list_label_v1": "representative_synthetic",
        "tree_list_label_v2": "mixed_measured_bc_synthetic_others",
        "model_estimate": True,
        "synthetic": False,
        "observed": False,
        "policy_grade": False,
        "national_claim": False,
    }
    rows.append(macro)

    aggregate = dict(macro)
    aggregate.update(
        {
            "comparison_scope": "national_mixed_expanded_aggregate",
            "province": "NATIONAL_MIXED_EXPANDED",
            "province_name": "13-province mixed-basis aggregate",
            "basis": (
                "BC expansion-weighted measured list + unit-weight synthetic "
                "lists; not a national supply estimate"
            ),
            "v1_logs_per_tree": _safe_div(
                float(v1["n_logs"].sum()), float(v1["n_trees"].sum())
            ),
            "v2_logs_per_tree": _safe_div(
                float(sum(int(row["n_logs"]) for row in province_rows_v2)),
                float(sum(int(row["n_trees"]) for row in province_rows_v2)),
            ),
            "v1_recovered_per_tree": _safe_div(
                float(v1["recovered_gross_m3"].sum()),
                float(v1["n_trees"].sum()),
            ),
            "v2_recovered_per_tree": _safe_div(
                float(
                    sum(
                        float(row["recovered_gross_m3_expanded"])
                        for row in province_rows_v2
                    )
                ),
                float(sum(int(row["n_trees"]) for row in province_rows_v2)),
            ),
            "v1_residue_m3_per_recovered_m3": _safe_div(
                float(v1["residue_m3"].sum()),
                float(v1["recovered_gross_m3"].sum()),
            ),
            "v2_residue_m3_per_recovered_m3": _safe_div(
                float(
                    sum(
                        float(row["residue_m3_expanded"])
                        for row in province_rows_v2
                    )
                ),
                float(
                    sum(
                        float(row["recovered_gross_m3_expanded"])
                        for row in province_rows_v2
                    )
                ),
            ),
            "v1_residue_odt_per_recovered_m3": _safe_div(
                float(v1["residue_odt"].sum()),
                float(v1["recovered_gross_m3"].sum()),
            ),
            "v2_residue_odt_per_recovered_m3": _safe_div(
                float(
                    sum(
                        float(row["residue_odt_expanded"])
                        for row in province_rows_v2
                    )
                ),
                float(
                    sum(
                        float(row["recovered_gross_m3_expanded"])
                        for row in province_rows_v2
                    )
                ),
            ),
            "v1_sawlog_share": _safe_div(
                float(v1["sawlog_m3"].sum()),
                float(v1["recovered_gross_m3"].sum()),
            ),
            "v2_sawlog_share": _safe_div(
                float(
                    sum(
                        float(row["sawlog_m3_expanded"])
                        for row in province_rows_v2
                    )
                ),
                float(
                    sum(
                        float(row["recovered_gross_m3_expanded"])
                        for row in province_rows_v2
                    )
                ),
            ),
            "v1_pulpwood_share": _safe_div(
                float(v1["pulpwood_m3"].sum()),
                float(v1["recovered_gross_m3"].sum()),
            ),
            "v2_pulpwood_share": _safe_div(
                float(
                    sum(
                        float(row["pulpwood_m3_expanded"])
                        for row in province_rows_v2
                    )
                ),
                float(
                    sum(
                        float(row["recovered_gross_m3_expanded"])
                        for row in province_rows_v2
                    )
                ),
            ),
            "v1_other_share": _safe_div(
                float(v1["other_m3"].sum()),
                float(v1["recovered_gross_m3"].sum()),
            ),
            "v2_other_share": _safe_div(
                float(
                    sum(
                        float(row["other_m3_expanded"])
                        for row in province_rows_v2
                    )
                ),
                float(
                    sum(
                        float(row["recovered_gross_m3_expanded"])
                        for row in province_rows_v2
                    )
                ),
            ),
        }
    )
    aggregate["v1_solid_share"] = aggregate["v1_sawlog_share"] + aggregate[
        "v1_other_share"
    ]
    aggregate["v2_solid_share"] = aggregate["v2_sawlog_share"] + aggregate[
        "v2_other_share"
    ]
    aggregate["solid_share_delta"] = (
        aggregate["v2_solid_share"] - aggregate["v1_solid_share"]
    )
    aggregate["residue_odt_per_recovered_delta"] = (
        aggregate["v2_residue_odt_per_recovered_m3"]
        - aggregate["v1_residue_odt_per_recovered_m3"]
    )
    rows.append(aggregate)
    return rows


def _bc_population_reconciliation(
    bc_tipsy_summary: Mapping[str, Any],
    v2_province_rows: Mapping[str, Mapping[str, Any]],
    v0_ledger_path: Path = BC_TREE_LEDGER_PATH,
) -> list[dict[str, Any]]:
    ledger = pd.read_csv(v0_ledger_path)
    bucked = ledger[ledger["bucked_status"] == "bucked"].copy()
    v0 = {
        "taper_total_m3_expanded": float(
            (bucked["taper_total_m3"] * bucked["expansion_factor"]).sum()
        ),
        "recovered_gross_m3_expanded": float(
            (bucked["recovered_log_m3"] * bucked["expansion_factor"]).sum()
        ),
        "residue_m3_expanded": float(
            (bucked["stem_residue_m3"] * bucked["expansion_factor"]).sum()
        ),
        "residue_odt_expanded": float(
            (bucked["residue_odt"] * bucked["expansion_factor"]).sum()
        ),
    }
    national = v2_province_rows["BC"]
    rows: list[dict[str, Any]] = []
    for metric, v0_value in v0.items():
        tipsy_value = float(bc_tipsy_summary[metric])
        national_value = float(national[metric])
        rows.append(
            {
                "metric": metric,
                "v0_full_population_expanded": v0_value,
                "v2_bc_tipsy_expanded": tipsy_value,
                "v2_bc_national_sorts_expanded": national_value,
                "tipsy_ratio_to_v0": _safe_div(tipsy_value, v0_value),
                "tipsy_relative_difference": _safe_div(
                    tipsy_value - v0_value, v0_value
                ),
                "national_sorts_ratio_to_v0": _safe_div(
                    national_value, v0_value
                ),
                "national_sorts_relative_difference": _safe_div(
                    national_value - v0_value, v0_value
                ),
                "v0_population": (
                    "bc_psp_validation_v0/tree_ledger.csv bucked rows; "
                    "PHF_TREE expansion applied"
                ),
                "note": (
                    "The BC empirical list is a post-stratified measured "
                    "aggregate.  Differences are sampling and sort-rule effects, "
                    "not a new inventory estimate."
                ),
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _schema_markdown() -> str:
    return """# National bucking pilot v2 schema

Every record is a pilot record.  BC tree geometry is measured FAIB PSP data;
the other 12 jurisdictions keep the v1 representative synthetic geometry.

## Labels

| Column | BC | Other 12 |
|---|---|---|
| `tree_list_label` | `measured_tree_list` | `representative_synthetic` |
| `measured_tree_list` | `true` | `false` |
| `synthetic` | `false` | `true` |
| `model_estimate` (tree rows) | `false` | `true` |
| `model_estimate` (log/residue/summary rows) | `true` | `true` |
| `observed` | `false` | `false` |
| `policy_grade` | `false` | `false` |
| `national_claim` | `false` | `false` |

BC bucked logs and residue are model estimates because taper and sort rules are
published models applied to measured stems.  They are not observed mill
receipts.  `grade=not_assigned`, `sort_basis=dimension_only`.

## Tables

| Table | Grain | Content |
|---|---|---|
| `tree_list.csv` | one selected stem | Combined 13-jurisdiction list |
| `tree_lists/BC.csv` | one measured aggregate stem | BC empirical list |
| `bc_tree_list_selection.csv` | one retained stem | Stratum, source PHF, pooled PHF |
| `bucked_logs.csv` | one recovered log | National-sort v2 pilot |
| `residue_ledger.csv` | one stem | National-sort v2 pilot |
| `reconciliation.csv` | one stem | National-sort v2 volume audit |
| `bc_tipsy_logs.csv` | one recovered log | BC interior TIPSY sort rerun |
| `bc_tipsy_residue.csv` | one stem | BC TIPSY residue |
| `bc_tipsy_reconciliation.csv` | one stem | BC TIPSY volume audit |
| `bc_tipsy_sort_rules.yaml`, `.csv` | one sort rule | Exact BC TIPSY rule file used |
| `province_summary.csv` | one province | Unweighted and expansion-weighted metrics |
| `coefficient_comparison.csv` | one province | Model versus facility coefficients |
| `bc_facility_comparison.csv` | one BC variant | v1, v2 TIPSY, v2 national, facility reference |
| `v1_v2_comparison.csv` | one province plus national rows | v1 to v2 deltas |
| `bc_population_reconciliation.csv` | one metric | v2 list versus full v0 population |
| `summary.json` | artifact | Machine-readable totals, labels, checks |
| `report.md` | artifact | Human-readable report |
| `manifest.json` | artifact | Inputs, outputs, hashes, checks |

## Expansion factors

- BC: `expansion_factor` is the pooled `PHF_TREE` total of the
  species x BEC zone x 2.5 cm DBH stratum.  `source_expansion_factor` is the
  retained stem's original FAIB value.  The BC list preserves the source PHF
  total exactly.
- Other provinces: `expansion_factor=1.0`; the v1 tree is a unit-weight
  representative draw, not an inventory expansion.

Absolute volumes are therefore not comparable between BC and the other
provinces.  Shares and per-tree metrics are the comparison basis; national rows
are explicitly macro or mixed-basis, never a national supply estimate.
"""


def _readme() -> str:
    return """# National bucking pilot v2 — measured BC list

**Scope: pilot.  BC tree geometry is measured FAIB PSP TSA24 data; the other 12
jurisdictions keep the v1 representative synthetic lists.  Not inventory, not a
national supply estimate, not an official grade.**

BC replaces the ten v1 synthetic trees with a post-stratified aggregate of
measured TSA24 PSP stems.  Within each species x BEC zone x 2.5 cm DBH stratum,
one PHF-weighted median stem is retained and assigned the stratum's summed
`PHF_TREE` expansion factor.  The BC list preserves the source expansion total
exactly and retains the measured DBH/height pair.  See
`bc_tree_list_selection.csv`.

BC is bucked twice:

1. `bc_tipsy_*`: BC Kozak (2004) taper with the BC interior TIPSY 4.5 dimension
   sorts, matching the `bc_psp_validation_v0` rule set.
2. `bucked_logs.csv` / `residue_ledger.csv`: the national pilot dimension sort
   set, so v1 and v2 national product shares are comparable.

The other 12 jurisdictions use the v1 local/donor taper dispatch and national
pilot sorts.  `grade=not_assigned`; no official grade, policy grade, or market
price is claimed.  Residue is taper total minus recovered gross logs per stem.

## Files

| File | Content |
|---|---|
| `bc_tree_list_selection.csv` | BC stratum selection and source provenance |
| `tree_lists/BC.csv` | BC measured aggregate tree list |
| `bc_tipsy_logs.csv`, `bc_tipsy_residue.csv` | BC TIPSY dimension-sort rerun |
| `bc_tipsy_sort_rules.yaml`, `bc_tipsy_sort_rules.csv` | Exact BC TIPSY rule file used |
| `bucked_logs.csv`, `residue_ledger.csv` | National-sort pilot |
| `province_summary.csv` | Per-province unweighted and expanded metrics |
| `coefficient_comparison.csv` | 13-province facility screening |
| `bc_facility_comparison.csv` | BC v1/v2 variant fit to facility coefficients |
| `v1_v2_comparison.csv` | Province and national v1-to-v2 deltas |
| `bc_population_reconciliation.csv` | v2 list versus full v0 population |
| `summary.json`, `report.md`, `manifest.json` | Machine-readable and human-readable audit |

## Reproduce

```bash
.venv/bin/python -m workflows.national.bucking.national_bucking_pilot_v2 \\
  --out-dir data/derived/national/bucking/national_bucking_pilot_v2
```
"""


def _report_markdown(
    summary: Mapping[str, Any],
    province_rows: Sequence[Mapping[str, Any]],
    comparison_rows: Sequence[Mapping[str, Any]],
    bc_facility_rows: Sequence[Mapping[str, Any]],
    v1_v2_rows: Sequence[Mapping[str, Any]],
    reconciliation_rows: Sequence[Mapping[str, Any]],
) -> str:
    lines: list[str] = []
    lines.append("# National bucking pilot v2 — measured BC list\n")
    lines.append(
        "**Scope: pilot.  Not inventory, not a national supply estimate, not an "
        "official grade.**  BC tree geometry is measured FAIB PSP TSA24 data; "
        "the other 12 jurisdictions are representative synthetic lists.  BC "
        "logs and residue are model estimates (`observed=false`, "
        "`policy_grade=false`, `national_claim=false`).\n"
    )
    lines.append("## BC empirical tree list\n")
    bc_stats = summary["bc_tree_list"]
    lines.append(
        f"- Source: {bc_stats['source_bucked_rows']:,} buckable live TSA24 PSP "
        f"stems from `selected_trees.csv`; {bc_stats['excluded_not_bucked_rows']:,} "
        "v0-skipped stems excluded (unsupported taper species or reference "
        "bucker balance failure).\n"
    )
    lines.append(
        f"- Selection: one measured PHF-weighted median stem per species x BEC "
        f"zone x 2.5 cm DBH stratum; {bc_stats['selected_trees']:,} retained "
        f"stems in {bc_stats['strata']:,} strata.\n"
    )
    lines.append(
        f"- Expansion: source PHF {bc_stats['source_phf_total']:,.3f}; selected "
        f"PHF {bc_stats['selected_phf_total']:,.3f}; "
        f"relative error {bc_stats['phf_relative_error']:.3g}.  DBH and height "
        "are the measured retained values; no synthetic geometry.\n"
    )
    lines.append("| Species | Source stems | Selected stems |\n")
    lines.append("|---|---:|---:|\n")
    for species in sorted(bc_stats["species"]):
        lines.append(
            f"| {species} | {bc_stats['species'][species]} | "
            f"{bc_stats['selected_species'].get(species, 0)} |\n"
        )
    lines.append("\n## BC bucking results\n")
    tipsy = summary["bc_tipsy"]
    lines.append(
        "| Rule set | Trees | Logs | Recovered m3 (expanded) | Residue m3 | "
        "Residue odt | Sawlog share | Pulpwood share | Other share | "
        "Solid share | Residue odt/m3 |\n"
    )
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|\n")
    for label, row in (
        ("BC interior TIPSY", tipsy),
        ("National pilot sorts", summary["bc_national"]),
    ):
        lines.append(
            f"| {label} | {row['n_trees']} | {row['n_logs']} | "
            f"{row['recovered_gross_m3_expanded']:.3f} | "
            f"{row['residue_m3_expanded']:.3f} | "
            f"{row['residue_odt_expanded']:.3f} | "
            f"{row['sawlog_share_of_recovered']:.3f} | "
            f"{row['pulpwood_share_of_recovered']:.3f} | "
            f"{row['other_share_of_recovered']:.3f} | "
            f"{row['solid_share_of_recovered']:.3f} | "
            f"{row['residue_odt_per_recovered_m3']:.4f} |\n"
        )
    lines.append("\n## v1→v2 comparison\n")
    lines.append(
        "| Scope | v1 trees | v2 trees | v1 logs/tree | v2 logs/tree | "
        "v1 residue m3/m3 | v2 residue m3/m3 | v1 solid share | v2 solid share | "
        "Solid delta |\n"
    )
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|\n")
    for row in v1_v2_rows:
        if row["comparison_scope"] not in {"province", "national_province_macro",
                                           "national_mixed_expanded_aggregate"}:
            continue
        if row["comparison_scope"] == "province" and row["province"] != "BC":
            continue
        lines.append(
            f"| {row['province']} | {row['v1_n_trees']} | {row['v2_n_trees']} | "
            f"{row['v1_logs_per_tree']:.3f} | {row['v2_logs_per_tree']:.3f} | "
            f"{row['v1_residue_m3_per_recovered_m3']:.4f} | "
            f"{row['v2_residue_m3_per_recovered_m3']:.4f} | "
            f"{row['v1_solid_share']:.3f} | {row['v2_solid_share']:.3f} | "
            f"{row['solid_share_delta']:+.3f} |\n"
        )
    lines.append(
        "\nOnly BC changes; the other 12 province rows are the v1 trees and "
        "sort rules.  Absolute v1 and v2 volumes are not directly comparable "
        "because the BC row changes from a 10-tree unweighted sample to an "
        "expansion-weighted measured aggregate.  Shares and per-tree metrics "
        "are the comparison basis.  National rows are macro or mixed-basis, "
        "never a national supply estimate.\n"
    )
    lines.append("\n## Facility coefficient fit\n")
    lines.append(
        "| Variant | Sawlog share | Solid share | Residue odt/m3 | "
        "Solid/facility | Residue/facility | Status |\n"
    )
    lines.append("|---|---:|---:|---:|---:|---:|---|\n")
    for row in bc_facility_rows:
        lines.append(
            f"| {row['variant']} | {row['sawlog_share']:.3f} | "
            f"{row['solid_share']:.3f} | "
            f"{row['residue_odt_per_recovered_m3']:.4f} | "
            f"{row['solid_share_model_over_facility']:.3f} | "
            f"{row['residue_odt_model_over_facility']:.3f} | "
            f"{row['comparison_status']} |\n"
        )
    lines.append(
        "\nFacility feedstock shares are mill receipts from "
        "`facility_product_chain_v1`, not TIPSY dimension classes.  The TIPSY "
        "rule set classifies nearly all conifer logs with a 12.5 cm top as "
        "sawlog, so its solid share is not expected to equal the facility "
        "sawlog + panel feedstock share.  The comparison is screening, not "
        "calibration.\n"
    )
    lines.append("\n## Expansion-weighted population check\n")
    lines.append(
        "| Metric | v0 full population | v2 TIPSY | TIPSY/v0 | v2 national | "
        "National/v0 |\n"
    )
    lines.append("|---|---:|---:|---:|---:|---:|\n")
    for row in reconciliation_rows:
        lines.append(
            f"| {row['metric']} | {row['v0_full_population_expanded']:.3f} | "
            f"{row['v2_bc_tipsy_expanded']:.3f} | "
            f"{row['tipsy_ratio_to_v0']:.4f} | "
            f"{row['v2_bc_national_sorts_expanded']:.3f} | "
            f"{row['national_sorts_ratio_to_v0']:.4f} |\n"
        )
    lines.append("\n## Checks\n")
    for check in summary.get("checks", []):
        lines.append(
            f"- `{check['check_id']}`: **{check['status']}** — "
            f"{check['detail']}\n"
        )
    lines.append("\n## Limitations\n")
    for limitation in summary.get("limitations", []):
        lines.append(f"- {limitation}\n")
    lines.append(
        "\nNo UI, public OSeCAN surface, policy grade, AAC, or national supply "
        "claim is made.\n"
    )
    return "".join(lines)


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------


def generate_pilot(
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    *,
    bc_rules_path: Path = BC_RULES_PATH,
) -> dict[str, Any]:
    """Build the v2 pilot and write the output directory."""
    started = time.monotonic()
    deletions = assert_no_unexpected_deletions()
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    bc_rules_path = Path(bc_rules_path)
    if not bc_rules_path.is_absolute():
        bc_rules_path = REPO_ROOT / bc_rules_path

    qc_registry = load_taper_registry(QC_TAPER_PATH)
    kozak_path = default_kozak_parameter_path()
    kozak_parameters = load_kozak_parameters(kozak_path)
    rules = load_national_sort_rules(SORT_RULES_PATH)

    print("Building measured BC empirical tree list...", file=sys.stderr)
    bc_trees, bc_tapers, bc_extras, selection, bc_stats = (
        build_bc_empirical_tree_list(qc_registry, kozak_parameters)
    )
    print(
        f"BC measured aggregate: {len(bc_trees):,} stems in "
        f"{bc_stats['strata']:,} strata; PHF preserved.",
        file=sys.stderr,
    )

    print("Bucking BC measured list with TIPSY interior sorts...", file=sys.stderr)
    tipsy = buck_bc_tipsy(bc_trees, bc_extras, rules_path=bc_rules_path)
    print(
        f"BC TIPSY: {tipsy['summary']['n_logs']:,} logs, "
        f"{tipsy['summary']['recovered_gross_m3_expanded']:,.3f} m3 expanded.",
        file=sys.stderr,
    )

    print("Assembling 13-jurisdiction v2 tree list...", file=sys.stderr)
    trees, tapers, extras, _compositions = build_national_v2_tree_list(
        qc_registry, kozak_parameters, bc_trees, bc_tapers, bc_extras
    )

    print("Bucking national v2 list...", file=sys.stderr)
    logs, residues = buck_national_v2(trees, tapers, rules)

    logs_by_tree: dict[str, list[PilotBuckedLog]] = {}
    for log in logs:
        logs_by_tree.setdefault(log.tree_id, []).append(log)
    tree_by_id = {tree.tree_id: tree for tree in trees}

    tree_rows: list[dict[str, Any]] = []
    for tree in trees:
        row = tree.to_dict()
        row.update(extras[tree.tree_id])
        tree_rows.append(row)

    log_rows: list[dict[str, Any]] = []
    for log in logs:
        row = log.to_dict()
        extra = extras[log.tree_id]
        weight = float(extra["expansion_factor"])
        row.update(
            {
                "taper_status": extra["taper_status"],
                "taper_jurisdiction": extra["taper_jurisdiction"],
                "taper_bec_zone": extra["taper_bec_zone"],
                "taper_proxy": extra["taper_proxy"],
                "species_nfi": extra["species_nfi"],
                "tree_list_basis": extra["tree_list_basis"],
                "tree_list_label": extra["tree_list_label"],
                "measured_tree_list": extra["measured_tree_list"],
                "sort_rule_set": NATIONAL_SORT_SET,
                "expansion_factor": weight,
                "volume_m3_expanded": log.volume_m3 * weight,
                "net_volume_m3_expanded": log.net_volume_m3 * weight,
                "defect_deduction_m3_expanded": (
                    log.defect_deduction_m3 * weight
                ),
                "value_cad_expanded": log.value_cad * weight,
                "model_estimate": True,
                "synthetic": extra["synthetic"],
                "observed": False,
                "policy_grade": False,
                "national_claim": False,
            }
        )
        log_rows.append(row)

    residue_rows: list[dict[str, Any]] = []
    for residue in residues:
        row = residue.to_dict()
        extra = extras[residue.tree_id]
        weight = float(extra["expansion_factor"])
        row.update(
            {
                "taper_status": extra["taper_status"],
                "taper_jurisdiction": extra["taper_jurisdiction"],
                "taper_bec_zone": extra["taper_bec_zone"],
                "taper_proxy": extra["taper_proxy"],
                "species_nfi": extra["species_nfi"],
                "tree_list_basis": extra["tree_list_basis"],
                "tree_list_label": extra["tree_list_label"],
                "measured_tree_list": extra["measured_tree_list"],
                "sort_rule_set": NATIONAL_SORT_SET,
                "expansion_factor": weight,
                "residue_volume_m3_expanded": residue.residue_volume_m3 * weight,
                "residue_odt_expanded": residue.residue_odt * weight,
                "green_tonnes_expanded": residue.green_tonnes * weight,
                "chip_green_tonnes_expanded": (
                    residue.chip_green_tonnes * weight
                ),
                "hog_green_tonnes_expanded": (
                    residue.hog_green_tonnes * weight
                ),
                "recovered_log_m3_expanded": (
                    residue.recovered_log_m3 * weight
                ),
                "taper_total_m3_expanded": residue.taper_total_m3 * weight,
                "model_estimate": True,
                "synthetic": extra["synthetic"],
                "observed": False,
                "policy_grade": False,
                "national_claim": False,
            }
        )
        residue_rows.append(row)

    reconciliation_rows: list[dict[str, Any]] = []
    for residue in residues:
        tree = tree_by_id[residue.tree_id]
        extra = extras[residue.tree_id]
        weight = float(extra["expansion_factor"])
        tree_logs = logs_by_tree.get(residue.tree_id, [])
        products: dict[str, float] = {}
        for log in tree_logs:
            products[log.product] = products.get(log.product, 0.0) + log.volume_m3
        reconciliation_rows.append(
            {
                "tree_id": tree.tree_id,
                "jurisdiction": tree.jurisdiction,
                "species": tree.species,
                "dbh_cm": tree.dbh_cm,
                "height_m": tree.height_m,
                "defect_pct": tree.defect_pct,
                "expansion_factor": weight,
                "taper_total_m3": residue.taper_total_m3,
                "recovered_gross_m3": residue.recovered_log_m3,
                "residue_m3": residue.residue_volume_m3,
                "defect_m3": sum(log.defect_deduction_m3 for log in tree_logs),
                "recovered_net_m3": sum(log.net_volume_m3 for log in tree_logs),
                "n_logs": len(tree_logs),
                "sawlog_m3": products.get("sawlog", 0.0),
                "pulpwood_m3": products.get("pulpwood", 0.0),
                "other_m3": products.get("other", 0.0),
                "value_cad": sum(log.value_cad for log in tree_logs),
                "balance_error_m3": residue.balance_error_m3,
                "recovered_gross_m3_expanded": (
                    residue.recovered_log_m3 * weight
                ),
                "residue_m3_expanded": residue.residue_volume_m3 * weight,
                "taper_total_m3_expanded": residue.taper_total_m3 * weight,
                "residue_odt_expanded": residue.residue_odt * weight,
                "evidence": weakest_evidence(
                    [residue.evidence, *(log.evidence for log in tree_logs)]
                ),
                "taper_evidence": residue.taper_evidence,
                "taper_id": tree.taper_id,
                "taper_status": extra["taper_status"],
                "taper_jurisdiction": extra["taper_jurisdiction"],
                "taper_proxy": extra["taper_proxy"],
                "species_nfi": extra["species_nfi"],
                "tree_list_basis": extra["tree_list_basis"],
                "tree_list_label": extra["tree_list_label"],
                "measured_tree_list": extra["measured_tree_list"],
                "sort_rule_set": NATIONAL_SORT_SET,
                "model_estimate": True,
                "synthetic": extra["synthetic"],
                "observed": False,
                "policy_grade": False,
                "national_claim": False,
            }
        )

    province_rows = _province_summary_v2(trees, logs, residues, extras)
    comparison_rows = _coefficient_comparison_v2(province_rows)
    v1_province_csv = (
        REPO_ROOT
        / "data"
        / "derived"
        / "national"
        / "bucking"
        / "national_bucking_pilot_v1"
        / "province_summary.csv"
    )
    v1_v2_rows = _v1_v2_comparison(province_rows, v1_province_csv)
    v1_province_map = {
        str(row["province"]): row
        for _, row in pd.read_csv(v1_province_csv).iterrows()
    }
    v2_province_map = {str(row["province"]): row for row in province_rows}
    bc_facility_rows = _bc_facility_comparison(
        v1_province_map, v2_province_map, tipsy["summary"]
    )
    reconciliation = _bc_population_reconciliation(
        tipsy["summary"], v2_province_map
    )
    bc_national = v2_province_map["BC"]

    product_totals_expanded: dict[str, float] = {}
    for row in log_rows:
        product = str(row["product"])
        product_totals_expanded[product] = (
            product_totals_expanded.get(product, 0.0)
            + float(row["volume_m3_expanded"])
        )
    totals = {
        "n_trees": len(trees),
        "n_measured_bc_trees": len(bc_trees),
        "n_synthetic_other_trees": len(trees) - len(bc_trees),
        "n_logs": len(logs),
        "taper_total_m3_expanded": sum(
            row["taper_total_m3_expanded"] for row in reconciliation_rows
        ),
        "recovered_gross_m3_expanded": sum(
            row["recovered_gross_m3_expanded"] for row in reconciliation_rows
        ),
        "recovered_net_m3_expanded": sum(
            float(row["recovered_net_m3"]) * float(row["expansion_factor"])
            for row in reconciliation_rows
        ),
        "defect_m3_expanded": sum(
            float(row["defect_m3"]) * float(row["expansion_factor"])
            for row in reconciliation_rows
        ),
        "residue_m3_expanded": sum(
            row["residue_m3_expanded"] for row in reconciliation_rows
        ),
        "residue_odt_expanded": sum(
            row["residue_odt_expanded"] for row in reconciliation_rows
        ),
        "sawlog_m3_expanded": product_totals_expanded.get("sawlog", 0.0),
        "pulpwood_m3_expanded": product_totals_expanded.get("pulpwood", 0.0),
        "other_m3_expanded": product_totals_expanded.get("other", 0.0),
        "max_balance_error_m3": max(
            row["balance_error_m3"] for row in reconciliation_rows
        ),
    }
    totals["balance_error_m3"] = abs(
        totals["recovered_gross_m3_expanded"]
        + totals["residue_m3_expanded"]
        - totals["taper_total_m3_expanded"]
    )
    totals["net_plus_defect_error_m3"] = abs(
        totals["recovered_net_m3_expanded"] + totals["defect_m3_expanded"]
        - totals["recovered_gross_m3_expanded"]
    )
    if totals["balance_error_m3"] > 1e-6:
        raise SchemaError(
            f"pilot v2 volume balance failed: {totals['balance_error_m3']:.12g} m3"
        )

    tree_columns = list(tree_rows[0])
    log_columns = list(log_rows[0])
    residue_columns = list(residue_rows[0])
    reconciliation_columns = list(reconciliation_rows[0])
    province_columns = list(province_rows[0])
    comparison_columns = list(comparison_rows[0])
    v1_v2_columns = list(v1_v2_rows[0])
    bc_facility_columns = list(bc_facility_rows[0])
    reconciliation_metric_columns = list(reconciliation[0])
    tipsy_log_columns = list(tipsy["logs"].columns)
    tipsy_residue_columns = list(tipsy["residue"].columns)
    tipsy_reconciliation_columns = list(tipsy["reconciliation"].columns)
    selection_columns = list(selection.columns)

    _write_csv(output / "tree_list.csv", tree_rows, tree_columns)
    (output / "tree_lists").mkdir(exist_ok=True)
    for province in PROVINCES:
        rows = [row for row in tree_rows if row["jurisdiction"] == province]
        _write_csv(
            output / "tree_lists" / f"{province}.csv", rows, tree_columns
        )
    _write_csv(
        output / "bc_tree_list_selection.csv",
        selection.to_dict(orient="records"),
        selection_columns,
    )
    _write_csv(output / "bucked_logs.csv", log_rows, log_columns)
    _write_csv(output / "residue_ledger.csv", residue_rows, residue_columns)
    _write_csv(
        output / "reconciliation.csv", reconciliation_rows, reconciliation_columns
    )
    _write_csv(
        output / "bc_tipsy_logs.csv",
        tipsy["logs"].to_dict(orient="records"),
        tipsy_log_columns,
    )
    _write_csv(
        output / "bc_tipsy_residue.csv",
        tipsy["residue"].to_dict(orient="records"),
        tipsy_residue_columns,
    )
    _write_csv(
        output / "bc_tipsy_reconciliation.csv",
        tipsy["reconciliation"].to_dict(orient="records"),
        tipsy_reconciliation_columns,
    )
    _write_csv(output / "province_summary.csv", province_rows, province_columns)
    _write_csv(
        output / "coefficient_comparison.csv",
        comparison_rows,
        comparison_columns,
    )
    _write_csv(
        output / "v1_v2_comparison.csv", v1_v2_rows, v1_v2_columns
    )
    _write_csv(
        output / "bc_facility_comparison.csv",
        bc_facility_rows,
        bc_facility_columns,
    )
    _write_csv(
        output / "bc_population_reconciliation.csv",
        reconciliation,
        reconciliation_metric_columns,
    )
    _write_csv(
        output / "taper_dispatch.csv",
        [
            {
                "tree_id": tree.tree_id,
                "jurisdiction": tree.jurisdiction,
                "species": tree.species,
                "species_nfi": extras[tree.tree_id]["species_nfi"],
                "taper_id": tapers[tree.tree_id].taper_id,
                "source_jurisdiction": tapers[tree.tree_id].source_jurisdiction,
                "bec_zone_or_anchor": tapers[tree.tree_id].bec_zone,
                "taper_status": tapers[tree.tree_id].taper_status,
                "taper_proxy": tapers[tree.tree_id].proxy,
                "taper_evidence": tapers[tree.tree_id].evidence,
                "taper_source": tapers[tree.tree_id].source,
                "tree_list_label": extras[tree.tree_id]["tree_list_label"],
            }
            for tree in trees
        ],
        [
            "tree_id",
            "jurisdiction",
            "species",
            "species_nfi",
            "taper_id",
            "source_jurisdiction",
            "bec_zone_or_anchor",
            "taper_status",
            "taper_proxy",
            "taper_evidence",
            "taper_source",
            "tree_list_label",
        ],
    )
    _write_csv(
        output / "sort_rules.csv",
        [asdict(rule) for rule in rules],
        list(asdict(rules[0])),
    )
    (output / "bc_tipsy_sort_rules.yaml").write_text(
        bc_rules_path.read_text(encoding="utf-8"), encoding="utf-8"
    )
    _write_csv(
        output / "bc_tipsy_sort_rules.csv",
        tipsy["rules"],
        list(tipsy["rules"][0]),
    )
    (output / "schema.md").write_text(_schema_markdown(), encoding="utf-8")
    (output / "README.md").write_text(_readme(), encoding="utf-8")

    checks: list[dict[str, Any]] = []

    def add_check(check_id: str, passed: bool, detail: str) -> None:
        checks.append(
            {
                "check_id": check_id,
                "status": "PASS" if passed else "FAIL",
                "detail": detail,
            }
        )

    bc_tree_rows = [row for row in tree_rows if row["jurisdiction"] == "BC"]
    source_ids = set(
        pd.read_csv(BC_SELECTED_TREES_PATH, usecols=["tree_id"], dtype=str)[
            "tree_id"
        ]
    )
    add_check(
        "bc_empirical_provenance",
        all(
            row["tree_list_label"] == BC_MEASURED_LABEL
            and row["measured_tree_list"]
            and not row["synthetic"]
            and not row["model_estimate"]
            and str(row["source_tree_id"]) in source_ids
            for row in bc_tree_rows
        ),
        f"{len(bc_tree_rows)} BC tree rows carry measured FAIB PSP provenance",
    )
    add_check(
        "bc_expansion_preserved",
        math.isclose(
            sum(float(row["expansion_factor"]) for row in bc_tree_rows),
            bc_stats["source_phf_total"],
            rel_tol=1e-12,
            abs_tol=1e-6,
        ),
        f"BC PHF total {bc_stats['source_phf_total']:.6f} preserved",
    )
    province_tree_counts = {
        province: sum(1 for tree in trees if tree.jurisdiction == province)
        for province in PROVINCES
    }
    add_check(
        "thirteen_jurisdictions",
        len({tree.jurisdiction for tree in trees}) == 13,
        f"{len({tree.jurisdiction for tree in trees})} jurisdictions present",
    )
    add_check(
        "bc_empirical_count",
        province_tree_counts["BC"] == len(bc_trees),
        f"BC tree count {province_tree_counts['BC']} equals measured list",
    )
    add_check(
        "other_provinces_unchanged",
        all(
            province_tree_counts[province] == N_TREES_PER_PROVINCE
            for province in PROVINCES
            if province != "BC"
        ),
        "; ".join(
            f"{province}={province_tree_counts[province]}"
            for province in PROVINCES
            if province != "BC"
        ),
    )
    add_check(
        "volume_balance",
        totals["balance_error_m3"] <= 1e-6,
        f"national v2 balance error {totals['balance_error_m3']:.3g} m3",
    )
    add_check(
        "net_defect_balance",
        totals["net_plus_defect_error_m3"] <= 1e-6,
        f"national v2 net+defect error "
        f"{totals['net_plus_defect_error_m3']:.3g} m3",
    )
    add_check(
        "bc_tipsy_conservation",
        all(
            abs(
                float(row["recovered_log_m3"])
                + float(row["residue_m3"])
                - float(row["taper_total_m3"])
            )
            <= CONSERVATION_TOL
            for row in tipsy["reconciliation"].to_dict(orient="records")
        ),
        f"{len(tipsy['reconciliation'])} BC TIPSY stems balance",
    )
    all_numeric_frames = [
        pd.DataFrame(tree_rows),
        pd.DataFrame(log_rows),
        pd.DataFrame(residue_rows),
        pd.DataFrame(reconciliation_rows),
        pd.DataFrame(province_rows),
        pd.DataFrame(comparison_rows),
        pd.DataFrame(v1_v2_rows),
        pd.DataFrame(bc_facility_rows),
        pd.DataFrame(reconciliation),
        tipsy["logs"],
        tipsy["residue"],
        tipsy["reconciliation"],
    ]
    non_negative_frames = [
        pd.DataFrame(tree_rows),
        pd.DataFrame(log_rows),
        pd.DataFrame(residue_rows),
        pd.DataFrame(reconciliation_rows),
        pd.DataFrame(province_rows),
        pd.DataFrame(comparison_rows),
        pd.DataFrame(bc_facility_rows),
        tipsy["logs"],
        tipsy["residue"],
        tipsy["reconciliation"],
    ]
    add_check(
        "no_nan",
        all(
            not frame.select_dtypes(include="number").isna().any().any()
            for frame in all_numeric_frames
        ),
        "numeric columns contain no NaN",
    )
    add_check(
        "no_negative",
        all(
            (frame.select_dtypes(include="number") >= 0.0).all().all()
            for frame in non_negative_frames
        ),
        "numeric columns are non-negative except explicit delta columns",
    )
    add_check(
        "grade_not_assigned",
        all(log.grade == "not_assigned" for log in logs)
        and all(log.grade_status == "not_assigned" for log in logs)
        and (tipsy["logs"]["grade"] == "not_assigned").all()
        and (tipsy["logs"]["grade_status"] == "not_assigned").all(),
        f"{len(logs)} national and {len(tipsy['logs'])} BC TIPSY log rows "
        "grade=not_assigned",
    )
    add_check(
        "taper_evidence",
        all(log.taper_evidence == "published_table" for log in logs)
        and all(
            residue.taper_evidence == "published_table" for residue in residues
        ),
        "all national v2 taper rows are published_table",
    )
    add_check(
        "comparison_rows",
        len(comparison_rows) == len(PROVINCES)
        and len(v1_v2_rows) >= len(PROVINCES) + 2
        and len(bc_facility_rows) == 4
        and len(reconciliation) == 4,
        f"{len(comparison_rows)} facility rows, {len(v1_v2_rows)} v1-v2 rows, "
        f"{len(bc_facility_rows)} BC facility variants, "
        f"{len(reconciliation)} population metrics",
    )
    add_check(
        "labels",
        all(
            row["observed"] is False
            and row["policy_grade"] is False
            and row["national_claim"] is False
            for row in (
                tree_rows
                + log_rows
                + residue_rows
                + reconciliation_rows
                + province_rows
                + comparison_rows
            )
        )
        and all(
            not row["model_estimate"] and not row["synthetic"]
            for row in bc_tree_rows
        )
        and all(
            row["model_estimate"] and row["synthetic"]
            for row in tree_rows
            if row["jurisdiction"] != "BC"
        ),
        "BC tree rows measured; other trees synthetic; observed/policy_grade/"
        "national_claim false",
    )
    add_check(
        "national_claim",
        all(
            row["national_claim"] is False
            for row in tree_rows
            + log_rows
            + residue_rows
            + province_rows
            + comparison_rows
            + v1_v2_rows
            + bc_facility_rows
        ),
        "no national claim on any row",
    )
    add_check(
        "repository_hygiene",
        not deletions,
        "no unexpected D in parent or data git status before run",
    )

    failed = [check for check in checks if check["status"] != "PASS"]
    if failed:
        raise SchemaError(f"pilot v2 checks failed: {failed}")

    elapsed = time.monotonic() - started
    summary = {
        "artifact": "national_bucking_pilot_v2",
        "schema_version": "national_bucking_pilot_v2",
        "scope": "pilot_measured_bc_plus_representative_synthetic_others",
        "national_claim": False,
        "labels": {
            "bc_tree_list": BC_MEASURED_LABEL,
            "other_tree_lists": SYNTHETIC_LABEL,
            "bc_tree_geometry_model_estimate": False,
            "bc_tree_geometry_synthetic": False,
            "bc_product_model_estimate": True,
            "other_tree_geometry_model_estimate": True,
            "other_tree_geometry_synthetic": True,
            "observed": False,
            "policy_grade": False,
            "national_claim": False,
            "ui_or_public_osecan_changed": False,
        },
        "counts": {
            "jurisdictions": len({tree.jurisdiction for tree in trees}),
            "trees": len(trees),
            "measured_bc_trees": len(bc_trees),
            "synthetic_other_trees": len(trees) - len(bc_trees),
            "logs": len(logs),
            "residue_rows": len(residues),
            "reconciliation_rows": len(reconciliation_rows),
            "province_summary_rows": len(province_rows),
            "coefficient_comparison_rows": len(comparison_rows),
            "v1_v2_comparison_rows": len(v1_v2_rows),
            "bc_facility_comparison_rows": len(bc_facility_rows),
            "bc_population_reconciliation_rows": len(reconciliation),
            "bc_tipsy_logs": len(tipsy["logs"]),
            "bc_tipsy_residue_rows": len(tipsy["residue"]),
        },
        "bc_tree_list": {
            **bc_stats,
            "phf_relative_error": _safe_div(
                bc_stats["selected_phf_total"] - bc_stats["source_phf_total"],
                bc_stats["source_phf_total"],
            ),
        },
        "totals": totals,
        "bc_tipsy": tipsy["summary"],
        "bc_national": dict(bc_national),
        "province_summary": province_rows,
        "taper_coverage": {
            "local_jurisdictions": sorted(
                {
                    tree.jurisdiction
                    for tree in trees
                    if extras[tree.tree_id]["taper_status"] == "local_published"
                }
            ),
            "donor_jurisdictions": sorted(
                {
                    tree.jurisdiction
                    for tree in trees
                    if extras[tree.tree_id]["taper_status"] == "donor_published"
                }
            ),
            "n_local_trees": sum(
                1
                for tree in trees
                if extras[tree.tree_id]["taper_status"] == "local_published"
            ),
            "n_donor_trees": sum(
                1
                for tree in trees
                if extras[tree.tree_id]["taper_status"] == "donor_published"
            ),
            "taper_ids": sorted({taper.taper_id for taper in tapers.values()}),
        },
        "evidence": {
            "bc_tree_tiers": ["published_table"],
            "other_tree_tiers": ["assumption"],
            "log_taper_tiers": ["published_table"],
            "log_row_tiers": sorted({log.evidence for log in logs}),
            "residue_row_tiers": sorted(
                {residue.evidence for residue in residues}
            ),
            "taper_status_values": sorted(
                {extras[tree.tree_id]["taper_status"] for tree in trees}
            ),
        },
        "checks": checks,
        "comparison": {
            "v1_v2": v1_v2_rows,
            "bc_facility": bc_facility_rows,
            "bc_population_reconciliation": reconciliation,
        },
        "limitations": [
            (
                "The BC empirical list is a post-stratified measured aggregate, "
                "not the full PSP tree list and not an inventory expansion."
            ),
            (
                "The other 12 jurisdictions remain representative synthetic "
                "lists from pilot v1."
            ),
            (
                "Absolute volumes are not comparable between expansion-weighted "
                "BC and unit-weight synthetic provinces."
            ),
            (
                "BC TIPSY dimension sorts and national pilot sorts are "
                "different rule sets; both are reported."
            ),
            (
                "Dimension-only pilot sorts are not official federal or "
                "provincial log grades; grade=not_assigned."
            ),
            (
                "Prices, density, moisture, chip and hog yields are pilot "
                "assumptions."
            ),
            (
                "Facility coefficient comparison is screening only; harvest "
                "residue and facility process residue are different streams."
            ),
            (
                "No national, operational, policy-grade, AAC, or investment "
                "claim is made."
            ),
            "No UI or public OSeCAN surface was changed.",
        ],
        "elapsed_seconds": elapsed,
        "created_utc": datetime.now(UTC).isoformat(timespec="seconds"),
    }

    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, default=_json_default) + "\n",
        encoding="utf-8",
    )
    (output / "report.md").write_text(
        _report_markdown(
            summary,
            province_rows,
            comparison_rows,
            bc_facility_rows,
            v1_v2_rows,
            reconciliation,
        ),
        encoding="utf-8",
    )

    output_files = [
        "README.md",
        "schema.md",
        "tree_list.csv",
        "bc_tree_list_selection.csv",
        "bucked_logs.csv",
        "residue_ledger.csv",
        "reconciliation.csv",
        "bc_tipsy_logs.csv",
        "bc_tipsy_residue.csv",
        "bc_tipsy_reconciliation.csv",
        "province_summary.csv",
        "coefficient_comparison.csv",
        "v1_v2_comparison.csv",
        "bc_facility_comparison.csv",
        "bc_population_reconciliation.csv",
        "summary.json",
        "report.md",
        "taper_dispatch.csv",
        "sort_rules.csv",
        "bc_tipsy_sort_rules.yaml",
        "bc_tipsy_sort_rules.csv",
    ] + [f"tree_lists/{province}.csv" for province in PROVINCES]
    manifest = {
        "artifact": "national_bucking_pilot_v2",
        "schema_version": "national_bucking_pilot_v2",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "scope": "pilot_measured_bc_plus_representative_synthetic_others",
        "national_claim": False,
        "labels": summary["labels"],
        "command": (
            ".venv/bin/python -m workflows.national.bucking."
            "national_bucking_pilot_v2 --out-dir "
            "data/derived/national/bucking/national_bucking_pilot_v2"
        ),
        "config": {
            "provinces": list(PROVINCES),
            "other_trees_per_province": N_TREES_PER_PROVINCE,
            "bc_source": "bc_psp_validation_v0/selected_trees.csv",
            "bc_dbh_breaks_cm": list(BC_DBH_BREAKS_CM),
            "bc_selection": (
                "one PHF-weighted median measured stem per species x BEC zone "
                "x 2.5 cm DBH stratum; expansion = stratum PHF"
            ),
            "bc_sort_rule_set_tipsy": TIPSY_SORT_SET,
            "national_sort_rule_set": NATIONAL_SORT_SET,
            "stump_height_m": STUMP_HEIGHT_M,
            "moisture_wet_basis": MOISTURE_WET_BASIS,
            "chip_yield_fraction": CHIP_YIELD_FRACTION,
            "hog_yield_fraction": HOG_YIELD_FRACTION,
            "price_deck_id": PRICE_DECK_ID,
            "basic_density_odt_m3": BASIC_DENSITY_ODT_M3,
            "donor_policy": DONOR_POLICY,
            "bec_anchor": BEC_ANCHOR,
            "bucker": (
                "value-maximising dynamic program over national pilot target "
                "lengths; BC TIPSY uses the reference nemora sorted bucker"
            ),
        },
        "inputs": {
            "bc_psp_selected_trees": {
                "path": str(BC_SELECTED_TREES_PATH.relative_to(REPO_ROOT)),
                "sha256": _sha256(BC_SELECTED_TREES_PATH),
            },
            "bc_psp_tree_ledger": {
                "path": str(BC_TREE_LEDGER_PATH.relative_to(REPO_ROOT)),
                "sha256": _sha256(BC_TREE_LEDGER_PATH),
            },
            "bc_psp_validation_summary": {
                "path": str(BC_VALIDATION_SUMMARY_PATH.relative_to(REPO_ROOT)),
                "sha256": _sha256(BC_VALIDATION_SUMMARY_PATH),
            },
            "bc_tipsy_sort_rules_snapshot": {
                "path": _relative_to_repo(output / "bc_tipsy_sort_rules.yaml"),
                "sha256": _sha256(output / "bc_tipsy_sort_rules.yaml"),
                "source_path": _relative_to_repo(bc_rules_path),
                "source_sha256": _sha256(bc_rules_path),
                "note": (
                    "The exact BC TIPSY rule file used by this run is copied "
                    "into the artifact; the source path is recorded separately "
                    "because the live rule set is versioned independently."
                ),
            },
            "bc_basic_density": {
                "path": str(BC_DENSITY_PATH.relative_to(REPO_ROOT)),
                "sha256": _sha256(BC_DENSITY_PATH),
            },
            "bc_price_profile": {
                "path": f"nemora/{BC_PRICE_PROFILE_PATH.relative_to(default_nemora_root())}",
                "sha256": _sha256(BC_PRICE_PROFILE_PATH),
            },
            "national_sort_rules": {
                "path": str(SORT_RULES_PATH.relative_to(REPO_ROOT)),
                "sha256": _sha256(SORT_RULES_PATH),
            },
            "facility_product_chain": {
                "path": str(FACILITY_CHAIN_PATH.relative_to(REPO_ROOT)),
                "sha256": _sha256(FACILITY_CHAIN_PATH),
            },
            "nfi_coverage": {
                "path": str(NFI_COVERAGE_PATH.relative_to(REPO_ROOT)),
                "sha256": _sha256(NFI_COVERAGE_PATH),
            },
            "nfi_height_models": {
                "path": str(NFI_HEIGHT_PATH.relative_to(REPO_ROOT)),
                "sha256": _sha256(NFI_HEIGHT_PATH),
            },
            "qc_taper_registry": {
                "path": str(QC_TAPER_PATH.relative_to(REPO_ROOT)),
                "sha256": _sha256(QC_TAPER_PATH),
            },
            "kozak_parameters": {
                "path": f"nemora/{kozak_path.relative_to(default_nemora_root())}",
                "sha256": _sha256(kozak_path),
            },
            "pilot_v1_province_summary": {
                "path": str(v1_province_csv.relative_to(REPO_ROOT)),
                "sha256": _sha256(v1_province_csv),
            },
        },
        "code": [
            "workflows/national/bucking/national_bucking_pilot_v2.py",
            "workflows/national/bucking/national_bucking_pilot_v1.py",
            "workflows/national/bucking/pilot_schema.py",
            "workflows/national/bucking/schneider2013.py",
            "workflows/national/bucking/taper.py",
            "workflows/national/bucking/bucking.py",
            "workflows/national/bucking/schema.py",
            "workflows/national/bucking/rules/national_pilot_sorts.csv",
            "workflows/national/bucking/rules/bc_interior.yaml",
            "workflows/national/bucking/rules/bc_basic_density.csv",
            "workflows/national/bucking/rules/qc_schneider2013_taper.csv",
        ],
        "counts": summary["counts"],
        "totals": totals,
        "checks": checks,
        "limitations": summary["limitations"],
        "outputs": [
            {
                "path": name,
                "sha256": _sha256(output / name),
                "bytes": (output / name).stat().st_size,
            }
            for name in output_files
        ],
        "evidence_tiers": list(EVIDENCE_TIERS),
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True, default=_json_default) + "\n",
        encoding="utf-8",
    )
    return manifest


def _json_default(value: Any) -> Any:
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--bc-rules",
        type=Path,
        default=BC_RULES_PATH,
        help="BC interior TIPSY rule file; the exact file is snapshotted.",
    )
    args = parser.parse_args(argv)
    manifest = generate_pilot(args.out_dir, bc_rules_path=args.bc_rules)
    totals = manifest["totals"]
    print(
        f"pilot v2 wrote {args.out_dir} | "
        f"trees={manifest['counts']['trees']} "
        f"(measured BC={manifest['counts']['measured_bc_trees']}) "
        f"logs={manifest['counts']['logs']} "
        f"recovered={totals['recovered_gross_m3_expanded']:.3f} m3 "
        f"residue={totals['residue_m3_expanded']:.3f} m3 "
        f"balance_error={totals['balance_error_m3']:.3g} m3"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
