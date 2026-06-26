"""Validate prototype tables against the FRESH soft-link contract."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from fresh_fibre.schema import read_rows, write_rows


DEFAULT_CONTRACT_PATH = Path("metadata/ffcm-archive/fresh-interface-contract.csv")
DEFAULT_INPUT_DIR = Path("metadata/ffcm-archive/prototype-inputs/bc-mini")

VALIDATION_COLUMNS = [
    "table_name",
    "direction",
    "table_path",
    "status",
    "row_count",
    "required_field_count",
    "missing_required_fields",
    "optional_field_count",
    "present_optional_fields",
    "notes",
]

OPTIONAL_ABSENT_TABLES = {"transporter", "import_offer"}


@dataclass(frozen=True)
class ContractTable:
    table_name: str
    direction: str
    required_fields: list[str]
    optional_fields: list[str]

    @classmethod
    def from_row(cls, row: dict[str, str]) -> "ContractTable":
        return cls(
            row["table_name"],
            row["direction"],
            split_fields(row["required_fields"]),
            split_fields(row["optional_fields"]),
        )


def split_fields(value: str) -> list[str]:
    return [field.strip() for field in value.split(";") if field.strip()]


def table_path(input_dir: Path, table: ContractTable) -> Path:
    if table.direction == "lp_output":
        return input_dir / "generated" / f"{table.table_name}.csv"
    return input_dir / f"{table.table_name}.csv"


def validate_contract(
    input_dir: Path = DEFAULT_INPUT_DIR,
    contract_path: Path = DEFAULT_CONTRACT_PATH,
) -> list[dict[str, object]]:
    contract_tables = [
        ContractTable.from_row(row)
        for row in read_rows(contract_path)
    ]
    results: list[dict[str, object]] = []
    for table in contract_tables:
        path = table_path(input_dir, table)
        if not path.exists():
            status = "missing_optional" if table.table_name in OPTIONAL_ABSENT_TABLES else "missing"
            results.append(
                {
                    "table_name": table.table_name,
                    "direction": table.direction,
                    "table_path": str(path),
                    "status": status,
                    "row_count": 0,
                    "required_field_count": len(table.required_fields),
                    "missing_required_fields": "; ".join(table.required_fields),
                    "optional_field_count": len(table.optional_fields),
                    "present_optional_fields": "",
                    "notes": "Table is intentionally absent from the BC mini fixture."
                    if status == "missing_optional"
                    else "Required prototype table is absent.",
                }
            )
            continue

        rows = read_rows(path)
        columns = set(rows[0]) if rows else set()
        missing_required = [field for field in table.required_fields if field not in columns]
        present_optional = [field for field in table.optional_fields if field in columns]
        if missing_required:
            status = "missing_required_fields"
            notes = "One or more required fields from fresh-interface-contract.csv are absent."
        elif not rows:
            status = "empty"
            notes = "Table exists but has no data rows."
        else:
            status = "ok"
            notes = "Required fields present."
        results.append(
            {
                "table_name": table.table_name,
                "direction": table.direction,
                "table_path": str(path),
                "status": status,
                "row_count": len(rows),
                "required_field_count": len(table.required_fields),
                "missing_required_fields": "; ".join(missing_required),
                "optional_field_count": len(table.optional_fields),
                "present_optional_fields": "; ".join(present_optional),
                "notes": notes,
            }
        )
    return results


def assert_contract_ok(results: list[dict[str, object]]) -> None:
    failures = [
        row for row in results
        if row["status"] not in {"ok", "missing_optional"}
    ]
    if failures:
        lines = [
            f"{row['table_name']}: {row['status']} ({row['missing_required_fields']})"
            for row in failures
        ]
        joined = "\n  - ".join(lines)
        raise ValueError(f"Contract validation failed:\n  - {joined}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR)
    parser.add_argument("--contract-path", type=Path, default=DEFAULT_CONTRACT_PATH)
    parser.add_argument("--output-path", type=Path)
    args = parser.parse_args()
    results = validate_contract(args.input_dir, args.contract_path)
    output_path = args.output_path or args.input_dir / "generated" / "contract_validation.csv"
    write_rows(output_path, VALIDATION_COLUMNS, results)
    assert_contract_ok(results)
    print(f"validated {len(results)} contract tables; wrote {output_path}")


if __name__ == "__main__":
    main()
