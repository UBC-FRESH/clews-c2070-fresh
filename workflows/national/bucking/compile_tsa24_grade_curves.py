"""CLI for compiling TSA24 managed-AU grade and residue curves."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .tsa24_grade_curves import (
    DEFAULT_AGE_MAX,
    DEFAULT_AGE_STEP,
    compile_grade_curves,
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


def _default_tipsy_database() -> Path:
    configured = os.environ.get("TIPSY_DATABASE")
    if configured:
        return Path(configured).expanduser().resolve()
    install = os.environ.get("TIPSY_INSTALL")
    if install:
        candidate = Path(install).expanduser().resolve() / "Database"
        if candidate.exists():
            return candidate
    raise SystemExit("set TIPSY_DATABASE (directory containing .tab/.taf pairs)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compile TSA24 managed-AU grade and residue curves from TIPSY .taf tables."
    )
    parser.add_argument(
        "--femic-root",
        type=Path,
        default=None,
        help="FEMIC TSA24 instance root (default: FEMIC_TSA24_ROOT).",
    )
    parser.add_argument(
        "--tipsy-database",
        type=Path,
        default=None,
        help="Directory containing TIPSY .tab/.taf pairs (default: TIPSY_DATABASE).",
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
    tipsy_database = args.tipsy_database or _default_tipsy_database()
    print(
        f"Compiling TSA24 grade curves from {femic_root} and {tipsy_database}",
        file=sys.stderr,
    )
    manifest = compile_grade_curves(
        femic_root=femic_root,
        tipsy_database=tipsy_database,
        out_dir=args.out_dir,
        age_max=args.age_max,
        age_step=args.age_step,
    )
    print(json.dumps(manifest["coverage"], indent=2), file=sys.stderr)
    print(f"Wrote {args.out_dir}", file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
