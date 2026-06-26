"""Export normalized ws3 harvest output to the FRESH supply-offer contract."""

from __future__ import annotations

import argparse
import hashlib
from dataclasses import dataclass
from pathlib import Path

from fresh_fibre.schema import number, read_rows, require_columns, write_rows


DEFAULT_INPUT_DIR = Path("metadata/ffcm-archive/prototype-inputs/bc-mini")
DEFAULT_WS3_OUTPUT_PATH = DEFAULT_INPUT_DIR / "ws3_harvest_output.csv"
DEFAULT_OUTPUT_PATH = DEFAULT_INPUT_DIR / "generated" / "supply_offer_from_ws3.csv"
DEFAULT_MANIFEST_PATH = DEFAULT_INPUT_DIR / "generated" / "ws3_export_manifest.csv"

SUPPLY_OFFER_COLUMNS = [
    "scenario_id",
    "period",
    "spatial_unit_id",
    "supply_node_id",
    "commodity_id",
    "quantity",
    "quantity_unit",
    "supply_cost",
    "supply_cost_unit",
    "species_group",
    "assortment_class",
    "grade",
    "moisture_basis",
    "carbon_factor",
    "harvest_action_id",
    "roadside_cost",
    "delivered_cost_proxy",
]

WS3_HARVEST_COLUMNS = set(SUPPLY_OFFER_COLUMNS) | {
    "ws3_run_id",
    "source_model",
    "source_record_id",
    "notes",
}

EXPORT_MANIFEST_COLUMNS = [
    "artifact_role",
    "table_path",
    "row_count",
    "sha256",
    "ws3_run_ids",
    "source_models",
    "notes",
]


@dataclass(frozen=True)
class Ws3HarvestOutput:
    """One normalized ws3 harvest-output record ready for LP handoff."""

    row: dict[str, str]

    @classmethod
    def from_row(cls, row: dict[str, str]) -> "Ws3HarvestOutput":
        return cls(row)

    def validate(self) -> None:
        record_id = self.row.get("source_record_id") or self.row.get("harvest_action_id") or "<unknown>"
        quantity = number(self.row["quantity"])
        supply_cost = number(self.row["supply_cost"])
        if quantity < 0:
            raise ValueError(f"ws3 harvest record {record_id} has negative quantity: {quantity}")
        if supply_cost < 0:
            raise ValueError(f"ws3 harvest record {record_id} has negative supply cost: {supply_cost}")

    def to_supply_offer(self) -> dict[str, str]:
        return {column: self.row.get(column, "") for column in SUPPLY_OFFER_COLUMNS}


def validate_references(
    rows: list[Ws3HarvestOutput],
    input_dir: Path | None,
) -> None:
    if input_dir is None:
        return

    scenario_ids = {row["scenario_id"] for row in read_rows(input_dir / "scenario.csv")}
    spatial_unit_ids = {row["spatial_unit_id"] for row in read_rows(input_dir / "spatial_unit.csv")}
    node_ids = {row["node_id"] for row in read_rows(input_dir / "network_node.csv")}
    commodity_ids = {row["commodity_id"] for row in read_rows(input_dir / "commodity.csv")}

    errors: list[str] = []
    for record in rows:
        row = record.row
        label = row.get("source_record_id") or row.get("harvest_action_id") or "<unknown>"
        if row["scenario_id"] not in scenario_ids:
            errors.append(f"{label} references unknown scenario {row['scenario_id']}")
        if row["spatial_unit_id"] not in spatial_unit_ids:
            errors.append(f"{label} references unknown spatial unit {row['spatial_unit_id']}")
        if row["supply_node_id"] not in node_ids:
            errors.append(f"{label} references unknown supply node {row['supply_node_id']}")
        if row["commodity_id"] not in commodity_ids:
            errors.append(f"{label} references unknown commodity {row['commodity_id']}")

    if errors:
        joined = "\n  - ".join(errors)
        raise ValueError(f"ws3 harvest-output reference validation failed:\n  - {joined}")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def unique_values(rows: list[dict[str, str]], column: str) -> str:
    values = sorted({row.get(column, "") for row in rows if row.get(column, "")})
    return "; ".join(values)


def build_export_manifest(
    ws3_output_path: Path,
    output_path: Path,
    input_dir: Path | None,
) -> list[dict[str, object]]:
    ws3_rows = read_rows(ws3_output_path)
    output_rows = read_rows(output_path)
    rows: list[dict[str, object]] = [
        {
            "artifact_role": "normalized_ws3_harvest_output",
            "table_path": str(ws3_output_path),
            "row_count": len(ws3_rows),
            "sha256": file_sha256(ws3_output_path),
            "ws3_run_ids": unique_values(ws3_rows, "ws3_run_id"),
            "source_models": unique_values(ws3_rows, "source_model"),
            "notes": "Input table emitted by the upstream ws3 normalization boundary.",
        },
        {
            "artifact_role": "exported_supply_offer",
            "table_path": str(output_path),
            "row_count": len(output_rows),
            "sha256": file_sha256(output_path),
            "ws3_run_ids": unique_values(ws3_rows, "ws3_run_id"),
            "source_models": "fresh_fibre.ws3_export",
            "notes": "Adapter output written in the FRESH supply_offer contract shape.",
        },
    ]
    if input_dir is not None:
        contract_supply_offer_path = input_dir / "supply_offer.csv"
        if contract_supply_offer_path.exists():
            contract_rows = read_rows(contract_supply_offer_path)
            rows.append(
                {
                    "artifact_role": "downstream_supply_offer_contract",
                    "table_path": str(contract_supply_offer_path),
                    "row_count": len(contract_rows),
                    "sha256": file_sha256(contract_supply_offer_path),
                    "ws3_run_ids": unique_values(ws3_rows, "ws3_run_id"),
                    "source_models": "fixture_contract",
                    "notes": "Current downstream LP input contract table used by the prototype.",
                }
            )
    return rows


def export_supply_offer(
    ws3_output_path: Path = DEFAULT_WS3_OUTPUT_PATH,
    output_path: Path = DEFAULT_OUTPUT_PATH,
    input_dir: Path | None = DEFAULT_INPUT_DIR,
    manifest_path: Path | None = None,
    write_manifest: bool = True,
) -> list[dict[str, str]]:
    rows = read_rows(ws3_output_path)
    require_columns(ws3_output_path, rows, WS3_HARVEST_COLUMNS)
    records = [Ws3HarvestOutput.from_row(row) for row in rows]
    for record in records:
        record.validate()
    validate_references(records, input_dir)
    supply_offer_rows = [record.to_supply_offer() for record in records]
    write_rows(output_path, SUPPLY_OFFER_COLUMNS, supply_offer_rows)
    if write_manifest:
        manifest_path = manifest_path or output_path.parent / DEFAULT_MANIFEST_PATH.name
        manifest_rows = build_export_manifest(ws3_output_path, output_path, input_dir)
        write_rows(manifest_path, EXPORT_MANIFEST_COLUMNS, manifest_rows)
    return supply_offer_rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR)
    parser.add_argument("--ws3-output-path", type=Path, default=DEFAULT_WS3_OUTPUT_PATH)
    parser.add_argument("--output-path", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--manifest-path", type=Path, default=DEFAULT_MANIFEST_PATH)
    parser.add_argument(
        "--skip-reference-validation",
        action="store_true",
        help="Only validate row shape and numeric fields.",
    )
    parser.add_argument(
        "--skip-manifest",
        action="store_true",
        help="Do not write the ws3 export manifest.",
    )
    args = parser.parse_args()
    input_dir = None if args.skip_reference_validation else args.input_dir
    rows = export_supply_offer(
        args.ws3_output_path,
        args.output_path,
        input_dir,
        args.manifest_path,
        not args.skip_manifest,
    )
    message = f"exported {len(rows)} supply offers to {args.output_path}"
    if not args.skip_manifest:
        message += f"; wrote {args.manifest_path}"
    print(message)


if __name__ == "__main__":
    main()
