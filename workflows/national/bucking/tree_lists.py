"""FAIB PSP tree-list adapter and stratified TSA24 sample selection.

The FAIB PSP archive is the first measured tree-list source for the national
bucking pipeline.  This module documents the column mapping and keeps the
selection logic explicit: the pilot never samples silently.

Column mapping (FAIB -> contract)
---------------------------------
``SITE_IDENTIFIER`` -> ``site_id``
``CLSTR_ID``        -> ``plot_id`` (one PSP cluster/plot)
``VISIT_NUMBER``    -> ``visit_id`` component
``TREE_NO``         -> ``tree_id`` component
``SPECIES``         -> ``species`` (BC PSP code, uppercased)
``DBH``             -> ``dbh_cm``
``HEIGHT``          -> ``height_m``
``LV_D``            -> ``status`` (``L`` -> live, ``D`` -> dead)
``PHF_TREE``        -> ``expansion_factor`` (per-hectare, already includes plot weight)
``VOL_MER``         -> ``observed_merchantable_m3`` (per tree, m3)
``VOL_WSV``         -> ``observed_whole_stem_m3`` (per tree, m3)
``AGE_TOT``         -> ``age`` (tree age; plot ``PROJ_AGE_1`` is the fallback)
``BEC_ZONE``        -> ``bec_zone`` (from ``faib_header``)
``BEC_SBZ``         -> ``bec_subzone``
``SI1``/``SI2``/``SI3`` -> ``site_index`` (plot maximum, from the compiled site-age table)
``PROJ_AGE_1``      -> ``stand_age`` (latest visit)
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np
import pandas as pd

from .schema import TreeRecord

__all__ = [
    "DEFAULT_ALLOCATION",
    "FaibTreeList",
    "SampleSelection",
    "load_faib_psp_tree_list",
    "sample_plot_trees",
    "select_stratified_sample",
]

DEFAULT_ALLOCATION: tuple[tuple[str, str, int], ...] = (
    ("SBS", "SW", 6),
    ("SBS", "BL", 3),
    ("SBS", "PL", 2),
    ("SBS", "AT", 1),
    ("SBS", "FD", 1),
    ("SBS", "EP", 1),
    ("ESSF", "BL", 2),
    ("ESSF", "SE", 1),
    ("ICH", "HW", 1),
    ("ICH", "SW", 1),
    ("SBPS", "PL", 1),
)

_TREE_COLUMNS = (
    "SITE_IDENTIFIER",
    "CLSTR_ID",
    "VISIT_NUMBER",
    "PLOT",
    "TREE_NO",
    "SPECIES",
    "DBH",
    "HEIGHT",
    "LV_D",
    "PHF_TREE",
    "VOL_MER",
    "VOL_WSV",
    "AGE_TOT",
)


@dataclass(frozen=True, slots=True)
class FaibTreeList:
    trees: tuple[TreeRecord, ...]
    plots: pd.DataFrame
    stats: dict[str, Any]


@dataclass(frozen=True, slots=True)
class SampleSelection:
    trees: tuple[TreeRecord, ...]
    plots: tuple[dict[str, Any], ...]
    strata: tuple[dict[str, Any], ...]
    seed: int
    max_plots: int
    max_trees_per_plot: int

    def manifest_record(self) -> dict[str, Any]:
        return {
            "method": "stratified plots by BEC zone and supported leading species; "
            "up to max_trees_per_plot trees per plot, sampled across species and DBH",
            "seed": self.seed,
            "max_plots": self.max_plots,
            "max_trees_per_plot": self.max_trees_per_plot,
            "n_plots": len(self.plots),
            "n_trees": len(self.trees),
            "strata": list(self.strata),
        }


def _clean_id(value: Any) -> str | None:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    if isinstance(value, (int, np.integer)):
        return str(int(value))
    if isinstance(value, (float, np.floating)) and float(value).is_integer():
        return str(int(value))
    text = str(value).strip()
    return text or None


def _numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def _species_crosswalk(path: Path) -> dict[str, dict[str, Any]]:
    frame = pd.read_csv(path, dtype=str)
    required = {"psp_species", "group", "known"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"species crosswalk missing columns: {', '.join(sorted(missing))}")
    crosswalk: dict[str, dict[str, Any]] = {}
    for row in frame.itertuples(index=False):
        code = str(row.psp_species).strip().upper()
        if not code:
            continue
        crosswalk[code] = {
            "fvs_species": (str(row.fvs_species).strip().upper() if pd.notna(row.fvs_species) else ""),
            "group": str(row.group).strip() if pd.notna(row.group) else "UNKNOWN",
            "known": str(row.known).strip().lower() == "true",
        }
    return crosswalk


def load_faib_psp_tree_list(
    raw_root: Path,
    *,
    tsa_name: str = "Prince George",
    species_crosswalk_path: Path | None = None,
    latest_visit_only: bool = True,
    supported_species: Iterable[str] | None = None,
) -> FaibTreeList:
    """Load live, valid TSA24 PSP trees at the latest visit per plot."""
    base = Path(raw_root) / "bc_faib_ground_plots" / "psp"
    header_path = base / "faib_header.csv.gz"
    visit_path = base / "faib_sample_byvisit.csv.gz"
    tree_path = base / "faib_tree_detail.csv.gz"
    siteage_path = base / "faib_compiled_spcsmries_siteage.csv.gz"
    for path in (header_path, visit_path, tree_path):
        if not path.exists():
            raise FileNotFoundError(f"FAIB PSP input not found: {path}")

    header = pd.read_csv(
        header_path,
        usecols=[
            "SITE_IDENTIFIER",
            "TSA_DESC",
            "BEC_ZONE",
            "BEC_SBZ",
            "Latitude",
            "Longitude",
        ],
        low_memory=False,
    )
    header["site_id"] = header["SITE_IDENTIFIER"].map(_clean_id)
    tsa_mask = header["TSA_DESC"].astype(str).str.contains(tsa_name, case=False, na=False)
    selected_header = header.loc[tsa_mask].copy()
    if selected_header.empty:
        raise ValueError(f"no FAIB PSP sites matched TSA_DESC containing {tsa_name!r}")
    selected_header = selected_header.drop_duplicates("site_id")
    site_ids = set(selected_header["site_id"].dropna())

    visits = pd.read_csv(
        visit_path,
        usecols=["SITE_IDENTIFIER", "CLSTR_ID", "VISIT_NUMBER", "MEAS_YR", "PROJ_AGE_1"],
        low_memory=False,
    )
    visits["site_id"] = visits["SITE_IDENTIFIER"].map(_clean_id)
    visits["plot_id"] = visits["CLSTR_ID"].map(_clean_id)
    visits = visits[visits["site_id"].isin(site_ids)].copy()
    visits["VISIT_NUMBER"] = _numeric(visits["VISIT_NUMBER"]).astype("Int64")
    visits["MEAS_YR"] = _numeric(visits["MEAS_YR"])
    visits["PROJ_AGE_1"] = _numeric(visits["PROJ_AGE_1"])
    if latest_visit_only:
        visits = visits.sort_values(["plot_id", "MEAS_YR", "VISIT_NUMBER"]).drop_duplicates(
            "plot_id", keep="last"
        )
    visits = visits.dropna(subset=["plot_id", "VISIT_NUMBER"])
    visit_keys = visits[["plot_id", "VISIT_NUMBER"]].drop_duplicates()

    trees = pd.read_csv(tree_path, usecols=list(_TREE_COLUMNS), low_memory=False)
    trees["site_id"] = trees["SITE_IDENTIFIER"].map(_clean_id)
    trees["plot_id"] = trees["CLSTR_ID"].map(_clean_id)
    trees["VISIT_NUMBER"] = _numeric(trees["VISIT_NUMBER"]).astype("Int64")
    trees = trees[trees["site_id"].isin(site_ids)].copy()
    trees = trees.merge(visit_keys, on=["plot_id", "VISIT_NUMBER"], how="inner")

    stats: dict[str, Any] = {
        "tsa_name": tsa_name,
        "input_sites": int(len(header)),
        "selected_sites": int(len(site_ids)),
        "selected_plots": int(visits["plot_id"].nunique()),
        "selected_tree_rows": int(len(trees)),
        "dropped": {},
    }
    dropped: dict[str, int] = {}
    dropped["dead"] = int((trees["LV_D"].astype(str).str.upper() != "L").sum())
    trees = trees[trees["LV_D"].astype(str).str.upper() == "L"].copy()

    for column in ("DBH", "HEIGHT", "PHF_TREE", "VOL_MER", "VOL_WSV", "AGE_TOT"):
        trees[column] = _numeric(trees[column])
    dropped["invalid_dbh"] = int((trees["DBH"] <= 0).sum())
    dropped["short_height"] = int((trees["HEIGHT"] <= 1.3).sum())
    dropped["invalid_expansion"] = int((trees["PHF_TREE"] <= 0).sum())
    dropped["missing_species"] = int(trees["SPECIES"].isna().sum())
    trees = trees[
        (trees["DBH"] > 0)
        & (trees["HEIGHT"] > 1.3)
        & (trees["PHF_TREE"] > 0)
        & trees["SPECIES"].notna()
    ].copy()

    tree_header = selected_header[
        ["site_id", "BEC_ZONE", "BEC_SBZ", "Latitude", "Longitude"]
    ].rename(
        columns={
            "BEC_ZONE": "bec_zone",
            "BEC_SBZ": "bec_subzone",
            "Latitude": "latitude",
            "Longitude": "longitude",
        }
    )
    trees = trees.merge(tree_header, on="site_id", how="left")
    trees["bec_zone"] = trees["bec_zone"].astype(str).str.strip().str.upper()
    dropped["missing_bec"] = int((trees["bec_zone"].isin({"", "NAN", "NONE"})).sum())
    trees = trees[~trees["bec_zone"].isin({"", "NAN", "NONE"})].copy()
    stats["live_valid_trees"] = int(len(trees))

    plot_age = visits.set_index("plot_id")["PROJ_AGE_1"].to_dict()
    site_index = _load_site_index(siteage_path, visit_keys)
    plot_site_index = (
        site_index.groupby("plot_id")["site_index"].max().to_dict() if not site_index.empty else {}
    )

    crosswalk_path = species_crosswalk_path
    if crosswalk_path is None:
        crosswalk_path = (
            Path(__file__).resolve().parents[3].parent.parent
            / "ws4"
            / "nemora"
            / "config"
            / "species"
            / "bc_psp_to_group.csv"
        )
    crosswalk = _species_crosswalk(crosswalk_path) if Path(crosswalk_path).exists() else {}
    supported = {code.upper() for code in (supported_species or _supported_species())}
    if not crosswalk:
        stats.setdefault("warnings", []).append(
            f"species crosswalk not found at {crosswalk_path}; species_group set to UNKNOWN"
        )

    records: list[TreeRecord] = []
    for row in trees.itertuples(index=False):
        species = str(row.SPECIES).strip().upper()
        mapping = crosswalk.get(species, {"group": "UNKNOWN", "known": False})
        plot_id = _clean_id(row.plot_id)
        visit_number = _clean_id(row.VISIT_NUMBER)
        plot_number = _clean_id(row.PLOT)
        tree_number = _clean_id(row.TREE_NO)
        tree_id = ":".join(
            part for part in (plot_id, visit_number, plot_number, tree_number) if part
        )
        stand_age = plot_age.get(plot_id)
        age = _finite(row.AGE_TOT)
        if age is None:
            age = _finite(stand_age)
        records.append(
            TreeRecord(
                tree_id=tree_id,
                site_id=str(row.site_id),
                species=species,
                dbh_cm=float(row.DBH),
                height_m=float(row.HEIGHT),
                status="live",
                expansion_factor=float(row.PHF_TREE),
                bec_zone=str(row.bec_zone),
                age=age,
                provenance=(
                    "faib_psp:tree_detail+header+sample_byvisit; "
                    f"TSA24={tsa_name}; latest visit per CLSTR_ID"
                ),
                plot_id=plot_id,
                visit_id=f"{plot_id}:{visit_number}" if visit_number else None,
                species_group=str(mapping["group"]),
                bec_subzone=_text_or_none(row.bec_subzone),
                site_index=_finite(plot_site_index.get(plot_id)),
                stand_age=_finite(stand_age),
                observed_merchantable_m3=_finite(row.VOL_MER),
                observed_whole_stem_m3=_finite(row.VOL_WSV),
                latitude=_finite(row.latitude),
                longitude=_finite(row.longitude),
            )
        )

    plot_rows: list[dict[str, Any]] = []
    for plot_id, group in trees.groupby("plot_id", sort=True):
        first = group.iloc[0]
        supported_rows = group[group["SPECIES"].astype(str).str.upper().isin(supported)]
        species_counts = group["SPECIES"].astype(str).str.upper().value_counts()
        supported_counts = supported_rows["SPECIES"].astype(str).str.upper().value_counts()
        plot_rows.append(
            {
                "plot_id": plot_id,
                "site_id": str(first["site_id"]),
                "bec_zone": str(first["bec_zone"]),
                "bec_subzone": _text_or_none(first["bec_subzone"]),
                "latitude": _finite(first["latitude"]),
                "longitude": _finite(first["longitude"]),
                "stand_age": _finite(plot_age.get(plot_id)),
                "site_index": _finite(plot_site_index.get(plot_id)),
                "n_live": int(len(group)),
                "n_live_supported": int(len(supported_rows)),
                "leading_species_all": str(species_counts.index[0]) if len(species_counts) else None,
                "leading_species": (
                    str(supported_counts.index[0]) if len(supported_counts) else None
                ),
                "species_counts": ";".join(
                    f"{code}:{count}" for code, count in species_counts.items()
                ),
            }
        )
    plots = pd.DataFrame(plot_rows)
    stats["plots_with_live_trees"] = int(len(plots))
    stats["dropped"] = dropped
    stats["species_crosswalk"] = crosswalk_path.name
    stats["latest_visit_only"] = latest_visit_only
    return FaibTreeList(trees=tuple(records), plots=plots, stats=stats)


def _supported_species() -> set[str]:
    # Kept local to avoid importing nemora for pure data inspection.  The
    # pilot passes the registry's supported set to the sampler explicitly.
    return {
        "AC",
        "AT",
        "B",
        "BA",
        "BL",
        "C",
        "CW",
        "D",
        "DR",
        "E",
        "EP",
        "F",
        "FD",
        "H",
        "HM",
        "HW",
        "L",
        "LW",
        "MB",
        "PA",
        "PL",
        "PW",
        "PY",
        "S",
        "SB",
        "SE",
        "SS",
        "SW",
        "Y",
        "YC",
    }


def _load_site_index(path: Path, visit_keys: pd.DataFrame) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=["plot_id", "site_index"])
    frame = pd.read_csv(
        path,
        usecols=[
            "SITE_IDENTIFIER",
            "CLSTR_ID",
            "VISIT_NUMBER",
            "SI1",
            "SI2",
            "SI3",
        ],
        low_memory=False,
    )
    frame["plot_id"] = frame["CLSTR_ID"].map(_clean_id)
    frame["VISIT_NUMBER"] = _numeric(frame["VISIT_NUMBER"]).astype("Int64")
    frame = frame.merge(visit_keys, on=["plot_id", "VISIT_NUMBER"], how="inner")
    for column in ("SI1", "SI2", "SI3"):
        frame[column] = _numeric(frame[column])
    frame["site_index"] = frame[["SI1", "SI2", "SI3"]].max(axis=1)
    frame = frame.dropna(subset=["site_index"])
    frame = frame[frame["site_index"] > 0]
    return frame[["plot_id", "site_index"]]


def _finite(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(number):
        return None
    return number


def _text_or_none(value: Any) -> str | None:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    text = str(value).strip()
    return text or None


def select_stratified_sample(
    trees: Sequence[TreeRecord],
    plots: pd.DataFrame,
    *,
    max_plots: int = 20,
    max_trees_per_plot: int = 15,
    seed: int = 20261115,
    supported_species: Iterable[str] | None = None,
    allocation: Sequence[tuple[str, str, int]] = DEFAULT_ALLOCATION,
) -> SampleSelection:
    """Select a documented BEC x leading-species stratified plot sample.

    Only one plot per site is selected.  Within a plot, trees are sampled
    across species and DBH quantiles.  The returned metadata records the
    quota, candidate count, selected count, and sampled tree count.
    """
    if max_plots <= 0:
        raise ValueError("max_plots must be positive")
    if max_trees_per_plot <= 0:
        raise ValueError("max_trees_per_plot must be positive")
    supported = {code.upper() for code in (supported_species or ())}
    if not supported:
        supported = {tree.species for tree in trees}

    plot_frame = plots.copy()
    if plot_frame.empty:
        raise ValueError("no plots available for sampling")
    plot_frame["bec_zone"] = plot_frame["bec_zone"].astype(str).str.upper()
    plot_frame["leading_species"] = plot_frame["leading_species"].astype(str).str.upper()
    candidates = plot_frame[
        plot_frame["leading_species"].isin(supported)
        & (plot_frame["n_live_supported"] > 0)
    ].copy()

    rng = random.Random(seed)
    selected_plots: list[dict[str, Any]] = []
    selected_plot_ids: set[str] = set()
    selected_sites: set[str] = set()
    strata_records: list[dict[str, Any]] = []

    def take_from(pool: pd.DataFrame, quota: int) -> list[pd.Series]:
        """Sample at most one plot per site from a candidate pool."""
        available = pool[~pool["site_id"].isin(selected_sites)]
        if available.empty or quota <= 0:
            return []
        sites = sorted(available["site_id"].astype(str).unique())
        rng.shuffle(sites)
        chosen: list[pd.Series] = []
        for site in sites:
            if len(chosen) >= quota:
                break
            site_rows = available[available["site_id"].astype(str) == site]
            # Prefer the plot with the most supported live trees; ties are
            # resolved by plot id for determinism.
            site_rows = site_rows.sort_values(
                ["n_live_supported", "plot_id"], ascending=[False, True]
            )
            chosen.append(site_rows.iloc[0])
            selected_sites.add(site)
        return chosen

    for bec_zone, leading_species, quota in allocation:
        remaining_slots = max_plots - len(selected_plots)
        if remaining_slots <= 0:
            break
        pool = candidates[
            (candidates["bec_zone"] == bec_zone.upper())
            & (candidates["leading_species"] == leading_species.upper())
        ]
        chosen = take_from(pool, min(quota, remaining_slots))
        for row in chosen:
            selected_plots.append(row.to_dict())
            selected_plot_ids.add(str(row["plot_id"]))
        strata_records.append(
            {
                "bec_zone": bec_zone.upper(),
                "leading_species": leading_species.upper(),
                "quota": quota,
                "candidate_plots": int(len(pool)),
                "selected_plots": len(chosen),
                "n_sampled_trees": 0,
            }
        )

    remaining = max_plots - len(selected_plots)
    if remaining > 0:
        # Fill from remaining candidates, prioritising BEC zones with the
        # fewest selected plots and then the largest supported tree counts.
        unselected = candidates[~candidates["plot_id"].astype(str).isin(selected_plot_ids)]
        chosen = take_from(unselected, remaining)
        for row in chosen:
            selected_plots.append(row.to_dict())
            selected_plot_ids.add(str(row["plot_id"]))
            bec = str(row["bec_zone"])
            strata_records.append(
                {
                    "bec_zone": bec,
                    "leading_species": str(row["leading_species"]),
                    "quota": 0,
                    "candidate_plots": int(
                        len(candidates[(candidates["bec_zone"] == bec)])
                    ),
                    "selected_plots": 1,
                    "n_sampled_trees": 0,
                    "selection": "fill",
                }
            )

    trees_by_plot: dict[str, list[TreeRecord]] = {}
    for tree in trees:
        if tree.species not in supported:
            continue
        trees_by_plot.setdefault(str(tree.plot_id), []).append(tree)

    sampled_trees: list[TreeRecord] = []
    plot_records: list[dict[str, Any]] = []
    for plot in selected_plots:
        plot_id = str(plot["plot_id"])
        plot_trees = trees_by_plot.get(plot_id, [])
        chosen_trees = sample_plot_trees(plot_trees, max_trees_per_plot)
        sampled_trees.extend(chosen_trees)
        record = dict(plot)
        record["n_sampled_trees"] = len(chosen_trees)
        record["sampled_species"] = ";".join(
            f"{code}:{count}"
            for code, count in pd.Series([tree.species for tree in chosen_trees])
            .value_counts()
            .items()
        )
        plot_records.append(record)

    counted_strata: set[tuple[str, str]] = set()
    for stratum in strata_records:
        key = (stratum["bec_zone"], stratum["leading_species"])
        if key in counted_strata:
            stratum["n_sampled_trees"] = 0
            continue
        counted_strata.add(key)
        stratum["n_sampled_trees"] = sum(
            record["n_sampled_trees"]
            for record in plot_records
            if record["bec_zone"] == stratum["bec_zone"]
            and record["leading_species"] == stratum["leading_species"]
        )

    sampled_trees.sort(key=lambda tree: (str(tree.plot_id), tree.tree_id))
    plot_records.sort(key=lambda record: str(record["plot_id"]))
    return SampleSelection(
        trees=tuple(sampled_trees),
        plots=tuple(plot_records),
        strata=tuple(strata_records),
        seed=seed,
        max_plots=max_plots,
        max_trees_per_plot=max_trees_per_plot,
    )


def sample_plot_trees(
    trees: Sequence[TreeRecord], max_trees: int
) -> list[TreeRecord]:
    """Sample up to ``max_trees`` across species and DBH quantiles."""
    if max_trees <= 0:
        return []
    if len(trees) <= max_trees:
        return sorted(trees, key=lambda tree: tree.tree_id)

    by_species: dict[str, list[TreeRecord]] = {}
    for tree in trees:
        by_species.setdefault(tree.species, []).append(tree)
    for group in by_species.values():
        group.sort(key=lambda tree: (tree.dbh_cm, tree.tree_id))

    species_order = sorted(
        by_species, key=lambda code: (-len(by_species[code]), code)
    )
    slots = {code: 0 for code in species_order}
    if max_trees >= len(species_order):
        for code in species_order:
            slots[code] = 1
    else:
        for code in species_order[:max_trees]:
            slots[code] = 1

    remaining = max_trees - sum(slots.values())
    if remaining > 0:
        quotas = {
            code: len(by_species[code]) / len(trees) * max_trees
            for code in species_order
        }
        remainders = []
        for code in species_order:
            if remaining <= 0:
                break
            capacity = len(by_species[code]) - slots[code]
            if capacity <= 0:
                continue
            add = min(capacity, int(quotas[code] - slots[code]), remaining)
            if add > 0:
                slots[code] += add
                remaining -= add
        for code in species_order:
            remainders.append((quotas[code] - slots[code], code))
        remainders.sort(key=lambda item: (-item[0], item[1]))
        for _fraction, code in remainders:
            if remaining <= 0:
                break
            if slots[code] < len(by_species[code]):
                slots[code] += 1
                remaining -= 1

    selected: list[TreeRecord] = []
    for code in species_order:
        group = by_species[code]
        count = min(slots[code], len(group), max_trees - len(selected))
        if count <= 0:
            break
        selected.extend(_spread_by_dbh(group, count))
    selected = selected[:max_trees]

    if len(selected) < max_trees:
        selected_ids = {tree.tree_id for tree in selected}
        for code in species_order:
            if len(selected) >= max_trees:
                break
            remaining_group = [tree for tree in by_species[code] if tree.tree_id not in selected_ids]
            need = max_trees - len(selected)
            extra = _spread_by_dbh(remaining_group, min(need, len(remaining_group)))
            selected.extend(extra)
            selected_ids.update(tree.tree_id for tree in extra)

    return sorted(selected, key=lambda tree: tree.tree_id)


def _spread_by_dbh(group: Sequence[TreeRecord], count: int) -> list[TreeRecord]:
    if count <= 0:
        return []
    if count >= len(group):
        return list(group)
    indices = np.linspace(0, len(group) - 1, count).round().astype(int)
    unique = sorted(set(int(index) for index in indices))
    if len(unique) < count:
        for index in range(len(group)):
            if index not in unique:
                unique.append(index)
            if len(unique) == count:
                break
        unique = sorted(unique)
    return [group[index] for index in unique]
