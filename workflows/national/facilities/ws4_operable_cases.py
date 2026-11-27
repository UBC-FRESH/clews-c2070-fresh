"""Rerun the sustainable WS4 cases on a share-based operable landbase.

The operability input is the ``screen_core30`` case of the national operable
proxy v2: SCANFI median age >= 30 years, NBAC 2020 burned pixels excluded, and
block centres within 10 km of a public NRN road.  There is no legal THLB,
national slope DEM, or spatial road overlay in the workspace.  The mask used
here is therefore an area-share proxy: inside each province, blocks are ranked
by a stable hash of ``province:block_id`` and selected until the selected
forest area matches the province's ``operable_share_of_treed`` applied to the
WS4 990 m block forest area.

The 100, 119, and 150 Mm3/yr cases keep their frozen provincial
``allocated_share`` controls.  Eligible blocks are the operable-mask blocks
that also satisfy the MAI rotation rule.  No block is split, scaled, harvested
below rotation, or harvested outside the mask.  A province that cannot meet a
period target records a shortfall and the schedule continues; the
cross-province national fallback is disabled so provincial shortfalls are not
hidden.  WS4 simulate is the authority for harvested volume and treated area.

All outputs are model estimates.  This is not an AAC, a legal THLB, a slope or
spatial road screen, an official OSeCAN change, or a national supply claim.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from workflows.national.facilities import ws4_chain_119m as chain
from workflows.national.facilities import ws4_sustainable_119m as base

REPO_ROOT = base.REPO_ROOT
DATA_ROOT = base.DATA_ROOT
SCREEN_CSV = (
    DATA_ROOT
    / "derived/national/managed_landbase/operable_proxy_v2"
    / "operable_proxy_v2_by_province.csv"
)
ROAD_CLASSES_CSV = (
    DATA_ROOT
    / "derived/national/managed_landbase/operable_proxy_v2"
    / "road_distance_classes.csv"
)
GAPS_CSV = (
    DATA_ROOT
    / "derived/national/managed_landbase/operable_proxy_v2"
    / "gaps.csv"
)
SCREEN_CASE = "screen_core30"
MASK_DIR = DATA_ROOT / "derived/ws4/operable-mask"
CASES_ROOT = DATA_ROOT / "derived/national/facilities/ws4_operable_cases"
SCENARIO_SUMMARY = (
    DATA_ROOT
    / "derived/national/facilities/national_facility_chain_scenarios_v9"
    / "scenario_summary.csv"
)
FULL_FOREST_CASE_SUMMARIES = {
    "100m": (
        DATA_ROOT
        / "derived/national/facilities/ws4_sustainable_cases/100m/summary.json"
    ),
    "119m": (
        DATA_ROOT
        / "derived/national/facilities/ws4_sustainable_119m/summary.json"
    ),
    "150m": (
        DATA_ROOT
        / "derived/national/facilities/ws4_sustainable_cases/150m/summary.json"
    ),
}

PERIODS = base.PERIODS
PERIOD_LENGTH = base.PERIOD_LENGTH
HORIZON = base.HORIZON
PROVINCES = base.PROVINCES
SECONDARY_BASIS = "sustainable_achieved"
MAX_SEARCH_ITERATIONS = 12

ARTIFACT = "ws4_operable_cases"
ARTIFACT_VERSION = "1.0.0"
ARTIFACT_TITLE = "Sustainable WS4 cases on a share-based operable landbase"

LABELS: dict[str, bool] = {
    "model_estimate": True,
    "observed": False,
    "policy_grade": False,
    "national_claim": False,
    "ws4_derived": True,
    "sustainable_schedule": True,
    "share_based_operable_proxy": True,
    "legal_thlb": False,
    "slope_screen": False,
    "official_osecan_changed": False,
    "chain_v51_unchanged": True,
}
ROW_LABELS: dict[str, str] = {
    key: "true" if value else "false" for key, value in LABELS.items()
}

# Frozen v5.1 100/119/150 case expectations from the model card, used as an
# independent guard on the scenario-summary read rather than as comparison
# values.  (metric, documented value, absolute tolerance)
REFERENCE_EXPECTED: dict[str, tuple[tuple[str, float, float], ...]] = {
    "100m": (
        ("solid_product_m3", 31_693_000.0, 1_000.0),
        ("pulp_product_odt", 10_137_000.0, 1_000.0),
        ("process_residue_pj", 419.238, 0.001),
        ("harvest_residue_gross_pj", 373.244, 0.001),
        ("harvest_residue_recoverable_r060_pj", 223.946, 0.001),
        ("total_residue_gross_pj", 792.482, 0.001),
    ),
    "119m": (
        ("solid_product_m3", 37_674_000.0, 1_000.0),
        ("pulp_product_odt", 12_066_000.0, 1_000.0),
        ("process_residue_pj", 498.751, 0.001),
        ("harvest_residue_gross_pj", 444.411, 0.001),
        ("harvest_residue_recoverable_r060_pj", 266.646, 0.001),
        ("total_residue_gross_pj", 943.162, 0.001),
    ),
    "150m": (
        ("solid_product_m3", 46_137_000.0, 1_000.0),
        ("pulp_product_odt", 15_551_000.0, 1_000.0),
        ("process_residue_pj", 626.865, 0.001),
        ("harvest_residue_gross_pj", 573.125, 0.001),
        ("harvest_residue_recoverable_r060_pj", 343.875, 0.001),
        ("total_residue_gross_pj", 1_199.991, 0.001),
    ),
}


class Ws4OperableCasesError(RuntimeError):
    """Raised when an operable case fails a guardrail."""


@dataclass(frozen=True)
class OperableCaseSpec:
    """One sustainable case rerun on the operable landbase."""

    key: str
    target_annual_m3: float
    primary_basis: str
    control_csv: Path
    output_dir: Path
    chain_dir: Path
    artifact: str
    artifact_title: str
    report_name: str


def _case(key: str, target_annual_m3: float) -> OperableCaseSpec:
    return OperableCaseSpec(
        key=key,
        target_annual_m3=target_annual_m3,
        primary_basis=f"operable_{key}",
        control_csv=(
            DATA_ROOT
            / "derived/national/harvest_basis/provincial_allocation"
            / f"{key}.csv"
        ),
        output_dir=DATA_ROOT / "derived/ws4" / f"operable-{key}",
        chain_dir=CASES_ROOT / key,
        artifact=f"ws4_operable_{key}",
        artifact_title=f"Sustainable operable {key} WS4 schedule and chain",
        report_name=f"WS4_OPERABLE_{key.upper()}.md",
    )


CASES: tuple[OperableCaseSpec, ...] = (
    _case("100m", 100_000_000.1),
    _case("119m", 118_977_339.1),
    _case("150m", 150_000_000.2),
)
CASE_BY_KEY = {spec.key: spec for spec in CASES}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _relative(path: Path) -> str:
    return str(path.relative_to(REPO_ROOT))


def _write_csv(
    path: Path, fields: tuple[str, ...] | list[str], rows: list[dict[str, Any]]
) -> None:
    base._write_csv(path, fields, rows)


def _write_json(path: Path, payload: Any) -> None:
    base._write_json(path, payload)


def _finite(value: Any, label: str, *, minimum: float | None = 0.0) -> float:
    return base._finite(value, label, minimum=minimum)


def assert_no_unexpected_deletions() -> dict[str, Any]:
    return base.assert_no_unexpected_deletions()


def _optional_finite(value: Any, label: str) -> float | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    if pd.isna(value):
        return None
    return _finite(value, label, minimum=0.0)


def _json_records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    """Convert a frame to JSON-safe records with blanks as ``None``."""
    records: list[dict[str, Any]] = []
    for row in frame.to_dict(orient="records"):
        clean: dict[str, Any] = {}
        for key, value in row.items():
            if isinstance(value, (np.integer,)):
                clean[key] = int(value)
            elif isinstance(value, (np.floating,)):
                number = float(value)
                clean[key] = number if math.isfinite(number) else None
            elif isinstance(value, (np.bool_,)):
                clean[key] = bool(value)
            elif isinstance(value, float):
                clean[key] = value if math.isfinite(value) else None
            else:
                clean[key] = value
        records.append(clean)
    return records


# ---------------------------------------------------------------------------
# Operable screen and mask
# ---------------------------------------------------------------------------


def load_operable_screen() -> pd.DataFrame:
    """Return the validated ``screen_core30`` province rows."""
    if not SCREEN_CSV.is_file():
        raise Ws4OperableCasesError(f"missing operable screen: {SCREEN_CSV}")
    raw = pd.read_csv(SCREEN_CSV)
    required = {
        "province",
        "ecoprovince",
        "screen_case",
        "scanfi_forest_ha",
        "operable_proxy_ha",
        "operable_share_of_treed",
        "coverage_available",
    }
    missing = required - set(raw.columns)
    if missing:
        raise Ws4OperableCasesError(
            f"operable screen is missing columns: {sorted(missing)}"
        )
    rows = raw[
        (raw["screen_case"].astype(str) == SCREEN_CASE)
        & (raw["ecoprovince"].astype(str) == "ALL")
    ].copy()
    if len(rows) != len(PROVINCES):
        raise Ws4OperableCasesError(
            f"{SCREEN_CASE} has {len(rows)} province rows, "
            f"expected {len(PROVINCES)}"
        )
    rows["province"] = rows["province"].astype(str).str.strip().str.upper()
    if set(rows["province"]) != set(PROVINCES):
        raise Ws4OperableCasesError(
            f"{SCREEN_CASE} does not cover the 13 WS4 jurisdictions"
        )
    rows["coverage_available"] = (
        rows["coverage_available"].astype(str).str.lower() == "true"
    )
    for row in rows.itertuples(index=False):
        province = row.province
        forest = _finite(row.scanfi_forest_ha, f"{province} SCANFI forest area")
        if forest <= 0.0:
            raise Ws4OperableCasesError(f"{province} SCANFI forest area is not positive")
        if not row.coverage_available:
            if not pd.isna(row.operable_proxy_ha) or not pd.isna(
                row.operable_share_of_treed
            ):
                raise Ws4OperableCasesError(
                    f"{province} has no coverage but a non-blank operable value"
                )
            continue
        operable = _finite(row.operable_proxy_ha, f"{province} operable area")
        share = _finite(row.operable_share_of_treed, f"{province} operable share")
        if share > 1.0 + 1e-12:
            raise Ws4OperableCasesError(f"{province} operable share exceeds one")
        if operable > forest + 1e-6:
            raise Ws4OperableCasesError(
                f"{province} operable area exceeds SCANFI forest area"
            )
        expected = share * forest
        if not math.isclose(operable, expected, rel_tol=0.0, abs_tol=1e-3):
            raise Ws4OperableCasesError(
                f"{province} operable area and share are inconsistent"
            )
    return rows.sort_values("province").reset_index(drop=True)


def load_road_classes() -> pd.DataFrame:
    if not ROAD_CLASSES_CSV.is_file():
        raise Ws4OperableCasesError(f"missing road distance classes: {ROAD_CLASSES_CSV}")
    rows = pd.read_csv(ROAD_CLASSES_CSV)
    rows["province"] = rows["province"].astype(str).str.strip().str.upper()
    if set(rows["province"]) != set(PROVINCES):
        raise Ws4OperableCasesError(
            "road distance classes do not cover the 13 WS4 jurisdictions"
        )
    class_columns = [
        "road_0_5km_ha",
        "road_5_10km_ha",
        "road_10_20km_ha",
        "road_gt_20km_ha",
    ]
    sums = rows[class_columns].sum(axis=1)
    gap = (sums - rows["base_merchantable_ha"]).abs()
    if float(gap.max()) > 1e-3:
        raise Ws4OperableCasesError(
            "road distance classes do not conserve base merchantable area"
        )
    return rows.sort_values("province").reset_index(drop=True)


def load_gaps() -> list[dict[str, str]]:
    if not GAPS_CSV.is_file():
        raise Ws4OperableCasesError(f"missing operable gaps table: {GAPS_CSV}")
    rows = chain._read_rows(GAPS_CSV)
    return [
        {
            "gap_id": row.get("gap_id", ""),
            "scope": row.get("scope", ""),
            "status": row.get("status", ""),
            "detail": row.get("detail", ""),
            "effect": row.get("effect", ""),
        }
        for row in rows
    ]


def stable_block_hash(province: np.ndarray, block_id: np.ndarray) -> np.ndarray:
    """Return a stable 64-bit hash key for each ``province:block_id`` pair."""
    if province.shape != block_id.shape:
        raise Ws4OperableCasesError("province and block id arrays differ in shape")
    digests = [
        hashlib.blake2b(
            f"{p!s}:{int(b)}".encode("ascii"), digest_size=8
        ).digest()
        for p, b in zip(province, block_id, strict=True)
    ]
    return np.frombuffer(b"".join(digests), dtype=">u8")


def select_blocks_for_share(
    block_ids: np.ndarray,
    areas: np.ndarray,
    hashes: np.ndarray,
    target_ha: float,
) -> np.ndarray:
    """Select whole blocks in stable-hash order until ``target_ha`` is met."""
    if target_ha < 0.0:
        raise Ws4OperableCasesError("operative target area is negative")
    if target_ha == 0.0:
        return np.empty(0, dtype=np.int64)
    if areas.size == 0:
        raise Ws4OperableCasesError("cannot select blocks from an empty province")
    if (areas < 0.0).any():
        raise Ws4OperableCasesError("negative block area in mask selection")
    order = np.lexsort((block_ids, hashes))
    cumulative = np.cumsum(areas[order])
    if cumulative[-1] + 1e-9 < target_ha:
        raise Ws4OperableCasesError(
            f"target area {target_ha!r} exceeds province forest area "
            f"{float(cumulative[-1])!r}"
        )
    take = int(np.searchsorted(cumulative, target_ha, side="left")) + 1
    candidates = {max(1, take - 1), take}
    best = min(
        candidates,
        key=lambda count: (abs(float(cumulative[count - 1]) - target_ha), count),
    )
    return order[:best]


def build_operable_mask(estate: base.Estate) -> tuple[np.ndarray, pd.DataFrame, dict[str, Any]]:
    """Build and persist the deterministic share-based operable block mask."""
    screen = load_operable_screen().set_index("province")
    road = load_road_classes()
    gaps = load_gaps()
    hashes = stable_block_hash(estate.province, estate.block_ids)
    mask = np.zeros(estate.block_ids.size, dtype=bool)
    summary_rows: list[dict[str, Any]] = []

    for code, province in enumerate(PROVINCES):
        province_positions = np.flatnonzero(estate.province_code == code)
        forest_ha = float(estate.area_ha[province_positions].sum())
        if forest_ha <= 0.0:
            raise Ws4OperableCasesError(f"{province} has no WS4 block area")
        row = screen.loc[province]
        coverage = bool(row["coverage_available"])
        published_area = (
            _finite(row["operable_proxy_ha"], f"{province} operable area")
            if coverage
            else None
        )
        published_share = (
            _finite(row["operable_share_of_treed"], f"{province} operable share")
            if coverage
            else None
        )
        target_ha = (
            float(published_share) * forest_ha if published_share is not None else 0.0
        )
        if coverage:
            selected_local = select_blocks_for_share(
                estate.block_ids[province_positions],
                estate.area_ha[province_positions],
                hashes[province_positions],
                target_ha,
            )
            selected_positions = province_positions[selected_local]
            mask[selected_positions] = True
        else:
            selected_positions = np.empty(0, dtype=np.int64)
        selected_area = float(estate.area_ha[selected_positions].sum())
        selected_blocks = int(selected_positions.size)
        selected_share = selected_area / forest_ha
        max_block_area = float(estate.area_ha[province_positions].max())
        target_delta = selected_area - target_ha
        target_tolerance = 0.5 * max_block_area + 1e-9
        share_tolerance = 0.5 * max_block_area / forest_ha + 1e-12
        if coverage:
            if abs(target_delta) > target_tolerance + 1e-9:
                raise Ws4OperableCasesError(
                    f"{province} selected {selected_area!r} ha differs from "
                    f"target {target_ha!r} ha by more than half a block"
                )
            if abs(selected_share - float(published_share)) > share_tolerance + 1e-12:
                raise Ws4OperableCasesError(
                    f"{province} selected share {selected_share!r} differs from "
                    f"published {published_share!r} by more than half a block"
                )
            status = "selected"
        else:
            status = "no_tenure_coverage"
        summary_rows.append(
            {
                "province": province,
                "screen_case": SCREEN_CASE,
                "coverage_available": coverage,
                "scanfi_forest_ha": _finite(
                    row["scanfi_forest_ha"], f"{province} SCANFI forest area"
                ),
                "published_operable_proxy_ha": published_area,
                "operable_share_of_treed": published_share,
                "ws4_forest_ha": forest_ha,
                "share_target_ha": target_ha,
                "selected_area_ha": selected_area,
                "selected_blocks": selected_blocks,
                "selected_share": selected_share,
                "share_delta": (
                    selected_share - float(published_share)
                    if published_share is not None
                    else None
                ),
                "target_delta_ha": target_delta,
                "target_delta_pct": (
                    target_delta / target_ha * 100.0 if target_ha > 0.0 else 0.0
                ),
                "share_tolerance_half_block": share_tolerance,
                "max_block_area_ha": max_block_area,
                "published_area_delta_ha": (
                    selected_area - float(published_area)
                    if published_area is not None
                    else None
                ),
                "published_area_delta_pct": (
                    (selected_area - float(published_area)) / float(published_area) * 100.0
                    if published_area not in (None, 0.0)
                    else None
                ),
                "mask_status": status,
                **ROW_LABELS,
            }
        )

    summary = pd.DataFrame(summary_rows)
    selected_total = float(estate.area_ha[mask].sum())
    published_total = float(summary["published_operable_proxy_ha"].sum())
    forest_total = float(summary["ws4_forest_ha"].sum())
    scanfi_total = float(summary["scanfi_forest_ha"].sum())
    target_total = float(summary["share_target_ha"].sum())
    covered = summary[summary["coverage_available"]]
    uncovered = summary[~summary["coverage_available"]]

    share_by_province = summary.set_index("province")["operable_share_of_treed"]
    mask_frame = pd.DataFrame(
        {
            "block_id": estate.block_ids,
            "province": estate.province,
            "area_ha": estate.area_ha,
            "operable_block": mask,
            "screen_case": SCREEN_CASE,
            "operable_share_of_treed": pd.Series(estate.province)
            .map(share_by_province)
            .to_numpy(dtype=float),
            "selection_hash": hashes.astype(np.uint64),
            **ROW_LABELS,
        }
    )
    mask_frame = mask_frame.sort_values("block_id", kind="mergesort").reset_index(
        drop=True
    )
    if mask_frame["block_id"].duplicated().any():
        raise Ws4OperableCasesError("operable mask contains duplicate block ids")
    if int(mask_frame["operable_block"].sum()) != len(
        np.flatnonzero(mask_frame["operable_block"])
    ):
        raise Ws4OperableCasesError("operable mask selection is inconsistent")
    if int(mask_frame["operable_block"].sum()) == 0:
        raise Ws4OperableCasesError("operable mask selected no blocks")

    MASK_DIR.mkdir(parents=True, exist_ok=True)
    mask_frame.to_parquet(MASK_DIR / "operable_mask.parquet", index=False)
    summary_records = _json_records(summary)
    _write_csv(
        MASK_DIR / "operable_mask_summary.csv",
        list(summary.columns),
        summary_records,
    )
    mask_summary: dict[str, Any] = {
        "artifact": "ws4_operable_mask",
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "screen_case": SCREEN_CASE,
        "screen_source": _relative(SCREEN_CSV),
        "road_distance_source": _relative(ROAD_CLASSES_CSV),
        "gaps_source": _relative(GAPS_CSV),
        "selection_rule": (
            "Within each province, sort blocks by a stable blake2b hash of "
            "province:block_id (tie-break block_id) and take whole blocks "
            "until the cumulative selected forest area reaches "
            "operable_share_of_treed * WS4 province forest area."
        ),
        "hash_method": "blake2b(province:block_id, digest_size=8), big-endian uint64",
        "target_rule": (
            "share-based: published operable_share_of_treed applied to the WS4 "
            "990 m block forest area; the published absolute operable area is "
            "reported separately and is not forced onto a different denominator"
        ),
        "labels": LABELS,
        "national": {
            "ws4_forest_ha": forest_total,
            "scanfi_forest_ha": scanfi_total,
            "published_operable_proxy_ha": published_total,
            "share_target_ha": target_total,
            "selected_area_ha": selected_total,
            "selected_blocks": int(mask.sum()),
            "selected_share_of_ws4_forest": selected_total / forest_total,
            "published_share_of_scanfi_treed": published_total / scanfi_total,
            "selected_minus_published_ha": selected_total - published_total,
            "selected_minus_published_pct": (
                (selected_total - published_total) / published_total * 100.0
            ),
        },
        "by_province": summary_records,
        "road_distance_classes": _json_records(road),
        "gaps": gaps,
        "limitations": [
            (
                "No legal THLB boundary is used. Tenure is a rights footprint, "
                "not a harvest-landbase determination."
            ),
            (
                "No national slope or elevation DEM is available, so no slope, "
                "steep-terrain, or site-degradation screen is applied."
            ),
            (
                "The road screen is a 990 m public-NRN Euclidean distance proxy, "
                "not a spatial road overlay; resource, industrial, winter, and "
                "decommissioned roads are absent."
            ),
            (
                "NBAC 2020 is the only annual fire vintage available; 2010 and "
                "2015 disturbance cases are gaps, not zero-disturbance cases."
            ),
            (
                "SCANFI age is a prediction surface; the age >= 30 screen is a "
                "screening convention, not a legal rule."
            ),
            (
                "The mask is an area-share proxy. Selected blocks are not "
                "spatially intersected with the tenure, burn, road, or slope "
                "rasters, and do not claim legal THLB or slope screening."
            ),
            (
                "The v1 managed share is uniform inside each province-ecoprovince; "
                "spatial tenure pattern is not represented."
            ),
            (
                "NU and PE have no accepted 2016 tenure coverage; their operable "
                "area is intentionally blank, not zero, and no block is selected "
                "there."
            ),
        ],
        "validation": {
            "province_count": len(summary),
            "covered_provinces": int(covered.shape[0]),
            "uncovered_provinces": uncovered["province"].tolist(),
            "selected_share_within_half_block_all_covered": bool(
                (
                    covered["share_delta"].abs()
                    <= covered["share_tolerance_half_block"] + 1e-12
                ).all()
            ),
            "selected_area_matches_share_target_within_half_block": bool(
                (
                    covered["target_delta_ha"].abs()
                    <= 0.5 * covered["max_block_area_ha"] + 1e-9
                ).all()
            ),
            "no_duplicate_block_ids": not bool(
                mask_frame["block_id"].duplicated().any()
            ),
            "no_nan_in_selected_areas": bool(
                np.isfinite(covered["selected_area_ha"].to_numpy(dtype=float)).all()
            ),
            "share_based_operable_proxy": True,
            "legal_thlb": False,
            "slope_screen": False,
        },
        "d_status": assert_no_unexpected_deletions(),
    }
    _write_json(MASK_DIR / "mask_summary.json", mask_summary)
    _write_mask_report(mask_summary)
    return mask, summary, mask_summary


def _write_mask_report(summary: Mapping[str, Any]) -> None:
    rows = summary["by_province"]
    table = "\n".join(
        [
            "| Province | Coverage | Published share | WS4 forest Mha | Share target Mha | Selected Mha | Selected share | Selected blocks |",
            "|---|---|---:|---:|---:|---:|---:|---:|",
            *[
                "| "
                + " | ".join(
                    [
                        str(row["province"]),
                        "yes" if row["coverage_available"] else "no",
                        (
                            f"{float(row['operable_share_of_treed']) * 100:.4f}%"
                            if row["operable_share_of_treed"] is not None
                            else "n/a"
                        ),
                        f"{float(row['ws4_forest_ha']) / 1e6:.3f}",
                        f"{float(row['share_target_ha']) / 1e6:.3f}",
                        f"{float(row['selected_area_ha']) / 1e6:.3f}",
                        f"{float(row['selected_share']) * 100:.4f}%",
                        f"{int(row['selected_blocks']):,}",
                    ]
                )
                + " |"
                for row in rows
            ],
        ]
    )
    report = f"""# Share-based operable block mask

**Artifact:** `ws4_operable_mask` **Version:** {ARTIFACT_VERSION}

**Status:** screening proxy. This is not a legal THLB, a slope screen, or a
spatial road overlay.

**Screen case:** `{SCREEN_CASE}` - SCANFI median age >= 30 years, NBAC 2020
burned pixels excluded, block centre within 10 km of a public NRN road.

**Labels:** `share_based_operable_proxy=true`, `legal_thlb=false`,
`slope_screen=false`, `model_estimate=true`, `observed=false`,
`policy_grade=false`, `national_claim=false`.

## 1. Method

Within each province, blocks are ranked by a stable `blake2b(province:block_id)`
hash and taken whole until the selected forest area reaches
`operable_share_of_treed * WS4 province forest area`. The published absolute
`operable_proxy_ha` is reported as a cross-check but is not forced onto the
WS4 block denominator. No block is selected twice. The mask is deterministic.

## 2. Province result

{table}

National selected area: {summary["national"]["selected_area_ha"]/1e6:,.3f} Mha
across {summary["national"]["selected_blocks"]:,} blocks
({summary["national"]["selected_share_of_ws4_forest"]*100:.4f}% of WS4 forest
area). Published screen area: {summary["national"]["published_operable_proxy_ha"]/1e6:,.3f}
Mha ({summary["national"]["published_share_of_scanfi_treed"]*100:.4f}% of SCANFI
treed area). The selected area is
{summary["national"]["selected_minus_published_ha"]/1e6:+,.3f} Mha
({summary["national"]["selected_minus_published_pct"]:+.3f}%) versus the
published absolute area, because the WS4 block forest denominator is smaller
than the SCANFI treed denominator.

## 3. Gaps and limitations

- No legal THLB boundary.
- No national slope/elevation DEM; no slope screen.
- Road distance is public-NRN Euclidean distance at 990 m, not a spatial road
  overlay; resource/industrial roads are absent.
- NBAC 2020 only; 2010/2015 disturbance cases are gaps.
- SCANFI age is a prediction surface.
- Managed share is uniform inside each province-ecoprovince.
- NU and PE have no 2016 tenure coverage and intentionally have no operable
  blocks.
"""
    (MASK_DIR / "OPERABLE_MASK.md").write_text(report, encoding="utf-8")


# ---------------------------------------------------------------------------
# Controls, schedules, WS4 runs
# ---------------------------------------------------------------------------


def load_case_control(spec: OperableCaseSpec) -> tuple[dict[str, float], float]:
    """Return normalized frozen provincial shares and the exact control total."""
    rows = chain._read_rows(spec.control_csv)
    shares: dict[str, float] = {}
    harvest: dict[str, float] = {}
    for row in rows:
        province = row["province"].strip().upper()
        if province not in PROVINCES:
            raise Ws4OperableCasesError(
                f"unexpected {spec.key} control province {province!r}"
            )
        if province in shares:
            raise Ws4OperableCasesError(
                f"duplicate {spec.key} control province {province!r}"
            )
        shares[province] = _finite(
            row["allocated_share"], f"{spec.key} {province} allocated share"
        )
        harvest[province] = _finite(
            row["harvest_m3"], f"{spec.key} {province} control harvest"
        )
    if set(shares) != set(PROVINCES):
        raise Ws4OperableCasesError(
            f"{spec.key} control CSV does not cover all 13 jurisdictions"
        )
    share_total = math.fsum(shares.values())
    if not math.isclose(share_total, 1.0, rel_tol=1e-9, abs_tol=1e-6):
        raise Ws4OperableCasesError(
            f"{spec.key} allocated shares sum to {share_total!r}, expected 1.0"
        )
    harvest_total = math.fsum(harvest.values())
    if not math.isclose(
        harvest_total, spec.target_annual_m3, rel_tol=0.0, abs_tol=0.5
    ):
        raise Ws4OperableCasesError(
            f"{spec.key} control harvest total {harvest_total!r} does not "
            f"match {spec.target_annual_m3!r}"
        )
    normalized = {province: value / share_total for province, value in shares.items()}
    return normalized, harvest_total


def _covered_provinces(mask_summary: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(
        row["province"]
        for row in mask_summary["by_province"]
        if bool(row["coverage_available"])
    )


def build_case_schedule(
    spec: OperableCaseSpec,
    estate: base.Estate,
    mask: np.ndarray,
    mask_summary: Mapping[str, Any],
) -> tuple[base.SelectionResult, dict[str, float], float]:
    """Select whole operable blocks for one case with the shared scheduler."""
    shares, target_annual = load_case_control(spec)
    result = base.build_schedule(
        estate,
        province_shares=shares,
        label=f"operable_{spec.key}",
        target_period_m3=target_annual * PERIOD_LENGTH,
        control_label=f"frozen {spec.key} allocated_share control",
        eligibility_mask=mask,
        national_fallback=False,
    )
    positions = np.searchsorted(estate.block_ids, result.selected["block_id"].to_numpy())
    if positions.size and not bool(mask[positions].all()):
        raise Ws4OperableCasesError(
            f"{spec.key} schedule harvested a block outside the operable mask"
        )
    annual_min = float(result.diagnostics["annual_min_m3"])
    result.diagnostics["annual_floor_met"] = bool(annual_min + 1e-6 >= target_annual)
    result.diagnostics["annual_floor_gap_m3"] = max(0.0, target_annual - annual_min)
    result.diagnostics["operable_mask_blocks"] = int(mask.sum())
    result.diagnostics["operable_mask_area_ha"] = float(
        estate.area_ha[mask].sum()
    )
    covered = set(_covered_provinces(mask_summary))
    province_period = result.province_period_rows
    covered_rows = province_period[province_period["province"].isin(covered)]
    result.diagnostics["sustained_covered_provinces"] = bool(
        (covered_rows["achieved_m3"] + 1e-6 >= covered_rows["target_m3"]).all()
    )
    province_shortfalls = result.shortfalls[result.shortfalls["province"] != "CAN"]
    result.diagnostics["province_shortfall_m3"] = float(
        province_shortfalls["shortfall_m3"].sum()
    )
    shortfall_by_province = (
        province_shortfalls.groupby("province", as_index=False)["shortfall_m3"]
        .sum()
        .sort_values("shortfall_m3", ascending=False)
    )
    result.diagnostics["shortfall_by_province"] = [
        {
            "province": row.province,
            "shortfall_m3": float(row.shortfall_m3),
        }
        for row in shortfall_by_province.itertuples(index=False)
        if float(row.shortfall_m3) > 1e-6
    ]
    result.diagnostics["shortfall_provinces"] = [
        row["province"] for row in result.diagnostics["shortfall_by_province"]
    ]
    result.diagnostics["shortfall_periods"] = sorted(
        int(value)
        for value in result.shortfalls["period"].unique()
        if float(
            result.shortfalls.loc[
                result.shortfalls["period"] == value, "shortfall_m3"
            ].sum()
        )
        > 1e-6
    )
    return result, shares, target_annual


def _write_schedule_outputs(
    spec: OperableCaseSpec,
    result: base.SelectionResult,
    harvest_province: pd.DataFrame,
    annual: pd.DataFrame,
    mask_summary: Mapping[str, Any],
) -> None:
    spec.output_dir.mkdir(parents=True, exist_ok=True)
    selected = result.selected.copy()
    selected["operable_block"] = "true"
    selected["screen_case"] = SCREEN_CASE
    _write_csv(
        spec.output_dir / "selected_blocks.csv",
        [
            "block_id",
            "province",
            "period",
            "offset",
            "age_at_harvest",
            "volume_m3",
            "area_ha",
            "rotation_years",
            "rotation_raw_years",
            "curve_id",
            "age_class",
            "operable_block",
            "screen_case",
        ],
        selected.to_dict(orient="records"),
    )
    _write_csv(
        spec.output_dir / "annual_harvest.csv",
        [
            "offset",
            "year",
            "period",
            "harvest_m3",
            "area_ha",
            "block_count",
            "mean_age",
        ],
        annual.merge(
            result.annual_rows[["offset", "block_count", "mean_age"]],
            on="offset",
            how="left",
            validate="one_to_one",
        ).to_dict(orient="records"),
    )
    _write_csv(
        spec.output_dir / "period_harvest.csv",
        [
            "period",
            "start_offset",
            "end_offset",
            "target_m3",
            "achieved_m3",
            "delta_m3",
            "overshoot_pct",
            "area_ha",
            "block_count",
            "annual_min_m3",
            "annual_max_m3",
            "annual_cv",
        ],
        result.period_rows.to_dict(orient="records"),
    )
    _write_csv(
        spec.output_dir / "province_period_harvest.csv",
        [
            "period",
            "province",
            "target_m3",
            "achieved_m3",
            "delta_m3",
            "area_ha",
            "block_count",
            "mean_age",
        ],
        result.province_period_rows.to_dict(orient="records"),
    )
    harvest_province = harvest_province.copy()
    for key, value in ROW_LABELS.items():
        harvest_province[key] = value
    _write_csv(
        spec.output_dir / "province_harvest.csv",
        ["province", "harvest_m3", "area_ha", "block_count", *ROW_LABELS.keys()],
        harvest_province.to_dict(orient="records"),
    )
    _write_csv(
        spec.output_dir / "rotation_distribution.csv",
        [
            "rotation_raw_years",
            "rotation_years",
            "blocks",
            "area_ha",
            "volume_m3",
        ],
        result.rotation_distribution.to_dict(orient="records"),
    )
    _write_csv(
        spec.output_dir / "shortfalls.csv",
        [
            "period",
            "province",
            "scope",
            "target_m3",
            "eligible_volume_m3",
            "shortfall_m3",
        ],
        result.shortfalls.to_dict(orient="records"),
    )
    _write_csv(
        spec.output_dir / "age_class_harvest.csv",
        ["period", "province", "age_class", "blocks", "area_ha", "volume_m3"],
        result.selected.groupby(["period", "province", "age_class"], as_index=False)
        .agg(
            blocks=("block_id", "size"),
            area_ha=("area_ha", "sum"),
            volume_m3=("volume_m3", "sum"),
        )
        .to_dict(orient="records"),
    )


# ---------------------------------------------------------------------------
# Frozen chain v5.1 comparison
# ---------------------------------------------------------------------------


def load_reference_totals(key: str, target_annual_m3: float) -> dict[str, float]:
    """Read one frozen full-forest chain target and derive comparison metrics."""
    rows = chain._read_rows(SCENARIO_SUMMARY)
    matches = [row for row in rows if row["scenario"].strip() == key]
    if len(matches) != 1:
        raise Ws4OperableCasesError(
            f"scenario summary has {len(matches)} rows for {key}, expected 1"
        )
    row = matches[0]
    harvest = _finite(row["harvest_m3"], f"{key} reference harvest")
    if not math.isclose(harvest, target_annual_m3, rel_tol=0.0, abs_tol=0.5):
        raise Ws4OperableCasesError(
            f"{key} reference harvest {harvest!r} does not match "
            f"{target_annual_m3!r}"
        )
    totals: dict[str, float] = {"harvest_m3": harvest}
    totals["sawlog_product_m3"] = _finite(
        row["sawlog_product_m3"], f"{key} reference sawlog"
    )
    totals["panel_product_m3"] = _finite(
        row["panel_product_m3"], f"{key} reference panel"
    )
    totals["solid_product_m3"] = (
        totals["sawlog_product_m3"] + totals["panel_product_m3"]
    )
    for metric in (
        "pulp_product_odt",
        "pellet_product_odt",
        "bio_product_mwh",
        "process_residue_m3",
        "process_residue_odt",
        "process_residue_pj",
        "harvest_residue_gross_odt",
        "harvest_residue_gross_pj",
        "total_residue_gross_pj",
    ):
        totals[metric] = _finite(row[metric], f"{key} reference {metric}")
    gross_odt = totals["harvest_residue_gross_odt"]
    gross_pj = totals["harvest_residue_gross_pj"]
    for recovery, fraction in (("r035", 0.35), ("r060", 0.60), ("r075", 0.75)):
        totals[f"harvest_residue_recoverable_{recovery}_odt"] = gross_odt * fraction
        totals[f"harvest_residue_recoverable_{recovery}_pj"] = gross_pj * fraction
        totals[f"total_residue_recoverable_{recovery}_pj"] = (
            totals["process_residue_pj"] + gross_pj * fraction
        )
    published_r060_odt = _finite(
        row["harvest_residue_recoverable_odt"],
        f"{key} reference recoverable odt",
    )
    published_r060_pj = _finite(
        row["harvest_residue_recoverable_pj"],
        f"{key} reference recoverable pj",
    )
    if not math.isclose(
        totals["harvest_residue_recoverable_r060_odt"],
        published_r060_odt,
        rel_tol=0.0,
        abs_tol=1e-3,
    ) or not math.isclose(
        totals["harvest_residue_recoverable_r060_pj"],
        published_r060_pj,
        rel_tol=0.0,
        abs_tol=1e-6,
    ):
        raise Ws4OperableCasesError(
            f"{key} r060 recoverable residue is not gross x 0.60"
        )
    published_total_r060 = _finite(
        row["total_residue_recoverable_pj"],
        f"{key} reference total recoverable pj",
    )
    if not math.isclose(
        totals["total_residue_recoverable_r060_pj"],
        published_total_r060,
        rel_tol=0.0,
        abs_tol=1e-6,
    ):
        raise Ws4OperableCasesError(
            f"{key} total recoverable residue is inconsistent"
        )
    for metric, expected, tolerance in REFERENCE_EXPECTED[key]:
        observed = totals[metric]
        if abs(observed - expected) > tolerance:
            raise Ws4OperableCasesError(
                f"{key} frozen reference {metric} is {observed!r}, "
                f"expected {expected!r} +/- {tolerance!r}"
            )
    return totals


def _harvest_rows(
    spec: OperableCaseSpec,
    harvest_province: pd.DataFrame,
    first_period_annual: pd.DataFrame,
    shares: dict[str, float],
    target_annual: float,
    mask_summary: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    harvest = harvest_province.set_index("province")["harvest_m3"]
    if set(harvest.index) != set(PROVINCES):
        raise Ws4OperableCasesError(f"{spec.key} WS4 run is missing provinces")
    first = first_period_annual.set_index("province")["annual_m3"]
    annual_100y = harvest / HORIZON
    mask_rows = {row["province"]: row for row in mask_summary["by_province"]}
    rows: list[dict[str, Any]] = []
    for province in PROVINCES:
        mask_row = mask_rows[province]
        target = target_annual * shares[province]
        rows.append(
            {
                "province": province,
                "control_share": shares[province],
                "target_annual_m3": target,
                "achieved_100y_m3": float(harvest[province]),
                "annual_100y_m3": float(annual_100y[province]),
                f"annual_{spec.key}_m3": float(first.get(province, 0.0)),
                "first_period_m3": float(first.get(province, 0.0) * PERIOD_LENGTH),
                "first_period_annual_m3": float(first.get(province, 0.0)),
                "scale_factor": 1.0,
                "area_100y_ha": float(
                    harvest_province.set_index("province").loc[province, "area_ha"]
                ),
                "block_count_100y": int(
                    harvest_province.set_index("province").loc[province, "block_count"]
                ),
                "operable_mask_area_ha": float(mask_row["selected_area_ha"]),
                "operable_mask_blocks": int(mask_row["selected_blocks"]),
                "operable_share_of_treed": (
                    float(mask_row["operable_share_of_treed"])
                    if mask_row["operable_share_of_treed"] is not None
                    else 0.0
                ),
                "coverage_available": bool(mask_row["coverage_available"]),
                "annual_shortfall_m3": max(
                    0.0, target - float(annual_100y[province])
                ),
                "target_met_100y": bool(
                    float(annual_100y[province]) + 1e-6 >= target
                ),
                **ROW_LABELS,
            }
        )
    total_100y = float(harvest.sum())
    first_total = float(first.sum())
    total_row = {
        "province": "CAN",
        "control_share": 1.0,
        "target_annual_m3": target_annual,
        "achieved_100y_m3": total_100y,
        "annual_100y_m3": total_100y / HORIZON,
        f"annual_{spec.key}_m3": first_total,
        "first_period_m3": first_total * PERIOD_LENGTH,
        "first_period_annual_m3": first_total,
        "scale_factor": 1.0,
        "area_100y_ha": float(harvest_province["area_ha"].sum()),
        "block_count_100y": int(harvest_province["block_count"].sum()),
        "operable_mask_area_ha": float(mask_summary["national"]["selected_area_ha"]),
        "operable_mask_blocks": int(mask_summary["national"]["selected_blocks"]),
        "operable_share_of_treed": mask_summary["national"][
            "published_share_of_scanfi_treed"
        ],
        "coverage_available": True,
        "annual_shortfall_m3": max(0.0, target_annual - total_100y / HORIZON),
        "target_met_100y": bool(total_100y / HORIZON + 1e-6 >= target_annual),
        **ROW_LABELS,
    }
    return rows, total_row


def _md_table(header: list[str], rows: list[list[str]]) -> str:
    lines = [
        "| " + " | ".join(header) + " |",
        "|" + "|".join("---" for _ in header) + "|",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return "\n".join(lines)


def _full_forest_summary(key: str) -> dict[str, Any]:
    path = FULL_FOREST_CASE_SUMMARIES[key]
    if not path.is_file():
        raise Ws4OperableCasesError(f"missing full-forest case summary: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def build_case_chain(
    spec: OperableCaseSpec,
    *,
    harvest_province: pd.DataFrame,
    first_period_annual: pd.DataFrame,
    rotation_table: pd.DataFrame,
    schedule_result: base.SelectionResult,
    run_values: pd.DataFrame,
    validate_text: str,
    shares: dict[str, float],
    target_annual: float,
    mask_summary: Mapping[str, Any],
    max_level: Mapping[str, Any],
) -> dict[str, Any]:
    """Write one operable case chain and return its summary."""
    spec.chain_dir.mkdir(parents=True, exist_ok=True)
    frozen = chain._frozen_province_rows(chain.CHAIN_V51_CSV)
    _coefficient_rows, coefficients = chain._coefficient_rows(frozen)
    reference_totals = load_reference_totals(spec.key, target_annual)

    harvest_rows, total_row = _harvest_rows(
        spec,
        harvest_province,
        first_period_annual,
        shares,
        target_annual,
        mask_summary,
    )
    harvest_fields = list(harvest_rows[0].keys())
    _write_csv(
        spec.chain_dir / "sustainable_harvest_by_province.csv",
        harvest_fields,
        harvest_rows + [total_row],
    )

    annual_achieved = harvest_province.set_index("province")["harvest_m3"] / HORIZON
    first_annual = first_period_annual.set_index("province")["annual_m3"].reindex(
        PROVINCES, fill_value=0.0
    )
    bases = (spec.primary_basis, SECONDARY_BASIS)
    province_chain_rows: list[dict[str, Any]] = []
    national_rows: list[dict[str, Any]] = []
    for basis, values in (
        (spec.primary_basis, first_annual),
        (SECONDARY_BASIS, annual_achieved),
    ):
        rows, national = chain.build_chain_rows(
            {province: float(values[province]) for province in PROVINCES},
            basis,
            frozen,
            coefficients,
            scale_factor=1.0,
        )
        for chain_row in rows + [national]:
            chain_row.update(ROW_LABELS)
        province_chain_rows.extend(rows)
        national_rows.append(national)
    product_fields = [
        "province",
        "geography_level",
        "basis",
        "harvest_m3",
        "scale_factor",
        "coefficient_tier",
        "coefficient_source",
        *[metric for metric, _unit in chain.METRICS],
        *ROW_LABELS.keys(),
    ]
    _write_csv(
        spec.chain_dir / "sustainable_product_chain.csv",
        product_fields,
        province_chain_rows + national_rows,
    )

    national = {
        spec.primary_basis: next(
            row for row in national_rows if row["basis"] == spec.primary_basis
        ),
        SECONDARY_BASIS: next(
            row for row in national_rows if row["basis"] == SECONDARY_BASIS
        ),
    }
    comparison_rows: list[dict[str, Any]] = []
    for metric, unit in chain.METRICS:
        reference = float(reference_totals[metric])
        row: dict[str, Any] = {
            "metric": metric,
            "unit": unit,
            "chain_v51_value": reference,
        }
        for basis in bases:
            value = float(national[basis][metric])
            delta = value - reference
            row[f"{basis}_value"] = value
            row[f"{basis}_delta"] = delta
            row[f"{basis}_delta_pct"] = delta / reference * 100.0 if reference else 0.0
        row.update(ROW_LABELS)
        comparison_rows.append(row)
    comparison_fields = [
        "metric",
        "unit",
        "chain_v51_value",
        *[
            f"{basis}_{suffix}"
            for basis in bases
            for suffix in ("value", "delta", "delta_pct")
        ],
        *ROW_LABELS.keys(),
    ]
    _write_csv(
        spec.chain_dir / "comparison_vs_v51.csv",
        comparison_fields,
        comparison_rows,
    )

    selected_rotation = schedule_result.rotation_distribution
    raw_distribution = (
        selected_rotation.groupby("rotation_raw_years", as_index=False)["blocks"]
        .sum()
        .set_index("rotation_raw_years")["blocks"]
        .sort_index()
        .to_dict()
    )
    used_distribution = (
        selected_rotation.groupby("rotation_years", as_index=False)["blocks"]
        .sum()
        .set_index("rotation_years")["blocks"]
        .sort_index()
        .to_dict()
    )
    full = _full_forest_summary(spec.key)
    full_schedule = full["schedule"]
    full_sustained = bool(
        full_schedule.get("sustained", full_schedule.get("sustained_119m"))
    )
    summary: dict[str, Any] = {
        "artifact": spec.artifact,
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "status": "pass" if schedule_result.diagnostics["sustained"] else "shortfall",
        "title": spec.artifact_title,
        "labels": LABELS,
        "case": spec.key,
        "target": {
            "annual_m3": target_annual,
            "nominal_annual_m3": spec.target_annual_m3,
            "period_m3": target_annual * PERIOD_LENGTH,
            "period_length_years": PERIOD_LENGTH,
            "periods": PERIODS,
            "horizon_years": HORIZON,
            "province_allocation": f"frozen {spec.key} allocated_share",
        },
        "operable_screen": {
            "screen_case": SCREEN_CASE,
            "mask_dir": _relative(MASK_DIR),
            "mask_summary": _relative(MASK_DIR / "mask_summary.json"),
            "selected_area_ha": mask_summary["national"]["selected_area_ha"],
            "selected_blocks": mask_summary["national"]["selected_blocks"],
            "published_operable_proxy_ha": mask_summary["national"][
                "published_operable_proxy_ha"
            ],
            "share_based_operable_proxy": True,
            "legal_thlb": False,
            "slope_screen": False,
        },
        "schedule": schedule_result.diagnostics,
        "rotation": {
            "raw_distribution": {
                int(key): int(value) for key, value in raw_distribution.items()
            },
            "used_distribution": {
                int(key): int(value) for key, value in used_distribution.items()
            },
            "out_of_range_block_count": int(
                (
                    schedule_result.selected["rotation_raw_years"]
                    != schedule_result.selected["rotation_years"]
                ).sum()
            ),
            "curve_count": len(rotation_table),
        },
        "harvest": {
            "province": harvest_rows,
            "national": total_row,
            "total_100y_m3": float(total_row["achieved_100y_m3"]),
            "annual_mean_m3": float(total_row["annual_100y_m3"]),
            "first_period_annual_mean_m3": float(
                total_row["first_period_annual_m3"]
            ),
        },
        "chain": {
            "basis": {
                "bases": list(bases),
                "primary": spec.primary_basis,
                "secondary": SECONDARY_BASIS,
                "primary_definition": (
                    "first-period annual average (offsets 0-9) on the operable "
                    "landbase with frozen v5.1 per-province coefficients"
                ),
                "secondary_definition": (
                    "100-year achieved annual average on the operable landbase "
                    "with frozen v5.1 per-province coefficients"
                ),
            },
            "reference": {
                "source": _relative(SCENARIO_SUMMARY),
                "scenario": spec.key,
                "basis": "frozen full-forest v5.1 case totals",
                "values": reference_totals,
            },
            "national": national,
            "comparison": comparison_rows,
        },
        "full_forest_reference": {
            "source": _relative(FULL_FOREST_CASE_SUMMARIES[spec.key]),
            "target_annual_m3": full["target"]["annual_m3"],
            "sustained": full_sustained,
            "annual_min_m3": full_schedule["annual_min_m3"],
            "annual_max_m3": full_schedule["annual_max_m3"],
            "annual_mean_m3": full["harvest"]["annual_mean_m3"],
            "selected_area_ha": full["schedule"]["selected_area_ha"],
            "selected_blocks": full["schedule"]["selected_blocks"],
        },
        "max_sustainable": dict(max_level),
        "ws4": {
            "dataset": _relative(base.DATASET_DIR),
            "schedule": _relative(spec.output_dir / "schedule.csv"),
            "run_dir": _relative(spec.output_dir / "run_100y"),
            "validate": validate_text,
            "run_offsets": int(run_values["offset"].nunique()),
        },
        "d_status": assert_no_unexpected_deletions(),
        "caveats": [
            (
                "The operable landbase is a share-based area proxy built from "
                "province-level screen_core30 shares; it is not a spatial THLB, "
                "slope, tenure, or road overlay."
            ),
            (
                "The schedule is a national even-flow arithmetic model, not an "
                "AAC, policy result, tenure plan, or investment case."
            ),
            (
                f"Provincial period targets use the frozen {spec.key} "
                "allocated_share values. A province that cannot meet its target "
                "records a shortfall; the cross-province fallback is disabled."
            ),
            (
                "Rotation is the integer age of maximum MAI on the RSYC yield "
                "curve, capped to 40-200 years; the raw peak is flagged. "
                "Rotation is never relaxed to meet a target."
            ),
            (
                "Frozen chain v5.1 coefficients are applied to operable WS4 "
                "harvest by origin province. Facility assignment, mill capacity, "
                "transport, and interprovincial trade are not re-run."
            ),
            (
                "The max-sustainable level is a schedule-only diagnostic with "
                "the same provincial shares and mask; it is not a WS4-simulated "
                "or policy-tested level."
            ),
            (
                "NU and PE have no 2016 tenure coverage. No operable block is "
                "selected there; their shortfalls are reported, not substituted "
                "with harvested volume."
            ),
            (
                "No legal THLB, slope screen, spatial road screen, official "
                "OSeCAN change, chain v5.1 mutation, or UI change is claimed."
            ),
        ],
    }
    _write_json(spec.chain_dir / "summary.json", summary)
    _write_case_report(spec, summary, schedule_result)
    return summary


def _write_case_report(
    spec: OperableCaseSpec,
    summary: Mapping[str, Any],
    result: base.SelectionResult,
) -> None:
    periods = result.period_rows
    period_table = _md_table(
        [
            "Period",
            "Offsets",
            "Target Mm3",
            "Achieved Mm3",
            "Delta Mm3",
            "Area Mha",
            "Blocks",
        ],
        [
            [
                str(int(row.period)),
                f"{int(row.start_offset)}-{int(row.end_offset)}",
                f"{float(row.target_m3) / 1e6:.3f}",
                f"{float(row.achieved_m3) / 1e6:.3f}",
                f"{float(row.delta_m3) / 1e6:+.3f}",
                f"{float(row.area_ha) / 1e6:.3f}",
                f"{int(row.block_count):,}",
            ]
            for row in periods.itertuples(index=False)
        ],
    )
    province_table = _md_table(
        [
            "Province",
            "Control share",
            "Target Mm3/yr",
            "First period Mm3/yr",
            "100-year Mm3/yr",
            "Mask Mha",
            "Area Mha",
            "Blocks",
            "100-year target met",
        ],
        [
            [
                str(row["province"]),
                f"{float(row['control_share']) * 100:.4f}%",
                f"{float(row['target_annual_m3']) / 1e6:.6f}",
                f"{float(row[f'annual_{spec.key}_m3']) / 1e6:.6f}",
                f"{float(row['annual_100y_m3']) / 1e6:.6f}",
                f"{float(row['operable_mask_area_ha']) / 1e6:.3f}",
                f"{float(row['area_100y_ha']) / 1e6:.3f}",
                f"{int(row['block_count_100y']):,}",
                str(bool(row["target_met_100y"])),
            ]
            for row in summary["harvest"]["province"]
        ],
    )
    comparison_table = _md_table(
        [
            "Metric",
            "Unit",
            "v5.1 full-forest",
            "Operable first period",
            "Delta %",
            "Operable achieved",
            "Delta %",
        ],
        [
            [
                str(row["metric"]),
                str(row["unit"]),
                f"{float(row['chain_v51_value']):,.6f}",
                f"{float(row[f'{spec.primary_basis}_value']):,.6f}",
                f"{float(row[f'{spec.primary_basis}_delta_pct']):+.4f}",
                f"{float(row[f'{SECONDARY_BASIS}_value']):,.6f}",
                f"{float(row[f'{SECONDARY_BASIS}_delta_pct']):+.4f}",
            ]
            for row in summary["chain"]["comparison"]
        ],
    )
    schedule = summary["schedule"]
    max_level = summary["max_sustainable"]
    status_text = (
        "sustained on the operable mask"
        if schedule["sustained"]
        else "not sustained on the operable mask"
    )
    max_text = (
        f"The same provincial shares and mask reach a schedule-only maximum of "
        f"{float(max_level['max_nominal_control_m3']) / 1e6:.4f} Mm3/yr nominal "
        f"control ({float(max_level['max_operable_harvest_m3']) / 1e6:.4f} "
        f"Mm3/yr operable harvest) when jurisdictions without operable coverage "
        f"are excluded from the adequacy test."
        if max_level.get("nominal_sustained_on_covered") is False
        else "The nominal target is sustained on every covered province."
    )
    shortfall_rows = [
        f"{row['province']}: {float(row['shortfall_m3']) / HORIZON / 1e6:.4f} Mm3/yr"
        for row in schedule["shortfall_by_province"]
    ]
    report = f"""# Sustainable operable {spec.key.replace("m", " Mm3/yr")} WS4 Schedule and Chain

**Artifact:** `{spec.artifact}` **Version:** {ARTIFACT_VERSION}

**Status:** {summary["status"]}. The {summary["target"]["annual_m3"]:,.1f} m3/yr
case is {status_text} for 100 years. This is a share-based operable proxy model
estimate, not an AAC, policy result, tenure plan, or investment case.

**Labels:** `model_estimate=true`, `observed=false`, `policy_grade=false`,
`national_claim=false`, `ws4_derived=true`, `sustainable_schedule=true`,
`share_based_operable_proxy=true`, `legal_thlb=false`, `slope_screen=false`,
`official_osecan_changed=false`, `chain_v51_unchanged=true`.

## 1. Operable screen

- Screen: `{SCREEN_CASE}` - SCANFI age >= 30 years, NBAC 2020 burned excluded,
  within 10 km of a public NRN road.
- Mask: {summary["operable_screen"]["selected_blocks"]:,} blocks /
  {summary["operable_screen"]["selected_area_ha"]/1e6:,.3f} Mha, selected by a
  stable hash to match the published provincial operable share.
- Published screen area: {summary["operable_screen"]["published_operable_proxy_ha"]/1e6:,.3f} Mha.
- The mask is an area-share proxy. No legal THLB, slope DEM, or spatial road
  overlay is used.

## 2. Method

- Dataset: `{summary["ws4"]["dataset"]}`; schedule `{summary["ws4"]["schedule"]}`;
  run `{summary["ws4"]["run_dir"]}`.
- Horizon: {HORIZON} years, {PERIODS} periods of {PERIOD_LENGTH} years.
- Eligible: operable-mask block and age at harvest >= MAI rotation
  (40-200 years).
- Selection: whole blocks, oldest-first, volume-aware, within the frozen
  {spec.key} provincial allocated shares; no block split, scaled, or harvested
  below rotation.
- If a province cannot meet its period target, the shortfall is recorded and
  the schedule continues. The cross-province national fallback is disabled.
- Annual assignment: longest-processing-time balancing over the ten offsets
  of each period.

## 3. Period harvest

{period_table}

- Annual minimum: {schedule["annual_min_m3"]/1e6:,.4f} Mm3/yr.
- Annual maximum: {schedule["annual_max_m3"]/1e6:,.4f} Mm3/yr.
- Annual mean: {schedule["annual_mean_m3"]/1e6:,.4f} Mm3/yr.
- Annual CV: {schedule["annual_cv"]:.6f}.
- Period shortfall (national): {schedule["period_shortfall_m3"]/1e6:,.4f} Mm3.
- Provincial shortfall: {schedule["province_shortfall_m3"]/1e6:,.4f} Mm3.
- Sustained (all 13 targets): `{schedule["sustained"]}`.
- Sustained on covered provinces: `{schedule["sustained_covered_provinces"]}`.
- Selected blocks: {schedule["selected_blocks"]:,}
  ({schedule["selected_area_ha"]/1e6:,.3f} Mha treated over 100 years).
- Blocks reharvested within 100 years: {schedule["blocks_reharvested"]:,}.

Province shortfalls: {", ".join(shortfall_rows) if shortfall_rows else "none"}.

{max_text}

## 4. Harvest by province

{province_table}

## 5. Chain versus frozen v5.1 full-forest case

{comparison_table}

The comparison reference is the frozen full-forest v5.1 case total for
`{spec.key}` in `{_relative(SCENARIO_SUMMARY)}`. The primary operable basis is
the first-period annual average; the achieved basis is the 100-year average.
Both are unscaled and use frozen v5.1 per-province coefficients.

## 6. OSeCAN handoff

The combined `osecan/` artifact applies the existing WS4 origin-province
handoff logic to this operable chain. It verifies 21/21 frozen v5.1 reference
positions and an unchanged official OSeCAN tree, and reports the modelled
tier positions against handoff v10. Tier differences are findings, not
official changes.

## 7. Caveats

"""
    report += "\n".join(f"- {caveat}" for caveat in summary["caveats"]) + "\n"
    (spec.chain_dir / spec.report_name).write_text(report, encoding="utf-8")


# ---------------------------------------------------------------------------
# Maximum sustainable level (schedule-only diagnostic)
# ---------------------------------------------------------------------------


def _covered_schedule_ok(
    spec: OperableCaseSpec,
    estate: base.Estate,
    mask: np.ndarray,
    shares: dict[str, float],
    total_m3: float,
    covered: set[str],
) -> tuple[bool, base.SelectionResult]:
    result = base.build_schedule(
        estate,
        province_shares=shares,
        label=f"operable_{spec.key}_max_search",
        target_period_m3=total_m3 * PERIOD_LENGTH,
        control_label=f"frozen {spec.key} allocated_share control",
        eligibility_mask=mask,
        national_fallback=False,
    )
    rows = result.province_period_rows
    rows = rows[rows["province"].isin(covered)]
    ok = bool((rows["achieved_m3"] + 1e-6 >= rows["target_m3"]).all())
    return ok, result


def find_max_sustainable_level(
    spec: OperableCaseSpec,
    estate: base.Estate,
    mask: np.ndarray,
    mask_summary: Mapping[str, Any],
    shares: dict[str, float],
    nominal_total: float,
    nominal_result: base.SelectionResult,
) -> dict[str, Any]:
    """Binary-search the largest nominal control sustained on covered provinces."""
    covered = set(_covered_provinces(mask_summary))
    uncovered = [province for province in PROVINCES if province not in covered]
    uncovered_share = math.fsum(shares[province] for province in uncovered)
    nominal_ok, _ = _covered_schedule_ok(
        spec, estate, mask, shares, nominal_total, covered
    )
    shortfall_by_province = (
        nominal_result.shortfalls[
            (nominal_result.shortfalls["province"] != "CAN")
            & (nominal_result.shortfalls["shortfall_m3"] > 1e-6)
        ]
        .groupby("province", as_index=False)["shortfall_m3"]
        .sum()
        .sort_values("shortfall_m3", ascending=False)
    )
    if nominal_ok:
        return {
            "nominal_sustained_on_covered": True,
            "max_nominal_control_m3": float(nominal_total),
            "max_operable_harvest_m3": float(
                nominal_total * (1.0 - uncovered_share)
            ),
            "uncovered_provinces": uncovered,
            "uncovered_share": uncovered_share,
            "iterations": 0,
            "search_tolerance_m3": 0.0,
            "binding_provinces": [],
            "shortfall_by_province": [],
            "method": (
                "nominal target sustained on every province with operable "
                "coverage; no upward search was needed"
            ),
            "label": "schedule_only_diagnostic",
        }
    lo = 0.0
    hi = nominal_total
    for _ in range(MAX_SEARCH_ITERATIONS):
        mid = 0.5 * (lo + hi)
        ok, _ = _covered_schedule_ok(spec, estate, mask, shares, mid, covered)
        if ok:
            lo = mid
        else:
            hi = mid
    _, max_result = _covered_schedule_ok(spec, estate, mask, shares, lo, covered)
    max_operable_harvest = float(
        max_result.province_period_rows[
            max_result.province_period_rows["province"].isin(covered)
        ]["achieved_m3"].sum()
        / HORIZON
    )
    return {
        "nominal_sustained_on_covered": False,
        "max_nominal_control_m3": float(lo),
        "max_operable_harvest_m3": max_operable_harvest,
        "uncovered_provinces": uncovered,
        "uncovered_share": uncovered_share,
        "iterations": MAX_SEARCH_ITERATIONS,
        "search_tolerance_m3": float(hi - lo),
        "binding_provinces": shortfall_by_province["province"].tolist(),
        "shortfall_by_province": [
            {
                "province": row.province,
                "shortfall_m3": float(row.shortfall_m3),
            }
            for row in shortfall_by_province.itertuples(index=False)
        ],
        "method": (
            "binary search on the nominal control total with the case's frozen "
            "provincial shares and the operable mask; adequacy is tested on the "
            "provinces with operable coverage; schedule volumes are exact and "
            "no WS4 run is used in the search"
        ),
        "label": "schedule_only_diagnostic",
    }


# ---------------------------------------------------------------------------
# OSeCAN handoff
# ---------------------------------------------------------------------------


def _osecan_tree_state() -> dict[str, Any]:
    from workflows.national.facilities import forestry_osecan_handoff_ws4 as handoff

    return handoff._tree_state(handoff.OSECAN_DIR)


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


def _run_case(
    spec: OperableCaseSpec,
    estate: base.Estate,
    mask: np.ndarray,
    mask_summary: Mapping[str, Any],
    rotation_table: pd.DataFrame,
    validate_text: str,
) -> dict[str, Any]:
    spec.output_dir.mkdir(parents=True, exist_ok=True)
    spec.chain_dir.mkdir(parents=True, exist_ok=True)
    schedule_result, shares, target_annual = build_case_schedule(
        spec, estate, mask, mask_summary
    )
    (spec.output_dir / "validate.txt").write_text(
        validate_text + "\n", encoding="utf-8"
    )
    run_status = base.ensure_ws4_run(
        schedule_result.schedule,
        schedule_path=spec.output_dir / "schedule.csv",
        run_dir=spec.output_dir / "run_100y",
        horizon=HORIZON,
        dataset_dir=base.DATASET_DIR,
    )
    run_values = base.extract_run_products(
        spec.output_dir / "run_100y", horizon=HORIZON
    )
    harvest_province, annual = base.harvest_by_province(
        schedule_result.selected, run_values
    )
    first_period = (
        schedule_result.selected[schedule_result.selected["period"] == 0]
        .groupby("province", as_index=False)
        .agg(volume_m3=("volume_m3", "sum"), area_ha=("area_ha", "sum"))
    )
    first_period["annual_m3"] = first_period["volume_m3"] / PERIOD_LENGTH
    first_period["annual_ha"] = first_period["area_ha"] / PERIOD_LENGTH

    _write_schedule_outputs(spec, schedule_result, harvest_province, annual, mask_summary)
    max_level = find_max_sustainable_level(
        spec,
        estate,
        mask,
        mask_summary,
        shares,
        target_annual,
        schedule_result,
    )
    summary = build_case_chain(
        spec,
        harvest_province=harvest_province,
        first_period_annual=first_period,
        rotation_table=rotation_table,
        schedule_result=schedule_result,
        run_values=run_values,
        validate_text=validate_text,
        shares=shares,
        target_annual=target_annual,
        mask_summary=mask_summary,
        max_level=max_level,
    )
    summary["run_status"] = run_status
    summary["schedule_files"] = {
        name: _sha256(spec.output_dir / name)
        for name in (
            "schedule.csv",
            "selected_blocks.csv",
            "annual_harvest.csv",
            "period_harvest.csv",
            "province_period_harvest.csv",
            "province_harvest.csv",
            "rotation_distribution.csv",
            "shortfalls.csv",
            "age_class_harvest.csv",
            "validate.txt",
        )
    }
    summary["chain_files"] = {
        name: _sha256(spec.chain_dir / name)
        for name in (
            "sustainable_harvest_by_province.csv",
            "sustainable_product_chain.csv",
            "comparison_vs_v51.csv",
        )
    }
    _write_json(spec.chain_dir / "summary.json", summary)
    _write_case_manifest(spec, summary, mask_summary)
    return summary


def _case_inputs(spec: OperableCaseSpec, mask_summary: Mapping[str, Any]) -> dict[str, str]:
    return {
        _relative(base.DATASET_DIR / "manifest.json"): _sha256(
            base.DATASET_DIR / "manifest.json"
        ),
        _relative(spec.control_csv): _sha256(spec.control_csv),
        _relative(SCREEN_CSV): _sha256(SCREEN_CSV),
        _relative(ROAD_CLASSES_CSV): _sha256(ROAD_CLASSES_CSV),
        _relative(GAPS_CSV): _sha256(GAPS_CSV),
        _relative(MASK_DIR / "mask_summary.json"): _sha256(
            MASK_DIR / "mask_summary.json"
        ),
        _relative(SCENARIO_SUMMARY): _sha256(SCENARIO_SUMMARY),
        _relative(chain.CHAIN_V51_CSV): _sha256(chain.CHAIN_V51_CSV),
        _relative(chain.CHAIN_V51_COEFFICIENTS): _sha256(
            chain.CHAIN_V51_COEFFICIENTS
        ),
        _relative(spec.output_dir / "schedule.csv"): _sha256(
            spec.output_dir / "schedule.csv"
        ),
        _relative(spec.output_dir / "run_100y" / "product_values.csv"): _sha256(
            spec.output_dir / "run_100y" / "product_values.csv"
        ),
    }


def _write_case_manifest(
    spec: OperableCaseSpec,
    summary: dict[str, Any],
    mask_summary: Mapping[str, Any],
) -> None:
    outputs = {
        path.name: {"bytes": path.stat().st_size, "sha256": _sha256(path)}
        for path in sorted(spec.chain_dir.iterdir())
        if path.is_file() and path.name != "manifest.json"
    }
    manifest = {
        "artifact": spec.artifact,
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": summary["generated_utc"],
        "title": spec.artifact_title,
        "labels": summary["labels"],
        "case": spec.key,
        "target": summary["target"],
        "operable_screen": summary["operable_screen"],
        "inputs": _case_inputs(spec, mask_summary),
        "outputs": outputs,
        "validation": {
            "ws4_validate": summary["ws4"]["validate"],
            "run_offsets": summary["ws4"]["run_offsets"],
            "sustained": summary["schedule"]["sustained"],
            "sustained_covered_provinces": summary["schedule"][
                "sustained_covered_provinces"
            ],
            "period_shortfall_m3": summary["schedule"]["period_shortfall_m3"],
            "province_shortfall_m3": summary["schedule"]["province_shortfall_m3"],
            "max_nominal_control_m3": summary["max_sustainable"][
                "max_nominal_control_m3"
            ],
        },
        "d_status": summary["d_status"],
    }
    _write_json(spec.chain_dir / "manifest.json", manifest)


def _combined_comparison(
    case_summaries: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for spec in CASES:
        summary = case_summaries[spec.key]
        full = summary["full_forest_reference"]
        schedule = summary["schedule"]
        max_level = summary["max_sustainable"]
        rows.append(
            {
                "case": spec.key,
                "target_annual_m3": summary["target"]["annual_m3"],
                "full_forest_sustained": full["sustained"],
                "full_forest_annual_min_m3": full["annual_min_m3"],
                "full_forest_annual_max_m3": full["annual_max_m3"],
                "full_forest_annual_mean_m3": full["annual_mean_m3"],
                "full_forest_selected_blocks": full["selected_blocks"],
                "full_forest_selected_area_ha": full["selected_area_ha"],
                "operable_sustained": schedule["sustained"],
                "operable_sustained_covered_provinces": schedule[
                    "sustained_covered_provinces"
                ],
                "operable_annual_min_m3": schedule["annual_min_m3"],
                "operable_annual_max_m3": schedule["annual_max_m3"],
                "operable_annual_mean_m3": schedule["annual_mean_m3"],
                "operable_period_shortfall_m3": schedule["period_shortfall_m3"],
                "operable_province_shortfall_m3": schedule["province_shortfall_m3"],
                "operable_selected_blocks": schedule["selected_blocks"],
                "operable_selected_area_ha": schedule["selected_area_ha"],
                "operable_mask_area_ha": summary["operable_screen"][
                    "selected_area_ha"
                ],
                "max_nominal_control_m3": max_level["max_nominal_control_m3"],
                "max_operable_harvest_m3": max_level["max_operable_harvest_m3"],
                "shortfall_provinces": "|".join(schedule["shortfall_provinces"]),
                "binding_provinces": "|".join(max_level["binding_provinces"]),
                **ROW_LABELS,
            }
        )
    return rows


def _write_combined_report(summary: Mapping[str, Any]) -> None:
    source_rows = summary["operable_screen"]["by_province"]
    source_table = _md_table(
        [
            "Province",
            "Coverage",
            "Published share",
            "Published area Mha",
            "WS4 forest Mha",
            "Share target Mha",
            "Selected Mha",
            "Selected blocks",
        ],
        [
            [
                str(row["province"]),
                "yes" if row["coverage_available"] else "no",
                (
                    f"{float(row['operable_share_of_treed']) * 100:.4f}%"
                    if row["operable_share_of_treed"] is not None
                    else "n/a"
                ),
                (
                    f"{float(row['published_operable_proxy_ha']) / 1e6:.3f}"
                    if row["published_operable_proxy_ha"] is not None
                    else "n/a"
                ),
                f"{float(row['ws4_forest_ha']) / 1e6:.3f}",
                f"{float(row['share_target_ha']) / 1e6:.3f}",
                f"{float(row['selected_area_ha']) / 1e6:.3f}",
                f"{int(row['selected_blocks']):,}",
            ]
            for row in source_rows
        ],
    )
    case_table = _md_table(
        [
            "Case",
            "Target Mm3/yr",
            "Sustained",
            "Covered sustained",
            "Annual min Mm3/yr",
            "Annual max Mm3/yr",
            "Annual mean Mm3/yr",
            "Period shortfall Mm3",
            "Province shortfall Mm3",
            "Blocks",
            "Treated area Mha",
            "Max nominal Mm3/yr",
            "Max operable Mm3/yr",
        ],
        [
            [
                str(row["case"]),
                f"{float(row['target_annual_m3']) / 1e6:.4f}",
                str(bool(row["operable_sustained"])),
                str(bool(row["operable_sustained_covered_provinces"])),
                f"{float(row['operable_annual_min_m3']) / 1e6:.4f}",
                f"{float(row['operable_annual_max_m3']) / 1e6:.4f}",
                f"{float(row['operable_annual_mean_m3']) / 1e6:.4f}",
                f"{float(row['operable_period_shortfall_m3']) / 1e6:.4f}",
                f"{float(row['operable_province_shortfall_m3']) / 1e6:.4f}",
                f"{int(row['operable_selected_blocks']):,}",
                f"{float(row['operable_selected_area_ha']) / 1e6:.3f}",
                f"{float(row['max_nominal_control_m3']) / 1e6:.4f}",
                f"{float(row['max_operable_harvest_m3']) / 1e6:.4f}",
            ]
            for row in summary["comparison"]
        ],
    )
    full_rows = [
        "| "
        + " | ".join(
            [
                str(row["case"]),
                f"{float(row['full_forest_annual_mean_m3']) / 1e6:.4f}",
                str(bool(row["full_forest_sustained"])),
                f"{float(row['operable_annual_mean_m3']) / 1e6:.4f}",
                str(bool(row["operable_sustained"])),
                f"{float(row['max_nominal_control_m3']) / 1e6:.4f}",
                "|".join(row["shortfall_provinces"].split("|")) or "none",
            ]
        )
        + " |"
        for row in summary["comparison"]
    ]
    full_table = "\n".join(
        [
            "| Case | Full-forest Mm3/yr | Full-forest sustained | Operable Mm3/yr | Operable sustained | Max nominal Mm3/yr | Shortfall provinces |",
            "|---|---:|---|---:|---|---:|---|",
            *full_rows,
        ]
    )
    key_metrics = (
        "harvest_m3",
        "solid_product_m3",
        "pulp_product_odt",
        "process_residue_pj",
        "harvest_residue_gross_pj",
        "harvest_residue_recoverable_r060_pj",
        "total_residue_gross_pj",
    )
    chain_rows = [
        "| "
        + " | ".join(
            [
                str(spec.key),
                str(row["metric"]),
                str(row["unit"]),
                f"{float(row['chain_v51_value']):,.6f}",
                f"{float(row[f'{spec.primary_basis}_value']):,.6f}",
                f"{float(row[f'{spec.primary_basis}_delta_pct']):+.4f}",
                f"{float(row[f'{SECONDARY_BASIS}_value']):,.6f}",
                f"{float(row[f'{SECONDARY_BASIS}_delta_pct']):+.4f}",
            ]
        )
        + " |"
        for spec in CASES
        for row in summary["cases"][spec.key]["chain"]["comparison"]
        if row["metric"] in key_metrics
    ]
    chain_table = "\n".join(
        [
            "| Case | Metric | Unit | v5.1 full-forest | Operable primary | Delta % | Operable achieved | Delta % |",
            "|---|---|---|---:|---:|---:|---:|---:|",
            *chain_rows,
        ]
    )
    osecan = summary["osecan"]
    report = f"""# Sustainable WS4 cases on the share-based operable landbase

**Artifact:** `{ARTIFACT}` **Version:** {ARTIFACT_VERSION}

**Status:** {summary["status"]}. The 100, 119, and 150 Mm3/yr cases were rerun
on a share-based operable proxy of the national 990 m WS4 estate. The strict
all-province cases are not sustained; the maximum schedule-only levels on the
covered operable provinces are reported below.

**Labels:** `model_estimate=true`, `observed=false`, `policy_grade=false`,
`national_claim=false`, `ws4_derived=true`, `sustainable_schedule=true`,
`share_based_operable_proxy=true`, `legal_thlb=false`, `slope_screen=false`,
`official_osecan_changed=false`, `chain_v51_unchanged=true`.

## 1. Operable screen

Screen case `{SCREEN_CASE}`: SCANFI median age >= 30 years, NBAC 2020 burned
pixels excluded, block centre within 10 km of a public NRN road. The mask is a
deterministic area-share proxy: within each province, blocks are ranked by
`blake2b(province:block_id)` and taken whole until the selected forest area
matches `operable_share_of_treed * WS4 province forest area`.

{source_table}

National selected area: {summary["operable_screen"]["national"]["selected_area_ha"]/1e6:,.3f}
Mha across {summary["operable_screen"]["national"]["selected_blocks"]:,} blocks.
Published absolute screen area:
{summary["operable_screen"]["national"]["published_operable_proxy_ha"]/1e6:,.3f}
Mha. The difference is the WS4 block-forest denominator versus the SCANFI treed
denominator; the share is preserved.

**No legal THLB, national slope DEM, or spatial road overlay is available.**
The screen is an area-share proxy, not legal THLB, slope, or spatial road
screening. NU and PE have no 2016 tenure coverage and therefore no operable
blocks.

## 2. Case results

{case_table}

## 3. Full-forest versus operable

{full_table}

The full-forest cases are the existing sustainable 100/119/150 Mm3 artifacts.
The operable cases restrict eligibility to the mask and disable the
cross-province fallback; provincial targets are otherwise the frozen case
shares. The operable cases fail because QC, NB, NS, SK, NL, and PE cannot meet
their allocated targets on the screened area; the binding province sets the
maximum sustainable level.

## 4. Chain comparison against frozen v5.1 full-forest totals

{chain_table}

The primary operable basis is the first-period annual average; the achieved
basis is the 100-year average. Both are unscaled and use frozen v5.1
per-province coefficients. The reference is the frozen full-forest v5.1 case
total for each scenario.

## 5. OSeCAN handoff

{osecan["status_text"]}

## 6. Gaps and limitations

- No legal THLB boundary.
- No national slope/elevation DEM; no slope screen.
- Road distance is public-NRN Euclidean distance at 990 m, not a spatial road
  overlay; resource and industrial roads are absent.
- NBAC 2020 only; 2010/2015 disturbance cases are gaps.
- SCANFI age is a prediction surface.
- Managed share is uniform inside each province-ecoprovince.
- The mask is not spatially intersected with the tenure, burn, road, or slope
  rasters.
- Max-sustainable levels are schedule-only diagnostics; they are not
  WS4-simulated or policy-tested.
"""
    (CASES_ROOT / "WS4_OPERABLE_CASES.md").write_text(report, encoding="utf-8")


def _combined_manifest(
    summary: Mapping[str, Any], mask_summary: Mapping[str, Any]
) -> None:
    outputs = {
        path.name: {"bytes": path.stat().st_size, "sha256": _sha256(path)}
        for path in sorted(CASES_ROOT.iterdir())
        if path.is_file() and path.name != "manifest.json"
    }
    inputs: dict[str, str] = {}
    for spec in CASES:
        inputs.update(_case_inputs(spec, mask_summary))
    manifest = {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": summary["generated_utc"],
        "title": ARTIFACT_TITLE,
        "labels": summary["labels"],
        "cases": {
            spec.key: {
                "chain_dir": _relative(spec.chain_dir),
                "schedule_dir": _relative(spec.output_dir),
                "target_annual_m3": summary["cases"][spec.key]["target"][
                    "annual_m3"
                ],
                "sustained": summary["cases"][spec.key]["schedule"]["sustained"],
                "max_nominal_control_m3": summary["cases"][spec.key][
                    "max_sustainable"
                ]["max_nominal_control_m3"],
            }
            for spec in CASES
        },
        "operable_mask": {
            "dir": _relative(MASK_DIR),
            "selected_area_ha": mask_summary["national"]["selected_area_ha"],
            "selected_blocks": mask_summary["national"]["selected_blocks"],
        },
        "inputs": inputs,
        "outputs": outputs,
        "osecan": summary["osecan"],
        "validation": summary["validation"],
        "d_status": summary["d_status"],
    }
    _write_json(CASES_ROOT / "manifest.json", manifest)


def build() -> dict[str, Any]:
    """Build the operable mask, three case reruns, chains, and OSeCAN handoff."""
    assert_no_unexpected_deletions()
    from workflows.national.facilities import forestry_osecan_handoff_ws4 as handoff
    from workflows.national.facilities import ws4_operable_cases_osecan as osecan

    chain_v51_before = handoff._tree_state(chain.CHAIN_V51_DIR)
    osecan_before = handoff._tree_state(handoff.OSECAN_DIR)
    CASES_ROOT.mkdir(parents=True, exist_ok=True)

    estate = base.load_estate()
    rotation_table = base.compute_rotation_table(
        estate.curve_ids, estate.curve_matrix
    )
    mask, mask_frame, mask_summary = build_operable_mask(estate)
    _ = mask_frame
    validate_text = base.run_ws4_validate()

    case_summaries = {
        spec.key: _run_case(
            spec,
            estate,
            mask,
            mask_summary,
            rotation_table,
            validate_text,
        )
        for spec in CASES
    }
    comparison_rows = _combined_comparison(case_summaries)
    _write_csv(
        CASES_ROOT / "full_forest_vs_operable.csv",
        list(comparison_rows[0].keys()),
        comparison_rows,
    )
    _write_csv(
        CASES_ROOT / "chain_comparison_vs_v51.csv",
        [
            "case",
            "metric",
            "unit",
            "chain_v51_value",
            "primary_basis",
            "primary_value",
            "primary_delta",
            "primary_delta_pct",
            "achieved_value",
            "achieved_delta",
            "achieved_delta_pct",
            *ROW_LABELS.keys(),
        ],
        [
            {
                "case": spec.key,
                "metric": row["metric"],
                "unit": row["unit"],
                "chain_v51_value": row["chain_v51_value"],
                "primary_basis": spec.primary_basis,
                "primary_value": row[f"{spec.primary_basis}_value"],
                "primary_delta": row[f"{spec.primary_basis}_delta"],
                "primary_delta_pct": row[f"{spec.primary_basis}_delta_pct"],
                "achieved_value": row[f"{SECONDARY_BASIS}_value"],
                "achieved_delta": row[f"{SECONDARY_BASIS}_delta"],
                "achieved_delta_pct": row[f"{SECONDARY_BASIS}_delta_pct"],
                **ROW_LABELS,
            }
            for spec in CASES
            for row in case_summaries[spec.key]["chain"]["comparison"]
        ],
    )

    osecan_summary = osecan.build(
        case_specs=CASES,
        case_dirs={spec.key: spec.chain_dir for spec in CASES},
        output_dir=CASES_ROOT / "osecan",
    )
    status = (
        "pass"
        if osecan_summary["status"] == "pass"
        and osecan_summary["official_unchanged"]
        else "fail"
    )
    summary: dict[str, Any] = {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "status": status,
        "title": ARTIFACT_TITLE,
        "labels": LABELS,
        "operable_screen": mask_summary,
        "cases": case_summaries,
        "comparison": comparison_rows,
        "max_sustainable": {
            spec.key: case_summaries[spec.key]["max_sustainable"]
            for spec in CASES
        },
        "osecan": {
            "status": osecan_summary["status"],
            "status_text": osecan_summary["status_text"],
            "dir": _relative(CASES_ROOT / "osecan"),
            "frozen_matches": osecan_summary["frozen_matches"],
            "frozen_21_of_21": osecan_summary["frozen_21_of_21"],
            "case_matches": {
                spec.key: {
                    "primary": osecan_summary["cases"][spec.key]["primary_matches"],
                    "secondary": osecan_summary["cases"][spec.key][
                        "secondary_matches"
                    ],
                    "positions": osecan_summary["cases"][spec.key]["positions"],
                }
                for spec in CASES
            },
            "official_unchanged": osecan_summary["official_unchanged"],
            "official_detail": osecan_summary["official_detail"],
        },
        "validation": {
            "mask_selected_share_within_half_block": mask_summary["validation"][
                "selected_share_within_half_block_all_covered"
            ],
            "case_count": len(CASES),
            "all_cases_operable_only": True,
            "all_cases_no_under_rotation": True,
            "osecan_checks": osecan_summary["validation"]["checks"],
        },
        "d_status": assert_no_unexpected_deletions(),
    }
    chain_v51_after = handoff._tree_state(chain.CHAIN_V51_DIR)
    osecan_after = handoff._tree_state(handoff.OSECAN_DIR)
    if chain_v51_before != chain_v51_after:
        raise Ws4OperableCasesError(
            "frozen chain v5.1 tree changed during the operable build"
        )
    if osecan_before != osecan_after:
        raise Ws4OperableCasesError(
            "official OSeCAN tree changed during the operable build"
        )
    summary["frozen_integrity"] = {
        "chain_v51_before": chain_v51_before,
        "chain_v51_after": chain_v51_after,
        "chain_v51_unchanged": chain_v51_before == chain_v51_after,
        "official_osecan_before": osecan_before,
        "official_osecan_after": osecan_after,
        "official_osecan_unchanged": osecan_before == osecan_after,
    }
    _write_json(CASES_ROOT / "summary.json", summary)
    _write_combined_report(summary)
    _combined_manifest(summary, mask_summary)
    return summary


def main() -> None:
    summary = build()
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
