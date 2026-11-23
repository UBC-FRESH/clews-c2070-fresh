"""Build the all-zone forestry residue handoff to OSeCAN v6 trade.

Version 6 trade keeps the v2 province-to-zone proxy weights and allocation
hierarchy but refreshes the province/case totals from the assignment-v5
trade ``national_residue_curve_v5_trade``, which carries measured total harvest
residue (stem + branch + foliage) for BC/QC/NB/YT and the trade-permissive
process-residue distribution.

The official OSeCAN tree remains read-only and is hash-checked before and after
the build.  All rows remain model estimates: ``observed=false``,
``policy_grade=false``.  No policy target or national supply claim is made.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from workflows.national.facilities import forestry_osecan_handoff_v6 as _v6

REPO_ROOT = _v6.REPO_ROOT
DATA_ROOT = _v6.DATA_ROOT
CURVE_DIR = (
    DATA_ROOT / "derived/national/facilities/national_residue_curve_v5_trade"
)
PROVINCE_CURVE = CURVE_DIR / "province_residue_curve.csv"
NATIONAL_CURVE = CURVE_DIR / "national_residue_curve.csv"
CURVE_SUMMARY = CURVE_DIR / "summary.json"
OUTPUT_DIR = (
    DATA_ROOT / "derived/national/facilities/forestry_osecan_handoff_v6_trade"
)
ARTIFACT = "forestry_osecan_handoff_v6_trade"
ARTIFACT_VERSION = "6.1.0"
ARTIFACT_TITLE = "Forestry Residue Handoff to OSeCAN v6 Trade (Total Residue)"
REPORT_NAME = "FORESTRY_OSECAN_HANDOFF_V6_TRADE.md"
CURVE_LABEL = "national_residue_curve_v5_trade"
MODULE_PATH = (
    "workflows.national.facilities.forestry_osecan_handoff_v6_trade"
)
TEST_PATH = "tests/test_forestry_osecan_handoff_v6_trade.py"
VERSION_NOTE = (
    "Version 6 trade preserves the all-zone v2 proxy weights and uses the "
    "assignment-v5 trade total-residue curve v5."
)

OVERRIDES: dict[str, Any] = {
    "OUTPUT_DIR": OUTPUT_DIR,
    "CURVE_DIR": CURVE_DIR,
    "PROVINCE_CURVE": PROVINCE_CURVE,
    "NATIONAL_CURVE": NATIONAL_CURVE,
    "CURVE_SUMMARY": CURVE_SUMMARY,
    "ARTIFACT": ARTIFACT,
    "ARTIFACT_VERSION": ARTIFACT_VERSION,
    "ARTIFACT_TITLE": ARTIFACT_TITLE,
    "REPORT_NAME": REPORT_NAME,
    "CURVE_LABEL": CURVE_LABEL,
    "MODULE_PATH": MODULE_PATH,
    "TEST_PATH": TEST_PATH,
    "VERSION_NOTE": VERSION_NOTE,
}

_SAVED_NAMES = tuple(OVERRIDES)


def _postprocess(summary: dict[str, Any], output_dir: Path) -> None:
    summary["artifact"] = ARTIFACT
    summary["artifact_version"] = ARTIFACT_VERSION
    summary["trade_sensitivity"] = {
        "enabled": True,
        "curve_artifact": CURVE_LABEL,
        "note": (
            "Zone allocation uses the assignment-v5 published-plus-proxy "
            "trade process distribution; harvest residue is unchanged."
        ),
    }
    summary["caveats"] = list(summary.get("caveats", [])) + [
        (
            "This handoff uses the assignment-v5 published-plus-proxy trade "
            "sensitivity. It is a screening counterfactual, not an "
            "interprovincial trade forecast."
        ),
    ]
    summary["reproduce"] = f".venv/bin/python -m {MODULE_PATH}"
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / REPORT_NAME
    if report_path.is_file():
        text = report_path.read_text(encoding="utf-8")
        text = text.replace("Version 6", "Version 6 trade")
        text += (
            "\n## Trade sensitivity\n\n"
            "Province/case totals come from `national_residue_curve_v5_trade` "
            "(assignment-v5 `published_plus_proxy`). National totals match the "
            "local handoff v6; the process-residue zone distribution differs. "
            "This is a screening counterfactual, not an interprovincial trade "
            "forecast.\n"
        )
        report_path.write_text(text, encoding="utf-8")
    (output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest_path = output_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifact"] = ARTIFACT
    manifest["artifact_version"] = ARTIFACT_VERSION
    manifest["trade_sensitivity"] = summary["trade_sensitivity"]
    manifest["outputs"] = {
        path.name: {
            "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for path in sorted(output_dir.iterdir())
        if path.is_file() and path.name != "manifest.json"
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def build(output_dir: Path | None = None) -> dict[str, Any]:
    """Build the trade handoff while isolating the v6 builder globals."""

    saved = {name: getattr(_v6, name) for name in _SAVED_NAMES}
    try:
        for name, value in OVERRIDES.items():
            setattr(_v6, name, value)
        target = Path(output_dir) if output_dir is not None else OUTPUT_DIR
        summary = _v6.build(output_dir=target)
    finally:
        for name, value in saved.items():
            setattr(_v6, name, value)
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
                "supply_curve": summary["supply_curve"]["case_tier"],
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
