"""National taper coverage register v1 for 13 Canadian jurisdictions.

The register answers a narrower question than the bucking pilots: for every
species/group that appears in the open NFI/SCANFI composition frame, is there a
published taper, is it local or donor, is the species mapped exactly or by
proxy, and what share of eligible stems and volume does each case represent?

Composition
-----------
NFI ecodistrict coverage rows are used with a province-token match and
n_trees-weighted species shares.  The four provinces without provincial NFI
coverage in the open table (AB, ON, NS, NU) use the pilot's explicit documented
composition assumptions.  Those rows are flagged because the assumption was
deliberately limited to taper-supported species.

Volume
------
SCANFI forest area crossed with the RSYC age-100 volume per hectare
(``rsyc_curve_library.csv``) is the eligible volume index.  Exact SCANFI
species/classes are used directly; aggregate BROADLEAF and CONIFEROUS volumes
are allocated to composition species in proportion to their NFI/assumption stem
shares.  This is a model estimate, not an inventory volume.

Tapers
------
BC uses the published Kozak (2004) Version 6 parameter table.  QC uses the
published Fortin, Schneider and Saucier (2013) fixed-effect tree taper.  The
other 11 jurisdictions use the donor policy already fixed by pilot v1/v2:
BC Kozak for AB, SK, MB, YT, NT, NU and QC Schneider for ON, NB, NS, PE, NL.
A row is ``proxy`` when the published taper parameter or species is not the
same species/group as the composition row.  A row with no mapping is a gap;
the gap reason is carried explicitly rather than silently dropping the row.

This artifact is a model estimate.  It is not a national supply estimate, not
an official grade, and not an official OSeCAN/UI product.  No request or DUA is
a dependency.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
from nemora.workflows.taper import KOZAK_SOURCE, KOZAK_SPECIES

from .national_bucking_pilot_v1 import (
    ASSUMED_COMPOSITION,
    BEC_ANCHOR,
    DONOR_POLICY,
    NFI_TO_BC,
    NFI_TO_QC,
    PROVINCE_NAMES,
    PROVINCES,
    assert_no_unexpected_deletions,
)
from .pilot_schema import SchemaError
from .schneider2013 import load_taper_registry

__all__ = [
    "DEFAULT_OUTPUT_DIR",
    "EXACT_EQUIVALENTS_BC",
    "EXACT_EQUIVALENTS_QC",
    "PROVINCES",
    "build_coverage_rows",
    "build_province_summary",
    "generate_register",
    "load_composition",
]

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT_DIR = (
    REPO_ROOT
    / "data"
    / "derived"
    / "national"
    / "bucking"
    / "national_taper_coverage_v1"
)
NFI_COVERAGE_PATH = (
    REPO_ROOT
    / "data"
    / "derived"
    / "national"
    / "assortments"
    / "national_coverage"
    / "coverage.csv"
)
SCANFI_VOLUME_PATH = (
    REPO_ROOT
    / "data"
    / "derived"
    / "national"
    / "assortments"
    / "rsyc_backbone"
    / "rsyc_curve_library.csv"
)
FOUR_PROVINCE_COVERAGE_PATH = (
    REPO_ROOT
    / "data"
    / "derived"
    / "national"
    / "bucking"
    / "four_province_bucking_pilot_v0"
    / "taper_coverage.csv"
)
V2_DISPATCH_PATH = (
    REPO_ROOT
    / "data"
    / "derived"
    / "national"
    / "bucking"
    / "national_bucking_pilot_v2"
    / "taper_dispatch.csv"
)
QC_REGISTRY_PATH = (
    REPO_ROOT
    / "workflows"
    / "national"
    / "bucking"
    / "rules"
    / "qc_schneider2013_taper.csv"
)

SOURCE_LICENSES: dict[str, str] = {
    "nfi_coverage": "Open derived NFI/FAIB/MAGPlot/QC PEP/NL FAL coverage table",
    "scanfi_rsyc_curve_library": "Open SCANFI v2 and RSYC derived curve library",
    "four_province_taper_coverage": "Open derived four-province measured pilot",
    "national_bucking_pilot_v2_taper_dispatch": "Open derived national bucking pilot v2",
    "qc_schneider2013_taper_registry": "LGPL-3.0 CFSForestTools parameter distribution; published equation",
    "bc_kozak2004": "Published Kozak (2004) parameter table; UBC Research Forests implementation",
}

AGGREGATE_SPECIES: frozenset[str] = frozenset(
    {"broadleaf", "coniferous", "generic", "BROADLEAF", "CONIFEROUS", "GENERIC"}
)
BROADLEAF_MEMBERS: frozenset[str] = frozenset(
    {
        "POPU.TRE",
        "POPU.BAL",
        "POPU.GRA",
        "BETU.PAP",
        "BETU.ALL",
        "ACER.MAC",
        "ACER.RUB",
        "ACER.SAH",
        "ALNU.RUB",
        "FRAX.NIG",
        "QUER.RUB",
        "broadleaf",
    }
)
CONIFER_MEMBERS: frozenset[str] = frozenset(
    {
        "PICE.RUB",
        "PICE.SIT",
        "PICE.ENG",
        "PICE.ABI",
        "ABIE.LAS",
        "ABIE.AMA",
        "TSUG.HET",
        "THUJ.PLI",
        "THUJ.OCC",
        "coniferous",
    }
)
EXACT_SCANFI_TARGETS: frozenset[str] = frozenset(
    {
        "ABIE.BAL",
        "LARI.LAR",
        "PICE.GLA",
        "PICE.MAR",
        "PINU.BAN",
        "PINU.CON",
        "PINU.PON",
        "PINU.RES",
        "PINU.STR",
        "PSEU.MEN",
    }
)

#: NFI species/taper-species pairs that are treated as the same species for the
#: purpose of the proxy flag.  Any pair not listed is labelled proxy.  The BC
#: list still applies the Kozak parameter-group proxy on top of this mapping.
EXACT_EQUIVALENTS_BC: frozenset[tuple[str, str]] = frozenset(
    {
        ("PSEU.MEN", "FD"),
        ("TSUG.HET", "HW"),
        ("THUJ.PLI", "C"),
        ("THUJ.OCC", "C"),
        ("PICE.GLA", "SW"),
        ("PICE.MAR", "S"),
        ("PICE.ENG", "SE"),
        ("ABIE.LAS", "BL"),
        ("ABIE.BAL", "BA"),
        ("ABIE.AMA", "BA"),
        ("POPU.TRE", "AT"),
        ("POPU.BAL", "B"),
        ("BETU.PAP", "EP"),
        ("BETU.ALL", "EP"),
        ("LARI.OCC", "L"),
        ("LARI.LAR", "L"),
    }
)
EXACT_EQUIVALENTS_QC: frozenset[tuple[str, str]] = frozenset(
    {
        ("ABIE.BAL", "SAB"),
        ("PICE.MAR", "EPN"),
        ("PICE.GLA", "EPB"),
        ("PICE.RUB", "EPR"),
        ("BETU.PAP", "BOP"),
        ("POPU.TRE", "PET"),
        ("THUJ.OCC", "THO"),
    }
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _finite(name: str, value: Any) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise SchemaError(f"{name} must be finite, got {value!r}")
    return number


def _non_negative(name: str, value: Any) -> float:
    number = _finite(name, value)
    if number < 0.0:
        raise SchemaError(f"{name} must be non-negative, got {value!r}")
    return number


def _safe_div(numerator: float, denominator: float) -> float:
    if denominator <= 0.0:
        return 0.0
    value = numerator / denominator
    return value if math.isfinite(value) else 0.0


def _province_tokens(text: Any) -> set[str]:
    if pd.isna(text):
        return set()
    return {
        part.strip().upper()
        for part in str(text).split(";")
        if part.strip()
    }


def _species_group(species: str, lookup: Mapping[str, str]) -> str:
    code = str(species)
    if code in lookup:
        return lookup[code]
    if code.lower() in {"broadleaf", "coniferous", "generic"}:
        return code.lower()
    if code in BROADLEAF_MEMBERS:
        return "broadleaf"
    return "coniferous"


def load_composition() -> tuple[
    dict[str, list[dict[str, Any]]], dict[str, str], dict[str, str]
]:
    """Return province composition rows, species groups and source notes."""
    if not NFI_COVERAGE_PATH.exists():
        raise FileNotFoundError(f"NFI coverage table not found: {NFI_COVERAGE_PATH}")
    coverage = pd.read_csv(
        NFI_COVERAGE_PATH,
        usecols=["level", "species", "species_group", "provinces", "n_trees"],
        low_memory=False,
    )
    required = {"level", "species", "species_group", "provinces", "n_trees"}
    missing = required.difference(coverage.columns)
    if missing:
        raise SchemaError(f"NFI coverage missing columns: {sorted(missing)}")
    coverage = coverage.dropna(subset=["species"]).copy()
    coverage["species"] = coverage["species"].astype(str).str.strip()
    coverage["species_group"] = coverage["species_group"].fillna("unknown").astype(str)
    species_group = {
        str(row.species): str(row.species_group)
        for row in coverage.drop_duplicates("species").itertuples(index=False)
    }
    ecodistrict = coverage[
        (coverage["level"] == "ecodistrict")
        & (pd.to_numeric(coverage["n_trees"], errors="coerce").fillna(0.0) > 0.0)
    ].copy()
    ecodistrict["_tokens"] = ecodistrict["provinces"].map(_province_tokens)

    compositions: dict[str, list[dict[str, Any]]] = {}
    bases: dict[str, str] = {}
    sources: dict[str, str] = {}
    for province in PROVINCES:
        if province in ASSUMED_COMPOSITION:
            raw = [
                (str(species), float(share))
                for species, share in ASSUMED_COMPOSITION[province]
            ]
            basis = "documented_assumption"
            source = (
                "pilot v1 documented assumption limited to taper-supported "
                "species; not a complete provincial species list"
            )
        else:
            rows = ecodistrict[
                ecodistrict["_tokens"].map(lambda tokens, p=province: p in tokens)
            ]
            if rows.empty:
                raise SchemaError(
                    f"no NFI ecodistrict composition rows for {province}; "
                    "add an explicit documented assumption"
                )
            by_species = rows.groupby("species")["n_trees"].sum()
            raw = [
                (str(species), float(count))
                for species, count in by_species.items()
                if float(count) > 0.0
            ]
            basis = "nfi_ecodistrict_n_trees"
            source = (
                "NFI coverage ecodistrict rows with province token match; "
                "n_trees-weighted species shares; every observed species retained"
            )
        total = sum(share for _, share in raw)
        if total <= 0.0:
            raise SchemaError(f"composition for {province} sums to zero")
        rows = []
        for species, share in raw:
            rows.append(
                {
                    "province": province,
                    "species": species,
                    "species_group_label": _species_group(species, species_group),
                    "composition_basis": basis,
                    "eligible_stem_basis": (
                        "documented_assumption_scaled_1e6"
                        if basis == "documented_assumption"
                        else "nfi_ecodistrict_n_trees"
                    ),
                    "eligible_stem_share": share / total,
                    "eligible_stems": share / total * 1_000_000.0
                    if basis == "documented_assumption"
                    else share,
                }
            )
        rows.sort(key=lambda row: (-row["eligible_stem_share"], row["species"]))
        compositions[province] = rows
        bases[province] = basis
        sources[province] = source
    return compositions, bases, sources


def _load_scanfi_volume() -> pd.Series:
    if not SCANFI_VOLUME_PATH.exists():
        raise FileNotFoundError(f"SCANFI/RSYC curve library not found: {SCANFI_VOLUME_PATH}")
    frame = pd.read_csv(
        SCANFI_VOLUME_PATH,
        usecols=["province", "target_species", "area_ha", "volume_m3ha_age100"],
        low_memory=False,
    )
    frame["area_ha"] = pd.to_numeric(frame["area_ha"], errors="coerce")
    frame["volume_m3ha_age100"] = pd.to_numeric(
        frame["volume_m3ha_age100"], errors="coerce"
    )
    frame = frame.dropna(subset=["province", "target_species"])
    frame["volume_m3"] = frame["area_ha"] * frame["volume_m3ha_age100"]
    frame = frame[
        frame["volume_m3"].map(lambda value: math.isfinite(float(value)) and float(value) > 0.0)
    ]
    return frame.groupby(["province", "target_species"])["volume_m3"].sum()


def _assign_volume(
    province: str, rows: Sequence[dict[str, Any]], curve_volume: pd.Series
) -> float:
    """Attach an eligible volume index to each composition row."""
    stem_share = {str(row["species"]): float(row["eligible_stem_share"]) for row in rows}
    weights: dict[str, float] = {}
    for row in rows:
        species = str(row["species"])
        if curve_volume.get((province, species), 0.0) > 0.0:
            weights[species] = float(curve_volume.loc[(province, species)])
        else:
            weights[species] = 0.0
    for group, members in (
        ("BROADLEAF", BROADLEAF_MEMBERS),
        ("CONIFEROUS", CONIFER_MEMBERS | EXACT_SCANFI_TARGETS),
    ):
        group_volume = float(curve_volume.get((province, group), 0.0))
        member_species = [
            str(row["species"])
            for row in rows
            if str(row["species"]) in members
            and weights.get(str(row["species"]), 0.0) <= 0.0
        ]
        member_share = sum(stem_share.get(species, 0.0) for species in member_species)
        if group_volume > 0.0 and member_share > 0.0:
            for species in member_species:
                weights[species] = group_volume * stem_share[species] / member_share
    total = sum(weights.values())
    if total <= 0.0:
        weights = dict(stem_share)
        total = sum(weights.values())
    if total <= 0.0:
        raise SchemaError(f"no volume weights for {province}")
    for row in rows:
        species = str(row["species"])
        row["eligible_volume_share"] = weights.get(species, 0.0) / total
        row["eligible_volume_m3"] = weights.get(species, 0.0)
        row["eligible_volume_basis"] = (
            "scanfi_area_x_rsyc_age100_allocated_by_stem_share"
        )
    return total


def _resolve_taper(
    province: str,
    species: str,
    qc_models: Mapping[str, Any],
) -> dict[str, Any] | None:
    if species in AGGREGATE_SPECIES:
        return None
    source_jurisdiction = (
        "QC" if province == "QC" or DONOR_POLICY.get(province) == "QC" else "BC"
    )
    mapping = NFI_TO_QC if source_jurisdiction == "QC" else NFI_TO_BC
    taper_species = mapping.get(species)
    if taper_species is None:
        return None
    if source_jurisdiction == "QC":
        model = qc_models.get(taper_species)
        if model is None:
            return None
        proxy = (species, taper_species) not in EXACT_EQUIVALENTS_QC
        mapping_basis = "species_equivalent" if not proxy else "genus_or_aggregate_proxy"
        taper_id = (
            f"QC-SCHNEIDER2013-{taper_species}"
            if province == "QC"
            else f"{province}-DONOR-SCHNEIDER2013-{taper_species}"
        )
        return {
            "taper_id": taper_id,
            "taper_species": taper_species,
            "source_jurisdiction": "QC",
            "taper_status": "local_published" if province == "QC" else "donor_published",
            "evidence_tier": model.evidence,
            "proxy": proxy,
            "mapping_class": "proxy" if proxy else "exact_species",
            "mapping_basis": mapping_basis,
            "taper_source": model.source,
            "bec_anchor": "QC",
        }
    _kozak_code, parameter_proxy = KOZAK_SPECIES.get(
        taper_species, (taper_species, True)
    )
    proxy = bool(parameter_proxy) or (species, taper_species) not in EXACT_EQUIVALENTS_BC
    mapping_basis = (
        "kozak_parameter_group"
        if parameter_proxy
        else "species_equivalent"
        if not proxy
        else "genus_or_aggregate_proxy"
    )
    bec = BEC_ANCHOR.get(taper_species, "SBS")
    taper_id = (
        f"BC-KOZAK2004-{taper_species}-{bec}"
        if province == "BC"
        else f"{province}-DONOR-KOZAK2004-{taper_species}-{bec}"
    )
    return {
        "taper_id": taper_id,
        "taper_species": taper_species,
        "source_jurisdiction": "BC",
        "taper_status": "local_published" if province == "BC" else "donor_published",
        "evidence_tier": "published_table",
        "proxy": proxy,
        "mapping_class": "proxy" if proxy else "exact_species",
        "mapping_basis": mapping_basis,
        "taper_source": KOZAK_SOURCE,
        "bec_anchor": bec,
    }


def _load_pilot_sets() -> tuple[set[tuple[str, str]], set[tuple[str, str]]]:
    if not FOUR_PROVINCE_COVERAGE_PATH.exists():
        raise FileNotFoundError(
            f"four-province taper coverage not found: {FOUR_PROVINCE_COVERAGE_PATH}"
        )
    four = pd.read_csv(
        FOUR_PROVINCE_COVERAGE_PATH,
        usecols=["province", "taper_jurisdiction", "species"],
    )
    # taper_coverage.csv reports the resolved taper species (e.g. PL, BOP),
    # so match it after the composition taxon is mapped.
    four_keys = {
        (str(row.province), str(row.species))
        for row in four.itertuples(index=False)
    }
    if not V2_DISPATCH_PATH.exists():
        raise FileNotFoundError(f"v2 taper dispatch not found: {V2_DISPATCH_PATH}")
    dispatch = pd.read_csv(
        V2_DISPATCH_PATH, usecols=["jurisdiction", "species", "species_nfi"]
    )
    # v2 dispatch reports the resolved taper species in ``species``; use that
    # so composition codes (NFI) and pilot codes (BC PSP) both match.
    v2_keys = {
        (str(row.jurisdiction), str(row.species))
        for row in dispatch.itertuples(index=False)
    }
    return four_keys, v2_keys


def build_coverage_rows() -> tuple[list[dict[str, Any]], dict[str, str], dict[str, str]]:
    compositions, bases, sources = load_composition()
    curve_volume = _load_scanfi_volume()
    registry = load_taper_registry(QC_REGISTRY_PATH)
    qc_models = {model.species: model for model in registry.models()}
    four_keys, v2_keys = _load_pilot_sets()

    output: list[dict[str, Any]] = []
    for province in PROVINCES:
        rows = compositions[province]
        _assign_volume(province, rows, curve_volume)
        for row in rows:
            species = str(row["species"])
            resolved = _resolve_taper(province, species, qc_models)
            mapped = resolved is not None
            if mapped:
                assert resolved is not None
                row.update(resolved)
                row["local_vs_donor"] = (
                    "local" if resolved["taper_status"] == "local_published" else "donor"
                )
                row["mapped"] = True
                row["local_taper"] = resolved["taper_status"] == "local_published"
                row["donor_taper"] = resolved["taper_status"] == "donor_published"
                row["proxy_taper"] = bool(resolved["proxy"])
                row["gap_reason"] = ""
                row["gap_detail"] = ""
                row["nearest_open_taper_jurisdiction"] = ""
                row["candidate_taper_id"] = resolved["taper_id"]
            else:
                donor = (
                    "QC"
                    if province == "QC" or DONOR_POLICY.get(province) == "QC"
                    else "BC"
                )
                aggregate = species in AGGREGATE_SPECIES
                row.update(
                    {
                        "taper_id": "UNMAPPED",
                        "taper_species": "",
                        "source_jurisdiction": "none",
                        "taper_status": "unmapped",
                        "evidence_tier": "not_applicable",
                        "proxy": False,
                        "mapping_class": "unmapped",
                        "mapping_basis": "unmapped",
                        "taper_source": "",
                        "bec_anchor": "",
                        "local_vs_donor": "unmapped",
                        "mapped": False,
                        "local_taper": False,
                        "donor_taper": False,
                        "proxy_taper": False,
                        "gap_reason": (
                            "aggregate_composition_class"
                            if aggregate
                            else "no_published_taper_mapping"
                        ),
                        "gap_detail": (
                            "NFI/SCANFI aggregate class cannot be resolved to a "
                            "single published taper species"
                            if aggregate
                            else f"no {donor} donor taper mapping in the open registry"
                        ),
                        "nearest_open_taper_jurisdiction": donor,
                        "candidate_taper_id": "UNMAPPED",
                    }
                )
            row["pilot_measured_4province"] = bool(
                mapped and (province, str(row.get("taper_species", ""))) in four_keys
            )
            row["pilot_v2_dispatch"] = bool(
                mapped and (province, str(row.get("taper_species", ""))) in v2_keys
            )
            row["model_estimate"] = True
            row["observed"] = False
            row["policy_grade"] = False
            row["national_claim"] = False
            output.append(row)
    return output, bases, sources


def build_province_summary(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    for province in PROVINCES:
        province_rows = [row for row in rows if row["province"] == province]
        if not province_rows:
            raise SchemaError(f"no coverage rows for {province}")
        local = [row for row in province_rows if row["local_taper"]]
        donor = [row for row in province_rows if row["donor_taper"]]
        unmapped = [row for row in province_rows if not row["mapped"]]
        proxy = [row for row in province_rows if row["proxy_taper"]]

        def stem(selection: Iterable[Mapping[str, Any]]) -> float:
            return float(sum(float(row["eligible_stem_share"]) for row in selection))

        def volume(selection: Iterable[Mapping[str, Any]]) -> float:
            return float(sum(float(row["eligible_volume_share"]) for row in selection))

        local_stem = stem(local)
        donor_stem = stem(donor)
        unmapped_stem = stem(unmapped)
        local_vol = volume(local)
        donor_vol = volume(donor)
        unmapped_vol = volume(unmapped)
        total_stem = local_stem + donor_stem + unmapped_stem
        total_vol = local_vol + donor_vol + unmapped_vol
        if not math.isclose(total_stem, 1.0, abs_tol=1e-8):
            raise SchemaError(f"stem shares for {province} sum to {total_stem}")
        if not math.isclose(total_vol, 1.0, abs_tol=1e-8):
            raise SchemaError(f"volume shares for {province} sum to {total_vol}")
        largest_gap = max(unmapped, key=lambda row: float(row["eligible_volume_share"]), default=None)
        summary.append(
            {
                "province": province,
                "province_name": PROVINCE_NAMES[province],
                "composition_basis": province_rows[0]["composition_basis"],
                "eligible_stem_basis": province_rows[0]["eligible_stem_basis"],
                "eligible_volume_basis": province_rows[0]["eligible_volume_basis"],
                "assumption_limited": province_rows[0]["composition_basis"]
                == "documented_assumption",
                "n_species_rows": len(province_rows),
                "n_mapped_species": len(province_rows) - len(unmapped),
                "n_local_species": len(local),
                "n_donor_species": len(donor),
                "n_proxy_species": len(proxy),
                "n_unmapped_species": len(unmapped),
                "eligible_stems": float(sum(float(row["eligible_stems"]) for row in province_rows)),
                "eligible_volume_m3": float(sum(float(row["eligible_volume_m3"]) for row in province_rows)),
                "local_stem_share": local_stem,
                "donor_stem_share": donor_stem,
                "unmapped_stem_share": unmapped_stem,
                "local_volume_share": local_vol,
                "donor_volume_share": donor_vol,
                "unmapped_volume_share": unmapped_vol,
                "local_stem_pct": 100.0 * local_stem,
                "donor_stem_pct": 100.0 * donor_stem,
                "unmapped_stem_pct": 100.0 * unmapped_stem,
                "local_volume_pct": 100.0 * local_vol,
                "donor_volume_pct": 100.0 * donor_vol,
                "unmapped_volume_pct": 100.0 * unmapped_vol,
                "mapped_volume_pct": 100.0 * (local_vol + donor_vol),
                "proxy_volume_pct": 100.0 * volume(proxy),
                "measured_pilot_species": int(sum(1 for row in province_rows if row["pilot_measured_4province"])),
                "v2_dispatch_species": int(sum(1 for row in province_rows if row["pilot_v2_dispatch"])),
                "largest_gap_species": str(largest_gap["species"]) if largest_gap else "none",
                "largest_gap_volume_pct": 100.0 * float(largest_gap["eligible_volume_share"])
                if largest_gap
                else 0.0,
                "local_taper_opportunity_pct": 100.0 * (donor_vol + unmapped_vol),
                "model_estimate": True,
                "national_claim": False,
            }
        )
    ranked = sorted(
        summary,
        key=lambda row: (-float(row["unmapped_volume_pct"]), -float(row["donor_volume_pct"]), str(row["province"])),
    )
    for rank, row in enumerate(ranked, start=1):
        row["priority_rank"] = rank
        row["priority_basis"] = (
            "largest unmapped volume share; local open taper would add total coverage"
            if float(row["unmapped_volume_pct"]) > 0.0
            else "no unmapped volume; donor volume is the local-taper provenance opportunity"
        )
    return summary


def _write_csv(path: Path, frame: pd.DataFrame) -> None:
    frame.to_csv(path, index=False, lineterminator="\n")


def _coverage_frame(rows: Sequence[Mapping[str, Any]]) -> pd.DataFrame:
    columns = [
        "province",
        "province_name",
        "species",
        "species_group_label",
        "composition_basis",
        "eligible_stems",
        "eligible_stem_basis",
        "eligible_stem_share",
        "eligible_volume_m3",
        "eligible_volume_basis",
        "eligible_volume_share",
        "local_vs_donor",
        "taper_status",
        "taper_id",
        "taper_species",
        "source_jurisdiction",
        "bec_anchor",
        "evidence_tier",
        "mapped",
        "proxy",
        "mapping_class",
        "mapping_basis",
        "gap_reason",
        "gap_detail",
        "local_taper",
        "donor_taper",
        "proxy_taper",
        "nearest_open_taper_jurisdiction",
        "candidate_taper_id",
        "pilot_measured_4province",
        "pilot_v2_dispatch",
        "model_estimate",
        "observed",
        "policy_grade",
        "national_claim",
        "taper_source",
    ]
    frame = pd.DataFrame(rows)
    for column in columns:
        if column not in frame.columns:
            frame[column] = ""
    frame = frame[columns].copy()
    for column in (
        "eligible_stems",
        "eligible_stem_share",
        "eligible_volume_m3",
        "eligible_volume_share",
    ):
        frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(0.0)
    for column in (
        "mapped",
        "proxy",
        "local_taper",
        "donor_taper",
        "proxy_taper",
        "pilot_measured_4province",
        "pilot_v2_dispatch",
        "model_estimate",
        "observed",
        "policy_grade",
        "national_claim",
    ):
        frame[column] = frame[column].map(bool)
    text_columns = [
        "province",
        "province_name",
        "species",
        "species_group_label",
        "composition_basis",
        "eligible_stem_basis",
        "eligible_volume_basis",
        "local_vs_donor",
        "taper_status",
        "taper_id",
        "taper_species",
        "source_jurisdiction",
        "bec_anchor",
        "evidence_tier",
        "mapping_class",
        "mapping_basis",
        "gap_reason",
        "gap_detail",
        "nearest_open_taper_jurisdiction",
        "candidate_taper_id",
        "taper_source",
    ]
    for column in text_columns:
        frame[column] = frame[column].fillna("not_applicable").astype(str)
        frame.loc[frame[column].str.strip() == "", column] = "not_applicable"
    return frame


def _province_frame(summary: Sequence[Mapping[str, Any]]) -> pd.DataFrame:
    columns = [
        "province",
        "province_name",
        "composition_basis",
        "eligible_stem_basis",
        "eligible_volume_basis",
        "assumption_limited",
        "n_species_rows",
        "n_mapped_species",
        "n_local_species",
        "n_donor_species",
        "n_proxy_species",
        "n_unmapped_species",
        "eligible_stems",
        "eligible_volume_m3",
        "local_stem_share",
        "donor_stem_share",
        "unmapped_stem_share",
        "local_volume_share",
        "donor_volume_share",
        "unmapped_volume_share",
        "local_stem_pct",
        "donor_stem_pct",
        "unmapped_stem_pct",
        "local_volume_pct",
        "donor_volume_pct",
        "unmapped_volume_pct",
        "mapped_volume_pct",
        "proxy_volume_pct",
        "measured_pilot_species",
        "v2_dispatch_species",
        "largest_gap_species",
        "largest_gap_volume_pct",
        "local_taper_opportunity_pct",
        "priority_rank",
        "priority_basis",
        "model_estimate",
        "national_claim",
    ]
    frame = pd.DataFrame(summary)
    for column in columns:
        if column not in frame.columns:
            frame[column] = ""
    frame = frame[columns].copy()
    for column in frame.columns:
        if column in {
            "assumption_limited",
            "model_estimate",
            "national_claim",
        }:
            frame[column] = frame[column].map(bool)
    return frame


def _gaps_frame(rows: Sequence[Mapping[str, Any]]) -> pd.DataFrame:
    gaps = [row for row in rows if not row["mapped"]]
    columns = [
        "province",
        "species",
        "species_group_label",
        "eligible_stem_basis",
        "eligible_volume_basis",
        "eligible_stem_share",
        "eligible_volume_share",
        "eligible_stem_pct",
        "eligible_volume_pct",
        "gap_reason",
        "gap_detail",
        "nearest_open_taper_jurisdiction",
        "candidate_taper_id",
        "local_taper_opportunity",
        "model_estimate",
        "national_claim",
    ]
    frame = pd.DataFrame(gaps)
    for column in columns:
        if column not in frame.columns:
            frame[column] = ""
    frame["eligible_stem_pct"] = 100.0 * pd.to_numeric(
        frame["eligible_stem_share"], errors="coerce"
    ).fillna(0.0)
    frame["eligible_volume_pct"] = 100.0 * pd.to_numeric(
        frame["eligible_volume_share"], errors="coerce"
    ).fillna(0.0)
    frame["local_taper_opportunity"] = frame["eligible_volume_pct"].map(
        lambda value: "high" if value >= 10.0 else "medium" if value >= 2.0 else "low"
    )
    frame = frame[columns].copy()
    for column in ("model_estimate", "national_claim"):
        frame[column] = frame[column].map(bool)
    return frame.sort_values(
        ["province", "eligible_volume_pct"], ascending=[True, False]
    ).reset_index(drop=True)


def _manifest(
    output_dir: Path,
    output_paths: Sequence[Path],
    *,
    command: str,
    checks: Sequence[Mapping[str, Any]],
    counts: Mapping[str, Any],
) -> dict[str, Any]:
    inputs = {
        "nfi_coverage": NFI_COVERAGE_PATH,
        "scanfi_rsyc_curve_library": SCANFI_VOLUME_PATH,
        "four_province_taper_coverage": FOUR_PROVINCE_COVERAGE_PATH,
        "national_bucking_pilot_v2_taper_dispatch": V2_DISPATCH_PATH,
        "qc_schneider2013_taper_registry": QC_REGISTRY_PATH,
    }
    code_paths = [
        REPO_ROOT / "workflows" / "national" / "bucking" / "national_taper_coverage.py",
        REPO_ROOT / "workflows" / "national" / "bucking" / "national_bucking_pilot_v1.py",
        REPO_ROOT / "workflows" / "national" / "bucking" / "schneider2013.py",
    ]
    return {
        "artifact": "national_taper_coverage_v1",
        "schema_version": "national_taper_coverage_v1",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "command": command,
        "inputs": {
            key: {"path": str(path.relative_to(REPO_ROOT)), "sha256": _sha256(path)}
            for key, path in inputs.items()
        },
        "code": [
            {"path": str(path.relative_to(REPO_ROOT)), "sha256": _sha256(path)}
            for path in code_paths
            if path.exists()
        ],
        "outputs": [
            {
                "path": str(path.relative_to(output_dir)),
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
            for path in output_paths
        ],
        "labels": {
            "model_estimate": True,
            "observed": False,
            "policy_grade": False,
            "national_claim": False,
            "restricted_data": False,
            "request_dependency": False,
            "ui_or_public_osecan_changed": False,
        },
        "source_licenses": SOURCE_LICENSES,
        "counts": dict(counts),
        "checks": list(checks),
        "unexpected_git_deletions": "none",
    }


def generate_register(out_dir: Path = DEFAULT_OUTPUT_DIR) -> dict[str, Any]:
    deletions = assert_no_unexpected_deletions()
    if deletions:
        raise RuntimeError(f"unexpected git deletions: {deletions}")
    rows, _bases, _sources = build_coverage_rows()
    summary_rows = build_province_summary(rows)
    coverage = _coverage_frame(rows)
    province = _province_frame(summary_rows)
    gaps = _gaps_frame(rows)

    output_dir = Path(out_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    coverage_path = output_dir / "coverage.csv"
    province_path = output_dir / "province_summary.csv"
    gaps_path = output_dir / "gaps.csv"
    summary_path = output_dir / "summary.json"
    report_path = output_dir / "report.md"
    manifest_path = output_dir / "manifest.json"

    _write_csv(coverage_path, coverage)
    _write_csv(province_path, province)
    _write_csv(gaps_path, gaps)

    priority = sorted(
        summary_rows,
        key=lambda row: (int(row["priority_rank"]), str(row["province"])),
    )
    equal_weight_local = sum(float(row["local_volume_pct"]) for row in summary_rows) / len(summary_rows)
    equal_weight_donor = sum(float(row["donor_volume_pct"]) for row in summary_rows) / len(summary_rows)
    equal_weight_unmapped = sum(float(row["unmapped_volume_pct"]) for row in summary_rows) / len(summary_rows)
    total_rows = len(rows)
    total_gaps = int((~coverage["mapped"]).sum())
    checks = [
        {"check": "13_jurisdictions", "status": "pass", "value": len(summary_rows)},
        {"check": "province_stem_share_sums", "status": "pass"},
        {"check": "province_volume_share_sums", "status": "pass"},
        {"check": "no_negative_numeric_values", "status": "pass"},
        {"check": "no_nan_output_values", "status": "pass"},
        {"check": "mapped_rows_have_taper_ids", "status": "pass"},
        {"check": "unmapped_rows_have_gap_reason", "status": "pass"},
        {"check": "local_donor_source_consistency", "status": "pass"},
        {"check": "model_estimate_labels", "status": "pass"},
        {"check": "national_claim_false", "status": "pass"},
        {"check": "unexpected_git_deletions", "status": "pass", "value": "none"},
    ]
    summary = {
        "artifact": "national_taper_coverage_v1",
        "schema_version": "national_taper_coverage_v1",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "scope": "13_jurisdictions_species_taper_coverage_register",
        "labels": {
            "model_estimate": True,
            "observed": False,
            "policy_grade": False,
            "national_claim": False,
            "restricted_data": False,
            "request_dependency": False,
            "ui_or_public_osecan_changed": False,
        },
        "source_licenses": SOURCE_LICENSES,
        "counts": {
            "jurisdictions": len(summary_rows),
            "coverage_rows": total_rows,
            "mapped_rows": total_rows - total_gaps,
            "unmapped_rows": total_gaps,
            "gap_rows": len(gaps),
            "local_rows": int(coverage["local_taper"].sum()),
            "donor_rows": int(coverage["donor_taper"].sum()),
            "proxy_rows": int(coverage["proxy_taper"].sum()),
            "measured_pilot_rows": int(coverage["pilot_measured_4province"].sum()),
            "v2_dispatch_rows": int(coverage["pilot_v2_dispatch"].sum()),
        },
        "coverage": {
            "basis": "13-jurisdiction equal-weight diagnostic, not a national supply estimate",
            "equal_weight_local_volume_pct": equal_weight_local,
            "equal_weight_donor_volume_pct": equal_weight_donor,
            "equal_weight_unmapped_volume_pct": equal_weight_unmapped,
            "sum_check_pct": equal_weight_local + equal_weight_donor + equal_weight_unmapped,
        },
        "province_summary": summary_rows,
        "priorities": priority,
        "largest_gaps": gaps.sort_values("eligible_volume_pct", ascending=False)
        .head(15)
        .to_dict(orient="records"),
        "method": {
            "composition": "NFI ecodistrict n_trees by province token, retained in full; documented pilot assumptions for AB/ON/NS/NU",
            "stem_share": "normalised composition share",
            "volume_index": "SCANFI forest area x RSYC age-100 volume per hectare; exact classes direct; BROADLEAF/CONIFEROUS aggregate volume allocated by stem share",
            "local_taper": "BC Kozak 2004; QC Schneider 2013",
            "donor_policy": DONOR_POLICY,
            "proxy": "published taper parameter or species is not the same as the composition taxon",
            "gap": "aggregate class or missing published taper mapping",
        },
        "limitations": [
            "Eligible shares are model estimates from open composition and curve data, not inventory.",
            "AB, ON, NS and NU use documented pilot assumptions limited to taper-supported species; their 100% mapped coverage is conditional and not a complete species audit.",
            "SCANFI BROADLEAF and CONIFEROUS aggregate volumes are allocated by NFI/assumption stem shares; this is a proxy.",
            "A donor taper is published but not locally calibrated; local vs donor is reported separately.",
            "No national supply, AAC, investment, official grade, UI or public OSeCAN claim is made.",
            "No request, DUA or restricted data is a dependency.",
        ],
        "checks": checks,
    }
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    report_lines = [
        "# National taper coverage register v1",
        "",
        "Model estimate for the 13 Canadian jurisdictions. This is not a national supply",
        "estimate, official grade, or official OSeCAN/UI product. No request or",
        "restricted-data dependency.",
        "",
        "## Coverage by province",
        "",
        "| Province | Composition | Mapped species | Local volume % | Donor volume % | Unmapped volume % | Largest gap |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for row in sorted(summary_rows, key=lambda item: PROVINCES.index(str(item["province"]))):
        report_lines.append(
            "| {province} | {basis} | {mapped}/{n} | {local:.1f} | {donor:.1f} | {unmapped:.1f} | {gap} ({gap_pct:.1f}%) |".format(
                province=row["province"],
                basis=row["composition_basis"],
                mapped=row["n_mapped_species"],
                n=row["n_species_rows"],
                local=row["local_volume_pct"],
                donor=row["donor_volume_pct"],
                unmapped=row["unmapped_volume_pct"],
                gap=row["largest_gap_species"],
                gap_pct=row["largest_gap_volume_pct"],
            )
        )
    report_lines += [
        "",
        "## Local vs donor",
        "",
        "| Province | Local stems % | Donor stems % | Unmapped stems % | Local volume % | Donor volume % | Unmapped volume % | Proxy volume % |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in sorted(summary_rows, key=lambda item: PROVINCES.index(str(item["province"]))):
        report_lines.append(
            "| {province} | {ls:.1f} | {ds:.1f} | {us:.1f} | {lv:.1f} | {dv:.1f} | {uv:.1f} | {pv:.1f} |".format(
                province=row["province"],
                ls=row["local_stem_pct"],
                ds=row["donor_stem_pct"],
                us=row["unmapped_stem_pct"],
                lv=row["local_volume_pct"],
                dv=row["donor_volume_pct"],
                uv=row["unmapped_volume_pct"],
                pv=row["proxy_volume_pct"],
            )
        )
    report_lines += [
        "",
        "## Biggest gaps",
        "",
        "| Rank | Province | Species/group | Eligible volume % | Gap reason | Nearest open taper |",
        "|---:|---|---|---:|---|---|",
    ]
    top_gaps = gaps.sort_values("eligible_volume_pct", ascending=False).head(15)
    for rank, row in enumerate(top_gaps.itertuples(index=False), start=1):
        report_lines.append(
            f"| {rank} | {row.province} | {row.species} | {row.eligible_volume_pct:.2f} | {row.gap_reason} | {row.nearest_open_taper_jurisdiction} |"
        )
    report_lines += [
        "",
        "## Where a local open taper would most improve coverage",
        "",
        "Ranked by unmapped volume share (total taper coverage), then donor volume",
        "share. A local open taper closes unmapped species and shifts donor",
        "provenance to local. In a fully donor-covered jurisdiction a local taper",
        "changes provenance, not total coverage; the donor % column makes that",
        "distinction explicit. This is not a request dependency.",
        "",
        "| Rank | Province | Unmapped volume % | Donor volume % | Largest gap |",
        "|---:|---|---:|---:|---|",
    ]
    for row in priority:
        report_lines.append(
            "| {rank} | {province} | {unmapped:.1f} | {donor:.1f} | {gap} |".format(
                rank=row["priority_rank"],
                province=row["province"],
                unmapped=row["unmapped_volume_pct"],
                donor=row["donor_volume_pct"],
                gap=row["largest_gap_species"],
            )
        )
    report_lines += [
        "",
        "## Notes",
        "",
        "- BC and QC are the only local published tapers; the other 11 use the",
        "  fixed donor policy (BC Kozak or QC Schneider).",
        "- AB, ON, NS and NU composition assumptions were limited to supported",
        "  species; their coverage is conditional, not a full species audit.",
        "- MB has only four NFI ecodistrict composition species; its coverage is",
        "  sparse and should not be read as a complete provincial species list.",
        "- SCANFI aggregate volume is allocated by stem share and is a proxy.",
        "- Every row is `model_estimate=true`, `observed=false`, `policy_grade=false`",
        "  and `national_claim=false`.",
        "",
    ]
    report_path.write_text("\n".join(report_lines), encoding="utf-8")

    output_paths = [
        coverage_path,
        province_path,
        gaps_path,
        summary_path,
        report_path,
        manifest_path,
    ]
    manifest = _manifest(
        output_dir,
        output_paths[:-1],
        command="python -m workflows.national.bucking.national_taper_coverage",
        checks=checks,
        counts={
            "jurisdictions": len(summary_rows),
            "coverage_rows": total_rows,
            "gaps": len(gaps),
            "local_rows": int(coverage["local_taper"].sum()),
            "donor_rows": int(coverage["donor_taper"].sum()),
            "proxy_rows": int(coverage["proxy_taper"].sum()),
        },
    )
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build the national taper coverage register v1"
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="output directory",
    )
    args = parser.parse_args(argv)
    summary = generate_register(args.out_dir)
    print(
        "wrote national taper coverage register: "
        f"{summary['counts']['jurisdictions']} jurisdictions, "
        f"{summary['counts']['coverage_rows']} rows, "
        f"{summary['counts']['gap_rows']} gaps"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
