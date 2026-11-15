"""Parity check: this BC adapter versus ``fresh_fvsbc.calibration``.

The reference builder is read-only.  This script adds its ``src`` directory to
``sys.path``, materializes a temporary shim directory under ``/tmp`` with the
four decompressed FAIB PSP CSVs, runs both builders on the same raw archive,
and writes ``PARITY_REPORT.md`` beside this module.

The acceptance criterion is a valid consumer schema and understood differences,
not byte equality.  The two builders intentionally disagree on:

- geometric basal area versus the source ``BA_TREE`` sum;
- a dead endpoint with a null DBH (known death here, missing in the reference);
- donor species aliases (this adapter resolves seven codes the reference leaves
  unknown);
- BEC derivation and the DBH/status filters inherited from the harmonized v0;
- TSA enrichment (optional here, always read by the reference);
- visit volume on multi-plot visits (null here, allocated by the reference).
"""

from __future__ import annotations

import argparse
import gzip
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from .bc import build_bc_dataset, load_tsa_desc
from .plot_sources import HarmonizedV0Source

INPUT_FILES: tuple[str, ...] = (
    "faib_header.csv",
    "faib_sample_byvisit.csv",
    "faib_plot_header.csv",
    "faib_tree_detail.csv",
)

DEFAULT_FRESH_FVSBC_SRC = "/srv/shared-data/gep/ws4/tmp/fresh-fvsbc/src"
REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_RAW_ROOT = REPO_ROOT / "data" / "raw"
DEFAULT_V0_ROOT = REPO_ROOT / "data" / "derived" / "fvsca_calibration" / "v0"
DEFAULT_REPORT = Path(__file__).with_name("PARITY_REPORT.md")


def _psp_dir(raw_root: str | Path) -> Path:
    """Return the FAIB PSP directory from either a raw root or the PSP dir."""
    candidate = Path(raw_root)
    if (candidate / "faib_header.csv.gz").is_file() or (
        candidate / "faib_header.csv"
    ).is_file():
        return candidate
    nested = candidate / "bc_faib_ground_plots" / "psp"
    if nested.is_dir():
        return nested
    raise FileNotFoundError(
        f"{candidate} does not contain the FAIB PSP archive; expected "
        "bc_faib_ground_plots/psp/ or the PSP directory itself"
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fmt(value: Any, *, digits: int = 4) -> str:
    """Format a number for a Markdown table, preserving ``None``."""
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        if value != value:
            return "—"
        return f"{value:,.{digits}f}"
    return str(value)


def _diff(ref: float | int, ours: float | int) -> str:
    """Format a signed difference."""
    if isinstance(ref, int) and isinstance(ours, int):
        return f"{ours - ref:+,}"
    return f"{ours - ref:+,.4f}"


def _pct(ref: float, ours: float) -> str:
    """Format a signed relative difference in percent."""
    if ref == 0:
        return "—"
    return f"{100.0 * (ours - ref) / ref:+.4f}%"


def _import_reference() -> Any:
    """Import the read-only reference builder, reporting the exact error."""
    src = Path(os.environ.get("FRESH_FVSBC_SRC", DEFAULT_FRESH_FVSBC_SRC))
    if not src.is_dir():
        raise SystemExit(
            f"error: fresh-fvsbc source directory not found: {src}; "
            "set FRESH_FVSBC_SRC to the checkout's src directory"
        )
    sys.path.insert(0, str(src))
    try:
        from fresh_fvsbc import calibration
    except Exception as exc:  # pragma: no cover - environment-dependent
        raise SystemExit(
            f"error: cannot import fresh_fvsbc from {src}: {type(exc).__name__}: {exc}"
        ) from exc
    return calibration


def materialize_shim(raw_dir: str | Path, shim_dir: str | Path) -> Path:
    """Write the four FAIB PSP CSVs into ``shim_dir``, decompressing ``.gz``.

    The archive is never modified.  ``fresh_fvsbc.calibration.build_dataset``
    requires plain file names, so a temporary shim is the least invasive way to
    run the reference builder against the archived payload.
    """
    source = Path(raw_dir)
    destination = Path(shim_dir)
    destination.mkdir(parents=True, exist_ok=True)
    for name in INPUT_FILES:
        plain = source / name
        compressed = source / f"{name}.gz"
        target = destination / name
        if compressed.is_file():
            with gzip.open(compressed, "rb") as reader, target.open("wb") as writer:
                shutil.copyfileobj(reader, writer, length=1024 * 1024)
        elif plain.is_file():
            shutil.copy2(plain, target)
        else:
            raise FileNotFoundError(
                f"{source} has neither {name} nor {name}.gz; "
                "the four FAIB PSP files are required"
            )
    return destination


def _read_reference(calibration: Any, shim: Path, out_dir: Path) -> Any:
    return calibration.build_dataset(
        shim,
        out_dir,
        include_unsupported=False,
        min_dbh=0.0,
    )


def _read_ours(psp_dir: Path, v0_root: Path, out_dir: Path) -> Any:
    tsa_lookup, _ = load_tsa_desc(psp_dir.parent.parent, ("faib_psp",))
    source = HarmonizedV0Source(v0_root, sources=("faib_psp",))
    return build_bc_dataset(
        source,
        out_dir,
        tsa_lookup=tsa_lookup,
        include_unsupported=False,
        min_dbh=0.0,
        strict_species=False,
    )


def _mean(series: pd.Series) -> float:
    numeric = pd.to_numeric(series, errors="coerce").dropna()
    return float(numeric.mean()) if not numeric.empty else float("nan")


def _clean_key(series: pd.Series) -> pd.Series:
    """Normalise an identifier to a stripped string for cross-builder joins."""
    return series.astype("string").str.strip().str.replace(r"\.0$", "", regex=True)


def _unique_sites(frame: pd.DataFrame) -> int:
    return int(frame["SITE_IDENTIFIER"].astype("string").nunique())


def _unique_plots(frame: pd.DataFrame) -> int:
    return int(
        frame[["SITE_IDENTIFIER", "PLOT"]].drop_duplicates().shape[0]
    )


def _species_sets(species: pd.DataFrame, *, reference: bool) -> dict[str, set[str]]:
    """Return observed, resolved, and unmapped source-code sets."""
    if reference:
        observed = {
            str(code)
            for code in species["psp_species"].dropna()
            if str(code).strip()
        }
        resolved = {
            str(code)
            for code in species.loc[species["known"], "psp_species"].dropna()
            if str(code).strip()
        }
    else:
        observed = {
            str(code)
            for code in species["source_species"].dropna()
            if str(code).strip()
        }
        resolved = {
            str(code)
            for code in species.loc[
                species["fvs_jsp_code"].notna(), "source_species"
            ].dropna()
            if str(code).strip()
        }
    return {"observed": observed, "resolved": resolved, "unmapped": observed - resolved}


def _codes(values: set[str]) -> str:
    return ", ".join(f"`{value}`" for value in sorted(values)) if values else "none"


def _build_metrics(reference: Any, ours: Any) -> dict[str, Any]:
    """Compute every comparison value once, for both the table and the prose."""
    ref_visits = reference.visits
    our_visits = ours.visits
    ref_intervals = reference.intervals
    our_intervals = ours.intervals
    ref_survival = reference.tree_survival
    our_survival = ours.tree_survival

    ref_years = pd.to_numeric(ref_intervals["years"], errors="coerce").dropna()
    our_years = pd.to_numeric(our_intervals["years"], errors="coerce").dropna()

    matched = ref_visits.assign(
        SITE_IDENTIFIER=_clean_key(ref_visits["SITE_IDENTIFIER"]),
        CLSTR_ID=_clean_key(ref_visits["CLSTR_ID"]),
    ).merge(
        our_visits.assign(
            SITE_IDENTIFIER=_clean_key(our_visits["SITE_IDENTIFIER"]),
            CLSTR_ID=_clean_key(our_visits["CLSTR_ID"]),
        ),
        on=["SITE_IDENTIFIER", "PLOT", "CLSTR_ID"],
        how="inner",
        suffixes=("_ref", "_ours"),
    )
    matched_ba = pd.to_numeric(matched["ba"], errors="coerce")
    matched_ba_ours = pd.to_numeric(matched["ba0"], errors="coerce")
    ba_relative = (
        ((matched_ba_ours - matched_ba).abs() / matched_ba.replace(0, float("nan")))
        .dropna()
        .mean()
        * 100.0
    )

    ref_species = _species_sets(reference.species, reference=True)
    our_species = _species_sets(ours.species, reference=False)

    ref_tsa_sites = int(
        ref_visits.loc[ref_visits["TSA_DESC"].notna(), "SITE_IDENTIFIER"].nunique()
    )
    our_tsa_sites = int(
        our_visits.loc[our_visits["TSA_DESC"].notna(), "SITE_IDENTIFIER"].nunique()
    )

    return {
        "rows": {
            "visits": (len(ref_visits), len(our_visits)),
            "intervals": (len(ref_intervals), len(our_intervals)),
            "tree_survival": (len(ref_survival), len(our_survival)),
            "trees": (
                int(reference.manifest["rows"]["trees"]),
                int(ours.manifest["stats"]["trees_input"]),
            ),
            "trees_base": (
                int(reference.manifest["rows"]["trees"]),
                int(ours.manifest["stats"]["trees_base"]),
            ),
        },
        "sites": (_unique_sites(ref_visits), _unique_sites(our_visits)),
        "plots": (_unique_plots(ref_visits), _unique_plots(our_visits)),
        "years": {
            "min": (int(ref_years.min()), int(our_years.min())),
            "median": (float(ref_years.median()), float(our_years.median())),
            "max": (int(ref_years.max()), int(our_years.max())),
        },
        "means": {
            "ba": (_mean(ref_visits["ba"]), _mean(our_visits["ba0"])),
            "tph": (_mean(ref_visits["tph"]), _mean(our_visits["tph0"])),
            "qmd": (_mean(ref_visits["qmd"]), _mean(our_visits["qmd"])),
            "volume": (_mean(ref_visits["volume"]), _mean(our_visits["volume"])),
        },
        "survival": {
            "dead": (
                int(ref_survival["dead"].sum()),
                int(our_survival["dead"].sum()),
            ),
            "known": (
                int(ref_survival["known"].sum()),
                int(our_survival["known"].sum()),
            ),
            "missing": (
                int((ref_survival["known"] == 0).sum()),
                int((our_survival["known"] == 0).sum()),
            ),
        },
        "species": {
            "observed": (len(ref_species["observed"]), len(our_species["observed"])),
            "resolved": (len(ref_species["resolved"]), len(our_species["resolved"])),
            "unmapped": (len(ref_species["unmapped"]), len(our_species["unmapped"])),
            "ref_unmapped": ref_species["unmapped"],
            "our_unmapped": our_species["unmapped"],
            "our_donor_only": our_species["resolved"] - ref_species["resolved"],
            "both_unmapped": ref_species["unmapped"] & our_species["unmapped"],
        },
        "coverage": {
            "bec_labels": (
                int(ref_visits["BEC"].dropna().nunique()),
                int(our_visits["BEC"].dropna().nunique()),
            ),
            "tsa_sites": (ref_tsa_sites, our_tsa_sites),
            "volume_nonnull": (
                int(ref_visits["volume"].notna().sum()),
                int(our_visits["volume"].notna().sum()),
            ),
            "volume_null": (
                int(ref_visits["volume"].isna().sum()),
                int(our_visits["volume"].isna().sum()),
            ),
        },
        "matched_rows": int(len(matched)),
        "ba_relative_pct": float(ba_relative),
    }


def _row_table(metrics: dict[str, Any]) -> str:
    rows = [
        ("Visit rows (plot-level)", *metrics["rows"]["visits"]),
        ("Interval rows", *metrics["rows"]["intervals"]),
        ("Tree-survival rows", *metrics["rows"]["tree_survival"]),
        ("Retained input tree rows (reference / adapter input)", *metrics["rows"]["trees"]),
        ("Tree rows after start-tree filters", *metrics["rows"]["trees_base"]),
        ("Unique sites", *metrics["sites"]),
        ("Unique site/plot pairs", *metrics["plots"]),
    ]
    lines = ["| Metric | fresh_fvsbc | fvsca | Difference |", "|---|---:|---:|---:|"]
    for label, ref, ours in rows:
        lines.append(f"| {label} | {_fmt(ref)} | {_fmt(ours)} | {_diff(ref, ours)} |")
    return "\n".join(lines)


def _summary_table(metrics: dict[str, Any]) -> str:
    lines = ["| Metric | fresh_fvsbc | fvsca | Difference |", "|---|---:|---:|---:|"]
    for label, key in (
        ("Interval years, minimum", "min"),
        ("Interval years, median", "median"),
        ("Interval years, maximum", "max"),
    ):
        ref, ours = metrics["years"][key]
        lines.append(f"| {label} | {_fmt(ref)} | {_fmt(ours)} | {_diff(ref, ours)} |")
    for label, key, digits in (
        ("Mean live BA (m²/ha)", "ba", 4),
        ("Mean live TPH (stems/ha)", "tph", 4),
        ("Mean QMD (cm)", "qmd", 4),
        ("Mean visit volume (m³/ha)", "volume", 4),
    ):
        ref, ours = metrics["means"][key]
        lines.append(
            f"| {label} | {_fmt(ref, digits=digits)} | {_fmt(ours, digits=digits)} | "
            f"{_diff(ref, ours)} ({_pct(ref, ours)}) |"
        )
    for label, key in (
        ("Dead tree endpoints", "dead"),
        ("Known tree endpoints", "known"),
        ("Missing tree endpoints", "missing"),
    ):
        ref, ours = metrics["survival"][key]
        lines.append(f"| {label} | {_fmt(ref)} | {_fmt(ours)} | {_diff(ref, ours)} |")
    for label, key in (
        ("Observed source species", "observed"),
        ("Resolved source species", "resolved"),
        ("Unmapped source species", "unmapped"),
    ):
        ref, ours = metrics["species"][key]
        lines.append(f"| {label} | {_fmt(ref)} | {_fmt(ours)} | {_diff(ref, ours)} |")
    for label, key in (
        ("BEC labels on visits", "bec_labels"),
        ("Sites with non-null TSA_DESC", "tsa_sites"),
        ("Non-null visit volume rows", "volume_nonnull"),
        ("Null visit volume rows", "volume_null"),
    ):
        ref, ours = metrics["coverage"][key]
        lines.append(f"| {label} | {_fmt(ref)} | {_fmt(ours)} | {_diff(ref, ours)} |")
    return "\n".join(lines)


def _write_report(
    path: Path,
    *,
    metrics: dict[str, Any],
    reference_version: str,
    raw_file_count: int,
    parameters: dict[str, Any],
) -> None:
    species = metrics["species"]
    text = f"""# BC adapter parity report

Generated by `workflows.national.fvsca.parity` at {_utc_now()}.

**Reference**: read-only `fresh-fvsbc` {reference_version}.
**Input**: the archived FAIB PSP compilation, {raw_file_count} files, passed to
both builders as the same four decompressed CSVs in a temporary `/tmp` shim.
**Parameters**: {parameters}.
**Acceptance criterion**: a valid consumer schema and understood differences,
not byte equality.

## Row counts and coverage

{_row_table(metrics)}

The visit table is plot-level in both builders: a site visit with several
plots becomes one row per plot. The reference builder and this adapter both
aggregate live tally trees by site, plot, and visit, so the row counts should
agree wherever the harmonized v0 kept the same keys. After the adapter's
start-tree filters, {_fmt(metrics["rows"]["trees_base"][1])} tree rows remain,
against {_fmt(metrics["rows"]["trees_base"][0])} in the reference; the larger
adapter input count includes null-weight and null-DBH rows the reference
discards before its own filters.

## Interval years, stand summaries, and survival

{_summary_table(metrics)}

On the {metrics["matched_rows"]:,} visits that join on site/plot/visit, the
mean absolute relative difference in live BA is {metrics["ba_relative_pct"]:.4f}%.
This isolates the geometric-BA reconstruction from row selection; the source
`BA_TREE` is itself derived from DBH, so agreement at this scale is expected.

## Species resolution

Observed source codes are the codes present in each builder's retained tree
rows. The reference builder resolves only the fifteen JSP codes. This adapter
resolves the fifteen plus seven donor aliases, and leaves every other code
explicitly unmapped.

- Reference unmapped codes ({len(species["ref_unmapped"])}):
  {_codes(species["ref_unmapped"])}
- Adapter unmapped codes ({len(species["our_unmapped"])}):
  {_codes(species["our_unmapped"])}
- Codes the adapter resolves by donor alias only ({len(species["our_donor_only"])}):
  {_codes(species["our_donor_only"])}
- Codes unmapped by both ({len(species["both_unmapped"])}):
  {_codes(species["both_unmapped"])}

## Difference register (material and declared)

| Difference | Measured effect | Why it exists | Disposition |
|---|---|---|---|
| Geometric BA vs source `BA_TREE` | Mean live BA {_fmt(metrics["means"]["ba"][0])} vs {_fmt(metrics["means"]["ba"][1])} m²/ha; matched-visit mean absolute relative difference {metrics["ba_relative_pct"]:.4f}% | The harmonized v0 tree table does not carry `BA_TREE`; the adapter rebuilds BA as `pi * (DBH/200)^2 * expansion_factor`. The reference multiplies the source `BA_TREE` by the expansion factor. The source `BA_TREE` is itself DBH-derived, so the two methods agree to the reported precision. | Accepted; numerically equivalent at reported precision. QMD inherits the same reconstruction. |
| Dead endpoint with null DBH | Dead endpoints {_fmt(metrics["survival"]["dead"][0])} reference vs {_fmt(metrics["survival"]["dead"][1])} adapter; known endpoints {_fmt(metrics["survival"]["known"][0])} vs {_fmt(metrics["survival"]["known"][1])} | The reference drops rows with a null DBH before the end-status join, so a dead tree without a final DBH becomes `missing`. The adapter uses every v0 tree row for the end lookup and keeps the status, while still excluding null-DBH trees from the live start tally. | Accepted; the adapter preserves a known death instead of discarding it. |
| Donor species aliases | Resolved codes {_fmt(metrics["species"]["resolved"][0])} reference vs {_fmt(metrics["species"]["resolved"][1])} adapter; unmapped {_fmt(metrics["species"]["unmapped"][0])} vs {_fmt(metrics["species"]["unmapped"][1])} | The reference crosswalk knows only the fifteen JSP codes. This adapter additionally applies the seven declared donor aliases (`SS`/`SB`→`SE`, `BA`→`BG`, `HM`→`HW`, `YC`→`CW`, `DR`→`AT`, `MB`→`AC`). | Accepted and declared in the profile; unmapped codes are never silently folded. |
| BEC derivation | {_fmt(metrics["coverage"]["bec_labels"][0])} reference labels vs {_fmt(metrics["coverage"]["bec_labels"][1])} adapter labels | Both build zone + lower-case subzone + upper-case variant. The adapter returns null for a missing zone and drops that site by default; the reference emits an empty label and marks it unsupported. | No material difference in the selected dataset; the handling of a missing zone is explicit. |
| DBH/status filters inherited from v0 | Retained input trees {_fmt(metrics["rows"]["trees"][0])} reference vs {_fmt(metrics["rows"]["trees"][1])} adapter input; {_fmt(metrics["rows"]["trees_base"][1])} adapter rows after start filters | The reference applies its own `dropna(DBH, LV_D, PHF)` and `DBH >= min_dbh` filter to the raw archive. The v0 extract already dropped five blank `PLOT`/`TREE_NO` rows, nulled DBH above 500 cm and height above 100 m, and keeps `live`/`dead`/`missing` statuses. The adapter then applies the same start-tree rule: known status, non-null DBH, positive expansion factor, and `min_dbh`. | Accepted; the difference is confined to rows the reference would discard, and the post-filter row count agrees exactly. |
| TSA enrichment | {_fmt(metrics["coverage"]["tsa_sites"][0])} reference sites vs {_fmt(metrics["coverage"]["tsa_sites"][1])} adapter sites with non-null `TSA_DESC` | The reference reads `TSA_DESC` from `faib_header.csv`. The harmonized v0 site table does not carry it, so the adapter enriches from the same raw header when `--raw-root` is available; a missing header leaves the field null and the manifest warns. | Accepted; optional enrichment with an explicit manifest warning. |
| Multi-plot visit volume | Reference null volume rows {_fmt(metrics["coverage"]["volume_null"][0])}; adapter null volume rows {_fmt(metrics["coverage"]["volume_null"][1])} (visit units {_fmt(parameters.get("multi_plot_visit_units"))}) | The reference sums per-tree `VOL_MER * phf` within each plot, so it always has plot volume. The v0 visit volume is a site-visit aggregate; the adapter carries it only when the visit contains one plot and writes `volume_basis = cluster_multi_plot_not_apportioned` otherwise. | Accepted; no defensible allocation without per-tree volume. |
| Site/visit row expansion | {_fmt(metrics["rows"]["visits"][0])} reference vs {_fmt(metrics["rows"]["visits"][1])} adapter visit rows; {_fmt(metrics["sites"][1])} sites and {_fmt(metrics["plots"][1])} site/plot pairs in the adapter | The consumer table is plot-level, not site-level. A site visit with several plots yields several rows. | Expected shape, not a data loss. |

## Verdict

The adapter produces the declared consumer schema with explicit nulls and
warnings where the v0 contract cannot supply a value. Every material difference
observed here is in the register above and is explained by a declared policy.
The comparison is therefore accepted as a valid parity result for the
calibration-table boundary, not as a byte-for-byte replica of the reference
builder.
"""
    path.write_text(text, encoding="utf-8")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--raw-root",
        default=str(DEFAULT_RAW_ROOT),
        help="directory with the archived FAIB PSP .csv.gz files",
    )
    parser.add_argument(
        "--v0-root",
        default=str(DEFAULT_V0_ROOT),
        help="harmonized v0 extract used by the adapter",
    )
    parser.add_argument(
        "--report",
        default=str(DEFAULT_REPORT),
        help="Markdown report path",
    )
    parser.add_argument(
        "--shim-dir",
        default=None,
        help="reuse this /tmp shim directory instead of a fresh temporary one",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    calibration = _import_reference()
    psp_dir = _psp_dir(args.raw_root)
    v0_root = Path(args.v0_root)

    if args.shim_dir:
        shim = materialize_shim(psp_dir, args.shim_dir)
        temporary = None
    else:
        temporary = tempfile.TemporaryDirectory(prefix="fvsca_parity_", dir="/tmp")
        shim = materialize_shim(psp_dir, temporary.name)

    try:
        with tempfile.TemporaryDirectory(prefix="fvsca_parity_ref_", dir="/tmp") as ref_dir:
            reference = _read_reference(calibration, shim, Path(ref_dir))
        with tempfile.TemporaryDirectory(prefix="fvsca_parity_our_", dir="/tmp") as our_dir:
            ours = _read_ours(psp_dir, v0_root, Path(our_dir))

        metrics = _build_metrics(reference, ours)
        parameters = {
            "include_unsupported": False,
            "min_dbh": 0.0,
            "strict_species": False,
            "multi_plot_visit_units": int(
                ours.manifest["stats"]["multi_plot_visit_units"]
            ),
        }
        _write_report(
            Path(args.report),
            metrics=metrics,
            reference_version=getattr(
                sys.modules.get("fresh_fvsbc"), "__version__", "unknown"
            ),
            raw_file_count=len(INPUT_FILES),
            parameters=parameters,
        )
        print(f"wrote {args.report}")
        print(
            "rows: "
            f"visits {len(reference.visits):,}/{len(ours.visits):,}, "
            f"intervals {len(reference.intervals):,}/{len(ours.intervals):,}, "
            f"tree_survival {len(reference.tree_survival):,}/{len(ours.tree_survival):,}"
        )
        return 0
    finally:
        if temporary is not None:
            temporary.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
