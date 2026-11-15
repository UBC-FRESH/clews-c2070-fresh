"""Bucking wrapper, rule loading, and residual accounting.

The reference dynamic program in ``nemora`` assigns dimension sorts and
maximises value.  This module converts its untyped dictionaries into the
national contracts and adds the residue ledger.  Residue is defined
independently as ``taper total - recovered logs`` so the volume balance is
explicit even when the reference bucker returns no logs.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
import yaml

from .schema import BuckedLog, GradeRule, GradeRuleSet, ResidueLedger, TreeRecord
from .taper import (
    NemoraKozakTaper,
    TaperDomainError,
    TaperRegistry,
    UnsupportedTaperError,
)

try:  # pragma: no cover - exercised through the public wrapper
    from nemora.workflows.bucking import (
        BuckingProfile,
        LogSort,
        buck_stem_sorted,
        market_group_for,
    )
except ImportError as exc:  # pragma: no cover - environment guard
    raise ImportError(
        "nemora is required for the BC bucking adapter. Set NEMORA_ROOT and add "
        "its src directory to PYTHONPATH, or install nemora editable."
    ) from exc

__all__ = [
    "BUCKER_BALANCE_ABS_TOL",
    "BUCKER_BALANCE_REL_TOL",
    "BuckingBatch",
    "BuckingError",
    "LOG_COLUMNS",
    "RESIDUE_COLUMNS",
    "buck_tree",
    "buck_trees",
    "load_grade_rules",
    "load_price_profile",
    "logs_frame",
    "residue_frame",
]

# The reference bucker integrates sections on a different edge sequence from
# kozak_total_volume.  The difference is numerical integration noise, not a
# volume leak; the ledger's own balance is exact because residue is defined as
# taper total minus recovered logs.  The bucker-internal check uses a 0.01%
# relative tolerance.
BUCKER_BALANCE_REL_TOL = 1e-4
BUCKER_BALANCE_ABS_TOL = 1e-6

LOG_COLUMNS: tuple[str, ...] = (
    "tree_id",
    "site_id",
    "plot_id",
    "species",
    "bec_zone",
    "log_index",
    "length_m",
    "large_end_dib_cm",
    "small_end_dib_cm",
    "volume_m3",
    "sort_code",
    "product",
    "price_cad_m3",
    "value_cad",
    "evidence",
    "species_market_group",
    "sort_basis",
    "grade_status",
    "price_period",
    "price_source",
)

RESIDUE_COLUMNS: tuple[str, ...] = (
    "tree_id",
    "site_id",
    "plot_id",
    "species",
    "bec_zone",
    "residue_volume_m3",
    "basic_density_odt_m3",
    "residue_odt",
    "moisture_wet_basis",
    "green_tonnes",
    "chip_yield_fraction",
    "chip_green_tonnes",
    "recovered_log_m3",
    "taper_total_m3",
    "bucker_residue_m3",
    "bucker_balance_error_m3",
    "evidence",
    "notes",
)


class BuckingError(ValueError):
    """Raised when a stem cannot be bucked without violating the contracts."""


@dataclass(slots=True)
class BuckingBatch:
    logs: list[BuckedLog] = field(default_factory=list)
    residue: list[ResidueLedger] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)
    models: dict[tuple[str, str], NemoraKozakTaper] = field(default_factory=dict)

    def add_model(self, model: NemoraKozakTaper) -> None:
        self.models.setdefault((model.species, model.bec_zone), model)


def load_grade_rules(path: Path) -> GradeRuleSet:
    """Load the versioned BC interior dimension rule set."""
    rule_path = Path(path)
    if not rule_path.exists():
        raise FileNotFoundError(f"grade rule set not found: {rule_path}")
    payload = yaml.safe_load(rule_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"grade rule set must be a mapping: {rule_path}")
    required = {"jurisdiction", "authority", "version", "basis", "grade_status", "sorts"}
    missing = required.difference(payload)
    if missing:
        raise ValueError(f"grade rule set missing keys: {', '.join(sorted(missing))}")
    raw_rules = payload["sorts"]
    if not isinstance(raw_rules, list) or not raw_rules:
        raise ValueError("grade rule set requires a non-empty sorts list")
    rules = tuple(GradeRule(**record) for record in raw_rules)
    priorities = [rule.priority for rule in rules]
    if len(priorities) != len(set(priorities)):
        raise ValueError("priority values must be unique within a rule set")
    ordered = tuple(sorted(rules, key=lambda rule: rule.priority))
    return GradeRuleSet(
        jurisdiction=str(payload["jurisdiction"]),
        authority=str(payload["authority"]),
        version=str(payload["version"]),
        basis=str(payload["basis"]),
        grade_status=str(payload["grade_status"]),
        rules=ordered,
    )


def load_price_profile(path: Path) -> dict[str, Any]:
    """Load and validate a product-level price profile."""
    price_path = Path(path)
    if not price_path.exists():
        raise FileNotFoundError(f"price profile not found: {price_path}")
    payload = yaml.safe_load(price_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"price profile must be a mapping: {price_path}")
    prices = payload.get("prices")
    if not isinstance(prices, dict) or not prices:
        raise ValueError(f"price profile has no prices mapping: {price_path}")
    for market, products in prices.items():
        if not isinstance(products, dict):
            raise ValueError(f"price profile market {market!r} must map products to prices")
        for product, price in products.items():
            if not isinstance(price, (int, float)) or not math.isfinite(float(price)):
                raise ValueError(
                    f"price profile {market!r}/{product!r} must be a finite number"
                )
    return payload


def _nemora_sorts(rules: GradeRuleSet, species_class: str) -> list[LogSort]:
    return [
        LogSort(
            code=rule.sort_code,
            product=rule.product,
            min_top_cm=rule.min_top_cm,
            min_length_m=rule.min_length_m,
            species_class=rule.species_class,
            priority=rule.priority,
        )
        for rule in rules.for_species_class(species_class)
    ]


def buck_tree(
    tree: TreeRecord,
    model: NemoraKozakTaper,
    rules: GradeRuleSet,
    price_profile: dict[str, Any],
    *,
    profile: BuckingProfile | None = None,
    basic_density_odt_m3: float = 0.45,
    moisture_wet_basis: float = 0.45,
    chip_yield_fraction: float = 0.90,
) -> tuple[list[BuckedLog], ResidueLedger]:
    """Buck one live stem and return its logs and residue ledger row."""
    if tree.status != "live":
        raise BuckingError(f"tree {tree.tree_id} is {tree.status!r}; only live trees are bucked")
    if tree.species != model.species or tree.bec_zone != model.bec_zone:
        raise BuckingError(
            f"taper key {model.species}/{model.bec_zone} does not match tree "
            f"{tree.species}/{tree.bec_zone} for {tree.tree_id}"
        )
    if tree.height_m <= 1.3:
        raise BuckingError(f"tree {tree.tree_id} height must exceed 1.3 m")

    active_profile = profile or BuckingProfile()
    market_group = market_group_for(tree.species)
    species_class = "deciduous" if market_group == "Deciduous" else "conifer"
    sorts = _nemora_sorts(rules, species_class)
    if not sorts:
        raise BuckingError(
            f"no {species_class} sorts in rule set {rules.jurisdiction}/"
            f"{rules.version} for tree {tree.tree_id}"
        )

    total_volume = model.total_volume_m3(tree.dbh_cm, tree.height_m)
    result = buck_stem_sorted(
        tree.dbh_cm,
        tree.height_m,
        model.parameters,
        species_group=tree.species,
        profile=active_profile,
        sorts=sorts,
        price_profile=price_profile,
        objective="value",
    )

    recovered = float(result.recovered_volume_m3)
    bucket_balance = recovered + float(result.residue_volume_m3)
    if result.logs:
        tolerance = max(BUCKER_BALANCE_ABS_TOL, BUCKER_BALANCE_REL_TOL * total_volume)
        balance_error = abs(bucket_balance - total_volume)
        if balance_error > tolerance:
            raise BuckingError(
                f"tree {tree.tree_id}: bucker volume balance failed: "
                f"recovered {recovered:.9f} + residue {result.residue_volume_m3:.9f} "
                f"= {bucket_balance:.9f}, taper total {total_volume:.9f}, "
                f"error {balance_error:.9g} > {tolerance:.9g}"
            )

    residue_volume = total_volume - recovered
    tolerance = max(BUCKER_BALANCE_ABS_TOL, BUCKER_BALANCE_REL_TOL * total_volume)
    if residue_volume < -tolerance:
        raise BuckingError(
            f"tree {tree.tree_id}: recovered volume {recovered:.9f} exceeds "
            f"taper total {total_volume:.9f}"
        )
    residue_volume = max(0.0, residue_volume)
    residue_odt = residue_volume * basic_density_odt_m3
    green_tonnes = residue_odt / (1.0 - moisture_wet_basis)
    chip_green_tonnes = green_tonnes * chip_yield_fraction

    logs: list[BuckedLog] = []
    rules_by_code = {rule.sort_code: rule for rule in rules.rules}
    for index, log in enumerate(result.logs, start=1):
        sort_code = str(log["product_sort"])
        rule = rules_by_code.get(sort_code)
        if rule is None:
            raise BuckingError(
                f"tree {tree.tree_id} log {index}: sort {sort_code!r} is not in the rule set"
            )
        length = float(log["length_m"])
        if rule.max_length_m is not None and length > rule.max_length_m + 1e-9:
            raise BuckingError(
                f"tree {tree.tree_id} log {index}: length {length:.3f} m exceeds "
                f"{sort_code} max_length_m {rule.max_length_m:.3f} m"
            )
        volume = float(log["volume_m3"])
        price = log.get("price_cad_m3")
        price_value = float(price) if price is not None else None
        value = volume * price_value if price_value is not None else None
        if log.get("log_value_cad") is not None and value is not None:
            if not math.isclose(float(log["log_value_cad"]), value, rel_tol=1e-9, abs_tol=1e-9):
                raise BuckingError(
                    f"tree {tree.tree_id} log {index}: value does not equal volume * price"
                )
        logs.append(
            BuckedLog(
                tree_id=tree.tree_id,
                log_index=index,
                length_m=float(log["length_m"]),
                large_end_dib_cm=float(log["large_end_dib_cm"]),
                small_end_dib_cm=float(log["small_end_dib_cm"]),
                volume_m3=volume,
                sort_code=sort_code,
                product=str(log["product"]),
                price_cad_m3=price_value,
                value_cad=value,
                evidence=model.evidence,
                site_id=tree.site_id,
                species=tree.species,
                species_market_group=market_group,
                sort_basis="dimension_only",
                grade_status=rules.grade_status,
                price_period=price_profile.get("period"),
                price_source=price_profile.get("source"),
            )
        )

    residue = ResidueLedger(
        tree_id=tree.tree_id,
        site_id=tree.site_id,
        residue_volume_m3=residue_volume,
        basic_density_odt_m3=basic_density_odt_m3,
        residue_odt=residue_odt,
        moisture_wet_basis=moisture_wet_basis,
        green_tonnes=green_tonnes,
        chip_yield_fraction=chip_yield_fraction,
        chip_green_tonnes=chip_green_tonnes,
        recovered_log_m3=recovered,
        taper_total_m3=total_volume,
        bucker_residue_m3=float(result.residue_volume_m3),
        bucker_balance_error_m3=abs(bucket_balance - total_volume),
        evidence="assumption",
        notes=(
            f"residue = taper total {total_volume:.6f} m3 - recovered logs "
            f"{recovered:.6f} m3; includes the stump-height section, top, and "
            f"unmerchantable sections; "
            f"density {basic_density_odt_m3:.2f} ODT/m3 (assumption), moisture "
            f"{moisture_wet_basis:.2f} wet basis, chip yield {chip_yield_fraction:.2f} "
            f"(assumption); taper source: {model.source}"
        ),
    )
    return logs, residue


def buck_trees(
    trees: Iterable[TreeRecord],
    registry: TaperRegistry,
    rules: GradeRuleSet,
    price_profile: dict[str, Any],
    *,
    jurisdiction: str = "BC",
    profile: BuckingProfile | None = None,
    basic_density_odt_m3: float = 0.45,
    moisture_wet_basis: float = 0.45,
    chip_yield_fraction: float = 0.90,
    skip_invalid: bool = False,
) -> BuckingBatch:
    """Buck a tree list, optionally recording explicit skips.

    ``skip_invalid`` is for pilots and audits.  The default is strict: an
    unsupported taper or domain error propagates.  Callers that skip must
    report the skip reasons; this function never drops a tree silently.
    """
    batch = BuckingBatch()
    for tree in trees:
        if tree.status != "live":
            reason = f"status is {tree.status!r}"
            if skip_invalid:
                batch.skipped.append(
                    {
                        "tree_id": tree.tree_id,
                        "species": tree.species,
                        "bec_zone": tree.bec_zone,
                        "reason": reason,
                    }
                )
                continue
            raise BuckingError(f"tree {tree.tree_id}: {reason}")
        try:
            model = registry.resolve(jurisdiction, tree.species, tree.bec_zone)
        except UnsupportedTaperError as exc:
            if skip_invalid:
                batch.skipped.append(
                    {
                        "tree_id": tree.tree_id,
                        "species": tree.species,
                        "bec_zone": tree.bec_zone,
                        "reason": f"unsupported_taper: {exc}",
                    }
                )
                continue
            raise
        try:
            logs, residue = buck_tree(
                tree,
                model,
                rules,
                price_profile,
                profile=profile,
                basic_density_odt_m3=basic_density_odt_m3,
                moisture_wet_basis=moisture_wet_basis,
                chip_yield_fraction=chip_yield_fraction,
            )
        except (BuckingError, TaperDomainError) as exc:
            if skip_invalid:
                batch.skipped.append(
                    {
                        "tree_id": tree.tree_id,
                        "species": tree.species,
                        "bec_zone": tree.bec_zone,
                        "reason": f"bucking_error: {exc}",
                    }
                )
                continue
            raise
        batch.add_model(model)
        batch.logs.extend(logs)
        batch.residue.append(residue)
    return batch


def logs_frame(logs: Iterable[BuckedLog], trees_by_id: dict[str, TreeRecord] | None = None) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for log in logs:
        row = log.to_dict()
        tree = (trees_by_id or {}).get(log.tree_id)
        row["plot_id"] = tree.plot_id if tree else None
        row["bec_zone"] = tree.bec_zone if tree else None
        rows.append(row)
    return pd.DataFrame(rows, columns=LOG_COLUMNS)


def residue_frame(
    residue: Iterable[ResidueLedger], trees_by_id: dict[str, TreeRecord] | None = None
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for ledger in residue:
        row = ledger.to_dict()
        tree = (trees_by_id or {}).get(ledger.tree_id)
        row["plot_id"] = tree.plot_id if tree else None
        row["species"] = tree.species if tree else None
        row["bec_zone"] = tree.bec_zone if tree else None
        rows.append(row)
    return pd.DataFrame(rows, columns=RESIDUE_COLUMNS)
