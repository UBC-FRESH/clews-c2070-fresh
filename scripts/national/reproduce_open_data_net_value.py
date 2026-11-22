#!/usr/bin/env python3
"""Reproduce the BC open-data net-value headline from the published bundle.

The bundle at ``data/derived/national/harvest_basis/open_data_net_value/
model_inputs/`` publishes the exact derived inputs and their SHA-256 manifest.
This script validates every hash, runs the committed model with
``inputs_dir`` set to the bundle, and fails closed unless the corrected
headline and byte-identical core outputs are reproduced.

Usage
-----
    .venv/bin/python scripts/national/reproduce_open_data_net_value.py
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from workflows.national.harvest import bc_open_cost_model as cost_model
from workflows.national.harvest import open_data_net_value as net_value

MODEL_DIR = REPO_ROOT / "data/derived/national/harvest_basis/open_data_net_value"
DEFAULT_BUNDLE_DIR = MODEL_DIR / "model_inputs"
MANIFEST_JSON = "input_manifest.json"

REQUIRED_SOURCE_COLUMNS = (
    "source_id",
    "title",
    "publisher",
    "url",
    "retrieved_utc",
    "licence_terms",
    "raw_redistributed",
    "citation",
)
BUNDLE_DATA_FILES = (
    "bc_price_panel_used.csv",
    "bc_cost_inputs_used.csv",
    "bc_cost_parameters_used.csv",
    "silviculture_inputs_used.csv",
    "qc_reference_used.csv",
    "zone_region_mapping.csv",
    "sources.csv",
)
FORBIDDEN_SUFFIXES = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".zip"}
CORE_OUTPUTS = (
    "bc_open_net_value.csv",
    "bc_open_net_value_summary.csv",
    "bc_open_net_value_sensitivity.csv",
    "qc_open_reference.csv",
)


class ReproductionError(ValueError):
    """A hash, input contract or headline reproduction failed."""


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ReproductionError(message)


def _load_manifest(bundle_dir: Path) -> dict[str, Any]:
    manifest_path = bundle_dir / MANIFEST_JSON
    _require(manifest_path.is_file(), f"missing bundle manifest: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    _require(
        manifest.get("labels", {}).get("raw_source_redistribution") is False,
        "manifest does not set raw_source_redistribution=false",
    )
    _require(
        manifest.get("labels", {}).get("input_publication") == "derived_model_inputs",
        "manifest does not set input_publication=derived_model_inputs",
    )
    return manifest


def verify_files(bundle_dir: Path, manifest: dict[str, Any]) -> None:
    listed = {item["path"]: item for item in manifest["files"]}
    _require(
        set(listed) == set(BUNDLE_DATA_FILES) | {"README.md", "RUN.md"},
        f"manifest file set is unexpected: {sorted(listed)}",
    )
    for name, item in listed.items():
        path = bundle_dir / name
        _require(path.is_file(), f"missing bundle file: {name}")
        _require(
            sha256_file(path) == item["sha256"],
            f"sha256 mismatch for {name}: {sha256_file(path)} vs {item['sha256']}",
        )
        _require(
            path.stat().st_size == item["bytes"],
            f"byte-size mismatch for {name}",
        )
        if path.suffix == ".csv":
            _require(
                len(_read_csv(path)) == item["rows"],
                f"row-count mismatch for {name}",
            )
    actual = {
        path.name
        for path in bundle_dir.iterdir()
        if path.is_file() and path.name != MANIFEST_JSON
    }
    _require(
        actual == set(listed),
        f"unlisted or missing bundle files: listed={sorted(listed)} "
        f"actual={sorted(actual)}",
    )
    forbidden = [
        path.name
        for path in bundle_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in FORBIDDEN_SUFFIXES
    ]
    _require(not forbidden, f"raw/forbidden files in bundle: {forbidden}")


def verify_sources(bundle_dir: Path) -> dict[str, dict[str, str]]:
    rows = _read_csv(bundle_dir / "sources.csv")
    _require(rows, "sources.csv is empty")
    missing = set(REQUIRED_SOURCE_COLUMNS) - set(rows[0])
    _require(not missing, f"sources.csv missing columns: {sorted(missing)}")
    by_id: dict[str, dict[str, str]] = {}
    for row in rows:
        _require(
            row["source_id"] not in by_id, f"duplicate source_id {row['source_id']}"
        )
        _require(
            row["raw_redistributed"] == "false", f"{row['source_id']} redistributes raw"
        )
        _require(
            row["url"].startswith("https://"), f"{row['source_id']} has no HTTPS URL"
        )
        _require(row["retrieved_utc"], f"{row['source_id']} has no retrieval date")
        _require(row["citation"], f"{row['source_id']} has no citation")
        _require(row["licence_terms"], f"{row['source_id']} has no licence terms")
        by_id[row["source_id"]] = row
    return by_id


def verify_price_panel(bundle_dir: Path, source_ids: set[str]) -> None:
    rows = _read_csv(bundle_dir / net_value.BUNDLE_PRICE_PANEL_CSV)
    counts = {
        "primary_3_month_2025-12": {"rows": 94, "used": 89, "excluded": 5},
        "sensitivity_1_month_2025-12": {"rows": 96, "used": 91, "excluded": 5},
    }
    for role, expected in counts.items():
        panel = [row for row in rows if row["panel_role"] == role]
        _require(len(panel) == expected["rows"], f"{role} has {len(panel)} rows")
        used = [row for row in panel if row["used_in_model"] == "true"]
        _require(len(used) == expected["used"], f"{role} has {len(used)} used rows")
        _require(
            len(panel) - len(used) == expected["excluded"],
            f"{role} excluded-row count changed",
        )
        _require(
            all(row["price_plausibility"] == "ok" for row in used),
            f"{role} uses an implausible price cell",
        )
        _require(
            all(row["outlier_flag"] == "false" for row in used),
            f"{role} marks a flagged cell as used",
        )
        _require(
            all(row["report_id"] in source_ids for row in panel),
            f"{role} cites a report missing from sources.csv",
        )
        _require(
            all(row["source_url"] and row["source_sha256"] for row in panel),
            f"{role} is missing report URL or hash",
        )
        _require(
            all(
                row["exclusion_reason"]
                for row in panel
                if row["used_in_model"] == "false"
            ),
            f"{role} excluded rows lack a reason",
        )

    primary = net_value.load_price_panels(bundle_dir / net_value.BUNDLE_PRICE_PANEL_CSV)
    _require(
        round(primary["Coast"].base_cad_m3, 2) == 122.59,
        "primary Coast price is not 122.59",
    )
    _require(
        round(primary["Interior"].base_cad_m3, 2) == 113.38,
        "primary Interior price is not 113.38",
    )
    _require(
        primary["Interior"].aggregates_excluded == 5, "Interior aggregate count changed"
    )
    sensitivity = net_value.load_price_panels(
        bundle_dir / net_value.BUNDLE_PRICE_PANEL_CSV,
        report_basis=net_value.SENSITIVITY_PRICE_BASIS,
        period_end=net_value.SENSITIVITY_PRICE_PERIOD_END,
    )
    _require(
        sensitivity["Coast"].report_basis == "1_month",
        "sensitivity panel is not 1_month",
    )


def verify_cost_inputs(bundle_dir: Path, source_ids: set[str]) -> None:
    rows = _read_csv(bundle_dir / net_value.BUNDLE_COST_INPUTS_CSV)
    parameters = {
        row["parameter_id"]: row
        for row in _read_csv(bundle_dir / "bc_cost_parameters_used.csv")
    }
    _require(len(rows) == 945, f"cost grid has {len(rows)} rows, expected 945")
    _require(
        {row["zone"] for row in rows} == set(net_value.EXPECTED_ZONES),
        "cost grid zone set changed",
    )
    for row in rows:
        ids = {item for item in row["parameter_ids"].split("|") if item}
        _require(ids, f"{row['scenario_id']} has no parameter IDs")
        missing = ids - set(parameters)
        _require(
            not missing,
            f"{row['scenario_id']} references parameters missing from the bundle: "
            f"{sorted(missing)}",
        )
        cited = {item for item in row["source_ids"].split("|") if item}
        _require(
            cited <= source_ids,
            f"{row['scenario_id']} cites unknown sources: {sorted(cited - source_ids)}",
        )
        _require(
            {parameters[item]["source_key"] for item in ids} == cited,
            f"{row['scenario_id']} source IDs do not match its parameter register",
        )
        for prefix in ("logging", "haul", "total"):
            low = float(row[f"{prefix}_low_cad_m3"])
            base = float(row[f"{prefix}_base_cad_m3"])
            high = float(row[f"{prefix}_high_cad_m3"])
            _require(low <= base <= high, f"{row['scenario_id']} {prefix} is unordered")


def verify_silviculture(bundle_dir: Path, source_ids: set[str]) -> dict[str, float]:
    rows = _read_csv(bundle_dir / net_value.BUNDLE_SILVICULTURE_CSV)
    loaded = [row for row in rows if row["loaded_into_model"] == "true"]
    excluded = [row for row in rows if row["loaded_into_model"] == "false"]
    _require(len(loaded) == 7, f"silviculture loaded rows = {len(loaded)}, expected 7")
    _require(
        len(excluded) == 15,
        f"silviculture Coast excluded rows = {len(excluded)}, expected 15",
    )
    _require(
        all(row["exclusion_reason"] for row in excluded),
        "a Coast silviculture exclusion has no reason",
    )
    _require(
        all(row["region"] == "Coast" for row in excluded),
        "non-Coast row marked as silviculture exclusion",
    )
    _require(
        all(row["source_id"] in source_ids for row in rows),
        "silviculture row cites an unknown source",
    )
    allowances = net_value.load_silviculture_allowances(
        bundle_dir / net_value.BUNDLE_SILVICULTURE_CSV
    )
    _require(
        set(allowances) == set(net_value.EXPECTED_ZONES),
        "silviculture zone set changed",
    )
    return {zone: item.value_cad_m3 for zone, item in allowances.items()}


def verify_qc(bundle_dir: Path, source_ids: set[str]) -> float:
    rows = _read_csv(bundle_dir / net_value.BUNDLE_QC_REFERENCE_CSV)
    _require(len(rows) == 1, "QC reference must contain exactly one row")
    row = rows[0]
    _require(row["benchmark_id"] == "qc_2022_sepm_total", "QC benchmark id changed")
    _require(row["source_id"] in source_ids, "QC source is missing from sources.csv")
    spread = float(row["cost_spread_after_transfers_cad_m3"])
    _require(abs(spread - 0.81) < 0.001, f"QC cost spread is {spread}, expected 0.81")
    return spread


def verify_zone_mapping(bundle_dir: Path) -> None:
    rows = _read_csv(bundle_dir / "zone_region_mapping.csv")
    by_zone = {row["zone"]: row for row in rows}
    expected_rate_group = {zone: rate_group for zone, rate_group, _ in cost_model.ZONES}
    _require(set(by_zone) == set(net_value.EXPECTED_ZONES), "zone mapping set changed")
    for zone, row in by_zone.items():
        _require(
            row["price_region"] == net_value.price_region_for_zone(zone),
            f"price region mapping changed for {zone}",
        )
        _require(
            row["appraisal_region"] == row["price_region"],
            f"appraisal/price region mismatch for {zone}",
        )
        _require(
            row["rate_group"] == expected_rate_group[zone],
            f"rate group mapping changed for {zone}",
        )
        expected_silviculture = row["price_region"] == "Interior"
        _require(
            (row["silviculture_deduction"] == "true") == expected_silviculture,
            f"silviculture mapping changed for {zone}",
        )


def _headline(out_dir: Path) -> dict[str, float | int]:
    summary = json.loads((out_dir / net_value.SUMMARY_JSON).read_text(encoding="utf-8"))
    summary_rows = _read_csv(out_dir / net_value.SUMMARY_CSV)
    qc = _read_csv(out_dir / net_value.QC_REFERENCE_CSV)[0]
    results = summary["results"]
    return {
        "median_base_net_model_cad_m3": round(
            results["median_base_net_model_cad_m3"], 2
        ),
        "min_base_net_model_cad_m3": round(results["min_base_net_model_cad_m3"], 2),
        "max_base_net_model_cad_m3": round(results["max_base_net_model_cad_m3"], 2),
        "negative_net_base_rows": results["negative_net_base_rows"],
        "south_west_ground_net_model_base_median_cad_m3": round(
            next(
                float(row["net_model_base_median_cad_m3"])
                for row in summary_rows
                if row["zone"] == "South West Zone" and row["system"] == "ground"
            ),
            2,
        ),
        "north_west_ground_net_model_base_median_cad_m3": round(
            next(
                float(row["net_model_base_median_cad_m3"])
                for row in summary_rows
                if row["zone"] == "North West Zone" and row["system"] == "ground"
            ),
            2,
        ),
        "qc_cost_spread_after_transfers_cad_m3": round(
            float(qc["cost_spread_after_transfers_cad_m3"]), 2
        ),
    }


def reproduce(
    bundle_dir: Path = DEFAULT_BUNDLE_DIR,
    out_dir: Path | None = None,
) -> dict[str, Any]:
    """Validate the bundle and reproduce the corrected headline."""
    manifest = _load_manifest(bundle_dir)
    verify_files(bundle_dir, manifest)
    source_ids = verify_sources(bundle_dir)
    verify_price_panel(bundle_dir, set(source_ids))
    verify_cost_inputs(bundle_dir, set(source_ids))
    allowances = verify_silviculture(bundle_dir, set(source_ids))
    spread = verify_qc(bundle_dir, set(source_ids))
    verify_zone_mapping(bundle_dir)

    if out_dir is None:
        with tempfile.TemporaryDirectory(prefix="odnv_repro_") as raw_dir:
            built_dir = Path(raw_dir)
            net_value.build(built_dir, inputs_dir=bundle_dir)
            observed = _headline(built_dir)
            output_hashes = {
                name: sha256_file(built_dir / name) for name in CORE_OUTPUTS
            }
    else:
        out_dir.mkdir(parents=True, exist_ok=True)
        net_value.build(out_dir, inputs_dir=bundle_dir)
        observed = _headline(out_dir)
        output_hashes = {name: sha256_file(out_dir / name) for name in CORE_OUTPUTS}

    for key, expected in manifest["headline"].items():
        _require(
            observed[key] == expected,
            f"headline mismatch for {key}: expected {expected}, observed {observed[key]}",
        )
    for name, expected in manifest["reproduction"]["core_output_sha256"].items():
        _require(
            output_hashes[name] == expected,
            f"reproduced output hash mismatch for {name}",
        )
        published = MODEL_DIR / name
        if published.is_file():
            _require(
                sha256_file(published) == output_hashes[name],
                f"reproduced output {name} differs from the published artifact",
            )
    _require(
        abs(allowances["North West Zone"] - 5.84) < 0.001,
        "North West silviculture allowance changed",
    )
    _require(abs(spread - 0.81) < 0.001, "QC spread changed")
    return {
        "bundle_dir": str(bundle_dir),
        "file_count": len(manifest["files"]),
        "headline": observed,
        "core_output_sha256": output_hashes,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bundle-dir",
        type=Path,
        default=DEFAULT_BUNDLE_DIR,
        help="Published model-input bundle directory.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Optional directory to retain reproduced model artifacts.",
    )
    parser.add_argument("--quiet", action="store_true", help="Only print failures.")
    args = parser.parse_args(argv)
    try:
        result = reproduce(args.bundle_dir, args.out_dir)
    except ReproductionError as exc:
        print(f"REPRODUCTION FAILED: {exc}", file=sys.stderr)
        return 1
    if not args.quiet:
        headline = result["headline"]
        print(
            "reproduced open-data net-value headline: "
            f"median {headline['median_base_net_model_cad_m3']:.2f}, "
            f"range {headline['min_base_net_model_cad_m3']:.2f} to "
            f"{headline['max_base_net_model_cad_m3']:.2f}, "
            f"{headline['negative_net_base_rows']} negative; "
            f"SW ground {headline['south_west_ground_net_model_base_median_cad_m3']:.2f}, "
            f"NW ground {headline['north_west_ground_net_model_base_median_cad_m3']:.2f}, "
            f"QC {headline['qc_cost_spread_after_transfers_cad_m3']:.2f}"
        )
        print(f"verified {result['file_count']} bundle files and core output hashes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
