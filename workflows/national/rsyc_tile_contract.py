"""Shared contract between the RSYC tile library producer and supply consumer.

The producer writes evidence tiers and allocation bases; the consumer rejects
anything outside this contract instead of silently dropping unknown rows.
"""

from __future__ import annotations

EVIDENCE_TIER_LABELS: dict[str, str] = {
    "rsyc_tile_exact": "exact",
    "rsyc_tile_group": "group",
    "rsyc_tile_generic": "generic",
    "rsyc_tile_control_fallback": "control_fallback",
}
EVIDENCE_TIERS: tuple[str, ...] = tuple(EVIDENCE_TIER_LABELS)

ALLOCATION_BASES: tuple[str, ...] = (
    "province_intersection",
    "mixed_province_intersection_and_district_fallback",
    "district_fallback_no_province_intersection",
)


class TileContractError(ValueError):
    """Raised when a library row violates the shared tile contract."""


def require_known_tier(tier: str, *, source: str) -> str:
    """Return a known evidence tier or fail with the offending value."""
    if tier not in EVIDENCE_TIER_LABELS:
        raise TileContractError(f"{source}: unknown evidence tier {tier!r}")
    return tier


def require_known_allocation_basis(basis: str, *, source: str) -> str:
    """Return a known allocation basis or fail with the offending value."""
    if basis not in ALLOCATION_BASES:
        raise TileContractError(f"{source}: unknown allocation basis {basis!r}")
    return basis
