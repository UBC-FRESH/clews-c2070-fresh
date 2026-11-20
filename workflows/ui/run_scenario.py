"""Milestone-1b runner CLI for ``clews.scenario.v1``.

The CLI contract is unchanged from milestone 1a::

    python -m workflows.ui.run_scenario \
        --scenario runtime/ui/scenarios/{scenario_id}.json \
        --out runtime/ui/runs/{run_id}/output

Exit codes: ``0`` when the assigned job finished (including a dry run), ``2``
when the scenario cannot be read or validated, and ``1`` on an operational
failure.  The terminal state is carried in ``{out}/run_summaries.json``.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from workflows.ui.contracts import SCHEMA_VERSION, load_scenario
from workflows.ui.osecan_runner import RunnerError
from workflows.ui.osecan_runner import run_scenario as run_osecan_scenario
from workflows.ui.storage import atomic_write_json

__all__ = ["build_parser", "main", "run_scenario", "write_summary"]


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def write_summary(output_dir: Path, summary: dict[str, Any]) -> Path:
    """Atomically write ``run_summaries.json`` under the run output directory."""

    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = output_dir / "run_summaries.json"
    atomic_write_json(summary_path, summary)
    return summary_path


def _failure_summary(
    *,
    scenario_path: Path,
    output_dir: Path,
    message: str,
    scenario_id: str | None = None,
    status: str = "failed",
) -> int:
    summary = {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "scenario_id": scenario_id,
        "scenario_path": str(scenario_path),
        "generated_utc": _utc_now(),
        "message": message,
        "artifacts": [],
    }
    summary_path = write_summary(output_dir, summary)
    print(f"{status}: {message} -> {summary_path}", file=sys.stderr)
    return 2 if status == "failed" and scenario_id is None else 1


def run_scenario(
    scenario_path: Path,
    output_dir: Path,
    *,
    model_root: Path | None = None,
    dry_run: bool = False,
) -> int:
    """Validate the scenario, build parameters, and optionally solve OSeCAN."""

    try:
        scenario = load_scenario(scenario_path)
    except (OSError, ValueError) as exc:
        return _failure_summary(
            scenario_path=scenario_path,
            output_dir=output_dir,
            message=f"scenario validation failed: {exc}",
        )

    try:
        summary = run_osecan_scenario(
            scenario,
            output_dir,
            model_root=model_root,
            dry_run=dry_run,
        )
    except RunnerError as exc:
        return _failure_summary(
            scenario_path=scenario_path,
            output_dir=output_dir,
            message=f"OSeCAN runner failed: {exc}",
            scenario_id=scenario.scenario_id,
        )
    except (OSError, ValueError) as exc:
        return _failure_summary(
            scenario_path=scenario_path,
            output_dir=output_dir,
            message=f"OSeCAN runner failed: {exc}",
            scenario_id=scenario.scenario_id,
        )
    except Exception as exc:  # noqa: BLE001 - operational failure must be summarized
        return _failure_summary(
            scenario_path=scenario_path,
            output_dir=output_dir,
            message=f"OSeCAN runner failed unexpectedly: {type(exc).__name__}: {exc}",
            scenario_id=scenario.scenario_id,
        )

    summary["scenario_path"] = str(scenario_path)
    summary_path = write_summary(output_dir, summary)
    print(f"completed: {scenario.scenario_id} -> {summary_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m workflows.ui.run_scenario",
        description="Validate a CLEWS/C2070 scenario, build OSeCAN parameters, and solve.",
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
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="generate and harmonize parameters without solving",
    )
    parser.add_argument(
        "--model-root",
        type=Path,
        default=None,
        help="disposable OSeCAN model copy (default: runtime/osecan_model, then /tmp/osecan_emfix)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run_scenario(
        args.scenario,
        args.out,
        model_root=args.model_root,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    raise SystemExit(main())
