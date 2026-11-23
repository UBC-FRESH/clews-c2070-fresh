"""Trusted 13-jurisdiction E1 + E2c product-coefficient boundary.

E1 measured rows (BC/QC/NB/YT) come from the four-province bucking pilot.  E2c
rows (AB/SK/MB/ON/NS/PE/NL/NT/NU) come from ``open_data_imputation_v3``, the
density-calibrated E2 build.  This module merges them into one 13-row artifact
so no downstream stage can silently fall back to a donor product coefficient.

Fail-closed checks:

* exactly the expected 13 jurisdictions and the 4 + 9 E1/E2c split;
* E1 is ``measured_bucking_pilot`` and E2 is
  ``e2c_open_inventory_density_calibrated``;
* product shares sum to one and stem + branch/foliage = total residue;
* 0.35/0.60/0.75 recoverable values equal gross total times the factor;
* the three open-inventory provinces stay within the +/-50% open-volume
  screening tolerance;
* every row carries ``model_estimate=true``, ``observed=false``,
  ``policy_grade=false`` and ``national_claim=false``.

Within-class SAW/PANEL and PELLET/BIO splits and mill conversion ratios are not
provincial product coefficients.  They remain documented donor assumptions
owned by the product-chain builder.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
V1_PATH = (
    REPO_ROOT
    / "data/derived/national/bucking/open_data_imputation_v1"
    / "product_coefficients.csv"
)
E2_V3_DIR = (
    REPO_ROOT / "data/derived/national/bucking/open_data_imputation_v3"
)
E2_V3_PATH = E2_V3_DIR / "product_coefficients.csv"
DENSITY_PATH = E2_V3_DIR / "density_calibration.csv"
VOLUME_COMPARISON_PATH = E2_V3_DIR / "comparison_v2_v3.csv"
OUTPUT_DIR = (
    REPO_ROOT
    / "data/derived/national/facilities/e1_e2c_product_coefficients_v1"
)
COEFFICIENT_PATH = OUTPUT_DIR / "product_coefficients.csv"
OPEN_VOLUME_PATH = OUTPUT_DIR / "open_volume_comparison.csv"
SUMMARY_PATH = OUTPUT_DIR / "summary.json"
REPORT_PATH = OUTPUT_DIR / "report.md"
ARTIFACT = "e1_e2c_product_coefficients_v1"
ARTIFACT_VERSION = "1.0.0"
MODULE_PATH = "workflows.national.facilities.e1_e2c_coefficients"

PROVINCES: tuple[str, ...] = (
    "BC",
    "AB",
    "SK",
    "MB",
    "ON",
    "QC",
    "NB",
    "NS",
    "PE",
    "NL",
    "YT",
    "NT",
    "NU",
)
MEASURED_PROVINCES: tuple[str, ...] = ("BC", "QC", "NB", "YT")
IMPUTED_PROVINCES: tuple[str, ...] = (
    "AB",
    "SK",
    "MB",
    "ON",
    "NS",
    "PE",
    "NL",
    "NT",
    "NU",
)
OPEN_INVENTORY_PROVINCES: tuple[str, ...] = ("SK", "NS", "PE")
MEASURED_TIER = "measured_bucking_pilot"
E2_TIER = "E2c_open_inventory_density_calibrated"
E1_SOURCE = "E1_four_province_bucking_pilot_measured"
E2_SOURCE = "E2c_density_calibrated_open_data_imputation"
SUM_TOLERANCE = 1e-9
RECOVERY_SCENARIOS: tuple[float, ...] = (0.35, 0.60, 0.75)
OPEN_VOLUME_TOLERANCE = 0.50
EVIDENCE_LABEL = "model_estimate"

FIELDS: tuple[str, ...] = (
    "province",
    "province_name",
    "coefficient_tier",
    "product_coefficient_source",
    "residue_coefficient_source",
    "evidence_label",
    "n_trees",
    "reference_recovered_m3_expanded",
    "solid_share",
    "pulp_share",
    "other_share",
    "stem_residue_odt_per_m3",
    "branch_foliage_odt_per_m3",
    "total_residue_odt_per_m3",
    "stem_residue_m3_per_m3",
    "recoverable_odt_per_m3_r035",
    "recoverable_odt_per_m3_r060",
    "recoverable_odt_per_m3_r075",
    "density_stems_ha",
    "density_statistic",
    "density_basis",
    "density_evidence_class",
    "density_expansion_label",
    "density_calibrated",
    "expansion_volume_m3ha",
    "inventory_volume_mean_m3ha",
    "open_volume_ratio",
    "open_volume_status",
    "model_estimate",
    "observed",
    "policy_grade",
    "national_claim",
)
OPEN_VOLUME_FIELDS: tuple[str, ...] = (
    "province",
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
)


class E1E2cCoefficientError(RuntimeError):
    """Raised when a 13-jurisdiction E1/E2c coefficient row is not trusted."""


@dataclass(frozen=True, slots=True)
class ProductCoefficients:
    """One jurisdiction's trusted E1 or E2c coefficients."""

    province: str
    province_name: str
    tier: str
    solid_share: float
    pulp_share: float
    other_share: float
    stem_residue_odt_per_m3: float
    branch_foliage_odt_per_m3: float
    total_residue_odt_per_m3: float
    stem_residue_m3_per_m3: float
    recoverable_odt_per_m3_r035: float
    recoverable_odt_per_m3_r060: float
    recoverable_odt_per_m3_r075: float
    n_trees: int
    density_stems_ha: float | None = None
    density_statistic: str = ""
    density_basis: str = ""
    density_evidence_class: str = ""
    density_expansion_label: str = ""
    expansion_volume_m3ha: float | None = None
    inventory_volume_mean_m3ha: float | None = None
    open_volume_ratio: float | None = None
    open_volume_status: str = "not_applicable"

    @property
    def is_measured(self) -> bool:
        return self.tier == MEASURED_TIER

    @property
    def is_density_calibrated(self) -> bool:
        return self.tier == E2_TIER


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_rows(path: Path, label: str) -> list[dict[str, str]]:
    if not path.is_file():
        raise E1E2cCoefficientError(f"{label} is missing: {path}")
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise E1E2cCoefficientError(f"{label} is empty: {path}")
    return rows


def _write_rows(
    path: Path,
    rows: Sequence[Mapping[str, Any]],
    fields: Sequence[str],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields), lineterminator="\n")
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


def _finite(value: Any, label: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as error:
        raise E1E2cCoefficientError(
            f"{label} is not numeric: {value!r}"
        ) from error
    if not math.isfinite(parsed) or parsed < 0.0:
        raise E1E2cCoefficientError(
            f"{label} is not finite and nonnegative: {parsed!r}"
        )
    return parsed


def _parse_bool(value: Any, label: str) -> bool:
    text = str(value).strip().lower()
    if text == "true":
        return True
    if text == "false":
        return False
    raise E1E2cCoefficientError(f"{label} is not a boolean: {value!r}")


def _require_close(left: float, right: float, label: str) -> None:
    if abs(left - right) > SUM_TOLERANCE * max(abs(left), abs(right), 1.0):
        raise E1E2cCoefficientError(f"{label} mismatch: {left!r} != {right!r}")


def _require_labels(row: Mapping[str, Any], province: str) -> None:
    for field in ("model_estimate", "observed", "policy_grade", "national_claim"):
        value = _parse_bool(row[field], f"{province} {field}")
        expected = field == "model_estimate"
        if value is not expected:
            raise E1E2cCoefficientError(
                f"{province}: {field} is {value}, expected {expected}"
            )


def _indexed(
    path: Path, label: str, key: str = "province"
) -> dict[str, dict[str, str]]:
    rows = _read_rows(path, label)
    indexed: dict[str, dict[str, str]] = {}
    for row in rows:
        province = str(row[key]).strip().upper()
        if province in indexed:
            raise E1E2cCoefficientError(f"{label} repeats {province}")
        indexed[province] = row
    return indexed


def build(output_dir: Path = OUTPUT_DIR) -> dict[str, Any]:
    """Merge E1 measured and E2c density-calibrated rows into one artifact."""

    generated_utc = datetime.now(UTC).replace(microsecond=0).isoformat()
    v1 = _indexed(V1_PATH, "E1/E2 v1 product coefficients")
    e2 = _indexed(E2_V3_PATH, "E2 v3 product coefficients")
    density = _indexed(DENSITY_PATH, "E2 v3 density calibration")
    volume = _indexed(VOLUME_COMPARISON_PATH, "E2 v3 volume comparison")
    if set(v1) != set(PROVINCES):
        raise E1E2cCoefficientError(
            f"v1 province set is not 13: {sorted(v1)}"
        )
    if set(e2) != set(IMPUTED_PROVINCES):
        raise E1E2cCoefficientError(
            f"E2 v3 province set is not nine: {sorted(e2)}"
        )
    for name, table in (
        ("density", density),
        ("volume comparison", volume),
    ):
        if set(table) != set(IMPUTED_PROVINCES):
            raise E1E2cCoefficientError(
                f"{name} province set is not nine: {sorted(table)}"
            )

    rows: list[dict[str, Any]] = []
    open_volume_rows: list[dict[str, Any]] = []
    for province in PROVINCES:
        if province in MEASURED_PROVINCES:
            source = v1[province]
            if str(source["coefficient_tier"]).strip() != MEASURED_TIER:
                raise E1E2cCoefficientError(
                    f"{province}: v1 tier is not {MEASURED_TIER}"
                )
            if (
                str(source["product_coefficient_source"]).strip()
                != E1_SOURCE
            ):
                raise E1E2cCoefficientError(
                    f"{province}: v1 product source is not E1 measured"
                )
            row: dict[str, Any] = {
                "province": province,
                "province_name": str(source["province_name"]).strip(),
                "coefficient_tier": MEASURED_TIER,
                "product_coefficient_source": E1_SOURCE,
                "residue_coefficient_source": str(
                    source["residue_coefficient_source"]
                ).strip(),
                "evidence_label": EVIDENCE_LABEL,
                "n_trees": int(source["n_trees"]),
                "reference_recovered_m3_expanded": _finite(
                    source["reference_recovered_m3_expanded"],
                    f"{province} reference recovered m3",
                ),
                "density_stems_ha": None,
                "density_statistic": "not_applicable",
                "density_basis": "E1 measured bucking pilot; no density target",
                "density_evidence_class": "measured",
                "density_expansion_label": "e1_measured_tree_list",
                "density_calibrated": "false",
                "expansion_volume_m3ha": None,
                "inventory_volume_mean_m3ha": None,
                "open_volume_ratio": None,
                "open_volume_status": "not_applicable",
            }
        else:
            source = e2[province]
            if str(source["provenance_tier"]).strip() != E2_TIER:
                raise E1E2cCoefficientError(
                    f"{province}: E2 v3 provenance tier is not {E2_TIER}"
                )
            density_row = density[province]
            volume_row = volume[province]
            evidence_class = str(
                density_row["density_evidence_class"]
            ).strip()
            if evidence_class not in {"open_inventory", "donor", "proxy"}:
                raise E1E2cCoefficientError(
                    f"{province}: unexpected density evidence {evidence_class!r}"
                )
            expansion_volume = _finite(
                volume_row["v3_taper_total_m3_expanded"],
                f"{province} expansion volume",
            )
            status = str(volume_row["volume_context_status"]).strip()
            inventory_volume_raw = str(
                volume_row["inventory_volume_mean_m3ha"]
            ).strip()
            inventory_volume = (
                _finite(inventory_volume_raw, f"{province} inventory volume")
                if status != "not_available" and inventory_volume_raw
                else None
            )
            ratio = (
                expansion_volume / inventory_volume
                if inventory_volume is not None and inventory_volume > 0.0
                else None
            )
            expected_status = (
                "not_available" if ratio is None else "within_50pct"
            )
            if status != expected_status:
                raise E1E2cCoefficientError(
                    f"{province}: volume status {status!r} is not "
                    f"{expected_status!r}"
                )
            if ratio is not None and not (
                1.0 - OPEN_VOLUME_TOLERANCE
                <= ratio
                <= 1.0 + OPEN_VOLUME_TOLERANCE
            ):
                raise E1E2cCoefficientError(
                    f"{province}: open-volume ratio {ratio!r} is outside "
                    "+/-50%"
                )
            row = {
                "province": province,
                "province_name": str(source["province_name"]).strip(),
                "coefficient_tier": E2_TIER,
                "product_coefficient_source": E2_SOURCE,
                "residue_coefficient_source": (
                    "E2c_density_calibrated_stem_plus_CFS_branch_foliage"
                ),
                "evidence_label": EVIDENCE_LABEL,
                "n_trees": int(source["n_trees"]),
                "reference_recovered_m3_expanded": _finite(
                    source["reference_recovered_m3_expanded"],
                    f"{province} reference recovered m3",
                ),
                "density_stems_ha": _finite(
                    density_row["density_stems_ha"],
                    f"{province} density",
                ),
                "density_statistic": str(
                    density_row["density_statistic"]
                ).strip(),
                "density_basis": str(density_row["density_basis"]).strip(),
                "density_evidence_class": evidence_class,
                "density_expansion_label": str(
                    density_row["expansion_label"]
                ).strip(),
                "density_calibrated": "true",
                "expansion_volume_m3ha": expansion_volume,
                "inventory_volume_mean_m3ha": inventory_volume,
                "open_volume_ratio": ratio,
                "open_volume_status": status,
            }
            open_volume_rows.append(
                {
                    "province": province,
                    "density_stems_ha": row["density_stems_ha"],
                    "density_evidence_class": evidence_class,
                    "expansion_volume_m3ha": expansion_volume,
                    "inventory_volume_mean_m3ha": inventory_volume,
                    "open_volume_ratio": ratio,
                    "open_volume_status": status,
                    "tolerance": OPEN_VOLUME_TOLERANCE,
                    "model_estimate": "true",
                    "observed": "false",
                    "policy_grade": "false",
                }
            )
        solid = _finite(source["solid_share"], f"{province} solid share")
        pulp = _finite(source["pulp_share"], f"{province} pulp share")
        other = _finite(source["other_share"], f"{province} other share")
        reference = row["reference_recovered_m3_expanded"]
        if reference > 0.0:
            _require_close(solid + pulp + other, 1.0, f"{province} shares")
        stem = _finite(
            source["stem_residue_odt_per_m3"], f"{province} stem residue"
        )
        branch = _finite(
            source["branch_foliage_odt_per_m3"],
            f"{province} branch/foliage",
        )
        total = _finite(
            source["total_residue_odt_per_m3"], f"{province} total residue"
        )
        _require_close(total, stem + branch, f"{province} residue conservation")
        for factor in RECOVERY_SCENARIOS:
            value = _finite(
                source[
                    "recoverable_odt_per_m3_r"
                    f"{round(factor * 100):03d}"
                ],
                f"{province} recoverable at {factor}",
            )
            _require_close(
                value,
                total * factor,
                f"{province} recovery at {factor}",
            )
            row[
                "recoverable_odt_per_m3_r"
                f"{round(factor * 100):03d}"
            ] = value
        row.update(
            {
                "solid_share": solid,
                "pulp_share": pulp,
                "other_share": other,
                "stem_residue_odt_per_m3": stem,
                "branch_foliage_odt_per_m3": branch,
                "total_residue_odt_per_m3": total,
                "stem_residue_m3_per_m3": _finite(
                    source["stem_residue_m3_per_m3"],
                    f"{province} stem residue m3",
                ),
                "model_estimate": "true",
                "observed": "false",
                "policy_grade": "false",
                "national_claim": "false",
            }
        )
        _require_labels(row, province)
        rows.append(row)

    if {row["province"] for row in rows} != set(PROVINCES):
        raise E1E2cCoefficientError("merged province set is not 13")
    checked = {
        row["province"]
        for row in open_volume_rows
        if row["open_volume_status"] == "within_50pct"
    }
    if checked != set(OPEN_INVENTORY_PROVINCES):
        raise E1E2cCoefficientError(
            f"open-volume checks are not exactly SK/NS/PE: {sorted(checked)}"
        )
    if len(open_volume_rows) != len(IMPUTED_PROVINCES):
        raise E1E2cCoefficientError(
            "open-volume table must carry all nine E2 jurisdictions"
        )

    output = Path(output_dir)
    _write_rows(output / "product_coefficients.csv", rows, FIELDS)
    _write_rows(
        output / "open_volume_comparison.csv",
        open_volume_rows,
        OPEN_VOLUME_FIELDS,
    )
    summary: dict[str, Any] = {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "status": "pass",
        "generated_utc": generated_utc,
        "scope": (
            "13-jurisdiction product coefficients: E1 measured for "
            "BC/QC/NB/YT and E2c density-calibrated for "
            "AB/SK/MB/ON/NS/PE/NL/NT/NU"
        ),
        "labels": {
            "model_estimate": True,
            "observed": False,
            "policy_grade": False,
            "national_claim": False,
        },
        "coefficient_tiers": {
            MEASURED_TIER: len(MEASURED_PROVINCES),
            E2_TIER: len(IMPUTED_PROVINCES),
            "donor_product_coefficients": 0,
        },
        "measured_provinces": list(MEASURED_PROVINCES),
        "density_calibrated_provinces": list(IMPUTED_PROVINCES),
        "donor_product_provinces": [],
        "open_inventory_provinces": list(OPEN_INVENTORY_PROVINCES),
        "open_volume_tolerance": OPEN_VOLUME_TOLERANCE,
        "open_volume_checks": open_volume_rows,
        "no_donor_product_coefficients": True,
        "province_coefficients": rows,
        "validation": {
            "checks": [
                {
                    "check_id": "thirteen_jurisdictions",
                    "status": "PASS",
                    "detail": "13 rows; 4 E1 measured + 9 E2c",
                },
                {
                    "check_id": "coefficient_source",
                    "status": "PASS",
                    "detail": "E1 or E2c on every row; 0 donor product shares",
                },
                {
                    "check_id": "product_share_conservation",
                    "status": "PASS",
                    "detail": "solid + pulp + other = 1",
                },
                {
                    "check_id": "residue_conservation",
                    "status": "PASS",
                    "detail": "stem + branch/foliage = total; scenarios = gross * factor",
                },
                {
                    "check_id": "open_volume",
                    "status": "PASS",
                    "detail": "SK/NS/PE within +/-50% of open inventory volume",
                },
                {
                    "check_id": "no_negative_or_nan",
                    "status": "PASS",
                    "detail": "numeric fields finite and nonnegative",
                },
            ]
        },
        "caveats": [
            (
                "E2c is an open-data density calibration, not measured "
                "inventory. Three provinces use open stem density, one uses "
                "donor plot density and five retain a 1,000 stems/ha proxy."
            ),
            (
                "Per-m3 product and residue coefficients are unchanged from "
                "E2 v2; density calibration changes the expanded reference "
                "volume, not the intensive product/residue ratios."
            ),
            (
                "No donor provincial product coefficient is applied. "
                "Within-class splits and mill conversion ratios remain "
                "labelled donor assumptions."
            ),
            (
                "All values are model estimates: observed=false, "
                "policy_grade=false, national_claim=false."
            ),
        ],
        "inputs": {
            str(path.relative_to(REPO_ROOT)): _sha256(path)
            for path in (
                V1_PATH,
                E2_V3_PATH,
                DENSITY_PATH,
                VOLUME_COMPARISON_PATH,
            )
        },
        "reproduce": f".venv/bin/python -m {MODULE_PATH}",
    }
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# E1 measured + E2c density-calibrated product coefficients",
        "",
        (
            "13 jurisdictions: BC/QC/NB/YT carry E1 measured coefficients; "
            "AB/SK/MB/ON/NS/PE/NL/NT/NU carry E2c density-calibrated "
            "open-data coefficients. No donor provincial product share is "
            "applied."
        ),
        "",
        "| Province | Tier | Solid | Pulp | Other | Total residue ODT/m3 | Density stems/ha | Density evidence |",
        "|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        density_text = (
            f"{float(row['density_stems_ha']):.2f}"
            if row["density_stems_ha"] is not None
            else "—"
        )
        lines.append(
            f"| {row['province']} | {row['coefficient_tier']} | "
            f"{row['solid_share']:.4f} | {row['pulp_share']:.4f} | "
            f"{row['other_share']:.4f} | "
            f"{row['total_residue_odt_per_m3']:.6f} | {density_text} | "
            f"{row['density_evidence_class']} |"
        )
    lines += [
        "",
        "## Open-volume screening",
        "",
        "| Province | Expanded volume m3/ha | Open inventory volume m3/ha | Ratio | Status |",
        "|---|---:|---:|---:|---|",
    ]
    for row in open_volume_rows:
        volume_text = (
            f"{row['inventory_volume_mean_m3ha']:.3f}"
            if row["inventory_volume_mean_m3ha"] is not None
            else "—"
        )
        ratio_text = (
            f"{row['open_volume_ratio']:.4f}"
            if row["open_volume_ratio"] is not None
            else "—"
        )
        lines.append(
            f"| {row['province']} | {row['expansion_volume_m3ha']:.3f} | "
            f"{volume_text} | {ratio_text} | {row['open_volume_status']} |"
        )
    lines += [
        "",
        (
            "No open volume field exists for AB, MB, ON, NL, NT or NU; their "
            "status is `not_available`, not a pass. MB density is donor "
            "evidence, not Manitoba inventory density."
        ),
        "",
        (
            "Labels: `model_estimate=true`, `observed=false`, "
            "`policy_grade=false`, `national_claim=false`."
        ),
        "",
        (
            "Per-m3 coefficients are unchanged from E2 v2; only the expanded "
            "reference volume and density evidence change."
        ),
        "",
    ]
    (output / "report.md").write_text("\n".join(lines), encoding="utf-8")
    outputs = {
        path.name: {"bytes": path.stat().st_size, "sha256": _sha256(path)}
        for path in sorted(output.iterdir())
        if path.is_file() and path.name != "manifest.json"
    }
    manifest = {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": generated_utc,
        "labels": summary["labels"],
        "inputs": summary["inputs"],
        "outputs": outputs,
        "validation": summary["validation"],
        "reproduce": summary["reproduce"],
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary


def _read_coefficient_rows(path: Path) -> list[dict[str, str]]:
    return _read_rows(path, "E1/E2c coefficient table")


def load_coefficients(
    path: Path = COEFFICIENT_PATH,
) -> dict[str, ProductCoefficients]:
    """Parse the 13 merged rows into trusted form."""

    rows = _read_coefficient_rows(path)
    by_province: dict[str, ProductCoefficients] = {}
    for row in rows:
        province = str(row["province"]).strip().upper()
        if province not in PROVINCES:
            raise E1E2cCoefficientError(f"unexpected province {province!r}")
        if province in by_province:
            raise E1E2cCoefficientError(f"duplicate province {province}")
        tier = str(row["coefficient_tier"]).strip()
        expected_tier = (
            MEASURED_TIER if province in MEASURED_PROVINCES else E2_TIER
        )
        if tier != expected_tier:
            raise E1E2cCoefficientError(
                f"{province}: tier {tier!r} is not {expected_tier!r}"
            )
        product_source = str(row["product_coefficient_source"]).strip()
        expected_source = (
            E1_SOURCE if tier == MEASURED_TIER else E2_SOURCE
        )
        if product_source != expected_source:
            raise E1E2cCoefficientError(
                f"{province}: product source {product_source!r} is not "
                f"{expected_source!r}"
            )
        _require_labels(row, province)
        solid = _finite(row["solid_share"], f"{province} solid share")
        pulp = _finite(row["pulp_share"], f"{province} pulp share")
        other = _finite(row["other_share"], f"{province} other share")
        reference = _finite(
            row["reference_recovered_m3_expanded"],
            f"{province} reference recovered m3",
        )
        if reference > 0.0:
            _require_close(solid + pulp + other, 1.0, f"{province} shares")
        stem = _finite(
            row["stem_residue_odt_per_m3"], f"{province} stem residue"
        )
        branch = _finite(
            row["branch_foliage_odt_per_m3"], f"{province} branch/foliage"
        )
        total = _finite(
            row["total_residue_odt_per_m3"], f"{province} total residue"
        )
        _require_close(total, stem + branch, f"{province} residue conservation")
        recoverable = {
            factor: _finite(
                row[
                    "recoverable_odt_per_m3_r"
                    f"{round(factor * 100):03d}"
                ],
                f"{province} recoverable at {factor}",
            )
            for factor in RECOVERY_SCENARIOS
        }
        for factor in RECOVERY_SCENARIOS:
            _require_close(
                recoverable[factor],
                total * factor,
                f"{province} recovery at {factor}",
            )
        density: float | None = None
        expansion_volume: float | None = None
        inventory_volume: float | None = None
        ratio: float | None = None
        status = str(row.get("open_volume_status", "")).strip()
        if province in IMPUTED_PROVINCES:
            if _parse_bool(row["density_calibrated"], f"{province} density"):
                density = _finite(
                    row["density_stems_ha"], f"{province} density"
                )
                if density <= 0.0:
                    raise E1E2cCoefficientError(
                        f"{province}: density is not positive"
                    )
            evidence = str(row["density_evidence_class"]).strip()
            if evidence not in {"open_inventory", "donor", "proxy"}:
                raise E1E2cCoefficientError(
                    f"{province}: unexpected density evidence {evidence!r}"
                )
            expansion_volume = _finite(
                row["expansion_volume_m3ha"],
                f"{province} expansion volume",
            )
            inventory_raw = str(
                row["inventory_volume_mean_m3ha"]
            ).strip()
            if inventory_raw:
                inventory_volume = _finite(
                    inventory_raw, f"{province} inventory volume"
                )
                if inventory_volume > 0.0:
                    ratio = expansion_volume / inventory_volume
            if province in OPEN_INVENTORY_PROVINCES:
                if status != "within_50pct" or ratio is None:
                    raise E1E2cCoefficientError(
                        f"{province}: open-volume check did not pass"
                    )
                if not (
                    1.0 - OPEN_VOLUME_TOLERANCE
                    <= ratio
                    <= 1.0 + OPEN_VOLUME_TOLERANCE
                ):
                    raise E1E2cCoefficientError(
                        f"{province}: open-volume ratio {ratio!r} is outside "
                        "+/-50%"
                    )
            elif status != "not_available":
                raise E1E2cCoefficientError(
                    f"{province}: expected not_available volume status, "
                    f"found {status!r}"
                )
        by_province[province] = ProductCoefficients(
            province=province,
            province_name=str(row["province_name"]).strip(),
            tier=tier,
            solid_share=solid,
            pulp_share=pulp,
            other_share=other,
            stem_residue_odt_per_m3=stem,
            branch_foliage_odt_per_m3=branch,
            total_residue_odt_per_m3=total,
            stem_residue_m3_per_m3=_finite(
                row["stem_residue_m3_per_m3"],
                f"{province} stem residue m3",
            ),
            recoverable_odt_per_m3_r035=recoverable[0.35],
            recoverable_odt_per_m3_r060=recoverable[0.60],
            recoverable_odt_per_m3_r075=recoverable[0.75],
            n_trees=int(row["n_trees"]),
            density_stems_ha=density,
            density_statistic=str(row["density_statistic"]).strip(),
            density_basis=str(row["density_basis"]).strip(),
            density_evidence_class=str(
                row["density_evidence_class"]
            ).strip(),
            density_expansion_label=str(
                row["density_expansion_label"]
            ).strip(),
            expansion_volume_m3ha=expansion_volume,
            inventory_volume_mean_m3ha=inventory_volume,
            open_volume_ratio=ratio,
            open_volume_status=status,
        )
    if set(by_province) != set(PROVINCES):
        missing = sorted(set(PROVINCES) - set(by_province))
        extra = sorted(set(by_province) - set(PROVINCES))
        raise E1E2cCoefficientError(
            f"province set mismatch: missing={missing}, extra={extra}"
        )
    measured = {
        province
        for province, coefficient in by_province.items()
        if coefficient.is_measured
    }
    if measured != set(MEASURED_PROVINCES):
        raise E1E2cCoefficientError(
            f"measured province set is not BC/QC/NB/YT: {sorted(measured)}"
        )
    if {
        province
        for province, coefficient in by_province.items()
        if coefficient.is_density_calibrated
    } != set(IMPUTED_PROVINCES):
        raise E1E2cCoefficientError(
            "E2c province set is not the nine imputed jurisdictions"
        )
    return by_province


def e2_coefficients(
    coefficients: Mapping[str, ProductCoefficients],
) -> dict[str, ProductCoefficients]:
    """Return the nine E2c rows after a set check."""

    selected = {
        province: coefficients[province] for province in IMPUTED_PROVINCES
    }
    if {row.tier for row in selected.values()} != {E2_TIER}:
        raise E1E2cCoefficientError("E2c rows do not all carry the E2c tier")
    return selected


def main() -> None:
    summary = build()
    print(
        json.dumps(
            {
                "artifact": summary["artifact"],
                "status": summary["status"],
                "coefficient_tiers": summary["coefficient_tiers"],
                "open_volume_checks": len(summary["open_volume_checks"]),
                "output_dir": str(OUTPUT_DIR.relative_to(REPO_ROOT)),
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
