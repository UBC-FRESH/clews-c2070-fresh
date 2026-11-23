"""Build national facility chain v5 with measured total harvest residue.

Version 5 keeps the assignment-v6 / capacity-v3 provincial floor and every
product quantity from chain v4.  It refreshes the product chain from v2 (stem
residue) to v3 (measured stem + branch + foliage residue) and reports the
stem-only versus total-residue difference for the same 100/119/150 Mm3 cases.

Products, process residue, and feedstock routing are unchanged by design: the
added branch and foliage dry mass has no measured volume, so it changes harvest
residue PJ and OSeCAN case positions, not mill product quantities.  All values
remain ``model_estimate`` with ``observed=false`` and ``policy_grade=false``.
No national supply, policy, or operating claim is made.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from workflows.national.facilities import national_facility_chain_scenarios_v4 as _v4

REPO_ROOT = _v4.REPO_ROOT
DATA_ROOT = _v4.DATA_ROOT
STEM_DIR = _v4.OUTPUT_DIR
PRODUCT_CHAIN_DIR = (
    DATA_ROOT / "derived/national/facilities/national_product_chain_v3"
)
PRODUCT_CHAIN_CSV = PRODUCT_CHAIN_DIR / "product_chain_by_province.csv"
PRODUCT_CHAIN_SUMMARY = PRODUCT_CHAIN_DIR / "summary.json"
MEASURED_REPLACEMENT = PRODUCT_CHAIN_DIR / "measured_coefficient_replacement.csv"
OUTPUT_DIR = (
    DATA_ROOT
    / "derived/national/facilities/national_facility_chain_scenarios_v5"
)
ARTIFACT = "national_facility_chain_scenarios_v5"
ARTIFACT_VERSION = "5.0.0"
ARTIFACT_TITLE = "National Facility Chain Scenarios V5 (Total Residue)"
REPORT_NAME = "NATIONAL_FACILITY_CHAIN_SCENARIOS_V5.md"
MODULE_PATH = "workflows.national.facilities.national_facility_chain_scenarios_v5"
TEST_PATH = "tests/test_national_facility_chain_scenarios_v5.py"
PRODUCT_CHAIN_ARTIFACT = "national_product_chain_v3"

_SAVED_NAMES = (
    "DONOR_DIR",
    "OUTPUT_DIR",
    "ARTIFACT",
    "ARTIFACT_VERSION",
    "ARTIFACT_TITLE",
    "REPORT_NAME",
    "MODULE_PATH",
    "TEST_PATH",
    "PRODUCT_CHAIN_DIR",
    "PRODUCT_CHAIN_CSV",
    "PRODUCT_CHAIN_SUMMARY",
    "MEASURED_REPLACEMENT",
)


def _patch_module_values() -> dict[str, Any]:
    saved = {name: getattr(_v4, name) for name in _SAVED_NAMES}
    _v4.DONOR_DIR = STEM_DIR
    _v4.OUTPUT_DIR = OUTPUT_DIR
    _v4.ARTIFACT = ARTIFACT
    _v4.ARTIFACT_VERSION = ARTIFACT_VERSION
    _v4.ARTIFACT_TITLE = ARTIFACT_TITLE
    _v4.REPORT_NAME = REPORT_NAME
    _v4.MODULE_PATH = MODULE_PATH
    _v4.TEST_PATH = TEST_PATH
    _v4.PRODUCT_CHAIN_DIR = PRODUCT_CHAIN_DIR
    _v4.PRODUCT_CHAIN_CSV = PRODUCT_CHAIN_CSV
    _v4.PRODUCT_CHAIN_SUMMARY = PRODUCT_CHAIN_SUMMARY
    _v4.MEASURED_REPLACEMENT = MEASURED_REPLACEMENT

    saved_dicts = {
        "V3": dict(_v4._V3_OVERRIDES),
        "BASE": dict(_v4._BASE_OVERRIDES),
    }
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
    return {"globals": saved, "dicts": saved_dicts}


def _restore_module_values(saved: dict[str, Any]) -> None:
    for name, value in saved["globals"].items():
        setattr(_v4, name, value)
    _v4._V3_OVERRIDES.clear()
    _v4._V3_OVERRIDES.update(saved["dicts"]["V3"])
    _v4._BASE_OVERRIDES.clear()
    _v4._BASE_OVERRIDES.update(saved["dicts"]["BASE"])


def build(output_dir: Path = OUTPUT_DIR) -> dict[str, Any]:
    """Build chain v5 and reframe the v4 comparison as stem-only vs total."""

    saved = _patch_module_values()
    try:
        summary = _v4.build(output_dir=output_dir)
    finally:
        _restore_module_values(saved)

    summary["artifact"] = ARTIFACT
    summary["artifact_version"] = ARTIFACT_VERSION
    summary["scope"] = (
        "100/119/150 Mm3 facility chain with measured total harvest residue "
        "(stem + branch + foliage) for BC/QC/NB/YT; products unchanged from v4."
    )
    summary["product_chain"] = {
        "artifact": PRODUCT_CHAIN_ARTIFACT,
        "reference_harvest_m3": 118_977_339.1,
        "conversion": (
            "measured four-province product shares and measured total harvest "
            "residue (stem + branch + foliage); donor coefficients for the "
            "other nine jurisdictions"
        ),
        "measured_provinces": ["BC", "NB", "QC", "YT"],
        "donor_provinces": list(summary["coefficient_replacement"]["donor_provinces"]),
        "harvest_residue_scope": "province_only",
        "process_residue_scope": "facility_assigned",
        "residue_components": "stem_plus_branch_foliage",
        "recovery_scenarios": [0.35, 0.60, 0.75],
        "calibrated_recovery_factor": False,
    }
    summary["stem_vs_total"] = {
        "stem_only_artifact": "national_facility_chain_scenarios_v4",
        "total_artifact": ARTIFACT,
        "products_unchanged": True,
        "metrics": summary.get("measured_vs_donor", {}).get("metrics", []),
        "primary_scenario": summary.get("measured_vs_donor", {}).get(
            "primary_scenario", "119m"
        ),
        "national_119m": summary.get("measured_vs_donor", {}).get(
            "national_119m", {}
        ),
        "comparison_csv": "stem_vs_total_comparison.csv",
    }
    summary["measured_vs_donor"]["comparison_label"] = (
        "stem_only_v4 versus total_residue_v5"
    )
    summary["labels"] = {
        "model_estimate": True,
        "observed": False,
        "policy_grade": False,
        "national_claim": False,
    }
    summary["caveats"] = [
        caveat
        for caveat in summary.get("caveats", [])
        if "Measured solid" not in caveat
    ] + [
        (
            "Products, process residue, and feedstock routing are unchanged "
            "from chain v4. Only harvest-residue dry mass changes, because "
            "branch and foliage have no measured volume."
        ),
        (
            "0.35/0.60/0.75 recovery scenarios are carried with equal status; "
            "0.60 is a reference value, not a calibrated recovery factor."
        ),
        (
            "The comparison table is stem-only v4 versus total-residue v5, not "
            "a donor-versus-measured coefficient table."
        ),
    ]

    output = Path(output_dir)
    original = output / "measured_vs_donor_comparison.csv"
    if original.is_file():
        shutil.copyfile(original, output / "stem_vs_total_comparison.csv")
    report_path = output / REPORT_NAME
    if report_path.is_file():
        text = report_path.read_text(encoding="utf-8")
        text = text.replace(
            "Measured versus donor coefficients",
            "Stem-only versus total residue",
        )
        text = text.replace("Donor", "Stem-only v4").replace(
            "donor", "stem-only v4"
        )
        report_path.write_text(text, encoding="utf-8")
    summary_path = output / "summary.json"
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    manifest_path = output / "manifest.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["artifact"] = ARTIFACT
        manifest["artifact_version"] = ARTIFACT_VERSION
        manifest["labels"] = summary["labels"]
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
                "output_dir": str(OUTPUT_DIR.relative_to(REPO_ROOT)),
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
