"""Build a BC log-market price surface for the pilot industrial sorts.

The surface is deliberately narrow.  It maps a published BC Interior log-market
species group x end-use price to a pilot TIPSY industrial sort group only where
both sides of that mapping are documented:

* species -> market group: ``nemora.workflows.bucking.market_group_for`` (the
  same mapping the bucker uses);
* TIPSY sort group -> published end use: the group names in
  ``rules/tipsy45_industrial_sorts.csv`` and the ``price_key`` contract in
  ``rules/bc_interior.yaml``;
* prices: reported averages in ``bc_log_market_prices.csv``.

Coast grade letters are quality grades, not TIPSY dimension sorts.  No coast
grade -> industrial-sort crosswalk is documented, so coast rows are reported as
gaps and are never used in the TSA24 surface.

The surface uses the latest reported interior observation for each
``species x end_use`` cell.  Ties at the same period prefer the three-month
report over the one-month report.  A missing cell is a gap, not a zero and not a
proxy.  Every price row carries its source report, period, reported volume, and
staleness so a reviewer can see exactly what was used.

This module does not tune a threshold, fit a price, or assign a grade.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from .bucking import load_grade_rules

__all__ = [
    "PRICE_SURFACE_SCHEMA",
    "SurfaceError",
    "build_sort_group_map",
    "build_surface",
    "select_latest_observations",
    "write_surface",
]

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PRICES = (
    REPO_ROOT
    / "data"
    / "derived"
    / "national"
    / "harvest_basis"
    / "bc_log_market"
    / "bc_log_market_prices.csv"
)
DEFAULT_RULES = REPO_ROOT / "workflows" / "national" / "bucking" / "rules" / "bc_interior.yaml"
DEFAULT_SURFACE_RULES = (
    REPO_ROOT / "workflows" / "national" / "bucking" / "rules" / "bc_interior_log_market.yaml"
)
DEFAULT_OUT_DIR = (
    REPO_ROOT / "data" / "derived" / "national" / "bucking" / "bc_log_market_surface_v1"
)

PRICE_SURFACE_SCHEMA = "bc_log_market_sort_price_surface_v1"

#: Market groups used by nemora.  The bucker maps species to these groups
#: before it looks up a product price.
MARKET_GROUPS: tuple[str, ...] = (
    "SPF",
    "Df-Larch",
    "Hem-Bal",
    "Cedar",
    "White Pine",
    "Yellow Pine",
    "Deciduous",
    "Other",
)
CONIFER_MARKET_GROUPS: tuple[str, ...] = (
    "SPF",
    "Df-Larch",
    "Hem-Bal",
    "Cedar",
    "White Pine",
    "Yellow Pine",
    "Other",
)

#: TIPSY industrial sort group -> published interior end use.  Sawlog/Peeler
#: is priced as sawlog: peeler quality is not represented by the dimension
#: bucker and the published peeler price therefore cannot be assigned.
SORT_GROUP_END_USE: Mapping[str, str] = {
    "Sawlog/Peeler": "sawlog",
    "Large Gang": "sawlog",
    "Small Gang": "sawlog",
    "Chip'n'saw": "sawlog",
    "Pulp": "pulpwood",
    "Deciduous Sawlog/Peeler": "sawlog",
    "Deciduous Pulp": "pulpwood",
}
SORT_GROUP_BASIS: Mapping[str, str] = {
    "Sawlog/Peeler": (
        "tipsy45_industrial_sorts.csv group name; peeler quality is not "
        "represented, so the published sawlog product price is used"
    ),
    "Large Gang": (
        "tipsy45_industrial_sorts.csv group name and bc_interior.yaml "
        "repository price_key=sawlog"
    ),
    "Small Gang": (
        "tipsy45_industrial_sorts.csv group name and bc_interior.yaml "
        "repository price_key=sawlog"
    ),
    "Chip'n'saw": (
        "tipsy45_industrial_sorts.csv group name and bc_interior.yaml "
        "repository price_key=sawlog"
    ),
    "Pulp": (
        "tipsy45_industrial_sorts.csv group name and the published interior "
        "pulpwood product"
    ),
    "Deciduous Sawlog/Peeler": (
        "tipsy45_industrial_sorts.csv group name and the published deciduous "
        "sawlog product"
    ),
    "Deciduous Pulp": (
        "tipsy45_industrial_sorts.csv group name and the published deciduous "
        "pulpwood product"
    ),
}
REPORT_BASIS_RANK: Mapping[str, int] = {"3_month": 1, "1_month": 0}


class SurfaceError(RuntimeError):
    """Raised when the market data or a mapping violates its contract."""


@dataclass(frozen=True, slots=True)
class SortGroup:
    name: str
    end_use: str
    status: str
    basis: str
    note: str


@dataclass(frozen=True, slots=True)
class SurfaceCell:
    market_group: str
    end_use: str
    price_cad_m3: float | None
    status: str
    period_end: str | None
    report_basis: str | None
    report_id: str | None
    source_document: str | None
    market_volume_m3: float | None
    staleness_days: int | None
    note: str


def _require_columns(frame: pd.DataFrame, required: Sequence[str], source: Path) -> None:
    missing = sorted(set(required).difference(frame.columns))
    if missing:
        raise SurfaceError(f"{source} is missing columns: {', '.join(missing)}")


def _parse_date(value: Any, *, context: str) -> date:
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError as exc:
        raise SurfaceError(f"invalid date {value!r} in {context}") from exc


def _positive_price(value: Any, *, context: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise SurfaceError(f"price is not numeric in {context}: {value!r}") from exc
    if not math.isfinite(number):
        raise SurfaceError(f"price is not finite in {context}: {value!r}")
    if number <= 0.0:
        raise SurfaceError(f"price must be positive in {context}: {value!r}")
    return number


def load_market_prices(path: Path = DEFAULT_PRICES) -> pd.DataFrame:
    """Load and parse the tidy market table at the boundary."""
    price_path = Path(path)
    if not price_path.exists():
        raise SurfaceError(f"market price table not found: {price_path}")
    frame = pd.read_csv(price_path)
    required = (
        "report_id",
        "region",
        "report_basis",
        "period_start",
        "period_end",
        "log_class",
        "species",
        "grade",
        "end_use",
        "price_cad_m3",
        "price_status",
        "is_aggregate",
        "price_plausibility",
        "volume_m3",
        "source_document",
        "source_sha256",
        "retrieved_utc",
    )
    _require_columns(frame, required, price_path)
    if frame.empty:
        raise SurfaceError(f"market price table is empty: {price_path}")

    text_columns = (
        "report_id",
        "region",
        "report_basis",
        "log_class",
        "species",
        "price_status",
        "source_document",
        "source_sha256",
    )
    for column in text_columns:
        frame[column] = frame[column].fillna("").astype(str).str.strip()
        if (frame[column] == "").any():
            raise SurfaceError(f"{price_path} has blank {column} values")

    frame["is_aggregate"] = frame["is_aggregate"].astype(bool)
    frame["price_cad_m3"] = pd.to_numeric(frame["price_cad_m3"], errors="raise")
    frame["volume_m3"] = pd.to_numeric(frame["volume_m3"], errors="coerce")
    frame["period_end"] = frame["period_end"].astype(str).str[:10]
    frame["grade"] = frame["grade"].fillna("").astype(str).str.strip()
    frame["end_use"] = frame["end_use"].fillna("").astype(str).str.strip()

    if frame[["period_end", "species"]].isna().any().any():
        raise SurfaceError(f"{price_path} has missing period_end or species")
    if (frame["price_cad_m3"] < 0).any() or frame["price_cad_m3"].isna().any():
        raise SurfaceError(f"{price_path} has negative or NaN prices")
    return frame


def select_latest_observations(prices: pd.DataFrame) -> pd.DataFrame:
    """Return one reported observation per region/class/species/grade/end use."""
    reported = prices[
        (prices["price_status"] == "reported")
        & (~prices["is_aggregate"].astype(bool))
        & (prices["price_cad_m3"] > 0)
    ].copy()
    if reported.empty:
        raise SurfaceError("market table has no positive, reported, non-aggregate cells")

    reported["_period"] = pd.to_datetime(reported["period_end"], errors="raise")
    reported["_basis_rank"] = (
        reported["report_basis"].map(REPORT_BASIS_RANK).fillna(-1).astype(int)
    )
    reported["_retrieved"] = pd.to_datetime(
        reported["retrieved_utc"], errors="coerce", utc=True
    )
    reported = reported.sort_values(
        ["_period", "_basis_rank", "_retrieved", "report_id"],
        ascending=[True, True, True, True],
    )
    keys = ["region", "log_class", "species", "grade", "end_use"]
    selected = reported.groupby(keys, dropna=False, sort=True).tail(1).copy()
    selected = selected.drop(columns=["_period", "_basis_rank", "_retrieved"])
    return selected.reset_index(drop=True)


def build_sort_group_map(rules_path: Path = DEFAULT_RULES) -> pd.DataFrame:
    """Map every pilot sort code to its documented market product."""
    rule_set = load_grade_rules(Path(rules_path))
    rows: list[dict[str, Any]] = []
    for rule in rule_set.rules:
        group = rule.tipsy_group or ""
        if group not in SORT_GROUP_END_USE:
            raise SurfaceError(
                f"sort {rule.sort_code} has undocumented TIPSY group {group!r}"
            )
        end_use = SORT_GROUP_END_USE[group]
        target_groups = (
            ("Deciduous",) if rule.species_class == "deciduous" else CONIFER_MARKET_GROUPS
        )
        rows.append(
            {
                "sort_code": rule.sort_code,
                "species_class": rule.species_class,
                "tipsy_group": group,
                "repository_product": rule.product,
                "repository_price_key": rule.price_key,
                "market_end_use": end_use,
                "target_market_groups": "|".join(target_groups),
                "mapping_status": (
                    "documented_tipsy_group_product"
                    if rule.product == end_use
                    else "documented_tipsy_group_product_changes_repository_key"
                ),
                "mapping_basis": SORT_GROUP_BASIS[group],
                "changes_repository_assignment": bool(rule.product != end_use),
            }
        )
    frame = pd.DataFrame(rows).sort_values(["species_class", "sort_code"]).reset_index(drop=True)
    if len(frame) != 20:
        raise SurfaceError(f"expected 20 pilot sort codes, found {len(frame)}")
    if frame["sort_code"].duplicated().any():
        raise SurfaceError("pilot sort codes are not unique")
    return frame


def build_sort_group_rows(sort_map: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for group, end_use in SORT_GROUP_END_USE.items():
        subset = sort_map[sort_map["tipsy_group"] == group]
        if subset.empty:
            raise SurfaceError(f"pilot sort map has no codes for TIPSY group {group}")
        rows.append(
            {
                "tipsy_group": group,
                "market_end_use": end_use,
                "sort_codes": "|".join(sorted(subset["sort_code"])),
                "mapping_status": "documented",
                "mapping_basis": SORT_GROUP_BASIS[group],
            }
        )
    return pd.DataFrame(rows)


def _latest_cell(
    observations: pd.DataFrame,
    *,
    market_group: str,
    end_use: str,
) -> pd.Series | None:
    matches = observations[
        (observations["region"] == "interior")
        & (observations["species"] == market_group)
        & (observations["end_use"] == end_use)
    ]
    if matches.empty:
        return None
    row = matches.sort_values(["period_end", "report_basis"], ascending=[True, True]).tail(1)
    return row.iloc[0]


def build_surface(
    prices: pd.DataFrame,
    sort_map: pd.DataFrame,
    *,
    as_of: date,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Return (surface cells, species-level gap rows, coast gap summary)."""
    observations = select_latest_observations(prices)
    cells: list[SurfaceCell] = []
    species_gaps: list[dict[str, Any]] = []

    for sort in sort_map.itertuples(index=False):
        target_groups = tuple(str(sort.target_market_groups).split("|"))
        for market_group in target_groups:
            observation = _latest_cell(
                observations,
                market_group=market_group,
                end_use=str(sort.market_end_use),
            )
            context = f"{market_group}/{sort.market_end_use}"
            if observation is None:
                cells.append(
                    SurfaceCell(
                        market_group=market_group,
                        end_use=str(sort.market_end_use),
                        price_cad_m3=None,
                        status="unmapped_no_published_price",
                        period_end=None,
                        report_basis=None,
                        report_id=None,
                        source_document=None,
                        market_volume_m3=None,
                        staleness_days=None,
                        note=f"no published interior {context} cell",
                    )
                )
                species_gaps.append(
                    {
                        "gap_type": "missing_published_price",
                        "market_group": market_group,
                        "market_end_use": sort.market_end_use,
                        "sort_code": sort.sort_code,
                        "tipsy_group": sort.tipsy_group,
                        "detail": f"no published interior {context} cell",
                    }
                )
                continue

            price = _positive_price(
                observation["price_cad_m3"], context=f"{context}/{observation['report_id']}"
            )
            period_end = _parse_date(observation["period_end"], context=context)
            volume = observation["volume_m3"]
            volume_value = float(volume) if pd.notna(volume) else None
            cells.append(
                SurfaceCell(
                    market_group=market_group,
                    end_use=str(sort.market_end_use),
                    price_cad_m3=price,
                    status="documented",
                    period_end=period_end.isoformat(),
                    report_basis=str(observation["report_basis"]),
                    report_id=str(observation["report_id"]),
                    source_document=str(observation["source_document"]),
                    market_volume_m3=volume_value,
                    staleness_days=(as_of - period_end).days,
                    note=(
                        "latest published interior observation; "
                        f"reported volume {volume_value:,.0f} m3"
                        if volume_value is not None
                        else "latest published interior observation; volume not reported"
                    ),
                )
            )

    surface = pd.DataFrame([asdict(cell) for cell in cells])
    surface = surface.drop_duplicates(
        subset=["market_group", "end_use"], keep="first"
    ).sort_values(["market_group", "end_use"]).reset_index(drop=True)
    gaps = pd.DataFrame(species_gaps)
    if gaps.empty:
        gaps = pd.DataFrame(
            columns=[
                "gap_type",
                "market_group",
                "market_end_use",
                "sort_code",
                "tipsy_group",
                "detail",
            ]
        )

    coast = observations[observations["region"] == "coast"].copy()
    if coast.empty:
        coast_summary = pd.DataFrame(
            columns=[
                "gap_type",
                "log_class",
                "species",
                "grade",
                "n_series",
                "latest_period_end",
                "latest_price_cad_m3",
                "detail",
            ]
        )
    else:
        latest_coast = (
            coast.sort_values(["log_class", "species", "grade", "period_end"])
            .groupby(["log_class", "species", "grade"], dropna=False, sort=True)
            .tail(1)
            .reset_index(drop=True)
        )
        counts = (
            coast.groupby(["log_class", "species", "grade"], dropna=False)
            .size()
            .rename("n_series")
            .reset_index()
        )
        coast_summary = latest_coast[
            ["log_class", "species", "grade", "period_end", "price_cad_m3"]
        ].merge(counts, on=["log_class", "species", "grade"], how="left")
        coast_summary = coast_summary.rename(
            columns={
                "period_end": "latest_period_end",
                "price_cad_m3": "latest_price_cad_m3",
            }
        )
        coast_summary.insert(0, "gap_type", "unmapped_no_sort_crosswalk")
        coast_summary["detail"] = (
            "coast grade letter is a quality grade; no documented crosswalk to "
            "TIPSY industrial dimension sorts or to TSA24 interior species groups"
        )
        coast_summary = coast_summary[
            [
                "gap_type",
                "log_class",
                "species",
                "grade",
                "n_series",
                "latest_period_end",
                "latest_price_cad_m3",
                "detail",
            ]
        ]
    return surface, gaps, coast_summary


def _surface_prices(surface: pd.DataFrame) -> dict[str, dict[str, float]]:
    prices: dict[str, dict[str, float]] = {group: {} for group in MARKET_GROUPS}
    for row in surface[surface["status"] == "documented"].itertuples(index=False):
        if row.price_cad_m3 is None or not math.isfinite(float(row.price_cad_m3)):
            raise SurfaceError(f"non-finite documented price for {row.market_group}/{row.end_use}")
        prices[row.market_group][row.end_use] = float(row.price_cad_m3)
    return {group: values for group, values in prices.items() if values}


def build_price_profile(
    surface: pd.DataFrame,
    *,
    as_of: date,
    source_sha256: str,
) -> dict[str, Any]:
    prices = _surface_prices(surface)
    periods = sorted(
        {
            str(row.period_end)
            for row in surface[surface["status"] == "documented"].itertuples(index=False)
            if row.period_end
        }
    )
    if not periods:
        raise SurfaceError("surface has no documented prices")
    return {
        "schema": PRICE_SURFACE_SCHEMA,
        "source": (
            "BC Interior Log Market Report, latest reported observation per "
            "species x end use in bc_log_market_prices.csv"
        ),
        "url": (
            "https://www2.gov.bc.ca/gov/content/industry/forestry/"
            "forestry-revenues/timber-pricing/interior-timber-pricing/"
            "interior-log-market-reports"
        ),
        "period": f"latest_observation_through_{as_of.isoformat()}",
        "observed_periods": periods,
        "units": "CAD/m3",
        "price_basis": "mill_delivered",
        "observation_type": "reported_market_average",
        "market_region": "interior",
        "source_table_sha256": source_sha256,
        "note": (
            "Published species x product averages only. Coast grade letters are "
            "not mapped because no coast-grade to TIPSY-sort crosswalk is "
            "documented. Peeler prices are not assigned because peeler quality "
            "is not represented by the dimension bucker. Missing cells are gaps, "
            "not substituted values."
        ),
        "prices": prices,
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_csv(frame: pd.DataFrame, path: Path) -> None:
    frame.to_csv(path, index=False, lineterminator="\n")


def _assert_surface_rules_match(sort_map: pd.DataFrame, surface_rules_path: Path) -> None:
    surface_rules = load_grade_rules(surface_rules_path)
    by_code = {rule.sort_code: rule for rule in surface_rules.rules}
    for row in sort_map.itertuples(index=False):
        rule = by_code.get(row.sort_code)
        if rule is None:
            raise SurfaceError(
                f"surface rule set {surface_rules_path} is missing sort {row.sort_code}"
            )
        if rule.product != row.market_end_use:
            raise SurfaceError(
                f"surface rule set gives {row.sort_code} product {rule.product!r}; "
                f"the documented market mapping requires {row.market_end_use!r}"
            )
        if rule.price_key != row.market_end_use:
            raise SurfaceError(
                f"surface rule set gives {row.sort_code} price_key {rule.price_key!r}; "
                f"the documented market mapping requires {row.market_end_use!r}"
            )


def write_surface(
    *,
    prices_path: Path = DEFAULT_PRICES,
    rules_path: Path = DEFAULT_RULES,
    surface_rules_path: Path = DEFAULT_SURFACE_RULES,
    out_dir: Path = DEFAULT_OUT_DIR,
    as_of: date | None = None,
) -> dict[str, Any]:
    """Build and write the surface, profile, gaps, and manifest."""
    price_path = Path(prices_path)
    rule_path = Path(rules_path)
    surface_rule_path = Path(surface_rules_path)
    output_dir = Path(out_dir)
    as_of_date = as_of or date.today()

    prices = load_market_prices(price_path)
    sort_map = build_sort_group_map(rule_path)
    _assert_surface_rules_match(sort_map, surface_rule_path)
    sort_groups = build_sort_group_rows(sort_map)
    surface, gaps, coast_gaps = build_surface(prices, sort_map, as_of=as_of_date)
    profile = build_price_profile(
        surface, as_of=as_of_date, source_sha256=_sha256(price_path)
    )

    if (surface["price_cad_m3"].dropna() <= 0).any():
        raise SurfaceError("surface contains a non-positive price")
    _surface_prices(surface)

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(sort_groups, output_dir / "tipsy_group_map.csv")
    _write_csv(sort_map, output_dir / "sort_code_map.csv")
    _write_csv(surface, output_dir / "price_surface.csv")
    _write_csv(gaps, output_dir / "map_gaps.csv")
    _write_csv(coast_gaps, output_dir / "coast_gaps.csv")
    (output_dir / "price_profile.yaml").write_text(
        yaml.safe_dump(profile, sort_keys=False), encoding="utf-8"
    )

    documented = surface[surface["status"] == "documented"]
    manifest = {
        "schema": PRICE_SURFACE_SCHEMA,
        "created_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "as_of": as_of_date.isoformat(),
        "label": "industrial_sort_validation",
        "grade_status": "not_assigned",
        "official_grade_validation": False,
        "inputs": [
            {
                "label": "bc_log_market_prices",
                "path": str(price_path.relative_to(REPO_ROOT))
                if price_path.is_relative_to(REPO_ROOT)
                else str(price_path),
                "sha256": _sha256(price_path),
                "rows": len(prices),
            },
            {
                "label": "pilot_sort_rules_baseline",
                "path": str(rule_path.relative_to(REPO_ROOT))
                if rule_path.is_relative_to(REPO_ROOT)
                else str(rule_path),
                "sha256": _sha256(rule_path),
            },
            {
                "label": "pilot_sort_rules_surface",
                "path": str(surface_rule_path.relative_to(REPO_ROOT))
                if surface_rule_path.is_relative_to(REPO_ROOT)
                else str(surface_rule_path),
                "sha256": _sha256(surface_rule_path),
            },
        ],
        "mapping": {
            "species_mapping": "nemora.workflows.bucking.market_group_for",
            "sort_group_end_use": dict(SORT_GROUP_END_USE),
            "coast_grade_policy": "unmapped_no_sort_crosswalk",
            "peeler_policy": "unmapped_quality_not_represented",
            "period_policy": (
                "latest reported observation per region/log_class/species/grade/end_use; "
                "same-period tie prefers 3_month over 1_month"
            ),
        },
        "coverage": {
            "n_sort_codes": len(sort_map),
            "n_tipsy_groups": len(sort_groups),
            "n_surface_cells": len(surface),
            "n_documented_cells": len(documented),
            "n_gap_cells": int((surface["status"] != "documented").sum()),
            "n_coast_gap_series": len(coast_gaps),
            "documented_market_groups": sorted(documented["market_group"].unique().tolist()),
            "documented_end_uses": sorted(documented["end_use"].unique().tolist()),
        },
        "outputs": [
            "tipsy_group_map.csv",
            "sort_code_map.csv",
            "price_surface.csv",
            "map_gaps.csv",
            "coast_gaps.csv",
            "price_profile.yaml",
        ],
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build the BC log-market price surface for TSA24 pilot industrial sorts."
    )
    parser.add_argument("--prices", type=Path, default=DEFAULT_PRICES)
    parser.add_argument("--rules", type=Path, default=DEFAULT_RULES)
    parser.add_argument("--surface-rules", type=Path, default=DEFAULT_SURFACE_RULES)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--as-of", type=date.fromisoformat, default=None)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        manifest = write_surface(
            prices_path=args.prices,
            rules_path=args.rules,
            surface_rules_path=args.surface_rules,
            out_dir=args.out_dir,
            as_of=args.as_of,
        )
    except (SurfaceError, FileNotFoundError, ValueError) as exc:
        print(f"SURFACE FAILED: {exc}")
        return 1
    print(json.dumps(manifest["coverage"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
