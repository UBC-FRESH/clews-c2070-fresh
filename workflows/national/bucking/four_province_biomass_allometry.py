"""Branch and foliage allometry for the measured four-province tree list.

The stem taper ledger stops at the stem.  This module adds live branch and live
foliage biomass to BC, QC, NB and YT measured stems using the published CFS
tree-level DBH + height equations (Lambert, Ung and Raulier 2005; Ung, Bernier
and Guo 2008).  Coefficients are vendored unmodified under
``rules/cfs_biomass`` and every lookup is explicit:

* species + NFI code exact match,
* species code exact match,
* no silent group fallback; an unmapped species halts the build.

BC mappings are the published ``bc_biomass_allometry_map.csv`` rows repeated in
the four-province map so one artifact covers all four provinces.  The two
mapping files are cross-checked by the tests.  Where the CFS table has no
species equation (mountain hemlock) or the measured code is an aggregate
(``BETU.ALL``), the row is labelled ``proxy=true`` with the published group or
species proxy in ``proxy_reason``.

The output is a model estimate, not an observation.  Branch and foliage mass is
never added to stem taper volume; it is carried as a separate oven-dry mass
component with gross and 0.35/0.60/0.75-recoverable scenario values.  No single
recovery factor is adopted.
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
    "COMPONENT_COLUMNS",
    "DEFAULT_RECOVERY_FACTORS",
    "FOUR_PROVINCE_MAP_PATH",
    "AllometryLookup",
    "annotate_component_row",
    "compute_measured_components",
    "load_four_province_biomass_allometry",
    "scenario_totals",
    "tree_components_for_measured_trees",
]

REPO_ROOT = Path(__file__).resolve().parents[3]
RULES_DIR = REPO_ROOT / "workflows" / "national" / "bucking" / "rules"
FOUR_PROVINCE_MAP_PATH = RULES_DIR / "four_province_biomass_allometry_map.csv"
CFS_DBH_HEIGHT_PATH = _bc.CFS_DBH_HEIGHT_PATH
PROVINCES: tuple[str, ...] = ("BC", "QC", "NB", "YT")
DEFAULT_RECOVERY_FACTORS: tuple[float, ...] = (0.35, 0.60, 0.75)
REFERENCE_RECOVERY_FACTOR = 0.60

COMPONENT_COLUMNS: tuple[str, ...] = (
    "stem_residue_odt",
    "branch_odt",
    "foliage_odt",
    "branch_foliage_odt",
    "total_residue_odt",
    "recoverable_residue_odt_r035",
    "recoverable_residue_odt_r060",
    "recoverable_residue_odt_r075",
    "recovery_factor_reference",
    "recovery_scenario_basis",
    "branch_foliage_estimated",
    "branch_foliage_evidence",
    "branch_foliage_equation_id",
    "branch_foliage_equation_species",
    "branch_foliage_reference",
    "branch_foliage_species_specific",
    "branch_foliage_proxy",
    "branch_foliage_proxy_reason",
    "branch_foliage_source",
    "branch_foliage_source_licence",
    "allometry_map_jurisdiction",
    "allometry_species_nfi",
    "residue_component_scope",
    "stem_residue_odt_expanded",
    "branch_odt_expanded",
    "foliage_odt_expanded",
    "branch_foliage_odt_expanded",
    "total_residue_odt_expanded",
    "recoverable_residue_odt_r035_expanded",
    "recoverable_residue_odt_r060_expanded",
    "recoverable_residue_odt_r075_expanded",
)


@dataclass(frozen=True, slots=True)
class AllometryLookup:
    """Trusted exact lookups parsed once at the artifact boundary."""

    by_nfi: Mapping[tuple[str, str], Mapping[str, _bc.BiomassEquation]]
    by_code: Mapping[tuple[str, str], Mapping[str, _bc.BiomassEquation]]
    ambiguous_codes: frozenset[tuple[str, str]] = frozenset()

    def resolve(
        self,
        jurisdiction: str,
        species: str,
        species_nfi: str | None,
    ) -> Mapping[str, _bc.BiomassEquation]:
        province = _text("jurisdiction", jurisdiction).upper()
        code = _text("species", species).upper()
        nfi = _text("species_nfi", species_nfi or code).upper()
        equations = self.by_nfi.get((province, nfi))
        if equations is not None:
            return equations
        if (province, code) in self.ambiguous_codes:
            raise SchemaError(
                f"{province}/{code} has multiple CFS equations; NFI code "
                f"{nfi!r} is required and did not match a mapped variant"
            )
        equations = self.by_code.get((province, code))
        if equations is not None:
            return equations
        raise SchemaError(
            "no CFS branch/foliage equation mapping for "
            f"{province}/{code} (NFI {nfi}); refusing a silent fallback"
        )


def _text(name: str, value: Any) -> str:
    text = "" if value is None else str(value).strip()
    if not text:
        raise SchemaError(f"{name} is required")
    return text


def _read_four_province_map(path: Path) -> list[dict[str, Any]]:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"four-province allometry map not found: {path}")
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    required = {
        "jurisdiction",
        "species_code",
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
        province = _text("jurisdiction", record["jurisdiction"]).upper()
        if province not in PROVINCES:
            raise SchemaError(f"unexpected allometry jurisdiction: {province!r}")
        code = _text("species_code", record["species_code"]).upper()
        nfi = _text("species_nfi", record["species_nfi"]).upper()
        rows.append(
            {
                "jurisdiction": province,
                "species_code": code,
                "species_nfi": nfi,
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
    if {row["jurisdiction"] for row in rows} != set(PROVINCES):
        raise SchemaError(
            "allometry map does not cover exactly BC, QC, NB and YT"
        )
    return rows


def load_four_province_biomass_allometry(
    map_path: Path = FOUR_PROVINCE_MAP_PATH,
    source_path: Path = CFS_DBH_HEIGHT_PATH,
) -> AllometryLookup:
    """Parse map rows and CFS coefficients into validated exact lookups."""

    coefficients = _bc._read_cfs_equations(source_path)
    rows = _read_four_province_map(map_path)
    by_nfi: dict[tuple[str, str], dict[str, _bc.BiomassEquation]] = {}
    by_code: dict[tuple[str, str], dict[str, _bc.BiomassEquation]] = {}
    ambiguous: set[tuple[str, str]] = set()
    for row in rows:
        equations: dict[str, _bc.BiomassEquation] = {}
        for component in ("branch", "foliage"):
            source_component = "branches" if component == "branch" else "foliage"
            key = (row["equation_species"], source_component)
            if key not in coefficients:
                raise SchemaError(
                    f"CFS table has no {source_component} equation for "
                    f"{row['equation_species']!r}"
                )
            values = coefficients[key]
            equations[component] = _bc.BiomassEquation(
                bc_species_code=(
                    f"{row['jurisdiction']}:{row['species_code']}:"
                    f"{row['species_nfi']}"
                ),
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
        nfi_key = (row["jurisdiction"], row["species_nfi"])
        code_key = (row["jurisdiction"], row["species_code"])
        existing = by_nfi.get(nfi_key)
        if existing is not None and (
            existing["branch"].equation_id != equations["branch"].equation_id
            or existing["foliage"].equation_id != equations["foliage"].equation_id
        ):
            raise SchemaError(
                f"conflicting NFI mapping for {nfi_key}: "
                f"{existing['branch'].equation_id} vs "
                f"{equations['branch'].equation_id}"
            )
        by_nfi[nfi_key] = equations
        code_existing = by_code.get(code_key)
        if code_existing is None:
            by_code[code_key] = equations
        elif (
            code_existing["branch"].equation_id
            != equations["branch"].equation_id
            or code_existing["foliage"].equation_id
            != equations["foliage"].equation_id
        ):
            # A species code such as PET maps to more than one NFI species.
            # Keep the NFI lookup authoritative and fail closed if the NFI
            # code is missing or unexpected.
            by_code.pop(code_key, None)
            ambiguous.add(code_key)
    return AllometryLookup(
        by_nfi=by_nfi,
        by_code=by_code,
        ambiguous_codes=frozenset(ambiguous),
    )


def compute_measured_components(
    *,
    tree_id: str,
    jurisdiction: str,
    species: str,
    species_nfi: str | None,
    species_group: str,
    dbh_cm: float,
    height_m: float,
    lookup: AllometryLookup,
) -> _bc.BiomassComponents:
    """Compute one measured stem's branch and foliage ODT."""

    equations = lookup.resolve(jurisdiction, species, species_nfi)
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


def tree_components_for_measured_trees(
    trees: Iterable[Any],
    lookup: AllometryLookup,
    *,
    species_nfi_by_tree: Mapping[str, str],
) -> dict[str, _bc.BiomassComponents]:
    """Compute components for every supplied measured tree, failing closed."""

    components: dict[str, _bc.BiomassComponents] = {}
    for tree in trees:
        province = str(tree.jurisdiction).upper()
        if province not in PROVINCES:
            raise SchemaError(
                f"measured-tree allometry received non-pilot province {province!r}"
            )
        if tree.tree_id in components:
            raise SchemaError(f"duplicate measured tree_id: {tree.tree_id}")
        species_nfi = species_nfi_by_tree.get(tree.tree_id, tree.species)
        components[tree.tree_id] = compute_measured_components(
            tree_id=tree.tree_id,
            jurisdiction=province,
            species=tree.species,
            species_nfi=species_nfi,
            species_group=tree.species_group,
            dbh_cm=tree.dbh_cm,
            height_m=tree.height_m,
            lookup=lookup,
        )
    if not components:
        raise SchemaError("no measured trees supplied to branch/foliage allometry")
    return components


def _factor_field(factor: float) -> str:
    return f"recoverable_residue_odt_r{round(factor * 100):03d}"


def annotate_component_row(
    row: Mapping[str, Any],
    components: _bc.BiomassComponents,
    *,
    weight: float,
    stem_residue_odt: float,
    jurisdiction: str,
    species_nfi: str,
) -> dict[str, Any]:
    """Return a residue row carrying total mass and three recovery scenarios."""

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
    for factor in DEFAULT_RECOVERY_FACTORS:
        field = _factor_field(factor)
        annotated[field] = total * factor
        annotated[f"{field}_expanded"] = total * factor * weight
    return annotated


def scenario_totals(
    rows: Iterable[Mapping[str, Any]],
) -> dict[str, float]:
    """Aggregate gross and scenario-recoverable residue from component rows."""

    totals = {
        "stem_residue_odt_expanded": 0.0,
        "branch_odt_expanded": 0.0,
        "foliage_odt_expanded": 0.0,
        "branch_foliage_odt_expanded": 0.0,
        "total_residue_odt_expanded": 0.0,
        "recovered_log_m3_expanded": 0.0,
    }
    for factor in DEFAULT_RECOVERY_FACTORS:
        totals[_factor_field(factor) + "_expanded"] = 0.0
    count = 0
    for row in rows:
        count += 1
        for field in totals:
            value = row.get(field)
            if value is None:
                raise SchemaError(f"component row is missing {field}")
            number = _bc._finite(field, value)
            if number < 0.0:
                raise SchemaError(f"{field} must be non-negative")
            totals[field] += number
    if not count:
        raise SchemaError("component scenario totals received no rows")
    if not math.isclose(
        totals["branch_foliage_odt_expanded"],
        totals["branch_odt_expanded"] + totals["foliage_odt_expanded"],
        rel_tol=1e-9,
        abs_tol=1e-6,
    ):
        raise SchemaError("branch plus foliage conservation failed")
    if not math.isclose(
        totals["total_residue_odt_expanded"],
        totals["stem_residue_odt_expanded"]
        + totals["branch_foliage_odt_expanded"],
        rel_tol=1e-9,
        abs_tol=1e-6,
    ):
        raise SchemaError("total residue conservation failed")
    for factor in DEFAULT_RECOVERY_FACTORS:
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
