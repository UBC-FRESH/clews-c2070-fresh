"""Build the v4 energy decision-impact matrix for the forestry handoff.

The matrix turns the four open questions in ``docs/energy-handoff-v4.md`` into
a compact, reproducible decision surface:

* D1 harvest recovery basis: 0.35 / 0.60 / 0.75;
* D2 process-residue availability: 0 / 25 / 50 / 75%;
* D3 biomass and waste incineration: qualitative storage-mix implication;
* D4 industrial feedstock demand: qualitative competition with energy.

D1 and D2 are reconciled exactly to ``forestry_osecan_handoff_v8`` at the
119 Mm3 planning allocation.  D3 and D4 carry qualitative text only; no
quantity is invented.  Every row is a model/screening value:
``model_estimate=true``, ``observed=false``, ``policy_grade=false``,
``national_claim=false``, and ``official_osecan_changed=false``.  Nothing here
is a policy target, a preferred case, or an official OSeCAN result.
"""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_ROOT = REPO_ROOT / "data"
FACILITY_DIR = DATA_ROOT / "derived/national/facilities"
HANDOFF_DIR = FACILITY_DIR / "forestry_osecan_handoff_v8"
HANDOFF_SUMMARY = HANDOFF_DIR / "summary.json"
HANDOFF_MANIFEST = HANDOFF_DIR / "manifest.json"
CHAIN_SUMMARY = (
    FACILITY_DIR / "national_facility_chain_scenarios_v7" / "summary.json"
)
CURVE_SUMMARY = (
    FACILITY_DIR / "national_residue_curve_v7" / "summary.json"
)
ECONOMICS_SUMMARY = (
    DATA_ROOT
    / "derived/national/harvest_basis/national_economics_v2_3/summary.json"
)
HANDOFF_V4_DOC = REPO_ROOT / "docs/energy-handoff-v4.md"
OUTPUT_DIR = FACILITY_DIR / "energy_decision_matrix_v1"
DOCS_PATH = REPO_ROOT / "docs/energy-decision-matrix.md"

ARTIFACT = "energy_decision_matrix_v1"
ARTIFACT_VERSION = "1.0.0"
ARTIFACT_TITLE = "Energy Decision-Impact Matrix V1 (v4 119 Mm3)"
REPORT_NAME = "ENERGY_DECISION_MATRIX_V1.md"
MODULE_PATH = "workflows.national.facilities.energy_decision_matrix_v1"
TEST_PATH = "tests/test_energy_decision_matrix_v1.py"

REFERENCE_CASE = "119m"
REFERENCE_LABEL = "119 Mm3 planning allocation"
EXPECTED_HARVEST_M3 = 118_977_339.1
TOLERANCE_PJ = 1e-6

EXPECTED_TIERS: dict[tuple[str, str], str] = {
    ("harvest", "harvest_recovery_035"): "T4",
    ("harvest", "harvest_recovery_060"): "above_T4",
    ("harvest", "harvest_recovery_075"): "above_T4",
    ("process", "process_availability_000"): "T1",
    ("process", "process_availability_025"): "T3",
    ("process", "process_availability_050"): "above_T4",
    ("process", "process_availability_075"): "above_T4",
}

EXPECTED_HANDOFF_TOTALS: dict[str, dict[str, float]] = {
    "harvest": {
        "harvest_recovery_035": 155.5437681267482,
        "harvest_recovery_060": 266.6464596457938,
        "harvest_recovery_075": 333.3080745574168,
    },
    "process": {
        "process_availability_000": 0.0,
        "process_availability_025": 124.6877292302247,
        "process_availability_050": 249.3754584602354,
        "process_availability_075": 374.0631876904527,
    },
}

RECOVERY_CASES: tuple[dict[str, Any], ...] = (
    {
        "option_id": "harvest_recovery_035",
        "option_label": "35% recovery",
        "case_key": "harvest_recovery_035",
        "interpretation": (
            "Lower screening case. Within T4 after the v3.2-to-v4 move from "
            "T3; the change is a coefficient effect, not a new recovery "
            "measurement."
        ),
        "recommendation": (
            "Carry as the lower bound; it is the only harvest case inside the "
            "adopted T4 band."
        ),
        "screening_default": False,
    },
    {
        "option_id": "harvest_recovery_060",
        "option_label": "60% recovery",
        "case_key": "harvest_recovery_060",
        "interpretation": (
            "Reference case only. Exceeds the T4 upper bound; 0.60 is not a "
            "calibrated recovery factor and has no measured provenance."
        ),
        "recommendation": (
            "Carry the full range; if one screening case is required, use "
            "0.60 as a reference-only case, not a preference."
        ),
        "screening_default": True,
    },
    {
        "option_id": "harvest_recovery_075",
        "option_label": "75% recovery",
        "case_key": "harvest_recovery_075",
        "interpretation": (
            "Upper screening case. Exceeds T4 by the widest margin; useful "
            "to show how much the branch/foliage and recovery definition "
            "can move the interface value."
        ),
        "recommendation": (
            "Carry as the upper bound; do not treat it as an achievable or "
            "sustainable recovery level."
        ),
        "screening_default": False,
    },
)

PROCESS_CASES: tuple[dict[str, Any], ...] = (
    {
        "option_id": "process_availability_000",
        "option_label": "0% availability",
        "case_key": "process_availability_000",
        "interpretation": (
            "Degenerate zero case. It is not a physical process-residue "
            "estimate and only anchors the availability sweep at T1."
        ),
        "recommendation": (
            "Use only as the zero anchor; do not report it as an available "
            "process-residue quantity."
        ),
        "screening_default": False,
    },
    {
        "option_id": "process_availability_025",
        "option_label": "25% availability",
        "case_key": "process_availability_025",
        "interpretation": (
            "Lowest non-zero case and the only non-zero process case inside "
            "the adopted T1-T4 range (T3)."
        ),
        "recommendation": (
            "Carry the full range; if one screening case is required, use "
            "25% as a conservative non-zero screening point, not an "
            "availability estimate."
        ),
        "screening_default": True,
    },
    {
        "option_id": "process_availability_050",
        "option_label": "50% availability",
        "case_key": "process_availability_050",
        "interpretation": (
            "Midpoint screening case. Exceeds the T4 upper bound; one "
            "availability assumption dominates the comparison with the 2050 "
            "energy context."
        ),
        "recommendation": (
            "Carry as the midpoint; do not describe it as central or likely "
            "without an OSeCAN-specific availability source."
        ),
        "screening_default": False,
    },
    {
        "option_id": "process_availability_075",
        "option_label": "75% availability",
        "case_key": "process_availability_075",
        "interpretation": (
            "Upper screening case. Exceeds T4 by the widest margin; the "
            "quantity is a model estimate, not observed mill availability."
        ),
        "recommendation": (
            "Carry as the upper bound; do not treat it as a technical or "
            "economic availability finding."
        ),
        "screening_default": False,
    },
)

DECISIONS: tuple[dict[str, Any], ...] = (
    {
        "decision_id": "D1",
        "decision": "Recovery basis for branch and foliage",
        "short_label": "Harvest recovery basis",
        "stream": "harvest",
        "question": (
            "Should branch and foliage count as energy feedstock, and on what "
            "uniform recovery basis (0.35 / 0.60 / 0.75)?"
        ),
        "cases": RECOVERY_CASES,
        "unit": "PJ",
        "source_field": "handoff_totals.harvest",
        "recommended_default": (
            "Report the full 0.35/0.60/0.75 range. If a single screening case "
            "is required, label 0.60 as reference-only; it is not calibrated "
            "and not preferred."
        ),
        "default_case": "harvest_recovery_060",
        "default_role": "screening_reference_not_policy",
        "context_reference": "",
        "qualitative_implication": "",
        "no_numbers_invented": True,
    },
    {
        "decision_id": "D2",
        "decision": "Process-residue availability",
        "short_label": "Process availability",
        "stream": "process",
        "question": (
            "What fraction of mill process residue is available to the energy "
            "system in the OSeCAN 2050 context?"
        ),
        "cases": PROCESS_CASES,
        "unit": "PJ",
        "source_field": "handoff_totals.process",
        "recommended_default": (
            "Report the full 0/25/50/75% range. If a single screening case is "
            "required, use 25%, the lowest non-zero case, as a conservative "
            "screening point; it is not an availability estimate."
        ),
        "default_case": "process_availability_025",
        "default_role": "screening_conservative_not_policy",
        "context_reference": "",
        "qualitative_implication": "",
        "no_numbers_invented": True,
    },
    {
        "decision_id": "D3",
        "decision": "Biomass and waste incineration assumptions",
        "short_label": "Biomass/waste incineration",
        "stream": "qualitative",
        "question": (
            "Does OSeCAN include biomass or waste incineration beyond forestry "
            "residue, and does forestry residue compete for the same "
            "combustion capacity?"
        ),
        "cases": (),
        "unit": "",
        "source_field": "",
        "recommended_default": (
            "Keep non-forestry feedstocks outside the forestry matrix until "
            "the OSeCAN biomass/waste technology list, feedstock definitions, "
            "and internal mill-use boundary are supplied. Do not add a "
            "forestry tier to a mixed biomass/waste stack."
        ),
        "default_case": "",
        "default_role": "qualitative_boundary_not_policy",
        "context_reference": "Rubino context: storage mix",
        "qualitative_implication": (
            "If municipal solid waste, agricultural residue, construction and "
            "demolition wood, or black liquor share the same combustion or "
            "storage capacity, the forestry tier positions are not directly "
            "comparable. Adding those feedstocks can change the storage mix "
            "and dilute forestry residue's share; counting mill process "
            "residue both as supply and as internal mill energy would double "
            "count. No quantity is assigned here because the OSeCAN feedstock "
            "and technology denominators are not in the handoff."
        ),
        "no_numbers_invented": True,
    },
    {
        "decision_id": "D4",
        "decision": "Industrial feedstock demand versus energy",
        "short_label": "Industrial feedstock",
        "stream": "qualitative",
        "question": (
            "Which industrial demands are in the OSeCAN demand stack, and do "
            "they compete with the energy system for the same residue?"
        ),
        "cases": (),
        "unit": "",
        "source_field": "",
        "recommended_default": (
            "Report process residue gross and flag industrial/chemical demand "
            "as un-netted until the OSeCAN demand stack and mill self-use "
            "boundary are supplied. Do not double count the same residue as "
            "both industrial feedstock and energy supply."
        ),
        "default_case": "",
        "default_role": "qualitative_boundary_not_policy",
        "context_reference": "Pehl context: industrial feedstock competition",
        "qualitative_implication": (
            "Chemicals, pulp, panel, pellet, liquid fuels, and other industrial "
            "demands can compete with energy for the same residue. The "
            "facility chain assigns process residue by class and province but "
            "does not net out internal mill consumption or existing "
            "industrial/chemical contracts. If OSeCAN treats those volumes as "
            "committed, available energy residue is lower than the handoff "
            "total; if OSeCAN adds new chemical or biofuel demand, the same "
            "residue may be counted twice. No quantity is assigned here."
        ),
        "no_numbers_invented": True,
    },
)

CSV_FIELDS = (
    "decision_id",
    "decision",
    "option_id",
    "option_label",
    "case_key",
    "stream",
    "value_pj",
    "unit",
    "osecan_tier",
    "tier_relation",
    "interpretation",
    "recommendation",
    "recommendation_role",
    "evidence_grade",
    "numeric_basis",
    "context_reference",
    "qualitative_implication",
    "no_numbers_invented",
    "model_estimate",
    "observed",
    "policy_grade",
    "national_claim",
    "official_osecan_changed",
    "source_artifact",
    "source_field",
)


class DecisionMatrixError(RuntimeError):
    """Raised when a source value or label contract is not trusted."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise DecisionMatrixError(f"required source missing: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _relative(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _close(left: float, right: float) -> bool:
    return abs(left - right) <= TOLERANCE_PJ


def _tier_relation(value_pj: float, tier: str, upper_t4: float) -> str:
    if tier == "above_T4":
        return (
            f"Exceeds T4 upper bound ({upper_t4:.3f} PJ) by "
            f"{value_pj - upper_t4:.3f} PJ."
        )
    return f"Within the adopted {tier} band."


def _tier_upper(tiers: dict[str, dict[str, Any]]) -> float:
    try:
        return float(tiers["T4"]["upper_pj"])
    except (KeyError, TypeError, ValueError) as exc:
        raise DecisionMatrixError("T4 upper bound is missing") from exc


def _check_sources(
    handoff: dict[str, Any],
    chain: dict[str, Any],
    curve: dict[str, Any],
    economics: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    checks: list[dict[str, Any]] = []

    labels = handoff.get("labels", {})
    labels_ok = (
        labels.get("model_estimate") is True
        and labels.get("observed") is False
        and labels.get("policy_grade") is False
        and labels.get("national_claim") is False
        and labels.get("official_osecan_changed") is False
    )
    checks.append(
        {
            "check": "handoff_source_labels",
            "detail": (
                "model_estimate=true, observed=false, policy_grade=false, "
                "national_claim=false, official_osecan_changed=false"
            ),
            "passed": labels_ok,
        }
    )

    totals_ok = True
    for stream, cases in EXPECTED_HANDOFF_TOTALS.items():
        actual = handoff.get("handoff_totals", {}).get(stream, {})
        for case_key, expected in cases.items():
            if not _close(float(actual.get(case_key, float("nan"))), expected):
                totals_ok = False
    checks.append(
        {
            "check": "handoff_totals_reconcile_v4",
            "detail": "harvest 0.35/0.60/0.75 and process 0/25/50/75%",
            "passed": totals_ok,
        }
    )

    tier_positions = handoff.get("supply_curve", {}).get("case_tier", {})
    tiers_ok = all(
        tier_positions.get(f"{stream}/{case_key}") == tier
        for (stream, case_key), tier in EXPECTED_TIERS.items()
    )
    checks.append(
        {
            "check": "osecan_tier_positions_reconcile_v4",
            "detail": "seven stream/case tier positions",
            "passed": tiers_ok,
        }
    )

    scenarios = chain.get("scenarios", {})
    reference = scenarios.get(REFERENCE_CASE, {})
    harvest_ok = _close(
        float(reference.get("harvest_m3", float("nan"))), EXPECTED_HARVEST_M3
    )
    checks.append(
        {
            "check": "reference_harvest_119m",
            "detail": f"harvest_m3={reference.get('harvest_m3')}",
            "passed": harvest_ok,
        }
    )

    curve_reference = curve.get("harvest_base_national", {})
    curve_ok = _close(
        float(curve_reference.get("gross_pj", float("nan"))),
        float(handoff["handoff_totals"]["harvest"]["harvest_recovery_060"])
        / 0.60,
    )
    checks.append(
        {
            "check": "curve_handoff_harvest_gross_consistency",
            "detail": "harvest gross = 60% recoverable / 0.60",
            "passed": curve_ok,
        }
    )

    economics_ok = (
        economics.get("artifact", "") == "national_economics_v2_3"
        or economics.get("model_id", "") == "national_economics_v2_3"
    )
    checks.append(
        {
            "check": "economics_context_read_only",
            "detail": "economics v2.3 is context, not a ranking input",
            "passed": economics_ok,
        }
    )

    tiers = handoff.get("supply_curve", {}).get("tiers", {})
    return tiers, checks


def _numeric_row(
    decision: dict[str, Any],
    case: dict[str, Any],
    value_pj: float,
    tier: str,
    upper_t4: float,
) -> dict[str, Any]:
    return {
        "decision_id": decision["decision_id"],
        "decision": decision["decision"],
        "option_id": case["option_id"],
        "option_label": case["option_label"],
        "case_key": case["case_key"],
        "stream": decision["stream"],
        "value_pj": f"{value_pj:.9f}",
        "unit": decision["unit"],
        "osecan_tier": tier,
        "tier_relation": _tier_relation(value_pj, tier, upper_t4),
        "interpretation": case["interpretation"],
        "recommendation": case["recommendation"],
        "recommendation_role": (
            "screening_default_not_policy"
            if case["screening_default"]
            else "range_member_not_policy"
        ),
        "evidence_grade": "model_estimate_screening",
        "numeric_basis": "reconciled_to_forestry_osecan_handoff_v8",
        "context_reference": "",
        "qualitative_implication": "",
        "no_numbers_invented": "true",
        "model_estimate": "true",
        "observed": "false",
        "policy_grade": "false",
        "national_claim": "false",
        "official_osecan_changed": "false",
        "source_artifact": "forestry_osecan_handoff_v8/summary.json",
        "source_field": f"{decision['source_field']}.{case['case_key']}",
    }


def _qualitative_row(decision: dict[str, Any]) -> dict[str, Any]:
    return {
        "decision_id": decision["decision_id"],
        "decision": decision["decision"],
        "option_id": "qualitative_only",
        "option_label": "Qualitative implication; no quantity assigned",
        "case_key": "",
        "stream": decision["stream"],
        "value_pj": "",
        "unit": "",
        "osecan_tier": "",
        "tier_relation": "not_quantified",
        "interpretation": decision["qualitative_implication"],
        "recommendation": decision["recommended_default"],
        "recommendation_role": decision["default_role"],
        "evidence_grade": "qualitative_context_not_model_output",
        "numeric_basis": "not_quantified",
        "context_reference": decision["context_reference"],
        "qualitative_implication": decision["qualitative_implication"],
        "no_numbers_invented": "true",
        "model_estimate": "true",
        "observed": "false",
        "policy_grade": "false",
        "national_claim": "false",
        "official_osecan_changed": "false",
        "source_artifact": "docs/energy-handoff-v4.md",
        "source_field": f"section_4.{decision['decision_id'].lower()}",
    }


def _build_rows(
    handoff: dict[str, Any], tiers: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    upper_t4 = _tier_upper(tiers)
    rows: list[dict[str, Any]] = []
    for decision in DECISIONS:
        cases = decision["cases"]
        if not cases:
            rows.append(_qualitative_row(decision))
            continue
        stream_totals = handoff["handoff_totals"][decision["stream"]]
        for case in cases:
            value = float(stream_totals[case["case_key"]])
            tier = str(
                handoff["supply_curve"]["case_tier"][
                    f"{decision['stream']}/{case['case_key']}"
                ]
            )
            rows.append(_numeric_row(decision, case, value, tier, upper_t4))
    return rows


def _validate_rows(
    rows: list[dict[str, Any]], handoff: dict[str, Any]
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    numeric = [row for row in rows if row["numeric_basis"] != "not_quantified"]
    qualitative = [row for row in rows if row["numeric_basis"] == "not_quantified"]

    checks.append(
        {
            "check": "matrix_row_counts",
            "detail": (
                f"rows={len(rows)} numeric={len(numeric)} "
                f"qualitative={len(qualitative)}"
            ),
            "passed": len(rows) == 9 and len(numeric) == 7 and len(qualitative) == 2,
        }
    )

    labels_ok = all(
        row["model_estimate"] == "true"
        and row["observed"] == "false"
        and row["policy_grade"] == "false"
        and row["national_claim"] == "false"
        and row["official_osecan_changed"] == "false"
        for row in rows
    )
    checks.append(
        {
            "check": "matrix_row_labels",
            "detail": "model_estimate=true; observed/policy/national=false",
            "passed": labels_ok,
        }
    )

    numeric_ok = True
    for row in numeric:
        value = float(row["value_pj"])
        source = float(
            handoff["handoff_totals"][row["stream"]][row["case_key"]]
        )
        if not _close(value, source):
            numeric_ok = False
        if row["osecan_tier"] != EXPECTED_TIERS[
            (row["stream"], row["case_key"])
        ]:
            numeric_ok = False
    checks.append(
        {
            "check": "numeric_rows_reconcile",
            "detail": "D1 and D2 values and tiers match handoff v8",
            "passed": numeric_ok,
        }
    )

    qualitative_ok = all(
        row["value_pj"] == ""
        and row["osecan_tier"] == ""
        and row["no_numbers_invented"].lower() == "true"
        and len(row["qualitative_implication"]) > 100
        for row in qualitative
    )
    checks.append(
        {
            "check": "qualitative_rows_carry_no_numbers",
            "detail": "D3 and D4 have no PJ value or tier and no invented number",
            "passed": qualitative_ok,
        }
    )

    status_ok = (
        handoff.get("status") == "pass"
        and handoff.get("record_counts", {}).get("harvest_cases") == 3
        and handoff.get("record_counts", {}).get("process_cases") == 4
    )
    checks.append(
        {
            "check": "handoff_status_and_case_counts",
            "detail": "handoff v8 status pass; 3 harvest and 4 process cases",
            "passed": status_ok,
        }
    )
    return checks


def _decision_summary(
    decision: dict[str, Any],
    rows: list[dict[str, Any]],
    handoff: dict[str, Any],
) -> dict[str, Any]:
    cases = [
        {
            "option_id": row["option_id"],
            "option_label": row["option_label"],
            "case_key": row["case_key"],
            "value_pj": (
                float(
                    handoff["handoff_totals"][decision["stream"]][
                        row["case_key"]
                    ]
                )
                if row["case_key"]
                else None
            ),
            "osecan_tier": row["osecan_tier"] or None,
            "tier_relation": row["tier_relation"],
            "interpretation": row["interpretation"],
            "screening_default": any(
                case["case_key"] == row["case_key"] and case["screening_default"]
                for case in decision["cases"]
            ),
        }
        for row in rows
        if row["decision_id"] == decision["decision_id"]
    ]
    return {
        "decision_id": decision["decision_id"],
        "decision": decision["decision"],
        "short_label": decision["short_label"],
        "stream": decision["stream"],
        "question": decision["question"],
        "unit": decision["unit"],
        "cases": cases,
        "recommended_default": decision["recommended_default"],
        "default_case": decision["default_case"],
        "default_role": decision["default_role"],
        "context_reference": decision["context_reference"],
        "qualitative_implication": decision["qualitative_implication"],
        "no_numbers_invented": decision["no_numbers_invented"],
    }


def _render_report(
    summary: dict[str, Any],
    rows: list[dict[str, Any]],
) -> str:
    lines = [
        "# Energy decision-impact matrix v1 (v4 handoff)",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Prepared | {summary['generated_utc'][:10]} |",
        "| Status | Model/screening matrix; `model_estimate`, not observed, not policy grade |",
        f"| Reference case | {REFERENCE_LABEL}; harvest {EXPECTED_HARVEST_M3:,.0f} m3 |",
        "| Source | `forestry_osecan_handoff_v8`, `national_residue_curve_v7`, `national_facility_chain_scenarios_v7` |",
        "| Evidence labels | `model_estimate=true`; `observed=false`; `policy_grade=false`; `national_claim=false` |",
        "| Scope | Read-only; no UI, no official OSeCAN change, no policy target |",
        "",
        "This matrix turns the four open questions in `docs/energy-handoff-v4.md`",
        "into a decision-impact table. It does not choose a policy case. D1 and D2",
        "reconcile exactly to the v4 handoff at the 119 Mm3 planning allocation.",
        "D3 and D4 are qualitative because the required OSeCAN feedstock and demand",
        "denominators are not in the handoff; no number is invented for them.",
        "",
        "## 1. Decision summary",
        "",
        "| ID | Decision | Numeric surface | Screening default | Role |",
        "|---|---|---|---|---|",
        "| D1 | Harvest recovery basis | 0.35 / 0.60 / 0.75 | 0.60 reference-only | Not policy; not calibrated |",
        "| D2 | Process-residue availability | 0 / 25 / 50 / 75% | 25% conservative non-zero | Not policy; not an availability estimate |",
        "| D3 | Biomass/waste incineration | Qualitative | No addition to forestry matrix | Rubino storage-mix context |",
        "| D4 | Industrial feedstock vs energy | Qualitative | Report gross, flag un-netted | Pehl competition context |",
        "",
        "The defaults are screening conveniences, not preferred cases. The v4",
        "handoff says to report full ranges until the definitions are answered.",
        "",
        "## 2. D1 - Harvest recovery basis",
        "",
        "Harvest residue is origin-province only and includes CFS branch and",
        "foliage dry mass. Harvest gross is 444.411 PJ at 119 Mm3.",
        "",
        "| Recovery | Harvest PJ | OSeCAN tier | Relation to T4 | Interpretation |",
        "|---:|---:|---|---|---|",
    ]
    for row in rows:
        if row["decision_id"] != "D1":
            continue
        lines.append(
            f"| {row['option_label']} | {float(row['value_pj']):.3f} | "
            f"{row['osecan_tier']} | {row['tier_relation']} | "
            f"{row['interpretation']} |"
        )
    lines += [
        "",
        "The 35% case is T4; 60% and 75% are above T4. The v3.2-to-v4 move for",
        "35% (T3 to T4) is a coefficient effect, not a recovery measurement.",
        "",
        "## 3. D2 - Process-residue availability",
        "",
        "Process residue is the larger stream: 498.751 PJ at 100% availability.",
        "The matrix carries the four screening cases below.",
        "",
        "| Availability | Process PJ | OSeCAN tier | Relation to T4 | Interpretation |",
        "|---|---:|---|---|---|",
    ]
    for row in rows:
        if row["decision_id"] != "D2":
            continue
        lines.append(
            f"| {row['option_label']} | {float(row['value_pj']):.3f} | "
            f"{row['osecan_tier']} | {row['tier_relation']} | "
            f"{row['interpretation']} |"
        )
    lines += [
        "",
        "The 25% case is T3; 50% and 75% are above T4. One availability",
        "assumption can dominate the comparison with the 2050 energy context.",
        "",
        "## 4. D3 - Biomass and waste incineration (qualitative)",
        "",
        DECISIONS[2]["qualitative_implication"],
        "",
        f"**Screening boundary.** {DECISIONS[2]['recommended_default']}",
        "",
        "## 5. D4 - Industrial feedstock versus energy (qualitative)",
        "",
        DECISIONS[3]["qualitative_implication"],
        "",
        f"**Screening boundary.** {DECISIONS[3]['recommended_default']}",
        "",
        "## 6. Reconciliation to the v4 handoff",
        "",
        "| Check | Result |",
        "|---|---|",
        "| D1 values | 155.544 / 266.646 / 333.308 PJ |",
        "| D1 tiers | T4 / above_T4 / above_T4 |",
        "| D2 values | 0.000 / 124.688 / 249.375 / 374.063 PJ |",
        "| D2 tiers | T1 / T3 / above_T4 / above_T4 |",
        "| Harvest gross | 444.411 PJ |",
        "| Process at 100% (not a matrix row) | 498.751 PJ |",
        f"| T4 upper bound | {summary['tier_thresholds']['T4']:.3f} PJ |",
        "",
        "Process and harvest remain separate streams and are never summed in this",
        "matrix. Total gross (943.162 PJ) is not an OSeCAN row.",
        "",
        "## 7. Labels, limitations, and non-claims",
        "",
        "- All rows are `model_estimate=true`, `observed=false`, `policy_grade=false`,",
        "  `national_claim=false`, and `official_osecan_changed=false`.",
        "- The 119 Mm3 case is a planning allocation, not a legal AAC or a",
        "  sustainable-supply determination.",
        "- F1/D1 recovery factors are equal-status scenarios; 0.60 is a reference",
        "  value only. D2 availability is a screening range, not an OSeCAN parameter.",
        "- Economics v2.3 is read as context only; no cost, margin, or investment",
        "  ranking is applied to any decision row.",
        "- D3 and D4 are qualitative. No non-forestry feedstock quantity, storage",
        "  volume, industrial demand quantity, price, or technology share is",
        "  invented.",
        "- This is not an official OSeCAN result, an operating plan, a policy target,",
        "  or an endorsement by the energy team. No official OSeCAN file was changed.",
        "",
        "## 8. Reproduce",
        "",
        "```bash",
        f".venv/bin/python -m {MODULE_PATH}",
        f".venv/bin/python -m pytest {TEST_PATH} -q",
        "```",
        "",
        "Artifact directory: `data/derived/national/facilities/energy_decision_matrix_v1/`.",
        "Companion memo: `docs/energy-decision-matrix.md`.",
        "",
    ]
    return "\n".join(lines)


def _render_docs(summary: dict[str, Any], report_text: str) -> str:
    del summary
    lines = [
        "# Energy decision-impact matrix v1",
        "",
        "| Field | Value |",
        "|---|---|",
        "| Status | Model/screening matrix; not observed, not policy grade |",
        "| Reference case | 119 Mm3 planning allocation |",
        "| Artifact | `data/derived/national/facilities/energy_decision_matrix_v1/` |",
        "| Module | `workflows/national/facilities/energy_decision_matrix_v1.py` |",
        "| Test | `tests/test_energy_decision_matrix_v1.py` |",
        "| Scope | Read-only; no UI, no official OSeCAN change |",
        "",
        "This is the short decision-oriented companion to",
        "`docs/energy-handoff-v4.md`. For each open decision it gives the",
        "reconciled quantity, OSeCAN tier, interpretation, and a screening",
        "default where one is useful. The defaults are not policy and not",
        "preferred cases.",
        "",
        "## Decision table",
        "",
        "| ID | Decision | Numeric surface | Screening default |",
        "|---|---|---|---|",
        "| D1 | Harvest recovery basis | 0.35 / 0.60 / 0.75 | 0.60 reference-only |",
        "| D2 | Process-residue availability | 0 / 25 / 50 / 75% | 25% conservative non-zero |",
        "| D3 | Biomass/waste incineration | Qualitative, Rubino storage-mix context | No addition until aligned |",
        "| D4 | Industrial feedstock vs energy | Qualitative, Pehl competition context | Report gross, flag un-netted |",
        "",
        "## D1 - Harvest recovery basis",
        "",
        "| Recovery | Harvest PJ | OSeCAN tier | Relation to T4 |",
        "|---:|---:|---|---|",
        "| 35% | 155.544 | T4 | Within T4 |",
        "| 60% | 266.646 | above_T4 | 53.108 PJ above T4 |",
        "| 75% | 333.308 | above_T4 | 119.770 PJ above T4 |",
        "",
        "Harvest gross is 444.411 PJ. The 35% case moved T3 to T4 in v4 via the",
        "coefficient change. The 0.60 factor is a reference value, not a",
        "calibrated recovery factor.",
        "",
        "## D2 - Process-residue availability",
        "",
        "| Availability | Process PJ | OSeCAN tier | Relation to T4 |",
        "|---|---:|---|---|",
        "| 0% | 0.000 | T1 | Zero anchor |",
        "| 25% | 124.688 | T3 | Within T3 |",
        "| 50% | 249.375 | above_T4 | 35.837 PJ above T4 |",
        "| 75% | 374.063 | above_T4 | 160.525 PJ above T4 |",
        "",
        "Process residue at 100% is 498.751 PJ, shown for scale only and not a",
        "matrix row. Process and harvest are separate streams and are never summed.",
        "",
        "## D3 - Biomass and waste incineration",
        "",
        "Qualitative only. If OSeCAN includes municipal solid waste, agricultural",
        "residue, construction/demolition wood, or black liquor in the same",
        "biomass/waste stack, forestry tier positions are not directly comparable",
        "unless feedstock and technology denominators are aligned. The storage mix",
        "can shift, and mill process residue counted both as supply and internal",
        "mill energy would double count. No quantity is invented.",
        "",
        "## D4 - Industrial feedstock versus energy",
        "",
        "Qualitative only. Chemicals, pulp, panel, pellet, liquid fuels, and other",
        "industrial demands can compete for the same residue. The facility chain",
        "does not net out internal mill consumption or existing industrial or",
        "chemical contracts. Report process residue gross and flag it as un-netted",
        "until the OSeCAN demand stack and accounting boundary are supplied. No",
        "quantity is invented.",
        "",
        "## Recommendation and labels",
        "",
        "Use the full ranges first. If a single screening case is required, use",
        "0.60 for D1 as a reference-only case and 25% for D2 as a conservative",
        "non-zero case. Both are model/screening conveniences, not policy",
        "recommendations, not preferred cases, and not calibrated values.",
        "",
        "All rows carry `model_estimate=true`, `observed=false`,",
        "`policy_grade=false`, `national_claim=false`, and",
        "`official_osecan_changed=false`. The 119 Mm3 case is a planning",
        "allocation, not a legal AAC, sustainable-supply, or policy determination.",
        "Economics v2.3 is context only; no cost-based ranking is applied.",
        "",
        "## Artifact",
        "",
        "The machine-readable matrix, summary, manifest, and full report are in",
        "`data/derived/national/facilities/energy_decision_matrix_v1/`.",
        "Reproduce with:",
        "",
        "```bash",
        f".venv/bin/python -m {MODULE_PATH}",
        f".venv/bin/python -m pytest {TEST_PATH} -q",
        "```",
        "",
        "The full report is reproduced below for convenience.",
        "",
        "---",
        "",
        report_text,
    ]
    return "\n".join(lines)


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=list(CSV_FIELDS), lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(rows)


def build(
    output_dir: Path | None = None,
    docs_path: Path | None = None,
) -> dict[str, Any]:
    """Build the decision matrix, report, summary, and manifest."""

    target = Path(output_dir) if output_dir is not None else OUTPUT_DIR
    docs_target = Path(docs_path) if docs_path is not None else DOCS_PATH
    generated_utc = datetime.now(UTC).isoformat(timespec="seconds")

    handoff = _read_json(HANDOFF_SUMMARY)
    chain = _read_json(CHAIN_SUMMARY)
    curve = _read_json(CURVE_SUMMARY)
    economics = _read_json(ECONOMICS_SUMMARY)
    tiers, source_checks = _check_sources(handoff, chain, curve, economics)
    rows = _build_rows(handoff, tiers)
    row_checks = _validate_rows(rows, handoff)

    checks = source_checks + row_checks
    failed = [check for check in checks if not check["passed"]]
    status = "pass" if not failed else "fail"

    thresholds = {
        tier: float(values["upper_pj"]) for tier, values in tiers.items()
    }
    summary: dict[str, Any] = {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "title": ARTIFACT_TITLE,
        "generated_utc": generated_utc,
        "status": status,
        "evidence_label": "model_estimate",
        "reference_case": {
            "label": REFERENCE_LABEL,
            "scenario_key": REFERENCE_CASE,
            "harvest_m3": EXPECTED_HARVEST_M3,
        },
        "tier_thresholds": thresholds,
        "decisions": [
            _decision_summary(decision, rows, handoff) for decision in DECISIONS
        ],
        "record_counts": {
            "decisions": 4,
            "numeric_decisions": 2,
            "qualitative_decisions": 2,
            "matrix_rows": len(rows),
            "numeric_rows": sum(
                1 for row in rows if row["numeric_basis"] != "not_quantified"
            ),
            "qualitative_rows": sum(
                1 for row in rows if row["numeric_basis"] == "not_quantified"
            ),
        },
        "labels": {
            "model_estimate": True,
            "observed": False,
            "policy_grade": False,
            "national_claim": False,
            "official_osecan_changed": False,
        },
        "inputs": {
            _relative(path): _sha256(path)
            for path in (
                HANDOFF_SUMMARY,
                HANDOFF_MANIFEST,
                CHAIN_SUMMARY,
                CURVE_SUMMARY,
                ECONOMICS_SUMMARY,
                HANDOFF_V4_DOC,
            )
        },
        "input_roles": {
            "forestry_osecan_handoff_v8/summary.json": "D1/D2 values and tiers",
            "forestry_osecan_handoff_v8/manifest.json": "handoff provenance",
            "national_facility_chain_scenarios_v7/summary.json": (
                "119 Mm3 harvest reference"
            ),
            "national_residue_curve_v7/summary.json": "harvest gross consistency",
            "national_economics_v2_3/summary.json": (
                "economics context only; no ranking input"
            ),
            "docs/energy-handoff-v4.md": "decision definitions and non-claims",
        },
        "validation": {
            "passed": not failed,
            "failed_check_count": len(failed),
            "checks": checks,
        },
        "non_claims": [
            "Not observed and not policy grade: observed=false, policy_grade=false.",
            "Not a national supply estimate, forecast, legal AAC, sustainable harvest level, tenure decision, or operating plan.",
            "Not an official OSeCAN result; no official OSeCAN file or public surface was changed.",
            "D1 and D2 are equal-status screening cases; the screening defaults are not preferred cases.",
            "D3 and D4 are qualitative; no non-forestry feedstock or industrial demand quantity is invented.",
            "No cost, margin, investment, or policy ranking is applied; economics v2.3 is context only.",
        ],
        "caveats": [
            "The 119 Mm3 case is a planning allocation, not a legal AAC.",
            "0.60 recovery is a reference value, not a calibrated recovery factor.",
            "Process availability 0/25/50/75% is a screening range, not an OSeCAN parameter.",
            "Process and harvest streams remain separate and are never summed in the matrix.",
            "Tier positions compare the quantity with adopted national supply-curve T1-T4 thresholds; above_T4 does not mean undeliverable.",
            "D3/D4 must be re-read once OSeCAN feedstock, technology, and demand boundaries are supplied.",
        ],
        "reproduce": f".venv/bin/python -m {MODULE_PATH}",
        "test": f".venv/bin/python -m pytest {TEST_PATH} -q",
        "docs": _relative(docs_target),
        "artifact_dir": _relative(target),
    }

    target.mkdir(parents=True, exist_ok=True)
    docs_target.parent.mkdir(parents=True, exist_ok=True)

    _write_csv(target / "decision_matrix.csv", rows)
    report_text = _render_report(summary, rows)
    (target / REPORT_NAME).write_text(report_text, encoding="utf-8")
    (target / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    docs_target.write_text(
        _render_docs(summary, report_text), encoding="utf-8"
    )

    output_paths = (
        target / "decision_matrix.csv",
        target / REPORT_NAME,
        target / "summary.json",
    )
    manifest: dict[str, Any] = {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "title": ARTIFACT_TITLE,
        "generated_utc": generated_utc,
        "status": status,
        "labels": summary["labels"],
        "reference_case": summary["reference_case"],
        "record_counts": summary["record_counts"],
        "inputs": summary["inputs"],
        "input_roles": summary["input_roles"],
        "outputs": {
            path.name: {
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
            for path in output_paths
        },
        "validation": summary["validation"],
        "reproduce": summary["reproduce"],
        "test": summary["test"],
        "docs": summary["docs"],
    }
    (target / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if status != "pass":
        failed_names = ", ".join(check["check"] for check in failed)
        raise DecisionMatrixError(f"decision matrix validation failed: {failed_names}")
    return summary


def main() -> None:
    summary = build()
    print(
        json.dumps(
            {
                "artifact": summary["artifact"],
                "status": summary["status"],
                "validation": summary["validation"]["passed"],
                "record_counts": summary["record_counts"],
                "artifact_dir": summary["artifact_dir"],
                "docs": summary["docs"],
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
