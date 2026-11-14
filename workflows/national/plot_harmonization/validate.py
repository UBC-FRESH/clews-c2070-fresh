"""Hard validation checks for the FVSCA calibration v0 outputs.

Run standalone::

    python -m workflows.national.plot_harmonization.validate \
        --out data/derived/fvsca_calibration/v0

Checks are deliberately fail-closed: a malformed key, a duplicate tree record,
an out-of-range measurement, or a broken reference fails the build.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .schema import (
    INTERVAL_COLUMNS,
    MAX_DBH_CM,
    MAX_HEIGHT_M,
    MAX_INTERVAL_YEARS,
    MAX_YEAR,
    MIN_DBH_CM,
    MIN_HEIGHT_M,
    MIN_INTERVAL_YEARS,
    MIN_YEAR,
    SITE_COLUMNS,
    SPECIES_GROUPS,
    STATUS_VALUES,
    TREE_COLUMNS,
    VISIT_COLUMNS,
)


@dataclass(frozen=True)
class Check:
    """One named validation result."""

    name: str
    status: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {"name": self.name, "status": self.status, "detail": self.detail}


def _check(name: str, condition: bool, detail: str) -> Check:
    return Check(name=name, status="PASS" if condition else "FAIL", detail=detail)


def _read(out: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    sites = pd.read_csv(
        out / "sites.csv",
        dtype={"source": "string", "site_id": "string"},
        low_memory=False,
    )
    visits = pd.read_csv(
        out / "visits.csv",
        dtype={"source": "string", "site_id": "string", "visit_id": "string"},
        low_memory=False,
    )
    trees = pd.read_csv(
        out / "trees.csv.gz",
        usecols=list(TREE_COLUMNS),
        dtype={
            "source": "string",
            "site_id": "string",
            "visit_id": "string",
            "tree_id": "string",
            "species_code": "string",
            "species_group": "string",
            "status": "string",
            "crown_class": "string",
            "damage_code": "string",
        },
        low_memory=False,
    )
    intervals = pd.read_csv(
        out / "intervals.csv.gz",
        dtype={
            "source": "string",
            "site_id": "string",
            "tree_id": "string",
            "status_end": "string",
        },
        low_memory=False,
    )
    return sites, visits, trees, intervals


def _missing_keys(
    left: pd.DataFrame,
    right: pd.DataFrame,
    keys: list[str],
    label: str,
) -> Check:
    left_keys = left[keys].drop_duplicates()
    right_keys = right[keys].drop_duplicates()
    before = len(left_keys)
    joined = left_keys.merge(right_keys, on=keys, how="left", indicator=True)
    missing = int((joined["_merge"] == "left_only").sum())
    return _check(
        label,
        missing == 0,
        f"{missing} of {before:,} distinct keys have no match",
    )


def _index_diff(expected: pd.Series, actual: pd.Series, label: str) -> Check:
    index = expected.index.union(actual.index)
    expected = expected.reindex(index, fill_value=0)
    actual = actual.reindex(index, fill_value=0)
    mismatches = int((expected != actual).sum())
    return _check(
        label,
        mismatches == 0,
        f"{mismatches:,} of {len(index):,} site keys disagree",
    )


def run_checks(out: Path) -> list[Check]:
    """Run all hard checks against an output directory."""
    out = Path(out)
    manifest_path = out / "manifest.json"
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.is_file()
        else {}
    )
    sites, visits, trees, intervals = _read(out)
    checks: list[Check] = []

    checks.append(
        _check(
            "site columns",
            tuple(sites.columns) == SITE_COLUMNS,
            f"{len(sites.columns)} columns",
        )
    )
    checks.append(
        _check(
            "visit columns",
            tuple(visits.columns) == VISIT_COLUMNS,
            f"{len(visits.columns)} columns",
        )
    )
    checks.append(
        _check(
            "tree columns",
            tuple(trees.columns) == TREE_COLUMNS,
            f"{len(trees.columns)} columns",
        )
    )
    checks.append(
        _check(
            "interval columns",
            tuple(intervals.columns) == INTERVAL_COLUMNS,
            f"{len(intervals.columns)} columns",
        )
    )

    for label, frame, keys in (
        ("sites", sites, ["source", "site_id"]),
        ("visits", visits, ["source", "site_id", "visit_id"]),
        ("trees", trees, ["source", "site_id", "visit_id", "tree_id"]),
        (
            "intervals",
            intervals,
            ["source", "site_id", "tree_id", "year0", "year1"],
        ),
    ):
        duplicates = int(frame.duplicated(keys, keep=False).sum())
        checks.append(
            _check(
                f"no duplicate {label} key",
                duplicates == 0,
                f"{duplicates:,} duplicate rows",
            )
        )

    checks.append(
        _missing_keys(
            visits, sites, ["source", "site_id"], "visit site referential integrity"
        )
    )
    checks.append(
        _missing_keys(
            trees,
            visits,
            ["source", "site_id", "visit_id"],
            "tree visit referential integrity",
        )
    )
    checks.append(
        _missing_keys(
            intervals,
            trees,
            ["source", "site_id", "tree_id"],
            "interval tree referential integrity",
        )
    )

    checks.append(
        _check(
            "site keys complete",
            int(sites[["source", "site_id"]].isna().any(axis=1).sum()) == 0,
            "null source/site_id",
        )
    )
    checks.append(
        _check(
            "visit keys complete",
            int(
                visits[["source", "site_id", "visit_id"]]
                .isna()
                .any(axis=1)
                .sum()
            )
            == 0,
            "null source/site_id/visit_id",
        )
    )
    checks.append(
        _check(
            "tree keys complete",
            int(
                trees[["source", "site_id", "visit_id", "tree_id"]]
                .isna()
                .any(axis=1)
                .sum()
            )
            == 0,
            "null source/site_id/visit_id/tree_id",
        )
    )

    dbh = pd.to_numeric(trees["dbh_cm"], errors="coerce")
    height = pd.to_numeric(trees["height_m"], errors="coerce")
    checks.append(
        _check(
            "DBH range",
            bool(dbh.dropna().between(MIN_DBH_CM, MAX_DBH_CM).all()),
            f"observed [{dbh.min()}, {dbh.max()}] cm",
        )
    )
    checks.append(
        _check(
            "height range",
            bool(height.dropna().between(MIN_HEIGHT_M, MAX_HEIGHT_M).all()),
            f"observed [{height.min()}, {height.max()}] m",
        )
    )
    checks.append(
        _check(
            "tree status domain",
            bool(trees["status"].isin(STATUS_VALUES).all()),
            f"values {sorted(trees['status'].dropna().unique().tolist())}",
        )
    )
    checks.append(
        _check(
            "species group domain",
            bool(trees["species_group"].isin(SPECIES_GROUPS).all()),
            f"values {sorted(trees['species_group'].dropna().unique().tolist())}",
        )
    )

    years = pd.to_numeric(visits["year"], errors="coerce")
    checks.append(
        _check(
            "visit year range",
            bool(years.dropna().between(MIN_YEAR, MAX_YEAR).all()),
            f"observed [{years.min()}, {years.max()}]",
        )
    )
    interval_years = pd.to_numeric(intervals["years"], errors="coerce")
    checks.append(
        _check(
            "interval length",
            bool(
                interval_years.dropna().between(
                    MIN_INTERVAL_YEARS, MAX_INTERVAL_YEARS
                ).all()
            ),
            f"observed [{interval_years.min()}, {interval_years.max()}] years",
        )
    )
    year0 = pd.to_numeric(intervals["year0"], errors="coerce")
    year1 = pd.to_numeric(intervals["year1"], errors="coerce")
    checks.append(
        _check(
            "interval year range",
            bool(
                year0.dropna().between(MIN_YEAR, MAX_YEAR).all()
                and year1.dropna().between(MIN_YEAR, MAX_YEAR).all()
            ),
            f"year0 [{year0.min()}, {year0.max()}], year1 [{year1.min()}, {year1.max()}]",
        )
    )
    checks.append(
        _check(
            "interval arithmetic",
            bool((interval_years == (year1 - year0)).all()),
            "years must equal year1-year0",
        )
    )
    exposure = pd.to_numeric(intervals["exposure_years"], errors="coerce")
    known = pd.to_numeric(intervals["known"], errors="coerce")
    checks.append(
        _check(
            "interval exposure",
            bool((exposure == interval_years * known).all()),
            "exposure_years must equal years*known",
        )
    )
    checks.append(
        _check(
            "interval end status",
            bool(intervals["status_end"].isin(["live", "dead"]).all()),
            f"values {sorted(intervals['status_end'].dropna().unique().tolist())}",
        )
    )
    checks.append(
        _check(
            "interval flags",
            bool(
                intervals["dead"].isin([0, 1]).all()
                and intervals["known"].isin([0, 1]).all()
                and (
                    intervals["dead"].astype(int)
                    == intervals["status_end"].eq("dead").astype(int)
                ).all()
            ),
            "dead/known must be 0/1 and dead must match status_end",
        )
    )

    site_visit_counts = visits.groupby(["source", "site_id"]).size()
    expected_visits = sites.set_index(["source", "site_id"])["n_visits"].astype(int)
    checks.append(
        _index_diff(expected_visits, site_visit_counts, "site n_visits consistency")
    )
    site_tree_counts = trees.groupby(["source", "site_id"]).size()
    expected_trees = sites.set_index(["source", "site_id"])["n_trees"].astype(int)
    checks.append(
        _index_diff(expected_trees, site_tree_counts, "site n_trees consistency")
    )
    visit_tree_counts = trees.groupby(["source", "site_id", "visit_id"]).size()
    expected_visit_trees = visits.set_index(
        ["source", "site_id", "visit_id"]
    )["n_trees"].astype(int)
    checks.append(
        _index_diff(expected_visit_trees, visit_tree_counts, "visit n_trees consistency")
    )

    source_counts: dict[str, dict[str, int]] = {}
    for source, frame in (
        ("sites", sites),
        ("visits", visits),
        ("trees", trees),
        ("intervals", intervals),
    ):
        for key, value in frame.groupby("source").size().items():
            source_counts.setdefault(str(key), {})[source] = int(value)
    for source, stats in manifest.get("sources", {}).items():
        output = stats.get("output", {})
        actual = source_counts.get(source, {})
        expected = {
            "sites": int(output.get("sites", 0)),
            "visits": int(output.get("visits", 0)),
            "trees": int(output.get("trees", 0)),
            "intervals": int(output.get("intervals", 0)),
        }
        disagreements = {
            name: (expected[name], actual.get(name, 0))
            for name in expected
            if expected[name] != actual.get(name, 0)
        }
        checks.append(
            _check(
                f"{source} manifest counts",
                not disagreements,
                "all four tables match"
                if not disagreements
                else f"expected/actual {disagreements}",
            )
        )
        selected = stats.get("selected", {})
        dropped = stats.get("dropped") or {}
        for name in ("sites", "visits", "trees"):
            denominator = int(selected.get(name, 0))
            numerator = int(output.get(f"{name}", 0))
            if denominator == 0:
                continue
            delta = abs(numerator - denominator) / denominator
            checks.append(
                _check(
                    f"{source} {name} reconciliation",
                    delta <= 0.005 or bool(dropped),
                    f"{delta:.4%} difference; dropped reasons: "
                    + (", ".join(sorted(dropped)) if dropped else "none"),
                )
            )

    return checks


def render_markdown(checks: list[Check]) -> str:
    """Render checks as a QA markdown section."""
    lines = ["## Validation", ""]
    failed = sum(1 for check in checks if check.status != "PASS")
    lines.append(f"{len(checks)} checks, {failed} failed.")
    lines.append("")
    lines.append("| check | status | detail |")
    lines.append("|---|---|---|")
    for check in checks:
        detail = str(check.detail).replace("|", "\\|")
        lines.append(f"| {check.name} | {check.status} | {detail} |")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    checks = run_checks(args.out)
    print(render_markdown(checks))
    return 0 if all(check.status == "PASS" for check in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
