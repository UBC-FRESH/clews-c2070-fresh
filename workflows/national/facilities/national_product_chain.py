"""Build the national product/residue chain v1 from BC anchor coefficients.

The chain is deliberately a coefficient-transfer model, not a facility
assignment.  It answers one question: if the 119 Mm3 provincial harvest is
processed through a BC-anchored sawlog / pulp / panel / pellet / bio product
chain, how much feedstock, product, and residue is generated in each province?

Evidence design
---------------
* British Columbia is the only province with a species-resolved product chain
  in the repository.  BC therefore uses species-group coefficients derived from
  the 2028 BC supply and assignment stages.
* Every other province has no provincial species/class split.  It receives the
  BC aggregate donor coefficient and is labelled ``donor_coefficient``.  No
  province-specific yield is invented.
* Class technology ratios are read from the BC lines of the FFCM archive that
  underlies the BC chain.  The sawmill residue ratio is cross-checked against
  the independently built BC Stage B residue ledger.

Output labels
-------------
Every row is ``model_estimate=true``, ``observed=false``,
``policy_grade=false``, and ``facility_assignment=false``.  The artifact is a
screening product/residue estimate, not a policy, AAC, or operating claim.

Fail-closed rules
-----------------
* No NaN, infinity, or negative quantity is written.
* Grade shares and class shares must sum to one.
* Harvest volume must be conserved by the grade split.
* Class feedstock must be conserved by the class split.
* The BC sawmill residue ratio must reconcile to BC Stage B.
* Any unexpected deletion in ``git status`` must halt the caller; the builder
  itself never deletes files.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[3]
OUTPUT_DIR = (
    REPO_ROOT / "data/derived/national/facilities/national_product_chain_v1"
)

HARVEST_INPUT = (
    REPO_ROOT
    / "data/derived/national/harvest_basis/provincial_allocation/119m.csv"
)
BC_CHAIN_DIR = REPO_ROOT / "data/derived/national/bc_chain"
BC_SUPPLY = BC_CHAIN_DIR / "bc_supply_by_zone.csv"
BC_STAGE_A = BC_CHAIN_DIR / "bc_stage_a_assignment.csv"
BC_STAGE_B = BC_CHAIN_DIR / "bc_stage_b_residue.csv"
BC_FACILITIES = BC_CHAIN_DIR / "facilities_bc.csv"
FFCM_LINES = REPO_ROOT / "metadata/ffcm-archive/parsed/processing_lines.csv"
FFCM_FLOWS = REPO_ROOT / "metadata/ffcm-archive/parsed/processing_line_flows.csv"
RSYC_SUMMARY = (
    REPO_ROOT
    / "data/derived/national/clews_supply/rsyc_backbone/"
    "national_residue_factor_summary.json"
)

PROVINCES: dict[str, str] = {
    "BC": "British Columbia",
    "AB": "Alberta",
    "SK": "Saskatchewan",
    "MB": "Manitoba",
    "ON": "Ontario",
    "QC": "Quebec",
    "NB": "New Brunswick",
    "NS": "Nova Scotia",
    "PE": "Prince Edward Island",
    "NL": "Newfoundland and Labrador",
    "YT": "Yukon",
    "NT": "Northwest Territories",
    "NU": "Nunavut",
}
CLASS_ORDER: tuple[str, ...] = ("SAW", "PULP", "PANEL", "PELLET", "BIO")
GRADE_ORDER: tuple[str, ...] = ("sawlog", "pulpwood", "other", "residue")
LOG_GRADES: tuple[str, ...] = ("sawlog", "pulpwood", "other")
RESIDUE_DENSITY_ODT_M3 = 0.45
RSYC_BASE_RECOVERY = 0.60
MBF_TO_M3 = 2.359737216
HARDWOOD_SPECIES = frozenset(
    {"ACER.MAC", "ALNU.RUB", "BETU.PAP", "POPU.BAL", "POPU.TRE"}
)
STAGE_A_CLASS_MAP = {
    "SAW": "SAW",
    "PAP": "PULP",
    "PEL": "PELLET",
    "BIO": "BIO",
}
STAGE_A_GRADE_MAP = {
    "SAWLOG": "sawlog",
    "PULP": "pulpwood",
    "OTHER_LOG": "other",
    "RESIDUE": "residue",
}
FFCM_CLASS_MAP = {
    "SAW": "SAW",
    "PAP": "PULP",
    "PLY": "PANEL",
    "OSB": "PANEL",
    "NSP": "PANEL",
    "PEL": "PELLET",
    "BIO": "BIO",
}
# One representative feedstock line per facility class avoids the FFCM
# log-equivalent duplicate rows (for example BC-PAP-179-1 chips and
# BC-PAP-179-11 log).  The selected line is the actual processing feedstock.
CLASS_FEEDSTOCK = {
    "SAW": "log",
    "PULP": "woodchips",
    "PANEL": "log",
    "PELLET": "sawdust_shavings",
    "BIO": "hogfuel",
}
CLASS_FFCM_CODES = {
    "SAW": frozenset({"SAW"}),
    "PULP": frozenset({"PAP"}),
    "PANEL": frozenset({"PLY", "OSB", "NSP"}),
    "PELLET": frozenset({"PEL"}),
    "BIO": frozenset({"BIO"}),
}
PRODUCT_COMMODITIES = {
    "SAW": (
        ("lumber_softwood_west", "lumber_softwood_east", "lumber_hardwood"),
        "mbf",
        "m3",
        MBF_TO_M3,
    ),
    "PULP": (
        (
            "kraft_softwood",
            "kraft_hardwood",
            "refiner_softwood",
            "refiner_hardwood",
            "sulfite_softwood",
            "sulfite_hardwood",
            "groundwood_softwood",
            "nssc_hardwood",
            "soda_hardwood",
        ),
        "admt",
        "odt",
        1.0,
    ),
    "PANEL": (("plywood", "osb", "mdf"), "m3", "m3", 1.0),
    "PELLET": (("pellet",), "odt", "odt", 1.0),
    "BIO": (("electricity_BC",), "kwh", "MWh", 1.0 / 1000.0),
}
RESIDUE_COMMODITIES: tuple[str, ...] = (
    "woodchips",
    "sawdust_shavings",
    "hogfuel",
    "internally_used_woodfuel",
    "other_pulping_byproducts",
    "peeler_core",
)
RESIDUE_LABELS = {
    "woodchips": "chips",
    "sawdust_shavings": "sawdust_shavings",
    "hogfuel": "hogfuel",
    "internally_used_woodfuel": "other",
    "other_pulping_byproducts": "other_pulping_byproducts",
    "peeler_core": "peeler_core",
}


class ProductChainError(RuntimeError):
    """Raised when an input invariant or conservation check fails."""


@dataclass(frozen=True)
class ProvinceResult:
    province: str
    harvest_m3: float
    row: dict[str, Any]
    donor_component_count: int


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_csv(path: Path, required: Sequence[str], label: str) -> pd.DataFrame:
    if not path.exists():
        raise ProductChainError(f"{label} not found: {path}")
    frame = pd.read_csv(path)
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise ProductChainError(
            f"{label} is missing columns: {', '.join(sorted(missing))}"
        )
    if frame.empty:
        raise ProductChainError(f"{label} is empty: {path}")
    return frame


def _finite_nonnegative(value: Any, label: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ProductChainError(f"{label} is not numeric: {value!r}") from exc
    if not math.isfinite(parsed):
        raise ProductChainError(f"{label} is not finite: {parsed!r}")
    if parsed < 0.0:
        raise ProductChainError(f"{label} is negative: {parsed!r}")
    return parsed


def _require_close(
    left: float, right: float, label: str, *, rel_tol: float = 1e-9
) -> None:
    scale = max(abs(left), abs(right), 1.0)
    if abs(left - right) > rel_tol * scale:
        raise ProductChainError(
            f"{label} mismatch: {left!r} != {right!r} "
            f"(relative {abs(left - right) / scale:.3e})"
        )


def _safe_share(numerator: float, denominator: float, label: str) -> float:
    if denominator <= 0.0:
        raise ProductChainError(f"{label} denominator must be positive")
    share = numerator / denominator
    if not math.isfinite(share) or share < -1e-12 or share > 1.0 + 1e-9:
        raise ProductChainError(f"{label} share is outside [0, 1]: {share!r}")
    return max(0.0, min(1.0, share))


def _species_group(species: Any) -> str:
    return "hardwood" if str(species).strip().upper() in HARDWOOD_SPECIES else "softwood"


def _province_from_facility(facility: Any) -> str:
    text = str(facility)
    if len(text) < 2 or text[2:3] != "-":
        raise ProductChainError(f"facility id has no province prefix: {facility!r}")
    return text[:2].upper()


def _ffcm_class(facility: Any) -> str | None:
    parts = str(facility).split("-")
    if len(parts) < 3:
        return None
    return parts[1].upper()


def _normalise_infeed(value: Any, unit: Any, label: str) -> float:
    rate = _finite_nonnegative(value, f"{label} infeed rate")
    unit_text = str(unit).strip().lower()
    if unit_text == "m3/y":
        return rate
    if unit_text in {"bdmt/y", "odt/y"}:
        return rate / RESIDUE_DENSITY_ODT_M3
    raise ProductChainError(f"{label} has unsupported infeed unit: {unit!r}")


def _normalise_product(
    value: float, source_unit: str, target_unit: str, factor: float, label: str
) -> float:
    if not math.isfinite(value) or value < 0.0:
        raise ProductChainError(f"{label} product value is invalid: {value!r}")
    if source_unit == "kwh" and target_unit == "MWh":
        return value * factor
    if source_unit == "mbf" and target_unit == "m3":
        return value * factor
    return value


def _build_grade_mix(
    supply: pd.DataFrame, stage_a: pd.DataFrame
) -> tuple[pd.DataFrame, dict[str, float]]:
    """Return grade shares and harvest-residue ratios by species group."""
    frame = supply.copy()
    frame["year"] = pd.to_numeric(frame["year"], errors="raise")
    years = sorted(frame["year"].unique())
    if 2028 not in years:
        raise ProductChainError("BC supply has no 2028 period")
    frame = frame[frame["year"] == 2028].copy()
    frame["species_group"] = frame["species"].map(_species_group)
    frame["volume_m3"] = pd.to_numeric(frame["volume_m3"], errors="raise")
    frame["residue_m3"] = pd.to_numeric(frame["residue_m3"], errors="raise")
    frame["residue_odt"] = pd.to_numeric(frame["residue_odt"], errors="raise")
    if (frame[["volume_m3", "residue_m3", "residue_odt"]] < 0).any().any():
        raise ProductChainError("BC supply contains a negative volume")

    assignments = stage_a.copy()
    assignments["grade"] = assignments["grade"].map(STAGE_A_GRADE_MAP)
    assignments["species_group"] = assignments["species"].map(_species_group)
    assignments["volume_m3"] = pd.to_numeric(
        assignments["volume_m3"], errors="raise"
    )
    recoverable_residue = (
        assignments[assignments["grade"] == "residue"]
        .groupby("species_group")["volume_m3"]
        .sum()
        .to_dict()
    )

    rows: list[dict[str, Any]] = []
    for group in ("softwood", "hardwood", "aggregate"):
        if group == "aggregate":
            log_rows = frame[frame["product"].isin(LOG_GRADES)]
            residue_rows = frame[frame["product"] == "residue"]
            recoverable_m3 = sum(recoverable_residue.values())
        else:
            log_rows = frame[
                (frame["species_group"] == group)
                & (frame["product"].isin(LOG_GRADES))
            ]
            residue_rows = frame[
                (frame["species_group"] == group)
                & (frame["product"] == "residue")
            ]
            recoverable_m3 = recoverable_residue.get(group, 0.0)
        log_m3 = float(log_rows["volume_m3"].sum())
        if log_m3 <= 0.0:
            continue
        sawlog_m3 = float(
            log_rows.loc[log_rows["product"] == "sawlog", "volume_m3"].sum()
        )
        pulpwood_m3 = float(
            log_rows.loc[log_rows["product"] == "pulpwood", "volume_m3"].sum()
        )
        other_m3 = float(
            log_rows.loc[log_rows["product"] == "other", "volume_m3"].sum()
        )
        _require_close(
            sawlog_m3 + pulpwood_m3 + other_m3,
            log_m3,
            f"BC {group} grade split",
        )
        residue_m3 = float(residue_rows["residue_m3"].sum())
        residue_odt = float(residue_rows["residue_odt"].sum())
        if residue_m3 <= 0.0 or residue_odt <= 0.0:
            raise ProductChainError(f"BC {group} harvest residue is not positive")
        recovery_share = _safe_share(
            recoverable_m3, residue_m3, f"BC {group} residue recovery"
        )
        rows.append(
            {
                "species_group": group,
                "log_m3": log_m3,
                "sawlog_share": _safe_share(
                    sawlog_m3, log_m3, f"BC {group} sawlog share"
                ),
                "pulpwood_share": _safe_share(
                    pulpwood_m3, log_m3, f"BC {group} pulpwood share"
                ),
                "other_share": _safe_share(
                    other_m3, log_m3, f"BC {group} other share"
                ),
                "harvest_residue_m3_per_m3": _safe_share(
                    residue_m3, log_m3, f"BC {group} residue m3 ratio"
                ),
                "harvest_residue_odt_per_m3": _safe_share(
                    residue_odt, log_m3, f"BC {group} residue odt ratio"
                ),
                "residue_recovery_share": recovery_share,
            }
        )

    result = pd.DataFrame(rows)
    for group in ("softwood", "hardwood", "aggregate"):
        subset = result[result["species_group"] == group]
        if subset.empty:
            continue
        row = subset.iloc[0]
        _require_close(
            float(row["sawlog_share"])
            + float(row["pulpwood_share"])
            + float(row["other_share"]),
            1.0,
            f"BC {group} grade shares",
        )
    group_log = {
        str(row["species_group"]): float(row["log_m3"])
        for _, row in result.iterrows()
        if row["species_group"] in {"softwood", "hardwood"}
    }
    total_group_log = sum(group_log.values())
    if total_group_log <= 0.0:
        raise ProductChainError("BC species-group log volume is not positive")
    group_shares = {
        group: volume / total_group_log for group, volume in group_log.items()
    }
    return result.reset_index(drop=True), group_shares


def _panel_share_of_sawlog(
    stage_a: pd.DataFrame, facilities: pd.DataFrame
) -> tuple[float, float]:
    facilities = facilities.copy()
    facilities["facility_class"] = facilities["facility_class"].astype(str).str.upper()
    panel_capacity = float(
        pd.to_numeric(
            facilities.loc[
                facilities["facility_class"].isin({"PLY", "OSB", "NSP"}),
                "capacity_m3_y",
            ],
            errors="raise",
        ).sum()
    )
    sawlog_assigned = float(
        pd.to_numeric(
            stage_a.loc[stage_a["grade"] == "SAWLOG", "volume_m3"], errors="raise"
        ).sum()
    )
    if panel_capacity <= 0.0 or sawlog_assigned <= 0.0:
        raise ProductChainError("BC panel or sawlog capacity is not positive")
    share = _safe_share(
        panel_capacity,
        sawlog_assigned + panel_capacity,
        "BC panel share of sawlog",
    )
    return share, panel_capacity


def _build_class_allocation(
    stage_a: pd.DataFrame, panel_share: float
) -> pd.DataFrame:
    frame = stage_a.copy()
    frame["grade"] = frame["grade"].map(STAGE_A_GRADE_MAP)
    frame["facility_class"] = frame["facility_class"].map(STAGE_A_CLASS_MAP)
    frame["species_group"] = frame["species"].map(_species_group)
    frame["volume_m3"] = pd.to_numeric(frame["volume_m3"], errors="raise")
    if frame[["grade", "facility_class"]].isna().any().any():
        raise ProductChainError("BC Stage A has an unmapped grade or class")
    if (frame["volume_m3"] < 0).any():
        raise ProductChainError("BC Stage A contains a negative volume")

    rows: list[dict[str, Any]] = []
    for group in ("softwood", "hardwood", "aggregate"):
        subset = frame if group == "aggregate" else frame[frame["species_group"] == group]
        for grade in GRADE_ORDER:
            grade_rows = subset[subset["grade"] == grade]
            total = float(grade_rows["volume_m3"].sum())
            if total <= 0.0:
                continue
            volumes = (
                grade_rows.groupby("facility_class")["volume_m3"].sum().to_dict()
            )
            shares = {
                facility_class: volume / total
                for facility_class, volume in volumes.items()
            }
            if grade == "sawlog":
                shares = {
                    facility_class: share * (1.0 - panel_share)
                    for facility_class, share in shares.items()
                }
                shares["PANEL"] = panel_share
            total_share = sum(shares.values())
            _require_close(total_share, 1.0, f"BC {group}/{grade} class shares")
            for facility_class, share in shares.items():
                if share <= 0.0:
                    continue
                if facility_class not in CLASS_ORDER:
                    raise ProductChainError(
                        f"unsupported class {facility_class!r} in BC Stage A"
                    )
                rows.append(
                    {
                        "species_group": group,
                        "grade": grade,
                        "facility_class": facility_class,
                        "share": float(share),
                    }
                )
    return pd.DataFrame(rows)


def _selected_ffcm_lines(lines: pd.DataFrame) -> pd.DataFrame:
    frame = lines.copy()
    facility_pattern = r"^[A-Z]{2}-[A-Z]+-\d+"
    frame = frame[
        frame["facility"].astype(str).str.match(facility_pattern)
    ].copy()
    if frame.empty:
        raise ProductChainError("FFCM lines contain no facility records")
    frame["province"] = frame["facility"].map(_province_from_facility)
    frame["ffcm_class"] = frame["facility"].map(_ffcm_class)
    frame = frame[frame["province"] == "BC"].copy()
    selected: list[pd.DataFrame] = []
    for facility_class in CLASS_ORDER:
        codes = CLASS_FFCM_CODES[facility_class]
        feedstock = CLASS_FEEDSTOCK[facility_class]
        subset = frame[
            frame["ffcm_class"].isin(codes)
            & (frame["feedstock"].astype(str).str.lower() == feedstock)
        ].copy()
        if facility_class in {"SAW", "PULP"}:
            subset["species_group"] = [
                "hardwood"
                if "hardwood" in str(value).lower()
                else "softwood"
                for value in subset["primary_product"]
            ]
        else:
            subset["species_group"] = "aggregate"
        subset["facility_class"] = facility_class
        subset["infeed_m3"] = [
            _normalise_infeed(value, unit, f"{facility_class} line {line}")
            for value, unit, line in zip(
                subset["infeed_rate"], subset["infeed_unit"], subset["processing_line"]
            )
        ]
        selected.append(subset)
    result = pd.concat(selected, ignore_index=True)
    if result.empty:
        raise ProductChainError("no BC FFCM processing lines were selected")
    if (result["infeed_m3"] <= 0.0).any():
        raise ProductChainError("selected BC FFCM line has non-positive infeed")
    return result


def _build_class_ratios(
    lines: pd.DataFrame, flows: pd.DataFrame
) -> tuple[pd.DataFrame, dict[str, float]]:
    selected = _selected_ffcm_lines(lines)
    flow_frame = flows.copy()
    flow_frame["value"] = pd.to_numeric(flow_frame["value"], errors="raise")
    flow_frame = flow_frame[flow_frame["processing_line"].isin(selected["processing_line"])]
    flow_totals = (
        flow_frame.groupby(["processing_line", "commodity"])["value"]
        .sum()
        .reset_index()
    )

    rows: list[dict[str, Any]] = []
    crosscheck: dict[str, float] = {}
    for facility_class in CLASS_ORDER:
        product_commodities, source_unit, target_unit, factor = PRODUCT_COMMODITIES[
            facility_class
        ]
        for group in ("softwood", "hardwood", "aggregate"):
            subset = selected[selected["facility_class"] == facility_class]
            if group == "aggregate":
                group_rows = subset
            else:
                group_rows = subset[subset["species_group"] == group]
            infeed = float(group_rows["infeed_m3"].sum())
            if infeed <= 0.0:
                continue
            lines_in_group = set(group_rows["processing_line"])
            group_flows = flow_totals[
                flow_totals["processing_line"].isin(lines_in_group)
            ]
            product_value = float(
                group_flows.loc[
                    group_flows["commodity"].isin(product_commodities), "value"
                ].sum()
            )
            product_ratio = _normalise_product(
                product_value,
                source_unit,
                target_unit,
                factor,
                f"{facility_class}/{group} product",
            ) / infeed
            if product_ratio < 0.0 or not math.isfinite(product_ratio):
                raise ProductChainError(
                    f"{facility_class}/{group} product ratio is invalid"
                )
            rows.append(
                {
                    "species_group": group,
                    "facility_class": facility_class,
                    "coefficient_name": "product_per_m3",
                    "value": product_ratio,
                    "unit": f"{target_unit}/m3",
                }
            )
            total_residue_odt = 0.0
            for commodity in RESIDUE_COMMODITIES:
                value = float(
                    group_flows.loc[group_flows["commodity"] == commodity, "value"].sum()
                )
                if value <= 0.0:
                    continue
                ratio = value / infeed
                total_residue_odt += ratio
                rows.append(
                    {
                        "species_group": group,
                        "facility_class": facility_class,
                        "coefficient_name": f"residue_{RESIDUE_LABELS[commodity]}_odt_per_m3",
                        "value": ratio,
                        "unit": "odt/m3",
                    }
                )
            rows.append(
                {
                    "species_group": group,
                    "facility_class": facility_class,
                    "coefficient_name": "residue_total_odt_per_m3",
                    "value": total_residue_odt,
                    "unit": "odt/m3",
                }
            )
            if facility_class == "SAW" and group in {"softwood", "aggregate"}:
                crosscheck[group] = total_residue_odt
    result = pd.DataFrame(rows)
    if result.empty:
        raise ProductChainError("no BC class ratios were derived")
    if (result["value"] < 0.0).any():
        raise ProductChainError("BC class ratio is negative")
    return result, crosscheck


def _coefficient_rows(
    grade_mix: pd.DataFrame,
    class_allocation: pd.DataFrame,
    class_ratios: pd.DataFrame,
    panel_share: float,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for _, record in grade_mix.iterrows():
        group = str(record["species_group"])
        tier = "bc_anchor_species_group" if group != "aggregate" else "donor_coefficient"
        for name, unit, note in (
            ("sawlog_share", "fraction", "share of BC log harvest"),
            ("pulpwood_share", "fraction", "share of BC log harvest"),
            ("other_share", "fraction", "share of BC log harvest"),
            (
                "harvest_residue_m3_per_m3",
                "m3/m3",
                "gross harvest residue per m3 log harvest",
            ),
            (
                "harvest_residue_odt_per_m3",
                "odt/m3",
                "gross harvest residue per m3 log harvest",
            ),
            (
                "residue_recovery_share",
                "fraction",
                "Stage A residue volume divided by gross BC residue volume",
            ),
        ):
            rows.append(
                {
                    "coefficient_scope": "BC anchor"
                    if tier == "bc_anchor_species_group"
                    else "BC donor",
                    "species_group": group,
                    "facility_class": "",
                    "grade": "",
                    "coefficient_name": name,
                    "value": float(record[name]),
                    "unit": unit,
                    "tier": tier,
                    "source": "BC 2028 supply and Stage A residue",
                    "notes": note,
                }
            )
    rows.append(
        {
            "coefficient_scope": "BC anchor",
            "species_group": "sawlog",
            "facility_class": "PANEL",
            "grade": "sawlog",
            "coefficient_name": "panel_share_of_sawlog",
            "value": panel_share,
            "unit": "fraction",
            "tier": "bc_anchor_species_group",
            "source": "BC panel nameplate divided by BC sawlog plus panel nameplate",
            "notes": "PANEL is absent from BC Stage A; capacity share is used as an explicit anchor.",
        }
    )
    for _, record in class_allocation.iterrows():
        group = str(record["species_group"])
        tier = "bc_anchor_species_group" if group != "aggregate" else "donor_coefficient"
        rows.append(
            {
                "coefficient_scope": "BC anchor"
                if tier == "bc_anchor_species_group"
                else "BC donor",
                "species_group": group,
                "facility_class": str(record["facility_class"]),
                "grade": str(record["grade"]),
                "coefficient_name": "class_share",
                "value": float(record["share"]),
                "unit": "fraction",
                "tier": tier,
                "source": "BC Stage A 2028 assignment",
                "notes": "share of grade volume routed to the facility class",
            }
        )
    for _, record in class_ratios.iterrows():
        group = str(record["species_group"])
        tier = "bc_anchor_species_group" if group != "aggregate" else "donor_coefficient"
        rows.append(
            {
                "coefficient_scope": "BC anchor"
                if tier == "bc_anchor_species_group"
                else "BC donor",
                "species_group": group,
                "facility_class": str(record["facility_class"]),
                "grade": "",
                "coefficient_name": str(record["coefficient_name"]),
                "value": float(record["value"]),
                "unit": str(record["unit"]),
                "tier": tier,
                "source": "BC FFCM archive processing-line ratios",
                "notes": "technology coefficient per m3 of class feedstock",
            }
        )
    return rows


def _lookup_grade_mix(
    grade_mix: pd.DataFrame, group: str
) -> pd.Series:
    subset = grade_mix[grade_mix["species_group"] == group]
    if subset.empty:
        raise ProductChainError(f"missing grade mix for {group}")
    return subset.iloc[0]


def _lookup_allocation(
    class_allocation: pd.DataFrame, group: str, grade: str
) -> dict[str, float]:
    subset = class_allocation[
        (class_allocation["species_group"] == group)
        & (class_allocation["grade"] == grade)
    ]
    if subset.empty and group != "aggregate":
        subset = class_allocation[
            (class_allocation["species_group"] == "aggregate")
            & (class_allocation["grade"] == grade)
        ]
    return {
        str(row["facility_class"]): float(row["share"])
        for _, row in subset.iterrows()
    }


def _lookup_ratios(
    class_ratios: pd.DataFrame, group: str, facility_class: str
) -> tuple[dict[str, float], bool]:
    subset = class_ratios[
        (class_ratios["species_group"] == group)
        & (class_ratios["facility_class"] == facility_class)
    ]
    used_donor = False
    if subset.empty and group != "aggregate":
        subset = class_ratios[
            (class_ratios["species_group"] == "aggregate")
            & (class_ratios["facility_class"] == facility_class)
        ]
        used_donor = True
    if subset.empty:
        raise ProductChainError(
            f"missing class ratios for {group}/{facility_class}"
        )
    return (
        {
            str(row["coefficient_name"]): float(row["value"])
            for _, row in subset.iterrows()
        },
        used_donor,
    )


def _apply_chain(
    harvest_m3: float,
    group: str,
    grade_mix: pd.DataFrame,
    class_allocation: pd.DataFrame,
    class_ratios: pd.DataFrame,
) -> tuple[dict[str, float], int]:
    mix = _lookup_grade_mix(grade_mix, group)
    grade_volume = {
        "sawlog": harvest_m3 * float(mix["sawlog_share"]),
        "pulpwood": harvest_m3 * float(mix["pulpwood_share"]),
        "other": harvest_m3 * float(mix["other_share"]),
        "residue": harvest_m3
        * float(mix["harvest_residue_m3_per_m3"])
        * float(mix["residue_recovery_share"]),
    }
    _require_close(
        grade_volume["sawlog"]
        + grade_volume["pulpwood"]
        + grade_volume["other"],
        harvest_m3,
        f"{group} log grade conservation",
    )

    class_feedstock = {facility_class: 0.0 for facility_class in CLASS_ORDER}
    for grade, volume in grade_volume.items():
        shares = _lookup_allocation(class_allocation, group, grade)
        if not shares:
            continue
        _require_close(sum(shares.values()), 1.0, f"{group}/{grade} allocation")
        for facility_class, share in shares.items():
            class_feedstock[facility_class] += volume * share

    outputs: dict[str, float] = {f"feedstock_{facility_class.lower()}_m3": 0.0 for facility_class in CLASS_ORDER}
    outputs.update({f"product_{facility_class.lower()}": 0.0 for facility_class in CLASS_ORDER})
    outputs.update({f"residue_{facility_class.lower()}_odt": 0.0 for facility_class in CLASS_ORDER})
    donor_component_count = 0
    for facility_class in CLASS_ORDER:
        ratios, used_donor = _lookup_ratios(class_ratios, group, facility_class)
        if used_donor:
            donor_component_count += 1
        feed = class_feedstock[facility_class]
        outputs[f"feedstock_{facility_class.lower()}_m3"] = feed
        outputs[f"product_{facility_class.lower()}"] = feed * ratios.get(
            "product_per_m3", 0.0
        )
        outputs[f"residue_{facility_class.lower()}_odt"] = feed * ratios.get(
            "residue_total_odt_per_m3", 0.0
        )

    outputs["harvest_residue_gross_m3"] = harvest_m3 * float(
        mix["harvest_residue_m3_per_m3"]
    )
    outputs["harvest_residue_gross_odt"] = harvest_m3 * float(
        mix["harvest_residue_odt_per_m3"]
    )
    outputs["harvest_residue_recoverable_m3"] = harvest_m3 * float(
        mix["harvest_residue_m3_per_m3"]
    ) * float(mix["residue_recovery_share"])
    outputs["harvest_residue_recoverable_odt"] = harvest_m3 * float(
        mix["harvest_residue_odt_per_m3"]
    ) * float(mix["residue_recovery_share"])
    outputs["harvest_residue_unrecovered_m3"] = (
        outputs["harvest_residue_gross_m3"]
        - outputs["harvest_residue_recoverable_m3"]
    )
    outputs["harvest_residue_unrecovered_odt"] = (
        outputs["harvest_residue_gross_odt"]
        - outputs["harvest_residue_recoverable_odt"]
    )
    return outputs, donor_component_count


def _sum_component(
    target: dict[str, float], source: Mapping[str, float]
) -> None:
    for key, value in source.items():
        target[key] = target.get(key, 0.0) + float(value)


def _build_province_row(
    province: str,
    harvest_m3: float,
    grade_mix: pd.DataFrame,
    class_allocation: pd.DataFrame,
    class_ratios: pd.DataFrame,
    group_shares: Mapping[str, float],
    rsyc_factors: Mapping[str, float],
) -> ProvinceResult:
    if province == "BC":
        components: dict[str, float] = {}
        donor_component_count = 0
        for group in ("softwood", "hardwood"):
            share = group_shares.get(group, 0.0)
            if share <= 0.0:
                continue
            group_outputs, group_donor = _apply_chain(
                harvest_m3 * share,
                group,
                grade_mix,
                class_allocation,
                class_ratios,
            )
            _sum_component(components, group_outputs)
            donor_component_count += group_donor
        tier = "bc_anchor_species_group"
        species_basis = "BC 2028 species-group volume share"
        source = "BC 2028 supply, Stage A, and FFCM archive BC lines"
        split_available = True
    else:
        components, donor_component_count = _apply_chain(
            harvest_m3,
            "aggregate",
            grade_mix,
            class_allocation,
            class_ratios,
        )
        tier = "donor_coefficient"
        species_basis = "BC aggregate donor; no provincial species/class split"
        source = "BC 2028 aggregate donor coefficients"
        split_available = False
        donor_component_count = len(CLASS_ORDER)

    # The class feedstock above includes the recoverable residue stream.  The
    # primary feedstock is the log-grade share; the residue feedstock is the
    # recoverable harvest residue routed to PELLET/BIO.
    primary_feedstock = (
        components["feedstock_saw_m3"]
        + components["feedstock_pulp_m3"]
        + components["feedstock_panel_m3"]
        + components["feedstock_pellet_m3"]
        + components["feedstock_bio_m3"]
        - components["harvest_residue_recoverable_m3"]
    )
    total_feedstock = (
        components["feedstock_saw_m3"]
        + components["feedstock_pulp_m3"]
        + components["feedstock_panel_m3"]
        + components["feedstock_pellet_m3"]
        + components["feedstock_bio_m3"]
    )
    _require_close(
        primary_feedstock + components["harvest_residue_recoverable_m3"],
        total_feedstock,
        f"{province} primary plus residue feedstock",
    )
    _require_close(
        primary_feedstock,
        harvest_m3,
        f"{province} primary feedstock conservation",
    )

    process_residue_odt = sum(
        components[f"residue_{facility_class.lower()}_odt"]
        for facility_class in CLASS_ORDER
    )
    process_residue_m3 = process_residue_odt / RESIDUE_DENSITY_ODT_M3
    total_residue_gross_odt = (
        process_residue_odt + components["harvest_residue_gross_odt"]
    )
    total_residue_gross_m3 = (
        process_residue_m3 + components["harvest_residue_gross_m3"]
    )
    total_residue_recoverable_odt = (
        process_residue_odt + components["harvest_residue_recoverable_odt"]
    )
    total_residue_recoverable_m3 = (
        process_residue_m3 + components["harvest_residue_recoverable_m3"]
    )

    rsyc_factor = rsyc_factors.get(province)
    if rsyc_factor is None:
        rsyc_base = 0.0
        model_recoverable = components["harvest_residue_gross_odt"] * RSYC_BASE_RECOVERY
        rsyc_ratio = 0.0
        rsyc_status = "not_available"
    elif harvest_m3 <= 0.0:
        rsyc_base = 0.0
        model_recoverable = 0.0
        rsyc_ratio = 0.0
        rsyc_status = "zero_harvest"
    else:
        rsyc_base = harvest_m3 * rsyc_factor
        model_recoverable = components["harvest_residue_gross_odt"] * RSYC_BASE_RECOVERY
        rsyc_ratio = model_recoverable / rsyc_base if rsyc_base > 0.0 else 0.0
        if rsyc_ratio < 0.80:
            rsyc_status = "model_below_rsyc"
        elif rsyc_ratio > 1.25:
            rsyc_status = "model_above_rsyc"
        else:
            rsyc_status = "within_rsyc_range"

    row: dict[str, Any] = {
        "province": province,
        "province_name": PROVINCES[province],
        "harvest_m3": harvest_m3,
        "sawlog_m3": 0.0,
        "pulpwood_m3": 0.0,
        "other_log_m3": 0.0,
        "primary_feedstock_m3": primary_feedstock,
        "residue_feedstock_m3": components["harvest_residue_recoverable_m3"],
        "total_feedstock_m3": total_feedstock,
        "sawlog_feedstock_m3": components["feedstock_saw_m3"],
        "pulp_feedstock_m3": components["feedstock_pulp_m3"],
        "panel_feedstock_m3": components["feedstock_panel_m3"],
        "pellet_feedstock_m3": components["feedstock_pellet_m3"],
        "bio_feedstock_m3": components["feedstock_bio_m3"],
        "sawlog_product_m3": components["product_saw"],
        "pulp_product_odt": components["product_pulp"],
        "panel_product_m3": components["product_panel"],
        "pellet_product_odt": components["product_pellet"],
        "bio_product_mwh": components["product_bio"],
        "sawlog_residue_m3": components["residue_saw_odt"] / RESIDUE_DENSITY_ODT_M3,
        "sawlog_residue_odt": components["residue_saw_odt"],
        "pulp_residue_m3": components["residue_pulp_odt"] / RESIDUE_DENSITY_ODT_M3,
        "pulp_residue_odt": components["residue_pulp_odt"],
        "panel_residue_m3": components["residue_panel_odt"] / RESIDUE_DENSITY_ODT_M3,
        "panel_residue_odt": components["residue_panel_odt"],
        "pellet_residue_m3": components["residue_pellet_odt"] / RESIDUE_DENSITY_ODT_M3,
        "pellet_residue_odt": components["residue_pellet_odt"],
        "bio_residue_m3": components["residue_bio_odt"] / RESIDUE_DENSITY_ODT_M3,
        "bio_residue_odt": components["residue_bio_odt"],
        "process_residue_m3": process_residue_m3,
        "process_residue_odt": process_residue_odt,
        "harvest_residue_gross_m3": components["harvest_residue_gross_m3"],
        "harvest_residue_gross_odt": components["harvest_residue_gross_odt"],
        "harvest_residue_recoverable_m3": components[
            "harvest_residue_recoverable_m3"
        ],
        "harvest_residue_recoverable_odt": components[
            "harvest_residue_recoverable_odt"
        ],
        "harvest_residue_unrecovered_m3": components[
            "harvest_residue_unrecovered_m3"
        ],
        "harvest_residue_unrecovered_odt": components[
            "harvest_residue_unrecovered_odt"
        ],
        "total_residue_gross_m3": total_residue_gross_m3,
        "total_residue_gross_odt": total_residue_gross_odt,
        "total_residue_recoverable_m3": total_residue_recoverable_m3,
        "total_residue_recoverable_odt": total_residue_recoverable_odt,
        "process_residue_odt_per_m3_feedstock": process_residue_odt / total_feedstock
        if total_feedstock > 0.0
        else 0.0,
        "harvest_residue_gross_odt_per_m3_harvest": components[
            "harvest_residue_gross_odt"
        ]
        / harvest_m3
        if harvest_m3 > 0.0
        else 0.0,
        "total_residue_gross_odt_per_m3_harvest": total_residue_gross_odt
        / harvest_m3
        if harvest_m3 > 0.0
        else 0.0,
        "rsyc_base_recoverable_odt": rsyc_base,
        "model_recoverable_odt_base": model_recoverable,
        "rsyc_ratio": rsyc_ratio,
        "rsyc_status": rsyc_status,
        "coefficient_tier": tier,
        "coefficient_source": source,
        "species_group_basis": species_basis,
        "province_species_split_available": "true" if split_available else "false",
        "donor_component_count": donor_component_count,
        "model_estimate": "true",
        "observed": "false",
        "policy_grade": "false",
        "facility_assignment": "false",
        "notes": (
            "BC species-group anchor for grade mix and class allocation; "
            "classes without a group-specific BC line use the BC aggregate donor."
            if province == "BC"
            else "BC aggregate donor; provincial species/class split is unavailable."
        ),
    }
    return ProvinceResult(
        province=province,
        harvest_m3=harvest_m3,
        row=row,
        donor_component_count=donor_component_count,
    )


def _fill_grade_columns(
    row: dict[str, Any],
    province: str,
    harvest_m3: float,
    grade_mix: pd.DataFrame,
    group_shares: Mapping[str, float],
) -> None:
    if province == "BC":
        sawlog = pulpwood = other = 0.0
        for group in ("softwood", "hardwood"):
            share = group_shares.get(group, 0.0)
            if share <= 0.0:
                continue
            mix = _lookup_grade_mix(grade_mix, group)
            sawlog += harvest_m3 * share * float(mix["sawlog_share"])
            pulpwood += harvest_m3 * share * float(mix["pulpwood_share"])
            other += harvest_m3 * share * float(mix["other_share"])
    else:
        mix = _lookup_grade_mix(grade_mix, "aggregate")
        sawlog = harvest_m3 * float(mix["sawlog_share"])
        pulpwood = harvest_m3 * float(mix["pulpwood_share"])
        other = harvest_m3 * float(mix["other_share"])
    row["sawlog_m3"] = sawlog
    row["pulpwood_m3"] = pulpwood
    row["other_log_m3"] = other
    _require_close(sawlog + pulpwood + other, harvest_m3, f"{province} grade split")


def _load_rsyc_factors(path: Path) -> dict[str, float]:
    if not path.exists():
        raise ProductChainError(f"RSYC summary not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    factors = payload.get("province_factors")
    if not isinstance(factors, list) or not factors:
        raise ProductChainError("RSYC summary has no province_factors")
    result: dict[str, float] = {}
    for record in factors:
        if not isinstance(record, dict):
            raise ProductChainError("RSYC province factor is not an object")
        province = str(record.get("province", "")).strip().upper()
        if not province:
            raise ProductChainError("RSYC province factor has no province")
        value = _finite_nonnegative(
            record.get("recoverable_odt_per_m3_base", 0.0),
            f"RSYC {province} recoverable factor",
        )
        result[province] = value
    return result


def _build_totals(
    rows: Sequence[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, float]]:
    numeric_columns = [
        column
        for column in rows[0]
        if column
        not in {
            "province",
            "province_name",
            "rsyc_status",
            "coefficient_tier",
            "coefficient_source",
            "species_group_basis",
            "province_species_split_available",
            "model_estimate",
            "observed",
            "policy_grade",
            "facility_assignment",
            "donor_component_count",
            "notes",
        }
    ]
    totals: dict[str, float] = {}
    for column in numeric_columns:
        totals[column] = float(sum(float(row[column]) for row in rows))
    totals["province_count"] = float(len(rows))
    totals["non_bc_donor_province_count"] = float(
        sum(1 for row in rows if row["province"] != "BC")
    )
    totals["bc_anchor_province_count"] = float(
        sum(1 for row in rows if row["province"] == "BC")
    )
    totals["process_residue_odt_per_m3_feedstock"] = (
        totals["process_residue_odt"] / totals["total_feedstock_m3"]
        if totals["total_feedstock_m3"] > 0.0
        else 0.0
    )
    totals["harvest_residue_gross_odt_per_m3_harvest"] = (
        totals["harvest_residue_gross_odt"] / totals["harvest_m3"]
        if totals["harvest_m3"] > 0.0
        else 0.0
    )
    totals["total_residue_gross_odt_per_m3_harvest"] = (
        totals["total_residue_gross_odt"] / totals["harvest_m3"]
        if totals["harvest_m3"] > 0.0
        else 0.0
    )
    totals["rsyc_ratio"] = (
        totals["model_recoverable_odt_base"] / totals["rsyc_base_recoverable_odt"]
        if totals["rsyc_base_recoverable_odt"] > 0.0
        else 0.0
    )
    metric_rows: list[dict[str, Any]] = []
    metric_specs = [
        ("harvest_m3", "m3", "Provincial allocation 119m harvest"),
        ("sawlog_m3", "m3", "Sawlog-grade harvest volume"),
        ("pulpwood_m3", "m3", "Pulpwood-grade harvest volume"),
        ("other_log_m3", "m3", "Other-log grade harvest volume"),
        ("primary_feedstock_m3", "m3", "Log-grade feedstock to facilities"),
        ("residue_feedstock_m3", "m3", "Recoverable harvest residue routed to PELLET/BIO"),
        ("total_feedstock_m3", "m3", "Primary plus recoverable residue feedstock"),
        ("sawlog_feedstock_m3", "m3", "Feedstock to SAW"),
        ("pulp_feedstock_m3", "m3", "Feedstock to PULP"),
        ("panel_feedstock_m3", "m3", "Feedstock to PANEL"),
        ("pellet_feedstock_m3", "m3", "Feedstock to PELLET"),
        ("bio_feedstock_m3", "m3", "Feedstock to BIO"),
        ("sawlog_product_m3", "m3", "Lumber product"),
        ("pulp_product_odt", "odt", "Pulp product"),
        ("panel_product_m3", "m3", "Panel product"),
        ("pellet_product_odt", "odt", "Pellet product"),
        ("bio_product_mwh", "MWh", "Electricity product"),
        ("sawlog_residue_odt", "odt", "Sawmill residue"),
        ("pulp_residue_odt", "odt", "Pulping residue"),
        ("panel_residue_odt", "odt", "Panel residue"),
        ("pellet_residue_odt", "odt", "Pellet residue"),
        ("bio_residue_odt", "odt", "Bio residue"),
        ("process_residue_odt", "odt", "Process residue across all classes"),
        ("harvest_residue_gross_odt", "odt", "Gross harvest residue"),
        ("harvest_residue_recoverable_odt", "odt", "Recoverable harvest residue"),
        ("harvest_residue_unrecovered_odt", "odt", "Unrecovered harvest residue"),
        ("total_residue_gross_odt", "odt", "Process plus gross harvest residue"),
        ("total_residue_recoverable_odt", "odt", "Process plus recoverable harvest residue"),
        ("process_residue_odt_per_m3_feedstock", "odt/m3", "Process residue ratio"),
        ("harvest_residue_gross_odt_per_m3_harvest", "odt/m3", "Harvest residue ratio"),
        ("total_residue_gross_odt_per_m3_harvest", "odt/m3", "Total residue ratio"),
        ("rsyc_base_recoverable_odt", "odt", "RSYC base recoverable residue at 119m harvest"),
        ("model_recoverable_odt_base", "odt", "Model gross harvest residue x 0.60"),
        ("rsyc_ratio", "ratio", "Model recoverable / RSYC base"),
        ("province_count", "count", "Provinces and territories"),
        ("bc_anchor_province_count", "count", "BC anchor provinces"),
        ("non_bc_donor_province_count", "count", "Donor-coefficient provinces"),
    ]
    for metric, unit, notes in metric_specs:
        value = totals.get(metric, 0.0)
        metric_rows.append(
            {
                "metric": metric,
                "unit": unit,
                "value": float(value),
                "notes": notes,
            }
        )
    return metric_rows, totals


def _validate_rows(
    rows: Sequence[dict[str, Any]],
    coefficients: Sequence[dict[str, Any]],
    class_ratios: pd.DataFrame,
    stage_b_ratio: float,
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []

    def record(check_id: str, passed: bool, detail: str) -> None:
        checks.append(
            {
                "check_id": check_id,
                "status": "PASS" if passed else "FAIL",
                "detail": detail,
            }
        )
        if not passed:
            raise ProductChainError(f"{check_id}: {detail}")

    numeric_keys = [
        key
        for key in rows[0]
        if key
        not in {
            "province",
            "province_name",
            "rsyc_status",
            "coefficient_tier",
            "coefficient_source",
            "species_group_basis",
            "province_species_split_available",
            "model_estimate",
            "observed",
            "policy_grade",
            "facility_assignment",
            "notes",
        }
    ]
    invalid: list[str] = []
    negative: list[str] = []
    for row in rows:
        for key in numeric_keys:
            value = row[key]
            if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                invalid.append(f"{row['province']}.{key}")
            elif float(value) < 0.0:
                negative.append(f"{row['province']}.{key}")
    record("finite_values", not invalid, f"{len(invalid)} non-finite fields")
    record("nonnegative_values", not negative, f"{len(negative)} negative fields")

    grade_errors: list[str] = []
    feedstock_errors: list[str] = []
    residue_errors: list[str] = []
    for row in rows:
        if abs(
            row["sawlog_m3"]
            + row["pulpwood_m3"]
            + row["other_log_m3"]
            - row["harvest_m3"]
        ) > 1e-6 * max(1.0, row["harvest_m3"]):
            grade_errors.append(row["province"])
        if abs(
            row["primary_feedstock_m3"]
            + row["residue_feedstock_m3"]
            - row["total_feedstock_m3"]
        ) > 1e-6 * max(1.0, row["total_feedstock_m3"]):
            feedstock_errors.append(row["province"])
        expected_total = (
            row["process_residue_odt"] + row["harvest_residue_gross_odt"]
        )
        if abs(row["total_residue_gross_odt"] - expected_total) > 1e-6 * max(
            1.0, expected_total
        ):
            residue_errors.append(row["province"])
    record("grade_conservation", not grade_errors, f"failed: {grade_errors}")
    record(
        "feedstock_conservation",
        not feedstock_errors,
        f"failed: {feedstock_errors}",
    )
    record("residue_total_consistency", not residue_errors, f"failed: {residue_errors}")

    donor_bad = [
        row["province"]
        for row in rows
        if row["province"] != "BC" and row["coefficient_tier"] != "donor_coefficient"
    ]
    anchor_bad = [
        row["province"]
        for row in rows
        if row["province"] == "BC"
        and not str(row["coefficient_tier"]).startswith("bc_anchor")
    ]
    record("donor_labels", not donor_bad, f"failed: {donor_bad}")
    record("bc_anchor_labels", not anchor_bad, f"failed: {anchor_bad}")
    label_bad = [
        row["province"]
        for row in rows
        if row["model_estimate"] != "true"
        or row["observed"] != "false"
        or row["policy_grade"] != "false"
        or row["facility_assignment"] != "false"
    ]
    record("evidence_labels", not label_bad, f"failed: {label_bad}")

    saw_ratio = float(
        class_ratios.loc[
            (class_ratios["species_group"] == "aggregate")
            & (class_ratios["facility_class"] == "SAW")
            & (class_ratios["coefficient_name"] == "residue_total_odt_per_m3"),
            "value",
        ].iloc[0]
    )
    record(
        "bc_sawmill_residue_crosscheck",
        abs(saw_ratio - stage_b_ratio) < 1e-6,
        f"FFCM {saw_ratio:.12f} vs Stage B {stage_b_ratio:.12f}",
    )

    coefficient_tiers = {str(record["tier"]) for record in coefficients}
    record(
        "coefficient_tiers_present",
        {"bc_anchor_species_group", "donor_coefficient"}.issubset(coefficient_tiers),
        f"tiers={sorted(coefficient_tiers)}",
    )
    return checks


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]], fieldnames: Sequence[str]) -> None:
    if not rows:
        raise ProductChainError(f"refusing to write empty CSV: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fieldnames))
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    key: (f"{value:.12g}" if isinstance(value, float) else value)
                    for key, value in row.items()
                }
            )


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def _report(
    summary: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    coefficients: Sequence[Mapping[str, Any]],
) -> str:
    totals = summary["national_totals"]
    rsyc = summary["rsyc_comparison"]
    lines = [
        "# National Product/Residue Chain v1",
        "",
        f"Generated {summary['generated_utc']}.",
        "",
        "This is a coefficient-transfer screening model anchored on the 2028 BC",
        "product/residue chain.  It is not a facility assignment, policy, AAC, or",
        "operating plan.  Every output row is `model_estimate=true`,",
        "`observed=false`, `policy_grade=false`, and `facility_assignment=false`.",
        "",
        "## Method",
        "",
        "* BC is the only province with a species-resolved product chain.  BC uses",
        "  softwood/hardwood grade shares and class allocations from the 2028 BC",
        "  supply and Stage A assignment.  Class technology ratios come from the BC",
        "  FFCM lines; classes without a group-specific BC line use the aggregate",
        "  donor ratio.",
        "* Every other province receives the BC aggregate donor coefficient and is",
        "  labelled `donor_coefficient`; no provincial yield is fabricated.",
        "* PANEL is absent from BC Stage A.  Its allocation is the explicit BC panel",
        "  nameplate share of sawlog plus panel nameplate, labelled as a BC anchor.",
        "* Harvest residue is gross BC supply with the BC Stage A residue recovery",
        "  share applied.  The recoverable portion is routed to PELLET/BIO; the",
        "  remainder is reported as unrecovered roadside residue.",
        "",
        "## Coefficient tiers",
        "",
        "| Tier | Scope | Count |",
        "|---|---|---:|",
    ]
    tier_counts: dict[str, int] = defaultdict(int)
    for record in coefficients:
        tier_counts[str(record["tier"])] += 1
    for tier, count in sorted(tier_counts.items()):
        lines.append(f"| `{tier}` | coefficient table | {count} |")
    lines.extend(
        [
            "",
            "## National totals",
            "",
            "| Metric | Unit | Value |",
            "|---|---|---:|",
        ]
    )
    key_metrics = [
        "harvest_m3",
        "sawlog_m3",
        "pulpwood_m3",
        "other_log_m3",
        "sawlog_feedstock_m3",
        "pulp_feedstock_m3",
        "panel_feedstock_m3",
        "pellet_feedstock_m3",
        "bio_feedstock_m3",
        "residue_feedstock_m3",
        "total_feedstock_m3",
        "sawlog_product_m3",
        "pulp_product_odt",
        "panel_product_m3",
        "pellet_product_odt",
        "bio_product_mwh",
        "process_residue_odt",
        "harvest_residue_gross_odt",
        "harvest_residue_recoverable_odt",
        "harvest_residue_unrecovered_odt",
        "total_residue_gross_odt",
        "total_residue_recoverable_odt",
        "process_residue_odt_per_m3_feedstock",
        "harvest_residue_gross_odt_per_m3_harvest",
        "total_residue_gross_odt_per_m3_harvest",
    ]
    for metric in key_metrics:
        value = totals.get(metric, 0.0)
        unit = next(
            (
                record["unit"]
                for record in summary["totals_table"]
                if record["metric"] == metric
            ),
            "",
        )
        lines.append(f"| `{metric}` | {unit} | {value:,.6f} |")
    lines.extend(
        [
            "",
            "## Provincial products/residue",
            "",
            "| Province | Harvest m3 | SAW feed | PULP feed | PANEL feed | PELLET feed | BIO feed | Process residue odt | Gross harvest residue odt | Tier |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    for row in rows:
        lines.append(
            "| {province} | {harvest_m3:,.0f} | {sawlog_feedstock_m3:,.0f} | "
            "{pulp_feedstock_m3:,.0f} | {panel_feedstock_m3:,.0f} | "
            "{pellet_feedstock_m3:,.0f} | {bio_feedstock_m3:,.0f} | "
            "{process_residue_odt:,.0f} | {harvest_residue_gross_odt:,.0f} | "
            "`{coefficient_tier}` |".format(**row)
        )
    lines.extend(
        [
            "",
            "## National totals and RSYC comparison",
            "",
            f"* Model gross harvest residue: **{totals['harvest_residue_gross_odt']:,.0f} odt**.",
            f"* Model recoverable at the RSYC base 0.60 recovery: **{totals['model_recoverable_odt_base']:,.0f} odt**.",
            f"* RSYC base recoverable residue at the 119 Mm3 provincial harvest: **{totals['rsyc_base_recoverable_odt']:,.0f} odt**.",
            f"* Ratio: **{totals['rsyc_ratio']:.3f}**.",
            f"* Status: **{rsyc['status']}**.",
            "",
            "The RSYC comparison is a screening check, not a calibration.  BC donor",
            "coefficients are applied to provinces with different species and",
            "residue regimes, so a province-level difference is expected.",
            "",
            "## Tests and status",
            "",
        ]
    )
    for check in summary["validation"]["checks"]:
        lines.append(
            f"* `{check['check_id']}`: **{check['status']}** — {check['detail']}"
        )
    lines.extend(
        [
            "",
            "## Caveats",
            "",
            "* No facility assignment, capacity check, transport, cost, or revenue is",
            "  modelled here.  Feedstock and residue are provincial totals only.",
            "* The FFCM archive is a 2018 engineering archive; class ratios are",
            "  technology proxies, not current operating measured yields.",
            "* The BC aggregate donor does not reproduce provincial species mixes.",
            "  Provinces with a large hardwood share may be over-assigned to sawlog.",
            "* BC species-group rows fall back to the BC aggregate ratio where no",
            "  group-specific BC line exists (PANEL/PELLET/BIO and hardwood PULP).",
            "* Harvest residue recovery uses the BC Stage A share; it is not a",
            "  provincial recovery measurement.",
            "* No UI or public OSeCAN surface is changed.",
            "",
            "## Reproduce",
            "",
            "```bash",
            ".venv/bin/python -m workflows.national.facilities.national_product_chain",
            "```",
            "",
            f"Status: **{summary['status']}**.  No policy/AAC claim is made.",
            "",
        ]
    )
    return "\n".join(lines)


def build(output_dir: Path | None = None) -> dict[str, Any]:
    """Build the national product/residue chain and write the artifact."""
    target_dir = Path(output_dir) if output_dir is not None else OUTPUT_DIR
    generated_utc = datetime.now(UTC).isoformat(timespec="seconds")

    harvest = _read_csv(
        HARVEST_INPUT,
        ["province", "harvest_m3"],
        "provincial 119m allocation",
    )
    harvest["province"] = harvest["province"].astype(str).str.upper()
    harvest["harvest_m3"] = pd.to_numeric(harvest["harvest_m3"], errors="raise")
    if (harvest["harvest_m3"] < 0).any():
        raise ProductChainError("provincial allocation contains negative harvest")
    if set(harvest["province"]) != set(PROVINCES):
        missing = set(PROVINCES) - set(harvest["province"])
        extra = set(harvest["province"]) - set(PROVINCES)
        raise ProductChainError(
            f"provincial allocation province set mismatch; missing={missing}, extra={extra}"
        )
    if harvest["province"].duplicated().any():
        raise ProductChainError("provincial allocation has duplicate provinces")

    supply = _read_csv(
        BC_SUPPLY,
        [
            "zone",
            "year",
            "species",
            "product",
            "volume_m3",
            "residue_m3",
            "residue_odt",
        ],
        "BC supply by zone",
    )
    stage_a = _read_csv(
        BC_STAGE_A,
        ["facility_class", "grade", "species", "volume_m3"],
        "BC Stage A assignment",
    )
    stage_b = _read_csv(
        BC_STAGE_B,
        ["source_facility", "residue_type", "generated_odt"],
        "BC Stage B residue",
    )
    facilities = _read_csv(
        BC_FACILITIES,
        ["facility_class", "capacity_m3_y"],
        "BC facility inventory",
    )
    ffcm_lines = _read_csv(
        FFCM_LINES,
        [
            "processing_line",
            "facility",
            "feedstock",
            "primary_product",
            "infeed_rate",
            "infeed_unit",
        ],
        "FFCM processing lines",
    )
    ffcm_flows = _read_csv(
        FFCM_FLOWS,
        ["processing_line", "commodity", "value"],
        "FFCM processing line flows",
    )
    rsyc_factors = _load_rsyc_factors(RSYC_SUMMARY)

    grade_mix, group_shares = _build_grade_mix(supply, stage_a)
    panel_share, panel_capacity = _panel_share_of_sawlog(stage_a, facilities)
    class_allocation = _build_class_allocation(stage_a, panel_share)
    class_ratios, _ = _build_class_ratios(ffcm_lines, ffcm_flows)
    coefficients = _coefficient_rows(
        grade_mix, class_allocation, class_ratios, panel_share
    )

    harvest_lookup = {
        str(row["province"]): float(row["harvest_m3"])
        for _, row in harvest.iterrows()
    }
    rows: list[dict[str, Any]] = []
    for province in PROVINCES:
        result = _build_province_row(
            province,
            harvest_lookup[province],
            grade_mix,
            class_allocation,
            class_ratios,
            group_shares,
            rsyc_factors,
        )
        _fill_grade_columns(
            result.row,
            province,
            result.harvest_m3,
            grade_mix,
            group_shares,
        )
        rows.append(result.row)

    totals_table, totals = _build_totals(rows)
    stage_b_ratio = float(
        pd.to_numeric(stage_b["generated_odt"], errors="raise").sum()
    ) / float(
        pd.to_numeric(
            stage_a.loc[stage_a["facility_class"] == "SAW", "volume_m3"],
            errors="raise",
        ).sum()
    )
    checks = _validate_rows(rows, coefficients, class_ratios, stage_b_ratio)

    national_rsyc = (
        totals["model_recoverable_odt_base"] / totals["rsyc_base_recoverable_odt"]
        if totals["rsyc_base_recoverable_odt"] > 0.0
        else 0.0
    )
    if national_rsyc < 0.80:
        rsyc_status = "model_below_rsyc"
    elif national_rsyc > 1.25:
        rsyc_status = "model_above_rsyc"
    else:
        rsyc_status = "within_rsyc_range"

    summary: dict[str, Any] = {
        "artifact": "national_product_chain_v1",
        "artifact_version": "1.0.0",
        "generated_utc": generated_utc,
        "status": "pass",
        "model_estimate": True,
        "observed": False,
        "policy_grade": False,
        "facility_assignment": False,
        "ui_or_public_osecan_changed": False,
        "scope": (
            "Provincial feedstock, product, and residue totals from the 119 Mm3 "
            "provincial allocation; no facility assignment."
        ),
        "harvest_input": str(HARVEST_INPUT.relative_to(REPO_ROOT)),
        "harvest_total_m3": totals["harvest_m3"],
        "province_count": len(rows),
        "coefficient_counts": {
            "rows": len(coefficients),
            "bc_anchor_species_group": sum(
                1
                for record in coefficients
                if record["tier"] == "bc_anchor_species_group"
            ),
            "donor_coefficient": sum(
                1 for record in coefficients if record["tier"] == "donor_coefficient"
            ),
        },
        "panel_anchor": {
            "panel_nameplate_m3_y": panel_capacity,
            "panel_share_of_sawlog": panel_share,
            "basis": "BC panel nameplate divided by BC sawlog plus panel nameplate",
        },
        "coefficient_tiers": {
            "BC": "bc_anchor_species_group",
            "non_BC": "donor_coefficient",
        },
        "national_totals": totals,
        "totals_table": totals_table,
        "rsyc_comparison": {
            "base_recovery_fraction": RSYC_BASE_RECOVERY,
            "model_recoverable_odt": totals["model_recoverable_odt_base"],
            "rsyc_base_recoverable_odt": totals["rsyc_base_recoverable_odt"],
            "ratio": national_rsyc,
            "status": rsyc_status,
            "province_factors_available": len(rsyc_factors),
            "basis": (
                "RSYC recoverable_odt_per_m3_base x provincial harvest; "
                "model gross harvest residue x 0.60"
            ),
        },
        "validation": {
            "checks": checks,
            "passed": all(check["status"] == "PASS" for check in checks),
            "failed_check_count": sum(
                1 for check in checks if check["status"] != "PASS"
            ),
        },
        "caveats": [
            "No facility assignment, capacity, transport, cost, or revenue is modelled.",
            "FFCM class ratios are 2018 engineering proxies, not current measured yields.",
            "BC aggregate donor does not reproduce provincial species mixes.",
            "BC species-group rows fall back to the BC aggregate ratio where no group-specific BC line exists (PANEL/PELLET/BIO and hardwood PULP).",
            "Harvest residue recovery uses the BC Stage A share, not a provincial measurement.",
            "The RSYC comparison is a screening check, not a calibration.",
            "No UI or public OSeCAN surface is changed.",
        ],
        "inputs": {
            str(path.relative_to(REPO_ROOT)): sha256(path)
            for path in (
                HARVEST_INPUT,
                BC_SUPPLY,
                BC_STAGE_A,
                BC_STAGE_B,
                BC_FACILITIES,
                FFCM_LINES,
                FFCM_FLOWS,
                RSYC_SUMMARY,
            )
        },
        "reproduce": (
            ".venv/bin/python -m "
            "workflows.national.facilities.national_product_chain"
        ),
    }

    row_fields = [
        "province",
        "province_name",
        "harvest_m3",
        "sawlog_m3",
        "pulpwood_m3",
        "other_log_m3",
        "primary_feedstock_m3",
        "residue_feedstock_m3",
        "total_feedstock_m3",
        "sawlog_feedstock_m3",
        "pulp_feedstock_m3",
        "panel_feedstock_m3",
        "pellet_feedstock_m3",
        "bio_feedstock_m3",
        "sawlog_product_m3",
        "pulp_product_odt",
        "panel_product_m3",
        "pellet_product_odt",
        "bio_product_mwh",
        "sawlog_residue_m3",
        "sawlog_residue_odt",
        "pulp_residue_m3",
        "pulp_residue_odt",
        "panel_residue_m3",
        "panel_residue_odt",
        "pellet_residue_m3",
        "pellet_residue_odt",
        "bio_residue_m3",
        "bio_residue_odt",
        "process_residue_m3",
        "process_residue_odt",
        "harvest_residue_gross_m3",
        "harvest_residue_gross_odt",
        "harvest_residue_recoverable_m3",
        "harvest_residue_recoverable_odt",
        "harvest_residue_unrecovered_m3",
        "harvest_residue_unrecovered_odt",
        "total_residue_gross_m3",
        "total_residue_gross_odt",
        "total_residue_recoverable_m3",
        "total_residue_recoverable_odt",
        "process_residue_odt_per_m3_feedstock",
        "harvest_residue_gross_odt_per_m3_harvest",
        "total_residue_gross_odt_per_m3_harvest",
        "rsyc_base_recoverable_odt",
        "model_recoverable_odt_base",
        "rsyc_ratio",
        "rsyc_status",
        "coefficient_tier",
        "coefficient_source",
        "species_group_basis",
        "province_species_split_available",
        "donor_component_count",
        "model_estimate",
        "observed",
        "policy_grade",
        "facility_assignment",
        "notes",
    ]
    coefficient_fields = [
        "coefficient_scope",
        "species_group",
        "facility_class",
        "grade",
        "coefficient_name",
        "value",
        "unit",
        "tier",
        "source",
        "notes",
    ]
    totals_fields = ["metric", "unit", "value", "notes"]

    target_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(target_dir / "product_chain_by_province.csv", rows, row_fields)
    _write_csv(target_dir / "product_chain_totals.csv", totals_table, totals_fields)
    _write_csv(target_dir / "coefficients.csv", coefficients, coefficient_fields)
    _write_json(target_dir / "summary.json", summary)
    (target_dir / "NATIONAL_PRODUCT_CHAIN_V1.md").write_text(
        _report(summary, rows, coefficients), encoding="utf-8"
    )

    outputs: dict[str, Any] = {}
    for path in sorted(target_dir.iterdir()):
        if not path.is_file() or path.name == "manifest.json":
            continue
        outputs[path.name] = {
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
        }
    manifest = {
        "artifact": "national_product_chain_v1",
        "artifact_version": "1.0.0",
        "generated_utc": generated_utc,
        "inputs": summary["inputs"],
        "outputs": outputs,
        "record_counts": {
            "province_rows": len(rows),
            "coefficient_rows": len(coefficients),
            "total_rows": len(totals_table),
        },
        "validation": summary["validation"],
        "rsyc_comparison": summary["rsyc_comparison"],
        "reproduce": summary["reproduce"],
    }
    _write_json(target_dir / "manifest.json", manifest)
    return summary


def main() -> None:
    summary = build()
    print(
        json.dumps(
            {
                "artifact": summary["artifact"],
                "harvest_total_m3": summary["harvest_total_m3"],
                "total_feedstock_m3": summary["national_totals"][
                    "total_feedstock_m3"
                ],
                "process_residue_odt": summary["national_totals"][
                    "process_residue_odt"
                ],
                "harvest_residue_gross_odt": summary["national_totals"][
                    "harvest_residue_gross_odt"
                ],
                "rsyc_ratio": summary["national_totals"]["rsyc_ratio"],
                "status": summary["status"],
                "output_dir": str(OUTPUT_DIR.relative_to(REPO_ROOT)),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
