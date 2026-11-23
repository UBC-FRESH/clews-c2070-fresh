"""Build national facility chain v6 with E1 measured + E2 imputed coefficients.

Version 6 keeps the assignment-v6 / capacity-v3 provincial floor and the
100/119/150 Mm3 cases.  It refreshes the product chain from v3 (E1 total
residue, donor E2 coefficients) to v4 (E1 measured + E2 imputed coefficients
and total residue for all 13 provinces).

The donor comparison is the prior chain v5 versus this E2 chain v6.  A separate
coefficient table compares the nine E2 provincial product shares and total
residue coefficients with the old donor values.  Products, process residue and
harvest residue are reported separately; branch and foliage add dry mass but no
measured volume.  All values remain ``model_estimate`` with ``observed=false``
and ``policy_grade=false``.  No national supply, policy, or operating claim is
made.
"""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any

from workflows.national.facilities import national_facility_chain_scenarios_v4 as _v4

REPO_ROOT = _v4.REPO_ROOT
DATA_ROOT = _v4.DATA_ROOT
STEM_DIR = (
    DATA_ROOT / "derived/national/facilities/national_facility_chain_scenarios_v5"
)
PRODUCT_CHAIN_DIR = (
    DATA_ROOT / "derived/national/facilities/national_product_chain_v4"
)
PRODUCT_CHAIN_CSV = PRODUCT_CHAIN_DIR / "product_chain_by_province.csv"
PRODUCT_CHAIN_SUMMARY = PRODUCT_CHAIN_DIR / "summary.json"
MEASURED_REPLACEMENT = PRODUCT_CHAIN_DIR / "e1_coefficient_replacement.csv"
OUTPUT_DIR = (
    DATA_ROOT / "derived/national/facilities/national_facility_chain_scenarios_v6"
)
ARTIFACT = "national_facility_chain_scenarios_v6"
ARTIFACT_VERSION = "6.0.0"
ARTIFACT_TITLE = "National Facility Chain Scenarios V6 (E1+E2 Coefficients)"
REPORT_NAME = "NATIONAL_FACILITY_CHAIN_SCENARIOS_V6.md"
MODULE_PATH = "workflows.national.facilities.national_facility_chain_scenarios_v6"
TEST_PATH = "tests/test_national_facility_chain_scenarios_v6.py"
PRODUCT_CHAIN_ARTIFACT = "national_product_chain_v4"
COMPARISON_FIELDS = (
    "scenario",
    "scope",
    "province",
    "metric",
    "unit",
    "donor_value",
    "e2_value",
    "delta",
    "delta_pct",
    "evidence_label",
    "observed",
    "policy_grade",
)

_SAVED_NAMES = (
    "DONOR_DIR",
    "OUTPUT_DIR",
    "PRODUCT_CHAIN_DIR",
    "PRODUCT_CHAIN_CSV",
    "PRODUCT_CHAIN_SUMMARY",
    "MEASURED_REPLACEMENT",
    "ARTIFACT",
    "ARTIFACT_VERSION",
    "ARTIFACT_TITLE",
    "REPORT_NAME",
    "MODULE_PATH",
    "TEST_PATH",
)


def _read_rows(path: Path, label: str) -> list[dict[str, str]]:
    if not path.is_file():
        raise _v4.ScenarioChainV4Error(f"{label} is missing: {path}")
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise _v4.ScenarioChainV4Error(f"{label} is empty: {path}")
    return rows


def _postprocess(summary: dict[str, Any], output: Path) -> None:
    product_summary = json.loads(
        PRODUCT_CHAIN_SUMMARY.read_text(encoding="utf-8")
    )
    coefficient_tiers = product_summary["coefficient_tiers"]
    summary["artifact"] = ARTIFACT
    summary["artifact_version"] = ARTIFACT_VERSION
    summary["scope"] = (
        "100/119/150 Mm3 facility chain with E1 measured product coefficients "
        "for BC/QC/NB/YT and E2 imputed product coefficients for the other "
        "nine; total harvest residue includes CFS branch and foliage."
    )
    summary["product_chain"] = {
        "artifact": PRODUCT_CHAIN_ARTIFACT,
        "reference_harvest_m3": 118_977_339.1,
        "conversion": (
            "E1 measured and E2 imputed provincial solid/pulp/other shares "
            "with donor within-class splits and class conversion ratios; "
            "E1 measured and E2 imputed stem + branch + foliage total residue"
        ),
        "measured_provinces": ["BC", "NB", "QC", "YT"],
        "imputed_provinces": ["AB", "MB", "NL", "NS", "NT", "NU", "ON", "PE", "SK"],
        "donor_provinces": [],
        "harvest_residue_scope": "province_only",
        "process_residue_scope": "facility_assigned",
        "residue_components": "stem_plus_branch_foliage",
        "recovery_scenarios": [0.35, 0.60, 0.75],
        "calibrated_recovery_factor": False,
    }
    summary["coefficient_tiers"] = coefficient_tiers
    summary["coefficient_replacement"] = {
        "measured_provinces": ["BC", "NB", "QC", "YT"],
        "e2_imputed_provinces": ["AB", "MB", "NL", "NS", "NT", "NU", "ON", "PE", "SK"],
        "donor_product_provinces": [],
        "coefficient_rows": product_summary["province_coefficients"],
    }
    summary["donor_vs_e2"] = {
        "prior_chain": "national_facility_chain_scenarios_v5",
        "prior_chain_label": "E1 measured + donor E2 product coefficients",
        "e2_chain": ARTIFACT,
        "primary_scenario": "119m",
        "metrics": [
            metric for metric, _ in _v4.PRODUCT_METRICS
        ],
        "coefficients": product_summary["donor_vs_e2_coefficients"],
        "comparison_csv": "donor_vs_e2_comparison.csv",
    }
    summary["measured_vs_donor"]["comparison_label"] = (
        "prior_donor_product_chain_v5 versus E2_imputed_chain_v6"
    )
    summary["measured_vs_donor"]["comparison_csv"] = "donor_vs_e2_comparison.csv"
    summary["labels"] = {
        "model_estimate": True,
        "observed": False,
        "policy_grade": False,
        "national_claim": False,
        "official_osecan_changed": False,
    }
    summary["caveats"] = [
        caveat
        for caveat in summary.get("caveats", [])
        if "Measured solid" not in caveat
    ] + [
        (
            "E2 is an open-data imputation, not measured inventory. The nine "
            "E2 provinces use SCANFI composition, donor geometry and published "
            "donor tapers; no local inventory measurement exists."
        ),
        (
            "E2 product shares replace the old donor product coefficients. "
            "Panel and pellet/bio splits, class conversion ratios and "
            "stem-residue m3 routing remain documented donor assumptions."
        ),
        (
            "Total harvest residue for E2 includes CFS branch and foliage dry "
            "mass. Branch and foliage have no measured volume, so products and "
            "process residue are not increased by that dry mass."
        ),
        (
            "0.35/0.60/0.75 recovery scenarios are carried with equal status; "
            "0.60 is a reference value, not a calibrated recovery factor."
        ),
    ]
    summary["method"]["product_residue"] = (
        "E1 measured and E2 imputed product-chain-v4 coefficients applied to "
        "assigned class feedstock; donor within-class splits and class ratios"
    )
    summary["method"]["labels"] = (
        "model_estimate / observed=false / policy_grade=false"
    )
    summary["inputs"][
        "data/derived/national/facilities/national_product_chain_v4/"
        "product_chain_by_province.csv"
    ] = _v4._base._sha256(PRODUCT_CHAIN_CSV)
    summary["inputs"][
        "data/derived/national/facilities/national_product_chain_v4/"
        "donor_vs_e2_coefficients.csv"
    ] = _v4._base._sha256(PRODUCT_CHAIN_DIR / "donor_vs_e2_coefficients.csv")

    output.mkdir(parents=True, exist_ok=True)
    comparison = _read_rows(
        output / "measured_vs_donor_comparison.csv",
        "prior-chain comparison",
    )
    with (output / "donor_vs_e2_comparison.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.DictWriter(
            handle, fieldnames=list(COMPARISON_FIELDS), lineterminator="\n"
        )
        writer.writeheader()
        for row in comparison:
            writer.writerow(
                {
                    "scenario": row["scenario"],
                    "scope": row["scope"],
                    "province": row["province"],
                    "metric": row["metric"],
                    "unit": row["unit"],
                    "donor_value": row["donor_value"],
                    "e2_value": row["measured_value"],
                    "delta": row["delta"],
                    "delta_pct": row["delta_pct"],
                    "evidence_label": "model_estimate",
                    "observed": "false",
                    "policy_grade": "false",
                }
            )
    report_path = output / REPORT_NAME
    if report_path.is_file():
        text = report_path.read_text(encoding="utf-8")
        text = text.replace(
            "Measured versus donor coefficients",
            "Prior donor-product chain versus E2 coefficients",
        )
        text += (
            "\n## E1/E2 coefficient source\n\n"
            "BC/QC/NB/YT use E1 measured coefficients; the other nine use E2 "
            "open-data imputation coefficients. No donor provincial product "
            "share is used. See `donor_vs_e2_comparison.csv` for the prior "
            "chain-v5 versus E2-chain-v6 change and "
            "`national_product_chain_v4/donor_vs_e2_coefficients.csv` for the "
            "coefficient table.\n"
        )
        report_path.write_text(text, encoding="utf-8")
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    manifest_path = output / "manifest.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["artifact"] = ARTIFACT
        manifest["artifact_version"] = ARTIFACT_VERSION
        manifest["labels"] = summary["labels"]
        manifest["coefficient_tiers"] = coefficient_tiers
        manifest["outputs"] = {
            path.name: {
                "bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            for path in sorted(output.iterdir())
            if path.is_file() and path.name != "manifest.json"
        }
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


def build(output_dir: Path | None = None) -> dict[str, Any]:
    """Build chain v6 with E1/E2 coefficients and prior-chain comparison."""

    saved = {name: getattr(_v4, name) for name in _SAVED_NAMES}
    saved_v3 = dict(_v4._V3_OVERRIDES)
    saved_base = dict(_v4._BASE_OVERRIDES)
    try:
        _v4.DONOR_DIR = STEM_DIR
        _v4.OUTPUT_DIR = OUTPUT_DIR
        _v4.PRODUCT_CHAIN_DIR = PRODUCT_CHAIN_DIR
        _v4.PRODUCT_CHAIN_CSV = PRODUCT_CHAIN_CSV
        _v4.PRODUCT_CHAIN_SUMMARY = PRODUCT_CHAIN_SUMMARY
        _v4.MEASURED_REPLACEMENT = MEASURED_REPLACEMENT
        _v4.ARTIFACT = ARTIFACT
        _v4.ARTIFACT_VERSION = ARTIFACT_VERSION
        _v4.ARTIFACT_TITLE = ARTIFACT_TITLE
        _v4.REPORT_NAME = REPORT_NAME
        _v4.MODULE_PATH = MODULE_PATH
        _v4.TEST_PATH = TEST_PATH
        _v4._V3_OVERRIDES.update(
            {
                "OUTPUT_DIR": OUTPUT_DIR,
                "ARTIFACT": ARTIFACT,
                "ARTIFACT_VERSION": ARTIFACT_VERSION,
                "ARTIFACT_TITLE": ARTIFACT_TITLE,
                "REPORT_NAME": REPORT_NAME,
                "MODULE_PATH": MODULE_PATH,
                "TEST_PATH": TEST_PATH,
            }
        )
        _v4._BASE_OVERRIDES.update(
            {
                "PRODUCT_CHAIN_CSV": PRODUCT_CHAIN_CSV,
                "PRODUCT_CHAIN_SUMMARY": PRODUCT_CHAIN_SUMMARY,
                "PRODUCT_CHAIN_ARTIFACT": PRODUCT_CHAIN_ARTIFACT,
                "PRODUCT_CHAIN_DIR": PRODUCT_CHAIN_DIR,
                "OUTPUT_DIR": OUTPUT_DIR,
                "ARTIFACT": ARTIFACT,
                "ARTIFACT_VERSION": ARTIFACT_VERSION,
                "ARTIFACT_TITLE": ARTIFACT_TITLE,
                "REPORT_NAME": REPORT_NAME,
                "MODULE_PATH": MODULE_PATH,
                "TEST_PATH": TEST_PATH,
            }
        )
        target = Path(output_dir) if output_dir is not None else OUTPUT_DIR
        summary = _v4.build(output_dir=target)
    finally:
        for name, value in saved.items():
            setattr(_v4, name, value)
        _v4._V3_OVERRIDES.clear()
        _v4._V3_OVERRIDES.update(saved_v3)
        _v4._BASE_OVERRIDES.clear()
        _v4._BASE_OVERRIDES.update(saved_base)
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
                "validation": summary["validation"]["passed"],
                "scenarios": list(summary["scenarios"]),
                "coefficient_tiers": summary["coefficient_tiers"],
                "output_dir": str(OUTPUT_DIR.relative_to(REPO_ROOT)),
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
