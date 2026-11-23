"""Build national facility chain v7 with E1 + E2c coefficients.

Version 7 keeps the assignment-v6 / capacity-v3 provincial floor and the
100/119/150 Mm3 cases.  It refreshes the product chain from v4 (E2 v1
coefficients) to v5 (E1 measured + E2c density-calibrated coefficients).

The comparison is the prior frozen v3.2 chain (``..._v6``) versus this E2c
chain v7.  Products, process residue and harvest residue are reported
separately; branch and foliage add dry mass but no measured volume.  All values
remain ``model_estimate`` with ``observed=false`` and ``policy_grade=false``.
No national supply, policy, or operating claim is made.
"""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any

from workflows.national.facilities import (
    national_facility_chain_scenarios_v6 as _v6,
)

REPO_ROOT = _v6.REPO_ROOT
DATA_ROOT = _v6.DATA_ROOT
STEM_DIR = (
    DATA_ROOT / "derived/national/facilities/national_facility_chain_scenarios_v6"
)
PRODUCT_CHAIN_DIR = (
    DATA_ROOT / "derived/national/facilities/national_product_chain_v5"
)
PRODUCT_CHAIN_CSV = PRODUCT_CHAIN_DIR / "product_chain_by_province.csv"
PRODUCT_CHAIN_SUMMARY = PRODUCT_CHAIN_DIR / "summary.json"
MEASURED_REPLACEMENT = PRODUCT_CHAIN_DIR / "e1_coefficient_replacement.csv"
OUTPUT_DIR = (
    DATA_ROOT / "derived/national/facilities/national_facility_chain_scenarios_v7"
)
ARTIFACT = "national_facility_chain_scenarios_v7"
ARTIFACT_VERSION = "7.0.0"
ARTIFACT_TITLE = "National Facility Chain Scenarios V7 (E1+E2c Coefficients)"
REPORT_NAME = "NATIONAL_FACILITY_CHAIN_SCENARIOS_V7.md"
MODULE_PATH = "workflows.national.facilities.national_facility_chain_scenarios_v7"
TEST_PATH = "tests/test_national_facility_chain_scenarios_v7.py"
PRODUCT_CHAIN_ARTIFACT = "national_product_chain_v5"
COMPARISON_FIELDS = (
    "scenario",
    "scope",
    "province",
    "metric",
    "unit",
    "v3_2_value",
    "v4_value",
    "delta",
    "delta_pct",
    "evidence_label",
    "observed",
    "policy_grade",
)

_SAVED_NAMES = (
    "STEM_DIR",
    "PRODUCT_CHAIN_DIR",
    "PRODUCT_CHAIN_CSV",
    "PRODUCT_CHAIN_SUMMARY",
    "MEASURED_REPLACEMENT",
    "PRODUCT_CHAIN_ARTIFACT",
    "OUTPUT_DIR",
    "ARTIFACT",
    "ARTIFACT_VERSION",
    "ARTIFACT_TITLE",
    "REPORT_NAME",
    "MODULE_PATH",
    "TEST_PATH",
)


def _read_rows(path: Path, label: str) -> list[dict[str, str]]:
    if not path.is_file():
        raise _v6._v4.ScenarioChainV4Error(f"{label} is missing: {path}")
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise _v6._v4.ScenarioChainV4Error(f"{label} is empty: {path}")
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
        "for BC/QC/NB/YT and E2c density-calibrated product coefficients for "
        "the other nine; total harvest residue includes CFS branch and foliage."
    )
    summary["product_chain"] = {
        "artifact": PRODUCT_CHAIN_ARTIFACT,
        "reference_harvest_m3": 118_977_339.1,
        "conversion": (
            "E1 measured and E2c density-calibrated provincial solid/pulp/"
            "other shares with donor within-class splits and class conversion "
            "ratios; E1 measured and E2c stem + branch + foliage total residue"
        ),
        "measured_provinces": ["BC", "NB", "QC", "YT"],
        "density_calibrated_provinces": [
            "AB",
            "MB",
            "NL",
            "NS",
            "NT",
            "NU",
            "ON",
            "PE",
            "SK",
        ],
        "donor_product_provinces": [],
        "harvest_residue_scope": "province_only",
        "process_residue_scope": "facility_assigned",
        "residue_components": "stem_plus_branch_foliage",
        "recovery_scenarios": [0.35, 0.60, 0.75],
        "calibrated_recovery_factor": False,
    }
    summary["coefficient_tiers"] = coefficient_tiers
    summary["coefficient_replacement"] = {
        "measured_provinces": ["BC", "NB", "QC", "YT"],
        "density_calibrated_provinces": [
            "AB",
            "MB",
            "NL",
            "NS",
            "NT",
            "NU",
            "ON",
            "PE",
            "SK",
        ],
        "donor_product_provinces": [],
        "coefficient_rows": product_summary["province_coefficients"],
    }
    summary["v3_2_vs_v4"] = {
        "prior_chain": "national_facility_chain_scenarios_v6",
        "prior_chain_label": (
            "frozen v3.2 chain with E2 v1 product coefficients"
        ),
        "e2c_chain": ARTIFACT,
        "primary_scenario": "119m",
        "metrics": [metric for metric, _ in _v6._v4.PRODUCT_METRICS],
        "coefficients": product_summary["v3_2_vs_v4"],
        "comparison_csv": "v3_2_vs_v4_comparison.csv",
        "baseline_note": (
            "The committed v3.2 chain uses E2 v1, not E2 v2. E2 v2 and E2c "
            "share per-m3 coefficients, so the deltas are E2 v1 to E2c "
            "coefficient effects at fixed assignment and harvest."
        ),
    }
    summary["measured_vs_donor"]["comparison_label"] = (
        "frozen_v3_2_chain_v6 versus E2c_chain_v7"
    )
    summary["measured_vs_donor"]["comparison_csv"] = (
        "v3_2_vs_v4_comparison.csv"
    )
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
            "E2c is an open-data density calibration, not measured inventory. "
            "SK/NS/PE use open stem density, MB uses donor median density and "
            "AB/ON/NL/NT/NU retain a labelled 1,000 stems/ha proxy."
        ),
        (
            "Density calibration is extensive: it changes stems/ha expansion "
            "and the expanded reference volume, not per-m3 product or "
            "residue coefficients."
        ),
        (
            "Total harvest residue includes CFS branch and foliage dry mass. "
            "Branch and foliage have no measured volume, so products and "
            "process residue are not increased by that dry mass."
        ),
        (
            "0.35/0.60/0.75 recovery scenarios are carried with equal status; "
            "0.60 is a reference value, not a calibrated recovery factor."
        ),
    ]
    summary["method"]["product_residue"] = (
        "E1 measured and E2c density-calibrated product-chain-v5 "
        "coefficients applied to assigned class feedstock; donor within-class "
        "splits and class ratios"
    )
    summary["method"]["labels"] = (
        "model_estimate / observed=false / policy_grade=false"
    )
    summary["inputs"][
        "data/derived/national/facilities/national_product_chain_v5/"
        "product_chain_by_province.csv"
    ] = _v6._v4._base._sha256(PRODUCT_CHAIN_CSV)
    summary["inputs"][
        "data/derived/national/facilities/national_product_chain_v5/"
        "v3_2_vs_v4_comparison.csv"
    ] = _v6._v4._base._sha256(
        PRODUCT_CHAIN_DIR / "v3_2_vs_v4_comparison.csv"
    )

    output.mkdir(parents=True, exist_ok=True)
    comparison = _read_rows(
        output / "measured_vs_donor_comparison.csv",
        "prior-chain comparison",
    )
    with (output / "v3_2_vs_v4_comparison.csv").open(
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
                    "v3_2_value": row["donor_value"],
                    "v4_value": row["measured_value"],
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
            "Frozen v3.2 versus E2c v4 coefficients",
        ).replace("E2 imputed", "E2c density-calibrated")
        text += (
            "\n## E1/E2c coefficient source\n\n"
            "BC/QC/NB/YT use E1 measured coefficients; the other nine use "
            "E2c density-calibrated open-data coefficients. No donor "
            "provincial product share is used. The frozen v3.2 baseline uses "
            "E2 v1 coefficients, not E2 v2; see "
            "`v3_2_vs_v4_comparison.csv` for the change and "
            "`national_product_chain_v5/e2_v2_vs_e2c_coefficients.csv` for "
            "the E2 v2 to E2c per-m3 coefficient check.\n"
        )
        report_path.write_text(text, encoding="utf-8")
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
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
    """Build chain v7 with E1/E2c coefficients and a v3.2 comparison."""

    saved = {name: getattr(_v6, name) for name in _SAVED_NAMES}
    saved_v4 = dict(_v6._v4._V3_OVERRIDES)
    saved_base = dict(_v6._v4._BASE_OVERRIDES)
    saved_postprocess = _v6._postprocess
    try:
        for name in _SAVED_NAMES:
            setattr(_v6, name, globals()[name])
        _v6._postprocess = lambda *args, **kwargs: None
        _v6._v4._V3_OVERRIDES.update(
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
        _v6._v4._BASE_OVERRIDES.update(
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
        summary = _v6.build(output_dir=target)
    finally:
        for name, value in saved.items():
            setattr(_v6, name, value)
        _v6._postprocess = saved_postprocess
        _v6._v4._V3_OVERRIDES.clear()
        _v6._v4._V3_OVERRIDES.update(saved_v4)
        _v6._v4._BASE_OVERRIDES.clear()
        _v6._v4._BASE_OVERRIDES.update(saved_base)
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
