"""Build the trade-enabled facility chain v5 on measured total residue.

This is the assignment-v5 ``published_plus_proxy`` sensitivity (unscaled
capacity, trade-permissive cross-border allocation) rebuilt with the measured
four-province ``national_product_chain_v3`` coefficients and measured stem +
branch/foliage harvest residue.  It is the trade counterpart of the
assignment-v6 ``province_capacity_floor`` chain v5, so the two chains share
products, process residue, harvest residue, and residue scenarios; only the
facility distribution differs.

The artifact is a screening counterfactual, not an interprovincial trade
forecast.  All values remain ``model_estimate`` with ``observed=false`` and
``policy_grade=false``.  No UI or public OSeCAN surface is changed.
"""

from __future__ import annotations

import json
import types
from pathlib import Path
from typing import Any

from workflows.national.facilities import (
    national_facility_assignment_v5 as v5,
)
from workflows.national.facilities import (
    national_facility_chain_scenarios as _base,
)

REPO_ROOT = _base.REPO_ROOT
DATA_ROOT = _base.DATA_ROOT
OUTPUT_DIR = (
    DATA_ROOT
    / "derived/national/facilities/national_facility_chain_scenarios_v5_trade"
)
CAPACITY_CSV = v5.CAPACITY_CSV
INVENTORY_CSV = v5.CAPACITY_CSV
BOUNDARY_GEOJSON = v5.BOUNDARY_GEOJSON
PRODUCT_CHAIN_DIR = (
    DATA_ROOT / "derived/national/facilities/national_product_chain_v3"
)
PRODUCT_CHAIN_CSV = PRODUCT_CHAIN_DIR / "product_chain_by_province.csv"
PRODUCT_CHAIN_SUMMARY = PRODUCT_CHAIN_DIR / "summary.json"
HARVEST_CASES = _base.HARVEST_CASES
SCENARIO_ORDER = _base.SCENARIO_ORDER
PRIMARY_SCENARIO = _base.PRIMARY_SCENARIO
CAPACITY_CASE = "published_plus_proxy"
INTENSITY_CASES = _base.INTENSITY_CASES

ARTIFACT = "national_facility_chain_scenarios_v5_trade"
ARTIFACT_VERSION = "5.0.0"
ARTIFACT_TITLE = (
    "National Facility Chain Scenarios V5 Trade (Measured Total Residue)"
)
REPORT_NAME = "NATIONAL_FACILITY_CHAIN_SCENARIOS_V5_TRADE.md"
CAPACITY_LABEL = "inventory v1 / capacity v3"
ASSIGNMENT_LABEL = "assignment-v5"
MODULE_PATH = (
    "workflows.national.facilities.national_facility_chain_scenarios_v5_trade"
)
TEST_PATH = "tests/test_national_facility_chain_scenarios_v5_trade.py"
ScenarioChainTradeError = _base.ScenarioChainError


def _read_facilities(_path: Path, _inventory: Path) -> tuple[Any, ...]:
    """Adapt the v5 single-path reader to the chain two-path call."""
    return v5.read_facilities(v5.CAPACITY_CSV)


_V4_SHIM = types.SimpleNamespace(
    read_facilities=_read_facilities,
    PUBLISHED_EVIDENCE=v5.PUBLISHED_EVIDENCE,
    CAPACITY_CSV=CAPACITY_CSV,
    INVENTORY_CSV=INVENTORY_CSV,
    BOUNDARY_GEOJSON=BOUNDARY_GEOJSON,
)

BASE_OVERRIDES: dict[str, Any] = {
    "OUTPUT_DIR": OUTPUT_DIR,
    "CAPACITY_CSV": CAPACITY_CSV,
    "INVENTORY_CSV": INVENTORY_CSV,
    "BOUNDARY_GEOJSON": BOUNDARY_GEOJSON,
    "PRODUCT_CHAIN_DIR": PRODUCT_CHAIN_DIR,
    "PRODUCT_CHAIN_CSV": PRODUCT_CHAIN_CSV,
    "PRODUCT_CHAIN_SUMMARY": PRODUCT_CHAIN_SUMMARY,
    "PRODUCT_CHAIN_ARTIFACT": "national_product_chain_v3",
    "ARTIFACT": ARTIFACT,
    "ARTIFACT_VERSION": ARTIFACT_VERSION,
    "ARTIFACT_TITLE": ARTIFACT_TITLE,
    "REPORT_NAME": REPORT_NAME,
    "CAPACITY_LABEL": CAPACITY_LABEL,
    "ASSIGNMENT_LABEL": ASSIGNMENT_LABEL,
    "CAPACITY_CASE": CAPACITY_CASE,
    "MODULE_PATH": MODULE_PATH,
    "TEST_PATH": TEST_PATH,
    "v4": _V4_SHIM,
}


def _measured_provinces() -> list[str]:
    summary = json.loads(
        PRODUCT_CHAIN_SUMMARY.read_text(encoding="utf-8")
    )
    replacement = summary.get("measured_coefficient_replacement", {})
    provinces = replacement.get("measured_provinces")
    if provinces:
        return sorted(str(province) for province in provinces)
    rows = summary.get("measured_replacement", [])
    return sorted({str(row["province"]) for row in rows})


def _postprocess(summary: dict[str, Any], output_dir: Path) -> None:
    measured = _measured_provinces()
    summary["artifact"] = ARTIFACT
    summary["artifact_version"] = ARTIFACT_VERSION
    summary["assignment"] = {
        "artifact": "national_facility_assignment_v5",
        "capacity_case": CAPACITY_CASE,
        "trade_sensitivity": True,
        "note": (
            "Unscaled published-plus-proxy capacity; cross-border allocation "
            "is trade-permissive and is not a forecast."
        ),
    }
    summary["product_chain"] = {
        "artifact": "national_product_chain_v3",
        "reference_harvest_m3": 118_977_339.1,
        "conversion": (
            "measured four-province solid/pulp/other and total harvest-residue "
            "coefficients; donor coefficients for the other nine "
            "jurisdictions"
        ),
        "harvest_residue_scope": "province_only",
        "process_residue_scope": "facility_assigned",
        "residue_components": "stem_plus_branch_foliage",
        "measured_provinces": measured,
        "donor_provinces": [
            province
            for province in _base.PROVINCES
            if province not in measured
        ],
    }
    summary["method"]["product_residue"] = (
        "measured four-province product-chain-v3 coefficients applied to "
        "assigned class feedstock; donor coefficients for the other nine"
    )
    summary["method"]["assignment"] = (
        "assignment-v5 published_plus_proxy, local-first, finite capacity, "
        "500 km cross-border penalty"
    )
    summary["trade_sensitivity"] = {
        "enabled": True,
        "capacity_case": CAPACITY_CASE,
        "counterfactual": (
            "Trade-permissive upper bound for cross-border allocation; not a "
            "provincial self-sufficiency result."
        ),
    }
    summary["caveats"] = [
        caveat.replace(
            "product-chain-v1 province/class coefficients",
            "measured four-province product-chain-v3 coefficients",
        )
        for caveat in summary.get("caveats", [])
    ] + [
        (
            "This is the assignment-v5 published-plus-proxy trade sensitivity. "
            "It is not the frozen assignment-v6 provincial-floor base and it "
            "is not an interprovincial trade forecast."
        ),
        (
            "Products, process residue, harvest residue, and residue "
            "scenarios match the local chain v5 at the national level; the "
            "trade case changes the provincial facility distribution."
        ),
    ]
    summary["reproduce"] = f".venv/bin/python -m {MODULE_PATH}"

    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / REPORT_NAME
    if report_path.is_file():
        text = report_path.read_text(encoding="utf-8")
        text = text.replace(
            "product-chain-v1 province/class coefficients",
            "measured four-province product-chain-v3 coefficients",
        )
        text = text.replace(
            "product_chain_v1",
            "national_product_chain_v3",
        )
        text += (
            "\n## Trade sensitivity\n\n"
            "This chain uses the assignment-v5 `published_plus_proxy` case "
            "(unscaled capacity, trade-permissive cross-border allocation). "
            "It is the trade counterpart of the assignment-v6 "
            "`province_capacity_floor` chain v5; national products, process "
            "residue, harvest residue and residue scenarios are shared, while "
            "the provincial facility distribution differs. It is a screening "
            "counterfactual, not an interprovincial trade forecast.\n"
        )
        report_path.write_text(text, encoding="utf-8")

    (output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _base._write_manifest(output_dir, summary)


def build(output_dir: Path | None = None) -> dict[str, Any]:
    """Build the trade chain while isolating shared module globals."""

    saved = {name: getattr(_base, name) for name in BASE_OVERRIDES}
    try:
        for name, value in BASE_OVERRIDES.items():
            setattr(_base, name, value)
        target = Path(output_dir) if output_dir is not None else OUTPUT_DIR
        summary = _base.build(output_dir=target)
    finally:
        for name, value in saved.items():
            setattr(_base, name, value)
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
                "scenarios": {
                    key: {
                        "assigned_m3": value["assigned_m3"],
                        "cross_border_m3": value["cross_border_m3"],
                        "proxy_share": value["proxy_share_of_assigned"],
                        "process_residue_pj": value["process_residue_pj"],
                    }
                    for key, value in summary["scenarios"].items()
                },
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
