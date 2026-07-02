"""Run a tiny local ws3 smoke model and emit normalized harvest output."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

from fresh_fibre.schema import read_rows, require_columns, write_rows
from fresh_fibre.ws3_export import WS3_HARVEST_FIELDNAMES, export_supply_offer


DEFAULT_INPUT_DIR = Path("metadata/ffcm-archive/prototype-inputs/bc-mini")
DEFAULT_WS3_PATH = Path(os.environ.get("FRESH_WS3_PATH", "../ws3"))
DEFAULT_MODEL_PATH = DEFAULT_WS3_PATH / "examples/data/woodstock_model_files_tsa24_clipped"
DEFAULT_OUTPUT_PATH = DEFAULT_INPUT_DIR / "generated" / "ws3_smoke_harvest_output.csv"
DEFAULT_SUPPLY_OFFER_PATH = DEFAULT_INPUT_DIR / "generated" / "ws3_smoke_supply_offer.csv"
DEFAULT_MANIFEST_PATH = DEFAULT_INPUT_DIR / "generated" / "ws3_smoke_export_manifest.csv"
DEFAULT_CROSSWALK_PATH = DEFAULT_INPUT_DIR / "ws3_supply_crosswalk.csv"

CROSSWALK_COLUMNS = {
    "source_model",
    "dtype_key_pattern",
    "spatial_unit_id",
    "supply_node_id",
    "commodity_id",
    "species_group",
    "assortment_class",
    "supply_cost",
    "supply_cost_unit",
    "roadside_cost",
    "delivered_cost_proxy",
    "mapping_status",
}

SOURCE_MODEL = "ws3.examples.tsa24_clipped"


def add_ws3_to_path(ws3_path: Path) -> None:
    try:
        import ws3  # noqa: F401  # type: ignore[import-not-found]
        return
    except ImportError:
        pass

    resolved = ws3_path.resolve()
    if not (resolved / "ws3").is_dir():
        raise FileNotFoundError(
            "ws3 is not installed and the fallback package directory was not found: "
            f"{resolved / 'ws3'}. Install with `python3 -m pip install -e ../ws3` "
            "or pass --ws3-path."
        )
    if str(resolved) not in sys.path:
        sys.path.insert(0, str(resolved))


def load_smoke_model(ws3_path: Path, model_path: Path, horizon: int) -> Any:
    add_ws3_to_path(ws3_path)
    import ws3.forest  # type: ignore[import-not-found]

    if not model_path.exists():
        raise FileNotFoundError(f"ws3 Woodstock smoke model path not found: {model_path}")

    fm = ws3.forest.ForestModel(
        model_name="tsa24_clipped",
        model_path=str(model_path),
        base_year=2020,
        horizon=horizon,
        period_length=10,
        max_age=300,
    )
    fm.import_landscape_section()
    fm.import_areas_section(convert_periods_to_years=10)
    fm.import_yields_section(convert_periods_to_years=10)
    fm.import_actions_section(convert_periods_to_years=10)
    fm.import_transitions_section(convert_periods_to_years=10)
    fm.initialize_areas()
    fm.add_null_action()
    fm.reset_actions()
    fm.actions["harvest"].is_harvest = True
    return fm


def schedule_smoke_harvest(fm: Any, period: int, target_area: float) -> list[tuple[Any, int, float, str, int, str]]:
    missing_area = fm.areaselector.operate(period, "harvest", target_area, verbose=False)
    if missing_area:
        raise ValueError(f"ws3 smoke run could not schedule {missing_area} ha of target harvest area")
    return fm.compile_schedule()


def format_number(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".")


def dtype_key_label(dtype_key: tuple[str, ...]) -> str:
    return "|".join(dtype_key)


def read_crosswalk(path: Path, input_dir: Path) -> list[dict[str, str]]:
    rows = read_rows(path)
    require_columns(path, rows, CROSSWALK_COLUMNS)
    validate_crosswalk(rows, input_dir)
    return rows


def validate_crosswalk(rows: list[dict[str, str]], input_dir: Path) -> None:
    spatial_unit_ids = {row["spatial_unit_id"] for row in read_rows(input_dir / "spatial_unit.csv")}
    supply_node_ids = {
        row["node_id"]
        for row in read_rows(input_dir / "network_node.csv")
        if row["node_type"] == "supply"
    }
    commodity_ids = {row["commodity_id"] for row in read_rows(input_dir / "commodity.csv")}

    errors: list[str] = []
    for row in rows:
        label = f"{row['source_model']}:{row['dtype_key_pattern']}"
        if row["spatial_unit_id"] not in spatial_unit_ids:
            errors.append(f"{label} references unknown spatial unit {row['spatial_unit_id']}")
        if row["supply_node_id"] not in supply_node_ids:
            errors.append(f"{label} references unknown supply node {row['supply_node_id']}")
        if row["commodity_id"] not in commodity_ids:
            errors.append(f"{label} references unknown commodity {row['commodity_id']}")
    if errors:
        joined = "\n  - ".join(errors)
        raise ValueError(f"ws3 supply crosswalk validation failed:\n  - {joined}")


def match_crosswalk_row(
    crosswalk_rows: list[dict[str, str]],
    source_model: str,
    dtype_key: tuple[str, ...],
) -> dict[str, str]:
    label = dtype_key_label(dtype_key)
    fallback: dict[str, str] | None = None
    for row in crosswalk_rows:
        if row["source_model"] != source_model:
            continue
        if row["dtype_key_pattern"] == label:
            return row
        if row["dtype_key_pattern"] == "*":
            fallback = row
    if fallback:
        return fallback
    raise ValueError(f"No ws3 supply crosswalk row for {source_model}:{label}")


def schedule_to_harvest_rows(
    fm: Any,
    schedule: list[tuple[Any, int, float, str, int, str]],
    ws3_run_id: str,
    crosswalk_rows: list[dict[str, str]],
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for index, (dtype_key, age, area, action_code, period, estate_type) in enumerate(schedule, start=1):
        if action_code != "harvest":
            continue
        crosswalk = match_crosswalk_row(crosswalk_rows, SOURCE_MODEL, dtype_key)
        quantity = fm.compile_product(
            period,
            "totvol",
            acode=action_code,
            dtype_keys=[dtype_key],
            age=age,
        )
        rows.append(
            {
                "ws3_run_id": ws3_run_id,
                "scenario_id": "bc_mini_001",
                "period": "0",
                "spatial_unit_id": crosswalk["spatial_unit_id"],
                "supply_node_id": crosswalk["supply_node_id"],
                "commodity_id": crosswalk["commodity_id"],
                "quantity": format_number(quantity),
                "quantity_unit": "m3",
                "supply_cost": crosswalk["supply_cost"],
                "supply_cost_unit": crosswalk["supply_cost_unit"],
                "species_group": crosswalk["species_group"],
                "assortment_class": crosswalk["assortment_class"],
                "grade": "ws3_smoke",
                "moisture_basis": "green",
                "carbon_factor": "",
                "harvest_action_id": f"ws3_smoke_harvest_{index:03d}",
                "roadside_cost": crosswalk["roadside_cost"],
                "delivered_cost_proxy": crosswalk["delivered_cost_proxy"],
                "source_model": SOURCE_MODEL,
                "source_record_id": f"{ws3_run_id}:{period}:{index:03d}",
                "notes": (
                    f"Real ws3 smoke-run schedule row; dtk={dtype_key_label(dtype_key)}; "
                    f"age={age}; area_ha={format_number(area)}; estate_type={estate_type}; "
                    f"mapping_status={crosswalk['mapping_status']}."
                ),
            }
        )
    return rows


def run_ws3_smoke(
    ws3_path: Path = DEFAULT_WS3_PATH,
    model_path: Path = DEFAULT_MODEL_PATH,
    input_dir: Path = DEFAULT_INPUT_DIR,
    output_path: Path = DEFAULT_OUTPUT_PATH,
    supply_offer_path: Path = DEFAULT_SUPPLY_OFFER_PATH,
    manifest_path: Path = DEFAULT_MANIFEST_PATH,
    crosswalk_path: Path = DEFAULT_CROSSWALK_PATH,
    period: int = 1,
    target_area: float = 10.0,
    horizon: int = 3,
) -> dict[str, object]:
    fm = load_smoke_model(ws3_path, model_path, horizon)
    crosswalk_rows = read_crosswalk(crosswalk_path, input_dir)
    schedule = schedule_smoke_harvest(fm, period, target_area)
    ws3_run_id = f"ws3_smoke_tsa24_p{period}_area{format_number(target_area)}"
    rows = schedule_to_harvest_rows(fm, schedule, ws3_run_id, crosswalk_rows)
    write_rows(output_path, WS3_HARVEST_FIELDNAMES, rows)
    supply_rows = export_supply_offer(output_path, supply_offer_path, input_dir, manifest_path)
    return {
        "ws3_run_id": ws3_run_id,
        "schedule_rows": len(schedule),
        "harvest_output_rows": len(rows),
        "supply_offer_rows": len(supply_rows),
        "total_quantity_m3": sum(float(row["quantity"]) for row in rows),
        "output_path": str(output_path),
        "supply_offer_path": str(supply_offer_path),
        "manifest_path": str(manifest_path),
        "crosswalk_path": str(crosswalk_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ws3-path", type=Path, default=DEFAULT_WS3_PATH)
    parser.add_argument("--model-path", type=Path, default=DEFAULT_MODEL_PATH)
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR)
    parser.add_argument("--output-path", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--supply-offer-path", type=Path, default=DEFAULT_SUPPLY_OFFER_PATH)
    parser.add_argument("--manifest-path", type=Path, default=DEFAULT_MANIFEST_PATH)
    parser.add_argument("--crosswalk-path", type=Path, default=DEFAULT_CROSSWALK_PATH)
    parser.add_argument("--period", type=int, default=1)
    parser.add_argument("--target-area", type=float, default=10.0)
    parser.add_argument("--horizon", type=int, default=3)
    args = parser.parse_args()
    result = run_ws3_smoke(
        args.ws3_path,
        args.model_path,
        args.input_dir,
        args.output_path,
        args.supply_offer_path,
        args.manifest_path,
        args.crosswalk_path,
        args.period,
        args.target_area,
        args.horizon,
    )
    print(
        "ran ws3 smoke harvest "
        f"{result['ws3_run_id']}: {result['harvest_output_rows']} rows, "
        f"{float(result['total_quantity_m3']):.3f} m3; wrote {result['output_path']}"
    )


if __name__ == "__main__":
    main()
