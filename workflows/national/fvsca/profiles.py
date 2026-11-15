"""Variant profiles for the FVS-CA multi-variant orchestrator.

FVS is not one model in Canada.  British Columbia, Ontario, and the Acadian
region each have their own calibrated FVS variant, with different stand keys,
tree-list records, and species lists.  Alberta, Saskatchewan, Manitoba, Yukon,
and the Northwest Territories have no calibrated FVS variant at all.

A :class:`VariantProfile` is the machine-readable statement of what a variant
needs and what it can carry.  It is deliberately a *contract*, not an adapter:
this module records the requirements, while :mod:`workflows.national.fvsca.bc`
implements the one variant with a working adapter in this prototype.

The BC values mirror the read-only ``fresh-fvsbc`` reference implementation:

- ``bec.py`` for the twelve resolved BEC zones;
- ``treelist.py`` for the fifteen JSP species and seven donor aliases;
- ``calibration.py`` for the calibration species groups.

Nothing here runs FVS and nothing here fits a model.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

__all__ = [
    "ACD_PROFILE",
    "BC_CALIBRATION_GROUPS",
    "BC_DONOR_SPECIES",
    "BC_JSP_CODES",
    "BC_PROFILE",
    "BC_SUPPORTED_ZONES",
    "ON_PROFILE",
    "PROFILES",
    "SITE_KEY_KINDS",
    "UNREPRESENTED_REGIONS",
    "VariantProfile",
    "derive_bec",
    "normalise_bec",
]

#: The site-key grammars a profile may declare.
SITE_KEY_KINDS: tuple[str, ...] = (
    "bec",
    "site_index_kodfor",
    "ecozone_site",
)

#: The twelve BEC zones the BC variant resolves, in ``fresh-fvsbc`` order.
BC_SUPPORTED_ZONES: tuple[str, ...] = (
    "ESSF",
    "ICH",
    "IDF",
    "MS",
    "PP",
    "SBS",
    "SBPS",
    "CWH",
    "CDF",
    "BWBS",
    "CMA",
    "MH",
)

#: BC's fifteen JSP species codes, from ``fresh-fvsbc`` ``treelist.py``.
BC_JSP_CODES: tuple[str, ...] = (
    "PW",
    "LW",
    "FD",
    "BG",
    "HW",
    "CW",
    "PL",
    "SE",
    "BL",
    "PY",
    "EP",
    "AT",
    "AC",
    "OC",
    "OH",
)

#: The seven high-priority BC species the fifteen-code variant does not carry,
#: and the donor code the reference writer substitutes until the true species
#: tables exist.  The output carries the donor code, not the input code.
BC_DONOR_SPECIES: Mapping[str, str] = MappingProxyType(
    {
        "SS": "SE",  # Sitka spruce -> spruce group
        "SB": "SE",  # black spruce -> spruce group
        "BA": "BG",  # amabilis fir -> grand fir, the nearest true fir
        "HM": "HW",  # mountain hemlock -> western hemlock
        "YC": "CW",  # yellow-cedar -> western redcedar
        "DR": "AT",  # red alder -> trembling aspen, a pioneer broadleaf
        "MB": "AC",  # bigleaf maple -> black cottonwood, a broadleaf
    }
)

#: The calibration groups ``fresh-fvsbc.calibration`` uses, keyed by source
#: species code.  A code outside this map falls back to the group of its
#: resolved JSP code, then to ``UNKNOWN``.
BC_CALIBRATION_GROUPS: Mapping[str, str] = MappingProxyType(
    {
        "FD": "FD",
        "PL": "PL",
        "SE": "SE",
        "SW": "SE",
        "SX": "SE",
        "HW": "HW",
        "CW": "HW",
        "EP": "HW",
        "AT": "HW",
        "AC": "HW",
        "OC": "HW",
        "OH": "HW",
        "PW": "PW",
        "LW": "LW",
        "BG": "BG",
        "BL": "BL",
        "PY": "PY",
    }
)

#: Candidate backends for the regions without a calibrated FVS variant.  These
#: are *candidates*, not implemented adapters, and are recorded so the gap is
#: explicit rather than silently aliased to a neighbouring FVS variant.
UNREPRESENTED_REGIONS: Mapping[str, str] = MappingProxyType(
    {
        "AB": (
            "No calibrated FVS variant. Candidate non-FVS backend: GYPSY "
            "(not implemented)."
        ),
        "SK": (
            "No calibrated FVS variant. Candidate non-FVS backend: MGM or a "
            "meta-model (not implemented)."
        ),
        "MB": (
            "No calibrated FVS variant. Candidate non-FVS backend: MGM or a "
            "meta-model (not implemented)."
        ),
        "YT": (
            "No calibrated FVS variant. Candidate non-FVS meta-model or "
            "transfer from BC (not implemented)."
        ),
        "NT": (
            "No calibrated FVS variant. Candidate non-FVS meta-model or "
            "transfer from BC (not implemented)."
        ),
    }
)

_PLAIN_MISSING = frozenset({"", "nan", "none", "<na>", "null"})


def _text(value: object) -> str:
    """Return a stripped string, or ``""`` for a missing value."""
    if value is None:
        return ""
    if isinstance(value, float) and value != value:
        return ""
    if not isinstance(value, str):
        try:
            if value != value:  # pandas.NA and numpy NaN both compare false
                return ""
        except TypeError:
            pass
    text = str(value).strip()
    return "" if text.lower() in _PLAIN_MISSING else text


def derive_bec(zone: object, subzone: object, variant: object) -> str | None:
    """Build the calibration BEC label from its three source fields.

    The label is the form ``fresh-fvsbc.calibration`` produces: upper-case
    zone, lower-case subzone, upper-case variant.  A missing zone returns
    ``None``; a missing subzone or variant is left blank rather than invented.

    Parameters
    ----------
    zone, subzone, variant : object
        The ``eco_zone``, ``eco_subzone``, and ``eco_variant`` values.

    Returns
    -------
    str or None
        A label such as ``CWHvm1``, or ``None`` when the zone is absent.
    """
    zone_text = _text(zone).upper()
    if not zone_text:
        return None
    subzone_text = _text(subzone).lower()
    variant_text = _text(variant)
    if variant_text.endswith(".0"):
        variant_text = variant_text[:-2]
    return f"{zone_text}{subzone_text}{variant_text.upper()}"


def normalise_bec(value: object) -> str | None:
    """Normalise the zone token of a BEC label to upper case."""
    text = _text(value)
    if not text:
        return None
    for zone in sorted(BC_SUPPORTED_ZONES, key=len, reverse=True):
        if text[: len(zone)].upper() == zone:
            return f"{zone}{text[len(zone):]}"
    return text


def _zone_of(bec: str) -> str:
    """Return the leading upper-case zone token of a BEC label."""
    match = re.match(r"^[A-Z]+", bec)
    return match.group(0) if match else ""


@dataclass(frozen=True, slots=True)
class VariantProfile:
    """The requirements and defaults of one FVS variant.

    Parameters
    ----------
    variant_id : str
        Short identifier used by the CLI and output metadata, for example
        ``bc``.
    display_name : str
        Human-readable variant name.
    fvs_variant : str or None
        The FVS variant code, for example ``FVSbc``.  ``None`` marks a region
        with no calibrated variant.
    site_key_kind : str
        One of :data:`SITE_KEY_KINDS`.  BC keys on BEC, Ontario on site index
        and KODFOR, and the Acadian variant on ecozone and site.
    site_key_fields : tuple of str
        The v0 columns that carry the site key.
    tree_list_format : str
        The tree-list record format the variant's FVS build reads.
    species_scheme : str
        A short description of the species list.
    species_count : int or None
        Declared species count when the full list is not carried here.
    species_codes : tuple of str
        The explicit species list, when this prototype carries one.
    donor_species : mapping
        Source code to donor code aliases.  The output carries the donor.
    calibration_groups : mapping
        Source code to calibration group.
    keyword_defaults : mapping
        Default keyword-file values or, where the value is not yet settled, a
        description of how the value is derived.
    bundle_defaults : mapping
        Defaults for the downstream run bundle, such as the backend name.
    supported_zones : tuple of str
        Zones the variant resolves.  Empty means "not yet declared", not
        "everything".
    coverage : tuple of str
        Province or territory codes the variant represents.
    implementation_status : str
        ``implemented`` or ``stub``.
    notes : tuple of str
        Explicit caveats for the orchestrator and reviewers.
    """

    variant_id: str
    display_name: str
    fvs_variant: str | None
    site_key_kind: str
    site_key_fields: tuple[str, ...]
    tree_list_format: str
    species_scheme: str
    species_count: int | None = None
    species_codes: tuple[str, ...] = ()
    donor_species: Mapping[str, str] = field(default_factory=dict)
    calibration_groups: Mapping[str, str] = field(default_factory=dict)
    keyword_defaults: Mapping[str, str] = field(default_factory=dict)
    bundle_defaults: Mapping[str, str] = field(default_factory=dict)
    supported_zones: tuple[str, ...] = ()
    coverage: tuple[str, ...] = ()
    implementation_status: str = "stub"
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.site_key_kind not in SITE_KEY_KINDS:
            raise ValueError(
                f"unknown site_key_kind {self.site_key_kind!r}; "
                f"expected one of {', '.join(SITE_KEY_KINDS)}"
            )
        if self.implementation_status not in {"implemented", "stub"}:
            raise ValueError(
                f"unknown implementation_status {self.implementation_status!r}"
            )
        for name in ("donor_species", "calibration_groups", "keyword_defaults", "bundle_defaults"):
            object.__setattr__(self, name, MappingProxyType(dict(getattr(self, name))))

    def site_key(self, row: Mapping[str, object]) -> str | None:
        """Return the site key for one v0 site row.

        BC keys on the BEC label.  The other kinds join their declared fields
        with ``|`` so a stub profile still has a deterministic, documented
        key; an adapter for those variants must replace this with its real
        grammar.
        """
        if self.site_key_kind == "bec":
            return derive_bec(
                row.get("eco_zone"),
                row.get("eco_subzone"),
                row.get("eco_variant"),
            )
        fields = [row.get(name) for name in self.site_key_fields]
        parts = [_text(value) for value in fields]
        key = "|".join(part for part in parts if part)
        return key or None

    def accepts_zone(self, zone: object) -> bool:
        """Return whether the variant resolves a BEC zone."""
        zone_text = _text(zone).upper()
        if not zone_text:
            return False
        return zone_text in self.supported_zones

    def accepts_bec(self, bec: object) -> bool:
        """Return whether a BEC label's zone is one the variant resolves."""
        label = normalise_bec(bec)
        if label is None:
            return False
        return self.accepts_zone(_zone_of(label))


BC_PROFILE = VariantProfile(
    variant_id="bc",
    display_name="British Columbia",
    fvs_variant="FVSbc",
    site_key_kind="bec",
    site_key_fields=("eco_zone", "eco_subzone", "eco_variant"),
    tree_list_format=(
        "BC TREFMT from canada/bc/blkdat.f: "
        "(I4,T1,I7,F6.0,I1,A3,F5.1,F4.1,2F5.1,F5.1,I1,3(I2,I2),2I1,I2,2I3,2I1,F3.0)"
    ),
    species_scheme=(
        "BC JSP 15-code list plus 7 donor aliases; unresolved source codes stay "
        "explicit rather than being silently imputed"
    ),
    species_count=len(BC_JSP_CODES),
    species_codes=BC_JSP_CODES,
    donor_species=BC_DONOR_SPECIES,
    calibration_groups=BC_CALIBRATION_GROUPS,
    keyword_defaults=MappingProxyType(
        {
            "STDINFO": "BEC zone + subzone + variant from the site key",
            "TREEFMT": "BC TREFMT (record width 69 columns)",
            "VOLUME": "gross merchantable volume (source VOL_MER)",
            "SITE_INDEX": "compiled species-level site index, where present",
        }
    ),
    bundle_defaults=MappingProxyType(
        {
            "backend": "FVSbc",
            "tree_list": "bc_trefmt",
            "volume": "merchantable",
            "biomass": "post-processor not implemented in this prototype",
        }
    ),
    supported_zones=BC_SUPPORTED_ZONES,
    coverage=("BC",),
    implementation_status="implemented",
    notes=(
        "PSP plots are normally fixed-area; non-PSP CMI/YSM/VRI plots can be "
        "variable-radius.  The v0 tree expansion factor carries the design, so "
        "the consumer tables do not need a separate BAF to rebuild BA/TPH.",
        "BEC labels are zone + lower-case subzone + upper-case variant, which "
        "is the calibration key, not the full FVS HABTYP region/zone/series "
        "string.  A run bundle must add a region and series later.",
    ),
)

ON_PROFILE = VariantProfile(
    variant_id="on",
    display_name="Ontario",
    fvs_variant="FVSon",
    site_key_kind="site_index_kodfor",
    site_key_fields=("site_index", "kodfor"),
    tree_list_format="Ontario FVS tree list (FVSon record layout; stub)",
    species_scheme="FVSon species list (72 codes; not carried in this prototype)",
    species_count=72,
    keyword_defaults=MappingProxyType(
        {
            "SITE_INDEX": "site index",
            "KODFOR": "Ontario forest-unit / KODFOR code",
        }
    ),
    bundle_defaults=MappingProxyType({"backend": "FVSon", "status": "stub"}),
    coverage=("ON",),
    implementation_status="stub",
    notes=(
        "Profile only.  No Ontario plot source adapter, species table, or "
        "keyword bundle is implemented in this prototype.",
        "Ontario needs site index and KODFOR, which the harmonized v0 FAIB "
        "tables do not carry.",
    ),
)

ACD_PROFILE = VariantProfile(
    variant_id="acd",
    display_name="Acadian",
    fvs_variant="FVSacd",
    site_key_kind="ecozone_site",
    site_key_fields=("ecozone", "site_id"),
    tree_list_format="Acadian FVS tree list (FVSacd record layout; stub)",
    species_scheme="Acadian species list (108 codes; not carried in this prototype)",
    species_count=108,
    keyword_defaults=MappingProxyType(
        {
            "ECOSITE": "ecozone and site key",
            "SITE_INDEX": "site index, where the source provides it",
        }
    ),
    bundle_defaults=MappingProxyType({"backend": "FVSacd", "status": "stub"}),
    coverage=("NB", "NS", "PE"),
    implementation_status="stub",
    notes=(
        "Profile only.  No Acadian plot source adapter, species table, or "
        "keyword bundle is implemented in this prototype.",
        "The Acadian variant represents New Brunswick, Nova Scotia, and Prince "
        "Edward Island; MAGPlot and NL FAL are not Acadian calibration sources.",
    ),
)

PROFILES: Mapping[str, VariantProfile] = MappingProxyType(
    {
        "bc": BC_PROFILE,
        "on": ON_PROFILE,
        "acd": ACD_PROFILE,
    }
)
