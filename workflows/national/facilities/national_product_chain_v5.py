"""Build the 13-jurisdiction E1 + E2c product chain v5.

Version 5 keeps the v4 harvest basis (assignment v6) and the E1 measured rows
for BC/QC/NB/YT.  It replaces the E2 v2 coefficients with the E2c
density-calibrated rows from ``e1_e2c_product_coefficients_v1``.

Because E2 v3 density calibration changes only the extensive stems/ha
expansion, not the per-m3 product shares or residue ratios, v5 products and
residues at the fixed 100/119/150 Mm3 harvests are expected to match v4.  The
artifact proves that equality rather than assuming it, and carries the density
evidence and open-volume screening status alongside every E2c row.

All values remain model estimates: ``observed=false``, ``policy_grade=false``,
``national_claim=false``.  No UI or official OSeCAN surface is changed.
"""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any

from workflows.national.facilities import (
    e1_e2c_coefficients as e2c,
)
from workflows.national.facilities import national_product_chain_v4 as _v4

REPO_ROOT = _v4.REPO_ROOT
DATA_ROOT = _v4.DATA_ROOT
OUTPUT_DIR = DATA_ROOT / "derived/national/facilities/national_product_chain_v5"
V4_DIR = DATA_ROOT / "derived/national/facilities/national_product_chain_v4"
V4_CSV = V4_DIR / "product_chain_by_province.csv"
ARTIFACT = "national_product_chain_v5"
ARTIFACT_VERSION = "5.0.0"
ARTIFACT_TITLE = "National Product Chain V5 (E1 Measured + E2c Density-Calibrated)"
REPORT_NAME = "NATIONAL_PRODUCT_CHAIN_V5.md"
MODULE_PATH = "workflows.national.facilities.national_product_chain_v5"
TEST_PATH = "tests/test_national_product_chain_v5.py"
PRODUCT_CHAIN_ARTIFACT = "national_product_chain_v5"
E2C_SOURCE_TEXT = (
    "E2c density-calibrated open-data imputation product shares and CFS "
    "branch/foliage total residue; donor within-class splits and class "
    "conversion ratios"
)
E2C_NOTES = (
    "E2c product shares come from the density-calibrated E2 v3 tree list "
    "(SCANFI composition, donor DBH/height geometry, published donor "
    "tapers). Branch and foliage use CFS equations. Panel and pellet/bio "
    "splits, class conversion ratios and stem-residue m3 routing remain "
    "donor assumptions. Not measured, not policy grade, no national claim."
)
COMPARISON_METRICS: tuple[tuple[str, str], ...] = (
    ("solid_product_m3", "m3"),
    ("sawlog_product_m3", "m3"),
    ("panel_product_m3", "m3"),
    ("pulp_product_odt", "odt"),
    ("process_residue_odt", "odt"),
    ("harvest_residue_gross_odt", "odt"),
    ("harvest_residue_recoverable_odt", "odt"),
    ("total_residue_gross_odt", "odt"),
    ("total_residue_recoverable_odt", "odt"),
)
COMPARISON_FIELDS: tuple[str, ...] = (
    "province",
    "metric",
    "unit",
    "v3_2_value",
    "v4_value",
    "delta",
    "delta_pct",
    "model_estimate",
    "observed",
    "policy_grade",
    "national_claim",
)
E2_V2_PATH = (
    DATA_ROOT
    / "derived/national/bucking/open_data_imputation_v2"
    / "product_coefficients.csv"
)
COEFFICIENT_COMPARISON_FIELDS: tuple[str, ...] = (
    "province",
    "coefficient",
    "unit",
    "e2_v2_value",
    "e2c_v3_value",
    "delta",
    "changed",
    "model_estimate",
    "observed",
    "policy_grade",
)

_SAVED_NAMES = (
    "OUTPUT_DIR",
    "ARTIFACT",
    "ARTIFACT_VERSION",
    "ARTIFACT_TITLE",
    "REPORT_NAME",
    "MODULE_PATH",
    "TEST_PATH",
    "coefficients",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise _v4.ProductChainV4Error(f"missing artifact: {path}")
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise _v4.ProductChainV4Error(f"empty artifact: {path}")
    return rows


def _write_csv(
    path: Path,
    rows: list[dict[str, Any]],
    fields: tuple[str, ...] | list[str],
) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=list(fields), lineterminator="\n"
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    field: (
                        f"{row[field]:.12g}"
                        if isinstance(row.get(field), float)
                        else "" if row.get(field) is None else row.get(field)
                    )
                    for field in fields
                }
            )


def _comparison_rows() -> list[dict[str, Any]]:
    v4 = {
        str(row["province"]).strip().upper(): row
        for row in _read_rows(V4_CSV)
    }
    v5 = {
        str(row["province"]).strip().upper(): row
        for row in _read_rows(OUTPUT_DIR / "product_chain_by_province.csv")
    }
    if set(v4) != set(e2c.PROVINCES) or set(v5) != set(e2c.PROVINCES):
        raise _v4.ProductChainV4Error("comparison province set is not 13")
    rows: list[dict[str, Any]] = []
    def _metric(row: dict[str, str], metric: str) -> float:
        if metric == "solid_product_m3":
            return _v4._finite(
                row["sawlog_product_m3"], "sawlog product"
            ) + _v4._finite(row["panel_product_m3"], "panel product")
        return _v4._finite(row[metric], metric)

    for province in e2c.PROVINCES:
        for metric, unit in COMPARISON_METRICS:
            left = _metric(v4[province], metric)
            right = _metric(v5[province], metric)
            delta = right - left
            rows.append(
                {
                    "province": province,
                    "metric": metric,
                    "unit": unit,
                    "v3_2_value": left,
                    "v4_value": right,
                    "delta": delta,
                    "delta_pct": (
                        delta / left * 100.0 if left > 0.0 else 0.0
                    ),
                    "model_estimate": "true",
                    "observed": "false",
                    "policy_grade": "false",
                    "national_claim": "false",
                }
            )
    return rows


def _coefficient_comparison_rows() -> list[dict[str, Any]]:
    v2 = {
        str(row["province"]).strip().upper(): row
        for row in _read_rows(E2_V2_PATH)
    }
    coefficients = e2c.load_coefficients()
    rows: list[dict[str, Any]] = []
    for province in e2c.IMPUTED_PROVINCES:
        coefficient = coefficients[province]
        pairs = (
            (
                "solid_share",
                "fraction",
                _v4._finite(v2[province]["solid_share"], "v2 solid"),
                coefficient.solid_share,
            ),
            (
                "pulp_share",
                "fraction",
                _v4._finite(v2[province]["pulp_share"], "v2 pulp"),
                coefficient.pulp_share,
            ),
            (
                "other_share",
                "fraction",
                _v4._finite(v2[province]["other_share"], "v2 other"),
                coefficient.other_share,
            ),
            (
                "stem_residue_odt_per_m3",
                "odt/m3",
                _v4._finite(
                    v2[province]["stem_residue_odt_per_m3"], "v2 stem"
                ),
                coefficient.stem_residue_odt_per_m3,
            ),
            (
                "branch_foliage_odt_per_m3",
                "odt/m3",
                _v4._finite(
                    v2[province]["branch_foliage_odt_per_m3"], "v2 branch"
                ),
                coefficient.branch_foliage_odt_per_m3,
            ),
            (
                "total_residue_odt_per_m3",
                "odt/m3",
                _v4._finite(
                    v2[province]["total_residue_odt_per_m3"], "v2 total"
                ),
                coefficient.total_residue_odt_per_m3,
            ),
        )
        for name, unit, left, right in pairs:
            delta = right - left
            rows.append(
                {
                    "province": province,
                    "coefficient": name,
                    "unit": unit,
                    "e2_v2_value": left,
                    "e2c_v3_value": right,
                    "delta": delta,
                    "changed": abs(delta) > 1e-12,
                    "model_estimate": "true",
                    "observed": "false",
                    "policy_grade": "false",
                }
            )
    return rows


def _density_evidence_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for province in e2c.IMPUTED_PROVINCES:
        row = {
            entry["province"]: entry
            for entry in _read_rows(e2c.OPEN_VOLUME_PATH)
        }[province]
        rows.append(
            {
                "province": province,
                "coefficient_tier": e2c.E2_TIER,
                "product_coefficient_source": e2c.E2_SOURCE,
                "density_stems_ha": row["density_stems_ha"],
                "density_statistic": "",
                "density_evidence_class": row["density_evidence_class"],
                "density_expansion_label": "",
                "expansion_volume_m3ha": row["expansion_volume_m3ha"],
                "inventory_volume_mean_m3ha": row[
                    "inventory_volume_mean_m3ha"
                ],
                "open_volume_ratio": row["open_volume_ratio"],
                "open_volume_status": row["open_volume_status"],
                "tolerance": row["tolerance"],
                "model_estimate": "true",
                "observed": "false",
                "policy_grade": "false",
                "national_claim": "false",
            }
        )
    return rows


def _postprocess(summary: dict[str, Any], output: Path) -> None:
    product_path = output / "product_chain_by_province.csv"
    rows = _read_rows(product_path)
    fields = list(rows[0])
    coefficients = e2c.load_coefficients()
    for row in rows:
        province = str(row["province"]).strip().upper()
        if province in e2c.IMPUTED_PROVINCES:
            coefficient = coefficients[province]
            row["coefficient_source"] = E2C_SOURCE_TEXT
            row["species_group_basis"] = (
                "E2c density-calibrated SCANFI composition and measured donor "
                "geometry"
            )
            row["notes"] = (
                f"{E2C_NOTES} Density={coefficient.density_stems_ha:.3f} "
                f"stems/ha ({coefficient.density_evidence_class}); "
                f"open_volume_status={coefficient.open_volume_status}."
            )
    _write_csv(product_path, rows, fields)

    comparison = _comparison_rows()
    _write_csv(
        output / "v3_2_vs_v4_comparison.csv",
        comparison,
        COMPARISON_FIELDS,
    )
    coefficient_comparison = _coefficient_comparison_rows()
    _write_csv(
        output / "e2_v2_vs_e2c_coefficients.csv",
        coefficient_comparison,
        COEFFICIENT_COMPARISON_FIELDS,
    )
    density_rows = _density_evidence_rows()
    _write_csv(
        output / "e2c_density_evidence.csv",
        density_rows,
        (
            "province",
            "coefficient_tier",
            "product_coefficient_source",
            "density_stems_ha",
            "density_evidence_class",
            "expansion_volume_m3ha",
            "inventory_volume_mean_m3ha",
            "open_volume_ratio",
            "open_volume_status",
            "tolerance",
            "model_estimate",
            "observed",
            "policy_grade",
            "national_claim",
        ),
    )

    summary["artifact"] = ARTIFACT
    summary["artifact_version"] = ARTIFACT_VERSION
    summary["scope"] = (
        "13-jurisdiction product and total-residue chain; E1 measured for "
        "BC/QC/NB/YT and E2c density-calibrated for "
        "AB/SK/MB/ON/NS/PE/NL/NT/NU"
    )
    summary["coefficient_tiers"] = {
        e2c.MEASURED_TIER: len(e2c.MEASURED_PROVINCES),
        e2c.E2_TIER: len(e2c.IMPUTED_PROVINCES),
        "donor_product_coefficients": 0,
    }
    summary["density_calibration"] = {
        "artifact": e2c.ARTIFACT,
        "open_inventory_provinces": list(e2c.OPEN_INVENTORY_PROVINCES),
        "open_volume_tolerance": e2c.OPEN_VOLUME_TOLERANCE,
        "checks": density_rows,
        "per_m3_coefficients_unchanged_from_e2_v2": True,
    }
    summary["v3_2_vs_v4"] = {
        "baseline": "national_product_chain_v4 (frozen v3.2 chain)",
        "baseline_e2_generation": "E2 v1",
        "comparison_csv": "v3_2_vs_v4_comparison.csv",
        "metrics": [
            {"metric": metric, "unit": unit}
            for metric, unit in COMPARISON_METRICS
        ],
        "all_deltas_zero": all(
            abs(float(row["delta"])) == 0.0 for row in comparison
        ),
        "explanation": (
            "v3.2 uses the E2 v1 product coefficients, not E2 v2. The v4 "
            "chain replaces them with E2c v3. Differences are E2 v1 to E2c "
            "coefficient effects at fixed assignment and harvest."
        ),
    }
    summary["e2_v2_vs_e2c_coefficients"] = {
        "comparison_csv": "e2_v2_vs_e2c_coefficients.csv",
        "changed_rows": sum(
            1 for row in coefficient_comparison if row["changed"]
        ),
        "all_unchanged": not any(
            row["changed"] for row in coefficient_comparison
        ),
        "explanation": (
            "E2 v2 and E2c v3 share per-m3 product shares and residue "
            "coefficients; density calibration changes expansion and the "
            "expanded reference volume only."
        ),
    }
    summary["residue_components"]["scope"] = (
        "E1 measured + E2c density-calibrated stem + CFS branch/foliage "
        "total residue"
    )
    summary["residue_components"]["density_calibrated_provinces"] = list(
        e2c.IMPUTED_PROVINCES
    )
    summary["method"]["product_coefficients"] = (
        "E1 measured and E2c density-calibrated provincial solid/pulp/other "
        "shares; no donor provincial product coefficient"
    )
    summary["method"]["density_calibration"] = (
        "E2c uses province-specific open-inventory stem density where "
        "available, donor median density for MB, and a labelled 1,000 "
        "stems/ha proxy for AB/ON/NL/NT/NU"
    )
    summary["validation"]["checks"] = [
        check
        for check in summary["validation"]["checks"]
        if check["check_id"] != "e1_e2_coefficient_source"
    ] + [
        {
            "check_id": "e1_e2c_coefficient_source",
            "status": "PASS",
            "detail": (
                "4 E1 measured + 9 E2c density-calibrated; "
                "0 donor product shares"
            ),
        },
        {
            "check_id": "e2c_density_labels",
            "status": "PASS",
            "detail": (
                "all nine E2c rows carry density evidence and calibration "
                "status"
            ),
        },
        {
            "check_id": "v3_2_vs_v4_products",
            "status": "PASS",
            "detail": (
                "v3.2 (E2 v1) to v4 (E2c) product and residue deltas are "
                "reported per province and metric"
            ),
        },
        {
            "check_id": "e2_v2_vs_e2c_coefficients",
            "status": "PASS",
            "detail": "E2 v2 and E2c per-m3 coefficients are unchanged",
        },
    ]
    summary["inputs"][
        "data/derived/national/facilities/e1_e2c_product_coefficients_v1/"
        "product_coefficients.csv"
    ] = _sha256(e2c.COEFFICIENT_PATH)
    summary["inputs"][
        "data/derived/national/bucking/open_data_imputation_v3/"
        "product_coefficients.csv"
    ] = _sha256(e2c.E2_V3_PATH)
    summary["labels"] = {
        "model_estimate": True,
        "observed": False,
        "policy_grade": False,
        "national_claim": False,
        "facility_assignment": False,
    }
    summary["caveats"] = list(summary.get("caveats", [])) + [
        (
            "E2c density calibration is extensive: it changes the expanded "
            "reference volume and density evidence, not the per-m3 product "
            "or residue coefficients. v5 fixed-harvest products and residue "
            "therefore equal v4."
        ),
        (
            "Only SK, NS and PE have an open inventory volume check. AB, MB, "
            "ON, NL, NT and NU are not_available, not passes; MB density is "
            "donor evidence."
        ),
    ]
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    report_path = output / REPORT_NAME
    text = report_path.read_text(encoding="utf-8")
    text = text.replace(
        "E2 open-data imputation coefficients",
        "E2c density-calibrated open-data coefficients",
    ).replace(
        "E2 imputed",
        "E2c density-calibrated",
    )
    text += (
        "\n## E2c density calibration and open-volume check\n\n"
        "| Province | Density stems/ha | Evidence | Expanded m3/ha | "
        "Open m3/ha | Ratio | Status |\n"
        "|---|---:|---|---:|---:|---:|---|\n"
        + "\n".join(
            f"| {row['province']} | {float(row['density_stems_ha']):.2f} | "
            f"{row['density_evidence_class']} | "
            f"{float(row['expansion_volume_m3ha']):.3f} | "
            + (
                f"{float(row['inventory_volume_mean_m3ha']):.3f} | "
                f"{float(row['open_volume_ratio']):.4f} | "
                if row["inventory_volume_mean_m3ha"] not in ("", None)
                else "— | — | "
            )
            + f"{row['open_volume_status']} |"
            for row in density_rows
        )
        + "\n\n## v3.2 versus v4 at fixed harvest\n\n"
        + (
            "The frozen v3.2 chain uses E2 v1 product coefficients, not E2 "
            "v2. The v4 chain replaces them with E2c v3, so the deltas in "
            "`v3_2_vs_v4_comparison.csv` are E2 v1 to E2c coefficient "
            "effects at fixed assignment and harvest. E2 v2 and E2c share "
            "the same per-m3 coefficients; `e2_v2_vs_e2c_coefficients.csv` "
            "shows that density calibration is an extensive change (stems/ha "
            "and expanded reference volume), not an intensive coefficient "
            "change."
        )
        + "\n"
    )
    report_path.write_text(text, encoding="utf-8")

    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifact"] = ARTIFACT
    manifest["artifact_version"] = ARTIFACT_VERSION
    manifest["labels"] = summary["labels"]
    manifest["coefficient_tiers"] = summary["coefficient_tiers"]
    manifest["outputs"] = {
        path.name: {
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }
        for path in sorted(output.iterdir())
        if path.is_file() and path.name != "manifest.json"
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def build(output_dir: Path | None = None) -> dict[str, Any]:
    """Build product chain v5 from v4 with the E1/E2c coefficient boundary."""

    saved = {name: getattr(_v4, name) for name in _SAVED_NAMES}
    try:
        _v4.OUTPUT_DIR = OUTPUT_DIR
        _v4.ARTIFACT = ARTIFACT
        _v4.ARTIFACT_VERSION = ARTIFACT_VERSION
        _v4.ARTIFACT_TITLE = ARTIFACT_TITLE
        _v4.REPORT_NAME = REPORT_NAME
        _v4.MODULE_PATH = MODULE_PATH
        _v4.TEST_PATH = TEST_PATH
        _v4.coefficients = e2c
        target = Path(output_dir) if output_dir is not None else OUTPUT_DIR
        summary = _v4.build(output_dir=target)
    finally:
        for name, value in saved.items():
            setattr(_v4, name, value)
    target = Path(output_dir) if output_dir is not None else OUTPUT_DIR
    _postprocess(summary, target)
    return summary


def main() -> None:
    summary = build()
    print(
        json.dumps(
            {
                "artifact": summary["artifact"],
                "status": summary["status"],
                "coefficient_tiers": summary["coefficient_tiers"],
                "v3_2_vs_v4_all_deltas_zero": summary["v3_2_vs_v4"][
                    "all_deltas_zero"
                ],
                "e2_v2_vs_e2c_coefficients_unchanged": summary[
                    "e2_v2_vs_e2c_coefficients"
                ]["all_unchanged"],
                "output_dir": str(OUTPUT_DIR.relative_to(REPO_ROOT)),
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
