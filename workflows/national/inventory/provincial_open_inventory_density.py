"""Build E2 v3 open-inventory density targets for nine provinces.

The E2 v2 tree lists used a uniform 1,000 stems/ha expansion for every
province.  This module replaces that screening default with the best open
density evidence available:

* SK: FRI stand ``BA`` and ``QMD`` imply an arithmetic stem density
  ``N = BA / (pi/4 * (QMD/100)^2)``.  ``MSTEMS`` is retained as a
  merchantable-stem cross-check.
* NS: ``TOTBA`` and ``AVDI`` imply the same arithmetic stem density from the
  full open DBF.  ``TOTMRVOL`` is retained as the volume check.
* PE: EFI2020 publishes merchantable trees/ha (``tphmerch``); ``ba``,
  ``bamerch`` and ``gtv`` are retained as supporting fields.
* AB: AVI publishes crown-density and structure *classes* but no numeric
  stems/ha.  The 1,000 stems/ha screening default is retained as a labelled
  proxy, with the area-weighted class shares recorded.
* MB: the open 1970s FRI has species and age but no density.  The median
  measured donor plot density is used and labelled as donor evidence.
* ON / NL / NT / NU: no open stand density; the 1,000 stems/ha screening
  default is retained as a labelled proxy.

No request, DUA, restricted data, facility coefficient or official OSeCAN
input is used.  Outputs are small, committed evidence tables; the full
provincial layers are not redistributed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .provincial_open_inventory_distributions import (
    PROVINCES,
    _iter_dbf,
    _positive,
)

__all__ = ["ARTIFACT_ID", "DEFAULT_OUTPUT_DIR", "build"]

ARTIFACT_ID = "provincial_open_inventory_density_v1"
REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT_DIR = REPO_ROOT / "data" / "derived" / "national" / "inventory" / ARTIFACT_ID
AUDIT_DIR = REPO_ROOT / "data/derived/national/inventory/provincial_open_inventory_audit_v1"
SAMPLE_DIR = AUDIT_DIR / "samples"
DISTRIBUTION_DIR = (
    REPO_ROOT / "data/derived/national/inventory/provincial_open_inventory_distributions_v1"
)
DONOR_TREE_PATH = (
    REPO_ROOT / "data/derived/national/bucking/four_province_bucking_pilot_v0/tree_list.csv"
)
DEFAULT_SOURCE_ROOT = REPO_ROOT / "tmp/pia"
RETRIEVAL_DATE = "2026-11-23"
DEFAULT_STEMS_PER_HA = 1000.0

DENSITY_COLUMNS = (
    "province",
    "density_stems_ha",
    "density_statistic",
    "density_basis",
    "density_evidence_class",
    "density_source_ids",
    "density_n",
    "density_mean_stems_ha",
    "density_p50_stems_ha",
    "density_p25_stems_ha",
    "density_p75_stems_ha",
    "supporting_fields",
    "inventory_volume_mean_m3ha",
    "inventory_volume_p50_m3ha",
    "inventory_volume_source",
    "expansion_label",
    "notes",
)


class DensityError(RuntimeError):
    """Raised when a density evidence contract is violated."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_csv(path: Path, rows: Iterable[Mapping[str, Any]], columns: Sequence[str]) -> int:
    frame = pd.DataFrame(list(rows), columns=list(columns))
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, lineterminator="\n")
    return len(frame)


def _weighted_quantile(values: Sequence[float], weights: Sequence[float], quantile: float) -> float:
    if len(values) == 0:
        raise DensityError("weighted quantile received no values")
    if len(values) != len(weights):
        raise DensityError("weighted quantile length mismatch")
    ordered = sorted(zip(values, weights, strict=True))
    total = math.fsum(weight for _, weight in ordered)
    if total <= 0.0:
        raise DensityError("weighted quantile received non-positive total weight")
    target = quantile * total
    cumulative = 0.0
    for value, weight in ordered:
        cumulative += weight
        if cumulative >= target:
            return float(value)
    return float(ordered[-1][0])


def _quantile_record(values: Sequence[float], weights: Sequence[float]) -> dict[str, Any]:
    return {
        "n": len(values),
        "mean": float(np.average(np.asarray(values, dtype=float), weights=np.asarray(weights, dtype=float))),
        "p05": _weighted_quantile(values, weights, 0.05),
        "p10": _weighted_quantile(values, weights, 0.10),
        "p25": _weighted_quantile(values, weights, 0.25),
        "p50": _weighted_quantile(values, weights, 0.50),
        "p75": _weighted_quantile(values, weights, 0.75),
        "p90": _weighted_quantile(values, weights, 0.90),
        "p95": _weighted_quantile(values, weights, 0.95),
        "min": float(np.min(values)),
        "max": float(np.max(values)),
    }


def _inventory_volume_context() -> dict[str, dict[str, Any]]:
    path = DISTRIBUTION_DIR / "attribute_quantiles.csv"
    frame = pd.read_csv(path)
    overall = frame[(frame["species_nfi"] == "ALL") & (frame["attribute"] == "volume")]
    result: dict[str, dict[str, Any]] = {}
    for province in PROVINCES:
        row = overall[overall["province"] == province]
        if row.empty:
            result[province] = {
                "inventory_volume_mean_m3ha": None,
                "inventory_volume_p50_m3ha": None,
                "inventory_volume_source": "not_available",
            }
            continue
        record = row.iloc[0]
        result[province] = {
            "inventory_volume_mean_m3ha": float(record["mean"]),
            "inventory_volume_p50_m3ha": float(record["p50"]),
            "inventory_volume_source": str(record["notes"]),
        }
    return result


def _sk_density() -> tuple[list[float], list[float], dict[str, float], str]:
    path = SAMPLE_DIR / "sk_fri_forest_stands_sample.csv"
    frame = pd.read_csv(path, low_memory=False)
    frame = frame[frame["TYPE"].fillna("").isin(["", "FOR"])]
    frame = frame[
        (frame["HECTARES"] > 0.0)
        & (frame["HEIGHT"] > 1.3)
        & (frame["QMD"] > 0.0)
        & (frame["BA"].notna())
        & (frame["GBVOL"] > 0.0)
    ]
    if frame.empty:
        raise DensityError("SK FRI sample has no valid stands")
    density = frame["BA"] / (math.pi / 4.0 * (frame["QMD"] / 100.0) ** 2)
    keep = np.isfinite(density) & (density > 0.0)
    density = density[keep]
    weights = frame.loc[keep, "HECTARES"]
    stats = _quantile_record(density.to_numpy(dtype=float), weights.to_numpy(dtype=float))
    return density.tolist(), weights.tolist(), stats, "sk_fri_forest_stands"


def _ns_density(source_root: Path) -> tuple[list[float], list[float], dict[str, float], str]:
    path = source_root / "ns_forest.dbf"
    if not path.is_file():
        raise DensityError(f"NS forest inventory DBF not found: {path}")
    density: list[float] = []
    weights: list[float] = []
    for record in _iter_dbf(path, ["HEIGHT", "TOTBA", "TOTMRVOL", "AVDI", "HECTARES"]):
        height = _positive(record.get("HEIGHT"), "HEIGHT")
        basal = _positive(record.get("TOTBA"), "TOTBA")
        volume = _positive(record.get("TOTMRVOL"), "TOTMRVOL")
        diameter = _positive(record.get("AVDI"), "AVDI")
        hectares = _positive(record.get("HECTARES"), "HECTARES")
        if None in (height, basal, volume, diameter, hectares) or height <= 1.3:
            continue
        stems = basal / (math.pi / 4.0 * (diameter / 100.0) ** 2)
        if not math.isfinite(stems) or stems <= 0.0:
            continue
        density.append(stems)
        weights.append(hectares)
    if not density:
        raise DensityError("NS DBF produced no density observations")
    stats = _quantile_record(density, weights)
    return density, weights, stats, "ns_forest_inventory"


def _pe_density() -> tuple[list[float], list[float], dict[str, float], str]:
    path = SAMPLE_DIR / "pe_efi_2020_sample.csv"
    frame = pd.read_csv(path, low_memory=False)
    frame = frame[
        (frame["tphmerch"] > 0.0)
        & (frame["gtv"] > 0.0)
        & (frame["SHAPE__Area"] > 0.0)
    ]
    if frame.empty:
        raise DensityError("PE EFI sample has no valid cells")
    density = frame["tphmerch"].to_numpy(dtype=float)
    weights = frame["SHAPE__Area"].to_numpy(dtype=float)
    stats = _quantile_record(density, weights)
    return density.tolist(), weights.tolist(), stats, "pe_efi_2020"


def _donor_density() -> tuple[list[float], list[float], dict[str, float], str]:
    frame = pd.read_csv(DONOR_TREE_PATH)
    if frame.empty:
        raise DensityError("donor tree list is empty")
    plot_totals = (
        frame.groupby(["jurisdiction", "source_plot_id"], sort=True)["expansion_factor"]
        .sum()
        .to_numpy(dtype=float)
    )
    if len(plot_totals) == 0:
        raise DensityError("donor tree list produced no plot densities")
    weights = np.ones(len(plot_totals), dtype=float)
    stats = _quantile_record(plot_totals.tolist(), weights.tolist())
    return plot_totals.tolist(), weights.tolist(), stats, "four_province_bucking_pilot_v0"


def _ab_density_structure(source_root: Path) -> list[dict[str, Any]]:
    path = source_root / "ab_fgdb/Data/10TM_Offset/AlbertaVegetationInventoryCrown.gdb"
    if not path.exists():
        raise DensityError(f"AB AVI FGDB not found: {path}")
    import pyogrio

    frame = pyogrio.read_dataframe(
        path,
        layer="AVI_Crown",
        read_geometry=False,
        columns=["DENSITY", "STRUC", "HEIGHT", "SP1", "Shape_Area"],
    )
    frame = frame[
        (frame["HEIGHT"] > 0.0)
        & (frame["SP1"].fillna("") != "")
        & (frame["Shape_Area"] > 0.0)
    ]
    if frame.empty:
        raise DensityError("AB AVI layer has no valid forest polygons")
    rows: list[dict[str, Any]] = []
    for field, label in (("DENSITY", "density"), ("STRUC", "structure")):
        subset = frame[frame[field].fillna("") != ""]
        total_area = float(subset["Shape_Area"].sum())
        for code, group in subset.groupby(field, dropna=False):
            area = float(group["Shape_Area"].sum())
            rows.append(
                {
                    "class_type": label,
                    "class_code": str(code),
                    "area_share": area / total_area if total_area > 0.0 else 0.0,
                    "area_ha": area / 10_000.0,
                    "mean_height_m": float(
                        np.average(group["HEIGHT"], weights=group["Shape_Area"])
                    ),
                    "n_polygons": len(group),
                    "notes": (
                        "AVI crown-density class; class is ordinal, not a numeric stems/ha field"
                        if field == "DENSITY"
                        else "AVI stand-structure class; no numeric stems/ha field"
                    ),
                }
            )
    return rows


def build(
    out_dir: Path = DEFAULT_OUTPUT_DIR,
    *,
    source_root: Path | None = None,
) -> dict[str, Any]:
    started = datetime.now(UTC)
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    source_root = Path(source_root or DEFAULT_SOURCE_ROOT)
    volume = _inventory_volume_context()

    density_rows: list[dict[str, Any]] = []
    quantile_rows: list[dict[str, Any]] = []

    sk_values, sk_weights, sk_stats, sk_source = _sk_density()
    ns_values, ns_weights, ns_stats, ns_source = _ns_density(source_root)
    pe_values, pe_weights, pe_stats, _pe_source = _pe_density()
    donor_values, donor_weights, donor_stats, _donor_source = _donor_density()
    ab_classes = _ab_density_structure(source_root)

    province_evidence: dict[str, dict[str, Any]] = {
        "AB": {
            "values": [],
            "weights": [],
            "stats": None,
            "target": DEFAULT_STEMS_PER_HA,
            "statistic": "proxy_default",
            "basis": (
                "AVI DENSITY and STRUC are classes, not numeric stems/ha; the "
                "1,000 stems/ha screening default is retained as a labelled proxy"
            ),
            "evidence_class": "proxy",
            "source_ids": "ab_avi_crown",
            "supporting": "DENSITY;STRUC;HEIGHT;SP1;Shape_Area",
            "notes": (
                "AVI density/structure class shares are recorded in "
                "ab_density_structure.csv; no numeric density calibration is claimed"
            ),
            "expansion_label": "avi_density_structure_class_proxy_1000",
        },
        "SK": {
            "values": sk_values,
            "weights": sk_weights,
            "stats": sk_stats,
            "target": sk_stats["p50"],
            "statistic": "area_weighted_median",
            "basis": (
                "N = BA / (pi/4 * (QMD/100)^2) per FRI stand, hectare-weighted "
                "median; MSTEMS retained as merchantable-stem cross-check"
            ),
            "evidence_class": "open_inventory",
            "source_ids": sk_source,
            "supporting": "MSTEMS;BA;QMD;GBVOL;GMVOL8;HECTARES",
            "notes": (
                "MSTEMS is a merchantable-stem subset; BA/QMD-implied total stem "
                "density is used for the whole-tree expansion. Volume check uses GBVOL."
            ),
            "expansion_label": "open_inventory_density_ba_qmd_median",
        },
        "MB": {
            "values": donor_values,
            "weights": donor_weights,
            "stats": donor_stats,
            "target": donor_stats["p50"],
            "statistic": "donor_median_plot_density",
            "basis": (
                "median sum of measured donor expansion factors per donor plot; "
                "MB open 1970s FRI has species and age but no density field"
            ),
            "evidence_class": "donor",
            "source_ids": "mb_fri_1970s;four_province_bucking_pilot_v0",
            "supporting": "SPECIES;AGE_YRPHOTO;AREA_HA;donor_expansion_factor",
            "notes": (
                "Donor density is measured cross-jurisdiction evidence, not MB "
                "inventory density; labelled donor_density_proxy."
            ),
            "expansion_label": "donor_density_median_proxy",
        },
        "NS": {
            "values": ns_values,
            "weights": ns_weights,
            "stats": ns_stats,
            "target": ns_stats["p50"],
            "statistic": "area_weighted_median",
            "basis": (
                "N = TOTBA / (pi/4 * (AVDI/100)^2) per polygon, hectare-weighted "
                "median; TOTMRVOL retained as the volume check"
            ),
            "evidence_class": "open_inventory",
            "source_ids": ns_source,
            "supporting": "TOTBA;AVDI;TOTMRVOL;HEIGHT;HECTARES",
            "notes": (
                "AVDI is the published average total diameter; the implied density "
                "is an arithmetic stem density, not a measured tree list."
            ),
            "expansion_label": "open_inventory_density_totba_avdi_median",
        },
        "PE": {
            "values": pe_values,
            "weights": pe_weights,
            "stats": pe_stats,
            "target": pe_stats["p50"],
            "statistic": "area_weighted_median",
            "basis": (
                "EFI2020 tphmerch, grid-cell-area-weighted median; ba, bamerch and "
                "gtv retained as supporting fields and volume check"
            ),
            "evidence_class": "open_inventory",
            "source_ids": "pe_efi_2020;pe_clui_2010_forest",
            "supporting": "tphmerch;ba;bamerch;gtv;SHAPE__Area",
            "notes": (
                "tphmerch is merchantable trees/ha; EFI volume gtv is the open "
                "volume check. Species and height still come from CLUI."
            ),
            "expansion_label": "open_inventory_density_tphmerch_median",
        },
    }
    for province in ("ON", "NL", "NT", "NU"):
        province_evidence[province] = {
            "values": [],
            "weights": [],
            "stats": None,
            "target": DEFAULT_STEMS_PER_HA,
            "statistic": "proxy_default",
            "basis": (
                "no open stand density field identified; the 1,000 stems/ha "
                "screening default is retained as a labelled proxy"
            ),
            "evidence_class": "proxy",
            "source_ids": "scanfi_v2;four_province_bucking_pilot_v0",
            "supporting": "none; donor geometry only",
            "notes": "donor-only province; density is an explicit screening proxy",
            "expansion_label": "donor_1000_density_proxy",
        }

    for province in PROVINCES:
        evidence = province_evidence[province]
        stats = evidence["stats"]
        target = float(evidence["target"])
        if not math.isfinite(target) or target <= 0.0:
            raise DensityError(f"{province} density target is not positive and finite")
        volume_context = volume[province]
        density_rows.append(
            {
                "province": province,
                "density_stems_ha": round(target, 6),
                "density_statistic": evidence["statistic"],
                "density_basis": evidence["basis"],
                "density_evidence_class": evidence["evidence_class"],
                "density_source_ids": evidence["source_ids"],
                "density_n": int(stats["n"]) if stats else 0,
                "density_mean_stems_ha": round(float(stats["mean"]), 6) if stats else 0.0,
                "density_p50_stems_ha": round(float(stats["p50"]), 6) if stats else 0.0,
                "density_p25_stems_ha": round(float(stats["p25"]), 6) if stats else 0.0,
                "density_p75_stems_ha": round(float(stats["p75"]), 6) if stats else 0.0,
                "supporting_fields": evidence["supporting"],
                "inventory_volume_mean_m3ha": volume_context["inventory_volume_mean_m3ha"],
                "inventory_volume_p50_m3ha": volume_context["inventory_volume_p50_m3ha"],
                "inventory_volume_source": volume_context["inventory_volume_source"],
                "expansion_label": evidence["expansion_label"],
                "notes": evidence["notes"],
            }
        )
        if stats:
            quantile_rows.append(
                {
                    "province": province,
                    "density_basis": evidence["basis"],
                    "density_evidence_class": evidence["evidence_class"],
                    "source_ids": evidence["source_ids"],
                    **{key: round(value, 6) for key, value in stats.items()},
                }
            )

    if len(density_rows) != len(PROVINCES):
        raise DensityError(f"expected {len(PROVINCES)} density rows, got {len(density_rows)}")
    if not all(row["density_evidence_class"] in {"open_inventory", "donor", "proxy"} for row in density_rows):
        raise DensityError("unexpected density evidence class")
    for province in ("SK", "NS", "PE"):
        row = next(item for item in density_rows if item["province"] == province)
        if row["density_evidence_class"] != "open_inventory":
            raise DensityError(f"{province} must be open_inventory density")

    quantile_columns = (
        "province",
        "density_basis",
        "density_evidence_class",
        "source_ids",
        "n",
        "mean",
        "p05",
        "p10",
        "p25",
        "p50",
        "p75",
        "p90",
        "p95",
        "min",
        "max",
    )
    _write_csv(out_dir / "density_targets.csv", density_rows, DENSITY_COLUMNS)
    _write_csv(out_dir / "density_quantiles.csv", quantile_rows, quantile_columns)
    _write_csv(
        out_dir / "ab_density_structure.csv",
        ab_classes,
        [
            "class_type",
            "class_code",
            "area_share",
            "area_ha",
            "mean_height_m",
            "n_polygons",
            "notes",
        ],
    )

    source_rows = [
        {
            "source_id": "sk_fri_forest_stands",
            "path": "data/derived/national/inventory/provincial_open_inventory_audit_v1/samples/sk_fri_forest_stands_sample.csv",
            "sha256": _sha256(SAMPLE_DIR / "sk_fri_forest_stands_sample.csv"),
            "retrieval_date": RETRIEVAL_DATE,
            "notes": "checked-in 500-stand FRI sample; full layer not redistributed",
        },
        {
            "source_id": "pe_efi_2020",
            "path": "data/derived/national/inventory/provincial_open_inventory_audit_v1/samples/pe_efi_2020_sample.csv",
            "sha256": _sha256(SAMPLE_DIR / "pe_efi_2020_sample.csv"),
            "retrieval_date": RETRIEVAL_DATE,
            "notes": "checked-in 500-cell EFI2020 sample",
        },
        {
            "source_id": "ns_forest_inventory",
            "path": "tmp/pia/ns_forest.dbf",
            "sha256": _sha256(source_root / "ns_forest.dbf"),
            "retrieval_date": RETRIEVAL_DATE,
            "notes": "full open DBF read once to derive density evidence; not redistributed",
        },
        {
            "source_id": "ab_avi_crown",
            "path": "tmp/pia/ab_fgdb/Data/10TM_Offset/AlbertaVegetationInventoryCrown.gdb",
            "sha256": "directory_gdb_not_hashed",
            "retrieval_date": RETRIEVAL_DATE,
            "notes": "full open FGDB read once for density/structure class shares; not redistributed",
        },
        {
            "source_id": "four_province_bucking_pilot_v0",
            "path": "data/derived/national/bucking/four_province_bucking_pilot_v0/tree_list.csv",
            "sha256": _sha256(DONOR_TREE_PATH),
            "retrieval_date": RETRIEVAL_DATE,
            "notes": "measured BC/QC/NB/YT donor tree list; donor density evidence",
        },
    ]
    _write_csv(
        out_dir / "source_provenance.csv",
        source_rows,
        ["source_id", "path", "sha256", "retrieval_date", "notes"],
    )

    targets = {row["province"]: row for row in density_rows}
    (out_dir / "README.md").write_text(
        "\n".join(
            [
                "# Provincial open inventory density v1",
                "",
                "Small committed density evidence tables for E2 v3.  No request,",
                "DUA, restricted data or official OSeCAN input is used.",
                "",
                "| Province | Target stems/ha | Evidence | Basis |",
                "|---|---:|---|---|",
                *[
                    f"| {province} | {targets[province]['density_stems_ha']:.2f} | "
                    f"{targets[province]['density_evidence_class']} | "
                    f"{targets[province]['density_statistic']} |"
                    for province in PROVINCES
                ],
                "",
                "SK uses BA/QMD-implied total stem density with MSTEMS as a",
                "merchantable-stem cross-check.  NS uses TOTBA/AVDI-implied total",
                "stem density with TOTMRVOL as the volume check.  PE uses EFI2020",
                "merchantable trees/ha with gtv as the volume check.",
                "",
                "AB AVI publishes density and structure classes, not stems/ha, so the",
                "AB target is a labelled 1,000 stems/ha proxy.  MB uses the median",
                "measured donor plot density.  ON, NL, NT and NU retain the labelled",
                "1,000 stems/ha proxy.",
                "",
                "Full AB, NS, SK and PE layers are not redistributed; only the small",
                "evidence tables and source hashes are committed.",
                "",
            ]
        ),
        encoding="utf-8",
    )

    manifest = {
        "artifact": ARTIFACT_ID,
        "generated_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "runtime_seconds": round((datetime.now(UTC) - started).total_seconds(), 3),
        "inputs": {
            str(path.relative_to(REPO_ROOT)): _sha256(path)
            for path in (
                SAMPLE_DIR / "sk_fri_forest_stands_sample.csv",
                SAMPLE_DIR / "pe_efi_2020_sample.csv",
                DONOR_TREE_PATH,
                DISTRIBUTION_DIR / "attribute_quantiles.csv",
            )
        },
        "outputs": {
            str(path.relative_to(REPO_ROOT)): _sha256(path)
            for path in (
                out_dir / "density_targets.csv",
                out_dir / "density_quantiles.csv",
                out_dir / "ab_density_structure.csv",
                out_dir / "source_provenance.csv",
                out_dir / "README.md",
            )
        },
        "checks": {
            "nine_provinces": len(density_rows) == len(PROVINCES),
            "open_inventory_density_sk_ns_pe": all(
                targets[province]["density_evidence_class"] == "open_inventory"
                for province in ("SK", "NS", "PE")
            ),
            "proxy_or_donor_gaps_labelled": all(
                targets[province]["density_evidence_class"] in {"donor", "proxy"}
                for province in ("AB", "MB", "ON", "NL", "NT", "NU")
            ),
            "positive_density": all(float(row["density_stems_ha"]) > 0.0 for row in density_rows),
        },
        "labels": {
            "model_estimate": True,
            "observed": False,
            "policy_grade": False,
            "national_claim": False,
            "provenance_tier": "E2c_open_inventory_density_calibrated",
        },
        "no_dua_dependency": True,
        "no_official_osecan_change": True,
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    if not all(manifest["checks"].values()):
        raise DensityError(f"density evidence checks failed: {manifest['checks']}")
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--source-root", type=Path, default=None)
    args = parser.parse_args(argv)
    manifest = build(args.out_dir, source_root=args.source_root)
    print(json.dumps(manifest["checks"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
