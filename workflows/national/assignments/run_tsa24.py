"""Run the TSA24/BC Stage A facility-assignment pilot.

Examples
--------
Base diagnostic (1,000 ha)::

    .venv/bin/python -m workflows.national.assignments.run_tsa24

Base plus the optional ws4-quota sensitivity::

    .venv/bin/python -m workflows.national.assignments.run_tsa24 --sensitivity-ws4-quota

The base run is a contract and data-shape pilot, not a TSA supply estimate.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .fixtures import (
    DIAGNOSTIC_ACCESS_KM,
    Tsa24Fixture,
    build_tsa24_fixture,
    write_fixture_tables,
)
from .model import solve_assignment
from .schema import AssignmentInputs, AssignmentResult, to_stage_b_tables
from .validate import ValidationReport, validate_assignment

__all__ = ["main", "run_pilot"]

CODE_VERSION = "0.1.0"
DEFAULT_OUT_DIR = Path("data/derived/national/assignments/tsa24_pilot")
DEFAULT_AREA_HA = 1000.0
WS4_QUOTA_AREA_HA = 306047.3

ASSIGNMENT_COLUMNS = [
    "assignment_id",
    "period",
    "supply_id",
    "origin",
    "grade_id",
    "facility_id",
    "facility_name",
    "facility_class",
    "volume_m3",
    "moisture_wet_basis",
    "supply_cost_cad_m3",
    "transport_cost_cad_m3",
    "gate_cost_cad_m3",
    "processing_cost_cad_m3",
    "product_revenue_cad_m3",
    "net_contribution_cad_m3",
    "contribution_cad",
    "transport_link_id",
    "evidence",
]
UTILIZATION_COLUMNS = [
    "facility_id",
    "facility_name",
    "facility_class",
    "nameplate_m3_y",
    "assigned_m3",
    "unused_capacity_m3",
    "utilization",
    "capacity_binding",
    "eligible_grades",
]
GRADE_UTILIZATION_COLUMNS = [
    "facility_id",
    "facility_name",
    "facility_class",
    "grade_id",
    "grade_capacity_m3_y",
    "assigned_m3",
    "utilization",
    "binding",
]
CONTRACT_COLUMNS = [
    "contract_id",
    "facility_id",
    "period",
    "accepted_grades",
    "min_volume_m3",
    "max_volume_m3",
    "assigned_m3",
    "within_minimum",
    "within_maximum",
    "gate_cost_cad_m3",
    "diagnostic",
    "evidence",
    "notes",
]
UNASSIGNED_COLUMNS = [
    "supply_id",
    "period",
    "grade_id",
    "origin",
    "volume_m3",
    "reason",
    "penalty_cost_cad_m3",
]
OBJECTIVE_COLUMNS = ["component", "value_cad", "unit", "notes"]


def _write_csv(
    path: Path, rows: Sequence[Mapping[str, Any]], columns: Sequence[str]
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=list(columns),
            extrasaction="ignore",
            lineterminator="\n",
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fmt(value: float, digits: int = 0) -> str:
    if value is None or not math.isfinite(float(value)):
        return "n/a"
    return f"{float(value):,.{digits}f}"


def _pct(value: float, digits: int = 1) -> str:
    if value is None or not math.isfinite(float(value)):
        return "n/a"
    return f"{100.0 * float(value):.{digits}f}%"


def _markdown_table(headers: Sequence[str], rows: Sequence[Sequence[object]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(value) for value in row) + " |")
    return "\n".join(lines)


def _ffcm_class_mix(repo_root: Path) -> dict[str, float] | None:
    path = repo_root / "metadata/ffcm-archive/lp-forensics/lp-variable-families.csv"
    if not path.exists():
        return None
    counts: dict[str, float] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if str(row.get("source_class", "")).strip().lower() != "fmu":
                continue
            destination = str(row.get("destination_class", "")).strip().upper()
            if destination not in {"SAW", "PAP", "PEL", "BIO"}:
                continue
            counts[destination] = counts.get(destination, 0.0) + float(
                row.get("variable_count", 0.0) or 0.0
            )
    total = sum(counts.values())
    if total <= 0:
        return None
    return {key: value / total for key, value in counts.items()}


def _grade_summary(
    inputs: AssignmentInputs, result: AssignmentResult
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for grade in inputs.grades:
        supplies = [item for item in inputs.supplies if item.grade_id == grade.grade_id]
        supplied = sum(item.volume_m3 for item in supplies)
        assigned = sum(
            item.volume_m3
            for item in result.assignments
            if item.grade_id == grade.grade_id
        )
        unassigned = sum(
            item.volume_m3
            for item in result.unassigned
            if item.grade_id == grade.grade_id
        )
        cost_values = [item.roadside_cost_cad_m3 for item in supplies]
        rows.append(
            {
                "grade_id": grade.grade_id,
                "product": grade.product,
                "unit": grade.unit,
                "offer_count": len(supplies),
                "supplied_m3": supplied,
                "supplied_odt": sum(item.volume_odt for item in supplies),
                "assigned_m3": assigned,
                "unassigned_m3": unassigned,
                "assigned_share": assigned / supplied if supplied > 0 else 0.0,
                "unassigned_share": unassigned / supplied if supplied > 0 else 0.0,
                "roadside_cost_min_cad_m3": min(cost_values) if cost_values else 0.0,
                "roadside_cost_max_cad_m3": max(cost_values) if cost_values else 0.0,
                "density_odt_m3": grade.density_odt_m3,
                "moisture_wet_basis": grade.moisture_wet_basis,
                "evidence": grade.evidence,
            }
        )
    return rows


def _class_summary(
    inputs: AssignmentInputs, result: AssignmentResult
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    utilization = {row["facility_id"]: row for row in result.facility_utilization}
    for facility_class in sorted(
        {facility.facility_class for facility in inputs.facilities}
    ):
        facilities = [
            facility
            for facility in inputs.facilities
            if facility.facility_class == facility_class
        ]
        nameplate = sum(facility.nameplate_m3_y for facility in facilities)
        assigned = sum(
            utilization.get(facility.facility_id, {}).get("assigned_m3", 0.0)
            for facility in facilities
        )
        binding = sum(
            1
            for facility in facilities
            if utilization.get(facility.facility_id, {}).get("capacity_binding", False)
        )
        facility_ids = {facility.facility_id for facility in facilities}
        links = sum(
            1 for link in inputs.transport_links if link.destination in facility_ids
        )
        linked_facilities = {
            link.destination
            for link in inputs.transport_links
            if link.destination in facility_ids
        }
        rows.append(
            {
                "facility_class": facility_class,
                "facility_count": len(facilities),
                "linked_facility_count": len(linked_facilities),
                "nameplate_m3_y": nameplate,
                "assigned_m3": assigned,
                "utilization": assigned / nameplate if nameplate > 0 else 0.0,
                "binding_facility_count": binding,
                "transport_link_count": links,
            }
        )
    return rows


def _report_markdown(
    repo_root: Path,
    fixture: Tsa24Fixture,
    result: AssignmentResult,
    validation: ValidationReport,
) -> str:
    inputs = fixture.inputs
    grade_rows = _grade_summary(inputs, result)
    class_rows = _class_summary(inputs, result)
    ffcm_mix = _ffcm_class_mix(repo_root)
    total_supply = sum(item.volume_m3 for item in inputs.supplies)

    grade_table = _markdown_table(
        [
            "Grade",
            "Product",
            "Offers",
            "Supply m3",
            "Supply ODT",
            "Assigned m3",
            "Unassigned m3",
            "Assigned share",
            "Roadside CAD/m3",
        ],
        [
            (
                row["grade_id"],
                row["product"],
                row["offer_count"],
                _fmt(row["supplied_m3"]),
                _fmt(row["supplied_odt"], 0) if row["supplied_odt"] > 0 else "0",
                _fmt(row["assigned_m3"]),
                _fmt(row["unassigned_m3"]),
                _pct(row["assigned_share"]),
                (
                    f"{_fmt(row['roadside_cost_min_cad_m3'], 2)}-"
                    f"{_fmt(row['roadside_cost_max_cad_m3'], 2)}"
                ),
            )
            for row in grade_rows
        ],
    )

    class_table = _markdown_table(
        [
            "Class",
            "Facilities",
            "With link",
            "Nameplate m3/y",
            "Assigned m3",
            "Utilization",
            "Binding",
            "Links",
        ],
        [
            (
                row["facility_class"],
                row["facility_count"],
                row["linked_facility_count"],
                _fmt(row["nameplate_m3_y"]),
                _fmt(row["assigned_m3"]),
                _pct(row["utilization"]),
                row["binding_facility_count"],
                row["transport_link_count"],
            )
            for row in class_rows
        ],
    )

    objective_table = _markdown_table(
        ["Component", "Value CAD", "Unit", "Notes"],
        [
            (row["component"], _fmt(row["value_cad"], 2), row["unit"], row["notes"])
            for row in result.objective_decomposition
        ],
    )

    validation_table = _markdown_table(
        ["Check", "Pass", "Detail"],
        [
            (check.check_id, "yes" if check.passed else "NO", check.detail)
            for check in validation.checks
        ],
    )

    top_facilities = [
        row
        for row in sorted(
            result.facility_utilization,
            key=lambda row: row["assigned_m3"],
            reverse=True,
        )
        if row["assigned_m3"] > 0.0
    ][:10]
    top_table = _markdown_table(
        ["Facility", "Class", "Assigned m3", "Nameplate m3/y", "Utilization"],
        [
            (
                row["facility_id"],
                row["facility_class"],
                _fmt(row["assigned_m3"]),
                _fmt(row["nameplate_m3_y"]),
                _pct(row["utilization"]),
            )
            for row in top_facilities
        ],
    )

    if ffcm_mix is not None:
        pilot_volume = {row["facility_class"]: row["assigned_m3"] for row in class_rows}
        pilot_total = sum(pilot_volume.values())
        mix_rows = []
        for facility_class in ("SAW", "PAP", "PEL", "BIO"):
            mix_rows.append(
                (
                    facility_class,
                    _fmt(pilot_volume.get(facility_class, 0.0)),
                    _pct(
                        pilot_volume.get(facility_class, 0.0) / pilot_total
                        if pilot_total
                        else 0.0
                    ),
                    _pct(ffcm_mix.get(facility_class, 0.0)),
                )
            )
        class_mix_table = _markdown_table(
            ["Class", "Pilot assigned m3", "Pilot share", "FFCM fmu-primary share"],
            mix_rows,
        )
        class_mix_note = (
            "FFCM shares are primary `fmu -> facility` variable families from "
            "`lp-variable-families.csv`; PLY/OSB/export and secondary "
            "processor-to-processor families are outside this pilot and are "
            "excluded from both shares. The comparison is directional, not a "
            "calibration target."
        )
    else:
        class_mix_table = (
            "FFCM variable-family table not found; no class-mix comparison made."
        )
        class_mix_note = ""

    contract_rows = result.contract_compliance
    contract_ok = sum(
        1 for row in contract_rows if row["within_minimum"] and row["within_maximum"]
    )
    binding_rows = [
        row for row in result.facility_utilization if row["capacity_binding"]
    ]
    binding_table = (
        _markdown_table(
            ["Facility", "Class", "Assigned m3", "Nameplate m3/y", "Utilization"],
            [
                (
                    row["facility_id"],
                    row["facility_class"],
                    _fmt(row["assigned_m3"]),
                    _fmt(row["nameplate_m3_y"]),
                    _pct(row["utilization"]),
                )
                for row in binding_rows[:10]
            ],
        )
        if binding_rows
        else "No facility reached nameplate capacity in this run."
    )

    assumption_rows = fixture.tables["assumptions"]
    assumption_table = _markdown_table(
        ["Category", "Assumption", "Value", "Unit", "Evidence"],
        [
            (
                row["category"],
                row["assumption"],
                row["value"],
                row["unit"],
                row["evidence"],
            )
            for row in assumption_rows
            if row["category"]
            in {
                "scope",
                "supply",
                "residue",
                "facility_capacity",
                "transport",
                "contracts",
                "objective",
                "grade",
            }
        ],
    )

    rejected_lines = [
        row
        for row in fixture.tables["line_screen"]
        if row["capacity_status"] == "rejected"
    ]
    rejected_transport = [
        row
        for row in fixture.tables["transport_screen"]
        if row["status"] != "accepted_diagnostic_route"
    ]
    rejected_table = (
        _markdown_table(
            ["Facility", "Line", "Reason"],
            [
                (row["facility_id"], row["line_id"], row["reason"])
                for row in rejected_lines[:15]
            ],
        )
        if rejected_lines
        else "No processing line was rejected for its capacity unit."
    )

    is_sensitivity = float(fixture.config["area_ha"]) > DEFAULT_AREA_HA * 1.01
    sensitivity_note = (
        "> **Period-scale sensitivity.** This run uses the ws4 quota area "
        f"`{_fmt(fixture.config['area_ha'])} ha/period`. It is not an annual "
        "supply forecast and its volume must not be compared directly with "
        "annual facility nameplate without a period normalization.\n"
        if is_sensitivity
        else ""
    )
    report = f"""# TSA24/BC Stage A Facility Assignment Pilot

> **Diagnostic pilot.** The supply area is `{_fmt(fixture.config["area_ha"])} ha`, a
> contract/data-shape test, not a TSA24 supply estimate. Assortment sorts are
> dimension-only; no BC official grade equivalence is claimed. MKRF is not used.
{sensitivity_note}
## 1. Scope and Stage A/B split

Stage A consumes the TSA24 assortment matrix, parsed FFCM BC facility lines, a
diagnostic road transport layer, and diagnostic contracts, then solves a
profit-maximising transportation/allocation LP. It emits facility assignment,
utilization, contract, unassigned-supply, objective, and validation tables.
Stage B (`fresh_fibre.graph` / `fresh_fibre.lp`) consumes the Stage B
`supply_offer`, `technology`, and `transport_link` subset written alongside this
report; Stage A does not invent Stage B demand or market rows.

- Scenario: `{inputs.scenario_id}`
- Period: `{inputs.periods[0]}`
- Supply offers: {len(inputs.supplies)}
- Facilities: {len(inputs.facilities)} screened; {len({link.destination for link in inputs.transport_links})} with a transport link
- Transport links: {len(inputs.transport_links)}
- Contracts: {len(inputs.contracts)}
- Total supply: {_fmt(total_supply)} m3 green
- Solver: {result.solver_name} {result.solver_version} ({result.status})

## 2. Fixture

### 2.1 Supply grades

The matrix is a per-hectare TSA24 stand composition. Each detail row is scaled
by the diagnostic area; the total row is a check, not a supply offer. Residue
uses the ledger's 0.45 ODT/m3 density and 0.45 wet-basis moisture.

{grade_table}

### 2.2 Facilities

Only BC `SAW`, `PAP`, `PEL`, and `BIO` facilities are in scope. Capacity is
normalized to m3/y from `m3/y` directly and from `odt/y` at 1/0.45 m3 per ODT.
`bdmt/y` capacity lines are rejected; for PAP they duplicate the paired `m3/y`
log line. Facility nameplate is the sum of the maximum normalized capacity per
primary product, so alternative feedstock lines are not counted repeatedly.

{class_table}

Rejected processing lines (first 15):

{rejected_table}

Facilities without an FFCM road route to a transload node are excluded from the
transport layer, not given a fabricated distance. Rejected facilities:
{len(rejected_transport)}.

### 2.3 Transport

The archive has no TSA24 supply-node-to-transload route. The pilot therefore
uses the minimum FFCM facility-to-`TL` road distance plus a documented
`{_fmt(DIAGNOSTIC_ACCESS_KM)} km` TSA24 access leg. This is a diagnostic road
estimate, not a Euclidean distance and not a production route. The facility leg
is from `routes.csv`; the supply leg is an assumption. Road cost uses
`BC_logging_truck` for logs and `BC_residue_truck` for residue, with archive
fuel/operating rates and truck capacities. No transload cost is applied because
the diagnostic path is direct road. Route capacity is absent from the parsed
route table, so links are uncapped.

### 2.4 Contracts

No populated contract table exists in the archive. Each facility receives a
diagnostic contract with `min=0`, `max=nameplate`, and `gate=0`. Contract
compliance is therefore a bound check, not evidence about real commercial terms.

## 3. LP formulation

Maximise product revenue minus roadside supply, transport, gate, processing,
and unassigned penalty:

```
max  sum_{{s,f,g,t}} (R_fg - C_s - H_sf - G_fg - P_fg) x[s,f,g,t]
     - penalty * sum_{{s,g,t}} u[s,g,t]
```

Subject to:

- supply balance: `sum_f x[s,f,g,t] + u[s,g,t] = V_s`
- facility nameplate: `sum_{{s,g}} x[s,f,g,t] <= nameplate_f`
- grade capacity: `sum_s x[s,f,g,t] <= capacity_fg`
- contract minimum/maximum per facility and period
- optional moisture blend: `sum_s M_s x <= limit_f * sum_s x`
- optional transport-link capacity
- `x >= 0`, `u >= 0`; no binaries in this pilot

There are no `y[f,l,t]` line-activity variables because the fixture aggregates
alternative feedstock lines into one grade-specific capacity and keeps the
facility nameplate as the total cap. `x` is in m3 green; `u` is in the same
basis. The multi-period key is present, but the pilot uses one period.

## 4. Results

### 4.1 Assigned versus unassigned by grade

{grade_table}

### 4.2 Facility utilization by class

{class_table}

### 4.3 Capacity binding

{binding_table}

### 4.4 Largest assignments

{top_table}

### 4.5 Contract compliance

{contract_ok} of {len(contract_rows)} diagnostic contracts satisfy both bounds.
All contracts are marked `diagnostic`.

### 4.6 Objective decomposition

{objective_table}

### 4.7 Facility-class mix versus FFCM

{class_mix_table}

{class_mix_note}

## 5. Validation

{validation_table}

Overall validation: **{"PASS" if validation.passed else "FAIL"}**.

## 6. Assumptions and unit conversions

{assumption_table}

The full assumption list, including rejected lines and rejected transport
links, is written to `assumptions.csv`, `line_screen.csv`, and
`transport_screen.csv`.

## 7. Gaps and next steps

- Replace the diagnostic 150 km TSA24 access leg with a real TSA24 supply-node
  to transload/facility route or an accepted haul-distance model.
- Replace the diagnostic contracts with populated minimum/maximum terms.
- Add route/transload capacity and water/rail alternatives; the current pilot
  is road-only and uncapped.
- Normalize `bdmt/y`/`admt/y` capacity only after an accepted project
  convention; the pilot rejects bdmt capacity rather than guessing.
- Add mill-residue supply (sawdust/woodchips/hogfuel) if the Stage A scope is
  extended beyond logging residue.
- Add multi-period inventory, demand/market clearing, and line-level `y`
  variables only when the data support them.
- Do not interpret the 1,000 ha result as a TSA24 supply estimate. The optional
  ws4-quota sensitivity is a period-scale diagnostic and must be labeled as
  such.
"""
    return report


def _manifest(
    out_dir: Path,
    fixture: Tsa24Fixture,
    result: AssignmentResult,
    validation: ValidationReport,
    report_path: Path,
) -> dict[str, Any]:
    output_paths = sorted(
        [
            path
            for path in out_dir.iterdir()
            if path.is_file() and path.name != "manifest.json"
        ]
    )
    outputs = {
        path.name: {
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }
        for path in output_paths
    }
    return {
        "pilot": "tsa24_bc_stage_a_facility_assignment",
        "code_version": CODE_VERSION,
        "generated_utc": datetime.now(UTC).isoformat(),
        "status": "pass" if validation.passed else "fail",
        "run": {
            "run_id": result.run_id,
            "scenario_id": result.scenario_id,
            "period": result.period,
            "solver": result.solver_name,
            "solver_version": result.solver_version,
            "solver_status": result.status,
            "objective_value_cad": result.objective_value_cad,
        },
        "config": dict(fixture.config),
        "counts": result.inputs_summary,
        "source_hashes": dict(fixture.source_hashes),
        "outputs": outputs,
        "report": report_path.name,
        "assumptions": list(fixture.tables["assumptions"]),
        "limitations": [
            "1,000 ha base area is diagnostic, not a TSA24 supply estimate.",
            "Assortment sorts are dimension-only; no BC official grade equivalence.",
            "TSA24 access distance is a diagnostic 150 km leg; not Euclidean and not a production route.",
            "Only BC SAW/PAP/PEL/BIO facilities are in scope.",
            "bdmt/y capacity lines are rejected; PAP uses paired m3/y log lines.",
            "Contracts are diagnostic min=0/max=nameplate; no populated contract data.",
            "Transport links are road-only and uncapped; transload costs are not applied.",
            "One period; no multi-period inventory dynamics.",
            "No MKRF input or output is used.",
        ],
    }


def _write_outputs(
    out_dir: Path,
    fixture: Tsa24Fixture,
    result: AssignmentResult,
    validation: ValidationReport,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(
        out_dir / "facility_assignment.csv",
        [item.to_dict() for item in result.assignments],
        ASSIGNMENT_COLUMNS,
    )
    _write_csv(
        out_dir / "facility_utilization.csv",
        result.facility_utilization,
        UTILIZATION_COLUMNS,
    )
    grade_rows = [
        {
            "facility_id": row["facility_id"],
            "facility_name": row["facility_name"],
            "facility_class": row["facility_class"],
            "grade_id": grade["grade_id"],
            "grade_capacity_m3_y": grade["capacity_m3_y"],
            "assigned_m3": grade["assigned_m3"],
            "utilization": grade["utilization"],
            "binding": grade["binding"],
        }
        for row in result.facility_utilization
        for grade in row["grade_rows"]
    ]
    _write_csv(
        out_dir / "facility_grade_utilization.csv",
        grade_rows,
        GRADE_UTILIZATION_COLUMNS,
    )
    _write_csv(
        out_dir / "contract_compliance.csv",
        result.contract_compliance,
        CONTRACT_COLUMNS,
    )
    _write_csv(
        out_dir / "unassigned_supply.csv",
        [item.to_dict() for item in result.unassigned],
        UNASSIGNED_COLUMNS,
    )
    _write_csv(
        out_dir / "objective_decomposition.csv",
        result.objective_decomposition,
        OBJECTIVE_COLUMNS,
    )
    write_fixture_tables(fixture, out_dir)
    stage_b = to_stage_b_tables(fixture.inputs, result)
    for name, rows in stage_b.items():
        if not rows:
            continue
        columns = list(rows[0].keys())
        _write_csv(out_dir / f"stage_b_{name}.csv", rows, columns)

    validation_payload = {
        "run_id": result.run_id,
        "scenario_id": result.scenario_id,
        "period": result.period,
        "status": "pass" if validation.passed else "fail",
        "validation": validation.to_dict(),
        "inputs": result.inputs_summary,
        "config": dict(fixture.config),
        "data_gaps": [
            "no TSA24 supply-node-to-transload route in the parsed FFCM graph",
            "only 8 of 179 BC facilities have coordinates in facility_geocoding.csv",
            "route capacity absent from routes.csv",
            "no populated contract table",
            "mixed capacity units require bdmt/y rejection and odt/y normalization",
            "no hogfuel supply in the TSA24 assortment matrix",
            "no mill-residue (sawdust/woodchips) supply in this pilot",
        ],
        "assumptions": list(fixture.tables["assumptions"]),
    }
    (out_dir / "validation_summary.json").write_text(
        json.dumps(validation_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def run_pilot(
    repo_root: Path,
    out_dir: Path,
    *,
    area_ha: float,
    period: str,
    scenario_id: str,
    sensitivity_label: str = "",
) -> tuple[AssignmentResult, ValidationReport, Tsa24Fixture]:
    """Build, solve, validate, and write one Stage A pilot run."""
    fixture = build_tsa24_fixture(
        repo_root, area_ha=area_ha, period=period, scenario_id=scenario_id
    )
    result = solve_assignment(fixture.inputs)
    validation = validate_assignment(fixture.inputs, result)
    result = replace(result, validation=validation.to_dict())

    report_text = _report_markdown(repo_root, fixture, result, validation)
    report_path = out_dir / "ASSIGNMENT_REPORT.md"
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report_text, encoding="utf-8")
    _write_outputs(out_dir, fixture, result, validation)

    manifest = _manifest(out_dir, fixture, result, validation, report_path)
    if sensitivity_label:
        manifest["sensitivity_label"] = sensitivity_label
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result, validation, fixture


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[3],
        help="Repository root (defaults to the package location).",
    )
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--area-ha", type=float, default=DEFAULT_AREA_HA)
    parser.add_argument("--period", default="2026")
    parser.add_argument("--scenario-id", default="tsa24_bc_stage_a_pilot")
    parser.add_argument(
        "--sensitivity-ws4-quota",
        action="store_true",
        help="Also run the documented ws4 quota area as a period-scale diagnostic.",
    )
    parser.add_argument("--msg", action="store_true", help="Show CBC solver output.")
    args = parser.parse_args(argv)

    repo_root = args.repo_root.resolve()
    out_dir = args.out_dir
    if not out_dir.is_absolute():
        out_dir = (Path.cwd() / out_dir).resolve()

    result, validation, fixture = run_pilot(
        repo_root,
        out_dir,
        area_ha=args.area_ha,
        period=args.period,
        scenario_id=args.scenario_id,
    )
    print(
        f"base: status={result.status} objective={result.objective_value_cad:,.2f} CAD; "
        f"assigned={result.assigned_volume_m3:,.0f} m3; "
        f"unassigned={result.unassigned_volume_m3:,.0f} m3; "
        f"validation={'PASS' if validation.passed else 'FAIL'}; "
        f"facilities={len(fixture.inputs.facilities)}; links={len(fixture.inputs.transport_links)}"
    )

    exit_code = 0 if validation.passed else 1
    if args.sensitivity_ws4_quota:
        sensitivity_dir = out_dir / "sensitivity_ws4_quota"
        sensitivity_result, sensitivity_validation, sensitivity_fixture = run_pilot(
            repo_root,
            sensitivity_dir,
            area_ha=WS4_QUOTA_AREA_HA,
            period=args.period,
            scenario_id=f"{args.scenario_id}_ws4_quota",
            sensitivity_label="ws4_quota_306047.3_ha_period_not_annual",
        )
        print(
            f"sensitivity: status={sensitivity_result.status} "
            f"objective={sensitivity_result.objective_value_cad:,.2f} CAD; "
            f"assigned={sensitivity_result.assigned_volume_m3:,.0f} m3; "
            f"unassigned={sensitivity_result.unassigned_volume_m3:,.0f} m3; "
            f"validation={'PASS' if sensitivity_validation.passed else 'FAIL'}; "
            f"facilities={len(sensitivity_fixture.inputs.facilities)}"
        )
        if not sensitivity_validation.passed:
            exit_code = 1
    if not validation.passed:
        for check in validation.failed_checks:
            print(f"FAILED {check.check_id}: {check.detail}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
