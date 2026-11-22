"""Public Canadian Biomass 2025 pellet-map extract and m3 conversion.

The source is the public ``Canadian Biomass`` 2025 Pellet Mill Map PDF.  Its
current-producer table reports finished pellet tonnes per year, not facility
infeed.  This module converts those reported tonnes to finished-pellet bulk
volume using a documented bulk density, and labels every converted value
``published_capacity_converted``.  It does not apply a pellet yield or claim
that the result is an infeed-equivalent capacity.

Bulk density basis: Natural Resources Canada, Solid Biofuels Bulletin No. 4,
*Graded Wood Pellets* (CAN/CSA-ISO 17225-2), which gives a bulk-density range
of 600-750 kg/m3.  The planning value used here is 650 kg/m3, inside that
range.

All values are model inputs with ``observed=false`` and ``policy_grade=false``.
No restricted or proprietary data are used.
"""

from __future__ import annotations

import csv
import json
import math
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
EXTRACT_CSV = (
    REPO_ROOT
    / "data/derived/national/facilities/canadian_biomass_pellet_map_2025"
    / "pellet_plants_2025.csv"
)
SOURCE_ID = "canadian_biomass_pellet_map_2025"
SOURCE_URL = (
    "https://www.canadianbiomassmagazine.ca/wp-content/uploads/2025/05/"
    "CB_PelletMap25_CWM_Digital.pdf"
)
SOURCE_TITLE = "Canadian Biomass 2025 Pellet Mill Map"
PUBLISHER = "Annex Business Media / Canadian Biomass"
LICENCE = (
    "Public PDF; factual capacity figures extracted with attribution. "
    "The PDF itself is not redistributed."
)
DENSITY_T_PER_M3 = 0.65
DENSITY_SOURCE_ID = "nrcan_graded_wood_pellets_bulk_density"
DENSITY_SOURCE_URL = (
    "https://natural-resources.canada.ca/sites/nrcan/files/files/"
    "NRCAN_BB_no4_e.pdf"
)
DENSITY_SOURCE_TITLE = "Solid Biofuels Bulletin No. 4: Graded Wood Pellets"
DENSITY_SOURCE_PUBLISHER = "Natural Resources Canada"
DENSITY_MIN_T_PER_M3 = 0.60
DENSITY_MAX_T_PER_M3 = 0.75
EXPECTED_ROWS = 43
PROVINCES = frozenset(
    {"BC", "AB", "SK", "MB", "ON", "QC", "NB", "NS", "PE", "NL", "YT", "NT", "NU"}
)
EVIDENCE_LABEL = "model_estimate"
OBSERVED = False
POLICY_GRADE = False


class PelletMapError(ValueError):
    """Raised when the public pellet-map extract violates an invariant."""


@dataclass(frozen=True, slots=True)
class PelletPlant:
    """One current-producer row from the 2025 pellet map."""

    map_row: int
    name: str
    city: str
    province: str
    bagged_pct: str
    bulk_pct: str
    capacity_tonnes_y: float | None
    plant_status: str
    city_lat: float
    city_lon: float
    geocode_display_name: str
    location_source_id: str
    location_source_url: str
    location_licence: str
    notes: str

    @property
    def capacity_m3_y(self) -> float | None:
        if self.capacity_tonnes_y is None:
            return None
        return convert_tonnes_to_m3(self.capacity_tonnes_y)


def _finite_float(value: str, *, field: str, context: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise PelletMapError(f"{context}: invalid {field} {value!r}") from error
    if not math.isfinite(number):
        raise PelletMapError(f"{context}: non-finite {field} {value!r}")
    return number


def convert_tonnes_to_m3(
    tonnes: float, *, density_t_per_m3: float = DENSITY_T_PER_M3
) -> float:
    """Convert finished pellet tonnes to finished-pellet bulk volume in m3.

    ``m3 = tonnes / density``.  The density must lie inside the documented
    CAN/CSA-ISO 17225-2 bulk-density range.  This is a finished-product
    volume; no infeed yield is applied.
    """

    value = float(tonnes)
    density = float(density_t_per_m3)
    if not math.isfinite(value) or value <= 0.0:
        raise PelletMapError(f"capacity tonnes must be finite and positive: {tonnes!r}")
    if not (DENSITY_MIN_T_PER_M3 <= density <= DENSITY_MAX_T_PER_M3):
        raise PelletMapError(
            "pellet bulk density must lie within "
            f"{DENSITY_MIN_T_PER_M3}-{DENSITY_MAX_T_PER_M3} t/m3"
        )
    return value / density


def read_extract(path: Path = EXTRACT_CSV) -> tuple[PelletPlant, ...]:
    """Read and validate the 43-row current-producer extract."""

    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
    except FileNotFoundError as error:
        raise PelletMapError(f"pellet-map extract is missing: {path}") from error
    if len(rows) != EXPECTED_ROWS:
        raise PelletMapError(
            f"{path}: expected {EXPECTED_ROWS} current-producer rows, "
            f"found {len(rows)}"
        )
    required = {
        "map_row",
        "name",
        "city",
        "province",
        "bagged_pct",
        "bulk_pct",
        "capacity_tonnes_y",
        "plant_status",
        "city_lat",
        "city_lon",
        "geocode_display_name",
        "location_source_id",
        "location_source_url",
        "location_licence",
    }
    missing = required - set(rows[0])
    if missing:
        raise PelletMapError(f"{path}: missing columns {sorted(missing)}")

    plants: list[PelletPlant] = []
    seen_rows: set[int] = set()
    for position, row in enumerate(rows, start=2):
        context = f"{path}:{position}"
        map_row = int(_finite_float(row["map_row"], field="map_row", context=context))
        if map_row in seen_rows:
            raise PelletMapError(f"{context}: duplicate map_row {map_row}")
        seen_rows.add(map_row)
        province = row["province"].strip().upper()
        if province not in PROVINCES:
            raise PelletMapError(f"{context}: unexpected province {province!r}")
        name = row["name"].strip()
        city = row["city"].strip()
        if not name or not city:
            raise PelletMapError(f"{context}: blank name or city")
        capacity_text = row["capacity_tonnes_y"].strip()
        capacity = (
            _finite_float(capacity_text, field="capacity_tonnes_y", context=context)
            if capacity_text
            else None
        )
        if capacity is not None and capacity <= 0.0:
            raise PelletMapError(f"{context}: capacity must be positive")
        plant_status = row["plant_status"].strip().lower() or "unknown"
        if plant_status not in {"unknown", "temporarily_closed"}:
            raise PelletMapError(
                f"{context}: unexpected plant_status {plant_status!r}"
            )
        if plant_status == "temporarily_closed" and capacity is not None:
            raise PelletMapError(
                f"{context}: temporarily closed plant carries a capacity"
            )
        plants.append(
            PelletPlant(
                map_row=map_row,
                name=name,
                city=city,
                province=province,
                bagged_pct=row["bagged_pct"].strip(),
                bulk_pct=row["bulk_pct"].strip(),
                capacity_tonnes_y=capacity,
                plant_status=plant_status,
                city_lat=_finite_float(
                    row["city_lat"], field="city_lat", context=context
                ),
                city_lon=_finite_float(
                    row["city_lon"], field="city_lon", context=context
                ),
                geocode_display_name=row["geocode_display_name"].strip(),
                location_source_id=row["location_source_id"].strip(),
                location_source_url=row["location_source_url"].strip(),
                location_licence=row["location_licence"].strip(),
                notes=row.get("notes", "").strip(),
            )
        )
    plants.sort(key=lambda plant: plant.map_row)
    if [plant.map_row for plant in plants] != list(range(1, EXPECTED_ROWS + 1)):
        raise PelletMapError(f"{path}: map_row must cover 1-{EXPECTED_ROWS}")
    return tuple(plants)


def summary(plants: Sequence[PelletPlant]) -> dict[str, Any]:
    """Return auditable counts and conversion totals for the extract."""

    with_capacity = [plant for plant in plants if plant.capacity_tonnes_y is not None]
    total_tonnes = sum(
        float(plant.capacity_tonnes_y or 0.0) for plant in with_capacity
    )
    total_m3 = sum(
        float(plant.capacity_m3_y or 0.0) for plant in with_capacity
    )
    by_province: dict[str, dict[str, Any]] = {}
    for province in sorted({plant.province for plant in plants}):
        group = [plant for plant in plants if plant.province == province]
        tonnes = sum(
            float(plant.capacity_tonnes_y)
            for plant in group
            if plant.capacity_tonnes_y is not None
        )
        by_province[province] = {
            "plants": len(group),
            "plants_with_capacity": sum(
                1 for plant in group if plant.capacity_tonnes_y is not None
            ),
            "capacity_tonnes_y": tonnes,
            "capacity_m3_y_finished_pellet": sum(
                float(plant.capacity_m3_y or 0.0) for plant in group
            ),
        }
    return {
        "artifact": "canadian_biomass_pellet_map_2025_extract",
        "artifact_version": "1.0.0",
        "source_id": SOURCE_ID,
        "source_url": SOURCE_URL,
        "source_title": SOURCE_TITLE,
        "publisher": PUBLISHER,
        "licence": LICENCE,
        "labels": {
            "evidence_label": EVIDENCE_LABEL,
            "observed": OBSERVED,
            "policy_grade": POLICY_GRADE,
        },
        "method": {
            "table": "CURRENT PRODUCERS",
            "rows": len(plants),
            "capacity_unit_reported": "finished pellet tonnes/yr",
            "density_t_per_m3": DENSITY_T_PER_M3,
            "density_range_t_per_m3": [
                DENSITY_MIN_T_PER_M3,
                DENSITY_MAX_T_PER_M3,
            ],
            "density_source_id": DENSITY_SOURCE_ID,
            "density_source_url": DENSITY_SOURCE_URL,
            "conversion": "capacity_m3_y = capacity_tonnes_y / density_t_per_m3",
            "conversion_basis": (
                "finished-pellet bulk volume; no pellet yield or infeed "
                "conversion is applied"
            ),
            "evidence_label_for_converted_values": "published_capacity_converted",
            "proposed_plants_excluded": True,
        },
        "counts": {
            "plants": len(plants),
            "plants_with_capacity": len(with_capacity),
            "plants_without_capacity": len(plants) - len(with_capacity),
            "temporarily_closed": sum(
                1 for plant in plants if plant.plant_status == "temporarily_closed"
            ),
            "by_province": dict(Counter(plant.province for plant in plants)),
        },
        "capacity": {
            "reported_tonnes_y": total_tonnes,
            "converted_m3_y_finished_pellet": total_m3,
            "by_province": by_province,
        },
        "validation": {
            "expected_rows": EXPECTED_ROWS,
            "rows_match": len(plants) == EXPECTED_ROWS,
            "map_rows_unique": len({plant.map_row for plant in plants}) == len(plants),
            "densities_within_documented_range": (
                DENSITY_MIN_T_PER_M3 <= DENSITY_T_PER_M3 <= DENSITY_MAX_T_PER_M3
            ),
            "no_restricted_data": True,
        },
        "caveats": [
            ("Values are published finished-pellet capacities, converted to m3 "
            "with a documented bulk density; they are not observed facility "
            "throughput or infeed."),
            ("The 650 kg/m3 density is a planning value inside the NRCan/"
            "CAN-CSA-ISO 17225-2 range of 600-750 kg/m3; the true plant-level "
            "density is unknown."),
            ("No pellet yield is applied, so the converted volume is not an "
            "infeed-equivalent capacity."),
            ("Skeena Bioenergy is marked temporarily closed and has no reported "
            "capacity."),
            ("The four proposed plants on the map are excluded; only the 43 "
            "current producers are extracted."),
            ("All values are model estimates with observed=false and "
            "policy_grade=false. No legal or AAC claim is made."),
            ("No restricted or proprietary data are used and no UI or public "
            "OSeCAN surface is changed."),
        ],
    }


def write_summary(output_dir: Path = EXTRACT_CSV.parent) -> dict[str, Any]:
    """Write the extract summary beside the CSV."""

    payload = summary(read_extract())
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "summary.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return payload


if __name__ == "__main__":
    print(json.dumps(write_summary(), indent=2, sort_keys=True))
