"""Branch and foliage allometry for the nine E2 imputed jurisdictions.

The E2 tree list is a SCANFI composition and measured BC/QC/NB/YT donor
DBH/height imputation.  This module adds live branch and live foliage biomass
to every E2 stem with the same published CFS tree-level DBH + height equations
used by the measured four-province pilot (Lambert, Ung and Raulier 2005; Ung,
Bernier and Guo 2008; Open Government Licence - Canada).

Mapping is by NFI species so the SCANFI aggregates are explicit:

* exact species equations for balsam fir, tamarack, black spruce, jack pine
  and lodgepole pine;
* published deciduous and conifer group equations for the SCANFI
  ``BROADLEAF`` and ``CONIFEROUS`` aggregates;
* eastern white pine for the ``PINU.STR`` white/red pine aggregate, labelled
  as a proxy because the aggregate may contain red pine.

Every row is ``imputed=true``, ``measured=false`` and carries its equation,
proxy label and source.  The output is a model estimate, not an observation.
Branch and foliage dry mass is never added to stem taper volume.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from . import bc_biomass_allometry as _bc
from .pilot_schema import SchemaError

__all__ = [
    "E2_MAP_PATH",
    "E2_PROVINCES",
    "AllometryLookup",
    "annotate_residue_row",
    "compute_e2_components",
    "load_e2_biomass_allometry",
    "scenario_totals",
]

REPO_ROOT = Path(__file__).resolve().parents[3]
RULES_DIR = REPO_ROOT / "workflows" / "national" / "bucking" / "rules"
E2_MAP_PATH = RULES_DIR / "e2_biomass_allometry_map.csv"
CFS_DBH_HEIGHT_PATH = _bc.CFS_DBH_HEIGHT_PATH

E2_PROVINCES: tuple[str, ...] = (
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
EVIDENCE_TIER = "published_table"
RECOVERY_SCENARIOS: tuple[float, ...] = (0.35, 0.60, 0.75)
REFERENCE_RECOVERY_FACTOR = 0.60


@dataclass(frozen=True, slots=True)
class AllometryLookup:
    """Trusted NFI-species lookup parsed once at the artifact boundary."""

    by_nfi: Mapping[str, Mapping[str, _bc.BiomassEquation]]

    def resolve(self, species_nfi: str) -> Mapping[str, _bc.BiomassEquation]:
        nfi = _text("species_nfi", species_nfi).upper()
        equations = self.by_nfi.get(nfi)
        if equations is None:
            raise SchemaError(
                f"no E2 CFS branch/foliage equation mapping for NFI species {nfi!r}"
            )
        return equations


def _text(name: str, value: Any) -> str:
    text = "" if value is None else str(value).strip()
    if not text:
        raise SchemaError(f"{name} is required")
    return text


def _read_e2_map(path: Path) -> list[dict[str, Any]]:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"E2 allometry map not found: {path}")
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    required = {
        "species_nfi",
        "common_name",
        "species_group",
        "equation_species",
        "species_specific",
        "proxy",
        "proxy_reason",
        "dbh_min_cm",
        "dbh_max_cm",
        "height_min_m",
        "height_max_m",
        "domain_basis",
    }
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise SchemaError(f"{path.name} is missing columns: {', '.join(missing)}")
    rows: list[dict[str, Any]] = []
    for record in frame.to_dict(orient="records"):
        rows.append(
            {
                "species_nfi": _text("species_nfi", record["species_nfi"]).upper(),
                "common_name": _text("common_name", record["common_name"]),
                "species_group": _text(
                    "species_group", record["species_group"]
                ).lower(),
                "equation_species": _text(
                    "equation_species", record["equation_species"]
                ),
                "species_specific": _bc._parse_bool(
                    "species_specific", record["species_specific"]
                ),
                "proxy": _bc._parse_bool("proxy", record["proxy"]),
                "proxy_reason": str(record["proxy_reason"]).strip() or "none",
                "dbh_min_cm": _bc._positive("dbh_min_cm", record["dbh_min_cm"]),
                "dbh_max_cm": _bc._positive("dbh_max_cm", record["dbh_max_cm"]),
                "height_min_m": _bc._positive(
                    "height_min_m", record["height_min_m"]
                ),
                "height_max_m": _bc._positive(
                    "height_max_m", record["height_max_m"]
                ),
                "domain_basis": _text("domain_basis", record["domain_basis"]),
            }
        )
    if not rows:
        raise SchemaError(f"{path.name} contains no mappings")
    return rows


def load_e2_biomass_allometry(
    map_path: Path = E2_MAP_PATH,
    source_path: Path = CFS_DBH_HEIGHT_PATH,
) -> AllometryLookup:
    """Parse E2 NFI mappings and CFS coefficients into validated equations."""

    coefficients = _bc._read_cfs_equations(source_path)
    by_nfi: dict[str, dict[str, _bc.BiomassEquation]] = {}
    for row in _read_e2_map(map_path):
        equations: dict[str, _bc.BiomassEquation] = {}
        for component in ("branch", "foliage"):
            source_component = "branches" if component == "branch" else "foliage"
            key = (row["equation_species"], source_component)
            values = coefficients.get(key)
            if values is None:
                raise SchemaError(
                    f"CFS table has no {source_component} equation for "
                    f"{row['equation_species']!r}"
                )
            equations[component] = _bc.BiomassEquation(
                bc_species_code=f"E2:{row['species_nfi']}",
                common_name=row["common_name"],
                species_group=row["species_group"],
                equation_species=row["equation_species"],
                component=component,
                a=values["a"],
                b=values["b"],
                c=values["c"],
                reference=values["reference"],
                species_specific=row["species_specific"],
                proxy=row["proxy"],
                proxy_reason=row["proxy_reason"],
                dbh_min_cm=row["dbh_min_cm"],
                dbh_max_cm=row["dbh_max_cm"],
                height_min_m=row["height_min_m"],
                height_max_m=row["height_max_m"],
                domain_basis=row["domain_basis"],
            )
        existing = by_nfi.get(row["species_nfi"])
        if existing is not None:
            raise SchemaError(
                f"duplicate E2 allometry mapping for {row['species_nfi']!r}"
            )
        by_nfi[row["species_nfi"]] = equations
    if not by_nfi:
        raise SchemaError("E2 allometry map contains no species")
    return AllometryLookup(by_nfi=by_nfi)


def compute_e2_components(
    *,
    tree_id: str,
    species: str,
    species_nfi: str,
    species_group: str,
    dbh_cm: float,
    height_m: float,
    lookup: AllometryLookup,
) -> _bc.BiomassComponents:
    """Compute one E2 imputed stem's branch and foliage ODT."""

    equations = lookup.resolve(species_nfi)
    branch = equations["branch"].predict_odt(dbh_cm, height_m)
    foliage = equations["foliage"].predict_odt(dbh_cm, height_m)
    return _bc.BiomassComponents(
        tree_id=tree_id,
        species=species,
        species_group=species_group,
        dbh_cm=dbh_cm,
        height_m=height_m,
        branch_odt=branch,
        foliage_odt=foliage,
        branch_foliage_odt=branch + foliage,
        branch_equation=equations["branch"],
        foliage_equation=equations["foliage"],
    )


def _factor_field(factor: float) -> str:
    return f"recoverable_residue_odt_r{round(factor * 100):03d}"


def annotate_residue_row(
    row: Mapping[str, Any],
    components: _bc.BiomassComponents,
    *,
    weight: float,
    stem_residue_odt: float,
    jurisdiction: str,
    species_nfi: str,
) -> dict[str, Any]:
    """Return an E2 residue row with total mass and three recovery scenarios."""

    weight = _bc._positive("weight", weight)
    stem = _bc._positive("stem_residue_odt", stem_residue_odt)
    province = _text("jurisdiction", jurisdiction).upper()
    nfi = _text("species_nfi", species_nfi).upper()
    total = stem + components.branch_foliage_odt
    annotated = dict(row)
    annotated.update(
        {
            "stem_residue_odt": stem,
            "branch_odt": components.branch_odt,
            "foliage_odt": components.foliage_odt,
            "branch_foliage_odt": components.branch_foliage_odt,
            "total_residue_odt": total,
            "recovery_factor_reference": REFERENCE_RECOVERY_FACTOR,
            "recovery_scenario_basis": (
                "0.35|0.60|0.75 scenarios; no single calibrated factor"
            ),
            "branch_foliage_estimated": True,
            "branch_foliage_evidence": components.evidence,
            "branch_foliage_equation_id": components.equation_id,
            "branch_foliage_equation_species": components.equation_species,
            "branch_foliage_reference": components.reference,
            "branch_foliage_species_specific": components.species_specific,
            "branch_foliage_proxy": components.proxy,
            "branch_foliage_proxy_reason": components.branch_equation.proxy_reason,
            "branch_foliage_source": components.branch_equation.source_dataset,
            "branch_foliage_source_licence": (
                components.branch_equation.source_licence
            ),
            "allometry_map_jurisdiction": province,
            "allometry_species_nfi": nfi,
            "residue_component_scope": "stem_plus_branch_foliage",
            "stem_residue_odt_expanded": stem * weight,
            "branch_odt_expanded": components.branch_odt * weight,
            "foliage_odt_expanded": components.foliage_odt * weight,
            "branch_foliage_odt_expanded": components.branch_foliage_odt * weight,
            "total_residue_odt_expanded": total * weight,
        }
    )
    for factor in RECOVERY_SCENARIOS:
        field = _factor_field(factor)
        annotated[field] = total * factor
        annotated[f"{field}_expanded"] = total * factor * weight
    return annotated


def scenario_totals(
    rows: Iterable[Mapping[str, Any]],
) -> dict[str, float]:
    """Aggregate gross and scenario-recoverable E2 residue with conservation checks."""

    totals = {
        "stem_residue_odt_expanded": 0.0,
        "branch_odt_expanded": 0.0,
        "foliage_odt_expanded": 0.0,
        "branch_foliage_odt_expanded": 0.0,
        "total_residue_odt_expanded": 0.0,
    }
    for factor in RECOVERY_SCENARIOS:
        totals[_factor_field(factor) + "_expanded"] = 0.0
    count = 0
    for row in rows:
        count += 1
        for field in totals:
            value = row.get(field)
            if value is None:
                raise SchemaError(f"E2 component row is missing {field}")
            number = _bc._finite(field, value)
            if number < 0.0:
                raise SchemaError(f"{field} must be non-negative")
            totals[field] += number
    if not count:
        raise SchemaError("E2 component scenario totals received no rows")
    if not math.isclose(
        totals["branch_foliage_odt_expanded"],
        totals["branch_odt_expanded"] + totals["foliage_odt_expanded"],
        rel_tol=1e-9,
        abs_tol=1e-6,
    ):
        raise SchemaError("E2 branch plus foliage conservation failed")
    if not math.isclose(
        totals["total_residue_odt_expanded"],
        totals["stem_residue_odt_expanded"]
        + totals["branch_foliage_odt_expanded"],
        rel_tol=1e-9,
        abs_tol=1e-6,
    ):
        raise SchemaError("E2 total residue conservation failed")
    for factor in RECOVERY_SCENARIOS:
        field = _factor_field(factor) + "_expanded"
        if not math.isclose(
            totals[field],
            totals["total_residue_odt_expanded"] * factor,
            rel_tol=1e-9,
            abs_tol=1e-6,
        ):
            raise SchemaError(f"{field} does not equal gross * {factor}")
    totals["n_trees"] = float(count)
    return totals
