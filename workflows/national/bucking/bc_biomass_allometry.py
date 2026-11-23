"""BC branch and foliage biomass allometry for measured-tree residue accounting.

The stem taper ledger in ``national_bucking_pilot_v2`` stops at the stem.  This
module adds the two aboveground components that the taper cannot represent:
live branches and live foliage.  It consumes the public CFS tree-level
allometric coefficient table and applies the DBH + height equation

``Biomass_kg = a * DBH_cm**b * Height_m**c``

to measured FAIB PSP stems.

Provenance
----------
* Coefficients: CFS Biomass and Nutrients Calculator, tree-level DBH + height
  table, Open Government Licence - Canada.
* Equations: Lambert, Ung and Raulier (2005); Ung, Bernier and Guo (2008).
* The unmodified source tables and their SHA-256 checksums are vendored under
  ``workflows/national/bucking/rules/cfs_biomass/``.

The output is oven-dry tonnes (ODT) per stem.  It is a model estimate, not an
observation, and it is never added to the stem taper volume.  Branch and
foliage biomass is a separate dry-mass component.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pandas as pd

from .pilot_schema import SchemaError
from .validate_tsa24_btc import DEFAULT_RECOVERY_FACTOR

__all__ = [
    "ALLOMETRY_MAP_PATH",
    "BRANCH_FOLIAGE_COLUMNS",
    "CFS_BIOMASS_DIR",
    "CFS_DBH_HEIGHT_PATH",
    "CFS_DBH_PATH",
    "DEFAULT_ALLOMETRY_RECOVERY_FACTOR",
    "EVIDENCE_TIER",
    "BiomassComponents",
    "BiomassEquation",
    "add_bc_component_fields",
    "add_not_estimated_component_fields",
    "compute_biomass_components",
    "load_bc_biomass_allometry",
    "tree_components_for_bc",
]

REPO_ROOT = Path(__file__).resolve().parents[3]
RULES_DIR = REPO_ROOT / "workflows" / "national" / "bucking" / "rules"
CFS_BIOMASS_DIR = RULES_DIR / "cfs_biomass"
CFS_DBH_HEIGHT_PATH = (
    CFS_BIOMASS_DIR / "tree_level_allometric_equations_dbh_height.csv"
)
CFS_DBH_PATH = CFS_BIOMASS_DIR / "tree_level_allometric_equations_dbh.csv"
ALLOMETRY_MAP_PATH = RULES_DIR / "bc_biomass_allometry_map.csv"

EVIDENCE_TIER = "published_table"
DEFAULT_ALLOMETRY_RECOVERY_FACTOR = DEFAULT_RECOVERY_FACTOR
COMPONENTS: tuple[str, ...] = ("branch", "foliage")
GROUP_FALLBACKS: Mapping[str, str] = MappingProxyType(
    {
        "conifer": "__CONIFER__",
        "conifers": "__CONIFER__",
        "deciduous": "__DECIDUOUS__",
        "hardwood": "__DECIDUOUS__",
        "hardwoods": "__DECIDUOUS__",
    }
)

#: Columns added to each residue ledger row.  A BC measured stem carries all
#: values; a non-BC synthetic row carries ``branch_foliage_estimated=False`` and
#: leaves the branch/foliage/total fields empty so an unestimated component is
#: never presented as zero biomass.
BRANCH_FOLIAGE_COLUMNS: tuple[str, ...] = (
    "stem_residue_odt",
    "branch_odt",
    "foliage_odt",
    "branch_foliage_odt",
    "total_residue_odt",
    "recovery_factor",
    "recoverable_residue_odt",
    "branch_foliage_estimated",
    "branch_foliage_evidence",
    "branch_foliage_equation_id",
    "branch_foliage_equation_species",
    "branch_foliage_reference",
    "branch_foliage_species_specific",
    "branch_foliage_proxy",
    "branch_foliage_source",
    "branch_foliage_source_licence",
    "residue_component_scope",
    "stem_residue_odt_expanded",
    "branch_odt_expanded",
    "foliage_odt_expanded",
    "branch_foliage_odt_expanded",
    "total_residue_odt_expanded",
    "recoverable_residue_odt_expanded",
)


@dataclass(frozen=True, slots=True)
class BiomassEquation:
    """One published branch or foliage equation for one species or group."""

    bc_species_code: str
    common_name: str
    species_group: str
    equation_species: str
    component: str
    a: float
    b: float
    c: float
    reference: str
    species_specific: bool
    proxy: bool
    proxy_reason: str
    dbh_min_cm: float
    dbh_max_cm: float
    height_min_m: float
    height_max_m: float
    domain_basis: str
    source_dataset: str = "CFS Biomass and Nutrients Calculator"
    source_licence: str = "Open Government Licence - Canada"

    def __post_init__(self) -> None:
        code = _required_text("bc_species_code", self.bc_species_code).upper()
        component = _required_text("component", self.component).lower()
        if component not in COMPONENTS:
            raise SchemaError(f"component must be one of {COMPONENTS}, got {component!r}")
        object.__setattr__(self, "bc_species_code", code)
        object.__setattr__(self, "component", component)
        for name in (
            "common_name",
            "species_group",
            "equation_species",
            "reference",
            "domain_basis",
            "source_dataset",
            "source_licence",
        ):
            object.__setattr__(self, name, _required_text(name, getattr(self, name)))
        object.__setattr__(self, "a", _positive("a", self.a))
        object.__setattr__(self, "b", _finite("b", self.b))
        object.__setattr__(self, "c", _finite("c", self.c))
        object.__setattr__(
            self,
            "dbh_min_cm",
            _positive("dbh_min_cm", self.dbh_min_cm),
        )
        object.__setattr__(
            self,
            "dbh_max_cm",
            _positive("dbh_max_cm", self.dbh_max_cm),
        )
        object.__setattr__(
            self,
            "height_min_m",
            _positive("height_min_m", self.height_min_m),
        )
        object.__setattr__(
            self,
            "height_max_m",
            _positive("height_max_m", self.height_max_m),
        )
        if self.dbh_min_cm >= self.dbh_max_cm:
            raise SchemaError("dbh_min_cm must be below dbh_max_cm")
        if self.height_min_m >= self.height_max_m:
            raise SchemaError("height_min_m must be below height_max_m")

    @property
    def equation_id(self) -> str:
        reference = _slug(self.reference)
        species = _slug(self.equation_species)
        return f"CFS-{reference}-{self.component.upper()}-{species}"

    def predict_odt(self, dbh_cm: float, height_m: float) -> float:
        """Return one component's oven-dry mass in tonnes for a live stem."""
        dbh = _positive("dbh_cm", dbh_cm)
        height = _positive("height_m", height_m)
        if height <= 1.3:
            raise SchemaError("height_m must exceed breast height (1.3 m)")
        _require_in_domain(self, dbh, height)
        try:
            kilograms = self.a * math.pow(dbh, self.b) * math.pow(height, self.c)
        except (OverflowError, ValueError) as exc:
            raise SchemaError(
                f"allometry {self.equation_id} failed for DBH={dbh}, height={height}"
            ) from exc
        if not math.isfinite(kilograms) or kilograms <= 0.0:
            raise SchemaError(
                f"allometry {self.equation_id} produced non-positive biomass "
                f"{kilograms!r} for DBH={dbh}, height={height}"
            )
        return kilograms / 1000.0


@dataclass(frozen=True, slots=True)
class BiomassComponents:
    """Per-stem branch and foliage ODT with explicit equation provenance."""

    tree_id: str
    species: str
    species_group: str
    dbh_cm: float
    height_m: float
    branch_odt: float
    foliage_odt: float
    branch_foliage_odt: float
    branch_equation: BiomassEquation
    foliage_equation: BiomassEquation

    def __post_init__(self) -> None:
        object.__setattr__(self, "tree_id", _required_text("tree_id", self.tree_id))
        object.__setattr__(self, "species", _required_text("species", self.species).upper())
        object.__setattr__(
            self, "species_group", _required_text("species_group", self.species_group).lower()
        )
        object.__setattr__(self, "dbh_cm", _positive("dbh_cm", self.dbh_cm))
        object.__setattr__(self, "height_m", _positive("height_m", self.height_m))
        object.__setattr__(self, "branch_odt", _positive("branch_odt", self.branch_odt))
        object.__setattr__(self, "foliage_odt", _positive("foliage_odt", self.foliage_odt))
        expected = self.branch_odt + self.foliage_odt
        if not math.isclose(self.branch_foliage_odt, expected, rel_tol=1e-12, abs_tol=1e-12):
            raise SchemaError(
                "branch_foliage_odt must equal branch_odt + foliage_odt; "
                f"got {self.branch_foliage_odt!r} vs {expected!r}"
            )

    @property
    def evidence(self) -> str:
        return EVIDENCE_TIER

    @property
    def equation_id(self) -> str:
        return f"{self.branch_equation.equation_id}|{self.foliage_equation.equation_id}"

    @property
    def equation_species(self) -> str:
        return self.branch_equation.equation_species

    @property
    def reference(self) -> str:
        return self.branch_equation.reference

    @property
    def species_specific(self) -> bool:
        return (
            self.branch_equation.species_specific
            and self.foliage_equation.species_specific
        )

    @property
    def proxy(self) -> bool:
        return self.branch_equation.proxy or self.foliage_equation.proxy

    def to_dict(self) -> dict[str, Any]:
        return {
            "tree_id": self.tree_id,
            "species": self.species,
            "species_group": self.species_group,
            "dbh_cm": self.dbh_cm,
            "height_m": self.height_m,
            "branch_odt": self.branch_odt,
            "foliage_odt": self.foliage_odt,
            "branch_foliage_odt": self.branch_foliage_odt,
            "branch_equation_id": self.branch_equation.equation_id,
            "foliage_equation_id": self.foliage_equation.equation_id,
            "branch_foliage_equation_id": self.equation_id,
            "equation_species": self.equation_species,
            "reference": self.reference,
            "evidence": self.evidence,
            "species_specific": self.species_specific,
            "proxy": self.proxy,
            "source": self.branch_equation.source_dataset,
            "source_licence": self.branch_equation.source_licence,
        }


# ---------------------------------------------------------------------------
# Boundary parsing
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


def _required_text(name: str, value: Any) -> str:
    if value is None:
        raise SchemaError(f"{name} is required")
    text = str(value).strip()
    if not text:
        raise SchemaError(f"{name} must be a non-empty string")
    return text


def _parse_bool(name: str, value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "y"}:
        return True
    if text in {"false", "0", "no", "n"}:
        return False
    raise SchemaError(f"{name} must be a boolean, got {value!r}")


def _slug(value: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "-", value.strip().upper()).strip("-")


def _require_in_domain(
    equation: BiomassEquation, dbh_cm: float, height_m: float
) -> None:
    if not equation.dbh_min_cm <= dbh_cm <= equation.dbh_max_cm:
        raise SchemaError(
            f"DBH {dbh_cm} cm for {equation.bc_species_code} is outside the "
            f"documented allometry domain [{equation.dbh_min_cm}, "
            f"{equation.dbh_max_cm}] cm"
        )
    if not equation.height_min_m <= height_m <= equation.height_max_m:
        raise SchemaError(
            f"height {height_m} m for {equation.bc_species_code} is outside the "
            f"documented allometry domain [{equation.height_min_m}, "
            f"{equation.height_max_m}] m"
        )


def _read_cfs_equations(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"CFS biomass equation table not found: {path}")
    frame = pd.read_csv(path, skiprows=5, encoding="latin-1")
    required = {
        "Species_en",
        "Component_en",
        "a",
        "b",
        "c",
        "Reference",
    }
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise SchemaError(
            f"{path.name} is missing columns: {', '.join(missing)}"
        )
    table: dict[tuple[str, str], dict[str, Any]] = {}
    for row in frame.itertuples(index=False):
        species = str(row.Species_en).strip()
        component = str(row.Component_en).strip().lower()
        if not species or component not in {"branches", "foliage"}:
            continue
        key = (species, component)
        if key in table:
            raise SchemaError(f"duplicate CFS equation for {species} {component}")
        table[key] = {
            "a": row.a,
            "b": row.b,
            "c": row.c,
            "reference": str(row.Reference).strip(),
        }
    if not table:
        raise SchemaError(f"{path.name} contains no branch or foliage equations")
    return table


def _read_map(path: Path) -> list[dict[str, Any]]:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"BC biomass allometry map not found: {path}")
    frame = pd.read_csv(path, dtype={"bc_species_code": str})
    required = {
        "bc_species_code",
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
    seen: set[str] = set()
    for row in frame.itertuples(index=False):
        code = _required_text("bc_species_code", row.bc_species_code).upper()
        if code in seen:
            raise SchemaError(f"duplicate BC species map entry: {code}")
        seen.add(code)
        rows.append(
            {
                "bc_species_code": code,
                "common_name": _required_text("common_name", row.common_name),
                "species_group": _required_text(
                    "species_group", row.species_group
                ).lower(),
                "equation_species": _required_text(
                    "equation_species", row.equation_species
                ),
                "species_specific": _parse_bool(
                    "species_specific", row.species_specific
                ),
                "proxy": _parse_bool("proxy", row.proxy),
                "proxy_reason": "" if pd.isna(row.proxy_reason) else str(row.proxy_reason).strip(),
                "dbh_min_cm": _positive("dbh_min_cm", row.dbh_min_cm),
                "dbh_max_cm": _positive("dbh_max_cm", row.dbh_max_cm),
                "height_min_m": _positive("height_min_m", row.height_min_m),
                "height_max_m": _positive("height_max_m", row.height_max_m),
                "domain_basis": _required_text("domain_basis", row.domain_basis),
            }
        )
    if not rows:
        raise SchemaError(f"{path.name} contains no species mappings")
    return rows


def load_bc_biomass_allometry(
    map_path: Path = ALLOMETRY_MAP_PATH,
    source_path: Path = CFS_DBH_HEIGHT_PATH,
) -> dict[str, dict[str, BiomassEquation]]:
    """Parse the BC species map and CFS coefficient table into trusted equations.

    The returned mapping is keyed by BC species code, then by ``branch`` or
    ``foliage``.  Group fallbacks ``__CONIFER__``, ``__DECIDUOUS__`` and
    ``__ALL__`` are loaded too.  All validation happens here, at the boundary.
    """
    cfs = _read_cfs_equations(source_path)
    rows = _read_map(map_path)
    table: dict[str, dict[str, BiomassEquation]] = {}
    for row in rows:
        equations: dict[str, BiomassEquation] = {}
        for component in COMPONENTS:
            source_component = "branches" if component == "branch" else "foliage"
            key = (row["equation_species"], source_component)
            if key not in cfs:
                raise SchemaError(
                    "CFS coefficient table has no "
                    f"{source_component} equation for {row['equation_species']!r} "
                    f"(mapped from {row['bc_species_code']})"
                )
            coefficients = cfs[key]
            equations[component] = BiomassEquation(
                bc_species_code=row["bc_species_code"],
                common_name=row["common_name"],
                species_group=row["species_group"],
                equation_species=row["equation_species"],
                component=component,
                a=coefficients["a"],
                b=coefficients["b"],
                c=coefficients["c"],
                reference=coefficients["reference"],
                species_specific=row["species_specific"],
                proxy=row["proxy"],
                proxy_reason=row["proxy_reason"] or "none",
                dbh_min_cm=row["dbh_min_cm"],
                dbh_max_cm=row["dbh_max_cm"],
                height_min_m=row["height_min_m"],
                height_max_m=row["height_max_m"],
                domain_basis=row["domain_basis"],
            )
        table[row["bc_species_code"]] = equations
    return table


def _resolve_equations(
    table: Mapping[str, Mapping[str, BiomassEquation]],
    species: str,
    species_group: str,
) -> Mapping[str, BiomassEquation]:
    code = _required_text("species", species).upper()
    if code in table:
        return table[code]
    fallback_code = GROUP_FALLBACKS.get(
        _required_text("species_group", species_group).lower(), "__ALL__"
    )
    if fallback_code not in table:
        raise SchemaError(
            f"no allometry for species {code!r} and no {fallback_code} fallback"
        )
    return table[fallback_code]


def compute_biomass_components(
    *,
    tree_id: str,
    species: str,
    species_group: str,
    dbh_cm: float,
    height_m: float,
    table: Mapping[str, Mapping[str, BiomassEquation]],
) -> BiomassComponents:
    """Compute branch and foliage ODT for one live measured stem."""
    equations = _resolve_equations(table, species, species_group)
    branch = equations["branch"].predict_odt(dbh_cm, height_m)
    foliage = equations["foliage"].predict_odt(dbh_cm, height_m)
    return BiomassComponents(
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


def tree_components_for_bc(
    trees: Iterable[Any],
    table: Mapping[str, Mapping[str, BiomassEquation]],
) -> dict[str, BiomassComponents]:
    """Compute components for every BC tree in a pilot tree list."""
    components: dict[str, BiomassComponents] = {}
    for tree in trees:
        if str(tree.jurisdiction).upper() != "BC":
            continue
        if tree.tree_id in components:
            raise SchemaError(f"duplicate BC tree_id in allometry input: {tree.tree_id}")
        components[tree.tree_id] = compute_biomass_components(
            tree_id=tree.tree_id,
            species=tree.species,
            species_group=tree.species_group,
            dbh_cm=tree.dbh_cm,
            height_m=tree.height_m,
            table=table,
        )
    if not components:
        raise SchemaError("no BC trees supplied to branch/foliage allometry")
    return components


# ---------------------------------------------------------------------------
# Ledger annotation
# ---------------------------------------------------------------------------


def _recovery_factor(value: float) -> float:
    factor = _finite("recovery_factor", value)
    if not 0.0 < factor <= 1.0:
        raise SchemaError("recovery_factor must be in (0, 1]")
    return factor


def add_bc_component_fields(
    row: Mapping[str, Any],
    components: BiomassComponents,
    *,
    weight: float,
    stem_residue_odt: float,
    recovery_factor: float = DEFAULT_ALLOMETRY_RECOVERY_FACTOR,
) -> dict[str, Any]:
    """Return a new residue row with gross and recoverable component fields."""
    weight = _positive("weight", weight)
    stem = _positive("stem_residue_odt", stem_residue_odt)
    factor = _recovery_factor(recovery_factor)
    total_gross = stem + components.branch_foliage_odt
    recoverable = total_gross * factor
    annotated = dict(row)
    annotated.update(
        {
            "stem_residue_odt": stem,
            "branch_odt": components.branch_odt,
            "foliage_odt": components.foliage_odt,
            "branch_foliage_odt": components.branch_foliage_odt,
            "total_residue_odt": total_gross,
            "recovery_factor": factor,
            "recoverable_residue_odt": recoverable,
            "branch_foliage_estimated": True,
            "branch_foliage_evidence": components.evidence,
            "branch_foliage_equation_id": components.equation_id,
            "branch_foliage_equation_species": components.equation_species,
            "branch_foliage_reference": components.reference,
            "branch_foliage_species_specific": components.species_specific,
            "branch_foliage_proxy": components.proxy,
            "branch_foliage_source": components.branch_equation.source_dataset,
            "branch_foliage_source_licence": components.branch_equation.source_licence,
            "residue_component_scope": "stem_plus_branch_foliage",
            "stem_residue_odt_expanded": stem * weight,
            "branch_odt_expanded": components.branch_odt * weight,
            "foliage_odt_expanded": components.foliage_odt * weight,
            "branch_foliage_odt_expanded": components.branch_foliage_odt * weight,
            "total_residue_odt_expanded": total_gross * weight,
            "recoverable_residue_odt_expanded": recoverable * weight,
        }
    )
    return annotated


def add_not_estimated_component_fields(
    row: Mapping[str, Any],
    *,
    stem_residue_odt: float,
) -> dict[str, Any]:
    """Annotate a non-BC row without pretending missing biomass is zero."""
    stem = _positive("stem_residue_odt", stem_residue_odt)
    annotated = dict(row)
    annotated.update(
        {
            "stem_residue_odt": stem,
            "branch_odt": None,
            "foliage_odt": None,
            "branch_foliage_odt": None,
            "total_residue_odt": None,
            "recovery_factor": None,
            "recoverable_residue_odt": None,
            "branch_foliage_estimated": False,
            "branch_foliage_evidence": "not_estimated",
            "branch_foliage_equation_id": "",
            "branch_foliage_equation_species": "",
            "branch_foliage_reference": "",
            "branch_foliage_species_specific": False,
            "branch_foliage_proxy": False,
            "branch_foliage_source": "",
            "branch_foliage_source_licence": "",
            "residue_component_scope": "stem_only_branch_foliage_not_estimated",
            "stem_residue_odt_expanded": stem * float(row["expansion_factor"]),
            "branch_odt_expanded": None,
            "foliage_odt_expanded": None,
            "branch_foliage_odt_expanded": None,
            "total_residue_odt_expanded": None,
            "recoverable_residue_odt_expanded": None,
        }
    )
    return annotated
