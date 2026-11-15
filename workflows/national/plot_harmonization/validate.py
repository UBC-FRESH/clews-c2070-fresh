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

from .common import sha256_file
from .schema import (
    CROSSWALK_COLUMNS,
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
            "visit_id0": "string",
            "visit_id1": "string",
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
    if not manifest_path.is_file():
        return [
            _check(
                "manifest present",
                False,
                f"{manifest_path.name} is missing; validation cannot verify counts or hashes",
            )
        ]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    sites, visits, trees, intervals = _read(out)
    checks: list[Check] = []

    for name, frame in (
        ("sites", sites),
        ("visits", visits),
        ("trees", trees),
        ("intervals", intervals),
    ):
        checks.append(
            _check(
                f"non-empty {name}",
                not frame.empty,
                f"{len(frame):,} rows",
            )
        )

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
        _missing_keys(
            intervals.rename(columns={"visit_id0": "visit_id"}),
            visits,
            ["source", "site_id", "visit_id"],
            "interval start visit referential integrity",
        )
    )
    checks.append(
        _missing_keys(
            intervals.rename(columns={"visit_id1": "visit_id"}),
            visits,
            ["source", "site_id", "visit_id"],
            "interval end visit referential integrity",
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
            bool(dbh.notna().any())
            and bool(dbh.dropna().between(MIN_DBH_CM, MAX_DBH_CM).all()),
            f"{int(dbh.notna().sum()):,} observed in [{dbh.min()}, {dbh.max()}] cm",
        )
    )
    checks.append(
        _check(
            "height range",
            bool(height.notna().any())
            and bool(height.dropna().between(MIN_HEIGHT_M, MAX_HEIGHT_M).all()),
            f"{int(height.notna().sum()):,} observed in "
            f"[{height.min()}, {height.max()}] m",
        )
    )
    checks.append(
        _check(
            "tree status domain",
            bool(trees["status"].notna().all())
            and bool(trees["status"].isin(STATUS_VALUES).all()),
            f"values {sorted(trees['status'].dropna().unique().tolist())}",
        )
    )
    checks.append(
        _check(
            "species group domain",
            bool(trees["species_group"].notna().all())
            and bool(trees["species_group"].isin(SPECIES_GROUPS).all()),
            f"values {sorted(trees['species_group'].dropna().unique().tolist())}",
        )
    )

    years = pd.to_numeric(visits["year"], errors="coerce")
    checks.append(
        _check(
            "visit year range",
            bool(years.notna().all())
            and bool(years.between(MIN_YEAR, MAX_YEAR).all()),
            f"{int(years.isna().sum()):,} null; observed "
            f"[{years.min()}, {years.max()}]",
        )
    )
    interval_years = pd.to_numeric(intervals["years"], errors="coerce")
    checks.append(
        _check(
            "interval length",
            bool(interval_years.notna().all())
            and bool(
                interval_years.between(
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
            bool(year0.notna().all())
            and bool(year1.notna().all())
            and bool(year0.between(MIN_YEAR, MAX_YEAR).all())
            and bool(year1.between(MIN_YEAR, MAX_YEAR).all()),
            f"year0 [{year0.min()}, {year0.max()}], year1 [{year1.min()}, {year1.max()}]",
        )
    )
    checks.append(
        _check(
            "interval arithmetic",
            bool(interval_years.notna().all())
            and bool(year0.notna().all())
            and bool(year1.notna().all())
            and bool((interval_years == (year1 - year0)).all()),
            "years must equal year1-year0",
        )
    )
    exposure = pd.to_numeric(intervals["exposure_years"], errors="coerce")
    known = pd.to_numeric(intervals["known"], errors="coerce")
    checks.append(
        _check(
            "interval exposure",
            bool(exposure.notna().all())
            and bool(known.notna().all())
            and bool((exposure == interval_years * known).all()),
            "exposure_years must equal years*known",
        )
    )
    checks.append(
        _check(
            "interval end status",
            bool(intervals["status_end"].notna().all())
            and bool(intervals["status_end"].isin(["live", "dead"]).all()),
            f"values {sorted(intervals['status_end'].dropna().unique().tolist())}",
        )
    )
    checks.append(
        _check(
            "interval flags",
            bool(intervals[["dead", "known"]].notna().all().all())
            and bool(intervals["dead"].isin([0, 1]).all())
            and bool(intervals["known"].isin([0, 1]).all())
            and bool(
                (
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
        explained = stats.get("explained_drops") or {}
        for name in ("sites", "visits", "trees"):
            denominator = int(selected.get(name, 0))
            numerator = int(output.get(f"{name}", 0))
            if denominator == 0:
                continue
            delta = abs(numerator - denominator) / denominator
            delta_rows = abs(numerator - denominator)
            explained_count = explained.get(name)
            explained_ok = (
                isinstance(explained_count, int) and delta_rows <= explained_count
            )
            checks.append(
                _check(
                    f"{source} {name} reconciliation",
                    delta <= 0.005 or explained_ok,
                    f"{delta:.4%} difference ({delta_rows:,} rows); "
                    f"explained_drops={explained_count}; reasons: "
                    + (", ".join(sorted(dropped)) if dropped else "none"),
                )
            )

    manifest_sources = set(manifest.get("sources", {}))
    output_sources = set(sites["source"].dropna().astype(str))
    checks.append(
        _check(
            "manifest source coverage",
            manifest_sources == output_sources,
            f"manifest {sorted(manifest_sources)}; outputs {sorted(output_sources)}",
        )
    )

    crosswalk_path = out / "species_crosswalk.csv"
    crosswalk = (
        pd.read_csv(crosswalk_path, dtype="string", low_memory=False)
        if crosswalk_path.is_file()
        else pd.DataFrame(columns=CROSSWALK_COLUMNS)
    )
    row_counts = {
        "sites.csv": len(sites),
        "visits.csv": len(visits),
        "trees.csv.gz": len(trees),
        "intervals.csv.gz": len(intervals),
        "species_crosswalk.csv": len(crosswalk),
    }
    outputs = manifest.get("outputs") or {}
    declared = set(outputs)
    missing_records = sorted(set(row_counts) - declared)
    checks.append(
        _check(
            "manifest outputs declared",
            bool(outputs) and not missing_records,
            "all five outputs declared"
            if not missing_records
            else f"missing records: {missing_records}",
        )
    )
    for name, record in outputs.items():
        path = out / name
        problems: list[str] = []
        if not path.is_file():
            problems.append("file missing")
        else:
            if int(record.get("bytes", -1)) != path.stat().st_size:
                problems.append(
                    f"bytes {record.get('bytes')} != {path.stat().st_size}"
                )
            if record.get("sha256") != sha256_file(path):
                problems.append("sha256 mismatch")
            expected_rows = row_counts.get(name)
            if expected_rows is not None and int(record.get("rows", -1)) != expected_rows:
                problems.append(
                    f"rows {record.get('rows')} != {expected_rows}"
                )
        checks.append(
            _check(
                f"output {name} manifest record",
                not problems,
                "hash, bytes, and rows match"
                if not problems
                else "; ".join(problems),
            )
        )

    checks.append(
        _check(
            "species crosswalk columns",
            tuple(crosswalk.columns) == CROSSWALK_COLUMNS,
            f"{len(crosswalk.columns)} columns",
        )
    )
    checks.append(
        _check(
            "species crosswalk non-empty",
            not crosswalk.empty,
            f"{len(crosswalk):,} rows",
        )
    )
    crosswalk_sources = set(crosswalk.get("source", pd.Series(dtype="string")).dropna())
    checks.append(
        _check(
            "species crosswalk source coverage",
            output_sources.issubset(crosswalk_sources),
            f"missing {sorted(output_sources - crosswalk_sources)}",
        )
    )
    groups = crosswalk.get("species_group", pd.Series(dtype="string"))
    checks.append(
        _check(
            "species crosswalk group domain",
            bool(groups.notna().all())
            and bool(groups.isin(SPECIES_GROUPS).all()),
            f"values {sorted(groups.dropna().unique().tolist())}",
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
