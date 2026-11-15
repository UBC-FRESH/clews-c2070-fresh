"""CLI for compiling TSA24 managed-AU live-biomass curves from BTC output."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .tsa24_biomass_curves import (
    DEFAULT_AGE_MAX,
    DEFAULT_AGE_STEP,
    DEFAULT_BIOMASS_OUTPUT,
    compile_biomass_curves_from_btc,
)

__all__ = ["main"]


def _default_femic_root() -> Path:
    configured = os.environ.get("FEMIC_TSA24_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    nemora_root = os.environ.get("NEMORA_ROOT")
    if nemora_root:
        candidate = (
            Path(nemora_root).expanduser().resolve().parent
            / "femic"
            / "external"
            / "femic-tsa24-instance"
        )
        if candidate.exists():
            return candidate
    raise SystemExit(
        "set FEMIC_TSA24_ROOT (or NEMORA_ROOT next to the FEMIC TSA24 instance)"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Compile TSA24 managed-AU live-biomass curves from the FEMIC BTC "
            "biomass-live and carbon indicator banks."
        )
    )
    parser.add_argument(
        "--femic-root",
        type=Path,
        default=None,
        help="FEMIC TSA24 instance root (default: FEMIC_TSA24_ROOT).",
    )
    parser.add_argument(
        "--biomass-output",
        default=DEFAULT_BIOMASS_OUTPUT,
        help=(
            "BTC biomass output filename under <femic-root>/data "
            f"(default: {DEFAULT_BIOMASS_OUTPUT})."
        ),
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("data/derived/national/bucking/tsa24_grade_curves"),
        help="Output directory.",
    )
    parser.add_argument("--age-max", type=int, default=DEFAULT_AGE_MAX)
    parser.add_argument("--age-step", type=int, default=DEFAULT_AGE_STEP)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    femic_root = args.femic_root or _default_femic_root()
    print(f"Compiling TSA24 biomass curves from {femic_root}", file=sys.stderr)
    manifest = compile_biomass_curves_from_btc(
        femic_root=femic_root,
        out_dir=args.out_dir,
        output_name=args.biomass_output,
        age_max=args.age_max,
        age_step=args.age_step,
    )
    print(json.dumps(manifest["coverage"], indent=2), file=sys.stderr)
    print(f"Wrote {args.out_dir}", file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
