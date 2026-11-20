"""Milestone-1a runner stub.

This command validates a ``clews.scenario.v1`` file and records that a real
OSeCAN solve has not been implemented yet.  It deliberately writes no solved
dispatch, emissions, or objective values.  Milestone 1b replaces only the body
of :func:`run_scenario`, keeping the CLI and summary contract below.

CLI contract for the API server and for 1b::

    python -m workflows.ui.run_scenario \
        --scenario runtime/ui/scenarios/{scenario_id}.json \
        --out runtime/ui/runs/{run_id}/output

Exit codes: ``0`` when the runner finished its assigned job, ``2`` when the
scenario cannot be read or validated.  The terminal state is carried in
``{out}/run_summaries.json`` as ``status``; 1b should report ``completed`` (or
``failed``) there and write artifacts alongside the summary.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from workflows.ui.contracts import SCHEMA_VERSION, load_scenario
from workflows.ui.storage import atomic_write_json

__all__ = ["build_parser", "main", "run_scenario", "write_summary"]

STUB_STATUS = "not_implemented"
STUB_MESSAGE = (
    "Milestone 1a contract stub: scenario validated, OSeCAN solve not implemented."
)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def write_summary(output_dir: Path, summary: dict[str, Any]) -> Path:
    """Atomically write ``run_summaries.json`` under the run output directory."""

    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = output_dir / "run_summaries.json"
    atomic_write_json(summary_path, summary)
    return summary_path


def run_scenario(scenario_path: Path, output_dir: Path) -> int:
    """Validate the scenario and record the not-implemented terminal state."""

    try:
        scenario = load_scenario(scenario_path)
    except (OSError, ValueError) as exc:
        write_summary(
            output_dir,
            {
                "schema_version": SCHEMA_VERSION,
                "status": "failed",
                "scenario_id": None,
                "scenario_path": str(scenario_path),
                "generated_utc": _utc_now(),
                "message": f"scenario validation failed: {exc}",
            },
        )
        print(f"scenario validation failed: {exc}", file=sys.stderr)
        return 2

    summary = {
        "schema_version": SCHEMA_VERSION,
        "status": STUB_STATUS,
        "scenario_id": scenario.scenario_id,
        "scenario_label": scenario.label,
        "scenario_path": str(scenario_path),
        "generated_utc": _utc_now(),
        "message": STUB_MESSAGE,
        "artifacts": [],
    }
    summary_path = write_summary(output_dir, summary)
    print(f"{STUB_STATUS}: {scenario.scenario_id} -> {summary_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m workflows.ui.run_scenario",
        description="Validate a CLEWS/C2070 scenario and write a run summary.",
    )
    parser.add_argument(
        "--scenario",
        type=Path,
        required=True,
        help="path to a clews.scenario.v1 JSON file",
    )
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="writable run output directory",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run_scenario(args.scenario, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
