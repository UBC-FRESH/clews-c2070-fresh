"""Forest cost/revenue ledger schema and fail-closed validator.

This module implements the audit-layer schema defined in
``planning/forest-cost-revenue-ledger.md``.  It parses and validates
``forest_cost_revenue.csv``; it does not ingest MERIS, BCTS, BMMB, HBS, or any
other source, and it does not compute derived values.

Every row is a rate (CAD per declared ``unit``), not a transaction total.  The
validator rejects the whole batch on the first invalid row or on any cross-row
violation: missing fields, non-finite numbers, unknown enums, sign-matrix
breaks, duplicate keys, source-exclusivity conflicts, unpaired mirrors,
currency/volume-basis mixing, and malformed coverage claims.  There is no
zero-fill, default row, or residual adjustment.
"""

from __future__ import annotations

import csv
import math
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
TEMPLATE_PATH = (
    REPO_ROOT
    / "data/derived/national/harvest_basis/forest_cost_revenue_template.csv"
)

CORE_LEDGER_FIELDS: tuple[str, ...] = (
    "jurisdiction",
    "source",
    "year",
    "spatial_unit",
    "species",
    "product",
    "volume_m3",
    "unit",
    "value_cad",
    "value_basis",
    "perspective",
    "component",
    "sign",
    "confidence",
    "licence",
    "notes",
)

# ``quality`` and ``dbh_cm`` were added after the initial 16-field ledger.
# They are optional so a legacy 16-field CSV still loads: a missing header
# column means ``quality=NA`` and no DBH curve coordinate.  The canonical
# template and newly written ledgers carry both columns.
OPTIONAL_LEDGER_FIELDS: tuple[str, ...] = ("quality", "dbh_cm")
LEDGER_FIELDS: tuple[str, ...] = CORE_LEDGER_FIELDS + OPTIONAL_LEDGER_FIELDS


class Jurisdiction(StrEnum):
    BC = "BC"
    QC = "QC"
    AB = "AB"
    SK = "SK"
    MB = "MB"
    ON = "ON"
    NB = "NB"
    NS = "NS"
    NL = "NL"
    PE = "PE"
    YT = "YT"
    NT = "NT"
    NU = "NU"
    CAN = "CAN"


class Perspective(StrEnum):
    CROWN = "crown"
    FOREST_SECTOR = "forest_sector"


class Component(StrEnum):
    DELIVERED_LOG_REVENUE = "delivered_log_revenue"
    RESIDUE_REVENUE = "residue_revenue"
    STUMPAGE_BASE = "stumpage_base"
    STUMPAGE_BID_PREMIUM = "stumpage_bid_premium"
    STUMPAGE_TOTAL = "stumpage_total"
    HARVEST = "harvest"
    ROAD = "road"
    HAUL = "haul"
    SILVICULTURE = "silviculture"
    RESIDUE_RECOVERY = "residue_recovery"
    OVERHEAD = "overhead"
    ADMIN_FEE = "admin_fee"
    CROWN_OTHER = "crown_other"
    CROWN_CREDIT = "crown_credit"


class Sign(IntEnum):
    POSITIVE = 1
    NEGATIVE = -1


class Unit(StrEnum):
    """Ledger rate units.  ``volume_m3`` is the matching quantity weight."""

    CAD_PER_M3_LOG = "CAD_per_m3_log"
    CAD_PER_ODT_RESIDUE = "CAD_per_odt_residue"
    CAD_PER_HA = "CAD_per_ha"
    CAD_PER_GJ = "CAD_per_gj"

    @property
    def quantity_unit(self) -> QuantityUnit:
        return {
            Unit.CAD_PER_M3_LOG: QuantityUnit.M3_LOG,
            Unit.CAD_PER_ODT_RESIDUE: QuantityUnit.ODT,
            Unit.CAD_PER_HA: QuantityUnit.HA,
            Unit.CAD_PER_GJ: QuantityUnit.GJ,
        }[self]


class QuantityUnit(StrEnum):
    """Physical quantity named by ``volume_m3`` and the rate denominator.

    ``M3_APPARENT`` is a volume basis, never a rate unit: a per-apparent-m3 row
    must still use ``CAD_per_m3_log`` and carry an apparent-to-solid conversion.
    """

    M3_LOG = "m3_log"
    ODT = "odt"
    HA = "ha"
    GJ = "gj"
    M3_APPARENT = "m3_apparent"


class VolumeBasis(StrEnum):
    M3_SOLID = "m3_solid"
    M3_APPARENT = "m3_apparent"


class Evidence(StrEnum):
    OBSERVED_PAYMENT = "observed_payment"
    OBSERVED_SALE = "observed_sale"
    OBSERVED_PRICE = "observed_price"
    ADMINISTERED_RATE = "administered_rate"
    APPRAISAL_ALLOWANCE = "appraisal_allowance"
    PUBLISHED_AVERAGE = "published_average"
    MODEL_DERIVED = "model_derived"
    MODEL_INPUT = "model_input"
    FALLBACK = "fallback"


OBSERVED_EVIDENCES = frozenset(
    {
        Evidence.OBSERVED_PAYMENT,
        Evidence.OBSERVED_SALE,
        Evidence.OBSERVED_PRICE,
    }
)


class PricePoint(StrEnum):
    STANDING = "standing"
    ROADSIDE = "roadside"
    MILL_GATE = "mill_gate"
    FACILITY_GATE = "facility_gate"
    NA = "na"


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Product(StrEnum):
    SAWLOG = "sawlog"
    PEELER = "peeler"
    PULPWOOD = "pulpwood"
    MIXED_LOG = "mixed_log"
    HARVEST_RESIDUE = "harvest_residue"
    MILL_RESIDUE = "mill_residue"
    HOG_FUEL = "hog_fuel"
    BIOMASS = "biomass"
    ALL = "all"


class Quality(StrEnum):
    """QC/BC log quality class; ``NA`` means no class is recorded."""

    F1 = "F1"
    F2 = "F2"
    F3 = "F3"
    F4 = "F4"
    DER = "DER"
    PATE = "PATE"
    AUTRE = "AUTRE"
    MIXED = "MIXED"
    NA = "NA"


SIGN_MATRIX: dict[Perspective, dict[Component, Sign]] = {
    Perspective.CROWN: {
        Component.STUMPAGE_BASE: Sign.POSITIVE,
        Component.STUMPAGE_BID_PREMIUM: Sign.POSITIVE,
        Component.STUMPAGE_TOTAL: Sign.POSITIVE,
        Component.ADMIN_FEE: Sign.POSITIVE,
        Component.CROWN_OTHER: Sign.POSITIVE,
        Component.CROWN_CREDIT: Sign.NEGATIVE,
    },
    Perspective.FOREST_SECTOR: {
        Component.DELIVERED_LOG_REVENUE: Sign.POSITIVE,
        Component.RESIDUE_REVENUE: Sign.POSITIVE,
        Component.STUMPAGE_BASE: Sign.NEGATIVE,
        Component.STUMPAGE_BID_PREMIUM: Sign.NEGATIVE,
        Component.STUMPAGE_TOTAL: Sign.NEGATIVE,
        Component.HARVEST: Sign.NEGATIVE,
        Component.ROAD: Sign.NEGATIVE,
        Component.HAUL: Sign.NEGATIVE,
        Component.SILVICULTURE: Sign.NEGATIVE,
        Component.RESIDUE_RECOVERY: Sign.NEGATIVE,
        Component.OVERHEAD: Sign.NEGATIVE,
        Component.ADMIN_FEE: Sign.NEGATIVE,
        Component.CROWN_OTHER: Sign.NEGATIVE,
        Component.CROWN_CREDIT: Sign.POSITIVE,
    },
}

STUMPAGE_COMPONENTS = frozenset(
    {
        Component.STUMPAGE_BASE,
        Component.STUMPAGE_BID_PREMIUM,
        Component.STUMPAGE_TOTAL,
    }
)
RESIDUE_COMPONENTS = frozenset(
    {Component.RESIDUE_REVENUE, Component.RESIDUE_RECOVERY}
)
TRANSFER_COMPONENTS = frozenset(
    {
        Component.STUMPAGE_BASE,
        Component.STUMPAGE_BID_PREMIUM,
        Component.STUMPAGE_TOTAL,
        Component.ADMIN_FEE,
        Component.CROWN_OTHER,
        Component.CROWN_CREDIT,
    }
)
LOG_PRODUCTS = frozenset(
    {
        Product.SAWLOG,
        Product.PEELER,
        Product.PULPWOOD,
        Product.MIXED_LOG,
    }
)
RESIDUE_PRODUCTS = frozenset(
    {Product.HARVEST_RESIDUE, Product.HOG_FUEL, Product.BIOMASS}
)

# Quality classes differentiate log value.  They do not attach to residue,
# area, or generic all-product cost rows.  The class-to-product map preserves
# the QC MERIS meaning: DER is peeling (peeler), F1-F4 are sawlog grades,
# PATE is pulpwood, AUTRE is other/mixed, and MIXED is a mixed log lot.
LOG_VALUE_COMPONENTS = frozenset(
    {
        Component.DELIVERED_LOG_REVENUE,
        Component.STUMPAGE_BASE,
        Component.STUMPAGE_BID_PREMIUM,
        Component.STUMPAGE_TOTAL,
    }
)
QUALITY_PRODUCTS: dict[Quality, frozenset[Product]] = {
    Quality.F1: frozenset({Product.SAWLOG}),
    Quality.F2: frozenset({Product.SAWLOG}),
    Quality.F3: frozenset({Product.SAWLOG}),
    Quality.F4: frozenset({Product.SAWLOG}),
    Quality.DER: frozenset({Product.PEELER}),
    Quality.PATE: frozenset({Product.PULPWOOD}),
    Quality.AUTRE: frozenset({Product.MIXED_LOG}),
    Quality.MIXED: frozenset({Product.MIXED_LOG}),
}

# These source pairs must never be additive for the same component/sale.
SOURCE_PAIR_RULES: tuple[tuple[frozenset[str], str], ...] = (
    (
        frozenset({"bcts_auction_results", "bc_hbs"}),
        "BCTS auction results and BC HBS billing",
    ),
    (
        frozenset({"qc_bmmb_sales", "qc_tarification"}),
        "QC BMMB sales and QC tarification",
    ),
)

_YEAR_RE = re.compile(r"\d{4}")
_REAL_CURRENCY_RE = re.compile(r"CAD_real_(\d{4})")
_TOKEN_KEY_RE = re.compile(r"[a-z][a-z0-9_]*")
_SOURCE_RE = re.compile(r"[a-z][a-z0-9_]*")
_SPECIES_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
_SPATIAL_UNIT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]*")
_LICENCE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]*")
_SNAKE_RE = re.compile(r"[a-z][a-z0-9_]*")


@dataclass(frozen=True, slots=True)
class LedgerIssue:
    line: int | None
    field: str
    message: str

    def __str__(self) -> str:
        line = f"line {self.line}" if self.line is not None else "ledger"
        return f"{line} [{self.field}]: {self.message}"


class LedgerValidationError(ValueError):
    """The whole batch failed; no valid subset is returned."""

    def __init__(self, issues: Iterable[LedgerIssue] | LedgerIssue | str):
        if isinstance(issues, str):
            issues = (LedgerIssue(None, "ledger", issues),)
        elif isinstance(issues, LedgerIssue):
            issues = (issues,)
        self.issues = tuple(issues)
        if not self.issues:
            raise ValueError("LedgerValidationError requires at least one issue")
        super().__init__("\n".join(str(issue) for issue in self.issues))


@dataclass(frozen=True, slots=True)
class ValueBasis:
    evidence: Evidence
    price_point: PricePoint

    @classmethod
    def parse(cls, raw: str) -> ValueBasis:
        parts = raw.split(":")
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise ValueError(
                f"value_basis {raw!r} must be '<evidence>:<price_point>'"
            )
        try:
            evidence = Evidence(parts[0])
        except ValueError:
            raise ValueError(
                f"unknown value_basis evidence {parts[0]!r}; expected one of "
                + ", ".join(item.value for item in Evidence)
            ) from None
        try:
            price_point = PricePoint(parts[1])
        except ValueError:
            raise ValueError(
                f"unknown value_basis price_point {parts[1]!r}; expected one of "
                + ", ".join(item.value for item in PricePoint)
            ) from None
        if evidence is Evidence.FALLBACK and price_point is not PricePoint.NA:
            raise ValueError("fallback evidence requires price_point 'na'")
        if price_point is PricePoint.NA and evidence not in {
            Evidence.FALLBACK,
            Evidence.MODEL_DERIVED,
            Evidence.MODEL_INPUT,
        }:
            raise ValueError(
                f"price_point 'na' is not valid for evidence {evidence.value!r}"
            )
        return cls(evidence=evidence, price_point=price_point)

    def __str__(self) -> str:
        return f"{self.evidence.value}:{self.price_point.value}"


@dataclass(frozen=True, slots=True)
class Licence:
    value: str

    def __post_init__(self) -> None:
        if self.value == "none":
            return
        if not _LICENCE_RE.fullmatch(self.value):
            raise ValueError(
                "licence must be a compact id ('BCTS-...', 'TFL...', 'UA...', "
                "'LOT...') or 'none' for an aggregate row"
            )

    @property
    def is_aggregate(self) -> bool:
        return self.value == "none"

    def __str__(self) -> str:
        return self.value


def parse_notes(notes: str, *, line_number: int | None = None) -> dict[str, str]:
    """Parse ``key=value`` tokens from semicolon-separated free text.

    Free-text chunks without ``=`` are ignored.  Duplicate token keys are an
    error because they make the row's meaning ambiguous.
    """
    tokens: dict[str, str] = {}
    for chunk in notes.split(";"):
        chunk = chunk.strip()
        if not chunk or "=" not in chunk:
            continue
        key, value = chunk.split("=", 1)
        key = key.strip()
        if not _TOKEN_KEY_RE.fullmatch(key):
            continue
        if key in tokens:
            raise LedgerValidationError(
                LedgerIssue(
                    line_number,
                    "notes",
                    f"duplicate notes token {key!r}",
                )
            )
        tokens[key] = value.strip()
    return tokens


@dataclass(frozen=True, slots=True)
class CostRevenueRow:
    jurisdiction: Jurisdiction
    source: str
    year: int
    spatial_unit: str
    species: str
    product: Product
    volume_m3: float
    unit: Unit
    value_cad: float
    value_basis: ValueBasis
    perspective: Perspective
    component: Component
    sign: Sign
    confidence: Confidence
    licence: Licence
    notes: str
    quality: Quality = Quality.NA
    dbh_cm: float | None = None
    line_number: int | None = field(default=None, compare=False, repr=False)

    @property
    def key(self) -> tuple[object, ...]:
        return (
            self.jurisdiction,
            self.source,
            self.year,
            self.spatial_unit,
            self.species,
            self.product,
            self.quality.value,
            self.dbh_cm,
            self.perspective,
            self.component,
            self.licence.value,
        )

    @property
    def coverage_key(self) -> tuple[object, ...]:
        return (
            self.jurisdiction,
            self.year,
            self.spatial_unit,
            self.species,
            self.product,
            self.quality.value,
            self.dbh_cm,
            self.licence.value,
            self.perspective,
            self.component,
        )

    @property
    def notes_tokens(self) -> Mapping[str, str]:
        return parse_notes(self.notes, line_number=self.line_number)

    @property
    def mirror_requested(self) -> bool:
        return self.notes_tokens.get("mirror") == "1"

    @property
    def is_comparison_only(self) -> bool:
        return self.notes_tokens.get("comparison_only") == "1"

    @property
    def volume_basis(self) -> str | None:
        return self.notes_tokens.get("volume_basis")


@dataclass(frozen=True, slots=True)
class Ledger:
    rows: tuple[CostRevenueRow, ...]

    def __len__(self) -> int:
        return len(self.rows)

    def __iter__(self):
        return iter(self.rows)

    @property
    def is_empty(self) -> bool:
        return not self.rows

    def by_key(self) -> dict[tuple[object, ...], CostRevenueRow]:
        return {row.key: row for row in self.rows}


def _format_key(key: Sequence[object]) -> str:
    return "/".join(str(part) for part in key)


def _canonical_source(source: str) -> str:
    return source.removeprefix("prov_")


def _parse_row(
    record: Mapping[str, str | None], *, line_number: int
) -> CostRevenueRow:
    def fail(field_name: str, message: str) -> None:
        raise LedgerValidationError(LedgerIssue(line_number, field_name, message))

    def text(field_name: str) -> str:
        raw = record.get(field_name)
        if raw is None or not raw.strip():
            fail(field_name, "required field is missing or blank")
        return raw.strip()

    def optional_text(field_name: str) -> str:
        raw = record.get(field_name)
        return raw.strip() if raw is not None else ""

    def number(
        field_name: str, *, positive: bool = False, nonnegative: bool = False
    ) -> float:
        raw = text(field_name)
        try:
            value = float(raw)
        except ValueError:
            fail(field_name, f"expected a number, got {raw!r}")
        if not math.isfinite(value):
            fail(field_name, "must be finite; NaN and infinity are rejected")
        if positive and value <= 0:
            fail(field_name, f"must be > 0, got {value}")
        if nonnegative and value < 0:
            fail(field_name, f"must be >= 0, got {value}")
        return value

    def enum(enum_type, field_name: str, raw: str):
        try:
            return enum_type(raw)
        except ValueError:
            choices = ", ".join(item.value for item in enum_type)
            fail(field_name, f"unknown value {raw!r}; expected one of {choices}")

    jurisdiction = enum(Jurisdiction, "jurisdiction", text("jurisdiction"))
    source = text("source")
    if not _SOURCE_RE.fullmatch(source):
        fail(
            "source",
            f"source {source!r} must be lowercase snake_case and registered or "
            "'prov_<id>'",
        )

    year_raw = text("year")
    if not _YEAR_RE.fullmatch(year_raw):
        fail("year", f"year {year_raw!r} must be a four-digit integer")
    year = int(year_raw)
    if not 1900 <= year <= 2100:
        fail("year", f"year {year} is outside 1900..2100")

    spatial_unit = text("spatial_unit")
    if not _SPATIAL_UNIT_RE.fullmatch(spatial_unit):
        fail(
            "spatial_unit",
            f"spatial_unit {spatial_unit!r} must be a compact stable id, not "
            "free text or coordinates",
        )

    species = text("species")
    if not _SPECIES_RE.fullmatch(species):
        fail("species", f"species {species!r} must be a canonical code or 'all'")

    product = enum(Product, "product", text("product"))
    volume_m3 = number("volume_m3", positive=True)
    unit = enum(Unit, "unit", text("unit"))
    value_cad = number("value_cad", nonnegative=True)

    quality_raw = optional_text("quality")
    quality = (
        Quality.NA if not quality_raw else enum(Quality, "quality", quality_raw)
    )
    dbh_raw = optional_text("dbh_cm")
    dbh_cm = (
        None
        if not dbh_raw or dbh_raw == Quality.NA.value
        else number("dbh_cm", positive=True)
    )

    value_basis_raw = text("value_basis")
    try:
        value_basis = ValueBasis.parse(value_basis_raw)
    except ValueError as exc:
        fail("value_basis", str(exc))

    perspective = enum(Perspective, "perspective", text("perspective"))
    component = enum(Component, "component", text("component"))

    sign_raw = text("sign")
    try:
        sign = Sign(int(sign_raw))
    except ValueError:
        fail("sign", f"sign {sign_raw!r} must be 1 or -1")
    confidence = enum(Confidence, "confidence", text("confidence"))

    licence_raw = text("licence")
    try:
        licence = Licence(licence_raw)
    except ValueError as exc:
        fail("licence", str(exc))

    notes = text("notes")
    tokens = parse_notes(notes, line_number=line_number)

    expected_sign = SIGN_MATRIX[perspective].get(component)
    if expected_sign is None:
        fail(
            "component",
            f"component {component.value!r} is outside the "
            f"{perspective.value!r} perspective boundary",
        )
    if sign is not expected_sign:
        fail(
            "sign",
            f"component {component.value!r} under {perspective.value!r} must "
            f"have sign {int(expected_sign)}, got {int(sign)}",
        )

    if product is Product.MILL_RESIDUE:
        fail(
            "product",
            "mill_residue belongs in a separate processing ledger and must not "
            "enter the harvest cost/revenue ledger",
        )
    if product is Product.ALL and component in RESIDUE_COMPONENTS:
        fail(
            "product",
            f"component {component.value!r} requires an explicit residue product",
        )
    if component in RESIDUE_COMPONENTS:
        if product not in RESIDUE_PRODUCTS:
            fail(
                "product",
                f"component {component.value!r} requires a residue product "
                f"({', '.join(item.value for item in sorted(RESIDUE_PRODUCTS, key=str))})",
            )
    elif product not in LOG_PRODUCTS and product is not Product.ALL:
        fail(
            "product",
            f"component {component.value!r} requires a log product or 'all'",
        )

    if unit is Unit.CAD_PER_M3_LOG:
        if component in RESIDUE_COMPONENTS:
            fail(
                "unit",
                f"component {component.value!r} cannot use CAD_per_m3_log",
            )
    elif unit is Unit.CAD_PER_ODT_RESIDUE:
        if component not in RESIDUE_COMPONENTS:
            fail(
                "unit",
                "CAD_per_odt_residue is valid only for residue_revenue or "
                "residue_recovery",
            )
    elif unit is Unit.CAD_PER_HA:
        if component is not Component.SILVICULTURE:
            fail(
                "unit",
                "CAD_per_ha is valid only for silviculture",
            )
    elif unit is Unit.CAD_PER_GJ:
        if component not in RESIDUE_COMPONENTS:
            fail(
                "unit",
                "CAD_per_gj is valid only for residue_revenue or residue_recovery",
            )
        if "conversion_source" not in tokens:
            fail(
                "notes",
                "CAD_per_gj requires a registered conversion_source=... token",
            )

    if quality is not Quality.NA:
        if unit is not Unit.CAD_PER_M3_LOG:
            fail(
                "unit",
                f"quality {quality.value!r} is valid only for CAD_per_m3_log",
            )
        if component not in LOG_VALUE_COMPONENTS:
            fail(
                "component",
                f"quality {quality.value!r} is valid only for log value "
                "components (delivered_log_revenue, stumpage_base, "
                "stumpage_bid_premium, stumpage_total)",
            )
        allowed_products = QUALITY_PRODUCTS[quality]
        if product not in allowed_products:
            fail(
                "quality",
                f"quality {quality.value!r} requires product "
                f"{', '.join(item.value for item in sorted(allowed_products, key=str))}; "
                f"got {product.value!r}",
            )

    if dbh_cm is not None:
        if value_basis.evidence is not Evidence.MODEL_INPUT:
            fail(
                "dbh_cm",
                "dbh_cm is valid only for model-input species/DBH revenue "
                "curves, not observed sales or costs",
            )
        if component is not Component.DELIVERED_LOG_REVENUE:
            fail(
                "dbh_cm",
                "dbh_cm is valid only for a delivered_log_revenue curve row",
            )
        if product not in LOG_PRODUCTS:
            fail(
                "dbh_cm",
                "dbh_cm requires an explicit log product (not 'all' or residue)",
            )
        if unit is not Unit.CAD_PER_M3_LOG:
            fail("dbh_cm", "dbh_cm requires CAD_per_m3_log")
        if quality is not Quality.NA:
            fail(
                "dbh_cm",
                "a species/DBH curve row cannot also carry a quality class; "
                "store the curve coordinate without a quality",
            )

    if source.startswith("prov_") and confidence is Confidence.HIGH:
        fail(
            "confidence",
            "a provisional source (prov_<id>) cannot exceed medium confidence",
        )
    if species == "spp" and confidence is not Confidence.LOW:
        fail(
            "species",
            "unresolved mixed species 'spp' requires low confidence",
        )

    if value_basis.evidence is Evidence.MODEL_INPUT:
        if tokens.get("model_input") != "1":
            fail(
                "notes",
                "model_input evidence requires an explicit model_input=1 token",
            )
        if tokens.get("not_observed") != "1":
            fail(
                "notes",
                "model_input evidence requires not_observed=1; a model input "
                "is not an observed transaction",
            )
        if confidence is Confidence.HIGH:
            fail(
                "confidence",
                "model_input evidence cannot use high confidence; high is "
                "reserved for observed payments, sales, or audited actuals",
            )

    if (
        tokens.get("model_input") == "1"
        and value_basis.evidence is not Evidence.MODEL_INPUT
    ):
        fail(
            "notes",
            "model_input=1 token requires value_basis evidence "
            f"'model_input', got {value_basis.evidence.value!r}",
        )
    if (
        tokens.get("not_observed") == "1"
        and value_basis.evidence in OBSERVED_EVIDENCES
    ):
        fail(
            "notes",
            f"not_observed=1 token conflicts with observed value_basis "
            f"evidence {value_basis.evidence.value!r}",
        )

    if (
        component in STUMPAGE_COMPONENTS
        and value_basis.price_point is not PricePoint.STANDING
    ):
        fail(
            "value_basis",
            f"stumpage component {component.value!r} must use price_point "
            "'standing'",
        )
    if component is Component.DELIVERED_LOG_REVENUE and value_basis.price_point not in {
        PricePoint.MILL_GATE,
        PricePoint.ROADSIDE,
    }:
        fail(
            "value_basis",
            "delivered_log_revenue must use price_point 'mill_gate' or "
            "'roadside'; a standing bid is not a log price",
        )
    if component is Component.RESIDUE_REVENUE and value_basis.price_point not in {
        PricePoint.FACILITY_GATE,
        PricePoint.ROADSIDE,
    }:
        fail(
            "value_basis",
            "residue_revenue must use price_point 'facility_gate' or "
            "'roadside'",
        )

    currency = tokens.get("currency_basis")
    if currency is None:
        fail("notes", "missing required currency_basis=... token")
    if currency != "CAD_nominal":
        match = _REAL_CURRENCY_RE.fullmatch(currency)
        if match is None:
            fail(
                "notes",
                "currency_basis must be CAD_nominal or CAD_real_<yyyy>",
            )
        real_year = int(match.group(1))
        if not 1900 <= real_year <= 2100:
            fail("notes", f"currency_basis year {real_year} is outside 1900..2100")
    observed_year = tokens.get("observed_year")
    if observed_year is not None:
        if not _YEAR_RE.fullmatch(observed_year):
            fail("notes", "observed_year must be a four-digit year")
        if not 1900 <= int(observed_year) <= 2100:
            fail("notes", f"observed_year {observed_year} is outside 1900..2100")

    if ("conversion" in tokens) != ("conversion_source" in tokens):
        fail(
            "notes",
            "conversion=... and conversion_source=... must appear together",
        )

    volume_basis = tokens.get("volume_basis")
    if volume_basis is not None:
        try:
            VolumeBasis(volume_basis)
        except ValueError:
            fail(
                "notes",
                "volume_basis must be m3_solid or m3_apparent",
            )
        if volume_basis == VolumeBasis.M3_APPARENT:
            if unit is not Unit.CAD_PER_M3_LOG:
                fail(
                    "notes",
                    "volume_basis=m3_apparent is valid only for CAD_per_m3_log",
                )
            if "conversion" not in tokens or "conversion_source" not in tokens:
                fail(
                    "notes",
                    "volume_basis=m3_apparent requires conversion=... and "
                    "conversion_source=...",
                )

    coverage = tokens.get("coverage")
    coverage_denominator = tokens.get("coverage_denominator")
    if (coverage is None) != (coverage_denominator is None):
        fail(
            "notes",
            "coverage=... and coverage_denominator=... must appear together",
        )
    if coverage is not None:
        if coverage not in {"sample", "partial", "full"}:
            fail(
                "notes",
                "coverage must be sample, partial, or full",
            )
        if not coverage_denominator:
            fail("notes", "coverage_denominator must not be blank")
    coverage_share = tokens.get("coverage_share")
    if coverage_share is not None:
        try:
            share = float(coverage_share)
        except ValueError:
            fail("notes", f"coverage_share {coverage_share!r} is not numeric")
        if not math.isfinite(share) or not 0.0 <= share <= 1.0:
            fail("notes", "coverage_share must be finite and within [0, 1]")
        if coverage is None:
            fail("notes", "coverage_share requires coverage and coverage_denominator")

    aggregate_count = tokens.get("aggregated_from")
    if licence.is_aggregate and aggregate_count is None:
        fail(
            "licence",
            "licence='none' is valid only for an aggregate row with "
            "notes aggregated_from=<n>",
        )
    if aggregate_count is not None:
        if not licence.is_aggregate:
            fail(
                "notes",
                "aggregated_from=... is valid only when licence='none'",
            )
        try:
            count = int(aggregate_count)
        except ValueError:
            fail("notes", "aggregated_from must be a positive integer")
        if count <= 0:
            fail("notes", "aggregated_from must be a positive integer")

    comparison_only = tokens.get("comparison_only")
    not_additive_with = tokens.get("not_additive_with")
    if (comparison_only is None) != (not_additive_with is None):
        fail(
            "notes",
            "comparison_only=1 and not_additive_with=<source> must appear together",
        )
    if comparison_only is not None and comparison_only != "1":
        fail("notes", "comparison_only must be '1'")

    mirror = tokens.get("mirror")
    if mirror is not None:
        if mirror != "1":
            fail("notes", "mirror must be '1' when present")
        if component not in TRANSFER_COMPONENTS:
            fail(
                "mirror",
                f"component {component.value!r} is not a Crown/sector transfer; "
                "mirror=1 is valid only for genuine transfers",
            )

    if not notes.strip():
        fail("notes", "notes must name the source table or page")

    return CostRevenueRow(
        jurisdiction=jurisdiction,
        source=source,
        year=year,
        spatial_unit=spatial_unit,
        species=species,
        product=product,
        volume_m3=volume_m3,
        unit=unit,
        value_cad=value_cad,
        value_basis=value_basis,
        perspective=perspective,
        component=component,
        sign=sign,
        confidence=confidence,
        licence=licence,
        notes=notes,
        quality=quality,
        dbh_cm=dbh_cm,
        line_number=line_number,
    )


def _mirror_counterparts(
    row: CostRevenueRow, rows: Sequence[CostRevenueRow]
) -> list[CostRevenueRow]:
    matches: list[CostRevenueRow] = []
    for other in rows:
        if other is row or other.mirror_requested:
            continue
        if other.perspective is row.perspective or other.sign is row.sign:
            continue
        if other.component is not row.component:
            continue
        if other.jurisdiction is not row.jurisdiction or other.year != row.year:
            continue
        if other.spatial_unit != row.spatial_unit or other.species != row.species:
            continue
        if other.product is not row.product or other.unit is not row.unit:
            continue
        if other.quality is not row.quality or other.dbh_cm != row.dbh_cm:
            continue
        if other.licence.value != row.licence.value:
            continue
        if other.value_basis != row.value_basis:
            continue
        if not math.isclose(other.volume_m3, row.volume_m3, rel_tol=0.0, abs_tol=1e-9):
            continue
        if not math.isclose(other.value_cad, row.value_cad, rel_tol=1e-9, abs_tol=1e-9):
            continue
        matches.append(other)
    return matches


def validate_rows(rows: Sequence[CostRevenueRow]) -> None:
    """Run cross-row checks; raise one aggregated LedgerValidationError."""
    issues: list[LedgerIssue] = []

    by_key: dict[tuple[object, ...], list[CostRevenueRow]] = defaultdict(list)
    for row in rows:
        by_key[row.key].append(row)
    for key, group in by_key.items():
        if len(group) > 1:
            lines = ", ".join(str(item.line_number) for item in group)
            issues.append(
                LedgerIssue(
                    group[0].line_number,
                    "key",
                    f"duplicate ledger key {_format_key(key)} on lines {lines}",
                )
            )

    stumpage_groups: dict[tuple[object, ...], set[Component]] = defaultdict(set)
    for row in rows:
        if row.component not in STUMPAGE_COMPONENTS:
            continue
        key = (
            row.jurisdiction,
            row.year,
            row.spatial_unit,
            row.species,
            row.product,
            row.quality.value,
            row.dbh_cm,
            row.licence.value,
        )
        stumpage_groups[key].add(row.component)
    for key, components in stumpage_groups.items():
        if Component.STUMPAGE_TOTAL in components and components & {
            Component.STUMPAGE_BASE,
            Component.STUMPAGE_BID_PREMIUM,
        }:
            issues.append(
                LedgerIssue(
                    None,
                    "component",
                    "stumpage_total is mutually exclusive with stumpage_base and "
                    f"stumpage_bid_premium for {_format_key(key)}",
                )
            )

    coverage_groups: dict[tuple[object, ...], list[CostRevenueRow]] = defaultdict(list)
    transaction_groups: dict[tuple[object, ...], list[CostRevenueRow]] = defaultdict(
        list
    )
    for row in rows:
        coverage_groups[row.coverage_key].append(row)
        transaction_key = (
            row.jurisdiction,
            row.year,
            row.spatial_unit,
            row.species,
            row.product,
            row.quality.value,
            row.dbh_cm,
            row.licence.value,
            row.component,
        )
        transaction_groups[transaction_key].append(row)

    for key, group in coverage_groups.items():
        sources = {row.source for row in group}
        additive = [row for row in group if not row.is_comparison_only]
        comparisons = [row for row in group if row.is_comparison_only]
        if len(sources) == 1:
            for row in comparisons:
                issues.append(
                    LedgerIssue(
                        row.line_number,
                        "notes",
                        "comparison_only row has no additive source in its "
                        f"coverage group {_format_key(key)}",
                    )
                )
            continue
        if len(additive) > 1:
            issues.append(
                LedgerIssue(
                    additive[0].line_number,
                    "source",
                    "source exclusivity violation: additive sources "
                    f"{sorted(row.source for row in additive)} cover the same "
                    f"key {_format_key(key)}; exactly one source is additive",
                )
            )
        if not additive:
            issues.append(
                LedgerIssue(
                    group[0].line_number,
                    "notes",
                    f"all sources are comparison_only for {_format_key(key)}; "
                    "one additive source is required",
                )
            )
        additive_sources = {_canonical_source(row.source) for row in additive}
        for row in comparisons:
            target = _canonical_source(row.notes_tokens["not_additive_with"])
            if target not in additive_sources:
                issues.append(
                    LedgerIssue(
                        row.line_number,
                        "notes",
                        f"not_additive_with={row.notes_tokens['not_additive_with']!r} "
                        f"does not name an additive source for {_format_key(key)}",
                    )
                )

    for key, group in transaction_groups.items():
        additive = [row for row in group if not row.is_comparison_only]
        canonical_sources = {_canonical_source(row.source) for row in additive}
        for pair, label in SOURCE_PAIR_RULES:
            if pair <= canonical_sources:
                issues.append(
                    LedgerIssue(
                        additive[0].line_number,
                        "source",
                        f"{label} are both additive for {_format_key(key)}; "
                        "use one source and mark the other comparison_only or "
                        "partition the volume",
                    )
                )

    currency_groups: dict[tuple[object, ...], set[str]] = defaultdict(set)
    volume_basis_groups: dict[tuple[object, ...], set[str]] = defaultdict(set)
    for row in rows:
        key = (
            row.jurisdiction,
            row.year,
            row.spatial_unit,
            row.species,
            row.product,
            row.quality.value,
            row.dbh_cm,
            row.licence.value,
            row.perspective,
        )
        currency = row.notes_tokens["currency_basis"]
        currency_groups[key].add(
            "nominal" if currency == "CAD_nominal" else "real"
        )
        if row.volume_basis is not None:
            volume_basis_groups[key].add(row.volume_basis)
    for key, bases in currency_groups.items():
        if len(bases) > 1:
            issues.append(
                LedgerIssue(
                    None,
                    "notes",
                    "nominal and constant-dollar rows are mixed for "
                    f"{_format_key(key)}; deflate in a derived view, not in the "
                    "audit ledger",
                )
            )
    for key, bases in volume_basis_groups.items():
        if len(bases) > 1:
            issues.append(
                LedgerIssue(
                    None,
                    "notes",
                    "m3_solid and m3_apparent rows are mixed for "
                    f"{_format_key(key)}; convert with a registered factor first",
                )
            )

    mirror_rows = [row for row in rows if row.mirror_requested]
    mirror_groups: dict[tuple[object, ...], list[CostRevenueRow]] = defaultdict(list)
    for row in mirror_rows:
        mirror_key = (
            row.jurisdiction,
            row.year,
            row.spatial_unit,
            row.species,
            row.product,
            row.quality.value,
            row.dbh_cm,
            row.licence.value,
            row.component,
            row.unit,
        )
        mirror_groups[mirror_key].append(row)
    for key, group in mirror_groups.items():
        if len(group) > 1:
            lines = ", ".join(str(row.line_number) for row in group)
            issues.append(
                LedgerIssue(
                    group[0].line_number,
                    "mirror",
                    f"duplicated mirror marker on lines {lines} for "
                    f"{_format_key(key)}",
                )
            )
    for row in mirror_rows:
        counterparts = _mirror_counterparts(row, rows)
        if not counterparts:
            issues.append(
                LedgerIssue(
                    row.line_number,
                    "mirror",
                    "mirror row has no matching opposite-perspective row with "
                    "the same component, licence, year, unit, volume, value "
                    "basis, and absolute value",
                )
            )
        elif len(counterparts) > 1:
            lines = ", ".join(str(item.line_number) for item in counterparts)
            issues.append(
                LedgerIssue(
                    row.line_number,
                    "mirror",
                    f"mirror row matches multiple counterparts on lines {lines}; "
                    "the mirror is ambiguous",
                )
            )

    if issues:
        raise LedgerValidationError(issues)


def validate_ledger(ledger: Ledger) -> None:
    validate_rows(ledger.rows)


def load_ledger(path: str | Path, *, validate: bool = True) -> Ledger:
    """Load and validate ``forest_cost_revenue.csv``; never return partial data."""
    path = Path(path)
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        header = reader.fieldnames
        if header is None:
            raise LedgerValidationError(
                LedgerIssue(None, "header", f"{path} is empty; expected a header")
            )
        issues: list[LedgerIssue] = []
        missing = [name for name in CORE_LEDGER_FIELDS if name not in header]
        unknown = [name for name in header if name not in LEDGER_FIELDS]
        duplicates = sorted({name for name in header if header.count(name) > 1})
        if missing:
            issues.append(
                LedgerIssue(None, "header", f"missing required fields: {missing}")
            )
        if unknown:
            issues.append(
                LedgerIssue(None, "header", f"unknown fields: {unknown}")
            )
        if duplicates:
            issues.append(
                LedgerIssue(None, "header", f"duplicate fields: {duplicates}")
            )
        if issues:
            raise LedgerValidationError(issues)

        rows: list[CostRevenueRow] = []
        for line_number, record in enumerate(reader, start=2):
            extra = record.get(None)
            if extra is not None:
                issues.append(
                    LedgerIssue(
                        line_number,
                        "row",
                        f"row has {len(extra)} value(s) beyond the "
                        f"{len(header)} columns",
                    )
                )
                continue
            if all(value is None or not value.strip() for value in record.values()):
                continue
            try:
                rows.append(_parse_row(record, line_number=line_number))
            except LedgerValidationError as exc:
                issues.extend(exc.issues)
    if issues:
        raise LedgerValidationError(issues)

    ledger = Ledger(tuple(rows))
    if validate:
        validate_ledger(ledger)
    return ledger
