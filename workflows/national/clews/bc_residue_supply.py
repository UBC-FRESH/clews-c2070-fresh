"""Build the all-BC woody-residue supply curve for OSeCAN zones B1/B2.

The module is deliberately conservative: every input is read from a named
derived artifact, every transformation is a pure function, and every output
carries the evidence needed to reproduce it.  It does not invent a time path,
a residue ratio, or a cost.

Zones
-----
``B1``
    FRESH-side provisional South Coast / Lower Mainland zone: Georgia
    Depression plus Southern Coastal Mountains.
``B2``
    FRESH-side provisional BC Interior & North zone: the remaining BC
    ecoprovinces, including the BC portions of the Montane Cordillera,
    Boreal Cordillera, Boreal Plains, and Taiga Plains.

C2070/OSeCAN has no published B1/B2 boundary.  The crosswalk in this module is
a FRESH-side provisional definition, not an official model boundary.

Harvest basis
-------------
The primary basis is the 2018 TSA apportionment (the volume actually
apportioned to tenure holders), not the AAC ceiling.  The parsed 36-TSA
artifact omits the Prince George TSA; the primary all-BC scenario therefore
adds the Prince George 2018 apportionment derived from the workbook
``Region 2`` tab.  The parsed-only 36-TSA total (38,726,439 m3/y) and the
all-BC AAC total are emitted as comparison scenarios.

Residue basis
-------------
The primary ratio is the area-weighted national-batch residue ratio
(``residue_odt / log_m3``), resolved by ecoprovince.  The BC all-species
aggregate is 0.080872 ODT/m3.  The TSA24 Stage B mill-residue ratio
(0.259553 ODT/m3) is reported but rejected: it is a mill-processing ratio on
a different mass basis and would double count the log input if applied to the
harvest volume.

Energy basis
------------
``1 ODT`` is one oven-dry tonne.  The dry lower heating value is 18.5 GJ/ODT.
``1 PJ = 1,000,000 GJ``.  No moisture credit is applied to the dry mass; the
model cap is an energy cap on dry residue.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import statistics
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

__all__ = [
    "HORIZON_END",
    "HORIZON_START",
    "LHV_GJ_PER_ODT",
    "PJ_GJ",
    "ZONE_NAMES",
    "BatchRatios",
    "BcResidueSupplyError",
    "Scenario",
    "TsaArea",
    "ZoneCost",
    "ZoneSupply",
    "allocate_harvest",
    "annual_residue_rows",
    "build_activity_limit_rows",
    "build_tsa_crosswalk",
    "build_variable_cost_rows",
    "build_zone_crosswalk",
    "canonical_tsa_name",
    "load_batch_ratios",
    "load_stage_b_costs",
    "load_tsa_areas",
    "load_tsa_volumes",
    "main",
    "zone_for_ecoprovince",
]

HORIZON_START = 2021
HORIZON_END = 2070
LHV_GJ_PER_ODT = 18.5
PJ_GJ = 1_000_000.0
STAGE_B_DENSITY_ODT_M3 = 0.45

ZONE_NAMES = {
    "B1": "South Coast / Lower Mainland",
    "B2": "BC Interior & North",
    "CAN": "Canada national rollup",
}

B1_ECOPROVINCES = frozenset({"Georgia Depression", "Southern Coastal Mountains"})

# One source of truth for the FRESH-side provisional crosswalk.  The
# rationale is written for the data report and is reused by the CSV writer.
_ECOPROVINCE_RATIONALE: dict[str, tuple[str, str]] = {
    "Georgia Depression": (
        "B1",
        (
            "Southern coastal lowland around the Strait of Georgia and the "
            "Lower Mainland; part of the recommended B1 default."
        ),
    ),
    "Southern Coastal Mountains": (
        "B1",
        (
            "Coast Mountains south of the northern coastal divide; part of "
            "the recommended B1 default."
        ),
    ),
    "Northern Coastal Mountains": (
        "B2",
        (
            "North coastal mountains; grouped with the northern interior in "
            "B2, not with the southern coastal B1 default."
        ),
    ),
    "Northern Montane Cordillera": (
        "B2",
        "Interior mountain system; part of the recommended B2 default.",
    ),
    "Central Montane Cordillera": (
        "B2",
        "Interior mountain system; part of the recommended B2 default.",
    ),
    "Southern Montane Cordillera": (
        "B2",
        "Interior mountain system; part of the recommended B2 default.",
    ),
    "Columbia Montane Cordillera": (
        "B2",
        "Interior mountain system; part of the recommended B2 default.",
    ),
    "Northern Boreal Cordillera": (
        "B2",
        "Northern boreal mountain system; part of the recommended B2 default.",
    ),
    "Southern Boreal Cordillera": (
        "B2",
        "Northern boreal mountain system; part of the recommended B2 default.",
    ),
    "Hay-Slave Lowlands": (
        "B2",
        (
            "BC portion of the Taiga Plains; part of the recommended B2 "
            "default. The area is a BC partial, not the full ecoprovince."
        ),
    ),
    "Boreal Foothills": (
        "B2",
        (
            "BC portion of the Boreal Plains foothills; part of the "
            "recommended B2 default."
        ),
    ),
    "Central Boreal Plains": (
        "B2",
        (
            "BC portion of the Boreal Plains; part of the recommended B2 "
            "default. The area is a BC partial, not the full ecoprovince."
        ),
    ),
    "Wrangel Mountains": (
        "B2",
        (
            "Small northern coastal mountain outlier; assigned to B2 for "
            "completeness. The VRI 2025 area weights carry no THLB for it."
        ),
    ),
    "NO_ECOPROVINCE": (
        "B2",
        (
            "Unlabelled 500 m raster sliver (2,337 ha); assigned to B2 by "
            "documented fallback, not by ecological evidence."
        ),
    ),
}

_ECOPROVINCE_TO_ZONE = {
    ecoprovince: zone for ecoprovince, (zone, _) in _ECOPROVINCE_RATIONALE.items()
}

# The parsed TSA apportionment artifact uses short workbook sheet names.  The
# BC area-weight artifact uses the FADM display names.  These aliases are the
# only accepted differences.
_TSA_DISPLAY = (
    "100 Mile House",
    "Arrow",
    "Arrowsmith",
    "Boundary",
    "Bulkley",
    "Cascadia",
    "Cassiar",
    "Cranbrook",
    "Dawson Creek",
    "Fort Nelson",
    "Fort St. John",
    "Fraser",
    "GBR North",
    "GBR South",
    "Golden",
    "Invermere",
    "Kalum",
    "Kamloops",
    "Kispiox",
    "Kootenay Lake",
    "Lakes",
    "Lillooet",
    "MacKenzie",
    "Merritt",
    "Morice",
    "NO_TSA",
    "Nass",
    "North Island",
    "Okanagan",
    "Pacific",
    "Prince George",
    "Queen Charlotte",
    "Quesnel",
    "Revelstoke",
    "Robson Valley",
    "Soo",
    "Sunshine Coast",
    "Williams Lake",
)

_TSA_ALIASES = {
    "100 mile": "100 Mile House",
    "100 mile house": "100 Mile House",
    "fort st john": "Fort St. John",
    "fort st. john": "Fort St. John",
    "great bear north": "GBR North",
    "gbr north": "GBR North",
    "great bear south": "GBR South",
    "gbr south": "GBR South",
    "haida gwaii": "Queen Charlotte",
    "queen charlotte": "Queen Charlotte",
    "mackenzie": "MacKenzie",
    "mac kenzie": "MacKenzie",
    "robson": "Robson Valley",
    "robson valley": "Robson Valley",
    "pg": "Prince George",
    "prince george": "Prince George",
    "no tsa": "NO_TSA",
}

# Prince George is present in the workbook but its ``PG`` sheet has only
# eight year columns, so the generic parser (which requires ten) skips it.
# The 2018 AAC is the ``Prince George`` row of the ``Region 2`` tab; the 2018
# apportionment is the sum of the PG sheet's 2018 apportionment classes.
# Dawson Creek, Fort Nelson, Fort St. John, and Okanagan use an
# ``Indicated Coniferous Apportionment`` header (or a backtick), so the generic
# parser misses their apportionment rows as well.  These five derived values
# make the difference between the parsed 36-TSA artifact and all-BC.
PRINCE_GEORGE_AAC_M3 = 8_375_000.0
PRINCE_GEORGE_APPORTIONMENT_M3 = 11_019_167.5

PARSED_AAC_M3 = 44_399_801.0
PARSED_APPORTIONMENT_M3 = 38_726_439.0
ALL_BC_AAC_M3 = PARSED_AAC_M3 + PRINCE_GEORGE_AAC_M3
MISSING_APPORTIONMENT_M3 = {
    "Dawson Creek": 1_860_000.0,
    "Fort Nelson": 1_625_000.0,
    "Fort St. John": 2_115_010.0,
    "Okanagan": 3_078_406.0,
    "Prince George": PRINCE_GEORGE_APPORTIONMENT_M3,
}
ALL_BC_APPORTIONMENT_M3 = PARSED_APPORTIONMENT_M3 + sum(
    MISSING_APPORTIONMENT_M3.values()
)

# Recomputed 2026-11-18 from the refined area-weighted batch after the species
# alias recovery (the previous constants were from the pre-refinement batch,
# sha256 e0a51d5...; the current batch is fd12a1a...).  The batch totals rose
# because 10,845,346 ha of formerly UNMAPPED_BC area now carries species
# composition.  Keep these in lockstep with the joined batch and its manifest;
# load_batch_ratios fails closed on either mismatch.
BATCH_LOG_M3 = 11_282_585_443.417416
BATCH_RESIDUE_ODT = 823_762_897.5178778
BATCH_JOINED_SHA256 = (
    "fd12a1ace118b951ddf1e82d03c17c4b0e63bf4b288814c09526db371c103e7b"
)
BATCH_BC_ALL_SPECIES_RATIO = BATCH_RESIDUE_ODT / BATCH_LOG_M3

STAGE_B_ODT = 8_325_597.70314148
STAGE_B_ASSIGNED_M3 = 32_076_713.2334
STAGE_B_TOTAL_RATIO = STAGE_B_ODT / STAGE_B_ASSIGNED_M3

INPUT_PATHS = {
    "tsa_apportionment": Path(
        "data/derived/national/assignments/tsa24_pilot/tsa_apportionment_2018.csv"
    ),
    "bc_area_weights": Path(
        "data/derived/national/assortments/bc_area_weights/bc_area_weights.csv"
    ),
    "bc_batch_joined": Path(
        "data/derived/national/assortments/bc_area_weights/"
        "bc_national_batch_area_joined.csv"
    ),
    "stage_b_supply": Path(
        "data/derived/national/assignments/tsa24_pilot/stage_b/residue_supply.csv"
    ),
    "stage_b_transport": Path(
        "data/derived/national/assignments/tsa24_pilot/stage_b/"
        "stage_b_transport_links.csv"
    ),
    "stage_b_demand": Path(
        "data/derived/national/assignments/tsa24_pilot/stage_b/residue_demand.csv"
    ),
    "bc_facility_tsa": Path(
        "data/derived/national/assignments/tsa24_pilot/bc_facility_tsa.csv"
    ),
}

OUTPUT_DIR = Path("data/derived/national/clews_supply/bc_woody_residue")


class BcResidueSupplyError(RuntimeError):
    """Raised when an input cannot be turned into a defensible supply curve."""


# ---------------------------------------------------------------------------
# Small parsing helpers
# ---------------------------------------------------------------------------


def _normalize_tsa_key(value: str) -> str:
    text = " ".join(str(value).strip().lower().replace("_", " ").split())
    for suffix in (" timber supply area", " tsa"):
        if text.endswith(suffix):
            text = text[: -len(suffix)].strip()
    return text.replace(".", "")


_TSA_KEY_TO_DISPLAY = {
    **_TSA_ALIASES,
    **{_normalize_tsa_key(name): name for name in _TSA_DISPLAY},
}


def canonical_tsa_name(value: str) -> str:
    """Return the FADM display name for a workbook or FADM TSA label."""
    key = _normalize_tsa_key(value)
    try:
        return _TSA_KEY_TO_DISPLAY[key]
    except KeyError as error:
        raise BcResidueSupplyError(f"Unknown TSA name: {value!r}") from error


def zone_for_ecoprovince(ecoprovince: str) -> str:
    """Return the provisional B1/B2 zone for a BC ecoprovince."""
    try:
        return _ECOPROVINCE_TO_ZONE[ecoprovince]
    except KeyError as error:
        raise BcResidueSupplyError(
            f"Unmapped ecoprovince: {ecoprovince!r}. Add it to the FRESH-side "
            "crosswalk before building supply."
        ) from error


def _read_csv(path: Path, label: str) -> list[dict[str, str]]:
    if not path.exists():
        raise BcResidueSupplyError(f"Missing {label}: {path}")
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise BcResidueSupplyError(f"Empty CSV header for {label}: {path}")
        rows = list(reader)
    if not rows:
        raise BcResidueSupplyError(f"Empty {label}: {path}")
    return rows


def _number(value: str | float, context: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise BcResidueSupplyError(f"Non-numeric value at {context}: {value!r}") from error
    if not math.isfinite(number):
        raise BcResidueSupplyError(f"Non-finite value at {context}: {value!r}")
    return number


def _require_columns(rows: Sequence[Mapping[str, str]], columns: Iterable[str], label: str) -> None:
    missing = sorted(set(columns) - set(rows[0].keys()))
    if missing:
        raise BcResidueSupplyError(f"{label} is missing columns: {missing}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


# ---------------------------------------------------------------------------
# Typed inputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TsaArea:
    """Area and THLB weights for one TSA, split by ecoprovince."""

    tsa_name: str
    tsa_number: str
    forest_area_ha: Mapping[str, float]
    thlb_area_ha: Mapping[str, float]

    @property
    def total_thlb_ha(self) -> float:
        return sum(self.thlb_area_ha.values())

    @property
    def total_forest_ha(self) -> float:
        return sum(self.forest_area_ha.values())


@dataclass(frozen=True, slots=True)
class BatchRatios:
    """BC area-weighted national-batch residue ratios.

    ``by_ecoprovince`` is the TSA-allocation input; ``by_zone`` is the direct
    matched-cell aggregate used by the refined all-BC chain and by the OSeCAN
    cap.  The two must not be confused: allocating AAC across ecoprovinces by
    TSA area and then applying ecoprovince ratios gives a different zone ratio
    than aggregating the matched batch cells by zone.
    """

    by_ecoprovince: Mapping[str, float]
    by_zone: Mapping[str, float]
    log_m3_by_zone: Mapping[str, float]
    residue_odt_by_zone: Mapping[str, float]
    bc_all_species: float
    log_m3: float
    residue_odt: float
    matched_cells: int
    no_area_cells: int


@dataclass(frozen=True, slots=True)
class ZoneSupply:
    """Annual residue supply for one zone."""

    zone_id: str
    annual_log_m3: float
    residue_odt: float
    residue_pj: float
    ratio_odt_per_m3: float
    ratio_basis: str
    ecoprovince_fallback_count: int


@dataclass(frozen=True, slots=True)
class Scenario:
    """One complete annual residue scenario."""

    label: str
    volume_basis: str
    ratio_mode: str
    zones: Mapping[str, ZoneSupply]
    rows: tuple[dict[str, str], ...]
    audit_rows: tuple[dict[str, str], ...]


@dataclass(frozen=True, slots=True)
class ZoneCost:
    """Delivered residue cost proxy for one zone."""

    zone_id: str
    transport_cad_per_odt: float
    processing_cad_per_odt: float
    total_cad_per_odt: float
    variable_cost_usd_per_gj: float
    source_count: int
    residue_odt_weight: float


# ---------------------------------------------------------------------------
# Input loaders
# ---------------------------------------------------------------------------


def load_tsa_areas(path: Path) -> dict[str, TsaArea]:
    """Load TSA forest and THLB area weights by ecoprovince."""
    rows = _read_csv(path, "BC area weights")
    _require_columns(
        rows,
        ("tsa_number", "tsa_name", "ecoprovince", "area_ha", "thlb_area_ha"),
        "BC area weights",
    )
    numbers: dict[str, str] = {}
    forest: dict[str, defaultdict[str, float]] = defaultdict(lambda: defaultdict(float))
    thlb: dict[str, defaultdict[str, float]] = defaultdict(lambda: defaultdict(float))

    for index, row in enumerate(rows, start=2):
        tsa = row["tsa_name"].strip()
        if not tsa:
            raise BcResidueSupplyError(f"BC area weights row {index}: empty tsa_name")
        ecoprovince = row["ecoprovince"].strip()
        zone_for_ecoprovince(ecoprovince)  # fail fast on an unmapped label
        number = row["tsa_number"].strip()
        existing = numbers.get(tsa)
        if existing is not None and existing != number:
            raise BcResidueSupplyError(
                f"BC area weights row {index}: TSA {tsa!r} has two numbers "
                f"({existing!r}, {number!r})"
            )
        numbers[tsa] = number
        area_ha = _number(row["area_ha"], f"BC area weights row {index} area_ha")
        thlb_ha = _number(row["thlb_area_ha"], f"BC area weights row {index} thlb_area_ha")
        if area_ha < 0 or thlb_ha < 0:
            raise BcResidueSupplyError(
                f"BC area weights row {index}: negative area for {tsa!r}/{ecoprovince!r}"
            )
        forest[tsa][ecoprovince] += area_ha
        thlb[tsa][ecoprovince] += thlb_ha

    return {
        tsa: TsaArea(
            tsa_name=tsa,
            tsa_number=numbers[tsa],
            forest_area_ha=dict(forest[tsa]),
            thlb_area_ha=dict(thlb[tsa]),
        )
        for tsa in sorted(numbers)
    }


def load_tsa_volumes(
    path: Path,
    row_type: str,
    *,
    extra_volumes: Mapping[str, float] | None = None,
    expected_total_m3: float,
    label: str,
) -> dict[str, float]:
    """Load a TSA-level volume basis (AAC or apportionment) from the parsed CSV."""
    rows = _read_csv(path, f"{label} TSA apportionment")
    _require_columns(
        rows,
        ("tsa", "row_type", "period_year", "volume_m3"),
        f"{label} TSA apportionment",
    )
    volumes: defaultdict[str, float] = defaultdict(float)
    positive_rows = 0
    for index, row in enumerate(rows, start=2):
        if row["row_type"] != row_type:
            continue
        year = _number(row["period_year"], f"{label} row {index} period_year")
        if int(year) != 2018:
            continue
        volume = _number(row["volume_m3"], f"{label} row {index} volume_m3")
        if volume <= 0:
            continue
        tsa = canonical_tsa_name(row["tsa"])
        volumes[tsa] += volume
        positive_rows += 1

    if positive_rows == 0:
        raise BcResidueSupplyError(f"{label}: no positive 2018 rows of type {row_type!r}")

    if extra_volumes:
        for tsa, volume in extra_volumes.items():
            if volume < 0:
                raise BcResidueSupplyError(
                    f"{label}: negative extra volume for {tsa!r}"
                )
            volumes[canonical_tsa_name(tsa)] += volume

    total = sum(volumes.values())
    if not math.isclose(total, expected_total_m3, rel_tol=1e-9, abs_tol=1e-6):
        raise BcResidueSupplyError(
            f"{label}: parsed total {total:,.6f} m3/y does not match the "
            f"documented {expected_total_m3:,.6f} m3/y"
        )
    return dict(volumes)


def _verify_batch_manifest(path: Path, digest: str) -> None:
    """Fail closed unless the joined batch matches its sibling manifest."""
    manifest_path = path.parent / "manifest.json"
    if not manifest_path.exists():
        raise BcResidueSupplyError(
            f"Missing BC area-weight manifest for batch check: {manifest_path}"
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    recorded = (
        manifest.get("outputs", {})
        .get(path.name, {})
        .get("sha256")
    )
    if recorded is None:
        raise BcResidueSupplyError(
            f"BC area-weight manifest does not record {path.name}"
        )
    if recorded != digest:
        raise BcResidueSupplyError(
            f"Batch {path.name} sha256 {digest} does not match the manifest "
            f"record {recorded}"
        )


def load_batch_ratios(path: Path) -> BatchRatios:
    """Compute ecoprovince, zone, and all-BC residue ODT per log m3 from the batch join."""
    rows = _read_csv(path, "BC national batch join")
    _require_columns(
        rows,
        ("ecoprovince", "stream", "volume_m3", "residue_odt", "area_status"),
        "BC national batch join",
    )
    log_m3: defaultdict[str, float] = defaultdict(float)
    residue_odt: defaultdict[str, float] = defaultdict(float)
    zone_log: defaultdict[str, float] = defaultdict(float)
    zone_residue: defaultdict[str, float] = defaultdict(float)
    matched_cells: set[str] = set()
    no_area_cells: set[str] = set()

    for index, row in enumerate(rows, start=2):
        ecoprovince = row["ecoprovince"].strip()
        zone = zone_for_ecoprovince(ecoprovince)
        status = row["area_status"].strip()
        if status == "no_area":
            no_area_cells.add(row["cell_key"])
            continue
        if status != "matched":
            raise BcResidueSupplyError(
                f"BC national batch join row {index}: unexpected area_status {status!r}"
            )
        matched_cells.add(row["cell_key"])
        stream = row["stream"].strip()
        if stream == "log":
            volume = _number(row["volume_m3"], f"batch row {index} volume_m3")
            log_m3[ecoprovince] += volume
            zone_log[zone] += volume
        elif stream == "residue":
            residue = _number(row["residue_odt"], f"batch row {index} residue_odt")
            residue_odt[ecoprovince] += residue
            zone_residue[zone] += residue

    total_log = sum(log_m3.values())
    total_residue = sum(residue_odt.values())
    digest = _sha256(path)
    if digest != BATCH_JOINED_SHA256:
        raise BcResidueSupplyError(
            f"Batch {path.name} sha256 {digest} does not match the pinned "
            f"{BATCH_JOINED_SHA256}; update BATCH_LOG_M3, BATCH_RESIDUE_ODT, "
            "BATCH_JOINED_SHA256, and the manifest together"
        )
    _verify_batch_manifest(path, digest)
    if not math.isclose(total_log, BATCH_LOG_M3, rel_tol=1e-9, abs_tol=1.0):
        raise BcResidueSupplyError(
            f"Batch log total {total_log:,.3f} m3 does not match documented "
            f"{BATCH_LOG_M3:,.3f} m3"
        )
    if not math.isclose(total_residue, BATCH_RESIDUE_ODT, rel_tol=1e-9, abs_tol=1.0):
        raise BcResidueSupplyError(
            f"Batch residue total {total_residue:,.3f} ODT does not match documented "
            f"{BATCH_RESIDUE_ODT:,.3f} ODT"
        )

    by_ecoprovince = {
        ecoprovince: residue_odt[ecoprovince] / log_m3[ecoprovince]
        for ecoprovince in log_m3
        if log_m3[ecoprovince] > 0
    }
    by_zone: dict[str, float] = {}
    for zone_id in ("B1", "B2"):
        if zone_log[zone_id] <= 0 or zone_residue[zone_id] <= 0:
            raise BcResidueSupplyError(
                f"Batch zone {zone_id} has no positive matched log/residue"
            )
        by_zone[zone_id] = zone_residue[zone_id] / zone_log[zone_id]
    return BatchRatios(
        by_ecoprovince=by_ecoprovince,
        by_zone=by_zone,
        log_m3_by_zone=dict(zone_log),
        residue_odt_by_zone=dict(zone_residue),
        bc_all_species=total_residue / total_log,
        log_m3=total_log,
        residue_odt=total_residue,
        matched_cells=len(matched_cells),
        no_area_cells=len(no_area_cells),
    )


# ---------------------------------------------------------------------------
# Crosswalks
# ---------------------------------------------------------------------------


def build_zone_crosswalk() -> list[dict[str, str]]:
    """Return the FRESH-side provisional ecoprovince -> B1/B2 crosswalk."""
    rows: list[dict[str, str]] = []
    for ecoprovince in sorted(_ECOPROVINCE_TO_ZONE):
        zone_id, rationale = _ECOPROVINCE_RATIONALE[ecoprovince]
        rows.append(
            {
                "ecoprovince": ecoprovince,
                "zone_id": zone_id,
                "zone_name": ZONE_NAMES[zone_id],
                "rationale": rationale,
                "crosswalk_status": "fresh_provisional",
                "c2070_boundary": "no_published_boundary",
            }
        )
    return rows


def build_tsa_crosswalk(
    areas: Mapping[str, TsaArea],
    apportionment_m3: Mapping[str, float],
    aac_m3: Mapping[str, float],
    *,
    split_threshold: float = 0.05,
) -> list[dict[str, str]]:
    """Map every TSA to its THLB-dominant zone and report material splits."""
    if not 0.0 < split_threshold < 0.5:
        raise BcResidueSupplyError("split_threshold must be between 0 and 0.5")

    rows: list[dict[str, str]] = []
    for tsa in sorted(areas):
        area = areas[tsa]
        thlb_total = area.total_thlb_ha
        if thlb_total <= 0:
            raise BcResidueSupplyError(f"TSA {tsa!r} has no THLB area weight")
        b1_thlb = sum(
            value for ecoprovince, value in area.thlb_area_ha.items()
            if ecoprovince in B1_ECOPROVINCES
        )
        b2_thlb = thlb_total - b1_thlb
        b1_share = b1_thlb / thlb_total
        b2_share = b2_thlb / thlb_total

        forest_total = area.total_forest_ha
        if forest_total <= 0:
            raise BcResidueSupplyError(f"TSA {tsa!r} has no forest area weight")
        b1_forest = sum(
            value for ecoprovince, value in area.forest_area_ha.items()
            if ecoprovince in B1_ECOPROVINCES
        )
        b1_forest_share = b1_forest / forest_total
        b2_forest_share = 1.0 - b1_forest_share

        dominant_ecoprovince = max(
            area.thlb_area_ha, key=lambda key: (area.thlb_area_ha[key], key)
        )
        dominant_ecoprovince_share = area.thlb_area_ha[dominant_ecoprovince] / thlb_total
        dominant_zone = "B1" if b1_share >= b2_share else "B2"
        volume = apportionment_m3.get(tsa, 0.0)
        split = bool(min(b1_share, b2_share) >= split_threshold and volume > 0.0)
        no_tsa = tsa == "NO_TSA"

        if no_tsa:
            rationale = (
                "Unlabelled TSA polygon in the 500 m label raster; it has no "
                "2018 apportionment and is retained for area accounting only."
            )
        elif split:
            rationale = (
                f"THLB-weighted split TSA: B1 {b1_share:.1%}, B2 {b2_share:.1%}; "
                f"dominant ecoprovince {dominant_ecoprovince} "
                f"({dominant_ecoprovince_share:.1%} of THLB). Harvest is "
                "apportioned by the TSA's ecoprovince THLB shares."
            )
        else:
            rationale = (
                f"THLB-dominant {dominant_ecoprovince} "
                f"({dominant_ecoprovince_share:.1%}); B1 {b1_share:.1%}, "
                f"B2 {b2_share:.1%}."
            )

        rows.append(
            {
                "tsa_number": area.tsa_number,
                "tsa_name": tsa,
                "zone_id": dominant_zone,
                "zone_name": ZONE_NAMES[dominant_zone],
                "dominant_ecoprovince": dominant_ecoprovince,
                "dominant_ecoprovince_share": f"{dominant_ecoprovince_share:.9f}",
                "b1_thlb_share": f"{b1_share:.9f}",
                "b2_thlb_share": f"{b2_share:.9f}",
                "b1_forest_share": f"{b1_forest_share:.9f}",
                "b2_forest_share": f"{b2_forest_share:.9f}",
                "split_tsa": "true" if split else "false",
                "no_tsa": "true" if no_tsa else "false",
                "apportionment_m3": f"{volume:.6f}",
                "aac_m3": f"{aac_m3.get(tsa, 0.0):.6f}",
                "rationale": rationale,
                "evidence": (
                    "bc_area_weights.csv THLB; tsa24_pilot/tsa_apportionment_2018.csv; "
                    "FRESH-side provisional zone crosswalk"
                ),
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Harvest and residue annualization
# ---------------------------------------------------------------------------


def allocate_harvest(
    volumes: Mapping[str, float],
    areas: Mapping[str, TsaArea],
) -> tuple[dict[str, float], list[dict[str, str]]]:
    """Allocate TSA volume to ecoprovinces by THLB area, with forest-area fallback."""
    allocated: defaultdict[str, float] = defaultdict(float)
    audit_rows: list[dict[str, str]] = []

    for tsa in sorted(volumes):
        volume = volumes[tsa]
        if volume < 0:
            raise BcResidueSupplyError(f"Negative volume for TSA {tsa!r}: {volume}")
        area = areas.get(tsa)
        if area is None:
            raise BcResidueSupplyError(f"No area weights for TSA {tsa!r}")

        weights = dict(area.thlb_area_ha)
        weight_basis = "thlb_area_ha"
        total_weight = sum(weights.values())
        if total_weight <= 0:
            weights = dict(area.forest_area_ha)
            weight_basis = "forest_area_ha_fallback"
            total_weight = sum(weights.values())
        if total_weight <= 0:
            raise BcResidueSupplyError(f"TSA {tsa!r} has no positive area weight")

        for ecoprovince in sorted(weights):
            share = weights[ecoprovince] / total_weight
            value = volume * share
            allocated[ecoprovince] += value
            audit_rows.append(
                {
                    "tsa_name": tsa,
                    "tsa_number": area.tsa_number,
                    "zone_id": zone_for_ecoprovince(ecoprovince),
                    "ecoprovince": ecoprovince,
                    "weight_basis": weight_basis,
                    "weight_value": f"{weights[ecoprovince]:.6f}",
                    "weight_share": f"{share:.9f}",
                    "allocated_log_m3": f"{value:.6f}",
                }
            )

    total_allocated = sum(allocated.values())
    total_volume = sum(volumes.values())
    if not math.isclose(total_allocated, total_volume, rel_tol=1e-12, abs_tol=1e-6):
        raise BcResidueSupplyError(
            f"Harvest allocation lost volume: {total_allocated:,.6f} vs "
            f"{total_volume:,.6f} m3/y"
        )
    return dict(allocated), audit_rows


def annual_residue_rows(
    allocated_log_m3: Mapping[str, float],
    ratios: BatchRatios,
    *,
    ratio_mode: str,
    volume_basis: str,
    horizon_start: int = HORIZON_START,
    horizon_end: int = HORIZON_END,
    lhv_gj_per_odt: float = LHV_GJ_PER_ODT,
) -> tuple[dict[str, ZoneSupply], list[dict[str, str]]]:
    """Build flat annual residue supply rows by zone."""
    if ratio_mode not in {"ecoprovince", "bc_all_species"}:
        raise BcResidueSupplyError(f"Unsupported ratio_mode: {ratio_mode!r}")
    if horizon_end < horizon_start:
        raise BcResidueSupplyError("horizon_end must be >= horizon_start")
    if lhv_gj_per_odt <= 0:
        raise BcResidueSupplyError("lhv_gj_per_odt must be positive")

    zone_log: defaultdict[str, float] = defaultdict(float)
    zone_residue: defaultdict[str, float] = defaultdict(float)
    fallback_count = 0

    for ecoprovince, volume in allocated_log_m3.items():
        if volume < 0:
            raise BcResidueSupplyError(
                f"Negative allocated volume for {ecoprovince!r}: {volume}"
            )
        ratio = ratios.by_ecoprovince.get(ecoprovince)
        if ratio is None:
            if ecoprovince != "NO_ECOPROVINCE":
                raise BcResidueSupplyError(
                    f"No batch residue ratio for ecoprovince {ecoprovince!r}"
                )
            ratio = ratios.bc_all_species
            fallback_count += 1
        if ratio_mode == "bc_all_species":
            ratio = ratios.bc_all_species
        zone = zone_for_ecoprovince(ecoprovince)
        zone_log[zone] += volume
        zone_residue[zone] += volume * ratio

    for zone_id in ("B1", "B2"):
        if zone_id not in zone_log:
            raise BcResidueSupplyError(f"No harvest volume allocated to zone {zone_id}")

    national_log = sum(zone_log.values())
    national_residue = sum(zone_residue.values())
    zone_log["CAN"] = national_log
    zone_residue["CAN"] = national_residue

    zones: dict[str, ZoneSupply] = {}
    rows: list[dict[str, str]] = []
    ratio_basis = (
        "batch_ecoprovince_residue_odt_per_log_m3"
        if ratio_mode == "ecoprovince"
        else "batch_bc_all_species_residue_odt_per_log_m3"
    )
    for zone_id in ("B1", "B2", "CAN"):
        log_m3 = zone_log[zone_id]
        residue_odt = zone_residue[zone_id]
        ratio = residue_odt / log_m3 if log_m3 > 0 else 0.0
        residue_pj = residue_odt * lhv_gj_per_odt / PJ_GJ
        zones[zone_id] = ZoneSupply(
            zone_id=zone_id,
            annual_log_m3=log_m3,
            residue_odt=residue_odt,
            residue_pj=residue_pj,
            ratio_odt_per_m3=ratio,
            ratio_basis=ratio_basis,
            ecoprovince_fallback_count=fallback_count,
        )
        for year in range(horizon_start, horizon_end + 1):
            rows.append(
                {
                    "year": str(year),
                    "zone_id": zone_id,
                    "annual_log_m3": f"{log_m3:.6f}",
                    "residue_odt": f"{residue_odt:.6f}",
                    "residue_pj": f"{residue_pj:.9f}",
                    "basis": "static_aac",
                    "volume_basis": volume_basis,
                    "profile": "flat",
                    "ratio_odt_per_m3": f"{ratio:.9f}",
                    "ratio_basis": ratio_basis,
                    "lhv_gj_per_odt": f"{lhv_gj_per_odt:.6f}",
                    "evidence": (
                        f"{volume_basis}; TSA harvest allocated to ecoprovinces by "
                        "THLB area weights; residue ratio from the BC area-weighted "
                        "national batch; flat 2021-2070, no invented time path"
                    ),
                }
            )

    return zones, rows


# ---------------------------------------------------------------------------
# OSeCAN parameter rows
# ---------------------------------------------------------------------------


def build_activity_limit_rows(
    scenario: Scenario,
    technology: str,
    zone_id: str,
    *,
    horizon_start: int = HORIZON_START,
    horizon_end: int = HORIZON_END,
) -> list[dict[str, str]]:
    """Emit otoole-shaped TotalTechnologyAnnualActivityUpperLimit rows."""
    if zone_id not in scenario.zones:
        raise BcResidueSupplyError(
            f"Scenario {scenario.label!r} lacks zone {zone_id!r}"
        )
    value = scenario.zones[zone_id].residue_pj
    return [
        {
            "REGION": "CAN",
            "TECHNOLOGY": technology,
            "YEAR": str(year),
            "VALUE": f"{value:.9f}",
        }
        for year in range(horizon_start, horizon_end + 1)
    ]


def build_variable_cost_rows(
    zone_costs: Mapping[str, ZoneCost],
    technology: str,
    zone_id: str,
    *,
    horizon_start: int = HORIZON_START,
    horizon_end: int = HORIZON_END,
) -> list[dict[str, str]]:
    """Emit otoole-shaped VariableCost rows in $/GJ."""
    if zone_id not in zone_costs:
        raise BcResidueSupplyError(f"Missing cost for zone {zone_id!r}")
    value = zone_costs[zone_id].variable_cost_usd_per_gj
    return [
        {
            "REGION": "CAN",
            "TECHNOLOGY": technology,
            "YEAR": str(year),
            "VALUE": f"{value:.9f}",
        }
        for year in range(horizon_start, horizon_end + 1)
    ]


# ---------------------------------------------------------------------------
# Cost derivation from Stage B
# ---------------------------------------------------------------------------


def load_stage_b_costs(
    repo_root: Path,
    tsa_crosswalk: Sequence[Mapping[str, str]],
    *,
    lhv_gj_per_odt: float = LHV_GJ_PER_ODT,
) -> tuple[dict[str, ZoneCost], list[dict[str, str]]]:
    """Derive zone delivered-cost proxies from the Stage B road links.

    For each sawmill source, the least-cost road link to any BIO recipient is
    used as the transport component.  The transport cost is converted from
    CAD/m3 green to CAD/ODT with the source density.  The BIO recipient
    operating cost is added as the fuel preparation/handling proxy.  The
    result is a mill-residue delivered-cost proxy, not a harvest-slash
    collection cost; it is a lower bound for harvest residue.
    """
    supply_rows = _read_csv(
        repo_root / INPUT_PATHS["stage_b_supply"], "Stage B residue supply"
    )
    link_rows = _read_csv(
        repo_root / INPUT_PATHS["stage_b_transport"], "Stage B transport links"
    )
    demand_rows = _read_csv(
        repo_root / INPUT_PATHS["stage_b_demand"], "Stage B residue demand"
    )
    facility_rows = _read_csv(
        repo_root / INPUT_PATHS["bc_facility_tsa"], "BC facility TSA cache"
    )

    _require_columns(
        supply_rows,
        ("source_facility", "odt", "density_odt_m3"),
        "Stage B residue supply",
    )
    _require_columns(
        link_rows,
        ("source_facility", "recipient_facility", "cost_cad_m3"),
        "Stage B transport links",
    )
    _require_columns(
        demand_rows,
        ("recipient_facility", "facility_class", "operating_cost_cad_per_input_odt"),
        "Stage B residue demand",
    )
    _require_columns(
        facility_rows,
        ("facility_id", "tsa_name"),
        "BC facility TSA cache",
    )

    source_odt: defaultdict[str, float] = defaultdict(float)
    source_density: dict[str, float] = {}
    for index, row in enumerate(supply_rows, start=2):
        source = row["source_facility"].strip()
        if not source:
            raise BcResidueSupplyError(f"Stage B supply row {index}: empty source_facility")
        odt = _number(row["odt"], f"Stage B supply row {index} odt")
        density = _number(
            row["density_odt_m3"], f"Stage B supply row {index} density_odt_m3"
        )
        if density <= 0:
            raise BcResidueSupplyError(
                f"Stage B supply row {index}: non-positive density for {source!r}"
            )
        existing = source_density.get(source)
        if existing is not None and not math.isclose(
            existing, density, rel_tol=1e-9, abs_tol=1e-12
        ):
            raise BcResidueSupplyError(
                f"Stage B source {source!r} has inconsistent densities "
                f"({existing}, {density})"
            )
        source_density[source] = density
        source_odt[source] += odt

    bio_processing: dict[str, float] = {}
    for index, row in enumerate(demand_rows, start=2):
        if row["facility_class"].strip() != "BIO":
            continue
        recipient = row["recipient_facility"].strip()
        bio_processing[recipient] = _number(
            row["operating_cost_cad_per_input_odt"],
            f"Stage B demand row {index} operating_cost_cad_per_input_odt",
        )
    if not bio_processing:
        raise BcResidueSupplyError("Stage B demand has no BIO recipient rows")
    processing_cad_per_odt = statistics.fmean(bio_processing.values())
    if any(value <= 0 for value in bio_processing.values()):
        raise BcResidueSupplyError("Stage B BIO processing costs must be positive")

    bio_links: defaultdict[str, list[float]] = defaultdict(list)
    for index, row in enumerate(link_rows, start=2):
        recipient = row["recipient_facility"].strip()
        if recipient not in bio_processing:
            continue
        source = row["source_facility"].strip()
        cost = _number(row["cost_cad_m3"], f"Stage B link row {index} cost_cad_m3")
        if cost < 0:
            raise BcResidueSupplyError(
                f"Stage B link row {index}: negative transport cost for {source!r}"
            )
        bio_links[source].append(cost)

    facility_to_tsa: dict[str, str] = {}
    for index, row in enumerate(facility_rows, start=2):
        facility = row["facility_id"].strip()
        tsa_name = row["tsa_name"].strip()
        if not facility or not tsa_name:
            continue
        facility_to_tsa[facility] = canonical_tsa_name(tsa_name)
    tsa_to_zone = {row["tsa_name"]: row["zone_id"] for row in tsa_crosswalk}

    zone_transport_weighted: defaultdict[str, float] = defaultdict(float)
    zone_odt_weight: defaultdict[str, float] = defaultdict(float)
    zone_sources: defaultdict[str, int] = defaultdict(int)
    missing: list[str] = []
    for source, odt in sorted(source_odt.items()):
        costs = bio_links.get(source)
        if not costs:
            missing.append(source)
            continue
        tsa = facility_to_tsa.get(source)
        if tsa is None or tsa not in tsa_to_zone:
            missing.append(source)
            continue
        zone = tsa_to_zone[tsa]
        transport_cad_per_odt = min(costs) / source_density[source]
        zone_transport_weighted[zone] += transport_cad_per_odt * odt
        zone_odt_weight[zone] += odt
        zone_sources[zone] += 1

    if missing:
        raise BcResidueSupplyError(
            "Stage B sources without a BIO link or TSA/zone mapping: "
            + ", ".join(missing[:10])
            + (" ..." if len(missing) > 10 else "")
        )

    zone_costs: dict[str, ZoneCost] = {}
    derivation_rows: list[dict[str, str]] = []
    for zone_id in ("B1", "B2"):
        if zone_odt_weight[zone_id] <= 0:
            raise BcResidueSupplyError(f"No Stage B residue weight for zone {zone_id}")
        transport = zone_transport_weighted[zone_id] / zone_odt_weight[zone_id]
        total = transport + processing_cad_per_odt
        zone_costs[zone_id] = ZoneCost(
            zone_id=zone_id,
            transport_cad_per_odt=transport,
            processing_cad_per_odt=processing_cad_per_odt,
            total_cad_per_odt=total,
            variable_cost_usd_per_gj=total / lhv_gj_per_odt,
            source_count=zone_sources[zone_id],
            residue_odt_weight=zone_odt_weight[zone_id],
        )
        derivation_rows.append(
            {
                "zone_id": zone_id,
                "transport_cad_per_odt": f"{transport:.9f}",
                "processing_cad_per_odt": f"{processing_cad_per_odt:.9f}",
                "total_cad_per_odt": f"{total:.9f}",
                "lhv_gj_per_odt": f"{lhv_gj_per_odt:.6f}",
                "variable_cost_usd_per_gj": f"{total / lhv_gj_per_odt:.9f}",
                "source_count": str(zone_sources[zone_id]),
                "residue_odt_weight": f"{zone_odt_weight[zone_id]:.6f}",
                "source_weighted_transport_cad_per_odt": f"{transport:.9f}",
                "source_weighted_variable_cost_usd_per_gj": (
                    f"{total / lhv_gj_per_odt:.9f}"
                ),
                "method": (
                    "least-cost Stage B road link per sawmill source to a BIO "
                    "recipient; transport CAD/m3 green / density; plus BIO "
                    "operating_cost_cad_per_input_odt"
                ),
                "evidence": (
                    "stage_b/residue_supply.csv; stage_b/stage_b_transport_links.csv; "
                    "stage_b/residue_demand.csv; bc_facility_tsa.csv"
                ),
            }
        )

    can_weight = sum(zone_odt_weight.values())
    can_transport = sum(zone_transport_weighted.values()) / can_weight
    can_total = can_transport + processing_cad_per_odt
    zone_costs["CAN"] = ZoneCost(
        zone_id="CAN",
        transport_cad_per_odt=can_transport,
        processing_cad_per_odt=processing_cad_per_odt,
        total_cad_per_odt=can_total,
        variable_cost_usd_per_gj=can_total / lhv_gj_per_odt,
        source_count=sum(zone_sources.values()),
        residue_odt_weight=can_weight,
    )
    derivation_rows.append(
        {
            "zone_id": "CAN",
            "transport_cad_per_odt": f"{can_transport:.9f}",
            "processing_cad_per_odt": f"{processing_cad_per_odt:.9f}",
            "total_cad_per_odt": f"{can_total:.9f}",
            "lhv_gj_per_odt": f"{lhv_gj_per_odt:.6f}",
            "variable_cost_usd_per_gj": f"{can_total / lhv_gj_per_odt:.9f}",
            "source_count": str(sum(zone_sources.values())),
            "residue_odt_weight": f"{can_weight:.6f}",
            "source_weighted_transport_cad_per_odt": f"{can_transport:.9f}",
            "source_weighted_variable_cost_usd_per_gj": (
                f"{can_total / lhv_gj_per_odt:.9f}"
            ),
            "method": (
                "Stage B least-cost BIO links, generated-ODT weighted across all "
                "sawmill sources; plus BIO operating_cost_cad_per_input_odt"
            ),
            "evidence": (
                "stage_b/residue_supply.csv; stage_b/stage_b_transport_links.csv; "
                "stage_b/residue_demand.csv; bc_facility_tsa.csv"
            ),
        }
    )
    return zone_costs, derivation_rows


# ---------------------------------------------------------------------------
# Writers and manifest
# ---------------------------------------------------------------------------


def _write_csv(
    path: Path,
    rows: Sequence[Mapping[str, str]],
    columns: Sequence[str],
) -> None:
    if not rows:
        raise BcResidueSupplyError(f"Refusing to write empty CSV: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=list(columns),
            extrasaction="ignore",
            lineterminator="\n",
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _write_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def _build_manifest(
    repo_root: Path,
    out_dir: Path,
    scenario: Scenario,
    parsed_scenario: Scenario,
    apportionment_scenario: Scenario,
    apportionment_parsed_scenario: Scenario,
    all_species_scenario: Scenario,
    ratios: BatchRatios,
    zone_costs: Mapping[str, ZoneCost],
    input_paths: Sequence[Path],
    output_paths: Sequence[Path],
) -> dict[str, object]:
    return {
        "artifact_version": "0.1.0",
        "generated_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "zone_definition": {
            "B1": "Georgia Depression + Southern Coastal Mountains",
            "B2": (
                "Northern Coastal Mountains + Montane Cordillera + "
                "Boreal Cordillera + Boreal Plains + Taiga Plains (BC portion) "
                "+ Wrangel Mountains + NO_ECOPROVINCE fallback"
            ),
            "status": "fresh_provisional_no_published_c2070_boundary",
        },
        "harvest_basis": {
            "primary": "2018 AAC, all-BC including Prince George",
            "primary_total_m3_y": scenario.zones["CAN"].annual_log_m3,
            "parsed_36_tsa_aac_total_m3_y": parsed_scenario.zones["CAN"].annual_log_m3,
            "all_bc_apportionment_total_m3_y": (
                apportionment_scenario.zones["CAN"].annual_log_m3
            ),
            "parsed_36_tsa_apportionment_total_m3_y": (
                apportionment_parsed_scenario.zones["CAN"].annual_log_m3
            ),
            "prince_george_aac_m3_y": PRINCE_GEORGE_AAC_M3,
            "prince_george_apportionment_m3_y": PRINCE_GEORGE_APPORTIONMENT_M3,
            "profile": "static_aac_2018_flat_2021_2070",
        },
        "scenario_totals": {
            scenario.label: {
                "national_log_m3": scenario.zones["CAN"].annual_log_m3,
                "national_residue_odt": scenario.zones["CAN"].residue_odt,
                "national_residue_pj": scenario.zones["CAN"].residue_pj,
            },
            parsed_scenario.label: {
                "national_log_m3": parsed_scenario.zones["CAN"].annual_log_m3,
                "national_residue_odt": parsed_scenario.zones["CAN"].residue_odt,
                "national_residue_pj": parsed_scenario.zones["CAN"].residue_pj,
            },
            apportionment_scenario.label: {
                "national_log_m3": (
                    apportionment_scenario.zones["CAN"].annual_log_m3
                ),
                "national_residue_odt": (
                    apportionment_scenario.zones["CAN"].residue_odt
                ),
                "national_residue_pj": (
                    apportionment_scenario.zones["CAN"].residue_pj
                ),
            },
            apportionment_parsed_scenario.label: {
                "national_log_m3": (
                    apportionment_parsed_scenario.zones["CAN"].annual_log_m3
                ),
                "national_residue_odt": (
                    apportionment_parsed_scenario.zones["CAN"].residue_odt
                ),
                "national_residue_pj": (
                    apportionment_parsed_scenario.zones["CAN"].residue_pj
                ),
            },
            all_species_scenario.label: {
                "national_log_m3": (
                    all_species_scenario.zones["CAN"].annual_log_m3
                ),
                "national_residue_odt": (
                    all_species_scenario.zones["CAN"].residue_odt
                ),
                "national_residue_pj": (
                    all_species_scenario.zones["CAN"].residue_pj
                ),
            },
        },
        "residue_basis": {
            "primary": "area-weighted national batch, ecoprovince-resolved",
            "bc_all_species_ratio_odt_per_m3": ratios.bc_all_species,
            "batch_log_m3": ratios.log_m3,
            "batch_residue_odt": ratios.residue_odt,
            "stage_b_total_ratio_odt_per_m3": STAGE_B_TOTAL_RATIO,
            "stage_b_status": "reported_not_used_mill_residue_different_basis",
        },
        "energy_basis": {
            "lhv_gj_per_odt": LHV_GJ_PER_ODT,
            "odt_definition": "oven-dry tonne",
            "moisture": "no moisture credit; dry-mass LHV",
            "pj_gj": PJ_GJ,
        },
        "key_totals": {
            zone_id: {
                "annual_log_m3": supply.annual_log_m3,
                "residue_odt": supply.residue_odt,
                "residue_pj": supply.residue_pj,
                "ratio_odt_per_m3": supply.ratio_odt_per_m3,
            }
            for zone_id, supply in scenario.zones.items()
        },
        "cost_basis": {
            zone_id: {
                "transport_cad_per_odt": cost.transport_cad_per_odt,
                "processing_cad_per_odt": cost.processing_cad_per_odt,
                "total_cad_per_odt": cost.total_cad_per_odt,
                "variable_cost_usd_per_gj": cost.variable_cost_usd_per_gj,
                "source_count": cost.source_count,
                "residue_odt_weight": cost.residue_odt_weight,
            }
            for zone_id, cost in zone_costs.items()
        },
        "inputs": [
            {
                "path": _relative(path, repo_root),
                "sha256": _sha256(path),
                "bytes": path.stat().st_size,
            }
            for path in input_paths
        ],
        "outputs": [
            {
                "path": _relative(path, out_dir),
                "sha256": _sha256(path),
                "bytes": path.stat().st_size,
            }
            for path in output_paths
        ],
        "assumptions": [
            "B1/B2 is a FRESH-side provisional crosswalk; C2070 has no published boundary.",
            "Harvest is uniform within a TSA and allocated to ecoprovinces by THLB area.",
            "The primary uses the 2018 AAC because it is the sustainable annual cut; the 2018 apportionment is a tenure snapshot and includes MPB-salvage over-allocation.",
            "The parsed 36-TSA AAC artifact omits Prince George; the primary adds the PG 2018 AAC (8,375,000 m3/y).",
            "The parsed 36-TSA apportionment artifact omits Dawson Creek, Fort Nelson, Fort St. John, Okanagan, and Prince George; the apportionment comparison adds their 2018 class sums.",
            "The residue ratio is the national-batch taper residue, not full AGB-based harvest slash.",
            "The cost is a Stage B mill-residue delivered-cost proxy, not a harvest-slash collection cost.",
            "Stage B costs are CAD; no deflator or exchange rate to C2070 $2020 is applied.",
            "The annual profile is flat; no time path is invented.",
        ],
    }


# ---------------------------------------------------------------------------
# Scenario construction
# ---------------------------------------------------------------------------


def _build_scenario(
    label: str,
    volume_basis: str,
    volumes: Mapping[str, float],
    areas: Mapping[str, TsaArea],
    ratios: BatchRatios,
    ratio_mode: str,
    horizon_start: int,
    horizon_end: int,
    lhv_gj_per_odt: float,
) -> Scenario:
    allocated, audit_rows = allocate_harvest(volumes, areas)
    zones, rows = annual_residue_rows(
        allocated,
        ratios,
        ratio_mode=ratio_mode,
        volume_basis=volume_basis,
        horizon_start=horizon_start,
        horizon_end=horizon_end,
        lhv_gj_per_odt=lhv_gj_per_odt,
    )
    return Scenario(
        label=label,
        volume_basis=volume_basis,
        ratio_mode=ratio_mode,
        zones=zones,
        rows=tuple(rows),
        audit_rows=tuple(audit_rows),
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the all-BC woody-residue supply curve for OSeCAN B1/B2."
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[3],
        help="Repository root (default: inferred from this module).",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help=f"Output directory (default: <repo-root>/{OUTPUT_DIR}).",
    )
    parser.add_argument("--horizon-start", type=int, default=HORIZON_START)
    parser.add_argument("--horizon-end", type=int, default=HORIZON_END)
    parser.add_argument(
        "--ratio-mode",
        choices=("ecoprovince", "bc_all_species"),
        default="ecoprovince",
        help="Primary residue-ratio resolution (default: ecoprovince).",
    )
    parser.add_argument(
        "--lhv-gj-per-odt",
        type=float,
        default=LHV_GJ_PER_ODT,
        help="Dry lower heating value in GJ per oven-dry tonne.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute and print the summary without writing files.",
    )
    return parser.parse_args(argv)


def _summary_payload(
    scenario: Scenario,
    zone_costs: Mapping[str, ZoneCost],
    ratios: BatchRatios,
) -> dict[str, object]:
    return {
        "scenario": scenario.label,
        "volume_basis": scenario.volume_basis,
        "ratio_mode": scenario.ratio_mode,
        "zones": {
            zone_id: {
                "annual_log_m3": round(supply.annual_log_m3, 6),
                "residue_odt": round(supply.residue_odt, 6),
                "residue_pj": round(supply.residue_pj, 9),
                "ratio_odt_per_m3": round(supply.ratio_odt_per_m3, 9),
            }
            for zone_id, supply in scenario.zones.items()
        },
        "costs": {
            zone_id: {
                "total_cad_per_odt": round(cost.total_cad_per_odt, 6),
                "variable_cost_usd_per_gj": round(cost.variable_cost_usd_per_gj, 6),
            }
            for zone_id, cost in zone_costs.items()
        },
        "batch_bc_all_species_ratio": round(ratios.bc_all_species, 9),
        "stage_b_total_ratio": round(STAGE_B_TOTAL_RATIO, 9),
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    repo_root = args.repo_root.resolve()
    out_dir = (args.out_dir or (repo_root / OUTPUT_DIR)).resolve()

    input_paths = [repo_root / relative for relative in INPUT_PATHS.values()]
    for path in input_paths:
        if not path.exists():
            raise BcResidueSupplyError(f"Missing input: {path}")

    areas = load_tsa_areas(repo_root / INPUT_PATHS["bc_area_weights"])
    ratios = load_batch_ratios(repo_root / INPUT_PATHS["bc_batch_joined"])

    aac_all_bc = load_tsa_volumes(
        repo_root / INPUT_PATHS["tsa_apportionment"],
        "aac",
        extra_volumes={"Prince George": PRINCE_GEORGE_AAC_M3},
        expected_total_m3=ALL_BC_AAC_M3,
        label="all-BC AAC",
    )
    aac_parsed = load_tsa_volumes(
        repo_root / INPUT_PATHS["tsa_apportionment"],
        "aac",
        extra_volumes=None,
        expected_total_m3=PARSED_AAC_M3,
        label="parsed 36-TSA AAC",
    )
    apportionment_all_bc = load_tsa_volumes(
        repo_root / INPUT_PATHS["tsa_apportionment"],
        "apportionment_class",
        extra_volumes=MISSING_APPORTIONMENT_M3,
        expected_total_m3=ALL_BC_APPORTIONMENT_M3,
        label="all-BC apportionment",
    )
    apportionment_parsed = load_tsa_volumes(
        repo_root / INPUT_PATHS["tsa_apportionment"],
        "apportionment_class",
        extra_volumes=None,
        expected_total_m3=PARSED_APPORTIONMENT_M3,
        label="parsed 36-TSA apportionment",
    )

    primary = _build_scenario(
        "all_bc_aac",
        "aac_2018_all_bc",
        aac_all_bc,
        areas,
        ratios,
        args.ratio_mode,
        args.horizon_start,
        args.horizon_end,
        args.lhv_gj_per_odt,
    )
    parsed_only = _build_scenario(
        "parsed_36_tsa_aac",
        "aac_2018_parsed_36_tsa",
        aac_parsed,
        areas,
        ratios,
        args.ratio_mode,
        args.horizon_start,
        args.horizon_end,
        args.lhv_gj_per_odt,
    )
    apportionment_full_scenario = _build_scenario(
        "all_bc_apportionment",
        "apportionment_2018_all_bc",
        apportionment_all_bc,
        areas,
        ratios,
        args.ratio_mode,
        args.horizon_start,
        args.horizon_end,
        args.lhv_gj_per_odt,
    )
    apportionment_parsed_scenario = _build_scenario(
        "parsed_36_tsa_apportionment",
        "apportionment_2018_parsed_36_tsa",
        apportionment_parsed,
        areas,
        ratios,
        args.ratio_mode,
        args.horizon_start,
        args.horizon_end,
        args.lhv_gj_per_odt,
    )
    all_species_scenario = _build_scenario(
        "all_bc_aac_bc_all_species_ratio",
        "aac_2018_all_bc",
        aac_all_bc,
        areas,
        ratios,
        "bc_all_species",
        args.horizon_start,
        args.horizon_end,
        args.lhv_gj_per_odt,
    )

    zone_crosswalk = build_zone_crosswalk()
    tsa_crosswalk = build_tsa_crosswalk(
        areas, apportionment_all_bc, aac_all_bc
    )
    zone_costs, cost_derivation = load_stage_b_costs(
        repo_root, tsa_crosswalk, lhv_gj_per_odt=args.lhv_gj_per_odt
    )

    # The national rollup must be consistent with the national cap, not with
    # the raw Stage B source mix.  Weight the zone costs by the zone PJ caps.
    b1_supply = primary.zones["B1"]
    b2_supply = primary.zones["B2"]
    total_pj = b1_supply.residue_pj + b2_supply.residue_pj
    can_cost = zone_costs["CAN"]
    cap_weighted_cost = (
        b1_supply.residue_pj * zone_costs["B1"].variable_cost_usd_per_gj
        + b2_supply.residue_pj * zone_costs["B2"].variable_cost_usd_per_gj
    ) / total_pj
    zone_costs["CAN"] = ZoneCost(
        zone_id="CAN",
        transport_cad_per_odt=cap_weighted_cost * args.lhv_gj_per_odt
        - can_cost.processing_cad_per_odt,
        processing_cad_per_odt=can_cost.processing_cad_per_odt,
        total_cad_per_odt=cap_weighted_cost * args.lhv_gj_per_odt,
        variable_cost_usd_per_gj=cap_weighted_cost,
        source_count=can_cost.source_count,
        residue_odt_weight=b1_supply.residue_odt + b2_supply.residue_odt,
    )
    for row in cost_derivation:
        if row["zone_id"] != "CAN":
            continue
        row.update(
            {
                "transport_cad_per_odt": (
                    f"{zone_costs['CAN'].transport_cad_per_odt:.9f}"
                ),
                "total_cad_per_odt": f"{zone_costs['CAN'].total_cad_per_odt:.9f}",
                "variable_cost_usd_per_gj": (
                    f"{zone_costs['CAN'].variable_cost_usd_per_gj:.9f}"
                ),
                "residue_odt_weight": (
                    f"{zone_costs['CAN'].residue_odt_weight:.6f}"
                ),
                "method": (
                    "PJ-cap-weighted average of the B1 and B2 zone costs, so "
                    "the national rollup reconciles with the national cap"
                ),
            }
        )

    if args.dry_run:
        print(json.dumps(_summary_payload(primary, zone_costs, ratios), indent=2))
        return 0

    # Primary outputs
    _write_csv(
        out_dir / "bc_zone_crosswalk.csv",
        zone_crosswalk,
        (
            "ecoprovince",
            "zone_id",
            "zone_name",
            "rationale",
            "crosswalk_status",
            "c2070_boundary",
        ),
    )
    _write_csv(
        out_dir / "bc_tsa_zone_crosswalk.csv",
        tsa_crosswalk,
        (
            "tsa_number",
            "tsa_name",
            "zone_id",
            "zone_name",
            "dominant_ecoprovince",
            "dominant_ecoprovince_share",
            "b1_thlb_share",
            "b2_thlb_share",
            "b1_forest_share",
            "b2_forest_share",
            "split_tsa",
            "no_tsa",
            "apportionment_m3",
            "aac_m3",
            "rationale",
            "evidence",
        ),
    )
    _write_csv(
        out_dir / "bc_annual_residue.csv",
        primary.rows,
        (
            "year",
            "zone_id",
            "annual_log_m3",
            "residue_odt",
            "residue_pj",
            "basis",
            "evidence",
            "volume_basis",
            "profile",
            "ratio_odt_per_m3",
            "ratio_basis",
            "lhv_gj_per_odt",
        ),
    )

    parameter_columns = ("REGION", "TECHNOLOGY", "YEAR", "VALUE")
    _write_csv(
        out_dir / "SUPBCWB1_activity_limits.csv",
        build_activity_limit_rows(primary, "SUPBCWB1", "B1"),
        parameter_columns,
    )
    _write_csv(
        out_dir / "SUPBCWB2_activity_limits.csv",
        build_activity_limit_rows(primary, "SUPBCWB2", "B2"),
        parameter_columns,
    )
    _write_csv(
        out_dir / "SUPBCWB1_variable_cost.csv",
        build_variable_cost_rows(zone_costs, "SUPBCWB1", "B1"),
        parameter_columns,
    )
    _write_csv(
        out_dir / "SUPBCWB2_variable_cost.csv",
        build_variable_cost_rows(zone_costs, "SUPBCWB2", "B2"),
        parameter_columns,
    )

    # Audit and comparison outputs
    _write_csv(
        out_dir / "audit" / "bc_tsa_volume_allocation.csv",
        primary.audit_rows,
        (
            "tsa_name",
            "tsa_number",
            "zone_id",
            "ecoprovince",
            "weight_basis",
            "weight_value",
            "weight_share",
            "allocated_log_m3",
        ),
    )
    _write_csv(
        out_dir / "audit" / "bc_apportionment_adjustments.csv",
        [
            {
                "tsa_name": "Dawson Creek",
                "apportionment_m3": "1860000.000000",
                "aac_m3": "1860000.000000",
                "basis": "workbook_indicated_coniferous_apportionment",
                "evidence": (
                    "TSA Tenure Apportionment V5.xls:Dawson Creek:2018 "
                    "Indicated Coniferous Apportionment class sum"
                ),
            },
            {
                "tsa_name": "Fort Nelson",
                "apportionment_m3": "1625000.000000",
                "aac_m3": "1625000.000000",
                "basis": "workbook_indicated_coniferous_apportionment",
                "evidence": (
                    "TSA Tenure Apportionment V5.xls:Fort Nelson:2018 "
                    "Indicated Coniferous Apportionment class sum"
                ),
            },
            {
                "tsa_name": "Fort St. John",
                "apportionment_m3": "2115010.000000",
                "aac_m3": "2115000.000000",
                "basis": "workbook_indicated_coniferous_apportionment",
                "evidence": (
                    "TSA Tenure Apportionment V5.xls:FtSt John:2018 "
                    "Indicated Coniferous Apportionment class sum"
                ),
            },
            {
                "tsa_name": "Okanagan",
                "apportionment_m3": "3078406.000000",
                "aac_m3": "3078406.000000",
                "basis": "workbook_apportionment_block_backtick_header",
                "evidence": (
                    "TSA Tenure Apportionment V5.xls:Okanagan:2018 "
                    "apportionment class sum; header is a backtick, not "
                    "'Indicated Apportionment'"
                ),
            },
            {
                "tsa_name": "Prince George",
                "apportionment_m3": f"{PRINCE_GEORGE_APPORTIONMENT_M3:.6f}",
                "aac_m3": f"{PRINCE_GEORGE_AAC_M3:.6f}",
                "basis": "workbook_pg_sheet_class_sum_and_region_2_aac",
                "evidence": (
                    "TSA Tenure Apportionment V5.xls:PG:2018 apportionment "
                    "class sum; PG sheet has 8 year columns so the generic "
                    "parser skips it; Region 2 2018 total 11,382,726.476268096 "
                    "minus Robson 400,000 = 10,982,726.476268096 (0.3% below "
                    "the direct class sum); AAC = 8,375,000 m3/y"
                ),
            },
        ],
        ("tsa_name", "apportionment_m3", "aac_m3", "basis", "evidence"),
    )
    _write_csv(
        out_dir / "audit" / "bc_pg_adjustment.csv",
        [
            {
                "tsa_name": "Prince George",
                "apportionment_m3": f"{PRINCE_GEORGE_APPORTIONMENT_M3:.6f}",
                "aac_m3": f"{PRINCE_GEORGE_AAC_M3:.6f}",
                "basis": "workbook_pg_sheet_class_sum_and_region_2_aac",
                "evidence": (
                    "TSA Tenure Apportionment V5.xls:PG:2018 apportionment "
                    "class sum; Region 2 2018 total - Robson = 10,982,726.476"
                ),
                "profile": "static_2018",
            }
        ],
        ("tsa_name", "apportionment_m3", "aac_m3", "basis", "evidence", "profile"),
    )
    _write_csv(
        out_dir / "audit" / "bc_cost_derivation.csv",
        cost_derivation,
        (
            "zone_id",
            "transport_cad_per_odt",
            "processing_cad_per_odt",
            "total_cad_per_odt",
            "lhv_gj_per_odt",
            "variable_cost_usd_per_gj",
            "source_count",
            "residue_odt_weight",
            "source_weighted_transport_cad_per_odt",
            "source_weighted_variable_cost_usd_per_gj",
            "method",
            "evidence",
        ),
    )
    _write_csv(
        out_dir / "audit" / "bc_residue_ratio_comparison.csv",
        [
            {
                "ratio_id": "batch_bc_all_species",
                "ratio_odt_per_m3": f"{ratios.bc_all_species:.9f}",
                "basis": "residue_odt / log_m3, area-weighted national batch",
                "used_in_primary": "false",
                "reason": "provincial aggregate; retained as comparison",
            },
            {
                "ratio_id": "batch_ecoprovince",
                "ratio_odt_per_m3": "",
                "basis": "residue_odt / log_m3 by ecoprovince",
                "used_in_primary": "true",
                "reason": (
                    "spatial resolution available; coastal and boreal ratios "
                    "differ by an order of magnitude"
                ),
            },
            {
                "ratio_id": "stage_b_mill_residue",
                "ratio_odt_per_m3": f"{STAGE_B_TOTAL_RATIO:.9f}",
                "basis": (
                    "8,325,597.703 ODT mill residue / 32,076,713.233 m3 "
                    "Stage B sawmill log input"
                ),
                "used_in_primary": "false",
                "reason": (
                    "mill-processing ratio on a different mass basis; applying "
                    "it to harvest volume would double count the log input"
                ),
            },
        ],
        ("ratio_id", "ratio_odt_per_m3", "basis", "used_in_primary", "reason"),
    )
    _write_csv(
        out_dir / "audit" / "bc_residue_ratio_by_ecoprovince.csv",
        [
            {
                "ecoprovince": ecoprovince,
                "zone_id": zone_for_ecoprovince(ecoprovince),
                "ratio_odt_per_m3": f"{ratio:.9f}",
            }
            for ecoprovince, ratio in sorted(ratios.by_ecoprovince.items())
        ],
        ("ecoprovince", "zone_id", "ratio_odt_per_m3"),
    )

    # Alternative annual scenarios
    for name, scenario in (
        ("bc_annual_residue_parsed_only.csv", parsed_only),
        ("bc_annual_residue_apportionment_full.csv", apportionment_full_scenario),
        ("bc_annual_residue_apportionment_parsed.csv", apportionment_parsed_scenario),
        ("bc_annual_residue_all_species_ratio.csv", all_species_scenario),
    ):
        _write_csv(
            out_dir / "comparison" / name,
            scenario.rows,
            (
                "year",
                "zone_id",
                "annual_log_m3",
                "residue_odt",
                "residue_pj",
                "basis",
                "evidence",
                "volume_basis",
                "profile",
                "ratio_odt_per_m3",
                "ratio_basis",
                "lhv_gj_per_odt",
            ),
        )

    # National rollup and placeholder comparison live under comparison/ so
    # they cannot be loaded alongside the zone technologies by accident.
    _write_csv(
        out_dir / "comparison" / "SUPBCW_activity_limits.csv",
        build_activity_limit_rows(primary, "SUPBCW", "CAN"),
        parameter_columns,
    )
    _write_csv(
        out_dir / "comparison" / "SUPBCW_variable_cost.csv",
        build_variable_cost_rows(zone_costs, "SUPBCW", "CAN"),
        parameter_columns,
    )
    _write_csv(
        out_dir / "comparison" / "SUPBCW_placeholder_comparison.csv",
        [
            {
                "scenario": "primary_all_bc_aac",
                "national_cap_pj": f"{primary.zones['CAN'].residue_pj:.6f}",
                "national_variable_cost_usd_per_gj": (
                    f"{zone_costs['CAN'].variable_cost_usd_per_gj:.6f}"
                ),
                "placeholder_cap_pj": "50.000000",
                "placeholder_cost_usd_per_gj": "4.000000",
                "cap_delta_pj": f"{primary.zones['CAN'].residue_pj - 50.0:.6f}",
                "cost_delta_usd_per_gj": (
                    f"{zone_costs['CAN'].variable_cost_usd_per_gj - 4.0:.6f}"
                ),
                "note": (
                    "Primary uses the all-BC 2018 AAC including Prince George "
                    "and ecoprovince-resolved batch residue ratios."
                ),
            },
            {
                "scenario": "parsed_36_tsa_aac",
                "national_cap_pj": f"{parsed_only.zones['CAN'].residue_pj:.6f}",
                "national_variable_cost_usd_per_gj": (
                    f"{zone_costs['CAN'].variable_cost_usd_per_gj:.6f}"
                ),
                "placeholder_cap_pj": "50.000000",
                "placeholder_cost_usd_per_gj": "4.000000",
                "cap_delta_pj": f"{parsed_only.zones['CAN'].residue_pj - 50.0:.6f}",
                "cost_delta_usd_per_gj": (
                    f"{zone_costs['CAN'].variable_cost_usd_per_gj - 4.0:.6f}"
                ),
                "note": (
                    "Parsed 36-TSA AAC artifact (44.4M m3/y); omits Prince "
                    "George and is retained as the task-stated comparison basis."
                ),
            },
            {
                "scenario": "all_bc_apportionment",
                "national_cap_pj": (
                    f"{apportionment_full_scenario.zones['CAN'].residue_pj:.6f}"
                ),
                "national_variable_cost_usd_per_gj": (
                    f"{zone_costs['CAN'].variable_cost_usd_per_gj:.6f}"
                ),
                "placeholder_cap_pj": "50.000000",
                "placeholder_cost_usd_per_gj": "4.000000",
                "cap_delta_pj": (
                    f"{apportionment_full_scenario.zones['CAN'].residue_pj - 50.0:.6f}"
                ),
                "cost_delta_usd_per_gj": (
                    f"{zone_costs['CAN'].variable_cost_usd_per_gj - 4.0:.6f}"
                ),
                "note": (
                    "All-BC 2018 apportionment, including the five TSAs the "
                    "generic parser misses; includes MPB-salvage over-allocation."
                ),
            },
            {
                "scenario": "parsed_36_tsa_apportionment",
                "national_cap_pj": (
                    f"{apportionment_parsed_scenario.zones['CAN'].residue_pj:.6f}"
                ),
                "national_variable_cost_usd_per_gj": (
                    f"{zone_costs['CAN'].variable_cost_usd_per_gj:.6f}"
                ),
                "placeholder_cap_pj": "50.000000",
                "placeholder_cost_usd_per_gj": "4.000000",
                "cap_delta_pj": (
                    f"{apportionment_parsed_scenario.zones['CAN'].residue_pj - 50.0:.6f}"
                ),
                "cost_delta_usd_per_gj": (
                    f"{zone_costs['CAN'].variable_cost_usd_per_gj - 4.0:.6f}"
                ),
                "note": (
                    "Parsed 36-TSA apportionment artifact (38.7M m3/y); omits "
                    "five TSAs and is the narrowest comparison."
                ),
            },
        ],
        (
            "scenario",
            "national_cap_pj",
            "national_variable_cost_usd_per_gj",
            "placeholder_cap_pj",
            "placeholder_cost_usd_per_gj",
            "cap_delta_pj",
            "cost_delta_usd_per_gj",
            "note",
        ),
    )

    output_paths = sorted(
        path
        for path in out_dir.rglob("*")
        if path.is_file()
        and path.suffix in {".csv", ".json", ".md"}
        and path.name != "manifest.json"
    )
    manifest = _build_manifest(
        repo_root,
        out_dir,
        primary,
        parsed_only,
        apportionment_full_scenario,
        apportionment_parsed_scenario,
        all_species_scenario,
        ratios,
        zone_costs,
        input_paths,
        output_paths,
    )
    _write_json(out_dir / "manifest.json", manifest)
    print(json.dumps(_summary_payload(primary, zone_costs, ratios), indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
