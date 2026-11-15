"""Focused regression tests for the national plot harmonization package.

These tests use synthetic frames and tiny on-disk packages.  They do not read
the archived raw corpora, so they run quickly and isolate the review fixes.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

from workflows.national.plot_harmonization import magplot, validate
from workflows.national.plot_harmonization.common import (
    compute_intervals,
    file_record,
    map_status,
)
from workflows.national.plot_harmonization.qc_pep import plot_area_ha
from workflows.national.plot_harmonization.schema import (
    CROSSWALK_COLUMNS,
    INTERVAL_COLUMNS,
    SITE_COLUMNS,
    TREE_COLUMNS,
    VISIT_COLUMNS,
)


def test_qc_plot_area_is_hectares() -> None:
    """25 stems/ha is one stem per 0.04 ha, not 400 m2."""
    area = plot_area_ha(pd.Series([25.0, 100.0])).tolist()
    assert area == [0.04, 0.01]


def test_map_status_strips_float_promotion() -> None:
    values = pd.Series([10.0, 0.0, None], dtype="Float64")
    mapped = map_status(values, {"10": "live", "0": "dead"})
    assert mapped.tolist() == ["live", "dead", "missing"]


def _write_magplot_zip(raw_root: Path) -> None:
    """Write a two-plot, two-visit ZIP package under raw_root/magplot."""
    base = raw_root / "magplot"
    base.mkdir(parents=True, exist_ok=True)
    sites = pd.DataFrame(
        {
            "magp_site_id": ["S1"],
            "latitude": [50.0],
            "longitude": [-120.0],
            "province": ["BC"],
            "gpp_type": ["1"],
        }
    )
    header = pd.DataFrame(
        {
            "magp_site_id": ["S1", "S1", "S1", "S1"],
            "plot_id": ["P1", "P1", "P2", "P2"],
            "plot_type_id": ["1"] * 4,
            "meas_num": ["1", "2", "2", "10"],
            "meas_year": [2000, 2005, 2000, 2005],
            "meas_plot_size": [0.04] * 4,
        }
    )
    rows = []
    for plot_id, meas_num in (("P1", "1"), ("P1", "2"), ("P2", "2"), ("P2", "10")):
        rows.append(
            {
                "magp_site_id": "S1",
                "plot_id": plot_id,
                "plot_type_id": "1",
                "meas_num": meas_num,
                "tree_num": "1",
                "tree_id": f"{plot_id}-1",
                "species_gsv": "PICE.GLAUCA",
                "dbh": 10.0,
                "height": 15.0,
                "crown_class": "1",
                "stem_ha": 100.0,
                "tree_status": "LS",
                "stem_cond": "1",
            }
        )
    trees = pd.DataFrame(rows)
    with zipfile.ZipFile(
        base / "MAGPlot_latest_data_package.zip", "w"
    ) as archive:
        for name, frame in (
            ("magp_sites.csv", sites),
            ("magp_tree_header.csv", header),
            ("magp_trees.csv", trees),
        ):
            archive.writestr(name, frame.to_csv(index=False))


def test_magplot_zip_multi_plot_visits_keep_both_intervals(
    tmp_path: Path,
) -> None:
    """Two plots x two visits must not lose intervals to a site-level order."""
    _write_magplot_zip(tmp_path)
    result = magplot.load_zip(tmp_path)
    intervals = compute_intervals(result.trees)

    assert len(result.visits) == 4
    assert len(intervals) == 2
    assert set(intervals["tree_id"]) == {"P1|1", "P2|1"}
    assert set(intervals["visit_id0"]) == {"P1|1", "P2|2"}
    assert set(intervals["visit_id1"]) == {"P1|2", "P2|10"}
    assert intervals["years"].tolist() == [5, 5]


_WFS_COLUMNS = [
    "magp_site_id",
    "plot_id",
    "plot_type_id",
    "meas_year",
    "meas_month",
    "meas_plot_size",
    "tree_num",
    "tree_id",
    "species_gsv",
    "dbh",
    "height",
    "crown_class",
    "stem_ha",
    "tree_status",
    "stem_cond",
]


def _write_wfs_package(raw_root: Path) -> None:
    base = raw_root / "magplot" / "wfs"
    base.mkdir(parents=True, exist_ok=True)
    sites = pd.DataFrame(
        {
            "magp_site_id": ["W1"],
            "province": ["SK"],
            "gpp_type": ["1"],
            "WKT": ["POINT (1000000 500000)"],
        }
    )
    sites.to_csv(base / "sites_summary.csv.gz", index=False, compression="gzip")

    rows = []
    for plot_id in ("P1", "P2"):
        for year in (2010, 2015):
            rows.append(
                {
                    "magp_site_id": "W1",
                    "plot_id": plot_id,
                    "plot_type_id": "1",
                    "meas_year": year,
                    "meas_month": 6,
                    "meas_plot_size": 0.04,
                    "tree_num": 1,
                    "tree_id": f"{plot_id}-1",
                    "species_gsv": "PICE.GLAUCA",
                    "dbh": 10.0,
                    "height": 15.0,
                    "crown_class": "1",
                    "stem_ha": 100.0,
                    "tree_status": "LS",
                    "stem_cond": "1",
                }
            )
    data = pd.DataFrame(rows, columns=_WFS_COLUMNS)
    layers = [
        "sk_trees_measure_num_0.csv.gz",
        "sk_trees_measure_num_1.csv.gz",
        "sk_trees_measure_num_2_8.csv.gz",
        "pei_trees_measure_num_0_11.csv.gz",
        "nl_trees_measure_num_0_2.csv.gz",
        "nl_trees_measure_num_3_9.csv.gz",
    ]
    for index, name in enumerate(layers):
        frame = data if index == 0 else data.iloc[0:0]
        frame.to_csv(base / name, index=False, compression="gzip")


def test_magplot_wfs_multi_plot_visits_keep_both_intervals(
    tmp_path: Path,
) -> None:
    """WFS visits must be ordered per plot, not per site and year."""
    _write_wfs_package(tmp_path)
    result = magplot.load_wfs(tmp_path, zip_site_ids=set())
    intervals = compute_intervals(result.trees)

    assert len(result.visits) == 4
    assert len(intervals) == 2
    assert set(intervals["tree_id"]) == {"P1|1", "P2|1"}
    assert intervals["years"].tolist() == [5, 5]


def test_intervals_keep_dead_endpoint_without_dbh() -> None:
    """A dead end is valid even when its DBH is null; a live end is not."""
    dead_end = pd.DataFrame(
        {
            "source": ["s", "s"],
            "site_id": ["S1", "S1"],
            "tree_id": ["T1", "T1"],
            "visit_id": ["V1", "V2"],
            "_visit_order": [1, 2],
            "_year": [2000, 2005],
            "dbh_cm": [10.0, np.nan],
            "status": ["live", "dead"],
        }
    )
    intervals = compute_intervals(dead_end)
    assert len(intervals) == 1
    assert intervals.loc[0, "status_end"] == "dead"
    assert pd.isna(intervals.loc[0, "dbh1_cm"])
    assert intervals.loc[0, "dbh0_cm"] == 10.0
    assert intervals.loc[0, "visit_id0"] == "V1"
    assert intervals.loc[0, "visit_id1"] == "V2"

    live_end = dead_end.copy()
    live_end["status"] = ["live", "live"]
    assert compute_intervals(live_end).empty


def _minimal_outputs(
    root: Path,
    *,
    selected_trees: int = 100,
    empty_visits: bool = False,
    null_visit_year: bool = False,
) -> dict:
    """Write a tiny internally consistent output directory and manifest."""
    root.mkdir(parents=True, exist_ok=True)
    sites = pd.DataFrame(
        [
            {
                "source": "test_src",
                "site_id": "S1",
                "province": "BC",
                "eco_zone": "ESSF",
                "eco_subzone": "mc",
                "eco_variant": pd.NA,
                "site_index": 20.0,
                "site_index_species": "AB",
                "plot_design": "test",
                "plot_area_ha": 0.04,
                "baf": pd.NA,
                "lat": 50.0,
                "lon": -120.0,
                "coord_accuracy": "gps",
                "first_year": 2000,
                "last_year": 2005,
                "n_visits": 2,
                "n_trees": 100,
            }
        ],
        columns=SITE_COLUMNS,
    )
    visits = pd.DataFrame(
        [
            {
                "source": "test_src",
                "site_id": "S1",
                "visit_id": "V1",
                "year": 2000,
                "n_trees": 50,
                "ba_m2ha": 10.0,
                "tph": 500.0,
                "qmd_cm": 15.0,
                "top_height_m": 20.0,
                "volume_m3ha": pd.NA,
                "site_index": 20.0,
                "age": 80.0,
            },
            {
                "source": "test_src",
                "site_id": "S1",
                "visit_id": "V2",
                "year": 2005,
                "n_trees": 50,
                "ba_m2ha": 11.0,
                "tph": 500.0,
                "qmd_cm": 16.0,
                "top_height_m": 22.0,
                "volume_m3ha": pd.NA,
                "site_index": 20.0,
                "age": 85.0,
            },
        ],
        columns=VISIT_COLUMNS,
    )
    if null_visit_year:
        visits.loc[visits["visit_id"].eq("V2"), "year"] = np.nan
    if empty_visits:
        visits = visits.iloc[0:0]

    tree_rows = []
    interval_rows = []
    for visit_id in ("V1", "V2"):
        for tree_number in range(50):
            tree_id = f"T{tree_number}"
            tree_rows.append(
                {
                    "source": "test_src",
                    "site_id": "S1",
                    "visit_id": visit_id,
                    "tree_id": tree_id,
                    "species_code": "AB",
                    "species_group": "conifer",
                    "dbh_cm": 10.0,
                    "height_m": 15.0,
                    "status": "live",
                    "crown_class": "1",
                    "expansion_factor": 100.0,
                    "damage_code": pd.NA,
                    "is_remeasured": True,
                }
            )
    for tree_number in range(50):
        interval_rows.append(
            {
                "source": "test_src",
                "site_id": "S1",
                "tree_id": f"T{tree_number}",
                "visit_id0": "V1",
                "visit_id1": "V2",
                "year0": 2000,
                "year1": 2005,
                "years": 5,
                "dbh0_cm": 10.0,
                "dbh1_cm": 10.0,
                "status_end": "live",
                "dead": 0,
                "known": 1,
                "exposure_years": 5.0,
            }
        )
    trees = pd.DataFrame(tree_rows, columns=TREE_COLUMNS)
    intervals = pd.DataFrame(interval_rows, columns=INTERVAL_COLUMNS)
    crosswalk = pd.DataFrame(
        [
            {
                "source": "test_src",
                "species_code": "AB",
                "species_group": "conifer",
                "notes": "test",
                "fvs_jsp_code_todo": pd.NA,
            }
        ],
        columns=CROSSWALK_COLUMNS,
    )

    sites.to_csv(root / "sites.csv", index=False)
    visits.to_csv(root / "visits.csv", index=False)
    trees.to_csv(root / "trees.csv.gz", index=False, compression="gzip")
    intervals.to_csv(root / "intervals.csv.gz", index=False, compression="gzip")
    crosswalk.to_csv(root / "species_crosswalk.csv", index=False)

    outputs = {}
    for name, rows in (
        ("sites.csv", len(sites)),
        ("visits.csv", len(visits)),
        ("trees.csv.gz", len(trees)),
        ("intervals.csv.gz", len(intervals)),
        ("species_crosswalk.csv", len(crosswalk)),
    ):
        record = file_record(root / name, relative_to=root)
        record["rows"] = int(rows)
        outputs[name] = record

    return {
        "product": "test",
        "sources": {
            "test_src": {
                "selected": {"sites": 1, "visits": 2, "trees": selected_trees},
                "output": {
                    "sites": 1,
                    "visits": len(visits),
                    "trees": 100,
                    "intervals": 50,
                },
                "dropped": {},
                "explained_drops": {"sites": 0, "visits": 0, "trees": 0},
            }
        },
        "outputs": outputs,
    }


def _write_manifest(root: Path, manifest: dict) -> None:
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def _find(checks: list[validate.Check], name: str) -> validate.Check:
    return next(check for check in checks if check.name == name)


def test_validate_rejects_explained_source_delta(tmp_path: Path) -> None:
    """A 1% delta with no explained drops must fail reconciliation."""
    manifest = _minimal_outputs(tmp_path, selected_trees=101)
    _write_manifest(tmp_path, manifest)
    checks = validate.run_checks(tmp_path)
    assert _find(checks, "test_src trees reconciliation").status == "FAIL"


def test_validate_null_only_reason_does_not_explain_delta(
    tmp_path: Path,
) -> None:
    """A DBH range reason nulls values and cannot cover a row-count delta."""
    manifest = _minimal_outputs(tmp_path, selected_trees=101)
    manifest["sources"]["test_src"]["dropped"] = {"tree_dbh_out_of_range": 1}
    _write_manifest(tmp_path, manifest)
    checks = validate.run_checks(tmp_path)
    assert _find(checks, "test_src trees reconciliation").status == "FAIL"


def test_validate_accepts_explained_source_delta(tmp_path: Path) -> None:
    """A 1% delta covered by exact explained drops may pass."""
    manifest = _minimal_outputs(tmp_path, selected_trees=101)
    manifest["sources"]["test_src"]["explained_drops"]["trees"] = 1
    _write_manifest(tmp_path, manifest)
    checks = validate.run_checks(tmp_path)
    assert _find(checks, "test_src trees reconciliation").status == "PASS"


def test_validate_requires_manifest(tmp_path: Path) -> None:
    _minimal_outputs(tmp_path)
    checks = validate.run_checks(tmp_path)
    assert len(checks) == 1
    assert checks[0].name == "manifest present"
    assert checks[0].status == "FAIL"


def test_validate_rejects_empty_required_frame(tmp_path: Path) -> None:
    manifest = _minimal_outputs(tmp_path, empty_visits=True)
    _write_manifest(tmp_path, manifest)
    checks = validate.run_checks(tmp_path)
    assert _find(checks, "non-empty visits").status == "FAIL"


def test_validate_rejects_null_visit_year(tmp_path: Path) -> None:
    manifest = _minimal_outputs(tmp_path, null_visit_year=True)
    _write_manifest(tmp_path, manifest)
    checks = validate.run_checks(tmp_path)
    assert _find(checks, "visit year range").status == "FAIL"


def test_validate_detects_output_hash_mismatch(tmp_path: Path) -> None:
    manifest = _minimal_outputs(tmp_path)
    _write_manifest(tmp_path, manifest)
    with (tmp_path / "sites.csv").open("a", encoding="utf-8") as handle:
        handle.write("\n")
    checks = validate.run_checks(tmp_path)
    assert _find(checks, "output sites.csv manifest record").status == "FAIL"


def test_validate_requires_species_crosswalk(tmp_path: Path) -> None:
    manifest = _minimal_outputs(tmp_path)
    (tmp_path / "species_crosswalk.csv").unlink()
    _write_manifest(tmp_path, manifest)
    checks = validate.run_checks(tmp_path)
    assert _find(checks, "species crosswalk non-empty").status == "FAIL"
    assert _find(checks, "species crosswalk source coverage").status == "FAIL"
