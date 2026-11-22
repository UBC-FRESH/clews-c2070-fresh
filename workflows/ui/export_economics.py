"""Export the corrected BC open-data economics sensitivity for the static UI.

The Forest Ledger UI is a no-build static application.  This exporter is the
only bridge between the committed ``open_data_net_value`` CSVs and the
``ui/public/data/economics`` bundle.  Every displayed number is parsed from a
committed CSV; no headline value is hand-entered in the UI.

The economics layer is a *reported sensitivity*.  It is never read by the
Forest LP, the public OSeCAN surface, or a live solver run.

Usage:
    .venv/bin/python workflows/ui/export_economics.py
    .venv/bin/python workflows/ui/export_economics.py --out /tmp/economics
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import statistics
import sys
import zipfile
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

SCHEMA_VERSION = "economics/1"
DATASET = "open_data_net_value"
SYSTEM_ORDER = ("ground", "cable", "heli")
REQUIRED_GROUP_COUNT = 21
REQUIRED_ZONE_COUNT = 7
REQUIRED_ROW_COUNT = 945
REQUIRED_MEDIAN = 48.84
REQUIRED_MIN = -23.38
REQUIRED_MAX = 86.85
REQUIRED_NEGATIVE_ROWS = 168
REQUIRED_QC_COST_SPREAD_AFTER = 0.81
REQUIRED_GROUP_MEDIANS = {
    ("South West Zone", "ground"): 64.40,
    ("South West Zone", "cable"): 50.48,
    ("South West Zone", "heli"): -1.78,
    ("North West Zone", "ground"): 57.76,
    ("North West Zone", "cable"): 45.77,
    ("North West Zone", "heli"): -6.49,
}
STALE_HEADLINE_VALUES = (45.81, -32.28, 182.0)
DETAIL_FILENAME = "detail.csv"
BUNDLE_FILENAME = "model_inputs.zip"
SUMMARY_FILENAME = "summary.json"
MANIFEST_FILENAME = "manifest.json"


class ExportError(RuntimeError):
    """Raised when a source artifact no longer matches the economics contract."""


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise ExportError(f"path escapes repository root: {path}") from exc


def _read_csv(path: Path, *, label: str) -> list[dict[str, str]]:
    if not path.is_file():
        raise ExportError(f"{label}: missing CSV artifact {path}")
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
    if not rows:
        raise ExportError(f"{label}: empty CSV artifact {path}")
    return rows


def _read_json(path: Path, *, label: str) -> Any:
    if not path.is_file():
        raise ExportError(f"{label}: missing JSON artifact {path}")
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except json.JSONDecodeError as exc:
        raise ExportError(f"{label}: invalid JSON in {path}: {exc}") from exc


def _float(row: dict[str, str], key: str, *, label: str) -> float:
    raw = row.get(key)
    if raw is None or raw == "":
        raise ExportError(f"{label}: missing numeric field {key}")
    try:
        value = float(raw)
    except ValueError as exc:
        raise ExportError(f"{label}: {key}={raw!r} is not numeric") from exc
    if not math.isfinite(value):
        raise ExportError(f"{label}: {key}={raw!r} is not finite")
    return value


def _optional_float(row: dict[str, str], key: str, *, label: str) -> float | None:
    raw = row.get(key)
    if raw is None or raw == "":
        return None
    return _float(row, key, label=label)


def _int(row: dict[str, str], key: str, *, label: str) -> int:
    value = _float(row, key, label=label)
    if not value.is_integer():
        raise ExportError(f"{label}: {key}={value!r} is not an integer")
    return int(value)


def _bool(row: dict[str, str], key: str, *, label: str) -> bool:
    raw = row.get(key)
    if raw == "true":
        return True
    if raw == "false":
        return False
    raise ExportError(f"{label}: {key}={raw!r} is not true/false")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ExportError(message)


def _parse_group(row: dict[str, str]) -> dict[str, Any]:
    label = f"{row.get('zone')} / {row.get('system')}"
    group = {
        "system": row["system"],
        "n_rows": _int(row, "n_rows", label=label),
        "logging_plus_haul_base_median_cad_m3": _float(
            row, "logging_plus_haul_base_median_cad_m3", label=label
        ),
        "silviculture_allowance_cad_m3": _optional_float(
            row, "road_silviculture_allowance_cad_m3", label=label
        ),
        "low_cad_m3": _float(row, "net_model_low_median_cad_m3", label=label),
        "base_cad_m3": _float(row, "net_model_base_median_cad_m3", label=label),
        "high_cad_m3": _float(row, "net_model_high_median_cad_m3", label=label),
        "base_min_cad_m3": _float(row, "net_model_base_min_cad_m3", label=label),
        "base_max_cad_m3": _float(row, "net_model_base_max_cad_m3", label=label),
        "envelope_min_cad_m3": _float(
            row, "net_model_envelope_min_cad_m3", label=label
        ),
        "envelope_max_cad_m3": _float(
            row, "net_model_envelope_max_cad_m3", label=label
        ),
        "negative_rows": _int(row, "negative_net_base_count", label=label),
        "negative_share": _float(row, "negative_net_base_share", label=label),
        "road_silviculture_included": _bool(
            row, "road_silviculture_included", label=label
        ),
    }
    _require(0 <= group["negative_rows"] <= group["n_rows"], f"{label}: bad negative count")
    _require(
        group["low_cad_m3"] <= group["base_cad_m3"] <= group["high_cad_m3"],
        f"{label}: low/base/high are not ordered",
    )
    _require(
        group["base_min_cad_m3"] <= group["base_cad_m3"] <= group["base_max_cad_m3"],
        f"{label}: base median is outside its observed range",
    )
    group["negative"] = group["base_cad_m3"] < 0 or group["negative_rows"] > 0
    return group


def _parse_summary_rows(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    zone_order = list(dict.fromkeys(row["zone"] for row in rows))
    _require(
        len(zone_order) == REQUIRED_ZONE_COUNT,
        f"expected {REQUIRED_ZONE_COUNT} zones, found {len(zone_order)}",
    )
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    price_region_by_zone: dict[str, str] = {}
    for row in rows:
        key = (row["zone"], row["system"])
        if key in by_key:
            raise ExportError(f"duplicate zone/system summary row: {key}")
        by_key[key] = _parse_group(row)
        price_region_by_zone[row["zone"]] = row["price_region"]
    _require(
        len(by_key) == REQUIRED_GROUP_COUNT,
        f"expected {REQUIRED_GROUP_COUNT} zone/system groups, found {len(by_key)}",
    )

    zones: list[dict[str, Any]] = []
    for zone in zone_order:
        systems = []
        for system in SYSTEM_ORDER:
            group = by_key.get((zone, system))
            if group is None:
                raise ExportError(f"missing zone/system group: {zone} / {system}")
            systems.append(group)
        zones.append(
            {
                "zone": zone,
                "price_region": price_region_by_zone[zone],
                "n_rows": sum(group["n_rows"] for group in systems),
                "systems": systems,
            }
        )

    for (zone, system), expected in REQUIRED_GROUP_MEDIANS.items():
        group = by_key[(zone, system)]
        if abs(group["base_cad_m3"] - expected) > 0.005:
            raise ExportError(
                f"corrected median mismatch for {zone} / {system}: "
                f"{group['base_cad_m3']} != {expected}"
            )
    return zones


def _headline_from_rows(rows: list[dict[str, str]]) -> dict[str, Any]:
    _require(
        len(rows) == REQUIRED_ROW_COUNT,
        f"expected {REQUIRED_ROW_COUNT} detail rows, found {len(rows)}",
    )
    values = [
        _float(row, "net_model_base_cad_m3", label=row.get("scenario_id", "detail"))
        for row in rows
    ]
    median = statistics.median(values)
    low = min(values)
    high = max(values)
    negative_rows = sum(1 for value in values if value < 0)
    headline = {
        "rows": len(values),
        "zone_system_groups": REQUIRED_GROUP_COUNT,
        "median_base_net_model_cad_m3": round(median, 2),
        "min_base_net_model_cad_m3": round(low, 2),
        "max_base_net_model_cad_m3": round(high, 2),
        "negative_net_base_rows": negative_rows,
        "negative_net_base_share": round(negative_rows / len(values), 6),
    }
    _require(
        abs(median - REQUIRED_MEDIAN) < 1e-9,
        f"corrected median mismatch: {median} != {REQUIRED_MEDIAN}",
    )
    _require(abs(low - REQUIRED_MIN) < 1e-9, f"corrected minimum mismatch: {low}")
    _require(abs(high - REQUIRED_MAX) < 1e-9, f"corrected maximum mismatch: {high}")
    _require(
        negative_rows == REQUIRED_NEGATIVE_ROWS,
        f"corrected negative-row count mismatch: {negative_rows}",
    )
    for stale in STALE_HEADLINE_VALUES:
        _require(
            abs(median - stale) > 1e-9
            and abs(low - stale) > 1e-9
            and abs(high - stale) > 1e-9
            and negative_rows != stale,
            f"stale economics headline value detected: {stale}",
        )
    return headline


def _validate_groups_against_detail(
    zones: list[dict[str, Any]], detail_rows: list[dict[str, str]]
) -> None:
    raw: dict[tuple[str, str], list[float]] = defaultdict(list)
    for row in detail_rows:
        label = row.get("scenario_id", "detail")
        raw[(row["zone"], row["system"])].append(
            _float(row, "net_model_base_cad_m3", label=label)
        )
    for zone in zones:
        for group in zone["systems"]:
            values = raw.get((zone["zone"], group["system"]), [])
            _require(
                bool(values),
                f"detail CSV has no rows for {zone['zone']} / {group['system']}",
            )
            if abs(statistics.median(values) - group["base_cad_m3"]) > 0.006:
                raise ExportError(
                    f"summary/detail median mismatch for "
                    f"{zone['zone']} / {group['system']}"
                )


def _qc_reference(row: dict[str, str]) -> dict[str, Any]:
    label = "QC reference"
    payload = {
        "reference_id": row["reference_id"],
        "geography": row["geography"],
        "species_group": row["species_group"],
        "product_or_grade": row["product_or_grade"],
        "price_year": _int(row, "price_year", label=label),
        "currency_basis": row["currency_basis"],
        "purchase_cost_cad_m3": _float(row, "purchase_cost_cad_m3", label=label),
        "bgad_before_transfers_cad_m3": _float(
            row, "bgad_before_transfers_cad_m3", label=label
        ),
        "transfers_total_cad_m3": _float(row, "transfers_total_cad_m3", label=label),
        "bgad_after_transfers_cad_m3": _float(
            row, "bgad_after_transfers_cad_m3", label=label
        ),
        "cost_spread_before_transfers_cad_m3": _float(
            row, "cost_spread_before_transfers_cad_m3", label=label
        ),
        "cost_spread_after_transfers_cad_m3": _float(
            row, "cost_spread_after_transfers_cad_m3", label=label
        ),
        "evidence": row["evidence"],
        "observed": _bool(row, "observed", label=label),
        "policy_grade": _bool(row, "policy_grade", label=label),
        "net_value_status": row["net_value_status"],
        "interpretation": row["interpretation"],
        "notes": row["notes"],
    }
    _require(
        abs(payload["cost_spread_after_transfers_cad_m3"] - REQUIRED_QC_COST_SPREAD_AFTER)
        < 1e-9,
        "QC after-transfer cost spread mismatch",
    )
    _require(
        payload["net_value_status"] == "separate_reference_not_net_value",
        "QC reference must be labelled separate_reference_not_net_value",
    )
    _require(not payload["observed"] and not payload["policy_grade"], "QC labels")
    return payload


def _evidence_rows(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    parsed: list[dict[str, Any]] = []
    for row in rows:
        label = row.get("summary_id", "evidence")
        _require(
            row.get("net_value_status") == "not_estimated",
            f"{label}: evidence summary must stay not_estimated",
        )
        _require(
            row.get("policy_grade") == "false",
            f"{label}: evidence summary must stay policy_grade=false",
        )
        parsed.append(
            {
                "summary_id": row["summary_id"],
                "jurisdiction": row["jurisdiction"],
                "evidence_tier": row["evidence_tier"],
                "revenue_basis": row["revenue_basis"],
                "revenue_measure": row["revenue_measure"],
                "revenue_min_cad_m3": _optional_float(
                    row, "revenue_min_cad_m3", label=label
                ),
                "revenue_median_cad_m3": _optional_float(
                    row, "revenue_median_cad_m3", label=label
                ),
                "revenue_max_cad_m3": _optional_float(
                    row, "revenue_max_cad_m3", label=label
                ),
                "cost_coverage": row["cost_coverage"],
                "net_value_status": row["net_value_status"],
                "net_value_reason": row["net_value_reason"],
                "policy_grade": False,
                "is_additive": _bool(row, "is_additive", label=label),
                "notes": row["notes"],
            }
        )
    _require(bool(parsed), "evidence-tier summary is empty")
    return parsed


def _copy_detail(source: Path, destination: Path, *, rows: int) -> dict[str, Any]:
    payload = source.read_bytes()
    destination.write_bytes(payload)
    return {
        "label": "Full 945-row BC detail CSV",
        "path": f"economics/{DETAIL_FILENAME}",
        "bytes": len(payload),
        "sha256": _sha256_bytes(payload),
        "rows": rows,
        "media_type": "text/csv",
    }


def _build_bundle_zip(source_dir: Path, destination: Path) -> dict[str, Any]:
    if not source_dir.is_dir():
        raise ExportError(f"model-input bundle directory is missing: {source_dir}")
    files = sorted(path for path in source_dir.rglob("*") if path.is_file())
    _require(bool(files), f"model-input bundle is empty: {source_dir}")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in files:
            relative = path.relative_to(source_dir).as_posix()
            info = zipfile.ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, path.read_bytes())
    payload = buffer.getvalue()
    destination.write_bytes(payload)
    return {
        "label": "Model-input bundle (ZIP)",
        "path": f"economics/{BUNDLE_FILENAME}",
        "bytes": len(payload),
        "sha256": _sha256_bytes(payload),
        "media_type": "application/zip",
        "files": [
            {
                "path": path.relative_to(source_dir).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": _sha256_file(path),
            }
            for path in files
        ],
    }


def _source_entry(path: Path, root: Path, *, rows: int | None = None) -> dict[str, Any]:
    return {
        "path": _relative(path, root),
        "bytes": path.stat().st_size,
        "sha256": _sha256_file(path),
        "rows": rows,
    }


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def build_bundle(root: Path, out_dir: Path) -> dict[str, Any]:
    root = root.resolve()
    out_dir = out_dir.resolve()
    source_root = root / "data/derived/national/harvest_basis/open_data_net_value"
    v2d_root = root / "data/derived/national/harvest_basis/forest_economics_v2d"
    summary_csv = source_root / "bc_open_net_value_summary.csv"
    detail_csv = source_root / "bc_open_net_value.csv"
    qc_csv = source_root / "qc_open_reference.csv"
    summary_json_path = source_root / "summary.json"
    v2d_summary_csv = v2d_root / "forest_economics_v2d_summary.csv"
    v2d_summary_json_path = v2d_root / "summary.json"
    model_inputs_dir = source_root / "model_inputs"

    summary_rows = _read_csv(summary_csv, label="BC zone/system summary")
    detail_rows = _read_csv(detail_csv, label="BC 945-row detail")
    qc_rows = _read_csv(qc_csv, label="QC reference")
    v2d_rows = _read_csv(v2d_summary_csv, label="forest economics v2d summary")
    summary_json = _read_json(summary_json_path, label="BC summary.json")
    v2d_summary_json = _read_json(v2d_summary_json_path, label="v2d summary.json")

    _require(len(qc_rows) == 1, f"expected 1 QC reference row, found {len(qc_rows)}")
    zones = _parse_summary_rows(summary_rows)
    headline = _headline_from_rows(detail_rows)
    _validate_groups_against_detail(zones, detail_rows)

    results = summary_json.get("results", {})
    for key in (
        "median_base_net_model_cad_m3",
        "min_base_net_model_cad_m3",
        "max_base_net_model_cad_m3",
        "negative_net_base_rows",
    ):
        expected = results.get(key)
        _require(expected is not None, f"summary.json/results is missing {key}")
        actual = headline[
            {
                "median_base_net_model_cad_m3": "median_base_net_model_cad_m3",
                "min_base_net_model_cad_m3": "min_base_net_model_cad_m3",
                "max_base_net_model_cad_m3": "max_base_net_model_cad_m3",
                "negative_net_base_rows": "negative_net_base_rows",
            }[key]
        ]
        if isinstance(expected, str):
            expected = float(expected)
        _require(
            abs(float(expected) - float(actual)) < 1e-9,
            f"summary.json results mismatch for {key}",
        )

    labels = summary_json.get("labels", {})
    _require(labels.get("evidence") == "model_estimate", "evidence label mismatch")
    _require(labels.get("observed") is False, "observed label mismatch")
    _require(labels.get("policy_grade") is False, "policy_grade label mismatch")
    _require(
        labels.get("qc_net_value_status") == "separate_reference_not_net_value",
        "QC net-value label mismatch",
    )
    _require(
        labels.get("combined_bc_qc_national_total") is False,
        "combined BC+QC total label mismatch",
    )
    _require(
        summary_json.get("scope", {}).get("combined_national_net_value") is False,
        "combined national net-value scope mismatch",
    )
    licensing = summary_json.get("licensing", {})
    _require(
        licensing.get("classification") == "public_reference_copyrighted",
        "source licensing classification mismatch",
    )

    qc_reference = _qc_reference(qc_rows[0])
    evidence_rows = _evidence_rows(v2d_rows)
    evidence_tiers = sorted({row["evidence_tier"] for row in evidence_rows})
    _require(
        all(row["net_value_status"] == "not_estimated" for row in evidence_rows),
        "evidence rows must remain not_estimated",
    )
    _require(
        v2d_summary_json.get("labels", {}).get("policy_grade") is False,
        "v2d policy_grade label mismatch",
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    detail_payload = _copy_detail(detail_csv, out_dir / DETAIL_FILENAME, rows=len(detail_rows))
    bundle_payload = _build_bundle_zip(model_inputs_dir, out_dir / BUNDLE_FILENAME)

    source_files = [
        _source_entry(summary_csv, root, rows=len(summary_rows)),
        _source_entry(detail_csv, root, rows=len(detail_rows)),
        _source_entry(qc_csv, root, rows=len(qc_rows)),
        _source_entry(v2d_summary_csv, root, rows=len(v2d_rows)),
        _source_entry(summary_json_path, root),
        _source_entry(v2d_summary_json_path, root),
    ]

    caveats = [str(item) for item in summary_json.get("caveats", [])]
    non_claims = [str(item) for item in summary_json.get("non_claims", [])]
    _require(bool(caveats), "summary.json caveats are empty")
    _require(bool(non_claims), "summary.json non_claims are empty")

    summary_payload = {
        "schema_version": SCHEMA_VERSION,
        "dataset": DATASET,
        "status": "model_estimate",
        "role": "reported_sensitivity",
        "sensitivity_role": (
            "Reported sensitivity only; not a Forest LP objective input and not a "
            "public OSeCAN input."
        ),
        "generated_utc": utc_now(),
        "currency": "CAD",
        "unit": "CAD/m3",
        "title": "BC open-data modeled mill-delivered margin",
        "labels": {
            "evidence": "model_estimate",
            "observed": False,
            "policy_grade": False,
            "price_basis": labels.get("price_basis"),
            "cost_basis": labels.get("cost_basis"),
            "net_value_status": "model_estimate",
            "qc_net_value_status": "separate_reference_not_net_value",
            "source_licensing": licensing.get("classification"),
            "combined_bc_qc_national_total": False,
            "forest_lp_input": False,
            "public_osecan_input": False,
        },
        "headline": headline,
        "zones": zones,
        "qc_reference": qc_reference,
        "evidence_tiers": evidence_tiers,
        "evidence_rows": evidence_rows,
        "caveats": caveats,
        "non_claims": non_claims,
        "sources": source_files,
        "downloads": {
            "detail_csv": detail_payload,
            "model_inputs_zip": bundle_payload,
            "manifest": {
                "label": "Economics export manifest",
                "path": f"economics/{MANIFEST_FILENAME}",
                "media_type": "application/json",
            },
        },
    }

    _write_json(out_dir / SUMMARY_FILENAME, summary_payload)

    checks = [
        {
            "name": "headline_matches_corrected_values",
            "ok": True,
            "detail": headline,
        },
        {
            "name": "zone_system_groups",
            "ok": len(zones) == REQUIRED_ZONE_COUNT
            and sum(len(zone["systems"]) for zone in zones) == REQUIRED_GROUP_COUNT,
            "detail": {
                "zones": len(zones),
                "groups": sum(len(zone["systems"]) for zone in zones),
            },
        },
        {
            "name": "qc_reference_separate_not_net_value",
            "ok": qc_reference["net_value_status"]
            == "separate_reference_not_net_value",
            "detail": qc_reference["cost_spread_after_transfers_cad_m3"],
        },
        {
            "name": "evidence_tiers_not_estimated",
            "ok": all(
                row["net_value_status"] == "not_estimated"
                and row["policy_grade"] is False
                for row in evidence_rows
            ),
            "detail": {
                "rows": len(evidence_rows),
                "tiers": evidence_tiers,
            },
        },
        {
            "name": "retired_headline_values_absent",
            "ok": True,
            "detail": "headline and zone/system medians exclude the retired guards",
        },
        {
            "name": "model_input_bundle_present",
            "ok": bool(bundle_payload["files"]),
            "detail": {
                "files": len(bundle_payload["files"]),
                "bytes": bundle_payload["bytes"],
            },
        },
    ]

    generated_files = [
        {
            "path": f"economics/{SUMMARY_FILENAME}",
            "bytes": (out_dir / SUMMARY_FILENAME).stat().st_size,
            "sha256": _sha256_file(out_dir / SUMMARY_FILENAME),
        },
        {
            "path": f"economics/{DETAIL_FILENAME}",
            "bytes": detail_payload["bytes"],
            "sha256": detail_payload["sha256"],
            "rows": detail_payload["rows"],
        },
        {
            "path": f"economics/{BUNDLE_FILENAME}",
            "bytes": bundle_payload["bytes"],
            "sha256": bundle_payload["sha256"],
        },
    ]

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "dataset": DATASET,
        "generated_utc": summary_payload["generated_utc"],
        "source_root": "data/derived/national/harvest_basis",
        "files": generated_files,
        "sources": source_files,
        "bundle": {
            "source_dir": _relative(model_inputs_dir, root),
            "zip": bundle_payload,
        },
        "validation": {"ok": all(check["ok"] for check in checks), "checks": checks},
    }
    _require(manifest["validation"]["ok"], "economics export validation failed")
    _write_json(out_dir / MANIFEST_FILENAME, manifest)
    return manifest


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=_REPO_ROOT,
        help="repository root (default: parent of workflows/)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=_REPO_ROOT / "ui/public/data/economics",
        help="output directory (default: ui/public/data/economics)",
    )
    args = parser.parse_args(argv)
    manifest = build_bundle(args.root, args.out)
    print(
        json.dumps(
            {
                "ok": manifest["validation"]["ok"],
                "files": len(manifest["files"]),
                "bundle_files": len(manifest["bundle"]["zip"]["files"]),
                "out": str(args.out),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
