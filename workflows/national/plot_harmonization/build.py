"""Build the unified v0 FVS-CA calibration plot dataset.

Run from the repository root::

    python -m workflows.national.plot_harmonization.build \
        --out data/derived/fvsca_calibration/v0 \
        --sources faib,magplot,qc_pep,nl_fal

The builder streams each source's tree table into ``trees.csv.gz`` and writes
the matching intervals as it goes, so the full national tree table is never
held in memory.  It writes a manifest, QA note, species crosswalk, and then
runs the hard validation checks.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from . import faib, magplot, nl_fal, qc_pep, validate
from .common import (
    SourceTables,
    dump_json,
    ensure_gpkg,
    file_record,
    compute_intervals,
    finalize_sites,
    finalize_visits,
    git_version,
    mark_remeasured,
    open_gzip_writer,
    zip_member_record,
)
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
    TREE_COLUMNS,
    VISIT_COLUMNS,
)
from .species import build_crosswalk

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUT = REPO_ROOT / "data" / "derived" / "fvsca_calibration" / "v0"
DEFAULT_SOURCES = "faib,magplot,qc_pep,nl_fal"

RAW_INPUTS = {
    "faib_psp": [
        "bc_faib_ground_plots/psp/faib_header.csv.gz",
        "bc_faib_ground_plots/psp/faib_sample_byvisit.csv.gz",
        "bc_faib_ground_plots/psp/faib_plot_header.csv.gz",
        "bc_faib_ground_plots/psp/faib_tree_detail.csv.gz",
        "bc_faib_ground_plots/psp/faib_compiled_spcsmries_siteage.csv.gz",
    ],
    "faib_non_psp": [
        "bc_faib_ground_plots/non_psp/publish_province/faib_header.csv.gz",
        "bc_faib_ground_plots/non_psp/publish_province/faib_sample_byvisit.csv.gz",
        "bc_faib_ground_plots/non_psp/publish_province/faib_plot_header.csv.gz",
        "bc_faib_ground_plots/non_psp/publish_province/faib_tree_detail.csv.gz",
        "bc_faib_ground_plots/non_psp/publish_province/faib_compiled_spcsmries_siteage.csv.gz",
    ],
    "magplot": ["magplot/MAGPlot_latest_data_package.zip"],
    "magplot_wfs": [
        "magplot/wfs/sites_summary.csv.gz",
        "magplot/wfs/sk_trees_measure_num_0.csv.gz",
        "magplot/wfs/sk_trees_measure_num_1.csv.gz",
        "magplot/wfs/sk_trees_measure_num_2_8.csv.gz",
        "magplot/wfs/pei_trees_measure_num_0_11.csv.gz",
        "magplot/wfs/nl_trees_measure_num_0_2.csv.gz",
        "magplot/wfs/nl_trees_measure_num_3_9.csv.gz",
    ],
    "qc_pep": ["qc_pep/PEP_GPKG.zip"],
    "nl_fal": [
        "nl_fal_psp/mature.geojson.gz",
        "nl_fal_psp/immature.geojson.gz",
        "nl_fal_psp/remeasurement.csv.gz",
        "nl_fal_psp/remeasurement_crew_date.csv.gz",
        "nl_fal_psp/mature_trees_remeasurement.csv.gz",
        "nl_fal_psp/immature_trees_remeasurement.csv.gz",
        "nl_fal_psp/mature_trees_permanent.csv.gz",
        "nl_fal_psp/immature_trees_permanent.csv.gz",
    ],
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _resolve(path: Path) -> Path:
    return path if path.is_absolute() else (REPO_ROOT / path)


def _raw_root() -> Path:
    external = os.environ.get("CLEWS_EXTERNAL_DATA_ROOT")
    if external:
        return Path(external).expanduser().resolve() / "raw"
    return REPO_ROOT / "data" / "raw"


def _input_records(raw_root: Path, source: str) -> list[dict]:
    records = []
    for relative in RAW_INPUTS.get(source, []):
        path = raw_root / relative
        if path.is_file():
            records.append(file_record(path, relative_to=REPO_ROOT))
    return records


def _load_source(
    source: str,
    raw_root: Path,
    *,
    max_sites: int | None,
    work_dir: Path,
    qc_gpkg: Path | None,
    magplot_dir: Path | None,
    include_wfs: bool,
    zip_site_ids: set[str] | None,
) -> list[SourceTables]:
    if source == "faib":
        return [
            faib.load(raw_root, source="faib_psp", max_sites=max_sites),
            faib.load(raw_root, source="faib_non_psp", max_sites=max_sites),
        ]
    if source == "magplot":
        results = [
            magplot.load_zip(
                raw_root, max_sites=max_sites, data_dir=magplot_dir
            )
        ]
        if include_wfs:
            ids = zip_site_ids
            if ids is None:
                ids = magplot.all_site_ids(raw_root, data_dir=magplot_dir)
            results.append(
                magplot.load_wfs(
                    raw_root, zip_site_ids=ids, max_sites=max_sites
                )
            )
        return results
    if source == "qc_pep":
        gpkg = qc_gpkg
        if gpkg is None:
            gpkg = ensure_gpkg(
                raw_root / "qc_pep" / "PEP_GPKG.zip", work_dir, "PEP.gpkg"
            )
        return [qc_pep.load(raw_root, gpkg_path=gpkg, max_sites=max_sites)]
    if source == "nl_fal":
        return [nl_fal.load(raw_root, max_sites=max_sites)]
    raise ValueError(f"unknown source {source!r}")


#: Reasons that remove rows after a source's ``selected`` baseline is counted.
#: Range and sentinel reasons only null values and therefore never explain a
#: row-count difference.
_EXPLAINED_TREE_DROP_REASONS = (
    "tree_missing_site_or_tree_key",
    "tree_missing_visit_year",
    "tree_visit_not_found",
    "tree_duplicate_key",
)


def _explained_drops(stats: dict) -> dict[str, int]:
    dropped = stats.get("dropped") or {}
    trees = sum(int(dropped.get(reason, 0)) for reason in _EXPLAINED_TREE_DROP_REASONS)
    return {"sites": 0, "visits": 0, "trees": trees}


def _input_labels(
    source: str, *, include_wfs: bool, qc_gpkg: Path | None
) -> list[str]:
    if source == "faib":
        return ["faib_psp", "faib_non_psp"]
    if source == "magplot":
        return ["magplot"] + (["magplot_wfs"] if include_wfs else [])
    if source == "qc_pep" and qc_gpkg is not None:
        return []
    return [source]


def _source_counts(stats: dict) -> dict[str, int]:
    selected = stats.get("selected", {})
    output = stats.get("output", {})
    return {
        "sites_in": int(selected.get("sites", 0)),
        "visits_in": int(selected.get("visits", 0)),
        "trees_in": int(selected.get("trees", 0)),
        "sites_out": int(output.get("sites", 0)),
        "visits_out": int(output.get("visits", 0)),
        "trees_out": int(output.get("trees", 0)),
        "intervals_out": int(output.get("intervals", 0)),
    }


def _reconciliation(stats: dict) -> dict[str, float | None]:
    counts = _source_counts(stats)
    out: dict[str, float | None] = {}
    for name in ("sites", "visits", "trees"):
        denominator = counts[f"{name}_in"]
        numerator = counts[f"{name}_out"]
        out[name] = (
            None if denominator == 0 else (numerator - denominator) / denominator
        )
    return out


def _as_int(value) -> int:
    return 0 if pd.isna(value) else int(value)


def _render_qa(
    manifest: dict,
    *,
    site_rows: pd.DataFrame,
    visit_rows: pd.DataFrame,
    interval_site_counts: pd.DataFrame,
) -> str:
    lines: list[str] = []
    lines.append("# FVSCA calibration v0 — QA")
    lines.append("")
    lines.append(f"Built: {manifest['built_utc']}")
    lines.append(f"Code: `{manifest['code_version'].get('commit')}`")
    lines.append("")
    lines.append("## Source reconciliation")
    lines.append("")
    lines.append(
        "Input counts are the selected source rows after the optional "
        "`--max-sites` filter. `delta` is `(out - in) / in`."
    )
    lines.append("")
    lines.append(
        "| source | sites in | sites out | visits in | visits out | trees in | "
        "trees out | intervals | delta sites | delta visits | delta trees |"
    )
    lines.append(
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"
    )
    for source, stats in manifest["sources"].items():
        counts = _source_counts(stats)
        delta = _reconciliation(stats)
        lines.append(
            "| {source} | {sites_in} | {sites_out} | {visits_in} | {visits_out} | "
            "{trees_in} | {trees_out} | {intervals_out} | {ds} | {dv} | {dt} |".format(
                source=source,
                **counts,
                ds="n/a" if delta["sites"] is None else f"{delta['sites']:.4%}",
                dv="n/a" if delta["visits"] is None else f"{delta['visits']:.4%}",
                dt="n/a" if delta["trees"] is None else f"{delta['trees']:.4%}",
            )
        )
    lines.append("")

    lines.append("## Dropped rows and reasons")
    lines.append("")
    any_drops = False
    for source, stats in manifest["sources"].items():
        dropped = stats.get("dropped") or {}
        if not dropped:
            continue
        any_drops = True
        lines.append(f"- **{source}**: " + ", ".join(
            f"`{reason}` {count:,}" for reason, count in sorted(dropped.items())
        ))
    if not any_drops:
        lines.append("No rows were dropped beyond the source's own blank keys.")
    lines.append("")

    lines.append("## Unit and domain checks")
    lines.append("")
    lines.append(
        "| source | DBH min | DBH max | height min | height max | years | "
        "interval min | interval max |"
    )
    lines.append("|---|---:|---:|---:|---:|---|---:|---:|")
    for source, stats in manifest["sources"].items():
        units = stats.get("units", {})
        lines.append(
            "| {source} | {dbh_min} | {dbh_max} | {height_min} | {height_max} | "
            "{year_min}–{year_max} | {imin} | {imax} |".format(
                source=source,
                **{
                    "dbh_min": units.get("dbh_min", "n/a"),
                    "dbh_max": units.get("dbh_max", "n/a"),
                    "height_min": units.get("height_min", "n/a"),
                    "height_max": units.get("height_max", "n/a"),
                    "year_min": units.get("year_min", "n/a"),
                    "year_max": units.get("year_max", "n/a"),
                    "imin": units.get("interval_min", "n/a"),
                    "imax": units.get("interval_max", "n/a"),
                },
            )
        )
    lines.append("")

    lines.append("## Site index and age coverage")
    lines.append("")
    lines.append("| source | visits | site index visits | % | age visits | % |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for source, stats in manifest["sources"].items():
        cov = stats.get("coverage", {})
        visits_n = _as_int(cov.get("visits"))
        si_n = _as_int(cov.get("site_index_visits"))
        age_n = _as_int(cov.get("age_visits"))
        if visits_n == 0:
            continue
        lines.append(
            f"| {source} | {visits_n:,} | {si_n:,} | {si_n / visits_n:.1%} | "
            f"{age_n:,} | {age_n / visits_n:.1%} |"
        )
    lines.append("")

    lines.append("## Coverage (province × source)")
    lines.append("")
    lines.append(
        "| province | source | sites | remeasured sites | visits | first year | "
        "last year | intervals | trees |"
    )
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|")
    if not site_rows.empty and not visit_rows.empty:
        sites = site_rows[["source", "site_id", "province", "n_visits", "n_trees"]]
        visits = visit_rows.copy()
        visits["year"] = pd.to_numeric(visits["year"], errors="coerce")
        merged = visits.merge(
            sites[["source", "site_id", "province"]],
            on=["source", "site_id"],
            how="left",
        )
        grouped = merged.groupby(["province", "source"], dropna=False).agg(
            visits=("visit_id", "size"),
            first_year=("year", "min"),
            last_year=("year", "max"),
        )
        site_summary = sites.groupby(["province", "source"], dropna=False).agg(
            sites=("site_id", "nunique"),
            remeasured=("n_visits", lambda s: int((s > 1).sum())),
            trees=("n_trees", "sum"),
        )
        if not interval_site_counts.empty:
            interval_summary = (
                interval_site_counts.groupby(
                    ["province", "source"], dropna=False
                )["intervals"]
                .sum()
                .rename("intervals")
            )
        else:
            interval_summary = None
        combined = grouped.join(site_summary, how="outer")
        if interval_summary is not None:
            combined = combined.join(interval_summary, how="outer")
        else:
            combined["intervals"] = 0
        for (province, source), row in combined.sort_index().iterrows():
            lines.append(
                f"| {province} | {source} | {_as_int(row.get('sites'))} | "
                f"{_as_int(row.get('remeasured'))} | {_as_int(row.get('visits'))} | "
                f"{'' if pd.isna(row.get('first_year')) else int(row.get('first_year'))} | "
                f"{'' if pd.isna(row.get('last_year')) else int(row.get('last_year'))} | "
                f"{_as_int(row.get('intervals'))} | {_as_int(row.get('trees'))} |"
            )
    lines.append("")

    lines.append("## Known issues")
    lines.append("")
    for note in manifest.get("notes", []):
        lines.append(f"- {note}")
    lines.append("")
    return "\n".join(lines) + "\n"


def _write_outputs(
    out_dir: Path,
    *,
    site_rows: list[pd.DataFrame],
    visit_rows: list[pd.DataFrame],
    species_observations: list[pd.DataFrame],
    interval_site_counts: list[pd.DataFrame],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    sites = pd.concat(site_rows, ignore_index=True) if site_rows else pd.DataFrame(
        columns=SITE_COLUMNS
    )
    visits = pd.concat(visit_rows, ignore_index=True) if visit_rows else pd.DataFrame(
        columns=VISIT_COLUMNS
    )
    for column in SITE_COLUMNS:
        if column not in sites:
            sites[column] = pd.NA
    for column in VISIT_COLUMNS:
        if column not in visits:
            visits[column] = pd.NA
    sites = sites[list(SITE_COLUMNS)].sort_values(
        ["source", "site_id"], kind="mergesort"
    ).reset_index(drop=True)
    visits = visits[list(VISIT_COLUMNS)].sort_values(
        ["source", "site_id", "visit_id"], kind="mergesort"
    ).reset_index(drop=True)
    sites.to_csv(out_dir / "sites.csv", index=False)
    visits.to_csv(out_dir / "visits.csv", index=False)

    observations = (
        pd.concat(species_observations, ignore_index=True)
        if species_observations
        else pd.DataFrame(columns=["source", "species_code", "n"])
    )
    crosswalk = build_crosswalk(observations)
    crosswalk = crosswalk[list(CROSSWALK_COLUMNS)]
    crosswalk.to_csv(out_dir / "species_crosswalk.csv", index=False)

    coverage = (
        pd.concat(interval_site_counts, ignore_index=True)
        if interval_site_counts
        else pd.DataFrame(columns=["source", "site_id", "intervals"])
    )
    coverage = coverage.merge(
        sites[["source", "site_id", "province"]],
        on=["source", "site_id"],
        how="left",
    )
    return sites, visits, coverage


def build(
    *,
    out: Path,
    sources: list[str],
    max_sites: int | None = None,
    work_dir: Path | None = None,
    qc_gpkg: Path | None = None,
    magplot_dir: Path | None = None,
    include_wfs: bool = True,
    run_validation: bool = True,
) -> int:
    out = _resolve(out)
    out.mkdir(parents=True, exist_ok=True)
    raw_root = _raw_root()
    work_dir = work_dir or Path(tempfile.gettempdir()) / "plot_harmonization"
    work_dir.mkdir(parents=True, exist_ok=True)

    built_utc = _utc_now()
    code_version = git_version(REPO_ROOT)
    manifest: dict = {
        "product": "fvsca_calibration_v0",
        "built_utc": built_utc,
        "code_version": code_version,
        "parameters": {
            "min_interval_years": MIN_INTERVAL_YEARS,
            "max_interval_years": MAX_INTERVAL_YEARS,
            "min_year": MIN_YEAR,
            "max_year": MAX_YEAR,
            "min_dbh_cm": MIN_DBH_CM,
            "max_dbh_cm": MAX_DBH_CM,
            "min_height_m": MIN_HEIGHT_M,
            "max_height_m": MAX_HEIGHT_M,
            "max_sites": max_sites,
            "include_wfs": include_wfs,
            "validation": run_validation,
        },
        "inputs": {},
        "sources": {},
        "totals": {},
        "outputs": {},
        "notes": [],
    }

    for source in sources:
        labels = _input_labels(
            source, include_wfs=include_wfs, qc_gpkg=qc_gpkg
        )
        for label in labels:
            if label in RAW_INPUTS:
                manifest["inputs"][label] = _input_records(raw_root, label)
    if "qc_pep" in sources:
        if qc_gpkg is not None:
            gpkg_path = Path(qc_gpkg)
            if gpkg_path.is_file():
                manifest["inputs"]["qc_pep_gpkg"] = [
                    file_record(gpkg_path, relative_to=REPO_ROOT)
                ]
        else:
            member = raw_root / "qc_pep" / "PEP_GPKG.zip"
            if member.is_file():
                manifest["inputs"].setdefault("qc_pep_member", []).append(
                    zip_member_record(member, "PEP.gpkg")
                )

    tree_path = out / "trees.csv.gz"
    interval_path = out / "intervals.csv.gz"
    site_rows: list[pd.DataFrame] = []
    visit_rows: list[pd.DataFrame] = []
    species_observations: list[pd.DataFrame] = []
    interval_site_counts: list[pd.DataFrame] = []
    total_trees = 0
    total_intervals = 0
    zip_site_ids: set[str] | None = None

    with open_gzip_writer(tree_path) as tree_handle, open_gzip_writer(
        interval_path
    ) as interval_handle:
        tree_header = True
        interval_header = True
        for source in sources:
            results = _load_source(
                source,
                raw_root,
                max_sites=max_sites,
                work_dir=work_dir,
                qc_gpkg=qc_gpkg,
                magplot_dir=magplot_dir,
                include_wfs=include_wfs,
                zip_site_ids=zip_site_ids,
            )
            for result in results:
                label = result.source
                stats = result.stats
                print(
                    f"[build] {label}: {len(result.sites):,} sites, "
                    f"{len(result.visits):,} visits, {len(result.trees):,} trees",
                    file=sys.stderr,
                    flush=True,
                )
                stats["source"] = label
                if label == "magplot":
                    zip_site_ids = set(result.sites["site_id"].astype(str))
                if result.trees.empty:
                    stats["output"] = {
                        "sites": int(len(result.sites)),
                        "visits": int(len(result.visits)),
                        "trees": 0,
                        "intervals": 0,
                    }
                    stats["explained_drops"] = _explained_drops(stats)
                    site_rows.append(
                        finalize_sites(result.sites, result.visits, result.trees)
                    )
                    visit_rows.append(
                        finalize_visits(result.visits, result.trees)
                    )
                    manifest["sources"][label] = stats
                    manifest["notes"].extend(
                        [f"{label}: {note}" for note in result.notes]
                    )
                    continue

                result.trees["is_remeasured"] = mark_remeasured(result.trees)
                intervals = compute_intervals(result.trees)
                sites_final = finalize_sites(
                    result.sites, result.visits, result.trees
                )
                visits_final = finalize_visits(result.visits, result.trees)
                stats["output"] = {
                    "sites": int(len(sites_final)),
                    "visits": int(len(visits_final)),
                    "trees": int(len(result.trees)),
                    "intervals": int(len(intervals)),
                }
                stats["explained_drops"] = _explained_drops(stats)
                stats["coverage"] = {
                    "visits": int(len(visits_final)),
                    "site_index_visits": int(visits_final["site_index"].notna().sum()),
                    "age_visits": int(visits_final["age"].notna().sum()),
                }
                stats["units"] = {
                    "dbh_min": _safe_min(result.trees["dbh_cm"]),
                    "dbh_max": _safe_max(result.trees["dbh_cm"]),
                    "height_min": _safe_min(result.trees["height_m"]),
                    "height_max": _safe_max(result.trees["height_m"]),
                    "year_min": _safe_min(result.trees["_year"]),
                    "year_max": _safe_max(result.trees["_year"]),
                    "interval_min": _safe_min(intervals["years"]),
                    "interval_max": _safe_max(intervals["years"]),
                }
                site_rows.append(sites_final)
                visit_rows.append(visits_final)
                species = (
                    result.trees.groupby(
                        ["source", "species_code"], dropna=False
                    )
                    .size()
                    .reset_index(name="n")
                )
                species_observations.append(species)
                if not intervals.empty:
                    counts = (
                        intervals.groupby(["source", "site_id"], sort=False)
                        .size()
                        .rename("intervals")
                        .reset_index()
                    )
                    interval_site_counts.append(counts)

                result.trees[list(TREE_COLUMNS)].to_csv(
                    tree_handle,
                    index=False,
                    header=tree_header,
                )
                tree_header = False
                intervals[list(INTERVAL_COLUMNS)].to_csv(
                    interval_handle,
                    index=False,
                    header=interval_header,
                )
                interval_header = False
                total_trees += len(result.trees)
                total_intervals += len(intervals)
                manifest["sources"][label] = stats
                manifest["notes"].extend(
                    [f"{label}: {note}" for note in result.notes]
                )
                del result, intervals, sites_final, visits_final

    sites, visits, interval_coverage = _write_outputs(
        out,
        site_rows=site_rows,
        visit_rows=visit_rows,
        species_observations=species_observations,
        interval_site_counts=interval_site_counts,
    )

    manifest["totals"] = {
        "sites": int(len(sites)),
        "visits": int(len(visits)),
        "trees": int(total_trees),
        "intervals": int(total_intervals),
        "sources": len(manifest["sources"]),
    }
    for name, rows in (
        ("sites.csv", len(sites)),
        ("visits.csv", len(visits)),
        ("trees.csv.gz", total_trees),
        ("intervals.csv.gz", total_intervals),
        ("species_crosswalk.csv", int(len(build_crosswalk(
            pd.concat(species_observations, ignore_index=True)
            if species_observations
            else pd.DataFrame(columns=["source", "species_code", "n"])
        )))),
    ):
        path = out / name
        record = file_record(path, relative_to=out)
        record["rows"] = int(rows)
        manifest["outputs"][name] = record

    # Reconciliation notes are generated even when a source is exactly within
    # tolerance; the validation step enforces the 0.5% rule.
    for label, stats in manifest["sources"].items():
        delta = _reconciliation(stats)
        stats["reconciliation"] = delta

    qa = _render_qa(
        manifest,
        site_rows=sites,
        visit_rows=visits,
        interval_site_counts=interval_coverage,
    )
    out.joinpath("QA.md").write_text(qa, encoding="utf-8")
    dump_json(out / "manifest.json", manifest)

    if run_validation:
        checks = validate.run_checks(out)
        validation = {
            "checked_utc": _utc_now(),
            "checks": [check.as_dict() for check in checks],
            "passed": all(check.status == "PASS" for check in checks),
        }
        dump_json(out / "validation.json", validation)
        with out.joinpath("QA.md").open("a", encoding="utf-8") as handle:
            handle.write("\n" + validate.render_markdown(checks) + "\n")
        if not validation["passed"]:
            return 1
    return 0


def _safe_min(series: pd.Series):
    numeric = pd.to_numeric(series, errors="coerce").dropna()
    return None if numeric.empty else float(numeric.min())


def _safe_max(series: pd.Series):
    numeric = pd.to_numeric(series, errors="coerce").dropna()
    return None if numeric.empty else float(numeric.max())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--sources", default=DEFAULT_SOURCES)
    parser.add_argument("--max-sites", type=int, default=None)
    parser.add_argument(
        "--sample",
        type=int,
        default=None,
        help="Alias for --max-sites, applied to each selected source.",
    )
    parser.add_argument("--work-dir", type=Path, default=None)
    parser.add_argument("--qc-gpkg", type=Path, default=None)
    parser.add_argument("--magplot-dir", type=Path, default=None)
    parser.add_argument("--no-wfs", action="store_true")
    parser.add_argument("--no-validate", action="store_true")
    args = parser.parse_args(argv)
    sources = [part.strip() for part in args.sources.split(",") if part.strip()]
    allowed = {"faib", "magplot", "qc_pep", "nl_fal"}
    unknown = sorted(set(sources) - allowed)
    if unknown:
        parser.error(f"unknown sources: {', '.join(unknown)}")
    max_sites = args.max_sites if args.max_sites is not None else args.sample
    return build(
        out=args.out,
        sources=sources,
        max_sites=max_sites,
        work_dir=args.work_dir,
        qc_gpkg=args.qc_gpkg,
        magplot_dir=args.magplot_dir,
        include_wfs=not args.no_wfs,
        run_validation=not args.no_validate,
    )


if __name__ == "__main__":
    sys.exit(main())
