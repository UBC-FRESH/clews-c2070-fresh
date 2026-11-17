"""Declared quantity units and conversions for the Stage A assignment layer.

The module is a parsed boundary.  A unit is canonicalised once, and every
conversion either returns a finite value with a documented factor or raises a
specific error.  There is no implicit fallback and no silent ``m3``/``ODT``
interchange.

Canonical units
---------------
``m3_green``
    Green solid cubic metre, the Stage A model basis.  The bare ``m3`` token is
    accepted as an alias because every Stage A quantity is green volume.
``odt``
    Oven-dry tonne.
``bdmt``
    Bone-dry metric tonne.  One bdmt is treated as one odt; this is the
    dry-mass equivalence recorded as a candidate in the FFCM unit registry.
``admt``
    Air-dry metric tonne.  Conversion to odt requires a declared air-dry
    moisture; without one the conversion is ambiguous and is rejected.
``mbf`` / ``bf``
    Thousand board feet / board foot.  Volume conversion requires a declared
    recovery factor (m3 per mbf); without one it is rejected.
``kwh`` / ``mwh`` / ``gj``
    Energy units.  They convert among themselves and never to mass or volume
    without a declared energy density, which this module does not assume.

Density
-------
ODT <-> m3 uses a declared basic density.  The default is 0.45 ODT/m3 from the
TSA24 residue ledger assumption; :class:`DensityBook` allows per-grade or
per-species overrides.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

__all__ = [
    "ACCEPTED_UNITS",
    "ARCHIVE_TONNE_UNIT",
    "DEFAULT_BASIC_DENSITY_ODT_M3",
    "AmbiguousConversionError",
    "Conversion",
    "ConversionContext",
    "DensityBook",
    "MissingConversionFactorError",
    "UnitError",
    "UnsupportedConversionError",
    "UnsupportedUnitError",
    "can_convert",
    "canonical_unit",
    "conversion_factor",
    "convert",
    "from_m3_green",
    "to_m3_green",
    "to_odt",
    "unit_dimension",
    "unit_registry_rows",
]


class UnitError(ValueError):
    """Base class for a rejected or ambiguous unit operation."""


class UnsupportedUnitError(UnitError):
    """Raised when a unit is unknown or deliberately outside the contract."""


class UnsupportedConversionError(UnitError):
    """Raised when two units have no declared conversion path."""


class MissingConversionFactorError(UnitError):
    """Raised when a conversion path needs a factor that was not declared."""


class AmbiguousConversionError(UnitError):
    """Raised when a unit or conversion has more than one defensible meaning."""


DEFAULT_BASIC_DENSITY_ODT_M3 = 0.45
DEFAULT_DENSITY_SOURCE = "tsa24_prototype/residue_ledger.csv basic_density_odt_m3"
ARCHIVE_TONNE_UNIT = "t"

_VOLUME_UNITS = frozenset({"m3_green"})
_DRY_MASS_UNITS = frozenset({"odt", "bdmt", "admt"})
_BOARD_UNITS = frozenset({"mbf", "bf"})
_ENERGY_UNITS = frozenset({"kwh", "mwh", "gj"})

_ALIASES = {
    "m3": "m3_green",
    "m^3": "m3_green",
    "m3_green": "m3_green",
    "m3green": "m3_green",
    "odt": "odt",
    "bdt": "bdmt",
    "bdmt": "bdmt",
    "admt": "admt",
    "mbf": "mbf",
    "bf": "bf",
    "kwh": "kwh",
    "mwh": "mwh",
    "gj": "gj",
}

# Units that look plausible but are not safe to infer.  Failing loudly here
# prevents an accidental `m3_odt` or wet-tonne conversion.
_AMBIGUOUS_UNITS = {
    "m3_odt": "m3_odt mixes a volume and an oven-dry mass basis; declare m3_green or odt",
    "m3solid": "m3_solid is not the Stage A green-volume basis",
    "m3_loose": "m3_loose is a bulk volume, not green solid volume",
    "m3s": "m3_s is ambiguous; use m3_green",
    "tonne": "tonne is ambiguous; use odt, bdmt, or admt",
    "tonnes": "tonne is ambiguous; use odt, bdmt, or admt",
    "metric_tonne": "metric tonne is ambiguous; use odt, bdmt, or admt",
    "kg": "kg is not a Stage A quantity unit; use odt/bdmt/admt",
}

_FACTORS: dict[tuple[str, str], tuple[float, str]] = {
    ("kwh", "mwh"): (1.0e-3, "SI: 1 kWh = 0.001 MWh"),
    ("mwh", "kwh"): (1.0e3, "SI: 1 MWh = 1000 kWh"),
    ("mwh", "gj"): (3.6, "SI: 1 MWh = 3.6 GJ"),
    ("gj", "mwh"): (1.0 / 3.6, "SI: 1 GJ = 1/3.6 MWh"),
    ("kwh", "gj"): (3.6e-3, "SI: 1 kWh = 0.0036 GJ"),
    ("gj", "kwh"): (1.0 / 3.6e-3, "SI: 1 GJ = 277.777... kWh"),
    ("mbf", "bf"): (1.0e3, "FFCM unit-registry.csv: 1 mbf = 1000 bf"),
    ("bf", "mbf"): (1.0e-3, "FFCM unit-registry.csv: 1000 bf = 1 mbf"),
}

_UNIT_SOURCES = {
    "m3_green": "Stage A model basis: green solid cubic metre",
    "odt": "FFCM unit-registry.csv: oven-dry tonne",
    "bdmt": "FFCM unit-registry.csv: bone-dry metric tonne; 1 bdmt = 1 odt dry-mass equivalence (candidate)",
    "admt": "FFCM unit-registry.csv: air-dry metric tonne; requires a declared air-dry moisture",
    "mbf": "FFCM unit-registry.csv: thousand board feet; volume needs a declared recovery factor",
    "bf": "FFCM unit-registry.csv: board foot",
    "kwh": "SI energy unit: kilowatt-hour",
    "mwh": "SI energy unit: megawatt-hour",
    "gj": "SI energy unit: gigajoule",
}

ACCEPTED_UNITS = frozenset(_ALIASES) | frozenset({ARCHIVE_TONNE_UNIT})


def canonical_unit(unit: str, *, allow_archive_tonne: bool = False) -> str:
    """Return the canonical unit token or raise a loud error."""
    token = str(unit).strip().lower().replace("³", "3").replace("^3", "3")
    token = token.replace(" ", "").replace("-", "_")
    if token in _AMBIGUOUS_UNITS:
        raise AmbiguousConversionError(_AMBIGUOUS_UNITS[token])
    if token == ARCHIVE_TONNE_UNIT:
        if not allow_archive_tonne:
            raise UnsupportedUnitError(
                "the bare tonne unit is not accepted; use odt, bdmt, or admt. "
                "Archive transload costs may opt in with allow_archive_tonne=True "
                "and an explicit archive-tonne-as-odt convention."
            )
        return "odt"
    canonical = _ALIASES.get(token)
    if canonical is None:
        raise UnsupportedUnitError(
            f"unit {unit!r} is not supported; accepted units are "
            f"{sorted(ACCEPTED_UNITS)}"
        )
    return canonical


def unit_dimension(unit: str, *, allow_archive_tonne: bool = False) -> str:
    """Return ``volume``, ``dry_mass``, ``board_volume`` or ``energy``."""
    canonical = canonical_unit(unit, allow_archive_tonne=allow_archive_tonne)
    if canonical in _VOLUME_UNITS:
        return "volume"
    if canonical in _DRY_MASS_UNITS:
        return "dry_mass"
    if canonical in _BOARD_UNITS:
        return "board_volume"
    if canonical in _ENERGY_UNITS:
        return "energy"
    raise UnsupportedUnitError(f"unit {unit!r} has no registered dimension")


def _positive(value: object, name: str) -> float:
    number = float(value)  # type: ignore[arg-type]
    if not math.isfinite(number) or number <= 0.0:
        raise MissingConversionFactorError(f"{name} must be finite and positive")
    return number


@dataclass(frozen=True, slots=True)
class DensityBook:
    """Declared ODT/m3 densities with grade and species overrides."""

    default_odt_m3: float = DEFAULT_BASIC_DENSITY_ODT_M3
    default_source: str = DEFAULT_DENSITY_SOURCE
    by_grade: Mapping[str, float] = field(default_factory=dict)
    by_species: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _positive(self.default_odt_m3, "default_odt_m3")
        normalized_grade = {
            str(key).strip().upper(): _positive(value, f"density for grade {key}")
            for key, value in self.by_grade.items()
        }
        normalized_species = {
            str(key).strip().upper(): _positive(value, f"density for species {key}")
            for key, value in self.by_species.items()
        }
        object.__setattr__(self, "by_grade", MappingProxyType(normalized_grade))
        object.__setattr__(self, "by_species", MappingProxyType(normalized_species))

    def resolve(self, *, grade_id: str = "", species: str = "") -> tuple[float, str]:
        grade = str(grade_id).strip().upper()
        if grade and grade in self.by_grade:
            return self.by_grade[grade], f"{self.default_source};grade_override={grade}"
        species_key = str(species).strip().upper()
        if species_key and species_key in self.by_species:
            return (
                self.by_species[species_key],
                f"{self.default_source};species_override={species_key}",
            )
        return self.default_odt_m3, self.default_source


@dataclass(frozen=True, slots=True)
class ConversionContext:
    """All factors that a conversion is allowed to use."""

    basic_density_odt_m3: float = DEFAULT_BASIC_DENSITY_ODT_M3
    density_source: str = DEFAULT_DENSITY_SOURCE
    recovery_factor_m3_per_mbf: float | None = None
    recovery_source: str = ""
    admt_moisture_wet_basis: float | None = None
    allow_archive_tonne: bool = False
    grade_id: str = ""
    species: str = ""

    def __post_init__(self) -> None:
        _positive(self.basic_density_odt_m3, "basic_density_odt_m3")
        object.__setattr__(self, "density_source", str(self.density_source).strip())
        object.__setattr__(self, "recovery_source", str(self.recovery_source).strip())
        object.__setattr__(self, "grade_id", str(self.grade_id).strip().upper())
        object.__setattr__(self, "species", str(self.species).strip().upper())
        if self.recovery_factor_m3_per_mbf is not None:
            _positive(self.recovery_factor_m3_per_mbf, "recovery_factor_m3_per_mbf")
        if self.admt_moisture_wet_basis is not None:
            moisture = float(self.admt_moisture_wet_basis)
            if not math.isfinite(moisture) or not 0.0 <= moisture < 1.0:
                raise AmbiguousConversionError(
                    "admt_moisture_wet_basis must be in [0, 1)"
                )
            object.__setattr__(self, "admt_moisture_wet_basis", moisture)

    @classmethod
    def from_density_book(
        cls,
        book: DensityBook,
        *,
        grade_id: str = "",
        species: str = "",
        recovery_factor_m3_per_mbf: float | None = None,
        recovery_source: str = "",
        admt_moisture_wet_basis: float | None = None,
        allow_archive_tonne: bool = False,
    ) -> ConversionContext:
        density, source = book.resolve(grade_id=grade_id, species=species)
        return cls(
            basic_density_odt_m3=density,
            density_source=source,
            recovery_factor_m3_per_mbf=recovery_factor_m3_per_mbf,
            recovery_source=recovery_source,
            admt_moisture_wet_basis=admt_moisture_wet_basis,
            allow_archive_tonne=allow_archive_tonne,
            grade_id=grade_id,
            species=species,
        )


DEFAULT_CONTEXT = ConversionContext()


@dataclass(frozen=True, slots=True)
class Conversion:
    value: float
    from_unit: str
    to_unit: str
    factor: float
    source: str


def _density_factor(context: ConversionContext) -> tuple[float, str]:
    return context.basic_density_odt_m3, context.density_source


def _admt_to_odt_factor(context: ConversionContext) -> tuple[float, str]:
    moisture = context.admt_moisture_wet_basis
    if moisture is None:
        raise AmbiguousConversionError(
            "admt -> odt requires a declared admt_moisture_wet_basis; "
            "air-dry tonne is not oven-dry tonne"
        )
    return 1.0 - moisture, "declared admt_moisture_wet_basis"


def conversion_factor(
    from_unit: str,
    to_unit: str,
    *,
    context: ConversionContext = DEFAULT_CONTEXT,
) -> tuple[float, str]:
    """Return ``(factor, source)`` for a declared conversion."""
    source_unit = canonical_unit(
        from_unit, allow_archive_tonne=context.allow_archive_tonne
    )
    target_unit = canonical_unit(
        to_unit, allow_archive_tonne=context.allow_archive_tonne
    )
    if source_unit == target_unit:
        return 1.0, "identity"

    direct = _FACTORS.get((source_unit, target_unit))
    if direct is not None:
        return direct

    source_dim = unit_dimension(
        source_unit, allow_archive_tonne=context.allow_archive_tonne
    )
    target_dim = unit_dimension(
        target_unit, allow_archive_tonne=context.allow_archive_tonne
    )

    if source_dim == "volume" and target_dim == "dry_mass":
        density, source = _density_factor(context)
        return density, source
    if source_dim == "dry_mass" and target_dim == "volume":
        density, source = _density_factor(context)
        return 1.0 / density, source
    if source_dim == "dry_mass" and target_dim == "dry_mass":
        if source_unit == "admt":
            factor, source = _admt_to_odt_factor(context)
            if target_unit == "odt":
                return factor, source
            if target_unit == "bdmt":
                return factor * 1.0, f"{source};bdmt=odt dry-mass equivalence"
        if target_unit == "admt":
            factor, source = _admt_to_odt_factor(context)
            if source_unit == "odt":
                return 1.0 / factor, f"{source};inverse"
            if source_unit == "bdmt":
                return 1.0 / factor, f"{source};bdmt=odt dry-mass equivalence;inverse"
        return 1.0, "dry-mass equivalence (bdmt = odt)"
    if source_dim == "volume" and target_dim == "board_volume":
        recovery = context.recovery_factor_m3_per_mbf
        if recovery is None:
            raise MissingConversionFactorError(
                "m3_green <-> mbf requires a declared recovery_factor_m3_per_mbf"
            )
        scale = 1000.0 if target_unit == "bf" else 1.0
        return scale / recovery, context.recovery_source or "declared recovery factor"
    if source_dim == "board_volume" and target_dim == "volume":
        recovery = context.recovery_factor_m3_per_mbf
        if recovery is None:
            raise MissingConversionFactorError(
                "mbf <-> m3_green requires a declared recovery_factor_m3_per_mbf"
            )
        scale = 1000.0 if source_unit == "bf" else 1.0
        return recovery / scale, context.recovery_source or "declared recovery factor"
    if source_dim == "energy" and target_dim == "energy":
        raise UnsupportedConversionError(
            f"no declared conversion path from {source_unit} to {target_unit}"
        )
    raise UnsupportedConversionError(
        f"cannot convert {source_unit} ({source_dim}) to {target_unit} "
        f"({target_dim}); no declared cross-dimension factor"
    )


def convert(
    value: float,
    from_unit: str,
    to_unit: str,
    *,
    context: ConversionContext = DEFAULT_CONTEXT,
) -> float:
    """Convert a finite value or raise a specific :class:`UnitError`."""
    number = float(value)
    if not math.isfinite(number):
        raise UnitError("quantity value must be finite")
    factor, _source = conversion_factor(from_unit, to_unit, context=context)
    result = number * factor
    if not math.isfinite(result):
        raise UnitError("conversion produced a non-finite value")
    return result


def explain(
    value: float,
    from_unit: str,
    to_unit: str,
    *,
    context: ConversionContext = DEFAULT_CONTEXT,
) -> Conversion:
    """Return the converted value together with its factor and source."""
    factor, source = conversion_factor(from_unit, to_unit, context=context)
    return Conversion(
        value=convert(value, from_unit, to_unit, context=context),
        from_unit=canonical_unit(
            from_unit, allow_archive_tonne=context.allow_archive_tonne
        ),
        to_unit=canonical_unit(
            to_unit, allow_archive_tonne=context.allow_archive_tonne
        ),
        factor=factor,
        source=source,
    )


def can_convert(
    from_unit: str,
    to_unit: str,
    *,
    context: ConversionContext = DEFAULT_CONTEXT,
) -> bool:
    try:
        conversion_factor(from_unit, to_unit, context=context)
    except UnitError:
        return False
    return True


def to_m3_green(
    value: float,
    unit: str,
    *,
    context: ConversionContext = DEFAULT_CONTEXT,
) -> float:
    """Convert a declared quantity to the Stage A green-m3 basis."""
    return convert(value, unit, "m3_green", context=context)


def from_m3_green(
    value: float,
    unit: str,
    *,
    context: ConversionContext = DEFAULT_CONTEXT,
) -> float:
    """Convert a green-m3 quantity to another declared unit."""
    return convert(value, "m3_green", unit, context=context)


def to_odt(
    value: float,
    unit: str,
    *,
    context: ConversionContext = DEFAULT_CONTEXT,
) -> float:
    """Convert a declared quantity to oven-dry tonnes."""
    return convert(value, unit, "odt", context=context)


def unit_registry_rows() -> tuple[dict[str, Any], ...]:
    """Return the audit table used by the pilot report and manifest."""
    rows: list[dict[str, Any]] = []
    for unit in sorted(_ALIASES):
        canonical = _ALIASES[unit]
        if unit != canonical:
            continue
        rows.append(
            {
                "unit": canonical,
                "dimension": unit_dimension(canonical),
                "accepted": "yes",
                "source": _UNIT_SOURCES[canonical],
            }
        )
    rows.extend(
        [
            {
                "unit": "m3",
                "dimension": "volume",
                "accepted": "alias_of_m3_green",
                "source": "Stage A green-volume alias; no separate meaning",
            },
            {
                "unit": "t",
                "dimension": "dry_mass",
                "accepted": "conditional_archive_tonne",
                "source": "FFCM transload $/t may opt in as odt; otherwise rejected",
            },
            {
                "unit": "m3_odt",
                "dimension": "ambiguous",
                "accepted": "no",
                "source": "mixed volume/mass basis; rejected",
            },
        ]
    )
    return tuple(rows)
