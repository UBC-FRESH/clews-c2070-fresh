"""Build the BC FVS-CA consumer dataset from the harmonized v0 extract.

Run from the parent repository root::

    python -m workflows.national.fvsca.build \
        --variant bc --source faib_psp \
        --v0-root data/derived/fvsca_calibration/v0 \
        --raw-root data/raw \
        --out data/derived/fvsca_calibration/bc_faib_fvsbc_schema

The builder reads the v0 plot source, applies the variant profile's domain
filter, passes the selected source to the BC consumer adapter, and writes the
three consumer tables, the species crosswalk, and the manifest.  It does not
run FVS and does not fit a model.

A source or adapter contract error exits non-zero with the error on stderr.  An
unexpected exception is not caught: it is a bug, not a data condition.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .bc import (
    RAW_HEADER_PATHS,
    BcAdapterError,
    build_bc_dataset,
    load_tsa_desc,
)
from .plot_sources import HarmonizedV0Source, PlotSourceError, normalise_sources
from .profiles import PROFILES

REPO_ROOT = Path(__file__).resolve().parents[3]

DEFAULT_V0_ROOT = "data/derived/fvsca_calibration/v0"
DEFAULT_RAW_ROOT = "data/raw"
DEFAULT_OUT = "data/derived/fvsca_calibration/bc_faib_fvsbc_schema"


def _resolve(path: str | Path) -> Path:
    """Resolve a path against the repository root when it is relative."""
    candidate = Path(path)
    return candidate if candidate.is_absolute() else (REPO_ROOT / candidate)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build one FVS-CA consumer dataset from the harmonized v0 plot "
            "extract. Only the BC profile has an adapter in this prototype."
        )
    )
    parser.add_argument(
        "--variant",
        required=True,
        choices=sorted(PROFILES),
        help="variant profile to build (only 'bc' is implemented)",
    )
    parser.add_argument(
        "--source",
        default="faib_psp",
        help=(
            "comma-separated v0 source labels, for example "
            "'faib_psp' or 'faib_psp,faib_non_psp'"
        ),
    )
    parser.add_argument(
        "--v0-root",
        default=DEFAULT_V0_ROOT,
        help=f"harmonized v0 extract (default: {DEFAULT_V0_ROOT})",
    )
    parser.add_argument(
        "--raw-root",
        default=DEFAULT_RAW_ROOT,
        help=(
            "raw data root, used only to enrich TSA_DESC and record the raw "
            f"header hashes (default: {DEFAULT_RAW_ROOT})"
        ),
    )
    parser.add_argument(
        "--out",
        default=DEFAULT_OUT,
        help=f"output dataset directory (default: {DEFAULT_OUT})",
    )
    parser.add_argument(
        "--include-unsupported",
        action="store_true",
        help="keep sites outside the twelve BEC zones the BC variant resolves",
    )
    parser.add_argument(
        "--min-dbh",
        type=float,
        default=0.0,
        metavar="CM",
        help="lower DBH limit in centimetres (default: 0)",
    )
    parser.add_argument(
        "--strict-species",
        action="store_true",
        help="fail when a calibration-row species code has no BC JSP mapping",
    )
    return parser


def _summary(dataset: Any, sources: tuple[str, ...]) -> dict[str, Any]:
    """Return the compact build summary printed to stdout."""
    manifest = dataset.manifest
    stats = manifest["stats"]
    return {
        "variant": manifest["variant"],
        "fvs_variant": manifest["fvs_variant"],
        "source_labels": list(sources),
        "output": str(dataset.directory),
        "rows": manifest["rows"],
        "species": {
            "crosswalk_rows": stats["species_crosswalk_rows"],
            "direct": stats["species_direct"],
            "donor": stats["species_donor"],
            "unmapped": stats["species_unmapped"],
            "unmapped_calibration_rows": stats["species_unmapped_calibration_rows"],
        },
        "site_counts": {
            "input": stats["sites_input"],
            "selected": stats["sites_selected"],
            "dropped_unsupported": stats["sites_dropped_unsupported"],
            "missing_bec": stats["sites_missing_bec"],
        },
        "intervals": {
            "count": stats["interval_count"],
            "years_min": stats["interval_years_min"],
            "years_max": stats["interval_years_max"],
        },
        "warnings": manifest["warnings"],
        "outputs": manifest["outputs"],
    }


def main(argv: list[str] | None = None) -> int:
    """Run the builder and return a process exit code."""
    args = _build_parser().parse_args(argv)
    profile = PROFILES[args.variant]
    try:
        sources = normalise_sources(args.source)
    except PlotSourceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    raw_root = _resolve(args.raw_root)
    tsa_lookup = None
    raw_inputs: dict[str, Path] = {}
    faib_sources = [source for source in sources if source in RAW_HEADER_PATHS]
    if faib_sources:
        tsa_lookup, used = load_tsa_desc(raw_root, faib_sources)
        raw_inputs = {relative: raw_root / relative for relative in used}

    try:
        source = HarmonizedV0Source(_resolve(args.v0_root), sources=sources)
        dataset = build_bc_dataset(
            source,
            _resolve(args.out),
            profile=profile,
            tsa_lookup=tsa_lookup,
            include_unsupported=args.include_unsupported,
            min_dbh=args.min_dbh,
            strict_species=args.strict_species,
            raw_inputs=raw_inputs,
        )
    except (BcAdapterError, PlotSourceError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(_summary(dataset, sources), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
